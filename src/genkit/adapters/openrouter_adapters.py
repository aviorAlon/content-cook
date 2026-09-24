"""OpenRouter：一个 key 覆盖生图 / 视频 / 语音（文本走 openai_chat，把 base_url 指到 openrouter 即可）。

用官方开源 SDK ``openrouter``（Apache-2.0）：
* 图：``client.images.generate``（/api/v1/images，支持 input_references 图生图）→ 同步返回 b64，落盘
* 视频：``client.video_generation.generate`` → ``get_generation`` 轮询 → ``get_video_content`` 下载
* 语音：``client.tts.create_speech``
SDK 可注入假客户端做测试（``client`` 参数）。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Mapping

from ..providers import ImageRequest, ProviderCommandError, ProviderRejected, QuotaExhausted, TaskSnapshot, TaskState, TaskSubmission, TaskType, VideoRequest
from .base import Adapter, to_data_url

PREFIX = "openrouter:"
_RESOLUTION_IMG = {"2K": "2K", "1K": "1K", "4K": "4K", "512": "512"}


def _client(config: Mapping[str, Any]):
    from openrouter import OpenRouter

    kwargs: dict[str, Any] = {"api_key": str(config.get("api_key") or "")}
    base = str(config.get("base_url") or "").rstrip("/")
    if base and "openrouter.ai/api/v1" not in base:
        kwargs["server_url"] = base
    return OpenRouter(**kwargs)


def _no_retry():
    """SDK 的「不重试」配置（假客户端不认这个参数也没关系：只在真 SDK 上传）。"""

    from openrouter.utils.retries import BackoffStrategy, RetryConfig

    return RetryConfig("none", BackoffStrategy(0, 0, 1.0, 0), False)


def _translate(exc: Exception, what: str) -> Exception:
    text = str(exc)
    low = text.lower()
    # 402 看 SDK 异常上的状态码，不在文案里找裸子串 "402"：生成 id、帧号、URL 里随手就带 402，会被记成欠费劝人充值
    if getattr(exc, "status_code", None) == 402 or "insufficient" in low or "credits" in low:
        return QuotaExhausted(f"{what}: {text[:300]}")
    if "moderation" in low or "flagged" in low or "policy" in low:
        return ProviderRejected(f"{what}: {text[:300]}")
    return ProviderCommandError(f"{what}: {text[:300]}")


class OpenRouterBase(Adapter):
    def __init__(self, config: Mapping[str, Any], *, client: Any = None, **kw: Any) -> None:
        super().__init__(config, **kw)
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = _client(self.config)
        return self._client


class OpenRouterImages(OpenRouterBase):
    name = "openrouter_images"

    def submit_image(self, request: ImageRequest) -> TaskSubmission:
        model = self.pick_model(request.model)
        prompt = request.prompt + (f"\n\nAvoid: {request.negative_prompt}" if request.negative_prompt else "")
        refs = [{"type": "image_url", "image_url": {"url": to_data_url(t)}} for t in request.reference_asset_ids]
        kwargs: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1, "output_format": "png",
                                  "aspect_ratio": request.aspect_ratio, "resolution": _RESOLUTION_IMG.get(request.image_size, "2K")}
        if request.quality and "gpt-image" in model:  # 其他模型（seedream / gemini image 等）不认这个参数，传了直接 400
            kwargs["quality"] = request.quality
        if refs:
            kwargs["input_references"] = refs
        try:
            response = self.client.images.generate(**kwargs)
        except Exception as exc:  # noqa: BLE001 - SDK 错误统一翻译
            raise _translate(exc, "openrouter images") from exc
        data = list(getattr(response, "data", None) or [])
        if not data:
            return TaskSubmission(task_id=None, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message="openrouter images: empty data")
        item = data[0]
        b64 = getattr(item, "b64_json", None) or (item.get("b64_json") if isinstance(item, Mapping) else None)
        if not b64:
            return TaskSubmission(task_id=None, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message="openrouter images: no b64_json")
        task_id = self.store_result(base64.b64decode(b64), suffix=".png", hint=request.name)
        return TaskSubmission(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.FINISHED)

    def ping(self) -> str:
        models = self.client.images.list_models()
        count = len(getattr(models, "data", None) or [])
        return f"{count} image models visible; using {self.model}"


class OpenRouterVideo(OpenRouterBase):
    name = "openrouter_video"

    def submit_video(self, request: VideoRequest) -> TaskSubmission:
        model = self.pick_model(request.model)
        prompt = request.prompt + (f"\n\nAvoid: {request.negative_prompt}" if request.negative_prompt else "")
        kwargs: dict[str, Any] = {"model": model, "prompt": prompt, "duration": int(request.duration),
                                  "resolution": str(request.resolution).lower(), "generate_audio": bool(request.generate_audio)}
        if request.aspect_ratio and request.aspect_ratio != "adaptive":
            kwargs["aspect_ratio"] = request.aspect_ratio
        refs = list(request.references)
        if request.request_type in {"first_frame", "first_last_frame"} and refs:
            frames = [{"type": "image_url", "image_url": {"url": to_data_url(refs[0].asset_id)}, "frame_type": "first"}]
            if request.request_type == "first_last_frame" and len(refs) > 1:
                frames.append({"type": "image_url", "image_url": {"url": to_data_url(refs[1].asset_id)}, "frame_type": "last"})
            kwargs["frame_images"] = frames
        elif refs:
            kwargs["input_references"] = [{"type": "image_url", "image_url": {"url": to_data_url(r.asset_id)}} for r in refs]
        try:
            response = self.client.video_generation.generate(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc, "openrouter video") from exc
        job_id = getattr(response, "id", None)
        if not job_id:
            return TaskSubmission(task_id=None, task_type=TaskType.VIDEO, status=TaskState.FAILED, error_message=f"openrouter video: no job id ({getattr(response, 'error', None)})")
        return TaskSubmission(task_id=f"{PREFIX}{job_id}", task_type=TaskType.VIDEO, status=TaskState.PENDING)

    def get_task(self, task_id: str) -> TaskSnapshot:
        job_id = task_id[len(PREFIX):] if task_id.startswith(PREFIX) else task_id
        try:
            response = self.client.video_generation.get_generation(job_id=job_id)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc, "openrouter video status") from exc
        status = str(getattr(response, "status", "") or "").lower()
        if status in {"completed", "succeeded", "success"}:
            # 内容要走带鉴权的 get_video_content：先落盘再给 file:// URL，runner 的下载不必带 key
            try:
                content = self.client.video_generation.get_video_content(job_id=job_id, index=0)
                # httpx.Response：有 iter_bytes 就分块写盘（不再整包二次拷贝）；假客户端 / 旧 SDK 退回 .content
                iterator = getattr(content, "iter_bytes", None)
                chunks = iterator(1 << 20) if callable(iterator) else [getattr(content, "content", None) or b""]
                local = self.store_stream(chunks, suffix=".mp4", hint=job_id)
            except Exception as exc:  # noqa: BLE001
                raise _translate(exc, "openrouter video content") from exc
            if not local:
                return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.PROCESSING)
            from ..providers import path_to_file_url
            return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.FINISHED,
                                output_urls=(path_to_file_url(local[len("file:"):]),))
        if status in {"failed", "error", "cancelled"}:
            err = getattr(response, "error", None)
            return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.FAILED, error_message=str(err or status))
        return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.PROCESSING if status in {"processing", "running"} else TaskState.PENDING)

    def ping(self) -> str:
        models = self.client.video_generation.list_videos_models()
        count = len(getattr(models, "data", None) or [])
        return f"{count} video models visible; using {self.model}"


class OpenRouterSpeech(OpenRouterBase):
    name = "openrouter_tts"

    def synthesize(self, text: str, destination: Path, *, voice: str | None = None, model: str | None = None) -> Path:
        kwargs: dict[str, Any] = {"input": text, "model": self.pick_model(model), "response_format": "mp3"}
        chosen = voice or str(self.config.get("voice") or "")
        if chosen:
            kwargs["voice"] = chosen
        try:
            response = self.client.tts.create_speech(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc, "openrouter tts") from exc
        payload = getattr(response, "content", None) or b""
        if not payload:
            raise ProviderCommandError("openrouter tts: empty audio")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(bytes(payload))
        return destination

    def ping(self) -> str:
        # 语音没有单独的模型列表接口，而 models.list 是公开的、验不了密钥：查当前 key 的信息
        # （GET /api/v1/key，只读、不计费）——假域名报连接错误、假 key 报 401。之前只拼字符串不联网（ADP-01）。
        # SDK 默认对连接错误按退避重试最长一小时：测试连接要的是马上给答案，这里关掉重试、30 秒封顶
        try:
            info = self.client.api_keys.get_current_key_metadata(retries=_no_retry(), timeout_ms=30_000)
        except Exception as exc:  # noqa: BLE001
            base = self.base_url or "https://openrouter.ai/api/v1"
            status = getattr(exc, "status_code", None)
            if status in (401, 403):
                raise ProviderCommandError(f"{base} 拒绝了这个密钥（{status}）：{str(exc)[:200]}——检查 api_key") from exc
            # 连接层的失败（httpx.ConnectError / ConnectTimeout、socket 错误）都没有状态码
            if status is None and (isinstance(exc, (OSError, ConnectionError)) or "connect" in type(exc).__name__.lower()):
                raise ProviderCommandError(f"连不上 {base}：{str(exc)[:200]}——检查接口地址、网络或代理") from exc
            raise _translate(exc, "openrouter tts ping (GET /key)") from exc
        data = getattr(info, "data", None)
        label = getattr(data, "label", None) or (data.get("label") if isinstance(data, Mapping) else None) or "key"
        return f"key ok ({label}); using {self.model}, voice {self.config.get('voice') or '-'}"


def build_images(config: Mapping[str, Any], **kw: Any) -> OpenRouterImages:
    return OpenRouterImages(config, **kw)


def build_video(config: Mapping[str, Any], **kw: Any) -> OpenRouterVideo:
    return OpenRouterVideo(config, **kw)


def build_tts(config: Mapping[str, Any], **kw: Any) -> OpenRouterSpeech:
    return OpenRouterSpeech(config, **kw)
