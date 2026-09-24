"""配乐接口（DESIGN §44，契约 docs/music-spec.md §4）。

* ``GET  /api/music``                        列表（id / 标题 / 体裁 / 时长 / 条数 / 进度 / 投放去向）
* ``GET  /api/music/next-id``                自动编号（MU-）
* ``GET  /api/music/briefs``                 预设 brief（skills/ai-music/scripts/music_briefs.json）
* ``GET  /api/music/targets``                可投放的项目：短剧剧集 + 绘本 + 播客（给前端下拉）
* ``POST /api/music``                        新建（title / brief / duration / count …；不填 id 由服务端编号）
* ``POST /api/music/examples/create``        复制示例
* ``GET/PUT/DELETE /api/music/{id}``         详情（spec + 提示词预览 + 历次）/ 保存 / 归档到 .webui_deleted/music/
* ``POST /api/music/{id}/references``        参考图上传（base64，jpg/png；张数上限按当前配乐来源，见 ``music.spec.source_limits``）
* ``POST /api/music/{id}/generate``          ``{count?, force?}`` 出配乐（后台任务 music-generate），返回 cost_note
* ``POST /api/music/{id}/pick``              ``{attempt}`` 采用某一条
* ``POST /api/music/{id}/use``               ``{module, target, slot?}`` 投放（§3）；``slot`` 对短剧是起始秒，
                                             对播客是槽名（intro / bed / outro，默认 bed）
* ``GET  /api/music/{id}/artifact/audio/{name}``  产物流（路径安全）

所有任务 ``module="music"``。测试注入点：``PROVIDER_FACTORY``。
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from genkit import local as local_mod
from genkit import trash
from genkit.ids import is_valid_id, slugify
from genkit.paths import resource as _resource
from music import briefs as B
from music import generate as G
from music import prompts as P
from music import spec as S
from music import use as U

from .. import deps, tasks
from ..uploads import HEIF_EXTENSIONS, UploadContentError, accept_image, same_content_index

router = APIRouter(prefix="/api/music", tags=["music"])

MODULE = "music"
BASE = "/api/music"
EXAMPLES_DIR = _resource("examples", "music")
EXAMPLE_DEFAULT = "lullaby_demo"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
KINDS = {"generate": "music-generate"}
MEDIA_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
               ".ogg": "audio/ogg", ".flac": "audio/flac"}

# 测试注入点（None = 用真实实现）
PROVIDER_FACTORY: Callable[[], Any] | None = None


# ---------------------------------------------------------------------------- 辅助


def _root() -> Path:
    return deps.PROJECT_ROOT


def _note(**fields: Any) -> None:
    task_id = tasks.current_task_id()
    if task_id:
        tasks.note(task_id, **fields)


def _relative(path: Path) -> str:
    try:
        return path.relative_to(_root()).as_posix()
    except ValueError:
        return str(path)


def _load(spec_id: str) -> dict[str, Any]:
    try:
        return S.load(spec_id, project_root=_root())
    except S.SpecError as exc:
        raise HTTPException(404, str(exc)) from exc


def _write(body: dict[str, Any], *, spec_id: str) -> dict[str, Any]:
    try:
        normalized = S.normalize(body, project_root=_root(), spec_id=spec_id)
    except S.SpecError as exc:
        raise HTTPException(400, f"改动没通过校验，没有写盘：{exc}") from exc
    S.save(normalized)
    return normalized


def _taken_ids() -> list[str]:
    # 自动编号时回收站里的 id 也算占用：删一个再建一个，新项目要是拿到刚删的 id，回收站里那一份就再也恢复不回来
    return S.existing_ids(_root()) + trash.archived_ids(_root(), MODULE)


def _fresh_id(requested: str | None) -> str:
    spec_id = str(requested or "").strip() or S.next_id(_taken_ids())
    if not is_valid_id(spec_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    if S.spec_path(_root(), spec_id).exists():
        raise HTTPException(409, f"配乐 {spec_id} 已经存在，换一个 id")
    return spec_id


def _audio_url(spec_id: str, name: str) -> str:
    return f"{BASE}/{spec_id}/artifact/audio/{name}"


def _attempt_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for attempt in spec.get("attempts") or ():
        name = str(attempt.get("file") or "")
        ok = bool(name) and not attempt.get("error") and S.attempt_path(spec, name) is not None
        rows.append({**attempt, "url": _audio_url(spec["id"], name) if ok else None,
                     "current": bool(name) and name == spec.get("current")})
    return rows


def _progress(spec: dict[str, Any]) -> dict[str, Any]:
    state = G.status(spec)
    return {"done": state["done"], "attempts": state["attempts"], "ok_attempts": state["ok_attempts"],
            "stale": state["stale"], "used": len(spec.get("used_in") or []), "count": spec.get("count")}


def _busy(spec_id: str) -> dict[str, str | None]:
    return {name: tasks.active_task_id(kind, spec_id) for name, kind in KINDS.items()}


def _detail(spec: dict[str, Any]) -> dict[str, Any]:
    current = S.current_path(spec)
    return {
        "id": spec["id"], "module": MODULE, "title": spec["title"],
        "spec": S.to_disk(spec),
        "prompts": P.compile_both(spec),
        "prompt": P.compile_prompt(spec),
        "attempts": _attempt_rows(spec),
        "current_url": _audio_url(spec["id"], str(spec["current"])) if current else None,
        "references": [{"file": f, "exists": S.resolve_asset(spec, f) is not None} for f in spec["reference_images"]],
        "used_in": list(spec.get("used_in") or []),
        "progress": _progress(spec), "busy": _busy(spec["id"]),
        "limits": S.source_limits(),
    }


class ReferenceUpload(BaseModel):
    filename: str
    data_base64: str


class GenerateRequest(BaseModel):
    count: int | None = None
    force: bool = False


class PickRequest(BaseModel):
    attempt: str


class UseRequest(BaseModel):
    module: str
    target: str
    # 短剧：起始秒（数或数字串）；播客：槽名 intro / bed / outro；绘本不用
    slot: float | str | None = None


class ExampleCreateRequest(BaseModel):
    name: str | None = None
    id: str | None = None


# ---------------------------------------------------------------------------- 列表 / 编号 / 预设 / 目标 / 新建


@router.get("")
def list_music() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec_id in S.existing_ids(_root()):
        entry: dict[str, Any] = {"id": spec_id, "module": MODULE, "pipeline": MODULE, "path": f"content/music/{spec_id}.json"}
        try:
            spec = S.load(spec_id, project_root=_root())
        except Exception as exc:  # noqa: BLE001 - 一个坏文件不能让列表挂掉
            entry.update({"status": "error", "error": str(exc)})
            rows.append(entry)
            continue
        entry.update({"status": "ok", "title": spec["title"], "genre": spec["brief"]["genre"],
                      "duration": spec["duration"], "count": spec["count"], "progress": _progress(spec),
                      "used_in": list(spec.get("used_in") or []),
                      "current_url": _audio_url(spec_id, str(spec["current"])) if S.current_path(spec) else None})
        rows.append(entry)
    return rows


@router.get("/next-id")
def next_id() -> dict[str, str]:
    return {"id": S.next_id(_taken_ids())}


@router.get("/briefs")
def list_briefs() -> dict[str, Any]:
    examples = sorted(p.stem for p in EXAMPLES_DIR.glob("*.json")) if EXAMPLES_DIR.is_dir() else []
    return {
        "briefs": B.payload(),
        "prompt_languages": list(S.PROMPT_LANGUAGES),
        "defaults": {"duration": S.DEFAULT_DURATION, "count": S.DEFAULT_COUNT, "music_params": dict(S.DEFAULT_MUSIC_PARAMS)},
        "limits": S.source_limits(),
        "examples": examples,
    }


@router.get("/targets")
def list_targets() -> dict[str, Any]:
    """可投放的项目（§3）：短剧剧集 + 绘本 + 播客（播客行带已有文件的槽 ``slots``）。只读，不改任何东西。"""

    targets = U.list_targets(_root())
    return {"modules": list(S.USE_MODULES), **targets}


@router.post("")
def create(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体要是项目文件对象")
    spec_id = _fresh_id(body.get("id"))
    return _detail(_write(body, spec_id=spec_id))


@router.post("/examples/create")
def create_from_example(req: ExampleCreateRequest | None = None) -> dict[str, Any]:
    req = req or ExampleCreateRequest()
    name = Path(req.name or EXAMPLE_DEFAULT).name
    source = EXAMPLES_DIR / f"{name}.json"
    if not source.is_file():
        raise HTTPException(404, f"没有这个示例：{name}")
    spec_id = _fresh_id(req.id)
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    try:
        spec = S.normalize(data, project_root=_root(), spec_id=spec_id)
    except S.SpecError as exc:
        raise HTTPException(400, f"示例没通过结构校验（这是示例文件本身的问题）：{exc}") from exc
    S.save(spec)
    return {"example": name, "path": f"content/music/{spec_id}.json", **_detail(spec)}


# ---------------------------------------------------------------------------- 读 / 写 / 删


@router.get("/{spec_id}")
def get_music(spec_id: str) -> dict[str, Any]:
    return _detail(_load(spec_id))


@router.put("/{spec_id}")
def put_music(spec_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _load(spec_id)
    if str(body.get("id") or spec_id) != spec_id:
        raise HTTPException(400, f"项目文件里的 id「{body.get('id')}」跟地址里的 {spec_id} 对不上")
    return _detail(_write(body, spec_id=spec_id))


@router.delete("/{spec_id}")
def delete_music(spec_id: str) -> dict[str, Any]:
    """归档（回收站里能恢复）。有任务在跑（出音乐）、或有文件被占用时 409，什么都不动。"""

    if is_valid_id(spec_id) and any(_busy(spec_id).values()):
        raise HTTPException(409, trash.TASKS_RUNNING)
    try:
        result = S.archive(spec_id, project_root=_root())
    except S.SpecError as exc:
        raise HTTPException(404, str(exc)) from exc
    except trash.FilesBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"archived_to": _relative(Path(result["archived_to"])), "output_moved": result["output_moved"],
            "assets_moved": result["assets_moved"]}


# ---------------------------------------------------------------------------- 参考图


@router.post("/{spec_id}/references")
def upload_reference(spec_id: str, req: ReferenceUpload) -> dict[str, Any]:
    spec = _load(spec_id)
    ext = Path(req.filename or "").suffix.lower().lstrip(".")
    # HEIC 放过扩展名这一关：由内容那一关转成 JPEG，或给出「在手机上导出为 JPG」的提示
    if ext not in S.REFERENCE_SUFFIXES and ext not in HEIF_EXTENSIONS:
        raise HTTPException(400, f"参考图只接受 jpg / png，收到的是 .{ext or '?'}")
    try:
        data = base64.b64decode(req.data_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"文件数据不是合法的 base64：{exc}") from exc
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
    # 扩展名只是第一道快速拒绝；真假、落盘后缀按内容定（配乐参考图只收 jpg / png，webp 内容也拒）；
    # 手机照片（MPO / 带 EXIF 方向）转成标准、转正的 JPEG
    try:
        stored, suffix, _width, _height = accept_image(data, allowed={"PNG": ".png", "JPEG": ".jpg"})
    except UploadContentError as exc:
        raise HTTPException(400, f"参考图只接受 jpg / png，收到的是 {exc.detected}" if exc.detected else str(exc)) from exc
    stem = slugify(Path(req.filename).stem, fallback="ref")
    digest = hashlib.sha256(data).hexdigest()[:8]
    target_dir = _root() / S.ASSETS_DIR / spec_id
    target = target_dir / f"{stem}-{digest}{suffix}"
    relative = target.relative_to(_root()).as_posix()
    # 文件层按内容摘要早就幂等了，列表层也要：同一张图再传一次不再追加第二条（重复条目会白占 10 张的名额，
    # 生成时还会把同一个 asset 带两遍）。按内容摘要认「同一张」：换了文件名的同一张图也沿用原来那条
    index = same_content_index(spec["reference_images"], target_dir.relative_to(_root()).as_posix(), digest)
    existing = index is not None
    if existing:
        relative = spec["reference_images"][index]
        target = _root() / relative
    max_refs = S.source_limits()["max_references"]
    if max_refs == 0 and not existing:
        raise HTTPException(400, "当前的配乐来源不收参考图——在「高级」里删掉参考图，或换一个收参考图的来源")
    if not existing and len(spec["reference_images"]) >= max_refs:
        raise HTTPException(400, f"参考图最多 {max_refs} 张，现在有 {len(spec['reference_images'])} 张")
    target_dir.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(stored)
    if not existing:
        raw = S.to_disk(spec)
        raw["reference_images"] = [*spec["reference_images"], relative]
        spec = _write(raw, spec_id=spec_id)
    return {"file": relative, "bytes": len(stored), "existing": existing, "references": _detail(spec)["references"]}


# ---------------------------------------------------------------------------- 生成 / 采用 / 投放


def _run_generate(spec_id: str, req: GenerateRequest) -> dict[str, Any]:
    spec = S.load(spec_id, project_root=_root())
    provider = PROVIDER_FACTORY() if PROVIDER_FACTORY else None
    result = G.generate_music(spec, count=req.count, force=req.force, provider=provider, note=_note)
    reason = deps.all_failed_message(result, unit="条配乐")
    if reason:
        raise G.MusicGenerationError(reason)   # 一条都没出：任务记 failed（部分失败仍正常返回，逐条原因在 result 里）
    return result


@router.post("/{spec_id}/generate")
def generate(spec_id: str, req: GenerateRequest | None = None) -> dict[str, Any]:
    req = req or GenerateRequest()
    spec = _load(spec_id)
    count = int(req.count if req.count is not None else spec["count"])
    low, high = S.source_limits()["count"]
    if not low <= count <= high:
        raise HTTPException(400, f"一次只能出 {low} 到 {high} 条")
    if not P.has_intent(spec):
        raise HTTPException(400, "提示词为空——先把 brief 里的用途 / 体裁 / 情绪填上一项")
    if PROVIDER_FACTORY is None:
        deps.require_roles(None, "music")   # 起任务前先查配乐角色：没配就 400，不起任务、不往项目文件记失败尝试
    try:
        task_id = tasks.start(KINDS["generate"], spec_id, lambda: _run_generate(spec_id, req),
                              params=req.model_dump(), module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    model = spec["music_params"]["model"]
    return {"task_id": task_id, "model": model, "count": count,
            "cost_note": local_mod.cost_note_for("music", count, f"{count} 次配乐请求（{model}），每次出 1 条；请求没变且已有采用的会跳过")}


@router.post("/{spec_id}/pick")
def pick(spec_id: str, req: PickRequest) -> dict[str, Any]:
    spec = _load(spec_id)
    if S.attempt_path(spec, str(req.attempt)) is None:
        raise HTTPException(400, "attempt 要是这个配乐成功出过的文件名之一")
    spec["current"] = str(req.attempt)
    S.save(spec)
    return _detail(spec)


@router.post("/{spec_id}/use")
def use(spec_id: str, req: UseRequest) -> dict[str, Any]:
    """投放（§3）：拷进目标项目的素材目录 + 写进它的配乐层，再往 ``used_in`` 记一条。"""

    spec = _load(spec_id)
    try:
        result = U.use_music(spec, module=req.module, target_id=req.target, slot=req.slot, project_root=_root())
    except U.UnknownTarget as exc:
        raise HTTPException(404, str(exc)) from exc
    except U.MusicUseError as exc:
        raise HTTPException(400, str(exc)) from exc
    S.save(spec)
    title = result.get("title") or result["target"]
    # 播客按槽说话（垫乐 / 片头 / 片尾）；短剧与绘本仍是「配乐层」
    layer = result.get("slot_label") or "配乐层"
    return {"ok": True, "module": result["module"], "target": result["target"], "target_title": title,
            "file": result["file"], "slot": result.get("slot") or "", "slot_label": result.get("slot_label") or "",
            "tracks": result.get("tracks"), "message": f"已放进《{title}》的{layer}", "detail": _detail(spec)}


# ---------------------------------------------------------------------------- 产物流


@router.get("/{spec_id}/artifact/audio/{name}")
def artifact_audio(spec_id: str, name: str) -> FileResponse:
    spec = _load(spec_id)
    if not name or "/" in name or "\\" in name or name.startswith(".") or name != Path(name).name:
        raise HTTPException(404, "没有这个文件")
    if not name.lower().endswith(S.AUDIO_SUFFIXES):
        raise HTTPException(404, "没有这个文件")
    path = S.attempt_path(spec, name)
    if path is None or path.is_symlink():
        raise HTTPException(404, "没有这个文件")
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"))


__all__ = ["router", "PROVIDER_FACTORY", "KINDS"]
