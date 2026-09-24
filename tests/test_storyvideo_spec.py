import copy
import json
from pathlib import Path

import pytest

from storyvideo.spec import SpecError, load_spec, normalize_spec, stable_json_hash
from storyvideo.prompts import video_request_data


def valid_spec(**updates):
    spec = {
        "id": "EP99",
        "output_root": "output/story_video/EP99",
        "cast": {"mei": {"block": "Mei"}, "tao": {"block": "Tao"}},
        "shots": [
            {
                "id": "S1",
                "dur": 4,
                "cast": ["mei"],
                "lines": [{"who": "mei", "en": "Hello.", "zh": "你好。"}],
                "shot": "wide",
                "art": "A bright room.",
                "motion": "Mei waves.",
            }
        ],
    }
    spec.update(updates)
    return spec


def write_spec(root: Path, data, name="EP99.json") -> Path:
    path = root / "content" / "story_video" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_loads_episode_id_and_normalizes_project_paths(tmp_path):
    path = write_spec(tmp_path, valid_spec())

    spec = load_spec("EP99", project_root=tmp_path)

    assert Path(spec["spec_path"]) == path.resolve()
    assert Path(spec["spec_dir"]) == path.parent.resolve()
    assert Path(spec["project_root"]) == tmp_path.resolve()
    assert Path(spec["output_root"]) == (
        tmp_path / "output" / "story_video" / "EP99"
    ).resolve()
    assert spec["waves"] == [["S1"]]


def test_loads_explicit_relative_and_absolute_paths(tmp_path):
    root_relative = tmp_path / "episodes" / "custom.json"
    root_relative.parent.mkdir()
    root_relative.write_text(json.dumps(valid_spec()), encoding="utf-8")

    relative = load_spec("episodes/custom.json", project_root=tmp_path)
    absolute = load_spec(root_relative, project_root=tmp_path)

    assert relative["spec_path"] == absolute["spec_path"]


def test_absolute_conventional_path_can_infer_project_root(tmp_path):
    path = write_spec(tmp_path, valid_spec())

    spec = load_spec(path)

    assert Path(spec["project_root"]) == tmp_path.resolve()
    assert Path(spec["output_root"]).is_relative_to(tmp_path.resolve())


def test_prop_ref_is_resolved_from_output_root(tmp_path):
    spec = valid_spec(prop_ref="00_props/prop.png")

    normalized = normalize_spec(spec, project_root=tmp_path)

    assert Path(normalized["prop_ref"]) == (
        tmp_path / "output/story_video/EP99/00_props/prop.png"
    ).resolve()


def test_fractional_duration_is_rejected_before_paid_request_compilation(tmp_path):
    value = valid_spec()
    value["shots"][0]["dur"] = 4.5

    with pytest.raises(SpecError, match="whole number"):
        normalize_spec(value, project_root=tmp_path)


def test_audio_less_video_mode_is_rejected_before_generation(tmp_path):
    value = valid_spec(video_params={"generate_audio": False})

    with pytest.raises(SpecError, match="delivery requires"):
        normalize_spec(value, project_root=tmp_path)


def test_unverified_fast_seedance_1080p_is_rejected_before_generation(tmp_path):
    value = valid_spec(
        video_params={
            "model": "doubao-seedance-2-0-fast-260128",
            "resolution": "1080p",
            "generate_audio": True,
        }
    )

    with pytest.raises(SpecError, match="no production-verified 1080p"):
        normalize_spec(value, project_root=tmp_path)


def test_seedance_defaults_are_explicit_and_production_verified(tmp_path):
    normalized = normalize_spec(valid_spec(), project_root=tmp_path)

    assert normalized["image_params"]["model"] == "gpt-image-2"
    assert normalized["video_params"]["model"] == "doubao-seedance-2-0-260128"
    assert normalized["video_params"]["resolution"] == "1080p"
    assert normalized["video_params"]["generate_audio"] is True


def test_standard_seedance_unverified_resolution_is_rejected(tmp_path):
    value = valid_spec(
        video_params={
            "model": "doubao-seedance-2-0-260128",
            "resolution": "8K",
            "generate_audio": True,
        }
    )

    with pytest.raises(SpecError, match="no production-verified 8K"):
        normalize_spec(value, project_root=tmp_path)


