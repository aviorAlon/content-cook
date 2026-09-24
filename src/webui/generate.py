"""付费生成的「预估 → 确认 → 提交」网关。

真正的提交/轮询/下载/去重全部走 ``StoryVideoRunner.run()``——那条状态机
（防重复提交、ambiguous-submission 对账、QC 闸门）已经被 CLI 用了很久，
这里不重新实现一遍，只是给它包一层「点按钮之前先看清楚要花什么钱」的确认门。

预估函数**不追求跟 runner 内部状态机完全一致**——它只看磁盘上有没有产物文件，
足够诚实地告诉人类"大概会新提交几个"。真正的安全保证仍然来自 run() 自己的逻辑：
就算预估算少了或算多了，run() 也不会因此多花一分钱或漏掉去重。
"""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Mapping

from storyvideo.prompts import frame_request_data, video_request_data
from storyvideo.runtime import ledger_path
from storyvideo.spec import stable_json_hash

from . import deps, tasks


def _shot_ids(spec: Mapping[str, Any], only: list[str] | None) -> list[str]:
    all_ids = [s["id"] for s in spec.get("shots", ()) if isinstance(s, dict)]
    if only is None:
        return all_ids
    unknown = [i for i in only if i not in all_ids]
    if unknown:
        raise ValueError(f"unknown shot id(s): {unknown}")
    return [i for i in all_ids if i in only]


_REGEN_STATES = {"prepared", "failed"}
_IN_FLIGHT_STATES = {"submitting", "submitted", "processing", "waiting_remote", "polled"}


def _job_record(spec: Mapping[str, Any], through: str, shot_id: str) -> dict[str, Any] | None:
    """账本里这一步的记录；账本不存在或没这条记录时返回 None（那时按文件判断就够了）。只读打开，不碰生产锁。
    账本位置按 runtime 的唯一规则（``ledger_path(spec["project_root"])``），跟 runner 开的是同一本。"""

    from genkit.store import RunStore
    from storyvideo.state import make_step_id

    output_root = Path(spec["output_root"])
    store_path = ledger_path(spec["project_root"])
    if not store_path.is_file():
        return None
    run_id = str(spec.get("id") or output_root.name)
    step_id = make_step_id("frames" if through == "frames" else "videos", shot_id)
    try:
        with RunStore(store_path, readonly=True) as store:
            job = store.get_job(run_id, step_id)
    except Exception:
        return None
    return dict(job) if job else None


def _job_status(spec: Mapping[str, Any], through: str, shot_id: str) -> str | None:
    job = _job_record(spec, through, shot_id)
    return str(job.get("status") or "") if job else None


