"""关系场景的英文词表与拒绝点定序（storyvideo/lint.py）：每种场景英文正文至少一条命中 + 一条不误报（词边界：
"background" 不算 "back"）；拒绝点之前的强行接触不报、之后的报、没有拒绝点时同镜出现就报；弱拒绝词（不能 / 我不 / no /
don't…）不在台词里、也不是开口说的，就不算拒绝点，不会把前面的强行接触压掉；中英混写两边都查；告警句沿用中文
（i18n 规则已有），能拆成问题卡。"""

from __future__ import annotations

import json
from pathlib import Path

from storyvideo import lint as L
from webui.problems import problem_card

ROOT = Path(__file__).resolve().parents[1]


def _typed(kind, motion, lines=None, art="rooftop at night, city lights"):
    return {
        "id": "REL-EN", "output_root": "output/story_video/REL-EN", "prompt_language": "en",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "en"},
        "cast": {"lin": {"name": "Lin", "block": "black hair."}, "zhou": {"name": "Zhou", "block": "short hair."}},
        "shots": [{"id": "S1", "dur": 6, "cast": ["lin", "zhou"], "shot": "medium shot, eye level", "art": art, "motion": motion,
                   "scene_type": kind, "lines": lines or []}],
    }


def _impacts(spec):
    return [m.split("——")[0] for m in L.check_relation_boundaries(spec)]


# ---------------------------------------------------------------------------- 词边界与各场景的英文词表


def test_word_boundary_does_not_match_inside_words():
    assert L._en_word_re("back").search("she stands in the background") is None
    assert L._en_word_re("back").search("she steps back.") is not None
    assert L._en_word_re("don't").search("don’t touch me".lower().replace("’", "'")) is not None
    assert L._en_word_re("like").search("unlike before") is None
    assert L._en_word_re("eye level").search("camera at eye-level") is not None
    # 只禁词内撇号：直单引号引起来的词能命中，"stop's" / "don't" 里的不算
    assert L._en_word_re("no").search("she says 'no' twice") is not None
    assert L._en_word_re("stop").search("the stop's sign") is None
    assert L._en_word_re("don").search("don't") is None
    assert L._hits((), ("back",), "the background of the frame") == []
    assert L._hits((), ("back", "steps back"), "she steps back") == ["steps back"]


def test_rejection_scene_en():
    # "background" 不算挡开动作 → 缺挡开动作
    msgs = L.check_relation_boundaries(_typed("rejection", "She stands in the background while he talks", [{"who": "lin", "en": "I'm sorry."}]))
    assert msgs == ["shot S1: 这一镜是「拒绝」场景，正文里看不到拒绝的一方怎么把人挡开（退半步 / 抽手 / 转身 / 走开）"
                    "——光嘴上说不行不够，观众得看见人往外撤；给拒绝的一方写一个表演动作（退半步、把手抽回来、侧过身让开）"]
    assert problem_card(msgs[0])["kind"] == "performance"
    assert _impacts(_typed("rejection", "She steps back and pushes him away")) == []
    assert _impacts(_typed("rejection", "She pulls her hand back, turns away")) == []
    # 挡开动作之后的强行接触：中文告警句、英文词，按词表顺序
    msgs = L.check_relation_boundaries(_typed("rejection", "She steps back; he keeps advancing and grabs her wrist"))
    assert msgs == ["shot S1: 这一镜是「拒绝」场景，正文里还有「grabs her wrist、keeps advancing」——对方已经说了不，之后再硬贴上去就是冒犯，不能当深情写；删掉这些动作，"
                    "或让对方接受边界（松手、退开）；真要写越界，就写成冒犯的表演，镜头站在拒绝的一方这边"]
    assert problem_card(msgs[0])["kind"] == "performance"


def test_breakup_scene_en():
    assert _impacts(_typed("breakup", "They argue in the living room; she cries", [{"who": "lin", "en": "We're done."}])) == [
        "shot S1: 这一镜是「分手」场景，正文里没有一个做了就撤不回的举动（拎起行李 / 关上门 / 交回钥匙 / 把对方的东西还回去 / 头也不回）"]
    assert _impacts(_typed("breakup", "She puts the keys on the table, picks up her suitcase and walks out the door")) == []
    assert _impacts(_typed("breakup", "He takes off the ring and leaves it on the counter without looking back")) == []


def test_forgiveness_scene_en():
    assert _impacts(_typed("forgiveness", "He hugs her; she stands there", [{"who": "lin", "en": "I forgive you."}])) == [
        "shot S1: 这一镜是「原谅」场景，正文里没交代两人往后算什么（和好 / 不和好 / 先隔开一段 / 再看看）",
        "shot S1: 这一镜是「原谅」场景，正文里有「hugs」，但只有一方在靠近，受伤的一方没有回应的动作",
    ]
    assert _impacts(_typed("forgiveness", "He hugs her and she hugs him back; they agree to take it slow")) == []
    # 落定了结果、没有亲密接触：什么都不提
    assert _impacts(_typed("forgiveness", "She nods", [{"who": "lin", "en": "Just friends, for now."}])) == []


