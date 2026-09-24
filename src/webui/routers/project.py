"""以资源为中心的项目接口：角色与场景、锚图、引用关系图、项目概览与下一步。

这是「资源为中心的工作台」重构的后端地基（DESIGN §11）。spec 的数据模型不变，
这里只是把它按用户能理解的对象重新切开并补上双向引用：

* 角色 / 场景 = ``spec.cast`` 的条目（有 name/voice 的是角色，只有 block 的是场景或道具；
  可以显式写 ``kind``）；每条带 ``used_in``（哪些镜引用了它）。
* 锚图 = ``cast.<id>.asset``：上传后存到 ``content/assets/<项目>/``，spec 里记相对路径。
* 关系图：镜 → 角色/场景、镜 → 参考镜、旁白/画面文字 → 镜、插卡 → 镜或插卡。
* 概览：每一道的完成数、审片状态、成片状态，以及「下一步该做什么」。

开始制作（冻结）之后，角色描述与锚图会进提示词/参考图，属于会影响生成的字段——
现阶段直接拒绝并列出受影响的镜次；「保存并预告费用」的语义是重构第 3 步的事。
"""

from __future__ import annotations

import base64
import copy
import hashlib
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from genkit.ids import slugify
from storyvideo.post_layers import START_ANCHOR
from storyvideo.spec import SpecError
from storyvideo.spec import load_spec as load_storyvideo_spec
from storyvideo.spec import normalize_spec as normalize_storyvideo_spec
from storyvideo.spec import split_cast_ref
from storyvideo.state import atomic_write_json, make_step_id

from ..deps import (
    FREEZE_MISMATCH_HINTS,
    PROJECT_ROOT,
    _deliverable_path,
    asr_rows,
    freeze_state,
    is_locked,
    qc_decision,
    qc_meta,
    read_json_bom,
    resolve_storyvideo_path,
    spec_not_found_detail,
)
from ..modules import module_for_pipeline
from ..uploads import UploadContentError, accept_image

router = APIRouter(tags=["project"])

CAST_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}\Z")
CAST_KINDS = ("character", "scene", "prop")
MAX_ASSET_BYTES = 20 * 1024 * 1024
ASSET_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _form_summary(value: Any) -> dict[str, str] | None:
    """概览页头部的形态标签（DESIGN §31）：spec.form 解析成 {id, name}；没写或找不到卡 = None。"""

    from storyvideo.forms import find_form

    card = find_form(value)
    return {"id": str(card["id"]), "name": str(card["name"])} if card else None


def _genre_summary(value: Any) -> dict[str, str] | None:
    """概览页头部的题材下拉（DESIGN §49）：spec.genre 解析成 {id, name}；没写或找不到卡 = None。"""

    from storyvideo.scriptgen import find_genre

    card = find_genre(value) if isinstance(value, str) else None
    return {"id": str(card["id"]), "name": str(card["name"])} if card else None


def _load(episode_id: str) -> tuple[Path, dict[str, Any]]:
    try:
        path, _root = resolve_storyvideo_path(episode_id)
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    return path, spec


def cast_kind(member: dict[str, Any]) -> str:
    explicit = str(member.get("kind") or "").strip()
    if explicit in CAST_KINDS:
        return explicit
    return "character" if (member.get("name") or member.get("voice")) else "scene"


def shots_using(spec: dict[str, Any], cast_id: str, variant: str | None = None) -> list[str]:
    """引用了这个成员的镜（``"lin@rain"`` 也算 lin 出场）；给 ``variant`` 时只数该变体。"""

    out: list[str] = []
    for s in spec.get("shots", ()):
        if not isinstance(s, dict):
            continue
        for ref in s.get("cast") or ():
            member_id, key = split_cast_ref(ref)
            if member_id == cast_id and (variant is None or key == variant):
                out.append(str(s["id"]))
                break
    return out


def normalize_asset_path(asset: Any, label: str) -> str:
    """锚图路径只收项目内的相对路径（content/assets/…）：绝对路径、``local:`` 令牌、``..`` 都拒（ValueError，接口层转 400）。

    成员锚图与变体锚图同一条规则（DESIGN §23 只给变体写了，成员这边曾经漏掉）：写进去的路径会被
    GET …/asset 原样 FileResponse、还会作为首帧参考图随请求上传——不限制就是本机任意文件读。反斜杠统一成 ``/``。
    """

    text = str(asset or "").strip().replace("\\", "/")
    # ~（主目录）和 Windows 的盘符相对写法（C:x.png——is_absolute() 认为它不是绝对路径）以前漏了（PB-3）；
    # 规则跟 storyvideo.spec.project_file 的字面检查一致
    windows = PureWindowsPath(text)
    if (Path(text).is_absolute() or text.startswith(("local:", "/", "~")) or windows.drive or windows.root
            or ".." in PurePosixPath(text).parts):
        raise ValueError(f"{label} 只能是项目内的相对路径（content/assets/…）")
    return text


