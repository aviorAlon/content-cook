"""剧本工艺审稿：随包清单的形状、审稿提示词、**引文核对闸门**（复用 genre_review.verify_items）、过时判定、接口与问题卡「工艺」。

模型一律是替身（假 ``scriptgen._dispatch``），这里验的是「模型说的话哪些能信」：引文要逐字落在所列镜 / 场的某个字段里、
镜号要存在、编号要对得上清单条目——对不上的降级、不进告警、或直接丢掉。项目不选题材也能审。
"""

import copy
import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import craft_review as CR
from storyvideo import genre_review as GR
from storyvideo import scriptgen
from storyvideo.spec import normalize_spec
from webui import deps, i18n
from webui.app import app
from webui.problems import KIND_LABELS, problem_card
from webui.routers import episodes as episodes_router
from webui.routers import project as project_router

client = TestClient(app)
EID = "CR1"
URL = f"/api/episodes/storyvideo/{EID}/craft-review"
ROOT = Path(__file__).resolve().parents[1]
_HAN = re.compile(r"[一-鿿]")
_QUOTED = re.compile(r"「[^」]*」|《[^》]*》|\"[^\"]*\"")


def shot(sid, motion, zh=None):
    lines = [{"who": "kid", "zh": zh, "en": "a line"}] if zh else []
    return {"id": sid, "dur": 5, "cast": ["kid"], "shot": "近景", "art": "客厅，傍晚", "motion": motion, "lines": lines}


def raw_spec(**extra):
    # 故意不写 genre：工艺审稿跟题材无关
    data = {
        "id": EID, "output_root": f"output/story_video/{EID}", "title_zh": "体检单",
        "cast": {"kid": {"block": "八岁男孩，蓝色校服"}},
        "episode_contract": {"premise": "男孩要让爸爸在手术同意书上签字", "hook": "男孩把体检单塞进书包"},
        "shots": [shot("S01", "男孩抱住妈妈", "没事，我早忘了。"), shot("S02", "两人抱在一起，镜头拉远")],
    }
    data.update(extra)
    return {k: v for k, v in data.items() if v is not None}


def checklist():
    return CR.load_checklist()


def expected_item(source, index):
    return next(it for it in CR.checklist_items(checklist()) if it["source"] == source and it["index"] == index)


def item(source="emotion", index=2, verdict="violated", shots=("S01",), quote="没事，我早忘了", why="w", fix="f", **extra):
    return {"source": source, "index": index, "item": extra.pop("item", "模型抄的条目"), "verdict": verdict,
            "shots": list(shots), "quote": quote, "why": why, "fix": fix, **extra}


def verify(root, items, screenplay=None, **spec_extra):
    spec = normalize_spec(raw_spec(**spec_extra), project_root=root)
    result, stats = CR.verify_review({"summary": "s", "items": items}, spec, screenplay)
    return spec, result, stats


def record(spec, result, stats, screenplay=None):
    return {"checklist_id": checklist()["id"], "checklist_name": checklist()["name"],
            "spec_hash": CR.review_hash(spec, checklist()["id"], screenplay), "result": result, "verification": stats}


# ---- 1. 清单文件

def test_checklist_has_four_groups_of_four_items_with_basis_and_confidence():
    data = json.loads((ROOT / "skills" / "ai-short-film" / "scripts" / "craft_checklist.json").read_text(encoding="utf-8"))
    assert data["id"] == "craft-v1" and data["name"] == "剧本工艺清单" and data["confidence"] == "低"
    assert "自拟" in data["basis"] and "没有拿真模型" in data["basis"]
    assert [g["key"] for g in data["groups"]] == list(CR.GROUP_LABELS)
    assert {g["key"]: g["name"] for g in data["groups"]} == CR.GROUP_LABELS
    for group in data["groups"]:
        assert [it["index"] for it in group["items"]] == [1, 2, 3, 4], group["key"]
        for it in group["items"]:
            assert it["text"].strip() and it["basis"].strip(), (group["key"], it["index"])
            assert "置信度" in it["basis"], (group["key"], it["index"])
            assert "蒸馏" not in it["basis"] and "验证过" not in it["basis"] and "草稿" not in it["basis"]
    items = CR.checklist_items(checklist())
    assert len(items) == 16 and items[0] == {**items[0], "source": "tension", "index": 1, "group": "张力"}
    assert CR.checklist_info() == {"id": "craft-v1", "name": "剧本工艺清单"}


