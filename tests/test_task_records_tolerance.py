"""任务记录 / 起草 job 记录的容错（发 tag 前的加固）：

* ``.webui_draft_cache/tasks/`` 或 ``jobs/`` 里一个「合法 JSON 但不是对象」（``[1,2,3]`` / ``"hello"`` / ``null``）、
  ``created_at`` 不是数字、或半截 JSON 的文件，只跳过那一条并记一行日志——列表接口不能整个 500，单条接口按 404；
* 建项目（``POST /api/scripts/save``）存盘后顺手标记 job 为「已保存」，标记那一步碰到坏文件不能把已成功的建项目报成 500；
* 后台任务失败时 ``error`` 只放人话，异常类名另存 ``error_type``（traceback 单独落盘）；程序 bug（KeyError 这类
  消息不成句的）类名照旧留在 ``error`` 里，不然弹窗只剩一段看不懂的原文。

程序自己只会写对象（原子写），这些坏文件只可能是外面放进来的；但一旦出现不会自愈（记录从不删除），所以要兜。
"""

from __future__ import annotations

import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from webui import deps, tasks
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import scripts as scripts_router

# 不让 TestClient 把服务端异常直接抛出来——这里就是要看状态码是不是 500
client = TestClient(app, raise_server_exceptions=False)

GOOD_TASK = {"task_id": "good", "kind": "post", "episode_id": "EP", "module": "drama", "status": "failed", "created_at": 2.0}
GOOD_JOB = {"job_id": "good", "episode_id": "EP", "kind": "draft", "status": "succeeded", "created_at": 2.0}

# 带中文失败原因的记录（真实记录里几乎都有中文），用来造「编码不是 UTF-8」的坏文件
_BAD_RECORD_ZH = json.dumps({"task_id": "bad", "job_id": "bad", "episode_id": "EP", "status": "failed",
                             "error": "生图来源没配置", "created_at": 1.0}, ensure_ascii=False)

BAD_SHAPES = [
    pytest.param("[1,2,3]", id="list"),
    pytest.param('"hello"', id="string"),
    pytest.param("null", id="null"),
    pytest.param("42", id="int"),
    pytest.param("{not json", id="half-json"),
    pytest.param(json.dumps({"task_id": "bad", "job_id": "bad", "episode_id": "EP", "status": "failed", "created_at": "yesterday"}), id="str-created-at"),
    pytest.param(json.dumps({"task_id": "bad", "job_id": "bad", "episode_id": "EP", "status": "failed", "created_at": None}), id="null-created-at"),
    # 手改后不是 UTF-8 的记录：中文 Windows 记事本存成 GBK / 「Unicode」（UTF-16 带 BOM）。以前 UnicodeDecodeError
    # 既不是 OSError 也不是 JSONDecodeError，列表、单条、恢复条全部 500
    pytest.param(_BAD_RECORD_ZH.encode("gbk"), id="gbk"),
    pytest.param(_BAD_RECORD_ZH.encode("utf-16"), id="utf16"),
    pytest.param(b"\xff\xfe{\x00}\x00", id="utf16-bare"),
]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    for module in (deps, episodes_router, post_router, scripts_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    story = tmp_path / "content" / "story_video"
    story.mkdir(parents=True)
    for module in (deps, episodes_router, scripts_router):
        monkeypatch.setattr(module, "STORYVIDEO_DIR", story)
    monkeypatch.setattr(scripts_router, "JOBS_DIR", tmp_path / ".webui_draft_cache" / "jobs")
    monkeypatch.setattr(tasks, "_warned", set())
    monkeypatch.setattr(scripts_router, "_warned_jobs", set())
    tasks._running.clear()
    tasks.tasks_dir().mkdir(parents=True)
    scripts_router.JOBS_DIR.mkdir(parents=True)
    return tmp_path


def _write_record(path, content: str | bytes) -> None:
    if isinstance(content, bytes):
        path.write_bytes(content)          # 原样字节：用来造编码不对的文件
    else:
        path.write_text(content, encoding="utf-8")


def _task_file(name: str, content: str | bytes) -> None:
    _write_record(tasks.tasks_dir() / f"{name}.json", content)


def _job_file(name: str, content: str | bytes) -> None:
    _write_record(scripts_router.JOBS_DIR / f"{name}.json", content)


def test_non_utf8_shapes_really_are_not_utf8():
    """防止样本本身退化成合法 UTF-8（那样下面的用例就测不到解码失败这条路了）。"""

    for content in (_BAD_RECORD_ZH.encode("gbk"), _BAD_RECORD_ZH.encode("utf-16"), b"\xff\xfe{\x00}\x00"):
        with pytest.raises(UnicodeDecodeError):
            content.decode("utf-8")


# ---------------------------------------------------------------------------- 任务记录


@pytest.mark.parametrize("text", BAD_SHAPES)
def test_one_bad_task_record_does_not_break_the_list(project, text):
    _task_file("good", json.dumps(GOOD_TASK))
    _task_file("bad", text)
    for url in ("/api/tasks", "/api/tasks?module=drama", "/api/tasks?episode_id=EP", "/api/tasks?module=sticker"):
        r = client.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code} {r.text[:120]}"
        ids = [row["task_id"] for row in r.json()]
        if url.endswith("sticker"):
            assert ids == []
        else:
            assert "good" in ids
            assert all(isinstance(row, dict) for row in r.json())
    # 单条：不是对象 / 读不出来 → 404 而不是响应模型校验 500；created_at 坏掉但仍是对象的记录照常返回
    r = client.get("/api/tasks/bad")
    assert r.status_code in (200, 404), r.text
    if r.status_code == 200:
        assert r.json()["task_id"] == "bad"
    assert client.get("/api/tasks/good").status_code == 200


