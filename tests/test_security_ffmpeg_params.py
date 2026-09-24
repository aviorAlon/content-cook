"""发布前安全自查（2026-09-23）·用户内容进 ffmpeg：插卡 id 不再当文件名（F1）、字幕 / 画面文字按原样画（F2 / F3）、
数据根路径带单引号照样合成（F4）、播客混音命令行不随句数涨（F5）、路径里的 %d 不被当序号模板（F6）。

真 ffmpeg 的几条用 360x640 的小片，几秒跑完；没有 ffmpeg 或中文字体时跳过。TTS 一律用本地正弦波替身，不联网。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from picturebook import assembly as book_assembly
from podcast import audio as PA
from podcast import spec as PS
from storyvideo import post_layers as pl
from storyvideo.assembly import _filter_path, _subtitle_filters

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
FONT = Path("C:/Windows/Fonts/msyh.ttc")
needs_media = pytest.mark.skipif(not (FFMPEG and FFPROBE and FONT.is_file()), reason="needs ffmpeg + ffprobe + msyh.ttc")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="needs ffmpeg + ffprobe")


def _clip(path: Path, color: str, seconds: float = 3.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=360x640:d={seconds}:r=12",
                    "-f", "lavfi", "-i", f"sine=d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(path)], check=True)


def _spec(base: Path) -> dict:
    root = base / "output" / "story_video" / "EP"
    _clip(root / "02_video" / "S1.mp4", "0x336699")
    _clip(root / "02_video" / "S2.mp4", "0x669933")
    font = str(FONT)
    return {
        "id": "EP", "output_root": str(root),
        "delivery": {"width": 360, "height": 640, "fps": 12, "crossfade_seconds": 0},
        "subtitle": {"language": "zh", "band_alpha": 0},
        "post_graphics": {"fonts": {"notebook": font, "ui": font, "subtitle": font}},
        "shots": [{"id": "S1", "dur": 3.0, "lines": []}, {"id": "S2", "dur": 3.0, "lines": []}],
    }


def _bright_pixels(video: Path, t: float, box: tuple[int, int, int, int], out: Path) -> int:
    """成片第 t 秒那一帧里，box 区域内接近白色（字幕字）的像素数。底色是纯蓝 / 纯绿，没有字就是 0。"""

    from PIL import Image

    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t}", "-i", str(video), "-frames:v", "1", str(out)],
                   check=True)
    with Image.open(out) as image:
        crop = image.convert("RGB").crop(box)
        return sum(count for count, (r, g, b) in crop.getcolors(crop.width * crop.height) if min(r, g, b) > 200)


def _dark_pixels(video: Path, t: float, box: tuple[int, int, int, int], out: Path) -> int:
    """画面文字是深色（0x1a1a1a）：box 区域内接近黑色的像素数。"""

    from PIL import Image

    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t}", "-i", str(video), "-frames:v", "1", str(out)],
                   check=True)
    with Image.open(out) as image:
        crop = image.convert("RGB").crop(box)
        return sum(count for count, (r, g, b) in crop.getcolors(crop.width * crop.height) if max(r, g, b) < 60)


# ---------------------------------------------------------------------------- F1：插卡 id 不再当文件名


def test_insert_card_files_are_numbered_not_named_after_ids(tmp_path):
    ui_dir = tmp_path / "ep" / "08_ui"
    inserts = [{"id": iid, "after": "S1", "dur": 1.0, "lines": ["x"]}
               for iid in ("../../../escaped", "D:/p/f", "..\\x", "card%d", "UI_A", "ui_a")]
    spec = {"shots": [{"id": "S1"}], "delivery": {"crossfade_seconds": 0}, "post_graphics": {"phone_ui_inserts": inserts}}
    items = pl.build_timeline(spec, video_dir=tmp_path / "v", ui_dir=ui_dir, durations={"S1": 3.0})
    cards = [x for x in items if x.kind == "ui"]
    assert [x.item_id for x in cards] == [x["id"] for x in inserts]            # id 照旧，只是文件名不用它
    assert [x.source.name for x in cards] == [f"ui_{n:03d}.png" for n in range(6)]
    assert all(x.source.parent == ui_dir for x in cards)


@pytest.mark.parametrize("bad", ["../x", "..\\x", "D:/p/f", "C:\\Users\\x\\photo", "来电?", "a%d", "card%03d", "a:b", "a*b",
                                 'a"b', "a<b", "a|b", "a\tb", "x" * 65])
def test_insert_id_validation_rejects_unsafe_ids(bad):
    from webui.routers.post import _validate_inserts

    errors = _validate_inserts([{"id": bad, "after": "S1", "dur": 1.0, "lines": ["x"]}], {"S1"}, 0.25)
    assert errors and "UI_2" in errors[0], (bad, errors)


def test_insert_id_validation_is_case_insensitive_and_keeps_chinese():
    from webui.routers.post import _validate_inserts

    card = {"after": "S1", "dur": 1.0, "lines": ["x"]}
    assert _validate_inserts([{**card, "id": "UI_A"}, {**card, "id": "ui_a"}], {"S1"}, 0.25)
    assert _validate_inserts([{**card, "id": "s1"}], {"S1"}, 0.25)            # 跟镜号只差大小写
    assert _validate_inserts([{**card, "id": "来电"}, {**card, "id": "UI_2"}, {**card, "id": "片头 卡"}], {"S1"}, 0.25) == []


# ---------------------------------------------------------------------------- F2：字幕 / 朗读文字按原样画（expansion=none）


def test_every_user_text_drawtext_disables_expansion(tmp_path):
    font = FONT if FONT.is_file() else tmp_path / "f.ttf"
    filters = _subtitle_filters(shot_id="S1", lines=[{"zh": "%{pts} C:\\temp 50%"}], windows=[(0.0, 1.0)], text_directory=tmp_path,
                                font_path=font, width=360, height=640, band_alpha=0.0, language="zh")
    drawtexts = [f for f in filters if f.startswith("drawtext=")]
    assert drawtexts and all("expansion=none" in f and "textfile=" in f for f in drawtexts)
    page = {"id": "P1", "segments": [{"lang": "zh", "text": "50% 的 \\ 反斜杠", "index": 0, "local_start": 0.0, "local_end": 1.0}]}
    book = book_assembly.subtitle_filters(page, text_dir=tmp_path, font=str(font), width=360, height=640, zh_size=96, en_size=64,
                                          highlight="0xFFD84D", page_duration=2.0)
    book_drawtexts = [f for f in book if f.startswith("drawtext=")]
    assert book_drawtexts and all("expansion=none" in f for f in book_drawtexts)


@needs_media
def test_subtitle_with_percent_is_drawn(tmp_path):
    """「打了50%折扣」：以前 drawtext 默认展开，报 Stray % 后整条字幕不画，合成照样成功。"""

    spec = _spec(tmp_path)
    spec["shots"][0]["lines"] = [{"who": "A", "zh": "打了50%折扣"}]
    spec["shots"][1]["lines"] = [{"who": "A", "zh": "打了五折"}]          # 对照：没有 % 的同样式台词
    manifest = pl.compose_post(spec, tts=lambda *a: None, project_root=tmp_path)
    video = Path(manifest["output"])
    band = (0, 520, 360, 640)
    assert _bright_pixels(video, 4.5, band, tmp_path / "control.png") > 0, "对照组（无 %）应当画出了字幕"
    assert _bright_pixels(video, 1.5, band, tmp_path / "percent.png") > 0, "带 % 的台词字幕没有画出来"


# ---------------------------------------------------------------------------- F3：画面文字里的 ' % \ , ; [ ] 不再改写滤镜图


NASTY_TEXTS = ["it's 我的", "don't, stop", "100%", "%{pts}", "back\\slash", "'quoted', [x]; a:b"]


@needs_media
def test_notebook_text_with_quotes_and_percent_composes_and_is_drawn_verbatim(tmp_path):
    spec = _spec(tmp_path)
    spec["post_graphics"]["notebook_text"] = [{"shot": "S1", "text": text, "at": 0.2, "size": 30, "y": 0.1 + 0.12 * i}
                                              for i, text in enumerate(NASTY_TEXTS)]
    manifest = pl.compose_post(spec, tts=lambda *a: None, project_root=tmp_path)
    video = Path(manifest["output"])
    assert video.is_file()
    text_dir = Path(spec["output_root"]) / "manifests" / "subtitles"
    assert [(text_dir / f"notebook.{i}.0.txt").read_text(encoding="utf-8") for i in range(len(NASTY_TEXTS))] == NASTY_TEXTS
    graph = (Path(spec["output_root"]) / "manifests" / "post_filtergraph.txt").read_text(encoding="utf-8")
    assert "it's" not in graph and "text='" not in graph                  # 用户文字不再进滤镜图
    assert _dark_pixels(video, 1.0, (0, 40, 360, 460), tmp_path / "nb.png") > 0


# ---------------------------------------------------------------------------- F4：路径里的单引号


def test_filter_path_closes_and_reopens_the_quote_for_apostrophes(tmp_path):
    value = _filter_path(tmp_path / "O'Brien" / "a.txt")
    assert "O'\\\\\\''Brien" in value and "\\:" in value
    assert "'" not in _filter_path(tmp_path / "plain" / "a.txt")


@needs_media
def test_project_root_with_apostrophe_and_brackets_composes(tmp_path):
    """数据根在 C:\\Users\\O'Brien\\… 这类路径下：以前引号提前闭合，所有带字幕的合成都失败。"""

    base = tmp_path / "O'Brien [x], 100%; y=z"
    spec = _spec(base)
    spec["shots"][0]["lines"] = [{"who": "A", "zh": "普通台词"}]
    spec["post_graphics"]["notebook_text"] = [{"shot": "S2", "text": "画面字", "at": 0.2}]
    manifest = pl.compose_post(spec, tts=lambda *a: None, project_root=base)
    video = Path(manifest["output"])
    assert video.is_file()
    assert _bright_pixels(video, 1.5, (0, 520, 360, 640), tmp_path / "sub.png") > 0


