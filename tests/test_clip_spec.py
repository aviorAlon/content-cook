"""单条视频项目文件（DESIGN §43，契约 docs/clip-spec.md §1）：归一化 / 编号 / 读写 / 归档。

节拍按 start 排序、end > start、id 不重复是**结构**（这里拦）；空洞 / 重叠 / 与总时长对不上是**告警**
（test_clip_lint.py），写到一半的项目也要能打开。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from clip import spec as S


def sample(**extra) -> dict:
    """契约 §1 那条示例短片的小号版本：两拍、六色色板、16:9。"""

    data = {
        "title": "城市清晨短片",
        "prompt_language": "zh",
        "style": "custom",
        "style_anchor": "清冷写实的城市纪录片质感",
        "global": {
            "look": "像一段从天亮拍到热闹的长镜头",
            "palette": ["雾蓝", "青灰", "米白", "砖红", "暖橙", "铜金"],
            "light": "日出前后的斜射晨光，街面薄雾",
            "aspect_ratio": "16:9",
            "safe_area": "行人和关键动作落在画面中部，四周留出余量",
            "text_policy": "画面上的字只有店招和片名，片名用英文大写",
            "audio": "街道环境声打底，配一段轻柔的钢琴",
        },
        "beats": [
            {"id": "B01", "start": 0.0, "end": 1.5, "shot": "航拍大远景", "picture": "薄雾里的城市天际线", "sound": "远处一声汽笛", "text": "GOOD MORNING"},
            {"id": "B02", "start": 1.5, "end": 3.0, "shot": "近景", "picture": "卷帘门拉起，蒸笼冒出白汽", "sound": "卷帘门哗啦一声", "text": ""},
        ],
        "continuity": {"one_take": True, "transitions": "借驶过的电车车身挡一下镜头", "last_frame": "停在整条街被阳光照亮的全景上",
                       "forbid": ["真实品牌的标识", "夜景霓虹"]},
        "video_params": {"model": "@video", "duration": 3, "resolution": "1080p", "generate_audio": False},
    }
    data.update(extra)
    return data


def build(tmp_path, *, clip_id: str = "CL-T-1", **extra) -> dict:
    return S.normalize(sample(**extra), project_root=tmp_path, clip_id=clip_id)


# ---------------------------------------------------------------------------- 编号 / 路径


def test_ids_are_numbered_per_day(tmp_path):
    assert S.next_clip_id([], today=date(2026, 9, 10)) == "CL-20260910-1"
    assert S.next_clip_id(["CL-20260910-1"], today=date(2026, 9, 10)) == "CL-20260910-2"
    assert S.existing_ids(tmp_path) == []
    (tmp_path / "content" / "clip").mkdir(parents=True)
    (tmp_path / "content" / "clip" / "CL-1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "content" / "clip" / "notes.txt").write_text("x", encoding="utf-8")
    assert S.existing_ids(tmp_path) == ["CL-1"]


def test_output_paths_live_under_output_clip(tmp_path):
    spec = build(tmp_path)
    assert S.output_root(spec).as_posix().endswith("output/clip/CL-T-1")
    assert S.video_dir(spec).name == "01_video" and S.frame_dir(spec).name == "00_frame"
    assert "output_root" not in S.to_disk(spec) and "project_root" not in S.to_disk(spec)


# ---------------------------------------------------------------------------- 归一化


def test_defaults_are_filled(tmp_path):
    spec = S.normalize({"title": "空片子", "style_anchor": "写实"}, project_root=tmp_path, clip_id="CL-T-2")
    assert spec["module"] == "clip" and spec["prompt_language"] == "zh" and spec["dialect"] == "auto"
    assert spec["global"]["aspect_ratio"] == S.DEFAULT_ASPECT_RATIO and spec["global"]["palette"] == []
    assert spec["beats"] == [] and spec["attempts"] == [] and spec["current"] is None
    assert spec["continuity"] == {"one_take": True, "transitions": "", "last_frame": "", "forbid": []}
    assert spec["video_params"]["model"] == "@video" and spec["video_params"]["duration"] == S.DEFAULT_DURATION
    assert spec["first_frame"] == {"mode": "none", "asset": None, "prompt": "", "error": None}


def test_title_and_style_anchor_are_required(tmp_path):
    with pytest.raises(S.ClipSpecError):
        S.normalize({"style_anchor": "x"}, project_root=tmp_path, clip_id="CL-T-3")
    with pytest.raises(S.ClipSpecError):        # custom 又没有锚句、也没有 look
        S.normalize({"title": "x"}, project_root=tmp_path, clip_id="CL-T-3")
    # global.look 顶替 style_anchor 也算有风格锚
    assert S.normalize({"title": "x", "global": {"look": "写实"}}, project_root=tmp_path, clip_id="CL-T-3")["style"] == "custom"


def test_style_accepts_card_id_or_custom(tmp_path):
    spec = build(tmp_path, style="cinematic_key_art")
    assert spec["style"] == "cinematic_key_art"
    assert build(tmp_path, style="电影质感")["style"] == "cinematic_key_art"        # 中文名也认
    with pytest.raises(S.ClipSpecError):
        build(tmp_path, style="没有这张卡")


def test_choices_are_checked(tmp_path):
    for field, value in (("prompt_language", "fr"), ("dialect", "gpt_image")):
        with pytest.raises(S.ClipSpecError):
            build(tmp_path, **{field: value})
    with pytest.raises(S.ClipSpecError):
        build(tmp_path, **{"global": {**sample()["global"], "aspect_ratio": "2:1"}})
    with pytest.raises(S.ClipSpecError):
        build(tmp_path, first_frame={"mode": "somehow"})
    with pytest.raises(S.ClipSpecError):
        build(tmp_path, **{"global": {**sample()["global"], "nope": 1}})


def test_beats_are_sorted_and_checked(tmp_path):
    beats = [
        {"id": "B03", "start": 3.0, "end": 4.5, "picture": "c"},
        {"id": "B01", "start": 0, "end": 1.5, "picture": "a"},
        {"id": "B02", "start": 1.5, "end": 3.0, "picture": "b"},
    ]
    spec = build(tmp_path, beats=beats)
    assert S.beat_ids(spec) == ["B01", "B02", "B03"]
    assert S.covered_seconds(spec) == 4.5
    assert spec["beats"][0]["shot"] == "" and spec["beats"][0]["text"] == ""
    # id 缺了按位置补，形状不对 / 重复 / end <= start 直接拒绝
    assert S.beat_ids(build(tmp_path, beats=[{"start": 0, "end": 1, "picture": "a"}])) == ["B01"]
    for bad in ([{"id": "beat-1", "start": 0, "end": 1}],
                [{"id": "B01", "start": 0, "end": 1}, {"id": "B01", "start": 1, "end": 2}],
                [{"id": "B01", "start": 2, "end": 2}],
                [{"id": "B01", "start": 2, "end": 1}]):
        with pytest.raises(S.ClipSpecError):
            build(tmp_path, beats=bad)


def test_gaps_and_overlaps_are_not_errors(tmp_path):
    """空洞 / 重叠只在 lint 里告警——写到一半的项目照样能打开。"""

    spec = build(tmp_path, beats=[{"id": "B01", "start": 0, "end": 1}, {"id": "B02", "start": 5, "end": 6}])
    assert S.covered_seconds(spec) == 6.0
    spec = build(tmp_path, beats=[{"id": "B01", "start": 0, "end": 3}, {"id": "B02", "start": 1, "end": 4}])
    assert S.beat_ids(spec) == ["B01", "B02"]


def test_resolution_is_aligned_through_capabilities(tmp_path):
    """``video_params.resolution`` 按能力表对齐到模型支持的档位（MiniMax 只有 768P）。"""

    spec = build(tmp_path, video_params={"model": "MiniMax-H3", "duration": 3, "resolution": "720p"})
    assert spec["video_params"]["resolution"] == "768P"
    # 角色占位符没法查（用哪个模型由「模型设置」决定）：原样留着，提交前适配器再对齐一次
    assert build(tmp_path)["video_params"]["resolution"] == "1080p"
    # 不认识的模型也原样
    assert build(tmp_path, video_params={"model": "some-new-model", "duration": 3, "resolution": "4K"})["video_params"]["resolution"] == "4K"
    assert S.align_resolution("MiniMax-H3", "720p", adapter="minimax_video") == "768P"


def test_duration_range(tmp_path):
    with pytest.raises(S.ClipSpecError):
        build(tmp_path, video_params={"model": "@video", "duration": 0})
    with pytest.raises(S.ClipSpecError):
        build(tmp_path, video_params={"model": "@video", "duration": S.DURATION_MAX + 1})


def test_first_frame_asset_must_be_inside_the_project(tmp_path):
    for bad in ("/etc/passwd", "..\\x.png", "../x.png", "C:/x.png"):
        with pytest.raises(S.ClipSpecError):
            build(tmp_path, first_frame={"mode": "upload", "asset": bad})
    spec = build(tmp_path, first_frame={"mode": "upload", "asset": "output/clip/CL-T-1/00_frame/a.png"})
    assert spec["first_frame"]["asset"] == "output/clip/CL-T-1/00_frame/a.png"


def test_current_must_point_at_an_attempt(tmp_path):
    attempts = [{"file": "CL-T-1-1.mp4", "model": "@video", "request_hash": "h"}]
    spec = build(tmp_path, attempts=attempts, current="CL-T-1-1.mp4")
    assert spec["attempts"][0]["error"] is None and spec["attempts"][0]["task_id"] is None
    with pytest.raises(S.ClipSpecError):
        build(tmp_path, attempts=attempts, current="CL-T-1-9.mp4")


# ---------------------------------------------------------------------------- 读 / 写 / 归档


def test_save_load_roundtrip(tmp_path):
    spec = build(tmp_path)
    path = S.save(spec)
    assert path == S.clip_path(tmp_path, "CL-T-1") and path.is_file()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "output_root" not in on_disk and on_disk["id"] == "CL-T-1"
    again = S.load("CL-T-1", project_root=tmp_path)
    assert S.to_disk(again) == S.to_disk(spec)


def test_load_errors(tmp_path):
    (tmp_path / "content" / "clip").mkdir(parents=True)
    with pytest.raises(S.ClipSpecError):
        S.load("nope", project_root=tmp_path)
    with pytest.raises(S.ClipSpecError):
        S.load("../secret", project_root=tmp_path)
    (tmp_path / "content" / "clip" / "CL-BAD.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(S.ClipSpecError):
        S.load("CL-BAD", project_root=tmp_path)
    (tmp_path / "content" / "clip" / "CL-X.json").write_text(json.dumps({"id": "CL-OTHER", "title": "x"}), encoding="utf-8")
    with pytest.raises(S.ClipSpecError):
        S.load("CL-X", project_root=tmp_path)


def test_archive_moves_spec_and_output(tmp_path):
    spec = build(tmp_path)
    S.save(spec)
    video = S.video_dir(spec)
    video.mkdir(parents=True)
    (video / "CL-T-1-1.mp4").write_bytes(b"x")
    result = S.archive("CL-T-1", project_root=tmp_path)
    dest = tmp_path / result["archived_to"] if not str(result["archived_to"]).startswith(str(tmp_path)) else None
    root = dest or result["archived_to"]
    assert result["output_moved"] is True
    assert (tmp_path / "content" / "clip" / "CL-T-1.json").exists() is False
    assert not S.output_root(spec).exists()
    from pathlib import Path

    assert (Path(root) / "spec.json").is_file() and (Path(root) / "output" / "01_video" / "CL-T-1-1.mp4").is_file()
    with pytest.raises(S.ClipSpecError):
        S.archive("CL-T-1", project_root=tmp_path)


def test_current_and_first_frame_paths(tmp_path):
    spec = build(tmp_path, attempts=[{"file": "CL-T-1-1.mp4", "model": "@video", "request_hash": "h"}], current="CL-T-1-1.mp4")
    assert S.current_path(spec) is None                       # 指针在、文件还没落盘
    S.video_dir(spec).mkdir(parents=True)
    (S.video_dir(spec) / "CL-T-1-1.mp4").write_bytes(b"x")
    assert S.current_path(spec).name == "CL-T-1-1.mp4"
    assert S.first_frame_path(spec) is None                   # mode = none
    frame = S.frame_dir(spec)
    frame.mkdir(parents=True)
    (frame / "a.png").write_bytes(b"x")
    spec["first_frame"] = {"mode": "upload", "asset": "output/clip/CL-T-1/00_frame/a.png", "prompt": "", "error": None}
    assert S.first_frame_path(spec).name == "a.png"
    assert S.attempt_by_file(spec, "CL-T-1-1.mp4") and S.attempt_by_file(spec, "nope") is None
    with pytest.raises(S.ClipSpecError):
        S.beat_by_id(spec, "B09")
    assert S.beat_by_id(spec, "B01")["shot"] == "航拍大远景"
