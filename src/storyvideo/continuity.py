"""接缝、表演、镜头设计的静态检查（2026-09-06 立；思路参考了 waooAI/waoowaoo 的视频导演规范，句子按本仓库的写法重写）。

三条检查，落在本仓库的字段上：

* **入口/出口状态**：镜次可以带 ``entry_state`` / ``exit_state``，写这一镜开始和结束那一刻的样子：动作做到了哪一步、
  谁站在哪、手里有什么、脸朝哪看着什么、哪个声音还没停。有 ``ref_frame`` 的镜次是参考链上的一环，缺入口状态就等于
  让模型自己猜上一镜结束在哪——参考链「拼接顿挫」最常见的根源。
* **情绪落到动作上**：``art`` / ``motion`` / ``performance`` 里出现情绪形容词（悲伤、开心、惊恐……）
  会被视频模型放大成夸张表情；改写成镜头拍得到的细节——目光落在哪、停多久、一个停顿、手上正做的事。
* **景别读得出**：``shot`` 里至少要能读出景别；cast 的角色 block 不写默认表情（写了会让每一镜都带着这个表情）。

全部是告警不是报错：老项目不受影响，新项目在 prepare / webui 里看到就改。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

STATE_FIELDS = ("entry_state", "exit_state")

# 结构化起止边界（2026-09-08，融合 drama-skills shot-template 的 start_boundary/end_boundary，DESIGN §22）：
# shot.entry / shot.exit = {positions, facing, gaze, hands, held_props, visible_state, same_as_previous:[key…]}。
# 每个字段是"不读上一镜也能理解的绝对事实"；真的没变就把 key 放进 same_as_previous，编译器渲染成「与上一镜相同：位置、朝向」。
BOUNDARY_KEYS = ("positions", "facing", "gaze", "hands", "held_props", "visible_state")
BOUNDARY_LABELS = {"positions": "位置", "facing": "朝向", "gaze": "视线", "hands": "手", "held_props": "持物", "visible_state": "可见状态"}
BOUNDARY_LABELS_EN = {"positions": "positions", "facing": "facing", "gaze": "gaze", "hands": "hands",
                      "held_props": "held props", "visible_state": "visible state"}
# 相对写法：要往回翻才解释得通，模型没有上一镜
RELATIVE_WORDS = ("同上", "不变", "同前", "见上")
# 镜外换手/换持物是硬伤（道具凭空易手）；位置/朝向跳变可能是有意的切换，措辞软一些
_HARD_BOUNDARY_KEYS = ("hands", "held_props")
_SOFT_BOUNDARY_KEYS = ("positions", "facing")

# 情绪形容词：出现在表演描述里就是「让模型演情绪」而不是「让模型做动作」
EMOTION_WORDS = (
    "悲伤", "伤心", "难过", "开心", "高兴", "愤怒", "生气", "害怕", "恐惧", "惊恐", "委屈", "兴奋",
    "紧张", "尴尬", "痛苦", "绝望", "幸福", "快乐", "焦虑", "失落", "深情", "温柔地", "感动", "崩溃",
    "心碎", "羞涩", "害羞", "厌恶", "嫉妒", "愧疚",
)
# 景别词：shot 里至少要有一个，否则模型自己定景别，相邻镜容易同景别硬切
FRAMING_WORDS = ("特写", "近景", "中近景", "中景", "全景", "远景", "大远景", "大特写", "胸口以上", "膝盖以上",
                 "半身", "全身", "俯视", "仰视", "平视", "第一人称", "近拍", "自拍", "俯拍", "蹲拍", "入画", "特写镜")
# 英文 spec 的景别词（小写比对）；之前只查中文，英文提示词路径的项目这条检查等于没做
FRAMING_WORDS_EN = ("close-up", "closeup", "close up", "extreme close", "medium shot", "medium close", "mid shot",
                    "wide shot", "wide", "full shot", "long shot", "establishing", "two-shot", "two shot", "over-the-shoulder",
                    "over the shoulder", "pov", "first-person", "first person", "selfie", "macro", "bust shot", "waist up",
                    "knee up", "high angle", "low angle", "eye level", "bird's-eye", "top-down", "cowboy shot", "insert")
# cast 角色 block 里不该出现的默认表情词
DEFAULT_EXPRESSION_WORDS = ("默认表情", "嘴角微翘", "微笑", "笑容")

_CJK = re.compile(r"[一-鿿]")


def _text(value: Any) -> str:
    return str(value or "").strip()


def check_continuity(spec: Mapping[str, Any]) -> list[str]:
    """入口/出口状态的存在性与相邻对齐（只查结构，不查语义）。"""

    warnings: list[str] = []
    shots = [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]
    uses_states = any(_text(s.get(f)) for s in shots for f in STATE_FIELDS) or any(
        _boundary_filled(s.get(k)) for s in shots for k in ("entry", "exit"))
    if not uses_states:
        return warnings  # 老项目：没用这套字段就不吵
    for shot in shots:
        shot_id = _text(shot.get("id"))
        # 「上一镜」按接缝关系取：尾帧续接的镜（DESIGN §30）对着源镜的出口比，不是列表里紧挨着的那一镜
        previous = previous_for(spec, shot)
        entry_obj, exit_obj = shot.get("entry"), shot.get("exit")
        # 结构化对象填了就是入口/出口；否则看自由文本
        entry = _text(shot.get("entry_state")) or ("*" if _boundary_filled(entry_obj) else "")
        exit_ = _text(shot.get("exit_state")) or ("*" if _boundary_filled(exit_obj) else "")
        if shot.get("ref_frame") and not entry:
            warnings.append(f"shot {shot_id}: 有 ref_frame 但没写 entry_state——参考链上的镜次要写清入口（动作做到了哪一步、谁站在哪、手里有什么、脸朝哪看着什么）")
        elif shot.get("ref_tail") and not entry:
            # 尾帧续接（DESIGN §30）也是参考链上的一环：入口就是源镜的出口那一刻
            warnings.append(f"shot {shot_id}: 有尾帧续接但没写 entry_state——接尾帧的镜要写清入口（即源镜收尾那一刻：谁站在哪、手里有什么、脸朝哪看着什么）")
        if previous is not None:
            prev_exit = _text(previous.get("exit_state")) or ("*" if _boundary_filled(previous.get("exit")) else "")
            if entry and not prev_exit:
                warnings.append(f"shot {shot_id}: 写了 entry_state，但上一镜 {previous.get('id')} 没有 exit_state，接缝只有一半")
            if prev_exit != "*" and entry != "*" and prev_exit and entry and prev_exit == entry:
                warnings.append(f"shot {shot_id}: entry_state 与上一镜 exit_state 一字不差——入口要在出口之后（时间前进），不是复制")
        if entry and not exit_ and shot is not shots[-1]:
            warnings.append(f"shot {shot_id}: 有 entry_state 没有 exit_state，下一镜没法接")
        warnings.extend(_check_structured_boundary(shot, previous))
    return warnings


def previous_for(spec: Mapping[str, Any], shot: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """接缝比对时「上一镜」是谁：写了 ``ref_tail`` 且源镜真的在本镜之前 → 源镜（入口就是它出口那一刻，中间隔着的镜
    不是这条接缝的另一半）；否则按镜次顺序取前一镜；第一镜 None。"""

    from .tail import tail_ref

    shots = [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]
    index = next((i for i, s in enumerate(shots) if s is shot), None)
    if index is None:
        sid = _text(shot.get("id"))
        index = next((i for i, s in enumerate(shots) if _text(s.get("id")) == sid), None)
    if index is None:
        return None
    tail = tail_ref(shot)
    if tail is not None:
        source = next((s for s in shots[:index] if _text(s.get("id")) == tail[0]), None)
        if source is not None:
            return source
    return shots[index - 1] if index > 0 else None


def _boundary_filled(obj: Any) -> bool:
    if not isinstance(obj, Mapping):
        return False
    return any(_text(obj.get(k)) for k in BOUNDARY_KEYS) or bool(obj.get("same_as_previous"))


def _check_structured_boundary(shot: Mapping[str, Any], previous: Mapping[str, Any] | None) -> list[str]:
    """结构化 entry/exit 的逐字段检查：相对写法、与上一镜相同的勾选、镜外换手/跳位（boundary_change 说明来源可压掉）。"""

    warnings: list[str] = []
    sid = _text(shot.get("id"))
    for side, key_cn in (("entry", "入口"), ("exit", "出口")):
        obj = shot.get(side)
        if not isinstance(obj, Mapping):
            continue
        for key in BOUNDARY_KEYS:
            value = _text(obj.get(key))
            hit = next((w for w in RELATIVE_WORDS if w in value), None)
            if hit:
                warnings.append(f"shot {sid}: {key_cn}的{BOUNDARY_LABELS[key]}「{value}」用了「{hit}」——每个字段写成不读上一镜也能理解的绝对事实；"
                                "真的没变就勾「与上一镜相同」，编译器会替你写")
    entry = shot.get("entry")
    if not isinstance(entry, Mapping):
        return warnings
    same = [str(k) for k in (entry.get("same_as_previous") or ())]
    prev_exit = previous.get("exit") if previous is not None else None
    prev_id = _text(previous.get("id")) if previous is not None else ""
    for key in same:
        if key not in BOUNDARY_KEYS:
            warnings.append(f"shot {sid}: same_as_previous 里的「{key}」不是边界字段（只认 {'/'.join(BOUNDARY_KEYS)}）")
            continue
        label = BOUNDARY_LABELS[key]
        if previous is None:
            warnings.append(f"shot {sid}: 第一镜勾了「与上一镜相同：{label}」，但它没有上一镜——把{label}写成绝对事实")
        elif not isinstance(prev_exit, Mapping) or not _text(prev_exit.get(key)):
            warnings.append(f"shot {sid}: 勾了「与上一镜相同：{label}」，但上一镜 {prev_id} 的出口没写{label}——先把上一镜出口补上，否则「相同」没有参照")
    if not isinstance(prev_exit, Mapping):
        return warnings
    change = _text(shot.get("boundary_change"))
    for key in _HARD_BOUNDARY_KEYS + _SOFT_BOUNDARY_KEYS:
        before, after = _text(prev_exit.get(key)), _text(entry.get(key))
        if not before or not after or before == after:
            continue
        label = BOUNDARY_LABELS[key]
        if key in same:
            warnings.append(f"shot {sid}: 勾了「与上一镜相同：{label}」，但本镜入口写的「{after}」和上一镜出口「{before}」不一样——二选一")
            continue
        if change:
            continue
        if key in _HARD_BOUNDARY_KEYS:
            warnings.append(f"shot {sid}: 持物/手部状态在镜外变了：上一镜 {prev_id} 出口{label}「{before}」，本镜入口「{after}」——"
                            "要么在上一镜的唯一动作里完成，要么写 boundary_change 说明来源")
        else:
            warnings.append(f"shot {sid}: {label}在镜外跳变：上一镜 {prev_id} 出口「{before}」，本镜入口「{after}」——"
                            "有意的切换就写 boundary_change 说明，否则对齐上一镜出口")
    return warnings


def check_performance_language(spec: Mapping[str, Any]) -> list[str]:
    """art / motion / performance / beats 里的情绪形容词。"""

    warnings: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        shot_id = _text(shot.get("id"))
        texts = [_text(shot.get(k)) for k in ("art", "motion", "performance")]
        for beat in shot.get("beats") or ():
            if isinstance(beat, Mapping):
                texts.extend(_text(beat.get(k)) for k in ("picture", "performance"))
        blob = " ".join(texts)
        hits = sorted({w for w in EMOTION_WORDS if w in blob})
        if hits:
            warnings.append(
                f"shot {shot_id}: 表演描述里有情绪形容词「{'、'.join(hits)}」——视频模型会放大成夸张表情；"
                "改写成镜头拍得到的动作（目光落在哪、停多久、一个停顿、手上正做的事）"
            )
    return warnings


def check_shot_design(spec: Mapping[str, Any]) -> list[str]:
    """shot 文本要能读出景别；cast 角色 block 不写默认表情。"""

    warnings: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        shot_text = _text(shot.get("shot"))
        if not shot_text.strip():
            continue
        if _CJK.search(shot_text):
            found = any(w in shot_text for w in FRAMING_WORDS)
        else:
            lowered = shot_text.lower()
            found = any(w in lowered for w in FRAMING_WORDS_EN)
        if not found:
            warnings.append(f"shot {shot.get('id')}: shot 里读不出景别（特写/近景/中景/全景…）——相邻镜同景别硬切是顿挫的第一来源")
    cast = spec.get("cast") or {}
    if isinstance(cast, Mapping):
        for cast_id, member in cast.items():
            if not isinstance(member, Mapping) or not member.get("name"):
                continue  # 只查有名字的角色，场景/道具块不查
            block = _text(member.get("block"))
            hits = [w for w in DEFAULT_EXPRESSION_WORDS if w in block and "不要默认" not in block]
            if hits:
                warnings.append(f"cast {cast_id}: 角色描述里有「{'、'.join(hits)}」——表情逐镜写，角色块只写长相/发型/服装/身材")
    return warnings


def check_beats(spec: Mapping[str, Any]) -> list[str]:
    """beats 的时间块要落在 dur 内、按时间前进。"""

    warnings: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping) or not shot.get("beats"):
            continue
        shot_id = _text(shot.get("id"))
        dur = float(shot.get("dur") or 0)
        last_end = 0.0
        for beat in shot.get("beats") or ():
            if not isinstance(beat, Mapping):
                continue
            start, end = _parse_span(beat.get("t"))
            if start is None:
                warnings.append(f"shot {shot_id}: beat 的 t 写法要像「0-3」或「0–3」，现在是 {beat.get('t')!r}")
                continue
            if start < last_end:
                warnings.append(f"shot {shot_id}: beat {beat.get('t')} 回到了更早的时间——时间只能前进")
            if end > dur + 0.01:
                warnings.append(f"shot {shot_id}: beat {beat.get('t')} 超过了 dur {dur:g}s")
            last_end = max(last_end, end)
    return warnings


def _parse_span(value: Any) -> tuple[float | None, float]:
    text = _text(value).replace("–", "-").replace("—", "-").replace("s", "")
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*", text)
    if not m:
        return None, 0.0
    return float(m.group(1)), float(m.group(2))


def check_all(spec: Mapping[str, Any], *, image_adapter: str = "") -> list[str]:
    """``image_adapter`` 透传给 lint：参考图上限按「模型设置」里的生图来源查（见 lint._max_reference_images）。"""

    from . import lint

    return (check_continuity(spec) + check_performance_language(spec) + check_shot_design(spec) + check_beats(spec)
            + lint.check_all(spec, image_adapter=image_adapter))


__all__ = ["check_continuity", "previous_for", "check_performance_language", "check_shot_design", "check_beats", "check_all",
           "EMOTION_WORDS", "FRAMING_WORDS", "STATE_FIELDS", "BOUNDARY_KEYS", "BOUNDARY_LABELS", "BOUNDARY_LABELS_EN",
           "RELATIVE_WORDS"]
