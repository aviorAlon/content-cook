"""出片（契约 docs/clip-spec.md §4）。

* 一次请求 = 一条片子：``01_video/<ID>-<n>.mp4``，**从不覆盖**；``attempts[]`` 记
  ``{file, created_at, model, request_hash, task_id, error}``，``current`` 指向采用的那一条。
* ``first_frame.mode``：``none`` 直接文生视频；``upload`` 用上传的图；``generate`` 先按【全片设定】+ 第一个节拍
  出一张首帧（**一次**生图请求），再图生视频。
* 请求哈希（提示词 / 模型 / 时长 / 画幅 / 分辨率 / 音频 + 首帧内容摘要）不变且文件在 → 跳过，``force`` 重出。
* 同步来源直接落盘；异步来源在任务里轮询（最多等 45 分钟）。**中途服务重启也不丢**：提交拿到 task_id 就先把
  attempt 写进项目文件，``resume_clip()`` 能从项目文件里接着等（``webui.generate.resume_inflight_watchers``
  启动时扫一遍 ``content/clip/``）。
* 失败不覆盖已有产物：失败的 attempt 记下原因，``current`` 还指着上一条能播的片子。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from genkit.adapters.base import local_token
from genkit.providers import AssetReference, ImageRequest, ProviderError, TaskState, TaskType, VideoRequest
from storyvideo.spec import stable_json_hash

from .prompts import frame_request_data, video_request_data
from .spec import current_path, first_frame_path, frame_dir, save, video_dir

MAX_WAIT_SECONDS = 45 * 60          # 视频比出图慢得多；一条片子最多等这么久
FRAME_WAIT_SECONDS = 20 * 60
POLL_SECONDS = 5.0


class ClipGenerationError(RuntimeError):
    """出片失败（提交被拒、轮询超时、下载空文件…）。"""


def _file_digest(path: str | Path | None) -> str:
    if not path:
        return "none"
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def request_hash(data: Mapping[str, Any], *, frame: str | Path | None = None) -> str:
    """请求指纹：提示词 / 模型 / 时长 / 画幅 / 分辨率 / 音频 + 首帧内容摘要（换了首帧也算变了）。"""

    payload = {k: v for k, v in data.items() if k != "first_frame_asset"}
    payload["first_frame_digest"] = _file_digest(frame)
    return stable_json_hash(payload)[:24]


def clip_status(spec: Mapping[str, Any]) -> dict[str, Any]:
    """接口层要的状态：有没有片子、提示词改过没（stale）、在飞的那次、历次尝试数、最近一次错误。"""

    path = current_path(spec)
    frame = first_frame_path(spec)
    digest = request_hash(video_request_data(spec), frame=frame)
    attempts = list(spec.get("attempts") or ())
    current = next((a for a in attempts if a.get("file") == spec.get("current")), None)
    inflight = inflight_attempt(spec)
    last_error = next((a.get("error") for a in reversed(attempts) if a.get("error")), None)
    return {
        "done": path is not None,
        "file": path.name if path else None,
        "attempts": len(attempts),
        "stale": bool(path) and bool((current or {}).get("request_hash")) and (current or {}).get("request_hash") != digest,
        "request_hash": digest,
        "inflight": dict(inflight) if inflight else None,
        "error": last_error,
        "first_frame": str(frame) if frame else None,
        # 在飞的那次优先：已经有一条能播的片子、同时又提交了新的一次，界面要显示「还在出」
        "status": "running" if inflight else ("done" if path else ("failed" if last_error else "todo")),
    }


def inflight_attempt(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    """最后一次尝试还在服务商那边：有 task_id、没记错误、文件还没落盘。"""

    for attempt in reversed(list(spec.get("attempts") or ())):
        if attempt.get("error"):
            return None
        if not attempt.get("task_id"):
            return None
        path = video_dir(spec) / str(attempt.get("file") or "")
        return None if path.is_file() else dict(attempt)
    return None


def _next_video_path(spec: Mapping[str, Any]) -> Path:
    root = video_dir(spec)
    n = len(spec.get("attempts") or []) + 1
    while (root / f"{spec['id']}-{n}.mp4").exists():
        n += 1
    return root / f"{spec['id']}-{n}.mp4"


def _next_frame_path(spec: Mapping[str, Any]) -> Path:
    root = frame_dir(spec)
    n = 1
    while (root / f"{spec['id']}-frame-{n}.png").exists():
        n += 1
    return root / f"{spec['id']}-frame-{n}.png"


def _provider_or_default(provider: Any) -> Any:
    if provider is not None:
        return provider
    from genkit.router import build_provider

    return build_provider()


def _wait_for_result(provider: Any, task_id: str, kind: TaskType, *, max_wait: float, poll: float,
                     sleep: Callable[[float], None], note: Callable[..., None] | None, label: str) -> tuple[str, ...]:
    started = time.monotonic()
    rounds = 0
    while True:
        snapshot = provider.get_task(task_id, kind)
        if snapshot.status is TaskState.FINISHED:
            if not snapshot.output_urls:
                raise ClipGenerationError("来源说完成了，但没有给结果地址")
            return tuple(snapshot.output_urls)
        if snapshot.status is TaskState.FAILED:
            raise ClipGenerationError(snapshot.error_message or "来源返回失败")
        rounds += 1
        if note:
            note(current=label, waiting_rounds=rounds)
        if time.monotonic() - started >= max_wait:
            raise ClipGenerationError(f"等了 {int(max_wait // 60)} 分钟还没出片（远端任务 {task_id}），先放弃这一次")
        sleep(poll)


# ---------------------------------------------------------------------------- 首帧


def generate_first_frame(spec: dict[str, Any], *, force: bool = False, provider: Any = None,
                         note: Callable[..., None] | None = None, max_wait: float = FRAME_WAIT_SECONDS,
                         poll: float = POLL_SECONDS, sleep: Callable[[float], None] = time.sleep,
                         write: bool = True) -> dict[str, Any]:
    """按【全片设定】+ 第一个节拍出一张首帧（一次生图请求）。已经有图且没 ``force`` → 跳过。"""

    first = spec.setdefault("first_frame", {"mode": "generate", "asset": None, "prompt": ""})
    existing = first_frame_path(spec)
    if existing is not None and not force:
        return {"file": existing.name, "asset": first.get("asset"), "skipped": True, "error": None}
    provider = _provider_or_default(provider)
    data = frame_request_data(spec)
    frame_dir(spec).mkdir(parents=True, exist_ok=True)
    target = _next_frame_path(spec)
    if note:
        note(current="first-frame")
    try:
        request = ImageRequest(name=data["name"], prompt=data["prompt"], model=data["model"],
                               aspect_ratio=data["aspect_ratio"], image_size=data["image_size"], quality=None)
        submission = provider.submit_image(request)
        if submission.status is TaskState.FAILED or not submission.task_id:
            raise ClipGenerationError(submission.error_message or "生图来源没有返回结果")
        if submission.status is TaskState.FINISHED and submission.output_urls:
            urls = tuple(submission.output_urls)
        else:
            urls = _wait_for_result(provider, submission.task_id, TaskType.IMAGE, max_wait=max_wait, poll=poll,
                                    sleep=sleep, note=note, label="first-frame")
        provider.download(urls[0], target)
        if not target.is_file() or target.stat().st_size == 0:
            raise ClipGenerationError("下载下来的首帧是空文件")
    except (ProviderError, ClipGenerationError, OSError, ValueError) as exc:
        message = str(exc)[:500]
        first["error"] = message
        if write:
            save(spec)
        return {"file": None, "asset": first.get("asset"), "skipped": False, "error": message}
    relative = target.resolve().relative_to(Path(str(spec["project_root"])).resolve()).as_posix()
    first.update({"mode": "generate", "asset": relative, "error": None})
    if write:
        save(spec)
    return {"file": target.name, "asset": relative, "skipped": False, "error": None}


def attach_first_frame(spec: dict[str, Any], data: bytes, filename: str, *, write: bool = True,
                       suffix: str | None = None) -> dict[str, Any]:
    """上传首帧：字节落进 ``00_frame/``（按内容摘要命名，**不覆盖**），路径写进 ``first_frame.asset``。
    ``suffix`` 由调用方按内容嗅探出来的真实格式给；不给才退回文件名的扩展名。"""

    from genkit.ids import slugify

    suffix = str(suffix or "").lower() or Path(filename or "").suffix.lower() or ".png"
    stem = slugify(Path(filename or "frame").stem, fallback="frame")
    digest = hashlib.sha256(data).hexdigest()[:8]
    root = frame_dir(spec)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{stem}-{digest}{suffix}"
    if not target.exists():
        target.write_bytes(data)
    relative = target.resolve().relative_to(Path(str(spec["project_root"])).resolve()).as_posix()
    spec.setdefault("first_frame", {})
    spec["first_frame"].update({"mode": "upload", "asset": relative, "error": None})
    if write:
        save(spec)
    return {"file": target.name, "asset": relative, "bytes": len(data)}


# ---------------------------------------------------------------------------- 出片


def _video_request(spec: Mapping[str, Any], data: Mapping[str, Any], frame: Path | None, name: str) -> VideoRequest:
    references: tuple[AssetReference, ...] = ()
    request_type = "text_prompt"
    if frame is not None:
        references = (AssetReference(local_token(frame), "first_frame"),)
        request_type = "first_frame"
    return VideoRequest(
        name=name, prompt=str(data["prompt"]), model=str(data["model"]), duration=int(data["duration"]),
        request_type=request_type, references=references, aspect_ratio=str(data["aspect_ratio"]),
        resolution=str(data["resolution"]), generate_audio=bool(data["generate_audio"]),
    )


def generate_clip(spec: dict[str, Any], *, force: bool = False, provider: Any = None,
                  note: Callable[..., None] | None = None, max_wait: float = MAX_WAIT_SECONDS,
                  poll: float = POLL_SECONDS, sleep: Callable[[float], None] = time.sleep,
                  write: bool = True) -> dict[str, Any]:
    """后台任务体（``clip-generate``）。就地更新 ``spec`` 并写盘；返回 ``{file, skipped, attempt, frame, error}``。"""

    provider = _provider_or_default(provider)
    result_frame: dict[str, Any] | None = None
    mode = str((spec.get("first_frame") or {}).get("mode") or "none")
    if mode == "generate":
        result_frame = generate_first_frame(spec, force=False, provider=provider, note=note, poll=poll,
                                            sleep=sleep, write=write)
        if result_frame.get("error"):
            return {"file": None, "skipped": False, "attempt": None, "frame": result_frame,
                    "error": f"首帧没出来，视频没有提交：{result_frame['error']}"}
    frame = first_frame_path(spec)
    if mode == "upload" and frame is None:
        return {"file": None, "skipped": False, "attempt": None, "frame": None,
                "error": "首帧模式是「用上传的图」，但还没有上传首帧"}

    data = video_request_data(spec)
    digest = request_hash(data, frame=frame)
    current = current_path(spec)
    same = next((a for a in spec.get("attempts") or () if a.get("file") == spec.get("current")), None)
    if not force and current is not None and (same or {}).get("request_hash") == digest:
        return {"file": current.name, "skipped": True, "attempt": dict(same or {}), "frame": result_frame, "error": None}

    video_dir(spec).mkdir(parents=True, exist_ok=True)
    target = _next_video_path(spec)
    attempt: dict[str, Any] = {"file": target.name, "created_at": time.time(), "model": data["model"],
                               "request_hash": digest, "task_id": None, "error": None}
    spec.setdefault("attempts", []).append(attempt)
    if note:
        note(current=str(spec.get("id")), progress="1/1")
    try:
        request = _video_request(spec, data, frame, f"{data['name']}-{len(spec['attempts'])}")
        submission = provider.submit_video(request)
        if submission.status is TaskState.FAILED or not submission.task_id:
            raise ClipGenerationError(submission.error_message or "视频来源没有返回结果")
        attempt["task_id"] = submission.task_id
        if write:
            save(spec)          # 先把 task_id 落盘：这一刻之后服务重启也能接着等
        if submission.status is TaskState.FINISHED and submission.output_urls:
            urls = tuple(submission.output_urls)
        else:
            urls = _wait_for_result(provider, submission.task_id, TaskType.VIDEO, max_wait=max_wait, poll=poll,
                                    sleep=sleep, note=note, label=str(spec.get("id")))
        provider.download(urls[0], target)
        if not target.is_file() or target.stat().st_size == 0:
            raise ClipGenerationError("下载下来的片子是空文件")
    except (ProviderError, ClipGenerationError, OSError, ValueError) as exc:
        attempt["error"] = str(exc)[:500]
        if write:
            save(spec)          # 失败不动 current：上一条能播的片子还在
        return {"file": None, "skipped": False, "attempt": dict(attempt), "frame": result_frame, "error": attempt["error"]}
    spec["current"] = target.name
    if write:
        save(spec)
    return {"file": target.name, "skipped": False, "attempt": dict(attempt), "frame": result_frame, "error": None}


def resume_clip(spec: dict[str, Any], *, provider: Any = None, note: Callable[..., None] | None = None,
                max_wait: float = MAX_WAIT_SECONDS, poll: float = POLL_SECONDS,
                sleep: Callable[[float], None] = time.sleep, write: bool = True) -> dict[str, Any]:
    """断点续等：只轮询 + 下载上一次提交的那条，**绝不提交新任务**（服务重启后由 lifespan 调起）。"""

    attempt = inflight_attempt(spec)
    if not attempt:
        return {"file": None, "resumed": False, "error": None}
    provider = _provider_or_default(provider)
    live = next(a for a in spec["attempts"] if a.get("file") == attempt["file"])
    target = video_dir(spec) / str(attempt["file"])
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        urls = _wait_for_result(provider, str(attempt["task_id"]), TaskType.VIDEO, max_wait=max_wait, poll=poll,
                                sleep=sleep, note=note, label=str(spec.get("id")))
        provider.download(urls[0], target)
        if not target.is_file() or target.stat().st_size == 0:
            raise ClipGenerationError("下载下来的片子是空文件")
    except (ProviderError, ClipGenerationError, OSError, ValueError) as exc:
        live["error"] = str(exc)[:500]
        if write:
            save(spec)
        return {"file": None, "resumed": True, "error": live["error"]}
    spec["current"] = target.name
    if write:
        save(spec)
    return {"file": target.name, "resumed": True, "error": None}


def cost_note(spec: Mapping[str, Any], *, force: bool = False) -> str:
    """点「出片」之前那一句：这次会发几个付费请求。视频来源配在本机时说「本地生成，不花钱」（DESIGN §45）。"""

    from genkit.local import cost_note_for

    params = spec.get("video_params") or {}
    model = str(params.get("model") or "@video")
    mode = str((spec.get("first_frame") or {}).get("mode") or "none")
    frame_needed = mode == "generate" and (first_frame_path(spec) is None or force)
    local = cost_note_for("video", 2 if frame_needed else 1, "")
    if local:
        return local
    head = f"1 次视频请求（{model}，{params.get('duration')} 秒）"
    if frame_needed:
        head = f"1 次生图请求（首帧，{(spec.get('image_params') or {}).get('model')}）+ " + head
    tail = "；请求没变且片子已在的会跳过" if not force else "；force 会重出一条新的（旧的不删）"
    return head + tail


__all__ = [
    "MAX_WAIT_SECONDS", "FRAME_WAIT_SECONDS", "POLL_SECONDS", "ClipGenerationError", "request_hash", "clip_status",
    "inflight_attempt", "generate_first_frame", "attach_first_frame", "generate_clip", "resume_clip", "cost_note",
]
