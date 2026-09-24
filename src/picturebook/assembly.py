"""绘本装配（契约 §5）：先混全书的声音，再逐页出画面片，最后流复制拼成成片。

画面：每页一条 Ken Burns（``scale(eval=frame)+crop``，方向按 ``kb.dir``，幅度随时长 1.05–1.12；用
scale+crop 做推拉，不用 zoompan），``fps=24`` / ``yuv420p``；翻页 ``xfade=slideleft:0.4``，
``flip: cut`` 硬切。字幕：每句两层 drawtext（念到这句时用 ``subtitle.highlight`` 着色，其余时段白色），中文在上、英文在下，
起止取自时间轴。混音：静音底轨定总长，叠上逐段朗读（按绝对时间 adelay）、翻页音效（xfade 开始后 0.2 s）和 BGM（gain、末尾淡出），
再整体 ``loudnorm=I=-14:TP=-2`` → aac → faststart。主版 9:16；``delivery.landscape_variant`` 为真再出 16:9。

为什么分段：要是全书每一页都作为同一条 ffmpeg 命令的一路输入、串成一条 xfade 链，ffmpeg 会同时解码全部页，峰值内存随页数
线性涨（1080x1920 下每多 10 页大约多 1.5 GB）；「本页硬切、下一页翻页」的书也整本出不来（concat 滤镜的输出时基是
1/1000000，接 xfade 时对不上）。所以：

1. 声音一条命令先混好（两个画幅共用；朗读段多于 ``MIX_GROUP`` 段先分组预混）。放在画面之前，朗读文件坏了几秒内就报。
2. 画面按 24 fps 的绝对帧网格切成每页一段（``frame_plan``）：每页一条命令（只读这一页、翻页时再加下一页的插画），
   直接按交付口径编码；翻页的那几帧归前一页的命令画（两页的 Ken Burns + 字幕各自按局部时间走，xfade slideleft）。
   每一帧只画一次、只编码一次，内存与页数无关，几页并行跑（``PAGE_WORKERS``）。
3. concat 演示器把各页的片流复制接起来、配上声音，写成片。页的起点、转场的起止都取在帧网格上，页与页首尾相接，
   硬切再多也不累积误差（每页各自按整帧向上取、一页页往后推的话，硬切多的书到片尾画面会比朗读晚零点几秒）。

混音的滤镜图写到 ``manifests/book_filtergraph.txt``（``-filter_complex_script``），逐页的滤镜图写到
``manifests/book_pages[_16x9].txt``，时间轴写到 ``manifests/timeline.json``。成片先写临时文件，核过之后才原子替换。
"""

from __future__ import annotations

import math
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Sequence

from storyvideo import subtitles
from storyvideo.assembly import AssemblyError, _filter_path, _run, _sync_file, _temporary_destination, _text_file, _wrap_text
from storyvideo.assembly import replace_final
from storyvideo.segmented import adelay_samples, concat_entry, premix_audio
from storyvideo.state import atomic_write_json

from . import narration as N
from . import pages as PG
from .spec import audio_dir, edit_dir, manifests_dir, resolve_asset
from .timeline import build_timeline

