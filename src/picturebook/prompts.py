"""插画提示词编译（契约 §4）。

顺序固定：风格锚 → 场景锚 block（有则）→ 每个出场角色的 block（一个一个写名字）→ scene → 构图句 → 排他锁 → NO_TEXT 尾句。
负面 = ``negative`` + 文字/水印词。参考图 = 出场角色定妆 + 场景锚（存在的文件）。语言随 ``prompt_language``：
只有 en_only 的书用英文；风格锚本身按用户写的原样进（通常是英文，插画模型吃英文风格词更稳）。
"""

from __future__ import annotations

from typing import Any, Mapping

from imagecraft import cards as CARDS
from imagecraft import dialects as D
from storyvideo.prompts import NO_TEXT_TAIL_CN, NO_TEXT_TAIL_EN

from .spec import page_by_id, prompt_language, resolve_asset

COMPOSITION_ZH = "画面底部约三分之一保持简洁，给字幕留位置；整页只画一个场景里的一个动作；给小孩子看的插画，色调柔和、气氛安心。"
COMPOSITION_EN = ("Keep the bottom third of the frame simple so the subtitles have room; show a single moment of a single action; "
                  "a picture book for small children, soft colours and a reassuring mood. ")
EXCLUSIVITY_ZH = "画面里只出现这几个角色：{names}；除此之外不画任何人，也不画别的动物。"
EXCLUSIVITY_EN = "Only these characters appear: {names}. Nobody else is in the picture, and no other animals. "
NO_CHARACTER_ZH = "画面里没有任何人物和动物。"
NO_CHARACTER_EN = "No people and no animals anywhere in the image. "
NEGATIVE_TEXT_WORDS = ("text", "letters", "words", "watermark", "signature", "logo", "subtitles", "文字", "字母", "水印")
KB_HINT_ZH = {"push": "主体居中，四周留出推近余量", "pull": "主体略小，四周留出拉远余量",
              "left": "画面左侧留出横向余量", "right": "画面右侧留出横向余量",
              "up": "画面上方留出纵向余量", "down": "画面下方留出纵向余量"}


# 连写规则只有一份实现（imagecraft.dialects 的 generic 方言），绘本与图片集不会两边漂移
_join_zh = D.join_book_zh
_join_en = D.join_sentences_en


def style_anchor(spec: Mapping[str, Any]) -> str:
    """风格锚：选了风格卡（DESIGN §41）就用卡的锚句，否则用 ``style_anchor``（没填卡 = 零变化）。"""

    card = CARDS.find_card(spec.get("style_card"))
    if card:
        return CARDS.anchor_text(card, prompt_language(spec) == "zh")
    return str(spec.get("style_anchor") or "")


def character_names(spec: Mapping[str, Any], page_id: str) -> list[str]:
    page = page_by_id(spec, page_id)
    zh = prompt_language(spec) == "zh"
    names: list[str] = []
    for ref in page.get("characters") or ():
        member = (spec.get("characters") or {}).get(ref) or {}
        name = (member.get("name") if zh else (member.get("name_en") or member.get("name"))) or ref
        names.append(str(name))
    return names


def compile_page_prompt(spec: Mapping[str, Any], page_id: str) -> str:
    page = page_by_id(spec, page_id)
    zh = prompt_language(spec) == "zh"
    characters = spec.get("characters") or {}
    scenes = spec.get("scenes") or {}
    parts: list[str] = [style_anchor(spec)]
    scene_ref = page.get("scene_ref")
    if scene_ref and scene_ref in scenes:
        anchor = scenes[scene_ref]
        parts.append(f"{anchor.get('name') or scene_ref}：{anchor.get('block') or ''}" if zh
                     else f"Setting — {anchor.get('name') or scene_ref}: {anchor.get('block') or ''}")
    names = character_names(spec, page_id)
    for ref, name in zip(page.get("characters") or (), names):
        member = characters.get(ref) or {}
        block = str(member.get("block") or "")
        parts.append(f"{name}：{block}" if zh else f"{name}: {block}")
    parts.append(str(page.get("scene") or ""))
    parts.append(COMPOSITION_ZH if zh else COMPOSITION_EN)
    if names:
        parts.append((EXCLUSIVITY_ZH if zh else EXCLUSIVITY_EN).format(names="、".join(names) if zh else ", ".join(names)))
    else:
        parts.append(NO_CHARACTER_ZH if zh else NO_CHARACTER_EN)
    body = _join_zh(parts) if zh else _join_en(parts)
    return body + (NO_TEXT_TAIL_CN if zh else NO_TEXT_TAIL_EN)


def compile_page_negative(spec: Mapping[str, Any], page_id: str) -> str:
    base = [x.strip() for x in str(spec.get("negative") or "").split(",") if x.strip()]
    for word in NEGATIVE_TEXT_WORDS:
        if word not in base:
            base.append(word)
    return ", ".join(base)


def page_reference_assets(spec: Mapping[str, Any], page_id: str) -> tuple[str, ...]:
    """出场角色定妆 + 场景锚：存在的本地文件的绝对路径（顺序 = 角色顺序，再场景）。"""

    page = page_by_id(spec, page_id)
    characters = spec.get("characters") or {}
    scenes = spec.get("scenes") or {}
    refs: list[str] = []
    for ref in page.get("characters") or ():
        path = resolve_asset(spec, (characters.get(ref) or {}).get("asset"))
        if path:
            refs.append(str(path))
    scene_ref = page.get("scene_ref")
    if scene_ref:
        path = resolve_asset(spec, (scenes.get(scene_ref) or {}).get("asset"))
        if path:
            refs.append(str(path))
    return tuple(dict.fromkeys(refs))


def page_request_data(spec: Mapping[str, Any], page_id: str) -> dict[str, Any]:
    """一页的生图请求（哈希 / 提交都用它）：画幅固定 3:4，两版交付都从中裁切。"""

    params = spec.get("image_params") or {}
    return {
        "name": f"{spec.get('id')}-{page_id}",
        "prompt": compile_page_prompt(spec, page_id),
        "negative_prompt": compile_page_negative(spec, page_id) or None,
        "model": str(params.get("model") or "@image"),
        "aspect_ratio": str(params.get("aspect_ratio") or "3:4"),
        "image_size": str(params.get("image_size") or "2K"),
        "reference_assets": list(page_reference_assets(spec, page_id)),
    }


__all__ = ["compile_page_prompt", "compile_page_negative", "page_reference_assets", "page_request_data", "character_names", "style_anchor",
           "COMPOSITION_ZH", "COMPOSITION_EN", "EXCLUSIVITY_ZH", "EXCLUSIVITY_EN"]