def test_item_label_keeps_the_first_sentence_and_shortens_long_ones():
    assert CR.item_label("集末要收在悬而未决处：问题刚被抛出来、人物还没做选择，画面就断。结尾那几镜已经把本集的矛盾了结") == "集末要收在悬而未决处：问题刚被抛出来、人物还没做选择，画面就断"
    tension1 = CR.item_label(expected_item("tension", 1)["item"])
    assert tension1.endswith("…") and len(tension1) <= CR.ITEM_LABEL_MAX + 1
    assert tension1.startswith("每场戏里都得有人想要一样东西、而另一个人")
    assert tension1.count("（") == tension1.count("）")
    compliance1 = CR.item_label(expected_item("compliance", 1)["item"])
    assert compliance1.endswith("…") and compliance1.count("（") == compliance1.count("）")
    assert CR.item_label("") == ""


# ---- 2. 提示词

def test_prompt_numbers_every_checklist_item_and_lists_every_shot(tmp_path):
    spec = normalize_spec(raw_spec(), project_root=tmp_path)
    system, user = CR.build_review_prompt(spec, checklist())
    items = CR.checklist_items(checklist())
    for key in CR.GROUP_LABELS:
        assert sum(1 for it in items if it["source"] == key) == 4
        assert f"{CR.GROUP_LABELS[key]}（{key}）：" in user
    for it in items:
        assert f"{it['index']}. {it['item']}" in user
    for sid in ("S01", "S02"):
        assert f"## 镜 {sid}" in user
    assert "没事，我早忘了。" in user and "两人抱在一起，镜头拉远" in user and "体检单" in user
    assert "男孩要让爸爸在手术同意书上签字" in user          # 契约进审稿正文
    for rule in ("张力", "情绪可见", "节奏", "合规", "quote", "找不到能引用的原文就判 ok", "不要编造", "不评", "一票否决", "哪一类红线"):
        assert rule in system
    assert "题材" in system and "无关" in system            # 说清跟题材无关
    assert "## 场" not in user
    screenplay = {"scenes": [{"id": "SC01", "heading": "内 · 医院走廊 · 夜", "beats": ["男孩偷偷签下爸爸的名字"],
                              "dialogue": [{"who": "kid", "zh": "我替你签"}]}]}
    _system, with_screenplay = CR.build_review_prompt(spec, checklist(), screenplay)
    assert "## 场 SC01" in with_screenplay and "男孩偷偷签下爸爸的名字" in with_screenplay


# ---- 3. 核对闸门

def test_existing_quote_passes_and_checklist_text_replaces_model_copy(tmp_path):
    spec, result, stats = verify(tmp_path, [item(item="模型改写过的条目")])
    it = result["items"][0]
    assert it["evidence_verified"] is True and it["verdict"] == "violated"
    assert it["item"] == expected_item("emotion", 2)["item"]
    assert stats == {"checked": 1, "verified": 1, "demoted": 0, "dropped": 0, "missing": 15}
    for quote in ("没事 我早忘了", "台词（kid）：没事，我早忘了。", "没事……早忘了"):
        _spec, again, _stats = verify(tmp_path, [item(quote=quote)])
        assert again["items"][0]["evidence_verified"] is True, quote


