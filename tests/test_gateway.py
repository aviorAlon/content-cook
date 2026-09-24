"""统一网关：设置里存一份地址+密钥，一键应用到四个角色；网关的 /models 进模型目录；能力检查按 base_url 认出网关。"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from genkit import capabilities
from genkit import model_catalog as MC
from genkit import settings as S
from genkit.adapters.openai_video import size_for
from webui.app import app

client = TestClient(app)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    for role in ("TEXT", "IMAGE", "VOICE", "VIDEO"):
        for key in ("ADAPTER", "BASE_URL", "API_KEY", "MODEL", "VOICE"):
            monkeypatch.delenv(f"STORYSTUDIO_{role}_{key}", raising=False)
    return tmp_path / "home"


def test_gateway_settings_roundtrip_and_masking(home):
    r = client.put("/api/settings", json={"gateway": {"base_url": "http://127.0.0.1:4000/v1/", "api_key": "sk-litellm-secret"}})
    assert r.status_code == 200
    gw = r.json()["settings"]["gateway"]
    assert gw["base_url"] == "http://127.0.0.1:4000/v1" and "*" in gw["api_key"] and "secret" not in gw["api_key"]
    # 回传打码值不覆盖真密钥
    client.put("/api/settings", json={"gateway": {"api_key": gw["api_key"]}})
    assert S.load_settings().gateway.api_key == "sk-litellm-secret"
    # 空串也不清掉（和角色密钥一致）
    client.put("/api/settings", json={"gateway": {"api_key": ""}})
    assert S.load_settings().gateway.api_key == "sk-litellm-secret"


def test_apply_gateway_sets_roles(home):
    assert client.post("/api/settings/gateway/apply", json={"roles": {"text": {"model": "gpt-4o"}}}).status_code == 400  # 没填网关
    client.put("/api/settings", json={"gateway": {"base_url": "http://gw.local/v1", "api_key": "k1"}})
    r = client.post("/api/settings/gateway/apply", json={
        "roles": {"text": {"model": "gpt-4o"}, "image": {"model": "gpt-image-1"}, "voice": {"model": "tts-1", "voice": "nova"}, "video": {"model": "sora-2"}},
    })
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == ["text", "image", "voice", "video"]
    st = S.load_settings()
    assert st.role("text").adapter == "openai_chat" and st.role("text").base_url == "http://gw.local/v1" and st.role("text").api_key == "k1"
    assert st.role("image").adapter == "openai_images" and st.role("image").model == "gpt-image-1"
    assert st.role("voice").adapter == "openai_speech" and st.role("voice").voice == "nova"
    assert st.role("video").adapter == "openai_video" and st.role("video").model == "sora-2"
    # 配乐角色不走网关（没有 OpenAI 兼容的音乐接口形状）：网关一键应用不碰它，它仍是"没选适配器"
    assert r.json()["status"] == {"text": [], "image": [], "voice": [], "video": [], "music": ["music: 没选适配器"]}
    assert client.post("/api/settings/gateway/apply", json={"roles": {"music": {"model": "lyria"}}}).json()["changed"] == []
    assert S.load_settings().role("music").adapter == ""
    # 只动给出的角色
    r = client.post("/api/settings/gateway/apply", json={"roles": {"video": {"model": "veo-3"}}})
    assert r.json()["changed"] == ["video"] and S.load_settings().role("text").model == "gpt-4o"
    assert client.post("/api/settings/gateway/apply", json={"roles": {"nope": {}}}).status_code == 400
    assert S.uses_gateway(S.load_settings(), S.load_settings().role("video")) is True


def test_gateway_test_and_catalog(home, monkeypatch):
    assert client.post("/api/settings/gateway/test").json()["ok"] is False
    client.put("/api/settings", json={"gateway": {"base_url": "http://gw.local/v1", "api_key": "k1"}})

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    captured = []

    def fake_urlopen(request, timeout):
        captured.append((request.full_url, request.get_header("Authorization")))
        body = {"object": "list", "data": [{"id": "gpt-4o"}, {"id": "gpt-image-1"}, {"id": "tts-1"}, {"id": "veo-3.1"}, {"id": "bytedance/seedance-1.0"}]}
        return FakeResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(MC.urllib.request, "urlopen", fake_urlopen)
    r = client.post("/api/settings/gateway/test").json()
    assert r["ok"] is True and r["count"] == 5
    assert captured[-1] == ("http://gw.local/v1/models", "Bearer k1")

    out = client.post("/api/system/model-catalog/refresh", json={"source": "gateway"}).json()
    assert out["source"] == "gateway" and out["count"] == 5
    assert [m["id"] for m in out["roles"]["video"]] == ["bytedance/seedance-1.0", "veo-3.1"]
    assert [m["id"] for m in out["roles"]["image"]] == ["gpt-image-1"]
    assert [m["id"] for m in out["roles"]["voice"]] == ["tts-1"]
    assert [m["id"] for m in out["roles"]["text"]] == ["gpt-4o"]
    # openrouter 那份不受影响
    assert client.get("/api/system/model-catalog?source=openrouter").json()["count"] == 0


def test_source_for_and_check_spec_with_gateway(home):
    client.put("/api/settings", json={"gateway": {"base_url": "http://gw.local/v1", "api_key": "k1"}})
    st = S.load_settings()
    assert MC.source_for("openrouter_video") == "openrouter"
    assert MC.source_for("openai_chat", "https://openrouter.ai/api/v1") == "openrouter"
    assert MC.source_for("openai_video", "http://gw.local/v1/", st) == "gateway"
    assert MC.source_for("openai_images", "https://api.openai.com/v1", st) is None
    assert MC.source_for("ark_video", "http://gw.local/v1", st) is None

    MC.save_catalog({"gateway": {"fetched_at": 0, "models": [m for m in (MC.classify_by_id({"id": x}) for x in ("gpt-image-1", "veo-3.1")) if m]}})
    S.apply_gateway(st, {"image": {"model": "gpt-image-9"}, "video": {"model": "veo-3.1"}})
    S.save_settings(st)
    spec = {"image_params": {"model": "@image"}, "video_params": {"model": "@video"}, "shots": [{"id": "S1", "dur": 6}], "cast": {}}
    msgs = [i["message"] for i in capabilities.check_spec(spec, st)["issues"]]
    assert any("统一网关" in m and "gpt-image-9" in m for m in msgs)
    assert not any("veo-3.1" in m and "没有模型" in m for m in msgs)
    # 网关目录的角色是猜的：不拿模态拦人
    assert MC.catalog_issues("image", "openai_images", "veo-3.1", base_url="http://gw.local/v1") == []


def test_openai_video_size_table():
    assert size_for("9:16", "720p") == "720x1280"
    assert size_for("16:9", "1080p") == "1920x1080"
    assert size_for("weird", "x") == "720x1280"
