"""vlog_handheld 产线 profile（2026-09-05）——

镜头语言库新增了 4 个手机持机镜位和 1 条 profile；prompts.compile_camera_phrase 为
amplitude="sudden" 的镜头换尾句（yank_jolt 后面不能再跟 slow and small）。这里锁住
这两处行为，防止改库或编译器改动时悄悄丢掉。

2026-09-08 起 vlog_handheld / ghost_pov 是 legacy_profiles（旧项目兼容），不再列给新项目；
编译器按 profiles → legacy_profiles 查找，所以下面的 spec 照旧能编译（见 test_camera_profiles.py）。
"""

import pytest

from storyvideo.prompts import CameraError, camera_library, compile_camera_phrase
from storyvideo.scriptgen import style_library


def _spec(move: str) -> dict:
    return {"camera_profile": "vlog_handheld", "shots": [{"id": "S1", "camera_move": move}]}


def test_vlog_profile_exists_in_both_libraries():
    # 2026-09-08 起是旧档：定义在 legacy_profiles，不在 profiles（新建项目的下拉不再列它）
    assert "vlog_handheld" in camera_library()["legacy_profiles"]
    assert "vlog_handheld" not in camera_library()["profiles"]
    assert camera_library()["legacy_profiles"]["vlog_handheld"]["legacy"] is True
    assert "vlog_handheld" in style_library()["frame_style_skeletons"]
    assert "vlog_handheld" in style_library()["scene_anchor_checklists"]


def test_vlog_profile_allows_phone_moves_and_rejects_cinema_moves():
    assert "front phone camera" in compile_camera_phrase(_spec("selfie_arm"), "S1")
    with pytest.raises(CameraError):
        compile_camera_phrase(_spec("press_in"), "S1")
    with pytest.raises(CameraError):
        compile_camera_phrase(_spec("orbit"), "S1")


def test_sudden_amplitude_swaps_the_slow_small_suffix():
    jolt = compile_camera_phrase(_spec("yank_jolt"), "S1")
    assert "sudden" in jolt
    assert "slow, small" not in jolt
    gentle = compile_camera_phrase(_spec("phone_walk"), "S1")
    assert "slow, small and motivated" in gentle


def test_vlog_skeleton_guardrail_blocks_both_ends():
    # 这档不走写实：写实真人在对白镜容易落进恐怖谷，所以用轻度 CG 化 3D。
    # 警戒句两头都要防：照片级写实（恐怖谷）和 2D 动漫/赛璐璐（过头）。
    sk = style_library()["frame_style_skeletons"]["vlog_handheld"]
    assert "照片级写实" in sk["guardrail_cn"]
    assert "2D 动漫" in sk["guardrail_cn"]
    assert "photorealistic" in sk["guardrail_en"] and "2D anime" in sk["guardrail_en"]


def test_zh_project_gets_chinese_compiler_tails():
    # 中文项目用中文提示词：编译器写死的运镜尾句 / 音效标签 / 口型锁 / 无文字尾句
    # 都不能再混英文进去。en 项目行为不变。
    from storyvideo.prompts import compile_frame_prompt, compile_video_prompt

    shot = {"id": "S1", "cast": [], "shot": "前置自拍", "art": "傍晚客厅", "motion": "她举着手机往前走",
            "camera_move": "yank_jolt", "sfx": "远处一声车铃", "lines": [{"who": "a", "zh": "出发。"}]}
    zh = {"camera_profile": "vlog_handheld", "prompt_language": "zh", "cast": {}, "shots": [shot]}
    en = {"camera_profile": "vlog_handheld", "prompt_language": "en", "cast": {}, "shots": [shot]}
    v = compile_video_prompt(zh, "S1")
    assert "镜头：" in v and "音效：远处一声车铃" in v and "嘴随台词自然开合" in v
    assert "CAMERA:" not in v and "SOUND:" not in v and "slow, small" not in v
    assert compile_frame_prompt(zh, "S1").endswith("画面中绝对不出现任何文字、字母、数字或符号。")
    ve = compile_video_prompt(en, "S1")
    assert "CAMERA:" in ve and "SOUND: 远处一声车铃" in ve and "mouth opens" in ve
    assert compile_frame_prompt(en, "S1").endswith("symbols anywhere in the image. ")


