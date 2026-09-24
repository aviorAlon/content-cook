"""系列记忆与集间交接（DESIGN §32，融合 drama-skills「集间状态交接 / 可恢复的跨集记忆」的判据）。

drama-skills 的原则：交接写成事实不写气氛词（谁知道了什么、谁拿着什么、哪个危险已经开始）；跨集记忆只保存后续
决策所需的内容，只写变化；新一集开写前先读活跃记忆。我们把它落成一个 JSON 文件：

    content/series/<series_id>.json = {
        "id": "<series_id>",
        "episodes": [{
            "episode_id": "EP-2", "episode_no": 2, "title": "…",
            "contract": {premise, hook, cliffhanger, must_pay_off, carry_over},
            "cast": {"lin": {"name": "林小满", "block": "…外形描述原文…"}},
            "established": ["本集已兑现、下一集不得推翻的事实"],
            "open_threads": ["留给下一集的未决事实"],
            "updated_at": 1700000000.0
        }]
    }

写入时机：人点「系列交接」，或导出交付包时顺手更新。起草下一集时（``series_id`` 给了）把上一集的条目压成一段
「上一集交接」进用户提示词：角色 block **原样**贴（身份不漂），open_threads 列出来要接住，established 列出来不得矛盾；
草稿回来后同 id 的角色 block 再用记忆里的覆盖一次——模型改写了也拉回来。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from .contract import DEFERRED_LABEL, deferred_text, normalize_series, payoff_status

SERIES_SUBDIR = ("content", "series")


def series_dir(project_root: Path | str) -> Path:
    return Path(project_root).joinpath(*SERIES_SUBDIR)


def series_path(project_root: Path | str, series_id: str) -> Path:
    normalize_series({"id": series_id})   # 名字不合法就在这里拦，不让它变成路径
    return series_dir(project_root) / f"{series_id}.json"


def load_series(project_root: Path | str, series_id: str) -> dict[str, Any] | None:
    path = series_path(project_root, series_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, Mapping):
        return None
    episodes = [e for e in (data.get("episodes") or ()) if isinstance(e, Mapping)]
    return {"id": str(data.get("id") or series_id), "episodes": [dict(e) for e in episodes]}


def list_series(project_root: Path | str) -> list[dict[str, Any]]:
    """``[{id, episodes: 集数, latest_no, latest_title}]``，按 id 排序。坏文件跳过。"""

    root = series_dir(project_root)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        memory = load_series(project_root, path.stem) if _safe_stem(path.stem) else None
        if memory is None:
            continue
        latest = _latest(memory["episodes"])
        out.append({
            "id": memory["id"],
            "episodes": len(memory["episodes"]),
            "latest_no": latest.get("episode_no") if latest else None,
            "latest_title": str(latest.get("title") or "") if latest else "",
        })
    return out


def _safe_stem(stem: str) -> bool:
    try:
        normalize_series({"id": stem})
        return True
    except ValueError:
        return False


def _sort_key(entry: Mapping[str, Any]) -> tuple[float, float]:
    no = entry.get("episode_no")
    return (float(no) if isinstance(no, (int, float)) and not isinstance(no, bool) else float("inf"),
            float(entry.get("updated_at") or 0))


def _latest(episodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not episodes:
        return None
    return max(episodes, key=_sort_key)


def previous_entry(project_root: Path | str, series_id: str, episode_no: int | None) -> dict[str, Any] | None:
    """起草第 ``episode_no`` 集时该接谁：集数比它小的里最大的一集；没给集数就取最新一集。"""

    memory = load_series(project_root, series_id)
    if memory is None or not memory["episodes"]:
        return None
    candidates = memory["episodes"]
    if isinstance(episode_no, int) and not isinstance(episode_no, bool):
        candidates = [e for e in candidates if isinstance(e.get("episode_no"), int) and e["episode_no"] < episode_no]
    return _latest(candidates)


def _cast_memory(spec: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for cid, member in (spec.get("cast") or {}).items():
        if not isinstance(member, Mapping):
            continue
        block = str(member.get("block") or "").strip()
        if not block:
            continue
        out[str(cid)] = {"name": str(member.get("name") or "").strip(), "block": block}
    return out


def build_entry(spec: Mapping[str, Any]) -> dict[str, Any]:
    """从一份（原始或归一化后的）spec 算出它在系列记忆里的条目。``series`` 缺了抛 ValueError。"""

    series = normalize_series(spec.get("series"))
    if series is None:
        raise ValueError("这个项目没有填系列（series.id）——在概览页「单集契约」卡里填好再交接")
    contract = spec.get("episode_contract") if isinstance(spec.get("episode_contract"), Mapping) else None
    paid, _unpaid, deferred = payoff_status(spec)
    # 标了「有意留白」的承诺不算已兑现：记进未决，标记统一写成「（有意留白）」，下一集起草时能看见它是作者故意留的
    threads = list((contract or {}).get("carry_over") or [])
    threads += [f"{deferred_text(item)}{DEFERRED_LABEL}" for item in deferred]
    return {
        "episode_id": str(spec.get("id") or ""),
        "episode_no": series.get("episode_no"),
        "title": str(spec.get("title_zh") or spec.get("title_en") or ""),
        "contract": dict(contract) if contract else None,
        "cast": _cast_memory(spec),
        "established": paid,
        "open_threads": threads,
        "updated_at": time.time(),
    }


def upsert_episode(project_root: Path | str, spec: Mapping[str, Any]) -> dict[str, Any]:
    """写入/更新这一集的条目（按 episode_id 找旧条目替换），返回 ``{series_id, path, entry, episodes}``。"""

    entry = build_entry(spec)
    series_id = normalize_series(spec.get("series"))["id"]   # build_entry 已保证存在
    path = series_path(project_root, series_id)
    memory = load_series(project_root, series_id) or {"id": series_id, "episodes": []}
    episodes = [e for e in memory["episodes"] if e.get("episode_id") != entry["episode_id"]]
    episodes.append(entry)
    episodes.sort(key=_sort_key)
    memory["episodes"] = episodes
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {"series_id": series_id, "path": str(path), "entry": entry, "episodes": len(episodes)}


def handoff_section(entry: Mapping[str, Any]) -> str:
    """把上一集条目压成用户提示词里的「上一集交接」段。角色 block 原样贴，不改一个字。"""

    lines = ["上一集交接（系列记忆，不是本集内容；接住它，不要重讲）："]
    head = f"- 上一集：{entry.get('episode_id') or ''}"
    if entry.get("episode_no"):
        head += f"（第 {entry['episode_no']} 集）"
    if entry.get("title"):
        head += f"《{entry['title']}》"
    lines.append(head)
    contract = entry.get("contract") if isinstance(entry.get("contract"), Mapping) else {}
    if contract.get("cliffhanger"):
        lines.append(f"- 上一集结尾（本集开场要从这里接）：{contract['cliffhanger']}")
    threads = [str(t) for t in (entry.get("open_threads") or ()) if str(t).strip()]
    if threads:
        lines.append("- 未决事实（本集要接住，至少推进其中一条）：")
        lines.extend(f"  - {t}" for t in threads)
    established = [str(t) for t in (entry.get("established") or ()) if str(t).strip()]
    if established:
        lines.append("- 已成立的事实（不得矛盾、不得重置）：")
        lines.extend(f"  - {t}" for t in established)
    cast = entry.get("cast") if isinstance(entry.get("cast"), Mapping) else {}
    if cast:
        lines.append("- 延续的角色（cast 用同一个 id，block 原样照抄，一个字都不要改；只有新角色才写新的）：")
        for cid, member in cast.items():
            if not isinstance(member, Mapping):
                continue
            name = str(member.get("name") or "").strip()
            label = f"{cid}（{name}）" if name else str(cid)
            lines.append(f"  - {label}：{member.get('block', '')}")
    return "\n".join(lines)


def apply_cast_memory(draft: dict[str, Any], entry: Mapping[str, Any]) -> list[str]:
    """草稿里跟记忆同 id 的角色，block（和 name）用记忆里的覆盖——身份不随模型改写漂移。返回被覆盖的 id。"""

    cast = draft.get("cast")
    memory = entry.get("cast") if isinstance(entry.get("cast"), Mapping) else {}
    if not isinstance(cast, dict) or not memory:
        return []
    touched: list[str] = []
    for cid, remembered in memory.items():
        if not isinstance(remembered, Mapping) or cid not in cast or not isinstance(cast[cid], dict):
            continue
        block = str(remembered.get("block") or "").strip()
        if not block:
            continue
        cast[cid]["block"] = block
        if remembered.get("name") and not cast[cid].get("name"):
            cast[cid]["name"] = remembered["name"]
        touched.append(str(cid))
    return touched


__all__ = [
    "series_dir", "series_path", "load_series", "list_series", "previous_entry",
    "build_entry", "upsert_episode", "handoff_section", "apply_cast_memory",
]
