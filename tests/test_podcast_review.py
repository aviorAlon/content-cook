"""播客真人感审稿：随包清单的形状、审稿提示词、**引文核对闸门**（复用 genre_review.verify_items，环节当「场」）、过时判定、
接口与问题卡「播客」。

模型一律是替身（假 dispatch），这里验的是「模型说的话哪些能信」：引文要逐字落在所列环节的某一句台词 / 节拍 / 听众问题里，
环节号要存在、编号要对得上清单条目——对不上的降级、不进告警、或直接丢掉；标题与目标不算证据。
"""

from __future__ import annotations

import copy
import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from podcast import lint as L
from podcast import review as R
from podcast import spec as S
from storyvideo import genre_review as GR
from webui import deps, i18n, tasks
from webui.app import app
from webui.problems import KIND_LABELS, problem_card
from webui.routers import episodes as episodes_router
from webui.routers import podcast as podcast_router

client = TestClient(app)
API = "/api/podcast"
ROOT = Path(__file__).resolve().parents[1]
_HAN = re.compile(r"[一-鿿]")
_QUOTED = re.compile(r"「[^」]*」|《[^》]*》|\"[^\"]*\"")


def raw_spec(**extra) -> dict:
    data = {
        "title": "戒不掉短视频", "format": "duo", "target_minutes": 2,
        "brief": {"topic": "刷短视频停不下来", "materials": "小夏上周日均 3 小时 41 分；2025 年的调查说平均一次停 26 分钟"},
        "hosts": [{"id": "zhou", "name": "老周", "persona": "做了十年产品", "stance": "设计的锅"},
                  {"id": "xiao", "name": "小夏", "persona": "语速快", "stance": "自己的问题"}],
        "segments": [
            {"id": "G01", "kind": "cold_open", "title": "凌晨两点十七", "goal": "一个具体场景把人拽进来", "beats": ["小夏昨晚刷到两点多"],
             "lines": [{"who": "xiao", "text": "我昨天晚上看了眼时间，两点十七。"}, {"who": "zhou", "text": "又刷到两点？"}]},
            {"id": "G02", "kind": "topic", "title": "无限下滑", "goal": "", "beats": [],
             "lines": [{"who": "zhou", "text": "首先我们来看第一点，其次是第二点。"}, {"who": "xiao", "text": "对对对，你说得对。"}]},
            {"id": "G03", "kind": "qa", "title": "听友问答", "questions": [{"from": "听友阿May", "text": "把手机设成黑白真的有用吗？"}],
             "lines": [{"who": "zhou", "text": "阿May 问：把手机设成黑白真的有用吗？", "kind": "question"},
                       {"who": "xiao", "text": "有用。", "kind": "answer"}]},
        ],
    }
    data.update(extra)
    return data


def make_spec(root, **extra) -> dict:
    return S.normalize(raw_spec(**extra), project_root=root, spec_id="PC-R")


def checklist():
    return R.load_checklist()


def expected_item(source, index):
    return next(it for it in R.checklist_items(checklist()) if it["source"] == source and it["index"] == index)


def item(source="speech", index=1, verdict="violated", shots=("G02",), quote="首先我们来看第一点，其次是第二点", why="w", fix="f", **extra):
    return {"source": source, "index": index, "item": extra.pop("item", "模型抄的条目"), "verdict": verdict,
            "shots": list(shots), "quote": quote, "why": why, "fix": fix, **extra}


def verify(root, items, **spec_extra):
    spec = make_spec(root, **spec_extra)
    result, stats = R.verify_review({"summary": "s", "items": items}, spec)
    return spec, result, stats


def record(spec, result, stats):
    return {"checklist_id": checklist()["id"], "checklist_name": checklist()["name"],
            "spec_hash": R.review_hash(spec, checklist()["id"]), "result": result, "verification": stats}


# ---- 1. 清单文件

