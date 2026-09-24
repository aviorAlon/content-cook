"""图像提示词方言（DESIGN §41）：同一张卡 + 同一段画面，按目标模型换组织方式。

三种写法是本仓库按对这几类模型的一般认识自己定的，没有对着真实模型做过对比（DESIGN §41 的「未验证」）：

* ``generic``（默认，**老项目逐字节不变**）——现在的写法：风格锚 + 主体 + 画面 + 约束 + NO_TEXT 尾句，一段连写。
  三条产线的连写规则不一样（图片集 / 绘本 / 海报），所以 ``parts["join"]`` 选一种；``imageset.prompts`` 与
  ``picturebook.prompts`` 直接复用这里的 joiner，保证只有一份实现、不会两边漂移。
* ``gpt_image``（模型 id 含 ``gpt-image``）——按「场景 → 主体 → 细节 → 用途 → 约束」分五行、每行带标签
  （``Scene:`` / ``Subject:`` / ``Key details:`` / ``Intended use:`` / ``Constraints:``），长要求分块写比一整段好读；
  ``masterpiece / 8k / stunning`` 这类只夸不描述的词挤占真正的要求，一律删掉；画面里要出现的文字用引号原样给。
* ``nano_banana``（模型 id 含 ``nano-banana`` / ``gemini-image``）——一到两段连贯的自然语言；焦段、光圈、感光度
  这类数值它读不出效果，删掉，改成直接描述画面长什么样；画幅单独放在最后一行 ``Aspect ratio: 1:1``；
  多张参考图按序号说明各自管什么。

``resolve_image_dialect(model, override)``：显式 ``image_dialect`` 优先，否则按模型 id 猜，猜不到 ``generic``。
没写 ``image_dialect`` 且模型不在识别表里的老项目，输出逐字节不变。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

IMAGE_DIALECTS = ("generic", "gpt_image", "nano_banana")
JOIN_STYLES = ("imageset", "picturebook", "poster")

# 空话词表（gpt_image 方言删掉、lint 提醒）：只夸不描述画面的词——画质标签、分辨率标签、形容词式的赞美。
# 它们不告诉模型画什么，只会挤占光线、材质、颜色这些真正的要求。
ANTI_SLOP_WORDS = (
    "masterpiece", "best quality", "high quality", "award-winning",
    "8k", "4k",
    "ultra detailed", "ultra-detailed", "hyper detailed", "hyper-detailed", "highly detailed",
    "breathtaking", "stunning", "gorgeous", "incredible", "epic",
    "大师级", "高质量画质", "画质极佳", "超精细", "极致细节", "惊艳", "史诗",
)
# 删词用的一条正则：长词在前（同一位置先认长的），结果不随上面词表的书写顺序变
_ANTI_SLOP_RE = re.compile(
    "|".join(re.escape(w) for w in sorted(ANTI_SLOP_WORDS, key=lambda w: (-len(w), w.lower()))),
    re.IGNORECASE,
)
# 数字镜头参数（焦段 / 光圈 / 感光度 / 色温数值）：nano_banana 方言删掉、lint 提醒——换成画面效果的描述
LENS_NUMBERS = re.compile(
    r"(?:\b\d{1,3}\s?mm\b|\bf\s?/\s?\d+(?:\.\d+)?\b|\bISO\s?\d{2,5}\b|\b\d{1,3}\s?毫米\b|\b\d{1,4}\s?K\s?色温\b)",
    re.IGNORECASE,
)

_CJK_LO, _CJK_HI = "㐀", "鿿"


# ---------------------------------------------------------------------------- 方言判定


def dialect_for_image_model(model: str | None) -> str:
    low = str(model or "").strip().lower()
    if not low:
        return "generic"
    if "gpt-image" in low or "gpt_image" in low:
        return "gpt_image"
    if "nano-banana" in low or "nano_banana" in low or "nanobanana" in low or "gemini-image" in low or "gemini-3-pro-image" in low:
        return "nano_banana"
    return "generic"


def resolve_image_dialect(model: str | None = None, override: Any = None) -> str:
    """显式方言优先（``auto`` / 空 = 按模型猜）；猜不到 ``generic``。不认识的取值也退回按模型猜。"""

    wanted = str(override or "").strip().lower().replace("-", "_")
    if wanted in IMAGE_DIALECTS:
        return wanted
    return dialect_for_image_model(model)


# ---------------------------------------------------------------------------- 连写（generic 的三种）


def _has_cjk(text: str) -> bool:
    return any(_CJK_LO <= ch <= _CJK_HI for ch in text)


def join_sentences_zh(parts: list[str]) -> str:
    """图片集的中文拼接：段落之间补句号——上一段是中文（哪怕以色值 #F7B8C4 结尾）用「。」，纯英文风格锚用「.」。"""

    out = ""
    previous = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if out and not out.endswith(("。", "！", "？", "；", "，", ".", "!", "?")):
            out += "。" if _has_cjk(previous) else "."
        out += part
        previous = part
    if out and not out.endswith(("。", "！", "？", ".", "!", "?")):
        out += "。" if _has_cjk(previous) else "."
    return out


