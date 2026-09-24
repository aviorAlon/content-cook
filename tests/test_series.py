"""系列交接（DESIGN §32）：记忆文件读写、起草时注入「上一集交接」、角色 block 覆盖、列表/详情/交接接口、导出顺手交接。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from storyvideo import scriptgen, series
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import export as export_router
from webui.routers import post as post_router
from webui.routers import scripts as scripts_router
from webui.routers import series as series_router

CONTRACT = {
    "premise": "p", "hook": "齐曼当众要求出示完整排班记录", "cliffhanger": "叶澄把备用钥匙交给齐曼",
    "must_pay_off": ["订单缺口的原因", "冷柜后的账本被发现"], "carry_over": ["齐曼不知道冷柜后的账本", "审查员早晨进场"],
}


def _spec(episode_id="EP-1", episode_no=1, contract=CONTRACT):
    spec = {
        "id": episode_id, "output_root": f"output/story_video/{episode_id}", "prompt_language": "zh",
        "title_zh": "厨房关停", "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"ye": {"name": "叶澄", "block": "三十岁女性，黑色短发，灰色围裙。"}, "qi": {"name": "齐曼", "block": "二十五岁女性，长发束起。"},
                 "kitchen": {"block": "共享厨房，不锈钢灶台。"}},
        "series": {"id": "kitchen", "episode_no": episode_no},
        "shots": [
            {"id": "S1", "dur": 5, "cast": ["ye", "qi"], "shot": "中景，平视", "art": "清晨", "motion": "齐曼要求出示排班记录", "lines": [{"who": "qi", "zh": "把完整的排班记录拿出来。"}]},
            {"id": "S2", "dur": 5, "cast": ["ye"], "shot": "近景，平视", "art": "叶澄", "motion": "订单缺口是她调的灶位", "lines": []},
            {"id": "S3", "dur": 5, "cast": ["ye", "qi"], "shot": "特写，平视", "art": "钥匙", "motion": "叶澄把备用钥匙交给齐曼", "lines": []},
        ],
    }
    if contract is not None:
        spec["episode_contract"] = contract
    return spec


@pytest.fixture
def root(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, export_router, scripts_router, series_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    for module in (deps, episodes_router, scripts_router):
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(scripts_router, "JOBS_DIR", tmp_path / "jobs")
    (content_dir / "EP-1.json").write_text(json.dumps(_spec(), ensure_ascii=False), encoding="utf-8")
    return tmp_path


# ---- 记忆文件


def test_upsert_and_load(root):
    out = series.upsert_episode(root, _spec())
    assert out["series_id"] == "kitchen" and out["episodes"] == 1
    memory = series.load_series(root, "kitchen")
    entry = memory["episodes"][0]
    assert entry["episode_id"] == "EP-1" and entry["episode_no"] == 1 and entry["title"] == "厨房关停"
    assert entry["established"] == ["订单缺口的原因"]                 # 正文里找得到的承诺才算已成立
    assert entry["open_threads"] == CONTRACT["carry_over"]
    assert entry["cast"]["ye"] == {"name": "叶澄", "block": "三十岁女性，黑色短发，灰色围裙。"}
    assert entry["cast"]["kitchen"]["name"] == ""
    # 同一集再写是替换，不是追加；第二集追加并按集数排
    series.upsert_episode(root, _spec())
    series.upsert_episode(root, _spec("EP-2", 2))
    memory = series.load_series(root, "kitchen")
    assert [e["episode_id"] for e in memory["episodes"]] == ["EP-1", "EP-2"]
    assert series.list_series(root) == [{"id": "kitchen", "episodes": 2, "latest_no": 2, "latest_title": "厨房关停"}]
    assert series.previous_entry(root, "kitchen", 2)["episode_id"] == "EP-1"
    assert series.previous_entry(root, "kitchen", 3)["episode_id"] == "EP-2"
    assert series.previous_entry(root, "kitchen", None)["episode_id"] == "EP-2"
    assert series.previous_entry(root, "kitchen", 1) is None
    assert series.previous_entry(root, "nothing", 2) is None
    with pytest.raises(ValueError, match="没有填系列"):
        series.upsert_episode(root, {**_spec(), "series": None})
    with pytest.raises(ValueError):
        series.series_path(root, "../evil")


def test_handoff_section_and_cast_override(root):
    entry = series.build_entry(_spec())
    text = series.handoff_section(entry)
    assert text.startswith("上一集交接")
    assert "EP-1（第 1 集）《厨房关停》" in text
    assert "上一集结尾（本集开场要从这里接）：叶澄把备用钥匙交给齐曼" in text
    assert "- 齐曼不知道冷柜后的账本" in text and "- 审查员早晨进场" in text
    assert "已成立的事实" in text and "- 订单缺口的原因" in text and "冷柜后的账本被发现" not in text.split("已成立的事实")[1].split("延续的角色")[0]
    assert "ye（叶澄）：三十岁女性，黑色短发，灰色围裙。" in text     # block 原样
    draft = {"cast": {"ye": {"block": "模型改写过的叶澄", "anchor_prompt": "a"}, "new": {"block": "新人"}}}
    assert series.apply_cast_memory(draft, entry) == ["ye"]
    assert draft["cast"]["ye"] == {"block": "三十岁女性，黑色短发，灰色围裙。", "anchor_prompt": "a", "name": "叶澄"}
    assert draft["cast"]["new"] == {"block": "新人"}


# ---- 起草时注入


def test_generate_draft_injects_handoff(monkeypatch, root):
    seen = {}

    def fake_dispatch(system_prompt, user_prompt, *, json_schema):
        seen["user"] = user_prompt
        return {"shots": [], "cast": {}}, None, 1, "settings"

    monkeypatch.setattr(scriptgen, "_dispatch", fake_dispatch)
    entry = series.build_entry(_spec())
    scriptgen.generate_draft(episode_id="EP-2", direction="续", target_seconds=60, camera_profile="dark_comedy_vertical",
                             prompt_language="zh", cast_hint=None, series_handoff=entry)
    assert "上一集交接" in seen["user"] and "ye（叶澄）：三十岁女性，黑色短发，灰色围裙。" in seen["user"]
    scriptgen.generate_draft(episode_id="EP-2", direction="续", target_seconds=60, camera_profile="dark_comedy_vertical",
                             prompt_language="zh", cast_hint=None)
    assert "上一集交接" not in seen["user"]


def test_draft_job_with_series_overrides_cast_and_stamps_series(monkeypatch, root):
    R = scripts_router
    series.upsert_episode(root, _spec())
    seen = {}

    def fake_generate_draft(**kw):
        seen.update(kw)
        return {"title_zh": "第二集", "logline": "l", "frame_style": "f", "scene_anchor": "s", "camera": "c", "neg": "n",
                "cast": {"ye": {"block": "被模型改写的叶澄", "anchor_prompt": "a"}, "lu": {"block": "新角色"}},
                "shots": [{"id": "S1", "dur": 5, "cast": ["ye"], "shot": "中景", "art": "a", "motion": "m", "lines": []}],
                "episode_contract": {"hook": "h", "must_pay_off": ["x"]}}, None, 1, "settings"

    monkeypatch.setattr(R, "generate_draft", fake_generate_draft)
    monkeypatch.setattr(R.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    client = TestClient(app)
    r = client.post("/api/scripts/jobs/draft", json={"episode_id": "EP-2", "direction": "续", "target_seconds": 60, "camera_profile": "dark_comedy_vertical",
                                                     "series_id": "kitchen", "episode_no": 2})
    assert r.status_code == 200, r.text
    assert seen["series_handoff"]["episode_id"] == "EP-1"
    job = client.get(f"/api/scripts/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded", job
    assert job["params"]["series_id"] == "kitchen" and job["params"]["episode_no"] == 2
    spec = job["result"]["spec"]
    assert spec["series"] == {"id": "kitchen", "episode_no": 2}
    assert spec["cast"]["ye"]["block"] == "三十岁女性，黑色短发，灰色围裙。" and spec["cast"]["lu"]["block"] == "新角色"
    assert spec["episode_contract"] == {"premise": "", "hook": "h", "cliffhanger": "", "must_pay_off": ["x"], "carry_over": []}
    # 系列 id 不合法在建 job 前就拦
    assert client.post("/api/scripts/jobs/draft", json={"direction": "x", "target_seconds": 60, "camera_profile": "p", "series_id": "bad id"}).status_code == 400
    assert client.post("/api/scripts/jobs/draft", json={"direction": "x", "target_seconds": 60, "camera_profile": "p", "series_id": "k", "episode_no": 0}).status_code == 400


# ---- 接口


def test_series_endpoints_and_handoff(root):
    client = TestClient(app)
    assert client.get("/api/series").json() == []
    assert client.get("/api/series/kitchen").status_code == 404
    assert client.get("/api/series/bad%20id").status_code == 400
    r = client.post("/api/episodes/storyvideo/EP-1/series/handoff")
    assert r.status_code == 200, r.text
    assert r.json()["entry"]["established"] == ["订单缺口的原因"]
    assert (root / "content" / "series" / "kitchen.json").is_file()
    assert client.get("/api/series").json()[0]["id"] == "kitchen"
    assert client.get("/api/series/kitchen").json()["episodes"][0]["episode_id"] == "EP-1"
    assert client.post("/api/episodes/storyvideo/NOPE/series/handoff").status_code == 404
    # 没填系列的项目 400
    (root / "content" / "story_video" / "EP-9.json").write_text(json.dumps({**_spec("EP-9"), "series": None}, ensure_ascii=False), encoding="utf-8")
    r = client.post("/api/episodes/storyvideo/EP-9/series/handoff")
    assert r.status_code == 400 and "没有填系列" in r.text


def test_export_runs_handoff(root, monkeypatch):
    from webui import export as export_lib

    monkeypatch.setattr(export_lib, "build_package", lambda spec, raw, dest: {"folder": str(dest), "zip": "", "name": "x.zip", "files": 0, "bytes": 0, "deliverable": None, "shots": []})
    from webui import tasks

    seen = {}

    def fake_start(kind, episode_id, fn, *, params=None):
        seen["result"] = fn()
        return "t1"

    monkeypatch.setattr(tasks, "start", fake_start)
    client = TestClient(app)
    assert client.post("/api/episodes/storyvideo/EP-1/export").status_code == 200
    assert seen["result"]["series_handoff"] == "kitchen"
    assert series.load_series(root, "kitchen")["episodes"][0]["episode_id"] == "EP-1"
