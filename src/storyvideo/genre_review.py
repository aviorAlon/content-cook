"""按题材审稿（DESIGN §49）：用文本模型拿选定的题材卡逐条对照这一集的剧本，**引文由程序核对**。

为什么是模型审、不是词表 lint：题材卡的「常见坑」「禁止漂移」是语义判断（「孩子只负责卖萌、撮合和说金句」
「冲突来自尚未被承认的关系，不是已知亲属的旧账」），字符串匹配判不了，只会出噪音。做法取 drama-skills
short-drama-review 的审稿表：每条 finding 要有位置和短引文，不许给无证据的结论——再加一道硬核对：

- 模型对每一条卡片条目（题材核心 / 常见坑 / 禁止漂移，各自从 1 编号）回答 ``ok`` / ``risk`` / ``violated``；
  判 risk / violated 必须抄一段原文（``quote``）并列出它来自哪几镜 / 哪几场（``shots``）。
- ``verify_review`` 把引文和正文都去掉空白与标点后逐字比对：引文必须落在所列镜 / 场（没列就在全集）的
  **某一个字段**里（一句台词、一条节拍、画面 / 美术 / 动作其中之一），所列的镜号 / 场号必须真的存在。
  对不上的条目保留，但标 ``evidence_verified: false``，``violated`` 降成 ``risk``，永远不进告警；编号对不上
  任何卡片条目的直接丢掉。卡片条目原文以卡为准，不信模型抄的。
- 审稿结果落在 ``<output_root>/manifests/genre_review.json``，带 ``spec_hash``（审的那份正文 + 题材卡 id 的
  稳定哈希）；正文改过之后哈希对不上 = 过时，过时的审稿不出告警，只给一句提示。

一次审稿 = 一次文本模型请求，不自动重试、不自动多审几次挑一个（跟起草同一个原则）。
"""

from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Mapping

from . import scriptgen
from .spec import load_spec, stable_json_hash

VERDICTS = ("ok", "risk", "violated")
SOURCES = ("core", "pitfall", "drift")
SOURCE_LABELS = {"core": "题材核心", "pitfall": "常见坑", "drift": "禁止漂移"}
VERDICT_LABELS = {"violated": "违反", "risk": "有风险", "ok": "没问题"}
REVIEW_FILENAME = "genre_review.json"
TASK_KIND = "genre-review"
# 去掉空白与标点后引文至少这么多个字——「爸爸」「S01」这种两三个字的「引文」在哪儿都能对上，不算证据
MIN_QUOTE_CHARS = 4
REMOTE_COST_NOTE = "发 1 次文本模型请求"
NO_GENRE_MESSAGE = "这一集没有选题材，先在概览里选一个"
STALE_NOTE = "剧本改过，这份审稿可能过时——重新审一次"
BAD_SHAPE_MESSAGE = "模型没有按审稿结构返回（缺 items 列表）——再试一次，或换文本模型"
# 引文里模型爱用的省略号：按它切成几段，每段都要在同一个字段里按顺序找到
_ELLIPSIS = re.compile(r"(?:…|\.\.\.|。。。)+")
# 引文开头带了字段名 / 说话人（「动作：…」「lin：…」）时，冒号后面那段也试一次
_LEAD_LABEL = re.compile(r"^[^：:]{1,12}[：:]\s*")


class GenreReviewError(ValueError):
    """审稿前置条件不满足（没选题材），或模型没按结构返回。"""


REVIEW_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "items"],
    "properties": {
        "summary": {"type": "string", "description": "一两句：这一集整体有没有守住这个题材"},
        "items": {
            "type": "array",
            "description": "每一条卡片条目回答一次",
            "items": {
                "type": "object",
                "required": ["source", "index", "item", "verdict", "shots", "quote", "why", "fix"],
                "properties": {
                    "source": {"type": "string", "enum": ["pitfall", "drift", "core"]},
                    "index": {"type": "integer", "description": "条目编号，每类各自从 1 数"},
                    "item": {"type": "string", "description": "条目原文"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "shots": {"type": "array", "items": {"type": "string"}, "description": "引文所在的镜号或场号"},
                    "quote": {"type": "string", "description": "剧本里的一段连续原文，逐字照抄"},
                    "why": {"type": "string", "description": "这段原文为什么踩了这条"},
                    "fix": {"type": "string", "description": "一句能落到剧本上的改法"},
                },
            },
        },
    },
}


