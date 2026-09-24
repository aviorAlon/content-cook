"""绘本文本检查（契约 §3）：每条规则命中 / 不命中，示例书零告警，问题卡形状。"""

from __future__ import annotations

from picturebook import lint as L
from tests.picturebook_fixtures import example_spec
from webui.problems import problem_card


def _spec(tmp_path, mutate=None):
    spec = example_spec(tmp_path)
    if mutate:
        mutate(spec)
    return spec


def _has(warnings, needle):
    return any(needle in w for w in warnings)


def test_example_is_clean_and_has_repetition_note(tmp_path):
    spec = _spec(tmp_path)
    assert L.lint_book(spec) == []
    notes = L.lint_notes(spec)
    assert len(notes) == 1 and "重复母题 1 组（P03/P05/P07）" in notes[0]
    report = L.lint_report(spec)
    assert report["stats"]["pages"] == 10 and report["stats"]["vocab"] == {"袜子/sock": 18, "风/wind": 6}


def test_age_grading_page_count_and_chars(tmp_path):
    spec = _spec(tmp_path, lambda s: s["pages"].__delitem__(slice(3, 6)))
    assert _has(L.lint_book(spec), "全书：2-4 岁的书要 8–10 页（含封面封底），现在是 7 页")
    spec = _spec(tmp_path, lambda s: s["pages"][1].__setitem__("zh", "小狐狸把洗好的红袜子黄袜子蓝袜子一只一只夹在绳子上"))
    assert _has(L.lint_book(spec), "page P02: 2-4 岁每页中文不超过 20 字，这页 25 字")
    # 封面不查字数
    spec = _spec(tmp_path, lambda s: s["pages"][0].__setitem__("zh", "小狐狸把洗好的红袜子黄袜子蓝袜子一只一只夹在绳子上"))
    assert not _has(L.lint_book(spec), "不超过 20 字")
    # 4-6 岁有下限
    spec = _spec(tmp_path, lambda s: s.__setitem__("age_band", "4-6"))
    warnings = L.lint_book(spec)
    assert _has(warnings, "全书：4-6 岁的书要 10–14 页") is False and _has(warnings, "4-6 岁每页中文要 20–40 字，这页只有 12 字")


def test_hooks_gap_last_page_and_scary(tmp_path):
    # 封面是书名页不计入：清掉 P02–P04 三页 → 在 P04 报；只清 P02/P03 两页不报
    spec = _spec(tmp_path, lambda s: [p.__setitem__("hook", "") for p in s["pages"][1:4]])
    warnings = L.lint_book(spec)
    assert _has(warnings, "page P04: 连续 3 页没有翻页钩") and not _has(warnings, "page P03:")
    # 示例里 P04 本来就没钩：只清 P03 → P03/P04 两页空，不报
    assert not _has(L.lint_book(_spec(tmp_path, lambda s: s["pages"][2].__setitem__("hook", ""))), "连续 3 页")
    spec = _spec(tmp_path, lambda s: s["pages"][-1].__setitem__("hook", "明天会发生什么？"))
    assert _has(L.lint_book(spec), "page P10: 结尾页留了翻页钩")
    spec = _spec(tmp_path, lambda s: s["pages"][2].__setitem__("hook", "树后面会不会有黑影？"))
    assert _has(L.lint_book(spec), "page P03: 翻页钩里有恐怖词「黑影」")


def test_kb_direction_must_change(tmp_path):
    spec = _spec(tmp_path, lambda s: s["pages"][2]["kb"].__setitem__("dir", "left"))
    assert _has(L.lint_book(spec), "page P03: KB 方向跟上一页一样（left）")


def test_onomatopoeia_per_page_and_per_book(tmp_path):
    spec = _spec(tmp_path, lambda s: s["pages"][3].__setitem__("zh", "哗啦啦，扑通扑通，是小鸟！"))
    assert _has(L.lint_book(spec), "page P04: 一页里有 2 组拟声词")
    # 中英同一组不重复算
    assert L.onomatopoeia_groups("呼呼呼") == ["呼呼"] and L.onomatopoeia_groups("Whoosh, whoosh!") == ["whoosh"]

    def many(s):
        s["pages"][3]["zh"], s["pages"][5]["zh"], s["pages"][7]["zh"], s["pages"][8]["zh"] = "哗啦啦下雨了。", "扑通一声。", "汪汪叫。", "嘎嘎笑。"

    assert _has(L.lint_book(_spec(tmp_path, many)), "全书：全书用了 5 组拟声词")


def test_scene_rules(tmp_path):
    spec = _spec(tmp_path, lambda s: s["pages"][1].__setitem__("scene", "小狐狸夹好袜子，然后回屋喝水"))
    assert _has(L.lint_book(spec), "page P02: 画面描述里有「然后」")
    spec = _spec(tmp_path, lambda s: s["pages"][1].__setitem__("scene", "小狐狸站在院子里，门牌上写着名字"))
    assert _has(L.lint_book(spec), "page P02: 画面描述里出现「写着」，插画里不能有文字")
    spec = _spec(tmp_path, lambda s: s["pages"][8].__setitem__("scene", "草地上，动物们围成一圈"))
    assert _has(L.lint_book(spec), "page P09: 群像页用了指代词「动物们」")
    # 单角色页用「大家」不算群像问题
    spec = _spec(tmp_path, lambda s: s["pages"][1].__setitem__("scene", "小狐狸对着大家笑"))
    assert not _has(L.lint_book(spec), "群像页")


def test_calm_ending_and_vocab(tmp_path):
    spec = _spec(tmp_path, lambda s: s["pages"][-1].__setitem__("zh", "小狐狸真开心！"))
    assert _has(L.lint_book(spec), "page P10: 结尾不够安稳：2-4 岁的书最后一页中文里没有")
    spec = _spec(tmp_path, lambda s: s["education"]["vocab"].append("月亮/moon"))
    assert _has(L.lint_book(spec), "全书：目标词「月亮/moon」全书只出现 0 次（要 ≥3 次）")


def test_repetition_note_when_missing(tmp_path):
    def vary(s):
        s["pages"][4]["zh"], s["pages"][6]["zh"] = "黄袜子也被吹跑了。", "最后一只蓝袜子飘了起来。"

    notes = L.lint_notes(_spec(tmp_path, vary))
    assert len(notes) == 1 and "没有出现 ≥3 次的重复句式" in notes[0]
    assert L.lint_book({"pages": []}) == ["全书：还没有分页——先起草或手填分页表"]


def test_problem_cards_parse_page_and_book_scope():
    card = problem_card("page P04: 连续 3 页没有翻页钩——隔一两页就在页尾留一个小问题或没做完的事，翻过去再揭晓")
    assert card["scope"] == "page" and card["target"] == "P04" and card["kind"] == "book"
    assert card["impact"] == "连续 3 页没有翻页钩" and card["fix"].startswith("隔一两页")
    book = problem_card("全书：目标词「风/wind」全书只出现 2 次（要 ≥3 次）——把它放进重复句式里，每次重复都带上")
    assert book["scope"] == "episode" and book["target"] == "" and book["kind"] == "book" and book["impact"].startswith("目标词")
