"""静态检查（docs/scriptlab-spec.md §4）：按 target 选判据，每条判据一个坏例子 + 一个干净例子；告警能拆成问题卡。

判据一条也不是这里新写的：播客那几条借 ``podcast.lint``、短剧两条借 ``storyvideo.screenplay``，
所以这里验的是「借对了没有、句头换对了没有、该跳过的跳过了没有」。
"""

from __future__ import annotations

import pytest

from script import lint as L
from script import spec as S
from webui.problems import KIND_LABELS, problem_card


def make(tmp_path, lines, *, target="podcast", speakers=None, blocks=None, versions=None, spec_id="SC-L", **extra) -> dict:
    speakers = speakers or [{"id": "a", "name": "阿一"}, {"id": "b", "name": "阿二"}]
    data = {
        "title": "t", "target": target, "speakers": speakers,
        "blocks": blocks or [{"id": "B01", "title": "一"}, {"id": "B02", "title": "二"}],
        "versions": versions or [{"id": "V1", "blocks": [{"id": "B01", "lines": lines}]}],
        "current": "V1",
    }
    data.update(extra)
    return S.normalize(data, project_root=tmp_path, spec_id=spec_id)


# 听友问答段：回答那三条判据只对 kind="qa" 的段成立（对谈段里的短接话不算敷衍的回答）
QA_BLOCKS = [{"id": "B01", "title": "一", "kind": "qa"}, {"id": "B02", "title": "二"}]

CLEAN = [
    {"who": "a", "text": "有一次我半夜两点被拉进一个新群。"},
    {"who": "b", "text": "嗯。"},
    {"who": "a", "text": "当时手机就在枕头边上。"},
    {"who": "b", "text": "我倒觉得那会儿就该回，客户在等。"},
    {"who": "a", "text": "那不一样。"},
    {"who": "b", "text": "怎么不一样，你上次不也回了。"},
]


def test_clean_script_has_no_warnings(tmp_path):
    assert L.lint_script(make(tmp_path, CLEAN)) == []


# ---- podcast：借 podcast.lint 的台词类判据


@pytest.mark.parametrize("lines, needle", [
    ([{"who": "a", "text": "首先我们来看第一点，其次是第二点。"}, *CLEAN], "书面语「首先、其次」"),
    ([{"who": "a", "text": "有一次" + "我说了很多" * 50}, *CLEAN], "没人插话"),
    ([{"who": "ab"[i % 2], "text": f"第{i}句有一次我倒觉得"} for i in range(8)], "每一句都换人"),
    ([{"who": "a", "text": "有一次" + "我讲了很多很多" * 20}, {"who": "b", "text": "我倒觉得不对。"}], "一人独大"),
    ([{"who": "a", "text": "有一次我半夜被拉进群。"}, {"who": "b", "text": "有一次我也是。"}], "反对或分歧"),
    ([{"who": "a", "text": "嗯嗯嗯那个然后嗯嗯那个然后有一次。"}, {"who": "b", "text": "我倒觉得不对。"}], "填充词"),
    ([{"who": "a", "text": "我们讨论边界感这件事。"}, {"who": "b", "text": "我倒觉得不对。"}], "具体例子"),
])
def test_podcast_judgements_each_have_a_bad_example(tmp_path, lines, needle):
    joined = "\n".join(L.lint_script(make(tmp_path, lines)))
    assert needle in joined
    # 干净例子不触发同一条
    assert needle not in "\n".join(L.lint_script(make(tmp_path, CLEAN)))


def test_podcast_answer_judgements(tmp_path):
    """段种类标了听友问答才判这三条：没标回答、回答太短、回答在复述问题。"""

    ask = {"who": "a", "text": "把手机设成黑白真的有用吗？", "kind": "question"}

    def qa(lines):
        return "\n".join(L.lint_script(make(tmp_path, lines, blocks=QA_BLOCKS)))

    assert "标成回答" in qa([ask, {"who": "b", "text": "我倒觉得有一次没用。"}])
    assert "结论加原因" in qa([ask, {"who": "b", "text": "有用。", "kind": "answer"}])
    echo = [ask, {"who": "b", "text": "把手机设成黑白真的有用，我试过一个月，有一次半夜还是刷到两点。", "kind": "answer"}]
    assert "复述问题" in qa(echo)
    good = [ask, {"who": "b", "text": "有用，但撑不过两周；我倒觉得有一次我自己试过就知道了。", "kind": "answer"}]
    joined = qa(good)
    assert "复述问题" not in joined and "结论加原因" not in joined


