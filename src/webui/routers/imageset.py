"""图片集接口（DESIGN §40 / §42，契约 docs/imageset-spec.md §2、docs/imagestudio-spec.md）：三个前缀共用一套处理器。

``make_router(module)`` 按模块造一份 ``APIRouter``（前缀 ``/api/sticker`` / ``/api/portrait`` / ``/api/image``），``module``
由前缀决定；``router`` 把三份都装进去，app 只挂一次。``{m}`` = sticker / portrait / image：

* ``GET  /api/{m}``                      列表（id / 标题 / 模板 / 进度 / 封面）
* ``GET  /api/{m}/next-id``              自动编号（ST- / PT-）
* ``GET  /api/{m}/templates``            模板与风格预设（含每模板 options_schema）+ 示例名 + ``model_candidates``
* ``POST /api/{m}``                      新建（title / template / subject / style / options …；不填 id 由服务端编号）
* ``POST /api/{m}/examples/create``      复制示例
* ``GET/PUT/DELETE /api/{m}/{id}``       详情（spec + items 状态 + 每张 url）/ 保存 / 归档到 .webui_deleted/<m>/
* ``POST /api/{m}/{id}/assets``          参考图上传（base64，不覆盖；≤ 6 张；婚纱可带 who）
* ``POST /api/{m}/{id}/expand``          按模板展开 items（保留已有 current）
* ``POST /api/{m}/{id}/generate``        ``{only?, force?}`` 出图（后台任务 set-generate），返回 cost_note
* ``POST /api/{m}/{id}/pick``            ``{item, attempt}`` 采用某次 / ``{picks:[…]}`` 挑选
* ``POST /api/{m}/{id}/post``            后处理（本机，同步返回产物；``{background: true}`` 走任务 set-post）
* ``POST /api/{m}/{id}/export``          打包 zip，返回下载 url
* ``GET  /api/{m}/{id}/artifact/raw|out|asset|export/{name}``  产物流（路径安全）

测试注入点：``PROVIDER_FACTORY``。
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
from genkit import model_catalog
from genkit import settings as settings_mod
from genkit import trash
from genkit.ids import is_valid_id, slugify
from genkit.paths import resource as _resource
from imageset import export as E
from imageset import generate as G
from imageset import post as PO
from imageset import spec as S
from imageset import templates as T
from storyvideo.state import atomic_write_json

from .. import deps, tasks
from ..uploads import HEIF_EXTENSIONS, UploadContentError, accept_image, same_content_index
from .local_models import cached_services

router = APIRouter(tags=["imageset"])

IMAGE_SUFFIXES = {"png": ".png", "jpg": ".jpg", "jpeg": ".jpg", "webp": ".webp"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
KINDS = {"generate": "set-generate", "post": "set-post"}
EXAMPLE_DEFAULTS = {"sticker": "xiaoman_emoji", "portrait": "baby_100_demo", "image": "lantern_night"}

# 测试注入点（None = 用真实实现）
PROVIDER_FACTORY: Callable[[], Any] | None = None

# 候选清单里「本机」那一段看哪几个预设（能顶这个角色的本机服务，按 local.ROLE_PREFERENCE 的顺序）
LOCAL_SERVICES_FOR_ROLE: dict[str, tuple[str, ...]] = {"image": ("sdwebui", "comfyui"), "video": ("comfyui",)}


# ---------------------------------------------------------------------------- 模型候选


def model_candidates(role: str = "image") -> list[dict[str, Any]]:
    """这个角色能挑的模型：``[{id, label, source}]``，顺序 configured → catalog → local，同一个 id 先到先得。

    * ``configured``：``@<role>`` 这个别名（永远在最前，它就是「设置里配好的那个」）+ 设置里那个模型的真实 id；
    * ``catalog``：模型目录缓存（``~/.storystudio/model_catalog.json``）里出这个模态的 id；
    * ``local``：最近一次本机扫描留在内存里的模型名（生图看 SD WebUI / ComfyUI）。

    **只读缓存，不发任何请求。** 目录只有用户在设置页点过「刷新模型目录」才有，本机那段只有扫过才有；
    两边都空时退回模板里那份静态默认清单（``templates.DEFAULT_COMPARE_MODELS``，source = ``default``），
    新装的机器上「多模型对比」照样能展开成几张图。
    """

    role = str(role or "image")
    alias = f"@{role}"
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(model_id: Any, label: str, source: str) -> None:
        key = str(model_id or "").strip()
        if not key or key.lower() in seen:
            return
        seen.add(key.lower())
        out.append({"id": key, "label": label or key, "source": source})

    configured = ""
    try:
        current = settings_mod.load_settings()
        configured = str(current.role(role).model or "").strip() if role in settings_mod.ROLES else ""
    except Exception:  # noqa: BLE001 - 设置文件坏了不该让新建页打不开
        configured = ""
    add(alias, f"{alias} → {configured}" if configured else alias, "configured")
    add(configured, configured, "configured")

    try:
        catalog = model_catalog.load_catalog()
    except Exception:  # noqa: BLE001 - 缓存文件坏了当没拉过
        catalog = {}
    for source in model_catalog.SOURCES:
        part = catalog.get(source)
        models = part.get("models") if isinstance(part, dict) else None
        for entry in models or []:
            if isinstance(entry, dict) and role in (entry.get("roles") or ()):
                add(entry.get("id"), str(entry.get("name") or entry.get("id") or ""), "catalog")

    for row in cached_services(LOCAL_SERVICES_FOR_ROLE.get(role, ())):
        if not row.get("alive"):
            continue
        service = str(row.get("name") or row.get("id") or "")
        for model in row.get("models") or []:
            add(model, f"{model} · {service}" if service else str(model), "local")

    if len(out) == 1 and role == "image":       # 只剩别名：没配模型、目录没拉过、本机没扫过
        for model in T.DEFAULT_COMPARE_MODELS:
            add(model, str(model), "default")
    return out


# ---------------------------------------------------------------------------- 辅助


def _root() -> Path:
    return deps.PROJECT_ROOT


def _examples_dir(module: str) -> Path:
    return _resource("examples", module)


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


class AssetUpload(BaseModel):
    filename: str
    data_base64: str
    who: str | None = None


class GenerateRequest(BaseModel):
    only: list[str] | None = None
    force: bool = False


class PickRequest(BaseModel):
    item: str | None = None
    attempt: str | None = None
    picks: list[str] | None = None


class PostRequest(BaseModel):
    background: bool = False


class ExampleCreateRequest(BaseModel):
    name: str | None = None
    id: str | None = None


def make_router(module: str) -> APIRouter:
    module = S.check_module(module)
    r = APIRouter(prefix=f"/api/{module}", tags=[module])
    base = f"/api/{module}"

    def load(spec_id: str) -> dict[str, Any]:
        try:
            return S.load(module, spec_id, project_root=_root())
        except S.SpecError as exc:
            raise HTTPException(404, str(exc)) from exc

    def write(spec_like: dict[str, Any], *, spec_id: str) -> dict[str, Any]:
        try:
            normalized = S.normalize(spec_like, module=module, project_root=_root(), spec_id=spec_id)
        except S.SpecError as exc:
            raise HTTPException(400, f"改动没通过校验，没有写盘：{exc}") from exc
        S.save(normalized)
        return normalized

    def busy(spec_id: str) -> dict[str, str | None]:
        return {name: tasks.active_task_id(kind, spec_id) for name, kind in KINDS.items()}

    def item_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for item in spec["items"]:
            status = G.item_status(spec, item)
            rows.append({
                **{k: item.get(k) for k in ("id", "label", "prompt", "negative", "aspect_ratio", "meta", "current", "request_hash")},
                **status,
                "url": f"{base}/{spec['id']}/artifact/raw/{item['current']}" if status["done"] else None,
                "picked": not spec["picks"] or item["id"] in spec["picks"],
                "attempts": [{**a, "url": f"{base}/{spec['id']}/artifact/raw/{a['file']}" if a.get("file") and not a.get("error")
                              and (S.raw_dir(spec) / a["file"]).is_file() else None} for a in item.get("attempts") or []],
            })
        return rows

    def outputs_payload(spec: dict[str, Any], run: dict[str, Any] | None) -> dict[str, Any] | None:
        if not run:
            return None
        return {**run, "outputs": [{**o, "url": f"{base}/{spec['id']}/artifact/out/{o['file']}"} for o in run["outputs"]]}

    def exports_payload(spec: dict[str, Any]) -> list[dict[str, Any]]:
        return [{**row, "url": f"{base}/{spec['id']}/artifact/export/{row['name']}"} for row in E.list_packages(spec)]

    def progress(spec: dict[str, Any]) -> dict[str, Any]:
        done = sum(1 for i in spec["items"] if S.current_path(spec, i) is not None)
        return {"done": done, "total": len(spec["items"]), "picked": len(S.picked_items(spec)),
                "posted": PO.latest_run(spec) is not None, "exported": bool(E.list_packages(spec))}

    def cover_url(spec: dict[str, Any]) -> str | None:
        for item in S.picked_items(spec) or []:
            return f"{base}/{spec['id']}/artifact/raw/{item['current']}"
        return None

    def detail(spec: dict[str, Any]) -> dict[str, Any]:
        template = T.template_for_spec(spec)
        return {
            "id": spec["id"], "module": module, "title": spec["title"], "template": spec["template"], "template_name": template.name,
            "spec": S.to_disk(spec), "items": item_rows(spec), "picks": list(spec["picks"]),
            "assets": [{**e, "url": f"{base}/{spec['id']}/artifact/asset/{Path(e['file']).name}" if e["path"] else None}
                       for e in S.asset_entries(spec)],
            "progress": progress(spec), "outputs": outputs_payload(spec, PO.latest_run(spec)), "exports": exports_payload(spec),
            "busy": busy(spec["id"]), "template_info": template.payload(), "font_available": PO.font_available(),
            "cover_url": cover_url(spec), "model_candidates": model_candidates("image"),
        }

    def taken_ids() -> list[str]:
        # 自动编号时回收站里的 id 也算占用：删一个再建一个，新项目要是拿到刚删的 id，回收站里那一份就再也恢复不回来
        return S.existing_ids(_root(), module) + trash.archived_ids(_root(), module)

    def fresh_id(requested: str | None) -> str:
        spec_id = str(requested or "").strip() or S.next_id(module, taken_ids())
        if not is_valid_id(spec_id):
            raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
        if S.spec_path(_root(), module, spec_id).exists():
            raise HTTPException(409, f"项目 {spec_id} 已经存在，换一个 id")
        return spec_id

    # ------------------------------------------------------------------ 列表 / 编号 / 模板 / 新建

    @r.get("")
    def list_sets() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for spec_id in S.existing_ids(_root(), module):
            entry: dict[str, Any] = {"id": spec_id, "module": module, "pipeline": module,
                                     "path": f"content/{module}/{spec_id}.json"}
            try:
                spec = S.load(module, spec_id, project_root=_root())
            except Exception as exc:  # noqa: BLE001 - 一个坏文件不能让列表挂掉
                entry.update({"status": "error", "error": str(exc)})
                rows.append(entry)
                continue
            template = T.template_for_spec(spec)
            entry.update({"status": "ok", "title": spec["title"], "template": spec["template"], "template_name": template.name,
                          "style": spec["style"], "progress": progress(spec), "cover_url": cover_url(spec)})
            rows.append(entry)
        return rows

    @r.get("/next-id")
    def next_id() -> dict[str, str]:
        return {"id": S.next_id(module, taken_ids())}

    @r.get("/templates")
    def templates() -> dict[str, Any]:
        examples_dir = _examples_dir(module)
        from imagecraft import cards as C

        return {
            "module": module,
            # 每个模板多带一份「适合」的风格卡（DESIGN §41）：前端的风格行不用再单独拉一次卡库
            "templates": [{**t.payload(), "style_cards": [C.card_payload(c) for c in T.style_cards_for(t)]}
                          for t in T.templates_for(module)],
            "style_card_categories": [dict(c) for c in C.categories()],
            "image_dialects": list(S.IMAGE_DIALECT_CHOICES),
            "subject_kinds": list(S.SUBJECT_KINDS), "max_assets": S.MAX_ASSETS, "who_choices": [w for w in S.WHO_CHOICES if w],
            "default_image_params": dict(S.DEFAULT_IMAGE_PARAMS), "default_fidelity": S.DEFAULT_FIDELITY,
            "examples": sorted(p.stem for p in examples_dir.glob("*.json")) if examples_dir.is_dir() else [],
            "font_available": PO.font_available(),
            # 「多模型对比」的模型多选从这里取候选（模板注册是静态数据，读不到设置与缓存）；只读缓存，不发请求
            "model_candidates": model_candidates("image"),
        }

    @r.post("")
    def create(body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise HTTPException(400, "请求体要是项目文件对象")
        spec_id = fresh_id(body.get("id"))
        spec = write(body, spec_id=spec_id)
        if not spec["items"]:
            spec["items"] = T.expand_items(spec)
            S.save(spec)
        return detail(spec)

    @r.post("/examples/create")
    def create_from_example(req: ExampleCreateRequest | None = None) -> dict[str, Any]:
        req = req or ExampleCreateRequest()
        name = Path(req.name or EXAMPLE_DEFAULTS[module]).name
        source = _examples_dir(module) / f"{name}.json"
        if not source.is_file():
            raise HTTPException(404, f"没有这个示例：{name}")
        spec_id = fresh_id(req.id)
        data = json.loads(source.read_text(encoding="utf-8-sig"))
        try:
            spec = S.normalize(data, module=module, project_root=_root(), spec_id=spec_id)
        except S.SpecError as exc:
            raise HTTPException(400, f"示例没通过结构校验（这是示例文件本身的问题）：{exc}") from exc
        if not spec["items"]:
            spec["items"] = T.expand_items(spec)
        S.save(spec)
        return {"example": name, "path": f"content/{module}/{spec_id}.json", **detail(spec)}

    # ------------------------------------------------------------------ 读 / 写 / 删

    @r.get("/{spec_id}")
    def get_set(spec_id: str) -> dict[str, Any]:
        return detail(load(spec_id))

    @r.put("/{spec_id}")
    def put_set(spec_id: str, body: dict[str, Any]) -> dict[str, Any]:
        load(spec_id)
        if str(body.get("id") or spec_id) != spec_id:
            raise HTTPException(400, f"项目文件里的 id「{body.get('id')}」跟地址里的 {spec_id} 对不上")
        return detail(write(body, spec_id=spec_id))

    @r.delete("/{spec_id}")
    def delete_set(spec_id: str) -> dict[str, Any]:
        """归档（回收站里能恢复）。有任务在跑（出图 / 后期 / 导出）、或有文件被占用时 409，什么都不动。"""

        if is_valid_id(spec_id) and any(busy(spec_id).values()):
            raise HTTPException(409, trash.TASKS_RUNNING)
        try:
            result = S.archive(module, spec_id, project_root=_root())
        except S.SpecError as exc:
            raise HTTPException(404, str(exc)) from exc
        except trash.FilesBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"archived_to": _relative(Path(result["archived_to"])), "output_moved": result["output_moved"],
                "assets_moved": result["assets_moved"]}

    # ------------------------------------------------------------------ 参考图

    @r.post("/{spec_id}/assets")
    def upload_asset(spec_id: str, req: AssetUpload) -> dict[str, Any]:
        spec = load(spec_id)
        who = str(req.who or "").strip()
        if who and who not in S.WHO_CHOICES:
            raise HTTPException(400, "who 只能是 bride / groom")
        ext = Path(req.filename or "").suffix.lower().lstrip(".")
        # HEIC（苹果手机默认的照片格式）放过扩展名这一关：由内容那一关转成 JPEG，或给出「在手机上导出为 JPG」的提示
        if ext not in IMAGE_SUFFIXES and ext not in HEIF_EXTENSIONS:
            raise HTTPException(400, f"只接受 {' / '.join(IMAGE_SUFFIXES)} 文件，收到的是 .{ext or '?'}")
        try:
            data = base64.b64decode(req.data_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"文件数据不是合法的 base64：{exc}") from exc
        if not data:
            raise HTTPException(400, "文件为空")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
        # 扩展名只是第一道快速拒绝；真假、后缀都按内容定（假 png 以前会 200 落盘，到后处理才炸）。纪念写真的照片多半是手机拍的：
        # MPO 取第一帧存成标准 JPEG、带 EXIF 方向的转正（竖拍的不能存成横的——出图参考图是按像素读的）
        try:
            stored, suffix, _width, _height = accept_image(data)
        except UploadContentError as exc:
            raise HTTPException(400, str(exc)) from exc
        stem = slugify(Path(req.filename).stem, fallback="ref")
        # 摘要按上传的原字节算（不是转正后的）：同一张照片再传一次照样认得出是同一张，老版本存下的条目也对得上
        digest = hashlib.sha256(data).hexdigest()[:8]
        target_dir = _root() / S.ASSETS_DIR / spec_id
        target = target_dir / f"{stem}-{digest}{suffix}"
        relative = target.relative_to(_root()).as_posix()
        raw = deps.read_json_bom(S.spec_path(_root(), module, spec_id))
        subject = raw.get("subject") if isinstance(raw.get("subject"), dict) else {}
        assets = list(subject.get("assets") or [])
        # 文件层按内容摘要早就幂等了，列表层也要：同一张图再传一次不再追加第二条（重复条目会白占 6 张的名额，
        # 婚纱模式换了 who 再传同一张，就地改那一条的 who 而不是多一条；没带 who 再传则原条目不动，不把已标的 who 抹掉）。
        # 按内容摘要认「同一张」而不是按完整路径：换了文件名（「photo (1).png」）的同一张图也算已经有了，沿用原来那条
        index = same_content_index([a.get("file") if isinstance(a, dict) else a for a in assets],
                                   target_dir.relative_to(_root()).as_posix(), digest)
        existing = index is not None
        if existing:
            relative = assets[index].get("file") if isinstance(assets[index], dict) else assets[index]
            target = _root() / relative
        if not existing and len(assets) >= S.MAX_ASSETS:
            raise HTTPException(400, f"参考图最多 {S.MAX_ASSETS} 张，现在有 {len(assets)} 张")
        target_dir.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(stored)
        entry = {"file": relative, "who": who} if who else relative
        if existing:
            if who:
                assets[index] = entry
        else:
            assets.append(entry)
        subject["assets"] = assets
        raw["subject"] = subject
        spec = write(raw, spec_id=spec_id)
        return {"file": relative, "bytes": len(stored), "who": who, "existing": existing,
                "url": f"{base}/{spec_id}/artifact/asset/{target.name}", "assets": detail(spec)["assets"]}

    # ------------------------------------------------------------------ 展开 / 出图 / 挑选 / 后处理 / 打包

    @r.post("/{spec_id}/expand")
    def expand(spec_id: str) -> dict[str, Any]:
        spec = load(spec_id)
        spec["items"] = T.expand_items(spec)
        spec["picks"] = [p for p in spec["picks"] if p in S.item_ids(spec)]
        S.save(spec)
        return detail(spec)

    def run_generate(spec_id: str, req: GenerateRequest) -> dict[str, Any]:
        spec = S.load(module, spec_id, project_root=_root())
        provider = PROVIDER_FACTORY() if PROVIDER_FACTORY else None
        result = G.generate_set(spec, only=req.only, force=req.force, provider=provider, note=_note)
        reason = deps.all_failed_message(result, unit="张")
        if reason:
            raise G.GenerationError(reason)   # 一张都没出：任务记 failed（部分失败仍正常返回，逐张原因在 result 里）
        return result

    @r.post("/{spec_id}/generate")
    def generate(spec_id: str, req: GenerateRequest | None = None) -> dict[str, Any]:
        req = req or GenerateRequest()
        spec = load(spec_id)
        if not spec["items"]:
            raise HTTPException(400, "还没有展开清单，先按模板展开")
        unknown = sorted(set(req.only or []) - set(S.item_ids(spec)))
        if unknown:
            raise HTTPException(404, f"没有这些图：{', '.join(unknown)}")
        if PROVIDER_FACTORY is None:
            deps.require_roles(None, "image")   # 起任务前先查生图角色：没配就 400，不起任务、不往项目文件记失败尝试
        try:
            task_id = tasks.start(KINDS["generate"], spec_id, lambda: run_generate(spec_id, req), params=req.model_dump(), module=module)
        except tasks.TaskBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        # 每张实际用的模型按 G.item_model 算（多模型对比模板每张换一个模型，写进 items[].meta.model），
        # 提示里只报项目级的 image_params.model 会跟网格里每张的标签自相矛盾
        targets = [i for i in spec["items"] if not req.only or i["id"] in req.only]
        models = list(dict.fromkeys(G.item_model(spec, i) for i in targets))
        count = len(targets)
        shown = "、".join(models)
        return {"task_id": task_id, "model": shown, "models": models, "count": count,
                "cost_note": local_mod.cost_note_for("image", count, f"最多 {count} 次生图请求（{shown}）；请求没变且图已在的会跳过")}

    @r.post("/{spec_id}/pick")
    def pick(spec_id: str, req: PickRequest) -> dict[str, Any]:
        spec = load(spec_id)
        if req.item:
            try:
                item = S.item_by_id(spec, req.item)
            except S.SpecError as exc:
                raise HTTPException(404, str(exc)) from exc
            attempt = next((a for a in item.get("attempts") or [] if a.get("file") == req.attempt and not a.get("error")), None)
            if not attempt or not (S.raw_dir(spec) / str(attempt["file"])).is_file():
                raise HTTPException(400, "attempt 要是这张图成功出过的文件名之一")
            item.update({"current": attempt["file"], "request_hash": attempt.get("request_hash"), "status": "done", "error": None})
        if req.picks is not None:
            spec["picks"] = req.picks
        try:
            spec = S.normalize(S.to_disk(spec), module=module, project_root=_root(), spec_id=spec_id)
        except S.SpecError as exc:
            raise HTTPException(400, str(exc)) from exc
        S.save(spec)
        return detail(spec)

    def run_post(spec_id: str) -> dict[str, Any]:
        spec = S.load(module, spec_id, project_root=_root())
        run = PO.run_post(spec, T.template_for_spec(spec), note=_note)
        return outputs_payload(spec, run) or {}

    @r.post("/{spec_id}/post")
    def post(spec_id: str, req: PostRequest | None = None) -> dict[str, Any]:
        req = req or PostRequest()
        spec = load(spec_id)
        if not S.picked_items(spec):
            raise HTTPException(425, "还没有可后处理的图——先出图，再挑选")
        if req.background:
            try:
                task_id = tasks.start(KINDS["post"], spec_id, lambda: run_post(spec_id), params=req.model_dump(), module=module)
            except tasks.TaskBusy as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"task_id": task_id}
        try:
            run = PO.run_post(spec, T.template_for_spec(spec))
        except PO.PostError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"run": outputs_payload(spec, run), "failed": run["failed"]}

    @r.post("/{spec_id}/export")
    def export(spec_id: str) -> dict[str, Any]:
        spec = load(spec_id)
        try:
            pkg = E.build_package(spec)
        except E.ExportError as exc:
            raise HTTPException(425, str(exc)) from exc
        return {"name": pkg["name"], "url": f"{base}/{spec_id}/artifact/export/{pkg['name']}", "files": pkg["files"],
                "bytes": pkg["bytes"], "zip_bytes": pkg["zip_bytes"], "items": pkg["items"], "exports": exports_payload(spec)}

    # ------------------------------------------------------------------ 产物流

    @r.get("/{spec_id}/artifact/raw/{name}")
    def artifact_raw(spec_id: str, name: str) -> FileResponse:
        spec = load(spec_id)
        name = _safe_name(name, (".png",))
        known = {a.get("file") for i in spec["items"] for a in i.get("attempts") or []} | {i.get("current") for i in spec["items"]}
        path = S.raw_dir(spec) / name
        if name not in known or not path.is_file() or path.is_symlink():
            raise HTTPException(404, "没有这个文件")
        return FileResponse(path, media_type="image/png")

    @r.get("/{spec_id}/artifact/out/{name}")
    def artifact_out(spec_id: str, name: str) -> FileResponse:
        spec = load(spec_id)
        name = _safe_name(name, (".png", ".jpg"))
        path = S.out_dir(spec) / name
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, "没有这个文件")
        return FileResponse(path, media_type="image/png" if name.endswith(".png") else "image/jpeg")

    @r.get("/{spec_id}/artifact/asset/{name}")
    def artifact_asset(spec_id: str, name: str) -> FileResponse:
        load(spec_id)
        name = _safe_name(name, tuple(IMAGE_SUFFIXES.values()))
        path = _root() / S.ASSETS_DIR / spec_id / name
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, "没有这个文件")
        return FileResponse(path)

    @r.get("/{spec_id}/artifact/export/{name}")
    def artifact_export(spec_id: str, name: str) -> FileResponse:
        spec = load(spec_id)
        path = E.resolve_zip(spec, name)
        if path is None:
            raise HTTPException(404, "没有这个文件")
        return FileResponse(path, media_type="application/zip", filename=path.name)

    return r


for _module in S.MODULES:
    router.include_router(make_router(_module))


__all__ = ["router", "make_router", "PROVIDER_FACTORY", "KINDS", "model_candidates", "LOCAL_SERVICES_FOR_ROLE"]