def test_checklist_has_three_groups_with_basis_and_low_confidence():
    data = json.loads((ROOT / "skills" / "ai-podcast" / "scripts" / "humanlike_checklist.json").read_text(encoding="utf-8"))
    assert data["id"] == "podcast-humanlike-v1" and data["name"] == "真人感清单" and data["confidence"] == "低"
    assert "podcast 模块" in data["basis"] and "没有拿真模型" in data["basis"] and "经验规则" in data["basis"]
    assert [g["key"] for g in data["groups"]] == list(R.GROUP_LABELS)
    assert {g["key"]: g["name"] for g in data["groups"]} == R.GROUP_LABELS
    for group in data["groups"]:
        assert 3 <= len(group["items"]) <= 4, group["key"]
        assert [it["index"] for it in group["items"]] == list(range(1, len(group["items"]) + 1)), group["key"]
        for it in group["items"]:
            assert it["text"].strip() and it["basis"].strip(), (group["key"], it["index"])
            assert "置信度低" in it["basis"] and "podcast 模块" in it["basis"], (group["key"], it["index"])
            assert "蒸馏" not in it["basis"] and "验证过" not in it["basis"]
            # 条目要能「读稿子 + 引一段原文」就判：每条都写了判 risk / violated 的条件与该引什么
            assert "判" in it["text"] and "引" in it["text"], (group["key"], it["index"])
    items = R.checklist_items(checklist())
    assert len(items) == 11 and items[0] == {**items[0], "source": "speech", "index": 1, "group": "口语"}
    assert R.checklist_info() == {"id": "podcast-humanlike-v1", "name": "真人感清单"}


# ---- 2. 正文与提示词

def test_material_lists_every_segment_with_kind_and_speaker_names(tmp_path):
    m = R.material(make_spec(tmp_path))
    assert list(m["segments"]) == ["G01", "G02", "G03"]
    assert m["segments"]["G01"]["kind"] == "冷开场"
    assert ("标题", "凌晨两点十七") in m["segments"]["G01"]["fields"] and ("目标", "一个具体场景把人拽进来") in m["segments"]["G01"]["fields"]
    assert ("节拍", "小夏昨晚刷到两点多") in m["segments"]["G01"]["fields"]
    assert ("台词（小夏）", "我昨天晚上看了眼时间，两点十七。") in m["segments"]["G01"]["fields"]
    assert ("听众问题（听友阿May）", "把手机设成黑白真的有用吗？") in m["segments"]["G03"]["fields"]
    labels = [label for label, _v in m["context"]]
    assert "节目" in labels and "主播" in labels and "选题" in labels and any(label.startswith("素材") for label in labels)
    assert ("主播", "老周（主播）；人设：做了十年产品；立场：设计的锅") in m["context"]


def test_prompt_numbers_every_checklist_item_and_lists_every_segment(tmp_path):
    spec = make_spec(tmp_path)
    system, user = R.build_review_prompt(spec, checklist())
    items = R.checklist_items(checklist())
    for key, label in R.GROUP_LABELS.items():
        assert f"{label}（{key}）：" in user
    for it in items:
        assert f"{it['index']}. {it['item']}" in user
    for sid, kind in (("G01", "冷开场"), ("G02", "话题"), ("G03", "听友问答")):
        assert f"## 环节 {sid}｜{kind}" in user
    assert "台词（老周）：首先我们来看第一点，其次是第二点。" in user
    assert "听众问题（听友阿May）：把手机设成黑白真的有用吗？" in user
    assert "全期（上下文，不能引用）：" in user and "2025 年的调查" in user and "立场：设计的锅" in user
    for rule in ("口语", "对谈动态", "内容", "speech", "dynamics", "substance", "quote", "环节号", "找不到能引用的原文就判 ok",
                 "不要编造", "不评", "不能当引文", "逐字核对"):
        assert rule in system
    assert "镜" not in system and "## 镜" not in user


# ---- 3. 核对闸门

def test_existing_quote_passes_and_checklist_text_replaces_model_copy(tmp_path):
    spec, result, stats = verify(tmp_path, [item(item="模型改写过的条目")])
    it = result["items"][0]
    assert it["evidence_verified"] is True and it["verdict"] == "violated"
    assert it["item"] == expected_item("speech", 1)["item"]
    assert stats == {"checked": 1, "verified": 1, "demoted": 0, "dropped": 0, "missing": 10}
    for quote in ("首先我们来看第一点 其次是第二点", "老周：首先我们来看第一点，其次是第二点。", "首先我们……其次是第二点"):
        _spec, again, _stats = verify(tmp_path, [item(quote=quote)])
        assert again["items"][0]["evidence_verified"] is True, quote
    # 节拍与听众问题也算证据
    _spec, beat, _ = verify(tmp_path, [item(shots=["G01"], quote="小夏昨晚刷到两点多")])
    assert beat["items"][0]["evidence_verified"] is True
    _spec, question, _ = verify(tmp_path, [item(source="substance", index=2, shots=["G03"], quote="把手机设成黑白真的有用吗")])
    assert question["items"][0]["evidence_verified"] is True
    # 没列环节号：全期找
    _spec, anywhere, _ = verify(tmp_path, [item(shots=[])])
    assert anywhere["items"][0]["evidence_verified"] is True