def test_short_replies_in_a_talk_block_are_not_answers(tmp_path):
    """对谈段里的一问一答不是听友问答：起草提示词专门要求「嗯」「对」这种接话单独成句，模型也会顺手把它标成
    question / answer——要是按台词猜段种类，一段正常的冷开场会被整段报成「回答太短」（真实误报，这条判据认
    段种类的由来）。"""

    lines = [{"who": "a", "text": "有一次我半夜两点被拉进一个新群，你猜我回没回？", "kind": "question"},
             {"who": "b", "text": "没回。", "kind": "answer"},
             {"who": "a", "text": "回了。我倒觉得当时不该回，客户在等也一样。"}]
    joined = "\n".join(L.lint_script(make(tmp_path, lines)))
    assert "结论加原因" not in joined and "标成回答" not in joined and "复述问题" not in joined
    # 同一段台词，段种类改成听友问答就照判——判据没丢，只是认段种类
    assert "结论加原因" in "\n".join(L.lint_script(make(tmp_path, lines, blocks=QA_BLOCKS)))


def test_podcast_skips_structure_and_voice_checks(tmp_path):
    """结构类（缺冷开场 / 收尾、音色）跳过——工作台没有环节种类与音色。"""

    joined = "\n".join(L.lint_script(make(tmp_path, CLEAN[:1])))
    for skipped in ("冷开场", "收尾", "音色", "听友问答环节没有", "这个环节还没有台词"):
        assert skipped not in joined


def test_every_borrowed_warning_is_recognised(tmp_path):
    """借来的每一条都要认得出属于哪条判据——认不出来说明 podcast.lint 改了句子，_PODCAST_MARKS 要跟着改。"""

    dirty = [{"who": "a", "text": "首先我们来看第一点。"}, {"who": "a", "text": "嗯那个然后" * 6},
             {"who": "b", "text": "把手机设成黑白有用吗？", "kind": "question"}, {"who": "b", "text": "有用。", "kind": "answer"}]
    doc = L.as_podcast(make(tmp_path, dirty, blocks=QA_BLOCKS))
    from podcast import lint as PL

    borrowed = PL.check_speech(doc) + PL.check_balance(doc) + PL.check_topics(doc)
    assert borrowed
    unknown = [w for w in borrowed if not L._mark(w)]
    assert not unknown, unknown


def test_a_judgement_word_inside_a_line_does_not_cross_wires(tmp_path):
    """台词里带判据词也不能串台：一句正好写着「书面语」的短回答，它那条「回答太短」不能被认成书面语那一条——
    认错了，generic 目标（只留四条）就会把它当书面语放行。"""

    lines = [{"who": "a", "text": "把手机设成黑白真的有用吗？", "kind": "question"},
             {"who": "b", "text": "书面语。", "kind": "answer"}]
    short = [w for w in L.lint_script(make(tmp_path, lines, blocks=QA_BLOCKS)) if "结论加原因" in w]
    assert short and L._mark(short[0]) == "answer_short"
    assert "结论加原因" not in "\n".join(L.lint_script(make(tmp_path, lines, blocks=QA_BLOCKS, target="generic")))


# ---- 短剧：借剧本层的两条 + 一镜的对白密度


def test_drama_line_length_and_speaking_cast(tmp_path):
    long_line = [{"who": "a", "text": "把完整的排班记录现在就拿出来放在这张桌子上给所有人看清楚。"}]
    joined = "\n".join(L.lint_script(make(tmp_path, long_line, target="drama_screenplay")))
    assert "block B01: 「阿一」的一句台词 29 字——一口气说不完，拆镜时要占满一整镜" in joined
    assert L.lint_script(make(tmp_path, [{"who": "a", "text": "把排班记录拿出来。"}], target="drama_screenplay")) == []
    crowd = [{"who": chr(ord("a") + i), "text": "我说一句。"} for i in range(4)]
    spec = make(tmp_path, crowd, target="drama_screenplay",
                speakers=[{"id": chr(ord("a") + i), "name": f"角色{i}"} for i in range(4)])
    assert "全篇：剧本里有 4 个人物开口说话" in "\n".join(L.lint_script(spec))


def test_drama_line_length_is_not_judged_in_english(tmp_path):
    """单句字数那条数的是汉字（>25 字），英文没有对应的词数阈值——借来的判据没有英文口径就不判，
    照搬会把几乎每句英文台词都报成太长。开口人数那条数的是人，英文照判。"""

    long_en = [{"who": "a", "text": "Put the whole schedule on this table right now so everyone can see it."}]
    spec = make(tmp_path, long_en, target="drama_screenplay", language="en")
    assert len(long_en[0]["text"]) > 25 and L.lint_script(spec) == []
    crowd = [{"who": chr(ord("a") + i), "text": "I speak."} for i in range(4)]
    many = make(tmp_path, crowd, target="drama_screenplay", language="en",
                speakers=[{"id": chr(ord("a") + i), "name": f"Cast{i}"} for i in range(4)])
    assert "全篇：剧本里有 4 个人物开口说话" in "\n".join(L.lint_script(many))


