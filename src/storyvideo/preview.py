"""生成前提示词预览 + 时长/成本预算校验。

两者都是纯函数，只读 spec（不碰远端、不碰磁盘产物），可以在 ``prepare`` 冻结
spec **之前**随便跑、随便改剧本再跑。跟 ``prepare`` 共用同一套编译函数
(``prompts.py``)，预览看到的就是真正会发给模型的文本，不是另一套近似描述。

设计动机：按段落估时长常常严重偏短——按字数实算（台词字数 ÷ 4.5 字/秒，再加反应镜和静默）
往往比标称长出一半以上。这类错误本该在写完对白的当下就被拦下，不必等真人逐段读一遍才发现。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .prompts import (
    compile_negative_prompt,
    compile_video_prompt,
    frame_request_data,
    validate_camera_usage,
    video_request_data,
)

CHARS_PER_SECOND = 4.5          # 正常语速（估算的快端）
SLOW_CHARS_PER_SECOND = 3.5     # 偏慢/带情绪停顿（估算的慢端）；容量按慢端核，不按快端赌
HEAD_SECONDS = 0.8              # 开口前的等待（视频提示词要求 0.8 秒内开口）
TAIL_SECONDS = 0.5              # 说完后的反应落点
DEFAULT_TOLERANCE = 0.35  # 纯语音时长允许比 dur 短最多 35%（反应/静默镜头天然如此）


def _dialogue_chars(shot: Mapping[str, Any]) -> int:
    total = 0
    for line in shot.get("lines") or ():
        if not isinstance(line, Mapping):
            continue
        text = line.get("zh") or line.get("en") or ""
        total += len(str(text))
    return total


def shot_duration_budget(shot: Mapping[str, Any]) -> dict[str, Any]:
    """单镜时长核算：台词字数换算的纯语音秒数 vs 声明的 dur。"""

    chars = _dialogue_chars(shot)
    speech_seconds = round(chars / CHARS_PER_SECOND, 2) if chars else 0.0
    speech_slow = round(chars / SLOW_CHARS_PER_SECOND, 2) if chars else 0.0
    dur = float(shot.get("dur") or 0)
    # 可用发声窗口 = 声明时长 - 开口前等待 - 末尾反应（drama-skills「对白预算」：并行动作共享时间，顺序事件累加）
    window = max(0.0, dur - HEAD_SECONDS - TAIL_SECONDS) if chars else dur
    # 快端都装不下 = 装不下；快端装得下、慢端装不下 = 偏紧（语速一慢就赶词）
    overflow = speech_seconds > window + 1e-6 if chars else False
    tight = (not overflow) and chars > 0 and speech_slow > window + 1e-6
    # 纯语音时长也不该离谱地短于声明时长——那通常是空镜/反应镜，允许，但要看得见。
    slack = (dur - speech_seconds) / dur if dur > 0 else 0.0
    return {
        "shot_id": shot.get("id"),
        "dialogue_chars": chars,
        "speech_seconds": speech_seconds,
        "speech_seconds_slow": speech_slow,
        "window_seconds": round(window, 2),
        "declared_dur": dur,
        "overflow": overflow,
        "tight": tight,
        "slack_ratio": round(slack, 2),
    }


def check_duration_budget(
    spec: Mapping[str, Any],
    *,
    slack_tolerance: float | None = DEFAULT_TOLERANCE,
) -> list[str]:
    """整集时长校验，返回告警字符串列表；不告警不代表时长一定「对」，只代表
    没有已知的两类错误：台词装不进声明的镜长，或全集实算时长偏离声明太多。

    ``overflow``（台词装不下）在两条产线上都是真信号——这条不接受关闭：
    装不下是硬事实（多出来的部分要么被截断、要么挤进下一镜），不是风格取舍。

    ``slack_tolerance``（台词占比过低）**不是**跨产线通用的信号：黑色喜剧线
    （dark_comedy_vertical）反应/静默镜是刻意的例外，容差收紧才有意义；
    但慢节奏的朗读类内容（绘本、分级读物）停顿是设计好的节奏，念得慢是常态不是例外——
    拿这类内容测这条时几乎每页都会触发。
    这类内容传 ``slack_tolerance=None`` 关掉这半条检查，只留 overflow。
    """

    warnings: list[str] = []
    shots = [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]
    total_chars = 0
    total_declared = 0.0
    for shot in shots:
        budget = shot_duration_budget(shot)
        total_chars += budget["dialogue_chars"]
        total_declared += budget["declared_dur"]
        if budget["overflow"]:
            warnings.append(
                f"shot {budget['shot_id']}: 台词 {budget['dialogue_chars']} 字 "
                f"≈{budget['speech_seconds']}s 纯语音，超过可用发声窗口 {budget['window_seconds']:g}s"
                f"（声明 {budget['declared_dur']:g}s 扣掉开口等待 {HEAD_SECONDS:g}s 和末尾反应 {TAIL_SECONDS:g}s）——"
                "这镜装不下这些台词，加时长、拆镜或删词；不要靠更快语速硬凑。"
            )
        elif budget["tight"]:
            warnings.append(
                f"shot {budget['shot_id']}: 台词 {budget['dialogue_chars']} 字按偏慢语速要 "
                f"≈{budget['speech_seconds_slow']}s，可用发声窗口只有 {budget['window_seconds']:g}s——"
                "偏紧：语气一慢就赶词，建议加 1–2 秒或把台词减一句。"
            )
        elif (
            slack_tolerance is not None
            and budget["declared_dur"] > 0
            and budget["slack_ratio"] > slack_tolerance
        ):
            warnings.append(
                f"shot {budget['shot_id']}: 台词只占声明时长的 "
                f"{round((1 - budget['slack_ratio']) * 100)}%（{budget['speech_seconds']}s "
                f"/ {budget['declared_dur']:g}s），确认这镜是有意留白/反应镜，不是漏写台词。"
            )

    total_speech = round(total_chars / CHARS_PER_SECOND, 1)
    if slack_tolerance is not None and total_declared > 0:
        drift = (total_speech - total_declared) / total_declared
        if drift > slack_tolerance:
            warnings.append(
                f"全集：{len(shots)} 镜台词共 {total_chars} 字 ≈{total_speech}s 纯语音，"
                f"比声明的总时长 {total_declared:g}s 多出 {round(drift * 100)}%——"
                "报时长前按这个数字来，不要按段落拍脑袋估。"
            )
    return warnings


def _dialogue_table(shot: Mapping[str, Any]) -> str:
    lines = [line for line in (shot.get("lines") or ()) if isinstance(line, Mapping)]
    if not lines:
        return "_（本镜无台词）_"
    rows = ["| 时间窗 | 说话人 | 台词 |", "|---|---|---|"]
    for line in lines:
        start = line.get("subtitle_start_seconds")
        end = line.get("subtitle_end_seconds")
        window = f"{start:.1f}–{end:.1f}s" if start is not None and end is not None else ""
        text = line.get("zh") or line.get("en") or ""
        rows.append(f"| {window} | {line.get('who', '')} | {text} |")
    return "\n".join(rows)


def render_shot_preview(spec: Mapping[str, Any], shot_id: str) -> str:
    """单镜预览：本镜真正会发给模型的完整提示词，加台词表和时长核算。"""

    shot = next(
        (s for s in spec.get("shots", ()) if isinstance(s, Mapping) and s.get("id") == shot_id),
        None,
    )
    if shot is None:
        raise KeyError(f"unknown shot: {shot_id}")

    frame_req = frame_request_data(spec, shot_id)
    video_req = video_request_data(spec, shot_id)
    budget = shot_duration_budget(shot)
    budget_line = (
        f"台词 {budget['dialogue_chars']} 字 ≈{budget['speech_seconds']}s "
        f"/ 声明 {budget['declared_dur']:g}s"
        + ("　⚠ 超时长" if budget["overflow"] else "")
    )

    parts = [
        f"### {shot_id}（{shot.get('dur', '?')}s）",
        "",
        f"cast: {', '.join(shot.get('cast') or ())}",
        f"ref_frame: {shot.get('ref_frame') or '（无，独立首帧）'}",
        f"时长核算: {budget_line}",
        "",
        "**台词**",
        "",
        _dialogue_table(shot),
        "",
        "**首帧提示词（frame，实际会发给模型的完整文本）**",
        "",
        "```",
        frame_req["prompt"],
        "```",
        "",
        "**首帧负向提示词**",
        "",
        "```",
        compile_negative_prompt(spec, shot_id),
        "```",
        "",
        "**视频提示词（video，实际会发给模型的完整文本）**",
        "",
        "```",
        compile_video_prompt(spec, shot_id),
        "```",
        "",
        f"视频模型参数: {video_req['model']} / {video_req['duration']}s / "
        f"{video_req['resolution']} / {video_req['aspect_ratio']}"
        + ("（原生音轨）" if video_req["generate_audio"] else "（无音轨）"),
    ]
    return "\n".join(parts)


def render_preview(
    spec: Mapping[str, Any],
    *,
    shot_ids: Sequence[str] | None = None,
    slack_tolerance: float | None = DEFAULT_TOLERANCE,
) -> str:
    """整集预览：逐镜提示词 + 全局告警（时长预算 + 镜头语言用量）。

    冻结前随便跑；不提交任何远端请求，纯读 spec。``slack_tolerance=None`` 关闭
    「台词占比过低」这条检查——留白/停顿是内容设计的一部分（如朗读类内容）
    时应该关闭，见 ``check_duration_budget`` 的说明。
    """

    shots = spec.get("shots", ())
    ids = list(shot_ids) if shot_ids else [s["id"] for s in shots if isinstance(s, Mapping)]

    duration_warnings = check_duration_budget(spec, slack_tolerance=slack_tolerance)
    camera_warnings = validate_camera_usage(spec)

    header = [
        f"# 预览：{spec.get('id', '(未命名)')}",
        "",
        f"{len(ids)} 镜 · 未冻结，可自由修改剧本后重新预览",
        "",
    ]
    if duration_warnings or camera_warnings:
        header.append("## ⚠ 告警")
        header.append("")
        for w in duration_warnings + camera_warnings:
            header.append(f"- {w}")
        header.append("")
    else:
        header.append("时长与镜头用量均在预算内，无告警。")
        header.append("")

    body = [render_shot_preview(spec, shot_id) for shot_id in ids]
    return "\n\n---\n\n".join(["\n".join(header)] + body)


__all__ = [
    "CHARS_PER_SECOND",
    "check_duration_budget",
    "render_preview",
    "render_shot_preview",
    "shot_duration_budget",
]


# ---- 台词清晰度（实拍经验）----
# 语气里写「含糊 / 气声 / 几乎发不出声 / 嘟囔」，Seedance 是字面执行的：真的给你念成乱语；
# 一两个字的台词和画外音短句经常不念或念错。这两类在起草期就拦住，别等成片才听出来。
_UNCLEAR_TONE_WORDS = ("含糊", "气声", "发不出声", "嘟囔", "听不清", "咕哝", "含在嘴里", "气音", "几乎没声")
_MIN_LINE_CHARS = 3


def check_dialogue_clarity(spec: Mapping[str, Any]) -> list[str]:
    import re

    warnings: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        shot_id = str(shot.get("id") or "")
        offscreen = str(shot.get("speech_mode") or "") in {"narration", "offscreen_narration"}
        for line in shot.get("lines") or ():
            if not isinstance(line, Mapping):
                continue
            zh = str(line.get("zh") or "")
            tone = str(line.get("tone") or "")
            hits = [w for w in _UNCLEAR_TONE_WORDS if w in tone]
            if hits:
                warnings.append(
                    f"shot {shot_id}: 台词「{zh}」的 tone 含「{'、'.join(hits)}」——模型会字面执行成乱语，"
                    "改成用音量/语速/情绪描述，并保留口齿清楚"
                )
            core = re.sub(r"[^一-鿿0-9A-Za-z]", "", zh)
            if 0 < len(core) < _MIN_LINE_CHARS:
                warnings.append(
                    f"shot {shot_id}: 台词「{zh}」只有 {len(core)} 个字——一两个字的台词模型经常不念或念错，"
                    "合并进相邻句或改成 3 字以上"
                )
            if offscreen and len(core) <= 4:
                warnings.append(
                    f"shot {shot_id}: 画外音短句「{zh}」——画外 + 短句是丢句高发组合，改成看得见嘴的镜或加长到 5 字以上"
                )
    return warnings
