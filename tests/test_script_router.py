"""剧本工作台接口（契约 docs/scriptlab-spec.md §8）：列表 / 编号 / 选项 / 新建 / 示例 / 详情形状 / 保存校验 /
生成任务（假模型）与 400 / 409 / 只重写几段 / 采用 / 手写另存 / 删版本 / 审稿的 400 与只读 GET / 归档。

这个模块不出图、不出声、不装配，所以只有一个注入点 ``DISPATCH``（文本模型）。
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from webui import deps, tasks
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import script as script_router

client = TestClient(app)
API = "/api/script"


@pytest.fixture
def project(tmp_path, monkeypatch):
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", tmp_path / "content" / "story_video")
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", tmp_path / "content" / "story_video")
    monkeypatch.setattr(tasks, "tasks_dir", lambda: tmp_path / ".webui_tasks")
    monkeypatch.setattr(script_router, "DISPATCH", None)
    return tmp_path


def wait(task_id: str, seconds: float = 10.0) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        task = tasks.get(task_id)
        if task and task["status"] in ("succeeded", "failed"):
            return task
        time.sleep(0.02)
    raise AssertionError("task did not finish")


def body(**extra) -> dict:
    data = {
        "title": "下班后的消息，要不要回", "target": "podcast", "language": "zh",
        "brief": {"topic": "回还是不回", "materials": "阿杰上周六被拉进新群"},
        "speakers": [{"id": "jie", "name": "阿杰", "persona": "慢"}, {"id": "lin", "name": "小林", "persona": "快"}],
        "blocks": [{"id": "B01", "title": "冷开场", "goal": "拽进来"}, {"id": "B02", "title": "分歧", "goal": "不和解"}],
        "versions": [{"id": "V1", "label": "第一版", "source": "hand", "blocks": [
            {"id": "B01", "lines": [{"who": "jie", "text": "上周六晚上十点，我被拉进一个新群。"},
                                    {"who": "lin", "text": "嗯，然后呢？"}]},
            {"id": "B02", "lines": [{"who": "lin", "text": "可是你那套在我这儿行不通。"}]},
        ]}],
        "current": "V1",
    }
    data.update(extra)
    return data


def draft(*blocks: str):
    """假模型：按要求返回这几段（不给就两段都返回）。"""

    wanted = list(blocks) or ["B01", "B02"]

    def dispatch(system_prompt, user_prompt, *, json_schema):
        return ({"blocks": [{"id": bid, "lines": [{"who": "阿杰", "text": f"模型写的 {bid} 第一句。", "kind": "talk"},
                                                  {"who": "lin", "text": "我不这么看。", "kind": "talk"}]}
                            for bid in wanted]}, None, 77, "settings")

    return dispatch


# ---------------------------------------------------------------------------- 列表 / 编号 / 选项 / 新建


def test_list_next_id_and_options(project):
    assert client.get(API).json() == []
    assert client.get(f"{API}/next-id").json()["id"].startswith("SC-")
    options = client.get(f"{API}/options").json()
    assert [t["id"] for t in options["targets"]] == ["podcast", "drama_screenplay", "drama_shot", "generic"]
    assert options["cues"][0] == "笑" and options["kinds"] == ["talk", "question", "answer"]
    assert options["limits"]["speakers"] == [1, 6] and options["examples"] == ["after_hours_message"]
    assert [s["id"] for s in options["sources"]] == ["model", "hand", "rewrite", "import"]
    assert [m["id"] for m in options["use_modules"]] == ["podcast", "drama_screenplay", "drama_shot"]
    # 写法约束按项目语言取：短剧那条「一句不超过 N 字」只进中文稿（英文没有对应口径）
    assert len(options["craft"]["podcast"]["zh"]) == 9
    assert len(options["craft"]["drama_screenplay"]["zh"]) == 6
    assert len(options["craft"]["drama_screenplay"]["en"]) == 5
    assert not any("一句不超过" in r for r in options["craft"]["drama_screenplay"]["en"])
    assert client.get(f"{API}/targets").json() == {"modules": ["podcast", "drama_screenplay", "drama_shot"],
                                                   "podcast": [], "drama": []}


def test_create_detail_update_and_validation(project):
    created = client.post(API, json=body()).json()
    sid = created["id"]
    assert sid.startswith("SC-") and created["module"] == "script"
    # 契约 §8 末尾那一份
    for key in ("spec", "current_version", "estimate", "warnings", "problems", "versions", "review",
                "targets_hint", "busy", "limits", "block_estimates", "used_in"):
        assert key in created, key
    assert created["estimate"]["lines"] == 3 and created["estimate"]["blocks"] == 2
    assert created["current_version"]["id"] == "V1"
    assert created["versions"][0]["id"] == "V1" and created["versions"][0]["current"] is True
    assert created["versions"][0]["estimate"]["lines"] == 3 and created["versions"][0]["review"] is None
    assert created["versions"][0]["source_label"] == "手写另存"
    assert [b["id"] for b in created["block_estimates"]] == ["B01", "B02"]
    assert created["busy"] == {"draft": None, "review": None}
    assert created["review"]["version"] == "V1" and created["review"]["review"] is None
    assert (project / "content" / "script" / f"{sid}.json").is_file()
    rows = client.get(API).json()
    assert rows[0]["id"] == sid and rows[0]["target"] == "podcast" and rows[0]["target_name"] == "播客对谈"
    assert rows[0]["speakers"] == 2 and rows[0]["blocks"] == 2 and rows[0]["versions"] == 1
    assert rows[0]["current"] == "V1" and rows[0]["used_in"] == 0 and rows[0]["path"].endswith(f"{sid}.json")
    # 保存：校验不过 400，不落盘
    detail = client.get(f"{API}/{sid}").json()
    bad = client.put(f"{API}/{sid}", json={**detail["spec"], "speakers": [{"id": "x", "name": "X"}]})
    assert bad.status_code == 400 and bad.json()["detail"].startswith("改动没通过校验，没有写盘：")
    assert client.get(f"{API}/{sid}").json()["spec"]["speakers"][0]["id"] == "jie"
    mismatch = client.put(f"{API}/{sid}", json={**detail["spec"], "id": "OTHER"})
    assert mismatch.status_code == 400
    ok = client.put(f"{API}/{sid}", json={**detail["spec"], "title": "改名"})
    assert ok.status_code == 200 and ok.json()["title"] == "改名"
    assert client.get(f"{API}/NOPE").status_code == 404
    assert client.post(API, json={**body(), "id": sid}).status_code == 409
    assert client.post(API, json={**body(), "id": "bad id"}).status_code == 400


def test_put_can_drop_a_block_once_every_version_drops_it_too(project):
    """写页删段：PUT 的骨架少一段、各版也同步剔掉那一段 → 200，盘上每一版都没那段（骨架是项目级的，删段影响每一版）。

    版本里还带着被删的段则照旧 400——那是「段号写错」的防线，不放松；前端 saveSkeleton 负责按剩下的段过滤 versions。"""

    sid = client.post(API, json=body()).json()["id"]
    client.post(f"{API}/{sid}/versions", json={"blocks": [{"id": "B02", "lines": [{"who": "lin", "text": "另一版。"}]}]})
    spec = client.get(f"{API}/{sid}").json()["spec"]
    stale = {**spec, "blocks": [b for b in spec["blocks"] if b["id"] != "B02"]}
    r = client.put(f"{API}/{sid}", json=stale)
    assert r.status_code == 400 and "版本 V1 里的段「B02」不在段落骨架里" in r.json()["detail"]
    filtered = {**stale, "versions": [{**v, "blocks": [b for b in v["blocks"] if b["id"] != "B02"]} for v in spec["versions"]]}
    r = client.put(f"{API}/{sid}", json=filtered)
    assert r.status_code == 200, r.json()
    saved = json.loads((project / "content" / "script" / f"{sid}.json").read_text(encoding="utf-8"))
    assert [b["id"] for b in saved["blocks"]] == ["B01"]
    assert len(saved["versions"]) == 2 and all([b["id"] for b in v["blocks"]] == ["B01"] for v in saved["versions"])
    assert saved["versions"][0]["blocks"][0]["lines"][0]["text"] == "上周六晚上十点，我被拉进一个新群。"   # 别的段一句没动


def test_target_names_follow_the_ui_language(project):
    """英文界面：``/options`` 的 ``targets[].name`` 与列表行的 ``target_name`` 翻成英文，措辞跟 ``english`` 字段同一套；
    中文原样。这两个键承载用户内容（角色名也叫 name），不能进中间件的整键翻译表，所以接口层点名翻。"""

    client.post(API, json=body(target="drama_shot"))
    en = {"X-StoryStudio-Lang": "en"}
    targets = client.get(f"{API}/options", headers=en).json()["targets"]
    assert [(t["id"], t["name"]) for t in targets] == [
        ("podcast", "Podcast"), ("drama_screenplay", "Short drama scene"),
        ("drama_shot", "Short drama shot"), ("generic", "Generic dialogue")]
    assert all(t["name"] == t["english"] for t in targets)
    assert client.get(API, headers=en).json()[0]["target_name"] == "Short drama shot"
    assert client.get(API).json()[0]["target_name"] == "短剧分镜（镜）"
    assert [t["name"] for t in client.get(f"{API}/options").json()["targets"]][0] == "播客对谈"


def test_example_copy_has_no_warnings(project):
    r = client.post(f"{API}/examples/create", json={"name": "after_hours_message"})
    assert r.status_code == 200
    data = r.json()
    assert data["example"] == "after_hours_message" and data["estimate"]["lines"] == 18
    assert data["warnings"] == [] and data["problems"] == []
    assert data["spec"]["current"] == "V1" and data["spec"]["target"] == "podcast"
    assert client.post(f"{API}/examples/create", json={"name": "nope"}).status_code == 404


# ---------------------------------------------------------------------------- 生成 / 重写


def test_generate_task_rejects_empty_project_and_appends_a_version(project, monkeypatch):
    from storyvideo import scriptgen

    monkeypatch.setattr(scriptgen, "scriptgen_backend", lambda: "unconfigured")   # 真函数没配时返回的就是这个值，不是 ""
    sid = client.post(API, json=body(versions=[], current="")).json()["id"]
    assert client.post(f"{API}/{sid}/generate", json={}).status_code == 400   # 没配文本模型

    monkeypatch.setattr(script_router, "DISPATCH", draft())
    r = client.post(f"{API}/{sid}/generate", json={"note": "开头先讲一个具体场景"})
    assert r.status_code == 200 and r.json()["cost_note"] == "发 1 次文本模型请求"
    task = wait(r.json()["task_id"])
    assert task["status"] == "succeeded" and task["module"] == "script" and task["kind"] == "sc-draft"
    assert task["result"]["version"] == "V1" and task["result"]["lines"] == 4
    detail = client.get(f"{API}/{sid}").json()
    # 契约 §3：生成后不自动采用——但第一版例外（没有可比的对象，不认它「用」页会一直说还没挑一版）。
    # current_version 是「到底采用了没有」，检查 / 估算回落的那一份叫 picked_version
    assert detail["spec"]["current"] == "V1" and detail["picked_version"] == "V1"
    assert detail["current_version"] is not None
    assert detail["versions"][0]["current"] is True
    assert detail["spec"]["versions"][0]["source"] == "model" and detail["spec"]["versions"][0]["backend"] == "settings"
    assert detail["spec"]["versions"][0]["blocks"][0]["lines"][0]["who"] == "jie"   # 名字换成 id
    assert detail["estimate"]["lines"] == 4

    # 只重写一段：其余段原样抄过来，source=rewrite，from_version 记上
    monkeypatch.setattr(script_router, "DISPATCH", draft("B02"))
    r = client.post(f"{API}/{sid}/generate", json={"blocks": ["B02"], "note": "分歧再明显一点", "from_version": "V1"})
    assert r.status_code == 200 and wait(r.json()["task_id"])["status"] == "succeeded"
    spec = client.get(f"{API}/{sid}").json()["spec"]
    assert len(spec["versions"]) == 2 and spec["versions"][1]["source"] == "rewrite"
    assert spec["versions"][1]["from_version"] == "V1"
    assert spec["versions"][1]["blocks"][0]["lines"] == spec["versions"][0]["blocks"][0]["lines"]
    # 段号写错 400（一次模型请求都不发），版本写错 404
    assert client.post(f"{API}/{sid}/generate", json={"blocks": ["B09"]}).status_code == 400
    assert client.post(f"{API}/{sid}/generate", json={"from_version": "V9"}).status_code == 404


def test_generate_rejects_empty_cast_and_outline_before_spending(project, monkeypatch):
    calls: list[tuple] = []

    def counting(*args, **kwargs):
        calls.append(args)
        return ({"blocks": []}, None, 0, "settings")

    monkeypatch.setattr(script_router, "DISPATCH", counting)
    empty = client.post(API, json=body(speakers=[], blocks=[], versions=[], current="")).json()["id"]
    r = client.post(f"{API}/{empty}/generate", json={})
    assert r.status_code == 400 and "角色表是空的" in r.json()["detail"]
    no_blocks = client.post(API, json=body(blocks=[], versions=[], current="")).json()["id"]
    r = client.post(f"{API}/{no_blocks}/generate", json={})
    assert r.status_code == 400 and "还没有段落骨架" in r.json()["detail"]
    # 「只重写这几段」没有垫底的版本
    fresh = client.post(API, json=body(versions=[], current="")).json()["id"]
    r = client.post(f"{API}/{fresh}/generate", json={"blocks": ["B01"]})
    assert r.status_code == 400 and "还没有可以基于的版本" in r.json()["detail"]
    assert calls == []          # 三条都拦在 dispatch 之前


def test_generate_is_busy_once(project, monkeypatch):
    sid = client.post(API, json=body()).json()["id"]

    def slow(*a, **k):
        time.sleep(0.4)
        return ({"blocks": [{"id": "B01", "lines": [{"who": "jie", "text": "x"}]}]}, None, 0, "settings")

    monkeypatch.setattr(script_router, "DISPATCH", slow)
    first = client.post(f"{API}/{sid}/generate", json={})
    assert first.status_code == 200
    assert client.post(f"{API}/{sid}/generate", json={}).status_code == 409
    assert client.get(f"{API}/{sid}").json()["busy"]["draft"] == first.json()["task_id"]
    wait(first.json()["task_id"])


# ---------------------------------------------------------------------------- 采用 / 手写另存 / 删版本


def test_pick_hand_save_and_delete_version(project, monkeypatch):
    sid = client.post(API, json=body()).json()["id"]
    # 手写另存：追加一版，source=hand，current 不动
    r = client.post(f"{API}/{sid}/versions", json={
        "label": "硬气一点", "from_version": "V1",
        "blocks": [{"id": "B01", "lines": [{"who": "jie", "text": "我十点以后不看手机。"}]},
                   {"id": "B02", "lines": [{"who": "lin", "text": "我不这么看。"}]}]})
    assert r.status_code == 200 and r.json()["version"] == "V2"
    data = r.json()
    assert data["spec"]["current"] == "V1" and data["spec"]["versions"][1]["source"] == "hand"
    assert data["spec"]["versions"][1]["label"] == "硬气一点" and data["spec"]["versions"][1]["from_version"] == "V1"
    assert [v["id"] for v in data["versions"]] == ["V1", "V2"]
    # 采用 V2
    picked = client.post(f"{API}/{sid}/pick", json={"version": "V2"})
    assert picked.status_code == 200 and picked.json()["spec"]["current"] == "V2"
    assert picked.json()["current_version"]["id"] == "V2"
    assert client.post(f"{API}/{sid}/pick", json={"version": "V9"}).status_code == 404
    # 空串能过 pydantic，但它走的是「不给版本」那条回落路径——采用一个没说清是哪一版的东西没有意义，400 不是 500
    blank = client.post(f"{API}/{sid}/pick", json={"version": ""})
    assert blank.status_code == 400 and "没说要采用哪一版" in blank.json()["detail"]
    empty = client.post(API, json=body(versions=[], current="")).json()["id"]
    assert client.post(f"{API}/{empty}/pick", json={"version": ""}).status_code == 400
    # 删版本：采用中的那一版不能删；删掉另一版后 from_version 那条线断开
    assert client.delete(f"{API}/{sid}/versions/V2").status_code == 400
    assert client.delete(f"{API}/{sid}/versions/V9").status_code == 404
    gone = client.delete(f"{API}/{sid}/versions/V1")
    assert gone.status_code == 200 and gone.json()["deleted"] == "V1" and gone.json()["unlinked"] == ["V2"]
    assert [v["id"] for v in gone.json()["spec"]["versions"]] == ["V2"]
    assert gone.json()["spec"]["versions"][0]["from_version"] == ""
    # 最后一版不能删（另起一份没采用任何一版的，否则先撞上「采用中的那一版」那条）
    lone = client.post(API, json=body(current="")).json()["id"]
    last = client.delete(f"{API}/{lone}/versions/V1")
    assert last.status_code == 400 and "最后一版" in last.json()["detail"]
    # 手写另存时 from_version 写错 404
    assert client.post(f"{API}/{sid}/versions", json={"from_version": "V9", "blocks": []}).status_code == 404


def test_a_version_added_while_nothing_is_adopted_is_the_one_adopted(project, monkeypatch):
    """契约 §3：项目还没采用任何一版时，**新加的这一版**自动采用。``current`` 可以被整体保存清空（版本表里还有旧版），
    这时手写另存 / 生成出来的新版要被采用，而不是最老的 V1——「试」页的确认框在这个状态下说的就是「这一版会直接采用」。"""

    sid = client.post(API, json=body(current="")).json()["id"]      # 有 V1，没采用
    lines = [{"id": "B01", "lines": [{"who": "jie", "text": "十点以后我不看手机。"}]}]
    r = client.post(f"{API}/{sid}/versions", json={"blocks": lines})
    assert r.status_code == 200 and r.json()["version"] == "V2" and r.json()["spec"]["current"] == "V2"

    spec = client.get(f"{API}/{sid}").json()["spec"]
    assert client.put(f"{API}/{sid}", json={**spec, "current": ""}).json()["spec"]["current"] == ""
    monkeypatch.setattr(script_router, "DISPATCH", draft())
    task = wait(client.post(f"{API}/{sid}/generate", json={}).json()["task_id"])
    assert task["status"] == "succeeded" and task["result"]["version"] == "V3"
    assert client.get(f"{API}/{sid}").json()["spec"]["current"] == "V3"


def test_deleting_a_version_takes_its_review_record_with_it(project):
    """``next_version_id`` 是「现有 id 里 V 后面最大的数 + 1」，删掉最新一版之后新的一版会拿到同一个 id；
    审稿记录是按版本 id 存文件的，留在盘上就会被一版全新的稿子当成自己的审稿结论（还显示「可能过时」）。"""

    from script import review as R
    from script import spec as S

    sid = client.post(API, json=body()).json()["id"]
    lines = [{"id": "B01", "lines": [{"who": "jie", "text": "十点以后我不看手机。"}]}]
    assert client.post(f"{API}/{sid}/versions", json={"blocks": lines}).json()["version"] == "V2"
    spec = S.load(sid, project_root=project)
    record = R.review_path(spec, "V2")
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"checklist_id": "x", "spec_hash": "y",
                                  "result": {"items": [{"verdict": "violated"}]}}), encoding="utf-8")
    assert {v["id"]: v for v in client.get(f"{API}/{sid}").json()["versions"]}["V2"]["review"] is not None
    assert client.delete(f"{API}/{sid}/versions/V2").status_code == 200
    assert not record.exists()
    # 新的一版又叫 V2，但它没审过
    assert client.post(f"{API}/{sid}/versions", json={"blocks": lines}).json()["version"] == "V2"
    assert {v["id"]: v for v in client.get(f"{API}/{sid}").json()["versions"]}["V2"]["review"] is None


# ---------------------------------------------------------------------------- 审稿（只读 GET + 拦截）


def test_review_get_is_free_and_post_checks_preconditions(project, monkeypatch):
    sid = client.post(API, json=body()).json()["id"]
    state = client.get(f"{API}/{sid}/review").json()
    assert state["review"] is None and state["stale"] is False and state["problems"] == []
    assert state["version"] == "V1" and state["cost_note"] == "发 1 次文本模型请求"
    assert state["checklist"]["id"]           # 播客目标 → 真人感清单
    assert client.get(f"{API}/{sid}/review?version=V9").status_code == 404

    from storyvideo import scriptgen

    monkeypatch.setattr(scriptgen, "scriptgen_backend", lambda: "unconfigured")
    assert client.post(f"{API}/{sid}/review", json={}).status_code == 400          # 没配文本模型
    monkeypatch.setattr(script_router, "DISPATCH", lambda *a, **k: ({"summary": "", "items": []}, None, 0, "settings"))
    # 没有版本 / 没有台词
    empty = client.post(API, json=body(versions=[], current="")).json()["id"]
    r = client.post(f"{API}/{empty}/review", json={})
    assert r.status_code == 400 and "还没有任何版本" in r.json()["detail"]
    blank = client.post(API, json=body(versions=[{"id": "V1", "source": "hand", "blocks": []}], current="V1")).json()["id"]
    r = client.post(f"{API}/{blank}/review", json={})
    assert r.status_code == 400 and "还没有台词" in r.json()["detail"]
    assert client.post(f"{API}/{sid}/review", json={"version": "V9"}).status_code == 404
    # 真的起一次：任务种类与 module
    r = client.post(f"{API}/{sid}/review", json={})
    assert r.status_code == 200 and r.json()["version"] == "V1"
    task = wait(r.json()["task_id"])
    assert task["status"] == "succeeded" and task["kind"] == "sc-review" and task["module"] == "script"
    assert task["result"]["version"] == "V1"
    after = client.get(f"{API}/{sid}/review").json()
    assert after["review"] is not None and after["stale"] is False
    assert client.get(f"{API}/{sid}").json()["versions"][0]["review"]["verdicts"] == {"violated": 0, "risk": 0, "ok": 0}


# ---------------------------------------------------------------------------- 归档


def test_archive_moves_the_project_out(project):
    sid = client.post(API, json=body()).json()["id"]
    r = client.delete(f"{API}/{sid}")
    assert r.status_code == 200 and r.json()["archived_to"].startswith(".webui_deleted/script/")
    assert not (project / "content" / "script" / f"{sid}.json").exists()
    assert client.get(API).json() == []
    assert client.delete(f"{API}/{sid}").status_code == 404
