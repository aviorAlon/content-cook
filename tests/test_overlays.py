"""交付面遮挡区（DESIGN §27）：预设、spec 校验、位置映射与 lint、后期块接口读写（含冻结后 amend 放行）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import lint, overlays
from storyvideo.runtime import StoryVideoRunner
from storyvideo.spec import SpecError, load_spec, normalize_spec
from webui import deps
from webui.app import app
from webui.problems import problem_card
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router

client = TestClient(app)

BOTTOM = {"label": "底部文案", "x": 0, "y": 0.8, "w": 1, "h": 0.2}
RIGHT = {"label": "右侧互动栏", "x": 0.84, "y": 0.4, "w": 0.16, "h": 0.4}


def _raw(**extra):
    spec = {
        "id": "OV-T", "output_root": "output/story_video/OV-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n",
        "subtitle": {"language": "zh"},
        "cast": {"a": {"name": "阿一", "block": "黑发。"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["a"], "shot": "中景，平视", "art": "阿一看手机", "motion": "她低头",
             "lines": [{"who": "a", "zh": "还有四天。"}],
             "readable_text": [{"surface": "手机屏幕", "text": "剩余 4 天", "language": "zh", "position": "底部"}]},
            {"id": "S2", "dur": 4, "cast": ["a"], "shot": "近景，平视", "art": "阿一", "motion": "她抬头", "lines": []},
        ],
    }
    spec.update(extra)
    return spec


# ---------------------------------------------------------------- 预设与解析


def test_presets_are_well_formed_and_resolve():
    assert [p["id"] for p in overlays.preset_choices()] == list(overlays.PRESET_ORDER)
    for pid in overlays.PRESET_ORDER:
        assert not overlays.validate({"overlay_preset": pid, "overlay_regions": overlays.preset_regions(pid)})
    assert overlays.preset_regions("none") == [] and overlays.preset_regions("nope") == []
    assert overlays.regions_for({}) == [] and overlays.regions_for({"delivery": {}}) == []
    assert [r["label"] for r in overlays.regions_for({"delivery": {"overlay_preset": "douyin"}})] == ["顶部状态与标题栏", "右侧互动栏", "底部文案与进度条"]
    # 显式区域优先于预设；空的显式数组回落到预设
    assert overlays.regions_for({"delivery": {"overlay_preset": "douyin", "overlay_regions": [BOTTOM]}}) == [dict(BOTTOM, x=0.0, y=0.8, w=1.0, h=0.2)]
    assert len(overlays.regions_for({"delivery": {"overlay_preset": "douyin", "overlay_regions": []}})) == 3
    payload = overlays.payload({"overlay_regions": [BOTTOM]})
    assert payload["custom"] is True and payload["preset"] == "none" and len(payload["regions"]) == 1 and len(payload["presets"]) == 4


def test_validate_reports_each_problem():
    bad = {"overlay_preset": "weibo", "overlay_regions": [
        {"label": 3, "x": "0", "y": 0.9, "w": 0.5, "h": 0.5, "extra": 1}, "junk", {"label": "ok", "x": 0.2, "y": 0.2, "w": 0, "h": 1.5},
    ]}
    problems = overlays.validate(bad)
    text = "\n".join(problems)
    assert "overlay_preset 只能是" in text and "weibo" in text
    assert "[0].label 要是文字" in text and "[0].x 要是 0 到 1 之间的数" in text and "[0] 超出交付面底边" in text and "[0] 有不认识的字段：extra" in text
    assert "[1] 必须是对象" in text
    assert "[2].w 要大于 0" in text and "[2].h 要在 0 到 1 之间" in text
    assert overlays.validate({"overlay_regions": "x"}) == ["delivery.overlay_regions 必须是数组：[{label, x, y, w, h}]，比例 0–1"]
    assert overlays.validate({"overlay_regions": [BOTTOM] * 13})[0].startswith("delivery.overlay_regions 最多 12 块")
    assert overlays.validate(None) == [] and overlays.validate({"width": 1080}) == []


def test_normalize_spec_accepts_and_rejects(tmp_path):
    ok = _raw(delivery={"width": 1080, "height": 1920, "overlay_preset": "douyin", "overlay_regions": [BOTTOM]})
    spec = normalize_spec(ok, project_root=tmp_path)
    assert spec["delivery"]["overlay_regions"] == [BOTTOM]   # 原样保留，不改写数值
    with pytest.raises(SpecError, match="overlay_preset 只能是"):
        normalize_spec(_raw(delivery={"overlay_preset": "weibo"}), project_root=tmp_path)
    with pytest.raises(SpecError, match=r"overlay_regions\[0\] 超出交付面右边"):
        normalize_spec(_raw(delivery={"overlay_regions": [{"label": "x", "x": 0.9, "y": 0, "w": 0.5, "h": 0.1}]}), project_root=tmp_path)
    # 老项目没这两个字段：source_hash 与没有 delivery 键的写法一致（什么都不加）
    plain = normalize_spec(_raw(), project_root=tmp_path)
    assert "overlay_regions" not in (plain.get("delivery") or {}) and "overlay_preset" not in (plain.get("delivery") or {})


# ---------------------------------------------------------------- 位置映射与 lint


def test_position_rect_maps_words_roughly():
    assert overlays.position_rect("") is None and overlays.position_rect("手机屏幕") is None
    x, y, w, h = overlays.position_rect("底部")
    assert y == 0.7 and y + h == 1.0 and x == 0.2
    assert overlays.position_rect("右下角")[0] == 0.7 and overlays.position_rect("左上角")[:2] == (0.0, 0.0)
    cx, cy, cw, ch = overlays.position_rect("画面中央偏上")
    assert (cx, cy) == (0.3, 0.15) and abs(cy + ch - 0.5) < 1e-9
    assert overlays.position_rect("bottom right")[0] == 0.7
    assert overlays.position_rect("居中")[1] == 0.35
    assert overlays.covering(overlays.position_rect("居中"), [BOTTOM, RIGHT]) == []
    assert [r["label"] for r in overlays.covering(overlays.position_rect("右侧"), [BOTTOM, RIGHT])] == ["右侧互动栏"]


def test_lint_overlays_hits_and_misses():
    spec = _raw(delivery={"overlay_preset": "douyin"})
    msgs = lint.check_overlays(spec)
    assert any(m.startswith("shot S1: 可读文字「剩余 4 天」放在「底部」，会被交付面的「底部文案与进度条」挡住——") for m in msgs)
    sub = [m for m in msgs if m.startswith("全集：字幕在画面底部")]
    assert len(sub) == 1 and "调到 22% 以上（现在 7%）" in sub[0]
    # 问题卡：分类是遮挡区，镜号解析正确
    card = problem_card(msgs[0])
    assert card["kind"] == "overlay" and card["scope"] == "shot" and card["target"] == "S1" and card["fix"].startswith("挪到安全区")
    # 没配遮挡区：什么都不报；位置没方位词：不判；字幕挪到安全区：不报
    assert lint.check_overlays(_raw()) == []
    quiet = _raw(delivery={"overlay_preset": "douyin"}, subtitle={"language": "zh", "safe_bottom_percent": 25})
    quiet["shots"][0]["readable_text"][0]["position"] = "手机屏幕正中"
    assert [m for m in lint.check_overlays(quiet) if "字幕" in m] == []
    quiet["shots"][0]["readable_text"][0]["position"] = ""
    assert lint.check_overlays(quiet) == []
    # 顶部字幕 + 顶部状态栏
    top = _raw(delivery={"overlay_preset": "douyin"}, subtitle={"language": "zh", "position": "top", "top_margin_ratio": 0.02})
    assert any("字幕在画面顶部" in m and "top_margin_ratio" in m for m in lint.check_overlays(top))
    # 显式区域
    custom = _raw(delivery={"overlay_regions": [RIGHT]})
    custom["shots"][0]["readable_text"][0]["position"] = "右侧"
    assert any("「右侧互动栏」挡住" in m for m in lint.check_overlays(custom))
    assert lint.check_all(custom)   # 走整体入口不炸


# ---------------------------------------------------------------- 后期块接口


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    return tmp_path


def _write(root: Path, **extra) -> Path:
    path = root / "content" / "story_video" / "OV-T.json"
    path.write_text(json.dumps(_raw(**extra), ensure_ascii=False), encoding="utf-8")
    return path


def test_post_layers_round_trip(project):
    path = _write(project, delivery={"width": 1080, "height": 1920, "fps": 24})
    body = client.get("/api/episodes/storyvideo/OV-T/post-layers").json()
    assert body["overlays"] == {"preset": "none", "custom": False, "regions": [], "presets": overlays.preset_choices()}
    assert "delivery" in body["editable_keys"]
    # 选预设
    r = client.put("/api/episodes/storyvideo/OV-T/post-layers", json={"delivery": {"overlay_preset": "douyin", "overlay_regions": []}})
    assert r.status_code == 200 and r.json()["changed_keys"] == ["delivery"], r.text
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["delivery"] == {"width": 1080, "height": 1920, "fps": 24, "overlay_preset": "douyin"}   # 宽高帧率原样，空数组不落盘
    body = client.get("/api/episodes/storyvideo/OV-T/post-layers").json()
    assert body["overlays"]["preset"] == "douyin" and len(body["overlays"]["regions"]) == 3 and body["overlays"]["custom"] is False
    # 自定义（百分比在前端换算；后端只认比例）
    r = client.put("/api/episodes/storyvideo/OV-T/post-layers", json={"delivery": {"overlay_preset": "", "overlay_regions": [BOTTOM]}})
    assert r.status_code == 200, r.text
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["delivery"] == {"width": 1080, "height": 1920, "fps": 24, "overlay_regions": [BOTTOM]}
    body = client.get("/api/episodes/storyvideo/OV-T/post-layers").json()
    assert body["overlays"]["custom"] is True and body["overlays"]["regions"][0]["label"] == "底部文案"
    # 校验：错的不落盘
    r = client.put("/api/episodes/storyvideo/OV-T/post-layers", json={"delivery": {"overlay_regions": [{"label": "x", "x": 0.5, "y": 0.5, "w": 0.6, "h": 0.1}]}})
    assert r.status_code == 400 and "超出交付面右边" in r.json()["detail"]
    assert json.loads(path.read_text(encoding="utf-8"))["delivery"]["overlay_regions"] == [BOTTOM]
    # 不能借这条路改宽高
    r = client.put("/api/episodes/storyvideo/OV-T/post-layers", json={"delivery": {"width": 720}})
    assert r.status_code == 400 and "只能改交付面遮挡区" in r.json()["detail"]
    # 清空
    r = client.put("/api/episodes/storyvideo/OV-T/post-layers", json={"delivery": {"overlay_preset": "", "overlay_regions": []}})
    assert r.status_code == 200 and "overlay_regions" not in json.loads(path.read_text(encoding="utf-8"))["delivery"]
    # 详情页的告警/问题卡带上遮挡区
    client.put("/api/episodes/storyvideo/OV-T/post-layers", json={"delivery": {"overlay_preset": "douyin"}})
    detail = client.get("/api/episodes/storyvideo/OV-T").json()
    assert any(c["kind"] == "overlay" for c in detail["problems"])


def test_overlays_amend_after_freeze_like_subtitle(project):
    _write(project, delivery={"width": 1080, "height": 1920})
    with StoryVideoRunner(str(project)) as runner:
        runner.prepare("OV-T")
    r = client.put("/api/episodes/storyvideo/OV-T/post-layers", json={"delivery": {"overlay_preset": "kuaishou"}})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "amended" and r.json()["locked"] is True
    spec = load_spec("OV-T", project_root=project)
    assert spec["delivery"]["overlay_preset"] == "kuaishou"
    # 账本与快照对齐：再改一次仍能 amend，不会「source changed after frozen」
    r = client.put("/api/episodes/storyvideo/OV-T/post-layers", json={"delivery": {"overlay_regions": [RIGHT]}})
    assert r.status_code == 200 and r.json()["status"] == "amended", r.text


def test_i18n_covers_overlay_strings():
    import re

    from webui import i18n

    i18n._compiled = None
    han = re.compile(r"[一-鿿]")
    # 用户内容（文字、位置、自定义标签）是捕获组，原样保留；样本里用英文内容，剩下的中文就都是没翻的
    samples = [
        "shot S1: 可读文字「4 days left」放在「bottom」，会被交付面的「Bottom band」挡住——挪到安全区或换载体",
        "全集：字幕在画面底部，落在交付面的「Bottom band」里——把字幕改到顶部，或把底部安全边距调到 22% 以上（现在 7%），或换遮挡区预设",
        "全集：字幕在画面顶部，落在交付面的「Top band」里——把字幕改到底部，或调大 top_margin_ratio 避开，或换遮挡区预设",
        "全集：字幕在画面底部，落在交付面的「Side column」里——缩窄字幕（减小字号或行宽），或换遮挡区预设",
        "这里只能改交付面遮挡区（overlay_preset / overlay_regions），不能改 delivery 的 width",
        "改动没通过校验，没有写盘：delivery.overlay_regions[0] 超出交付面右边：x + w = 1.1 > 1",
        "delivery.overlay_preset 只能是 none / douyin / kuaishou / shipinhao，现在是「weibo」",
        "抖音（竖屏）", "右侧互动栏",
    ]
    untranslated = [s for s in samples if han.search(i18n.tr(s, "en"))]
    assert not untranslated, untranslated
