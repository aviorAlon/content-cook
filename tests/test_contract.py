"""单集契约（DESIGN §32）：``episode_contract`` 归一化、lint 命中/不命中、问题卡分类、globals PUT 可改、老项目零变化。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from storyvideo import lint
from storyvideo.contract import coerce_contract, mentioned, normalize_contract, normalize_series, payoff_status
from storyvideo.spec import SpecError, normalize_spec
from webui import deps, i18n
from webui.app import app
from webui.problems import problem_card
from webui.routers import episodes as episodes_router

CONTRACT = {
    "premise": "叶澄刚删去一条换班记录，齐曼手里有订单记录",
    "hook": "齐曼当众要求出示完整排班记录",
    "cliffhanger": "叶澄把备用钥匙交给齐曼",
    "must_pay_off": ["订单缺口的原因", "叶澄承认改过排班"],
    "carry_over": ["齐曼不知道冷柜后的账本"],
}


def _spec(contract=CONTRACT, **extra):
    spec = {
        "id": "CT-T", "output_root": "output/story_video/CT-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"ye": {"name": "叶澄", "block": "黑发。"}, "qi": {"name": "齐曼", "block": "短发。"}},
        "shots": [
            {"id": "S1", "dur": 5, "cast": ["ye", "qi"], "shot": "中景，平视", "art": "共享厨房清晨", "motion": "齐曼把订单拍在桌上",
             "lines": [{"who": "qi", "zh": "把完整的排班记录拿出来。"}]},
            {"id": "S2", "dur": 5, "cast": ["ye"], "shot": "近景，平视", "art": "叶澄站在排班表前", "motion": "她翻开记录",
             "lines": [{"who": "ye", "zh": "记录就在这里。"}]},
            {"id": "S3", "dur": 6, "cast": ["ye", "qi"], "shot": "中景，平视", "art": "两人对峙", "motion": "叶澄承认改过排班，但不说为谁",
             "lines": [{"who": "ye", "zh": "订单缺口是我调的灶位。"}]},
            {"id": "S4", "dur": 5, "cast": ["ye", "qi"], "shot": "特写，平视", "art": "钥匙", "motion": "叶澄把备用钥匙交给齐曼，转身离开",
             "lines": []},
        ],
    }
    if contract is not None:
        spec["episode_contract"] = contract
    spec.update(extra)
    return spec


# ---- 归一化


def test_normalize_contract_strings_and_lists():
    out = normalize_contract({"premise": " p ", "hook": "h", "must_pay_off": "一\n二；三", "carry_over": ["a", " ", "b"]})
    assert out == {"premise": "p", "hook": "h", "cliffhanger": "", "must_pay_off": ["一", "二", "三"], "carry_over": ["a", "b"]}
    assert normalize_contract(None) is None
    assert normalize_contract({}) is None and normalize_contract({"premise": "", "must_pay_off": []}) is None
    with pytest.raises(ValueError, match="不认识的字段：foo"):
        normalize_contract({"foo": 1})
    with pytest.raises(ValueError, match="episode_contract.hook 要是一句话"):
        normalize_contract({"hook": 3})
    with pytest.raises(ValueError, match="每一条都要是字符串"):
        normalize_contract({"must_pay_off": [1]})
    # 宽松版：救得回来的救，救不回来的丢
    assert coerce_contract({"hook": "h", "must_pay_off": ["a", 2], "junk": 1}) == {"premise": "", "hook": "h", "cliffhanger": "", "must_pay_off": ["a"], "carry_over": []}
    assert coerce_contract("nope") is None and coerce_contract({"hook": 5}) is None


def test_normalize_series():
    assert normalize_series({"id": " s1 ", "episode_no": "3"}) == {"id": "s1", "episode_no": 3}
    assert normalize_series({"id": "s1"}) == {"id": "s1"}
    assert normalize_series(None) is None
    for bad, msg in (({"id": ""}, "不能为空"), ({"id": "a b"}, "只能用英文"), ({"id": "s", "episode_no": 0}, "正整数"),
                     ({"id": "s", "episode_no": "x"}, "正整数"), ({"id": "s", "foo": 1}, "不认识的字段"), ("s", "要是对象")):
        with pytest.raises(ValueError, match=msg):
            normalize_series(bad)


def test_spec_normalizes_contract_and_series_and_leaves_old_projects_alone(tmp_path):
    spec = normalize_spec(_spec(series={"id": "kitchen", "episode_no": "2"}), project_root=tmp_path)
    assert spec["episode_contract"]["must_pay_off"] == CONTRACT["must_pay_off"]
    assert spec["series"] == {"id": "kitchen", "episode_no": 2}
    plain = normalize_spec(_spec(contract=None), project_root=tmp_path)
    assert "episode_contract" not in plain and "series" not in plain
    # 空契约归一成没写；坏形状 SpecError
    assert "episode_contract" not in normalize_spec(_spec(contract={"premise": ""}), project_root=tmp_path)
    with pytest.raises(SpecError, match="episode_contract 里不认识的字段"):
        normalize_spec(_spec(contract={"nope": "x"}), project_root=tmp_path)
    with pytest.raises(SpecError, match="series.id"):
        normalize_spec(_spec(series={"id": "bad id"}), project_root=tmp_path)
    # 契约不进 source_hash 之外的东西：source_hash 只看原始 JSON，两份只差契约的 spec 镜次 hash 相同
    assert [s["source_hash"] for s in spec["shots"]] == [s["source_hash"] for s in plain["shots"]]


# ---- 判据与 lint


def test_mentioned_uses_tokens_then_fragment_ratio():
    blob = "齐曼把订单拍在桌上 把完整的排班记录拿出来。"
    assert mentioned("齐曼当众要求出示完整排班记录", blob)       # 片段命中比例够
    assert mentioned("订单缺口的原因", "订单缺口是我调的灶位")     # 二字片段
    assert mentioned("the killer confesses", "The killer confesses at dawn")
    assert not mentioned("火星上的账本", blob)
    assert not mentioned("", blob) and not mentioned("x", "")


def test_check_contract_hits_and_misses():
    assert lint.check_contract(_spec(contract=None)) == []
    assert lint.check_contract(_spec()) == []          # 钩子在前两镜、集尾在最后一镜、承诺都兑现了
    spec = _spec(contract={**CONTRACT, "hook": "审查员提前进入厨房", "cliffhanger": "齐曼报了警", "must_pay_off": ["订单缺口的原因", "冷柜后的账本被发现"]})
    msgs = lint.check_contract(spec)
    assert any(m.startswith("全集：契约里的钩子「审查员提前进入厨房」在前 2 镜") for m in msgs)
    assert any("集尾「齐曼报了警」在最后一镜 S4" in m for m in msgs)
    assert any("兑现的「冷柜后的账本被发现」在任何一镜" in m for m in msgs)
    assert not any("订单缺口的原因" in m for m in msgs)
    assert all("——" in m for m in msgs)
    assert any("episode_contract 要是对象" in m for m in lint.check_contract(_spec(contract="x")))
    assert lint.check_contract(spec) == [m for m in lint.check_all(spec) if "契约" in m or "episode_contract" in m]


def test_payoff_status():
    paid, unpaid, deferred = payoff_status(_spec(contract={**CONTRACT, "must_pay_off": ["订单缺口的原因", "冷柜后的账本被发现"]}))
    assert paid == ["订单缺口的原因"] and unpaid == ["冷柜后的账本被发现"] and deferred == []
    assert payoff_status(_spec(contract=None)) == ([], [], [])


def test_problem_card_kind_and_english():
    msg = lint.check_contract(_spec(contract={**CONTRACT, "hook": "审查员提前进入厨房"}))[0]
    card = problem_card(msg)
    assert card["scope"] == "episode" and card["kind"] == "contract"
    assert card["impact"].startswith("契约里的钩子") and card["fix"].startswith("开场要让观众")
    for text in (msg, card["impact"], card["fix"]):
        out = i18n.tr(text, "en")
        assert "契约" not in out and "找不到" not in out, out
    assert i18n.tr("episode_contract.hook 要是一句话（字符串）", "en") == "episode_contract.hook must be a sentence (string)"


# ---- 接口：详情带出、globals PUT 可改


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    (content_dir / "CT-T.json").write_text(json.dumps(_spec(), ensure_ascii=False), encoding="utf-8")
    return content_dir


def test_detail_and_globals_put(project):
    client = TestClient(app)
    d = client.get("/api/episodes/storyvideo/CT-T").json()
    assert d["episode_contract"]["hook"] == CONTRACT["hook"] and d["series"] is None
    new = {**CONTRACT, "hook": "审查员提前进入厨房", "carry_over": "一\n二"}
    r = client.put("/api/episodes/storyvideo/CT-T/globals", json={"values": {"episode_contract": new, "series": {"id": "kitchen", "episode_no": 1}}})
    assert r.status_code == 200, r.text
    assert not any("审查员提前进入厨房" in w for w in r.json()["warnings"])   # 契约的核对告警走 lint 通道，不在这个响应里
    saved = json.loads((project / "CT-T.json").read_text(encoding="utf-8"))
    assert saved["episode_contract"]["hook"] == "审查员提前进入厨房" and saved["series"] == {"id": "kitchen", "episode_no": 1}
    d = client.get("/api/episodes/storyvideo/CT-T").json()
    assert d["series"] == {"id": "kitchen", "episode_no": 1}
    assert any(c["kind"] == "contract" for c in d["problems"])
    # 坏形状 400，不写盘
    r = client.put("/api/episodes/storyvideo/CT-T/globals", json={"values": {"episode_contract": {"foo": 1}}})
    assert r.status_code == 400 and "不认识的字段" in r.text
    assert json.loads((project / "CT-T.json").read_text(encoding="utf-8"))["episode_contract"]["hook"] == "审查员提前进入厨房"
    # 清空 = 删键
    r = client.put("/api/episodes/storyvideo/CT-T/globals", json={"values": {"episode_contract": None, "series": ""}})
    assert r.status_code == 200
    saved = json.loads((project / "CT-T.json").read_text(encoding="utf-8"))
    assert "episode_contract" not in saved and "series" not in saved