# ---------------------------------------------------------------------------- F6：路径里的 %d 不当 image2 的序号模板


def test_still_image_inputs_disable_sequence_patterns(tmp_path):
    item = pl.TimelineItem("ui", "card%d", 2.0, 0.0, tmp_path / "a%d" / "ui_000.png")
    cmd = pl._normalize_item_command(item, {"id": "card%d", "lines": ["x"]}, tmp_path / "out.mov", ffmpeg="ffmpeg", width=360, height=640, fps=12)
    head = cmd[:cmd.index("-i")]                     # 卡图那一路 -i 之前的输入选项
    assert head[head.index("-f") + 1] == "image2" and head[head.index("-pattern_type") + 1] == "none"


@needs_media
def test_data_root_with_percent_d_composes_insert_cards(tmp_path):
    # 路径里只有一处 %d 时 image2 才当它是序号模板（两处就不算合法模板了）：数据根带一处、插卡 id 不带
    base = tmp_path / "shows%d"
    spec = _spec(base)
    spec["post_graphics"]["phone_ui_inserts"] = [{"id": "card", "after": "S1", "dur": 1.0, "layout": "card", "lines": ["插卡"]}]
    manifest = pl.compose_post(spec, tts=lambda *a: None, project_root=base)
    assert Path(manifest["output"]).is_file() and [x["id"] for x in manifest["timeline"]] == ["S1", "card", "S2"]


