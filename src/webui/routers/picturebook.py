"""绘本 / 有声书接口（DESIGN §39，契约 docs/picturebook-spec.md §6）。

* ``GET  /api/picturebook``                      列表（id / 标题 / 年龄段 / 页数 / 进度 / 封面）
* ``GET  /api/picturebook/next-id``              自动编号
* ``GET  /api/picturebook/presets``              新建页要的选项：风格预设、年龄段、双语策略、音色库
* ``POST /api/picturebook``                      新建（body = spec 子集；不填 id 由服务端编号）
* ``POST /api/picturebook/examples/create``      从示例书复制一本（新 id）
* ``POST /api/picturebook/jobs/draft``           LLM 起草分页表（后台任务 pb-draft；``save`` 为真时直接建书）
* ``GET/PUT/DELETE /api/picturebook/{id}``       读 / 整体保存（校验 + lint 一起返回）/ 归档到 .webui_deleted/picturebook/
* ``GET  /api/picturebook/{id}/lint``            文本检查 + 问题卡
* ``POST /api/picturebook/{id}/pages/generate``  插画（后台任务 pb-pages）
* ``POST /api/picturebook/{id}/narration/generate`` 朗读（后台任务 pb-narration）
* ``POST /api/picturebook/{id}/assemble``        装配（后台任务 pb-assemble）
* ``GET  /api/picturebook/{id}/artifact/page/{pid}`` / ``audio/{name}`` / ``final`` / ``asset/{kind}/{name}``  产物流（路径安全）
* ``POST /api/picturebook/{id}/assets/{kind}/{key}`` 定妆 / 场景锚 / BGM / 翻页音效上传（base64，不覆盖，顺手写进项目文件）
* ``GET  /api/picturebook/{id}/script-sheet?lang=zh``  朗读稿 .xlsx
* ``GET  /api/picturebook/{id}/summary``         概览：进度、问题数、下一步

所有任务 ``module="picturebook"``。测试注入点：``SYNTHESIZER`` / ``PROVIDER_FACTORY`` / ``DISPATCH`` / ``FFMPEG`` / ``FFPROBE``。
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
from picturebook import assembly as A
from picturebook import draft as D
from picturebook import lint as L
from picturebook import narration as N
from picturebook import pages as PG
from picturebook import scriptsheet as X
from picturebook import spec as S
from picturebook.timeline import build_timeline, languages_for
from storyvideo import subtitles
from storyvideo.assembly import AssemblyError
from storyvideo.scriptgen import ScriptGenError
from storyvideo.state import atomic_write_json

from .. import deps, tasks
from ..problems import problem_cards
from ..uploads import HEIF_EXTENSIONS, UploadContentError, accept_audio, accept_image

router = APIRouter(tags=["picturebook"])

MODULE = "picturebook"
EXAMPLES_DIR = _resource("examples", "picturebook")
IMAGE_SUFFIXES = {"png": ".png", "jpg": ".jpg", "jpeg": ".jpg", "webp": ".webp"}
AUDIO_SUFFIXES = {"mp3": ".mp3", "wav": ".wav", "m4a": ".m4a", "aac": ".aac", "ogg": ".ogg", "flac": ".flac"}
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
ASSET_KINDS = {"character": "characters", "scene": "scenes", "music": "music", "sfx": "sfx"}
KINDS = {"draft": "pb-draft", "pages": "pb-pages", "narration": "pb-narration", "assemble": "pb-assemble"}

# 测试注入点（None = 用真实实现）
SYNTHESIZER: Callable[..., Any] | None = None
PROVIDER_FACTORY: Callable[[], Any] | None = None
DISPATCH: Callable[..., Any] | None = None
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


# ---------------------------------------------------------------------------- 辅助


def _root() -> Path:
    return deps.PROJECT_ROOT


def _load(book_id: str) -> dict[str, Any]:
    try:
        return S.load_book(book_id, project_root=_root())
    except S.SpecError as exc:
        raise HTTPException(404, str(exc)) from exc


def _safe_name(name: str, suffixes: tuple[str, ...]) -> str:
    if not name or "/" in name or "\\" in name or name.startswith(".") or name != Path(name).name or not name.lower().endswith(suffixes):
        raise HTTPException(404, "没有这个文件")
    return name


def _relative(path: Path) -> str:
    try:
        return path.relative_to(_root()).as_posix()
    except ValueError:
        return str(path)


def _final_paths(spec: dict[str, Any]) -> tuple[Path, Path]:
    return A.final_path(spec), A.landscape_path(spec)


def _page_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    images = PG.page_status(spec)
    audio = N.status(spec)
    book_id = spec["id"]
    rows = []
    for page in spec["pages"]:
        pid = page["id"]
        img = images.get(pid) or {}
        row: dict[str, Any] = {
            "id": pid, "kind": page["kind"],
            "image_url": f"/api/picturebook/{book_id}/artifact/page/{pid}" if img.get("done") else None,
            "image_stale": bool(img.get("stale")), "image_attempts": img.get("attempts", 0), "image_error": img.get("error"),
            "audio": {},
        }
        for lang, info in (audio.get(pid) or {}).items():
            row["audio"][lang] = {
                **info,
                "url": f"/api/picturebook/{book_id}/artifact/audio/{Path(str(info['file'])).stem}" if info.get("done") and info.get("file") else None,
            }
        rows.append(row)
    return rows


def _busy(book_id: str) -> dict[str, str | None]:
    return {name: tasks.active_task_id(kind, book_id) for name, kind in KINDS.items()}


def _voices() -> list[dict[str, Any]]:
    try:
        from genkit.voices import list_profiles

        return [{"id": p["id"], "name": p.get("name") or p["id"], "engine": p.get("engine") or "edge", "tags": p.get("tags") or []}
                for p in list_profiles()]
    except Exception:  # noqa: BLE001 - 音色库读不了不该拦住绘本页
        return []


def detail(spec: dict[str, Any]) -> dict[str, Any]:
    report = L.lint_report(spec)
    final, landscape = _final_paths(spec)
    timeline = build_timeline(spec, N.segments_for_timeline(spec))
    return {
        "id": spec["id"], "module": MODULE, "title": spec.get("title_zh") or spec.get("title_en") or "",
        "spec": S.to_disk(spec),
        "warnings": report["warnings"], "notes": report["notes"], "problems": problem_cards(report["warnings"]), "stats": report["stats"],
        "pages": _page_rows(spec),
        "timeline": {"total": timeline["total"], "estimated": timeline["estimated"],
                     "pages": [{"id": p["id"], "start": p["start"], "duration": p["duration"]} for p in timeline["pages"]]},
        "final_url": f"/api/picturebook/{spec['id']}/artifact/final" if final.is_file() else None,
        "final_path": _relative(final) if final.is_file() else None,
        "final_size_bytes": final.stat().st_size if final.is_file() else None,
        "landscape_url": f"/api/picturebook/{spec['id']}/artifact/final?variant=16x9" if landscape.is_file() else None,
        "prompt_language": S.prompt_language(spec),
        "busy": _busy(spec["id"]),
        "fonts": subtitles.available_fonts(),
        "default_font": subtitles.resolve_font({"font_path": (spec.get("subtitle") or {}).get("font_path")}) or "",
        "voices": _voices(),
    }


def _progress(spec: dict[str, Any]) -> dict[str, Any]:
    images = PG.page_status(spec)
    audio = N.status(spec)
    langs = languages_for(spec["bilingual_mode"])
    total = len(spec["pages"])
    final, landscape = _final_paths(spec)
    narration_done = sum(1 for pid in S.page_ids(spec) if all((audio.get(pid) or {}).get(l, {}).get("done") for l in langs))
    return {
        "pages": sum(1 for v in images.values() if v.get("done")), "pages_total": total,
        "narration": narration_done, "narration_total": total,
        "final": final.is_file(), "landscape": landscape.is_file(),
    }


def next_step(spec: dict[str, Any], progress: dict[str, Any], warnings: list[str]) -> dict[str, str]:
    if not spec["pages"]:
        return {"key": "draft", "label": "起草分页表", "hint": "用 AI 起草，或在分页表里手填"}
    if warnings:
        return {"key": "lint", "label": "处理文本问题", "hint": f"还有 {len(warnings)} 条文本检查告警——不拦生成，但成片前最好清掉"}
    if progress["pages"] < progress["pages_total"]:
        return {"key": "pages", "label": "生成插画", "hint": f"还有 {progress['pages_total'] - progress['pages']} 页没有插画"}
    if progress["narration"] < progress["narration_total"]:
        return {"key": "narration", "label": "生成朗读", "hint": f"还有 {progress['narration_total'] - progress['narration']} 页没有朗读"}
    if not progress["final"]:
        return {"key": "assemble", "label": "装配成片", "hint": "本地 ffmpeg 拼接，不花钱"}
    return {"key": "done", "label": "已完成", "hint": "可以下载成片，或导出朗读稿交给真人朗读"}


def _write(spec_like: dict[str, Any], *, book_id: str) -> dict[str, Any]:
    try:
        normalized = S.normalize_book(spec_like, project_root=_root(), book_id=book_id)
    except S.SpecError as exc:
        raise HTTPException(400, f"改动没通过校验，没有写盘：{exc}") from exc
    atomic_write_json(S.book_path(_root(), book_id), S.to_disk(normalized))
    return normalized


def _next_book_id() -> str:
    # 自动编号时回收站里的 id 也算占用：删一本再建一本，新书要是拿到刚删的 id，回收站里那一本就再也恢复不回来
    return S.next_book_id(S.existing_book_ids(_root()) + trash.archived_ids(_root(), MODULE))


# ---------------------------------------------------------------------------- 列表 / 编号 / 新建


@router.get("/api/picturebook")
def list_books() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for book_id in S.existing_book_ids(_root()):
        entry: dict[str, Any] = {"id": book_id, "module": MODULE, "pipeline": "picturebook", "path": f"{S.BOOKS_DIR.as_posix()}/{book_id}.json"}
        try:
            spec = S.load_book(book_id, project_root=_root())
        except Exception as exc:  # noqa: BLE001 - 一个坏文件不能让列表挂掉
            entry.update({"status": "error", "error": str(exc)})
            items.append(entry)
            continue
        progress = _progress(spec)
        cover = PG.page_image_path(spec, spec["pages"][0]["id"]) if spec["pages"] else None
        entry.update({
            "status": "ok", "title": spec.get("title_zh") or spec.get("title_en") or "", "age_band": spec["age_band"],
            "bilingual_mode": spec["bilingual_mode"], "page_count": len(spec["pages"]), "progress": progress,
            "cover_url": f"/api/picturebook/{book_id}/artifact/page/{spec['pages'][0]['id']}" if cover else None,
        })
        items.append(entry)
    return items


@router.get("/api/picturebook/next-id")
def next_id() -> dict[str, str]:
    return {"id": _next_book_id()}


@router.get("/api/picturebook/presets")
def presets() -> dict[str, Any]:
    return {
        "styles": D.STYLE_PRESETS, "age_bands": list(S.AGE_BANDS), "bilingual_modes": list(S.BILINGUAL_MODES), "kb_dirs": list(S.KB_DIRS),
        "default_negative": S.DEFAULT_NEGATIVE, "default_pause": dict(S.DEFAULT_PAUSE), "voices": _voices(),
        "examples": sorted(p.stem for p in EXAMPLES_DIR.glob("*.json")) if EXAMPLES_DIR.is_dir() else [],
    }


@router.post("/api/picturebook")
def create_book(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体要是项目文件对象")
    book_id = str(body.get("id") or "").strip() or _next_book_id()
    if not is_valid_id(book_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    if S.book_path(_root(), book_id).exists():
        raise HTTPException(409, f"绘本 {book_id} 已经存在，换一个 id")
    spec = _write(body, book_id=book_id)
    return detail(spec)


class ExampleCreateRequest(BaseModel):
    name: str = "fox_three_socks"
    book_id: str | None = None


@router.post("/api/picturebook/examples/create")
def create_from_example(req: ExampleCreateRequest) -> dict[str, Any]:
    source = EXAMPLES_DIR / f"{Path(req.name).name}.json"
    if not source.is_file():
        raise HTTPException(404, f"没有这个示例：{req.name}")
    book_id = str(req.book_id or "").strip() or _next_book_id()
    if not is_valid_id(book_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    if S.book_path(_root(), book_id).exists():
        raise HTTPException(409, f"绘本 {book_id} 已经存在，换一个 id")
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    try:
        spec = S.normalize_book(data, project_root=_root(), book_id=book_id)
    except S.SpecError as exc:
        raise HTTPException(400, f"示例没通过结构校验（这是示例文件本身的问题）：{exc}") from exc
    atomic_write_json(S.book_path(_root(), book_id), S.to_disk(spec))
    return {"id": book_id, "path": f"{S.BOOKS_DIR.as_posix()}/{book_id}.json", "example": req.name, **detail(spec)}


# ---------------------------------------------------------------------------- 起草


class DraftRequest(BaseModel):
    direction: str
    age_band: str = "2-4"
    bilingual_mode: str = "sentence_alternate"
    education: dict[str, Any] | None = None
    page_count: int | None = None
    style: str | None = None
    title: str | None = None
    save: bool = False
    book_id: str | None = None


def run_draft(req: DraftRequest, book_id: str) -> dict[str, Any]:
    out = D.generate_book_draft(direction=req.direction, age_band=req.age_band, bilingual_mode=req.bilingual_mode, education=req.education,
                                page_count=req.page_count, style=req.style, title=req.title, dispatch=DISPATCH)
    spec_like = out["spec"]
    try:
        normalized = S.normalize_book(spec_like, project_root=_root(), book_id=book_id)
    except S.SpecError as exc:
        raise ScriptGenError(f"起草结果没通过结构校验：{exc}") from exc
    report = L.lint_report(normalized)
    result: dict[str, Any] = {"id": book_id, "draft": S.to_disk(normalized), "warnings": report["warnings"], "notes": report["notes"],
                              "cost_usd": out["cost_usd"], "tokens": out["tokens"], "backend": out["backend"], "saved": False}
    if req.save:
        if S.book_path(_root(), book_id).exists():
            raise ScriptGenError(f"绘本 {book_id} 已经存在，起草结果没有写盘——从任务结果里取草稿另建")
        atomic_write_json(S.book_path(_root(), book_id), S.to_disk(normalized))
        result["saved"] = True
    return result


@router.post("/api/picturebook/jobs/draft")
def draft_job(req: DraftRequest) -> dict[str, Any]:
    if not str(req.direction or "").strip():
        raise HTTPException(400, "故事方向不能为空")
    if req.age_band not in S.AGE_BANDS:
        raise HTTPException(400, f"年龄段只能是 {' / '.join(S.AGE_BANDS)}")
    if req.bilingual_mode not in S.BILINGUAL_MODES:
        raise HTTPException(400, f"双语策略只能是 {' / '.join(S.BILINGUAL_MODES)}")
    if req.page_count is not None and not 4 <= req.page_count <= 24:
        raise HTTPException(400, "页数要在 4 到 24 之间")
    book_id = str(req.book_id or "").strip() or _next_book_id()
    if not is_valid_id(book_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    if req.save and S.book_path(_root(), book_id).exists():
        raise HTTPException(409, f"绘本 {book_id} 已经存在，换一个 id")
    try:
        task_id = tasks.start(KINDS["draft"], book_id, lambda: run_draft(req, book_id), params=req.model_dump(), module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id, "id": book_id}


# ---------------------------------------------------------------------------- 读 / 写 / 删


@router.get("/api/picturebook/{book_id}")
def get_book(book_id: str) -> dict[str, Any]:
    return detail(_load(book_id))


@router.put("/api/picturebook/{book_id}")
def put_book(book_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _load(book_id)
    if str(body.get("id") or book_id) != book_id:
        raise HTTPException(400, f"项目文件里的 id「{body.get('id')}」跟地址里的 {book_id} 对不上")
    spec = _write(body, book_id=book_id)
    return detail(spec)


@router.delete("/api/picturebook/{book_id}")
def delete_book(book_id: str) -> dict[str, Any]:
    """归档到 ``.webui_deleted/picturebook/``（产物目录、上传的角色参考图 / 配乐一并搬走，附 manifest），不是 ``os.remove``；
    回收站里能恢复（``genkit.trash``）。有任务在跑、或有文件被占用时 409，什么都不动。"""

    path = S.book_path(_root(), book_id)
    if not is_valid_id(book_id) or not path.is_file():
        raise HTTPException(404, f"没有这本绘本：{book_id}")
    if any(_busy(book_id).values()):
        raise HTTPException(409, trash.TASKS_RUNNING)
    try:
        result = trash.archive(_root(), MODULE, book_id)
    except trash.FilesBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"archived_to": _relative(Path(result["archived_to"])), "output_moved": result["output_moved"],
            "assets_moved": result["assets_moved"]}


# ---------------------------------------------------------------------------- 检查 / 概览


@router.get("/api/picturebook/{book_id}/lint")
def lint_book(book_id: str) -> dict[str, Any]:
    spec = _load(book_id)
    report = L.lint_report(spec)
    return {"id": book_id, **report, "problems": problem_cards(report["warnings"])}


@router.get("/api/picturebook/{book_id}/summary")
def summary(book_id: str) -> dict[str, Any]:
    spec = _load(book_id)
    progress = _progress(spec)
    warnings = L.lint_book(spec)
    return {
        "id": book_id, "module": MODULE, "title": spec.get("title_zh") or spec.get("title_en") or "", "age_band": spec["age_band"],
        "bilingual_mode": spec["bilingual_mode"], "progress": progress, "problem_count": len(warnings), "problems": problem_cards(warnings),
        "next": next_step(spec, progress, warnings), "busy": _busy(book_id),
        "final_url": f"/api/picturebook/{book_id}/artifact/final" if progress["final"] else None,
    }


# ---------------------------------------------------------------------------- 三个后台任务


class PagesRequest(BaseModel):
    only: list[str] | None = None
    force: bool = False


def _note(**fields: Any) -> None:
    task_id = tasks.current_task_id()
    if task_id:
        tasks.note(task_id, **fields)


def run_pages(book_id: str, req: PagesRequest) -> dict[str, Any]:
    spec = S.load_book(book_id, project_root=_root())
    provider = PROVIDER_FACTORY() if PROVIDER_FACTORY else None
    result = PG.generate_pages(spec, only=req.only, force=req.force, provider=provider, note=_note)
    reason = deps.all_failed_message(result, unit="页插画")
    if reason:
        raise PG.PageGenerationError(reason)   # 一页都没出：任务记 failed（部分失败仍正常返回，逐页原因在 result 里）
    return result


@router.post("/api/picturebook/{book_id}/pages/generate")
def generate_pages(book_id: str, req: PagesRequest) -> dict[str, Any]:
    spec = _load(book_id)
    if not spec["pages"]:
        raise HTTPException(400, "这本书还没有分页，没有可生成的插画")
    unknown = sorted(set(req.only or []) - set(S.page_ids(spec)))
    if unknown:
        raise HTTPException(404, f"没有这些页：{', '.join(unknown)}")
    if PROVIDER_FACTORY is None:
        deps.require_roles(None, "image")   # 起任务前先查生图角色：没配就 400，不起任务、不给页记失败尝试
    try:
        task_id = tasks.start(KINDS["pages"], book_id, lambda: run_pages(book_id, req), params=req.model_dump(), module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    model = (spec.get("image_params") or {}).get("model") or "@image"
    count = len(req.only) if req.only else len(spec["pages"])
    return {"task_id": task_id, "model": model,
            "cost_note": local_mod.cost_note_for("image", count, f"最多 {count} 次生图请求（{model}）；请求没变且图已在的页会跳过")}


class NarrationRequest(BaseModel):
    only: list[str] | None = None
    languages: list[str] | None = None
    force: bool = False


def run_narration(book_id: str, req: NarrationRequest) -> dict[str, Any]:
    spec = S.load_book(book_id, project_root=_root())
    result = N.synthesize_narration(spec, only=req.only, languages=req.languages, force=req.force, synthesizer=SYNTHESIZER,
                                    prober=lambda p: N.probe_duration(p, ffprobe=FFPROBE), note=_note)
    reason = deps.all_failed_message(result, unit="段朗读", done_key="synthesized")
    if reason:
        raise N.NarrationError(reason)   # 一段都没合成出来：任务记 failed（部分失败仍正常返回）
    return result


@router.post("/api/picturebook/{book_id}/narration/generate")
def generate_narration(book_id: str, req: NarrationRequest) -> dict[str, Any]:
    spec = _load(book_id)
    if not spec["pages"]:
        raise HTTPException(400, "这本书还没有分页，没有可朗读的文本")
    unknown = sorted(set(req.only or []) - set(S.page_ids(spec)))
    if unknown:
        raise HTTPException(404, f"没有这些页：{', '.join(unknown)}")
    bad = sorted(set(req.languages or []) - {"zh", "en"})
    if bad:
        raise HTTPException(400, "languages 只认 zh / en")
    if SYNTHESIZER is None:
        # 绑定了「模型设置」来源的音色档案才要语音角色（占位 edge 音色不要）：没配就 400，别起一个必然全失败的任务
        wanted = req.languages or languages_for(spec["bilingual_mode"])
        if any(str(N.voice_profile(spec, lang).get("engine") or "") == "settings" for lang in wanted):
            deps.require_roles(None, "voice")
    try:
        task_id = tasks.start(KINDS["narration"], book_id, lambda: run_narration(book_id, req), params=req.model_dump(), module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    placeholder = {lang: N.voice_profile(spec, lang).get("placeholder") for lang in languages_for(spec["bilingual_mode"])}
    return {"task_id": task_id, "placeholder": placeholder}


class AssembleRequest(BaseModel):
    landscape: bool | None = None
    font: str | None = None


def run_assemble(book_id: str, req: AssembleRequest) -> dict[str, Any]:
    spec = S.load_book(book_id, project_root=_root())
    try:
        return A.assemble_book(spec, ffmpeg=FFMPEG, ffprobe=FFPROBE, font_path=req.font or None, landscape=req.landscape, note=_note)
    except AssemblyError as exc:
        from ..humanize import humanize

        raise AssemblyError(humanize(exc)) from exc


@router.post("/api/picturebook/{book_id}/assemble")
def assemble(book_id: str, req: AssembleRequest | None = None) -> dict[str, Any]:
    req = req or AssembleRequest()
    spec = _load(book_id)
    progress = _progress(spec)
    if progress["pages"] < progress["pages_total"]:
        raise HTTPException(425, f"还有 {progress['pages_total'] - progress['pages']} 页没有插画，先生成插画")
    if progress["narration"] < progress["narration_total"]:
        raise HTTPException(425, f"还有 {progress['narration_total'] - progress['narration']} 页没有朗读，先生成朗读")
    if req.font and not Path(req.font).expanduser().is_file():
        raise HTTPException(400, f"字体文件不存在：{req.font}")
    try:
        task_id = tasks.start(KINDS["assemble"], book_id, lambda: run_assemble(book_id, req), params=req.model_dump(), module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id}


# ---------------------------------------------------------------------------- 产物流


@router.get("/api/picturebook/{book_id}/artifact/page/{page_id}")
def artifact_page(book_id: str, page_id: str) -> FileResponse:
    spec = _load(book_id)
    if page_id not in S.page_ids(spec):
        raise HTTPException(404, "没有这个文件")
    path = PG.page_image_path(spec, page_id)
    if path is None or path.is_symlink():
        raise HTTPException(404, "没有这个文件")
    return FileResponse(path, media_type="image/png")


@router.get("/api/picturebook/{book_id}/artifact/audio/{name}")
def artifact_audio(book_id: str, name: str) -> FileResponse:
    """``name`` = ``P01_zh`` / ``P01_zh_2``（不带扩展名）。"""

    spec = _load(book_id)
    stem = _safe_name(name + ".mp3", (".mp3",))[:-4]
    if not any(stem.startswith(f"{pid}_") for pid in S.page_ids(spec)):
        raise HTTPException(404, "没有这个文件")
    path = S.audio_dir(spec) / f"{stem}.mp3"
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "没有这个文件")
    return FileResponse(path, media_type="audio/mpeg")


@router.get("/api/picturebook/{book_id}/artifact/final")
def artifact_final(book_id: str, variant: str | None = None) -> FileResponse:
    spec = _load(book_id)
    final, landscape = _final_paths(spec)
    path = landscape if variant == "16x9" else final
    if not path.is_file():
        raise HTTPException(404, "还没有成片")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/api/picturebook/{book_id}/artifact/asset/{kind}/{name}")
def artifact_asset(book_id: str, kind: str, name: str) -> FileResponse:
    if kind not in ASSET_KINDS:
        raise HTTPException(404, "没有这个文件")
    _load(book_id)
    suffixes = tuple(IMAGE_SUFFIXES.values()) if kind in ("character", "scene") else tuple(AUDIO_SUFFIXES.values())
    name = _safe_name(name, suffixes)
    path = _root() / S.ASSETS_DIR / book_id / ASSET_KINDS[kind] / name
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "没有这个文件")
    return FileResponse(path)


# ---------------------------------------------------------------------------- 上传


class AssetUpload(BaseModel):
    filename: str
    data_base64: str


@router.post("/api/picturebook/{book_id}/assets/{kind}/{key}")
def upload_asset(book_id: str, kind: str, key: str, req: AssetUpload) -> dict[str, Any]:
    """``kind``：character（定妆，key = 角色 id）/ scene（场景锚，key = 场景 id）/ music（BGM）/ sfx（翻页音效）。
    文件存进 content/assets/<ID>/<kind>/<名>-<摘要><后缀>（不覆盖），路径顺手写进项目文件对应字段。"""

    if kind not in ASSET_KINDS:
        raise HTTPException(400, "kind 只能是 character / scene / music / sfx")
    spec = _load(book_id)
    table = ASSET_KINDS[kind]
    if kind in ("character", "scene") and key not in (spec.get(table) or {}):
        raise HTTPException(404, f"没有这个{'角色' if kind == 'character' else '场景'}：{key}")
    image = kind in ("character", "scene")
    ext = Path(req.filename or "").suffix.lower().lstrip(".")
    # 图片的扩展名只是第一道快速拒绝（HEIC 放过去，由内容那一关给「导出为 JPG」的提示）；音频不看扩展名，全按内容认
    if image and ext not in IMAGE_SUFFIXES and ext not in HEIF_EXTENSIONS:
        raise HTTPException(400, f"只接受 {' / '.join(IMAGE_SUFFIXES)} 文件，收到的是 .{ext or '?'}")
    try:
        data = base64.b64decode(req.data_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"文件数据不是合法的 base64：{exc}") from exc
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
    # 真假、落盘字节与后缀按内容定：假 mp3 以前会 200 写进 music.file，装配时才在 ffmpeg 里炸；手机照片（MPO / 带 EXIF
    # 方向）转成标准、转正的 JPEG；音频有 ffprobe 就要读得出音频流且时长 > 0，AIFF 这类转成 wav 收
    try:
        if image:
            stored, suffix = accept_image(data)[:2]
        else:
            stored, suffix = accept_audio(data, req.filename, allowed=tuple(AUDIO_SUFFIXES.values()), max_bytes=MAX_UPLOAD_BYTES)[:2]
    except UploadContentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    stem = slugify(key if image else Path(req.filename).stem, fallback=kind)
    digest = hashlib.sha256(data).hexdigest()[:8]
    target_dir = _root() / S.ASSETS_DIR / book_id / table
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{stem}-{digest}{suffix}"
    if not target.exists():
        target.write_bytes(stored)
    relative = target.relative_to(_root()).as_posix()
    raw = deps.read_json_bom(S.book_path(_root(), book_id))
    if kind in ("character", "scene"):
        raw.setdefault(table, {}).setdefault(key, {})["asset"] = relative
    elif kind == "music":
        music = raw.get("music") if isinstance(raw.get("music"), dict) else {}
        music["file"] = relative
        raw["music"] = music
    else:
        raw["page_turn_sfx"] = relative
    _write(raw, book_id=book_id)
    return {"file": relative, "bytes": len(stored), "url": f"/api/picturebook/{book_id}/artifact/asset/{kind}/{target.name}"}


# ---------------------------------------------------------------------------- 朗读稿


@router.get("/api/picturebook/{book_id}/script-sheet")
def script_sheet(book_id: str, lang: str = "zh") -> FileResponse:
    spec = _load(book_id)
    try:
        path = X.write_script_sheet(spec, lang)
    except X.ScriptSheetError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=path.name)
