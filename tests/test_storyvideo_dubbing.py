"""台词配音（TTS 兜底）：计划、逐句渲染、时间安排、真实 ffmpeg 混音；后期块校验与就绪检查。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import post_layers as pl
from storyvideo.runtime import StoryVideoRunner
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router

FFMPEG = shutil.which("ffmpeg")
FONT = Path("C:/Windows/Fonts/msyh.ttc")
client = TestClient(app)


def _spec(root: Path, mode: str = "all", **extra) -> dict:
    spec = {
        "id": "DUB-T", "output_root": str(root),
        "delivery": {"width": 360, "height": 640, "fps": 12, "crossfade_seconds": 0.25},
        "subtitle": {"chinese_font_size": 20, "safe_bottom_percent": 7, "band_alpha": 0.2, "language": "zh"},
        "cast": {
            "lin": {"name": "林小满", "block": "林小满：黑发。", "voice_tts": {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-4%"}},
            "cat": {"name": "招财猫", "block": "金色招财猫。"},
        },
        "post_graphics": {"fonts": {"notebook": str(FONT), "ui": str(FONT), "subtitle": str(FONT)},
                          "dubbing": {"mode": mode, "duck": 0.1}},
        "shots": [
            {"id": "S1", "dur": 3, "shot": "近景，她的脸", "lines": [{"who": "lin", "zh": "你怎么才来？"}, {"who": "lin", "zh": "说个数啊。"}]},
            {"id": "S2", "dur": 3, "shot": "特写", "speech_mode": "offscreen_narration", "lines": [{"who": "cat", "zh": "要看绩效。"}]},
            {"id": "S3", "dur": 3, "shot": "中景", "lines": []},
        ],
    }
    spec.update(extra)
    return spec


def test_dubbing_plan_reports_missing_tts_and_lip_risk(tmp_path):
    plan = pl.dubbing_plan(_spec(tmp_path))
    assert plan["mode"] == "all" and plan["shots"] == ["S1", "S2"] and plan["duck"] == 0.1
    assert plan["missing_tts_cast"] == {"cat": ["S2"]}
    assert plan["lip_risk"] == ["S1"]          # S2 是画外说话，不算风险；S3 没台词
    assert pl.dubbing_plan(_spec(tmp_path, mode="off"))["shots"] == []
    spec = _spec(tmp_path, mode="shots"); spec["post_graphics"]["dubbing"]["shots"] = ["S1"]
    assert pl.dubbing_plan(spec)["shots"] == ["S1"]


def test_render_dub_lines_uses_cast_tts_and_caches(tmp_path, monkeypatch):
    spec = _spec(tmp_path)
    spec["cast"]["cat"]["voice_tts"] = {"engine": "edge", "voice": "zh-CN-YunxiNeural"}
    calls = []

    def fake_tts(text, dest: Path, profile):
        calls.append((text, profile["voice"]))
        dest.write_bytes(b"mp3")

    monkeypatch.setattr(pl, "probe_media", lambda path, ffprobe="ffprobe": type("I", (), {"duration_seconds": 1.2})())
    rows = pl.render_dub_lines(spec, tmp_path / "07_vo", tts=fake_tts)
    assert [(r["shot"], r["index"], r["who"]) for r in rows] == [("S1", 0, "lin"), ("S1", 1, "lin"), ("S2", 0, "cat")]
    assert calls == [("你怎么才来？", "zh-CN-XiaoxiaoNeural"), ("说个数啊。", "zh-CN-XiaoxiaoNeural"), ("要看绩效。", "zh-CN-YunxiNeural")]
    pl.render_dub_lines(spec, tmp_path / "07_vo", tts=fake_tts)
    assert len(calls) == 3                     # 第二次全部命中缓存
    # 缺 TTS 音色：明确点名
    with pytest.raises(pl.PostLayerError, match="cat"):
        pl.render_dub_lines(_spec(tmp_path), tmp_path / "07_vo", tts=fake_tts)


def test_dub_schedule_defers_and_flags_overflow():
    items = [pl.TimelineItem(kind="shot", item_id="S1", duration=3.0, start=0.0, source=Path("x")),
             pl.TimelineItem(kind="shot", item_id="S2", duration=3.0, start=2.75, source=Path("y"))]
    rows = [{"shot": "S1", "index": 0, "who": "lin", "zh": "一", "path": "a", "duration": 2.0},
            {"shot": "S1", "index": 1, "who": "lin", "zh": "二", "path": "b", "duration": 1.5},
            {"shot": "S2", "index": 0, "who": "cat", "zh": "三", "path": "c", "duration": 0.8}]
    sched, overflow = pl._dub_schedule(items, rows)
    assert sched[0]["abs_start"] == pytest.approx(0.3)                 # 名义起点
    assert sched[1]["abs_start"] == pytest.approx(0.3 + 2.0 + 0.15)    # 第一句没念完，顺延
    assert sched[2]["abs_start"] == pytest.approx(2.75 + 0.3)
    assert len(overflow) == 1 and "S1 第 2 句" in overflow[0]


@pytest.mark.skipif(not FFMPEG or not FONT.is_file(), reason="needs ffmpeg and a CJK font")
def test_compose_post_with_dubbing_end_to_end(tmp_path):
    root = tmp_path / "EP"
    (root / "02_video").mkdir(parents=True)
    for sid in ("S1", "S2", "S3"):
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=360x640:r=12:d=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(root / "02_video" / f"{sid}.mp4")], check=True)
    spec = _spec(root)
    spec["cast"]["cat"]["voice_tts"] = {"engine": "edge", "voice": "zh-CN-YunxiNeural"}

    def fake_dub(text, dest: Path, profile):
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=0.6",
                        "-c:a", "libmp3lame", str(dest)], check=True)

    manifest = pl.compose_post(spec, dub_tts=fake_dub)
    assert Path(manifest["output"]).is_file()
    assert manifest["dub_shots"] == ["S1", "S2"] and len(manifest["dub"]) == 3 and manifest["dub_overflow"] == []
    assert manifest["duration_seconds"] == pytest.approx(8.5, abs=0.6)   # 3×3 − 2×0.25
    graph = (root / "manifests" / "post_filtergraph.txt").read_text(encoding="utf-8")
    assert "volume='if(between(t,0.000,3.000)+between(t,2.750,5.750),0.100,1)'" in graph


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    spec = {
        "id": "DUB-P", "output_root": "output/story_video/DUB-P", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "block": "黑发。"}},
        "shots": [{"id": "S1", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m", "lines": [{"who": "lin", "zh": "你怎么才来？"}]}],
    }
    (content_dir / "DUB-P.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_dubbing_block_validation_readiness_and_precheck(project):
    url = "/api/episodes/storyvideo/DUB-P/post-layers"
    bad = client.put(url, json={"post_graphics": {"dubbing": {"mode": "loud"}}})
    assert bad.status_code == 400 and "dubbing.mode" in bad.json()["detail"]
    bad = client.put(url, json={"post_graphics": {"dubbing": {"mode": "shots", "shots": ["NOPE"]}}})
    assert bad.status_code == 400 and "NOPE" in bad.json()["detail"]
    bad = client.put(url, json={"post_graphics": {"dubbing": {"mode": "all", "duck": 3}}})
    assert bad.status_code == 400 and "duck" in bad.json()["detail"]
    ok = client.put(url, json={"post_graphics": {"dubbing": {"mode": "all", "duck": 0.2}}})
    assert ok.status_code == 200, ok.text
    ready = client.get(url).json()["readiness"]["dubbing"]
    assert ready["shots"] == ["S1"] and ready["missing_tts_cast"] == {"lin": ["S1"]} and ready["lip_risk"] == ["S1"]
    # 制作中也能开关（后期块）
    with StoryVideoRunner(project) as runner:
        runner.prepare("DUB-P")
    off = client.put(url, json={"post_graphics": {"dubbing": {"mode": "off"}}})
    assert off.status_code == 200 and off.json()["status"] == "amended"
    # 开着配音但角色没绑 TTS：后期合成前置拦截并点名
    client.put(url, json={"post_graphics": {"dubbing": {"mode": "all"}}})
    out = project / "output" / "story_video" / "DUB-P" / "02_video"; out.mkdir(parents=True, exist_ok=True)
    (out / "S1.mp4").write_bytes(b"mp4")
    resp = client.post("/api/episodes/storyvideo/DUB-P/post")
    assert resp.status_code == 425 and "lin" in resp.json()["detail"]