def _asset_file(asset: str) -> Path | None:
    """spec 里记的锚图路径 → 磁盘文件；不在项目 content/ 之下（绝对路径、``local:``、``..``、手改的 spec）一律 None。
    读出侧也守边界：写入侧的校验挡不住直接改 spec 文件的人。"""

    try:
        relative = normalize_asset_path(asset, "asset")
    except ValueError:
        return None
    if not relative:
        return None
    resolved = (PROJECT_ROOT / relative).resolve()
    if not resolved.is_relative_to((PROJECT_ROOT / "content").resolve()):
        return None
    return resolved


def _asset_url(episode_id: str, cast_id: str, member: dict[str, Any], variant: str | None = None) -> str | None:
    asset = str(member.get("asset") or "").strip()
    if not asset:
        return None
    file = _asset_file(asset)
    if file is None or not file.is_file():
        return None
    base = f"/api/episodes/storyvideo/{episode_id}/cast/{cast_id}"
    return f"{base}/variants/{variant}/asset" if variant else f"{base}/asset"


def variants_view(episode_id: str, cast_id: str, member: dict[str, Any], spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """角色变体（DESIGN §23）给界面看的形状：每个变体带 asset_url 与实际用到它的镜。"""

    out: dict[str, dict[str, Any]] = {}
    for key, variant in (member.get("variants") or {}).items():
        if not isinstance(variant, dict):
            continue
        out[str(key)] = {
            "label": variant.get("label") or "",
            "delta": variant.get("delta") or "",
            "asset": variant.get("asset") or "",
            "asset_url": _asset_url(episode_id, cast_id, variant, str(key)),
            "shots": [str(x) for x in (variant.get("shots") or ())],
            "used_in": shots_using(spec, cast_id, str(key)),
        }
    return out


def cast_view(episode_id: str, raw: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    from genkit import voices as V

    library = {p["id"]: p for p in V.list_profiles()}
    out = []
    for cast_id, member in (raw.get("cast") or {}).items():
        if not isinstance(member, dict):
            continue
        ref = str(member.get("voice_ref") or "")
        out.append({
            "id": cast_id,
            "kind": cast_kind(member),
            "name": member.get("name") or "",
            "voice": member.get("voice") or "",
            "voice_ref": ref,
            "voice_name": library[ref]["name"] if ref in library else ("（音色库里已删除）" if ref else ""),
            "voice_tts": member.get("voice_tts") or None,
            "block": member.get("block") or "",
            "locks": [dict(l) for l in (member.get("locks") or ()) if isinstance(l, dict)],
            "asset": member.get("asset") or "",
            "asset_url": _asset_url(episode_id, cast_id, member),
            "variants": variants_view(episode_id, cast_id, member, spec),
            "used_in": shots_using(spec, cast_id),
        })
    return out


@router.get("/api/episodes/storyvideo/{episode_id}/cast")
def list_cast(episode_id: str) -> list[dict[str, Any]]:
    path, spec = _load(episode_id)
    return cast_view(episode_id, read_json_bom(path), spec)


class CastEdit(BaseModel):
    kind: str | None = None
    name: str | None = None
    voice: str | None = None
    block: str | None = None
    asset: str | None = None   # 传空串 = 清掉锚图
    # 连续性锁：[{"surface": "淡蓝色粗针织羊毛毛衣", "shots": "all" | ["S1"], "note": "可选"}]；None = 不动，[] = 清空。
    # 锁面原样进生效镜的首帧和视频提示词，所以是生成字段——制作中走 _refuse_if_locked / revise。
    locks: list[dict[str, Any]] | None = None
    # 角色变体（DESIGN §23）：{"rain": {"label": "雨夜", "delta": "外加一件橙色旧雨衣…", "asset": "content/assets/…", "shots": ["S4"]}}；
    # None = 不动，{} = 清空。delta 接在 block 后进首帧提示词、asset 顶替锚图，所以也是生成字段——制作中走 revise。
    variants: dict[str, Any] | None = None


VARIANT_KEY_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}\Z")


def normalize_variants(raw: Any) -> dict[str, dict[str, Any]]:
    """校验并整理变体表；形状不对抛 ValueError（接口层转 400）。asset 只收项目内的相对路径。"""

    if not isinstance(raw, Mapping):
        raise ValueError("variants 必须是对象：{变体键: {label, delta, asset, shots}}")
    out: dict[str, dict[str, Any]] = {}
    for key, item in raw.items():
        key = str(key or "").strip()
        if not VARIANT_KEY_RE.fullmatch(key):
            raise ValueError("变体键只能用英文、数字、下划线、点、短横线，且以英文/数字/下划线开头（不能有 @）")
        if not isinstance(item, Mapping):
            raise ValueError(f"variants.{key} 必须是对象：{{label, delta, asset, shots}}")
        delta = str(item.get("delta") or "").strip()
        if not delta:
            raise ValueError(f"variants.{key}.delta（相对基底的差异）不能为空——只写换了什么：服装、发型状态、伤、湿污、伪装")
        variant: dict[str, Any] = {"delta": delta}
        label = str(item.get("label") or "").strip()
        if label:
            variant["label"] = label
        asset = item.get("asset")
        if asset is not None and str(asset).strip():
            variant["asset"] = normalize_asset_path(asset, f"variants.{key}.asset")
        shots = item.get("shots")
        if shots is not None:
            if not isinstance(shots, list) or not all(isinstance(x, str) for x in shots):
                raise ValueError(f"variants.{key}.shots 必须是镜号列表")
            cleaned = list(dict.fromkeys(x.strip() for x in shots if x.strip()))
            if cleaned:
                variant["shots"] = cleaned
        out[key] = variant
    return out


def normalize_locks(raw: Any) -> list[dict[str, Any]]:
    """校验并整理锁列表；形状不对抛 ValueError（接口层转 400）。"""

    if not isinstance(raw, list):
        raise ValueError("locks 必须是列表")
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"locks[{index}] 必须是对象：{{surface, shots, note}}")
        surface = str(item.get("surface") or "").strip()
        if not surface:
            raise ValueError(f"locks[{index}].surface（锁面）不能为空——填要逐字进提示词的短语")
        scope = item.get("shots", "all")
        if scope is None or scope == "all":
            shots: Any = "all"
        elif isinstance(scope, list) and all(isinstance(s, str) for s in scope):
            shots = list(dict.fromkeys(s.strip() for s in scope if s.strip()))
            if not shots:
                raise ValueError(f"locks[{index}].shots 指定镜时至少要选一镜，或写 \"all\"")
        else:
            raise ValueError(f"locks[{index}].shots 只能是 \"all\" 或镜号列表")
        lock: dict[str, Any] = {"surface": surface, "shots": shots}
        note = str(item.get("note") or "").strip()
        if note:
            lock["note"] = note
        out.append(lock)
    return out


