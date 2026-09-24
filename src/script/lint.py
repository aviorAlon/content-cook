"""静态检查（契约 docs/scriptlab-spec.md §4）：不花钱，按 ``target`` 选判据。

**一条判据也不新造**（契约 §10）：把工作台的某一版拼成已有模块认的形状，再调它们的函数，词表与阈值都是人家的——

* ``podcast``：拼成 ``{hosts, segments, language, format}`` 交给 ``podcast.lint`` 的
  ``check_speech`` / ``check_balance`` / ``check_topics``，取其中的台词类判据：书面语、独白太长、机械轮流、一人独大、
  全程没分歧、填充词密度、没有具体例子、回答复述问题 / 太短。结构类（缺冷开场 / 收尾、音色）跳过——工作台没有
  环节种类与音色。cue 写错在 ``spec.normalize`` 就拦下了，这里不再判。
* ``drama_screenplay`` / ``drama_shot``：拼成 ``{cast, scenes}`` 交给 ``storyvideo.screenplay.check_line_length``
  （单句 > ``LINE_MAX_CHARS`` 字，**只判中文稿**）与 ``check_speaking_cast``（开口的人 > ``SPEAKING_CAST_MAX``，
  不分语言）。``LINE_MAX_CHARS`` 是按中文「一口气说不完」定的**字数**，英文稿没有对应的词数阈值——借来的判据
  没有英文口径就不判（照搬会把几乎每一句英文台词都报成太长），要判先得有人给出英文的口径。
* ``drama_shot`` 另加对白密度：一段就是一镜，**容量模型整个借 ``storyvideo.preview.shot_duration_budget``**
  （把这一段拼成一镜 ``{dur: SHOT_SECONDS_CEILING, lines}`` 交给它），窗口、快端秒数、慢端秒数、装不下 / 偏紧
  的判断全用它回的那几个数——跟拆镜之后 ``preview.check_duration_budget`` 对同一段台词算出来的**是同一个数**，
  而且慢端那半条「偏紧」的信号也跟着来了。preview 以后改窗口口径（改 ``HEAD_SECONDS`` / 换算法），这边自动跟着变，
  不会静默分叉。**只判中文稿**：preview 数的是字符、按中文 4.5 字/秒定标，英文一句话的字符数是词数的五六倍，
  照搬等于每段英文台词都报装不下；英文该多少词一镜仓库里没有这个判据，工作台也不新造（契约 §10），
  跟单句字数那条同一个理由。注意这条的秒数与 ``craft.estimate`` 的秒数**口径不同**：这里是 preview 的纯语音
  （字数 ÷ 语速，不含句间停顿），那边含句间与段间停顿——一个回答「这一镜装不装得下」，一个回答「这一段念出来多久」。
  两个数都在 ``block_estimates`` 的同一行里，但各有各的名字（``speech_seconds`` / ``seconds``），界面上分开标注，
  不要互相对账。
  ``drama_screenplay`` **不出**这条告警：仓库里没有「一场该多长」的判据，拿一镜的窗口去卡一场会天天误报，自己定一个
  阈值又是新造判据（契约 §10 不许）。改成不带阈值地把每段的字数 / 两种秒数报出来（``block_estimates``）——契约 §4
  给 ``drama_screenplay`` 点名的那个「``preview.CHARS_PER_SECOND`` 4.5 字/秒估的秒数」就是那一行的
  ``speech_seconds``——§8 的详情里回传给前端显示，判断留给人。
* ``generic``：两边的交集里能静态判的四条——书面语、独白太长、一人独大、没有具体例子。

段种类：``podcast.lint.check_topics`` 要靠环节种类分话题 / 问答，工作台就让段骨架自己说
（``spec.BLOCK_KINDS``）：``kind=qa`` 的段当听友问答（这一段里 ``kind=question`` 的台词当听众问题），
其余当话题。**不按台词猜**——对谈里的追问也是问句，「嗯」「对」这种短接话也会被标成回答（起草提示词还专门
要求这么写），猜的结果是把一段正常对谈整段报成「回答太短」。收尾总结那条永远不判（没有 outro 种类），
正好对上契约「结构类跳过」。

告警句形状换成工作台的：``block B01: 影响——改法`` / ``全篇：影响——改法``（正文一个字不改，只换句头），
接口层 ``webui/problems.py`` 照旧按「——」拆成问题卡，分类落在已有的 ``dialogue`` / ``podcast``。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from podcast import craft as PC
from podcast import lint as PL
from storyvideo import screenplay as SP
from storyvideo.lint import SHOT_SECONDS_CEILING
from storyvideo.preview import HEAD_SECONDS, TAIL_SECONDS, shot_duration_budget

from . import spec as S
from .craft import estimate

# 播客那几条判据在告警句里的稳定标记：按 target 选用哪几条时靠它认。上游改了句子，
# tests/test_script_lint.py 里「每条都要认得出来」那条会先炸，不会静默丢判据。
_PODCAST_MARKS: tuple[tuple[str, str], ...] = (
    ("bookish", "书面语"),
    ("monologue", "没人插话"),
    ("alternation", "每一句都换人"),
    ("cue", "不在清单里"),
    ("dominance", "一人独大"),
    ("disagree", "反对或分歧"),
    ("newsreader", "播音腔"),      # 「一个填充词都没有」那句里也有「填充词」，所以排在 filler 前面
    ("filler", "填充词"),
    ("specific", "具体例子"),
    ("answer_missing", "标成回答"),
    ("answer_short", "结论加原因"),
    ("answer_echo", "复述问题"),
)
# 契约 §4 的表：podcast 用台词类的全部（cue 已在归一化拦下），generic 只留四条
_PODCAST_KEYS = ("bookish", "monologue", "alternation", "dominance", "disagree", "filler", "newsreader",
                 "specific", "answer_missing", "answer_short", "answer_echo")
_GENERIC_KEYS = ("bookish", "monologue", "dominance", "specific")

_SEGMENT_HEAD = re.compile(r"^segment (\S+?): ")
_SCENE_HEAD = re.compile(r"^scene (\S+?): ")
# 告警里「」包的是用户自己的台词 / 名字，认判据时要先抠掉（见 _mark）
_QUOTED = re.compile(r"「[^」]*」")


def _version_blocks(spec: Mapping[str, Any], version: str | None) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """``[(骨架段, 这一版的台词)]``，按骨架顺序。没有版本 = 空。"""

    picked = S.pick_version(spec, version)
    if picked is None:
        return []
    by_id = {str(b.get("id")): list(b.get("lines") or []) for b in picked.get("blocks") or () if isinstance(b, Mapping)}
    out = []
    for block in spec.get("blocks") or ():
        if isinstance(block, Mapping) and block.get("id"):
            out.append((dict(block), by_id.get(str(block["id"]), [])))
    return out


def _rehead(warning: str) -> str:
    """把借来的句头换成工作台的：``segment X: `` / ``scene X: `` → ``block X: ``；``全期：`` / ``全集：`` → ``全篇：``。"""

    for prefix in ("全期：", "全集："):
        if warning.startswith(prefix):
            return "全篇：" + warning[len(prefix):]
    text = _SEGMENT_HEAD.sub(r"block \1: ", warning, count=1)
    if text != warning:
        return text
    return _SCENE_HEAD.sub(r"block \1: ", warning, count=1)


def _mark(warning: str) -> str:
    """一条播客告警属于哪条判据；认不出来 = 空串（上游新增了判据）。

    先把「」里的内容抠掉再认：判据词只认告警自己的固定文字，不在用户台词里找。不抠的话，一句正好写着
    「书面语」的短回答，它那条「回答太短」会被先命中的 bookish 认走，generic 目标下就跟着放行了。
    """

    body = _QUOTED.sub("", warning)
    for key, needle in _PODCAST_MARKS:
        if needle in body:
            return key
    return ""


def as_podcast(spec: Mapping[str, Any], version: str | None = None) -> dict[str, Any]:
    """拼成 ``podcast.lint`` 认的形状：角色当主播、段当环节，形式按人数选（1 人 solo、2 人 duo、再多 roundtable）。"""

    speakers = [s for s in spec.get("speakers") or () if isinstance(s, Mapping)]
    fmt = "solo" if len(speakers) <= 1 else "duo" if len(speakers) == 2 else "roundtable"
    segments = []
    for block, lines in _version_blocks(spec, version):
        qa = str(block.get("kind") or "talk") == "qa"
        questions = [{"from": "", "text": str(l.get("text") or "")}
                     for l in lines if qa and l.get("kind") == "question"]
        segments.append({
            "id": str(block["id"]),
            "kind": "qa" if qa else "topic",
            "title": str(block.get("title") or ""),
            "goal": str(block.get("goal") or ""),
            "beats": [],
            "questions": questions,
            "lines": lines,
        })
    return {
        "format": fmt,
        "language": str(spec.get("language") or "zh"),
        "hosts": [{"id": s["id"], "name": s.get("name") or s["id"], "role": "host"} for s in speakers],
        "segments": segments,
    }


def as_screenplay(spec: Mapping[str, Any], version: str | None = None) -> dict[str, Any]:
    """拼成 ``storyvideo.screenplay`` 认的形状：角色当人物表、段当场次、台词的 ``text`` 当 ``zh``。"""

    speakers = [s for s in spec.get("speakers") or () if isinstance(s, Mapping)]
    scenes = []
    for block, lines in _version_blocks(spec, version):
        scenes.append({
            "id": str(block["id"]),
            "heading": str(block.get("title") or ""),
            "beats": [],
            "dialogue": [{"who": str(l.get("who") or ""), "zh": str(l.get("text") or "")} for l in lines],
        })
    return {"cast": [{"id": s["id"], "name": s.get("name") or s["id"]} for s in speakers], "scenes": scenes}


def check_podcast_speech(spec: Mapping[str, Any], version: str | None = None, *,
                         keys: tuple[str, ...] = _PODCAST_KEYS, keep_unknown: bool = True) -> list[str]:
    """借 ``podcast.lint`` 的三个函数，只留 ``keys`` 里那几条判据，句头换成工作台的。

    ``keep_unknown`` 是给 podcast 目标留的后路：认不出来的告警（上游新增了判据）照样放出来，不静默丢；
    generic 目标只要契约列的那四条，所以关掉。"""

    doc = as_podcast(spec, version)
    borrowed = PL.check_speech(doc) + PL.check_balance(doc) + PL.check_topics(doc)
    out: list[str] = []
    for warning in borrowed:
        mark = _mark(warning)
        if mark in keys or (keep_unknown and not mark):
            out.append(_rehead(warning))
    return out


def check_drama_lines(spec: Mapping[str, Any], version: str | None = None) -> list[str]:
    """借剧本层的两条：单句太长（> ``LINE_MAX_CHARS`` 字，**只判中文稿**）、开口的人太多（>3，不分语言）。

    ``check_line_length`` 数的是**汉字个数**（剧本层的经验上限，一句不超过 ``LINE_MAX_CHARS`` 字）。英文一句 25 个字符
    才五六个词，照搬等于每句英文台词都报太长；英文该多少词一句，仓库里没有这个判据，工作台也不新造
    （契约 §10），所以英文稿这条直接不判。开口人数那条数的是人，跟语言无关，照判。
    """

    doc = as_screenplay(spec, version)
    borrowed = SP.check_line_length(doc) if str(spec.get("language") or "zh") != "en" else []
    return [_rehead(w) for w in borrowed + SP.check_speaking_cast(doc)]


def block_budget(block_id: str, lines: list[Mapping[str, Any]]) -> dict[str, Any]:
    """把一段拼成一镜交给 ``storyvideo.preview.shot_duration_budget``：``dur`` 取单镜稳定上限
    （``storyvideo.lint.SHOT_SECONDS_CEILING``），台词的 ``text`` 当 ``zh``。

    容量模型一个数也不在这里自己算——窗口（扣掉开口等待与末尾反应）、快端 / 慢端秒数、装不下 / 偏紧的判断
    全是 preview 的，跟拆镜之后对同一段台词做的体检是同一套口径。
    """

    return shot_duration_budget({"id": block_id, "dur": SHOT_SECONDS_CEILING,
                                 "lines": [{"zh": str(l.get("text") or "")} for l in lines]})


def check_shot_density(spec: Mapping[str, Any], version: str | None = None) -> list[str]:
    """一段就是一镜时的对白密度：借 ``preview.shot_duration_budget`` 的容量模型判「这一镜装不装得下」。

    两档信号跟 ``preview.check_duration_budget`` 一样：快端（4.5 字/秒）都装不下 = **装不下**；
    快端装得下、慢端（3.5 字/秒）装不下 = **偏紧**（语气一慢就赶词）。自己不定阈值、不减常量。

    **英文稿不判**：preview 的容量模型数的是字符、按中文语速定标，英文一句话的字符数是词数的五六倍，
    照搬等于每段都报装不下；英文该多少词一镜仓库里没有这个判据，工作台也不新造（契约 §10）——
    跟 ``check_drama_lines`` 里单句字数那条同一个理由。
    """

    if str(spec.get("language") or "zh") == "en":
        return []
    out: list[str] = []
    for block, lines in _version_blocks(spec, version):
        budget = block_budget(str(block["id"]), lines)
        if not budget["dialogue_chars"]:
            continue
        window = f"{budget['window_seconds']:g} 秒（单镜按 {SHOT_SECONDS_CEILING:g} 秒算，" \
                 f"扣掉开口等待 {HEAD_SECONDS:g} 秒和末尾反应 {TAIL_SECONDS:g} 秒）"
        if budget["overflow"]:
            out.append(
                f"block {block['id']}: 台词 {budget['dialogue_chars']} 字 ≈{budget['speech_seconds']:g} 秒纯语音，"
                f"超过一镜的可用发声窗口 {window}"
                "——这一镜装不下这些台词，拆成两段，或者把说明性的半句改成动作"
            )
        elif budget["tight"]:
            out.append(
                f"block {block['id']}: 台词 {budget['dialogue_chars']} 字按偏慢语速要 "
                f"≈{budget['speech_seconds_slow']:g} 秒，一镜的可用发声窗口只有 {window}"
                "——偏紧，语气一慢就赶词；减一句，或者把说明性的半句改成动作"
            )
    return out


def block_estimates(spec: Mapping[str, Any], version: str | None = None) -> list[dict[str, Any]]:
    """逐段的 ``{id, title, chars, lines, seconds, minutes, speech_seconds}``。

    **两个都叫「秒」的数，口径不一样，所以各带各的名字：**

    * ``seconds`` / ``minutes``：整篇估算那一套（``podcast.craft.estimate``，中文 4.5 字/秒、英文 2.6 词/秒，
      **含句间与段间停顿**），回答「这一段念出来多久」；
    * ``speech_seconds``：``storyvideo.preview`` 的**纯语音**口径（``block_budget`` 借的那个容量模型，不含停顿），
      回答「这些字发声要占多久」——契约 §4 给 ``drama_screenplay`` / ``drama_shot`` 点名的「对白密度
      （``preview.CHARS_PER_SECOND`` 4.5 字/秒估的秒数）」落的就是这一个数，也是 ``check_shot_density``
      那条告警里的秒数。英文稿没有这个口径（容量模型数的是字符、按中文语速定标），给 ``None``，界面上别显示。

    **不带任何阈值、不判对错**：一场该多长、一段该多少字，仓库里没有这个判据，工作台也不新造（契约 §10）。
    这份数交给 §8 的详情返回、前端逐段显示，人自己看。一镜装不装得下是另一回事：那有现成的判据
    （``check_shot_density``），照样出告警。
    """

    language = str(spec.get("language") or "zh")
    out: list[dict[str, Any]] = []
    for block, lines in _version_blocks(spec, version):
        est = PC.estimate({"language": language, "segments": [{"lines": lines}] if lines else []})
        budget = block_budget(str(block["id"]), lines) if language != "en" else None
        out.append({"id": str(block["id"]), "title": str(block.get("title") or ""), "chars": est["chars"],
                    "lines": est["lines"], "seconds": est["seconds"], "minutes": est["minutes"],
                    "speech_seconds": budget["speech_seconds"] if budget else None})
    return out


def lint_script(spec: Mapping[str, Any], version: str | None = None) -> list[str]:
    """按 ``target`` 选判据，返回全部告警（顺序固定：逐段的在前、整篇的在后，跟借来的函数一致）。"""

    target = str(spec.get("target") or "generic")
    if target == "podcast":
        return check_podcast_speech(spec, version)
    if target in ("drama_screenplay", "drama_shot"):
        warnings = check_drama_lines(spec, version)
        if target == "drama_shot":
            warnings += check_shot_density(spec, version)
        return warnings
    return check_podcast_speech(spec, version, keys=_GENERIC_KEYS, keep_unknown=False)


__all__ = ["as_podcast", "as_screenplay", "check_podcast_speech", "check_drama_lines",
           "block_budget", "check_shot_density", "block_estimates", "lint_script", "estimate"]
