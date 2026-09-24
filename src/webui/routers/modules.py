"""``GET /api/modules``：产品名 + 模块注册表 + 每个模块的项目数（DESIGN §38）。

只读、不联网、不花钱。项目数按模块的 ``project_kinds`` 数对应内容目录里的项目文件——短剧 =
``content/story_video`` 下的 spec 数（跳过剧本层文件）、绘本 = ``content/picturebook`` 下的 json 数；planned 模块没有目录，恒为 0。
路径常量在调用时从 ``deps`` 取，测试 monkeypatch ``deps.STORYVIDEO_DIR`` 就能隔离。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import deps
from ..branding import product_payload
from ..modules import Module, modules_payload

router = APIRouter(tags=["modules"])


def _count_projects(module: Module) -> int:
    if "storyvideo" in module.project_kinds:
        directory = deps.STORYVIDEO_DIR
        if not directory.is_dir():
            return 0
        return sum(1 for p in directory.glob("*.json") if not deps.is_screenplay_file(p))
    # 其余 ready 模块（绘本…）：内容目录下每个 <ID>.json 就是一个项目
    if module.content_dir:
        directory = deps.PROJECT_ROOT / module.content_dir
        if not directory.is_dir():
            return 0
        return sum(1 for p in directory.glob("*.json") if p.is_file())
    return 0


@router.get("/api/modules")
def list_modules() -> dict[str, Any]:
    return {"product": product_payload(), "modules": modules_payload(_count_projects)}
