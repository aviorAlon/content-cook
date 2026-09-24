"""单集契约（DESIGN §32，融合 drama-skills「单集契约」的判据，不取它的 Markdown 语法）。

drama-skills 说：写节拍或剧本之前，先为本集回答「进入状态 / 当前压力与钩子 / 目标与反对力量 / 当集兑现 /
出去状态与交接」。我们把它压成五个字段放在 spec 顶层（可选，老项目没有就什么都不变）：

    episode_contract = {
        "premise":      "本集一句话前提（进入状态 + 谁现在要什么）",
        "hook":         "开场钩子：观众能立即理解的未稳定局面（不是一句神秘台词）",
        "cliffhanger":  "集尾出去的压力：某人已经决定做什么 / 某个后果已经启动",
        "must_pay_off": ["本集自己必须兑现的东西（局部结果）", ...],
        "carry_over":   ["留给下一集的未决事实：谁知道了什么、谁拿着什么、哪个危险已经开始", ...],
    }

契约**不进提示词**（提示词金丝雀守着）：它是写作时的承诺，写完之后由 ``lint.check_contract`` 反过来核对正文
有没有兑现——钩子要在前两镜看得见、集尾要落在最后一镜、承诺兑现的事要在某一镜里找得到。这里放形状归一化
和「正文里提到了吗」这个判据；系列交接（``series.py``）也用同一个判据决定哪些承诺算已兑现。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

CONTRACT_TEXT_KEYS = ("premise", "hook", "cliffhanger")
CONTRACT_LIST_KEYS = ("must_pay_off", "carry_over")
CONTRACT_KEYS = CONTRACT_TEXT_KEYS + CONTRACT_LIST_KEYS

# 拆「有辨识度的词」：按标点/空格切，≥2 字才算（跟揭示保护 lint 同一套切法）
_SPLIT = re.compile(r"[\s，。、；：！？,.;:!?「」『』《》〈〉()（）\[\]【】\-—–_/\\'\"“”‘’]+")
TOKEN_MIN = 2
_CJK = re.compile(r"[一-鿿]")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
# 整句没有原样出现时的兜底：中文看二字片段、英文看词，命中比例到这个数就算「提到了」。
# 契约是概括句、正文是具体动作（「齐曼当众要求出示完整排班记录」 vs 「把完整的排班记录拿出来」），
# 同义改写只剩三四成片段重合，所以门槛放低；这是告警不是门禁，宁可漏报也别把兑现了的事说成没兑现。
ECHO_RATIO = 0.35


class ContractError(ValueError):
    """契约 / 系列字段形状不对（spec 归一化时包成 SpecError）。"""


def _clean_text(value: Any, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ContractError(f"{label} 要是一句话（字符串）")
    return value.strip()


def _clean_list(value: Any, label: str) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        # 一行一条 / 分号分隔都收；界面 textarea 里就是这么填的
        items: list[Any] = [p for p in re.split(r"[\n;；]+", value)]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ContractError(f"{label} 要是字符串列表（一行一条）")
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ContractError(f"{label} 里每一条都要是字符串")
        text = item.strip()
        if text:
            out.append(text)
    return out


def normalize_contract(value: Any) -> dict[str, Any] | None:
    """``episode_contract`` → ``{premise, hook, cliffhanger, must_pay_off, carry_over}``；None / 空对象 → None。

    形状不对抛 ``ContractError``；不认识的键直接拒绝（写错键名等于契约没写，静默丢掉会骗人）。
    """

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ContractError("episode_contract 要是对象（premise / hook / cliffhanger / must_pay_off / carry_over）")
    unknown = [k for k in value if k not in CONTRACT_KEYS]
    if unknown:
        raise ContractError(f"episode_contract 里不认识的字段：{'、'.join(str(k) for k in unknown)}")
    out: dict[str, Any] = {}
    for key in CONTRACT_TEXT_KEYS:
        out[key] = _clean_text(value.get(key), f"episode_contract.{key}")
    for key in CONTRACT_LIST_KEYS:
        out[key] = _clean_list(value.get(key), f"episode_contract.{key}")
    if not any(out[k] for k in CONTRACT_KEYS):
        return None
    return out


def coerce_contract(value: Any) -> dict[str, Any] | None:
    """模型草稿用的宽松版：能救的救（列表里的非字符串丢掉、不认识的键忽略），救不回来就 None，绝不抛。"""

    if not isinstance(value, Mapping):
        return None
    cleaned: dict[str, Any] = {}
    for key in CONTRACT_TEXT_KEYS:
        raw = value.get(key)
        cleaned[key] = raw.strip() if isinstance(raw, str) else ""
    for key in CONTRACT_LIST_KEYS:
        raw = value.get(key)
        if isinstance(raw, str):
            cleaned[key] = raw
        elif isinstance(raw, (list, tuple)):
            cleaned[key] = [x for x in raw if isinstance(x, str)]
        else:
            cleaned[key] = []
    try:
        return normalize_contract(cleaned)
    except ContractError:
        return None


_SERIES_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,63}\Z")


def normalize_series(value: Any) -> dict[str, Any] | None:
    """``series = {id, episode_no}``：id 是文件名安全的 slug，episode_no 是正整数（可缺）。None → None。"""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ContractError("series 要是对象（id / episode_no）")
    unknown = [k for k in value if k not in ("id", "episode_no")]
    if unknown:
        raise ContractError(f"series 里不认识的字段：{'、'.join(str(k) for k in unknown)}")
    sid = value.get("id")
    if not isinstance(sid, str) or not sid.strip():
        raise ContractError("series.id 不能为空")
    sid = sid.strip()
    if not _SERIES_ID_RE.fullmatch(sid):
        raise ContractError("series.id 只能用英文、数字、下划线、点、短横线，且以英文/数字/下划线开头")
    out: dict[str, Any] = {"id": sid}
    no = value.get("episode_no")
    if no is not None and no != "":
        if isinstance(no, bool) or not isinstance(no, (int, float, str)):
            raise ContractError("series.episode_no 要是正整数")
        try:
            number = int(str(no).strip())
        except ValueError as exc:
            raise ContractError("series.episode_no 要是正整数") from exc
        if number <= 0 or (isinstance(no, float) and not float(no).is_integer()):
            raise ContractError("series.episode_no 要是正整数")
        out["episode_no"] = number
    return out


# ---- 「正文里提到了吗」判据

def shot_text(shot: Mapping[str, Any]) -> str:
    """一镜的可核对正文：画面、美术、动作、台词（中英）、beats 三栏。"""

    parts = [str(shot.get(k) or "") for k in ("shot", "art", "motion")]
    for line in shot.get("lines") or ():
        if isinstance(line, Mapping):
            parts.extend(str(line.get(k) or "") for k in ("zh", "en"))
    for beat in shot.get("beats") or ():
        if isinstance(beat, Mapping):
            parts.extend(str(beat.get(k) or "") for k in ("picture", "performance", "sound"))
    return " ".join(p for p in parts if p)


def tokens(text: str) -> list[str]:
    return [tok for tok in _SPLIT.split(text or "") if len(tok) >= TOKEN_MIN]


def _bigrams(text: str) -> set[str]:
    chars = "".join(_CJK.findall(text))
    return {chars[i:i + 2] for i in range(len(chars) - 1)}


def echo_ratio(text: str, blob: str) -> float:
    """``text`` 有多大比例在 ``blob`` 里出现：中文按二字片段、英文按 ≥3 字母的词；两边都没有可比的单位 → 0。"""

    units: set[str] = _bigrams(text)
    units |= {w.lower() for w in _LATIN_WORD.findall(text)}
    if not units:
        return 0.0
    low = blob.lower()
    hit = sum(1 for u in units if u in low)
    return hit / len(units)


def mentioned(text: str, blob: str) -> bool:
    """契约里的一句话在正文里「提到了」：原句或它拆出来的任一有辨识度的词原样出现；都没有时看片段命中比例。"""

    text = (text or "").strip()
    if not text or not blob:
        return False
    if text in blob:
        return True
    if any(tok in blob for tok in tokens(text)):
        return True
    return echo_ratio(text, blob) >= ECHO_RATIO


# ---- 「有意留白」豁免（本仓库自定的写法，取的是这个常用词的字面意思）：must_pay_off 的条目写成「……（有意留白）」/
# 「……（留白）」= 作者明确说这一集不兑现。它既不算已兑现也不算未兑现，check_contract 不报它，系列记忆把它记进未决并标「有意留白」。
DEFERRED_MARKERS = ("（有意留白）", "（留白）", "(有意留白)", "(留白)")
DEFERRED_LABEL = "（有意留白）"


_TRAILING_PUNCT = "。．.；;！!，, "


def _core(item: str) -> str:
    """去掉末尾的句号 / 分号等标点，标记写在句号前后都认。"""

    return str(item or "").strip().rstrip(_TRAILING_PUNCT)


def is_deferred(item: str) -> bool:
    text = _core(item)
    return any(text.endswith(m) for m in DEFERRED_MARKERS)


def deferred_text(item: str) -> str:
    """去掉末尾的留白标记，只剩承诺本身；不是留白条目就原样返回；只写了「（留白）」没写承诺的，原样返回免得变成空串。"""

    text = _core(item)
    for marker in DEFERRED_MARKERS:
        if text.endswith(marker):
            core = text[: -len(marker)].strip().rstrip(_TRAILING_PUNCT)
            return core or str(item or "").strip()
    return str(item or "").strip()


def payoff_status(spec: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """``must_pay_off`` 里哪些在某一镜正文里找得到（已兑现）、哪些找不到（未兑现）、哪些标了「有意留白」（deferred，
    单独归一类，正文里有没有提到都不看）。没有契约 → 三个空列表。"""

    contract = spec.get("episode_contract")
    if not isinstance(contract, Mapping):
        return [], [], []
    blob = " ".join(shot_text(s) for s in spec.get("shots", ()) if isinstance(s, Mapping))
    paid: list[str] = []
    unpaid: list[str] = []
    deferred: list[str] = []
    for item in contract.get("must_pay_off") or ():
        item = str(item or "").strip()
        if not item:
            continue
        if is_deferred(item):
            deferred.append(item)
        else:
            (paid if mentioned(item, blob) else unpaid).append(item)
    return paid, unpaid, deferred


__all__ = [
    "CONTRACT_KEYS", "CONTRACT_TEXT_KEYS", "CONTRACT_LIST_KEYS", "ContractError", "DEFERRED_MARKERS", "DEFERRED_LABEL",
    "normalize_contract", "coerce_contract", "normalize_series",
    "shot_text", "tokens", "echo_ratio", "mentioned", "is_deferred", "deferred_text", "payoff_status",
]