def test_seedance_25_supports_thirty_seconds_at_native_720p(tmp_path):
    value = valid_spec(
        video_params={
            "model": "doubao-seedance-2-5-260628",
            "resolution": "720p",
            "generate_audio": True,
            "target_fps": 60,
            "seed": 20260807,
        }
    )
    value["shots"][0]["dur"] = 30

    normalized = normalize_spec(value, project_root=tmp_path)
    request = video_request_data(normalized, "S1")

    assert normalized["video_params"]["resolution"] == "720p"
    assert request["duration"] == 30
    assert request["extra_params"] == {"target_fps": 60, "seed": 20260807}


def test_seedance_25_rejects_duration_above_thirty_seconds(tmp_path):
    value = valid_spec(
        video_params={
            "model": "doubao-seedance-2-5-260628",
            "resolution": "720p",
            "generate_audio": True,
        }
    )
    value["shots"][0]["dur"] = 31

    with pytest.raises(SpecError, match="4-30 seconds"):
        normalize_spec(value, project_root=tmp_path)


def test_seedance_25_accepts_1080p(tmp_path):
    """按服务商的模型清单，Seedance 2.5 开放 1080p；没写分辨率时默认的 1080p 也不再被拦。"""

    value = valid_spec(
        video_params={
            "model": "doubao-seedance-2-5-260628",
            "resolution": "1080p",
            "generate_audio": True,
        }
    )
    value["shots"][0]["dur"] = 30

    normalized = normalize_spec(value, project_root=tmp_path)
    request = video_request_data(normalized, "S1")
    assert normalized["video_params"]["resolution"] == "1080p"
    assert request["resolution"] == "1080p" and request["duration"] == 30

    implicit = normalize_spec(
        valid_spec(video_params={"model": "doubao-seedance-2-5-260628", "generate_audio": True}),
        project_root=tmp_path,
    )
    assert implicit["video_params"]["resolution"] == "1080p"


@pytest.mark.parametrize("resolution", ["480p", "2K"])
def test_seedance_25_opens_only_720p_and_1080p(tmp_path, resolution):
    """只多放开 1080p 这一档；清单里有的 480p 也仍按「没有生产验证」拦下（交付是 1080 宽，没人要它）。"""

    value = valid_spec(
        video_params={
            "model": "doubao-seedance-2-5-260628",
            "resolution": resolution,
            "generate_audio": True,
        }
    )

    with pytest.raises(SpecError, match=f"no production-verified {resolution}"):
        normalize_spec(value, project_root=tmp_path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("target_fps", 30, "target_fps must be 60"),
        ("seed", -1, "seed must be an integer"),
        ("seed", True, "seed must be an integer"),
    ],
)
def test_seedance_rejects_invalid_motion_parameters(tmp_path, field, value, message):
    params = {
        "model": "doubao-seedance-2-5-260628",
        "resolution": "720p",
        "generate_audio": True,
        field: value,
    }

    with pytest.raises(SpecError, match=message):
        normalize_spec(valid_spec(video_params=params), project_root=tmp_path)


def test_minimax_h3_first_frame_parameters_are_normalized(tmp_path):
    normalized = normalize_spec(
        valid_spec(
            video_params={
                "model": "MiniMax-H3",
                "generate_audio": True,
                "count": 1,
                "aigc_watermark": False,
            }
        ),
        project_root=tmp_path,
    )

    assert normalized["video_params"] == {
        "model": "MiniMax-H3",
        "resolution": "768P",
        "aspect_ratio": "adaptive",
        "generate_audio": True,
        "count": 1,
        "aigc_watermark": False,
    }


@pytest.mark.parametrize(
    ("video_params", "message"),
    [
        (
            {
                "model": "MiniMax-H3",
                "resolution": "1080p",
                "generate_audio": True,
            },
            "no production-verified 1080p",
        ),
        (
            {
                "model": "MiniMax-H3",
                "resolution": "2K",
                "generate_audio": True,
            },
            "no production-verified 2K",
        ),
        (
            {
                "model": "MiniMax-H3",
                "resolution": "768P",
                "aspect_ratio": "9:16",
                "generate_audio": True,
            },
            "aspect_ratio=adaptive",
        ),
    ],
)
def test_minimax_h3_rejects_unverified_request_parameters(
    tmp_path, video_params, message
):
    with pytest.raises(SpecError, match=message):
        normalize_spec(
            valid_spec(video_params=video_params),
            project_root=tmp_path,
        )