def test_fabricated_quote_is_demoted_and_never_becomes_a_warning(tmp_path):
    spec, result, stats = verify(tmp_path, [item(quote="我知道你上个月也刷到三点")])
    it = result["items"][0]
    assert it["evidence_verified"] is False and it["evidence_problem"] == "quote_not_found"
    assert it["verdict"] == "risk" and it["model_verdict"] == "violated"
    assert stats["demoted"] == 1 and stats["verified"] == 0
    assert R.review_problems(spec, record(spec, result, stats)) == []
    # 引文在别的环节里：不算
    _spec, wrong_segment, _ = verify(tmp_path, [item(shots=["G01"])])
    assert wrong_segment["items"][0]["evidence_problem"] == "quote_not_found"
    # 标题 / 目标 / 上下文（选题、素材、主播人设）不是说出口的话，不算证据
    for quote in ("凌晨两点十七", "一个具体场景把人拽进来", "刷短视频停不下来", "做了十年产品", "2025 年的调查说平均一次停 26 分钟"):
        _spec, not_evidence, _ = verify(tmp_path, [item(shots=[], quote=quote)])
        assert not_evidence["items"][0]["evidence_problem"] == "quote_not_found", quote
    _spec, short, _ = verify(tmp_path, [item(quote="首先")])
    assert short["items"][0]["evidence_problem"] == "quote_too_short"
    _spec, empty, _ = verify(tmp_path, [item(quote="")])
    assert empty["items"][0]["evidence_problem"] == "no_quote"


def test_two_adjacent_lines_can_be_quoted_together(tmp_path):
    """清单里「机械轮流」「各说各话」「分不清谁在说」几条要引相邻两句，「嗯 / 对」这类接话又不够 4 字：
    相邻两句按原顺序连着抄——直接拼、空格、省略号、换行、带说话人名——都算证据；不相邻、颠倒、跨环节的对不上。"""

    example = json.loads((ROOT / "examples" / "podcast" / "short_video_demo.json").read_text(encoding="utf-8"))
    spec = S.normalize(example, project_root=tmp_path, spec_id="PC-EX")
    g03 = spec["segments"][2]
    assert g03["id"] == "G03" and g03["lines"][0]["text"] == "你想过没有，为什么短视频没有底？" and g03["lines"][1]["text"] == "没有底？"

    def check(quote, shots=("G03",)):
        result, _stats = R.verify_review({"summary": "s", "items": [item(source="dynamics", index=3, verdict="risk", shots=shots, quote=quote)]}, spec)
        it = result["items"][0]
        return it["evidence_problem"] if it["evidence_verified"] is False else "ok"

    for quote in ("你想过没有，为什么短视频没有底？没有底？", "你想过没有，为什么短视频没有底？ 没有底？", "为什么短视频没有底？…没有底？",
                  "你想过没有，为什么短视频没有底？\n没有底？", "老周：你想过没有，为什么短视频没有底？\n小夏：没有底？",
                  "老周：你想过没有，为什么短视频没有底？ 小夏：没有底？"):
        assert check(quote) == "ok", quote
    # 接话本身太短仍不算，连着前一句就够了；收尾的「下期见。」同理
    assert check("没有底？") == "quote_too_short"
    assert check("下期见。", shots=["G06"]) == "quote_too_short"
    assert check("我们下期念。下期见。", shots=["G06"]) == "ok"
    # 不相邻 / 颠倒 / 跨环节的两句拼在一起：对不上
    assert check("你想过没有，为什么短视频没有底？对。你刷朋友圈") == "quote_not_found"
    assert check("没有底？你想过没有，为什么短视频没有底？") == "quote_not_found"
    assert check("我那是复发，性质不一样。好，这期就聊这个", shots=["G01", "G02"]) == "quote_not_found"
    # 只跨句界的碎片（前一句的尾巴 + 后一句的开头，去标点刚好 4 字）不算：既不是一句，也不是连着抄的两句
    assert check("有底没有") == "quote_not_found"
    result, stats = R.verify_review({"summary": "s", "items": [item(source="dynamics", index=3, verdict="violated", shots=["G03"], quote="有底没有")]}, spec)
    assert result["items"][0]["verdict"] == "risk" and result["items"][0]["model_verdict"] == "violated"
    assert stats["verified"] == 0 and stats["demoted"] == 1
    # 合句只给核对用：进提示词的正文与哈希不变
    assert all(label != "台词" for label, _v in R.material(spec)["segments"]["G03"]["fields"])
    pairs = [d for d in R._as_screenplay(spec)["scenes"][2]["dialogue"] if not d["who"]]
    assert len(pairs) == 2 * (len(g03["lines"]) - 1) and pairs[1]["zh"] == "老周：你想过没有，为什么短视频没有底？\n小夏：没有底？"


