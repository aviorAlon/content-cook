"""题材 × 镜头风格的搭配提示（DESIGN §48）。

数据在 ``skills/ai-short-film/scripts/genre_camera_pairing.json``：按题材卡 id 给
``{recommended: [profile id], avoid: [profile id], why: 一句话}``。这张表是本仓库人工设定的
（``_meta.basis``），不是任何来源的蒸馏，所以只做提示——lint 只在「avoid」时给一条软告警，
既不推荐也不回避的组合什么都不说；不拦截、不进提示词。

跟题材卡一样是可选增强：文件缺了或读坏了，所有调用方拿到的都是空搭配，功能照常。
"""

from __future__ import annotations

import json
from typing import Any

from genkit.paths import code_root as _code_root

PAIRING_PATH = _code_root() / "skills" / "ai-short-film" / "scripts" / "genre_camera_pairing.json"


def pairing_table() -> dict[str, dict[str, Any]]:
    """整张搭配表：题材卡 id → 原始行。文件不在 / 读不出来 → 空表。"""

    if not PAIRING_PATH.is_file():
        return {}
    try:
        with open(PAIRING_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    rows = data.get("pairings") if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        return {}
    return {str(k): v for k, v in rows.items() if isinstance(v, dict)}


def pairing_for(genre_id: str | None) -> dict[str, Any]:
    """一张题材卡的搭配（总是三个键；没有这张卡的行就是空列表 + 空理由）。"""

    row = pairing_table().get(str(genre_id or "").strip()) or {}
    return {
        "recommended": [str(p) for p in (row.get("recommended") or ()) if str(p).strip()],
        "avoid": [str(p) for p in (row.get("avoid") or ()) if str(p).strip()],
        "why": str(row.get("why") or "").strip(),
    }


def pairing_verdict(genre_id: str | None, camera_profile: str | None) -> str:
    """``"recommended"`` / ``"avoid"`` / ``""``（既不推荐也不回避，或者缺一边）。"""

    profile = str(camera_profile or "").strip()
    if not profile:
        return ""
    entry = pairing_for(genre_id)
    if profile in entry["avoid"]:
        return "avoid"
    if profile in entry["recommended"]:
        return "recommended"
    return ""


def pairing_warning(genre_name: str, camera_profile: str, entry: dict[str, Any]) -> str:
    """「全集：影响——改法」形状的软告警；改法里列这个题材常配的档（不含当前这档）。"""

    others = [p for p in entry.get("recommended") or () if p != camera_profile]
    fix = f"换成 {' / '.join(others)}，或确认这就是想要的反差" if others else "换一档镜头风格，或确认这就是想要的反差"
    return f"全集：题材「{genre_name}」配镜头风格「{camera_profile}」容易打架：{entry.get('why') or ''}——{fix}"


__all__ = ["PAIRING_PATH", "pairing_table", "pairing_for", "pairing_verdict", "pairing_warning"]
