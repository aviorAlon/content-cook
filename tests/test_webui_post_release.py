"""routers/post.py 发第一个 tag 前的修复（2026-09-22 审计 POST-2/4/5/6/7/9、DRAMA-04/05/06/11）：

* PUT post-layers 写盘前把每个数值 / 引用 / 枚举字段都拦住（插卡 dur / layout / 文字 / after 成环，旁白 at、整块声线、
  引擎，画面文字 at / until / y / size / text，字体文件，字幕 render_mode），不再等到合成时炸成裸 ValueError；
* 配乐上传验内容；字幕块漏了 language 沿用原值；插卡预览按交付尺寸画；残片不当成片。
（手改 / 老项目文件里的坏值不在开始合成时拦：缺配乐文件这类本来就是「跳过并写进清单」的，拦了反而合成不了；
合成侧逐项兜底成人话报错或清单里的 skipped，见 tests/test_post_compose_segmented.py。）
"""

from __future__ import annotations

import json
import shutil
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router

client = TestClient(app)
FONT = Path("C:/Windows/Fonts/msyh.ttc")
needs_font = pytest.mark.skipif(not FONT.is_file(), reason="needs a CJK font (msyh.ttc)")
needs_ffprobe = pytest.mark.skipif(not (shutil.which("ffprobe") and shutil.which("ffmpeg")), reason="needs ffmpeg + ffprobe")
URL = "/api/episodes/storyvideo/POST-R/post-layers"


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    return tmp_path


def write_spec(root: Path, **extra) -> Path:
    spec = {
        "id": "POST-R", "output_root": "output/story_video/POST-R", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n",
        "subtitle": {"language": "zh", "chinese_font_size": 40, "band_alpha": 0.3},
        "cast": {"a": {"block": "A：黑发。"}},
        "shots": [
            {"id": "S1", "dur": 6, "cast": ["a"], "shot": "中景", "art": "a", "motion": "m", "lines": [{"who": "a", "zh": "你怎么才来？"}]},
            {"id": "S2", "dur": 4, "cast": ["a"], "shot": "近景", "art": "a", "motion": "m", "lines": []},
        ],
        "post_graphics": {"phone_ui_inserts": [{"id": "UI_1", "after": "S1", "dur": 3, "layout": "plain", "lines": ["x"]}]},
    }
    spec.update(extra)
    path = root / "content" / "story_video" / "POST-R.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def _put(body: dict):
    return client.put(URL, json=body)


def _insert(**fields) -> dict:
    return {"post_graphics": {"phone_ui_inserts": [{"id": "C1", "after": "S1", "dur": 3, "layout": "card", "lines": ["x"], **fields}]}}


# ---------------------------------------------------------------------------- POST-4 / DRAMA-04：插卡 dur


@pytest.mark.parametrize("dur", ["abc", -3, 0, 0.1, [1], True, 61, "3"])
def test_insert_duration_is_validated_before_writing(project, dur):
    path = write_spec(project)
    before = path.read_text(encoding="utf-8")
    resp = _put(_insert(dur=dur))
    assert resp.status_code == 400, resp.text
    assert "phone_ui_inserts[0].dur 要是大于 0、不小于交叉溶解 0.25 秒、不超过 60 秒的数（留空 = 4 秒）" in resp.json()["detail"]
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("dur", [None, "", 0.25, 3, 60])
def test_insert_duration_accepts_blank_and_range(project, dur):
    write_spec(project)
    fields = {"dur": dur} if dur is not None else {}
    resp = _put({"post_graphics": {"phone_ui_inserts": [{"id": "C1", "after": "S1", "layout": "card", "lines": ["x"], **fields}]}})
    assert resp.status_code == 200, resp.text


def test_insert_layout_and_text_are_validated(project):
    write_spec(project)
    resp = _put(_insert(layout="hologram", screen="x"))
    assert resp.status_code == 400 and "layout 只能是 notification / sms / shop / plain / card，现在是「hologram」" in resp.json()["detail"]
    # plain（或没写版式）没字：以前存得进去，预览 425、合成才失败
    for layout in ("plain", None):
        insert = {"id": "C1", "after": "S1", "dur": 3, "lines": []}
        if layout:
            insert["layout"] = layout
        resp = _put({"post_graphics": {"phone_ui_inserts": [insert]}})
        assert resp.status_code == 400 and "phone_ui_inserts[0] 没有文字：写 lines 或 screen" in resp.json()["detail"]
    # 界面写的是单数 notification：照样认
    assert _put(_insert(layout="notification", items=["一条"])).status_code == 200