def test_reunion_scene_en():
    assert _impacts(_typed("reunion", "They meet at the station and chat about work")) == [
        "shot S1: 这一镜是「重逢」场景，正文里看不出是凭什么认出来的（停步 / 一件旧物 / 一道疤 / 一个老习惯 / 背影）"]
    assert _impacts(_typed("reunion", "She sees the same scarf and stops in her tracks")) == []
    assert _impacts(_typed("reunion", "He hears her voice from behind and freezes")) == []


def test_confession_scene_en():
    assert _impacts(_typed("confession", "He hands her the letter", [{"who": "zhou", "en": "I have liked you for years."}])) == [
        "shot S1: 这一镜是「表白」场景，正文里看不出被表白的一方接没接住：对方没有任何动静，也没交代是故意不答"]
    assert _impacts(_typed("confession", "He hands her the letter; she freezes and looks away")) == []
    assert _impacts(_typed("confession", "He hands her the letter; she says nothing")) == []
    assert any("「表白」场景" in m for m in L.check_all(_typed("confession", "He hands her the letter")))


def test_mixed_language_checks_both_tables():
    # 中文挡开动作 + 英文强行接触：两边都查
    assert _impacts(_typed("rejection", "她后退半步; he grabs her wrist")) == ["shot S1: 这一镜是「拒绝」场景，正文里还有「grabs her wrist」"]
    # 英文挡开动作 + 中文强行接触
    assert _impacts(_typed("rejection", "She steps back; 周野继续逼近")) == ["shot S1: 这一镜是「拒绝」场景，正文里还有「继续逼近」"]
    # 英文台词 + 中文动作：台词里的反应算数
    assert _impacts(_typed("confession", "周野把信递过去", [{"who": "lin", "en": "She nods."}])) == []


# ---------------------------------------------------------------------------- 拒绝点定序


def test_rejection_point_orders_forced_contact():
    # 拒绝点（挡开动作）之前的强行接触算戏的前半段：不报；之后的报
    assert _impacts(_typed("rejection", "He grabs her wrist; she pulls away and steps back")) == []
    assert _impacts(_typed("rejection", "周野抓住手腕，她抽回手，往后退")) == []
    assert _impacts(_typed("rejection", "She steps back; he grabs her wrist")) == ["shot S1: 这一镜是「拒绝」场景，正文里还有「grabs her wrist」"]
    # 拒绝表态（动作正文里写的话）也是拒绝点
    assert _impacts(_typed("rejection", "她说不要，周野抓住手腕")) == [
        "shot S1: 这一镜是「拒绝」场景，正文里看不到拒绝的一方怎么把人挡开（退半步 / 抽手 / 转身 / 走开）",
        "shot S1: 这一镜是「拒绝」场景，正文里还有「抓住手腕」",
    ]
    assert _impacts(_typed("rejection", "周野抓住手腕，她说不要，抽回手")) == []
    assert _impacts(_typed("rejection", "He grabs her arm; she says no and pulls away")) == []
    # 第一次出现在拒绝点之前、之后又出现一次：照报
    assert _impacts(_typed("rejection", "周野抓住手腕，她抽回手，周野又抓住手腕")) == ["shot S1: 这一镜是「拒绝」场景，正文里还有「抓住手腕」"]
    # 没有拒绝点：同镜出现就报（老行为）
    assert _impacts(_typed("rejection", "He pins her against the wall")) == [
        "shot S1: 这一镜是「拒绝」场景，正文里看不到拒绝的一方怎么把人挡开（退半步 / 抽手 / 转身 / 走开）",
        "shot S1: 这一镜是「拒绝」场景，正文里还有「pins her against」",
    ]


def test_rejection_point_ignores_refusal_inside_forced_phrase_and_art():
    # "won't let go" 里的 let go 不是拒绝表态；"no" 在里面的话之后才算
    assert _impacts(_typed("rejection", "He won't let go of her hand")) == [
        "shot S1: 这一镜是「拒绝」场景，正文里看不到拒绝的一方怎么把人挡开（退半步 / 抽手 / 转身 / 走开）",
        "shot S1: 这一镜是「拒绝」场景，正文里还有「won't let go」",
    ]
    assert L._rejection_point("he won't let go", 100) == -1
    assert L._rejection_point("no, he won't let go", 100) == 0
    # 美术里的 "no windows" 不是拒绝点：动作里的强行接触照报
    assert "shot S1: 这一镜是「拒绝」场景，正文里还有「grabs her wrist」" in _impacts(_typed("rejection", "He grabs her wrist", art="dim room, no windows"))
    # 台词排在动作前面：台词里说了 no，动作里的强行接触都算拒绝之后
    assert "shot S1: 这一镜是「拒绝」场景，正文里还有「grabs her wrist」" in _impacts(_typed("rejection", "He grabs her wrist", [{"who": "lin", "en": "No. Don't."}]))
    assert "shot S1: 这一镜是「拒绝」场景，正文里还有「抓住手腕」" in _impacts(_typed("rejection", "周野抓住手腕", [{"who": "lin", "zh": "不要。"}]))


