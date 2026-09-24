"""长单镜视频提示词的三条通用写法：形态卡构图句进视频提示词（P1）、参考图相似度百分比（P3）、风格骨架要求具体色名（P2）。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from storyvideo import forms as F
from storyvideo.prompts import compile_frame_prompt, compile_video_prompt, reference_roles_clause
from storyvideo.scriptgen import style_library
from storyvideo.spec import SpecError, load_spec
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router

client = TestClient(app)


def _raw(**extra):
    raw = {
        "id": "PP", "output_root": "output/story_video/PP", "prompt_language": "zh",
        "frame_style": "写实", "scene_anchor": "夜市摊位", "camera": "近景", "neg": "n",
        "cast": {"lin": {"name": "林小满", "block": "黑发", "asset": "content/assets/lin.png"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "她抬头", "motion": "静", "lines": []},
            {"id": "S2", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "她转身", "motion": "静", "lines": [], "ref_frame": "S1"},
        ],
    }
    raw.update(extra)
    return raw


def _spec(tmp_path, raw=None):
    raw = raw or _raw()
    (tmp_path / "content" / "assets").mkdir(parents=True, exist_ok=True)
    (tmp_path / "content" / "assets" / "lin.png").write_bytes(b"png")
    path = tmp_path / "content" / "PP.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return load_spec(path, project_root=tmp_path)


# ------------------------------------------------------------------ P1 形态卡构图句
def test_every_form_card_has_composition_zh_and_en():
    for card in F.form_cards():
        assert card["composition"].strip() and card["english"]["composition"].strip(), card["id"]
        assert "9:16" in card["composition"] or "16:9" in card["composition"]      # 画幅必须显式写出来
        other = "横版" if "9:16" in card["composition"] else "竖版"
        tail = card["composition"].split("；")[-1]
        assert "原生" in tail and "不是" in tail and other in tail                     # 句末：原生画幅，排除另一种
        english = card["english"]["composition"]
        assert "native" in english and "landscape" in english and "portrait" in english


def test_composition_sentence_joins_motion_budget_in_video_prompt(tmp_path):
    card = F.find_form("vertical_drama")
    assert F.composition_sentence({"form": "vertical_drama", "prompt_language": "zh"}) == card["composition"]
    assert F.composition_sentence({"form": "vertical_drama", "prompt_language": "en"}) == card["english"]["composition"]
    assert F.composition_sentence({}) == ""
    joined = F.form_prompt_sentences({"form": "vertical_drama", "prompt_language": "zh"})
    assert joined == card["motion_budget"] + card["composition"]
    spec = _spec(tmp_path, _raw(form="vertical_drama"))
    video = compile_video_prompt(spec, "S1")
    assert card["motion_budget"] in video and card["composition"] in video
    # 没写 form 的项目一个字不变
    assert card["composition"] not in compile_video_prompt(_spec(tmp_path), "S1")
    assert compile_frame_prompt(spec, "S1") == compile_frame_prompt(_spec(tmp_path), "S1")   # 首帧不带构图句


# ------------------------------------------------------------------ P3 参考图相似度
def test_fidelity_clause_zh_en_and_validation(tmp_path):
    raw = _raw()
    raw["shots"][1]["reference_roles"] = [{"target": "cast:lin", "control": ["脸"], "fidelity": 80}]
    spec = _spec(tmp_path, raw)
    # 相似度那句的措辞归 storyvideo.prompts，这里只守：百分数写进去了，排在「逐字对照」之后，整条以句号收
    clause = reference_roles_clause(spec, "S2")
    assert "脸逐字对照；" in clause and "80%" in clause and clause.endswith("。")
    assert clause.index("脸逐字对照") < clause.index("80%")
    raw["prompt_language"] = "en"
    spec = _spec(tmp_path, raw)
    clause = reference_roles_clause(spec, "S2")
    assert "follow face exactly; " in clause and "80%" in clause
    assert clause.index("follow face exactly") < clause.index("80%")
    # 只写 fidelity 也算一条有效说明（取整）
    raw["shots"][1]["reference_roles"] = [{"target": "cast:lin", "fidelity": 65.4}]
    spec = _spec(tmp_path, raw)
    assert "65%" in reference_roles_clause(spec, "S2") and "65.4" not in reference_roles_clause(spec, "S2")
    # 越界 / 非数拒收
    for bad in (0, 101, "high", True):
        raw["shots"][1]["reference_roles"] = [{"target": "cast:lin", "fidelity": bad}]
        with pytest.raises(SpecError):
            _spec(tmp_path, raw)


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    (tmp_path / "content" / "assets").mkdir(parents=True, exist_ok=True)
    (tmp_path / "content" / "assets" / "lin.png").write_bytes(b"png")
    raw = _raw()
    raw["shots"][1]["cast"] = ["lin"]
    (content_dir / "PP.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_shot_edit_roundtrips_fidelity_and_rejects_bad(project):
    ok = client.put("/api/episodes/storyvideo/PP/shots/S2", json={"reference_roles": [{"target": "cast:lin", "fidelity": "80"}]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["shot"]["reference_roles"] == [{"target": "cast:lin", "fidelity": 80}]
    saved = json.loads((project / "content" / "story_video" / "PP.json").read_text(encoding="utf-8"))
    assert saved["shots"][1]["reference_roles"] == [{"target": "cast:lin", "fidelity": 80}]
    bad = client.put("/api/episodes/storyvideo/PP/shots/S2", json={"reference_roles": [{"target": "cast:lin", "fidelity": 150}]})
    assert bad.status_code == 400 and "1–100" in bad.json()["detail"]
    en = client.put("/api/episodes/storyvideo/PP/shots/S2", json={"reference_roles": [{"target": "cast:lin", "fidelity": "x"}]},
                    headers={"X-StoryStudio-Lang": "en"})
    assert en.status_code == 400 and "percentage between 1 and 100" in en.json()["detail"]
    empty = client.put("/api/episodes/storyvideo/PP/shots/S2", json={"reference_roles": [{"target": "cast:lin"}]})
    assert empty.status_code == 400 and "fidelity" in empty.json()["detail"]


# ------------------------------------------------------------------ P2 风格骨架要求具体色名
def test_new_skeletons_ask_for_concrete_colour_names():
    skeletons = style_library()["frame_style_skeletons"]
    for pid in ("classic_film", "short_drama_vertical", "anime_action", "tv_drama", "suspense_thriller"):
        items = skeletons[pid]["structure_cn"]
        assert any("具体色名" in item and "5–7" in item for item in items), pid
