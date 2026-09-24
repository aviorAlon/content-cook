"""Technical media validation for the story-video production runtime.

The generation provider downloads atomically, but an atomically downloaded file
can still be an HTML error page or a truncated media container.  This module is
the final local boundary: a job is not marked successful until FFprobe can fully
describe the expected stream.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any


class MediaValidationError(RuntimeError):
    """A generated artifact is missing, malformed, or has the wrong streams."""


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration_seconds: float
    width: int | None
    height: int | None
    fps: float | None
    sample_rate: int | None
    has_video: bool
    has_audio: bool
    size_bytes: int
    video_codec: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "sample_rate": self.sample_rate,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "size_bytes": self.size_bytes,
            "video_codec": self.video_codec,
        }


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _rate(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator_value = float(denominator)
            return float(numerator) / denominator_value if denominator_value else None
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def probe_media(
    path: str | Path,
    *,
    ffprobe: str = "ffprobe",
    timeout_seconds: float = 30.0,
) -> MediaInfo:
    """Return normalized stream metadata or raise ``MediaValidationError``."""

    artifact = Path(path).expanduser().resolve()
    if not artifact.is_file():
        raise MediaValidationError(f"media artifact does not exist: {artifact}")
    size = artifact.stat().st_size
    if size <= 0:
        raise MediaValidationError(f"media artifact is empty: {artifact}")
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(artifact),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise MediaValidationError(f"ffprobe could not inspect {artifact}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffprobe failed").strip()[-500:]
        raise MediaValidationError(f"invalid media artifact {artifact}: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MediaValidationError(f"ffprobe returned invalid JSON for {artifact}") from exc
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise MediaValidationError(f"ffprobe returned no streams for {artifact}")
    video = next(
        (stream for stream in streams if stream.get("codec_type") == "video"), None
    )
    audio = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), None
    )
    duration_value = (payload.get("format") or {}).get("duration")
    if duration_value in (None, "N/A") and video:
        duration_value = video.get("duration")
    try:
        duration = float(duration_value or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return MediaInfo(
        path=artifact,
        duration_seconds=duration,
        width=_positive_int(video.get("width")) if video else None,
        height=_positive_int(video.get("height")) if video else None,
        fps=_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")) if video else None,
        sample_rate=_positive_int(audio.get("sample_rate")) if audio else None,
        has_video=video is not None,
        has_audio=audio is not None,
        size_bytes=size,
        video_codec=str(video.get("codec_name") or "").lower() or None if video else None,
    )


def validate_media(
    path: str | Path,
    kind: str,
    *,
    require_audio: bool = False,
    min_size_bytes: int = 1024,
    expected_duration_seconds: float | None = None,
    duration_tolerance_seconds: float = 3.0,
    expected_dimensions: tuple[int, int] | None = None,
    ffprobe: str = "ffprobe",
) -> MediaInfo:
    """Validate an image or video before the state store records success."""

    normalized_kind = str(kind).strip().lower()
    if normalized_kind not in {"image", "video"}:
        raise ValueError("kind must be image or video")
    if min_size_bytes < 1:
        raise ValueError("min_size_bytes must be positive")
    info = probe_media(path, ffprobe=ffprobe)
    if info.size_bytes < min_size_bytes:
        raise MediaValidationError(
            f"{normalized_kind} artifact is suspiciously small: {info.size_bytes} bytes"
        )
    if not info.has_video or not info.width or not info.height:
        raise MediaValidationError(f"{normalized_kind} artifact has no visual stream")
    if normalized_kind == "image" and info.video_codec not in {
        "png",
        "mjpeg",
        "webp",
        "bmp",
        "tiff",
        "gif",
    }:
        raise MediaValidationError(
            f"image artifact uses a non-still visual codec: {info.video_codec or 'unknown'}"
        )
    if normalized_kind == "video":
        if info.duration_seconds <= 0:
            raise MediaValidationError("video artifact has no positive duration")
        if require_audio and not info.has_audio:
            raise MediaValidationError("video artifact has no audio stream")
        if expected_duration_seconds is not None:
            difference = abs(info.duration_seconds - float(expected_duration_seconds))
            if difference > float(duration_tolerance_seconds):
                raise MediaValidationError(
                    "video duration differs from the requested duration: "
                    f"expected {expected_duration_seconds:.2f}s, got {info.duration_seconds:.2f}s"
                )
    if expected_dimensions is not None:
        expected_width, expected_height = expected_dimensions
        if (info.width, info.height) != (int(expected_width), int(expected_height)):
            raise MediaValidationError(
                "media dimensions differ from the delivery profile: "
                f"expected {expected_width}x{expected_height}, got {info.width}x{info.height}"
            )
    return info


__all__ = ["MediaInfo", "MediaValidationError", "probe_media", "validate_media"]
