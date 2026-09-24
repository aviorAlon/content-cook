"""出配乐（契约 docs/music-spec.md §2 / §4 的后台任务 ``music-generate``）。

* 一次跑 ``count`` 条：**逐条**提交 ``provider.submit_music``（每条 ``count=1``），一条失败不拖累其余
* 条数与参考图张数按当前来源的上限查（``spec.source_limits``：核心上限，再按来源登记的能力收紧），不合就在提交前拒绝
* 请求哈希（提示词 / 模型 / 时长 + 参考图内容摘要）不变且已有 current 文件 → 整轮跳过（``force`` 重出）
* 从不覆盖：每条写 ``01_raw/<ID>-<n>.<ext>``，n 一直往后数；``current`` 指向本轮第一条成功的
* 异步来源在任务内轮询，单条最多等 20 分钟；每条的 attempt 追加进项目文件并逐条原子写盘
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from genkit.adapters.base import local_token
from genkit.providers import MusicRequest, ProviderError, TaskState, TaskType
from storyvideo.spec import stable_json_hash

from .prompts import compile_prompt, has_intent
from .spec import AUDIO_SUFFIXES, current_path, raw_dir, reference_paths, save, source_limits

MAX_WAIT_SECONDS = 20 * 60
POLL_SECONDS = 5.0
DEFAULT_SUFFIX = ".mp3"


class MusicGenerationError(RuntimeError):
    pass


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def request_data(spec: Mapping[str, Any]) -> dict[str, Any]:
    """一次配乐请求的内容（哈希 / 提交都用它）。"""

    params = spec.get("music_params") or {}
    return {
        "name": str(spec.get("id") or "music"),
        "prompt": compile_prompt(spec),
        "model": str(params.get("model") or "@music"),
        "duration": int(spec.get("duration") or 60),
        "extra_params": dict(params.get("extra_params") or {}),
        "reference_assets": [str(p) for p in reference_paths(spec)],
    }


def request_hash(data: Mapping[str, Any]) -> str:
    payload = {k: v for k, v in data.items() if k != "reference_assets"}
    payload["reference_digests"] = [_file_digest(Path(p)) for p in data.get("reference_assets") or ()]
    return stable_json_hash(payload)[:24]


def status(spec: Mapping[str, Any]) -> dict[str, Any]:
    """接口层要的整体状态：有没有采用、历次数量、提示词改过没（stale）。"""

    path = current_path(spec)
    digest = request_hash(request_data(spec))
    latest = next((a.get("request_hash") for a in reversed(spec.get("attempts") or []) if not a.get("error")), None)
    return {
        "done": path is not None,
        "current": spec.get("current"),
        "attempts": len(spec.get("attempts") or []),
        "ok_attempts": sum(1 for a in spec.get("attempts") or () if not a.get("error")),
        "stale": bool(path) and bool(latest) and latest != digest,
        "request_hash": digest,
    }


def suffix_for(url: str) -> str:
    """结果地址的扩展名（音乐接口一般给 .wav 或 .mp3）；认不出按 .mp3。"""

    name = Path(urlsplit(str(url)).path).suffix.lower()
    return name if name in AUDIO_SUFFIXES else DEFAULT_SUFFIX


def _next_attempt_path(spec: Mapping[str, Any], used: set[str], suffix: str) -> Path:
    root = raw_dir(spec)
    n = len(spec.get("attempts") or []) + 1
    while (root / f"{spec['id']}-{n}{suffix}").exists() or f"{spec['id']}-{n}{suffix}" in used:
        n += 1
    return root / f"{spec['id']}-{n}{suffix}"


def _wait_for_result(provider: Any, task_id: str, *, max_wait: float, poll: float, sleep: Callable[[float], None],
                     note: Callable[..., None] | None, label: str) -> tuple[str, ...]:
    started = time.monotonic()
    rounds = 0
    while True:
        snapshot = provider.get_task(task_id, TaskType.MUSIC)
        if snapshot.status is TaskState.FINISHED:
            if not snapshot.output_urls:
                raise MusicGenerationError("配乐来源说完成了，但没有给结果地址")
            return tuple(snapshot.output_urls)
        if snapshot.status is TaskState.FAILED:
            raise MusicGenerationError(snapshot.error_message or "配乐来源返回失败")
        rounds += 1
        if note:
            note(current=label, waiting_rounds=rounds)
        if time.monotonic() - started >= max_wait:
            raise MusicGenerationError(f"等了 {int(max_wait // 60)} 分钟还没出配乐（远端任务 {task_id}），先放弃这一条")
        sleep(poll)


def generate_music(spec: dict[str, Any], *, count: int | None = None, force: bool = False, provider: Any = None,
                   note: Callable[..., None] | None = None, max_wait: float = MAX_WAIT_SECONDS,
                   poll: float = POLL_SECONDS, sleep: Callable[[float], None] = time.sleep,
                   write: bool = True, limits: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """后台任务体。就地更新 ``spec`` 并（``write``）逐条写盘；返回 ``{generated, skipped, failed}``。
    ``limits`` 不给就按「模型设置」里配乐来源的上限（``spec.source_limits``）。"""

    data = request_data(spec)
    if not has_intent(spec):
        raise MusicGenerationError("提示词为空——先把 brief 里的用途 / 体裁 / 情绪填上一项")
    bounds = limits if limits is not None else source_limits()
    low, high = bounds["count"]
    wanted = int(count if count is not None else (spec.get("count") or 1))
    if not low <= wanted <= high:
        raise MusicGenerationError(f"一次只能出 {low} 到 {high} 条")
    references = len(data["reference_assets"])
    if references and not bounds["max_references"]:
        raise MusicGenerationError("当前的配乐来源不收参考图——在「高级」里删掉参考图，或换一个收参考图的来源")
    if references > bounds["max_references"]:
        raise MusicGenerationError(f"参考图最多 {bounds['max_references']} 张，现在有 {references} 张")
    digest = request_hash(data)
    if not force and current_path(spec) is not None and any(
        a.get("request_hash") == digest and not a.get("error") for a in spec.get("attempts") or ()
    ):
        return {"generated": [], "skipped": [str(spec.get("current"))], "failed": []}
    if provider is None:
        from genkit.router import build_provider

        provider = build_provider()
    raw_dir(spec).mkdir(parents=True, exist_ok=True)
    generated: list[str] = []
    failed: list[dict[str, Any]] = []
    planned: set[str] = set()
    for number in range(1, wanted + 1):
        label = f"{spec['id']} #{number}"
        if note:
            note(progress=f"{number}/{wanted}", current=label)
        attempt: dict[str, Any] = {"file": "", "created_at": time.time(), "model": data["model"], "request_hash": digest,
                                   "task_id": None, "error": None}
        target: Path | None = None
        try:
            request = MusicRequest(
                name=f"{data['name']}-{number}", prompt=data["prompt"], model=data["model"], duration=data["duration"],
                count=1, reference_asset_ids=tuple(local_token(p) for p in data["reference_assets"]),
                extra_params=dict(data["extra_params"]),
            )
            submission = provider.submit_music(request)
            if submission.status is TaskState.FAILED or not submission.task_id:
                raise MusicGenerationError(submission.error_message or "配乐来源没有返回结果")
            attempt["task_id"] = submission.task_id
            if submission.status is TaskState.FINISHED and submission.output_urls:
                urls = tuple(submission.output_urls)
            else:
                urls = _wait_for_result(provider, submission.task_id, max_wait=max_wait, poll=poll, sleep=sleep,
                                        note=note, label=label)
            target = _next_attempt_path(spec, planned, suffix_for(urls[0]))
            planned.add(target.name)
            attempt["file"] = target.name
            provider.download(urls[0], target)
            if not target.is_file() or target.stat().st_size == 0:
                raise MusicGenerationError("下载下来的音频是空文件")
        except (ProviderError, MusicGenerationError, OSError, ValueError) as exc:
            attempt["error"] = str(exc)[:500]
            if target is not None and (not target.is_file() or target.stat().st_size == 0):
                target.unlink(missing_ok=True)      # 半截文件不留在 01_raw 里冒充产物
                attempt["file"] = ""
            spec.setdefault("attempts", []).append(attempt)
            failed.append({"index": number, "error": attempt["error"]})
            if write:
                save(spec)
            continue
        spec.setdefault("attempts", []).append(attempt)
        if not generated:                            # 本轮第一条成功的自动成为「采用」；其余留给 /pick
            spec["current"] = target.name
        generated.append(target.name)
        if write:
            save(spec)
    return {"generated": generated, "skipped": [], "failed": failed}


__all__ = ["MAX_WAIT_SECONDS", "POLL_SECONDS", "MusicGenerationError", "request_data", "request_hash", "status",
           "suffix_for", "generate_music"]
