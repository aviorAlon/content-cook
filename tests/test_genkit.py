"""genkit：设置读写/打码、按配置装配路由、file:/url:/异步前缀的任务快照、各适配器用替身客户端/HTTP 跑通。"""
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from genkit import settings as S
from genkit.adapters import REGISTRY
from genkit.adapters.ark_images import ArkImages
from genkit.adapters.ark_video import ArkVideo
from genkit.adapters.base import HttpResponse, local_token, to_data_url
from genkit.adapters.minimax_video import MiniMaxVideo
from genkit.adapters.openai_compat import OpenAIChat, OpenAIImages
from genkit.adapters.openrouter_adapters import OpenRouterImages, OpenRouterVideo
from genkit.providers import AssetReference, ImageRequest, ProviderCommandError, ProviderRejected, TaskState, VideoRequest
from genkit.router import RoutedProvider

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")


# ---------------------------------------------------------------- settings


def test_settings_roundtrip_mask_and_merge(tmp_path):
    path = tmp_path / "settings.json"
    st = S.load_settings(path)
    assert all(not st.role(r).configured for r in S.ROLES)
    S.update_settings({"roles": {"image": {"adapter": "openrouter_images", "api_key": "sk-1234567890abcdef", "model": "m"}}}, path)
    st = S.load_settings(path)
    assert st.role("image").api_key == "sk-1234567890abcdef"
    masked = st.as_dict(mask_secrets=True)["roles"]["image"]["api_key"]
    assert masked.startswith("sk-1") and "*" in masked and masked.endswith("cdef")
    # 回传打码值不覆盖原密钥；空串也不覆盖
    S.update_settings({"roles": {"image": {"api_key": masked, "model": "m2"}}}, path)
    S.update_settings({"roles": {"image": {"api_key": ""}}}, path)
    st = S.load_settings(path)
    assert st.role("image").api_key == "sk-1234567890abcdef" and st.role("image").model == "m2"
    assert S.validate_role(st, "image") == []
    assert S.validate_role(st, "video") == ["video: 没选适配器"]
    assert S.effective(st.role("image"), "image").base_url == "https://openrouter.ai/api/v1"


def test_settings_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_TEXT_ADAPTER", "openai_chat")
    monkeypatch.setenv("STORYSTUDIO_TEXT_API_KEY", "k")
    monkeypatch.setenv("STORYSTUDIO_TEXT_MODEL", "gpt")
    st = S.load_settings(tmp_path / "none.json")
    assert S.validate_role(st, "text") == []


def test_registry_matches_settings_table():
    assert {r: set(v) for r, v in S.ADAPTERS.items()} == {r: set(v) for r, v in REGISTRY.items()}


# ---------------------------------------------------------------- router


def _settings(tmp_path, **roles):
    st = S.Settings(cache_dir=str(tmp_path / "cache"))
    for role, values in roles.items():
        cfg = st.role(role)
        for k, v in values.items():
            setattr(cfg, k, v)
    return st


def test_router_requires_configuration_and_dispatches(tmp_path):
    st = _settings(tmp_path)
    provider = RoutedProvider(st)
    with pytest.raises(ProviderCommandError, match="image 模型未配置"):
        provider.submit_image(ImageRequest(name="n", prompt="p", model="x"))
    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG)
    token = provider.upload_asset(ref)
    assert token.startswith("local:") and to_data_url(token).startswith("data:image/png;base64,")
    # file: / url: 任务不联网
    cached = tmp_path / "cache" / "a.png"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(PNG)
    snap = provider.get_task(f"file:{cached}", "image")
    assert snap.status is TaskState.FINISHED and snap.output_urls[0].startswith("file:///")
    dest = provider.download(snap.output_urls[0], tmp_path / "out" / "a.png")
    assert dest.read_bytes() == PNG
    assert provider.get_task("url:https://x/y.png", "image").output_urls == ("https://x/y.png",)
    with pytest.raises(ProviderCommandError, match="unknown task id scheme"):
        provider.get_task("weird", "image")


def test_router_uses_injected_adapters(tmp_path):
    calls = []

    class FakeImages:
        name = "fake"

        def submit_image(self, request):
            calls.append(request.prompt)
            return SimpleNamespace(task_id="file:x", task_type="image", status="finished")

    provider = RoutedProvider(_settings(tmp_path), adapters={"image": FakeImages()})
    provider.submit_image(ImageRequest(name="n", prompt="hello", model="m"))
    assert calls == ["hello"]


# ---------------------------------------------------------------- adapters（替身客户端）


class _FakeOpenAI:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
        self.images = SimpleNamespace(generate=self._gen, edit=self._edit)

    def _chat(self, **kw):
        self.calls.append(("chat", kw))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))], usage=None, model=kw["model"])

    def _gen(self, **kw):
        self.calls.append(("gen", kw))
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(PNG).decode(), url=None)])

    def _edit(self, image=None, **kw):
        self.calls.append(("edit", kw))
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(PNG).decode(), url=None)])


