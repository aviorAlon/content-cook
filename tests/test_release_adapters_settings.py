"""发第一个 tag 前的适配层与设置修复（审计 ADP-01…12、FAM-7）的回归测试。

每条对应一个曾经复现过的缺陷：测试连接不联网也报 ✓、火山把参数错误当审核拦截、本地模型手填地址丢 /v1、
换来源旧密钥跟着走、设置文件坏了全线 500（以及修它时引入的：并发保存把好文件当坏文件挪走、被拒的请求挪走坏文件）、环境变量覆盖被落盘、参考图上限跨来源猜、
openai_images 能力表多写 2K 档、count 透传多花钱（及加载时拒绝让老项目打不开）、短密钥打码回明文、试听未配置报 502。
一行真实网络请求都不发（假 client / 假 fetch / 指向 127.0.0.1:9 的连接拒绝）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from genkit import capabilities as C
from genkit import local as L
from genkit import settings as S
from genkit.adapters.ark_images import ArkImages, translate_ark_error
from genkit.adapters.ark_video import ArkVideo
from genkit.adapters.openai_compat import OpenAIImages, OpenAISpeech
from genkit.adapters.openrouter_adapters import OpenRouterSpeech
from genkit.providers import ImageRequest, ProviderCommandError, ProviderRejected, QuotaExhausted, VideoRequest
from genkit.router import RoutedProvider
from storyvideo import lint
from storyvideo.spec import normalize_spec
from webui.app import app
from webui.humanize import classify_failure
from webui.routers import local_models as LM

client = TestClient(app)
DEAD = "http://127.0.0.1:9"      # 没人监听的端口：连接立刻被拒绝，不会真的出网


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    for role in ("TEXT", "IMAGE", "VOICE", "VIDEO", "MUSIC"):
        for key in ("ADAPTER", "BASE_URL", "API_KEY", "MODEL", "VOICE"):
            monkeypatch.delenv(f"STORYSTUDIO_{role}_{key}", raising=False)
    LM.reset_hint_cache()
    yield tmp_path / "home"
    LM.reset_hint_cache()


def _on_disk(home) -> dict:
    return json.loads((home / "settings.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------- ADP-01 测试连接要真的探一次


class _Raising:
    """替身 SDK client：任何属性链最后一调就抛给定异常，用来证明 ping 真的碰了 client。"""

    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls: list[str] = []

    def __getattr__(self, name):
        return _Raising._Node(self, name)

    class _Node:
        def __init__(self, root, path):
            self.root, self.path = root, path

        def __getattr__(self, name):
            return _Raising._Node(self.root, f"{self.path}.{name}")

        def __call__(self, *args, **kwargs):
            self.root.calls.append(self.path)
            raise self.root.exc


def _fake_models(count: int):
    return SimpleNamespace(models=SimpleNamespace(list=lambda **kw: SimpleNamespace(data=[SimpleNamespace(id=f"m{i}") for i in range(count)])))


def test_openai_speech_ping_lists_models_and_translates_connection_error(tmp_path):
    import openai

    adapter = OpenAISpeech({"base_url": "https://nonexistent.invalid/v1", "api_key": "bad", "model": "tts-1"},
                           client=_fake_models(3), cache_dir=tmp_path)
    assert adapter.ping().startswith("reachable (3 models)")
    failing = _Raising(openai.APIConnectionError(request=None))
    adapter = OpenAISpeech({"base_url": "https://nonexistent.invalid/v1", "api_key": "bad", "model": "tts-1"}, client=failing, cache_dir=tmp_path)
    with pytest.raises(ProviderCommandError) as info:
        adapter.ping()
    assert failing.calls == ["models.list"] and "连不上 https://nonexistent.invalid/v1" in str(info.value)


@pytest.mark.parametrize("status,needle", [(401, "拒绝了这个密钥（401）"), (404, "/models 不存在（404）")])
def test_openai_ping_explains_auth_and_wrong_path(tmp_path, status, needle):
    import httpx
    import openai

    response = httpx.Response(status, request=httpx.Request("GET", "https://gw.local/models"))
    exc = (openai.AuthenticationError if status == 401 else openai.NotFoundError)("nope", response=response, body=None)
    for cls in (OpenAISpeech, OpenAIImages):
        adapter = cls({"base_url": "https://gw.local", "api_key": "k", "model": "m"}, client=_Raising(exc), cache_dir=tmp_path)
        with pytest.raises(ProviderCommandError) as info:
            adapter.ping()
        assert needle in str(info.value)


def test_openai_speech_ping_really_connects(tmp_path):
    """不注入 client：SDK 客户端真的会被构造并连一次（连接被拒绝 → 人话），而不是只拼配置字符串。"""

    adapter = OpenAISpeech({"base_url": DEAD + "/v1", "api_key": "bad", "model": "tts-1"}, cache_dir=tmp_path)
    with pytest.raises(ProviderCommandError) as info:
        adapter.ping()
    assert adapter._client is not None and "连不上" in str(info.value)


def test_openrouter_tts_ping_checks_the_key(tmp_path):
    ok = SimpleNamespace(api_keys=SimpleNamespace(get_current_key_metadata=lambda **kw: SimpleNamespace(data=SimpleNamespace(label="team key"))))
    adapter = OpenRouterSpeech({"api_key": "k", "model": "x", "voice": "y"}, client=ok, cache_dir=tmp_path)
    assert adapter.ping() == "key ok (team key); using x, voice y"
    failing = _Raising(RuntimeError("API error occurred: Status 401 - User not found"))
    adapter = OpenRouterSpeech({"api_key": "bad-key", "model": "x", "voice": "y"}, client=failing, cache_dir=tmp_path)
    with pytest.raises(ProviderCommandError):
        adapter.ping()
    assert failing.calls == ["api_keys.get_current_key_metadata"]
    # 真 SDK 的 401 异常带 status_code：说成「拒绝了这个密钥」
    import httpx
    from openrouter import errors as ORE

    response = httpx.Response(401, request=httpx.Request("GET", "https://openrouter.ai/api/v1/key"), text="{}")
    unauthorized = ORE.UnauthorizedResponseError(SimpleNamespace(error=SimpleNamespace(message="User not found.")), response)
    adapter = OpenRouterSpeech({"api_key": "bad-key", "model": "x", "voice": "y"}, client=_Raising(unauthorized), cache_dir=tmp_path)
    with pytest.raises(ProviderCommandError) as info:
        adapter.ping()
    assert "拒绝了这个密钥（401）：User not found." in str(info.value)


@pytest.mark.parametrize("cls", [ArkImages, ArkVideo])
def test_ark_ping_lists_tasks(tmp_path, cls):
    calls = []
    ok = SimpleNamespace(content_generation=SimpleNamespace(tasks=SimpleNamespace(list=lambda **kw: calls.append(kw) or SimpleNamespace(items=[]))))
    adapter = cls({"base_url": "https://ark.example/api/v3", "api_key": "k", "model": "m"}, client=ok, cache_dir=tmp_path)
    assert adapter.ping().startswith("reachable") and calls == [{"page_size": 1, "timeout": 30.0}]
    failing = _Raising(RuntimeError("Error code: 401 - {'error': {'code': 'AuthenticationError'}}"))
    adapter = cls({"base_url": "https://ark.example/api/v3", "api_key": "bad", "model": "m"}, client=failing, cache_dir=tmp_path)
    with pytest.raises(ProviderCommandError):
        adapter.ping()
    assert failing.calls == ["content_generation.tasks.list"]


def test_ark_ping_really_connects(tmp_path):
    """真 SDK 指向没人听的端口（SDK 自带重试，这条要几秒）：连接被拒绝 → 人话，不再是「Ark client ready」。"""

    adapter = ArkImages({"base_url": DEAD + "/api/v3", "api_key": "bad-key", "model": "m"}, cache_dir=tmp_path)
    with pytest.raises(ProviderCommandError) as info:
        adapter.ping()
    assert adapter._client is not None and "连不上" in str(info.value)


def test_ark_ping_tells_bad_key_from_overdue_account(tmp_path):
    """401 是密钥错；欠费（方舟回 403 AccountOverdueError）密钥是对的，要报成欠费而不是「拒绝了这个密钥」。"""

    bad_key = _ark_error("ArkAuthenticationError", 401, "AuthenticationError", "The API key in the request is missing or invalid.")
    adapter = ArkVideo({"base_url": "https://ark.example/api/v3", "api_key": "bad", "model": "m"}, client=_Raising(bad_key), cache_dir=tmp_path)
    with pytest.raises(ProviderCommandError) as info:
        adapter.ping()
    assert "拒绝了这个密钥（401）" in str(info.value)
    overdue = _ark_error("ArkPermissionDeniedError", 403, "AccountOverdueError", "The request failed because your account has an overdue balance.")
    adapter = ArkImages({"base_url": "https://ark.example/api/v3", "api_key": "k", "model": "m"}, client=_Raising(overdue), cache_dir=tmp_path)
    with pytest.raises(QuotaExhausted):
        adapter.ping()


def test_openai_ping_caps_the_probe_timeout(tmp_path):
    seen = {}
    fake = SimpleNamespace(models=SimpleNamespace(list=lambda **kw: seen.update(kw) or SimpleNamespace(data=[])))
    OpenAISpeech({"base_url": "https://gw.local/v1", "api_key": "k", "model": "tts-1"}, client=fake, cache_dir=tmp_path).ping()
    assert seen == {"timeout": 30.0}


def test_edge_ping_fetches_the_voice_list(tmp_path, monkeypatch):
    """Edge 也要真的联一次网（取音色表，不合成）：之前只 import 一下库就报 ready，断网也是 ✓。"""

    import edge_tts

    from genkit.adapters.speech import EdgeSpeech

    async def voices():
        return [{"ShortName": "zh-CN-YunxiNeural"}, {"ShortName": "en-US-AriaNeural"}]

    monkeypatch.setattr(edge_tts, "list_voices", voices)
    assert EdgeSpeech({"model": "zh-CN-YunxiNeural"}, cache_dir=tmp_path).ping() == "edge-tts reachable (2 voices), voice zh-CN-YunxiNeural"

    async def offline():
        raise OSError("Cannot connect to host speech.platform.bing.com:443")

    monkeypatch.setattr(edge_tts, "list_voices", offline)
    with pytest.raises(ProviderCommandError) as info:
        EdgeSpeech({"model": "zh-CN-YunxiNeural"}, cache_dir=tmp_path).ping()
    assert "连不上 Edge 在线语音服务" in str(info.value) and "speech.platform.bing.com" in str(info.value)


def test_settings_test_endpoint_reports_failure_for_unreachable_sources(home):
    """用户面：假地址 + 假密钥的 openai_speech 不能再回 ok:true（ark / openrouter 的探针在上面按适配器各验过）。"""

    values = {"adapter": "openai_speech", "base_url": DEAD + "/v1", "api_key": "bad-key", "model": "tts-1"}
    assert client.put("/api/settings", json={"roles": {"voice": values}}).status_code == 200
    body = client.post("/api/settings/test/voice").json()
    assert body["ok"] is False and "连不上" in body["message"], body


# ---------------------------------------------------------------------------- ADP-02 火山错误分类


def _ark_error(cls_name: str, status: int, code: str, message: str, request_id: str = "req-test-0000"):
    import httpx
    from volcenginesdkarkruntime import _exceptions as E

    body = {"code": code, "message": message, "param": "", "type": "BadRequest"}
    response = httpx.Response(status, request=httpx.Request("POST", "https://ark.example/api/v3/contents/generations/tasks"))
    return getattr(E, cls_name)(f"Error code: {status} - {{'error': {body!r}}}", response=response, body=body, request_id=request_id)


def test_ark_parameter_and_not_found_errors_are_not_moderation():
    bad = _ark_error("ArkBadRequestError", 400, "InvalidParameter", "The parameter `content` specified in the request are not valid: duration 20 is out of range [2, 12].")
    exc = translate_ark_error(bad, "ark video submit")
    assert type(exc) is ProviderCommandError
    assert classify_failure(f"{type(exc).__name__}: {exc}")["kind"] == "other"
    missing = _ark_error("ArkNotFoundError", 404, "NotFound", "The content generation task abc does not exist.")
    assert type(translate_ark_error(missing, "ark video status")) is ProviderCommandError
    # request_id 里的数字串含 402 不算欠费
    assert type(translate_ark_error(_ark_error("ArkBadRequestError", 400, "InvalidParameter", "bad", request_id="20250922140212ABCDEF"), "x")) is ProviderCommandError


def test_ark_real_moderation_and_quota_still_classified():
    sensitive = _ark_error("ArkBadRequestError", 400, "SensitiveContentDetected", "The request failed because the input text may contain sensitive information.")
    assert isinstance(translate_ark_error(sensitive, "ark images"), ProviderRejected)
    blocked = _ark_error("ArkBadRequestError", 400, "OutputContentBlocked", "blocked")
    assert isinstance(translate_ark_error(blocked, "ark images"), ProviderRejected)
    assert isinstance(translate_ark_error(RuntimeError("Error: 内容审核未通过"), "ark images"), ProviderRejected)
    overdue = _ark_error("ArkAPIStatusError", 402, "AccountOverdueError", "account overdue")
    assert isinstance(translate_ark_error(overdue, "ark images"), QuotaExhausted)
    assert isinstance(translate_ark_error(RuntimeError("Error code: 429 - quota exceeded"), "ark images"), QuotaExhausted)


def _video_request():
    return VideoRequest(name="n", prompt="p", model="m", duration=4, resolution="720p", aspect_ratio="9:16")


def _image_request():
    return ImageRequest(name="n", prompt="p", model="m", aspect_ratio="9:16")


def test_ark_submit_parameter_error_is_a_failed_submission_not_moderation(tmp_path):
    """提交时明确的 4xx（参数越界）：记成一次失败的提交（可改 spec 后重提），不是审核拦截，
    也不再抛出去让运行时当成「不知道服务商收没收到」卡在待对账。"""

    bad = _ark_error("ArkBadRequestError", 400, "InvalidParameter", "The parameter `content` specified in the request are not valid.")
    video = ArkVideo({"api_key": "k", "model": "m"}, client=_Raising(bad), cache_dir=tmp_path).submit_video(_video_request())
    image = ArkImages({"api_key": "k", "model": "m"}, client=_Raising(bad), cache_dir=tmp_path).submit_image(_image_request())
    for sub, what in ((video, "ark video submit"), (image, "ark images")):
        assert sub.status.value == "failed" and sub.task_id is None
        assert sub.error_message.startswith(f"ProviderCommandError: {what}: Error code: 400")
        info = classify_failure(sub.error_message)
        assert info["kind"] == "other" and info["retryable"] is True


def test_ark_submit_still_raises_when_the_outcome_is_unknown_or_special(tmp_path):
    import httpx
    from volcenginesdkarkruntime import _exceptions as E

    # 连接错误 / 5xx：任务建没建不确定，照旧抛 ProviderCommandError（运行时进待对账，不自动重提）
    unreachable = E.ArkAPIConnectionError(request=httpx.Request("POST", "https://ark.example/api/v3/contents/generations/tasks"), request_id="r1")
    server = _ark_error("ArkInternalServerError", 500, "InternalServiceError", "internal error")
    for exc in (unreachable, server):
        with pytest.raises(ProviderCommandError) as info:
            ArkVideo({"api_key": "k", "model": "m"}, client=_Raising(exc), cache_dir=tmp_path).submit_video(_video_request())
        assert not isinstance(info.value, ProviderRejected)
    # 审核 / 欠费：照旧抛专门的异常
    sensitive = _ark_error("ArkBadRequestError", 400, "InputTextSensitiveContentDetected", "may contain sensitive information")
    with pytest.raises(ProviderRejected):
        ArkImages({"api_key": "k", "model": "m"}, client=_Raising(sensitive), cache_dir=tmp_path).submit_image(_image_request())
    overdue = _ark_error("ArkPermissionDeniedError", 403, "AccountOverdueError", "overdue balance")
    with pytest.raises(QuotaExhausted):
        ArkVideo({"api_key": "k", "model": "m"}, client=_Raising(overdue), cache_dir=tmp_path).submit_video(_video_request())


# ---------------------------------------------------------------------------- ADP-03 手填地址接预设路径


def test_adapter_url_adds_the_preset_path_for_host_only_addresses():
    ollama = L.preset("ollama")
    assert L.adapter_url(ollama, "http://127.0.0.1:11434") == "http://127.0.0.1:11434/v1"
    assert L.adapter_url(ollama, "http://192.168.1.9:11434/") == "http://192.168.1.9:11434/v1"
    assert L.adapter_url(ollama, "http://192.168.1.9:11434/v1") == "http://192.168.1.9:11434/v1"      # 自己写了路径就照用户的
    assert L.adapter_url(ollama, "http://192.168.1.9:11434/proxy/v1/") == "http://192.168.1.9:11434/proxy/v1"
    assert L.adapter_url(ollama) == "http://127.0.0.1:11434/v1"
    assert L.adapter_url(L.preset("comfyui"), "http://192.168.1.9:8188/") == "http://192.168.1.9:8188"   # 预设本来无路径


@pytest.mark.parametrize("service,role", [("ollama", "text"), ("lmstudio", "text"), ("vllm", "text"), ("llamacpp", "text"), ("openai_speech_local", "voice")])
def test_apply_to_role_keeps_the_v1_path_when_the_user_typed_host_only(service, role):
    st = S.Settings()
    L.apply_to_role(st, service, role, "m", "http://127.0.0.1:9999")
    assert st.role(role).base_url == "http://127.0.0.1:9999/v1"


def test_scan_row_echoes_the_adapter_address(home):
    seen: list[str] = []

    def fetch(url, timeout):
        seen.append(url)
        if url.startswith("http://127.0.0.1:18814/api/tags"):
            return {"models": [{"name": "qwen2.5:7b"}]}
        raise OSError("connection refused")

    rows = L.scan(["ollama"], fetch=fetch, base_urls={"ollama": "http://127.0.0.1:18814"})
    assert rows[0]["alive"] and rows[0]["base_url"] == "http://127.0.0.1:18814/v1" and seen == ["http://127.0.0.1:18814/api/tags"]
    # 接口：扫描行 → 「应用」原样传回 → 文本角色的地址带 /v1
    import genkit.local as local_mod

    original = local_mod.default_fetch
    local_mod.default_fetch = fetch
    try:
        body = client.get("/api/system/local-scan?ids=ollama&base_url=http://127.0.0.1:18814&timeout=0.1").json()
        assert body["services"][0]["base_url"] == "http://127.0.0.1:18814/v1"
        applied = client.post("/api/settings/local/apply", json={"service": "ollama", "role": "text", "model": "qwen2.5:7b", "base_url": body["services"][0]["base_url"]}).json()
        assert applied["settings"]["roles"]["text"]["base_url"] == "http://127.0.0.1:18814/v1"
        direct = client.post("/api/settings/local/apply", json={"service": "ollama", "role": "text", "model": "qwen2.5:7b", "base_url": "http://127.0.0.1:18814"}).json()
        assert direct["settings"]["roles"]["text"]["base_url"] == "http://127.0.0.1:18814/v1"
    finally:
        local_mod.default_fetch = original


# ---------------------------------------------------------------------------- ADP-04 换来源清密钥 / 显式清空


def test_switching_adapter_drops_the_old_key(tmp_path):
    path = tmp_path / "settings.json"
    S.update_settings({"roles": {"image": {"adapter": "openai_images", "api_key": "sk-1234567890abcdef", "model": "gpt-image-1"}}}, path)
    masked = S.load_settings(path).as_dict(mask_secrets=True)["roles"]["image"]["api_key"]
    # 表单换来源时回传的正是打码值：换来源就清，打码值不能把旧 key 又带回来
    S.update_settings({"roles": {"image": {"adapter": "sdwebui", "base_url": "http://192.168.1.50:7860", "model": "sd_xl", "api_key": masked}}}, path)
    st = S.load_settings(path)
    assert st.role("image").adapter == "sdwebui" and st.role("image").api_key == ""
    assert RoutedProvider(st).adapter("image").auth_headers() == {}
    # 同一来源重存：保留
    S.update_settings({"roles": {"video": {"adapter": "ark_video", "api_key": "sk-videoKEY000000", "model": "x"}}}, path)
    S.update_settings({"roles": {"video": {"adapter": "ark_video", "api_key": "", "model": "y"}}}, path)
    assert S.load_settings(path).role("video").api_key == "sk-videoKEY000000"
    # 换成另一个要 key 的来源也清：密钥跟来源走
    S.update_settings({"roles": {"video": {"adapter": "openai_video", "api_key": masked, "model": "sora-2"}}}, path)
    assert S.load_settings(path).role("video").api_key == ""
    # 取消配置（adapter 置空）同样清
    S.update_settings({"roles": {"image": {"adapter": "openai_images", "api_key": "sk-again00000000000", "model": "m"}}}, path)
    S.update_settings({"roles": {"image": {"adapter": ""}}}, path)
    assert S.load_settings(path).role("image").api_key == ""


def test_unknown_adapter_keeps_its_key_on_unrelated_saves(tmp_path):
    """手改出来的、表里没有的 adapter：装不起来、密钥也发不出去，改别的字段时不要顺手把密钥删了。"""

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"roles": {"text": {"adapter": "some_future_adapter", "api_key": "sk-keepME000000000", "model": "m"}}}), encoding="utf-8")
    S.update_settings({"roles": {"text": {"model": "m2"}}}, path)
    st = S.load_settings(path)
    assert st.role("text").model == "m2" and st.role("text").api_key == "sk-keepME000000000"


def test_same_source_save_keeps_a_proxy_key_on_a_source_without_key_field(tmp_path):
    """审查复现：docs §8.1 / sdwebui.py 写明 WebUI 挡在反向代理后面时手配的 api_key「填了才带」。表单里没有密钥栏，
    同一张卡改个模型再保存（请求里没有 api_key）不能把它删掉，否则反代立刻 401；换来源、显式清除才清。"""

    path = tmp_path / "settings.json"
    seed = {"roles": {"image": {"adapter": "sdwebui", "base_url": "http://192.168.1.50:7860",
                                "api_key": "proxy-bearer-123456789", "model": "a.safetensors"}}}
    path.write_text(json.dumps(seed), encoding="utf-8")
    # Settings.vue save(role) 的形状：{adapter, 该来源 fields 里每个字段}，sdwebui 的 fields 是 base_url / model
    S.update_settings({"roles": {"image": {"adapter": "sdwebui", "base_url": "http://192.168.1.50:7860", "model": "b.safetensors"}}}, path)
    st = S.load_settings(path)
    assert st.role("image").model == "b.safetensors" and st.role("image").api_key == "proxy-bearer-123456789"
    assert RoutedProvider(st).adapter("image").auth_headers() == {"Authorization": "Bearer proxy-bearer-123456789"}
    S.update_settings({"roles": {"image": {"api_key": None}}}, path)             # 设置页「清除密钥」
    assert S.load_settings(path).role("image").api_key == ""
    path.write_text(json.dumps(seed), encoding="utf-8")
    S.update_settings({"roles": {"image": {"adapter": "comfyui", "model": "x"}}}, path)     # 换来源
    assert S.load_settings(path).role("image").api_key == ""


def test_null_clears_a_key_while_empty_and_masked_keep_it(tmp_path):
    path = tmp_path / "settings.json"
    S.update_settings({"roles": {"text": {"adapter": "openai_chat", "api_key": "sk-textKEY0000000000", "model": "m"}},
                       "gateway": {"base_url": "http://gw.local/v1", "api_key": "sk-gwKEY00000000000"}}, path)
    S.update_settings({"roles": {"text": {"api_key": ""}}, "gateway": {"api_key": ""}}, path)
    st = S.load_settings(path)
    assert st.role("text").api_key == "sk-textKEY0000000000" and st.gateway.api_key == "sk-gwKEY00000000000"
    S.update_settings({"roles": {"text": {"api_key": None}}, "gateway": {"api_key": None}}, path)
    st = S.load_settings(path)
    assert st.role("text").api_key == "" and st.gateway.api_key == ""


def test_form_path_and_local_apply_agree_on_clearing_keys(home):
    client.put("/api/settings", json={"roles": {"image": {"adapter": "openai_images", "api_key": "sk-1234567890abcdef", "model": "gpt-image-1"}}})
    assert _on_disk(home)["roles"]["image"]["api_key"] == "sk-1234567890abcdef"
    r = client.put("/api/settings", json={"roles": {"image": {"adapter": "sdwebui", "base_url": "http://127.0.0.1:7860", "model": "sd_xl"}}})
    assert r.status_code == 200 and r.json()["settings"]["roles"]["image"]["api_key"] == ""
    assert _on_disk(home)["roles"]["image"]["api_key"] == ""
    client.put("/api/settings", json={"roles": {"text": {"adapter": "openai_chat", "api_key": "sk-textKEY0000000000", "model": "m"}}})
    cleared = client.put("/api/settings", json={"roles": {"text": {"api_key": None}}})
    assert cleared.status_code == 200 and cleared.json()["settings"]["roles"]["text"]["api_key"] == ""
    assert _on_disk(home)["roles"]["text"]["api_key"] == ""


# ---------------------------------------------------------------------------- ADP-05 设置文件坏了


def test_bom_settings_file_loads(tmp_path):
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"roles": {"text": {"adapter": "openai_chat", "model": "gpt-x", "api_key": "k"}}}).encode("utf-8"))
    assert S.load_settings(path).role("text").model == "gpt-x"


@pytest.mark.parametrize("content", ["{ not json", "[]", "\"x\""])
def test_corrupt_settings_file_is_reported_not_500(home, monkeypatch, content):
    monkeypatch.setattr(L, "default_fetch", lambda url, timeout: (_ for _ in ()).throw(OSError("connection refused")))
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(content, encoding="utf-8")
    got = client.get("/api/settings")
    assert got.status_code == 200
    body = got.json()
    assert body["warning"].startswith("设置文件损坏：") and "保存一次" in body["warning"]
    assert body["settings"]["roles"]["text"]["adapter"] == "" and body["status"]["text"] == ["text: 没选适配器"]
    assert client.get("/api/voices/catalog?engine=settings").status_code == 200
    assert client.get("/api/system/local-hint?role=image").status_code == 200
    assert client.get("/api/health").status_code == 200
    # 正常文件不带 warning
    (home / "settings.json").write_text("{}", encoding="utf-8")
    assert client.get("/api/settings").json()["warning"] == ""


def test_saving_over_a_corrupt_settings_file_backs_it_up_and_rebuilds(home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text("{ not json", encoding="utf-8")
    r = client.put("/api/settings", json={"roles": {"text": {"adapter": "openai_chat", "model": "x", "api_key": "sk-textKEY0000000000"}}})
    assert r.status_code == 200, r.text
    assert _on_disk(home)["roles"]["text"]["model"] == "x"
    backups = list(home.glob("settings.json.broken-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "{ not json"
    assert client.get("/api/settings").json()["warning"] == ""
    # 别的写盘路径（关掉本地提示 / 一键应用）同样不再 500
    (home / "settings.json").write_text("{ not json", encoding="utf-8")
    assert client.post("/api/system/local-hint/dismiss", json={"role": "all"}).status_code == 200
    assert len(list(home.glob("settings.json.broken-*"))) == 2


@pytest.mark.parametrize("call", [
    ("/api/settings/gateway/apply", {"roles": {"nope": {"model": "x"}}}),        # 未知角色
    ("/api/settings/gateway/apply", {"roles": {"text": {"model": "x"}}}),        # 还没配网关
    ("/api/settings/local/apply", {"service": "nope", "role": "text"}),          # 不认识的本地服务
])
def test_rejected_write_leaves_a_corrupt_settings_file_alone(home, call):
    """审查复现：请求被 400 拒掉之前坏文件就被挪成 .broken-*——什么都没写，settings.json 却没了，
    「设置文件损坏」的提示也跟着消失。备份只该发生在真正写盘前。"""

    home.mkdir(parents=True, exist_ok=True)
    broken = '{"roles": {"text": {"adapter": "openai_chat", "api_key": "sk-hand-edited-123456789",}}}'
    (home / "settings.json").write_text(broken, encoding="utf-8")
    r = client.post(call[0], json=call[1])
    assert r.status_code == 400, r.text
    assert (home / "settings.json").read_text(encoding="utf-8") == broken
    assert list(home.glob("settings.json.broken-*")) == []
    assert client.get("/api/settings").json()["warning"].startswith("设置文件损坏：")


def test_concurrent_saves_keep_every_change_and_never_quarantine_a_good_file(home):
    """审查复现：几个保存同时到（设置页连点两张卡的「保存」、保存与关本地提示同时发生）。之前读到别人写了一半的
    共用 .json.tmp（Extra data）或撞上 os.replace / icacls 的 PermissionError，就把好好的文件当坏文件挪走、
    从空配置重建——所有密钥没了。现在整段读改存串行、临时文件各用各的。"""

    import threading

    from webui.routers.local_models import HintDismiss, dismiss_local_hint

    S.update_settings({"roles": {"image": {"adapter": "openai_images", "api_key": "sk-IMAGEKEY-1234567890", "model": "gpt-image-1"}}})
    errors: list[str] = []
    rounds = 6

    def saver(role: str) -> None:
        for j in range(rounds):
            try:
                got = S.update_settings({"roles": {role: {"model": f"{role}-{j}"}}})
                if got.role("image").api_key != "sk-IMAGEKEY-1234567890":
                    errors.append(f"{role}#{j}: image key now {got.role('image').api_key!r}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{role}#{j}: {type(exc).__name__}: {exc}")

    def dismisser() -> None:
        for _ in range(rounds):
            try:
                dismiss_local_hint(HintDismiss(role="image"))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"dismiss: {type(exc).__name__}: {exc}")

    def reader() -> None:
        for _ in range(rounds * 3):
            try:
                if S.load_settings().role("image").api_key != "sk-IMAGEKEY-1234567890":
                    errors.append("reader saw the image key missing")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"reader: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=saver, args=(r,)) for r in ("text", "voice", "music")]
    threads += [threading.Thread(target=dismisser), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert list(home.glob("settings.json.broken-*")) == [] and list(home.glob("*.tmp")) == []
    final = S.load_settings()
    assert final.role("image").api_key == "sk-IMAGEKEY-1234567890"
    # 读改存串行：谁的改动都没被别人的整份覆盖掉
    assert [final.role(r).model for r in ("text", "voice", "music")] == [f"{r}-{rounds - 1}" for r in ("text", "voice", "music")]
    assert final.local_hints_dismissed == ["image"]


def test_unreadable_settings_file_is_neither_quarantined_nor_overwritten(tmp_path, monkeypatch):
    """读不了（Windows 上别的进程正占着 / 权限）不是坏文件：短暂重读；一直读不了就报出来，不备份、不用空配置覆盖。"""

    import pathlib

    path = tmp_path / "settings.json"
    S.save_settings(S.Settings(), path)
    S.update_settings({"roles": {"text": {"adapter": "openai_chat", "api_key": "sk-textKEY0000000000", "model": "m"}}}, path)
    original = path.read_bytes()
    real_read = pathlib.Path.read_text
    fails = {"left": 1}

    def flaky(self, *args, **kwargs):
        if self == path and fails["left"] > 0:
            fails["left"] -= 1
            raise PermissionError(13, "Permission denied", str(self))
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", flaky)
    # 瞬时占用：重读一次就好
    assert S.load_settings(path).role("text").api_key == "sk-textKEY0000000000"
    # 一直读不了：只读接口给「读不了」的人话，写盘路径报错、原文件原封不动
    fails["left"] = 10 ** 6
    settings, warning = S.load_settings_or_default(path)
    assert warning.startswith("设置文件读不了：") and "设置文件损坏" not in warning
    with pytest.raises(S.SettingsFileError) as info:
        S.update_settings({"roles": {"text": {"model": "other"}}}, path)
    assert info.value.unreadable
    fails["left"] = 0
    assert path.read_bytes() == original and list(tmp_path.glob("settings.json.broken-*")) == []


def test_load_settings_or_default_gives_the_reason(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    settings, warning = S.load_settings_or_default(path)
    assert isinstance(settings, S.Settings) and str(path) in warning and "Expecting" in warning
    with pytest.raises(ValueError):       # 老调用方的 except ValueError 仍接得住
        S.load_settings(path)


# ---------------------------------------------------------------------------- ADP-06 环境变量覆盖不落盘


def test_env_override_is_not_persisted_by_unrelated_update(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_IMAGE_API_KEY", "env-only-secret")
    monkeypatch.setenv("STORYSTUDIO_IMAGE_MODEL", "env-model")
    path = tmp_path / "settings.json"
    returned = S.update_settings({"roles": {"text": {"model": "gpt"}}}, path)
    assert returned.role("image").api_key == "env-only-secret"       # 接口回的是生效值
    on_disk = json.loads(path.read_text(encoding="utf-8"))["roles"]["image"]
    assert on_disk["api_key"] == "" and on_disk["model"] == ""       # 文件里只有用户写的
    assert S.load_settings(path, apply_env=False).role("image").api_key == ""
    monkeypatch.delenv("STORYSTUDIO_IMAGE_API_KEY")
    monkeypatch.delenv("STORYSTUDIO_IMAGE_MODEL")
    assert S.load_settings(path).role("image").api_key == ""


def test_env_override_survives_no_write_path(home, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_VIDEO_API_KEY", "env-video-secret")
    monkeypatch.setenv("STORYSTUDIO_VIDEO_ADAPTER", "ark_video")
    shown = client.get("/api/settings").json()["settings"]["roles"]["video"]
    assert shown["adapter"] == "ark_video" and "*" in shown["api_key"]
    assert client.post("/api/system/local-hint/dismiss", json={"role": "all"}).status_code == 200
    assert client.post("/api/system/local-hint/reset").status_code == 200
    client.put("/api/settings", json={"gateway": {"base_url": "http://gw.local/v1", "api_key": "sk-gwKEY00000000000"}})
    assert client.post("/api/settings/gateway/apply", json={"roles": {"text": {"model": "gpt-4o"}}}).status_code == 200
    assert client.post("/api/settings/local/apply", json={"service": "comfyui", "role": "image", "model": "x"}).status_code == 200
    on_disk = _on_disk(home)["roles"]["video"]
    assert on_disk["api_key"] == "" and on_disk["adapter"] == ""


# ---------------------------------------------------------------------------- ADP-09 参考图上限按来源查


def _cap_spec():
    cast = {f"c{i}": {"name": f"人{i}", "block": "x", "asset": f"c{i}.png"} for i in range(16)}
    ids = list(cast)
    return {"cast": cast, "image_params": {"model": "gpt-image-2"},
            "shots": [{"id": "S1", "dur": 4, "cast": ids[:1], "shot": "中景", "art": "a", "motion": "m"},
                      {"id": "S2", "dur": 4, "cast": ids, "shot": "近景", "art": "b", "motion": "m", "ref_frame": "S1",
                       "reference_roles": [{"target": "cast:c0", "use": "只取脸"}]}]}


def test_reference_cap_follows_the_configured_image_source(home):
    spec = _cap_spec()
    assert any("最多收 16 张" in m for m in lint.check_reference_roles(spec, image_adapter="openai_images"))
    assert lint.check_reference_roles(spec, image_adapter="ark_images") == []          # 这个来源下没记上限
    assert lint.check_reference_roles(spec) == []                                         # 没配来源不猜
    from webui import deps

    assert deps.configured_image_adapter() == ""
    client.put("/api/settings", json={"roles": {"image": {"adapter": "ark_images", "model": "gpt-image-2"}}})
    assert deps.configured_image_adapter() == "ark_images"
    from storyvideo import continuity

    assert not any("最多收" in m for m in continuity.check_all(spec, image_adapter=deps.configured_image_adapter()))
    client.put("/api/settings", json={"roles": {"image": {"adapter": "openai_images", "api_key": "sk-1234567890abcdef", "model": "gpt-image-2"}}})
    assert any("最多收 16 张" in m for m in continuity.check_all(spec, image_adapter=deps.configured_image_adapter()))


# ---------------------------------------------------------------------------- ADP-10 openai_images 尺寸档


def test_openai_images_capability_table_matches_what_the_adapter_sends(tmp_path):
    assert C.lookup("image", "openai_images", "gpt-image-2", use_catalog=False)["image_sizes"] == ["1K"]
    assert C.lookup("image", "openai_images", "gpt-image-1", use_catalog=False)["image_sizes"] == ["1K"]
    st = S.Settings()
    st.role("image").adapter = "openai_images"; st.role("image").api_key = "k"; st.role("image").model = "gpt-image-2"
    spec = {"image_params": {"model": "@image", "image_size": "2K"}, "video_params": {}, "shots": [], "cast": {}}
    msgs = [i["message"] for i in C.check_spec(spec, st)["issues"] if i["role"] == "image"]
    assert any("支持的尺寸档是 1K" in m and "spec 写的是 2K" in m for m in msgs)
    spec["image_params"]["image_size"] = "1K"
    assert not any("尺寸档" in i["message"] for i in C.check_spec(spec, st)["issues"])
    # 适配器本身仍只发 1024 系
    sent = {}
    fake = SimpleNamespace(images=SimpleNamespace(generate=lambda **kw: sent.update(kw) or SimpleNamespace(data=[])))
    OpenAIImages({"api_key": "k", "model": "gpt-image-2"}, client=fake, cache_dir=tmp_path).submit_image(
        ImageRequest(name="n", prompt="p", model="gpt-image-2", aspect_ratio="9:16", image_size="2K"))
    assert sent["size"] == "1024x1536"


# ---------------------------------------------------------------------------- ADP-11 count


def _video_spec(**video_params):
    return {"id": "EP99", "output_root": "output/story_video/EP99", "cast": {"su": {"block": "Su"}},
            "shots": [{"id": "S1", "dur": 4, "cast": ["su"], "lines": [{"who": "su", "en": "Hi.", "zh": "嗨。"}], "shot": "wide", "art": "a", "motion": "m"}],
            "video_params": video_params}


@pytest.mark.parametrize("model", ["doubao-seedance-2-0-260128", "kling-v2-5-turbo", "MiniMax-H3"])
@pytest.mark.parametrize("count", [4, 2, 0, -1, "4", 2.5, True])
def test_count_other_than_one_still_loads_and_lint_says_it_has_no_effect(tmp_path, model, count):
    """加载时不拦（之前 MiniMax-H3 明确放行 2 / 4，拦了老项目详情直接 404）；原值原样保留（它在出片请求指纹里，
    改了会让已出的镜头平白变过时）；lint 提醒这一项不起作用。"""

    extra = {"aspect_ratio": "adaptive"} if model == "MiniMax-H3" else {}
    spec = normalize_spec(_video_spec(model=model, generate_audio=True, count=count, **extra), project_root=tmp_path)
    assert spec["video_params"]["count"] == count and type(spec["video_params"]["count"]) is type(count)
    warns = lint.check_video_count(spec)
    assert len(warns) == 1 and warns[0].startswith("video_params.count 写的是 ") and "不起作用" in warns[0]
    assert warns[0] in lint.check_all(spec)


def test_count_absent_or_one_gives_no_warning(tmp_path):
    assert lint.check_video_count(normalize_spec(_video_spec(model="kling-v2-5-turbo"), project_root=tmp_path)) == []
    assert lint.check_video_count(normalize_spec(_video_spec(model="MiniMax-H3", aspect_ratio="adaptive", count=1), project_root=tmp_path)) == []


def test_old_minimax_count_project_opens_in_the_web_ui(home, tmp_path, monkeypatch):
    """审查复现：MiniMax-H3 + count 4 的老项目，修之前（加载时报 SpecError）详情 404、界面里打不开也改不了。"""

    from webui import deps
    from webui.routers import episodes as episodes_router

    root = tmp_path / "proj"
    content = root / "content" / "story_video"
    content.mkdir(parents=True)
    monkeypatch.setattr(episodes_router, "PROJECT_ROOT", root)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content)
    monkeypatch.setattr(episodes_router, "DELETED_DIR", root / ".webui_deleted")
    monkeypatch.setattr(deps, "PROJECT_ROOT", root)
    data = _video_spec(model="MiniMax-H3", aspect_ratio="adaptive", count=4)
    data["id"], data["output_root"] = "ADP11-COMPAT", str(root / "output" / "story_video" / "ADP11-COMPAT")
    (content / "ADP11-COMPAT.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    r = client.get("/api/episodes/storyvideo/ADP11-COMPAT")
    assert r.status_code == 200, r.text
    assert any(w.startswith("video_params.count 写的是 4") for w in r.json()["warnings"])


def test_count_one_still_passes(tmp_path):
    """count 原值原样留在项目里（在请求指纹里）；只发 1 条由各适配器自己保证（插件来源在插件自己的测试里断言）。"""

    assert normalize_spec(_video_spec(model="doubao-seedance-2-0-260128", count=1), project_root=tmp_path)["video_params"]["count"] == 1


# ---------------------------------------------------------------------------- ADP-12 短密钥打码


@pytest.mark.parametrize("key", ["abcdefghi", "abcdefghij", "abcdefghijk", "sk-12345678", "abcdefghijkl", "abcdefghijklmno"])
def test_short_keys_are_mostly_hidden(key):
    """9–15 位的密钥最多露首 2 尾 2，且至少遮一半（之前固定露首 4 尾 4，9 位只遮 1 个字符）。"""

    masked = S._mask(key)
    assert len(masked) == len(key) and masked.count("*") >= len(key) // 2
    assert masked == key[:2] + "*" * (len(key) - 4) + key[-2:]


def test_long_keys_keep_head_and_tail_and_masked_values_do_not_overwrite(tmp_path):
    assert S._mask("sk-1234567890abcdef") == "sk-1***********cdef"
    assert "secret" not in S._mask("sk-litellm-secret")
    assert S._mask("abcdefghijklmnop") == "abcd********mnop" and S._mask("abcdefgh") == "********"
    path = tmp_path / "settings.json"
    S.update_settings({"roles": {"text": {"adapter": "openai_chat", "api_key": "abcdefghi", "model": "m"}}}, path)
    masked = S.load_settings(path).as_dict(mask_secrets=True)["roles"]["text"]["api_key"]
    assert masked == "ab*****hi"
    S.update_settings({"roles": {"text": {"api_key": masked}}}, path)
    assert S.load_settings(path).role("text").api_key == "abcdefghi"


# ---------------------------------------------------------------------------- FAM-7 试听未配置 → 400


def test_sample_with_unconfigured_settings_engine_is_400_not_502(home):
    assert client.post("/api/voices", json={"id": "fam7-settings", "name": "x", "engine": "settings", "voice": "alloy"}).status_code == 200
    r = client.post("/api/voices/fam7-settings/sample", json={})
    assert r.status_code == 400 and "voice 模型未配置" in r.json()["detail"]
    assert client.post("/api/voices", json={"id": "fam7-minimax", "name": "y", "engine": "minimax", "voice": "male-qn-qingse"}).status_code == 200
    assert client.post("/api/voices/fam7-minimax/sample", json={}).status_code == 400
