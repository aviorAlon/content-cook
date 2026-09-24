"""提示词骨架（契约 §2 ``prompts.py``）。

一张图的提示词 = 风格锚 + 主体身份块 + 单张画面 + 模板固定约束 + NO_TEXT 尾句；负面词 = 模板负面 + 文字 / 水印词。

写法上的几条判断：正面描述要什么而不是不要什么（「纯白背景 #FFFFFF、四周留边」而不是「没有背景」）、
材料与颜色写具体（十六进制色值）、不用「精美 / 高质量」这类空话；真人参考照走「相貌相似度约 N%」的相似度句
（跟短剧 ``reference_roles_clause`` 同一写法：相似度写成百分数）；多人画面逐人点名 + 排他锁（跟绘本插画同一判断）。文字一律不让模型画：
表情包的中文短语、百天的「100」、写真的祝语与日期都在本机叠（post.py）。
"""

from __future__ import annotations

from typing import Any, Mapping

from imagecraft import cards as CARDS
from imagecraft import dialects as D
from storyvideo.prompts import NO_TEXT_TAIL_CN, NO_TEXT_TAIL_EN

from .spec import asset_entries

# ---------------------------------------------------------------------------- 固定句

CHARACTER_LOCK_ZH = "同一角色、同一头身比、同一线宽、同一配色，五官与发型每一张都一致。"
CHARACTER_LOCK_EN = "The same character in every image: same head-to-body ratio, same line weight, same palette, identical face and hair. "
REFERENCE_CHARACTER_ZH = "与参考图（定妆）同一角色，脸、发型、服装逐字对照参考图。"
REFERENCE_CHARACTER_EN = "The same character as the reference image (character sheet): face, hair and outfit follow the reference exactly. "

STICKER_CONSTRAINTS_ZH = "纯白背景 #FFFFFF，主体居中、四周留出约 12% 空白边，平光、轮廓边缘干净利落、主体外没有投影。"
STICKER_CONSTRAINTS_EN = ("Pure white background #FFFFFF, subject centered with about 12% empty margin on all sides, flat even light, "
                          "crisp clean outline, no cast shadow outside the subject. ")
AVATAR_CONSTRAINTS_ZH = "单人头像，正对镜头略偏 15 度，视线看向镜头，主体占画面约 70%，构图居中、四周留呼吸空间。"
AVATAR_CONSTRAINTS_EN = ("Solo portrait for an avatar, face turned about 15 degrees off camera, eyes on the lens, subject fills about 70% "
                         "of the frame, centered composition with breathing room. ")

FIDELITY_ZH = "和参考照片是同一个人，相貌相似度约 {n}%：脸型、五官比例、肤色逐字对照参考照片。"
FIDELITY_EN = "The same person as in the reference photo, at roughly {n}% facial likeness: face shape, feature proportions and skin tone follow the photo exactly. "
PORTRAIT_SAFETY_ZH = "保持参考照片原本的脸型与五官比例，肤色自然真实、保留皮肤质感，影楼级柔光布光。"
PORTRAIT_SAFETY_EN = ("Keep the original face shape and feature proportions from the reference photo, natural true-to-life skin tone with "
                      "visible skin texture, studio-grade soft lighting. ")
BABY_SAFETY_ZH = "婴儿口鼻周围干净无遮挡，脸上与口鼻处没有任何饰品，脸周只有柔软织物。"
BABY_SAFETY_EN = "The baby's nose and mouth stay clear and uncovered, no accessories on the face or near the nose and mouth, only soft fabric around the face. "
TEXT_SPACE_ZH = "画面上三分之一干净、低细节、色调均匀，留给后期叠字。"
TEXT_SPACE_EN = "The upper third of the frame stays clean, low in detail and even in tone, reserved for text added later. "
TEXT_SPACE_BOTTOM_ZH = "画面下三分之一干净、低细节、色调均匀，留给后期叠字。"
TEXT_SPACE_BOTTOM_EN = "The lower third of the frame stays clean, low in detail and even in tone, reserved for text added later. "
EXCLUSIVITY_ZH = "画面里只有这些人：{names}，没有其他人。"
EXCLUSIVITY_EN = "Only these people appear: {names}. Nobody else is in the picture. "
SOLO_ZH = "画面里只有这一个人，没有其他人。"
SOLO_EN = "Only this one person in the frame, no other people. "

NEGATIVE_TEXT_ZH = "文字,字母,数字,水印,logo,签名,字幕"
NEGATIVE_TEXT_EN = "text,letters,numbers,watermark,logo,signature,caption"
NEGATIVE_STICKER_ZH = "多个角色,背景物件,投影,渐变底,裁切到边"
NEGATIVE_STICKER_EN = "multiple characters,background objects,drop shadow,gradient background,cropped at the edge"
NEGATIVE_PORTRAIT_ZH = "改变脸型,过度美白,过度磨皮,塑料感皮肤,多余的人,变形的手"
NEGATIVE_PORTRAIT_EN = "altered face shape,over-whitened skin,over-smoothed skin,plastic skin,extra people,deformed hands"
NEGATIVE_BABY_ZH = "口鼻处饰品,坚硬道具贴脸,遮住口鼻"
NEGATIVE_BABY_EN = "accessories near nose or mouth,hard props against the face,covered nose or mouth"

