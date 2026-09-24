"""按题材审稿（DESIGN §49）：spec.genre 的归一化与落盘、审稿提示词、**引文核对闸门**、过时判定、接口与问题卡。

模型一律是替身（假 ``scriptgen._dispatch``），这里验的是「模型说的话哪些能信」：引文要逐字落在所列镜 / 场的某个字段里、
镜号要存在、编号要对得上卡片条目——对不上的降级、不进告警、或直接丢掉。
"""

import copy
import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import genre_review as GR
from storyvideo import scriptgen
from storyvideo.spec import SpecError, normalize_spec
from webui import deps, i18n
from webui import revise as R
from webui.app import app
from webui.problems import KIND_LABELS, problem_card
from webui.routers import episodes as episodes_router
from webui.routers import project as project_router

client = TestClient(app)
EID = "GR1"
URL = f"/api/episodes/storyvideo/{EID}/genre-review"
CARD_ID = "qinzi-yinmi"
_HAN = re.compile(r"[一-鿿]")
_QUOTED = re.compile(r"「[^」]*」|《[^》]*》|\"[^\"]*\"")


def shot(sid, motion, zh=None):
    lines = [{"who": "kid", "zh": zh, "en": "a line"}] if zh else []
    return {"id": sid, "dur": 5, "cast": ["kid"], "shot": "近景", "art": "客厅，傍晚", "motion": motion, "lines": lines}


def raw_spec(**extra):
    data = {
        "id": EID, "output_root": f"output/story_video/{EID}", "title_zh": "体检单", "genre": CARD_ID,
        "cast": {"kid": {"block": "八岁男孩，蓝色校服"}},
        "episode_contract": {"premise": "男孩要让爸爸在手术同意书上签字", "hook": "男孩把体检单塞进书包"},
        "shots": [shot("S01", "男孩抱住妈妈", "妈妈别哭，我给你唱首歌吧。"), shot("S02", "男孩把体检单塞进书包")],
    }
    data.update(extra)
    return {k: v for k, v in data.items() if v is not None}


def card():
    return scriptgen.find_genre(CARD_ID)


def item(source="pitfall", index=1, verdict="violated", shots=("S01",), quote="妈妈别哭，我给你唱首歌", why="w", fix="f", **extra):
    return {"source": source, "index": index, "item": extra.pop("item", "模型抄的条目"), "verdict": verdict,
            "shots": list(shots), "quote": quote, "why": why, "fix": fix, **extra}


def verify(root, items, screenplay=None, **spec_extra):
    spec = normalize_spec(raw_spec(**spec_extra), project_root=root)
    result, stats = GR.verify_review({"summary": "s", "items": items}, spec, screenplay)
    return spec, result, stats


def record(spec, result, stats, screenplay=None):
    return {"card_id": CARD_ID, "card_name": card()["name"], "spec_hash": GR.review_hash(spec, CARD_ID, screenplay),
            "result": result, "verification": stats}


# ---- 1. spec.genre：归一化、起草落盘、全局字段

def test_spec_genre_accepts_id_name_alias_and_rejects_unknown(tmp_path):
    for value in (CARD_ID, CARD_ID.upper(), "亲子隐秘", "萌宝"):
        assert normalize_spec(raw_spec(genre=value), project_root=tmp_path)["genre"] == CARD_ID
    with pytest.raises(SpecError, match="unknown genre card: 火星"):
        normalize_spec(raw_spec(genre="火星"), project_root=tmp_path)
    for bad in ("  ", 3):
        with pytest.raises(SpecError, match="genre must be a non-empty string"):
            normalize_spec(raw_spec(genre=bad), project_root=tmp_path)
    # 老项目没写 genre：归一化结果里没有这个键，审稿告警为空
    old = normalize_spec(raw_spec(genre=None), project_root=tmp_path)
    assert "genre" not in old
    assert GR.review_problems(old) == []


