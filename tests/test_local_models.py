"""本地模型（DESIGN §45，契约 docs/local-models-spec.md）：预设与地址判定、扫描、模型列表解析、
一键应用到角色、空密钥放行、不花钱的文案、成本闸不拦本地、四个接口。

一行网络请求都不发：探活走注入的假 fetch，接口测试把 ``genkit.local.default_fetch`` 换掉。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from genkit import capabilities as C
from genkit import local as L
from genkit import settings as S
from storyvideo import poster as P
from storyvideo import scriptgen
from webui.app import app

client = TestClient(app)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    for role in ("TEXT", "IMAGE", "VOICE", "VIDEO", "MUSIC"):
        for key in ("ADAPTER", "BASE_URL", "API_KEY", "MODEL", "VOICE"):
            monkeypatch.delenv(f"STORYSTUDIO_{role}_{key}", raising=False)
    return tmp_path / "home"


def _settings(role: str, adapter: str, base_url: str, *, api_key: str = "", model: str = "m") -> S.Settings:
    st = S.Settings()
    cfg = st.role(role)
    cfg.adapter, cfg.base_url, cfg.api_key, cfg.model = adapter, base_url, api_key, model
    return st


# ---------------------------------------------------------------------------- 预设


def test_presets_cover_the_seven_services_and_are_all_local():
    assert [s.id for s in L.PRESETS] == [
        "ollama", "lmstudio", "vllm", "llamacpp", "comfyui", "sdwebui", "openai_speech_local"]
    for svc in L.PRESETS:
        assert svc.name and svc.doc and svc.kind in (L.KIND_OLLAMA, L.KIND_OPENAI, L.KIND_COMFYUI, L.KIND_SDWEBUI)
        assert svc.roles and set(svc.roles) <= set(S.ROLES)
        assert set(svc.adapter) == set(svc.roles)          # 每个角色都说清楚填哪个适配器
        assert svc.needs_key is False
        # 预设的地址一律是本机；探活地址也一样（扫描永远不会打到公网）
        assert L.is_local_url(svc.base_url) and L.is_local_url(L.probe_url(svc)) and L.is_local_url(L.models_url(svc))
    assert L.PRESETS_BY_ID["comfyui"].roles == ("image", "video")
    assert L.PRESETS_BY_ID["ollama"].base_url == "http://127.0.0.1:11434/v1"
    assert L.probe_url(L.PRESETS_BY_ID["ollama"]) == "http://127.0.0.1:11434/api/tags"
    assert L.models_url(L.PRESETS_BY_ID["comfyui"]) == "http://127.0.0.1:8188/object_info/CheckpointLoaderSimple"


def test_preset_probe_follows_a_user_supplied_address():
    svc = L.PRESETS_BY_ID["comfyui"]
    assert L.probe_url(svc, "http://192.168.1.9:9188") == "http://192.168.1.9:9188/system_stats"
    assert L.models_url(svc, "http://192.168.1.9:9188") == "http://192.168.1.9:9188/object_info/CheckpointLoaderSimple"
    with pytest.raises(ValueError):
        L.preset("nope")


# ---------------------------------------------------------------------------- 地址判定


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434/v1", "http://127.1.2.3:8188", "http://localhost:7860", "https://localhost/v1",
    "http://[::1]:8188", "http://10.1.2.3:8000/v1", "http://172.16.5.5", "http://192.168.1.9:7860",
    "http://169.254.1.1", "127.0.0.1:11434", "http://LOCALHOST:1234/v1",
])
def test_is_local_url_accepts(url):
    assert L.is_local_url(url) is True


@pytest.mark.parametrize("url", [
    "", "   ", "https://api.openai.com/v1", "http://8.8.8.8", "http://172.32.0.1", "http://11.0.0.1",
    # 「长得像本机」的公网地址：主机名不是 IP、也不是 localhost
    "http://127.0.0.1.evil.com", "http://localhost.evil.com", "http://127.0.0.1@evil.com",
    "http://evil.com#127.0.0.1", "http://evil.com/127.0.0.1",
    # 协议不对 / 未指定地址 / 组播
    "ftp://127.0.0.1", "file:///etc/passwd", "http://0.0.0.0:8188", "http://224.0.0.1",
])
def test_is_local_url_rejects(url):
    assert L.is_local_url(url) is False


# ---------------------------------------------------------------------------- 模型列表解析


def test_parse_models_handles_the_four_shapes():
    assert L.parse_models(L.KIND_OLLAMA, {"models": [{"name": "qwen2.5:7b"}, {"model": "llama3:8b"}, {"name": "qwen2.5:7b"}]}) == [
        "qwen2.5:7b", "llama3:8b"]
    assert L.parse_models(L.KIND_OPENAI, {"data": [{"id": "gpt-oss"}, {"id": "qwen"}]}) == ["gpt-oss", "qwen"]
    assert L.parse_models(L.KIND_SDWEBUI, [{"title": "sd_xl [abc]", "model_name": "sd_xl"}, {"title": "only_title"}]) == [
        "sd_xl", "only_title"]
    assert L.parse_models(L.KIND_COMFYUI, {
        "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["a.safetensors", "b.safetensors"], {}]}}}}) == [
        "a.safetensors", "b.safetensors"]
    # 形状不对 = 列不出模型，但不抛（探活成功仍然算探到了）
    for kind in (L.KIND_OLLAMA, L.KIND_OPENAI, L.KIND_SDWEBUI, L.KIND_COMFYUI):
        assert L.parse_models(kind, "nonsense") == [] and L.parse_models(kind, {}) == []


def test_models_for_fetches_and_refuses_public_addresses():
    seen = []

    def fake(url, timeout):
        seen.append(url)
        return {"data": [{"id": "kokoro"}]}

    svc = L.PRESETS_BY_ID["openai_speech_local"]
    assert L.models_for(svc, fetch=fake) == ["kokoro"]
    assert L.models_for("openai_speech_local", "http://192.168.0.5:8880/v1", fetch=fake) == ["kokoro"]
    assert seen == ["http://127.0.0.1:8880/v1/models", "http://192.168.0.5:8880/v1/models"]
    with pytest.raises(ValueError, match="只能扫本机地址"):
        L.models_for(svc, "https://api.openai.com/v1", fetch=fake)


# ---------------------------------------------------------------------------- 扫描


def _fake_fetch(table):
    """table: url → 返回值（或抛出来的异常）。表里没有的 url 一律当「连不上」。"""

    def fetch(url, timeout):
        value = table.get(url)
        if value is None:
            raise OSError("connection refused")
        if isinstance(value, Exception):
            raise value
        return value

    return fetch


def test_scan_reports_alive_dead_timeout_and_partial_failures():
    fetch = _fake_fetch({
        "http://127.0.0.1:11434/api/tags": {"models": [{"name": "qwen2.5:7b"}]},
        "http://127.0.0.1:7860/sdapi/v1/sd-models": [{"model_name": "sd_xl"}],
        "http://127.0.0.1:1234/v1/models": TimeoutError("timed out"),
        # ComfyUI 活着，但取模型列表那一下挂了
        "http://127.0.0.1:8188/system_stats": {"system": {}},
        "http://127.0.0.1:8188/object_info/CheckpointLoaderSimple": OSError("boom"),
    })
    rows = {r["id"]: r for r in L.scan(fetch=fetch, timeout=0.1)}
    assert set(rows) == set(L.PRESETS_BY_ID)
    assert rows["ollama"]["alive"] is True and rows["ollama"]["models"] == ["qwen2.5:7b"] and not rows["ollama"]["error"]
    assert rows["ollama"]["roles"] == ["text"] and rows["ollama"]["base_url"] == "http://127.0.0.1:11434/v1"
    assert rows["sdwebui"]["alive"] is True and rows["sdwebui"]["models"] == ["sd_xl"]
    assert rows["vllm"]["alive"] is False and "OSError" in rows["vllm"]["error"]
    assert rows["lmstudio"]["alive"] is False and "TimeoutError" in rows["lmstudio"]["error"]
    # 活着但列不出模型：仍然 alive，错误里说清楚是取列表失败
    assert rows["comfyui"]["alive"] is True and rows["comfyui"]["models"] == []
    assert rows["comfyui"]["error"].startswith("探到了，但取模型列表失败：")


def test_scan_can_target_a_few_services_and_a_manual_address():
    seen = []

    def fetch(url, timeout):
        seen.append(url)
        return {"models": [{"name": "qwen"}]}

    rows = L.scan(["ollama"], fetch=fetch, base_urls={"ollama": "http://192.168.1.9:11434/v1"})
    assert len(rows) == 1 and rows[0]["base_url"] == "http://192.168.1.9:11434/v1" and rows[0]["alive"]
    assert seen == ["http://192.168.1.9:11434/api/tags"]
    with pytest.raises(ValueError):
        L.scan(["nope"], fetch=fetch)


def test_scan_never_touches_a_public_address():
    def fetch(url, timeout):        # 一次都不该被调到
        raise AssertionError(f"不该请求 {url}")

    with pytest.raises(ValueError, match="只能扫本机地址"):
        L.scan(["comfyui"], fetch=fetch, base_urls={"comfyui": "https://evil.example.com"})


# ---------------------------------------------------------------------------- 应用到角色


def test_apply_to_role_writes_adapter_address_model_and_clears_the_key():
    st = _settings("text", "openai_chat", "https://api.openai.com/v1", api_key="sk-remote", model="gpt-4o")
    L.apply_to_role(st, "ollama", "text", "qwen2.5:7b")
    cfg = st.role("text")
    assert (cfg.adapter, cfg.base_url, cfg.api_key, cfg.model) == ("openai_chat", "http://127.0.0.1:11434/v1", "", "qwen2.5:7b")
    assert cfg.local is True and S.role_is_local(st, "text") is True
    # 多角色服务：两个角色各写各的；地址可以手填（只要是本机）
    L.apply_to_role(st, "comfyui", "image", "sd_xl.safetensors", "http://192.168.1.9:8188/")
    L.apply_to_role(st, "comfyui", "video", "wan.safetensors")
    assert st.role("image").adapter == "comfyui" and st.role("image").base_url == "http://192.168.1.9:8188"
    assert st.role("video").adapter == "comfyui" and st.role("video").model == "wan.safetensors"
    # 不留模型就不动原来的模型名
    st.role("voice").model = "keep-me"
    L.apply_to_role(st, "openai_speech_local", "voice")
    assert st.role("voice").adapter == "openai_speech" and st.role("voice").model == "keep-me"


def test_apply_to_role_refuses_wrong_role_unknown_service_and_public_address():
    st = S.Settings()
    with pytest.raises(ValueError, match="不能配"):
        L.apply_to_role(st, "sdwebui", "video", "x")
    with pytest.raises(ValueError, match="不认识的本地服务"):
        L.apply_to_role(st, "nope", "image", "x")
    with pytest.raises(ValueError, match="未知角色"):
        L.apply_to_role(st, "comfyui", "nope", "x")
    with pytest.raises(ValueError, match="只能应用本机地址"):
        L.apply_to_role(st, "comfyui", "image", "x", "https://api.example.com")
    assert st.role("image").adapter == ""       # 失败不留半成品


# ---------------------------------------------------------------------------- 不要 key


def test_empty_api_key_passes_for_local_and_still_fails_for_remote():
    local = _settings("text", "openai_chat", "http://127.0.0.1:11434/v1", model="qwen")
    assert S.validate_role(local, "text") == [] and local.role("text").configured
    remote = _settings("text", "openai_chat", "https://api.openai.com/v1", model="gpt-4o")
    assert S.validate_role(remote, "text") == ["text.api_key"]
    # 别的字段照样必填：本地也要有模型
    no_model = _settings("text", "openai_chat", "http://127.0.0.1:11434/v1", model="")
    assert S.validate_role(no_model, "text") == ["text.model"]
    # 地址留空 + 适配器默认地址是本机（sdwebui / comfyui）也算本地
    default_local = S.Settings()
    default_local.role("image").adapter = "sdwebui"
    default_local.role("image").model = "sd_xl"
    assert S.role_is_local(default_local, "image") is True
    # 「长得像本机」的公网地址不放行
    fake_local = _settings("text", "openai_chat", "http://127.0.0.1.evil.com/v1", model="x")
    assert S.validate_role(fake_local, "text") == ["text.api_key"]


def test_api_key_placeholder_is_explicit_for_local_only():
    assert L.api_key_for("http://127.0.0.1:11434/v1", "") == L.PLACEHOLDER_API_KEY == "local"
    assert L.api_key_for("http://127.0.0.1:11434/v1", "sk-real") == "sk-real"
    assert L.api_key_for("https://api.openai.com/v1", "") == "missing"


def test_settings_api_view_exposes_the_derived_local_flag(home):
    client.put("/api/settings", json={"roles": {"text": {"adapter": "openai_chat", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen"}}})
    doc = client.get("/api/settings").json()
    assert doc["settings"]["roles"]["text"]["local"] is True and doc["status"]["text"] == []
    assert doc["settings"]["roles"]["image"]["local"] is False
    # 写盘那一份不带推导字段
    saved = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert "local" not in saved["roles"]["text"]


def test_local_roles_are_marked_free_in_the_capability_table():
    for role, adapter in (("image", "sdwebui"), ("image", "comfyui"), ("video", "comfyui")):
        caps = C.lookup(role, adapter, "x", use_catalog=False)
        assert caps["local"] is True and caps["cost"] == "free" and caps["source"] == "doc"
        assert caps.get("aspect_ratios")
    # 本地语音那条没有自己的适配器 id，靠地址推导
    speech = C.lookup("voice", "openai_speech", "kokoro", use_catalog=False, base_url="http://127.0.0.1:8880/v1")
    assert speech["local"] is True and speech["cost"] == "free"
    assert "local" not in C.lookup("voice", "openai_speech", "tts-1", use_catalog=False, base_url="https://api.openai.com/v1")
    assert C.is_local("sdwebui") and not C.is_local("openai_images", "https://api.openai.com/v1")


# ---------------------------------------------------------------------------- 不花钱


def test_cost_notes_switch_to_the_free_line_for_local_sources(home):
    assert L.free_cost_note(3) == "本地生成，不花钱（3 次请求）"
    # 远端：各来源保留自己那句
    client.put("/api/settings", json={"roles": {"image": {"adapter": "openai_images", "base_url": "https://api.openai.com/v1",
                                                          "api_key": "sk-x", "model": "gpt-image-1"}}})
    assert L.cost_note_for("image", 4, "最多 4 次生图请求") == "最多 4 次生图请求"
    assert P.cost_note("gpt-image-1") == "1 次生图请求（gpt-image-1）"
    # 本地：一律换成不花钱那一句
    client.put("/api/settings", json={"roles": {"image": {"adapter": "sdwebui", "base_url": "http://127.0.0.1:7860", "model": "sd_xl"}}})
    assert L.cost_note_for("image", 4, "最多 4 次生图请求") == "本地生成，不花钱（4 次请求）"
    assert P.cost_note("sd_xl") == "本地生成，不花钱（1 次请求）"
    assert L.role_local("image") is True and L.role_local("video") is False


def test_estimate_plan_says_local_when_the_role_runs_on_this_machine(home, tmp_path, monkeypatch):
    from storyvideo.spec import normalize_spec
    from webui.generate import estimate_plan

    (tmp_path / "content" / "story_video").mkdir(parents=True)
    raw = {"id": "EP-L", "output_root": "output/story_video/EP-L", "cast": {"su": {"block": "su"}},
           "shots": [{"id": "S1", "dur": 5, "cast": ["su"], "shot": "wide", "art": "a room", "motion": "su waves"}],
           "subtitle": {"language": "zh"}}
    path = tmp_path / "content" / "story_video" / "EP-L.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    spec = normalize_spec(raw, spec_path=str(path), project_root=str(tmp_path))

    client.put("/api/settings", json={"roles": {"image": {"adapter": "openai_images", "base_url": "https://api.openai.com/v1",
                                                          "api_key": "sk-x", "model": "gpt-image-1"}}})
    remote = estimate_plan(spec, through="frames", only=None)
    assert remote["local"] is False and remote["cost_note"] is None and remote["pending_count"] == 1

    client.put("/api/settings", json={"roles": {"image": {"adapter": "comfyui", "base_url": "http://127.0.0.1:8188", "model": "sd_xl"}}})
    local = estimate_plan(spec, through="frames", only=None)
    assert local["local"] is True and local["cost_note"] == "本地生成，不花钱（1 次请求）"
    # 成片装配本来就是本机 ffmpeg
    assert estimate_plan(spec, through="final", only=None)["local"] is True


def test_scriptgen_budget_gate_is_skipped_for_local_text_models(home, monkeypatch):
    monkeypatch.delenv("SCRIPTGEN_MAX_BUDGET_USD", raising=False)
    monkeypatch.setattr(scriptgen, "_read_dotenv", lambda path: {})
    client.put("/api/settings", json={"roles": {"text": {"adapter": "openai_chat", "base_url": "https://api.openai.com/v1",
                                                         "api_key": "sk-x", "model": "gpt-4o"}}})
    assert scriptgen.text_role_is_local() is False
    assert scriptgen.scriptgen_max_budget_usd() == scriptgen.DEFAULT_MAX_BUDGET_USD
    client.put("/api/settings", json={"roles": {"text": {"adapter": "openai_chat", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen"}}})
    assert scriptgen.text_role_is_local() is True
    assert scriptgen.scriptgen_max_budget_usd() == ""        # 空 = 不设上限，本地不花钱


# ---------------------------------------------------------------------------- 接口


@pytest.fixture
def fake_probe(monkeypatch):
    """接口测试一律走假 fetch：不碰真网络，也不依赖本机有没有装这些服务。"""

    calls: list[str] = []

    def fetch(url, timeout):
        calls.append(url)
        if url.startswith("http://127.0.0.1:11434"):
            return {"models": [{"name": "qwen2.5:7b"}, {"name": "llama3:8b"}]}
        raise OSError("connection refused")

    monkeypatch.setattr(L, "default_fetch", fetch)
    return calls


def test_local_scan_endpoint(home, fake_probe):
    body = client.get("/api/system/local-scan?timeout=0.1").json()
    rows = {r["id"]: r for r in body["services"]}
    assert body["alive"] == ["ollama"] and rows["ollama"]["models"] == ["qwen2.5:7b", "llama3:8b"]
    assert rows["sdwebui"]["alive"] is False
    assert [p["id"] for p in body["presets"]] == [s.id for s in L.PRESETS] and body["roles"] == list(S.ROLES)
    # 只扫某几个
    only = client.get("/api/system/local-scan?ids=ollama,sdwebui&timeout=0.1").json()
    assert [r["id"] for r in only["services"]] == ["ollama", "sdwebui"]
    assert client.get("/api/system/local-scan?ids=nope").status_code == 400


def test_local_scan_endpoint_refuses_public_addresses(home, fake_probe):
    r = client.get("/api/system/local-scan?ids=comfyui&base_url=https://evil.example.com")
    assert r.status_code == 400 and "只能扫本机地址" in r.json()["detail"]
    assert client.get("/api/system/local-scan?base_url=http://127.0.0.1:9000").status_code == 400   # 手填地址要说清楚是哪个服务
    assert not [u for u in fake_probe if "evil" in u]
    ok = client.get("/api/system/local-scan?ids=ollama&base_url=http://192.168.1.9:11434/v1&timeout=0.1")
    assert ok.status_code == 200 and ok.json()["services"][0]["base_url"] == "http://192.168.1.9:11434/v1"


def test_local_apply_endpoint(home, fake_probe):
    r = client.post("/api/settings/local/apply", json={"service": "ollama", "role": "text", "model": "qwen2.5:7b"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == ["text"] and r.json()["status"]["text"] == []
    role = r.json()["settings"]["roles"]["text"]
    assert role["adapter"] == "openai_chat" and role["base_url"] == "http://127.0.0.1:11434/v1"
    assert role["api_key"] == "" and role["local"] is True
    saved = S.load_settings()
    assert saved.role("text").model == "qwen2.5:7b" and saved.role("text").api_key == ""
    # 落盘之后角色就算「配好了」——没有 key 也是
    assert client.get("/api/health").json()["roles"]["text"]["configured"] is True


def test_local_apply_endpoint_rejects_public_address_and_wrong_role(home):
    bad = client.post("/api/settings/local/apply", json={"service": "comfyui", "role": "image", "model": "x",
                                                         "base_url": "https://evil.example.com"})
    assert bad.status_code == 400 and "只能应用本机地址" in bad.json()["detail"]
    assert client.post("/api/settings/local/apply", json={"service": "sdwebui", "role": "video"}).status_code == 400
    assert client.post("/api/settings/local/apply", json={"service": "nope", "role": "image"}).status_code == 400
    assert S.load_settings().role("image").adapter == ""


def test_local_workflow_endpoints():
    body = client.get("/api/system/local-workflows").json()
    assert isinstance(body["workflows"], list) and body["user_dir"].endswith("local_workflows")
    for row in body["workflows"]:
        assert row["name"] and row["source"] in ("builtin", "user") and isinstance(row["missing"], list)
    if body["workflows"]:
        name = body["workflows"][0]["name"]
        checked = client.post("/api/system/local-workflows/check", json={"name": name}).json()
        assert checked["name"] == name and isinstance(checked["missing"], list) and "ok" in checked
    assert client.post("/api/system/local-workflows/check", json={"name": " "}).status_code == 400


def test_local_workflow_endpoints_degrade_when_the_comfyui_adapter_is_missing(monkeypatch):
    from webui.routers import local_models

    monkeypatch.setattr(local_models, "_comfyui", lambda: None)
    body = client.get("/api/system/local-workflows").json()
    assert body["workflows"] == [] and body["note"] == local_models.COMFY_MISSING
    r = client.post("/api/system/local-workflows/check", json={"name": "txt2img_sdxl"})
    assert r.status_code == 425 and "ComfyUI 适配器还没装上" in r.json()["detail"]
