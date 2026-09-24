"""跨路由共享的东西：路径常量、运行时错误元组、几个纯函数辅助。

拆分自单文件 app.py（app.py 单文件曾装了 7 类职责，没用 APIRouter 拆分）。这里只放「好几个路由模块都要用」的东西——只在一个
路由模块内部用的辅助函数留在那个模块里，不往这里塞，不然这个文件会变成
新的一个"什么都装"的地方。
"""

from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from fastapi import HTTPException

from genkit import settings as settings_mod
from genkit.providers import ProviderError
from genkit.store import RunCollisionError, RunStore

from storyvideo.assembly import AssemblyError
from storyvideo.media import MediaValidationError
from storyvideo.continuity import check_all as check_continuity_rules
from storyvideo.post_layers import silence_overrun_warnings
from storyvideo.preview import check_dialogue_clarity, check_duration_budget, shot_duration_budget
from storyvideo.prompts import (
    compile_frame_prompt,
    compile_negative_prompt,
    compile_video_prompt,
    reference_slot_plan,
    validate_camera_usage,
)
from storyvideo.qc import QCError
from storyvideo.runtime import StoryVideoError, StoryVideoRunner, ledger_path
from storyvideo.spec import SpecError, stable_json_hash
from storyvideo.state import read_json

from .modules import module_for_pipeline
from .problems import problem_cards

from genkit.paths import data_root as _data_root

PROJECT_ROOT = _data_root()
STORYVIDEO_DIR = PROJECT_ROOT / "content" / "story_video"
STATIC_DIR = Path(__file__).resolve().parent / "static"
# AI 起草/单镜重写走「后台线程 job + 持久化记录」而不是一个阻塞 1-15 分钟的
# HTTP 请求——阻塞请求下标签页一意外消失，真花钱生成的草稿就彻底找不回来了。
# job 记录从不覆盖/删除，天然形成版本历史。跟真正的项目文件（content/story_video/）分开存放，不会被 git 追踪。
DRAFT_CACHE_DIR = PROJECT_ROOT / ".webui_draft_cache"
JOBS_DIR = DRAFT_CACHE_DIR / "jobs"
# 系列记忆（DESIGN §32）：content/series/<series_id>.json，跟项目文件一样是内容、不是缓存
SERIES_DIR = PROJECT_ROOT / "content" / "series"
# 剧本层（DESIGN §32）：两步起草的第一步落在项目文件旁边，<id>.screenplay.json；列项目时要把它跳过
SCREENPLAY_SUFFIX = ".screenplay"


def screenplay_path(episode_id: str) -> Path:
    return STORYVIDEO_DIR / f"{episode_id}{SCREENPLAY_SUFFIX}.json"


def is_screenplay_file(path: Path) -> bool:
    return path.stem.endswith(SCREENPLAY_SUFFIX)


RUNNER_ERRORS = (
    AssemblyError,
    MediaValidationError,
    ProviderError,
    QCError,
    RunCollisionError,
    SpecError,
    StoryVideoError,
)


def ledger_run(run_id: str) -> dict[str, Any] | None:
    """账本里这个 id 的 run 行；账本文件不存在、打不开、id 形状不合法或没这条记录都返回 None（只读打开，不碰生产锁）。"""

    store_path = ledger_path(PROJECT_ROOT)
    if not store_path.is_file():
        return None
    try:
        with RunStore(store_path, readonly=True) as store:
            return store.get_run(str(run_id))
    except Exception:  # noqa: BLE001 - 探测用途：账本坏了 / 版本不对 / id 不合法都当「没有记录」
        return None


def ledger_run_ids() -> list[str]:
    """账本里所有 run 的 id（只读；账本不存在或打不开返回空表）。

    给「下一个可用编号」用：项目文件没了、账本行还在的 id（补丁前的归档、手动删了项目文件）照样被占着，
    再发出去的话起草 / 出剧本这些付费步骤都能做完，到存盘才 409，那份草稿就再也存不下来。
    """

    store_path = ledger_path(PROJECT_ROOT)
    if not store_path.is_file():
        return []
    try:
        with RunStore(store_path, readonly=True) as store:
            return list(store.list_run_ids())
    except Exception:  # noqa: BLE001 - 探测用途：账本坏了就只按项目文件算
        return []


def local_artifacts_exist(spec: Mapping[str, Any]) -> bool:
    """输出目录里有没有首帧 / 视频 / 成片文件（只看文件，不看账本）。"""

    output_root = Path(spec["output_root"])
    shots = [s for s in spec.get("shots", ()) if isinstance(s, dict)]
    candidates = [
        *(output_root / "01_frames" / f"{s['id']}.png" for s in shots),
        *(output_root / "02_video" / f"{s['id']}.mp4" for s in shots),
        output_root / f"{spec.get('id')}_final.mp4",
    ]
    return any(p.is_file() for p in candidates)
