"""routers/post.py：后期块读写（含冻结后 amend）、台词识别闸与后期合成的后台任务、就绪检查。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo.runtime import StoryVideoRunner
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router

client = TestClient(app)


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    return tmp_path


def write_spec(root: Path, episode_id: str = "POST-T", **extra) -> Path:
    spec = {
        "id": episode_id, "output_root": f"output/story_video/{episode_id}", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n",
        "subtitle": {"language": "zh"},
        "cast": {"a": {"block": "A：黑发。"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["a"], "shot": "中景", "art": "a", "motion": "m",
             "lines": [{"who": "a", "zh": "你怎么才来？"}]},
            {"id": "S2", "dur": 4, "cast": ["a"], "shot": "近景", "art": "a", "motion": "m", "lines": []},
        ],
        "voice_over": {"edge": {"voice": "zh-CN-YunxiNeural"}, "lines": [{"shot": "S1", "at": 0.5, "zh": "凌晨一点。"}]},
        "post_graphics": {"phone_ui_inserts": [{"id": "UI_1", "after": "S1", "dur": 3, "layout": "plain", "lines": ["x"]}]},
    }
    spec.update(extra)
    path = root / "content" / "story_video" / f"{episode_id}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def wait_task(task_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = client.get(f"/api/tasks/{task_id}").json()
        if item["status"] in ("succeeded", "failed"):
            return item
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish")


def test_get_post_layers_reports_blocks_and_readiness(project):
    write_spec(project)
    body = client.get("/api/episodes/storyvideo/POST-T/post-layers").json()
    assert body["locked"] is False
    assert body["voice_over"]["lines"][0]["zh"] == "凌晨一点。"
    assert body["post_graphics"]["phone_ui_inserts"][0]["id"] == "UI_1"
    assert body["readiness"]["missing_videos"] == ["S1", "S2"]
    assert body["readiness"]["has_post_layers"] is True
    assert body["asr"] is None and body["post"] is None
    assert "voice_over" in body["post_only_keys"] and "shots" not in body["post_only_keys"]


def test_put_post_layers_on_draft_writes_file(project):
    path = write_spec(project)
    resp = client.put("/api/episodes/storyvideo/POST-T/post-layers", json={
        "voice_over": {"edge": {"voice": "zh-CN-XiaoxiaoNeural"}, "lines": []},
        "design_notes": "改了声线",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "saved"
    assert sorted(resp.json()["changed_keys"]) == ["design_notes", "voice_over"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["voice_over"]["edge"]["voice"] == "zh-CN-XiaoxiaoNeural"
    assert saved["design_notes"] == "改了声线"
    # 生成字段没被碰
    assert saved["neg"] == "n" and len(saved["shots"]) == 2
    # 同样的内容再提交一次：unchanged，不写盘
    again = client.put("/api/episodes/storyvideo/POST-T/post-layers", json={"design_notes": "改了声线"})
    assert again.json()["status"] == "unchanged"


def test_put_post_layers_on_frozen_episode_amends(project):
    path = write_spec(project)
    with StoryVideoRunner(project) as runner:
        runner.prepare("POST-T")
    assert client.get("/api/episodes/storyvideo/POST-T").json()["locked"] is True
    resp = client.put("/api/episodes/storyvideo/POST-T/post-layers", json={
        "post_graphics": {"phone_ui_inserts": [{"id": "UI_1", "after": "S2", "dur": 2, "layout": "plain", "lines": ["y"]}]},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["locked"] is True and body["status"] == "amended"
    assert body["amend"]["changed_keys"] == ["post_graphics"]
    # 修订后 runner.status 仍然认这个 run（快照与哈希已同步）
    with StoryVideoRunner(project) as runner:
        assert runner.status("POST-T")["run_id"] == "POST-T"
    assert json.loads(path.read_text(encoding="utf-8"))["post_graphics"]["phone_ui_inserts"][0]["after"] == "S2"
    # 非法内容：形状校验挡住、不写盘、快照不变
    before = path.read_text(encoding="utf-8")
    bad = client.put("/api/episodes/storyvideo/POST-T/post-layers", json={"voice_over": {"lines": "not-a-list"}})
    assert bad.status_code == 400 and "voice_over" in bad.json()["detail"]
    bad2 = client.put("/api/episodes/storyvideo/POST-T/post-layers", json={"voice_over": {"lines": [{"shot": "NOPE", "zh": "x"}]}})
    assert bad2.status_code == 400 and "NOPE" in bad2.json()["detail"]
    bad3 = client.put("/api/episodes/storyvideo/POST-T/post-layers", json={"post_graphics": {"phone_ui_inserts": [{"after": "S1"}]}})
    assert bad3.status_code == 400 and "id" in bad3.json()["detail"]
    assert path.read_text(encoding="utf-8") == before


def test_asr_task_runs_with_injected_transcriber(project, monkeypatch):
    write_spec(project)
    spec_out = project / "output" / "story_video" / "POST-T" / "02_video"
    spec_out.mkdir(parents=True)
    (spec_out / "S1.mp4").write_bytes(b"fake")
    monkeypatch.setattr(post_router, "readiness", lambda spec: {
        "ffmpeg_ok": True, "asr_available": True, "fonts_ok": True, "fonts_missing": [],
        "videos_total": 2, "missing_videos": ["S2"], "has_post_layers": True})
    import storyvideo.asr_qc as asr_mod
    monkeypatch.setattr(asr_mod, "transcribe_file", lambda path, model="small", language="zh": "你怎么才来")
    resp = client.post("/api/episodes/storyvideo/POST-T/asr-qc", json={"record_retakes": False})
    assert resp.status_code == 200, resp.text
    task = wait_task(resp.json()["task_id"])
    assert task["status"] == "succeeded", task
    rows = {r["shot_id"]: r for r in task["result"]["rows"]}
    assert rows["S1"]["status"] == "pass"
    assert rows["S2"]["status"] == "missing_video"
    assert task["result"]["failing"] == []
    # 报告落盘后，项目详情的镜次上能看到识别结果
    detail = client.get("/api/episodes/storyvideo/POST-T").json()
    assert detail["asr_summary"]["pass"] == 1
    s1 = next(s for s in detail["shots"] if s["id"] == "S1")
    assert s1["asr"]["status"] == "pass"
    # 未锁定 + 要记重摇：拒绝
    denied = client.post("/api/episodes/storyvideo/POST-T/asr-qc", json={"record_retakes": True})
    assert denied.status_code == 409


def test_post_task_precheck_and_run(project, monkeypatch):
    write_spec(project)
    out = project / "output" / "story_video" / "POST-T"
    (out / "02_video").mkdir(parents=True)
    # 缺视频：425 并点名
    resp = client.post("/api/episodes/storyvideo/POST-T/post")
    assert resp.status_code == 425 and "S1" in resp.json()["detail"]
    for sid in ("S1", "S2"):
        (out / "02_video" / f"{sid}.mp4").write_bytes(b"fake")
    monkeypatch.setattr(post_router, "readiness", lambda spec: {
        "ffmpeg_ok": True, "asr_available": False, "fonts_ok": True, "fonts_missing": [],
        "videos_total": 2, "missing_videos": [], "has_post_layers": True})

    def fake_compose(spec, **kwargs):
        dest = Path(spec["output_root"]) / f"{spec['id']}_post.mp4"
        dest.write_bytes(b"mp4")
        return {"output": str(dest), "duration_seconds": 8.0, "timeline": [], "vo": [{}], "ui_inserts": ["x"]}

    monkeypatch.setattr(post_router, "compose_post", fake_compose)
    resp = client.post("/api/episodes/storyvideo/POST-T/post")
    assert resp.status_code == 200, resp.text
    task = wait_task(resp.json()["task_id"])
    assert task["status"] == "succeeded", task
    assert task["result"]["vo_count"] == 1 and task["result"]["ui_inserts"] == 1
    detail = client.get("/api/episodes/storyvideo/POST-T").json()
    assert detail["final_kind"] == "post"


def test_font_status_shape():
    from storyvideo.post_layers import default_fonts, font_status

    fonts = default_fonts()
    assert set(fonts) == {"notebook", "ui", "subtitle"}
    status = font_status()
    assert set(status) == {"ok", "fonts", "missing"}
    assert status["ok"] == (not status["missing"])
