"""Callable, fail-closed post-production for normalized StoryVideo specs.

Head trimming, language-selectable burned-in subtitles, short crossfades, speech
cleanup and loudness normalisation, behind a side-effect-free import and one
callable entry point.

``assemble_episode`` deliberately accepts only the output of
``storyvideo.spec.normalize_spec``/``load_spec``.  In particular, dialogue is
read from the normalized ``shot["lines"]`` array; legacy ``speaker/en/zh``
fields are not interpreted here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import logging
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any
import uuid

from .media import MediaInfo, MediaValidationError, validate_media


class AssemblyError(RuntimeError):
    """Raised when an episode cannot be assembled safely."""


@dataclass(frozen=True)
class AssemblyResult:
    """Artifacts and effective delivery settings from one assembly run."""

    final_path: Path
    duration_seconds: float
    width: int
    height: int
    fps: float
    clip_paths: tuple[Path, ...]
    contact_sheet_path: Path | None = None

    def __fspath__(self) -> str:
        return os.fspath(self.final_path)


@dataclass(frozen=True)
class _Clip:
    path: Path
    duration_seconds: float


_SHOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RESOLUTION = re.compile(r"^([1-9][0-9]{2,3})p$", re.IGNORECASE)
_DIMENSIONS = re.compile(r"^([1-9][0-9]{2,4})\s*[xX]\s*([1-9][0-9]{2,4})$")
_RATIO = re.compile(r"^([1-9][0-9]?)\s*:\s*([1-9][0-9]?)$")
_RMS_TIME = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
_RMS_VALUE = re.compile(r"RMS_level=(-?(?:[0-9]+(?:\.[0-9]+)?|inf))", re.IGNORECASE)
_SUBTITLE_COLOR = re.compile(r"^(?:0x|#)[0-9A-Fa-f]{6}$")
_SUBTITLE_LANGUAGES = frozenset({"bilingual", "zh", "en"})

_DEFAULT_WIDTH = 1080
_DEFAULT_HEIGHT = 1920
_DEFAULT_FPS = 24.0
_DEFAULT_CROSSFADE_SECONDS = 0.25
_DEFAULT_MAXRATE_KBPS = 12000
_DEFAULT_BUFSIZE_KBPS = 24000
_MAX_VIDEO_RATE_KBPS = 100_000
_MAX_VIDEO_BUFSIZE_KBPS = 200_000
_SPEECH_DB = -34.0
_MIN_SPEECH_RUN = 3
_SPEECH_KEEP_SECONDS = 0.35
_MAX_TRIM_FRACTION = 0.45


def _tail(value: str | None, limit: int = 1000) -> str:
    """ffmpeg 的 stderr 截成能进任务面板的长度：超长时头尾各留一段，不只留尾。
    「Stream specifier ':a' in filtergraph description <整张滤镜图> matches no streams」这类报错把原因写在最前面、
    滤镜图夹在中间（随镜数线性增长），只留尾巴的话用户看到的是一屏滤镜图加四个字。"""

    detail = (value or "").strip()
    if not detail:
        return "command failed without diagnostics"
    if len(detail) <= limit:
        return detail
    head = limit // 3
    return detail[:head] + " … " + detail[-(limit - head):]


# ffmpeg 失败时的报错口径（2026-09-22 发第一个 tag 前改的）：以前把 stderr 的头尾各一段（最多 1000 字）连同滤镜图原样
# 抛给任务面板，界面上是一屏英文加「遇到没预料到的错误」。现在给人看的只有一行原因，完整命令行、滤镜图、stderr 进日志
# （桌面版在 ~/.storystudio/logs/storystudio.log）——排障信息一点不少，只是不再堆在弹窗里。
_FFMPEG_LOG = logging.getLogger("storystudio.ffmpeg")
FFMPEG_LOG_HINT = "完整报错写进了日志 storystudio.log"
_REASON_LIMIT = 240
_LOG_GRAPH_LIMIT = 200_000
# stderr 每行开头「[Parsed_xfade_175 @ 000001ece68e0380] 」这种滤镜 / 解码器实例前缀：对人没有信息量
_FFMPEG_INSTANCE = re.compile(r"^\[[^\]]* @ [0-9A-Fa-fx]+\]\s*")
# 只说「出错了」、不说为什么的收尾行（真正的原因在它们前面）：挑原因时跳过
_FFMPEG_GENERIC = (
    "error reinitializing filters", "failed to inject frame into filter network", "error while processing the decoded data",
    "failed to configure output pad", "error configuring filters", "error initializing complex filters", "error while filtering",
    "conversion failed", "nothing was written into output file", "error opening output files", "error opening output file",
    "error initializing filter", "error configuring the filter graph",
)
# 「D:\...\P01_zh.mp3: Invalid argument」：带目录分隔符、有扩展名的路径 + 冒号 + 原因
_FFMPEG_FILE_LINE = re.compile(r"^(?P<path>[^\[]*?[\\/][^\\/]*?\.[A-Za-z0-9]{2,5}): (?P<msg>.+)$")
_FFMPEG_OOM = ("cannot allocate memory", "out of memory", "failed to allocate")
_FFMPEG_DISK = ("no space left on device", "disk full")
# 没带 -loglevel error 的命令，stderr 里先是横幅与输入输出的描述（「Input #0 …」「  encoder : Lavf60…」）：都不是原因
_FFMPEG_INFO = ("ffmpeg version", "built with", "configuration:", "libav", "libsw", "libpostproc", "input #", "output #", "stream #",
                "stream mapping", "metadata:", "duration:", "press [q]", "side data:")
_FFMPEG_META = re.compile(r"^[A-Za-z_][\w.-]*\s+: ")
# 「Stream specifier ':a' in filtergraph description <整张滤镜图> matches no streams」：某个输入里没有滤镜要读的那一路
# （坏文件、拖错了文件改了扩展名）。原话把整张滤镜图夹在中间、又不说是哪个文件，换成一句话；是哪个文件由 _run 去查
# 滤镜图从 -filter_complex_script 读时带换行，这句原话会跨很多行：在整段 stderr 上找，不按行找
_FFMPEG_NO_STREAM = re.compile(r"Stream specifier '(?P<spec>[^']*)' in filtergraph description .*? matches no streams", re.S)
_NO_STREAM_TEXT = {"audio": ("读不出音频", "文件坏了，或者不是音频文件"), "video": ("读不出画面", "文件坏了，或者不是视频 / 图片"),
                   "": ("读不出要用的音视频", "文件坏了，或者格式不对")}
# 滤镜图本身拼得不对：原话里是一截滤镜图，对人没用。这是程序拼滤镜图时的问题，不是素材的问题
_FFMPEG_GRAPH_SYNTAX = ("trailing garbage after a filter", "error parsing filterchain", "error parsing a filter description",
                        "no option name near", "unable to parse graph description")
FFMPEG_GRAPH_MESSAGE = "滤镜图拼写有误（程序的问题，不是素材的问题）"


def _ffmpeg_lines(diagnostic: str | None) -> list[str]:
    lines = [_FFMPEG_INSTANCE.sub("", " ".join(line.split())) for line in (diagnostic or "").splitlines()]
    return [line for line in lines if line and not line.lower().startswith(_FFMPEG_INFO) and not _FFMPEG_META.match(line)]


def _missing_stream_kind(diagnostic: str | None) -> str | None:
    """「… matches no streams」是缺哪种流：audio / video / ""（别的写法）；不是这种报错返回 None。"""

    match = _FFMPEG_NO_STREAM.search(diagnostic or "")
    if match is None:
        return None
    spec = match["spec"].rsplit(":", 1)[-1]
    return {"a": "audio", "v": "video"}.get(spec[:1], "")


def ffmpeg_reason(diagnostic: str | None) -> str:
    """ffmpeg 的 stderr → 给人看的一行原因。

    ffmpeg 通常先写真正的原因、再写几行「Error reinitializing filters!」这类收尾：取第一条不是收尾套话的行，去掉
    滤镜实例前缀，超长的头尾各留一段。内存不够 / 磁盘满这两种人能自己处理的，前面点明；输入里缺流、滤镜图拼写有误这两种
    原话里夹着滤镜图的，换成一句话。"""

    lines = _ffmpeg_lines(diagnostic)
    if not lines:
        return "ffmpeg 没有给出原因"
    for needles, label in ((_FFMPEG_OOM, "内存不够"), (_FFMPEG_DISK, "磁盘空间不够")):
        hit = next((line for line in lines if any(n in line.lower() for n in needles)), None)
        if hit is not None:
            return f"{label}：{_tail(hit, _REASON_LIMIT)}"
    kind = _missing_stream_kind(diagnostic)
    if kind is not None:
        what, why = _NO_STREAM_TEXT[kind]
        return f"有一个输入文件{what}（{why}）"
    cause = next((line for line in lines if not line.lower().startswith(_FFMPEG_GENERIC)), lines[0])
    if cause.lower().startswith(_FFMPEG_GRAPH_SYNTAX):
        return FFMPEG_GRAPH_MESSAGE
    # 坏的是哪个输入文件：ffmpeg 把它单写成一行「<绝对路径>: Invalid argument」，原因那一行却不带文件名
    # （「Failed to read frame size: Could not seek to 1873.」）。把文件名接到原因前面，路径只留文件名
    named = next((m for m in map(_FFMPEG_FILE_LINE.match, lines) if m), None)
    if named is not None:
        name = re.split(r"[\\/]", named["path"])[-1]
        cause = f"{name}: {named['msg']}" if cause == named.group(0) else f"{name}: {cause}"
    return _tail(cause, _REASON_LIMIT)


def _ffprobe_beside(ffmpeg: str) -> str:
    """与这个 ffmpeg 同目录的 ffprobe；ffmpeg 是裸名字（走 PATH）或同目录没有时走 PATH。"""

    path = Path(ffmpeg)
    if "ffmpeg" in path.name.lower():
        candidate = path.with_name(path.name.lower().replace("ffmpeg", "ffprobe"))
        if path.parent != Path() and candidate.is_file():
            return str(candidate)
    return shutil.which("ffprobe") or "ffprobe"


def _unreadable_input(command: Sequence[str], kind: str) -> str | None:
    """「… matches no streams」时是哪个输入：逐个 ffprobe 命令里的文件输入（``-f`` 指定了格式的 lavfi 源、concat 清单
    不算），第一个探不出、或者里面没有 ``kind`` 那种流的就是，返回文件名。只在失败之后跑：最多几十个文件、每个几十毫秒。"""

    args = [str(part) for part in command]
    probe = _ffprobe_beside(args[0])
    forced = False
    for index in range(1, len(args) - 1):
        if args[index] == "-f":
            forced = True
            continue
        if args[index] != "-i":
            continue
        path = Path(args[index + 1])
        if not forced and path.is_file():
            try:
                probed = subprocess.run([probe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                                        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                        timeout=30, shell=False, check=False)
            except (OSError, subprocess.TimeoutExpired):
                return None          # 连 ffprobe 都跑不了：不猜是哪个
            types = probed.stdout.split()
            if probed.returncode != 0 or not types or (kind and kind not in types):
                return path.name
        forced = False
    return None


def explain_ffmpeg_failure(command: Sequence[str], diagnostic: str | None) -> str:
    """一条失败的 ffmpeg 命令给人看的一行原因：``ffmpeg_reason``；输入里缺流时再查出是哪个文件，点名。"""

    kind = _missing_stream_kind(diagnostic)
    if kind is not None:
        name = _unreadable_input(command, kind)
        if name is not None:
            what, why = _NO_STREAM_TEXT[kind]
            return f"{name} {what}（{why}）"
    return ffmpeg_reason(diagnostic)


def ffmpeg_failure_message(purpose: str, reason: str) -> str:
    """任务面板上的那一句：哪一步（括号里，给开发者对日志用）+ 一行原因 + 去哪看完整报错。"""

    return f"ffmpeg 出错（{purpose}）：{reason}——{FFMPEG_LOG_HINT}"


def _launch_reason(exc: OSError, program: str) -> str:
    """ffmpeg 没能启动的一行原因。WinError 206 在 Python 里也是 FileNotFoundError，但意思是命令行太长，不是找不到程序。"""

    if getattr(exc, "winerror", None) == 206:
        return "命令行超过了 Windows 的长度上限"
    if isinstance(exc, FileNotFoundError):
        return f"找不到 ffmpeg 程序：{program}"
    return f"启动失败：{_tail(str(exc), _REASON_LIMIT)}"


def log_ffmpeg_failure(command: Sequence[str], purpose: str, detail: str | bytes | None) -> None:
    """失败的 ffmpeg 命令进日志：完整命令行（内联的滤镜图就在里面）、``-filter_complex_script`` 指向的滤镜图、完整 stderr。"""

    args = [str(part) for part in command]
    parts = [f"ffmpeg failed ({purpose})", "command: " + subprocess.list2cmdline(args)]
    if "-filter_complex_script" in args[:-1]:
        script = Path(args[args.index("-filter_complex_script") + 1])
        try:
            graph = script.read_text(encoding="utf-8", errors="replace")[:_LOG_GRAPH_LIMIT]
        except OSError as exc:
            graph = f"<unreadable: {exc}>"
        parts.append(f"filter graph ({script}):\n{graph}")
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", errors="replace")
    parts.append("stderr:\n" + (detail or "").strip())
    _FFMPEG_LOG.error("\n".join(parts))


def _run(
    command: Sequence[str],
    *,
    purpose: str,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    """Run one media command without a shell and turn every failure into context.

    失败时抛的 AssemblyError 只有一行原因（``ffmpeg_failure_message``），完整命令、滤镜图、stderr 进日志。"""

    try:
        completed = subprocess.run(
            [str(part) for part in command],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        log_ffmpeg_failure(command, purpose, exc.stderr)
        raise AssemblyError(ffmpeg_failure_message(purpose, f"超过 {timeout_seconds:g} 秒还没跑完，已中止")) from exc
    except OSError as exc:
        log_ffmpeg_failure(command, purpose, str(exc))
        raise AssemblyError(ffmpeg_failure_message(purpose, _launch_reason(exc, str(command[0])))) from exc
    if completed.returncode != 0:
        diagnostic = completed.stderr or completed.stdout
        log_ffmpeg_failure(command, purpose, diagnostic)
        raise AssemblyError(ffmpeg_failure_message(purpose, explain_ffmpeg_failure(command, diagnostic)))
    return completed


def _number(value: Any, *, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise AssemblyError(f"{field} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AssemblyError(f"{field} must be a number") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise AssemblyError(f"{field} must be between {minimum:g} and {maximum:g}")
    return result


def _even(value: float) -> int:
    rounded = int(round(value))
    return rounded if rounded % 2 == 0 else rounded + 1


def _dimensions_from_profile(profile: Mapping[str, Any]) -> tuple[int, int] | None:
    width = profile.get("width")
    height = profile.get("height")
    if width is not None or height is not None:
        if width is None or height is None:
            raise AssemblyError("delivery width and height must be provided together")
        parsed_width = int(_number(width, field="delivery.width", minimum=2, maximum=8192))
        parsed_height = int(
            _number(height, field="delivery.height", minimum=2, maximum=8192)
        )
        if parsed_width % 2 or parsed_height % 2:
            raise AssemblyError("delivery width and height must be even")
        return parsed_width, parsed_height

    raw_resolution = profile.get("resolution")
    if raw_resolution is None:
        return None
    resolution = str(raw_resolution).strip()
    explicit = _DIMENSIONS.fullmatch(resolution)
    if explicit:
        parsed_width, parsed_height = (int(part) for part in explicit.groups())
        if parsed_width % 2 or parsed_height % 2:
            raise AssemblyError("delivery resolution dimensions must be even")
        return parsed_width, parsed_height

    short_side_match = _RESOLUTION.fullmatch(resolution)
    if short_side_match is None:
        raise AssemblyError(
            "delivery resolution must look like '1080p' or '1080x1920'"
        )
    short_side = int(short_side_match.group(1))
    ratio_text = str(profile.get("aspect_ratio") or "9:16").strip()
    ratio_match = _RATIO.fullmatch(ratio_text)
    if ratio_match is None:
        raise AssemblyError("delivery aspect_ratio must look like '9:16'")
    ratio_width, ratio_height = (int(part) for part in ratio_match.groups())
    if ratio_width <= ratio_height:
        return _even(short_side), _even(short_side * ratio_height / ratio_width)
    return _even(short_side * ratio_width / ratio_height), _even(short_side)


def _delivery_settings(spec: Mapping[str, Any]) -> tuple[int, int, float, float]:
    video_params = spec.get("video_params") or {}
    delivery = spec.get("delivery") or {}
    if not isinstance(video_params, Mapping):
        raise AssemblyError("spec.video_params must be an object")
    if not isinstance(delivery, Mapping):
        raise AssemblyError("spec.delivery must be an object")

    # Delivery is the final-output contract and therefore wins over provider
    # request parameters when both specify the same setting.
    merged: dict[str, Any] = dict(video_params)
    merged.update(delivery)
    dimensions = _dimensions_from_profile(merged) or (_DEFAULT_WIDTH, _DEFAULT_HEIGHT)
    fps_value = merged.get("fps", merged.get("frame_rate", _DEFAULT_FPS))
    fps = _number(fps_value, field="delivery.fps", minimum=1, maximum=240)
    crossfade_value = merged.get("crossfade_seconds", _DEFAULT_CROSSFADE_SECONDS)
    crossfade = _number(
        crossfade_value,
        field="delivery.crossfade_seconds",
        minimum=0,
        maximum=5,
    )
    return dimensions[0], dimensions[1], fps, crossfade


def _delivery_bitrate_settings(spec: Mapping[str, Any]) -> tuple[int, int]:
    delivery = spec.get("delivery") or {}
    if not isinstance(delivery, Mapping):
        raise AssemblyError("spec.delivery must be an object")

    def whole_kbps(*, field: str, default: int, maximum: int) -> int:
        parsed = _number(
            delivery.get(field, default),
            field=f"delivery.{field}",
            minimum=1,
            maximum=maximum,
        )
        if not parsed.is_integer():
            raise AssemblyError(f"delivery.{field} must be a whole number of kbps")
        return int(parsed)

    maxrate = whole_kbps(
        field="maxrate_kbps",
        default=_DEFAULT_MAXRATE_KBPS,
        maximum=_MAX_VIDEO_RATE_KBPS,
    )
    bufsize = whole_kbps(
        field="bufsize_kbps",
        default=_DEFAULT_BUFSIZE_KBPS,
        maximum=_MAX_VIDEO_BUFSIZE_KBPS,
    )
    if bufsize < maxrate:
        raise AssemblyError(
            "delivery.bufsize_kbps must be greater than or equal to "
            "delivery.maxrate_kbps"
        )
    return maxrate, bufsize


def delivery_settings(spec: Mapping[str, Any]) -> tuple[int, int, float, float]:
    """Return the validated width, height, fps and crossfade delivery contract."""

    return _delivery_settings(spec)


def preflight_assembly(
    spec: Mapping[str, Any],
    *,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    font_path: str | os.PathLike[str] | None = None,
) -> None:
    """Fail before paid generation if local post-production cannot run."""

    _require_normalized_spec(spec)
    _delivery_settings(spec)
    _delivery_bitrate_settings(spec)
    _choose_font(spec, font_path)
    for label, command in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        candidate = Path(command).expanduser()
        available = candidate.is_file() if candidate.parent != Path(".") else bool(
            shutil.which(command)
        )
        if not available:
            raise AssemblyError(f"{label} executable is not available: {command}")


def _require_normalized_spec(spec: Mapping[str, Any]) -> tuple[Path, list[Mapping[str, Any]]]:
    if not isinstance(spec, Mapping):
        raise TypeError("spec must be a normalized mapping")
    missing_markers = [
        key for key in ("project_root", "spec_dir", "waves", "source_hash") if key not in spec
    ]
    if missing_markers:
        raise AssemblyError(
            "assemble_episode requires a normalized spec; missing derived field(s): "
            + ", ".join(missing_markers)
        )
    root_value = spec.get("output_root")
    if not isinstance(root_value, str) or not root_value.strip():
        raise AssemblyError("normalized spec.output_root must be an absolute path")
    output_root = Path(root_value).expanduser()
    if not output_root.is_absolute():
        raise AssemblyError("normalized spec.output_root must be an absolute path")
    shots = spec.get("shots")
    if not isinstance(shots, list) or not shots:
        raise AssemblyError("normalized spec.shots must be a non-empty list")
    subtitle_language = _subtitle_language(spec)
    required_languages = (
        ("en", "zh") if subtitle_language == "bilingual" else (subtitle_language,)
    )
    for index, shot in enumerate(shots):
        if not isinstance(shot, Mapping):
            raise AssemblyError(f"spec.shots[{index}] must be an object")
        shot_id = shot.get("id")
        if not isinstance(shot_id, str) or _SHOT_ID.fullmatch(shot_id) is None:
            raise AssemblyError(f"shot id is unsafe for a media filename: {shot_id!r}")
        lines = shot.get("lines")
        if not isinstance(lines, list):
            raise AssemblyError(
                f"shot {shot_id}.lines is not normalized; load the spec with load_spec"
            )
        for line_index, line in enumerate(lines):
            if not isinstance(line, Mapping):
                raise AssemblyError(f"shot {shot_id}.lines[{line_index}] must be an object")
            for language in required_languages:
                text = line.get(language)
                if not isinstance(text, str) or not text.strip():
                    raise AssemblyError(
                        f"shot {shot_id}.lines[{line_index}].{language} is empty"
                    )
    return output_root.resolve(), shots


def _validate_source(
    path: Path,
    shot: Mapping[str, Any],
    *,
    ffprobe: str,
) -> MediaInfo:
    shot_id = str(shot["id"])
    if not path.is_file():
        raise AssemblyError(f"missing source video for shot {shot_id}: {path}")
    try:
        return validate_media(
            path,
            "video",
            require_audio=True,
            expected_duration_seconds=float(shot["dur"]),
            duration_tolerance_seconds=min(
                2.0, max(0.75, float(shot["dur"]) * 0.2)
            ),
            ffprobe=ffprobe,
        )
    except (MediaValidationError, OSError, TypeError, ValueError) as exc:
        raise AssemblyError(f"invalid source video for shot {shot_id}: {exc}") from exc


def _speech_envelope(
    path: Path,
    *,
    ffmpeg: str,
    timeout_seconds: float,
) -> list[tuple[float, float]]:
    completed = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "aresample=16000,astats=metadata=1:reset=4,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
            "-f",
            "null",
            "-",
        ],
        purpose=f"analyse dialogue timing in {path.name}",
        timeout_seconds=timeout_seconds,
    )
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    times = [float(value) for value in _RMS_TIME.findall(output)]
    levels: list[float] = []
    for value in _RMS_VALUE.findall(output):
        levels.append(float("-inf") if value.lower() == "-inf" else float(value))
    return list(zip(times[: len(levels)], levels[: len(times)]))


def _segments_from_envelope(
    envelope: Sequence[tuple[float, float]],
    *,
    min_gap: float = 0.40,
) -> list[tuple[float, float]]:
    if not envelope:
        return []
    runs: list[tuple[float, float]] = []
    current: tuple[float, float] | None = None
    for timestamp, level in envelope:
        if level > _SPEECH_DB:
            current = (current[0], timestamp) if current else (timestamp, timestamp)
        elif current is not None:
            runs.append(current)
            current = None
    if current is not None:
        runs.append(current)
    step = envelope[1][0] - envelope[0][0] if len(envelope) > 1 else 0.02
    runs = [run for run in runs if run[1] - run[0] >= _MIN_SPEECH_RUN * step]
    merged: list[tuple[float, float]] = []
    for run in runs:
        if merged and run[0] - merged[-1][1] < min_gap:
            merged[-1] = (merged[-1][0], run[1])
        else:
            merged.append(run)
    return merged


def _trim_from_envelope(
    envelope: Sequence[tuple[float, float]], duration_seconds: float
) -> float:
    active = [level > _SPEECH_DB for _, level in envelope]
    onset: float | None = None
    for index in range(max(0, len(active) - _MIN_SPEECH_RUN + 1)):
        if all(active[index : index + _MIN_SPEECH_RUN]):
            onset = envelope[index][0]
            break
    if onset is None:
        return 0.0
    return max(
        0.0,
        min(
            onset - _SPEECH_KEEP_SECONDS,
            duration_seconds * _MAX_TRIM_FRACTION,
        ),
    )


def _group_segments(
    segments: Sequence[tuple[float, float]], count: int
) -> list[tuple[float, float]]:
    grouped = list(segments)
    while len(grouped) > count:
        index = min(
            range(len(grouped) - 1),
            key=lambda item: grouped[item + 1][0] - grouped[item][1],
        )
        grouped[index] = (grouped[index][0], grouped[index + 1][1])
        del grouped[index + 1]
    return grouped


def _line_windows(
    segments: Sequence[tuple[float, float]],
    *,
    cut_seconds: float,
    duration_seconds: float,
    line_count: int,
) -> list[tuple[float, float]]:
    if line_count < 1:
        return []
    clipped = [
        (max(0.0, start - cut_seconds), min(duration_seconds, end - cut_seconds))
        for start, end in segments
        if end > cut_seconds and start < cut_seconds + duration_seconds
    ]
    if len(clipped) > line_count:
        clipped = _group_segments(clipped, line_count)
    if len(clipped) == line_count:
        cuts = [
            (clipped[index][1] + clipped[index + 1][0]) / 2
            for index in range(line_count - 1)
        ]
    else:
        cuts = [duration_seconds * (index + 1) / line_count for index in range(line_count - 1)]
    bounds = [0.0, *cuts, duration_seconds]
    return [(bounds[index], bounds[index + 1]) for index in range(line_count)]


def _explicit_subtitle_windows(
    lines: Sequence[Mapping[str, Any]],
    *,
    cut_seconds: float,
    duration_seconds: float,
) -> list[tuple[float, float]] | None:
    """Return opt-in subtitle windows on the untrimmed source timeline."""

    raw_windows: list[tuple[float, float] | None] = []
    maximum_source_time = cut_seconds + duration_seconds
    for index, line in enumerate(lines):
        has_start = "subtitle_start_seconds" in line
        has_end = "subtitle_end_seconds" in line
        if has_start != has_end:
            raise AssemblyError(
                f"subtitle timing for line {index} must provide "
                "subtitle_start_seconds and subtitle_end_seconds together"
            )
        if not has_start:
            raw_windows.append(None)
            continue
        start = _number(
            line["subtitle_start_seconds"],
            field=f"lines[{index}].subtitle_start_seconds",
            minimum=0,
            maximum=maximum_source_time,
        )
        end = _number(
            line["subtitle_end_seconds"],
            field=f"lines[{index}].subtitle_end_seconds",
            minimum=0,
            maximum=maximum_source_time,
        )
        if end <= start:
            raise AssemblyError(
                f"subtitle timing for line {index} must end after it starts"
            )
        raw_windows.append((start, end))

    if not any(window is not None for window in raw_windows):
        return None
    if any(window is None for window in raw_windows):
        raise AssemblyError(
            "explicit subtitle timing must be provided for every line in a shot"
        )

    complete = [window for window in raw_windows if window is not None]
    for index in range(1, len(complete)):
        if complete[index][0] < complete[index - 1][1]:
            raise AssemblyError(
                "explicit subtitle windows must be ordered and non-overlapping"
            )

    windows = [
        (
            max(0.0, start - cut_seconds),
            min(duration_seconds, end - cut_seconds),
        )
        for start, end in complete
    ]
    if any(end <= start for start, end in windows):
        raise AssemblyError(
            "explicit subtitle window falls outside the effective source clip"
        )
    return windows


def _wrap_text(text: str, maximum_units: int) -> tuple[str, int]:
    """Wrap subtitle text without a runtime Pillow/font dependency."""

    units = text.strip().split() if " " in text.strip() else list(text.strip())
    separator = " " if " " in text.strip() else ""
    lines: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}{separator}{unit}" if current else unit
        # Wide CJK characters count roughly twice an average Latin glyph.
        visual_units = sum(2 if ord(character) > 255 else 1 for character in candidate)
        if current and visual_units > maximum_units:
            lines.append(current)
            current = unit
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > 2:
        # Preserve all dialogue.  Smaller text is preferable to silently
        # dropping a third line; ffmpeg will render the explicit newlines.
        midpoint = max(1, math.ceil(len(units) / 2))
        lines = [separator.join(units[:midpoint]), separator.join(units[midpoint:])]
    return "\n".join(lines), max(1, len(lines))


def _filter_path(path: Path) -> str:
    """滤镜参数里的文件路径，调用方一律包在 ``'...'`` 里（``fontfile='…'`` / ``textfile='…'``）。

    ffmpeg 解析两层：滤镜图层按引号切，引号里原样照抄（反斜杠也照抄），遇到下一个 ' 就闭合；选项层再去掉一层
    ``\\:`` ``\\'`` ``\\\\`` 转义。所以 : 和 \\ 在引号里带着转义传给选项层即可；' 不能待在引号里——先闭合引号，
    在引号外写 ``\\\\\\'``（图层去一层得 ``\\'``，选项层再去一层得 '），再重新开引号。以前写成引号里的 ``\\'``，
    数据根在 C:\\Users\\O'Brien\\… 这种路径下时引号提前闭合，所有带字幕的合成都失败（F4）。"""

    value = path.resolve().as_posix()
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\\\\\''")


