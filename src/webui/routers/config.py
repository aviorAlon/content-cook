"""基础配置管理：镜头语言库 / 风格骨架库的读写——拆分自 app.py（见 deps.py 头部
注释）。这两份 JSON 是所有项目共用的库，写盘前都要跑一遍一致性检查
（``storyvideo.config_validate``），过不了直接拒绝、不写盘；写盘后清掉
``lru_cache``，不然当前进程还在用旧数据，得等重启才生效。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from storyvideo.config_validate import validate_camera_library, validate_style_library
from storyvideo.prompts import CAMERA_LIBRARY_PATH, camera_library
from storyvideo.scriptgen import STYLE_LIBRARY_PATH, style_library
from storyvideo.state import atomic_write_json

router = APIRouter()


@router.get("/api/config/camera-library")
def get_camera_library_config() -> dict[str, Any]:
    """镜头语言库的原始 JSON——给「基础配置」管理页用，不做任何裁剪。"""
    return camera_library()


class LibraryUpdate(BaseModel):
    data: dict[str, Any]


@router.put("/api/config/camera-library")
def update_camera_library_config(req: LibraryUpdate) -> dict[str, Any]:
    """镜头语言库是所有项目共用的基础配置，改错了会波及所有项目的起草/校验——

    写盘前跑一遍一致性检查（``storyvideo.config_validate``：id 不重复、
    profile 引用的 id 真实存在），过不了直接拒绝、不写盘。写盘后清掉内存缓存，
    不然这个进程还在用旧数据，得等重启才生效。
    """
    errors = validate_camera_library(req.data)
    if errors:
        raise HTTPException(400, "；".join(errors))
    atomic_write_json(CAMERA_LIBRARY_PATH, req.data)
    camera_library.cache_clear()
    return {"ok": True}


@router.get("/api/config/style-library")
def get_style_library_config() -> dict[str, Any]:
    """风格骨架库的原始 JSON——给「基础配置」管理页用。"""
    return style_library()


@router.put("/api/config/style-library")
def update_style_library_config(req: LibraryUpdate) -> dict[str, Any]:
    """同 update_camera_library_config，换成风格骨架库那一套一致性检查。"""
    errors = validate_style_library(req.data)
    if errors:
        raise HTTPException(400, "；".join(errors))
    atomic_write_json(STYLE_LIBRARY_PATH, req.data)
    style_library.cache_clear()
    return {"ok": True}
