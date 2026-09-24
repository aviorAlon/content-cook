"""插画生成（契约 §6 ``pages/generate``）。

* ``manifests/pages.json``：每页的请求哈希、当前产物指针、历次尝试；请求哈希不变且文件在 → 跳过（``force`` 除外）
* 从不覆盖：每次写 ``05_pages/P01-<attempt>.png``，``current`` 指针指向最新一次；``page_image_path`` 按指针取
* 同步出图（网关返回 base64 / file: 任务）直接落盘；异步来源在任务内轮询，单页最多等 20 分钟
* 一页失败不拦其他页；原因进结果，任务记录里能看到
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from genkit.adapters.base import local_token
from genkit.providers import ImageRequest, ProviderError, TaskState, TaskType
from storyvideo.spec import stable_json_hash
from storyvideo.state import atomic_write_json, read_json

from .prompts import page_request_data
from .spec import manifests_dir, page_ids, pages_dir

MAX_WAIT_SECONDS = 20 * 60
POLL_SECONDS = 5.0


class PageGenerationError(RuntimeError):
    pass


def manifest_path(spec: Mapping[str, Any]) -> Path:
    return manifests_dir(spec) / "pages.json"


def load_manifest(spec: Mapping[str, Any]) -> dict[str, Any]:
    data = read_json(manifest_path(spec), missing=None)
    if not isinstance(data, dict):
        return {"pages": {}}
    data.setdefault("pages", {})
    return data


def _file_digest(path: str) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def request_hash(data: Mapping[str, Any]) -> str:
    """请求指纹：提示词 / 负面 / 模型 / 画幅 / 尺寸 + 参考图内容摘要（换了定妆图也算变了）。"""

    payload = {k: v for k, v in data.items() if k != "reference_assets"}
    payload["reference_digests"] = [_file_digest(p) for p in data.get("reference_assets") or ()]
    return stable_json_hash(payload)[:24]


def page_image_path(spec: Mapping[str, Any], page_id: str, manifest: Mapping[str, Any] | None = None) -> Path | None:
    """当前插画：清单指针指向的文件；没有清单时退回手动放进去的 ``P01.png``。"""

    manifest = manifest or load_manifest(spec)
    root = pages_dir(spec)
    entry = (manifest.get("pages") or {}).get(page_id) or {}
    current = entry.get("current")
    if current:
        path = root / str(current)
        if path.is_file():
            return path
    fallback = root / f"{page_id}.png"
    return fallback if fallback.is_file() else None


def page_status(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    manifest = load_manifest(spec)
    out: dict[str, dict[str, Any]] = {}
    for pid in page_ids(spec):
        entry = (manifest.get("pages") or {}).get(pid) or {}
        path = page_image_path(spec, pid, manifest)
        try:
            current_hash = request_hash(page_request_data(spec, pid))
        except Exception:  # noqa: BLE001 - 编译失败的页不该让详情页整页报错
            current_hash = None
        out[pid] = {
            "done": path is not None, "file": path.name if path else None, "attempts": len(entry.get("attempts") or []),
            "stale": bool(path) and bool(entry.get("request_hash")) and entry.get("request_hash") != current_hash,
            "error": entry.get("error"), "model": entry.get("model"),
        }
    return out


def _next_attempt_name(page_id: str, entry: Mapping[str, Any]) -> str:
    n = len(entry.get("attempts") or []) + 1
    return f"{page_id}-{n}.png"


def _wait_for_result(provider: Any, task_id: str, *, max_wait: float, poll: float, sleep: Callable[[float], None],
                     note: Callable[..., None] | None, label: str) -> tuple[str, ...]:
    started = time.monotonic()
    rounds = 0
    while True:
        snapshot = provider.get_task(task_id, TaskType.IMAGE)
        if snapshot.status is TaskState.FINISHED:
            if not snapshot.output_urls:
                raise PageGenerationError("生图来源说完成了，但没有给结果地址")
            return tuple(snapshot.output_urls)
        if snapshot.status is TaskState.FAILED:
            raise PageGenerationError(snapshot.error_message or "生图来源返回失败")
        rounds += 1
        if note:
            note(current=label, waiting_rounds=rounds)
        if time.monotonic() - started >= max_wait:
            raise PageGenerationError(f"等了 {int(max_wait // 60)} 分钟还没出图（远端任务 {task_id}），先放弃这一页")
        sleep(poll)


def generate_pages(spec: Mapping[str, Any], *, only: list[str] | None = None, force: bool = False, provider: Any = None,
                   note: Callable[..., None] | None = None, max_wait: float = MAX_WAIT_SECONDS, poll: float = POLL_SECONDS,
                   sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """后台任务体。返回 ``{generated, skipped, failed}``（页 id 列表 / [{page, error}]）。"""

    wanted = set(only) if only else None
    all_ids = page_ids(spec)
    unknown = sorted(wanted - set(all_ids)) if wanted else []
    if unknown:
        raise PageGenerationError(f"没有这些页：{', '.join(unknown)}")
    if provider is None:
        from genkit.router import build_provider

        provider = build_provider()
    root = pages_dir(spec)
    root.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(spec)
    targets = [pid for pid in all_ids if not wanted or pid in wanted]
    generated: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, Any]] = []
    for number, pid in enumerate(targets, 1):
        data = page_request_data(spec, pid)
        digest = request_hash(data)
        entry = manifest["pages"].setdefault(pid, {})
        if not force and entry.get("request_hash") == digest and page_image_path(spec, pid, manifest) is not None:
            skipped.append(pid)
            continue
        if note:
            note(progress=f"{number}/{len(targets)}", current=pid)
        target = root / _next_attempt_name(pid, entry)
        attempt: dict[str, Any] = {"file": target.name, "hash": digest, "model": data["model"], "created_at": time.time(), "task_id": None}
        try:
            request = ImageRequest(
                name=data["name"], prompt=data["prompt"], negative_prompt=data["negative_prompt"], model=data["model"],
                aspect_ratio=data["aspect_ratio"], image_size=data["image_size"], quality=None,
                reference_asset_ids=tuple(local_token(p) for p in data["reference_assets"]),
            )
            submission = provider.submit_image(request)
            if submission.status is TaskState.FAILED or not submission.task_id:
                raise PageGenerationError(submission.error_message or "生图来源没有返回结果")
            attempt["task_id"] = submission.task_id
            if submission.status is TaskState.FINISHED and submission.output_urls:
                urls = tuple(submission.output_urls)
            else:
                urls = _wait_for_result(provider, submission.task_id, max_wait=max_wait, poll=poll, sleep=sleep, note=note, label=pid)
            provider.download(urls[0], target)
            if not target.is_file() or target.stat().st_size == 0:
                raise PageGenerationError("下载下来的插画是空文件")
        except (ProviderError, PageGenerationError, OSError, ValueError) as exc:
            attempt["error"] = str(exc)[:500]
            entry.setdefault("attempts", []).append(attempt)
            entry["error"] = attempt["error"]
            failed.append({"page": pid, "error": attempt["error"]})
            atomic_write_json(manifest_path(spec), manifest)
            continue
        entry.setdefault("attempts", []).append(attempt)
        entry.update({"request_hash": digest, "current": target.name, "model": data["model"], "prompt": data["prompt"],
                      "negative": data["negative_prompt"], "updated_at": time.time(), "error": None})
        generated.append(pid)
        atomic_write_json(manifest_path(spec), manifest)
    atomic_write_json(manifest_path(spec), manifest)
    return {"generated": generated, "skipped": skipped, "failed": failed, "manifest": str(manifest_path(spec))}


__all__ = ["MAX_WAIT_SECONDS", "PageGenerationError", "manifest_path", "load_manifest", "request_hash", "page_image_path",
           "page_status", "generate_pages"]
