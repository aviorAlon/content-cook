"""出片（契约 docs/clip-spec.md §4）：同步 / 异步来源、从不覆盖、请求哈希跳过、force 重出、失败不动 current、
三种首帧模式、断点续等。

用假来源（同步 / 异步两种），不联网、不花钱。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from clip import generate as G
from clip import prompts as P
from clip import spec as S
from genkit.providers import ProviderError, TaskSnapshot, TaskState, TaskSubmission, TaskType, path_to_file_url
from tests.test_clip_spec import build


def png_bytes(color=(120, 60, 80)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


class FakeProvider:
    """同步型：submit 直接 FINISHED 带 ``file:`` 任务 id。``async_rounds`` > 0 时先 PROCESSING 几轮再完成。"""

    def __init__(self, cache: Path, *, fail: bool = False, raises: bool = False, async_rounds: int = 0,
                 frame_fail: bool = False, empty: bool = False):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.fail = fail
        self.raises = raises
        self.async_rounds = async_rounds
        self.frame_fail = frame_fail
        self.empty = empty
        self.videos: list = []
        self.images: list = []
        self.polls = 0

    # ---- 生图（首帧）
    def submit_image(self, request):
        self.images.append(request)
        if self.frame_fail:
            return TaskSubmission(task_id=None, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message="内容被审核拦截")
        path = self.cache / f"{request.name}-{len(self.images)}.png"
        path.write_bytes(png_bytes())
        return TaskSubmission(task_id=f"file:{path}", task_type=TaskType.IMAGE, status=TaskState.FINISHED,
                              output_urls=(path_to_file_url(path),))

    # ---- 视频
    def submit_video(self, request):
        self.videos.append(request)
        if self.raises:
            raise ProviderError("网关连不上")
        if self.fail:
            return TaskSubmission(task_id=None, task_type=TaskType.VIDEO, status=TaskState.FAILED, error_message="余额不足")
        path = self.cache / f"{request.name}.mp4"
        path.write_bytes(b"" if self.empty else b"fake-mp4-bytes")
        if self.async_rounds:
            return TaskSubmission(task_id=f"remote:{path}", task_type=TaskType.VIDEO, status=TaskState.PROCESSING)
        return TaskSubmission(task_id=f"file:{path}", task_type=TaskType.VIDEO, status=TaskState.FINISHED,
                              output_urls=(path_to_file_url(path),))

    def get_task(self, task_id, task_type):
        self.polls += 1
        path = Path(task_id.split(":", 1)[1])
        if task_id.startswith("remote:") and self.polls < self.async_rounds:
            return TaskSnapshot(task_id=task_id, task_type=task_type, status=TaskState.PROCESSING)
        return TaskSnapshot(task_id=task_id, task_type=task_type, status=TaskState.FINISHED,
                            output_urls=(path_to_file_url(path),))

    def download(self, url, destination, **kw):
        src = Path(url.replace("file:///", "").replace("file://", ""))
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, destination)
        return Path(destination)


def videos(spec) -> list[str]:
    root = S.video_dir(spec)
    return sorted(p.name for p in root.glob("*.mp4")) if root.is_dir() else []


def run(spec, provider, **kw):
    return G.generate_clip(spec, provider=provider, sleep=lambda _s: None, poll=0, **kw)


# ---------------------------------------------------------------------------- 同步 / 异步


def test_sync_source_writes_one_take(tmp_path):
    spec = build(tmp_path)
    S.save(spec)
    provider = FakeProvider(tmp_path / "cache")
    result = run(spec, provider)
    assert result["skipped"] is False and result["error"] is None and result["file"] == "CL-T-1-1.mp4"
    assert videos(spec) == ["CL-T-1-1.mp4"] and spec["current"] == "CL-T-1-1.mp4"
    (attempt,) = spec["attempts"]
    assert attempt["file"] == "CL-T-1-1.mp4" and attempt["model"] == "@video" and attempt["error"] is None
    assert attempt["task_id"] and attempt["request_hash"] and attempt["created_at"]
    # 请求形状：三段提示词 + 时长 / 画幅 / 分辨率 / 音频；没有首帧就是文生视频
    (request,) = provider.videos
    assert request.prompt.startswith(P.HEAD_GLOBAL[0]) and P.HEAD_TIMELINE[0] in request.prompt
    assert request.duration == 3 and request.aspect_ratio == "16:9" and request.resolution == "1080p"
    assert request.generate_audio is False and request.request_type == "text_prompt" and request.references == ()
    # 逐次写盘：任务中途挂了也不丢
    assert S.load("CL-T-1", project_root=tmp_path)["current"] == "CL-T-1-1.mp4"


def test_async_source_is_polled_inside_the_task(tmp_path):
    spec = build(tmp_path)
    S.save(spec)
    provider = FakeProvider(tmp_path / "cache", async_rounds=3)
    result = run(spec, provider)
    assert result["file"] == "CL-T-1-1.mp4" and provider.polls == 3
    assert spec["attempts"][0]["task_id"].startswith("remote:")
    assert (S.video_dir(spec) / "CL-T-1-1.mp4").read_bytes() == b"fake-mp4-bytes"


def test_async_timeout_is_a_failed_attempt(tmp_path):
    spec = build(tmp_path)
    S.save(spec)
    provider = FakeProvider(tmp_path / "cache", async_rounds=999)
    result = G.generate_clip(spec, provider=provider, sleep=lambda _s: None, poll=0, max_wait=0)
    assert result["file"] is None and "还没出片" in result["error"]
    assert spec["current"] is None and spec["attempts"][0]["error"]


# ---------------------------------------------------------------------------- 从不覆盖 / 跳过 / force


def test_never_overwrites_and_force_adds_a_new_take(tmp_path):
    spec = build(tmp_path)
    S.save(spec)
    provider = FakeProvider(tmp_path / "cache")
    run(spec, provider)
    run(spec, provider, force=True)
    run(spec, provider, force=True)
    assert videos(spec) == ["CL-T-1-1.mp4", "CL-T-1-2.mp4", "CL-T-1-3.mp4"]
    assert [a["file"] for a in spec["attempts"]] == videos(spec)
    assert spec["current"] == "CL-T-1-3.mp4" and len(provider.videos) == 3


def test_same_request_is_skipped_until_the_prompt_changes(tmp_path):
    spec = build(tmp_path)
    S.save(spec)
    provider = FakeProvider(tmp_path / "cache")
    run(spec, provider)
    again = run(spec, provider)
    assert again["skipped"] is True and again["file"] == "CL-T-1-1.mp4" and len(provider.videos) == 1
    # 改了提示词里的任何一个字 → 不再跳过
    spec["beats"][0]["picture"] = "薄雾里的城市天际线与电视塔同框"
    third = run(spec, provider)
    assert third["skipped"] is False and third["file"] == "CL-T-1-2.mp4" and len(provider.videos) == 2
    assert spec["attempts"][0]["request_hash"] != spec["attempts"][1]["request_hash"]


def test_failure_keeps_the_previous_take(tmp_path):
    """一次失败不动 current：上一条能播的片子还在，失败原因记在 attempts 里。"""

    spec = build(tmp_path)
    S.save(spec)
    run(spec, FakeProvider(tmp_path / "cache"))
    result = run(spec, FakeProvider(tmp_path / "cache", fail=True), force=True)
    assert result["file"] is None and "余额不足" in result["error"]
    assert spec["current"] == "CL-T-1-1.mp4" and len(spec["attempts"]) == 2
    assert spec["attempts"][1]["error"] and spec["attempts"][1]["task_id"] is None
    assert S.load("CL-T-1", project_root=tmp_path)["current"] == "CL-T-1-1.mp4"
    # 来源直接抛异常、下载下来是空文件，也都只是一次失败的 attempt
    assert "网关连不上" in run(spec, FakeProvider(tmp_path / "cache", raises=True), force=True)["error"]
    assert "空文件" in run(spec, FakeProvider(tmp_path / "cache", empty=True), force=True)["error"]
    assert spec["current"] == "CL-T-1-1.mp4"


# ---------------------------------------------------------------------------- 首帧三种模式


def test_first_frame_none_is_text_to_video(tmp_path):
    spec = build(tmp_path)
    S.save(spec)
    provider = FakeProvider(tmp_path / "cache")
    run(spec, provider)
    assert provider.images == [] and provider.videos[0].request_type == "text_prompt"


def test_first_frame_generate_makes_one_image_request_then_the_video(tmp_path):
    spec = build(tmp_path, first_frame={"mode": "generate"})
    S.save(spec)
    provider = FakeProvider(tmp_path / "cache")
    result = run(spec, provider)
    assert len(provider.images) == 1 and len(provider.videos) == 1
    assert provider.images[0].prompt.startswith(P.HEAD_GLOBAL[0] + "单帧静止画面")
    assert provider.videos[0].request_type == "first_frame"
    assert provider.videos[0].references[0].type == "first_frame"
    assert result["frame"]["skipped"] is False and result["file"] == "CL-T-1-1.mp4"
    assert spec["first_frame"]["asset"] == "output/clip/CL-T-1/00_frame/CL-T-1-frame-1.png"
    assert S.first_frame_path(spec).is_file()
    # 第二次出片不再重出首帧（首帧就一张，出片可以出很多条）
    run(spec, provider, force=True)
    assert len(provider.images) == 1 and len(provider.videos) == 2


def test_first_frame_generate_failure_stops_before_the_video(tmp_path):
    spec = build(tmp_path, first_frame={"mode": "generate"})
    S.save(spec)
    provider = FakeProvider(tmp_path / "cache", frame_fail=True)
    result = run(spec, provider)
    assert result["file"] is None and "首帧没出来" in result["error"] and "审核" in result["error"]
    assert provider.videos == [] and spec["attempts"] == []          # 视频请求根本没发出去
    assert S.load("CL-T-1", project_root=tmp_path)["first_frame"]["error"]


def test_first_frame_upload(tmp_path):
    spec = build(tmp_path, first_frame={"mode": "upload"})
    S.save(spec)
    provider = FakeProvider(tmp_path / "cache")
    # 还没上传就出片 → 明确的一句话，不提交
    assert "还没有上传首帧" in run(spec, provider)["error"]
    assert provider.videos == []
    info = G.attach_first_frame(spec, png_bytes(), "我的首帧.png")
    assert info["file"].endswith(".png") and info["bytes"] > 0
    assert spec["first_frame"]["mode"] == "upload" and spec["first_frame"]["asset"].startswith("output/clip/CL-T-1/00_frame/")
    run(spec, provider)
    assert provider.images == [] and provider.videos[0].request_type == "first_frame"
    # 同一张图再传一次不覆盖也不重复（按内容摘要命名）
    again = G.attach_first_frame(spec, png_bytes(), "我的首帧.png")
    assert again["file"] == info["file"] and len(list(S.frame_dir(spec).glob("*.png"))) == 1


def test_changing_the_first_frame_changes_the_request_hash(tmp_path):
    spec = build(tmp_path, first_frame={"mode": "upload"})
    S.save(spec)
    provider = FakeProvider(tmp_path / "cache")
    G.attach_first_frame(spec, png_bytes((10, 10, 10)), "a.png")
    run(spec, provider)
    G.attach_first_frame(spec, png_bytes((200, 30, 30)), "b.png")
    result = run(spec, provider)
    assert result["skipped"] is False and result["file"] == "CL-T-1-2.mp4"


# ---------------------------------------------------------------------------- 状态 / 断点续等 / 花钱提示


def test_clip_status(tmp_path):
    spec = build(tmp_path)
    S.save(spec)
    assert G.clip_status(spec)["status"] == "todo"
    run(spec, FakeProvider(tmp_path / "cache"))
    status = G.clip_status(spec)
    assert status == {**status, "done": True, "file": "CL-T-1-1.mp4", "attempts": 1, "stale": False,
                      "inflight": None, "error": None, "status": "done"}
    spec["beats"][0]["picture"] = "改过的画面"
    assert G.clip_status(spec)["stale"] is True                      # 提示词改过，片子还是旧请求的
    spec["attempts"].append({"file": "CL-T-1-2.mp4", "created_at": 1, "model": "@video", "request_hash": "h",
                             "task_id": "remote:x", "error": None})
    assert G.clip_status(spec)["status"] == "running"
    assert G.inflight_attempt(spec)["task_id"] == "remote:x"


def test_resume_only_polls_and_downloads(tmp_path):
    """服务重启：提交拿到 task_id 就已经写盘了，重开之后接着等，绝不再提交一次。"""

    spec = build(tmp_path)
    S.save(spec)
    provider = FakeProvider(tmp_path / "cache", async_rounds=999)
    G.generate_clip(spec, provider=provider, sleep=lambda _s: None, poll=0, max_wait=0)
    spec["attempts"][0]["error"] = None                              # 上一轮记了超时，重启时当作还在飞
    S.save(spec)
    reloaded = S.load("CL-T-1", project_root=tmp_path)
    assert G.inflight_attempt(reloaded)["file"] == "CL-T-1-1.mp4"
    resumed = G.resume_clip(reloaded, provider=FakeProvider(tmp_path / "cache"), sleep=lambda _s: None, poll=0)
    assert resumed == {"file": "CL-T-1-1.mp4", "resumed": True, "error": None}
    assert reloaded["current"] == "CL-T-1-1.mp4" and len(reloaded["attempts"]) == 1
    assert len(provider.videos) == 1                                 # 没有第二次提交
    # 没有在飞的任务时什么都不做
    assert G.resume_clip(reloaded, provider=provider)["resumed"] is False


def test_cost_note(tmp_path):
    spec = build(tmp_path)
    assert G.cost_note(spec) == "1 次视频请求（@video，3 秒）；请求没变且片子已在的会跳过"
    frame = build(tmp_path, first_frame={"mode": "generate"})
    assert G.cost_note(frame).startswith("1 次生图请求（首帧，@image）+ 1 次视频请求（@video，3 秒）")
    assert "force 会重出一条新的" in G.cost_note(spec, force=True)


@pytest.mark.parametrize("mode", ["none", "generate", "upload"])
def test_request_hash_covers_mode(tmp_path, mode):
    spec = build(tmp_path, first_frame={"mode": mode})
    digest = G.request_hash({"prompt": "x", "first_frame_mode": mode}, frame=None)
    assert len(digest) == 24
    assert G.request_hash({"prompt": "x", "first_frame_mode": "none"}, frame=None) == \
        G.request_hash({"prompt": "x", "first_frame_mode": "none", "first_frame_asset": "a"}, frame=None)
    assert G.clip_status(spec)["request_hash"]
