"""三段式提示词编译（契约 docs/clip-spec.md §2）。

【全片设定】/【分段时间线】/【衔接与收尾】三段拼一条，按 ``prompt_language`` 出中英，按方言组织。判据是长单镜视频
提示词的三条通用写法：

* **P1**：时长 + 画幅显式写出，并**排除另一种画幅**，再加主体安全区——画幅、排除句、安全区在同一句里说完，
  不允许两处画幅互相矛盾（这是最常见的写法错误之一）。
* **P2**：色板写 5–7 个具体色名（不足 5 个由 ``lint`` 提醒，不拦）。
* **P3**：要出现在画面里的文字用引号原样写出来，模型才知道逐字渲染哪几个字。

方言（``imagecraft.dialects``）只用两种：``generic``（默认写法）与 ``nano_banana``（剥掉数字镜头参数，
结尾单独一行 ``Aspect ratio: <画幅>``）。未知模型走 ``generic``——``gpt_image`` 那套分段带标签是给出图用的，
视频这边没有对应判据，一律折回 ``generic``。
"""

from __future__ import annotations

from typing import Any, Mapping

from imagecraft import cards as CARDS
from imagecraft import dialects as D

from .spec import beat_ids

CLIP_DIALECTS = ("generic", "nano_banana")

# 画幅 → 「排除另一种画幅」那句（P1）。表里没有的画幅走兜底句，仍然写死"只有这一种"。
_LANDSCAPE = ("横向画面从头到尾不变，不改成 9:16 竖向", "landscape from the first frame to the last, never switched to a 9:16 vertical frame")
_PORTRAIT = ("竖向画面从头到尾不变，不改成 16:9 横向", "vertical from the first frame to the last, never switched to a 16:9 horizontal frame")
FORBIDDEN_ASPECT = {
    "16:9": _LANDSCAPE,
    "21:9": _LANDSCAPE,
    "4:3": _LANDSCAPE,
    "9:16": _PORTRAIT,
    "3:4": _PORTRAIT,
    "1:1": ("正方形画面从头到尾不变，不改成横向或竖向", "square from the first frame to the last, never switched to a horizontal or vertical frame"),
}
# 首帧是一张静止图，没有"从头到尾"——同一个意思换成不带时间的说法
_LANDSCAPE_STILL = ("横向画面，不做成 9:16 竖向", "a landscape frame, not a 9:16 vertical one")
_PORTRAIT_STILL = ("竖向画面，不做成 16:9 横向", "a vertical frame, not a 16:9 horizontal one")
FORBIDDEN_ASPECT_STILL = {
    "16:9": _LANDSCAPE_STILL,
    "21:9": _LANDSCAPE_STILL,
    "4:3": _LANDSCAPE_STILL,
    "9:16": _PORTRAIT_STILL,
    "3:4": _PORTRAIT_STILL,
    "1:1": ("正方形画面，不做成横向或竖向", "a square frame, neither horizontal nor vertical"),
}

HEAD_GLOBAL = ("【全片设定】", "[Whole clip] ")
HEAD_TIMELINE = ("【分段时间线】", "[Beat by beat] ")
HEAD_CONTINUITY = ("【衔接与收尾】", "[Transitions & ending] ")

# 编译器自己加的两句：开头的时长句、【衔接与收尾】开头的一镜到底 / 硬切说明
OPENER = ("视频时长 {duration} 秒", "Video length {duration} seconds")
ONE_TAKE = ("一镜到底：摄影机从第一秒拍到最后一秒，中间没有剪辑点",
            "A single take: the camera keeps rolling from the first second to the last, with no edit points")
HARD_CUTS = ("段与段之间用硬切，不做转场特效", "Hard cuts between segments, no transition effects")


# ---------------------------------------------------------------------------- 方言 / 语言


def is_zh(spec: Mapping[str, Any]) -> bool:
    return str(spec.get("prompt_language") or "zh") == "zh"


def resolve_dialect(model: str | None = None, override: Any = None) -> str:
    """显式方言优先（``auto`` / 空 = 按模型猜）；``gpt_image`` 与不认识的取值一律折回 ``generic``。"""

    wanted = str(override or "").strip().lower().replace("-", "_")
    if wanted in CLIP_DIALECTS:
        return wanted
    guessed = D.dialect_for_image_model(model)
    return guessed if guessed in CLIP_DIALECTS else "generic"


def dialect_for(spec: Mapping[str, Any]) -> str:
    model = str((spec.get("video_params") or {}).get("model") or "")
    return resolve_dialect(model, spec.get("dialect"))


def style_anchor(spec: Mapping[str, Any]) -> str:
    """风格锚：选了风格卡（DESIGN §41）就用卡的锚句，否则用自写的 ``style_anchor``。"""

    card = style_card(spec)
    if card:
        return CARDS.anchor_text(card, is_zh(spec))
    return str(spec.get("style_anchor") or "")


