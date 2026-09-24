"""可选组件与更新：faster-whisper 安装/模型下载任务、版本清单检查；插卡预览接口；镜次画面文字条目。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import settings as S
from genkit.version import __version__
from webui import deps
from webui.app import app
from webui.routers import assist
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router

client = TestClient(app)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    return tmp_path


def wait_task(task_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = client.get(f"/api/tasks/{task_id}").json()
        if item["status"] in ("succeeded", "failed"):
            return item
        time.sleep(0.05)
    raise AssertionError("task did not finish")


def test_asr_status_and_install_task(home, monkeypatch):
    monkeypatch.setattr(assist.importlib.util, "find_spec", lambda name: None)
    monkeypatch.delattr(assist.sys, "_MEIPASS", raising=False)
    body = client.get("/api/system/asr").json()
    assert body["installed"] is False and body["frozen"] is False and "pip install" in body["install_hint"]
    calls = []
    monkeypatch.setattr(assist, "run_pip_install", lambda pkg: (calls.append(pkg), {"package": pkg, "log": "ok"})[1])
    resp = client.post("/api/system/asr/install")
    assert resp.status_code == 200 and resp.json()["task_id"]
    task = wait_task(resp.json()["task_id"])
    assert task["status"] == "succeeded" and calls == ["faster-whisper"]
    # 打包版：exe 里没有 pip，要借本机同版本的 Python；找不到就给说明（找得到的那条路见 tests/test_asr_extra_site.py）
    monkeypatch.setattr(assist.sys, "_MEIPASS", str(home), raising=False)
    monkeypatch.setattr(assist, "external_python", lambda: None)
    assert client.post("/api/system/asr/install").status_code == 425
    assert client.get("/api/system/asr").json()["frozen"] is True


def test_asr_model_download_task(home, monkeypatch):
    monkeypatch.setattr(assist.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(assist, "asr_model_cached", lambda model: model == "tiny")
    monkeypatch.setattr(assist, "download_asr_model", lambda model: {"model": model, "cached": True})
    assert client.post("/api/system/asr/model", json={"model": "tiny"}).json()["task_id"] is None
    assert client.post("/api/system/asr/model", json={"model": "huge"}).status_code == 400
    resp = client.post("/api/system/asr/model", json={"model": "small"})
    task = wait_task(resp.json()["task_id"])
    assert task["status"] == "succeeded" and task["result"]["model"] == "small"


def test_update_check(home, monkeypatch):
    assert client.get("/api/system/update-check").json() == {"configured": False, "current": __version__, "update_available": False}
    client.put("/api/settings", json={"update_url": "https://example.invalid/manifest.json"})
    assert S.load_settings().update_url == "https://example.invalid/manifest.json"
    monkeypatch.setattr(assist, "fetch_manifest", lambda url, timeout=8.0: {"version": "9.9.9", "url": "https://dl", "notes": "big"})
    body = client.get("/api/system/update-check").json()
    assert body["update_available"] is True and body["latest"] == "9.9.9" and body["url"] == "https://dl"
    monkeypatch.setattr(assist, "fetch_manifest", lambda url, timeout=8.0: {"version": __version__})
    assert client.get("/api/system/update-check").json()["update_available"] is False
    def boom(url, timeout=8.0): raise OSError("no network")
    monkeypatch.setattr(assist, "fetch_manifest", boom)
    body = client.get("/api/system/update-check").json()
    assert body["update_available"] is False and "no network" in body["error"]
    assert assist.parse_version("v1.2.3") == (1, 2, 3) and assist.parse_version("1.10") > assist.parse_version("1.9.9")


def test_insert_preview_and_notebook_links(home):
    spec = {
        "id": "PV-T", "output_root": "output/story_video/PV-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"a": {"block": "A。"}},
        "shots": [{"id": "S1", "dur": 4, "cast": ["a"], "shot": "中景", "art": "a", "motion": "m", "lines": []}],
        "post_graphics": {
            "phone_ui_inserts": [{"id": "UI_1", "after": "S1", "dur": 3, "layout": "notification", "app": "工资卡", "title": "到账", "items": ["¥3.5"]}],
            "notebook_text": [{"shot": "S1", "text": "你好", "y": 0.4, "at": 0.5}],
        },
    }
    (home / "content" / "story_video" / "PV-T.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    font_ok = Path("C:/Windows/Fonts/msyh.ttc").is_file() or Path("/System/Library/Fonts/PingFang.ttc").is_file()
    resp = client.get("/api/episodes/storyvideo/PV-T/post-layers/insert-preview/UI_1")
    if font_ok:
        assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
        # 缓存文件名只用内容摘要，不再拼插卡 id（F1：id 是自由文本）
        cached = list((home / "output" / "story_video" / "PV-T" / "08_ui_preview").glob("*.png"))
        assert len(cached) == 1
        assert client.get("/api/episodes/storyvideo/PV-T/post-layers/insert-preview/UI_1").status_code == 200
    else:
        assert resp.status_code in (200, 425)
    assert client.get("/api/episodes/storyvideo/PV-T/post-layers/insert-preview/NOPE").status_code == 404
    shot = client.get("/api/episodes/storyvideo/PV-T").json()["shots"][0]
    assert shot["links"]["notebook_text"] == [{"text": "你好", "at": 0.5, "y": 0.4, "size": None}]


def test_hf_cache_dir_follows_env_vars(monkeypatch, tmp_path):
    """HF_HUB_CACHE 优先；只设 HF_HOME 时是 <HF_HOME>/hub（以前这里对字符串做 / 运算直接抛 TypeError）；都没设走默认。"""

    from webui.routers import assist

    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    assert assist._hf_cache_dir() == tmp_path / "hf" / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "cache"))
    assert assist._hf_cache_dir() == tmp_path / "cache"
    monkeypatch.delenv("HF_HUB_CACHE")
    monkeypatch.delenv("HF_HOME")
    assert assist._hf_cache_dir().parts[-3:] == (".cache", "huggingface", "hub")
