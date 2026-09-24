"""出图风格卡与图像方言（DESIGN §41，契约 docs/image-style-cards.md）。

三块，互不依赖调用方：

* ``cards``  —— 共享卡库 ``skills/ai-image/scripts/style_cards.json`` 的读取与查找（id / 名 / 别名 / ``fits`` 筛选）。
* ``dialects`` —— 同一份画面事实按目标模型换组织方式：``generic``（老项目逐字节不变）/ ``gpt_image``（五行带标签）
  / ``nano_banana``（自然语言 + ``Aspect ratio:`` 尾行、不写数字镜头参数）。
* ``lint``   —— 按方言检查一段图像提示词，返回和其他 lint 同形状的告警（``…——…``，进问题卡）。

卡与方言的文字都是本仓库自己写的，依据是常见的摄影 / 插画 / 版式常识。
"""

from __future__ import annotations

from .cards import (
    STYLE_CARDS_PATH,
    anchor_text,
    card_ids,
    card_payload,
    card_summary,
    cards_for,
    categories,
    find_card,
    normalize_card,
    style_cards,
)
from .dialects import (
    IMAGE_DIALECTS,
    compile_image_prompt,
    dialect_for_image_model,
    join_book_zh,
    join_concat,
    join_sentences_en,
    join_sentences_zh,
    render_generic,
    render_gpt_image,
    render_nano_banana,
    resolve_image_dialect,
    strip_lens_numbers,
)
from .lint import check_card, check_image_prompt, check_items

__all__ = [
    "STYLE_CARDS_PATH", "style_cards", "categories", "find_card", "normalize_card", "cards_for", "card_ids",
    "card_payload", "card_summary", "anchor_text",
    "IMAGE_DIALECTS", "dialect_for_image_model", "resolve_image_dialect", "compile_image_prompt",
    "render_generic", "render_gpt_image", "render_nano_banana", "strip_lens_numbers",
    "join_sentences_zh", "join_sentences_en", "join_book_zh", "join_concat",
    "check_image_prompt", "check_card", "check_items",
]
