"""Pillow 后处理（契约 §2 ``post.py``）：本机、不花钱。

* 白底 / 纯色底转透明：四角取样定底色，从边缘做容差泛洪（``ImageDraw.floodfill`` 的 ``thresh``），泛洪到的像素透明，
  再收 1 像素 + 轻羽化去白边。不用 rembg——模板已经让模型出纯白底、主体外无投影，泛洪足够。
* 尺寸：微信表情主图 240×240 PNG ≤ 500 KB（超了就量化到 256 色以下）、缩略 120×120、面板图标 50×50、横幅 750×400；
  头像 1080 / 512 / 200（可选圆形蒙版）；写真 3:4 主版 + 4:3 副版居中裁切。
* 叠字：复用海报的字体解析（``_font_candidates`` / ``_load_font``）、字距（``_draw_tracked``）与柔和投影（``_shadowed_text``）。
* 产物在 ``<output_root>/05_out/``，文件名带运行戳，**从不覆盖**；``post.json`` 每次运行追加一条（最新一条就是当前产物）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Mapping

from storyvideo.poster import (
    PosterError,
    _draw_tracked,
    _font_candidates,
    _load_font,
    _shadowed_text,
    _text_width,
    render_poster,
)
from storyvideo.state import atomic_write_json, read_json

from .spec import current_path, out_dir, picked_items

WECHAT_MAIN = 240
WECHAT_THUMB = 120
WECHAT_ICON = 50
WECHAT_BANNER = (750, 400)
WECHAT_MAX_BYTES = 500 * 1024
AVATAR_SIZES = (1080, 512, 200)
STICKER_SIZES = (512, 1024)
IMAGE_LONG_SIDE = 2048     # 图片工作台：主版之外再出一版长边 2048（贴社交平台够用、体积小）
CARD_SHORT_SIDE = 1080     # 图卡画布的短边（3:4 → 1080×1440）
TITLE_COLOR = (247, 241, 230, 255)
CAPTION_COLOR = (40, 40, 40, 255)
ACCENT_COLOR = (217, 178, 111, 235)


class PostError(RuntimeError):
    """后处理失败的人话原因。"""


# ---------------------------------------------------------------------------- 基础


def open_rgba(path: str | Path):
    from PIL import Image

    source = Path(path)
    if not source.is_file():
        raise PostError(f"图片不存在：{source.name}")
    try:
        im = Image.open(source)
        im.load()
    except OSError as exc:
        # 只带文件名和异常类型：PIL 的原文里有本机绝对路径（cannot identify image file 'C:\\…'），会原样进界面
        raise PostError(f"图片读不出来：{source.name}（{type(exc).__name__}）") from exc
    return im.convert("RGBA")


def _color_diff(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    return sum(abs(int(x) - int(y)) for x, y in zip(a[:3], b[:3]))


def background_color(im) -> tuple[int, int, int]:
    """四角 + 四边中点取样，按通道取中位数——比只看左上角稳（角上偶尔有一点脏）。"""

    rgb = im.convert("RGB")
    w, h = rgb.size
    points = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    samples = [rgb.getpixel(p) for p in points]
    channels = []
    for i in range(3):
        values = sorted(s[i] for s in samples)
        channels.append(values[len(values) // 2])
    return tuple(channels)  # type: ignore[return-value]


def to_transparent(im, *, tolerance: int = 60, feather: float = 0.8):
    """底色（四角取样）连通区域透明；主体内部同色的封闭区域不受影响（泛洪只从边缘进）。"""

    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    rgba = im.convert("RGBA")
    rgb = rgba.convert("RGB")
    bg = background_color(rgb)
    sentinel = tuple((c + 128) % 256 for c in bg)
    work = rgb.copy()
    w, h = work.size
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    for seed in seeds:
        pixel = work.getpixel(seed)
        if pixel == sentinel or _color_diff(pixel, bg) > tolerance:
            continue
        ImageDraw.floodfill(work, seed, sentinel, thresh=tolerance)
    changed = ImageChops.difference(work, rgb).convert("L")
    alpha = changed.point(lambda v: 0 if v > 0 else 255)
    # 收 1 像素去掉底色沾染的边，再轻羽化
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(feather))
    alpha = ImageChops.multiply(alpha, rgba.split()[3])
    out = rgba.copy()
    out.putalpha(alpha)
    return out


def content_bbox(im) -> tuple[int, int, int, int]:
    alpha = im.convert("RGBA").split()[3]
    bbox = alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
    return bbox or (0, 0, im.width, im.height)


def trim(im, margin: float = 0.0):
    left, top, right, bottom = content_bbox(im)
    mx = int((right - left) * margin)
    my = int((bottom - top) * margin)
    return im.crop((max(0, left - mx), max(0, top - my), min(im.width, right + mx), min(im.height, bottom + my)))


def fit_square(im, size: int, *, pad: float = 0.06, background: tuple[int, int, int, int] | None = None):
    """裁到内容 → 等比缩到 ``size×size`` 内（四周留 ``pad``）→ 居中放在透明（或指定底色）画布上。"""

    from PIL import Image

    content = trim(im.convert("RGBA"))
    inner = max(1, int(size * (1 - 2 * pad)))
    scale = min(inner / content.width, inner / content.height)
    resized = content.resize((max(1, round(content.width * scale)), max(1, round(content.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), background or (0, 0, 0, 0))
    canvas.alpha_composite(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
    return canvas


def cover(im, width: int, height: int):
    """等比放大到盖满后居中裁切——不补边（跟海报 ``_fit_cover`` 同一规则，尺寸参数化）。"""

    from PIL import Image

    im = im.convert("RGBA")
    scale = max(width / im.width, height / im.height)
    resized = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def parse_aspect(aspect: str) -> tuple[int, int]:
    try:
        a, b = str(aspect).replace("x", ":").split(":")
        w, h = int(a), int(b)
    except (TypeError, ValueError) as exc:
        raise PostError(f"画幅要写成 3:4 这样的形状，收到「{aspect}」") from exc
    if w <= 0 or h <= 0:
        raise PostError(f"画幅要写成 3:4 这样的形状，收到「{aspect}」")
    return w, h


def crop_aspect(im, aspect: str, *, max_side: int | None = None):
    """居中裁切到目标画幅（不缩放、不补边）；``max_side`` 给了再等比缩到长边不超过它。"""

    from PIL import Image

    aw, ah = parse_aspect(aspect)
    im = im.convert("RGBA")
    target_ratio = aw / ah
    if im.width / im.height > target_ratio:
        new_w = max(1, round(im.height * target_ratio))
        left = (im.width - new_w) // 2
        out = im.crop((left, 0, left + new_w, im.height))
    else:
        new_h = max(1, round(im.width / target_ratio))
        top = (im.height - new_h) // 2
        out = im.crop((0, top, im.width, top + new_h))
    if max_side and max(out.size) > max_side:
        scale = max_side / max(out.size)
        out = out.resize((max(1, round(out.width * scale)), max(1, round(out.height * scale))), Image.LANCZOS)
    return out


def circle(im):
    """圆形蒙版（正方形输入；非正方形先居中裁成正方形）。"""

    from PIL import Image, ImageDraw

    im = im.convert("RGBA")
    side = min(im.size)
    if im.size != (side, side):
        im = cover(im, side, side)
    mask = Image.new("L", (side * 4, side * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, side * 4 - 1, side * 4 - 1), fill=255)
    mask = mask.resize((side, side), Image.LANCZOS)
    from PIL import ImageChops

    out = im.copy()
    out.putalpha(ImageChops.multiply(im.split()[3], mask))
    return out


def head_crop(im, size: int):
    """取内容框上半段的正方形（头部）缩到 ``size``——面板图标用。"""

    im = im.convert("RGBA")
    left, top, right, bottom = content_bbox(im)
    w, h = right - left, bottom - top
    side = max(1, min(w, int(h * 0.55)))
    cx = left + w // 2
    box = (max(0, cx - side // 2), top, min(im.width, cx - side // 2 + side), min(im.height, top + side))
    return fit_square(im.crop(box), size, pad=0.04)


# ---------------------------------------------------------------------------- 写盘


def next_free(path: Path) -> Path:
    """已存在就 ``名-2`` / ``名-3``…——产物从不覆盖。"""

    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def save_png(im, path: str | Path, *, max_bytes: int | None = None) -> Path:
    """PNG 写盘；超过 ``max_bytes`` 就逐级量化（256 → 128 → 64 → 32 色）直到达标，仍超就报错。"""

    from PIL import Image

    target = next_free(Path(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.stem}.tmp.png")
    candidates: list[Any] = [im]
    if max_bytes:
        for colors in (256, 128, 64, 32):
            candidates.append(("quantize", colors))
    try:
        for candidate in candidates:
            picture = im
            if isinstance(candidate, tuple):
                picture = im.convert("RGBA").quantize(colors=candidate[1], method=Image.Quantize.FASTOCTREE)
            picture.save(tmp, format="PNG", optimize=True)
            if not max_bytes or tmp.stat().st_size <= max_bytes:
                tmp.replace(target)
                return target
        tmp.unlink(missing_ok=True)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise PostError(f"图片写盘失败：{exc}") from exc
    raise PostError(f"{target.name} 量化后仍超过 {max_bytes // 1024} KB")


def save_jpeg(im, path: str | Path, *, quality: int = 90) -> Path:
    target = next_free(Path(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.stem}.tmp.jpg")
    try:
        im.convert("RGB").save(tmp, format="JPEG", quality=quality, optimize=True)
        tmp.replace(target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise PostError(f"图片写盘失败：{exc}") from exc
    return target


# ---------------------------------------------------------------------------- 叠字


def font_available(font: str | None = None) -> bool:
    return _font_candidates(font, None)[0] is not None


def overlay_text(im, text: str, *, subtitle: str = "", position: str = "bottom", font: str | None = None,
                 color: tuple[int, int, int, int] = TITLE_COLOR, scale: float = 0.075, band: bool = True):
    """居中叠一行主字（可带一行副字）：字号按画面宽度定，最多缩到 24 像素；``band`` 在文字后面压一层渐变。"""

    from PIL import Image

    text = " ".join(str(text or "").split())
    if not text:
        return im.convert("RGBA")
    if position not in ("top", "bottom"):
        raise PostError("叠字位置只认 top / bottom")
    title_font_path, body_font_path = _font_candidates(font, None)
    if not title_font_path:
        raise PostError("没找到可用的中文字体——在字幕样式里指定字体文件，或把字体装进系统")
    canvas = im.convert("RGBA").copy()
    width, height = canvas.size
    max_width = int(width * 0.86)
    size = max(24, int(width * scale))
    cjk = any("㐀" <= ch <= "鿿" for ch in text)
    while size > 24:
        f = _load_font(title_font_path, size)
        if _text_width(f, text, size * (0.06 if cjk else 0.01)) <= max_width:
            break
        size -= 2
    title_font = _load_font(title_font_path, size)
    tracking = size * (0.06 if cjk else 0.01)
    sub = " ".join(str(subtitle or "").split())
    sub_size = max(16, int(size * 0.45))
    sub_font = _load_font(body_font_path or title_font_path, sub_size)
    block_h = int(size * 1.2) + (int(sub_size * 1.5) if sub else 0)
    edge = int(height * 0.06)
    y = edge if position == "top" else height - edge - block_h
    if band:
        band_h = block_h + edge * 2
        grad = Image.linear_gradient("L").resize((1, band_h))
        if position == "top":
            grad = grad.transpose(Image.FLIP_TOP_BOTTOM)
        alpha = grad.point(lambda v: int(0.55 * v))
        layer = Image.new("RGBA", (width, band_h), (0, 0, 0, 255))
        layer.putalpha(alpha.resize((width, band_h)))
        canvas.alpha_composite(layer, (0, 0 if position == "top" else height - band_h))
    tw = _text_width(title_font, text, tracking)
    x = (width - tw) // 2

    def draw_title(draw, fill, _x=x, _y=y):
        _draw_tracked(draw, (_x, _y), text, title_font, fill or color, tracking)

    _shadowed_text(canvas, draw_title, size)
    if sub:
        y2 = y + int(size * 1.2)
        sw = _text_width(sub_font, sub, sub_size * 0.04)
        sx = (width - sw) // 2

        def draw_sub(draw, fill, _x=sx, _y=y2):
            _draw_tracked(draw, (_x, _y), sub, sub_font, fill or ACCENT_COLOR, sub_size * 0.04)

        _shadowed_text(canvas, draw_sub, sub_size)
    return canvas


def draw_text_in_box(im, text: str, box: tuple[int, int, int, int], *, font: str | None = None,
                     color: tuple[int, int, int, int] = CAPTION_COLOR, max_size: int | None = None, shadow: bool = True):
    """把一行字居中放进 ``box``（横幅标题用）：字号按框宽定，最多两行（中文对半、英文按词对半）。原地画，返回 im。"""

    from PIL import ImageDraw

    text = " ".join(str(text or "").split())
    canvas = im.convert("RGBA")
    if not text:
        return canvas
    title_font_path, _ = _font_candidates(font, None)
    if not title_font_path:
        raise PostError("没找到可用的中文字体——在字幕样式里指定字体文件，或把字体装进系统")
    left, top, right, bottom = box
    bw, bh = right - left, bottom - top
    cjk = any("㐀" <= ch <= "鿿" for ch in text)
    lines = [text]
    if (cjk and len(text) > 6) or (not cjk and len(text) > 14):
        words = text.split(" ")
        if len(words) > 1:
            mid = max(1, len(words) // 2)
            lines = [" ".join(words[:mid]), " ".join(words[mid:])]
        else:
            mid = (len(text) + 1) // 2
            lines = [text[:mid], text[mid:]]
    size = max_size or int(bh * 0.42 / len(lines))
    while size > 12:
        f = _load_font(title_font_path, size)
        tracking = size * (0.06 if cjk else 0.01)
        if max(_text_width(f, line, tracking) for line in lines) <= bw * 0.94 and int(size * 1.2) * len(lines) <= bh:
            break
        size -= 2
    f = _load_font(title_font_path, size)
    tracking = size * (0.06 if cjk else 0.01)
    line_h = int(size * 1.2)
    y = top + (bh - line_h * len(lines)) // 2
    for line in lines:
        x = left + (bw - _text_width(f, line, tracking)) // 2

        def draw(draw_obj, fill, _x=x, _y=y, _line=line):
            _draw_tracked(draw_obj, (_x, _y), _line, f, fill or color, tracking)

        if shadow:
            _shadowed_text(canvas, draw, size)
        else:
            draw(ImageDraw.Draw(canvas), color)
        y += line_h
    return canvas


def caption_below(im, text: str, *, font: str | None = None, color: tuple[int, int, int, int] = CAPTION_COLOR):
    """表情包的中文短语：放在透明画布下方约 22% 的带里（不压主体），主体等比缩上去。"""

    from PIL import Image

    text = " ".join(str(text or "").split())
    im = im.convert("RGBA")
    if not text:
        return im
    title_font_path, _ = _font_candidates(font, None)
    if not title_font_path:
        raise PostError("没找到可用的中文字体——在字幕样式里指定字体文件，或把字体装进系统")
    side = im.width
    band = int(side * 0.22)
    subject = fit_square(im, side - band, pad=0.02)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(subject, ((side - subject.width) // 2, 0))
    size = int(band * 0.72)
    while size > 10:
        f = _load_font(title_font_path, size)
        if _text_width(f, text, size * 0.04) <= side * 0.92:
            break
        size -= 2
    f = _load_font(title_font_path, size)
    tw = _text_width(f, text, size * 0.04)
    x = (side - tw) // 2
    y = side - band + (band - size) // 2

    def draw(draw_obj, fill, _x=x, _y=y):
        _draw_tracked(draw_obj, (_x, _y), text, f, fill or color, size * 0.04)

    _shadowed_text(canvas, draw, size)
    return canvas


def card_canvas(aspect: str, short_side: int = CARD_SHORT_SIDE) -> tuple[int, int]:
    """图卡画布：短边固定 ``short_side``，长边按画幅算（3:4 → 1080×1440，16:9 → 1920×1080）。"""

    aw, ah = parse_aspect(aspect)
    if aw >= ah:
        return max(1, round(short_side * aw / ah)), short_side
    return short_side, max(1, round(short_side * ah / aw))


def render_card(base: str | Path, out: str | Path, *, title: str, subtitle: str = "", aspect: str = "3:4",
                layout: str = "auto", font: str | None = None) -> Path:
    """图卡 = 海报那套叠字（``storyvideo.poster.render_poster``）换一个画布尺寸：底图满幅裁切、渐变压暗、
    粗体中文字体、副标题限 24 字。海报的 ``PosterError`` 在这里翻成 ``PostError``，一张失败不拖累整批。"""

    try:
        return render_poster(base, out, title=title, subtitle=subtitle or None, font=font, layout=layout,
                             canvas_size=card_canvas(aspect))
    except PosterError as exc:
        raise PostError(str(exc)) from exc


def contact_sheet(paths: list[Path], *, columns: int = 3, cell: tuple[int, int] = (600, 800), gap: int = 16,
                  background: tuple[int, int, int] = (250, 247, 242)):
    """九宫格样片：每格 cover 裁到 ``cell``，最多 ``columns × columns`` 格（多余的不进样片）。"""

    from PIL import Image

    rows_paths = list(paths)[: columns * columns]
    if not rows_paths:
        raise PostError("没有可拼样片的图片")
    columns = max(1, min(columns, len(rows_paths)))
    rows = (len(rows_paths) + columns - 1) // columns
    cw, ch = cell
    sheet = Image.new("RGB", (columns * cw + gap * (columns + 1), rows * ch + gap * (rows + 1)), background)
    for index, path in enumerate(rows_paths):
        tile = cover(open_rgba(path), cw, ch).convert("RGB")
        col, row = index % columns, index // columns
        sheet.paste(tile, (gap + col * (cw + gap), gap + row * (ch + gap)))
    return sheet


# ---------------------------------------------------------------------------- 运行记录


def manifest_path(spec: Mapping[str, Any]) -> Path:
    return out_dir(spec) / "post.json"


def load_manifest(spec: Mapping[str, Any]) -> dict[str, Any]:
    data = read_json(manifest_path(spec), missing=None)
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return {"schema_version": 1, "runs": []}
    return data


def latest_run(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    """最新一次后处理（产物文件还在的那些）；没有 = None。"""

    runs = load_manifest(spec)["runs"]
    root = out_dir(spec)
    for run in reversed(runs):
        outputs = [o for o in run.get("outputs") or () if (root / str(o.get("file") or "")).is_file()]
        if outputs:
            return {**run, "outputs": outputs}
    return None


def output_record(path: Path, *, role: str, item: str | None, size: int | None = None) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as im:
        width, height = im.size
    return {"item": item, "role": role, "file": path.name, "width": width, "height": height, "bytes": path.stat().st_size}


class PostContext:
    """一次后处理运行：输出目录 + 运行戳（进文件名，保证不覆盖）+ 标题 / 叠字文本。"""

    def __init__(self, spec: Mapping[str, Any], stamp: int | None = None):
        self.spec = spec
        self.dir = out_dir(spec)
        self.stamp = int(stamp if stamp is not None else time.time() * 1000)
        self.dir.mkdir(parents=True, exist_ok=True)

    def name(self, item: str | None, role: str, suffix: str = ".png") -> Path:
        stem = f"{item}-{role}-{self.stamp}" if item else f"{role}-{self.stamp}"
        return self.dir / f"{stem}{suffix}"


def run_post(spec: Mapping[str, Any], template: Any, *, note: Callable[..., None] | None = None) -> dict[str, Any]:
    """按模板跑一遍：挑选（空 = 全部）里有 current 的每张 → ``template.post``；再 ``template.post_extra``（横幅 / 图标 / 样片）。
    一张失败不拖累其他张；结果追加进 ``post.json``。"""

    items = picked_items(spec)
    if not items:
        raise PostError("还没有可后处理的图——先出图，再挑选")
    ctx = PostContext(spec)
    outputs: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for number, item in enumerate(items, 1):
        if note:
            note(progress=f"{number}/{len(items)}", current=item["id"])
        path = current_path(spec, item)
        try:
            outputs.extend(template.post(spec, item, path, ctx))
        except (PostError, OSError, ValueError) as exc:
            failed.append({"item": item["id"], "error": str(exc)[:500]})
    extra = getattr(template, "post_extra", None)
    if extra and outputs:
        try:
            outputs.extend(extra(spec, items, ctx) or [])
        except (PostError, OSError, ValueError) as exc:
            failed.append({"item": "", "error": str(exc)[:500]})
    run = {"stamp": ctx.stamp, "created_at": time.time(), "template": template.id, "items": [i["id"] for i in items],
           "outputs": outputs, "failed": failed}
    manifest = load_manifest(spec)
    manifest["runs"].append(run)
    atomic_write_json(manifest_path(spec), manifest)
    return run


__all__ = [
    "PostError", "PostContext", "WECHAT_MAIN", "WECHAT_THUMB", "WECHAT_ICON", "WECHAT_BANNER", "WECHAT_MAX_BYTES", "AVATAR_SIZES",
    "STICKER_SIZES", "IMAGE_LONG_SIDE", "CARD_SHORT_SIDE", "open_rgba", "background_color", "to_transparent", "content_bbox", "trim",
    "fit_square", "cover", "parse_aspect",
    "crop_aspect", "circle", "head_crop", "next_free", "save_png", "save_jpeg", "font_available", "overlay_text", "draw_text_in_box", "caption_below",
    "card_canvas", "render_card", "contact_sheet", "manifest_path", "load_manifest", "latest_run", "output_record", "run_post",
]