# ---- 题材卡条目

def spec_card(spec: Mapping[str, Any]) -> dict[str, Any]:
    """spec.genre 对应的题材卡；没选题材（或卡已经不在卡库里）抛 ``GenreReviewError``。"""

    card = scriptgen.find_genre(spec.get("genre")) if isinstance(spec.get("genre"), str) else None
    if card is None:
        raise GenreReviewError(NO_GENRE_MESSAGE)
    return card


def card_items(card: Mapping[str, Any]) -> list[dict[str, Any]]:
    """卡里要审的条目，按「题材核心 → 常见坑 → 禁止漂移」排，每类各自从 1 编号。"""

    out: list[dict[str, Any]] = []
    core = str(card.get("core") or "").strip()
    if core:
        out.append({"source": "core", "index": 1, "item": core})
    for source, key in (("pitfall", "pitfalls"), ("drift", "disallowed_drift")):
        texts = [str(t).strip() for t in (card.get(key) or ()) if str(t).strip()]
        out.extend({"source": source, "index": i, "item": text} for i, text in enumerate(texts, 1))
    return out


# ---- 正文：进提示词的和核对引文用的是同一份字段

def load_screenplay(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    """项目文件旁边的 ``<id>.screenplay.json``（DESIGN §32）；没有或读不出来 = None。"""

    import json

    spec_dir = spec.get("spec_dir")
    if not spec_dir or not spec.get("id"):
        return None
    path = Path(str(spec_dir)) / f"{spec['id']}.screenplay.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("scenes"), list) else None


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _shot_fields(shot: Mapping[str, Any]) -> list[tuple[str, str]]:
    fields = [(label, _text(shot.get(key))) for key, label in (("shot", "画面"), ("art", "美术"), ("motion", "动作"))]
    for beat in shot.get("beats") or ():
        if isinstance(beat, Mapping):
            parts = [_text(beat.get(k)) for k in ("picture", "performance", "sound")]
            fields.extend(("节拍", p) for p in parts if p)
    for line in shot.get("lines") or ():
        if isinstance(line, Mapping):
            who = _text(line.get("who"))
            for key in ("zh", "en"):
                if _text(line.get(key)):
                    fields.append((f"台词（{who}）" if who else "台词", _text(line.get(key))))
    return [(label, value) for label, value in fields if value]


def _scene_fields(scene: Mapping[str, Any]) -> list[tuple[str, str]]:
    fields = [("场景", _text(scene.get("heading")))]
    raw_beats = scene.get("beats")
    beats = raw_beats.splitlines() if isinstance(raw_beats, str) else (raw_beats if isinstance(raw_beats, list) else [])
    fields.extend(("节拍", _text(b)) for b in beats)
    for line in scene.get("dialogue") or ():
        if isinstance(line, Mapping):
            who = _text(line.get("who"))
            for key in ("zh", "en"):
                if _text(line.get(key)):
                    fields.append((f"台词（{who}）" if who else "台词", _text(line.get(key))))
    fields.append(("这一场改变了", _text(scene.get("change"))))
    return [(label, value) for label, value in fields if value]


def _episode_fields(spec: Mapping[str, Any]) -> list[tuple[str, str]]:
    fields = [("剧名", _text(spec.get("title_zh")) or _text(spec.get("title_en"))), ("梗概", _text(spec.get("logline")))]
    contract = spec.get("episode_contract")
    if isinstance(contract, Mapping):
        for key, label in (("premise", "前提"), ("hook", "开场钩子"), ("cliffhanger", "集尾")):
            fields.append((label, _text(contract.get(key))))
        for key, label in (("must_pay_off", "本集必须兑现"), ("carry_over", "留给下一集")):
            fields.extend((label, _text(x)) for x in (contract.get(key) or ()))
    return [(label, value) for label, value in fields if value]


def episode_material(spec: Mapping[str, Any], screenplay: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """审稿看的全部正文：``{"episode": [...], "shots": {id: [...]}, "scenes": {id: [...]}}``，每项是 (字段名, 原文)。"""

    from .screenplay import scene_id

    shots = {str(s.get("id")): _shot_fields(s) for s in spec.get("shots", ()) if isinstance(s, Mapping) and s.get("id")}
    scenes: dict[str, list[tuple[str, str]]] = {}
    if isinstance(screenplay, Mapping):
        for i, scene in enumerate((s for s in screenplay.get("scenes") or () if isinstance(s, Mapping)), 1):
            scenes[scene_id(scene, i)] = _scene_fields(scene)
    return {"episode": _episode_fields(spec), "shots": shots, "scenes": scenes}


def material_rows(spec: Mapping[str, Any], screenplay: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """``episode_material`` 摊成能进稳定哈希的列表（元组变列表、字典变 [号, 字段] 对）。题材审稿和工艺审稿的哈希都用它。"""

    material = episode_material(spec, screenplay)
    return {
        "episode": [list(f) for f in material["episode"]],
        "shots": [[sid, [list(f) for f in fields]] for sid, fields in material["shots"].items()],
        "scenes": [[sid, [list(f) for f in fields]] for sid, fields in material["scenes"].items()],
    }


def review_hash(spec: Mapping[str, Any], card_id: str, screenplay: Mapping[str, Any] | None = None) -> str:
    """审的那份东西的稳定哈希：正文（剧名 / 契约 / 每镜 / 剧本场次）+ 题材卡 id。改了哪一样 = 审稿过时。"""

    return stable_json_hash({"card": str(card_id), "material": material_rows(spec, screenplay)})


# ---- 提示词

def build_review_prompt(
    spec: Mapping[str, Any], card: Mapping[str, Any], screenplay: Mapping[str, Any] | None = None
) -> tuple[str, str]:
    """返回 ``(system_prompt, user_prompt)``。卡片条目带编号，每镜 / 每场带号。"""

    system_prompt = (
        f"你是竖屏短剧的题材审稿人。只做一件事：拿题材卡「{card.get('name', '')}」逐条对照这一集的剧本，"
        "判断有没有守住这个题材的核心、有没有踩卡里写的常见坑、有没有漂成卡里禁止的样子。\n\n"
        "规则（每条都要守）：\n"
        "1. 只按给定的卡片条目判断：题材核心（core，这个题材的契约）、常见坑（pitfall）、禁止漂移（drift）。"
        "卡片之外的写作好坏（台词漂不漂亮、节奏快不快、画面美不美）一律不评。\n"
        "2. 每一条卡片条目回答一次：source 填 core / pitfall / drift，index 填条目前面的编号（每类各自从 1 数），"
        "item 抄条目原文。\n"
        "3. verdict 只能是 ok（没踩）、risk（有迹象，还不确定）、violated（明确踩了）。\n"
        "4. 判 risk 或 violated 必须给证据：quote 逐字抄剧本里的一段连续原文——一句台词、一条节拍或一个字段里的一段，"
        "不带镜号、字段名和说话人，不改字，不用省略号拼接；shots 列出这段原文所在的镜号或场号"
        "（照抄正文里「镜」「场」后面的号）。\n"
        "5. 找不到能引用的原文就判 ok。不要凭整体印象判问题，不要编造剧本里没写的情节、动机或事实。\n"
        "6. why 一两句说清这段原文怎么对上了这条条目；fix 给一句能落到剧本上的改法。判 ok 时 shots 给空数组，"
        "quote / why / fix 可以留空。\n"
        "7. summary 一两句：这一集整体有没有守住这个题材。\n\n"
        "你给的引文会被程序逐字核对：对不上正文、或不在你列的镜 / 场里的引文，不计入结果。"
    )
    items = card_items(card)
    user_parts: list[str] = []
    user_parts.append(f"题材卡：{card.get('name', '')}")
    groups = (("core", "题材核心（core）"), ("pitfall", "常见坑（pitfall）"), ("drift", "禁止漂移（drift）"))
    for source, heading in groups:
        rows = [f"{it['index']}. {it['item']}" for it in items if it["source"] == source]
        if rows:
            user_parts.append(heading + "：\n" + "\n".join(rows))
    material = episode_material(spec, screenplay)
    if material["episode"]:
        user_parts.append("全集：\n" + "\n".join(f"{label}：{value}" for label, value in material["episode"]))
    shot_blocks = []
    for sid, fields in material["shots"].items():
        shot_blocks.append(f"## 镜 {sid}\n" + "\n".join(f"{label}：{value}" for label, value in fields))
    user_parts.append("分镜正文（每镜带镜号）：\n\n" + "\n\n".join(shot_blocks))
    if material["scenes"]:
        scene_blocks = [f"## 场 {sid}\n" + "\n".join(f"{label}：{value}" for label, value in fields)
                        for sid, fields in material["scenes"].items()]
        user_parts.append("剧本场次（每场带场号；分镜是按它拆的）：\n\n" + "\n\n".join(scene_blocks))
    return system_prompt, "\n\n".join(user_parts)


# ---- 核对：审稿结果能不能信

def normalize_quote(text: str) -> str:
    """去掉空白、标点、符号，英文转小写——只比字。"""

    return "".join(
        ch.lower() for ch in unicodedata.normalize("NFKC", str(text or ""))
        if not unicodedata.category(ch).startswith(("P", "Z", "S", "C"))
    )


def _found_in(quote: str, fields: list[str]) -> bool:
    """引文（已去掉字段名前缀的候选之一）能在某一个字段里逐字找到；带省略号的按段、按顺序找。"""

    whole = normalize_quote(quote)
    if len(whole) < MIN_QUOTE_CHARS:
        return False
    normalized = [normalize_quote(f) for f in fields]
    if any(whole in f for f in normalized):
        return True
    pieces = [normalize_quote(p) for p in _ELLIPSIS.split(quote)]
    pieces = [p for p in pieces if p]
    if len(pieces) < 2:
        return False
    for field in normalized:
        pos = 0
        for piece in pieces:
            hit = field.find(piece, pos)
            if hit < 0:
                break
            pos = hit + len(piece)
        else:
            return True
    return False


def _quote_ok(quote: str, fields: list[str]) -> bool:
    candidates = [quote]
    stripped = _LEAD_LABEL.sub("", quote, count=1)
    if stripped != quote:
        candidates.append(stripped)
    return any(_found_in(c, fields) for c in candidates)


def _as_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _as_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[\s,，、;；]+", value)
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(str(v).strip() for v in value if isinstance(v, (str, int)) and str(v).strip()))


def verify_items(
    result: Any,
    spec: Mapping[str, Any],
    screenplay: Mapping[str, Any] | None,
    expected_items: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, int]]:
    """防编造的闸门，与清单是什么无关：``expected_items`` 是 ``[{source, index, item}]``（按要显示的顺序），
    题材审稿传题材卡的三类条目，工艺审稿传工艺清单的四组条目。返回 ``(清洗后的结果, {checked, verified, demoted, dropped, missing})``。

    - 编号对不上任何条目（或 source / verdict 不认识、同一条答了两次）的条目丢掉，计 ``dropped``；
    - risk / violated 逐条核对：引文去空白标点后 ≥ ``MIN_QUOTE_CHARS`` 字、落在所列镜 / 场（没列就在全集）的某一个
      字段里、所列镜号 / 场号都存在——都满足 ``evidence_verified: true``；否则 ``false``，violated 降为 risk，计 ``demoted``；
    - 条目原文一律换成清单里的原文；清单里有、模型没答的条目计 ``missing``。
    """

    expected = {(str(it["source"]), int(it["index"])): str(it["item"]) for it in expected_items}
    order = {key: n for n, key in enumerate(expected)}
    material = episode_material(spec, screenplay)
    shot_ids, scene_ids = set(material["shots"]), set(material["scenes"])
    all_fields = [v for _l, v in material["episode"]]
    all_fields += [v for fields in material["shots"].values() for _l, v in fields]
    all_fields += [v for fields in material["scenes"].values() for _l, v in fields]

    raw_items = result.get("items") if isinstance(result, Mapping) else None
    stats = {"checked": 0, "verified": 0, "demoted": 0, "dropped": 0, "missing": 0}
    kept: dict[tuple[str, int], dict[str, Any]] = {}
    for raw in raw_items if isinstance(raw_items, list) else ():
        if not isinstance(raw, Mapping):
            stats["dropped"] += 1
            continue
        source = str(raw.get("source") or "").strip().lower()
        index = _as_index(raw.get("index"))
        verdict = str(raw.get("verdict") or "").strip().lower()
        key = (source, index)
        if key not in expected or verdict not in VERDICTS or key in kept:
            stats["dropped"] += 1
            continue
        item: dict[str, Any] = {
            "source": source, "index": index, "item": expected[key], "verdict": verdict,
            "shots": _as_ids(raw.get("shots")),
            "quote": _text(raw.get("quote")), "why": _text(raw.get("why")), "fix": _text(raw.get("fix")),
        }
        if verdict in ("risk", "violated"):
            stats["checked"] += 1
            ids = item["shots"]
            unknown = [i for i in ids if i not in shot_ids and i not in scene_ids]
            if ids:
                fields = [v for i in ids for _l, v in [*material["shots"].get(i, []), *material["scenes"].get(i, [])]]
            else:
                fields = all_fields
            problem = ""
            if not item["quote"]:
                problem = "no_quote"
            elif unknown:
                problem = "unknown_ids"
            elif len(normalize_quote(item["quote"])) < MIN_QUOTE_CHARS:
                problem = "quote_too_short"
            elif not _quote_ok(item["quote"], fields):
                problem = "quote_not_found"
            if problem:
                item["evidence_verified"] = False
                item["evidence_problem"] = problem
                item["model_verdict"] = verdict
                item["verdict"] = "risk"
                if unknown:
                    item["unknown_ids"] = unknown
                stats["demoted"] += 1
            else:
                item["evidence_verified"] = True
                stats["verified"] += 1
        kept[key] = item
    stats["missing"] = sum(1 for key in expected if key not in kept)
    items = sorted(kept.values(), key=lambda it: order[(it["source"], it["index"])])
    summary = _text(result.get("summary")) if isinstance(result, Mapping) else ""
    return {"summary": summary, "items": items}, stats


def verify_review(
    result: Any,
    spec: Mapping[str, Any],
    screenplay: Mapping[str, Any] | None = None,
    *,
    card: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """题材审稿的核对：条目取 ``card_items(card)``（没给卡就按 spec.genre 找），其余全在 ``verify_items``。"""

    card = card if card is not None else spec_card(spec)
    return verify_items(result, spec, screenplay, card_items(card))


# ---- 落盘与读取

def review_path(spec: Mapping[str, Any]) -> Path:
    return Path(str(spec["output_root"])) / "manifests" / REVIEW_FILENAME


def load_review(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    import json

    path = review_path(spec)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_stale(spec: Mapping[str, Any], review: Mapping[str, Any], screenplay: Mapping[str, Any] | None = None) -> bool:
    """审完之后正文 / 剧本 / 题材改过了吗。"""

    if str(review.get("card_id") or "") != str(spec.get("genre") or ""):
        return True
    return str(review.get("spec_hash") or "") != review_hash(spec, str(review.get("card_id") or ""), screenplay)


def _model_name(backend: str) -> str:
    try:
        if backend == "settings":
            from genkit import settings as settings_mod

            return settings_mod.effective(settings_mod.load_settings().role("text"), "text").model or ""
        if backend == "openai_proxy":
            return scriptgen.llm_proxy_model() or ""
    except Exception:  # noqa: BLE001 - 记不下模型名不该让审稿失败
        return ""
    return ""


def run_review(episode_id: str, *, project_root: str | Path | None = None) -> dict[str, Any]:
    """一次文本模型请求 → 核对 → 写 ``manifests/genre_review.json``。返回写下的记录。"""

    from .state import atomic_write_json

    spec = load_spec(episode_id, project_root=project_root)
    card = spec_card(spec)
    screenplay = load_screenplay(spec)
    system_prompt, user_prompt = build_review_prompt(spec, card, screenplay)
    structured, cost_usd, usage_tokens, backend = scriptgen._dispatch(
        system_prompt, user_prompt, json_schema=REVIEW_JSON_SCHEMA,
    )
    if not isinstance(structured, Mapping) or not isinstance(structured.get("items"), list):
        raise GenreReviewError(BAD_SHAPE_MESSAGE)
    result, verification = verify_review(structured, spec, screenplay, card=card)
    record = {
        "card_id": str(card["id"]),
        "card_name": str(card["name"]),
        "model": _model_name(backend),
        "backend": backend,
        "created_at": time.time(),
        "spec_hash": review_hash(spec, str(card["id"]), screenplay),
        "had_screenplay": screenplay is not None,
        "usage_tokens": usage_tokens,
        "cost_usd": cost_usd,
        "result": result,
        "verification": verification,
    }
    path = review_path(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, record)
    return record


# ---- 告警：只有核对过的 risk / violated 进问题卡

def _clean(text: Any) -> str:
    """模型写的字进告警句之前：不许带「——」（问题卡靠它切影响 / 改法）、不许带「」（引号要能配对）、不换行。"""

    out = str(text or "").replace("——", "，").replace("--", "-").replace("「", "『").replace("」", "』")
    return re.sub(r"\s+", " ", out).strip()


def compose_warning(lead: str, item: Mapping[str, Any], spec: Mapping[str, Any],
                    screenplay: Mapping[str, Any] | None = None) -> str:
    """``lead``（「按 X 审稿（模型判断）：违反 Y「条目」」那段，已经 ``_clean`` 过）接上号 / 原文 / 理由 / 建议，
    再按引了几处定句头：只引一镜 → ``shot``；只引一场 → ``scene``；没引或引了多处 → ``全集：``，号列在原文前面。
    题材审稿和工艺审稿共用这一段拼法，英文规则也照同一个形状写。"""

    material = episode_material(spec, screenplay)
    ids = [str(i) for i in item.get("shots") or ()]
    body = lead
    head = "全集："
    if len(ids) == 1 and ids[0] in material["shots"]:
        head = f"shot {ids[0]}: "
    elif len(ids) == 1 and ids[0] in material["scenes"]:
        head = f"scene {ids[0]}: "
    elif ids:
        # 号列在原文前面：整句永远以「」」收尾——i18n.tr 会把以「）」结尾的句子拆成「人话（原文）」来翻
        body += f"（见 {'、'.join(ids)}）"
    body += f"，原文「{_clean(item.get('quote'))}」"
    if _clean(item.get("why")):
        body += f"，理由「{_clean(item.get('why'))}」"
    if _clean(item.get("fix")):
        body += f"——建议「{_clean(item.get('fix'))}」"
    return head + body


def review_warning(item: Mapping[str, Any], card_name: str, spec: Mapping[str, Any],
                   screenplay: Mapping[str, Any] | None = None) -> str:
    """一条核对过的条目 → 「shot S03: 按题材卡「X」审稿（模型判断）：违反常见坑「…」，原文「…」，理由「…」——建议「…」」。"""

    lead = (f"按题材卡「{_clean(card_name)}」审稿（模型判断）：{VERDICT_LABELS[str(item['verdict'])]}"
            f"{SOURCE_LABELS[str(item['source'])]}「{_clean(item.get('item'))}」")
    return compose_warning(lead, item, spec, screenplay)


def review_problems(
    spec: Mapping[str, Any],
    review: Mapping[str, Any] | None = None,
    screenplay: Mapping[str, Any] | None = None,
) -> list[str]:
    """审稿里核对过的 risk / violated 条目 → 告警字符串（问题卡「题材」）。没审过、过时、没选题材 → 空列表。"""

    if not spec.get("genre"):
        return []
    if screenplay is None:
        screenplay = load_screenplay(spec)
    review = review if review is not None else load_review(spec)
    if not review or is_stale(spec, review, screenplay):
        return []
    items = ((review.get("result") or {}).get("items") or ()) if isinstance(review.get("result"), Mapping) else ()
    name = str(review.get("card_name") or review.get("card_id") or "")
    order = {"violated": 0, "risk": 1}
    picked = [it for it in items if isinstance(it, Mapping) and it.get("evidence_verified") is True
              and it.get("verdict") in order and it.get("source") in SOURCE_LABELS]
    picked.sort(key=lambda it: order[str(it["verdict"])])
    return [review_warning(it, name, spec, screenplay) for it in picked]


def review_state(spec: Mapping[str, Any]) -> dict[str, Any]:
    """接口用：``{review, stale, note, warnings}``。"""

    screenplay = load_screenplay(spec)
    review = load_review(spec)
    stale = bool(review) and is_stale(spec, review, screenplay)
    return {
        "review": review,
        "stale": stale,
        "note": STALE_NOTE if stale else "",
        "warnings": [] if stale or not review else review_problems(spec, review, screenplay),
    }


__all__ = [
    "GenreReviewError", "REVIEW_JSON_SCHEMA", "TASK_KIND", "MIN_QUOTE_CHARS", "REMOTE_COST_NOTE", "NO_GENRE_MESSAGE",
    "STALE_NOTE", "card_items", "spec_card", "load_screenplay", "episode_material", "material_rows", "review_hash",
    "build_review_prompt", "normalize_quote", "verify_items", "verify_review", "review_path", "load_review", "is_stale",
    "run_review", "compose_warning", "review_warning", "review_problems", "review_state",
]
