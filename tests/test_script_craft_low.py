"""剧本工艺清单的三件小事（docs/script-craft-gap.md）：无形态时的单镜时长上限、契约「有意留白」豁免、关系场景。
每条判据一个坏例子触发 + 示例剧集零新告警；老项目不写新字段零变化。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import lint as L
from storyvideo import series
from storyvideo.contract import deferred_text, is_deferred, payoff_status
from storyvideo.spec import SCENE_TYPES, SpecError, normalize_spec
from webui import deps
from webui.app import app
from webui.problems import problem_card
from webui.routers import episodes as episodes_router

ROOT = Path(__file__).resolve().parents[1]


def _spec(**extra):
    base = {
        "id": "LOW-T", "output_root": "output/story_video/LOW-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"lin": {"name": "林小满", "block": "黑发。"}, "zhou": {"name": "周野", "block": "短发。"}},
        "shots": [
            {"id": "S1", "dur": 6, "cast": ["lin", "zhou"], "shot": "中景，平视", "art": "天台夜里", "motion": "周野把一封信递到她面前",
             "lines": [{"who": "zhou", "zh": "我喜欢你很久了。"}]},
            {"id": "S2", "dur": 5, "cast": ["lin"], "shot": "近景，平视", "art": "林小满", "motion": "她低头看信"},
            {"id": "S3", "dur": 5, "cast": ["lin", "zhou"], "shot": "特写，平视", "art": "两只手", "motion": "手停在半空"},
        ],
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------- 1. 无形态时的单镜时长上限


def test_duration_ceiling_with_or_without_form():
    spec = _spec()
    spec["shots"][0]["dur"] = 12
    msgs = L.check_shot_duration_ceiling(spec)
    assert msgs == ["shot S1: 单镜时长 12 秒——视频模型单次生成多在 5–10 秒内稳，再长动作容易散；拆成两镜，每镜只管一件事"]
    card = problem_card(msgs[0])
    assert card["scope"] == "shot" and card["target"] == "S1" and card["kind"] == "timing"
    assert card["impact"] == "单镜时长 12 秒" and card["fix"].startswith("视频模型单次生成")
    # 10 秒整不提；选了形态照判——check_form 只看全集平均单镜时长，不看单镜
    spec["shots"][0]["dur"] = 10
    assert L.check_shot_duration_ceiling(spec) == []
    spec["shots"][0]["dur"] = 12
    spec["form"] = "vertical_drama"
    assert len(L.check_shot_duration_ceiling(spec)) == 1
    assert any("单镜时长 12 秒" in m for m in L.check_all(_spec(shots=[{**_spec()["shots"][0], "dur": 12}])))


# ---------------------------------------------------------------------------- 2. 契约「有意留白」豁免

CONTRACT = {
    "premise": "p", "hook": "周野把信递到她面前", "cliffhanger": "手停在半空",
    "must_pay_off": ["一封信递到她面前", "信里写了谁的名字（有意留白）", "她为什么不接（留白）", "她把信撕了"],
    "carry_over": ["信还在她手里"],
}


def test_deferred_markers():
    assert is_deferred("信里写了谁的名字（有意留白）") and is_deferred("x（留白）") and is_deferred("x (留白)") and is_deferred(" x(有意留白) ")
    assert not is_deferred("留白的信") and not is_deferred("") and not is_deferred("有意留白在前面的")
    assert deferred_text("信里写了谁的名字（有意留白）") == "信里写了谁的名字"
    assert deferred_text("她为什么不接（留白）") == "她为什么不接" and deferred_text("普通条目") == "普通条目"


def test_payoff_status_splits_deferred_and_contract_lint_skips_it():
    spec = _spec(episode_contract=CONTRACT)
    paid, unpaid, deferred = payoff_status(spec)
    assert paid == ["一封信递到她面前"]
    assert unpaid == ["她把信撕了"]
    assert deferred == ["信里写了谁的名字（有意留白）", "她为什么不接（留白）"]
    msgs = L.check_contract(spec)
    assert any("「她把信撕了」在任何一镜" in m for m in msgs)
    assert not any("留白" in m for m in msgs)
    # 契约归一化不动条目文字（标记原样保留），老项目没有标记的条目照旧判
    normalized = normalize_spec(spec, project_root=ROOT)
    assert normalized["episode_contract"]["must_pay_off"] == CONTRACT["must_pay_off"]


def test_series_memory_records_deferred_as_open_thread(tmp_path):
    spec = _spec(episode_contract=CONTRACT, series={"id": "letter", "episode_no": 1}, title_zh="天台")
    entry = series.build_entry(spec)
    assert entry["established"] == ["一封信递到她面前"]
    # 留白条目记进未决并统一标「（有意留白）」（「（留白）」也改成全称），排在 carry_over 之后
    assert entry["open_threads"] == ["信还在她手里", "信里写了谁的名字（有意留白）", "她为什么不接（有意留白）"]
    out = series.upsert_episode(tmp_path, spec)
    handoff = series.handoff_section(out["entry"])
    assert "  - 她为什么不接（有意留白）" in handoff and "已成立的事实" in handoff
    # 没有留白条目的项目条目形状不变
    plain = series.build_entry(_spec(episode_contract={**CONTRACT, "must_pay_off": ["一封信递到她面前"]}, series={"id": "letter"}))
    assert plain["open_threads"] == ["信还在她手里"]


# ---------------------------------------------------------------------------- 3. 关系场景


def _typed(kind, motion, lines=None, art="天台夜里"):
    spec = _spec()
    spec["shots"][0].update({"scene_type": kind, "motion": motion, "art": art, "lines": lines or []})
    return spec


def test_scene_type_normalization():
    spec = normalize_spec(_typed("Rejection", "她后退半步"), project_root=ROOT)
    assert spec["shots"][0]["scene_type"] == "rejection"
    assert "scene_type" not in normalize_spec(_typed("", "她后退半步"), project_root=ROOT)["shots"][0]
    with pytest.raises(SpecError, match="scene_type must be one of"):
        normalize_spec(_typed("kiss", "她后退半步"), project_root=ROOT)
    with pytest.raises(SpecError, match="scene_type must be one of"):
        normalize_spec(_typed(3, "她后退半步"), project_root=ROOT)
    assert SCENE_TYPES == ("confession", "reunion", "forgiveness", "rejection", "breakup")
    assert set(L.SCENE_TYPE_LABELS) == set(SCENE_TYPES)


def test_relation_boundaries_silent_without_scene_type():
    spec = _spec()
    spec["shots"][0]["motion"] = "周野强吻她，她没有任何反应"
    assert L.check_relation_boundaries(spec) == []
    spec["shots"][0]["scene_type"] = "unknown"
    assert L.check_relation_boundaries(spec) == []


def test_rejection_scene():
    msgs = L.check_relation_boundaries(_typed("rejection", "她看着信，说不行", [{"who": "lin", "zh": "对不起，我不能。"}]))
    assert msgs == ["shot S1: 这一镜是「拒绝」场景，正文里看不到拒绝的一方怎么把人挡开（退半步 / 抽手 / 转身 / 走开）"
                    "——光嘴上说不行不够，观众得看见人往外撤；给拒绝的一方写一个表演动作（退半步、把手抽回来、侧过身让开）"]
    assert problem_card(msgs[0])["kind"] == "performance"
    # 有挡开动作就不提；挡开之后还有强行接触 → 告警，「逼近」被「继续逼近」包含时只报长的
    assert L.check_relation_boundaries(_typed("rejection", "她后退半步，把信推回去")) == []
    msgs = L.check_relation_boundaries(_typed("rejection", "她抽回手，往后退；周野继续逼近，抓住手腕，强吻她"))
    assert len(msgs) == 1 and msgs[0].startswith("shot S1: 这一镜是「拒绝」场景，正文里还有「强吻、抓住手腕、继续逼近」——对方已经说了不，之后再硬贴上去就是冒犯，不能当深情写")
    assert problem_card(msgs[0])["kind"] == "performance"
    # 没挡开动作 + 强行接触：两条都提
    assert len(L.check_relation_boundaries(_typed("rejection", "周野把她按在墙上"))) == 2


def test_breakup_scene():
    msgs = L.check_relation_boundaries(_typed("breakup", "两人在客厅对吼，她哭了", [{"who": "lin", "zh": "我们分手吧。"}]))
    assert len(msgs) == 1 and msgs[0].startswith("shot S1: 这一镜是「分手」场景，正文里没有一个做了就撤不回的举动（拎起行李 / 关上门 / 交回钥匙 / 把对方的东西还回去 / 头也不回）")
    assert problem_card(msgs[0])["kind"] == "performance"
    assert L.check_relation_boundaries(_typed("breakup", "她把钥匙放在桌上，拎起行李走出门")) == []


def test_forgiveness_scene():
    msgs = L.check_relation_boundaries(_typed("forgiveness", "周野抱住她，她站着", [{"who": "lin", "zh": "我原谅你。"}]))
    assert [m.split("——")[0] for m in msgs] == [
        "shot S1: 这一镜是「原谅」场景，正文里没交代两人往后算什么（和好 / 不和好 / 先隔开一段 / 再看看）",
        "shot S1: 这一镜是「原谅」场景，正文里有「抱住」，但只有一方在靠近，受伤的一方没有回应的动作",
    ]
    assert {problem_card(m)["kind"] for m in msgs} == {"performance"}
    assert L.check_relation_boundaries(_typed("forgiveness", "周野抱住她，她回抱，两人保持距离先做朋友")) == []
    # 落定了结果、没有亲密接触：什么都不提
    assert L.check_relation_boundaries(_typed("forgiveness", "她点头", [{"who": "lin", "zh": "先观察期吧。"}])) == []


def test_reunion_scene():
    msgs = L.check_relation_boundaries(_typed("reunion", "两人在车站相遇，聊起近况"))
    assert len(msgs) == 1 and msgs[0].startswith("shot S1: 这一镜是「重逢」场景，正文里看不出是凭什么认出来的（停步 / 一件旧物 / 一道疤 / 一个老习惯 / 背影）")
    assert problem_card(msgs[0])["kind"] == "performance"
    assert L.check_relation_boundaries(_typed("reunion", "她看到那条围巾，停步")) == []


def test_confession_scene():
    msgs = L.check_relation_boundaries(_typed("confession", "周野把信递过去", [{"who": "zhou", "zh": "我喜欢你很久了。"}]))
    assert len(msgs) == 1 and msgs[0].startswith("shot S1: 这一镜是「表白」场景，正文里看不出被表白的一方接没接住：对方没有任何动静，也没交代是故意不答——成没成，观众得从对方身上看出来")
    assert problem_card(msgs[0])["kind"] == "performance"
    assert L.check_relation_boundaries(_typed("confession", "周野把信递过去，她愣住，别过脸")) == []
    assert L.check_relation_boundaries(_typed("confession", "周野把信递过去，她没开口")) == []
    # 都进 check_all
    assert any("「表白」场景" in m for m in L.check_all(_typed("confession", "周野把信递过去")))


# ---------------------------------------------------------------------------- 编辑接口：scene_type 存取


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    (content_dir / "LOW-T.json").write_text(json.dumps(_spec(), ensure_ascii=False), encoding="utf-8")
    return content_dir / "LOW-T.json"


def test_shot_editor_reads_and_writes_scene_type(project):
    client = TestClient(app)
    r = client.put("/api/episodes/storyvideo/LOW-T/shots/S1", json={"scene_type": "confession"})
    assert r.status_code == 200, r.text
    assert r.json()["shot"]["scene_type"] == "confession"
    assert next(s for s in json.loads(project.read_text(encoding="utf-8"))["shots"] if s["id"] == "S1")["scene_type"] == "confession"
    # 不认识的值：校验挡住，不写盘
    before = project.read_text(encoding="utf-8")
    assert client.put("/api/episodes/storyvideo/LOW-T/shots/S1", json={"scene_type": "kiss"}).status_code == 400
    assert project.read_text(encoding="utf-8") == before
    # 清掉
    assert client.put("/api/episodes/storyvideo/LOW-T/shots/S1", json={"scene_type": ""}).status_code == 200
    assert "scene_type" not in next(s for s in json.loads(project.read_text(encoding="utf-8"))["shots"] if s["id"] == "S1")
    assert client.get("/api/episodes/storyvideo/LOW-T").json()["shots"][0]["scene_type"] == ""


# ---------------------------------------------------------------------------- 示例剧集零新告警


def test_example_episode_has_no_new_warnings():
    spec = json.loads((ROOT / "examples" / "episodes" / "first_episode.json").read_text(encoding="utf-8-sig"))
    assert L.check_shot_duration_ceiling(spec) == []
    assert L.check_relation_boundaries(spec) == []
    assert L.check_contract(spec) == []
    assert payoff_status(spec) == ([], [], [])
