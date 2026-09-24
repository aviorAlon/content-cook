"""角色变体（2026-09-08，融合 drama-skills「身份 vs 版本」判据，DESIGN §23）：

``cast.<id>.variants = {key: {label, delta, asset?, shots?}}`` + ``shot.cast: ["lin@rain"]``。身份（block）只写一份，
变体只写差异；编译时 block + delta，锚图变体优先；所有按 id 找成员的地方都经过 ``split_cast_ref``；
没写变体的 spec 一个字不变（金丝雀）。
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import lint as L
from storyvideo.post_layers import dubbing_plan
from storyvideo.prompts import compile_frame_prompt, compile_video_prompt, frame_reference_asset_ids, video_prompt_parts
from storyvideo.runtime import StoryVideoRunner
from storyvideo.spec import SpecError, load_spec, normalize_spec, split_cast_ref
from webui import deps
from webui import revise as R
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

DELTA = "外加一件橙色旧雨衣，兜帽放下，头发湿贴额头"


def _spec(lang="zh", **extra):
    spec = {
        "id": "VAR-T", "output_root": "output/story_video/VAR-T", "prompt_language": lang,
        "frame_style": "S. ", "scene_anchor": "A. ", "camera": "", "neg": "文字", "subtitle": {"language": "zh"},
        "cast": {
            "lin": {"name": "林小满", "block": "林小满：黑发，圆脸。", "voice": "清亮",
                    "variants": {"rain": {"label": "雨夜", "delta": DELTA, "shots": ["S2"]}}},
            "cat": {"name": "招财猫", "block": "金色招财猫。"},
            "office": {"block": "办公区。"},
        },
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["office", "lin"], "shot": "中景，平视", "art": "她坐着", "motion": "她抬头",
             "lines": [{"who": "lin", "zh": "来了。"}]},
            {"id": "S2", "dur": 4, "cast": ["office", "lin@rain"], "shot": "近景，平视", "art": "她伸手", "motion": "她伸手",
             "lines": [{"who": "lin", "zh": "雨真大。"}]},
            {"id": "S3", "dur": 4, "cast": ["office", "cat"], "shot": "全景，平视", "art": "空镜", "motion": "灯闪", "lines": []},
        ],
    }
    spec.update(extra)
    return spec


def _png(size=(4, 4), color=(200, 30, 30)) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------- 引用拆分与归一化


def test_split_cast_ref():
    assert split_cast_ref("lin@rain") == ("lin", "rain")
    assert split_cast_ref("lin") == ("lin", None)
    assert split_cast_ref("lin@") == ("lin", None)
    assert split_cast_ref("") == ("", None)
    assert split_cast_ref(None) == ("", None)


def test_normalize_accepts_variant_refs_and_rejects_bad_ones(tmp_path):
    spec = normalize_spec(_spec(), project_root=tmp_path)
    assert spec["shots"][1]["cast"] == ["office", "lin@rain"]           # 原样保留
    assert spec["cast"]["lin"]["variants"]["rain"]["delta"] == DELTA
    # 引用不存在的变体 / 不存在的成员 / 空变体键 → SpecError
    bad = _spec(); bad["shots"][1]["cast"] = ["lin@snow"]
    with pytest.raises(SpecError, match="unknown variant: lin@snow"):
        normalize_spec(bad, project_root=tmp_path)
    bad = _spec(); bad["shots"][1]["cast"] = ["nobody@rain"]
    with pytest.raises(SpecError, match="unknown cast"):
        normalize_spec(bad, project_root=tmp_path)
    bad = _spec(); bad["shots"][1]["cast"] = ["lin@"]
    with pytest.raises(SpecError, match="empty variant key"):
        normalize_spec(bad, project_root=tmp_path)
    # 变体表本身的形状
    bad = _spec(); bad["cast"]["lin"]["variants"] = {"rain": {"label": "雨夜"}}
    with pytest.raises(SpecError, match="delta must be a non-empty string"):
        normalize_spec(bad, project_root=tmp_path)
    bad = _spec(); bad["cast"]["lin"]["variants"] = [{"delta": "x"}]
    with pytest.raises(SpecError, match="variants must be an object"):
        normalize_spec(bad, project_root=tmp_path)
    bad = _spec(); bad["cast"]["lin"]["variants"] = {"a@b": {"delta": "x"}}
    with pytest.raises(SpecError, match="without '@'"):
        normalize_spec(bad, project_root=tmp_path)


def test_normalize_resolves_variant_asset_like_member_asset(tmp_path):
    assets = tmp_path / "content" / "assets" / "VAR-T"; assets.mkdir(parents=True)
    (assets / "lin.png").write_bytes(b"x"); (assets / "lin_rain.png").write_bytes(b"y")
    raw = _spec()
    raw["cast"]["lin"]["asset"] = "content/assets/VAR-T/lin.png"
    raw["cast"]["lin"]["variants"]["rain"]["asset"] = "content/assets/VAR-T/lin_rain.png"
    raw["cast"]["lin"]["variants"]["snow"] = {"delta": "肩上落雪", "asset": "content/assets/VAR-T/missing.png"}
    spec = normalize_spec(raw, project_root=tmp_path)
    lin = spec["cast"]["lin"]
    assert Path(lin["asset"]).is_absolute() and Path(lin["asset"]).is_file()
    assert Path(lin["variants"]["rain"]["asset"]).is_absolute() and Path(lin["variants"]["rain"]["asset"]).name == "lin_rain.png"
    assert lin["variants"]["snow"]["asset"] == "content/assets/VAR-T/missing.png"   # 不存在的文件原样保留
    assert raw["cast"]["lin"]["variants"]["rain"]["asset"] == "content/assets/VAR-T/lin_rain.png"  # 原 dict 不动


# ---------------------------------------------------------------- 编译


def test_frame_prompt_appends_delta_once_and_picks_variant_anchor():
    spec = _spec()
    base = compile_frame_prompt(spec, "S1")
    assert "林小满：黑发，圆脸。" in base and DELTA not in base
    frame = compile_frame_prompt(spec, "S2")
    assert f"林小满：黑发，圆脸。{DELTA}。" in frame
    assert frame.count(DELTA) == 1 and frame.count("林小满：黑发，圆脸。") == 1
    assert frame.index("林小满：黑发") < frame.index("镜位：")
    # 视频提示词不带外形块，也不带 delta
    video = compile_video_prompt(spec, "S2")
    assert DELTA not in video
    # 锚图：变体有就用变体的，没有回落到成员的
    spec["cast"]["lin"]["asset"] = "LIN"
    assert frame_reference_asset_ids(spec, "S2") == ("LIN",)
    spec["cast"]["lin"]["variants"]["rain"]["asset"] = "LIN_RAIN"
    assert frame_reference_asset_ids(spec, "S2") == ("LIN_RAIN",)
    assert frame_reference_asset_ids(spec, "S1") == ("LIN",)
    # 同一镜同时出现基底和变体（少见，但不该重复占参考槽）
    spec["shots"][1]["cast"] = ["lin", "lin@rain"]
    assert frame_reference_asset_ids(spec, "S2") == ("LIN", "LIN_RAIN")


def test_frame_prompt_english_join_and_punctuation():
    spec = _spec(lang="en")
    spec["cast"]["lin"]["block"] = "Lin: black hair, round face."
    spec["cast"]["lin"]["variants"]["rain"]["delta"] = "plus an old orange raincoat, hood down"
    frame = compile_frame_prompt(spec, "S2")
    assert "Lin: black hair, round face. plus an old orange raincoat, hood down. " in frame
    # 基底没有句号 → 用 ". " 衔接；delta 自带句号不重复
    spec["cast"]["lin"]["block"] = "Lin: black hair "
    spec["cast"]["lin"]["variants"]["rain"]["delta"] = "wet hair."
    assert "Lin: black hair. wet hair. " in compile_frame_prompt(spec, "S2")
    zh = _spec()
    zh["cast"]["lin"]["block"] = "林小满：黑发"
    assert f"林小满：黑发。{DELTA}。" in compile_frame_prompt(zh, "S2")


def test_locks_and_performance_still_see_member_behind_variant():
    spec = _spec(performance_lock=True)
    spec["cast"]["lin"]["locks"] = [{"surface": "淡蓝色毛衣", "shots": "all"}]
    assert "固定不变：淡蓝色毛衣。" in compile_frame_prompt(spec, "S2")
    assert video_prompt_parts(spec, "S2")["performance"]   # lin@rain 有 name → 表演锁生效


def test_specs_without_variants_compile_identically(tmp_path):
    raw = json.loads((ROOT / "examples" / "episodes" / "first_episode.json").read_text(encoding="utf-8-sig"))
    raw["id"] = "NOVAR"; raw["output_root"] = "output/story_video/NOVAR"
    content = tmp_path / "content" / "story_video"; content.mkdir(parents=True)
    (content / "NOVAR.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    spec = load_spec("NOVAR", project_root=tmp_path)
    assert not any("variants" in m for m in spec["cast"].values() if isinstance(m, dict))
    for shot in spec["shots"]:
        assert all("@" not in c for c in shot["cast"])
    # 加了变体但没有镜引用 → 首帧提示词与参考图和加之前完全一样
    before = {s["id"]: (compile_frame_prompt(spec, s["id"]), frame_reference_asset_ids(spec, s["id"])) for s in spec["shots"]}
    first = next(iter(spec["cast"]))
    spec["cast"][first]["variants"] = {"x": {"delta": "多了一顶帽子"}}
    after = {s["id"]: (compile_frame_prompt(spec, s["id"]), frame_reference_asset_ids(spec, s["id"])) for s in spec["shots"]}
    assert before == after


# ---------------------------------------------------------------- lint


def test_lint_unused_variant_and_offscreen_switch():
    # 默认 spec：S1 基底 → S2 rain 相邻切造型、S2 没声明边界变化 → 恰好一条告警
    spec = _spec()
    msgs = L.check_variants(spec)
    assert len(msgs) == 1
    assert "shot S2" in msgs[0] and "林小满（lin）" in msgs[0] and "「基底」（S1）" in msgs[0] and "「rain」" in msgs[0] and "造型在镜外切换" in msgs[0]
    spec["shots"][1]["boundary_change"] = {"what": "披上雨衣"}
    assert L.check_variants(spec) == []
    assert not [m for m in L.check_all(spec) if "变体" in m or "造型" in m]
    # 定义了没用 → 提示
    spec["cast"]["lin"]["variants"]["snow"] = {"label": "雪夜", "delta": "肩上落雪"}
    msgs = L.check_variants(spec)
    assert len(msgs) == 1 and "cast.lin" in msgs[0] and "snow（雪夜）" in msgs[0] and "没有任何镜用到" in msgs[0]
    # 反向：S2 rain → S3 基底，S3 没声明 → 告警；声明了（任何真值）→ 不报
    spec = _spec()
    spec["shots"][1]["boundary_change"] = {"what": "披上雨衣"}
    spec["shots"][2]["cast"] = ["office", "lin"]
    msgs = L.check_variants(spec)
    assert len(msgs) == 1 and "shot S3" in msgs[0] and "「rain」（S2）" in msgs[0] and "「基底」" in msgs[0]
    spec["shots"][2]["boundary_change"] = "脱掉雨衣"
    assert L.check_variants(spec) == []
    # 中间隔了一镜（时间跳跃）不算相邻，不报
    spec = _spec()
    spec["shots"][1]["cast"] = ["office"]
    spec["shots"][2]["cast"] = ["office", "lin@rain"]
    assert L.check_variants(spec) == []
    # check_all 已接上
    spec = _spec(); spec["cast"]["lin"]["variants"]["snow"] = {"delta": "肩上落雪"}
    assert any("没有任何镜用到" in m for m in L.check_all(spec))
    assert any("造型在镜外切换" in m for m in L.check_all(spec))


def test_lint_reports_anchor_image_that_does_not_exist(tmp_path):
    """DRAMA-11：锚图指向的文件不存在时没人报，出图时它会被当成服务商的 asset id 原样提交。

    normalize_spec 会把**存在**的项目内相对路径换成绝对路径，所以归一化之后还留着相对路径形状的，就是磁盘上没有。"""

    raw = _spec()
    raw["cast"]["lin"]["variants"]["rain"]["asset"] = "content/assets/nope.png"
    raw["cast"]["cat"]["asset"] = "content/assets/gone.png"
    spec = normalize_spec(raw, project_root=tmp_path)

    msgs = L.check_variants(spec)
    assert [m for m in msgs if "cast.lin 的变体「rain」的锚图文件不存在：content/assets/nope.png" in m]
    assert [m for m in msgs if "cast.cat 的锚图文件不存在：content/assets/gone.png" in m]
    assert any("锚图文件不存在" in m for m in L.check_all(spec))

    # 文件真的在 → 不报（归一化把它换成了绝对路径）
    real = tmp_path / "content" / "assets" / "nope.png"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(base64.b64decode(_png()))
    raw["cast"]["cat"].pop("asset")
    spec = normalize_spec(raw, project_root=tmp_path)
    assert not [m for m in L.check_variants(spec) if "锚图文件不存在" in m]

    # 历史上的远端 asset id（不是路径形状）不误报
    raw["cast"]["lin"]["variants"]["rain"]["asset"] = "cgs-7f31a0e2"
    spec = normalize_spec(raw, project_root=tmp_path)
    assert not [m for m in L.check_variants(spec) if "锚图文件不存在" in m]


def test_cast_mentions_and_camera_columns_see_member_behind_variant():
    spec = _spec()
    # S2 正文提到「林小满」且 cast 里是 lin@rain：不该报「cast 没有 lin」
    spec["shots"][1]["art"] = "林小满伸手"
    assert not any("S2" in m and "cast 没有 lin" in m for m in L.check_cast_mentions(spec))
    # 机位高度检查只对有人物的镜：lin@rain 算人物
    spec["shots"][1]["shot"] = "近景"
    assert any("shot S2" in m for m in L.check_camera_columns(spec))


# ---------------------------------------------------------------- 配音 / 后端解析仍按裸 id


def test_dubbing_plan_and_links_resolve_bare_member():
    spec = _spec(post_graphics={"dubbing": {"mode": "all"}})
    plan = dubbing_plan(spec)
    assert plan["shots"] == ["S1", "S2"]
    assert set(plan["missing_tts_cast"]) == {"lin"}          # 没绑 TTS → 缺的是 lin，不是 lin@rain
    spec["cast"]["lin"]["voice_tts"] = {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural"}
    assert dubbing_plan(spec)["missing_tts_cast"] == {}
    links = deps.shot_links(spec)
    assert set(links) == {"S1", "S2", "S3"}
    assert project_router.shots_using(spec, "lin") == ["S1", "S2"]
    assert project_router.shots_using(spec, "lin", "rain") == ["S2"]
    assert project_router.shots_using(spec, "lin", None) == ["S1", "S2"]
    graph = project_router.build_graph(spec)
    uses = [e for e in graph["edges"] if e["type"] == "uses" and e["to"] == "cast:lin"]
    assert [e["from"] for e in uses] == ["shot:S1", "shot:S2"] and uses[1]["variant"] == "rain" and "variant" not in uses[0]


# ---------------------------------------------------------------- 接口 + revise


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    spec = _spec(subtitle={"language": "zh"})
    spec["cast"]["lin"].pop("variants")
    spec["shots"][1]["cast"] = ["office", "lin"]
    (content_dir / "VAR-T.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _raw(project: Path) -> dict:
    return json.loads((project / "content" / "story_video" / "VAR-T.json").read_text(encoding="utf-8"))


def test_cast_api_variants_roundtrip_validation_and_upload(project):
    url = "/api/episodes/storyvideo/VAR-T/cast/lin"
    variants = {"rain": {"label": " 雨夜 ", "delta": f" {DELTA} ", "shots": ["S2", "S2", " S3"]}, "hurt": {"delta": "左手缠着纱布"}}
    resp = client.put(url, json={"variants": variants})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["variants"]["rain"] == {"label": "雨夜", "delta": DELTA, "asset": "", "asset_url": None, "shots": ["S2", "S3"], "used_in": []}
    assert body["variants"]["hurt"]["label"] == "" and body["variants"]["hurt"]["shots"] == []
    assert _raw(project)["cast"]["lin"]["variants"] == {"rain": {"label": "雨夜", "delta": DELTA, "shots": ["S2", "S3"]}, "hurt": {"delta": "左手缠着纱布"}}
    # 不传 = 不动
    assert set(client.put(url, json={"voice": "低沉"}).json()["variants"]) == {"rain", "hurt"}
    # 镜头引用变体 → used_in 与 shots_using 都算上
    shot = client.put("/api/episodes/storyvideo/VAR-T/shots/S2", json={"cast": ["office", "lin@rain"]})
    assert shot.status_code == 200, shot.text
    listed = {c["id"]: c for c in client.get("/api/episodes/storyvideo/VAR-T/cast").json()}
    assert listed["lin"]["variants"]["rain"]["used_in"] == ["S2"] and listed["lin"]["used_in"] == ["S1", "S2"]
    # 引用不存在的变体：spec 校验拦下，不写盘
    assert client.put("/api/episodes/storyvideo/VAR-T/shots/S2", json={"cast": ["lin@snow"]}).status_code == 400
    assert _raw(project)["shots"][1]["cast"] == ["office", "lin@rain"]
    # 校验：delta 空、键有 @、不是对象、asset 绝对路径、shots 不是列表 → 400，且不写盘
    for bad in ({"rain": {"delta": " "}}, {"a@b": {"delta": "x"}}, {"rain": "x"}, {"rain": {"delta": "x", "asset": "C:/x.png"}},
                {"rain": {"delta": "x", "asset": "../x.png"}}, {"rain": {"delta": "x", "shots": "S2"}}, {"": {"delta": "x"}}):
        r = client.put(url, json={"variants": bad})
        assert r.status_code == 400, (bad, r.text)
    assert client.put(url, json={"variants": ["x"]}).status_code == 422
    assert set(_raw(project)["cast"]["lin"]["variants"]) == {"rain", "hurt"}
    # 变体锚图上传：落在 content/assets/<项目>/lin_rain-<摘要>.png，写进 variants.rain.asset，asset_url 可取
    up = client.post(f"{url}/variants/rain/asset", json={"filename": "r.png", "data_base64": _png()})
    assert up.status_code == 200, up.text
    assert up.json()["asset"].startswith("content/assets/VAR-T/lin_rain-") and up.json()["asset"].endswith(".png")
    assert (project / up.json()["asset"]).is_file()
    assert up.json()["asset_url"] == f"{url}/variants/rain/asset"
    assert client.get(f"{url}/variants/rain/asset").status_code == 200
    assert client.get(f"{url}/variants/hurt/asset").status_code == 404
    assert client.post(f"{url}/variants/snow/asset", json={"filename": "r.png", "data_base64": _png()}).status_code == 404
    assert client.post(f"{url}/variants/rain/asset", json={"filename": "r.png", "data_base64": "bm90IGFuIGltYWdl"}).status_code == 400
    listed = {c["id"]: c for c in client.get("/api/episodes/storyvideo/VAR-T/cast").json()}
    assert listed["lin"]["variants"]["rain"]["asset_url"] == f"{url}/variants/rain/asset"
    # 生成用的 spec：S2 参考图是变体锚图的绝对路径；S1 没有（成员本身没锚图）
    spec = load_spec("VAR-T", project_root=project)
    refs = frame_reference_asset_ids(spec, "S2")
    assert len(refs) == 1 and Path(refs[0]).is_absolute() and Path(refs[0]).name.startswith("lin_rain-")
    assert frame_reference_asset_ids(spec, "S1") == ()
    assert f"林小满：黑发，圆脸。{DELTA}。" in compile_frame_prompt(spec, "S2")
    # 删掉还在用的变体：spec 校验拦下（镜里还引用）
    assert client.put(url, json={"variants": {}}).status_code == 400
    client.put("/api/episodes/storyvideo/VAR-T/shots/S2", json={"cast": ["office", "lin"]})
    assert client.put(url, json={"variants": {}}).json()["variants"] == {}
    assert "variants" not in _raw(project)["cast"]["lin"]
    # 新建时也能带变体
    created = client.post("/api/episodes/storyvideo/VAR-T/cast", json={"id": "bo", "name": "阿博", "block": "阿博：平头。", "variants": {"suit": {"label": "西装", "delta": "换成黑西装"}}})
    assert created.status_code == 200 and created.json()["variants"]["suit"]["delta"] == "换成黑西装"


def test_variants_refused_on_locked_project_and_revise_handles_it(project):
    with StoryVideoRunner(project) as runner:
        runner.prepare("VAR-T")
    url = "/api/episodes/storyvideo/VAR-T/cast/lin"
    resp = client.put(url, json={"variants": {"rain": {"delta": DELTA}}})
    assert resp.status_code == 409 and "S1" in resp.json()["detail"] and "S2" in resp.json()["detail"]
    assert "variants" not in _raw(project)["cast"]["lin"]
    # 制作中变体锚图上传也拒绝（先要有变体，这里 404 在前；给一个有变体的项目验证 409）
    raw = _raw(project); raw["cast"]["lin"]["variants"] = {"rain": {"delta": DELTA}}
    (project / "content" / "story_video" / "VAR-T.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    assert client.post(f"{url}/variants/rain/asset", json={"filename": "r.png", "data_base64": _png()}).status_code == 409
    staged = client.post(f"{url}/variants/rain/asset?stage=true", json={"filename": "r.png", "data_base64": _png()})
    assert staged.status_code == 200 and staged.json()["staged"] is True and (project / staged.json()["asset"]).is_file()
    assert "asset" not in _raw(project)["cast"]["lin"]["variants"]["rain"]
    # revise：cast_edit 带 variants（含 staged 路径）→ 写进新版本；没有镜引用变体时提示词不变，不影响任何镜
    mutation = {"kind": "cast_edit", "cast_id": "lin", "variants": {"rain": {"label": "雨夜", "delta": DELTA, "asset": staged.json()["asset"]}}}
    out = R.apply_mutation(_raw(project), mutation)
    assert out["cast"]["lin"]["variants"]["rain"] == {"label": "雨夜", "delta": DELTA, "asset": staged.json()["asset"]}
    body = client.post("/api/episodes/storyvideo/VAR-T/revise-edit/preview", json={"mutation": mutation}).json()
    assert body["affected_frames"] == []
    # 镜头切到变体 → 只有那一镜首帧要重出
    mutation = {"kind": "shot_edit", "shot_id": "S2", "cast": ["office", "lin@rain"]}
    body = client.post("/api/episodes/storyvideo/VAR-T/revise-edit/preview", json={"mutation": mutation}).json()
    assert body["affected_frames"] == ["S2"], body
    # 形状不对的变体在 revise 里也是 400
    bad = {"kind": "cast_edit", "cast_id": "lin", "variants": {"rain": {"delta": ""}}}
    assert client.post("/api/episodes/storyvideo/VAR-T/revise-edit/preview", json={"mutation": bad}).status_code == 400
