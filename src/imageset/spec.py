"""图片集项目文件：归一化 / 读 / 写 / 编号 / 归档（契约 §1）。

跟绘本的 ``picturebook.spec`` 一个思路：一处边界把形状校验完，后面的模块只认归一化后的字典。归一化后多两个
运行时键 ``output_root``（``output/<module>/<ID>``）与 ``project_root``，写盘时去掉（``to_disk``）。

项目文件同时也是产物清单：``items[].attempts`` 记每次出图，``items[].current`` 指向采用的那张，文件在
``01_raw/<item>-<n>.png``，**从不覆盖**。后处理产物另有 ``05_out/post.json``（见 post.py）。
"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from genkit import trash
from genkit.ids import ID_RE, next_episode_id
from genkit.paths import data_root as _data_root
from storyvideo.spec import SpecError, _resolve_local_asset

MODULES = ("sticker", "portrait", "image")
ID_PREFIX = {"sticker": "ST", "portrait": "PT", "image": "IM"}
ASSETS_DIR = Path("content") / "assets"
SUBJECT_KINDS = ("character", "photo")
WHO_CHOICES = ("", "bride", "groom")
MAX_ASSETS = 6
ITEM_STATUSES = ("todo", "done", "failed")
PROMPT_LANGUAGES = ("zh", "en")
# 图像方言（DESIGN §41）：可空；auto 按 image_params.model 猜，缺省 = generic（老项目提示词逐字节不变）
IMAGE_DIALECT_CHOICES = ("auto", "generic", "gpt_image", "nano_banana")
DEFAULT_IMAGE_PARAMS = {"model": "@image", "image_size": "2K"}
DEFAULT_FIDELITY = 85
_ITEM_ID_RE = re.compile(r"I\d{2,3}\Z")


class SetSpecError(SpecError):
    """图片集项目文件缺字段或形状不对。"""


# ---------------------------------------------------------------------------- 路径


def check_module(module: str) -> str:
    module = str(module or "").strip()
    if module not in MODULES:
        raise SetSpecError(f"module 只能是 {' / '.join(MODULES)}，收到「{module}」")
    return module


def content_dir(project_root: Path, module: str) -> Path:
    return Path(project_root) / "content" / check_module(module)


def output_dir(module: str) -> Path:
    return Path("output") / check_module(module)


def spec_path(project_root: Path, module: str, spec_id: str) -> Path:
    return content_dir(project_root, module) / f"{spec_id}.json"


def existing_ids(project_root: Path, module: str) -> list[str]:
    root = content_dir(project_root, module)
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.json") if p.is_file())


def next_id(module: str, existing: Iterable[str], *, today: date | None = None) -> str:
    """ST-20260909-1 / PT-20260909-1 / IM-20260910-1…（按天编号）。"""

    return next_episode_id(existing, prefix=ID_PREFIX[check_module(module)], today=today)


def output_root(spec: Mapping[str, Any]) -> Path:
    return Path(str(spec["output_root"]))


def raw_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "01_raw"


def out_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "05_out"


def export_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "09_export"


def resolve_asset(spec: Mapping[str, Any], value: Any) -> Path | None:
    """项目相对的素材路径 → 数据目录 content/ 或 output/ 里存在的绝对路径；不存在 / 空 / 越界 → None。"""

    try:
        resolved = _resolve_local_asset(value, Path(str(spec.get("project_root") or _data_root())))
    except SpecError:
        return None   # 越界的写法（~、绝对路径、盘符、..、指向外面的链接）当没有这个文件：不读数据目录外的东西
    return Path(resolved) if resolved else None


def asset_entries(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``subject.assets`` 统一成 ``[{file, who, path}]``（``path`` = 存在的绝对路径或 None）。"""

    out: list[dict[str, Any]] = []
    for entry in (spec.get("subject") or {}).get("assets") or ():
        file = entry["file"] if isinstance(entry, Mapping) else str(entry)
        who = str(entry.get("who") or "") if isinstance(entry, Mapping) else ""
        path = resolve_asset(spec, file)
        out.append({"file": file, "who": who, "path": str(path) if path else None})
    return out


# ---------------------------------------------------------------------------- 归一化


def _text(value: Any, field: str, *, required: bool = False, default: str = "") -> str:
    if value is None:
        if required:
            raise SetSpecError(f"{field} 不能为空")
        return default
    if not isinstance(value, str):
        raise SetSpecError(f"{field} 要是文本")
    out = value.strip()
    if required and not out:
        raise SetSpecError(f"{field} 不能为空")
    return out


