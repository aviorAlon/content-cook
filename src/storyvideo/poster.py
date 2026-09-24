"""海报：项目成型时的一张关键画面 + 本地叠上去的标题。

两件事分开做，因为它们的可靠性完全不同：

* **底图**（key visual）走生图模型——跟首帧同一套风格骨架 / 场景锚 / 角色块，只是构图换成海报
  （竖版、主体大、上三分之一留给标题）。跟首帧一样**明令画面里不许出现任何文字**：模型渲染文字不可靠，
  片名一旦被模型画歪就整张作废（DESIGN §20 的文字政策，同一判断）。也可以不花钱：直接拿某一镜的首帧
  （或视频第一帧）当底图。
* **标题**在本机用 Pillow 叠——粗体中文字体（微软雅黑粗体 / 黑体 / 思源黑体 Bold）> 字幕字体解析；底图满幅裁切
  不补边，标题放细节少的一端，背后是取自画面暗部的渐变压暗层，标题带字距与柔和投影，副标题限 24 字。输出固定 1080×1920 PNG。

产物在 ``<output_root>/07_poster/``：``base-<毫秒>.png``（底图）、``poster-<毫秒>.png``（成品）、``poster.json``
（每次生成一条：提示词、模型、底图来源、标题、时间）。**从不覆盖**，最新一条就是当前海报。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from storyvideo.prompts import (
    FRAME_STYLE,
    NO_TEXT_TAIL_CN,
    NO_TEXT_TAIL_EN,
    cast_anchor_asset,
    cast_block_text,
    shot_cast_ids,
)
from storyvideo.spec import split_cast_ref
from storyvideo.state import atomic_write_json, read_json
from storyvideo.subtitles import resolve_font

POSTER_SUBDIR = "07_poster"
POSTER_WIDTH, POSTER_HEIGHT = 1080, 1920
MAX_CAST = 2

COMPOSITION_CN = (
    "这是一张竖版电影海报的关键画面（9:16）：主体人物占画面大部分、面向观众方向，姿态与眼神有戏；"
    "画面上三分之一保持干净、少细节、背景色调偏暗，留给片名；电影级主光从一侧打来，轮廓光分离人物与背景，"
    "景深浅、氛围浓，细节精致、成片质感。"
)
COMPOSITION_EN = (
    "This is the key visual of a vertical movie poster (9:16): the main character fills most of the frame, facing "
    "the viewer with a charged pose and gaze; the upper third stays calm, low in detail and darker in tone, reserved "
    "for the title; cinematic key light from one side with a rim light separating the figure from the background, "
    "shallow depth of field, thick atmosphere, polished finished-poster quality. "
)
NEGATIVE_EXTRA_CN = "文字,字母,数字,水印,logo,标题,字幕"
NEGATIVE_EXTRA_EN = "text,letters,numbers,watermark,logo,title,caption"


class PosterError(RuntimeError):
    """海报生成 / 叠字失败的人话原因。"""


# ---------------------------------------------------------------------------- 路径与清单


def poster_dir(spec: Mapping[str, Any]) -> Path:
    return Path(str(spec["output_root"])) / POSTER_SUBDIR


def manifest_path(spec: Mapping[str, Any]) -> Path:
    return poster_dir(spec) / "poster.json"


def poster_paths(spec: Mapping[str, Any], stamp: int | None = None) -> dict[str, Any]:
    """这一次生成用的文件位置：``base-<毫秒>.png`` / ``poster-<毫秒>.png`` / ``poster.json``。毫秒戳保证不覆盖。"""

    stamp = int(stamp if stamp is not None else time.time() * 1000)
    root = poster_dir(spec)
    while (root / f"poster-{stamp}.png").exists() or (root / f"base-{stamp}.png").exists():
        stamp += 1
    return {"dir": root, "base": root / f"base-{stamp}.png", "poster": root / f"poster-{stamp}.png",
            "manifest": manifest_path(spec), "stamp": stamp}


def load_manifest(spec: Mapping[str, Any]) -> dict[str, Any]:
    data = read_json(manifest_path(spec), missing={}) or {}
    if not isinstance(data.get("entries"), list):
        data["entries"] = []
    return data


def record(spec: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
    """往 poster.json 追加一条（只追加，不删不改）。"""

    manifest = load_manifest(spec)
    manifest["schema_version"] = 1
    manifest["entries"].append(dict(entry))
    atomic_write_json(manifest_path(spec), manifest)
    return dict(entry)


def _entry_exists(spec: Mapping[str, Any], entry: Mapping[str, Any]) -> bool:
    name = str(entry.get("poster") or "")
    return bool(name) and (poster_dir(spec) / name).is_file()


def history(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """已有的海报（新到旧），只列成品文件还在的。"""

    rows = [dict(e) for e in load_manifest(spec)["entries"] if isinstance(e, Mapping) and _entry_exists(spec, e)]
    rows.sort(key=lambda e: float(e.get("created_at") or 0), reverse=True)
    return rows


def latest(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = history(spec)
    return rows[0] if rows else None


def latest_poster_path(spec: Mapping[str, Any]) -> Path | None:
    """当前海报文件（最新一条成品）；没有 = None。侧栏封面与交付包用。"""

    entry = latest(spec)
    return poster_dir(spec) / str(entry["poster"]) if entry else None


def latest_base_path(spec: Mapping[str, Any]) -> Path | None:
    """最新一条的底图（重排文字时复用）；文件不在了就往前找。"""

    for entry in history(spec):
        base = poster_dir(spec) / str(entry.get("base") or "")
        if entry.get("base") and base.is_file():
            return base
    return None


# ---------------------------------------------------------------------------- 提示词


def _is_zh(spec: Mapping[str, Any]) -> bool:
    return str(spec.get("prompt_language") or "en").strip().lower().startswith("zh")


def _shots(spec: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]


def present_cast(spec: Mapping[str, Any], limit: int = MAX_CAST) -> list[str]:
    """出镜最多的 1–2 个角色 id（按镜数计，变体不分开算；同镜数按首次出场先后）。
    只算有 block 的人物成员——场景 / 道具块没有 name，不当海报主体。"""

    cast = spec.get("cast") or {}
    counts: dict[str, int] = {}
    order: dict[str, int] = {}
    for index, shot in enumerate(_shots(spec)):
        for member_id in dict.fromkeys(shot_cast_ids(shot)):
            member = cast.get(member_id)
            if not isinstance(member, Mapping) or not str(member.get("block") or "").strip():
                continue
            explicit = str(member.get("kind") or "").strip()
            is_person = explicit == "character" if explicit else bool(member.get("name") or member.get("voice"))
            if not is_person:
                continue
            counts[member_id] = counts.get(member_id, 0) + 1
            order.setdefault(member_id, index)
    ranked = sorted(counts, key=lambda m: (-counts[m], order[m]))
    return ranked[:limit]


def hook_shot(spec: Mapping[str, Any], base_shot: str | None = None) -> Mapping[str, Any] | None:
    shots = _shots(spec)
    if base_shot:
        for shot in shots:
            if str(shot.get("id")) == str(base_shot):
                return shot
    return shots[0] if shots else None


def hook_text(spec: Mapping[str, Any], base_shot: str | None = None) -> str:
    """钩子画面：指定了底图镜就用它的 art；否则单集契约的 hook，再否则第一镜的 art。"""

    if base_shot:
        shot = hook_shot(spec, base_shot)
        art = str((shot or {}).get("art") or "").strip()
        if art:
            return art
    contract = spec.get("episode_contract")
    if isinstance(contract, Mapping) and str(contract.get("hook") or "").strip():
        return str(contract["hook"]).strip()
    shot = hook_shot(spec)
    return str((shot or {}).get("art") or "").strip()


def _sentence(text: str, zh: bool) -> str:
    text = text.strip()
    if not text:
        return ""
    if zh:
        return text if text.endswith(("。", "！", "？", "；")) else text + "。"
    return (text if text.endswith((".", "!", "?", ";")) else text + ".") + " "


def poster_prompt(spec: Mapping[str, Any], *, base_shot: str | None = None, style_card: str | None = None) -> dict[str, str]:
    """无文字的关键画面提示词：风格骨架 + 场景锚 + 出镜最多的角色块 + 钩子画面 + 海报构图 + 「不出现文字」。

    ``zh`` / ``en`` 各一份（骨架、锚、角色块是 spec 里写好的原文，两份共用；构图句与禁字句按语言）；
    ``prompt`` 是项目提示词语言那一份。标题**不进**提示词——文字在本机叠。

    ``style_card``（可空，DESIGN §41）：给了就用这张风格卡的锚句顶掉视觉风格骨架，只影响这一次海报；
    没给的时候一个字不变。"""

    from imagecraft import cards as CARDS

    zh = _is_zh(spec)
    card = CARDS.find_card(style_card if style_card is not None else spec.get("style_card"))
    style = str(spec.get("frame_style") or FRAME_STYLE)
    anchor = str(spec.get("scene_anchor") or "")
    members = present_cast(spec)
    hook = hook_text(spec, base_shot)

    def build(lang_zh: bool) -> str:
        parts = [_sentence(CARDS.anchor_text(card, lang_zh), lang_zh) if card else style, anchor]
        for member_id in members:
            parts.append(cast_block_text(spec, member_id))
        parts.append(_sentence(hook, lang_zh))
        parts.append(COMPOSITION_CN if lang_zh else COMPOSITION_EN)
        parts.append(NO_TEXT_TAIL_CN if lang_zh else NO_TEXT_TAIL_EN)
        return "".join(p for p in parts if p)

    neg = str(spec.get("neg") or "").strip()
    negative = ",".join(v for v in (neg, NEGATIVE_EXTRA_CN if zh else NEGATIVE_EXTRA_EN) if v)
    out = {"zh": build(True), "en": build(False), "negative": negative, "language": "zh" if zh else "en",
           "cast": list(members), "hook": hook, "style_card": str(card["id"]) if card else ""}
    out["prompt"] = out["zh"] if zh else out["en"]
    return out


def poster_reference_asset_ids(spec: Mapping[str, Any]) -> tuple[str, ...]:
    """参考图：入选角色的锚图（跟首帧请求的角色槽同一来源），去重保序。"""

    refs = [cast_anchor_asset(spec, split_cast_ref(m)[0]) for m in present_cast(spec)]
    return tuple(dict.fromkeys(r for r in refs if r))


def poster_request_data(spec: Mapping[str, Any], *, base_shot: str | None = None, style_card: str | None = None) -> dict[str, Any]:
    """给 provider 的请求字段：模型 / 尺寸 / 工作区沿用 image_params 与首帧请求，画幅固定 9:16。

    参考图跟首帧请求同一来源（``cast_anchor_asset``）：规范化后的 spec 里本地锚图已经是绝对路径，
    适配器自己负责上传 / 转 data URL。"""

    params = spec.get("image_params") or {}
    model = str(params.get("model") or "gpt-image-2")
    prompt = poster_prompt(spec, base_shot=base_shot, style_card=style_card)
    return {
        "name": f"{spec.get('id', 'story')}-poster",
        "prompt": prompt["prompt"],
        "negative_prompt": prompt["negative"] or None,
        "model": model,
        "aspect_ratio": "9:16",
        "image_size": str(params.get("image_size") or "2K"),
        "quality": None if model.lower().startswith(("doubao-seedream", "@")) or "gpt-image" not in model else str(params.get("quality") or "high"),
        "reference_asset_ids": poster_reference_asset_ids(spec),
        "workspace_id": str(spec.get("workspace_id") or "") or None,
    }


# ---------------------------------------------------------------------------- 标题建议


def suggested_title(spec: Mapping[str, Any]) -> str:
    return str(spec.get("title_zh") or spec.get("title_en") or spec.get("id") or "").strip()


def suggested_kicker(spec: Mapping[str, Any]) -> str:
    """标题上方的小标：系列集数「第 3 集」> 形态卡名（竖屏短剧）> 空。"""

    series = spec.get("series")
    if isinstance(series, Mapping):
        try:
            no = int(series.get("episode_no") or 0)
        except (TypeError, ValueError):
            no = 0
        if no > 0:
            return f"第 {no} 集" if _is_zh(spec) else f"Episode {no}"
    if spec.get("form"):
        from storyvideo.forms import find_form

        card = find_form(spec.get("form"))
        if card:
            return str(card.get("name") if _is_zh(spec) else (card.get("english") or {}).get("name") or card.get("name") or "")
    return ""


def suggested_subtitle(spec: Mapping[str, Any]) -> str:
    """副标题：单集契约的一句话前提 → logline → 英文片名（中文片名做主标题时）。超 24 字在标点处截断。"""

    contract = spec.get("episode_contract")
    candidates = [
        str(contract.get("premise") or "") if isinstance(contract, Mapping) else "",
        str(spec.get("logline") or ""),
        str(spec.get("title_en") or "") if spec.get("title_zh") else "",
    ]
    for text in candidates:
        text = " ".join(text.split())
        if text:
            return clean_subtitle(text)
    return ""


# ---------------------------------------------------------------------------- 叠字（Pillow）

# 设计规则：
#   1. 底图满幅裁切到 9:16，绝不补黑边；
#   2. 标题放在细节最少的那一端（上/下自动判断，也可指定），背后是从画面边缘往里渐隐的压暗层，颜色取自底图
#      自己的暗部，不是一条平涂黑带；
#   3. 标题用粗体中文字体（微软雅黑粗体 / 黑体 / 思源黑体 Bold），按宽度定字号、最多两行、带字距与柔和投影；
#   4. 副标题限 24 字内，超出在标点处截断，最多两行；
#   5. 可选小标（「第 3 集」/ 形态名）+ 一条金色细线，做视觉层级。
TITLE_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Bold.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
)
BODY_FONT_FOR = {"msyhbd.ttc": "msyh.ttc", "simhei.ttf": "simhei.ttf"}
LAYOUTS = ("auto", "top", "bottom")
SUBTITLE_MAX_CHARS = 24
TITLE_COLOR = (247, 241, 230, 255)
SUBTITLE_COLOR = (233, 225, 211, 235)
ACCENT_COLOR = (217, 178, 111, 235)
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_BREAK_PUNCT = "，。！？；：——…、,.!?;:"


def _is_cjk_text(text: str) -> bool:
    return bool(_CJK.search(text))


def _wrap_title(title: str) -> list[str]:
    """标题超过 8 个字（或英文过长）分两行：中文按字数对半，英文按词对半。"""

    title = " ".join(title.split())
    cjk = _is_cjk_text(title)
    if (cjk and len(title) <= 8) or (not cjk and len(title) <= 16):
        return [title]
    words = title.split(" ")
    if len(words) > 1:
        mid = max(1, len(words) // 2)
        return [" ".join(words[:mid]), " ".join(words[mid:])]
    mid = (len(title) + 1) // 2
    return [title[:mid], title[mid:]]


def ffmpeg_available(ffmpeg: str = "ffmpeg") -> bool:
    return shutil.which(ffmpeg) is not None


def clean_subtitle(text: str | None, limit: int = SUBTITLE_MAX_CHARS) -> str:
    """副标题：压空白；超过 ``limit`` 字在最后一个标点处截断（至少保留 8 字），再不行硬截加省略号。
    英文按字母算，阈值放宽到 2.5 倍（24 个汉字 ≈ 60 个字母的版面宽度）。"""

    value = " ".join(str(text or "").split())
    if not _is_cjk_text(value):
        limit = int(limit * 2.5)
    if len(value) <= limit:
        return value
    cut = value[:limit]
    best = max((cut.rfind(ch) for ch in _BREAK_PUNCT), default=-1)
    if best >= 8:
        return cut[:best].rstrip(_BREAK_PUNCT + " ")
    return cut[: limit - 1].rstrip() + "…"


def _font_candidates(font: str | None, style: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    """(标题字体, 正文字体)。显式给了就都用它；否则粗体候选 → 字幕字体；正文字体尽量用同家族的常规体。"""

    explicit = str(font or "").strip()
    if explicit and Path(explicit).expanduser().is_file():
        return explicit, explicit
    title_font = next((c for c in TITLE_FONT_CANDIDATES if Path(c).is_file()), None) or resolve_font(style)
    if not title_font or not Path(title_font).expanduser().is_file():
        return None, None
    body = Path(title_font).with_name(BODY_FONT_FOR.get(Path(title_font).name, Path(title_font).name))
    return title_font, str(body if body.is_file() else title_font)


def _load_font(path: str, size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(path, size=size)
    except OSError as exc:
        raise PosterError(f"字体读不出来：{Path(path).name}（{exc}）") from exc


def _text_width(font, text: str, tracking: float) -> int:
    if not text:
        return 0
    return int(sum(font.getlength(ch) for ch in text) + tracking * (len(text) - 1))


def _draw_tracked(draw, xy: tuple[int, int], text: str, font, fill, tracking: float) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking


def _fit_cover(im, size: tuple[int, int] | None = None):
    """等比放大到盖满目标尺寸（默认 1080×1920）后居中裁切——不补边。"""

    from PIL import Image

    width, height = size or (POSTER_WIDTH, POSTER_HEIGHT)
    im = im.convert("RGB")
    scale = max(width / im.width, height / im.height)
    resized = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _band_busyness(im, top: bool, fraction: float = 0.34) -> float:
    """一端的「忙碌度」：边缘强度均值（细节多少）+ 灰度标准差的一半 + 亮度偏亮的惩罚（亮且细节多的地方叠字最难读）。"""

    from PIL import ImageFilter, ImageStat

    # 尺寸从画布本身取（海报是 1080×1920，图卡是别的画幅，同一套判据）
    h = int(im.height * fraction)
    box = (0, 0, im.width, h) if top else (0, im.height - h, im.width, im.height)
    gray = im.crop(box).convert("L").resize((360, max(1, int(h / 3))))
    edges = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES))
    stat = ImageStat.Stat(gray)
    return float(edges.mean[0]) + float(stat.stddev[0]) * 0.5 + max(0.0, float(stat.mean[0]) - 110) * 0.5


def pick_layout(im, layout: str = "auto") -> str:
    if layout in ("top", "bottom"):
        return layout
    return "top" if _band_busyness(im, True) <= _band_busyness(im, False) + 4 else "bottom"


def _scrim_color(im, top: bool) -> tuple[int, int, int]:
    """压暗层颜色：取那一端的平均色再压到很暗，让渐变跟画面同色系而不是纯黑。"""

    from PIL import ImageStat

    h = int(im.height * 0.3)
    box = (0, 0, im.width, h) if top else (0, im.height - h, im.width, im.height)
    r, g, b = (ImageStat.Stat(im.crop(box).resize((60, 60))).mean + [0, 0, 0])[:3]
    return int(r * 0.18), int(g * 0.18), int(b * 0.22)


def _scrim(im, top: bool, height: int, color: tuple[int, int, int], strength: float = 0.86):
    """从画面边缘 ``strength`` 不透明度渐隐到 0 的压暗层（二次缓动，避免看出分界线）。"""

    from PIL import Image

    height = max(1, min(im.height, height))
    grad = Image.linear_gradient("L").resize((1, height))          # 0 → 255 从上到下
    if top:
        grad = grad.transpose(Image.FLIP_TOP_BOTTOM)                    # 上端最不透明
    alpha = grad.point(lambda v: int(strength * 255 * (v / 255) ** 1.6))
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    band = Image.new("RGBA", (im.width, height), color + (255,))
    band.putalpha(alpha.resize((im.width, height)))
    layer.paste(band, (0, 0 if top else im.height - height))
    return layer


def _wrap_by_width(text: str, font, max_width: int, tracking: float, max_lines: int = 2) -> list[str]:
    """副标题按像素宽折行：中文逐字、英文逐词；超过 max_lines 行的部分丢掉并加省略号。"""

    units = list(text) if _is_cjk_text(text) else text.split(" ")
    joiner = "" if _is_cjk_text(text) else " "
    lines: list[str] = []
    current = ""
    for unit in units:
        candidate = (current + joiner + unit) if current else unit
        if _text_width(font, candidate, tracking) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = unit
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip("，。、,. ") + "…"
    # 中文两行时对半分，别让第二行只剩一两个字（孤字行很难看）
    if len(lines) == 2 and joiner == "" and len(lines[1]) < max(3, len(lines[0]) // 3):
        whole = lines[0] + lines[1]
        mid = (len(whole) + 1) // 2
        lines = [whole[:mid], whole[mid:]]
    return lines


def _shadowed_text(canvas, draw_fn, size: int) -> None:
    """先把文字画到透明层做模糊投影，再叠正文——比描边柔和得多。"""

    from PIL import Image, ImageDraw, ImageFilter

    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer), (0, 0, 0, 255))
    shadow = layer.filter(ImageFilter.GaussianBlur(max(2, size * 0.07)))
    shadow = Image.eval(shadow.split()[3], lambda a: int(a * 0.72))
    canvas.alpha_composite(Image.merge("RGBA", (Image.new("L", canvas.size, 0),) * 3 + (shadow,)), (0, int(size * 0.05)))
    text_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(text_layer), None)
    canvas.alpha_composite(text_layer)


def render_poster(
    base_png: str | Path,
    out_png: str | Path,
    *,
    title: str,
    subtitle: str | None = None,
    font: str | None = None,
    style: Mapping[str, Any] | None = None,
    layout: str = "auto",
    kicker: str | None = None,
    ffmpeg: str = "ffmpeg",
    canvas_size: tuple[int, int] | None = None,
) -> Path:
    """底图 → 满幅裁到 1080×1920 → 自动选一端压暗渐变 → 小标 / 金线 / 标题 / 副标题 → PNG。

    ``layout``：auto（细节少的一端）/ top / bottom。``kicker``：标题上方的小标（「第 3 集」/ 形态名），可空。
    ``canvas_size``：输出画布尺寸，默认 1080×1920（海报）；图片工作台的图卡按自己的画幅传（同一段叠字代码）。
    字体：显式路径 > 粗体中文候选 > 字幕字体解析。``out_png`` 已存在时拒绝——海报从不覆盖。返回输出路径，
    并把实际用的布局写在 ``render_poster.last_layout``。"""

    from PIL import Image

    base = Path(base_png)
    out = Path(out_png)
    title = " ".join(str(title or "").split())
    if not title:
        raise PosterError("海报标题不能为空")
    if not base.is_file():
        raise PosterError(f"海报底图不存在：{base.name}")
    if out.exists():
        raise PosterError(f"海报文件已存在，不覆盖：{out.name}")
    if layout not in LAYOUTS:
        raise PosterError(f"海报布局只认 auto / top / bottom，给的是 {layout!r}")
    title_font_path, body_font_path = _font_candidates(font, style)
    if not title_font_path:
        raise PosterError("没找到可用的中文字体——在字幕样式里指定字体文件，或把字体装进系统")
    try:
        source = Image.open(base)
        source.load()
    except OSError as exc:
        raise PosterError(f"海报底图读不出来：{base.name}（{exc}）") from exc

    canvas = _fit_cover(source, canvas_size).convert("RGBA")
    width, height = canvas.size
    chosen = pick_layout(canvas, layout)
    top = chosen == "top"

    # ---- 排版度量
    margin_x = int(width * 0.08)
    max_width = width - margin_x * 2
    lines = _wrap_title(title)
    cjk = _is_cjk_text(title)
    size = 176 if cjk else 150
    while size > 64:
        f = _load_font(title_font_path, size)
        tracking = size * (0.06 if cjk else 0.01)
        if max(_text_width(f, line, tracking) for line in lines) <= max_width:
            break
        size -= 6
    title_font = _load_font(title_font_path, size)
    tracking = size * (0.06 if cjk else 0.01)
    line_h = int(size * 1.16)

    sub = clean_subtitle(subtitle)
    sub_size = 42
    sub_font = _load_font(body_font_path, sub_size)
    sub_tracking = sub_size * 0.04
    sub_lines = _wrap_by_width(sub, sub_font, max_width, sub_tracking) if sub else []
    sub_line_h = int(sub_size * 1.4)

    kick = " ".join(str(kicker or "").split())
    kick_size = 30
    kick_font = _load_font(body_font_path, kick_size)
    kick_tracking = kick_size * 0.32

    block_h = line_h * len(lines)
    if kick:
        block_h += kick_size + 26 + 22          # 小标 + 间距 + 金线区
    if sub_lines:
        block_h += 22 + sub_line_h * len(sub_lines)
    edge = int(height * (0.11 if top else 0.09))
    block_top = edge if top else height - edge - block_h

    # ---- 压暗层：覆盖文字块并再往画面里延伸一段
    scrim_h = block_h + edge + int(height * 0.16)
    canvas.alpha_composite(_scrim(canvas, top, scrim_h, _scrim_color(canvas, top)))

    # ---- 文字
    y = block_top
    if kick:
        kw = _text_width(kick_font, kick, kick_tracking)
        kx = (width - kw) // 2

        def draw_kick(draw, fill, _y=y, _x=kx):
            _draw_tracked(draw, (_x, _y), kick, kick_font, fill or ACCENT_COLOR, kick_tracking)

        _shadowed_text(canvas, draw_kick, kick_size)
        y += kick_size + 26
        from PIL import ImageDraw

        ImageDraw.Draw(canvas).rectangle((width // 2 - 44, y, width // 2 + 44, y + 3), fill=ACCENT_COLOR)
        y += 22

    for line in lines:
        w = _text_width(title_font, line, tracking)
        x = (width - w) // 2

        def draw_line(draw, fill, _y=y, _x=x, _line=line):
            _draw_tracked(draw, (_x, _y), _line, title_font, fill or TITLE_COLOR, tracking)

        _shadowed_text(canvas, draw_line, size)
        y += line_h

    if sub_lines:
        y += 22
        for line in sub_lines:
            w = _text_width(sub_font, line, sub_tracking)
            x = (width - w) // 2

            def draw_sub(draw, fill, _y=y, _x=x, _line=line):
                _draw_tracked(draw, (_x, _y), _line, sub_font, fill or SUBTITLE_COLOR, sub_tracking)

            _shadowed_text(canvas, draw_sub, sub_size)
            y += sub_line_h

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.tmp.png")
    try:
        canvas.convert("RGB").save(tmp, format="PNG", optimize=True)
        tmp.replace(out)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise PosterError(f"海报写盘失败：{exc}") from exc
    render_poster.last_layout = chosen   # type: ignore[attr-defined]
    return out


render_poster.last_layout = "auto"   # type: ignore[attr-defined]


def extract_first_frame(video: str | Path, out_png: str | Path, *, ffmpeg: str = "ffmpeg") -> Path:
    """视频第一帧 → PNG（某一镜没有首帧文件、只有视频时当底图用）。"""

    video = Path(video)
    out = Path(out_png)
    if not video.is_file():
        raise PosterError(f"视频不存在：{video.name}")
    if not ffmpeg_available(ffmpeg):
        raise PosterError("没找到 ffmpeg，抽视频首帧要用它——把 ffmpeg 放进 PATH 再试")
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-frames:v", "1",
                           "-update", "1", str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not out.is_file():
        raise PosterError(f"抽取视频首帧失败（ffmpeg）：{(proc.stderr or '').strip()[-600:] or '没有输出'}")
    return out


def cost_note(model: str) -> str:
    # 生图角色配的是本机来源（ComfyUI / SD WebUI / 本地兼容接口）时不报模型名当"要花的钱"，
    # 直接说不花钱——本地跑一张海报的成本是电费（DESIGN §45）
    from genkit.local import cost_note_for

    return cost_note_for("image", 1, f"1 次生图请求（{model}）")


__all__ = [
    "POSTER_SUBDIR", "POSTER_WIDTH", "POSTER_HEIGHT", "PosterError",
    "poster_dir", "manifest_path", "poster_paths", "load_manifest", "record", "history", "latest",
    "latest_poster_path", "latest_base_path",
    "present_cast", "hook_text", "poster_prompt", "poster_reference_asset_ids", "poster_request_data",
    "suggested_title", "suggested_subtitle", "render_poster", "clean_subtitle", "pick_layout", "suggested_kicker", "LAYOUTS", "SUBTITLE_MAX_CHARS", "extract_first_frame", "ffmpeg_available", "cost_note",
]