def test_bad_created_at_sorts_last_instead_of_raising(project):
    _task_file("old", json.dumps({**GOOD_TASK, "task_id": "old", "created_at": 1.0}))
    _task_file("new", json.dumps({**GOOD_TASK, "task_id": "new", "created_at": 3.0}))
    _task_file("weird", json.dumps({**GOOD_TASK, "task_id": "weird", "created_at": "yesterday"}))
    _task_file("none", json.dumps({**GOOD_TASK, "task_id": "none", "created_at": None}))
    ids = [t["task_id"] for t in tasks.list_tasks()]
    assert ids[:2] == ["new", "old"] and set(ids[2:]) == {"weird", "none"}


def test_get_active_and_write_treat_non_dict_records_as_missing(project):
    _task_file("weird", '"str"')
    assert tasks.get("weird") is None
    tasks._running[("EP", "post")] = "weird"
    assert tasks.active_task_id("post", "EP") is None
    # 后台线程往同名的坏文件里写进度：从空字典起，不在 str 上 .update
    tasks.note("weird", progress="1/3")
    assert tasks.get("weird") == {"progress": "1/3", "updated_at": tasks.get("weird")["updated_at"]}


def test_bad_task_record_is_logged_once(project, caplog):
    _task_file("stray", "[1,2,3]")
    with caplog.at_level(logging.WARNING, logger="storystudio.tasks"):
        tasks.list_tasks()
        tasks.list_tasks()
        tasks.get("stray")
    hits = [rec for rec in caplog.records if "stray.json" in rec.getMessage()]
    assert len(hits) == 1 and hits[0].levelno == logging.WARNING and "不是对象" in hits[0].getMessage()


def test_failed_task_error_is_plain_text_with_type_aside(project, caplog):
    class ClipGenerationError(RuntimeError):
        pass

    def boom():
        raise ClipGenerationError("video 模型未配置：video: 没选适配器")

    with caplog.at_level(logging.WARNING, logger="storystudio.tasks"):
        task_id = tasks.start("clip-generate", "CL-1", boom, module="clip")
        for _ in range(200):
            item = tasks.get(task_id) or {}
            if item.get("status") == "failed":
                break
            time.sleep(0.01)
    assert item["status"] == "failed"
    assert item["error"] == "video 模型未配置：video: 没选适配器"          # 弹窗原样展示，不带 ClipGenerationError: 前缀
    assert item["error_type"] == "ClipGenerationError"
    assert "ClipGenerationError" in item["traceback"]                       # 排障信息不丢
    assert any(task_id in rec.getMessage() and "ClipGenerationError: video 模型未配置" in rec.getMessage()
               for rec in caplog.records)                                  # 日志里照旧带类名
    assert client.get(f"/api/tasks/{task_id}").json()["error"] == item["error"]

    def silent():
        raise ValueError()

    task_id = tasks.start("clip-generate", "CL-2", silent, module="clip")
    for _ in range(200):
        item = tasks.get(task_id) or {}
        if item.get("status") == "failed":
            break
        time.sleep(0.01)
    assert item["error"] == "ValueError" and item["error_type"] == "ValueError"   # 空消息的异常退回类名，不留空串


