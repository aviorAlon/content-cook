"""生图：本机 Stable Diffusion WebUI（AUTOMATIC1111）/ Forge 的 ``/sdapi/v1`` 接口。同步返回，直接落盘。

本机服务的三点不同（docs/local-models-spec.md §2/§3/§4.1）：不要 key（``api_key`` 允许为空，
只有挡在反向代理后面时才填）、不花钱、地址是 ``http://127.0.0.1:7860`` 这种本机地址。
WebUI 必须带 ``--api`` 启动（``webui-user.bat`` 的 ``COMMANDLINE_ARGS`` 里加），否则这些路径 404。

四个用到的接口：

* ``POST /sdapi/v1/txt2img``：``{prompt, negative_prompt, width, height, steps, cfg_scale,
  sampler_name, seed, batch_size, n_iter}`` → ``{images: ["<base64>"], parameters, info: "<json 字符串>"}``
* ``POST /sdapi/v1/img2img``：同上再加 ``init_images: ["<base64>"]`` 与 ``denoising_strength``；
  **只取第一张参考图**——这个接口的 ``init_images`` 虽然是数组，但一次只重绘一张底图，多出来的
  不会变成"多参考图"。多给了就在 ``last_info["notes"]`` 与产物旁的 ``.info.json`` 里记一句，不闷声丢掉。
* ``GET /sdapi/v1/options`` / ``POST /sdapi/v1/options {"sd_model_checkpoint": ...}``：读 / 切当前模型
* ``GET /sdapi/v1/sd-models``：模型清单（``ping`` 与设置页的"扫描本机"用）

尺寸规则（``aspect_ratio`` + ``image_size`` → ``width``/``height``，一律取 8 的倍数）：
短边固定 **1024**（SDXL 原生），``image_size`` 写 ``1K`` / ``2K`` / ``4K`` 都是 1024。
理由：SD 的基础出图分辨率一旦明显超过 checkpoint 的原生尺寸就会出"双头 / 重复肢体"，
放大是 WebUI 里 hires-fix / extras 的活，不该由基础分辨率硬顶上去；``4K`` 给到 1536 作为折中上限。
显式要小图（``image_size`` 写 ``512`` 或 ``SD15``，即 SD1.5 一档的模型）才降到 512。
需要精确尺寸时 ``extra_params`` 里直接给 ``width`` / ``height``（仍会对齐到 8 的倍数）。

**这个适配器还没对真实 SD WebUI 验证过**：字段名、响应形状全部按其公开 API 文档（``/docs`` 里的
``StableDiffusionTxt2ImgProcessingAPI``）写，只有假 HTTP 测试。见 docs/local-models-spec.md §7/§8。
"""

from __future__ import annotations

import base64
import binascii
import json
import math
from pathlib import Path
from typing import Any, Mapping

from ..providers import (
    ImageRequest,
    ProviderCommandError,
    ProviderError,
    ProviderTimeout,
    TaskState,
    TaskSubmission,
    TaskType,
)
from .base import Adapter, HttpResponse, raise_for_status, token_path

DEFAULT_BASE_URL = "http://127.0.0.1:7860"
DEFAULT_SHORT_SIDE = 1024
# image_size 档位 → 短边像素。见模块头的尺寸规则。
SHORT_SIDE_BY_SIZE = {"512": 512, "SD15": 512, "1K": 1024, "2K": 1024, "4K": 1536}
# 采样参数默认值；config["extra"] 与 request.extra_params 都能逐项覆盖。
DEFAULT_PARAMS: dict[str, Any] = {
    "steps": 30,
    "cfg_scale": 7.0,
    "sampler_name": "DPM++ 2M Karras",
    "seed": -1,
    "denoising_strength": 0.6,
}
PARAM_KEYS = ("steps", "cfg_scale", "sampler_name", "seed", "denoising_strength", "width", "height")
# 出图可能很慢（本机显卡、一张 SDXL 十几秒到几分钟），别用默认的 120 秒
GENERATE_TIMEOUT = 900.0
SWITCH_TIMEOUT = 600.0   # 切 checkpoint 要把几个 G 的权重读进显存

