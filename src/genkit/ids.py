"""内部 id 的自动生成：用户只填名字，id 是程序的事。

所有 id 都满足 ``[A-Za-z0-9_][A-Za-z0-9_.-]*``（文件名、URL、spec 引用都安全）。名字里有英文/数字就
压成 slug（"Girl Calm" → girl_calm）；纯中文名压不出东西时退到 ``<前缀><序号>``（cast1、voice2）。
同名/撞 id 时追加 ``-2``、``-3``。镜次 id 按 ``S<序号>`` 递增（现有 S3_LOOK 算 3）。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable

ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}\Z")
_SHOT_NUM = re.compile(r"[Ss](\d+)")


def is_valid_id(value: str) -> bool:
    return bool(ID_RE.fullmatch(str(value or "")))


def slugify(text: str, *, fallback: str = "") -> str:
    """只留 ASCII 字母数字，其余压成下划线；压不出东西返回 fallback。"""

    raw = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_").lower()
    raw = raw[:40]
    if raw and not re.match(r"[A-Za-z0-9_]", raw[0]):
        raw = "_" + raw
    return raw or fallback


def unique_id(base: str, taken: Iterable[str]) -> str:
    """base 没被占就用 base，否则 base-2、base-3…"""

    used = {str(x) for x in taken}
    if base not in used:
        return base
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    return f"{base}-{n}"


def numbered_id(prefix: str, taken: Iterable[str]) -> str:
    """prefix1、prefix2…里第一个没被占的。"""

    used = {str(x) for x in taken}
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def id_from_name(name: str, taken: Iterable[str], *, prefix: str) -> str:
    """名字 → id：能 slug 就 slug（去重），否则 prefix + 序号。"""

    taken = list(taken)
    slug = slugify(name)
    if slug:
        return unique_id(slug, taken)
    return numbered_id(prefix, taken)


def next_shot_id(existing: Iterable[str]) -> str:
    """S1、S2…：取现有镜 id 里 S 后面数字的最大值 + 1（S3_LOOK 算 3，别的形状忽略）。"""

    highest = 0
    ids = [str(x) for x in existing]
    for sid in ids:
        match = _SHOT_NUM.match(sid)
        if match:
            highest = max(highest, int(match.group(1)))
    candidate = f"S{highest + 1}"
    return unique_id(candidate, ids)


def next_episode_id(existing: Iterable[str], *, prefix: str = "EP", today: date | None = None) -> str:
    """EP-20260907-1、EP-20260907-2…（按天编号，看一眼就知道哪天建的）。"""

    stamp = (today or date.today()).strftime("%Y%m%d")
    return numbered_id(f"{prefix}-{stamp}-", existing)


__all__ = ["ID_RE", "is_valid_id", "slugify", "unique_id", "numbered_id", "id_from_name", "next_shot_id", "next_episode_id"]