class CastCreate(CastEdit):
    id: str | None = None      # 不给就按名字生成（纯中文名退到 cast1、cast2…）


def allocate_cast_id(cast: Mapping[str, Any], req: "CastCreate") -> str:
    """新角色/场景的 id：给了就校验，没给按名字生成。草稿接口与制作中的 revise 共用。"""

    from genkit.ids import id_from_name

    cast_id = str(req.id or "").strip()
    if not cast_id:
        cast_id = id_from_name(str(req.name or ""), cast.keys(), prefix="cast")
    if not CAST_ID_RE.fullmatch(cast_id):
        raise ValueError("id 只能用英文、数字、下划线、点、短横线，且以英文/数字/下划线开头")
    if cast_id in cast:
        raise KeyError(cast_id)
    return cast_id


def _refuse_if_locked(spec: dict[str, Any], cast_id: str, what: str) -> None:
    if is_locked(spec["output_root"]):
        affected = shots_using(spec, cast_id)
        raise HTTPException(
            409,
            f"项目已开始制作，{what}会改变引用它的镜次的提示词或参考图"
            f"（{', '.join(affected) if affected else '当前没有镜引用它'}）——"
            "要改就复制成新版本再改；只想改声音描述以外的备注类内容，请用后期层。",
        )


def _write_cast(path: Path, raw: dict[str, Any]) -> None:
    try:
        normalize_storyvideo_spec(raw, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(400, f"改动没通过校验，没有写盘：{exc}") from exc
    atomic_write_json(path, raw)


def _apply_edit(member: dict[str, Any], edit: CastEdit) -> dict[str, Any]:
    if edit.kind is not None:
        if edit.kind not in CAST_KINDS:
            raise HTTPException(400, f"kind 只能是 {', '.join(CAST_KINDS)}")
        member["kind"] = edit.kind
    for key in ("name", "voice", "block"):
        value = getattr(edit, key)
        if value is None:
            continue
        if str(value).strip():
            member[key] = str(value).strip()
        else:
            member.pop(key, None)
    if edit.asset is not None:
        if str(edit.asset).strip():
            try:
                member["asset"] = normalize_asset_path(edit.asset, "asset")
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        else:
            member.pop("asset", None)
    if edit.locks is not None:
        try:
            locks = normalize_locks(edit.locks)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if locks:
            member["locks"] = locks
        else:
            member.pop("locks", None)
    if edit.variants is not None:
        try:
            variants = normalize_variants(edit.variants)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if variants:
            member["variants"] = variants
        else:
            member.pop("variants", None)
    if not str(member.get("block") or "").strip():
        raise HTTPException(400, "block（外形/场景描述）不能为空——它是进首帧提示词的正文")
    return member


@router.post("/api/episodes/storyvideo/{episode_id}/cast")
def create_cast(episode_id: str, req: CastCreate) -> dict[str, Any]:
    path, spec = _load(episode_id)
    raw = read_json_bom(path)
    cast = raw.setdefault("cast", {})
    try:
        cast_id = allocate_cast_id(cast, req)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(409, f"已经有一个「{exc.args[0]}」") from exc
    if is_locked(spec["output_root"]):
        raise HTTPException(409, "项目已开始制作，不能新增角色/场景——复制成新版本再加")
    member = _apply_edit({}, req)
    if "kind" not in member and req.kind is None:
        member["kind"] = "character" if (member.get("name") or member.get("voice")) else "scene"
    cast[cast_id] = member
    _write_cast(path, raw)
    return next(c for c in cast_view(episode_id, raw, spec) if c["id"] == cast_id)


@router.put("/api/episodes/storyvideo/{episode_id}/cast/{cast_id}")
def update_cast(episode_id: str, cast_id: str, edit: CastEdit) -> dict[str, Any]:
    path, spec = _load(episode_id)
    raw = read_json_bom(path)
    cast = raw.get("cast") or {}
    if cast_id not in cast or not isinstance(cast[cast_id], dict):
        raise HTTPException(404, f"没有这个角色/场景：{cast_id}")
    _refuse_if_locked(spec, cast_id, f"改「{cast_id}」")
    cast[cast_id] = _apply_edit(dict(cast[cast_id]), edit)
    _write_cast(path, raw)
    fresh = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    return next(c for c in cast_view(episode_id, raw, fresh) if c["id"] == cast_id)


@router.delete("/api/episodes/storyvideo/{episode_id}/cast/{cast_id}")
def delete_cast(episode_id: str, cast_id: str) -> dict[str, Any]:
    path, spec = _load(episode_id)
    raw = read_json_bom(path)
    cast = raw.get("cast") or {}
    if cast_id not in cast:
        raise HTTPException(404, f"没有这个角色/场景：{cast_id}")
    used = shots_using(spec, cast_id)
    if used:
        raise HTTPException(409, f"「{cast_id}」还被这些镜引用：{', '.join(used)}——先把它从这些镜里移掉")
    if is_locked(spec["output_root"]):
        raise HTTPException(409, "项目已开始制作，不能删除角色/场景——复制成新版本再改")
    del cast[cast_id]
    _write_cast(path, raw)
    return {"deleted": cast_id}


class AssetUpload(BaseModel):
    filename: str
    data_base64: str


@router.post("/api/episodes/storyvideo/{episode_id}/cast/{cast_id}/asset")
def upload_cast_asset(episode_id: str, cast_id: str, req: AssetUpload, stage: bool = False) -> dict[str, Any]:
    """角色锚图：存进 ``content/assets/<项目>/``，spec 里记相对路径。生成首帧时作为参考图随请求编码发送。

    ``stage=true``：只存文件、不写 spec，返回相对路径——制作中的项目用它先把图放好，再走「保存并预告费用」
    （mutation kind=cast_asset）把路径写进新版本。"""

    path, spec = _load(episode_id)
    raw = read_json_bom(path)
    cast = raw.get("cast") or {}
    if cast_id not in cast or not isinstance(cast[cast_id], dict):
        raise HTTPException(404, f"没有这个角色/场景：{cast_id}")
    if not stage:
        _refuse_if_locked(spec, cast_id, f"给「{cast_id}」换锚图")
    relative, width, height, size = _store_asset(episode_id, cast_id, req)
    if stage:
        return {"id": cast_id, "asset": relative, "width": width, "height": height, "bytes": size, "staged": True}
    cast[cast_id]["asset"] = relative
    _write_cast(path, raw)
    return {
        "id": cast_id, "asset": relative, "width": width, "height": height, "bytes": size,
        "asset_url": f"/api/episodes/storyvideo/{episode_id}/cast/{cast_id}/asset",
    }


def _store_asset(episode_id: str, stem: str, req: AssetUpload) -> tuple[str, int, int, int]:
    """解码、验图、落盘到 ``content/assets/<项目>/<stem>-<摘要>.<ext>``；返回 (相对路径, 宽, 高, 字节数)。"""

    try:
        data = base64.b64decode(req.data_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"图片数据不是合法的 base64：{exc}") from exc
    if not data:
        raise HTTPException(400, "图片为空")
    if len(data) > MAX_ASSET_BYTES:
        raise HTTPException(413, f"图片超过 {MAX_ASSET_BYTES // 1024 // 1024}MB")
    # 跟其余图片上传走同一个验图（uploads.accept_image）：以前这里自己用 PIL 验，手机 / 相机直出的 JPEG（PIL 认成 MPO）
    # 被当成「收到的是 MPO」拒掉；带 EXIF 方向的竖拍照片原样存，出首帧时参考图是横的。现在 MPO 取第一帧存成标准 JPEG、
    # 方向转进像素，HEIC 读不了就说清「在手机上导出为 JPG」
    try:
        stored, suffix, width, height = accept_image(data)
    except UploadContentError as exc:
        raise HTTPException(400, str(exc)) from exc
    digest = hashlib.sha256(data).hexdigest()[:8]
    assets_dir = PROJECT_ROOT / "content" / "assets" / episode_id
    # 文件名里的 stem 是角色 / 变体 id：以前原样拼进去，id 里带反斜杠（Windows 上是路径分隔符）时「..\..\x」就把
    # 锚图写到了数据目录外（PB-6）。跟别的上传接口一样过 slugify（中文 id 压成 asset，靠摘要区分），再核一次边界
    stem = slugify(stem, fallback="asset")
    target = (assets_dir / f"{stem}-{digest}{suffix}").resolve()
    if not target.is_relative_to(assets_dir.resolve()):
        raise HTTPException(400, "锚图文件名不合法")
    assets_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(stored)
    return target.relative_to(PROJECT_ROOT.resolve()).as_posix(), width, height, len(stored)


@router.post("/api/episodes/storyvideo/{episode_id}/cast/{cast_id}/variants/{key}/asset")
def upload_variant_asset(episode_id: str, cast_id: str, key: str, req: AssetUpload, stage: bool = False) -> dict[str, Any]:
    """变体锚图（DESIGN §23）：存进 ``content/assets/<项目>/<角色>_<变体>-<摘要>.<ext>``，写到 ``variants.<key>.asset``。
    引用该变体的镜生成首帧时用它顶替成员锚图。``stage=true`` 同成员锚图：只存文件，路径由 revise 写进新版本。"""

    path, spec = _load(episode_id)
    raw = read_json_bom(path)
    cast = raw.get("cast") or {}
    if cast_id not in cast or not isinstance(cast[cast_id], dict):
        raise HTTPException(404, f"没有这个角色/场景：{cast_id}")
    variants = cast[cast_id].get("variants") or {}
    if key not in variants or not isinstance(variants[key], dict):
        raise HTTPException(404, f"「{cast_id}」没有变体：{key}")
    if not stage:
        _refuse_if_locked(spec, cast_id, f"给「{cast_id}」的变体「{key}」换锚图")
    relative, width, height, size = _store_asset(episode_id, f"{cast_id}_{key}", req)
    if stage:
        return {"id": cast_id, "variant": key, "asset": relative, "width": width, "height": height, "bytes": size, "staged": True}
    variants[key]["asset"] = relative
    _write_cast(path, raw)
    return {
        "id": cast_id, "variant": key, "asset": relative, "width": width, "height": height, "bytes": size,
        "asset_url": f"/api/episodes/storyvideo/{episode_id}/cast/{cast_id}/variants/{key}/asset",
    }


@router.get("/api/episodes/storyvideo/{episode_id}/cast/{cast_id}/variants/{key}/asset")
def get_variant_asset(episode_id: str, cast_id: str, key: str) -> FileResponse:
    # 读原始文件而不是归一化后的 spec：归一化会把存在的相对路径展开成绝对路径，边界校验就没法做了
    path, _spec = _load(episode_id)
    member = (read_json_bom(path).get("cast") or {}).get(cast_id)
    if not isinstance(member, dict):
        raise HTTPException(404, f"没有这个角色/场景：{cast_id}")
    variant = (member.get("variants") or {}).get(key)
    if not isinstance(variant, dict):
        raise HTTPException(404, f"「{cast_id}」没有变体：{key}")
    asset = str(variant.get("asset") or "")
    if not asset:
        raise HTTPException(404, "这个变体没有锚图")
    return FileResponse(_serve_asset(asset))


def _serve_asset(asset: str) -> Path:
    """锚图只从项目 content/ 之下发：spec 里手写的绝对路径 / ``local:`` / ``..`` 一律 404，不把本机别的文件发出去。"""

    file = _asset_file(asset)
    if file is None:
        raise HTTPException(404, "锚图不在项目 content/ 目录下，不能读取——重新上传一张")
    if not file.is_file():
        raise HTTPException(404, f"锚图文件不存在：{file.relative_to(PROJECT_ROOT.resolve()).as_posix()}")
    return file


@router.get("/api/episodes/storyvideo/{episode_id}/cast/{cast_id}/asset")
def get_cast_asset(episode_id: str, cast_id: str) -> FileResponse:
    path, _spec = _load(episode_id)
    member = (read_json_bom(path).get("cast") or {}).get(cast_id)
    if not isinstance(member, dict):
        raise HTTPException(404, f"没有这个角色/场景：{cast_id}")
    asset = str(member.get("asset") or "")
    if not asset:
        raise HTTPException(404, "这个角色没有锚图")
    return FileResponse(_serve_asset(asset))


# ---------------------------------------------------------------------------- 引用关系图


def build_graph(spec: dict[str, Any]) -> dict[str, Any]:
    """节点：镜、角色/场景、旁白句、插卡、画面文字；边：uses / ref_frame / attached_to / after（片头卡是 before 第一镜）。
    前端用它画双向链接：站在任何一个对象上都能看到它连着谁。"""

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    cast = spec.get("cast") or {}
    for cast_id, member in cast.items():
        if isinstance(member, dict):
            nodes.append({"id": f"cast:{cast_id}", "type": cast_kind(member), "label": member.get("name") or cast_id})
    shots = [s for s in spec.get("shots", ()) if isinstance(s, dict)]
    for index, shot in enumerate(shots):
        sid = str(shot["id"])
        nodes.append({"id": f"shot:{sid}", "type": "shot", "label": sid, "index": index, "dur": shot.get("dur")})
        for cast_ref in shot.get("cast") or ():
            cast_id, variant_key = split_cast_ref(cast_ref)
            edge: dict[str, Any] = {"from": f"shot:{sid}", "to": f"cast:{cast_id}", "type": "uses"}
            if variant_key:
                edge["variant"] = variant_key
            edges.append(edge)
        if shot.get("ref_frame"):
            edges.append({"from": f"shot:{sid}", "to": f"shot:{shot['ref_frame']}", "type": "ref_frame"})
    vo = spec.get("voice_over") or {}
    for i, line in enumerate(vo.get("lines") or ()):
        if isinstance(line, dict):
            nid = f"vo:{i}"
            nodes.append({"id": nid, "type": "voice_over", "label": line.get("zh") or "", "at": line.get("at")})
            edges.append({"from": nid, "to": f"shot:{line.get('shot')}", "type": "attached_to"})
    pg = spec.get("post_graphics") or {}
    for insert in pg.get("phone_ui_inserts") or ():
        if isinstance(insert, dict):
            iid = str(insert.get("id"))
            nodes.append({"id": f"insert:{iid}", "type": "insert", "label": insert.get("title") or iid, "dur": insert.get("dur")})
            after = str(insert.get("after"))
            if after == START_ANCHOR:
                # 片头卡（插在第一镜之前）：连到第一镜、边的类型写 before，别连出一个不存在的「shot:__start__」
                first = next((str(s.get("id")) for s in spec.get("shots", ()) if isinstance(s, dict)), "")
                edges.append({"from": f"insert:{iid}", "to": f"shot:{first}", "type": "before"})
                continue
            target = f"insert:{after}" if any(isinstance(x, dict) and str(x.get("id")) == after for x in pg.get("phone_ui_inserts") or ()) else f"shot:{after}"
            edges.append({"from": f"insert:{iid}", "to": target, "type": "after"})
    for i, note in enumerate(pg.get("notebook_text") or ()):
        if isinstance(note, dict):
            nid = f"note:{i}"
            nodes.append({"id": nid, "type": "notebook_text", "label": note.get("text") or ""})
            edges.append({"from": nid, "to": f"shot:{note.get('shot')}", "type": "attached_to"})
    return {"nodes": nodes, "edges": edges}


@router.get("/api/episodes/storyvideo/{episode_id}/graph")
def get_graph(episode_id: str) -> dict[str, Any]:
    _path, spec = _load(episode_id)
    return build_graph(spec)


# ---------------------------------------------------------------------------- 概览与下一步


def build_summary(episode_id: str, raw: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    output_root = Path(spec["output_root"])
    shots = [s for s in spec.get("shots", ()) if isinstance(s, dict)]
    ids = [str(s["id"]) for s in shots]
    freeze = freeze_state(spec)
    locked = bool(freeze["locked"])
    from ..generate import job_records

    # 账本里被重拍 / 上游重拍重置过（prepared 且 error 里记着重置原因）或明确失败的镜：文件还在盘上
    # （重拍只留底、不删现文件），但它是被作废的旧产物，要重出而不是去审——上游首帧重拍把视频重置之后，
    # 这一镜的视频曾经落在「待审」，审它只会被 runner 以「还没生成完」拒绝。
    # 从没提交过的 prepared（error 为空，ensure_job 新建的）不算：那是手工放进来 / 还没导入的旧产物，照旧按文件算
    jobs = job_records(spec) if locked else {}

    def needs_regen(kind: str, sid: str) -> bool:
        job = jobs.get(make_step_id(kind, sid)) or {}
        status = str(job.get("status") or "")
        return status == "failed" or (status == "prepared" and bool(job.get("error")))

    def gate(kind: str) -> dict[str, Any]:
        sub, suffix = ("01_frames", ".png") if kind == "frames" else ("02_video", ".mp4")
        have = [sid for sid in ids if (output_root / sub / f"{sid}{suffix}").is_file()]
        # qc_decision 对作废（stale）的裁决返回 None（跟 runner 同一口径）：这些镜落进 pending，
        # 另外单列 stale——文件变了，runner 会要求先重新过技术检查，界面据此提示。账本要重出的镜（needs_regen）
        # 不算这两类：重拍裁决照旧算 retake，其余（上游重拍重置的）算 missing
        decisions = {sid: qc_decision(spec, kind, sid) for sid in have}
        regen = {sid for sid in have if decisions[sid] != "retake" and needs_regen(kind, sid)}
        stale = [sid for sid in have
                 if sid not in regen
                 and (meta := qc_meta(spec, kind, sid)) and meta.get("stale") and meta.get("decision") in ("pass", "waive")]
        return {
            "total": len(ids),
            "done": len(have),
            "missing": [sid for sid in ids if sid not in have or sid in regen],
            "passed": [sid for sid, d in decisions.items() if d in ("pass", "waive") and sid not in regen],
            "pending": [sid for sid, d in decisions.items() if not d and sid not in regen],
            "retake": [sid for sid, d in decisions.items() if d == "retake"],
            "stale": stale,
        }

    frames, videos = gate("frames"), gate("videos")
    asr = asr_rows(spec)
    final_path, final_kind = _deliverable_path(spec)
    final_exists = final_path.is_file()
    final_decision = qc_decision(spec, "final", "final") if final_exists else None
    vo = raw.get("voice_over") or {}
    pg = raw.get("post_graphics") or {}
    has_post = bool(vo.get("lines") or pg.get("phone_ui_inserts") or pg.get("notebook_text") or pg.get("labels"))
    post_exists = (output_root / f"{episode_id}_post.mp4").is_file()
    cover_shot = next((sid for sid in ids if (output_root / "01_frames" / f"{sid}.png").is_file()), None)
    # 海报（DESIGN §36）：有就当封面；成片通过后还没有海报，下一步提示顺带点一句
    from storyvideo.poster import latest as latest_poster

    poster_entry = latest_poster(spec)

    from ..generate import failed_jobs, inflight_shot_ids, is_watching, tail_pending

    failures = failed_jobs(spec) if locked else []
    tail_waiting = tail_pending(spec) if locked else []
    # 后台轮询（DESIGN §34）：还在服务商那边的镜 + 有没有 generate-watch 任务在等它们
    in_progress = inflight_shot_ids(spec) if locked else []
    watching = is_watching(episode_id) if in_progress else False
    failed_ids = sorted({f["shot_id"] for f in failures})
    # 项目文件 / 账本 / 冻结快照对不上：下面每一种「下一步」都会被 runner 拦下（生成首帧、审片、合成全是 400），
    # 所以排在最前面，先把这件事说清楚。账本行丢了的那种有现成的出路（导入已有产物，不花钱），直接指过去
    if freeze["mismatch"] == "ledger_missing":
        nxt = {"key": "import_legacy", "label": "导入已有产物", "hint": FREEZE_MISMATCH_HINTS["ledger_missing"]}
    elif freeze["mismatch"]:
        nxt = {"key": "freeze", "label": "处理版本冲突", "hint": FREEZE_MISMATCH_HINTS[freeze["mismatch"]]}
    elif not locked:
        nxt = {"key": "start", "label": "开始制作", "hint": "确认剧本、角色、镜头之后开始制作；之后每一笔生成都有账本"}
    elif failures:
        kinds = sorted({f["label"] for f in failures})
        nxt = {"key": "failed", "label": "处理失败的任务", "hint": f"{len(failed_ids)} 镜提交失败（{'、'.join(kinds)}），概览页有原因和重提入口", "shots": failed_ids}
    elif frames["missing"] or frames["retake"]:
        n = len(frames["missing"]) + len(frames["retake"])
        nxt = {"key": "frames", "label": "生成首帧", "hint": f"还有 {n} 镜没有可用首帧", "shots": frames["missing"] + frames["retake"]}
        # 尾帧续接（DESIGN §30）：缺的首帧全在等源镜视频过检时，下一步其实是先把源镜的视频出完、审掉
        waiting = {t["shot_id"]: t for t in tail_waiting}
        if waiting and all(sid in waiting for sid in frames["missing"] + frames["retake"]):
            sources = sorted({t["source"] for t in waiting.values()})
            nxt = {"key": "tail_wait", "label": "等尾帧", "hint": f"{len(waiting)} 镜在等 {'、'.join(sources)} 的视频过检后抽尾帧",
                   "shots": sorted(waiting), "sources": sources}
    elif frames["pending"]:
        nxt = {"key": "review_frames", "label": "审首帧", "hint": f"{len(frames['pending'])} 镜待审", "shots": frames["pending"]}
        if frames["stale"]:
            nxt["hint"] += f"；{len(frames['stale'])} 镜的检查结果已作废（文件变了或上游重拍过），点「生成首帧」会重新做技术检查，不花钱"
    elif videos["missing"] or videos["retake"]:
        n = len(videos["missing"]) + len(videos["retake"])
        nxt = {"key": "videos", "label": "生成视频", "hint": f"还有 {n} 镜没有可用视频", "shots": videos["missing"] + videos["retake"]}
    elif videos["pending"]:
        nxt = {"key": "review_videos", "label": "审视频", "hint": f"{len(videos['pending'])} 镜待审；有台词的镜可以先跑台词识别闸", "shots": videos["pending"]}
        if videos["stale"]:
            nxt["hint"] += f"；{len(videos['stale'])} 镜的检查结果已作废（文件变了或上游重拍过），点「生成视频」会重新做技术检查，不花钱"
    elif has_post and not post_exists:
        nxt = {"key": "post", "label": "生成后期合成版", "hint": "旁白、插卡、画面文字会在这一步合进去"}
    elif not final_exists:
        nxt = {"key": "final", "label": "生成成片", "hint": "本地拼接，不花钱"}
    elif final_decision is None:
        nxt = {"key": "review_final", "label": "审成片", "hint": "看一遍成片，通过或重拍"}
    elif final_decision == "retake":
        nxt = {"key": "retake_final", "label": "重新合成成片", "hint": "成片被记了重拍——改完相关镜次后重新合成"}
    else:
        nxt = {"key": "done", "label": "已完成", "hint": "成片已通过，可以导出"}
    # 海报提醒放在独立的 poster 字段里，不改 next_step 的文案：成片已经合成、还没有海报时点一句
    poster_hint = "" if poster_entry or not final_exists else "还没有海报——成片页可以生成一张，交付包会带上"

    return {
        "id": episode_id,
        "module": module_for_pipeline("storyvideo"),   # 功能模块标签（DESIGN §38）
        "title": raw.get("title_zh") or raw.get("title_en") or episode_id,
        "logline": raw.get("logline") or "",
        "camera_profile": raw.get("camera_profile") or "",
        "form": _form_summary(raw.get("form")),   # 形态卡（DESIGN §31）：{id, name} 或 None
        "genre": _genre_summary(raw.get("genre")),   # 题材卡（DESIGN §49）：{id, name} 或 None
        "locked": locked,
        "shots_total": len(ids),
        "total_seconds": sum(float(s.get("dur") or 0) for s in shots),
        "cast_count": sum(1 for m in (raw.get("cast") or {}).values() if isinstance(m, dict) and cast_kind(m) == "character"),
        "scene_count": sum(1 for m in (raw.get("cast") or {}).values() if isinstance(m, dict) and cast_kind(m) != "character"),
        "cover_url": (f"/api/episodes/storyvideo/{episode_id}/poster/file/{poster_entry['poster']}" if poster_entry
                      else f"/api/episodes/storyvideo/{episode_id}/artifact/frame/{cover_shot}" if cover_shot else None),
        "poster": {"exists": bool(poster_entry), "title": (poster_entry or {}).get("title") or "", "hint": poster_hint},
        "frames": frames,
        # 尾帧续接（DESIGN §30）：这一轮推迟的镜（源镜视频还没过检）
        "tail_pending": tail_waiting,
        "videos": videos,
        "asr": {"ran": bool(asr), "pass": sum(1 for r in asr.values() if r.get("status") == "pass"),
                "fail": sum(1 for r in asr.values() if r.get("status") in ("fail", "silent"))},
        "final": {"exists": final_exists, "kind": final_kind if final_exists else None, "decision": final_decision},
        "post_layers": {"present": has_post, "composed": post_exists},
        "failures": failures,
        # 后台轮询（DESIGN §34）：在飞的镜与是否有后台任务在等结果（false 时界面会补发 generate-resume）
        "in_progress": in_progress,
        "watching": watching,
        # 冻结三方核对：mismatch 非空时 next_step 指向的动作会被 runner 拦下，hint 说清先处理什么
        "freeze": {"mismatch": freeze["mismatch"],
                   "hint": FREEZE_MISMATCH_HINTS[freeze["mismatch"]] if freeze["mismatch"] else ""},
        "next_step": nxt,
    }


@router.get("/api/episodes/storyvideo/{episode_id}/summary")
def get_summary(episode_id: str) -> dict[str, Any]:
    path, spec = _load(episode_id)
    return build_summary(episode_id, read_json_bom(path), spec)