def test_minimax_h3_duration_is_limited_to_four_through_fifteen_seconds(tmp_path):
    value = valid_spec(
        video_params={"model": "MiniMax-H3", "generate_audio": True}
    )
    value["shots"][0]["dur"] = 16

    with pytest.raises(SpecError, match="4-15 seconds for MiniMax-H3"):
        normalize_spec(value, project_root=tmp_path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(id="EP-BAD."),
        lambda value: value["shots"][0].update(id="S" * 65),
    ],
)
def test_run_and_shot_ids_are_rejected_before_state_can_be_created(
    tmp_path, mutate
):
    value = valid_spec()
    mutate(value)

    with pytest.raises(SpecError, match="safe slug|alphanumeric first"):
        normalize_spec(value, project_root=tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("output_root", "../elsewhere"),
        ("output_root", "output/../../elsewhere"),
    ],
)
def test_output_root_cannot_escape_project(tmp_path, field, value):
    with pytest.raises(SpecError, match="outside project boundary"):
        normalize_spec(valid_spec(**{field: value}), project_root=tmp_path)


def test_explicit_spec_path_cannot_escape_declared_project(tmp_path):
    outside = tmp_path.parent / "outside-storyvideo-spec.json"
    outside.write_text(json.dumps(valid_spec()), encoding="utf-8")
    try:
        with pytest.raises(SpecError, match="outside project boundary"):
            load_spec(outside, project_root=tmp_path)
    finally:
        outside.unlink()


def test_prop_ref_cannot_escape_output_root(tmp_path):
    with pytest.raises(SpecError, match="prop_ref resolves outside"):
        normalize_spec(
            valid_spec(prop_ref="../borrowed.png"), project_root=tmp_path
        )


def test_missing_top_level_fields_are_rejected(tmp_path):
    for field in ("output_root", "cast", "shots"):
        spec = valid_spec()
        del spec[field]
        with pytest.raises(SpecError, match=field):
            normalize_spec(spec, project_root=tmp_path)


@pytest.mark.parametrize("duration", [0, -1, True, float("inf"), "8"])
def test_duration_must_be_positive_finite_number(tmp_path, duration):
    spec = valid_spec()
    spec["shots"][0]["dur"] = duration

    with pytest.raises(SpecError, match="dur"):
        normalize_spec(spec, project_root=tmp_path)


def test_duplicate_shot_id_is_rejected(tmp_path):
    spec = valid_spec()
    spec["shots"].append(copy.deepcopy(spec["shots"][0]))

    with pytest.raises(SpecError, match="duplicate shot id: S1"):
        normalize_spec(spec, project_root=tmp_path)


def test_unknown_cast_reference_is_rejected(tmp_path):
    spec = valid_spec()
    spec["shots"][0]["cast"] = ["ghost"]

    with pytest.raises(SpecError, match="unknown cast: ghost"):
        normalize_spec(spec, project_root=tmp_path)


@pytest.mark.parametrize("field", ["shot", "art", "motion"])
def test_required_shot_text_is_nonempty(tmp_path, field):
    spec = valid_spec()
    spec["shots"][0][field] = "  "

    with pytest.raises(SpecError, match=field):
        normalize_spec(spec, project_root=tmp_path)


@pytest.mark.parametrize("language", ["en", "zh"])
def test_each_modern_line_needs_both_languages(tmp_path, language):
    spec = valid_spec()
    spec["shots"][0]["lines"][0][language] = ""

    with pytest.raises(SpecError, match=language):
        normalize_spec(spec, project_root=tmp_path)


@pytest.mark.parametrize(
    ("language", "required", "omitted"),
    [("zh", "zh", "en"), ("en", "en", "zh")],
)
def test_single_language_subtitles_only_require_selected_text(
    tmp_path, language, required, omitted
):
    spec = valid_spec(subtitle={"language": language})
    line = spec["shots"][0]["lines"][0]
    del line[omitted]

    normalized = normalize_spec(spec, project_root=tmp_path)

    assert normalized["subtitle"]["language"] == language
    assert normalized["shots"][0]["lines"][0][required]
    assert omitted not in normalized["shots"][0]["lines"][0]


