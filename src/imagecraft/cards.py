"""风格卡库（DESIGN §41）：``skills/ai-image/scripts/style_cards.json`` 的读取与查找。

跟形态卡（``storyvideo.forms``）同一形状：文件缺了只是没有卡可选，不该让别的功能打不开；
按 id（不分大小写）、中文名、英文名或别名找卡；``fits`` 是 ``<module>.<template>`` 或 ``picturebook`` /
``poster``，只用于界面里「适合」筛选，**不做硬限制**（用户想跨用也允许）。

一张卡的字段：``id / name / english / aliases / category / summary / anchor_zh / anchor_en /
lighting / palette / materials / camera / constraints / fits / model_hint``。
``anchor_zh`` / ``anchor_en`` 是进提示词的那句话，其余是可选补强项，由编译器按方言组织。卡库的出处写在 ``_meta.basis``。
"""

from __future__ import annotations

import copy
import functools
import json
from typing import Any, Mapping

from genkit.paths import code_root as _code_root

STYLE_CARDS_PATH = _code_root() / "skills" / "ai-image" / "scripts" / "style_cards.json"

MODEL_HINTS = ("any", "gpt_image", "nano_banana")
PALETTE_MIN, PALETTE_MAX = 5, 7


@functools.lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    if not STYLE_CARDS_PATH.is_file():
        return {}
    try:
        with open(STYLE_CARDS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):   # 卡库坏了 = 没有卡可选，不该让出图打不开
        return {}
    return data if isinstance(data, dict) else {}


@functools.lru_cache(maxsize=1)
def style_cards() -> tuple[dict[str, Any], ...]:
    """卡列表（``cards``）；缺字段的条目直接丢掉——半张卡进不了提示词。"""

    rows = _data().get("cards") or ()
    return tuple(
        c for c in rows
        if isinstance(c, dict) and c.get("id") and c.get("name") and c.get("category") and (c.get("anchor_zh") or c.get("anchor_en"))
    )


@functools.lru_cache(maxsize=1)
def categories() -> tuple[dict[str, Any], ...]:
    rows = _data().get("categories") or ()
    return tuple(c for c in rows if isinstance(c, dict) and c.get("id"))


def meta() -> dict[str, Any]:
    return copy.deepcopy(_data().get("_meta") or {})


def card_ids() -> list[str]:
    return [str(c["id"]) for c in style_cards()]


def category_ids() -> list[str]:
    return [str(c["id"]) for c in categories()]


def find_card(name_or_id: Any) -> dict[str, Any] | None:
    """按 id（不分大小写）、中文名、英文名或别名找卡；找不到返回 None。"""

    key = str(name_or_id or "").strip()
    if not key:
        return None
    low = key.lower()
    for card in style_cards():
        names = [str(card["id"]).lower(), str(card["name"]).lower(), str(card.get("english") or "").lower()]
        aliases = [str(a).strip().lower() for a in (card.get("aliases") or ())]
        if low in names or low in aliases:
            return card
    return None


def normalize_card(value: Any) -> str:
    """把 id / 名 / 别名统一成卡的 id；不认识的抛 ValueError（spec 层包成 SpecError）。"""

    card = find_card(value)
    if card is None:
        raise ValueError(f"unknown style card: {value}")
    return str(card["id"])


def cards_for(*, fits: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
    """按「适合」目标（``sticker.emoji_pack`` / ``picturebook`` / ``poster``）与分类筛卡；两个都不给 = 全部。"""

    want_fits = str(fits or "").strip()
    want_category = str(category or "").strip()
    out: list[dict[str, Any]] = []
    for card in style_cards():
        if want_category and str(card.get("category")) != want_category:
            continue
        if want_fits and want_fits not in [str(f) for f in (card.get("fits") or ())]:
            continue
        out.append(dict(card))
    return out


def anchor_text(card: Mapping[str, Any] | None, zh: bool) -> str:
    """进提示词的那句话；卡为空 = 空串（调用方按老路走）。"""

    if not card:
        return ""
    if zh:
        return str(card.get("anchor_zh") or card.get("anchor_en") or "").strip()
    return str(card.get("anchor_en") or card.get("anchor_zh") or "").strip()


def card_summary(card: Mapping[str, Any]) -> str:
    """下拉/卡片里的一句摘要：一句话 + 色板前三个色名。"""

    summary = str(card.get("summary") or "").strip()
    palette = [str(p) for p in (card.get("palette") or ())][:3]
    return f"{summary}（{'、'.join(palette)}…）" if summary and palette else summary


def card_payload(card: Mapping[str, Any]) -> dict[str, Any]:
    """接口返回的一张卡：原样字段 + 一句摘要（前端直接渲染，不再猜字段）。"""

    out = copy.deepcopy(dict(card))
    out["aliases"] = [str(a) for a in (card.get("aliases") or ())]
    out["palette"] = [str(p) for p in (card.get("palette") or ())]
    out["constraints"] = [str(c) for c in (card.get("constraints") or ())]
    out["fits"] = [str(f) for f in (card.get("fits") or ())]
    out["model_hint"] = str(card.get("model_hint") or "any")
    out["summary_line"] = card_summary(card)
    return out


def library_payload(*, fits: str | None = None, category: str | None = None) -> dict[str, Any]:
    return {
        "categories": [dict(c) for c in categories()],
        "cards": [card_payload(c) for c in cards_for(fits=fits, category=category)],
        "model_hints": list(MODEL_HINTS),
    }


__all__ = [
    "STYLE_CARDS_PATH", "MODEL_HINTS", "PALETTE_MIN", "PALETTE_MAX", "style_cards", "categories", "meta",
    "card_ids", "category_ids", "find_card", "normalize_card", "cards_for", "anchor_text", "card_summary",
    "card_payload", "library_payload",
]
