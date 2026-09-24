"""播客项目文件（docs/podcast-spec.md §1）：归一化、形式与人数、说话人核对、环节 id、读写归档。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast import spec as S

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "podcast" / "short_video_demo.json"


def example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8-sig"))


def minimal(**extra) -> dict:
    data = {
        "title": "测试期", "format": "duo",
        "hosts": [{"id": "a", "name": "阿一"}, {"id": "b", "name": "阿二"}],
        "segments": [{"kind": "topic", "title": "话题", "lines": [{"who": "a", "text": "你好。"}, {"who": "阿二", "text": "嗯。"}]}],
    }
    data.update(extra)
    return data


def test_example_normalizes_and_keeps_shape(tmp_path):
    spec = S.normalize(example(), project_root=tmp_path, spec_id="PC-1")
    assert spec["id"] == "PC-1" and spec["module"] == "podcast" and spec["format"] == "duo"
    assert [h["id"] for h in spec["hosts"]] == ["zhou", "xiao"]
    assert [s["kind"] for s in spec["segments"]] == ["cold_open", "intro", "topic", "topic", "qa", "outro"]
    assert spec["output_root"] == str((tmp_path / "output" / "podcast" / "PC-1").resolve())
    disk = S.to_disk(spec)
    assert "output_root" not in disk and "project_root" not in disk
    assert disk["pause"] == {"line": 0.35, "segment": 1.2} and disk["music"]["bed_gain"] == 0.18


def test_defaults_ids_and_speaker_name_mapping(tmp_path):
    spec = S.normalize(minimal(), project_root=tmp_path, spec_id="PC-2")
    assert spec["segments"][0]["id"] == "G01"
    assert spec["segments"][0]["lines"][1]["who"] == "b"        # 名字换成 id
    assert spec["segments"][0]["lines"][0]["kind"] == "talk" and spec["segments"][0]["lines"][0]["cue"] == ""
    assert spec["target_minutes"] == S.DEFAULT_MINUTES and spec["language"] == "zh"
    assert spec["hosts"][0]["role"] == "host" and spec["hosts"][1]["role"] == "cohost"


@pytest.mark.parametrize("data, message", [
    (minimal(segments=[{"lines": [{"who": "nobody", "text": "x"}]}]), "说话人「nobody」不在主播表里"),
    (minimal(format="solo"), "形式「单人独白」要 1 人"),
    (minimal(format="roundtable"), "形式「圆桌」要 3 到 4 人"),
    (minimal(format="interview"), "访谈要至少一位 host 和一位 guest——在主播表里把角色选对"),
    (minimal(hosts=[{"id": "a", "name": "x"}, {"id": "a", "name": "y"}]), "主播 id 重复了"),
    (minimal(segments=[{"id": "G01", "lines": []}, {"id": "G01", "lines": []}]), "环节 id 重复了"),
    (minimal(title=""), "title 不能为空"),
    (minimal(target_minutes=0), "target_minutes 要在 1 到 90 之间"),
    (minimal(pause={"line": 9}), "pause.line 要在 0 到 5 之间"),
    (minimal(music={"bed": "../x.mp3"}), "只能是项目内的相对路径"),
    (minimal(music={"bed": "content/assets/a.txt"}), "要是音频文件"),
    (minimal(segments=[{"kind": "song"}]), "kind 只能是"),
    (minimal(module="music"), "module 是「music」"),
])
def test_rejects_bad_shapes(tmp_path, data, message):
    with pytest.raises(S.PodcastSpecError) as exc:
        S.normalize(data, project_root=tmp_path, spec_id="PC-3")
    assert message in str(exc.value)


def test_interview_and_roundtable_accept_matching_hosts(tmp_path):
    spec = S.normalize(minimal(format="interview", hosts=[{"id": "h", "name": "主", "role": "host"}, {"id": "g", "name": "客", "role": "guest"}],
                               segments=[]), project_root=tmp_path, spec_id="PC-4")
    assert spec["format"] == "interview"
    spec = S.normalize(minimal(format="roundtable", hosts=[{"id": "a", "name": "1"}, {"id": "b", "name": "2"}, {"id": "c", "name": "3"}],
                               segments=[]), project_root=tmp_path, spec_id="PC-5")
    assert len(spec["hosts"]) == 3


def test_voice_accepts_profile_id_or_inline(tmp_path):
    data = minimal(hosts=[{"id": "a", "name": "阿一", "voice": "lib-1"}, {"id": "b", "name": "阿二", "voice": {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-3%"}}])
    spec = S.normalize(data, project_root=tmp_path, spec_id="PC-6")
    assert spec["hosts"][0]["voice"] == "lib-1"
    assert spec["hosts"][1]["voice"] == {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-3%"}


def test_next_id_save_load_and_archive(tmp_path):
    assert S.next_id([]).startswith("PC-")
    spec = S.normalize(example(), project_root=tmp_path, spec_id="PC-7")
    path = S.save(spec)
    assert path == tmp_path / "content" / "podcast" / "PC-7.json"
    loaded = S.load("PC-7", project_root=tmp_path)
    assert loaded["title"] == spec["title"] and len(S.all_lines(loaded)) == 41
    assert S.existing_ids(tmp_path) == ["PC-7"]
    (tmp_path / "output" / "podcast" / "PC-7" / "07_audio").mkdir(parents=True)
    result = S.archive("PC-7", project_root=tmp_path)
    assert result["output_moved"] and not path.exists()
    assert (Path(result["archived_to"]) / "spec.json").is_file()
    with pytest.raises(S.PodcastSpecError):
        S.load("PC-7", project_root=tmp_path)


def test_load_rejects_bad_json_and_mismatched_id(tmp_path):
    folder = tmp_path / "content" / "podcast"
    folder.mkdir(parents=True)
    (folder / "PC-8.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(S.PodcastSpecError, match="不是合法的 JSON"):
        S.load("PC-8", project_root=tmp_path)
    (folder / "PC-9.json").write_text(json.dumps({**minimal(), "id": "OTHER"}), encoding="utf-8")
    with pytest.raises(S.PodcastSpecError, match="对不上"):
        S.load("PC-9", project_root=tmp_path)


def test_resolve_asset_stays_inside_project(tmp_path):
    spec = S.normalize(minimal(), project_root=tmp_path, spec_id="PC-10")
    target = tmp_path / "content" / "assets" / "PC-10" / "bed.mp3"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"ID3")
    assert S.resolve_asset(spec, "content/assets/PC-10/bed.mp3") == target.resolve()
    assert S.resolve_asset(spec, "content/assets/PC-10/missing.mp3") is None
    assert S.resolve_asset(spec, "") is None


def test_formats_payload_lists_four_formats():
    rows = S.formats_payload()
    assert [r["id"] for r in rows] == ["solo", "duo", "interview", "roundtable"]
    assert rows[1]["hosts"] == [2, 2] and rows[3]["hosts"] == [3, 4]


# ---------------------------------------------------------------------------- 2026-09-22 发第一个 tag 前的审计修复


@pytest.mark.parametrize("value", [3.7, "3.7", 90.5, 0.9])
def test_target_minutes_rejects_non_integers_the_same_way_for_float_and_string(tmp_path, value):
    """PC-12：3.7（浮点）以前被 int() 静默截成 3、90.5 混过范围检查，"3.7" 却 400——现在一个口径：都拒。"""

    with pytest.raises(S.PodcastSpecError) as exc:
        S.normalize(minimal(target_minutes=value), project_root=tmp_path, spec_id="PC-M")
    assert "target_minutes 要是整数" in str(exc.value)


def test_target_minutes_accepts_integral_values(tmp_path):
    for value in (3, 3.0, "3"):
        assert S.normalize(minimal(target_minutes=value), project_root=tmp_path, spec_id="PC-M")["target_minutes"] == 3


def test_inline_voice_engine_must_be_a_known_engine(tmp_path):
    """PC-06：内联档案的 engine 乱写以前照收（合成静默走 edge、报价当付费引擎）；现在跟音色库一个口径，PUT 400。"""

    bad = minimal(hosts=[{"id": "a", "name": "阿一", "voice": {"engine": "nope", "voice": "zh-CN-XiaoxiaoNeural"}}, {"id": "b", "name": "阿二"}])
    with pytest.raises(S.PodcastSpecError, match="hosts\\[0\\].voice.engine 只能是 edge / settings / minimax，收到「nope」"):
        S.normalize(bad, project_root=tmp_path, spec_id="PC-V")
    ok = minimal(hosts=[{"id": "a", "name": "阿一", "voice": {"engine": "minimax", "voice": "mm-1"}},
                        {"id": "b", "name": "阿二", "voice": {"voice": "zh-CN-XiaoyiNeural"}}])
    spec = S.normalize(ok, project_root=tmp_path, spec_id="PC-V")
    assert spec["hosts"][0]["voice"] == {"engine": "minimax", "voice": "mm-1"} and spec["hosts"][1]["voice"] == {"voice": "zh-CN-XiaoyiNeural"}


def test_archive_moves_uploaded_assets_too(tmp_path):
    """PC-09：归档把 content/assets/<ID> 一起搬走——以前留在原地，重建同 id 会直接复用上一期的片头 / 垫乐。"""

    spec = S.normalize(minimal(), project_root=tmp_path, spec_id="PC-AR")
    S.save(spec)
    (tmp_path / "output" / "podcast" / "PC-AR" / "07_audio").mkdir(parents=True)
    (tmp_path / "output" / "podcast" / "PC-AR" / "07_audio" / "G01_01.mp3").write_bytes(b"ID3")
    assets = tmp_path / "content" / "assets" / "PC-AR"
    assets.mkdir(parents=True)
    (assets / "intro-1f764980.mp3").write_bytes(b"ID3")
    result = S.archive("PC-AR", project_root=tmp_path)
    assert result["output_moved"] and result["assets_moved"]
    dest = Path(result["archived_to"])
    assert (dest / "spec.json").is_file() and (dest / "output" / "07_audio" / "G01_01.mp3").is_file()
    assert (dest / "assets" / "intro-1f764980.mp3").is_file()
    assert not assets.exists() and not (tmp_path / "output" / "podcast" / "PC-AR").exists()
    # 没有素材 / 没有产物：照样归档，标 False
    spec2 = S.normalize(minimal(), project_root=tmp_path, spec_id="PC-AR2")
    S.save(spec2)
    result2 = S.archive("PC-AR2", project_root=tmp_path)
    assert result2["output_moved"] is False and result2["assets_moved"] is False and (Path(result2["archived_to"]) / "spec.json").is_file()


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="只有 Windows 会因为文件句柄被占用而搬不动整个目录")
def test_archive_with_an_open_file_moves_nothing_and_keeps_the_project(tmp_path):
    """PC-09：产物目录里有文件正被写（合成中）时归档整体失败：项目文件还在、产物原地不动、归档目录不留半截副本。"""

    spec = S.normalize(minimal(), project_root=tmp_path, spec_id="PC-LOCK")
    path = S.save(spec)
    audio = tmp_path / "output" / "podcast" / "PC-LOCK" / "07_audio"
    audio.mkdir(parents=True)
    (audio / "G01_01.mp3").write_bytes(b"ID3")
    (audio / "G01_02.mp3").write_bytes(b"ID3")
    assets = tmp_path / "content" / "assets" / "PC-LOCK"
    assets.mkdir(parents=True)
    (assets / "bed.mp3").write_bytes(b"ID3")
    with (audio / "G01_02.mp3").open("ab") as handle:
        handle.write(b"still writing")
        with pytest.raises(S.PodcastArchiveError, match="产物目录有文件正被占用，没归档"):
            S.archive("PC-LOCK", project_root=tmp_path)
    assert path.is_file() and (audio / "G01_01.mp3").is_file() and (audio / "G01_02.mp3").is_file() and (assets / "bed.mp3").is_file()
    deleted = tmp_path / ".webui_deleted" / "podcast"
    assert not deleted.exists() or not any(deleted.iterdir())
    assert S.load("PC-LOCK", project_root=tmp_path)["id"] == "PC-LOCK"
    # 句柄放开后就能归档
    result = S.archive("PC-LOCK", project_root=tmp_path)
    assert result["output_moved"] and result["assets_moved"] and not path.exists()
