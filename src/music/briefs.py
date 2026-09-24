"""预设 brief 库（契约 docs/music-spec.md §2）：``skills/ai-music/scripts/music_briefs.json`` 的读取与查找。

跟风格卡库（``imagecraft.cards``）同一形状：文件缺了只是"没有预设可选"，不该让新建页打不开；
一条预设的 ``brief`` 形状**对齐项目文件的 ``brief`` 字段**，选了就整份填进表单，之后随便改。
"""

from __future__ import annotations

import copy
import functools
import json
from typing import Any

from genkit.paths import code_root as _code_root

BRIEFS_PATH = _code_root() / "skills" / "ai-music" / "scripts" / "music_briefs.json"
BRIEF_KEYS = ("usage", "mood", "genre", "instruments", "bpm", "structure", "avoid")


@functools.lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    if not BRIEFS_PATH.is_file():
        return {}
    try:
        with open(BRIEFS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):   # 库坏了 = 没有预设可选，不该让新建页打不开
        return {}
    return data if isinstance(data, dict) else {}


@functools.lru_cache(maxsize=1)
def presets() -> tuple[dict[str, Any], ...]:
    """预设列表；缺 id / name / brief 的条目直接丢掉（半条预设填进表单只会让人困惑）。"""

    rows = _data().get("briefs") or ()
    return tuple(
        p for p in rows
        if isinstance(p, dict) and p.get("id") and p.get("name") and isinstance(p.get("brief"), dict)
    )


def preset_ids() -> list[str]:
    return [str(p["id"]) for p in presets()]


def find_preset(key: str) -> dict[str, Any] | None:
    """按 id（不分大小写）、中文名或英文名找一条预设。"""

    needle = str(key or "").strip().lower()
    if not needle:
        return None
    for preset in presets():
        if needle in {str(preset["id"]).lower(), str(preset.get("name") or "").lower(), str(preset.get("english") or "").lower()}:
            return copy.deepcopy(preset)
    return None


def preset_payload(preset: dict[str, Any]) -> dict[str, Any]:
    """给接口 / 前端的形状：id / 名字 / 一句话 / 建议时长 + 整份 brief（只留认识的键）。"""

    brief = preset.get("brief") or {}
    return {
        "id": str(preset["id"]),
        "name": str(preset.get("name") or preset["id"]),
        "english": str(preset.get("english") or ""),
        "note": str(preset.get("note") or ""),
        "note_en": str(preset.get("note_en") or ""),
        "duration": int(preset["duration"]) if str(preset.get("duration") or "").strip().isdigit() else None,
        "brief": {k: copy.deepcopy(brief.get(k)) for k in BRIEF_KEYS if brief.get(k) not in (None, "", [])},
    }


def payload() -> list[dict[str, Any]]:
    return [preset_payload(p) for p in presets()]


__all__ = ["BRIEFS_PATH", "BRIEF_KEYS", "presets", "preset_ids", "find_preset", "preset_payload", "payload"]
