"""单条视频的静态检查（契约 docs/clip-spec.md §3）。

全部是告警不是错误，形状跟其它 lint 一样（``影响——怎么做``，``beat B02: …`` / ``全片：…``），
``webui.problems.problem_card`` 直接拆成问题卡。七条自己的规则 + ``imagecraft.lint`` 的通用四项：

* 时间轴**空洞** / **重叠**（一个时间点只归一拍，空档模型会自己填）
* 节拍覆盖的总时长与 ``video_params.duration`` 差超过 1 秒
* 色板不足 5 个具体色名（``prompts`` 的 P2）
* 节拍里出现「然后 / 接着」这类连接词——一个节拍只做一件事
* 【衔接与收尾】没写最后一帧（最后一帧决定观众记住什么）
* 节拍写了画面文字，文字政策却说全片无文字（自相矛盾）

通用四项（空话 / 数字镜头参数 / 否定式描述 / 画面文字没加引号 / 卡的色板）跑在**用户写的正文**上，
不跑在编译后的整条提示词上：画幅那句「不改成 9:16 竖向」和「no transition effects」是编译器自己加的产品决定，
拿去查否定式描述只会自己告自己（见契约 §7 的偏差记录）。
"""

from __future__ import annotations

from typing import Any, Mapping

from imagecraft.cards import PALETTE_MIN, PALETTE_MAX
from imagecraft.lint import check_image_prompt

from . import prompts as P
from .spec import covered_seconds

# 一个节拍只做一件事：这些词一出现就说明这一拍里塞了两件事
CONNECTIVES = ("然后", "接着", "随后", "紧接着", "之后再", "再然后", " then ", " after that", " and then ")
# 文字政策里出现这些说法 = 全片不该有任何画面文字。只认"文字"本身：「无水印」「无字幕」说的是别的东西，
# 画面里的片名、店招跟它们并不矛盾（「无水印」与「有片名」可以同时成立）。
NO_TEXT_MARKS = ("无文字", "没有文字", "不出现文字", "无可见文字", "不出现任何文字",
                 "no text", "no on-screen text", "no visible text", "no lettering")
GAP_TOLERANCE = 0.05          # 时间轴对齐的容差（秒）：0.05 以内不算空洞/重叠
DURATION_TOLERANCE = 1.0      # 节拍总时长与 duration 的允许差（契约 §1）


def _beat(beat_id: str, impact: str, fix: str) -> str:
    return f"beat {beat_id}: {impact}——{fix}"


def _clip(impact: str, fix: str) -> str:
    return f"全片：{impact}——{fix}"


def _secs(value: Any) -> str:
    return f"{float(value):g}"


# ---------------------------------------------------------------------------- 单条规则


def check_timeline(spec: Mapping[str, Any]) -> list[str]:
    """空洞 / 重叠：相邻节拍要首尾相接。"""

    out: list[str] = []
    beats = list(spec.get("beats") or ())
    for previous, beat in zip(beats, beats[1:]):
        gap = float(beat["start"]) - float(previous["end"])
        if gap > GAP_TOLERANCE:
            out.append(_clip(
                f"时间轴在 {_secs(previous['end'])}–{_secs(beat['start'])} 秒是空的（{previous['id']} 与 {beat['id']} 之间）",
                "节拍首尾相接（上一拍的 end = 下一拍的 start）；空档里模型会自己填一段没人批准的画面"))
        elif gap < -GAP_TOLERANCE:
            out.append(_beat(str(beat["id"]),
                             f"与 {previous['id']} 在 {_secs(beat['start'])}–{_secs(previous['end'])} 秒重叠",
                             "一个时间点只归一拍，改 start / end 让它们首尾相接"))
    return out


def check_duration(spec: Mapping[str, Any]) -> list[str]:
    """节拍覆盖的总时长与 ``video_params.duration`` 对不上。"""

    beats = list(spec.get("beats") or ())
    if not beats:
        return []
    duration = float((spec.get("video_params") or {}).get("duration") or 0)
    covered = covered_seconds(spec)
    if not duration or abs(covered - duration) <= DURATION_TOLERANCE:
        return []
    return [_clip(f"节拍覆盖 {_secs(covered)} 秒，出片时长写的是 {_secs(duration)} 秒",
                  "把最后一拍的 end 或出片时长改成一样；对不上的那几秒由模型自己决定演什么")]


def check_palette(spec: Mapping[str, Any]) -> list[str]:
    """色板要 5–7 个具体色名（P2）。项目没写就看风格卡的。"""

    colors = P.palette(spec)
    if len(colors) >= PALETTE_MIN:
        return []
    return [_clip(f"色板只有 {len(colors)} 个色名",
                  f"色板写 {PALETTE_MIN}–{PALETTE_MAX} 个具体色名（雾蓝、砖红、米白），模型才有的抓")]


