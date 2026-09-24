"""AI 起草/单镜重写：后台 job 系统 + 存盘——拆分自 app.py（见 deps.py 头部注释）。

起草/重写走「后台线程 job + 持久化记录」而不是一个阻塞 1-15 分钟的 HTTP
请求；job 记录从不覆盖/删除，天然形成版本历史，也是项目级花费汇总的数据来源。产出先只在草稿态展示，要显式调用
``/api/scripts/save`` 才会真的写进项目文件（``content/story_video/``）。
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from storyvideo.preview import check_duration_budget, shot_duration_budget
from storyvideo.prompts import (
    CameraError,
    compile_frame_prompt,
    compile_negative_prompt,
    compile_video_prompt,
    validate_camera_usage,
)
from storyvideo.scriptgen import (
    ScriptGenError,
    assemble_spec,
    find_genre,
    generate_draft,
    generate_screenplay,
    genre_cards,
    regenerate_shot,
    total_duration_warning,
)
from storyvideo.forms import card_summary as form_card_summary, find_form, form_cards
from storyvideo.spec import SpecError
from storyvideo.spec import normalize_spec as normalize_storyvideo_spec
from storyvideo.post_layers import music_file_problem
from storyvideo.state import atomic_write_json

from genkit.paths import resource as _resource

from ..deps import JOBS_DIR, PROJECT_ROOT, STORYVIDEO_DIR, is_screenplay_file, merge_shot_edit, screenplay_path
from ..problems import problem_cards

router = APIRouter()


class ScriptGenRequest(BaseModel):
    episode_id: str | None = None     # 不给就 EP-<日期>-<序号>
    title: str | None = None          # 用户起的剧名；不给就用 AI 起的
    direction: str
    target_seconds: float
    camera_profile: str
    prompt_language: str = "zh"
    cast_hint: str | None = None
    genre: str | None = None          # 题材卡 id/名/别名（DESIGN §26）；不给就不注入
    form: str | None = None           # 形态卡 id/名/别名（DESIGN §31）；不给就不注入、不补默认值
    # 剧本层与系列交接（DESIGN §32）
    use_screenplay: bool = False      # 两步起草第二步：按已保存的 <id>.screenplay.json 拆镜
    series_id: str | None = None      # 系列 id：给了就把上一集的记忆注入提示词，并写进 spec.series
    episode_no: int | None = None     # 本集是第几集（决定接谁）


def _draft_shot_view(spec: dict[str, Any], shot: dict[str, Any]) -> dict[str, Any]:
    """按草稿(可能还没通过 normalize_spec)拼一份展示用的镜次视图。

    刻意对每一块编译分别兜底：草稿里某一镜的 camera_move 非法或时长越界，
    不该拖累其它 15 镜也看不到——那些镜次的创作内容是真花钱换来的，不能
    因为另一镜的一个数字越界就整份看不见。
    """

    shot_id = shot.get("id", "")
    view: dict[str, Any] = {
        "id": shot_id,
        "dur": shot.get("dur"),
        "cast": shot.get("cast") or [],
        "camera_move": shot.get("camera_move") or "",
        "camera_combo": shot.get("camera_combo") or "",
        "lines": [
            {
                "who": line.get("who", ""),
                "zh": line.get("zh", ""),
                "subtitle_start_seconds": line.get("subtitle_start_seconds"),
                "subtitle_end_seconds": line.get("subtitle_end_seconds"),
            }
            for line in shot.get("lines") or []
        ],
    }
    errors: list[str] = []
    try:
        view["budget"] = shot_duration_budget(shot)
    except Exception as exc:  # noqa: BLE001 - 展示用途，任何异常都不该拖垮整页
        view["budget"] = None
        errors.append(str(exc))
    try:
        view["frame_prompt"] = compile_frame_prompt(spec, shot_id)
        view["neg_prompt"] = compile_negative_prompt(spec, shot_id)
        view["video_prompt"] = compile_video_prompt(spec, shot_id)
    except Exception as exc:  # noqa: BLE001 - 同上；常见是 CameraError
        view["frame_prompt"] = view["neg_prompt"] = view["video_prompt"] = ""
        errors.append(str(exc))
    if errors:
        view["shot_error"] = "；".join(errors)
    return view


def _build_draft_result(
    spec: dict[str, Any],
    *,
    target_seconds: float,
    cost_usd: float | None,
    usage_tokens: int | None,
    backend: str,
) -> dict[str, Any]:
    """草稿起草/单镜重写共用的收尾：校验+编译预览。纯函数，不碰磁盘——

    持久化是调用方（job worker）的事，这里只管把 spec 拼成展示用的结果。

    结构校验（比如某个模型的 dur 取值范围）没过，不代表整份草稿作废——那份
    创作内容是真花钱、真花时间换来的，绝不能因为其中一个数字越界就整份丢掉。
    能编译展示的镜次照常展示，校验错误作为醒目的警告一起带回去，让人在 JSON
    编辑框里改那一个字段就行；真正的硬校验只在 ``/api/scripts/save`` 落盘前把关。
    """
    warnings: list[str] = []
    try:
        validated = normalize_storyvideo_spec(spec, project_root=PROJECT_ROOT)
        warnings += (
            check_duration_budget(validated)
            + validate_camera_usage(validated)
            + total_duration_warning(validated, target_seconds)
        )
        display_spec = validated
    except SpecError as exc:
        warnings.append(f"⚠ 没通过结构校验，保存前必须先在下面的 JSON 里改掉：{exc}")
        display_spec = spec

    shots = [
        _draft_shot_view(display_spec, s) for s in spec.get("shots", []) if isinstance(s, dict)
    ]
    return {
        "spec": spec,
        "warnings": warnings,
        "shots": shots,
        "cost_usd": cost_usd,
        "usage_tokens": usage_tokens,
        "backend": backend,
    }


class DraftShotEditRequest(BaseModel):
    spec: dict[str, Any]
    shot_id: str
    lines: list[dict[str, Any]] | None = None
    camera_move: str | None = None
    camera_combo: str | None = None
    dur: float | None = None
    target_seconds: float = 0.0


@router.post("/api/scripts/draft/shot")
def edit_draft_shot(req: DraftShotEditRequest) -> dict[str, Any]:
    """草稿阶段（还没保存成正式项目）的逐镜编辑——不花钱、不调用任何 AI 接口，

    纯本地合并 + 重新校验 + 重新编译预览。是已保存项目「逐镜编辑保存」
    （``routers/episodes.py`` 的 ``PUT .../shots/{shot_id}``）的镜像版本，
    只是操作对象是内存里还没落盘的草稿 spec，不是磁盘文件；合并规则复用同一个
    ``merge_shot_edit``，两边不会各写一套慢慢跑偏。统一草稿态/
    已保存态的编辑体验——以前保存前改台词/运镜只能裸改整份 JSON，现在两边都是
    逐镜的结构化编辑，JSON 编辑框降级成兜底。
    """
    mutated = copy.deepcopy(req.spec)
    shots = mutated.get("shots") or []
    idx = next((i for i, s in enumerate(shots) if isinstance(s, dict) and s.get("id") == req.shot_id), None)
    if idx is None:
        raise HTTPException(404, f"草稿里没有这一镜：{req.shot_id}")
    merge_shot_edit(
        shots[idx],
        lines=req.lines, camera_move=req.camera_move,
        camera_combo=req.camera_combo, dur=req.dur,
    )
    mutated["shots"] = shots
    # cost_usd/usage_tokens/backend 留空——这次调用不花钱，不该在草稿页顶部的
    # 花费展示区里冒出一条看起来像"又生成了一次"的记录；前端合并结果时只取
    # spec/warnings/shots 三个字段，原有的花费信息保持不变。
    return _build_draft_result(
        mutated, target_seconds=req.target_seconds,
        cost_usd=None, usage_tokens=None, backend="",
    )


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


_log = logging.getLogger("storystudio.scripts")
_warned_jobs: set[str] = set()   # 记过警告的坏文件：恢复条会反复轮询列表，同一个文件别每次都记一行


def _read_job(path: Path) -> dict[str, Any] | None:
    """读一条 job 记录；读不出来 / 不是 JSON 对象都返回 None。

    以前只兜住 JSONDecodeError，``[]`` / ``"x"`` / ``null`` 这种「合法 JSON 但不是对象」会在 ``job.get`` 处炸——
    列表 500、恢复条静默清空、连建项目（存盘后顺手标记 job）都跟着 500。程序自己只写对象，这种文件只可能
    是外面放进来的，跳过并记一行日志让人能找到它。手改过的记录还可能不是 UTF-8（中文 Windows 的记事本常存成
    GBK），``UnicodeDecodeError`` 不是 OSError 也不是 JSONDecodeError，也得一起接住，否则照样 500。
    带 BOM 的（老记事本「UTF-8」存盘）内容是好的，按 utf-8-sig 照读。
    """

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return None                          # 打不开（被占用 / 没权限）不是记录坏了，别记成「坏记录，删掉即可」
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        reason = f"读不出来（{exc}）"
        data = None
    else:
        reason = f"顶层是 {type(data).__name__}，不是对象"
    if isinstance(data, dict):
        return data
    key = str(path)
    if key not in _warned_jobs:
        _warned_jobs.add(key)
        _log.warning("跳过坏的 job 记录 %s：%s（删掉这个文件即可）", path, reason)
    return None


def _job_created_at(job: dict[str, Any]) -> float:
    """排序键：``created_at`` 不是数字的记录排到最后，而不是让整个 sort 抛 TypeError。"""

    value = job.get("created_at")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _write_job(jid: str, **fields: Any) -> None:
    """合并写入一条 job 记录——不是替换，是 patch。job 文件从不删除，天然是

    这个 episode_id 的起草/重写历史。写失败只能尽量不崩：
    调用方是后台线程，没有 HTTP 响应可以把失败传回去，这是这个设计残留的
    唯一没兜住的角落——磁盘写失败本身概率很低，不为它单独建重试机制。

    第一个参数刻意不叫 ``job_id``——``fields`` 里经常也要塞一个 ``job_id``
    字段（存进 JSON 里，不是函数参数），撞名会让 Python 直接报
    "got multiple values for argument"，之前就是这么栽的。
    """

    path = _job_path(jid)
    current: dict[str, Any] = (_read_job(path) if path.is_file() else None) or {}
    current.update(fields)
    current["updated_at"] = time.time()
    try:
        atomic_write_json(path, current)
    except OSError:
        pass


def _series_handoff(req: ScriptGenRequest) -> dict[str, Any] | None:
    """系列记忆里该接的上一集条目；没填系列或系列还没有记忆就是 None。"""

    if not req.series_id:
        return None
    from storyvideo.series import previous_entry

    return previous_entry(PROJECT_ROOT, req.series_id, req.episode_no)


def _read_screenplay(episode_id: str) -> dict[str, Any] | None:
    path = screenplay_path(episode_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _run_generate_job(job_id: str, req: ScriptGenRequest) -> None:
    _write_job(job_id, status="running")
    try:
        screenplay = _read_screenplay(req.episode_id) if req.use_screenplay else None
        if req.use_screenplay and screenplay is None:
            _write_job(job_id, status="failed", error=f"起草失败：项目 {req.episode_id} 还没有剧本——先出剧本，再拆镜")
            return
        handoff = _series_handoff(req)
        draft, cost_usd, usage_tokens, backend = generate_draft(
            episode_id=req.episode_id,
            direction=req.direction,
            target_seconds=req.target_seconds,
            camera_profile=req.camera_profile,
            prompt_language=req.prompt_language,
            cast_hint=req.cast_hint,
            title=req.title,
            genre=req.genre,
            form=req.form,
            screenplay=screenplay,
            series_handoff=handoff,
        )
        if handoff:
            # 同 id 的角色外形用记忆里的原文覆盖——模型改写了也拉回来，系列里身份不漂（DESIGN §32）
            from storyvideo.series import apply_cast_memory

            apply_cast_memory(draft, handoff)
        if screenplay and not draft.get("episode_contract") and isinstance(screenplay.get("contract"), dict):
            draft["episode_contract"] = screenplay["contract"]   # 剧本的契约就是本集的契约
        spec = assemble_spec(
            req.episode_id, draft,
            camera_profile=req.camera_profile, prompt_language=req.prompt_language, form=req.form, genre=req.genre,
        )
        if req.title and req.title.strip():
            spec["title_zh"] = req.title.strip()
        if req.series_id:
            spec["series"] = {"id": req.series_id, **({"episode_no": req.episode_no} if req.episode_no else {})}
    except ScriptGenError as exc:
        # ScriptGenError 的原始文案是给开发者排障用的（claude CLI
        # 退出码/LLM 代理 HTTP 状态码这类），不是操作者能看懂的措辞——加一句
        # "起草失败"的前缀，原始细节留在后面（真出问题时能贴给开发者看）。
        _write_job(job_id, status="failed", error=f"起草失败：{exc}")
        return
    except Exception as exc:  # noqa: BLE001 - 后台线程，任何异常都不能让 job 卡在 running 里
        _write_job(job_id, status="failed", error=f"起草遇到没预料到的错误（请把这条完整信息发给开发者）：{exc}")
        return
    result = _build_draft_result(
        spec, target_seconds=req.target_seconds,
        cost_usd=cost_usd, usage_tokens=usage_tokens, backend=backend,
    )
    _write_job(job_id, status="succeeded", result=result)


def _run_regenerate_shot_job(job_id: str, req: "ShotRegenerateRequest") -> None:
    _write_job(job_id, status="running")
    try:
        new_shot, cost_usd, usage_tokens, backend = regenerate_shot(
            spec=req.spec, shot_id=req.shot_id, feedback=req.feedback,
        )
    except ScriptGenError as exc:
        _write_job(job_id, status="failed", error=f"重写这一镜失败：{exc}")
        return
    except Exception as exc:  # noqa: BLE001
        _write_job(job_id, status="failed", error=f"重写这一镜遇到没预料到的错误（请把这条完整信息发给开发者）：{exc}")
        return

    mutated = copy.deepcopy(req.spec)
    shots = mutated.get("shots") or []
    idx = next((i for i, s in enumerate(shots) if isinstance(s, dict) and s.get("id") == req.shot_id), None)
    if idx is None:
        _write_job(job_id, status="failed", error=f"草稿里没有这一镜：{req.shot_id}")
        return
    shots[idx] = new_shot
    mutated["shots"] = shots

    result = _build_draft_result(
        mutated, target_seconds=req.target_seconds,
        cost_usd=cost_usd, usage_tokens=usage_tokens, backend=backend,
    )
    _write_job(job_id, status="succeeded", result=result)


@router.post("/api/scripts/jobs/draft")
def create_draft_job(req: ScriptGenRequest) -> dict[str, Any]:
    """立刻返回 job_id，真正的起草在后台线程跑——不再用一个阻塞 1-15 分钟的

    HTTP 请求硬扛付费调用。标签页关掉、页面刷新，进度和结果都在磁盘上，
    重新打开页面能凭 ``GET /api/scripts/jobs`` 找回来（见模块顶部注释）。
    这一步本身就会真花钱/真耗用量，所以没有做跟图片/视频生成一样的
    plan_token 二次确认弹窗——点这个按钮本身就是确认。产出不落盘、不建
    项目文件；要显式调用 ``/api/scripts/save`` 才会真的写文件。
    """
    from genkit.ids import is_valid_id, next_episode_id as _next_episode_id

    req.episode_id = str(req.episode_id or "").strip() or _next_episode_id(existing_episode_ids())
    if not is_valid_id(req.episode_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    if (STORYVIDEO_DIR / f"{req.episode_id}.json").exists():
        raise HTTPException(409, f"项目 {req.episode_id} 已经存在，换一个 id，或者去编辑现有项目。")
    # 起草真花钱：账本还占着这个 id 的话，出来的草稿存盘时必然 409（草稿 id 由 job 决定，界面改不了），在花钱前拦
    _refuse_ledger_taken(req.episode_id)
    # 题材在这里就拦：后台线程里才发现是 ValueError，用户只会看到一条「起草失败」。
    req.genre = str(req.genre or "").strip() or None
    if req.genre and find_genre(req.genre) is None:
        raise HTTPException(400, f"不认识的题材：{req.genre}")
    req.form = str(req.form or "").strip() or None
    if req.form and find_form(req.form) is None:
        raise HTTPException(400, f"不认识的形态：{req.form}")
    _check_series_fields(req)
    if req.use_screenplay and not screenplay_path(req.episode_id).is_file():
        raise HTTPException(400, f"项目 {req.episode_id} 还没有剧本——先出剧本，再拆镜")
    job_id = uuid.uuid4().hex
    _write_job(
        job_id, job_id=job_id, episode_id=req.episode_id, kind="generate_draft",
        status="pending", created_at=time.time(),
        params={
            "direction": req.direction, "target_seconds": req.target_seconds,
            "camera_profile": req.camera_profile, "prompt_language": req.prompt_language,
            "cast_hint": req.cast_hint, "title": req.title, "genre": req.genre, "form": req.form,
            "use_screenplay": req.use_screenplay, "series_id": req.series_id, "episode_no": req.episode_no,
        },
    )
    threading.Thread(target=_run_generate_job, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id, "episode_id": req.episode_id}


# ---- 剧本层（DESIGN §32）：两步起草的第一步——先出剧本（场次 / 节拍 / 对白 / 契约），人看过改过再拆镜


def _check_series_fields(req: ScriptGenRequest) -> None:
    """系列 id / 集数在建 job 前就拦；后台线程里才发现只会剩一条「起草失败」。"""

    from storyvideo.contract import ContractError, normalize_series

    req.series_id = str(req.series_id or "").strip() or None
    if req.series_id is None:
        req.episode_no = None
        return
    try:
        cleaned = normalize_series({"id": req.series_id, "episode_no": req.episode_no})
    except ContractError as exc:
        raise HTTPException(400, str(exc)) from exc
    req.series_id = cleaned["id"]
    req.episode_no = cleaned.get("episode_no")


def _run_screenplay_job(job_id: str, req: ScriptGenRequest) -> None:
    _write_job(job_id, status="running")
    try:
        screenplay, cost_usd, usage_tokens, backend = generate_screenplay(
            episode_id=req.episode_id,
            direction=req.direction,
            target_seconds=req.target_seconds,
            prompt_language=req.prompt_language,
            cast_hint=req.cast_hint,
            title=req.title,
            genre=req.genre,
            form=req.form,
            series_handoff=_series_handoff(req),
        )
    except ScriptGenError as exc:
        _write_job(job_id, status="failed", error=f"出剧本失败：{exc}")
        return
    except Exception as exc:  # noqa: BLE001 - 后台线程，任何异常都不能让 job 卡在 running 里
        _write_job(job_id, status="failed", error=f"出剧本遇到没预料到的错误（请把这条完整信息发给开发者）：{exc}")
        return
    # 能归一化就存归一化后的（场次 id 补成 S01…、空行去掉），编辑器一打开就是干净的；
    # 归一化失败绝不丢掉这次真花钱的产出——原样存下来，让人在编辑器里改，PUT 时再把关。
    from storyvideo.screenplay import lint_screenplay, normalize_screenplay

    try:
        screenplay = normalize_screenplay(screenplay)
    except SpecError:
        pass
    warnings = lint_screenplay(screenplay)
    path = screenplay_path(req.episode_id)
    try:
        STORYVIDEO_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, screenplay)
    except OSError as exc:
        _write_job(job_id, status="failed", error=f"剧本写盘失败：{exc}")
        return
    # 剧本已经落盘（<id>.screenplay.json），恢复条不用再把它当「没保存的草稿」提醒
    _write_job(
        job_id, status="succeeded", saved=True,
        result={
            "screenplay": screenplay, "warnings": warnings, "problems": problem_cards(warnings),
            "cost_usd": cost_usd, "usage_tokens": usage_tokens, "backend": backend,
            "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        },
    )


@router.post("/api/scripts/jobs/screenplay")
def create_screenplay_job(req: ScriptGenRequest) -> dict[str, Any]:
    """两步起草第一步：后台出一份剧本 JSON，成功后存成 ``content/story_video/<id>.screenplay.json``
    （项目文件本身还不存在——第二步 ``/api/scripts/jobs/draft`` 带 ``use_screenplay: true`` 按它拆镜，
    再 ``/api/scripts/save`` 才建项目）。同样真花钱，点按钮就是确认。"""

    from genkit.ids import is_valid_id, next_episode_id as _next_episode_id

    req.episode_id = str(req.episode_id or "").strip() or _next_episode_id(existing_episode_ids())
    if not is_valid_id(req.episode_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    if (STORYVIDEO_DIR / f"{req.episode_id}.json").exists():
        raise HTTPException(409, f"项目 {req.episode_id} 已经存在，换一个 id，或者去编辑现有项目。")
    _refuse_ledger_taken(req.episode_id)   # 同起草：出剧本也真花钱，账本占着的 id 之后建不成项目
    req.genre = str(req.genre or "").strip() or None
    if req.genre and find_genre(req.genre) is None:
        raise HTTPException(400, f"不认识的题材：{req.genre}")
    req.form = str(req.form or "").strip() or None
    if req.form and find_form(req.form) is None:
        raise HTTPException(400, f"不认识的形态：{req.form}")
    _check_series_fields(req)
    job_id = uuid.uuid4().hex
    _write_job(
        job_id, job_id=job_id, episode_id=req.episode_id, kind="generate_screenplay",
        status="pending", created_at=time.time(),
        params={
            "direction": req.direction, "target_seconds": req.target_seconds,
            "prompt_language": req.prompt_language, "cast_hint": req.cast_hint, "title": req.title,
            "genre": req.genre, "form": req.form, "series_id": req.series_id, "episode_no": req.episode_no,
        },
    )
    threading.Thread(target=_run_screenplay_job, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id, "episode_id": req.episode_id}


class ScreenplayPut(BaseModel):
    screenplay: dict[str, Any]


def _screenplay_view(episode_id: str, screenplay: dict[str, Any]) -> dict[str, Any]:
    """逐场编辑器要的一整份：剧本本身 + 告警（整句）+ 问题卡（逐场分组用）+ 顶部三个数 + 纯文本预览。"""

    from storyvideo.screenplay import lint_screenplay, screenplay_stats
    from storyvideo.scriptgen import screenplay_text

    warnings = lint_screenplay(screenplay)
    return {
        "episode_id": episode_id,
        "screenplay": screenplay,
        "warnings": warnings,
        "problems": problem_cards(warnings),
        "stats": screenplay_stats(screenplay),
        "text": screenplay_text(screenplay),
        "path": str(screenplay_path(episode_id).relative_to(PROJECT_ROOT)).replace("\\", "/"),
    }


@router.get("/api/episodes/storyvideo/{episode_id}/screenplay")
def get_screenplay(episode_id: str) -> dict[str, Any]:
    """已保存的剧本（两步起草第一步的产物）。项目建成后它还在，供回看/继续改；没有就 404。

    刻意不在读的时候做硬校验：模型刚吐出来的剧本可能哪里形状不对，那也得让人在编辑器里看见、
    改掉——读不出来只能去翻文件就是回到裸 JSON 了。校验只在 PUT 落盘前把关。
    """

    from genkit.ids import is_valid_id

    if not is_valid_id(episode_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    screenplay = _read_screenplay(episode_id)
    if screenplay is None:
        raise HTTPException(404, f"项目 {episode_id} 没有剧本——用「两步起草」先出剧本")
    return _screenplay_view(episode_id, screenplay)


@router.put("/api/episodes/storyvideo/{episode_id}/screenplay")
def put_screenplay(episode_id: str, req: ScreenplayPut) -> dict[str, Any]:
    """人改过的剧本写回去（整份替换）：先归一化+校验（场次 id 补齐/不重复、场景标题不空、
    说话人必须在人物表里），过了才落盘，返回跟 GET 一样的一份（含新的告警）。

    校验没过 = 400 + 一句人话，界面把它显示在保存按钮旁边——比默默存一份拆镜时会出错的剧本好。
    """

    from genkit.ids import is_valid_id
    from storyvideo.screenplay import normalize_screenplay

    if not is_valid_id(episode_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    if not (STORYVIDEO_DIR / f"{episode_id}.json").exists():
        # 项目还没建（两步起草的第一步）：剧本文件会占住这个 id，账本也占着的话之后建不成项目。
        # 项目已经在的就是正常改剧本，账本里有它的 run 天经地义
        _refuse_ledger_taken(episode_id)
    try:
        screenplay = normalize_screenplay(req.screenplay)
    except SpecError as exc:
        raise HTTPException(400, f"剧本没通过校验，没有保存：{exc}") from exc
    STORYVIDEO_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(screenplay_path(episode_id), screenplay)
    return _screenplay_view(episode_id, screenplay)


class ShotRegenerateRequest(BaseModel):
    episode_id: str
    spec: dict[str, Any]
    shot_id: str
    feedback: str | None = None
    target_seconds: float = 0.0


@router.post("/api/scripts/jobs/shot")
def create_shot_job(req: ShotRegenerateRequest) -> dict[str, Any]:
    """同上，但只重写草稿里的一镜，其它镜次/全局字段原样不动——真花钱重写

    整份 11 镜的草稿，只为改一句台词，是明显的浪费。
    """
    job_id = uuid.uuid4().hex
    _write_job(
        job_id, job_id=job_id, episode_id=req.episode_id, kind="regenerate_shot",
        status="pending", created_at=time.time(),
        params={"shot_id": req.shot_id, "feedback": req.feedback},
    )
    threading.Thread(target=_run_regenerate_shot_job, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@router.get("/api/scripts/jobs/{job_id}")
def get_script_job(job_id: str) -> dict[str, Any]:
    path = _job_path(job_id)
    if not path.is_file():
        raise HTTPException(404, f"没有这个 job：{job_id}")
    job = _read_job(path)
    if job is None:
        # 半截 JSON 以前直接 500；非对象的记录会撞响应模型校验也是 500——都按「记录坏了」说清楚
        raise HTTPException(404, f"job 记录损坏：{job_id}")
    return job


@router.get("/api/scripts/jobs")
def list_script_jobs(episode_id: str | None = None) -> list[dict[str, Any]]:
    """按时间倒序列出 job 记录。job 从不覆盖/删除，天然是某个 episode_id 的

    起草/重写历史（版本历史）。不传 episode_id 就列全部——
    用于「有没有还没保存的草稿/还在跑的任务」这种全局恢复场景。
    """
    if not JOBS_DIR.is_dir():
        return []
    jobs = []
    for path in JOBS_DIR.glob("*.json"):
        job = _read_job(path)
        if job is None:
            continue                         # 一个坏文件不能拖垮整个列表
        if episode_id and job.get("episode_id") != episode_id:
            continue
        jobs.append(job)
    jobs.sort(key=_job_created_at, reverse=True)
    return jobs


def _mark_episode_jobs_saved(episode_id: str) -> None:
    """项目存盘后，把这个 episode_id 名下已成功的 job 标记为「已保存」——

    job 历史本身不删（还是版本记录），但恢复条不该再把已经保存过的草稿当成
    「还没保存」提醒你。
    """
    if not JOBS_DIR.is_dir():
        return
    for path in JOBS_DIR.glob("*.json"):
        job = _read_job(path)
        if job is None:
            continue
        if job.get("episode_id") == episode_id and job.get("status") == "succeeded":
            job["saved"] = True
            try:
                atomic_write_json(path, job)
            except OSError:
                pass


@router.post("/api/scripts/jobs/{job_id}/dismiss")
def dismiss_script_job(job_id: str) -> dict[str, Any]:
    """把恢复条里的这条提醒关掉——不删 job 记录本身（还是版本历史的一部分），

    只是不再把它当成「需要你去处理」的东西列进恢复条。
    """
    if not _job_path(job_id).is_file():
        raise HTTPException(404, f"没有这个 job：{job_id}")
    _write_job(job_id, dismissed=True)
    return {"dismissed": job_id}


EXAMPLES_DIR = _resource("examples", "episodes")


def _list_examples() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not EXAMPLES_DIR.is_dir():
        return items
    for path in sorted(EXAMPLES_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        shots = [s for s in raw.get("shots", ()) if isinstance(s, dict)]
        items.append({
            "name": path.stem,
            "title": raw.get("title_zh") or raw.get("id") or path.stem,
            "logline": raw.get("logline") or "",
            "camera_profile": raw.get("camera_profile") or "",
            "shot_count": len(shots),
            "total_seconds": sum(float(s.get("dur") or 0) for s in shots),
            "has_voice_over": bool((raw.get("voice_over") or {}).get("lines")),
            "has_inserts": bool((raw.get("post_graphics") or {}).get("phone_ui_inserts")),
            "notes": raw.get("design_notes") or "",
        })
    return items


@router.get("/api/scripts/genres")
def list_genres() -> list[dict[str, Any]]:
    """新建项目页「题材（可选）」下拉：每张题材卡一行摘要（id/名/别名/核心一句话）。

    完整卡只进系统提示词，不进接口——卡是写作指导，不是给观众看的交付物。
    另带出处（``confidence`` / ``basis`` / ``source_file``，从原卡抄录）和这张卡的镜头风格搭配
    （``pairing``：recommended / avoid / why，DESIGN §48），页面不用再多发一个请求。
    """

    from storyvideo.pairing import pairing_for

    return [
        {
            "id": card["id"],
            "name": card["name"],
            "aliases": list(card.get("aliases") or ()),
            "core": str(card.get("core") or ""),
            "confidence": str(card.get("confidence") or ""),
            "basis": str(card.get("basis") or ""),
            "source_file": str(card.get("source_file") or ""),
            "pairing": pairing_for(card["id"]),
        }
        for card in genre_cards()
    ]


@router.get("/api/scripts/forms")
def list_forms() -> list[dict[str, Any]]:
    """新建项目页「形态（可选）」下拉：每张形态卡一行（id/名/别名/默认值/必带项/一句摘要）（DESIGN §31）。"""

    return [
        {
            "id": card["id"],
            "name": card["name"],
            "aliases": list(card.get("aliases") or ()),
            "defaults": dict(card.get("defaults") or {}),
            "must_have": list(card.get("must_have") or ()),
            "summary": form_card_summary(card),
        }
        for card in form_cards()
    ]


@router.get("/api/examples")
def list_examples() -> list[dict[str, Any]]:
    """随程序打包的示例剧集（``examples/episodes/*.json``）——新用户不用先起草，
    直接复制一份就能走到「出首帧」。"""

    return _list_examples()


class ExampleCreateRequest(BaseModel):
    episode_id: str | None = None     # 不给就 DEMO-1、DEMO-2…


def existing_episode_ids() -> list[str]:
    from genkit import trash

    from ..deps import ledger_run_ids
    from . import episodes

    # 账本里还有 run 的 id 也算占用：项目文件没了、账本行还在（手动删了项目文件、补丁前的归档）的 id 再发出去，
    # 起草 / 出剧本这些付费步骤都能做完，到存盘才 409，那份草稿就存不下来了。
    # 回收站里的 id 同样算占用：删一个再建一个，新项目要是拿到刚删的 id，回收站里那一份就再也恢复不回来
    taken = ledger_run_ids() + trash.archived_ids(PROJECT_ROOT, trash.STORYVIDEO, root=episodes.DELETED_DIR)
    if not STORYVIDEO_DIR.is_dir():
        return taken
    # 两步起草的 <id>.screenplay.json 不是项目，但它占着这个 id：不能再分配给别的新项目
    return [p.stem.removesuffix(".screenplay") for p in STORYVIDEO_DIR.glob("*.json")] + taken


@router.get("/api/episodes/next-id")
def next_episode_id(prefix: str = "EP") -> dict[str, str]:
    """给新建项目/复制示例的表单预填一个还不存在的项目编号（用户可改）。"""

    from genkit.ids import next_episode_id as _next, numbered_id

    prefix = prefix.strip() or "EP"
    if prefix.upper() == "DEMO":
        return {"id": numbered_id("DEMO-", existing_episode_ids())}
    return {"id": _next(existing_episode_ids(), prefix=prefix)}


@router.post("/api/examples/{name}/create")
def create_from_example(name: str, req: ExampleCreateRequest) -> dict[str, Any]:
    """把示例复制成一个新项目：只改 id 与 output_root，其余（台词、旁白、插卡）
    原样保留。不调用任何付费接口。"""

    source = EXAMPLES_DIR / f"{name}.json"
    if not source.is_file() or source.parent != EXAMPLES_DIR:
        raise HTTPException(404, f"没有这个示例：{name}")
    from genkit.ids import is_valid_id, numbered_id

    episode_id = str(req.episode_id or "").strip() or numbered_id("DEMO-", existing_episode_ids())
    if not is_valid_id(episode_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    target = STORYVIDEO_DIR / f"{episode_id}.json"
    if target.exists():
        raise HTTPException(409, f"项目 {episode_id} 已经存在，换一个 id。")
    _refuse_ledger_taken(episode_id)
    spec = json.loads(source.read_text(encoding="utf-8-sig"))
    spec["id"] = episode_id
    spec["output_root"] = f"output/story_video/{episode_id}"
    _refuse_bad_music_files(spec)
    try:
        validated = normalize_storyvideo_spec(spec, project_root=PROJECT_ROOT)
        validate_camera_usage(validated)
    except SpecError as exc:
        raise HTTPException(400, f"示例没通过结构校验（这是示例文件本身的问题）：{exc}") from exc
    except CameraError as exc:
        raise HTTPException(400, f"示例里的运镜不在当前镜头库里：{exc}") from exc
    STORYVIDEO_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, spec)
    return {"id": episode_id, "path": str(target.relative_to(PROJECT_ROOT)).replace("\\", "/"), "example": name}


class ScriptSaveRequest(BaseModel):
    episode_id: str
    spec: dict[str, Any]


def _refuse_bad_music_files(spec: Mapping[str, Any]) -> None:
    """草稿 / 示例里的配乐 post_graphics.music[].file 过一遍跟后期页同一条规则（music_file_problem）再落盘。

    后期页的 PUT 早就查，存草稿和从示例建项目却不查：数据目录外的音频就这样写进项目，合成时混进成片（PB-5）。
    只查配乐这一项，不跑整套后期块校验——草稿里别的字段不该因此被误拒。"""

    graphics = spec.get("post_graphics")
    music = graphics.get("music") if isinstance(graphics, Mapping) else None
    if not isinstance(music, list):
        return
    for i, track in enumerate(music):
        file = str(track.get("file") or "").strip() if isinstance(track, Mapping) else ""
        problem = music_file_problem(file, PROJECT_ROOT) if file else None
        if problem:
            raise HTTPException(400, f"草稿里的配乐 music[{i}] 用不了：{problem}——只能用上传到数据目录 content/ 下的音频；"
                                     "删掉这一条，建好项目后在「后期层」的「配乐」里上传。没有保存")


def _refuse_ledger_taken(episode_id: str) -> None:
    """账本里还有这个 id 的 run（项目文件被手动删了、归档没带走账本行）→ 409：建出来也永远开不了工，不如现在说。"""

    from ..deps import ledger_run

    if ledger_run(episode_id) is not None:
        raise HTTPException(409, f"这个 id 在账本里还有旧的制作记录（{episode_id}）——换一个 id，或先从归档把它恢复回来")


@router.post("/api/scripts/save")
def save_script(req: ScriptSaveRequest) -> dict[str, Any]:
    """把（可能已经人工改过的）草稿存成新项目文件。episode_id 必须还不存在——

    这是建项目，不是编辑已有项目；已有项目请用逐镜编辑接口。

    episode_id 走跟其它建项目入口同一套 id 校验（这里曾经漏了：``"../x"`` 会把文件写到 content/story_video
    之外），而且必须跟草稿里的 ``spec.id`` 一致——文件名与 spec.id 分叉的项目，列表按文件名、详情 / 账本 /
    产物链接按 spec.id，首帧生成出来界面上是 404。
    """
    from genkit.ids import is_valid_id

    episode_id = str(req.episode_id or "").strip()
    if not is_valid_id(episode_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    spec = dict(req.spec)
    spec_id = str(spec.get("id") or "").strip()
    if spec_id and spec_id != episode_id:
        raise HTTPException(400, f"草稿里的 id「{spec_id}」跟要保存的项目 id「{episode_id}」对不上——两边要一致")
    spec["id"] = episode_id
    root_str = str(spec.get("output_root") or "").strip()
    if not root_str:
        spec["output_root"] = f"output/story_video/{episode_id}"
    elif Path(root_str).name != episode_id:
        raise HTTPException(400, f"草稿里的 output_root「{root_str}」没有以项目 id「{episode_id}」结尾——产物目录必须跟项目 id 对应")
    path = STORYVIDEO_DIR / f"{episode_id}.json"
    if path.resolve().parent != STORYVIDEO_DIR.resolve():
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    if path.exists():
        raise HTTPException(409, f"项目 {episode_id} 已经存在，不会覆盖。")
    _refuse_ledger_taken(episode_id)
    _refuse_bad_music_files(spec)
    try:
        validated = normalize_storyvideo_spec(spec, project_root=PROJECT_ROOT)
        validate_camera_usage(validated)
    except SpecError as exc:
        raise HTTPException(400, f"草稿没通过结构校验，没有保存：{exc}") from exc
    except CameraError as exc:
        raise HTTPException(400, f"草稿里的运镜不合法，没有保存：{exc}") from exc
    atomic_write_json(path, spec)
    try:
        _mark_episode_jobs_saved(episode_id)
    except Exception:  # noqa: BLE001 - 标记只是「恢复条别再提醒」的附带动作，项目文件已经写好了，不能因为它把建项目报成失败
        _log.exception("项目 %s 已保存，但标记它名下的 job 为「已保存」时出错", episode_id)
    return {"id": episode_id, "path": str(path.relative_to(PROJECT_ROOT))}
