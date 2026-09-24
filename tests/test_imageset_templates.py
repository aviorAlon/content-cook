"""模板包（DESIGN §40，契约 §3 / §4）：七个模板的展开条数与画幅、提示词里的身份块 / 相似度句 / 固定约束 /
NO_TEXT 尾句、选项改动改变 items、options_schema 前端可直接渲染。

提示词只在这里断言「必须出现的句子」——具体措辞（风格锚、画面描写）是内容，改文案不该在这里失败；
但身份锁、婴儿安全句、白底无影句、排他锁是**判据**，少一条就是提示词写错了。
"""

from __future__ import annotations

import pytest

from imageset import prompts as PR
from imageset import spec as S
from imageset import templates as T
from storyvideo.prompts import NO_TEXT_TAIL_CN, NO_TEXT_TAIL_EN

# 模板 → (模块, 默认张数, 默认画幅, 选项里的「清单」键)；图片工作台的三个模板没有「清单」选项，单独在
# tests/test_image_studio.py 里核对
TEMPLATES = {
    "emoji_pack": ("sticker", 16, "1:1", "expressions"),
    "sticker_set": ("sticker", 8, "1:1", "themes"),
    "avatar_set": ("sticker", 9, "1:1", "styles"),
    "newborn_30": ("portrait", 6, "3:4", "scenes"),
    "baby_100": ("portrait", 6, "3:4", "scenes"),
    "first_birthday": ("portrait", 6, "3:4", "scenes"),
    "wedding": ("portrait", 8, "3:4", "scenes"),
}
BABY_TEMPLATES = ("newborn_30", "baby_100", "first_birthday")
STICKER_TEMPLATES = ("emoji_pack", "sticker_set")


def build(template_id: str, tmp_path, *, options=None, **extra) -> dict:
    module = TEMPLATES[template_id][0]
    subject = {"kind": "character", "name": "小满", "block": "齐肩黑发低马尾，圆框眼镜"} if module == "sticker" else \
        {"kind": "photo", "name": "宝宝", "block": "", "fidelity": 88}
    data = {"title": "测试集", "template": template_id, "subject": subject, "options": options or {}}
    data.update(extra)
    return S.normalize(data, module=module, project_root=tmp_path, spec_id=f"{S.ID_PREFIX[module]}-T-1")


# ---------------------------------------------------------------------------- 注册表


def test_registry_covers_every_template():
    """七个成组模板 + 图片工作台的三个（DESIGN §42，那三个的细节在 tests/test_image_studio.py）。"""

    assert sorted(T.template_ids("sticker")) == ["avatar_set", "emoji_pack", "sticker_set"]
    assert sorted(T.template_ids("portrait")) == ["baby_100", "first_birthday", "newborn_30", "wedding"]
    assert sorted(T.template_ids("image")) == ["freeform", "model_compare", "text_card"]
    assert len(T.TEMPLATES) == 10
    assert set(t.id for t in T.TEMPLATES.values()) == set(TEMPLATES) | {"freeform", "model_compare", "text_card"}
    assert T.get_template("sticker", "baby_100") is None and T.get_template("nope", "emoji_pack") is None
    with pytest.raises(KeyError):
        T.template_for_spec({"module": "sticker", "template": "nope"})


