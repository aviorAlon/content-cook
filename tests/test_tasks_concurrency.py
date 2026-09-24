"""后台任务的互斥（webui/tasks.py）：同一项目同一种任务**同时**提交只放行一个——直调 ``tasks.start`` 与走 HTTP 的播客合成各验一层。

2026-09-22 审计实撞：pending 记录在锁外落盘、忙判定又只看磁盘，几个请求同时点「合成」全都 200，三个任务一起写同一批 mp3。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webui import deps, tasks
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import podcast as podcast_router

client = TestClient(app)
MP3 = b"ID3" + b"\x00" * 64
API = "/api/podcast"


def wait(task_id: str, seconds: float = 10.0) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        task = tasks.get(task_id)
        if task and task["status"] in ("succeeded", "failed"):
            return task
        time.sleep(0.02)
    raise AssertionError("task did not finish")


@pytest.fixture
def project(tmp_path, monkeypatch):
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", tmp_path / "content" / "story_video")
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", tmp_path / "content" / "story_video")
    monkeypatch.setattr(tasks, "tasks_dir", lambda: tmp_path / ".webui_tasks")
    tasks._running.clear()
    return tmp_path


def _fire_together(n: int, fn) -> list:
    """n 个线程在 Barrier 上对齐后同时调 fn；返回每个线程的返回值或异常。"""

    barrier = threading.Barrier(n)
    results: list = [None] * n

    def worker(i: int) -> None:
        barrier.wait()
        try:
            results[i] = fn()
        except Exception as exc:  # noqa: BLE001 - 异常也是结果
            results[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_start_lets_only_one_task_through_when_submitted_at_once(project):
    gate = threading.Event()

    def body() -> dict:
        gate.wait(5)
        return {"ok": 1}

    try:
        results = _fire_together(6, lambda: tasks.start("pc-audio", "PC-RACE", body, module="podcast"))
        started = [r for r in results if isinstance(r, str)]
        busy = [r for r in results if isinstance(r, tasks.TaskBusy)]
        assert len(started) == 1 and len(busy) == 5, results
        assert all(started[0] in str(b) for b in busy)
        assert tasks.active_task_id("pc-audio", "PC-RACE") == started[0]
        # 别的项目 / 别的种类不受影响
        assert isinstance(tasks.start("pc-audio", "PC-OTHER", lambda: {"ok": 2}, module="podcast"), str)
        assert isinstance(tasks.start("pc-draft", "PC-RACE", lambda: {"ok": 3}, module="podcast"), str)
    finally:
        gate.set()
    assert wait(started[0])["status"] == "succeeded"
    # 跑完就能再提
    assert tasks.active_task_id("pc-audio", "PC-RACE") is None
    again = tasks.start("pc-audio", "PC-RACE", lambda: {"ok": 4}, module="podcast")
    assert wait(again)["status"] == "succeeded"


def test_failed_task_frees_the_slot_and_disk_record_still_counts(project):
    def boom() -> dict:
        raise RuntimeError("x")

    first = tasks.start("pc-audio", "PC-F", boom, module="podcast")
    assert wait(first)["status"] == "failed"
    assert tasks.active_task_id("pc-audio", "PC-F") is None
    # 测试 / 进程重启前的记录：内存里没有镜像时按磁盘记录判忙（老办法照样认）
    tasks._running[("PC-F", "pc-audio")] = "fake-task"
    tasks._write("fake-task", task_id="fake-task", kind="pc-audio", episode_id="PC-F", module="podcast", status="running",
                 created_at=time.time())
    with pytest.raises(tasks.TaskBusy):
        tasks.start("pc-audio", "PC-F", lambda: {"ok": 1}, module="podcast")
    tasks._write("fake-task", status="succeeded")
    assert isinstance(tasks.start("pc-audio", "PC-F", lambda: {"ok": 1}, module="podcast"), str)


def test_memory_status_settles_even_if_the_terminal_record_is_never_written(project, monkeypatch):
    """内存镜像的终态在自己的 try/finally 里改，不靠 run() 里写失败 / 成功记录的那几行：那几行被别的改动整块替换、
    或者落盘失败，这个项目这种任务也不会在内存里永远是 running、一直 409 到重启。"""

    real_write = tasks._write

    def lossy_write(tid, **fields):
        if fields.get("status") in ("failed", "succeeded"):
            return                                   # 模拟终态记录没落盘（或写记录的那几行被改掉了）
        real_write(tid, **fields)

    monkeypatch.setattr(tasks, "_write", lossy_write)
    done = threading.Event()

    def boom() -> dict:
        try:
            raise RuntimeError("x")
        finally:
            done.set()

    first = tasks.start("pc-audio", "PC-L", boom, module="podcast")
    assert done.wait(5)
    deadline = time.time() + 5
    while tasks._status.get(first) != "failed" and time.time() < deadline:
        time.sleep(0.01)
    assert tasks._status[first] == "failed"
    assert tasks.get(first)["status"] == "running"      # 磁盘上还停在 running
    assert tasks.active_task_id("pc-audio", "PC-L") is None
    second = tasks.start("pc-audio", "PC-L", lambda: {"ok": 1}, module="podcast")
    deadline = time.time() + 5
    while tasks._status.get(second) != "succeeded" and time.time() < deadline:
        time.sleep(0.01)
    assert tasks._status[second] == "succeeded" and tasks.active_task_id("pc-audio", "PC-L") is None


def test_concurrent_synthesize_requests_give_one_200_and_the_rest_409(project, monkeypatch):
    gate = threading.Event()

    def slow_synth(profile, text, destination: Path):
        gate.wait(5)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(MP3 + text.encode("utf-8"))

    monkeypatch.setattr(podcast_router, "SYNTHESIZER", slow_synth)
    monkeypatch.setattr(podcast_router, "PROBER", lambda p: 1.0)
    monkeypatch.setattr(podcast_router, "RUNNER", lambda command: Path(command[-1]).write_bytes(MP3))
    monkeypatch.setattr(podcast_router, "FFMPEG", "ffmpeg")
    monkeypatch.setattr(podcast_router, "VOICE_LOOKUP", lambda vid: None)
    pid = client.post(API, json={
        "title": "并发", "format": "duo", "hosts": [{"id": "a", "name": "阿一"}, {"id": "b", "name": "阿二"}],
        "segments": [{"id": "G01", "kind": "cold_open", "lines": [{"who": "a", "text": "一。"}, {"who": "b", "text": "二。"}]}],
    }).json()["id"]
    try:
        responses = _fire_together(4, lambda: client.post(f"{API}/{pid}/synthesize", json={"force": True}))
        codes = sorted(r.status_code for r in responses)
        assert codes == [200, 409, 409, 409], codes
        task_id = next(r.json()["task_id"] for r in responses if r.status_code == 200)
        assert all("已经在跑" in r.json()["detail"] for r in responses if r.status_code == 409)
        assert client.get(f"{API}/{pid}").json()["busy"]["audio"] == task_id
        running = [t for t in tasks.list_tasks(pid, "podcast") if t["status"] in ("pending", "running")]
        assert [t["task_id"] for t in running] == [task_id]
    finally:
        gate.set()
    assert wait(task_id)["status"] == "succeeded"
    assert client.get(f"{API}/{pid}").json()["busy"]["audio"] is None
