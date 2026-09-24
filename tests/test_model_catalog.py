"""模型目录：OpenRouter 三个列表的解析与合并、缓存、能力表的动态覆盖、能力检查里的目录提示、接口。"""

from __future__ import annotations

import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from genkit import capabilities
from genkit import model_catalog as MC
from genkit import settings as S
from webui.app import app

client = TestClient(app)

CHAT = [
    {"id": "openai/gpt-image-1", "name": "GPT Image 1", "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["image"]}},
    {"id": "openai/gpt-4o-mini-tts", "name": "TTS", "architecture": {"input_modalities": ["text"], "output_modalities": ["audio"]}},
    {"id": "anthropic/claude", "name": "Claude", "architecture": {"modality": "text+image->text"}, "context_length": 200000, "pricing": {"prompt": "0.000003"}},
    {"name": "no id"},
]
IMAGES = [
    {"id": "bytedance-seed/seedream-4.5", "name": "Seedream 4.5", "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["image"]},
     "supported_parameters": {"aspect_ratio": {"type": "enum", "values": ["1:1", "16:9", "9:16"]}, "input_references": {"type": "range", "min": 0, "max": 5}}},
    {"id": "openai/gpt-image-1", "name": "GPT Image 1", "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["image"]},
     "supported_parameters": {"input_references": {"type": "range", "min": 0, "max": 0}}},
]
VIDEOS = [
    {"id": "minimax/hailuo-3", "name": "Hailuo 3", "supported_resolutions": ["768p", "1080p"], "supported_aspect_ratios": ["16:9", "9:16"],
     "supported_durations": [6, 10], "supported_frame_images": ["first_frame", "last_frame"], "generate_audio": True},
    {"id": "text-only/video", "name": "T2V only", "supported_durations": [5], "supported_frame_images": [], "generate_audio": False},
]


def sample_models():
    return MC.merge_models(
        [m for m in (MC.classify(x) for x in CHAT) if m],
        [m for m in (MC.classify_image(x) for x in IMAGES) if m],
        [m for m in (MC.classify_video(x) for x in VIDEOS) if m],
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    return tmp_path / "home"


def test_classify_and_merge():
    rows = {m["id"]: m for m in sample_models()}
    assert len(rows) == 6
    assert rows["anthropic/claude"]["roles"] == ["text"] and rows["anthropic/claude"]["input"] == ["text", "image"]
    assert rows["openai/gpt-4o-mini-tts"]["roles"] == ["voice"]
    # 专用接口的 caps 覆盖 /models 里的同 id 记录，角色合并
    gpt = rows["openai/gpt-image-1"]
    assert gpt["roles"] == ["image"] and gpt["caps"] == {"reference_images": False, "max_reference_images": 0}
    seed = rows["bytedance-seed/seedream-4.5"]
    assert seed["caps"] == {"aspect_ratios": ["1:1", "16:9", "9:16"], "reference_images": True, "max_reference_images": 5}
    hailuo = rows["minimax/hailuo-3"]
    assert hailuo["caps"] == {"durations": {"choices": [6, 10]}, "resolutions": ["768p", "1080p"], "aspect_ratios": ["16:9", "9:16"], "first_frame": True, "audio": True}
    t2v = rows["text-only/video"]
    assert t2v["caps"]["first_frame"] is False and t2v["input"] == ["text"]
    assert [m["id"] for m in sample_models()] == sorted(m["id"] for m in sample_models())


def test_refresh_caches_and_borrows_key(home):
    settings = S.Settings()
    settings.role("image").adapter = "openrouter_images"
    settings.role("image").api_key = "sk-or-v1-secret"
    S.save_settings(settings)
    seen = {}

    def fake_fetch(base_url, api_key):
        seen["base"] = base_url
        seen["key"] = api_key
        return sample_models()

    part = MC.refresh("openrouter", fetcher=fake_fetch)
    assert seen == {"base": "https://openrouter.ai/api/v1", "key": "sk-or-v1-secret"}
    assert part["fetched_at"] >= int(time.time()) - 5
    assert (home / "model_catalog.json").exists()
    assert [m["id"] for m in MC.models_for("video")] == ["minimax/hailuo-3", "text-only/video"]
    assert MC.find("openrouter", "OpenAI/GPT-Image-1")["name"] == "GPT Image 1"
    with pytest.raises(ValueError):
        MC.refresh("nope", fetcher=fake_fetch)


def test_catalog_silent_without_cache(home):
    assert MC.catalog_issues("image", "openrouter_images", "whatever") == []
    assert MC.dynamic_caps("video", "openrouter_video", "minimax/hailuo-3") is None
    # 静态表照旧
    assert capabilities.lookup("video", "openrouter_video", "google/veo-3.1")["source"] == "doc"


def test_dynamic_caps_override_static_table(home):
    MC.save_catalog({"openrouter": {"fetched_at": 0, "models": sample_models()}})
    caps = capabilities.lookup("video", "openrouter_video", "minimax/hailuo-3")
    assert caps["source"] == "catalog" and caps["resolutions"] == ["768p", "1080p"] and caps["known"] is True
    # 目录里没有的模型退回静态表；非 openrouter 适配器不受目录影响
    assert capabilities.lookup("video", "openrouter_video", "google/veo-3.1")["source"] == "doc"
    assert capabilities.lookup("video", "ark_video", "doubao-seedance-2-0-fast")["source"] == "verified"
    assert capabilities.lookup("video", "openrouter_video", "minimax/hailuo-3", use_catalog=False)["source"] == "doc"


def test_catalog_issues(home):
    MC.save_catalog({"openrouter": {"fetched_at": 0, "models": sample_models()}})
    assert MC.catalog_issues("image", "openrouter_images", "bytedance-seed/seedream-4.5", uses_refs=True) == []
    missing = MC.catalog_issues("image", "openrouter_images", "bytedance-seed/seedream-9")
    assert missing and "没有模型" in missing[0]["message"]
    wrong = MC.catalog_issues("image", "openrouter_images", "minimax/hailuo-3")
    assert wrong and "不出图" in wrong[0]["message"]
    # 参考图上限 0 → 不接受参考图
    no_ref = MC.catalog_issues("image", "openrouter_images", "openai/gpt-image-1", uses_refs=True)
    assert no_ref and "不接受图片输入" in no_ref[0]["message"]
    t2v = MC.catalog_issues("video", "openrouter_video", "text-only/video", uses_refs=True)
    assert t2v and "首帧会被忽略" in t2v[0]["message"]
    assert MC.catalog_issues("video", "ark_video", "text-only/video", uses_refs=True) == []


def test_check_spec_uses_catalog(home):
    MC.save_catalog({"openrouter": {"fetched_at": 0, "models": sample_models()}})
    settings = S.Settings()
    settings.role("image").adapter = "openrouter_images"
    settings.role("image").model = "bytedance-seed/seedream-9"
    settings.role("video").adapter = "openrouter_video"
    settings.role("video").model = "minimax/hailuo-3"
    spec = {"image_params": {"model": "@image"}, "video_params": {"model": "@video", "resolution": "720p"},
            "shots": [{"id": "S1", "dur": 6}, {"id": "S2", "dur": 7}], "cast": {}}
    report = capabilities.check_spec(spec, settings)
    msgs = [i["message"] for i in report["issues"]]
    assert any("没有模型 bytedance-seed/seedream-9" in m for m in msgs)
    # 目录来的区间：7 秒越界、720p 不在列表；都是提醒不是拦截
    assert any("按服务商目录" in m and "S2" in m for m in msgs)
    assert any("768p / 1080p" in m for m in msgs)
    assert report["errors"] == 0

    settings.role("video").model = "text-only/video"
    spec["shots"] = [{"id": "S1", "dur": 5}]
    msgs = [i["message"] for i in capabilities.check_spec(spec, settings)["issues"]]
    assert any("不接受首帧图" in m for m in msgs)


def test_catalog_endpoints(home, monkeypatch):
    empty = client.get("/api/system/model-catalog").json()
    assert empty["count"] == 0 and empty["fetched_at"] is None
    assert client.get("/api/system/model-catalog?source=nope").status_code == 404

    monkeypatch.setattr(MC, "fetch_openrouter", lambda base, key: sample_models())
    out = client.post("/api/system/model-catalog/refresh", json={"source": "openrouter"}).json()
    assert out["count"] == 6
    assert [m["id"] for m in out["roles"]["image"]] == ["bytedance-seed/seedream-4.5", "openai/gpt-image-1"]
    assert out["roles"]["video"][0]["caps"]["durations"] == {"choices": [6, 10]}
    assert out["roles"]["voice"][0]["id"] == "openai/gpt-4o-mini-tts"

    def boom(base, key):
        raise OSError("network down")

    monkeypatch.setattr(MC, "fetch_openrouter", boom)
    r = client.post("/api/system/model-catalog/refresh", json={"source": "openrouter"})
    assert r.status_code == 502 and "network down" in r.json()["detail"]
    # 失败不覆盖已有缓存
    assert client.get("/api/system/model-catalog").json()["count"] == 6


def test_fetch_openrouter_hits_three_endpoints(monkeypatch):
    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    captured = []

    def fake_urlopen(request, timeout):
        captured.append((request.full_url, request.get_header("Authorization")))
        if request.full_url.endswith("/images/models"):
            body = {"data": IMAGES}
        elif request.full_url.endswith("/videos/models"):
            raise OSError("videos endpoint down")
        else:
            body = {"data": CHAT}
        return FakeResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(MC.urllib.request, "urlopen", fake_urlopen)
    models = MC.fetch_openrouter("https://openrouter.ai/api/v1/", "sk-or-x")
    assert [u for u, _ in captured] == ["https://openrouter.ai/api/v1/models", "https://openrouter.ai/api/v1/images/models", "https://openrouter.ai/api/v1/videos/models"]
    assert all(a == "Bearer sk-or-x" for _, a in captured)
    # 视频接口挂了不算失败，只是没有视频模型
    ids = [m["id"] for m in models]
    assert "bytedance-seed/seedream-4.5" in ids and not any(m["roles"] == ["video"] for m in models)
