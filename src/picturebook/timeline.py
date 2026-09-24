"""朗读驱动的时间轴（契约 §5）——纯函数，不碰 ffmpeg。

页时长 = lead + 中文 + between + 英文 + tail；2-4 岁三个停顿各 +0.2；封面多停 2 s 让人看清书名；封底多留 3 s 给 BGM 收尾；
``sentence_alternate`` 一句中文接一句英文（中 → 0.3 s → 英 → 0.5 s → 下一句）；同一语言多句之间 0.3 s。
翻页 ``page_turn`` 用 0.4 s xfade，相邻两页重叠 0.4 s（前页的 tail ≥ 0.4、后页的 lead ≥ 0.4，语音不会撞进转场）；
``cut`` 不重叠。

输入的 ``segments``：``{page_id: {"zh": [{"text", "duration", "file"}], "en": [...]}}``（narration.json 的形状）。
某页某语言没有实测音频时按字数估一个（``estimated: True``），预览够用，装配前必须有真音频。
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

XFADE_SECONDS = 0.4
COVER_HOLD_SECONDS = 2.0
BACK_HOLD_SECONDS = 3.0
AGE_PAUSE_EXTRA = {"2-4": 0.2, "4-6": 0.0, "6-8": 0.0}
ALT_ZH_TO_EN = 0.3
ALT_PAIR_GAP = 0.5
SAME_LANG_GAP = 0.3
ESTIMATE_ZH_PER_CHAR = 0.30
ESTIMATE_EN_PER_CHAR = 0.075
ESTIMATE_BASE = 0.3
FPS = 24

_ZH_SPLIT = re.compile(r"(?<=[。！？；!?])\s*")
_EN_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'“(])")


def split_sentences(text: str, lang: str) -> list[str]:
    """按句号/问号/感叹号切句；切不出就整段一句。"""

    text = str(text or "").strip()
    if not text:
        return []
    parts = (_ZH_SPLIT if lang == "zh" else _EN_SPLIT).split(text)
    out = [p.strip() for p in parts if p and p.strip()]
    return out or [text]


def estimate_duration(text: str, lang: str) -> float:
    if lang == "zh":
        return round(ESTIMATE_BASE + ESTIMATE_ZH_PER_CHAR * len(re.findall(r"[一-鿿]", text)), 3)
    return round(ESTIMATE_BASE + ESTIMATE_EN_PER_CHAR * len(text), 3)


def languages_for(mode: str) -> tuple[str, ...]:
    return {"zh_only": ("zh",), "en_only": ("en",)}.get(mode, ("zh", "en"))


def pauses(spec: Mapping[str, Any]) -> dict[str, float]:
    base = dict((spec.get("narration") or {}).get("pause") or {})
    extra = AGE_PAUSE_EXTRA.get(str(spec.get("age_band") or ""), 0.0)
    return {key: round(float(base.get(key, default)) + extra, 3)
            for key, default in (("lead", 0.4), ("between", 0.6), ("tail", 0.8))}


def _segments_for(page: Mapping[str, Any], lang: str, provided: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if provided:
        return [{"text": str(s.get("text") or ""), "duration": float(s.get("duration") or 0.0), "file": s.get("file"),
                 "estimated": bool(s.get("estimated", False))} for s in provided if float(s.get("duration") or 0.0) > 0]
    return [{"text": s, "duration": estimate_duration(s, lang), "file": None, "estimated": True}
            for s in split_sentences(str(page.get(lang) or ""), lang)]


def _layout(mode: str, zh: list[dict[str, Any]], en: list[dict[str, Any]], p: Mapping[str, float]) -> tuple[list[dict[str, Any]], float]:
    """页内各段的局部起止；返回 (段列表, 语音部分结束时刻)。"""

    t = p["lead"]
    out: list[dict[str, Any]] = []

    def put(lang: str, index: int, seg: Mapping[str, Any]) -> None:
        nonlocal t
        out.append({"lang": lang, "index": index, "text": seg["text"], "file": seg.get("file"), "estimated": seg.get("estimated", False),
                    "local_start": round(t, 3), "local_end": round(t + seg["duration"], 3), "duration": round(seg["duration"], 3)})
        t += seg["duration"]

    def run(lang: str, segs: list[dict[str, Any]]) -> None:
        nonlocal t
        for i, seg in enumerate(segs):
            if i:
                t += SAME_LANG_GAP
            put(lang, i, seg)

    if mode == "zh_only":
        run("zh", zh)
    elif mode == "en_only":
        run("en", en)
    elif mode == "page_zh_then_en":
        run("zh", zh)
        if zh and en:
            t += p["between"]
        run("en", en)
    else:  # sentence_alternate
        pairs = max(len(zh), len(en))
        for i in range(pairs):
            if i:
                t += ALT_PAIR_GAP
            if i < len(zh):
                put("zh", i, zh[i])
            if i < len(zh) and i < len(en):
                t += ALT_ZH_TO_EN
            if i < len(en):
                put("en", i, en[i])
    return out, t


def _display_windows(segments: list[dict[str, Any]], duration: float) -> None:
    """每段的字幕显示窗口：从上一段同语言的显示结束（第一段从 0）到下一段同语言的朗读开始（最后一段到页尾）。"""

    by_lang: dict[str, list[dict[str, Any]]] = {}
    for seg in segments:
        by_lang.setdefault(seg["lang"], []).append(seg)
    for rows in by_lang.values():
        for i, seg in enumerate(rows):
            start = 0.0 if i == 0 else rows[i - 1]["display_end"]
            end = duration if i == len(rows) - 1 else rows[i + 1]["local_start"]
            seg["display_start"], seg["display_end"] = round(start, 3), round(end, 3)


def build_timeline(spec: Mapping[str, Any], segments: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]] | None = None) -> dict[str, Any]:
    mode = str(spec.get("bilingual_mode") or "sentence_alternate")
    langs = languages_for(mode)
    p = pauses(spec)
    segments = segments or {}
    pages_out: list[dict[str, Any]] = []
    cursor = 0.0
    estimated_any = False
    pages = [pg for pg in spec.get("pages") or () if isinstance(pg, Mapping)]
    for index, page in enumerate(pages):
        pid = str(page["id"])
        provided = segments.get(pid) or {}
        zh = _segments_for(page, "zh", provided.get("zh")) if "zh" in langs else []
        en = _segments_for(page, "en", provided.get("en")) if "en" in langs else []
        segs, speech_end = _layout(mode, zh, en, p)
        hold = COVER_HOLD_SECONDS if page.get("kind") == "cover" else 0.0
        duration = speech_end + hold + p["tail"]
        back_hold = BACK_HOLD_SECONDS if page.get("kind") == "back" else 0.0
        duration += back_hold
        duration = round(duration, 3)
        _display_windows(segs, duration)
        last = index == len(pages) - 1
        transition = XFADE_SECONDS if (not last and page.get("flip", "page_turn") == "page_turn") else 0.0
        for seg in segs:
            seg["start"] = round(cursor + seg["local_start"], 3)
            seg["end"] = round(cursor + seg["local_end"], 3)
            estimated_any = estimated_any or bool(seg.get("estimated"))
        pages_out.append({
            "id": pid, "kind": page.get("kind") or "page", "start": round(cursor, 3), "duration": duration,
            "end": round(cursor + duration, 3), "speech_end": round(speech_end, 3), "cover_hold": hold, "back_hold": back_hold,
            "flip": page.get("flip", "page_turn"), "transition_out": transition, "kb": dict(page.get("kb") or {}),
            "segments": segs,
        })
        cursor = round(cursor + duration - transition, 3)
    total = round(pages_out[-1]["end"], 3) if pages_out else 0.0
    return {"mode": mode, "age_band": spec.get("age_band"), "pauses": p, "fps": FPS, "xfade": XFADE_SECONDS,
            "total": total, "estimated": estimated_any, "pages": pages_out}


__all__ = ["XFADE_SECONDS", "COVER_HOLD_SECONDS", "BACK_HOLD_SECONDS", "AGE_PAUSE_EXTRA", "ALT_ZH_TO_EN", "ALT_PAIR_GAP",
           "SAME_LANG_GAP", "FPS", "split_sentences", "estimate_duration", "languages_for", "pauses", "build_timeline"]
