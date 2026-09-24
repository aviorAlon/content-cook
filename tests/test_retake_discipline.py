"""重拍处置与重投纪律（DESIGN §24，融合 drama-skills 处置五分法 + 生产纪律 §五.6）。

- QC 裁决记录带可选的缺陷 / 处置元数据与历史；老 qc.json 没有这些键照常加载。
- 账本按 (run, step) 数同一份请求提交了几次：prepare → 提交 → 重拍 → 再提交。
- review 接口：同一份提示词已出过 2 次再原样重提 → 409；force 放行；改文字重出不拦；
  保留 / 后期处理落成 pass / waive；换模型对比不落账（400）。
- 镜头详情暴露 attempts 与 qc_meta。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo.qc import (
    QCError,
    item_meta,
    load_qc,
    record_technical_results,
    review as review_qc,
)
from storyvideo.runtime import RESUBMIT_LIMIT, StoryVideoRetryLimit, StoryVideoRunner
from tests.test_storyvideo_runtime import FakeProvider, fake_media, write_episode  # noqa: F401 - fixture re-export
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router

client = TestClient(app)


# ---------------------------------------------------------------------------- QC 记录


def _artifact(root: Path, name: str, payload: bytes) -> Path:
    path = root / name
    path.write_bytes(payload)
    return path


def test_qc_decision_keeps_defect_disposition_and_history(tmp_path):
    qc_path = tmp_path / "qc.json"
    frame = _artifact(tmp_path, "S1.png", b"take-one")
    record_technical_results(qc_path, "frames", {"S1": (frame, {})})

    review_qc(qc_path, "frames", "retake", reason="脸漂了", defect="identity_drift", disposition="resubmit")
    item = load_qc(qc_path)["gates"]["frames"]["items"]["S1"]
    assert item["defect"] == "identity_drift" and item["disposition"] == "resubmit"
    assert [h["decision"] for h in item["history"]] == ["retake"]

    # 换了新产物：裁决与处置作废，历史留着（它记录的是这一镜以前为什么被退回）
    frame.write_bytes(b"take-two")
    record_technical_results(qc_path, "frames", {"S1": (frame, {})})
    item = load_qc(qc_path)["gates"]["frames"]["items"]["S1"]
    assert item["decision"] is None and item["defect"] is None and item["disposition"] is None
    assert len(item["history"]) == 1

    review_qc(qc_path, "frames", "retake", reason="又漂了", defect="identity_drift", disposition="edit")
    meta = item_meta(load_qc(qc_path), "frames", "S1")
    assert meta["defect"] == "identity_drift" and meta["disposition"] == "edit"
    assert meta["same_defect_retakes"] == 2          # 同一缺陷第二次出现——该改结构了
    assert [h["disposition"] for h in meta["history"]] == ["resubmit", "edit"]
    assert meta["last_retake"]["disposition"] == "edit"

    # 再换一次产物：裁决清空，但 last_retake 仍从历史里拿得到（弹窗要显示「上次退回」）
    frame.write_bytes(b"take-three")
    record_technical_results(qc_path, "frames", {"S1": (frame, {})})
    fresh = item_meta(load_qc(qc_path), "frames", "S1")
    assert fresh["decision"] is None and fresh["last_retake"]["defect"] == "identity_drift"

    with pytest.raises(QCError, match="unsupported retake defect"):
        review_qc(qc_path, "frames", "retake", reason="x", defect="blurry-face")
    with pytest.raises(QCError, match="unsupported retake disposition"):
        review_qc(qc_path, "frames", "retake", reason="x", disposition="again")


def test_old_qc_records_without_meta_still_load_and_review(tmp_path):
    qc_path = tmp_path / "qc.json"
    frame = _artifact(tmp_path, "S1.png", b"legacy")
    record_technical_results(qc_path, "frames", {"S1": (frame, {})})
    review_qc(qc_path, "frames", "pass")
    # 模拟老版本写的记录：没有 defect / disposition / history
    raw = json.loads(qc_path.read_text(encoding="utf-8"))
    for key in ("defect", "disposition", "history"):
        raw["gates"]["frames"]["items"]["S1"].pop(key, None)
    qc_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    meta = item_meta(load_qc(qc_path), "frames", "S1")
    assert meta["decision"] == "pass" and meta["defect"] is None and meta["disposition"] is None
    assert meta["history"] == [] and meta["same_defect_retakes"] == 0 and meta["last_retake"] is None
    assert item_meta(load_qc(qc_path), "frames", "NOPE") is None

    review_qc(qc_path, "frames", "retake", reason="old record, new verdict")
    item = load_qc(qc_path)["gates"]["frames"]["items"]["S1"]
    assert item["decision"] == "retake" and item["defect"] is None and len(item["history"]) == 1


# ---------------------------------------------------------------------------- 账本计数


def test_attempts_count_same_request_across_retakes(tmp_path, fake_media):
    write_episode(tmp_path)
    provider = FakeProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.prepare("EP99")
        assert runner.shot_attempts("EP99", "S1") == {
            "frames": {"total": 0, "same_request": 0, "request_hash": runner.store.get_job("EP99", "frame-S1")["request_hash"]},
            "videos": {"total": 0, "same_request": 0, "request_hash": runner.store.get_job("EP99", "video-S1")["request_hash"]},
        }
        runner.run("EP99")                                   # 第 1 次提交首帧
        first = runner.shot_attempts("EP99", "S1")["frames"]
        assert first["total"] == 1 and first["same_request"] == 1

        runner.review("EP99", "frames", "retake", shots=["S1"], reason="脸漂了", defect="identity_drift", disposition="resubmit")
        runner.run("EP99")                                   # 第 2 次，同一份提示词
        second = runner.shot_attempts("EP99", "S1")["frames"]
        assert second["total"] == 2 and second["same_request"] == 2 and second["request_hash"] == first["request_hash"]

        # 第 3 次原样重提被拦；改文字重出 / force 不拦
        with pytest.raises(StoryVideoRetryLimit) as info:
            runner.review("EP99", "frames", "retake", shots=["S1"], reason="还是漂", disposition="resubmit")
        assert info.value.shot_id == "S1" and info.value.attempts["same_request"] == RESUBMIT_LIMIT
        assert "已经出过 2 次" in str(info.value) and "改文字重出" in str(info.value)
        with pytest.raises(StoryVideoRetryLimit):
            runner.review("EP99", "frames", "retake", shots=["S1"], reason="不写处置也算原样重提")
        assert runner.store.get_job("EP99", "frame-S1")["status"] == "succeeded"   # 被拦时账本一字不动

        runner.review("EP99", "frames", "retake", shots=["S1"], reason="还是漂", disposition="resubmit", force=True)
        runner.run("EP99")
        assert runner.shot_attempts("EP99", "S1")["frames"]["total"] == 3
        runner.review("EP99", "frames", "retake", shots=["S1"], reason="改动作文字", defect="motion", disposition="edit")
        assert runner.store.get_job("EP99", "frame-S1")["status"] == "prepared"

        # 账本层：指纹对不上（提示词变了）就不算同一份请求
        store = runner.store
        assert store.attempts_for("EP99", "frame-S1")["same_request"] == 3
        changed = store.attempts_for("EP99", "frame-S1", request_hash="some-other-fingerprint")
        assert changed == {"total": 3, "same_request": 0, "request_hash": first["request_hash"]}
        assert store.attempts_for("EP99", "frame-NOPE") == {"total": 0, "same_request": 0, "request_hash": None}
    assert len(provider.submissions) == 3


# ---------------------------------------------------------------------------- review 接口


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    return tmp_path


def _review(payload: dict):
    return client.post("/api/episodes/storyvideo/EP99/review", json={"gate": "frames", "shots": ["S1"], "reviewer": "webui", **payload})


def _shot(shot_id: str = "S1") -> dict:
    detail = client.get("/api/episodes/storyvideo/EP99").json()
    return next(s for s in detail["shots"] if s["id"] == shot_id)


def test_review_endpoint_enforces_retry_discipline_and_dispositions(project, fake_media):
    # EP99.json 在 write_episode 里 mkdir(parents=True) 会撞上 fixture 建好的目录：先删再让它建
    (project / "content" / "story_video").rmdir()
    write_episode(project)
    provider = FakeProvider()
    with StoryVideoRunner(project, provider=provider) as runner:
        runner.run("EP99")
        runner.review("EP99", "frames", "retake", shots=["S1"], reason="第一次退回", defect="seam", disposition="resubmit")
        runner.run("EP99")

    # 详情：提交次数 + 上一次裁决的上下文（新产物还没审，decision 为空，但「上次为什么退回」还在）
    shot = _shot()
    assert shot["attempts"]["frames"] == {"total": 2, "same_request": 2, "request_hash": shot["attempts"]["frames"]["request_hash"]}
    assert shot["attempts"]["videos"]["total"] == 0
    meta = shot["qc_meta"]["frames"]
    assert meta["decision"] is None and meta["defect"] is None
    assert meta["last_retake"]["defect"] == "seam" and meta["last_retake"]["disposition"] == "resubmit"
    assert meta["same_defect_retakes"] == 1 and shot["qc_meta"]["videos"] is None

    # 第三次原样重提 → 409，人话 + 结构化字段；不写处置同样算原样重提
    r = _review({"decision": "retake", "reason": "还是有缝", "defect": "seam", "disposition": "resubmit"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["kind"] == "retry_limit" and detail["shot_id"] == "S1" and detail["gate"] == "frames"
    assert detail["attempts"]["same_request"] == 2 and "已经出过 2 次" in detail["message"]
    assert _review({"decision": "retake", "reason": "没写处置"}).status_code == 409
    # 英文界面拿到翻译过的人话
    r_en = client.post("/api/episodes/storyvideo/EP99/review", headers={"X-StoryStudio-Lang": "en"},
                       json={"gate": "frames", "shots": ["S1"], "decision": "retake", "reason": "x"})
    assert r_en.status_code == 409 and "already been generated 2 times" in r_en.json()["detail"]["message"]

    # 换模型对比不落账
    r = _review({"decision": "retake", "reason": "试试别的模型", "disposition": "model"})
    assert r.status_code == 400 and "对比" in r.json()["detail"]

    # 改文字重出：不拦，记重拍 + 元数据，任务回到待提交
    r = _review({"decision": "retake", "reason": "改动作文字", "defect": "motion", "disposition": "edit"})
    assert r.status_code == 200, r.text
    shot = _shot()
    meta = shot["qc_meta"]["frames"]
    assert meta["decision"] == "retake" and meta["defect"] == "motion" and meta["disposition"] == "edit"
    assert meta["attempts"]["same_request"] == 2 and meta["reason"] == "改动作文字"
    assert [h["defect"] for h in meta["history"]] == ["seam", "motion"]
    assert meta["last_retake"]["defect"] == "motion" and meta["same_defect_retakes"] == 1
    assert shot["frame_qc_decision"] == "retake"

    with StoryVideoRunner(project, provider=provider) as runner:
        runner.run("EP99")                                   # 第 3 次
    assert _shot()["attempts"]["frames"]["same_request"] == 3
    assert _review({"decision": "retake", "reason": "再来", "disposition": "resubmit"}).status_code == 409
    r = _review({"decision": "retake", "reason": "再来", "disposition": "resubmit", "force": True})
    assert r.status_code == 200, r.text
    with StoryVideoRunner(project, provider=provider) as runner:
        runner.run("EP99")                                   # 第 4 次
    assert len(provider.submissions) == 4

    # 保留 → pass、后期处理 → waive，都带缺陷记录
    r = _review({"decision": "retake", "reason": "", "defect": "quality", "disposition": "keep"})
    assert r.status_code == 200, r.text
    shot = _shot()
    assert shot["frame_qc_decision"] == "pass" and shot["qc_meta"]["frames"]["disposition"] == "keep"
    assert shot["qc_meta"]["frames"]["defect"] == "quality"
    r = _review({"decision": "retake", "reason": "边缘帧有噪点，后期修", "defect": "quality", "disposition": "post"})
    assert r.status_code == 200, r.text
    shot = _shot()
    assert shot["frame_qc_decision"] == "waive" and shot["qc_meta"]["frames"]["disposition"] == "post"
    assert shot["qc_meta"]["frames"]["attempts"]["total"] == 4
    # 无效枚举 → 400（不是 500）
    assert _review({"decision": "retake", "reason": "x", "defect": "fuzzy"}).status_code == 400

    # 失败清单里也带次数（概览页「第 N 次」标签）
    with StoryVideoRunner(project, provider=provider) as runner:
        runner.review("EP99", "frames", "retake", shots=["S1"], reason="拿来做失败", force=True)
        runner.store.mark_submitting("EP99", "frame-S1")
        runner.store.mark_failed("EP99", "frame-S1", "QuotaExhausted: HTTP 402")
    failures = client.get("/api/episodes/storyvideo/EP99/failures").json()["items"]
    assert failures and failures[0]["shot_id"] == "S1" and failures[0]["attempts"] == 5


def test_shot_detail_without_ledger_or_qc_is_empty_not_broken(project):
    (project / "content" / "story_video").rmdir()
    write_episode(project)
    shot = _shot()
    assert shot["attempts"] == {
        "frames": {"total": 0, "same_request": 0, "request_hash": None},
        "videos": {"total": 0, "same_request": 0, "request_hash": None},
    }
    assert shot["qc_meta"] == {"frames": None, "videos": None}
