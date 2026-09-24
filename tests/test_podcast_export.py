"""播客交付包：podcast/export.py 的布局 / manifest / checksums / AI 生成说明与工具清单 / 没混音的包 / 包外的东西一个都不进来 /
同一分钟重复导出报错 / 下载名解析，以及 routers/podcast.py 的导出任务 pc-export、列表、下载与路径安全。"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from podcast import audio as A
from podcast import export as E
from podcast import spec as S
from webui import deps, tasks
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import podcast as podcast_router

client = TestClient(app)
MP3 = b"ID3" + b"\x00" * 64
API = "/api/podcast"
LOOKUP = {"lib": {"id": "lib", "name": "库里的", "engine": "minimax", "voice": "mm-1"}}


def fake_synth(profile, text, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(MP3 + text.encode("utf-8"))


def fake_prober(path: Path) -> float:
    return round(0.5 + len(path.read_bytes()) / 100.0, 3)


def fake_runner(command):
    Path(command[-1]).write_bytes(MP3 + b"mixed")


def lookup(vid: str):
    return LOOKUP.get(vid)


def write_spec(root: Path, spec_id: str = "PC-S", **extra) -> dict:
    """项目文件落盘（磁盘原样要能进包），返回归一化后的 spec。"""

    data = {
        "id": spec_id, "title": "导出测试期", "format": "duo", "target_minutes": 1,
        "brief": {"topic": "t", "takeaways": ["a"]},
        "hosts": [{"id": "a", "name": "阿一", "role": "host"}, {"id": "b", "name": "阿二", "role": "cohost", "voice": "lib"}],
        "segments": [
            {"id": "G01", "kind": "cold_open", "title": "冷", "lines": [{"who": "a", "text": "第一句。"}, {"who": "b", "text": "第二句。", "cue": "停顿"}]},
            {"id": "G02", "kind": "intro", "title": "开", "lines": [{"who": "a", "text": "第三句。"}]},
        ],
        "draft": {"model": "gpt-x", "backend": "gateway", "created_at": 1758000000, "usage_tokens": 12},
    }
    data.update(extra)
    path = S.spec_path(root, spec_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return S.load(spec_id, project_root=root)


def synthesize(spec: dict, *, mix: bool = True) -> None:
    A.synthesize_lines(spec, synthesizer=fake_synth, prober=fake_prober, lookup=lookup)
    if mix:
        A.mix(spec, ffmpeg="ffmpeg", runner=fake_runner, prober=fake_prober)


def plant_decoys(root: Path, spec: dict, home: Path) -> None:
    """包外的诱饵：设置 / 密钥、账本、manifests、音乐素材、07_audio 里不在清单上的散文件、临时文件。"""

    (home / ".storystudio").mkdir(parents=True, exist_ok=True)
    (home / ".storystudio" / "settings.json").write_text('{"api_key": "SECRET"}', encoding="utf-8")
    (root / "storyvideo.sqlite3").write_bytes(b"SECRET-ledger")
    out = S.output_root(spec)
    (out / "manifests").mkdir(parents=True, exist_ok=True)
    (out / "manifests" / "run_state.json").write_text('{"path": "C:/SECRET"}', encoding="utf-8")
    (root / "content" / "assets" / spec["id"]).mkdir(parents=True, exist_ok=True)
    (root / "content" / "assets" / spec["id"] / "bed.mp3").write_bytes(b"SECRET-music")
    S.audio_dir(spec).mkdir(parents=True, exist_ok=True)
    (S.audio_dir(spec) / "stray.mp3").write_bytes(b"SECRET-stray")
    (S.audio_dir(spec) / "G01_01.mp3.tmp").write_bytes(b"SECRET-tmp")
    S.mix_dir(spec).mkdir(parents=True, exist_ok=True)
    (S.mix_dir(spec) / "other.mp3").write_bytes(b"SECRET-other")


def inside(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("STORYSTUDIO_HOME", str(home / ".storystudio"))
    return home


# ---------------------------------------------------------------------------- 打包


def test_build_package_layout_manifest_checksums_and_nothing_else(tmp_path, home):
    spec = write_spec(tmp_path)
    synthesize(spec)
    plant_decoys(tmp_path, spec, home)
    now = datetime(2026, 9, 16, 12, 30)

    pkg = E.build_package(spec, now=now, lookup=lookup)

    folder = Path(pkg["folder"])
    assert folder == S.output_root(spec) / "09_export" / "PC-S-20260916-1230" and pkg["name"] == "PC-S-20260916-1230.zip"
    assert Path(pkg["zip"]) == folder.parent / pkg["name"] and Path(pkg["zip"]).is_file()
    files = inside(folder)
    assert files == {"spec.json", "mix.mp3", "shownotes.md", "transcript.txt", "chapters.json", "ai_disclosure.md",
                     "lines/G01_01.mp3", "lines/G01_02.mp3", "lines/G02_01.mp3", "manifest.json", "checksums.sha256"}
    content = files - {"manifest.json", "checksums.sha256"}
    assert pkg["files"] == len(content) and pkg["bytes"] == sum((folder / f).stat().st_size for f in content)
    assert pkg["mixed"] is True and pkg["lines"] == 3 and pkg["zip_bytes"] > 0 and pkg["duration"] > 0
    # 拷的是原文件，原文件没动
    assert (folder / "mix.mp3").read_bytes() == (S.mix_dir(spec) / "PC-S.mp3").read_bytes() == MP3 + b"mixed"
    assert (folder / "lines" / "G01_02.mp3").read_bytes() == (S.audio_dir(spec) / "G01_02.mp3").read_bytes()
    assert json.loads((folder / "spec.json").read_text(encoding="utf-8")) == json.loads(S.spec_path(tmp_path, "PC-S").read_text(encoding="utf-8"))
    assert (folder / "shownotes.md").read_text(encoding="utf-8").startswith("# 导出测试期")

    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1 and manifest["id"] == "PC-S" and manifest["module"] == "podcast"
    assert manifest["title"] == "导出测试期" and manifest["format"] == "duo" and manifest["format_name"] == "双人对谈"
    assert manifest["app_version"] and manifest["exported_at"] and manifest["spec_source"] == "disk"
    # 音色库档案的主播：voice 是真正送去 TTS 的音色名，档案 id 在 profile_id（以前 voice 记的是档案 id，真音色进不了包）
    assert manifest["hosts"] == [
        {"id": "a", "name": "阿一", "role": "host", "engine": "edge", "voice": "zh-CN-YunxiNeural", "profile_id": "", "placeholder": True},
        {"id": "b", "name": "阿二", "role": "cohost", "engine": "minimax", "voice": "mm-1", "profile_id": "lib", "placeholder": False},
    ]
    assert manifest["duration"] > 0 and [c["id"] for c in manifest["chapters"]] == ["G01", "G02"]
    assert manifest["mix"] == {"file": "mix.mp3", "source": "08_mix/PC-S.mp3", "bytes": (folder / "mix.mp3").stat().st_size, "stale": False}
    assert manifest["texts"] == {"shownotes.md": True, "transcript.txt": True, "chapters.json": True}
    assert manifest["line_count"] == 3 and manifest["lines_included"] is True
    assert [(r["segment"], r["index"], r["who"], r["who_name"], r["file"]) for r in manifest["lines"]] == [
        ("G01", 1, "a", "阿一", "lines/G01_01.mp3"), ("G01", 2, "b", "阿二", "lines/G01_02.mp3"), ("G02", 1, "a", "阿一", "lines/G02_01.mp3")]
    assert all(r["duration"] > 0 and r["bytes"] > 0 and r["text"] for r in manifest["lines"])
    assert manifest["audio"] == {"synthesized_lines": 3, "synthesized_at": manifest["ai_disclosure"]["synthesized_at"], "stale": False,
                                 "mix_stale": False}
    assert {f["path"] for f in manifest["files"]} == content
    assert manifest["file_count"] == len(content) and manifest["total_bytes"] == sum(f["bytes"] for f in manifest["files"])
    assert "settings / api keys (~/.storystudio)" in manifest["excluded"] and "ledger (sqlite)" in manifest["excluded"]
    assert not any("还没混音" in x for x in manifest["excluded"])

    # 生成内容标注 + 用了哪些工具：起草模型来自 draft，音色来自合成清单
    disclosure = manifest["ai_disclosure"]
    assert disclosure["script"]["drafted_by_model"] is True and disclosure["script"]["model"] == "gpt-x" and disclosure["script"]["backend"] == "gateway"
    assert disclosure["hosts"][0]["placeholder"] is True and disclosure["hosts"][0]["from"] == "manifest"
    assert disclosure["hosts"][1] == {"id": "b", "name": "阿二", "role": "cohost", "engine": "minimax", "voice": "mm-1", "profile_id": "lib",
                                      "label": "库里的", "placeholder": False, "missing": False, "no_tts": False, "from": "manifest"}
    assert disclosure["mixed"] is True and disclosure["mix_stale"] is False and disclosure["synthesized_at"] and "没有查账本" in disclosure["basis"]
    assert "老版本" not in disclosure["basis"]
    assert manifest["audio"]["mix_stale"] is False and manifest["mix"]["stale"] is False
    md = (folder / "ai_disclosure.md").read_text(encoding="utf-8")
    assert md.startswith("# AI 生成说明与工具清单 · 导出测试期")
    assert "【AI 生成说明】本期播客由 AI 辅助制作" in md and "【用到的生成工具】" in md
    assert "发布时按平台规定在显眼处标明 AI 生成。" in md
    assert "稿子由文本模型起草" in md and "- 稿子：文本模型 gpt-x 起草（来源 gateway，" in md
    assert "- 阿一（主播）：edge / zh-CN-YunxiNeural（占位音色，按合成清单）" in md
    assert "- 阿二（搭档）：minimax / mm-1（音色库档案「库里的」（lib），按合成清单）" in md
    assert "- 混音：ffmpeg 本机混音，成品 mix.mp3\n" in md and "旧参数" not in md

    # checksums 覆盖包里每一个文件（含 manifest），重算全部一致；改一个字节就能查出来
    lines = (folder / "checksums.sha256").read_text(encoding="utf-8").strip().splitlines()
    assert {ln.split("  ", 1)[1] for ln in lines} == files - {"checksums.sha256"}
    for ln in lines:
        digest, rel = ln.split("  ", 1)
        assert hashlib.sha256((folder / rel).read_bytes()).hexdigest() == digest
    assert E.verify_checksums(folder) == []
    (folder / "shownotes.md").write_text("tampered", encoding="utf-8")
    assert E.verify_checksums(folder) == ["shownotes.md"]

    # 包外的东西一个都没进来：没有设置 / 密钥、账本、manifests、音乐素材、散文件、临时文件
    for rel in files:
        assert ".." not in rel and not rel.startswith("/") and (folder / rel).resolve().is_relative_to(folder.resolve())
    joined = "\n".join(files)
    for word in ("settings", "sqlite", "manifests", "run_state", "audio_manifest", "bed.mp3", "stray", ".tmp", "other.mp3"):
        assert word not in joined, word
    assert b"SECRET" not in b"".join((folder / f).read_bytes() for f in files)

    with zipfile.ZipFile(pkg["zip"]) as zf:
        names = {n for n in zf.namelist() if not n.endswith("/")}
    assert names == {f"PC-S-20260916-1230/{f}" for f in files}


def test_package_without_mix_and_without_lines(tmp_path, home):
    spec = write_spec(tmp_path, draft={})
    synthesize(spec, mix=False)
    pkg = E.build_package(spec, now=datetime(2026, 9, 16, 12, 31), lookup=lookup)
    folder = Path(pkg["folder"])
    assert inside(folder) == {"spec.json", "ai_disclosure.md", "lines/G01_01.mp3", "lines/G01_02.mp3", "lines/G02_01.mp3",
                              "manifest.json", "checksums.sha256"}
    assert pkg["mixed"] is False and pkg["duration"] is None
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mix"] is None and manifest["duration"] is None and manifest["chapters"] == []
    assert manifest["texts"] == {"shownotes.md": False, "transcript.txt": False, "chapters.json": False}
    assert "mix.mp3：还没混音（只有逐句音频）" in manifest["excluded"]
    # 手写稿（没有 draft.model）→ 标识写「手写」
    assert manifest["ai_disclosure"]["script"]["drafted_by_model"] is False and manifest["ai_disclosure"]["script"]["model"] is None
    md = (folder / "ai_disclosure.md").read_text(encoding="utf-8")
    assert "稿子为手写（项目文件里没有起草记录）" in md and "- 稿子：手写（项目文件里没有起草记录）" in md
    assert "- 混音：还没混音，包里没有成品" in md
    assert E.verify_checksums(folder) == []

    # 不带逐句
    pkg2 = E.build_package(spec, now=datetime(2026, 9, 16, 12, 32), include_lines=False, lookup=lookup)
    folder2 = Path(pkg2["folder"])
    assert inside(folder2) == {"spec.json", "ai_disclosure.md", "manifest.json", "checksums.sha256"} and pkg2["lines"] == 0
    manifest2 = json.loads((folder2 / "manifest.json").read_text(encoding="utf-8"))
    assert manifest2["lines"] == [] and manifest2["lines_included"] is False and manifest2["audio"]["synthesized_lines"] == 3
    assert "lines/：按要求没带逐句音频" in manifest2["excluded"]


def test_stale_audio_and_old_manifest_are_flagged(tmp_path, home):
    spec = write_spec(tmp_path)
    synthesize(spec)
    # 稿子改过：包照打，manifest 标 stale
    changed = S.normalize({**S.to_disk(spec), "segments": [{**spec["segments"][0], "lines": [{"who": "a", "text": "改过的第一句。"}, {"who": "b", "text": "第二句。"}]},
                                                             spec["segments"][1]]}, project_root=tmp_path, spec_id="PC-S")
    pkg = E.build_package(changed, now=datetime(2026, 9, 16, 12, 33), lookup=lookup)
    manifest = json.loads((Path(pkg["folder"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["audio"]["stale"] is True and manifest["line_count"] == 3
    # 老清单没记 hosts → 主播那几行按导出时的音色设置，口径里说明
    raw = json.loads(A.manifest_path(spec).read_text(encoding="utf-8"))
    raw.pop("hosts", None)
    A.manifest_path(spec).write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    disclosure = E.ai_disclosure(spec, mixed=True, lookup=lookup)
    assert all(h["from"] == "current" for h in disclosure["hosts"]) and "老版本" in disclosure["basis"]
    assert disclosure["hosts"][1]["engine"] == "minimax" and disclosure["hosts"][0]["placeholder"] is True
    assert "按导出时的音色设置" in E.disclosure_markdown(disclosure, spec)


def test_export_refuses_without_audio_and_never_overwrites_same_minute(tmp_path, home):
    spec = write_spec(tmp_path)
    assert E.exportable(spec) is False and E.list_packages(spec) == []
    with pytest.raises(E.ExportError, match="还没有可以导出的东西——先合成"):
        E.build_package(spec, lookup=lookup)
    assert not E.export_root(spec).exists()
    synthesize(spec)
    assert E.exportable(spec) is True
    now = datetime(2026, 9, 16, 12, 30)
    first = E.build_package(spec, now=now, lookup=lookup)
    before = Path(first["zip"]).read_bytes()
    with pytest.raises(E.ExportError, match="这一分钟已经导出过一个交付包了"):
        E.build_package(spec, now=now, lookup=lookup)
    assert Path(first["zip"]).read_bytes() == before and E.verify_checksums(Path(first["folder"])) == []
    # 文件夹删了、zip 还在 → 也不覆盖
    second = E.build_package(spec, now=datetime(2026, 9, 16, 12, 31), lookup=lookup)
    import shutil

    shutil.rmtree(second["folder"])
    with pytest.raises(E.ExportError, match="这一分钟已经导出过"):
        E.build_package(spec, now=datetime(2026, 9, 16, 12, 31), lookup=lookup)
    rows = E.list_packages(spec)
    assert {r["name"] for r in rows} == {"PC-S-20260916-1231.zip", "PC-S-20260916-1230.zip"}
    assert rows == sorted(rows, key=lambda r: r["created"], reverse=True)          # 新到旧
    full = next(r for r in rows if r["name"] == "PC-S-20260916-1230.zip")
    assert full["size"] > 0 and full["files"] == 9 and full["bytes"] == first["bytes"] and full["mixed"] is True and full["lines"] == 3
    gone = next(r for r in rows if r["name"] == "PC-S-20260916-1231.zip")
    assert gone["files"] is None and gone["mixed"] is None        # 文件夹没了就只剩 zip 本身的信息


def test_resolve_zip_rejects_traversal_and_unknown(tmp_path, home):
    spec = write_spec(tmp_path)
    synthesize(spec)
    pkg = E.build_package(spec, now=datetime(2026, 9, 16, 12, 30), lookup=lookup)
    (S.output_root(spec) / "evil.zip").write_bytes(b"zip")
    assert E.resolve_zip(spec, pkg["name"]) == Path(pkg["zip"])
    for bad in ("", "../evil.zip", "..\\evil.zip", "sub/x.zip", "manifest.json", ".", "..", "nope.zip", "PC-S.mp3", "PC-S-20260916-1230"):
        assert E.resolve_zip(spec, bad) is None, bad


# ---------------------------------------------------------------------------- 接口


@pytest.fixture
def project(tmp_path, monkeypatch, home):
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", tmp_path / "content" / "story_video")
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", tmp_path / "content" / "story_video")
    monkeypatch.setattr(tasks, "tasks_dir", lambda: tmp_path / ".webui_tasks")
    monkeypatch.setattr(podcast_router, "SYNTHESIZER", fake_synth)
    monkeypatch.setattr(podcast_router, "PROBER", fake_prober)
    monkeypatch.setattr(podcast_router, "RUNNER", fake_runner)
    monkeypatch.setattr(podcast_router, "FFMPEG", "ffmpeg")
    monkeypatch.setattr(podcast_router, "VOICE_LOOKUP", lookup)
    monkeypatch.setattr(podcast_router, "DISPATCH", None)
    return tmp_path


def wait(task_id: str, seconds: float = 10.0) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        task = tasks.get(task_id)
        if task and task["status"] in ("succeeded", "failed"):
            return task
        time.sleep(0.02)
    raise AssertionError("task did not finish")


def test_export_api_task_list_download_and_path_safety(project):
    spec = write_spec(project, "PC-API")
    pid = spec["id"]
    assert client.post(f"{API}/NOPE/export", json={}).status_code == 404
    assert client.get(f"{API}/NOPE/export").status_code == 404
    # 一句都没合成过 → 400
    r = client.post(f"{API}/{pid}/export", json={})
    assert r.status_code == 400 and r.json()["detail"] == "还没有可以导出的东西——先合成"
    assert client.get(f"{API}/{pid}/export").json() == {"id": pid, "packages": [], "export_dir": f"output/podcast/{pid}/09_export"}
    assert client.get(f"{API}/{pid}").json()["exports"] == []

    assert wait(client.post(f"{API}/{pid}/synthesize", json={}).json()["task_id"])["status"] == "succeeded"
    plant_decoys(project, S.load(pid, project_root=project), project / "home")
    created = client.post(f"{API}/{pid}/export", json={})
    assert created.status_code == 200, created.text
    assert created.json()["name"].startswith(f"{pid}-") and created.json()["name"].endswith(".zip")
    task = wait(created.json()["task_id"])
    assert task["status"] == "succeeded", task.get("error")
    assert task["kind"] == "pc-export" and task["module"] == "podcast" and task["params"]["include_lines"] is True
    result = task["result"]
    assert result["name"] == created.json()["name"] and result["files"] == 9 and result["mixed"] is True and result["lines"] == 3
    assert result["url"] == f"{API}/{pid}/export/{result['name']}"
    assert Path(result["zip"]).parent == project / "output" / "podcast" / pid / "09_export"
    assert E.verify_checksums(Path(result["folder"])) == []

    # 同一分钟内再点：409，不覆盖
    again = client.post(f"{API}/{pid}/export", json={})
    assert again.status_code == 409 and again.json()["detail"].startswith("这一分钟已经导出过一个交付包了")

    listed = client.get(f"{API}/{pid}/export").json()
    assert len(listed["packages"]) == 1
    pkg = listed["packages"][0]
    assert pkg["name"] == result["name"] and pkg["size"] == result["zip_bytes"] and pkg["files"] == 9 and pkg["bytes"] == result["bytes"]
    assert pkg["mixed"] is True and pkg["lines"] == 3 and pkg["url"] == result["url"] and pkg["created"]
    detail = client.get(f"{API}/{pid}").json()
    assert [e["name"] for e in detail["exports"]] == [pkg["name"]] and detail["busy"]["export"] is None

    dl = client.get(pkg["url"])
    assert dl.status_code == 200 and dl.headers["content-type"].startswith("application/zip")
    assert pkg["name"] in dl.headers.get("content-disposition", "") and dl.content == Path(result["zip"]).read_bytes()
    with zipfile.ZipFile(Path(result["zip"])) as zf:
        assert not any(b"SECRET" in zf.read(n) for n in zf.namelist() if not n.endswith("/"))

    # 路径安全：..、编码过的分隔符、包外真实存在的 zip、不是 zip、不存在
    (project / "output" / "podcast" / pid / "evil.zip").write_bytes(b"zip")
    (project / "evil.zip").write_bytes(b"zip")
    for bad in ("..%2Fevil.zip", "..%5Cevil.zip", "..%2F..%2F..%2Fevil.zip", "sub%2Fx.zip", "nope.zip", "manifest.json", ".hidden.zip",
                f"{pid}.mp3", "%2Fetc%2Fpasswd"):
        r = client.get(f"{API}/{pid}/export/{bad}")
        assert r.status_code == 404, bad
        if "%2F" not in bad:                       # 带编码斜杠的在路由层就落不到处理器（框架 404），其余是我们自己的 404
            assert r.json()["detail"].startswith("没有这个交付包：")
    assert client.get(f"{API}/{pid}/export/../evil.zip").status_code == 404
    assert client.get(f"{API}/NOPE/export/{pkg['name']}").status_code == 404

    # 不带逐句（把这一分钟的包挪走才能再导：接口按「文件夹或 zip 已存在」判重）
    import shutil

    shutil.rmtree(result["folder"])
    Path(result["zip"]).unlink()
    no_lines = client.post(f"{API}/{pid}/export", json={"include_lines": False})
    assert no_lines.status_code == 200, no_lines.text
    done = wait(no_lines.json()["task_id"])
    assert done["status"] == "succeeded" and done["result"]["lines"] == 0 and done["params"]["include_lines"] is False
    assert inside(Path(done["result"]["folder"])) == {"spec.json", "mix.mp3", "shownotes.md", "transcript.txt", "chapters.json",
                                                      "ai_disclosure.md", "manifest.json", "checksums.sha256"}


# ---------------------------------------------------------------------------- 2026-09-22 发第一个 tag 前的审计修复


def test_disclosure_tells_inline_library_and_broken_profiles_apart(tmp_path, home):
    """PC-10 / PC-06：内联档案不再被标成「音色库档案」；音色库档案写真音色 + 档案 id；档案坏了写清用的是占位音色和为什么；
    老清单（voice 记的是档案 id、没有 profile_id）照旧显示成音色库档案。"""

    spec = write_spec(tmp_path, hosts=[{"id": "a", "name": "阿一", "role": "host", "voice": {"engine": "edge", "voice": "zh-CN-XiaoyiNeural", "pitch": "+5Hz"}},
                                       {"id": "b", "name": "阿二", "role": "cohost", "voice": "lib"}])
    synthesize(spec)
    disclosure = E.ai_disclosure(spec, mixed=True, lookup=lookup)
    assert disclosure["hosts"][0]["voice"] == "zh-CN-XiaoyiNeural" and disclosure["hosts"][0]["profile_id"] == "" and disclosure["hosts"][0]["label"] == "zh-CN-XiaoyiNeural"
    assert disclosure["hosts"][1]["voice"] == "mm-1" and disclosure["hosts"][1]["profile_id"] == "lib"
    md = E.disclosure_markdown(disclosure, spec)
    assert "- 阿一（主播）：edge / zh-CN-XiaoyiNeural（内联指定音色，按合成清单）" in md
    assert "- 阿二（搭档）：minimax / mm-1（音色库档案「库里的」（lib），按合成清单）" in md
    assert "音色库档案「zh-CN-XiaoyiNeural」" not in md
    # 老清单：voice 记的是档案 id
    raw = json.loads(A.manifest_path(spec).read_text(encoding="utf-8"))
    raw["hosts"]["b"] = {"voice": "lib", "engine": "minimax", "placeholder": False, "label": "库里的"}
    A.manifest_path(spec).write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    old = E.ai_disclosure(spec, mixed=True, lookup=lookup)
    assert old["hosts"][1]["voice"] == "lib" and old["hosts"][1]["profile_id"] == "lib"
    assert "- 阿二（搭档）：minimax / lib（音色库档案「库里的」（lib），按合成清单）" in E.disclosure_markdown(old, spec)
    # 档案坏了：占位 + 原因
    broken = write_spec(tmp_path, "PC-B", hosts=[{"id": "a", "name": "阿一", "role": "host", "voice": "gone"},
                                                {"id": "b", "name": "阿二", "role": "cohost", "voice": "desc"}])
    library = {"desc": {"id": "desc", "name": "只有描述", "engine": "edge", "voice": ""}}
    A.synthesize_lines(broken, synthesizer=fake_synth, prober=fake_prober, lookup=library.get)
    md = E.disclosure_markdown(E.ai_disclosure(broken, mixed=False, lookup=library.get), broken)
    assert "（占位音色，绑的档案「gone」已不存在，按合成清单）" in md and "（占位音色，绑的档案「desc」只有描述，按合成清单）" in md
    pkg = E.build_package(broken, now=datetime(2026, 9, 16, 12, 40), lookup=library.get)
    manifest = json.loads((Path(pkg["folder"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["hosts"][0]["profile_id"] == "gone" and manifest["hosts"][0]["placeholder"] is True


def test_package_flags_a_mix_made_with_old_inputs(tmp_path, home):
    """PC-04：停顿改过没重混就导出——包照打，但 manifest.audio.mix_stale / mix.stale 为 true，ai_disclosure 的混音行说明成品是旧参数混的。"""

    spec = write_spec(tmp_path)
    synthesize(spec)
    changed = write_spec(tmp_path, pause={"line": 2.0, "segment": 1.2})
    pkg = E.build_package(changed, now=datetime(2026, 9, 16, 12, 41), lookup=lookup)
    folder = Path(pkg["folder"])
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["audio"]["stale"] is False and manifest["audio"]["mix_stale"] is True and manifest["mix"]["stale"] is True
    assert manifest["ai_disclosure"]["mix_stale"] is True
    md = (folder / "ai_disclosure.md").read_text(encoding="utf-8")
    assert "- 混音：ffmpeg 本机混音，成品 mix.mp3（停顿 / 音乐 / 台词改过之后没重混，包里的成品是按旧参数混的）" in md
    # 重混之后再导：干净
    A.mix(changed, ffmpeg="ffmpeg", runner=fake_runner, prober=fake_prober)
    pkg2 = E.build_package(changed, now=datetime(2026, 9, 16, 12, 42), lookup=lookup)
    manifest2 = json.loads((Path(pkg2["folder"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest2["audio"]["mix_stale"] is False and manifest2["mix"]["stale"] is False
    assert "旧参数" not in (Path(pkg2["folder"]) / "ai_disclosure.md").read_text(encoding="utf-8")


_HAN = __import__("re").compile(r"[一-鿿]")


def test_english_episode_disclosure_and_manifest_are_english(tmp_path, home):
    """PC-07：language=en 的 ai_disclosure.md 全英文（中文声明作引文附上）、manifest 的 format_name 用英文名；中文期不变。"""

    spec = write_spec(tmp_path, "PC-EN", language="en", title="Why we scroll",
                      hosts=[{"id": "a", "name": "Zhou", "role": "host"}, {"id": "b", "name": "Xia", "role": "cohost", "voice": "lib"}],
                      segments=[{"id": "G01", "kind": "cold_open", "lines": [{"who": "a", "text": "Two seventeen."}, {"who": "b", "text": "Again?"}]}])
    synthesize(spec)
    pkg = E.build_package(spec, now=datetime(2026, 9, 16, 12, 43), lookup=lookup)
    folder = Path(pkg["folder"])
    md = (folder / "ai_disclosure.md").read_text(encoding="utf-8")
    body = "\n".join(line for line in md.splitlines() if not line.startswith("> "))     # 引文那行是中文声明
    assert not _HAN.search(__import__("re").sub(r'"[^"]*"', "", body)), body          # 引号里是档案名（用户内容）
    assert md.startswith("# AI disclosure and tool list · Why we scroll")
    assert "[AI disclosure] This episode was made with AI assistance: the script was drafted by a text model" in md
    assert "> 本期播客由 AI 辅助制作" in md and "[Generation tools used]" in md
    assert "- Script: drafted by text model gpt-x (source gateway, " in md
    assert "- Zhou (Host): edge / en-US-GuyNeural (placeholder voice, per synthesis manifest)" in md      # 英文期的占位池
    assert '- Xia (Co-host): minimax / mm-1 (voice-library profile "库里的" (lib), per synthesis manifest)' in md
    assert "- Mix: mixed locally with ffmpeg, product mix.mp3" in md and "- Synthesized at: " in md
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format_name"] == "Two hosts" and manifest["language"] == "en"
    assert manifest["ai_disclosure"]["statement"].startswith("This episode was made with AI assistance") and manifest["ai_disclosure"]["basis"].startswith("The tool list is summarised")
    assert manifest["ai_disclosure"]["statement_zh"].startswith("本期播客由 AI 辅助制作") and manifest["ai_disclosure"]["script"]["label"] == "Script"
    assert (folder / "shownotes.md").read_text(encoding="utf-8").count("## Chapters") == 1
    # 手写稿的英文声明；老清单退回口径也是英文
    hand = write_spec(tmp_path, "PC-EN2", language="en", draft={}, hosts=[{"id": "a", "name": "Zhou"}],
                      format="solo", segments=[{"id": "G01", "kind": "cold_open", "lines": [{"who": "a", "text": "Hi."}]}])
    disclosure = E.ai_disclosure(hand, {"lines": [], "updated_at": None}, mixed=False, lookup=lookup)
    assert disclosure["statement"].startswith("This episode was made with AI assistance: the script was written by hand")
    assert disclosure["basis"].endswith("are summarised from the current settings.")
    assert "- Mix: not mixed yet, no product in this package" in E.disclosure_markdown(disclosure, hand)


def _strings(value, path=""):
    """manifest 里每个字符串值连同它的键路径（``ai_disclosure.hosts[0].label`` 这种），方便断言时点名是哪一处。"""

    if isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _strings(item, f"{path}[{i}]")
    elif isinstance(value, str):
        yield path, value


def test_english_episode_manifest_has_no_chinese_labels(tmp_path, home):
    """PC-07 审查：英文期随包发出去的 manifest.json 里也不能剩中文标签——占位音色的 label（含档案坏了的那种）、
    「没混音」「没带逐句」两条 excluded 都按语言写；statement_zh 是特意附的中文声明，档案名是用户内容，不算。中文期不变。"""

    spec = write_spec(tmp_path, "PC-EN3", language="en", title="Why we scroll",
                      hosts=[{"id": "a", "name": "Zhou", "role": "host"}, {"id": "b", "name": "Xia", "role": "cohost", "voice": "gone"}],
                      segments=[{"id": "G01", "kind": "cold_open", "lines": [{"who": "a", "text": "Two seventeen."}, {"who": "b", "text": "Again?"}]}])
    synthesize(spec, mix=False)
    pkg = E.build_package(spec, now=datetime(2026, 9, 16, 12, 45), include_lines=False, lookup=lookup)
    manifest = json.loads((Path(pkg["folder"]) / "manifest.json").read_text(encoding="utf-8"))
    leftovers = [(path, text) for path, text in _strings(manifest) if _HAN.search(text) and path != "ai_disclosure.statement_zh"]
    assert leftovers == []
    hosts = manifest["ai_disclosure"]["hosts"]
    assert hosts[0]["label"] == "placeholder voice en-US-GuyNeural"
    assert hosts[1]["label"].startswith("placeholder voice ") and hosts[1]["label"].endswith(', the bound profile "gone" no longer exists')
    assert "mix.mp3: not mixed yet (per-line audio only)" in manifest["excluded"]
    assert "lines/: per-line audio left out on request" in manifest["excluded"]
    # 中文期照旧：中文 excluded、audio.host_profiles 给的中文 label
    zh = write_spec(tmp_path, "PC-ZH3")
    synthesize(zh, mix=False)
    zh_pkg = E.build_package(zh, now=datetime(2026, 9, 16, 12, 45), include_lines=False, lookup=lookup)
    zh_manifest = json.loads((Path(zh_pkg["folder"]) / "manifest.json").read_text(encoding="utf-8"))
    assert "mix.mp3：还没混音（只有逐句音频）" in zh_manifest["excluded"] and "lines/：按要求没带逐句音频" in zh_manifest["excluded"]
    assert zh_manifest["ai_disclosure"]["hosts"][0]["label"].startswith("占位音色 ")


def test_package_counts_lines_the_same_way_as_the_audio_page(tmp_path, home):
    """PC-03：0 字节 / 跟清单记的大小对不上（半截）的逐句 mp3，配音页不算「已合成」，交付包也不带、不计数——两处一个口径。"""

    spec = write_spec(tmp_path)
    synthesize(spec, mix=False)
    audio = S.audio_dir(spec)
    (audio / "G01_01.mp3").write_bytes(b"")
    (audio / "G01_02.mp3").write_bytes((audio / "G01_02.mp3").read_bytes()[:10])
    state = A.status(spec, lookup=lookup)
    assert [r["file"] for r in state["lines"]] == [None, None, "G02_01.mp3"]
    assert [e["file"] for e in E.synthesized_lines(spec)] == ["G02_01.mp3"]
    pkg = E.build_package(spec, now=datetime(2026, 9, 16, 12, 44), lookup=lookup)
    manifest = json.loads((Path(pkg["folder"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["audio"]["synthesized_lines"] == 1 and [r["file"] for r in manifest["lines"]] == ["lines/G02_01.mp3"]
