"""三段式提示词编译（契约 docs/clip-spec.md §2）。

判据见 docs/prompt-patterns.md：P1（时长 + 画幅显式 + 排除另一种画幅 + 安全区）、P2（色板 5–7 个具体色名）、
P3（要出现在画面里的文字用引号原样写）。方言只有 generic 与 nano_banana 两种，未知模型走 generic。

段标题与编译器固定加的句子（开头的时长句、「排除另一种画幅」、一镜到底 / 硬切的说明）归 ``clip.prompts``：
这里通过 ``HEAD_*`` / ``OPENER`` / ``ONE_TAKE`` / ``HARD_CUTS`` / ``forbidden_aspect`` 取，只守结构与判据，不守措辞。
"""

from __future__ import annotations

import pytest

from clip import prompts as P
from clip import spec as S
from tests.test_clip_spec import build, sample

G, T, C = P.HEAD_GLOBAL[0], P.HEAD_TIMELINE[0], P.HEAD_CONTINUITY[0]


def _sentence_with(text: str, needle: str) -> str:
    """含 ``needle`` 的那一句（按中文句号 / 英文句点切）。"""

    for sentence in text.replace(". ", "。").split("。"):
        if needle in sentence:
            return sentence
    raise AssertionError(f"{needle!r} not in {text!r}")


# ---------------------------------------------------------------------------- 三段


def test_three_sections_in_order(tmp_path):
    text = P.compile_clip_prompt(build(tmp_path))
    assert G and T and C and len({G, T, C}) == 3
    assert text.index(G) < text.index(T) < text.index(C)
    assert text.count(G) == 1
    # 每段都在自己的行上，节拍一拍一行
    lines = text.splitlines()
    assert lines[0].startswith(G) and lines[1].startswith(T + "0–1.5 秒：")
    assert lines[2].startswith("1.5–3 秒：") and lines[3].startswith(C)


def test_global_section_carries_p1_and_p2(tmp_path):
    spec = build(tmp_path)
    text = P.global_section(spec)
    head = text[len(G):].split("。")[0]
    assert head.startswith(P.OPENER[0].format(duration=3))              # 编译器的开头句
    assert "3 秒" in head and "1080p" in head                            # 时长 + 分辨率显式，写在第一句
    # P1：画幅、排除另一种画幅、安全区在同一句里说完
    frame = _sentence_with(text, P.forbidden_aspect("16:9", True))
    assert "16:9" in frame and "行人和关键动作落在画面中部，四周留出余量" in frame
    assert frame.index("16:9") < frame.index(P.forbidden_aspect("16:9", True))
    assert "色板：雾蓝、青灰、米白、砖红、暖橙、铜金" in text            # P2
    assert "清冷写实的城市纪录片质感" in text and "像一段从天亮拍到热闹的长镜头" in text
    assert "画面上的字只有店招和片名，片名用英文大写" in text and "街道环境声打底，配一段轻柔的钢琴" in text


@pytest.mark.parametrize("ratio,other", [
    ("16:9", ("竖",)), ("9:16", ("横",)), ("1:1", ("横", "竖")),
    ("4:3", ("竖",)), ("3:4", ("横",)), ("21:9", ("竖",)),
])
def test_every_aspect_ratio_forbids_the_other_one(tmp_path, ratio, other):
    spec = build(tmp_path, **{"global": {**sample()["global"], "aspect_ratio": ratio}})
    forbidden = P.forbidden_aspect(ratio, True)
    assert all(word in forbidden for word in other) and ratio not in forbidden
    frame = _sentence_with(P.compile_clip_prompt(spec), forbidden)
    assert ratio in frame and frame.index(ratio) < frame.index(forbidden)
    # 表外的画幅走兜底句：仍然写死「只有这一种」
    assert "5:4" in P.forbidden_aspect("5:4", True) and "5:4" in P.forbidden_aspect("5:4", False)


def test_timeline_line_shape_and_quoted_on_screen_text(tmp_path):
    spec = build(tmp_path)
    line = P.beat_line(spec, spec["beats"][0])
    assert line == '0–1.5 秒：航拍大远景，薄雾里的城市天际线；声音：远处一声汽笛；画面文字："GOOD MORNING"。'
    # P3：画面文字带引号；没写文字的那一拍不出现「画面文字」
    assert '画面文字' not in P.beat_line(spec, spec["beats"][1])
    assert P.beat_preview(spec)[1]["id"] == "B02"


