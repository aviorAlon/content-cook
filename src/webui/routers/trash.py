"""回收站接口：跨模块列出删除（归档）过的项目、原样恢复、彻底删除。

* ``GET    /api/deleted[?module=]``                  ``.webui_deleted/`` 里的归档（新的在前）：标题 / 删除时间 / 大小 / 能不能恢复。只读
* ``POST   /api/deleted/{module}/{entry}/restore``   放回原位；同 id 的项目已存在、原位被占 → 409 说清原因，什么都不动
* ``DELETE /api/deleted/{module}/{entry}``           彻底删除这一条归档（不可恢复；界面上要二次确认）

各模块的删除接口都只是归档（``genkit.trash.archive``，短剧在 episodes.delete_episode），以前界面上只有短剧能从
「已删除的项目」里恢复，其它模块的确认框只能教人「手动搬回来」。短剧的恢复要把剧本、锚图、账本行各归各位，
实现在 ``episodes.restore_archive``；其它模块走 ``genkit.trash.restore``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from genkit import trash

from .. import deps
from . import episodes

router = APIRouter()


def _root() -> Path:
    return deps.PROJECT_ROOT


def _storyvideo_rows() -> list[dict[str, Any]]:
    # 短剧的归档目录由 episodes 自己记着（测试把它单独指到临时目录）；正常运行时跟数据根下的 .webui_deleted 是同一个
    rows = trash.list_entries(episodes.PROJECT_ROOT, trash.STORYVIDEO, root=episodes.DELETED_DIR)
    for row in rows:
        # 只剩账本记录的归档本来就要求项目文件已经在原位，不算「同 id 被占」
        row["id_taken"] = (not row["ledger_only"]) and (episodes.STORYVIDEO_DIR / f"{row['id']}.json").exists()
    return rows


@router.get("/api/deleted")
def list_deleted(module: str | None = None) -> list[dict[str, Any]]:
    """列出归档，新的在前。``module`` 不填列全部模块（短剧的归档目录名是 storyvideo）。

    ``id_taken``：同 id 的项目现在就在——这时恢复会 409，界面先把原因写在这一行上，不用点了才知道。"""

    rows: list[dict[str, Any]] = []
    if module in (None, "", trash.STORYVIDEO):
        rows += _storyvideo_rows()
    if module != trash.STORYVIDEO:
        # 短剧的归档上面已经列过：这里连目录都不进（它的产物最大，多走一遍磁盘再丢掉，回收站页每次打开都要付）
        for row in trash.list_entries(_root(), module or None, exclude=(trash.STORYVIDEO,)):
            row["id_taken"] = row["module"] in trash.GENERIC_MODULES and trash.spec_target(_root(), row["module"], row["id"]).exists()
            rows.append(row)
    rows.sort(key=lambda item: (item["archived_at"], item["entry"]), reverse=True)
    return rows


@router.post("/api/deleted/{module}/{entry}/restore")
def restore_deleted(module: str, entry: str) -> dict[str, Any]:
    """把一条归档放回原位。双击、恢复的同时点彻底删除都会在线程池里并发：整个过程拿回收站的锁。"""

    with trash.LOCK:
        if module == trash.STORYVIDEO:
            return {"module": module, **episodes.restore_archive(entry)}
        try:
            return trash.restore(_root(), module, entry)
        except trash.TrashError as exc:
            raise HTTPException(exc.status, str(exc)) from exc


@router.delete("/api/deleted/{module}/{entry}")
def purge_deleted(module: str, entry: str) -> dict[str, Any]:
    """彻底删除一条归档（项目文件、产物、素材、短剧的账本记录一起从磁盘上删掉，不可恢复）。"""

    root = episodes.DELETED_DIR if module == trash.STORYVIDEO else None
    try:
        return trash.purge(_root(), module, entry, root=root)
    except trash.TrashError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
