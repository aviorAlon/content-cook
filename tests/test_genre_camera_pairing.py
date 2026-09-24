"""题材 × 镜头风格搭配（DESIGN §48）：搭配表完整性、lint 只在「会打架」时出声、spec 没有 genre 时什么都不做、
问题卡分类「搭配」、告警在英文下翻得干净、题材接口带搭配。
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from storyvideo import lint, pairing
from storyvideo.pairing import PAIRING_PATH, pairing_for, pairing_table, pairing_verdict, pairing_warning
from storyvideo.prompts import camera_library
from storyvideo.scriptgen import find_genre, genre_cards
from webui import i18n
from webui.app import app
from webui.problems import KIND_LABELS, problem_card

client = TestClient(app)

_HAN = re.compile(r"[一-鿿]")
_QUOTED = re.compile(r"「[^」]*」|《[^》]*》|\"[^\"]*\"")


def _cjk_left(text: str) -> bool:
    return bool(_HAN.search(_QUOTED.sub("", text)))


def _spec(genre, profile="suspense_thriller"):
    spec = {"episode_id": "EP-PAIR", "camera_profile": profile, "shots": []}
    if genre is not ...:
        spec["genre"] = genre
    return spec


def test_pairing_file_covers_every_genre_with_known_profiles():
    data = json.loads(PAIRING_PATH.read_text(encoding="utf-8"))
    assert data["_meta"]["basis"].startswith("搭配是本仓库人工设定（2026-09-11）") and "drama-skills" in data["_meta"]["basis"]
    rows = data["pairings"]
    assert set(rows) == {c["id"] for c in genre_cards()} and len(rows) == 12
    profiles = set(camera_library()["profiles"])
    for gid, row in rows.items():
        assert set(row) == {"recommended", "avoid", "why"}, gid
        assert row["recommended"], gid
        assert set(row["recommended"]) <= profiles and set(row["avoid"]) <= profiles, gid
        assert len(set(row["recommended"])) == len(row["recommended"]) and len(set(row["avoid"])) == len(row["avoid"]), gid
        assert not set(row["recommended"]) & set(row["avoid"]), gid          # 不能同时推荐又回避
        assert row["why"].strip() and "——" not in row["why"], gid            # 「——」会把问题卡劈错
    # avoid 只放真打架的组合：总数不超过题材数
    assert sum(len(r["avoid"]) for r in rows.values()) <= len(rows)
    assert pairing_table() == rows


def test_pairing_follows_the_genre_cards_own_logic():
    rows = pairing_table()
    assert "suspense_thriller" in rows["xuanyi-guize"]["recommended"]
    assert {"short_drama_vertical", "tv_drama"} <= set(rows["zhichang-xiju"]["recommended"])
    assert "suspense_thriller" in rows["zhichang-xiju"]["avoid"]
    for gid in ("xianxia-xiuzhen", "dongzuo-renwu"):
        assert set(rows[gid]["recommended"]) <= {"anime_action", "classic_film"}, gid
    assert pairing_verdict("zhichang-xiju", "tv_drama") == "recommended"
    assert pairing_verdict("zhichang-xiju", "suspense_thriller") == "avoid"
    assert pairing_verdict("zhichang-xiju", "classic_film") == ""
    assert pairing_verdict("zhichang-xiju", "") == "" and pairing_verdict("nope", "tv_drama") == ""
    assert pairing_for("nope") == {"recommended": [], "avoid": [], "why": ""}


def test_lint_warns_on_avoid_with_names_why_and_alternatives():
    warnings = lint.check_genre_camera(_spec("职场喜剧"))
    assert len(warnings) == 1
    w = warnings[0]
    why = pairing_for("zhichang-xiju")["why"]
    assert w == f"全集：题材「职场喜剧」配镜头风格「suspense_thriller」容易打架：{why}——换成 short_drama_vertical / tv_drama，或确认这就是想要的反差"
    # id / 别名 / {id} 形状都认；check_all 接上了
    assert lint.check_genre_camera(_spec("zhichang-xiju")) == warnings
    assert lint.check_genre_camera(_spec("办公室")) == warnings
    assert lint.check_genre_camera(_spec({"id": "zhichang-xiju", "name": "职场喜剧"})) == warnings
    assert w in lint.check_all(_spec("zhichang-xiju"))
    assert "check_genre_camera" in lint.__all__


def test_lint_is_silent_for_recommended_and_neutral_pairs():
    assert lint.check_genre_camera(_spec("职场喜剧", "short_drama_vertical")) == []   # 推荐
    assert lint.check_genre_camera(_spec("职场喜剧", "classic_film")) == []           # 既不推荐也不回避：不唠叨
    assert lint.check_genre_camera(_spec("悬疑规则", "suspense_thriller")) == []


def test_lint_is_a_noop_without_a_usable_genre(monkeypatch):
    assert lint.check_genre_camera(_spec(...)) == []            # 没有 genre 字段（兄弟改动落地前的老 spec）
    for genre in (None, "", "   ", "火星题材", 3, ["zhichang-xiju"], {"id": ""}):
        assert lint.check_genre_camera(_spec(genre)) == [], genre
    assert lint.check_genre_camera({"genre": "职场喜剧", "shots": []}) == []   # 没有 camera_profile
    assert not any("配镜头风格" in w for w in lint.check_all(_spec(...)))
    # 搭配表缺了：照常不报
    monkeypatch.setattr(pairing, "PAIRING_PATH", PAIRING_PATH.with_name("no_such_pairing.json"))
    assert lint.check_genre_camera(_spec("职场喜剧")) == []


def test_problem_card_kind_is_pairing():
    card = problem_card(lint.check_genre_camera(_spec("职场喜剧"))[0])
    assert card["kind"] == "pairing" and KIND_LABELS["pairing"] == "搭配"
    assert card["scope"] == "episode" and card["target"] == ""
    assert card["impact"].startswith("题材「职场喜剧」配镜头风格「suspense_thriller」容易打架：")
    assert card["fix"] == "换成 short_drama_vertical / tv_drama，或确认这就是想要的反差"
    # 理由里有「运镜」「表演」这类别的分类的词（家庭关系那条）也不会被分走
    family = problem_card(lint.check_genre_camera(_spec("家庭关系", "anime_action"))[0])
    assert family["kind"] == "pairing"


@pytest.mark.parametrize("genre_id", [c["id"] for c in genre_cards()])
def test_warning_translates_fully_for_every_genre(genre_id):
    """每张卡的理由都有英文：整句和问题卡两半在英文下不剩中文（改了 why 忘了改 i18n_rules 就挂在这里）。"""

    card = find_genre(genre_id)
    entry = pairing_for(genre_id)
    i18n._compiled = None
    for profile in entry["avoid"] or ["anime_action"]:
        for variant in (entry, {**entry, "recommended": []}):
            text = pairing_warning(card["name"], profile, variant)
            out = i18n.tr(text, "en")
            assert out != text and not _cjk_left(out), out
            assert out.startswith("Whole episode: genre ") and f'camera style "{profile}"' in out
            parts = problem_card(text)
            for half in (parts["impact"], parts["fix"]):
                got = i18n.tr(half, "en")
                assert got != half and not _cjk_left(got), (half, got)


def test_workplace_comedy_warning_english_wording():
    i18n._compiled = None
    out = i18n.tr(lint.check_genre_camera(_spec("职场喜剧"))[0], "en")
    assert out.startswith('Whole episode: genre "Workplace comedy" with camera style "suspense_thriller" tends to clash: ')
    assert out.endswith(" — switch to short_drama_vertical / tv_drama, or confirm the contrast is intended")


def test_genres_endpoint_carries_pairing():
    rows = {r["id"]: r for r in client.get("/api/scripts/genres").json()}
    assert rows["zhichang-xiju"]["pairing"] == pairing_for("zhichang-xiju")
    assert all(set(r["pairing"]) == {"recommended", "avoid", "why"} and r["pairing"]["recommended"] for r in rows.values())
