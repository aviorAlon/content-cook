"""单条视频接口（DESIGN §43，契约 docs/clip-spec.md §5）。

* ``GET  /api/clip``                       列表（id / 标题 / 时长 / 节拍数 / 进度 / 封面片）
* ``GET  /api/clip/next-id``               自动编号（CL-）
* ``POST /api/clip``                       新建（body = 项目文件子集；不填 id 由服务端编号）
* ``POST /api/clip/examples/create``       复制示例（不花钱）
* ``GET/PUT/DELETE /api/clip/{id}``        详情（spec + 编译出来的提示词 + 检查 + 历次片子）/ 保存 / 归档
* ``GET  /api/clip/{id}/lint``             检查 + 问题卡
* ``POST /api/clip/{id}/first-frame``      首帧：``{mode: upload, filename, data_base64}`` 同步落盘；
                                           ``{mode: generate}`` 起后台任务 ``clip-frame``；``{mode: none}`` 清掉
* ``POST /api/clip/{id}/generate``         ``{force?}`` 出片（后台任务 ``clip-generate``），返回 ``cost_note``
* ``POST /api/clip/{id}/pick``             ``{attempt}`` 采用历次里的某一条
* ``GET  /api/clip/{id}/artifact/video/{name}`` / ``/frame/{name}``   产物流（路径安全）

所有任务 ``module="clip"``。断点续等：``resume_clip_watchers()`` 由 ``webui.generate.resume_inflight_watchers``
在服务启动时调一次——上次退出时还在服务商那边的片子，服务一起来就接着等。测试注入点：``PROVIDER_FACTORY``。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clip import generate as G
from clip import lint as L
from clip import prompts as P
from clip import spec as S
from genkit import local as local_mod
from genkit import trash
from genkit.ids import is_valid_id
from genkit.paths import resource as _resource

from .. import deps, tasks
from ..problems import problem_cards
from ..uploads import HEIF_EXTENSIONS, UploadContentError, accept_image

router = APIRouter(tags=["clip"])

MODULE = "clip"
EXAMPLES_DIR = _resource("examples", MODULE)
DEFAULT_EXAMPLE = "city_morning"
KINDS = {"generate": "clip-generate", "frame": "clip-frame"}
IMAGE_SUFFIXES = {"png": ".png", "jpg": ".jpg", "jpeg": ".jpg", "webp": ".webp"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# 测试注入点（None = 用真实实现）
PROVIDER_FACTORY: Callable[[], Any] | None = None


# ---------------------------------------------------------------------------- 辅助


def _root() -> Path:
    return deps.PROJECT_ROOT


def _provider() -> Any:
    return PROVIDER_FACTORY() if PROVIDER_FACTORY else None


def _load(clip_id: str) -> dict[str, Any]:
    try:
        return S.load(clip_id, project_root=_root())
    except S.SpecError as exc:
        raise HTTPException(404, str(exc)) from exc


def _write(spec_like: dict[str, Any], *, clip_id: str) -> dict[str, Any]:
    try:
        normalized = S.normalize(spec_like, project_root=_root(), clip_id=clip_id)
    except S.SpecError as exc:
        raise HTTPException(400, f"改动没通过校验，没有写盘：{exc}") from exc
    S.save(normalized)
    return normalized


def _safe_name(name: str, suffixes: tuple[str, ...]) -> str:
    if (not name or "/" in name or "\\" in name or name.startswith(".") or name != Path(name).name
            or not name.lower().endswith(suffixes)):
        raise HTTPException(404, "没有这个文件")
    return name


def _relative(path: Path) -> str:
    try:
        return path.relative_to(_root()).as_posix()
    except ValueError:
        return str(path)


def _note(**fields: Any) -> None:
    task_id = tasks.current_task_id()
    if task_id:
        tasks.note(task_id, **fields)


def _busy(clip_id: str) -> dict[str, str | None]:
    return {name: tasks.active_task_id(kind, clip_id) for name, kind in KINDS.items()}


def _video_url(clip_id: str, name: str | None) -> str | None:
    return f"/api/clip/{clip_id}/artifact/video/{name}" if name else None


def _frame_url(spec: dict[str, Any]) -> str | None:
    path = S.first_frame_path(spec)
    return f"/api/clip/{spec['id']}/artifact/frame/{path.name}" if path else None


def _attempt_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for attempt in spec.get("attempts") or ():
        file = str(attempt.get("file") or "")
        exists = bool(file) and (S.video_dir(spec) / file).is_file()
        rows.append({**attempt, "url": _video_url(spec["id"], file) if exists else None,
                     "current": file == spec.get("current"), "exists": exists})
    return rows


def _progress(spec: dict[str, Any]) -> dict[str, Any]:
    status = G.clip_status(spec)
    return {"done": bool(status["done"]), "attempts": status["attempts"], "beats": len(spec.get("beats") or ()),
            "duration": (spec.get("video_params") or {}).get("duration"), "status": status["status"]}


def detail(spec: dict[str, Any]) -> dict[str, Any]:
    report = L.lint_report(spec)
    lines = {row["id"]: row["line"] for row in P.beat_preview(spec)}
    return {
        "id": spec["id"], "module": MODULE, "title": spec["title"], "spec": S.to_disk(spec),
        "prompt": P.compile_clip_prompt(spec), "frame_prompt": P.compile_frame_prompt(spec),
        "sections": {"global": P.global_section(spec), "timeline": P.timeline_section(spec),
                     "continuity": P.continuity_section(spec)},
        "prompt_language": spec["prompt_language"], "dialect": P.dialect_for(spec), "aspect_ratio": P.aspect_ratio(spec),
        "palette": P.palette(spec), "style_anchor": P.style_anchor(spec),
        "beats": [{**beat, "line": lines.get(beat["id"], "")} for beat in spec.get("beats") or ()],
        "warnings": report["warnings"], "notes": report["notes"], "problems": problem_cards(report["warnings"]),
        "stats": report["stats"], "status": G.clip_status(spec), "progress": _progress(spec),
        "attempts": _attempt_rows(spec), "current_url": _video_url(spec["id"], spec.get("current")),
        "first_frame": {**(spec.get("first_frame") or {}), "url": _frame_url(spec)},
        "cost_note": G.cost_note(spec), "busy": _busy(spec["id"]),
    }


def _taken_ids() -> list[str]:
    # 自动编号时回收站里的 id 也算占用：删一个再建一个，新项目要是拿到刚删的 id，回收站里那一份就再也恢复不回来
    return S.existing_ids(_root()) + trash.archived_ids(_root(), MODULE)


def _fresh_id(requested: str | None) -> str:
    clip_id = str(requested or "").strip() or S.next_clip_id(_taken_ids())
    if not is_valid_id(clip_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    if S.clip_path(_root(), clip_id).exists():
        raise HTTPException(409, f"项目 {clip_id} 已经存在，换一个 id")
    return clip_id


# ---------------------------------------------------------------------------- 列表 / 编号 / 新建


@router.get("/api/clip")
def list_clips() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for clip_id in S.existing_ids(_root()):
        entry: dict[str, Any] = {"id": clip_id, "module": MODULE, "pipeline": MODULE,
                                 "path": f"{S.CLIPS_DIR.as_posix()}/{clip_id}.json"}
        try:
            spec = S.load(clip_id, project_root=_root())
        except Exception as exc:  # noqa: BLE001 - 一个坏文件不能让列表挂掉
            entry.update({"status": "error", "error": str(exc)})
            rows.append(entry)
            continue
        entry.update({"status": "ok", "title": spec["title"], "aspect_ratio": P.aspect_ratio(spec),
                      "duration": spec["video_params"]["duration"], "beats": len(spec["beats"]),
                      "progress": _progress(spec), "video_url": _video_url(clip_id, spec.get("current"))})
        rows.append(entry)
    return rows


@router.get("/api/clip/next-id")
def next_id() -> dict[str, str]:
    return {"id": S.next_clip_id(_taken_ids())}


@router.post("/api/clip")
def create_clip(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体要是项目文件对象")
    clip_id = _fresh_id(body.get("id"))
    return detail(_write(body, clip_id=clip_id))


class ExampleCreateRequest(BaseModel):
    name: str | None = None
    id: str | None = None


@router.post("/api/clip/examples/create")
def create_from_example(req: ExampleCreateRequest | None = None) -> dict[str, Any]:
    req = req or ExampleCreateRequest()
    name = Path(req.name or DEFAULT_EXAMPLE).name
    source = EXAMPLES_DIR / f"{name}.json"
    if not source.is_file():
        raise HTTPException(404, f"没有这个示例：{name}")
    clip_id = _fresh_id(req.id)
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    try:
        spec = S.normalize(data, project_root=_root(), clip_id=clip_id)
    except S.SpecError as exc:
        raise HTTPException(400, f"示例没通过结构校验（这是示例文件本身的问题）：{exc}") from exc
    S.save(spec)
    return {"example": name, "path": f"{S.CLIPS_DIR.as_posix()}/{clip_id}.json", **detail(spec)}


# ---------------------------------------------------------------------------- 读 / 写 / 删 / 检查


@router.get("/api/clip/{clip_id}")
def get_clip(clip_id: str) -> dict[str, Any]:
    return detail(_load(clip_id))


@router.put("/api/clip/{clip_id}")
def put_clip(clip_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _load(clip_id)
    if str(body.get("id") or clip_id) != clip_id:
        raise HTTPException(400, f"项目文件里的 id「{body.get('id')}」跟地址里的 {clip_id} 对不上")
    return detail(_write(body, clip_id=clip_id))


@router.delete("/api/clip/{clip_id}")
def delete_clip(clip_id: str) -> dict[str, Any]:
    """归档（回收站里能恢复）。有任务在跑（首帧 / 出片）、或有文件被占用时 409，什么都不动。"""

    if is_valid_id(clip_id) and any(_busy(clip_id).values()):
        raise HTTPException(409, trash.TASKS_RUNNING)
    try:
        result = S.archive(clip_id, project_root=_root())
    except S.SpecError as exc:
        raise HTTPException(404, str(exc)) from exc
    except trash.FilesBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"archived_to": _relative(Path(result["archived_to"])), "output_moved": result["output_moved"],
            "assets_moved": result["assets_moved"]}


@router.get("/api/clip/{clip_id}/lint")
def lint_clip(clip_id: str) -> dict[str, Any]:
    spec = _load(clip_id)
    report = L.lint_report(spec)
    return {"id": clip_id, **report, "problems": problem_cards(report["warnings"]), "prompt": P.compile_clip_prompt(spec)}


# ---------------------------------------------------------------------------- 首帧 / 出片 / 采用


class FirstFrameRequest(BaseModel):
    mode: str = "generate"
    filename: str | None = None
    data_base64: str | None = None
    prompt: str | None = None
    force: bool = False


def run_frame(clip_id: str, force: bool) -> dict[str, Any]:
    spec = S.load(clip_id, project_root=_root())
    result = G.generate_first_frame(spec, force=force, provider=_provider(), note=_note)
    if result.get("error"):
        raise G.ClipGenerationError(result["error"])
    return result


@router.post("/api/clip/{clip_id}/first-frame")
def first_frame(clip_id: str, req: FirstFrameRequest | None = None) -> dict[str, Any]:
    req = req or FirstFrameRequest()
    spec = _load(clip_id)
    mode = str(req.mode or "generate").strip()
    if mode not in S.FIRST_FRAME_MODES:
        raise HTTPException(400, f"first_frame.mode 只能是 {' / '.join(S.FIRST_FRAME_MODES)}，收到「{mode}」")
    if req.prompt is not None:
        spec["first_frame"]["prompt"] = str(req.prompt)
    if mode == "none":
        spec["first_frame"].update({"mode": "none", "asset": None, "error": None})
        S.save(spec)
        return {"mode": "none", **detail(spec)}
    if mode == "upload":
        ext = Path(req.filename or "").suffix.lower().lstrip(".")
        # HEIC 放过扩展名这一关：由内容那一关转成 JPEG，或给出「在手机上导出为 JPG」的提示
        if ext not in IMAGE_SUFFIXES and ext not in HEIF_EXTENSIONS:
            raise HTTPException(400, f"只接受 {' / '.join(IMAGE_SUFFIXES)} 文件，收到的是 .{ext or '?'}")
        try:
            data = base64.b64decode(req.data_base64 or "", validate=True)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"文件数据不是合法的 base64：{exc}") from exc
        if not data:
            raise HTTPException(400, "文件为空")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
        # 内容真假、落盘后缀都按字节定：这张图之后会当首帧参考图交给付费的视频模型，junk 不能在这里过关；
        # 手机照片（MPO / 带 EXIF 方向）先转成标准、转正的 JPEG——视频模型按像素读，竖拍的首帧不能变成横的
        try:
            stored, suffix, _width, _height = accept_image(data)
        except UploadContentError as exc:
            raise HTTPException(400, str(exc)) from exc
        info = G.attach_first_frame(spec, stored, str(req.filename or "frame.png"), suffix=suffix)
        return {"mode": "upload", "file": info["file"], "bytes": info["bytes"], **detail(spec)}
    if PROVIDER_FACTORY is None:
        deps.require_roles(None, "image")   # 起任务前先查生图角色：没配就 400，不起任务、不改项目文件
    spec["first_frame"]["mode"] = "generate"
    S.save(spec)
    try:
        task_id = tasks.start(KINDS["frame"], clip_id, lambda: run_frame(clip_id, req.force),
                              params={"force": req.force}, module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    model = spec["image_params"]["model"]
    return {"mode": "generate", "task_id": task_id, "model": model,
            "cost_note": local_mod.cost_note_for("image", 1, f"1 次生图请求（首帧，{model}）；已经有首帧且没勾「重出」的会跳过")}


class GenerateRequest(BaseModel):
    force: bool = False


def run_generate(clip_id: str, force: bool) -> dict[str, Any]:
    spec = S.load(clip_id, project_root=_root())
    result = G.generate_clip(spec, force=force, provider=_provider(), note=_note)
    if result.get("error"):
        raise G.ClipGenerationError(result["error"])
    return result


@router.post("/api/clip/{clip_id}/generate")
def generate(clip_id: str, req: GenerateRequest | None = None) -> dict[str, Any]:
    req = req or GenerateRequest()
    spec = _load(clip_id)
    if not spec["beats"]:
        raise HTTPException(400, "还没有节拍，先在「写法」里写时间轴")
    if str(spec["first_frame"]["mode"]) == "upload" and S.first_frame_path(spec) is None:
        raise HTTPException(425, "首帧模式是「用上传的图」，但还没有上传首帧")
    if PROVIDER_FACTORY is None:
        # 起任务前先查角色：出片要视频模型；首帧模式是「生成」且还没有首帧的，会先出首帧、也要生图模型
        needed = ["video"]
        if str(spec["first_frame"]["mode"]) == "generate" and S.first_frame_path(spec) is None:
            needed.insert(0, "image")
        deps.require_roles(None, *needed)
    try:
        task_id = tasks.start(KINDS["generate"], clip_id, lambda: run_generate(clip_id, req.force),
                              params=req.model_dump(), module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id, "model": spec["video_params"]["model"], "duration": spec["video_params"]["duration"],
            "cost_note": G.cost_note(spec, force=req.force)}


class PickRequest(BaseModel):
    attempt: str


@router.post("/api/clip/{clip_id}/pick")
def pick(clip_id: str, req: PickRequest) -> dict[str, Any]:
    spec = _load(clip_id)
    attempt = S.attempt_by_file(spec, req.attempt)
    if not attempt or attempt.get("error") or not (S.video_dir(spec) / str(attempt["file"])).is_file():
        raise HTTPException(400, "attempt 要是这条片子成功出过的文件名之一")
    spec["current"] = str(attempt["file"])
    S.save(spec)
    return detail(spec)


# ---------------------------------------------------------------------------- 产物流


@router.get("/api/clip/{clip_id}/artifact/video/{name}")
def artifact_video(clip_id: str, name: str) -> FileResponse:
    spec = _load(clip_id)
    name = _safe_name(name, (".mp4",))
    known = {str(a.get("file")) for a in spec.get("attempts") or ()}
    path = S.video_dir(spec) / name
    if name not in known or not path.is_file() or path.is_symlink():
        raise HTTPException(404, "没有这个文件")
    return FileResponse(path, media_type="video/mp4", filename=name)


@router.get("/api/clip/{clip_id}/artifact/frame/{name}")
def artifact_frame(clip_id: str, name: str) -> FileResponse:
    spec = _load(clip_id)
    name = _safe_name(name, tuple(IMAGE_SUFFIXES.values()))
    path = S.frame_dir(spec) / name
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "没有这个文件")
    return FileResponse(path)


# ---------------------------------------------------------------------------- 断点续等（DESIGN §34 的做法）


def resume_clip_watchers(project_root: Path | None = None) -> list[dict[str, Any]]:
    """服务启动时扫 ``content/clip/``：还有片子在服务商那边的项目，起一个 ``clip-generate`` 任务接着等。

    只轮询 + 下载，**从不提交新任务**，所以不会花钱。项目文件坏了 / 已删的跳过。
    """

    root = Path(project_root or deps.PROJECT_ROOT)
    out: list[dict[str, Any]] = []
    for clip_id in S.existing_ids(root):
        try:
            spec = S.load(clip_id, project_root=root)
            if not G.inflight_attempt(spec):
                continue
        except Exception:  # noqa: BLE001 - 坏文件不该拦住服务启动
            continue

        def _resume(cid: str = clip_id) -> dict[str, Any]:
            live = S.load(cid, project_root=root)
            return G.resume_clip(live, provider=_provider(), note=_note)

        try:
            task_id = tasks.start(KINDS["generate"], clip_id, _resume, params={"resume": True}, module=MODULE)
        except tasks.TaskBusy:
            continue
        out.append({"episode_id": clip_id, "module": MODULE, "task_id": task_id, "started": True})
    return out


__all__ = ["router", "resume_clip_watchers", "PROVIDER_FACTORY", "KINDS", "MODULE", "detail"]