def test_openai_chat_and_images(tmp_path):
    fake = _FakeOpenAI()
    chat = OpenAIChat({"model": "gpt-x", "api_key": "k"}, client=fake, cache_dir=tmp_path)
    data, meta = chat.complete_json("sys", "user")
    assert data == {"ok": True} and meta["model"] == "gpt-x"
    assert fake.calls[0][1]["response_format"] == {"type": "json_object"}
    images = OpenAIImages({"model": "gpt-image-1", "api_key": "k"}, client=fake, cache_dir=tmp_path)
    sub = images.submit_image(ImageRequest(name="frame", prompt="p", model="@image", aspect_ratio="9:16", quality="high"))
    assert sub.status is TaskState.FINISHED and sub.task_id.startswith("file:") and Path(sub.task_id[5:]).read_bytes() == PNG
    assert fake.calls[-1][1]["size"] == "1024x1536" and fake.calls[-1][1]["model"] == "gpt-image-1"
    ref = tmp_path / "r.png"
    ref.write_bytes(PNG)
    images.submit_image(ImageRequest(name="f2", prompt="p", model="m", reference_asset_ids=(local_token(ref),)))
    assert fake.calls[-1][0] == "edit"


def _chunk(content=None, reasoning=None, usage=None, model="m"):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)] if (content or reasoning) else [], usage=usage, model=model)


def test_openai_chat_streams_and_ignores_reasoning(tmp_path):
    """推理型模型先吐几分钟推理片段再出正文：流式接收只拼正文，用量从最后一个片段取；请求带 stream 与 include_usage。"""

    calls = []

    def create(**kw):
        calls.append(kw)
        return iter([_chunk(reasoning="想一想"), _chunk(reasoning="再想想"), _chunk(content='{"ok"'), _chunk(content=": true}"),
                     SimpleNamespace(choices=[], usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 42}), model="m")])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    chat = OpenAIChat({"model": "m", "api_key": "k"}, client=fake, cache_dir=tmp_path)
    data, meta = chat.complete_json("sys", "user")
    assert data == {"ok": True} and meta["usage"] == {"total_tokens": 42} and meta["model"] == "m"
    assert calls[0]["stream"] is True and calls[0]["stream_options"] == {"include_usage": True}
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_openai_chat_falls_back_when_gateway_rejects_stream(tmp_path):
    """网关不认 stream_options → 去掉再试；连 stream 都不认 → 退回一次性请求。别的 400 原样报，不重试。"""

    import openai

    def bad(message):
        return openai.BadRequestError(message, response=SimpleNamespace(status_code=400, headers={}, request=None), body=None)

    calls = []

    def create(**kw):
        calls.append(kw)
        if "stream_options" in kw:
            raise bad("unknown parameter: stream_options")
        if kw.get("stream"):
            raise bad("stream is not supported")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": 1}'))], usage=None, model="m")

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    chat = OpenAIChat({"model": "m", "api_key": "k"}, client=fake, cache_dir=tmp_path)
    data, _meta = chat.complete_json("sys", "user")
    assert data == {"ok": 1} and [("stream_options" in c, bool(c.get("stream"))) for c in calls] == [(True, True), (False, True), (False, False)]

    def refuse(**kw):
        raise bad("content_policy violation")

    chat = OpenAIChat({"model": "m", "api_key": "k"}, client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=refuse))), cache_dir=tmp_path)
    with pytest.raises(ProviderRejected):
        chat.complete_json("sys", "user")


def test_openai_chat_stream_gives_up_after_the_total_cap(tmp_path, monkeypatch):
    from genkit.adapters import openai_compat

    monkeypatch.setattr(openai_compat, "STREAM_MAX_SECONDS", -1.0)
    closed = []

    class Stream:
        def __iter__(self):
            yield _chunk(reasoning="想")
            yield _chunk(content="{}")

        def close(self):
            closed.append(True)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: Stream())))
    chat = OpenAIChat({"model": "m", "api_key": "k"}, client=fake, cache_dir=tmp_path)
    with pytest.raises(ProviderCommandError, match="流式接收超过"):
        chat.complete("sys", "user")
    assert closed == [True]


