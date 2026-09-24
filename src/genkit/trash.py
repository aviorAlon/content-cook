"""回收站：各模块「删除」的共用实现——归档到 ``.webui_deleted/<模块>/<id>__<时间>/``、列出、原样恢复、彻底删除。

删除从来不是 ``os.remove``：项目文件、``output/<模块>/<id>/`` 里花钱换来的产物、``content/assets/<id>/`` 里上传的参考图 /
音乐一起搬进归档目录，再写一份 ``manifest.json``（原路径 → 归档里的名字、删除时间、标题），回收站页按它列出、恢复。

以前每个模块各写一份归档函数：大多只搬项目文件 + 产物目录，``content/assets/<id>/`` 留在原地（同 id 重建会「继承」
上一个项目的参考图 / 音乐），同一秒内同 id 归档两次会混进同一个目录，产物有文件被占用时搬到一半就冒 500；恢复只有短剧有，
其它模块的确认框只能教人「手动搬回来」——归档里的项目文件已经改名成 spec.json，照字面搬回去是找不回来的。

归档里的名字固定：``spec.json``（项目文件）、``output``（产物目录）、``assets``（上传的素材）。恢复按下面的布局表放回
原位，**不按 manifest 里记的原路径**：manifest 是给人看、给回收站列表用的记录，恢复不能让一份被改过的 manifest 把文件
搬到数据根以外去。

短剧（storyvideo）的归档另外带剧本、账本行、「被新版本引用就不搬」的锚图目录，恢复还要把账本行放回去——这些留在
``webui/routers/episodes.py``，这里只给它提供目录命名、manifest 与列表项。
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import stat
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .ids import is_valid_id

DELETED_DIRNAME = ".webui_deleted"
MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = 1
SPEC_NAME = "spec.json"
OUTPUT_NAME = "output"
ASSETS_NAME = "assets"

# 走通用归档 / 恢复的模块：项目文件都在 content/<模块>/<id>.json、产物都在 output/<模块>/<id>/（各模块 normalize 里
# output_root 固定这么拼，不读项目文件里的值）。短剧不在这张表里：它的项目目录叫 content/story_video、产物目录跟着
# 项目文件里的 output_root 走，还有剧本和账本，归档 / 恢复在 episodes.py
GENERIC_MODULES = ("clip", "music", "podcast", "script", "sticker", "portrait", "image", "picturebook")
STORYVIDEO = "storyvideo"

# 归档目录名：<id>__<UTC 时间戳>（同一秒撞名时再加 -序号）；恢复 / 彻底删除只认这个形状（不带路径分隔符，防 .. 穿越）
ENTRY_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*__\d{8}T\d{6}Z(?:-\d+)?\Z")
MODULE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

# 删除时这个项目还有后台任务在跑（出图 / 出片 / 合成 / 导出）：任务线程会接着往搬走的目录里写、甚至把项目文件存回原处，
# 列表里冒出一个只剩半截的「幽灵项目」，回收站里那一份也因为原位被占恢复不了。各模块的删除接口先查、409 这一句
TASKS_RUNNING = "这个项目还有后台任务在跑，等它结束再删——不然任务会把文件接着写回原处；进度在「任务中心」里能看到"

# 归档、恢复、彻底删除都是「先查再搬」：双击恢复、恢复的同时点彻底删除，两个请求在线程池里并发，
# 第二个查的时候第一个还没搬完，就会搬半截、或删掉正在恢复的东西。本机单用户，整个回收站一把锁足够
LOCK = threading.RLock()


class TrashError(Exception):
    """回收站操作失败的基类；``status`` 是接口层该回的 HTTP 状态码。"""

    status = 400


class EntryNotFound(TrashError):
    status = 404


class RestoreConflict(TrashError):
    """原位置已经被占：恢复不覆盖任何东西。"""

    status = 409


class FilesBusy(TrashError):
    """有文件正被别的程序占着（Windows 上正在写 / 正在播放），整体没搬、没删。``cause`` 是底层的 OSError。"""

    status = 409

    def __init__(self, message: str, cause: OSError | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class UnsupportedModule(TrashError):
    status = 400


# ---------------------------------------------------------------------------- 路径


def deleted_root(project_root: Path) -> Path:
    return Path(project_root) / DELETED_DIRNAME


def spec_target(project_root: Path, module: str, project_id: str) -> Path:
    return Path(project_root) / "content" / module / f"{project_id}.json"


def output_target(project_root: Path, module: str, project_id: str) -> Path:
    return Path(project_root) / "output" / module / project_id


def assets_target(project_root: Path, project_id: str) -> Path:
    return Path(project_root) / "content" / "assets" / project_id


def layout(project_root: Path, module: str, project_id: str) -> dict[str, Path]:
    """通用模块：归档里的名字 → 原位置（绝对路径）。"""

    if module not in GENERIC_MODULES:
        raise UnsupportedModule(f"暂不支持恢复这个模块的归档：{module}")
    return {
        SPEC_NAME: spec_target(project_root, module, project_id),
        OUTPUT_NAME: output_target(project_root, module, project_id),
        ASSETS_NAME: assets_target(project_root, project_id),
    }


def relative(path: Path, project_root: Path) -> str:
    """数据根内的路径给相对形式（正斜杠），根外的（短剧自定义 output_root）给绝对路径。"""

    try:
        return Path(path).resolve(strict=False).relative_to(Path(project_root).resolve(strict=False)).as_posix()
    except ValueError:
        return str(path)


def new_entry_dir(project_root: Path, module: str, project_id: str, *, now: datetime | None = None,
                  root: Path | None = None) -> tuple[Path, datetime]:
    """建一个空的归档目录并返回 (目录, 删除时间)。同一秒内同 id 归档两次（删 → 重建 → 再删）会撞同一个名字：
    第二份的项目文件会盖掉第一份、产物被套进第一份的 output 里，两份混在一起谁都恢复不了——撞名就加序号。"""

    now = now or datetime.now(timezone.utc)
    base = (root or deleted_root(project_root)) / module
    stamp = now.strftime(_STAMP_FORMAT)
    dest = base / f"{project_id}__{stamp}"
    serial = 1
    while True:
        try:
            dest.mkdir(parents=True)
            return dest, now
        except FileExistsError:
            serial += 1
            dest = base / f"{project_id}__{stamp}-{serial}"


# ---------------------------------------------------------------------------- 搬家


def move(source: Path, target: Path) -> None:
    """整体搬家：同一卷上用 ``os.rename``，原子——目录里有文件正被占用（Windows 上正在写的 mp3）就整体失败、一个文件
    都不动；``shutil.move`` 退回「拷贝 + 逐个删」时会删到一半再抛，留下半截副本。只有跨卷（EXDEV）才退回 ``shutil.move``。"""

    try:
        os.rename(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.move(str(source), str(target))


def move_all(moves: Iterable[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """按顺序搬；中途哪一步搬不动，就把已经搬过的逆序搬回去再把 OSError 抛出去——要么全搬、要么原样不动。"""

    done: list[tuple[Path, Path]] = []
    for source, target in moves:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            move(source, target)
        except OSError:
            for moved_from, moved_to in reversed(done):
                try:
                    move(moved_to, moved_from)
                except OSError:  # 搬回去也失败：能搬回多少是多少，剩下的留在原处让人自己处理
                    pass
            raise
        done.append((source, target))
    return done


# ---------------------------------------------------------------------------- manifest


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def title_of(raw: Any) -> str:
    """项目文件里人能认的名字：各模块字段不一样（title / title_zh / title_en），取第一个非空的。"""

    if not isinstance(raw, Mapping):
        return ""
    for key in ("title", "title_zh", "title_en"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def write_manifest(entry_dir: Path, *, project_root: Path, module: str, project_id: str, title: str, deleted_at: datetime,
                   items: Iterable[tuple[Path, str]], kept: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """在归档目录里写 ``manifest.json``：原路径 → 归档里的名字、删除时间、标题。写失败不影响归档本身
    （列表 / 恢复对没有 manifest 的老归档照样能处理），由调用方决定记不记日志。"""

    payload = {
        "format": MANIFEST_FORMAT,
        "module": module,
        "id": project_id,
        "title": title,
        "deleted_at": deleted_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "archived_to": relative(entry_dir, project_root),
        "items": [{"from": relative(source, project_root), "to": name} for source, name in items],
        "kept": [dict(k) for k in kept],
    }
    _write_json(Path(entry_dir) / MANIFEST_NAME, payload)
    return payload


def read_manifest(entry_dir: Path) -> dict[str, Any]:
    """读不出来 / 没有都返回空字典：列表要扛得住一个坏文件。"""

    try:
        raw = _read_json(Path(entry_dir) / MANIFEST_NAME)
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def finish(entry_dir: Path) -> bool:
    """恢复完收尾：归档目录里只剩 manifest 就连它一起删掉、删掉空目录；还剩别的（没放回去的账本记录）就留着。"""

    entry_dir = Path(entry_dir)
    try:
        leftovers = [p for p in entry_dir.iterdir() if p.name != MANIFEST_NAME]
    except OSError:
        return False
    if leftovers:
        return False
    try:
        (entry_dir / MANIFEST_NAME).unlink(missing_ok=True)
        entry_dir.rmdir()
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------- 引用检查


def _json_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_strings(item)


def asset_dir_users(project_root: Path, project_id: str, *, exclude: Path) -> list[str]:
    """还引用着 ``content/assets/<project_id>/`` 里文件的其它项目（``模块/id``）。

    有人引用就把这个目录留在原地：搬走的话，别的项目的参考图 / 配乐跟着没了，出图时悄悄少一张参考、合成时找不到音乐。
    整份 JSON 的字符串都扫，不只看某几个字段——漏掉一处就是一个悬空引用。"""

    marker = f"content/assets/{project_id}/"
    content = Path(project_root) / "content"
    if not content.is_dir():
        return []
    exclude = Path(exclude).resolve(strict=False)
    users: list[str] = []
    for module_dir in sorted(content.iterdir()):
        if not module_dir.is_dir() or module_dir.name == "assets":
            continue
        for path in sorted(module_dir.glob("*.json")):
            if path.resolve(strict=False) == exclude:
                continue
            try:
                raw = _read_json(path)
            except (OSError, ValueError):
                continue
            if any(marker in text.replace("\\", "/") for text in _json_strings(raw)):
                users.append(f"{module_dir.name}/{path.stem.split('.')[0]}")
    return sorted(set(users))


# ---------------------------------------------------------------------------- 归档（通用模块）


def archive(project_root: Path, module: str, project_id: str, *, title: str | None = None) -> dict[str, Any]:
    """把一个通用模块的项目归档：产物目录、上传的素材先搬，项目文件最后搬——中途搬不动（有文件被占用）就把搬过的
    搬回去、抛 ``FilesBusy``，项目还在列表里，归档目录不留半截副本。

    返回 ``archived_to``（绝对路径，各模块原来的返回形状）、``entry``、``output_moved``、``assets_moved``、
    ``assets_kept_for``（素材目录没搬的原因：这些项目还引用着它）。调用方先确认项目文件存在、id 合法。"""

    project_root = Path(project_root)
    targets = layout(project_root, module, project_id)
    spec_path = targets[SPEC_NAME]
    if not is_valid_id(project_id) or not spec_path.is_file():
        raise EntryNotFound(f"没有这个项目：{module}/{project_id}")
    with LOCK:
        if title is None:
            try:
                title = title_of(_read_json(spec_path))
            except (OSError, ValueError):   # 坏掉的项目文件也得能删（列表里 status=error 的那种）
                title = ""
        kept: list[dict[str, Any]] = []
        plan: list[tuple[Path, str]] = []
        if targets[OUTPUT_NAME].is_dir():
            plan.append((targets[OUTPUT_NAME], OUTPUT_NAME))
        assets_kept_for: list[str] = []
        if targets[ASSETS_NAME].is_dir():
            assets_kept_for = asset_dir_users(project_root, project_id, exclude=spec_path)
            if assets_kept_for:
                kept.append({"path": relative(targets[ASSETS_NAME], project_root), "used_by": assets_kept_for})
            else:
                plan.append((targets[ASSETS_NAME], ASSETS_NAME))
        plan.append((spec_path, SPEC_NAME))
        dest, now = new_entry_dir(project_root, module, project_id)
        try:
            move_all((source, dest / name) for source, name in plan)
        except OSError as exc:
            try:
                dest.rmdir()
            except OSError:
                pass
            raise FilesBusy(f"有文件正被占用，没删除：{exc}——等正在进行的生成 / 导出结束，或关掉正在打开它的程序再删", exc) from exc
        try:
            write_manifest(dest, project_root=project_root, module=module, project_id=project_id, title=title or "",
                           deleted_at=now, items=plan, kept=kept)
        except OSError:
            pass   # 没有 manifest 的归档列表 / 恢复照样认（按固定名字），不为它把已经搬完的删除算失败
    moved = {name for _source, name in plan}
    return {
        "archived_to": str(dest),
        "entry": dest.name,
        "output_moved": OUTPUT_NAME in moved,
        "assets_moved": ASSETS_NAME in moved,
        "assets_kept_for": assets_kept_for,
    }


# ---------------------------------------------------------------------------- 列表


def _dir_size(path: Path) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(base, name)).st_size
            except OSError:
                continue
    return total


def _stamp_iso(stamp: str) -> str:
    try:
        moment = datetime.strptime(stamp.split("-")[0], _STAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def entry_info(project_root: Path, module: str, entry_dir: Path) -> dict[str, Any]:
    """归档目录 → 列表项：id / 标题 / 删除时间 / 大小 / 里面有什么 / 能不能从界面恢复。spec.json 读不出来也照列。"""

    entry_dir = Path(entry_dir)
    name = entry_dir.name
    project_id, _sep, stamp = name.rpartition("__")
    manifest = read_manifest(entry_dir)
    title = str(manifest.get("title") or "")
    has_spec = (entry_dir / SPEC_NAME).is_file()
    # 项目文件本身是坏的（删除前列表里就是 status=error）：照样能恢复（原样放回），界面在这一行上先说一声
    spec_broken = False
    if has_spec:
        try:
            raw = _read_json(entry_dir / SPEC_NAME)
            if isinstance(raw, Mapping):
                project_id = str(raw.get("id") or project_id)
            else:
                spec_broken = True
            title = title or title_of(raw)
        except (OSError, ValueError):   # 列表要能扛住一个坏文件
            spec_broken = True
    has_ledger = (entry_dir / "ledger.json").is_file()
    if module == STORYVIDEO:
        restorable = has_spec or has_ledger
    else:
        restorable = module in GENERIC_MODULES and has_spec
    # 项目文件里的 id 被手改成不合法的（带 / 之类）：恢复接口会拒（「归档里的项目 id 不合法」），列表别标成能恢复
    if not is_valid_id(project_id):
        restorable = False
    return {
        "module": module,
        "entry": name,
        "id": project_id,
        "title": title,
        "archived_at": stamp,
        "deleted_at": str(manifest.get("deleted_at") or _stamp_iso(stamp)),
        "archived_to": relative(entry_dir, project_root),
        "bytes": _dir_size(entry_dir),
        "has_spec": has_spec,
        "spec_broken": spec_broken,
        "has_output": (entry_dir / OUTPUT_NAME).is_dir(),
        "has_assets": (entry_dir / ASSETS_NAME).is_dir(),
        "has_screenplay": (entry_dir / "screenplay.json").is_file(),
        "has_ledger": has_ledger,
        # 只剩账本记录（短剧）：项目文件和输出目录已经被手动搬回原位，恢复只把账本行放回去
        "ledger_only": has_ledger and not has_spec,
        "restorable": restorable,
        "has_manifest": bool(manifest),
    }


def list_entries(project_root: Path, module: str | None = None, *, root: Path | None = None,
                 exclude: Iterable[str] = ()) -> list[dict[str, Any]]:
    """``.webui_deleted/<模块>/<id>__<时间>/`` 里的归档，新的在前。只读。``module`` 不填列全部模块。

    ``exclude``：跳过这些模块目录，连目录大小都不算——回收站接口的短剧归档另走一路（归档目录单独记着），
    放在这里再列一遍会把最大的那批产物（首帧、视频）多走一遍磁盘再丢掉。"""

    base = root or deleted_root(project_root)
    if not base.is_dir():
        return []
    skip = set(exclude)
    out: list[dict[str, Any]] = []
    for module_dir in sorted(base.iterdir()):
        if not module_dir.is_dir() or module_dir.name in skip or (module and module_dir.name != module):
            continue
        for entry in module_dir.iterdir():
            if entry.is_dir() and "__" in entry.name and not entry.name.startswith("."):
                out.append(entry_info(project_root, module_dir.name, entry))
    out.sort(key=lambda item: (item["archived_at"], item["entry"]), reverse=True)
    return out


def archived_ids(project_root: Path, module: str, *, root: Path | None = None) -> list[str]:
    """回收站里这个模块还收着的项目 id（按归档目录名 ``<id>__<时间>`` 取，不读文件、不算大小）。

    自动编号要把它们也算作占用：只看 content/<模块>/ 的话，「删一个 → 当天再建一个」会把刚删的 id 原样再发出去，
    回收站里那一份从此 id 被占、恢复只能 409；409 让人先删掉新的，新的进了回收站又同样卡住，两份永远没法同时要回来。
    用户自己填的 id 不走这里（撞上了，恢复时照常 409 说清）。彻底删除中途留下的 ``.purging-`` 墓碑不算。"""

    base = (root or deleted_root(project_root)) / module
    try:
        names = [p.name for p in base.iterdir() if p.is_dir()]
    except OSError:   # 这个模块还没删过东西（目录不在）/ 读不了：只按现有项目算
        return []
    return sorted({name.rpartition("__")[0] for name in names if "__" in name and not name.startswith(".")})


def find_entry(project_root: Path, module: str, entry: str, *, root: Path | None = None) -> Path:
    """校验模块名 / 归档名的形状并返回归档目录；形状不对或目录不在都算「没有这条归档」。"""

    if not MODULE_RE.fullmatch(str(module or "")) or not ENTRY_RE.fullmatch(str(entry or "")):
        raise EntryNotFound(f"没有这条归档：{module}/{entry}")
    path = (root or deleted_root(project_root)) / module / entry
    if not path.is_dir():
        raise EntryNotFound(f"没有这条归档：{module}/{entry}")
    return path


# ---------------------------------------------------------------------------- 恢复（通用模块）


def restore(project_root: Path, module: str, entry: str) -> dict[str, Any]:
    """把一条通用模块的归档放回原位。同 id 的项目（或它的产物 / 素材目录）已经在 → ``RestoreConflict``，什么都不动；
    恢复不覆盖任何东西，也不只恢复一半（搬到一半失败就把搬过的搬回归档）。"""

    project_root = Path(project_root)
    if module not in GENERIC_MODULES:
        raise UnsupportedModule(f"暂不支持恢复这个模块的归档：{module}")
    with LOCK:
        source = find_entry(project_root, module, entry)
        if not (source / SPEC_NAME).is_file():
            raise EntryNotFound(f"没有这条归档：{module}/{entry}")
        # 项目文件本身是坏的（列表里 status=error 的项目照样能删）：按归档目录名里的 id 原样放回去，跟删除前一样在列表里
        # 标成出错，人能拿回文件自己修。拒绝恢复的话，回收站列着「能恢复」、点了却读不出来，文件也拿不回来
        try:
            raw = _read_json(source / SPEC_NAME)
        except (OSError, ValueError):
            raw = None
        spec_broken = not isinstance(raw, Mapping)
        project_id = str((raw.get("id") if isinstance(raw, Mapping) else "") or "").strip() or entry.rpartition("__")[0]
        if not is_valid_id(project_id):
            raise TrashError(f"归档里的项目 id 不合法：{project_id}")
        targets = layout(project_root, module, project_id)
        # 先把每一处目标都查一遍，任何一处已被占就整体拒绝。只报第一条：拦住的原因通常是同一个（同 id 的项目还在）
        if targets[SPEC_NAME].exists():
            raise RestoreConflict(f"没有恢复：项目 {project_id} 已经存在——先删除现在这个同 id 的项目（它也会进回收站），再恢复这一份")
        if (source / OUTPUT_NAME).exists() and targets[OUTPUT_NAME].exists():
            raise RestoreConflict(f"没有恢复：同 id 的产物目录已经存在（{relative(targets[OUTPUT_NAME], project_root)}）——"
                                  "先把它挪走或改名，再恢复这一份")
        if (source / ASSETS_NAME).exists() and targets[ASSETS_NAME].exists():
            raise RestoreConflict(f"没有恢复：同 id 的素材目录已经存在（{relative(targets[ASSETS_NAME], project_root)}）——"
                                  "先把它挪走或改名，再恢复这一份")
        # 项目文件最后放回：它一出现项目就进了列表，不能让人点进一个产物还没回来的项目
        plan = [(source / name, targets[name]) for name in (OUTPUT_NAME, ASSETS_NAME, SPEC_NAME) if (source / name).exists()]
        try:
            move_all(plan)
        except OSError as exc:
            raise FilesBusy(f"有文件正被占用，没恢复：{exc}——关掉正在打开归档里文件的程序再试", exc) from exc
        finish(source)
    restored = {SPEC_NAME: False, OUTPUT_NAME: False, ASSETS_NAME: False}
    for moved_from, _target in plan:
        restored[moved_from.name] = True
    return {
        "module": module,
        "id": project_id,
        "restored": {"spec": restored[SPEC_NAME], "output": restored[OUTPUT_NAME], "assets": restored[ASSETS_NAME]},
        "path": relative(targets[SPEC_NAME], project_root),
        # 放回去的项目文件读不出来：界面别跳进一个打不开的项目页，改为提示去修这个文件
        "spec_broken": spec_broken,
    }


# ---------------------------------------------------------------------------- 彻底删除


def _rmtree(path: Path) -> None:
    def _retry_writable(func: Any, target: str, *_exc: Any) -> None:
        # Windows 上只读文件（从别处拷来的素材常带只读位）rmtree 删不掉：去掉只读再删一次，还不行就照常抛
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry_writable)
    else:
        shutil.rmtree(path, onerror=_retry_writable)


def purge(project_root: Path, module: str, entry: str, *, root: Path | None = None) -> dict[str, Any]:
    """从磁盘上删掉一条归档（不可恢复）。先把整个目录改名成墓碑再删：改名在 Windows 上遇到被占用的文件会整体失败，
    这时一个文件都没删、归档原样留在回收站里；删墓碑中途失败就改回原名，留在列表里让人再点一次。"""

    with LOCK:
        path = find_entry(project_root, module, entry, root=root)
        size = _dir_size(path)
        tomb = path.with_name(f".purging-{entry}")
        if tomb.exists():   # 上次删到一半、又没能改回原名留下的墓碑（列表里看不到）：先清掉，不然改名撞名
            try:
                _rmtree(tomb)
            except OSError:
                pass
        try:
            os.rename(path, tomb)
        except OSError as exc:
            raise FilesBusy(f"有文件正被占用，没删除：{exc}——关掉正在打开归档里文件的程序再试", exc) from exc
        try:
            _rmtree(tomb)
        except OSError as exc:
            try:
                os.rename(tomb, path)
            except OSError:
                pass
            raise FilesBusy(f"有文件正被占用，没删干净：{exc}——关掉正在打开归档里文件的程序再试", exc) from exc
    return {"module": module, "entry": entry, "freed_bytes": size}


__all__ = [
    "DELETED_DIRNAME", "MANIFEST_NAME", "SPEC_NAME", "OUTPUT_NAME", "ASSETS_NAME", "GENERIC_MODULES", "STORYVIDEO", "ENTRY_RE",
    "TrashError", "EntryNotFound", "RestoreConflict", "FilesBusy", "UnsupportedModule",
    "deleted_root", "spec_target", "output_target", "assets_target", "layout", "relative", "new_entry_dir", "move", "move_all",
    "title_of", "write_manifest", "read_manifest", "finish", "asset_dir_users", "archive", "entry_info", "list_entries",
    "archived_ids", "find_entry", "restore", "purge", "LOCK", "TASKS_RUNNING",
]
