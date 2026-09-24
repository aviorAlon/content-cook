"""打包（DESIGN §40，契约 §2 ``export.py``）：按模板 ``export_layout`` 组织的目录 + zip、manifest、
checksums.sha256、只带挑选过的图。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime

import pytest

from imageset import export as E
from imageset import post as P
from imageset import spec as S
from imageset import templates as T
from tests.test_imageset_post import build_spec


def package(spec, **kw) -> dict:
    P.run_post(spec, T.template_for_spec(spec))
    return E.build_package(spec, **kw)


def zip_names(pkg: dict) -> list[str]:
    with zipfile.ZipFile(pkg["zip"]) as zf:
        return sorted(n for n in zf.namelist() if not n.endswith("/"))


# ---------------------------------------------------------------------------- 目录布局


def test_emoji_pack_layout(tmp_path):
    spec = build_spec("sticker", "emoji_pack", tmp_path, items=2, options={"expressions": ["开心", "哭"], "with_caption": False})
    pkg = package(spec)
    from pathlib import Path

    folder = Path(pkg["folder"])
    assert folder.name == pkg["name"][: -len(".zip")] and folder.parent == S.export_dir(spec)
    inside = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    assert inside == ["banner.png", "checksums.sha256", "icon.png", "manifest.json",
                      "stickers/I01.png", "stickers/I02.png", "thumbs/I01.png", "thumbs/I02.png"]
    assert zip_names(pkg) == [f"{folder.name}/{name}" for name in inside]
    assert pkg["files"] == 6 and pkg["bytes"] > 0 and pkg["zip_bytes"] > 0
    assert {row["role"] for row in pkg["items"]} == {"main", "thumb", "banner", "icon"}
    assert {row["path"] for row in pkg["items"]} == set(n for n in inside if n not in ("manifest.json", "checksums.sha256"))


def test_portrait_layout(tmp_path):
    spec = build_spec("portrait", "baby_100", tmp_path, items=2, options={"scenes": ["小马甲", "全家福"], "text": ""})
    pkg = package(spec)
    from pathlib import Path

    folder = Path(pkg["folder"])
    inside = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    assert inside == ["album/I01.png", "album/I02.png", "album_4x3/I01.png", "album_4x3/I02.png",
                      "checksums.sha256", "contact_sheet.jpg", "manifest.json"]
    assert zip_names(pkg) == [f"{folder.name}/{name}" for name in inside]


def test_other_templates_follow_their_layouts(tmp_path):
    from pathlib import Path

    sticker_set = build_spec("sticker", "sticker_set", tmp_path / "a", items=1, options={"themes": ["咖啡"]})
    folder = Path(package(sticker_set)["folder"])
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == \
        ["checksums.sha256", "manifest.json", "stickers/1024/I01.png", "stickers/512/I01.png"]
    avatars = build_spec("sticker", "avatar_set", tmp_path / "b", items=1, options={"styles": ["写实"], "circle": True})
    folder = Path(package(avatars)["folder"])
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == \
        ["avatars/1080/I01.png", "avatars/200/I01.png", "avatars/512/I01.png", "avatars/circle/I01.png",
         "checksums.sha256", "manifest.json"]


# ---------------------------------------------------------------------------- manifest 与 sha256


def test_manifest_and_checksums(tmp_path):
    from pathlib import Path

    spec = build_spec("sticker", "emoji_pack", tmp_path, items=2, options={"expressions": ["开心", "哭"], "with_caption": False})
    pkg = package(spec)
    folder = Path(pkg["folder"])
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == spec["id"] and manifest["module"] == "sticker" and manifest["template"] == "emoji_pack"
    assert manifest["template_name"] == "表情包" and manifest["title"] == "后处理测试" and manifest["style"] == spec["style"]
    assert manifest["file_count"] == 6 and manifest["total_bytes"] == sum(r["bytes"] for r in manifest["files"])
    assert [i["label"] for i in manifest["items"]] == ["开心", "哭"]
    assert {"id": "I01", "label": "开心"} in manifest["items"]
    assert manifest["spec"]["template"] == "emoji_pack" and "items" not in manifest["spec"]
    assert manifest["app_version"] and manifest["exported_at"] and manifest["picks"] == []
    main = next(r for r in manifest["files"] if r["role"] == "main")
    assert main["path"] == f"stickers/{main['item']}.png" and main["width"] == main["height"] == 240 and main["label"]

    lines = (folder / "checksums.sha256").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 7                                    # 6 张产物 + manifest.json（checksums 自己不算）
    for line in lines:
        digest, _, rel = line.partition("  ")
        assert hashlib.sha256((folder / rel).read_bytes()).hexdigest() == digest
    assert E.verify_checksums(folder) == []
    (folder / "manifest.json").write_text("tampered", encoding="utf-8")
    assert E.verify_checksums(folder) == ["manifest.json"]


def test_only_picked_items_are_packaged(tmp_path):
    from pathlib import Path

    spec = build_spec("sticker", "emoji_pack", tmp_path, items=3, options={"expressions": ["开心", "哭", "生气"], "with_caption": False})
    spec["picks"] = ["I02"]
    pkg = package(spec)
    folder = Path(pkg["folder"])
    inside = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    assert inside == ["banner.png", "checksums.sha256", "icon.png", "manifest.json", "stickers/I02.png", "thumbs/I02.png"]
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["picks"] == ["I02"] and [i["id"] for i in manifest["items"]] == ["I02"]


# ---------------------------------------------------------------------------- 前置条件 / 历史 / 下载解析


def test_export_needs_post_outputs(tmp_path):
    spec = build_spec("sticker", "emoji_pack", tmp_path, items=1, options={"expressions": ["开心"], "with_caption": False})
    with pytest.raises(E.ExportError, match="先跑后处理"):
        E.build_package(spec)
    assert E.list_packages(spec) == []
    # 后处理产物文件被删掉（记录还在）→ latest_run 认不到 → 还是拒绝
    P.run_post(spec, T.template_for_spec(spec))
    for path in S.out_dir(spec).glob("*.png"):
        path.unlink()
    with pytest.raises(E.ExportError, match="先跑后处理"):
        E.build_package(spec)


def test_packages_never_collide_and_are_listed_newest_first(tmp_path):
    spec = build_spec("sticker", "sticker_set", tmp_path, items=1, options={"themes": ["咖啡"]})
    now = datetime(2026, 9, 9, 12, 30)
    first = package(spec, now=now)
    second = E.build_package(spec, now=now)
    assert first["name"] == f"{spec['id']}-20260909-1230.zip" and second["name"] == f"{spec['id']}-20260909-1230-2.zip"
    assert E.package_name(spec, now) == f"{spec['id']}-20260909-1230"
    rows = E.list_packages(spec)
    assert {r["name"] for r in rows} == {first["name"], second["name"]}
    assert all(r["size"] > 0 and r["files"] == 2 and r["bytes"] for r in rows)


def test_resolve_zip_rejects_traversal_and_unknown(tmp_path):
    spec = build_spec("sticker", "sticker_set", tmp_path, items=1, options={"themes": ["咖啡"]})
    pkg = package(spec)
    assert E.resolve_zip(spec, pkg["name"]).name == pkg["name"]
    for bad in ("", "../secret.zip", "..\\secret.zip", "sub/dir.zip", "manifest.json", ".", "..", "nope.zip"):
        assert E.resolve_zip(spec, bad) is None
