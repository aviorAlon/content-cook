"""风格卡库的完整性（DESIGN §41 / 契约 docs/image-style-cards.md §1）。

卡库是代码资源，进不了包或写坏了就是"界面上没有风格可挑"，所以这里把契约里能静态核对的都核对了：
id 唯一、分类存在、色板 5–7 个色名、``camera`` 不写数字焦段光圈、``fits`` 每个目标都指向真实的
模板 / 绘本 / 海报、文件大小、必须覆盖的那张清单。
"""
from __future__ import annotations

import json
import re

import pytest

from imagecraft import cards as C

CARDS = C.style_cards()
CATEGORY_IDS = C.category_ids()
SIZE_CAP = 40 * 1024

# 契约 §1 的覆盖清单：每一类必须有的卡（按中文名找，卡可以多不能少）
REQUIRED = {
    "portrait": ["影楼柔光", "窗光自然", "暖调胶片", "黑白棚拍"],
    "illustration": ["水彩绘本", "蜡笔童趣", "剪纸拼贴", "扁平矢量"],
    "character": ["扁平可爱贴纸", "线稿涂鸦", "Q 版三维", "像素"],
    "product": ["纯色悬浮", "场景静物"],
    "fashion": ["街头大片", "编辑部棚拍"],
    "food": ["暗调质感", "明亮平铺"],
    "poster": ["极简大字", "电影质感", "复古印刷"],
    "social": ["干净 UI 展示", "社交九宫格"],
}
REQUIRED_FIELDS = ("id", "name", "english", "aliases", "category", "summary", "anchor_zh", "anchor_en",
                   "lighting", "palette", "materials", "camera", "constraints", "fits", "model_hint")


def _fit_targets() -> set[str]:
    """``fits`` 允许的目标：真实的 ``<module>.<template>`` + picturebook + poster。"""

    from imageset.templates import TEMPLATES

    return {f"{module}.{tid}" for module, tid in TEMPLATES} | {"picturebook", "poster"}


def test_library_loads_and_size_is_capped():
    assert C.STYLE_CARDS_PATH.is_file(), C.STYLE_CARDS_PATH
    raw = C.STYLE_CARDS_PATH.read_bytes()
    assert len(raw) <= SIZE_CAP, f"卡库 {len(raw)} 字节，超过 {SIZE_CAP}"
    data = json.loads(raw.decode("utf-8"))
    assert data["_meta"]["schema_version"] == 1
    assert data["_meta"]["basis"].startswith("本仓库自写")     # 卡的文字是自己写的，写明出处
    # 文件本身按 ensure_ascii=False / indent 2 写：中文原样、两格缩进
    text = raw.decode("utf-8")
    assert "\\u" not in text
    assert '\n  "cards"' in text


def test_card_count_and_per_category_range():
    assert 22 <= len(CARDS) <= 26, len(CARDS)
    for cid in CATEGORY_IDS:
        n = len([c for c in CARDS if c["category"] == cid])
        assert 2 <= n <= 4, (cid, n)


def test_every_card_has_every_field():
    for card in CARDS:
        for field in REQUIRED_FIELDS:
            assert card.get(field), (card["id"], field)
        assert isinstance(card["palette"], list) and isinstance(card["constraints"], list)
        assert isinstance(card["fits"], list) and isinstance(card["aliases"], list)
        assert card["model_hint"] in C.MODEL_HINTS, card["id"]


def test_ids_and_names_are_unique():
    ids = [c["id"] for c in CARDS]
    names = [c["name"] for c in CARDS]
    english = [c["english"] for c in CARDS]
    assert len(set(ids)) == len(ids)
    assert len(set(names)) == len(names)
    assert len(set(english)) == len(english)
    # 别名也不能撞（撞了 find_card 就有两个答案）
    keys: list[str] = []
    for card in CARDS:
        keys += [str(card["id"]).lower(), str(card["name"]).lower(), str(card["english"]).lower()]
        keys += [str(a).lower() for a in card["aliases"]]
    assert len(set(keys)) == len(keys), [k for k in keys if keys.count(k) > 1]


