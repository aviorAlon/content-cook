"""真人感起草（docs/podcast-spec.md §2）：估算、提示词里的八条约束与骨架、回填（说话人核对、只重写一个环节）、假模型跑通。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast import craft as C
from podcast import spec as S

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "podcast" / "short_video_demo.json"


def example_spec(tmp_path, **extra) -> dict:
    data = json.loads(EXAMPLE.read_text(encoding="utf-8-sig"))
    data.update(extra)
    return S.normalize(data, project_root=tmp_path, spec_id="PC-C")


def test_line_seconds_and_estimate(tmp_path):
    assert C.line_seconds("一二三四五六七八九", "zh") == 2.0
    assert C.line_seconds("一二三四五六七八九", "zh", cue="停顿") == 2.6
    assert C.line_seconds("one two three four five six", "en") == round(6 / C.EN_WORDS_PER_SECOND, 2)
    est = C.estimate(example_spec(tmp_path))
    assert est["lines"] == 41 and est["segments"] == 6 and est["target_minutes"] == 4
    assert 3.0 <= est["minutes"] <= 5.0
    empty = C.estimate(example_spec(tmp_path, segments=[]))
    assert empty == {"chars": 0, "lines": 0, "segments": 0, "seconds": 0.0, "minutes": 0.0, "target_minutes": 4}


def test_default_skeleton_scales_topics_with_minutes(tmp_path):
    short = C.default_skeleton(example_spec(tmp_path, segments=[], target_minutes=5))
    long = C.default_skeleton(example_spec(tmp_path, segments=[], target_minutes=30))
    assert [s["kind"] for s in short] == ["cold_open", "intro", "topic", "qa", "outro"]
    assert [s["kind"] for s in long].count("topic") == 3
    assert [s["id"] for s in short] == ["G01", "G02", "G03", "G04", "G05"]
    # 话题的目标默认取「听完带走」
    assert long[2]["goal"] == "无限下滑是怎么设计的"


def test_prompt_carries_rules_hosts_brief_and_skeleton(tmp_path):
    system_prompt, user_prompt = C.build_draft_prompt(example_spec(tmp_path, segments=[]))
    for rule in C.CRAFT_RULES:
        assert rule in system_prompt
    assert "双人对谈" in system_prompt and "who 只能是主播表里的 id" in system_prompt
    assert "- zhou：老周（主播）" in user_prompt and "立场：认为主要是设计的锅" in user_prompt
    assert "素材（只能用这里的事实和数字，不许编）" in user_prompt and "26 分钟" in user_prompt
    assert "## 环节 G01｜冷开场（kind=cold_open）" in user_prompt and "## 环节 G05｜收尾（kind=outro）" in user_prompt
    # 有稿子时整期重写：骨架不带台词
    _, full = C.build_draft_prompt(example_spec(tmp_path))
    assert "我昨天晚上看了眼时间" not in full
    # 只重写一段：其余环节带台词当上下文，目标环节标出来
    _, one = C.build_draft_prompt(example_spec(tmp_path), only_segment="G03", note="更吵一点")
    assert "【只重写这个环节，其余环节原样不动】" in one and "只返回环节 G03 一个对象" in one
    assert "我昨天晚上看了眼时间" in one and "这次的额外要求：更吵一点" in one


def test_apply_draft_maps_names_rejects_strangers_and_keeps_skeleton_kinds(tmp_path):
    spec = example_spec(tmp_path)
    structured = {"segments": [
        {"id": "G01", "kind": "topic", "title": "改过的标题", "lines": [{"who": "老周", "text": "嗯。", "cue": "笑", "kind": "talk"}]},
        {"id": "G09", "kind": "topic", "title": "新话题", "goal": "g", "beats": ["b"], "lines": [{"who": "xiao", "text": "新的。", "cue": "不存在的", "kind": "answer"}]},
    ]}
    out = C.apply_draft(spec, structured)
    assert out[0]["id"] == "G01" and out[0]["kind"] == "cold_open" and out[0]["title"] == "凌晨两点十七"   # 种类 / 标题以人写的为准
    assert out[0]["lines"] == [{"who": "zhou", "text": "嗯。", "cue": "笑", "kind": "talk"}]
    assert out[1]["id"] == "G09" and out[1]["lines"][0]["cue"] == "" and out[1]["lines"][0]["kind"] == "answer"
    # 模型漏掉的骨架环节保留原样
    assert [s["id"] for s in out] == ["G01", "G09", "G02", "G03", "G04", "G05", "G06"]
    with pytest.raises(C.DraftError, match="没有这个人"):
        C.apply_draft(spec, {"segments": [{"id": "G01", "kind": "topic", "title": "", "lines": [{"who": "路人", "text": "x"}]}]})
    with pytest.raises(C.DraftError, match="缺 segments"):
        C.apply_draft(spec, {"items": []})


def test_apply_draft_only_segment_swaps_that_segment(tmp_path):
    spec = example_spec(tmp_path)
    structured = {"segments": [{"id": "G03", "kind": "topic", "title": "x", "beats": ["新节拍"], "lines": [{"who": "zhou", "text": "重写了。"}]}]}
    out = C.apply_draft(spec, structured, only_segment="G03")
    assert [s["id"] for s in out] == ["G01", "G02", "G03", "G04", "G05", "G06"]
    assert out[2]["lines"] == [{"who": "zhou", "text": "重写了。", "cue": "", "kind": "talk"}] and out[2]["beats"] == ["新节拍"]
    assert out[2]["title"] == "无限下滑是怎么设计的"
    assert out[0]["lines"][0]["text"] == "我昨天晚上看了眼时间，两点十七。"
    with pytest.raises(C.DraftError, match="没有返回环节 G04"):
        C.apply_draft(spec, structured, only_segment="G04")


def test_run_draft_writes_segments_and_record(tmp_path):
    spec = example_spec(tmp_path, segments=[])
    S.save(spec)
    seen = {}

    def fake_dispatch(system_prompt, user_prompt, *, json_schema):
        seen["schema"] = json_schema
        seen["user"] = user_prompt
        return ({"segments": [
            {"id": "G01", "kind": "cold_open", "title": "冷", "lines": [{"who": "xiao", "text": "两点十七。"}]},
            {"id": "G02", "kind": "intro", "title": "开", "lines": [{"who": "zhou", "text": "我是老周。"}]},
        ]}, None, 321, "settings")

    fresh = C.run_draft(spec, note="短一点", dispatch=fake_dispatch)
    assert seen["schema"] is C.DRAFT_JSON_SCHEMA and "短一点" in seen["user"]
    assert [s["id"] for s in fresh["segments"]][:2] == ["G01", "G02"]
    assert fresh["draft"]["backend"] == "settings" and fresh["draft"]["usage_tokens"] == 321 and fresh["draft"]["note"] == "短一点"
    on_disk = S.load("PC-C", project_root=tmp_path)
    assert on_disk["segments"][0]["lines"][0]["text"] == "两点十七。"
    # 骨架里的其余环节还在（没台词，lint 会提）
    assert [s["kind"] for s in on_disk["segments"]] == ["cold_open", "intro", "topic", "qa", "outro"]
