"""图像方言（DESIGN §41 / 契约 docs/image-style-cards.md §2）。

最重要的一条在最后：``generic`` 方言的四条产线（表情包 / 写真 / 绘本一页 / 海报）逐字节对上
``tests/fixtures/image_prompts_generic.json`` 的黄金快照——这份快照是 §41 落地**之前**的编译结果，
它守的和提示词金丝雀守的是同一件事：老项目的付费请求不许因为加了风格卡而改一个字。
（海报那条换成了下面的 ``POSTER_SPEC``：换之前核对过旧示例仍逐字节一致，再用同一份代码重记。
写真那条在真人相似度句改措辞时重记过一次，只差那一句。）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from imagecraft import dialects as D

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "image_prompts_generic.json").read_text(encoding="utf-8"))

BASE_PARTS = {
    "zh": True,
    "join": "imageset",
    "segments": ["影楼柔光棚拍：大柔光箱 45 度主光，85mm 人像镜头，stunning 的成片质感", "小满：圆脸短发的小女孩"],
    "tail": "画面中绝对不出现任何文字、字母、数字或符号。",
    "subject": "小满：圆脸短发的小女孩",
    "scene": "干净的米灰色无缝背景",
    "details": ["柔光箱主光 + 反光板补光", "色板：米白、暖灰、淡杏、浅木色、柔粉",
                "85mm 人像镜头的观感，f/2.8 的浅景深", "a stunning cinematic finish"],
    "use_case": "keepsake portrait album",
    "constraints": ["保留毛孔与发丝", "灯具与反光板不进画"],
    "aspect_ratio": "3:4",
    "references": ["定妆照，脸与发型照它", "服装参考"],
}


# ---------------------------------------------------------------------------- 方言判定


@pytest.mark.parametrize("model,expected", [
    ("gpt-image-2", "gpt_image"),
    ("openai/gpt-image-1-mini", "gpt_image"),
    ("nano-banana-pro", "nano_banana"),
    ("google/nano_banana_2", "nano_banana"),
    ("gemini-image-3", "nano_banana"),
    ("doubao-seedream-4-0", "generic"),
    ("@image", "generic"),
    ("", "generic"),
    (None, "generic"),
])
def test_dialect_by_model_id(model, expected):
    assert D.dialect_for_image_model(model) == expected
    assert D.resolve_image_dialect(model) == expected


def test_override_beats_the_model_guess():
    assert D.resolve_image_dialect("doubao-seedream", "gpt_image") == "gpt_image"
    assert D.resolve_image_dialect("gpt-image-2", "generic") == "generic"
    assert D.resolve_image_dialect("gpt-image-2", "nano-banana") == "nano_banana"     # 连字符也认
    # auto / 空 / 不认识的取值都退回按模型猜——老项目写了别的东西也不会崩
    for override in ("auto", "", None, "whatever"):
        assert D.resolve_image_dialect("nano-banana-pro", override) == "nano_banana"
        assert D.resolve_image_dialect("doubao-seedream", override) == "generic"


# ---------------------------------------------------------------------------- gpt_image


def test_gpt_image_emits_five_labels_and_drops_anti_slop():
    out = D.render_gpt_image(BASE_PARTS)
    lines = out.split("\n")
    assert [line.split(":")[0] for line in lines] == list(D.GPT_IMAGE_LABELS)
    assert D.GPT_IMAGE_LABELS == ("Scene", "Subject", "Key details", "Intended use", "Constraints")
    assert out.startswith("Scene: 干净的米灰色无缝背景")
    assert "小满：圆脸短发的小女孩" in out
    assert "色板：米白、暖灰、淡杏、浅木色、柔粉" in out
    assert "Intended use: keepsake portrait album" in out
    # 空话（只夸不描述画面的词）一个都不许留
    for word in ("masterpiece", "4k", "epic", "stunning"):
        assert word not in out.lower()
    assert "a cinematic finish" in out          # 删掉空话，句子仍然读得通
    assert "85mm 人像镜头的观感" in out          # 数字镜头参数只在 nano_banana 方言里删
    # 删词不许留下悬空的空格与标点
    assert "， " not in out and "  " not in out
    # 尾句（画面文字政策）进 Constraints，不再单独挂在末尾
    assert out.rstrip().endswith("画面中绝对不出现任何文字、字母、数字或符号。")
    assert "Constraints: 保留毛孔与发丝；灯具与反光板不进画；" in out


def test_gpt_image_quotes_the_text_that_must_appear_and_indexes_references():
    out = D.render_gpt_image({**BASE_PARTS, "text_in_image": "营业中"})
    assert "「营业中」" in out
    assert "参考图 1：定妆照，脸与发型照它" in out and "参考图 2：服装参考" in out


def test_gpt_image_falls_back_to_segments_when_slots_are_missing():
    out = D.render_gpt_image({"zh": True, "segments": ["风格锚一句", "主体一句", "画面一句"], "tail": "无文字。"})
    assert "Subject: 风格锚一句" in out         # 没给槽位时首段当主体，其余当细节
    assert "Key details: 主体一句；画面一句" in out
    assert out.count("\n") == 4                # 五段一个不少


# ---------------------------------------------------------------------------- nano_banana


def test_nano_banana_has_ratio_line_and_no_lens_numbers():
    out = D.render_nano_banana(BASE_PARTS)
    assert out.endswith("\n\nAspect ratio: 3:4")
    assert out.split("\n")[-1] == D.RATIO_LINE.format(ratio="3:4")
    for token in ("85mm", "50mm", "f/2.8", "ISO 800"):
        assert token not in out
    assert "人像镜头的观感" in out              # 只删数字，观感描述留着
    assert "stunning" in out                    # 删空话只是 gpt_image 方言的事，NB 不删
    assert "参考图 1：定妆照，脸与发型照它" in out


def test_nano_banana_defaults_the_ratio_line():
    out = D.render_nano_banana({"zh": False, "segments": ["A soft studio portrait shot at 85mm, f/1.8, ISO 400"],
                                "references": ["face to follow"]})
    assert out.endswith("\n\nAspect ratio: 1:1")
    assert "Reference image 1: face to follow." in out
    assert "85mm" not in out and "f/1.8" not in out and "ISO 400" not in out


def test_strip_lens_numbers_is_reusable():
    assert D.strip_lens_numbers("85mm 人像镜头") == "人像镜头"
    assert D.strip_lens_numbers("shot at 50 mm, f/2.8, ISO 800") == "shot at"
    assert D.strip_lens_numbers("shallow depth of field") == "shallow depth of field"


def test_strip_anti_slop_removes_words_formed_by_an_earlier_removal():
    """删掉一个词后前后两截拼出另一个空话，也要删掉；结果跟词表的书写顺序无关。"""

    assert D.strip_anti_slop("画质极致细节极佳") == ""
    assert D.strip_anti_slop("hyperincredible-detailed x") == "x"
    assert D.strip_anti_slop("极致极致细节细节 portrait") == "portrait"
    assert D.strip_anti_slop("soft light, 8K, Stunning studio") == "soft light, studio"
    for glued in ("cinematic极致高质量画质细节ultra-detailed", "award-winningaward-winning画质hyper detailed画质极佳极佳"):
        out = D.strip_anti_slop(glued).lower()
        assert not any(word.lower() in out for word in D.ANTI_SLOP_WORDS), (glued, out)


# ---------------------------------------------------------------------------- generic：逐字节


def test_generic_joiners_match_each_production_line():
    assert D.render_generic({"zh": True, "join": "imageset", "segments": ["甲", "乙"], "tail": "尾。"}) == "甲。乙。尾。"
    assert D.render_generic({"zh": True, "join": "picturebook", "segments": ["Style anchor", "中文"], "tail": "尾。"}) == "Style anchor.中文。尾。"
    assert D.render_generic({"zh": True, "join": "poster", "segments": ["甲。", "乙。"], "tail": "尾。"}) == "甲。乙。尾。"
    assert D.render_generic({"zh": False, "join": "imageset", "segments": ["one", "two"], "tail": "Tail. "}) == "one. two. Tail. "


def test_generic_sticker_prompt_is_byte_identical():
    from imageset import spec as S, templates as T

    spec = S.normalize({
        "id": "ST-20260909-1", "module": "sticker", "title": "小满表情包", "template": "emoji_pack",
        "subject": {"kind": "character", "name": "小满", "block": "圆脸短发的小女孩，穿黄色连衣裙"},
        "style": "flat_cute", "prompt_language": "zh",
        "options": {"expressions": ["开心"], "with_caption": False},
    }, module="sticker", project_root=".")
    assert T.expand_items(spec)[0]["prompt"] == GOLDEN["sticker"]


def test_generic_portrait_prompt_is_byte_identical():
    from imageset import spec as S, templates as T

    spec = S.normalize({
        "id": "PT-20260909-1", "module": "portrait", "title": "百天照", "template": "baby_100",
        "subject": {"kind": "photo", "name": "宝宝", "block": "", "fidelity": 85},
        "style": "studio_soft", "prompt_language": "zh",
        "options": {"scenes": ["百天蛋糕（无火）"], "count_per_scene": 1, "text": "", "aspect": "3:4"},
    }, module="portrait", project_root=".")
    assert T.expand_items(spec)[0]["prompt"] == GOLDEN["portrait"]


def test_generic_picturebook_page_prompt_is_byte_identical():
    from picturebook import prompts as PBP, spec as PBS

    book = PBS.normalize_book({
        "id": "PB-20260909-1", "title_zh": "小狐狸的三只袜子", "title_en": "Three Socks",
        "age_band": "2-4", "bilingual_mode": "sentence_alternate",
        "characters": {"fox": {"name": "小狐狸", "name_en": "Little Fox", "block": "尖耳朵的橙色小狐狸，戴蓝围巾"}},
        "scenes": {"forest": {"name": "森林", "block": "松树林里的小路"}},
        "pages": [{"id": "P01", "zh": "小狐狸走进森林。", "en": "Little Fox walks into the forest.",
                   "scene": "小狐狸沿着小路往前走", "characters": ["fox"], "scene_ref": "forest"}],
    }, project_root=".")
    assert book["style_card"] == ""
    assert PBP.compile_page_prompt(book, "P01") == GOLDEN["picturebook"]


# 海报快照用的小项目：只放 poster_prompt 读得到的字段（风格骨架 / 场景锚 / 角色块 / 钩子镜）
POSTER_SPEC = {
    "id": "POSTER-G", "prompt_language": "zh", "neg": "可读文字, 字幕, 水印",
    "frame_style": "写实电影感竖屏画面。",
    "scene_anchor": "竖版取景，人物放在画面中部。",
    "cast": {
        "w": {"name": "阿宁", "block": "阿宁：短卷发，灰色卫衣。"},
        "room": {"block": "书房·夜：书桌、台灯。"},
    },
    "shots": [
        {"id": "S1", "dur": 6, "cast": ["room", "w"], "shot": "中景", "art": "她坐在书桌前，台灯照亮半边脸。",
         "motion": "她抬头。", "lines": []},
        {"id": "S2", "dur": 5, "cast": ["room", "w"], "shot": "近景", "art": "她望向窗外。", "motion": "她皱眉。", "lines": []},
    ],
}


def test_generic_poster_prompt_is_byte_identical():
    from storyvideo import poster as P

    spec = json.loads(json.dumps(POSTER_SPEC))
    prompt = P.poster_prompt(spec)
    assert prompt["zh"] == GOLDEN["poster_zh"]
    assert prompt["en"] == GOLDEN["poster_en"]
    assert prompt["style_card"] == ""