def _failed_item(episode_id: str, exc: BaseException) -> dict:
    def boom():
        raise exc

    task_id = tasks.start("clip-generate", episode_id, boom, module="clip")
    for _ in range(200):
        item = tasks.get(task_id) or {}
        if item.get("status") == "failed":
            return item
        time.sleep(0.01)
    raise AssertionError(f"任务没失败：{item}")


@pytest.mark.parametrize(("exc", "error"), [
    # 程序 bug：消息离了类名看不懂（弹窗里只剩「'shots'」），类名留着，外面再包一句「这是程序没兜住」
    pytest.param(KeyError("shots"), "遇到没预料到的错误：KeyError: 'shots'——请把这条任务记录发给开发者", id="keyerror"),
    pytest.param(KeyError("林夏"), "遇到没预料到的错误：KeyError: '林夏'——请把这条任务记录发给开发者", id="keyerror-cjk-key"),   # 键的 repr，有汉字也不是一句话
    pytest.param(AttributeError("'NoneType' object has no attribute 'get'"),
                 "遇到没预料到的错误：AttributeError: 'NoneType' object has no attribute 'get'——请把这条任务记录发给开发者", id="attributeerror"),
    pytest.param(IndexError("list index out of range"), "遇到没预料到的错误：IndexError: list index out of range——请把这条任务记录发给开发者", id="indexerror"),
    # 内置异常带中文人话（项目里不少地方 raise ValueError("…")）：照样去掉类名
    pytest.param(ValueError("剧本还没有镜头"), "剧本还没有镜头", id="valueerror-zh"),
])
def test_failed_task_keeps_type_name_when_message_is_not_a_sentence(project, exc, error):
    item = _failed_item("CL-3", exc)
    assert item["error"] == error
    assert item["error_type"] == type(exc).__name__


# ---------------------------------------------------------------------------- job 记录


@pytest.mark.parametrize("text", BAD_SHAPES)
def test_one_bad_job_record_does_not_break_the_list(project, text):
    _job_file("good", json.dumps(GOOD_JOB))
    _job_file("bad", text)
    for url in ("/api/scripts/jobs", "/api/scripts/jobs?episode_id=EP"):
        r = client.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code} {r.text[:120]}"
        assert "good" in [row["job_id"] for row in r.json()]
    r = client.get("/api/scripts/jobs/bad")
    assert r.status_code in (200, 404), r.text
    if r.status_code == 404:
        assert r.json()["detail"] == "job 记录损坏：bad"
    assert client.get("/api/scripts/jobs/good").status_code == 200
    assert client.get("/api/scripts/jobs/nope").status_code == 404


def test_bad_job_record_is_logged_once(project, caplog):
    _job_file("stray", "null")
    with caplog.at_level(logging.WARNING, logger="storystudio.scripts"):
        client.get("/api/scripts/jobs")
        client.get("/api/scripts/jobs")
    hits = [rec for rec in caplog.records if "stray.json" in rec.getMessage()]
    assert len(hits) == 1 and "不是对象" in hits[0].getMessage()