def test_checklist_wording_matches_the_gate_and_the_static_check():
    """条目让模型引的东西，闸门都得认：要引两句的都写明「相邻」「按原顺序连着抄」；接话太短的那条写明「不少于四个字」；
    提示词把「一句或相邻两句」与最短字数讲清；独白阈值跟 lint 用同一个数。"""

    items = R.checklist_items(checklist())
    # 「引…两句」才是让模型引两句（dynamics 1 的「展开了至少两句来回」是判 ok 的条件，不是引法）
    two_line_items = [it for it in items if re.search(r"引[^；。]*两句", it["item"])]
    assert {(it["source"], it["index"]) for it in two_line_items} == {("speech", 3), ("dynamics", 2), ("dynamics", 3)}
    for it in two_line_items:
        assert "相邻" in it["item"] and "按原顺序连着抄" in it["item"], (it["source"], it["index"])
    assert not any("连续两句" in it["item"] for it in items)
    dynamics4 = expected_item("dynamics", 4)["item"]
    assert "不少于四个字" in dynamics4 and "连起来引" in dynamics4 and "最长的一句" not in dynamics4
    speech2 = expected_item("speech", 2)
    assert L.MONOLOGUE_CHARS == 220 and f"约 {L.MONOLOGUE_CHARS} 字" in speech2["item"] and "与静态检查同一阈值" in speech2["item"]
    assert "200 字" not in speech2["item"] and f"MONOLOGUE_CHARS = {L.MONOLOGUE_CHARS}" in speech2["basis"]
    system, _user = R.build_review_prompt(raw_spec(), checklist())
    assert "相邻的两句台词" in system and f"至少 {GR.MIN_QUOTE_CHARS} 个字" in system and "连起来引" in system
    assert GR.MIN_QUOTE_CHARS == 4


def test_nonexistent_segment_id_is_demoted(tmp_path):
    spec, result, stats = verify(tmp_path, [item(shots=["G02", "G09"])])
    it = result["items"][0]
    assert it["evidence_verified"] is False and it["evidence_problem"] == "unknown_ids" and it["unknown_ids"] == ["G09"]
    assert it["verdict"] == "risk" and stats["demoted"] == 1
    assert R.review_problems(spec, record(spec, result, stats)) == []


def test_out_of_range_index_unknown_group_verdict_and_duplicates_are_dropped(tmp_path):
    items = [
        item(index=2, verdict="ok", shots=[], quote=""),
        item(index=2),                          # 同一条答了两次：留第一次
        item(index=5),                          # 口语组只有 4 条
        item(source="substance", index=4),      # 内容组只有 3 条
        item(source="dynamics", index=0),
        item(source="tension"),                 # 工艺清单的组不算
        item(source="pitfall", index=1),        # 题材卡的分类不算
        item(verdict="terrible"),
        item(index=True),
        item(source="dynamics", index="3", verdict="ok", shots=[], quote=""),
    ]
    _spec, result, stats = verify(tmp_path, items)
    assert [(it["source"], it["index"], it["verdict"]) for it in result["items"]] == [("speech", 2, "ok"), ("dynamics", 3, "ok")]
    assert stats["dropped"] == 8 and stats["checked"] == 0


