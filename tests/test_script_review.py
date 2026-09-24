"""剧本工作台的审稿（docs/scriptlab-spec.md §5）：清单按 target 选、提示词、**引文核对闸门**（段当「场」，
合句与碎片降级都借 ``podcast.review``）、按版本存记录与过时判定、告警句头。

模型一律是替身（假 dispatch），这里验的是「模型说的话哪些能信」：引文要逐字落在所列段的某一句台词或相邻两句里，
段号要存在、编号要对得上清单条目；段的标题 / 目标 / 备注与 brief 不算证据。
"""

from __future__ import annotations

import pytest

from script import review as R
from script import spec as S
from storyvideo import craft_review as CR
from storyvideo import genre_review as GR
from webui.problems import KIND_LABELS, problem_card

LINES = [
    {"who": "jie", "text": "你知道的，我们家的规矩是长子继承。"},
    {"who": "lin", "text": "我知道。"},
    {"who": "jie", "text": "那就别再问了。"},
]


def make(tmp_path, *, target="generic", lines=None, spec_id="SC-R", **extra) -> dict:
    data = {
        "title": "老宅那一夜", "target": target,
        "brief": {"topic": "长子继承", "materials": "老宅 2018 年过户"},
        "speakers": [{"id": "jie", "name": "阿杰", "persona": "长子", "stance": "规矩不能破"},
                     {"id": "lin", "name": "小林", "persona": "次子"}],
        "blocks": [{"id": "B01", "title": "堂屋", "goal": "把规矩摆上桌", "note": "别解释来历"},
                   {"id": "B02", "title": "院子"}],
        "versions": [{"id": "V1", "blocks": [{"id": "B01", "lines": lines if lines is not None else LINES}]}],
        "current": "V1",
    }
    data.update(extra)
    return S.normalize(data, project_root=tmp_path, spec_id=spec_id)


def item(spec, *, source=None, index=1, verdict="violated", shots=("B01",),
         quote="你知道的，我们家的规矩是长子继承", why="w", fix="f", **extra) -> dict:
    items = R.checklist_items(R.load_checklist(spec))
    source = source or items[0]["source"]
    return {"source": source, "index": index, "item": extra.pop("item", "模型抄的条目"), "verdict": verdict,
            "shots": list(shots), "quote": quote, "why": why, "fix": fix, **extra}


# ---- 1. 清单按 target 选


def test_checklist_follows_target(tmp_path):
    podcast = make(tmp_path, target="podcast")
    assert R.sheet_key(podcast) == "humanlike"
    assert R.load_checklist(podcast)["id"] == "podcast-humanlike-v1"
    assert R.checklist_info(podcast) == {"id": "podcast-humanlike-v1", "name": "真人感清单"}
    assert R.group_labels(podcast) == {"speech": "口语", "dynamics": "对谈动态", "substance": "内容"}
    for target in ("drama_screenplay", "drama_shot", "generic"):
        spec = make(tmp_path, target=target)
        assert R.sheet_key(spec) == "craft" and R.load_checklist(spec)["id"] == "craft-v1"
        assert R.group_labels(spec) == CR.GROUP_LABELS
    assert R.checklist_items(R.load_checklist(podcast))[0]["source"] == "speech"


# ---- 2. 正文与提示词


def test_material_lists_blocks_and_keeps_context_unquotable(tmp_path):
    m = R.material(make(tmp_path))
    assert list(m["blocks"]) == ["B01", "B02"]
    assert m["blocks"]["B01"]["title"] == "堂屋"
    assert ("目标", "把规矩摆上桌") in m["blocks"]["B01"]["fields"]
    assert ("台词（阿杰）", "你知道的，我们家的规矩是长子继承。") in m["blocks"]["B01"]["fields"]
    labels = [label for label, _v in m["context"]]
    assert "稿子" in labels and "目标形态" in labels and "角色" in labels
    assert ("角色", "阿杰；人设：长子；立场：规矩不能破") in m["context"]
    # 核对用的那份只放台词——标题 / 目标 / 备注不是说出口的话，不算证据
    doc = R.as_podcast(make(tmp_path))
    assert doc["segments"][0]["beats"] == [] and doc["segments"][0]["questions"] == []
    assert [l["text"] for l in doc["segments"][0]["lines"]] == [l["text"] for l in LINES]