def test_continuity_section(tmp_path):
    spec = build(tmp_path)
    text = P.continuity_section(spec)
    off = build(tmp_path, continuity={**sample()["continuity"], "one_take": False})
    off_text = P.continuity_section(off)
    # one_take 开着：先声明一镜到底（具体措辞归编译器），不是硬切那句
    assert text.startswith(C + P.ONE_TAKE[0]) and P.HARD_CUTS[0] not in text
    assert off_text.startswith(C + P.HARD_CUTS[0]) and P.ONE_TAKE[0] not in off_text
    assert text.split("借驶过的电车车身挡一下镜头")[0] != off_text.split("借驶过的电车车身挡一下镜头")[0]
    assert "借驶过的电车车身挡一下镜头" in text
    assert "最后一帧：停在整条街被阳光照亮的全景上" in text
    assert "禁止：真实品牌的标识、夜景霓虹" in text


def test_empty_sections_disappear(tmp_path):
    spec = S.normalize({"title": "空", "style_anchor": "写实"}, project_root=tmp_path, clip_id="CL-T-9")
    text = P.compile_clip_prompt(spec)
    assert T not in text                                  # 没有节拍就没有分段时间线
    assert G in text and C in text
    assert "禁止：" not in text and "最后一帧" not in text


# ---------------------------------------------------------------------------- 中英


def test_english_prompt(tmp_path):
    spec = build(tmp_path, prompt_language="en")
    text = P.compile_clip_prompt(spec)
    head = text[len(P.HEAD_GLOBAL[1]):].split(". ")[0]
    assert text.startswith(P.HEAD_GLOBAL[1] + P.OPENER[1].format(duration=3)) and "1080p" in head
    frame = _sentence_with(text, P.forbidden_aspect("16:9", False))
    assert "16:9" in frame and frame.index("16:9") < frame.index(P.forbidden_aspect("16:9", False))
    assert "Palette: 雾蓝, 青灰" in text                   # 色名原样（用户自己写的字）
    assert (P.HEAD_TIMELINE[1] + '0–1.5s: 航拍大远景, 薄雾里的城市天际线; sound: 远处一声汽笛; on-screen text: "GOOD MORNING".') in text
    assert (P.HEAD_CONTINUITY[1] + P.ONE_TAKE[1]) in text and P.HARD_CUTS[1] not in text
    assert "Last frame: 停在整条街被阳光照亮的全景上" in text
    assert "Do not include: 真实品牌的标识, 夜景霓虹" in text
    assert G not in text


# ---------------------------------------------------------------------------- 风格卡


def test_style_card_anchor_and_palette_are_applied(tmp_path):
    from imagecraft.cards import find_card

    card = find_card("cinematic_key_art")
    spec = build(tmp_path, style="cinematic_key_art", style_anchor="不该出现的自写锚句",
                 **{"global": {**sample()["global"], "palette": []}})
    text = P.compile_clip_prompt(spec)
    assert P.style_anchor(spec) == card["anchor_zh"] and card["anchor_zh"] in text
    assert "不该出现的自写锚句" not in text
    # 项目没写色板 → 用卡的色板（卡本来就是 5–7 个具体色名）
    assert P.palette(spec) == [str(c) for c in card["palette"]]
    assert "色板：" + "、".join(card["palette"]) in text
    # 项目写了色板 → 以项目为准
    own = build(tmp_path, style="cinematic_key_art")
    assert P.palette(own) == sample()["global"]["palette"]
    # 英文项目用英文锚句
    en = build(tmp_path, style="cinematic_key_art", prompt_language="en")
    assert card["anchor_en"] in P.compile_clip_prompt(en)


# ---------------------------------------------------------------------------- 方言