# ---------------------------------------------------------------------------- DRAMA-05：after 成环


@pytest.mark.parametrize("inserts, chain", [
    ([{"id": "C1", "after": "C1", "dur": 3, "lines": ["x"]}], "C1 → C1"),
    ([{"id": "C1", "after": "C2", "dur": 3, "lines": ["x"]}, {"id": "C2", "after": "C1", "dur": 3, "lines": ["y"]}], "C1 → C2 → C1"),
])
def test_insert_after_cycles_are_rejected(project, inserts, chain):
    path = write_spec(project)
    resp = _put({"post_graphics": {"phone_ui_inserts": inserts}})
    assert resp.status_code == 400
    assert f"phone_ui_inserts[0] 的 after 形成了环（{chain}），没有接到任何镜上" in resp.json()["detail"]
    assert "UI_1" in path.read_text(encoding="utf-8")                  # 没写盘


def test_insert_chain_reaching_a_shot_is_fine(project):
    write_spec(project)
    resp = _put({"post_graphics": {"phone_ui_inserts": [{"id": "C1", "after": "S1", "dur": 3, "lines": ["x"]},
                                                        {"id": "C2", "after": "C1", "dur": 3, "lines": ["y"]},
                                                        {"id": "OPEN", "after": "__start__", "dur": 3, "lines": ["z"]}]}})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------- DRAMA-06：旁白 at / 整块声线 / 引擎


@pytest.mark.parametrize("vo, needle", [
    ({"lines": [{"shot": "S1", "at": "abc", "zh": "a"}]}, "voice_over.lines[0].at 要是不小于 0 的秒数，现在是「abc」"),
    ({"lines": [{"shot": "S1", "at": -1, "zh": "a"}]}, "voice_over.lines[0].at 要是不小于 0 的秒数，现在是「-1」"),
    ({"lines": [{"shot": "S1", "at": [1], "zh": "a"}]}, "voice_over.lines[0].at 要是不小于 0 的秒数"),
    ({"lines": [{"shot": "S1", "at": True, "zh": "a"}]}, "voice_over.lines[0].at 要是不小于 0 的秒数"),
    ({"lines": [{"shot": "S1", "at": 99, "zh": "a"}]}, "voice_over.lines[0].at = 99s 不在镜 S1 里：这一镜只有 6s"),
    ({"edge": {"voice": "zh-CN-YunxiNeural", "rate": "fast"}, "lines": []}, "voice_over.edge.rate 要写成 +10% / -6% 这样，现在是「fast」"),
    ({"edge": {"voice": "zh-CN-YunxiNeural", "pitch": "high"}, "lines": []}, "voice_over.edge.pitch 要写成 +5Hz / -8Hz 这样，现在是「high」"),
    ({"edge": "zh-CN-YunxiNeural", "lines": []}, "voice_over.edge 必须是对象：{voice, rate, pitch}"),
    ({"engine": "bogus", "lines": []}, "voice_over.engine 只能是 edge / settings / minimax（留空 = 老规则），现在是「bogus」"),
])
def test_voice_over_numbers_and_block_voice_are_validated(project, vo, needle):
    write_spec(project)
    resp = _put({"voice_over": vo})
    assert resp.status_code == 400, resp.text
    assert needle in resp.json()["detail"]


def test_voice_over_valid_values_pass(project):
    write_spec(project)
    resp = _put({"voice_over": {"engine": "edge", "edge": {"voice": "zh-CN-YunxiNeural", "rate": "-4%", "pitch": "+2Hz"},
                                "lines": [{"shot": "S1", "at": 0.5, "zh": "a"}, {"shot": "S2", "zh": "没写 at = 镜头开头"}]}})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------- POST-4 / DRAMA-11：画面文字、字体、字幕 render_mode