def test_prompt_numbers_every_item_and_lists_every_block(tmp_path):
    spec = make(tmp_path)
    checklist = R.load_checklist(spec)
    system, user = R.build_review_prompt(spec, checklist)
    for it in R.checklist_items(checklist):
        assert f"{it['index']}. {it['item']}" in user
    for key, label in R.group_labels(spec).items():
        assert f"{label}（{key}）：" in user
    assert "## 段 B01｜堂屋" in user and "## 段 B02｜院子" in user
    assert "台词（阿杰）：你知道的，我们家的规矩是长子继承。" in user
    assert "全篇（上下文，不能引用）：" in user and "老宅 2018 年过户" in user
    for rule in ("段号", "找不到能引用的原文就判 ok", "不要编造", "不评", "不能当引文", "逐字核对", "相邻的两句台词"):
        assert rule in system
    assert "## 镜 " not in user and "## 环节 " not in user and "## 场 " not in user
    podcast_system, _ = R.build_review_prompt(make(tmp_path, target="podcast"), R.load_checklist(make(tmp_path, target="podcast")))
    assert "口语 / 对谈动态 / 内容" in podcast_system


# ---- 3. 核对闸门


def test_existing_quote_passes_and_checklist_text_replaces_model_copy(tmp_path):
    spec = make(tmp_path)
    checklist = R.load_checklist(spec)
    result, stats = R.verify_review({"summary": "s", "items": [item(spec, item="模型改写过的条目")]}, spec, checklist=checklist)
    row = result["items"][0]
    assert row["evidence_verified"] is True and row["verdict"] == "violated"
    assert row["item"] == R.checklist_items(checklist)[0]["item"]
    assert stats["checked"] == 1 and stats["verified"] == 1 and stats["demoted"] == 0


def test_adjacent_pair_quote_passes_but_cross_line_fragment_is_demoted(tmp_path):
    """相邻两句拼起来能引（接话太短时用）；只跨句界的碎片不是稿子里的一句，降级。"""

    spec = make(tmp_path)
    pair = R.verify_review({"summary": "", "items": [item(spec, quote="我知道。那就别再问了。")]}, spec)[0]["items"][0]
    assert pair["evidence_verified"] is True
    fragment = R.verify_review({"summary": "", "items": [item(spec, quote="长子继承。我知")]}, spec)[0]["items"][0]
    assert fragment["evidence_verified"] is False and fragment["evidence_problem"] == "quote_not_found"
    assert fragment["verdict"] == "risk" and fragment["model_verdict"] == "violated"


@pytest.mark.parametrize("kwargs, problem", [
    ({"quote": ""}, "no_quote"),
    ({"quote": "长子"}, "quote_too_short"),
    ({"quote": "这句稿子里根本没有"}, "quote_not_found"),
    ({"shots": ("B09",)}, "unknown_ids"),
    ({"quote": "把规矩摆上桌"}, "quote_not_found"),        # 段的目标不算证据
    ({"quote": "老宅 2018 年过户"}, "quote_not_found"),     # brief 不算证据
    ({"shots": ("B02",)}, "quote_not_found"),              # 引文不在所列的段里
])
def test_bad_evidence_is_demoted(tmp_path, kwargs, problem):
    spec = make(tmp_path)
    row = R.verify_review({"summary": "", "items": [item(spec, **kwargs)]}, spec)[0]["items"][0]
    assert row["evidence_verified"] is False and row["evidence_problem"] == problem
    assert row["verdict"] == "risk"


def test_unknown_index_is_dropped(tmp_path):
    spec = make(tmp_path)
    _result, stats = R.verify_review({"summary": "", "items": [item(spec, index=99), item(spec, source="不存在的组")]}, spec)
    assert stats["dropped"] == 2


# ---- 4. 落盘、按版本、过时


def test_run_review_writes_one_record_per_version(tmp_path):
    spec = make(tmp_path, versions=[{"id": "V1", "blocks": [{"id": "B01", "lines": LINES}]},
                                    {"id": "V2", "blocks": [{"id": "B01", "lines": [{"who": "lin", "text": "我不认这个规矩，房子该分。"}]}]}],
                current="V1")
    S.save(spec)
    seen: dict = {}

    def fake(system_prompt, user_prompt, *, json_schema):
        seen["schema"] = json_schema
        seen["user"] = user_prompt
        return ({"summary": "整体偏解释", "items": [item(spec)]}, 0.02, 99, "settings")

    record = R.run_review(spec, dispatch=fake)
    assert seen["schema"] is R.REVIEW_JSON_SCHEMA and "你知道的" in seen["user"]
    assert record["version"] == "V1" and record["checklist_id"] == "craft-v1" and record["backend"] == "settings"
    path = R.review_path(spec)
    assert path.name == "review_V1.json" and path.is_file()
    assert not R.review_path(spec, "V2").is_file()
    assert R.load_review(spec)["version"] == "V1"
    assert R.load_review(spec, "V2") is None
    assert not R.is_stale(spec, record)


