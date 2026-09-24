"""真人感静态检查（docs/podcast-spec.md §3）：示例稿子干净；每条判据能被一个坏例子触发；告警能拆成问题卡「播客」。"""

from __future__ import annotations

import json
from pathlib import Path

from podcast import lint as L
from podcast import spec as S
from webui.problems import KIND_LABELS, problem_card

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "podcast" / "short_video_demo.json"


def example_spec(tmp_path, **extra) -> dict:
    data = json.loads(EXAMPLE.read_text(encoding="utf-8-sig"))
    data.update(extra)
    return S.normalize(data, project_root=tmp_path, spec_id="PC-L")


def duo(tmp_path, lines, *, kind="topic", **extra) -> dict:
    data = {
        "title": "t", "format": "duo", "target_minutes": 1,
        "hosts": [{"id": "a", "name": "阿一", "voice": "v1"}, {"id": "b", "name": "阿二", "voice": "v2"}],
        "segments": [{"id": "G01", "kind": "cold_open", "lines": [{"who": "a", "text": "开头。"}]},
                     {"id": "G02", "kind": "intro", "lines": [{"who": "b", "text": "我是阿二。"}]},
                     {"id": "G03", "kind": kind, "lines": lines},
                     {"id": "G04", "kind": "outro", "lines": [{"who": "a", "text": "下期见。"}]}],
    }
    data.update(extra)
    return S.normalize(data, project_root=tmp_path, spec_id="PC-D")


def test_example_only_warns_about_unbound_voices(tmp_path):
    warnings = L.lint_podcast(example_spec(tmp_path))
    assert len(warnings) == 1 and "还没绑音色" in warnings[0]
    card = problem_card(warnings[0])
    assert card["kind"] == "podcast" and card["scope"] == "episode" and card["fix"]
    assert KIND_LABELS["podcast"] == "播客"


def test_structure_checks(tmp_path):
    spec = example_spec(tmp_path)
    by_id = {s["id"]: s for s in spec["segments"]}
    spec["segments"] = [by_id["G03"], by_id["G02"], by_id["G04"], by_id["G05"]]   # 去掉冷开场 / 收尾，话题排到最前
    by_id["G05"]["questions"] = []
    by_id["G02"]["lines"] = []
    joined = "\n".join(L.check_structure(spec))
    assert "全期：没有冷开场环节" in joined and "全期：没有收尾环节" in joined and "全期：没有开场环节" not in joined
    assert "segment G03: 第一个环节是「话题」" in joined
    assert "segment G05: 听友问答环节没有一条听众问题" in joined
    assert "segment G02: 这个环节还没有台词" in joined
    assert L.check_structure(example_spec(tmp_path, segments=[])) == []


def test_speech_checks_bookish_monologue_alternation_and_cue(tmp_path):
    spec = duo(tmp_path, [{"who": "a", "text": "首先我们来看第一点，其次是第二点。", "cue": "跳舞"}])
    joined = "\n".join(L.check_speech(spec))
    assert "书面语「首先、其次」" in joined and "cue「跳舞」不在清单里" in joined
    long_line = {"who": "a", "text": "有一次" + "我说" * 120}
    assert any("没人插话" in w for w in L.check_speech(duo(tmp_path, [long_line])))
    # 单人独白不判独白太长
    solo = duo(tmp_path, [long_line], format="solo", hosts=[{"id": "a", "name": "阿一"}],
               segments=[{"id": "G01", "kind": "topic", "lines": [long_line]}])
    assert not any("没人插话" in w for w in L.check_speech(solo))
    alternating = [{"who": "ab"[i % 2], "text": f"第{i}句有一次"} for i in range(8)]
    assert any("机械轮流" in w for w in L.check_speech(duo(tmp_path, alternating)))
    natural = alternating[:]
    natural[3] = {"who": "a", "text": "接着说有一次"}
    assert not any("机械轮流" in w for w in L.check_speech(duo(tmp_path, natural)))