def test_fabricated_quote_is_demoted_and_never_becomes_a_warning(tmp_path):
    spec, result, stats = verify(tmp_path, [item(quote="我知道爸爸的秘密")])
    it = result["items"][0]
    assert it["evidence_verified"] is False and it["evidence_problem"] == "quote_not_found"
    assert it["verdict"] == "risk" and it["model_verdict"] == "violated"
    assert stats["demoted"] == 1 and stats["verified"] == 0
    assert CR.review_problems(spec, record(spec, result, stats)) == []
    _spec, wrong_shot, _ = verify(tmp_path, [item(shots=["S02"])])
    assert wrong_shot["items"][0]["evidence_problem"] == "quote_not_found"
    _spec, short, _ = verify(tmp_path, [item(quote="没事")])
    assert short["items"][0]["evidence_problem"] == "quote_too_short"
    _spec, empty, _ = verify(tmp_path, [item(quote="")])
    assert empty["items"][0]["evidence_problem"] == "no_quote"


def test_nonexistent_shot_id_is_demoted(tmp_path):
    spec, result, stats = verify(tmp_path, [item(shots=["S01", "S09"])])
    it = result["items"][0]
    assert it["evidence_verified"] is False and it["evidence_problem"] == "unknown_ids" and it["unknown_ids"] == ["S09"]
    assert it["verdict"] == "risk" and stats["demoted"] == 1
    assert CR.review_problems(spec, record(spec, result, stats)) == []


def test_out_of_range_index_unknown_group_verdict_and_duplicates_are_dropped(tmp_path):
    items = [
        item(index=1, verdict="ok", shots=[], quote=""),
        item(index=1),                          # 同一条答了两次：留第一次
        item(index=5),                          # 情绪可见组只有 4 条
        item(source="tension", index=0),
        item(source="vibe"),                    # 不是清单里的组
        item(source="pitfall", index=1),        # 题材卡的分类不算
        item(verdict="terrible"),
        item(index=True),
        item(source="rhythm", index="3", verdict="ok", shots=[], quote=""),
    ]
    _spec, result, stats = verify(tmp_path, items)
    assert [(it["source"], it["index"], it["verdict"]) for it in result["items"]] == [("emotion", 1, "ok"), ("rhythm", 3, "ok")]
    assert stats["dropped"] == 7 and stats["checked"] == 0


def test_verified_items_become_warnings_with_group_name_and_short_item(tmp_path):
    items = [
        item(source="tension", index=3, verdict="risk", shots=["S01", "S02"], quote="两人抱在一起，镜头拉远", why="收掉了——没钩子", fix=""),
        item(why="台词「没事」，动作栏是抱住"),
        item(source="compliance", index=1, verdict="ok", shots=[], quote=""),
    ]
    spec, result, stats = verify(tmp_path, items)
    warnings = CR.review_problems(spec, record(spec, result, stats))
    assert len(warnings) == 2
    emotion2 = CR.item_label(expected_item("emotion", 2)["item"]).replace("「", "『").replace("」", "』")
    assert warnings[0] == (f"shot S01: 按工艺清单审稿（模型判断）：违反情绪可见「{emotion2}」，原文「没事，我早忘了」，"
                           "理由「台词『没事』，动作栏是抱住」——建议「f」")
    assert warnings[1] == ("全集：按工艺清单审稿（模型判断）：有风险张力「集末要收在悬而未决处：问题刚被抛出来、人物还没做选择，画面就断」（见 S01、S02），"
                           "原文「两人抱在一起，镜头拉远」，理由「收掉了，没钩子」")
    first, second = problem_card(warnings[0]), problem_card(warnings[1])
    assert (first["kind"], first["scope"], first["target"]) == ("craft", "shot", "S01")
    assert first["impact"].startswith("按工艺清单审稿（模型判断）：违反情绪可见") and first["fix"] == "建议「f」"
    assert (second["kind"], second["scope"], second["fix"]) == ("craft", "episode", "")