def test_bom_records_are_read_not_skipped(project, caplog):
    """老记事本「UTF-8」存盘带 BOM：内容是好的，跟项目文件一样照读，不当坏记录跳过、也不记警告。"""

    bom = b"\xef\xbb\xbf"
    _task_file("bom", bom + json.dumps({**GOOD_TASK, "task_id": "bom", "error": "生图来源没配置"}, ensure_ascii=False).encode("utf-8"))
    _job_file("bom", bom + json.dumps({**GOOD_JOB, "job_id": "bom"}).encode("utf-8"))
    with caplog.at_level(logging.WARNING):
        assert [t["task_id"] for t in client.get("/api/tasks").json()] == ["bom"]
        assert client.get("/api/tasks/bom").json()["error"] == "生图来源没配置"
        assert [j["job_id"] for j in client.get("/api/scripts/jobs").json()] == ["bom"]
        assert client.get("/api/scripts/jobs/bom").json()["job_id"] == "bom"
    assert not [rec for rec in caplog.records if "bom.json" in rec.getMessage()]


def test_unopenable_job_file_is_not_reported_as_a_bad_record(project, monkeypatch, caplog):
    """打不开（Windows 上另一边正在替换文件、没权限）不是记录坏了：跳过，但不记「坏记录，删掉即可」，
    也不占掉「只警告一次」的名额——等它真坏了还得能记下来。"""

    _job_file("good", json.dumps(GOOD_JOB))
    _job_file("busy", json.dumps({**GOOD_JOB, "job_id": "busy"}))
    real_read_text = type(scripts_router.JOBS_DIR).read_text

    def flaky(self, *args, **kwargs):
        if self.name == "busy.json":
            raise PermissionError(13, "另一个程序正在使用此文件")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(type(scripts_router.JOBS_DIR), "read_text", flaky)
    with caplog.at_level(logging.WARNING, logger="storystudio.scripts"):
        assert [j["job_id"] for j in client.get("/api/scripts/jobs").json()] == ["good"]
    assert not [rec for rec in caplog.records if "busy.json" in rec.getMessage()]
    assert str(scripts_router.JOBS_DIR / "busy.json") not in scripts_router._warned_jobs


def _episode_spec() -> dict:
    from genkit.paths import resource

    spec = json.loads(resource("examples", "episodes", "first_episode.json").read_text(encoding="utf-8-sig"))
    spec["id"] = "saved-ep"
    spec["output_root"] = "output/story_video/saved-ep"
    return spec


@pytest.mark.parametrize("text", ["[]", '"x"', "null", _BAD_RECORD_ZH.encode("gbk")], ids=["list", "string", "null", "gbk"])
def test_save_script_succeeds_even_with_a_bad_job_file(project, text):
    """以前：先写盘再标记 job，标记那步在坏文件上炸 → 界面报「保存失败」但项目已建成，再点是 409。"""

    _job_file("good", json.dumps({**GOOD_JOB, "episode_id": "saved-ep"}))
    _job_file("bad", text)
    r = client.post("/api/scripts/save", json={"episode_id": "saved-ep", "spec": _episode_spec()})
    assert r.status_code == 200, r.text
    assert (project / "content" / "story_video" / "saved-ep.json").is_file()
    # 附带动作照常做：这个项目名下已成功的 job 被标成「已保存」，恢复条不再提醒；坏文件原样留着
    assert json.loads((scripts_router.JOBS_DIR / "good.json").read_text(encoding="utf-8"))["saved"] is True
    raw = text if isinstance(text, bytes) else text.encode("utf-8")
    assert (scripts_router.JOBS_DIR / "bad.json").read_bytes() == raw
    assert client.get("/api/scripts/jobs").status_code == 200
    # 同 id 再存才是 409（不是因为上一次「失败」了）
    assert client.post("/api/scripts/save", json={"episode_id": "saved-ep", "spec": _episode_spec()}).status_code == 409


def test_save_script_survives_marking_failure(project, monkeypatch, caplog):
    """标记只是「恢复条别再提醒」的附带动作：它抛什么都不该把已经写好的项目报成失败。"""

    def explode(episode_id: str) -> None:
        raise RuntimeError("磁盘抽风")

    monkeypatch.setattr(scripts_router, "_mark_episode_jobs_saved", explode)
    with caplog.at_level(logging.ERROR, logger="storystudio.scripts"):
        r = client.post("/api/scripts/save", json={"episode_id": "saved-ep", "spec": _episode_spec()})
    assert r.status_code == 200, r.text
    assert (project / "content" / "story_video" / "saved-ep.json").is_file()
    assert any("saved-ep" in rec.getMessage() for rec in caplog.records)