def test_verified_items_become_segment_warnings_with_group_name_and_short_item(tmp_path):
    items = [
        item(source="dynamics", index=1, verdict="risk", shots=["G01", "G02"], quote="对对对，你说得对", why="反对——被收掉了", fix=""),
        item(why="整句是「分点列举」"),
        item(source="substance", index=3, verdict="ok", shots=[], quote=""),
        item(source="substance", index=2, verdict="risk", shots=[], quote="把手机设成黑白真的有用吗", why="", fix="先给结论"),
    ]
    spec, result, stats = verify(tmp_path, items)
    warnings = R.review_problems(spec, record(spec, result, stats))
    assert len(warnings) == 3
    speech1 = expected_item("speech", 1)["item"].split("。")[0]
    assert warnings[0] == (f"segment G02: 按真人感清单审稿（模型判断）：违反口语「{speech1}」，原文「首先我们来看第一点，其次是第二点」，"
                           "理由「整句是『分点列举』」——建议「f」")
    dynamics1 = expected_item("dynamics", 1)["item"].split("。")[0]
    assert warnings[1] == (f"全期：按真人感清单审稿（模型判断）：有风险对谈动态「{dynamics1}」（见 G01、G02），"
                           "原文「对对对，你说得对」，理由「反对，被收掉了」")
    substance2 = expected_item("substance", 2)["item"].split("。")[0].replace("「", "『").replace("」", "』")
    assert warnings[2] == f"全期：按真人感清单审稿（模型判断）：有风险内容「{substance2}」，原文「把手机设成黑白真的有用吗」——建议「先给结论」"
    first, second, third = (problem_card(w) for w in warnings)
    assert (first["kind"], first["scope"], first["target"]) == ("podcast", "segment", "G02")
    assert first["impact"].startswith("按真人感清单审稿（模型判断）：违反口语") and first["fix"] == "建议「f」"
    assert (second["kind"], second["scope"], second["fix"]) == ("podcast", "episode", "")
    assert (third["kind"], third["scope"], third["fix"]) == ("podcast", "episode", "建议「先给结论」")
    assert KIND_LABELS["podcast"] == "播客"


def test_drama_words_in_the_model_reason_do_not_change_the_kind(tmp_path):
    """模型写的理由里会出现「台词」「时长」「镜头」这些别的分类的词，句头「按真人感清单审稿」要把卡钉在「播客」。"""

    items = [item(why="这句台词像镜头脚本里的旁白，时长也太长", fix="改成半句加接话"),
             item(source="dynamics", index=4, verdict="risk", shots=["G02"], quote="对对对，你说得对", why="表演成分大于内容，像形态卡里的模板", fix="")]
    spec, result, stats = verify(tmp_path, items)
    warnings = R.review_problems(spec, record(spec, result, stats))
    assert len(warnings) == 2 and [problem_card(w)["kind"] for w in warnings] == ["podcast", "podcast"]


def test_review_goes_stale_when_script_context_or_checklist_changes(tmp_path, monkeypatch):
    spec, result, stats = verify(tmp_path, [item()])
    rec = record(spec, result, stats)
    assert not R.is_stale(spec, rec) and R.review_problems(spec, rec)
    # 改一句台词
    edited_raw = raw_spec()
    edited_raw["segments"][1]["lines"][0]["text"] = "你想过没有，为什么短视频没有底？"
    edited = S.normalize(edited_raw, project_root=tmp_path, spec_id="PC-R")
    assert R.is_stale(edited, rec) and R.review_problems(edited, rec) == []
    # 改素材 / 主播立场：审稿看过的上下文变了，也算过时
    materials = S.normalize(raw_spec(brief={"topic": "刷短视频停不下来", "materials": "换了一份素材"}), project_root=tmp_path, spec_id="PC-R")
    assert R.is_stale(materials, rec)
    # 没审的东西（音乐、停顿、音色）改了不算过时
    untouched = S.normalize(raw_spec(music={"bed_gain": 0.3}, pause={"line": 0.5}), project_root=tmp_path, spec_id="PC-R")
    untouched["hosts"][0]["voice"] = "lib"
    assert not R.is_stale(untouched, rec)
    # 清单换了版本：旧审稿过时
    assert R.is_stale(spec, {**rec, "checklist_id": "podcast-humanlike-v0"})
    monkeypatch.setattr(R, "CHECKLIST_PATH", tmp_path / "missing.json")
    assert R.is_stale(spec, rec) and R.review_problems(spec, rec) == []
    assert R.review_state(spec)["note"] == R.MISSING_CHECKLIST_MESSAGE


def test_review_state_reads_the_saved_file_and_separates_the_two_notes(tmp_path, monkeypatch):
    spec, result, stats = verify(tmp_path, [item()])
    rec = record(spec, result, stats)
    assert R.review_state(spec) == {"review": None, "stale": False, "note": "", "warnings": []}
    R.review_path(spec).parent.mkdir(parents=True, exist_ok=True)
    R.review_path(spec).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    ok = R.review_state(spec)
    assert ok["stale"] is False and ok["note"] == "" and ok["warnings"] and ok["review"]["spec_hash"] == rec["spec_hash"]
    monkeypatch.setattr(R, "CHECKLIST_PATH", tmp_path / "missing.json")
    missing = R.review_state(spec)
    assert missing["stale"] is True and missing["note"] == R.MISSING_CHECKLIST_MESSAGE and missing["warnings"] == []
    assert missing["note"] != R.STALE_NOTE
    R.review_path(spec).unlink()
    assert R.review_state(spec) == {"review": None, "stale": False, "note": R.MISSING_CHECKLIST_MESSAGE, "warnings": []}


