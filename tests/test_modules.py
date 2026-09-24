"""模块注册表与产品名（DESIGN §38）：``webui/modules.py``、``/api/modules``、列表/概览的 ``module`` 标签、
``/api/tasks?module=``、``/api/health`` 里的产品名。

内容目录不搬：短剧仍在 content/story_video；这里只验证注册表把它登记对了、计数只数项目文件（跳过剧本层）。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from webui import branding, deps, modules, tasks
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import project as project_router

client = TestClient(app)


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    # 各路由模块在 import 时各自绑了 PROJECT_ROOT（见 test_webui_episodes_router 的说明），都要 patch
    for module in (deps, episodes_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    return tmp_path


def _spec(episode_id: str) -> dict:
    return {
        "id": episode_id, "title_zh": "加班夜", "output_root": f"output/story_video/{episode_id}",
        "cast": {"su": {"name": "苏", "voice": "女", "block": "短发"}},
        "shots": [{"id": "S1", "dur": 5, "cast": ["su"], "lines": [{"who": "su", "en": "Hi.", "zh": "嗨。"}],
                   "shot": "wide", "art": "a room", "motion": "su waves"}],
    }


def _write(root, episode_id: str) -> None:
    (root / "content" / "story_video" / f"{episode_id}.json").write_text(json.dumps(_spec(episode_id), ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------- 注册表形状


def test_registry_shape():
    ids = [m.id for m in modules.MODULES]
    assert ids == ["drama", "image", "clip", "picturebook", "sticker", "portrait", "music", "podcast", "script"]
    assert len(set(ids)) == len(ids)
    drama = modules.get_module("drama")
    assert drama is not None and drama.status == "ready" and drama.available
    assert drama.route == "/drama" and drama.content_dir == "content/story_video"
    assert drama.project_kinds == ("storyvideo",)
    assert drama.content_dir == str(deps.STORYVIDEO_DIR.relative_to(deps.PROJECT_ROOT)).replace("\\", "/")
    book = modules.get_module("picturebook")
    assert book is not None and book.status == "ready" and book.available
    assert book.route == "/picturebook" and book.content_dir == "content/picturebook"
    assert book.project_kinds == ("picturebook",)
    # 图片集引擎的三个模块（DESIGN §40 / §42）：同一套接口、各自的内容目录与路由
    for mid in ("sticker", "portrait", "image"):
        m = modules.get_module(mid)
        assert m is not None and m.status == "ready" and m.available
        assert m.route == f"/{mid}" and m.content_dir == f"content/{mid}"
        assert m.project_kinds == (mid,)
    # 配乐（DESIGN §44）：自己的内容目录与路由；产物投放到别的模块，但项目本身是它自己的
    music = modules.get_module("music")
    assert music is not None and music.status == "ready" and music.available
    assert music.route == "/music" and music.content_dir == "content/music"
    assert music.project_kinds == ("music",) and music.icon == "🎵"
    # 单条视频（DESIGN §43）：一个项目就是一条片子，不拆镜、不进账本
    clip = modules.get_module("clip")
    assert clip is not None and clip.status == "ready" and clip.available
    assert clip.route == "/clip" and clip.content_dir == "content/clip"
    assert clip.project_kinds == ("clip",) and clip.icon == "🎞"
    # 剧本工作台（docs/scriptlab-spec.md）：只管文字，产物投放到播客 / 短剧，项目本身是它自己的
    script = modules.get_module("script")
    assert script is not None and script.status == "ready" and script.available
    assert script.route == "/script" and script.content_dir == "content/script"
    assert script.project_kinds == ("script",) and script.icon == "✍️"
    for m in modules.MODULES:
        assert m.name and m.english and m.description and m.description_en
        if m.status == "planned":
            assert not m.available and m.route is None and m.content_dir is None and m.project_kinds == ()
    assert modules.module_for_pipeline("music") == "music"
    assert modules.module_for_pipeline("storyvideo") == "drama"
    assert modules.module_for_pipeline("picturebook") == "picturebook"
    assert modules.module_for_pipeline("sticker") == "sticker"
    assert modules.module_for_pipeline("portrait") == "portrait"
    assert modules.module_for_pipeline("image") == "image"
    assert modules.module_for_pipeline("clip") == "clip"
    assert modules.module_for_pipeline("script") == "script"
    assert modules.module_for_pipeline("oldmodule") is None
    assert modules.get_module("nope") is None


def test_ready_module_needs_route_and_dir():
    with pytest.raises(ValueError):
        modules.Module(id="x", name="x", english="x", description="x", status="ready")
    with pytest.raises(ValueError):
        modules.Module(id="x", name="x", english="x", description="x", status="someday")


# ---------------------------------------------------------------------------- /api/modules


def test_api_modules_counts_drama_projects_and_marks_planned(isolated_project):
    _write(isolated_project, "EP1")
    _write(isolated_project, "EP2")
    # 剧本层文件不是项目，不计数
    (isolated_project / "content" / "story_video" / "EP1.screenplay.json").write_text("{}", encoding="utf-8")
    body = client.get("/api/modules").json()
    assert body["product"] == {"name": branding.PRODUCT_NAME, "name_en": branding.PRODUCT_NAME_EN, "short": branding.PRODUCT_SHORT}
    by_id = {m["id"]: m for m in body["modules"]}
    assert list(by_id) == ["drama", "image", "clip", "picturebook", "sticker", "portrait", "music", "podcast", "script"]
    assert by_id["drama"]["available"] is True and by_id["drama"]["project_count"] == 2
    assert by_id["drama"]["route"] == "/drama" and by_id["drama"]["icon"]
    # 绘本模块：content/picturebook 下每个 json 一个项目（这里还没有 → 0，但模块可用）
    assert by_id["picturebook"]["available"] is True and by_id["picturebook"]["project_count"] == 0
    assert by_id["picturebook"]["route"] == "/picturebook"
    # 表情与头像 / 纪念写真 / 图片工作台：可用，各自数自己的内容目录（这里还没有 → 0）
    for mid in ("sticker", "portrait", "image"):
        assert by_id[mid]["available"] is True and by_id[mid]["project_count"] == 0
        assert by_id[mid]["route"] == f"/{mid}" and by_id[mid]["icon"]
    # 配乐（DESIGN §44）：content/music 下每个 json 一个项目
    assert by_id["music"]["available"] is True and by_id["music"]["project_count"] == 0
    assert by_id["music"]["route"] == "/music" and by_id["music"]["icon"] == "🎵"
    # 单条视频（DESIGN §43）：content/clip 下每个 json 一条片子
    assert by_id["clip"]["available"] is True and by_id["clip"]["project_count"] == 0
    assert by_id["clip"]["route"] == "/clip" and by_id["clip"]["icon"] == "🎞"
    # 剧本工作台：content/script 下每个 json 一份稿子
    assert by_id["script"]["available"] is True and by_id["script"]["project_count"] == 0
    assert by_id["script"]["route"] == "/script" and by_id["script"]["icon"] == "✍️"
    # 占位模块（还没落地的那些）：不可用、没有路由、计数恒为 0；每个模块都要有中英说明
    for row in by_id.values():
        assert row["description"] and row["description_en"]
        if row["status"] == "planned":
            assert row["available"] is False and row["project_count"] == 0 and row["route"] is None


def test_api_modules_counts_picturebooks(isolated_project):
    books = isolated_project / "content" / "picturebook"
    books.mkdir(parents=True)
    (books / "PB-20260909-1.json").write_text("{}", encoding="utf-8")
    (books / "PB-20260909-2.json").write_text("{}", encoding="utf-8")
    (books / "notes.txt").write_text("x", encoding="utf-8")
    by_id = {m["id"]: m for m in client.get("/api/modules").json()["modules"]}
    assert by_id["picturebook"]["project_count"] == 2
    assert by_id["drama"]["project_count"] == 0


def test_api_modules_counts_music(isolated_project):
    """配乐（DESIGN §44）：``content/music`` 下每个 json 一个项目。"""

    folder = isolated_project / "content" / "music"
    folder.mkdir(parents=True)
    (folder / "MU-20260910-1.json").write_text("{}", encoding="utf-8")
    (folder / "MU-20260910-2.json").write_text("{}", encoding="utf-8")
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    by_id = {m["id"]: m for m in client.get("/api/modules").json()["modules"]}
    assert by_id["music"]["project_count"] == 2
    assert by_id["picturebook"]["project_count"] == 0


def test_api_modules_counts_image_sets(isolated_project):
    """图片集三个模块：``content/sticker`` / ``content/portrait`` / ``content/image`` 下每个 json 一个项目。"""

    for module, count in (("sticker", 2), ("portrait", 1), ("image", 3)):
        folder = isolated_project / "content" / module
        folder.mkdir(parents=True)
        for n in range(count):
            (folder / f"X-{n}.json").write_text("{}", encoding="utf-8")
        (folder / "notes.txt").write_text("x", encoding="utf-8")
    by_id = {m["id"]: m for m in client.get("/api/modules").json()["modules"]}
    assert by_id["sticker"]["project_count"] == 2 and by_id["portrait"]["project_count"] == 1
    assert by_id["image"]["project_count"] == 3
    assert by_id["picturebook"]["project_count"] == 0


def test_api_modules_counts_clips(isolated_project):
    """单条视频（DESIGN §43）：``content/clip`` 下每个 json 一条片子。"""

    folder = isolated_project / "content" / "clip"
    folder.mkdir(parents=True)
    for name in ("CL-20260910-1.json", "CL-20260910-2.json"):
        (folder / name).write_text("{}", encoding="utf-8")
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    by_id = {m["id"]: m for m in client.get("/api/modules").json()["modules"]}
    assert by_id["clip"]["project_count"] == 2
    assert by_id["drama"]["project_count"] == 0


def test_api_modules_without_content_dir(isolated_project, monkeypatch):
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", isolated_project / "nowhere")
    body = client.get("/api/modules").json()
    assert next(m for m in body["modules"] if m["id"] == "drama")["project_count"] == 0


# ---------------------------------------------------------------------------- 列表 / 详情 / 概览带 module


def test_listing_detail_and_summary_carry_module(isolated_project):
    _write(isolated_project, "EP1")
    rows = client.get("/api/episodes").json()
    assert [r["module"] for r in rows] == ["drama"]
    assert rows[0]["pipeline"] == "storyvideo"
    detail = client.get("/api/episodes/storyvideo/EP1").json()
    assert detail["module"] == "drama"
    summary = client.get("/api/episodes/storyvideo/EP1/summary").json()
    assert summary["module"] == "drama"


def test_listing_error_rows_still_carry_module(isolated_project):
    (isolated_project / "content" / "story_video" / "BAD.json").write_text("{not json", encoding="utf-8")
    rows = client.get("/api/episodes").json()
    assert rows[0]["status"] == "error" and rows[0]["module"] == "drama"


# ---------------------------------------------------------------------------- 任务按模块过滤


def _wait(task_id: str) -> dict:
    import time

    for _ in range(200):
        item = tasks.get(task_id)
        if item and item.get("status") in ("succeeded", "failed"):
            return item
        time.sleep(0.01)
    raise AssertionError("task did not finish")


def test_tasks_record_module_and_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    tasks._running.clear()
    t1 = tasks.start("asr-qc", "EP1", lambda: {"ok": 1})
    t2 = tasks.start("render", "IMG1", lambda: {"ok": 2}, module="image")
    _wait(t1)
    _wait(t2)
    assert tasks.get(t1)["module"] == "drama"
    assert tasks.get(t2)["module"] == "image"
    # 老记录没有 module 字段：按 drama 算
    (tasks.tasks_dir() / "old.json").write_text(json.dumps({"task_id": "old", "kind": "post", "episode_id": "EP0",
                                                             "status": "succeeded", "created_at": 1}), encoding="utf-8")
    assert {t["task_id"] for t in tasks.list_tasks(module="drama")} == {t1, "old"}
    assert {t["task_id"] for t in tasks.list_tasks(module="image")} == {t2}
    assert {t["task_id"] for t in tasks.list_tasks()} == {t1, t2, "old"}
    assert [t["task_id"] for t in tasks.list_tasks("EP1", "drama")] == [t1]
    assert tasks.list_tasks("EP1", "image") == []

    assert {t["task_id"] for t in client.get("/api/tasks?module=drama").json()} == {t1, "old"}
    assert {t["task_id"] for t in client.get("/api/tasks?module=image").json()} == {t2}
    assert {t["task_id"] for t in client.get("/api/tasks").json()} == {t1, t2, "old"}
    r = client.get("/api/tasks?module=nope")
    assert r.status_code == 404 and "nope" in r.json()["detail"]
    r_en = client.get("/api/tasks?module=nope", headers={"X-StoryStudio-Lang": "en"})
    assert r_en.json()["detail"] == "No such module: nope"


def test_tasks_listing_shape_for_the_task_centre(tmp_path, monkeypatch):
    """任务中心页（DESIGN §38）要的字段与过滤：kind / episode_id / module / status / 时间戳 / error，
    ``?episode_id=`` 与 ``?module=`` 可以叠加，列表按 created_at 倒序。"""

    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    tasks._running.clear()
    ok = tasks.start("pb-pages", "PB-1", lambda: {"pages": 3}, module="picturebook")
    _wait(ok)

    def boom() -> dict:
        raise RuntimeError("生图来源没配置")

    bad = tasks.start("set-generate", "ST-1", boom, module="sticker")
    _wait(bad)

    rows = client.get("/api/tasks").json()
    assert [r["task_id"] for r in rows] == [bad, ok]        # created_at 倒序
    by_id = {r["task_id"]: r for r in rows}
    assert by_id[ok]["kind"] == "pb-pages" and by_id[ok]["module"] == "picturebook"
    assert by_id[ok]["status"] == "succeeded" and by_id[ok]["result"] == {"pages": 3}
    assert by_id[ok]["episode_id"] == "PB-1"
    assert by_id[ok]["created_at"] <= by_id[ok]["started_at"] <= by_id[ok]["finished_at"]
    assert by_id[bad]["status"] == "failed" and "生图来源没配置" in by_id[bad]["error"]

    assert [r["task_id"] for r in client.get("/api/tasks?module=sticker").json()] == [bad]
    assert [r["task_id"] for r in client.get("/api/tasks?episode_id=PB-1").json()] == [ok]
    assert [r["task_id"] for r in client.get("/api/tasks?episode_id=PB-1&module=picturebook").json()] == [ok]
    assert client.get("/api/tasks?episode_id=PB-1&module=sticker").json() == []
    assert client.get("/api/tasks?episode_id=nope").json() == []


# ---------------------------------------------------------------------------- 产品名唯一来源


def test_product_name_is_single_sourced():
    from studio import desktop

    assert desktop.APP_TITLE == branding.PRODUCT_NAME
    assert app.title.startswith(branding.PRODUCT_NAME)
    assert client.get("/api/health").json()["product"]["name"] == branding.PRODUCT_NAME
    assert "Content Cook" in branding.PRODUCT_NAME and "Content Cook" in branding.PRODUCT_NAME_EN