@pytest.mark.parametrize("template_id", sorted(TEMPLATES))
def test_options_schema_is_renderable(template_id):
    """前端按 schema 渲染小表单：每条都要有 key / label / type / default；choices 里每项有 id。"""

    module = TEMPLATES[template_id][0]
    template = T.get_template(module, template_id)
    assert template.module == module and template.name and template.english
    assert template.description and template.description_en
    keys = set()
    for field in template.options_schema:
        assert set(("key", "label", "label_en", "type", "default")) <= set(field), field
        assert field["type"] in ("list", "bool", "string", "int")
        assert field["key"] and field["key"] not in keys
        keys.add(field["key"])
        if field["type"] == "list":
            assert isinstance(field["default"], list) and field["default"]
        if field["type"] == "int":
            assert isinstance(field["default"], int) and field["min"] <= field["default"] <= field["max"]
        for choice in field.get("choices") or ():
            assert choice["id"] and choice["name"]
    # 风格预设：中英两版锚都在，payload 给前端的形状对
    payload = template.payload()
    assert payload["id"] == template_id and payload["module"] == module and payload["options_schema"] == template.options_schema
    assert payload["styles"] and all(s["id"] and s["name"] and s["english"] and s["anchor"] and s["anchor_en"] for s in payload["styles"])
    assert payload["export_layout"] == template.export_layout and all(payload["export_layout"].values())


# ---------------------------------------------------------------------------- 展开：条数与画幅


@pytest.mark.parametrize("template_id", sorted(TEMPLATES))
def test_expand_default_count_and_aspect(template_id, tmp_path):
    module, count, aspect, list_key = TEMPLATES[template_id]
    spec = build(template_id, tmp_path)
    assert len(spec["options"][list_key]) == count
    items = T.expand_items(spec)
    assert len(items) == count
    assert [i["id"] for i in items] == [f"I{n:02d}" for n in range(1, count + 1)]
    assert {i["aspect_ratio"] for i in items} == {aspect}
    assert [i["label"] for i in items] == list(spec["options"][list_key])
    assert all(i["prompt"] and i["negative"] and i["status"] == "todo" and i["current"] is None for i in items)


@pytest.mark.parametrize("template_id", sorted(TEMPLATES))
def test_prompts_carry_identity_and_no_text_tail(template_id, tmp_path):
    module = TEMPLATES[template_id][0]
    spec = build(template_id, tmp_path)
    items = T.expand_items(spec)
    for item in items:
        assert item["prompt"].endswith(NO_TEXT_TAIL_CN)
        if module == "sticker":
            assert "小满：齐肩黑发低马尾，圆框眼镜" in item["prompt"]
            assert PR.CHARACTER_LOCK_ZH in item["prompt"]
            assert PR.REFERENCE_CHARACTER_ZH not in item["prompt"]      # 没有定妆图时不提参考图
        else:
            assert PR.FIDELITY_ZH.format(n=88) in item["prompt"]
            assert PR.PORTRAIT_SAFETY_ZH in item["prompt"]
        assert "文字" in item["negative"] and "水印" in item["negative"]


@pytest.mark.parametrize("template_id", sorted(TEMPLATES))
def test_english_prompts_use_english_fixed_sentences(template_id, tmp_path):
    spec = build(template_id, tmp_path, prompt_language="en")
    item = T.expand_items(spec)[0]
    assert item["prompt"].rstrip().endswith(NO_TEXT_TAIL_EN.strip())
    assert "text,letters" in item["negative"]
    if TEMPLATES[template_id][0] == "sticker":
        assert PR.CHARACTER_LOCK_EN.strip() in item["prompt"]
    else:
        assert PR.FIDELITY_EN.format(n=88).strip() in item["prompt"]


# ---------------------------------------------------------------------------- 模板各自的固定约束


@pytest.mark.parametrize("template_id", STICKER_TEMPLATES)
def test_sticker_templates_lock_white_background_and_no_shadow(template_id, tmp_path):
    for item in T.expand_items(build(template_id, tmp_path)):
        assert PR.STICKER_CONSTRAINTS_ZH in item["prompt"]
        assert "纯白背景 #FFFFFF" in item["prompt"] and "主体外没有投影" in item["prompt"]
        assert "投影" in item["negative"] and "渐变底" in item["negative"]


