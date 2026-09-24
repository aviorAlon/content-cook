from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from storyvideo.assembly import AssemblyError, assemble_episode
from storyvideo.spec import normalize_spec


def _raw_spec(tmp_path: Path, *, shots: list[dict] | None = None, **updates):
    default_shots = [
        {
            "id": "S1",
            "dur": 4,
            "cast": ["su"],
            "lines": [
                {"who": "su", "en": "Wake up.", "zh": "起床啦。"},
                {"who": "su", "en": "Breakfast is ready.", "zh": "早饭好了。"},
            ],
            "shot": "medium, locked off",
            "art": "A bright apartment living room.",
            "motion": "Su speaks two short lines.",
        }
    ]
    value = {
        "id": "EP99",
        "output_root": "output/story_video/EP99",
        "cast": {"su": {"appearance": "adult man"}},
        "shots": shots or default_shots,
        "audio_mode": "native_seedance_speech",
    }
    value.update(updates)
    return value


def _normalized_spec(tmp_path: Path, **updates):
    return normalize_spec(_raw_spec(tmp_path, **updates), project_root=tmp_path)


def _write_source_clips(spec: dict) -> None:
    video_dir = Path(spec["output_root"]) / "02_video"
    video_dir.mkdir(parents=True)
    for shot in spec["shots"]:
        (video_dir / f"{shot['id']}.mp4").write_bytes(b"source" * 300)


def _probe_payload(path: Path, facts: dict[str, float]) -> str:
    if path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        return json.dumps(
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "mjpeg",
                        "width": 1080,
                        "height": 1280,
                        "avg_frame_rate": "1/1",
                    }
                ],
                "format": {},
            }
        )
    duration = next(
        (value for marker, value in facts.items() if marker in path.name),
        facts.get("default", 4.0),
    )
    return json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": facts.get("width", 1080),
                    "height": facts.get("height", 1920),
                    "avg_frame_rate": f"{int(facts.get('fps', 24))}/1",
                },
                {
                    "codec_type": "audio",
                    "sample_rate": "48000",
                    "channels": 2,
                },
            ],
            "format": {"duration": str(duration)},
        }
    )


