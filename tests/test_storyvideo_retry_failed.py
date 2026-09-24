"""失败任务原地重提：failed → prepared；succeeded/prepared 不动。"""
import json
from pathlib import Path

from storyvideo.runtime import StoryVideoRunner


def test_retry_failed_resets_only_failed_jobs(tmp_path):
    spec = {
        "id": "RETRY-T", "output_root": "output/story_video/RETRY-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n",
        "cast": {"a": {"block": "A（真人）"}},
        "shots": [{"id": "S1", "dur": 4, "cast": ["a"], "shot": "s", "art": "a", "motion": "m", "lines": []},
                  {"id": "S2", "dur": 4, "cast": ["a"], "shot": "s", "art": "a", "motion": "m", "lines": []}],
    }
    path = tmp_path / "content" / "story_video" / "RETRY-T.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    with StoryVideoRunner(tmp_path) as runner:
        runner.prepare("RETRY-T")
        runner.store.mark_failed("RETRY-T", "frame-S1", "服务端处理超时")
        info = runner.retry_failed("RETRY-T")
        assert info["reset"] == ["frame-S1"] and info["skipped"] == []
        assert runner.store.get_job("RETRY-T", "frame-S1")["status"] == "prepared"
        assert runner.store.get_job("RETRY-T", "frame-S2")["status"] == "prepared"
        assert runner.retry_failed("RETRY-T")["reset"] == []
