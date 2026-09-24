"""绘本装配（契约 §5）：Ken Burns 表达式 / 字幕层纯函数；真 ffmpeg 三页出片（没有 ffmpeg 就跳过）。"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from picturebook import assembly as A
from picturebook import narration as N
from picturebook.timeline import build_timeline
from storyvideo import subtitles
from storyvideo.assembly import AssemblyError
from tests.picturebook_fixtures import FFMPEG, FFPROBE, lavfi_png, sine_mp3, small_book

needs_ffmpeg = pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="needs ffmpeg + ffprobe on PATH")


def test_kb_scale_auto_amplitude_and_explicit():
    assert A.kb_scale({"kb": {"dir": "push", "scale": None}}, 4.0) == (1.0, 1.05)
    assert A.kb_scale({"kb": {"dir": "push", "scale": None}}, 20.0) == (1.0, 1.12)
    assert A.kb_scale({"kb": {"dir": "left", "scale": None}}, 12.0) == (1.0, 1.085)
    assert A.kb_scale({"kb": {"dir": "pull", "scale": None}}, 4.0) == (1.05, 1.0)
    assert A.kb_scale({"kb": {"dir": "pull", "scale": [1.06, 1.0]}}, 30.0) == (1.06, 1.0)


def test_ken_burns_filter_directions():
    push = A.ken_burns_filter("push", (1.0, 1.08), 5.0, (768, 1024), 1080, 1920)
    assert "scale=w='trunc(" in push and "eval=frame" in push and "crop=1080:1920:x='(iw-1080)/2':y='(ih-1920)/2'" in push
    assert "(1.0000+(0.0800)*t/5.000)" in push
    left = A.ken_burns_filter("left", (1.0, 1.06), 5.0, (768, 1024), 1080, 1920)
    assert "x='(iw-1080)*(1-(t/5.000))'" in left
    assert "x='(iw-1080)*(t/5.000)'" in A.ken_burns_filter("right", (1.0, 1.06), 5.0, (768, 1024), 1080, 1920)
    assert "y='(ih-1920)*(1-(t/5.000))'" in A.ken_burns_filter("up", (1.0, 1.06), 5.0, (768, 1024), 1080, 1920)
    assert "y='(ih-1920)*(t/5.000)'" in A.ken_burns_filter("down", (1.0, 1.06), 5.0, (768, 1024), 1080, 1920)


def test_subtitle_filters_have_white_and_highlight_layers(tmp_path):
    page = {"id": "P02", "segments": [
        {"lang": "zh", "index": 0, "text": "红袜子飞到哪儿去了？", "local_start": 0.6, "local_end": 2.0, "display_start": 0.0, "display_end": 6.0},
        {"lang": "en", "index": 0, "text": "Whoosh — where did the red sock go?", "local_start": 2.3, "local_end": 4.0, "display_start": 0.0, "display_end": 6.0},
    ]}
    font = tmp_path / "font.ttf"
    font.write_bytes(b"x")
    filters = A.subtitle_filters(page, text_dir=tmp_path, font=str(font), width=1080, height=1920, zh_size=96, en_size=64,
                                 highlight="0xFFD27A", page_duration=6.0)
    assert filters[0].startswith("drawbox=")
    zh_white = [f for f in filters if "P02_zh_1.txt" in f and "fontcolor=0xFFFFFF" in f][0]
    zh_hi = [f for f in filters if "P02_zh_1.txt" in f and "fontcolor=0xFFD27A" in f][0]
    assert "enable='between(t,0.000,0.600)+between(t,2.000,6.000)'" in zh_white and "fontsize=96" in zh_white
    assert "enable='between(t,0.600,2.000)'" in zh_hi
    en = [f for f in filters if "P02_en_1.txt" in f]
    assert len(en) == 2 and "fontsize=64" in en[0]
    assert (tmp_path / "P02_zh_1.txt").read_text(encoding="utf-8") == "红袜子飞到哪儿去了？"
    # 16:9 按高度缩放字号
    small = A.subtitle_filters(page, text_dir=tmp_path, font=str(font), width=1920, height=1080, zh_size=96, en_size=64,
                               highlight="0xFFD27A", page_duration=6.0)
    assert "fontsize=54" in [f for f in small if "P02_zh_1.txt" in f][0]


def test_assemble_refuses_without_narration(tmp_path):
    spec = small_book(tmp_path)
    with pytest.raises(AssemblyError, match="这些朗读还没合成"):
        A.assemble_book(spec, ffmpeg="ffmpeg-missing", ffprobe="ffprobe-missing")


def _probe(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    return int(stream["width"]), int(stream["height"]), float(data["format"]["duration"])


@needs_ffmpeg
def test_real_ffmpeg_three_pages(tmp_path):
    if not subtitles.resolve_font(None):
        pytest.skip("no CJK font on this machine")
    spec = small_book(tmp_path)
    spec["age_band"] = "4-6"
    root = tmp_path / "output" / "picturebook" / "PB-S-1"
    for pid, color in (("P01", "0xC8A878"), ("P02", "0x88AACC"), ("P03", "0x445577")):
        lavfi_png(root / "05_pages" / f"{pid}.png", color)
    sine = sine_mp3(tmp_path / "sine.mp3", 0.6)

    def synth(profile, text, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(sine.read_bytes())

    result = N.synthesize_narration(spec, synthesizer=synth, prober=lambda p: N.probe_duration(p, ffprobe=FFPROBE), lookup=lambda _: None)
    assert result["failed"] == [] and len(result["synthesized"]) == 6
    out = A.assemble_book(spec, ffmpeg=FFMPEG, ffprobe=FFPROBE, landscape=True, timeout_seconds=300)
    final = root / "07_edit" / "PB-S-1_book.mp4"
    assert final.is_file() and out["final"]["path"] == str(final)
    timeline = json.loads((root / "manifests" / "timeline.json").read_text(encoding="utf-8"))
    expected = build_timeline(spec, N.segments_for_timeline(spec))["total"]
    assert timeline["total"] == pytest.approx(expected) and timeline["estimated"] is False
    width, height, duration = _probe(final)
    assert (width, height) == (1080, 1920) and duration == pytest.approx(expected, abs=0.75)
    # 分段出片：逐页的字幕链与翻页记在 book_pages.txt（P01 → P02 翻页，P02 → P03 硬切：只有第一页那条有 xfade），
    # 两个画幅共用的混音在 book_filtergraph.txt
    pages_log = (root / "manifests" / "book_pages.txt").read_text(encoding="utf-8")
    assert "FFD27A" in pages_log and pages_log.count("xfade=transition=slideleft:duration=") == 1
    graph = (root / "manifests" / "book_filtergraph.txt").read_text(encoding="utf-8")
    assert "loudnorm=I=-14:TP=-2" in graph and "asplit=1[fl0]" in graph and (root / "07_audio" / "flip.wav").is_file()
    landscape = root / "07_edit" / "PB-S-1_book_16x9.mp4"
    assert landscape.is_file() and _probe(landscape)[:2] == (1920, 1080)


@needs_ffmpeg
def test_synthesized_flip_is_audible_and_reused(tmp_path):
    """没配翻页音效时现合成的那一声要听得见（峰值高于 -40 dB），合成一次以后复用。"""

    spec = small_book(tmp_path)
    spec["page_turn_sfx"] = None
    flip = A._ensure_flip(spec, ffmpeg=FFMPEG, timeout=60)
    assert flip.name == "flip.wav" and flip.is_file()
    probe = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(flip), "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True, check=True)
    peak = float(re.search(r"max_volume:\s*(-?[\d.]+) dB", probe.stderr).group(1))
    assert peak > -40
    stamp = flip.stat().st_mtime_ns
    assert A._ensure_flip(spec, ffmpeg=FFMPEG, timeout=60) == flip and flip.stat().st_mtime_ns == stamp


def test_subtitle_units_follow_font_size_and_width():
    """96 号中文在 1080 宽一行 10 个字（20 单位）；1920 宽横版放得更多；英文按 0.55 倍字宽估。"""

    from picturebook.assembly import subtitle_units
    from storyvideo.assembly import _wrap_text

    assert subtitle_units("zh", 96, 1080) == 20
    assert subtitle_units("zh", 96, 1920) == 36
    assert subtitle_units("zh", 64, 1080) == 30
    assert 12 <= subtitle_units("en", 64, 1080) <= 40
    wrapped, lines = _wrap_text("呼呼呼，风一吹，红袜子飞走了！", maximum_units=subtitle_units("zh", 96, 1080))
    assert lines == 2 and all(len(line) <= 10 for line in wrapped.splitlines())