def test_failed_checklist_read_is_not_cached(tmp_path, monkeypatch):
    path = tmp_path / "late.json"
    monkeypatch.setattr(R, "CHECKLIST_PATH", path)
    assert R.checklist_info() is None
    path.write_text(json.dumps({"id": "podcast-late", "name": "补上的清单", "groups": []}, ensure_ascii=False), encoding="utf-8")
    assert R.checklist_info() == {"id": "podcast-late", "name": "补上的清单"}
    path.write_text("{", encoding="utf-8")
    assert R.checklist_info() == {"id": "podcast-late", "name": "补上的清单"}


def test_verify_is_the_shared_gate_with_segments_as_scenes(tmp_path):
    """真人感审稿的 verify_review 是 genre_review.verify_items 的薄壳：环节当「场」传进去，同一份答案两边判法一致。"""

    spec = make_spec(tmp_path)
    answer = {"summary": "s", "items": [item(), item(source="dynamics", index=1, shots=["G09"])]}
    via_review, stats_review = R.verify_review(answer, spec)
    via_items, stats_items = GR.verify_items(answer, {}, R._as_screenplay(spec), R.checklist_items(checklist()))
    assert via_review == via_items and stats_review == stats_items
    assert via_review["items"][0]["evidence_verified"] is True and via_review["items"][1]["evidence_problem"] == "unknown_ids"


def test_run_review_writes_the_record_with_a_fake_model(tmp_path):
    spec = make_spec(tmp_path)
    seen = {}

    def dispatch(system_prompt, user_prompt, *, json_schema):
        seen.update(system=system_prompt, user=user_prompt, schema=json_schema)
        return {"summary": "像 AI 稿", "items": [item(), item(index=99)]}, 0.01, 321, "settings"

    rec = R.run_review(spec, dispatch=dispatch)
    assert seen["schema"] is R.REVIEW_JSON_SCHEMA and "## 环节 G02｜话题" in seen["user"]
    assert rec["checklist_id"] == "podcast-humanlike-v1" and rec["backend"] == "settings" and rec["usage_tokens"] == 321
    assert rec["verification"] == {"checked": 1, "verified": 1, "demoted": 0, "dropped": 1, "missing": 10}
    assert rec["spec_hash"] == R.review_hash(spec, "podcast-humanlike-v1")
    saved = json.loads(R.review_path(spec).read_text(encoding="utf-8"))
    assert saved["result"]["summary"] == "像 AI 稿" and R.review_path(spec).name == "humanlike_review.json"
    with pytest.raises(R.HumanlikeReviewError, match="缺 items"):
        R.run_review(spec, dispatch=lambda *a, **k: ({"summary": "没问题"}, None, 0, "settings"))


# ---- 4. 接口

@pytest.fixture
def project(tmp_path, monkeypatch):
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", tmp_path / "content" / "story_video")
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", tmp_path / "content" / "story_video")
    monkeypatch.setattr(tasks, "tasks_dir", lambda: tmp_path / ".webui_tasks")
    monkeypatch.setattr(podcast_router, "VOICE_LOOKUP", lambda vid: None)
    monkeypatch.setattr(podcast_router, "DISPATCH", None)
    return tmp_path


def wait(task_id: str, seconds: float = 10.0) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        task = tasks.get(task_id)
        if task and task["status"] in ("succeeded", "failed"):
            return task
        time.sleep(0.02)
    raise AssertionError("task did not finish")


def fake_dispatch(monkeypatch, answer, gate=None):
    seen = {}

    def _fake(system_prompt, user_prompt, *, json_schema):
        seen.update(system=system_prompt, user=user_prompt, schema=json_schema, calls=seen.get("calls", 0) + 1)
        if gate is not None:
            gate.wait(10)
        return copy.deepcopy(answer), None, 321, "settings"

    monkeypatch.setattr(podcast_router, "DISPATCH", _fake)
    return seen


def create(**extra) -> str:
    r = client.post(API, json=raw_spec(**extra))
    assert r.status_code == 200, r.text
    return r.json()["id"]