@pytest.mark.parametrize("model,expected", [
    ("@video", "generic"), ("doubao-seedance-2-0", "generic"), ("veo-3", "generic"),
    ("gemini-3-pro-image", "nano_banana"), ("nano-banana-2", "nano_banana"),
    ("gpt-image-2", "generic"),                       # 出图那套分段带标签对视频没判据，折回 generic
])
def test_dialect_is_guessed_from_the_model(tmp_path, model, expected):
    spec = build(tmp_path, video_params={"model": model, "duration": 3, "resolution": "1080p"})
    assert P.dialect_for(spec) == expected


def test_explicit_dialect_wins(tmp_path):
    spec = build(tmp_path, dialect="nano_banana")
    assert P.dialect_for(spec) == "nano_banana"
    assert P.resolve_dialect("nano-banana", "generic") == "generic"
    assert P.resolve_dialect("what-is-this", "auto") == "generic"


def test_nano_banana_strips_lens_numbers_and_adds_ratio_line(tmp_path):
    body = {**sample()["global"], "look": "50mm 镜头，f/2.8 浅景深，ISO 800 颗粒"}
    spec = build(tmp_path, dialect="nano_banana", **{"global": body})
    text = P.compile_clip_prompt(spec)
    for number in ("50mm", "f/2.8", "ISO 800"):
        assert number not in text
    assert "镜头" in text and "浅景深" in text            # 只删数字，观感描述留着
    assert text.endswith("\n\nAspect ratio: 16:9")
    # generic 不动数字，也不加画幅尾行
    plain = build(tmp_path, **{"global": body})
    assert "50mm" in P.compile_clip_prompt(plain) and "Aspect ratio:" not in P.compile_clip_prompt(plain)


# ---------------------------------------------------------------------------- 首帧 / 请求


def test_frame_prompt_is_global_plus_first_beat(tmp_path):
    spec = build(tmp_path, first_frame={"mode": "generate"})
    text = P.compile_frame_prompt(spec)
    assert text.startswith(G + "单帧静止画面（这条视频的首帧）")
    assert "3 秒" not in text and "1080p" not in text          # 首帧不写时长与分辨率
    assert "街道环境声打底" not in text                   # 首帧不带声音说明
    assert '航拍大远景，薄雾里的城市天际线；画面文字："GOOD MORNING"。' in text
    assert "卷帘门拉起" not in text                       # 只有第一拍
    # 首帧是一张静止图：画幅句用不带时间的那种说法
    assert P.forbidden_aspect("16:9", True, still=True) in text
    assert P.forbidden_aspect("16:9", True) not in text and "从头到尾" not in text
    en = P.compile_frame_prompt(build(tmp_path, prompt_language="en", first_frame={"mode": "generate"}))
    assert P.forbidden_aspect("16:9", False, still=True) in en and "first frame to the last" not in en
    for ratio in ("9:16", "1:1", "5:4"):
        assert "从头到尾" not in P.forbidden_aspect(ratio, True, still=True)
        assert "first frame to the last" not in P.forbidden_aspect(ratio, False, still=True)
    assert "5:4" in P.forbidden_aspect("5:4", True, still=True) and "5:4" in P.forbidden_aspect("5:4", False, still=True)
    # 自己写了首帧提示词就用它替掉「第一个节拍」那部分
    own = build(tmp_path, first_frame={"mode": "generate", "prompt": "只画天际线剪影"})
    assert "只画天际线剪影" in P.compile_frame_prompt(own)
    assert "航拍大远景" not in P.compile_frame_prompt(own)


def test_request_data(tmp_path):
    spec = build(tmp_path, first_frame={"mode": "upload", "asset": "output/clip/CL-T-1/00_frame/a.png"})
    video = P.video_request_data(spec)
    assert video["name"] == "CL-T-1" and video["duration"] == 3 and video["aspect_ratio"] == "16:9"
    assert video["resolution"] == "1080p" and video["generate_audio"] is False and video["model"] == "@video"
    assert video["prompt"] == P.compile_clip_prompt(spec)
    assert video["first_frame_mode"] == "upload" and video["first_frame_asset"].endswith("a.png")
    frame = P.frame_request_data(spec)
    assert frame["name"] == "CL-T-1-frame" and frame["model"] == "@image" and frame["image_size"] == "2K"
    assert frame["aspect_ratio"] == "16:9" and frame["prompt"] == P.compile_frame_prompt(spec)
