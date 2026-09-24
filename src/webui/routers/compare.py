"""首帧对比试跑接口：出候选（后台任务，花钱）、列候选、看图、采用。语义见 webui.compare。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from storyvideo.spec import SpecError

from .. import compare as C
from .. import tasks
from ..deps import RUNNER_ERRORS, require_roles, spec_not_found_detail
from ..humanize import humanize
from . import post as post_router

router = APIRouter(tags=["compare"])


class CompareRequest(BaseModel):
    shot_id: str
    models: list[str]


@router.post("/api/episodes/storyvideo/{episode_id}/compare/frames")
def start_compare(episode_id: str, req: CompareRequest) -> dict[str, Any]:
    """真实调用生图接口，每个模型一次请求。不走账本，产物只在 09_compare/。"""

    _path, spec = post_router._load(episode_id)
    models = [m.strip() for m in req.models if m.strip()]
    if not models:
        raise HTTPException(400, "至少给一个模型 id")
    if not any(str(s["id"]) == req.shot_id for s in spec["shots"]):
        raise HTTPException(404, f"没有这一镜：{req.shot_id}")
    # 起任务前先查生图角色（2026-09-22 审计 DRAMA-15）：没配时每个候选都会失败，任务却记成 succeeded
    require_roles(C.build_provider(), "image")
    try:
        task_id = tasks.start("compare", f"{episode_id}:{req.shot_id}", lambda: C.run_candidates(episode_id, req.shot_id, models),
                              params={"models": models})
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id, "requests": len(models)}


@router.get("/api/episodes/storyvideo/{episode_id}/compare/frames/{shot_id}")
def list_compare(episode_id: str, shot_id: str) -> dict[str, Any]:
    _path, spec = post_router._load(episode_id)
    return {"shot_id": shot_id, "candidates": C.list_candidates(spec, shot_id)}


@router.get("/api/episodes/storyvideo/{episode_id}/compare/frames/{shot_id}/file/{name}")
def compare_file(episode_id: str, shot_id: str, name: str) -> FileResponse:
    _path, spec = post_router._load(episode_id)
    path = C.compare_dir(spec, shot_id) / Path(name).name
    if not path.is_file():
        raise HTTPException(404, "候选不存在")
    return FileResponse(path, media_type="image/png")


class AdoptRequest(BaseModel):
    name: str
    reason: str = ""


@router.post("/api/episodes/storyvideo/{episode_id}/compare/frames/{shot_id}/adopt")
def adopt_candidate(episode_id: str, shot_id: str, req: AdoptRequest) -> dict[str, Any]:
    try:
        return C.adopt(episode_id, shot_id, req.name, reason=req.reason)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    except C.CompareError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RUNNER_ERRORS as exc:
        raise HTTPException(400, f"采用失败：{humanize(exc)}") from exc