ANSWER = {"summary": "一处书面语", "items": [item(), item(source="dynamics", index=1, quote="我知道你上个月也刷到三点"), item(index=99)]}


def test_post_rejects_empty_script_missing_model_and_missing_checklist(project, monkeypatch):
    from storyvideo import scriptgen

    pid = create()
    url = f"{API}/{pid}/humanlike-review"
    got = client.get(url).json()
    assert got["checklist"] == {"id": "podcast-humanlike-v1", "name": "真人感清单"}
    assert got["review"] is None and got["stale"] is False and got["problems"] == [] and got["task_id"] is None
    assert got["note"] == "" and got["cost_note"] == "发 1 次文本模型请求"
    monkeypatch.setattr(scriptgen, "scriptgen_backend", lambda: "unconfigured")   # 真函数没配时返回的就是这个值，不是 ""
    r = client.post(url)
    assert r.status_code == 400 and "文本模型" in r.json()["detail"]
    fake_dispatch(monkeypatch, ANSWER)
    monkeypatch.setattr(R, "CHECKLIST_PATH", project / "missing.json")
    r = client.post(url)
    assert r.status_code == 400 and r.json()["detail"] == R.MISSING_CHECKLIST_MESSAGE
    assert client.get(url).json()["checklist"] is None
    empty = create(segments=[])
    r = client.post(f"{API}/{empty}/humanlike-review")
    assert r.status_code == 400 and r.json()["detail"] == R.NO_LINES_MESSAGE
    assert client.post(f"{API}/NOPE/humanlike-review").status_code == 404
    assert client.get(f"{API}/NOPE/humanlike-review").status_code == 404


def test_review_task_writes_record_and_get_returns_verified_problem_cards(project, monkeypatch):
    pid = create()
    url = f"{API}/{pid}/humanlike-review"
    seen = fake_dispatch(monkeypatch, ANSWER)
    r = client.post(url)
    assert r.status_code == 200, r.text
    assert r.json()["cost_note"] == "发 1 次文本模型请求"
    task = wait(r.json()["task_id"])
    assert task["status"] == "succeeded", task
    assert task["kind"] == "pc-review" and task["module"] == "podcast" and task["params"] == {"checklist": "podcast-humanlike-v1"}
    assert task["result"]["verification"] == {"checked": 2, "verified": 1, "demoted": 1, "dropped": 1, "missing": 9}
    assert task["result"]["counts"] == {"violated": 1, "risk": 0, "ok": 0, "unverified": 1}
    assert task["result"]["checklist_id"] == "podcast-humanlike-v1"
    assert seen["calls"] == 1 and seen["schema"] is R.REVIEW_JSON_SCHEMA and "## 环节 G02｜话题" in seen["user"]

    saved = json.loads((project / "output" / "podcast" / pid / "manifests" / "humanlike_review.json").read_text(encoding="utf-8"))
    for key in ("checklist_id", "checklist_name", "model", "backend", "created_at", "spec_hash", "result", "verification"):
        assert key in saved
    assert saved["checklist_name"] == "真人感清单" and saved["backend"] == "settings"

    got = client.get(url).json()
    assert set(got) == {"checklist", "review", "stale", "note", "problems", "cost_note", "task_id"}
    assert got["stale"] is False and got["note"] == "" and got["task_id"] is None
    assert got["review"]["spec_hash"] == saved["spec_hash"]
    assert [(c["kind"], c["scope"], c["target"]) for c in got["problems"]] == [("podcast", "segment", "G02")]
    assert got["problems"][0]["impact"].startswith("按真人感清单审稿（模型判断）：违反口语")
    # 详情的问题卡：静态检查的 + 审稿的一起
    detail = client.get(f"{API}/{pid}").json()
    review_cards = [c for c in detail["problems"] if c["impact"].startswith("按真人感清单审稿")]
    assert [c["target"] for c in review_cards] == ["G02"] and len(detail["problems"]) > 1
    assert detail["busy"]["review"] is None
    en = client.get(url, headers={"X-StoryStudio-Lang": "en"}).json()
    assert en["problems"][0]["impact"].startswith("Humanlike checklist review (model judgement): violates spoken language")