FPS = 24
PORTRAIT = (1080, 1920)
LANDSCAPE = (1920, 1080)
KB_MIN, KB_MAX = 1.05, 1.12
KB_SHORT, KB_LONG = 4.0, 20.0          # 页时长 ≤4 s 用最小幅度，≥20 s 用最大
SUBTITLE_TOP_RATIO = 0.72              # 字幕区从画面高度的 72% 起
BAND_ALPHA = 0.22
FLIP_GAIN = 0.8
# 没给翻页音效时现合成一声：一小段棕噪声，削掉低频闷响和高频嘶声，起得快、收得慢，听着像纸页扫过
FLIP_SOURCE = "anoisesrc=color=brown:duration=0.35:amplitude=0.6"
FLIP_SHAPE = "highpass=f=500,lowpass=f=4000,afade=t=in:st=0:d=0.02,afade=t=out:st=0.05:d=0.3,volume=0.6"
BGM_FADE_IN = 1.0
DEFAULT_TIMEOUT = 20 * 60
# 逐页的命令同时跑几页、每页的编码线程上限（见 _run_pages）。Ken Burns 的逐帧缩放是单线程的，一页一页排队时别的核就闲着，
# 几页并行能把核用满；每条命令约 0.5 GB。x264 线程封顶 4 条（默认按核数开，核数多的机器一条就多占几百 MB、速度不变）。
PAGE_WORKERS = max(1, min(4, (os.cpu_count() or 1) // 2))
PAGE_ENCODER_THREADS = 4
# 逐页的片直接按交付口径编码：拼接只做流复制，每一帧只编码一次
_VIDEO_ENCODE = ("-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p")
_AUDIO_ENCODE = ("-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2")
# 翻页那条命令两路末尾各多补几帧、出片时按帧数截：ffmpeg 6.0 的 xfade 在第一路报 EOF 时会把还没溶完的转场判成结束（见
# storyvideo.assembly.join_inputs），两路都比要用的多几帧，EOF 就只会出现在要用的帧之后
_SPARE_FRAMES = 3


def final_path(spec: Mapping[str, Any]) -> Path:
    return edit_dir(spec) / f"{spec.get('id')}_book.mp4"


def landscape_path(spec: Mapping[str, Any]) -> Path:
    return edit_dir(spec) / f"{spec.get('id')}_book_16x9.mp4"


def timeline_path(spec: Mapping[str, Any]) -> Path:
    return manifests_dir(spec) / "timeline.json"


def _color(value: Any, default: str = "#FFD27A") -> str:
    return subtitles.normalize_color(value, default)


def kb_scale(page: Mapping[str, Any], duration: float) -> tuple[float, float]:
    """kb.scale 显式给了就用；否则按时长在 1.05–1.12 之间取幅度（push/left/right/up/down 放大，pull 缩小）。"""

    kb = page.get("kb") or {}
    explicit = kb.get("scale")
    if explicit and len(explicit) == 2 and float(explicit[0]) != float(explicit[1]):
        return float(explicit[0]), float(explicit[1])
    ratio = min(1.0, max(0.0, (duration - KB_SHORT) / (KB_LONG - KB_SHORT)))
    amplitude = round(KB_MIN + (KB_MAX - KB_MIN) * ratio, 3)
    if str(kb.get("dir") or "push") == "pull":
        return amplitude, 1.0
    return 1.0, amplitude


def _image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as img:
        return int(img.width), int(img.height)


# 插画真实格式 → ffmpeg 解码器。强制 image2（为了关掉序号模板）以后 ffmpeg 只按扩展名挑解码器，.png 一律走 png 解码器；
# 插画却一律按 .png 落盘（下载结果原样写字节），Seedream 等出图接口给的其实是 JPEG、有的是 WebP——png 解码器报 Invalid PNG
# signature，-loop 1 让它一直重试，装配卡到超时才失败。所以按文件内容指定解码器
_STILL_DECODERS = {"PNG": "png", "JPEG": "mjpeg", "MPO": "mjpeg", "WEBP": "webp", "GIF": "gif", "BMP": "bmp", "TIFF": "tiff"}


def _still_input_options(path: Path) -> list[str]:
    """插画那一路 ``-i`` 之前的输入选项：``-f image2 -pattern_type none``（数据目录路径里的 %d / %03d 不当序号模板，F6）
    加上按真实内容选的 ``-c:v``。Pillow 认不出的格式什么都不加，交给 ffmpeg 自己按内容探测（改之前的行为）。"""

    from PIL import Image

    try:
        with Image.open(path) as img:
            codec = _STILL_DECODERS.get(str(img.format or "").upper())
    except Exception:  # noqa: BLE001 - 认不出就退回 ffmpeg 自己探测，读不了的图后面 _image_size 会报
        return []
    return ["-f", "image2", "-pattern_type", "none", "-c:v", codec] if codec else []


def ken_burns_filter(direction: str, scale: tuple[float, float], duration: float, image_size: tuple[int, int],
                     width: int, height: int) -> str:
    """一页的 scale+crop 表达式：先算出正好盖住输出画幅的底尺寸，再按 t 缩放、按方向移动裁切窗。"""

    iw, ih = image_size
    cover = max(width / iw, height / ih)
    base_w, base_h = iw * cover, ih * cover
    s0, s1 = scale
    zoom = f"({s0:.4f}+({s1 - s0:.4f})*t/{duration:.3f})"
    scale_expr = f"scale=w='trunc({base_w:.3f}*{zoom}/2)*2':h='trunc({base_h:.3f}*{zoom}/2)*2':eval=frame"
    progress = f"(t/{duration:.3f})"
    centre_x, centre_y = f"(iw-{width})/2", f"(ih-{height})/2"
    x, y = centre_x, centre_y
    if direction == "left":
        x = f"(iw-{width})*(1-{progress})"
    elif direction == "right":
        x = f"(iw-{width})*{progress}"
    elif direction == "up":
        y = f"(ih-{height})*(1-{progress})"
    elif direction == "down":
        y = f"(ih-{height})*{progress}"
    return f"{scale_expr},crop={width}:{height}:x='{x}':y='{y}'"


def _enable(windows: list[tuple[float, float]]) -> str:
    parts = [f"between(t,{a:.3f},{b:.3f})" for a, b in windows if b - a > 0.02]
    return "enable='" + "+".join(parts) + "'" if parts else ""


def subtitle_units(lang: str, size_px: int, width: int) -> int:
    """一行能放的「视觉单位」（CJK 一字 2 单位、拉丁一字 1 单位），按字号与画幅宽算——
    中文按固定 19 字算的话，96 号字在 1080 宽上只放得下 10 个字，整行会溢出画面。"""

    usable = width * 0.92
    if lang == "zh":
        return max(8, int(usable / max(1.0, float(size_px))) * 2)
    return max(12, min(40, int(round(usable / max(1.0, size_px * 0.55)))))


def subtitle_filters(page: Mapping[str, Any], *, text_dir: Path, font: str, width: int, height: int, zh_size: int, en_size: int,
                     highlight: str, page_duration: float) -> list[str]:
    """一页的字幕层：底色带 + 每段两层 drawtext（常白 / 高亮）。字号按 1920 高基准缩放。"""

    scale = height / PORTRAIT[1]
    zh_px = max(20, int(round(zh_size * scale)))
    en_px = max(16, int(round(en_size * scale)))
    gap = max(6, int(round(20 * scale)))
    border = max(2, int(round(5 * scale)))
    zh_y = int(round(height * SUBTITLE_TOP_RATIO))
    en_y = zh_y + int(zh_px * 1.25) * 2 + gap
    band_h = height - zh_y + 16
    filters: list[str] = []
    segments = page.get("segments") or []
    if not segments:
        return filters
    filters.append(f"drawbox=x=0:y={zh_y - 16}:w={width}:h={band_h}:color=black@{BAND_ALPHA:.3f}:t=fill")
    font_expr = _filter_path(Path(font))
    for seg in segments:
        lang = seg["lang"]
        size = zh_px if lang == "zh" else en_px
        wrapped, _lines = _wrap_text(str(seg["text"]), maximum_units=subtitle_units(lang, size, width))
        path = _filter_path(_text_file(text_dir, f"{page['id']}_{lang}_{seg['index'] + 1}.txt", wrapped))
        y = zh_y if lang == "zh" else en_y
        # expansion=none：朗读文字按原样画（默认展开会让含 % 的一句整条不画、\ 被吞、%{pts} 变时间戳，F2）
        base = (f"drawtext=fontfile='{font_expr}':textfile='{path}':expansion=none:fontsize={size}:borderw={border}:bordercolor=0x111111@0.9:"
                f"shadowx=0:shadowy=2:shadowcolor=black@0.6:x=(w-text_w)/2:y={y}:line_spacing={max(4, int(round(8 * scale)))}")
        ls, le = float(seg["local_start"]), float(seg["local_end"])
        ds, de = float(seg.get("display_start", 0.0)), float(seg.get("display_end", page_duration))
        white = _enable([(ds, ls), (le, de)])
        if white:
            filters.append(f"{base}:fontcolor=0xFFFFFF:{white}")
        filters.append(f"{base}:fontcolor={highlight}:{_enable([(ls, le)])}")
    return filters


def _ensure_flip(spec: Mapping[str, Any], *, ffmpeg: str, timeout: float) -> Path:
    configured = resolve_asset(spec, spec.get("page_turn_sfx"))
    if configured:
        return configured
    target = audio_dir(spec) / "flip.wav"
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    # 先写临时文件再换名：上面按「文件在不在」复用，半路失败留下的半截 flip.wav 会被以后每一次装配拿去用
    temporary = _temporary_destination(target)
    try:
        _run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", FLIP_SOURCE, "-af", FLIP_SHAPE, "-ar", "48000", str(temporary)],
             purpose="synthesize page-flip sfx", timeout_seconds=timeout)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _run_pages(jobs: list[tuple[list[str], str]], *, timeout: float) -> None:
    """逐页的命令几条并行跑（``PAGE_WORKERS``）。一页失败：还没开始的不再跑，已经在跑的跑完，报第一个错。"""

    pool = ThreadPoolExecutor(max_workers=PAGE_WORKERS)
    try:
        futures = [pool.submit(_run, cmd, purpose=purpose, timeout_seconds=timeout) for cmd, purpose in jobs]
        for future in futures:
            future.result()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def _frame(seconds: float) -> int:
    """时间轴上的秒 → 24 fps 帧网格上最近的那一帧。"""

    return int(math.floor(float(seconds) * FPS + 0.5))


def frame_plan(pages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """每页那条命令在绝对帧网格上负责的帧 ``[first, end)``，一页接一页正好铺满 ``[0, 全书帧数)``。

    * 页的局部时间从 ``_frame(start)`` 那一帧起算（局部第 k 帧 = 这一页的第 k/24 秒，Ken Burns 与字幕窗口都按它）；
      ``local`` = 这条命令的第一帧在本页的局部帧号（翻页进来的那几帧已经由上一页的命令画了）；
    * 每页画到 ``_frame(end)``（本页终点）：硬切时正好是下一页的起点；翻页时 ``[turn, end)`` 这几帧是本页滑出、下一页
      滑入（``turn`` = 下一页起点那一帧），下一页的命令从 ``end`` 接着画。
    停顿可以设成 0，一页可能短到放不下前后两次翻页：这时报错，不出一段错位的片。"""

    plan: list[dict[str, Any]] = []
    first = 0
    for index, page in enumerate(pages):
        start, end = _frame(page["start"]), _frame(page["end"])
        turns = index + 1 < len(pages) and float(page.get("transition_out") or 0) > 0
        turn = _frame(pages[index + 1]["start"]) if turns else None
        if end <= first or (turn is not None and not first <= turn < end):
            raise AssemblyError(f"第 {page['id']} 页只有 {float(page['duration']):.2f} 秒，放不下前后两次翻页（各 0.4 秒）"
                                "——把这一页前后的翻页改成硬切，或把朗读停顿调长")
        plan.append({"first": first, "end": end, "local": first - start, "turn": turn})
        first = end
    return plan


def _page_chain(spec: Mapping[str, Any], page: Mapping[str, Any], image: Path, *, index: int, local: int, frames: int, width: int,
                height: int, text_dir: Path, font: str, highlight: str) -> str:
    """一页的画面链（输入 ``[index:v]`` 是这一页插画的循环）：从这一页的第 ``local`` 帧起画 ``frames`` 帧，Ken Burns + 字幕。
    起点可以挪到第 ``local`` 帧（翻页进来的那几帧已经由上一页的命令画了），按帧数截而不是按页时长截。"""

    style = spec.get("subtitle") or {}
    duration = float(page["duration"])
    kb = ken_burns_filter(str((page.get("kb") or {}).get("dir") or "push"), kb_scale(page, duration), duration, _image_size(image), width, height)
    # 插画只解码一次，loop 滤镜在滤镜图里循环这一帧（-loop 1 会让每一帧都重新解码一遍 PNG，更慢，解码线程还按核数开、
    # 更占内存）。fps 之后时基是 1/24：setpts 加 local 就是从这一页的第 local 帧画起，Ken Burns 的 t 与字幕 enable
    # 窗口里的 t 都是页内时间。先按帧数截、再进 Ken Burns：截在后面的话多出来的帧页内时间超过页时长，「拉远」的 Ken Burns
    # 过了页尾缩放小于 1、裁不出整幅，ffmpeg 直接崩掉（没有报错输出）。
    # setsar=1：scale(eval=frame)+crop 会留下 4095:4096 这种像素比，与 1:1 的片接不到一起
    chain = [f"[{index}:v]loop=loop=-1:size=1:start=0", f"fps={FPS}", f"setpts=PTS-STARTPTS+{local}" if local else "setpts=PTS-STARTPTS",
             f"trim=end_frame={frames}", kb, "setsar=1", "format=yuv420p"]
    chain += subtitle_filters(page, text_dir=text_dir, font=font, width=width, height=height, zh_size=int(style.get("zh_size") or 96),
                              en_size=int(style.get("en_size") or 64), highlight=highlight, page_duration=duration)
    chain.append("setpts=PTS-STARTPTS")
    return ",".join(chain)


def _still_input(image: Path) -> list[str]:
    """插画那一路输入：一张图、按 24 fps 记时间戳（循环与截帧在 ``_page_chain`` 里）。前面的输入选项见
    ``_still_input_options``：数据目录路径里的 %d 不当序号模板，解码器按插画真实内容选（.png 里常是 JPEG）。"""

    return [*_still_input_options(image), "-framerate", str(FPS), "-i", str(image)]


def _page_command(spec: Mapping[str, Any], row: Mapping[str, Any], page: Mapping[str, Any], image: Path,
                  following: Mapping[str, Any] | None, following_image: Path | None, *, ffmpeg: str, destination: Path, width: int,
                  height: int, text_dir: Path, font: str, highlight: str) -> tuple[list[str], str]:
    """一页的那段片（``frame_plan`` 的一行）：本页的链，翻页时再接下一页开头几帧的链、两路 xfade slideleft；按帧数截、按交付口径编码。
    返回 (命令, 滤镜图)。"""

    frames = row["end"] - row["first"]
    style = {"width": width, "height": height, "text_dir": text_dir, "font": font, "highlight": highlight}
    cmd: list[str] = [ffmpeg, "-y", "-loglevel", "error", *_still_input(image)]
    graph = _page_chain(spec, page, image, index=0, local=row["local"], frames=frames, **style)
    if row["turn"] is None:
        graph += "[v]"
    else:
        assert following is not None and following_image is not None
        offset = row["turn"] - row["first"]          # 这条命令的第几帧开始翻页
        fade = row["end"] - row["turn"]              # 翻页占几帧（0.4 秒落在帧网格上是 9 或 10 帧）
        cmd += _still_input(following_image)
        # 两路末尾各补几帧（复制最后一帧，不再过 Ken Burns），EOF 落在要用的帧之后，出片时按帧数截掉
        spare = f",tpad=stop={_SPARE_FRAMES}:stop_mode=clone"
        graph += spare + "[a];" + _page_chain(spec, following, following_image, index=1, local=0, frames=fade, **style) + spare + "[b];"
        graph += f"[a][b]xfade=transition=slideleft:duration={fade / FPS:.6f}:offset={offset / FPS:.6f},trim=end_frame={frames}[v]"
    cmd += ["-filter_complex", graph, "-map", "[v]", *_VIDEO_ENCODE, "-threads", str(PAGE_ENCODER_THREADS), "-r", str(FPS),
            str(destination)]
    return cmd, graph


def _audio_command(spec: Mapping[str, Any], pages: Sequence[Mapping[str, Any]], total: float, *, narration: list[tuple[Path, float]],
                   premixed: list[Any] | None, ffmpeg: str, flip: Path | None, bgm: Path | None) -> tuple[list[str], list[str]]:
    """全书那条混音命令的输入与滤镜图：静音底轨（定总长）+ 朗读（逐段，或预混后的几段）+ 翻页音效 + BGM → loudnorm。
    画面在各页自己的命令里出，这条命令只有声音的输入。"""

    cmd: list[str] = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    filters: list[str] = []
    next_input = 1

    # ---- 朗读段
    mix_labels: list[str] = ["[0:a]"]
    if premixed is None:
        placed = [(path, f"adelay={int(round(start * 1000))}|{int(round(start * 1000))}") for path, start in narration]
    else:
        placed = [(clip.path, adelay_samples(clip.start_sample)) for clip in premixed]
    for path, delay in placed:
        cmd += ["-i", str(path)]
        filters.append(f"[{next_input}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,{delay}[n{next_input}]")
        mix_labels.append(f"[n{next_input}]")
        next_input += 1

    # ---- 翻页音效：xfade 开始后过半个转场时长响起
    flip_times = [float(page["start"]) + float(pages[i - 1]["transition_out"]) / 2
                  for i, page in enumerate(pages) if i and float(pages[i - 1].get("transition_out") or 0) > 0]
    if flip and flip_times:
        cmd += ["-i", str(flip)]
        outs = "".join(f"[fl{k}]" for k in range(len(flip_times)))
        filters.append(f"[{next_input}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,asplit={len(flip_times)}{outs}")
        for k, at in enumerate(flip_times):
            ms = int(round(at * 1000))
            filters.append(f"[fl{k}]adelay={ms}|{ms},volume={FLIP_GAIN:.2f}[fa{k}]")
            mix_labels.append(f"[fa{k}]")
        next_input += 1

    # ---- BGM：gain + 尾段渐弱（封底页的 3 s 在时间轴里，渐弱盖住它）
    if bgm:
        music = spec.get("music") or {}
        gain = float(music.get("gain") or 0.25)
        fade = float(music.get("fade_out") or 3.0)
        fade = min(fade, total)
        cmd += ["-stream_loop", "-1", "-i", str(bgm)]
        filters.append(
            f"[{next_input}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
            f"volume={gain:.3f},afade=t=in:st=0:d={min(BGM_FADE_IN, total):.3f},afade=t=out:st={max(0.0, total - fade):.3f}:d={fade:.3f}[bgm]")
        mix_labels.append("[bgm]")
        next_input += 1

    filters.append("".join(mix_labels) + f"amix=inputs={len(mix_labels)}:duration=first:normalize=0:dropout_transition=0,"
                   "loudnorm=I=-14:TP=-2:LRA=11[aout]")
    return cmd, filters


def _mix_audio(spec: Mapping[str, Any], timeline: Mapping[str, Any], work: Path, *, ffmpeg: str, flip: Path | None, bgm: Path | None,
               timeout: float) -> Path:
    """全书的声音混成一个 AAC 文件（两个画幅共用）。朗读段多于 ``MIX_GROUP`` 段先分组预混：一条命令的输入数与命令行长度
    不随段数涨。在画面之前跑：朗读文件坏了几秒内就报（以前要等全书的画面都编码完才读到朗读）。"""

    pages = timeline["pages"]
    total = float(timeline["total"])
    audio_root = audio_dir(spec)
    narration: list[tuple[Path, float]] = []
    for page in pages:
        for seg in page.get("segments") or ():
            file = seg.get("file")
            if not file:
                raise AssemblyError(f"第 {page['id']} 页的{'中文' if seg['lang'] == 'zh' else '英文'}朗读还没合成，先生成朗读")
            path = audio_root / str(file)
            if not path.is_file():
                raise AssemblyError(f"朗读文件不见了：{path.name}——重新生成朗读")
            narration.append((path, float(seg["start"])))
    premixed = premix_audio(narration, work, ffmpeg=ffmpeg, sample_rate=48000, run=_run, timeout_seconds=timeout)
    cmd, filters = _audio_command(spec, pages, total, narration=narration, premixed=premixed, ffmpeg=ffmpeg, flip=flip, bgm=bgm)
    graph = manifests_dir(spec) / "book_filtergraph.txt"
    graph.parent.mkdir(parents=True, exist_ok=True)
    graph.write_text(";\n".join(filters), encoding="utf-8")
    destination = work / "book_audio.m4a"
    cmd += ["-filter_complex_script", str(graph), "-map", "[aout]", *_AUDIO_ENCODE, str(destination)]
    _run(cmd, purpose="mix picture book audio", timeout_seconds=timeout)
    for clip in premixed or ():
        clip.path.unlink(missing_ok=True)
    return destination


def _render(spec: Mapping[str, Any], timeline: Mapping[str, Any], plan: Sequence[Mapping[str, Any]], images: Sequence[Path], *, work: Path,
            audio: Path, width: int, height: int, destination: Path, log_name: str, ffmpeg: str, ffprobe: str, font: str,
            timeout: float) -> dict[str, Any]:
    """出一个画幅的成片：逐页出片（几页并行）→ concat 演示器把各页的片流复制接起来、配上混好的声音 → 核过之后原子替换。
    ``timeout`` 是每一条 ffmpeg 命令的上限。"""

    pages = timeline["pages"]
    total = float(timeline["total"])
    text_dir = edit_dir(spec) / "subs"
    text_dir.mkdir(parents=True, exist_ok=True)
    highlight = _color((spec.get("subtitle") or {}).get("highlight"))
    tag = f"{width}x{height}"
    jobs: list[tuple[list[str], str]] = []
    pieces: list[Path] = []
    log: list[str] = []
    for index, (page, row) in enumerate(zip(pages, plan)):
        following = pages[index + 1] if row["turn"] is not None else None
        piece = work / f"{tag}_{index:03d}.mp4"
        cmd, graph = _page_command(spec, row, page, images[index], following, images[index + 1] if following else None, ffmpeg=ffmpeg,
                                   destination=piece, width=width, height=height, text_dir=text_dir, font=font, highlight=highlight)
        turn = f", page turn from frame {row['turn']}" if row["turn"] is not None else ""
        log.append(f"page {page['id']}: frames {row['first']}-{row['end']}{turn}\n{graph}")
        jobs.append((cmd, f"render picture book page {page['id']}"))
        pieces.append(piece)
    (manifests_dir(spec) / log_name).write_text("\n\n".join(log), encoding="utf-8")
    temporary = _temporary_destination(destination)
    try:
        _run_pages(jobs, timeout=timeout)
        listing = work / f"{tag}_pages.txt"
        listing.write_text("".join(concat_entry(piece) for piece in pieces), encoding="utf-8")
        _run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(audio),
              "-map", "0:v", "-map", "1:a", "-c", "copy", "-movflags", "+faststart", str(temporary)],
             purpose=f"assemble picture book {tag}", timeout_seconds=timeout)
        for piece in pieces:
            piece.unlink(missing_ok=True)
        from storyvideo.media import MediaValidationError, validate_media

        try:
            info = validate_media(temporary, "video", require_audio=True, expected_duration_seconds=total,
                                  duration_tolerance_seconds=max(0.75, total * 0.02), expected_dimensions=(width, height), ffprobe=ffprobe)
        except (MediaValidationError, OSError, TypeError, ValueError) as exc:
            raise AssemblyError(f"assembled final media is invalid: {exc}") from exc
        _sync_file(temporary)
        replace_final(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(destination), "duration": info.duration_seconds, "width": width, "height": height,
            "graph": str(manifests_dir(spec) / "book_filtergraph.txt"), "pages": str(manifests_dir(spec) / log_name)}