def join_sentences_en(parts: list[str]) -> str:
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not part.endswith((".", "!", "?")):
            part += "."
        out.append(part)
    return " ".join(out) + (" " if out else "")


def join_book_zh(parts: list[str]) -> str:
    """绘本的中文拼接：英文风格锚接中文正文时，英文句尾用半角句号，别在字母后面挂全角句号。"""

    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if out and not out.endswith(("。", "！", "？", "；", "，", ".", "!", "?")):
            out += "." if ord(out[-1]) < 128 else "。"
        out += part
    if out and not out.endswith(("。", "！", "？")):
        out += "。"
    return out


def join_concat(parts: list[str]) -> str:
    """海报：各段自带标点，直接接起来。"""

    return "".join(p for p in parts if p)


JOINERS: dict[str, tuple[Callable[[list[str]], str], Callable[[list[str]], str]]] = {
    "imageset": (join_sentences_zh, join_sentences_en),
    "picturebook": (join_book_zh, join_sentences_en),
    "poster": (join_concat, join_concat),
}


# ---------------------------------------------------------------------------- 清洗


def strip_anti_slop(text: str) -> str:
    """删掉空话（``ANTI_SLOP_WORDS``），顺手把留下的空格与逗号压回去。

    反复删到不再变为止：删掉一个词后前后两截可能拼出另一个词（``画质极致细节极佳`` → ``画质极佳``），也要删掉。
    """

    out = text
    while True:
        stripped = _ANTI_SLOP_RE.sub("", out)
        if stripped == out:
            break
        out = stripped
    return _tidy(out)


def strip_lens_numbers(text: str) -> str:
    """删掉数字镜头参数（50mm / f/2.8 / ISO 800）。"""

    return _tidy(LENS_NUMBERS.sub("", text))


def _tidy(text: str) -> str:
    out = re.sub(r"[ \t]{2,}", " ", text)
    out = re.sub(r"[ \t]+([,，。；;、.：:])", r"\1", out)
    out = re.sub(r"([，。；、：])[ \t]+", r"\1", out)          # 中文标点后不留空格（删词留下的）
    out = re.sub(r"([,，、])\s*([,，。；;、.])", r"\2", out)
    out = re.sub(r"^[\s,，、;；]+", "", out)
    out = re.sub(r"[\s,，、;；]+$", "", out)                    # 删词留下的悬空分隔符
    return out.strip()


