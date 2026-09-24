"""结构化起止边界与轴线（DESIGN §22，融合 drama-skills shot-template / blocking-playbooks §1b）：
编译（中/英、generic / minimax-h3 / seedance）、没声明零变化、连续性逐字段告警、轴线 lint、ShotEdit 接口与校验、revise 复用。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import continuity as ct
from storyvideo import lint as L
from storyvideo.prompts import axis_clause, boundary_text, compile_frame_prompt, compile_video_prompt
from webui import deps, revise
from webui.app import app
from webui.routers import episodes as episodes_router

client = TestClient(app)

ENTRY = {
    "positions": "她坐长凳右端，他站她正前方约两步偏画面左", "facing": "她朝正前；他朝她", "gaze": "她看他的鞋尖；他看出口",
    "hands": "她双手交叠压膝；他插袋", "held_props": "她无；他左手拎伞", "visible_state": "报纸半湿",
    "same_as_previous": ["positions", "facing"],
}
EXIT = {
    "positions": "她坐长凳右端，他站她正前方约一步", "facing": "两人相对", "gaze": "她看他的眼睛；他看她",
    "hands": "她双手压膝；他双手插袋", "held_props": "她无；他左手拎伞", "visible_state": "报纸半湿",
}
ZH_ENTRY = ("入口状态：位置——她坐长凳右端，他站她正前方约两步偏画面左；朝向——她朝正前；他朝她；视线——她看他的鞋尖；他看出口；"
            "手——她双手交叠压膝；他插袋；持物——她无；他左手拎伞；可见状态——报纸半湿；与上一镜相同：位置、朝向。")
AXIS = {"anchors": ["lin", "cat"], "side": "left", "screen": {"lin": "left", "cat": "right"}}
AXIS_ZH = "轴线：机位在林小满—招财猫连线的左侧；林小满固定占画面左，招财猫占画面右。"


def _spec(lang: str = "zh", **extra):
    spec = {
        "prompt_language": lang, "frame_style": "F。", "scene_anchor": "S。", "camera": "C。",
        "cast": {"lin": {"name": "林小满", "block": "林小满：黑发。"}, "cat": {"name": "招财猫", "block": "金色。"}, "room": {"block": "候车室。"}},
        "shots": [
            {"id": "S1", "dur": 5, "cast": ["room", "lin", "cat"], "shot": "中景，平视", "art": "她坐在长凳上。", "motion": "她抬头。", "lines": []},
            {"id": "S2", "dur": 5, "cast": ["room", "lin", "cat"], "shot": "近景，平视", "art": "她看着他。", "motion": "她开口。",
             "ref_frame": "S1", "lines": []},
        ],
    }
    spec.update(extra)
    return spec


# ---------------------------------------------------------------------------- 编译


def test_structured_boundary_compiles_zh_and_beats_free_text():
    spec = _spec()
    spec["shots"][1]["entry"] = ENTRY
    spec["shots"][1]["exit"] = EXIT
    spec["shots"][1]["entry_state"] = "自由文本入口"          # 结构化对象在时自由文本不再权威
    f = compile_frame_prompt(spec, "S2")
    v = compile_video_prompt(spec, "S2")
    assert ZH_ENTRY + " " in f and "自由文本入口" not in f
    assert v.startswith(ZH_ENTRY + " 她开口。")
    assert v.endswith(" 出口状态：位置——她坐长凳右端，他站她正前方约一步；朝向——两人相对；视线——她看他的眼睛；他看她；"
                      "手——她双手压膝；他双手插袋；持物——她无；他左手拎伞；可见状态——报纸半湿。")
    # 全空的结构化对象 = 没写，回落到自由文本
    spec["shots"][1]["entry"] = {"positions": "  ", "same_as_previous": []}
    assert compile_video_prompt(spec, "S2").startswith("入口状态：自由文本入口 ")
    assert boundary_text(None, True) == "" and boundary_text({}, False) == ""


def test_structured_boundary_compiles_en():
    spec = _spec("en")
    spec["shots"][1]["entry"] = {"positions": "she sits at the right end of the bench", "hands": "hands folded on her knees",
                                 "same_as_previous": ["positions"]}
    spec["shots"][1]["exit"] = {"gaze": "she looks at his eyes"}
    v = compile_video_prompt(spec, "S2")
    assert v.startswith("ENTRY STATE: positions — she sits at the right end of the bench; hands — hands folded on her knees; "
                        "same as previous shot: positions. ")
    assert v.endswith(" EXIT STATE: gaze — she looks at his eyes.")
    assert "ENTRY STATE: positions — she sits" in compile_frame_prompt(spec, "S2")


def test_structured_boundary_flows_into_dialects():
    spec = _spec(video_params={"prompt_dialect": "minimax-h3"})
    spec["shots"][1]["entry"] = ENTRY
    spec["shots"][1]["exit"] = EXIT
    spec["shots"][1]["axis"] = AXIS
    text = compile_video_prompt(spec, "S2")
    assert text.startswith("integrated_multimodal_description: [Shot 1] ")
    assert "开场：位置——她坐长凳右端" in text and "与上一镜相同：位置、朝向" in text
    assert "收尾：位置——她坐长凳右端，他站她正前方约一步" in text
    assert "轴线：机位在林小满—招财猫连线的左侧" in text
    spec["video_params"]["prompt_dialect"] = "seedance"
    text = compile_video_prompt(spec, "S2")
    assert "开场：位置——她坐长凳右端" in text and "收尾：位置——" in text and AXIS_ZH in text


def test_absent_objects_compile_byte_identically():
    base = _spec()
    base["shots"][1]["entry_state"] = "她坐在长凳右端。"
    base["shots"][1]["exit_state"] = "她起身。"
    before = {sid: (compile_frame_prompt(base, sid), compile_video_prompt(base, sid)) for sid in ("S1", "S2")}
    spec = copy.deepcopy(base)
    spec["shots"][1]["entry"] = {}
    spec["shots"][1]["exit"] = {}
    spec["shots"][1]["axis"] = {}
    spec["shots"][0]["axis"] = {"anchors": [], "screen": {}}
    after = {sid: (compile_frame_prompt(spec, sid), compile_video_prompt(spec, sid)) for sid in ("S1", "S2")}
    assert before == after
    assert "轴线" not in before["S2"][0] and "AXIS" not in before["S2"][1]


def test_axis_clause_zh_en_and_break():
    spec = _spec()
    spec["shots"][1]["axis"] = AXIS
    f, v = compile_frame_prompt(spec, "S2"), compile_video_prompt(spec, "S2")
    assert AXIS_ZH + " C。" in f and v.startswith(AXIS_ZH + " 她开口。")   # 没有入口时轴线句就是视频提示词的开头
    spec["shots"][1]["axis"] = {**AXIS, "break": True}
    assert AXIS_ZH + "本镜有意越轴，新的机位侧从这一镜起沿用。" in compile_video_prompt(spec, "S2")
    # 锚点直接写名字也行；只有锚点没有侧
    assert axis_clause(spec, {"axis": {"anchors": ["甲", "乙"]}}, True) == "轴线：轴线取甲—乙连线。"
    en = _spec("en")
    en["shots"][1]["axis"] = AXIS
    assert ("AXIS: camera stays on the left side of the 林小满—招财猫 line; 林小满 always holds screen left, 招财猫 screen right."
            in compile_frame_prompt(en, "S2"))


# ---------------------------------------------------------------------------- 连续性


def test_continuity_field_by_field():
    spec = _spec()
    spec["shots"][0]["exit"] = EXIT
    spec["shots"][1]["entry"] = {**ENTRY, "held_props": "她左手拎伞；他无", "same_as_previous": []}
    msgs = ct.check_continuity(spec)
    assert any("S2" in m and "持物/手部状态在镜外变了" in m and "她无；他左手拎伞" in m and "boundary_change" in m for m in msgs)
    assert any("S2" in m and "位置在镜外跳变" in m for m in msgs)
    assert not any("有 ref_frame 但没写 entry_state" in m for m in msgs)   # 结构化入口算入口
    assert not any("接缝只有一半" in m for m in msgs)
    # boundary_change 说明来源 → 两条都压掉
    spec["shots"][1]["boundary_change"] = "伞在镜外已递给她"
    msgs = ct.check_continuity(spec)
    assert not any("镜外变了" in m or "镜外跳变" in m for m in msgs)
    # 勾了「相同」但两边写得不一样
    spec["shots"][1]["entry"] = {**ENTRY, "positions": "她站起来了"}
    assert any("勾了「与上一镜相同：位置」，但本镜入口写的" in m for m in ct.check_continuity(spec))


def test_continuity_relative_words_and_same_as_previous():
    spec = _spec()
    spec["shots"][0]["exit"] = {"positions": "她坐长凳右端"}
    spec["shots"][1]["entry"] = {"positions": "同上", "gaze": "视线不变", "same_as_previous": ["facing", "nonsense"]}
    msgs = ct.check_continuity(spec)
    assert any("入口的位置「同上」用了「同上」" in m for m in msgs)
    assert any("入口的视线「视线不变」用了「不变」" in m for m in msgs)
    assert any("上一镜 S1 的出口没写朝向" in m for m in msgs)
    assert any("「nonsense」不是边界字段" in m for m in msgs)
    # 第一镜勾「相同」
    spec["shots"][0]["entry"] = {"positions": "她坐着", "same_as_previous": ["positions"]}
    assert any("S1" in m and "第一镜勾了" in m for m in ct.check_continuity(spec))
    # 只用自由文本的老项目：行为不变
    old = _spec()
    old["shots"][0]["exit_state"] = "她坐着。"
    old["shots"][1]["entry_state"] = "她坐着。"
    assert [m for m in ct.check_continuity(old) if "一字不差" in m]
    assert ct.check_continuity(_spec()) == []


def _relay_spec():
    """S3 接 S1 的尾帧，中间隔着 S2（DESIGN §30）：S3 的接缝另一半是 S1，不是 S2。"""
    spec = _spec()
    spec["shots"][1].pop("ref_frame", None)
    spec["shots"].append({"id": "S3", "dur": 5, "cast": ["room", "lin", "cat"], "shot": "特写，平视", "art": "她。", "motion": "她。",
                          "ref_tail": "S1", "lines": []})
    return spec


def test_previous_for_follows_tail_relay():
    spec = _relay_spec()
    s1, s2, s3 = spec["shots"]
    assert ct.previous_for(spec, s1) is None
    assert ct.previous_for(spec, s2) is s1
    assert ct.previous_for(spec, s3) is s1                     # 尾帧续接 → 源镜
    s3["ref_tail"] = {"shot": "S1", "mode": "direct"}
    assert ct.previous_for(spec, s3) is s1
    s3["ref_tail"] = "S9"                                      # 源镜不存在 → 退回列表顺序（形状问题由 check_tail_relay 报）
    assert ct.previous_for(spec, s3) is s2
    del s3["ref_tail"]
    assert ct.previous_for(spec, s3) is s2
    assert ct.previous_for(spec, {"id": "S3"}) is s2           # 同 id 的副本也认
    assert "previous_for" in ct.__all__


def test_continuity_compares_tail_relay_shot_with_its_source():
    spec = _relay_spec()
    s1, s2, s3 = spec["shots"]
    s1["exit"] = {"positions": "她坐长凳右端", "held_props": "她无；他左手拎伞"}
    s2["exit"] = {"positions": "她站在门口", "held_props": "她左手拎伞；他无"}
    s3["entry"] = {"positions": "她坐长凳右端", "held_props": "她无；他左手拎伞", "same_as_previous": ["positions"]}
    msgs = ct.check_continuity(spec)
    # 和 S1 一致：不报跳变、不报换手；勾「相同」的参照也是 S1 的出口
    assert not any("S3" in m and ("跳变" in m or "镜外变了" in m or "没有参照" in m) for m in msgs)
    # 改成和 S1 不一致 → 点名的是 S1，不是 S2
    s3["entry"] = {"positions": "她站在门口", "held_props": "她左手拎伞；他无"}
    msgs = [m for m in ct.check_continuity(spec) if m.startswith("shot S3")]
    assert any("上一镜 S1 出口持物「她无；他左手拎伞」" in m for m in msgs)
    assert any("位置在镜外跳变：上一镜 S1 出口「她坐长凳右端」" in m for m in msgs)
    assert not any("S2" in m for m in msgs)
    # 自由文本接缝同样对着源镜
    s1["exit_state"] = "她坐着。"; s3["entry_state"] = "她坐着。"; s3.pop("entry")
    assert any(m.startswith("shot S3") and "一字不差" in m for m in ct.check_continuity(spec))


def test_axis_compares_tail_relay_shot_with_its_source():
    spec = _relay_spec()
    s1, s2, s3 = spec["shots"]
    s1["axis"] = AXIS
    s2["axis"] = {**AXIS, "screen": {"lin": "right", "cat": "left"}, "break": True}    # S2 有意越轴
    s3["axis"] = {**AXIS, "screen": {"lin": "right", "cat": "left"}}                   # 和 S2 一样，但接的是 S1 的尾帧
    msgs = L.check_axis(spec)
    assert any("shot S3" in m and "越轴" in m and "上一镜 S1 占画面左" in m for m in msgs)
    assert not any("shot S3" in m and "S2" in m for m in msgs)
    s3["axis"] = {**AXIS, "screen": {"lin": "left", "cat": "right"}}                   # 回到 S1 的一侧：不报
    assert not any("shot S3" in m for m in L.check_axis(spec))


# ---------------------------------------------------------------------------- 轴线 lint


def test_axis_lint_flip_and_break():
    spec = _spec()
    spec["shots"][0]["axis"] = AXIS
    spec["shots"][1]["axis"] = {"anchors": ["cat", "lin"], "side": "left", "screen": {"lin": "right", "cat": "left"}}
    msgs = L.check_axis(spec)
    assert any("S2" in m and "越轴" in m and "林小满 上一镜 S1 占画面左，本镜占画面右" in m for m in msgs)
    spec["shots"][1]["axis"]["break"] = True
    assert not any("越轴" in m for m in L.check_axis(spec))
    # 机位侧换了、画面侧没写 → 也提醒
    spec["shots"][1]["axis"] = {"anchors": ["lin", "cat"], "side": "right"}
    assert any("机位从轴线左侧换到了右侧" in m for m in L.check_axis(spec))
    # 锚点不同就不比
    spec["shots"][1]["axis"] = {"anchors": ["lin", "room"], "side": "right", "screen": {"lin": "right"}}
    assert not any("越轴" in m or "机位从" in m for m in L.check_axis(spec))
    # 形状
    spec["shots"][1]["axis"] = {"anchors": ["lin", "cat"], "side": "up", "screen": {"lin": "middle"}}
    msgs = L.check_axis(spec)
    assert any("axis.side 只能是" in m for m in msgs) and any("axis.screen 里 林小满" in m for m in msgs)
    assert "check_axis" in L.__all__ and any("越轴" in m for m in L.check_all(_axis_flip_spec()))


def _axis_flip_spec():
    spec = _spec()
    spec["shots"][0]["axis"] = AXIS
    spec["shots"][1]["axis"] = {**AXIS, "screen": {"lin": "right", "cat": "left"}}
    return spec


def test_bare_left_right_hint_is_conservative():
    spec = _spec()
    spec["shots"][1]["motion"] = "他从右侧接近，向左看，站在左边的长凳旁"
    msgs = L.check_axis(spec)
    assert len(msgs) == 1 and "从右侧" in msgs[0] and "向左" in msgs[0] and "左边" in msgs[0] and "画面左右" in msgs[0]
    # 身体部位、「左右」、带主体的都不算
    spec["shots"][1]["motion"] = "她的右手握伞，左手插袋，左右为难，向画面左看，镜头右侧留暗，他站在她的左侧，屏幕左下角有字"
    assert L.check_axis(spec) == []
    spec["shots"][1]["motion"] = "she walks in from the left"      # 只查中文正文
    assert L.check_axis(spec) == []


# ---------------------------------------------------------------------------- 接口


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    return tmp_path


def _write(root: Path, episode_id: str = "BND-T") -> Path:
    spec = {
        "id": episode_id, "output_root": f"output/story_video/{episode_id}", "prompt_language": "zh",
        "camera_profile": "dark_comedy_vertical",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n",
        "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "block": "林小满：黑发。"}, "cat": {"name": "招财猫", "block": "金色。"}, "office": {"block": "办公区。"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["office", "lin", "cat"], "shot": "中景", "art": "a", "motion": "m", "lines": []},
            {"id": "S2", "dur": 4, "cast": ["office", "lin", "cat"], "shot": "近景", "art": "a", "motion": "m", "ref_frame": "S1", "lines": []},
        ],
    }
    path = root / "content" / "story_video" / f"{episode_id}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def _saved(path: Path, sid: str = "S2") -> dict:
    return next(s for s in json.loads(path.read_text(encoding="utf-8"))["shots"] if s["id"] == sid)


def test_shot_edit_roundtrip_and_clear(project):
    path = _write(project)
    resp = client.put("/api/episodes/storyvideo/BND-T/shots/S2", json={
        "entry": {**ENTRY, "visible_state": "  "}, "exit": EXIT, "axis": {**AXIS, "break": False}, "boundary_change": "伞在镜外已递给她",
    })
    assert resp.status_code == 200, resp.text
    saved = _saved(path)
    assert saved["entry"]["positions"] == ENTRY["positions"] and "visible_state" not in saved["entry"]
    assert saved["entry"]["same_as_previous"] == ["positions", "facing"] and saved["exit"] == EXIT
    assert saved["axis"] == AXIS and "break" not in saved["axis"] and saved["boundary_change"] == "伞在镜外已递给她"
    shot = resp.json()["shot"]
    assert shot["entry"]["positions"] == ENTRY["positions"] and shot["exit"]["gaze"] == EXIT["gaze"]
    assert shot["axis"]["screen"] == {"lin": "left", "cat": "right"} and shot["boundary_change"] == "伞在镜外已递给她"
    assert AXIS_ZH in shot["frame_prompt"] and "入口状态：位置——她坐长凳右端" in shot["video_prompt"]
    # 没动 = 原样；{} / "" = 清掉
    assert client.put("/api/episodes/storyvideo/BND-T/shots/S2", json={"sfx": "x"}).status_code == 200
    assert _saved(path)["axis"] == AXIS
    resp = client.put("/api/episodes/storyvideo/BND-T/shots/S2", json={"entry": {}, "exit": {}, "axis": {}, "boundary_change": ""})
    assert resp.status_code == 200
    saved = _saved(path)
    assert all(k not in saved for k in ("entry", "exit", "axis", "boundary_change"))
    assert resp.json()["shot"]["entry"] is None and resp.json()["shot"]["axis"] is None and resp.json()["shot"]["boundary_change"] == ""


@pytest.mark.parametrize("payload", [
    {"entry": {"positions": "x", "mood": "y"}},
    {"entry": {"positions": ["x"]}},
    {"entry": {"same_as_previous": ["mood"]}},
    {"exit": {"same_as_previous": "positions"}},
    {"entry": "她坐着"},
    {"axis": {"anchors": ["lin"]}},
    {"axis": {"anchors": ["lin", "lin"]}},
    {"axis": {"anchors": ["lin", "cat"], "side": "up"}},
    {"axis": {"anchors": ["lin", "cat"], "screen": {"lin": "middle"}}},
    {"axis": {"anchors": ["lin", "cat"], "break": "yes"}},
    {"axis": {"anchors": ["lin", "cat"], "tilt": 1}},
    {"axis": ["lin", "cat"]},
])
def test_shot_edit_validation_400_writes_nothing(project, payload):
    path = _write(project)
    before = path.read_text(encoding="utf-8")
    resp = client.put("/api/episodes/storyvideo/BND-T/shots/S2", json=payload)
    # 形状对、内容不对 = 400；连类型都不对（字符串 / 列表当对象）由 pydantic 挡在 422
    assert resp.status_code in (400, 422), resp.text
    assert path.read_text(encoding="utf-8") == before


def test_revise_shot_edit_reuses_apply(project):
    path = _write(project)
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = revise.apply_mutation(raw, {"kind": "shot_edit", "shot_id": "S2", "entry": ENTRY, "axis": AXIS, "boundary_change": "x"})
    shot = next(s for s in out["shots"] if s["id"] == "S2")
    assert shot["entry"]["positions"] == ENTRY["positions"] and shot["axis"] == AXIS and shot["boundary_change"] == "x"
    assert "entry" not in raw["shots"][1]   # 原件不动
