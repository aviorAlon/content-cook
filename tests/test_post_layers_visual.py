"""storyvideo.post_layers 的画面层：纯色字幕卡（layout=card）、片头插卡（after=__start__）、
文字角标（post_graphics.labels，Pillow 画 PNG 再 overlay）。

两类钉子：
* 逐字节不变：现有四种插卡版式的像素与改码前记下的基准一致；没有新字段的 spec，每一项的归一化视频链、接缝偏移、
  画面文字 / 字幕的时间、混音链都与改成分段流式合成之前一样（以前钉的是整张滤镜图逐字不变——分段之后图的结构
  必然变，改成钉这些事实，见 2026-09-22 发 tag 前的 POST-1）；
* 片头插卡让第一镜起点后移，字幕 / 配音 / 旁白 / 画面文字 / 配乐压低段的绝对时间一起跟着走。
滤镜图类测试把 ffmpeg / ffprobe 换成替身（不跑真实编码）；最后一条用真实 ffmpeg 端到端跑一遍。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from storyvideo import post_layers as pl

FFMPEG = shutil.which("ffmpeg")
FONT = Path("C:/Windows/Fonts/msyh.ttc")
needs_font = pytest.mark.skipif(not FONT.is_file(), reason="needs a CJK font (msyh.ttc)")


# ---------------------------------------------------------------- 替身：ffprobe 按文件名给时长，ffmpeg 只记命令


def _fake_media(monkeypatch, shot_durations: dict[str, float], *, audio: float = 1.0) -> list[list[str]]:
    holder: dict[str, float] = {}
    commands: list[list[str]] = []

    def probe(path, ffprobe="ffprobe"):
        p = Path(path)
        if p.stem in shot_durations and p.suffix == ".mp4" and p.parent.name == "02_video":
            return SimpleNamespace(duration_seconds=shot_durations[p.stem])
        if p.suffix == ".mp3":
            return SimpleNamespace(duration_seconds=audio)
        return SimpleNamespace(duration_seconds=holder["dest"])     # 成片：等于时间线长度

    def run(cmd, **kwargs):
        commands.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"")      # 成片是从临时文件原子替换过去的：替身也得留下一个文件

    real_build = pl.build_timeline

    def build(spec, **kwargs):
        items = real_build(spec, **kwargs)
        holder["dest"] = items[-1].start + items[-1].duration
        return items

    monkeypatch.setattr(pl, "probe_media", probe)
    monkeypatch.setattr(pl, "_run", run)
    monkeypatch.setattr(pl, "build_timeline", build)
    return commands


def _prepare_root(tmp_path: Path, shots=("S1", "S2")) -> Path:
    root = tmp_path / "EP"
    (root / "02_video").mkdir(parents=True)
    for sid in shots:
        (root / "02_video" / f"{sid}.mp4").write_bytes(b"x")
    return root


def _colors(im) -> set:
    """图里出现过的所有颜色（getdata 在 Pillow 14 要删，用 getcolors）。"""

    return {color for _count, color in im.getcolors(im.width * im.height)}


def _graph(root: Path) -> str:
    return (root / "manifests" / "post_filtergraph.txt").read_text(encoding="utf-8")


def _silent_tts(text: str, dest: Path, *_profile) -> None:
    dest.write_bytes(b"mp3")


# ---------------------------------------------------------------- 逐字节不变：现有版式与滤镜图

_LEGACY_INSERTS = {
    "plain": {"id": "P", "layout": "plain", "lines": ["系统通知", "今晚有雨", "记得带伞"]},
    "screen": {"id": "Q", "screen": "「系统通知」→ 今晚有雨｜记得带伞"},
    "notifications": {"id": "N", "layout": "notification", "app": "日历", "title": "通知", "time": "23:41",
                      "items": ["你有一条新的日程", "明早 9 点开会，别迟到"]},
    "sms": {"id": "S", "layout": "sms", "sender": "10086", "stamp": "今天 09:00", "message": "您的快递已到驿站，请及时取件。"},
    "shop": {"id": "H", "layout": "shop", "app": "商城", "title": "示例商品", "subtitle": "一句卖点", "price": "¥4.00",
             "sold": "已售 1 万+", "button": "立即购买", "sheet": {"title": "登录", "field": "手机号", "button": "登录"}},
}
# 老版式（加 card 版式之前的画法）用 Pillow 12.3 + msyh.ttc 渲染出的像素 sha256（换示例文字时先核对过旧文字的基准
# 仍逐像素一致，再用同一份代码重记）。换了 Pillow / FreeType 大版本才需要重记；平时这条挂了 = 有人动了老版式的画法。
_LEGACY_PIXELS = {
    "plain": "8636e4a73a9ae6972616aa3a4115fbda5d95751cd1f5ff7acfbcd15381c58525",
    "screen": "8636e4a73a9ae6972616aa3a4115fbda5d95751cd1f5ff7acfbcd15381c58525",
    "notifications": "1807d38886352edc1627ed536a69b6a113718778ab9dc23690dd45d39a7bd552",
    "sms": "f0b229c112e7a80511ce7bd65af7eeaf50f0ff5c2f1268a15718458df17d12b5",
    "shop": "5e3175b3a0531690cc1619020ad3545a0ece19e66307d98c4530a13b1504e066",
}


@needs_font
@pytest.mark.parametrize("name", sorted(_LEGACY_INSERTS))
def test_legacy_layouts_render_pixel_identical(tmp_path, name):
    from PIL import Image

    out = pl.render_ui_card(_LEGACY_INSERTS[name], tmp_path / f"{name}.png", font_path=str(FONT))
    with Image.open(out) as im:
        assert im.mode == "RGB" and im.size == pl.UI_SIZE
        assert hashlib.sha256(im.tobytes()).hexdigest() == _LEGACY_PIXELS[name]


def _legacy_spec(root: Path) -> dict:
    return {
        "id": "POST-G", "output_root": str(root),
        "delivery": {"width": 360, "height": 640, "fps": 12, "crossfade_seconds": 0.25},
        "subtitle": {"chinese_font_size": 20, "safe_bottom_percent": 7, "band_alpha": 0.2, "font_path": str(FONT)},
        "post_graphics": {
            "fonts": {"notebook": str(FONT), "ui": str(FONT), "subtitle": str(FONT)},
            "phone_ui_inserts": [{"id": "UI_1", "after": "S1", "dur": 2, "layout": "plain", "lines": ["系统通知", "今晚有雨"]}],
            "notebook_text": [{"shot": "S2", "text": "你好", "y": 0.5, "size": 40}],
        },
        "shots": [{"id": "S1", "dur": 3, "lines": []}, {"id": "S2", "dur": 3, "lines": [{"who": "x", "zh": "你怎么才来？"}]}],
    }


# 改码前每一路输入的视频链（分段之后逐项归一化，链的写法只多了一个 tpad：音轨比画面长的片要把画面定格补到
# 本项时长，不补的话接缝会落在画面 EOF 之后、整组画面丢掉；画面本来够长时补出来的帧全被后面的 trim 截掉）
_LEGACY_CHAIN = ("scale=360:640:force_original_aspect_ratio=decrease,pad=360:640:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=12,format=yuv420p,"
                 "tpad=stop_mode=clone:stop_duration={d},trim=duration={d},setpts=PTS-STARTPTS")
# 拼接那条命令：接缝偏移与以前链上的 offset 相同；链尾截到时间线长度 7.5s（中间片不多出恒定帧率补的那一帧）；
# 声音从同一批中间片的第二次打开（输入 3–5）里读（音画同一个输入时 ffmpeg 6.0 的 xfade 会硬切，见 assembly.join_inputs）
_LEGACY_JOIN = ("[0:v][1:v]xfade=transition=fade:duration=0.250:offset=2.750[vx1];[3:a][4:a]acrossfade=d=0.250:c1=tri:c2=tri[ax1];"
                "[vx1][2:v]xfade=transition=fade:duration=0.250:offset=4.500,trim=duration=7.500[vout];"
                "[ax1][5:a]acrossfade=d=0.250:c1=tri:c2=tri[aout]")


def _normalize_graphs(commands: list[list[str]]) -> list[str]:
    return [cmd[cmd.index("-filter_complex") + 1] for cmd in commands
            if "-filter_complex" in cmd and "xfade=" not in cmd[cmd.index("-filter_complex") + 1]]


def _join_graphs(commands: list[list[str]]) -> list[str]:
    return [cmd[cmd.index("-filter_complex") + 1] for cmd in commands
            if "-filter_complex" in cmd and "xfade=" in cmd[cmd.index("-filter_complex") + 1]]


def _enable(graph: str, needle: str) -> str:
    """滤镜图里包含 needle 的那条 drawtext 的显示区间。"""

    for filt in re.findall(r"drawtext=(?:[^,;']|'[^']*')+", graph):
        if needle in filt:
            return re.search(r"enable='(.+?)'", filt).group(1)
    raise AssertionError(f"no drawtext with {needle!r} in {graph}")


@needs_font
def test_filtergraph_without_new_fields_keeps_chains_seams_and_times(tmp_path, monkeypatch):
    commands = _fake_media(monkeypatch, {"S1": 3.0, "S2": 3.0})
    root = _prepare_root(tmp_path)
    manifest = pl.compose_post(_legacy_spec(root))
    # 2 镜 + 1 插卡各自归一化：镜一路输入，插卡一路 PNG + 一路静音；视频链与以前逐字相同，音轨多一个 apad
    # （声音比画面短的片补静音到这一项的时长，拼接时每段长度跟着时间线走，不会越拼声音越超前）
    assert _normalize_graphs(commands) == [
        "[0:v]" + _LEGACY_CHAIN.format(d="3.000") + "[v];[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,apad,atrim=duration=3.000,asetpts=PTS-STARTPTS[a]",
        "[0:v]" + _LEGACY_CHAIN.format(d="2.000") + "[v];[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,apad,atrim=duration=2.000,asetpts=PTS-STARTPTS[a]",
        "[0:v]" + _LEGACY_CHAIN.format(d="3.000") + "[v];[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,apad,atrim=duration=3.000,asetpts=PTS-STARTPTS[a]",
    ]
    assert [sum(1 for x in cmd if x == "-i") for cmd in commands[:3]] == [1, 2, 1]
    # 接缝的偏移 = 以前链上的 offset；最后一遍只有拼好的那一路视频输入
    assert _join_graphs(commands) == [_LEGACY_JOIN]
    assert sum(1 for x in commands[-1] if x == "-i") == 1 and commands[-1][commands[-1].index("-i") + 1].endswith("join_1_000.mov")
    graph = _graph(root)
    # 画面文字走 textfile + expansion=none（F3）：文字写进 manifests/subtitles/notebook.<条>.<行>.txt
    notebook_file = root / "manifests" / "subtitles" / "notebook.0.0.txt"
    assert notebook_file.read_text(encoding="utf-8") == "你好"
    assert graph.startswith("[0:v]drawtext=fontfile='C\\:/Windows/Fonts/msyh.ttc':"
                            f"textfile='{pl._filter_path(notebook_file)}':expansion=none:fontsize=40:fontcolor=0x1a1a1a@0.92:"
                            "x=(w-text_w)/2:y=320:enable='between(t,4.900,7.450)',")
    assert _enable(graph, "S2.zh.0.txt") == "between(t,4.800,7.250)"
    assert graph.endswith("[vout];\n[0:a]loudnorm=I=-16:TP=-1.5:LRA=9[aout]")
    assert manifest["labels"] == [] and manifest["labels_skipped"] == []
    # 卡图按时间线编号起名，不拿 id 当文件名（F1）
    assert manifest["ui_inserts"] == [str(root / "08_ui" / "ui_000.png")] and manifest["ui_inserts_skipped"] == []


# ---------------------------------------------------------------- 纯色字幕卡


@needs_font
def test_card_is_solid_background_with_centered_text_and_no_phone_chrome(tmp_path):
    from PIL import Image

    insert = {"id": "END", "after": "S2", "layout": "card", "lines": ["声音由 AI 合成", "", "夜行"]}
    out = pl.render_ui_card(insert, tmp_path / "END.png", font_path=str(FONT))
    with Image.open(out) as im:
        assert im.size == pl.UI_SIZE and im.mode == "RGB"
        # 默认黑底：上面 400px（plain 的状态栏 / 电量 / 卡片都在这里）一个非黑像素都没有
        assert im.crop((0, 0, 1080, 400)).getbbox() is None
        assert im.getpixel((0, 0)) == (0, 0, 0) and im.getpixel((1079, 1919)) == (0, 0, 0)
        box = im.convert("L").point(lambda v: 255 if v > 100 else 0).getbbox()
        assert box is not None
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        assert abs(cx - 540) < 12 and abs(cy - 960) < 40            # 行块整体居中
        assert (255, 255, 255) in _colors(im)                        # 默认白字


@needs_font
def test_card_honours_background_and_color_and_screen_text(tmp_path):
    from PIL import Image

    insert = {"id": "C", "layout": "card", "background": "#102030", "color": "#FFD84D", "screen": "第一行｜第二行"}
    out = pl.render_ui_card(insert, tmp_path / "C.png", font_path=str(FONT))
    with Image.open(out) as im:
        assert im.getpixel((5, 5)) == (16, 32, 48)
        assert (255, 216, 77) in _colors(im)
    assert pl.card_lines(insert) == ["第一行", "第二行"]
    assert pl.card_lines({"lines": ["A", None, " B "]}) == ["A", "", "B"]


def test_card_without_text_is_a_readable_error(tmp_path):
    with pytest.raises(pl.PostLayerError, match="字幕卡 END 没有文字"):
        pl.render_ui_card({"id": "END", "layout": "card", "lines": ["", ""]}, tmp_path / "x.png", font_path=str(FONT))


def test_card_push_filter_only_for_card_with_push():
    push = pl._card_push_filter({"layout": "card"}, 3.0, 24)
    # perspective 按小数坐标重采样（zoompan 的整像素裁切会让字边缘来回跳）；on 从 1 数起，第一帧 on-1=0 是原图
    k = "(1-1/(1+0.030*min(max(on-1,0)/71,1)))"
    assert push == (f"perspective=x0='W/2*{k}':y0='H/2*{k}':x1='W-W/2*{k}':y1='H/2*{k}':"
                    f"x2='W/2*{k}':y2='H-H/2*{k}':x3='W-W/2*{k}':y3='H-H/2*{k}':interpolation=cubic:eval=frame,")
    assert "zoompan" not in push
    assert pl._card_push_filter({"layout": "card", "motion": "push"}, 3.0, 24) == push
    assert pl._card_push_filter({"layout": "card", "motion": "none"}, 3.0, 24) == ""
    assert pl._card_push_filter({"layout": "plain"}, 3.0, 24) == ""
    assert pl._card_push_filter(None, 3.0, 24) == ""


@pytest.mark.skipif(not FFMPEG or not FONT.is_file(), reason="needs ffmpeg and a CJK font")
@pytest.mark.parametrize("width", [1080, 540])
def test_card_push_is_monotonic_frame_by_frame(tmp_path, monkeypatch, width):
    """真实 ffmpeg 跑 compose_post 给片头卡写出的那条视频链：字的外框逐帧只往外扩，左沿不往右回、右沿不往左回。
    换成 zoompan 的写法，1080 宽下 96 帧里有 9 帧左沿往回跳 1px（整像素裁切），静止的字幕卡上看着就是抖。"""

    from PIL import Image

    commands = _fake_media(monkeypatch, {"S1": 3.0})
    root = _prepare_root(tmp_path, shots=("S1",))
    height = width * 16 // 9
    spec = {
        "id": "POST-P", "output_root": str(root),
        "delivery": {"width": width, "height": height, "fps": 24, "crossfade_seconds": 0.25},
        "subtitle": {"font_path": str(FONT)},
        "post_graphics": {"fonts": {"notebook": str(FONT), "ui": str(FONT), "subtitle": str(FONT)},
                          "phone_ui_inserts": [{"id": "OPEN", "after": "__start__", "dur": 4, "layout": "card",
                                                "lines": ["声音由 AI 合成", "", "夜行"]}]},
        "shots": [{"id": "S1", "dur": 3, "lines": []}],
    }
    pl.compose_post(spec)
    cmd = commands[0]                                              # 第一条命令：片头卡的归一化（推镜就在这一条里做）
    chain = cmd[cmd.index("-filter_complex") + 1].split(";")[0]
    assert chain.startswith("[0:v]perspective=") and chain.endswith("[v]")
    card_input = cmd[cmd.index("-loop"):cmd.index("-i") + 2]      # 第 0 路输入就是片头卡：-loop 1 -t 4 -framerate 24 -i OPEN.png
    raw = subprocess.run([FFMPEG, "-loglevel", "error", *card_input, "-filter_complex", chain, "-map", "[v]",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    frame = width * height
    boxes = [Image.frombytes("L", (width, height), raw[k * frame:(k + 1) * frame]).point(lambda v: 255 if v > 128 else 0).getbbox()
             for k in range(len(raw) // frame)]
    assert len(boxes) == 96 and all(boxes)
    lefts, rights = [b[0] for b in boxes], [b[2] for b in boxes]
    assert all(b <= a for a, b in zip(lefts, lefts[1:])), lefts
    assert all(b >= a for a, b in zip(rights, rights[1:])), rights
    assert 1.015 < (rights[-1] - lefts[-1]) / (rights[0] - lefts[0]) < 1.04      # 整段推 ≈ 1.03


# ---------------------------------------------------------------- 片头插卡：时间线后移


def test_start_insert_goes_before_first_shot_and_chains(tmp_path):
    spec = {
        "delivery": {"crossfade_seconds": 0.25},
        "post_graphics": {"phone_ui_inserts": [
            {"id": "OPEN", "after": "__start__", "dur": 3, "layout": "card", "lines": ["x"]},
            {"id": "OPEN2", "after": "OPEN", "dur": 2, "layout": "card", "lines": ["y"]},
            {"id": "UI_1", "after": "S1", "dur": 2},
        ]},
        "shots": [{"id": "S1"}, {"id": "S2"}],
    }
    items = pl.build_timeline(spec, video_dir=tmp_path, ui_dir=tmp_path, durations={"S1": 3.0, "S2": 3.0})
    assert [x.item_id for x in items] == ["OPEN", "OPEN2", "S1", "UI_1", "S2"]
    assert [round(x.start, 2) for x in items] == [0.0, 2.75, 4.5, 7.25, 9.0]
    assert items[0].kind == "ui" and items[0].source == tmp_path / "ui_000.png"   # 按时间线编号（F1）
    assert pl.absolute_time(items, "S1", 0.5) == pytest.approx(5.0)
    # 没有片头卡：第一镜照旧从 0 开始
    spec["post_graphics"]["phone_ui_inserts"] = spec["post_graphics"]["phone_ui_inserts"][2:]
    items = pl.build_timeline(spec, video_dir=tmp_path, ui_dir=tmp_path, durations={"S1": 3.0, "S2": 3.0})
    assert [(x.item_id, round(x.start, 2)) for x in items] == [("S1", 0.0), ("UI_1", 2.75), ("S2", 4.5)]


def _timed_spec(root: Path, *, start_card: bool) -> dict:
    inserts = [{"id": "OPEN", "after": "__start__", "dur": 4, "layout": "card", "lines": ["本片由 AI 生成"]}] if start_card else []
    # 配乐只认数据目录 content/ 下的相对路径（PB-5）；调用方把 project_root 给成 root 往上两级
    music_file = "content/assets/POST-S/bgm.mp3"
    (root.parents[1] / music_file).parent.mkdir(parents=True, exist_ok=True)
    (root.parents[1] / music_file).write_bytes(b"mp3")
    return {
        "id": "POST-S", "output_root": str(root),
        "delivery": {"width": 360, "height": 640, "fps": 12, "crossfade_seconds": 0.25},
        "subtitle": {"chinese_font_size": 20, "font_path": str(FONT)},
        "cast": {"x": {"voice_tts": {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural"}}},
        "voice_over": {"lines": [{"shot": "S1", "at": 0.5, "zh": "凌晨一点。"}]},
        "post_graphics": {
            "fonts": {"notebook": str(FONT), "ui": str(FONT), "subtitle": str(FONT)},
            "dubbing": {"mode": "all"},
            "phone_ui_inserts": inserts,
            "notebook_text": [{"shot": "S2", "text": "你好"}],
            "music": [{"file": music_file, "start": 0, "duck": 0.3}],
        },
        "shots": [{"id": "S1", "dur": 3, "lines": []}, {"id": "S2", "dur": 3, "lines": [{"who": "x", "zh": "你怎么才来？"}]}],
    }


def _times(graph: str) -> dict[str, list[float]]:
    """把滤镜图里跟绝对时间有关的数抠出来：enable 区间、adelay、压低段（配音镜压原声、配乐让路）。"""

    return {
        "enable": [float(x) for pair in re.findall(r"enable='between\(t,([\d.]+),([\d.]+)\)'", graph) for x in pair],
        "adelay": [float(x) / 1000 for x in re.findall(r"adelay=(\d+)\|", graph)],
        "duck": [float(x) for pair in re.findall(r"between\(t,([\d.]+),([\d.]+)\)(?:\+|,0\.\d+,1\)':eval)", graph) for x in pair],
    }


@needs_font
def test_start_card_shifts_every_absolute_time(tmp_path, monkeypatch):
    """同一份 spec 加一张 4 秒片头卡：第一镜后移 4 - 0.25 = 3.75 秒，字幕、画面文字、旁白、配音、配乐压低段全都跟着移；
    配乐的 start 是时间线绝对秒（从第几秒开始放），不跟着移。"""

    commands = _fake_media(monkeypatch, {"S1": 3.0, "S2": 3.0})
    base_root = _prepare_root(tmp_path / "a")
    shifted_root = _prepare_root(tmp_path / "b")
    base = pl.compose_post(_timed_spec(base_root, start_card=False), tts=_silent_tts, dub_tts=_silent_tts, project_root=tmp_path)
    base_commands = len(commands)
    shifted = pl.compose_post(_timed_spec(shifted_root, start_card=True), tts=_silent_tts, dub_tts=_silent_tts, project_root=tmp_path)
    assert [x["id"] for x in shifted["timeline"]] == ["OPEN", "S1", "S2"]
    offset = 4 - 0.25
    assert shifted["timeline"][1]["start"] == pytest.approx(offset)
    assert shifted["expected_duration_seconds"] == pytest.approx(base["expected_duration_seconds"] + offset)

    a, b = _times(_graph(base_root)), _times(_graph(shifted_root))
    assert a["enable"] and len(a["enable"]) == len(b["enable"])               # 画面文字 + 台词字幕
    assert b["enable"] == pytest.approx([t + offset for t in a["enable"]], abs=0.002)
    assert a["duck"] and b["duck"] == pytest.approx([t + offset for t in a["duck"]], abs=0.002)   # 配音镜压原声 + 配乐压低段
    # adelay：旁白、配音按绝对时间后移；配乐 start=0 不动（每份图里最后一个 adelay 是配乐）
    assert b["adelay"][:-1] == pytest.approx([t + offset for t in a["adelay"][:-1]], abs=0.002)
    assert a["adelay"][-1] == b["adelay"][-1] == 0.0
    assert shifted["vo"][0]["at"] == base["vo"][0]["at"] == 0.5
    assert [d["abs_start"] for d in shifted["dub"]] == pytest.approx([d["abs_start"] + offset for d in base["dub"]], abs=0.002)
    # 片头卡是 card + 默认 push：只有它的归一化命令带推镜（perspective），而且是第一条（时间线第一项）
    with_card = [" ".join(cmd) for cmd in commands[base_commands:]]
    assert sum("perspective=" in cmd for cmd in with_card) == 1 and "perspective=" in with_card[0]
    assert not any("perspective=" in " ".join(cmd) for cmd in commands[:base_commands])
    assert "perspective" not in _graph(shifted_root) and "perspective" not in _graph(base_root)


# ---------------------------------------------------------------- 文字角标


DOUYIN = [{"label": "顶部状态与标题栏", "x": 0.0, "y": 0.0, "w": 1.0, "h": 0.09},
          {"label": "右侧互动栏", "x": 0.84, "y": 0.42, "w": 0.16, "h": 0.36},
          {"label": "底部文案与进度条", "x": 0.0, "y": 0.78, "w": 1.0, "h": 0.22}]


def test_label_position_avoids_platform_overlays():
    frame = (1080, 1920)
    # 抖音：右上角落在顶栏（0–172.8px）之下、右侧互动栏（806px 起）之上
    x, y = pl.label_xy("top_right", (120, 50), frame, DOUYIN)
    assert x == 1080 - 48 - 120
    assert 172.8 < y and y + 50 < 0.42 * 1920
    assert y == 173 + 16                                               # 顶栏下沿 + 16px
    # 没有遮挡区：上边距 48
    assert pl.label_xy("top_right", (120, 50), frame, []) == (912, 48)
    assert pl.label_xy("top_left", (120, 50), frame, [])[0] == 48
    assert pl.label_xy("top_center", (120, 50), frame, [])[0] == (1080 - 120) // 2
    # 底部居中：落在底栏（1497.6px 起）之上
    x, y = pl.label_xy("bottom_center", (200, 60), frame, DOUYIN)
    assert x == 440 and y + 60 <= 1497.6 - 16 + 1
    assert pl.label_xy("bottom_center", (200, 60), frame, []) == (440, 1920 - 48 - 60)
    # 居中：不避让（片头居中标识是有意压在正中的）
    assert pl.label_xy("center", (200, 60), frame, [{"x": 0, "y": 0, "w": 1, "h": 1}]) == (440, 930)
    # 遮挡区铺满整屏也不会把角标推出画外
    x, y = pl.label_xy("top_right", (120, 50), frame, [{"x": 0, "y": 0, "w": 1, "h": 1}])
    assert 0 <= y <= 1920 - 50
    # 尺寸按交付宽度缩放：540 宽时边距 24
    assert pl.label_xy("top_left", (60, 25), (540, 960), []) == (24, 24)


@needs_font
def test_render_label_is_transparent_png_with_box_and_opacity(tmp_path):
    from PIL import Image

    path, w, h = pl.render_label({"text": "AI 生成", "position": "top_right"}, tmp_path / "a.png", font_path=str(FONT))
    with Image.open(path) as im:
        assert im.mode == "RGBA" and im.size == (w, h)
        alpha = im.getchannel("A")
        assert alpha.getextrema()[1] == round(255 * 0.9)             # 默认 opacity 0.9：最不透明处 = 229
        assert alpha.getpixel((0, h // 2)) > 0                        # 默认带半透明底：左边缘中间有底色
        assert alpha.getpixel((0, 0)) == 0                            # 圆角外是透明的
    path, w2, h2 = pl.render_label({"text": "AI 生成", "box": False, "opacity": 1, "color": "#FFD84D", "size": 68},
                                   tmp_path / "b.png", font_path=str(FONT))
    with Image.open(path) as im:
        assert im.getchannel("A").getextrema()[1] == 255
        assert im.getchannel("A").getpixel((0, h2 // 2)) == 0        # 不要底：边缘透明
        assert (255, 216, 77, 255) in _colors(im)
    assert h2 > h                                                     # 字号 68 比默认 34 高


@needs_font
def test_label_layers_resolve_times_and_skip_late_labels(tmp_path):
    spec = {"delivery": {"overlay_preset": "douyin"}, "post_graphics": {"labels": [
        {"text": "AI 生成", "position": "top_right"},
        {"text": "夜行", "position": "center", "start": 0.5, "end": 99, "size": 90},
        {"text": "太晚", "position": "top_left", "start": 30},
        {"text": " ", "position": "top_left"},
    ]}}
    rows, skipped = pl.label_layers(spec, tmp_path, width=1080, height=1920, timeline_end=12.0, font_path=str(FONT))
    assert [(r["text"], r["start"], r["end"]) for r in rows] == [("AI 生成", 0.0, 12.0), ("夜行", 0.5, 12.0)]
    assert skipped == ["太晚"]
    assert all(Path(r["path"]).is_file() for r in rows)
    top_right = rows[0]
    assert top_right["y"] >= int(0.09 * 1920) and top_right["x"] + top_right["width"] == 1080 - 48


@needs_font
def test_compose_overlays_labels_on_top_of_text(tmp_path, monkeypatch):
    commands = _fake_media(monkeypatch, {"S1": 3.0, "S2": 3.0})
    plain_root = _prepare_root(tmp_path / "plain")
    pl.compose_post(_legacy_spec(plain_root))
    plain_graph = _graph(plain_root)
    root = _prepare_root(tmp_path)
    spec = _legacy_spec(root)
    spec["delivery"]["overlay_preset"] = "douyin"
    spec["post_graphics"]["labels"] = [{"text": "AI 生成", "position": "top_right"},
                                       {"text": "夜行", "position": "center", "start": 0, "end": 2.5, "size": 90}]
    manifest = pl.compose_post(spec)
    graph = _graph(root)
    # 文字链先出 [vtxt]，两条角标依次叠，最后一条出 [vout]；角标不走 drawtext
    assert "[vtxt];" in graph and "drawtext=fontfile" in graph and "AI 生成" not in graph
    pattern = re.compile(r"(\[\w+\])\[(\d+):v\]overlay=x=(\d+):y=(\d+):enable='between\(t,([\d.]+),([\d.]+)\)'(\[\w+\])")
    overlays = [m.groups() for m in map(pattern.fullmatch, graph.split(";\n")) if m]
    assert [o[0] for o in overlays] == ["[vtxt]", "[vlb0]"] and overlays[-1][-1] == "[vout]"
    assert [(o[4], o[5]) for o in overlays] == [("0.000", "7.500"), ("0.000", "2.500")]
    # 角标 PNG 作为单帧输入挂在最后一遍命令行的最后（拼好的视频是第 0 路）
    cmd = commands[-1]
    inputs = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-i"]
    assert [Path(p).name for p in inputs[-2:]] == ["label_01.png", "label_02.png"] and len(inputs) == 3
    assert [int(o[1]) for o in overlays] == [len(inputs) - 2, len(inputs) - 1]
    assert [x["text"] for x in manifest["labels"]] == ["AI 生成", "夜行"]
    assert [(x["size"], x["shrunk"]) for x in manifest["labels"]] == [(34, False), (90, False)]
    # 除了 [vout] 改名为 [vtxt]，文字链与音频链与没有角标时逐字一致（字幕文本文件在各自的输出目录下，路径换算成同一个再比）
    lines = graph.split(";\n")
    plain_lines = plain_graph.replace(pl._filter_path(plain_root), pl._filter_path(root)).split(";\n")
    assert lines[0] == plain_lines[0].replace("[vout]", "[vtxt]") and lines[-1] == plain_lines[-1]


def test_label_xy_keeps_x_inside_frame():
    """比画面还宽的角标：右上角的 x 本来会算成负数（从左边出画），居中的两头都出画。横向也夹进画面。"""

    frame = (1080, 1920)
    for position in pl.LABEL_POSITIONS:
        x, y = pl.label_xy(position, (1500, 60), frame, DOUYIN)
        assert x == 0 and 0 <= y <= 1920 - 60, position
    # 放得下画面、放不下页边距：贴着画面边缘，不出画
    assert pl.label_xy("top_right", (1060, 60), frame, [])[0] == 0
    assert pl.label_xy("top_left", (1060, 60), frame, [])[0] == 20


@needs_font
@pytest.mark.parametrize("width", [1080, 540])
def test_wide_label_is_shrunk_to_fit_inside_margins(tmp_path, width):
    """接口层放行 24 个字、字号到 200：一行能有一千五百像素宽。合成时把字号缩到放进页边距以内，manifest 记 shrunk。"""

    spec = {"delivery": {"overlay_preset": "douyin"}, "post_graphics": {"labels": [
        {"text": "字" * 24, "position": "top_right", "size": 60},
        {"text": "夜行夜行夜行", "position": "center", "size": 200},
        {"text": "AI 生成", "position": "top_right"},
    ]}}
    rows, _ = pl.label_layers(spec, tmp_path, width=width, height=width * 16 // 9, timeline_end=10.0, font_path=str(FONT))
    margin = round(48 * width / 1080)
    for row in rows:
        assert margin <= row["x"] and row["x"] + row["width"] <= width - margin, row
    assert [r["shrunk"] for r in rows] == [True, True, False]
    assert rows[0]["size"] < 60 and rows[1]["size"] < 200 and rows[2]["size"] == 34
    assert rows[0]["x"] + rows[0]["width"] == width - margin                 # 右上角仍贴右边距
    assert abs(rows[1]["x"] + rows[1]["width"] / 2 - width / 2) <= 1          # 居中的仍在正中
    # 缩得不过头：再大 1px 就放不下了
    grown = pl.fit_label_size({**spec["post_graphics"]["labels"][0], "size": rows[0]["size"] + 1080 / width},
                              max_width=width - 2 * margin, max_height=10_000, font_path=str(FONT), scale=width / 1080)
    assert grown < rows[0]["size"] + 1080 / width


def test_label_text_normalizes_lines():
    assert pl.label_text(" 夜行 \r\n NIGHT WALK \n\n") == "夜行\nNIGHT WALK"
    assert pl.label_text("\n夜行\n\nNIGHT WALK") == "夜行\n\nNIGHT WALK"          # 中间的空行留着当行距
    assert pl.label_text(None) == "" and pl.label_text(" \n ") == ""


@needs_font
def test_multiline_label_keeps_every_line(tmp_path):
    """两行的片头标识：画布要按整块量（font.getbbox 只量一行，第二行会画到画布外被裁掉），多行按贴的那条边对齐。"""

    from PIL import Image

    cfg = {"box": False, "opacity": 1, "size": 90, "position": "center"}
    _, w_zh, h_one = pl.render_label({**cfg, "text": "夜行"}, tmp_path / "zh.png", font_path=str(FONT))
    _, w_en, _ = pl.render_label({**cfg, "text": "NIGHT WALK"}, tmp_path / "en.png", font_path=str(FONT))
    path, w, h = pl.render_label({**cfg, "text": "夜行\nNIGHT WALK"}, tmp_path / "two.png", font_path=str(FONT))
    assert h > h_one * 1.8
    assert abs(w - max(w_zh, w_en)) <= 2                                     # 宽 = 最宽的那一行，不是整串当一行
    with Image.open(path) as im:
        alpha = im.getchannel("A")
        ink = alpha.getbbox()
        assert ink[1] <= 2 and ink[3] >= h - 2                                # 上下两行都在画布里
        top = alpha.crop((0, 0, w, h // 3)).getbbox()
        assert abs((top[0] + top[2]) / 2 - w / 2) <= 3                         # 居中：短的那行在正中
    path, w, h = pl.render_label({**cfg, "position": "top_right", "text": "AI\n生成内容"}, tmp_path / "r.png", font_path=str(FONT))
    with Image.open(path) as im:
        alpha = im.getchannel("A")
        top, bottom = alpha.crop((0, 0, w, h // 3)).getbbox(), alpha.crop((0, h - h // 3, w, h)).getbbox()
        # 右上角：右对齐（按字的步进宽对齐，「I」和汉字的字形右侧留白不同，墨迹右沿差几像素）
        assert abs(top[2] - bottom[2]) <= 12 and top[0] > bottom[0] + 100
    # 单行不受影响：与多行支持加进来之前一样按 getbbox 量
    from PIL import ImageFont
    font = ImageFont.truetype(str(FONT), 90)
    left, top_, right, bottom_ = font.getbbox("夜行", stroke_width=5)
    assert (w_zh, h_one) == (right - left, bottom_ - top_)


# ---------------------------------------------------------------- 真实 ffmpeg 端到端


@pytest.mark.skipif(not FFMPEG or not FONT.is_file(), reason="needs ffmpeg and a CJK font")
def test_compose_with_start_card_and_labels_end_to_end(tmp_path):
    from PIL import Image

    root = tmp_path / "EP"
    (root / "02_video").mkdir(parents=True)
    for sid, color in (("S1", "blue"), ("S2", "red")):
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=360x640:r=12:d=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(root / "02_video" / f"{sid}.mp4")], check=True)
    spec = {
        "id": "POST-E", "output_root": str(root),
        "delivery": {"width": 360, "height": 640, "fps": 12, "crossfade_seconds": 0.25, "overlay_preset": "douyin"},
        "subtitle": {"chinese_font_size": 20, "font_path": str(FONT)},
        "post_graphics": {
            "fonts": {"notebook": str(FONT), "ui": str(FONT), "subtitle": str(FONT)},
            "phone_ui_inserts": [{"id": "OPEN", "after": "__start__", "dur": 2, "layout": "card", "lines": ["本片由 AI 生成"]}],
            "labels": [{"text": "AI 生成", "position": "top_right", "opacity": 1, "size": 60}],
        },
        "shots": [{"id": "S1", "dur": 3, "lines": []}, {"id": "S2", "dur": 3, "lines": []}],
    }
    manifest = pl.compose_post(spec)
    assert [x["id"] for x in manifest["timeline"]] == ["OPEN", "S1", "S2"]
    assert manifest["duration_seconds"] == pytest.approx(2 + 3 + 3 - 0.5, abs=0.6)
    frame = tmp_path / "f.png"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", "3.0", "-i", manifest["output"], "-frames:v", "1", str(frame)], check=True)
    label = manifest["labels"][0]
    with Image.open(frame) as im:
        rgb = im.convert("RGB")
        # 3.0s 已经是蓝色的第一镜；角标区域里有接近白的字
        assert rgb.getpixel((180, 320))[2] > 150
        region = rgb.crop((label["x"], label["y"], label["x"] + 60, label["y"] + 30))
        assert any(min(px) > 200 for px in _colors(region))