def test_avatar_template_locks_avatar_framing(tmp_path):
    items = T.expand_items(build("avatar_set", tmp_path))
    assert all(PR.AVATAR_CONSTRAINTS_ZH in i["prompt"] for i in items)
    assert "头肩像" in items[0]["prompt"]                      # crop 默认 headshot
    assert items[0]["meta"] == {"art": "写实", "crop": "headshot"}
    half = T.expand_items(build("avatar_set", tmp_path, options={"crop": "half"}))
    assert "半身像，双手可见" in half[0]["prompt"] and half[0]["meta"]["crop"] == "half"


@pytest.mark.parametrize("template_id", BABY_TEMPLATES)
def test_baby_templates_carry_safety_lines(template_id, tmp_path):
    items = T.expand_items(build(template_id, tmp_path))
    for item in items:
        assert PR.BABY_SAFETY_ZH in item["prompt"]
        assert "口鼻处饰品" in item["negative"] and "遮住口鼻" in item["negative"]
    joined = " ".join(i["prompt"] for i in items)
    if template_id == "newborn_30":
        assert "婴儿安睡或安静躺着" in joined and "道具离脸一掌以上" in joined
    else:
        assert "没有火焰" in joined


def test_wedding_names_both_people_and_locks_exclusivity(tmp_path):
    spec = build("wedding", tmp_path, options={"scenes": ["教堂"]},
                 subject={"kind": "photo", "fidelity": 92,
                          "assets": [{"file": "content/assets/PT-T-1/bride.png", "who": "bride"},
                                     {"file": "content/assets/PT-T-1/groom.png", "who": "groom"}]})
    prompt = T.expand_items(spec)[0]["prompt"]
    assert "新娘（参考图 1）" in prompt and "新郎（参考图 2）" in prompt
    assert prompt.count(PR.FIDELITY_ZH.format(n=92)) == 2                     # 逐人点名，各写一次相似度
    assert PR.EXCLUSIVITY_ZH.format(names="新娘、新郎") in prompt
    assert PR.SOLO_ZH not in prompt
    assert PR.BABY_SAFETY_ZH not in prompt                                    # 婚纱不带婴儿安全句
    # 只有一位（或没标 who）时退回单人排他锁
    solo = build("wedding", tmp_path, options={"scenes": ["教堂"]},
                 subject={"kind": "photo", "fidelity": 92, "assets": ["content/assets/PT-T-1/x.png"]})
    assert PR.SOLO_ZH in T.expand_items(solo)[0]["prompt"]


def test_portrait_reserves_text_space_only_when_needed(tmp_path):
    plain = T.expand_items(build("first_birthday", tmp_path, options={"scenes": ["学步"], "text": ""}))[0]
    assert PR.TEXT_SPACE_ZH not in plain["prompt"]
    with_text = T.expand_items(build("first_birthday", tmp_path, options={"scenes": ["学步"], "text": "周岁快乐|2026.09.09"}))[0]
    assert PR.TEXT_SPACE_ZH in with_text["prompt"]
    # 场景名自己说要留空位的，也留（百天的数字气球）
    reserve = T.expand_items(build("baby_100", tmp_path, options={"scenes": ["数字气球「100」（留空位）"], "text": ""}))[0]
    assert PR.TEXT_SPACE_ZH in reserve["prompt"]


# ---------------------------------------------------------------------------- 选项改动 → items 变


def test_edited_expression_list_changes_items(tmp_path):
    spec = build("emoji_pack", tmp_path, options={"expressions": ["开心", "自创的表情"], "with_caption": True})
    items = T.expand_items(spec)
    assert [i["label"] for i in items] == ["开心", "自创的表情"]
    assert "眼睛弯成月牙" in items[0]["prompt"]                       # 清单里的表情用内置描写
    assert "这一张的表情「自创的表情」：自创的表情" in items[1]["prompt"]  # 清单外的用原文
    assert [i["meta"]["caption"] for i in items] == ["开心", "自创的表情"]
    no_caption = T.expand_items(build("emoji_pack", tmp_path, options={"expressions": ["开心"], "with_caption": False}))
    assert no_caption[0]["meta"]["caption"] == ""


