"""配乐项目文件：归一化 / 读 / 写 / 编号 / 归档（契约 docs/music-spec.md §2）。

跟绘本 ``picturebook.spec``、图片集 ``imageset.spec`` 一个思路：一处边界把形状校验完，后面的模块只认归一化
后的字典。归一化后多两个运行时键 ``output_root``（``output/music/<ID>``）与 ``project_root``，写盘时去掉
（``to_disk``）。

项目文件同时是产物清单：``attempts`` 记每次出的每一条（``{file, created_at, model, request_hash, task_id,
error}``），``current`` 指向采用的那条，文件在 ``01_raw/<ID>-<n>.<ext>``，**从不覆盖**。``used_in`` 记它被投放
到哪些项目（``{module, id, slot, at}``）。
"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from genkit import trash
from genkit.ids import ID_RE, next_episode_id
from genkit.paths import data_root as _data_root
from storyvideo.spec import SpecError, _resolve_local_asset

MODULE = "music"
ID_PREFIX = "MU"
CONTENT_DIR = Path("content") / "music"
OUTPUT_DIR = Path("output") / "music"
ASSETS_DIR = Path("content") / "assets"

PROMPT_LANGUAGES = ("zh", "en")
# 核心自己的上限（docs/music-spec.md §1「上限从哪来」）：只是项目文件的防手滑闸，不代表哪个来源的能力。
# 来源在能力记录里登记了更紧的数（``counts`` / ``reference_images`` / ``max_reference_images``）就按来源的来，见 ``source_limits``。
REFERENCE_SUFFIXES = {"jpg": ".jpg", "jpeg": ".jpg", "png": ".png"}   # 参考图用 jpg / png：图像接口普遍都认的两种格式
MAX_REFERENCES = 16
COUNT_MIN, COUNT_MAX = 1, 5
DURATION_MIN, DURATION_MAX = 5, 600
BPM_MIN, BPM_MAX = 30, 240
DEFAULT_DURATION = 60
DEFAULT_COUNT = 1
DEFAULT_MUSIC_PARAMS = {"model": "@music"}
AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
USE_MODULES = ("drama", "picturebook", "podcast")

_LIST_FIELDS = ("mood", "instruments", "avoid")


class MusicSpecError(SpecError):
    """配乐项目文件缺字段或形状不对。"""


# ---------------------------------------------------------------------------- 路径


def content_dir(project_root: Path) -> Path:
    return Path(project_root) / CONTENT_DIR


def spec_path(project_root: Path, spec_id: str) -> Path:
    return content_dir(project_root) / f"{spec_id}.json"


def existing_ids(project_root: Path) -> list[str]:
    root = content_dir(project_root)
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.json") if p.is_file())


def next_id(existing: Iterable[str], *, today: date | None = None) -> str:
    """MU-20260910-1、MU-20260910-2…（按天编号）。"""

    return next_episode_id(existing, prefix=ID_PREFIX, today=today)


def output_root(spec: Mapping[str, Any]) -> Path:
    return Path(str(spec["output_root"]))


def raw_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "01_raw"


def resolve_asset(spec: Mapping[str, Any], value: Any) -> Path | None:
    """项目相对的素材路径 → 数据目录 content/ 或 output/ 里存在的绝对路径；不存在 / 空 / 越界 → None。"""

    try:
        resolved = _resolve_local_asset(value, Path(str(spec.get("project_root") or _data_root())))
    except SpecError:
        return None   # 越界的写法（~、绝对路径、盘符、..、指向外面的链接）当没有这个文件：不读数据目录外的东西
    return Path(resolved) if resolved else None


def reference_paths(spec: Mapping[str, Any]) -> list[Path]:
    """参考图里**文件真的在**的那些（生成时按这个列表上传）。"""

    out: list[Path] = []
    for entry in spec.get("reference_images") or ():
        path = resolve_asset(spec, entry)
        if path is not None:
            out.append(path)
    # 老项目文件 / 手改 JSON 里同一张图可能记了两条：请求里不带重复的 asset（跟图片集的 reference_assets 对齐）
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------- 上限：核心的 + 来源登记的


def _positive(value: Any, default: int | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


def limits_from_caps(caps: Mapping[str, Any] | None) -> dict[str, Any]:
    """一条能力记录 → 这个来源下的上限（形状跟接口返回的 ``limits`` 一样）。

    记录里没写的项用核心上限；写了只会收紧、不会放宽：``counts`` = 一轮能出几条（``{min, max}``），
    ``reference_images: False`` = 不收参考图，``max_reference_images`` = 一次最多几张。"""

    caps = caps if isinstance(caps, Mapping) else {}
    low, high = COUNT_MIN, COUNT_MAX
    counts = caps.get("counts")
    if isinstance(counts, Mapping):
        high = min(high, _positive(counts.get("max"), None) or high)
        low = min(high, max(low, _positive(counts.get("min"), None) or low))
    references = MAX_REFERENCES
    if caps.get("reference_images") is False:
        references = 0
    else:
        declared = _positive(caps.get("max_reference_images"), None)
        if declared is not None:
            references = min(references, declared)
    return {"count": [low, high], "duration": [DURATION_MIN, DURATION_MAX], "bpm": [BPM_MIN, BPM_MAX],
            "max_references": references}


def source_caps(settings: Any = None) -> dict[str, Any]:
    """「模型设置」里配乐角色当前来源的能力记录；没选来源 / 设置读不出 → ``{}``（按核心上限）。"""

    try:
        from genkit import capabilities
        from genkit import settings as settings_mod

        current = settings if settings is not None else settings_mod.load_settings()
        role = current.role("music")
        if not role.adapter:
            return {}
        model = settings_mod.effective(role, "music").model
        return dict(capabilities.lookup("music", role.adapter, model or "", base_url=role.base_url or ""))
    except Exception:  # noqa: BLE001 - 上限只是防手滑：设置坏了就按核心上限，报错交给「模型设置」页
        return {}


def source_limits(settings: Any = None) -> dict[str, Any]:
    """当前配乐来源下的上限：核心上限，再按来源登记的能力收紧。"""

    return limits_from_caps(source_caps(settings))


# ---------------------------------------------------------------------------- 归一化


def _text(value: Any, field: str, *, required: bool = False, default: str = "") -> str:
    if value is None:
        if required:
            raise MusicSpecError(f"{field} 不能为空")
        return default
    if not isinstance(value, str):
        raise MusicSpecError(f"{field} 要是文本")
    out = value.strip()
    if required and not out:
        raise MusicSpecError(f"{field} 不能为空")
    return out


def _int(value: Any, field: str, *, default: int, minimum: int, maximum: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise MusicSpecError(f"{field} 要是整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MusicSpecError(f"{field} 要是整数") from exc
    if not minimum <= number <= maximum:
        raise MusicSpecError(f"{field} 要在 {minimum} 到 {maximum} 之间")
    return number


def _str_list(raw: Any, field: str) -> list[str]:
    """数组或「顿号 / 逗号分隔的一行」都认（表单里人常常直接打一行）。"""

    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        parts = re.split(r"[、,，/]", raw)
    elif isinstance(raw, list):
        parts = [str(v) for v in raw]
    else:
        raise MusicSpecError(f"{field} 要是数组（或用顿号分隔的一行）")
    return [p.strip() for p in parts if str(p).strip()]


def _relative_path(value: Any, field: str) -> str:
    text = _text(value, field, required=True)
    if "\\" in text or text.startswith("/") or (len(text) > 1 and text[1] == ":") or ".." in Path(text).parts:
        raise MusicSpecError(f"{field} 只能是项目内的相对路径（content/assets/…）")
    return text


def _brief(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise MusicSpecError("brief 要是对象（usage / mood / genre / instruments / bpm / structure / avoid）")
    bpm = raw.get("bpm")
    return {
        "usage": _text(raw.get("usage"), "brief.usage"),
        "mood": _str_list(raw.get("mood"), "brief.mood"),
        "genre": _text(raw.get("genre"), "brief.genre"),
        "instruments": _str_list(raw.get("instruments"), "brief.instruments"),
        "bpm": None if bpm in (None, "") else _int(bpm, "brief.bpm", default=0, minimum=BPM_MIN, maximum=BPM_MAX),
        "structure": _text(raw.get("structure"), "brief.structure"),
        "avoid": _str_list(raw.get("avoid"), "brief.avoid"),
    }


def _references(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise MusicSpecError("reference_images 要是数组（参考图路径）")
    out: list[str] = []
    for index, entry in enumerate(raw):
        field = f"reference_images[{index}]"
        path = _relative_path(entry["file"] if isinstance(entry, Mapping) else entry, field)
        if not path.lower().endswith(tuple(REFERENCE_SUFFIXES.values())):
            raise MusicSpecError(f"{field} 只接受 jpg / png 参考图")
        out.append(path)
    if len(out) > MAX_REFERENCES:
        raise MusicSpecError(f"参考图最多 {MAX_REFERENCES} 张，现在有 {len(out)} 张")
    return out


def _music_params(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise MusicSpecError("music_params 要是对象（model / extra_params）")
    extra = raw.get("extra_params")
    if extra is not None and not isinstance(extra, Mapping):
        raise MusicSpecError("music_params.extra_params 要是对象")
    out: dict[str, Any] = {"model": _text(raw.get("model"), "music_params.model", default="@music") or "@music"}
    if extra:
        out["extra_params"] = dict(extra)
    return out


def _attempt(raw: Any, field: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise MusicSpecError(f"{field} 要是对象")
    return {
        "file": _text(raw.get("file"), f"{field}.file"),
        "created_at": raw.get("created_at"),
        "model": _text(raw.get("model"), f"{field}.model"),
        "request_hash": _text(raw.get("request_hash"), f"{field}.request_hash"),
        "task_id": raw.get("task_id"),
        "error": _text(raw.get("error"), f"{field}.error") or None,
    }


def _attempts(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise MusicSpecError("attempts 要是数组")
    return [_attempt(a, f"attempts[{i}]") for i, a in enumerate(raw)]


def _used_in(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise MusicSpecError("used_in 要是数组")
    out: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        field = f"used_in[{index}]"
        if not isinstance(entry, Mapping):
            raise MusicSpecError(f"{field} 要是对象（module / id / slot / at）")
        module = _text(entry.get("module"), f"{field}.module", required=True)
        if module not in USE_MODULES:
            raise MusicSpecError(f"{field}.module 只能是 {' / '.join(USE_MODULES)}，收到「{module}」")
        out.append({
            "module": module,
            "id": _text(entry.get("id"), f"{field}.id", required=True),
            "slot": _text(entry.get("slot"), f"{field}.slot"),
            "at": entry.get("at"),
        })
    return out


def normalize(data: Mapping[str, Any], *, project_root: str | os.PathLike[str] | None = None,
              spec_id: str | None = None) -> dict[str, Any]:
    """校验 + 补默认值。``spec_id`` 给了就覆盖 / 补上 id（新建时用）。"""

    if not isinstance(data, Mapping):
        raise MusicSpecError("项目文件要是一个 JSON 对象")
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else _data_root()
    raw = copy.deepcopy(dict(data))
    identifier = _text(spec_id if spec_id is not None else raw.get("id"), "id")
    if not identifier:
        raise MusicSpecError("id 不能为空——新建时由服务端编号")
    if not ID_RE.fullmatch(identifier):
        raise MusicSpecError("id 只能用英文、数字、下划线、点、短横线")
    attempts = _attempts(raw.get("attempts"))
    current = _text(raw.get("current"), "current") or None
    if current and current not in {a["file"] for a in attempts}:
        raise MusicSpecError(f"current「{current}」不在 attempts 里——只能采用出过的那几条")
    normalized: dict[str, Any] = {
        "id": identifier,
        "module": MODULE,
        "title": _text(raw.get("title"), "title", required=True),
        "brief": _brief(raw.get("brief")),
        "prompt_language": _choice(raw.get("prompt_language"), "prompt_language", PROMPT_LANGUAGES, default="zh"),
        "duration": _int(raw.get("duration"), "duration", default=DEFAULT_DURATION, minimum=DURATION_MIN, maximum=DURATION_MAX),
        "count": _int(raw.get("count"), "count", default=DEFAULT_COUNT, minimum=COUNT_MIN, maximum=COUNT_MAX),
        "reference_images": _references(raw.get("reference_images")),
        "music_params": _music_params(raw.get("music_params")),
        "attempts": attempts,
        "current": current,
        "used_in": _used_in(raw.get("used_in")),
    }
    file_module = _text(raw.get("module"), "module")
    if file_module and file_module != MODULE:
        raise MusicSpecError(f"项目文件的 module 是「{file_module}」，跟这个模块（{MODULE}）对不上")
    normalized["output_root"] = str((root / OUTPUT_DIR / identifier).resolve(strict=False))
    normalized["project_root"] = str(root)
    return normalized


def _choice(value: Any, field: str, choices: tuple[str, ...], *, default: str | None = None) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            raise MusicSpecError(f"{field} 不能为空，只能是 {' / '.join(choices)}")
        return default
    text = str(value).strip()
    if text not in choices:
        raise MusicSpecError(f"{field} 只能是 {' / '.join(choices)}，收到「{text}」")
    return text


def to_disk(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in spec.items() if k not in ("output_root", "project_root")}


def load(spec_id: str, *, project_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else _data_root()
    if not ID_RE.fullmatch(str(spec_id or "")):
        raise MusicSpecError(f"没有这个配乐：{spec_id}")
    path = spec_path(root, spec_id)
    if not path.is_file():
        raise MusicSpecError(f"没有这个配乐：{spec_id}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise MusicSpecError(f"读不了项目文件 {path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise MusicSpecError(f"项目文件不是合法的 JSON（{path} 第 {exc.lineno} 行）") from exc
    if isinstance(data, Mapping) and str(data.get("id") or "") not in ("", spec_id):
        raise MusicSpecError(f"项目文件里的 id「{data.get('id')}」跟文件名 {spec_id} 对不上")
    return normalize(data, project_root=root, spec_id=spec_id)


def save(spec: Mapping[str, Any]) -> Path:
    """归一化后的 spec 原子写盘（``to_disk`` 形状）；返回路径。"""

    from storyvideo.state import atomic_write_json

    target = spec_path(Path(str(spec["project_root"])), str(spec["id"]))
    atomic_write_json(target, to_disk(spec))
    return target


def archive(spec_id: str, *, project_root: Path) -> dict[str, Any]:
    """归档到 ``.webui_deleted/music/<ID>__<时间>/``（项目文件 + 产物目录 + 上传的参考图一起搬，附 manifest），不是 ``os.remove``。

    实现在 ``genkit.trash``：回收站页按它列出、恢复；有文件被占用时抛 ``trash.FilesBusy``，什么都不动。"""

    path = spec_path(project_root, spec_id)
    if not ID_RE.fullmatch(str(spec_id or "")) or not path.is_file():
        raise MusicSpecError(f"没有这个配乐：{spec_id}")
    return trash.archive(Path(project_root), MODULE, spec_id)


# ---------------------------------------------------------------------------- 产物


def attempt_path(spec: Mapping[str, Any], name: str) -> Path | None:
    """attempts 里某个文件名对应的、**真的在盘上**的路径；不在返回 None。"""

    if not name or name != Path(name).name:
        return None
    known = {str(a.get("file")) for a in spec.get("attempts") or () if a.get("file") and not a.get("error")}
    if name not in known:
        return None
    path = raw_dir(spec) / name
    return path if path.is_file() else None


def current_path(spec: Mapping[str, Any]) -> Path | None:
    current = spec.get("current")
    return attempt_path(spec, str(current)) if current else None


def ok_attempts(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [a for a in spec.get("attempts") or () if a.get("file") and not a.get("error")]


def record_use(spec: dict[str, Any], *, module: str, target_id: str, slot: str = "") -> dict[str, Any]:
    """往 ``used_in`` 追一条（同一个目标同一个位置只留最新一条）。就地改并返回这条记录。"""

    entry = {"module": module, "id": target_id, "slot": str(slot or ""), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rows = [r for r in spec.get("used_in") or [] if not (r.get("module") == module and r.get("id") == target_id and str(r.get("slot") or "") == entry["slot"])]
    rows.append(entry)
    spec["used_in"] = rows
    return entry


__all__ = [
    "MODULE", "ID_PREFIX", "CONTENT_DIR", "OUTPUT_DIR", "ASSETS_DIR", "PROMPT_LANGUAGES", "REFERENCE_SUFFIXES",
    "MAX_REFERENCES", "COUNT_MIN", "COUNT_MAX", "DURATION_MIN", "DURATION_MAX", "BPM_MIN", "BPM_MAX",
    "DEFAULT_DURATION", "DEFAULT_COUNT", "DEFAULT_MUSIC_PARAMS", "AUDIO_SUFFIXES", "USE_MODULES", "MusicSpecError",
    "limits_from_caps", "source_caps", "source_limits",
    "content_dir", "spec_path", "existing_ids", "next_id", "output_root", "raw_dir", "resolve_asset", "reference_paths",
    "normalize", "to_disk", "load", "save", "archive", "attempt_path", "current_path", "ok_attempts", "record_use",
]
