"""后期层：旁白逐句声线 / 旁白字幕 / 只在旁白时段压原声，绝对静音区间（后期层的这两块能力）。

合成部分不跑真实 ffmpeg 也能测：把 ``_run`` 和 ``probe_media`` 换成替身，只读 compose_post 写出的
``manifests/post_filtergraph.txt``（最后一遍「文字 + 混音」的滤镜图）。另有一条真 ffmpeg 的端到端，确认静音区间真的是数字零、
旁白压低的表达式 ffmpeg 认。

2026-09-22 发第一个 tag 前把合成改成了分段流式（每项先归一化、再分组 xfade、最后一遍叠字混音），滤镜图的结构必然变：
以前钉「滤镜图逐字不变」的几条改成钉「行为不变」——每一条画面文字 / 字幕 / 旁白 / 配音 / 配乐的绝对时间、压低段、
混音链的形状、每一项的归一化视频链、接缝的 xfade 偏移都和改之前一样，只是不再挤在同一条命令里。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

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


# ---------------------------------------------------------------------------- 替身：不跑 ffmpeg 也能拿到滤镜图


def _fake_media(monkeypatch, durations: dict[str, float], final: float, vo_seconds: float = 1.2) -> list[list[str]]:
    """镜头按 durations 报时长，成片（临时文件 .<id>_post.<hex>.tmp.mp4）报 final，旁白 / 配音 mp3 一律报 vo_seconds；
    ffmpeg 不真跑（只记命令、把输出文件建成空文件——成片是原子替换过去的，得有东西可替换），插卡不真画。"""

    commands: list[list[str]] = []

    def probe(path, ffprobe="ffprobe"):
        p = Path(path)
        if p.parent.name == "02_video":
            return SimpleNamespace(duration_seconds=durations[p.stem])
        if "_post." in p.name and p.suffix == ".mp4":
            return SimpleNamespace(duration_seconds=final)
        return SimpleNamespace(duration_seconds=vo_seconds)

    def run(cmd, **kw):
        commands.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"")

    monkeypatch.setattr(pl, "probe_media", probe)
    monkeypatch.setattr(pl, "_run", run)
    monkeypatch.setattr(pl, "render_ui_card", lambda insert, dest, **kw: Path(dest))
    return commands


def _drawtexts(graph: str) -> list[dict[str, str]]:
    """滤镜图里每条 drawtext 的键值（textfile / text、enable 的起止、字号……）。"""

    out: list[dict[str, str]] = []
    for match in re.finditer(r"drawtext=((?:[^,;']|'[^']*')+)", graph):
        kv: dict[str, str] = {}
        for part in re.findall(r"(\w+)=('[^']*'|[^:]+)", match.group(1)):
            kv[part[0]] = part[1].strip("'")
        out.append(kv)
    return out


def _subtitle_window(graph: str, text_file: str) -> tuple[str, str] | None:
    """某个字幕文本文件（如 S2.zh.0.txt）那条 drawtext 的显示区间。"""

    for kv in _drawtexts(graph):
        if kv.get("textfile", "").endswith(text_file):
            m = re.fullmatch(r"between\(t,([\d.]+),([\d.]+)\)", kv["enable"])
            return (m.group(1), m.group(2)) if m else None
    return None


def _write_audio(text: str, dest: Path, *_profile) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"mp3")


def _rich_spec(tmp_path: Path) -> dict:
    """配音 + 配乐 + 插卡 + 本子字 + 台词字幕（含要转义的字符）+ 交叉溶解都在，但没有旁白、没有静音区间。"""

    root = tmp_path / "output" / "story_video" / "SNAP"
    (root / "02_video").mkdir(parents=True)
    for sid in ("S1", "S2", "S3"):
        (root / "02_video" / f"{sid}.mp4").write_bytes(b"x")
    music = tmp_path / "content" / "assets" / "SNAP" / "music"
    music.mkdir(parents=True)
    (music / "bgm.mp3").write_bytes(b"x")
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    for name in ("nb.ttf", "ui.ttf", "sub.ttf"):
        (fonts / name).write_bytes(b"x")
    return {
        "id": "SNAP", "output_root": str(root),
        "delivery": {"width": 360, "height": 640, "fps": 12, "crossfade_seconds": 0.25},
        "subtitle": {"chinese_font_size": 20, "safe_bottom_percent": 7, "band_alpha": 0.2, "font_path": str(fonts / "sub.ttf")},
        "cast": {"lin": {"name": "林", "voice_tts": {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural"}}},
        "post_graphics": {
            "fonts": {"notebook": str(fonts / "nb.ttf"), "ui": str(fonts / "ui.ttf"), "subtitle": str(fonts / "sub.ttf")},
            "phone_ui_inserts": [{"id": "UI_1", "after": "S1", "dur": 2, "lines": ["x"]}],
            "notebook_text": [{"shot": "S2", "text": "你好", "y": 0.5, "size": 40}],
            "dubbing": {"mode": "shots", "shots": ["S3"], "duck": 0.1},
            "music": [{"file": "content/assets/SNAP/music/bgm.mp3", "start": 1.0, "end": 8.0, "gain": 0.5, "duck": 0.3,
                       "fade_in": 0.5, "fade_out": 1.0, "label": "bgm"}],
        },
        "shots": [
            {"id": "S1", "dur": 3, "lines": []},
            {"id": "S2", "dur": 4, "lines": [{"who": "lin", "zh": "你怎么才来？"}, {"who": "lin", "zh": "说：个数'啊%"}]},
            {"id": "S3", "dur": 2.5, "lines": [{"who": "lin", "zh": "走了。"}]},
        ],
    }


def _minimal_spec(tmp_path: Path) -> dict:
    """两镜、无交叉溶解、没有任何后期块：走 concat + 单独 loudnorm 那条路。"""

    root = tmp_path / "output" / "story_video" / "MIN"
    (root / "02_video").mkdir(parents=True)
    for sid in ("S1", "S2"):
        (root / "02_video" / f"{sid}.mp4").write_bytes(b"x")
    fonts = tmp_path / "fonts"
    fonts.mkdir(exist_ok=True)
    (fonts / "sub.ttf").write_bytes(b"x")
    return {
        "id": "MIN", "output_root": str(root),
        "delivery": {"width": 360, "height": 640, "fps": 12, "crossfade_seconds": 0},
        "subtitle": {"font_path": str(fonts / "sub.ttf")},
        "post_graphics": {"fonts": {"notebook": str(fonts / "sub.ttf"), "ui": str(fonts / "sub.ttf"), "subtitle": str(fonts / "sub.ttf")}},
        "shots": [{"id": "S1", "dur": 3, "lines": []}, {"id": "S2", "dur": 3, "lines": []}],
    }


RICH_DURATIONS = {"S1": 3.0, "S2": 4.0, "S3": 2.5}
RICH_FINAL = 3.0 + 2.0 + 4.0 + 2.5 - 3 * 0.25          # 时间线：S1 0 / UI_1 2.75 / S2 4.5 / S3 8.25
MIN_DURATIONS = {"S1": 3.0, "S2": 3.0}


def _compose_graph(spec: dict, tmp_path: Path) -> tuple[dict, str]:
    manifest = pl.compose_post(spec, tts=_write_audio, dub_tts=_write_audio, project_root=tmp_path)
    graph = (Path(spec["output_root"]) / "manifests" / "post_filtergraph.txt").read_text(encoding="utf-8")
    # 字体路径跟着 tmp 目录走：换成占位符再比
    return manifest, graph.replace(pl._filter_path(tmp_path), "<TMP>")


# ---------------------------------------------------------------------------- 行为不变：没有旁白、没有静音时的时间与混音链
# 改成分段流式之前，这里钉的是旧版 post_layers.py 对同一份 spec 写出的整张滤镜图（逐字不变）。
# 分段之后每一项各自归一化、接缝在拼接命令里做，最后一遍只叠字混音——图的结构必然变，钉的改成下面这些事实：
# 归一化视频链、接缝偏移、每条画面文字 / 字幕 / 配音 / 配乐的绝对时间、压低段、amix + loudnorm 的形状，一个数都不能变。

# tpad：音轨比画面长的片要把画面定格补到本项时长（不补的话接缝会落在画面 EOF 之后、整组画面丢掉）；
# 画面本来够长时补出来的帧全被后面的 trim 截掉，产物与补之前逐帧相同
_SCALE = ("scale=360:640:force_original_aspect_ratio=decrease,pad=360:640:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=12,"
          "format=yuv420p,tpad=stop_mode=clone:stop_duration={d},trim=duration={d},setpts=PTS-STARTPTS")
_MUSIC_RICH = ("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,atrim=0:7.000,asetpts=PTS-STARTPTS,"
               "afade=t=in:st=0:d=0.500,afade=t=out:st=6.000:d=1.000,volume=0.500,adelay=1000|1000,"
               "volume='if(between(t,4.500,8.500)+between(t,8.250,10.750)+between(t,8.550,9.750),0.300,1)':eval=frame")


def _video_chains(commands: list[list[str]]) -> list[str]:
    """每条归一化命令的视频链（去掉输入 / 输出标签）。"""

    out = []
    for cmd in commands:
        if "-filter_complex" not in cmd or "xfade=" in cmd[cmd.index("-filter_complex") + 1]:
            continue
        graph = cmd[cmd.index("-filter_complex") + 1]
        video = graph.split(";")[0]
        out.append(video[video.index("]") + 1:video.rindex("[")])
    return out


def _join_graphs(commands: list[list[str]]) -> list[str]:
    return [cmd[cmd.index("-filter_complex") + 1] for cmd in commands
            if "-filter_complex" in cmd and "xfade=" in cmd[cmd.index("-filter_complex") + 1]]


def _assert_rich_behaviour(commands: list[list[str]], graph: str, tmp_path: Path) -> None:
    # 四项各自归一化，视频链与以前每一路的写法逐字一样（插卡那一路一样：没有推镜的 plain 卡不带 perspective）
    assert _video_chains(commands) == [_SCALE.format(d=d) for d in ("3.000", "2.000", "4.000", "2.500")]
    # 一条 xfade 命令接四段：接缝的偏移就是以前链上的 offset；链尾截到时间线长度（中间片不多出恒定帧率补的那一帧）。
    # 声音从同一批中间片的第二次打开（输入 4–7）里读——音画同一个输入时 ffmpeg 6.0 的 xfade 会硬切（assembly.join_inputs）
    assert _join_graphs(commands) == [
        "[0:v][1:v]xfade=transition=fade:duration=0.250:offset=2.750[vx1];[4:a][5:a]acrossfade=d=0.250:c1=tri:c2=tri[ax1];"
        "[vx1][2:v]xfade=transition=fade:duration=0.250:offset=4.500[vx2];[ax1][6:a]acrossfade=d=0.250:c1=tri:c2=tri[ax2];"
        "[vx2][3:v]xfade=transition=fade:duration=0.250:offset=8.250,trim=duration=10.750[vout];"
        "[ax2][7:a]acrossfade=d=0.250:c1=tri:c2=tri[aout]"
    ]
    lines = graph.split(";\n")
    # 最后一遍：拼好的单路视频进来叠画面文字与台词字幕，时间和以前一样
    # 画面文字走 textfile + expansion=none（F3）
    assert lines[0].startswith("[0:v]drawtext=fontfile='<TMP>/fonts/nb.ttf':"
                               "textfile='<TMP>/output/story_video/SNAP/manifests/subtitles/notebook.0.0.txt':expansion=none:"
                               "fontsize=40:fontcolor=0x1a1a1a@0.92:"
                               "x=(w-text_w)/2:y=320:enable='between(t,4.900,8.450)',") and lines[0].endswith("[vout]")
    assert _subtitle_window(graph, "S2.zh.0.txt") == ("4.800", "6.500")
    assert _subtitle_window(graph, "S2.zh.1.txt") == ("6.550", "8.250")
    assert _subtitle_window(graph, "S3.zh.0.txt") == ("8.550", "10.500")
    subtitles_dir = tmp_path / "output" / "story_video" / "SNAP" / "manifests" / "subtitles"
    assert (subtitles_dir / "S2.zh.1.txt").read_text(encoding="utf-8") == "说：个数'啊%"
    # 混音：配音按绝对时间 adelay、配乐的裁剪 / 淡入淡出 / 压低段、配音镜压原声、amix + loudnorm，与以前逐项相同
    assert [ln for ln in lines if "adelay=8550|8550,volume=1.0[dub" in ln]
    assert [ln for ln in lines if ln.endswith("]") and _MUSIC_RICH in ln and "[mus" in ln]
    assert "[0:a]volume='if(between(t,8.250,10.750),0.100,1)':eval=frame[axd]" in lines
    assert re.fullmatch(r"\[axd\]\[dub\d+\]\[mus\d+\]amix=inputs=3:duration=first:normalize=0:dropout_transition=0,"
                        r"loudnorm=I=-16:TP=-1.5:LRA=9\[aout\]", lines[-1])


def _no_vo(spec: dict) -> dict:
    return spec


def _vo_empty_with_subtitles(spec: dict) -> dict:
    spec["voice_over"] = {"edge": {"voice": "zh-CN-YunxiNeural"}, "subtitles": True, "lines": []}
    return spec


def _vo_blank_lines(spec: dict) -> dict:
    # 空文本的旁白句会被跳过：等于没有旁白
    spec["voice_over"] = {"subtitles": False, "lines": [{"shot": "S1", "at": 0.5, "zh": "  "}]}
    return spec


def _silence_empty(spec: dict) -> dict:
    spec["post_graphics"]["silence"] = []
    return spec


@pytest.mark.parametrize("variant", [_no_vo, _vo_empty_with_subtitles, _vo_blank_lines, _silence_empty])
def test_graph_without_narration_or_silence_keeps_every_time_and_the_mix(tmp_path, monkeypatch, variant):
    commands = _fake_media(monkeypatch, RICH_DURATIONS, RICH_FINAL)
    manifest, graph = _compose_graph(variant(_rich_spec(tmp_path)), tmp_path)
    _assert_rich_behaviour(commands, graph, tmp_path)
    assert manifest["silence"] == [] and manifest["vo"] == [] and manifest["vo_skipped"] == []


def test_minimal_graph_concatenates_without_reencoding_the_join(tmp_path, monkeypatch):
    """两镜、无交叉溶解、没有后期块：两段各自归一化后走 concat 演示器（流复制，内存与镜数无关），最后一遍只做响度归一。"""

    commands = _fake_media(monkeypatch, MIN_DURATIONS, 6.0)
    _manifest, graph = _compose_graph(_minimal_spec(tmp_path), tmp_path)
    assert _video_chains(commands) == [_SCALE.format(d="3.000")] * 2
    concat = [cmd for cmd in commands if "concat" in cmd]
    assert len(concat) == 1 and concat[0][concat[0].index("-f") + 1] == "concat" and "copy" in concat[0]
    assert _join_graphs(commands) == []
    assert graph == "[0:v]null[vout];\n[0:a]loudnorm=I=-16:TP=-1.5:LRA=9[aout]"


# ---------------------------------------------------------------------------- 旁白：只在旁白时段压原声，带斜坡


def _eval_gain(expr: str, t: float) -> float:
    """把 ffmpeg 的 volume 表达式按 Python 求值（只用到 clip / min / 四则运算 / t）。"""

    return eval(expr, {"__builtins__": {}}, {"clip": lambda x, lo, hi: max(lo, min(hi, x)), "min": min, "t": t})


def test_vo_duck_volume_shape():
    expr = pl.vo_duck_volume([(1.0, 2.0), (5.0, 5.5)])
    gain = lambda t: _eval_gain(expr, t)  # noqa: E731
    # 时段外一点不动
    for t in (0.0, 0.84, 2.26, 3.0, 4.8, 5.8, 9.0):
        assert gain(t) == pytest.approx(1.0), t
    # 旁白发声的整段保持 0.55
    for t in (1.0, 1.5, 2.0, 5.0, 5.5):
        assert gain(t) == pytest.approx(0.55), t
    # 句首前 0.15s 线性压下去、句尾后 0.25s 线性放回来（不是硬切）
    assert gain(0.925) == pytest.approx(1 - 0.45 * 0.5)
    assert gain(2.125) == pytest.approx(1 - 0.45 * 0.5)
    assert gain(0.9) > gain(0.95) > gain(1.0)
    # 两句挨得很近（上一句的放回斜坡碰到下一句的压下斜坡）：并成一段，中间不冒一下
    close = pl.vo_duck_volume([(2.3, 3.0), (1.0, 2.0)])
    assert close.count("clip(min(") == 1
    for t in (2.0, 2.1, 2.15, 2.2, 2.3):
        assert _eval_gain(close, t) == pytest.approx(0.55), t
    assert _eval_gain(close, 3.3) == pytest.approx(1.0)
    # 离得够远：各压各的，中间回到原音量
    apart = pl.vo_duck_volume([(1.0, 2.0), (2.5, 3.0)])
    assert apart.count("clip(min(") == 2 and _eval_gain(apart, 2.3) == pytest.approx(1.0)
    # 旁白从 0.1s 开始：斜坡起点是负数，也不写出 t--0.050
    early = pl.vo_duck_volume([(0.1, 1.0)])
    assert "t+0.050" in early and "--" not in early
    assert _eval_gain(early, 0.0) == pytest.approx(1 - 0.45 * (0.05 / 0.15))


def _with_narration(spec: dict, **vo) -> dict:
    spec["voice_over"] = {"edge": {"voice": "zh-CN-YunxiNeural"},
                          "lines": [{"shot": "S1", "at": 0.5, "zh": "凌晨一点。"}, {"shot": "UI_1", "at": 0.2, "zh": "还有一条消息。"}], **vo}
    return spec


def test_narration_ducks_native_audio_only_during_lines(tmp_path, monkeypatch):
    _fake_media(monkeypatch, RICH_DURATIONS, RICH_FINAL, vo_seconds=1.2)
    manifest, graph = _compose_graph(_with_narration(_rich_spec(tmp_path)), tmp_path)
    assert "volume=0.55" not in graph                       # 不再全片乘 0.55
    duck = next(f for f in graph.split(";\n") if f.startswith("[axd]volume="))
    expr = re.fullmatch(r"\[axd\]volume='(.+)':eval=frame\[axq\]", duck).group(1)
    # 两句旁白：S1 0.5→1.7，UI_1（起点 2.75）0.2 → 2.95→4.15
    assert expr == pl.vo_duck_volume([(0.5, 1.7), (2.95, 4.15)])
    assert _eval_gain(expr, 0.2) == pytest.approx(1.0) and _eval_gain(expr, 1.0) == pytest.approx(0.55)
    assert _eval_gain(expr, 2.2) == pytest.approx(1.0) and _eval_gain(expr, 9.0) == pytest.approx(1.0)
    assert graph.split(";\n")[-1].startswith("[axq][vo")
    # 没开字幕：旁白不烧字（旁白字幕的文本文件叫 vo<句号>.zh.0.txt）
    assert "vo1.zh.0.txt" not in graph and "vo2.zh.0.txt" not in graph
    assert [round(v["duration"], 2) for v in manifest["vo"]] == [1.2, 1.2]


def _style(kv: dict[str, str]) -> dict[str, str]:
    """drawtext 的样式键（去掉文字来源与时段）。"""

    return {k: v for k, v in kv.items() if k not in ("textfile", "text", "enable")}


def test_narration_subtitles_use_real_tts_duration_and_dialogue_style(tmp_path, monkeypatch):
    _fake_media(monkeypatch, RICH_DURATIONS, RICH_FINAL, vo_seconds=1.37)
    _manifest, graph = _compose_graph(_with_narration(_rich_spec(tmp_path), subtitles=True), tmp_path)
    # 样式与台词字幕逐项相同，只是文字和时段不同：从绝对起点开始，显示真实 TTS 时长
    assert _subtitle_window(graph, "vo1.zh.0.txt") == ("0.500", "1.870")
    assert _subtitle_window(graph, "vo2.zh.0.txt") == ("2.950", "4.320")
    subtitles_dir = tmp_path / "output" / "story_video" / "SNAP" / "manifests" / "subtitles"
    assert (subtitles_dir / "vo1.zh.0.txt").read_text(encoding="utf-8") == "凌晨一点。"
    by_file = {kv["textfile"].rsplit("/", 1)[-1]: kv for kv in _drawtexts(graph) if "textfile" in kv}
    assert _style(by_file["vo1.zh.0.txt"]) == _style(by_file["S2.zh.0.txt"])
    # 台词字幕照旧
    assert _subtitle_window(graph, "S2.zh.0.txt") == ("4.800", "6.500")


def test_dialogue_subtitles_yield_to_narration_subtitles_in_the_same_shot(tmp_path, monkeypatch):
    """旁白字幕开着时同镜的台词字幕让路（「人物先说、画外音再答」那种）：旁白在台词字幕亮着时开口 →
    台词字幕在旁白开口前 0.1s 收；台词字幕本该在旁白字幕亮着时出现 → 等旁白字幕收了再出；不足 0.3s 的不画。
    S2 起点 4.5、两句台词均分成 4.8–6.5 / 6.55–8.25；旁白 at 1.5 → 6.0–7.37。"""

    _fake_media(monkeypatch, RICH_DURATIONS, RICH_FINAL, vo_seconds=1.37)
    spec = _rich_spec(tmp_path)
    spec["voice_over"] = {"edge": {"voice": "zh-CN-YunxiNeural"}, "subtitles": True,
                          "lines": [{"shot": "S2", "at": 1.5, "zh": "好的，马上就到。"}]}
    manifest, graph = _compose_graph(spec, tmp_path)
    assert _subtitle_window(graph, "vo1.zh.0.txt") == ("6.000", "7.370")
    assert _subtitle_window(graph, "S2.zh.0.txt") == ("4.800", "5.900")           # 提前收
    assert _subtitle_window(graph, "S2.zh.1.txt") == ("7.470", "8.250")           # 推后出
    assert "6.550" not in graph
    assert manifest["subtitle_cuts"] == [
        {"shot": "S2", "line": 0, "zh": "你怎么才来？", "was": [4.8, 6.5], "now": [4.8, 5.9], "drawn": True},
        {"shot": "S2", "line": 1, "zh": "说：个数'啊%", "was": [6.55, 8.25], "now": [7.47, 8.25], "drawn": True},
    ]
    # 旁白字幕关着：台词字幕原样，不记让路
    spec["voice_over"]["subtitles"] = False
    manifest2, graph2 = _compose_graph(spec, tmp_path)
    assert _subtitle_window(graph2, "S2.zh.0.txt") == ("4.800", "6.500") and manifest2["subtitle_cuts"] == []


# ---------------------------------------------------------------------------- 旁白：逐句声线


def _vo_spec(engine: str = "edge", line_edge: dict | None = None) -> dict:
    lines = [{"shot": "S1", "at": 0, "zh": "今晚有雨。"}, {"shot": "S2", "at": 0.3, "zh": "前方右转。"}]
    if line_edge is not None:
        lines[1]["edge"] = line_edge
    return {"voice_over": {"engine": engine, "edge": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "-6%"}, "lines": lines}}


def test_line_edge_overrides_voice_and_enters_cache_key(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "probe_media", lambda path, ffprobe="ffprobe": SimpleNamespace(duration_seconds=1.0))
    calls = []

    def fake_tts(text, dest, edge=None):
        calls.append((text, edge))
        dest.write_bytes(b"mp3")

    spec = _vo_spec(line_edge={"voice": "zh-CN-YunyangNeural", "rate": "-4%"})
    rows = pl.render_vo_lines(spec, tmp_path / "vo", tts=fake_tts)
    block = {"voice": "zh-CN-XiaoxiaoNeural", "rate": "-6%", "pitch": "-8Hz"}      # 整块：DEFAULT_EDGE 上叠 voice_over.edge
    line = {"voice": "zh-CN-YunyangNeural", "rate": "-4%", "pitch": "-8Hz"}         # 逐句：只覆盖写了的，pitch 沿用整块
    assert calls == [("今晚有雨。", None), ("前方右转。", line)]
    assert "edge" not in rows[0] and rows[1]["edge"] == line
    # 没写逐句声线的句子缓存键跟以前一模一样：老项目已渲染的旁白 mp3 照样复用
    old_digest = hashlib.sha1(json.dumps(["今晚有雨。", block], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    assert Path(rows[0]["path"]).name == f"vo_01_S1_{old_digest}.mp3"
    # 写了逐句声线的句子：键里多一个路径标记（这类句子一定走 edge-tts）
    new_digest = hashlib.sha1(json.dumps(["前方右转。", line, "line-edge"], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    assert Path(rows[1]["path"]).name == f"vo_02_S2_{new_digest}.mp3"
    saved = json.loads((tmp_path / "vo" / "vo_manifest.json").read_text(encoding="utf-8"))
    assert saved["edge"] == block and saved["lines"][1]["edge"] == line
    # 再跑一遍全命中缓存；只换这一句的声线 → 只重合成这一句
    calls.clear()
    pl.render_vo_lines(spec, tmp_path / "vo", tts=fake_tts)
    assert calls == []
    spec["voice_over"]["lines"][1]["edge"]["voice"] = "zh-CN-YunjianNeural"
    rows2 = pl.render_vo_lines(spec, tmp_path / "vo", tts=fake_tts)
    assert [c[0] for c in calls] == ["前方右转。"] and rows2[1]["path"] != rows[1]["path"]
    assert rows2[0]["path"] == rows[0]["path"]
    # 同一句不写逐句声线 = 用整块声线，文件也不同
    rows3 = pl.render_vo_lines(_vo_spec(), tmp_path / "vo", tts=fake_tts)
    assert rows3[1]["path"] not in (rows[1]["path"], rows2[1]["path"]) and "edge" not in rows3[1]


def test_default_tts_sends_line_edge_to_edge_tts_even_when_settings_voice_is_configured(tmp_path, monkeypatch):
    """引擎没写（老规则）且「模型设置」配了别家语音：整块句子走别家，写了逐句 edge 的句子直接走 edge-tts。"""

    import genkit.adapters.speech as speech
    import genkit.router as router
    import genkit.settings as settings

    calls = []

    class FakeEdge:
        def __init__(self, config):
            self.config = config

        def synthesize(self, text, destination, voice=None, rate=None, pitch=None, **_kw):
            calls.append(("edge", text, voice, rate, pitch))
            destination.write_bytes(b"mp3")

    def provider_speech(text, destination, voice=None):
        calls.append(("settings", text, voice))
        destination.write_bytes(b"mp3")

    monkeypatch.setattr(speech, "EdgeSpeech", FakeEdge)
    monkeypatch.setattr(settings, "load_settings", lambda: SimpleNamespace(role=lambda name: SimpleNamespace(adapter="openai_speech", model="tts-1")))
    monkeypatch.setattr(settings, "validate_role", lambda loaded, name: [])
    monkeypatch.setattr(router, "build_provider", lambda loaded: SimpleNamespace(speech=lambda: SimpleNamespace(synthesize=provider_speech)))
    monkeypatch.setattr(pl, "probe_media", lambda path, ffprobe="ffprobe": SimpleNamespace(duration_seconds=1.0))

    pl.render_vo_lines(_vo_spec(engine="", line_edge={"voice": "zh-CN-YunyangNeural", "pitch": "+2Hz"}), tmp_path / "vo")
    assert calls == [("settings", "今晚有雨。", "zh-CN-XiaoxiaoNeural"),
                     ("edge", "前方右转。", "zh-CN-YunyangNeural", "-6%", "+2Hz")]


def test_line_edge_equal_to_block_does_not_reuse_settings_provider_audio(tmp_path, monkeypatch):
    """逐句声线合并后恰好等于整块声线：值一样，但合成路径不一样（整块走「模型设置」的别家语音，逐句走 edge-tts），
    缓存键得分开——否则先前别家合成的 mp3 会被直接复用，这句的逐句 edge 被悄悄忽略。"""

    import genkit.adapters.speech as speech
    import genkit.router as router
    import genkit.settings as settings

    calls = []

    class FakeEdge:
        def __init__(self, config):
            self.config = config

        def synthesize(self, text, destination, voice=None, rate=None, pitch=None, **_kw):
            calls.append(("edge", text))
            destination.write_bytes(b"mp3")

    def provider_speech(text, destination, voice=None):
        calls.append(("settings", text))
        destination.write_bytes(b"mp3")

    monkeypatch.setattr(speech, "EdgeSpeech", FakeEdge)
    monkeypatch.setattr(settings, "load_settings", lambda: SimpleNamespace(role=lambda name: SimpleNamespace(adapter="openai_speech", model="tts-1")))
    monkeypatch.setattr(settings, "validate_role", lambda loaded, name: [])
    monkeypatch.setattr(router, "build_provider", lambda loaded: SimpleNamespace(speech=lambda: SimpleNamespace(synthesize=provider_speech)))
    monkeypatch.setattr(pl, "probe_media", lambda path, ffprobe="ffprobe": SimpleNamespace(duration_seconds=1.0))

    spec = {"voice_over": {"edge": {"voice": "zh-CN-YunxiNeural"}, "lines": [{"shot": "S1", "at": 0, "zh": "前方右转。"}]}}
    first = pl.render_vo_lines(spec, tmp_path / "vo")
    assert calls == [("settings", "前方右转。")]
    # 逐句声线只写 voice、而且跟整块同一个音色：合并后 voice/rate/pitch 与整块一字不差
    spec["voice_over"]["lines"][0]["edge"] = {"voice": "zh-CN-YunxiNeural"}
    calls.clear()
    second = pl.render_vo_lines(spec, tmp_path / "vo")
    assert calls == [("edge", "前方右转。")]
    assert second[0]["path"] != first[0]["path"] and second[0]["edge"] == {**pl.DEFAULT_EDGE, "voice": "zh-CN-YunxiNeural"}
    # 去掉逐句声线：回到整块的旧文件（键还是旧公式），不重合成
    del spec["voice_over"]["lines"][0]["edge"]
    calls.clear()
    assert pl.render_vo_lines(spec, tmp_path / "vo")[0]["path"] == first[0]["path"] and calls == []


def test_line_edge_on_non_edge_engine_is_refused_at_compose_time(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "probe_media", lambda path, ffprobe="ffprobe": SimpleNamespace(duration_seconds=1.0))
    with pytest.raises(pl.PostLayerError, match="逐句声线只对 edge 引擎生效"):
        pl.render_vo_lines(_vo_spec(engine="minimax", line_edge={"voice": "zh-CN-YunyangNeural"}), tmp_path / "vo", tts=_write_audio)


def test_validate_voice_over_extras():
    ok = _vo_spec(line_edge={"voice": "zh-CN-YunyangNeural", "rate": "+4%", "pitch": "-6Hz"})["voice_over"]
    assert pl.validate_voice_over_extras({**ok, "subtitles": True}) == []
    assert pl.validate_voice_over_extras({**ok, "engine": ""}) == []                     # 老规则 = edge
    assert pl.validate_voice_over_extras({**ok, "subtitles": None}) == []
    assert pl.validate_voice_over_extras({"lines": [{"shot": "S1", "zh": "x", "edge": None}]}) == []
    assert pl.validate_voice_over_extras({"subtitles": "yes"}) == ["voice_over.subtitles 只能是 true 或 false"]
    errs = pl.validate_voice_over_extras({**ok, "engine": "settings"})
    assert errs == ["voice_over.lines[1] 写了逐句声线 edge，但旁白引擎是「settings」——逐句声线只对 edge 引擎生效："
                    "删掉这句的 edge，或把旁白换成 edge 音色"]
    bad = pl.validate_voice_over_extras({"lines": [
        {"zh": "a", "edge": "zh-CN-YunyangNeural"},
        {"zh": "b", "edge": {"rate": "fast", "pitch": "-8", "volume": "+0%"}},
        {"zh": "c", "edge": {"voice": "  ", "rate": 5}},
    ]})
    assert bad == [
        "voice_over.lines[0].edge 必须是对象：{voice, rate, pitch}",
        "voice_over.lines[1].edge 只认 voice / rate / pitch，多了：volume",
        "voice_over.lines[1].edge 缺 voice：写这一句用的 edge 音色名，如 zh-CN-YunyangNeural",
        "voice_over.lines[1].edge.rate 要写成 +10% / -6% 这样，现在是「fast」",
        "voice_over.lines[1].edge.pitch 要写成 +5Hz / -8Hz 这样，现在是「-8」",
        "voice_over.lines[2].edge 缺 voice：写这一句用的 edge 音色名，如 zh-CN-YunyangNeural",
        "voice_over.lines[2].edge.rate 要写成 +10% / -6% 这样，现在是「5」",
    ]


# ---------------------------------------------------------------------------- 绝对静音区间


def test_validate_silence():
    shots = [{"id": "S1", "dur": 4}, {"id": "S2", "dur": 3}]
    good = [{"shot": "S1", "at": 1.0, "dur": 0.8, "label": "片中静音"}, {"shot": "S1", "at": 1.8, "dur": 2.2}, {"shot": "S2", "at": 0, "dur": 3}]
    assert pl.validate_silence(good, shots) == []
    assert pl.validate_silence(None, shots) == []
    assert pl.validate_silence({"shot": "S1"}, shots) == ["post_graphics.silence 必须是数组"]
    assert pl.validate_silence([
        "x",
        {"shot": "NOPE", "at": 0, "dur": 1},
        {"shot": "S1", "at": -1, "dur": 1},
        {"shot": "S1", "at": True, "dur": 0},
        {"shot": "S1", "at": 0, "dur": 5.5, "label": 3},
        {"shot": "S2", "at": 2.5, "dur": 1},
    ], shots) == [
        "silence[0] 必须是对象",
        "silence[1] 的 shot「NOPE」不是本集的镜号",
        "silence[2].at 要是不小于 0 的秒数，从这一镜在后期时间轴上的起点算",
        "silence[3].at 要是不小于 0 的秒数，从这一镜在后期时间轴上的起点算",
        "silence[3].dur 要大于 0、不超过 5 秒",
        "silence[4].dur 要大于 0、不超过 5 秒",
        "silence[4].label 要是文字",
        "silence[5] 超出了镜 S2 的时长：at + dur = 3.5s，这一镜 3s",
    ]
    assert pl.validate_silence([{"shot": "S1", "at": 1.0, "dur": 1.0}, {"shot": "S1", "at": 1.5, "dur": 0.2}], shots) == [
        "silence[1] 和 silence[0] 在镜 S1 里重叠了"]
    # 首尾相接不算重叠；不同镜的同一段 at 不算重叠
    assert pl.validate_silence([{"shot": "S1", "at": 0, "dur": 1}, {"shot": "S1", "at": 1, "dur": 1}, {"shot": "S2", "at": 0, "dur": 1}], shots) == []


def test_adjacent_silences_with_decimal_seconds_are_not_overlapping():
    """小数秒首尾相接：0.1 + 0.2 = 0.30000000000000004、2.7 + 0.6 = 3.3000000000000003，浮点误差不能判成重叠。"""

    shots = [{"id": "S1", "dur": 6}]
    assert pl.validate_silence([{"shot": "S1", "at": 0.1, "dur": 0.2}, {"shot": "S1", "at": 0.3, "dur": 1}], shots) == []
    assert pl.validate_silence([{"shot": "S1", "at": 2.7, "dur": 0.6}, {"shot": "S1", "at": 3.3, "dur": 0.5}], shots) == []
    # 顺序反过来写也一样
    assert pl.validate_silence([{"shot": "S1", "at": 0.3, "dur": 1}, {"shot": "S1", "at": 0.1, "dur": 0.2}], shots) == []
    # 真重叠（哪怕只多 0.01s）照样拦
    assert pl.validate_silence([{"shot": "S1", "at": 0.1, "dur": 0.21}, {"shot": "S1", "at": 0.3, "dur": 1}], shots) == [
        "silence[1] 和 silence[0] 在镜 S1 里重叠了"]
    # 恰好写到镜尾（小数相加）也不算超出
    assert pl.validate_silence([{"shot": "S1", "at": 5.7, "dur": 0.3}], [{"id": "S1", "dur": 6}]) == []


def test_silence_overrun_warnings():
    spec = {"shots": [{"id": "S1", "dur": 4}, {"id": "S2", "dur": 3}],
            "post_graphics": {"silence": [{"shot": "S1", "at": 3.5, "dur": 1}, {"shot": "S1", "at": 0.1, "dur": 0.2},
                                          {"shot": "S2", "at": 2.7, "dur": 0.3}, {"shot": "NOPE", "at": 9, "dur": 1}, "x"]}}
    assert pl.silence_overrun_warnings(spec) == [
        "shot S1: 静音区间 silence[0] 到 4.5s 才结束，超出了这一镜的时长 4s——"
        "把这一镜改长，或改小 post_graphics.silence 里这一条的 at / dur；不改的话后期块再保存会被拦下"]
    assert pl.silence_overrun_warnings({"shots": spec["shots"]}) == []
    assert pl.silence_overrun_warnings({"shots": spec["shots"], "post_graphics": {"silence": {"shot": "S1"}}}) == []
    # 问题卡：落在这一镜上，归「时长」
    from webui.problems import problem_card

    card = problem_card(pl.silence_overrun_warnings(spec)[0])
    assert (card["scope"], card["target"], card["kind"]) == ("shot", "S1", "timing")


def test_silence_windows_are_absolute_and_clipped_to_real_shot(tmp_path):
    items = pl.build_timeline(_rich_spec(tmp_path), video_dir=tmp_path, ui_dir=tmp_path, durations={"S1": 3.0, "S2": 3.9, "S3": 2.5})
    spec = {"post_graphics": {"silence": [{"shot": "S2", "at": 1.0, "dur": 0.8, "label": "静音"}, {"shot": "S2", "at": 3.5, "dur": 0.5}]}}
    rows = pl.silence_windows(spec, items)
    # S2 在时间线上从 4.5 开始（S1 3 + UI_1 2 − 两次交叉溶解）
    assert rows[0] == {"shot": "S2", "at": 1.0, "dur": 0.8, "label": "静音", "start": 5.5, "end": 6.3}
    # 计划 4s、成片 3.9s：末端截到镜尾
    assert rows[1]["start"] == 8.0 and rows[1]["end"] == 8.4
    assert pl.silence_windows({}, items) == []
    with pytest.raises(pl.PostLayerError, match="这一镜成片只有 3.90s"):
        pl.silence_windows({"post_graphics": {"silence": [{"shot": "S2", "at": 3.95, "dur": 0.5}]}}, items)
    with pytest.raises(pl.PostLayerError, match="不在时间线里"):
        pl.silence_windows({"post_graphics": {"silence": [{"shot": "UI_1", "at": 0, "dur": 0.5}]}}, items)
    with pytest.raises(pl.PostLayerError, match="写得不对"):
        pl.silence_windows({"post_graphics": {"silence": [{"shot": "S2", "at": 0, "dur": 9}]}}, items)


def test_silence_is_applied_after_loudnorm_and_written_to_manifest(tmp_path, monkeypatch):
    _fake_media(monkeypatch, RICH_DURATIONS, RICH_FINAL)
    spec = _rich_spec(tmp_path)
    _manifest0, graph0 = _compose_graph(copy.deepcopy(spec), tmp_path)
    spec["post_graphics"]["silence"] = [{"shot": "S2", "at": 1.0, "dur": 0.8, "label": "唯一静音"}, {"shot": "S3", "at": 0, "dur": 0.5}]
    manifest, graph = _compose_graph(spec, tmp_path)
    filters = graph.split(";\n")
    # 除了最后一行，跟没有静音时逐字一样；最后一行是在 loudnorm 之后接置零
    assert filters[:-1] == graph0.split(";\n")[:-1]
    assert re.fullmatch(
        r"\[axd\]\[dub\d+\]\[mus\d+\]amix=inputs=3:duration=first:normalize=0:dropout_transition=0,loudnorm=I=-16:TP=-1.5:LRA=9,"
        r"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        r"aeval=exprs='val\(ch\)\*not\(between\(t,5\.500,6\.300\)\+between\(t,8\.250,8\.750\)\)':channel_layout=same\[aout\]",
        filters[-1])
    expected = [{"shot": "S2", "at": 1.0, "dur": 0.8, "label": "唯一静音", "start": 5.5, "end": 6.3},
                {"shot": "S3", "at": 0.0, "dur": 0.5, "label": "", "start": 8.25, "end": 8.75}]
    assert manifest["silence"] == expected
    saved = json.loads((Path(spec["output_root"]) / "manifests" / "post.json").read_text(encoding="utf-8"))
    assert saved["silence"] == expected


def test_silence_on_plain_timeline(tmp_path, monkeypatch):
    _fake_media(monkeypatch, MIN_DURATIONS, 6.0)
    spec = _minimal_spec(tmp_path)
    spec["post_graphics"]["silence"] = [{"shot": "S2", "at": 0.25, "dur": 1}]
    _manifest, graph = _compose_graph(spec, tmp_path)
    assert graph.split(";\n")[-1] == (
        "[0:a]loudnorm=I=-16:TP=-1.5:LRA=9,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        "aeval=exprs='val(ch)*not(between(t,3.250,4.250))':channel_layout=same[aout]"
    )


def test_bad_silence_fails_before_ffmpeg_runs(tmp_path, monkeypatch):
    ran = _fake_media(monkeypatch, MIN_DURATIONS, 6.0)
    spec = _minimal_spec(tmp_path)
    spec["post_graphics"]["silence"] = [{"shot": "S2", "at": 3.2, "dur": 0.5}]
    with pytest.raises(pl.PostLayerError, match="silence\\[0\\]"):
        pl.compose_post(spec, project_root=tmp_path)
    assert ran == []


# ---------------------------------------------------------------------------- 真 ffmpeg：静音是数字零，旁白压低的表达式 ffmpeg 认


def _silences(path: Path) -> list[tuple[float, float]]:
    out = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af", "silencedetect=noise=-80dB:d=0.2", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", out)]
    return list(zip(starts, ends))


@pytest.mark.skipif(not FFMPEG or not FONT.is_file(), reason="needs ffmpeg and a CJK font")
def test_compose_post_with_narration_subtitles_and_silence_end_to_end(tmp_path):
    root = tmp_path / "EP"
    (root / "02_video").mkdir(parents=True)
    for sid in ("S1", "S2"):
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=360x640:r=12:d=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(root / "02_video" / f"{sid}.mp4")], check=True)

    def fake_tts(text: str, dest: Path, edge=None) -> None:   # 0.8 秒正弦波代替 edge-tts；逐句声线换个音高
        freq = 330 if edge else 220
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=0.8",
                        "-c:a", "libmp3lame", str(dest)], check=True)

    spec = {
        "id": "VOS", "output_root": str(root),
        "delivery": {"width": 360, "height": 640, "fps": 12, "crossfade_seconds": 0.25},
        "subtitle": {"chinese_font_size": 20},
        "post_graphics": {"fonts": {"notebook": str(FONT), "ui": str(FONT), "subtitle": str(FONT)},
                          "silence": [{"shot": "S2", "at": 1.0, "dur": 0.8, "label": "绝对静音"}]},
        "voice_over": {"subtitles": True, "edge": {"voice": "zh-CN-XiaoxiaoNeural"},
                       "lines": [{"shot": "S1", "at": 0.05, "zh": "今晚有雨。"},
                                 {"shot": "S2", "at": 0.1, "zh": "前方右转。", "edge": {"voice": "zh-CN-YunyangNeural"}}]},
        "shots": [{"id": "S1", "dur": 3, "lines": []}, {"id": "S2", "dur": 3, "lines": []}],
    }
    manifest = pl.compose_post(spec, tts=fake_tts)
    out = Path(manifest["output"])
    assert out.is_file() and manifest["duration_seconds"] == pytest.approx(5.75, abs=0.6)
    assert manifest["silence"] == [{"shot": "S2", "at": 1.0, "dur": 0.8, "label": "绝对静音", "start": 3.75, "end": 4.55}]
    assert manifest["vo"][1]["edge"]["voice"] == "zh-CN-YunyangNeural"
    # 原声是连续的正弦波，全片只有 silence 那一段是数字静音（-80dB 以下），边界误差在几十毫秒内
    spans = _silences(out)
    assert len(spans) == 1, spans
    assert spans[0][0] == pytest.approx(3.75, abs=0.05) and spans[0][1] == pytest.approx(4.55, abs=0.05)


# ---------------------------------------------------------------------------- 接口：PUT post-layers 的校验


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    spec = {
        "id": "VOS-P", "output_root": "output/story_video/VOS-P", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"a": {"block": "A：黑发。"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["a"], "shot": "中景", "art": "a", "motion": "m", "lines": []},
            {"id": "S2", "dur": 4, "cast": ["a"], "shot": "近景", "art": "a", "motion": "m", "lines": []},
        ],
        "voice_over": {"engine": "edge", "edge": {"voice": "zh-CN-XiaoxiaoNeural"}, "lines": [{"shot": "S1", "at": 0.5, "zh": "今晚有雨。"}]},
    }
    (content_dir / "VOS-P.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return tmp_path


URL = "/api/episodes/storyvideo/VOS-P/post-layers"


def _vo_body(**changes) -> dict:
    vo = {"engine": "edge", "edge": {"voice": "zh-CN-XiaoxiaoNeural"},
          "lines": [{"shot": "S1", "at": 0.5, "zh": "今晚有雨。"}, {"shot": "S2", "at": 0.2, "zh": "前方右转。"}]}
    vo.update(changes)
    return vo


def test_put_voice_over_line_edge_and_subtitles(project):
    path = project / "content" / "story_video" / "VOS-P.json"
    before = path.read_text(encoding="utf-8")
    bad_cases = [
        (_vo_body(subtitles="yes"), "voice_over.subtitles 只能是 true 或 false"),
        (_vo_body(lines=[{"shot": "S1", "at": 0, "zh": "x", "edge": "zh-CN-YunyangNeural"}]), "voice_over.lines[0].edge 必须是对象"),
        (_vo_body(lines=[{"shot": "S1", "at": 0, "zh": "x", "edge": {"rate": "-4%"}}]), "voice_over.lines[0].edge 缺 voice"),
        (_vo_body(lines=[{"shot": "S1", "at": 0, "zh": "x", "edge": {"voice": "v", "pitch": "low"}}]), "edge.pitch 要写成"),
        (_vo_body(engine="settings", lines=[{"shot": "S1", "at": 0, "zh": "x", "edge": {"voice": "zh-CN-YunyangNeural"}}]),
         "旁白引擎是「settings」"),
    ]
    for body, needle in bad_cases:
        resp = client.put(URL, json={"voice_over": body})
        assert resp.status_code == 400 and needle in resp.json()["detail"], (body, resp.text)
    assert path.read_text(encoding="utf-8") == before
    good = _vo_body(subtitles=True)
    good["lines"][1]["edge"] = {"voice": "zh-CN-YunyangNeural", "rate": "-4%"}
    resp = client.put(URL, json={"voice_over": good})
    assert resp.status_code == 200, resp.text
    saved = json.loads(path.read_text(encoding="utf-8"))["voice_over"]
    assert saved["subtitles"] is True and saved["lines"][1]["edge"] == {"voice": "zh-CN-YunyangNeural", "rate": "-4%"}
    # 英文界面：新文案有译文
    en = client.put(URL, json={"voice_over": _vo_body(subtitles=1)}, headers={"X-StoryStudio-Lang": "en"})
    assert en.status_code == 400 and en.json()["detail"] == \
        "The change failed validation and was not saved: voice_over.subtitles must be true or false"


def test_put_silence_validation_and_amend_when_frozen(project):
    bad_cases = [
        ({"silence": {"shot": "S1"}}, "post_graphics.silence 必须是数组"),
        ({"silence": [{"shot": "NOPE", "at": 0, "dur": 1}]}, "silence[0] 的 shot「NOPE」不是本集的镜号"),
        ({"silence": [{"shot": "S1", "at": -0.5, "dur": 1}]}, "silence[0].at"),
        ({"silence": [{"shot": "S1", "at": 0, "dur": 0}]}, "silence[0].dur 要大于 0、不超过 5 秒"),
        ({"silence": [{"shot": "S1", "at": 3.5, "dur": 1}]}, "silence[0] 超出了镜 S1 的时长：at + dur = 4.5s，这一镜 4s"),
        ({"silence": [{"shot": "S1", "at": 1, "dur": 1}, {"shot": "S1", "at": 1.5, "dur": 1}]}, "silence[1] 和 silence[0] 在镜 S1 里重叠了"),
    ]
    for pg, needle in bad_cases:
        resp = client.put(URL, json={"post_graphics": pg})
        assert resp.status_code == 400 and needle in resp.json()["detail"], (pg, resp.text)
    good = {"silence": [{"shot": "S2", "at": 1.0, "dur": 0.8, "label": "片中静音"}]}
    resp = client.put(URL, json={"post_graphics": good})
    assert resp.status_code == 200 and resp.json()["status"] == "saved", resp.text
    # 制作中也能改（post_graphics 是后期块，走 amend）
    with StoryVideoRunner(project) as runner:
        runner.prepare("VOS-P")
    moved = copy.deepcopy(good)
    moved["silence"][0]["at"] = 2.0
    resp = client.put(URL, json={"post_graphics": moved})
    assert resp.status_code == 200 and resp.json()["status"] == "amended", resp.text
    got = client.get(URL).json()
    assert got["post_graphics"]["silence"][0]["at"] == 2.0


def test_delete_shot_with_silence_is_blocked_and_links_list_it(project):
    """静音区间也按镜号挂在镜上：删镜要像旁白 / 画面文字一样被拦；镜头详情的反向引用里列出来。"""

    resp = client.put(URL, json={"post_graphics": {"silence": [{"shot": "S2", "at": 1.0, "dur": 0.8, "label": "唯一静音"}]}})
    assert resp.status_code == 200, resp.text
    path = project / "content" / "story_video" / "VOS-P.json"
    before = path.read_text(encoding="utf-8")
    blocked = client.delete("/api/episodes/storyvideo/VOS-P/shots/S2")
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "「S2」还有关联：挂着 1 段静音区间——先解开这些关联再删"
    assert path.read_text(encoding="utf-8") == before
    en = client.delete("/api/episodes/storyvideo/VOS-P/shots/S2", headers={"X-StoryStudio-Lang": "en"})
    assert en.json()["detail"] == '"S2" still has links: 1 silence ranges attached — unlink them before deleting'
    # 和旁白一起挂着：按固定顺序拼，英文整句也能翻
    resp = client.put(URL, json={"post_graphics": {"silence": [{"shot": "S1", "at": 1.0, "dur": 0.5}, {"shot": "S1", "at": 2.0, "dur": 0.5}]}})
    assert resp.status_code == 200, resp.text
    both = client.delete("/api/episodes/storyvideo/VOS-P/shots/S1", headers={"X-StoryStudio-Lang": "en"})
    assert both.status_code == 409
    assert both.json()["detail"] == ('"S1" still has links: 1 narration lines attached; 2 silence ranges attached'
                                     " — unlink them before deleting")
    # 反向引用
    links = {s["id"]: s["links"] for s in client.get("/api/episodes/storyvideo/VOS-P").json()["shots"]}
    assert links["S1"]["silence"] == [{"at": 1.0, "dur": 0.5, "label": ""}, {"at": 2.0, "dur": 0.5, "label": ""}]
    assert links["S2"]["silence"] == []
    # 静音区间解开以后就能删（S2 上没有别的关联）
    assert client.delete("/api/episodes/storyvideo/VOS-P/shots/S2").status_code == 200


def test_shrinking_a_shot_under_its_silence_warns(project):
    """改短镜头不拦，但静音区间超出新时长要当场说（镜头编辑响应 + 概览问题卡），否则要到之后保存后期块才撞上 400。"""

    shot_url = "/api/episodes/storyvideo/VOS-P/shots/S1"
    assert client.put(shot_url, json={"dur": 6}).status_code == 200
    assert client.put(URL, json={"post_graphics": {"silence": [{"shot": "S1", "at": 4.5, "dur": 1}]}}).status_code == 200
    shrink = client.put(shot_url, json={"dur": 4})
    assert shrink.status_code == 200, shrink.text
    warning = ("shot S1: 静音区间 silence[0] 到 5.5s 才结束，超出了这一镜的时长 4s——"
               "把这一镜改长，或改小 post_graphics.silence 里这一条的 at / dur；不改的话后期块再保存会被拦下")
    assert warning in shrink.json()["warnings"]
    detail = client.get("/api/episodes/storyvideo/VOS-P").json()
    card = next(c for c in detail["problems"] if c["text"] == warning)
    assert (card["scope"], card["target"], card["kind"]) == ("shot", "S1", "timing")
    en = client.get("/api/episodes/storyvideo/VOS-P", headers={"X-StoryStudio-Lang": "en"}).json()
    assert ("shot S1: silence range silence[0] ends at 5.5s, past this shot's duration of 4s — lengthen this shot, "
            "or lower this entry's at / dur in post_graphics.silence; otherwise saving post layers will be blocked") in en["warnings"]
    # 提醒说的是实情：不改的话后期块保存确实被拦
    resp = client.put(URL, json={"voice_over": _vo_body()})
    assert resp.status_code == 400 and "silence[0] 超出了镜 S1 的时长" in resp.json()["detail"]
    # 改回够长：提醒消失，后期块又能保存
    grow = client.put(shot_url, json={"dur": 6})
    assert grow.status_code == 200 and warning not in grow.json()["warnings"]
    assert warning not in client.get("/api/episodes/storyvideo/VOS-P").json()["warnings"]
    assert client.put(URL, json={"voice_over": _vo_body()}).status_code == 200