# 没配文本模型时的 400（播客 / 剧本工作台 / 短剧审稿共用；英文在 i18n_rules 里）
NO_TEXT_MODEL = "还没有可用的文本模型：在「模型设置」里给「剧本文本」选一个来源并填密钥"


def require_roles(provider: Any, *roles: str) -> None:
    """起任务前先拦「角色没配」：没配就 400（说清去「模型设置」配哪个角色），不起任务、不留任务记录、不锁草稿。

    ``provider`` 给 ``None`` 按当前模型设置查；给了就按它自己的设置查（``RoutedProvider.settings``）——测试注入的
    假来源没有 settings，一律不拦。判断本身只有 ``genkit.settings.role_missing_message`` 一处，别在路由里各写一套。
    2026-09-22 审计：设置为空（全新安装）第一次点「生成」，各模块有的 400、有的起任务再逐项失败却记成「成功」，
    短剧直接把项目锁死在「提交结果不明」。
    """

    if provider is not None:
        loaded = getattr(provider, "settings", None)
        if not isinstance(loaded, settings_mod.Settings):
            return
    else:
        try:
            loaded = settings_mod.load_settings()
        except ValueError:
            loaded = settings_mod.Settings()   # 设置文件坏了按「全部未配置」拦；设置页会给出真实错误
    for role in roles:
        message = settings_mod.role_missing_message(loaded, role)
        if message:
            raise HTTPException(400, message)


def require_text_model(dispatch: Any = None) -> None:
    """起草 / 审稿前先拦「没配文本模型」：测试注入了 ``dispatch`` 就不拦。判断在 ``scriptgen.text_model_available``
    （设置里的文本角色 → .env 代理，两条路都没有才算没配）。"""

    if dispatch is not None:
        return
    from storyvideo.scriptgen import text_model_available

    if not text_model_available():
        raise HTTPException(400, NO_TEXT_MODEL)


def all_failed_message(result: Mapping[str, Any], *, unit: str, done_key: str = "generated") -> str | None:
    """批量任务的结果一件都没成、也没跳过、全在 ``failed`` 里 → 返回该让任务记 failed 的原因（几件 + 首条原因）；
    否则 ``None``（部分失败仍按「一件失败不拖累整批」正常返回，原因在 ``result.failed`` 里逐条记着）。

    任务运行器只看任务体抛不抛异常：0 成功的批次以前也记成 succeeded，任务中心显示绿色「成功」而一张图都没有
    （2026-09-22 审计 FAM-3 / PKG-5）。
    """

    failed = list(result.get("failed") or ())
    if not failed or result.get(done_key) or result.get("skipped"):
        return None
    first = str((failed[0] or {}).get("error") or "全部失败")
    return first if len(failed) == 1 else f"{len(failed)} {unit}全部失败：{first}"


def is_locked(output_root: str) -> bool:
    """「已开始制作」：冻结快照在，或者账本里有这个项目的 run（output_root 按约定以项目 id 结尾）。

    以前只看快照文件：输出目录被清掉之后界面就把项目当草稿放行编辑，而账本里 run 还在，之后每次
    prepare 都撞「内容被改过」——两套真相只信一套的结果。账本说开始过就算开始过，详情页另给出原因。
    """

    root = Path(output_root)
    if (root / "manifests" / "episode.snapshot.json").is_file():
        return True
    return ledger_run(root.name) is not None


def freeze_state(spec: Mapping[str, Any]) -> dict[str, Any]:
    """项目文件、账本里的 run、冻结快照三方是否对得上（只读，不改任何东西）。

    ``mismatch``：None = 一致（或还没开始制作）；``snapshot_missing`` = 账本有 run 但快照不在（输出目录被清空/归档）；
    ``source_changed`` = 项目文件在开始制作之后被外部改过（跟账本记的 hash 不一致）；``snapshot_changed`` = 快照文件
    本身被改过；``ledger_missing`` = 快照和产物文件都在、账本里却没有这个 run（从归档手动搬回来、账本行丢了）。
    所有会写盘/花钱的入口（prepare / review / generate / 后期块）都做同一组核对并拒绝，这里提前算出来
    让详情页能先说，而不是等每个动作各报一次错。
    """

    root = Path(str(spec["output_root"]))
    snapshot_path = root / "manifests" / "episode.snapshot.json"
    run = ledger_run(str(spec.get("id") or root.name))
    snapshot_exists = snapshot_path.is_file()

    def snapshot_hash() -> str | None:
        try:
            return stable_json_hash(read_json(snapshot_path, missing={}))
        except Exception:  # noqa: BLE001 - 快照文件坏了（不是合法 JSON）也算被外部改过，详情页不能因此 500
            return None

    mismatch: str | None = None
    if run is not None:
        if not snapshot_exists:
            mismatch = "snapshot_missing"
        elif run.get("input_hash") != spec.get("source_hash"):
            mismatch = "source_changed"
        elif snapshot_hash() != spec.get("source_hash"):
            mismatch = "snapshot_changed"
    elif snapshot_exists and local_artifacts_exist(spec):
        # 账本没有 run、快照和产物却在：prepare 会以「本地已有文件但账本没登记」拒绝，只有 import-legacy 能重新登记。
        # 没有产物时不算：prepare 会当成全新项目重写快照、建 run，自己就走通了
        mismatch = "ledger_missing"
    return {"locked": snapshot_exists or run is not None, "run": run is not None, "snapshot": snapshot_exists,
            "mismatch": mismatch}


