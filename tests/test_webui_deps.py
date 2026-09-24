"""src/webui/deps.py 里跨路由共享的纯函数/辅助——app.py 拆分成 APIRouter
之前这些逻辑散在一个 912 行的文件里，没有任何测试覆盖。
"""

import json
from pathlib import Path

import webui.deps as deps
from webui.deps import is_locked, list_dir, merge_shot_edit, uses_legacy_shot_format


# ---- merge_shot_edit ----
# 回归测试：整体替换 lines 会把双语项目的
# en 字段静默冲掉（界面只显示/编辑中文，不能把没被编辑的字段一起抹掉）。


def test_merge_shot_edit_preserves_untouched_fields_like_en():
    target = {
        "id": "S1",
        "lines": [{"who": "su", "en": "Hello.", "zh": "你好。", "subtitle_start_seconds": 0.0}],
    }
    merge_shot_edit(target, lines=[{"who": "su", "zh": "你好呀。"}], camera_move=None, camera_combo=None, dur=None)
    assert target["lines"][0]["en"] == "Hello."  # 没被编辑的字段原样保留
    assert target["lines"][0]["zh"] == "你好呀。"
    assert target["lines"][0]["subtitle_start_seconds"] == 0.0


def test_merge_shot_edit_new_line_beyond_original_length_gets_defaults():
    target = {"id": "S1", "lines": [{"who": "a", "zh": "line1"}]}
    merge_shot_edit(
        target,
        lines=[{"who": "a", "zh": "line1"}, {"who": "b", "zh": "line2"}],
        camera_move=None, camera_combo=None, dur=None,
    )
    assert len(target["lines"]) == 2
    assert target["lines"][1]["zh"] == "line2"


def test_merge_shot_edit_camera_move_clears_camera_combo():
    target = {"id": "S1", "lines": [], "camera_combo": "combo_a"}
    merge_shot_edit(target, lines=None, camera_move="push_in", camera_combo=None, dur=None)
    assert target["camera_move"] == "push_in"
    assert "camera_combo" not in target


def test_merge_shot_edit_camera_combo_clears_camera_move():
    target = {"id": "S1", "lines": [], "camera_move": "push_in"}
    merge_shot_edit(target, lines=None, camera_move=None, camera_combo="combo_a", dur=None)
    assert target["camera_combo"] == "combo_a"
    assert "camera_move" not in target


def test_merge_shot_edit_empty_string_clears_camera_field_entirely():
    target = {"id": "S1", "lines": [], "camera_move": "push_in"}
    merge_shot_edit(target, lines=None, camera_move="", camera_combo=None, dur=None)
    assert "camera_move" not in target  # 空字符串代表"清空运镜声明"，不是留一个空字符串


def test_merge_shot_edit_updates_dur():
    target = {"id": "S1", "lines": [], "dur": 5}
    merge_shot_edit(target, lines=None, camera_move=None, camera_combo=None, dur=7.5)
    assert target["dur"] == 7.5


def test_merge_shot_edit_none_fields_leave_target_untouched():
    target = {"id": "S1", "lines": [{"who": "a", "zh": "x"}], "dur": 5}
    original = dict(target)
    merge_shot_edit(target, lines=None, camera_move=None, camera_combo=None, dur=None)
    assert target["lines"] == original["lines"]
    assert target["dur"] == original["dur"]


# ---- is_locked ----


def test_is_locked_true_when_snapshot_exists(tmp_path):
    (tmp_path / "manifests").mkdir()
    (tmp_path / "manifests" / "episode.snapshot.json").write_text("{}", encoding="utf-8")
    assert is_locked(str(tmp_path)) is True


def test_is_locked_false_when_no_snapshot(tmp_path):
    assert is_locked(str(tmp_path)) is False


# ---- uses_legacy_shot_format ----


def test_detects_legacy_speaker_en_zh_shot():
    raw = {"shots": [{"id": "S1", "speaker": "su", "en": "Hi", "zh": "嗨"}]}
    assert uses_legacy_shot_format(raw) is True


def test_modern_lines_shot_is_not_legacy():
    raw = {"shots": [{"id": "S1", "lines": [{"who": "su", "zh": "嗨"}]}]}
    assert uses_legacy_shot_format(raw) is False


def test_mixed_shots_any_legacy_one_triggers_true():
    raw = {
        "shots": [
            {"id": "S1", "lines": [{"who": "su", "zh": "嗨"}]},
            {"id": "S2", "speaker": "su", "zh": "旧格式"},
        ]
    }
    assert uses_legacy_shot_format(raw) is True


# ---- list_dir ----
# 回归测试：一个目录里有一份损坏的 JSON，不该让整个列表接口崩掉——损坏的那份
# 标 status=error 单独列出来，其它项目正常显示。


def _fake_loader(path, *, project_root):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "output_root" not in data:
        raise ValueError("missing output_root")
    return data


def test_list_dir_survives_one_broken_file(tmp_path, monkeypatch):
    # list_dir 拼 entry["path"] 时用的是 deps.PROJECT_ROOT（模块级常量，真实
    # 项目根目录）——测试用的 tmp_path 不在那棵目录树下，得patch成 tmp_path
    # 自己才能算出相对路径，跟 webui 路由测试用的是同一个隔离技巧。
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    good = tmp_path / "GOOD-001.json"
    good.write_text(
        json.dumps({"id": "GOOD-001", "output_root": str(tmp_path / "out"), "shots": [1, 2]}),
        encoding="utf-8",
    )
    broken = tmp_path / "BROKEN-002.json"
    broken.write_text("{not valid json", encoding="utf-8")

    items = list_dir(tmp_path, "storyvideo", _fake_loader)

    by_id = {i["id"]: i for i in items}
    assert by_id["GOOD-001"]["status"] == "ok"
    assert by_id["GOOD-001"]["shot_count"] == 2
    assert by_id["BROKEN-002"]["status"] == "error"
    assert "error" in by_id["BROKEN-002"]


def test_list_dir_empty_for_missing_directory(tmp_path):
    assert list_dir(tmp_path / "does-not-exist", "storyvideo", _fake_loader) == []