def test_podcast_words_in_the_model_reason_do_not_change_the_kind(tmp_path):
    """张力组的理由几乎必然谈「分歧」，播客那组关键词不能把工艺卡抢走（KINDS 里工艺 / 题材排在播客前面）。"""

    items = [item(source="tension", index=1, verdict="violated", why="两人的分歧只在嘴上", fix="改"),
             item(source="rhythm", index=4, verdict="risk", why="每个环节都靠主播式的播音腔推进", fix="换一种钩子")]
    spec, result, stats = verify(tmp_path, items)
    warnings = CR.review_problems(spec, record(spec, result, stats))
    assert len(warnings) == 2 and "分歧" in warnings[0] and "播音腔" in warnings[1]
    assert [problem_card(w)["kind"] for w in warnings] == ["craft", "craft"]


def test_screenplay_scene_quote_verifies_and_scopes_to_the_scene(tmp_path):
    screenplay = {"scenes": [{"id": "SC01", "heading": "内 · 医院走廊 · 夜", "beats": ["男孩偷偷签下爸爸的名字"], "dialogue": []}]}
    spec, result, stats = verify(tmp_path, [item(source="rhythm", index=3, shots=["SC01"], quote="男孩偷偷签下爸爸的名字")], screenplay)
    assert result["items"][0]["evidence_verified"] is True
    warnings = CR.review_problems(spec, record(spec, result, stats, screenplay), screenplay)
    assert warnings[0].startswith("scene SC01: 按工艺清单审稿（模型判断）：违反节奏「")
    _spec, without, _ = verify(tmp_path, [item(shots=["SC01"], quote="男孩偷偷签下爸爸的名字")])
    assert without["items"][0]["evidence_problem"] == "unknown_ids"


def test_review_goes_stale_when_reviewed_text_or_checklist_changes(tmp_path, monkeypatch):
    spec, result, stats = verify(tmp_path, [item()])
    rec = record(spec, result, stats)
    assert not CR.is_stale(spec, rec) and CR.review_problems(spec, rec)
    edited = normalize_spec(raw_spec(shots=[shot("S01", "男孩推开妈妈", "没事，我早忘了。"), shot("S02", "两人抱在一起，镜头拉远")]),
                            project_root=tmp_path)
    assert CR.is_stale(edited, rec) and CR.review_problems(edited, rec) == []
    # 没审的东西（负向词、题材）改了不算过时
    untouched = normalize_spec(raw_spec(neg="水印", genre="qinzi-yinmi"), project_root=tmp_path)
    assert not CR.is_stale(untouched, rec)
    # 清单换了版本：旧审稿过时
    assert CR.is_stale(spec, {**rec, "checklist_id": "craft-v0"})
    monkeypatch.setattr(CR, "CHECKLIST_PATH", tmp_path / "missing.json")
    assert CR.is_stale(spec, rec) and CR.review_problems(spec, rec) == []
    # 清单读不出来：横幅说的是清单的事，不是「剧本改过」
    assert CR.review_state(spec)["note"] == CR.MISSING_CHECKLIST_MESSAGE


def test_missing_checklist_note_is_not_the_stale_note(tmp_path, monkeypatch):
    """清单读不出来时 review_state 的 note 是「清单文件读不出来」，跟「剧本改过」分得开；没审过也一样提示。"""

    spec, result, stats = verify(tmp_path, [item()])
    rec = record(spec, result, stats)
    (tmp_path / "manifests").mkdir(parents=True, exist_ok=True)
    CR.review_path(spec).parent.mkdir(parents=True, exist_ok=True)
    CR.review_path(spec).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    ok = CR.review_state(spec)
    assert ok["stale"] is False and ok["note"] == "" and ok["warnings"]
    monkeypatch.setattr(CR, "CHECKLIST_PATH", tmp_path / "missing.json")
    missing = CR.review_state(spec)
    assert missing["stale"] is True and missing["note"] == CR.MISSING_CHECKLIST_MESSAGE and missing["warnings"] == []
    assert missing["note"] != CR.STALE_NOTE
    CR.review_path(spec).unlink()
    assert CR.review_state(spec) == {"review": None, "stale": False, "note": CR.MISSING_CHECKLIST_MESSAGE, "warnings": []}


