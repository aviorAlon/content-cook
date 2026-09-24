"""提示词金丝雀：编译器/镜头库/风格尾句任何改动都会在这里现形。有意改动就跑 scripts/refresh_prompt_canary.py --update。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import refresh_prompt_canary as canary  # noqa: E402


def test_prompts_match_golden():
    spec = json.loads(canary.SPEC_PATH.read_text(encoding="utf-8"))
    expected = json.loads(canary.EXPECTED_PATH.read_text(encoding="utf-8"))
    actual = canary.compile_all(spec)
    drift = canary.diff(expected, actual)
    assert not drift, "\n".join(drift) + "\n\n提示词漂了。确认是有意的再: python scripts/refresh_prompt_canary.py --update"


def test_golden_covers_the_features_it_claims():
    expected = json.loads(canary.EXPECTED_PATH.read_text(encoding="utf-8"))
    assert expected["S1_JOLT"]["video"].endswith("出口状态：她一只手扶着顶层的马克杯，另一只手拿着抽出来的书；客厅安静。")
    assert expected["S2_TURN"]["video"].startswith("入口状态：")
    assert "[2–6s] 表演：" in expected["S2_TURN"]["video"]
    assert "幅度突发且短促" in expected["S1_JOLT"]["video"]           # pov_punch = sudden
    assert "台词：宋晴用普通话说：「书我自己拿就行，你坐着吧。」" in expected["S3_LINE"]["video"]
    assert "画外的宋晴（不在画面里）" in expected["S4_OFFSCREEN"]["video"]
    assert "谁也不看镜头" in expected["S3_LINE"]["video"] and "谁也不看镜头" not in expected["S5_SELFIE"]["video"]
    assert "视觉风格——媒介与技法" in expected["S1_JOLT"]["frame"]
    assert expected["S1_JOLT"]["negative"] == "可读文字, 字幕, 水印"


def test_golden_covers_every_form_card_in_both_languages():
    """形态卡的运动预算句与构图句进视频提示词：每张卡中英各有一份黄金输出，卡的措辞一改就在这里现形。"""

    from storyvideo.forms import composition_sentence, form_cards, motion_budget_sentence

    expected = json.loads(canary.EXPECTED_PATH.read_text(encoding="utf-8"))
    spec = json.loads(canary.SPEC_PATH.read_text(encoding="utf-8"))
    cards = form_cards()
    assert cards
    for card in cards:
        for lang in ("zh", "en"):
            variant = {**spec, "form": card["id"], "prompt_language": lang}
            video = expected[f"FORM_{card['id']}_{lang}"]["video"]
            assert composition_sentence(variant) in video and motion_budget_sentence(variant) in video, (card["id"], lang)
    # 没写 form 的镜头不带形态句（老项目零变化）
    plain = expected[canary.FORM_SHOT]["video"]
    assert composition_sentence({**spec, "form": cards[0]["id"]}) not in plain
