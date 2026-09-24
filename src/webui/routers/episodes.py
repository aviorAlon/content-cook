"""项目（episode）读取/编辑/生成——拆分自 app.py（见 deps.py 头部注释）。

覆盖 storyvideo 的读取/逐镜编辑/锁定/导入旧产物/预估+生成。真正会花钱的只有 ``generate_storyvideo`` 一个端点，且要求
plan_token 二次确认（见该函数的 docstring）。
"""

from __future__ import annotations

import copy
import json
import logging
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from genkit import trash
from genkit.ids import is_valid_id
from genkit.router import build_provider
from genkit.store import RunStore, StoreError
from pydantic import BaseModel

from storyvideo.post_layers import silence_overrun_warnings
from storyvideo.preview import check_duration_budget
from storyvideo.prompts import CameraError, camera_library, find_camera_profile, validate_camera_usage
from storyvideo.runtime import (
    StoryVideoBlocked,
    StoryVideoGatePending,
    StoryVideoRetryLimit,
    StoryVideoRunner,
    ledger_path,
)
from storyvideo.spec import SpecError
from storyvideo.spec import load_spec as load_storyvideo_spec
from storyvideo.spec import normalize_spec as normalize_storyvideo_spec
from storyvideo.spec import stable_json_hash
from storyvideo.state import atomic_write_json, read_json

from ..humanize import humanize
from ..deps import (
    FREEZE_MISMATCH_HINTS,
    PROJECT_ROOT,
    RUNNER_ERRORS,
    STORYVIDEO_DIR,
    SpecWriteBusy,
    episode_detail,
    freeze_state,
    has_unimported_legacy_artifacts,
    is_locked,
    is_screenplay_file,
    ledger_run,
    list_dir,
    _deliverable_path,
    merge_shot_edit,
    read_json_bom,
    require_roles,
    require_text_model,
    resolve_storyvideo_path,
    screenplay_path,
    shot_detail,
    spec_not_found_detail,
    spec_write_lock,
    uses_legacy_shot_format,
)
from ..generate import ensure_watcher, estimate_plan, inflight_shot_ids

router = APIRouter()
log = logging.getLogger(__name__)

# 「删除项目」是归档，不是真删——跟 job 记录/草稿版本历史同一个原则：已经写过
# 创作内容、可能已经花过钱生成过产物的东西，不能因为一次误点就从磁盘上消失。
# 挪到这个目录（已加入 .gitignore），不再出现在项目列表里，但整份 spec + 生成
# 产物都还在，找回来走回收站（routers/trash.py：POST /api/deleted/storyvideo/<归档目录名>/restore → restore_archive）。
DELETED_DIR = PROJECT_ROOT / trash.DELETED_DIRNAME
# 归档目录名：<id>__<UTC 时间戳>（同一秒撞名时再加 -序号）；恢复接口只认这个形状（不带路径分隔符，防止 .. 穿越）
_ARCHIVE_ENTRY_RE = trash.ENTRY_RE
_LEDGER_ARCHIVE_NAME = "ledger.json"


def _episode_assets_dir(episode_id: str) -> Path:
    """角色锚图目录（routers/project.py 的 _store_asset 往这里写）。"""

    return PROJECT_ROOT / "content" / "assets" / episode_id


def _own_output_root(value: Any, episode_id: str) -> Path | None:
    """项目文件里的 output_root 只在「落在数据目录里、以项目 id 结尾」时才认作这个项目自己的输出目录——
    跟 normalize_spec 同一条规则（_resolve_within + ``output_root.name == id``）。

    删除 / 恢复会整个搬动这个目录：不守这条规则的话，一份从别的数据根拷来的项目文件（列表里已经标「解析失败」）
    点一下删除，就把数据根外面那个目录（别人花钱出的片子）整个搬进归档，恢复时再原样写回外面；写成
    ``output/story_video`` / ``content`` 这种共享目录的，会把别的项目的产物一起搬走（2026-09-23 发布前自查 PB-2）。"""

    if not isinstance(value, str) or not value.strip() or value.strip().startswith("~"):
        return None
    root = PROJECT_ROOT.resolve()
    raw = Path(value.strip())
    try:
        candidate = (raw if raw.is_absolute() else root / raw).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    if candidate == root or candidate.name != episode_id or not candidate.is_relative_to(root):
        return None
    if candidate.is_relative_to(DELETED_DIR.resolve()):
        return None
    return candidate


def _strings(value: Any) -> Iterator[str]:
    """JSON 里所有字符串值（递归）。"""

    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _asset_dir_users(episode_id: str) -> list[str]:
    """还引用着 ``content/assets/<episode_id>/`` 里文件的其它短剧项目（按项目文件名）。

    「复制为新版本」（revise / revise-edit）不复制锚图，新版本的成员 / 变体 asset 仍写着 content/assets/<旧id>/…：
    删旧版本时把这个目录整个搬进归档，新版本的锚图就跟着没了——草稿态的新版本出首帧时没有锚图参考（花钱、人物漂），
    已开始制作的新版本 plan 变了，prepare / review 一律「already has different inputs」。所以有人引用就留在原地。
    整份 JSON 的字符串都扫（成员、变体、配乐路径…），不只看 cast.*.asset：漏掉一处就又是一个悬空引用。
    """

    marker = f"content/assets/{episode_id}/"
    users: list[str] = []
    for path in sorted(STORYVIDEO_DIR.glob("*.json")):
        if path.stem == episode_id or is_screenplay_file(path):
            continue
        try:
            raw = read_json_bom(path)
        except Exception:  # noqa: BLE001 - 读不出来的项目文件不挡删除（它本来也读不到锚图）
            continue
        if any(marker in text.replace("\\", "/") for text in _strings(raw)):
            users.append(path.stem)
    return users


def _ledger_id_taken(episode_id: str) -> str | None:
    """账本里还有这个 id 的 run 时给出 409 的文案（建项目 / 复制新版本前查一遍）；没有返回 None。

    正常归档会把账本行一起搬走，所以只有「项目文件被手动删了、账本行还在」这种状态会撞上——
    与其让人建出来之后在 prepare 上撞「内容被改过」，不如在这里就说清楚。
    """

    if ledger_run(episode_id) is None:
        return None
    return f"这个 id 在账本里还有旧的制作记录（{episode_id}）——换一个 id，或先从归档把它恢复回来"


@router.get("/api/episodes")
def list_episodes() -> list[dict[str, Any]]:
    return (
        list_dir(STORYVIDEO_DIR, "storyvideo", load_storyvideo_spec)
    )


