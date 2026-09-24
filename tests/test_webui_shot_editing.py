"""镜头编辑器需要的接口：全字段编辑、新增、删除（带关联检查）、排序；开始制作后一律拒绝。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo.runtime import StoryVideoRunner
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router

client = TestClient(app)


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    return tmp_path


def write_spec(root: Path, episode_id: str = "SHOT-T") -> Path:
    spec = {
        "id": episode_id, "output_root": f"output/story_video/{episode_id}", "prompt_language": "zh",
        "camera_profile": "dark_comedy_vertical",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n",
        "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "block": "林小满：黑发。"}, "office": {"block": "办公区。"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["office", "lin"], "shot": "中景", "art": "a", "motion": "m", "lines": []},
            {"id": "S2", "dur": 4, "cast": ["office", "lin"], "shot": "近景", "art": "a", "motion": "m",
             "ref_frame": "S1", "lines": [{"who": "lin", "zh": "你怎么才来？"}]},
        ],
        "voice_over": {"lines": [{"shot": "S2", "at": 0.5, "zh": "凌晨一点。"}]},
    }
    path = root / "content" / "story_video" / f"{episode_id}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def test_edit_all_fields_and_clear_optional_ones(project):
    path = write_spec(project)
    resp = client.put("/api/episodes/storyvideo/SHOT-T/shots/S2", json={
        "shot": "特写，招财猫占满画面", "art": "金色招财猫", "motion": "唯一动作：铃铛晃一下",
        "sfx": "铃铛", "entry_state": "猫在原位", "exit_state": "猫还在原位",
        "cast": ["office"], "ref_frame": "", "camera_move": "macro", "dur": 6,
        "lines": [{"who": "lin", "zh": "可以，但是要看你的绩效。", "tone": "平稳，口齿清楚"}],
    })
    assert resp.status_code == 200, resp.text
    saved = next(s for s in json.loads(path.read_text(encoding="utf-8"))["shots"] if s["id"] == "S2")
    assert saved["shot"].startswith("特写") and saved["cast"] == ["office"] and "ref_frame" not in saved
    assert saved["camera_move"] == "macro" and saved["dur"] == 6 and saved["sfx"] == "铃铛"
    assert saved["lines"][0]["tone"] == "平稳，口齿清楚"
    assert resp.json()["shot"]["ref_frame"] is None
    # 清掉可选字段；必填字段清空被拒
    assert client.put("/api/episodes/storyvideo/SHOT-T/shots/S2", json={"sfx": ""}).status_code == 200
    assert "sfx" not in next(s for s in json.loads(path.read_text(encoding="utf-8"))["shots"] if s["id"] == "S2")
    assert client.put("/api/episodes/storyvideo/SHOT-T/shots/S2", json={"motion": "  "}).status_code == 400
    # 引用不存在的角色 / 非法运镜：校验挡住，不写盘
    before = path.read_text(encoding="utf-8")
    assert client.put("/api/episodes/storyvideo/SHOT-T/shots/S2", json={"cast": ["nobody"]}).status_code == 400
    assert client.put("/api/episodes/storyvideo/SHOT-T/shots/S2", json={"camera_move": "not_a_move"}).status_code == 400
    assert path.read_text(encoding="utf-8") == before


def test_create_delete_and_reorder(project):
    path = write_spec(project)
    created = client.post("/api/episodes/storyvideo/SHOT-T/shots", json={
        "id": "S1b", "after": "S1", "shot": "近景", "art": "她抬头", "motion": "唯一动作：抬头", "cast": ["lin"], "dur": 5,
    })
    assert created.status_code == 200, created.text
    ids = [s["id"] for s in json.loads(path.read_text(encoding="utf-8"))["shots"]]
    assert ids == ["S1", "S1b", "S2"]
    assert client.post("/api/episodes/storyvideo/SHOT-T/shots", json={"id": "S1b", "shot": "x", "art": "y", "motion": "z"}).status_code == 409
    assert client.post("/api/episodes/storyvideo/SHOT-T/shots", json={"id": "S9", "shot": "x"}).status_code == 400
    head = client.post("/api/episodes/storyvideo/SHOT-T/shots", json={"id": "S0", "after": "", "shot": "全景", "art": "y", "motion": "z"})
    assert head.status_code == 200
    assert [s["id"] for s in json.loads(path.read_text(encoding="utf-8"))["shots"]][0] == "S0"

    # 删除：被参考 / 挂着旁白的镜不能删
    blocked = client.delete("/api/episodes/storyvideo/SHOT-T/shots/S1")
    assert blocked.status_code == 409 and "S2" in blocked.json()["detail"]
    blocked2 = client.delete("/api/episodes/storyvideo/SHOT-T/shots/S2")
    assert blocked2.status_code == 409 and "旁白" in blocked2.json()["detail"]
    assert client.delete("/api/episodes/storyvideo/SHOT-T/shots/S1b").status_code == 200
    assert "S1b" not in [s["id"] for s in json.loads(path.read_text(encoding="utf-8"))["shots"]]

    # 排序
    assert client.put("/api/episodes/storyvideo/SHOT-T/shots-order", json={"order": ["S1", "S0", "S2"]}).status_code == 200
    assert [s["id"] for s in json.loads(path.read_text(encoding="utf-8"))["shots"]] == ["S1", "S0", "S2"]
    assert client.put("/api/episodes/storyvideo/SHOT-T/shots-order", json={"order": ["S1", "S0"]}).status_code == 400


def test_everything_is_refused_after_start(project):
    write_spec(project)
    with StoryVideoRunner(project) as runner:
        runner.prepare("SHOT-T")
    assert client.put("/api/episodes/storyvideo/SHOT-T/shots/S1", json={"sfx": "x"}).status_code == 409
    assert client.post("/api/episodes/storyvideo/SHOT-T/shots", json={"id": "S9", "shot": "x", "art": "y", "motion": "z"}).status_code == 409
    assert client.delete("/api/episodes/storyvideo/SHOT-T/shots/S2").status_code == 409
    assert client.put("/api/episodes/storyvideo/SHOT-T/shots-order", json={"order": ["S2", "S1"]}).status_code == 409
    assert "复制为新版本" in client.put("/api/episodes/storyvideo/SHOT-T/shots/S1", json={"sfx": "x"}).json()["detail"]