def test_failed_checklist_read_is_not_cached(tmp_path, monkeypatch):
    """第一次读清单失败（文件被占用 / 还没修好）不能记一辈子：文件补上后下一次就能读到。"""

    path = tmp_path / "late.json"
    monkeypatch.setattr(CR, "CHECKLIST_PATH", path)
    assert CR.checklist_info() is None
    path.write_text(json.dumps({"id": "craft-late", "name": "补上的清单", "groups": []}, ensure_ascii=False), encoding="utf-8")
    assert CR.checklist_info() == {"id": "craft-late", "name": "补上的清单"}
    # 读成功之后才缓存：文件再坏掉也还拿得到上一次读到的
    path.write_text("{", encoding="utf-8")
    assert CR.checklist_info() == {"id": "craft-late", "name": "补上的清单"}


def test_genre_review_still_uses_the_shared_gate(tmp_path):
    """题材审稿的 verify_review 现在是 verify_items 的薄壳：同一份答案两边判法一致。"""

    spec = normalize_spec(raw_spec(genre="qinzi-yinmi"), project_root=tmp_path)
    answer = {"summary": "s", "items": [{"source": "pitfall", "index": 1, "item": "x", "verdict": "violated",
                                          "shots": ["S01"], "quote": "没事，我早忘了", "why": "w", "fix": "f"}]}
    via_card, stats_card = GR.verify_review(answer, spec)
    via_items, stats_items = GR.verify_items(answer, spec, None, GR.card_items(GR.spec_card(spec)))
    assert via_card == via_items and stats_card == stats_items and via_card["items"][0]["evidence_verified"] is True


# ---- 4. 接口

def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content = tmp_path / "content" / "story_video"
    content.mkdir(parents=True, exist_ok=True)
    for module in (deps, episodes_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content)
    return tmp_path


@pytest.fixture
def project(tmp_path, monkeypatch):
    return _isolate(tmp_path, monkeypatch)


def write(root: Path, **extra) -> Path:
    path = root / "content" / "story_video" / f"{EID}.json"
    path.write_text(json.dumps(raw_spec(**extra), ensure_ascii=False), encoding="utf-8")
    return path


def fake_dispatch(monkeypatch, answer, gate=None):
    seen = {}

    def _fake(system_prompt, user_prompt, *, json_schema):
        seen.update(system=system_prompt, user=user_prompt, schema=json_schema, calls=seen.get("calls", 0) + 1)
        if gate is not None:
            gate.wait(10)
        return copy.deepcopy(answer), None, 321, "settings"

    monkeypatch.setattr(scriptgen, "_dispatch", _fake)
    monkeypatch.setattr(scriptgen, "scriptgen_backend", lambda: "settings")   # 接口起任务前先查文本模型配没配
    return seen


