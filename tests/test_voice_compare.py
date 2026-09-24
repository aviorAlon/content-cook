"""音色对比角色（DESIGN §29）：同一句台词、多个候选音色并排试听；默认取角色第一句台词，一个失败不拖累整批，
文件落在项目 09_voice_compare/<角色>/ 下永不覆盖，清单接口与路径穿越拦截，数量上下限。合成器注入，不联网。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import voices as V
from genkit.providers import ProviderError
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router
from webui.routers import voices as voices_router

client = TestClient(app)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    return tmp_path / "home"


@pytest.fixture
def project(tmp_path, monkeypatch, home):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    spec = {
        "id": "VC-T", "output_root": "output/story_video/VC-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "bilingual"}, "dialogue_prompt": "auto",
        "cast": {
            "lin": {"name": "林小满", "voice": "原来的声音描述", "block": "林小满：黑发。"},
            "mute": {"name": "路人", "voice": "低沉", "block": "路人：灰衣。"},
        },
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["lin", "mute"], "shot": "中景", "art": "a", "motion": "m",
             "lines": [{"who": "lin", "zh": "你怎么才来？", "en": "Why are you so late?"}]},
            {"id": "S2", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m",
             "lines": [{"who": "lin", "zh": "第二句不该被选中。", "en": "Second line."}]},
        ],
    }
    (content_dir / "VC-T.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    for vid in ("a", "b", "c"):
        client.post("/api/voices", json={"id": vid, "name": f"音色{vid}", "engine": "edge", "voice": f"zh-CN-{vid}Neural"})
    client.post("/api/voices", json={"id": "desc_only", "name": "只有描述", "description": "只有描述"})
    return tmp_path


@pytest.fixture
def fake_synth(monkeypatch):
    calls: list[tuple[str, str, Path]] = []

    def synth(profile, text, destination: Path):
        calls.append((profile["id"], text, destination))
        if profile["id"] == "b" and "炸" in text:
            raise ProviderError("模拟网关 502")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"ID3" + profile["id"].encode())
        return destination

    monkeypatch.setattr(V, "synthesize_with_profile", synth)
    return calls


COMPARE = "/api/episodes/storyvideo/VC-T/cast/lin/voice-compare"


def test_default_line_is_first_dialogue_and_files_never_overwrite(project, fake_synth):
    resp = client.post(COMPARE, json={"voices": ["a", "b"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"] == "你怎么才来？"
    assert [i["voice_id"] for i in body["items"]] == ["a", "b"]
    assert all("error" not in i and i["url"].startswith(f"{COMPARE}/file/") for i in body["items"])
    assert body["items"][0]["name"] == "音色a"
    assert sorted(c[0] for c in fake_synth) == ["a", "b"] and all(c[1] == "你怎么才来？" for c in fake_synth)
    out_dir = project / "output" / "story_video" / "VC-T" / "09_voice_compare" / "lin"
    files = sorted(p.name for p in out_dir.glob("*.mp3"))
    assert len(files) == 2 and all(voices_router._COMPARE_NAME_RE.match(n) for n in files)
    # 同样的请求再来一次：新文件，旧文件还在（永不覆盖）
    again = client.post(COMPARE, json={"voices": ["a", "b"]}).json()
    assert len(list(out_dir.glob("*.mp3"))) == 4
    assert {i["file"] for i in again["items"]}.isdisjoint(set(files))


def test_custom_text_and_english_project_picks_en(project, fake_synth):
    body = client.post(COMPARE, json={"voices": ["a", "c"], "text": "  自定义的一句。 "}).json()
    assert body["text"] == "自定义的一句。"
    assert all(c[1] == "自定义的一句。" for c in fake_synth)
    path = project / "content" / "story_video" / "VC-T.json"
    spec = json.loads(path.read_text(encoding="utf-8")); spec["prompt_language"] = "en"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    assert client.post(COMPARE, json={"voices": ["a", "c"]}).json()["text"] == "Why are you so late?"


def test_no_lines_needs_custom_text(project, fake_synth):
    resp = client.post("/api/episodes/storyvideo/VC-T/cast/mute/voice-compare", json={"voices": ["a", "b"]})
    assert resp.status_code == 400 and resp.json()["detail"] == "这个角色没有台词，填一句试听文字"
    assert fake_synth == []
    ok = client.post("/api/episodes/storyvideo/VC-T/cast/mute/voice-compare", json={"voices": ["a", "b"], "text": "让开。"})
    assert ok.status_code == 200 and ok.json()["text"] == "让开。"
    assert client.post("/api/episodes/storyvideo/VC-T/cast/nobody/voice-compare", json={"voices": ["a", "b"]}).status_code == 404


def test_one_failure_does_not_fail_the_batch(project, fake_synth):
    body = client.post(COMPARE, json={"voices": ["a", "b", "desc_only", "ghost"], "text": "炸一下"}).json()
    by_id = {i["voice_id"]: i for i in body["items"]}
    assert "url" in by_id["a"] and "error" not in by_id["a"]
    assert by_id["b"]["error"].startswith("试听合成失败：") and "url" not in by_id["b"]
    assert "没有 TTS 音色" in by_id["desc_only"]["error"]
    assert by_id["ghost"]["error"] == "音色库里没有：ghost" and by_id["ghost"]["name"] == "ghost"
    # 只有成功的进清单
    listed = client.get(COMPARE).json()
    assert [i["voice_id"] for i in listed["runs"][0]["items"]] == ["a"]
    # 英文界面：条目里的错误也翻
    en = client.post(COMPARE, json={"voices": ["a", "ghost"], "text": "x"}, headers={"X-StoryStudio-Lang": "en"}).json()
    assert next(i for i in en["items"] if i["voice_id"] == "ghost")["error"] == "Not in the voice library: ghost"


def test_timeout_is_per_voice(project, fake_synth, monkeypatch):
    import threading

    monkeypatch.setattr(voices_router, "COMPARE_TIMEOUT_SECONDS", 0.05)
    release = threading.Event()

    def slow(profile, text, destination: Path):
        if profile["id"] == "c":
            release.wait(2)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"ID3")
        return destination

    monkeypatch.setattr(V, "synthesize_with_profile", slow)
    try:
        body = client.post(COMPARE, json={"voices": ["a", "c"]}).json()
    finally:
        release.set()
    by_id = {i["voice_id"]: i for i in body["items"]}
    assert "url" in by_id["a"]
    assert by_id["c"]["error"] == "合成超时（0 秒）"


def test_listing_and_file_streaming_rejects_traversal(project, fake_synth):
    assert client.get(COMPARE).json() == {"cast_id": "lin", "default_text": "你怎么才来？", "bound": "", "runs": []}
    first = client.post(COMPARE, json={"voices": ["a", "b"]}).json()
    client.post(COMPARE, json={"voices": ["b", "c"], "text": "第二批。"})
    listed = client.get(COMPARE).json()
    assert [r["text"] for r in listed["runs"]] == ["第二批。", "你怎么才来？"]
    assert [i["voice_id"] for i in listed["runs"][0]["items"]] == ["b", "c"]
    item = listed["runs"][1]["items"][0]
    assert item["in_library"] is True and item["name"] == "音色a" and item["url"] == first["items"][0]["url"]
    got = client.get(item["url"])
    assert got.status_code == 200 and got.headers["content-type"].startswith("audio/mpeg") and got.content == b"ID3a"
    # 库里删了音色：清单还在，标 in_library=False，名字退回记录时的
    client.delete("/api/voices/a")
    assert client.get(COMPARE).json()["runs"][1]["items"][0]["in_library"] is False
    # 文件被清掉：清单跳过它
    out_dir = project / "output" / "story_video" / "VC-T" / "09_voice_compare" / "lin"
    for p in out_dir.glob("a-*.mp3"):
        p.unlink()
    assert [i["voice_id"] for i in client.get(COMPARE).json()["runs"][1]["items"]] == ["b"]
    # 路径穿越 / 非样本文件名一律 404
    (out_dir / "index.json").is_file()
    for bad in ("..%2F..%2Findex.json", "index.json", ".hidden.mp3", "..\\x.mp3", "a-zzzzzzzz-1.mp3", "b-0123abcd-x.mp3"):
        assert client.get(f"{COMPARE}/file/{bad}").status_code == 404, bad
    assert client.get(f"{COMPARE}/file/nope-0123abcd-1.mp3").status_code == 404
    assert client.get("/api/episodes/storyvideo/VC-T/cast/nobody/voice-compare").status_code == 404


def test_count_limits_and_duplicates(project, fake_synth):
    r = client.post(COMPARE, json={"voices": ["a"]})
    assert r.status_code == 400 and r.json()["detail"] == "对比试听要选 2 到 6 个音色，现在是 1 个"
    r = client.post(COMPARE, json={"voices": ["a", "b", "c", "a2", "b2", "c2", "d2"]})
    assert r.status_code == 400 and "现在是 7 个" in r.json()["detail"]
    r = client.post(COMPARE, json={"voices": ["a", "", "a"]})
    assert r.status_code == 400 and r.json()["detail"] == "音色重复了：a"
    assert client.post(COMPARE, json={"voices": []}).status_code == 400
    assert fake_synth == []
    # 六个正好可以（不存在的音色算条目失败，不算数量错）
    ok = client.post(COMPARE, json={"voices": ["a", "b", "c", "x1", "x2", "x3"]})
    assert ok.status_code == 200 and len(ok.json()["items"]) == 6
    # 采用：复用现有绑定接口
    bound = client.post("/api/episodes/storyvideo/VC-T/cast/lin/voice", json={"voice_id": "b"})
    assert bound.status_code == 200 and bound.json()["voice_ref"] == "b"
    assert client.get(COMPARE).json()["bound"] == "b"