def style_card(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    style = str(spec.get("style") or "")
    if not style or style == "custom":
        return None
    return CARDS.find_card(style)


def palette(spec: Mapping[str, Any]) -> list[str]:
    """色板：项目里写了就用项目的，没写就用风格卡的（卡的色板本来就是 5–7 个具体色名）。"""

    own = [str(p).strip() for p in ((spec.get("global") or {}).get("palette") or ()) if str(p).strip()]
    if own:
        return own
    card = style_card(spec)
    return [str(p).strip() for p in ((card or {}).get("palette") or ()) if str(p).strip()]


def aspect_ratio(spec: Mapping[str, Any]) -> str:
    return str((spec.get("global") or {}).get("aspect_ratio") or "16:9")


def forbidden_aspect(ratio: str, zh: bool, *, still: bool = False) -> str:
    """「排除另一种画幅」那句；``still=True`` 给首帧（一张静止图）用，不带"从头到尾"。"""

    pair = (FORBIDDEN_ASPECT_STILL if still else FORBIDDEN_ASPECT).get(str(ratio))
    if pair:
        return pair[0] if zh else pair[1]
    if still:
        return f"保持 {ratio}，不改成别的比例" if zh else f"kept at {ratio}, not another ratio"
    return (f"从头到尾保持 {ratio}，不改成别的比例" if zh
            else f"kept at {ratio} from the first frame to the last, never switched to another ratio")


# ---------------------------------------------------------------------------- 三段


def _secs(value: Any) -> str:
    return f"{float(value):g}"


def _join(parts: list[str], zh: bool) -> str:
    """段内连写：中文按上一句是不是中文决定用「。」还是「.」（``1080p`` 后面接中文仍然是「。」）。"""

    return (D.join_sentences_zh(parts) if zh else D.join_sentences_en(parts)).strip()


def global_section(spec: Mapping[str, Any], *, for_frame: bool = False) -> str:
    """【全片设定】：时长 + 画幅 + 排除另一画幅 + 安全区 + 风格锚 + 色板 + 光线 + 文字政策 + 声音说明。"""

    zh = is_zh(spec)
    section = spec.get("global") or {}
    params = spec.get("video_params") or {}
    ratio = aspect_ratio(spec)
    parts: list[str] = []
    if for_frame:
        parts.append("单帧静止画面（这条视频的首帧）" if zh else "A single still frame (the first frame of this video)")
    else:
        duration, resolution = params.get("duration"), str(params.get("resolution") or "")
        head = (OPENER[0] if zh else OPENER[1]).format(duration=duration)
        parts.append(f"{head}，{resolution}" if zh and resolution else (f"{head}, {resolution}" if resolution else head))
    parts.append(style_anchor(spec))
    parts.append(str(section.get("look") or ""))
    colors = palette(spec)
    if colors:
        parts.append(("色板：" + "、".join(colors)) if zh else ("Palette: " + ", ".join(colors)))
    parts.append(str(section.get("light") or ""))
    forbidden = forbidden_aspect(ratio, zh, still=for_frame)
    frame_line = f"画幅 {ratio}，{forbidden}" if zh else f"Aspect ratio {ratio}, {forbidden}"
    safe_area = str(section.get("safe_area") or "")
    if safe_area:
        frame_line += ("；" if zh else "; ") + safe_area
    parts.append(frame_line)
    parts.append(str(section.get("text_policy") or ""))
    if not for_frame:
        parts.append(str(section.get("audio") or ""))
    return (HEAD_GLOBAL[0] if zh else HEAD_GLOBAL[1]) + _join(parts, zh)


def beat_line(spec: Mapping[str, Any], beat: Mapping[str, Any]) -> str:
    """一个节拍一行：``0–2.5 秒：<景别>，<画面>；声音：<声音>；画面文字："TEXT"。``"""

    zh = is_zh(spec)
    span = f"{_secs(beat.get('start'))}–{_secs(beat.get('end'))}"
    head = f"{span} 秒：" if zh else f"{span}s: "
    body = [x for x in (str(beat.get("shot") or ""), str(beat.get("picture") or "")) if x]
    line = head + ("，".join(body) if zh else ", ".join(body))
    sound = str(beat.get("sound") or "")
    if sound:
        line += ("；声音：" if zh else "; sound: ") + sound
    text = str(beat.get("text") or "")
    if text:
        # P3：要出现在画面里的文字用引号原样写出来
        line += ('；画面文字："' if zh else '; on-screen text: "') + text + '"'
    return line + ("。" if zh else ".")


def timeline_section(spec: Mapping[str, Any]) -> str:
    """【分段时间线】：绝对时间段，一拍一行，一拍只做一件事。"""

    zh = is_zh(spec)
    beats = list(spec.get("beats") or ())
    if not beats:
        return ""
    lines = [beat_line(spec, beat) for beat in beats]
    return (HEAD_TIMELINE[0] if zh else HEAD_TIMELINE[1]) + "\n".join(lines)


def continuity_section(spec: Mapping[str, Any]) -> str:
    """【衔接与收尾】：一镜到底（或硬切）+ 转场载体 + 最后一帧 + 禁止清单。"""

    zh = is_zh(spec)
    section = spec.get("continuity") or {}
    parts: list[str] = []
    pair = ONE_TAKE if section.get("one_take", True) else HARD_CUTS
    parts.append(pair[0] if zh else pair[1])
    parts.append(str(section.get("transitions") or ""))
    last_frame = str(section.get("last_frame") or "")
    if last_frame:
        parts.append((f"最后一帧：{last_frame}" if zh else f"Last frame: {last_frame}"))
    forbid = [str(f).strip() for f in (section.get("forbid") or ()) if str(f).strip()]
    if forbid:
        parts.append(("禁止：" + "、".join(forbid)) if zh else ("Do not include: " + ", ".join(forbid)))
    return (HEAD_CONTINUITY[0] if zh else HEAD_CONTINUITY[1]) + _join(parts, zh)


# ---------------------------------------------------------------------------- 成条


def _render(sections: list[str], *, dialect: str, ratio: str) -> str:
    sections = [s.strip() for s in sections]
    if dialect == "nano_banana":
        # 数字镜头参数（50mm / f/2.8 / ISO 800）对 Nano Banana 不起作用，剥掉；画幅单独放最后一行
        sections = [D.strip_lens_numbers(s) for s in sections]
        body = "\n".join(s for s in sections if s)
        return D.with_ratio_line(body, ratio)
    return "\n".join(s for s in sections if s)


def compile_clip_prompt(spec: Mapping[str, Any]) -> str:
    """整条视频的提示词：三段按顺序拼起来（缺的段直接不出现）。"""

    sections = [global_section(spec), timeline_section(spec), continuity_section(spec)]
    return _render(sections, dialect=dialect_for(spec), ratio=aspect_ratio(spec))


def compile_frame_prompt(spec: Mapping[str, Any]) -> str:
    """首帧提示词（``first_frame.mode == generate``）：【全片设定】+ 第一个节拍。

    ``first_frame.prompt`` 写了就用它替掉「第一个节拍」那部分（用户自己说这一帧画什么）。
    """

    zh = is_zh(spec)
    own = str((spec.get("first_frame") or {}).get("prompt") or "").strip()
    sections = [global_section(spec, for_frame=True)]
    if own:
        sections.append(own)
    else:
        beats = list(spec.get("beats") or ())
        if beats:
            first = beats[0]
            body = [x for x in (str(first.get("shot") or ""), str(first.get("picture") or "")) if x]
            line = ("，".join(body) if zh else ", ".join(body))
            text = str(first.get("text") or "")
            if text:
                line += ('；画面文字："' if zh else '; on-screen text: "') + text + '"'
            if line:
                sections.append(line + ("。" if zh else "."))
    model = str((spec.get("image_params") or {}).get("model") or "")
    return _render(sections, dialect=resolve_dialect(model, spec.get("dialect")), ratio=aspect_ratio(spec))


def video_request_data(spec: Mapping[str, Any]) -> dict[str, Any]:
    """一条片子的视频请求（哈希 / 提交都用它）。"""

    params = spec.get("video_params") or {}
    first = spec.get("first_frame") or {}
    return {
        "name": str(spec.get("id") or "clip"),
        "prompt": compile_clip_prompt(spec),
        "model": str(params.get("model") or "@video"),
        "duration": int(params.get("duration") or 10),
        "aspect_ratio": aspect_ratio(spec),
        "resolution": str(params.get("resolution") or "1080p"),
        "generate_audio": bool(params.get("generate_audio", False)),
        "first_frame_mode": str(first.get("mode") or "none"),
        "first_frame_asset": str(first.get("asset") or "") or None,
    }


def frame_request_data(spec: Mapping[str, Any]) -> dict[str, Any]:
    """首帧的生图请求。"""

    params = spec.get("image_params") or {}
    return {
        "name": f"{spec.get('id')}-frame",
        "prompt": compile_frame_prompt(spec),
        "model": str(params.get("model") or "@image"),
        "aspect_ratio": aspect_ratio(spec),
        "image_size": str(params.get("image_size") or "2K"),
    }


def beat_preview(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """编辑器右侧的逐拍预览：id + 编译出来的那一行（写法页的时间条也用这个顺序）。"""

    return [{"id": bid, "line": beat_line(spec, beat)}
            for bid, beat in zip(beat_ids(spec), spec.get("beats") or ())]


__all__ = [
    "CLIP_DIALECTS", "FORBIDDEN_ASPECT", "FORBIDDEN_ASPECT_STILL", "HEAD_GLOBAL", "HEAD_TIMELINE", "HEAD_CONTINUITY", "OPENER", "ONE_TAKE", "HARD_CUTS",
    "is_zh", "resolve_dialect", "dialect_for", "style_anchor", "style_card",
    "palette", "aspect_ratio", "forbidden_aspect", "global_section", "timeline_section", "continuity_section",
    "beat_line", "compile_clip_prompt", "compile_frame_prompt", "video_request_data", "frame_request_data", "beat_preview",
]
