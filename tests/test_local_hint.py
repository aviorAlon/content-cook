"""「本机可以跑」提示（DESIGN §45「推荐而不强制」，契约 docs/local-models-spec.md §9）：

点名规则（生图 sdwebui 优先、视频只点 ComfyUI、文本按预设顺序）、缓存（一个 TTL 窗口内不重复探活、
只探能顶这个角色的那几个预设）、关掉之后不再回来（存进设置文件，重启还在）、角色已经是本机时不出提示、
只探本机地址、两个接口的形状。

一行网络请求都不发：探活走注入的假 fetch，接口测试把 ``genkit.local.default_fetch`` 换掉。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from genkit import local as L
from genkit import settings as S
from webui.app import app
from webui.routers import local_models as LM

client = TestClient(app)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    for role in ("TEXT", "IMAGE", "VOICE", "VIDEO", "MUSIC"):
        for key in ("ADAPTER", "BASE_URL", "API_KEY", "MODEL", "VOICE"):
            monkeypatch.delenv(f"STORYSTUDIO_{role}_{key}", raising=False)
    LM.reset_hint_cache()
    yield tmp_path / "home"
    LM.reset_hint_cache()


def _fetch(alive_prefixes: tuple[str, ...], seen: list[str] | None = None):
    """假 fetch：地址以这些前缀开头就算在跑，其余「连不上」。"""

    def fetch(url, timeout):
        if seen is not None:
            seen.append(url)
        for prefix in alive_prefixes:
            if url.startswith(prefix):
                return {"models": [{"name": "m1"}], "data": [{"id": "m1"}], "system": {}}
        raise OSError("connection refused")

    return fetch


SD = "http://127.0.0.1:7860"
COMFY = "http://127.0.0.1:8188"
OLLAMA = "http://127.0.0.1:11434"
LMSTUDIO = "http://127.0.0.1:1234"


# ---------------------------------------------------------------------------- 点名规则


def test_image_prefers_sdwebui_and_only_names_comfyui_when_it_is_the_only_one():
    assert L.recommend("image", ["sdwebui", "comfyui"]) == "sdwebui"
    assert L.recommend("image", ["comfyui", "sdwebui"]) == "sdwebui"   # 顺序由偏好表决定，不看谁先探到
    assert L.recommend("image", ["comfyui"]) == "comfyui"
    assert L.recommend("image", ["sdwebui"]) == "sdwebui"
    assert L.recommend("image", []) == ""
    assert L.recommend("image", ["ollama"]) == ""                      # 文本服务不顶生图


def test_video_only_ever_names_comfyui():
    # SD WebUI 不出视频：它在跑也不该被点名到视频这一档
    assert L.recommend("video", ["sdwebui"]) == ""
    assert L.recommend("video", ["sdwebui", "comfyui"]) == "comfyui"
    assert [s.id for s in L.presets_for_role("video")] == ["comfyui"]


def test_text_takes_the_first_alive_one_in_preset_order():
    assert [s.id for s in L.presets_for_role("text")] == ["ollama", "lmstudio", "vllm", "llamacpp"]
    assert L.recommend("text", ["llamacpp", "vllm", "lmstudio", "ollama"]) == "ollama"
    assert L.recommend("text", ["llamacpp", "lmstudio"]) == "lmstudio"
    assert L.recommend("text", ["llamacpp"]) == "llamacpp"


def test_every_recommendable_service_has_a_one_line_reason_and_music_has_none():
    for role in S.ROLES:
        for service_id in L.ROLE_PREFERENCE[role]:
            assert service_id in L.PRESETS_BY_ID
            reason = L.reason_for(service_id)
            assert reason and len(reason) <= 30 and "请" not in reason and "先安装" not in reason
    assert L.ROLE_PREFERENCE["music"] == () and L.presets_for_role("music") == ()
    assert L.recommend("music", ["ollama", "comfyui"]) == ""
    assert L.reason_for("nope") == ""


# ---------------------------------------------------------------------------- 缓存


def test_only_probes_the_presets_that_serve_the_asked_role(home):
    seen: list[str] = []
    LM.probe_for_hint("image", fetch=_fetch((SD,), seen))
    # 生图这一档只有 SD WebUI 和 ComfyUI；Ollama / vLLM / 本地语音一个都不该被碰
    assert seen and all(u.startswith(SD) or u.startswith(COMFY) for u in seen), seen


def test_second_call_inside_the_ttl_window_issues_no_probe(home, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(LM, "_clock", lambda: now[0])
    seen: list[str] = []
    fetch = _fetch((SD,), seen)

    first = LM.hint_payload("image", fetch=fetch)
    assert first["suggestion"]["service"] == "sdwebui"
    count = len(seen)
    assert count

    now[0] += LM.HINT_TTL / 2
    again = LM.hint_payload("image", fetch=fetch)
    assert len(seen) == count                      # 窗口内：一个请求都没再发
    assert again["suggestion"] == first["suggestion"]

    now[0] += LM.HINT_TTL
    LM.hint_payload("image", fetch=fetch)
    assert len(seen) > count                       # 过期了才重探


def test_a_full_scan_fills_the_cache_so_the_hint_does_not_probe_again(home, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(L, "default_fetch", _fetch((SD,), seen))
    client.get("/api/system/local-scan?timeout=0.1")
    count = len(seen)
    assert count
    body = LM.hint_payload("image")
    assert len(seen) == count and body["suggestion"]["service"] == "sdwebui" and body["scanned"] is True


def test_a_role_without_local_presets_probes_nothing(home):
    seen: list[str] = []
    body = LM.hint_payload("music", fetch=_fetch((SD,), seen))
    assert seen == [] and body["available"] == [] and body["suggestion"] is None


def test_the_hint_never_touches_a_non_local_address(home, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(L, "default_fetch", _fetch((), seen))
    # 设置里塞一个公网地址：提示只探写死的预设，绝不会跟着角色配置去连它
    st = S.load_settings()
    st.role("image").adapter, st.role("image").base_url = "openai_images", "https://api.openai.com/v1"
    S.save_settings(st)
    client.get("/api/system/local-hint?role=image")
    assert seen and all(L.is_local_url(u) for u in seen), seen
    assert not [u for u in seen if "openai.com" in u]
    # 接口本身也不收手填地址：多给的查询参数被忽略，不会变成一个探测器
    LM.reset_hint_cache()
    seen.clear()
    client.get("/api/system/local-hint?role=image&base_url=https://evil.example.com")
    assert seen and all(L.is_local_url(u) for u in seen), seen


# ---------------------------------------------------------------------------- 什么时候不提示


def test_no_suggestion_when_nothing_is_running(home):
    body = LM.hint_payload("image", fetch=_fetch(()))
    assert body["available"] == [] and body["suggestion"] is None and body["dismissed"] is False


def test_no_suggestion_when_the_role_is_already_local(home):
    st = S.load_settings()
    st.role("image").adapter, st.role("image").base_url = "sdwebui", SD
    S.save_settings(st)
    body = LM.hint_payload("image", fetch=_fetch((SD, COMFY)))
    assert body["current_is_local"] is True and body["suggestion"] is None
    # 探到的仍然照实报（设置页要用），只是不再劝人切换
    assert [x["service"] for x in body["available"]] == ["sdwebui", "comfyui"]


def test_dismissal_is_persisted_and_suppresses_the_suggestion(home):
    r = client.post("/api/system/local-hint/dismiss", json={"role": "image"})
    assert r.status_code == 200 and r.json()["dismissed"] == ["image"]
    # 存进了设置文件：重新读一遍还在（不是浏览器里的一次性状态）
    assert S.load_settings().local_hints_dismissed == ["image"]
    body = LM.hint_payload("image", fetch=_fetch((SD,)))
    assert body["dismissed"] is True and body["suggestion"] is None
    assert [x["service"] for x in body["available"]] == ["sdwebui"]
    # 关的是生图那一条，别的角色照旧
    LM.reset_hint_cache()
    text = LM.hint_payload("text", fetch=_fetch((OLLAMA,)))
    assert text["dismissed"] is False and text["suggestion"]["service"] == "ollama"


def test_dismiss_all_covers_every_role_and_reset_brings_it_back(home):
    assert client.post("/api/system/local-hint/dismiss", json={"role": "all"}).json()["dismissed"] == ["all"]
    for role in ("image", "video", "text"):
        assert L.hint_dismissed(S.load_settings(), role) is True
    assert LM.hint_payload("image", fetch=_fetch((SD,)))["suggestion"] is None
    assert client.post("/api/system/local-hint/reset").json()["dismissed"] == []
    assert S.load_settings().local_hints_dismissed == []
    LM.reset_hint_cache()
    assert LM.hint_payload("image", fetch=_fetch((SD,)))["suggestion"]["service"] == "sdwebui"


def test_dismiss_rejects_an_unknown_role(home):
    assert client.post("/api/system/local-hint/dismiss", json={"role": "nope"}).status_code == 400
    assert S.load_settings().local_hints_dismissed == []


def test_dismissals_survive_a_settings_reload_and_ignore_junk(home):
    st = S.load_settings()
    st.local_hints_dismissed = ["image", "image", "nope", "video"]
    S.save_settings(st)
    assert S.load_settings().local_hints_dismissed == ["image", "video"]
    # 「全部」压过逐个
    st = S.load_settings()
    st.local_hints_dismissed = ["image", "all"]
    S.save_settings(st)
    assert S.load_settings().local_hints_dismissed == ["all"]
    assert S.normalize_hint_dismissals("image") == [] and S.normalize_hint_dismissals(None) == []


# ---------------------------------------------------------------------------- 接口形状


def test_local_hint_endpoint_shape(home, monkeypatch):
    monkeypatch.setattr(L, "default_fetch", _fetch((SD, COMFY)))
    body = client.get("/api/system/local-hint?role=image").json()
    assert set(body) == {"role", "available", "current_is_local", "suggestion", "dismissed", "scanned"}
    assert body["role"] == "image" and body["current_is_local"] is False and body["dismissed"] is False
    assert body["suggestion"] == {"service": "sdwebui", "name": L.PRESETS_BY_ID["sdwebui"].name,
                                  "reason": L.reason_for("sdwebui")}
    first = body["available"][0]
    assert set(first) == {"service", "name", "base_url", "models", "roles"}
    assert first["service"] == "sdwebui" and first["base_url"] == SD and first["roles"] == ["image"]
    # 视频那一档点名 ComfyUI（同一批在跑的服务，不同角色不同答案）
    video = client.get("/api/system/local-hint?role=video").json()
    assert video["suggestion"]["service"] == "comfyui"
    assert client.get("/api/system/local-hint?role=nope").status_code == 400


def test_local_scan_endpoint_marks_the_recommended_service_per_role(home, monkeypatch):
    monkeypatch.setattr(L, "default_fetch", _fetch((SD, COMFY, LMSTUDIO)))
    body = client.get("/api/system/local-scan?timeout=0.1").json()
    assert body["recommend"] == {"text": "lmstudio", "image": "sdwebui", "video": "comfyui"}
    rows = {r["id"]: r for r in body["services"]}
    assert rows["sdwebui"]["reason"] == L.reason_for("sdwebui")
    assert rows["comfyui"]["reason"] == L.reason_for("comfyui")


def test_the_english_side_translates_the_reason(home, monkeypatch):
    monkeypatch.setattr(L, "default_fetch", _fetch((SD,)))
    body = client.get("/api/system/local-hint?role=image", headers={"X-StoryStudio-Lang": "en"}).json()
    reason = body["suggestion"]["reason"]
    assert reason and not any("一" <= ch <= "鿿" for ch in reason), reason
