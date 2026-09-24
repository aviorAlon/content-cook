"""配乐项目文件（docs/music-spec.md §2）：归一化的默认值与边界、编号、读写、归档、产物路径、used_in。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from music import spec as S


def _minimal(**over):
    return {"title": "睡前摇篮曲", "brief": {"usage": "绘本睡前朗读的底噪"}, **over}


def test_normalize_defaults(tmp_path):
    spec = S.normalize(_minimal(), project_root=tmp_path, spec_id="MU-20260910-1")
    assert spec["id"] == "MU-20260910-1" and spec["module"] == "music"
    assert spec["duration"] == S.DEFAULT_DURATION and spec["count"] == S.DEFAULT_COUNT
    assert spec["prompt_language"] == "zh"
    assert spec["music_params"] == {"model": "@music"}
    assert spec["brief"] == {"usage": "绘本睡前朗读的底噪", "mood": [], "genre": "", "instruments": [],
                             "bpm": None, "structure": "", "avoid": []}
    assert spec["attempts"] == [] and spec["current"] is None and spec["used_in"] == []
    assert Path(spec["output_root"]) == (tmp_path / "output" / "music" / "MU-20260910-1")
    assert Path(spec["project_root"]) == tmp_path
    assert "output_root" not in S.to_disk(spec) and "project_root" not in S.to_disk(spec)


def test_brief_lists_accept_a_single_line(tmp_path):
    spec = S.normalize(_minimal(brief={"usage": "x", "mood": "温柔、安定", "instruments": ["音乐盒", " "], "avoid": "鼓组, 人声"}),
                       project_root=tmp_path, spec_id="MU-1")
    assert spec["brief"]["mood"] == ["温柔", "安定"]
    assert spec["brief"]["instruments"] == ["音乐盒"]
    assert spec["brief"]["avoid"] == ["鼓组", "人声"]


def test_count_duration_bpm_bounds(tmp_path):
    assert S.normalize(_minimal(count=S.COUNT_MAX), project_root=tmp_path, spec_id="MU-1")["count"] == S.COUNT_MAX
    for bad in (0, S.COUNT_MAX + 1, "x", True):
        with pytest.raises(S.MusicSpecError):
            S.normalize(_minimal(count=bad), project_root=tmp_path, spec_id="MU-1")
    for bad in (0, 4, 601):
        with pytest.raises(S.MusicSpecError):
            S.normalize(_minimal(duration=bad), project_root=tmp_path, spec_id="MU-1")
    with pytest.raises(S.MusicSpecError):
        S.normalize(_minimal(brief={"usage": "x", "bpm": 400}), project_root=tmp_path, spec_id="MU-1")
    assert S.normalize(_minimal(brief={"usage": "x", "bpm": ""}), project_root=tmp_path, spec_id="MU-1")["brief"]["bpm"] is None


def test_references_are_relative_jpg_or_png_and_capped(tmp_path):
    ok = S.normalize(_minimal(reference_images=["content/assets/MU-1/a.png", {"file": "content/assets/MU-1/b.jpg"}]),
                     project_root=tmp_path, spec_id="MU-1")
    assert ok["reference_images"] == ["content/assets/MU-1/a.png", "content/assets/MU-1/b.jpg"]
    for bad in ("/abs/a.png", "../a.png", "C:/x/a.png"):
        with pytest.raises(S.MusicSpecError):
            S.normalize(_minimal(reference_images=[bad]), project_root=tmp_path, spec_id="MU-1")
    with pytest.raises(S.MusicSpecError) as info:
        S.normalize(_minimal(reference_images=["content/assets/MU-1/a.webp"]), project_root=tmp_path, spec_id="MU-1")
    assert "jpg" in str(info.value)
    with pytest.raises(S.MusicSpecError) as info:
        S.normalize(_minimal(reference_images=[f"content/assets/MU-1/r{n}.png" for n in range(S.MAX_REFERENCES + 1)]),
                    project_root=tmp_path, spec_id="MU-1")
    assert f"最多 {S.MAX_REFERENCES} 张" in str(info.value)


def test_limits_come_from_the_source_capability_row():
    """核心上限是兜底；来源在能力记录里登记了更紧的数就按来源的来，登记得更松也不放宽。"""

    core = S.limits_from_caps({})
    assert core["count"] == [S.COUNT_MIN, S.COUNT_MAX] and core["max_references"] == S.MAX_REFERENCES
    assert core["duration"] == [S.DURATION_MIN, S.DURATION_MAX] and core["bpm"] == [S.BPM_MIN, S.BPM_MAX]
    tight = S.limits_from_caps({"counts": {"min": 1, "max": 2}, "reference_images": True, "max_reference_images": 3})
    assert tight["count"] == [1, 2] and tight["max_references"] == 3
    loose = S.limits_from_caps({"counts": {"max": 99}, "max_reference_images": 99})
    assert loose["count"] == [S.COUNT_MIN, S.COUNT_MAX] and loose["max_references"] == S.MAX_REFERENCES
    assert S.limits_from_caps({"reference_images": False})["max_references"] == 0
    assert S.limits_from_caps({"counts": {"min": "x", "max": True}})["count"] == [S.COUNT_MIN, S.COUNT_MAX]
    assert S.limits_from_caps(None) == core


def test_source_limits_follow_the_configured_music_source(tmp_path, monkeypatch, load_plugin):
    from genkit import settings as settings_mod

    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    current = settings_mod.Settings()
    assert S.source_limits(current) == S.limits_from_caps({})             # 没选来源：核心上限
    record = load_plugin("example_async")
    assert record.status == "loaded", record.message
    current.role("music").adapter = "example_async_music"
    limits = S.source_limits(current)
    assert limits["count"] == [1, 2] and limits["max_references"] == 2     # 示例插件登记的能力


def test_title_and_module_and_id_checks(tmp_path):
    with pytest.raises(S.MusicSpecError):
        S.normalize({"brief": {}}, project_root=tmp_path, spec_id="MU-1")
    with pytest.raises(S.MusicSpecError) as info:
        S.normalize(_minimal(module="picturebook"), project_root=tmp_path, spec_id="MU-1")
    assert "对不上" in str(info.value)
    with pytest.raises(S.MusicSpecError):
        S.normalize(_minimal(), project_root=tmp_path, spec_id="不行的 id")


def test_current_must_be_an_attempt(tmp_path):
    attempts = [{"file": "MU-1-1.mp3", "model": "lyria", "request_hash": "h"}]
    spec = S.normalize(_minimal(attempts=attempts, current="MU-1-1.mp3"), project_root=tmp_path, spec_id="MU-1")
    assert spec["current"] == "MU-1-1.mp3"
    with pytest.raises(S.MusicSpecError) as info:
        S.normalize(_minimal(attempts=attempts, current="MU-1-9.mp3"), project_root=tmp_path, spec_id="MU-1")
    assert "不在 attempts 里" in str(info.value)


def test_used_in_shape(tmp_path):
    spec = S.normalize(_minimal(used_in=[{"module": "drama", "id": "EP1", "slot": "12", "at": "2026-09-10T00:00:00Z"}]),
                       project_root=tmp_path, spec_id="MU-1")
    assert spec["used_in"][0]["module"] == "drama" and spec["used_in"][0]["slot"] == "12"
    with pytest.raises(S.MusicSpecError):
        S.normalize(_minimal(used_in=[{"module": "nope", "id": "EP1"}]), project_root=tmp_path, spec_id="MU-1")


def test_next_id_and_existing_ids(tmp_path):
    import datetime

    today = datetime.date(2026, 9, 10)
    assert S.next_id([], today=today) == "MU-20260910-1"
    assert S.next_id(["MU-20260910-1"], today=today) == "MU-20260910-2"
    assert S.existing_ids(tmp_path) == []
    S.save(S.normalize(_minimal(), project_root=tmp_path, spec_id="MU-20260910-1"))
    assert S.existing_ids(tmp_path) == ["MU-20260910-1"]


def test_save_load_roundtrip_and_errors(tmp_path):
    spec = S.normalize(_minimal(count=3, duration=90), project_root=tmp_path, spec_id="MU-1")
    path = S.save(spec)
    assert path == tmp_path / "content" / "music" / "MU-1.json"
    again = S.load("MU-1", project_root=tmp_path)
    assert again["count"] == 3 and again["duration"] == 90 and again["title"] == "睡前摇篮曲"
    with pytest.raises(S.MusicSpecError):
        S.load("NOPE", project_root=tmp_path)
    (tmp_path / "content" / "music" / "BAD.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(S.MusicSpecError):
        S.load("BAD", project_root=tmp_path)
    (tmp_path / "content" / "music" / "MIS.json").write_text(json.dumps({"id": "OTHER", "title": "x"}), encoding="utf-8")
    with pytest.raises(S.MusicSpecError) as info:
        S.load("MIS", project_root=tmp_path)
    assert "对不上" in str(info.value)


def test_archive_moves_spec_and_output(tmp_path):
    spec = S.normalize(_minimal(), project_root=tmp_path, spec_id="MU-1")
    S.save(spec)
    S.raw_dir(spec).mkdir(parents=True)
    (S.raw_dir(spec) / "MU-1-1.mp3").write_bytes(b"x")
    result = S.archive("MU-1", project_root=tmp_path)
    dest = Path(result["archived_to"])
    assert result["output_moved"] is True
    assert (dest / "spec.json").is_file() and (dest / "output" / "01_raw" / "MU-1-1.mp3").is_file()
    assert not S.spec_path(tmp_path, "MU-1").exists()
    with pytest.raises(S.MusicSpecError):
        S.archive("MU-1", project_root=tmp_path)


def test_attempt_paths_and_current_path(tmp_path):
    attempts = [{"file": "MU-1-1.mp3", "model": "m", "request_hash": "h"},
                {"file": "MU-1-2.mp3", "model": "m", "request_hash": "h"},
                {"file": "", "model": "m", "request_hash": "h", "error": "boom"}]
    spec = S.normalize(_minimal(attempts=attempts, current="MU-1-2.mp3"), project_root=tmp_path, spec_id="MU-1")
    assert S.current_path(spec) is None                     # 文件还不在盘上
    S.raw_dir(spec).mkdir(parents=True)
    (S.raw_dir(spec) / "MU-1-2.mp3").write_bytes(b"x")
    assert S.current_path(spec).name == "MU-1-2.mp3"
    assert S.attempt_path(spec, "MU-1-1.mp3") is None        # 记了但文件不在
    assert S.attempt_path(spec, "../../secret.mp3") is None  # 路径穿越
    assert S.attempt_path(spec, "unknown.mp3") is None       # 不在 attempts 里
    assert [a["file"] for a in S.ok_attempts(spec)] == ["MU-1-1.mp3", "MU-1-2.mp3"]


def test_record_use_dedupes_same_target_and_slot(tmp_path):
    spec = S.normalize(_minimal(), project_root=tmp_path, spec_id="MU-1")
    S.record_use(spec, module="drama", target_id="EP1", slot="12")
    S.record_use(spec, module="drama", target_id="EP1", slot="12")
    S.record_use(spec, module="drama", target_id="EP1", slot="30")
    S.record_use(spec, module="picturebook", target_id="PB-1")
    assert [(r["module"], r["id"], r["slot"]) for r in spec["used_in"]] == [
        ("drama", "EP1", "12"), ("drama", "EP1", "30"), ("picturebook", "PB-1", "")]
    assert all(r["at"] for r in spec["used_in"])
    # 归一化认得回来
    assert len(S.normalize(S.to_disk(spec), project_root=tmp_path, spec_id="MU-1")["used_in"]) == 3


def test_reference_paths_only_lists_existing_files(tmp_path):
    assets = tmp_path / "content" / "assets" / "MU-1"
    assets.mkdir(parents=True)
    (assets / "a.png").write_bytes(b"x")
    spec = S.normalize(_minimal(reference_images=["content/assets/MU-1/a.png", "content/assets/MU-1/missing.png"]),
                       project_root=tmp_path, spec_id="MU-1")
    assert [p.name for p in S.reference_paths(spec)] == ["a.png"]
