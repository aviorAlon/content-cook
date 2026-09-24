"""形态卡（DESIGN §31，融合 drama-skills G19）：卡文件完整性、id/名/别名解析与 spec 归一化、默认值只补缺省、
运动预算句进三种方言的视频提示词（没写 form 的 spec 零变化）、lint 每条规则的命中与不命中、接口与起草参数透传。
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from storyvideo import forms as F
from storyvideo import lint as L
from storyvideo import scriptgen
from storyvideo.prompts import compile_frame_prompt, compile_video_prompt, video_prompt_parts
from storyvideo.scriptgen import assemble_spec, build_system_prompt, generate_draft
from storyvideo.spec import SpecError, normalize_spec
from webui.app import app

client = TestClient(app)

REQUIRED_DEFAULTS = ("aspect_ratio", "resolution", "shot_seconds", "subtitles", "max_shots_per_minute", "hook_seconds", "portrait")


def _spec(**extra):
    base = {
        "id": "EP-FORM",
        "output_root": "output/story_video/EP-FORM",
        "prompt_language": "zh",
        "frame_style": "竖屏写实短剧的一帧画面。",
        "scene_anchor": "9:16 构图。",
        "camera": "手持。",
        "neg": "字幕",
        "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "block": "黑发，白 T 恤。"}},
        "shots": [
            {"id": "S1", "dur": 5, "cast": ["lin"], "shot": "中景，平视", "art": "林小满坐在工位。", "motion": "她抬头。",
             "lines": [{"who": "lin", "zh": "你怎么才来？"}]},
            {"id": "S2", "dur": 5, "cast": ["lin"], "shot": "近景，平视", "art": "她盯着屏幕。", "motion": "她伸手。"},
        ],
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------- 卡文件

def test_cards_file_is_small_and_complete():
    raw = F.FORM_CARDS_PATH.read_bytes()
    assert len(raw) <= 15 * 1024, "form_cards.json 超过 15 KB"
    data = json.loads(raw.decode("utf-8"))
    assert data["_meta"]["version"] == 1 and "drama-skills" in data["_meta"]["source"]
    cards = F.form_cards()
    assert 4 <= len(cards) <= 6
    assert len({c["id"] for c in cards}) == len(cards) and len({c["name"] for c in cards}) == len(cards)
    assert {"vertical_drama", "horizontal_short", "anime_vertical", "talking_head", "product_story"} <= {c["id"] for c in cards}
    seen: dict[str, str] = {}
    for card in cards:
        assert card["id"].isascii()
        for key in REQUIRED_DEFAULTS:
            assert key in card["defaults"], (card["id"], key)
        lo, hi = card["defaults"]["shot_seconds"]
        assert 0 < lo < hi
        assert card["must_have"] and all(str(m).strip() for m in card["must_have"])
        assert str(card["motion_budget"]).strip()
        english = card["english"]
        assert english["name"].strip() and english["motion_budget"].strip()
        assert len(english["must_have"]) == len(card["must_have"])
        # 别名不能跨卡撞车
        for key in [card["id"], card["name"], english["name"], *card["aliases"]]:
            low = key.lower()
            assert low not in seen or seen[low] == card["id"], (key, seen.get(low), card["id"])
            seen[low] = card["id"]


def test_find_and_normalize_by_id_name_alias():
    assert F.find_form("vertical_drama")["name"] == "竖屏短剧"
    assert F.find_form("VERTICAL_DRAMA")["id"] == "vertical_drama"
    assert F.find_form("竖屏短剧")["id"] == "vertical_drama"
    assert F.find_form(" 短剧 ")["id"] == "vertical_drama"
    assert F.find_form("Vertical short drama")["id"] == "vertical_drama"
    assert F.find_form("vlog")["id"] == "talking_head"
    assert F.find_form("漫剧")["id"] == "anime_vertical"
    assert F.find_form("") is None and F.find_form(None) is None and F.find_form("火星形态") is None
    assert F.normalize_form("横屏") == "horizontal_short"
    with pytest.raises(ValueError):
        F.normalize_form("火星形态")


def test_spec_normalizes_form_to_id_and_rejects_unknown(tmp_path):
    spec = normalize_spec(_spec(form="短剧"), project_root=tmp_path)
    assert spec["form"] == "vertical_drama"
    assert "form" not in normalize_spec(_spec(), project_root=tmp_path)
    with pytest.raises(SpecError, match="form references unknown form card: 火星形态"):
        normalize_spec(_spec(form="火星形态"), project_root=tmp_path)
    with pytest.raises(SpecError, match="form must be a non-empty string"):
        normalize_spec(_spec(form="  "), project_root=tmp_path)
    with pytest.raises(SpecError, match="form must be a non-empty string"):
        normalize_spec(_spec(form=3), project_root=tmp_path)


# ---------------------------------------------------------------- 默认值

def test_form_defaults_fill_only_missing_keys():
    draft = {"form": "horizontal_short", "image_params": {"model": "@image"}, "video_params": {"aspect_ratio": "9:16"}}
    out = F.apply_form_defaults(draft)
    assert out is draft
    assert draft["image_params"] == {"model": "@image", "aspect_ratio": "16:9"}
    # 明写的 9:16 不被卡的 16:9 覆盖；缺的 resolution 补上
    assert draft["video_params"] == {"aspect_ratio": "9:16", "resolution": "1080p"}
    assert "subtitle" not in draft           # 横屏短片字幕可选，不补字幕块
    portrait = F.apply_form_defaults({"form": "vertical_drama"})
    assert portrait["subtitle"]["language"] and portrait["image_params"]["aspect_ratio"] == "9:16"
    explicit = {"form": "vertical_drama", "subtitle": {"display": "full_shot"}}
    F.apply_form_defaults(explicit)
    assert explicit["subtitle"] == {"display": "full_shot"}
    untouched = {"image_params": {"aspect_ratio": "1:1"}}
    assert F.apply_form_defaults(copy.deepcopy(untouched)) == untouched
    assert F.form_defaults({}) == {} and F.form_defaults({"form": "火星"}) == {}
    assert F.form_defaults({"form": "口播"})["max_shots_per_minute"] == 8


def test_assemble_spec_with_form_applies_defaults_and_without_form_is_unchanged():
    draft = {"title_zh": "x", "shots": [{"id": "S1", "dur": 5}], "cast": {}}
    plain = assemble_spec("EP-1", copy.deepcopy(draft), camera_profile="p", prompt_language="zh")
    assert "form" not in plain and plain["image_params"]["aspect_ratio"] == "9:16"
    landscape = assemble_spec("EP-1", copy.deepcopy(draft), camera_profile="p", prompt_language="zh", form="横屏短片")
    assert landscape["form"] == "horizontal_short"
    assert landscape["image_params"]["aspect_ratio"] == "16:9" and landscape["video_params"]["aspect_ratio"] == "16:9"
    assert landscape["video_params"]["model"] == "@video" and landscape["video_params"]["resolution"] == "1080p"
    assert landscape["subtitle"]            # 通用默认的字幕块仍在（字幕可选 ≠ 删掉样式）
    with pytest.raises(ValueError, match="不认识的形态：火星"):
        assemble_spec("EP-1", copy.deepcopy(draft), camera_profile="p", prompt_language="zh", form="火星")


# ---------------------------------------------------------------- 提示词

def test_motion_budget_sentence_zh_en_and_empty():
    card = F.find_form("vertical_drama")
    assert F.motion_budget_sentence({"form": "vertical_drama", "prompt_language": "zh"}) == card["motion_budget"]
    assert F.motion_budget_sentence({"form": "vertical_drama", "prompt_language": "en"}) == card["english"]["motion_budget"]
    assert F.motion_budget_sentence({}) == "" and F.motion_budget_sentence({"form": "火星"}) == ""


def test_video_prompt_gets_form_budget_in_all_dialects_and_frame_prompt_unchanged(tmp_path):
    zh_card = F.find_form("vertical_drama")["motion_budget"]
    plain = normalize_spec(_spec(), project_root=tmp_path)
    with_form = normalize_spec(_spec(form="vertical_drama"), project_root=tmp_path)
    for sid in ("S1", "S2"):
        assert compile_frame_prompt(plain, sid) == compile_frame_prompt(with_form, sid)   # 首帧不带运动预算
        assert zh_card not in compile_video_prompt(plain, sid)
        video = compile_video_prompt(with_form, sid)
        assert zh_card in video
        parts = video_prompt_parts(with_form, sid)
        assert parts["form_budget"] == zh_card + F.find_form("vertical_drama")["composition"]   # 运动预算 + 构图句（P1）
        assert video_prompt_parts(plain, sid)["form_budget"] == ""
        # 通用方言：紧跟在揭示句位置之后、表演句之前——即去掉这一句就是老输出
        assert video.replace(" " + zh_card + F.find_form("vertical_drama")["composition"], "") == compile_video_prompt(plain, sid)
    for dialect in ("minimax-h3", "seedance"):
        raw = _spec(form="vertical_drama", video_params={"model": "MiniMax-H3" if dialect == "minimax-h3" else "seedance-1.0", "prompt_dialect": dialect})
        spec = normalize_spec(raw, project_root=tmp_path)
        text = compile_video_prompt(spec, "S1")
        assert zh_card.rstrip("。") in text, dialect
        raw_plain = _spec(video_params=raw["video_params"])
        assert zh_card.rstrip("。") not in compile_video_prompt(normalize_spec(raw_plain, project_root=tmp_path), "S1")


def test_english_project_gets_english_budget(tmp_path):
    raw = _spec(form="talking_head", prompt_language="en")
    spec = normalize_spec(raw, project_root=tmp_path)
    en = F.find_form("talking_head")["english"]["motion_budget"]
    assert en in compile_video_prompt(spec, "S2")
    assert F.find_form("talking_head")["motion_budget"] not in compile_video_prompt(spec, "S2")


def test_canary_spec_has_no_form_and_compiles_unchanged():
    from pathlib import Path

    root = Path(__file__).resolve().parent / "canary"
    spec = json.loads((root / "mini_spec.json").read_text(encoding="utf-8"))
    expected = json.loads((root / "expected_prompts.json").read_text(encoding="utf-8"))
    assert "form" not in spec
    for shot in spec["shots"]:
        assert compile_video_prompt(spec, shot["id"]) == expected[shot["id"]]["video"]


# ---------------------------------------------------------------- lint

def test_lint_silent_without_form_and_on_clean_vertical_spec():
    assert L.check_form(_spec()) == []
    assert L.check_form(_spec(form="vertical_drama")) == []


def test_lint_aspect_mismatch():
    msgs = L.check_form(_spec(form="vertical_drama", video_params={"aspect_ratio": "16:9"}, image_params={"aspect_ratio": "9:16"}))
    assert msgs == ["全集：形态「竖屏短剧」是竖屏（9:16），但视频参数的画幅是 16:9——改画幅，或换形态"]
    msgs = L.check_form(_spec(form="横屏", image_params={"aspect_ratio": "9:16"}, delivery={"width": 1080, "height": 1920}))
    assert any("生图参数的画幅是 9:16" in m for m in msgs) and any("交付尺寸是 1080×1920" in m for m in msgs)
    # adaptive / 读不出的画幅不判
    assert L.check_form(_spec(form="vertical_drama", video_params={"aspect_ratio": "adaptive"})) == []


def test_lint_hook_window_on_first_shot():
    # 首镜 8s，无台词无 beats → 告警；有台词（默认 0.8s 开口）→ 不告警；beat 2s 起 → 不告警；台词字幕 4s 才起 → 告警
    silent = _spec(form="vertical_drama")
    silent["shots"][0] = {"id": "S1", "dur": 8, "cast": ["lin"], "shot": "中景，平视", "art": "她坐着。", "motion": "她不动。"}
    msgs = L.check_form(silent)
    assert msgs == ["shot S1: 首镜 8s，前 3 秒既没开口也没节拍——形态「竖屏短剧」要求前 3 秒有钩子；把开口或动作提前，或把首镜拆短"]
    silent["shots"][0]["lines"] = [{"who": "lin", "zh": "你怎么才来？"}]
    assert L.check_form(silent) == []
    silent["shots"][0]["lines"] = [{"who": "lin", "zh": "你怎么才来？", "subtitle_start_seconds": 4, "subtitle_end_seconds": 6}]
    assert any("第一件事在 4s 才发生" in m for m in L.check_form(silent))
    silent["shots"][0].pop("lines")
    silent["shots"][0]["beats"] = [{"t": "2-8", "picture": "她起身"}]
    assert L.check_form(silent) == []
    # 首镜本身不超过钩子窗口就不判
    short = _spec(form="vertical_drama")
    short["shots"][0] = {"id": "S1", "dur": 3, "cast": ["lin"], "shot": "中景", "art": "a", "motion": "m"}
    assert L.check_form(short) == []


def test_lint_average_duration_and_shots_per_minute():
    long = _spec(form="vertical_drama")
    for s in long["shots"]:
        s["dur"] = 12
    msgs = L.check_form(long)
    assert any(m.startswith("全集：平均每镜 12.0s，超出形态「竖屏短剧」的 3–8s——") for m in msgs)
    tiny = _spec(form="口播")
    tiny["shots"] = [{"id": f"S{i}", "dur": 3, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m",
                      "lines": [{"who": "lin", "zh": "你怎么才来？"}]} for i in range(1, 13)]   # 36s，20 镜/分钟
    msgs = L.check_form(tiny)
    assert any("低于形态「口播 / vlog」的 5–15s" in m for m in msgs)
    assert any("每分钟 20 镜，超过形态「口播 / vlog」的上限 8 镜" in m for m in msgs)
    # 不到 30 秒的片段不外推每分钟镜数
    tiny["shots"] = tiny["shots"][:4]
    assert not any("每分钟" in m for m in L.check_form(tiny))
    assert not any("每分钟" in m for m in L.check_form(_spec(form="vertical_drama")))


def test_lint_subtitles_off_when_form_requires_them():
    assert any("要求字幕常开" in m for m in L.check_form(_spec(form="vertical_drama", subtitle={"render_mode": "none"})))
    assert any("要求字幕常开" in m for m in L.check_form(_spec(form="anime_vertical", subtitle={"display": "off"})))
    assert L.check_form(_spec(form="vertical_drama", subtitle={"display": "full_shot"})) == []
    assert L.check_form(_spec(form="horizontal_short", subtitle={"render_mode": "none"}, image_params={"aspect_ratio": "16:9"})) == []


def test_lint_wide_shot_without_closer_follow_up_on_portrait_forms():
    spec = _spec(form="vertical_drama")
    spec["shots"] = [
        {"id": "S1", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m", "lines": [{"who": "lin", "zh": "你怎么才来？"}]},
        {"id": "S2", "dur": 4, "cast": ["lin"], "shot": "大全景，俯拍", "art": "a", "motion": "m"},
        {"id": "S3", "dur": 4, "cast": ["lin"], "shot": "全景", "art": "a", "motion": "m"},
        {"id": "S4", "dur": 4, "cast": ["lin"], "shot": "远景", "art": "a", "motion": "m"},
    ]
    msgs = L.check_form(spec)
    assert [m.split(":")[0] for m in msgs] == ["shot S2", "shot S3", "shot S4"]
    assert msgs[0] == "shot S2: 竖屏形态「竖屏短剧」里用了远景，之后 2 镜内没有更近的景别——竖屏上远景里人脸认不出，紧接一个近景或特写"
    spec["shots"][3]["shot"] = "特写"
    msgs = L.check_form(spec)
    assert [m.split(":")[0] for m in msgs] == ["shot S3"] or msgs == []   # S2 两镜内（S4）回到特写；S3 的两镜内也有 S4
    assert not any("S2" in m for m in msgs)
    # 横屏形态不管远景；英文景别词同样识别
    spec["form"] = "horizontal_short"
    spec["image_params"] = {"aspect_ratio": "16:9"}
    assert not any("远景" in m for m in L.check_form(spec))
    en = _spec(form="vertical_drama", prompt_language="en")
    en["shots"] = [
        {"id": "S1", "dur": 4, "cast": ["lin"], "shot": "wide shot, high angle", "art": "a", "motion": "m", "lines": [{"who": "lin", "zh": "你怎么才来？"}]},
        {"id": "S2", "dur": 4, "cast": ["lin"], "shot": "full shot", "art": "a", "motion": "m"},
    ]
    assert len([m for m in L.check_form(en) if "远景" in m]) == 2
    en["shots"][1]["shot"] = "close-up"
    assert not any("远景" in m for m in L.check_form(en))


def test_check_all_includes_form_and_problem_cards_classify_as_form():
    from webui.problems import KIND_LABELS, problem_card

    spec = _spec(form="vertical_drama", video_params={"aspect_ratio": "16:9"})
    warnings = L.check_all(spec)
    hit = [w for w in warnings if "形态「竖屏短剧」" in w]
    assert hit
    card = problem_card(hit[0])
    assert card["kind"] == "form" and card["scope"] == "episode" and card["fix"] == "改画幅，或换形态"
    assert KIND_LABELS["form"] == "形态"
    assert problem_card("shot S2: 竖屏形态「竖屏短剧」里用了远景，之后 2 镜内没有更近的景别——竖屏上远景里人脸认不出，紧接一个近景或特写")["kind"] == "form"


# ---------------------------------------------------------------- 起草与接口

def test_system_prompt_with_form_appends_card_section_and_without_is_unchanged():
    plain = build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh")
    with_form = build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh", form="竖屏短剧")
    assert with_form.startswith(plain)
    section = with_form[len(plain):]
    card = F.find_form("vertical_drama")
    assert "形态卡：竖屏短剧" in section and "9:16" in section and "3–8 秒" in section
    assert card["must_have"][0] in section and "- 必带项：" in section
    assert card["motion_budget"] in section and "- 运动预算：" in section
    assert "字幕：常开" in section
    assert build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh", form="vertical_drama") == with_form
    assert build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh", form=None) == plain
    assert build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh", form="  ") == plain
    assert "形态卡：" not in plain
    both = build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh", genre="复仇打脸", form="口播")
    assert both.index("题材卡：复仇打脸") < both.index("形态卡：口播 / vlog")
    with pytest.raises(ValueError, match="不认识的形态：火星"):
        build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh", form="火星")


def test_generate_draft_passes_form_section_and_rejects_unknown(monkeypatch):
    seen = {}

    def fake_dispatch(system_prompt, user_prompt, *, json_schema):
        seen["system"] = system_prompt
        return {"shots": []}, None, 1, "settings"

    monkeypatch.setattr(scriptgen, "_dispatch", fake_dispatch)
    generate_draft(episode_id="EP-1", direction="x", target_seconds=60, camera_profile="dark_comedy_vertical",
                   prompt_language="zh", cast_hint=None, form="品牌")
    assert "形态卡：品牌情景" in seen["system"]

    def boom(*a, **k):  # pragma: no cover
        raise AssertionError("未知形态不该打到模型")

    monkeypatch.setattr(scriptgen, "_dispatch", boom)
    with pytest.raises(ValueError, match="不认识的形态：火星"):
        generate_draft(episode_id="EP-1", direction="x", target_seconds=60, camera_profile="dark_comedy_vertical",
                       prompt_language="zh", cast_hint=None, form="火星")


def test_forms_endpoint_lists_cards():
    rows = client.get("/api/scripts/forms").json()
    assert len(rows) == len(F.form_cards())
    assert set(rows[0]) == {"id", "name", "aliases", "defaults", "must_have", "summary"}
    vertical = next(r for r in rows if r["id"] == "vertical_drama")
    assert vertical["defaults"]["aspect_ratio"] == "9:16" and vertical["must_have"]
    assert vertical["summary"] == "9:16 · 单镜 3–8 秒 · 每分钟不超过 12 镜 · 字幕常开"


def test_draft_job_stores_form_and_rejects_unknown(tmp_path, monkeypatch):
    from webui.routers import scripts as R

    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    monkeypatch.setattr(R, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(R, "JOBS_DIR", tmp_path / "jobs")
    seen = {}

    def fake_generate_draft(**kw):
        seen.update(kw)
        raise R.ScriptGenError("没配文本模型")

    monkeypatch.setattr(R, "generate_draft", fake_generate_draft)
    monkeypatch.setattr(R.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    body = {"direction": "一个人加班", "target_seconds": 60, "camera_profile": "dark_comedy_vertical", "form": "短剧"}
    r = client.post("/api/scripts/jobs/draft", json=body)
    assert r.status_code == 200, r.text
    assert seen["form"] == "短剧"
    job = client.get(f"/api/scripts/jobs/{r.json()['job_id']}").json()
    assert job["params"]["form"] == "短剧" and job["status"] == "failed"
    bad = client.post("/api/scripts/jobs/draft", json={**body, "form": "火星形态"})
    assert bad.status_code == 400 and "不认识的形态：火星形态" in bad.json()["detail"]
    assert client.post("/api/scripts/jobs/draft", json={**body, "form": "火星形态"}, headers={"X-StoryStudio-Lang": "en"}).json()["detail"] == 'Unknown form "火星形态"'
    # 起草成功路径：assemble_spec 拿到 form → spec.form + 画幅默认值
    def ok_generate_draft(**kw):
        return {"title_zh": "t", "logline": "", "frame_style": "f", "scene_anchor": "s", "camera": "c", "neg": "n", "cast": {},
                "shots": [{"id": "S1", "dur": 5, "cast": [], "shot": "中景", "art": "a", "motion": "m", "lines": []}]}, None, 1, "settings"

    monkeypatch.setattr(R, "generate_draft", ok_generate_draft)
    r = client.post("/api/scripts/jobs/draft", json={**body, "form": "横屏"})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/scripts/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded", job.get("error")
    assert job["result"]["spec"]["form"] == "horizontal_short"
    assert job["result"]["spec"]["video_params"]["aspect_ratio"] == "16:9"


def test_summary_exposes_form_name():
    from webui.routers.project import _form_summary

    assert _form_summary("短剧") == {"id": "vertical_drama", "name": "竖屏短剧"}
    assert _form_summary(None) is None and _form_summary("火星") is None
