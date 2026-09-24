"""剧本的逐场编辑器（DESIGN §32）：归一化/校验、六条 lint（每条命中 + 不命中）、GET/PUT 的形状与 400、
统计数字，以及改过的剧本仍然能拆镜。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from storyvideo.screenplay import (
    MAX_SCENES,
    check_cast_usage,
    check_contract_echo,
    check_headings,
    check_scene_content,
    check_scene_count,
    lint_screenplay,
    normalize_screenplay,
    screenplay_stats,
)
from storyvideo.spec import SpecError
from webui import deps
from webui.app import app
from webui.problems import classify, problem_card
from webui.routers import scripts as scripts_router

CLEAN = {
    "title": "厨房关停",
    "logline": "排班员叶澄暗中调灶位保护弱势摊主，被齐曼当众揭开。",
    "contract": {"premise": "叶澄在维持一份改过的排班", "hook": "齐曼当众要求出示完整排班记录",
                 "cliffhanger": "叶澄把备用钥匙交给齐曼", "must_pay_off": ["订单缺口的原因"],
                 "carry_over": ["齐曼不知道冷柜后的账本"]},
    "cast": [{"id": "ye", "name": "叶澄", "block": "三十岁，黑色短发"},
             {"id": "qi", "name": "齐曼", "role": "要恢复公开规则"}],
    "scenes": [
        {"id": "S01", "heading": "内 · 共享厨房 · 清晨", "beats": ["齐曼把订单拍在桌上", "叶澄合上排班本"],
         "dialogue": [{"who": "qi", "zh": "把完整的排班记录拿出来。", "cue": "压着火"}, {"who": "ye", "zh": "记录就在这里。"}],
         "change": "齐曼拿到了公开质问的位置"},
        {"id": "S02", "heading": "内 · 冷柜前 · 清晨", "beats": ["叶澄把备用钥匙交给齐曼，没提账本"],
         "dialogue": [{"who": "narrator", "zh": "那天之后，冷柜再没上过锁。"}], "note": "给拆镜：手部特写"},
    ],
}


def clean() -> dict:
    return json.loads(json.dumps(CLEAN))


# ---------------------------------------------------------------- 归一化 / 校验

def test_normalize_keeps_content_and_fills_defaults():
    doc = clean()
    doc["scenes"][0].pop("id")            # 没给 id 的场次按位置补 S01…
    doc["scenes"][1]["id"] = ""
    doc["scenes"][0]["beats"].append("   ")   # 界面上刚加出来的空行：丢掉，不当错误
    doc["scenes"][0]["dialogue"].append({"who": "", "zh": ""})
    out = normalize_screenplay(doc)
    assert [s["id"] for s in out["scenes"]] == ["S01", "S02"]
    assert out["scenes"][0]["beats"] == ["齐曼把订单拍在桌上", "叶澄合上排班本"]
    assert len(out["scenes"][0]["dialogue"]) == 2
    # 老字段原样留着：cue / change / note / role / block
    assert out["scenes"][0]["dialogue"][0]["cue"] == "压着火"
    assert out["scenes"][0]["change"] == "齐曼拿到了公开质问的位置" and out["scenes"][1]["note"].startswith("给拆镜")
    assert out["cast"][0]["block"] == "三十岁，黑色短发" and out["cast"][1]["role"] == "要恢复公开规则"
    # 契约五个键永远都在（编辑器要稳定的输入框）
    assert set(out["contract"]) == {"premise", "hook", "cliffhanger", "must_pay_off", "carry_over"}
    assert out["contract"]["must_pay_off"] == ["订单缺口的原因"]
    assert normalize_screenplay({"scenes": [{"heading": "内 · 巷口 · 夜"}]})["contract"]["hook"] == ""
    # 说话人可以写角色名，也可以是旁白
    doc = clean()
    doc["scenes"][0]["dialogue"][0]["who"] = "齐曼"
    assert normalize_screenplay(doc)["scenes"][0]["dialogue"][0]["who"] == "齐曼"
    # 节拍也收「一行一条」的整块文本（编辑器兜底、老文件）
    doc = clean()
    doc["scenes"][0]["beats"] = "第一件事\n\n第二件事"
    assert normalize_screenplay(doc)["scenes"][0]["beats"] == ["第一件事", "第二件事"]


@pytest.mark.parametrize("mutate, message", [
    (lambda d: "不是对象", "剧本要是对象"),
    (lambda d: d.update(scenes=[]), "至少要有一场"),
    (lambda d: d.update(scenes="一场"), "至少要有一场"),
    (lambda d: d["scenes"].append("第三场"), "第 3 场要是对象"),
    (lambda d: d["scenes"][1].update(id="S01"), "场次 id 重复了：S01"),
    (lambda d: d["scenes"][0].update(heading="   "), "场 S01 没有场景标题"),
    (lambda d: d["scenes"][0]["dialogue"][0].update(who="路人甲"), "说话人「路人甲」不在人物表里"),
    (lambda d: d["scenes"][0]["dialogue"][0].update(zh=""), "第 1 句台词是空的"),
    (lambda d: d["scenes"][0].update(dialogue={"who": "ye"}), "的对白要是列表"),
    (lambda d: d["scenes"][0]["beats"].append(42), "条节拍要是一句话"),
    (lambda d: d.update(cast={"ye": {}}), "人物表（cast）要是列表"),
    (lambda d: d["cast"].append({"name": "路人"}), "没有 id"),
    (lambda d: d["cast"].append({"id": "ye", "name": "另一个叶澄"}), "id 重复了：ye"),
    (lambda d: d.update(title=["厨房"]), "「剧名」要是一段文字"),
])
def test_normalize_rejects_broken_shapes(mutate, message):
    doc = clean()
    result = mutate(doc)
    with pytest.raises(SpecError, match=message):
        normalize_screenplay(result if isinstance(result, str) else doc)


def test_stats_counts_scenes_lines_and_words():
    stats = screenplay_stats(clean())
    assert stats["scenes"] == 2 and stats["dialogue_lines"] == 3
    assert stats["words"] == sum(
        len(x) for x in ["齐曼把订单拍在桌上", "叶澄合上排班本", "叶澄把备用钥匙交给齐曼，没提账本",
                         "把完整的排班记录拿出来。", "记录就在这里。", "那天之后，冷柜再没上过锁。"]
    )
    assert screenplay_stats({"scenes": []}) == {"scenes": 0, "dialogue_lines": 0, "words": 0}
    assert screenplay_stats("坏文件") == {"scenes": 0, "dialogue_lines": 0, "words": 0}


# ---------------------------------------------------------------- 六条 lint：每条命中 + 不命中

def test_clean_screenplay_has_no_warnings():
    assert lint_screenplay(clean()) == []
    assert lint_screenplay(normalize_screenplay(clean())) == []


def test_lint_empty_scene():
    doc = clean()
    doc["scenes"][1].update(beats=[], dialogue=[])
    hits = check_scene_content(doc)
    assert hits == ["scene S02: 这一场既没有节拍也没有对白——写出这一场发生了什么（谁做了什么），或者删掉这一场"]
    assert hits[0] in lint_screenplay(doc)
    doc["scenes"][1]["beats"] = ["叶澄站着没动"]
    assert check_scene_content(doc) == []


def test_lint_heading_without_place_or_time():
    doc = clean()
    doc["scenes"][0]["heading"] = "回忆"
    assert check_headings(doc) == [
        "scene S01: 场景标题「回忆」没写地点、时间——按「内/外 · 地点 · 时间」写全，拆镜时才知道这一场在哪、什么光线"
    ]
    doc["scenes"][0]["heading"] = "内 · 厨房"
    assert "没写时间" in check_headings(doc)[0]
    doc["scenes"][0]["heading"] = "厨房 · 清晨"
    assert "没写地点" in check_headings(doc)[0]
    # place / time 单独写了也算数（标题可以随便写）
    doc["scenes"][0].update(heading="回忆", place="共享厨房", time="清晨")
    assert check_headings(doc) == []


def test_lint_contract_echo():
    doc = clean()
    doc["contract"]["hook"] = "冷柜里的账本被人翻过"
    doc["contract"]["cliffhanger"] = "齐曼报了警"
    hits = check_contract_echo(doc)
    assert hits[0].startswith("全集：开场钩子「冷柜里的账本被人翻过」在第一场看不见——")
    assert hits[1].startswith("全集：集尾压力「齐曼报了警」没有落在最后一场——")
    assert check_contract_echo(clean()) == []
    assert check_contract_echo({"scenes": [], "contract": {"hook": "x"}}) == []
    assert check_contract_echo({"scenes": [{"heading": "内"}]}) == []   # 没契约就不判


def test_lint_cast_never_appears():
    doc = clean()
    doc["cast"].append({"id": "zhou", "name": "周姨"})
    assert check_cast_usage(doc) == [
        "cast zhou: 「周姨」在剧本里一句台词也没有，节拍里也没点名——给他一场真的要做点什么的戏，或者从人物表里删掉"
    ]
    # 节拍里点名了就算出场（哪怕一句台词也没有）
    doc["scenes"][0]["beats"].append("周姨在门口张望")
    assert check_cast_usage(doc) == []


def test_lint_too_many_scenes():
    doc = clean()
    assert check_scene_count(doc) == []
    doc["scenes"] = [dict(doc["scenes"][0], id=f"S{i:02d}") for i in range(1, MAX_SCENES + 2)]
    assert check_scene_count(doc) == [
        f"全集：一集写了 {MAX_SCENES + 1} 场——竖屏短剧一集通常不超过 {MAX_SCENES} 场，"
        "场次太碎每一场都来不及改变什么；把同地点同时间的场次合并"
    ]


def test_lint_survives_a_broken_file():
    """模型刚吐出来的剧本什么形状都可能有：lint 不许炸，形状不对就跳过那条判据。"""

    assert lint_screenplay({"scenes": "不是列表", "cast": "也不是"}) == []
    assert lint_screenplay(None) == []
    assert lint_screenplay({"scenes": [{"beats": None, "dialogue": None}]})[0].startswith("scene S01: ")


def test_warnings_become_problem_cards():
    doc = clean()
    doc["scenes"][1].update(beats=[], dialogue=[])
    card = problem_card(lint_screenplay(doc)[0])
    assert card["scope"] == "scene" and card["target"] == "S02" and card["kind"] == "screenplay"
    assert card["impact"] == "这一场既没有节拍也没有对白" and card["fix"].startswith("写出这一场发生了什么")
    assert classify("全集：一集写了 15 场——场次太碎每一场都来不及改变什么") == "screenplay"


# ---------------------------------------------------------------- 接口

@pytest.fixture
def root(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, scripts_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(scripts_router, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(scripts_router.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    return tmp_path


def write_screenplay(root, episode_id: str, doc: dict) -> None:
    (root / "content" / "story_video" / f"{episode_id}.screenplay.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_get_returns_editor_payload(root):
    client = TestClient(app)
    doc = clean()
    doc["scenes"][1].update(beats=[], dialogue=[])
    write_screenplay(root, "EP-1", doc)
    r = client.get("/api/episodes/storyvideo/EP-1/screenplay")
    assert r.status_code == 200
    body = r.json()
    assert body["episode_id"] == "EP-1" and body["screenplay"]["scenes"][0]["id"] == "S01"
    # 空掉的最后一场：既报「什么都没发生」，也报「集尾压力没落在最后一场」
    assert body["warnings"] == [
        "scene S02: 这一场既没有节拍也没有对白——写出这一场发生了什么（谁做了什么），或者删掉这一场",
        "全集：集尾压力「叶澄把备用钥匙交给齐曼」没有落在最后一场——最后一场要写出某人已经决定做什么、或者某个后果已经启动",
    ]
    assert [(p["scope"], p["target"], p["kind"]) for p in body["problems"]] == [
        ("scene", "S02", "screenplay"), ("episode", "", "screenplay")]
    assert body["stats"] == {"scenes": 2, "dialogue_lines": 2, "words": 35}
    assert body["text"].startswith("《厨房关停》")   # 纯文本预览仍在（第二步提示词用同一份排版）
    assert client.get("/api/episodes/storyvideo/EP-9/screenplay").status_code == 404


def test_put_normalizes_validates_and_relints(root):
    client = TestClient(app)
    write_screenplay(root, "EP-1", clean())
    doc = clean()
    doc["scenes"][0].pop("id")
    doc["scenes"][0]["heading"] = "回忆"
    doc["scenes"][0]["beats"].append("")
    r = client.put("/api/episodes/storyvideo/EP-1/screenplay", json={"screenplay": doc})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["screenplay"]["scenes"][0]["id"] == "S01"          # id 补上了
    assert body["screenplay"]["scenes"][0]["beats"] == ["齐曼把订单拍在桌上", "叶澄合上排班本"]
    assert any(w.startswith("scene S01: 场景标题「回忆」") for w in body["warnings"])
    assert body["stats"]["scenes"] == 2
    saved = json.loads((root / "content" / "story_video" / "EP-1.screenplay.json").read_text(encoding="utf-8"))
    assert saved["scenes"][0]["id"] == "S01" and saved["contract"]["hook"] == "齐曼当众要求出示完整排班记录"
    # 校验没过：400 + 一句人话，磁盘上那份不动
    bad = clean()
    bad["scenes"][0]["dialogue"][0]["who"] = "路人甲"
    r = client.put("/api/episodes/storyvideo/EP-1/screenplay", json={"screenplay": bad})
    assert r.status_code == 400 and "说话人「路人甲」不在人物表里" in r.json()["detail"]
    assert r.json()["detail"].startswith("剧本没通过校验，没有保存：")
    assert json.loads((root / "content" / "story_video" / "EP-1.screenplay.json").read_text(encoding="utf-8"))["scenes"][0]["heading"] == "回忆"
    assert client.put("/api/episodes/storyvideo/EP-1/screenplay", json={"screenplay": {"scenes": []}}).status_code == 400
    assert client.put("/api/episodes/storyvideo/坏 id/screenplay", json={"screenplay": clean()}).status_code == 400


def test_edited_screenplay_still_drafts(root, monkeypatch):
    """编辑器改完 → PUT → 拆镜：第二步拿到的是改过的那一份，契约照抄剧本。"""

    client = TestClient(app)
    write_screenplay(root, "EP-1", clean())
    doc = clean()
    doc["scenes"][0]["dialogue"][0]["zh"] = "排班记录，现在。"
    assert client.put("/api/episodes/storyvideo/EP-1/screenplay", json={"screenplay": doc}).status_code == 200
    seen = {}

    def fake_generate_draft(**kw):
        seen.update(kw)
        return {"title_zh": "t", "logline": "l", "frame_style": "f", "scene_anchor": "s", "camera": "c", "neg": "n",
                "cast": {"ye": {"block": "b"}},
                "shots": [{"id": "S1", "dur": 5, "cast": ["ye"], "shot": "中景", "art": "a", "motion": "m", "lines": []}]}, None, 1, "settings"

    monkeypatch.setattr(scripts_router, "generate_draft", fake_generate_draft)
    r = client.post("/api/scripts/jobs/draft", json={
        "episode_id": "EP-1", "direction": "厨房", "target_seconds": 60,
        "camera_profile": "dark_comedy_vertical", "use_screenplay": True})
    assert r.status_code == 200, r.text
    assert seen["screenplay"]["scenes"][0]["dialogue"][0]["zh"] == "排班记录，现在。"
    job = client.get(f"/api/scripts/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded"
    assert job["result"]["spec"]["episode_contract"]["hook"] == "齐曼当众要求出示完整排班记录"


def test_screenplay_job_stores_a_normalized_document(root, monkeypatch):
    """出剧本的 job：模型给的场次没 id / 有空节拍，落盘前先归一化，编辑器一打开就是干净的。"""

    client = TestClient(app)
    raw = clean()
    raw["scenes"][0].pop("id")
    raw["scenes"][0]["beats"].append("  ")
    monkeypatch.setattr(scripts_router, "generate_screenplay",
                        lambda **kw: (json.loads(json.dumps(raw)), None, 3, "settings"))
    r = client.post("/api/scripts/jobs/screenplay", json={
        "episode_id": "EP-5", "direction": "厨房", "target_seconds": 60, "camera_profile": "dark_comedy_vertical"})
    assert r.status_code == 200, r.text
    saved = json.loads((root / "content" / "story_video" / "EP-5.screenplay.json").read_text(encoding="utf-8"))
    assert saved["scenes"][0]["id"] == "S01" and saved["scenes"][0]["beats"] == ["齐曼把订单拍在桌上", "叶澄合上排班本"]
    # 归一化不过的（模型写了人物表里没有的说话人）也绝不丢掉这次真花钱的产出
    broken = clean()
    broken["scenes"][0]["dialogue"][0]["who"] = "路人甲"
    monkeypatch.setattr(scripts_router, "generate_screenplay",
                        lambda **kw: (json.loads(json.dumps(broken)), None, 3, "settings"))
    r = client.post("/api/scripts/jobs/screenplay", json={
        "episode_id": "EP-6", "direction": "厨房", "target_seconds": 60, "camera_profile": "dark_comedy_vertical"})
    assert r.status_code == 200
    body = client.get("/api/episodes/storyvideo/EP-6/screenplay").json()
    assert body["screenplay"]["scenes"][0]["dialogue"][0]["who"] == "路人甲"
