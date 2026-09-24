"""分段合成的共享部分：音频的分组预混（播客混音、绘本朗读、短剧后期版的旁白 + 配音）与 concat 演示器清单。

为什么要分组（2026-09-22 发第一个 tag 前改的）：以前这几条路径把每一句配音 / 朗读都作为一路输入接进同一条混音命令。
内存不大（播客 150 句实测峰值 0.19 GB），但 Windows 的命令行上限是 32767 个字符，每一路 ``-i <绝对路径>`` 加上它那一段
滤镜，150 句左右的播客就超了——ffmpeg 根本起不来（WinError 206「文件名或扩展名太长」），90 分钟上限的一期一定混不出来。

``premix_audio``：按绝对时间铺的一串音频超过 ``MIX_GROUP`` 路时，按时间顺序每 ``MIX_GROUP`` 路先混成一个中间文件，
再把中间文件按它们的起点铺进最后那条混音命令。声音是相加的（amix normalize=0），中间文件存 32 位浮点（WavPack 无损压缩），
先分组相加再相加结果与一次相加一样、不削顶；每一路的落点按采样数算，与以前按毫秒 adelay 的落点一致。

画面这边：短剧后期版的分组拼接在 ``post_layers._join_segments``，绘本按帧网格逐页出片（``picturebook.assembly``），
两边都用 concat 演示器流复制接片，清单的写法在这里（``concat_entry``）。

函数都不自己跑命令：``run`` 由调用方传入（各模块有自己的报错口径，测试也在这一层换替身）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

# 一条混音命令最多接几路按时间铺的音频（逐句配音 / 朗读）。一路 ≈ 一个绝对路径 + 一段 aformat/adelay 滤镜，64 路在
# 最长的数据根路径下也远低于 Windows 的命令行上限；混音本身的内存与路数关系不大
MIX_GROUP = 64
_AUDIO_FORMAT = "aformat=sample_fmts=fltp:sample_rates={rate}:channel_layouts=stereo"
# 预混中间文件：WavPack 32 位浮点，无损、不削顶。以前用 FLAC 24 位（整数），两句重叠或 TTS 的 mp3 解码出来略过满幅时，
# 叠加超过 0 dBFS 的部分被削平，而一条命令直接混时是浮点相加、交给 loudnorm 压，不会削——实测两路叠到 1.43 时 FLAC 峰值
# 被压成 1.0（误差 0.26），WavPack 浮点逐采样一致；体积约为 32 位浮点 WAV 的四成
_PREMIX_ENCODE = ("-c:a", "wavpack", "-sample_fmt", "fltp")
_PREMIX_SUFFIX = ".wv"

Runner = Callable[..., Any]


def concat_entry(path: Path) -> str:
    """concat 演示器清单里的一行：单引号包住的 posix 路径，路径里的单引号按它的规则写成 '\\''。"""

    return "file '" + path.resolve().as_posix().replace("'", "'\\''") + "'\n"


@dataclass(frozen=True)
class PlacedAudio:
    """铺在时间线上的一路音频：文件 + 起点（采样数，按 ``sample_rate``）。"""

    path: Path
    start_sample: int


def ms_to_sample(milliseconds: int, sample_rate: int) -> int:
    """毫秒起点 → 采样数：以前各模块都按 ``adelay=<毫秒>`` 铺，先取整到毫秒再换采样，落点与以前一致。

    向下取整，不四舍五入：ffmpeg 的 adelay 把毫秒换成采样数时是截断的（``delay * sample_rate / 1000`` 赋给 int），
    四舍五入的话一半的句子会晚一个采样，逐采样比对改前改后的成品时每句开头都对不上。"""

    return milliseconds * sample_rate // 1000


def adelay_samples(start_sample: int) -> str:
    """按采样数铺的 adelay（``S`` 后缀 = 采样数，ffmpeg 4.2 起支持）。"""

    return f"adelay={start_sample}S|{start_sample}S"


def premix_audio(items: Sequence[tuple[Path, float]], work: Path, *, ffmpeg: str, sample_rate: int, run: Runner,
                 timeout_seconds: float = 600.0, group: int | None = None, log: list[str] | None = None) -> list[PlacedAudio] | None:
    """一串按绝对时间铺的音频 ``[(文件, 起点秒)]``：不超过 ``group`` 路时返回 None（调用方照旧一条命令直接混）；
    超过时按起点排序、每 ``group`` 路先混成一个中间文件，中间文件还多就再分组混一级，返回最后一级
    ``[PlacedAudio]``——调用方把它们按 ``adelay_samples(start_sample)`` 铺进最后那条混音命令，代替逐路输入。

    每一路先 ``aformat`` 到 ``sample_rate`` 立体声浮点（与以前逐路的格式化一样），组内按相对这一组起点的采样数 adelay；
    组的长度 = 组里最晚结束的那一路（amix duration=longest），不补不截。中间文件放在 ``work`` 里，由调用方清。"""

    # 调用时才取模块里的值：测试把组调小就能在几句话上走分组那条路。至少两路一组，一路一组永远混不少
    group = max(2, MIX_GROUP if group is None else group)
    if len(items) <= group:
        return None
    audio_format = _AUDIO_FORMAT.format(rate=sample_rate)
    level = sorted((PlacedAudio(Path(path), ms_to_sample(int(round(float(start) * 1000)), sample_rate)) for path, start in items),
                   key=lambda x: x.start_sample)
    originals = {x.path for x in level}
    depth = 0
    while len(level) > group:
        depth += 1
        next_level: list[PlacedAudio] = []
        for g, k in enumerate(range(0, len(level), group)):
            chunk = level[k:k + group]
            if len(chunk) == 1:
                next_level.append(chunk[0])
                continue
            base = chunk[0].start_sample
            dest = work / f"premix_{depth}_{g:03d}{_PREMIX_SUFFIX}"
            cmd: list[str] = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
            filters: list[str] = []
            for i, clip in enumerate(chunk):
                cmd += ["-i", str(clip.path)]
                filters.append(f"[{i}:a]{audio_format},{adelay_samples(clip.start_sample - base)}[p{i}]")
            labels = "".join(f"[p{i}]" for i in range(len(chunk)))
            filters.append(f"{labels}amix=inputs={len(chunk)}:duration=longest:normalize=0:dropout_transition=0[out]")
            graph = ";".join(filters)
            cmd += ["-filter_complex", graph, "-map", "[out]", *_PREMIX_ENCODE, "-ar", str(sample_rate), str(dest)]
            if log is not None:
                log.append(f"premix level {depth} group {g + 1}: {len(chunk)} inputs\n{graph}")
            run(cmd, purpose=f"premix audio (level {depth}, group {g + 1})", timeout_seconds=timeout_seconds)
            next_level.append(PlacedAudio(dest, base))
        # 上一级的中间文件已经混进这一级了（原始素材不动）
        for clip in level:
            if clip not in next_level and clip.path not in originals:
                clip.path.unlink(missing_ok=True)
        level = next_level
    return level


__all__ = ["MIX_GROUP", "concat_entry", "PlacedAudio", "ms_to_sample", "adelay_samples", "premix_audio"]
