"""配乐时间线层：条目解析、说话时段、真实 ffmpeg 混音（截断 + 淡入淡出 + 压低）、后期块校验、上传/试听接口。"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import post_layers as pl
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router

FFMPEG = shutil.which("ffmpeg")
client = TestClient(app)


def test_music_tracks_defaults_and_missing(tmp_path):
    (tmp_path / "content" / "assets" / "EP" / "music").mkdir(parents=True)
    (tmp_path / "content" / "assets" / "EP" / "music" / "a.mp3").write_bytes(b"x")
    spec = {"post_graphics": {"music": [
        {"file": "content/assets/EP/music/a.mp3", "start": 2, "gain": "bad", "end": 1},
        {"file": "content/assets/EP/music/nope.mp3", "label": "片尾"},
        "junk",
    ]}}
    rows = pl.music_tracks(spec, project_root=tmp_path)
    assert len(rows) == 2
    assert rows[0]["missing"] is False and rows[0]["gain"] == 0.6 and rows[0]["end"] is None and rows[0]["duck"] == 0.3
    assert rows[1]["missing"] is True and rows[1]["label"] == "片尾"


def test_speech_spans():
    items = [pl.TimelineItem("shot", "S1", 3.0, 0.0, Path("a")), pl.TimelineItem("shot", "S2", 3.0, 3.0, Path("b"))]
    spec = {"shots": [{"id": "S1", "lines": [{"zh": "x"}]}, {"id": "S2", "lines": []}]}
    spans = pl._speech_spans(items, spec, [{"shot": "S2", "at": 0.5, "duration": 1.0}], [{"abs_start": 4.0, "abs_end": 4.6}])
    assert spans == [(0.0, 3.0), (3.5, 4.5), (4.0, 4.6)]


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
def test_compose_post_with_music_end_to_end(tmp_path):
    project = tmp_path
    root = project / "output" / "story_video" / "MUS"
    (root / "02_video").mkdir(parents=True)
    for sid in ("S1", "S2"):
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=360x640:r=12:d=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(root / "02_video" / f"{sid}.mp4")], check=True)
    music_dir = project / "content" / "assets" / "MUS" / "music"
    music_dir.mkdir(parents=True)
    # 20 秒的配乐，比 6 秒的时间线长得多：成片不能被拖长
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=20", "-c:a", "libmp3lame", str(music_dir / "bgm.mp3")], check=True)
    spec = {
        "id": "MUS", "output_root": str(root), "prompt_language": "zh",
        "cast": {"a": {"name": "阿一", "block": "x"}},
        "shots": [{"id": "S1", "dur": 3, "cast": ["a"], "lines": [{"who": "a", "zh": "你好"}]}, {"id": "S2", "dur": 3, "cast": ["a"], "lines": []}],
        "post_graphics": {"music": [{"file": "content/assets/MUS/music/bgm.mp3", "start": 1.0, "gain": 0.5, "duck": 0.2, "fade_in": 0.5, "fade_out": 1.0, "label": "bgm"}]},
    }
    manifest = pl.compose_post(spec, project_root=project)
    assert Path(manifest["output"]).is_file()
    assert manifest["music"][0]["label"] == "bgm" and manifest["music_missing"] == []
    assert manifest["duration_seconds"] == pytest.approx(5.75, abs=0.6)    # 2×3 − 0.25 交叉淡化；配乐没有拖长
    graph = (root / "manifests" / "post_filtergraph.txt").read_text(encoding="utf-8")
    assert "afade=t=in:st=0:d=0.500" in graph and "afade=t=out" in graph and "volume=0.500" in graph
    assert "adelay=1000|1000" in graph and "duration=first" in graph
    assert "between(t,0.000,3.000)" in graph and ",0.200,1)'" in graph      # S1 有台词 → 压到 0.2


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    spec = {
        "id": "MUS-P", "output_root": "output/story_video/MUS-P", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "block": "黑发。"}},
        "shots": [{"id": "S1", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m", "lines": []}],
    }
    (content_dir / "MUS-P.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_music_upload_validation_and_readiness(project):
    bad = client.post("/api/episodes/storyvideo/MUS-P/music", json={"filename": "x.txt", "data_base64": base64.b64encode(b"abc").decode()})
    # 不再按扩展名拒，按内容：有 ffprobe 是「不是能读的音频」，没有 ffprobe 退回文件头是「不是可用的音频文件」
    assert bad.status_code == 400 and ("不是能读的音频" in bad.json()["detail"] or "不是可用的音频文件" in bad.json()["detail"])
    # 上传现在用 ffprobe 验内容（发 tag 前的 POST-5：以前 .mp3 里塞任意字节也 200，合成时才炸一屏滤镜图）：
    # 给一段真的声音（WAV 字节，名字叫 .mp3——落盘后缀按真实格式是 .wav）
    import io
    import wave

    sound = io.BytesIO()
    with wave.open(sound, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x10" * 8000)
    up = client.post("/api/episodes/storyvideo/MUS-P/music", json={"filename": "主题曲 demo.mp3", "data_base64": base64.b64encode(sound.getvalue()).decode()})
    assert up.status_code == 200, up.text
    rel = up.json()["file"]
    assert rel.startswith("content/assets/MUS-P/music/") and rel.endswith(".wav")
    assert client.get(up.json()["url"]).status_code == 200
    assert client.get("/api/episodes/storyvideo/MUS-P/music/..%2Fx.mp3").status_code in (404, 422)
    # 校验：缺文件 / 越界 / end<=start
    r = client.put("/api/episodes/storyvideo/MUS-P/post-layers", json={"post_graphics": {"music": [{"file": "content/assets/MUS-P/music/nope.mp3"}]}})
    assert r.status_code == 400 and "文件不存在" in r.json()["detail"]
    r = client.put("/api/episodes/storyvideo/MUS-P/post-layers", json={"post_graphics": {"music": [{"file": rel, "gain": 5}]}})
    assert r.status_code == 400 and "gain" in r.json()["detail"]
    r = client.put("/api/episodes/storyvideo/MUS-P/post-layers", json={"post_graphics": {"music": [{"file": rel, "start": 3, "end": 2}]}})
    assert r.status_code == 400 and "end" in r.json()["detail"]
    r = client.put("/api/episodes/storyvideo/MUS-P/post-layers", json={"post_graphics": {"music": [{"file": rel, "label": "主题曲", "start": 0.5, "gain": 0.7}]}})
    assert r.status_code == 200, r.text
    got = client.get("/api/episodes/storyvideo/MUS-P/post-layers").json()
    assert got["post_graphics"]["music"][0]["label"] == "主题曲"
    assert got["readiness"]["music"] == [{"label": "主题曲", "file": rel, "missing": False, "start": 0.5, "end": None, "reason": ""}]
