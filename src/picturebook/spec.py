"""绘本项目文件：归一化 / 读 / 写 / 编号（契约 §1）。

跟短剧的 ``storyvideo.spec`` 一个思路：一处边界把形状校验完，后面的模块只认归一化后的字典。
归一化后的字典多两个键：``output_root``（绝对路径，``output/picturebook/<ID>``）和 ``project_root``；
写盘时把这两个去掉（``to_disk``），项目文件里永远只有相对路径和用户填的内容。
"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from genkit.ids import ID_RE, next_episode_id
from genkit.paths import data_root as _data_root
from storyvideo.spec import SpecError, _resolve_local_asset

BOOKS_DIR = Path("content") / "picturebook"
OUTPUT_DIR = Path("output") / "picturebook"
ASSETS_DIR = Path("content") / "assets"

AGE_BANDS = ("2-4", "4-6", "6-8")
BILINGUAL_MODES = ("sentence_alternate", "page_zh_then_en", "zh_only", "en_only")
KB_DIRS = ("push", "pull", "left", "right", "up", "down")
PAGE_KINDS = ("cover", "page", "back")
FLIPS = ("page_turn", "cut")
ASPECT_RATIOS = ("9:16", "16:9")
_PAGE_ID_RE = re.compile(r"P\d{2,3}\Z")

DEFAULT_STYLE_ANCHOR = "Soft watercolor picture-book art, rounded friendly shapes, airy pastel washes, visible paper grain"
DEFAULT_NEGATIVE = "photo, 3D render, text, letters, watermark, frightening, gloomy"
DEFAULT_PAUSE = {"lead": 0.4, "between": 0.6, "tail": 0.8}
DEFAULT_NARRATION = {"zh_voice": "", "en_voice": "", "en_rate": "-8%", "pause": dict(DEFAULT_PAUSE)}
DEFAULT_DELIVERY = {"aspect_ratio": "9:16", "resolution": "1080p", "landscape_variant": False}
DEFAULT_IMAGE_PARAMS = {"model": "@image", "aspect_ratio": "3:4", "image_size": "2K"}
DEFAULT_SUBTITLE = {"font_path": None, "zh_size": 96, "en_size": 64, "highlight": "#FFD27A"}
DEFAULT_MUSIC_GAIN = 0.25
DEFAULT_MUSIC_FADE = 3.0


class BookSpecError(SpecError):
    """绘本项目文件缺字段或形状不对。"""


# ---------------------------------------------------------------------------- 路径


def books_dir(project_root: Path) -> Path:
    return Path(project_root) / BOOKS_DIR


def book_path(project_root: Path, book_id: str) -> Path:
    return books_dir(project_root) / f"{book_id}.json"


def existing_book_ids(project_root: Path) -> list[str]:
    root = books_dir(project_root)
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.json"))


def next_book_id(existing: Iterable[str], *, today: date | None = None) -> str:
    """PB-20260909-1、PB-20260909-2…（按天编号）。"""

    return next_episode_id(existing, prefix="PB", today=today)


def output_root(spec: Mapping[str, Any]) -> Path:
    return Path(str(spec["output_root"]))


def assets_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "04_assets"


def pages_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "05_pages"


def audio_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "07_audio"


def edit_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "07_edit"


def export_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "09_export"


def manifests_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "manifests"


def resolve_asset(spec: Mapping[str, Any], value: Any) -> Path | None:
    """项目相对的素材路径 → 数据目录 content/ 或 output/ 里存在的绝对路径；不存在 / 空 / 越界 → None。"""

    try:
        resolved = _resolve_local_asset(value, Path(str(spec.get("project_root") or _data_root())))
    except SpecError:
        return None   # 越界的写法（~、绝对路径、盘符、..、指向外面的链接）当没有这个文件：不读数据目录外的东西
    return Path(resolved) if resolved else None


def prompt_language(spec: Mapping[str, Any]) -> str:
    """提示词语言：只有 en_only 用英文；其余中文（契约 §4）。"""

    return "en" if str(spec.get("bilingual_mode") or "") == "en_only" else "zh"


# ---------------------------------------------------------------------------- 归一化


def _text(value: Any, field: str, *, required: bool = False, default: str = "") -> str:
    if value is None:
        if required:
            raise BookSpecError(f"{field} 不能为空")
        return default
    if not isinstance(value, str):
        raise BookSpecError(f"{field} 要是文本")
    out = value.strip()
    if required and not out:
        raise BookSpecError(f"{field} 不能为空")
    return out


def _number(value: Any, field: str, *, default: float, minimum: float, maximum: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise BookSpecError(f"{field} 要是数字") from exc
    if not minimum <= float(value) <= maximum:
        raise BookSpecError(f"{field} 要在 {minimum:g} 到 {maximum:g} 之间")
    return float(value)


def _choice(value: Any, field: str, choices: tuple[str, ...], *, default: str | None = None) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            raise BookSpecError(f"{field} 不能为空，只能是 {' / '.join(choices)}")
        return default
    text = str(value).strip()
    if text not in choices:
        raise BookSpecError(f"{field} 只能是 {' / '.join(choices)}，收到「{text}」")
    return text


def _asset(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    text = _text(value, field)
    if not text:
        return None
    if "\\" in text or text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise BookSpecError(f"{field} 只能是项目内的相对路径（content/assets/…）")
    if ".." in Path(text).parts:
        raise BookSpecError(f"{field} 只能是项目内的相对路径（content/assets/…）")
    return text.replace("\\", "/")


def _education(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise BookSpecError("education 要是对象（cognition / vocab / emotion）")
    vocab_raw = raw.get("vocab") or []
    if not isinstance(vocab_raw, list):
        raise BookSpecError("education.vocab 要是数组（如「袜子/sock」）")
    vocab = [str(v).strip() for v in vocab_raw if str(v).strip()]
    return {
        "cognition": _text(raw.get("cognition"), "education.cognition"),
        "vocab": vocab,
        "emotion": _text(raw.get("emotion"), "education.emotion"),
    }


def _table(raw: Any, field: str, *, name_en: bool) -> dict[str, dict[str, Any]]:
    """characters / scenes 表：id → {name, name_en?, block, asset}。"""

    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise BookSpecError(f"{field} 要是对象（id → {{name, block, asset}}）")
    out: dict[str, dict[str, Any]] = {}
    for key, member in raw.items():
        key = str(key).strip()
        if not ID_RE.fullmatch(key):
            raise BookSpecError(f"{field} 的 id「{key}」只能用英文、数字、下划线、点、短横线")
        if not isinstance(member, Mapping):
            raise BookSpecError(f"{field}.{key} 要是对象（name / block / asset）")
        row: dict[str, Any] = {
            "name": _text(member.get("name"), f"{field}.{key}.name") or key,
            "block": _text(member.get("block"), f"{field}.{key}.block", required=True),
            "asset": _asset(member.get("asset"), f"{field}.{key}.asset"),
        }
        if name_en:
            row["name_en"] = _text(member.get("name_en"), f"{field}.{key}.name_en")
        out[key] = row
    return out


def _narration(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise BookSpecError("narration 要是对象（zh_voice / en_voice / en_rate / pause）")
    pause_raw = raw.get("pause") or {}
    if not isinstance(pause_raw, Mapping):
        raise BookSpecError("narration.pause 要是对象（lead / between / tail）")
    en_rate = _text(raw.get("en_rate"), "narration.en_rate", default=DEFAULT_NARRATION["en_rate"])
    if en_rate and not re.fullmatch(r"[+-]?\d{1,3}%", en_rate):
        raise BookSpecError("narration.en_rate 要写成百分比，如 -8%")
    return {
        "zh_voice": _text(raw.get("zh_voice"), "narration.zh_voice"),
        "en_voice": _text(raw.get("en_voice"), "narration.en_voice"),
        "en_rate": en_rate,
        "pause": {
            key: _number(pause_raw.get(key), f"narration.pause.{key}", default=DEFAULT_PAUSE[key], minimum=0.0, maximum=5.0)
            for key in ("lead", "between", "tail")
        },
    }


def _music(raw: Any) -> dict[str, Any] | None:
    if raw is None or raw == {} or raw == "":
        return None
    if isinstance(raw, str):
        raw = {"file": raw}
    if not isinstance(raw, Mapping):
        raise BookSpecError("music 要是对象（file / gain / fade_out）或留空")
    file = _asset(raw.get("file"), "music.file")
    if not file:
        return None
    return {
        "file": file,
        "gain": _number(raw.get("gain"), "music.gain", default=DEFAULT_MUSIC_GAIN, minimum=0.0, maximum=1.0),
        "fade_out": _number(raw.get("fade_out"), "music.fade_out", default=DEFAULT_MUSIC_FADE, minimum=0.0, maximum=30.0),
    }


def _delivery(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise BookSpecError("delivery 要是对象（aspect_ratio / resolution / landscape_variant）")
    return {
        "aspect_ratio": _choice(raw.get("aspect_ratio"), "delivery.aspect_ratio", ASPECT_RATIOS, default="9:16"),
        "resolution": _text(raw.get("resolution"), "delivery.resolution", default="1080p") or "1080p",
        "landscape_variant": bool(raw.get("landscape_variant", False)),
    }


def _image_params(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise BookSpecError("image_params 要是对象（model / aspect_ratio / image_size）")
    return {
        "model": _text(raw.get("model"), "image_params.model", default="@image") or "@image",
        "aspect_ratio": _text(raw.get("aspect_ratio"), "image_params.aspect_ratio", default="3:4") or "3:4",
        "image_size": _text(raw.get("image_size"), "image_params.image_size", default="2K") or "2K",
    }


def _subtitle(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise BookSpecError("subtitle 要是对象（font_path / zh_size / en_size / highlight）")
    font = raw.get("font_path")
    font_path = _text(font, "subtitle.font_path") if font is not None else ""
    highlight = _text(raw.get("highlight"), "subtitle.highlight", default=DEFAULT_SUBTITLE["highlight"]) or DEFAULT_SUBTITLE["highlight"]
    if not re.fullmatch(r"(?:#|0x)?[0-9A-Fa-f]{6}", highlight):
        raise BookSpecError("subtitle.highlight 要是 #RRGGBB")
    return {
        "font_path": font_path or None,
        "zh_size": int(_number(raw.get("zh_size"), "subtitle.zh_size", default=96, minimum=24, maximum=300)),
        "en_size": int(_number(raw.get("en_size"), "subtitle.en_size", default=64, minimum=16, maximum=300)),
        "highlight": "#" + highlight[-6:].upper(),
    }


def _kb(raw: Any, page_id: str) -> dict[str, Any]:
    raw = raw or {}
    if isinstance(raw, str):
        raw = {"dir": raw}
    if not isinstance(raw, Mapping):
        raise BookSpecError(f"page {page_id}.kb 要是对象（dir / scale）")
    direction = _choice(raw.get("dir"), f"page {page_id}.kb.dir", KB_DIRS, default="push")
    scale = raw.get("scale")
    if scale in (None, [], ()):
        return {"dir": direction, "scale": None}
    if not isinstance(scale, (list, tuple)) or len(scale) != 2:
        raise BookSpecError(f"page {page_id}.kb.scale 要是两个数字 [起, 止]，如 [1.0, 1.08]")
    pair = [_number(scale[0], f"page {page_id}.kb.scale[0]", default=1.0, minimum=1.0, maximum=1.5),
            _number(scale[1], f"page {page_id}.kb.scale[1]", default=1.08, minimum=1.0, maximum=1.5)]
    return {"dir": direction, "scale": pair}


def _pages(raw: Any, *, mode: str, characters: Mapping[str, Any], scenes: Mapping[str, Any]) -> list[dict[str, Any]]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise BookSpecError("pages 要是数组")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    count = len(raw)
    for index, page in enumerate(raw):
        if not isinstance(page, Mapping):
            raise BookSpecError(f"pages[{index}] 要是对象")
        page_id = _text(page.get("id"), f"pages[{index}].id") or f"P{index + 1:02d}"
        if not _PAGE_ID_RE.fullmatch(page_id):
            raise BookSpecError(f"pages[{index}].id 要写成 P01 这样的形状，收到「{page_id}」")
        if page_id in seen:
            raise BookSpecError(f"页 id 重复：{page_id}")
        seen.add(page_id)
        default_kind = "cover" if index == 0 else ("back" if index == count - 1 and count > 1 else "page")
        kind = _choice(page.get("kind"), f"page {page_id}.kind", PAGE_KINDS, default=default_kind)
        zh = _text(page.get("zh"), f"page {page_id}.zh")
        en = _text(page.get("en"), f"page {page_id}.en")
        if mode != "en_only" and not zh:
            raise BookSpecError(f"page {page_id}.zh 不能为空——双语策略 {mode} 需要中文")
        if mode != "zh_only" and not en:
            raise BookSpecError(f"page {page_id}.en 不能为空——双语策略 {mode} 需要英文")
        cast_raw = page.get("characters") or []
        if not isinstance(cast_raw, list):
            raise BookSpecError(f"page {page_id}.characters 要是数组")
        cast: list[str] = []
        for ref in cast_raw:
            ref = str(ref).strip()
            if not ref:
                continue
            if ref not in characters:
                raise BookSpecError(f"page {page_id} 的出场角色「{ref}」不在 characters 表里")
            if ref not in cast:
                cast.append(ref)
        scene_ref = page.get("scene_ref")
        scene_ref = _text(scene_ref, f"page {page_id}.scene_ref") if scene_ref is not None else ""
        if scene_ref and scene_ref not in scenes:
            raise BookSpecError(f"page {page_id} 的场景锚「{scene_ref}」不在 scenes 表里")
        out.append({
            "id": page_id,
            "kind": kind,
            "scene": _text(page.get("scene"), f"page {page_id}.scene", required=True),
            "characters": cast,
            "scene_ref": scene_ref or None,
            "zh": zh,
            "en": en,
            "hook": _text(page.get("hook"), f"page {page_id}.hook"),
            "education": _text(page.get("education"), f"page {page_id}.education"),
            "kb": _kb(page.get("kb"), page_id),
            "flip": _choice(page.get("flip"), f"page {page_id}.flip", FLIPS, default="page_turn"),
        })
    return out


def _style_card(raw: Any) -> str:
    """``style_card``（可空，DESIGN §41）：填了就用这张卡的锚句替换 ``style_anchor``；没填零变化。"""

    from imagecraft.cards import find_card

    text = _text(raw, "style_card")
    if not text:
        return ""
    card = find_card(text)
    if card is None:
        raise BookSpecError(f"没有这张风格卡：{text}")
    return str(card["id"])


def normalize_book(data: Mapping[str, Any], *, project_root: str | os.PathLike[str] | None = None,
                   book_id: str | None = None) -> dict[str, Any]:
    """校验 + 补默认值。``book_id`` 给了就覆盖/补上 id（新建时用）。"""

    if not isinstance(data, Mapping):
        raise BookSpecError("项目文件要是一个 JSON 对象")
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else _data_root()
    raw = copy.deepcopy(dict(data))
    identifier = _text(book_id if book_id is not None else raw.get("id"), "id")
    if not identifier:
        raise BookSpecError("id 不能为空——新建时由服务端编号")
    if not ID_RE.fullmatch(identifier):
        raise BookSpecError("id 只能用英文、数字、下划线、点、短横线")
    mode = _choice(raw.get("bilingual_mode"), "bilingual_mode", BILINGUAL_MODES, default="sentence_alternate")
    characters = _table(raw.get("characters"), "characters", name_en=True)
    scenes = _table(raw.get("scenes"), "scenes", name_en=False)
    normalized: dict[str, Any] = {
        "id": identifier,
        "title_zh": _text(raw.get("title_zh"), "title_zh"),
        "title_en": _text(raw.get("title_en"), "title_en"),
        "age_band": _choice(raw.get("age_band"), "age_band", AGE_BANDS, default="2-4"),
        "bilingual_mode": mode,
        "education": _education(raw.get("education")),
        "style_anchor": _text(raw.get("style_anchor"), "style_anchor", default=DEFAULT_STYLE_ANCHOR) or DEFAULT_STYLE_ANCHOR,
        "style_card": _style_card(raw.get("style_card")),
        "negative": _text(raw.get("negative"), "negative", default=DEFAULT_NEGATIVE),
        "characters": characters,
        "scenes": scenes,
        "narration": _narration(raw.get("narration")),
        "music": _music(raw.get("music")),
        "page_turn_sfx": _asset(raw.get("page_turn_sfx"), "page_turn_sfx"),
        "delivery": _delivery(raw.get("delivery")),
        "image_params": _image_params(raw.get("image_params")),
        "subtitle": _subtitle(raw.get("subtitle")),
        "pages": _pages(raw.get("pages"), mode=mode, characters=characters, scenes=scenes),
    }
    if not normalized["title_zh"] and not normalized["title_en"]:
        raise BookSpecError("title_zh / title_en 至少填一个")
    normalized["output_root"] = str((root / OUTPUT_DIR / identifier).resolve(strict=False))
    normalized["project_root"] = str(root)
    return normalized


def to_disk(spec: Mapping[str, Any]) -> dict[str, Any]:
    """归一化字典 → 项目文件内容（去掉两个运行时路径键）。"""

    return {k: copy.deepcopy(v) for k, v in spec.items() if k not in ("output_root", "project_root")}


def load_book(book_id: str, *, project_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else _data_root()
    if not ID_RE.fullmatch(str(book_id or "")):
        raise BookSpecError(f"没有这本绘本：{book_id}")
    path = book_path(root, book_id)
    if not path.is_file():
        raise BookSpecError(f"没有这本绘本：{book_id}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise BookSpecError(f"读不了项目文件 {path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise BookSpecError(f"项目文件不是合法的 JSON（{path} 第 {exc.lineno} 行）") from exc
    if isinstance(data, Mapping) and str(data.get("id") or "") not in ("", book_id):
        raise BookSpecError(f"项目文件里的 id「{data.get('id')}」跟文件名 {book_id} 对不上")
    return normalize_book(data, project_root=root, book_id=book_id)


def save_book(spec: Mapping[str, Any], *, project_root: str | os.PathLike[str] | None = None) -> Path:
    """校验后原子写盘；返回路径。写的是 ``to_disk`` 形状。"""

    from storyvideo.state import atomic_write_json

    normalized = normalize_book(spec, project_root=project_root)
    target = book_path(Path(normalized["project_root"]), normalized["id"])
    atomic_write_json(target, to_disk(normalized))
    return target


def page_by_id(spec: Mapping[str, Any], page_id: str) -> dict[str, Any]:
    for page in spec.get("pages") or ():
        if str(page.get("id")) == page_id:
            return page
    raise BookSpecError(f"没有这一页：{page_id}")


def page_ids(spec: Mapping[str, Any]) -> list[str]:
    return [str(p["id"]) for p in spec.get("pages") or ()]


__all__ = [
    "AGE_BANDS", "BILINGUAL_MODES", "KB_DIRS", "PAGE_KINDS", "FLIPS", "BOOKS_DIR", "OUTPUT_DIR", "ASSETS_DIR",
    "DEFAULT_STYLE_ANCHOR", "DEFAULT_NEGATIVE", "DEFAULT_PAUSE", "BookSpecError", "books_dir", "book_path",
    "existing_book_ids", "next_book_id", "normalize_book", "to_disk", "load_book", "save_book", "page_by_id", "page_ids",
    "output_root", "assets_dir", "pages_dir", "audio_dir", "edit_dir", "export_dir", "manifests_dir", "resolve_asset",
    "prompt_language",
]
