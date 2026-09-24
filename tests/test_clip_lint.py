"""单条视频的静态检查（契约 docs/clip-spec.md §3）：每条规则命中与不命中各一次，外加通用四项与问题卡形状。"""

from __future__ import annotations

from clip import lint as L
from clip import prompts as P
from clip import spec as S
from tests.test_clip_spec import build, sample
from webui.problems import problem_card, problem_cards


def beats(*spans) -> list[dict]:
    return [{"id": f"B{i:02d}", "start": a, "end": b, "shot": "中景", "picture": f"第 {i} 拍"}
            for i, (a, b) in enumerate(spans, 1)]


# ---------------------------------------------------------------------------- 时间轴


def test_gap_is_reported(tmp_path):
    ok = build(tmp_path, beats=beats((0, 1.5), (1.5, 3)))
    assert L.check_timeline(ok) == []
    bad = build(tmp_path, beats=beats((0, 1.5), (2, 3)))
    (warning,) = L.check_timeline(bad)
    assert warning.startswith("全片：时间轴在 1.5–2 秒是空的（B01 与 B02 之间）——")
    assert "首尾相接" in warning


def test_overlap_is_reported(tmp_path):
    bad = build(tmp_path, beats=beats((0, 2), (1.5, 3)))
    (warning,) = L.check_timeline(bad)
    assert warning.startswith("beat B02: 与 B01 在 1.5–2 秒重叠——")
    # 0.05 秒以内的对齐误差不算
    assert L.check_timeline(build(tmp_path, beats=beats((0, 1.5), (1.53, 3)))) == []


def test_duration_mismatch(tmp_path):
    params = {"model": "@video", "duration": 10, "resolution": "1080p"}
    bad = build(tmp_path, beats=beats((0, 1.5), (1.5, 3)), video_params=params)
    (warning,) = L.check_duration(bad)
    assert "节拍覆盖 3 秒，出片时长写的是 10 秒" in warning
    # 差 1 秒以内不告警；没有节拍也不告警
    ok = build(tmp_path, beats=beats((0, 5), (5, 9.5)), video_params=params)
    assert L.check_duration(ok) == []
    assert L.check_duration(build(tmp_path, beats=[], video_params=params)) == []


# ---------------------------------------------------------------------------- 色板 / 连接词 / 最后一帧 / 文字政策


def test_palette_needs_five_colour_names(tmp_path):
    assert L.check_palette(build(tmp_path)) == []
    short = build(tmp_path, **{"global": {**sample()["global"], "palette": ["雾蓝", "砖红"]}})
    (warning,) = L.check_palette(short)
    assert warning.startswith("全片：色板只有 2 个色名——色板写 5–7 个具体色名")
    # 项目没写色板但选了风格卡 → 用卡的色板，不告警
    card = build(tmp_path, style="cinematic_key_art", **{"global": {**sample()["global"], "palette": []}})
    assert L.check_palette(card) == []


def test_connectives_split_a_beat_in_two(tmp_path):
    assert L.check_connectives(build(tmp_path)) == []
    bad = build(tmp_path, beats=[{"id": "B01", "start": 0, "end": 3, "shot": "中景", "picture": "电车进站，然后车门打开"}])
    (warning,) = L.check_connectives(bad)
    assert warning.startswith("beat B01: 画面里有连接词「然后」——一个节拍只做一件事")
    en = build(tmp_path, beats=[{"id": "B01", "start": 0, "end": 3, "picture": "the tram pulls in and then the doors open"}])
    assert "and then" in L.check_connectives(en)[0]
    # 词里带 then 的英文单词不算（strengthen）
    fine = build(tmp_path, beats=[{"id": "B01", "start": 0, "end": 3, "picture": "the morning light strengthens"}])
    assert L.check_connectives(fine) == []


def test_last_frame_must_be_written(tmp_path):
    assert L.check_last_frame(build(tmp_path)) == []
    bad = build(tmp_path, continuity={**sample()["continuity"], "last_frame": ""})
    (warning,) = L.check_last_frame(bad)
    # 段标题的措辞归编译器（clip.prompts.HEAD_*），这里只认「全片」范围与这条规则本身
    assert warning.startswith("全片：") and "没写最后一帧——" in warning


