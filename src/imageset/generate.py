"""出图任务（契约 §2 ``generate.py``）。

* 按 items 逐张请求 ``provider.submit_image``（``build_provider()``）；同步来源直接落盘，异步来源在任务内轮询，单张最多等 20 分钟
* 请求哈希（提示词 / 负面 / 模型 / 画幅 / 尺寸 + 参考图内容摘要）不变且已有 current 文件 → 跳过（``force`` 重出）
* 从不覆盖：每次写 ``01_raw/<item>-<n>.png``，``current`` 指向最新一次
* 一张失败不拖累整批；每张的 attempt 追加进项目文件的 ``items[].attempts``，项目文件逐张原子写盘（任务中途挂了也不丢）
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from genkit.adapters.base import local_token
from genkit.providers import ImageRequest, ProviderError, TaskState, TaskType
from storyvideo.spec import stable_json_hash

from .prompts import reference_assets
from .spec import current_path, item_ids, raw_dir, save

MAX_WAIT_SECONDS = 20 * 60
POLL_SECONDS = 5.0


class GenerationError(RuntimeError):
    pass


def _file_digest(path: str) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def item_model(spec: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    """这一张用哪个模型：``item.meta.model`` 非空就用它（多模型对比），否则项目的 ``image_params.model``。"""

    meta = item.get("meta") or {}
    override = str(meta.get("model") or "").strip() if isinstance(meta, Mapping) else ""
    return override or str((spec.get("image_params") or {}).get("model") or "@image")


def request_data(spec: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    """一张图的生图请求（哈希 / 提交都用它）。提示词取 items[].prompt（展开后可手改）。"""

    params = spec.get("image_params") or {}
    return {
        "name": f"{spec.get('id')}-{item.get('id')}",
        "prompt": str(item.get("prompt") or ""),
        "negative_prompt": str(item.get("negative") or "") or None,
        "model": item_model(spec, item),
        "aspect_ratio": str(item.get("aspect_ratio") or "1:1"),
        "image_size": str(params.get("image_size") or "2K"),
        "reference_assets": list(reference_assets(spec)),
    }


def request_hash(data: Mapping[str, Any]) -> str:
    payload = {k: v for k, v in data.items() if k != "reference_assets"}
    payload["reference_digests"] = [_file_digest(p) for p in data.get("reference_assets") or ()]
    return stable_json_hash(payload)[:24]


def item_status(spec: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    """接口层要的每张状态：done / stale（提示词改过、图还是旧请求的）/ attempts / error。"""

    path = current_path(spec, item)
    prompt = str(item.get("prompt") or "").strip()
    digest = request_hash(request_data(spec, item)) if prompt else None
    return {
        "done": path is not None, "file": path.name if path else None, "attempts": len(item.get("attempts") or []),
        "stale": bool(path) and bool(item.get("request_hash")) and item.get("request_hash") != digest,
        "error": item.get("error"), "status": "done" if path else ("failed" if item.get("error") else "todo"),
    }


def _next_attempt_path(spec: Mapping[str, Any], item: Mapping[str, Any]) -> Path:
    root = raw_dir(spec)
    n = len(item.get("attempts") or []) + 1
    while (root / f"{item['id']}-{n}.png").exists():
        n += 1
    return root / f"{item['id']}-{n}.png"


def _wait_for_result(provider: Any, task_id: str, *, max_wait: float, poll: float, sleep: Callable[[float], None],
                     note: Callable[..., None] | None, label: str) -> tuple[str, ...]:
    started = time.monotonic()
    rounds = 0
    while True:
        snapshot = provider.get_task(task_id, TaskType.IMAGE)
        if snapshot.status is TaskState.FINISHED:
            if not snapshot.output_urls:
                raise GenerationError("生图来源说完成了，但没有给结果地址")
            return tuple(snapshot.output_urls)
        if snapshot.status is TaskState.FAILED:
            raise GenerationError(snapshot.error_message or "生图来源返回失败")
        rounds += 1
        if note:
            note(current=label, waiting_rounds=rounds)
        if time.monotonic() - started >= max_wait:
            raise GenerationError(f"等了 {int(max_wait // 60)} 分钟还没出图（远端任务 {task_id}），先放弃这一张")
        sleep(poll)


def generate_set(spec: dict[str, Any], *, only: list[str] | None = None, force: bool = False, provider: Any = None,
                 note: Callable[..., None] | None = None, max_wait: float = MAX_WAIT_SECONDS, poll: float = POLL_SECONDS,
                 sleep: Callable[[float], None] = time.sleep, write: bool = True) -> dict[str, Any]:
    """后台任务体。就地更新 ``spec['items']`` 并（``write``）逐张写盘；返回 ``{generated, skipped, failed}``。"""

    wanted = set(only) if only else None
    ids = item_ids(spec)
    unknown = sorted(wanted - set(ids)) if wanted else []
    if unknown:
        raise GenerationError(f"没有这些图：{', '.join(unknown)}")
    if not ids:
        raise GenerationError("还没有展开清单，没有可出的图")
    if provider is None:
        from genkit.router import build_provider

        provider = build_provider()
    raw_dir(spec).mkdir(parents=True, exist_ok=True)
    targets = [i for i in spec["items"] if not wanted or i["id"] in wanted]
    generated: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, Any]] = []
    for number, item in enumerate(targets, 1):
        if not str(item.get("prompt") or "").strip():
            failed.append({"item": item["id"], "error": "提示词为空"})
            continue
        data = request_data(spec, item)
        digest = request_hash(data)
        if not force and item.get("request_hash") == digest and current_path(spec, item) is not None:
            skipped.append(item["id"])
            continue
        if note:
            note(progress=f"{number}/{len(targets)}", current=item["id"])
        target = _next_attempt_path(spec, item)
        attempt: dict[str, Any] = {"file": target.name, "created_at": time.time(), "model": data["model"], "request_hash": digest,
                                   "task_id": None, "error": None}
        try:
            request = ImageRequest(
                name=data["name"], prompt=data["prompt"], negative_prompt=data["negative_prompt"], model=data["model"],
                aspect_ratio=data["aspect_ratio"], image_size=data["image_size"], quality=None,
                reference_asset_ids=tuple(local_token(p) for p in data["reference_assets"]),
            )
            submission = provider.submit_image(request)
            if submission.status is TaskState.FAILED or not submission.task_id:
                raise GenerationError(submission.error_message or "生图来源没有返回结果")
            attempt["task_id"] = submission.task_id
            if submission.status is TaskState.FINISHED and submission.output_urls:
                urls = tuple(submission.output_urls)
            else:
                urls = _wait_for_result(provider, submission.task_id, max_wait=max_wait, poll=poll, sleep=sleep, note=note, label=item["id"])
            provider.download(urls[0], target)
            if not target.is_file() or target.stat().st_size == 0:
                raise GenerationError("下载下来的图是空文件")
        except (ProviderError, GenerationError, OSError, ValueError) as exc:
            attempt["error"] = str(exc)[:500]
            item.setdefault("attempts", []).append(attempt)
            item["error"] = attempt["error"]
            item["status"] = "failed" if current_path(spec, item) is None else item.get("status", "todo")
            failed.append({"item": item["id"], "error": attempt["error"]})
            if write:
                save(spec)
            continue
        item.setdefault("attempts", []).append(attempt)
        item.update({"current": target.name, "request_hash": digest, "status": "done", "error": None})
        generated.append(item["id"])
        if write:
            save(spec)
    return {"generated": generated, "skipped": skipped, "failed": failed}


__all__ = ["MAX_WAIT_SECONDS", "POLL_SECONDS", "GenerationError", "item_model", "request_data", "request_hash", "item_status",
           "generate_set"]
