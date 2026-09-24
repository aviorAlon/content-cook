"""交付包：webui/export.py 的打包规则 + routers/export.py 的三个端点。"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo.spec import load_spec
from webui import deps
from webui import export as export_lib
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import export as export_router
from webui.routers import post as post_router

client = TestClient(app)


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, export_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    # 家目录也指到临时目录：包里绝不能出现 ~/.storystudio 的东西，这里放一份「密钥」当诱饵
    home = tmp_path / "home"
    (home / ".storystudio").mkdir(parents=True)
    (home / ".storystudio" / "settings.json").write_text('{"api_key": "SECRET"}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return tmp_path


def write_spec(root: Path, episode_id: str = "EXP-T", **extra) -> Path:
    spec = {
        "id": episode_id, "output_root": f"output/story_video/{episode_id}", "prompt_language": "zh",
        "title_zh": "导出测试", "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n",
        "subtitle": {"language": "zh"},
        "cast": {"a": {"block": "A：黑发。"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["a"], "shot": "中景", "art": "a", "motion": "m",
             "lines": [{"who": "a", "zh": "你怎么才来？"}]},
            {"id": "S2", "dur": 4, "cast": ["a"], "shot": "近景", "art": "a", "motion": "m", "lines": []},
        ],
    }
    spec.update(extra)
    path = root / "content" / "story_video" / f"{episode_id}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def fake_outputs(root: Path, episode_id: str = "EXP-T", *, post: bool = False) -> Path:
    out = root / "output" / "story_video" / episode_id
    (out / "01_frames").mkdir(parents=True)
    (out / "02_video").mkdir()
    (out / "07_vo").mkdir()
    (out / "manifests").mkdir()
    (out / "01_frames" / "S1.png").write_bytes(b"png-S1")
    (out / "01_frames" / "S2.png").write_bytes(b"png-S2")
    (out / "02_video" / "S1.mp4").write_bytes(b"mp4-S1")          # S2 没视频
    (out / f"{episode_id}_final.mp4").write_bytes(b"final-bytes")
    if post:
        (out / f"{episode_id}_post.mp4").write_bytes(b"post-bytes")
    (out / "07_vo" / "vo_00_S1_abc.mp3").write_bytes(b"mp3")
    # 不该进包的诱饵：账本、带绝对路径的 manifests、临时文件
    (out / "manifests" / "run_state.json").write_text("{}", encoding="utf-8")
    (out / "manifests" / "qc.json").write_text(json.dumps({
        "schema_version": 1, "updated_at": "2026-09-08T00:00:00Z",
        "gates": {"frames": {"status": "accepted", "items": {"S1": {
            "artifact": str(out / "01_frames" / "S1.png"), "artifact_sha256": "x", "technical_passed": True,
            "decision": "pass", "reviewer": "human", "reason": "", "reviewed_at": "2026-09-08T00:00:00Z"}}},
            "videos": {"status": "not_ready", "items": {}}, "final": {"status": "not_ready", "items": {}}},
    }), encoding="utf-8")
    (root / "storyvideo.sqlite3").write_bytes(b"ledger")
    return out


def wait_task(task_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = client.get(f"/api/tasks/{task_id}").json()
        if item["status"] in ("succeeded", "failed"):
            return item
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish")


def test_build_package_layout_manifest_and_checksums(project):
    path = write_spec(project)
    out = fake_outputs(project)
    spec = load_spec("EXP-T", project_root=project)
    raw = json.loads(path.read_text(encoding="utf-8"))
    dest = out / "09_export" / "EXP-T-20260908-1200"

    summary = export_lib.build_package(spec, raw, dest)

    assert Path(summary["folder"]) == dest and Path(summary["zip"]).is_file()
    assert summary["deliverable"] == "final" and summary["name"] == "EXP-T-20260908-1200.zip"
    files = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    assert files == {
        "spec.json", "prompts.md", "ai_disclosure.md", "manifest.json", "checksums.sha256", "qc.json", "final.mp4",
        "frames/S1.png", "frames/S2.png", "videos/S1.mp4", "voices/vo_00_S1_abc.mp3",
    }
    content = files - {"manifest.json", "checksums.sha256"}
    assert summary["files"] == len(content)
    assert summary["bytes"] == sum((dest / f).stat().st_size for f in content)
    assert (dest / "final.mp4").read_bytes() == b"final-bytes"

    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "EXP-T" and manifest["title"] == "导出测试"
    assert manifest["app_version"] and manifest["exported_at"]
    assert manifest["deliverable"] == {"kind": "final", "source": "EXP-T_final.mp4", "file": "final.mp4"}
    assert manifest["shots"] == [{"id": "S1", "frame": True, "video": True}, {"id": "S2", "frame": True, "video": False}]
    assert manifest["voices"] == ["vo_00_S1_abc.mp3"] and manifest["has_qc"] is True
    listed = {f["path"] for f in manifest["files"]}
    assert listed == files - {"manifest.json", "checksums.sha256"}
    assert manifest["file_count"] == len(listed)
    assert manifest["total_bytes"] == sum(f["bytes"] for f in manifest["files"])

    # spec 快照是磁盘原样；提示词文档每镜三段围栏
    assert json.loads((dest / "spec.json").read_text(encoding="utf-8")) == raw
    prompts = (dest / "prompts.md").read_text(encoding="utf-8")
    assert "## S1" in prompts and "## S2" in prompts and prompts.count("```text") == 6
    assert "黑发" in prompts   # 角色块进了首帧提示词

    # qc.json 只有裁决，没有本机绝对路径
    qc = json.loads((dest / "qc.json").read_text(encoding="utf-8"))
    assert qc["gates"]["frames"]["items"]["S1"]["decision"] == "pass"
    assert "artifact" not in qc["gates"]["frames"]["items"]["S1"]
    assert str(out) not in (dest / "qc.json").read_text(encoding="utf-8")

    # checksums 覆盖包里每一个文件（含 manifest），重算全部一致
    lines = (dest / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    assert {ln.split("  ", 1)[1] for ln in lines} == files - {"checksums.sha256"}
    for ln in lines:
        digest, rel = ln.split("  ", 1)
        assert hashlib.sha256((dest / rel).read_bytes()).hexdigest() == digest
    assert export_lib.verify_checksums(dest) == []

    # 包外的东西一个都没进来：没有设置/密钥、没有账本、没有 manifests
    for rel in files:
        assert ".." not in rel and not rel.startswith("/") and (dest / rel).resolve().is_relative_to(dest.resolve())
    joined = "\n".join(files)
    assert "settings" not in joined and "sqlite" not in joined and "manifests" not in joined and "run_state" not in joined
    assert "SECRET" not in b"".join((dest / f).read_bytes() for f in files).decode("utf-8", "ignore")

    with zipfile.ZipFile(summary["zip"]) as zf:
        names = set(zf.namelist())
    assert {f"EXP-T-20260908-1200/{f}" for f in files} <= names


def test_build_package_prefers_post_cut_and_survives_missing_outputs(project):
    path = write_spec(project)
    out = fake_outputs(project, post=True)
    spec = load_spec("EXP-T", project_root=project)
    raw = json.loads(path.read_text(encoding="utf-8"))
    summary = export_lib.build_package(spec, raw, out / "09_export" / "a")
    assert summary["deliverable"] == "post"
    assert (out / "09_export" / "a" / "final.mp4").read_bytes() == b"post-bytes"

    # 什么产物都没有的草稿：只出文档，manifest 说清楚
    write_spec(project, "EXP-E")
    spec2 = load_spec("EXP-E", project_root=project)
    raw2 = json.loads((project / "content" / "story_video" / "EXP-E.json").read_text(encoding="utf-8"))
    dest2 = project / "output" / "story_video" / "EXP-E" / "09_export" / "b"
    s2 = export_lib.build_package(spec2, raw2, dest2)
    assert s2["deliverable"] is None
    m2 = json.loads((dest2 / "manifest.json").read_text(encoding="utf-8"))
    assert m2["deliverable"] is None and m2["has_qc"] is False and m2["voices"] == []
    assert {f["path"] for f in m2["files"]} == {"spec.json", "prompts.md", "ai_disclosure.md"}
    # 同一目录不能重复导出（不覆盖已交付的东西）
    with pytest.raises(FileExistsError):
        export_lib.build_package(spec2, raw2, dest2)


def test_build_package_disclosure_reads_ledger_but_never_ships_it(project):
    """工具清单优先按账本（runtime 规则下的 storyvideo.sqlite3）；账本本身仍然不进包。"""

    from genkit.store import RunStore
    from storyvideo.runtime import ledger_path

    path = write_spec(project, image_params={"model": "@image"})
    out = fake_outputs(project)
    with RunStore(ledger_path(project)) as store:
        store.create_run("EXP-T", "storyvideo", "EXP-T", "in", "plan", out)
        for sid in ("S1", "S2"):
            store.ensure_job("EXP-T", f"frame-{sid}", "image", f"h-{sid}", f"01_frames/{sid}.png")
            store.mark_submitting("EXP-T", f"frame-{sid}", provider="ark_images", model="doubao-seedream-4-0-250828")
            store.mark_submitted("EXP-T", f"frame-{sid}", f"img-{sid}", "finished")
    spec = load_spec("EXP-T", project_root=project)
    raw = json.loads(path.read_text(encoding="utf-8"))
    dest = out / "09_export" / "ledger"
    export_lib.build_package(spec, raw, dest)
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    frames = manifest["ai_disclosure"]["tools"]["frames"]
    assert frames["from"] == "ledger" and frames["sources"] == [
        {"provider": "ark_images", "model": "doubao-seedream-4-0-250828", "count": 2}]
    assert "首帧 / 视频按账本逐 job 记录" in manifest["ai_disclosure"]["basis"] and "以各行标注为准" in manifest["ai_disclosure"]["basis"]
    assert manifest["ai_disclosure"]["tools"]["videos"]["from"] == "settings"   # 视频还没提交过
    md = (dest / "ai_disclosure.md").read_text(encoding="utf-8")
    assert "- 首帧：ark_images / doubao-seedream-4-0-250828 ×2（按账本）" in md
    files = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    assert not any("sqlite" in f for f in files)


def test_build_package_disclosure_traces_voices_and_music(project):
    """AI 生成说明与工具清单补全：manifest.ai_disclosure 与 ai_disclosure.md 两处都有配乐、每个角色的配音声线、旁白声线（整块 + 逐句）。"""

    cast = {"a": {"name": "阿明", "block": "A：黑发。",
                  "voice_tts": {"engine": "edge", "voice": "zh-CN-YunxiNeural", "rate": "-5%", "pitch": "-2Hz"}}}
    voice_over = {"engine": "edge", "edge": {"voice": "zh-CN-XiaoyiNeural", "rate": "-3%"},
                  "lines": [{"shot": "S2", "at": 0.3, "zh": "他没回头。"},
                            {"shot": "S2", "at": 2.0, "zh": "广播说，前方到站。", "edge": {"voice": "zh-CN-YunxiaNeural", "rate": "-2%"}}]}
    graphics = {"dubbing": {"mode": "all"},
                "music": [{"label": "BGM-A 木吉他小调", "file": "content/assets/EXP-T/music/MU-A-1-0a1b2c3d.mp3", "start": 0, "end": 8}]}
    path = write_spec(project, cast=cast, voice_over=voice_over, post_graphics=graphics)
    out = fake_outputs(project)
    spec = load_spec("EXP-T", project_root=project)
    raw = json.loads(path.read_text(encoding="utf-8"))
    dest = out / "09_export" / "voices"
    export_lib.build_package(spec, raw, dest)

    disclosure = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))["ai_disclosure"]
    assert disclosure["basis_audio"].startswith("旁白、台词配音与配乐按导出时的项目文件记")
    tools = disclosure["tools"]
    assert tools["narration"]["voice"] == "zh-CN-XiaoyiNeural" and tools["narration"]["rate"] == "-3%"
    # 逐句换声线没写的音调沿用整块的（整块没写，就是合成时的默认 -8Hz）
    assert tools["narration"]["line_voices"] == [
        {"index": 1, "shot": "S2", "engine": "edge", "voice": "zh-CN-YunxiaNeural", "rate": "-2%", "pitch": "-8Hz"}]
    assert tools["dubbing"]["voices"] == [{"cast": "a", "name": "阿明", "engine": "edge", "voice": "zh-CN-YunxiNeural",
                                           "rate": "-5%", "pitch": "-2Hz", "used": True}]
    assert tools["music"]["tracks"] == [{"label": "BGM-A 木吉他小调", "file": "content/assets/EXP-T/music/MU-A-1-0a1b2c3d.mp3"}]

    md = (dest / "ai_disclosure.md").read_text(encoding="utf-8")
    assert "- 旁白：edge / zh-CN-XiaoyiNeural（语速 -3%，音调 -8Hz）" in md
    assert "  - 第 2 句（S2）换声线：edge / zh-CN-YunxiaNeural（语速 -2%，音调 -8Hz）" in md
    assert "- 台词配音：all\n  - 「阿明」（a）：edge / zh-CN-YunxiNeural（语速 -5%，音调 -2Hz）\n" in md
    assert "- 配乐：1 条\n  - 「BGM-A 木吉他小调」：content/assets/EXP-T/music/MU-A-1-0a1b2c3d.mp3\n" in md
    assert str(project) not in md   # 本机路径不进包


def test_resolve_zip_rejects_paths_outside_export_dir(project):
    write_spec(project)
    out = fake_outputs(project)
    spec = load_spec("EXP-T", project_root=project)
    (out / "09_export").mkdir()
    (out / "09_export" / "ok.zip").write_bytes(b"zip")
    (out / "evil.zip").write_bytes(b"zip")
    assert export_lib.resolve_zip(spec, "ok.zip") == out / "09_export" / "ok.zip"
    for bad in ("", "../evil.zip", "..\\evil.zip", "sub/ok.zip", "ok.txt", "missing.zip", "..", "EXP-T_final.mp4"):
        assert export_lib.resolve_zip(spec, bad) is None, bad


def test_export_api_task_list_and_download(project):
    write_spec(project)
    out = fake_outputs(project)

    assert client.get("/api/episodes/storyvideo/EXP-T/export").json()["packages"] == []
    assert client.post("/api/episodes/storyvideo/NOPE/export").status_code == 404

    created = client.post("/api/episodes/storyvideo/EXP-T/export")
    assert created.status_code == 200, created.text
    task = wait_task(created.json()["task_id"])
    assert task["status"] == "succeeded", task.get("error")
    result = task["result"]
    assert result["files"] == 9 and result["deliverable"] == "final"   # 9 份内容（含 ai_disclosure.md）+ manifest + checksums
    assert Path(result["zip"]).is_file() and Path(result["zip"]).parent == out / "09_export"

    # 同一分钟内再点：409（目录已存在），不会覆盖
    again = client.post("/api/episodes/storyvideo/EXP-T/export")
    assert again.status_code == 409

    listed = client.get("/api/episodes/storyvideo/EXP-T/export").json()
    assert len(listed["packages"]) == 1
    pkg = listed["packages"][0]
    assert pkg["name"] == result["name"] and pkg["size"] == result["zip_bytes"]
    assert pkg["files"] == 9 and pkg["deliverable"] == "final" and pkg["bytes"] == result["bytes"]
    assert pkg["download_url"].endswith(f"/export/download?name={pkg['name']}")

    dl = client.get(pkg["download_url"])
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/zip")
    assert pkg["name"] in dl.headers.get("content-disposition", "")
    assert dl.content == Path(result["zip"]).read_bytes()

    for bad in ("../EXP-T_final.mp4", "nope.zip", "", "manifests/qc.json"):
        r = client.get("/api/episodes/storyvideo/EXP-T/export/download", params={"name": bad})
        assert r.status_code == 404, bad
