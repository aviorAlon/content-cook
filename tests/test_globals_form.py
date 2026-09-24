"""形态可改（DESIGN §31）：``spec.form`` 通过 ``PUT …/globals`` 改——id / 中文名 / 别名都认、落盘统一成 id；
空值删键；不认识的 400「不认识的形态：…」（英文界面翻译）且不写盘；制作中走 revise 的 globals 同一条路。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from webui import deps, revise
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import project as project_router

client = TestClient(app)


def _spec() -> dict:
    return {
        "id": "FORM-G", "output_root": "output/story_video/FORM-G", "prompt_language": "zh",
        "frame_style": "真实拍摄。", "scene_anchor": "9:16。", "camera": "手持。", "neg": "字幕", "subtitle": {"language": "zh"},
        "camera_profile": "dark_comedy_vertical",
        "cast": {"lin": {"name": "林小满", "block": "黑发。"}},
        "shots": [
            {"id": "S1", "dur": 5, "cast": ["lin"], "shot": "中景，平视", "art": "她坐着。", "motion": "她抬头。", "lines": []},
            {"id": "S2", "dur": 5, "cast": ["lin"], "shot": "近景，平视", "art": "她看屏幕。", "motion": "她伸手。", "lines": []},
        ],
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(project_router, "PROJECT_ROOT", tmp_path)
    path = content_dir / "FORM-G.json"
    path.write_text(json.dumps(_spec(), ensure_ascii=False), encoding="utf-8")
    return path


def _saved(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_globals_put_accepts_removes_and_rejects_form(project):
    assert "form" in revise.GLOBAL_KEYS
    # 中文名 → 落盘成 id；summary 带名字
    r = client.put("/api/episodes/storyvideo/FORM-G/globals", json={"values": {"form": "横屏短片"}})
    assert r.status_code == 200, r.text
    assert _saved(project)["form"] == "horizontal_short"
    assert client.get("/api/episodes/storyvideo/FORM-G/summary").json()["form"] == {"id": "horizontal_short", "name": "横屏短片"}
    # id 与别名也认
    assert client.put("/api/episodes/storyvideo/FORM-G/globals", json={"values": {"form": "vertical_drama"}}).status_code == 200
    assert _saved(project)["form"] == "vertical_drama"
    # 不认识的：400、中文原句、英文界面翻译，且不写盘
    bad = client.put("/api/episodes/storyvideo/FORM-G/globals", json={"values": {"form": "火星形态"}})
    assert bad.status_code == 400 and bad.json()["detail"] == "不认识的形态：火星形态"
    assert client.put("/api/episodes/storyvideo/FORM-G/globals", json={"values": {"form": "火星形态"}},
                      headers={"X-StoryStudio-Lang": "en"}).json()["detail"] == 'Unknown form "火星形态"'
    assert _saved(project)["form"] == "vertical_drama"
    # 空 = 删键（None 和 "" 都行）
    assert client.put("/api/episodes/storyvideo/FORM-G/globals", json={"values": {"form": None}}).status_code == 200
    assert "form" not in _saved(project)
    assert client.put("/api/episodes/storyvideo/FORM-G/globals", json={"values": {"form": "短剧"}}).status_code == 200
    assert client.put("/api/episodes/storyvideo/FORM-G/globals", json={"values": {"form": ""}}).status_code == 200
    assert "form" not in _saved(project)


def test_apply_mutation_normalizes_form_for_revise():
    raw = _spec()
    out = revise.apply_mutation(raw, {"kind": "globals", "values": {"form": "Landscape short film"}})
    assert out["form"] == "horizontal_short" and "form" not in raw
    with pytest.raises(revise.ReviseError, match="不认识的形态：火星"):
        revise.apply_mutation(raw, {"kind": "globals", "values": {"form": "火星"}})
