"""「保存并预告费用」接口：对已开始制作的项目，先预览一次编辑会作废哪些镜，再复制成新版本只重出受影响的镜。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from storyvideo.spec import SpecError

from .. import revise as R
from ..deps import RUNNER_ERRORS, SpecWriteBusy, spec_not_found_detail
from ..humanize import humanize

router = APIRouter(tags=["revise"])


class PreviewRequest(BaseModel):
    mutation: dict[str, Any]


class ApplyRequest(PreviewRequest):
    new_episode_id: str


@router.post("/api/episodes/storyvideo/{episode_id}/revise-edit/preview")
def revise_preview(episode_id: str, req: PreviewRequest) -> dict[str, Any]:
    try:
        return R.preview(episode_id, req.mutation)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    except (R.ReviseError, ValueError) as exc:
        raise HTTPException(400, humanize(exc)) from exc


@router.post("/api/episodes/storyvideo/{episode_id}/revise-edit/apply")
def revise_apply(episode_id: str, req: ApplyRequest) -> dict[str, Any]:
    try:
        return R.apply(episode_id, req.mutation, req.new_episode_id)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    except SpecWriteBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except R.ReviseError as exc:
        raise HTTPException(400, humanize(exc)) from exc
    except RUNNER_ERRORS as exc:
        raise HTTPException(400, f"新版本已写出但进入制作失败：{humanize(exc)}") from exc
