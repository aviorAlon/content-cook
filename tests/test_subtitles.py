"""字幕样式：默认值合并、校验、颜色归一、字体解析；装配滤镜吃描边/边距；后期块接口能存能拦；制作中走 amend。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import subtitles as SUB
from storyvideo.runtime import POST_ONLY_KEYS, StoryVideoRunner
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router

client = TestClient(app)


def test_effective_style_and_colors():
    style = SUB.effective_style({"chinese_font_size": 52, "chinese_color": "#ffcc00", "outline_color": "111111"})
    assert style["chinese_font_size"] == 52 and style["language"] == "zh" and style["position"] == "bottom"
    assert style["chinese_color"] == "0xFFCC00" and style["outline_color"] == "0x111111" and style["english_color"] == "0xFFD84D"
    assert SUB.normalize_color("garbage", "0xABCDEF") == "0xABCDEF"
    assert SUB.css_color("0xFFCC00") == "#FFCC00"
    assert SUB.effective_style(None) == SUB.effective_style({})


def test_validate(tmp_path):
    assert SUB.validate(None) == [] and SUB.validate({}) == []
    assert SUB.validate("x") == ["subtitle 必须是对象"]
    problems = SUB.validate({"language": "fr", "position": "middle", "chinese_font_size": 999, "band_alpha": 2, "outline_color": "red",
                             "font_path": str(tmp_path / "nope.ttf"), "max_lines": "abc"})
    joined = " ".join(problems)
    assert "语言" in joined and "位置" in joined and "chinese_font_size" in joined and "band_alpha" in joined
    assert "outline_color" in joined and "字体文件不存在" in joined and "max_lines 要是数字" in joined
    font = tmp_path / "ok.ttf"; font.write_bytes(b"f")
    assert SUB.validate({"language": "bilingual", "position": "top", "chinese_font_size": 40, "outline_width": 3, "band_alpha": 0,
                         "chinese_color": "0xFFFFFF", "font_path": str(font)}) == []


def test_resolve_font_and_available(tmp_path, monkeypatch):
    font = tmp_path / "mine.ttf"; font.write_bytes(b"f")
    assert SUB.resolve_font({"font_path": str(font)}) == str(font.resolve())
    monkeypatch.setattr(SUB, "FONT_CANDIDATES", ((str(tmp_path / "missing.ttf"), "缺"), (str(font), "有")))
    assert SUB.resolve_font({}) == str(font)
    assert SUB.available_fonts() == [{"path": str(font), "label": "有"}]
    monkeypatch.setattr(SUB, "FONT_CANDIDATES", ())
    assert SUB.resolve_font({}, fallback="fb") == "fb"


def test_assembly_filter_uses_outline_and_bottom_margin(tmp_path):
    from storyvideo.assembly import _subtitle_filters

    text_dir = tmp_path / "t"; text_dir.mkdir()
    font = tmp_path / "f.ttf"; font.write_bytes(b"f")
    lines = [{"zh": "你好。", "en": "Hi."}]
    filters = _subtitle_filters(shot_id="S1", lines=lines, windows=[(0.0, 1.0)], text_directory=text_dir, font_path=font,
                                width=1080, height=1920, band_alpha=0.0, language="zh", chinese_font_size=40,
                                outline_color="0xFF0000", outline_width=6, safe_bottom_percent=10)
    draw = next(f for f in filters if f.startswith("drawtext="))
    assert "borderw=6" in draw and "bordercolor=0xFF0000@0.95" in draw and "fontsize=40" in draw
    # 底部边距 10% → 192px：y = 1920 - 192 - 文字高度
    assert int(draw.split(":y=")[1].split(":")[0]) == 1920 - 192 - int(40 * 1.2)
    default = _subtitle_filters(shot_id="S1", lines=lines, windows=[(0.0, 1.0)], text_directory=text_dir, font_path=font,
                                width=1080, height=1920, band_alpha=0.2, language="zh")
    assert "borderw=5" in next(f for f in default if f.startswith("drawtext=")) and "bordercolor=0x111111@0.95" in default[1]


def test_subtitle_is_a_post_only_key():
    assert "subtitle" in POST_ONLY_KEYS


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
    spec = {
        "id": "SUB-T", "output_root": "output/story_video/SUB-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"a": {"name": "阿一", "block": "黑发。"}},
        "shots": [{"id": "S1", "dur": 4, "cast": ["a"], "shot": "近景", "art": "a", "motion": "m", "lines": [{"who": "a", "zh": "你好。"}]}],
    }
    (content_dir / "SUB-T.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_post_layers_subtitle_roundtrip_and_validation(project):
    got = client.get("/api/episodes/storyvideo/SUB-T/post-layers").json()
    assert got["subtitle"] == {"language": "zh"} and got["subtitle_effective"]["chinese_font_size"] == 46
    assert "subtitle" in got["editable_keys"] and isinstance(got["fonts_available"], list) and got["subtitle_defaults"]["outline_width"] == 4
    r = client.put("/api/episodes/storyvideo/SUB-T/post-layers", json={"subtitle": {"language": "zh", "chinese_font_size": 52, "outline_width": 3, "chinese_color": "#FFCC00", "band_alpha": 0}})
    assert r.status_code == 200, r.text
    assert r.json()["changed_keys"] == ["subtitle"]
    saved = json.loads((project / "content" / "story_video" / "SUB-T.json").read_text(encoding="utf-8"))
    assert saved["subtitle"]["chinese_font_size"] == 52 and saved["shots"][0]["dur"] == 4
    bad = client.put("/api/episodes/storyvideo/SUB-T/post-layers", json={"subtitle": {"language": "fr"}})
    assert bad.status_code == 400 and "字幕语言" in bad.json()["detail"]
    assert json.loads((project / "content" / "story_video" / "SUB-T.json").read_text(encoding="utf-8"))["subtitle"]["chinese_font_size"] == 52


def test_subtitle_change_amends_while_in_production(project):
    with StoryVideoRunner(project) as runner:
        runner.prepare("SUB-T")
    r = client.put("/api/episodes/storyvideo/SUB-T/post-layers", json={"subtitle": {"language": "zh", "position": "top", "chinese_font_size": 40}})
    assert r.status_code == 200, r.text
    assert r.json()["locked"] is True and r.json()["status"] == "amended"
    assert client.get("/api/episodes/storyvideo/SUB-T").json()["locked"] is True
