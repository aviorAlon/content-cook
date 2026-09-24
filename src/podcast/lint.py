"""真人感静态检查（契约 docs/podcast-spec.md §3）：不花钱，把「像不像真人在聊」拆成能数的判据。

告警句形状跟别的模块一样：``segment G02: 影响——改法`` / ``全期：影响——改法``，接口层（``webui/problems.py``）拆成
问题卡（分类「播客」）。每条都给改法，不只报「有问题」。阈值是可调默认值，不是行业标准（§7）。

词表按 ``spec.language`` 选：zh 用中文表、按字数；en 用 ``*_EN`` 表（小写、词边界匹配）、独白与填充词占比按词数。
两套词表都是自拟经验词，置信度低。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from . import spec as S
from .craft import EN_WORDS_PER_SECOND, estimate

# 书面语 / 口播腔：这些词在真人对谈里几乎不出现，AI 稿里到处都是
BOOKISH = ("首先", "其次", "最后一点", "综上所述", "总而言之", "值得注意的是", "不难发现", "总的来说", "一方面", "另一方面",
           "让我们", "换句话说", "由此可见", "毋庸置疑", "众所周知")
# 反对 / 分歧的信号（双人以上整期至少要出现一次）
DISAGREE = ("不同意", "不对", "我倒觉得", "不见得", "未必", "可是", "等等", "我不这么看", "反过来", "不是这样", "我不觉得",
            "那不一样", "你这个", "我反对", "有问题", "错了", "不太对", "但是我")
# 具体例子的信号：数字之外的
SPECIFIC = ("有一次", "上周", "上个月", "去年", "前年", "那天", "我记得", "比如", "举个例子", "当时", "那年", "那次", "有个朋友", "我自己")
FILLERS = ("嗯", "啊", "对对对", "就是说", "那个", "然后", "呃", "这个这个", "对吧")
# 结尾总结全篇
OUTRO_SUMMARY = ("今天我们聊了", "总结一下", "三点", "回顾一下", "今天的内容", "我们今天讲了", "以上就是")
# ---- 同一组判据的英文词表：``spec.language == "en"`` 的稿子用这些（小写短语，按词边界匹配，"like" 不命中 "unlike"）。
# 自拟经验词，**置信度低**："like" / "right" / "actually" / "once" 这种词在正常句子里也常见，命中只是提示。
BOOKISH_EN = ("firstly", "secondly", "thirdly", "lastly", "in conclusion", "to sum up", "to summarize", "to summarise", "in summary",
              "it is worth noting", "it's worth noting", "it should be noted", "on the one hand", "on the other hand", "let us",
              "in other words", "furthermore", "moreover", "thus", "hence", "as we can see", "it is evident", "needless to say",
              "as is well known", "in addition")
DISAGREE_EN = ("i disagree", "disagree", "not really", "i don't think", "i don't think so", "that's not", "that is not", "actually",
               "but i", "wait", "hold on", "hang on", "no way", "not sure about that", "i doubt", "that's wrong", "you're wrong",
               "i see it differently", "push back", "the other way around", "it's not that", "not the same", "not exactly",
               "i wouldn't say", "come on")
SPECIFIC_EN = ("last week", "last month", "last year", "the year before", "one time", "once", "i remember", "for example", "for instance",
               "the other day", "when i", "back when", "there was this", "a friend of mine", "my friend", "myself", "that day",
               "that time", "that night", "yesterday", "this morning", "years ago", "at the time", "e.g.")
FILLERS_EN = ("um", "umm", "uh", "uhh", "er", "erm", "hmm", "like", "you know", "i mean", "kind of", "sort of", "right", "yeah")
OUTRO_SUMMARY_EN = ("to sum up", "in summary", "to summarize", "to summarise", "today we talked about", "today we discussed",
                    "we talked about", "we covered", "let's recap", "let us recap", "recap", "three points", "three things",
                    "key takeaways", "takeaways", "to wrap up", "wrapping up")
MONOLOGUE_CHARS = 220           # 双人以上：一句超过这个字数 ≈ 50 秒没人插话
MONOLOGUE_WORDS = 120           # 英文稿：一句超过这个词数 ≈ 45 秒没人插话（词数 ÷ craft.EN_WORDS_PER_SECOND）
DOMINANCE_RATIO = 0.70          # duo / roundtable：一个人的字数占比上限（英文按词数）
FILLER_RATIO = 0.08             # 填充词字数占比上限（英文按词数）
ALTERNATION_MIN_LINES = 8       # 机械轮流：环节至少这么多句才判
NO_FILLER_MIN_LINES = 20        # 双人以上整期这么多句还一个填充词没有 → 播音腔（软提示）
ANSWER_MIN_CHARS = 20
ANSWER_ECHO_CHARS = 12
MINUTES_TOLERANCE = 0.25
_DIGIT = re.compile(r"\d")
_PUNCT = re.compile(r"[\s，。？！、,.?!:：;；「」『』“”\"'（）()]")
ECHO_PREFIX_CHARS = 6           # 回答以问题的前几个字开头 = 复述问题


def _strip(text: str) -> str:
    return _PUNCT.sub("", str(text or ""))


def _is_en(spec: Mapping[str, Any]) -> bool:
    """按 ``spec.language`` 选词表与阈值：zh 用中文表按字数，en 用英文表按词数（估算 estimate 已经这样分）。"""

    return str(spec.get("language") or "zh") == "en"


_EN_WORD_RES: dict[str, re.Pattern[str]] = {}


def _en_word_re(word: str) -> re.Pattern[str]:
    """英文短语的词边界正则：前后不能贴着字母 / 数字，也不能贴着词内的撇号（"don" 不命中 "don't"、"like" 不命中 "unlike"），
    但直单引号引起来的词能命中（"says 'right'"）；短语内的空格可以是任意空白或连字符。"""

    pattern = _EN_WORD_RES.get(word)
    if pattern is None:
        body = r"[\s\-]+".join(re.escape(part) for part in word.split())
        pattern = re.compile(rf"(?<![a-z0-9])(?<![a-z]'){body}(?![a-z0-9])(?!'[a-z0-9])")
        _EN_WORD_RES[word] = pattern
    return pattern


def _hits(words_zh: tuple[str, ...], words_en: tuple[str, ...], text: str, en: bool) -> list[str]:
    """命中的词，按词表顺序：中文按子串（老行为不动）；英文小写后按词边界，被更长的命中短语包含的不重复报
    （"we talked about" 被 "today we talked about" 包含时只留长的）。"""

    if not en:
        return [w for w in words_zh if w in text]
    low = text.lower().replace("’", "'")
    found = [w for w in words_en if _en_word_re(w).search(low)]
    return [w for w in found if not any(o != w and w in o for o in found)]


def _size(text: str, en: bool) -> int:
    """一句的长度：中文按字，英文按词（和 craft.line_seconds 的口径一致）。"""

    return len(text.split()) if en else len(text)


def _filler_size(joined: str, en: bool) -> int:
    """填充词占的字数（中文）或词数（英文）。"""

    if not en:
        return sum(joined.count(w) * len(w) for w in FILLERS)
    low = joined.lower().replace("’", "'")
    return sum(len(_en_word_re(w).findall(low)) * len(w.split()) for w in FILLERS_EN)


def _echoes(answer: str, question: str) -> bool:
    """回答去掉标点后，以问题（去标点）的前 6 个字开头。"""

    head = _strip(question)[:ECHO_PREFIX_CHARS]
    return len(head) >= 4 and _strip(answer).startswith(head)


def _segments(spec: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [s for s in spec.get("segments") or () if isinstance(s, Mapping)]


def _lines(segment: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [l for l in segment.get("lines") or () if isinstance(l, Mapping) and str(l.get("text") or "").strip()]


def _sid(segment: Mapping[str, Any], index: int) -> str:
    return str(segment.get("id") or S.default_segment_id(index))


def _multi(spec: Mapping[str, Any]) -> bool:
    return len(spec.get("hosts") or ()) > 1 and str(spec.get("format") or "duo") != "solo"


def _names(spec: Mapping[str, Any]) -> dict[str, str]:
    return {str(h["id"]): str(h.get("name") or h["id"]) for h in spec.get("hosts") or () if isinstance(h, Mapping) and h.get("id")}


def check_structure(spec: Mapping[str, Any]) -> list[str]:
    segments = _segments(spec)
    if not segments:
        return []
    out: list[str] = []
    kinds = [str(s.get("kind") or "") for s in segments]
    for kind, label, fix in (
        ("cold_open", "冷开场", "加一个环节：从一个具体场景或对话中段切入，不自我介绍"),
        ("intro", "开场", "加一个环节：介绍人和今天聊什么，一句话说清为什么值得听"),
        ("outro", "收尾", "加一个环节：留一句话和一个互动号召，不总结全篇"),
    ):
        if kind not in kinds:
            out.append(f"全期：没有{label}环节——{fix}")
    if kinds and kinds[0] not in ("cold_open", "intro"):
        out.append(f"segment {_sid(segments[0], 1)}: 第一个环节是「{S.SEGMENT_KIND_LABELS.get(kinds[0], kinds[0])}」——冷开场或开场要放在最前面，听众前 30 秒决定走不走")
    for i, segment in enumerate(segments, 1):
        sid = _sid(segment, i)
        if segment.get("kind") == "qa" and not segment.get("questions"):
            out.append(f"segment {sid}: 听友问答环节没有一条听众问题——填两三条真的会被问的问题（带称呼），起草才有东西可答")
        if not _lines(segment):
            out.append(f"segment {sid}: 这个环节还没有台词——点「起草整期」或「重写这一段」，或者手写几句")
    return out


def check_speech(spec: Mapping[str, Any]) -> list[str]:
    """书面语、独白太长、机械轮流、不认识的 cue。"""

    out: list[str] = []
    multi = _multi(spec)
    en = _is_en(spec)
    names = _names(spec)
    for i, segment in enumerate(_segments(spec), 1):
        sid = _sid(segment, i)
        rows = _lines(segment)
        for line in rows:
            text = str(line.get("text") or "")
            hits = _hits(BOOKISH, BOOKISH_EN, text, en)
            if hits:
                out.append(f"segment {sid}: 「{names.get(str(line.get('who')), line.get('who'))}」的台词里有书面语「{'、'.join(hits)}」——真人聊天不这么说；删掉连接词，直接说事")
            if multi and en and _size(text, en) > MONOLOGUE_WORDS:
                words = _size(text, en)
                out.append(f"segment {sid}: 「{names.get(str(line.get('who')), line.get('who'))}」一句 {words} 词——约 {round(words / EN_WORDS_PER_SECOND)} 秒没人插话，真人对谈里别人早接话了；拆开，让另一个人接一句或追问")
            elif multi and not en and len(text) > MONOLOGUE_CHARS:
                out.append(f"segment {sid}: 「{names.get(str(line.get('who')), line.get('who'))}」一句 {len(text)} 字——约 {round(len(text) / 4.5)} 秒没人插话，真人对谈里别人早接话了；拆开，让另一个人接一句或追问")
            cue = str(line.get("cue") or "")
            if cue and cue not in S.CUES:
                out.append(f"segment {sid}: cue「{cue}」不在清单里——换成「{' / '.join(S.CUES)}」里的一个，或留空")
        if multi and len(rows) >= ALTERNATION_MIN_LINES:
            switches = sum(1 for a, b in zip(rows, rows[1:]) if a.get("who") != b.get("who"))
            if switches == len(rows) - 1:
                out.append(f"segment {sid}: {len(rows)} 句每一句都换人——机械轮流像排队发言；让一个人连说两三句、另一个人只接一个「嗯」或打断")
    return out


def check_balance(spec: Mapping[str, Any]) -> list[str]:
    """一人独大、全程没分歧、填充词密度。"""

    if not _multi(spec):
        return []
    out: list[str] = []
    en = _is_en(spec)
    names = _names(spec)
    fmt = str(spec.get("format") or "duo")
    chars: dict[str, int] = {}          # 每人的字数（英文：词数）
    all_text: list[str] = []
    total_lines = 0
    for segment in _segments(spec):
        for line in _lines(segment):
            text = str(line.get("text") or "")
            chars[str(line.get("who"))] = chars.get(str(line.get("who")), 0) + _size(text, en)
            all_text.append(text)
            total_lines += 1
    if not all_text:
        return []
    total = sum(chars.values()) or 1
    if fmt in ("duo", "roundtable"):
        who, most = max(chars.items(), key=lambda kv: kv[1])
        if most / total > DOMINANCE_RATIO:
            unit = "词" if en else "字"
            out.append(f"全期：「{names.get(who, who)}」说了 {round(most / total * 100)}% 的{unit}——一人独大另一个人就成了捧哏；把一部分例子和观点分给别人，或让别人反驳")
    joined = "\n".join(all_text)
    if not _hits(DISAGREE, DISAGREE_EN, joined, en):
        out.append("全期：整期没有一处反对或分歧——真人对谈总有人不同意；加一处明确的不同意见，而且不要马上和解")
    filler_chars = _filler_size(joined, en)
    body = max(total if en else len(joined), 1)     # 中文照旧按拼起来的全文字数，英文按总词数
    if filler_chars / body > FILLER_RATIO:
        examples = "um / uh / like / you know…" if en else "嗯 / 啊 / 那个 / 然后…"
        out.append(f"全期：填充词（{examples}）占了 {round(filler_chars / body * 100)}%——TTS 念出来像结巴；留在接话处一两个，其余删掉")
    elif total_lines >= NO_FILLER_MIN_LINES and filler_chars == 0:
        examples = "「um」「right」" if en else "「嗯」「对」"
        out.append(f"全期：一个填充词都没有——略显播音腔；在接话和被打断的地方加一两个{examples}")
    return out


def check_topics(spec: Mapping[str, Any]) -> list[str]:
    """话题环节要有具体例子；问答环节的回答要像回答；收尾不总结全篇。"""

    out: list[str] = []
    en = _is_en(spec)
    for i, segment in enumerate(_segments(spec), 1):
        sid = _sid(segment, i)
        rows = _lines(segment)
        if not rows:
            continue
        joined = "\n".join(str(l.get("text") or "") for l in rows)
        kind = str(segment.get("kind") or "")
        if kind == "topic" and not _DIGIT.search(joined) and not _hits(SPECIFIC, SPECIFIC_EN, joined, en):
            out.append(f"segment {sid}: 整段没有一个具体例子——没有数字，也没有「有一次 / 上周 / 我记得 / 比如」；真人播客靠具体经历撑，不靠观点，从素材里拿一件事讲细")
        if kind == "qa":
            questions = [str(q.get("text") or "") for q in segment.get("questions") or () if isinstance(q, Mapping)]
            answers = [l for l in rows if l.get("kind") == "answer"]
            if questions and not answers:
                out.append(f"segment {sid}: 有听众问题但没有一句标成回答（kind=answer）——把回答的句子标出来，检查和节目单都靠它")
            for line in answers:
                text = str(line.get("text") or "").strip()
                if len(text) < ANSWER_MIN_CHARS:
                    out.append(f"segment {sid}: 回答「{text}」只有 {len(text)} 字——听众要的是结论加原因，不是一句敷衍；先给结论，再讲一个为什么")
                    continue
                if any(_echoes(text, q) for q in questions):
                    out.append(f"segment {sid}: 回答开头「{text[:ANSWER_ECHO_CHARS]}…」在复述问题——直接给结论，问题刚念过听众记得")
        if kind == "outro":
            hits = _hits(OUTRO_SUMMARY, OUTRO_SUMMARY_EN, joined, en)
            if hits:
                out.append(f"segment {sid}: 收尾在总结全篇（「{'、'.join(hits)}」）——真人播客结尾只留一句和互动号召；删掉回顾，留下一期预告")
    return out


def check_length(spec: Mapping[str, Any]) -> list[str]:
    est = estimate(spec)
    if not est["lines"]:
        return []
    target = est["target_minutes"]
    minutes = est["minutes"]
    if abs(minutes - target) / max(target, 1) > MINUTES_TOLERANCE:
        direction = "短" if minutes < target else "长"
        fix = "让起草多写一个话题或补一段亲历" if minutes < target else "砍掉最弱的一个话题，或把重复的观点删掉"
        return [f"全期：预计 {minutes} 分钟，目标 {target} 分钟，偏{direction}——{fix}"]
    return []


def _voice_key(host: Mapping[str, Any], profiles: Mapping[str, Mapping[str, Any]] | None) -> str:
    """两位主播「听起来是不是同一个人」的比较键：给了解析后的合成档案（音色库档案 id → 引擎 + 音色）就比真正的音色，
    否则只能比档案 id / 内联的引擎 + 音色。占位音色不参与（按语言轮流分，本来就不同）。"""

    voice = host.get("voice")
    if not voice:
        return ""
    resolved = (profiles or {}).get(str(host.get("id") or ""))
    if resolved and resolved.get("placeholder"):
        return ""            # 档案不存在 / 只有描述 → 已退到占位轮换，不再拿坏掉的档案 id 去比
    if resolved and resolved.get("voice"):
        return f"{resolved.get('engine') or 'edge'}|{resolved.get('voice')}"
    if isinstance(voice, Mapping):
        return "|".join(str(voice.get(k) or "") for k in ("engine", "voice"))
    return str(voice)


def check_voices(spec: Mapping[str, Any], profiles: Mapping[str, Mapping[str, Any]] | None = None) -> list[str]:
    """两位主播同一个音色 / 没绑音色。``profiles`` 是 ``audio.host_profiles`` 的结果：两个名字不同的音色库档案
    底下可能是同一个 edge 音色（两个名字不同的档案底下都是 XiaoxiaoNeural 并不少见），只比档案 id 查不出来。"""

    hosts = [h for h in spec.get("hosts") or () if isinstance(h, Mapping)]
    out: list[str] = []
    # 档案引用坏了（audio.host_profiles 打的标记）：不管几位主播都要说，不然保存 200 没告警、成片却是占位音色
    for host in hosts:
        resolved = (profiles or {}).get(str(host.get("id") or "")) or {}
        name = host.get("name") or host.get("id")
        # 句子里带「主播」二字：问题卡按关键词归类（webui/problems.py），没有它就落进「其他」
        if resolved.get("missing"):
            out.append(f"全期：主播「{name}」绑的音色档案「{resolved.get('profile_id')}」已不存在——去音色库重新挑一个，或先留空用占位音色")
        elif resolved.get("no_tts"):
            out.append(f"全期：主播「{name}」绑的档案「{resolved.get('profile_id')}」只有文字描述，不能出声——换一个带 TTS 音色的档案")
    if len(hosts) < 2:
        return out
    seen: dict[str, str] = {}
    for host in hosts:
        key = _voice_key(host, profiles)
        if not key:
            continue
        if key in seen:
            out.append(f"全期：「{seen[key]}」和「{host.get('name')}」绑的是同一个音色——听众分不清谁在说；给其中一位换一个音色")
        else:
            seen[key] = str(host.get("name") or host.get("id"))
    unbound = [str(h.get("name") or h.get("id")) for h in hosts if not h.get("voice")]
    if unbound and len(unbound) < len(hosts):
        out.append(f"全期：「{'、'.join(unbound)}」还没绑音色——会用占位音色出声，能听但和别人不好区分；去音色库挑一个")
    elif unbound:
        out.append("全期：主播都还没绑音色——会按语言轮流分配占位音色，能出声；正式出片前去音色库给每个人挑一个")
    return out


def lint_podcast(spec: Mapping[str, Any], profiles: Mapping[str, Mapping[str, Any]] | None = None) -> list[str]:
    """全部告警（顺序固定：结构 → 台词 → 平衡 → 话题 / 问答 → 时长 → 音色）。``profiles`` 给了就按真正的音色比重复。"""

    return (check_structure(spec) + check_speech(spec) + check_balance(spec) + check_topics(spec)
            + check_length(spec) + check_voices(spec, profiles))


__all__ = ["BOOKISH", "DISAGREE", "SPECIFIC", "FILLERS", "OUTRO_SUMMARY", "BOOKISH_EN", "DISAGREE_EN", "SPECIFIC_EN", "FILLERS_EN",
           "OUTRO_SUMMARY_EN", "MONOLOGUE_CHARS", "MONOLOGUE_WORDS", "DOMINANCE_RATIO", "FILLER_RATIO",
           "check_structure", "check_speech", "check_balance", "check_topics", "check_length", "check_voices", "lint_podcast"]
