"""本机后处理（DESIGN §40，契约 §2 ``post.py``）：白底转透明、微信表情规格、头像尺寸、写真 3:4 / 4:3、叠字。

全部走真 Pillow（不花钱、不联网）。叠字要中文字体，本机没有字体时按 ``post.font_available()`` 跳过那几条，
尺寸与透明度的断言不受影响。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from imageset import post as P
from imageset import spec as S
from imageset import templates as T

needs_font = pytest.mark.skipif(not P.font_available(), reason="needs a CJK font on this machine")


# ---------------------------------------------------------------------------- 素材


def subject_png(path: Path, *, size=(600, 600), background=(255, 255, 255), color=(210, 60, 80)) -> Path:
    """白底 + 居中实心圆（「主体」）+ 头顶一小块深色（头部，图标裁切用），四角一定是底色。"""

    from PIL import Image, ImageDraw

    im = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(im)
    w, h = size
    draw.ellipse((w * 0.25, h * 0.25, w * 0.75, h * 0.85), fill=color)
    draw.ellipse((w * 0.38, h * 0.18, w * 0.62, h * 0.42), fill=(60, 40, 40))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, format="PNG")
    return path


def photo_png(path: Path, *, size=(1200, 800)) -> Path:
    """横构图的「照片」：渐变 + 一块主体，用来验证 3:4 / 4:3 裁切。"""

    from PIL import Image, ImageDraw

    im = Image.new("RGB", size, (240, 232, 220))
    draw = ImageDraw.Draw(im)
    for x in range(size[0]):
        draw.line((x, 0, x, size[1]), fill=(200 - x * 100 // size[0], 180, 160 + x * 60 // size[0]))
    draw.rectangle((size[0] * 0.35, size[1] * 0.2, size[0] * 0.65, size[1] * 0.9), fill=(90, 70, 60))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, format="PNG")
    return path


def build_spec(module: str, template_id: str, tmp_path: Path, *, items: int = 2, options=None) -> dict:
    """展开好 items、每张都有 current 文件的 spec（后处理的入口条件）。"""

    subject = {"kind": "character", "name": "小满", "block": "齐肩黑发"} if module == "sticker" else \
        {"kind": "photo", "name": "宝宝", "block": "", "fidelity": 88}
    spec = S.normalize({"title": "后处理测试", "template": template_id, "subject": subject, "options": options or {}},
                       module=module, project_root=tmp_path, spec_id=f"{S.ID_PREFIX[module]}-P-1")
    spec["items"] = T.expand_items(spec)[:items]
    raw = S.raw_dir(spec)
    for item in spec["items"]:
        name = f"{item['id']}-1.png"
        if module == "sticker":
            subject_png(raw / name)
        else:
            photo_png(raw / name)
        item.update({"current": name, "status": "done"})
    S.save(spec)
    return spec


def corners(im) -> list[tuple[int, ...]]:
    w, h = im.size
    return [im.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]


def outputs_by_role(run: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in run["outputs"]:
        out.setdefault(row["role"], []).append(row)
    return out


# ---------------------------------------------------------------------------- 白底转透明


def test_to_transparent_keeps_subject_opaque_and_clears_corners(tmp_path):
    im = P.open_rgba(subject_png(tmp_path / "s.png"))
    assert P.background_color(im) == (255, 255, 255)
    out = P.to_transparent(im)
    assert out.mode == "RGBA" and out.size == im.size
    assert [p[3] for p in corners(out)] == [0, 0, 0, 0]
    center = out.getpixel((out.width // 2, int(out.height * 0.6)))
    assert center[3] == 255 and center[:3] == (210, 60, 80)
    # 主体内部与主体外的像素分得开：透明像素占比在合理区间（背景多、主体不被吃掉）
    alpha = out.split()[3].tobytes()
    clear = sum(1 for a in alpha if a == 0)
    assert 0.35 < clear / len(alpha) < 0.85


def test_to_transparent_handles_non_white_flat_background(tmp_path):
    im = P.open_rgba(subject_png(tmp_path / "s.png", background=(12, 200, 120)))
    out = P.to_transparent(im)
    assert [p[3] for p in corners(out)] == [0, 0, 0, 0]
    assert out.getpixel((out.width // 2, int(out.height * 0.6)))[3] == 255


def test_content_bbox_trim_and_fit_square(tmp_path):
    im = P.to_transparent(P.open_rgba(subject_png(tmp_path / "s.png")))
    left, top, right, bottom = P.content_bbox(im)
    assert 0 < left < right < im.width and 0 < top < bottom < im.height
    trimmed = P.trim(im)
    assert trimmed.width == right - left and trimmed.height == bottom - top
    square = P.fit_square(im, 240)
    assert square.size == (240, 240) and [p[3] for p in corners(square)] == [0, 0, 0, 0]
    padded = P.fit_square(im, 240, background=(255, 255, 255, 255))
    assert corners(padded)[0] == (255, 255, 255, 255)


def test_crop_aspect_cover_and_circle(tmp_path):
    im = P.open_rgba(photo_png(tmp_path / "p.png"))
    three_four = P.crop_aspect(im, "3:4")
    assert round(three_four.width / three_four.height, 3) == 0.75 and three_four.height == im.height
    four_three = P.crop_aspect(im, "4:3")
    assert four_three.width / four_three.height == pytest.approx(4 / 3, rel=0.005)
    small = P.crop_aspect(im, "3:4", max_side=400)
    assert max(small.size) == 400
    assert P.cover(im, 200, 200).size == (200, 200)
    round_im = P.circle(P.cover(im, 200, 200))
    assert round_im.size == (200, 200) and [p[3] for p in corners(round_im)] == [0, 0, 0, 0]
    assert round_im.getpixel((100, 100))[3] == 255
    assert P.parse_aspect("3:4") == (3, 4) and P.parse_aspect("4x3") == (4, 3)
    for bad in ("3-4", "0:4", "x"):
        with pytest.raises(P.PostError, match="画幅"):
            P.parse_aspect(bad)


def test_save_png_never_overwrites_and_respects_max_bytes(tmp_path):
    from PIL import Image

    im = Image.new("RGBA", (300, 300), (10, 20, 30, 255))
    first = P.save_png(im, tmp_path / "a.png")
    second = P.save_png(im, tmp_path / "a.png")
    assert first.name == "a.png" and second.name == "a-2.png" and first.read_bytes() == second.read_bytes()
    assert not list(tmp_path.glob(".*.tmp.png"))
    with pytest.raises(P.PostError, match="KB"):
        P.save_png(P.open_rgba(photo_png(tmp_path / "big.png", size=(1600, 1600))), tmp_path / "small.png", max_bytes=64)
    with pytest.raises(P.PostError, match="图片不存在"):
        P.open_rgba(tmp_path / "nope.png")


# ---------------------------------------------------------------------------- 表情包：微信规格


def test_emoji_post_produces_wechat_sizes(tmp_path):
    spec = build_spec("sticker", "emoji_pack", tmp_path, items=2, options={"expressions": ["开心", "哭"], "with_caption": False})
    run = P.run_post(spec, T.template_for_spec(spec))
    assert run["failed"] == [] and run["items"] == ["I01", "I02"]
    roles = outputs_by_role(run)
    assert sorted(roles) == ["banner", "icon", "main", "thumb"]
    for row in roles["main"]:
        assert (row["width"], row["height"]) == (P.WECHAT_MAIN, P.WECHAT_MAIN) == (240, 240)
        assert row["bytes"] <= P.WECHAT_MAX_BYTES == 500 * 1024
    for row in roles["thumb"]:
        assert (row["width"], row["height"]) == (P.WECHAT_THUMB, P.WECHAT_THUMB) == (120, 120)
    assert len(roles["main"]) == len(roles["thumb"]) == 2
    assert (roles["banner"][0]["width"], roles["banner"][0]["height"]) == P.WECHAT_BANNER == (750, 400)
    assert (roles["icon"][0]["width"], roles["icon"][0]["height"]) == (P.WECHAT_ICON, P.WECHAT_ICON) == (50, 50)
    assert roles["banner"][0]["item"] is None and roles["icon"][0]["item"] is None
    # 主图与缩略都是透明底（四角 alpha 0）
    for row in roles["main"] + roles["thumb"]:
        im = P.open_rgba(S.out_dir(spec) / row["file"])
        assert [p[3] for p in corners(im)] == [0, 0, 0, 0]
    # 运行记录：post.json 追加一条，latest_run 拿到它
    assert P.manifest_path(spec).is_file()
    assert P.latest_run(spec)["stamp"] == run["stamp"] and len(P.load_manifest(spec)["runs"]) == 1
    # 再跑一次：文件名带新运行戳，老产物不动
    before = {row["file"] for row in run["outputs"]}
    again = P.run_post(spec, T.template_for_spec(spec))
    assert not ({row["file"] for row in again["outputs"]} & before)
    assert all((S.out_dir(spec) / name).is_file() for name in before)
    assert len(P.load_manifest(spec)["runs"]) == 2 and P.latest_run(spec)["stamp"] == again["stamp"]


@needs_font
def test_emoji_caption_is_drawn_below_the_subject(tmp_path):
    plain = build_spec("sticker", "emoji_pack", tmp_path / "a", items=1,
                       options={"expressions": ["开心"], "with_caption": False})
    captioned = build_spec("sticker", "emoji_pack", tmp_path / "b", items=1,
                           options={"expressions": ["开心"], "with_caption": True})
    a = P.run_post(plain, T.template_for_spec(plain))
    b = P.run_post(captioned, T.template_for_spec(captioned))
    main_a = P.open_rgba(S.out_dir(plain) / next(r["file"] for r in a["outputs"] if r["role"] == "main"))
    main_b = P.open_rgba(S.out_dir(captioned) / next(r["file"] for r in b["outputs"] if r["role"] == "main"))
    assert main_a.size == main_b.size == (240, 240)
    assert main_a.tobytes() != main_b.tobytes()


@needs_font
def test_caption_below_puts_text_under_the_subject(tmp_path):
    """``caption_below`` 把主体缩到上方 78%，下方 22% 的带里只有文字——不压主体。"""

    side = 480
    band_h = int(side * 0.22)
    base = P.fit_square(P.to_transparent(P.open_rgba(subject_png(tmp_path / "s.png"))), side, pad=0.02)
    captioned = P.caption_below(base, "开心")
    assert captioned.size == base.size == (side, side)
    band = (0, side - band_h, side, side)

    def opaque(im) -> int:
        return sum(1 for a in im.crop(band).split()[3].tobytes() if a > 8)

    assert opaque(captioned) > 0 and captioned.tobytes() != base.tobytes()      # 带里有字
    # 主体整个缩进了上方 78%，没有被字压到
    top = captioned.crop((0, 0, side, side - band_h))
    assert P.content_bbox(top)[3] > (side - band_h) * 0.9
    assert P.caption_below(base, "   ").tobytes() == base.tobytes()


def test_sticker_set_post_outputs_512_and_1024(tmp_path):
    spec = build_spec("sticker", "sticker_set", tmp_path, items=2, options={"themes": ["咖啡", "加班"]})
    run = P.run_post(spec, T.template_for_spec(spec))
    roles = outputs_by_role(run)
    assert sorted(roles) == ["s1024", "s512"] and len(roles["s512"]) == len(roles["s1024"]) == 2
    assert {(r["width"], r["height"]) for r in roles["s512"]} == {(512, 512)}
    assert {(r["width"], r["height"]) for r in roles["s1024"]} == {(1024, 1024)}
    assert P.STICKER_SIZES == (512, 1024)


# ---------------------------------------------------------------------------- 头像尺寸


def test_avatar_post_outputs_three_sizes_and_circle(tmp_path):
    spec = build_spec("sticker", "avatar_set", tmp_path, items=1, options={"styles": ["写实"], "circle": True})
    run = P.run_post(spec, T.template_for_spec(spec))
    roles = outputs_by_role(run)
    assert sorted(roles) == ["a1080", "a200", "a512", "circle"]
    assert P.AVATAR_SIZES == (1080, 512, 200)
    for size in P.AVATAR_SIZES:
        row = roles[f"a{size}"][0]
        assert (row["width"], row["height"]) == (size, size)
        assert [p[3] for p in corners(P.open_rgba(S.out_dir(spec) / row["file"]))] == [255, 255, 255, 255]
    circle = P.open_rgba(S.out_dir(spec) / roles["circle"][0]["file"])
    assert circle.size == (512, 512) and [p[3] for p in corners(circle)] == [0, 0, 0, 0]
    assert circle.getpixel((256, 256))[3] == 255
    # 关掉圆形版本
    off = build_spec("sticker", "avatar_set", tmp_path / "off", items=1, options={"styles": ["写实"], "circle": False})
    assert "circle" not in outputs_by_role(P.run_post(off, T.template_for_spec(off)))


# ---------------------------------------------------------------------------- 写真：3:4 + 4:3 + 叠字


def test_portrait_post_makes_3x4_and_4x3(tmp_path):
    spec = build_spec("portrait", "baby_100", tmp_path, items=2, options={"scenes": ["小马甲", "全家福"], "text": ""})
    run = P.run_post(spec, T.template_for_spec(spec))
    roles = outputs_by_role(run)
    assert sorted(roles) == ["album", "album_4x3", "contact_sheet"]
    for row in roles["album"]:
        assert round(row["width"] / row["height"], 3) == 0.75
    for row in roles["album_4x3"]:
        assert row["width"] / row["height"] == pytest.approx(4 / 3, rel=0.005)
    sheet = roles["contact_sheet"][0]
    assert sheet["file"].endswith(".jpg") and sheet["item"] is None and sheet["width"] > 0
    # 主版画幅跟着 options.aspect 走
    square = build_spec("portrait", "baby_100", tmp_path / "sq", items=1, options={"scenes": ["小马甲"], "aspect": "1:1", "text": ""})
    album = next(r for r in P.run_post(square, T.template_for_spec(square))["outputs"] if r["role"] == "album")
    assert album["width"] == album["height"]


@needs_font
def test_portrait_overlay_text_changes_pixels(tmp_path):
    plain = build_spec("portrait", "first_birthday", tmp_path / "a", items=1, options={"scenes": ["学步"], "text": ""})
    texted = build_spec("portrait", "first_birthday", tmp_path / "b", items=1,
                        options={"scenes": ["学步"], "text": "周岁快乐|2026.09.09"})
    a = next(r for r in P.run_post(plain, T.template_for_spec(plain))["outputs"] if r["role"] == "album")
    b = next(r for r in P.run_post(texted, T.template_for_spec(texted))["outputs"] if r["role"] == "album")
    assert (a["width"], a["height"]) == (b["width"], b["height"])
    im_a = P.open_rgba(S.out_dir(plain) / a["file"])
    im_b = P.open_rgba(S.out_dir(texted) / b["file"])
    assert im_a.tobytes() != im_b.tobytes()
    # 叠字在下方：底部 1/4 差异明显，顶部几乎没动
    def diff(box):
        pa, pb = im_a.crop(box).tobytes(), im_b.crop(box).tobytes()
        return sum(1 for x, y in zip(pa, pb) if x != y) / len(pa)

    w, h = im_a.size
    assert diff((0, int(h * 0.75), w, h)) > 0.2
    assert diff((0, 0, w, int(h * 0.25))) < 0.02


@needs_font
def test_overlay_text_helpers(tmp_path):
    im = P.open_rgba(photo_png(tmp_path / "p.png", size=(900, 1200)))
    assert P.overlay_text(im, "   ").tobytes() == im.convert("RGBA").tobytes()    # 空文字不动
    top = P.overlay_text(im, "百天快乐", subtitle="2026.09.09", position="top")
    bottom = P.overlay_text(im, "百天快乐", subtitle="2026.09.09", position="bottom")
    assert top.size == bottom.size == im.size and top.tobytes() != bottom.tobytes()
    with pytest.raises(P.PostError, match="top / bottom"):
        P.overlay_text(im, "x", position="middle")
    boxed = P.draw_text_in_box(im.copy(), "一段比较长的横幅标题文字", (40, 40, 860, 300))
    assert boxed.tobytes() != im.convert("RGBA").tobytes()
    assert P.draw_text_in_box(im.copy(), "", (0, 0, 10, 10)).tobytes() == im.convert("RGBA").tobytes()
    captioned = P.caption_below(P.fit_square(P.to_transparent(P.open_rgba(subject_png(tmp_path / "s.png"))), 480), "开心")
    assert captioned.size == (480, 480)


# ---------------------------------------------------------------------------- 运行编排


def test_run_post_needs_pictures_and_survives_one_bad_file(tmp_path):
    spec = build_spec("sticker", "emoji_pack", tmp_path, items=2, options={"expressions": ["开心", "哭"], "with_caption": False})
    empty = S.normalize(S.to_disk({**spec, "items": [], "picks": []}), module="sticker", project_root=tmp_path, spec_id=spec["id"])
    with pytest.raises(P.PostError, match="先出图"):
        P.run_post(empty, T.template_for_spec(empty))
    # 第二张的文件坏了：第一张照样出产物，失败记进 failed
    (S.raw_dir(spec) / "I02-1.png").write_bytes(b"not a png")
    run = P.run_post(spec, T.template_for_spec(spec))
    assert [f["item"] for f in run["failed"]] == ["I02"] and run["failed"][0]["error"]
    assert {r["item"] for r in run["outputs"] if r["item"]} == {"I01"}


def test_run_post_only_covers_picked_items(tmp_path):
    spec = build_spec("sticker", "sticker_set", tmp_path, items=3, options={"themes": ["咖啡", "加班", "周末"]})
    spec["picks"] = ["I02"]
    run = P.run_post(spec, T.template_for_spec(spec))
    assert run["items"] == ["I02"] and {r["item"] for r in run["outputs"]} == {"I02"}


def test_post_context_names_never_collide(tmp_path):
    spec = S.normalize({"title": "x", "template": "emoji_pack", "subject": {"name": "小满"}},
                       module="sticker", project_root=tmp_path, spec_id="ST-P-2")
    ctx = P.PostContext(spec, stamp=1700)
    assert ctx.name("I01", "main").name == "I01-main-1700.png"
    assert ctx.name(None, "banner").name == "banner-1700.png"
    assert ctx.name(None, "contact_sheet", ".jpg").name == "contact_sheet-1700.jpg"
    assert P.latest_run(spec) is None and P.load_manifest(spec) == {"schema_version": 1, "runs": []}


def test_contact_sheet_limits_to_nine_cells(tmp_path):
    paths = [photo_png(tmp_path / f"p{i}.png", size=(400, 300)) for i in range(11)]
    sheet = P.contact_sheet(paths, columns=3, cell=(120, 160), gap=8)
    assert sheet.size == (3 * 120 + 8 * 4, 3 * 160 + 8 * 4)
    with pytest.raises(P.PostError, match="没有可拼样片"):
        P.contact_sheet([])
