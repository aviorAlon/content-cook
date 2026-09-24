"""合成路径的内存、速度与稳健性（2026-09-22 发第一个 tag 前）：绘本按帧网格逐页出片、朗读 / 播客 / 后期版旁白分组预混，
成品原子替换（上一版被播放器开着时等一会儿再放弃），ffmpeg 失败只报一行原因。

实测（一台多核台式机，ffmpeg 6.0；峰值 = 全部 ffmpeg 子进程 RSS 之和，核数用进程亲和性限定；别的任务同时在跑，
耗时是大概数）：

* 绘本 1080x1920，依次是 改之前（全书一条命令）→ 分段第一版（逐页中间片 + 分组 xfade 重编码全书 + 最后一遍再编码一次）
  → 现在（先混声音；逐页一条命令直接按交付口径编码，翻页归前一页；concat 演示器流复制接片）：
  - 10 页 / 4 核：173 s、2.0 GB → 311 s、0.9 GB → 115 s、1.1 GB（两页并行；一页一页来 140 s、0.55 GB）
  - 10 页 / 8 核：128 s、2.6 GB →（审查时实测约为改前的 1.75 倍）→ 85 s、2.2 GB（四页并行）
  - 40 页 / 全部核：144–172 s、7.4 GB → 139–170 s、2.3 GB → 108 s、2.3 GB（四页并行；三页并行 141 s、3.1 GB 是改 -loop 1 之前）
  「本页硬切、下一页翻页」的书改之前整本出不来（concat 滤镜输出时基 1/1000000，接 xfade 对不上）；硬切多的书改之前画面一页页
  往后推（每页按整帧向上取），40 页每 3 页一次硬切到片尾比朗读晚 0.3 s——现在页的起点都取在帧网格上，不累积。
* 播客：内存不涨（150 句 0.19 GB），但全部逐句 mp3 + 内联滤镜图塞进一条命令，150 句左右命令行就超过 Windows 的 32767 字符
  上限（WinError 206，ffmpeg 起不来）。改后超过 MIX_GROUP 句先分组预混：150 句 0.14 GB / 26 s，1000 句（87 分钟）0.17 GB / 173 s。
* 装配版（assemble_episode）上一轮已分组：40 镜 2.3 GB / 80 s，一条命令最多 16 个输入，这里不改。

多数用替身（ffmpeg 只记命令、写出输出文件），最后几条跑真 ffmpeg（小画幅、把组调小，走得到分组那条路）。
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from picturebook import assembly as A
from picturebook import narration as N
from picturebook.spec import normalize_book
from picturebook.timeline import build_timeline as book_timeline
from podcast import audio as PA
from podcast import spec as PS
from storyvideo import assembly as SA
from storyvideo import segmented
from storyvideo.assembly import AssemblyError, _launch_reason, _run, ffmpeg_reason

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="needs ffmpeg + ffprobe on PATH")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="只有 Windows 不让替换正被别的进程读着的文件")
WINDOWS_CMDLINE_LIMIT = 32767


def _inputs(cmd: list[str]) -> list[str]:
    return [cmd[i + 1] for i, x in enumerate(cmd) if x == "-i"]


def _is_final(path: str) -> bool:
    """成片的临时文件（.<名>.<uuid>.tmp.mp4）；翻页音效的临时文件是 .tmp.wav，不算。"""

    name = Path(path).name
    return ".tmp." in name and name.endswith(".mp4")


def _graph_of(cmd: list[str]) -> str:
    if "-filter_complex" in cmd:
        return cmd[cmd.index("-filter_complex") + 1]
    if "-filter_complex_script" in cmd:
        return Path(cmd[cmd.index("-filter_complex_script") + 1]).read_text(encoding="utf-8")
    return ""


def _grid(seconds: float) -> int:
    return math.floor(seconds * 24 + 0.5)


# ---------------------------------------------------------------------------- 绘本：替身跑 40 页


def _book(tmp_path: Path, n: int, *, flips: list[str] | None = None, mode: str = "sentence_alternate") -> dict:
    rows = []
    for i in range(n):
        kind = "cover" if i == 0 else ("back" if i == n - 1 else "page")
        rows.append({"id": f"P{i + 1:02d}", "kind": kind, "scene": "小熊在森林里，中景", "characters": ["bear"],
                     "zh": "小熊走进森林。它抬头看天。", "en": "Little Bear walks in. He looks up.",
                     "kb": {"dir": ("push", "left", "right", "pull")[i % 4]}, "flip": (flips or ["page_turn"] * n)[i]})
    data = {"title_zh": "小熊", "title_en": "Bear", "age_band": "4-6", "bilingual_mode": mode,
            "education": {"cognition": "", "vocab": [], "emotion": ""},
            "characters": {"bear": {"name": "小熊", "name_en": "Little Bear", "block": "棕色小熊"}}, "pages": rows}
    return normalize_book(data, project_root=tmp_path, book_id="PB-R-1")


def _fake_book_media(tmp_path: Path, spec: dict, monkeypatch) -> tuple[list[list[str]], list[str]]:
    """插画是真 PNG（Ken Burns 要读尺寸），朗读是假 mp3、按文件名报时长；ffmpeg 只记命令并写出输出文件。
    返回 (命令, concat 清单的内容)——清单在临时目录里，装配完就清，跑的当下读。"""

    from PIL import Image

    root = Path(spec["output_root"])
    (root / "05_pages").mkdir(parents=True, exist_ok=True)
    for page in spec["pages"]:
        Image.new("RGB", (60, 100), (200, 180, 140)).save(root / "05_pages" / f"{page['id']}.png")

    def synth(profile, text, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"ID3" + text.encode("utf-8"))

    result = N.synthesize_narration(spec, synthesizer=synth, prober=lambda p: 1.2 + (len(p.name) % 3) * 0.3, lookup=lambda _: None)
    assert result["failed"] == []
    commands: list[list[str]] = []
    listings: list[str] = []
    lock = threading.Lock()                    # 逐页的命令在线程池里并行跑

    def run(cmd, *, purpose, timeout_seconds):
        cmd = [str(x) for x in cmd]
        with lock:
            commands.append(cmd)
            if "concat" in cmd:
                listings.append(Path(_inputs(cmd)[0]).read_text(encoding="utf-8"))
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"GOOD" if _is_final(cmd[-1]) else b"x")

    monkeypatch.setattr(A, "_run", run)
    import storyvideo.media as media

    monkeypatch.setattr(media, "validate_media", lambda path, *a, **k: SimpleNamespace(duration_seconds=k.get("expected_duration_seconds")))
    return commands, listings


def _font(tmp_path: Path) -> Path:
    font = tmp_path / "f.ttf"
    font.write_bytes(b"x")
    return font


def test_forty_page_book_renders_each_page_once_on_the_frame_grid(tmp_path, monkeypatch):
    n = 40
    spec = _book(tmp_path, n)
    commands, listings = _fake_book_media(tmp_path, spec, monkeypatch)
    out = A.assemble_book(spec, ffmpeg="ffmpeg", ffprobe="ffprobe", font_path=_font(tmp_path), landscape=False)
    timeline = json.loads((Path(spec["output_root"]) / "manifests" / "timeline.json").read_text(encoding="utf-8"))
    pages = timeline["pages"]
    premix = [c for c in commands if c[-1].endswith(".wv")]
    audio = [c for c in commands if c[-1].endswith("book_audio.m4a")]
    page_cmds = [c for c in commands if re.search(r"1080x1920_\d{3}\.mp4$", c[-1])]
    final = [c for c in commands if _is_final(c[-1])]
    assert len(audio) == 1 and len(final) == 1 and len(page_cmds) == n
    # 顺序：先把声音混好（朗读文件坏了几秒内就报），再逐页出片，最后流复制接片
    assert max(map(commands.index, premix + audio)) < min(map(commands.index, page_cmds))
    assert max(map(commands.index, page_cmds)) < commands.index(final[0])
    # 每页一条命令，只读这一页的插画（翻页时再加下一页的）；直接按交付口径编码，编码线程封顶
    turns = [c for c in page_cmds if "xfade=" in _graph_of(c)]
    assert len(turns) == n - 1 and all(len(_inputs(c)) == (2 if c in turns else 1) for c in page_cmds)
    assert all(c[c.index("-preset") + 1] == "medium" and c[c.index("-crf") + 1] == "20" for c in page_cmds)
    assert all(c[c.index("-threads") + 1] == str(A.PAGE_ENCODER_THREADS) for c in page_cmds)
    # 帧网格：一页接一页正好铺满 [0, 全书帧数)；页的局部时间从它起点那一帧算；翻页那几帧归前一页，offset / duration 都是整帧
    plan = A.frame_plan(pages)
    assert plan[0]["first"] == 0 and plan[-1]["end"] == _grid(timeline["total"])
    assert all(a["end"] == b["first"] for a, b in zip(plan, plan[1:]))
    by_name = {Path(c[-1]).name: c for c in page_cmds}
    for index, (page, row) in enumerate(zip(pages, plan)):
        graph = _graph_of(by_name[f"1080x1920_{index:03d}.mp4"])
        frames = row["end"] - row["first"]
        assert row["local"] == row["first"] - _grid(page["start"])
        head = "[0:v]loop=loop=-1:size=1:start=0,fps=24," + (f"setpts=PTS-STARTPTS+{row['local']}" if row["local"] else "setpts=PTS-STARTPTS")
        assert graph.startswith(f"{head},trim=end_frame={frames},scale=w=")
        if index + 1 < n:
            fade = re.search(r"xfade=transition=slideleft:duration=([\d.]+):offset=([\d.]+),trim=end_frame=(\d+)\[v\]$", graph)
            assert row["turn"] == _grid(pages[index + 1]["start"]) and 9 <= row["end"] - row["turn"] <= 10
            assert float(fade[1]) * 24 == pytest.approx(row["end"] - row["turn"], abs=1e-3)
            assert float(fade[2]) * 24 == pytest.approx(row["turn"] - row["first"], abs=1e-3) and int(fade[3]) == frames
            # 下一页开头那几帧：从它自己的第 0 帧画起
            assert f"[1:v]loop=loop=-1:size=1:start=0,fps=24,setpts=PTS-STARTPTS,trim=end_frame={row['end'] - row['turn']}," in graph
    # 接片：concat 演示器按页序流复制，配上混好的声音；不再解码、不再编码
    assert final[0][final[0].index("-c") + 1] == "copy" and _inputs(final[0])[1].endswith("book_audio.m4a")
    assert [Path(p).name for p in re.findall(r"file '([^']+)'", listings[0])] == [f"1080x1920_{i:03d}.mp4" for i in range(n)]
    # 朗读 160 段 > MIX_GROUP：先分组预混，每条预混命令不超过一组；混音那条只有底轨 + 几段预混 + 翻页音效
    segs = [s for p in pages for s in p["segments"]]
    assert len(segs) == 4 * n and [len(_inputs(c)) for c in premix] == [64, 64, 32]
    assert len(_inputs(audio[0])) == 1 + 3 + 1
    assert max(len(_inputs(c)) for c in commands) <= segmented.MIX_GROUP
    # 朗读的落点与改前一致：预混组的起点 + 组内相对采样数 = 按毫秒铺的绝对位置
    audio_graph = (Path(spec["output_root"]) / "manifests" / "book_filtergraph.txt").read_text(encoding="utf-8")
    bases = [int(x) for x in re.findall(r"adelay=(\d+)S\|", audio_graph)]
    placed = []
    for cmd, base in zip(premix, bases):
        placed += [base + int(x) for x in re.findall(r"adelay=(\d+)S\|", _graph_of(cmd))]
    assert sorted(placed) == sorted(segmented.ms_to_sample(int(round(s["start"] * 1000)), 48000) for s in segs)
    assert audio_graph.endswith("loudnorm=I=-14:TP=-2:LRA=11[aout]") and f"asplit={n - 1}" in audio_graph
    # 成片是原子替换上去的；工作目录与临时文件都清掉
    edit = Path(spec["output_root"]) / "07_edit"
    assert Path(out["final"]["path"]).read_bytes() == b"GOOD"
    assert sorted(p.name for p in edit.iterdir() if p.name.startswith(".")) == []


def test_small_book_keeps_the_single_mix_command_shape(tmp_path, monkeypatch):
    """朗读段不超过 MIX_GROUP：不预混，混音那条每段一路输入、按毫秒 adelay（与改之前同一种写法）；硬切的页没有 xfade。"""

    spec = _book(tmp_path, 3, flips=["page_turn", "cut", "cut"])
    commands, _ = _fake_book_media(tmp_path, spec, monkeypatch)
    A.assemble_book(spec, ffmpeg="ffmpeg", ffprobe="ffprobe", font_path=_font(tmp_path), landscape=False)
    assert not [c for c in commands if c[-1].endswith(".wv")]
    audio = [c for c in commands if c[-1].endswith("book_audio.m4a")][0]
    graph = _graph_of(audio)
    timeline = json.loads((Path(spec["output_root"]) / "manifests" / "timeline.json").read_text(encoding="utf-8"))
    starts = [int(round(s["start"] * 1000)) for p in timeline["pages"] for s in p["segments"]]
    assert re.findall(r"adelay=(\d+)\|", graph)[:len(starts)] == [str(ms) for ms in starts]
    assert len(_inputs(audio)) == 1 + len(starts) + 1          # 底轨 + 每段一路 + 翻页音效
    pages = [c for c in commands if re.search(r"_\d{3}\.mp4$", c[-1])]
    assert ["xfade=" in _graph_of(c) for c in pages] == [True, False, False]


def test_failed_book_render_keeps_the_previous_final_and_leaves_no_half_file(tmp_path, monkeypatch):
    spec = _book(tmp_path, 4)
    commands, _ = _fake_book_media(tmp_path, spec, monkeypatch)
    edit = Path(spec["output_root"]) / "07_edit"
    edit.mkdir(parents=True, exist_ok=True)
    final = edit / "PB-R-1_book.mp4"
    final.write_bytes(b"PREVIOUS")

    def dying(cmd, *, purpose, timeout_seconds):
        commands.append([str(x) for x in cmd])
        Path(cmd[-1]).write_bytes(b"half")                        # ffmpeg 被杀时留下的半截输出
        if _is_final(cmd[-1]) or "page P03" in purpose:
            raise AssemblyError(f"ffmpeg 出错（{purpose}）：Cannot allocate memory——完整报错写进了日志 storystudio.log")

    monkeypatch.setattr(A, "_run", dying)
    with pytest.raises(AssemblyError, match="page P03"):
        A.assemble_book(spec, ffmpeg="ffmpeg", ffprobe="ffprobe", font_path=_font(tmp_path), landscape=False)
    assert final.read_bytes() == b"PREVIOUS" and sorted(p.name for p in edit.iterdir() if p.name.startswith(".")) == []


def test_frame_plan_has_no_drift_with_many_hard_cuts(tmp_path):
    """硬切多的书：每页的第一帧就是它起点那一帧（四舍五入到帧网格），全书帧数 = 时间轴总长 × 24 取整——改之前每页按整帧向上取、
    一页页往后推，40 页每 3 页一次硬切到片尾画面比朗读晚 0.3 s。"""

    n = 30
    flips = ["page_turn" if i % 6 == 5 else "cut" for i in range(n)]      # 25 次硬切、4 次翻页
    timeline = book_timeline(_book(tmp_path, n, flips=flips))
    pages = timeline["pages"]
    plan = A.frame_plan(pages)
    assert sum(1 for p in pages[:-1] if not p["transition_out"]) >= 20
    assert plan[0]["first"] == 0 and all(a["end"] == b["first"] for a, b in zip(plan, plan[1:]))
    assert plan[-1]["end"] == _grid(timeline["total"]) and abs(plan[-1]["end"] - timeline["total"] * 24) <= 0.5
    for index, (page, row) in enumerate(zip(pages, plan)):
        appears = row["first"] if index == 0 or not pages[index - 1]["transition_out"] else plan[index - 1]["turn"]
        assert appears == _grid(page["start"]), page["id"]


def test_frame_plan_refuses_a_page_too_short_for_two_page_turns():
    pages = [{"id": "P01", "start": 0.0, "end": 3.0, "duration": 3.0, "transition_out": 0.4},
             {"id": "P02", "start": 2.6, "end": 3.2, "duration": 0.6, "transition_out": 0.4},
             {"id": "P03", "start": 2.8, "end": 6.0, "duration": 3.2, "transition_out": 0.0}]
    with pytest.raises(AssemblyError, match="第 P02 页只有 0.60 秒，放不下前后两次翻页"):
        A.frame_plan(pages)
    pages[1]["transition_out"] = 0.0                               # 改成硬切就排得下
    pages[2]["start"] = 3.2
    assert [row["turn"] for row in A.frame_plan(pages)] == [62, None, None]


def test_half_written_intermediates_are_never_reused(tmp_path, monkeypatch):
    """按「文件在不在」复用的中间文件（旁白 / 配音 mp3、翻页音效）：合成到一半失败不能留下半截文件被下一次当成现成的。"""

    from storyvideo import post_layers as pl

    monkeypatch.setattr(pl, "probe_media", lambda path, ffprobe="ffprobe": SimpleNamespace(duration_seconds=1.0))
    spec = {"voice_over": {"lines": [{"shot": "S1", "at": 0.5, "zh": "今晚有雨。"}]}}
    calls: list[str] = []

    def broken(text, dest, *_rest):
        calls.append("broken")
        Path(dest).write_bytes(b"half an mp3")           # 断网前已经写了一半
        raise RuntimeError("edge-tts failed: connection reset")

    with pytest.raises(RuntimeError):
        pl.render_vo_lines(spec, tmp_path / "vo", tts=broken)
    assert [p.name for p in (tmp_path / "vo").iterdir()] == []

    def working(text, dest, *_rest):
        calls.append("working")
        Path(dest).write_bytes(b"ID3 full")

    rows = pl.render_vo_lines(spec, tmp_path / "vo", tts=working)
    assert calls == ["broken", "working"] and Path(rows[0]["path"]).read_bytes() == b"ID3 full"
    # 翻页音效同理：ffmpeg 半路失败不留 flip.wav
    book = _book(tmp_path / "pb", 2)

    def dying(cmd, *, purpose, timeout_seconds):
        Path(cmd[-1]).write_bytes(b"half")
        raise AssemblyError("ffmpeg 出错（synthesize page-flip sfx）：boom——完整报错写进了日志 storystudio.log")

    monkeypatch.setattr(A, "_run", dying)
    with pytest.raises(AssemblyError):
        A._ensure_flip(book, ffmpeg="ffmpeg", timeout=5)
    audio = Path(book["output_root"]) / "07_audio"
    assert not (audio / "flip.wav").exists() and list(audio.iterdir()) == []


def test_post_layers_premix_many_narration_and_dub_lines(tmp_path, monkeypatch):
    """后期版最后一遍的旁白 + 配音也是每句一路输入：句子多于 MIX_GROUP 时先分组预混（在出画面之前），落点不变，旁白压原声照旧。"""

    from storyvideo import post_layers as pl
    from tests.test_post_compose_segmented import FakeMedia, _silent_tts, _spec

    monkeypatch.setattr(segmented, "MIX_GROUP", 4)
    media = FakeMedia(monkeypatch, {f"S{i + 1}": 3.0 for i in range(4)})
    spec = _spec(tmp_path, 4)
    spec["voice_over"] = {"lines": [{"shot": f"S{k // 3 + 1}", "at": 0.2 + (k % 3) * 0.9, "zh": f"第{k}句旁白"} for k in range(11)]}
    manifest = pl.compose_post(spec, tts=_silent_tts, project_root=tmp_path)
    premix = [c for c in media.commands if c[-1].endswith(".wv")]
    normalise = [c for c in media.commands if Path(c[-1]).name.startswith("seg_")]
    final = media.commands[-1]
    assert [len(_inputs(c)) for c in premix] == [4, 4, 3]
    assert max(map(media.commands.index, premix)) < min(map(media.commands.index, normalise))
    assert len(_inputs(final)) == 1 + 3 and max(len(_inputs(c)) for c in premix) <= 4
    graph = (Path(spec["output_root"]) / "manifests" / "post_filtergraph.txt").read_text(encoding="utf-8")
    assert "[axq]" in graph and "volume=1.0[vo" not in graph      # 旁白时段压原声照旧；逐句的输入没了
    bases = [int(x) for x in re.findall(r"adelay=(\d+)S\|", graph)]
    placed = [base + int(x) for cmd, base in zip(premix, bases) for x in re.findall(r"adelay=(\d+)S\|", _graph_of(cmd))]
    starts = [pl.absolute_time([pl.TimelineItem(kind="shot", item_id=x["id"], duration=x["duration"], start=x["start"], source=Path("x"))
                                for x in manifest["timeline"]], row["shot"], row["at"]) for row in manifest["vo"]]
    assert sorted(placed) == sorted(segmented.ms_to_sample(int(round(s * 1000)), 48000) for s in starts)


# ---------------------------------------------------------------------------- 绘本：真 ffmpeg（小画幅）


def _solid_png(path: Path, rgb: tuple[int, int, int]) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (90, 160), rgb).save(path, format="PNG")          # 扩展名可以不是 .png（冒充 mp3 的那张）


def _tone(path: Path, seconds: float, freq: int, *, volume: float = 1.0, codec: str = "libmp3lame") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-af", f"volume={volume}", "-ar", "44100", "-c:a", codec, str(path)], check=True)
    return path


def _onsets(path: Path) -> list[float]:
    text = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-af", "silencedetect=n=-45dB:d=0.12", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    return [float(x) for x in re.findall(r"silence_end: ([\d.]+)", text)]


def _duration_and_frames(path: Path) -> tuple[float, int]:
    info = json.loads(subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-show_entries",
                                      "stream=codec_type,nb_read_frames,duration", "-of", "json", str(path)],
                                     capture_output=True, text=True, check=True).stdout)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    return float(video["duration"]), int(video["nb_read_frames"])


def _frame_rgb(path: Path, frame: int, *, left: float = 0.0, right: float = 1.0) -> tuple[int, int, int]:
    """第 frame 帧（从 0 数）横向 [left, right) 那一条、上 40% 的平均颜色（字幕带在下面）。"""

    crop = f"crop=iw*{right - left:.3f}:ih*0.4:iw*{left:.3f}:0,scale=1:1"
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{frame}),{crop}", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return raw[0], raw[1], raw[2]


def _close(got: tuple[int, int, int], want: tuple[int, int, int]) -> bool:
    return all(abs(a - b) < 40 for a, b in zip(got, want))


_COLORS = [(220, 40, 40), (40, 200, 40), (40, 60, 220), (230, 220, 40), (40, 210, 220), (210, 40, 210), (240, 240, 240)]


def _real_book(tmp_path: Path, flips: list[str], monkeypatch, *, size: tuple[int, int] = (180, 320)) -> tuple[dict, Path]:
    from storyvideo import subtitles

    if not subtitles.resolve_font(None):
        pytest.skip("no CJK font on this machine")
    monkeypatch.setattr(A, "PORTRAIT", size)
    spec = _book(tmp_path, len(flips), flips=flips, mode="zh_only")
    root = Path(spec["output_root"])
    for k, page in enumerate(spec["pages"]):
        _solid_png(root / "05_pages" / f"{page['id']}.png", _COLORS[k % len(_COLORS)])
    silence = tmp_path / "content" / "silence.wav"
    silence.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "0.3", str(silence)], check=True)
    spec["page_turn_sfx"] = "content/silence.wav"            # 翻页音效换成静音，朗读的起点才量得准
    tones = [_tone(tmp_path / f"tone{k}.mp3", 0.5, 400 + 60 * k) for k in range(3)]
    counter = {"k": 0}

    def synth(profile, text, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tones[counter["k"] % 3], destination)
        counter["k"] += 1

    assert N.synthesize_narration(spec, synthesizer=synth, prober=lambda p: N.probe_duration(p, ffprobe=FFPROBE), lookup=lambda _: None)["failed"] == []
    return spec, root


@needs_ffmpeg
def test_real_book_matches_the_frame_grid_including_cut_before_page_turn(tmp_path, monkeypatch):
    """7 页、朗读组调成 4（走预混）；第 2 页硬切后接翻页——改之前这种书整本出不来。帧数 = 帧网格总帧数；每页中段是这一页的
    颜色；翻页中间那一帧左边是前一页、右边是后一页（滑动，不是硬切）；朗读的起点与不预混时逐个相同。"""

    flips = ["page_turn", "cut", "page_turn", "page_turn", "page_turn", "cut", "cut"]
    spec, root = _real_book(tmp_path, flips, monkeypatch)
    results = {}
    for group in (4, 64):                                     # 4 = 预混（7 段）；64 = 不预混，混音那条逐段一路输入（改前的写法）
        monkeypatch.setattr(segmented, "MIX_GROUP", group)
        out = A.assemble_book(spec, ffmpeg=FFMPEG, ffprobe=FFPROBE, landscape=False, timeout_seconds=300)
        final = Path(out["final"]["path"])
        results[group] = (_onsets(final), _duration_and_frames(final))
        if group == 4:
            timeline = json.loads((root / "manifests" / "timeline.json").read_text(encoding="utf-8"))
            plan = A.frame_plan(timeline["pages"])
            for k, (page, row) in enumerate(zip(timeline["pages"], plan)):
                body_end = row["turn"] if row["turn"] is not None else row["end"]
                assert _close(_frame_rgb(final, (row["first"] + body_end) // 2), _COLORS[k]), page["id"]
                if row["turn"] is not None:
                    middle = (row["turn"] + row["end"]) // 2
                    assert _close(_frame_rgb(final, middle, right=0.2), _COLORS[k]), (page["id"], "slide out")
                    assert _close(_frame_rgb(final, middle, left=0.8), _COLORS[k + 1]), (page["id"], "slide in")
            assert "xfade=transition=slideleft" in (root / "manifests" / "book_pages.txt").read_text(encoding="utf-8")
    total = timeline["total"]
    starts = [s["start"] for p in timeline["pages"] for s in p["segments"]]
    (onsets, (duration, frames)), (direct_onsets, (direct_duration, direct_frames)) = results[4], results[64]
    # silencedetect 在片尾那段静音结束（EOF）时还会报一次 silence_end，那不是朗读的起点
    onsets, direct_onsets = [t for t in onsets if t < total - 0.05], [t for t in direct_onsets if t < total - 0.05]
    assert frames == direct_frames == plan[-1]["end"] == _grid(total) and abs(duration - total) <= 1 / 24
    assert len(onsets) == len(starts) == len(direct_onsets)
    assert all(abs(a - b) < 0.005 for a, b in zip(onsets, direct_onsets))
    assert all(abs(a - b) < 0.05 for a, b in zip(onsets, starts))
    assert sorted(p.name for p in (root / "07_edit").iterdir() if p.name.startswith(".")) == []


@needs_ffmpeg
def test_real_book_with_many_hard_cuts_does_not_drift(tmp_path, monkeypatch):
    """22 页、20 次硬切：总帧数 = 时间轴总长落在帧网格上；最后几次硬切，切点那一帧已经是新的一页、前一帧还是上一页。"""

    n = 22
    flips = ["page_turn" if i in (7, 14) else "cut" for i in range(n)]
    spec, root = _real_book(tmp_path, flips, monkeypatch, size=(90, 160))
    out = A.assemble_book(spec, ffmpeg=FFMPEG, ffprobe=FFPROBE, landscape=False, timeout_seconds=300)
    final = Path(out["final"]["path"])
    timeline = json.loads((root / "manifests" / "timeline.json").read_text(encoding="utf-8"))
    duration, frames = _duration_and_frames(final)
    assert frames == _grid(timeline["total"]) and abs(duration - timeline["total"]) <= 1 / 24
    for k in (n - 3, n - 2, n - 1):
        cut = _grid(timeline["pages"][k]["start"])
        assert _close(_frame_rgb(final, cut), _COLORS[k % len(_COLORS)]) and _close(_frame_rgb(final, cut - 1), _COLORS[(k - 1) % len(_COLORS)])


@needs_ffmpeg
def test_real_bad_narration_is_named_before_any_page_is_rendered(tmp_path, monkeypatch):
    """朗读文件坏了（这里是一张 PNG 冒充 mp3）：混音在出画面之前跑，几秒内就报，报错点名是哪个文件、不带滤镜图。"""

    spec, root = _real_book(tmp_path, ["page_turn", "cut", "cut"], monkeypatch)
    bad = sorted((root / "07_audio").glob("P02_zh_*.mp3"))[0]
    _solid_png(bad, (1, 2, 3))
    purposes: list[str] = []
    real_run = A._run

    def spy(cmd, *, purpose, timeout_seconds):
        purposes.append(purpose)
        return real_run(cmd, purpose=purpose, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(A, "_run", spy)
    with pytest.raises(AssemblyError) as exc:
        A.assemble_book(spec, ffmpeg=FFMPEG, ffprobe=FFPROBE, landscape=False, timeout_seconds=300)
    assert str(exc.value) == (f"ffmpeg 出错（mix picture book audio）：{bad.name} 读不出音频（文件坏了，或者不是音频文件）"
                              "——完整报错写进了日志 storystudio.log")
    assert not [p for p in purposes if "page" in p]


# ---------------------------------------------------------------------------- 播客：替身跑 150 / 1000 句


def _podcast(tmp_path: Path, lines: int, *, bed: bool = True) -> dict:
    segments, n = [], 0
    for g in range((lines + 24) // 25):
        rows = []
        for _ in range(min(25, lines - n)):
            rows.append({"who": "a" if n % 2 == 0 else "b", "text": f"第{n + 1}句，短视频为什么让人停不下来。", "cue": "停顿" if n % 17 == 5 else ""})
            n += 1
        segments.append({"id": f"G{g + 1:02d}", "kind": "cold_open" if g == 0 else "topic", "title": f"环节{g + 1}", "lines": rows})
    music = {}
    if bed:
        folder = tmp_path / "content" / "assets" / "PC-R"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "bed.mp3").write_bytes(b"ID3bed")
        music = {"bed": "content/assets/PC-R/bed.mp3", "bed_gain": 0.2, "duck": 0.35}
    data = {"title": "压测", "format": "duo", "target_minutes": 60, "hosts": [{"id": "a", "name": "阿一"}, {"id": "b", "name": "阿二"}],
            "segments": segments, "music": music}
    return PS.normalize(data, project_root=tmp_path, spec_id="PC-R")


def _fake_synth(profile, text, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"ID3" + text.encode("utf-8"))


def _fake_prober(path):
    return 2.0 + (len(path.read_bytes()) % 7) * 0.5


def _fake_lines(spec: dict) -> None:
    result = PA.synthesize_lines(spec, synthesizer=_fake_synth, prober=_fake_prober, lookup=lambda _: None)
    assert result["failed"] == []


@pytest.mark.parametrize("lines", [150, 1000])
def test_long_podcast_is_premixed_in_groups_and_every_command_fits_the_windows_limit(tmp_path, monkeypatch, lines):
    # 数据根放在一条很长的路径下（用户名长 / 数据根放得深）：改前 150 句这里的命令行就超过 32767 字符
    deep = tmp_path / ("很长的用户目录名" * 6) / "StoryStudio"
    spec = _podcast(deep, lines)
    _fake_lines(spec)
    commands: list[list[str]] = []
    graphs: list[str] = []

    def runner(command):
        command = [str(x) for x in command]
        commands.append(command)
        graphs.append(_graph_of(command))
        Path(command[-1]).write_bytes(b"ID3mix")

    result = PA.mix(spec, ffmpeg="ffmpeg", runner=runner, prober=lambda p: 30.0)
    premix, final = commands[:-1], commands[-1]
    assert premix and all(c[-1].endswith(".wv") for c in premix)
    assert max(len(_inputs(c)) for c in commands) <= segmented.MIX_GROUP + 3
    # 一条命令最多 MIX_GROUP 个输入：路径按 Windows 的 260 字符上限算也只有两万多字符
    assert max(len(subprocess.list2cmdline(c)) for c in commands) < WINDOWS_CMDLINE_LIMIT
    # 最后一遍的滤镜图在文件里；垫乐压低仍按每一句算（每句一个 between）
    assert "-filter_complex_script" in final and "-filter_complex" not in final
    timeline = PA.build_timeline(spec, PA.load_manifest(spec), music_durations={})
    items = [i for i in timeline["items"] if i["kind"] == "line"]
    assert len(items) == lines and graphs[-1].count("between(t,") == lines
    assert graphs[-1].endswith("loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100[out]")
    # 每一句的落点与改前一致：一路一路往上找到最后那条命令，（各级组的起点 +）组内相对采样数 = 按毫秒铺的绝对位置
    relative: dict[str, int] = {}          # 文件 → 相对它被混进的那一组的起点（采样数）
    parent: dict[str, str] = {}            # 文件 → 它被混进的那个预混文件
    for cmd, graph in zip(premix, graphs):
        for path, offset in zip(_inputs(cmd), re.findall(r"adelay=(\d+)S\|", graph)):
            relative[path], parent[path] = int(offset), cmd[-1]
    final_bases = dict(zip(_inputs(final)[1:], (int(x) for x in re.findall(r"adelay=(\d+)S\|", graphs[-1]))))

    def absolute(path: str) -> int:
        return final_bases[path] if path in final_bases else absolute(parent[path]) + relative[path]

    root = PS.audio_dir(spec)
    got = sorted(absolute(str(root / i["file"])) for i in items)
    assert got == sorted(segmented.ms_to_sample(int(round(i["start"] * 1000)), 44100) for i in items)
    assert result["duration"] == timeline["total"] and Path(result["mix"]).read_bytes() == b"ID3mix"
    # 预混中间文件与临时成品都清掉
    assert sorted(p.name for p in PS.mix_dir(spec).iterdir() if p.name.startswith(".")) == []


def test_short_podcast_keeps_the_single_command(tmp_path):
    spec = _podcast(tmp_path, 30)
    _fake_lines(spec)
    commands: list[list[str]] = []
    graphs: list[str] = []

    def runner(command):
        commands.append([str(x) for x in command])
        graphs.append(_graph_of(commands[-1]))           # 滤镜图文件在临时目录里，混完就清：跑的当下读
        Path(command[-1]).write_bytes(b"ID3")

    PA.mix(spec, ffmpeg="ffmpeg", runner=runner, prober=lambda p: 30.0)
    assert len(commands) == 1 and len(_inputs(commands[0])) == 1 + 30 + 1
    # 与分组之前同一张滤镜图：每句一路输入、按毫秒 adelay
    assert graphs[0] == _graph_of(PA.mix_command(spec, PA.build_timeline(spec, PA.load_manifest(spec), music_durations={}),
                                                 music={"bed": PS.resolve_asset(spec, spec["music"]["bed"])}))
    assert "adelay=0|0" in graphs[0]


def test_failed_mix_keeps_the_previous_product_and_leaves_no_half_file(tmp_path):
    spec = _podcast(tmp_path, 80)
    _fake_lines(spec)
    out = PS.mix_dir(spec)
    out.mkdir(parents=True, exist_ok=True)
    good = out / "PC-R.mp3"
    good.write_bytes(b"PREVIOUS")

    def dying(command):
        Path(command[-1]).write_bytes(b"half")               # 混到一半被杀
        if not str(command[-1]).endswith(".wv"):
            raise PA.AudioError("混音失败：Cannot allocate memory——完整报错写进了日志 storystudio.log")

    with pytest.raises(PA.AudioError, match="Cannot allocate memory"):
        PA.mix(spec, ffmpeg="ffmpeg", runner=dying, prober=lambda p: 30.0)
    assert good.read_bytes() == b"PREVIOUS"
    assert sorted(p.name for p in out.iterdir()) == ["PC-R.mp3"]
    # 没产出文件（ffmpeg 退出 0 却什么都没写）同样不碰上一版
    with pytest.raises(PA.AudioError, match="混音没有产出文件"):
        PA.mix(spec, ffmpeg="ffmpeg", runner=lambda c: None, prober=lambda p: 30.0)
    assert good.read_bytes() == b"PREVIOUS" and sorted(p.name for p in out.iterdir()) == ["PC-R.mp3"]


@needs_ffmpeg
def test_real_premixed_podcast_lands_every_line_where_the_single_command_did(tmp_path, monkeypatch):
    spec = _podcast(tmp_path, 9, bed=False)
    tones = [_tone(tmp_path / f"t{k}.mp3", 0.4 + 0.1 * k, 300 + 80 * k) for k in range(3)]
    counter = {"k": 0}

    def synth(profile, text, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tones[counter["k"] % 3], destination)
        counter["k"] += 1

    prober = lambda p: PA.probe_duration(p, ffprobe=FFPROBE)  # noqa: E731
    assert PA.synthesize_lines(spec, synthesizer=synth, prober=prober, lookup=lambda _: None)["failed"] == []
    measured = {}
    for group in (2, 64):                                     # 2 = 两级预混；64 = 改前的一条命令
        monkeypatch.setattr(segmented, "MIX_GROUP", group)
        result = PA.mix(spec, ffmpeg=FFMPEG, ffprobe=FFPROBE, prober=prober)
        info = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", result["mix"]],
                                         capture_output=True, text=True, check=True).stdout)
        measured[group] = (_onsets(Path(result["mix"])), float(info["format"]["duration"]), result["duration"])
    (onsets, duration, total), (direct, direct_duration, _) = measured[2], measured[64]
    onsets, direct = [t for t in onsets if t < total - 0.05], [t for t in direct if t < total - 0.05]   # 去掉片尾 EOF 那一次
    starts = [i["start"] for i in PA.build_timeline(spec, PA.load_manifest(spec))["items"] if i["kind"] == "line"]
    assert len(onsets) == len(direct) == len(starts) - 1 + (starts[0] > 0.12)
    assert all(abs(a - b) < 0.005 for a, b in zip(onsets, direct))
    assert abs(duration - direct_duration) < 0.03 and abs(duration - total) < 0.1


def _overall_peak_db(args: list[str]) -> float:
    err = subprocess.run([FFMPEG, "-hide_banner", *args, "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stderr
    overall = err[err.rfind("Overall"):]
    return float(re.search(r"Peak level dB: (-?[\d.]+|-inf)", overall)[1].replace("-inf", "-999"))


@needs_ffmpeg
def test_real_premix_keeps_float_headroom_when_lines_overlap(tmp_path):
    """三句叠在一起、加起来远超满幅：分组预混的中间文件是浮点（WavPack），与一条命令直接混逐采样一样、不削顶。
    以前的 FLAC 24 位是整数，超过 0 dBFS 的部分被削平（两路叠到 1.43 时误差 0.26）。"""

    loud = [_tone(tmp_path / f"loud{k}.wav", 1.0, 300 + 110 * k, volume=8.0, codec="pcm_f32le") for k in range(3)]
    items = [(loud[0], 0.0), (loud[1], 0.2), (loud[2], 0.45)]

    def run(cmd, *, purpose, timeout_seconds):
        subprocess.run(cmd, check=True)

    premixed = segmented.premix_audio(items, tmp_path, ffmpeg=FFMPEG, sample_rate=48000, run=run, group=2)
    assert premixed is not None and [p.path.suffix for p in premixed] == [".wv", ".wav"]

    def mix(placed: list[tuple[Path, str]], dest: Path) -> Path:
        cmd = [FFMPEG, "-y", "-loglevel", "error"]
        filters = []
        for k, (path, delay) in enumerate(placed):
            cmd += ["-i", str(path)]
            filters.append(f"[{k}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,{delay}[p{k}]")
        filters.append("".join(f"[p{k}]" for k in range(len(placed))) + f"amix=inputs={len(placed)}:duration=longest:normalize=0[out]")
        subprocess.run([*cmd, "-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", "pcm_f32le", str(dest)], check=True)
        return dest

    direct = mix([(path, f"adelay={int(start * 1000)}|{int(start * 1000)}") for path, start in items], tmp_path / "direct.wav")
    grouped = mix([(clip.path, segmented.adelay_samples(clip.start_sample)) for clip in premixed], tmp_path / "grouped.wav")
    assert _overall_peak_db(["-i", str(direct), "-af", "astats=metadata=0"]) > 6.0          # 真的叠过了满幅
    assert _overall_peak_db(["-i", str(grouped), "-af", "astats=metadata=0"]) > 6.0
    difference = _overall_peak_db(["-i", str(direct), "-i", str(grouped), "-filter_complex",
                                   "[1:a]volume=-1[n];[0:a][n]amix=inputs=2:normalize=0:duration=longest,astats=metadata=0"])
    assert difference < -100


# ---------------------------------------------------------------------------- 成品被播放器开着：等一会儿再放弃，上一版原样


@windows_only
def test_replace_final_waits_for_the_reader_then_gives_up_with_a_clear_reason(tmp_path, monkeypatch):
    dest = tmp_path / "PC-R.mp3"
    dest.write_bytes(b"PREVIOUS")
    temporary = tmp_path / ".PC-R.tmp.mp3"
    temporary.write_bytes(b"NEW")
    # 播放器读一会儿就放开（缓冲够了 / 切走了）：等得到，换上去
    holder = open(dest, "rb")
    threading.Timer(0.4, holder.close).start()
    SA.replace_final(temporary, dest)
    assert dest.read_bytes() == b"NEW" and not temporary.exists()
    # 一直开着：到时限放弃，抛调用方给的错误类型，上一版原样
    monkeypatch.setattr(SA, "REPLACE_RETRY_SECONDS", 0.3)
    temporary.write_bytes(b"NEWER")
    with open(dest, "rb"), pytest.raises(PA.AudioError) as exc:
        SA.replace_final(temporary, dest, error=PA.AudioError)
    assert str(exc.value) == SA.FINAL_BUSY_MESSAGE and dest.read_bytes() == b"NEW"


@windows_only
def test_podcast_resynthesis_while_the_player_holds_the_previous_mix(tmp_path, monkeypatch):
    """页面上的播放器在流式放上一版（服务端开着文件边读边发）时点「合成整期」：以前 ffmpeg -y 原地覆盖；改成原子替换后
    Windows 不让换，PermissionError 不是 AudioError，整个配音任务失败、面板上是一句带两个绝对路径的英文。现在等几秒再放弃，
    原因进 mix_error（任务不算失败），上一版原样、不留临时文件；播放器放开之后再点一次就换上了。"""

    monkeypatch.setattr(SA, "REPLACE_RETRY_SECONDS", 0.3)
    spec = _podcast(tmp_path, 12)
    _fake_lines(spec)
    out = PS.mix_dir(spec)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "PC-R.mp3"
    dest.write_bytes(b"PREVIOUS")

    def runner(command):
        Path(command[-1]).write_bytes(b"ID3new")

    kwargs = {"synthesizer": _fake_synth, "prober": lambda p: 30.0, "lookup": lambda _: None, "ffmpeg": "ffmpeg", "runner": runner}
    with open(dest, "rb"):
        result = PA.run_synthesize(spec, **kwargs)
    assert result["mixed"] is False and result["mix_error"] == SA.FINAL_BUSY_MESSAGE
    assert dest.read_bytes() == b"PREVIOUS" and sorted(p.name for p in out.iterdir()) == ["PC-R.mp3"]
    result = PA.run_synthesize(spec, **kwargs)
    assert result["mixed"] is True and result["mix_error"] == "" and dest.read_bytes() == b"ID3new"


# ---------------------------------------------------------------------------- ffmpeg 失败：给人一行原因，完整报错进日志


def test_ffmpeg_reason_picks_the_cause_not_the_trailing_boilerplate():
    stderr = ("[Parsed_xfade_175 @ 000001ece68e0380] First input link main timebase (1/1000000) do not match the corresponding "
              "second input link xfade timebase (1/24)\n"
              "[Parsed_xfade_175 @ 000001ece68e0380] Failed to configure output pad on Parsed_xfade_175\n"
              "Error reinitializing filters!\nFailed to inject frame into filter network: Invalid argument\n"
              "Error while processing the decoded data for stream #0:0\n")
    assert ffmpeg_reason(stderr) == ("First input link main timebase (1/1000000) do not match the corresponding second input link "
                                     "xfade timebase (1/24)")
    assert ffmpeg_reason("[libx264 @ 0x1] Error while filtering: Cannot allocate memory\nConversion failed!\n").startswith(
        "内存不够：Error while filtering: Cannot allocate memory")
    assert ffmpeg_reason("av_interleaved_write_frame(): No space left on device\n").startswith("磁盘空间不够：")
    # 坏的输入文件：原因那一行不带文件名，文件名在下一行——接到原因前面，路径只留文件名（端到端实测的原话）
    assert ffmpeg_reason("[mp3 @ 0000024522d3a280] Failed to read frame size: Could not seek to 1873.\n"
                         "D:\\data\\output\\picturebook\\PB-E2E-1\\07_audio\\P01_zh.mp3: Invalid argument\n") == (
        "P01_zh.mp3: Failed to read frame size: Could not seek to 1873.")
    assert ffmpeg_reason("C:/Users/x/My Book/07_audio/P02_en.mp3: No such file or directory\n") == "P02_en.mp3: No such file or directory"
    assert ffmpeg_reason("") == "ffmpeg 没有给出原因"
    assert ffmpeg_reason("Error reinitializing filters!\n") == "Error reinitializing filters!"


def test_ffmpeg_reason_never_carries_the_filter_graph():
    """原话里夹着滤镜图的两类（输入里缺流、滤镜图拼写有误）换成一句话；没带 -loglevel error 的命令，横幅与输入描述不当原因。"""

    graph = "[0:a]aformat=sample_fmts=fltp:sample_rates=48000," + "adelay=1000S|1000S[p1];" * 200 + "amix=inputs=64[out]"
    for spec, what in ((":a", "读不出音频（文件坏了，或者不是音频文件）"), ("12:v", "读不出画面（文件坏了，或者不是视频 / 图片）"),
                       ("m:language:eng", "读不出要用的音视频（文件坏了，或者格式不对）")):
        reason = ffmpeg_reason(f"Stream specifier '{spec}' in filtergraph description {graph} matches no streams.\n")
        assert reason == f"有一个输入文件{what}" and "adelay" not in reason
    for line in ("[AVFilterGraph @ 0000019a] Trailing garbage after a filter: /output/picturebook/PB-1/07_edit/subs/P01_zh_1.txt':"
                 "fontsize=96:borderw=5:bordercolor=0x111111@0.9:shadowx=0:shadowy=2:shadowcolor=black@0.6[v]",
                 "[AVFilterGraph @ 0000019a] Error parsing filterchain '[0:v]fps=24,scale=w=1080' around: :h=1920"):
        assert ffmpeg_reason(line + "\nError initializing complex filters.\n") == SA.FFMPEG_GRAPH_MESSAGE
    banner = ("ffmpeg version 6.0-full_build-www.gyan.dev Copyright (c) 2000-2023 the FFmpeg developers\n"
              "  built with gcc 12.2.0 (Rev10, Built by MSYS2 project)\n  configuration: --enable-gpl --enable-version3\n"
              "  libavutil      58.  2.100 / 58.  2.100\nInput #0, mov,mp4,m4a,3gp,3g2,mj2, from 'S1.mp4':\n  Metadata:\n"
              "    major_brand     : isom\n    encoder         : Lavf60.3.100\n  Duration: 00:00:05.00, start: 0.000000, bitrate: 90 kb/s\n"
              "  Stream #0:0[0x1](und): Video: h264 (High), yuv420p, 1080x1920, 24 fps\n"
              "[h264 @ 000001d] error while decoding MB 12 40, bytestream -7\n")
    assert ffmpeg_reason(banner) == "error while decoding MB 12 40, bytestream -7"


def test_launch_failures_are_named_correctly():
    class TooLong(FileNotFoundError):
        winerror = 206

    assert _launch_reason(TooLong("[WinError 206] 文件名或扩展名太长。"), "ffmpeg") == "命令行超过了 Windows 的长度上限"
    assert _launch_reason(FileNotFoundError("nope"), "C:/x/ffmpeg.exe") == "找不到 ffmpeg 程序：C:/x/ffmpeg.exe"
    assert _launch_reason(PermissionError("denied"), "ffmpeg") == "启动失败：denied"


@needs_ffmpeg
def test_real_ffmpeg_failure_is_one_line_for_the_user_and_everything_in_the_log(tmp_path, caplog):
    script = tmp_path / "graph.txt"
    script.write_text("[0:v]nosuchfilter[v]", encoding="utf-8")
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.2",
           "-filter_complex_script", str(script), "-map", "[v]", str(tmp_path / "out.mp4")]
    with caplog.at_level(logging.ERROR, logger="storystudio.ffmpeg"), pytest.raises(AssemblyError) as exc:
        _run(cmd, purpose="render picture book page P02", timeout_seconds=60)
    message = str(exc.value)
    assert "\n" not in message and message.startswith("ffmpeg 出错（render picture book page P02）：")
    assert "nosuchfilter" in message and message.endswith("——完整报错写进了日志 storystudio.log")
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "[0:v]nosuchfilter[v]" in logged and "stderr:" in logged and "color=c=black" in logged
    # 超时：一行说清楚，不再是「could not run …: Command '[…整条命令…]' timed out」
    with pytest.raises(AssemblyError) as slow:
        _run([FFMPEG, "-loglevel", "error", "-re", "-f", "lavfi", "-i", "anullsrc", "-f", "null", "-"], purpose="compose post layers",
             timeout_seconds=0.5)
    assert str(slow.value) == "ffmpeg 出错（compose post layers）：超过 0.5 秒还没跑完，已中止——完整报错写进了日志 storystudio.log"
    with pytest.raises(AssemblyError, match="找不到 ffmpeg 程序：definitely-not-ffmpeg-8862"):
        _run(["definitely-not-ffmpeg-8862", "-version"], purpose="render shot S1", timeout_seconds=5)


@needs_ffmpeg
def test_real_missing_stream_names_the_bad_input(tmp_path):
    """「Stream specifier ':a' in filtergraph description <整张滤镜图> matches no streams」：逐个 ffprobe 输入，点名那个坏文件；
    lavfi 源不去探。输入都读得出（只是滤镜写错了流）时说「有一个输入文件」。"""

    good = _tone(tmp_path / "P01_zh_1.mp3", 0.3, 440)
    bad = tmp_path / "P02_zh_1.mp3"
    _solid_png(bad, (1, 2, 3))                                  # 一张 PNG 冒充 mp3：ffmpeg 读得开，但里面没有音频
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-t", "1", "-i", "anullsrc=r=48000:cl=stereo", "-i", str(good), "-i", str(bad),
           "-filter_complex", "[0:a][1:a][2:a]amix=inputs=3[out]", "-map", "[out]", str(tmp_path / "o.wav")]
    with pytest.raises(AssemblyError) as exc:
        _run(cmd, purpose="mix picture book audio", timeout_seconds=60)
    assert str(exc.value) == ("ffmpeg 出错（mix picture book audio）：P02_zh_1.mp3 读不出音频（文件坏了，或者不是音频文件）"
                              "——完整报错写进了日志 storystudio.log")
    with pytest.raises(PA.AudioError) as mixed:
        PA._run(cmd)
    assert str(mixed.value) == "混音失败：P02_zh_1.mp3 读不出音频（文件坏了，或者不是音频文件）——完整报错写进了日志 storystudio.log"
    # 读画面却只有一个 lavfi 静音源：lavfi 不去探，查不出是哪个文件
    wrong = [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-t", "1", "-i", "anullsrc", "-filter_complex", "[0:v]null[out]", "-map", "[out]",
             str(tmp_path / "o.mp4")]
    with pytest.raises(AssemblyError, match="：有一个输入文件读不出画面（文件坏了，或者不是视频 / 图片）——"):
        _run(wrong, purpose="render shot S1", timeout_seconds=60)
    # 音频文件拿去当画面用：那个文件里确实没有画面，点名它
    with pytest.raises(AssemblyError, match="：P01_zh_1.mp3 读不出画面"):
        _run([FFMPEG, "-y", "-loglevel", "error", "-i", str(good), "-filter_complex", "[0:v]null[out]", "-map", "[out]", str(tmp_path / "o.mp4")],
             purpose="render shot S1", timeout_seconds=60)


@needs_ffmpeg
def test_real_podcast_mix_failure_is_one_line(tmp_path, caplog):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio at all" * 10)
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(bad), "-filter_complex", "[0:a]anull[out]", "-map", "[out]",
           str(tmp_path / "o.mp3")]
    with caplog.at_level(logging.ERROR, logger="storystudio.ffmpeg"), pytest.raises(PA.AudioError) as exc:
        PA._run(cmd)
    message = str(exc.value)
    assert "\n" not in message and message.startswith("混音失败：bad.mp3") and message.endswith("——完整报错写进了日志 storystudio.log")
    assert "bad.mp3" in "\n".join(r.getMessage() for r in caplog.records)


def test_long_podcast_timeout_scales_with_the_episode():
    assert PA._mix_timeout({"total": 120.0}) == PA.MIX_TIMEOUT_SECONDS
    assert PA._mix_timeout({"total": 5400.0}) == 2700.0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command-line limit")
def test_measured_before_state_long_command_line_is_what_used_to_break(tmp_path):
    """钉住改前的失败方式：同一期 150 句、长数据根，改前那种「一条命令接全部句子 + 内联滤镜图」的命令行超过 Windows 上限。"""

    deep = tmp_path / ("很长的用户目录名" * 6) / "StoryStudio"
    spec = _podcast(deep, 150)
    _fake_lines(spec)
    timeline = PA.build_timeline(spec, PA.load_manifest(spec), music_durations={})
    single = PA.mix_command(spec, timeline, ffmpeg="ffmpeg", music={"bed": deep / "content" / "assets" / "PC-R" / "bed.mp3"})
    assert len(subprocess.list2cmdline(single)) > WINDOWS_CMDLINE_LIMIT
