"""制作中也能改：影响判定（含参考链传染）、预览、复制成新版本并带走可复用产物与裁决。"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo.qc import artifact_sha256
from storyvideo.runtime import StoryVideoRunner
from webui import deps
from webui import revise as R
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router

client = TestClient(app)
FFPROBE = shutil.which("ffprobe")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    spec = {
        "id": "REV-T", "output_root": "output/story_video/REV-T", "prompt_language": "zh", "dialogue_prompt": "auto",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "voice": "女声", "block": "林小满：黑发。"}, "office": {"block": "办公区。"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["office", "lin"], "shot": "中景", "art": "a", "motion": "m1", "lines": []},
            {"id": "S2", "dur": 4, "cast": ["office", "lin"], "shot": "近景", "art": "a", "motion": "m2", "ref_frame": "S1", "lines": []},
            {"id": "S3", "dur": 4, "cast": ["office"], "shot": "全景", "art": "a", "motion": "m3", "lines": []},
        ],
    }
    (content_dir / "REV-T.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def png_bytes() -> bytes:
    from PIL import Image
    import random

    img = Image.new("RGB", (320, 568))
    px = img.load()
    for x in range(0, 320, 4):
        for y in range(0, 568, 4):
            px[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()


def start_with_frames(root: Path, decisions: dict[str, str]) -> Path:
    """开始制作，并伪造"三镜首帧都已生成、审片有裁决"的状态。"""

    with StoryVideoRunner(root) as runner:
        runner.prepare("REV-T")
    out = root / "output" / "story_video" / "REV-T"
    (out / "01_frames").mkdir(parents=True, exist_ok=True)
    items = {}
    for sid, decision in decisions.items():
        path = out / "01_frames" / f"{sid}.png"
        path.write_bytes(png_bytes())
        items[sid] = {"artifact": str(path.resolve()), "artifact_sha256": artifact_sha256(path), "technical_passed": True,
                      "probe": {}, "decision": decision, "reviewer": "test", "reason": "", "reviewed_at": "t", "stale": False}
    qc_path = out / "manifests" / "qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8")) if qc_path.is_file() else {"schema_version": 1, "gates": {}}
    qc.setdefault("gates", {})["frames"] = {"status": "pending_review", "items": items, "evidence": []}
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    return out


def test_impact_reasons_and_reference_chain(project):
    out = start_with_frames(project, {"S1": "pass", "S2": "pass", "S3": "pass"})
    # 只改 S2 的动作：动作进视频提示词，首帧不变 → 首帧全可复用，只有 S2 的视频作废
    prev = client.post("/api/episodes/storyvideo/REV-T/revise-edit/preview", json={"mutation": {"kind": "shot_edit", "shot_id": "S2", "motion": "新动作"}})
    assert prev.status_code == 200, prev.text
    body = prev.json()
    assert body["affected_frames"] == [] and body["affected_videos"] == ["S2"]
    assert body["reusable_frames"] == ["S1", "S2", "S3"] and body["requests"] == {"frames": 0, "videos": 1}
    assert body["suggested_id"] == "REV-T-v2" and body["locked"] is True
    assert "视频请求变了" in body["reasons"]["S2"]
    # 改角色外形：引用它的 S1、S2 首帧作废（S2 还通过参考链），S3 不受影响
    body = client.post("/api/episodes/storyvideo/REV-T/revise-edit/preview", json={"mutation": {"kind": "cast_edit", "cast_id": "lin", "block": "林小满：短发。"}}).json()
    assert body["affected_frames"] == ["S1", "S2"] and body["reusable_frames"] == ["S3"]
    assert body["affected_videos"] == ["S1", "S2"]
    # 只改 S1 的画面：S2 因为参考链跟着作废
    body = client.post("/api/episodes/storyvideo/REV-T/revise-edit/preview", json={"mutation": {"kind": "shot_edit", "shot_id": "S1", "art": "新画面"}}).json()
    assert body["affected_frames"] == ["S1", "S2"] and "参考镜 S1" in body["reasons"]["S2"]
    # 加一镜 / 删一镜
    body = client.post("/api/episodes/storyvideo/REV-T/revise-edit/preview", json={"mutation": {"kind": "shot_create", "id": "S4", "after": "S3", "shot": "中景", "art": "b", "motion": "m4"}}).json()
    assert body["affected_frames"] == ["S4"] and body["reasons"]["S4"] == "新镜"
    body = client.post("/api/episodes/storyvideo/REV-T/revise-edit/preview", json={"mutation": {"kind": "shot_delete", "shot_id": "S3"}}).json()
    assert body["removed_shots"] == ["S3"] and body["affected_frames"] == []
    # 非法改动：校验挡住
    bad = client.post("/api/episodes/storyvideo/REV-T/revise-edit/preview", json={"mutation": {"kind": "shot_edit", "shot_id": "S1", "camera_move": "nope"}})
    assert bad.status_code == 400
    assert client.post("/api/episodes/storyvideo/REV-T/revise-edit/preview", json={"mutation": {"kind": "teleport"}}).status_code == 400


@pytest.mark.skipif(not FFPROBE, reason="import_legacy validates artifacts with ffprobe")
def test_apply_creates_revision_with_reusable_artifacts_and_decisions(project):
    start_with_frames(project, {"S1": "pass", "S2": "pass", "S3": "retake"})
    resp = client.post("/api/episodes/storyvideo/REV-T/revise-edit/apply", json={
        "mutation": {"kind": "shot_edit", "shot_id": "S3", "art": "新画面"}, "new_episode_id": "REV-T-v2"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "REV-T-v2" and body["copied"]["frames"] == ["S1", "S2"] and body["affected_frames"] == ["S3"]
    new_root = project / "output" / "story_video" / "REV-T-v2"
    assert (new_root / "01_frames" / "S1.png").is_file() and not (new_root / "01_frames" / "S3.png").is_file()
    # 新版本已进入制作，旧版本原样
    assert client.get("/api/episodes/storyvideo/REV-T-v2").json()["locked"] is True
    assert client.get("/api/episodes/storyvideo/REV-T").json()["locked"] is True
    old_spec = json.loads((project / "content" / "story_video" / "REV-T.json").read_text(encoding="utf-8"))
    assert old_spec["shots"][2]["art"] == "a"
    new_spec = json.loads((project / "content" / "story_video" / "REV-T-v2.json").read_text(encoding="utf-8"))
    assert new_spec["shots"][2]["art"] == "新画面" and new_spec["output_root"] == "output/story_video/REV-T-v2"
    # 裁决跟着产物走：S1/S2 的通过保留，S3 要重出
    detail = client.get("/api/episodes/storyvideo/REV-T-v2").json()
    by_id = {s["id"]: s for s in detail["shots"]}
    assert by_id["S1"]["frame_qc_decision"] == "pass" and by_id["S2"]["frame_url"]
    assert by_id["S3"]["frame_url"] is None
    summary = client.get("/api/episodes/storyvideo/REV-T-v2/summary").json()
    assert summary["next_step"]["key"] == "frames" and summary["next_step"]["shots"] == ["S3"]
    # 再来一次：建议 id 递增；重名拒绝
    assert client.post("/api/episodes/storyvideo/REV-T-v2/revise-edit/preview", json={"mutation": {"kind": "shot_edit", "shot_id": "S1", "sfx": "x"}}).json()["suggested_id"] == "REV-T-v3"
    dup = client.post("/api/episodes/storyvideo/REV-T/revise-edit/apply", json={"mutation": {"kind": "shot_edit", "shot_id": "S1", "sfx": "x"}, "new_episode_id": "REV-T-v2"})
    assert dup.status_code == 400


def test_apply_refuses_on_draft(project):
    resp = client.post("/api/episodes/storyvideo/REV-T/revise-edit/apply", json={"mutation": {"kind": "shot_edit", "shot_id": "S1", "sfx": "x"}, "new_episode_id": "REV-T-v2"})
    assert resp.status_code == 400 and "直接保存" in resp.json()["detail"]


def test_apply_mutation_covers_cast_and_dubbing(project):
    raw = json.loads((project / "content" / "story_video" / "REV-T.json").read_text(encoding="utf-8"))
    out = R.apply_mutation(raw, {"kind": "cast_create", "id": "boss", "name": "老板", "block": "西装。"})
    assert out["cast"]["boss"]["kind"] == "character" and "boss" not in raw["cast"]
    with pytest.raises(R.ReviseError, match="还被这些镜引用"):
        R.apply_mutation(raw, {"kind": "cast_delete", "cast_id": "lin"})
    out = R.apply_mutation(raw, {"kind": "shot_order", "order": ["S3", "S1", "S2"]})
    assert [s["id"] for s in out["shots"]] == ["S3", "S1", "S2"]
    out = R.apply_mutation(raw, {"kind": "globals", "values": {"neg": "changed"}})
    assert out["neg"] == "changed"
    with pytest.raises(R.ReviseError):
        R.apply_mutation(raw, {"kind": "globals", "values": {"id": "x"}})
    raw["shots"][0]["lines"] = [{"who": "lin", "zh": "你怎么才来？"}]
    raw["post_graphics"] = {"dubbing": {"mode": "all"}}
    out = R.apply_mutation(raw, {"kind": "dubbing_offscreen"})
    assert out["shots"][0]["speech_mode"] == "offscreen_narration" and "speech_mode" not in out["shots"][1]
