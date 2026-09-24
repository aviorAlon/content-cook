"""交付包：把项目的创作文档与生产产物打成一个自包含的 zip，给别人（客户、剪辑、存档）。

* ``POST /api/episodes/storyvideo/{id}/export``                    后台任务（大文件拷贝），返回 task_id；结果 {folder, zip, name, files, bytes, deliverable}
* ``GET  /api/episodes/storyvideo/{id}/export``                    已有的交付包列表（名字、大小、时间、manifest 摘要）
* ``GET  /api/episodes/storyvideo/{id}/export/download?name=x.zip`` 下载；名字必须是 09_export 里的纯文件名，否则 404

打包规则在 ``webui/export.py``（白名单拷贝，设置/密钥/锚图输入/账本一律不进包）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from storyvideo.spec import SpecError
from storyvideo.spec import load_spec as load_storyvideo_spec

from .. import export as export_lib
from .. import tasks
from ..deps import PROJECT_ROOT, read_json_bom, resolve_storyvideo_path, spec_not_found_detail

router = APIRouter(tags=["export"])


def _load(episode_id: str) -> tuple[Path, dict[str, Any]]:
    try:
        path, _root = resolve_storyvideo_path(episode_id)
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    return path, spec


@router.get("/api/episodes/storyvideo/{episode_id}/export")
def list_export_packages(episode_id: str) -> dict[str, Any]:
    _path, spec = _load(episode_id)
    packages = export_lib.list_packages(spec)
    for row in packages:
        row["download_url"] = f"/api/episodes/storyvideo/{episode_id}/export/download?name={row['name']}"
    return {"id": episode_id, "packages": packages, "export_dir": str(export_lib.export_root(spec))}


@router.post("/api/episodes/storyvideo/{episode_id}/export")
def start_export(episode_id: str) -> dict[str, Any]:
    path, spec = _load(episode_id)
    raw = read_json_bom(path)
    dest = export_lib.export_root(spec) / export_lib.package_name(spec)
    if dest.exists() or dest.with_suffix(".zip").exists():
        raise HTTPException(409, "这一分钟已经导出过一个交付包了，稍等一分钟再点，或者直接下载已有的那个。")

    def work() -> dict[str, Any]:
        result = export_lib.build_package(spec, raw, dest)
        # 系列交接（DESIGN §32）：导出就是「这一集定稿了」，顺手把它写进系列记忆；没填系列就跳过，写失败不拖累导出
        if spec.get("series"):
            from storyvideo.series import upsert_episode

            try:
                result["series_handoff"] = upsert_episode(PROJECT_ROOT, spec)["series_id"]
            except (OSError, ValueError):
                result["series_handoff"] = None
        return result

    try:
        task_id = tasks.start("export", episode_id, work, params={"dest": str(dest)})
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id}


@router.get("/api/episodes/storyvideo/{episode_id}/export/download")
def download_export_package(episode_id: str, name: str = "") -> FileResponse:
    _path, spec = _load(episode_id)
    target = export_lib.resolve_zip(spec, name)
    if target is None:
        raise HTTPException(404, f"没有这个交付包：{name or '(空)'}——只能下载 09_export 目录里已导出的 zip。")
    return FileResponse(target, media_type="application/zip", filename=target.name)
