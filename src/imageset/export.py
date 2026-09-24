"""打包（契约 §2 ``export.py``）：``09_export/<ID>-<时间>/`` + 同名 zip。

按模板 ``export_layout``（role → 包内路径，``{item}`` = 图片 id，``{label}`` = 标签压成的文件名）把最新一次后处理的产物摆进去；表情包 = ``stickers/`` +
``thumbs/`` + ``banner.png`` + ``icon.png``，写真 = ``album/`` + ``album_4x3/`` + ``contact_sheet.jpg``；再加一份 ``manifest.json``
（id / 标题 / 模板 / 每张的标签与文件）与 ``checksums.sha256``。只读 + 复制，产物目录之外的文件一个都不碰（跟交付包同一规矩）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from genkit.ids import slugify
from genkit.version import __version__

from .post import latest_run
from .spec import export_dir, out_dir, to_disk
from .templates import template_for_spec


class ExportError(RuntimeError):
    pass


def package_name(spec: Mapping[str, Any], now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"{spec.get('id')}-{stamp}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, boundary: Path) -> bool:
    try:
        return path.resolve().is_relative_to(boundary.resolve())
    except OSError:
        return False


def _free_dir(root: Path, name: str) -> Path:
    dest = root / name
    n = 2
    while dest.exists() or dest.with_suffix(".zip").exists():
        dest = root / f"{name}-{n}"
        n += 1
    return dest


def build_package(spec: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """返回 ``{folder, zip, name, files, bytes, zip_bytes, items}``。没有后处理产物 → ExportError。"""

    template = template_for_spec(spec)
    run = latest_run(spec)
    if not run:
        raise ExportError("还没有后处理产物，先跑后处理")
    source_root = out_dir(spec)
    dest = _free_dir(export_dir(spec), package_name(spec, now))
    dest.mkdir(parents=True)
    labels = {str(i["id"]): str(i.get("label") or "") for i in spec.get("items") or ()}
    rows: list[dict[str, Any]] = []
    for output in run["outputs"]:
        pattern = template.export_layout.get(str(output.get("role")))
        if not pattern:
            continue
        src = source_root / str(output["file"])
        if not src.is_file() or src.is_symlink() or not _inside(src, source_root):
            continue
        item_id = str(output.get("item") or "")
        # 包内路径按 role 的模板拼：``{item}`` = 图片 id，``{label}`` = 这张图的标签压成文件名（多模型对比按模型名归档）
        rel = pattern.format(item=item_id, label=slugify(labels.get(item_id, ""), fallback=item_id or "file"))
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        rows.append({"item": output.get("item"), "label": labels.get(str(output.get("item") or ""), ""), "role": output["role"],
                     "path": rel, "width": output.get("width"), "height": output.get("height"), "bytes": target.stat().st_size})
    if not rows:
        shutil.rmtree(dest, ignore_errors=True)
        raise ExportError("后处理产物里没有可打包的文件")
    manifest = {
        "schema_version": 1, "id": spec.get("id"), "module": spec.get("module"), "title": spec.get("title") or "",
        "template": template.id, "template_name": template.name, "style": spec.get("style"),
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"), "app_version": __version__,
        "picks": list(spec.get("picks") or []), "items": [{"id": k, "label": v} for k, v in labels.items() if k in run.get("items", [])],
        "files": rows, "file_count": len(rows), "total_bytes": sum(r["bytes"] for r in rows),
        "spec": {k: v for k, v in to_disk(spec).items() if k != "items"},
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"{_sha256(p)}  {p.relative_to(dest).as_posix()}" for p in sorted(x for x in dest.rglob("*") if x.is_file())]
    (dest / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    zip_path = Path(shutil.make_archive(str(dest), "zip", root_dir=str(dest.parent), base_dir=dest.name))
    return {"folder": str(dest), "zip": str(zip_path), "name": zip_path.name, "files": len(rows), "bytes": manifest["total_bytes"],
            "zip_bytes": zip_path.stat().st_size, "items": rows}


def verify_checksums(folder: Path) -> list[str]:
    root = Path(folder)
    bad: list[str] = []
    for line in (root / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _sep, rel = line.partition("  ")
        target = root / rel
        if not target.is_file() or _sha256(target) != digest:
            bad.append(rel)
    return bad


def list_packages(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    root = export_dir(spec)
    if not root.is_dir():
        return []
    rows = []
    for zip_path in root.glob("*.zip"):
        if not zip_path.is_file():
            continue
        stat = zip_path.stat()
        row: dict[str, Any] = {"name": zip_path.name, "size": stat.st_size,
                               "created": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
                               "files": None, "bytes": None}
        manifest_path = root / zip_path.stem / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                row["files"] = manifest.get("file_count")
                row["bytes"] = manifest.get("total_bytes")
            except (OSError, ValueError):
                pass
        rows.append(row)
    rows.sort(key=lambda r: r["created"], reverse=True)
    return rows


def resolve_zip(spec: Mapping[str, Any], name: str) -> Path | None:
    if not name or name != Path(name).name or "/" in name or "\\" in name or name in (".", "..") or not name.endswith(".zip"):
        return None
    root = export_dir(spec)
    target = root / name
    if not target.is_file() or target.is_symlink() or not _inside(target, root):
        return None
    return target


__all__ = ["ExportError", "package_name", "build_package", "verify_checksums", "list_packages", "resolve_zip"]