@pytest.mark.parametrize("entry, needle", [
    ({"shot": "S1"}, "notebook_text[0] 缺 text（画面文字）"),
    ({"shot": "S1", "text": "字", "at": "x"}, "notebook_text[0].at 要是不小于 0 的秒数，现在是「x」"),
    ({"shot": "S1", "text": "字", "at": -1}, "notebook_text[0].at 要是不小于 0 的秒数"),
    ({"shot": "S1", "text": "字", "at": 7}, "notebook_text[0].at = 7s 不在镜 S1 里：这一镜只有 6s"),
    ({"shot": "S1", "text": "字", "at": 3, "until": 0.95}, "notebook_text[0].until 要大于 at、不超过镜 S1 的时长 6s，现在是「0.95」"),
    ({"shot": "S1", "text": "字", "until": 100}, "notebook_text[0].until 要大于 at、不超过镜 S1 的时长 6s，现在是「100」"),
    ({"shot": "S1", "text": "字", "y": 2}, "notebook_text[0].y 要是 0 到 1 之间的数，现在是「2」"),
    ({"shot": "S1", "text": "字", "size": "abc"}, "notebook_text[0].size 要是 12 到 300 之间的数，现在是「abc」"),
])
def test_notebook_text_fields_are_validated(project, entry, needle):
    write_spec(project)
    resp = _put({"post_graphics": {"notebook_text": [entry]}})
    assert resp.status_code == 400, resp.text
    assert needle in resp.json()["detail"]


def test_notebook_text_valid_entry_passes(project):
    write_spec(project)
    resp = _put({"post_graphics": {"notebook_text": [{"shot": "S1", "text": "你好", "at": 0.5, "until": 6, "y": 0.5, "size": 40}]}})
    assert resp.status_code == 200, resp.text


def test_font_overrides_must_exist_and_readiness_uses_them(project, tmp_path):
    path = write_spec(project)
    resp = _put({"post_graphics": {"fonts": {"ui": "C:/nope/nope.ttf"}}})
    assert resp.status_code == 400 and "post_graphics.fonts.ui 指向的字体文件不存在：C:/nope/nope.ttf" in resp.json()["detail"]
    # 手改文件写进去的覆盖字体：就绪检查按它查（以前只查默认字体，显示「✓ 中文字体」而合成必失败）
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec["post_graphics"]["fonts"] = {"ui": str(tmp_path / "missing-ui.ttf")}
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    readiness = client.get(URL).json()["readiness"]
    assert readiness["fonts_ok"] is False and "ui" in readiness["fonts_missing"]


def test_subtitle_render_mode_is_an_enum(project):
    write_spec(project)
    resp = _put({"subtitle": {"language": "zh", "render_mode": "bogus"}})
    assert resp.status_code == 400
    assert "字幕 render_mode 只能是 programmatic_only / none / off，现在是「bogus」" in resp.json()["detail"]
    assert _put({"subtitle": {"language": "zh", "render_mode": "none"}}).status_code == 200


# ---------------------------------------------------------------------------- POST-9：字幕块漏了 language


def test_subtitle_put_without_language_keeps_the_current_language(project):
    path = write_spec(project)
    resp = _put({"subtitle": {"chinese_font_size": 42}})
    assert resp.status_code == 200, resp.text
    assert json.loads(path.read_text(encoding="utf-8"))["subtitle"] == {"language": "zh", "chinese_font_size": 42}
    # 空块 = 恢复默认：language 同样沿用（以前 400「shot S1.lines[0].en must be a non-empty string」）
    assert _put({"subtitle": {}}).status_code == 200
    assert json.loads(path.read_text(encoding="utf-8"))["subtitle"] == {"language": "zh"}
    # 明写了 language 就按写的
    assert _put({"subtitle": {"language": "bilingual"}}).status_code == 400


# ---------------------------------------------------------------------------- POST-5：配乐上传验内容


def _wav_bytes(tmp_path: Path) -> bytes:
    target = tmp_path / "tone.wav"
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x10" * 8000)
    return target.read_bytes()