_OFFLINE_MARKERS = (
    "connection refused", "actively refused", "refused it", "10061", "111]",
    "failed to establish", "max retries", "newconnectionerror", "connectionerror",
    "connection aborted", "connection reset", "unreachable", "no route to host",
    "name or service not known", "拒绝",
)


# --------------------------------------------------------------------------- 纯函数（好测）


def round8(value: float) -> int:
    """对齐到 8 的倍数——SD 的 VAE 按 8 像素下采样，不是 8 的倍数会被服务端悄悄裁掉。
    用 ``floor(x + 0.5)`` 而不是 ``round``：后者对 .5 走银行家舍入（900 → 896），尺寸这种地方要可预期。"""

    return max(8, int(math.floor(float(value) / 8.0 + 0.5)) * 8)


def parse_ratio(aspect_ratio: str) -> tuple[float, float]:
    text = str(aspect_ratio or "").strip().replace("：", ":").replace("x", ":").replace("×", ":")
    if ":" in text:
        left, _, right = text.partition(":")
        try:
            w, h = float(left), float(right)
        except ValueError:
            w = h = 0.0
        if w > 0 and h > 0:
            return w, h
    return 9.0, 16.0  # 认不出来（"adaptive" 这类）按竖屏来，和其它生图适配器的兜底一致


def dimensions(aspect_ratio: str, image_size: str, *, short_side: int | None = None) -> tuple[int, int]:
    """画幅 + 尺寸档 → (width, height)，都是 8 的倍数。"""

    base = int(short_side or SHORT_SIDE_BY_SIZE.get(str(image_size or "").strip().upper(), DEFAULT_SHORT_SIDE))
    w, h = parse_ratio(aspect_ratio)
    if w <= h:
        return round8(base), round8(base * h / w)
    return round8(base * w / h), round8(base)


def normalize_checkpoint(value: str) -> str:
    """``sd_xl_base_1.0.safetensors [31e35c80fc]`` / ``SDXL/sd_xl_base_1.0.safetensors`` → ``sd_xl_base_1.0``。

    ``GET /options`` 回的 ``sd_model_checkpoint`` 是 WebUI 的"标题"（带哈希、可能带子目录），
    用户在设置里填的往往只是文件名，直接字符串比较会每张图都白切一次模型。"""

    text = str(value or "").strip()
    text = text.split(" [")[0].strip()
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in (".safetensors", ".ckpt", ".sft", ".pth"):
        if text.lower().endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.lower()


def same_checkpoint(current: str, wanted: str) -> bool:
    left, right = normalize_checkpoint(current), normalize_checkpoint(wanted)
    return bool(left) and bool(right) and left == right


def decode_image(value: str) -> bytes:
    """``images[0]``：裸 base64，个别分支带 ``data:image/png;base64,`` 前缀。"""

    text = str(value or "")
    if text.startswith("data:"):
        text = text.split(",", 1)[-1]
    try:
        raw = base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ProviderCommandError(f"sd webui 返回的图片不是合法 base64：{text[:60]!r}") from exc
    if not raw:
        raise ProviderCommandError("sd webui 返回了空图片")
    return raw