WHO_LABELS = {"bride": ("新娘", "the bride"), "groom": ("新郎", "the groom")}


# ---------------------------------------------------------------------------- 拼接


def is_zh(spec: Mapping[str, Any]) -> bool:
    return str(spec.get("prompt_language") or "zh").lower().startswith("zh")


_has_cjk = D._has_cjk
# 连写规则只有一份实现（imagecraft.dialects），generic 方言与这里逐字节同源，不会两边漂移
join_zh = D.join_sentences_zh
join_en = D.join_sentences_en


def image_dialect(spec: Mapping[str, Any] | None) -> str:
    """这个项目该用哪种图像方言：``spec.image_dialect`` 优先，否则按 ``image_params.model`` 猜；猜不到 generic。"""

    if not isinstance(spec, Mapping):
        return "generic"
    model = str((spec.get("image_params") or {}).get("model") or "")
    return D.resolve_image_dialect(model, spec.get("image_dialect"))


def assemble(parts: list[str], zh: bool, *, spec: Mapping[str, Any] | None = None, subject: str = "",
             scene: str = "", details: list[str] | None = None, use_case: str = "",
             constraints: list[str] | None = None, aspect: str = "", references: list[str] | None = None) -> str:
    """风格锚 + 主体 + 画面 + 约束 + NO_TEXT 尾句。

    ``generic``（没写 ``image_dialect``、模型也不在识别表里 = 老项目）走原来的连写，**逐字节不变**；
    别的方言把这些命名槽位交给 ``imagecraft.dialects`` 重排（槽位没给时由 ``segments`` 退化推出）。
    """

    dialect = image_dialect(spec)
    tail = NO_TEXT_TAIL_CN if zh else NO_TEXT_TAIL_EN
    if dialect == "generic":
        return (join_zh(parts) if zh else join_en(parts)) + tail
    return D.compile_image_prompt({
        "zh": zh, "join": "imageset", "segments": parts, "tail": tail.strip(),
        "subject": subject, "scene": scene, "details": details or [], "use_case": use_case,
        "constraints": constraints or [], "aspect_ratio": aspect, "references": references or [],
    }, dialect)


# ---------------------------------------------------------------------------- 风格卡