@router.delete("/api/episodes/{pipeline}/{episode_id}")
def delete_episode(pipeline: str, episode_id: str) -> dict[str, Any]:
    """归档到 ``.webui_deleted/``，不调用任何付费接口，也不是 ``os.remove``。

    生成产物（``output_root`` 指向的目录，包含真花钱换来的首帧/视频）一并
    搬过去，不是只挪 spec 文件——那些产物比 spec 本身更值钱，不能落下。
    没有生成过任何东西（草稿阶段就删）的项目自然没有 output_root 目录可搬，
    只归档 spec，正常。
    """
    if pipeline != "storyvideo":
        raise HTTPException(404, f"unknown pipeline: {pipeline}")
    # id 先过合法性检查（别的接口要么查 is_valid_id、要么走 resolve_spec_path 的边界）：Windows 上 URL 里解码出来的
    # 反斜杠是路径分隔符，「..\..\x」会让下面四处按 id 拼的路径（项目文件、归档目录、剧本、锚图目录）走出数据目录
    if not is_valid_id(episode_id):
        raise HTTPException(404, f"没有这个项目：{pipeline}/{episode_id}")
    content_dir = STORYVIDEO_DIR
    spec_path = content_dir / f"{episode_id}.json"
    if not spec_path.is_file():
        raise HTTPException(404, f"没有这个项目：{pipeline}/{episode_id}")

    output_root: Path | None = None
    output_rejected = ""   # 项目文件写了 output_root、但不是这个项目自己的输出目录：原值（给提示用）
    title = ""
    try:
        raw = read_json_bom(spec_path)
        title = trash.title_of(raw)
        root_str = raw.get("output_root")
        output_root = _own_output_root(root_str, episode_id)
        if output_root is None and isinstance(root_str, str) and root_str.strip():
            output_rejected = root_str.strip()
    except Exception:  # noqa: BLE001 - 读不出 output_root 不该拦住删除本身
        output_root = None

    deleted_at = datetime.now(timezone.utc)
    stamp = deleted_at.strftime("%Y%m%dT%H%M%SZ")
    dest = DELETED_DIR / pipeline / f"{episode_id}__{stamp}"
    # 同一秒内同 id 归档两次（删 → 重建 → 再删）会撞同一个目录名：第二份的 spec 覆盖第一份、output 被搬进
    # 第一份的 output 里，两份归档混在一起谁都恢复不了。撞名就加序号
    serial = 1
    while dest.exists():
        serial += 1
        dest = DELETED_DIR / pipeline / f"{episode_id}__{stamp}-{serial}"
    dest.mkdir(parents=True)
    shutil.move(str(spec_path), str(dest / "spec.json"))
    # 两步起草的剧本（<id>.screenplay.json）与角色锚图目录（content/assets/<id>/）也是这个项目的内容：
    # 留在原地的话，同 id 新建的项目会「继承」上一个项目的剧本（has_screenplay=true、剧本页显示旧内容）
    screenplay_moved = False
    sp = screenplay_path(episode_id)
    if sp.is_file():
        shutil.move(str(sp), str(dest / "screenplay.json"))
        screenplay_moved = True
    assets_moved = False
    assets_kept_for: list[str] = []
    assets_dir = _episode_assets_dir(episode_id)
    if assets_dir.is_dir():
        # 别的项目（复制出来的新版本）还引用着这个目录：留在原地，不然删旧版本等于把新版本的锚图也删了。
        # 归档里没有 assets 的条目恢复时照常处理（目录本来就在原位）
        assets_kept_for = _asset_dir_users(episode_id)
        if not assets_kept_for:
            shutil.move(str(assets_dir), str(dest / "assets"))
            assets_moved = True
    output_moved = False
    if output_root is not None and output_root.is_dir():
        shutil.move(str(output_root), str(dest / "output"))
        output_moved = True
    # 账本里这个 id 的 run / jobs 一并搬进归档（dest/ledger.json）：行留在账本里，同 id 重建的项目永远开不了工
    # （prepare 拿旧 run 比快照 / hash 必然对不上）。放在文件都搬完之后：账本这一步失败不回滚文件，
    # 返回 ledger_archived=false，恢复接口对「账本行还在」的归档同样能处理
    ledger_archived = False
    ledger_error: str | None = None
    store_path = ledger_path(PROJECT_ROOT)
    if store_path.is_file():
        try:
            with RunStore(store_path) as store:
                record = store.archive_run(episode_id)
            if record is not None:
                atomic_write_json(dest / _LEDGER_ARCHIVE_NAME, record)
                ledger_archived = True
        except Exception as exc:  # noqa: BLE001 - 账本打不开 / id 形状不合法：文件已归档，别把整个请求变成 500
            log.exception("archive ledger rows for %s", episode_id)
            ledger_error = f"账本记录没能一起归档：{humanize(exc)}"
    # 回收站页的清单（原路径 → 归档里的名字、删除时间、标题）：写不出来不算删除失败，没有清单的归档照样能列出、恢复
    items: list[tuple[Path, str]] = [(spec_path, "spec.json")]
    if screenplay_moved:
        items.append((sp, "screenplay.json"))
    if assets_moved:
        items.append((assets_dir, "assets"))
    if output_moved and output_root is not None:
        items.append((output_root, "output"))
    if ledger_archived:
        items.append((store_path, _LEDGER_ARCHIVE_NAME))
    kept = [{"path": trash.relative(assets_dir, PROJECT_ROOT), "used_by": assets_kept_for}] if assets_kept_for else []
    try:
        trash.write_manifest(dest, project_root=PROJECT_ROOT, module=pipeline, project_id=episode_id, title=title,
                             deleted_at=deleted_at, items=items, kept=kept)
    except OSError:
        log.exception("write trash manifest for %s", episode_id)
    result: dict[str, Any] = {
        "archived_to": str(dest.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "output_moved": output_moved,
        "screenplay_moved": screenplay_moved,
        "assets_moved": assets_moved,
        # 锚图目录没搬的原因：这些项目还引用着它（空表 = 没有锚图目录，或已经搬进归档）
        "assets_kept_for": assets_kept_for,
        "ledger_archived": ledger_archived,
    }
    warnings: list[str] = []
    if output_rejected:
        warnings.append(f"项目文件里的输出目录（{output_rejected}）不在数据目录里、或不是以项目 id 结尾，没有搬动它，"
                        "留在原处；只归档了项目文件")
    if ledger_error:
        warnings.append(ledger_error)
    if warnings:
        result["warning"] = "；".join(warnings)
    return result


def restore_archive(entry: str) -> dict[str, Any]:
    """把一条短剧归档搬回原位：spec.json → content/story_video/<id>.json、output → 项目文件里的 output_root、
    剧本 / 锚图 / 账本行各归各位。同 id 的项目（或它的任何一部分）已经存在 → 409，什么都不动。

    回收站接口（routers/trash.py 的 POST /api/deleted/storyvideo/<归档目录名>/restore）按模块分派到这里；
    其它模块走 ``genkit.trash.restore``。"""

    module = "storyvideo"
    if not _ARCHIVE_ENTRY_RE.fullmatch(entry):
        raise HTTPException(404, f"没有这条归档：{module}/{entry}")
    source = DELETED_DIR / module / entry
    if source.is_dir() and not (source / "spec.json").is_file() and (source / _LEDGER_ARCHIVE_NAME).is_file():
        return _restore_ledger_only(entry, source)
    if not source.is_dir() or not (source / "spec.json").is_file():
        raise HTTPException(404, f"没有这条归档：{module}/{entry}")
    # 项目文件本身是坏的（delete_episode 读不出来也照删）：按归档目录名里的 id 原样放回去，跟删除前一样在列表里标成出错，
    # 人能拿回文件自己修（同 genkit.trash.restore）。输出目录删除时就没搬（读不出 output_root），这里退回默认位置
    try:
        raw = read_json_bom(source / "spec.json")
    except Exception:  # noqa: BLE001
        raw = None
    spec_broken = not isinstance(raw, Mapping)
    if spec_broken:
        raw = {}
    episode_id = str(raw.get("id") or "").strip() or entry.rpartition("__")[0]
    if not is_valid_id(episode_id):
        raise HTTPException(400, f"归档里的项目 id 不合法：{episode_id}")
    root_str = str(raw.get("output_root") or f"output/story_video/{episode_id}")
    # 输出目录跟删除同一条规则（见 _own_output_root）：归档里的 spec.json 把它写到数据目录外 / 不以 id 结尾时，
    # 不往那里写——整体 409、什么都不动，比悄悄换个地方放更不容易出错
    output_root = _own_output_root(root_str, episode_id)
    if (source / "output").is_dir() and output_root is None:
        raise HTTPException(
            409, f"没有恢复：归档里的项目文件把输出目录写成了数据目录外、或不以项目 id 结尾的位置（{root_str}）——"
                 f"把归档里 spec.json 的 output_root 改成 output/story_video/{episode_id} 再恢复")

    # 先把每一处目标都查一遍，任何一处已被占就整体拒绝——恢复不能覆盖别人的东西，也不能只恢复一半
    blockers: list[str] = []
    spec_target = STORYVIDEO_DIR / f"{episode_id}.json"
    if spec_target.exists():
        blockers.append(f"项目 {episode_id} 已经存在")
    if (source / "screenplay.json").is_file() and screenplay_path(episode_id).exists():
        blockers.append("同 id 的剧本文件已经存在")
    if (source / "assets").is_dir() and _episode_assets_dir(episode_id).exists():
        blockers.append("同 id 的锚图目录已经存在")
    if (source / "output").is_dir() and output_root is not None and output_root.exists():
        blockers.append("同 id 的输出目录已经存在")
    if (source / _LEDGER_ARCHIVE_NAME).is_file() and ledger_run(episode_id) is not None:
        blockers.append("账本里已经有同 id 的制作记录")
    if blockers:
        # 只报第一条：拦住的原因通常是同一个（同 id 的项目还在），全列出来反而像是要一条条处理
        raise HTTPException(409, f"没有恢复：{blockers[0]}——先把现有的同 id 项目归档或改名")

    restored: dict[str, Any] = {"spec": True, "screenplay": False, "assets": False, "output": False, "ledger": False}
    STORYVIDEO_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source / "spec.json"), str(spec_target))
    if (source / "screenplay.json").is_file():
        shutil.move(str(source / "screenplay.json"), str(screenplay_path(episode_id)))
        restored["screenplay"] = True
    if (source / "assets").is_dir():
        _episode_assets_dir(episode_id).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source / "assets"), str(_episode_assets_dir(episode_id)))
        restored["assets"] = True
    if (source / "output").is_dir() and output_root is not None:
        output_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source / "output"), str(output_root))
        restored["output"] = True
    warning: str | None = None
    if (source / _LEDGER_ARCHIVE_NAME).is_file():
        try:
            payload = json.loads((source / _LEDGER_ARCHIVE_NAME).read_text(encoding="utf-8"))
            with RunStore(ledger_path(PROJECT_ROOT)) as store:
                store.restore_run(payload)
            (source / _LEDGER_ARCHIVE_NAME).unlink()
            restored["ledger"] = True
        except Exception as exc:  # noqa: BLE001 - 文件都已归位，账本这一步失败只提示：没有账本行的项目 prepare 会当成从没开始过
            log.exception("restore ledger rows for %s", episode_id)
            warning = f"文件已恢复，但账本记录没能放回：{humanize(exc)}"
    # 只剩回收站清单就连它一起删掉、删空目录；还剩东西（比如没放回的 ledger.json）就留着，回收站里还能再点恢复
    trash.finish(source)
    result: dict[str, Any] = {"id": episode_id, "restored": restored,
                              "path": str(spec_target.relative_to(PROJECT_ROOT)).replace("\\", "/"), "spec_broken": spec_broken}
    if warning:
        result["warning"] = warning
    return result