def test_book_page_decoder_follows_the_real_image_format(tmp_path):
    """强制 image2 以后 ffmpeg 只按扩展名挑解码器；插画一律叫 .png，内容却常是 JPEG / WebP——解码器得按内容选。"""

    from PIL import Image

    for fmt, codec in (("PNG", "png"), ("JPEG", "mjpeg"), ("WEBP", "webp")):
        page = tmp_path / f"{fmt}.png"
        Image.new("RGB", (8, 8), (10, 20, 30)).save(page, format=fmt)
        assert book_assembly._still_input_options(page) == ["-f", "image2", "-pattern_type", "none", "-c:v", codec], fmt
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not an image at all")
    assert book_assembly._still_input_options(junk) == []            # 认不出：交给 ffmpeg 自己探测（改之前的行为）


@needs_ffmpeg
def test_book_pages_that_are_jpeg_or_webp_under_png_names_still_assemble(tmp_path):
    """插画按 .png 落盘，Seedream 等出图接口给的其实是 JPEG（有的是 WebP）。只加 -f image2 -pattern_type none 的话 png 解码器
    读 JPEG 报 Invalid PNG signature，-loop 1 一直重试，装配卡到超时才失败（审查发现的回归）。数据根带 %d，两条一起验。"""

    from PIL import Image

    from picturebook import narration as N
    from storyvideo import subtitles
    from tests.picturebook_fixtures import sine_mp3, small_book

    if not subtitles.resolve_font(None):
        pytest.skip("no CJK font on this machine")
    base = tmp_path / "books%d"
    spec = small_book(base)
    pages = base / "output" / "picturebook" / "PB-S-1" / "05_pages"
    pages.mkdir(parents=True)
    for pid, fmt, color in (("P01", "JPEG", (200, 60, 60)), ("P02", "WEBP", (60, 160, 60)), ("P03", "PNG", (60, 60, 200))):
        Image.new("RGB", (384, 512), color).save(pages / f"{pid}.png", format=fmt)
    sine = sine_mp3(tmp_path / "sine.mp3", 0.6)

    def synth(profile, text, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(sine.read_bytes())

    assert N.synthesize_narration(spec, synthesizer=synth, prober=lambda p: N.probe_duration(p, ffprobe=FFPROBE),
                                  lookup=lambda _: None)["failed"] == []
    out = book_assembly.assemble_book(spec, ffmpeg=FFMPEG, ffprobe=FFPROBE, timeout_seconds=60)
    final = Path(out["final"]["path"])
    assert final.is_file()
    # 封面（JPEG 那页）画面中间是插画的红色，不是黑屏
    frame = tmp_path / "cover.png"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.3", "-i", str(final), "-frames:v", "1", str(frame)],
                   check=True)
    with Image.open(frame) as image:
        r, g, b = image.convert("RGB").getpixel((image.width // 2, image.height // 3))
    assert r > 150 and g < 110 and b < 110, (r, g, b)


# ---------------------------------------------------------------------------- F5：播客混音命令行长度 / 分组预混


def _long_spec(root: Path, segments: int, per_segment: int) -> dict:
    data = {"title": "长", "format": "duo", "target_minutes": 30,
            "hosts": [{"id": "a", "name": "甲"}, {"id": "b", "name": "乙"}],
            "segments": [{"id": f"G{s + 1:02d}", "kind": "topic" if s else "intro", "title": f"环节{s + 1}",
                          "lines": [{"who": "ab"[i % 2], "text": f"第{s + 1}段第{i + 1}句。"} for i in range(per_segment)]}
                         for s in range(segments)]}
    return PS.normalize(data, project_root=root, spec_id="PC-L")


def _synth(profile, text, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"ID3" + b"\x00" * 64 + text.encode("utf-8"))


def _recording_runner(seen: list[dict]):
    """假 ffmpeg：记下每条命令和它的滤镜图（内联的，或 -filter_complex_script 指向的文件——跑完临时目录就删了，得当场读）。"""

    def runner(command):
        command = [str(c) for c in command]
        if "-filter_complex_script" in command:
            graph = Path(command[command.index("-filter_complex_script") + 1]).read_text(encoding="utf-8")
        else:
            graph = command[command.index("-filter_complex") + 1]
        seen.append({"command": command, "graph": graph})
        Path(command[-1]).write_bytes(b"ID3fake")

    return runner


def test_podcast_mix_commands_fit_the_windows_command_line_at_any_length(tmp_path):
    """九百句、数据根路径又长：每一条命令都远低于 CreateProcess 的 32767 字符；amix 一次不超过 MIX_GROUP 路；
    最后那条的滤镜图（垫乐压低段每句一项）只在脚本文件里。"""

    from storyvideo import segmented

    root = tmp_path / ("very_long_data_root_" * 5)
    spec = _long_spec(root, segments=30, per_segment=30)
    PA.synthesize_lines(spec, synthesizer=_synth, prober=lambda p: 2.5, lookup=lambda _: None)
    bed = root / "content" / "assets" / "PC-L" / "bed.mp3"
    bed.parent.mkdir(parents=True, exist_ok=True)
    bed.write_bytes(b"ID3fake")
    spec["music"] = {"bed": "content/assets/PC-L/bed.mp3", "bed_gain": 0.2, "duck": 0.4}
    seen: list[dict] = []
    PA.mix(spec, ffmpeg="ffmpeg", runner=_recording_runner(seen), prober=lambda p: 2.5)
    assert len(seen) >= 2                                                 # 先分组预混，再一遍总混
    for step in seen:
        assert len(subprocess.list2cmdline(step["command"])) < 30000
        amix = re.search(r"amix=inputs=(\d+)", step["graph"])
        assert amix and int(amix.group(1)) <= segmented.MIX_GROUP + 3
    final = seen[-1]
    assert "-filter_complex_script" in final["command"] and "-filter_complex" not in final["command"]
    assert final["graph"].count("between(t,") == 900 and "between(t," not in " ".join(final["command"])


def test_small_podcasts_still_mix_in_one_pass(tmp_path):
    spec = _long_spec(tmp_path, segments=2, per_segment=10)
    PA.synthesize_lines(spec, synthesizer=_synth, prober=lambda p: 1.0, lookup=lambda _: None)
    seen: list[dict] = []
    PA.mix(spec, ffmpeg="ffmpeg", runner=_recording_runner(seen), prober=lambda p: 1.0)
    assert len(seen) == 1 and seen[0]["graph"].count("adelay=") == 20


def test_mix_runs_every_step_and_cleans_up(tmp_path):
    spec = _long_spec(tmp_path, segments=5, per_segment=20)            # 100 句 > MIX_GROUP：分组预混
    PA.synthesize_lines(spec, synthesizer=_synth, prober=lambda p: 1.0, lookup=lambda _: None)
    seen: list[dict] = []
    result = PA.mix(spec, ffmpeg="ffmpeg", runner=_recording_runner(seen), prober=lambda p: 1.0)
    assert len(seen) == 3 and Path(result["mix"]).is_file()            # 两组预混 + 一遍总混
    assert not list(PS.mix_dir(spec).glob(".mix-*"))                    # 临时目录混完就删
    assert [p.name for p in PS.mix_dir(spec).iterdir() if p.name.endswith(".mp3")] == [f"{spec['id']}.mp3"]


@pytest.mark.skipif(sys.platform != "win32", reason="打开着的文件删不掉是 Windows 的行为")
def test_mix_succeeds_even_if_the_work_dir_cannot_be_removed(tmp_path):
    """Windows 上杀毒 / 索引进程短暂占着刚写出的中间文件时临时目录删不掉：成品已经写好，不该因此报错、漏写混音记录
    （任务会被报成「没预料到的错误」，用户去重混）。下一次混音把残留清掉。"""

    spec = _long_spec(tmp_path, segments=5, per_segment=20)            # 100 句：分组预混，临时目录里有中间文件
    PA.synthesize_lines(spec, synthesizer=_synth, prober=lambda p: 1.0, lookup=lambda _: None)
    held = []

    def holding_runner(command):
        Path(command[-1]).write_bytes(b"ID3fake")
        if not held:                                                     # 第一条是预混：输出就在临时目录里
            held.append(open(Path(command[-1]).parent / "premix_held.wv", "wb"))   # noqa: SIM115 - 故意一直开着

    try:
        result = PA.mix(spec, ffmpeg="ffmpeg", runner=holding_runner, prober=lambda p: 1.0)
        assert Path(result["mix"]).is_file() and (PS.mix_dir(spec) / PA.CHAPTERS_NAME).is_file()
        assert list(PS.mix_dir(spec).glob(".mix-*"))                     # 确实留下了删不掉的残留
    finally:
        for handle in held:
            handle.close()
    PA.mix(spec, ffmpeg="ffmpeg", runner=lambda command: Path(command[-1]).write_bytes(b"ID3fake"), prober=lambda p: 1.0)
    assert not list(PS.mix_dir(spec).glob(".mix-*"))                     # 下一次混音顺手清掉了


def test_command_line_too_long_is_explained(monkeypatch):
    def boom(*_a, **_k):
        exc = OSError("[WinError 206] 文件名或扩展名太长。")
        exc.winerror = 206
        raise exc

    monkeypatch.setattr(PA.subprocess, "run", boom)
    with pytest.raises(PA.AudioError, match="命令行超出了系统的长度上限"):
        PA._run(["ffmpeg", "-version"])


@needs_ffmpeg
def test_real_ffmpeg_mixes_a_long_podcast_in_groups(tmp_path):
    """真 ffmpeg：120 句（三组预混 + 一遍总混），成品时长跟时间线一致。"""

    spec = _long_spec(tmp_path, segments=6, per_segment=20)
    tone = tmp_path / "tone.mp3"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=0.3",
                    "-c:a", "libmp3lame", str(tone)], check=True)

    def synth(profile, text, destination: Path):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tone, destination)

    PA.synthesize_lines(spec, synthesizer=synth, prober=lambda p: PA.probe_duration(p, ffprobe=FFPROBE), lookup=lambda _: None)
    result = PA.mix(spec, ffmpeg=FFMPEG, ffprobe=FFPROBE)
    duration = PA.probe_duration(Path(result["mix"]), ffprobe=FFPROBE)
    assert abs(duration - float(result["duration"])) < 0.15, (duration, result["duration"])
