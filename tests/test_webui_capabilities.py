"""模型能力表：查表、按设置检查 spec、开始制作前的拦截；配音镜一键切画外。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import capabilities as C
from genkit import settings as S
from storyvideo.runtime import StoryVideoRunner
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router

client = TestClient(app)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    for role in ("TEXT", "IMAGE", "VOICE", "VIDEO"):
        for key in ("ADAPTER", "BASE_URL", "API_KEY", "MODEL", "VOICE"):
            monkeypatch.delenv(f"STORYSTUDIO_{role}_{key}", raising=False)
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    return tmp_path


def write_spec(root: Path, **extra) -> Path:
    spec = {
        "id": "CAP-T", "output_root": "output/story_video/CAP-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "image_params": {"model": "@image", "aspect_ratio": "9:16", "image_size": "2K"},
        "video_params": {"model": "@video", "resolution": "1080p", "aspect_ratio": "9:16"},
        "cast": {"lin": {"name": "林小满", "block": "黑发。", "voice_tts": {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural"}}},
        "shots": [
            {"id": "S1", "dur": 6, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m", "lines": [{"who": "lin", "zh": "你怎么才来？"}]},
            {"id": "S2", "dur": 20, "cast": ["lin"], "shot": "中景", "art": "a", "motion": "m", "lines": []},
        ],
        "post_graphics": {"dubbing": {"mode": "all"}},
    }
    spec.update(extra)
    path = root / "content" / "story_video" / "CAP-T.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def test_lookup_prefers_longest_prefix_and_reports_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))  # 不让本机已拉取的模型目录影响静态表断言
    caps = C.lookup("video", "ark_video", "doubao-seedance-2-0-fast-260128")
    assert caps["resolutions"] == ["720p"] and caps["source"] == "verified"
    caps = C.lookup("video", "ark_video", "doubao-seedance-2-0-260128")
    assert caps["resolutions"] == ["1080p"]
    assert C.lookup("video", "openrouter_video", "google/veo-3.1")["durations"] == {"choices": [4, 6, 8]}
    assert C.lookup("video", "nope_adapter", "x")["known"] is False
    assert C.effective_model("@video", "m1") == "m1" and C.effective_model("doubao-x", "m1") == "doubao-x"


def test_check_spec_levels(project):
    write_spec(project)
    from storyvideo.spec import load_spec

    spec = load_spec("CAP-T", project_root=project)
    st = S.Settings()
    # 什么都没配：两条 warn
    res = C.check_spec(spec, st)
    assert res["errors"] == 0 and res["warnings"] == 2
    # verified 的组合越界：error，点名 S2
    st.role("video").adapter = "ark_video"; st.role("video").api_key = "k"; st.role("video").model = "doubao-seedance-2-0-260128"
    st.role("image").adapter = "ark_images"; st.role("image").api_key = "k"; st.role("image").model = "doubao-seedream-4-0-250828"
    res = C.check_spec(spec, st)
    errs = [i for i in res["issues"] if i["level"] == "error"]
    assert len(errs) == 1 and errs[0]["shots"] == ["S2"] and "4–15 秒" in errs[0]["message"]
    # doc 级记录：同样越界只 warn；不生成原生对白的模型提示要配音
    st.role("video").adapter = "minimax_video"; st.role("video").model = "MiniMax-Hailuo-02"
    res = C.check_spec(spec, st)
    assert res["errors"] == 0
    msgs = " ".join(i["message"] for i in res["issues"])
    assert "6 / 10 秒" in msgs and "台词配音" in msgs and "1080P" not in msgs
    # 未知模型：info
    st.role("video").model = "mystery-model"
    assert any(i["level"] == "info" for i in C.check_spec(spec, st)["issues"])


def test_capability_check_reports_reference_cap(project):
    """参考图上限（DESIGN §30）：能力表给了出图模型的 max_reference_images 时，能力检查逐镜数首帧请求会带的参考图。"""
    from webui import i18n

    cast = {"lin": {"name": "林小满", "block": "黑发。", "asset": "content/assets/lin.png"},
            "chen": {"name": "陈默", "block": "短发。", "asset": "content/assets/chen.png"}}
    shots = [
        {"id": "S1", "dur": 6, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m", "lines": []},
        {"id": "S2", "dur": 6, "cast": ["lin", "chen"], "shot": "中景", "art": "a", "motion": "m", "lines": [], "ref_frame": "S1"},
    ]
    write_spec(project, cast=cast, shots=shots)
    st = S.Settings()
    st.role("image").adapter = "ark_images"; st.role("image").api_key = "k"; st.role("image").model = "doubao-seededit-3-0-i2i-250628"
    S.save_settings(st)
    check = client.get("/api/episodes/storyvideo/CAP-T/capability-check").json()
    assert check["image"]["max_reference_images"] == 1
    hit = [i for i in check["issues"] if "最多收 1 张参考图" in i["message"]]
    assert len(hit) == 1 and hit[0]["level"] == "warn" and hit[0]["shots"] == ["S2"]
    assert "S2 ×3" in hit[0]["message"]      # 参考镜首帧 + 两张定妆
    i18n._compiled = None
    assert i18n.tr(hit[0]["message"], "en").startswith("doubao-seededit-3-0-i2i-250628 (per docs, untested) accepts at most 1 reference images")
    # 上限够用：一行 info 说上限；没有镜带参考图的项目什么都不说
    st.role("image").model = "doubao-seedream-4-0-250828"
    S.save_settings(st)
    check = client.get("/api/episodes/storyvideo/CAP-T/capability-check").json()
    hit = [i for i in check["issues"] if "最多收 10 张参考图" in i["message"]]
    assert len(hit) == 1 and hit[0]["level"] == "info" and "每镜都在上限内" in hit[0]["message"]
    write_spec(project)
    check = client.get("/api/episodes/storyvideo/CAP-T/capability-check").json()
    assert not any("张参考图" in i["message"] for i in check["issues"])


def test_prepare_blocks_on_error_unless_forced(project, monkeypatch):
    write_spec(project)
    st = S.Settings()
    st.role("video").adapter = "ark_video"; st.role("video").api_key = "k"; st.role("video").model = "doubao-seedance-2-0-260128"
    S.save_settings(st)
    check = client.get("/api/episodes/storyvideo/CAP-T/capability-check").json()
    assert check["errors"] == 1 and check["video"]["source"] == "verified"
    blocked = client.post("/api/episodes/storyvideo/CAP-T/prepare")
    assert blocked.status_code == 409 and blocked.json()["detail"]["check"]["errors"] == 1
    assert client.get("/api/episodes/storyvideo/CAP-T").json()["locked"] is False
    forced = client.post("/api/episodes/storyvideo/CAP-T/prepare?force=true")
    assert forced.status_code == 200, forced.text
    assert client.get("/api/episodes/storyvideo/CAP-T").json()["locked"] is True


def test_dubbed_shots_offscreen_toggle(project):
    path = write_spec(project)
    resp = client.post("/api/episodes/storyvideo/CAP-T/dubbing/offscreen", json={})
    assert resp.status_code == 200 and resp.json()["changed"] == ["S1"]      # S2 没台词，不是配音镜
    shots = {s["id"]: s for s in json.loads(path.read_text(encoding="utf-8"))["shots"]}
    assert shots["S1"]["speech_mode"] == "offscreen_narration" and "speech_mode" not in shots["S2"]
    again = client.post("/api/episodes/storyvideo/CAP-T/dubbing/offscreen", json={})
    assert again.json()["changed"] == []
    # 嘴型风险随之消失
    ready = client.get("/api/episodes/storyvideo/CAP-T/post-layers").json()["readiness"]["dubbing"]
    assert ready["lip_risk"] == []
    with StoryVideoRunner(project) as runner:
        runner.prepare("CAP-T")
    write_spec(project)  # 恢复成没有 speech_mode 的样子会被 amend 拒绝；这里只验证锁定后接口本身拒绝
    assert client.post("/api/episodes/storyvideo/CAP-T/dubbing/offscreen", json={"shots": ["S1"]}).status_code == 409


def test_snap_resolution_and_adapter_alignment(tmp_path, monkeypatch):
    """spec 写 720p、模型只认 768P/2K：提交时自动换成最接近的档位；能力检查只提示不拦。"""
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    assert C.snap_resolution(["768P", "2K"], "720p") == "768P"
    assert C.snap_resolution(["768P", "2K"], "1080p") == "768P"
    assert C.snap_resolution(["768P", "2K"], "1440p") == "2K"
    assert C.snap_resolution(["720p", "1080p"], "768P") == "720p"
    assert C.snap_resolution(["720p", "1080p"], "1080P") == "1080p"       # 大小写不同也算支持，用列表里的写法
    assert C.snap_resolution([], "720p") is None and C.snap_resolution(["720p"], "") is None
    assert C.resolution_for("video", "minimax_video", "MiniMax-H3", "720p") == "768P"
    assert C.resolution_for("video", "openrouter_video", "minimax/hailuo-3", "720p") == "768p"
    assert C.resolution_for("video", "openrouter_video", "minimax/hailuo-3", "768P") == "768p"   # 大小写不同也对齐成列表里的写法
    assert C.resolution_for("video", "openrouter_video", "google/veo-3.1", "768P") == "720p"
    assert C.resolution_for("video", "nope_adapter", "x", "720p") == "720p"       # 没记录原样
    # 火山直连的 Seedance 2.5 只记了 720p：1080p 对齐到 720p（别的来源放开了也不跟着改）
    assert C.resolution_for("video", "ark_video", "doubao-seedance-2-5-260628", "1080p") == "720p"
    assert C.resolution_for("video", "ark_video", "doubao-seedance-2-5-260628", "720p") == "720p"
    # 能力检查：越界但能对齐 → info
    st = S.Settings()
    st.role("video").adapter = "minimax_video"; st.role("video").api_key = "k"; st.role("video").model = "MiniMax-H3"
    spec = {"image_params": {"model": "@image"}, "video_params": {"model": "@video", "resolution": "720p"}, "shots": [{"id": "S1", "dur": 6}], "cast": {}}
    report = C.check_spec(spec, st)
    hits = [i for i in report["issues"] if "分辨率" in i["message"]]
    assert hits and hits[0]["level"] == "info" and "自动换成 768P" in hits[0]["message"]
    assert report["errors"] == 0


def test_plugin_capability_rows_align_resolutions(tmp_path, monkeypatch, load_plugin):
    """插件登记的能力条目跟内置的一样参与对齐与能力检查；插件撤回后条目也跟着消失。"""

    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))   # 不让本机已拉取的模型目录影响静态表断言
    assert load_plugin("example_async").status == "loaded"
    caps = C.lookup("video", "example_async_video", "demo-video-2")
    assert caps["resolutions"] == ["720p"] and caps["source"] == "doc" and caps["durations"] == {"min": 2, "max": 12}
    assert C.resolution_for("video", "example_async_video", "demo-video-2", "1080p") == "720p"
    st = S.Settings()
    st.role("video").adapter = "example_async_video"; st.role("video").model = "demo-video-2"
    spec = {"image_params": {"model": "@image"}, "video_params": {"model": "@video", "resolution": "1080p"},
            "shots": [{"id": "S1", "dur": 20}], "cast": {}}
    report = C.check_spec(spec, st)
    assert any("自动换成 720p" in i["message"] for i in report["issues"]), report["issues"]
    assert any("2–12 秒" in i["message"] and i["level"] == "warn" for i in report["issues"]), report["issues"]   # doc 记录：只提醒
    assert report["errors"] == 0
