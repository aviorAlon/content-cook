"""首帧对比试跑：候选出图（注入假 provider）、列表、采用回账本（重拍 + 认领）。"""

from __future__ import annotations

import io
import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit.providers import TaskSnapshot, TaskState, TaskSubmission, TaskType, path_to_file_url
from storyvideo.runtime import StoryVideoRunner
from webui import compare as C
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router

client = TestClient(app)
FFPROBE = shutil.which("ffprobe")


def png_bytes(color=(200, 30, 30)) -> bytes:
    from PIL import Image
    import random

    img = Image.new("RGB", (320, 568), color)
    px = img.load()
    for x in range(0, 320, 5):
        for y in range(0, 568, 5):
            px[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()


class FakeProvider:
    def __init__(self, cache: Path, fail_model: str | None = None):
        self.cache = cache; self.fail_model = fail_model; self.requests = []

    def submit_image(self, request):
        self.requests.append(request)
        if request.model == self.fail_model:
            return TaskSubmission(task_id=None, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message="余额不足")
        path = self.cache / f"{request.model.replace('/', '_')}.png"
        path.write_bytes(png_bytes())
        return TaskSubmission(task_id=f"file:{path}", task_type=TaskType.IMAGE, status=TaskState.FINISHED)

    def get_task(self, task_id, task_type):
        path = Path(task_id[len("file:"):])
        return TaskSnapshot(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.FINISHED, output_urls=(path_to_file_url(path),))

    def download(self, url, destination, **kw):
        src = Path(url.replace("file:///", "").replace("file://", ""))
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, destination)
        return Path(destination)


