"""ComfyUI（本机）：把一份 API 格式的工作流按映射改几个字段，提交给本机的 ComfyUI 跑。生图 + 生视频。

流程（本机服务，没有鉴权，也不花钱）：

* 提交 ``POST /prompt`` ``{"prompt": <API 格式工作流>, "client_id": <uuid>}`` → ``{"prompt_id": ...}``，
  任务 id 是 ``comfy:<prompt_id>``（router 按前缀分派轮询）
* 轮询 ``GET /history/{prompt_id}``：出现记录就看映射里 ``output`` 那个节点的 ``images`` / ``gifs`` / ``videos``；
  历史里还没有就再看一眼 ``GET /queue``——两边都找不到（ComfyUI 重启过、任务被清掉）就直接报失败，
  而不是无限轮询下去
* 取件 ``GET /view?filename=&subfolder=&type=output``（二进制），快照里给的就是这个地址：本机接口不带密钥，
  运行时的通用下载器能直接拉；``download()`` 也留着，给想经适配器落盘的调用方用
* 参考图 ``POST /upload/image``（multipart，字段 ``image`` + ``overwrite=true``）→ ``{name, subfolder}``，
  按文件 sha1 缓存，同一张图重投不重复上传；返回的文件名填进工作流的 LoadImage

工作流：内置三份在 ``skills/local-models/workflows/``，用户自己在 ComfyUI 里「保存（API 格式）」导出的
放 ``<项目根>/local_workflows/``，每份配一份同名 ``.map.json`` 说明哪个节点的哪个字段是提示词 / 负面 /
宽 / 高 / 步数 / 种子 / 输入图 / 输出节点。角色配置的 ``extra.workflow`` 选用哪一份，不选就用该任务类型的
第一份内置工作流。

种子每次随机（记进运行记录，好结果能复现）；工作流千差万别，能力表只记画幅与尺寸区间，不吹参考图与原生音频。

**这个适配器还没对真实 ComfyUI 验证过**：所有接口形状按 ComfyUI 公开文档 / 前端源码写
（见 docs/local-models-spec.md §8），只有假 HTTP 测试。
"""

from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import random
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from ..paths import code_root, data_root
from ..providers import (
    ImageRequest, ProviderCommandError, TaskSnapshot, TaskState, TaskSubmission, TaskType, VideoRequest,
)
from .base import Adapter, raise_for_status, token_path

PREFIX = "comfy:"
DEFAULT_BASE_URL = "http://127.0.0.1:8188"
BUILTIN_DIR = code_root() / "skills" / "local-models" / "workflows"
USER_DIR_NAME = "local_workflows"
# 内置工作流的顺序就是「没选工作流时用哪一份」的顺序（每个任务类型取第一份）
BUILTIN_NAMES = ("txt2img_sdxl", "img2img_sdxl", "txt2video_wan")
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
# 映射里这些键指向工作流的一个输入位；``output`` 是节点号，单独处理
PATCH_KEYS = ("prompt", "negative", "width", "height", "steps", "seed", "image", "model", "cfg", "denoise", "length", "fps")
META_KEYS = ("task", "title", "_comment")
OUTPUT_KEYS = ("images", "gifs", "videos", "audio")
# image_size 档 → 短边像素（SDXL 的原生边长是 1024）
SHORT_SIDE = {"1K": 1024, "2K": 1440, "4K": 2048}
DEFAULT_SHORT_SIDE = 1024
MAX_SEED = 2**32 - 1
# 本机权重文件的后缀：只有长这样的模型名才会被填进工作流的 ckpt_name / unet_name
CKPT_SUFFIXES = (".safetensors", ".ckpt", ".sft", ".pt", ".pth", ".gguf", ".bin")


# --------------------------------------------------------------------------- 工作流：读取 / 映射 / 打补丁


@dataclass(frozen=True)
class Workflow:
    """一份 API 格式工作流 + 它的映射。``graph`` 是可以直接发给 ``/prompt`` 的节点表（已去掉注释键）。"""

    name: str
    graph: dict[str, Any]
    mapping: dict[str, Any]
    task: str = "image"
    source: str = "builtin"
    path: str = ""
    title: str = ""

    @property
    def output_node(self) -> str:
        return _output_node(self.mapping)

    def keys(self) -> list[str]:
        return [k for k in PATCH_KEYS if k in self.mapping]