def check_connectives(spec: Mapping[str, Any]) -> list[str]:
    """一个节拍只做一件事：出现「然后 / 接着」说明这一拍塞了两件事。"""

    out: list[str] = []
    for beat in spec.get("beats") or ():
        text = f" {beat.get('shot') or ''} {beat.get('picture') or ''} ".lower()
        hits: list[str] = []
        for word in CONNECTIVES:          # 英文连接词自带前后空格，避免命中 strengthen 这类词里的 then
            if word.lower() in text and word.strip() not in hits:
                hits.append(word.strip())
        if not hits:
            continue
        out.append(_beat(str(beat["id"]), f"画面里有连接词「{'、'.join(hits)}」",
                         "一个节拍只做一件事：两件事拆成两拍，各自给自己的时间段"))
    return out


def check_last_frame(spec: Mapping[str, Any]) -> list[str]:
    if str((spec.get("continuity") or {}).get("last_frame") or "").strip():
        return []
    return [_clip(f"{P.HEAD_CONTINUITY[0]}没写最后一帧",
                  "写清片子最后停在什么画面上、画面主体是谁——观众带走的就是这一帧，不写模型会随手收尾")]


def check_text_policy(spec: Mapping[str, Any]) -> list[str]:
    """节拍写了画面文字，文字政策却说全片无文字。"""

    policy = str((spec.get("global") or {}).get("text_policy") or "")
    low = policy.lower()
    if not any(mark.lower() in low for mark in NO_TEXT_MARKS):
        return []
    out: list[str] = []
    for beat in spec.get("beats") or ():
        text = str(beat.get("text") or "").strip()
        if not text:
            continue
        out.append(_beat(str(beat["id"]), f"要在画面里出现文字「{text}」，文字政策却写着全片无文字",
                         "两处对不上模型只会挑一处听：改文字政策，或把这一拍的画面文字删掉"))
    return out


def check_shared(spec: Mapping[str, Any]) -> list[str]:
    """``imagecraft.lint`` 的通用项，跑在用户写的正文上（不含编译器自己加的画幅句与转场句）。"""

    section = spec.get("global") or {}
    continuity = spec.get("continuity") or {}
    body = [str(section.get(k) or "") for k in ("look", "light", "safe_area", "text_policy", "audio")]
    body.append(str(spec.get("style_anchor") or ""))
    for beat in spec.get("beats") or ():
        body.extend(str(beat.get(k) or "") for k in ("shot", "picture", "sound"))
    body.extend([str(continuity.get("transitions") or ""), str(continuity.get("last_frame") or "")])
    text = "\n".join(x for x in body if x.strip())
    warnings = check_image_prompt(text, dialect=P.dialect_for(spec), card=P.style_card(spec), label="")
    return [w if w.startswith(("beat ", "全片：")) else _clip(*_split(w)) for w in warnings]


def _split(warning: str) -> tuple[str, str]:
    for sep in ("——", "--"):
        if sep in warning:
            impact, fix = warning.split(sep, 1)
            return impact.strip(), fix.strip()
    return warning.strip(), ""


# ---------------------------------------------------------------------------- 汇总


def lint_clip(spec: Mapping[str, Any]) -> list[str]:
    """全部告警（顺序固定：时间轴 → 总时长 → 色板 → 连接词 → 最后一帧 → 文字政策 → 通用四项）。"""

    warnings: list[str] = []
    warnings += check_timeline(spec)
    warnings += check_duration(spec)
    warnings += check_palette(spec)
    warnings += check_connectives(spec)
    warnings += check_last_frame(spec)
    warnings += check_text_policy(spec)
    warnings += check_shared(spec)
    return warnings


def lint_notes(spec: Mapping[str, Any]) -> list[str]:
    """提示（不是告警）：还没写节拍、没写转场载体。"""

    notes: list[str] = []
    if not (spec.get("beats") or ()):
        notes.append(_clip("还没有节拍", "时间轴是这条片子的骨架：先按秒把画面切成 4–10 拍，再逐拍写画面"))
    if not str((spec.get("continuity") or {}).get("transitions") or "").strip():
        notes.append(_clip("没写转场载体", "借前景里本来就有的东西挡一下镜头再露出下一拍（门框、路过的人、树枝），比后期叠的特效自然"))
    return notes


def lint_stats(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "beats": len(spec.get("beats") or ()),
        "covered": covered_seconds(spec),
        "duration": (spec.get("video_params") or {}).get("duration"),
        "palette": len(P.palette(spec)),
        "dialect": P.dialect_for(spec),
        "prompt_chars": len(P.compile_clip_prompt(spec)),
    }


def lint_report(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {"warnings": lint_clip(spec), "notes": lint_notes(spec), "stats": lint_stats(spec)}


__all__ = [
    "CONNECTIVES", "NO_TEXT_MARKS", "GAP_TOLERANCE", "DURATION_TOLERANCE",
    "check_timeline", "check_duration", "check_palette", "check_connectives", "check_last_frame",
    "check_text_policy", "check_shared", "lint_clip", "lint_notes", "lint_stats", "lint_report",
]
