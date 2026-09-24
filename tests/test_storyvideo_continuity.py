"""continuity 检查 + prompts 的四个 opt-in 能力（入口/出口状态、beats、表演锁、视觉风格）。老 spec 零变化。"""
from storyvideo import continuity as ct
from storyvideo.prompts import compile_frame_prompt, compile_video_prompt


def _base(**extra):
    spec = {
        "prompt_language": "zh", "dialogue_prompt": "auto",
        "frame_style": "F。", "scene_anchor": "S。", "camera": "C。",
        "cast": {"w": {"name": "阿宁", "block": "阿宁：短卷发，灰色卫衣。"}, "room": {"block": "书房。"}},
        "shots": [
            {"id": "S1", "dur": 5, "cast": ["room", "w"], "shot": "中景", "art": "她坐在书桌前。", "motion": "她抬头。",
             "camera_move": "hold_stare", "lines": []},
            {"id": "S2", "dur": 5, "cast": ["room", "w"], "shot": "近景", "art": "她看着手机。", "motion": "她开口。",
             "camera_move": "hold_stare", "ref_frame": "S1", "lines": []},
        ],
    }
    spec.update(extra)
    return spec


def test_old_spec_compiles_unchanged():
    spec = _base()
    v = compile_video_prompt(spec, "S2")
    assert "入口状态" not in v and "出口状态" not in v and "情绪只从看得见的小动作里出来" not in v and "谁也不看镜头" not in v
    assert "视觉风格" not in compile_frame_prompt(spec, "S1")
    assert ct.check_continuity(spec) == []          # 没用状态字段就不吵


def test_entry_exit_states_and_beats_and_locks():
    spec = _base(performance_lock=True)
    spec["shots"][0]["exit_state"] = "她坐在书桌前，面向手机，手指停在屏幕上方。"
    spec["shots"][1]["entry_state"] = "她坐在书桌前，面向手机，手指已停住。"
    spec["shots"][1]["exit_state"] = "她说完，视线仍在手机上。"
    spec["shots"][1]["beats"] = [{"t": "0-2", "picture": "屏幕亮起", "sound": "嗡嗡"}, {"t": "2-5", "performance": "她眉头皱起"}]
    v = compile_video_prompt(spec, "S2")
    assert v.startswith("入口状态：她坐在书桌前")
    assert v.endswith("出口状态：她说完，视线仍在手机上。")
    assert "[0–2s] 画面：屏幕亮起；声音：嗡嗡" in v and "[2–5s] 表演：她眉头皱起" in v
    assert "情绪最多转一次" in v and "情绪只从看得见的小动作里出来" in v and "谁也不看镜头" in v
    f = compile_frame_prompt(spec, "S2")
    assert "入口状态：她坐在书桌前" in f


def test_gaze_lock_skipped_for_selfie_or_explicit_camera_gaze():
    spec = _base(performance_lock=True)
    spec["shots"][0]["camera_move"] = "hold_stare"
    spec["shots"][0]["gaze"] = "camera"
    assert "谁也不看镜头" not in compile_video_prompt(spec, "S1")
    spec["shots"][0].pop("gaze")
    spec["shots"][0]["cast"] = ["room"]           # 没有人物就不加视线句
    assert "谁也不看镜头" not in compile_video_prompt(spec, "S1")


def test_visual_style_clause_in_frame_prompt():
    spec = _base(visual_style={"medium": "水彩插画，纸纹可见", "palette": [{"hex": "#DDE6ED", "role": "主色"}, {"hex": "#1F2A36", "role": "暗部"}],
                               "edge": "细描线", "finish": "全片统一完成度"})
    f = compile_frame_prompt(spec, "S1")
    assert "视觉风格——媒介与技法：水彩插画，纸纹可见；色板：#DDE6ED（主色）、#1F2A36（暗部）；线与边缘：细描线；完成度：全片统一完成度" in f


def test_continuity_warnings():
    spec = _base()
    spec["shots"][1]["entry_state"] = "x"
    w = ct.check_continuity(spec)
    assert any("S2" in x and "没有 exit_state" in x for x in w)       # 上一镜缺出口
    spec["shots"][0]["exit_state"] = "x"
    assert any("一字不差" in x for x in ct.check_continuity(spec))
    spec["shots"][1].pop("entry_state")
    assert any("有 ref_frame 但没写 entry_state" in x for x in ct.check_continuity(spec))


def test_performance_and_shot_design_and_beats_warnings():
    spec = _base()
    spec["shots"][0]["motion"] = "她很悲伤地看着手机，惊恐地后退。"
    spec["shots"][1]["shot"] = "第一人称站在门口"          # 有「第一人称」算读得出机位
    spec["shots"][1]["beats"] = [{"t": "0-3"}, {"t": "2-6"}, {"t": "x"}]
    spec["cast"]["w"]["block"] = "阿宁：短卷发。默认表情：嘴角微翘。"
    w = ct.check_all(spec)
    assert any("情绪形容词「悲伤、惊恐」" in x for x in w)
    assert any("角色描述里有「默认表情、嘴角微翘」" in x for x in w)
    assert any("回到了更早的时间" in x for x in w) and any("超过了 dur" in x for x in w) and any("t 写法" in x for x in w)
    spec["shots"][0]["shot"] = "站在门口看她"
    assert any("读不出景别" in x for x in ct.check_shot_design(spec))