def test_balance_checks_dominance_disagreement_and_fillers(tmp_path):
    lines = [{"who": "a", "text": "有一次" + "我讲了很多很多" * 20}, {"who": "b", "text": "嗯。"}]
    joined = "\n".join(L.check_balance(duo(tmp_path, lines)))
    assert "「阿一」说了" in joined and "一人独大" in joined
    assert "整期没有一处反对或分歧" in joined
    agree = [{"who": "a", "text": "有一次我觉得对。"}, {"who": "b", "text": "我倒觉得不对。"}]
    assert not any("分歧" in w for w in L.check_balance(duo(tmp_path, agree)))
    fillers = [{"who": "a", "text": "嗯那个然后嗯那个然后有一次"}, {"who": "b", "text": "嗯嗯嗯不对"}]
    assert any("填充词" in w and "像结巴" in w for w in L.check_balance(duo(tmp_path, fillers)))
    many = [{"who": "ab"[i % 2], "text": f"第{i}句不对" if i == 1 else f"第{i}句有一次"} for i in range(24)]
    many[5] = {"who": "b", "text": "连说"}
    assert any("播音腔" in w for w in L.check_balance(duo(tmp_path, many)))
    assert L.check_balance(duo(tmp_path, [], format="solo", hosts=[{"id": "a", "name": "阿一"}],
                               segments=[{"id": "G01", "kind": "topic", "lines": [{"who": "a", "text": "首先"}]}])) == []


def test_topic_qa_and_outro_checks(tmp_path):
    vague = [{"who": "a", "text": "我觉得这事很复杂。"}, {"who": "b", "text": "对，不对，很复杂。"}]
    assert any("没有一个具体例子" in w for w in L.check_topics(duo(tmp_path, vague)))
    concrete = [{"who": "a", "text": "上周我刷了 3 小时。"}, {"who": "b", "text": "不对吧。"}]
    assert not any("具体例子" in w for w in L.check_topics(duo(tmp_path, concrete)))
    qa = duo(tmp_path, [
        {"who": "a", "text": "阿May 问：黑白模式有用吗？", "kind": "question"},
        {"who": "b", "text": "黑白模式有用吗，我觉得这个问题要看情况，因为每个人不一样。", "kind": "answer"},
        {"who": "b", "text": "有用。", "kind": "answer"},
    ], kind="qa")
    qa["segments"][2]["questions"] = [{"from": "阿May", "text": "黑白模式有用吗？"}]
    joined = "\n".join(L.check_topics(qa))
    assert "在复述问题" in joined and "只有 3 字" in joined
    no_answer = duo(tmp_path, [{"who": "a", "text": "阿May 问：黑白模式有用吗？"}], kind="qa")
    no_answer["segments"][2]["questions"] = [{"from": "阿May", "text": "黑白模式有用吗？"}]
    assert any("没有一句标成回答" in w for w in L.check_topics(no_answer))
    outro = duo(tmp_path, [{"who": "a", "text": "有一次 1"}])
    outro["segments"][3]["lines"] = [{"who": "a", "text": "总结一下今天我们聊了三点。", "cue": "", "kind": "talk"}]
    assert any("收尾在总结全篇" in w for w in L.check_topics(outro))


def test_length_and_voice_checks(tmp_path):
    spec = example_spec(tmp_path, target_minutes=12)
    assert any("偏短" in w and "目标 12 分钟" in w for w in L.check_length(spec))
    assert L.check_length(example_spec(tmp_path)) == []
    same = example_spec(tmp_path)
    same["hosts"][0]["voice"] = "v1"
    same["hosts"][1]["voice"] = "v1"
    assert any("同一个音色" in w for w in L.check_voices(same))
    one = example_spec(tmp_path)
    one["hosts"][0]["voice"] = "v1"
    assert any("「小夏」还没绑音色" in w for w in L.check_voices(one))
    solo = example_spec(tmp_path)
    solo["hosts"] = solo["hosts"][:1]
    assert L.check_voices(solo) == []


def test_segment_warning_becomes_a_card_with_target(tmp_path):
    spec = duo(tmp_path, [{"who": "a", "text": "首先。"}])
    warning = next(w for w in L.lint_podcast(spec) if w.startswith("segment G03:"))
    card = problem_card(warning)
    assert card["scope"] == "segment" and card["target"] == "G03" and card["kind"] == "podcast"
    assert card["impact"].startswith("「阿一」的台词里有书面语") and card["fix"].startswith("真人聊天不这么说")


# ---------------------------------------------------------------------------- 英文稿子：按 spec.language 选英文词表，独白与填充词按词数


def duo_en(tmp_path, lines, *, kind="topic", outro=None, **extra) -> dict:
    data = {
        "title": "t", "format": "duo", "language": "en", "target_minutes": 1,
        "hosts": [{"id": "a", "name": "Sam", "voice": "v1"}, {"id": "b", "name": "Lee", "voice": "v2"}],
        "segments": [{"id": "G01", "kind": "cold_open", "lines": [{"who": "a", "text": "So there I was."}]},
                     {"id": "G02", "kind": "intro", "lines": [{"who": "b", "text": "I am Lee."}]},
                     {"id": "G03", "kind": kind, "lines": lines},
                     {"id": "G04", "kind": "outro", "lines": outro or [{"who": "a", "text": "See you next week."}]}],
    }
    data.update(extra)
    return S.normalize(data, project_root=tmp_path, spec_id="PC-E")


