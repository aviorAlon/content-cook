"""模型审稿（契约 docs/scriptlab-spec.md §5）：一次文本模型请求，清单按 ``target`` 选，引文照样由程序逐字核对。

清单不新写，复用已有的两张：

* ``target=podcast`` → ``skills/ai-podcast/scripts/humanlike_checklist.json``（口语 / 对谈动态 / 内容，
  读法在 ``podcast.review``）。
* 其余 → ``skills/ai-short-film/scripts/craft_checklist.json``（张力 / 情绪可见 / 节奏 / 合规，读法在
  ``storyvideo.craft_review``）。

核对闸门整套借用：把**段当「场」**，可引用的字段只有每一句台词和相邻两句拼成的合句（``podcast.review._adjacent_pairs``），
段标题 / 目标 / 备注只当上下文进提示词、不算证据；「碎片不算证据」那道补充检查也是它的
（``podcast.review._demote_boundary_fragments``）。做法是把工作台的那一版拼成 ``podcast.review`` 认的播客稿形状，
再原样调它的 ``verify_review``——两张清单条目的形状一样（groups → items → index / text / basis），同一道闸门都吃得下。

审的是指定的那一版（不给就是 ``current``，还没采用就是最新一版——第一版自动采用、之后的版本不顶替（契约 §3），
但整体保存时 ``current`` 可能被清空，这时最新的那一版也要能审）。记录写 ``output/script/<ID>/manifests/review_<版本 id>.json``：
换一版 = 各自一份；``spec_hash`` 只算**那一版的台词 + 清单 id**，那一版的台词改了 = 过时（改别的版本、改标题
都不影响已经审过的那一版）。

告警句头是「按真人感清单审稿（模型判断）」/「按工艺清单审稿（模型判断）」，跟两个模块现有的句式一致，
只把位置句头换成工作台的 ``block B01: `` / ``全篇：``，问题卡分类不变（播客 / 工艺）。

一次审稿 = 一次文本模型请求，不自动重试、不自动多审几次挑一个。``dispatch`` 可注入（测试用假模型）。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from podcast import review as PR
from storyvideo import craft_review as CR
from storyvideo import genre_review as GR
from storyvideo.spec import stable_json_hash

from . import spec as S

TASK_KIND = "sc-review"
REMOTE_COST_NOTE = GR.REMOTE_COST_NOTE
BAD_SHAPE_MESSAGE = GR.BAD_SHAPE_MESSAGE
VERDICT_LABELS = GR.VERDICT_LABELS
STALE_NOTE = "这一版的台词改过，这份审稿可能过时——重新审一次"
NO_LINES_MESSAGE = "这一版还没有台词——先生成或手写几句，再审"
# 一版也没有才报这句：没采用时 ``spec.pick_version`` 会回落到最新一版，刚生成完的第一版照样能审
NO_VERSION_MESSAGE = "还没有任何版本——先在「试」里生成一版，或者手写一版"
# 两张清单各自的句头、组名与「清单读不出来」的话，都按 target 取（两边现成的，不另写）
_SHEETS: dict[str, dict[str, Any]] = {
    "humanlike": {"lead": "按真人感清单审稿（模型判断）", "groups": PR.GROUP_LABELS,
                  "missing": PR.MISSING_CHECKLIST_MESSAGE, "load": PR.load_checklist, "info": PR.checklist_info},
    "craft": {"lead": "按工艺清单审稿（模型判断）", "groups": CR.GROUP_LABELS,
              "missing": CR.MISSING_CHECKLIST_MESSAGE, "load": CR.load_checklist, "info": CR.checklist_info},
}


class ScriptReviewError(ValueError):
    """清单读不出来，或模型没按结构返回。"""


REVIEW_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "items"],
    "properties": {
        "summary": {"type": "string", "description": "一两句：这一版台词整体怎么样，最要紧的一处问题是什么"},
        "items": {
            "type": "array",
            "description": "每一条清单条目回答一次",
            "items": {
                "type": "object",
                "required": ["source", "index", "item", "verdict", "shots", "quote", "why", "fix"],
                "properties": {
                    "source": {"type": "string", "description": "条目所在的组（清单里写的 key）"},
                    "index": {"type": "integer", "description": "条目编号，每组各自从 1 数"},
                    "item": {"type": "string", "description": "条目原文"},
                    "verdict": {"type": "string", "enum": list(GR.VERDICTS)},
                    "shots": {"type": "array", "items": {"type": "string"}, "description": "引文所在的段号"},
                    "quote": {"type": "string", "description": "稿子里的一句台词或相邻的两句台词，逐字照抄"},
                    "why": {"type": "string", "description": "这段原文为什么踩了这条"},
                    "fix": {"type": "string", "description": "一句能落到稿子上的改法"},
                },
            },
        },
    },
}


# ---- 清单：按 target 选


def sheet_key(spec: Mapping[str, Any]) -> str:
    """``target=podcast`` 用真人感清单，其余（两种短剧 + 通用）用工艺清单。"""

    return "humanlike" if str(spec.get("target") or "") == "podcast" else "craft"


def load_checklist(spec: Mapping[str, Any]) -> dict[str, Any]:
    """随包的那张清单；读不出来抛 ``ScriptReviewError``（清单是代码资源，缺了就是包坏了）。"""

    sheet = _SHEETS[sheet_key(spec)]
    try:
        return sheet["load"]()
    except (PR.HumanlikeReviewError, CR.CraftReviewError) as exc:
        raise ScriptReviewError(str(exc)) from exc


def checklist_info(spec: Mapping[str, Any]) -> dict[str, str] | None:
    """接口用：``{id, name}``；清单读不出来 = None。"""

    return _SHEETS[sheet_key(spec)]["info"]()


def checklist_items(checklist: Mapping[str, Any]) -> list[dict[str, Any]]:
    """两张清单形状一样，直接用工艺审稿的读法（``podcast.review`` 也是这么干的）。"""

    return CR.checklist_items(checklist)


def group_labels(spec: Mapping[str, Any]) -> dict[str, str]:
    return dict(_SHEETS[sheet_key(spec)]["groups"])


def missing_checklist_message(spec: Mapping[str, Any]) -> str:
    return str(_SHEETS[sheet_key(spec)]["missing"])


# ---- 正文：进提示词的和核对引文用的是同一份台词


def _blocks(spec: Mapping[str, Any], version: str | None) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    picked = S.pick_version(spec, version)
    if picked is None:
        return []
    by_id = {str(b.get("id")): list(b.get("lines") or []) for b in picked.get("blocks") or () if isinstance(b, Mapping)}
    return [(dict(b), by_id.get(str(b["id"]), [])) for b in spec.get("blocks") or ()
            if isinstance(b, Mapping) and b.get("id")]


def as_podcast(spec: Mapping[str, Any], version: str | None = None) -> dict[str, Any]:
    """拼成 ``podcast.review`` 认的播客稿：段当环节，**只放台词**（节拍与听众问题留空）——段标题 / 目标 / 备注
    不是稿子里说出口的话，不算证据，所以不进这份（它们照样进提示词当上下文）。"""

    hosts = [{"id": s["id"], "name": s.get("name") or s["id"], "role": "host"}
             for s in spec.get("speakers") or () if isinstance(s, Mapping)]
    segments = [{"id": str(block["id"]), "kind": "topic", "title": "", "goal": "", "beats": [], "questions": [],
                 "lines": lines} for block, lines in _blocks(spec, version)]
    return {"format": "duo", "language": str(spec.get("language") or "zh"), "title": "", "brief": {},
            "hosts": hosts, "segments": segments}


def material(spec: Mapping[str, Any], version: str | None = None) -> dict[str, Any]:
    """审稿看的全部正文：``{"context": [...], "blocks": {段号: {"title": …, "fields": [...]}}}``，每项是 (字段名, 原文)。
    ``context`` 是不能被引用的上下文（稿子名 / 目标形态 / 角色表 / brief）。"""

    names = S.speaker_names(spec)
    target = S.TARGETS.get(str(spec.get("target") or "generic"), S.TARGETS["generic"])
    context: list[tuple[str, str]] = [("稿子", str(spec.get("title") or "")), ("目标形态", str(target["name"]))]
    for speaker in spec.get("speakers") or ():
        if not isinstance(speaker, Mapping) or not speaker.get("id"):
            continue
        parts = [str(speaker.get("name") or speaker["id"])]
        if speaker.get("persona"):
            parts.append(f"人设：{speaker['persona']}")
        if speaker.get("stance"):
            parts.append(f"立场：{speaker['stance']}")
        context.append(("角色", "；".join(parts)))
    brief = spec.get("brief") or {}
    if isinstance(brief, Mapping):
        for key, label in (("topic", "选题"), ("angle", "切入角度"), ("audience", "受众"), ("tone", "语气")):
            context.append((label, str(brief.get(key) or "")))
        context.append(("素材（台词里的事实和数字只能从这里来）", str(brief.get("materials") or "")))
        if brief.get("avoid"):
            context.append(("不要", "；".join(str(x) for x in brief["avoid"] if str(x).strip())))
    blocks: dict[str, dict[str, Any]] = {}
    for block, lines in _blocks(spec, version):
        fields: list[tuple[str, str]] = [("目标", str(block.get("goal") or "")), ("备注", str(block.get("note") or ""))]
        for line in lines:
            who = str(line.get("who") or "")
            text = str(line.get("text") or "").strip()
            if text:
                fields.append((f"台词（{names.get(who, who)}）", text))
        blocks[str(block["id"])] = {"title": str(block.get("title") or ""),
                                    "fields": [(l, v) for l, v in fields if v]}
    return {"context": [(l, v) for l, v in context if v], "blocks": blocks}


def review_hash(spec: Mapping[str, Any], checklist_id: str, version: str | None = None) -> str:
    """审的那份东西的稳定哈希：**那一版的台词** + 清单 id。台词改了 = 审稿过时；改标题、改别的版本都不算。"""

    rows = [[str(block["id"]), str(line.get("who") or ""), str(line.get("text") or ""), str(line.get("kind") or "talk")]
            for block, lines in _blocks(spec, version) for line in lines]
    return stable_json_hash({"checklist": str(checklist_id), "lines": rows})


def has_lines(spec: Mapping[str, Any], version: str | None = None) -> bool:
    return any(lines for _block, lines in _blocks(spec, version))


# ---- 提示词


def build_review_prompt(spec: Mapping[str, Any], checklist: Mapping[str, Any], version: str | None = None) -> tuple[str, str]:
    """返回 ``(system_prompt, user_prompt)``。清单条目按组编号，每段带段号。"""

    labels = group_labels(spec)
    target = S.TARGETS.get(str(spec.get("target") or "generic"), S.TARGETS["generic"])
    groups_zh = " / ".join(labels.values())
    groups_en = " / ".join(labels)
    system_prompt = (
        f"你是台词审稿人。只做一件事：拿「{checklist.get('name', '')}」逐条对照这一版台词，"
        f"判断{groups_zh}这几组判据有没有守住。这一版稿子要拿去做{target['name']}。\n\n"
        "规则（每条都要守）：\n"
        f"1. 只按给定的清单条目判断（{groups_en}）。清单之外的好坏（选题有没有意思、观点对不对、人物讨不讨喜）一律不评。\n"
        f"2. 每一条清单条目回答一次：source 填 {groups_en} 之一，index 填条目前面的编号（每组各自从 1 数），item 抄条目原文。\n"
        "3. verdict 只能是 ok（没踩）、risk（有迹象，还不确定）、violated（明确踩了）。条目里写明了判 risk / violated 的条件，"
        "按条目写的判，不要自己加严或放宽。\n"
        "4. 判 risk 或 violated 必须给证据：quote 逐字抄稿子里的一句台词、或相邻的两句台词（按原顺序连着抄，中间换行或直接接上都行）——"
        "不带段号、字段名和说话人名，不改字，不用省略号把不相邻的话拼在一起；"
        f"引文去掉标点至少 {GR.MIN_QUOTE_CHARS} 个字，要引的那句太短（「嗯」「对」），就把它和相邻的一句连起来引，并在 why 里说明。"
        "shots 列出这段原文所在的段号（照抄正文里「段」后面的号）。"
        "要通读全篇才能判的条目，引文仍要落到一个段的一句或相邻两句里。\n"
        "5. 找不到能引用的原文就判 ok。不要凭整体印象判问题，不要编造稿子里没写的话。"
        "稿子名、目标形态、角色表、brief、段的标题 / 目标 / 备注都只是上下文，不能当引文。\n"
        "6. why 一两句说清这段原文怎么对上了这条条目；fix 给一句能落到稿子上的改法。判 ok 时 shots 给空数组，"
        "quote / why / fix 可以留空。\n"
        "7. summary 一两句：这一版台词整体怎么样，最要紧的一处问题是什么。\n\n"
        "你给的引文会被程序逐字核对：对不上正文、或不在你列的段里的引文，不计入结果。"
    )
    items = checklist_items(checklist)
    user_parts: list[str] = [f"清单：{checklist.get('name', '')}"]
    for group in checklist.get("groups") or ():
        if not isinstance(group, Mapping) or not group.get("key"):
            continue
        key = str(group["key"])
        rows = [f"{it['index']}. {it['item']}" for it in items if it["source"] == key]
        if rows:
            user_parts.append(f"{group.get('name') or labels.get(key, key)}（{key}）：\n" + "\n".join(rows))
    m = material(spec, version)
    if m["context"]:
        user_parts.append("全篇（上下文，不能引用）：\n" + "\n".join(f"{label}：{value}" for label, value in m["context"]))
    blocks = [f"## 段 {bid}｜{block['title']}\n" + "\n".join(f"{label}：{value}" for label, value in block["fields"])
              for bid, block in m["blocks"].items()]
    user_parts.append("台词正文（每段带段号）：\n\n" + "\n\n".join(blocks))
    return system_prompt, "\n\n".join(user_parts)


# ---- 核对：整套借 podcast.review（段当「场」、相邻两句的合句、碎片降级）


def verify_review(result: Any, spec: Mapping[str, Any], *, checklist: Mapping[str, Any] | None = None,
                  version: str | None = None) -> tuple[dict[str, Any], dict[str, int]]:
    """闸门本身在 ``genre_review.verify_items``，合句与碎片降级在 ``podcast.review.verify_review``；
    这里只负责把工作台的那一版拼成它认的形状。"""

    checklist = checklist if checklist is not None else load_checklist(spec)
    return PR.verify_review(result, as_podcast(spec, version), checklist=checklist)


# ---- 落盘与读取


def review_filename(version_id: str) -> str:
    return f"review_{version_id}.json"


def review_path(spec: Mapping[str, Any], version: str | None = None) -> Path:
    picked = S.pick_version(spec, version)
    vid = str(picked["id"]) if picked else str(version or spec.get("current") or "none")
    return S.output_root(spec) / "manifests" / review_filename(vid)


def load_review(spec: Mapping[str, Any], version: str | None = None) -> dict[str, Any] | None:
    path = review_path(spec, version)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_stale(spec: Mapping[str, Any], review: Mapping[str, Any], version: str | None = None) -> bool:
    """审完之后这一版的台词改过了吗，或清单换了版本（id 不同）。清单读不出来也算过时——没法核对。"""

    info = checklist_info(spec)
    if info is None or str(review.get("checklist_id") or "") != info["id"]:
        return True
    return str(review.get("spec_hash") or "") != review_hash(spec, info["id"], version)


Dispatch = Callable[..., tuple[dict[str, Any], float | None, int | None, str]]


def run_review(spec: Mapping[str, Any], *, version: str | None = None, dispatch: Dispatch | None = None) -> dict[str, Any]:
    """一次文本模型请求 → 核对 → 写 ``manifests/review_<版本 id>.json``。返回写下的记录。"""

    from storyvideo.state import atomic_write_json

    if dispatch is None:
        from storyvideo.scriptgen import _dispatch as dispatch  # type: ignore[assignment]
    picked = S.pick_version(spec, version)
    if picked is None:
        raise ScriptReviewError(NO_VERSION_MESSAGE)
    if not has_lines(spec, version):
        raise ScriptReviewError(NO_LINES_MESSAGE)
    checklist = load_checklist(spec)
    system_prompt, user_prompt = build_review_prompt(spec, checklist, version)
    structured, cost_usd, usage_tokens, backend = dispatch(system_prompt, user_prompt, json_schema=REVIEW_JSON_SCHEMA)
    if not isinstance(structured, Mapping) or not isinstance(structured.get("items"), list):
        raise ScriptReviewError(BAD_SHAPE_MESSAGE)
    result, verification = verify_review(structured, spec, checklist=checklist, version=version)
    record = {
        "version": str(picked["id"]),
        "checklist_id": str(checklist["id"]),
        "checklist_name": str(checklist.get("name") or checklist["id"]),
        "model": GR._model_name(backend),
        "backend": backend,
        "created_at": time.time(),
        "spec_hash": review_hash(spec, str(checklist["id"]), version),
        "usage_tokens": usage_tokens,
        "cost_usd": cost_usd,
        "result": result,
        "verification": verification,
    }
    path = review_path(spec, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, record)
    return record


# ---- 告警：只有核对过的 risk / violated 进问题卡（分类不变：播客 / 工艺）

_SCENE_HEAD = re.compile(r"^scene (\S+?): ")


def review_warning(item: Mapping[str, Any], spec: Mapping[str, Any], version: str | None = None) -> str:
    """一条核对过的条目 → 「block B01: 按工艺清单审稿（模型判断）：违反张力「…」，原文「…」，理由「…」——建议「…」」。
    拼法在 ``genre_review.compose_warning``（段当「场」传进去），只把它给的句头 ``scene`` / 「全集」换成
    ``block`` / 「全篇」——多段 / 无段挂「全篇：」。"""

    sheet = _SHEETS[sheet_key(spec)]
    labels = sheet["groups"]
    lead = (f"{sheet['lead']}：{VERDICT_LABELS[str(item['verdict'])]}"
            f"{labels[str(item['source'])]}「{GR._clean(CR.item_label(str(item.get('item') or '')))}」")
    text = GR.compose_warning(lead, item, {}, PR._as_screenplay(as_podcast(spec, version)))
    if text.startswith("全集："):
        return "全篇：" + text[len("全集："):]
    return _SCENE_HEAD.sub(r"block \1: ", text, count=1)


def review_problems(spec: Mapping[str, Any], review: Mapping[str, Any] | None = None,
                    version: str | None = None) -> list[str]:
    """审稿里核对过的 risk / violated 条目 → 告警字符串。没审过、过时 → 空列表。"""

    review = review if review is not None else load_review(spec, version)
    if not review or is_stale(spec, review, version):
        return []
    items = ((review.get("result") or {}).get("items") or ()) if isinstance(review.get("result"), Mapping) else ()
    labels = group_labels(spec)
    order = {"violated": 0, "risk": 1}
    picked = [it for it in items if isinstance(it, Mapping) and it.get("evidence_verified") is True
              and it.get("verdict") in order and it.get("source") in labels]
    picked.sort(key=lambda it: order[str(it["verdict"])])
    return [review_warning(it, spec, version) for it in picked]


def review_state(spec: Mapping[str, Any], version: str | None = None) -> dict[str, Any]:
    """接口用：``{version, review, stale, note, warnings}``。清单读不出来时 ``note`` 说的是清单的事，不是「台词改过」。"""

    current = S.pick_version(spec, version)
    review = load_review(spec, version) if current else None
    info = checklist_info(spec)
    stale = bool(review) and is_stale(spec, review, version)
    return {
        "version": str(current["id"]) if current else "",
        "review": review,
        "stale": stale,
        "note": missing_checklist_message(spec) if info is None else (STALE_NOTE if stale else ""),
        "warnings": [] if stale or not review else review_problems(spec, review, version),
    }


__all__ = [
    "TASK_KIND", "REMOTE_COST_NOTE", "BAD_SHAPE_MESSAGE", "STALE_NOTE", "NO_LINES_MESSAGE", "NO_VERSION_MESSAGE",
    "REVIEW_JSON_SCHEMA", "ScriptReviewError", "sheet_key", "load_checklist", "checklist_info", "checklist_items",
    "group_labels", "missing_checklist_message", "as_podcast", "material", "review_hash", "has_lines",
    "build_review_prompt", "verify_review", "review_filename", "review_path", "load_review", "is_stale",
    "run_review", "review_warning", "review_problems", "review_state",
]