def job_records(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """账本里这个项目的全部任务，按 step_id 索引；一次只读打开（详情页每镜都要看，别逐镜开库）。"""

    from genkit.store import RunStore

    output_root = Path(spec["output_root"])
    store_path = ledger_path(spec["project_root"])
    if not store_path.is_file():
        return {}
    run_id = str(spec.get("id") or output_root.name)
    try:
        with RunStore(store_path, readonly=True) as store:
            if store.get_run(run_id) is None:
                return {}
            snapshot = store.snapshot(run_id)
    except Exception:
        return {}
    return {str(job["step_id"]): dict(job) for job in snapshot.get("jobs") or () if job}


_NO_ATTEMPTS = {"total": 0, "same_request": 0, "request_hash": None}


def shot_attempts(
    spec: Mapping[str, Any], shot_id: str, *, jobs: Mapping[str, Mapping[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    """这一镜首帧/视频各提交过几次（DESIGN §24）。

    跟 ``RunStore.attempts_for`` 同一套算法：一个 run 里同一步的 request_hash 钉死，所以
    ``same_request == attempt``；这里再拿当前 spec 编译出的指纹核对一次，对不上就算 0。
    ``jobs`` 传 ``job_records(spec)`` 的结果可避免逐镜开库。
    """

    from storyvideo.state import input_fingerprint, make_step_id

    if jobs is None:
        jobs = job_records(spec)
    out: dict[str, dict[str, Any]] = {}
    for stage, kind, data in (
        ("frames", "frame", frame_request_data),
        ("videos", "video", video_request_data),
    ):
        job = jobs.get(make_step_id(stage, shot_id))
        if not job:
            out[stage] = dict(_NO_ATTEMPTS)
            continue
        total = int(job.get("attempt") or 0)
        try:
            expected = input_fingerprint(kind, data(spec, shot_id))
        except Exception:  # noqa: BLE001 - 编译失败的镜不该让详情页整页报错
            expected = None
        recorded = job.get("request_hash")
        same = total if expected is None or expected == recorded else 0
        out[stage] = {"total": total, "same_request": same, "request_hash": recorded}
    return out


def failed_jobs(spec: Mapping[str, Any], only: list[str] | None = None) -> list[dict[str, Any]]:
    """账本里明确失败的首帧/视频任务，带分类与建议（界面「失败任务」卡与重提接口都用它）。"""

    from .humanize import classify_failure

    out: list[dict[str, Any]] = []
    for shot_id in _shot_ids(spec, only):
        for stage in ("frames", "videos"):
            job = _job_record(spec, stage, shot_id)
            if not job or str(job.get("status") or "") != "failed":
                continue
            error = str(job.get("error") or "")
            info = classify_failure(error)
            out.append({
                "stage": stage, "shot_id": shot_id, "step_id": str(job.get("step_id") or f"{stage}:{shot_id}"),
                "error": error, "ambiguous": bool(job.get("ambiguous_submission")),
                "kind": info["kind"], "label": info["label"], "advice": info["advice"],
                "retryable": info["retryable"] and not job.get("ambiguous_submission"),
                # 同一份请求已经提交过几次（DESIGN §24）：≥2 时界面标「第 N 次」，提醒别再原样重提
                "attempts": int(job.get("attempt") or 0),
            })
    return out


def _tail_gated(spec: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """尾帧续接里源镜视频还没过检的镜（只读 qc.json；没有就全都在等）。"""

    from storyvideo.qc import load_qc
    from storyvideo.tail import tail_gated

    qc_path = Path(spec["output_root"]) / "manifests" / "qc.json"
    try:
        qc = load_qc(qc_path) if qc_path.is_file() else None
    except Exception:  # noqa: BLE001
        qc = None
    return tail_gated(spec, qc)


def tail_pending(spec: Mapping[str, Any]) -> list[dict[str, str]]:
    """界面用：``[{shot_id, source, mode, reason}]``。"""

    return [{"shot_id": shot_id, **info} for shot_id, info in _tail_gated(spec).items()]


def _job_needs_regeneration(spec: Mapping[str, Any], through: str, shot_id: str) -> bool:
    """重摇后的 prepared / 明确失败的 failed → 要重新提交，即使旧文件还在。"""

    return (_job_status(spec, through, shot_id) or "") in _REGEN_STATES


def estimate_plan(
    spec: Mapping[str, Any], *, through: str, only: list[str] | None
) -> dict[str, Any]:
    """只读：这一批「大概」要新提交几个 frame/video，用什么模型。

    ``through in ("assemble", "final")`` 是最终成片装配——这一步是纯本地
    ffmpeg 拼接，不调用任何付费接口，所以这里的「预估」只是「有没有已经拼好的
    成片」，不是「要花多少钱」。真正的前置条件（所有镜次的 frame/video QC
    是否都已通过）由 ``StoryVideoRunner.run()`` 自己在真正装配时把关
    （没通过会抛 ``StoryVideoGatePending``），这里不重复判断。
    """

    if through in ("assemble", "final"):
        output_root = Path(spec["output_root"])
        final_path = output_root / f"{spec.get('id', 'story')}_final.mp4"
        already_have = final_path.is_file()
        plan_token = stable_json_hash(
            {"episode": spec.get("id"), "through": through, "already_have": already_have}
        )
        return {
            "through": through,
            "items": [{"shot_id": "final", "already_have": already_have, "model": None, "duration": None}],
            # 成片装配本来就是本机 ffmpeg，不调用任何付费接口
            "local": True,
            "cost_note": None,
            "pending_count": 0 if already_have else 1,
            "already_have_count": 1 if already_have else 0,
            "missing_upstream_frames": [],
            "plan_token": plan_token,
        }

    if through not in ("frames", "videos"):
        raise ValueError("through must be 'frames', 'videos', 'assemble', or 'final' for an estimate")

    output_root = Path(spec["output_root"])
    ids = _shot_ids(spec, only)
    items: list[dict[str, Any]] = []
    pending: list[str] = []
    # 尾帧续接（DESIGN §30）：源镜视频还没过检的镜这一轮不提交，单独标出来并给原因（「等 S1 视频过检后抽尾帧」）
    gated = _tail_gated(spec)

    for shot_id in ids:
        if shot_id in gated:
            items.append({
                "shot_id": shot_id, "already_have": False, "in_progress": False, "failed": False,
                "model": None, "duration": None,
                "waiting_tail": dict(gated[shot_id]),
            })
            continue
        if through == "frames":
            artifact = output_root / "01_frames" / f"{shot_id}.png"
            req = frame_request_data(spec, shot_id)
        else:
            artifact = output_root / "02_video" / f"{shot_id}.mp4"
            req = video_request_data(spec, shot_id)
        already_have = artifact.is_file()
        # 有文件 ≠ 不用生成：人工点了「重摇」之后，账本里这一步会被重置回 prepared（旧文件先留着当参照），
        # runner.run() 会重新提交它。预估只看文件会把重摇的镜算成「已有，跳过」，UI 就显示「没有新东西要生成」，
        # 看起来像按钮没反应。以账本状态为准。
        job_status = _job_status(spec, through, shot_id)
        if already_have and (job_status or "") in _REGEN_STATES:
            already_have = False
        # 正在生成中（另一个进程/上一次点击发起的）：既不是「已有」也不该再提交一次，
        # 单独标出来让 UI 说清楚——否则重出进行中再点一次，看到「已经有产物」会以为是 bug。
        in_progress = (job_status or "") in _IN_FLIGHT_STATES
        if not already_have and not in_progress:
            pending.append(shot_id)
        items.append(
            {
                "shot_id": shot_id,
                "already_have": already_have,
                "in_progress": in_progress,
                "failed": (job_status or "") == "failed",
                "model": req.get("model"),
                "duration": req.get("duration") if through == "videos" else None,
            }
        )

    if through == "videos":
        missing_frames = [
            i["shot_id"]
            for i in items
            if not (output_root / "01_frames" / f"{i['shot_id']}.png").is_file()
        ]
    else:
        missing_frames = []

    plan_token = stable_json_hash(
        {
            "episode": spec.get("id"),
            "through": through,
            "pending": sorted(pending),
            "models": sorted({i["model"] for i in items if i["model"]}),
        }
    )
    in_progress_ids = [i["shot_id"] for i in items if i.get("in_progress")]
    tail_pending = [{"shot_id": i["shot_id"], **i["waiting_tail"]} for i in items if i.get("waiting_tail")]
    # 本地来源（ComfyUI / SD WebUI / 本机兼容接口）不花钱：确认框不该报一个假的美元数（DESIGN §45）
    from genkit.local import free_cost_note, role_local

    local = role_local("image" if through == "frames" else "video")
    return {
        "through": through,
        "items": items,
        "local": local,
        "cost_note": free_cost_note(len(pending)) if local else None,
        "pending_count": len(pending),
        "already_have_count": len(items) - len(pending) - len(in_progress_ids) - len(tail_pending),
        "in_progress": in_progress_ids,
        # 尾帧续接：这一轮推迟的镜（不算待生成，也不算已有；下一轮源镜过检后自动接上）
        "tail_pending": tail_pending,
        # 明确失败的镜：runner.run() 不会自动重提它们，界面要停下轮询、显示原因、给「重提」入口
        "failed": [i["shot_id"] for i in items if i.get("failed")],
        "missing_upstream_frames": missing_frames,
        "plan_token": plan_token,
    }


# ---------------------------------------------------------------------------- 后台轮询（DESIGN §34）
#
# 以前的问题：点「生成视频」→ run(wait=False) 把几条视频提交给异步来源就返回；服务端之后没有任何东西再去问
# 服务商，前端的轮询又在「pending 为 0」时就以为完事——几个小时后服务商早做完了，界面还说「正在生成中」。
# 这里起一个 generate-watch 后台任务，每隔 SLEEP_SECONDS 跑一次 runner.poll()（只问进度、下载、记账，
# 绝不提交新任务）；生成端点提交后自动起它，服务重启时扫账本把在飞的补起，界面也可以显式 /generate-resume。

WATCH_KIND = "generate-watch"
SLEEP_SECONDS = 15.0          # 两轮之间的间隔（测试改成 0）
MAX_WAIT_SECONDS = 60 * 60    # 最多等这么久；超过就收工并记下还没回来的任务
REPEAT_ERROR_LIMIT = 3        # 同一条错误连着出现这么多次就停（否则一直转）
_LEDGER_INFLIGHT_STATES = ("submitted", "processing", "finished")


def _provider():
    """生成端点用的同一个路由适配器；测试把它换成假服务商。"""

    from genkit.router import build_provider

    return build_provider()


def inflight_step_ids(spec: Mapping[str, Any]) -> list[str]:
    """账本里还在服务商那边的首帧/视频任务（submitted / processing / finished 三态）。只读。"""

    return [
        step_id
        for step_id, job in job_records(spec).items()
        if str(job.get("status") or "") in _LEDGER_INFLIGHT_STATES
        and (step_id.startswith("frame-") or step_id.startswith("video-"))
    ]


def inflight_shot_ids(spec: Mapping[str, Any]) -> list[str]:
    """同上，按镜号（spec 顺序去重）——概览页和 generate-resume 给人看的。"""

    steps = set(inflight_step_ids(spec))
    return [
        s["id"] for s in spec.get("shots", ()) if isinstance(s, dict)
        and (f"frame-{s['id']}" in steps or f"video-{s['id']}" in steps)
    ]


def is_watching(episode_id: str) -> bool:
    """这个项目的后台轮询任务正在跑吗（pending / running）。"""

    return tasks.active_task_id(WATCH_KIND, episode_id) is not None


def give_up_message(minutes: int, remaining: list[str]) -> str:
    return f"等了 {minutes} 分钟服务商还没返回：{'、'.join(remaining)}——下次打开项目或点「生成」会接着等"


def watch_loop(episode_id: str, project_root: Path) -> dict[str, Any]:
    """后台任务体：poll → 没有在飞的就收工；否则睡一会儿再来。

    一轮里某条任务轮询/下载失败（``StoryVideoError``）不能悄悄吞掉，也不能让别的任务跟着停：
    人话原因写进任务记录（``error`` / ``errors``），继续；同一条错误连着 REPEAT_ERROR_LIMIT 次才停下。
    """

    from storyvideo.runtime import StoryVideoError, StoryVideoPollErrors, StoryVideoRunner
    from storyvideo.spec import load_spec

    from .humanize import humanize

    task_id = tasks.current_task_id()
    started = time.monotonic()
    rounds = 0
    last_error: str | None = None
    repeats = 0
    errors: list[str] = []
    while True:
        rounds += 1
        inflight: list[str]
        try:
            with StoryVideoRunner(str(project_root), provider=_provider()) as runner:
                result = runner.poll(episode_id)
            inflight = list(result.get("inflight") or [])
            last_error, repeats = None, 0
        except deps.RUNNER_ERRORS as exc:
            parts = [humanize(e) for e in exc.errors] if isinstance(exc, StoryVideoPollErrors) else [humanize(exc)]
            message = "；".join(parts)
            repeats = repeats + 1 if message == last_error else 1
            last_error = message
            errors.append(message)
            if task_id:
                tasks.note(task_id, error=message, errors=errors[-20:], error_repeats=repeats, rounds=rounds)
            if repeats >= REPEAT_ERROR_LIMIT:
                raise StoryVideoError(message) from exc
            try:
                inflight = inflight_step_ids(load_spec(episode_id, project_root=project_root))
            except Exception:  # noqa: BLE001 - 读不出账本就按「还在飞」处理，下一轮再看
                inflight = ["?"]
        if task_id:
            tasks.note(task_id, inflight=inflight, rounds=rounds)
        if not inflight:
            return {"inflight": [], "rounds": rounds, "errors": errors}
        if time.monotonic() - started >= MAX_WAIT_SECONDS:
            minutes = int(MAX_WAIT_SECONDS // 60)
            return {"inflight": inflight, "rounds": rounds, "errors": errors, "gave_up": True,
                    "message": give_up_message(minutes, inflight)}
        time.sleep(SLEEP_SECONDS)


def ensure_watcher(episode_id: str, *, project_root: Path | None = None) -> dict[str, Any]:
    """有在飞的任务就保证有一个后台轮询任务在跑：``{task_id, started, inflight}``。

    已经有一个在跑（``TaskBusy``）→ 返回那个的 task_id、``started: False``；
    没有在飞的任务 → ``task_id: None``、``inflight: []``，什么都不起。从不花钱。
    """

    from storyvideo.spec import load_spec

    root = Path(project_root or deps.PROJECT_ROOT)
    spec = load_spec(episode_id, project_root=root)
    inflight = inflight_step_ids(spec)
    if not inflight:
        return {"task_id": tasks.active_task_id(WATCH_KIND, episode_id), "started": False, "inflight": []}
    try:
        task_id = tasks.start(WATCH_KIND, episode_id, lambda: watch_loop(episode_id, root), params={"inflight": inflight})
        started = True
    except tasks.TaskBusy:
        task_id = tasks.active_task_id(WATCH_KIND, episode_id)
        started = False
    return {"task_id": task_id, "started": started, "inflight": inflight}


def resume_inflight_watchers(project_root: Path | None = None) -> list[dict[str, Any]]:
    """服务启动时扫一遍还在服务商那边的任务，给它们起后台轮询：短剧看账本，单条视频看项目文件。

    两边都只轮询 + 下载，从不提交新任务，所以起服务这一下不会花钱。
    """

    root = Path(project_root or deps.PROJECT_ROOT)
    out = _resume_storyvideo_watchers(root)
    out.extend(_resume_clip_watchers(root))
    return out


def _resume_clip_watchers(root: Path) -> list[dict[str, Any]]:
    """单条视频模块（DESIGN §43）：``content/clip/`` 里最后一次尝试还没落盘的项目接着等。"""

    try:
        from .routers.clip import resume_clip_watchers

        return list(resume_clip_watchers(root))
    except Exception:  # noqa: BLE001 - 续等失败不能拦住服务启动
        return []


def _resume_storyvideo_watchers(root: Path) -> list[dict[str, Any]]:
    """短剧：扫账本，哪个项目还有在飞的任务就给它起后台轮询。没有账本文件 = 什么都不做。

    扫之前先可写打开一次：老账本（schema 1）只读打开不迁移，而详情页 / 失败任务 / 在飞任务 / 导出溯源全是只读打开、
    出错一律当「没有记录」，已锁定的项目在用户点审片或生成之前没有任何一处会可写打开——升级后老项目的状态就全错
    且没有提示。服务启动是单进程、最便宜的一刻，可写打开只升表结构，不占生产锁、不提交任何任务。
    """

    from genkit.store import RunStore

    store_path = ledger_path(root)
    if not store_path.is_file():
        return []
    try:
        RunStore(store_path).close()
    except Exception:  # noqa: BLE001 - 账本坏了 / 来自未来的版本：下面只读打开会再遇到一次，这里不拦
        pass
    try:
        with RunStore(store_path, readonly=True) as store:
            candidates = []
            for run_id in store.list_run_ids():
                jobs = store.snapshot(run_id).get("jobs") or ()
                if any(str(job.get("status") or "") in _LEDGER_INFLIGHT_STATES for job in jobs):
                    candidates.append(run_id)
    except Exception:  # noqa: BLE001 - 账本打不开不该拦住服务启动
        return []
    out: list[dict[str, Any]] = []
    for episode_id in candidates:
        try:
            info = ensure_watcher(episode_id, project_root=root)
        except Exception:  # noqa: BLE001 - spec 已删/坏掉的项目跳过
            continue
        if info.get("task_id"):
            out.append({"episode_id": episode_id, **info})
    return out


__all__ = [
    "estimate_plan", "failed_jobs", "tail_pending", "job_records",
    "ensure_watcher", "inflight_shot_ids", "inflight_step_ids", "is_watching", "resume_inflight_watchers", "watch_loop",
    "SLEEP_SECONDS", "MAX_WAIT_SECONDS", "REPEAT_ERROR_LIMIT", "WATCH_KIND",
]