def test_edited_theme_and_shape_change_items(tmp_path):
    items = T.expand_items(build("sticker_set", tmp_path, options={"themes": ["咖啡", "遛狗"], "shape": "square"}))
    assert [i["label"] for i in items] == ["咖啡", "遛狗"]
    assert all(i["meta"]["shape"] == "square" for i in items)
    assert "方形贴纸，四角微圆" in items[0]["prompt"]
    assert "双手捧着一杯冒热气的白色马克杯" in items[0]["prompt"] and "贴图主题「遛狗」：遛狗" in items[1]["prompt"]


@pytest.mark.parametrize("template_id", ["newborn_30", "baby_100", "first_birthday", "wedding"])
def test_count_per_scene_multiplies_items(template_id, tmp_path):
    spec = build(template_id, tmp_path, options={"scenes": ["自定场景 A", "自定场景 B"], "count_per_scene": 3, "aspect": "1:1"})
    items = T.expand_items(spec)
    assert len(items) == 6
    assert [i["label"] for i in items] == ["自定场景 A #1", "自定场景 A #2", "自定场景 A #3",
                                           "自定场景 B #1", "自定场景 B #2", "自定场景 B #3"]
    assert [i["meta"]["take"] for i in items] == [1, 2, 3, 1, 2, 3]
    assert {i["meta"]["scene"] for i in items} == {"自定场景 A", "自定场景 B"}
    assert {i["aspect_ratio"] for i in items} == {"1:1"}
    # 一张一场景时标签不带 #
    single = T.expand_items(build(template_id, tmp_path, options={"scenes": ["自定场景 A"], "count_per_scene": 1}))
    assert [i["label"] for i in single] == ["自定场景 A"]


def test_style_preset_and_custom_anchor_reach_the_prompt(tmp_path):
    pixel = T.expand_items(build("emoji_pack", tmp_path, options={"expressions": ["开心"]}, style="pixel"))[0]
    assert pixel["prompt"].startswith("像素风贴纸")
    custom = T.expand_items(build("emoji_pack", tmp_path, options={"expressions": ["开心"]},
                                  style="custom", style_anchor="铜版画质感，只有黑白两色"))[0]
    assert custom["prompt"].startswith("铜版画质感，只有黑白两色")


def test_expand_keeps_existing_current_and_attempts(tmp_path):
    spec = build("emoji_pack", tmp_path, options={"expressions": ["开心", "哭"]})
    spec["items"] = T.expand_items(spec)
    spec["items"][0].update({"current": "I01-1.png", "request_hash": "abc", "status": "done",
                             "attempts": [{"file": "I01-1.png", "model": "@image", "request_hash": "abc"}]})
    spec["options"]["expressions"] = ["开心", "生气", "困"]
    again = T.expand_items(spec)
    assert [i["label"] for i in again] == ["开心", "生气", "困"]
    assert again[0]["current"] == "I01-1.png" and again[0]["status"] == "done" and len(again[0]["attempts"]) == 1
    assert again[1]["current"] is None and again[1]["attempts"] == []
    assert "眉毛竖起" in again[1]["prompt"]      # 换了表情，提示词跟着重算


def test_character_with_reference_asset_mentions_the_sheet(tmp_path):
    assets = tmp_path / "content" / "assets" / "ST-T-1"
    assets.mkdir(parents=True)
    (assets / "ref.png").write_bytes(b"x")
    spec = build("emoji_pack", tmp_path, options={"expressions": ["开心"]},
                 subject={"kind": "character", "name": "小满", "block": "齐肩黑发", "assets": ["content/assets/ST-T-1/ref.png"]})
    prompt = T.expand_items(spec)[0]["prompt"]
    assert PR.REFERENCE_CHARACTER_ZH in prompt
    assert PR.reference_assets(spec) == (str(assets / "ref.png"),)
