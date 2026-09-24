"""画面文字政策（DESIGN §20，融合 drama-skills 文字政策 / IMG-07）：shot.readable_text 非空时首帧尾句改成正面声明、
视频提示词加「保持清晰可读」、方言字幕禁令只禁叠加层；没声明的镜一个字都不变；lint 三项；编辑接口校验与回读。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import lint
from storyvideo.prompts import (
    NO_TEXT_TAIL_CN,
    NO_TEXT_TAIL_EN,
    compile_frame_prompt,
    compile_video_prompt,
    video_prompt_parts,
)
from storyvideo.spec import load_spec
from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router

ROOT = Path(__file__).resolve().parents[1]
PHONE = {"surface": "手机屏幕", "text": "剩余 4 天 23:59:58", "language": "zh", "position": "画面中央偏上"}
CONTRACT = {"surface": "合同签名栏", "text": "林小满", "language": "zh"}
ZH_TAIL = ("画面里唯一允许出现的文字：《剩余 4 天 23:59:58》，位于手机屏幕（画面中央偏上），字迹清晰可读；"
           "除此之外画面任何位置不出现其他文字、字母、数字或符号。")
ZH_VIDEO = "手机屏幕上的文字《剩余 4 天 23:59:58》保持清晰可读、内容不变。"


def _spec(tmp_path, *, readable=None, shot_id="S2_WISH", language="zh", **video_params):
    raw = json.loads((ROOT / "examples" / "episodes" / "first_episode.json").read_text(encoding="utf-8-sig"))
    raw["id"] = "RT-T"
    raw["output_root"] = "output/story_video/RT-T"
    raw["prompt_language"] = language
    raw["video_params"] = {**raw.get("video_params", {}), **video_params}
    if readable is not None:
        next(s for s in raw["shots"] if s["id"] == shot_id)["readable_text"] = readable
    content = tmp_path / "content" / "story_video"
    content.mkdir(parents=True, exist_ok=True)
    (content / "RT-T.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return load_spec("RT-T", project_root=tmp_path)


# ---------------------------------------------------------------- 编译


def test_without_readable_text_nothing_changes(tmp_path):
    spec = _spec(tmp_path)
    for shot in spec["shots"]:
        assert compile_frame_prompt(spec, shot["id"]).endswith(NO_TEXT_TAIL_CN)
        assert video_prompt_parts(spec, shot["id"])["readable_text"] == ""
        assert "保持清晰可读" not in compile_video_prompt(spec, shot["id"])
    # 空列表 / 全是废条目 = 没声明
    spec2 = _spec(tmp_path, readable=[{}, {"surface": "", "text": "x"}, "junk"])
    assert compile_frame_prompt(spec2, "S2_WISH").endswith(NO_TEXT_TAIL_CN)
    assert video_prompt_parts(spec2, "S2_WISH")["readable_text"] == ""


def test_zh_frame_and_generic_video(tmp_path):
    spec = _spec(tmp_path, readable=[PHONE], prompt_dialect="generic")
    frame = compile_frame_prompt(spec, "S2_WISH")
    assert frame.endswith(ZH_TAIL) and NO_TEXT_TAIL_CN not in frame
    video = compile_video_prompt(spec, "S2_WISH")
    parts = video_prompt_parts(spec, "S2_WISH")
    assert parts["readable_text"] == ZH_VIDEO
    assert ZH_VIDEO in video and video.index(ZH_VIDEO) > video.index(parts["lock"])   # 通用方言：接在锁句之后
    # 只影响声明了的那一镜
    assert compile_frame_prompt(spec, "S1_OVERTIME").endswith(NO_TEXT_TAIL_CN)
    assert "保持清晰可读" not in compile_video_prompt(spec, "S1_OVERTIME")


def test_zh_multiple_entries_and_language_tags(tmp_path):
    spec = _spec(tmp_path, readable=[PHONE, {**CONTRACT, "language": "en", "text": "LIN"}, {"surface": "榜单", "text": "01", "language": "digits"}])
    frame = compile_frame_prompt(spec, "S2_WISH")
    assert ("画面里唯一允许出现的文字：《剩余 4 天 23:59:58》，位于手机屏幕（画面中央偏上）；《LIN》（英文），位于合同签名栏；"
            "《01》（数字），位于榜单，字迹清晰可读；除此之外画面任何位置不出现其他文字、字母、数字或符号。") in frame
    assert "手机屏幕上的文字《剩余 4 天 23:59:58》、合同签名栏上的文字《LIN》、榜单上的文字《01》保持清晰可读、内容不变。" in compile_video_prompt(spec, "S2_WISH")


def test_zh_dialects_keep_subtitle_ban_but_allow_named_text(tmp_path):
    for dialect in ("minimax-h3", "seedance"):
        spec = _spec(tmp_path, readable=[PHONE], model="MiniMax-H3", resolution="768P", aspect_ratio="adaptive", prompt_dialect=dialect)
        text = compile_video_prompt(spec, "S2_WISH")
        assert "手机屏幕上的文字《剩余 4 天 23:59:58》保持清晰可读、内容不变" in text, dialect
        assert "除上面指明的文字外" in text and "画面里没有任何可读文字" not in text, dialect   # 两种方言的禁字句写法不同
        assert "字幕" in text, dialect
        # 没声明的镜：老句子原样
        other = compile_video_prompt(spec, "S1_OVERTIME")
        assert "画面里没有任何可读文字" in other and "保持清晰可读" not in other, dialect


def test_en_frame_video_and_dialects(tmp_path):
    spec = _spec(tmp_path, language="en", readable=[{"surface": "phone screen", "text": "4 days left", "language": "en", "position": "upper centre"}], prompt_dialect="generic")
    frame = compile_frame_prompt(spec, "S2_WISH")
    assert frame.endswith('The only text allowed in the image: "4 days left" on the phone screen (upper centre), clearly legible; '
                          "no other text, letters, numbers or symbols anywhere else in the image. ")
    assert NO_TEXT_TAIL_EN not in frame
    assert 'The text "4 days left" on the phone screen stays clearly legible and unchanged.' in compile_video_prompt(spec, "S2_WISH")
    assert compile_frame_prompt(spec, "S1_OVERTIME").endswith(NO_TEXT_TAIL_EN)
    # 中文字 + 数字在英文项目里带语言标签；两条用 and 连
    spec2 = _spec(tmp_path, language="en", readable=[{"surface": "phone screen", "text": "剩余 4 天", "language": "zh"}, {"surface": "scoreboard", "text": "01", "language": "digits"}])
    assert '"剩余 4 天" (Chinese characters) on the phone screen; "01" (digits) on the scoreboard, clearly legible' in compile_frame_prompt(spec2, "S2_WISH")
    assert 'The text "剩余 4 天" on the phone screen and the text "01" on the scoreboard stay clearly legible and unchanged.' in compile_video_prompt(spec2, "S2_WISH")
    for dialect in ("minimax-h3", "seedance"):
        spec3 = _spec(tmp_path, language="en", readable=[{"surface": "phone screen", "text": "4 days left", "language": "en"}],
                      model="MiniMax-H3", resolution="768P", aspect_ratio="adaptive", prompt_dialect=dialect)
        text = compile_video_prompt(spec3, "S2_WISH")
        assert "stays clearly legible and unchanged" in text and "apart from the text named above" in text, dialect
        assert "no readable text anywhere" not in text and "no readable text appears anywhere" not in text, dialect


# ---------------------------------------------------------------- lint


def _lint_spec(shot: dict) -> dict:
    base = {"id": "S1", "dur": 4, "cast": [], "shot": "中景，平视", "art": "桌上一部手机", "motion": "唯一动作：抬手"}
    return {"prompt_language": "zh", "cast": {}, "shots": [{**base, **shot}]}


def test_lint_silent_without_readable_text():
    assert lint.check_readable_text(_lint_spec({})) == []
    assert lint.check_readable_text(_lint_spec({"art": "桌上一部手机，无文字"})) == []


def test_lint_long_text_contradiction_and_episode_reminder():
    w = lint.check_readable_text(_lint_spec({"readable_text": [PHONE]}))
    assert len(w) == 1 and "很多模型对中文字形不稳" in w[0] and w[0].startswith("shot S1")
    long = {"surface": "合同", "text": "本合同自双方签字之日起生效，有效期三年，到期自动续签一次", "language": "zh"}
    w = lint.check_readable_text(_lint_spec({"readable_text": [long]}))
    assert any("个字" in x and "拆成几处短文字" in x for x in w)
    w = lint.check_readable_text(_lint_spec({"readable_text": [PHONE], "art": "桌上一部手机，画面无文字", "neg_extra": "no text, blurry"}))
    clash = [x for x in w if "互相打架" in x]
    assert len(clash) == 1 and "无文字" in clash[0] and "no text" in clash[0]
    # 全是废条目：提示补全，不发整集提醒
    w = lint.check_readable_text(_lint_spec({"readable_text": [{"surface": "手机"}]}))
    assert len(w) == 1 and "没有一条合法条目" in w[0]
    # 接进 check_all
    assert any("中文字形不稳" in x for x in lint.check_all(_lint_spec({"readable_text": [PHONE]})))


# ---------------------------------------------------------------- 编辑接口

client = TestClient(app)


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    spec = {
        "id": "RT-API", "output_root": "output/story_video/RT-API", "prompt_language": "zh",
        "camera_profile": "dark_comedy_vertical",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n",
        "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "block": "林小满：黑发。"}, "office": {"block": "办公区。"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["office", "lin"], "shot": "中景", "art": "a", "motion": "m", "lines": []},
        ],
    }
    path = content_dir / "RT-API.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def test_api_roundtrip_and_validation(project):
    url = "/api/episodes/storyvideo/RT-API/shots/S1"
    resp = client.put(url, json={"readable_text": [{"surface": " 手机屏幕 ", "text": "剩余 4 天 23:59:58", "position": "画面中央偏上"}]})
    assert resp.status_code == 200, resp.text
    saved = json.loads(project.read_text(encoding="utf-8"))["shots"][0]
    assert saved["readable_text"] == [{"surface": "手机屏幕", "text": "剩余 4 天 23:59:58", "language": "zh", "position": "画面中央偏上"}]
    shot = resp.json()["shot"]
    assert shot["readable_text"] == saved["readable_text"]
    assert shot["frame_prompt"].endswith(ZH_TAIL) and ZH_VIDEO in shot["video_prompt"]
    # 详情页也带出来
    detail = client.get("/api/episodes/storyvideo/RT-API").json()
    assert detail["shots"][0]["readable_text"] == saved["readable_text"]
    # 没动 = 保留；[] = 清掉
    assert client.put(url, json={"sfx": "铃铛"}).status_code == 200
    assert json.loads(project.read_text(encoding="utf-8"))["shots"][0]["readable_text"] == saved["readable_text"]
    resp = client.put(url, json={"readable_text": []})
    assert resp.status_code == 200 and "readable_text" not in json.loads(project.read_text(encoding="utf-8"))["shots"][0]
    assert resp.json()["shot"]["readable_text"] == [] and resp.json()["shot"]["frame_prompt"].endswith(NO_TEXT_TAIL_CN)
    # 形状不对：400，不写盘
    before = project.read_text(encoding="utf-8")
    for bad in (
        [{"surface": "手机屏幕"}],
        [{"text": "剩余 4 天"}],
        [{"surface": "手机屏幕", "text": "  "}],
        [{"surface": "手机屏幕", "text": "x", "language": "jp"}],
        ["手机屏幕：剩余 4 天"],
    ):
        r = client.put(url, json={"readable_text": bad})
        assert r.status_code in (400, 422), (bad, r.text)
    assert project.read_text(encoding="utf-8") == before