def test_en_bookish_and_monologue_by_words(tmp_path):
    msgs = L.check_speech(duo_en(tmp_path, [{"who": "a", "text": "Firstly, let us look at this. In conclusion it works, unlike before."}]))
    assert msgs == ["segment G03: 「Sam」的台词里有书面语「firstly、in conclusion、let us」——真人聊天不这么说；删掉连接词，直接说事"]
    card = problem_card(msgs[0])
    assert card["scope"] == "segment" and card["target"] == "G03" and card["kind"] == "podcast"
    # 中文书面语表在英文稿里不用；英文表在中文稿里不用
    assert L.check_speech(duo_en(tmp_path, [{"who": "a", "text": "首先 I remember."}])) == []
    assert L.check_speech(duo(tmp_path, [{"who": "a", "text": "有一次 firstly。"}])) == []
    # 独白阈值按词数：142 词报、400 个字母但只 41 词不报
    long_line = {"who": "a", "text": "I remember " + "we said " * 70}
    msgs = L.check_speech(duo_en(tmp_path, [long_line]))
    assert msgs == ["segment G03: 「Sam」一句 142 词——约 55 秒没人插话，真人对谈里别人早接话了；拆开，让另一个人接一句或追问"]
    assert problem_card(msgs[0])["impact"] == "「Sam」一句 142 词" and problem_card(msgs[0])["kind"] == "podcast"
    assert L.check_speech(duo_en(tmp_path, [{"who": "a", "text": "I remember " + "abcdefghij " * 40}])) == []


def test_en_disagreement_specific_fillers_and_outro(tmp_path):
    agree = [{"who": "a", "text": "I remember one time it worked."}, {"who": "b", "text": "Yes it did."}]
    assert any("整期没有一处反对或分歧" in w for w in L.check_balance(duo_en(tmp_path, agree)))
    disagree = [{"who": "a", "text": "I remember one time it worked."}, {"who": "b", "text": "Hold on, I don't think so."}]
    assert not any("分歧" in w for w in L.check_balance(duo_en(tmp_path, disagree)))
    # 具体例子：英文信号词；没有就报（改法句沿用中文）
    vague = [{"who": "a", "text": "It is complicated."}, {"who": "b", "text": "Actually, very."}]
    assert any("整段没有一个具体例子" in w for w in L.check_topics(duo_en(tmp_path, vague)))
    concrete = [{"who": "a", "text": "The other day I tried it."}, {"who": "b", "text": "Actually, very."}]
    assert not any("具体例子" in w for w in L.check_topics(duo_en(tmp_path, concrete)))
    # 填充词按词数、例子换成英文；"unlike" / "upright" 不算 like / right
    fillers = [{"who": "a", "text": "Um, like, you know, I mean, uh, right."}, {"who": "b", "text": "Actually no, wait."}]
    msgs = [w for w in L.check_balance(duo_en(tmp_path, fillers)) if "填充词" in w]
    assert msgs == ["全期：填充词（um / uh / like / you know…）占了 36%——TTS 念出来像结巴；留在接话处一两个，其余删掉"]   # 8 个填充词 / 22 词
    clean = [{"who": "a", "text": "Unlike before it was bright and upright, actually."}, {"who": "b", "text": "Wait, no."}]
    assert not any("填充词" in w for w in L.check_balance(duo_en(tmp_path, clean)))
    # 收尾总结：被更长短语包含的不重复报
    outro = duo_en(tmp_path, concrete, outro=[{"who": "a", "text": "To sum up, today we talked about three things."}])
    assert [w for w in L.check_topics(outro) if "收尾" in w] == [
        "segment G04: 收尾在总结全篇（「to sum up、today we talked about、three things」）——真人播客结尾只留一句和互动号召；删掉回顾，留下一期预告"]
    # 中文稿的填充词占比口径不变（分母还是拼起来的全文字数）
    zh = [{"who": "a", "text": "嗯那个然后嗯那个然后有一次"}, {"who": "b", "text": "嗯嗯嗯不对"}]
    assert any("填充词（嗯 / 啊 / 那个 / 然后…）占了 38%" in w for w in L.check_balance(duo(tmp_path, zh)))   # 13 字 / 34 字（含换行）


