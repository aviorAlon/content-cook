"""后期合成（compose_post）发第一个 tag 前的修复（2026-09-22 审计 POST-1/2/3/4/5/6/7/8、DRAMA-04/05/06）。

* POST-1：以前全部镜头作为并行输入接进一条 ffmpeg 的 xfade 链，峰值内存随镜数线性涨（≈0.83 GB/镜 @1080x1920）。
  现在逐项归一化 → 按 JOIN_GROUP 分组层级式拼接 → 最后一遍叠字混音：任何一条命令并行解码的视频不超过一组。
  装配版（assemble_episode）的拼接段同样分组。
* POST-2：成片先写临时文件，核过之后才原子替换；失败不碰上一版成片与 post.json。
* 其余：字幕与装配版同一套规则、插卡 / 旁白 / 画面文字的数值兜底、清单不漏项、横屏插卡整屏、交付合同同一口径。

合成部分多数用替身（ffmpeg 只记命令、ffprobe 按文件名报时长），最后几条用真实 ffmpeg 跑 24 条 1080x1920 的片。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from storyvideo import post_layers as pl
from storyvideo.assembly import JOIN_GROUP, AssemblyError, _tail

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="needs ffmpeg + ffprobe on PATH")
FONT = Path("C:/Windows/Fonts/msyh.ttc")
needs_font = pytest.mark.skipif(not FONT.is_file(), reason="needs a CJK font (msyh.ttc)")


# ---------------------------------------------------------------------------- 替身


class FakeMedia:
    """ffprobe 按文件名报时长（镜 = durations，成片临时文件 = 时间线长度或 final），ffmpeg 只记命令并写出输出文件。"""

    def __init__(self, monkeypatch, durations: dict[str, float], *, final: float | None = None, audio: float = 1.2,
                 no_audio: tuple[str, ...] = ()):
        self.commands: list[list[str]] = []
        self.durations = durations
        self.final = final
        self.timeline_end = 0.0
        self.fail_final: Exception | None = None
        real_build = pl.build_timeline

        def build(spec, **kwargs):
            items = real_build(spec, **kwargs)
            self.timeline_end = items[-1].start + items[-1].duration
            return items

        def probe(path, ffprobe="ffprobe"):
            p = Path(path)
            if p.parent.name == "02_video":
                return SimpleNamespace(duration_seconds=durations[p.stem], has_audio=True, size_bytes=1)
            if "_post." in p.name and p.suffix == ".mp4":
                return SimpleNamespace(duration_seconds=self.final if self.final is not None else self.timeline_end,
                                       has_audio=True, size_bytes=p.stat().st_size)
            return SimpleNamespace(duration_seconds=audio, has_audio=p.name not in no_audio, size_bytes=1)

        def run(cmd, **kwargs):
            self.commands.append([str(x) for x in cmd])
            out = Path(cmd[-1])
            if "_post." in out.name and self.fail_final is not None:
                out.write_bytes(b"half")          # ffmpeg 被杀时留下的半截输出
                raise self.fail_final
            out.write_bytes(b"GOOD-" + out.name.encode("utf-8") if "_post." in out.name else b"")

        monkeypatch.setattr(pl, "probe_media", probe)
        monkeypatch.setattr(pl, "_run", run)
        monkeypatch.setattr(pl, "build_timeline", build)
        monkeypatch.setattr(pl, "render_ui_card", lambda insert, dest, **kw: Path(dest))

    def graphs(self) -> list[str]:
        return [c[c.index("-filter_complex") + 1] for c in self.commands if "-filter_complex" in c]


def _inputs(cmd: list[str]) -> list[str]:
    return [cmd[i + 1] for i, x in enumerate(cmd) if x == "-i"]


def _video_inputs(cmd: list[str]) -> list[str]:
    """这条命令真正解码画面的片段输入（滤镜图里被 [k:v] 引用的 mp4 / mov；没有滤镜图的 concat 复制只读一路清单）。
    有交叉溶解的拼接命令把同一批文件开两遍、后一半只取声音，那一半不算。"""

    paths = _inputs(cmd)
    if "-filter_complex" not in cmd:
        return paths
    used = {int(k) for k in re.findall(r"\[(\d+):v\]", cmd[cmd.index("-filter_complex") + 1])}
    return [p for i, p in enumerate(paths) if i in used and Path(p).suffix.lower() in (".mp4", ".mov")]


def _audio_input_indices(cmd: list[str]) -> list[int]:
    return sorted({int(k) for k in re.findall(r"\[(\d+):a\]", cmd[cmd.index("-filter_complex") + 1])})


def _spec(tmp_path: Path, n: int = 3, *, crossfade: float = 0.25, dur: float = 3.0, **extra) -> dict:
    root = tmp_path / "output" / "story_video" / "EP"
    (root / "02_video").mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (root / "02_video" / f"S{i + 1}.mp4").write_bytes(b"x")
    fonts = tmp_path / "fonts"
    fonts.mkdir(exist_ok=True)
    (fonts / "f.ttf").write_bytes(b"x")
    spec = {
        "id": "EP", "output_root": str(root),
        "delivery": {"width": 360, "height": 640, "fps": 12, "crossfade_seconds": crossfade},
        "subtitle": {"language": "zh", "font_path": str(fonts / "f.ttf"), "chinese_font_size": 30},
        "post_graphics": {"fonts": {"notebook": str(fonts / "f.ttf"), "ui": str(fonts / "f.ttf"), "subtitle": str(fonts / "f.ttf")}},
        "shots": [{"id": f"S{i + 1}", "dur": dur, "lines": []} for i in range(n)],
    }
    spec.update(extra)
    return spec


def _silent_tts(text, dest, *_rest):
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(b"mp3")


# ---------------------------------------------------------------------------- POST-1：分段流式，任何一条命令都不并行接全部镜头


def test_many_shots_never_feed_more_than_one_group_into_a_command(tmp_path, monkeypatch):
    n = 24
    media = FakeMedia(monkeypatch, {f"S{i + 1}": 3.0 for i in range(n)})
    spec = _spec(tmp_path, n)
    manifest = pl.compose_post(spec, project_root=tmp_path)
    normalize = [c for c in media.commands if "-filter_complex" in c and "xfade=" not in c[c.index("-filter_complex") + 1]]
    joins = [c for c in media.commands if "-filter_complex" in c and "xfade=" in c[c.index("-filter_complex") + 1]]
    final = media.commands[-1]
    # 每一项一条归一化命令，只读自己那一路
    assert len(normalize) == n and all(len(_inputs(c)) == 1 for c in normalize)
    # 24 段：第一级 3 组 × 8 段，第二级把 3 段接起来；任何一条并行解码的画面都不超过一组
    assert [len(_video_inputs(c)) for c in joins] == [8, 8, 8, 3]
    assert max(len(_video_inputs(c)) for c in media.commands) <= JOIN_GROUP
    # 声音从同一批文件的第二次打开里读（音画同一个输入时 ffmpeg 6.0 的 xfade 会硬切，见 assembly.join_inputs）
    for cmd in joins:
        paths, m = _inputs(cmd), len(_inputs(cmd)) // 2
        assert paths[m:] == paths[:m] and _audio_input_indices(cmd) == list(range(m, 2 * m))
    # 最后一遍只有拼好的那一路视频
    assert len(_video_inputs(final)) == 1 and _inputs(final)[0].endswith("join_2_000.mov")
    # 第二级的接缝 = 以前一条链上第 8、16 项的 offset（时间线起点），交叉溶解量不变
    starts = [x["start"] for x in manifest["timeline"]]
    top = joins[-1][joins[-1].index("-filter_complex") + 1]
    assert re.findall(r"offset=([\d.]+)", top) == [f"{starts[8]:.3f}", f"{starts[16]:.3f}"]
    assert re.findall(r"xfade=transition=fade:duration=([\d.]+)", top) == ["0.250", "0.250"]
    assert manifest["join"] == {"segments": n, "group_size": JOIN_GROUP, "commands": 4}
    assert manifest["expected_duration_seconds"] == pytest.approx(n * 3.0 - (n - 1) * 0.25)


def test_many_shots_without_crossfade_are_joined_by_one_sequential_concat(tmp_path, monkeypatch):
    n = 24
    media = FakeMedia(monkeypatch, {f"S{i + 1}": 3.0 for i in range(n)})
    pl.compose_post(_spec(tmp_path, n, crossfade=0), project_root=tmp_path)
    concat = [c for c in media.commands if "concat" in c]
    assert len(concat) == 1 and concat[0][concat[0].index("-c") + 1] == "copy"
    listing = Path(_inputs(concat[0])[0])
    assert not listing.exists()                     # 临时工作目录合成完就清掉
    assert not any("xfade=" in g for g in media.graphs())
    assert max(len(_video_inputs(c)) for c in media.commands) == 1


def test_assembly_join_is_grouped_when_there_are_many_shots(tmp_path):
    """装配版的拼接段同一个毛病（≈0.45 GB/镜）：超过 JOIN_GROUP 镜时先分组拼中间片，最后一级命令与以前同一形状。"""

    from unittest.mock import patch

    from storyvideo.assembly import assemble_episode
    from storyvideo.spec import normalize_spec

    n = 20
    raw = {"id": "EP99", "output_root": "output/story_video/EP99", "cast": {"su": {"appearance": "adult man"}},
           "audio_mode": "native_seedance_speech",
           "shots": [{"id": f"S{i + 1}", "dur": 4, "cast": ["su"], "shot": "medium", "art": "a", "motion": "m", "lines": []}
                     for i in range(n)]}
    spec = normalize_spec(raw, project_root=tmp_path)
    video_dir = Path(spec["output_root"]) / "02_video"
    video_dir.mkdir(parents=True)
    for shot in spec["shots"]:
        (video_dir / f"{shot['id']}.mp4").write_bytes(b"source" * 300)
    font = tmp_path / "font.ttf"
    font.write_bytes(b"font")
    calls: list[list[str]] = []
    expected = n * 4.0 - (n - 1) * 0.25

    def probe_payload(path: Path) -> str:
        if path.name.startswith("join_1_000"):
            duration = 8 * 4.0 - 7 * 0.25
        elif path.name.startswith("join_1_001"):
            duration = 8 * 4.0 - 7 * 0.25
        elif path.name.startswith("join_1_002"):
            duration = 4 * 4.0 - 3 * 0.25
        elif "EP99_final" in path.name:
            duration = expected
        else:
            duration = 4.0
        return json.dumps({"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920,
                                        "avg_frame_rate": "24/1"},
                                       {"codec_type": "audio", "sample_rate": "48000", "channels": 2}],
                           "format": {"duration": str(duration)}})

    def run(command, **kwargs):
        command = [str(x) for x in command]
        calls.append(command)
        if Path(command[0]).name.lower().startswith("ffprobe"):
            return subprocess.CompletedProcess(command, 0, stdout=probe_payload(Path(command[-1])), stderr="")
        if command[-1] != "-" and command[-2:] != ["null", "-"]:
            Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(command[-1]).write_bytes(b"generated" * 300)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with patch("storyvideo.assembly.subprocess.run", side_effect=run):
        result = assemble_episode(spec, font_path=font, trim_head_fn=lambda *_: 0.0, speech_segments_fn=lambda _p: [])
    joins = [c for c in calls if Path(c[0]).name.lower().startswith("ffmpeg") and "-filter_complex" in c
             and "xfade=" in c[c.index("-filter_complex") + 1]]
    assert [len(_video_inputs(c)) for c in joins] == [8, 8, 4, 3]
    for cmd in joins:                                   # 声音从同一批文件的第二次打开里读
        paths, m = _inputs(cmd), len(_inputs(cmd)) // 2
        assert paths[m:] == paths[:m] and _audio_input_indices(cmd) == list(range(m, 2 * m))
    # 中间片：高质量中间编码，链尾截到这一组的长度；最后一级：与以前同一套交付编码（crf 21 + maxrate）
    first = joins[0][joins[0].index("-filter_complex") + 1]
    assert first.count("xfade=") == 7 and f"trim=duration={8 * 4.0 - 7 * 0.25:.3f}[v7]" in first
    assert "pcm_s24le" in joins[0] and "-maxrate" not in joins[0]
    last = joins[-1]
    assert "-maxrate" in last and "trim=" not in last[last.index("-filter_complex") + 1]
    assert re.findall(r"offset=([\d.]+)", last[last.index("-filter_complex") + 1]) == [f"{8 * 3.75:.3f}", f"{16 * 3.75:.3f}"]
    assert result.duration_seconds == pytest.approx(expected)


# ---------------------------------------------------------------------------- POST-2：临时文件 + 原子替换


def test_failed_compose_keeps_previous_output_and_manifest(tmp_path, monkeypatch):
    media = FakeMedia(monkeypatch, {"S1": 3.0, "S2": 3.0})
    spec = _spec(tmp_path, 2)
    root = Path(spec["output_root"])
    first = pl.compose_post(spec, project_root=tmp_path)
    dest = root / "EP_post.mp4"
    good = dest.read_bytes()
    manifest_before = (root / "manifests" / "post.json").read_text(encoding="utf-8")
    assert good.startswith(b"GOOD-") and first["size_bytes"] == len(good) and first["output"] == str(dest)
    # 第二次在最后一遍被杀（超时 / OOM / 关机）：旧成片、旧清单原样在，临时文件和工作目录都清掉
    media.fail_final = AssemblyError("compose post layers: could not run ffmpeg: timed out after 900.0 seconds")
    with pytest.raises(AssemblyError, match="timed out"):
        pl.compose_post(spec, project_root=tmp_path)
    assert dest.read_bytes() == good
    assert (root / "manifests" / "post.json").read_text(encoding="utf-8") == manifest_before
    assert sorted(p.name for p in root.iterdir() if p.name.startswith(".")) == []
    # 写出来了、但时长对不上时间线：同样不替换
    media.fail_final = None
    media.final = 99.0
    with pytest.raises(pl.PostLayerError, match="与时间线"):
        pl.compose_post(spec, project_root=tmp_path)
    assert dest.read_bytes() == good and not [p for p in root.iterdir() if ".tmp" in p.name]


def test_deliverable_ignores_a_post_output_that_does_not_match_its_manifest(tmp_path):
    from webui import deps

    root = tmp_path / "EP"
    (root / "manifests").mkdir(parents=True)
    post, final = root / "EP_post.mp4", root / "EP_final.mp4"
    final.write_bytes(b"final")
    post.write_bytes(b"x" * 48)                                     # 改成原子替换之前留下的残片
    (root / "manifests" / "post.json").write_text(json.dumps({"output": str(post), "size_bytes": 849819}), encoding="utf-8")
    spec = {"id": "EP", "output_root": str(root)}
    assert deps._deliverable_path(spec) == (final, "final")
    # 字节数对得上才优先后期版；老清单没记字节数的照旧
    (root / "manifests" / "post.json").write_text(json.dumps({"output": str(post), "size_bytes": 48}), encoding="utf-8")
    assert deps._deliverable_path(spec) == (post, "post")
    (root / "manifests" / "post.json").write_text(json.dumps({"output": str(post)}), encoding="utf-8")
    assert deps._deliverable_path(spec) == (post, "post")


# ---------------------------------------------------------------------------- POST-3：字幕与装配版同一套规则


def _drawtexts(graph: str) -> dict[str, str]:
    """textfile 名 → enable 区间。"""

    out = {}
    for m in re.finditer(r"textfile='([^']+)'[^;]*?enable='between\(t,([\d.]+),([\d.]+)\)'", graph):
        out[m.group(1).rsplit("/", 1)[-1]] = (m.group(2), m.group(3))
    return out


def _graph(spec) -> str:
    return (Path(spec["output_root"]) / "manifests" / "post_filtergraph.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("language, expected", [
    ("zh", {"S1.zh.0.txt"}),
    ("bilingual", {"S1.en.0.txt", "S1.zh.0.txt"}),
    ("en", {"S1.en.0.txt", "S2.en.0.txt"}),        # S2 只有英文台词：英文字幕照画，以前一条都没有
])
def test_subtitle_language_is_honoured(tmp_path, monkeypatch, language, expected):
    FakeMedia(monkeypatch, {"S1": 5.0, "S2": 5.0})
    spec = _spec(tmp_path, 2, crossfade=0, dur=5)
    spec["subtitle"]["language"] = language
    spec["shots"][0]["lines"] = [{"who": "a", "zh": "你好", "en": "Hello"}]
    spec["shots"][1]["lines"] = [{"who": "a", "en": "Only English"}]
    manifest = pl.compose_post(spec, project_root=tmp_path)
    assert set(_drawtexts(_graph(spec))) == expected
    assert manifest["subtitle"]["language"] == language


def test_subtitle_display_full_shot_and_explicit_windows(tmp_path, monkeypatch):
    FakeMedia(monkeypatch, {"S1": 5.0, "S2": 8.0})
    spec = _spec(tmp_path, 2, crossfade=0, dur=5)
    spec["shots"][0]["lines"] = [{"who": "a", "zh": "整镜"}]
    spec["shots"][1]["lines"] = [{"who": "a", "zh": "第一句", "subtitle_start_seconds": 0.5, "subtitle_end_seconds": 1.5},
                                 {"who": "a", "zh": "第二句", "subtitle_start_seconds": 6.0, "subtitle_end_seconds": 7.5}]
    pl.compose_post(spec, project_root=tmp_path)
    windows = _drawtexts(_graph(spec))
    # 逐句显式窗口按这一镜在时间线上的起点换算（S2 从 5.0 开始）
    assert windows["S2.zh.0.txt"] == ("5.500", "6.500") and windows["S2.zh.1.txt"] == ("11.000", "12.500")
    spec["subtitle"]["display"] = "full_shot"
    pl.compose_post(spec, project_root=tmp_path)
    windows = _drawtexts(_graph(spec))
    assert windows["S1.zh.0.txt"] == ("0.000", "5.000") and windows["S2.zh.0.txt"] == ("5.000", "13.000")


def test_long_dialogue_and_narration_subtitles_wrap_instead_of_running_off_frame(tmp_path, monkeypatch):
    FakeMedia(monkeypatch, {"S1": 5.0})
    spec = _spec(tmp_path, 1, crossfade=0, dur=5)
    long_line = "这一句台词非常非常长，长到一行根本放不下，装配版会自动折行但后期版不会。"
    spec["shots"][0]["lines"] = [{"who": "a", "zh": long_line}]
    spec["voice_over"] = {"subtitles": True, "lines": [{"shot": "S1", "at": 3.0, "zh": long_line}]}
    pl.compose_post(spec, tts=_silent_tts, project_root=tmp_path)
    text_dir = Path(spec["output_root"]) / "manifests" / "subtitles"
    for name in ("S1.zh.0.txt", "vo1.zh.0.txt"):
        wrapped = (text_dir / name).read_text(encoding="utf-8")
        assert wrapped.replace("\n", "") == long_line and wrapped.count("\n") == 1
    graph = _graph(spec)
    assert "text='这一句" not in graph and "line_spacing=" in graph


# ---------------------------------------------------------------------------- POST-4 / DRAMA-04 / DRAMA-05 / DRAMA-06：合成侧兜手改 spec


@pytest.mark.parametrize("dur", ["abc", -1, 0, [1], True])
def test_bad_insert_duration_fails_with_a_readable_error_before_ffmpeg(tmp_path, monkeypatch, dur):
    media = FakeMedia(monkeypatch, {"S1": 3.0, "S2": 3.0})
    spec = _spec(tmp_path, 2)
    spec["post_graphics"]["phone_ui_inserts"] = [{"id": "C", "after": "S1", "dur": dur, "lines": ["x"]}]
    with pytest.raises(pl.PostLayerError, match="插卡「C」的 dur 要是大于 0 的秒数"):
        pl.compose_post(spec, project_root=tmp_path)
    assert media.commands == []


def test_insert_shorter_than_the_crossfade_keeps_the_timeline_monotonic(tmp_path):
    spec = _spec(tmp_path, 2)
    spec["post_graphics"]["phone_ui_inserts"] = [{"id": "C", "after": "S1", "dur": 0.1, "lines": ["x"]}]
    items = pl.build_timeline(spec, video_dir=tmp_path, ui_dir=tmp_path, durations={"S1": 3.0, "S2": 3.0})
    assert [x.item_id for x in items] == ["S1", "C", "S2"]
    assert all(b.start > a.start for a, b in zip(items, items[1:]))
    # 重叠夹到短的一方的一半：卡本身留得住，xfade 第二路有帧可溶
    assert pl.seam_overlaps(items) == [0.0, 0.05, 0.05]


@pytest.mark.parametrize("c1_after", ["C1", "C2"])          # 自引用 / C1 ↔ C2 互相引用
def test_insert_cycle_fails_before_ffmpeg(tmp_path, monkeypatch, c1_after):
    media = FakeMedia(monkeypatch, {"S1": 3.0, "S2": 3.0})
    spec = _spec(tmp_path, 2)
    spec["post_graphics"]["phone_ui_inserts"] = [{"id": "C1", "after": c1_after, "dur": 2, "lines": ["x"]},
                                                 {"id": "C2", "after": "C1", "dur": 2, "lines": ["y"]}]
    with pytest.raises(pl.PostLayerError, match="形成了环"):
        pl.compose_post(spec, project_root=tmp_path)
    assert media.commands == []


def test_narration_outside_the_film_is_reported_not_silently_dropped(tmp_path, monkeypatch):
    FakeMedia(monkeypatch, {"S1": 3.0, "S2": 3.0}, audio=1.2)
    spec = _spec(tmp_path, 2)
    spec["voice_over"] = {"lines": [{"shot": "S1", "at": 0.5, "zh": "正常"}, {"shot": "S2", "at": 2.5, "zh": "尾巴出片"},
                                    {"shot": "S2", "at": 999, "zh": "太晚"}]}
    manifest = pl.compose_post(spec, tts=_silent_tts, project_root=tmp_path)
    assert [(x["zh"], x["start"]) for x in manifest["vo_skipped"]] == [("太晚", pytest.approx(1001.75))]
    assert len(manifest["vo_overflow"]) == 1 and "尾巴出片" in manifest["vo_overflow"][0]
    graph = _graph(spec)
    assert "adelay=999" not in graph and graph.count("[vo") >= 2
    # at 写成字符串 / 负数：人话报错，不再是裸 ValueError，也不再静默按 0 播
    for bad in ("abc", -5):
        spec["voice_over"] = {"lines": [{"shot": "S1", "at": bad, "zh": "坏"}]}
        with pytest.raises(pl.PostLayerError, match="at 要是不小于 0 的秒数"):
            pl.compose_post(spec, tts=_silent_tts, project_root=tmp_path)


def test_notebook_text_is_clipped_to_its_shot_and_bad_values_fail(tmp_path, monkeypatch):
    FakeMedia(monkeypatch, {"S1": 3.0, "S2": 3.0})
    spec = _spec(tmp_path, 2)
    spec["post_graphics"]["notebook_text"] = [{"shot": "S1", "text": "压到后面", "at": 1.0, "until": 100}]
    pl.compose_post(spec, project_root=tmp_path)
    assert "enable='between(t,1.000,2.950)'" in _graph(spec)          # 截到 S1 镜尾，不再压到后面每一镜
    for entry, needle in (({"until": 0.5, "at": 1.0}, "until 要大于 at"), ({"y": 2}, "y 要是 0 到 1"),
                          ({"size": "abc"}, "size 要是不小于 1"), ({"at": "x"}, "at 要是不小于 0"),
                          ({"at": 3.5}, "从镜 S1 的 3.5s 开始，但这一镜成片只有 3.00s"),       # 起点在镜外：以前悄悄不出现
                          ({"shot": "NOPE"}, "挂在的「NOPE」不在时间线上")):                     # 挂错镜：以前静默跳过
        spec["post_graphics"]["notebook_text"] = [{"shot": "S1", "text": "字", **entry}]
        with pytest.raises(pl.PostLayerError, match=needle):
            pl.compose_post(spec, project_root=tmp_path)


# ---------------------------------------------------------------------------- POST-5：配乐不是音频


def test_non_audio_music_names_the_track_instead_of_dumping_the_filtergraph(tmp_path, monkeypatch):
    media = FakeMedia(monkeypatch, {"S1": 3.0}, no_audio=("cover.mp3",))
    spec = _spec(tmp_path, 1)
    music = tmp_path / "content" / "music"
    music.mkdir(parents=True)
    (music / "cover.mp3").write_bytes(b"\x89PNG not audio")
    spec["post_graphics"]["music"] = [{"file": "content/music/cover.mp3", "label": "封面图", "start": 0}]
    with pytest.raises(pl.PostLayerError, match="配乐「封面图」不是可读的音频文件"):
        pl.compose_post(spec, project_root=tmp_path)
    assert media.commands == []


def test_long_ffmpeg_errors_keep_the_cause_at_the_front():
    graph = "[0:v]scale=1080:1920," + "x" * 3000
    text = _tail(f"Stream specifier ':a' in filtergraph description {graph} matches no streams.")
    assert text.startswith("Stream specifier ':a'") and text.endswith("matches no streams.") and len(text) < 1100


# ---------------------------------------------------------------------------- POST-6：清单不漏项


def test_manifest_lists_dropped_inserts_and_music_with_reasons(tmp_path, monkeypatch):
    FakeMedia(monkeypatch, {"S1": 3.0, "S2": 3.0})
    spec = _spec(tmp_path, 2)
    music = tmp_path / "content" / "music"
    music.mkdir(parents=True)
    (music / "a.mp3").write_bytes(b"x")
    spec["post_graphics"]["phone_ui_inserts"] = [{"id": "OK", "after": "S1", "dur": 2, "lines": ["x"]},
                                                 {"id": "LOST", "after": "NOPE", "dur": 2, "lines": ["y"]},
                                                 {"id": "TAIL", "after": "LOST", "dur": 2, "lines": ["z"]}]
    spec["post_graphics"]["music"] = [{"file": "content/music/a.mp3", "label": "ok", "start": 0},
                                      {"file": "content/music/a.mp3", "label": "late", "start": 30},
                                      {"file": "content/music/nope.mp3", "label": "missing", "start": 0}]
    manifest = pl.compose_post(spec, project_root=tmp_path)
    root = Path(spec["output_root"])
    assert [x["id"] for x in manifest["timeline"]] == ["S1", "OK", "S2"]
    assert manifest["ui_inserts"] == [str(root / "08_ui" / "ui_000.png")]      # 按时间线记：清单里有的插卡一定在成片里
    assert [(x["id"], x["reason"]) for x in manifest["ui_inserts_skipped"]] == [
        ("LOST", "after「NOPE」既不是镜号也不是别的插卡 id，没有接到时间线上"),
        ("TAIL", "after「LOST」指向的插卡也没有接到时间线上"),
    ]
    assert [m["label"] for m in manifest["music"]] == ["ok"] and manifest["music_missing"] == ["missing"]
    assert [(m["label"], m["start"]) for m in manifest["music_skipped"]] == [("late", 30.0)]
    assert "不早于片尾" in manifest["music_skipped"][0]["reason"]


@needs_font
def test_corner_labels_in_the_same_row_do_not_overlap(tmp_path):
    spec = {"delivery": {"overlay_preset": "douyin"},
            "post_graphics": {"labels": [{"text": "AI 生成", "position": "top_right"},
                                         {"text": "这是一条非常非常长的角标文字二十四个字整", "position": "top_left", "size": 46}]}}
    rows, _skipped = pl.label_layers(spec, tmp_path, width=1080, height=1920, timeline_end=10.0, font_path=str(FONT))
    (a, b) = [(r["x"], r["y"], r["x"] + r["width"], r["y"] + r["height"]) for r in rows]
    assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


# ---------------------------------------------------------------------------- POST-7 / POST-8：交付宽高 / 帧率同一口径，插卡按交付尺寸画


@pytest.mark.parametrize("extra, size, fps", [
    ({"video_params": {"aspect_ratio": "16:9", "resolution": "1080p"}, "delivery": None}, (1920, 1080), 24),
    ({"video_params": {"resolution": "720p", "fps": 30}, "delivery": None}, (720, 1280), 30),
    ({"delivery": {"resolution": "720p", "fps": 30, "crossfade_seconds": 0}}, (720, 1280), 30),
    ({"delivery": {"width": 360, "height": 640, "fps": 12, "crossfade_seconds": 0}}, (360, 640), 12),
])
def test_compose_uses_the_same_delivery_contract_as_assembly(tmp_path, monkeypatch, extra, size, fps):
    from storyvideo.assembly import delivery_settings

    media = FakeMedia(monkeypatch, {"S1": 3.0, "S2": 3.0})
    sizes: list[tuple[int, int]] = []
    monkeypatch.setattr(pl, "render_ui_card", lambda insert, dest, **kw: sizes.append(kw["size"]) or Path(dest))
    spec = _spec(tmp_path, 2)
    spec.update({k: v for k, v in extra.items() if v is not None})
    if extra.get("delivery", 1) is None:
        spec.pop("delivery")
    spec["post_graphics"]["phone_ui_inserts"] = [{"id": "C", "after": "S1", "dur": 2, "layout": "card", "lines": ["字幕卡"]}]
    manifest = pl.compose_post(spec, project_root=tmp_path)
    width, height, contract_fps, _xfade = delivery_settings(spec)
    assert (width, height, contract_fps) == (*size, fps)
    assert manifest["delivery"]["width"] == width and manifest["delivery"]["height"] == height and manifest["delivery"]["fps"] == fps
    norm = [c[c.index("-filter_complex") + 1] for c in media.commands if "-filter_complex" in c and "xfade=" not in c[c.index("-filter_complex") + 1]]
    assert all(f"scale={width}:{height}:" in g and f"fps={fps:g}," in g for g in norm)
    assert sizes == [(width, height)]                 # 插卡按交付尺寸画，横屏时不再是中间一条竖条


@needs_font
@pytest.mark.parametrize("layout", ["card", "plain", "notification"])
def test_landscape_insert_fills_the_frame(tmp_path, layout):
    from PIL import Image

    insert = {"id": "C", "layout": layout, "lines": ["横屏插卡"], "items": ["一条通知"], "background": "#2040A0"}
    out = pl.render_ui_card(insert, tmp_path / "c.png", font_path=str(FONT), size=(1920, 1080))
    with Image.open(out) as im:
        assert im.size == (1920, 1080)
        if layout == "card":
            assert im.getpixel((10, 540)) == im.getpixel((1910, 540)) == (0x20, 0x40, 0xA0)


# ---------------------------------------------------------------------------- 真 ffmpeg：24 条 1080x1920 片


def _make_clips(root: Path, n: int, seconds: float) -> None:
    video = root / "02_video"
    video.mkdir(parents=True, exist_ok=True)
    first = video / "S1.mp4"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=1080x1920:rate=24:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(first)], check=True)
    for i in range(1, n):
        shutil.copyfile(first, video / f"S{i + 1}.mp4")


class _PeakRss:
    """本进程子进程里 ffmpeg 的峰值 RSS（MB）；没有 psutil 时不量。"""

    def __init__(self):
        try:
            import psutil
        except ImportError:
            self.psutil = None
            return
        self.psutil = psutil
        self.peak = 0
        self.stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        me = self.psutil.Process()
        while not self.stop:
            total = 0
            for proc in me.children(recursive=True):
                try:
                    if proc.name().lower().startswith("ffmpeg"):
                        total += proc.memory_info().rss
                except self.psutil.Error:
                    pass
            self.peak = max(self.peak, total)
            time.sleep(0.05)

    def done(self) -> float | None:
        if self.psutil is None:
            return None
        self.stop = True
        self.thread.join(timeout=2)
        return self.peak / 1e6


@needs_ffmpeg
@pytest.mark.parametrize("crossfade", [0.25, 0.0])
def test_real_24_shots_at_1080x1920_match_the_manifest_with_bounded_memory(tmp_path, crossfade):
    n, seconds = 24, 1.0
    root = tmp_path / "output" / "story_video" / "MEM"
    _make_clips(root, n, seconds)
    previous = root / "MEM_post.mp4"
    spec = {"id": "MEM", "output_root": str(root), "subtitle": {"language": "zh"},
            "delivery": {"width": 1080, "height": 1920, "fps": 24, "crossfade_seconds": crossfade},
            "shots": [{"id": f"S{i + 1}", "dur": seconds, "lines": []} for i in range(n)]}
    peak = _PeakRss()
    manifest = pl.compose_post(spec, project_root=tmp_path)
    rss = peak.done()
    info = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,duration",
                                      "-of", "json", str(previous)], capture_output=True, text=True, check=True).stdout)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    expected = n * seconds - (n - 1) * crossfade
    assert manifest["expected_duration_seconds"] == pytest.approx(expected, abs=1e-3)
    # post.json 记的就是成片的实际时长；画面与时间线差不到一帧（分几级拼也不多出帧），整片（含 AAC 尾巴）差不到 0.1 秒
    assert float(info["format"]["duration"]) == pytest.approx(manifest["duration_seconds"], abs=1e-3)
    assert float(video["duration"]) == pytest.approx(expected, abs=1 / 24 + 1e-3)
    assert manifest["duration_seconds"] == pytest.approx(expected, abs=0.1)
    assert video["width"] == 1080 and video["height"] == 1920
    assert manifest["size_bytes"] == previous.stat().st_size
    assert not [p for p in root.iterdir() if p.name.startswith(".")]      # 工作目录与临时成片都清掉了
    if rss is not None:
        # 以前一条命令并行接 24 路（24 条 5 s 的片实测峰值 ≈ 20 GB）。分段后每条命令最多解一组画面，同样 24 条 5 s 实测 ≈ 2.2 GB
        assert rss < 3500, f"peak ffmpeg RSS {rss:.0f} MB"


# ---------------------------------------------------------------------------- 真 ffmpeg：每个接缝都是真溶解，不是硬切


def _black_white_clips(video_dir: Path, n: int, seconds: float, *, audio_tail: float = 0.0) -> None:
    """黑白交替的源片。``audio_tail`` > 0 时音轨比画面长这么多秒——导入的外部片子、别家模型的出片常这样，
    而容器时长（= 各镜在时间线上占的长度）跟着音轨走。"""

    video_dir.mkdir(parents=True, exist_ok=True)
    for color in ("white", "black"):
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:size=180x320:rate=24:duration={seconds}",
                        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds + audio_tail}",
                        "-map", "0:v", "-map", "1:a",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        str(video_dir.parent / f"{color}-src.mp4")], check=True)
    for i in range(n):
        shutil.copyfile(video_dir.parent / ("white-src.mp4" if i % 2 == 0 else "black-src.mp4"), video_dir / f"S{i + 1}.mp4")


def _hard_cut_seams(output: Path, n: int, seconds: float, crossfade: float) -> tuple[int, list[int]]:
    """黑白交替片拼出来的成片：(帧数, 溶解窗口里一帧中间亮度都没有的接缝)。"""

    text = subprocess.run([FFMPEG, "-loglevel", "error", "-i", str(output), "-vf",
                           "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-", "-f", "null", "-"],
                          capture_output=True, text=True, check=True).stdout
    lumas = [float(line.split("=")[1]) for line in text.splitlines() if "YAVG" in line]
    hard = []
    for k in range(1, n):
        start = k * (seconds - crossfade)
        window = lumas[int(round(start * 24)):int(round((start + crossfade) * 24)) + 1]
        if not [v for v in window if 40 < v < 200]:
            hard.append(k)
    return len(lumas), hard


@needs_ffmpeg
def test_real_crossfades_are_not_hard_cuts_in_the_post_version(tmp_path):
    """ffmpeg 6.0 的 xfade 在音画同一个输入时，从一组的第 5 个接缝起全变硬切（分段后第一次实测就是这样）；
    画面和声音各读各的文件实例之后，12 段（跨两组、两级）每个接缝都要看得到溶解，帧数与时间线一致。"""

    n, seconds, crossfade = 12, 1.0, 0.25
    root = tmp_path / "output" / "story_video" / "SEAM"
    _black_white_clips(root / "02_video", n, seconds)
    spec = {"id": "SEAM", "output_root": str(root), "subtitle": {"language": "zh"},
            "delivery": {"width": 180, "height": 320, "fps": 24, "crossfade_seconds": crossfade},
            "shots": [{"id": f"S{i + 1}", "dur": seconds, "lines": []} for i in range(n)]}
    manifest = pl.compose_post(spec, project_root=tmp_path)
    frames, hard = _hard_cut_seams(Path(manifest["output"]), n, seconds, crossfade)
    assert hard == [] and frames == round((n * seconds - (n - 1) * crossfade) * 24)


@needs_ffmpeg
@needs_font
def test_real_crossfades_are_not_hard_cuts_in_the_assembled_version(tmp_path):
    """装配版的拼接段同一个毛病（改分段之前 12 镜里第 9、10 个接缝就是硬切）。"""

    from storyvideo.assembly import assemble_episode
    from storyvideo.spec import normalize_spec

    n, seconds, crossfade = 12, 4.0, 0.25          # 具名模型的镜长下限是 4 秒
    raw = {"id": "SEAM", "output_root": "output/story_video/SEAM", "cast": {"a": {"appearance": "x"}},
           "audio_mode": "native_seedance_speech", "subtitle": {"language": "zh"},
           "delivery": {"width": 180, "height": 320, "fps": 24, "crossfade_seconds": crossfade},
           "shots": [{"id": f"S{i + 1}", "dur": seconds, "cast": ["a"], "shot": "medium", "art": "a", "motion": "m", "lines": []}
                     for i in range(n)]}
    spec = normalize_spec(raw, project_root=tmp_path)
    _black_white_clips(Path(spec["output_root"]) / "02_video", n, seconds)
    result = assemble_episode(spec, font_path=FONT, trim_head_fn=lambda *_: 0.0)
    frames, hard = _hard_cut_seams(Path(result.final_path), n, seconds, crossfade)
    assert hard == [] and frames == round((n * seconds - (n - 1) * crossfade) * 24)


def _video_stream_seconds(path: Path) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration",
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True).stdout
    return float(out.strip())


@needs_ffmpeg
def test_clips_whose_audio_outlasts_the_picture_still_have_picture_to_the_end(tmp_path):
    """镜次的音轨比画面长时，成片后半段没有画面。

    每镜在时间线上占的长度取的是**容器**时长（= 音轨长），归一化那一步以前只给音轨补静音、没给画面补长，
    于是每段中间片的画面比声音短。接缝的 xfade offset 按时间线算，音尾比交叉溶解还长时 offset 就落在前一段
    画面 EOF 之后——ffmpeg 判定溶解结束，这一组剩下的画面整段丢掉，层级式拼接每一级再丢一次。
    修之前 12 镜 / 音尾 +0.3s：61.4s 的时间线只剩 35.0s 有画面（841 帧 / 应有 1472），而按容器时长做的
    时长校验照样过，界面上看不出来。"""

    n, seconds, tail, crossfade = 12, 1.0, 0.3, 0.25
    root = tmp_path / "output" / "story_video" / "TAIL"
    _black_white_clips(root / "02_video", n, seconds, audio_tail=tail)
    spec = {"id": "TAIL", "output_root": str(root), "subtitle": {"language": "zh"},
            "delivery": {"width": 180, "height": 320, "fps": 24, "crossfade_seconds": crossfade},
            "shots": [{"id": f"S{i + 1}", "dur": seconds, "lines": []} for i in range(n)]}
    manifest = pl.compose_post(spec, project_root=tmp_path)

    timeline = manifest["duration_seconds"]
    output = Path(manifest["output"])
    assert _video_stream_seconds(output) >= timeline - 1 / 24
    frames, hard = _hard_cut_seams(output, n, seconds + tail, crossfade)
    assert hard == [] and abs(frames - round(timeline * 24)) <= 1


@needs_ffmpeg
@needs_font
def test_assembled_clips_whose_audio_outlasts_the_picture_keep_their_picture(tmp_path):
    """装配版同一个毛病：usable_duration 也取容器时长，画面短出来的那一截同样让接缝落在画面 EOF 之后。
    这里 dur=5 > 源片画面 4s，音轨 4.6s → 每镜在时间线上占 4.6s，画面却只有 4s。"""

    from storyvideo.assembly import assemble_episode
    from storyvideo.spec import normalize_spec

    n, seconds, tail, crossfade = 12, 4.0, 0.6, 0.25
    raw = {"id": "TAIL", "output_root": "output/story_video/TAIL", "cast": {"a": {"appearance": "x"}},
           "audio_mode": "native_seedance_speech", "subtitle": {"language": "zh"},
           "delivery": {"width": 180, "height": 320, "fps": 24, "crossfade_seconds": crossfade},
           "shots": [{"id": f"S{i + 1}", "dur": 5, "cast": ["a"], "shot": "medium", "art": "a", "motion": "m", "lines": []}
                     for i in range(n)]}
    spec = normalize_spec(raw, project_root=tmp_path)
    _black_white_clips(Path(spec["output_root"]) / "02_video", n, seconds, audio_tail=tail)
    result = assemble_episode(spec, font_path=FONT, trim_head_fn=lambda *_: 0.0)

    timeline = n * (seconds + tail) - (n - 1) * crossfade
    assert _video_stream_seconds(Path(result.final_path)) >= timeline - 1 / 24
    frames, hard = _hard_cut_seams(Path(result.final_path), n, seconds + tail, crossfade)
    assert hard == [] and abs(frames - round(timeline * 24)) <= 1