def _lines(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    return [str(v).strip() for v in values if str(v or "").strip()]


def _slots(parts: Mapping[str, Any]) -> dict[str, Any]:
    """结构化槽位；调用方只给 ``segments`` 时也能退化出一份可用的分段（首段当主体，其余当细节）。"""

    segments = _lines(parts.get("segments"))
    subject = str(parts.get("subject") or "").strip()
    details = _lines(parts.get("details"))
    scene = str(parts.get("scene") or "").strip()
    if not subject and segments:
        subject, segments = segments[0], segments[1:]
    if not details:
        details = segments
    constraints = _lines(parts.get("constraints"))
    tail = str(parts.get("tail") or "").strip()
    if tail:
        constraints = constraints + [tail]
    return {
        "zh": bool(parts.get("zh")),
        "scene": scene,
        "subject": subject,
        "details": details,
        "use_case": str(parts.get("use_case") or "").strip(),
        "constraints": constraints,
        "text_in_image": str(parts.get("text_in_image") or "").strip(),
        "aspect_ratio": str(parts.get("aspect_ratio") or "").strip(),
        "references": _lines(parts.get("references")),
    }


# ---------------------------------------------------------------------------- 渲染


def render_generic(parts: Mapping[str, Any]) -> str:
    """现在的写法：按产线的连写规则把各段接起来，再原样挂上 NO_TEXT 尾句。"""

    zh = bool(parts.get("zh"))
    style = str(parts.get("join") or "imageset")
    joiner_zh, joiner_en = JOINERS.get(style, JOINERS["imageset"])
    body = (joiner_zh if zh else joiner_en)([str(s) for s in (parts.get("segments") or ())])
    return body + str(parts.get("tail") or "")


# gpt_image 的五行标签（顺序即输出顺序）：场景 → 主体 → 细节 → 用途 → 约束
GPT_IMAGE_LABELS = ("Scene", "Subject", "Key details", "Intended use", "Constraints")
# nano_banana 最后单独一行写画幅
RATIO_LINE = "Aspect ratio: {ratio}"


def with_ratio_line(body: str, ratio: str) -> str:
    """正文后空一行，单独一行写画幅（nano_banana 方言；单条视频的 nano_banana 也用它）。"""

    return f"{body}\n\n" + RATIO_LINE.format(ratio=ratio or "1:1")


def _reference_line(index: int, role: str, zh: bool) -> str:
    return f"参考图 {index}：{role}" if zh else f"Reference image {index}: {role}"


def render_gpt_image(parts: Mapping[str, Any]) -> str:
    """GPT Image 的五行带标签写法；空话一律删掉，画面文字用引号原样给。"""

    s = _slots(parts)
    zh = s["zh"]
    scene = s["scene"] or ("背景简洁，环境跟着主体走" if zh else "a simple setting that suits the subject")
    subject = s["subject"] or ("画面主体" if zh else "the main subject")
    details = list(s["details"])
    if s["text_in_image"]:
        details.append((f"画面上的文字只有「{s['text_in_image']}」，一字不差"
                        if zh else f'the only lettering in the image is "{s["text_in_image"]}", reproduced letter for letter'))
    if s["references"]:
        details.extend(_reference_line(i, r, zh) for i, r in enumerate(s["references"], 1))
    use_case = s["use_case"] or ("成组出图" if zh else "image set")
    constraints = list(s["constraints"]) or [("画面上不带任何文字或水印" if zh else "keep the image free of any lettering or watermark")]
    sep = "；" if zh else "; "
    values = (scene, subject, sep.join(details), use_case, sep.join(constraints))
    return "\n".join(f"{label}: {strip_anti_slop(value)}" for label, value in zip(GPT_IMAGE_LABELS, values))


def render_nano_banana(parts: Mapping[str, Any]) -> str:
    """Nano Banana 的自然语言写法：1–2 段连贯句子，数字镜头参数删掉，最后单独一行 ``Aspect ratio: …``。"""

    s = _slots(parts)
    zh = s["zh"]
    head = [x for x in (s["subject"], s["scene"], *s["details"]) if x]
    if s["text_in_image"]:
        head.append((f"画面上出现文字「{s['text_in_image']}」，一字不差" if zh
                     else f'the image shows the lettering "{s["text_in_image"]}", reproduced letter for letter'))
    tail_block = list(s["constraints"])
    for index, role in enumerate(s["references"], 1):
        tail_block.append(_reference_line(index, role, zh))
    joiner = join_sentences_zh if zh else join_sentences_en
    paragraphs = [strip_lens_numbers(joiner(head))]
    if tail_block:
        paragraphs.append(strip_lens_numbers(joiner(tail_block)))
    body = "\n\n".join(p for p in paragraphs if p)
    return with_ratio_line(body, s["aspect_ratio"])


RENDERERS: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "generic": render_generic,
    "gpt_image": render_gpt_image,
    "nano_banana": render_nano_banana,
}


def compile_image_prompt(parts: Mapping[str, Any], dialect: str = "generic") -> str:
    return RENDERERS.get(str(dialect or "generic"), render_generic)(parts)


__all__ = [
    "IMAGE_DIALECTS", "JOIN_STYLES", "ANTI_SLOP_WORDS", "LENS_NUMBERS", "JOINERS", "RENDERERS",
    "GPT_IMAGE_LABELS", "RATIO_LINE", "with_ratio_line",
    "dialect_for_image_model", "resolve_image_dialect",
    "join_sentences_zh", "join_sentences_en", "join_book_zh", "join_concat",
    "strip_anti_slop", "strip_lens_numbers",
    "render_generic", "render_gpt_image", "render_nano_banana", "compile_image_prompt",
]
