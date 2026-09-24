"""单条视频接口（DESIGN §43，契约 docs/clip-spec.md §5）：CRUD / 检查 / 首帧 / 出片任务 / 采用 / 产物流 /
归档 / 示例 / 断点续等。

出片走假来源（``R.PROVIDER_FACTORY`` 注入），不联网、不花钱。
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clip import prompts as P
from clip import spec as S
from tests.test_clip_generate import FakeProvider, png_bytes
from tests.test_clip_spec import sample
from webui import deps, tasks
from webui.app import app
from webui.routers import clip as R

client = TestClient(app)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(R, "PROVIDER_FACTORY", None)
    (tmp_path / "content" / "clip").mkdir(parents=True)
    tasks._running.clear()
    return tmp_path


def create(**extra) -> dict:
    r = client.post("/api/clip", json=sample(**extra))
    assert r.status_code == 200, r.text
    return r.json()


def wait_task(task_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = client.get(f"/api/tasks/{task_id}").json()
        if item["status"] in ("succeeded", "failed"):
            return item
        time.sleep(0.02)
    raise AssertionError("task did not finish")


def generate(clip_id: str, project: Path, monkeypatch, **payload) -> dict:
    monkeypatch.setattr(R, "PROVIDER_FACTORY", lambda: FakeProvider(project / "cache"))
    r = client.post(f"/api/clip/{clip_id}/generate", json=payload)
    assert r.status_code == 200, r.text
    task = wait_task(r.json()["task_id"])
    assert task["status"] == "succeeded", task
    return r.json()


# ---------------------------------------------------------------------------- 编号 / 新建 / 列表


def test_next_id_create_and_list(project):
    nid = client.get("/api/clip/next-id").json()["id"]
    assert nid.startswith("CL-") and nid.endswith("-1")
    created = create()
    assert created["id"] == nid and created["module"] == "clip" and created["title"] == "城市清晨短片"
    assert "output_root" not in created["spec"] and "project_root" not in created["spec"]
    assert created["prompt"].startswith(P.HEAD_GLOBAL[0]) and created["sections"]["timeline"].startswith(P.HEAD_TIMELINE[0])
    assert created["stats"]["beats"] == 2 and created["dialect"] == "generic" and created["aspect_ratio"] == "16:9"
    assert created["busy"] == {"generate": None, "frame": None}
    assert created["attempts"] == [] and created["current_url"] is None
    assert created["first_frame"]["mode"] == "none" and created["first_frame"]["url"] is None
    assert created["progress"] == {"done": False, "attempts": 0, "beats": 2, "duration": 3, "status": "todo"}
    assert (project / "content" / "clip" / f"{nid}.json").is_file()

    second = create()
    assert second["id"].endswith("-2")
    assert client.post("/api/clip", json=sample(id=nid)).status_code == 409
    assert client.post("/api/clip", json=sample(id="bad id")).status_code == 400
    assert client.post("/api/clip", json=sample(title="")).status_code == 400

    rows = client.get("/api/clip").json()
    assert [r["id"] for r in rows] == [nid, second["id"]]
    assert rows[0]["module"] == "clip" and rows[0]["pipeline"] == "clip" and rows[0]["status"] == "ok"
    assert rows[0]["path"] == f"content/clip/{nid}.json" and rows[0]["duration"] == 3 and rows[0]["beats"] == 2
    assert rows[0]["video_url"] is None
    # 坏文件不让列表挂掉
    (project / "content" / "clip" / "CL-BAD.json").write_text("{not json", encoding="utf-8")
    broken = next(r for r in client.get("/api/clip").json() if r["id"] == "CL-BAD")
    assert broken["status"] == "error" and broken["error"]


def test_example_create(project):
    r = client.post("/api/clip/examples/create", json={})
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["example"] == R.DEFAULT_EXAMPLE == "city_morning" and (project / payload["path"]).is_file()
    assert payload["id"].startswith("CL-") and len(payload["spec"]["beats"]) == 10
    assert payload["warnings"] == [] and payload["notes"] == []       # 示例零告警
    assert payload["stats"]["covered"] == 30.0 and payload["stats"]["palette"] == 6
    assert client.post("/api/clip/examples/create", json={"name": "nope"}).status_code == 404
    assert client.post("/api/clip/examples/create", json={"name": "../picturebook/fox_three_socks"}).status_code == 404


# ---------------------------------------------------------------------------- 读 / 写 / 检查 / 删


def test_get_put_and_validation_rejects_without_writing(project):
    clip_id = create()["id"]
    detail = client.get(f"/api/clip/{clip_id}").json()
    assert detail["beats"][0]["line"].startswith("0–1.5 秒：")
    assert client.get("/api/clip/nope").status_code == 404

    body = {**detail["spec"], "title": "改过的标题"}
    body["beats"][0]["picture"] = "薄雾里的城市天际线与电视塔同框"
    saved = client.put(f"/api/clip/{clip_id}", json=body)
    assert saved.status_code == 200
    assert saved.json()["title"] == "改过的标题" and "电视塔" in saved.json()["prompt"]

    bad = client.put(f"/api/clip/{clip_id}", json={**detail["spec"], "video_params": {"duration": 0}})
    assert bad.status_code == 400 and "没通过校验" in bad.json()["detail"]
    assert client.get(f"/api/clip/{clip_id}").json()["title"] == "改过的标题"     # 没写盘
    assert client.put(f"/api/clip/{clip_id}", json={**detail["spec"], "id": "CL-OTHER"}).status_code == 400


def test_lint_endpoint_and_problem_cards(project):
    clip_id = create(continuity={**sample()["continuity"], "last_frame": ""},
                     video_params={"model": "@video", "duration": 30, "resolution": "1080p"})["id"]
    payload = client.get(f"/api/clip/{clip_id}/lint").json()
    assert payload["id"] == clip_id and payload["prompt"].startswith(P.HEAD_GLOBAL[0])
    assert any("没写最后一帧" in w for w in payload["warnings"])
    assert any("节拍覆盖 3 秒" in w for w in payload["warnings"])
    kinds = {c["kind"] for c in payload["problems"]}
    assert "clip" in kinds and all(c["fix"] for c in payload["problems"])
    assert client.get("/api/clip/nope/lint").status_code == 404


def test_delete_archives_spec_and_output(project):
    clip_id = create()["id"]
    output = project / "output" / "clip" / clip_id / "01_video"
    output.mkdir(parents=True)
    (output / f"{clip_id}-1.mp4").write_bytes(b"x")
    r = client.delete(f"/api/clip/{clip_id}")
    assert r.status_code == 200 and r.json()["output_moved"] is True
    assert r.json()["archived_to"].startswith(".webui_deleted/clip/")
    assert not (project / "content" / "clip" / f"{clip_id}.json").exists()
    assert (project / r.json()["archived_to"] / "spec.json").is_file()
    assert client.delete(f"/api/clip/{clip_id}").status_code == 404


# ---------------------------------------------------------------------------- 首帧


def test_first_frame_upload_generate_and_none(project, monkeypatch):
    clip_id = create()["id"]
    data = base64.b64encode(png_bytes()).decode()
    r = client.post(f"/api/clip/{clip_id}/first-frame", json={"mode": "upload", "filename": "ref.png", "data_base64": data})
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["mode"] == "upload" and payload["first_frame"]["mode"] == "upload"
    assert payload["first_frame"]["url"].startswith(f"/api/clip/{clip_id}/artifact/frame/")
    assert client.get(payload["first_frame"]["url"]).status_code == 200
    # 坏输入
    assert client.post(f"/api/clip/{clip_id}/first-frame", json={"mode": "upload", "filename": "x.exe", "data_base64": data}).status_code == 400
    assert client.post(f"/api/clip/{clip_id}/first-frame", json={"mode": "upload", "filename": "x.png", "data_base64": "!!"}).status_code == 400
    assert client.post(f"/api/clip/{clip_id}/first-frame", json={"mode": "upload", "filename": "x.png", "data_base64": ""}).status_code == 400
    assert client.post(f"/api/clip/{clip_id}/first-frame", json={"mode": "nope"}).status_code == 400
    # 清掉
    cleared = client.post(f"/api/clip/{clip_id}/first-frame", json={"mode": "none"}).json()
    assert cleared["first_frame"]["mode"] == "none" and cleared["first_frame"]["asset"] is None
    # 生成：后台任务 clip-frame
    monkeypatch.setattr(R, "PROVIDER_FACTORY", lambda: FakeProvider(project / "cache"))
    started = client.post(f"/api/clip/{clip_id}/first-frame", json={"mode": "generate", "prompt": "只画天际线剪影"})
    assert started.status_code == 200 and started.json()["task_id"] and "1 次生图请求" in started.json()["cost_note"]
    task = wait_task(started.json()["task_id"])
    assert task["status"] == "succeeded" and task["module"] == "clip" and task["kind"] == "clip-frame"
    after = client.get(f"/api/clip/{clip_id}").json()
    assert after["first_frame"]["mode"] == "generate" and after["first_frame"]["url"]
    assert after["spec"]["first_frame"]["prompt"] == "只画天际线剪影"
    assert "只画天际线剪影" in after["frame_prompt"]


def test_first_frame_generate_failure_becomes_a_failed_task(project, monkeypatch):
    clip_id = create()["id"]
    monkeypatch.setattr(R, "PROVIDER_FACTORY", lambda: FakeProvider(project / "cache", frame_fail=True))
    started = client.post(f"/api/clip/{clip_id}/first-frame", json={"mode": "generate"})
    task = wait_task(started.json()["task_id"])
    assert task["status"] == "failed" and "审核" in task["error"]
    assert client.get(f"/api/clip/{clip_id}").json()["first_frame"]["error"]


# ---------------------------------------------------------------------------- 出片 / 采用 / 产物流


def test_generate_reports_cost_and_writes_a_take(project, monkeypatch):
    clip_id = create()["id"]
    started = generate(clip_id, project, monkeypatch)
    assert started["model"] == "@video" and started["duration"] == 3
    assert started["cost_note"] == "1 次视频请求（@video，3 秒）；请求没变且片子已在的会跳过"
    detail = client.get(f"/api/clip/{clip_id}").json()
    assert detail["status"]["done"] is True and detail["progress"]["status"] == "done"
    assert detail["current_url"] == f"/api/clip/{clip_id}/artifact/video/{clip_id}-1.mp4"
    assert detail["attempts"][0]["current"] is True and detail["attempts"][0]["url"] == detail["current_url"]
    stream = client.get(detail["current_url"])
    assert stream.status_code == 200 and stream.headers["content-type"] == "video/mp4"
    assert stream.content == b"fake-mp4-bytes"
    # 任务记录带模块
    task = tasks.list_tasks(clip_id, "clip")[0]
    assert task["kind"] == "clip-generate" and task["module"] == "clip"


def test_generate_refuses_without_beats_or_first_frame(project, monkeypatch):
    empty = client.post("/api/clip", json={"title": "空片子", "style_anchor": "写实"}).json()["id"]
    r = client.post(f"/api/clip/{empty}/generate", json={})
    assert r.status_code == 400 and "还没有节拍" in r.json()["detail"]
    waiting = create(first_frame={"mode": "upload"})["id"]
    r = client.post(f"/api/clip/{waiting}/generate", json={})
    assert r.status_code == 425 and "还没有上传首帧" in r.json()["detail"]
    assert client.post("/api/clip/nope/generate", json={}).status_code == 404


def test_generate_is_busy_while_one_is_running(project, monkeypatch):
    clip_id = create()["id"]
    monkeypatch.setattr(R, "PROVIDER_FACTORY", lambda: FakeProvider(project / "cache"))
    tasks._running[(clip_id, "clip-generate")] = "fake-task"
    tasks._write("fake-task", task_id="fake-task", kind="clip-generate", episode_id=clip_id, module="clip",
                 status="running", created_at=time.time())
    r = client.post(f"/api/clip/{clip_id}/generate", json={})
    assert r.status_code == 409 and "已经在跑" in r.json()["detail"]
    assert client.get(f"/api/clip/{clip_id}").json()["busy"]["generate"] == "fake-task"


def test_pick_switches_the_current_take(project, monkeypatch):
    clip_id = create()["id"]
    generate(clip_id, project, monkeypatch)
    generate(clip_id, project, monkeypatch, force=True)
    detail = client.get(f"/api/clip/{clip_id}").json()
    assert [a["file"] for a in detail["attempts"]] == [f"{clip_id}-1.mp4", f"{clip_id}-2.mp4"]
    assert detail["spec"]["current"] == f"{clip_id}-2.mp4"
    picked = client.post(f"/api/clip/{clip_id}/pick", json={"attempt": f"{clip_id}-1.mp4"})
    assert picked.status_code == 200 and picked.json()["spec"]["current"] == f"{clip_id}-1.mp4"
    assert picked.json()["attempts"][0]["current"] is True and picked.json()["attempts"][1]["current"] is False
    assert client.post(f"/api/clip/{clip_id}/pick", json={"attempt": f"{clip_id}-9.mp4"}).status_code == 400


@pytest.mark.parametrize("name", ["../../secret.mp4", "..%2Fsecret.mp4", "sub/x.mp4", ".hidden.mp4", "x.txt", "nope.mp4"])
def test_artifact_paths_are_safe(project, monkeypatch, name):
    clip_id = create()["id"]
    generate(clip_id, project, monkeypatch)
    (project / "secret.mp4").write_bytes(b"secret")
    assert client.get(f"/api/clip/{clip_id}/artifact/video/{name}").status_code == 404
    assert client.get(f"/api/clip/{clip_id}/artifact/frame/{name}").status_code == 404
    # 只有 attempts 里记过的文件名能取
    stray = S.video_dir(S.load(clip_id, project_root=project)) / "stray.mp4"
    stray.write_bytes(b"x")
    assert client.get(f"/api/clip/{clip_id}/artifact/video/stray.mp4").status_code == 404


# ---------------------------------------------------------------------------- 断点续等


def test_resume_clip_watchers_picks_up_inflight_takes(project, monkeypatch):
    """服务重启：项目文件里最后一次尝试有 task_id、文件还没落盘 → 起一个只轮询的任务把它接回来。"""

    from webui import generate as WG

    clip_id = create()["id"]
    spec = S.load(clip_id, project_root=project)
    cache = project / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "remote.mp4").write_bytes(b"fake-mp4-bytes")
    spec["attempts"] = [{"file": f"{clip_id}-1.mp4", "created_at": time.time(), "model": "@video",
                         "request_hash": "h", "task_id": f"file:{cache / 'remote.mp4'}", "error": None}]
    S.save(spec)
    assert client.get(f"/api/clip/{clip_id}").json()["status"]["status"] == "running"

    monkeypatch.setattr(R, "PROVIDER_FACTORY", lambda: FakeProvider(cache))
    resumed = WG.resume_inflight_watchers(project)
    assert [r["episode_id"] for r in resumed] == [clip_id]
    assert resumed[0]["module"] == "clip"
    task = wait_task(resumed[0]["task_id"])
    assert task["status"] == "succeeded" and task["result"]["resumed"] is True
    after = client.get(f"/api/clip/{clip_id}").json()
    assert after["spec"]["current"] == f"{clip_id}-1.mp4" and after["status"]["done"] is True
    # 没有在飞的任务时不起任何任务（也就不会花钱）
    tasks._running.clear()
    assert R.resume_clip_watchers(project) == []


def test_resume_skips_broken_specs(project):
    (project / "content" / "clip" / "CL-BAD.json").write_text("{not json", encoding="utf-8")
    assert R.resume_clip_watchers(project) == []


# ---------------------------------------------------------------------------- 英文


def test_english_detail_is_translated(project):
    clip_id = create(continuity={**sample()["continuity"], "last_frame": ""})["id"]
    payload = client.get(f"/api/clip/{clip_id}/lint", headers={"X-StoryStudio-Lang": "en"}).json()
    assert payload["warnings"], payload
    r = client.get("/api/clip/nope", headers={"X-StoryStudio-Lang": "en"})
    assert r.status_code == 404 and json.dumps(r.json(), ensure_ascii=False)