def user_dir(project_root: str | Path | None = None) -> Path:
    """用户自己的工作流目录：``<项目根>/local_workflows/``。"""

    root = Path(project_root).expanduser() if project_root else data_root()
    return root / USER_DIR_NAME


def _is_node(value: Any) -> bool:
    return isinstance(value, Mapping) and isinstance(value.get("class_type"), str)


def _clean_graph(raw: Mapping[str, Any], *, name: str) -> dict[str, Any]:
    """去掉 ``_comment`` 这类非节点键——ComfyUI 只认 ``{class_type, inputs}``，多一个键就是 400。"""

    graph = {str(k): copy.deepcopy(v) for k, v in raw.items() if _is_node(v)}
    if not graph:
        raise ProviderCommandError(f"工作流 {name} 里没有节点——要的是 ComfyUI 的「保存（API 格式）」导出，不是界面里那份带 nodes/links 的 .json")
    return graph


def _output_node(mapping: Mapping[str, Any]) -> str:
    value = mapping.get("output")
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    return str(value or "")


def _read_json(path: Path, *, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProviderCommandError(f"读不到{what}：{path}（{exc}）") from exc
    except ValueError as exc:
        raise ProviderCommandError(f"{what}不是合法 JSON：{path}（{exc}）") from exc


def _find(name: str, project_root: str | Path | None) -> tuple[Path, str]:
    """先看用户目录（同名可覆盖内置），再看内置目录。返回 (工作流文件, 来源)。"""

    if not NAME_RE.match(str(name or "")):
        raise ProviderCommandError(f"工作流名不合法：{name!r}（只能是字母、数字、下划线、点和短横）")
    candidates = [(user_dir(project_root) / f"{name}.json", "user"), (BUILTIN_DIR / f"{name}.json", "builtin")]
    for path, source in candidates:
        if path.is_file():
            return path, source
    known = ", ".join(w["name"] for w in list_workflows(project_root)) or "（一份都没有）"
    raise ProviderCommandError(f"找不到工作流 {name}：内置的和 {user_dir(project_root)} 里都没有。现有的是 {known}")


def load_workflow(name: str, project_root: str | Path | None = None) -> Workflow:
    """按名字读一份工作流 + 它的 ``.map.json``。用户目录优先，找不到就报一句人话。"""

    path, source = _find(name, project_root)
    return _load_file(path, source)


def _load_file(path: Path, source: str) -> Workflow:
    name = path.stem
    map_path = path.with_name(f"{path.stem}.map.json")
    if not map_path.is_file():
        raise ProviderCommandError(f"工作流 {name} 少了映射文件 {map_path.name}：要一份同名的 .map.json 说明哪个节点的哪个字段是提示词 / 负面 / 宽 / 高 / 种子 / 输入图 / 输出节点")
    raw = _read_json(path, what=f"工作流 {name}")
    mapping_raw = _read_json(map_path, what=f"工作流 {name} 的映射")
    if not isinstance(raw, Mapping) or not isinstance(mapping_raw, Mapping):
        raise ProviderCommandError(f"工作流 {name} 与它的映射都必须是 JSON 对象")
    mapping = {str(k): v for k, v in mapping_raw.items()}
    graph = _clean_graph(raw, name=name)
    task = str(mapping.get("task") or "image").strip().lower()
    if task not in ("image", "video"):
        raise ProviderCommandError(f"工作流 {name} 的映射里 task 只能是 image 或 video，现在是 {task!r}")
    return Workflow(name=name, graph=graph, mapping=mapping, task=task, source=source,
                    path=str(path), title=str(mapping.get("title") or name))


def missing_paths(workflow: Workflow) -> list[dict[str, Any]]:
    """把映射套到工作流上做一次干跑：返回指不到的项（``{key, path, reason}``）。"""

    missing: list[dict[str, Any]] = []
    for key, value in workflow.mapping.items():
        if key in META_KEYS:
            continue
        if key == "output":
            node = _output_node(workflow.mapping)
            if not node:
                missing.append({"key": "output", "path": "", "reason": "映射里没写输出节点"})
            elif node not in workflow.graph:
                missing.append({"key": "output", "path": node, "reason": f"工作流里没有 {node} 号节点"})
            continue
        try:
            _locate(workflow, key, value)
        except ProviderCommandError as exc:
            missing.append({"key": key, "path": _path_text(value), "reason": str(exc)})
    return missing


def check_mapping(name: str, project_root: str | Path | None = None) -> dict[str, Any]:
    """设置页的「检查映射」：报这份工作流的映射有哪些路径找不到。工作流本身读不了时也返回结构，不抛。"""

    try:
        workflow = load_workflow(name, project_root)
    except ProviderCommandError as exc:
        return {"name": name, "ok": False, "error": str(exc), "missing": [], "keys": []}
    missing = missing_paths(workflow)
    return {"name": name, "ok": not missing, "error": "", "missing": missing, "keys": workflow.keys(),
            "task": workflow.task, "source": workflow.source, "title": workflow.title,
            "output": workflow.output_node, "path": workflow.path}


def _describe(path: Path, source: str) -> dict[str, Any]:
    name = path.stem
    entry: dict[str, Any] = {"name": name, "source": source, "path": str(path), "task": "image", "title": name,
                             "ok": False, "error": "", "keys": [], "missing": []}
    try:
        workflow = _load_file(path, source)
    except ProviderCommandError as exc:
        entry["error"] = str(exc)
        return entry
    missing = missing_paths(workflow)
    entry.update({"task": workflow.task, "title": workflow.title, "keys": workflow.keys(),
                  "missing": missing, "ok": not missing, "output": workflow.output_node,
                  "comment": str(workflow.mapping.get("_comment") or "")})
    return entry


def builtin_workflows() -> list[dict[str, Any]]:
    """内置工作流清单（每条含 name / task / 映射是否合法 / 缺失路径）。不读用户目录，import 后随时可调。"""

    return [_describe(BUILTIN_DIR / f"{name}.json", "builtin") for name in BUILTIN_NAMES if (BUILTIN_DIR / f"{name}.json").is_file()]


def list_workflows(project_root: str | Path | None = None) -> list[dict[str, Any]]:
    """内置 + ``<项目根>/local_workflows/`` 里的工作流；同名时用户那份在前（加载时也是用户优先）。"""

    rows = builtin_workflows()
    folder = user_dir(project_root)
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            if path.name.endswith(".map.json"):
                continue
            entry = _describe(path, "user")
            rows = [r for r in rows if r["name"] != entry["name"]]
            rows.insert(0, entry)
    return rows


def default_workflow_name(task: str, project_root: str | Path | None = None) -> str:
    """该任务类型的第一份内置工作流；内置目录不在（打包漏了）时退到用户目录里的第一份。"""

    for row in builtin_workflows():
        if row["task"] == task:
            return str(row["name"])
    for row in list_workflows(project_root):
        if row["task"] == task:
            return str(row["name"])
    raise ProviderCommandError(f"没有可用的{'视频' if task == 'video' else '生图'}工作流：内置的 {BUILTIN_DIR} 不在，{user_dir(project_root)} 里也没有")


def _path_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "/".join(str(p) for p in value)
    return str(value)


def _locate(workflow: Workflow, key: str, raw_path: Any) -> tuple[dict[str, Any], str]:
    """把 ``[节点号, "inputs", 字段]`` 解成 (容器, 末段键)，指不到就报一句点名工作流 / 键 / 路径的话。"""

    def fail(reason: str) -> ProviderCommandError:
        return ProviderCommandError(
            f"工作流 {workflow.name} 的映射里 {key} 指向 {_path_text(raw_path)}，{reason}"
            f"——在 ComfyUI 里重新「保存（API 格式）」导出，或改 {workflow.name}.map.json"
        )

    if not isinstance(raw_path, (list, tuple)) or len(raw_path) < 2:
        raise fail("但路径要写成 [节点号, \"inputs\", 字段名]")
    parts = [str(p) for p in raw_path]
    node_id = parts[0]
    node = workflow.graph.get(node_id)
    if not isinstance(node, dict):
        raise fail(f"但这份工作流里没有 {node_id} 号节点")
    container: Any = node
    for part in parts[1:-1]:
        nxt = container.get(part) if isinstance(container, dict) else None
        if not isinstance(nxt, dict):
            raise fail(f"但 {node_id} 号节点（{node.get('class_type')}）下面没有 {part}")
        container = nxt
    last = parts[-1]
    if last not in container:
        raise fail(f"但 {node_id} 号节点（{node.get('class_type')}）的 {parts[1]} 里没有 {last} 这一项")
    return container, last


def patch(workflow: Workflow, values: Mapping[str, Any]) -> dict[str, Any]:
    """按映射把值写进工作流的副本；映射里没有的键跳过，值是 None 的也跳过。"""

    graph = copy.deepcopy(workflow.graph)
    patched = Workflow(name=workflow.name, graph=graph, mapping=workflow.mapping, task=workflow.task,
                       source=workflow.source, path=workflow.path, title=workflow.title)
    for key, value in values.items():
        if value is None or key not in workflow.mapping:
            continue
        container, last = _locate(patched, key, workflow.mapping[key])
        container[last] = value
    return graph


def current_value(workflow: Workflow, key: str, fallback: Any = None) -> Any:
    """工作流里这个位置现在的值（算帧数要知道工作流自己的帧率）；映射里没有就给 fallback。"""

    if key not in workflow.mapping:
        return fallback
    try:
        container, last = _locate(workflow, key, workflow.mapping[key])
    except ProviderCommandError:
        return fallback
    return container.get(last, fallback)


# --------------------------------------------------------------------------- 尺寸 / 帧数


def size_for(aspect_ratio: str, image_size: str | None = None, *, short_side: int | None = None) -> tuple[int, int]:
    """画幅 + 尺寸档 → (宽, 高)，按短边算，取 8 的整数倍（SD 系列的潜空间是 8 倍下采样）。"""

    base = short_side or SHORT_SIDE.get(str(image_size or "").upper(), DEFAULT_SHORT_SIDE)
    parts = re.split(r"[:xX×/]", str(aspect_ratio or "9:16").strip())
    try:
        w, h = float(parts[0]), float(parts[1])
    except (IndexError, ValueError):
        w, h = 9.0, 16.0
    if w <= 0 or h <= 0:
        w, h = 9.0, 16.0
    if w <= h:
        width, height = base, base * h / w
    else:
        width, height = base * w / h, base
    return _round8(width), _round8(height)


def _round8(value: float) -> int:
    return max(64, int(round(value / 8.0)) * 8)


def frames_for(duration: float, fps: float) -> int:
    """时长 × 帧率 → 帧数，取 4n+1（Wan / Hunyuan 这类视频模型的潜空间要求）。"""

    total = max(1.0, float(duration)) * max(1.0, float(fps))
    return int(max(1, round((total - 1) / 4.0)) * 4 + 1)


# --------------------------------------------------------------------------- 适配器


def _multipart(fields: Mapping[str, str], file_field: str, filename: str, content: bytes, mime: str) -> tuple[bytes, str]:
    boundary = f"----storystudio{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {mime}\r\n\r\n".encode("utf-8") + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


@dataclass
class _Run:
    """一次提交的运行记录：种子和工作流写进缓存，好结果能照着复现；查询时也靠它知道输出节点。"""

    prompt_id: str = ""
    workflow: str = ""
    task: str = "image"
    output: str = ""
    seed: int = 0
    model: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"prompt_id": self.prompt_id, "workflow": self.workflow, "task": self.task,
                "output": self.output, "seed": self.seed, "model": self.model, **self.extra}


