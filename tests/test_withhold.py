"""观众可见性 / 揭示保护（DESIGN §28，融合 drama-skills 的「观众可见性」判据）：``shot.withhold = [{fact, carrier, until}]``。
声明镜与其后、until 之前的镜，首帧与视频提示词（中英、三种方言）都多一句排除句；没声明的镜一个字不变；
spec 归一化拒绝坏形状与不存在的 until；lint 四类；编辑接口校验与回读；英文规则表覆盖。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import lint
from storyvideo.prompts import active_withholds, compile_frame_prompt, compile_video_prompt, video_prompt_parts
from storyvideo.spec import SpecError, load_spec, normalize_spec
from webui import deps, i18n
from webui.app import app
from webui.problems import problem_card
from webui.routers import episodes as episodes_router

ROOT = Path(__file__).resolve().parents[1]
RAW = json.loads((ROOT / "examples" / "episodes" / "first_episode.json").read_text(encoding="utf-8-sig"))
IDS = [s["id"] for s in RAW["shots"]]
assert len(IDS) >= 5
KILLER = {"fact": "凶手是陈默", "carrier": "脸", "until": IDS[3]}
ZH_FRAME = "不得揭示：凶手是陈默——脸全程不入画或被遮挡、看不清。画面任何位置都不透露这一点。"
ZH_VIDEO = "不得揭示：凶手是陈默——脸全程不入画或被遮挡、看不清。画面、动作与台词都不透露这一点。"
EN_FRAME = "Do not reveal: the killer is Chen Mo — the face stays out of frame or hidden and unreadable. Nothing anywhere in the image gives it away."
EN_VIDEO = "Do not reveal: the killer is Chen Mo — the face stays out of frame or hidden and unreadable. Nothing in the picture, action or dialogue gives it away."


def _spec(tmp_path, *, withhold=None, shot_id=None, language="zh", **video_params):
    raw = json.loads(json.dumps(RAW))
    raw["id"] = "WH-T"
    raw["output_root"] = "output/story_video/WH-T"
    raw["prompt_language"] = language
    raw["video_params"] = {**raw.get("video_params", {}), **video_params}
    if withhold is not None:
        next(s for s in raw["shots"] if s["id"] == (shot_id or IDS[1]))["withhold"] = withhold
    content = tmp_path / "content" / "story_video"
    content.mkdir(parents=True, exist_ok=True)
    (content / "WH-T.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return load_spec("WH-T", project_root=tmp_path)


# ---------------------------------------------------------------- 归一化


def test_normalize_accepts_and_rejects(tmp_path):
    spec = _spec(tmp_path, withhold=[KILLER, {"fact": "她怀孕了"}])
    shot = next(s for s in spec["shots"] if s["id"] == IDS[1])
    assert shot["withhold"] == [KILLER, {"fact": "她怀孕了"}]     # 原样保留，不改形状
    for bad, needle in (
        ([{"fact": "x", "until": "S_NOPE"}], f"shot {IDS[1]}.withhold[0].until references unknown shot: S_NOPE"),
        ([{"carrier": "脸"}], f"shot {IDS[1]}.withhold[0].fact must be a non-empty string"),
        ([{"fact": "  "}], "withhold[0].fact must be a non-empty string"),
        ("凶手是陈默", f"shot {IDS[1]}.withhold must be a list"),
        (["凶手是陈默"], f"shot {IDS[1]}.withhold[0] must be an object"),
        ([{"fact": "x", "until": 3}], f"shot {IDS[1]}.withhold[0].until must be a string"),
    ):
        with pytest.raises(SpecError) as excinfo:
            _spec(tmp_path, withhold=bad)
        assert needle in str(excinfo.value), (bad, str(excinfo.value))
    # until 指向后面的镜是常态：收集完所有镜再核对
    assert normalize_spec({**json.loads(json.dumps(RAW)), "id": "WH-T", "output_root": "output/story_video/WH-T",
                           "shots": [{**RAW["shots"][0], "withhold": [{"fact": "x", "until": IDS[-1]}]}, *RAW["shots"][1:]]},
                          project_root=tmp_path)


# ---------------------------------------------------------------- 编译


def test_without_withhold_nothing_changes(tmp_path):
    spec = _spec(tmp_path)
    for shot in spec["shots"]:
        assert video_prompt_parts(spec, shot["id"])["withhold"] == ""
        assert "不得揭示" not in compile_frame_prompt(spec, shot["id"])
        assert "不得揭示" not in compile_video_prompt(spec, shot["id"])
    # 空列表 = 没声明；废条目 spec 校验会拒，编译器自己也要能跳过（老 JSON 直接喂进来的情况）
    spec2 = _spec(tmp_path, withhold=[])
    assert compile_frame_prompt(spec2, IDS[1]) == compile_frame_prompt(spec, IDS[1])
    next(s for s in spec2["shots"] if s["id"] == IDS[1])["withhold"] = [{}, {"carrier": "脸"}, "junk"]
    assert compile_frame_prompt(spec2, IDS[1]) == compile_frame_prompt(spec, IDS[1])
    assert compile_video_prompt(spec2, IDS[1]) == compile_video_prompt(spec, IDS[1])


def test_zh_frame_and_generic_video_cover_declaring_shot_until_reveal(tmp_path):
    spec = _spec(tmp_path, withhold=[KILLER], prompt_dialect="generic")
    assert [s["id"] for s in spec["shots"] if active_withholds(spec, s["id"])] == [IDS[1], IDS[2]]
    for sid in (IDS[1], IDS[2]):
        frame = compile_frame_prompt(spec, sid)
        assert ZH_FRAME in frame
        assert frame.index(ZH_FRAME) < frame.index(str(spec["camera"]))        # 首帧：轴线句之后、全局机位句之前
        video = compile_video_prompt(spec, sid)
        parts = video_prompt_parts(spec, sid)
        assert parts["withhold"] == ZH_VIDEO and ZH_VIDEO in video
        assert video.index(ZH_VIDEO) > video.index(parts["lock"])             # 通用方言：接在锁句之后
    for sid in (IDS[0], IDS[3], IDS[4]):                                        # 之前的镜、揭示镜及之后：不注入
        assert "不得揭示" not in compile_frame_prompt(spec, sid)
        assert "不得揭示" not in compile_video_prompt(spec, sid)


def test_no_until_means_whole_episode_and_multiple_entries(tmp_path):
    spec = _spec(tmp_path, withhold=[{"fact": "凶手是陈默", "carrier": "脸"}, {"fact": "她怀孕了", "carrier": "手上的戒指"}])
    assert [s["id"] for s in spec["shots"] if active_withholds(spec, s["id"])] == IDS[1:]
    frame = compile_frame_prompt(spec, IDS[4])
    assert "不得揭示：凶手是陈默——脸全程不入画或被遮挡、看不清；她怀孕了——手上的戒指全程不入画或被遮挡、看不清。画面任何位置都不透露这些信息。" in frame
    assert "不得揭示：凶手是陈默——脸全程不入画或被遮挡、看不清；她怀孕了——手上的戒指全程不入画或被遮挡、看不清。画面、动作与台词都不透露这些信息。" in compile_video_prompt(spec, IDS[4])
    # 没写载体：只说事实
    spec2 = _spec(tmp_path, withhold=[{"fact": "她怀孕了"}])
    assert "不得揭示：她怀孕了。画面任何位置都不透露这一点。" in compile_frame_prompt(spec2, IDS[1])
    # 两镜各自声明同一件事：一镜只出现一次
    raw_two = [KILLER]
    spec3 = _spec(tmp_path, withhold=raw_two, shot_id=IDS[0])
    next(s for s in spec3["shots"] if s["id"] == IDS[1])["withhold"] = [KILLER]
    assert compile_video_prompt(spec3, IDS[1]).count("不得揭示") == 1


def test_zh_dialects_carry_the_clause(tmp_path):
    for dialect in ("minimax-h3", "seedance"):
        spec = _spec(tmp_path, withhold=[KILLER], model="MiniMax-H3", resolution="768P", aspect_ratio="adaptive", prompt_dialect=dialect)
        text = compile_video_prompt(spec, IDS[1])
        assert "不得揭示：凶手是陈默——脸全程不入画或被遮挡、看不清" in text, dialect
        assert "字幕" in text and text.index("不得揭示") < text.index("字幕"), dialect   # 文字政策句之前
        assert "不得揭示" not in compile_video_prompt(spec, IDS[3]), dialect


def test_en_frame_video_and_dialects(tmp_path):
    spec = _spec(tmp_path, language="en", withhold=[{"fact": "the killer is Chen Mo", "carrier": "face", "until": IDS[3]}], prompt_dialect="generic")
    assert EN_FRAME in compile_frame_prompt(spec, IDS[2])
    assert EN_VIDEO in compile_video_prompt(spec, IDS[2])
    assert "Do not reveal" not in compile_frame_prompt(spec, IDS[3])
    spec2 = _spec(tmp_path, language="en", withhold=[{"fact": "A", "carrier": "face"}, {"fact": "B"}])
    assert "Do not reveal: A — the face stays out of frame or hidden and unreadable; B. Nothing anywhere in the image gives them away." in compile_frame_prompt(spec2, IDS[1])
    for dialect in ("minimax-h3", "seedance"):
        spec3 = _spec(tmp_path, language="en", withhold=[{"fact": "the killer is Chen Mo", "carrier": "face", "until": IDS[3]}],
                      model="MiniMax-H3", resolution="768P", aspect_ratio="adaptive", prompt_dialect=dialect)
        text = compile_video_prompt(spec3, IDS[1])
        assert "Do not reveal: the killer is Chen Mo — the face stays out of frame or hidden and unreadable" in text, dialect
        ban = next(i for i in (text.find("subtitles"), text.find("captions")) if i >= 0)   # 各方言的字幕禁令写法不同
        assert text.index("Do not reveal") < ban, dialect


# ---------------------------------------------------------------- lint


def _lint_spec(*shots: dict) -> dict:
    out = []
    for i, shot in enumerate(shots, 1):
        base = {"id": f"S{i}", "dur": 4, "cast": [], "shot": "中景，平视", "art": "桌上一部手机", "motion": "唯一动作：抬手", "lines": []}
        out.append({**base, **shot})
    return {"prompt_language": "zh", "cast": {}, "shots": out}


def test_lint_silent_without_withhold_and_after_reveal():
    assert lint.check_withhold(_lint_spec({}, {"art": "凶手是陈默"})) == []
    w = lint.check_withhold(_lint_spec({"withhold": [KILLER | {"until": "S3"}]}, {}, {"art": "正面特写：凶手是陈默"}, {"motion": "陈默摘下面具"}))
    assert w == []     # S3 起可以揭示：S3、S4 都不查


def test_lint_leak_before_reveal_literal_and_tokens():
    w = lint.check_withhold(_lint_spec({"withhold": [{"fact": "凶手是陈默", "carrier": "脸", "until": "S3"}]}, {"art": "镜头转向，凶手是陈默的脸"}, {}))
    assert w == ["shot S2: 正文写到了「凶手是陈默」的内容，但揭示要等到 S3——删掉或改成不露信息的写法"]
    # 声明镜自己也查；台词与 beats 也算正文；一镜一事实只报一次
    w = lint.check_withhold(_lint_spec({"withhold": [{"fact": "凶手是陈默", "until": "S2"}], "lines": [{"who": "a", "zh": "凶手是陈默！"}],
                                        "beats": [{"t": "0-2", "picture": "凶手是陈默"}]}, {}))
    assert w == ["shot S1: 正文写到了「凶手是陈默」的内容，但揭示要等到 S2——删掉或改成不露信息的写法"]
    # 有辨识度的词：按标点/空格拆，≥2 字才算
    w = lint.check_withhold(_lint_spec({"withhold": [{"fact": "凶手：陈默"}]}, {"motion": "陈默推门进来"}, {"art": "桌上一把凶器"}))
    assert w == ["shot S2: 正文写到了「凶手：陈默」的内容，但这一集都不揭示——删掉或改成不露信息的写法"]   # 「凶器」不含「凶手」
    assert lint.check_withhold(_lint_spec({"withhold": [{"fact": "凶手 是 陈默"}]}, {"art": "他是谁"})) == []   # 「是」一个字不算


def test_lint_bad_until_carrier_clash_and_shape():
    w = lint.check_withhold(_lint_spec({}, {"withhold": [{"fact": "凶手是陈默", "until": "S1"}]}, {"art": "凶手是陈默"}))
    assert len(w) == 1 and w[0].startswith("shot S2: 「凶手是陈默」的揭示点 until（S1）不在本镜之后")
    w = lint.check_withhold(_lint_spec({"withhold": [{"fact": "凶手是陈默", "until": "S9"}]}, {"art": "凶手是陈默"}))
    assert w[0] == "shot S1: 「凶手是陈默」的揭示点 until 指向不存在的镜 S9——改成现有镜号，或留空表示整集不揭示"
    assert w[1].startswith("shot S2: 正文写到了")       # 不存在的 until 按整集查
    w = lint.check_withhold(_lint_spec({"withhold": [{"fact": "她已被解雇", "carrier": "手机屏幕", "until": "S3"}]},
                                       {"readable_text": [{"surface": "手机屏幕", "text": "解雇通知"}]},
                                       {"readable_text": [{"surface": "手机屏幕", "text": "解雇通知"}]}))
    assert w == ["shot S2: 「手机屏幕」既是「她已被解雇」的泄露载体（手机屏幕），又被声明为画面里必须可读的文字——观众会直接读到；删掉这条可读文字，或换一个载体"]
    w = lint.check_withhold(_lint_spec({"withhold": [{"carrier": "脸"}, "junk"]}))
    assert len(w) == 2 and all("withhold[" in x and "写法不对" in x for x in w)
    assert lint.check_withhold(_lint_spec({"withhold": "凶手是陈默"}))[0].startswith("shot S1: withhold 必须是列表")
    # 接进 check_all；问题卡分类
    all_w = lint.check_all(_lint_spec({"withhold": [{"fact": "凶手是陈默", "until": "S3"}]}, {"art": "凶手是陈默"}, {}))
    leak = [x for x in all_w if "正文写到了「凶手是陈默」" in x]
    assert len(leak) == 1
    card = problem_card(leak[0])
    assert card["scope"] == "shot" and card["target"] == "S2" and card["kind"] == "withhold"
    assert card["impact"].startswith("正文写到了") and card["fix"].startswith("删掉")


def test_lint_messages_have_english_rules():
    i18n._compiled = None
    samples = [
        "shot S2: 正文写到了「凶手是陈默」的内容，但揭示要等到 S3——删掉或改成不露信息的写法",
        "shot S2: 正文写到了「凶手是陈默」的内容，但这一集都不揭示——删掉或改成不露信息的写法",
        "shot S1: 「凶手是陈默」的揭示点 until 指向不存在的镜 S9——改成现有镜号，或留空表示整集不揭示",
        "shot S2: 「凶手是陈默」的揭示点 until（S1）不在本镜之后——揭示要发生在保护开始之后；改成后面的镜号，或留空表示整集不揭示",
        "shot S2: 「手机屏幕」既是「她已被解雇」的泄露载体（手机屏幕），又被声明为画面里必须可读的文字——观众会直接读到；删掉这条可读文字，或换一个载体",
        "shot S1: withhold[0] 写法不对——fact 填观众此刻还不能知道的事实，carrier 填会泄露它的载体（脸 / 戒指 / 台词），until 填从哪一镜起可以揭示（留空 = 整集不揭示）",
        "withhold[0] 要是对象（fact / carrier / until）",
        "withhold[0]：fact（先不揭示的事实）不能为空",
        "withhold[1].until 要是一句话（字符串）",
    ]
    han = re.compile(r"[一-鿿]")
    # 事实 / 载体是用户内容，翻译后仍留在引号里；把引号和括号里的内容去掉再看有没有漏翻的中文
    quoted = re.compile(r"「[^」]*」|（[^）]*）|\"[^\"]*\"|\([^)]*\)")
    leftover = {s: i18n.tr(s, "en") for s in samples if han.search(quoted.sub("", i18n.tr(s, "en")))}
    assert not leftover, leftover
    assert i18n.tr(samples[0], "en") == 'shot S2: the text gives away "凶手是陈默", but the reveal must wait until S3 — cut it or rewrite it so nothing is disclosed'


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
        "id": "WH-API", "output_root": "output/story_video/WH-API", "prompt_language": "zh",
        "camera_profile": "dark_comedy_vertical",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n",
        "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "block": "林小满：黑发。"}, "office": {"block": "办公区。"}},
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["office", "lin"], "shot": "中景", "art": "a", "motion": "m", "lines": []},
            {"id": "S2", "dur": 4, "cast": ["office"], "shot": "近景", "art": "b", "motion": "m2", "lines": []},
            {"id": "S3", "dur": 4, "cast": ["lin"], "shot": "特写", "art": "c", "motion": "m3", "lines": []},
        ],
    }
    path = content_dir / "WH-API.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def test_api_roundtrip_and_validation(project):
    url = "/api/episodes/storyvideo/WH-API/shots/S1"
    resp = client.put(url, json={"withhold": [{"fact": " 凶手是陈默 ", "carrier": "脸", "until": "S3"}, {"fact": "她怀孕了", "carrier": "", "until": ""}]})
    assert resp.status_code == 200, resp.text
    saved = json.loads(project.read_text(encoding="utf-8"))["shots"][0]
    assert saved["withhold"] == [{"fact": "凶手是陈默", "carrier": "脸", "until": "S3"}, {"fact": "她怀孕了"}]   # 空的可选键不落盘
    shot = resp.json()["shot"]
    assert shot["withhold"] == saved["withhold"]
    assert "不得揭示：凶手是陈默——脸全程不入画或被遮挡、看不清；她怀孕了。画面任何位置都不透露这些信息。" in shot["frame_prompt"]
    assert "不得揭示" in shot["video_prompt"]
    # 详情页每镜带出；S2 被 S1 的保护覆盖，S3 是揭示镜、只剩整集那条
    detail = client.get("/api/episodes/storyvideo/WH-API").json()
    by_id = {s["id"]: s for s in detail["shots"]}
    assert by_id["S1"]["withhold"] == saved["withhold"] and by_id["S2"]["withhold"] == []
    assert "凶手是陈默" in by_id["S2"]["frame_prompt"] and "凶手是陈默" not in by_id["S3"]["frame_prompt"]
    assert "她怀孕了" in by_id["S3"]["frame_prompt"]
    # 没动 = 保留；[] = 清掉
    assert client.put(url, json={"sfx": "铃铛"}).status_code == 200
    assert json.loads(project.read_text(encoding="utf-8"))["shots"][0]["withhold"] == saved["withhold"]
    resp = client.put(url, json={"withhold": []})
    assert resp.status_code == 200 and "withhold" not in json.loads(project.read_text(encoding="utf-8"))["shots"][0]
    assert resp.json()["shot"]["withhold"] == [] and "不得揭示" not in resp.json()["shot"]["frame_prompt"]
    # 形状不对 / until 指向不存在的镜：400，不写盘
    before = project.read_text(encoding="utf-8")
    for bad in (
        [{"carrier": "脸"}],
        [{"fact": "  "}],
        [{"fact": "x", "until": 3}],
        [{"fact": "x", "extra": 1}],
        ["凶手是陈默"],
        [{"fact": "x", "until": "S9"}],
    ):
        r = client.put(url, json={"withhold": bad})
        assert r.status_code in (400, 422), (bad, r.text)
    assert project.read_text(encoding="utf-8") == before
    assert "S9" in client.put(url, json={"withhold": [{"fact": "x", "until": "S9"}]}).json()["detail"]