def _restore_ledger_only(entry: str, source: Path) -> dict[str, Any]:
    """归档里只剩 ledger.json：项目文件和输出目录已经被手动搬回原位（老的删除提示就是这么教的），只差账本行。

    不放回的话：快照在、账本没有 run，prepare / 审片一律「本地已有文件但账本没登记」，而这条归档又列在
    回收站里恢复不了。只在「搬回来的就是归档里这一份」时放回——项目文件的 hash 与冻结快照都得跟归档的 run
    对得上，把旧 run 接到别的内容上，之后每一步都会撞「内容被改过」；对不上就让人走「导入已有产物」重新登记。
    """

    try:
        payload = json.loads((source / _LEDGER_ARCHIVE_NAME).read_text(encoding="utf-8"))
        run = payload["run"]
        episode_id = str(run["run_id"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"归档里的账本记录读不出来：{humanize(exc)}") from exc
    if not is_valid_id(episode_id) or episode_id != entry.rpartition("__")[0]:
        raise HTTPException(400, f"归档里的项目 id 不合法：{episode_id}")
    if ledger_run(episode_id) is not None:
        raise HTTPException(409, "没有恢复：账本里已经有同 id 的制作记录——先把现有的同 id 项目归档或改名")
    moved_back = f"没有恢复：归档里只剩账本记录，项目 {episode_id} 的项目文件或输出目录还不在原位——先把它们搬回来，再恢复账本记录"
    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(409, moved_back) from exc
    snapshot = Path(str(spec["output_root"])) / "manifests" / "episode.snapshot.json"
    if not snapshot.is_file():
        raise HTTPException(409, moved_back)
    if run.get("input_hash") != spec["source_hash"] or stable_json_hash(read_json(snapshot, missing={})) != spec["source_hash"]:
        raise HTTPException(
            409, f"没有恢复：现在的项目 {episode_id} 跟归档里的制作记录对不上（不是同一份内容）——点「导入已有产物」重新登记"
        )
    try:
        with RunStore(ledger_path(PROJECT_ROOT)) as store:
            store.restore_run(payload)
    except StoreError as exc:   # 含 RunCollisionError：检查之后、放回之前同 id 又被占了
        raise HTTPException(409, f"没有恢复：{humanize(exc)}") from exc
    (source / _LEDGER_ARCHIVE_NAME).unlink()
    trash.finish(source)
    return {"id": episode_id,
            "restored": {"spec": False, "screenplay": False, "assets": False, "output": False, "ledger": True},
            "path": str((STORYVIDEO_DIR / f"{episode_id}.json").relative_to(PROJECT_ROOT)).replace("\\", "/")}


@router.get("/api/episodes/storyvideo/{episode_id}")
def get_storyvideo_episode(episode_id: str) -> dict[str, Any]:
    try:
        path, _root = resolve_storyvideo_path(episode_id)
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc

    raw = read_json_bom(path)
    legacy = uses_legacy_shot_format(raw)
    detail = episode_detail(spec, editable=not legacy)
    if legacy:
        detail["readonly_reason"] = (
            "这一集用的是旧式 speaker/en/zh 平铺字段（不是现代的 lines 数组）。"
            "这个工具只知道怎么写现代格式，写回去会在旧字段边上留一个不完整的 lines，"
            "把文件搞乱——所以锁死只读，改台词请直接编辑源 JSON。"
        )
    # 按账本判断而不是按「锁定」：快照在、账本没有 run（从归档手动搬回来、账本行丢了）时也得导入一次才能继续
    if not detail["freeze"]["run"] and has_unimported_legacy_artifacts(spec):
        detail["needs_import_legacy"] = True
    return detail


@router.get("/api/episodes/storyvideo/{episode_id}/artifact/{kind}/{shot_id}")
def get_storyvideo_artifact(episode_id: str, kind: str, shot_id: str) -> FileResponse:
    """把首帧/视频文件发给浏览器——生成按钮接上之后一直缺的那一环：点了生成，
    钱也花了，界面上却看不见产出的东西。路径按 spec 里真实存在的 shot id 核对，
    不直接信任 URL 拼出来的路径。
    """
    if kind not in ("frame", "video", "final"):
        raise HTTPException(404, "unknown artifact kind")
    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    if kind == "final":
        # 成片：shot_id 位置固定填 "final"（跟 qc.json final 闸门的 item 键一致）。
        # 带 filename 让浏览器「下载」时得到有意义的文件名，而不是 "final"。
        final, _kind = _deliverable_path(spec)   # 后期合成版优先
        if not final.is_file():
            raise HTTPException(404, "final video not assembled yet")
        return FileResponse(final, media_type="video/mp4", filename=final.name)
    known_ids = {s["id"] for s in spec.get("shots", ()) if isinstance(s, dict)}
    if shot_id not in known_ids:
        raise HTTPException(404, f"unknown shot id: {shot_id}")
    output_root = Path(spec["output_root"])
    sub, suffix = ("01_frames", ".png") if kind == "frame" else ("02_video", ".mp4")
    artifact = output_root / sub / f"{shot_id}{suffix}"
    if not artifact.is_file():
        raise HTTPException(404, "artifact not generated yet")
    media_type = "image/png" if kind == "frame" else "video/mp4"
    return FileResponse(artifact, media_type=media_type)


class ReviewRequest(BaseModel):
    gate: str
    decision: str
    shots: list[str] | None = None
    reason: str = ""
    reviewer: str = "webui"
    # 重拍处置（DESIGN §24）：缺陷枚举 + 处置五分法，都可选；老调用方不带也照常
    defect: str | None = None
    disposition: str | None = None
    # 同一份提示词已原样重提到上限时，review 返回 409；明确 force 才放行
    force: bool = False


# 处置 → 真正记进账本的裁决：保留 = 通过，后期处理 = 跳过（接受，交后期）；改文字重出 / 原样重提
# 才是真的重拍；换模型对比不进账本（走对比区），在这里被拒。
_DISPOSITION_DECISION = {"keep": "pass", "post": "waive"}


@router.post("/api/episodes/storyvideo/{episode_id}/review")
def review_storyvideo(episode_id: str, req: ReviewRequest) -> dict[str, Any]:
    """记 QC 裁决——跟 CLI 的 ``storyvideo review`` 是同一个调用，不碰生成。

    带 ``disposition`` 的重拍按处置五分法落账：``keep`` 记通过、``post`` 记跳过（都带缺陷/原因），
    ``edit`` / ``resubmit`` 记重拍；``resubmit``（或没写处置）而同一份提示词已经出过 2 次 → 409，
    带 ``force: true`` 才放行；``model`` 不落账（400，去多模型对比区）。
    """
    decision = str(req.decision or "").strip().lower()
    disposition = str(req.disposition or "").strip().lower() or None
    if decision == "retake" and disposition == "model":
        raise HTTPException(400, "换模型对比不记重拍——在镜头页「首帧对比试跑」里出候选，满意再采用")
    if decision == "retake" and disposition in _DISPOSITION_DECISION:
        decision = _DISPOSITION_DECISION[disposition]
    try:
        with StoryVideoRunner(str(PROJECT_ROOT)) as runner:
            result = runner.review(
                episode_id,
                req.gate,
                decision,
                shots=req.shots,
                reason=req.reason,
                reviewer=req.reviewer,
                defect=req.defect,
                disposition=disposition,
                force=req.force,
            )
    except StoryVideoRetryLimit as exc:
        raise HTTPException(409, {
            "kind": "retry_limit",
            "message": str(exc),
            "shot_id": exc.shot_id,
            "gate": exc.gate,
            "attempts": exc.attempts,
        }) from exc
    except RUNNER_ERRORS as exc:
        # 底层异常的原始文案是给开发者调试用的措辞，直接甩给
        # 操作台前端会让不懂内部架构的人摸不着头脑。加一句"这次在做什么"的
        # 中文前缀，原始内容原样跟在后面——不重写底层异常本身（那是跟 CLI
        # 共享的，CLI 使用者更需要看原始细节），只在 webui 边界补一层人话。
        raise HTTPException(400, f"记录审核结果失败：{humanize(exc)}") from exc
    return result


class ShotEdit(BaseModel):
    lines: list[dict[str, Any]] | None = None
    camera_move: str | None = None
    camera_combo: str | None = None
    dur: float | None = None
    # 资源为中心的镜头编辑器（DESIGN §11）：画面六项、动作、参考镜、出场角色、接缝状态、音效
    shot: str | None = None
    art: str | None = None
    motion: str | None = None
    sfx: str | None = None
    entry_state: str | None = None
    exit_state: str | None = None
    speech_mode: str | None = None
    gaze: str | None = None
    purpose: str | None = None          # 镜头功能：取值见 storyvideo.lint.PURPOSES
    scene_type: str | None = None       # 关系场景类型：取值见 storyvideo.spec.SCENE_TYPES；空串 = 不声明
    neg_extra: str | None = None
    ref_frame: str | None = None        # 空串 = 独立首帧
    cast: list[str] | None = None
    # 画面文字政策（DESIGN §20）：画面里必须可读的文字；None = 没动，[] = 清掉（回到「画面无文字」）
    readable_text: list[dict[str, Any]] | None = None
    # 结构化起止边界与轴线（DESIGN §22）：None = 没动，{} / "" = 清掉（回到自由文本 entry_state/exit_state）
    entry: dict[str, Any] | None = None
    exit: dict[str, Any] | None = None
    axis: dict[str, Any] | None = None
    boundary_change: str | None = None
    # 观众可见性 / 揭示保护（DESIGN §28）：先不揭示的事实；None = 没动，[] = 清掉
    withhold: list[dict[str, Any]] | None = None
    # 参考槽位用途 + 尾帧续接（DESIGN §30）：None = 没动；reference_roles [] = 清掉；ref_tail {} / "" = 清掉
    reference_roles: list[dict[str, Any]] | None = None
    ref_tail: dict[str, Any] | str | None = None


_AXIS_KEYS = ("anchors", "side", "screen", "break")
_WITHHOLD_KEYS = ("fact", "carrier", "until")
_ROLE_KEYS = ("target", "use", "control", "ignore", "fidelity")
_TAIL_KEYS = ("shot", "mode")


def _clean_reference_roles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每条：target 非空（prev_frame / tail / prop / cast:<id>），use 字符串，control / ignore 是方面词列表；
    target 是否真的落在本镜的参考图上由写盘前的 spec 校验把关（同样 400 不写盘）。"""

    out: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise HTTPException(400, f"reference_roles[{i}] 要是对象（target / use / control / ignore）")
        unknown = [k for k in item if k not in _ROLE_KEYS]
        if unknown:
            raise HTTPException(400, f"reference_roles[{i}] 里不认识的字段：{'、'.join(map(str, unknown))}")
        target = item.get("target")
        if not isinstance(target, str) or not target.strip():
            raise HTTPException(400, f"reference_roles[{i}]：target（哪张参考图）不能为空")
        use = item.get("use")
        if use is not None and not isinstance(use, str):
            raise HTTPException(400, f"reference_roles[{i}].use 要是一句话（字符串）")
        entry: dict[str, Any] = {"target": target.strip()}
        if use and use.strip():
            entry["use"] = use.strip()
        for key in ("control", "ignore"):
            raw = item.get(key)
            if raw is None:
                continue
            if not isinstance(raw, list) or any(not isinstance(a, str) for a in raw):
                raise HTTPException(400, f"reference_roles[{i}].{key} 要是方面词列表（脸 / 发型 / 服装 / 姿势 / 机位 / 光线 / 场景 / 色调）")
            aspects = list(dict.fromkeys(a.strip() for a in raw if a.strip()))
            if aspects:
                entry[key] = aspects
        fidelity = item.get("fidelity")
        if fidelity not in (None, ""):
            try:
                value = int(round(float(fidelity)))
            except (TypeError, ValueError):
                raise HTTPException(400, f"reference_roles[{i}].fidelity 要是 1–100 的百分数（相似度约多少）") from None
            if not 1 <= value <= 100:
                raise HTTPException(400, f"reference_roles[{i}].fidelity 要是 1–100 的百分数（相似度约多少）")
            entry["fidelity"] = value
        if len(entry) == 1:
            raise HTTPException(400, f"reference_roles[{i}]：use / control / ignore / fidelity 至少写一项，不然这条说明是空的")
        out.append(entry)
    return out


def _clean_ref_tail(value: Any) -> dict[str, str]:
    """ref_tail：镜号字符串（= 作参考图）或 {shot, mode}；空 = 清掉。源镜是否在本镜之前由 spec 校验把关。"""

    from storyvideo.tail import TAIL_MODES

    if isinstance(value, str):
        source, mode = value.strip(), "reference"
    elif isinstance(value, dict):
        unknown = [k for k in value if k not in _TAIL_KEYS]
        if unknown:
            raise HTTPException(400, f"ref_tail 里不认识的字段：{'、'.join(map(str, unknown))}")
        source_raw, mode_raw = value.get("shot"), value.get("mode")
        if source_raw is not None and not isinstance(source_raw, str):
            raise HTTPException(400, "ref_tail.shot 要是镜号")
        if mode_raw is not None and not isinstance(mode_raw, str):
            raise HTTPException(400, "ref_tail.mode 只能是 reference（作参考图）/ direct（直接当首帧）")
        source, mode = str(source_raw or "").strip(), (str(mode_raw or "").strip().lower() or "reference")
    else:
        raise HTTPException(400, "ref_tail 要是镜号，或 {shot, mode} 对象")
    if not source:
        return {}
    if mode not in TAIL_MODES:
        raise HTTPException(400, f"ref_tail.mode 只能是 reference（作参考图）/ direct（直接当首帧），收到「{mode}」")
    return {"shot": source, "mode": mode}


def _clean_withhold(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """每条：fact 非空；carrier / until 可选字符串（until 指向的镜是否存在由 spec 校验在写盘前把关）。形状不对 400。"""

    out: list[dict[str, str]] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise HTTPException(400, f"withhold[{i}] 要是对象（fact / carrier / until）")
        unknown = [k for k in item if k not in _WITHHOLD_KEYS]
        if unknown:
            raise HTTPException(400, f"withhold[{i}] 里不认识的字段：{'、'.join(map(str, unknown))}")
        for key in ("carrier", "until"):
            if item.get(key) is not None and not isinstance(item.get(key), str):
                raise HTTPException(400, f"withhold[{i}].{key} 要是一句话（字符串）")
        fact = str(item.get("fact") or "").strip() if isinstance(item.get("fact"), (str, type(None))) else ""
        if not fact:
            raise HTTPException(400, f"withhold[{i}]：fact（先不揭示的事实）不能为空")
        entry = {"fact": fact}
        for key in ("carrier", "until"):
            value = str(item.get(key) or "").strip()
            if value:
                entry[key] = value
        out.append(entry)
    return out


def _clean_boundary(obj: Any, label: str) -> dict[str, Any]:
    """entry / exit：六个字段都是字符串（空的丢掉），same_as_previous 是已知字段名的列表；别的键 400。"""

    from storyvideo.continuity import BOUNDARY_KEYS

    if not isinstance(obj, dict):
        raise HTTPException(400, f"{label} 要是对象（positions / facing / gaze / hands / held_props / visible_state / same_as_previous）")
    unknown = [k for k in obj if k not in BOUNDARY_KEYS and k != "same_as_previous"]
    if unknown:
        raise HTTPException(400, f"{label} 里不认识的字段：{'、'.join(map(str, unknown))}")
    out: dict[str, Any] = {}
    for key in BOUNDARY_KEYS:
        value = obj.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise HTTPException(400, f"{label}.{key} 要是一句话（字符串）")
        if value.strip():
            out[key] = value.strip()
    same = obj.get("same_as_previous")
    if same is not None:
        if not isinstance(same, list) or any(not isinstance(k, str) or k not in BOUNDARY_KEYS for k in same):
            raise HTTPException(400, f"{label}.same_as_previous 要是字段名列表（只认 {' / '.join(BOUNDARY_KEYS)}）")
        if same:
            out["same_as_previous"] = list(dict.fromkeys(same))
    return out


def _clean_axis(obj: Any) -> dict[str, Any]:
    """axis：anchors 两个不同的名字，side ∈ left/right，screen 每人 left/right，break 布尔；别的键 400。"""

    from storyvideo.prompts import AXIS_SIDES

    if not isinstance(obj, dict):
        raise HTTPException(400, "axis 要是对象（anchors / side / screen / break）")
    unknown = [k for k in obj if k not in _AXIS_KEYS]
    if unknown:
        raise HTTPException(400, f"axis 里不认识的字段：{'、'.join(map(str, unknown))}")
    anchors_raw = obj.get("anchors") or []
    if not isinstance(anchors_raw, list) or any(not isinstance(a, str) for a in anchors_raw):
        raise HTTPException(400, "axis.anchors 要是名字列表")
    anchors = [a.strip() for a in anchors_raw if a.strip()]
    if anchors and (len(anchors) != 2 or anchors[0] == anchors[1]):
        raise HTTPException(400, "axis.anchors 要恰好两个不同的锚点（轴线是两个可命名锚点的连线）")
    side = obj.get("side")
    if side is not None and not isinstance(side, str):
        raise HTTPException(400, "axis.side 只能是 left / right")
    side = str(side or "").strip().lower()
    if side and side not in AXIS_SIDES:
        raise HTTPException(400, f"axis.side 只能是 left / right，收到「{side}」")
    screen_raw = obj.get("screen") or {}
    if not isinstance(screen_raw, dict):
        raise HTTPException(400, "axis.screen 要是 {名字: left/right} 对象")
    screen: dict[str, str] = {}
    for key, value in screen_raw.items():
        if not str(key).strip():
            continue
        if not isinstance(value, str) or (value.strip() and value.strip().lower() not in AXIS_SIDES):
            raise HTTPException(400, f"axis.screen 里「{key}」的画面侧只能是 left / right")
        if value.strip():
            screen[str(key).strip()] = value.strip().lower()
    brk = obj.get("break")
    if brk not in (None, True, False):
        raise HTTPException(400, "axis.break 只能是 true / false")
    out: dict[str, Any] = {}
    if anchors:
        out["anchors"] = anchors
    if side:
        out["side"] = side
    if screen:
        out["screen"] = screen
    if brk:
        out["break"] = True
    return out


def _clean_readable_text(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """每条：载体 surface + 文字 text 非空，language ∈ zh/en/digits（缺省 zh），position 可选。形状不对 400。"""

    from storyvideo.prompts import READABLE_TEXT_LANGUAGES

    out: list[dict[str, str]] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise HTTPException(400, f"readable_text[{i}] 要是对象（surface / text / language / position）")
        surface = str(item.get("surface") or "").strip()
        text = str(item.get("text") or "").strip()
        if not surface or not text:
            raise HTTPException(400, f"readable_text[{i}]：载体（surface）和文字（text）都不能为空")
        lang = str(item.get("language") or "zh").strip().lower()
        if lang not in READABLE_TEXT_LANGUAGES:
            raise HTTPException(400, f"readable_text[{i}].language 只能是 {' / '.join(READABLE_TEXT_LANGUAGES)}，收到「{lang}」")
        entry = {"surface": surface, "text": text, "language": lang}
        position = str(item.get("position") or "").strip()
        if position:
            entry["position"] = position
        out.append(entry)
    return out


_SHOT_TEXT_FIELDS = ("shot", "art", "motion", "sfx", "entry_state", "exit_state", "speech_mode", "gaze", "purpose", "scene_type", "neg_extra",
                     "boundary_change")
_SHOT_REQUIRED_TEXT = ("shot", "art", "motion")

LOCKED_EDIT_MESSAGE = (
    "项目已开始制作，镜头的画面、动作、台词、运镜会进提示词，不能原地改——"
    "点「复制为新版本」在新版本里改（已生成的产物不受影响）。"
)


def _apply_shot_fields(target: dict[str, Any], edit: ShotEdit) -> None:
    """把编辑器提交的镜头字段合并进原始镜次；None = 没动，空串 = 清掉（必填三项除外）。"""

    merge_shot_edit(
        target,
        lines=edit.lines, camera_move=edit.camera_move,
        camera_combo=edit.camera_combo, dur=edit.dur,
    )
    for key in _SHOT_TEXT_FIELDS:
        value = getattr(edit, key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            target[key] = text
        elif key in _SHOT_REQUIRED_TEXT:
            raise HTTPException(400, f"{key} 不能为空——它是首帧/视频提示词的正文")
        else:
            target.pop(key, None)
    if edit.ref_frame is not None:
        ref = edit.ref_frame.strip()
        if ref:
            target["ref_frame"] = ref
        else:
            target.pop("ref_frame", None)
    if edit.cast is not None:
        target["cast"] = [str(c).strip() for c in edit.cast if str(c).strip()]
    if edit.readable_text is not None:
        cleaned = _clean_readable_text(edit.readable_text)
        if cleaned:
            target["readable_text"] = cleaned
        else:
            target.pop("readable_text", None)
    for key in ("entry", "exit"):
        value = getattr(edit, key)
        if value is None:
            continue
        cleaned_boundary = _clean_boundary(value, key)
        if cleaned_boundary:
            target[key] = cleaned_boundary
        else:
            target.pop(key, None)
    if edit.axis is not None:
        cleaned_axis = _clean_axis(edit.axis)
        if cleaned_axis:
            target["axis"] = cleaned_axis
        else:
            target.pop("axis", None)
    if edit.withhold is not None:
        cleaned_withhold = _clean_withhold(edit.withhold)
        if cleaned_withhold:
            target["withhold"] = cleaned_withhold
        else:
            target.pop("withhold", None)
    if edit.ref_tail is not None:
        cleaned_tail = _clean_ref_tail(edit.ref_tail)
        if cleaned_tail:
            target["ref_tail"] = cleaned_tail
        else:
            target.pop("ref_tail", None)
    if edit.reference_roles is not None:
        cleaned_roles = _clean_reference_roles(edit.reference_roles)
        if cleaned_roles:
            target["reference_roles"] = cleaned_roles
        else:
            target.pop("reference_roles", None)


def _load_editable(episode_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    try:
        path, _root = resolve_storyvideo_path(episode_id)
        current = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    if is_locked(current["output_root"]):
        raise HTTPException(409, LOCKED_EDIT_MESSAGE)
    return path, current, read_json_bom(path)


@contextmanager
def _editing(episode_id: str) -> Iterator[tuple[Path, dict[str, Any], dict[str, Any]]]:
    """草稿态「读→改→写」的整段互斥：锁按项目文件路径，持有到写盘完成；同一份文件正在被另一次保存改着就 409。

    没有锁的时候两个逐镜 PUT 各读一份旧文件、各写一次，后写的抹掉先写的字段，两边都 200（丢更新）。
    """

    try:
        path, _root = resolve_storyvideo_path(episode_id)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    try:
        with spec_write_lock(path):
            yield _load_editable(episode_id)
    except SpecWriteBusy as exc:
        raise HTTPException(409, str(exc)) from exc


def _validate_and_write(path: Path, mutated: dict[str, Any], shot_id: str | None) -> dict[str, Any]:
    try:
        validated = normalize_storyvideo_spec(mutated, project_root=PROJECT_ROOT)
        # normalize_spec 不校验 camera_move/camera_combo 是否是镜头库里的真实 id——
        # 那条校验在编译提示词时才触发（CameraError，不是 SpecError）。必须在这里、
        # 写盘之前就把编译也跑一遍，不然「非法运镜」会先落盘，响应再崩溃，
        # 造成「报错了但其实已经写坏文件」的假象。
        shot_payload = None
        if shot_id is not None:
            updated_shot = next(s for s in validated["shots"] if s["id"] == shot_id)
            shot_payload = shot_detail(validated, updated_shot)
        # 改短镜头不拦，但挂在这一镜上的静音区间超出新时长时要当场说：不改的话之后每次保存后期块都会被校验挡下
        response_warnings = check_duration_budget(validated) + validate_camera_usage(validated) + silence_overrun_warnings(validated)
        if shot_id is None:
            for s in validated["shots"]:
                shot_detail(validated, s)   # 编译每一镜，任何非法运镜/引用在写盘前现形
    except (SpecError, CameraError) as exc:
        raise HTTPException(400, f"改动没通过校验，没有写盘：{humanize(exc)}") from exc
    atomic_write_json(path, mutated)
    return {"shot": shot_payload, "warnings": response_warnings}


@router.put("/api/episodes/storyvideo/{episode_id}/shots/{shot_id}")
def edit_storyvideo_shot(episode_id: str, shot_id: str, edit: ShotEdit) -> dict[str, Any]:
    with _editing(episode_id) as (path, _current, raw):
        target = next((s for s in raw.get("shots", []) if s.get("id") == shot_id), None)
        if target is None:
            raise HTTPException(404, f"shot not found: {shot_id}")
        if "lines" not in target and (
            "en" in target or "zh" in target or "speaker" in target
        ):
            raise HTTPException(
                409,
                "这一镜是旧式 speaker/en/zh 平铺字段，这个工具不知道怎么安全写回这种格式，"
                "拒绝编辑——改台词请直接编辑源 JSON 文件。",
            )
        mutated = copy.deepcopy(raw)
        mutated_shot = next(s for s in mutated["shots"] if s.get("id") == shot_id)
        _apply_shot_fields(mutated_shot, edit)
        return _validate_and_write(path, mutated, shot_id)


class ShotCreate(ShotEdit):
    id: str | None = None             # 不给就 S<下一个序号>
    after: str | None = None          # 插在哪一镜后面；None = 末尾；"" = 开头


def allocate_shot_id(existing: Iterable[str], req: ShotCreate) -> str:
    from genkit.ids import next_shot_id

    sid = str(req.id or "").strip()
    return sid or next_shot_id(existing)


@router.post("/api/episodes/storyvideo/{episode_id}/shots")
def create_storyvideo_shot(episode_id: str, req: ShotCreate) -> dict[str, Any]:
    with _editing(episode_id) as (path, _current, raw):
        shots = raw.setdefault("shots", [])
        shot_id = allocate_shot_id([str(s.get("id")) for s in shots if isinstance(s, dict)], req)
        if any(isinstance(s, dict) and s.get("id") == shot_id for s in shots):
            raise HTTPException(409, f"已经有一镜叫「{shot_id}」")
        new_shot: dict[str, Any] = {"id": shot_id, "dur": 5, "cast": [], "lines": []}
        if req.dur is None:
            req.dur = 5
        _apply_shot_fields(new_shot, req)
        for key in _SHOT_REQUIRED_TEXT:
            if not str(new_shot.get(key) or "").strip():
                raise HTTPException(400, f"新镜需要填 {key}（画面 / 美术 / 动作）")
        new_shot.setdefault("generation_role", "source_clip")
        new_shot.setdefault("request_type", "first_frame")
        mutated = copy.deepcopy(raw)
        if req.after is None:
            mutated["shots"].append(new_shot)
        elif req.after == "":
            mutated["shots"].insert(0, new_shot)
        else:
            idx = next((i for i, s in enumerate(mutated["shots"]) if isinstance(s, dict) and s.get("id") == req.after), None)
            if idx is None:
                raise HTTPException(404, f"没有这一镜：{req.after}")
            mutated["shots"].insert(idx + 1, new_shot)
        return _validate_and_write(path, mutated, shot_id)


@router.delete("/api/episodes/storyvideo/{episode_id}/shots/{shot_id}")
def delete_storyvideo_shot(episode_id: str, shot_id: str) -> dict[str, Any]:
    with _editing(episode_id) as (path, _current, raw):
        shots = raw.get("shots", [])
        if not any(isinstance(s, dict) and s.get("id") == shot_id for s in shots):
            raise HTTPException(404, f"shot not found: {shot_id}")
        if len(shots) <= 1:
            raise HTTPException(409, "至少要保留一镜")
        blockers: list[str] = []
        refs = [str(s.get("id")) for s in shots if isinstance(s, dict) and s.get("ref_frame") == shot_id]
        if refs:
            blockers.append(f"被 {', '.join(refs)} 当作参考镜")
        vo = [ln for ln in ((raw.get("voice_over") or {}).get("lines") or ()) if isinstance(ln, dict) and str(ln.get("shot")) == shot_id]
        if vo:
            blockers.append(f"挂着 {len(vo)} 句旁白")
        pg = raw.get("post_graphics") or {}
        ins = [x for x in (pg.get("phone_ui_inserts") or ()) if isinstance(x, dict) and str(x.get("after")) == shot_id]
        if ins:
            blockers.append(f"后面接着插卡 {', '.join(str(x.get('id')) for x in ins)}")
        notes = [x for x in (pg.get("notebook_text") or ()) if isinstance(x, dict) and str(x.get("shot")) == shot_id]
        if notes:
            blockers.append(f"叠着 {len(notes)} 条画面文字")
        # 绝对静音区间也按镜号挂着：删了镜就成了悬空引用，之后每次保存后期块（含给旁白绑音色）都会被校验挡下
        silence = pg.get("silence") if isinstance(pg.get("silence"), list) else ()
        muted = [x for x in silence if isinstance(x, dict) and str(x.get("shot")) == shot_id]
        if muted:
            blockers.append(f"挂着 {len(muted)} 段静音区间")
        if blockers:
            raise HTTPException(409, f"「{shot_id}」还有关联：{'；'.join(blockers)}——先解开这些关联再删")
        mutated = copy.deepcopy(raw)
        mutated["shots"] = [s for s in mutated["shots"] if not (isinstance(s, dict) and s.get("id") == shot_id)]
        result = _validate_and_write(path, mutated, None)
        return {"deleted": shot_id, "warnings": result["warnings"]}


class ShotOrder(BaseModel):
    order: list[str]


@router.put("/api/episodes/storyvideo/{episode_id}/shots-order")
def reorder_storyvideo_shots(episode_id: str, req: ShotOrder) -> dict[str, Any]:
    with _editing(episode_id) as (path, _current, raw):
        by_id = {s.get("id"): s for s in raw.get("shots", []) if isinstance(s, dict)}
        if sorted(req.order) != sorted(by_id) or len(req.order) != len(by_id):
            raise HTTPException(400, "order 必须恰好包含现有的每一镜各一次")
        mutated = copy.deepcopy(raw)
        mutated["shots"] = [copy.deepcopy(by_id[sid]) for sid in req.order]
        result = _validate_and_write(path, mutated, None)
        return {"order": req.order, "warnings": result["warnings"]}


class ReviseRequest(BaseModel):
    new_episode_id: str


@router.post("/api/episodes/storyvideo/{episode_id}/revise")
def revise_storyvideo_episode(episode_id: str, req: ReviseRequest) -> dict[str, Any]:
    """「开新 revision」——锁定项目那句"改设计要开新 revision"提示文案背后

    一直没有对应的功能，以前只能手写脚本改出新版本。这里只复制 spec（故事/分镜/台词/运镜/风格），不碰生成
    产物——新 revision 的 ``output_root`` 指向一个全新目录，跟原项目已经
    生成的首帧/视频完全不共享，改了设计之后重新生成是新 revision 自己的事，
    不会让人误以为旧产物能直接配新剧本用。新 revision 天然是未锁定的草稿
    （新目录下还没有 ``manifests/episode.snapshot.json``），可以照常逐镜编辑，
    不需要走"锁定"就能改。不限于已锁定的项目才能复制——已锁定项目要开新
    revision 正是这个功能存在的理由；对未锁定草稿调用也没什么问题，只是
    没那么必要（直接改原草稿更直接）。
    """
    try:
        path, _root = resolve_storyvideo_path(episode_id)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc

    new_id = req.new_episode_id.strip()
    if not new_id:
        raise HTTPException(400, "新 id 不能为空")
    # 这个 id 是用户在高级页自由输入的：跟建项目的其它入口一样校验形状，不然 "../x" 会写到 content/story_video 之外
    if not is_valid_id(new_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    new_path = STORYVIDEO_DIR / f"{new_id}.json"
    if new_path.exists():
        raise HTTPException(409, f"项目 {new_id} 已经存在，换一个 id。")
    taken = _ledger_id_taken(new_id)
    if taken:
        raise HTTPException(409, taken)

    mutated = copy.deepcopy(read_json_bom(path))
    mutated["id"] = new_id
    mutated["output_root"] = f"output/story_video/{new_id}"

    try:
        normalize_storyvideo_spec(mutated, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(400, f"复制出来的新 revision 没通过结构校验，没有创建：{humanize(exc)}") from exc

    atomic_write_json(new_path, mutated)
    return {"id": new_id, "path": str(new_path.relative_to(PROJECT_ROOT)).replace("\\", "/")}


@router.get("/api/episodes/storyvideo/{episode_id}/capability-check")
def capability_check(episode_id: str) -> dict[str, Any]:
    """按当前模型设置检查 spec 参数是否越界（时长、分辨率、画幅、参考图）。只读，不花钱。"""

    from genkit import settings as settings_mod
    from genkit.capabilities import check_spec

    from genkit.capabilities import effective_model
    from storyvideo.dialects import dialect_for_model, resolve_dialect

    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    current = settings_mod.load_settings()
    out = check_spec(spec, current)
    out["issues"].extend(_reference_cap_issues(spec, out.get("image") or {}))
    params = spec.get("video_params") or {}
    video_cfg = current.role("video")
    model = effective_model(params.get("model"), settings_mod.effective(video_cfg, "video").model if video_cfg.adapter else "")
    out["dialect"] = {
        "setting": str(params.get("prompt_dialect") or ""),        # spec 里写的：auto / generic / minimax-h3 / seedance / 空
        "effective": resolve_dialect(spec, current),                 # 实际会用的
        "for_model": dialect_for_model(model),                       # 当前视频模型对应的官方语法
    }
    return out


def _reference_cap_issues(spec: Mapping[str, Any], image_caps: Mapping[str, Any]) -> list[dict[str, Any]]:
    """参考图上限（DESIGN §30）：能力表 / 目录给了出图模型的 max_reference_images 时，逐镜数首帧请求会带的参考图
    （上一镜首帧/尾帧 → 出场定妆 → 道具，共用锚图只算一张），超的点名 warn；没超但有镜带参考图时给一行 info 说上限。"""

    from storyvideo.prompts import reference_slot_plan

    cap = image_caps.get("max_reference_images")
    if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
        return []
    model = str(image_caps.get("model") or "")
    label = model + {"verified": "", "catalog": "（按服务商目录）"}.get(str(image_caps.get("source")), "（按文档，未实测）")
    counts: list[tuple[str, int]] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, dict):
            continue
        sid = str(shot.get("id"))
        try:
            n = len({s["index"] for s in reference_slot_plan(spec, sid)})
        except Exception:  # noqa: BLE001
            continue
        if n:
            counts.append((sid, n))
    if not counts:
        return []
    over = [(sid, n) for sid, n in counts if n > cap]
    if over:
        listed = "、".join(f"{sid} ×{n}" for sid, n in over)   # 语言中立的写法，英文界面不用再翻
        return [{"level": "warn", "role": "image", "shots": [sid for sid, _ in over],
                 "message": f"{label} 最多收 {cap} 张参考图，这些镜带的更多，超出的会被丢掉：{listed}——减少出场锚图或去掉道具参考"}]
    return [{"level": "info", "role": "image",
             "message": f"{label} 最多收 {cap} 张参考图（上一镜首帧/尾帧 → 出场定妆 → 道具），本集每镜都在上限内"}]


class GlobalsEdit(BaseModel):
    values: dict[str, Any]


@router.put("/api/episodes/storyvideo/{episode_id}/globals")
def put_globals(episode_id: str, edit: GlobalsEdit) -> dict[str, Any]:
    """草稿态改全局生成字段（image_params / video_params / 风格锁等，见 revise.GLOBAL_KEYS）。
    制作中的项目走 revise-edit（kind=globals），这里直接 409。"""

    from .. import revise as R

    # 跟逐镜编辑同一把锁：全局字段和逐镜 PUT 同时改同一份草稿时，各读一份旧文件各写一次会互相抹掉
    with _editing(episode_id) as (path, _current, raw):
        try:
            mutated = R.apply_mutation(raw, {"kind": "globals", "values": edit.values})
        except R.ReviseError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _validate_and_write(path, mutated, None)


def _genre_review_cost_note() -> str:
    from genkit import local as local_mod
    from storyvideo.genre_review import REMOTE_COST_NOTE

    return local_mod.cost_note_for("text", 1, REMOTE_COST_NOTE)


def _run_genre_review(episode_id: str) -> dict[str, Any]:
    from storyvideo import genre_review as GR

    record = GR.run_review(episode_id, project_root=PROJECT_ROOT)
    items = record["result"]["items"]
    return {
        "card_id": record["card_id"], "card_name": record["card_name"], "created_at": record["created_at"],
        "verification": record["verification"],
        "counts": {
            "violated": sum(1 for it in items if it["verdict"] == "violated"),
            "risk": sum(1 for it in items if it["verdict"] == "risk" and it.get("evidence_verified")),
            "ok": sum(1 for it in items if it["verdict"] == "ok"),
            "unverified": sum(1 for it in items if it.get("evidence_verified") is False),
        },
    }


@router.post("/api/episodes/storyvideo/{episode_id}/genre-review")
def start_genre_review(episode_id: str) -> dict[str, Any]:
    """按题材审稿（DESIGN §49）：后台任务 ``genre-review``，发 1 次文本模型请求。没选题材 400，已经在跑 409。
    不进账本、不改 spec，制作中的项目也能审（结果写在 manifests/genre_review.json）。"""

    from storyvideo import genre_review as GR

    from .. import tasks

    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    if not spec.get("genre"):
        raise HTTPException(400, GR.NO_GENRE_MESSAGE)
    require_text_model()
    try:
        task_id = tasks.start(GR.TASK_KIND, episode_id, lambda: _run_genre_review(episode_id),
                              params={"genre": spec["genre"]}, module="drama")
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id, "cost_note": _genre_review_cost_note()}


@router.get("/api/episodes/storyvideo/{episode_id}/genre-review")
def get_genre_review(episode_id: str) -> dict[str, Any]:
    """上次审稿的结果 + 是否过时 + 核对过的条目变成的问题卡。没审过 ``review: null``。只读，不花钱。"""

    from storyvideo import genre_review as GR
    from storyvideo.scriptgen import find_genre

    from .. import tasks
    from ..problems import problem_cards

    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    card = find_genre(spec.get("genre")) if spec.get("genre") else None
    state = GR.review_state(spec)
    return {
        "genre": {"id": str(card["id"]), "name": str(card["name"])} if card else None,
        "review": state["review"],
        "stale": state["stale"],
        "note": state["note"],
        "problems": problem_cards(state["warnings"]),
        "cost_note": _genre_review_cost_note(),
        "task_id": tasks.active_task_id(GR.TASK_KIND, episode_id),
    }


def _craft_review_cost_note() -> str:
    from genkit import local as local_mod
    from storyvideo.craft_review import REMOTE_COST_NOTE

    return local_mod.cost_note_for("text", 1, REMOTE_COST_NOTE)


def _run_craft_review(episode_id: str) -> dict[str, Any]:
    from storyvideo import craft_review as CR

    record = CR.run_review(episode_id, project_root=PROJECT_ROOT)
    items = record["result"]["items"]
    return {
        "checklist_id": record["checklist_id"], "checklist_name": record["checklist_name"], "created_at": record["created_at"],
        "verification": record["verification"],
        "counts": {
            "violated": sum(1 for it in items if it["verdict"] == "violated"),
            "risk": sum(1 for it in items if it["verdict"] == "risk" and it.get("evidence_verified")),
            "ok": sum(1 for it in items if it["verdict"] == "ok"),
            "unverified": sum(1 for it in items if it.get("evidence_verified") is False),
        },
    }


@router.post("/api/episodes/storyvideo/{episode_id}/craft-review")
def start_craft_review(episode_id: str) -> dict[str, Any]:
    """剧本工艺审稿：跟题材审稿同一套闸门，清单与题材无关（张力 / 情绪可见 / 节奏 / 合规），不用先选题材。
    后台任务 ``craft-review``，发 1 次文本模型请求；清单文件读不出来 400，已经在跑 409。
    不进账本、不改 spec，制作中的项目也能审（结果写在 manifests/craft_review.json）。"""

    from storyvideo import craft_review as CR

    from .. import tasks

    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    info = CR.checklist_info()
    if info is None:
        raise HTTPException(400, CR.MISSING_CHECKLIST_MESSAGE)
    require_text_model()
    try:
        task_id = tasks.start(CR.TASK_KIND, episode_id, lambda: _run_craft_review(episode_id),
                              params={"checklist": info["id"]}, module="drama")
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id, "cost_note": _craft_review_cost_note()}


@router.get("/api/episodes/storyvideo/{episode_id}/craft-review")
def get_craft_review(episode_id: str) -> dict[str, Any]:
    """上次工艺审稿的结果 + 是否过时 + 核对过的条目变成的问题卡。没审过 ``review: null``。只读，不花钱。"""

    from storyvideo import craft_review as CR

    from .. import tasks
    from ..problems import problem_cards

    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    state = CR.review_state(spec)
    return {
        "checklist": CR.checklist_info(),
        "review": state["review"],
        "stale": state["stale"],
        "note": state["note"],
        "problems": problem_cards(state["warnings"]),
        "cost_note": _craft_review_cost_note(),
        "task_id": tasks.active_task_id(CR.TASK_KIND, episode_id),
    }


@router.post("/api/episodes/storyvideo/{episode_id}/prepare")
def prepare_storyvideo_episode(episode_id: str, force: bool = False) -> dict[str, Any]:
    """锁定草稿，冻结进 manifests/episode.snapshot.json。不花钱，local-only——

    跟 CLI 的 ``storyvideo prepare`` 是同一个调用，锁定之后这个工具的编辑接口
    会跟 CLI 一样拒绝原地改，要改设计得复制新 revision。

    开始制作前先过一遍模型能力表：实测过的组合下参数越界（比如 Seedance 2.0 给了 20 秒的镜）
    直接拦住（409 + 问题列表），``force=true`` 可以硬过——文档级的记录只提醒不拦。
    """
    if not force:
        try:
            issues = capability_check(episode_id)
        except HTTPException:
            issues = {"errors": 0, "issues": []}
        if issues.get("errors"):
            raise HTTPException(409, {"message": "模型能力检查没通过，开始制作前先改参数（或强制开始）", "check": issues})
    try:
        with StoryVideoRunner(str(PROJECT_ROOT)) as runner:
            spec = runner.prepare(episode_id)
            result = runner.status(spec["spec_path"] or episode_id)
    except RUNNER_ERRORS as exc:
        # 同上——补一句人话前缀，原始技术细节保留在后面。
        raise HTTPException(400, f"锁定失败：{humanize(exc)}") from exc
    return result


@router.post("/api/episodes/storyvideo/{episode_id}/import-legacy")
def import_legacy_episode(episode_id: str) -> dict[str, Any]:
    """登记本地已有、但账本里没记录的产物。不调用任何生成接口，只读本地文件、

    做技术校验、把结果写进账本——跟 CLI 的
    ``storyvideo import-legacy --confirm-existing-artifacts`` 是同一个调用。

    只对「账本里还没有这个 run、本地又有产物」的项目开放（详情页 ``needs_import_legacy`` 就是这个条件）：
    账本里已有 run 的项目再调会把 run 状态倒回 awaiting_frames_qc、把重拍过的旧片重新登记成成功；
    空草稿调它等于悄悄锁定项目、状态还写成 legacy_import_partial。这两种都 409。
    判断看账本而不是「锁定」（快照文件在不在）：从归档手动搬回来、或账本行丢了的项目，快照在、账本没有 run，
    prepare 要求先导入——这里再以「已经开始制作」拒绝就成了死路。
    """
    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    if ledger_run(str(spec["id"])) is not None:
        raise HTTPException(409, "项目已经开始制作，产物都记在账本里了——不用再导入；缺的镜直接点「生成」")
    if not has_unimported_legacy_artifacts(spec):
        raise HTTPException(409, "没有可导入的产物：输出目录里没有首帧 / 视频 / 成片文件——直接「开始制作」即可")
    try:
        with StoryVideoRunner(str(PROJECT_ROOT)) as runner:
            result = runner.import_legacy(episode_id, confirmed_existing_artifacts=True)
    except RUNNER_ERRORS as exc:
        # 同上。
        raise HTTPException(400, f"导入已有产物失败：{humanize(exc)}") from exc
    return result


@router.get("/api/episodes/storyvideo/{episode_id}/generate-estimate")
def generate_estimate(
    episode_id: str, through: str, only: str | None = None
) -> dict[str, Any]:
    """只读预估：这一批大概要新提交几个 frame/video，用什么模型。不花钱。

    项目文件 / 账本 / 冻结快照对不上时直接 409：这时 runner 必然拒绝生成，照发 plan_token 的话界面会先弹
    花钱确认框、确认之后才报错——先说清要处理什么。"""
    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
        freeze = freeze_state(spec)
        if freeze["mismatch"]:
            raise HTTPException(409, FREEZE_MISMATCH_HINTS[freeze["mismatch"]])
        only_ids = [i.strip() for i in only.split(",") if i.strip()] if only else None
        return estimate_plan(spec, through=through, only=only_ids)
    except (SpecError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


class GenerateRequest(BaseModel):
    through: str
    only: list[str] | None = None
    plan_token: str
    wait: bool = False


@router.post("/api/episodes/storyvideo/{episode_id}/generate")
def generate_storyvideo(episode_id: str, req: GenerateRequest) -> dict[str, Any]:
    """真正会花钱的唯一端点。要求 plan_token 跟「此刻重新算一遍」的预估一致——

    防的不是恶意调用（本地单用户工具没有这个威胁模型），防的是界面上一次
    意外的重复点击/请求，在人没看清「这次要新提交几个」之前就把钱花出去。
    token 不匹配说明磁盘状态在预估和提交之间变了（比如另一个进程已经在跑），
    直接拒绝，让前端重新拉一次预估。真正的去重/防重复提交仍然由
    StoryVideoRunner.run() 自己的状态机保证，这层 token 只是人机确认闸门。
    """
    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
        current = estimate_plan(spec, through=req.through, only=req.only)
    except (SpecError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if current["plan_token"] != req.plan_token:
        raise HTTPException(
            409,
            "计划已过期（磁盘状态在你看到预估之后变了）——重新拉一次预估再确认，"
            "不会因为这次拒绝而多花钱，什么都还没提交。",
        )
    provider = build_provider()
    # 起任务前先查角色配好没有（2026-09-22 审计 DRAMA-01 / FE-02）：以前没配模型也会进 runner.run()——run 先 prepare
    # 把草稿锁死，再把第一镜记成「提交结果不明」，项目从此永远 409。首帧要生图模型；视频要视频模型，
    # 首帧还没出的那些镜还要先出首帧、也要生图模型。没有要新提交的就不查（纯轮询 / 什么都不用做）
    needed: list[str] = []
    if req.through == "frames" and current["pending_count"]:
        needed.append("image")
    elif req.through == "videos":
        if current.get("missing_upstream_frames"):
            needed.append("image")
        if current["pending_count"]:
            needed.append("video")
    require_roles(provider, *needed)
    try:
        with StoryVideoRunner(str(PROJECT_ROOT), provider=provider) as runner:
            result = runner.run(
                episode_id, only=req.only, through=req.through, wait=req.wait
            )
    except StoryVideoGatePending as exc:
        # 这三类异常的原始文案是状态机内部的英文守卫消息（比如
        # "frame QC must be accepted before video submission"）——不是给操作
        # 台前端用户看的措辞。逐条翻译不现实（这层守卫消息有几十条，散在
        # runtime.py 各处，而且是 CLI 也在用的共享模块，不该为了 webui 的可读性
        # 去重写底层状态机的异常文案）。退而求其次：至少告诉操作者"是哪个动作
        # 被拦、大概是哪一类原因"，原始技术细节保留在后面给会看的人/开发者用。
        raise HTTPException(425, f"生成暂时不能进行（还差一个前置步骤没完成）：{humanize(exc)}") from exc
    except StoryVideoBlocked as exc:
        raise HTTPException(409, f"生成被拦下了（当前项目状态不允许这个操作）：{humanize(exc)}") from exc
    except RUNNER_ERRORS as exc:
        raise HTTPException(400, f"生成失败：{humanize(exc)}") from exc
    # wait=False 提交即返回：之后由后台轮询（DESIGN §34）去问服务商、下载、记账；这里只负责把它起起来
    watch = ensure_watcher(episode_id, project_root=PROJECT_ROOT)
    result["watch_task_id"] = watch["task_id"]
    result["inflight"] = watch["inflight"]
    return result


@router.post("/api/episodes/storyvideo/{episode_id}/generate-resume")
def generate_resume(episode_id: str) -> dict[str, Any]:
    """不花钱、不要 plan_token：把「已提交、还没拿到结果」的任务交给后台轮询（DESIGN §34）。

    已经有一个在跑就返回它的 task_id；没有在飞的任务返回 ``inflight: []``（200，不是错误）。
    """
    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except (SpecError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    watch = ensure_watcher(episode_id, project_root=PROJECT_ROOT)
    return {"task_id": watch["task_id"], "started": watch["started"], "inflight": watch["inflight"],
            "in_progress": inflight_shot_ids(spec)}


@router.get("/api/episodes/storyvideo/{episode_id}/failures")
def list_failures(episode_id: str) -> dict[str, Any]:
    """账本里明确失败的首帧/视频任务：原因分类（余额/审核/超时/其他）、人话建议、能不能原地重提。"""

    from ..generate import failed_jobs

    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except (SpecError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    items = failed_jobs(spec)
    return {"items": items, "count": len(items),
            "retryable": [i["shot_id"] for i in items if i["retryable"]],
            "moderation": sorted({i["shot_id"] for i in items if i["kind"] == "moderation"})}


class RetryFailedRequest(BaseModel):
    only: list[str] | None = None
    reason: str = "retry after failed attempt"
    force: bool = False   # 审核拦截的镜默认不重提（原样重提会再被拦），force 才重提


@router.post("/api/episodes/storyvideo/{episode_id}/retry-failed")
def retry_failed(episode_id: str, req: RetryFailedRequest) -> dict[str, Any]:
    """把失败任务重置回待提交，下一次「生成」会重新提交。不花钱——真正花钱的还是 /generate。

    审核拦截类失败默认跳过并给出「改写提示词」建议；超时/余额类直接重置。"""

    from ..generate import failed_jobs

    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except (SpecError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    items = failed_jobs(spec, req.only)
    if not items:
        raise HTTPException(409, "没有失败的任务可以重提")
    skipped_moderation = sorted({i["shot_id"] for i in items if i["kind"] == "moderation"}) if not req.force else []
    wanted = sorted({i["shot_id"] for i in items if i["shot_id"] not in skipped_moderation})
    result: dict[str, Any] = {"run_id": None, "reset": [], "skipped": []}
    if wanted:
        try:
            with StoryVideoRunner(str(PROJECT_ROOT)) as runner:
                result = runner.retry_failed(episode_id, only=wanted, reason=req.reason)
        except RUNNER_ERRORS as exc:
            raise HTTPException(400, f"重提失败：{humanize(exc)}") from exc
    advice = []
    if skipped_moderation:
        advice.append(f"{'、'.join(skipped_moderation)} 是内容被审核拦截，没有重提：改写这些镜的画面/台词描述后再重拍（或勾选「强制重提」）")
    if result.get("skipped"):
        advice.append("提交结果不明的任务要先对账：" + "；".join(result["skipped"]))
    return {"reset": result.get("reset", []), "skipped": result.get("skipped", []),
            "skipped_moderation": skipped_moderation, "advice": advice}


@router.get("/api/camera-options")
def camera_options(profile: str | None = None) -> dict[str, Any]:
    library = camera_library()
    shots = library.get("shots", [])
    combos = library.get("combos_within_shot", [])
    legacy = False

    def _detail(entry: dict[str, Any]) -> dict[str, Any]:
        # 出处与置信度（DESIGN §48）：refs 是风格参考，basis 写明是人工设定、没做逐镜分析
        return {"english": str(entry.get("english") or ""), "refs": list(entry.get("refs") or ()),
                "basis": str(entry.get("basis") or ""), "confidence": str(entry.get("confidence") or "")}

    profile_details = {name: _detail(p) for name, p in (library.get("profiles") or {}).items() if isinstance(p, dict)}
    if profile:
        # 旧项目的 profile 在 legacy_profiles 里：镜头编辑器仍要拿到它的白名单，
        # 但下面的 profiles 列表只列当前 5 条经典产线（新建项目的下拉不再给旧档）。
        prof = find_camera_profile(profile, library)
        if prof is None:
            raise HTTPException(404, f"unknown camera_profile: {profile}")
        profile_details.setdefault(profile, _detail(prof))
        legacy = profile not in (library.get("profiles") or {})
        allow = set(prof.get("allow") or ())
        allow_combos = set(prof.get("combos") or ())
        shots = [s for s in shots if s["id"] in allow]
        combos = [c for c in combos if c["id"] in allow_combos]
    return {
        "legacy": legacy,
        "shots": [
            {"id": s["id"], "cn": s["cn"], "cat": s["cat"], "risk": s["risk"], "note": s.get("note", "")}
            for s in shots
        ],
        "combos": [
            {"id": c["id"], "cn": c["cn"], "risk": c["risk"], "when": c.get("when", "")}
            for c in combos
        ],
        "profiles": sorted(library.get("profiles", {}).keys()),
        "profile_details": profile_details,
    }