def _int(value: Any, field: str, *, default: int, minimum: int, maximum: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise SetSpecError(f"{field} 要是整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SetSpecError(f"{field} 要是整数") from exc
    if not minimum <= number <= maximum:
        raise SetSpecError(f"{field} 要在 {minimum} 到 {maximum} 之间")
    return number


def _choice(value: Any, field: str, choices: tuple[str, ...], *, default: str | None = None) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            raise SetSpecError(f"{field} 不能为空，只能是 {' / '.join(c for c in choices if c)}")
        return default
    text = str(value).strip()
    if text not in choices:
        raise SetSpecError(f"{field} 只能是 {' / '.join(c for c in choices if c)}，收到「{text}」")
    return text


def _relative_path(value: Any, field: str) -> str:
    text = _text(value, field, required=True)
    if "\\" in text or text.startswith("/") or (len(text) > 1 and text[1] == ":") or ".." in Path(text).parts:
        raise SetSpecError(f"{field} 只能是项目内的相对路径（content/assets/…）")
    return text


def _assets(raw: Any) -> list[Any]:
    """字符串（普通参考图）或 ``{file, who}``（婚纱照标新娘 / 新郎）；最多 6 张。"""

    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SetSpecError("subject.assets 要是数组（参考图路径）")
    out: list[Any] = []
    for index, entry in enumerate(raw):
        field = f"subject.assets[{index}]"
        if isinstance(entry, Mapping):
            file = _relative_path(entry.get("file"), f"{field}.file")
            who = _choice(entry.get("who"), f"{field}.who", WHO_CHOICES, default="")
            out.append({"file": file, "who": who} if who else file)
        else:
            out.append(_relative_path(entry, field))
    if len(out) > MAX_ASSETS:
        raise SetSpecError(f"参考图最多 {MAX_ASSETS} 张，现在有 {len(out)} 张")
    return out


def _subject(raw: Any, *, module: str) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise SetSpecError("subject 要是对象（kind / name / block / assets / fidelity）")
    default_kind = "photo" if module == "portrait" else "character"
    kind = _choice(raw.get("kind"), "subject.kind", SUBJECT_KINDS, default=default_kind)
    return {
        "kind": kind,
        "name": _text(raw.get("name"), "subject.name"),
        "block": _text(raw.get("block"), "subject.block"),
        "assets": _assets(raw.get("assets")),
        "fidelity": _int(raw.get("fidelity"), "subject.fidelity", default=DEFAULT_FIDELITY, minimum=0, maximum=100),
    }


def _style(raw: Any, template: Any) -> str:
    """``style``：模板预设 id > 风格卡 id / 名 / 别名（DESIGN §41）> ``custom``。缺省 = 模板第一个预设。"""

    from imagecraft.cards import find_card

    style_ids = tuple(s["id"] for s in template.styles)
    value = raw.strip() if isinstance(raw, str) else raw
    if value is None or value == "":
        return style_ids[0]
    text = str(value).strip()
    if text in style_ids or text == "custom":
        return text
    card = find_card(text)
    if card is None:
        raise SetSpecError(f"style 只能是模板预设 id（{' / '.join(style_ids)}）、风格卡 id 或 custom，收到「{text}」")
    return str(card["id"])


def _image_dialect(raw: Any) -> str:
    """``image_dialect``（可空）：``auto`` 按模型猜，缺省 = 空串 = 老项目按 generic 写，输出逐字节不变。"""

    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ""
    return _choice(raw, "image_dialect", IMAGE_DIALECT_CHOICES)


def _image_params(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise SetSpecError("image_params 要是对象（model / image_size）")
    return {
        "model": _text(raw.get("model"), "image_params.model", default="@image") or "@image",
        "image_size": _text(raw.get("image_size"), "image_params.image_size", default="2K") or "2K",
    }


def _options(raw: Any, schema: list[Mapping[str, Any]]) -> dict[str, Any]:
    """按模板的 options_schema 校验：list（字符串数组）/ bool / string / int，缺省补默认。不认识的键原样保留。"""

    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise SetSpecError("options 要是对象")
    out: dict[str, Any] = dict(raw)
    for field in schema:
        key = str(field["key"])
        kind = str(field.get("type") or "string")
        value = raw.get(key)
        default = copy.deepcopy(field.get("default"))
        if kind == "list":
            if value is None:
                out[key] = list(default or [])
                continue
            if not isinstance(value, list):
                raise SetSpecError(f"options.{key} 要是数组")
            items = [str(v).strip() for v in value if str(v).strip()]
            if not items:
                raise SetSpecError(f"options.{key} 至少要有一项")
            out[key] = items
        elif kind == "bool":
            out[key] = bool(default) if value is None else bool(value)
        elif kind == "int":
            out[key] = _int(value, f"options.{key}", default=int(default or 0),
                            minimum=int(field.get("min", 0)), maximum=int(field.get("max", 99)))
        else:
            text = _text(value, f"options.{key}", default=str(default or ""))
            choices = field.get("choices")
            if choices:
                ids = tuple(str(c["id"] if isinstance(c, Mapping) else c) for c in choices)
                text = _choice(text or None, f"options.{key}", ids, default=str(default or ids[0]))
            out[key] = text
    return out


def _attempt(raw: Any, field: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise SetSpecError(f"{field} 要是对象")
    return {
        "file": _text(raw.get("file"), f"{field}.file"),
        "created_at": raw.get("created_at"),
        "model": _text(raw.get("model"), f"{field}.model"),
        "request_hash": _text(raw.get("request_hash"), f"{field}.request_hash"),
        "task_id": raw.get("task_id"),
        "error": _text(raw.get("error"), f"{field}.error") or None,
    }


def _items(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SetSpecError("items 要是数组")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise SetSpecError(f"items[{index}] 要是对象")
        item_id = _text(item.get("id"), f"items[{index}].id") or f"I{index + 1:02d}"
        if not _ITEM_ID_RE.fullmatch(item_id):
            raise SetSpecError(f"items[{index}].id 要写成 I01 这样的形状，收到「{item_id}」")
        if item_id in seen:
            raise SetSpecError(f"图片 id 重复：{item_id}")
        seen.add(item_id)
        attempts = [_attempt(a, f"item {item_id}.attempts[{i}]") for i, a in enumerate(item.get("attempts") or [])]
        current = _text(item.get("current"), f"item {item_id}.current") or None
        out.append({
            "id": item_id,
            "label": _text(item.get("label"), f"item {item_id}.label"),
            "prompt": _text(item.get("prompt"), f"item {item_id}.prompt"),
            "negative": _text(item.get("negative"), f"item {item_id}.negative"),
            "aspect_ratio": _text(item.get("aspect_ratio"), f"item {item_id}.aspect_ratio", default="1:1") or "1:1",
            "meta": dict(item.get("meta") or {}) if isinstance(item.get("meta"), Mapping) else {},
            "status": _choice(item.get("status"), f"item {item_id}.status", ITEM_STATUSES, default="todo"),
            "current": current,
            "request_hash": _text(item.get("request_hash"), f"item {item_id}.request_hash") or None,
            "error": _text(item.get("error"), f"item {item_id}.error") or None,
            "attempts": attempts,
        })
    return out


def _picks(raw: Any, items: list[dict[str, Any]]) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SetSpecError("picks 要是数组（图片 id）")
    ids = {i["id"] for i in items}
    out: list[str] = []
    for pick in raw:
        pick = str(pick).strip()
        if not pick:
            continue
        if pick not in ids:
            raise SetSpecError(f"picks 里的「{pick}」不是这个项目的图片 id")
        if pick not in out:
            out.append(pick)
    return out


def normalize(data: Mapping[str, Any], *, module: str, project_root: str | os.PathLike[str] | None = None,
              spec_id: str | None = None) -> dict[str, Any]:
    """校验 + 补默认值。``spec_id`` 给了就覆盖 / 补上 id（新建时用）；``module`` 由路由前缀决定，文件里写别的模块就拒绝。"""

    from .templates import get_template

    if not isinstance(data, Mapping):
        raise SetSpecError("项目文件要是一个 JSON 对象")
    module = check_module(module)
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else _data_root()
    raw = copy.deepcopy(dict(data))
    identifier = _text(spec_id if spec_id is not None else raw.get("id"), "id")
    if not identifier:
        raise SetSpecError("id 不能为空——新建时由服务端编号")
    if not ID_RE.fullmatch(identifier):
        raise SetSpecError("id 只能用英文、数字、下划线、点、短横线")
    file_module = _text(raw.get("module"), "module")
    if file_module and file_module != module:
        raise SetSpecError(f"项目文件的 module 是「{file_module}」，跟这个模块（{module}）对不上")
    template_id = _text(raw.get("template"), "template", required=True)
    template = get_template(module, template_id)
    if template is None:
        from .templates import template_ids

        raise SetSpecError(f"template 只能是 {' / '.join(template_ids(module))}，收到「{template_id}」")
    style = _style(raw.get("style"), template)
    style_anchor = _text(raw.get("style_anchor"), "style_anchor")
    if style == "custom" and not style_anchor:
        raise SetSpecError("style 选 custom 时 style_anchor 不能为空（自写风格锚）")
    items = _items(raw.get("items"))
    normalized: dict[str, Any] = {
        "id": identifier,
        "module": module,
        "title": _text(raw.get("title"), "title", required=True),
        "template": template_id,
        "subject": _subject(raw.get("subject"), module=module),
        "style": style,
        "style_anchor": style_anchor,
        "image_dialect": _image_dialect(raw.get("image_dialect")),
        "prompt_language": _choice(raw.get("prompt_language"), "prompt_language", PROMPT_LANGUAGES, default="zh"),
        "options": _options(raw.get("options"), template.options_schema),
        "image_params": _image_params(raw.get("image_params")),
        "items": items,
        "picks": _picks(raw.get("picks"), items),
    }
    normalized["output_root"] = str((root / output_dir(module) / identifier).resolve(strict=False))
    normalized["project_root"] = str(root)
    return normalized


def to_disk(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in spec.items() if k not in ("output_root", "project_root")}


def load(module: str, spec_id: str, *, project_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else _data_root()
    module = check_module(module)
    if not ID_RE.fullmatch(str(spec_id or "")):
        raise SetSpecError(f"没有这个项目：{spec_id}")
    path = spec_path(root, module, spec_id)
    if not path.is_file():
        raise SetSpecError(f"没有这个项目：{spec_id}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise SetSpecError(f"读不了项目文件 {path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise SetSpecError(f"项目文件不是合法的 JSON（{path} 第 {exc.lineno} 行）") from exc
    if isinstance(data, Mapping) and str(data.get("id") or "") not in ("", spec_id):
        raise SetSpecError(f"项目文件里的 id「{data.get('id')}」跟文件名 {spec_id} 对不上")
    return normalize(data, module=module, project_root=root, spec_id=spec_id)


def save(spec: Mapping[str, Any]) -> Path:
    """归一化后的 spec 原子写盘（``to_disk`` 形状）；返回路径。"""

    from storyvideo.state import atomic_write_json

    target = spec_path(Path(str(spec["project_root"])), str(spec["module"]), str(spec["id"]))
    atomic_write_json(target, to_disk(spec))
    return target


def archive(module: str, spec_id: str, *, project_root: Path) -> dict[str, Any]:
    """归档到 ``.webui_deleted/<module>/<ID>__<时间>/``（项目文件 + 产物目录 + 上传的参考图一起搬，附 manifest），不是 ``os.remove``。

    实现在 ``genkit.trash``：回收站页按它列出、恢复；有文件被占用时抛 ``trash.FilesBusy``，什么都不动。"""

    module = check_module(module)
    path = spec_path(project_root, module, spec_id)
    if not ID_RE.fullmatch(str(spec_id or "")) or not path.is_file():
        raise SetSpecError(f"没有这个项目：{spec_id}")
    return trash.archive(Path(project_root), module, spec_id)


def item_by_id(spec: Mapping[str, Any], item_id: str) -> dict[str, Any]:
    for item in spec.get("items") or ():
        if str(item.get("id")) == item_id:
            return item
    raise SetSpecError(f"没有这张图：{item_id}")


def item_ids(spec: Mapping[str, Any]) -> list[str]:
    return [str(i["id"]) for i in spec.get("items") or ()]


def picked_items(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """挑选结果（空 = 全部）里**已有 current 文件**的 items。"""

    picks = spec.get("picks") or []
    rows = [i for i in spec.get("items") or () if not picks or i["id"] in picks]
    return [i for i in rows if current_path(spec, i) is not None]


def current_path(spec: Mapping[str, Any], item: Mapping[str, Any]) -> Path | None:
    current = item.get("current")
    if not current:
        return None
    path = raw_dir(spec) / str(current)
    return path if path.is_file() else None


__all__ = [
    "MODULES", "ID_PREFIX", "ASSETS_DIR", "SUBJECT_KINDS", "WHO_CHOICES", "MAX_ASSETS", "ITEM_STATUSES",
    "PROMPT_LANGUAGES", "IMAGE_DIALECT_CHOICES", "SetSpecError",
    "check_module", "content_dir", "output_dir", "spec_path", "existing_ids", "next_id", "output_root", "raw_dir", "out_dir",
    "export_dir", "resolve_asset", "asset_entries", "normalize", "to_disk", "load", "save", "archive", "item_by_id", "item_ids",
    "picked_items", "current_path",
]
