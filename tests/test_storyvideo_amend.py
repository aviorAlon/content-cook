"""冻结后修改后期块（post_graphics / voice_over）：amend 接受；改到生成字段：拒绝。"""
import json
from pathlib import Path

import pytest

from genkit.store import RunCollisionError
from storyvideo.runtime import POST_ONLY_KEYS, StoryVideoRunner


def _write_spec(root: Path, **extra) -> Path:
    spec = {
        "id": "AMEND-T", "output_root": "output/story_video/AMEND-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n",
        "cast": {"a": {"block": "A（真人）"}},
        "shots": [{"id": "S1", "dur": 4, "cast": ["a"], "shot": "s", "art": "a", "motion": "m", "lines": []}],
        "post_graphics": {"phone_ui_inserts": []},
        "voice_over": {"lines": [{"shot": "S1", "at": 0.5, "zh": "今天是周一。"}]},
    }
    spec.update(extra)
    path = root / "content" / "story_video" / "AMEND-T.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def test_amend_accepts_post_only_edit_and_refuses_generation_edit(tmp_path):
    _write_spec(tmp_path)
    with StoryVideoRunner(tmp_path) as runner:
        runner.prepare("AMEND-T")
        assert runner.amend_post_layers("AMEND-T")["status"] == "unchanged"
        # 只改后期块：接受，快照与 run 哈希同步更新，status 照常
        _write_spec(tmp_path, post_graphics={"phone_ui_inserts": [{"id": "UI_1", "after": "S1", "dur": 3, "layout": "plain", "lines": ["x"]}]},
                    voice_over={"edge": {"voice": "zh-CN-YunxiNeural"}, "lines": []})
        info = runner.amend_post_layers("AMEND-T")
        assert info["status"] == "amended" and info["changed_keys"] == ["post_graphics", "voice_over"]
        assert runner.status("AMEND-T")["run_id"] == "AMEND-T"
        # 改到会进提示词的字段：拒绝，并点名字段
        _write_spec(tmp_path, post_graphics={"phone_ui_inserts": [{"id": "UI_1", "after": "S1", "dur": 3, "layout": "plain", "lines": ["x"]}]},
                    voice_over={"edge": {"voice": "zh-CN-YunxiNeural"}, "lines": []}, neg="changed")
        with pytest.raises(RunCollisionError, match="neg"):
            runner.amend_post_layers("AMEND-T")


def test_post_only_keys_never_include_generation_inputs():
    for key in ("shots", "cast", "neg", "frame_style", "image_params", "video_params", "delivery"):
        assert key not in POST_ONLY_KEYS