def suffix_for(raw: bytes) -> str:
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def error_text(response: HttpResponse) -> str:
    """SD WebUI 的报错形状：500 是 ``{"error": "OutOfMemoryError", "detail": "", "body": "", "errors": "CUDA out of memory"}``，
    FastAPI 的 422 是 ``{"detail": [{...}]}``。把能读的都拼出来给用户看。"""

    body = response.body[:2000].decode("utf-8", "replace")
    try:
        data = json.loads(body)
    except ValueError:
        return body.strip()
    if isinstance(data, Mapping):
        parts = []
        for key in ("error", "errors", "detail", "msg", "message"):
            value = data.get(key)
            if not value:
                continue
            parts.append(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        if parts:
            return "；".join(dict.fromkeys(parts))
    return body.strip()


# --------------------------------------------------------------------------- 适配器


class SDWebUI(Adapter):
    name = "sdwebui"

    def __init__(self, config: Mapping[str, Any], **kw: Any) -> None:
        super().__init__(config, **kw)
        self._checkpoint: str | None = None      # 本进程内缓存当前 checkpoint，一批图只切一次
        self.last_info: dict[str, Any] = {}

    @property
    def base_url(self) -> str:
        return (str(self.config.get("base_url") or "").rstrip("/")) or DEFAULT_BASE_URL

    # ---- 参数
    def params(self, request: ImageRequest | None = None) -> dict[str, Any]:
        merged = dict(DEFAULT_PARAMS)
        for source in (self.config.get("extra") or {}, (request.extra_params if request else {}) or {}):
            if not isinstance(source, Mapping):
                continue
            for key in PARAM_KEYS:
                if source.get(key) is not None:
                    merged[key] = source[key]
        return merged

    def model_for(self, request: ImageRequest) -> str:
        """spec 里写了具体模型就用 spec 的，否则用设置里的；两边都空 = 用 WebUI 里当前加载的那个
        （本机服务没有"模型必填"的道理——用户可能就想用界面上选好的）。"""

        requested = str(getattr(request, "model", "") or "").strip()
        if requested and not requested.startswith("@"):
            return requested
        return self.model

    # ---- checkpoint
    def current_checkpoint(self) -> str:
        if self._checkpoint is None:
            data = self._request("GET", "/sdapi/v1/options", what="读取当前模型", timeout=60.0)
            self._checkpoint = str((data or {}).get("sd_model_checkpoint") or "") if isinstance(data, Mapping) else ""
        return self._checkpoint

    def ensure_checkpoint(self, model: str) -> str:
        """需要就切模型；返回真正切过去的模型名，不用切返回空串。"""

        wanted = str(model or "").strip()
        if not wanted or same_checkpoint(self.current_checkpoint(), wanted):
            return ""
        self._request("POST", "/sdapi/v1/options", json_body={"sd_model_checkpoint": wanted},
                      what=f"切换模型到 {wanted}", timeout=SWITCH_TIMEOUT)
        self._checkpoint = wanted
        return wanted

    def list_models(self) -> list[str]:
        data = self._request("GET", "/sdapi/v1/sd-models", what="读取模型清单", timeout=60.0)
        if not isinstance(data, list):
            return []
        return [str(m.get("title") or m.get("model_name") or "") for m in data if isinstance(m, Mapping)]

    # ---- 生图
    def submit_image(self, request: ImageRequest) -> TaskSubmission:
        params = self.params(request)
        switched = self.ensure_checkpoint(self.model_for(request))
        width, height = dimensions(request.aspect_ratio, request.image_size)
        if params.get("width"):
            width = round8(params["width"])
        if params.get("height"):
            height = round8(params["height"])
        body: dict[str, Any] = {
            "prompt": request.prompt,
            "negative_prompt": str(request.negative_prompt or ""),
            "width": width,
            "height": height,
            "steps": int(params["steps"]),
            "cfg_scale": float(params["cfg_scale"]),
            "sampler_name": str(params["sampler_name"]),
            "seed": int(params["seed"]),
            "batch_size": 1,
            "n_iter": 1,
        }
        notes: list[str] = []
        refs = [token_path(token) for token in request.reference_asset_ids]
        endpoint = "txt2img"
        if refs:
            endpoint = "img2img"
            body["init_images"] = [self._encode_reference(refs[0])]
            body["denoising_strength"] = float(params["denoising_strength"])
            if len(refs) > 1:
                dropped = "、".join(p.name for p in refs[1:])
                notes.append(
                    f"SD WebUI 的 img2img 一次只重绘一张底图：用了 {refs[0].name}，"
                    f"另外 {len(refs) - 1} 张参考图（{dropped}）没有发给本机服务——"
                    "要多图控制请改用 ControlNet / IP-Adapter 扩展，或换一个吃多参考图的生图来源"
                )
        if switched:
            notes.append(f"已把本机 WebUI 的模型切到 {switched}")

        data = self._request("POST", f"/sdapi/v1/{endpoint}", json_body=body,
                             what=f"{endpoint} 出图", timeout=GENERATE_TIMEOUT)
        images = (data or {}).get("images") if isinstance(data, Mapping) else None
        if not images:
            detail = json.dumps(data, ensure_ascii=False)[:200] if data else ""
            return TaskSubmission(task_id=None, task_type=TaskType.IMAGE, status=TaskState.FAILED,
                                  error_message=f"sd webui {endpoint} 没有返回图片{('：' + detail) if detail else ''}")
        raw = decode_image(images[0])
        task_id = self.store_result(raw, suffix=suffix_for(raw), hint=request.name)
        self.last_info = {
            "endpoint": endpoint,
            "base_url": self.base_url,
            "checkpoint": self._checkpoint or "",
            "switched_checkpoint": switched,
            "request": {k: v for k, v in body.items() if k != "init_images"},
            "reference_images": [str(p) for p in refs],
            "notes": notes,
            "server_info": _parse_server_info(data),
        }
        self._write_sidecar(task_id)
        return TaskSubmission(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.FINISHED)

    def ping(self) -> str:
        models = self.list_models()
        current = self.current_checkpoint() or "（未知）"
        return f"本机 SD WebUI 可用（{self.base_url}），{len(models)} 个模型，当前 {current}"

    # ---- 内部
    def _encode_reference(self, path: Path) -> str:
        if not path.is_file():
            raise ProviderCommandError(f"reference image does not exist: {path}")
        return base64.b64encode(path.read_bytes()).decode("ascii")

    def _write_sidecar(self, task_id: str) -> None:
        """把这次的参数/备注写到产物旁边的 ``.info.json``：本机出图要能复现，
        多参考图被丢掉这种事也必须留痕（不塞进 TaskSubmission.error_message——那是失败原因，
        成功的任务写了会被账本当错误记）。"""

        try:
            path = Path(task_id[len("file:"):]) if task_id.startswith("file:") else Path(task_id)
            path.with_suffix(".info.json").write_text(
                json.dumps(self.last_info, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _request(self, method: str, path: str, *, what: str, json_body: Any = None, timeout: float = 120.0) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = self.http(method, url, headers=self.auth_headers(), json_body=json_body, timeout=timeout)
        except (ProviderError, OSError) as exc:
            raise self._transport_error(exc, what) from exc
        if not 200 <= response.status < 300:
            # 走公共的 raise_for_status 分类（余额 / 审核 / 其它），只是把正文换成服务端的 error/detail 原话。
            # 本机服务没有余额一说，现实中落到 timeout（连不上）与 other（爆显存、模型名写错）两桶。
            raise_for_status(HttpResponse(response.status, error_text(response).encode("utf-8")),
                             what=f"sd webui {what}（{self.base_url}）")
        if not response.body:
            return {}
        return response.json()

    def _transport_error(self, exc: Exception, what: str) -> ProviderError:
        text = f"{exc}".lower()
        if isinstance(exc, (ConnectionRefusedError, ConnectionError)) or any(m in text for m in _OFFLINE_MARKERS):
            return ProviderTimeout(
                f"连不上本机 Stable Diffusion WebUI（{self.base_url}）——{what}失败。"
                "确认 WebUI 已经在这台机器上启动，并且启动参数带了 --api"
                "（webui-user.bat 的 COMMANDLINE_ARGS 里加 --api），地址和端口与「模型设置」里填的一致"
            )
        return ProviderTimeout(f"sd webui {what}（{self.base_url}）：{exc}")


def _parse_server_info(data: Mapping[str, Any]) -> Any:
    """响应里的 ``info`` 是一个 JSON 字符串（种子、实际用的采样器等）。解不出来就留原文。"""

    info = data.get("info") if isinstance(data, Mapping) else None
    if isinstance(info, str):
        try:
            return json.loads(info)
        except ValueError:
            return info[:2000]
    return info


def build(config: Mapping[str, Any], **kw: Any) -> SDWebUI:
    return SDWebUI(config, **kw)
