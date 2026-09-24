"""绘本项目文件（DESIGN §39 / 契约 §1）：归一化默认值、形状错误、编号、读写往返。"""

from __future__ import annotations

import copy
import json
from datetime import date

import pytest

from picturebook import spec as S
from tests.picturebook_fixtures import example_data, example_spec


def test_example_normalizes_with_defaults(tmp_path):
    spec = example_spec(tmp_path, "PB-X-1")
    assert spec["id"] == "PB-X-1" and spec["output_root"].endswith("PB-X-1") and "picturebook" in spec["output_root"]
    assert spec["pages"][0]["kind"] == "cover" and spec["pages"][-1]["kind"] == "back"
    assert spec["pages"][1]["kb"] == {"dir": "left", "scale": None}
    assert spec["pages"][0]["kb"]["scale"] == [1.0, 1.06]
    assert spec["narration"]["pause"] == {"lead": 0.4, "between": 0.6, "tail": 0.8}
    assert spec["subtitle"]["highlight"] == "#FFD27A" and spec["music"] is None and spec["page_turn_sfx"] is None
    assert S.prompt_language(spec) == "zh"


def test_minimal_book_fills_ids_kinds_and_defaults(tmp_path):
    data = {"title_zh": "小书", "pages": [
        {"scene": "a", "zh": "一", "en": "One."}, {"scene": "b", "zh": "二", "en": "Two."}, {"scene": "c", "zh": "三", "en": "Three."}]}
    spec = S.normalize_book(data, project_root=tmp_path, book_id="PB-M-1")
    assert [p["id"] for p in spec["pages"]] == ["P01", "P02", "P03"]
    assert [p["kind"] for p in spec["pages"]] == ["cover", "page", "back"]
    assert all(p["kb"]["dir"] == "push" and p["flip"] == "page_turn" for p in spec["pages"])
    assert spec["age_band"] == "2-4" and spec["bilingual_mode"] == "sentence_alternate"
    assert spec["style_anchor"] == S.DEFAULT_STYLE_ANCHOR and spec["image_params"]["aspect_ratio"] == "3:4"
    assert spec["delivery"] == {"aspect_ratio": "9:16", "resolution": "1080p", "landscape_variant": False}


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d["pages"][1].__setitem__("characters", ["ghost"]), "出场角色「ghost」不在 characters 表里"),
    (lambda d: d["pages"][1].__setitem__("scene_ref", "moon"), "场景锚「moon」不在 scenes 表里"),
    (lambda d: d["pages"][1].__setitem__("zh", ""), "page P02.zh 不能为空"),
    (lambda d: d.__setitem__("age_band", "9"), "age_band 只能是"),
    (lambda d: d["pages"][1]["kb"].__setitem__("dir", "spin"), "kb.dir 只能是"),
    (lambda d: d["pages"][1].__setitem__("id", "P01"), "页 id 重复"),
    (lambda d: d["pages"][1].__setitem__("id", "page-2"), "要写成 P01 这样的形状"),
    (lambda d: d["characters"]["fox"].__setitem__("asset", "C:/x/fox.png"), "只能是项目内的相对路径"),
    (lambda d: d["characters"]["fox"].__setitem__("block", ""), "characters.fox.block 不能为空"),
    (lambda d: d["pages"][1].__setitem__("scene", " "), "page P02.scene 不能为空"),
    (lambda d: d["narration"].__setitem__("en_rate", "slow"), "en_rate 要写成百分比"),
    (lambda d: d.__setitem__("pages", "nope"), "pages 要是数组"),
])
def test_shape_errors(tmp_path, mutate, needle):
    data = example_data()
    mutate(data)
    with pytest.raises(S.BookSpecError) as exc:
        S.normalize_book(data, project_root=tmp_path, book_id="PB-E-1")
    assert needle in str(exc.value)


def test_bilingual_mode_decides_required_text(tmp_path):
    data = example_data()
    data["bilingual_mode"] = "en_only"
    for page in data["pages"]:
        page["zh"] = ""
    spec = S.normalize_book(data, project_root=tmp_path, book_id="PB-EN-1")
    assert S.prompt_language(spec) == "en"
    data["bilingual_mode"] = "zh_only"
    with pytest.raises(S.BookSpecError):
        S.normalize_book(data, project_root=tmp_path, book_id="PB-ZH-1")


def test_bad_or_missing_id(tmp_path):
    with pytest.raises(S.BookSpecError, match="id 不能为空"):
        S.normalize_book(example_data(), project_root=tmp_path)
    with pytest.raises(S.BookSpecError, match="只能用英文"):
        S.normalize_book(example_data(), project_root=tmp_path, book_id="bad id")


def test_next_book_id_numbers_per_day():
    today = date(2026, 9, 9)
    assert S.next_book_id([], today=today) == "PB-20260909-1"
    assert S.next_book_id(["PB-20260909-1", "PB-20260908-3"], today=today) == "PB-20260909-2"


def test_save_and_load_roundtrip(tmp_path):
    spec = example_spec(tmp_path, "PB-R-1")
    path = S.save_book(spec, project_root=tmp_path)
    assert path == tmp_path / "content" / "picturebook" / "PB-R-1.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "output_root" not in on_disk and "project_root" not in on_disk and on_disk["id"] == "PB-R-1"
    loaded = S.load_book("PB-R-1", project_root=tmp_path)
    assert loaded["pages"] == spec["pages"] and loaded["characters"] == spec["characters"]
    assert S.existing_book_ids(tmp_path) == ["PB-R-1"]
    # 文件名与文件里的 id 对不上 → 拒绝
    wrong = copy.deepcopy(on_disk)
    wrong["id"] = "PB-OTHER"
    (tmp_path / "content" / "picturebook" / "PB-R-2.json").write_text(json.dumps(wrong), encoding="utf-8")
    with pytest.raises(S.BookSpecError, match="对不上"):
        S.load_book("PB-R-2", project_root=tmp_path)
    with pytest.raises(S.BookSpecError, match="没有这本绘本"):
        S.load_book("PB-NOPE", project_root=tmp_path)


def test_resolve_asset_only_when_file_exists(tmp_path):
    spec = example_spec(tmp_path, "PB-A-1")
    assert S.resolve_asset(spec, "content/assets/PB-A-1/fox.png") is None
    target = tmp_path / "content" / "assets" / "PB-A-1" / "fox.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    assert S.resolve_asset(spec, "content/assets/PB-A-1/fox.png") == target.resolve()
    assert S.resolve_asset(spec, None) is None
    assert S.page_by_id(spec, "P03")["id"] == "P03"
    with pytest.raises(S.BookSpecError):
        S.page_by_id(spec, "P99")
