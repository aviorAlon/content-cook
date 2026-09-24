"""朗读驱动的时间轴（契约 §5）：各年龄段 / 各双语策略的公式、封面封底附加、句窗、翻页重叠、估算。"""

from __future__ import annotations

import pytest

from picturebook import timeline as T
from tests.picturebook_fixtures import small_book


def _segs(**pages):
    """{"P01": ([zh 秒...], [en 秒...])} → build_timeline 的 segments 输入。"""

    out = {}
    for pid, (zh, en) in pages.items():
        out[pid] = {}
        if zh:
            out[pid]["zh"] = [{"text": f"zh{i}", "duration": d, "file": f"{pid}_zh_{i + 1}.mp3"} for i, d in enumerate(zh)]
        if en:
            out[pid]["en"] = [{"text": f"en{i}", "duration": d, "file": f"{pid}_en_{i + 1}.mp3"} for i, d in enumerate(en)]
    return out


def _one_page(tmp_path, mode, kind="page", age="2-4"):
    spec = small_book(tmp_path, mode=mode, pages=3)
    spec["age_band"] = age
    spec["pages"] = [dict(spec["pages"][1], kind=kind)]
    return spec


def test_pauses_by_age_band(tmp_path):
    spec = small_book(tmp_path)
    assert T.pauses(spec) == {"lead": 0.6, "between": 0.8, "tail": 1.0}
    spec["age_band"] = "4-6"
    assert T.pauses(spec) == {"lead": 0.4, "between": 0.6, "tail": 0.8}
    spec["narration"]["pause"]["lead"] = 1.0
    assert T.pauses(spec)["lead"] == 1.0


@pytest.mark.parametrize("age, extra", [("2-4", 0.2), ("4-6", 0.0), ("6-8", 0.0)])
def test_page_zh_then_en_formula(tmp_path, age, extra):
    spec = _one_page(tmp_path, "page_zh_then_en", age=age)
    tl = T.build_timeline(spec, _segs(P02=([1.5], [2.0])))
    page = tl["pages"][0]
    assert page["duration"] == pytest.approx((0.4 + extra) + 1.5 + (0.6 + extra) + 2.0 + (0.8 + extra))
    zh, en = page["segments"]
    assert (zh["local_start"], zh["local_end"]) == pytest.approx((0.4 + extra, 1.9 + extra))
    assert en["local_start"] == pytest.approx(0.4 + extra + 1.5 + 0.6 + extra)
    assert tl["estimated"] is False


def test_sentence_alternate_pairs(tmp_path):
    spec = _one_page(tmp_path, "sentence_alternate", age="4-6")
    tl = T.build_timeline(spec, _segs(P02=([1.0, 1.2], [1.4, 1.6])))
    page = tl["pages"][0]
    expected = 0.4 + 1.0 + 0.3 + 1.4 + 0.5 + 1.2 + 0.3 + 1.6 + 0.8
    assert page["duration"] == pytest.approx(expected)
    order = [(s["lang"], s["index"]) for s in page["segments"]]
    assert order == [("zh", 0), ("en", 0), ("zh", 1), ("en", 1)]
    zh1, en1, zh2, en2 = page["segments"]
    assert en1["local_start"] == pytest.approx(0.4 + 1.0 + 0.3)
    assert zh2["local_start"] == pytest.approx(en1["local_end"] + 0.5)
    # 句窗：第一句从 0 显示到下一句同语言开始；最后一句显示到页尾
    assert (zh1["display_start"], zh1["display_end"]) == pytest.approx((0.0, zh2["local_start"]))
    assert (zh2["display_start"], zh2["display_end"]) == pytest.approx((zh2["local_start"], page["duration"]))
    assert en2["display_end"] == pytest.approx(page["duration"])


def test_single_language_modes_and_same_language_gap(tmp_path):
    spec = _one_page(tmp_path, "zh_only", age="4-6")
    tl = T.build_timeline(spec, _segs(P02=([1.0, 2.0], None)))
    assert tl["pages"][0]["duration"] == pytest.approx(0.4 + 1.0 + 0.3 + 2.0 + 0.8)
    assert all(s["lang"] == "zh" for s in tl["pages"][0]["segments"])
    spec = _one_page(tmp_path, "en_only", age="4-6")
    tl = T.build_timeline(spec, _segs(P02=(None, [2.5])))
    assert tl["pages"][0]["duration"] == pytest.approx(0.4 + 2.5 + 0.8)


def test_cover_and_back_extras(tmp_path):
    cover = _one_page(tmp_path, "page_zh_then_en", kind="cover", age="4-6")
    tl = T.build_timeline(cover, _segs(P02=([1.0], [1.0])))
    assert tl["pages"][0]["duration"] == pytest.approx(0.4 + 1.0 + 0.6 + 1.0 + 0.8 + T.COVER_HOLD_SECONDS)
    assert tl["pages"][0]["cover_hold"] == 2.0
    back = _one_page(tmp_path, "page_zh_then_en", kind="back", age="4-6")
    tl = T.build_timeline(back, _segs(P02=([1.0], [1.0])))
    assert tl["pages"][0]["duration"] == pytest.approx(0.4 + 1.0 + 0.6 + 1.0 + 0.8 + T.BACK_HOLD_SECONDS)


def test_xfade_overlap_and_cut(tmp_path):
    spec = small_book(tmp_path, mode="page_zh_then_en")
    spec["age_band"] = "4-6"
    tl = T.build_timeline(spec, _segs(P01=([1.0], [1.0]), P02=([1.0], [1.0]), P03=([1.0], [1.0])))
    p1, p2, p3 = tl["pages"]
    assert p1["transition_out"] == T.XFADE_SECONDS and p2["transition_out"] == 0.0 and p3["transition_out"] == 0.0
    assert p2["start"] == pytest.approx(p1["duration"] - 0.4)          # page_turn 重叠 0.4
    assert p3["start"] == pytest.approx(p2["start"] + p2["duration"])   # cut 不重叠
    assert tl["total"] == pytest.approx(p3["start"] + p3["duration"])
    seg = p2["segments"][0]
    assert seg["start"] == pytest.approx(p2["start"] + seg["local_start"])


def test_missing_audio_is_estimated(tmp_path):
    spec = small_book(tmp_path)
    tl = T.build_timeline(spec, {})
    assert tl["estimated"] is True and all(s["estimated"] and s["file"] is None for p in tl["pages"] for s in p["segments"])
    assert tl["total"] > 0
    partial = T.build_timeline(spec, _segs(P01=([1.0], [1.0])))
    assert partial["pages"][0]["segments"][0]["estimated"] is False and partial["estimated"] is True


def test_split_sentences():
    assert T.split_sentences("是小鸟！小鸟在袜子里做了个窝。", "zh") == ["是小鸟！", "小鸟在袜子里做了个窝。"]
    assert T.split_sentences("呼呼呼，红袜子飞到哪儿去了？", "zh") == ["呼呼呼，红袜子飞到哪儿去了？"]
    assert T.split_sentences("It is Little Bird! She made a nest in the sock.", "en") == ["It is Little Bird!", "She made a nest in the sock."]
    assert T.split_sentences("Whoosh — where did the red sock go?", "en") == ["Whoosh — where did the red sock go?"]
    assert T.split_sentences("", "zh") == []