def test_auto_dialogue_block_follows_lines_not_motion():
    # dialogue_prompt="auto"：台词段按 lines 生成，UI 改 lines 就改了模型念的词；未开启时不注入。
    from storyvideo.prompts import compile_video_prompt

    def spec(mode, speech_mode=None, lang="zh"):
        shot = {"id": "S1", "cast": ["w"], "shot": "x", "art": "y", "motion": "她对着镜头打招呼。",
                "lines": [{"who": "w", "zh": "大家好。"}, {"who": "w", "zh": "今天去河边走走。"}]}
        if speech_mode:
            shot["speech_mode"] = speech_mode
        d = {"prompt_language": lang, "cast": {"w": {"name": "阿沅", "voice": "清亮的年轻女声"}}, "shots": [shot]}
        if mode:
            d["dialogue_prompt"] = mode
        return d

    on = compile_video_prompt(spec("auto"), "S1")
    assert "台词：阿沅用普通话说：「大家好。」，然后：「今天去河边走走。」——清亮的年轻女声。嘴型与台词一致。" in on
    assert "0.8 秒内开口" in on and on.startswith("她对着镜头打招呼。 台词：")
    off = compile_video_prompt(spec("auto", "offscreen_narration"), "S1")
    assert "画外的阿沅（不在画面里）" in off and "嘴型与台词一致" not in off
    assert "台词：" not in compile_video_prompt(spec(None), "S1")
    en = compile_video_prompt(spec("auto", lang="en"), "S1")
    assert 'LINES: 阿沅 says: "大家好。", then: "今天去河边走走。"' in en


def test_auto_dialogue_block_carries_per_line_tone():
    from storyvideo.prompts import compile_video_prompt
    shot = {"id": "S1", "cast": ["w"], "shot": "x", "art": "y", "motion": "她说。",
            "lines": [{"who": "w", "zh": "……灯别关。", "tone": "半梦半醒，侧躺着，声音发黏、很轻"}, {"who": "w", "zh": "嗯。"}]}
    spec = {"prompt_language": "zh", "dialogue_prompt": "auto", "cast": {"w": {"name": "阿沅"}}, "shots": [shot]}
    v = compile_video_prompt(spec, "S1")
    assert "「……灯别关。」（半梦半醒，侧躺着，声音发黏、很轻），然后：「嗯。」" in v


def test_merge_shot_edit_keeps_and_clears_tone():
    from webui.deps import merge_shot_edit
    target = {"lines": [{"who": "w", "zh": "a", "tone": "小声"}, {"who": "w", "zh": "b", "tone": "大声"}]}
    merge_shot_edit(target, lines=[{"who": "w", "zh": "a2"}, {"who": "w", "zh": "b", "tone": ""}], camera_move=None, camera_combo=None, dur=None)
    assert target["lines"][0]["tone"] == "小声" and target["lines"][0]["zh"] == "a2"   # 没传 tone → 保留
    assert "tone" not in target["lines"][1]                                          # 传空串 → 清掉


def test_frame_request_drops_quality_for_seedream():
    from storyvideo.prompts import frame_request_data
    shot = {"id": "S1", "cast": [], "shot": "x", "art": "y", "motion": "z"}
    base = {"id": "EP", "cast": {}, "shots": [shot]}
    assert frame_request_data({**base, "image_params": {"model": "doubao-seedream-5-0-pro-260628", "quality": "high"}}, "S1")["quality"] is None
    assert frame_request_data({**base, "image_params": {"model": "gpt-image-2"}}, "S1")["quality"] == "high"


def test_ghost_pov_profile_and_sudden_punch():
    # 全程第一人称视角：没有手机四镜也没有推轨/环绕；pov_punch 与 yank_jolt 同为突发幅度
    import json
    from pathlib import Path
    lib = json.loads(Path("skills/ai-short-film/scripts/camera_library.json").read_text(encoding="utf-8"))
    prof = lib["legacy_profiles"]["ghost_pov"]   # 2026-09-08 起是旧档
    assert {"pov_float", "pov_punch", "pov_crouch", "pov_look_down", "hold_stare"} <= set(prof["allow"])
    assert not {"selfie_arm", "phone_walk", "orbit", "push"} & set(prof["allow"])
    punch = next(s for s in lib["shots"] if s["id"] == "pov_punch")
    assert punch["amplitude"] == "sudden"
    assert "手机" not in punch["prompt_cn"] and punch["prompt_cn"].startswith("第一人称视角")