def test_text_policy_contradiction(tmp_path):
    assert L.check_text_policy(build(tmp_path)) == []                      # 「字只有店招和片名」不矛盾
    bad = build(tmp_path, **{"global": {**sample()["global"], "text_policy": "全片无文字、无字幕"}})
    (warning,) = L.check_text_policy(bad)
    assert warning.startswith("beat B01: 要在画面里出现文字「GOOD MORNING」，文字政策却写着全片无文字——")
    # 「无水印」「无字幕」说的是别的东西：跟画面里的英文片名不矛盾
    logo = build(tmp_path, **{"global": {**sample()["global"], "text_policy": "片头一行英文片名；不打水印，也不叠字幕"}})
    assert L.check_text_policy(logo) == []


# ---------------------------------------------------------------------------- 通用四项（imagecraft.lint）


def test_shared_checks_run_on_what_the_user_wrote(tmp_path):
    """否定式描述 / 画面文字没加引号 / 数字镜头参数（nano_banana）——都查用户写的正文。"""

    negative = build(tmp_path, beats=[{"id": "B01", "start": 0, "end": 3, "picture": "街道慢慢热闹，不要出现路人"}])
    assert any("否定式描述" in w and w.startswith("全片：") for w in L.check_shared(negative))
    quoted = build(tmp_path, beats=[{"id": "B01", "start": 0, "end": 3, "picture": "店招上面写着 OPEN 24 HOURS"}])
    assert any("没加引号" in w for w in L.check_shared(quoted))
    lens = build(tmp_path, dialect="nano_banana", **{"global": {**sample()["global"], "look": "50mm 镜头"}})
    assert any("数字镜头参数" in w for w in L.check_shared(lens))
    assert L.check_shared(build(tmp_path)) == []


def test_generated_boilerplate_does_not_lint_itself(tmp_path):
    """编译器自己加的「排除另一种画幅」那句（中英）是产品决定，不该被当成否定式描述。"""

    for spec, zh in ((build(tmp_path), True), (build(tmp_path, prompt_language="en"), False)):
        needle = P.forbidden_aspect("16:9", zh)
        assert needle in P.compile_clip_prompt(spec)
        assert L.check_shared(spec) == []


# ---------------------------------------------------------------------------- 汇总 / 问题卡


def test_report_and_notes(tmp_path):
    clean = build(tmp_path)
    report = L.lint_report(clean)
    assert report["warnings"] == [] and report["notes"] == []
    assert report["stats"] == {"beats": 2, "covered": 3.0, "duration": 3, "palette": 6, "dialect": "generic",
                               "prompt_chars": len(P.compile_clip_prompt(clean))}
    empty = S.normalize({"title": "空", "style_anchor": "写实"}, project_root=tmp_path, clip_id="CL-T-8")
    notes = L.lint_notes(empty)
    assert any("还没有节拍" in n for n in notes) and any("没写转场载体" in n for n in notes)


def test_every_rule_fires_together(tmp_path):
    bad = build(tmp_path,
                beats=[{"id": "B01", "start": 0, "end": 2, "picture": "电车进站，然后车门打开", "text": "GO"},
                       {"id": "B02", "start": 3, "end": 4, "picture": "近景"}],
                continuity={**sample()["continuity"], "last_frame": ""},
                video_params={"model": "@video", "duration": 30, "resolution": "1080p"},
                **{"global": {**sample()["global"], "palette": ["雾蓝"], "text_policy": "全片无文字"}})
    warnings = L.lint_clip(bad)
    for needle in ("时间轴在 2–3 秒是空的", "节拍覆盖 4 秒", "色板只有 1 个色名", "连接词「然后」",
                   "没写最后一帧", "文字政策却写着全片无文字"):
        assert any(needle in w for w in warnings), needle
    assert all("——" in w for w in warnings)


def test_warnings_become_problem_cards(tmp_path):
    bad = build(tmp_path, beats=beats((0, 2), (1, 4)), continuity={**sample()["continuity"], "last_frame": ""})
    cards = problem_cards(L.lint_clip(bad))
    beat_card = next(c for c in cards if c["scope"] == "beat")
    assert beat_card["target"] == "B02" and beat_card["kind"] == "clip" and beat_card["fix"]
    assert "重叠" in beat_card["impact"] and not beat_card["impact"].startswith("beat ")
    clip_card = next(c for c in cards if c["scope"] == "episode")
    assert clip_card["target"] == "" and not clip_card["impact"].startswith("全片")
    # 色板那条归「出图」（跟绘本 / 图片集同一分类）
    short = build(tmp_path, **{"global": {**sample()["global"], "palette": []}})
    assert problem_card(L.check_palette(short)[0])["kind"] == "image"
