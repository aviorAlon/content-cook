"""``GET /api/recent``：跨模块的「最近改过的项目」（DESIGN §38）。

工作台首页要一眼看到「我上次在做什么」，而不是先选模块再选项目。这里把每个 ready 模块的内容目录
走一遍（``*.json`` 就是一个项目），按文件 mtime 倒序，返回够画一行的最少字段：模块、图标、id、标题、
前端地址、时间戳。

只读、不联网、不花钱。三条纪律：

* **一个坏文件不能毁掉整张表**——JSON 解析失败、编码不对、stat 失败，都退回「用 id 当标题」或跳过这一条。
* **不整份解析大文件**——标题在文件头部，超过 ``_FULL_PARSE_LIMIT`` 的只在头部正则找一下。
* **剧本层文件不是项目**（``<id>.screenplay.json``，DESIGN §32），跟列表接口一样跳过。

路径常量在调用时从 ``deps`` 取（不是 import 时拷一份），测试 monkeypatch ``deps.PROJECT_ROOT`` 就能隔离。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from .. import deps
from ..modules import MODULES, Module

router = APIRouter(tags=["recent"])

# 项目页地址：跟 frontend/src/router.js 一一对应（改路由时这里也要改，测试逐条比对）
PROJECT_ROUTES: dict[str, str] = {
    "drama": "/drama/p/{id}/overview",
    "picturebook": "/picturebook/b/{id}/overview",
    "sticker": "/sticker/s/{id}/items",
    "portrait": "/portrait/s/{id}/items",
    "image": "/image/s/{id}/items",
    "clip": "/clip/c/{id}/write",
    "music": "/music/m/{id}/generate",
    "podcast": "/podcast/p/{id}/script",
    "script": "/script/p/{id}/write",
}

# 标题字段按这个顺序取第一个非空的（短剧 / 绘本是 title_zh + title_en，其余模块是 title）
TITLE_KEYS = ("title_zh", "title", "title_en")

_FULL_PARSE_LIMIT = 1_000_000        # 超过这个大小只在文件头部找标题
_HEAD_BYTES = 65_536
_TITLE_RE = re.compile(r'"(title_zh|title_en|title)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def project_url(module_id: str, project_id: str) -> str | None:
    template = PROJECT_ROUTES.get(module_id)
    return template.format(id=project_id) if template else None


def _from_head(path: Path) -> str:
    """大文件：只读头部，正则捞标题（捞不到返回空串，调用方退回 id）。"""

    try:
        head = path.read_bytes()[:_HEAD_BYTES].decode("utf-8", "ignore")
    except OSError:
        return ""
    found = {m.group(1): m.group(2) for m in _TITLE_RE.finditer(head)}
    for key in TITLE_KEYS:
        raw = found.get(key)
        if not raw:
            continue
        try:
            text = json.loads(f'"{raw}"')
        except ValueError:
            text = raw
        if str(text).strip():
            return str(text).strip()
    return ""


def read_title(path: Path, project_id: str) -> str:
    """项目标题；文件坏了 / 没有标题字段都退回 id，绝不抛。"""

    try:
        size = path.stat().st_size
    except OSError:
        return project_id
    if size > _FULL_PARSE_LIMIT:
        return _from_head(path) or project_id
    try:
        # 跟各模块的列表接口同一套读法（utf-8-sig）：项目文件历史上有些带 BOM，纯 utf-8 读会当成坏文件退回 id，
        # 于是同一个项目侧栏有标题、首页「最近」只剩 id
        raw = deps.read_json_bom(path)
    except (OSError, ValueError, UnicodeDecodeError):
        return project_id
    if not isinstance(raw, dict):
        return project_id
    for key in TITLE_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return project_id


def _module_dir(module: Module) -> Path | None:
    if not module.content_dir:
        return None
    # 短剧的目录常量单列（测试会 monkeypatch deps.STORYVIDEO_DIR）
    directory = deps.STORYVIDEO_DIR if module.id == "drama" else deps.PROJECT_ROOT / module.content_dir
    return directory if directory.is_dir() else None


def recent_projects(limit: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for module in MODULES:
        if not module.available:
            continue
        directory = _module_dir(module)
        if directory is None:
            continue
        for path in sorted(directory.glob("*.json")):
            if deps.is_screenplay_file(path) or not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            project_id = path.stem
            try:
                rel = str(path.relative_to(deps.PROJECT_ROOT)).replace("\\", "/")
            except ValueError:
                rel = str(path)
            rows.append({
                "module": module.id,
                "module_name": module.name,
                "module_name_en": module.english,
                "icon": module.icon,
                "id": project_id,
                "title": read_title(path, project_id),
                "url": project_url(module.id, project_id),
                "mtime": mtime,
                "path": rel,
            })
    rows.sort(key=lambda r: (r["mtime"], r["id"]), reverse=True)
    return rows[:limit]


@router.get("/api/recent")
def list_recent(limit: int = Query(12, ge=1, le=100)) -> list[dict[str, Any]]:
    return recent_projects(limit)


__all__ = ["PROJECT_ROUTES", "TITLE_KEYS", "project_url", "read_title", "recent_projects", "router"]
