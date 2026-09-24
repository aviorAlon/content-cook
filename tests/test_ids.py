"""id 自动生成：用户只填名字；音色 / 角色 / 镜次 / 示例复制不传 id 也能建，传了照旧校验。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import ids
from webui import deps, revise as R
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router
from webui.routers import scripts as scripts_router

client = TestClient(app)


def test_slug_and_unique():
    assert ids.slugify("Girl Calm") == "girl_calm"
    assert ids.slugify("林小满") == ""
    assert ids.slugify("林小满 Lin", fallback="x") == "lin"
    assert ids.slugify("  ---  ", fallback="cast1") == "cast1"
    assert ids.unique_id("a", ["a", "a-2"]) == "a-3"
    assert ids.numbered_id("cast", ["cast1", "cast3"]) == "cast2"
    assert ids.id_from_name("小满", ["cast1"], prefix="cast") == "cast2"
    assert ids.id_from_name("Boss", ["boss"], prefix="cast") == "boss-2"
    assert ids.is_valid_id("girl_calm") and not ids.is_valid_id("bad id") and not ids.is_valid_id("-x")


def test_next_shot_and_episode_ids():
    assert ids.next_shot_id(["S1", "S2_WISH", "S3_LOOK"]) == "S4"
    assert ids.next_shot_id([]) == "S1"
    assert ids.next_shot_id(["INTRO", "s7"]) == "S8"
    assert ids.next_shot_id(["S1", "S2", "S3"]) == "S4"
    assert ids.next_episode_id(["EP-20260907-1"], today=date(2026, 9, 7)) == "EP-20260907-2"
    assert ids.next_episode_id([], prefix="MY", today=date(2026, 9, 7)) == "MY-20260907-1"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, project_router, scripts_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    for module in (deps, episodes_router, scripts_router):
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    spec = {
        "id": "ID-T", "output_root": "output/story_video/ID-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "block": "黑发。"}},
        "shots": [{"id": "S1", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m", "lines": []},
                  {"id": "S2_WISH", "dur": 4, "cast": ["lin"], "shot": "中景", "art": "a", "motion": "m", "lines": []}],
    }
    (content_dir / "ID-T.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_voice_create_without_id(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    a = client.post("/api/voices", json={"name": "小满", "voice": "zh-CN-XiaoxiaoNeural"}).json()
    b = client.post("/api/voices", json={"name": "小满", "voice": "zh-CN-XiaoxiaoNeural"}).json()
    c = client.post("/api/voices", json={"name": "Calm Narrator", "voice": "v"}).json()
    assert a["id"] == "voice1" and b["id"] == "voice2" and c["id"] == "calm_narrator"
    assert client.post("/api/voices", json={"id": "bad id", "name": "x", "voice": "v"}).status_code == 400
    assert client.post("/api/voices", json={"id": "voice1", "name": "x", "voice": "v"}).status_code == 400


def test_cast_create_without_id(project):
    zh = client.post("/api/episodes/storyvideo/ID-T/cast", json={"name": "老板", "block": "西装。"})
    assert zh.status_code == 200, zh.text
    assert zh.json()["id"] == "cast1"
    en = client.post("/api/episodes/storyvideo/ID-T/cast", json={"name": "Boss Wang", "block": "西装。"}).json()
    assert en["id"] == "boss_wang"
    dup = client.post("/api/episodes/storyvideo/ID-T/cast", json={"name": "Boss Wang", "block": "x"}).json()
    assert dup["id"] == "boss_wang-2"
    assert client.post("/api/episodes/storyvideo/ID-T/cast", json={"id": "bad id!", "block": "x"}).status_code == 400
    assert client.post("/api/episodes/storyvideo/ID-T/cast", json={"id": "lin", "block": "x"}).status_code == 409


def test_shot_create_without_id(project):
    r = client.post("/api/episodes/storyvideo/ID-T/shots", json={"shot": "全景", "art": "y", "motion": "z"})
    assert r.status_code == 200, r.text
    assert r.json()["shot"]["id"] == "S3"
    head = client.post("/api/episodes/storyvideo/ID-T/shots", json={"after": "", "shot": "全景", "art": "y", "motion": "z"}).json()
    assert head["shot"]["id"] == "S4"
    ids_now = [s["id"] for s in client.get("/api/episodes/storyvideo/ID-T").json()["shots"]]
    assert ids_now == ["S4", "S1", "S2_WISH", "S3"]


def test_revise_mutations_without_id(project):
    raw = json.loads((project / "content" / "story_video" / "ID-T.json").read_text(encoding="utf-8"))
    out = R.apply_mutation(raw, {"kind": "cast_create", "name": "老板", "block": "西装。"})
    assert "cast1" in out["cast"] and out["cast"]["cast1"]["name"] == "老板"
    out = R.apply_mutation(raw, {"kind": "shot_create", "shot": "全景", "art": "y", "motion": "z"})
    assert [s["id"] for s in out["shots"]] == ["S1", "S2_WISH", "S3"]
    with pytest.raises(R.ReviseError):
        R.apply_mutation(raw, {"kind": "cast_create", "id": "lin", "block": "x"})


def test_example_copy_without_id_and_next_id(project):
    nxt = client.get("/api/episodes/next-id?prefix=DEMO").json()["id"]
    assert nxt == "DEMO-1"
    r = client.post("/api/examples/first_episode/create", json={})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "DEMO-1"
    assert client.get("/api/episodes/next-id?prefix=DEMO").json()["id"] == "DEMO-2"
    ep = client.get("/api/episodes/next-id").json()["id"]
    assert ep.startswith("EP-") and ep.endswith("-1")
    assert client.post("/api/examples/first_episode/create", json={"episode_id": "bad id"}).status_code == 400