def test_assemble_spec_writes_genre_only_when_drafting_used_a_card():
    draft = {"title_zh": "t", "cast": {"kid": {"block": "b"}}, "shots": [shot("S01", "m")]}
    spec = scriptgen.assemble_spec("EP9", copy.deepcopy(draft), camera_profile="dark_comedy_vertical", prompt_language="zh", genre="萌宝")
    assert spec["genre"] == CARD_ID
    plain = scriptgen.assemble_spec("EP9", copy.deepcopy(draft), camera_profile="dark_comedy_vertical", prompt_language="zh")
    assert "genre" not in plain
    with pytest.raises(ValueError, match="不认识的题材：火星"):
        scriptgen.assemble_spec("EP9", copy.deepcopy(draft), camera_profile="dark_comedy_vertical", prompt_language="zh", genre="火星")


def test_draft_job_stores_the_chosen_genre_on_the_spec(tmp_path, monkeypatch):
    from webui.routers import scripts as S

    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    monkeypatch.setattr(S, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(S, "JOBS_DIR", tmp_path / "jobs")
    draft = {"title_zh": "t", "cast": {"kid": {"block": "b"}}, "shots": [shot("S01", "m")]}
    monkeypatch.setattr(S, "generate_draft", lambda **kw: (copy.deepcopy(draft), None, 1, "settings"))
    monkeypatch.setattr(S.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    r = client.post("/api/scripts/jobs/draft", json={"direction": "x", "target_seconds": 5, "camera_profile": "dark_comedy_vertical", "genre": "亲子隐秘"})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/scripts/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded", job
    assert job["result"]["spec"]["genre"] == CARD_ID


def test_globals_accept_genre_normalize_reject_and_clear(tmp_path, monkeypatch):
    project = _isolate(tmp_path, monkeypatch)
    path = write(project, genre=None)
    r = client.put(f"/api/episodes/storyvideo/{EID}/globals", json={"values": {"genre": "萌宝"}})
    assert r.status_code == 200, r.text
    assert json.loads(path.read_text(encoding="utf-8"))["genre"] == CARD_ID
    assert client.get(f"/api/episodes/storyvideo/{EID}/summary").json()["genre"] == {"id": CARD_ID, "name": "亲子隐秘"}
    r = client.put(f"/api/episodes/storyvideo/{EID}/globals", json={"values": {"genre": "火星"}})
    assert r.status_code == 400 and r.json()["detail"] == "不认识的题材：火星"
    assert json.loads(path.read_text(encoding="utf-8"))["genre"] == CARD_ID
    for empty in (None, ""):
        client.put(f"/api/episodes/storyvideo/{EID}/globals", json={"values": {"genre": CARD_ID}})
        r = client.put(f"/api/episodes/storyvideo/{EID}/globals", json={"values": {"genre": empty}})
        assert r.status_code == 200, r.text
        assert "genre" not in json.loads(path.read_text(encoding="utf-8"))
    assert client.get(f"/api/episodes/storyvideo/{EID}/summary").json()["genre"] is None


def test_genre_is_a_global_key_that_never_touches_a_prompt(tmp_path):
    assert "genre" in R.GLOBAL_KEYS
    mutated = R.apply_mutation(raw_spec(genre=None), {"kind": "globals", "values": {"genre": "亲子隐秘"}})
    assert mutated["genre"] == CARD_ID
    with pytest.raises(R.ReviseError, match="不认识的题材：火星"):
        R.apply_mutation(raw_spec(), {"kind": "globals", "values": {"genre": "火星"}})
    old = normalize_spec(raw_spec(genre=None), project_root=tmp_path)
    new = normalize_spec(mutated, project_root=tmp_path)
    impact = R.impact(old, new)
    assert impact["affected_frames"] == [] and impact["affected_videos"] == []


# ---- 2. 提示词

def test_prompt_numbers_every_card_item_and_lists_every_shot(tmp_path):
    spec = normalize_spec(raw_spec(), project_root=tmp_path)
    c = GR.spec_card(spec)
    system, user = GR.build_review_prompt(spec, c)
    items = GR.card_items(c)
    assert sum(1 for it in items if it["source"] == "pitfall") == len(c["pitfalls"])
    assert sum(1 for it in items if it["source"] == "drift") == len(c["disallowed_drift"])
    assert sum(1 for it in items if it["source"] == "core") == 1
    for it in items:
        assert f"{it['index']}. {it['item']}" in user
    for sid in ("S01", "S02"):
        assert f"## 镜 {sid}" in user
    assert "妈妈别哭，我给你唱首歌吧。" in user and "男孩把体检单塞进书包" in user and "体检单" in user
    assert "男孩要让爸爸在手术同意书上签字" in user          # 契约进审稿正文
    for rule in ("常见坑", "禁止漂移", "quote", "找不到能引用的原文就判 ok", "不要编造", "不评"):
        assert rule in system
    assert "## 场" not in user
    screenplay = {"scenes": [{"id": "SC01", "heading": "内 · 医院走廊 · 夜", "beats": ["男孩偷偷签下爸爸的名字"],
                              "dialogue": [{"who": "kid", "zh": "我替你签"}]}]}
    _system, with_screenplay = GR.build_review_prompt(spec, c, screenplay)
    assert "## 场 SC01" in with_screenplay and "男孩偷偷签下爸爸的名字" in with_screenplay


# ---- 3. 核对闸门

def test_existing_quote_passes_and_card_text_replaces_model_copy(tmp_path):
    spec, result, stats = verify(tmp_path, [item(item="模型改写过的条目")])
    it = result["items"][0]
    assert it["evidence_verified"] is True and it["verdict"] == "violated"
    assert it["item"] == card()["pitfalls"][0]
    assert stats == {"checked": 1, "verified": 1, "demoted": 0, "dropped": 0, "missing": len(GR.card_items(card())) - 1}
    # 空白 / 标点不同、带字段名前缀、用省略号截断的真实原文也算对上
    for quote in ("妈妈 别哭 我给你唱首歌吧", "台词（kid）：妈妈别哭，我给你唱首歌吧。", "妈妈别哭……唱首歌吧"):
        _spec, again, _stats = verify(tmp_path, [item(quote=quote)])
        assert again["items"][0]["evidence_verified"] is True, quote


def test_fabricated_quote_is_demoted_and_never_becomes_a_warning(tmp_path):
    spec, result, stats = verify(tmp_path, [item(quote="我知道爸爸的秘密")])
    it = result["items"][0]
    assert it["evidence_verified"] is False and it["evidence_problem"] == "quote_not_found"
    assert it["verdict"] == "risk" and it["model_verdict"] == "violated"
    assert stats["demoted"] == 1 and stats["verified"] == 0
    assert GR.review_problems(spec, record(spec, result, stats)) == []
    # 原文是真的，但不在所列的那一镜里：也不算
    _spec, wrong_shot, _ = verify(tmp_path, [item(shots=["S02"])])
    assert wrong_shot["items"][0]["evidence_problem"] == "quote_not_found"
    # 太短算不上证据；没给引文也不算
    _spec, short, _ = verify(tmp_path, [item(quote="妈妈")])
    assert short["items"][0]["evidence_problem"] == "quote_too_short"
    _spec, empty, _ = verify(tmp_path, [item(quote="")])
    assert empty["items"][0]["evidence_problem"] == "no_quote"


def test_nonexistent_shot_id_is_demoted(tmp_path):
    spec, result, stats = verify(tmp_path, [item(shots=["S01", "S09"])])
    it = result["items"][0]
    assert it["evidence_verified"] is False and it["evidence_problem"] == "unknown_ids" and it["unknown_ids"] == ["S09"]
    assert it["verdict"] == "risk" and stats["demoted"] == 1
    assert GR.review_problems(spec, record(spec, result, stats)) == []


def test_out_of_range_index_unknown_source_verdict_and_duplicates_are_dropped(tmp_path):
    items = [
        item(index=1, verdict="ok", shots=[], quote=""),
        item(index=1),                         # 同一条答了两次：留第一次
        item(index=99),                        # 卡里没有第 99 条常见坑
        item(source="drift", index=len(card()["disallowed_drift"]) + 1),
        item(source="vibe"),
        item(verdict="terrible"),
        item(index=True),
        item(index="2", verdict="ok", shots=[], quote=""),
    ]
    _spec, result, stats = verify(tmp_path, items)
    assert [(it["source"], it["index"], it["verdict"]) for it in result["items"]] == [("pitfall", 1, "ok"), ("pitfall", 2, "ok")]
    assert stats["dropped"] == 6 and stats["checked"] == 0


def test_verified_items_become_warnings_with_model_judgement_prefix(tmp_path):
    items = [
        item(source="drift", index=1, verdict="risk", shots=["S01", "S02"], quote="男孩把体检单塞进书包", why="冲突是旧账——不对", fix=""),
        item(why="孩子只在安慰「甜」"),
    ]
    spec, result, stats = verify(tmp_path, items)
    warnings = GR.review_problems(spec, record(spec, result, stats))
    pitfall, drift = card()["pitfalls"][0], card()["disallowed_drift"][0]
    assert warnings[0] == (f"shot S01: 按题材卡「亲子隐秘」审稿（模型判断）：违反常见坑「{pitfall}」，原文「妈妈别哭，我给你唱首歌」，"
                           "理由「孩子只在安慰『甜』」——建议「f」")
    assert warnings[1] == (f"全集：按题材卡「亲子隐秘」审稿（模型判断）：有风险禁止漂移「{drift.replace('——', '，')}」（见 S01、S02），"
                           "原文「男孩把体检单塞进书包」，理由「冲突是旧账，不对」")
    first, second = problem_card(warnings[0]), problem_card(warnings[1])
    assert (first["kind"], first["scope"], first["target"]) == ("genre", "shot", "S01")
    assert first["impact"].startswith("按题材卡「亲子隐秘」审稿（模型判断）：违反常见坑") and first["fix"] == "建议「f」"
    assert (second["kind"], second["scope"], second["fix"]) == ("genre", "episode", "")
    # 模型写的理由里出现播客那组关键词（「分歧」「主播」）也不能把题材卡抢走
    _spec, with_podcast_words, _stats = verify(tmp_path, [item(why="两人的分歧只在嘴上，像两个主播在念稿")])
    assert problem_card(GR.review_problems(spec, record(spec, with_podcast_words, _stats))[0])["kind"] == "genre"


def test_screenplay_scene_quote_verifies_and_scopes_to_the_scene(tmp_path):
    screenplay = {"scenes": [{"id": "SC01", "heading": "内 · 医院走廊 · 夜", "beats": ["男孩偷偷签下爸爸的名字"], "dialogue": []}]}
    spec, result, stats = verify(tmp_path, [item(shots=["SC01"], quote="男孩偷偷签下爸爸的名字")], screenplay)
    assert result["items"][0]["evidence_verified"] is True
    warnings = GR.review_problems(spec, record(spec, result, stats, screenplay), screenplay)
    assert warnings[0].startswith("scene SC01: 按题材卡「亲子隐秘」审稿（模型判断）")
    # 没有剧本时同一个场号不存在
    _spec, without, _ = verify(tmp_path, [item(shots=["SC01"], quote="男孩偷偷签下爸爸的名字")])
    assert without["items"][0]["evidence_problem"] == "unknown_ids"


def test_review_goes_stale_when_reviewed_text_or_genre_changes(tmp_path):
    spec, result, stats = verify(tmp_path, [item()])
    rec = record(spec, result, stats)
    assert not GR.is_stale(spec, rec) and GR.review_problems(spec, rec)
    edited = normalize_spec(raw_spec(shots=[shot("S01", "男孩推开妈妈", "妈妈别哭，我给你唱首歌吧。"), shot("S02", "男孩把体检单塞进书包")]),
                            project_root=tmp_path)
    assert GR.is_stale(edited, rec) and GR.review_problems(edited, rec) == []
    regenre = normalize_spec(raw_spec(genre="fuchou-dalian"), project_root=tmp_path)
    assert GR.is_stale(regenre, rec)
    # 没审的东西（负向词）改了不算过时
    untouched = normalize_spec(raw_spec(neg="水印"), project_root=tmp_path)
    assert not GR.is_stale(untouched, rec)


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


ANSWER = {"summary": "一处踩坑", "items": [item(), item(index=2, quote="我知道爸爸的秘密"), item(index=99)]}


def test_post_without_genre_is_400_and_get_is_empty(project):
    write(project, genre=None)
    r = client.post(URL)
    assert r.status_code == 400 and r.json()["detail"] == "这一集没有选题材，先在概览里选一个"
    got = client.get(URL).json()
    assert got["genre"] is None and got["review"] is None and got["stale"] is False and got["problems"] == []


def test_review_task_writes_record_and_get_returns_verified_problem_cards(project, monkeypatch):
    write(project)
    seen = fake_dispatch(monkeypatch, ANSWER)
    r = client.post(URL)
    assert r.status_code == 200, r.text
    assert r.json()["cost_note"] == "发 1 次文本模型请求"
    task = wait_task(r.json()["task_id"])
    assert task["status"] == "succeeded", task
    assert task["kind"] == "genre-review" and task["module"] == "drama"
    total = len(GR.card_items(card()))
    assert task["result"]["verification"] == {"checked": 2, "verified": 1, "demoted": 1, "dropped": 1, "missing": total - 2}
    assert task["result"]["counts"] == {"violated": 1, "risk": 0, "ok": 0, "unverified": 1}
    assert seen["calls"] == 1 and seen["schema"] is GR.REVIEW_JSON_SCHEMA and "## 镜 S01" in seen["user"]

    saved = json.loads((project / "output" / "story_video" / EID / "manifests" / "genre_review.json").read_text(encoding="utf-8"))
    for key in ("card_id", "card_name", "model", "backend", "created_at", "spec_hash", "result", "verification"):
        assert key in saved
    assert saved["card_id"] == CARD_ID and saved["card_name"] == "亲子隐秘" and saved["backend"] == "settings"

    got = client.get(URL).json()
    assert set(got) == {"genre", "review", "stale", "note", "problems", "cost_note", "task_id"}
    assert got["genre"] == {"id": CARD_ID, "name": "亲子隐秘"}
    assert got["stale"] is False and got["note"] == "" and got["task_id"] is None
    assert got["review"]["spec_hash"] == saved["spec_hash"]
    assert [(c["kind"], c["target"]) for c in got["problems"]] == [("genre", "S01")]
    assert got["problems"][0]["impact"].startswith("按题材卡「亲子隐秘」审稿（模型判断）：违反常见坑")
    detail = client.get(f"/api/episodes/storyvideo/{EID}").json()
    assert [c["target"] for c in detail["problems"] if c["kind"] == "genre"] == ["S01"]
    en = client.get(URL, headers={"X-StoryStudio-Lang": "en"}).json()
    assert en["problems"][0]["impact"].startswith('Genre card "亲子隐秘" review (model judgement): violates pitfall')


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
    assert not any(c["kind"] == "genre" for c in detail["problems"])


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


# ---- 5. 问题卡分类与英文

def test_problem_card_kind_label_and_english_translation(tmp_path):
    assert KIND_LABELS["genre"] == "题材"
    items = [item(), item(source="core", index=1, verdict="risk", shots=["S01", "S02"], quote="男孩把体检单塞进书包", why="", fix="")]
    spec, result, stats = verify(tmp_path, items)
    warnings = GR.review_problems(spec, record(spec, result, stats))
    assert len(warnings) == 2
    i18n._compiled = None
    for text in warnings:
        out = i18n.tr(text, "en")
        assert out != text and not _HAN.search(_QUOTED.sub("", out)), out
        card_ = problem_card(text)
        assert card_["kind"] == "genre"
        for half in (card_["impact"], card_["fix"]):
            if half:
                got = i18n.tr(half, "en")
                assert got != half and not _HAN.search(_QUOTED.sub("", got)), (half, got)
    assert i18n.tr(warnings[0], "en").startswith('shot S01: Genre card "亲子隐秘" review (model judgement): violates pitfall')
    assert i18n.tr(warnings[1], "en").startswith('Whole episode: Genre card "亲子隐秘" review (model judgement): is at risk on genre core')
    assert i18n.tr("这一集没有选题材，先在概览里选一个", "en") == "This episode has no genre yet — pick one on the overview page first"