def test_openrouter_images_and_video(tmp_path):
    class FakeOR:
        def __init__(self):
            self.kw = {}
            self.images = SimpleNamespace(generate=self._img)
            self.video_generation = SimpleNamespace(generate=self._vgen, get_generation=self._vget, get_video_content=self._vcontent)

        def _img(self, **kw):
            self.kw["img"] = kw
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(PNG).decode())])

        def _vgen(self, **kw):
            self.kw["vgen"] = kw
            return SimpleNamespace(id="job1", status="pending", error=None)

        def _vget(self, job_id):
            return SimpleNamespace(status="completed", error=None)

        def _vcontent(self, job_id, index=0):
            return SimpleNamespace(content=b"MP4DATA")

    fake = FakeOR()
    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG)
    img = OpenRouterImages({"api_key": "k", "model": "seedream"}, client=fake, cache_dir=tmp_path)
    sub = img.submit_image(ImageRequest(name="f", prompt="p", model="@image", image_size="2K", reference_asset_ids=(local_token(ref),)))
    assert sub.status is TaskState.FINISHED and fake.kw["img"]["input_references"][0]["image_url"]["url"].startswith("data:image/png")
    vid = OpenRouterVideo({"api_key": "k", "model": "minimax/hailuo-3"}, client=fake, cache_dir=tmp_path)
    sub = vid.submit_video(VideoRequest(name="v", prompt="p", duration=6, request_type="first_frame", resolution="768p",
                                        aspect_ratio="adaptive", references=(AssetReference(local_token(ref), "first_frame"),)))
    assert sub.task_id == "openrouter:job1" and fake.kw["vgen"]["frame_images"][0]["frame_type"] == "first"
    assert "aspect_ratio" not in fake.kw["vgen"]
    snap = vid.get_task("openrouter:job1")
    assert snap.status is TaskState.FINISHED and snap.output_urls[0].startswith("file:///")


def test_ark_images_and_video_with_fake_sdk(tmp_path):
    class FakeArk:
        def __init__(self):
            self.kw = {}
            self.images = SimpleNamespace(generate=self._img)
            self.content_generation = SimpleNamespace(tasks=SimpleNamespace(create=self._create, get=self._get))

        def _img(self, **kw):
            self.kw["img"] = kw
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(PNG).decode(), url=None)])

        def _create(self, **kw):
            self.kw["create"] = kw
            return SimpleNamespace(id="t1")

        def _get(self, task_id):
            return SimpleNamespace(status="succeeded", content=SimpleNamespace(video_url="https://ark/v.mp4"))

    fake = FakeArk()
    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG)
    img = ArkImages({"api_key": "k", "model": "seedream"}, client=fake, cache_dir=tmp_path)
    assert img.submit_image(ImageRequest(name="f", prompt="p", model="@image", reference_asset_ids=(local_token(ref),))).status is TaskState.FINISHED
    assert fake.kw["img"]["image"].startswith("data:image/png") and fake.kw["img"]["watermark"] is False
    vid = ArkVideo({"api_key": "k", "model": "seedance"}, client=fake, cache_dir=tmp_path)
    sub = vid.submit_video(VideoRequest(name="v", prompt="p", duration=5, request_type="first_frame", references=(AssetReference(local_token(ref), "first_frame"),)))
    assert sub.task_id == "ark:t1"
    text = fake.kw["create"]["content"][0]["text"]
    assert "--duration 5" in text and "--ratio adaptive" in text and fake.kw["create"]["content"][1]["role"] == "first_frame"
    assert vid.get_task("ark:t1").output_urls == ("https://ark/v.mp4",)


def test_minimax_video_with_fake_http(tmp_path):
    seen = []

    def http(method, url, headers=None, json_body=None, data=None, timeout=0):
        seen.append((method, url, json_body))
        if url.endswith("/video_generation"):
            return HttpResponse(200, json.dumps({"task_id": "9", "base_resp": {"status_code": 0}}).encode())
        if "/query/video_generation" in url:
            return HttpResponse(200, json.dumps({"status": "Success", "file_id": "f1"}).encode())
        if "/files/retrieve" in url:
            return HttpResponse(200, json.dumps({"file": {"download_url": "https://mm/v.mp4"}}).encode())
        raise AssertionError(url)

    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG)
    mm = MiniMaxVideo({"api_key": "k", "model": "MiniMax-Hailuo-02", "base_url": "https://api.minimax.chat/v1"}, http=http, cache_dir=tmp_path)
    sub = mm.submit_video(VideoRequest(name="v", prompt="p", duration=6, request_type="first_frame", resolution="768P", references=(AssetReference(local_token(ref), "first_frame"),)))
    assert sub.task_id == "minimax:9" and seen[0][2]["first_frame_image"].startswith("data:image/png")
    assert mm.get_task("minimax:9").output_urls == ("https://mm/v.mp4",)


def test_http_error_translation(tmp_path):
    from genkit.adapters.base import raise_for_status
    from genkit.providers import ProviderRejected, QuotaExhausted

    with pytest.raises(QuotaExhausted):
        raise_for_status(HttpResponse(402, b"insufficient balance"), what="x")
    with pytest.raises(ProviderRejected):
        raise_for_status(HttpResponse(400, b"content moderation blocked"), what="x")
    with pytest.raises(ProviderCommandError):
        raise_for_status(HttpResponse(500, b"boom"), what="x")
