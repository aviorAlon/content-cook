"""音色克隆与参考音频（DESIGN §33）：MiniMax 语音适配器（合成 / 上传 → 克隆 / 错误分类，替身 HTTP）、档案的参考音频与克隆
字段往返、路由的上传 / 克隆（后台任务内联跑）/ 流式读取 / 路径穿越、合成时挑克隆 id、旁白轨按 minimax 引擎分派。
全部不联网；MiniMax 那边按公开文档写，未真实调用。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import settings as S
from genkit import voices as V
from genkit.adapters import REGISTRY
from genkit.adapters import minimax_speech as M
from genkit.adapters.base import HttpResponse
from genkit.providers import ProviderCommandError, ProviderRateLimited, ProviderRejected, QuotaExhausted
from webui import deps
from webui.app import app

client = TestClient(app)
MP3 = b"ID3" + bytes(range(64)) * 8


class FakeHTTP:
    """按 URL 片段回放 JSON；记录每次请求（multipart 正文按 latin-1 解码进 seen，方便断言字段）。"""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.seen: list[tuple[str, str, dict, object]] = []

    def __call__(self, method, url, headers=None, json_body=None, data=None, timeout=0):
        body = json_body if json_body is not None else (data.decode("latin-1") if isinstance(data, bytes) else data)
        self.seen.append((method, url, dict(headers or {}), body))
        for needle, reply in self.routes.items():
            if needle in url:
                if callable(reply):
                    reply = reply(body)
                status, payload = reply if isinstance(reply, tuple) else (200, reply)
                return HttpResponse(status, json.dumps(payload).encode())
        raise AssertionError(f"no route for {url}")

    def urls(self, needle: str) -> list[str]:
        return [u for _, u, _, _ in self.seen if needle in u]


OK = {"base_resp": {"status_code": 0, "status_msg": "success"}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    return tmp_path / "home"


@pytest.fixture
def ref_audio(tmp_path):
    path = tmp_path / "ref.mp3"
    path.write_bytes(MP3)
    return path


def _adapter(routes: dict[str, object], tmp_path: Path, **config) -> tuple[M.MiniMaxSpeech, FakeHTTP]:
    http = FakeHTTP(routes)
    cfg = {"api_key": "k", "base_url": "https://api.minimax.chat/v1", "model": "speech-02-hd", "voice": "female-shaonv", **config}
    return M.MiniMaxSpeech(cfg, http=http, cache_dir=tmp_path / "cache"), http


# ---------------------------------------------------------------------------- 适配器


def test_registry_and_settings_table_list_minimax_speech():
    assert "minimax_speech" in REGISTRY["voice"] and "minimax_speech" in S.ADAPTERS["voice"]
    assert S.ADAPTERS["voice"]["minimax_speech"]["defaults"]["base_url"] == "https://api.minimax.chat/v1"


def test_synthesize_decodes_hex_audio_and_sends_voice_setting(tmp_path):
    adapter, http = _adapter({"/t2a_v2": {"data": {"audio": MP3.hex(), "status": 2}, **OK}}, tmp_path)
    out = adapter.synthesize("你好", tmp_path / "o.mp3", voice="ss_v1_abcdef12", rate="-10%")
    assert out.read_bytes() == MP3
    method, url, headers, body = http.seen[0]
    assert method == "POST" and url.endswith("/v1/t2a_v2") and headers["Authorization"] == "Bearer k"
    assert body["model"] == "speech-02-hd" and body["text"] == "你好" and body["stream"] is False
    assert body["voice_setting"] == {"voice_id": "ss_v1_abcdef12", "speed": 0.9}
    assert body["audio_setting"]["format"] == "mp3"
    # 不给音色用配置里的；GroupId 只在配了才带
    adapter.synthesize("再来", tmp_path / "o2.mp3")
    assert http.seen[1][3]["voice_setting"] == {"voice_id": "female-shaonv"}
    grouped, http2 = _adapter({"/t2a_v2": {"data": {"audio": MP3.hex()}, **OK}}, tmp_path, extra={"group_id": "g1"})
    grouped.synthesize("x", tmp_path / "o3.mp3")
    assert http2.seen[0][1].endswith("/t2a_v2?GroupId=g1")


def test_upload_then_clone_flow(tmp_path, ref_audio):
    routes = {
        "/files/upload": {"file": {"file_id": 123456, "bytes": len(MP3), "purpose": "voice_clone"}, **OK},
        "/voice_clone": {"input_sensitive": False, "input_sensitive_type": 0, "demo_audio": "https://mm.example/demo.mp3", **OK},
    }
    adapter, http = _adapter(routes, tmp_path)
    result = adapter.clone_voice(ref_audio, voice_id="ss_girl_a_1a2b3c4d", preview_text="试听一句。")
    assert result == {"voice_id": "ss_girl_a_1a2b3c4d", "file_id": "123456", "demo_audio": "https://mm.example/demo.mp3"}
    upload = next(s for s in http.seen if "/files/upload" in s[1])
    assert upload[2]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert 'name="purpose"\r\n\r\nvoice_clone' in upload[3] and 'filename="ref.mp3"' in upload[3]
    assert "local:" not in upload[3] and str(ref_audio) not in upload[3]
    clone = next(s for s in http.seen if "/voice_clone" in s[1])
    assert clone[3] == {"file_id": 123456, "voice_id": "ss_girl_a_1a2b3c4d", "text": "试听一句。", "model": "speech-02-hd"}
    # 客户端自选 id 的规则
    with pytest.raises(ProviderCommandError):
        adapter.clone_voice(ref_audio, voice_id="1short")
    assert M.clone_voice_id("girl.a-2", "0123456789abcdef") == "ss_girl_a_2_01234567"
    assert M.clone_voice_id("小雨", "abcdef0123") == "ss_voice_abcdef01"


def test_reference_validation_before_upload(tmp_path):
    adapter, http = _adapter({}, tmp_path)
    with pytest.raises(ProviderCommandError):
        adapter.upload_reference(tmp_path / "missing.mp3")
    bad = tmp_path / "ref.ogg"
    bad.write_bytes(MP3)
    with pytest.raises(ProviderCommandError):
        adapter.upload_reference(bad)
    assert http.seen == []


def test_error_classification(tmp_path, ref_audio):
    quota, _ = _adapter({"/t2a_v2": {"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}}, tmp_path)
    with pytest.raises(QuotaExhausted):
        quota.synthesize("x", tmp_path / "q.mp3")
    moderated, _ = _adapter({"/t2a_v2": {"base_resp": {"status_code": 1026, "status_msg": "input contains sensitive content"}}}, tmp_path)
    with pytest.raises(ProviderRejected):
        moderated.synthesize("x", tmp_path / "m.mp3")
    http429, _ = _adapter({"/t2a_v2": (429, {"error": "rate limited"})}, tmp_path)
    with pytest.raises(ProviderRateLimited):          # 限流：配额的子类（同样可重提），但提示是「等一会」不是「去充值」
        http429.synthesize("x", tmp_path / "r.mp3")
    limited, _ = _adapter({"/t2a_v2": {"base_resp": {"status_code": 1002, "status_msg": "rate limit"}}}, tmp_path)
    with pytest.raises(ProviderRateLimited):
        limited.synthesize("x", tmp_path / "l.mp3")
    flagged, _ = _adapter({"/files/upload": {"file": {"file_id": 1}, **OK}, "/voice_clone": {"input_sensitive": True, "input_sensitive_type": 2, **OK}}, tmp_path)
    with pytest.raises(ProviderRejected):
        flagged.clone_voice(ref_audio, voice_id="ss_x_12345678")
    other, _ = _adapter({"/t2a_v2": {"base_resp": {"status_code": 2013, "status_msg": "invalid params"}}}, tmp_path)
    with pytest.raises(ProviderCommandError) as info:
        other.synthesize("x", tmp_path / "o.mp3")
    assert "2013" in str(info.value)
    assert M.speed_from_rate("+50%") == 1.5 and M.speed_from_rate("-80%") == 0.5 and M.speed_from_rate("") is None and M.speed_from_rate("快") is None


# ---------------------------------------------------------------------------- 档案


def test_profile_round_trip_and_reset_on_new_reference(home, ref_audio, tmp_path):
    created = client.post("/api/voices", json={"id": "girl_a", "name": "小雨", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural"})
    assert created.status_code == 200 and "clone" not in created.json() and "reference_audio" not in created.json()
    # 绝对路径也行；扩展名、存在性、大小都查
    assert client.put("/api/voices/girl_a", json={"reference_audio": str(tmp_path / "nope.mp3")}).status_code == 400
    assert client.put("/api/voices/girl_a", json={"reference_audio": str(tmp_path / "x.ogg")}).status_code == 400
    ok = client.put("/api/voices/girl_a", json={"reference_audio": str(ref_audio)})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["reference_audio"] == str(ref_audio) and body["has_reference_audio"] is True
    assert body["clone"] == {"provider": "", "voice_id": "", "status": "none", "cloned_at": None, "error": ""}
    # 磁盘上没有克隆字段之外的东西改变；旧档案（无字段）照常读
    raw = json.loads((home / "voices.json").read_text(encoding="utf-8"))["voices"]["girl_a"]
    assert raw["clone"]["status"] == "none" and raw["reference_audio"] == str(ref_audio)
    # 只有参考音频、没有 TTS 音色和描述也能存（克隆之前的状态）
    assert client.post("/api/voices", json={"id": "ref_only", "name": "只有参考", "reference_audio": str(ref_audio)}).status_code == 200
    # 清空参考音频 → 两个字段一起消失
    cleared = client.put("/api/voices/girl_a", json={"reference_audio": ""}).json()
    assert "reference_audio" not in cleared and "clone" not in cleared
    # 大文件
    big = tmp_path / "big.wav"
    big.write_bytes(b"\0" * (M.MAX_REFERENCE_BYTES + 1))
    assert client.put("/api/voices/girl_a", json={"reference_audio": str(big)}).status_code == 400


def test_clone_voice_writes_provider_id_and_synth_uses_it(home, ref_audio, tmp_path, monkeypatch):
    client.post("/api/voices", json={"id": "girl_a", "name": "小雨", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "sample_text": "试听。"})
    client.put("/api/voices/girl_a", json={"reference_audio": str(ref_audio)})
    routes = {"/files/upload": {"file": {"file_id": 7}, **OK}, "/voice_clone": {"demo_audio": "", **OK},
              "/t2a_v2": {"data": {"audio": MP3.hex()}, **OK}}
    adapter, http = _adapter(routes, tmp_path)
    clone = V.clone_voice(V.get_profile("girl_a"), adapter=adapter)
    expected_id = M.clone_voice_id("girl_a", M.reference_digest(ref_audio))
    assert clone["status"] == "ready" and clone["provider"] == "minimax" and clone["voice_id"] == expected_id and clone["cloned_at"]
    profile = V.get_profile("girl_a")
    assert profile["engine"] == "minimax" and profile["voice"] == expected_id and profile["clone"]["voice_id"] == expected_id
    assert next(s for s in http.seen if "/voice_clone" in s[1])[3]["text"] == "试听。"
    # 合成走 minimax 引擎并挑克隆 id（哪怕 voice 被手改过）
    monkeypatch.setattr(V, "build_minimax_speech", lambda settings=None: adapter)
    V.synthesize_with_profile({**profile, "voice": "hand-edited"}, "你好", tmp_path / "s.mp3")
    assert http.seen[-1][3]["voice_setting"]["voice_id"] == expected_id
    # 绑给角色 / 旁白拷的是克隆 id
    member = V.bind_cast({"name": "林"}, profile)
    assert member["voice_tts"]["engine"] == "minimax" and member["voice_tts"]["voice"] == expected_id
    vo = V.bind_voice_over({}, profile)
    assert vo["engine"] == "minimax" and vo["edge"]["voice"] == expected_id
    # 换参考音频 → 克隆归零，再合成就报「还没克隆」
    other = tmp_path / "other.wav"
    other.write_bytes(MP3 + b"x")
    reset = client.put("/api/voices/girl_a", json={"reference_audio": str(other), "voice": ""}).json()
    assert reset["clone"]["status"] == "none"
    with pytest.raises(V.VoiceError):
        V.synthesize_with_profile(reset, "x", tmp_path / "n.mp3")


def test_clone_failure_is_recorded_on_profile(home, ref_audio, tmp_path):
    client.post("/api/voices", json={"id": "v", "name": "v", "reference_audio": str(ref_audio)})
    adapter, _ = _adapter({"/files/upload": {"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}}, tmp_path)
    with pytest.raises(QuotaExhausted):
        V.clone_voice(V.get_profile("v"), adapter=adapter)
    clone = V.get_profile("v")["clone"]
    assert clone["status"] == "failed" and "1008" in clone["error"] and clone["voice_id"].startswith("ss_v_")
    with pytest.raises(V.VoiceError):
        V.clone_voice({"id": "v", "reference_audio": ""}, adapter=adapter)


def test_minimax_config_falls_back_to_video_key(home):
    assert V.minimax_speech_config() is None and V.available_engines() == ["edge", "settings"]
    S.update_settings({"roles": {"video": {"adapter": "minimax_video", "api_key": "mm-key"}}})
    cfg = V.minimax_speech_config()
    assert cfg["adapter"] == "minimax_speech" and cfg["api_key"] == "mm-key" and cfg["model"] == "speech-02-hd" and cfg["base_url"] == "https://api.minimax.chat/v1"
    assert V.available_engines() == ["edge", "settings", "minimax"]
    S.update_settings({"roles": {"voice": {"adapter": "minimax_speech", "api_key": "voice-key", "model": "speech-02-turbo"}}})
    assert V.minimax_speech_config()["api_key"] == "voice-key" and V.minimax_speech_config()["model"] == "speech-02-turbo"


# ---------------------------------------------------------------------------- 路由


@pytest.fixture
def inline_tasks(monkeypatch):
    from webui import tasks

    seen: dict[str, object] = {}

    def fake_start(kind, episode_id, fn, *, params=None):
        seen["kind"], seen["episode_id"] = kind, episode_id
        try:
            seen["result"] = fn()
        except Exception as exc:  # noqa: BLE001
            seen["error"] = str(exc)
        return "t1"

    monkeypatch.setattr(tasks, "start", fake_start)
    return seen


def _wav_bytes() -> bytes:
    """一段真的 WAV（1 秒静音）：参考音频上传按内容认（有 ffprobe 就让它读），只有文件头的假字节过不了。"""

    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 8000)
    return buffer.getvalue()


def test_router_upload_stream_traversal_and_clone(home, tmp_path, monkeypatch, inline_tasks):
    client.post("/api/voices", json={"id": "girl_a", "name": "小雨", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural"})
    wav = _wav_bytes()
    b64 = base64.b64encode(wav).decode()
    assert client.post("/api/voices/nope/reference-audio", json={"filename": "a.mp3", "data_base64": b64}).status_code == 404
    junk = base64.b64encode(b"definitely not audio").decode()
    assert client.post("/api/voices/girl_a/reference-audio", json={"filename": "a.mp3", "data_base64": junk}).status_code == 400
    assert client.post("/api/voices/girl_a/reference-audio", json={"filename": "a.mp3", "data_base64": "***"}).status_code == 400
    assert client.post("/api/voices/girl_a/reference-audio", json={"filename": "a.mp3", "data_base64": ""}).status_code == 400
    resp = client.post("/api/voices/girl_a/reference-audio", json={"filename": "我的声音.WAV", "data_base64": b64})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reference_audio"].startswith("reference/girl_a-") and body["reference_audio"].endswith(".wav")
    assert body["has_reference_audio"] is True and body["clone"]["status"] == "none" and body["bytes"] == len(wav)
    stored = V.reference_dir() / Path(body["reference_audio"]).name
    assert stored.read_bytes() == wav
    # 流式读取
    got = client.get("/api/voices/girl_a/reference-audio")
    assert got.status_code == 200 and got.content == wav and got.headers["content-type"].startswith("audio/wav")
    # 再传一份 → 新文件名（不覆盖）、旧文件尽力清理
    again = client.post("/api/voices/girl_a/reference-audio", json={"filename": "b.mp3", "data_base64": b64}).json()
    assert again["reference_audio"] != body["reference_audio"]
    assert not stored.exists() and (V.reference_dir() / Path(again["reference_audio"]).name).is_file()
    # 路径穿越：档案里指向目录外的非音频文件读不到；id 不合法直接 404
    assert client.get("/api/voices/../settings/reference-audio").status_code in (404, 405)
    assert client.get("/api/voices/nope/reference-audio").status_code == 404
    client.post("/api/voices", json={"id": "plain", "name": "p", "engine": "edge", "voice": "v"})
    assert client.get("/api/voices/plain/reference-audio").status_code == 404
    # 克隆：没配密钥 → 400 说清楚；配了 → 后台任务（这里内联跑）写回档案
    assert client.post("/api/voices/plain/clone").status_code == 400
    no_key = client.post("/api/voices/girl_a/clone")
    assert no_key.status_code == 400 and "MiniMax" in no_key.json()["detail"]
    S.update_settings({"roles": {"voice": {"adapter": "minimax_speech", "api_key": "k"}}})
    adapter, http = _adapter({"/files/upload": {"file": {"file_id": 9}, **OK}, "/voice_clone": {"demo_audio": "", **OK}}, tmp_path)
    monkeypatch.setattr(V, "build_minimax_speech", lambda settings=None: adapter)
    started = client.post("/api/voices/girl_a/clone")
    assert started.status_code == 200, started.text
    assert started.json()["task_id"] == "t1" and inline_tasks["kind"] == "voice-clone" and inline_tasks["episode_id"] == "voice:girl_a"
    profile = next(v for v in client.get("/api/voices").json() if v["id"] == "girl_a")
    assert profile["clone"]["status"] == "ready" and profile["engine"] == "minimax" and profile["voice"] == profile["clone"]["voice_id"]
    assert 'filename="girl_a-' in next(s for s in http.seen if "/files/upload" in s[1])[3]
    # 目录接口按密钥列出 minimax 引擎与系统音色
    catalog = client.get("/api/voices/catalog?engine=minimax").json()
    assert catalog["engines"] == ["edge", "settings", "minimax"] and catalog["adapter"] == "minimax_speech"
    assert any(v["voice"] == "female-shaonv" for v in catalog["voices"])
    monkeypatch.setattr(V, "edge_catalog", lambda prefixes=("zh-", "en-"): [])
    assert client.get("/api/voices/catalog?engine=edge").json()["engines"] == ["edge", "settings", "minimax"]


def test_router_clone_failure_humanized_in_profile(home, ref_audio, tmp_path, monkeypatch, inline_tasks):
    client.post("/api/voices", json={"id": "v", "name": "v", "reference_audio": str(ref_audio)})
    S.update_settings({"roles": {"video": {"adapter": "minimax_video", "api_key": "k"}}})
    adapter, _ = _adapter({"/files/upload": {"base_resp": {"status_code": 1026, "status_msg": "sensitive"}}}, tmp_path)
    monkeypatch.setattr(V, "build_minimax_speech", lambda settings=None: adapter)
    assert client.post("/api/voices/v/clone").status_code == 200
    assert inline_tasks["error"].startswith("克隆失败：")
    clone = client.get("/api/voices").json()[0]["clone"]
    assert clone["status"] == "failed" and clone["error"].startswith("克隆失败：") and "1026" in clone["error"]
    # 没配密钥的目录：空表 + 说明，不是 502
    S.update_settings({"roles": {"video": {"adapter": "", "api_key": ""}}})
    body = client.get("/api/voices/catalog?engine=minimax").json()
    assert body["voices"] == [] and "MiniMax" in body["note"] and body["engines"] == ["edge", "settings"]


def test_narration_track_routes_minimax_engine(tmp_path, monkeypatch):
    from storyvideo import post_layers

    calls = []
    monkeypatch.setattr(V, "synthesize_with_profile", lambda profile, text, destination: (calls.append((profile, text)), destination.write_bytes(MP3)))
    monkeypatch.setattr(post_layers, "probe_media", lambda path, ffprobe=None: type("I", (), {"duration_seconds": 1.0})())
    spec = {"voice_over": {"engine": "minimax", "edge": {"voice": "ss_narr_12345678", "rate": "-5%"}, "lines": [{"shot": "S1", "at": 0, "zh": "凌晨一点。"}]}}
    rows = post_layers.render_vo_lines(spec, tmp_path / "vo")
    assert len(rows) == 1 and calls == [({"engine": "minimax", "voice": "ss_narr_12345678", "rate": "-5%"}, "凌晨一点。")]


# ---------------------------------------------------------------------------- 视频模型的参考音频（spec / 指纹 / 运行时）


def test_video_reference_audio_in_spec_and_request_data(tmp_path):
    from storyvideo.prompts import video_request_data
    from storyvideo.spec import SpecError, normalize_spec

    base = {
        "id": "RA-T", "output_root": "output/story_video/RA-T", "prompt_language": "zh", "frame_style": "x", "scene_anchor": "y",
        "camera": "z", "neg": "n", "subtitle": {"language": "zh"}, "dialogue_prompt": "auto",
        "cast": {"lin": {"name": "林", "block": "林：黑发。"}},
        "shots": [{"id": "S1", "dur": 4, "cast": ["lin"], "shot": "中景", "art": "a", "motion": "m", "lines": []}],
    }
    plain = normalize_spec(dict(base), project_root=tmp_path)
    assert "reference_audio" not in video_request_data(plain, "S1")["extra_params"]
    with_audio = normalize_spec({**base, "video_params": {"reference_audio": "content/assets/RA-T/ref.mp3"}}, project_root=tmp_path)
    assert video_request_data(with_audio, "S1")["extra_params"]["reference_audio"] == "content/assets/RA-T/ref.mp3"
    for bad in ({"reference_audio": 3}, {"reference_audio": "x.ogg"}):
        with pytest.raises(SpecError):
            normalize_spec({**base, "video_params": bad}, project_root=tmp_path)
    emptied = normalize_spec({**base, "video_params": {"reference_audio": ""}}, project_root=tmp_path)
    assert "reference_audio" not in emptied["video_params"]


def test_runtime_resolves_reference_audio_to_asset_token(tmp_path, fake_media):
    from storyvideo.runtime import StoryVideoError, StoryVideoRunner
    from tests.test_storyvideo_runtime import FakeProvider, write_episode

    path = write_episode(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["video_params"]["reference_audio"] = "content/assets/EP99/voice_ref.mp3"
    path.write_text(json.dumps(value), encoding="utf-8")
    provider = FakeProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.run("EP99", through="frames")
        runner.review("EP99", "frames", "pass")
        # 文件不在 → 提交前就停，不会带着坏路径发出去
        with pytest.raises(StoryVideoError) as info:
            runner.run("EP99", through="videos")
        assert "reference audio does not exist" in str(info.value)
        audio = tmp_path / "content" / "assets" / "EP99" / "voice_ref.mp3"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(MP3)
        runner.run("EP99", through="videos")
    video = provider.submissions[1]
    assert video.extra_params["reference_audio"] == "upload-voice_ref"
    assert audio in provider.uploads
    assert all(r.type == "first_frame" for r in video.references)


from tests.test_storyvideo_runtime import fake_media  # noqa: E402,F401 - 复用运行时测试的假 ffprobe