def test_drama_shot_density(tmp_path):
    """一段就是一镜：台词的纯语音秒数超过一镜的可用发声窗口 = 装不下。"""

    packed = [{"who": "a", "text": "把排班记录拿出来。"}, {"who": "b", "text": "我没有。"},
              {"who": "a", "text": "那就等着。"}, {"who": "b", "text": "等到什么时候。"},
              {"who": "a", "text": "等到你想起来。"}, {"who": "b", "text": "我真的没有。"},
              {"who": "a", "text": "那就再等等。"}]
    joined = "\n".join(L.lint_script(make(tmp_path, packed, target="drama_shot")))
    assert "这一镜装不下这些台词" in joined and "≈" in joined and "8.7 秒" in joined
    # 同一份稿子按「场」审就不判这条（场不是一镜）
    assert "装不下" not in "\n".join(L.lint_script(make(tmp_path, packed, target="drama_screenplay")))
    assert L.check_shot_density(make(tmp_path, [{"who": "a", "text": "把排班记录拿出来。"}], target="drama_shot")) == []


def test_drama_shot_density_borrows_the_preview_budget(tmp_path):
    """容量模型整条借 ``storyvideo.preview``：同一段台词，工作台算出来的窗口 / 秒数跟拆镜之后
    ``shot_duration_budget`` 对同一镜算的**一模一样**（以前是常量相减 + 含停顿的估算，两边对不上）。
    慢端那半条「偏紧」的信号也跟着借来了。"""

    from storyvideo.preview import shot_duration_budget
    from storyvideo.lint import SHOT_SECONDS_CEILING

    packed = [{"who": "a", "text": "把排班记录拿出来。"}, {"who": "b", "text": "我没有。"},
              {"who": "a", "text": "那就等着。"}, {"who": "b", "text": "等到什么时候。"},
              {"who": "a", "text": "等到你想起来。"}, {"who": "b", "text": "我真的没有。"},
              {"who": "a", "text": "那就再等等。"}]
    spec = make(tmp_path, packed, target="drama_shot")
    budget = shot_duration_budget({"id": "S1", "dur": SHOT_SECONDS_CEILING,
                                   "lines": [{"zh": l["text"]} for l in packed]})
    warning = L.check_shot_density(spec)[0]
    assert f"台词 {budget['dialogue_chars']} 字 ≈{budget['speech_seconds']:g} 秒纯语音" in warning
    assert f"可用发声窗口 {budget['window_seconds']:g} 秒" in warning
    # 慢端「偏紧」：快端装得下、慢端装不下（35 字：快 7.78 秒、慢 10 秒，窗口 8.7 秒）
    tight = make(tmp_path, [{"who": "a", "text": "一" * 35}], target="drama_shot")
    assert L.block_budget("B01", [{"text": "一" * 35}])["tight"] is True
    assert "偏紧，语气一慢就赶词" in L.check_shot_density(tight)[0]


def test_drama_shot_density_is_not_judged_in_english(tmp_path):
    """preview 的容量模型数的是字符、按中文语速定标；英文一句话的字符数是词数的五六倍，照搬等于
    每段都报装不下。借来的判据没有英文口径就不判——跟单句字数那条同一个理由。"""

    long_en = [{"who": "a", "text": " ".join(["word"] * 30)}]
    assert L.check_shot_density(make(tmp_path, long_en, target="drama_shot", language="en")) == []


def test_drama_skips_podcast_judgements(tmp_path):
    joined = "\n".join(L.lint_script(make(tmp_path, [{"who": "a", "text": "首先我们来看第一点。"}], target="drama_shot")))
    assert "书面语" not in joined and "分歧" not in joined


# ---- generic：两边的交集


def test_generic_keeps_only_four_judgements(tmp_path):
    dirty = [{"who": "a", "text": "首先我们来看第一点。"}] + [
        {"who": "ab"[i % 2], "text": f"第{'一二三四五六七八'[i]}句我们讨论一下。"} for i in range(8)]
    joined = "\n".join(L.lint_script(make(tmp_path, dirty, target="generic")))
    assert "书面语" in joined and "具体例子" in joined
    assert "机械轮流" not in joined and "分歧" not in joined and "填充词" not in joined and "播音腔" not in joined


def test_generic_still_catches_monologue_and_dominance(tmp_path):
    lines = [{"who": "a", "text": "有一次" + "我讲了很多很多" * 40}, {"who": "b", "text": "嗯。"}]
    joined = "\n".join(L.lint_script(make(tmp_path, lines, target="generic")))
    assert "没人插话" in joined and "一人独大" in joined


# ---- 版本与问题卡


