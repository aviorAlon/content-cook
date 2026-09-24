"""配乐提示词编译（契约 docs/music-spec.md §2）。

一句话用途开头，然后体裁、情绪、乐器、速度、结构，最后一句「不要：…」。中英各一份，按
``prompt_language`` 取。时长也写进去——音乐生成接口大多没有时长参数，只能靠
描述表达（见 docs/music-spec.md §7 实现偏差）。

编译是纯函数：同一份 brief 出同一段字，没有随机、不联网。
"""

from __future__ import annotations

from typing import Any, Mapping

SEP_ZH = "；"
SEP_EN = "; "


def _joined(values: Any, sep: str) -> str:
    return sep.join(str(v).strip() for v in (values or ()) if str(v).strip())


def compile_prompt(spec: Mapping[str, Any], *, language: str | None = None) -> str:
    """按语言编译一段配乐描述。``language`` 不给就取 ``spec['prompt_language']``。"""

    lang = str(language or spec.get("prompt_language") or "zh")
    return compile_en(spec) if lang == "en" else compile_zh(spec)


def compile_zh(spec: Mapping[str, Any]) -> str:
    brief = spec.get("brief") or {}
    parts: list[str] = []
    usage = str(brief.get("usage") or "").strip()
    if usage:
        parts.append(f"用途：{usage}")
    genre = str(brief.get("genre") or "").strip()
    if genre:
        parts.append(f"体裁：{genre}")
    mood = _joined(brief.get("mood"), "、")
    if mood:
        parts.append(f"情绪：{mood}")
    instruments = _joined(brief.get("instruments"), "、")
    if instruments:
        parts.append(f"乐器：{instruments}")
    bpm = brief.get("bpm")
    if bpm:
        parts.append(f"速度：约 {int(bpm)} BPM")
    duration = spec.get("duration")
    if duration:
        parts.append(f"时长：约 {int(duration)} 秒")
    structure = str(brief.get("structure") or "").strip()
    if structure:
        parts.append(f"结构：{structure}")
    parts.append("纯器乐、无人声、可循环、成品混音")
    avoid = _joined(brief.get("avoid"), "、")
    if avoid:
        parts.append(f"不要：{avoid}")
    return SEP_ZH.join(parts) + "。"


def compile_en(spec: Mapping[str, Any]) -> str:
    brief = spec.get("brief") or {}
    parts: list[str] = []
    usage = str(brief.get("usage") or "").strip()
    if usage:
        parts.append(f"Use: {usage}")
    genre = str(brief.get("genre") or "").strip()
    if genre:
        parts.append(f"Genre: {genre}")
    mood = _joined(brief.get("mood"), ", ")
    if mood:
        parts.append(f"Mood: {mood}")
    instruments = _joined(brief.get("instruments"), ", ")
    if instruments:
        parts.append(f"Instruments: {instruments}")
    bpm = brief.get("bpm")
    if bpm:
        parts.append(f"Tempo: around {int(bpm)} BPM")
    duration = spec.get("duration")
    if duration:
        parts.append(f"Length: about {int(duration)} seconds")
    structure = str(brief.get("structure") or "").strip()
    if structure:
        parts.append(f"Structure: {structure}")
    parts.append("Instrumental only, no vocals, loopable, finished mix")
    avoid = _joined(brief.get("avoid"), ", ")
    if avoid:
        parts.append(f"Avoid: {avoid}")
    return SEP_EN.join(parts) + "."


INTENT_KEYS = ("usage", "genre", "mood", "instruments", "structure")


def has_intent(spec: Mapping[str, Any]) -> bool:
    """brief 里有没有真正说明「要什么音乐」的一项。固定尾句（纯器乐…）与时长不算——
    只有它们的话编译出来的仍是一句谁都能写的废话，不该拿去花钱。"""

    brief = spec.get("brief") or {}
    for key in INTENT_KEYS:
        value = brief.get(key)
        if isinstance(value, list):
            if any(str(v).strip() for v in value):
                return True
        elif str(value or "").strip():
            return True
    return False


def compile_both(spec: Mapping[str, Any]) -> dict[str, str]:
    """两版一起给前端预览（生成用的是 ``prompt_language`` 那一版）。"""

    return {"zh": compile_zh(spec), "en": compile_en(spec)}


__all__ = ["INTENT_KEYS", "has_intent", "compile_prompt", "compile_zh", "compile_en", "compile_both"]
