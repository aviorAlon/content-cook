"""题材卡（DESIGN §26，融合 drama-skills G25）：卡文件完整性、别名解析、系统提示词注入
只在给了题材时出现（不给时逐字节不变）、未知题材明确报错、接口与起草 job 参数透传。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from storyvideo import scriptgen
from storyvideo.scriptgen import (
    GENRE_CARDS_PATH,
    build_system_prompt,
    find_genre,
    generate_draft,
    genre_cards,
)
from webui.app import app

client = TestClient(app)

PROSE_FIELDS = ("core", "pressure", "strategies", "payoff", "production_notes")
LIST_FIELDS = ("aliases", "grains", "hooks", "pitfalls")
NO_DRIFT_LINE = "按这张卡定压力来源、场面颗粒和集尾钩子；不要漂移到别的题材；卡里的内容不出现在交付物里，只用来指导写作"


def test_cards_file_loads_with_12_complete_cards():
    data = json.loads(GENRE_CARDS_PATH.read_text(encoding="utf-8"))
    assert data["_meta"]["version"] == 1 and "drama-skills" in data["_meta"]["source"]
    cards = genre_cards()
    assert len(cards) == 12
    assert len({c["id"] for c in cards}) == 12 and len({c["name"] for c in cards}) == 12
    assert all(c["id"].isascii() for c in cards)
    for card in cards:
        for field in PROSE_FIELDS:
            assert isinstance(card[field], str) and card[field].strip(), (card["id"], field)
        for field in LIST_FIELDS:
            assert isinstance(card[field], list) and card[field] and all(x.strip() for x in card[field]), (card["id"], field)
        assert all(card["phases"][k].strip() for k in ("early", "mid", "late")), card["id"]
        assert card["disallowed_drift"] and all(x.strip() for x in card["disallowed_drift"]), card["id"]


def test_cards_carry_provenance_from_the_original_files():
    """出处（DESIGN §48）：confidence / basis 从原卡逐字抄录，source_file 指回原卡文件；原卡说例子是合成虚构的。"""

    data = json.loads(GENRE_CARDS_PATH.read_text(encoding="utf-8"))
    assert "合成虚构" in data["_meta"]["provenance"] and "不来自抽样统计" in data["_meta"]["provenance"]
    assert GENRE_CARDS_PATH.stat().st_size <= 42_000
    names = {c["name"] for c in genre_cards()}
    for card in genre_cards():
        for field in ("confidence", "basis", "source_file"):
            assert isinstance(card.get(field), str) and card[field].strip(), (card["id"], field)
        assert card["confidence"] in ("高", "中", "低"), card["id"]
        assert card["source_file"].endswith(".md") and card["source_file"][:-3] in names, card["id"]
        assert card["source_file"][:-3] == card["name"], card["id"]
    # 抽两张核对原卡的值：古装权谋「高」、破镜重圆「中」
    assert find_genre("古装权谋")["confidence"] == "高" and find_genre("破镜重圆")["confidence"] == "中"
    assert {c["basis"] for c in genre_cards()} == {"套件既有工艺文件与短剧写作经验归纳"}


def test_find_genre_by_id_name_and_alias():
    assert find_genre("qinzi-yinmi")["name"] == "亲子隐秘"
    assert find_genre("QINZI-YINMI")["name"] == "亲子隐秘"
    assert find_genre("亲子隐秘")["id"] == "qinzi-yinmi"
    assert find_genre("萌宝")["name"] == "亲子隐秘"
    assert find_genre("霸总")["name"] == "豪门婚恋"
    assert find_genre("规则怪谈")["name"] == "悬疑规则"
    assert find_genre(" 追妻 ")["name"] == "破镜重圆"
    assert find_genre("不存在的题材") is None
    assert find_genre("") is None and find_genre(None) is None
    # 别名不能跨卡撞车，否则下拉里选 A 实际注入 B
    seen: dict[str, str] = {}
    for card in genre_cards():
        for key in [card["id"], card["name"], *card["aliases"]]:
            assert key not in seen or seen[key] == card["id"], (key, seen.get(key), card["id"])
            seen[key] = card["id"]


def test_system_prompt_with_genre_appends_card_section():
    plain = build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh")
    with_card = build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh", genre="复仇打脸")
    card = find_genre("复仇打脸")
    assert with_card.startswith(plain)
    section = with_card[len(plain):]
    assert "题材卡：复仇打脸" in section
    assert card["grains"][0] in section and "- 场面颗粒：" in section
    assert card["hooks"][0] in section and "- 集尾钩子：" in section
    assert card["phases"]["late"] in section and "- 后期：" in section
    assert card["pitfalls"][0] in section and "- 常见坑：" in section
    assert section.rstrip().endswith(NO_DRIFT_LINE)
    # 出处字段是给人看的，不进提示词
    assert card["basis"] not in with_card and card["source_file"] not in with_card
    # 同一张卡按 id 和按别名给，注入的段落一模一样
    assert build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh", genre="fuchou-dalian") == with_card


def test_system_prompt_without_genre_is_unchanged():
    plain = build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh")
    assert "题材卡：" not in plain and NO_DRIFT_LINE not in plain
    assert build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh", genre=None) == plain
    assert build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh", genre="  ") == plain


def test_generate_draft_rejects_unknown_genre(monkeypatch):
    def boom(*a, **k):  # pragma: no cover - 走到这里说明校验没拦住
        raise AssertionError("未知题材不该打到模型")

    monkeypatch.setattr(scriptgen, "_dispatch", boom)
    with pytest.raises(ValueError, match="不认识的题材：火星题材"):
        generate_draft(
            episode_id="EP-1", direction="x", target_seconds=60, camera_profile="dark_comedy_vertical",
            prompt_language="zh", cast_hint=None, genre="火星题材",
        )


def test_generate_draft_passes_genre_section_to_dispatch(monkeypatch):
    seen = {}

    def fake_dispatch(system_prompt, user_prompt, *, json_schema):
        seen["system"] = system_prompt
        return {"shots": []}, None, 1, "settings"

    monkeypatch.setattr(scriptgen, "_dispatch", fake_dispatch)
    generate_draft(
        episode_id="EP-1", direction="x", target_seconds=60, camera_profile="dark_comedy_vertical",
        prompt_language="zh", cast_hint=None, genre="萌宝",
    )
    assert "题材卡：亲子隐秘" in seen["system"] and NO_DRIFT_LINE in seen["system"]


def test_genres_endpoint_lists_12_summaries():
    rows = client.get("/api/scripts/genres").json()
    assert len(rows) == 12
    assert set(rows[0]) == {"id", "name", "aliases", "core", "confidence", "basis", "source_file", "pairing"}
    assert all(r["core"] and r["name"] and r["id"] for r in rows)
    assert all(r["confidence"] and r["basis"] and r["source_file"] == f"{r['name']}.md" for r in rows)
    assert all(set(r["pairing"]) == {"recommended", "avoid", "why"} for r in rows)
    assert {r["name"] for r in rows} >= {"亲子隐秘", "仙侠修真", "复仇打脸", "破镜重圆", "身份错位"}


def test_draft_job_stores_genre_and_rejects_unknown(tmp_path, monkeypatch):
    from webui.routers import scripts as R

    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    monkeypatch.setattr(R, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(R, "JOBS_DIR", tmp_path / "jobs")
    seen = {}

    def fake_generate_draft(**kw):
        seen.update(kw)
        raise R.ScriptGenError("没配文本模型")

    monkeypatch.setattr(R, "generate_draft", fake_generate_draft)
    monkeypatch.setattr(R.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    body = {"direction": "一个人加班", "target_seconds": 60, "camera_profile": "dark_comedy_vertical", "genre": "zhichang-xiju"}
    r = client.post("/api/scripts/jobs/draft", json=body)
    assert r.status_code == 200, r.text
    assert seen["genre"] == "zhichang-xiju"
    job = client.get(f"/api/scripts/jobs/{r.json()['job_id']}").json()
    assert job["params"]["genre"] == "zhichang-xiju"
    # 不给题材：参数里是 None，不是漏字段
    r2 = client.post("/api/scripts/jobs/draft", json={**body, "genre": None, "episode_id": "EP-G2"})
    assert r2.status_code == 200 and client.get(f"/api/scripts/jobs/{r2.json()['job_id']}").json()["params"]["genre"] is None
    # 未知题材在建 job 前就 400，不进后台线程
    r3 = client.post("/api/scripts/jobs/draft", json={**body, "genre": "火星题材", "episode_id": "EP-G3"})
    assert r3.status_code == 400 and "不认识的题材" in r3.json()["detail"]