def test_lint_reads_the_asked_version(tmp_path):
    versions = [{"id": "V1", "blocks": [{"id": "B01", "lines": [{"who": "a", "text": "首先我们来看第一点。"}]}]},
                {"id": "V2", "blocks": [{"id": "B01", "lines": CLEAN}]}]
    spec = make(tmp_path, [], versions=versions, current="V2")
    assert L.lint_script(spec) == []                      # 默认审 current
    assert "书面语" in "\n".join(L.lint_script(spec, "V1"))
    assert L.lint_script(make(tmp_path, [], versions=[], current="")) == []


def test_lint_checks_the_latest_version_when_nothing_is_picked(tmp_path):
    """有版本但 current 是空的（第一版会自动采用，但整体保存时 current 可能被清空，契约 §3）：检查要照样跑，不能显示成「零告警」。"""

    spec = make(tmp_path, [{"who": "a", "text": "首先我们来看第一点，其次是第二点。"}], current="")
    assert "书面语" in "\n".join(L.lint_script(spec))
    assert L.lint_script(spec) == L.lint_script(spec, "V1")
    # 有两版、没采用 = 看最新那版
    versions = [{"id": "V1", "blocks": [{"id": "B01", "lines": [{"who": "a", "text": "首先我们来看第一点。"}]}]},
                {"id": "V2", "blocks": [{"id": "B01", "lines": CLEAN}]}]
    assert L.lint_script(make(tmp_path, [], versions=versions, current="")) == []


def test_block_estimates_report_seconds_without_judging(tmp_path):
    """逐段的字数 / 秒数：不带阈值、不出告警（「一场该多长」仓库里没有判据，工作台不新造）——
    drama_screenplay 的「对白密度」就落在这里，数摆出来给人看。

    同一行里两个「秒」各带各的名字：``seconds`` 含句间停顿（整篇估算那一套），``speech_seconds`` 是
    preview 的纯语音（契约 §4 点名的那一个，也是「一镜装不下」那条告警里的秒数）——两个数不该互相对账。"""

    packed = [{"who": "a", "text": "把排班记录拿出来。"}, {"who": "b", "text": "我没有。"}]
    spec = make(tmp_path, packed, target="drama_screenplay")
    rows = L.block_estimates(spec)
    assert [r["id"] for r in rows] == ["B01", "B02"] and rows[0]["title"] == "一"
    assert rows[0]["chars"] == 13 and rows[0]["lines"] == 2 and rows[0]["seconds"] > 0
    # 纯语音 = 13 字 ÷ preview 的 4.5 字/秒；含停顿的那个更大，所以两列要分开标注
    assert rows[0]["speech_seconds"] == L.block_budget("B01", packed)["speech_seconds"] == 2.89
    assert rows[0]["seconds"] > rows[0]["speech_seconds"]
    assert rows[1] == {"id": "B02", "title": "二", "chars": 0, "lines": 0, "seconds": 0.0, "minutes": 0.0,
                       "speech_seconds": 0.0}
    assert L.lint_script(spec) == []                      # 这份数不会变成告警
    assert L.block_estimates(make(tmp_path, [], versions=[], current="")) == [
        {"id": "B01", "title": "一", "chars": 0, "lines": 0, "seconds": 0.0, "minutes": 0.0, "speech_seconds": 0.0},
        {"id": "B02", "title": "二", "chars": 0, "lines": 0, "seconds": 0.0, "minutes": 0.0, "speech_seconds": 0.0}]
    # 英文稿没有这个口径（容量模型数的是字符、按中文语速定标），给 None——界面上别显示，别拿它当秒数
    english = make(tmp_path, [{"who": "a", "text": "Bring the shift log."}], target="drama_screenplay", language="en")
    assert L.block_estimates(english)[0]["speech_seconds"] is None
    assert L.block_estimates(english)[0]["seconds"] > 0


def test_warnings_split_into_problem_cards(tmp_path):
    warnings = L.lint_script(make(tmp_path, [{"who": "a", "text": "首先我们来看第一点。"}, {"who": "b", "text": "我倒觉得不对。"}]))
    card = problem_card(warnings[0])
    assert card["kind"] == "podcast" and card["fix"] and KIND_LABELS["podcast"] == "播客"
    # 句头要被 problems.py 认出来剥掉：认不出来就会当整篇级，「block B01: 」还留在影响里，英文界面下翻不出来
    assert card["scope"] == "block" and card["target"] == "B01" and not card["impact"].startswith("block")
    whole = problem_card("全篇：整期没有一处反对或分歧——真人对谈总有人不同意；加一处明确的不同意见，而且不要马上和解")
    assert whole["scope"] == "episode" and whole["target"] == "" and whole["impact"].startswith("整期")
    drama = L.lint_script(make(tmp_path, [{"who": "a", "text": "把完整的排班记录现在就拿出来放在这张桌子上给所有人看。"}],
                               target="drama_shot"))
    assert problem_card(drama[0])["kind"] in KIND_LABELS
    assert all("——" in w for w in warnings + drama)        # 每条都给改法