def test_review_goes_stale_when_that_version_changes(tmp_path):
    spec = make(tmp_path)
    S.save(spec)

    def fake(system_prompt, user_prompt, *, json_schema):
        return ({"summary": "s", "items": [item(spec)]}, None, 1, "settings")

    record = R.run_review(spec, dispatch=fake)
    assert not R.is_stale(spec, record)
    renamed = make(tmp_path, title="改了标题")
    assert not R.is_stale(renamed, record)              # 改标题不算
    edited = make(tmp_path, lines=[*LINES, {"who": "lin", "text": "我不认。"}])
    assert R.is_stale(edited, record)
    assert R.review_problems(edited, record) == []
    assert R.review_state(edited)["note"] == R.STALE_NOTE


def test_review_reads_the_latest_version_when_nothing_is_picked(tmp_path):
    """有版本但 current 是空的（第一版会自动采用，但整体保存时 current 可能被清空，契约 §3）：审的是最新那一版，不该报「没有版本」。"""

    spec = make(tmp_path, spec_id="SC-R2", current="")
    S.save(spec)
    record = R.run_review(spec, dispatch=lambda *a, **k: ({"summary": "s", "items": [item(spec)]}, None, 1, "settings"))
    assert record["version"] == "V1" and R.review_path(spec).name == "review_V1.json"
    assert R.review_state(spec)["version"] == "V1"


def test_run_review_needs_a_version_with_lines(tmp_path):
    empty = make(tmp_path, versions=[{"id": "V1", "blocks": []}], current="V1")
    with pytest.raises(R.ScriptReviewError, match="还没有台词"):
        R.run_review(empty, dispatch=lambda *a, **k: ({}, None, None, "settings"))
    none = make(tmp_path, versions=[], current="")
    with pytest.raises(R.ScriptReviewError, match="还没有任何版本"):
        R.run_review(none, dispatch=lambda *a, **k: ({}, None, None, "settings"))
    spec = make(tmp_path)
    with pytest.raises(R.ScriptReviewError, match="缺 items"):
        R.run_review(spec, dispatch=lambda *a, **k: ({"summary": "s"}, None, None, "settings"))


# ---- 5. 告警与问题卡


def test_warning_heads_and_problem_cards(tmp_path):
    spec = make(tmp_path)
    one = R.verify_review({"summary": "", "items": [item(spec)]}, spec)[0]["items"][0]
    text = R.review_warning(one, spec)
    assert text.startswith("block B01: 按工艺清单审稿（模型判断）：违反")
    assert "，原文「你知道的，我们家的规矩是长子继承」，理由「w」——建议「f」" in text
    card = problem_card(text)
    assert card["kind"] == "craft" and KIND_LABELS["craft"] == "工艺" and card["fix"] == "建议「f」"
    # 引了两段 / 没引段 → 全篇
    two = R.verify_review({"summary": "", "items": [item(spec, shots=("B01", "B02"))]}, spec)[0]["items"][0]
    assert R.review_warning(two, spec).startswith("全篇：按工艺清单审稿（模型判断）：")
    none = R.verify_review({"summary": "", "items": [item(spec, shots=())]}, spec)[0]["items"][0]
    assert R.review_warning(none, spec).startswith("全篇：")


def test_podcast_target_uses_the_humanlike_lead(tmp_path):
    spec = make(tmp_path, target="podcast")
    row = R.verify_review({"summary": "", "items": [item(spec)]}, spec)[0]["items"][0]
    text = R.review_warning(row, spec)
    assert text.startswith("block B01: 按真人感清单审稿（模型判断）：违反口语「")
    assert problem_card(text)["kind"] == "podcast"


def test_review_problems_only_keeps_verified_risk_and_violated(tmp_path):
    spec = make(tmp_path)
    S.save(spec)
    rows = [item(spec), item(spec, index=2, verdict="ok", shots=(), quote="", why="", fix=""),
            item(spec, index=3, verdict="risk", quote="这句稿子里根本没有")]

    def fake(system_prompt, user_prompt, *, json_schema):
        return ({"summary": "s", "items": rows}, None, None, "settings")

    record = R.run_review(spec, dispatch=fake)
    warnings = R.review_problems(spec, record)
    assert len(warnings) == 1 and warnings[0].startswith("block B01: ")
    state = R.review_state(spec)
    assert state["version"] == "V1" and state["stale"] is False and state["warnings"] == warnings
    assert GR.VERDICT_LABELS["violated"] == "违反"
