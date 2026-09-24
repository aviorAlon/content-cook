"""图像提示词的静态检查（DESIGN §41 / 契约 §3）。

全部是告警不是报错，形状跟其他 lint 一样（``影响——怎么做``，前缀 ``image <标签>: ``），
``webui.problems.problem_card`` 能直接拆成问题卡（scope ``image``、kind ``image``）。

* **空话**：``gpt_image`` 方言里出现 ``masterpiece / 8k / stunning`` 这类只夸不描述画面的词——
  它们占着位置却不说画什么，把真正的要求冲淡了。
* **数字镜头参数**：``nano_banana`` 方言里出现 ``50mm / f/2.8 / ISO 800``——这类数值换不来对应的画面效果，
  不如直接写想要的样子。
* **否定式描述**：``no people`` / 「不要出现路人」——点名一样东西再说"不要"，反而把它带进了画面；
  写清画面里有什么更稳。画面文字政策那句 NO_TEXT 尾句是既定例外，不告警。
* **画面文字没加引号**：要出现在画面里的文字必须用引号原样写出来，模型才知道逐字渲染哪几个字。
* **色板太短**：卡带 ``palette`` 但少于 5 个具体色名——提示，不是错误（色名写具体，模型才有的抓）。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .cards import PALETTE_MIN, PALETTE_MAX
from .dialects import ANTI_SLOP_WORDS, LENS_NUMBERS

# 画面文字政策的既定例外：这些句子里的"没有文字"是产品决定，不是写法问题
TEXT_POLICY_WORDS = ("文字", "字母", "数字", "符号", "水印", "字幕", "logo", "标志", "签名",
                     "text", "letters", "numbers", "symbols", "watermark", "caption", "subtitle", "signature", "lettering")

NEGATIVE_MARKERS = (
    re.compile(r"不要出现[^，。；、\n]{0,14}"),
    re.compile(r"不要有[^，。；、\n]{0,14}"),
    re.compile(r"不允许出现[^，。；、\n]{0,14}"),
    re.compile(r"禁止出现[^，。；、\n]{0,14}"),
    re.compile(r"不能出现[^，。；、\n]{0,14}"),
    re.compile(r"\bno\s+[a-z][a-z\-]*(?:\s+[a-z][a-z\-]*){0,3}", re.IGNORECASE),
    re.compile(r"\bwithout\s+[a-z][a-z\-]*(?:\s+[a-z][a-z\-]*){0,3}", re.IGNORECASE),
    re.compile(r"\bavoid\s+[a-z][a-z\-]*(?:\s+[a-z][a-z\-]*){0,3}", re.IGNORECASE),
)

TEXT_MARKERS = re.compile(
    r"(写着|上面写着|上面写|文字是|文字为|标题是|标题为|字样是|字样为|text reads|text says|sign reads|sign says|label reads|headline reads)"
    r"\s*[:：]?\s*([^\n]{0,24})",
    re.IGNORECASE,
)
QUOTE_CHARS = "\"'「『“‘《【"


def _head(label: str) -> str:
    label = str(label or "").strip()
    return f"image {label}: " if label else ""


def _quoted(items) -> str:
    return "、".join(str(i) for i in items)


# ---------------------------------------------------------------------------- 单条规则


def check_anti_slop(prompt: str, dialect: str = "generic", *, label: str = "") -> list[str]:
    if str(dialect) != "gpt_image":
        return []
    low = str(prompt or "").lower()
    hits = [w for w in ANTI_SLOP_WORDS if w.lower() in low]
    if not hits:
        return []
    return [f"{_head(label)}GPT Image 方言里出现了空话「{_quoted(sorted(set(hits)))}」"
            f"——这些词只夸不描述画面，删掉，换成具体的光线、材质或颜色"]


def check_lens_numbers(prompt: str, dialect: str = "generic", *, label: str = "") -> list[str]:
    if str(dialect) != "nano_banana":
        return []
    hits = [m.group(0).strip() for m in LENS_NUMBERS.finditer(str(prompt or ""))]
    if not hits:
        return []
    return [f"{_head(label)}Nano Banana 方言里出现了数字镜头参数「{_quoted(sorted(set(hits)))}」"
            f"——这类数值换不来对应的画面，改成直接描述想要的样子（背景虚到什么程度、离主体多近、透视夸不夸张）"]


def check_negative_framing(prompt: str, *, label: str = "") -> list[str]:
    text = str(prompt or "")
    hits: list[str] = []
    for pattern in NEGATIVE_MARKERS:
        for match in pattern.finditer(text):
            phrase = match.group(0).strip()
            low = phrase.lower()
            if any(word in low for word in TEXT_POLICY_WORDS):
                continue          # 画面文字政策那句是既定例外
            if phrase not in hits:
                hits.append(phrase)
    if not hits:
        return []
    return [f"{_head(label)}提示词里有否定式描述「{_quoted(hits[:3])}」"
            f"——反着写：说清画面里有什么，别列没有什么；不想要的东西写进负向词"]


def check_quoted_text(prompt: str, *, label: str = "") -> list[str]:
    text = str(prompt or "")
    out: list[str] = []
    for match in TEXT_MARKERS.finditer(text):
        rest = match.group(2).strip()
        if not rest or rest[0] in QUOTE_CHARS:
            continue
        snippet = re.split(r"[，。；、,.;\n]", rest)[0].strip()
        if not snippet:
            continue
        out.append(f"{_head(label)}要出现在画面里的文字「{snippet}」没加引号"
                   f"——要出现的文字用引号原样写出来，模型才知道逐字渲染哪几个字")
    return out


def check_card(card: Mapping[str, Any] | None, *, label: str = "") -> list[str]:
    """卡本身的提示：色板要 5–7 个具体色名。"""

    if not card:
        return []
    palette = [str(p).strip() for p in (card.get("palette") or ()) if str(p).strip()]
    if len(palette) >= PALETTE_MIN:
        return []
    name = str(card.get("name") or card.get("id") or "")
    head = _head(label or str(card.get("id") or ""))
    return [f"{head}风格卡「{name}」的色板只有 {len(palette)} 个色名"
            f"——色板写 {PALETTE_MIN}–{PALETTE_MAX} 个具体色名（雾蓝、砖红、米白），模型才有的抓"]


# ---------------------------------------------------------------------------- 汇总


def check_image_prompt(prompt: str, *, dialect: str = "generic", card: Mapping[str, Any] | None = None,
                       label: str = "") -> list[str]:
    """一段图像提示词的全部告警（顺序固定：方言两条 → 否定式 → 画面文字 → 卡的色板）。"""

    warnings: list[str] = []
    warnings += check_anti_slop(prompt, dialect, label=label)
    warnings += check_lens_numbers(prompt, dialect, label=label)
    warnings += check_negative_framing(prompt, label=label)
    warnings += check_quoted_text(prompt, label=label)
    warnings += check_card(card, label=label)
    return warnings


def check_items(items, *, dialect: str = "generic", card: Mapping[str, Any] | None = None) -> list[str]:
    """一组图（``imageset`` 的 ``items``）：逐张查提示词，卡的色板整组只提一次。"""

    warnings: list[str] = []
    for item in items or ():
        if not isinstance(item, Mapping):
            continue
        warnings += check_anti_slop(str(item.get("prompt") or ""), dialect, label=str(item.get("id") or ""))
        warnings += check_lens_numbers(str(item.get("prompt") or ""), dialect, label=str(item.get("id") or ""))
        warnings += check_negative_framing(str(item.get("prompt") or ""), label=str(item.get("id") or ""))
        warnings += check_quoted_text(str(item.get("prompt") or ""), label=str(item.get("id") or ""))
    warnings += check_card(card)
    return warnings


__all__ = ["TEXT_POLICY_WORDS", "NEGATIVE_MARKERS", "TEXT_MARKERS", "check_anti_slop", "check_lens_numbers",
           "check_negative_framing", "check_quoted_text", "check_card", "check_image_prompt", "check_items"]
