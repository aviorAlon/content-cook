"""形态卡（DESIGN §31，融合 drama-skills G19）：``spec.form`` 选一种交付形态（竖屏短剧 / 横屏短片 / 竖屏动画 /
口播 / 品牌情景），卡决定三件事——

* **默认值**（``defaults``）：画幅、分辨率、单镜时长区间、字幕开关、每分钟镜数上限、钩子窗口。``apply_form_defaults``
  只补缺省的键，明写的值永远不动。
* **必带项**（``must_have``）：进起草系统提示词（scriptgen），能静态核对的由 ``lint.check_form`` 查。
* **运动预算句**（``motion_budget``）：一句话进每镜的视频提示词（``prompts.video_prompt_parts`` 的 ``form_budget`` 部件）。

取 drama-skills 形态卡的判据（运动预算是"哪些必须全动、哪些用 hold / 局部循环 / 剪辑承接"的分配，不是风格标签），
不取它的 Markdown 写法。没写 ``form`` 的 spec：这里每个函数都返回空，编译结果一个字不变。
"""

from __future__ import annotations

import copy
import functools
import json
from typing import Any, Mapping

from genkit.paths import code_root as _code_root

FORM_CARDS_PATH = _code_root() / "skills" / "ai-short-film" / "scripts" / "form_cards.json"

# 形态卡管的三个生成参数键：起草时先按卡填，再让明写的值盖回来
_IMAGE_KEYS = ("aspect_ratio",)
_VIDEO_KEYS = ("aspect_ratio", "resolution")
# 字幕"关掉"的写法：装配现在只认 display=speech/full_shot，这两种取值是给 lint 看的显式声明
SUBTITLE_OFF_VALUES = ("none", "off")


@functools.lru_cache(maxsize=1)
def form_cards() -> list[dict[str, Any]]:
    """形态卡列表（form_cards.json 的 ``cards``）；文件缺了只是没有形态可选，不该让别的功能打不开。"""

    if not FORM_CARDS_PATH.is_file():
        return []
    with open(FORM_CARDS_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    cards = data.get("cards") if isinstance(data, dict) else data
    return [c for c in (cards or ()) if isinstance(c, dict) and c.get("id") and c.get("name") and isinstance(c.get("defaults"), dict)]


def find_form(name_or_id: Any) -> dict[str, Any] | None:
    """按 id（不分大小写）、中文名、英文名或别名找形态卡；找不到返回 None。"""

    key = str(name_or_id or "").strip()
    if not key:
        return None
    low = key.lower()
    for card in form_cards():
        english = card.get("english") or {}
        names = [str(card["id"]).lower(), str(card["name"]).lower(), str(english.get("name") or "").lower()]
        aliases = [str(a).strip().lower() for a in (card.get("aliases") or ())]
        if low in names or low in aliases:
            return card
    return None


def normalize_form(value: Any) -> str:
    """把 id / 名 / 别名统一成卡的 id；不认识的抛 ValueError（spec 层包成 SpecError）。"""

    card = find_form(value)
    if card is None:
        raise ValueError(f"unknown form card: {value}")
    return str(card["id"])


def form_for_spec(spec: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return find_form((spec or {}).get("form")) if isinstance(spec, Mapping) else None


def form_defaults(spec: Mapping[str, Any] | None) -> dict[str, Any]:
    """spec.form 对应卡的 ``defaults`` 副本；没写 form 或找不到卡 = 空 dict。"""

    card = form_for_spec(spec)
    return copy.deepcopy(card.get("defaults") or {}) if card else {}


def apply_form_defaults(draft: dict[str, Any]) -> dict[str, Any]:
    """按 ``draft["form"]`` 补缺省的生成参数——只补没有的键，明写的值（哪怕是 None 以外的任何值）不动。

    补：``image_params.aspect_ratio``、``video_params.aspect_ratio`` / ``resolution``；卡要求字幕且 draft 完全没有
    ``subtitle`` 块时补一份默认字幕样式（有块就不碰，关不关由块自己说）。没有 form 的 draft 原样返回。
    """

    defaults = form_defaults(draft)
    if not defaults:
        return draft
    image = draft.get("image_params")
    if not isinstance(image, dict):
        image = {}
        draft["image_params"] = image
    for key in _IMAGE_KEYS:
        if key in defaults and key not in image:
            image[key] = defaults[key]
    video = draft.get("video_params")
    if not isinstance(video, dict):
        video = {}
        draft["video_params"] = video
    for key in _VIDEO_KEYS:
        if key in defaults and key not in video:
            video[key] = defaults[key]
    if defaults.get("subtitles") and "subtitle" not in draft:
        from .subtitles import DEFAULT_STYLE

        draft["subtitle"] = dict(DEFAULT_STYLE)
    return draft


def motion_budget_sentence(spec: Mapping[str, Any] | None) -> str:
    """进视频提示词的运动预算句，按 ``prompt_language`` 选中英文；没写 form = 空串（老项目零变化）。"""

    card = form_for_spec(spec)
    if not card:
        return ""
    zh = str((spec or {}).get("prompt_language") or "zh").lower().startswith("zh")
    if zh:
        return str(card.get("motion_budget") or "").strip()
    return str((card.get("english") or {}).get("motion_budget") or card.get("motion_budget") or "").strip()


def composition_sentence(spec: Mapping[str, Any] | None) -> str:
    """构图句：主体落在画面哪一块 + 句末写明原生画幅、不是另一种画幅改出来的。没写 form = 空串。"""

    card = form_for_spec(spec)
    if not card:
        return ""
    zh = str((spec or {}).get("prompt_language") or "zh").lower().startswith("zh")
    if zh:
        return str(card.get("composition") or "").strip()
    return str((card.get("english") or {}).get("composition") or card.get("composition") or "").strip()


def form_prompt_sentences(spec: Mapping[str, Any] | None) -> str:
    """进视频提示词的形态句：运动预算 + 构图，中文直接拼、英文空格连。"""

    parts = [x for x in (motion_budget_sentence(spec), composition_sentence(spec)) if x]
    if not parts:
        return ""
    zh = str((spec or {}).get("prompt_language") or "zh").lower().startswith("zh")
    return "".join(parts) if zh else " ".join(parts)


def subtitles_off(subtitle: Any) -> bool:
    """spec.subtitle 明确声明了不烧字幕（render_mode / display = none|off）。缺块不算关。"""

    if not isinstance(subtitle, Mapping):
        return False
    for key in ("render_mode", "display"):
        if str(subtitle.get(key) or "").strip().lower() in SUBTITLE_OFF_VALUES:
            return True
    return False


def card_summary(card: Mapping[str, Any]) -> str:
    """下拉里的一句摘要：画幅 · 单镜时长 · 字幕。"""

    d = card.get("defaults") or {}
    lo, hi = (list(d.get("shot_seconds") or [0, 0]) + [0, 0])[:2]
    parts = [str(d.get("aspect_ratio") or "")]
    if lo or hi:
        parts.append(f"单镜 {lo:g}–{hi:g} 秒")
    if d.get("max_shots_per_minute"):
        parts.append(f"每分钟不超过 {d['max_shots_per_minute']} 镜")
    parts.append("字幕常开" if d.get("subtitles") else "字幕可选")
    return " · ".join(p for p in parts if p)


__all__ = ["FORM_CARDS_PATH", "SUBTITLE_OFF_VALUES", "form_cards", "find_form", "normalize_form", "form_for_spec", "form_defaults",
           "apply_form_defaults", "motion_budget_sentence", "composition_sentence", "form_prompt_sentences", "subtitles_off", "card_summary"]
