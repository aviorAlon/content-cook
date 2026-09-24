"""音色库：档案增删改、目录、试听（注入合成器）、绑定给角色（拷贝语义、开始制作后拒绝）、绑定给旁白（后期块，制作中可改）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import voices as V
from storyvideo.runtime import StoryVideoRunner
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router

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
        "id": "VO-T", "output_root": "output/story_video/VO-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"}, "dialogue_prompt": "auto",
        "cast": {"lin": {"name": "林小满", "voice": "原来的声音描述", "block": "林小满：黑发。"}},
        "shots": [{"id": "S1", "dur": 4, "cast": ["lin"], "shot": "中景", "art": "a", "motion": "m",
                   "lines": [{"who": "lin", "zh": "你怎么才来？"}]}],
        "voice_over": {"lines": [{"shot": "S1", "at": 0.5, "zh": "凌晨一点。"}]},
    }
    path = content_dir / "VO-T.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_profile_crud_and_validation(home):
    assert client.get("/api/voices").json() == []
    created = client.post("/api/voices", json={"id": "girl_a", "name": "小雨", "description": "二十六七岁的女声，语速中等", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-4%"})
    assert created.status_code == 200, created.text
    assert created.json()["has_sample"] is False
    assert client.post("/api/voices", json={"id": "girl_a", "name": "x", "voice": "v"}).status_code == 400
    assert client.post("/api/voices", json={"id": "bad id", "name": "x", "voice": "v"}).status_code == 400
    assert client.post("/api/voices", json={"id": "empty", "name": "x"}).status_code == 400      # 既无音色也无描述
    assert client.post("/api/voices", json={"id": "only_desc", "name": "旁观者", "description": "低沉男声"}).status_code == 200
    updated = client.put("/api/voices/girl_a", json={"pitch": "-6Hz", "tags": ["女", "年轻"]})
    assert updated.status_code == 200 and updated.json()["pitch"] == "-6Hz" and updated.json()["tags"] == ["女", "年轻"]
    assert client.put("/api/voices/nope", json={"name": "x"}).status_code == 404
    assert client.put("/api/voices/girl_a", json={"engine": "magic"}).status_code == 400
    assert len(client.get("/api/voices").json()) == 2
    assert client.delete("/api/voices/only_desc").status_code == 200
    assert client.delete("/api/voices/only_desc").status_code == 404
    assert (home / "voices.json").is_file()


def test_catalog_uses_edge_list(home, monkeypatch):
    monkeypatch.setattr(V, "edge_catalog", lambda prefixes=("zh-", "en-"): [{"voice": "zh-CN-YunxiNeural", "gender": "Male", "locale": "zh-CN", "label": "Yunxi", "personalities": [], "categories": []}])
    body = client.get("/api/voices/catalog?engine=edge&locale=zh").json()
    assert body["voices"][0]["voice"] == "zh-CN-YunxiNeural"
    body = client.get("/api/voices/catalog?engine=settings").json()
    assert body["adapter"] == "" and "还没" in body["note"]


def test_sample_synthesis_with_injected_engine(home, monkeypatch):
    client.post("/api/voices", json={"id": "v1", "name": "v1", "engine": "edge", "voice": "zh-CN-YunxiNeural"})
    client.post("/api/voices", json={"id": "desc_only", "name": "d", "description": "只有描述"})
    calls = []

    def fake_synth(profile, text, destination: Path):
        calls.append((profile["id"], text))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"ID3fake")
        return destination

    monkeypatch.setattr(V, "synthesize_with_profile", fake_synth)
    resp = client.post("/api/voices/v1/sample", json={"text": "试听这一句。"})
    assert resp.status_code == 200, resp.text
    assert calls == [("v1", "试听这一句。")]
    assert client.get("/api/voices/v1/sample").status_code == 200
    assert client.get("/api/voices").json()[0]["has_sample"] is True or any(v["has_sample"] for v in client.get("/api/voices").json())
    assert client.post("/api/voices/desc_only/sample", json={}).status_code == 400
    # 改了 TTS 参数，旧样本作废
    client.put("/api/voices/v1", json={"rate": "+5%"})
    assert client.get("/api/voices/v1/sample").status_code == 404


def test_bind_cast_copies_description_and_refuses_after_start(project):
    path = project / "content" / "story_video" / "VO-T.json"
    client.post("/api/voices", json={"id": "girl_a", "name": "小雨", "description": "二十六七岁的女声，语速中等，口齿清楚", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-4%"})
    resp = client.post("/api/episodes/storyvideo/VO-T/cast/lin/voice", json={"voice_id": "girl_a"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["voice_ref"] == "girl_a" and body["voice_name"] == "小雨"
    assert body["voice"] == "二十六七岁的女声，语速中等，口齿清楚"
    assert body["voice_tts"] == {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-4%"}
    saved = json.loads(path.read_text(encoding="utf-8"))["cast"]["lin"]
    assert saved["voice_ref"] == "girl_a" and saved["voice"].startswith("二十六七岁")
    # 描述进视频提示词
    from storyvideo.prompts import compile_video_prompt
    from storyvideo.spec import load_spec
    assert "二十六七岁的女声" in compile_video_prompt(load_spec("VO-T", project_root=project), "S1")
    # 拷贝语义：库里改了描述，项目不变
    client.put("/api/voices/girl_a", json={"description": "改过的描述"})
    assert json.loads(path.read_text(encoding="utf-8"))["cast"]["lin"]["voice"].startswith("二十六七岁")
    # 解绑保留文字，去掉引用
    un = client.post("/api/episodes/storyvideo/VO-T/cast/lin/voice", json={"voice_id": None}).json()
    assert un["voice_ref"] == "" and un["voice"].startswith("二十六七岁")
    assert client.post("/api/episodes/storyvideo/VO-T/cast/lin/voice", json={"voice_id": "nope"}).status_code == 404
    with StoryVideoRunner(project) as runner:
        runner.prepare("VO-T")
    assert client.post("/api/episodes/storyvideo/VO-T/cast/lin/voice", json={"voice_id": "girl_a"}).status_code == 409


def test_bind_narration_works_while_in_production(project):
    path = project / "content" / "story_video" / "VO-T.json"
    client.post("/api/voices", json={"id": "narr", "name": "旁白男", "engine": "edge", "voice": "zh-CN-YunxiNeural", "rate": "-6%", "pitch": "-8Hz"})
    client.post("/api/voices", json={"id": "desc_only", "name": "d", "description": "只有描述"})
    with StoryVideoRunner(project) as runner:
        runner.prepare("VO-T")
    resp = client.post("/api/episodes/storyvideo/VO-T/voice-over/voice", json={"voice_id": "narr"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "amended" and resp.json()["locked"] is True
    vo = json.loads(path.read_text(encoding="utf-8"))["voice_over"]
    assert vo["voice_ref"] == "narr" and vo["engine"] == "edge"
    assert vo["edge"] == {"voice": "zh-CN-YunxiNeural", "rate": "-6%", "pitch": "-8Hz"}
    assert vo["lines"][0]["zh"] == "凌晨一点。"
    assert client.post("/api/episodes/storyvideo/VO-T/voice-over/voice", json={"voice_id": "desc_only"}).status_code == 400
    with StoryVideoRunner(project) as runner:
        assert runner.status("VO-T")["run_id"] == "VO-T"


def test_structured_description_compiles_both_languages(home):
    traits = {"gender": "female", "age": "young", "timbre": "清亮, 略带沙哑", "pace": "medium", "volume": "normal", "accent": "标准普通话", "mood": "平静"}
    prev = client.post("/api/voices/preview-description", json={"traits": traits}).json()
    assert prev["zh"] == "二十多岁的女声，清亮、略带沙哑，语速中等，音量正常，标准普通话，情绪基调平静，口齿清楚"
    assert prev["en"].startswith("A female voice in their twenties, clear and bright, slightly husky timbre, medium pace, normal volume, standard Mandarin, calm baseline")
    assert prev["en"].endswith("every word clearly articulated")
    created = client.post("/api/voices", json={"id": "girl_s", "name": "小雨", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "traits": traits})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["description_mode"] == "traits" and body["description"] == prev["zh"] and body["description_en"] == prev["en"]
    # 改一个维度，两版描述跟着变
    upd = client.put("/api/voices/girl_s", json={"traits": {**traits, "age": "middle"}}).json()
    assert upd["description"].startswith("四五十岁的女声")
    # 切到手写模式：描述原样，英文版不再提供
    custom = client.put("/api/voices/girl_s", json={"description_mode": "custom", "description": "随便写的"}).json()
    assert custom["description"] == "随便写的" and "description_en" not in custom
    assert client.post("/api/voices/preview-description", json={"traits": {"gender": "robot"}}).status_code == 400


def test_draft_traits_from_catalog_and_english_project_binding(project, monkeypatch):
    monkeypatch.setattr(V, "edge_catalog", lambda prefixes=("zh-", "en-"): [
        {"voice": "zh-CN-XiaoxiaoNeural", "gender": "Female", "locale": "zh-CN", "label": "Xiaoxiao", "personalities": ["Warm", "Cute"], "categories": []}])
    draft = client.get("/api/voices/draft-traits?voice=zh-CN-XiaoxiaoNeural").json()
    assert draft["found"] and draft["traits"]["gender"] == "female" and draft["traits"]["timbre"] == ["温暖", "甜"]
    assert draft["traits"]["accent"] == "标准普通话" and draft["traits"]["age"] == ""
    assert client.get("/api/voices/draft-traits?voice=nope").json()["found"] is False

    client.post("/api/voices", json={"id": "girl_s", "name": "小雨", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural",
                                     "traits": {"gender": "female", "age": "young", "timbre": ["温暖"], "pace": "medium", "volume": "normal"}})
    path = project / "content" / "story_video" / "VO-T.json"
    spec = json.loads(path.read_text(encoding="utf-8")); spec["prompt_language"] = "en"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    bound = client.post("/api/episodes/storyvideo/VO-T/cast/lin/voice", json={"voice_id": "girl_s"}).json()
    assert bound["voice"].startswith("A female voice in their twenties, warm timbre")


def test_locked_sample_does_not_break_save(home, monkeypatch):
    """Windows 上浏览器播着旧样本时文件删不掉（WinError 32）：保存/删除不能因此失败，旧文件下次再清。"""
    client.post("/api/voices", json={"id": "lock", "name": "l", "engine": "edge", "voice": "zh-CN-YunxiNeural"})
    monkeypatch.setattr(V, "synthesize_with_profile", lambda profile, text, dest: (dest.write_bytes(b"ID3a"), dest)[1])
    first = client.post("/api/voices/lock/sample", json={}).json()
    old_path = V.current_sample("lock")
    assert old_path is not None and old_path.name.startswith("lock-")

    real_unlink = Path.unlink

    def locked_unlink(self, *a, **kw):
        if self == old_path:
            raise PermissionError(32, "另一个程序正在使用此文件")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    # 改参数：旧样本删不掉也要保存成功，且 has_sample 变 False（参数变了就是另一个文件名）
    r = client.put("/api/voices/lock", json={"rate": "+5%"})
    assert r.status_code == 200, r.text
    assert r.json()["has_sample"] is False
    assert old_path.exists()      # 没删掉，留着下次清
    assert client.get("/api/voices/lock/sample").status_code == 404
    # 重新试听：新文件名，旧的仍在也不影响
    second = client.post("/api/voices/lock/sample", json={}).json()
    assert second["sample_url"] != first["sample_url"]
    assert client.get("/api/voices/lock/sample").status_code == 200
    # 删除档案：锁着的文件跳过，不报错
    assert client.delete("/api/voices/lock").status_code == 200
    monkeypatch.setattr(Path, "unlink", real_unlink)
    V._sweep_samples("lock")
    assert V.current_sample("lock") is None
