"""镜头风格 profile 改为经典产线（2026-09-08，DESIGN §35）——

profiles 只剩 5 条市面验证过的经典产线；旧六档挪进 legacy_profiles 只为已有项目兼容。
这里锁住：五档引用的 id 都真实存在、旧档仍能编译（金丝雀夹具用 ghost_pov）、接口只列新档、
示例项目在新档下能编译、起草的白名单文案对每档都能生成、库里的说明文案不带项目代号。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo.config_validate import validate_camera_library, validate_style_library
from storyvideo.prompts import (
    CameraError,
    camera_library,
    compile_camera_phrase,
    find_camera_profile,
    validate_camera_usage,
)
from storyvideo.scriptgen import ScriptGenError, _camera_allowlist_text, _style_guidance_text, style_library
from webui.app import app

NEW_PROFILES = ["classic_film", "short_drama_vertical", "anime_action", "tv_drama", "suspense_thriller"]
LEGACY_PROFILES = ["manhua_dialogue", "wuxia_action", "dark_comedy_vertical", "vlog_handheld", "ghost_pov", "static_book"]
# 项目代号（「ABC-DEF-001」「EP09」「EP1」这种）不该出现在随程序分发的说明文案里：库讲的是通用做法，不是某个项目的记录。
# 边界用「前后不是字母数字」而不是 \b：中文字符也算 \w，「样片EP09」里 \b 不成立
PROJECT_CODE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z]{3,}(?:-[A-Z0-9]+)*-\d{1,3}|EP\d{1,3})(?![A-Za-z0-9])")


def _strings(node):
    """库里所有字符串（含键名），用来查项目代号。"""

    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


client = TestClient(app)


def test_five_classic_profiles_with_valid_ids():
    lib = camera_library()
    assert list(lib["profiles"].keys()) == NEW_PROFILES
    shot_ids = {s["id"] for s in lib["shots"]}
    combo_ids = {c["id"] for c in lib["combos_within_shot"]}
    seq_ids = {c["id"] for c in lib["combos_sequence"]}
    for name, prof in lib["profiles"].items():
        assert set(prof["allow"]) <= shot_ids, name
        assert set(prof["combos"]) <= combo_ids, name
        assert set(prof["recommended_sequences"]) <= seq_ids, name
        assert prof["refs"] and prof["english"] and prof["用于"] and prof["why"], name
        assert isinstance(prof["max_moves_per_episode"], int), name
        assert not prof.get("legacy"), name
        # 出处（DESIGN §48）：按参考作品的公认风格人工设定，没做逐镜分析——置信度低
        assert prof["basis"] == "按参考作品的公认风格人工设定（2026-09-08），未做逐镜分析", name
        assert prof["confidence"] == "低", name
    assert validate_camera_library(lib) == []


def test_library_states_that_every_move_risk_is_inferred():
    lib = camera_library()
    assert all(s["risk_source"] == "inferred" for s in lib["shots"])
    assert "inferred" in lib["_note"] and "不证明单个运镜可靠" in lib["_note"]
    assert lib["_basis"] and "未做逐镜分析" in lib["_basis"]
    assert "单个运镜的 risk：低" in lib["_confidence"] and "_evidence" in lib["_confidence"]
    assert "蒸馏" not in lib["_usage"].replace("不是逐镜分析过的蒸馏来源", "")


def test_libraries_carry_no_project_codes():
    """两份库随程序分发、在配置页里给人看：说明文案只讲通用做法，不带任何项目代号。"""

    for name, lib in (("camera_library", camera_library()), ("style_library", style_library())):
        hits = sorted({m.group(0) for s in _strings(lib) for m in PROJECT_CODE.finditer(s)})
        assert hits == [], (name, hits)
    # 规则本身没写坏：带不带空格、紧挨中文、单个数字的集号都要认出来
    assert PROJECT_CODE.search("来自 ABC-DEF-001") and PROJECT_CODE.search("EP09 S1")
    assert PROJECT_CODE.search("样片EP09") and PROJECT_CODE.search("EP1 r2") and PROJECT_CODE.search("见ABC-7号")
    assert not PROJECT_CODE.search("STEP1") and not PROJECT_CODE.search("16:9")


def test_legacy_profiles_kept_but_marked_and_renamed():
    lib = camera_library()
    assert list(lib["legacy_profiles"].keys()) == LEGACY_PROFILES
    assert not set(lib["profiles"]) & set(lib["legacy_profiles"])
    for name, prof in lib["legacy_profiles"].items():
        assert prof["legacy"] is True, name
        assert prof["用于"].startswith("旧项目兼容"), name
        assert prof["basis"] == "旧版本沿用的档位：白名单与上限为兼容已有项目保留，未做逐镜标定", name
        assert prof["confidence"] == "中", name
    # 旧白名单一条没丢：金丝雀夹具靠它
    assert {"pov_float", "pov_punch", "pov_crouch", "pov_look_down"} <= set(lib["legacy_profiles"]["ghost_pov"]["allow"])
    assert find_camera_profile("ghost_pov") is lib["legacy_profiles"]["ghost_pov"]
    assert find_camera_profile("tv_drama") is lib["profiles"]["tv_drama"]
    assert find_camera_profile("") is None and find_camera_profile("nope") is None


def test_legacy_profile_still_compiles_and_still_restricts():
    spec = {"camera_profile": "ghost_pov", "prompt_language": "zh",
            "shots": [{"id": "S1", "camera_move": "pov_punch"}, {"id": "S2", "camera_move": "press_in"}]}
    assert "第一人称视角猛地往前一冲" in compile_camera_phrase(spec, "S1")
    with pytest.raises(CameraError):
        compile_camera_phrase(spec, "S2")
    with pytest.raises(CameraError, match="未知的 camera_profile"):
        compile_camera_phrase({"camera_profile": "gone", "shots": [{"id": "S1", "camera_move": "push"}]}, "S1")


def test_new_profile_allowlist_and_cap():
    spec = {"camera_profile": "short_drama_vertical", "prompt_language": "zh",
            "shots": [{"id": f"S{i}", "camera_move": "press_in"} for i in range(6)] + [{"id": "X", "camera_move": "orbit"}]}
    assert "往人物脸上靠" in compile_camera_phrase(spec, "S0")
    with pytest.raises(CameraError, match="short_drama_vertical"):
        compile_camera_phrase(spec, "X")
    spec["shots"].pop()
    warnings = validate_camera_usage(spec)
    assert len(warnings) == 1 and "上限 5" in warnings[0]
    # anime_action 开放 orbit，短剧不开放；classic_film 开放 oner
    assert "orbit" in camera_library()["profiles"]["anime_action"]["allow"]
    assert "along an arc around the character" in compile_camera_phrase(
        {"camera_profile": "anime_action", "shots": [{"id": "S1", "camera_move": "orbit"}]}, "S1")
    assert "oner" in camera_library()["profiles"]["classic_film"]["allow"]


def test_camera_options_lists_only_new_profiles_but_resolves_legacy():
    r = client.get("/api/camera-options")
    assert r.status_code == 200
    assert r.json()["profiles"] == sorted(NEW_PROFILES)
    assert r.json()["legacy"] is False
    # 出处与置信度（DESIGN §48）：新建项目页不用再拉整份库
    details = r.json()["profile_details"]
    assert set(details) == set(NEW_PROFILES)
    lib = camera_library()
    for name in NEW_PROFILES:
        assert details[name]["refs"] == lib["profiles"][name]["refs"], name
        assert details[name]["confidence"] == "低" and "未做逐镜分析" in details[name]["basis"], name
        assert details[name]["english"] == lib["profiles"][name]["english"], name

    r = client.get("/api/camera-options", params={"profile": "ghost_pov"})
    assert r.status_code == 200
    body = r.json()
    assert body["legacy"] is True
    assert body["profile_details"]["ghost_pov"]["confidence"] == "中" and body["profile_details"]["ghost_pov"]["refs"] == []
    assert {s["id"] for s in body["shots"]} == set(camera_library()["legacy_profiles"]["ghost_pov"]["allow"])

    r = client.get("/api/camera-options", params={"profile": "suspense_thriller"})
    assert r.status_code == 200
    assert r.json()["legacy"] is False
    assert {s["id"] for s in r.json()["shots"]} == set(camera_library()["profiles"]["suspense_thriller"]["allow"])
    assert {c["id"] for c in r.json()["combos"]} == {"spy", "vertigo"}

    assert client.get("/api/camera-options", params={"profile": "nope"}).status_code == 404


def test_example_episode_compiles_under_short_drama_vertical():
    spec = json.loads(Path("examples/episodes/first_episode.json").read_text(encoding="utf-8"))
    assert spec["camera_profile"] == "short_drama_vertical"
    moves = [s["camera_move"] for s in spec["shots"] if s.get("camera_move")]
    assert moves  # 示例真的用了运镜，不是空跑
    for shot in spec["shots"]:
        compile_camera_phrase(spec, shot["id"])  # 不抛
    assert validate_camera_usage(spec) == []


@pytest.mark.parametrize("profile", NEW_PROFILES)
def test_scriptgen_allowlist_and_style_guidance_for_each_new_profile(profile):
    lib = camera_library()
    text = _camera_allowlist_text(profile)
    assert f"camera_profile={profile!r}" in text
    for mid in lib["profiles"][profile]["allow"]:
        assert f"- {mid}（" in text
    for cid in lib["profiles"][profile]["combos"]:
        assert f"- {cid}（" in text
    assert f"上限：{lib['profiles'][profile]['max_moves_per_episode']} 镜" in text
    # 风格库里有同名骨架 + 清单，起草时不会退回 none
    style = style_library()
    assert profile in style["profiles"]
    guidance = _style_guidance_text(profile)
    assert style["frame_style_skeletons"][profile]["guardrail_cn"] in guidance
    must = [c["cn"] for c in style["scene_anchor_checklists"][profile] if c["universal"]]
    assert must and must[0] in guidance


def test_scriptgen_allowlist_resolves_legacy_and_lists_only_new_when_unknown():
    text = _camera_allowlist_text("dark_comedy_vertical")
    assert "- hold_stare（" in text
    with pytest.raises(ScriptGenError) as exc:
        _camera_allowlist_text("nope")
    msg = str(exc.value)
    for p in NEW_PROFILES:
        assert p in msg
    for p in LEGACY_PROFILES:
        assert p not in msg


def test_style_library_keeps_legacy_keys():
    style = style_library()
    assert validate_style_library(style) == []
    for key in NEW_PROFILES + LEGACY_PROFILES + ["none"]:
        assert key in style["frame_style_skeletons"] and key in style["scene_anchor_checklists"], key
    for key in LEGACY_PROFILES:
        assert style["frame_style_skeletons"][key]["用于"].startswith("旧项目兼容"), key


def test_validate_camera_library_checks_legacy_and_rejects_clash():
    lib = json.loads(json.dumps(camera_library()))
    lib["legacy_profiles"]["ghost_pov"]["allow"].append("no_such_move")
    errors = validate_camera_library(lib)
    assert any("'ghost_pov'.allow" in e and "no_such_move" in e for e in errors)
    lib = json.loads(json.dumps(camera_library()))
    lib["legacy_profiles"]["tv_drama"] = dict(lib["profiles"]["tv_drama"])
    assert any("重名" in e and "tv_drama" in e for e in validate_camera_library(lib))
    lib = json.loads(json.dumps(camera_library()))
    lib["legacy_profiles"] = "bad"
    assert any("legacy_profiles" in e for e in validate_camera_library(lib))
    lib = json.loads(json.dumps(camera_library()))
    del lib["legacy_profiles"]
    assert validate_camera_library(lib) == []
