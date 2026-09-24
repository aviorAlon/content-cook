"""预设 brief 库（docs/music-spec.md §2）：8–10 张、形状对齐项目文件的 brief、每张都能直接编译出提示词。"""

from __future__ import annotations

import json

import pytest

from music import briefs as B
from music import prompts as P
from music import spec as S

WANTED = {"睡前摇篮曲", "史诗战鼓", "都市轻快", "悬疑低频", "温情钢琴", "国风古筝", "赛博电子", "纪录片弦乐", "喜剧诙谐", "时尚节拍"}


def test_library_file_exists_and_has_eight_to_ten():
    assert B.BRIEFS_PATH.is_file(), B.BRIEFS_PATH
    presets = B.presets()
    assert 8 <= len(presets) <= 10
    assert {p["name"] for p in presets} == WANTED
    assert len({p["id"] for p in presets}) == len(presets)


def test_shape_matches_the_project_file_brief(tmp_path):
    for row in B.payload():
        assert row["id"] and row["name"] and row["english"] and row["note"] and row["note_en"]
        assert isinstance(row["duration"], int) and S.DURATION_MIN <= row["duration"] <= S.DURATION_MAX
        brief = row["brief"]
        assert set(brief) <= set(B.BRIEF_KEYS)
        assert brief["usage"] and brief["genre"] and brief["mood"] and brief["instruments"]
        assert S.BPM_MIN <= brief["bpm"] <= S.BPM_MAX
        # 整份填进表单就应当直接过校验
        spec = S.normalize({"title": row["name"], "brief": brief, "duration": row["duration"]},
                           project_root=tmp_path, spec_id="MU-1")
        assert spec["brief"]["genre"] == brief["genre"]
        text = P.compile_zh(spec)
        assert brief["usage"] in text and brief["genre"] in text
        assert P.compile_en(spec).startswith("Use: ")


def test_find_preset_by_id_name_or_english():
    assert B.find_preset("bedtime_lullaby")["name"] == "睡前摇篮曲"
    assert B.find_preset("睡前摇篮曲")["id"] == "bedtime_lullaby"
    assert B.find_preset("Bedtime Lullaby")["id"] == "bedtime_lullaby"
    assert B.find_preset("nope") is None and B.find_preset("") is None
    assert "bedtime_lullaby" in B.preset_ids()


def test_broken_library_is_not_fatal(monkeypatch, tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(B, "BRIEFS_PATH", broken)
    B._data.cache_clear(); B.presets.cache_clear()
    try:
        assert B.presets() == () and B.payload() == [] and B.find_preset("bedtime_lullaby") is None
        monkeypatch.setattr(B, "BRIEFS_PATH", tmp_path / "missing.json")
        B._data.cache_clear(); B.presets.cache_clear()
        assert B.presets() == ()
        # 缺 id / brief 的条目直接丢掉
        partial = tmp_path / "partial.json"
        partial.write_text(json.dumps({"briefs": [{"id": "a", "name": "A", "brief": {"usage": "x"}}, {"name": "半张"}]}), encoding="utf-8")
        monkeypatch.setattr(B, "BRIEFS_PATH", partial)
        B._data.cache_clear(); B.presets.cache_clear()
        assert B.preset_ids() == ["a"]
    finally:
        B._data.cache_clear(); B.presets.cache_clear()


def test_library_file_is_valid_json_and_versioned():
    data = json.loads(B.BRIEFS_PATH.read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["note"]
    assert all("brief" in row for row in data["briefs"])