def _choose_font(spec: Mapping[str, Any], explicit: str | os.PathLike[str] | None) -> Path:
    configured = explicit
    subtitle = spec.get("subtitle") or {}
    if not isinstance(subtitle, Mapping):
        raise AssemblyError("spec.subtitle must be an object")
    if configured is None:
        configured = subtitle.get("font_path") or subtitle.get("fontfile")
    from .subtitles import FONT_CANDIDATES

    candidates = [Path(configured).expanduser() if configured else None]
    candidates += [Path(path) for path, _label in FONT_CANDIDATES]
    candidates.append(Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    requested = f": {configured}" if configured else ""
    raise AssemblyError(f"no usable subtitle font was found{requested}")


def _subtitle_language(spec: Mapping[str, Any]) -> str:
    subtitle = spec.get("subtitle")
    if subtitle is None:
        return "bilingual"
    if not isinstance(subtitle, Mapping):
        raise AssemblyError("spec.subtitle must be an object")
    language = subtitle.get("language", "bilingual")
    if not isinstance(language, str) or language not in _SUBTITLE_LANGUAGES:
        raise AssemblyError("subtitle.language must be bilingual, zh, or en")
    return language


def _text_file(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def _subtitle_filters(
    *,
    shot_id: str,
    lines: Sequence[Mapping[str, Any]],
    windows: Sequence[tuple[float, float]],
    text_directory: Path,
    font_path: Path,
    width: int,
    height: int,
    band_alpha: float,
    language: str = "bilingual",
    position: str = "bottom",
    english_font_size: float | None = None,
    chinese_font_size: float | None = None,
    english_color: str = "0xFFD84D",
    chinese_color: str = "0xFFFFFF",
    top_margin_ratio: float = 0.12,
    outline_color: str = "0x111111",
    outline_width: float | None = None,
    safe_bottom_percent: float | None = None,
) -> list[str]:
    scale = width / _DEFAULT_WIDTH
    english_size = max(
        24,
        int(round(64 * scale if english_font_size is None else english_font_size * scale)),
    )
    chinese_size = max(
        20,
        int(round(46 * scale if chinese_font_size is None else chinese_font_size * scale)),
    )
    gap = max(4, int(round(10 * scale)))
    # 底部边距：spec 给了百分比就按百分比，否则沿用老的 300/1920
    if safe_bottom_percent is not None:
        bottom = max(20, int(round(height * float(safe_bottom_percent) / 100.0)))
    else:
        bottom = max(100, int(round(300 * height / _DEFAULT_HEIGHT)))
    if outline_width is not None:
        border_english = max(0, int(round(float(outline_width) * 1.2 * scale)))
        border_chinese = max(0, int(round(float(outline_width) * scale)))
    else:
        border_english = max(2, int(round(6 * scale)))
        border_chinese = max(2, int(round(5 * scale)))
    border_color = f"{outline_color}@0.95"
    font = _filter_path(font_path)
    filters: list[str] = []
    for index, (line, window) in enumerate(zip(lines, windows, strict=True)):
        layers: list[tuple[str, int, int, str, int, int, int, str]] = []
        if language in {"bilingual", "en"}:
            # Picture-book style layouts use large type.  Keep each English line
            # inside the 1080px title-safe area even when the configured size is
            # around 100px; the old 30-unit limit clipped several short sentences.
            english_units = max(
                12,
                min(30, int(round((width * 0.88) / max(1.0, english_size * 0.55)))),
            )
            english, english_lines = _wrap_text(
                str(line["en"]), maximum_units=english_units
            )
            english_path = _filter_path(
                _text_file(text_directory, f"{shot_id}.en.{index}.txt", english)
            )
            layers.append(
                (
                    english_path,
                    english_size,
                    int(english_size * 1.20) * english_lines,
                    english_color,
                    border_english,
                    8,
                    3,
                    "0.75",
                )
            )
        if language in {"bilingual", "zh"}:
            chinese, chinese_lines = _wrap_text(str(line["zh"]), maximum_units=38)
            chinese_path = _filter_path(
                _text_file(text_directory, f"{shot_id}.zh.{index}.txt", chinese)
            )
            layers.append(
                (
                    chinese_path,
                    chinese_size,
                    int(chinese_size * 1.20) * chinese_lines,
                    chinese_color,
                    border_chinese,
                    6,
                    2,
                    "0.7",
                )
            )
        block_height = sum(layer[2] for layer in layers) + gap * (len(layers) - 1)
        block_y = (
            max(0, int(round(height * top_margin_ratio)))
            if position == "top"
            else height - bottom - block_height
        )
        start, end = window
        enable = f"enable='between(t,{start:.3f},{end:.3f})'"
        filters.append(
            f"drawbox=x=0:y={block_y-16}:w={width}:h={block_height+32}:"
            f"color=black@{band_alpha:.3f}:t=fill:{enable}"
        )
        layer_y = block_y
        for (
            text_path,
            size,
            text_height,
            color,
            border,
            spacing,
            shadow_y,
            shadow_alpha,
        ) in layers:
            # expansion=none：台词是数据不是模板。默认的 normal 会把 textfile 的内容按 %{...} / \ 展开——含 % 的一句
            # （「打了50%折扣」）报 Stray % 整条不画而 ffmpeg 仍退出 0，\ 被吞，%{pts} 变成时间戳（F2）
            filters.append(
                f"drawtext=fontfile='{font}':textfile='{text_path}':expansion=none:"
                f"fontcolor={color}:fontsize={size}:borderw={border}:"
                f"bordercolor={border_color}:shadowx=0:shadowy={shadow_y}:"
                f"shadowcolor=black@{shadow_alpha}:"
                f"x=(w-text_w)/2:y={layer_y}:line_spacing={spacing}:{enable}"
            )
            layer_y += text_height + gap
    return filters


# 一条拼接命令最多并行接几路视频（2026-09-22 发第一个 tag 前补的）。ffmpeg 会同时解码一条命令里的全部输入，后面
# 各路的帧在滤镜图里排队等链前面的节点消费，峰值内存随路数线性涨（1080x1920 实测装配版 ≈0.45 GB/镜、后期版
# ≈0.83 GB/镜，16 GB 的机器十几二十镜就 OOM）。超过这个数就先分组拼成中间片、再把中间片往上接（层级式），
# 每一级的内存都被组大小封顶。后期层（post_layers）的分段拼接用同一个数和同一种中间片编码。
JOIN_GROUP = 8
# 中间片的编码：veryfast + crf 16 视觉上近乎无损；音频 24 位 PCM，不多一次 AAC 损失（mov 装得下 PCM，mp4 装不下）。
# 最后一级才按交付口径编码，与分组之前完全一样。
_INTERMEDIATE_ENCODE = ("-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "pcm_s24le")


def join_inputs(paths: Sequence[Path | str], *, crossfade: bool) -> list[str]:
    """一条拼接命令的 ``-i`` 列表。有交叉溶解（``crossfade``）时同一批文件再开一遍：前一半只取画面（xfade 链），
    后一半只取声音（acrossfade 链，滤镜图里音轨的输入号从 ``len(paths)`` 起）；无交叉溶解走 concat，每个文件开一遍。

    为什么要开两遍：ffmpeg 6.0 的 xfade 走到 offset、第二路还没有帧时，只要第一路的上游已经报了 EOF（哪怕剩下的溶解帧
    还排在链路里）就判定「溶解结束」，把第一路剩下的帧丢掉——这个接缝硬切。acrossfade 要先等第一路的声音读到 EOF 才溶，
    音画从同一个输入读时，为了声音把上一段整个读完，上一段剩下的画面帧就连同 EOF 一起推进了 xfade 链，下一段的画面却还没
    读——于是硬切。黑白交替片实测：音画同一个输入时第 5 个接缝起全是硬切（改分段之前的装配版 12 镜里第 9、10 个接缝也是），
    画面和声音各读各的文件实例之后一个都没有，帧数也与时间线一致。声音那一半只解音轨，不多解码一路画面。"""

    inputs: list[str] = []
    for path in paths:
        inputs.extend(["-i", str(path)])
    if crossfade:
        for path in paths:
            inputs.extend(["-i", str(path)])
    return inputs


def _join_graph(
    clips: Sequence[_Clip],
    crossfade_seconds: float,
    *,
    trim_to: float | None = None,
) -> tuple[str, str, str]:
    """拼接一组已渲染片段的滤镜图，返回 (filter_complex, 视频出口, 音频出口)；输入按 ``join_inputs`` 的顺序给。

    无交叉溶解走 concat（每段按最长的那条流对齐）；否则一条 xfade / acrossfade 链，offset 按各段实际时长累加，
    声音从后一半输入读（见 ``join_inputs``：音画同一个输入时 ffmpeg 6.0 的 xfade 会在接缝硬切）。
    ``trim_to`` 只给中间片用：xfade 报的结束时间比最后一帧晚，写进中间片时恒定帧率会在末尾补一帧，分几级就多几帧；
    链尾截到这一组应有的长度，接出来与一条链直接进成片逐帧一样。"""

    if crossfade_seconds == 0:
        joined_inputs = "".join(f"[{index}:v][{index}:a]" for index in range(len(clips)))
        return f"{joined_inputs}concat=n={len(clips)}:v=1:a=1[vout][aout]", "vout", "aout"
    audio_base = len(clips)
    video_chain: list[str] = []
    audio_chain: list[str] = []
    previous_video = "0:v"
    previous_audio = f"{audio_base}:a"
    offset = 0.0
    for index in range(1, len(clips)):
        offset += clips[index - 1].duration_seconds - crossfade_seconds
        tail = f",trim=duration={trim_to:.3f}" if trim_to is not None and index == len(clips) - 1 else ""
        video_chain.append(
            f"[{previous_video}][{index}:v]xfade=transition=fade:"
            f"duration={crossfade_seconds:.3f}:offset={offset:.3f}{tail}[v{index}]"
        )
        audio_chain.append(
            f"[{previous_audio}][{audio_base + index}:a]acrossfade=d={crossfade_seconds:.3f}:"
            f"c1=tri:c2=tri[a{index}]"
        )
        previous_video = f"v{index}"
        previous_audio = f"a{index}"
    return ";".join([*video_chain, *audio_chain]), previous_video, previous_audio


def _join_levels(
    clips: Sequence[_Clip],
    *,
    temporary: Path,
    crossfade_seconds: float,
    fps: float,
    ffmpeg: str,
    ffprobe: str,
    timeout: float,
) -> list[_Clip]:
    """段数超过 ``JOIN_GROUP`` 时先按组拼成中间片，一级一级往上，直到剩下的段一条命令接得下；返回最后一级要接的段。

    不超过 ``JOIN_GROUP`` 段时原样返回，最后那条拼接命令与分组之前同一形状（只多了 ``join_inputs`` 给声音开的那一半输入）。"""

    level = list(clips)
    depth = 0
    while len(level) > JOIN_GROUP:
        depth += 1
        next_level: list[_Clip] = []
        for group_index, start in enumerate(range(0, len(level), JOIN_GROUP)):
            group = level[start:start + JOIN_GROUP]
            if len(group) == 1:
                next_level.append(group[0])
                continue
            length = sum(clip.duration_seconds for clip in group) - crossfade_seconds * (len(group) - 1)
            graph, video, audio = _join_graph(
                group, crossfade_seconds, trim_to=length if crossfade_seconds > 0 else None
            )
            destination = temporary / f"join_{depth}_{group_index:03d}.mov"
            _run(
                [
                    ffmpeg, "-y", "-loglevel", "error", *join_inputs([clip.path for clip in group], crossfade=crossfade_seconds > 0),
                    "-filter_complex", graph, "-map", f"[{video}]", "-map", f"[{audio}]",
                    *_INTERMEDIATE_ENCODE, "-r", f"{fps:g}", str(destination),
                ],
                purpose=f"join rendered shots (level {depth}, group {group_index + 1})",
                timeout_seconds=timeout,
            )
            try:
                info = validate_media(destination, "video", require_audio=True, ffprobe=ffprobe)
            except (MediaValidationError, OSError, TypeError, ValueError) as exc:
                raise AssemblyError(f"joined intermediate {destination.name} is invalid: {exc}") from exc
            # 下一级的 xfade offset 用这一组算出来的长度（= 一条链时各段时长减重叠的累加，逐帧同一个接缝），不用中间片的
            # 容器时长：acrossfade 出来的音轨常比画面长零点几秒，容器时长跟着音轨走，拿它算会把后一组整体往后推
            next_level.append(_Clip(destination, length if crossfade_seconds > 0 else info.duration_seconds))
        level = next_level
    return level


def _sync_file(path: Path) -> None:
    try:
        with path.open("r+b") as stream:
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise AssemblyError(f"could not sync completed artifact {path}: {exc}") from exc


def _temporary_destination(destination: Path) -> Path:
    suffix = destination.suffix or ".mp4"
    return destination.with_name(
        f".{destination.stem}.{uuid.uuid4().hex}.tmp{suffix}"
    )


# 成品换上去时上一版正被别的进程开着读：界面上的播放器在流式放它（服务端边读边发、浏览器按缓冲节奏慢慢收，连接一直开着），
# 或者本机播放器开着它。Windows 上 os.replace 会被拒（WinError 5 / 32）——以前 ffmpeg -y 原地覆盖写得进去，改成「临时文件 +
# 原子替换」之后才冒出来。多半一两秒就放开了（缓冲够了、播放器切走），先等一会儿再试；还占着就报一句能照做的话，上一版原样留着。
REPLACE_RETRY_SECONDS = 5.0
FINAL_BUSY_MESSAGE = "上一版成品正被占用（多半是页面上的播放器还开着它），新版没能换上去——刷新页面或关掉播放器后再合成一次"


def replace_final(temporary: Path, destination: Path, *, error: type[Exception] = AssemblyError) -> None:
    """把核过的临时文件原子替换成成品；目标被占用时（只有 Windows 会这样）等 ``REPLACE_RETRY_SECONDS`` 秒再放弃，
    放弃时抛 ``error(FINAL_BUSY_MESSAGE)``（播客混音传 AudioError，失败记进 mix_error、不算任务失败）。临时文件由调用方清。"""

    delay = 0.1
    deadline = time.monotonic() + REPLACE_RETRY_SECONDS
    while True:
        try:
            os.replace(temporary, destination)
            return
        except PermissionError as exc:
            if os.name != "nt":
                raise
            if time.monotonic() >= deadline:
                raise error(FINAL_BUSY_MESSAGE) from exc
            time.sleep(delay)
            delay = min(delay * 2, 1.0)


def _final_name(spec: Mapping[str, Any], output_root: Path) -> str:
    episode_id = spec.get("id")
    if not isinstance(episode_id, str) or not episode_id.strip():
        episode_id = output_root.name
    if _SHOT_ID.fullmatch(episode_id) is None:
        raise AssemblyError(f"episode id is unsafe for an output filename: {episode_id!r}")
    return f"{episode_id}_final.mp4"


def assemble_episode(
    spec: Mapping[str, Any],
    *,
    video_dir: str | os.PathLike[str] | None = None,
    final_path: str | os.PathLike[str] | None = None,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    font_path: str | os.PathLike[str] | None = None,
    create_contact_sheet: bool = False,
    contact_sheet_path: str | os.PathLike[str] | None = None,
    trim_head_fn: Callable[[str, float], float] | None = None,
    speech_segments_fn: Callable[[str], Sequence[tuple[float, float]]] | None = None,
    timeout_seconds: float = 600.0,
) -> AssemblyResult:
    """Assemble a normalized episode spec into one delivery-ready MP4.

    Source clips default to ``<output_root>/02_video/<shot id>.mp4`` and the
    final defaults to ``<output_root>/<episode id>_final.mp4``.  The default
    delivery is 1080x1920 at 24 fps.  ``spec.video_params`` may override those
    values and ``spec.delivery`` takes final precedence.

    A successful ffmpeg exit alone is never accepted.  Every source, rendered
    clip, final temporary file and optional contact sheet is probed before the
    final artifact is installed with :func:`os.replace`.
    """

    output_root, shots = _require_normalized_spec(spec)
    width, height, fps, crossfade_seconds = _delivery_settings(spec)
    maxrate_kbps, bufsize_kbps = _delivery_bitrate_settings(spec)
    timeout = _number(
        timeout_seconds,
        field="timeout_seconds",
        minimum=1,
        maximum=86400,
    )
    source_root = (
        Path(video_dir).expanduser().resolve()
        if video_dir is not None
        else output_root / "02_video"
    )
    destination = (
        Path(final_path).expanduser().resolve()
        if final_path is not None
        else output_root / _final_name(spec, output_root)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if contact_sheet_path is not None and not create_contact_sheet:
        raise AssemblyError(
            "contact_sheet_path requires create_contact_sheet=True"
        )
    qc_destination = (
        Path(contact_sheet_path).expanduser().resolve()
        if contact_sheet_path is not None
        else output_root / "x_final_qc.jpg"
    )
    selected_font = _choose_font(spec, font_path)
    subtitle = spec.get("subtitle") or {}
    subtitle_language = _subtitle_language(spec)
    band_alpha = _number(
        subtitle.get("band_alpha", 0.35),
        field="subtitle.band_alpha",
        minimum=0,
        maximum=1,
    )
    subtitle_position = str(subtitle.get("position") or "bottom").strip().lower()
    if subtitle_position not in {"top", "bottom"}:
        raise AssemblyError("subtitle.position must be top or bottom")
    subtitle_display = str(subtitle.get("display") or "speech").strip().lower()
    if subtitle_display not in {"speech", "full_shot"}:
        raise AssemblyError("subtitle.display must be speech or full_shot")
    english_font_size = _number(
        subtitle.get("english_font_size", 64),
        field="subtitle.english_font_size",
        minimum=12,
        maximum=300,
    )
    chinese_font_size = _number(
        subtitle.get("chinese_font_size", 46),
        field="subtitle.chinese_font_size",
        minimum=12,
        maximum=300,
    )
    top_margin_ratio = _number(
        subtitle.get("top_margin_ratio", 0.12),
        field="subtitle.top_margin_ratio",
        minimum=0,
        maximum=0.70,
    )

    def subtitle_color(field: str, default: str) -> str:
        value = str(subtitle.get(field) or default).strip()
        if _SUBTITLE_COLOR.fullmatch(value) is None:
            raise AssemblyError(f"subtitle.{field} must be 0xRRGGBB or #RRGGBB")
        return "0x" + value[-6:].upper()

    english_color = subtitle_color("english_color", "0xFFD84D")
    chinese_color = subtitle_color("chinese_color", "0xFFFFFF")
    outline_color = subtitle_color("outline_color", "0x111111")
    outline_width = (
        _number(subtitle.get("outline_width"), field="subtitle.outline_width", minimum=0, maximum=20)
        if subtitle.get("outline_width") is not None else None
    )
    safe_bottom_percent = (
        _number(subtitle.get("safe_bottom_percent"), field="subtitle.safe_bottom_percent", minimum=0, maximum=40)
        if subtitle.get("safe_bottom_percent") is not None else None
    )

    sources: list[tuple[Mapping[str, Any], Path, MediaInfo]] = []
    for shot in shots:
        source = (source_root / f"{shot['id']}.mp4").resolve()
        try:
            source.relative_to(source_root)
        except ValueError as exc:
            raise AssemblyError(f"source path escapes video directory: {source}") from exc
        info = _validate_source(source, shot, ffprobe=ffprobe)
        sources.append((shot, source, info))

    final_temporary = _temporary_destination(destination)
    qc_temporary = _temporary_destination(qc_destination) if create_contact_sheet else None
    clip_results: list[_Clip] = []
    try:
        with tempfile.TemporaryDirectory(prefix=".assembly-", dir=output_root) as temporary_name:
            temporary = Path(temporary_name)
            text_directory = temporary / "subtitles"
            text_directory.mkdir()

            for shot, source, source_info in sources:
                shot_id = str(shot["id"])
                lines = list(shot["lines"])
                planned_duration = float(shot["dur"])
                usable_duration = min(planned_duration, source_info.duration_seconds)
                envelope: list[tuple[float, float]] = []
                if lines and (trim_head_fn is None or speech_segments_fn is None):
                    envelope = _speech_envelope(
                        source,
                        ffmpeg=ffmpeg,
                        timeout_seconds=timeout,
                    )
                if lines:
                    if trim_head_fn is not None:
                        cut_seconds = float(trim_head_fn(str(source), planned_duration))
                    else:
                        cut_seconds = _trim_from_envelope(envelope, usable_duration)
                    if speech_segments_fn is not None:
                        segments = list(speech_segments_fn(str(source)))
                    else:
                        segments = _segments_from_envelope(envelope)
                else:
                    cut_seconds = 0.0
                    segments = []
                if not math.isfinite(cut_seconds) or not 0 <= cut_seconds < usable_duration:
                    raise AssemblyError(
                        f"shot {shot_id} head trim is outside its usable duration: "
                        f"{cut_seconds!r}"
                    )
                effective_duration = usable_duration - cut_seconds
                if effective_duration <= crossfade_seconds and len(shots) > 1:
                    raise AssemblyError(
                        f"shot {shot_id} is not longer than the {crossfade_seconds:.3f}s crossfade"
                    )
                windows = _line_windows(
                    segments,
                    cut_seconds=cut_seconds,
                    duration_seconds=effective_duration,
                    line_count=len(lines),
                )
                explicit_windows = _explicit_subtitle_windows(
                    lines,
                    cut_seconds=cut_seconds,
                    duration_seconds=effective_duration,
                )
                if subtitle_display == "speech" and explicit_windows is not None:
                    windows = explicit_windows
                if subtitle_display == "full_shot":
                    windows = [(0.0, effective_duration) for _line in lines]
                video_filters = [
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
                    "setsar=1",
                    f"fps={fps:g}",
                    "format=yuv420p",
                    # 画面定格补到本镜时长（tpad=clone）：usable_duration 取的是**容器**时长，音轨比画面长的源片
                    # （导入的外部片子、别家模型的出片）画面会短这一截，而下面 _join_levels 的 xfade offset 是按
                    # 各段时长累加的——offset 落在前一段画面 EOF 之后，这一组剩下的画面就整段丢掉。画面本来够长时
                    # 补出来的帧全被输出的 -t 截掉，产物与补之前逐帧相同。同一条口径见 post_layers._normalize_item_command
                    f"tpad=stop_mode=clone:stop_duration={effective_duration:.3f}",
                ]
                video_filters.extend(
                    _subtitle_filters(
                        shot_id=shot_id,
                        lines=lines,
                        windows=windows,
                        text_directory=text_directory,
                        font_path=selected_font,
                        width=width,
                        height=height,
                        band_alpha=band_alpha,
                        language=subtitle_language,
                        position=subtitle_position,
                        english_font_size=english_font_size,
                        chinese_font_size=chinese_font_size,
                        english_color=english_color,
                        chinese_color=chinese_color,
                        top_margin_ratio=top_margin_ratio,
                        outline_color=outline_color,
                        outline_width=outline_width,
                        safe_bottom_percent=safe_bottom_percent,
                    )
                )
                rendered = temporary / f"{shot_id}.mp4"
                _run(
                    [
                        ffmpeg,
                        "-y",
                        "-loglevel",
                        "error",
                        "-ss",
                        f"{cut_seconds:.3f}",
                        "-i",
                        str(source),
                        "-vf",
                        ",".join(video_filters),
                        "-r",
                        f"{fps:g}",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:v",
                        "libx264",
                        "-crf",
                        "19",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        "-t",
                        f"{effective_duration:.3f}",
                        str(rendered),
                    ],
                    purpose=f"render shot {shot_id}",
                    timeout_seconds=timeout,
                )
                try:
                    rendered_info = validate_media(
                        rendered,
                        "video",
                        require_audio=True,
                        expected_duration_seconds=effective_duration,
                        duration_tolerance_seconds=0.75,
                        expected_dimensions=(width, height),
                        ffprobe=ffprobe,
                    )
                except (MediaValidationError, OSError, TypeError, ValueError) as exc:
                    raise AssemblyError(
                        f"rendered media for shot {shot_id} is invalid: {exc}"
                    ) from exc
                clip_results.append(_Clip(rendered, rendered_info.duration_seconds))

            if len(clip_results) == 1:
                joined = clip_results[0].path
                expected_duration = clip_results[0].duration_seconds
            else:
                joined = temporary / "joined.mp4"
                # 镜数多时先分组拼成中间片（见 JOIN_GROUP）：任何一条拼接命令最多并行解码 JOIN_GROUP 路
                final_level = _join_levels(
                    clip_results,
                    temporary=temporary,
                    crossfade_seconds=crossfade_seconds,
                    fps=fps,
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                    timeout=timeout,
                )
                inputs = join_inputs([clip.path for clip in final_level], crossfade=crossfade_seconds > 0)
                filter_complex, previous_video, previous_audio = _join_graph(
                    final_level, crossfade_seconds
                )
                _run(
                    [
                        ffmpeg,
                        "-y",
                        "-loglevel",
                        "error",
                        *inputs,
                        "-filter_complex",
                        filter_complex,
                        "-map",
                        f"[{previous_video}]",
                        "-map",
                        f"[{previous_audio}]",
                        "-r",
                        f"{fps:g}",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:v",
                        "libx264",
                        "-crf",
                        "21",
                        "-maxrate",
                        f"{maxrate_kbps}k",
                        "-bufsize",
                        f"{bufsize_kbps}k",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "160k",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(joined),
                    ],
                    purpose="join rendered shots",
                    timeout_seconds=timeout,
                )
                expected_duration = sum(
                    clip.duration_seconds for clip in clip_results
                ) - crossfade_seconds * (len(clip_results) - 1)

            _run(
                [
                    ffmpeg,
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(joined),
                    "-af",
                    "loudnorm=I=-16:TP=-1.5:LRA=9",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-r",
                    f"{fps:g}",
                    "-movflags",
                    "+faststart",
                    str(final_temporary),
                ],
                purpose="normalise final audio",
                timeout_seconds=timeout,
            )
            try:
                final_info = validate_media(
                    final_temporary,
                    "video",
                    require_audio=True,
                    expected_duration_seconds=expected_duration,
                    duration_tolerance_seconds=max(0.75, expected_duration * 0.02),
                    expected_dimensions=(width, height),
                    ffprobe=ffprobe,
                )
            except (MediaValidationError, OSError, TypeError, ValueError) as exc:
                raise AssemblyError(f"assembled final media is invalid: {exc}") from exc
            if final_info.sample_rate != 48000:
                raise AssemblyError(
                    "assembled final media has the wrong audio sample rate: "
                    f"expected 48000 Hz, got {final_info.sample_rate!r}"
                )

            if create_contact_sheet:
                assert qc_temporary is not None
                qc_temporary.parent.mkdir(parents=True, exist_ok=True)
                interval = max(0.1, final_info.duration_seconds / 6.5)
                _run(
                    [
                        ffmpeg,
                        "-y",
                        "-loglevel",
                        "error",
                        "-i",
                        str(final_temporary),
                        "-vf",
                        "select='isnan(prev_selected_t)+"
                        f"gte(t-prev_selected_t\\,{interval:.3f})',"
                        "scale=360:-2,tile=3x2",
                        "-frames:v",
                        "1",
                        str(qc_temporary),
                    ],
                    purpose="generate final QC contact sheet",
                    timeout_seconds=timeout,
                )
                try:
                    validate_media(qc_temporary, "image", min_size_bytes=1, ffprobe=ffprobe)
                except (MediaValidationError, OSError, TypeError, ValueError) as exc:
                    raise AssemblyError(f"final QC contact sheet is invalid: {exc}") from exc

            installed_qc: Path | None = None
            if create_contact_sheet:
                assert qc_temporary is not None
                _sync_file(qc_temporary)
                replace_final(qc_temporary, qc_destination)
                installed_qc = qc_destination
            # Install the delivery artifact last.  If publishing the optional
            # evidence image fails, the previous known-good final remains in
            # place instead of being replaced by a half-committed assembly run.
            _sync_file(final_temporary)
            replace_final(final_temporary, destination)

            return AssemblyResult(
                final_path=destination,
                duration_seconds=final_info.duration_seconds,
                width=width,
                height=height,
                fps=fps,
                clip_paths=tuple(source for _, source, _ in sources),
                contact_sheet_path=installed_qc,
            )
    finally:
        final_temporary.unlink(missing_ok=True)
        if qc_temporary is not None:
            qc_temporary.unlink(missing_ok=True)


__all__ = [
    "AssemblyError",
    "AssemblyResult",
    "assemble_episode",
    "delivery_settings",
    "preflight_assembly",
]