def test_editing_the_script_marks_the_review_stale(project, monkeypatch):
    pid = create()
    url = f"{API}/{pid}/humanlike-review"
    fake_dispatch(monkeypatch, ANSWER)
    assert wait(client.post(url).json()["task_id"])["status"] == "succeeded"
    spec = client.get(f"{API}/{pid}").json()["spec"]
    spec["segments"][1]["lines"][0]["text"] = "你想过没有，为什么短视频没有底？"
    assert client.put(f"{API}/{pid}", json=spec).status_code == 200
    got = client.get(url).json()
    assert got["stale"] is True and got["note"] == "稿子改过，这份审稿可能过时——重新审一次"
    assert got["problems"] == [] and got["review"] is not None
    detail = client.get(f"{API}/{pid}").json()
    assert not any(c["impact"].startswith("按真人感清单审稿") for c in detail["problems"])
    en = client.get(url, headers={"X-StoryStudio-Lang": "en"}).json()
    assert en["note"].startswith("The script changed after this review")


def test_second_post_while_running_is_409(project, monkeypatch):
    pid = create()
    url = f"{API}/{pid}/humanlike-review"
    gate = threading.Event()
    fake_dispatch(monkeypatch, ANSWER, gate=gate)
    first = client.post(url)
    assert first.status_code == 200, first.text
    try:
        second = client.post(url)
        assert second.status_code == 409 and "已经在跑" in second.json()["detail"]
        assert client.get(url).json()["task_id"] == first.json()["task_id"]
        assert client.get(f"{API}/{pid}").json()["busy"]["review"] == first.json()["task_id"]
    finally:
        gate.set()
    assert wait(first.json()["task_id"])["status"] == "succeeded"


def test_model_without_items_fails_the_task(project, monkeypatch):
    pid = create()
    url = f"{API}/{pid}/humanlike-review"
    fake_dispatch(monkeypatch, {"summary": "看起来不错"})
    task = wait(client.post(url).json()["task_id"])
    assert task["status"] == "failed" and "模型没有按审稿结构返回（缺 items 列表）" in task["error"]
    assert client.get(url).json()["review"] is None


def test_local_text_role_gets_the_free_cost_note(project, monkeypatch):
    from genkit import local as local_mod

    pid = create()
    url = f"{API}/{pid}/humanlike-review"
    fake_dispatch(monkeypatch, ANSWER)
    monkeypatch.setattr(local_mod, "role_local", lambda role, settings=None: role == "text")
    r = client.post(url)
    assert r.json()["cost_note"] == "本地生成，不花钱（1 次请求）"
    wait(r.json()["task_id"])
    assert client.get(url).json()["cost_note"] == "本地生成，不花钱（1 次请求）"


# ---- 5. 英文

def test_problem_cards_translate_fully(tmp_path):
    items = [
        item(),
        item(source="dynamics", index=1, verdict="risk", shots=["G01", "G02"], quote="对对对，你说得对", why="", fix=""),
        item(source="substance", index=1, verdict="risk", shots=["G02"], quote="对对对，你说得对", why="整段没有一件带数字的事", fix="从素材里拿一件事讲细"),
        item(source="substance", index=2, verdict="violated", shots=[], quote="把手机设成黑白真的有用吗", why="只有结论没有原因", fix="再讲一个为什么"),
    ]
    spec, result, stats = verify(tmp_path, items)
    warnings = R.review_problems(spec, record(spec, result, stats))
    assert len(warnings) == 4
    i18n._compiled = None
    for text in warnings:
        out = i18n.tr(text, "en")
        assert out != text and not _HAN.search(_QUOTED.sub("", out)), out
        card_ = problem_card(text)
        assert card_["kind"] == "podcast"
        for half in (card_["impact"], card_["fix"]):
            if half:
                got = i18n.tr(half, "en")
                assert got != half and not _HAN.search(_QUOTED.sub("", got)), (half, got)
    assert i18n.tr(warnings[0], "en").startswith("segment G02: Humanlike checklist review (model judgement): violates spoken language")
    assert i18n.tr(warnings[1], "en").startswith("Whole episode: Humanlike checklist review (model judgement): violates substance")
    assert i18n.tr(warnings[2], "en").startswith("Whole episode: Humanlike checklist review (model judgement): is at risk on conversation dynamics")
    assert i18n.tr(warnings[3], "en").startswith("segment G02: Humanlike checklist review (model judgement): is at risk on substance")
    assert i18n.tr(R.MISSING_CHECKLIST_MESSAGE, "en").startswith("The humanlike checklist file could not be read")
    assert i18n.tr(R.NO_LINES_MESSAGE, "en").startswith("The script has no lines yet")
    assert i18n.tr(R.STALE_NOTE, "en").startswith("The script changed after this review")