# 三种不一致对应的提示（详情页 warnings / readonly_reason 用）：说清是哪一层对不上、下一步做什么
FREEZE_MISMATCH_HINTS = {
    "snapshot_missing": "输出目录不在了，但账本里还有这个项目的制作记录——把目录搬回来（或从归档恢复），或者换一个 id 重建",
    "source_changed": "项目文件在开始制作之后被外部改过，跟冻结快照对不上——撤回改动，或复制成新版本再改",
    "snapshot_changed": "冻结快照文件被外部改过，账本对不上——不要手动编辑 manifests 目录；改设计请复制成新版本",
    "ledger_missing": "账本里没有这个项目的制作记录，产物文件却都在——点「导入已有产物」重新登记，不花钱；"
                      "如果是从归档手动搬回来的，也可以在侧栏「已删除的项目」里恢复它的账本记录",
}


def redact_local_paths(text: str) -> str:
    """报错原文里的本机绝对路径收成相对数据目录的写法：数据目录下的路径去掉前缀，数据目录本身写成「<数据目录>」，
    用户主目录写成「~」——接口文案会被贴进反馈、截图，不该带出用户名和数据根位置（2026-09-23 发布前自查 PB-8）。
    正反斜杠两种写法都认（SpecError 里的路径是 resolve 过的原生写法，as_posix 过的也有）。"""

    out = str(text)
    candidates: list[tuple[str, str]] = []
    for base, label in ((PROJECT_ROOT, "<数据目录>"), (Path.home(), "~")):
        try:
            resolved = base.resolve()
        except (OSError, RuntimeError):
            resolved = base
        for form in {str(base), str(resolved), base.as_posix(), resolved.as_posix()}:
            if form and form not in (".", "/"):
                candidates.append((form, label))
    # 长的先换：数据目录通常在主目录下面，先换主目录的话数据目录前缀就对不上了
    for form, label in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        if label == "<数据目录>":
            for sep in ("\\", "/"):
                out = out.replace(form + sep, "")
        out = out.replace(form, label)
    return out


_OUTSIDE_FIELD_RE = re.compile(r"^([\w.\[\]]+) resolves outside project boundary")


def spec_not_found_detail(exc: BaseException, episode_id: str) -> str:
    """SpecError → 给接口的 404 文案：找不到文件时说「没有这个项目」而不是带本机绝对路径的英文原文；
    文件在但读不了/结构不对时走 humanize（原文附在后面，本机绝对路径先收成相对写法，见 :func:`redact_local_paths`）。

    id 本身越界（``..\\x`` 这种）也当「没有这个项目」：对调用方来说就是这个 id 在数据目录里不对应任何项目；
    项目文件里的某个路径字段越界时只说是哪个字段，不回显解析出来的绝对路径。"""

    from .humanize import humanize

    text = str(exc)
    if "episode spec not found" in text or text.startswith("spec path resolves outside project boundary"):
        return f"没有这个项目：storyvideo/{episode_id}"
    outside = _OUTSIDE_FIELD_RE.match(text)
    if outside:
        return f"项目文件里的 {outside.group(1)} 指向数据目录之外，已拒绝读取——改成数据目录内的相对路径"
    return humanize(redact_local_paths(text))


class SpecWriteBusy(RuntimeError):
    """同一份项目文件正被另一次保存改着。"""


_SPEC_LOCKS_GUARD = threading.Lock()
_SPEC_LOCKS: dict[str, threading.Lock] = {}


@contextmanager
def spec_write_lock(path: str | Path) -> Iterator[None]:
    """一份项目文件同一时刻只允许一次「读→改→写」；拿不到锁直接抛 SpecWriteBusy（接口层转 409），不排队。

    路由是同步 def，Starlette 把它们丢进线程池并发跑：两个逐镜 PUT 各自读同一份旧文件、各写一次，后写的
    抹掉先写的字段，两边都 200。atomic_write_json 只防写半截，不防丢更新。服务是单进程，进程内锁够用；
    不排队是因为排队之后第二个请求照样基于旧内容覆盖——它该失败，让人刷新后再改。
    """

    resolved = Path(path).resolve()
    key = str(resolved).casefold() if os.name == "nt" else str(resolved)
    with _SPEC_LOCKS_GUARD:
        lock = _SPEC_LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise SpecWriteBusy(f"项目文件正在被另一次保存改着（{resolved.name}）——等它完成，刷新后再改")
    try:
        yield
    finally:
        lock.release()


