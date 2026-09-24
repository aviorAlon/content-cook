"""视频提示词方言：按模型选方言、显式覆盖、老项目不变；MiniMax H3 三段结构与 <d> 台词；Seedance 的 {} 与 <> 语法。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from genkit import settings as S
from storyvideo import dialects as D
from storyvideo.prompts import compile_video_prompt, video_prompt_parts
from storyvideo.spec import load_spec

ROOT = Path(__file__).resolve().parents[1]


def _spec(tmp_path, **video_params):
    raw = json.loads((ROOT / "examples" / "episodes" / "first_episode.json").read_text(encoding="utf-8-sig"))
    raw["id"] = "DIA-T"
    raw["output_root"] = "output/story_video/DIA-T"
    raw["video_params"] = {**raw.get("video_params", {}), **video_params}
    content = tmp_path / "content" / "story_video"
    content.mkdir(parents=True, exist_ok=True)
    (content / "DIA-T.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return load_spec("DIA-T", project_root=tmp_path)


def test_dialect_for_model_and_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    assert D.dialect_for_model("MiniMax-H3") == "minimax-h3"
    assert D.dialect_for_model("minimax/hailuo-3") == "minimax-h3"
    assert D.dialect_for_model("doubao-seedance-2-0-fast-260128") == "seedance"
    assert D.dialect_for_model("kling-v2-5-turbo") == "generic" and D.dialect_for_model("") == "generic"
    # 缺省（老项目）= generic；auto 看模型；显式覆盖优先
    assert D.resolve_dialect({"video_params": {"model": "MiniMax-H3"}}) == "generic"
    assert D.resolve_dialect({"video_params": {"model": "MiniMax-H3", "prompt_dialect": "auto"}}) == "minimax-h3"
    assert D.resolve_dialect({"video_params": {"model": "MiniMax-H3", "prompt_dialect": "generic"}}) == "generic"
    assert D.resolve_dialect({"video_params": {"model": "kling", "prompt_dialect": "seedance"}}) == "seedance"
    # @video + 模型设置
    assert D.resolve_dialect({"video_params": {"model": "@video", "prompt_dialect": "auto"}}) == "generic"
    st = S.Settings(); st.role("video").adapter = "minimax_video"; st.role("video").model = "MiniMax-H3"; S.save_settings(st)
    assert D.resolve_dialect({"video_params": {"model": "@video", "prompt_dialect": "auto"}}) == "minimax-h3"
    assert D.resolve_dialect({"video_params": {"model": "@video", "prompt_dialect": "auto"}}, settings=S.Settings()) == "generic"


def test_generic_unchanged_without_field(tmp_path):
    spec = _spec(tmp_path, model="MiniMax-H3", resolution="768P", aspect_ratio="adaptive")
    spec["video_params"].pop("prompt_dialect", None)
    text = compile_video_prompt(spec, "S3_CAT_SPEAKS")
    assert "integrated_multimodal_description" not in text and "<d>" not in text
    assert text.startswith("入口状态：") and "台词：招财猫用普通话说：「可以，但是要看你的绩效。」" in text


def test_minimax_h3_structure(tmp_path):
    spec = _spec(tmp_path, model="MiniMax-H3", resolution="768P", aspect_ratio="adaptive", prompt_dialect="auto")
    text = compile_video_prompt(spec, "S3_CAT_SPEAKS")
    lines = text.splitlines()
    assert lines[0].startswith("integrated_multimodal_description: [Shot 1] ")
    assert lines[1].startswith("overall_soundscape: ") and lines[2] == "non_diegetic_music: N/A"
    assert "<d>[Chinese] 可以，但是要看你的绩效。</d>" in lines[0]
    assert "招财猫 (S2)" in lines[0]                        # 说话人 ID 按 cast 顺序：林小满 S1、招财猫 S2
    assert "嘴型与台词一致" in lines[0] and "不生成字幕" in lines[0]
    assert "铃铛轻响一声" in lines[1] and "铃铛轻响一声" not in lines[0]   # 音效只在 soundscape
    assert "可以，但是要看你的绩效" not in lines[1]                       # 对白不重复进 soundscape
    parts = video_prompt_parts(spec, "S3_CAT_SPEAKS")
    assert parts["lines"][0]["who"] == "cat" or parts["lines"][0]["name"] == "招财猫"
    # 无台词镜：没有 <d>，仍是三段
    silent = compile_video_prompt(spec, "S1_OVERTIME")
    assert "<d>" not in silent and silent.count("\n") == 2


def test_minimax_h3_offscreen_and_speaker_ids(tmp_path):
    spec = _spec(tmp_path, model="MiniMax-H3", resolution="768P", aspect_ratio="adaptive", prompt_dialect="auto")
    shot = next(s for s in spec["shots"] if s["id"] == "S3_CAT_SPEAKS")
    shot["speech_mode"] = "offscreen_narration"
    text = compile_video_prompt(spec, "S3_CAT_SPEAKS")
    assert "画外说话，不在画面里" in text and "嘴型" not in text.splitlines()[0]
    ids = D._speaker_ids(spec)
    assert list(ids.values()) == ["S1", "S2"] and "office" not in ids


def test_seedance_grammar(tmp_path):
    spec = _spec(tmp_path, model="doubao-seedance-2-0-fast-260128", resolution="720p", prompt_dialect="auto")
    text = compile_video_prompt(spec, "S3_CAT_SPEAKS")
    assert '{招财猫用中文说："可以，但是要看你的绩效。"}' in text
    assert "<铃铛轻响一声>" in text
    assert "整段视频不加字幕" in text and "\n" not in text
    assert "台词：" not in text       # 通用方言的台词段不再出现


def test_english_project_h3(tmp_path):
    spec = _spec(tmp_path, model="MiniMax-H3", resolution="768P", aspect_ratio="adaptive", prompt_dialect="minimax-h3")
    spec["prompt_language"] = "en"
    text = compile_video_prompt(spec, "S3_CAT_SPEAKS")
    assert text.startswith("integrated_multimodal_description: [Shot 1] ") and "<d>[" in text
    assert "no readable text appears" in text and "Room tone only" in text


def test_capability_check_reports_dialect_and_globals_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from webui import deps
    from webui.app import app
    from webui.routers import episodes as episodes_router
    from webui.routers import post as post_router
    from webui.routers import project as project_router

    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    for module in (deps, episodes_router, post_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    _spec(tmp_path)   # 示例：@video + auto
    client = TestClient(app)
    st = S.Settings(); st.role("video").adapter = "minimax_video"; st.role("video").model = "MiniMax-H3"; S.save_settings(st)
    d = client.get("/api/episodes/storyvideo/DIA-T/capability-check").json()["dialect"]
    assert d == {"setting": "auto", "effective": "minimax-h3", "for_model": "minimax-h3"}
    assert client.get("/api/episodes/storyvideo/DIA-T").json()["video_params"]["prompt_dialect"] == "auto"
    # 草稿：改 globals → generic
    r = client.put("/api/episodes/storyvideo/DIA-T/globals", json={"values": {"video_params": {"model": "@video", "resolution": "1080p", "aspect_ratio": "9:16", "generate_audio": True, "prompt_dialect": "generic"}}})
    assert r.status_code == 200, r.text
    d = client.get("/api/episodes/storyvideo/DIA-T/capability-check").json()["dialect"]
    assert d["setting"] == "generic" and d["effective"] == "generic" and d["for_model"] == "minimax-h3"
    assert client.put("/api/episodes/storyvideo/DIA-T/globals", json={"values": {"shots": []}}).status_code == 400
    assert "<d>" not in client.get("/api/episodes/storyvideo/DIA-T").json()["shots"][2]["video_prompt"]