def test_weak_refusal_words_do_not_exempt_earlier_contact():
    """弱词（不能 / 我不 / don't / no…）出现在动作正文里、又不是开口说的：不算拒绝点，前面的强行接触照报（旧版会被压掉）。"""

    forced = "shot S1: 这一镜是「拒绝」场景，正文里还有「{}」"
    for motion, words in (
        ("周野抓住手腕，她挣扎，不能挣脱", "抓住手腕"),
        ("周野堵在门口，她说我不知道", "堵在门口"),
        ("He grabs her wrist; they don't speak.", "grabs her wrist"),
        ("He grabs her wrist; she doesn't move, doesn't say no.", "grabs her wrist"),
        ("He pins her against the wall. No sound.", "pins her against"),
        ("He grabs her wrist under the falling leaves", "grabs her wrist"),
        ("He grabs her arm; she says no way", "grabs her arm"),
    ):
        assert forced.format(words) in _impacts(_typed("rejection", motion)), motion
    # 开口说的弱词才算拒绝点：说话动词 / 开引号 + 弱词 + 句读
    boundary = "shot S1: 这一镜是「拒绝」场景，正文里看不到拒绝的一方怎么把人挡开（退半步 / 抽手 / 转身 / 走开）"
    assert _impacts(_typed("rejection", "周野抓住手腕，她说：「不行。」")) == [boundary]
    assert _impacts(_typed("rejection", "周野抓住手腕。「不要。」")) == [boundary]
    assert _impacts(_typed("rejection", "He grabs her arm; she says no.")) == [boundary]
    assert _impacts(_typed("rejection", "He grabs her arm; she tells him 'no' and steps back")) == []
    assert _impacts(_typed("rejection", "He grabs her arm; she whispers I can't, then steps back")) == []
    # 强拒绝词在任何位置都算
    assert _impacts(_typed("rejection", "周野抓住手腕，她喊放开")) == [boundary]
    assert _impacts(_typed("rejection", "He grabs her wrist. Don't touch me. He backs away")) == []
    # 台词里的弱词照旧算：动作里的强行接触全算拒绝之后
    assert forced.format("抓住手腕") in _impacts(_typed("rejection", "周野抓住手腕", [{"who": "lin", "zh": "我不知道。"}]))
    assert L._rejection_point("he grabs her wrist; she says no.", 100, 0) == 29
    assert L._rejection_point("he grabs her wrist; no sound.", 100, 0) == -1
    assert L._rejection_point("no sound; he grabs her wrist", 100, 8) == 0
    # 名词 leaves 不算挡开动作，both / together 不算回触
    assert _impacts(_typed("rejection", "Dead leaves rustle; he grabs her wrist")) == [boundary, forced.format("grabs her wrist")]
    assert _impacts(_typed("rejection", "She leaves him standing there")) == []
    assert _impacts(_typed("forgiveness", "He hugs her; both stand still, together", [{"who": "lin", "en": "Let's start over."}])) == [
        "shot S1: 这一镜是「原谅」场景，正文里有「hugs」，但只有一方在靠近，受伤的一方没有回应的动作"]
    assert _impacts(_typed("forgiveness", "He hugs her; they hold each other", [{"who": "lin", "en": "Let's start over."}])) == []


def test_scene_parts_order_and_example_unchanged():
    lines, action, art = L._scene_parts({"motion": "he grabs her wrist", "art": "no windows", "exit_state": "door open",
                                         "lines": [{"who": "lin", "zh": "不要。", "en": "No."}], "beats": [{"picture": "P", "performance": "Q"}]})
    # 各段用换行拼：开口拒绝的判定要看到句尾
    assert lines == "不要。\nNo." and action == "he grabs her wrist\ndoor open\nP\nQ" and art == "no windows"
    # 中文示例剧集零变化；没写 scene_type 的英文正文也不判
    spec = json.loads((ROOT / "examples" / "episodes" / "first_episode.json").read_text(encoding="utf-8-sig"))
    assert L.check_relation_boundaries(spec) == []
    untyped = _typed("rejection", "He pins her against the wall")
    del untyped["shots"][0]["scene_type"]
    assert L.check_relation_boundaries(untyped) == []
