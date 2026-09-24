"""起草与重写（契约 docs/scriptlab-spec.md §3）：一次文本模型请求 → 追加一版台词，从不覆盖旧版。

写法不另起一套，按 ``target`` 从已有的经验规则里选：

* ``podcast``：``podcast.craft.CRAFT_RULES`` 那八条（口语不是书面语、有人不同意、具体压倒观点、每个人是一个人、
  问答要像回答、不自我介绍、禁止清单、时长可数）+ 轮流那条能数的要求（``PODCAST_TURN_RULE``，同 ``podcast/craft.py``
  里校准出来的那一条，段换成工作台的段）。
* ``drama_screenplay`` / ``drama_shot``：``DRAMA_RULES`` 六条短剧台词写法——话里留一层没说出口的、嘴上和身上各演一半、
  只写能拍能录的、单句不超过 ``storyvideo.screenplay.LINE_MAX_CHARS`` 字、开口的人不超过 ``SPEAKING_CAST_MAX`` 个、
  不让人物给知情的人讲他们都知道的事。
  **出处**：剧本工艺清单的台词与情绪判据（按编剧常识自拟，docs/script-craft-gap.md）+ 本仓库剧本层的两条静态检查；
  都是经验规则，不是测量结论，**置信度低**。其中「一句不超过 N 字」数的是汉字，**只放进中文稿的提示词**
  （``DRAMA_RULES_EN`` 去掉了它）——英文一句该多少词，仓库里没有这个判据，检查那边 ``lint.check_drama_lines``
  对英文稿也同样不判，两处口径一致。
* ``generic``：两者的交集（口语、有分歧、具体、不用台词交代前情），直接从上面两套里挑，不另写。

一律带上：角色表（人设 / 立场）、brief（素材里没有的事实数字不许编）、段落骨架（标题 / 目标 / 备注）、语言、
``who`` 只能用 ``speakers`` 的 id。回填照 ``podcast.craft.apply_draft`` 的规矩：名字换成 id、不认识的说话人报错、
``cue`` 不在清单里就清空、模型漏掉的段保留上一版的内容。

新版本追加在 ``versions`` 末尾，``current`` **不动**——界面上让人比过再挑（契约 §3）。因为 ``current`` 不动，
「只重写这几段」找垫底的那一版时不能只看 ``current``：``from_version`` → ``current`` → 最新一版（``spec.pick_version``
的回落），一版也没有就直接拒绝（``NO_BASE_MESSAGE``），不静默产出空段。``dispatch`` 可注入（测试用假模型）。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from podcast import craft as PC
from storyvideo.screenplay import LINE_MAX_CHARS, SPEAKING_CAST_MAX

from . import spec as S

TASK_KIND = "sc-draft"
REMOTE_COST_NOTE = PC.REMOTE_COST_NOTE
BAD_SHAPE_MESSAGE = "模型没有按稿子结构返回（缺 blocks 列表）——再试一次，或换文本模型"
# 「只重写这几段」没有可以基于的那一版就直接拒绝：既不能把没选中的段悄悄清空，也不能让模型对着空白重写
NO_BASE_MESSAGE = "还没有可以基于的版本——先生成或手写一版，再只重写其中几段"
# 没有段落骨架就没有「写哪几段」：拦在发请求之前，不然会静默追加一个 blocks 为空的废版本（角色表空着由
# spec.require_speakers 拦，两条一起在 build_prompt 里出）
NO_BLOCKS_MESSAGE = "还没有段落骨架——先加至少一段（标题 + 目标），再生成"

# 播客那条「能数的」轮流要求（同 podcast/craft.py 的系统提示词：只写「禁止机械轮流」时
# 模型仍是每一句都换人），这里把「环节」换成工作台的「段」。
PODCAST_TURN_RULE = (
    "真人对谈不是一问一答轮流：每个段里至少有两处同一个人连着说两三句（补充、举例、改口），"
    "另一个人的接话可以短到一两个字（「嗯」「对」「啊？」）也单独成一句；整段每一句都换人算机械轮流，检查会打回。"
)

# 短剧台词写法（出处见模块文档字符串；置信度低）。字数与人数直接引常量，别在句子里写死数字。
DRAMA_RULES: tuple[str, ...] = (
    "话里留一层没说出口的：人物嘴上那句多半在绕开他最在意的事，真意留给观众从动作和前后文里听出来；别让一句台词把心事交代干净。",
    "嘴上和身上各演一半：嘴上说「挺好的」的人，筷子半天没动；情绪放进手上的动作、停顿和答非所问，不写成形容词，也不让人物自己报出来。",
    "只写能拍能录的：内心活动换成一个动作或一句别的话，旁白式的解说删掉。",
    f"一句不超过 {LINE_MAX_CHARS} 字：一口气说不完的句子拆成两句，或者把说明性的那半句改成一个动作。",
    f"开口的人不超过 {SPEAKING_CAST_MAX} 个：竖屏一集观众只记得住三张脸；只说一两句的人改成不开口的在场者。",
    "别让人物给知情的人讲他们都知道的事：规矩、来历、谁跟谁是什么关系、以前出过什么事，交给一个动作、一件道具或一样看得见的东西带出来。",
)

# 英文稿的那套：去掉「一句不超过 N 字」——那条数的是汉字，英文没有对应的词数阈值（仓库里没有这个判据，
# 不自己造，契约 §10），检查那边对英文稿也同样不判这条
DRAMA_RULES_EN: tuple[str, ...] = tuple(r for r in DRAMA_RULES if not r.startswith("一句不超过"))

# generic = 两套的交集：播客那三条（口语 / 有分歧 / 具体）+ 短剧最后那条（不用台词交代前情）。
# 按句首取，不抄正文——播客那三条改了字，这里跟着变；取不到（上游改了句首）会被 tests/test_script_craft.py 逮住。
_GENERIC_HEADS = ("口语不是书面语", "有人不同意", "具体压倒观点")
GENERIC_RULES: tuple[str, ...] = tuple(r for r in PC.CRAFT_RULES if r.startswith(_GENERIC_HEADS)) + (DRAMA_RULES[-1],)


class ScriptLabError(ValueError):
    """模型没按结构返回，或返回的说话人不在角色表里。"""


def rules_for(target: str, language: str = "zh") -> tuple[str, ...]:
    """这个目标形态用哪几条写法（界面上的「写法约束」也读它）。

    短剧那套的「一句不超过 N 字」只进中文稿（``language="en"`` 换成 ``DRAMA_RULES_EN``）：那条数的是汉字，
    英文没有对应口径，检查也不判——提示词里写了等于要模型守一条谁也不会检查的规矩。
    """

    if target == "podcast":
        return tuple(PC.CRAFT_RULES) + (PODCAST_TURN_RULE,)
    if target in ("drama_screenplay", "drama_shot"):
        return DRAMA_RULES_EN if language == "en" else DRAMA_RULES
    return GENERIC_RULES


DRAFT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["blocks"],
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "lines"],
                "properties": {
                    "id": {"type": "string", "description": "段 id，沿用给定的，不要新编"},
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["who", "text"],
                            "properties": {
                                "who": {"type": "string", "description": "角色表里的 id"},
                                "text": {"type": "string", "description": "一句话（可以是半句、接话、口误后改口）"},
                                "cue": {"type": "string", "description": "空，或 " + " / ".join(S.CUES) + " 之一"},
                                "kind": {"type": "string", "enum": list(S.LINE_KINDS)},
                            },
                        },
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------- 估算


def estimate(spec: Mapping[str, Any], version: str | None = None) -> dict[str, Any]:
    """``{chars, lines, blocks, seconds, minutes}``：算法直接用 ``podcast.craft.estimate``（中文 4.5 字/秒、
    英文 2.6 词/秒、句间 / 段间停顿都是它的默认值），把工作台的一版拼成它认的形状再喂进去。"""

    picked = S.pick_version(spec, version)
    known = [b["id"] for b in spec.get("blocks") or () if isinstance(b, Mapping)]
    by_id = {str(b.get("id")): b for b in (picked.get("blocks") or ()) if isinstance(b, Mapping)} if picked else {}
    # 段间停顿只算真有台词的段之间（空段不会被念出来）；段数仍报骨架的段数，界面上那个「几段」是骨架
    segments = [{"id": bid, "lines": (by_id.get(bid) or {}).get("lines") or []} for bid in known]
    est = PC.estimate({"language": spec.get("language") or "zh", "segments": [s for s in segments if s["lines"]]})
    return {"chars": est["chars"], "lines": est["lines"], "blocks": len(known),
            "seconds": est["seconds"], "minutes": est["minutes"]}


# ---------------------------------------------------------------------------- 提示词


def _speaker_block(spec: Mapping[str, Any]) -> str:
    rows = []
    for speaker in spec.get("speakers") or ():
        parts = [f"- {speaker['id']}：{speaker['name']}"]
        if speaker.get("persona"):
            parts.append(f"人设：{speaker['persona']}")
        if speaker.get("stance"):
            parts.append(f"立场：{speaker['stance']}")
        rows.append("；".join(parts))
    return "\n".join(rows)


def _brief_block(spec: Mapping[str, Any]) -> str:
    brief = spec.get("brief") or {}
    rows = []
    for key, label in (("topic", "选题"), ("angle", "切入角度"), ("audience", "受众"), ("tone", "语气")):
        if brief.get(key):
            rows.append(f"{label}：{brief[key]}")
    if brief.get("materials"):
        rows.append(f"素材（只能用这里的事实和数字，不许编）：\n{brief['materials']}")
    if brief.get("avoid"):
        rows.append("不要：" + "；".join(brief["avoid"]))
    return "\n".join(rows)


def _block_text(block: Mapping[str, Any], lines: list[Mapping[str, Any]], names: Mapping[str, str],
                *, with_lines: bool) -> str:
    rows = [f"## 段 {block.get('id')}｜{block.get('title') or ''}"]
    if str(block.get("kind") or "talk") == "qa":
        # 只标问答段：对谈段是默认，写出来反而像在提示模型往问答上靠
        rows.append(f"种类：{S.BLOCK_KIND_LABELS['qa']}（这一段在回答听众来信，念问题的那句标 question、回答它的标 answer）")
    if block.get("goal"):
        rows.append(f"目标：{block['goal']}")
    if block.get("note"):
        rows.append(f"备注：{block['note']}")
    if with_lines:
        for line in lines:
            cue = f"（{line['cue']}）" if line.get("cue") else ""
            who = names.get(str(line.get("who")), str(line.get("who")))
            rows.append(f"{who}{cue}：{line['text']}")
    return "\n".join(rows)


def build_prompt(spec: Mapping[str, Any], *, note: str = "", blocks: list[str] | None = None,
                 from_version: str | None = None) -> tuple[str, str]:
    """返回 ``(system_prompt, user_prompt)``。``blocks`` 给了就只重写这几段，其余段带台词进提示词当上下文。

    **三道拦截都在这里**，所以 ``run_generate`` 一定在 dispatch 之前拒绝，不白花一次模型请求（接口层照旧 400）：

    * 角色表空着（``spec.require_speakers``）——不拦的话模型随便挑个名字说话，回填时报的是「角色表里没有这个人」，
      而角色表根本是空的，措辞误导。
    * 段落骨架空着（``NO_BLOCKS_MESSAGE``）——没段可写，生成出来是一个 ``blocks`` 为空的废版本，还会写盘。
    * 「只重写这几段」没有一版垫底（``NO_BASE_MESSAGE``）：``from_version`` 不给就是 ``current``，没采用就是最新
      一版（``spec.pick_version``）；一版也没有时拒绝——不然模型收到的是「只重写这一段，其余段原样不动」却一句原文都没有。
    """

    try:
        S.require_speakers(spec)
    except S.ScriptSpecError as exc:
        raise ScriptLabError(str(exc)) from exc
    if not [b for b in spec.get("blocks") or () if isinstance(b, Mapping) and b.get("id")]:
        raise ScriptLabError(NO_BLOCKS_MESSAGE)
    target = str(spec.get("target") or "generic")
    label = S.TARGETS.get(target, S.TARGETS["generic"])
    lang = str(spec.get("language") or "zh")
    language = "中文" if lang == "zh" else "英文"
    rules = "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules_for(target, lang), 1))
    system_prompt = (
        f"你是剧本工作台的台词编辑，这一版稿子要拿去做{label['name']}（{label['note']}），语言是{language}。"
        "目标只有一个：这些话像人真的会说出口，不像 AI 直出的稿子。\n\n"
        "写法（每条都要守）：\n"
        f"{rules}\n\n"
        "输出规则：\n"
        "- 只输出一个 JSON 对象，blocks 按给定的段顺序；段 id 沿用给定的，不要新编、不要漏。\n"
        "- 每句台词一个对象：who 只能是角色表里的 id；text 是这个人此刻说出口的话；"
        f"cue 留空或从「{' / '.join(S.CUES)}」里选一个；kind 只在标着「{S.BLOCK_KIND_LABELS['qa']}」的段里用"
        "（念听众问题的那句标 question、正面回答它的那句标 answer），别的段一律 talk——对谈里的追问、"
        "「嗯」「对」这种接话都是 talk。\n"
        "- 同一个人连说几句就分成几个对象；不要在 text 里写说话人名、段号、括号里的舞台说明或心理描写。"
    )
    names = S.speaker_names(spec)
    picked = S.pick_version(spec, from_version) if (blocks or from_version) else None
    lines_by_block: dict[str, list[Mapping[str, Any]]] = {}
    if picked:
        lines_by_block = {str(b.get("id")): list(b.get("lines") or []) for b in picked.get("blocks") or ()}
    user_parts = [f"稿子：{spec.get('title') or ''}", "角色表：\n" + _speaker_block(spec)]
    brief = _brief_block(spec)
    if brief:
        user_parts.append("brief：\n" + brief)
    skeleton = [b for b in spec.get("blocks") or () if isinstance(b, Mapping)]
    wanted = [str(b) for b in blocks or ()]
    if wanted:
        if picked is None:
            raise ScriptLabError(NO_BASE_MESSAGE)
        missing = [bid for bid in wanted if bid not in {str(b.get("id")) for b in skeleton}]
        if missing:
            raise ScriptLabError(f"没有这些段：{'、'.join(missing)}——从段落骨架里选")
        rows = []
        for block in skeleton:
            bid = str(block.get("id"))
            body = _block_text(block, lines_by_block.get(bid, []), names, with_lines=True)
            rows.append("【只重写这一段，其余段原样不动】\n" + body if bid in wanted else body)
        user_parts.append("现有稿子（每段带段号）：\n\n" + "\n\n".join(rows))
        user_parts.append(f"只返回段 {'、'.join(wanted)}（blocks 里只放它们），台词全部重写。")
    else:
        user_parts.append("段落骨架（每段带段号；每一段都要写满台词）：\n\n"
                          + "\n\n".join(_block_text(b, [], names, with_lines=False) for b in skeleton))
    if note and note.strip():
        user_parts.append(f"这次的额外要求：{note.strip()}")
    return system_prompt, "\n\n".join(user_parts)


# ---------------------------------------------------------------------------- 回填


def _clean_lines(raw_lines: Any, speakers: Mapping[str, Any], names: Mapping[str, str], where: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw_lines, list):
        return out
    for line in raw_lines:
        if not isinstance(line, Mapping):
            continue
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        who = str(line.get("who") or "").strip()
        if who not in speakers:
            if who in names:
                who = names[who]
            else:
                raise ScriptLabError(f"模型在段 {where} 里让「{who}」说话，但角色表里没有这个人——重新生成，或把这个人加进角色表")
        cue = str(line.get("cue") or "").strip()
        kind = str(line.get("kind") or "talk").strip()
        out.append({"who": who, "text": text, "cue": cue if cue in S.CUES else "",
                    "kind": kind if kind in S.LINE_KINDS else "talk"})
    return out


def apply_draft(spec: Mapping[str, Any], structured: Any, *, blocks: list[str] | None = None,
                base: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """模型返回的 blocks → 一版的 ``blocks``（未归一化，交给 ``spec.normalize`` 再校验一遍）。

    * ``blocks`` 给了 = 只重写这几段，其余段从 ``base``（哪一版改的）原样抄过来；要重写的段模型没返回、
      或者返回了一段空台词（``{"id": "B02", "lines": []}``，或几句 text 全是空白被清干净）就报错——
      被点名重写的那一段变成空台词跟「没返回」是同一回事，不能静默写进新版本。
    * 不给 = 整版新的；模型漏掉的段保留 ``base`` 里的内容（没有 ``base`` 就是空台词）。
    * 骨架里没有的段号直接忽略——骨架是人定的，模型不许自己加段（加了也没地方显示）。
    """

    if not isinstance(structured, Mapping) or not isinstance(structured.get("blocks"), list):
        raise ScriptLabError(BAD_SHAPE_MESSAGE)
    speakers = S.speaker_by_id(spec)
    names = {str(s["name"]): str(s["id"]) for s in speakers.values()}
    known = [str(b["id"]) for b in spec.get("blocks") or () if isinstance(b, Mapping) and b.get("id")]
    previous: dict[str, list[dict[str, Any]]] = {}
    if isinstance(base, Mapping):
        previous = {str(b.get("id")): list(b.get("lines") or []) for b in base.get("blocks") or () if isinstance(b, Mapping)}
    returned: dict[str, list[dict[str, Any]]] = {}
    for i, raw in enumerate(structured["blocks"], 1):
        if not isinstance(raw, Mapping):
            continue
        bid = str(raw.get("id") or "").strip() or (known[i - 1] if i <= len(known) else S.default_block_id(i))
        if bid not in known:
            continue
        returned[bid] = _clean_lines(raw.get("lines"), speakers, names, bid)
    wanted = [str(b) for b in blocks or ()]
    for bid in wanted:
        if not returned.get(bid):        # 没这个键、或者返回的是一段空台词，都算没返回
            raise ScriptLabError(f"模型没有返回段 {bid}——再试一次")
    out: list[dict[str, Any]] = []
    for bid in known:
        if wanted:
            lines = returned[bid] if bid in wanted else previous.get(bid, [])
        else:
            lines = returned.get(bid, previous.get(bid, []))
        out.append({"id": bid, "lines": lines})
    return out


# ---------------------------------------------------------------------------- 跑一次


Dispatch = Callable[..., tuple[dict[str, Any], float | None, int | None, str]]


def run_generate(spec: Mapping[str, Any], *, note: str = "", blocks: list[str] | None = None,
                 from_version: str | None = None, label: str = "", dispatch: Dispatch | None = None) -> dict[str, Any]:
    """一次文本模型请求 → 回填 → 追加一版（``current`` 不动）→ 归一化 → 写盘。返回写下的 spec（归一化后）。"""

    from storyvideo.genre_review import _model_name    # 跟播客审稿同一个取模型名的办法

    if dispatch is None:
        from storyvideo.scriptgen import _dispatch as dispatch  # type: ignore[assignment]
    wanted = [str(b) for b in blocks or ()]
    if wanted:
        # 「只重写这几段」要有一版垫底：给了就用给的，不给先看 current，还没采用就看最新一版（pick_version 的回落）；
        # 一版也没有 = 没东西可改，直接拒绝——否则没选中的段会被写成空台词，提示词里也一句原文都没有
        base = S.pick_version(spec, from_version or None)
        if base is None:
            raise ScriptLabError(NO_BASE_MESSAGE)
        base_id = str(base["id"])
    else:
        # 整版新写：只有明确指定了基于哪一版才带 base（模型漏掉的段保留那一版），不然就是一张白纸，from_version 留空
        base_id = str(from_version or "")
        base = S.pick_version(spec, base_id) if base_id else None
    system_prompt, user_prompt = build_prompt(spec, note=note, blocks=wanted or None, from_version=base_id or None)
    structured, _cost, _usage_tokens, backend = dispatch(system_prompt, user_prompt, json_schema=DRAFT_JSON_SCHEMA)
    version_blocks = apply_draft(spec, structured, blocks=wanted or None, base=base)
    raw = S.to_disk(spec)
    versions = list(raw.get("versions") or [])
    vid = S.next_version_id(str(v.get("id") or "") for v in versions if isinstance(v, Mapping))
    versions.append({
        "id": vid,
        "label": label.strip() if label and label.strip() else f"第 {len(versions) + 1} 版",
        "source": "rewrite" if wanted else "model",
        "model": _model_name(backend),
        "backend": backend,
        "note": note.strip() if note else "",
        "created_at": time.time(),
        "from_version": base_id if base is not None else "",
        "blocks": version_blocks,
    })
    raw["versions"] = versions
    S.adopt_if_first(raw)   # 第一版自动采用；之后的版本要人挑
    try:
        fresh = S.normalize(raw, project_root=spec.get("project_root"), spec_id=str(spec["id"]))
    except S.ScriptSpecError as exc:
        raise ScriptLabError(f"模型返回的稿子没通过校验：{exc}") from exc
    S.save(fresh)
    return fresh


__all__ = ["TASK_KIND", "REMOTE_COST_NOTE", "BAD_SHAPE_MESSAGE", "NO_BASE_MESSAGE", "NO_BLOCKS_MESSAGE",
           "PODCAST_TURN_RULE", "DRAMA_RULES", "DRAMA_RULES_EN", "GENERIC_RULES", "DRAFT_JSON_SCHEMA",
           "ScriptLabError", "rules_for", "estimate", "build_prompt", "apply_draft", "run_generate"]
