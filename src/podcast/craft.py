"""真人感起草（契约 docs/podcast-spec.md §2）：一次文本模型请求 → 环节 + 台词。

「像真人在聊」不是形容词，是写进系统提示词的八条可执行约束（``CRAFT_RULES``）：口语不是书面语、有人不同意、
具体压倒观点、每个人是一个人、问答要像回答、冷开场不自我介绍、禁止清单、时长可数。它们是经验规则（§7），
不是测量结论；静态检查（``lint.py``）用同一套判据反过来查模型交回来的稿子。

模型返回的说话人只认主播表里的 id（名字也认，归一化时换成 id）；不认识的说话人直接报错，不静默丢句子——
跟剧本层同一原则。``dispatch`` 可注入（测试用假模型）。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from . import spec as S

# 语速沿用短剧的估算（storyvideo.preview.CHARS_PER_SECOND，真实事故校准）
CHARS_PER_SECOND = 4.5
EN_WORDS_PER_SECOND = 2.6
PAUSE_CUE_EXTRA = 0.6           # cue 含「停顿」的句前再加的秒数
TASK_KIND = "pc-draft"
REMOTE_COST_NOTE = "发 1 次文本模型请求"
BAD_SHAPE_MESSAGE = "模型没有按稿子结构返回（缺 segments 列表）——再试一次，或换文本模型"

CRAFT_RULES: tuple[str, ...] = (
    "口语不是书面语：短句、半句、接话、自我修正（「不对，我说错了」）；不用「首先 / 其次 / 综上 / 值得注意的是」这类连接词；不列点。",
    "有人不同意：双人以上至少一处明确分歧，而且不马上和解；有追问（「你说的『上瘾』具体指什么？」）。",
    "具体压倒观点：每个话题环节至少一段亲历或案例，带时间 / 地点 / 数字；材料只从 brief 的素材里取，不许编数据。",
    "每个人是一个人：按人设和立场说话，口癖有但不密；两个人不许观点一致到分不清谁在说。",
    "问答要像回答：把听众的称呼和问题念出来；先给结论再讲原因；允许「这个我不确定」；不复述问题。",
    "冷开场不自我介绍：从一个具体场景或对话中段切入；开场环节再介绍人与题；收尾不总结全篇，只留一句 + 互动号召或下期预告。",
    "禁止：排比、每段末尾升华、「今天我们聊了三点」、机械轮流（一句一换人）、心理描写；cue 只标能演的（笑 / 轻笑 / 停顿 / 打断 / 叹气 / 加快 / 放慢 / 低声 / 强调）。",
    "时长可数：中文按每秒 4.5 字估，整期落在目标分钟的 ±20%。",
)

DRAFT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["segments"],
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "kind", "title", "lines"],
                "properties": {
                    "id": {"type": "string", "description": "环节 id，沿用给定的；新环节按 G01、G02 编"},
                    "kind": {"type": "string", "enum": list(S.SEGMENT_KINDS)},
                    "title": {"type": "string"},
                    "goal": {"type": "string", "description": "这一段听众要得到什么（一句）"},
                    "beats": {"type": "array", "items": {"type": "string"}, "description": "这一段要过的点，一条一句"},
                    "questions": {"type": "array", "items": {"type": "object", "required": ["from", "text"],
                                                              "properties": {"from": {"type": "string"}, "text": {"type": "string"}}},
                                  "description": "只有听友问答环节用：听众称呼 + 问题"},
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["who", "text"],
                            "properties": {
                                "who": {"type": "string", "description": "主播 id"},
                                "text": {"type": "string", "description": "一句话，口语，可以是半句或接话"},
                                "cue": {"type": "string", "description": "空，或 笑 / 轻笑 / 停顿 / 打断 / 叹气 / 加快 / 放慢 / 低声 / 强调 之一"},
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


def line_seconds(text: str, language: str = "zh", cue: str = "") -> float:
    """一句话念出来大约几秒（中文按字、英文按词），含 cue 的额外停顿。"""

    body = str(text or "").strip()
    if not body:
        return 0.0
    if language == "en":
        seconds = len(body.split()) / EN_WORDS_PER_SECOND
    else:
        seconds = len(body) / CHARS_PER_SECOND
    if "停顿" in str(cue or ""):
        seconds += PAUSE_CUE_EXTRA
    return round(seconds, 2)


def estimate(spec: Mapping[str, Any]) -> dict[str, Any]:
    """``{chars, lines, segments, seconds, minutes, target_minutes}``：字数 ÷ 语速 + 句间 / 环节间停顿。"""

    language = str(spec.get("language") or "zh")
    pause = spec.get("pause") or S.DEFAULT_PAUSE
    chars = 0
    lines = 0
    seconds = 0.0
    segments = [s for s in spec.get("segments") or () if isinstance(s, Mapping)]
    for segment in segments:
        rows = [l for l in segment.get("lines") or () if isinstance(l, Mapping) and str(l.get("text") or "").strip()]
        for line in rows:
            text = str(line.get("text") or "").strip()
            chars += len(text)
            lines += 1
            seconds += line_seconds(text, language, str(line.get("cue") or ""))
        if rows:
            seconds += float(pause.get("line", 0.35)) * max(0, len(rows) - 1)
    if segments:
        seconds += float(pause.get("segment", 1.2)) * max(0, len(segments) - 1)
    return {
        "chars": chars, "lines": lines, "segments": len(segments),
        "seconds": round(seconds, 1), "minutes": round(seconds / 60.0, 1),
        "target_minutes": int(spec.get("target_minutes") or S.DEFAULT_MINUTES),
    }


# ---------------------------------------------------------------------------- 提示词


def default_skeleton(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """没写环节时给起草用的骨架：冷开场 → 开场 → 两三个话题 → 听友问答 → 收尾。按目标分钟定话题数。"""

    minutes = int(spec.get("target_minutes") or S.DEFAULT_MINUTES)
    topics = 1 if minutes <= 6 else 2 if minutes <= 15 else 3
    rows: list[dict[str, Any]] = [
        {"kind": "cold_open", "title": "冷开场", "goal": "用一个具体场景把人拽进来，不自我介绍"},
        {"kind": "intro", "title": "开场", "goal": "介绍人和今天聊什么，一句话说清为什么值得听"},
    ]
    takeaways = list(spec.get("brief", {}).get("takeaways") or [])
    for i in range(topics):
        goal = takeaways[i] if i < len(takeaways) else ""
        rows.append({"kind": "topic", "title": f"话题 {i + 1}", "goal": goal})
    rows.append({"kind": "qa", "title": "听友问答", "goal": "念出听众的问题并真的回答"})
    rows.append({"kind": "outro", "title": "收尾", "goal": "留一句话和一个互动号召，不总结全篇"})
    for i, row in enumerate(rows, 1):
        row.update({"id": S.default_segment_id(i), "beats": [], "questions": [], "lines": []})
    return rows


def _host_block(spec: Mapping[str, Any]) -> str:
    rows = []
    for host in spec.get("hosts") or ():
        role = {"host": "主播", "cohost": "搭档", "guest": "嘉宾"}.get(str(host.get("role") or ""), str(host.get("role") or ""))
        parts = [f"- {host['id']}：{host['name']}（{role}）"]
        if host.get("persona"):
            parts.append(f"人设：{host['persona']}")
        if host.get("stance"):
            parts.append(f"立场：{host['stance']}")
        rows.append("；".join(parts))
    return "\n".join(rows)


def _brief_block(spec: Mapping[str, Any]) -> str:
    brief = spec.get("brief") or {}
    rows = []
    for key, label in (("topic", "选题"), ("angle", "切入角度"), ("audience", "听众"), ("tone", "语气")):
        if brief.get(key):
            rows.append(f"{label}：{brief[key]}")
    if brief.get("takeaways"):
        rows.append("听完带走：" + "；".join(brief["takeaways"]))
    if brief.get("materials"):
        rows.append(f"素材（只能用这里的事实和数字，不许编）：\n{brief['materials']}")
    if brief.get("avoid"):
        rows.append("不要：" + "；".join(brief["avoid"]))
    return "\n".join(rows)


def _segment_block(segment: Mapping[str, Any], *, with_lines: bool) -> str:
    kind = str(segment.get("kind") or "topic")
    rows = [f"## 环节 {segment.get('id')}｜{S.SEGMENT_KIND_LABELS.get(kind, kind)}（kind={kind}）｜{segment.get('title') or ''}"]
    if segment.get("goal"):
        rows.append(f"目标：{segment['goal']}")
    for beat in segment.get("beats") or ():
        rows.append(f"- 要过的点：{beat}")
    for q in segment.get("questions") or ():
        who = q.get("from") or "听众"
        rows.append(f"- 听众问题（{who}）：{q.get('text')}")
    if with_lines:
        for line in segment.get("lines") or ():
            cue = f"（{line['cue']}）" if line.get("cue") else ""
            rows.append(f"{line['who']}{cue}：{line['text']}")
    return "\n".join(rows)


def build_draft_prompt(spec: Mapping[str, Any], *, note: str = "", only_segment: str | None = None) -> tuple[str, str]:
    """返回 ``(system_prompt, user_prompt)``。``only_segment`` 给了只重写那一个环节，其余环节原文进提示词当上下文。"""

    fmt = S.FORMATS[str(spec.get("format") or "duo")]
    language = "中文" if str(spec.get("language") or "zh") == "zh" else "英文"
    minutes = int(spec.get("target_minutes") or S.DEFAULT_MINUTES)
    rules = "\n".join(f"{i}. {rule}" for i, rule in enumerate(CRAFT_RULES, 1))
    system_prompt = (
        f"你是播客的稿子编辑，写的是{fmt['name']}（{fmt['note']}），语言是{language}。"
        "目标只有一个：听起来像真人在聊，不像 AI 直出的稿子。\n\n"
        "真人播客和 AI 稿的差别，写成下面八条硬约束，每条都要守：\n"
        f"{rules}\n\n"
        "输出规则：\n"
        "- 只输出一个 JSON 对象，segments 按播放顺序；每个环节沿用给定的 id / kind / title，节拍与听众问题可以补全。\n"
        "- 每句台词一个对象：who 只能是主播表里的 id；text 是这个人此刻说出口的话（可以是半句、接话、口误后改口）；"
        "cue 空或从允许清单里选一个；听友问答环节里念问题的句子 kind=question、回答 kind=answer，其余 talk。\n"
        "- 一句台词不超过 120 字；同一个人连说几句就分成几个对象；别让一个人连讲超过 4 句没人接话（单人独白除外）。\n"
        # 实测：只写「禁止机械轮流」时，模型仍会每一句都换人——改成能数的要求
        "- 真人对谈不是一问一答轮流：每个环节里至少有两处同一个人连着说两三句（补充、举例、改口），"
        "另一个人的接话可以短到一两个字（「嗯」「对」「啊？」）也单独成一句；整段每一句都换人算机械轮流，检查会打回。\n"
        f"- 整期按每秒 4.5 字估，总字数落在 {int(minutes * 60 * CHARS_PER_SECOND * 0.8)}–{int(minutes * 60 * CHARS_PER_SECOND * 1.2)} 字。"
    )
    segments = [s for s in spec.get("segments") or () if isinstance(s, Mapping)] or default_skeleton(spec)
    user_parts = [f"节目：{spec.get('title') or ''}", f"形式：{fmt['name']}，目标 {minutes} 分钟", "主播表：\n" + _host_block(spec)]
    brief = _brief_block(spec)
    if brief:
        user_parts.append("选题 brief：\n" + brief)
    if only_segment:
        blocks = []
        for segment in segments:
            if str(segment.get("id")) == only_segment:
                blocks.append("【只重写这个环节，其余环节原样不动】\n" + _segment_block(segment, with_lines=True))
            else:
                blocks.append(_segment_block(segment, with_lines=True))
        user_parts.append("现有稿子（每个环节带 id）：\n\n" + "\n\n".join(blocks))
        user_parts.append(f"只返回环节 {only_segment} 一个对象（segments 里只放它），台词全部重写。")
    else:
        user_parts.append("环节骨架（每个环节带 id；没有台词的要写满，有台词的也全部重写）：\n\n"
                          + "\n\n".join(_segment_block(s, with_lines=False) for s in segments))
    if note and note.strip():
        user_parts.append(f"这次的额外要求：{note.strip()}")
    return system_prompt, "\n\n".join(user_parts)


# ---------------------------------------------------------------------------- 回填


class DraftError(ValueError):
    """模型没按结构返回，或返回的说话人不在主播表里。"""


def _clean_lines(raw_lines: Any, hosts: dict[str, dict[str, Any]], names: dict[str, str], where: str) -> list[dict[str, Any]]:
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
        if who not in hosts:
            if who in names:
                who = names[who]
            else:
                raise DraftError(f"模型在环节 {where} 里让「{who}」说话，但主播表里没有这个人——重新起草，或把这个人加进主播表")
        cue = str(line.get("cue") or "").strip()
        kind = str(line.get("kind") or "talk").strip()
        out.append({"who": who, "text": text, "cue": cue if cue in S.CUES else "", "kind": kind if kind in S.LINE_KINDS else "talk"})
    return out


def apply_draft(spec: Mapping[str, Any], structured: Any, *, only_segment: str | None = None) -> list[dict[str, Any]]:
    """把模型返回的 segments 变成项目文件里的环节列表（未归一化，交给 ``spec.normalize`` 再校验一遍）。"""

    if not isinstance(structured, Mapping) or not isinstance(structured.get("segments"), list):
        raise DraftError(BAD_SHAPE_MESSAGE)
    hosts = S.host_by_id(spec)
    names = {str(h["name"]): str(h["id"]) for h in hosts.values()}
    existing = [dict(s) for s in spec.get("segments") or () if isinstance(s, Mapping)] or default_skeleton(spec)
    returned: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for i, raw in enumerate(structured["segments"], 1):
        if not isinstance(raw, Mapping):
            continue
        sid = str(raw.get("id") or "").strip() or S.default_segment_id(i)
        kind = str(raw.get("kind") or "topic").strip()
        questions = [{"from": str(q.get("from") or "").strip(), "text": str(q.get("text") or "").strip()}
                     for q in (raw.get("questions") or ()) if isinstance(q, Mapping) and str(q.get("text") or "").strip()]
        returned[sid] = {
            "id": sid,
            "kind": kind if kind in S.SEGMENT_KINDS else "topic",
            "title": str(raw.get("title") or "").strip(),
            "goal": str(raw.get("goal") or "").strip(),
            "beats": [str(b).strip() for b in (raw.get("beats") or ()) if str(b).strip()],
            "questions": questions,
            "lines": _clean_lines(raw.get("lines"), hosts, names, sid),
        }
        order.append(sid)
    if only_segment:
        if only_segment not in returned:
            raise DraftError(f"模型没有返回环节 {only_segment}——再试一次")
        out = []
        for segment in existing:
            if str(segment.get("id")) == only_segment:
                fresh = returned[only_segment]
                # 只换台词与节拍；种类 / 标题 / 听众问题以人写的为准（模型可能改了它们）
                merged = dict(segment)
                merged["lines"] = fresh["lines"]
                merged["beats"] = fresh["beats"] or segment.get("beats") or []
                if not merged.get("goal") and fresh["goal"]:
                    merged["goal"] = fresh["goal"]
                out.append(merged)
            else:
                out.append(segment)
        return out
    # 整期：按模型返回的顺序；模型漏掉的骨架环节保留原样（空台词会被 lint 提出来）
    out = [returned[sid] for sid in order]
    seen = set(order)
    for segment in existing:
        sid = str(segment.get("id"))
        if sid not in seen:
            out.append(segment)
    # 骨架里的种类 / 标题以人写的为准（模型不许把冷开场改成话题）
    by_id = {str(s.get("id")): s for s in existing}
    for segment in out:
        base = by_id.get(str(segment.get("id")))
        if base is not None:
            segment["kind"] = base.get("kind") or segment["kind"]
            if base.get("title"):
                segment["title"] = base["title"]
            if base.get("questions"):
                segment["questions"] = base["questions"]
    return out


def _model_name(backend: str) -> str:
    try:
        if backend == "settings":
            from genkit import settings as settings_mod

            return settings_mod.effective(settings_mod.load_settings().role("text"), "text").model or ""
        if backend == "openai_proxy":
            from storyvideo import scriptgen

            return scriptgen.llm_proxy_model() or ""
    except Exception:  # noqa: BLE001 - 记不下模型名不该让起草失败
        return ""
    return ""


Dispatch = Callable[..., tuple[dict[str, Any], float | None, int | None, str]]


def run_draft(spec: dict[str, Any], *, note: str = "", only_segment: str | None = None,
              dispatch: Dispatch | None = None) -> dict[str, Any]:
    """一次文本模型请求 → 回填环节 → 归一化 → 写盘。返回写下的 spec（归一化后）。"""

    if dispatch is None:
        from storyvideo.scriptgen import _dispatch as dispatch  # type: ignore[assignment]
    system_prompt, user_prompt = build_draft_prompt(spec, note=note, only_segment=only_segment)
    structured, _cost, usage_tokens, backend = dispatch(system_prompt, user_prompt, json_schema=DRAFT_JSON_SCHEMA)
    segments = apply_draft(spec, structured, only_segment=only_segment)
    raw = S.to_disk(spec)
    raw["segments"] = segments
    raw["draft"] = {"model": _model_name(backend), "backend": backend, "created_at": time.time(),
                    "usage_tokens": usage_tokens, "note": note.strip() if note else ""}
    try:
        fresh = S.normalize(raw, project_root=spec.get("project_root"), spec_id=str(spec["id"]))
    except S.PodcastSpecError as exc:
        raise DraftError(f"模型返回的稿子没通过校验：{exc}") from exc
    S.save(fresh)
    return fresh


__all__ = ["CRAFT_RULES", "DRAFT_JSON_SCHEMA", "CHARS_PER_SECOND", "TASK_KIND", "REMOTE_COST_NOTE", "DraftError",
           "line_seconds", "estimate", "default_skeleton", "build_draft_prompt", "apply_draft", "run_draft"]
