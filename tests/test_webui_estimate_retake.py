"""webui.generate.estimate_plan：重摇后的镜（账本里 prepared、旧文件仍在）必须算作待生成，
否则 UI 显示「没有新东西要生成」，看起来像按钮没反应。"""
import json

from genkit.store import RunStore
from storyvideo.spec import normalize_spec
from webui.generate import estimate_plan


def _spec(tmp_path, ep="EP-T"):
    root = tmp_path
    (root / "content" / "story_video").mkdir(parents=True)
    raw = {
        "id": ep, "output_root": f"output/story_video/{ep}",
        "cast": {"su": {"block": "su"}},
        "shots": [{"id": "S1", "dur": 5, "cast": ["su"], "lines": [{"who": "su", "zh": "嗨。"}],
                   "shot": "wide", "art": "a room", "motion": "su waves"}],
        "subtitle": {"language": "zh"},
    }
    p = root / "content" / "story_video" / f"{ep}.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return normalize_spec(raw, spec_path=str(p), project_root=str(root))


def test_estimate_counts_retaken_shot_as_pending_even_if_file_exists(tmp_path):
    spec = _spec(tmp_path)
    out = tmp_path / "output" / "story_video" / "EP-T"
    (out / "02_video").mkdir(parents=True)
    (out / "02_video" / "S1.mp4").write_bytes(b"old take")
    # 没有账本：按文件判断 → 已有，跳过
    assert estimate_plan(spec, through="videos", only=None)["pending_count"] == 0
    # 有账本且这一步是 prepared（重摇后被重置）→ 待生成
    with RunStore(out.parent / "storyvideo.sqlite3") as store:
        store.create_run("EP-T", "storyvideo", "EP-T", "h", "p", out)
        store.ensure_job("EP-T", "video-S1", "video", "rh", out / "02_video" / "S1.mp4")
    est = estimate_plan(spec, through="videos", only=["S1"])
    assert est["pending_count"] == 1 and est["items"][0]["already_have"] is False


def test_estimate_marks_in_flight_job_as_in_progress_not_pending(tmp_path):
    spec = _spec(tmp_path, ep="EP-U")
    out = tmp_path / "output" / "story_video" / "EP-U"
    (out / "02_video").mkdir(parents=True)
    (out / "02_video" / "S1.mp4").write_bytes(b"old take")
    with RunStore(out.parent / "storyvideo.sqlite3") as store:
        store.create_run("EP-U", "storyvideo", "EP-U", "h", "p", out)
        store.ensure_job("EP-U", "video-S1", "video", "rh", out / "02_video" / "S1.mp4")
        store.mark_submitting("EP-U", "video-S1")
        store.mark_submitted("EP-U", "video-S1", "task-1", "pending")
    est = estimate_plan(spec, through="videos", only=["S1"])
    assert est["pending_count"] == 0 and est["in_progress"] == ["S1"] and est["already_have_count"] == 0