def _fake_media_commands(calls: list, *, durations: dict[str, float] | None = None):
    facts = {"default": 4.0, **(durations or {})}

    def run(command, **kwargs):
        command = [str(part) for part in command]
        calls.append((command, kwargs))
        if Path(command[0]).name.lower().startswith("ffprobe"):
            return subprocess.CompletedProcess(
                command, 0, stdout=_probe_payload(Path(command[-1]), facts), stderr=""
            )
        if command[-2:] != ["null", "-"] and command[-1] != "-":
            output = Path(command[-1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"generated" * 300)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    return run


def _ffmpeg_calls(calls):
    return [item for item in calls if Path(item[0][0]).name.lower().startswith("ffmpeg")]


def _render_filters(calls):
    return [
        command[command.index("-vf") + 1]
        for command, _ in _ffmpeg_calls(calls)
        if "-vf" in command and "drawtext=" in command[command.index("-vf") + 1]
    ]


def test_assemble_defaults_to_24fps_48khz_and_installs_qc_atomically(tmp_path):
    spec = _normalized_spec(tmp_path)
    _write_source_clips(spec)
    font = tmp_path / "font.ttf"
    font.write_bytes(b"font")
    calls = []

    with patch(
        "storyvideo.assembly.subprocess.run", side_effect=_fake_media_commands(calls)
    ):
        result = assemble_episode(
            spec,
            font_path=font,
            create_contact_sheet=True,
        )

    assert result.final_path == Path(spec["output_root"]) / "EP99_final.mp4"
    assert result.final_path.is_file()
    assert result.contact_sheet_path == Path(spec["output_root"]) / "x_final_qc.jpg"
    assert result.contact_sheet_path.is_file()
    assert result.fps == 24
    ffmpeg = _ffmpeg_calls(calls)
    assert ffmpeg
    assert all(kwargs["shell"] is False for _, kwargs in ffmpeg)
    flattened = [part for command, _ in ffmpeg for part in command]
    assert any("fps=24" in part for part in flattened)
    assert "48000" in flattened
    assert any("loudnorm=I=-16:TP=-1.5:LRA=9" in part for part in flattened)
    assert not any("afftdn" in part or "acompressor" in part for part in flattened)


def test_two_shots_use_video_and_audio_crossfades(tmp_path):
    first = _raw_spec(tmp_path)["shots"][0]
    second = json.loads(json.dumps(first))
    second["id"] = "S2"
    spec = _normalized_spec(tmp_path, shots=[first, second])
    _write_source_clips(spec)
    font = tmp_path / "font.ttf"
    font.write_bytes(b"font")
    calls = []

    with patch(
        "storyvideo.assembly.subprocess.run",
        side_effect=_fake_media_commands(calls, durations={"EP99_final": 7.75}),
    ):
        result = assemble_episode(spec, font_path=font)

    complex_filters = [
        command[command.index("-filter_complex") + 1]
        for command, _ in _ffmpeg_calls(calls)
        if "-filter_complex" in command
    ]
    assert any("xfade=transition=fade" in value for value in complex_filters)
    assert any("acrossfade=d=0.250" in value for value in complex_filters)
    join_command = next(
        command for command, _ in _ffmpeg_calls(calls) if "-maxrate" in command
    )
    assert join_command[join_command.index("-maxrate") + 1] == "12000k"
    assert join_command[join_command.index("-bufsize") + 1] == "24000k"
    assert result.duration_seconds == pytest.approx(7.75)


def test_delivery_bitrate_settings_override_join_encoder(tmp_path):
    first = _raw_spec(tmp_path)["shots"][0]
    second = json.loads(json.dumps(first))
    second["id"] = "S2"
    spec = _normalized_spec(
        tmp_path,
        shots=[first, second],
        delivery={"maxrate_kbps": 6400, "bufsize_kbps": 12800},
    )
    _write_source_clips(spec)
    font = tmp_path / "font.ttf"
    font.write_bytes(b"font")
    calls = []

    with patch(
        "storyvideo.assembly.subprocess.run",
        side_effect=_fake_media_commands(calls, durations={"EP99_final": 7.75}),
    ):
        assemble_episode(spec, font_path=font)

    join_command = next(
        command for command, _ in _ffmpeg_calls(calls) if "-maxrate" in command
    )
    assert join_command[join_command.index("-maxrate") + 1] == "6400k"
    assert join_command[join_command.index("-bufsize") + 1] == "12800k"


@pytest.mark.parametrize(
    ("delivery", "message"),
    [
        ({"maxrate_kbps": True}, "delivery.maxrate_kbps must be a number"),
        ({"maxrate_kbps": 0}, "delivery.maxrate_kbps must be between"),
        (
            {"maxrate_kbps": 3800.5},
            "delivery.maxrate_kbps must be a whole number of kbps",
        ),
        (
            {"maxrate_kbps": 8000, "bufsize_kbps": 7999},
            "delivery.bufsize_kbps must be greater than or equal to",
        ),
        ({"bufsize_kbps": 200001}, "delivery.bufsize_kbps must be between"),
    ],
)
def test_delivery_bitrate_settings_fail_before_media_processing(
    tmp_path, delivery, message
):
    spec = _normalized_spec(tmp_path, delivery=delivery)

    with patch("storyvideo.assembly.subprocess.run") as run:
        with pytest.raises(AssemblyError, match=message):
            assemble_episode(spec, font_path=tmp_path / "missing-font.ttf")

    run.assert_not_called()


def test_top_full_shot_subtitles_and_hard_cut_boundaries(tmp_path):
    first = _raw_spec(tmp_path)["shots"][0]
    second = json.loads(json.dumps(first))
    second["id"] = "P02"
    spec = _normalized_spec(
        tmp_path,
        shots=[first, second],
        delivery={"crossfade_seconds": 0},
        subtitle={
            "position": "top",
            "display": "full_shot",
            "english_font_size": 104,
            "chinese_font_size": 57,
            "english_color": "0xFFFFFF",
            "chinese_color": "#FFF7E8",
            "top_margin_ratio": 0.12,
            "band_alpha": 0,
        },
    )
    _write_source_clips(spec)
    font = tmp_path / "font.ttf"
    font.write_bytes(b"font")
    calls = []

    with patch(
        "storyvideo.assembly.subprocess.run",
        side_effect=_fake_media_commands(calls, durations={"EP99_final": 8.0}),
    ):
        assemble_episode(
            spec,
            font_path=font,
            trim_head_fn=lambda _path, _duration: 0.0,
            speech_segments_fn=lambda _path: [(0.2, 1.2), (1.4, 2.4)],
        )

    ffmpeg = _ffmpeg_calls(calls)
    filters = [
        command[command.index("-filter_complex") + 1]
        for command, _ in ffmpeg
        if "-filter_complex" in command
    ]
    assert any("concat=n=2:v=1:a=1" in value for value in filters)
    assert all("xfade=" not in value and "acrossfade=" not in value for value in filters)
    render_filters = [
        command[command.index("-vf") + 1]
        for command, _ in ffmpeg
        if "-vf" in command and "drawtext=" in command[command.index("-vf") + 1]
    ]
    assert render_filters
    assert all("y=230" in value for value in render_filters)
    assert all("between(t,0.000,4.000)" in value for value in render_filters)
    assert any("fontcolor=0xFFFFFF" in value for value in render_filters)
    assert any("fontcolor=0xFFF7E8" in value for value in render_filters)


@pytest.mark.parametrize(
    ("language", "expected_languages"),
    [
        (None, ("en", "zh")),
        ("bilingual", ("en", "zh")),
        ("zh", ("zh",)),
        ("en", ("en",)),
    ],
)
def test_subtitle_language_controls_rendered_text_layers(
    tmp_path, language, expected_languages
):
    shot = _raw_spec(tmp_path)["shots"][0]
    shot["lines"] = [{"who": "su", "en": "Wake up.", "zh": "起床啦。"}]
    updates = {"shots": [shot]}
    if language is not None:
        updates["subtitle"] = {"language": language}
    spec = _normalized_spec(tmp_path, **updates)
    _write_source_clips(spec)
    font = tmp_path / "font.ttf"
    font.write_bytes(b"font")
    calls = []

    with patch(
        "storyvideo.assembly.subprocess.run", side_effect=_fake_media_commands(calls)
    ):
        assemble_episode(
            spec,
            font_path=font,
            trim_head_fn=lambda _path, _duration: 0.0,
            speech_segments_fn=lambda _path: [(0.2, 1.2)],
        )

    render_filter = _render_filters(calls)[0]
    assert render_filter.count("drawbox=") == 1
    assert render_filter.count("drawtext=") == len(expected_languages)
    for selected in expected_languages:
        assert f".{selected}.0.txt" in render_filter
    for omitted in {"en", "zh"} - set(expected_languages):
        assert f".{omitted}.0.txt" not in render_filter
    if expected_languages == ("en", "zh"):
        assert render_filter.index(".en.0.txt") < render_filter.index(".zh.0.txt")


def test_assembly_rejects_invalid_mutated_subtitle_language_before_media_work(tmp_path):
    spec = _normalized_spec(tmp_path)
    spec["subtitle"] = {"language": "fr"}

    with patch("storyvideo.assembly.subprocess.run") as run:
        with pytest.raises(AssemblyError, match="subtitle.language"):
            assemble_episode(spec, font_path=tmp_path / "font.ttf")

    run.assert_not_called()


def test_speech_subtitles_honor_explicit_source_timeline_window(tmp_path):
    shot = _raw_spec(tmp_path)["shots"][0]
    shot["lines"] = [
        {
            "who": "su",
            "en": "Wake up.",
            "zh": "起床啦。",
            "subtitle_start_seconds": 2.25,
            "subtitle_end_seconds": 3.8,
        }
    ]
    spec = _normalized_spec(
        tmp_path,
        shots=[shot],
        subtitle={"display": "speech"},
    )
    _write_source_clips(spec)
    font = tmp_path / "font.ttf"
    font.write_bytes(b"font")
    calls = []

    with patch(
        "storyvideo.assembly.subprocess.run", side_effect=_fake_media_commands(calls)
    ):
        assemble_episode(
            spec,
            font_path=font,
            trim_head_fn=lambda _path, _duration: 0.0,
            speech_segments_fn=lambda _path: [],
        )

    render_filters = [
        command[command.index("-vf") + 1]
        for command, _ in _ffmpeg_calls(calls)
        if "-vf" in command and "drawtext=" in command[command.index("-vf") + 1]
    ]
    assert render_filters
    assert all("between(t,2.250,3.800)" in value for value in render_filters)
    assert all("between(t,0.000,4.000)" not in value for value in render_filters)


def test_missing_source_clip_fails_before_rendering(tmp_path):
    spec = _normalized_spec(tmp_path)
    font = tmp_path / "font.ttf"
    font.write_bytes(b"font")

    with pytest.raises(AssemblyError, match="missing source video"):
        assemble_episode(spec, font_path=font)


def test_failed_final_command_preserves_existing_delivery(tmp_path):
    spec = _normalized_spec(tmp_path)
    _write_source_clips(spec)
    font = tmp_path / "font.ttf"
    font.write_bytes(b"font")
    final = Path(spec["output_root"]) / "EP99_final.mp4"
    final.write_bytes(b"previous-approved-delivery")
    calls = []
    regular = _fake_media_commands(calls)

    def fail_loudnorm(command, **kwargs):
        command = [str(part) for part in command]
        if "-af" in command and "loudnorm" in command[command.index("-af") + 1]:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="encoder failed")
        return regular(command, **kwargs)

    with patch("storyvideo.assembly.subprocess.run", side_effect=fail_loudnorm):
        with pytest.raises(AssemblyError, match="encoder failed"):
            assemble_episode(spec, font_path=font)

    assert final.read_bytes() == b"previous-approved-delivery"
