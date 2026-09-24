"""``GET /api/recent``（DESIGN §38）：工作台首页的「最近」——跨模块按 mtime 倒序列项目。

只读、不联网。这里验证四件事：顺序按 mtime、一个坏文件不毁整张表、剧本层文件不算项目、
每个模块的项目地址跟前端路由对得上。
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from webui import deps
from webui.app import app
from webui.routers import recent as recent_router

client = TestClient(app)


@pytest.fixture
def content_root(tmp_path, monkeypatch):
    story = tmp_path / "content" / "story_video"
    story.mkdir(parents=True)
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", story)
    return tmp_path


def _write(root, module: str, project_id: str, payload: dict | str, mtime: float | None = None) -> None:
    folder = root / "content" / ("story_video" if module == "drama" else module)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{project_id}.json"
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _get(query: str = "") -> list[dict]:
    r = client.get(f"/api/recent{query}")
    assert r.status_code == 200
    return r.json()


# ---------------------------------------------------------------------------- 顺序 / 形状


def test_orders_by_mtime_across_modules(content_root):
    _write(content_root, "drama", "EP1", {"title_zh": "加班夜"}, mtime=1000)
    _write(content_root, "picturebook", "PB-1", {"title_zh": "小狐狸的三只袜子"}, mtime=3000)
    _write(content_root, "music", "MU-1", {"title": "摇篮曲"}, mtime=2000)
    rows = _get()
    assert [r["id"] for r in rows] == ["PB-1", "MU-1", "EP1"]
    assert [r["module"] for r in rows] == ["picturebook", "music", "drama"]
    assert [r["title"] for r in rows] == ["小狐狸的三只袜子", "摇篮曲", "加班夜"]
    first = rows[0]
    assert first["module_name"] == "绘本 / 有声书" and first["module_name_en"] == "Picture book / audiobook"
    assert first["icon"] == "📖"
    assert first["url"] == "/picturebook/b/PB-1/overview"
    assert first["mtime"] == 3000
    assert first["path"] == "content/picturebook/PB-1.json"


def test_empty_when_no_content_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", tmp_path / "nowhere")
    assert _get() == []


def test_limit_honoured(content_root):
    for n in range(5):
        _write(content_root, "clip", f"CL-{n}", {"title": f"片子 {n}"}, mtime=1000 + n)
    assert [r["id"] for r in _get("?limit=2")] == ["CL-4", "CL-3"]
    assert len(_get()) == 5           # 默认 12
    r = client.get("/api/recent?limit=0")
    assert r.status_code == 422       # 上下界由 Query 兜住，不是静默取默认值


# ---------------------------------------------------------------------------- 容错


def test_broken_json_falls_back_to_id_and_does_not_break_the_list(content_root):
    _write(content_root, "drama", "BAD", "{not json", mtime=2000)
    _write(content_root, "drama", "EP1", {"title_zh": "加班夜"}, mtime=1000)
    rows = _get()
    assert [(r["id"], r["title"]) for r in rows] == [("BAD", "BAD"), ("EP1", "加班夜")]
    assert rows[0]["url"] == "/drama/p/BAD/overview"


def test_titleless_and_non_dict_files_fall_back_to_id(content_root):
    _write(content_root, "sticker", "ST-1", {"items": []}, mtime=3000)
    _write(content_root, "sticker", "ST-2", "[1, 2, 3]", mtime=2000)
    _write(content_root, "sticker", "ST-3", {"title": "   "}, mtime=1000)
    assert [r["title"] for r in _get()] == ["ST-1", "ST-2", "ST-3"]


def test_screenplay_sidecars_are_not_projects(content_root):
    _write(content_root, "drama", "EP1", {"title_zh": "加班夜"}, mtime=1000)
    (content_root / "content" / "story_video" / "EP1.screenplay.json").write_text("{}", encoding="utf-8")
    (content_root / "content" / "story_video" / "notes.txt").write_text("x", encoding="utf-8")
    assert [r["id"] for r in _get()] == ["EP1"]


def test_title_prefers_zh_then_plain_then_en(content_root):
    assert recent_router.read_title.__doc__       # 纯函数，直接点
    _write(content_root, "picturebook", "PB-1", {"title_en": "Little Bear", "title_zh": "小熊"}, mtime=3000)
    _write(content_root, "picturebook", "PB-2", {"title_en": "Only English"}, mtime=2000)
    _write(content_root, "image", "IM-1", {"title": "河灯夜", "title_en": "Lantern night"}, mtime=1000)
    assert [r["title"] for r in _get()] == ["小熊", "Only English", "河灯夜"]


def test_huge_file_still_gets_a_title_from_the_head(content_root, monkeypatch):
    monkeypatch.setattr(recent_router, "_FULL_PARSE_LIMIT", 10)
    _write(content_root, "clip", "CL-1", {"title": "城市清晨短片", "prompt": "x" * 200}, mtime=1000)
    assert [r["title"] for r in _get()] == ["城市清晨短片"]


def test_bom_project_file_keeps_its_title(content_root):
    """各模块列表接口都用 utf-8-sig 读（项目文件历史上有些带 BOM）；「最近」以前用纯 utf-8，同一个文件只剩 id。"""

    folder = content_root / "content" / "clip"
    folder.mkdir(parents=True, exist_ok=True)
    bom = b"\xef\xbb\xbf"                # UTF-8 BOM（写成字节，源码里别放看不见的 U+FEFF）
    (folder / "withbom.json").write_bytes(bom + json.dumps({"title": "城市清晨 · 30 秒示例短片"}, ensure_ascii=False).encode("utf-8"))
    assert recent_router.read_title(folder / "withbom.json", "withbom") == "城市清晨 · 30 秒示例短片"
    assert {r["id"]: r["title"] for r in _get()}["withbom"] == "城市清晨 · 30 秒示例短片"
    # 真坏的文件仍退回 id，不抛
    (folder / "broken.json").write_bytes(bom + b"{not json")
    assert recent_router.read_title(folder / "broken.json", "broken") == "broken"
    assert {r["id"]: r["title"] for r in _get()}["broken"] == "broken"


# ---------------------------------------------------------------------------- 地址跟前端路由对齐


def test_url_per_module(content_root):
    for module, project_id in (("drama", "EP1"), ("picturebook", "PB-1"), ("sticker", "ST-1"),
                               ("portrait", "PT-1"), ("image", "IM-1"), ("clip", "CL-1"), ("music", "MU-1"),
                               ("podcast", "PC-1"), ("script", "SC-1")):
        _write(content_root, module, project_id, {"title": project_id})
    urls = {r["module"]: r["url"] for r in _get()}
    assert urls == {
        "drama": "/drama/p/EP1/overview",
        "picturebook": "/picturebook/b/PB-1/overview",
        "sticker": "/sticker/s/ST-1/items",
        "portrait": "/portrait/s/PT-1/items",
        "image": "/image/s/IM-1/items",
        "clip": "/clip/c/CL-1/write",
        "music": "/music/m/MU-1/generate",
        "podcast": "/podcast/p/PC-1/script",
        "script": "/script/p/SC-1/write",
    }
    # 注册表里每个 ready 模块都要有地址模板（新模块落地时别忘了这一处）
    from webui.modules import MODULES

    assert {m.id for m in MODULES if m.available} <= set(recent_router.PROJECT_ROUTES)
    assert recent_router.project_url("nope", "X") is None