@pytest.mark.parametrize(("language", "required"), [("zh", "zh"), ("en", "en")])
def test_single_language_subtitles_reject_missing_selected_text(
    tmp_path, language, required
):
    spec = valid_spec(subtitle={"language": language})
    del spec["shots"][0]["lines"][0][required]

    with pytest.raises(SpecError, match=rf"\.{required} must be a non-empty string"):
        normalize_spec(spec, project_root=tmp_path)


@pytest.mark.parametrize("language", ["", "fr", "EN", None, True, 1])
def test_invalid_subtitle_language_is_rejected(tmp_path, language):
    with pytest.raises(SpecError, match="subtitle.language"):
        normalize_spec(
            valid_spec(subtitle={"language": language}),
            project_root=tmp_path,
        )


def test_missing_subtitle_language_preserves_legacy_shape_and_contract(tmp_path):
    normalized = normalize_spec(valid_spec(), project_root=tmp_path)

    assert "subtitle" not in normalized
    assert normalized["shots"][0]["lines"][0] == {
        "who": "mei",
        "en": "Hello.",
        "zh": "你好。",
    }


def test_explicit_subtitle_timing_is_validated_and_preserved(tmp_path):
    spec = valid_spec()
    spec["shots"][0]["lines"][0].update(
        subtitle_start_seconds=2.25,
        subtitle_end_seconds=3.8,
    )

    normalized = normalize_spec(spec, project_root=tmp_path)

    line = normalized["shots"][0]["lines"][0]
    assert line["subtitle_start_seconds"] == 2.25
    assert line["subtitle_end_seconds"] == 3.8


@pytest.mark.parametrize(
    "timing",
    [
        {"subtitle_start_seconds": 1.0},
        {"subtitle_start_seconds": True, "subtitle_end_seconds": 2.0},
        {"subtitle_start_seconds": -0.1, "subtitle_end_seconds": 2.0},
        {"subtitle_start_seconds": 2.0, "subtitle_end_seconds": 2.0},
        {"subtitle_start_seconds": 2.0, "subtitle_end_seconds": 4.1},
    ],
)
def test_invalid_explicit_subtitle_timing_is_rejected(tmp_path, timing):
    spec = valid_spec()
    spec["shots"][0]["lines"][0].update(timing)

    with pytest.raises(SpecError, match="subtitle|finite number"):
        normalize_spec(spec, project_root=tmp_path)


def test_ref_frame_supports_forward_references_and_arbitrary_depth(tmp_path):
    spec = valid_spec()
    template = spec["shots"][0]
    spec["shots"] = []
    for shot_id, ref in [
        ("S4", "S3"),
        ("S2", "S1"),
        ("S1", None),
        ("S5", None),
        ("S3", "S2"),
    ]:
        shot = copy.deepcopy(template)
        shot["id"] = shot_id
        if ref:
            shot["ref_frame"] = ref
        spec["shots"].append(shot)

    normalized = normalize_spec(spec, project_root=tmp_path)

    # Stable ordering within each wave follows the source shot order.
    assert normalized["waves"] == [["S1", "S5"], ["S2"], ["S3"], ["S4"]]


def test_unknown_ref_frame_is_rejected(tmp_path):
    spec = valid_spec()
    spec["shots"][0]["ref_frame"] = "S404"

    with pytest.raises(SpecError, match="unknown shot: S404"):
        normalize_spec(spec, project_root=tmp_path)


def test_ref_frame_cycle_reports_the_cycle(tmp_path):
    spec = valid_spec()
    second = copy.deepcopy(spec["shots"][0])
    second["id"] = "S2"
    spec["shots"][0]["ref_frame"] = "S2"
    second["ref_frame"] = "S1"
    spec["shots"].append(second)

    with pytest.raises(SpecError, match=r"cycle detected: S1 -> S2 -> S1"):
        normalize_spec(spec, project_root=tmp_path)


def test_legacy_single_line_fields_are_normalized_and_silent_shots_stay_silent(tmp_path):
    speaking = valid_spec()["shots"][0]
    speaking.pop("lines")
    speaking.update(speaker="mei", en="Look.", zh="你看。")
    silent = copy.deepcopy(speaking)
    silent.update(id="S2", speaker=None, en="", zh="")
    spec = valid_spec(shots=[speaking, silent])

    normalized = normalize_spec(spec, project_root=tmp_path)

    assert normalized["shots"][0]["lines"] == [
        {"who": "mei", "en": "Look.", "zh": "你看。"}
    ]
    assert normalized["shots"][1]["lines"] == []


