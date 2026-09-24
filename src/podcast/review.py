"""真人感审稿：跟短剧的按题材审稿（``storyvideo.genre_review``，DESIGN §49）/ 剧本工艺审稿（``storyvideo.craft_review``）
同一套「模型逐条判 + 程序逐字核对引文」的闸门，清单换成播客的三组（口语 / 对谈动态 / 内容，
``skills/ai-podcast/scripts/humanlike_checklist.json``）。

为什么在 ``lint.py`` 之外再加一道模型审：静态检查只能数词（书面语词表、填充词占比、换人次数），
「两个人观点一致到分不清谁在说」「回答只是敷衍没有结论」「数据不在素材里」是语义判断，词表判不了。
清单条目由 ``podcast/craft.py`` 的八条约束和 ``lint.py`` 的判据改写而来，每条 ``basis`` 各自注明来源与置信度，
全部是经验规则，也还没有拿真模型审过真稿。

跟工艺审稿的差别只有这几处，其余（核对闸门、告警拼法）都从 ``genre_review`` / ``craft_review`` 复用：

- 正文是播客稿：每个环节的种类 / 标题 / 目标 / 节拍 / 听众问题 / 每句台词（「说话人名：文本」），环节号就是 segment id；
  主播表与选题 brief 只当上下文进提示词，不能被引用；
- 核对时把环节当「场」传给 ``genre_review.verify_items``：引文必须逐字落在所列环节的某一句台词、相邻的两句台词
  （清单里「机械轮流」「各说各话」「分不清谁在说」几条要引两句才说得清；接话太短不够 ``MIN_QUOTE_CHARS`` 字也靠它）、
  某一条节拍或某一条听众问题里，标题与目标不算证据；
- 结果落在 ``<output_root>/manifests/humanlike_review.json``，``spec_hash`` = 稿子正文（进提示词的上下文 + 环节）+
  清单 id 的稳定哈希，稿子改过 = 过时；
- 告警句头是「按真人感清单审稿（模型判断）」，落在一个环节挂 ``segment G03: ``，多环节 / 无环节挂「全期：」，
  问题卡分类仍是「播客」（``webui/problems.py`` 把这个句头列在播客关键词最前面）。

一次审稿 = 一次文本模型请求，不自动重试、不自动多审几次挑一个。``dispatch`` 可注入（测试用假模型）。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from genkit.paths import code_root as _code_root
from storyvideo import craft_review as CR
from storyvideo import genre_review as GR
from storyvideo.spec import stable_json_hash

from . import spec as S

CHECKLIST_PATH = _code_root() / "skills" / "ai-podcast" / "scripts" / "humanlike_checklist.json"
GROUP_LABELS = {"speech": "口语", "dynamics": "对谈动态", "substance": "内容"}
VERDICT_LABELS = GR.VERDICT_LABELS
REVIEW_FILENAME = "humanlike_review.json"
TASK_KIND = "pc-review"
REMOTE_COST_NOTE = GR.REMOTE_COST_NOTE
BAD_SHAPE_MESSAGE = GR.BAD_SHAPE_MESSAGE
STALE_NOTE = "稿子改过，这份审稿可能过时——重新审一次"
MISSING_CHECKLIST_MESSAGE = "真人感清单文件读不出来——重装一次程序，或检查 skills/ai-podcast/scripts/humanlike_checklist.json"
NO_LINES_MESSAGE = "稿子里还没有台词——先起草或手写几句，再审"


class HumanlikeReviewError(ValueError):
    """清单读不出来，或模型没按结构返回。"""


REVIEW_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "items"],
    "properties": {
        "summary": {"type": "string", "description": "一两句：这期稿子整体像不像真人在聊，最要紧的一处问题是什么"},
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
                    "shots": {"type": "array", "items": {"type": "string"}, "description": "引文所在的环节号"},
                    "quote": {"type": "string", "description": "稿子里的一句台词、相邻的两句台词或一条节拍，逐字照抄"},
                    "why": {"type": "string", "description": "这段原文为什么踩了这条"},
                    "fix": {"type": "string", "description": "一句能落到稿子上的改法"},
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
    """随包的真人感清单；读不出来抛 ``HumanlikeReviewError``（清单是代码资源，缺了就是包坏了，不是用户的项目坏了）。"""

    data = _read_checklist(str(CHECKLIST_PATH))
    if data is None:
        raise HumanlikeReviewError(MISSING_CHECKLIST_MESSAGE)
    return data


def checklist_info() -> dict[str, str] | None:
    """接口用：``{id, name}``；清单读不出来 = None。"""

    try:
        data = load_checklist()
    except HumanlikeReviewError:
        return None
    return {"id": str(data["id"]), "name": str(data.get("name") or data["id"])}


def checklist_items(checklist: Mapping[str, Any]) -> list[dict[str, Any]]:
    """清单里要审的条目：形状跟工艺清单一样（groups → items → index / text / basis），直接用工艺审稿的读法。"""

    return CR.checklist_items(checklist)


# ---- 正文：进提示词的和核对引文用的是同一份字段

def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _names(spec: Mapping[str, Any]) -> dict[str, str]:
    return {str(h["id"]): _text(h.get("name")) or str(h["id"]) for h in spec.get("hosts") or () if isinstance(h, Mapping) and h.get("id")}


def _segments(spec: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [s for s in spec.get("segments") or () if isinstance(s, Mapping)]


def _segment_fields(segment: Mapping[str, Any], names: Mapping[str, str]) -> list[tuple[str, str]]:
    """一个环节进提示词的字段：标题 / 目标 / 节拍 / 听众问题 / 台词（说话人名：文本）。"""

    fields: list[tuple[str, str]] = [("标题", _text(segment.get("title"))), ("目标", _text(segment.get("goal")))]
    fields.extend(("节拍", _text(b)) for b in segment.get("beats") or ())
    for q in segment.get("questions") or ():
        if isinstance(q, Mapping) and _text(q.get("text")):
            who = _text(q.get("from")) or "听众"
            fields.append((f"听众问题（{who}）", _text(q.get("text"))))
    for line in segment.get("lines") or ():
        if isinstance(line, Mapping) and _text(line.get("text")):
            who = str(line.get("who") or "")
            fields.append((f"台词（{names.get(who, who)}）", _text(line.get("text"))))
    return [(label, value) for label, value in fields if value]


def _context_fields(spec: Mapping[str, Any]) -> list[tuple[str, str]]:
    """全期的上下文（不能被引用）：节目名 / 形式 / 主播表 / 选题 brief。"""

    fmt = S.FORMATS.get(str(spec.get("format") or "duo"), S.FORMATS["duo"])
    fields: list[tuple[str, str]] = [("节目", _text(spec.get("title"))), ("形式", f"{fmt['name']}（{fmt['note']}）")]
    for host in spec.get("hosts") or ():
        if not isinstance(host, Mapping) or not host.get("id"):
            continue
        role = {"host": "主播", "cohost": "搭档", "guest": "嘉宾"}.get(str(host.get("role") or ""), str(host.get("role") or ""))
        parts = [f"{_text(host.get('name')) or host['id']}（{role}）"]
        if _text(host.get("persona")):
            parts.append(f"人设：{_text(host.get('persona'))}")
        if _text(host.get("stance")):
            parts.append(f"立场：{_text(host.get('stance'))}")
        fields.append(("主播", "；".join(parts)))
    brief = spec.get("brief") or {}
    if isinstance(brief, Mapping):
        for key, label in (("topic", "选题"), ("angle", "切入角度"), ("audience", "听众"), ("tone", "语气")):
            fields.append((label, _text(brief.get(key))))
        if brief.get("takeaways"):
            fields.append(("听完带走", "；".join(_text(x) for x in brief["takeaways"] if _text(x))))
        fields.append(("素材（台词里的数据只能从这里来）", _text(brief.get("materials"))))
        if brief.get("avoid"):
            fields.append(("不要", "；".join(_text(x) for x in brief["avoid"] if _text(x))))
    return [(label, value) for label, value in fields if value]


def material(spec: Mapping[str, Any]) -> dict[str, Any]:
    """审稿看的全部正文：``{"context": [...], "segments": {id: {"kind": 种类名, "fields": [...]}}}``，每项是 (字段名, 原文)。"""

    names = _names(spec)
    segments: dict[str, dict[str, Any]] = {}
    for i, segment in enumerate(_segments(spec), 1):
        sid = str(segment.get("id") or S.default_segment_id(i))
        kind = str(segment.get("kind") or "topic")
        segments[sid] = {"kind": S.SEGMENT_KIND_LABELS.get(kind, kind), "fields": _segment_fields(segment, names)}
    return {"context": _context_fields(spec), "segments": segments}


def material_rows(spec: Mapping[str, Any]) -> dict[str, Any]:
    """``material`` 摊成能进稳定哈希的列表（元组变列表、字典变 [号, 种类, 字段] 组）。"""

    m = material(spec)
    return {
        "context": [list(f) for f in m["context"]],
        "segments": [[sid, seg["kind"], [list(f) for f in seg["fields"]]] for sid, seg in m["segments"].items()],
    }


def review_hash(spec: Mapping[str, Any], checklist_id: str) -> str:
    """审的那份东西的稳定哈希：稿子正文（上下文 + 每个环节）+ 清单 id。改了哪一样 = 审稿过时。"""

    return stable_json_hash({"checklist": str(checklist_id), "material": material_rows(spec)})


def _adjacent_pairs(dialogue: list[dict[str, str]]) -> list[dict[str, str]]:
    """相邻两句台词拼成的「合句」，只给核对用：清单里「机械轮流」「各说各话」「分不清谁在说」几条要引两句才说得清，
    「嗯 / 对」这类接话又不够 ``MIN_QUOTE_CHARS`` 字。每对给两种写法——不带名（``前一句后一句``）和带名
    （``甲：前一句\\n乙：后一句``）——模型直接拼、加空格、换行或带说话人名抄都能对上（核对前会去掉空白与标点），
    不相邻或颠倒顺序的两句对不上。"""

    pairs: list[dict[str, str]] = []
    for a, b in zip(dialogue, dialogue[1:]):
        pairs.append({"who": "", "zh": a["zh"] + b["zh"]})
        pairs.append({"who": "", "zh": f"{a['who']}：{a['zh']}\n{b['who']}：{b['zh']}"})
    return pairs


def _as_screenplay(spec: Mapping[str, Any]) -> dict[str, Any]:
    """把环节当「场」交给 ``genre_review.verify_items`` / ``compose_warning``：只放台词（每一句 + 相邻两句的合句）、
    节拍与听众问题（``_scene_fields`` 读 beats / dialogue），标题与目标故意不放——它们不是稿子里说出口的话，不算证据。"""

    names = _names(spec)
    scenes = []
    for i, segment in enumerate(_segments(spec), 1):
        beats = [_text(b) for b in segment.get("beats") or () if _text(b)]
        beats += [_text(q.get("text")) for q in segment.get("questions") or () if isinstance(q, Mapping) and _text(q.get("text"))]
        dialogue = [{"who": names.get(str(l.get("who") or ""), str(l.get("who") or "")), "zh": _text(l.get("text"))}
                    for l in segment.get("lines") or () if isinstance(l, Mapping) and _text(l.get("text"))]
        scenes.append({"id": str(segment.get("id") or S.default_segment_id(i)), "beats": beats,
                       "dialogue": dialogue + _adjacent_pairs(dialogue)})
    return {"scenes": scenes}


# 传给 genre_review 的「集」：没有镜、没有剧名 / 契约——所有可引用的字段都在 ``_as_screenplay`` 的场里
_NO_EPISODE: dict[str, Any] = {}


# ---- 提示词

def build_review_prompt(spec: Mapping[str, Any], checklist: Mapping[str, Any]) -> tuple[str, str]:
    """返回 ``(system_prompt, user_prompt)``。清单条目按三组编号，每个环节带环节号。"""

    system_prompt = (
        f"你是播客稿子的真人感审稿人。只做一件事：拿「{checklist.get('name', '')}」逐条对照这一期的稿子，"
        "判断口语、对谈动态、内容三组判据有没有守住——也就是听起来像不像真人在聊，而不是 AI 直出的稿子。\n\n"
        "规则（每条都要守）：\n"
        "1. 只按给定的清单条目判断：口语（speech）、对谈动态（dynamics）、内容（substance）。"
        "清单之外的好坏（选题有没有意思、观点对不对、主播讨不讨喜）一律不评。\n"
        "2. 每一条清单条目回答一次：source 填 speech / dynamics / substance，index 填条目前面的编号（每组各自从 1 数），"
        "item 抄条目原文。\n"
        "3. verdict 只能是 ok（没踩）、risk（有迹象，还不确定）、violated（明确踩了）。条目里写明了判 risk / violated 的条件，"
        "按条目写的判，不要自己加严或放宽；条目写了「不判」的形式或环节，判 ok。\n"
        "4. 判 risk 或 violated 必须给证据：quote 逐字抄稿子里的一句台词、相邻的两句台词（按原顺序连着抄，中间换行或直接接上都行）、"
        "一条节拍或一条听众问题——不带环节号、字段名和说话人名，不改字，不用省略号把不相邻的话拼在一起；"
        f"引文去掉标点至少 {GR.MIN_QUOTE_CHARS} 个字，要引的那句太短（「嗯」「对」「下期见」），就把它和相邻的一句连起来引，并在 why 里说明。"
        "shots 列出这段原文所在的环节号（照抄正文里「环节」后面的号）。"
        "要通读全期才能判的条目（没有分歧、一人独大、分不清谁在说），引文仍要落到一个环节的一句或相邻两句里。\n"
        "5. 找不到能引用的原文就判 ok。不要凭整体印象判问题，不要编造稿子里没写的话。"
        "节目名、主播表、选题 brief 只是上下文，不能当引文。\n"
        "6. why 一两句说清这段原文怎么对上了这条条目；fix 给一句能落到稿子上的改法。判 ok 时 shots 给空数组，"
        "quote / why / fix 可以留空。\n"
        "7. summary 一两句：这期稿子整体像不像真人在聊，最要紧的一处问题是什么。\n\n"
        "你给的引文会被程序逐字核对：对不上正文、或不在你列的环节里的引文，不计入结果。"
    )
    items = checklist_items(checklist)
    user_parts: list[str] = [f"真人感清单：{checklist.get('name', '')}"]
    for group in checklist.get("groups") or ():
        if not isinstance(group, Mapping) or not group.get("key"):
            continue
        key = str(group["key"])
        rows = [f"{it['index']}. {it['item']}" for it in items if it["source"] == key]
        if rows:
            user_parts.append(f"{group.get('name') or GROUP_LABELS.get(key, key)}（{key}）：\n" + "\n".join(rows))
    m = material(spec)
    if m["context"]:
        user_parts.append("全期（上下文，不能引用）：\n" + "\n".join(f"{label}：{value}" for label, value in m["context"]))
    blocks = [f"## 环节 {sid}｜{seg['kind']}\n" + "\n".join(f"{label}：{value}" for label, value in seg["fields"])
              for sid, seg in m["segments"].items()]
    user_parts.append("稿子正文（每个环节带环节号）：\n\n" + "\n\n".join(blocks))
    return system_prompt, "\n\n".join(user_parts)


# ---- 核对：条目取清单的三组，闸门本身在 genre_review.verify_items

def verify_review(
    result: Any,
    spec: Mapping[str, Any],
    *,
    checklist: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """真人感审稿的核对：条目取 ``checklist_items``（没给就读随包的清单），环节当「场」，其余全在 ``genre_review.verify_items``。"""

    checklist = checklist if checklist is not None else load_checklist()
    cleaned, stats = GR.verify_items(result, _NO_EPISODE, _as_screenplay(spec), checklist_items(checklist))
    _demote_boundary_fragments(cleaned, stats, spec)
    return cleaned, stats


def _single_fields(spec: Mapping[str, Any]) -> dict[str, tuple[list[str], list[str]]]:
    """每个环节里能单独当证据的字段（台词 / 节拍 / 听众问题）与单独的台词列表：``{环节号: (全部单字段, 台词)}``。"""

    out: dict[str, tuple[list[str], list[str]]] = {}
    for i, segment in enumerate(_segments(spec), 1):
        sid = str(segment.get("id") or S.default_segment_id(i))
        lines = [_text(l.get("text")) for l in segment.get("lines") or () if isinstance(l, Mapping) and _text(l.get("text"))]
        singles = list(lines)
        singles += [_text(b) for b in segment.get("beats") or () if _text(b)]
        singles += [_text(q.get("text")) for q in segment.get("questions") or () if isinstance(q, Mapping) and _text(q.get("text"))]
        out[sid] = (singles, lines)
    return out


def _demote_boundary_fragments(result: dict[str, Any], stats: dict[str, int], spec: Mapping[str, Any]) -> None:
    """合句是两句的裸拼接、核对又是子串匹配，所以只跨句界的碎片（前一句的尾巴 + 后一句的开头，去标点刚好凑够字数）也能
    对上——它既不是稿子里的一句，也不是「按原顺序连着抄的两句」。这里补一道：引文没有落在任何一个单句 / 节拍 / 听众问题
    里的，必须整段包含合句里的某一句（去空白标点后），否则按「引文找不到」降级，跟闸门里的降级同一套字段。"""

    fields = _single_fields(spec)
    for item in result.get("items") or ():
        if not isinstance(item, Mapping) or item.get("evidence_verified") is not True:
            continue
        ids = [str(i) for i in (item.get("shots") or ())] or list(fields)
        singles = [f for sid in ids for f in fields.get(sid, ([], []))[0]]
        quote = str(item.get("quote") or "")
        if GR._quote_ok(quote, singles):
            continue
        normalized = GR.normalize_quote(quote)
        whole_lines = [GR.normalize_quote(l) for sid in ids for l in fields.get(sid, ([], []))[1]]
        if any(line and line in normalized for line in whole_lines):
            continue
        item["evidence_verified"] = False
        item["evidence_problem"] = "quote_not_found"
        item["model_verdict"] = item["verdict"]
        item["verdict"] = "risk"
        stats["verified"] = max(0, int(stats.get("verified", 0)) - 1)
        stats["demoted"] = int(stats.get("demoted", 0)) + 1


# ---- 落盘与读取

def review_path(spec: Mapping[str, Any]) -> Path:
    return S.output_root(spec) / "manifests" / REVIEW_FILENAME


def load_review(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    path = review_path(spec)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_stale(spec: Mapping[str, Any], review: Mapping[str, Any]) -> bool:
    """审完之后稿子改过了吗，或清单换了版本（id 不同）。清单读不出来也算过时——没法核对。"""

    info = checklist_info()
    if info is None or str(review.get("checklist_id") or "") != info["id"]:
        return True
    return str(review.get("spec_hash") or "") != review_hash(spec, info["id"])


Dispatch = Callable[..., tuple[dict[str, Any], float | None, int | None, str]]


def run_review(spec: Mapping[str, Any], *, dispatch: Dispatch | None = None) -> dict[str, Any]:
    """一次文本模型请求 → 核对 → 写 ``manifests/humanlike_review.json``。返回写下的记录。"""

    from storyvideo.state import atomic_write_json

    if dispatch is None:
        from storyvideo.scriptgen import _dispatch as dispatch  # type: ignore[assignment]
    checklist = load_checklist()
    system_prompt, user_prompt = build_review_prompt(spec, checklist)
    structured, cost_usd, usage_tokens, backend = dispatch(system_prompt, user_prompt, json_schema=REVIEW_JSON_SCHEMA)
    if not isinstance(structured, Mapping) or not isinstance(structured.get("items"), list):
        raise HumanlikeReviewError(BAD_SHAPE_MESSAGE)
    result, verification = verify_review(structured, spec, checklist=checklist)
    record = {
        "checklist_id": str(checklist["id"]),
        "checklist_name": str(checklist.get("name") or checklist["id"]),
        "model": GR._model_name(backend),
        "backend": backend,
        "created_at": time.time(),
        "spec_hash": review_hash(spec, str(checklist["id"])),
        "usage_tokens": usage_tokens,
        "cost_usd": cost_usd,
        "result": result,
        "verification": verification,
    }
    path = review_path(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, record)
    return record


# ---- 告警：只有核对过的 risk / violated 进问题卡（分类「播客」）

_SCENE_HEAD = re.compile(r"^scene (\S+?): ")


def review_warning(item: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    """一条核对过的条目 → 「segment G03: 按真人感清单审稿（模型判断）：违反口语「…」，原文「…」，理由「…」——建议「…」」。
    拼法在 ``genre_review.compose_warning``（环节当「场」传进去），只把它给的句头 ``scene`` / 「全集」换成
    ``segment`` / 「全期」——多环节 / 无环节挂「全期：」。条目一段太长，句子里只带第一句（卡片上仍显示全文）。"""

    lead = (f"按真人感清单审稿（模型判断）：{VERDICT_LABELS[str(item['verdict'])]}"
            f"{GROUP_LABELS[str(item['source'])]}「{GR._clean(CR.item_label(str(item.get('item') or '')))}」")
    text = GR.compose_warning(lead, item, _NO_EPISODE, _as_screenplay(spec))
    if text.startswith("全集："):
        return "全期：" + text[len("全集："):]
    return _SCENE_HEAD.sub(r"segment \1: ", text, count=1)


def review_problems(spec: Mapping[str, Any], review: Mapping[str, Any] | None = None) -> list[str]:
    """审稿里核对过的 risk / violated 条目 → 告警字符串（问题卡「播客」）。没审过、过时 → 空列表。"""

    review = review if review is not None else load_review(spec)
    if not review or is_stale(spec, review):
        return []
    items = ((review.get("result") or {}).get("items") or ()) if isinstance(review.get("result"), Mapping) else ()
    order = {"violated": 0, "risk": 1}
    picked = [it for it in items if isinstance(it, Mapping) and it.get("evidence_verified") is True
              and it.get("verdict") in order and it.get("source") in GROUP_LABELS]
    picked.sort(key=lambda it: order[str(it["verdict"])])
    return [review_warning(it, spec) for it in picked]


def review_state(spec: Mapping[str, Any]) -> dict[str, Any]:
    """接口用：``{review, stale, note, warnings}``。清单读不出来时 ``note`` 说的是清单的事，不是「稿子改过」——
    稿子没改，照「重新审一次」去点只会得到 400。"""

    review = load_review(spec)
    info = checklist_info()
    stale = bool(review) and is_stale(spec, review)
    return {
        "review": review,
        "stale": stale,
        "note": MISSING_CHECKLIST_MESSAGE if info is None else (STALE_NOTE if stale else ""),
        "warnings": [] if stale or not review else review_problems(spec, review),
    }


__all__ = [
    "HumanlikeReviewError", "CHECKLIST_PATH", "GROUP_LABELS", "REVIEW_JSON_SCHEMA", "TASK_KIND", "REMOTE_COST_NOTE", "STALE_NOTE",
    "BAD_SHAPE_MESSAGE", "MISSING_CHECKLIST_MESSAGE", "NO_LINES_MESSAGE", "load_checklist", "checklist_info", "checklist_items",
    "material", "material_rows", "review_hash", "build_review_prompt", "verify_review", "review_path", "load_review", "is_stale",
    "run_review", "review_warning", "review_problems", "review_state",
]