def wait_task(task_id, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        record = client.get(f"/api/tasks/{task_id}").json()
        if record.get("status") in ("succeeded", "failed"):
            return record
        time.sleep(0.05)
    raise AssertionError("task did not finish")


ANSWER = {"summary": "一处踩线", "items": [item(), item(source="tension", index=3, quote="我知道爸爸的秘密"), item(index=99)]}


def test_post_without_checklist_file_is_400_and_get_is_empty(project, monkeypatch):
    write(project)
    got = client.get(URL).json()
    assert got["checklist"] == {"id": "craft-v1", "name": "剧本工艺清单"}
    assert got["review"] is None and got["stale"] is False and got["problems"] == [] and got["task_id"] is None
    monkeypatch.setattr(CR, "CHECKLIST_PATH", project / "missing.json")
    r = client.post(URL)
    assert r.status_code == 400 and r.json()["detail"] == CR.MISSING_CHECKLIST_MESSAGE
    assert client.get(URL).json()["checklist"] is None
    assert client.post("/api/episodes/storyvideo/NOPE/craft-review").status_code == 404


def test_review_task_writes_record_and_get_returns_verified_problem_cards(project, monkeypatch):
    write(project)
    seen = fake_dispatch(monkeypatch, ANSWER)
    r = client.post(URL)
    assert r.status_code == 200, r.text
    assert r.json()["cost_note"] == "发 1 次文本模型请求"
    task = wait_task(r.json()["task_id"])
    assert task["status"] == "succeeded", task
    assert task["kind"] == "craft-review" and task["module"] == "drama" and task["params"] == {"checklist": "craft-v1"}
    assert task["result"]["verification"] == {"checked": 2, "verified": 1, "demoted": 1, "dropped": 1, "missing": 14}
    assert task["result"]["counts"] == {"violated": 1, "risk": 0, "ok": 0, "unverified": 1}
    assert task["result"]["checklist_id"] == "craft-v1"
    assert seen["calls"] == 1 and seen["schema"] is CR.REVIEW_JSON_SCHEMA and "## 镜 S01" in seen["user"]

    saved = json.loads((project / "output" / "story_video" / EID / "manifests" / "craft_review.json").read_text(encoding="utf-8"))
    for key in ("checklist_id", "checklist_name", "model", "backend", "created_at", "spec_hash", "result", "verification"):
        assert key in saved
    assert saved["checklist_id"] == "craft-v1" and saved["checklist_name"] == "剧本工艺清单" and saved["backend"] == "settings"

    got = client.get(URL).json()
    assert set(got) == {"checklist", "review", "stale", "note", "problems", "cost_note", "task_id"}
    assert got["checklist"] == {"id": "craft-v1", "name": "剧本工艺清单"}
    assert got["stale"] is False and got["note"] == "" and got["task_id"] is None
    assert got["review"]["spec_hash"] == saved["spec_hash"]
    assert [(c["kind"], c["target"]) for c in got["problems"]] == [("craft", "S01")]
    assert got["problems"][0]["impact"].startswith("按工艺清单审稿（模型判断）：违反情绪可见")
    detail = client.get(f"/api/episodes/storyvideo/{EID}").json()
    assert [c["target"] for c in detail["problems"] if c["kind"] == "craft"] == ["S01"]
    en = client.get(URL, headers={"X-StoryStudio-Lang": "en"}).json()
    assert en["problems"][0]["impact"].startswith("Craft checklist review (model judgement): violates visible emotion")


def test_editing_the_script_marks_the_review_stale(project, monkeypatch):
    write(project)
    fake_dispatch(monkeypatch, ANSWER)
    assert wait_task(client.post(URL).json()["task_id"])["status"] == "succeeded"
    r = client.put(f"/api/episodes/storyvideo/{EID}/shots/S02", json={"motion": "男孩把体检单撕碎"})
    assert r.status_code == 200, r.text
    got = client.get(URL).json()
    assert got["stale"] is True and got["note"] == "剧本改过，这份审稿可能过时——重新审一次"
    assert got["problems"] == [] and got["review"] is not None
    detail = client.get(f"/api/episodes/storyvideo/{EID}").json()
    assert not any(c["kind"] == "craft" for c in detail["problems"])


def test_second_post_while_running_is_409(project, monkeypatch):
    write(project)
    gate = threading.Event()
    fake_dispatch(monkeypatch, ANSWER, gate=gate)
    first = client.post(URL)
    assert first.status_code == 200, first.text
    try:
        second = client.post(URL)
        assert second.status_code == 409 and "已经在跑" in second.json()["detail"]
        assert client.get(URL).json()["task_id"] == first.json()["task_id"]
    finally:
        gate.set()
    assert wait_task(first.json()["task_id"])["status"] == "succeeded"


def test_model_without_items_fails_the_task(project, monkeypatch):
    write(project)
    fake_dispatch(monkeypatch, {"summary": "看起来不错"})
    task = wait_task(client.post(URL).json()["task_id"])
    assert task["status"] == "failed" and "模型没有按审稿结构返回（缺 items 列表）" in task["error"]
    assert client.get(URL).json()["review"] is None


def test_local_text_role_gets_the_free_cost_note(project, monkeypatch):
    from genkit import local as local_mod

    write(project)
    fake_dispatch(monkeypatch, ANSWER)
    monkeypatch.setattr(local_mod, "role_local", lambda role, settings=None: role == "text")
    r = client.post(URL)
    assert r.json()["cost_note"] == "本地生成，不花钱（1 次请求）"
    wait_task(r.json()["task_id"])
    assert client.get(URL).json()["cost_note"] == "本地生成，不花钱（1 次请求）"


def test_craft_and_genre_reviews_live_side_by_side(project, monkeypatch):
    """同一集两种审稿各写各的文件、各出各的问题卡，互不覆盖。"""

    write(project, genre="qinzi-yinmi")
    fake_dispatch(monkeypatch, ANSWER)
    assert wait_task(client.post(URL).json()["task_id"])["status"] == "succeeded"
    genre_answer = {"summary": "s", "items": [{"source": "pitfall", "index": 1, "item": "x", "verdict": "violated",
                                                "shots": ["S02"], "quote": "两人抱在一起，镜头拉远", "why": "w", "fix": "f"}]}
    fake_dispatch(monkeypatch, genre_answer)
    assert wait_task(client.post(f"/api/episodes/storyvideo/{EID}/genre-review").json()["task_id"])["status"] == "succeeded"
    manifests = project / "output" / "story_video" / EID / "manifests"
    assert (manifests / "craft_review.json").is_file() and (manifests / "genre_review.json").is_file()
    detail = client.get(f"/api/episodes/storyvideo/{EID}").json()
    assert sorted((c["kind"], c["target"]) for c in detail["problems"] if c["kind"] in ("craft", "genre")) == [("craft", "S01"), ("genre", "S02")]


# ---- 5. 问题卡分类与英文

def test_problem_card_kind_label_and_english_translation(tmp_path):
    assert KIND_LABELS["craft"] == "工艺"
    items = [
        item(),
        item(source="tension", index=3, verdict="risk", shots=["S01", "S02"], quote="两人抱在一起，镜头拉远", why="", fix=""),
        item(source="rhythm", index=1, verdict="risk", shots=["S02"], quote="两人抱在一起，镜头拉远", why="三镜没有新信息", fix="删掉一镜"),
        item(source="compliance", index=3, verdict="violated", shots=[], quote="男孩要让爸爸在手术同意书上签字", why="医疗场面（高风险题材）", fix="只写结果"),
    ]
    spec, result, stats = verify(tmp_path, items)
    warnings = CR.review_problems(spec, record(spec, result, stats))
    assert len(warnings) == 4
    i18n._compiled = None
    for text in warnings:
        out = i18n.tr(text, "en")
        assert out != text and not _HAN.search(_QUOTED.sub("", out)), out
        card_ = problem_card(text)
        assert card_["kind"] == "craft"
        for half in (card_["impact"], card_["fix"]):
            if half:
                got = i18n.tr(half, "en")
                assert got != half and not _HAN.search(_QUOTED.sub("", got)), (half, got)
    assert i18n.tr(warnings[0], "en").startswith("shot S01: Craft checklist review (model judgement): violates visible emotion")
    assert i18n.tr(warnings[1], "en").startswith("Whole episode: Craft checklist review (model judgement): violates compliance")
    assert i18n.tr(warnings[2], "en").startswith("Whole episode: Craft checklist review (model judgement): is at risk on tension")
    assert i18n.tr(warnings[3], "en").startswith("shot S02: Craft checklist review (model judgement): is at risk on rhythm")
    assert i18n.tr(CR.MISSING_CHECKLIST_MESSAGE, "en").startswith("The craft checklist file could not be read")