def style_card(spec: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """``spec.style`` 指向的风格卡；选的是模板预设 / custom 就返回 None（老项目零变化）。"""

    if not isinstance(spec, Mapping):
        return None
    style = str(spec.get("style") or "")
    if not style or style == "custom":
        return None
    return CARDS.find_card(style)


def card_detail_sentence(card: Mapping[str, Any] | None, zh: bool) -> str:
    """卡的补强项（布光 / 色板 / 材质 / 镜头感）拼成一句；英文提示词只用锚句，补强项不做中英混排。"""

    if not card or not zh:
        return ""
    palette = "、".join(str(p) for p in (card.get("palette") or ()))
    bits = [
        f"布光：{card['lighting']}" if card.get("lighting") else "",
        f"色板：{palette}" if palette else "",
        f"材质：{card['materials']}" if card.get("materials") else "",
        f"镜头感：{card['camera']}" if card.get("camera") else "",
    ]
    return "；".join(b for b in bits if b)


def card_constraint_sentence(card: Mapping[str, Any] | None, zh: bool) -> str:
    if not card or not zh:
        return ""
    items = [str(c).strip() for c in (card.get("constraints") or ()) if str(c).strip()]
    return "；".join(items)


def card_details(card: Mapping[str, Any] | None, zh: bool) -> list[str]:
    """给 gpt_image / nano_banana 方言的 ``Key details`` 分条（generic 用不到）。"""

    if not card or not zh:
        return []
    palette = "、".join(str(p) for p in (card.get("palette") or ()))
    return [x for x in (str(card.get("lighting") or ""), f"色板：{palette}" if palette else "",
                        str(card.get("materials") or ""), str(card.get("camera") or "")) if x]


def negative(spec: Mapping[str, Any], *extra: str) -> str:
    zh = is_zh(spec)
    words: list[str] = []
    for chunk in (*extra, NEGATIVE_TEXT_ZH if zh else NEGATIVE_TEXT_EN):
        for word in str(chunk or "").split(","):
            word = word.strip()
            if word and word not in words:
                words.append(word)
    return ",".join(words)


# ---------------------------------------------------------------------------- 主体身份块


def has_subject(spec: Mapping[str, Any]) -> bool:
    """这个项目到底有没有主体：名字 / 身份块 / 参考图，三个都空 = 没有（图片工作台允许只写画面）。"""

    subject = spec.get("subject") or {}
    if not isinstance(subject, Mapping):
        return False
    return bool(str(subject.get("name") or "").strip() or str(subject.get("block") or "").strip()
                or (subject.get("assets") or ()))


def subject_block(spec: Mapping[str, Any]) -> str:
    """主体身份块；主体为空时是空串——``assemble`` 会跳过空段，提示词里连身份锁都不出现（契约 §2）。"""

    return identity_block(spec) if has_subject(spec) else ""


def identity_block(spec: Mapping[str, Any]) -> str:
    """角色：``名字：外形块`` + 同一角色锁（有定妆图再加参考句）；真人：相似度句（婚纱照按新娘 / 新郎逐人点名）。"""

    zh = is_zh(spec)
    subject = spec.get("subject") or {}
    kind = str(subject.get("kind") or "character")
    name = str(subject.get("name") or "").strip()
    block = str(subject.get("block") or "").strip()
    fidelity = int(subject.get("fidelity") or 0)
    assets = asset_entries(spec)
    if kind == "character":
        parts: list[str] = []
        if block:
            parts.append(f"{name}：{block}" if name and zh else (f"{name}: {block}" if name else block))
        elif name:
            parts.append(name)
        parts.append(CHARACTER_LOCK_ZH if zh else CHARACTER_LOCK_EN)
        if assets:
            parts.append(REFERENCE_CHARACTER_ZH if zh else REFERENCE_CHARACTER_EN)
        return join_zh(parts) if zh else join_en(parts)
    # 真人参考照
    people = [e for e in assets if e["who"] in WHO_LABELS]
    if people:
        seen: list[str] = []
        parts = []
        for entry in people:
            who = entry["who"]
            if who in seen:
                continue
            seen.append(who)
            slots = [str(i + 1) for i, e in enumerate(assets) if e["who"] == who]
            label = WHO_LABELS[who][0] if zh else WHO_LABELS[who][1]
            slot_text = ("参考图 " + "、".join(slots)) if zh else ("reference " + ", ".join(slots))
            sentence = (FIDELITY_ZH if zh else FIDELITY_EN).format(n=fidelity)
            parts.append(f"{label}（{slot_text}）：{sentence}" if zh else f"{label.capitalize()} ({slot_text}): {sentence}")
        if block:
            parts.append(block)
        return join_zh(parts) if zh else join_en(parts)
    parts = [(FIDELITY_ZH if zh else FIDELITY_EN).format(n=fidelity)]
    if name:
        parts.insert(0, (f"主体：{name}" if zh else f"Subject: {name}"))
    if block:
        parts.append(block)
    return join_zh(parts) if zh else join_en(parts)


def exclusivity(spec: Mapping[str, Any]) -> str:
    """真人写真的排他锁：婚纱两人逐人点名，其余单人。"""

    zh = is_zh(spec)
    people = []
    for entry in asset_entries(spec):
        if entry["who"] in WHO_LABELS and entry["who"] not in people:
            people.append(entry["who"])
    if len(people) >= 2:
        names = [WHO_LABELS[w][0] if zh else WHO_LABELS[w][1] for w in people]
        return (EXCLUSIVITY_ZH if zh else EXCLUSIVITY_EN).format(names="、".join(names) if zh else ", ".join(names))
    return SOLO_ZH if zh else SOLO_EN


def reference_assets(spec: Mapping[str, Any]) -> tuple[str, ...]:
    """参考图：存在的本地文件的绝对路径（顺序 = subject.assets 顺序，去重）。"""

    return tuple(dict.fromkeys(e["path"] for e in asset_entries(spec) if e["path"]))


__all__ = [
    "is_zh", "join_zh", "join_en", "assemble", "negative", "identity_block", "has_subject", "subject_block",
    "exclusivity", "reference_assets",
    "image_dialect", "style_card", "card_detail_sentence", "card_constraint_sentence", "card_details",
    "CHARACTER_LOCK_ZH", "CHARACTER_LOCK_EN", "STICKER_CONSTRAINTS_ZH", "STICKER_CONSTRAINTS_EN", "AVATAR_CONSTRAINTS_ZH",
    "AVATAR_CONSTRAINTS_EN", "FIDELITY_ZH", "FIDELITY_EN", "PORTRAIT_SAFETY_ZH", "PORTRAIT_SAFETY_EN", "BABY_SAFETY_ZH",
    "BABY_SAFETY_EN", "TEXT_SPACE_ZH", "TEXT_SPACE_EN", "TEXT_SPACE_BOTTOM_ZH", "TEXT_SPACE_BOTTOM_EN",
    "EXCLUSIVITY_ZH", "EXCLUSIVITY_EN", "SOLO_ZH", "SOLO_EN",
    "NEGATIVE_STICKER_ZH", "NEGATIVE_STICKER_EN", "NEGATIVE_PORTRAIT_ZH", "NEGATIVE_PORTRAIT_EN", "NEGATIVE_BABY_ZH", "NEGATIVE_BABY_EN",
]