def test_categories_exist_and_are_used():
    assert len(CATEGORY_IDS) == 8
    for card in CARDS:
        assert card["category"] in CATEGORY_IDS, card["id"]
    for cid in CATEGORY_IDS:
        assert any(c["category"] == cid for c in CARDS), cid


def test_palette_is_five_to_seven_concrete_colour_names():
    for card in CARDS:
        palette = card["palette"]
        assert C.PALETTE_MIN <= len(palette) <= C.PALETTE_MAX, (card["id"], len(palette))
        assert len(set(palette)) == len(palette), card["id"]
        for name in palette:
            assert isinstance(name, str) and 1 <= len(name.strip()) <= 6, (card["id"], name)
            assert not re.search(r"\d", name), (card["id"], name)      # 色名不是色值


def test_camera_has_no_lens_numbers():
    """``camera`` 只写观感——焦段、光圈这类数字换不来对应的画面（nano_banana 方言还会把它们删掉），写了只是噪音。"""

    for card in CARDS:
        assert not re.search(r"\d", card["camera"]), (card["id"], card["camera"])
        low = card["camera"].lower()
        assert "mm" not in low and "f/" not in low and "iso" not in low, card["id"]


def test_fits_targets_all_resolve():
    targets = _fit_targets()
    for card in CARDS:
        assert card["fits"], card["id"]
        for fit in card["fits"]:
            assert fit in targets, (card["id"], fit)
    # 每个目标至少有一张卡可选，否则界面上那一格是空的
    covered = {f for c in CARDS for f in c["fits"]}
    assert targets <= covered, targets - covered


@pytest.mark.parametrize("category,names", sorted(REQUIRED.items()))
def test_required_coverage(category, names):
    have = {c["name"] for c in CARDS if c["category"] == category}
    assert set(names) <= have, set(names) - have


def test_find_card_by_id_name_english_alias():
    card = C.find_card("studio_soft_portrait")
    assert card and card["name"] == "影楼柔光"
    assert C.find_card("影楼柔光")["id"] == "studio_soft_portrait"
    assert C.find_card("Soft studio portrait")["id"] == "studio_soft_portrait"
    assert C.find_card("STUDIO")["id"] == "studio_soft_portrait"        # 别名不分大小写
    assert C.find_card("没有这张") is None
    assert C.find_card("") is None and C.find_card(None) is None
    assert C.normalize_card("柔光棚拍") == "studio_soft_portrait"
    with pytest.raises(ValueError):
        C.normalize_card("nope")


def test_cards_for_filters():
    book = C.cards_for(fits="picturebook")
    assert book and all("picturebook" in c["fits"] for c in book)
    portraits = C.cards_for(category="portrait")
    assert len(portraits) == 4
    both = C.cards_for(category="portrait", fits="portrait.wedding")
    assert both and all(c["category"] == "portrait" and "portrait.wedding" in c["fits"] for c in both)
    assert len(C.cards_for()) == len(CARDS)


def test_payloads_are_json_safe():
    payload = C.library_payload()
    assert json.dumps(payload, ensure_ascii=False)
    assert len(payload["cards"]) == len(CARDS)
    assert payload["categories"][0]["id"] == "portrait"
    one = C.card_payload(CARDS[0])
    assert one["summary_line"] and one["id"] == CARDS[0]["id"]
    assert C.anchor_text(CARDS[0], True) == CARDS[0]["anchor_zh"]
    assert C.anchor_text(CARDS[0], False) == CARDS[0]["anchor_en"]
    assert C.anchor_text(None, True) == ""


def test_missing_library_is_not_fatal(monkeypatch, tmp_path):
    """卡库缺了 / 坏了只是没有卡可选，不该让出图打不开。"""

    from pathlib import Path

    for path in (tmp_path / "nope.json", tmp_path / "broken.json"):
        if path.name == "broken.json":
            path.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(C, "STYLE_CARDS_PATH", Path(path))
        for fn in (C._data, C.style_cards, C.categories):
            fn.cache_clear()
        assert C.style_cards() == () and C.categories() == ()
        assert C.find_card("studio_soft_portrait") is None
    monkeypatch.undo()
    for fn in (C._data, C.style_cards, C.categories):
        fn.cache_clear()
    assert len(C.style_cards()) == len(CARDS)
