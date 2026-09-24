"""系列记忆与集间交接（DESIGN §32）：

* ``GET  /api/series``                                       列出系列（id、集数、最新一集）
* ``GET  /api/series/{series_id}``                           一个系列的记忆（各集契约 / 角色 block / 已成立事实 / 未决线索）
* ``POST /api/episodes/storyvideo/{id}/series/handoff``     把这一集写进/更新到系列记忆（已兑现 = lint 在正文里找得到的承诺）

记忆文件在 ``content/series/<series_id>.json``（``storyvideo/series.py``）。起草下一集时 ``/api/scripts/jobs/draft``
带 ``series_id`` 就会把上一集的条目注入提示词，见 ``routers/scripts.py``。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from storyvideo import series as series_lib
from storyvideo.contract import ContractError, normalize_series
from storyvideo.spec import SpecError
from storyvideo.spec import load_spec as load_storyvideo_spec

from ..deps import PROJECT_ROOT, spec_not_found_detail

router = APIRouter(tags=["series"])


@router.get("/api/series")
def list_series() -> list[dict[str, Any]]:
    return series_lib.list_series(PROJECT_ROOT)


@router.get("/api/series/{series_id}")
def get_series(series_id: str) -> dict[str, Any]:
    try:
        normalize_series({"id": series_id})
    except ContractError as exc:
        raise HTTPException(400, str(exc)) from exc
    memory = series_lib.load_series(PROJECT_ROOT, series_id)
    if memory is None:
        raise HTTPException(404, f"没有这个系列：{series_id}")
    return memory


@router.post("/api/episodes/storyvideo/{episode_id}/series/handoff")
def handoff(episode_id: str) -> dict[str, Any]:
    """写这一集的交接条目：契约、角色外形原文、已兑现的承诺（established）、留给下一集的线索（open_threads）。
    不花钱、不改项目文件；导出交付包时也会顺手做一次。"""

    try:
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    try:
        return series_lib.upsert_episode(PROJECT_ROOT, spec)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
