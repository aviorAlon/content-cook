"""剧本工艺审稿：跟按题材审稿（``genre_review``，DESIGN §49）同一套「模型逐条判 + 程序逐字核对引文」的闸门，
但清单与题材无关——张力 / 情绪可见 / 节奏 / 合规四组判据（``skills/ai-short-film/scripts/craft_checklist.json``），
任何项目都能审，不用先选题材。

条目按编剧常识自拟（张力看有没有人在争、情绪看有没有落在身体上、节奏看有没有新信息、合规看红线，docs/script-craft-gap.md），
每条 ``basis`` 各自注明依据与置信度，全部是经验规则、不是测量结论，也还没有拿真模型审过真剧本。为什么用模型审、不做词表
lint，理由跟题材审稿一样：「巧合是不是替人物解了围」「翻盘之前有没有线索」「钩子是不是老一招」是语义判断，
字符串匹配判不了。

跟题材审稿的差别只有这几处，其余（正文字段、核对闸门、过时判定、告警拼法）都从 ``genre_review`` 复用：

- 条目来自清单的四组（``source`` = 组的 key），不是题材卡的三类；条目本身写明了判 risk / violated 的条件；
- 合规组的 ``violated`` 是一票否决级别的风险，提示词要求 ``why`` 写清是哪一类红线；
- 结果落在 ``<output_root>/manifests/craft_review.json``，``spec_hash`` = 正文 + 清单 id 的稳定哈希；
- 告警句头是「按工艺清单审稿（模型判断）」，问题卡分类「工艺」；条目一段太长，句子里只带它的第一句（卡片上仍显示全文）。

一次审稿 = 一次文本模型请求，不自动重试、不自动多审几次挑一个。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

from genkit.paths import code_root as _code_root

from . import genre_review as GR
from . import scriptgen
from .spec import load_spec, stable_json_hash

CHECKLIST_PATH = _code_root() / "skills" / "ai-short-film" / "scripts" / "craft_checklist.json"
GROUP_LABELS = {"tension": "张力", "emotion": "情绪可见", "rhythm": "节奏", "compliance": "合规"}
VERDICT_LABELS = GR.VERDICT_LABELS
REVIEW_FILENAME = "craft_review.json"
TASK_KIND = "craft-review"
REMOTE_COST_NOTE = GR.REMOTE_COST_NOTE
STALE_NOTE = GR.STALE_NOTE
BAD_SHAPE_MESSAGE = GR.BAD_SHAPE_MESSAGE
MISSING_CHECKLIST_MESSAGE = "工艺清单文件读不出来——重装一次程序，或检查 skills/ai-short-film/scripts/craft_checklist.json"
# 条目是一整段判据，进告警句只取到第一个句号 / 分号，再按逗号截到这么多字（卡片上仍显示全文）
ITEM_LABEL_MAX = 60


class CraftReviewError(ValueError):
    """清单读不出来，或模型没按结构返回。"""


REVIEW_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "items"],
    "properties": {
        "summary": {"type": "string", "description": "一两句：这一集在张力 / 情绪 / 节奏上最要紧的问题，合规有没有红线"},
        "items": {
            "type": "array",
            "description": "每一条清单条目回答一次",
            "items": {
                "type": "object",
                "required": ["source", "index", "item", "verdict", "shots", "quote", "why", "fix"],
                "properties": {
                    "source": {"type": "string", "enum": list(GROUP_LABELS)},
                    "index": {"type": "integer", "description": "条目编号，每组各自从 1 数"},
                    "item": {"type": "string", "description": "条目原文"},
                    "verdict": {"type": "string", "enum": list(GR.VERDICTS)},
                    "shots": {"type": "array", "items": {"type": "string"}, "description": "引文所在的镜号或场号"},
                    "quote": {"type": "string", "description": "剧本里的一段连续原文，逐字照抄"},
                    "why": {"type": "string", "description": "这段原文为什么踩了这条；合规组要写清是哪一类红线"},
                    "fix": {"type": "string", "description": "一句能落到剧本上的改法"},
                },
            },
        },
    },
}


# ---- 清单

# 只缓存读成功的清单：读失败（更新程序时文件被占用、包损坏后修好）不能记一辈子，下次再读
_CHECKLIST_CACHE: dict[str, dict[str, Any]] = {}


def _read_checklist(path: str) -> dict[str, Any] | None:
    cached = _CHECKLIST_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("id") or not isinstance(data.get("groups"), list):
        return None
    _CHECKLIST_CACHE[path] = data
    return data


def load_checklist() -> dict[str, Any]:
    """随包的工艺清单；读不出来抛 ``CraftReviewError``（清单是代码资源，缺了就是包坏了，不是用户的项目坏了）。"""

    data = _read_checklist(str(CHECKLIST_PATH))
    if data is None:
        raise CraftReviewError(MISSING_CHECKLIST_MESSAGE)
    return data


def checklist_info() -> dict[str, str] | None:
    """接口用：``{id, name}``；清单读不出来 = None。"""

    try:
        data = load_checklist()
    except CraftReviewError:
        return None
    return {"id": str(data["id"]), "name": str(data.get("name") or data["id"])}


def checklist_items(checklist: Mapping[str, Any]) -> list[dict[str, Any]]:
    """清单里要审的条目，按组的顺序排，``source`` 是组的 key、``index`` 每组各自从 1 编（以文件里写的 index 为准，缺了按位置补）。"""

    out: list[dict[str, Any]] = []
    for group in checklist.get("groups") or ():
        if not isinstance(group, Mapping) or not group.get("key"):
            continue
        key = str(group["key"])
        for pos, raw in enumerate((it for it in (group.get("items") or ()) if isinstance(it, Mapping)), 1):
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            index = raw.get("index") if isinstance(raw.get("index"), int) and not isinstance(raw.get("index"), bool) else pos
            out.append({"source": key, "index": int(index), "item": text, "group": str(group.get("name") or GROUP_LABELS.get(key, key)),
                        "basis": str(raw.get("basis") or "")})
    return out


def item_label(text: str) -> str:
    """条目进告警句的短形：取到第一个句号 / 分号；还超过 ``ITEM_LABEL_MAX`` 字就截到上限，退到最近的逗号 / 顿号
    （不退过一半以上），不留半个括号，末尾加「…」。"""

    head = re.split(r"[。；;]", str(text or ""), maxsplit=1)[0].strip()
    if len(head) <= ITEM_LABEL_MAX:
        return head
    cut = head[:ITEM_LABEL_MAX]
    pos = max(cut.rfind("，"), cut.rfind("、"))
    if pos >= ITEM_LABEL_MAX * 0.6:
        cut = cut[:pos]
    if cut.count("（") > cut.count("）"):
        cut = cut[:cut.rfind("（")]
    return cut.rstrip("，、：: （") + "…"


def review_hash(spec: Mapping[str, Any], checklist_id: str, screenplay: Mapping[str, Any] | None = None) -> str:
    """审的那份东西的稳定哈希：正文（剧名 / 契约 / 每镜 / 剧本场次）+ 清单 id。改了哪一样 = 审稿过时。"""

    return stable_json_hash({"checklist": str(checklist_id), "material": GR.material_rows(spec, screenplay)})


# ---- 提示词

def build_review_prompt(
    spec: Mapping[str, Any], checklist: Mapping[str, Any], screenplay: Mapping[str, Any] | None = None
) -> tuple[str, str]:
    """返回 ``(system_prompt, user_prompt)``。清单条目按四组编号，每镜 / 每场带号。"""

    system_prompt = (
        f"你是竖屏短剧的剧本工艺审稿人。只做一件事：拿「{checklist.get('name', '')}」逐条对照这一集的剧本，"
        "判断张力、情绪可见、节奏、合规四组判据有没有守住。清单与题材无关，只看写法。\n\n"
        "规则（每条都要守）：\n"
        "1. 只按给定的清单条目判断：张力（tension）、情绪可见（emotion）、节奏（rhythm）、合规（compliance）。"
        "清单之外的写作好坏（题材守没守住、画面美不美、人设讨不讨喜）一律不评。\n"
        "2. 每一条清单条目回答一次：source 填 tension / emotion / rhythm / compliance，index 填条目前面的编号（每组各自从 1 数），"
        "item 抄条目原文。\n"
        "3. verdict 只能是 ok（没踩）、risk（有迹象，还不确定）、violated（明确踩了）。条目里写明了判 risk / violated 的条件，"
        "按条目写的判，不要自己加严或放宽。\n"
        "4. 判 risk 或 violated 必须给证据：quote 逐字抄剧本里的一段连续原文——一句台词、一条节拍或一个字段里的一段，"
        "不带镜号、字段名和说话人，不改字，不用省略号拼接；shots 列出这段原文所在的镜号或场号"
        "（照抄正文里「镜」「场」后面的号）。要通读全集才能判的条目（被动主角、匀速、悬念同型），引文仍要落到一镜或一场。\n"
        "5. 找不到能引用的原文就判 ok。不要凭整体印象判问题，不要编造剧本里没写的情节、动机或事实。\n"
        "6. why 一两句说清这段原文怎么对上了这条条目；fix 给一句能落到剧本上的改法。判 ok 时 shots 给空数组，"
        "quote / why / fix 可以留空。\n"
        "7. 合规（compliance）组的 violated 意味着一票否决级别的风险：不看上下文是否「有必要」，why 要写清是哪一类红线"
        "（现实政治 / 真实人物 / 歧视 / 封建迷信 / 色情低俗 / 极端暴力 / 美化违法 / 未成年人 / 真实名称背负面情节）；"
        "拿不准的判 risk，同样写清是哪一类。\n"
        "8. summary 一两句：这一集在张力、情绪、节奏上最要紧的一处问题，以及合规有没有红线。\n\n"
        "你给的引文会被程序逐字核对：对不上正文、或不在你列的镜 / 场里的引文，不计入结果。"
    )
    items = checklist_items(checklist)
    user_parts: list[str] = [f"工艺清单：{checklist.get('name', '')}"]
    for group in checklist.get("groups") or ():
        if not isinstance(group, Mapping) or not group.get("key"):
            continue
        key = str(group["key"])
        rows = [f"{it['index']}. {it['item']}" for it in items if it["source"] == key]
        if rows:
            user_parts.append(f"{group.get('name') or GROUP_LABELS.get(key, key)}（{key}）：\n" + "\n".join(rows))
    material = GR.episode_material(spec, screenplay)
    if material["episode"]:
        user_parts.append("全集：\n" + "\n".join(f"{label}：{value}" for label, value in material["episode"]))
    shot_blocks = [f"## 镜 {sid}\n" + "\n".join(f"{label}：{value}" for label, value in fields)
                   for sid, fields in material["shots"].items()]
    user_parts.append("分镜正文（每镜带镜号）：\n\n" + "\n\n".join(shot_blocks))
    if material["scenes"]:
        scene_blocks = [f"## 场 {sid}\n" + "\n".join(f"{label}：{value}" for label, value in fields)
                        for sid, fields in material["scenes"].items()]
        user_parts.append("剧本场次（每场带场号；分镜是按它拆的）：\n\n" + "\n\n".join(scene_blocks))
    return system_prompt, "\n\n".join(user_parts)


# ---- 核对：条目取清单的四组，闸门本身在 genre_review.verify_items

def verify_review(
    result: Any,
    spec: Mapping[str, Any],
    screenplay: Mapping[str, Any] | None = None,
    *,
    checklist: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """工艺审稿的核对：条目取 ``checklist_items``（没给就读随包的清单），其余全在 ``genre_review.verify_items``。"""

    checklist = checklist if checklist is not None else load_checklist()
    return GR.verify_items(result, spec, screenplay, checklist_items(checklist))


# ---- 落盘与读取

def review_path(spec: Mapping[str, Any]) -> Path:
    return Path(str(spec["output_root"])) / "manifests" / REVIEW_FILENAME


def load_review(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    path = review_path(spec)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_stale(spec: Mapping[str, Any], review: Mapping[str, Any], screenplay: Mapping[str, Any] | None = None) -> bool:
    """审完之后正文 / 剧本改过了吗，或清单换了版本（id 不同）。清单读不出来也算过时——没法核对。"""

    info = checklist_info()
    if info is None or str(review.get("checklist_id") or "") != info["id"]:
        return True
    return str(review.get("spec_hash") or "") != review_hash(spec, info["id"], screenplay)


def run_review(episode_id: str, *, project_root: str | Path | None = None) -> dict[str, Any]:
    """一次文本模型请求 → 核对 → 写 ``manifests/craft_review.json``。返回写下的记录。"""

    from .state import atomic_write_json

    spec = load_spec(episode_id, project_root=project_root)
    checklist = load_checklist()
    screenplay = GR.load_screenplay(spec)
    system_prompt, user_prompt = build_review_prompt(spec, checklist, screenplay)
    structured, cost_usd, usage_tokens, backend = scriptgen._dispatch(
        system_prompt, user_prompt, json_schema=REVIEW_JSON_SCHEMA,
    )
    if not isinstance(structured, Mapping) or not isinstance(structured.get("items"), list):
        raise CraftReviewError(BAD_SHAPE_MESSAGE)
    result, verification = verify_review(structured, spec, screenplay, checklist=checklist)
    record = {
        "checklist_id": str(checklist["id"]),
        "checklist_name": str(checklist.get("name") or checklist["id"]),
        "model": GR._model_name(backend),
        "backend": backend,
        "created_at": time.time(),
        "spec_hash": review_hash(spec, str(checklist["id"]), screenplay),
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


# ---- 告警：只有核对过的 risk / violated 进问题卡（分类「工艺」）

def review_warning(item: Mapping[str, Any], spec: Mapping[str, Any], screenplay: Mapping[str, Any] | None = None) -> str:
    """一条核对过的条目 → 「shot S03: 按工艺清单审稿（模型判断）：违反张力「…」，原文「…」，理由「…」——建议「…」」。
    多镜 / 无镜挂「全集：」，只引一场挂 ``scene``（拼法在 ``genre_review.compose_warning``）。"""

    lead = (f"按工艺清单审稿（模型判断）：{VERDICT_LABELS[str(item['verdict'])]}"
            f"{GROUP_LABELS[str(item['source'])]}「{GR._clean(item_label(str(item.get('item') or '')))}」")
    return GR.compose_warning(lead, item, spec, screenplay)


def review_problems(
    spec: Mapping[str, Any],
    review: Mapping[str, Any] | None = None,
    screenplay: Mapping[str, Any] | None = None,
) -> list[str]:
    """审稿里核对过的 risk / violated 条目 → 告警字符串（问题卡「工艺」）。没审过、过时 → 空列表。"""

    if screenplay is None:
        screenplay = GR.load_screenplay(spec)
    review = review if review is not None else load_review(spec)
    if not review or is_stale(spec, review, screenplay):
        return []
    items = ((review.get("result") or {}).get("items") or ()) if isinstance(review.get("result"), Mapping) else ()
    order = {"violated": 0, "risk": 1}
    picked = [it for it in items if isinstance(it, Mapping) and it.get("evidence_verified") is True
              and it.get("verdict") in order and it.get("source") in GROUP_LABELS]
    picked.sort(key=lambda it: order[str(it["verdict"])])
    return [review_warning(it, spec, screenplay) for it in picked]


def review_state(spec: Mapping[str, Any]) -> dict[str, Any]:
    """接口用：``{review, stale, note, warnings}``。清单读不出来时 ``note`` 说的是清单的事，不是「剧本改过」——
    剧本没改，照「重新审一次」去点只会得到 400。"""

    screenplay = GR.load_screenplay(spec)
    review = load_review(spec)
    info = checklist_info()
    stale = bool(review) and is_stale(spec, review, screenplay)
    return {
        "review": review,
        "stale": stale,
        "note": MISSING_CHECKLIST_MESSAGE if info is None else (STALE_NOTE if stale else ""),
        "warnings": [] if stale or not review else review_problems(spec, review, screenplay),
    }


__all__ = [
    "CraftReviewError", "CHECKLIST_PATH", "GROUP_LABELS", "REVIEW_JSON_SCHEMA", "TASK_KIND", "REMOTE_COST_NOTE", "STALE_NOTE",
    "BAD_SHAPE_MESSAGE", "MISSING_CHECKLIST_MESSAGE", "load_checklist", "checklist_info", "checklist_items", "item_label",
    "review_hash", "build_review_prompt", "verify_review", "review_path", "load_review", "is_stale", "run_review",
    "review_warning", "review_problems", "review_state",
]
