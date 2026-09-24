"""config_validate.py 保护的是「基础配置」管理页（webui）写盘前的最后一道
关卡——这两份 JSON 改错了会波及所有项目的起草/校验，之前完全没有测试覆盖。
这里直接测纯函数本身，不需要起 webui 服务。
"""

from storyvideo.config_validate import validate_camera_library, validate_style_library


def valid_camera_library(**overrides):
    lib = {
        "shots": [{"id": "static_wide", "cn": "静止远景"}],
        "combos_within_shot": [{"id": "combo_a", "cn": "组合A"}],
        "combos_sequence": [{"id": "seq_a", "cn": "序列A"}],
        "profiles": {
            "demo_profile": {
                "用于": "测试",
                "allow": ["static_wide"],
                "combos": ["combo_a"],
                "recommended_sequences": ["seq_a"],
            }
        },
    }
    lib.update(overrides)
    return lib


def valid_style_library(**overrides):
    lib = {
        "neg_pool": [{"id": "neg_a", "cn": "负向词A"}],
        "frame_style_skeletons": {"demo_profile": {"用于": "测试"}},
        "scene_anchor_checklists": {"demo_profile": [{"id": "check_a"}]},
    }
    lib.update(overrides)
    return lib


# ---- validate_camera_library ----


def test_valid_camera_library_has_no_errors():
    assert validate_camera_library(valid_camera_library()) == []


def test_rejects_non_dict_top_level():
    assert validate_camera_library([1, 2, 3]) == ["顶层必须是一个 JSON 对象"]


def test_detects_duplicate_shot_id():
    lib = valid_camera_library()
    lib["shots"].append({"id": "static_wide", "cn": "重复的镜头"})
    errors = validate_camera_library(lib)
    assert any("shots 里有重复 id" in e for e in errors)


def test_detects_duplicate_combo_within_shot_id():
    lib = valid_camera_library()
    lib["combos_within_shot"].append({"id": "combo_a", "cn": "重复组合"})
    errors = validate_camera_library(lib)
    assert any("combos_within_shot 里有重复 id" in e for e in errors)


def test_detects_duplicate_combo_sequence_id():
    lib = valid_camera_library()
    lib["combos_sequence"].append({"id": "seq_a", "cn": "重复序列"})
    errors = validate_camera_library(lib)
    assert any("combos_sequence 里有重复 id" in e for e in errors)


def test_profile_allow_referencing_unknown_shot_is_rejected():
    lib = valid_camera_library()
    lib["profiles"]["demo_profile"]["allow"] = ["does_not_exist"]
    errors = validate_camera_library(lib)
    assert any("引用了不存在的镜头 id" in e for e in errors)


def test_profile_combos_must_reference_combos_within_shot_not_combos_sequence():
    """这条检查是防回归写的——combos_within_shot 和 combos_sequence 两个 id
    空间长得像、很容易填混（镜内组合 vs 跨镜节拍）。profile
    的 combos 字段错误地填了一个 combos_sequence 的 id，必须被拒绝，而且
    错误信息要点明是不是填错地方了。
    """
    lib = valid_camera_library()
    lib["profiles"]["demo_profile"]["combos"] = ["seq_a"]  # 该填 combo_a，误填了 seq_a
    errors = validate_camera_library(lib)
    assert any("是不是把 combos_sequence 的 id 填错地方了" in e for e in errors)


def test_profile_recommended_sequences_referencing_unknown_seq_is_rejected():
    lib = valid_camera_library()
    lib["profiles"]["demo_profile"]["recommended_sequences"] = ["does_not_exist"]
    errors = validate_camera_library(lib)
    assert any("引用了不存在的镜头组" in e for e in errors)


def test_profile_must_be_an_object():
    lib = valid_camera_library()
    lib["profiles"]["broken_profile"] = "not an object"
    errors = validate_camera_library(lib)
    assert any("必须是对象" in e for e in errors)


# ---- validate_style_library ----


def test_valid_style_library_has_no_errors():
    assert validate_style_library(valid_style_library()) == []


def test_detects_duplicate_neg_pool_id():
    lib = valid_style_library()
    lib["neg_pool"].append({"id": "neg_a", "cn": "重复负向词"})
    errors = validate_style_library(lib)
    assert any("neg_pool 里有重复 id" in e for e in errors)


def test_skeleton_and_checklist_profile_sets_must_match():
    """基础配置页的逐条 CRUD 就是靠这条检查兜底的——skeleton 和 checklist
    按 profile 名字成对存在，只改一边会在这里被拒绝，而不是留一个半成品。
    """
    lib = valid_style_library()
    lib["frame_style_skeletons"]["orphan_profile"] = {"用于": "只有骨架没有清单"}
    errors = validate_style_library(lib)
    assert any("profile 集合对不上" in e for e in errors)
    assert any("orphan_profile" in e for e in errors)


def test_checklist_item_id_duplicates_are_rejected():
    lib = valid_style_library()
    lib["scene_anchor_checklists"]["demo_profile"].append({"id": "check_a"})
    errors = validate_style_library(lib)
    assert any("检查项 id 重复" in e for e in errors)


def test_rejects_non_dict_top_level_for_style_library():
    assert validate_style_library("not a dict") == ["顶层必须是一个 JSON 对象"]
