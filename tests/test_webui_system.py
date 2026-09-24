"""/api/health、示例剧集、本机访问令牌、桌面启动器的目录约定。"""

from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient

from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import scripts as scripts_router

client = TestClient(app)


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    # 每个路由模块在 import 时各自绑了一份路径常量（见 test_webui_episodes_router 的说明），三处都要 patch
    for module in (deps, scripts_router, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    return tmp_path


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "settings.json"))
    for role in ("TEXT", "IMAGE", "VOICE", "VIDEO"):
        for key in ("ADAPTER", "BASE_URL", "API_KEY", "MODEL", "VOICE"):
            monkeypatch.delenv(f"STORYSTUDIO_{role}_{key}", raising=False)
    return tmp_path


def test_health_reports_version_ffmpeg_and_roles(isolated_settings):
    body = client.get("/api/health").json()
    from genkit.version import __version__

    assert body["version"] == __version__
    # 五个角色：配乐（DESIGN §44）是第五个
    assert set(body["roles"]) == {"text", "image", "voice", "video", "music"}
    assert body["roles"]["image"]["configured"] is False
    assert body["roles"]["image"]["missing"]
    assert "ffmpeg_ok" in body and "ffmpeg_download_url" in body
    assert "api_key" not in json.dumps(body)


def test_examples_are_listed_with_summary():
    items = client.get("/api/examples").json()
    names = {item["name"] for item in items}
    assert "first_episode" in names
    first = next(item for item in items if item["name"] == "first_episode")
    assert first["shot_count"] == 5
    assert first["has_voice_over"] and first["has_inserts"]
    assert first["camera_profile"]


def test_create_from_example_writes_a_valid_project(isolated_project):
    resp = client.post("/api/examples/first_episode/create", json={"episode_id": "DEMO-001"})
    assert resp.status_code == 200, resp.text
    path = isolated_project / "content" / "story_video" / "DEMO-001.json"
    assert path.is_file()
    spec = json.loads(path.read_text(encoding="utf-8"))
    assert spec["id"] == "DEMO-001"
    assert spec["output_root"] == "output/story_video/DEMO-001"
    # 模型交给「模型设置」决定，示例不写死厂商
    assert spec["image_params"]["model"] == "@image"
    assert spec["video_params"]["model"] == "@video"
    # 项目页能直接读出来（走真实的 load_spec + 提示词编译）
    detail = client.get("/api/episodes/storyvideo/DEMO-001").json()
    assert len(detail["shots"]) == 5
    assert detail["editable"] is True

    dup = client.post("/api/examples/first_episode/create", json={"episode_id": "DEMO-001"})
    assert dup.status_code == 409
    missing = client.post("/api/examples/nope/create", json={"episode_id": "X"})
    assert missing.status_code == 404
    blank = client.post("/api/examples/first_episode/create", json={"episode_id": "  "})
    assert blank.status_code == 200 and blank.json()["id"] == "DEMO-1"   # 空着 = 自动编号


def test_local_token_guard(monkeypatch, isolated_settings):
    monkeypatch.setenv("STORYSTUDIO_TOKEN", "sekrit")
    fresh = TestClient(app)
    assert fresh.get("/api/health").status_code == 401
    assert fresh.get("/api/health", headers={"X-StoryStudio-Token": "sekrit"}).status_code == 200
    # 首页带 token 打开 → 种 cookie → 之后的 /api 请求自动带上。cookie 名带服务端口（TestClient 的是 80）：
    # cookie 不分端口，同机两个实例不能共用一个名字（SEC-4）
    page = fresh.get("/?token=sekrit")
    assert page.status_code == 200
    assert fresh.cookies.get("storystudio_token_80") == "sekrit"
    assert fresh.get("/api/health").status_code == 200
    # 静态资源不设门
    assert fresh.get("/static/settings.js").status_code == 200


def test_token_guard_is_off_without_env(monkeypatch, isolated_settings):
    monkeypatch.delenv("STORYSTUDIO_TOKEN", raising=False)
    assert TestClient(app).get("/api/health").status_code == 200


def test_desktop_default_project_root(monkeypatch, tmp_path):
    from studio import desktop

    monkeypatch.delenv("STORYSTUDIO_PROJECT_ROOT", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.chdir(tmp_path)
    assert desktop.default_project_root() == tmp_path.resolve()
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert desktop.default_project_root().name == "StoryStudio"
    monkeypatch.setenv("STORYSTUDIO_PROJECT_ROOT", str(tmp_path / "custom"))
    assert desktop.default_project_root() == (tmp_path / "custom").resolve()


def test_assemble_spec_follows_settings_and_has_no_personal_defaults():
    from storyvideo.scriptgen import assemble_spec

    spec = assemble_spec("EP-X", {"cast": {}, "shots": [{"id": "S1"}]}, camera_profile="dark_comedy_vertical", prompt_language="zh")
    assert "workspace_id" not in spec
    assert spec["image_params"]["model"] == "@image"
    assert spec["video_params"]["model"] == "@video"
    assert assemble_spec("EP-Y", {}, camera_profile="x", prompt_language="zh", workspace_id="team-a")["workspace_id"] == "team-a"
