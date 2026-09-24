"""上传文件的内容嗅探：按字节判断真实格式，落盘后缀也按真实格式定。

只看文件名扩展名的话，``this is not a png`` 名叫 x.png、16 字节文本名叫 a.mp3 都会 200 落盘并写进项目文件，
要到出图 / 出片 / 装配那一步才炸（单条视频还会把它当首帧参考图交给付费模型）。短剧的定妆图 / 图片集 / 单条视频 /
绘本 / 配乐参考图，以及播客音乐、短剧配乐、绘本 BGM 与音效、音色参考音频统一走这里。

* ``accept_image``：PIL 打开 + verify，返回要落盘的字节、后缀与（转正之后的）宽高；认不出来或格式不在接受范围内抛
  ``UploadContentError``。手机照片要处理三件事（纪念写真的用户基本是用手机传照片）：
  相机 / 手机直出、带 MPF 索引的 JPEG（PIL 认成 MPO）取第一帧另存为标准 JPEG；带 EXIF 方向的照片把方向转进像素
  （竖拍的照片不能被存成横的——模型接口和后处理多半不看 EXIF）；HEIC（苹果手机默认格式）PIL 读得了就转成 JPEG，
  读不了给「请在手机上导出为 JPG」的提示。``sniff_image`` 是只要 (后缀, 宽, 高) 的简写。
* ``accept_audio``：按内容认音频。本机有 ffprobe 就让它读：读不出、没有音频流、时长为 0 都拒；读得出但容器 / 编码不在
  接受范围内（AIFF、浏览器录的 webm、wma、带画面的 mp4…）用 ffmpeg 只取音轨转成 wav（PCM 源）或 m4a（其余）收——
  扩展名不再是拒收的理由（AIFF 内容叫 .wav 的照收）；转出来的大小有上限，超了 413。没有 ffprobe 才退回只看文件头（``sniff_audio``）。
* ``sniff_audio``：只看文件头（ID3 / MPEG 帧同步 / RIFF(RF64 / BW64)-WAVE / FORM-AIFF / fLaC / OggS / ftyp / ADTS）。
* ``same_content_index``：参考图列表按内容摘要查重（同一张图换了文件名再传，不再多记一条）。
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, NamedTuple

# PIL 的 format 名 → 落盘后缀（三处图片上传接受的集合；配乐参考图只要前两种）
IMAGE_FORMATS: dict[str, str] = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
_FORMAT_LABELS = {"PNG": "PNG", "JPEG": "JPEG", "WEBP": "WebP"}

# 扩展名闸门要放行、交给内容判断的图片扩展名。HEIC / HEIF 是苹果手机默认的照片格式：在扩展名这一关就拒的话，
# 用户只会看到「只接受 png / jpg…」，不知道该怎么办；放过去由 accept_image 转 JPEG 或给出「导出为 JPG」的提示
HEIF_EXTENSIONS = ("heic", "heif", "hif")
HEIF_HINT = ("这是 HEIC 格式的照片（苹果手机默认的格式），这里读不了——请在手机上导出为 JPG 再传"
             "（iPhone 可在「设置 → 相机 → 格式」里选「兼容性最佳」，之后拍的就是 JPG）")
# ISO BMFF 文件头 ftyp 里的品牌：HEIC / HEIF 图片（mif1 / msf1 是 HEIF 的通用品牌）
_HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1")
# 要转成标准 JPEG 再收的照片格式（PIL 的 format 名）：MPO 是带 MPF 索引的多帧 JPEG；HEIF / AVIF 装了插件 PIL 才认得
_PHOTO_FORMATS = ("MPO", "HEIF", "HEIC", "AVIF")
_EXIF_ORIENTATION = 0x0112

# ffprobe 的 format_name 是逗号分隔的一串别名（m4a 报 mov,mp4,m4a,…；RF64 / BW64 也报 wav）：按别名认落盘后缀
_AUDIO_CONTAINERS = {"mp3": ".mp3", "wav": ".wav", "mov": ".m4a", "mp4": ".m4a", "m4a": ".m4a", "aac": ".aac",
                     "ogg": ".ogg", "flac": ".flac"}
# 容器对了还不够，原样存还要求编码跟后缀相符：mov / mp4 / 3gp 都报成 .m4a，手机录的视频（带画面、AMR 音轨）、
# ALAC 的 m4a、装着 mp3 / ADPCM 的 wav 原样存下，浏览器试听放不出、音色克隆被服务商拒。不相符的走转换
_AUDIO_CODECS = {".mp3": ("mp3",), ".wav": ("pcm_",), ".m4a": ("aac",), ".aac": ("aac",),
                 ".ogg": ("vorbis", "opus", "flac"), ".flac": ("flac",)}
# 转换出来的 AAC 码率（字节 / 秒）：192kbps 听不出损失，一分钟约 1.4MB——转 wav 一分钟就是 10MB（立体声 44.1kHz），
# 一小时 26MB 的 webm 录音转成 wav 是 659MB，整份读进内存
_AAC_BITRATE = "192k"
_AAC_BYTES_PER_SECOND = 192_000 // 8
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)     # 打包版是窗口程序：起 ffprobe / ffmpeg 别闪黑框


class UploadContentError(ValueError):
    """内容认不出来 / 不是接受的格式（路由翻成 ``status``：多数是 400，转换后超过上限是 413）。"""

    def __init__(self, message: str, *, detected: str = "", status: int = 400) -> None:
        super().__init__(message)
        self.detected = detected   # 认出来但不接受时：真实格式名（调用方想换自己的措辞时用）
        self.status = status


class AcceptedImage(NamedTuple):
    data: bytes      # 要落盘的字节：多数就是上传的原样；MPO / HEIC / 带 EXIF 方向的照片是重新编码过的
    suffix: str
    width: int       # 转正之后的宽高
    height: int


class AcceptedAudio(NamedTuple):
    data: bytes      # 要落盘的字节：容器与编码都在接受范围内就是原样，否则是 ffmpeg 转出来的 wav / m4a
    suffix: str
    duration: float  # 秒；没探测过（本机没有 ffprobe）是 0
    format: str      # 真实格式：ffprobe 的 format_name，或按文件头认出的后缀


def _is_heif(data: bytes) -> bool:
    return len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS


def _orientation(image: Any) -> int:
    """EXIF 方向（2–8 要转）；没有、坏了、值不认识都按 1（不用转）——别因为元数据拒掉一张能看的图。"""

    try:
        value = int(image.getexif().get(_EXIF_ORIENTATION) or 1)
    except Exception:  # noqa: BLE001 - PIL 读坏 EXIF 抛的类型五花八门
        return 1
    return value if 2 <= value <= 8 else 1


def _encode(image: Any, fmt: str, icc: bytes | None) -> bytes:
    """重新编码成 ``fmt``。EXIF 不带过去：方向已经转进像素（再带着 Orientation，看图软件会再转一次）；
    手机照片里的 GPS 位置也不该跟着参考图发给模型服务商。ICC 色彩配置带上，免得偏色。"""

    extra = {"icc_profile": icc} if icc else {}
    buffer = io.BytesIO()
    if fmt == "JPEG":
        if image.mode not in ("RGB", "L", "CMYK"):
            image = image.convert("RGB")
        image.save(buffer, "JPEG", quality=95, **extra)
    elif fmt == "WEBP":
        image.save(buffer, "WEBP", quality=95, **extra)
    else:
        image.save(buffer, fmt, **extra)
    return buffer.getvalue()


def _unreadable_image(exc: BaseException) -> UploadContentError:
    return UploadContentError(f"不是可读的图片文件：{str(exc).strip() or type(exc).__name__}")


def accept_image(data: bytes, *, allowed: Mapping[str, str] = IMAGE_FORMATS) -> AcceptedImage:
    """验图并定下要落盘的字节。解析失败 → 「不是可读的图片文件」（HEIC 读不了 → 「请在手机上导出为 JPG」）；
    格式不在 ``allowed`` 里 → 「只接受 …，收到的是 X」。MPO / HEIC 转成 JPEG（要求 ``allowed`` 收 JPEG），带 EXIF 方向的转正。"""

    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        image = Image.open(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - 任何解析失败都按「不是图片」处理（PIL 对坏文件抛的类型五花八门）
        if _is_heif(data):
            # 没装 HEIF 插件（打包版就没有）或插件也解不开：说清怎么办。不带 detected——调用方拿 detected 拼
            # 「只接受 …，收到的是 X」，会把「导出为 JPG」这句要紧的话吞掉
            raise UploadContentError(HEIF_HINT) from exc
        if isinstance(exc, UnidentifiedImageError):
            raise UploadContentError("不是可读的图片文件：认不出图片格式") from exc
        raise _unreadable_image(exc) from exc
    with image:
        fmt = str(image.format or "").upper()
        # MPO：文件头就是标准 JPEG，第一帧是主图（后面是预览图或增益图），当 JPEG 收——否则婚纱 / 写真这类恰恰最常见的
        # 相机原片会被拒，还告诉用户「收到的是 MPO」。但不能原样存：多帧索引留在文件里，有的解码器 / 模型接口会读错帧
        target = "JPEG" if fmt in _PHOTO_FORMATS else fmt
        suffix = allowed.get(target)
        if not suffix:
            accepted = " / ".join(_FORMAT_LABELS.get(name, name) for name in allowed)
            raise UploadContentError(f"只接受 {accepted}，收到的是 {fmt or '未知格式'}", detected=fmt or "未知格式")
        width, height = image.size
        if target == fmt and _orientation(image) == 1:
            return AcceptedImage(data, suffix, int(width), int(height))
        try:
            image.seek(0)
            upright = ImageOps.exif_transpose(image)
            encoded = _encode(upright, target, image.info.get("icc_profile"))
        except Exception as exc:  # noqa: BLE001 - verify 过了但解码 / 重编码失败（截断的文件）：同样按读不了处理
            raise _unreadable_image(exc) from exc
        return AcceptedImage(encoded, suffix, int(upright.width), int(upright.height))


def sniff_image(data: bytes, *, allowed: Mapping[str, str] = IMAGE_FORMATS) -> tuple[str, int, int]:
    """只要 ``(后缀, 宽, 高)`` 的 :func:`accept_image`（宽高是转正之后的）。"""

    accepted = accept_image(data, allowed=allowed)
    return accepted.suffix, accepted.width, accepted.height


def sniff_audio(data: bytes) -> str:
    """按文件头认音频，返回落盘后缀；认不出抛 ``UploadContentError``。"""

    head = bytes(data[:16])
    if head.startswith(b"ID3"):
        return ".mp3"
    if head[:4] in (b"RIFF", b"RF64", b"BW64") and head[8:12] == b"WAVE":
        # RF64 / BW64 是超过 4GB 也能写的 WAV 变体（不少 DAW、录音机导出的大文件和 BWF 就用它），ffmpeg 照读
        return ".wav"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        # Mac 上录音 / 导出常见的 AIFF：认得出，但各处上传都不收 .aiff 后缀——accept_audio 有 ffmpeg 时转成 wav
        return ".aiff"
    if head.startswith(b"fLaC"):
        return ".flac"
    if head.startswith(b"OggS"):
        return ".ogg"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return ".m4a"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        # MPEG 音频帧同步：layer 位为 00 是 ADTS（aac），其余是 mp3
        return ".aac" if (head[1] >> 1) & 0x03 == 0 else ".mp3"
    raise UploadContentError("不是可用的音频文件：认不出格式，mp3 / wav / m4a / aac / ogg / flac 的文件头都对不上")


def _tool(name: str) -> str | None:
    """本机的 ffprobe / ffmpeg（测试替换它来模拟「没装」）。"""

    return shutil.which(name)


def _unreadable_audio(name: str, reason: str, *, detected: str = "") -> UploadContentError:
    return UploadContentError(f"上传的文件不是能读的音频（{name}）：{reason}——换一个 mp3 / wav / m4a 试试", detected=detected)


def _tool_reason(text: str, temp: Path, name: str) -> str:
    """工具原话压成一行、临时文件的绝对路径换回原文件名、去掉「[mp3 @ 0000028531…]」这种解码器内存地址、只取尾巴
    （ffprobe 的报错多行、带本机路径）。"""

    text = re.sub(r"\[[\w.-]+ @ (?:0x)?[0-9a-fA-F]+\]\s*", "", str(text).replace(str(temp), name))
    return " ".join(text.split())[-200:] or "读不出来"


class _AudioProbe(NamedTuple):
    format: str           # ffprobe 的 format_name
    duration: float       # 秒；读不出记 0
    codec: str            # 第一条音轨的编码（ffmpeg 转换时也只取这一条）
    channels: int         # 读不出记 0
    sample_rate: int      # 读不出记 0
    other_streams: bool   # 音轨之外还有别的：画面、字幕、数据流（mp3 / m4a 的封面图不算）


def _int(value: Any) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _ffprobe_audio(ffprobe: str, source: Path, name: str) -> _AudioProbe:
    """ffprobe 读一遍。读不出 / 没有音频流抛 UploadContentError；时长读不出记 0
    （浏览器录的 webm 头里常常没有时长，要转完再量，由调用方判断）。"""

    entries = ("format=format_name,duration:stream=codec_type,codec_name,duration,channels,sample_rate"
               ":stream_disposition=attached_pic")
    try:
        done = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", entries, "-of", "json", str(source)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _unreadable_audio(name, _tool_reason(str(exc), source, name)) from exc
    if done.returncode != 0:
        raise _unreadable_audio(name, _tool_reason(done.stderr or done.stdout, source, name))
    try:
        payload = json.loads(done.stdout or "{}")
    except ValueError as exc:
        raise _unreadable_audio(name, "ffprobe 的输出不是 JSON") from exc
    fmt = str((payload.get("format") or {}).get("format_name") or "")
    streams = [s for s in payload.get("streams") or () if isinstance(s, dict)]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        raise _unreadable_audio(name, "里面没有音频流", detected=fmt)
    duration = 0.0
    for value in [(payload.get("format") or {}).get("duration"), *(s.get("duration") for s in audio)]:
        try:
            duration = max(duration, float(value))
        except (TypeError, ValueError):
            continue
    other = any(s.get("codec_type") != "audio" and not (s.get("disposition") or {}).get("attached_pic") for s in streams)
    first = audio[0]
    return _AudioProbe(fmt, duration, str(first.get("codec_name") or "").lower(), _int(first.get("channels")),
                       _int(first.get("sample_rate")), other)


def _storable_as_is(suffix: str, probe: _AudioProbe) -> bool:
    """容器对得上后缀之外，编码也对得上、而且只有音轨（封面图除外）才原样存。"""

    if probe.other_streams:
        return False
    return any(probe.codec == c or (c.endswith("_") and probe.codec.startswith(c)) for c in _AUDIO_CODECS.get(suffix, ()))


class _Conversion(NamedTuple):
    suffix: str
    args: tuple[str, ...]        # ffmpeg 的编码参数
    bytes_per_second: float      # 预估转出来多大用；0 = 估不出（只靠 -fs 兜底）


def _conversion(probe: _AudioProbe, allowed: tuple[str, ...]) -> _Conversion | None:
    """读得出、却不能原样存的音频转成什么。PCM（AIFF 这类）转 wav：大小跟原文件差不多、无损；其余（webm / opus、wma、
    AMR、视频里的音轨…）转 m4a（AAC 192k）——压缩过的音频转成 wav 会胀大 10–25 倍（审查实测：一小时 26MB 的 webm
    转出 659MB 的 wav）。源本来就是 AAC（手机录的视频里多半是）直接拷贝音轨，不重编码。这个上传口两种都不收返回 None。"""

    def wav() -> _Conversion:
        return _Conversion(".wav", ("-c:a", "pcm_s16le"), (probe.sample_rate or 48000) * (probe.channels or 2) * 2)

    if probe.codec.startswith("pcm_") and ".wav" in allowed:
        return wav()
    if ".m4a" in allowed:
        if probe.codec == "aac":
            return _Conversion(".m4a", ("-c:a", "copy", "-movflags", "+faststart"), 0)
        # AAC 只支持到 96kHz，高采样率的源先降到 48kHz（听感上没区别）
        rate = ("-ar", "48000") if probe.sample_rate > 48000 else ()
        return _Conversion(".m4a", ("-c:a", "aac", "-b:a", _AAC_BITRATE, *rate, "-movflags", "+faststart"), _AAC_BYTES_PER_SECOND)
    if ".wav" in allowed:
        return wav()
    return None


def _too_large(name: str, label: str, max_bytes: int) -> UploadContentError:
    return UploadContentError(f"这段音频（{name}）要转成 {label} 才能用，转完会超过 {max_bytes // 1024 // 1024}MB"
                              "——剪短一些，或先转成 mp3 再传", status=413)


def _convert(ffmpeg: str, source: Path, target: Path, name: str, plan: _Conversion, max_bytes: int) -> None:
    """只取第一条音轨按 ``plan`` 转换，写到 ``target``。``-fs``：输出一过 ``max_bytes`` ffmpeg 就停——头里没写时长的
    录音事先估不出大小，靠它兜底，别转出几百 MB 再读进内存。被截住时 ffmpeg 可能返回非 0（m4a 收尾写不完），
    这里按「超过上限」拒，不说成「读不出来」。"""

    label = plan.suffix.lstrip(".")
    command = [ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn", *plan.args,
               "-fs", str(max_bytes + 1), str(target)]
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                              check=False, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _unreadable_audio(name, _tool_reason(str(exc), source, name)) from exc
    size = target.stat().st_size if target.is_file() else 0
    if size > max_bytes:
        raise _too_large(name, label, max_bytes)
    if done.returncode != 0 or size == 0:
        raise _unreadable_audio(name, _tool_reason(done.stderr or f"转成 {label} 失败", source, name))


# 转换输出的默认上限：各上传口里最大的那个（短剧配乐 / 绘本 40MB）；调用方传自己的上限
MAX_CONVERTED_BYTES = 40 * 1024 * 1024


def accept_audio(data: bytes, filename: str, *, allowed: Iterable[str], max_bytes: int = MAX_CONVERTED_BYTES,
                 duration_probe: Callable[[Path], float] | None = None) -> AcceptedAudio:
    """按内容认音频，返回要落盘的字节、后缀、时长。``allowed`` 是这个上传口收的后缀（``.mp3`` 这种）。

    * 本机有 ffprobe：读不出 / 没有音频流 / 时长为 0 → 「上传的文件不是能读的音频（名）：原因——换一个 … 试试」；
      容器与编码都对得上 ``allowed`` 里的后缀就原样存、后缀按容器定（WAV 字节叫 .mp3 存成 .wav）；对不上（AIFF、webm、
      wma、带画面的 mp4、ALAC 的 m4a…）就用 ffmpeg 只取音轨转成 wav / m4a（见 :func:`_conversion`）。
    * ``max_bytes``：转出来的文件的上限（传这个上传口自己的上限），超了抛 status 为 413 的 UploadContentError——
      头里写了时长的事先估算、不用真转就拒；没写的靠 ffmpeg ``-fs`` 截住。原样存的不受它管（上传本身已经限过大小）。
    * ``duration_probe``：调用方自带的时长探测器（播客模块的注入点，测试用它顶替 ffprobe）——后缀按文件头 / 扩展名定。
    * 都没有：只看文件头（``sniff_audio``），认出来的格式不收时退回扩展名（AIFF 内容叫 .wav 的照收，装配时 ffmpeg 按内容读）。
    """

    allowed = tuple(allowed)
    name = Path(str(filename or "")).name or "audio"
    ext = Path(name).suffix.lower()
    ffprobe = None if duration_probe is not None else _tool("ffprobe")
    if duration_probe is None and not ffprobe:
        return AcceptedAudio(data, _suffix_by_header(data, ext, allowed), 0.0, "")
    with tempfile.TemporaryDirectory(prefix="storystudio-upload-") as folder:
        # 后缀照用户给的（只要像个扩展名）：ffprobe 主要按内容认，但裸 ADTS / 无 ID3 的 mp3 这类扩展名能帮它一把
        source = Path(folder) / ("upload" + (ext if ext[1:].isalnum() and len(ext) <= 6 else ".bin"))
        source.write_bytes(data)
        if duration_probe is not None:
            try:
                duration = float(duration_probe(source))
            except Exception as exc:  # noqa: BLE001 - 探测器抛什么都归成一句人话
                raise _unreadable_audio(name, _tool_reason(str(exc), source, name)) from exc
            if duration <= 0:
                raise _unreadable_audio(name, "时长为 0")
            return AcceptedAudio(data, _suffix_by_header(data, ext, allowed, probed=True), duration, "")
        probe = _ffprobe_audio(str(ffprobe), source, name)
        fmt = probe.format
        suffix = next((_AUDIO_CONTAINERS[alias] for alias in fmt.split(",") if alias in _AUDIO_CONTAINERS), "")
        ffmpeg = _tool("ffmpeg")
        plan = _conversion(probe, allowed)
        if suffix in allowed and (_storable_as_is(suffix, probe) or plan is None or not ffmpeg):
            # 能原样存；或内容不纯（带画面…）但本机转不了——容器本身这里收，照旧原样存（装配时 ffmpeg 只取音轨）
            if probe.duration <= 0:
                raise _unreadable_audio(name, "时长为 0", detected=fmt)
            return AcceptedAudio(data, suffix, probe.duration, fmt)
        # 读得出音频、却不能原样存：转换后收，而不是按扩展名拒
        if plan is None or not ffmpeg:
            labels = " / ".join(s.lstrip(".") for s in allowed)
            raise UploadContentError(f"这种音频格式（{fmt}）这里用不了——先转成 {labels} 再传", detected=fmt)
        label = plan.suffix.lstrip(".")
        if probe.duration * plan.bytes_per_second > max_bytes:
            raise _too_large(name, label, max_bytes)     # 头里写了时长：不用真转就知道放不下
        target = Path(folder) / f"converted{plan.suffix}"
        _convert(ffmpeg, source, target, name, plan, max_bytes)
        duration = _ffprobe_audio(str(ffprobe), target, name).duration     # 转完再量：原文件头里可能压根没写时长
        if duration <= 0:
            raise _unreadable_audio(name, "时长为 0", detected=fmt)
        # 读进内存的大小有上限（max_bytes，跟上传本身的上限同一量级），路由拿去写盘
        return AcceptedAudio(target.read_bytes(), plan.suffix, duration, fmt)


def _suffix_by_header(data: bytes, ext: str, allowed: tuple[str, ...], *, probed: bool = False) -> str:
    """没用 ffprobe 时的后缀：文件头认出来且收 → 用它；认出来但不收（AIFF）而扩展名收 → 用扩展名；否则拒。
    ``probed``：调用方的探测器已经读出了时长，文件头认不出（少见的变体）也按扩展名收。"""

    try:
        sniffed = sniff_audio(data)
    except UploadContentError:
        if probed and ext in allowed:
            return ext
        raise
    if sniffed in allowed:
        return sniffed
    if ext in allowed:
        return ext
    labels = " / ".join(s.lstrip(".") for s in allowed)
    raise UploadContentError(f"这种音频格式（{sniffed.lstrip('.')}）这里用不了——先转成 {labels} 再传", detected=sniffed.lstrip("."))


def same_content_index(paths: Iterable[object], folder: str, digest: str) -> int | None:
    """参考图列表里第一条跟这次上传「内容相同」的下标；没有返回 None。

    上传落盘名是 ``<文件名 slug>-<内容摘要 8 位><后缀>``：同一目录下摘要相同就是同一张图，只是文件名不同——
    Windows 重复下载的「photo (1).png」、改过名的副本。只按完整相对路径查重的话，这类会再记一条，照样白占名额，
    生成时还把同一张图当两张参考图带上。``folder`` 是项目根下的相对目录（posix），只认这个目录里上传进来的条目；
    后缀不参与比较（老版本按扩展名定后缀，同一段 PNG 字节可能存成过 .jpg）。"""

    marker = f"-{digest}"
    for index, raw in enumerate(paths):
        if not isinstance(raw, str) or not raw:
            continue
        path = PurePosixPath(raw)
        if path.parent.as_posix() == folder and path.stem.endswith(marker):
            return index
    return None


__all__ = ["IMAGE_FORMATS", "HEIF_EXTENSIONS", "HEIF_HINT", "MAX_CONVERTED_BYTES", "UploadContentError", "AcceptedImage",
           "AcceptedAudio", "accept_image", "accept_audio", "sniff_image", "sniff_audio", "same_content_index"]