def test_en_dominance_and_no_filler_wording(tmp_path):
    # 一人独大按词数、句子写「词」；中文稿仍写「字」
    lines = [{"who": "a", "text": "I remember " + "we said a lot " * 20}, {"who": "b", "text": "Wait, no."}]
    msgs = [w for w in L.check_balance(duo_en(tmp_path, lines)) if "一人独大" in w]
    assert msgs == ["全期：「Sam」说了 95% 的词——一人独大另一个人就成了捧哏；把一部分例子和观点分给别人，或让别人反驳"]   # 90 词 / 95 词
    assert problem_card(msgs[0])["kind"] == "podcast"
    zh = [{"who": "a", "text": "有一次" + "我讲了很多很多" * 20}, {"who": "b", "text": "嗯。"}]
    assert any("说了 " in w and "% 的字" in w for w in L.check_balance(duo(tmp_path, zh)))
    # 一个填充词都没有：英文稿的例子换成 um / right
    many = [{"who": "ab"[i % 2], "text": f"Line {i} no." if i == 1 else f"Line {i} once."} for i in range(24)]
    many[5] = {"who": "b", "text": "Twice."}
    msgs = [w for w in L.check_balance(duo_en(tmp_path, many)) if "播音腔" in w]
    assert msgs == ["全期：一个填充词都没有——略显播音腔；在接话和被打断的地方加一两个「um」「right」"]
    assert problem_card(msgs[0])["kind"] == "podcast"
    # 直单引号引起来的填充词也算（词边界只禁词内撇号）
    quoted = [{"who": "a", "text": "He said 'right' and 'like' and 'um' twice, you know."}, {"who": "b", "text": "Wait, no."}]
    assert any("填充词（um / uh / like / you know…）" in w for w in L.check_balance(duo_en(tmp_path, quoted)))
    assert L._en_word_re("right").search("he said 'right' twice") is not None
    assert L._en_word_re("right").search("upright, right's") is None


def test_same_underlying_voice_behind_two_library_profiles(tmp_path):
    """两个名字不同的音色库档案底下是同一个 edge 音色：给了解析后的档案就能查出来；只比档案 id 查不出来。"""

    spec = example_spec(tmp_path)
    spec["hosts"][0]["voice"] = "voice1"
    spec["hosts"][1]["voice"] = "voice2"
    assert not any("同一个音色" in w for w in L.check_voices(spec))
    profiles = {"zhou": {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "placeholder": False},
                "xiao": {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "placeholder": False}}
    assert any("「老周」和「小夏」绑的是同一个音色" in w for w in L.check_voices(spec, profiles))
    profiles["xiao"]["voice"] = "zh-CN-YunxiNeural"
    assert not any("同一个音色" in w for w in L.lint_podcast(spec, profiles))


def test_broken_voice_references_are_warned_and_not_compared_as_same_voice(tmp_path):
    """PC-06（2026-09-22 审计）：档案 id 不存在 / 档案只有描述——按 host_profiles 的标记出告警；两个人都绑坏档案不会误报「同一个音色」。"""

    spec = example_spec(tmp_path)
    spec["hosts"][0]["voice"] = "gone"
    spec["hosts"][1]["voice"] = "desc"
    profiles = {"zhou": {"engine": "edge", "voice": "zh-CN-YunxiNeural", "placeholder": True, "missing": True, "profile_id": "gone"},
                "xiao": {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "placeholder": True, "no_tts": True, "profile_id": "desc"}}
    warnings = L.check_voices(spec, profiles)
    assert warnings == [
        "全期：主播「老周」绑的音色档案「gone」已不存在——去音色库重新挑一个，或先留空用占位音色",
        "全期：主播「小夏」绑的档案「desc」只有文字描述，不能出声——换一个带 TTS 音色的档案",
    ]
    for w in warnings:
        card = problem_card(w)
        assert card["kind"] == "podcast" and card["scope"] == "episode" and card["fix"]
    # 两人绑同一个坏 id：走占位轮换，不算同声
    spec["hosts"][1]["voice"] = "gone"
    profiles["xiao"] = {"engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "placeholder": True, "missing": True, "profile_id": "gone"}
    assert not any("同一个音色" in w for w in L.check_voices(spec, profiles))
    # 单人独白也要说档案坏了
    solo = example_spec(tmp_path, format="solo", hosts=[{"id": "zhou", "name": "老周", "voice": "gone"}], segments=[])
    assert any("已不存在" in w for w in L.check_voices(solo, {"zhou": profiles["zhou"]}))
    # 没给 profiles（音色库读不到）：退回老行为，不报这两条
    assert not any("已不存在" in w or "只有文字描述" in w for w in L.check_voices(spec))
