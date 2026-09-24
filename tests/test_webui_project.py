"""routers/project.py：角色与场景、锚图、引用关系图、概览与下一步；deps 的镜次反向引用；错误文案翻译。"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo.runtime import StoryVideoRunner
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router

client = TestClient(app)


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    return tmp_path


def write_spec(root: Path, episode_id: str = "PRJ-T", **extra) -> Path:
    spec = {
        "id": episode_id, "output_root": f"output/story_video/{episode_id}", "prompt_language": "zh",
        "title_zh": "加班夜", "logline": "一句话。",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n",
        "subtitle": {"language": "zh"},
        "cast": {
            "lin": {"name": "林小满", "voice": "女声", "block": "林小满：黑发。"},
            "cat": {"name": "招财猫", "block": "金色招财猫。"},
            "office": {"block": "办公区·深夜。"},
        },
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["office", "lin"], "shot": "中景", "art": "a", "motion": "m", "lines": []},
            {"id": "S2", "dur": 4, "cast": ["office", "lin", "cat"], "shot": "近景", "art": "a", "motion": "m",
             "ref_frame": "S1", "lines": [{"who": "lin", "zh": "你怎么才来？"}]},
        ],
        "voice_over": {"lines": [{"shot": "S1", "at": 0.5, "zh": "凌晨一点。"}]},
        "post_graphics": {"phone_ui_inserts": [{"id": "UI_1", "after": "S2", "dur": 3, "layout": "plain", "lines": ["x"]}],
                          "notebook_text": [{"shot": "S2", "text": "你好"}]},
    }
    spec.update(extra)
    path = root / "content" / "story_video" / f"{episode_id}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def png_base64(size=(8, 8)) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_cast_list_infers_kind_and_usage(project):
    write_spec(project)
    cast = {c["id"]: c for c in client.get("/api/episodes/storyvideo/PRJ-T/cast").json()}
    assert cast["lin"]["kind"] == "character" and cast["lin"]["used_in"] == ["S1", "S2"]
    assert cast["cat"]["kind"] == "character" and cast["cat"]["used_in"] == ["S2"]
    assert cast["office"]["kind"] == "scene" and cast["office"]["used_in"] == ["S1", "S2"]
    assert cast["lin"]["asset_url"] is None


def test_cast_create_update_delete_on_draft(project):
    path = write_spec(project)
    created = client.post("/api/episodes/storyvideo/PRJ-T/cast", json={"id": "boss", "name": "老板", "block": "老板：西装。"})
    assert created.status_code == 200, created.text
    assert created.json()["kind"] == "character" and created.json()["used_in"] == []
    assert client.post("/api/episodes/storyvideo/PRJ-T/cast", json={"id": "boss", "block": "x"}).status_code == 409
    assert client.post("/api/episodes/storyvideo/PRJ-T/cast", json={"id": "bad id!", "block": "x"}).status_code == 400

    updated = client.put("/api/episodes/storyvideo/PRJ-T/cast/lin", json={"voice": "低沉女声", "kind": "character"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["voice"] == "低沉女声"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["cast"]["lin"]["voice"] == "低沉女声" and saved["cast"]["lin"]["name"] == "林小满"
    assert client.put("/api/episodes/storyvideo/PRJ-T/cast/lin", json={"block": "  "}).status_code == 400
    assert client.put("/api/episodes/storyvideo/PRJ-T/cast/nope", json={"block": "x"}).status_code == 404

    # 被镜引用的不能删；没被引用的可以
    used = client.delete("/api/episodes/storyvideo/PRJ-T/cast/office")
    assert used.status_code == 409 and "S1" in used.json()["detail"]
    assert client.delete("/api/episodes/storyvideo/PRJ-T/cast/boss").status_code == 200
    assert "boss" not in json.loads(path.read_text(encoding="utf-8"))["cast"]


def test_cast_edit_refused_after_start_with_affected_shots(project):
    write_spec(project)
    with StoryVideoRunner(project) as runner:
        runner.prepare("PRJ-T")
    resp = client.put("/api/episodes/storyvideo/PRJ-T/cast/cat", json={"block": "银色招财猫。"})
    assert resp.status_code == 409
    assert "S2" in resp.json()["detail"] and "S1" not in resp.json()["detail"].split("（")[1]
    assert client.post("/api/episodes/storyvideo/PRJ-T/cast", json={"id": "x", "block": "x"}).status_code == 409
    upload = client.post("/api/episodes/storyvideo/PRJ-T/cast/lin/asset", json={"filename": "a.png", "data_base64": png_base64()})
    assert upload.status_code == 409


def test_asset_upload_is_stored_relative_and_resolved_for_generation(project):
    path = write_spec(project)
    resp = client.post("/api/episodes/storyvideo/PRJ-T/cast/lin/asset", json={"filename": "lin.png", "data_base64": png_base64()})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["asset"].startswith("content/assets/PRJ-T/lin-") and body["asset"].endswith(".png")
    assert body["width"] == 8
    assert (project / body["asset"]).is_file()
    # spec 文件里是相对路径
    assert json.loads(path.read_text(encoding="utf-8"))["cast"]["lin"]["asset"] == body["asset"]
    # 列表里能拿到预览地址，且能下载
    cast = {c["id"]: c for c in client.get("/api/episodes/storyvideo/PRJ-T/cast").json()}
    assert cast["lin"]["asset_url"] == "/api/episodes/storyvideo/PRJ-T/cast/lin/asset"
    assert client.get(cast["lin"]["asset_url"]).status_code == 200
    # 归一化后是绝对路径，编译器把它作为参考图
    from storyvideo.prompts import frame_reference_asset_ids
    from storyvideo.spec import load_spec

    spec = load_spec("PRJ-T", project_root=project)
    resolved = spec["cast"]["lin"]["asset"]
    assert Path(resolved).is_absolute() and Path(resolved).is_file()
    assert resolved in frame_reference_asset_ids(spec, "S1")
    # 非图片 / 坏 base64
    assert client.post("/api/episodes/storyvideo/PRJ-T/cast/lin/asset", json={"filename": "a.png", "data_base64": "aGVsbG8="}).status_code == 400
    assert client.post("/api/episodes/storyvideo/PRJ-T/cast/lin/asset", json={"filename": "a.png", "data_base64": "@@"}).status_code == 400
    # 清掉锚图
    assert client.put("/api/episodes/storyvideo/PRJ-T/cast/lin", json={"asset": ""}).json()["asset"] == ""


def test_graph_and_shot_links(project):
    write_spec(project)
    graph = client.get("/api/episodes/storyvideo/PRJ-T/graph").json()
    edges = {(e["from"], e["to"], e["type"]) for e in graph["edges"]}
    assert ("shot:S2", "shot:S1", "ref_frame") in edges
    assert ("shot:S2", "cast:cat", "uses") in edges
    assert ("vo:0", "shot:S1", "attached_to") in edges
    assert ("insert:UI_1", "shot:S2", "after") in edges
    assert ("note:0", "shot:S2", "attached_to") in edges
    types = {n["type"] for n in graph["nodes"]}
    assert {"shot", "character", "scene", "voice_over", "insert", "notebook_text"} <= types

    detail = client.get("/api/episodes/storyvideo/PRJ-T").json()
    assert detail["title"] == "加班夜"
    s1 = next(s for s in detail["shots"] if s["id"] == "S1")
    s2 = next(s for s in detail["shots"] if s["id"] == "S2")
    assert s1["links"]["referenced_by"] == ["S2"] and s1["links"]["voice_over"][0]["zh"] == "凌晨一点。"
    assert s2["links"]["inserts_after"] == ["UI_1"] and [n["text"] for n in s2["links"]["notebook_text"]] == ["你好"]


def test_summary_next_step_walks_the_pipeline(project):
    write_spec(project)
    url = "/api/episodes/storyvideo/PRJ-T/summary"
    s = client.get(url).json()
    assert s["next_step"]["key"] == "start" and s["title"] == "加班夜"
    assert s["cast_count"] == 2 and s["scene_count"] == 1 and s["shots_total"] == 2

    with StoryVideoRunner(project) as runner:
        runner.prepare("PRJ-T")
    s = client.get(url).json()
    assert s["locked"] and s["next_step"]["key"] == "frames" and s["next_step"]["shots"] == ["S1", "S2"]

    out = project / "output" / "story_video" / "PRJ-T"
    (out / "01_frames").mkdir(parents=True, exist_ok=True)
    for sid in ("S1", "S2"):
        (out / "01_frames" / f"{sid}.png").write_bytes(b"png")
    s = client.get(url).json()
    assert s["next_step"]["key"] == "review_frames" and s["frames"]["pending"] == ["S1", "S2"]
    assert s["cover_url"].endswith("/artifact/frame/S1")

    qc_path = out / "manifests" / "qc.json"
    qc_path.parent.mkdir(parents=True, exist_ok=True)
    qc = {"schema_version": 1, "gates": {"frames": {"items": {"S1": {"decision": "pass"}, "S2": {"decision": "retake"}}}}}
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    s = client.get(url).json()
    assert s["next_step"]["key"] == "frames" and s["next_step"]["shots"] == ["S2"]

    qc["gates"]["frames"]["items"]["S2"] = {"decision": "pass"}
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    assert client.get(url).json()["next_step"]["key"] == "videos"

    (out / "02_video").mkdir(exist_ok=True)
    for sid in ("S1", "S2"):
        (out / "02_video" / f"{sid}.mp4").write_bytes(b"mp4")
    assert client.get(url).json()["next_step"]["key"] == "review_videos"
    qc["gates"]["videos"] = {"items": {"S1": {"decision": "pass"}, "S2": {"decision": "waive"}}}
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    assert client.get(url).json()["next_step"]["key"] == "post"   # 有旁白/插卡 → 先后期合成

    (out / "PRJ-T_post.mp4").write_bytes(b"mp4")
    s = client.get(url).json()
    assert s["next_step"]["key"] == "review_final" and s["final"]["kind"] == "post"
    qc["gates"]["final"] = {"items": {"final": {"decision": "pass"}}}
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    assert client.get(url).json()["next_step"]["key"] == "done"


def test_episode_list_carries_title_cover_and_progress(project):
    write_spec(project)
    out = project / "output" / "story_video" / "PRJ-T" / "01_frames"
    out.mkdir(parents=True)
    (out / "S2.png").write_bytes(b"png")
    items = client.get("/api/episodes").json()
    item = next(i for i in items if i["id"] == "PRJ-T")
    assert item["title"] == "加班夜"
    assert item["progress"] == {"frames": 1, "videos": 0, "total": 2, "final": None}
    assert item["cover_url"].endswith("/artifact/frame/S2")


def test_humanize_translates_known_guards_and_passes_through():
    from webui.humanize import humanize

    text = humanize(RuntimeError("frame QC must be accepted before video submission"))
    assert text.startswith("先把首帧审过") and "frame QC must be accepted" in text
    text = humanize("episode source changed after the run was frozen in generation-affecting fields (neg, shots); create an explicit revision")
    assert "neg, shots" in text and text.startswith("开始制作之后改到了")
    assert humanize("something nobody mapped") == "something nobody mapped"
    assert humanize("cannot retake frames/S3 while its state is submitted; wait") .startswith("frames/S3 现在处于「submitted」")