def configured_image_adapter() -> str:
    """「模型设置」里生图角色当前的来源 id（空 = 没配 / 设置文件读不出来）。lint 的参考图上限按它查——
    跟能力检查接口一个口径，不再按模型 id 跨来源猜（ADP-09）。"""

    from genkit import settings as settings_mod

    try:
        return str(settings_mod.load_settings_or_default()[0].role("image").adapter or "")
    except Exception:  # noqa: BLE001 - 详情页不该因为设置读不出来而失败
        return ""


def has_unimported_legacy_artifacts(spec: Mapping[str, Any]) -> bool:
    """有本地产物（首帧/视频/成片）但账本里没有这个 run 的记录——``prepare()``

    会直接拒绝这种状态（怕把已经生成、可能已经花过钱的东西当成没生成过），
    要求先显式 ``import-legacy``。这里镜像同一个判断条件，好让界面在人点
    「锁定」之前就说清楚，不是等报错了才告诉他。
    """

    try:
        with StoryVideoRunner(str(PROJECT_ROOT)) as runner:
            runner.status(spec["id"])
        return False  # 账本里已经有记录，不是这种情况
    except RUNNER_ERRORS:
        pass
    except Exception:  # noqa: BLE001 - 探测用途，任何异常都当「还没有账本记录」处理
        pass

    return local_artifacts_exist(spec)


def uses_legacy_shot_format(raw_spec: dict[str, Any]) -> bool:
    """Legacy specs put ``speaker``/``en``/``zh`` directly on the shot instead
    of a ``lines`` array (``spec.py`` still parses this for reading). Our edit
    endpoint only knows how to write the modern ``lines`` shape; writing it back
    onto a legacy shot leaves both the old flat fields AND a new empty-ish
    ``lines`` array sitting next to each other — a real corruption, so legacy
    shots stay read-only.
    """

    for shot in raw_spec.get("shots", ()):
        if isinstance(shot, dict) and "lines" not in shot and (
            "en" in shot or "zh" in shot or "speaker" in shot
        ):
            return True
    return False


def qc_decision(spec: Mapping[str, Any], gate: str, shot_id: str) -> str | None:
    from storyvideo.qc import load_qc

    qc_path = Path(spec["output_root"]) / "manifests" / "qc.json"
    if not qc_path.is_file():
        return None
    qc = load_qc(qc_path)
    item = (qc.get("gates", {}).get(gate, {}).get("items", {}) or {}).get(shot_id)
    if not item:
        return None
    decision = item.get("decision")
    # 跟 runner 同一口径（runtime._technical_qc_current）：作废（stale）或没过技术检查的「通过」不算通过——
    # 作废时 qc.py 刻意保留 decision 作审计上下文，界面只读 decision 就会把已作废的「通过」当通过，
    # 概览跳过审片、runner 却拦着不放行。重拍 / 改文字 / 重设计这些否定裁决不受影响：记重拍本身就会把这一项
    # 作废（产物待重出），它仍然是重拍。上下文（stale 标记、上次原因）走 qc_meta，这里只回答「算不算通过」
    if decision in ("pass", "waive") and (item.get("stale") is True or item.get("technical_passed") is False):
        return None
    return decision


def qc_meta(spec: Mapping[str, Any], gate: str, shot_id: str) -> dict[str, Any] | None:
    """这一镜在某道闸的裁决上下文（缺陷/处置/原因/历史，DESIGN §24）；没审过或没 qc.json 就是 None。"""

    from storyvideo.qc import QCError, item_meta, load_qc

    qc_path = Path(spec["output_root"]) / "manifests" / "qc.json"
    if not qc_path.is_file():
        return None
    try:
        return item_meta(load_qc(qc_path), gate, shot_id)
    except QCError:
        return None


def asr_rows(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """上次台词识别闸的逐镜结果（manifests/asr_qc.json），按 shot id 索引；没跑过就是空。"""

    path = Path(spec["output_root"]) / "manifests" / "asr_qc.json"
    if not path.is_file():
        return {}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(r.get("shot_id")): r for r in report.get("rows", ()) if isinstance(r, dict)}