def assemble_book(spec: Mapping[str, Any], *, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe", font_path: str | os.PathLike[str] | None = None,
                  landscape: bool | None = None, timeout_seconds: float = DEFAULT_TIMEOUT, note: Any = None) -> dict[str, Any]:
    """装配主版（9:16）；``landscape``（默认按 ``delivery.landscape_variant``）为真再出 16:9。"""

    manifest = N.sync_imported(spec, prober=lambda p: N.probe_duration(p, ffprobe=ffprobe))
    timeline = build_timeline(spec, N.segments_for_timeline(spec, manifest))
    if not timeline["pages"]:
        raise AssemblyError("这本书还没有分页")
    missing = sorted({f"{p['id']}_{s['lang']}" for p in timeline["pages"] for s in p["segments"] if s.get("estimated") or not s.get("file")})
    if missing:
        raise AssemblyError(f"这些朗读还没合成：{', '.join(missing)}——先生成朗读")
    font = str(font_path) if font_path else subtitles.resolve_font(spec.get("subtitle"))
    if not font or not Path(font).is_file():
        raise AssemblyError("UI font unavailable: 没找到可用的中文字体，在字幕设置里指定 font_path")
    # 素材先查齐（插画），缺了在跑任何 ffmpeg 之前就报；帧网格排不下（页太短）同样先报
    images: list[Path] = []
    for page in timeline["pages"]:
        image = PG.page_image_path(spec, page["id"])
        if image is None:
            raise AssemblyError(f"第 {page['id']} 页还没有插画，先生成插画")
        images.append(image)
    plan = frame_plan(timeline["pages"])
    atomic_write_json(timeline_path(spec), timeline)
    edit_dir(spec).mkdir(parents=True, exist_ok=True)
    # 改之前 16:9 另有一张全书滤镜图；现在声音两个画幅共用 book_filtergraph.txt，留着旧文件只会误导排障
    (manifests_dir(spec) / "book_filtergraph_16x9.txt").unlink(missing_ok=True)
    flip = _ensure_flip(spec, ffmpeg=ffmpeg, timeout=timeout_seconds) if any(float(p.get("transition_out") or 0) > 0 for p in timeline["pages"]) else None
    music = spec.get("music") or {}
    bgm = resolve_asset(spec, music.get("file")) if music else None
    if music and music.get("file") and bgm is None:
        raise AssemblyError(f"配乐文件不存在：{music.get('file')}")
    if note:
        note(stage="render", total=timeline["total"])
    want_landscape = bool((spec.get("delivery") or {}).get("landscape_variant")) if landscape is None else bool(landscape)
    render = {"ffmpeg": ffmpeg, "ffprobe": ffprobe, "font": font, "timeout": timeout_seconds}
    # 声音与逐页的片都放在这个临时目录里，出完就清；清不掉（Windows 上偶尔有杀毒 / 预览进程占着）不该让已经出好的成片算失败
    with tempfile.TemporaryDirectory(prefix=".book-", dir=edit_dir(spec), ignore_cleanup_errors=True) as work_name:
        work = Path(work_name)
        audio = _mix_audio(spec, timeline, work, ffmpeg=ffmpeg, flip=flip, bgm=bgm, timeout=timeout_seconds)
        main = _render(spec, timeline, plan, images, work=work, audio=audio, width=PORTRAIT[0], height=PORTRAIT[1],
                       destination=final_path(spec), log_name="book_pages.txt", **render)
        result: dict[str, Any] = {"final": main, "landscape": None, "timeline": str(timeline_path(spec)), "total": timeline["total"]}
        if want_landscape:
            if note:
                note(stage="render_16x9")
            result["landscape"] = _render(spec, timeline, plan, images, work=work, audio=audio, width=LANDSCAPE[0], height=LANDSCAPE[1],
                                          destination=landscape_path(spec), log_name="book_pages_16x9.txt", **render)
    return result


__all__ = ["AssemblyError", "FPS", "PORTRAIT", "LANDSCAPE", "final_path", "landscape_path", "timeline_path", "kb_scale", "ken_burns_filter",
           "subtitle_filters", "frame_plan", "assemble_book"]
