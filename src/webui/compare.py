"""首帧对比试跑：同一镜用几个不同的模型各出一张候选，并排看；满意的可以「采用」为这一镜的首帧。

候选走的是跟正式生成完全相同的提示词与参考图（frame_request_data + 参考镜首帧 + 角色锚图），只把
model 换掉；结果放在 ``output/<id>/09_compare/<shot>/``，不进账本——它们是"看看再说"的样片。

「采用」必须回到账本里：记一次重拍（job 回到 prepared）→ 把候选拷成 01_frames/<shot>.png →
import_legacy 认领（prepared 的 job 有文件就标 succeeded，全部首帧齐时重记技术检查，未变的裁决按哈希保留）。
这样账本、QC、后续视频生成看到的都是同一张图，没有绕过任何闸。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from genkit.adapters.base import local_token
from genkit.providers import ImageRequest, ProviderError, TaskState, TaskType
from genkit.router import build_provider
from storyvideo.prompts import compile_frame_prompt, compile_negative_prompt, frame_reference_asset_ids, frame_request_data
from storyvideo.runtime import StoryVideoRunner
from storyvideo.state import atomic_write_json, read_json

from . import deps, tasks

# 异步来源（提交后只拿到远端任务 id 的来源）在后台任务里逐个模型轮询，跟绘本 / 图片集 / 海报同一口径：一张候选最多等 20 分钟。
# 测试把这两个常量调小（run_candidates 不把它们写死在默认参数里，就是为了能 monkeypatch）。
MAX_WAIT_SECONDS = 20 * 60
POLL_SECONDS = 5.0


class CompareError(ValueError):
    pass


def _note(**fields: Any) -> None:
    """往自己这条后台任务记录里写一行进度（不在任务线程里时什么也不做）。"""

    task_id = tasks.current_task_id()
    if task_id:
        tasks.note(task_id, **fields)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:60] or "model"


def compare_dir(spec: Mapping[str, Any], shot_id: str) -> Path:
    return Path(str(spec["output_root"])) / "09_compare" / shot_id


def manifest_path(spec: Mapping[str, Any]) -> Path:
    return Path(str(spec["output_root"])) / "manifests" / "compare.json"


def load_manifest(spec: Mapping[str, Any]) -> dict[str, Any]:
    return read_json(manifest_path(spec), missing={}) or {}


def build_request(spec: Mapping[str, Any], shot_id: str, model: str) -> ImageRequest:
    """跟 runtime 的首帧请求一致：提示词、负向词、画幅、尺寸、参考图（参考镜首帧 + 角色锚图）。"""

    data = frame_request_data(spec, shot_id)
    shot = next(s for s in spec["shots"] if str(s["id"]) == shot_id)
    dependency = None
    ref = shot.get("ref_frame")
    if ref:
        ref_path = Path(str(spec["output_root"])) / "01_frames" / f"{ref}.png"
        if ref_path.is_file():
            dependency = local_token(ref_path)
    refs = frame_reference_asset_ids(spec, shot_id, dependency_asset_id=dependency)
    return ImageRequest(
        name=f"{spec.get('id')}-{shot_id}-compare",
        prompt=compile_frame_prompt(spec, shot_id),
        negative_prompt=compile_negative_prompt(spec, shot_id) or None,
        model=model,
        aspect_ratio=data["aspect_ratio"],
        image_size=data["image_size"],
        quality=None if str(model).lower().startswith(("doubao-seedream", "@")) or "gpt-image" not in model else data["quality"],
        reference_asset_ids=tuple(refs),
    )


def _wait_for_result(provider: Any, task_id: str, *, max_wait: float, poll: float,
                     sleep: Callable[[float], None], note: Callable[..., None] | None, label: str) -> tuple[str, ...]:
    """异步来源：轮询到完成 / 失败 / 超时。每轮写一行进度，界面轮询任务时能看到「还在等哪个模型」。"""

    started = time.monotonic()
    rounds = 0
    while True:
        snapshot = provider.get_task(task_id, TaskType.IMAGE)
        if snapshot.status is TaskState.FINISHED:
            if not snapshot.output_urls:
                raise CompareError("生图来源说完成了，但没有给结果地址")
            return tuple(snapshot.output_urls)
        if snapshot.status is TaskState.FAILED:
            raise CompareError(snapshot.error_message or "生图来源返回失败")
        rounds += 1
        if note:
            note(current=label, waiting_rounds=rounds)
        if time.monotonic() - started >= max_wait:
            raise CompareError(f"等了 {int(max_wait // 60)} 分钟还没出图（远端任务 {task_id}），先放弃这一张")
        sleep(poll)


def run_candidates(episode_id: str, shot_id: str, models: list[str], *, provider: Any = None,
                   max_wait: float | None = None, poll: float | None = None,
                   sleep: Callable[[float], None] = time.sleep,
                   note: Callable[..., None] | None = _note) -> dict[str, Any]:
    """逐个模型出一张候选。

    同步型生图（网关返回 base64 / ``file:`` 任务）直接落盘；异步型（返回远端任务 id 的来源）在这个任务里
    逐个模型独立轮询，单张最多等 ``MAX_WAIT_SECONDS``（20 分钟）。**一个模型失败不拖累其他模型**：原因记在
    它自己那一行（``error``），别的候选照常出、照常能用；每跑完一个就把清单写盘（任务中途挂了也不丢）。"""

    from storyvideo.spec import load_spec

    spec = load_spec(episode_id, project_root=deps.PROJECT_ROOT)
    if not any(str(s["id"]) == shot_id for s in spec["shots"]):
        raise CompareError(f"没有这一镜：{shot_id}")
    provider = provider or build_provider()
    out_dir = compare_dir(spec, shot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(spec)
    rows: list[dict[str, Any]] = list(manifest.get(shot_id) or [])
    results: list[dict[str, Any]] = []
    wanted = [m.strip() for m in models if m.strip()]
    for number, model in enumerate(wanted, 1):
        stamp = int(time.time() * 1000)
        row: dict[str, Any] = {"model": model, "created_at": time.time(), "file": None, "task_id": None, "error": None}
        target = out_dir / f"{_slug(model)}-{stamp}.png"
        if note:
            note(progress=f"{number}/{len(wanted)}", current=model)
        try:
            request = build_request(spec, shot_id, model)
            submission = provider.submit_image(request)
            if submission.status is TaskState.FAILED or not submission.task_id:
                raise CompareError(submission.error_message or "生图来源没有返回结果")
            row["task_id"] = submission.task_id
            if submission.status is TaskState.FINISHED and submission.output_urls:
                urls = tuple(submission.output_urls)
            else:
                urls = _wait_for_result(provider, submission.task_id, sleep=sleep, note=note, label=model,
                                        max_wait=MAX_WAIT_SECONDS if max_wait is None else max_wait,
                                        poll=POLL_SECONDS if poll is None else poll)
            provider.download(urls[0], target)
            if not target.is_file() or target.stat().st_size == 0:
                raise CompareError("下载下来的图是空文件")
            row["file"] = target.name
        except (ProviderError, CompareError, OSError, ValueError) as exc:
            target.unlink(missing_ok=True)        # 半张候选不留在 09_compare 里
            row["error"] = str(exc)[:500]
        rows.append(row)
        results.append(row)
        manifest[shot_id] = rows
        atomic_write_json(manifest_path(spec), manifest)
    manifest[shot_id] = rows
    atomic_write_json(manifest_path(spec), manifest)
    if wanted and all(r["error"] for r in results):
        # 一张候选都没出：任务要记 failed 带原因（每一行的原因照样在清单里）——否则任务中心显示绿色「成功」，
        # 用户只有点进候选才知道一张都没出（2026-09-22 审计 DRAMA-15）
        first = str(results[0]["error"])
        raise CompareError(first if len(results) == 1 else f"{len(results)} 个候选全部失败：{first}")
    return {"shot_id": shot_id, "candidates": results}


def list_candidates(spec: Mapping[str, Any], shot_id: str) -> list[dict[str, Any]]:
    rows = load_manifest(spec).get(shot_id) or []
    out = []
    for row in rows:
        item = dict(row)
        item["exists"] = bool(row.get("file")) and (compare_dir(spec, shot_id) / str(row.get("file"))).is_file()
        out.append(item)
    return out


def adopt(episode_id: str, shot_id: str, file_name: str, *, reason: str = "") -> dict[str, Any]:
    """把候选采用为这一镜的首帧（见模块说明）。要求项目已开始制作且这一镜已有首帧记录。"""

    from storyvideo.spec import load_spec

    spec = load_spec(episode_id, project_root=deps.PROJECT_ROOT)
    candidate = compare_dir(spec, shot_id) / Path(file_name).name
    if not candidate.is_file():
        raise CompareError(f"候选文件不存在：{file_name}")
    if not deps.is_locked(str(spec["output_root"])):
        raise CompareError("项目还没开始制作，没有首帧账本可采用——先「开始制作」")
    frame = Path(str(spec["output_root"])) / "01_frames" / f"{shot_id}.png"
    with StoryVideoRunner(str(deps.PROJECT_ROOT)) as runner:
        if frame.is_file():
            runner.review(episode_id, "frames", "retake", shots=[shot_id],
                          reason=reason or f"采用对比候选 {candidate.name}", reviewer="compare")
            backup = compare_dir(spec, shot_id) / f"replaced-{int(time.time())}.png"
            shutil.copy2(frame, backup)
        frame.parent.mkdir(parents=True, exist_ok=True)
        # 先拷成同目录的临时文件再原子替换，不原地覆盖写：上面的重拍把旧首帧用硬链接留底到 01_frames/takes/，
        # 原地写会把留底也改成候选图，被换掉的那一版就只剩 09_compare 里的 replaced-*.png 了
        staged = frame.with_name(f".{frame.stem}.adopt{frame.suffix}")
        shutil.copy2(candidate, staged)
        os.replace(staged, frame)
        result = runner.import_legacy(episode_id, confirmed_existing_artifacts=True)
    return {"shot_id": shot_id, "adopted": candidate.name, "status": result.get("status")}


__all__ = ["MAX_WAIT_SECONDS", "POLL_SECONDS", "CompareError", "build_request", "run_candidates", "list_candidates",
           "adopt", "compare_dir"]
