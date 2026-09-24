"""routers/post.py 的画面层校验：文字角标 labels、纯色字幕卡 layout=card、片头插卡 after=__start__。
写坏了要在写盘前拦住（不然等到后期合成才报错）；写对了原样落盘，并且「只有角标」也算有后期层。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_webui_post_layers import client, project, write_spec  # noqa: F401  （project 是 fixture）
from webui.routers import project as project_router

FONT = Path("C:/Windows/Fonts/msyh.ttc")
URL = "/api/episodes/storyvideo/POST-T/post-layers"


def _put_graphics(post_graphics: dict):
    return client.put(URL, json={"post_graphics": post_graphics})


def test_labels_card_and_start_insert_are_saved_as_is(project):
    path = write_spec(project)
    graphics = {
        "phone_ui_inserts": [
            {"id": "OPEN", "after": "__start__", "dur": 5, "layout": "card", "lines": ["本片由 AI 生成"]},
            {"id": "END", "after": "S2", "dur": 3, "layout": "card", "background": "#101820", "color": "#E0E0E0",
             "motion": "none", "screen": "声音由 AI 合成"},
            {"id": "UI_1", "after": "S1", "dur": 3, "layout": "plain", "lines": ["x"]},
        ],
        "labels": [
            {"text": "AI 生成", "position": "top_right"},
            {"text": "夜行", "position": "center", "start": 0, "end": 5, "size": 96, "color": "#FFFFFF", "opacity": 1, "box": False},
        ],
    }
    resp = _put_graphics(graphics)
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed_keys"] == ["post_graphics"]
    saved = json.loads(path.read_text(encoding="utf-8"))["post_graphics"]
    assert saved == graphics
    body = client.get(URL).json()
    assert body["post_graphics"]["labels"][1]["size"] == 96


def test_only_labels_still_counts_as_post_layers(project):
    write_spec(project, voice_over={"lines": []}, post_graphics={"labels": [{"text": "AI 生成", "position": "top_right"}]})
    assert client.get(URL).json()["readiness"]["has_post_layers"] is True
    assert client.get("/api/episodes/storyvideo/POST-T").json()["has_post_layers"] is True
    write_spec(project, voice_over={"lines": []}, post_graphics={})
    assert client.get(URL).json()["readiness"]["has_post_layers"] is False


@pytest.mark.parametrize("labels, needle", [
    ("AI", "post_graphics.labels 必须是数组"),
    (["AI"], "labels[0] 必须是对象"),
    ([{"text": " ", "position": "top_right"}], "labels[0].text 不能为空"),
    ([{"text": "字" * 25, "position": "top_right"}], "labels[0].text 最多 24 个字，现在 25 个字"),
    ([{"text": "AI 生成"}], "labels[0].position 必填"),
    ([{"text": "AI 生成", "position": "右上"}], "labels[0].position 只能是 top_right / top_left / top_center / center / bottom_center，现在是「右上」"),
    ([{"text": "AI 生成", "position": "top_right", "start": -1}], "labels[0].start 要是不小于 0 的秒数"),
    ([{"text": "AI 生成", "position": "top_right", "start": 5, "end": 5}], "labels[0].end 要大于 start"),
    ([{"text": "AI 生成", "position": "top_right", "end": "片尾"}], "labels[0].end 要大于 start"),
    ([{"text": "AI 生成", "position": "top_right", "size": 4}], "labels[0].size 要是 12 到 200 之间的数"),
    ([{"text": "AI 生成", "position": "top_right", "color": "white"}], "labels[0].color 要是 #RRGGBB 形式的颜色，现在是「white」"),
    ([{"text": "AI 生成", "position": "top_right", "color": "#FFF"}], "labels[0].color 要是 #RRGGBB"),
    ([{"text": "AI 生成", "position": "top_right", "opacity": 1.5}], "labels[0].opacity 要是 0 到 1 之间的数"),
    ([{"text": "AI 生成", "position": "top_right", "opacity": True}], "labels[0].opacity 要是 0 到 1 之间的数"),
    ([{"text": "AI 生成", "position": "top_right", "box": "yes"}], "labels[0].box 要是 true 或 false"),
])
def test_bad_labels_are_rejected_before_writing(project, labels, needle):
    path = write_spec(project)
    before = path.read_text(encoding="utf-8")
    resp = _put_graphics({"labels": labels})
    assert resp.status_code == 400, resp.text
    assert needle in resp.json()["detail"]
    assert path.read_text(encoding="utf-8") == before


def test_label_text_may_wrap_and_newlines_do_not_count(project):
    """两行的片头标识可以写（合成时按整块量高度、逐行画）；字数上限按去掉换行后的字数算。"""

    write_spec(project)
    ok = [{"text": "夜行\nNIGHT WALK", "position": "center", "size": 96},
          {"text": "字" * 12 + "\n" + "字" * 12, "position": "top_right"}]
    assert _put_graphics({"labels": ok}).status_code == 200
    resp = _put_graphics({"labels": [{"text": "字" * 13 + "\r\n" + "字" * 12, "position": "top_right"}]})
    assert resp.status_code == 400 and "labels[0].text 最多 24 个字，现在 25 个字" in resp.json()["detail"]
    resp = _put_graphics({"labels": [{"text": " \n ", "position": "top_right"}]})
    assert resp.status_code == 400 and "labels[0].text 不能为空" in resp.json()["detail"]


def test_wide_label_is_not_rejected_it_is_shrunk_at_compose(project):
    """24 个字配 200 号字一行放不下画面：这里不拦（字数和字号各自合法），合成时 post_layers 缩字号放进画面。"""

    write_spec(project)
    assert _put_graphics({"labels": [{"text": "字" * 24, "position": "top_right", "size": 200}]}).status_code == 200


@pytest.mark.parametrize("insert, needle", [
    ({"id": "__start__", "after": "S1", "layout": "plain", "lines": ["x"]}, "phone_ui_inserts[0] 的 id 不能用保留字「__start__」"),
    ({"id": "C", "after": "S1", "layout": "card", "lines": ["", ""]}, "phone_ui_inserts[0] 是字幕卡，要写 lines 或 screen"),
    ({"id": "C", "after": "S1", "layout": "card", "lines": ["x"], "background": "black"},
     "phone_ui_inserts[0].background 要是 #RRGGBB 形式的颜色，现在是「black」"),
    ({"id": "C", "after": "S1", "layout": "card", "lines": ["x"], "color": "#GGGGGG"}, "phone_ui_inserts[0].color 要是 #RRGGBB"),
    ({"id": "C", "after": "S1", "layout": "card", "lines": ["x"], "motion": "zoom"},
     "phone_ui_inserts[0].motion 只能是 push / none，现在是「zoom」"),
    ({"id": "C", "after": "__begin__", "layout": "card", "lines": ["x"]}, "phone_ui_inserts[0] 的 after「__begin__」既不是镜号也不是别的插卡 id"),
])
def test_bad_cards_are_rejected(project, insert, needle):
    write_spec(project)
    resp = _put_graphics({"phone_ui_inserts": [insert]})
    assert resp.status_code == 400, resp.text
    assert needle in resp.json()["detail"]


def test_card_only_keys_are_not_checked_on_other_layouts(project):
    """background / motion 只对 card 有意义；老版式上多写了也不拦（它们本来就不读这些键）。"""

    write_spec(project)
    resp = _put_graphics({"phone_ui_inserts": [{"id": "UI_1", "after": "S1", "layout": "plain", "lines": ["x"], "motion": "zoom"}]})
    assert resp.status_code == 200, resp.text


def test_start_insert_is_amendable_on_frozen_episode(project):
    from storyvideo.runtime import StoryVideoRunner

    write_spec(project)
    with StoryVideoRunner(project) as runner:
        runner.prepare("POST-T")
    resp = _put_graphics({"phone_ui_inserts": [{"id": "OPEN", "after": "__start__", "dur": 5, "layout": "card", "lines": ["片头"]}],
                          "labels": [{"text": "AI 生成", "position": "top_right"}]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "amended"


@pytest.mark.skipif(not FONT.is_file(), reason="needs a CJK font")
def test_card_insert_preview_renders(project):
    from PIL import Image

    write_spec(project, post_graphics={"fonts": {"ui": str(FONT)}, "phone_ui_inserts": [
        {"id": "OPEN", "after": "__start__", "dur": 5, "layout": "card", "background": "#203040", "lines": ["本片由 AI 生成"]}]})
    resp = client.get(f"{URL}/insert-preview/OPEN")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
    out = next((project / "output" / "story_video" / "POST-T" / "08_ui_preview").glob("*.png"))   # 文件名只用内容摘要（F1）
    with Image.open(out) as im:
        assert im.getpixel((10, 10)) == (32, 48, 64)      # 纯色底，没有状态栏


def test_card_insert_preview_without_text_is_425(project):
    write_spec(project, post_graphics={"phone_ui_inserts": [{"id": "OPEN", "after": "__start__", "layout": "card", "lines": []}]})
    resp = client.get(f"{URL}/insert-preview/OPEN")
    assert resp.status_code == 425 and "字幕卡 OPEN 没有文字" in resp.json()["detail"]


def test_graph_links_start_insert_before_first_shot():
    spec = {"cast": {}, "shots": [{"id": "S1", "cast": []}, {"id": "S2", "cast": []}],
            "post_graphics": {"phone_ui_inserts": [{"id": "OPEN", "after": "__start__"}, {"id": "UI_1", "after": "S1"}]}}
    edges = {(e["from"], e["to"], e["type"]) for e in project_router.build_graph(spec)["edges"]}
    assert ("insert:OPEN", "shot:S1", "before") in edges
    assert ("insert:UI_1", "shot:S1", "after") in edges
    assert not any(e[1] == "shot:__start__" for e in edges)