class ComfyUI(Adapter):
    """本机 ComfyUI：一个类同时管生图和生视频（同一套 /prompt · /history · /view）。"""

    name = "comfyui"

    def __init__(self, config: Mapping[str, Any], *, project_root: str | Path | None = None, **kw: Any) -> None:
        super().__init__(config, **kw)
        self.extra = dict(self.config.get("extra") or {})
        self.project_root = Path(project_root) if project_root else None
        self.client_id = str(self.extra.get("client_id") or uuid.uuid4())
        self.last_info: dict[str, Any] = {}

    @property
    def base_url(self) -> str:
        return (str(self.config.get("base_url") or "") or DEFAULT_BASE_URL).rstrip("/")

    # ---- 工作流
    def workflow_name(self, task: str, *, wants_image: bool = False) -> str:
        chosen = str(self.extra.get("workflow") or "").strip()
        if chosen:
            return chosen
        if wants_image:
            # 没指定又带了参考图：优先挑一份收输入图的内置工作流（图生图），挑不到再回退
            for row in builtin_workflows():
                if row["task"] == task and "image" in row.get("keys", ()):
                    return str(row["name"])
        return default_workflow_name(task, self.project_root)

    def workflow(self, task: str, *, wants_image: bool = False, override: str = "") -> Workflow:
        name = str(override or "").strip() or self.workflow_name(task, wants_image=wants_image)
        return load_workflow(name, self.project_root)

    # ---- 参考图：POST /upload/image，按文件 sha1 缓存（换服务器算另一份缓存）
    def upload_image(self, token: str) -> str:
        path = token_path(token)
        if not path.is_file():
            raise ProviderCommandError(f"参考图不存在：{path}")
        payload = path.read_bytes()
        key = f"{self.base_url}|{hashlib.sha1(payload).hexdigest()}"
        cache = self.cache_dir / "comfyui_uploads.json"
        table: dict[str, str] = {}
        if cache.is_file():
            try:
                loaded = json.loads(cache.read_text(encoding="utf-8"))
                table = {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, Mapping) else {}
            except (OSError, ValueError):
                table = {}
        if key in table:
            return table[key]
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        body, content_type = _multipart({"overwrite": "true", "type": "input"}, "image", path.name, payload, mime)
        response = self.http("POST", f"{self.base_url}/upload/image", headers={"Content-Type": content_type}, data=body, timeout=120.0)
        raise_for_status(response, what="comfyui upload/image")
        data = response.json()
        name = str((data or {}).get("name") or "") if isinstance(data, Mapping) else ""
        if not name:
            raise ProviderCommandError(f"ComfyUI 的 /upload/image 没有返回文件名：{json.dumps(data, ensure_ascii=False)[:200]}")
        subfolder = str(data.get("subfolder") or "")
        value = f"{subfolder}/{name}" if subfolder else name
        table[key] = value
        try:
            cache.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
        return value

    # ---- 提交
    def _seed(self, request_extra: Mapping[str, Any]) -> int:
        pinned = request_extra.get("seed", self.extra.get("seed"))
        if pinned is not None and str(pinned).strip() != "":
            try:
                return int(pinned) & MAX_SEED
            except (TypeError, ValueError):
                raise ProviderCommandError(f"种子要是整数，现在是 {pinned!r}") from None
        return random.SystemRandom().randrange(0, MAX_SEED)

    def _model_for(self, requested: str | None) -> str:
        """checkpoint / unet 的文件名。spec 里的模型名只有**看着像本机权重文件**才用——spec 的默认值是
        云端模型 id（doubao-seedance-…），塞进 ckpt_name 只会让 ComfyUI 报「不在列表里」。"""

        value = str(requested or "").strip()
        if value and not value.startswith("@") and value.lower().endswith(CKPT_SUFFIXES):
            return value
        return self.model

    def _submit(self, graph: Mapping[str, Any], run: _Run, task_type: TaskType) -> TaskSubmission:
        response = self.http("POST", f"{self.base_url}/prompt", json_body={"prompt": dict(graph), "client_id": self.client_id}, timeout=120.0)
        if response.status == 400:
            raise ProviderCommandError(f"ComfyUI 不接受这份工作流（{run.workflow}）：{_node_errors(response.body)}")
        raise_for_status(response, what="comfyui prompt")
        data = response.json()
        prompt_id = str((data or {}).get("prompt_id") or "") if isinstance(data, Mapping) else ""
        if not prompt_id:
            return TaskSubmission(task_id=None, task_type=task_type, status=TaskState.FAILED,
                                  error_message=f"ComfyUI 的 /prompt 没有返回 prompt_id：{json.dumps(data, ensure_ascii=False)[:200]}")
        run.prompt_id = prompt_id
        self._remember(run)
        return TaskSubmission(task_id=f"{PREFIX}{prompt_id}", task_type=task_type, status=TaskState.PENDING)

    def submit_image(self, request: ImageRequest) -> TaskSubmission:
        override = str(request.extra_params.get("workflow") or "")
        refs = list(request.reference_asset_ids)
        workflow = self.workflow("image", wants_image=bool(refs), override=override)
        if workflow.task != "image":
            raise ProviderCommandError(f"工作流 {workflow.name} 是{'视频' if workflow.task == 'video' else workflow.task}工作流，不能用来出图——在「模型设置」里给生图角色换一份")
        seed = self._seed(request.extra_params)
        width, height = size_for(request.aspect_ratio, request.image_size)
        values: dict[str, Any] = {
            "prompt": request.prompt,
            "negative": request.negative_prompt or "",
            "width": width, "height": height, "seed": seed,
            "steps": _int_or_none(request.extra_params.get("steps", self.extra.get("steps"))),
            "cfg": _float_or_none(request.extra_params.get("cfg", self.extra.get("cfg"))),
            "denoise": _float_or_none(request.extra_params.get("denoise", self.extra.get("denoise"))),
            "model": self._model_for(request.model) or None,
        }
        if "image" in workflow.mapping:
            if not refs:
                raise ProviderCommandError(f"工作流 {workflow.name} 要一张输入图（LoadImage），但这一张没有参考图——换成文生图工作流，或给这一镜配一张参考图")
            values["image"] = self.upload_image(refs[0])
        graph = patch(workflow, values)
        run = _Run(workflow=workflow.name, task="image", output=workflow.output_node, seed=seed,
                   model=str(values.get("model") or ""), extra={"width": width, "height": height, "prompt": request.prompt[:200]})
        return self._submit(graph, run, TaskType.IMAGE)

    def submit_video(self, request: VideoRequest) -> TaskSubmission:
        override = str(request.extra_params.get("workflow") or "")
        refs = [r for r in request.references if r.type != "audio"]
        workflow = self.workflow("video", wants_image=bool(refs), override=override)
        if workflow.task != "video":
            raise ProviderCommandError(f"工作流 {workflow.name} 是生图工作流，不能用来出视频——在「模型设置」里给视频角色换一份")
        seed = self._seed(request.extra_params)
        width, height = size_for(request.aspect_ratio, short_side=_resolution_short_side(request.resolution))
        fps = _float_or_none(request.extra_params.get("fps", self.extra.get("fps"))) or float(current_value(workflow, "fps", 16) or 16)
        values: dict[str, Any] = {
            "prompt": request.prompt,
            "negative": request.negative_prompt or "",
            "width": width, "height": height, "seed": seed,
            "length": frames_for(request.duration, fps),
            "fps": int(fps),
            "steps": _int_or_none(request.extra_params.get("steps", self.extra.get("steps"))),
            "cfg": _float_or_none(request.extra_params.get("cfg", self.extra.get("cfg"))),
            "model": self._model_for(request.model) or None,
        }
        if "image" in workflow.mapping:
            if not refs:
                raise ProviderCommandError(f"工作流 {workflow.name} 要一张首帧图（LoadImage），但这一镜没有——换成文生视频工作流，或给这一镜配首帧")
            first = next((r for r in refs if r.type in ("first_frame", "image")), refs[0])
            values["image"] = self.upload_image(first.asset_id)
        graph = patch(workflow, values)
        run = _Run(workflow=workflow.name, task="video", output=workflow.output_node, seed=seed,
                   model=str(values.get("model") or ""),
                   extra={"width": width, "height": height, "length": values["length"], "fps": int(fps), "prompt": request.prompt[:200]})
        return self._submit(graph, run, TaskType.VIDEO)

    # ---- 运行记录（种子 / 输出节点）
    def _run_path(self, prompt_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in prompt_id)[:80]
        return self.cache_dir / f"comfyui_run_{safe}.json"

    def _remember(self, run: _Run) -> None:
        self.last_info = run.as_dict()
        try:
            self._run_path(run.prompt_id).write_text(json.dumps(self.last_info, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _recall(self, prompt_id: str) -> dict[str, Any]:
        if self.last_info.get("prompt_id") == prompt_id:
            return dict(self.last_info)
        path = self._run_path(prompt_id)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, Mapping):
                    return dict(data)
            except (OSError, ValueError):
                pass
        return {}

    # ---- 轮询
    def get_task(self, task_id: str) -> TaskSnapshot:
        prompt_id = task_id[len(PREFIX):] if task_id.startswith(PREFIX) else task_id
        run = self._recall(prompt_id)
        kind = TaskType.VIDEO if str(run.get("task")) == "video" else TaskType.IMAGE
        response = self.http("GET", f"{self.base_url}/history/{prompt_id}", timeout=60.0)
        raise_for_status(response, what="comfyui history")
        history = response.json()
        entry = history.get(prompt_id) if isinstance(history, Mapping) else None
        if not isinstance(entry, Mapping):
            return self._queue_snapshot(task_id, prompt_id, kind)
        status = entry.get("status") if isinstance(entry.get("status"), Mapping) else {}
        if str(status.get("status_str") or "").lower() == "error":
            return TaskSnapshot(task_id=task_id, task_type=kind, status=TaskState.FAILED,
                                error_message=f"ComfyUI 报错：{_history_error(status)}")
        urls = self._outputs(entry.get("outputs"), str(run.get("output") or ""))
        if urls:
            if kind is TaskType.IMAGE and urls[0].endswith((".mp4", ".webm", ".gif")):
                kind = TaskType.VIDEO
            return TaskSnapshot(task_id=task_id, task_type=kind, status=TaskState.FINISHED, output_urls=urls)
        if status.get("completed") is True:
            return TaskSnapshot(task_id=task_id, task_type=kind, status=TaskState.FAILED,
                                error_message=f"ComfyUI 说这个任务跑完了，但输出节点 {run.get('output') or '（未知）'} 没有图片或视频——检查工作流的输出节点是不是 SaveImage / VHS_VideoCombine，以及映射里的 output 写的是不是它")
        return TaskSnapshot(task_id=task_id, task_type=kind, status=TaskState.PROCESSING)

    def _queue_snapshot(self, task_id: str, prompt_id: str, kind: TaskType) -> TaskSnapshot:
        """历史里还没有：在队列里就是排队/在跑，两边都没有就是任务丢了（ComfyUI 重启或队列被清空）。"""

        response = self.http("GET", f"{self.base_url}/queue", timeout=30.0)
        raise_for_status(response, what="comfyui queue")
        data = response.json()
        running = _queue_ids(data, "queue_running")
        if prompt_id in running:
            return TaskSnapshot(task_id=task_id, task_type=kind, status=TaskState.PROCESSING)
        if prompt_id in _queue_ids(data, "queue_pending"):
            return TaskSnapshot(task_id=task_id, task_type=kind, status=TaskState.PENDING)
        return TaskSnapshot(task_id=task_id, task_type=kind, status=TaskState.FAILED,
                            error_message=f"ComfyUI 的历史和队列里都没有 {prompt_id}：多半是 ComfyUI 重启过或队列被清空了，这一条重新生成即可")

    def _outputs(self, outputs: Any, output_node: str) -> tuple[str, ...]:
        """映射指定的输出节点优先；它不在（工作流被改过、记录丢了）就扫所有输出节点。"""

        if not isinstance(outputs, Mapping):
            return ()
        nodes: list[Any] = []
        if output_node and isinstance(outputs.get(output_node), Mapping):
            nodes.append(outputs[output_node])
        else:
            nodes.extend(v for v in outputs.values() if isinstance(v, Mapping))
        urls: list[str] = []
        for node in nodes:
            for key in OUTPUT_KEYS:
                for item in node.get(key) or ():
                    url = self.view_url(item)
                    if url and url not in urls:
                        urls.append(url)
        return tuple(urls)

    def view_url(self, item: Any) -> str:
        if not isinstance(item, Mapping) or not item.get("filename"):
            return ""
        query = {"filename": str(item["filename"]), "subfolder": str(item.get("subfolder") or ""), "type": str(item.get("type") or "output")}
        return f"{self.base_url}/view?" + "&".join(f"{k}={quote(v, safe='')}" for k, v in query.items())

    # ---- 取件：/view 是本机接口、不带密钥，运行时的通用下载器也能直接拉；这里给想经适配器落盘的调用方
    def download(self, url: str, destination: str | Path, *, max_bytes: int | None = None, timeout_seconds: float | None = None) -> Path:
        dest = Path(destination).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        response = self.http("GET", url, timeout=float(timeout_seconds or 600.0))
        raise_for_status(response, what="comfyui view")
        if not response.body:
            raise ProviderCommandError(f"ComfyUI 取件是空的：{url[:120]}")
        if max_bytes and len(response.body) > int(max_bytes):
            raise ProviderCommandError(f"成品超过 {max_bytes} 字节：{url[:120]}")
        dest.write_bytes(response.body)
        return dest

    def ping(self) -> str:
        response = self.http("GET", f"{self.base_url}/system_stats", timeout=20.0)
        raise_for_status(response, what="comfyui system_stats")
        data = response.json()
        system = data.get("system") if isinstance(data, Mapping) else {}
        version = str((system or {}).get("comfyui_version") or (system or {}).get("python_version") or "")
        devices = data.get("devices") if isinstance(data, Mapping) else []
        device = str(devices[0].get("name") or "") if isinstance(devices, list) and devices and isinstance(devices[0], Mapping) else ""
        rows = list_workflows(self.project_root)
        chosen = str(self.extra.get("workflow") or "") or "（按任务类型用第一份内置的）"
        return f"reachable{f' ComfyUI {version}' if version else ''}{f'，{device}' if device else ''}；工作流 {chosen}，可选 {len(rows)} 份"


def _queue_ids(data: Any, key: str) -> set[str]:
    """``/queue`` 的一项是 ``[序号, prompt_id, prompt, extra_data, outputs]``（前端源码里的形状）。"""

    out: set[str] = set()
    rows = data.get(key) if isinstance(data, Mapping) else None
    for row in rows or ():
        if isinstance(row, Sequence) and not isinstance(row, (str, bytes)) and len(row) >= 2:
            out.add(str(row[1]))
        elif isinstance(row, Mapping) and row.get("prompt_id"):
            out.add(str(row["prompt_id"]))
    return out


def _history_error(status: Mapping[str, Any]) -> str:
    messages = status.get("messages")
    for message in reversed(list(messages or ())):
        if isinstance(message, Sequence) and not isinstance(message, (str, bytes)) and len(message) >= 2:
            kind, payload = str(message[0]), message[1]
            if kind == "execution_error" and isinstance(payload, Mapping):
                return f"{payload.get('node_type') or ''} {payload.get('exception_message') or ''}".strip()
    return str(status.get("status_str") or "execution error")


def _node_errors(body: bytes) -> str:
    """``/prompt`` 的 400 正文：``{"error": {...}, "node_errors": {节点号: {...}}}``。"""

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return body[:300].decode("utf-8", "replace")
    error = data.get("error") if isinstance(data, Mapping) else None
    head = str((error or {}).get("message") or "") if isinstance(error, Mapping) else str(error or "")
    bits: list[str] = [head] if head else []
    node_errors = data.get("node_errors") if isinstance(data, Mapping) else None
    for node_id, detail in (node_errors or {}).items() if isinstance(node_errors, Mapping) else ():
        if isinstance(detail, Mapping):
            errs = "；".join(str(e.get("message") or "") for e in (detail.get("errors") or ()) if isinstance(e, Mapping))
            bits.append(f"{node_id} 号节点（{detail.get('class_type') or ''}）{errs}")
    return "；".join(b for b in bits if b)[:400] or body[:300].decode("utf-8", "replace")


def _int_or_none(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolution_short_side(resolution: str | None) -> int:
    """视频分辨率档 → 短边像素；认不出来按 480（Wan 1.3B 的原生档）。"""

    digits = "".join(ch for ch in str(resolution or "") if ch.isdigit())
    if not digits:
        return 480
    value = int(digits)
    if str(resolution or "").strip().upper().endswith("K") and value <= 8:
        return {1: 1024, 2: 1440, 4: 2160}.get(value, value * 1000)
    return value


def build(config: Mapping[str, Any], **kw: Any) -> ComfyUI:
    return ComfyUI(config, **kw)


__all__ = ["ComfyUI", "PREFIX", "Workflow", "build", "builtin_workflows", "check_mapping", "default_workflow_name",
           "frames_for", "list_workflows", "load_workflow", "missing_paths", "patch", "size_for", "user_dir"]
