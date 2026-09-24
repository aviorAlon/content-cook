"""src/webui/routers/scripts.py：AI 起草 job 系统 + 草稿逐镜编辑。这里覆盖的都是
实际出过的 bug 的回归测试，以及新增的免费草稿逐镜编辑端点——之前完全没有测试，这两类问题以前只能靠手动
点击才发现。
"""

import json

import pytest
from fastapi.testclient import TestClient

from webui.app import app
from webui.routers import scripts as scripts_router
from webui.routers.scripts import _build_draft_result, _draft_shot_view, _job_path, _write_job

client = TestClient(app)


def valid_shot(shot_id="S1", dur=5, zh="嗨。"):
    return {
        "id": shot_id, "dur": dur, "cast": ["mei"],
        "lines": [{"who": "mei", "en": "Hi.", "zh": zh}],
        "shot": "wide", "art": "a room", "motion": "mei waves",
    }


def draft_spec(*shots):
    return {
        "id": "T1", "output_root": "output/story_video/T1",
        "cast": {"mei": {"block": "Mei"}},
        "shots": list(shots),
    }


# ---- _write_job：job_id 关键字参数撞名的回归测试 ----
# 真实踩过的 bug：_write_job(job_id, job_id=job_id, ...) 里第一个位置参数
# 也叫 job_id，Python 直接报 "got multiple values for argument 'job_id'"。
# 修法是第一个参数改叫 jid，这里锁死这个签名不能退化回去。


def test_write_job_accepts_job_id_as_a_field_without_kwarg_collision(tmp_path, monkeypatch):
    monkeypatch.setattr(scripts_router, "JOBS_DIR", tmp_path)
    jid = "abc123"
    _write_job(jid, job_id=jid, episode_id="EP-T1", status="pending")
    data = json.loads(_job_path(jid).read_text(encoding="utf-8"))
    assert data["job_id"] == jid
    assert data["episode_id"] == "EP-T1"
    assert data["status"] == "pending"


def test_write_job_merges_not_replaces(tmp_path, monkeypatch):
    monkeypatch.setattr(scripts_router, "JOBS_DIR", tmp_path)
    jid = "abc123"
    _write_job(jid, status="pending", episode_id="EP-T1")
    _write_job(jid, status="running")  # 只更新 status，episode_id 不该丢
    data = json.loads(_job_path(jid).read_text(encoding="utf-8"))
    assert data["status"] == "running"
    assert data["episode_id"] == "EP-T1"


# ---- _build_draft_result：部分校验失败不该丢掉整份草稿的回归测试 ----
# 真实踩过的 bug：起草结果里某一镜的 dur 越界，之前会让 normalize_spec 直接
# 抛异常、整份草稿（已经付费生成的全部镜头）被当场丢弃。现在改成校验失败
# 只留一条警告，所有镜次仍然照常展示。


def test_build_draft_result_keeps_all_shots_when_one_shot_fails_validation():
    spec = draft_spec(valid_shot("S1", dur=5), valid_shot("S2", dur=999))  # S2 时长非法

    result = _build_draft_result(spec, target_seconds=90, cost_usd=0.5, usage_tokens=None, backend="claude_cli")

    assert [s["id"] for s in result["shots"]] == ["S1", "S2"]  # 两镜都还在，不是只剩 S1
    assert any("没通过结构校验" in w for w in result["warnings"])
    assert result["cost_usd"] == 0.5  # 花费信息原样带回去，不因为校验失败而丢


def test_build_draft_result_with_fully_valid_spec_has_no_validation_warning():
    spec = draft_spec(valid_shot("S1", dur=5))
    result = _build_draft_result(spec, target_seconds=90, cost_usd=None, usage_tokens=100, backend="openai_proxy")
    assert not any("没通过结构校验" in w for w in result["warnings"])
    assert result["usage_tokens"] == 100


# ---- _draft_shot_view：单镜编译失败不该让别的字段也拿不到 ----


def test_draft_shot_view_reports_shot_error_without_dropping_other_fields():
    # compile_frame_prompt/compile_video_prompt 是拿 shot_id 去 spec 自己的
    # shots 列表里查数据的，不是用传进来的 shot 参数——非法运镜必须写在
    # spec 那份 shot 里，传进去的 shot 参数才需要跟 spec 里的是同一条。
    bad_shot = valid_shot("S1", dur=5)
    bad_shot["camera_move"] = "not_a_real_camera_move_id"
    spec = draft_spec(bad_shot)
    view = _draft_shot_view(spec, bad_shot)
    assert view["id"] == "S1"
    assert view["lines"][0]["zh"] == "嗨。"  # 台词等基础字段依然给出来了
    assert "shot_error" in view  # 非法运镜导致编译视频提示词失败，反映在这个字段


def test_draft_shot_view_healthy_shot_has_no_shot_error():
    spec = draft_spec(valid_shot("S1", dur=5))
    view = _draft_shot_view(spec, valid_shot("S1", dur=5))
    assert "shot_error" not in view
    assert view["budget"] is not None


# ---- POST /api/scripts/draft/shot：免费草稿逐镜编辑端点 ----
# 不写盘、不调用任何 AI 接口——测试直接打真实 app，不需要造隔离的项目目录。


def test_draft_shot_endpoint_merges_edited_line():
    spec = draft_spec(valid_shot("S1", dur=5, zh="旧台词"))
    r = client.post(
        "/api/scripts/draft/shot",
        json={"spec": spec, "shot_id": "S1", "lines": [{"who": "mei", "zh": "新台词"}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["spec"]["shots"][0]["lines"][0]["zh"] == "新台词"
    assert body["shots"][0]["lines"][0]["zh"] == "新台词"
    assert body["cost_usd"] is None  # 这次调用不花钱，不该带出一个费用数字


def test_draft_shot_endpoint_unknown_shot_id_is_404():
    spec = draft_spec(valid_shot("S1", dur=5))
    r = client.post("/api/scripts/draft/shot", json={"spec": spec, "shot_id": "DOES-NOT-EXIST"})
    assert r.status_code == 404


def test_draft_job_generates_episode_id_and_keeps_title(tmp_path, monkeypatch):
    """起草：不传 episode_id 就自动编号；剧名进 job 参数并传给 generate_draft。"""
    from webui.routers import scripts as R

    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    monkeypatch.setattr(R, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(R, "JOBS_DIR", tmp_path / "jobs")
    seen = {}

    def fake_generate_draft(**kw):
        seen.update(kw)
        raise R.ScriptGenError("没配文本模型")

    monkeypatch.setattr(R, "generate_draft", fake_generate_draft)
    monkeypatch.setattr(R.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    r = client.post("/api/scripts/jobs/draft", json={"title": "加班夜的许愿", "direction": "一个人加班", "target_seconds": 60, "camera_profile": "dark_comedy_vertical"})
    assert r.status_code == 200, r.text
    assert r.json()["episode_id"].startswith("EP-") and r.json()["episode_id"].endswith("-1")
    assert seen["episode_id"] == r.json()["episode_id"] and seen["title"] == "加班夜的许愿"
    job = client.get(f"/api/scripts/jobs/{r.json()['job_id']}").json()
    assert job["params"]["title"] == "加班夜的许愿" and job["status"] == "failed"
    assert client.post("/api/scripts/jobs/draft", json={"episode_id": "bad id", "direction": "x", "target_seconds": 60, "camera_profile": "p"}).status_code == 400
