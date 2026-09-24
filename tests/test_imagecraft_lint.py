"""图像提示词的静态检查（DESIGN §41 / 契约 docs/image-style-cards.md §3）。

每条规则一命中一不命中，再确认告警能被 ``webui.problems`` 拆成问题卡（scope ``image``、kind ``image``、
「影响」和「怎么做」两半都不空）——检查函数只有变成问题卡才会出现在界面上。
"""
from __future__ import annotations

import pytest

from imagecraft import cards as C
from imagecraft import lint as L
from webui.problems import problem_card

NO_TEXT_ZH = "画面中绝对不出现任何文字、字母、数字或符号。"
NO_TEXT_EN = "Absolutely NO text, letters, numbers or symbols anywhere in the image. "


# ---------------------------------------------------------------------------- 1. 空话（gpt_image）


def test_anti_slop_hits_only_in_gpt_image_dialect():
    prompt = "Scene: a masterpiece studio, 4k, epic and stunning"
    hit = L.check_anti_slop(prompt, "gpt_image", label="I01")
    assert len(hit) == 1
    assert hit[0].startswith("image I01: GPT Image 方言里出现了空话「")
    assert "只夸不描述画面" in hit[0]
    # 别的方言不管这条
    assert L.check_anti_slop(prompt, "nano_banana") == []
    assert L.check_anti_slop(prompt, "generic") == []


def test_anti_slop_miss_on_a_concrete_prompt():
    assert L.check_anti_slop("Scene: a tiled bakery counter at dawn, flour on a walnut board, one pendant lamp", "gpt_image") == []


# ---------------------------------------------------------------------------- 2. 数字镜头参数（nano_banana）


def test_lens_numbers_hit_only_in_nano_banana_dialect():
    prompt = "柔光人像，85mm 镜头，f/2.8，ISO 800，浅景深"
    hit = L.check_lens_numbers(prompt, "nano_banana", label="I02")
    assert len(hit) == 1
    assert hit[0].startswith("image I02: Nano Banana 方言里出现了数字镜头参数「")
    assert "改成直接描述想要的样子" in hit[0]
    for token in ("85mm", "f/2.8", "ISO 800"):
        assert token in hit[0]
    assert L.check_lens_numbers(prompt, "gpt_image") == []          # 只查 nano_banana 方言
    assert L.check_lens_numbers(prompt, "generic") == []


def test_lens_numbers_miss_when_only_the_look_is_described():
    assert L.check_lens_numbers("离人一臂远的平视，背景虚成大片色块，透视轻微拉伸", "nano_banana") == []


# ---------------------------------------------------------------------------- 3. 否定式描述


def test_negative_framing_hit():
    hit = L.check_negative_framing("A solo portrait, no people in the background，不要出现路人", label="I03")
    assert len(hit) == 1
    assert "提示词里有否定式描述「" in hit[0]
    assert "no people" in hit[0] and "不要出现路人" in hit[0]
    assert "反着写：说清画面里有什么" in hit[0]


def test_negative_framing_exempts_the_no_text_policy():
    """NO_TEXT 尾句是既定的产品决定，不是写法问题——不告警（契约 §3）。"""

    assert L.check_negative_framing(f"影楼柔光棚拍，背景干净。{NO_TEXT_ZH}") == []
    assert L.check_negative_framing(f"Soft studio portrait. {NO_TEXT_EN}") == []
    assert L.check_negative_framing("画面里不要出现文字与水印") == []
    # 正面表述完全不命中
    assert L.check_negative_framing("干净的米灰色无缝背景，画面里只有这一个人") == []


# ---------------------------------------------------------------------------- 4. 画面文字没加引号


def test_quoted_text_hit_and_miss():
    hit = L.check_quoted_text("招牌上写着 营业中，暖黄的灯", label="I04")
    assert len(hit) == 1
    assert "要出现在画面里的文字「营业中」没加引号" in hit[0]
    assert "用引号原样写出来" in hit[0]
    # 加了引号就不提；中英两种引号都认
    assert L.check_quoted_text("招牌上写着「营业中」") == []
    assert L.check_quoted_text('the sign reads "OPEN"') == []
    assert L.check_quoted_text("影楼柔光棚拍，背景干净无杂物") == []


# ---------------------------------------------------------------------------- 5. 卡的色板


def test_palette_hint_hit_and_miss():
    short = {"id": "x", "name": "试验卡", "palette": ["米白", "暖灰", "淡杏"]}
    hit = L.check_card(short)
    assert len(hit) == 1
    assert "image x: 风格卡「试验卡」的色板只有 3 个色名" in hit[0]
    assert "色板写 5–7 个具体色名" in hit[0]
    assert L.check_card(None) == []
    # 库里每一张卡都过（色板 5–7 个具体色名）
    for card in C.style_cards():
        assert L.check_card(card) == [], card["id"]


# ---------------------------------------------------------------------------- 汇总与问题卡


def test_check_image_prompt_collects_every_rule_in_order():
    prompt = "Scene: stunning studio, 85mm, no people，招牌上写着 营业中"
    card = {"id": "x", "name": "试验卡", "palette": ["米白"]}
    warnings = L.check_image_prompt(prompt, dialect="gpt_image", card=card, label="I05")
    # 空话 + 否定式 + 没加引号 + 色板；数字镜头参数只在 NB 方言查
    assert len(warnings) == 4
    assert all(w.startswith("image I05: ") for w in warnings)
    assert not any("数字镜头参数" in w for w in warnings)
    nb = L.check_image_prompt(prompt, dialect="nano_banana", card=card, label="I05")
    assert len(nb) == 4 and any("数字镜头参数" in w for w in nb)
    assert not any("空话" in w for w in nb)


def test_a_clean_prompt_from_the_library_raises_nothing():
    card = C.find_card("studio_soft_portrait")
    prompt = card["anchor_zh"] + NO_TEXT_ZH
    assert L.check_image_prompt(prompt, dialect="generic", card=card, label="I01") == []


def test_check_items_walks_a_whole_set_and_hints_the_card_once():
    items = [{"id": "I01", "prompt": "stunning studio"}, {"id": "I02", "prompt": "epic studio"}]
    card = {"id": "x", "name": "试验卡", "palette": ["米白"]}
    warnings = L.check_items(items, dialect="gpt_image", card=card)
    assert [w.split(":")[0] for w in warnings] == ["image I01", "image I02", "image x"]


@pytest.mark.parametrize("warning", [
    'image I01: GPT Image 方言里出现了空话「stunning、epic」——这些词只夸不描述画面，删掉，换成具体的光线、材质或颜色',
    'image I02: Nano Banana 方言里出现了数字镜头参数「85mm、f/2.8」——这类数值换不来对应的画面，改成直接描述想要的样子（背景虚到什么程度、离主体多近、透视夸不夸张）',
    'image I03: 提示词里有否定式描述「no people」——反着写：说清画面里有什么，别列没有什么；不想要的东西写进负向词',
    'image I04: 要出现在画面里的文字「营业中」没加引号——要出现的文字用引号原样写出来，模型才知道逐字渲染哪几个字',
    'image studio_soft_portrait: 风格卡「影楼柔光」的色板只有 3 个色名——色板写 5–7 个具体色名（雾蓝、砖红、米白），模型才有的抓',
])
def test_every_warning_becomes_a_problem_card(warning):
    card = problem_card(warning)
    assert card["scope"] == "image"
    assert card["target"] == warning.split(" ")[1].rstrip(":")
    assert card["kind"] == "image"
    assert card["impact"] and card["fix"]
    assert "——" not in card["impact"] and "——" not in card["fix"]
