import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from storyvideo.media import MediaValidationError, probe_media, validate_media


def media_file(tmp_path: Path, name: str = "clip.mp4") -> Path:
    path = tmp_path / name
    path.write_bytes(b"media" * 1000)
    return path


def probe_result(
    *, audio=True, duration=4.0, width=1080, height=1920, fps="24/1", codec="h264"
):
    streams = [
        {
            "codec_type": "video",
            "codec_name": codec,
            "width": width,
            "height": height,
            "avg_frame_rate": fps,
        }
    ]
    if audio:
        streams.append({"codec_type": "audio", "sample_rate": "48000"})
    return subprocess.CompletedProcess(
        ["ffprobe"],
        0,
        stdout=json.dumps({"streams": streams, "format": {"duration": str(duration)}}),
        stderr="",
    )


def test_probe_normalizes_video_and_audio_metadata(tmp_path):
    path = media_file(tmp_path)
    with patch("storyvideo.media.subprocess.run", return_value=probe_result()):
        info = probe_media(path)

    assert info.width == 1080
    assert info.height == 1920
    assert info.fps == 24
    assert info.sample_rate == 48000
    assert info.has_audio is True


def test_video_can_require_audio(tmp_path):
    path = media_file(tmp_path)
    with patch(
        "storyvideo.media.subprocess.run", return_value=probe_result(audio=False)
    ):
        with pytest.raises(MediaValidationError, match="no audio"):
            validate_media(path, "video", require_audio=True)


def test_duration_and_dimensions_are_enforced(tmp_path):
    path = media_file(tmp_path)
    with patch(
        "storyvideo.media.subprocess.run",
        return_value=probe_result(duration=9, width=720, height=1280),
    ):
        with pytest.raises(MediaValidationError, match="duration differs"):
            validate_media(
                path,
                "video",
                expected_duration_seconds=4,
                duration_tolerance_seconds=1,
                expected_dimensions=(1080, 1920),
            )


def test_ffprobe_failure_is_not_accepted_as_media(tmp_path):
    path = media_file(tmp_path)
    failed = subprocess.CompletedProcess(
        ["ffprobe"], 1, stdout="", stderr="Invalid data found"
    )
    with patch("storyvideo.media.subprocess.run", return_value=failed):
        with pytest.raises(MediaValidationError, match="Invalid data"):
            validate_media(path, "video")


def test_image_needs_visual_stream_but_not_duration_or_audio(tmp_path):
    path = media_file(tmp_path, "frame.png")
    with patch(
        "storyvideo.media.subprocess.run",
        return_value=probe_result(audio=False, duration=0, fps="1/1", codec="png"),
    ):
        info = validate_media(path, "image")

    assert info.has_video is True
    assert info.has_audio is False
    assert info.duration_seconds == 0


def test_image_rejects_a_video_container_with_only_a_visual_stream(tmp_path):
    path = media_file(tmp_path, "frame.png")
    with patch(
        "storyvideo.media.subprocess.run",
        return_value=probe_result(audio=False, duration=4, codec="h264"),
    ):
        with pytest.raises(MediaValidationError, match="non-still"):
            validate_media(path, "image")
