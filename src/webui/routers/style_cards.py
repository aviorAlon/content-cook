"""风格卡库的只读接口（DESIGN §41，契约 docs/image-style-cards.md §4）。

* ``GET /api/style-cards``          ``{categories, cards, model_hints}``；``?category=`` / ``?fits=`` 过滤
* ``GET /api/style-cards/{id}``     单张卡（id / 中文名 / 英文名 / 别名都能找到）

卡库是代码资源（``skills/ai-image/scripts/style_cards.json``），跟形态卡 / 题材卡一样只读不写；
``fits`` 只做「适合」筛选，不做硬限制——用户想把绘本的水彩卡用到写真上也允许。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from imagecraft import cards as C

router = APIRouter(tags=["style-cards"])


@router.get("/api/style-cards")
def list_style_cards(category: str | None = None, fits: str | None = None) -> dict[str, Any]:
    wanted = str(category or "").strip()
    if wanted and wanted not in C.category_ids():
        raise HTTPException(404, f"没有这个风格分类：{wanted}")
    return C.library_payload(fits=fits, category=wanted or None)


@router.get("/api/style-cards/{card_id}")
def get_style_card(card_id: str) -> dict[str, Any]:
    card = C.find_card(card_id)
    if card is None:
        raise HTTPException(404, f"没有这张风格卡：{card_id}")
    return C.card_payload(card)