@needs_ffprobe
def test_music_upload_rejects_files_without_an_audio_stream(project, tmp_path):
    import base64

    write_spec(project)
    music_url = "/api/episodes/storyvideo/POST-R/music"
    bad = client.post(music_url, json={"filename": "x.mp3", "data_base64": base64.b64encode(b"not an mp3 at all").decode()})
    assert bad.status_code == 400
    # 发布前加固后按内容认（uploads.accept_audio）：原因是 ffprobe 的原话（去掉临时路径），文件名换回用户那个
    detail = bad.json()["detail"]
    assert detail.startswith("上传的文件不是能读的音频（x.mp3）：") and detail.endswith("——换一个 mp3 / wav / m4a 试试"), detail
    assert not list((project / "content" / "assets" / "POST-R" / "music").glob("x-*"))   # 没留下坏文件
    good = client.post(music_url, json={"filename": "tone.wav", "data_base64": base64.b64encode(_wav_bytes(tmp_path)).decode()})
    assert good.status_code == 200, good.text
    assert (project / good.json()["file"]).is_file()


# ---------------------------------------------------------------------------- POST-7：插卡预览按交付尺寸画


@needs_font
def test_insert_preview_uses_the_delivery_size(project):
    from PIL import Image

    write_spec(project, video_params={"aspect_ratio": "16:9", "resolution": "1080p"},
               post_graphics={"phone_ui_inserts": [{"id": "C1", "after": "S1", "dur": 3, "layout": "card", "lines": ["横屏"]}]})
    resp = client.get(f"{URL}/insert-preview/C1")
    assert resp.status_code == 200
    out = sorted((project / "output" / "story_video" / "POST-R" / "08_ui_preview").glob("*.png"))   # 文件名只用内容摘要（F1）
    with Image.open(out[-1]) as im:
        assert im.size == (1920, 1080)


# ---------------------------------------------------------------------------- POST-2：残片不当成片


def test_post_manifest_reports_a_truncated_output_as_missing(project):
    write_spec(project)
    root = project / "output" / "story_video" / "POST-R"
    (root / "manifests").mkdir(parents=True)
    post = root / "POST-R_post.mp4"
    post.write_bytes(b"x" * 48)
    (root / "manifests" / "post.json").write_text(json.dumps({"output": str(post), "duration_seconds": 15.8, "size_bytes": 849819}),
                                                  encoding="utf-8")
    assert client.get(URL).json()["post"]["output_exists"] is False
    (root / "manifests" / "post.json").write_text(json.dumps({"output": str(post), "duration_seconds": 15.8, "size_bytes": 48}),
                                                  encoding="utf-8")
    assert client.get(URL).json()["post"]["output_exists"] is True


# ---------------------------------------------------------------------------- DRAMA-11：逐镜接口的引用 / 枚举校验


def test_shot_edit_rejects_unknown_speaker_and_speech_mode(project):
    """审计里 PUT /shots/S1 写 who="ghost" + speech_mode="bogus" 是 200：ghost 一路走进付费的视频提示词
    （「ghost用普通话说：…」、没有声线），bogus 被当成画内说话、照样要求嘴型跟台词对上。现在写盘前就拦。"""

    write_spec(project)
    url = "/api/episodes/storyvideo/POST-R/shots/S1"

    bad_who = client.put(url, json={"lines": [{"who": "ghost", "zh": "嗨。"}]})
    assert bad_who.status_code == 400
    assert "说话人「ghost」不在人物表里" in bad_who.json()["detail"]

    bad_mode = client.put(url, json={"speech_mode": "bogus"})
    assert bad_mode.status_code == 400
    assert "speech_mode 只能是 narration, offscreen_narration" in bad_mode.json()["detail"]

    # 认识的值照旧写得进去，坏值一个都没落盘
    assert client.put(url, json={"speech_mode": "offscreen_narration"}).status_code == 200
    saved = json.loads((project / "content" / "story_video" / "POST-R.json").read_text(encoding="utf-8"))
    assert saved["shots"][0]["speech_mode"] == "offscreen_narration"
    assert saved["shots"][0]["lines"] == [{"who": "a", "zh": "你怎么才来？"}]
