"""后台轮询与断点续等（DESIGN §34）。

现象：点「生成视频」→ run(wait=False) 把几条视频提交给异步服务商就返回，之后服务端没有任何
东西再去问服务商；前端的轮询在 pending 为 0 时就收工（pending 不含在飞的），几小时后服务商早做完了，
界面还说「正在生成中」。这里测：runner.poll 只收不发；generate-watch 后台任务转到没有在飞的为止；
generate-resume 端点的 TaskBusy / 空闲两条路；启动扫账本；一轮里的下载错误记下来、继续转。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit.providers import TaskSnapshot, TaskState, TaskType
from storyvideo.runtime import StoryVideoRunner
from storyvideo.spec import load_spec
from storyvideo.qc import load_qc
from webui import deps, generate, i18n, tasks
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import project as project_router

from tests.test_storyvideo_runtime import FakeProvider, fake_media  # noqa: F401 - fixture re-export

client = TestClient(app)
EP = "WATCH-T"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(generate, "SLEEP_SECONDS", 0)
    # 每个测试从没有正在跑的任务开始（模块级 _running 是跨测试的）
    tasks._running.clear()
    return tmp_path


def write_spec(root: Path, shots: int = 1) -> Path:
    spec = {
        "id": EP, "output_root": f"output/story_video/{EP}", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"a": {"name": "阿一", "block": "黑发。"}},
        "shots": [
            {"id": f"S{i}", "dur": 4, "cast": ["a"], "shot": "近景", "art": "a", "motion": "m", "lines": []}
            for i in range(1, shots + 1)
        ],
        "video_params": {"model": "video-model", "resolution": "1080p", "generate_audio": True},
        "image_params": {"model": "image-model"},
    }
    path = root / "content" / "story_video" / f"{EP}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def submit_by_hand(root: Path, *steps: tuple[str, str]) -> None:
    """把账本推到「已提交、等服务商」：(step_id, task_id)。不经过 provider，模拟「提交之后服务端再没人去问服务商」的状态。"""

    with StoryVideoRunner(root) as runner:
        runner.prepare(EP)
        for step_id, task_id in steps:
            runner.store.mark_submitting(EP, step_id)
            runner.store.mark_submitted(EP, step_id, task_id, "pending")


class ScriptedProvider(FakeProvider):
    """按剧本回答 get_task：{task_id: [状态, 状态, ...]}，最后一个状态一直重复；'finished' 带结果地址。"""

    def __init__(self, script: dict[str, list[str]], *, download_failures: int = 0):
        super().__init__(pending=True)
        self.script = {k: list(v) for k, v in script.items()}
        self.download_failures = download_failures
        self.download_calls = 0

    def get_task(self, task_id, task_type):
        normalized = TaskType(task_type)
        self.polls.append((task_id, normalized))
        states = self.script.get(task_id) or ["processing"]
        state = states.pop(0) if len(states) > 1 else states[0]
        if state == "finished":
            return TaskSnapshot(task_id, normalized, TaskState.FINISHED, (f"https://example.test/{task_id}",))
        if state == "failed":
            return TaskSnapshot(task_id, normalized, TaskState.FAILED, error_message="provider failed")
        return TaskSnapshot(task_id, normalized, TaskState.PROCESSING)

    def download(self, url, destination, **kwargs):
        self.download_calls += 1
        if self.download_calls <= self.download_failures:
            raise RuntimeError("disk full")
        return super().download(url, destination, **kwargs)


def wait_task(task_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = tasks.get(task_id) or {}
        if item.get("status") in ("succeeded", "failed"):
            return item
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not finish: {tasks.get(task_id)}")


# ---- (1) runner.poll：只收不发


def test_poll_downloads_finished_leaves_processing_and_never_submits(project, fake_media):
    write_spec(project, shots=2)
    submit_by_hand(project, ("frame-S1", "img-1"), ("frame-S2", "img-2"))
    provider = ScriptedProvider({"img-1": ["finished"], "img-2": ["processing"]})
    with StoryVideoRunner(project, provider=provider) as runner:
        result = runner.poll(EP)
        s1 = runner.store.get_job(EP, "frame-S1")
        s2 = runner.store.get_job(EP, "frame-S2")
        v1 = runner.store.get_job(EP, "video-S1")
    assert result["inflight"] == ["frame-S2"]
    assert result["status"] == "frames_partial"
    assert s1["status"] == "succeeded" and (project / "output/story_video" / EP / "01_frames/S1.png").is_file()
    assert s2["status"] == "processing"
    assert v1["status"] == "prepared"          # prepared 的视频任务碰都没碰
    assert provider.submissions == []          # 一次都没提交
    assert provider.downloads == ["https://example.test/img-1"]

    # 第二轮：S2 也回来了 → 没有在飞的，顺手把首帧的技术检查记进 qc.json，审片才不会被「技术检查还没跑完」挡住
    provider.script["img-2"] = ["finished"]
    with StoryVideoRunner(project, provider=provider) as runner:
        result = runner.poll(EP)
    assert result["inflight"] == [] and result["status"] == "awaiting_frames_qc"
    qc = load_qc(project / "output/story_video" / EP / "manifests/qc.json")
    assert set(qc["gates"]["frames"]["items"]) == {"S1", "S2"}
    assert provider.submissions == []


# ---- (2) 后台任务：processing ×2 → finished


def test_watch_loop_polls_until_finished_and_installs_artifact(project, fake_media, monkeypatch):
    write_spec(project)
    submit_by_hand(project, ("video-S1", "vid-1"))
    provider = ScriptedProvider({"vid-1": ["processing", "processing", "finished"]})
    monkeypatch.setattr(generate, "_provider", lambda: provider)

    info = generate.ensure_watcher(EP, project_root=project)
    assert info["started"] is True and info["inflight"] == ["video-S1"]
    item = wait_task(info["task_id"])
    assert item["status"] == "succeeded", item
    assert item["result"]["inflight"] == [] and item["result"]["rounds"] == 3
    assert item["kind"] == generate.WATCH_KIND and item["episode_id"] == EP
    assert (project / "output/story_video" / EP / "02_video/S1.mp4").is_file()
    with StoryVideoRunner(project) as runner:
        assert runner.store.get_job(EP, "video-S1")["status"] == "succeeded"
        assert runner.store.get_run(EP)["status"] == "awaiting_videos_qc"
    assert provider.submissions == []
    assert len(provider.polls) == 3
    # 收工后再问：没有在飞的，不再起任务
    again = generate.ensure_watcher(EP, project_root=project)
    assert again == {"task_id": None, "started": False, "inflight": []}


# ---- (3) generate-resume 端点：正在跑返回同一个 task_id；空闲 inflight: []


class BlockingProvider(FakeProvider):
    """第一次 get_task 卡住直到测试放行——让「已经在跑」这一段可以被观察。"""

    def __init__(self):
        super().__init__(pending=True)
        self.entered = threading.Event()
        self.release = threading.Event()

    def get_task(self, task_id, task_type):
        normalized = TaskType(task_type)
        self.polls.append((task_id, normalized))
        self.entered.set()
        assert self.release.wait(timeout=15), "test never released the provider"
        return TaskSnapshot(task_id, normalized, TaskState.FINISHED, (f"https://example.test/{task_id}",))


def test_generate_resume_returns_running_task_then_idle(project, fake_media, monkeypatch):
    write_spec(project)
    submit_by_hand(project, ("frame-S1", "img-1"))
    provider = BlockingProvider()
    monkeypatch.setattr(generate, "_provider", lambda: provider)

    first = client.post(f"/api/episodes/storyvideo/{EP}/generate-resume")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["started"] is True and body["inflight"] == ["frame-S1"] and body["in_progress"] == ["S1"]
    assert provider.entered.wait(timeout=10)
    try:
        # 概览：在飞的镜 + 后台在等
        summary = client.get(f"/api/episodes/storyvideo/{EP}/summary").json()
        assert summary["in_progress"] == ["S1"] and summary["watching"] is True
        second = client.post(f"/api/episodes/storyvideo/{EP}/generate-resume").json()
        assert second["task_id"] == body["task_id"] and second["started"] is False
        assert second["inflight"] == ["frame-S1"]
    finally:
        provider.release.set()
    item = wait_task(body["task_id"])
    assert item["status"] == "succeeded", item
    idle = client.post(f"/api/episodes/storyvideo/{EP}/generate-resume").json()
    assert idle["inflight"] == [] and idle["in_progress"] == [] and idle["task_id"] is None
    summary = client.get(f"/api/episodes/storyvideo/{EP}/summary").json()
    assert summary["in_progress"] == [] and summary["watching"] is False
    assert provider.submissions == []


def test_generate_endpoint_starts_watcher_after_async_submission(project, fake_media, monkeypatch):
    write_spec(project)
    provider = FakeProvider(pending=True)      # 提交后一直 processing
    monkeypatch.setattr(episodes_router, "build_provider", lambda: provider)
    monkeypatch.setattr(generate, "_provider", lambda: provider)
    monkeypatch.setattr(generate, "MAX_WAIT_SECONDS", 0)   # 后台任务一轮就收工，别在测试里转下去
    est = client.get(f"/api/episodes/storyvideo/{EP}/generate-estimate?through=frames").json()
    assert est["pending_count"] == 1
    r = client.post(f"/api/episodes/storyvideo/{EP}/generate",
                    json={"through": "frames", "plan_token": est["plan_token"], "wait": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inflight"] == ["frame-S1"] and body["watch_task_id"]
    assert len(provider.submissions) == 1
    item = wait_task(body["watch_task_id"])
    assert item["result"]["gave_up"] is True and item["result"]["inflight"] == ["frame-S1"]
    assert len(provider.submissions) == 1     # 后台轮询没有再提交
    # 预估现在把它标成在飞（不是待生成）
    est = client.get(f"/api/episodes/storyvideo/{EP}/generate-estimate?through=frames").json()
    assert est["pending_count"] == 0 and est["in_progress"] == ["S1"]


# ---- (4) 启动扫账本


def test_resume_inflight_watchers_without_ledger_is_noop(project):
    assert generate.resume_inflight_watchers(project) == []
    assert tasks.list_tasks() == []


def test_resume_inflight_watchers_starts_task_for_inflight_run(project, fake_media, monkeypatch):
    write_spec(project)
    submit_by_hand(project, ("video-S1", "vid-1"))
    provider = ScriptedProvider({"vid-1": ["processing"]})
    monkeypatch.setattr(generate, "_provider", lambda: provider)
    monkeypatch.setattr(generate, "MAX_WAIT_SECONDS", 0)
    started = generate.resume_inflight_watchers(project)
    assert [s["episode_id"] for s in started] == [EP]
    item = wait_task(started[0]["task_id"])
    assert item["status"] == "succeeded" and item["result"]["gave_up"] is True
    assert item["result"]["inflight"] == ["video-S1"]
    message = item["result"]["message"]
    assert "video-S1" in message and "还没返回" in message
    i18n._compiled = None
    english = i18n.tr(message, "en")
    assert "video-S1" in english and not any("一" <= ch <= "鿿" for ch in english), english
    assert provider.submissions == []


def test_resume_inflight_watchers_upgrades_old_ledger_before_scanning(project):
    """schema 1 的老账本只读打开不迁移，而详情页 / 失败任务 / 在飞任务这些只读路径出错一律当「没有记录」——已锁定的
    项目在用户点审片或生成之前没有任何一处会可写打开。服务启动先可写打开一次把账本升到当前版本，之后 failed_jobs
    能列出老账本里那条失败任务；这一下只升表结构，不起轮询、不提交。"""

    from genkit.store import SCHEMA_VERSION, RunStore
    from storyvideo.runtime import ledger_path
    from tests.test_small_fixes import _old_ledger

    write_spec(project)
    run_dir = project / "output" / "story_video" / EP
    run_dir.mkdir(parents=True)
    _old_ledger(ledger_path(project), run_dir, user_version=1, frame_status="failed")
    spec = load_spec(EP, project_root=project)
    # 升级前：只读打开报版本不一致，被吞成「没有记录」——失败任务列不出来、提交次数全是 0
    assert generate.failed_jobs(spec) == [] and generate.job_records(spec) == {} and generate.inflight_step_ids(spec) == []
    assert generate.resume_inflight_watchers(project) == [] and tasks.list_tasks() == []   # failed 不是在飞的，不起轮询
    assert RunStore.schema_version_of(ledger_path(project)) == SCHEMA_VERSION == 2
    failed = generate.failed_jobs(spec)
    assert [(f["stage"], f["shot_id"], f["attempts"], f["retryable"]) for f in failed] == [("frames", "S1", 1, True)]
    assert "厂商返回 500" in failed[0]["error"]
    records = generate.job_records(spec)
    assert records["frame-S1"]["status"] == "failed" and records["frame-S1"]["provider"] is None
    assert generate.shot_attempts(spec, "S1", jobs=records)["frames"]["total"] == 1


# ---- (5) 一轮里的下载错误：记下来、继续；同一条错误连着 3 次才停


def test_download_error_is_recorded_and_loop_continues(project, fake_media, monkeypatch):
    write_spec(project)
    submit_by_hand(project, ("video-S1", "vid-1"))
    provider = ScriptedProvider({"vid-1": ["finished"]}, download_failures=2)
    monkeypatch.setattr(generate, "_provider", lambda: provider)
    info = generate.ensure_watcher(EP, project_root=project)
    item = wait_task(info["task_id"])
    assert item["status"] == "succeeded", item
    assert provider.download_calls == 3
    assert len(item["errors"]) == 2
    assert "video-S1 的结果下载或校验失败" in item["error"] and "disk full" in item["error"]
    assert item["result"]["inflight"] == [] and item["result"]["rounds"] == 3
    assert (project / "output/story_video" / EP / "02_video/S1.mp4").is_file()


def test_same_error_three_times_stops_the_watcher(project, fake_media, monkeypatch):
    write_spec(project)
    submit_by_hand(project, ("video-S1", "vid-1"))
    provider = ScriptedProvider({"vid-1": ["finished"]}, download_failures=99)
    monkeypatch.setattr(generate, "_provider", lambda: provider)
    info = generate.ensure_watcher(EP, project_root=project)
    item = wait_task(info["task_id"])
    assert item["status"] == "failed", item
    assert provider.download_calls == generate.REPEAT_ERROR_LIMIT
    assert item["error_repeats"] == generate.REPEAT_ERROR_LIMIT
    assert "disk full" in item["error"]
    with StoryVideoRunner(project) as runner:
        assert runner.store.get_job(EP, "video-S1")["status"] == "finished"   # 账本没被动过，下次还能接着下
    # 任务停了 → 概览 watching: false，界面会补发 generate-resume
    spec = load_spec(EP, project_root=project)
    assert generate.inflight_shot_ids(spec) == ["S1"] and generate.is_watching(EP) is False


def test_poll_error_on_one_job_does_not_skip_the_others(project, fake_media):
    write_spec(project, shots=2)
    submit_by_hand(project, ("video-S1", "vid-1"), ("video-S2", "vid-2"))
    provider = ScriptedProvider({"vid-1": ["finished"], "vid-2": ["finished"]}, download_failures=1)
    from storyvideo.runtime import StoryVideoPollErrors

    with StoryVideoRunner(project, provider=provider) as runner:
        with pytest.raises(StoryVideoPollErrors) as info:
            runner.poll(EP)
        assert [str(e) for e in info.value.errors] == [str(info.value)]
        assert "video-S1" in str(info.value)
        assert runner.store.get_job(EP, "video-S2")["status"] == "succeeded"   # S1 失败没拦住 S2
        assert runner.store.get_job(EP, "video-S1")["status"] == "finished"
