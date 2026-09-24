"""插画提示词（契约 §4）：顺序、出场角色一个个写名字 + 排他锁、NO_TEXT 尾句、中英、参考图。"""

from __future__ import annotations

from picturebook import prompts as P
from storyvideo.prompts import NO_TEXT_TAIL_CN, NO_TEXT_TAIL_EN
from tests.picturebook_fixtures import example_data, example_spec, png_bytes
from picturebook.spec import normalize_book


def test_zh_prompt_order_lock_and_tail(tmp_path):
    spec = example_spec(tmp_path)
    text = P.compile_page_prompt(spec, "P04")
    anchor = spec["style_anchor"]
    assert text.startswith(anchor)
    i_scene_anchor = text.index("大树：院子边一棵枝叶茂密的大树")
    i_fox = text.index("小狐狸：橘红色小狐狸，白色尾巴尖")
    i_bird = text.index("小鸟：圆滚滚的天蓝色小鸟")
    i_scene = text.index("大树的枝头挂着红袜子")
    i_comp = text.index(P.COMPOSITION_ZH)
    i_lock = text.index(P.EXCLUSIVITY_ZH.format(names="小狐狸、小鸟"))
    assert len(anchor) < i_scene_anchor < i_fox < i_bird < i_scene < i_comp < i_lock
    assert text.endswith(NO_TEXT_TAIL_CN)
    assert "小老鼠：" not in text and "灰色小老鼠" not in text   # 没出场的角色一个字不进


def test_en_only_prompt_uses_english_lock_and_tail(tmp_path):
    data = example_data()
    data["bilingual_mode"] = "en_only"
    spec = normalize_book(data, project_root=tmp_path, book_id="PB-EN")
    text = P.compile_page_prompt(spec, "P04")
    assert "Little Fox: 橘红色小狐狸" in text and "Little Bird: 圆滚滚的天蓝色小鸟" in text
    assert "Only these characters appear: Little Fox, Little Bird. Nobody else is in the picture, and no other animals." in text
    assert P.COMPOSITION_EN.strip() in text and text.rstrip().endswith(NO_TEXT_TAIL_EN.strip())
    assert "画面里只出现这几个角色" not in text


def test_no_character_page_gets_no_character_lock(tmp_path):
    data = example_data()
    data["pages"][1]["characters"] = []
    spec = normalize_book(data, project_root=tmp_path, book_id="PB-NC")
    text = P.compile_page_prompt(spec, "P02")
    assert P.NO_CHARACTER_ZH in text and "画面里只出现这几个角色" not in text


def test_negative_adds_text_words_without_duplicates(tmp_path):
    spec = example_spec(tmp_path)
    neg = P.compile_page_negative(spec, "P02")
    parts = [x.strip() for x in neg.split(",")]
    assert parts[:3] == ["photo", "3D render", "text"] and "watermark" in parts and "水印" in parts
    assert len(parts) == len(set(parts))


def test_reference_assets_are_present_files_in_order(tmp_path):
    data = example_data()
    data["characters"]["fox"]["asset"] = "content/assets/PB-REF/fox.png"
    data["characters"]["bird"]["asset"] = "content/assets/PB-REF/bird.png"    # 不创建文件 → 跳过
    data["scenes"]["tree"]["asset"] = "content/assets/PB-REF/tree.png"
    for name in ("fox.png", "tree.png"):
        target = tmp_path / "content" / "assets" / "PB-REF" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png_bytes())
    spec = normalize_book(data, project_root=tmp_path, book_id="PB-REF")
    refs = P.page_reference_assets(spec, "P04")
    assert [r.endswith(n) for r, n in zip(refs, ("fox.png", "tree.png"))] == [True, True] and len(refs) == 2
    req = P.page_request_data(spec, "P04")
    assert req["aspect_ratio"] == "3:4" and req["model"] == "@image" and req["name"] == "PB-REF-P04"
    assert req["reference_assets"] == list(refs) and req["negative_prompt"].startswith("photo, ")