class AsyncFakeProvider:
    """异步来源（提交后只拿到远端任务 id 的来源）：提交只拿到远端任务 id，每个模型各自轮询 ``rounds`` 轮才出结果；
    ``fail_model`` 那一个远端返回失败（用来验证一个模型失败不拖累其他模型）。"""

    def __init__(self, cache: Path, rounds: int = 2, fail_model: str | None = None):
        self.cache = cache; self.rounds = rounds; self.fail_model = fail_model
        self.requests = []; self.polls: dict[str, int] = {}

    def submit_image(self, request):
        self.requests.append(request)
        return TaskSubmission(task_id=f"exasync-img:{request.model}", task_type=TaskType.IMAGE, status=TaskState.PROCESSING)

    def get_task(self, task_id, task_type):
        model = task_id.split(":", 1)[1]
        self.polls[model] = self.polls.get(model, 0) + 1
        if self.polls[model] <= self.rounds:
            return TaskSnapshot(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.PROCESSING)
        if model == self.fail_model:
            return TaskSnapshot(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message="内容被拦截")
        path = self.cache / f"{model.replace('/', '_')}.png"
        path.write_bytes(png_bytes())
        return TaskSnapshot(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.FINISHED,
                            output_urls=(path_to_file_url(path),))

    download = FakeProvider.download


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    spec = {
        "id": "CMP-T", "output_root": "output/story_video/CMP-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "block": "黑发。"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["lin"], "shot": "中景", "art": "a", "motion": "m", "lines": []},
            {"id": "S2", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m", "ref_frame": "S1", "lines": []},
        ],
    }
    (content_dir / "CMP-T.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def wait_task(task_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = client.get(f"/api/tasks/{task_id}").json()
        if item["status"] in ("succeeded", "failed"):
            return item
        time.sleep(0.05)
    raise AssertionError("task did not finish")


def test_candidates_use_same_prompt_and_refs(project, monkeypatch, tmp_path):
    fake = FakeProvider(tmp_path / "cache", fail_model="bad/model")
    (tmp_path / "cache").mkdir()
    monkeypatch.setattr(C, "build_provider", lambda: fake)
    # S2 参考 S1：先放一张 S1 首帧，候选请求里应带它作参考
    frames = project / "output" / "story_video" / "CMP-T" / "01_frames"; frames.mkdir(parents=True)
    (frames / "S1.png").write_bytes(png_bytes((10, 10, 200)))
    result = C.run_candidates("CMP-T", "S2", ["openai/gpt-image-2", "bytedance-seed/seedream-4.5", "bad/model"])
    rows = result["candidates"]
    assert [r["model"] for r in rows] == ["openai/gpt-image-2", "bytedance-seed/seedream-4.5", "bad/model"]
    assert rows[0]["file"] and rows[1]["file"] and rows[2]["error"] == "余额不足"
    req = fake.requests[0]
    assert "S1.png" in req.reference_asset_ids[0] and req.aspect_ratio == "9:16"
    assert fake.requests[0].quality == "high" and fake.requests[1].quality is None
    listed = client.get("/api/episodes/storyvideo/CMP-T/compare/frames/S2").json()["candidates"]
    assert len(listed) == 3 and listed[0]["exists"] and listed[2]["exists"] is False
    resp = client.get(f"/api/episodes/storyvideo/CMP-T/compare/frames/S2/file/{listed[0]['file']}")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"


def test_compare_endpoint_runs_as_task(project, monkeypatch, tmp_path):
    fake = FakeProvider(tmp_path / "cache"); (tmp_path / "cache").mkdir()
    monkeypatch.setattr(C, "build_provider", lambda: fake)
    assert client.post("/api/episodes/storyvideo/CMP-T/compare/frames", json={"shot_id": "S1", "models": []}).status_code == 400
    assert client.post("/api/episodes/storyvideo/CMP-T/compare/frames", json={"shot_id": "NOPE", "models": ["m"]}).status_code == 404
    resp = client.post("/api/episodes/storyvideo/CMP-T/compare/frames", json={"shot_id": "S1", "models": ["m1", "m2"]})
    assert resp.status_code == 200 and resp.json()["requests"] == 2
    task = wait_task(resp.json()["task_id"])
    assert task["status"] == "succeeded" and len(task["result"]["candidates"]) == 2
    assert task["progress"] == "2/2" and task["current"] == "m2"      # 每个模型写一行进度


# ---------------------------------------------------------------------------- 异步来源（2026-09-10）


def test_async_candidates_are_polled_independently_and_one_failure_is_isolated(project, tmp_path):
    """异步来源：每个模型各自轮询到完成；其中一个远端失败只废掉它自己那一行，别的候选照常能看能用。"""

    from storyvideo.spec import load_spec

    fake = AsyncFakeProvider(tmp_path / "cache", rounds=1, fail_model="bad/model"); (tmp_path / "cache").mkdir()
    assert C.MAX_WAIT_SECONDS == 20 * 60 and C.POLL_SECONDS == 5.0   # 跟绘本 / 图片集 / 海报同一口径
    slept: list[float] = []
    result = C.run_candidates("CMP-T", "S1", ["m1", "bad/model", "m2"], provider=fake, poll=0.01, sleep=slept.append)
    rows = result["candidates"]
    assert [r["model"] for r in rows] == ["m1", "bad/model", "m2"] and slept
    assert rows[0]["file"] and rows[2]["file"] and rows[1]["file"] is None
    assert rows[1]["error"] == "内容被拦截" and rows[0]["error"] is None
    assert [r["task_id"] for r in rows] == ["exasync-img:m1", "exasync-img:bad/model", "exasync-img:m2"]
    assert fake.polls == {"m1": 2, "bad/model": 2, "m2": 2}
    listed = C.list_candidates(load_spec("CMP-T", project_root=project), "S1")
    assert [c["exists"] for c in listed] == [True, False, True]
    out = project / "output" / "story_video" / "CMP-T" / "09_compare" / "S1"
    assert len(list(out.glob("*.png"))) == 2                          # 失败的那一个不留半张图
    manifest = json.loads((project / "output" / "story_video" / "CMP-T" / "manifests" / "compare.json").read_text(encoding="utf-8"))
    assert manifest["S1"][1]["error"] == "内容被拦截" and len(manifest["S1"]) == 3


def test_async_timeout_only_costs_that_candidate(project, monkeypatch, tmp_path):
    """等到上限（模块常量，测试调小）：那一行记人话原因，不留半张图，后面的模型照常跑。"""

    fake = AsyncFakeProvider(tmp_path / "cache", rounds=10_000); (tmp_path / "cache").mkdir()
    monkeypatch.setattr(C, "MAX_WAIT_SECONDS", 0)
    monkeypatch.setattr(C, "POLL_SECONDS", 0)
    # 只有这一个候选、它又超时了 = 一张都没出：任务体抛错让任务记 failed（原因照样落在清单那一行里）
    with pytest.raises(C.CompareError, match="分钟还没出图") as info:
        C.run_candidates("CMP-T", "S1", ["slow"], provider=fake)
    assert "exasync-img:slow" in str(info.value)
    rows = C.list_candidates(__import__("storyvideo.spec", fromlist=["load_spec"]).load_spec("CMP-T", project_root=project), "S1")
    assert rows[0]["exists"] is False and "分钟还没出图" in rows[0]["error"] and "exasync-img:slow" in rows[0]["error"]
    assert fake.polls == {"slow": 1}
    out = project / "output" / "story_video" / "CMP-T" / "09_compare" / "S1"
    assert not list(out.glob("*.png"))
    # 超时之后同一镜还能继续出别的候选（清单只是多了一行错误）
    ok = AsyncFakeProvider(tmp_path / "cache", rounds=0)
    assert C.run_candidates("CMP-T", "S1", ["fast"], provider=ok)["candidates"][0]["file"]
    assert len(C.list_candidates(__import__("storyvideo.spec", fromlist=["load_spec"]).load_spec("CMP-T", project_root=project), "S1")) == 2


@pytest.mark.skipif(not FFPROBE, reason="adopt goes through import_legacy which validates with ffprobe")
def test_adopt_replaces_frame_through_the_ledger(project, monkeypatch, tmp_path):
    fake = FakeProvider(tmp_path / "cache"); (tmp_path / "cache").mkdir()
    monkeypatch.setattr(C, "build_provider", lambda: fake)
    # 草稿态不能采用
    C.run_candidates("CMP-T", "S1", ["m1"])
    name = C.list_candidates(__import__("storyvideo.spec", fromlist=["load_spec"]).load_spec("CMP-T", project_root=project), "S1")[0]["file"]
    assert client.post("/api/episodes/storyvideo/CMP-T/compare/frames/S1/adopt", json={"name": name}).status_code == 400
    # 开始制作并伪造"两镜首帧都已生成且通过"。S2 去掉参考链：参考镜重拍会连带作废引用它的镜（那是正确行为），
    # 这里要验证的是"没关联的镜裁决保留"
    spec_path = project / "content" / "story_video" / "CMP-T.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8")); spec["shots"][1].pop("ref_frame")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    with StoryVideoRunner(project) as runner:
        runner.prepare("CMP-T")
    out = project / "output" / "story_video" / "CMP-T"
    (out / "01_frames").mkdir(parents=True, exist_ok=True)
    for sid in ("S1", "S2"):
        (out / "01_frames" / f"{sid}.png").write_bytes(png_bytes((0, 120, 0)))
    with StoryVideoRunner(project) as runner:
        runner.import_legacy("CMP-T", confirmed_existing_artifacts=True)
        runner.review("CMP-T", "frames", "pass", shots=["S1", "S2"], reason="", reviewer="test")
    before = (out / "01_frames" / "S1.png").read_bytes()
    resp = client.post("/api/episodes/storyvideo/CMP-T/compare/frames/S1/adopt", json={"name": name})
    assert resp.status_code == 200, resp.text
    after = (out / "01_frames" / "S1.png").read_bytes()
    assert after != before and after == (out / "09_compare" / "S1" / name).read_bytes()
    assert list((out / "09_compare" / "S1").glob("replaced-*.png"))
    detail = client.get("/api/episodes/storyvideo/CMP-T").json()
    by_id = {s["id"]: s for s in detail["shots"]}
    assert by_id["S1"]["frame_url"] and by_id["S1"]["frame_qc_decision"] is None   # 换了图要重审
    assert by_id["S2"]["frame_qc_decision"] == "pass"                               # 没换的裁决保留
    with StoryVideoRunner(project) as runner:
        assert runner.status("CMP-T")["run_id"] == "CMP-T"
    assert client.post("/api/episodes/storyvideo/CMP-T/compare/frames/S1/adopt", json={"name": "nope.png"}).status_code == 400