@pytest.mark.parametrize(
    ("language", "selected", "omitted"),
    [("zh", "你看。", "en"), ("en", "Look.", "zh")],
)
def test_legacy_lines_follow_single_language_contract(
    tmp_path, language, selected, omitted
):
    shot = valid_spec()["shots"][0]
    shot.pop("lines")
    shot.update(speaker="mei")
    shot[language] = selected

    normalized = normalize_spec(
        valid_spec(shots=[shot], subtitle={"language": language}),
        project_root=tmp_path,
    )

    assert normalized["shots"][0]["lines"] == [
        {language: selected, "who": "mei"}
    ]
    assert omitted not in normalized["shots"][0]["lines"][0]


def test_legacy_both_speaker_is_accepted(tmp_path):
    shot = valid_spec()["shots"][0]
    shot.pop("lines")
    shot.update(
        cast=["mei", "tao"],
        speaker="both",
        en="Wait. / Over here!",
        zh="等等。／这边！",
    )

    normalized = normalize_spec(valid_spec(shots=[shot]), project_root=tmp_path)

    assert normalized["shots"][0]["lines"][0]["who"] == "both"


def test_line_speaker_must_be_in_cast(tmp_path):
    """DRAMA-11：写错的 who 以前一路走到付费的视频提示词里（「ghost用普通话说：…」、没有声线），
    配音计划那边只报一句「未绑定音色」。screenplay 层早就拦了，两层口径要一致。"""

    shot = valid_spec()["shots"][0]
    shot["lines"] = [{"who": "ghost", "en": "Hi.", "zh": "嗨。"}]

    with pytest.raises(SpecError) as exc:
        normalize_spec(valid_spec(shots=[shot]), project_root=tmp_path)
    assert "shot S1.lines[0].who is not in cast: ghost" in str(exc.value)

    # 认 id、id@变体、角色名、旁白别名
    base = valid_spec()
    base["cast"]["mei"] = {"block": "Mei", "name": "小梅", "variants": {"rain": {"delta": "雨衣"}}}
    for who in ("mei", "mei@rain", "小梅", "narrator", "旁白", "V.O."):
        shot = valid_spec()["shots"][0]
        shot["lines"] = [{"who": who, "en": "Hi.", "zh": "嗨。"}]
        normalized = normalize_spec(
            valid_spec(cast=base["cast"], shots=[shot]), project_root=tmp_path
        )
        assert normalized["shots"][0]["lines"][0]["who"] == who


def test_speech_mode_only_accepts_known_values(tmp_path):
    """DRAMA-11：不认识的 speech_mode 以前等同「画内说话」——提示词照样要求嘴型跟台词对上，
    配音叠上去穿帮要等出片才看得出来。"""

    shot = valid_spec()["shots"][0]
    shot["speech_mode"] = "bogus"

    with pytest.raises(SpecError) as exc:
        normalize_spec(valid_spec(shots=[shot]), project_root=tmp_path)
    assert "shot S1.speech_mode must be one of: narration, offscreen_narration" in str(exc.value)

    for mode in ("narration", "offscreen_narration"):
        shot = valid_spec()["shots"][0]
        shot["speech_mode"] = mode
        normalized = normalize_spec(valid_spec(shots=[shot]), project_root=tmp_path)
        assert normalized["shots"][0]["speech_mode"] == mode

    # 空串 = 没写（界面清空这一栏走的就是这条）
    shot = valid_spec()["shots"][0]
    shot["speech_mode"] = "  "
    normalized = normalize_spec(valid_spec(shots=[shot]), project_root=tmp_path)
    assert "speech_mode" not in normalized["shots"][0]


def test_source_hashes_use_raw_json_not_derived_paths_or_waves(tmp_path):
    raw = valid_spec()
    raw_copy = copy.deepcopy(raw)

    first = normalize_spec(raw, project_root=tmp_path / "one")
    second = normalize_spec(raw, project_root=tmp_path / "two")

    assert raw == raw_copy
    assert first["source_hash"] == second["source_hash"] == stable_json_hash(raw)
    assert first["shots"][0]["source_hash"] == second["shots"][0]["source_hash"]
    assert first["output_root"] != second["output_root"]