def shot_links(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """每一镜的反向引用：谁把它当参考镜、挂了几句旁白、后面接了哪些插卡、几条画面文字、几段静音区间。
    一次算完整集，避免逐镜重复扫描。"""

    shots = [s for s in spec.get("shots", ()) if isinstance(s, dict)]
    links: dict[str, dict[str, Any]] = {str(s["id"]): _empty_links() for s in shots}
    for s in shots:
        ref = s.get("ref_frame")
        if ref and str(ref) in links:
            links[str(ref)]["referenced_by"].append(str(s["id"]))
    for line in (spec.get("voice_over") or {}).get("lines") or ():
        if isinstance(line, Mapping) and str(line.get("shot")) in links:
            links[str(line.get("shot"))]["voice_over"].append({"at": line.get("at"), "zh": line.get("zh")})
    pg = spec.get("post_graphics") or {}
    for insert in pg.get("phone_ui_inserts") or ():
        if isinstance(insert, Mapping) and str(insert.get("after")) in links:
            links[str(insert.get("after"))]["inserts_after"].append(str(insert.get("id")))
    for note in pg.get("notebook_text") or ():
        if isinstance(note, Mapping) and str(note.get("shot")) in links:
            links[str(note.get("shot"))]["notebook_text"].append(
                {"text": note.get("text") or "", "at": note.get("at"), "y": note.get("y"), "size": note.get("size")}
            )
    # 绝对静音区间（post_graphics.silence）：at 相对这一镜在后期时间轴上的起点
    for mute in pg.get("silence") if isinstance(pg.get("silence"), list) else ():
        if isinstance(mute, Mapping) and str(mute.get("shot")) in links:
            links[str(mute.get("shot"))]["silence"].append(
                {"at": mute.get("at"), "dur": mute.get("dur"), "label": mute.get("label") or ""}
            )
    return links


def _empty_links() -> dict[str, list[Any]]:
    return {"referenced_by": [], "voice_over": [], "inserts_after": [], "notebook_text": [], "silence": []}


def shot_detail(
    spec: dict[str, Any], shot: dict[str, Any], *, pipeline: str = "storyvideo",
    asr: Mapping[str, Mapping[str, Any]] | None = None,
    links: Mapping[str, Mapping[str, Any]] | None = None,
    jobs: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """``pipeline`` 只影响 frame_url/video_url 指向哪条产线的 artifact 端点——

    ``jobs`` 是 ``generate.job_records(spec)`` 的结果（整集一次读账本）；不传就为这一镜单独读。

    URL 里的产线段不能硬编码成 ``storyvideo``：别的产线的项目一旦真的生成过首帧/视频，
    这两个 URL 就会指向 ``content/story_video/`` 下根本不存在的 id，图直接 404。
    """
    from .generate import shot_attempts

    shot_id = shot["id"]
    output_root = Path(spec["output_root"])
    has_frame = (output_root / "01_frames" / f"{shot_id}.png").is_file()
    has_video = (output_root / "02_video" / f"{shot_id}.mp4").is_file()
    episode_id = spec.get("id")
    attempts = shot_attempts(spec, shot_id, jobs=jobs) if pipeline == "storyvideo" else None
    frame_meta = qc_meta(spec, "frames", shot_id) if has_frame else None
    video_meta = qc_meta(spec, "videos", shot_id) if has_video else None
    if attempts is not None:
        # 裁决上下文里带上「到目前为止提交过几次」，审片人一眼看到这是第几次
        for meta, stage in ((frame_meta, "frames"), (video_meta, "videos")):
            if meta is not None:
                meta["attempts"] = dict(attempts[stage])
    return {
        "id": shot_id,
        "dur": shot.get("dur"),
        "cast": shot.get("cast") or [],
        "ref_frame": shot.get("ref_frame"),
        "camera_move": shot.get("camera_move") or "",
        "camera_combo": shot.get("camera_combo") or "",
        # 镜头编辑器（DESIGN §11）直接编辑这些字段，原样带出
        "shot": shot.get("shot") or "",
        "art": shot.get("art") or "",
        "motion": shot.get("motion") or "",
        "sfx": shot.get("sfx") or "",
        "entry_state": shot.get("entry_state") or "",
        "exit_state": shot.get("exit_state") or "",
        "speech_mode": shot.get("speech_mode") or "",
        "gaze": shot.get("gaze") or "",
        "purpose": shot.get("purpose") or "",
        # 关系场景类型（表白 / 重逢 / 原谅 / 拒绝 / 分手）：可选，编辑器原样带出，只给 lint 看
        "scene_type": shot.get("scene_type") or "",
        # 画面文字政策（DESIGN §20）：画面里必须可读的文字，编辑器原样带出
        "readable_text": [dict(x) for x in (shot.get("readable_text") or []) if isinstance(x, Mapping)],
        # 结构化起止边界与轴线（DESIGN §22）：没写 = null，编辑器据此决定默认用结构化还是自由文本
        "entry": dict(shot["entry"]) if isinstance(shot.get("entry"), Mapping) else None,
        "exit": dict(shot["exit"]) if isinstance(shot.get("exit"), Mapping) else None,
        "axis": dict(shot["axis"]) if isinstance(shot.get("axis"), Mapping) else None,
        "boundary_change": shot.get("boundary_change") or "",
        # 观众可见性 / 揭示保护（DESIGN §28）：先不揭示的事实，编辑器原样带出
        "withhold": [dict(x) for x in (shot.get("withhold") or []) if isinstance(x, Mapping)],
        # 参考槽位用途 + 尾帧续接（DESIGN §30）：ref_tail 归一化成 {shot, mode}；reference_slots 是本镜首帧请求
        # 实际会带的参考图（顺序 = 请求顺序），编辑器据此列 target 下拉
        "ref_tail": dict(shot["ref_tail"]) if isinstance(shot.get("ref_tail"), Mapping) else None,
        "reference_roles": [dict(x) for x in (shot.get("reference_roles") or []) if isinstance(x, Mapping)],
        "reference_slots": reference_slot_plan(spec, shot_id),
        "lines": [
            {
                "who": line.get("who", ""),
                "zh": line.get("zh") or line.get("en") or "",
                "tone": line.get("tone") or "",
                "subtitle_start_seconds": line.get("subtitle_start_seconds"),
                "subtitle_end_seconds": line.get("subtitle_end_seconds"),
            }
            for line in shot.get("lines") or []
        ],
        "budget": shot_duration_budget(shot),
        "frame_prompt": compile_frame_prompt(spec, shot_id),
        "neg_prompt": compile_negative_prompt(spec, shot_id),
        "video_prompt": compile_video_prompt(spec, shot_id),
        "frame_url": (
            f"/api/episodes/{pipeline}/{episode_id}/artifact/frame/{shot_id}"
            if has_frame else None
        ),
        "video_url": (
            f"/api/episodes/{pipeline}/{episode_id}/artifact/video/{shot_id}"
            if has_video else None
        ),
        "frame_qc_decision": qc_decision(spec, "frames", shot_id) if has_frame else None,
        "video_qc_decision": qc_decision(spec, "videos", shot_id) if has_video else None,
        # 重拍处置与重投计数（DESIGN §24）：每道闸的缺陷/处置/原因/历史，以及首帧/视频各提交过几次
        "qc_meta": {"frames": frame_meta, "videos": video_meta},
        "attempts": attempts,
        "asr": (
            {k: (asr or {})[shot_id].get(k) for k in ("status", "cer", "expected", "heard")}
            if asr and shot_id in asr and has_video else None
        ),
        "links": dict((links or {}).get(shot_id) or _empty_links()),
    }


def cover_url(spec: Mapping[str, Any], frames_done: list[str] | None = None) -> str | None:
    """侧栏 / 概览的封面：有海报（DESIGN §36）用海报，否则第一张已生成的首帧，都没有 = None。"""

    from storyvideo.poster import latest as latest_poster

    episode_id = spec.get("id")
    entry = latest_poster(spec)
    if entry:
        return f"/api/episodes/storyvideo/{episode_id}/poster/file/{entry['poster']}"
    if frames_done is None:
        root = Path(str(spec["output_root"]))
        frames_done = [str(s["id"]) for s in spec.get("shots", ()) if isinstance(s, dict)
                       and (root / "01_frames" / f"{s['id']}.png").is_file()]
    return f"/api/episodes/storyvideo/{episode_id}/artifact/frame/{frames_done[0]}" if frames_done else None


def post_output_intact(output_root: Path, post: Path) -> bool:
    """<id>_post.mp4 是不是 manifests/post.json 描述的那一份成片：清单记了 size_bytes 时按字节数核对。

    后期合成现在先写临时文件、成功后才替换，所以正常情况下两者一定一致；这里防的是改成原子替换之前留下的残片
    （ffmpeg 被杀 / OOM 时 -y 把上一版好的成片截成几十字节，post.json 却还写着 15.8s）——不能把它当交付物给界面和导出。
    没有清单（老项目 / 手放的文件）或清单没记字节数时不判，照旧当成片。"""

    manifest_path = output_root / "manifests" / "post.json"
    if not manifest_path.is_file():
        return True
    try:
        manifest = read_json_bom(manifest_path)
    except (OSError, ValueError):
        return True
    if not isinstance(manifest, dict):
        return True
    recorded = manifest.get("size_bytes")
    output = str(manifest.get("output") or "")
    if not isinstance(recorded, int) or isinstance(recorded, bool) or recorded <= 0 or not output:
        return True
    if Path(output).name != post.name:
        return True
    try:
        return post.stat().st_size == recorded
    except OSError:
        return False


def _deliverable_path(spec) -> tuple[Path, str]:
    """成片路径与种类：post（后期合成版）优先于 final（装配版）——但只在它是清单描述的那一份完整成片时。"""

    root = Path(spec["output_root"])
    post = root / f"{spec.get('id')}_post.mp4"
    if post.is_file() and post_output_intact(root, post):
        return post, "post"
    return root / f"{spec.get('id')}_final.mp4", "final"


def episode_detail(
    spec: dict[str, Any], *, editable: bool, pipeline: str = "storyvideo"
) -> dict[str, Any]:
    freeze = freeze_state(spec)
    locked = bool(freeze["locked"])
    warnings = check_duration_budget(spec) + validate_camera_usage(spec) + check_dialogue_clarity(spec) + check_continuity_rules(spec)
    locked = is_locked(spec["output_root"])
    warnings = (check_duration_budget(spec) + validate_camera_usage(spec) + check_dialogue_clarity(spec)
                + check_continuity_rules(spec, image_adapter=configured_image_adapter()))
    # 镜头改短后超出新时长的静音区间：镜头编辑的响应界面不显示，放进问题卡才看得到（不改的话后期块保存会被拦）
    warnings = warnings + silence_overrun_warnings(spec)
    # 项目文件 / 账本 / 冻结快照对不上（外部改过文件、输出目录被清空）：先在这里说，别等每个动作各报一次 400
    if freeze["mismatch"]:
        warnings = warnings + [f"全集：{FREEZE_MISMATCH_HINTS[freeze['mismatch']]}"]
    # 按题材审稿（DESIGN §49）与剧本工艺审稿：只有核对过引文、且没过时的 risk / violated 条目进问题卡；审稿文件坏了不拦详情页
    try:
        from storyvideo.genre_review import review_problems

        warnings = warnings + review_problems(spec)
    except Exception:  # noqa: BLE001
        pass
    try:
        from storyvideo.craft_review import review_problems as craft_review_problems

        warnings = warnings + craft_review_problems(spec)
    except Exception:  # noqa: BLE001
        pass
    # 成片：装配后落在 <output_root>/<id>_final.mp4。界面之前只展示每镜的首帧/视频，成片没有入口，
    # 用户装配完不知道去哪看、去哪下（2026-09-06）。final 闸门的 qc item 键固定是 "final"。
    # 2026-09-07：有后期层的项目（storyvideo post）交付物是 <id>_post.mp4（旁白/插卡/画面文字已叠），
    # 存在时优先展示它；没有就退回装配版 <id>_final.mp4。
    final_path, final_kind = _deliverable_path(spec)
    final_exists = final_path.is_file()
    asr = asr_rows(spec)
    links = shot_links(spec)
    vo = spec.get("voice_over") or {}
    pg = spec.get("post_graphics") or {}
    from .generate import job_records

    jobs = job_records(spec) if pipeline == "storyvideo" and locked else {}
    return {
        "id": spec.get("id"),
        "module": module_for_pipeline(pipeline),   # 属于哪个功能模块（DESIGN §38）；前端按它分组
        "title": spec.get("title_zh") or spec.get("title_en") or "",
        "logline": spec.get("logline") or "",
        "locked": locked,
        "editable": editable and not locked,
        # 冻结三方核对（账本 run / 快照 / 项目文件）：mismatch 非空时界面别再引导「开始制作 / 生成」
        "freeze": freeze,
        # 账本说开始过、输出目录却不在：锁定但没法继续，readonly_reason 说清为什么（路由层对旧格式另有一句，会覆盖）
        "readonly_reason": FREEZE_MISMATCH_HINTS["snapshot_missing"] if freeze["mismatch"] == "snapshot_missing" else None,
        "camera_profile": spec.get("camera_profile") or "",
        # 剧本层（DESIGN §32）：项目页的「剧本」标签据此显示 ✓ 还是空状态
        "has_screenplay": screenplay_path(str(spec.get("id") or "")).is_file(),
        "video_params": dict(spec.get("video_params") or {}),
        "warnings": warnings,
        "problems": problem_cards(warnings),   # 结构化问题卡：在哪、影响、怎么做（Overview 分镜分组显示）
        # 单集契约与系列（DESIGN §32）：没写 = null，概览页据此显示空卡
        "episode_contract": dict(spec["episode_contract"]) if isinstance(spec.get("episode_contract"), Mapping) else None,
        "series": dict(spec["series"]) if isinstance(spec.get("series"), Mapping) else None,
        "final_url": f"/api/episodes/{pipeline}/{spec.get('id')}/artifact/final/final" if final_exists else None,
        "final_path": str(final_path.relative_to(PROJECT_ROOT)) if final_exists and final_path.is_relative_to(PROJECT_ROOT) else (str(final_path) if final_exists else None),
        "final_size_bytes": final_path.stat().st_size if final_exists else None,
        "final_qc_decision": qc_decision(spec, "final", "final") if final_exists else None,
        "final_kind": final_kind if final_exists else None,
        "has_post_layers": bool(vo.get("lines") or pg.get("phone_ui_inserts") or pg.get("notebook_text") or pg.get("labels")),
        "asr_summary": (
            {"pass": sum(1 for r in asr.values() if r.get("status") == "pass"),
             "fail": sum(1 for r in asr.values() if r.get("status") in ("fail", "silent")),
             "review": sum(1 for r in asr.values() if r.get("status") == "review"),
             "stray": sum(1 for r in asr.values() if r.get("status") == "stray")}
            if asr else None
        ),
        "shots": [
            shot_detail(spec, shot, pipeline=pipeline, asr=asr, links=links, jobs=jobs)
            for shot in spec.get("shots", []) if isinstance(shot, dict)
        ],
    }


def list_dir(directory: Path, pipeline: str, loader) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not directory.is_dir():
        return items
    for path in sorted(directory.glob("*.json")):
        if is_screenplay_file(path):
            continue   # 两步起草的剧本文件不是项目
        entry: dict[str, Any] = {
            "id": path.stem,
            "pipeline": pipeline,
            "module": module_for_pipeline(pipeline),   # 功能模块标签（DESIGN §38）
            "path": str(path.relative_to(PROJECT_ROOT)),
        }
        try:
            spec = loader(str(path), project_root=PROJECT_ROOT)
        except Exception as exc:  # noqa: BLE001 - listing must survive one bad file
            entry["status"] = "error"
            # 跟详情页同一句人话；原文里的本机绝对路径（用户名、数据根）不往界面上带
            entry["error"] = (spec_not_found_detail(exc, path.stem) if pipeline == "storyvideo"
                              else redact_local_paths(str(exc)))
            items.append(entry)
            continue
        output_root = spec["output_root"]
        entry["shot_count"] = len(spec.get("shots", ()))
        entry["locked"] = is_locked(output_root)
        entry["status"] = "ok"
        if pipeline == "storyvideo":
            # 侧栏要能认出项目：标题、封面（第一张已生成的首帧）、两道进度（列表不做 QC 判断，只数文件）
            root = Path(output_root)
            ids = [str(s["id"]) for s in spec.get("shots", ()) if isinstance(s, dict)]
            frames_done = [sid for sid in ids if (root / "01_frames" / f"{sid}.png").is_file()]
            videos_done = [sid for sid in ids if (root / "02_video" / f"{sid}.mp4").is_file()]
            final_path, final_kind = _deliverable_path(spec)
            entry["title"] = spec.get("title_zh") or spec.get("title_en") or ""
            entry["cover_url"] = cover_url(spec, frames_done)
            entry["progress"] = {
                "frames": len(frames_done), "videos": len(videos_done), "total": len(ids),
                "final": final_kind if final_path.is_file() else None,
            }
        items.append(entry)
    return items


def resolve_storyvideo_path(episode_id: str) -> tuple[Path, Path]:
    from storyvideo.spec import resolve_spec_path

    return resolve_spec_path(episode_id, project_root=PROJECT_ROOT)


def merge_shot_edit(
    target: dict[str, Any],
    *,
    lines: list[dict[str, Any]] | None,
    camera_move: str | None,
    camera_combo: str | None,
    dur: float | None,
) -> dict[str, Any]:
    """把「界面上实际编辑过的字段」合并进一份镜次数据，就地改 ``target`` 并
    返回它——已保存项目的逐镜编辑（``routers/episodes.py``）和还没保存的
    草稿逐镜编辑（``routers/scripts.py``）背后是同一个合并
    规则，抽成共享函数，不让两处各写一遍、以后改一处忘了改另一处。

    按下标跟原台词合并，只覆盖界面实际展示/编辑过的字段（who/zh/timing）。
    界面只显示中文，不merge会把双语项目的 en 字段静默冲掉——之前直接整体
    替换时踩过：一份要求 en+zh 都在的 spec，保存后 en 消失导致校验直接拒绝，
    但如果校验规则恰好只要求 zh，这个丢失不会报错，会真的丢数据。
    """
    if lines is not None:
        original_lines = target.get("lines") or []
        merged = []
        for i, incoming in enumerate(lines):
            base = dict(original_lines[i]) if i < len(original_lines) else {}
            base["who"] = incoming.get("who", base.get("who", ""))
            base["zh"] = incoming.get("zh", base.get("zh", ""))
            if "tone" in incoming:  # 语气/音量/语速；空串=清掉
                if str(incoming.get("tone") or "").strip():
                    base["tone"] = str(incoming["tone"]).strip()
                else:
                    base.pop("tone", None)
            if incoming.get("subtitle_start_seconds") is not None:
                base["subtitle_start_seconds"] = incoming["subtitle_start_seconds"]
            if incoming.get("subtitle_end_seconds") is not None:
                base["subtitle_end_seconds"] = incoming["subtitle_end_seconds"]
            merged.append(base)
        target["lines"] = merged
    if camera_move is not None:
        target["camera_move"] = camera_move or None
        if camera_move:
            target.pop("camera_combo", None)
    if camera_combo is not None:
        target["camera_combo"] = camera_combo or None
        if camera_combo:
            target.pop("camera_move", None)
    if dur is not None:
        target["dur"] = dur
    # None 值不留在 JSON 里（未声明字段就是没有，不是显式 null）。
    for key in ("camera_move", "camera_combo"):
        if target.get(key) is None:
            target.pop(key, None)
    return target


def read_json_bom(path: Path) -> dict[str, Any]:
    """项目文件历史上有些带 UTF-8 BOM——好几个端点都要「读原始 JSON 再改」，
    统一在这里处理，别每处各自记一遍 ``encoding="utf-8-sig"``。
    """
    return json.loads(path.read_text(encoding="utf-8-sig"))
