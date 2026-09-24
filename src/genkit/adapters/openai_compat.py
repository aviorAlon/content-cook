"""OpenAI 兼容接口三件套（文本 / 生图 / 语音），全部用官方开源 SDK ``openai``。

同一份配置（base_url + api_key + model）适用于 OpenAI、OpenRouter、DeepSeek、Moonshot、通义、豆包等所有兼容网关。
SDK 客户端可注入假客户端做测试（``client`` 参数）。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Mapping

from ..providers import (
    ImageRequest,
    ProviderCommandError,
    ProviderRateLimited,
    ProviderRejected,
    QuotaExhausted,
    TaskState,
    TaskSubmission,
    TaskType,
)
from .base import Adapter, token_path

_SIZE_BY_RATIO = {"9:16": "1024x1536", "16:9": "1536x1024", "1:1": "1024x1024", "3:4": "1024x1536", "4:3": "1536x1024"}


def _translate(exc: Exception, what: str) -> Exception:
    try:
        import openai
    except ImportError:  # pragma: no cover
        return ProviderCommandError(f"{what}: {exc}")
    if isinstance(exc, openai.RateLimitError):
        # OpenAI 的 429 有两种：瞬时限流（code=rate_limit_exceeded，等几十秒）与余额耗尽（code=insufficient_quota）
        # ——按 body 里的 code / 文案分，别一律说成「余额不足」叫人去充值
        body = getattr(exc, "body", None)
        code = str(getattr(exc, "code", None) or (body.get("code") if isinstance(body, Mapping) else "") or "")
        if any(k in f"{code} {exc}".lower() for k in ("insufficient", "quota", "billing", "balance")):
            return QuotaExhausted(f"{what}: {exc}")
        return ProviderRateLimited(f"{what}: {exc}")
    if getattr(exc, "status_code", None) == 402:
        return QuotaExhausted(f"{what}: {exc}")
    if isinstance(exc, openai.BadRequestError) and any(k in str(exc).lower() for k in ("moderation", "safety", "policy", "content_policy")):
        return ProviderRejected(f"{what}: {exc}")
    return ProviderCommandError(f"{what}: {exc}")


def probe_models(adapter: "OpenAIBase") -> int:
    """「测试连接」真正探一次：GET {base_url}/models（免费、不产生任务）。返回列到的模型数。

    失败按最常见的三种翻成人话——连不上（域名 / 端口 / 代理）、401（密钥）、404（地址少了 /v1 或写错）；
    其余交给 :func:`_translate`。只校验填写、不联网的 ping 会把假域名假密钥都报成 ✓（ADP-01）。"""

    try:
        # 30 秒封顶：client 默认 300 秒是给出图用的，测试连接要马上给答案（黑洞地址不能把按钮卡几分钟）
        models = adapter.client.models.list(timeout=30.0)
    except Exception as exc:  # noqa: BLE001 - 下面按类型翻译
        try:
            import openai
        except ImportError:  # pragma: no cover
            raise _translate(exc, "models.list") from exc
        base = adapter.base_url or "（默认地址）"
        if isinstance(exc, openai.APIConnectionError):
            raise ProviderCommandError(f"连不上 {base}：{exc}——检查接口地址、网络或代理") from exc
        status = getattr(exc, "status_code", None)
        if status in (401, 403):
            raise ProviderCommandError(f"{base} 拒绝了这个密钥（{status}）：{exc}——检查 api_key") from exc
        if status == 404:
            raise ProviderCommandError(f"{base}/models 不存在（404）——接口地址是不是少了 /v1 或写错了") from exc
        raise _translate(exc, "models.list") from exc
    return len(list(getattr(models, "data", None) or []))


class OpenAIBase(Adapter):
    def __init__(self, config: Mapping[str, Any], *, client: Any = None, **kw: Any) -> None:
        super().__init__(config, **kw)
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI

            from ..local import api_key_for

            # openai SDK 构造时要求 api_key 非空。本机服务（Ollama / LM Studio / vLLM / llama.cpp /
            # 本地语音）通常没有鉴权，用户会把密钥留空——api_key_for 在本机地址下补一个显式的占位值
            # "local"（**不是凭据**，只为过 SDK 的非空校验）；远端留空仍是 "missing"，那是真的配错了，
            # 让服务商明确报 401，而不是这里假装有 key（DESIGN §45）。
            self._client = OpenAI(api_key=api_key_for(self.base_url, self.api_key), base_url=self.base_url or None,
                                  timeout=300.0, max_retries=1)
        return self._client


# 流式接收的总时长上限（秒）。单次读超时仍是调用方给的 timeout：流式时它是「两个片段之间最多等多久」，
# 推理型模型一直在吐推理片段，不会触发；总时长另设上限防止无限拖。
# 推理型模型可能先推理好几分钟才开始出正文，一次性等整包很容易超时，所以默认流式接收。
STREAM_MAX_SECONDS = 900.0


class _StreamUnsupported(Exception):
    """网关不接受 ``stream`` 参数：退回一次性请求。"""


def _usage_dict(usage: Any) -> dict[str, Any]:
    return usage.model_dump() if hasattr(usage, "model_dump") else (dict(usage) if isinstance(usage, Mapping) else {})


def _from_completion(completion: Any) -> tuple[str, dict[str, Any]]:
    try:
        text = completion.choices[0].message.content or ""
    except (AttributeError, IndexError) as exc:
        raise ProviderCommandError(f"chat.completions returned no choices: {completion}") from exc
    return str(text), {"usage": _usage_dict(getattr(completion, "usage", None)), "model": getattr(completion, "model", None)}


def _mentions_stream(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) in (400, 422) and "stream" in str(exc).lower()


class OpenAIChat(OpenAIBase):
    name = "openai_chat"

    def complete(self, system_prompt: str, user_prompt: str, *, json_mode: bool = True, temperature: float = 0.7,
                 timeout: float = 180.0, model: str | None = None, stream: bool = True) -> tuple[str, dict[str, Any]]:
        """默认流式接收：只拼正文（``delta.content``），推理片段（``reasoning_content``）只用来保持连接不超时。
        网关明确不接受流式参数（400 且报错提到 stream）时退回一次性请求；网关忽略 stream、直接回整包也照收。"""

        kwargs: dict[str, Any] = {
            "model": self.pick_model(model),
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": temperature,
            "timeout": timeout,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if stream:
            try:
                return self._complete_stream(kwargs)
            except _StreamUnsupported:
                pass
        try:
            completion = self.client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc, "chat.completions") from exc
        return _from_completion(completion)

    def _complete_stream(self, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        import time

        attempts = ({"stream": True, "stream_options": {"include_usage": True}}, {"stream": True})
        response = None
        for index, extra in enumerate(attempts):
            try:
                response = self.client.chat.completions.create(**kwargs, **extra)
                break
            except Exception as exc:  # noqa: BLE001
                if not _mentions_stream(exc):
                    raise _translate(exc, "chat.completions") from exc
                if index == len(attempts) - 1:
                    raise _StreamUnsupported() from exc
                # 有的网关认 stream、不认 stream_options（用量统计）：去掉它再试一次
        if hasattr(response, "choices"):
            return _from_completion(response)   # 网关忽略了 stream，直接回了整包
        parts: list[str] = []
        usage: Any = None
        model_name = None
        deadline = time.monotonic() + STREAM_MAX_SECONDS
        try:
            for chunk in response:
                model_name = model_name or getattr(chunk, "model", None)
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                for choice in getattr(chunk, "choices", None) or ():
                    content = getattr(getattr(choice, "delta", None), "content", None)
                    if content:
                        parts.append(content)
                if time.monotonic() > deadline:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
                    raise ProviderCommandError(f"chat.completions: 流式接收超过 {STREAM_MAX_SECONDS:.0f} 秒还没结束，已放弃")
        except ProviderCommandError:
            raise
        except Exception as exc:  # noqa: BLE001 - 读到一半断了（读超时 / 连接重置）
            raise _translate(exc, "chat.completions") from exc
        return "".join(parts), {"usage": _usage_dict(usage), "model": model_name}

    def complete_json(self, system_prompt: str, user_prompt: str, **kw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        text, meta = self.complete(system_prompt, user_prompt, json_mode=True, **kw)
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
        try:
            return json.loads(cleaned), meta
        except ValueError as exc:
            raise ValueError(f"model did not return JSON: {text[:300]}") from exc

    def ping(self) -> str:
        text, meta = self.complete("你只回答 OK。", "回复 OK", json_mode=False, timeout=60.0)
        return f"{meta.get('model') or self.model}: {text.strip()[:40]}"


class OpenAIImages(OpenAIBase):
    name = "openai_images"

    def submit_image(self, request: ImageRequest) -> TaskSubmission:
        model = self.pick_model(request.model)
        size = str(request.extra_params.get("size") or _SIZE_BY_RATIO.get(request.aspect_ratio, "1024x1536"))
        prompt = request.prompt + (f"\n\nAvoid: {request.negative_prompt}" if request.negative_prompt else "")
        kwargs: dict[str, Any] = {"model": model, "prompt": prompt, "size": size, "n": 1}
        if request.quality and "gpt-image" in model:  # 其他模型（seedream / gemini image 等）不认这个参数，传了直接 400
            kwargs["quality"] = request.quality
        refs = [token_path(t) for t in request.reference_asset_ids]
        try:
            if refs:
                handles = [open(p, "rb") for p in refs]
                try:
                    result = self.client.images.edit(image=handles if len(handles) > 1 else handles[0], **kwargs)
                finally:
                    for h in handles:
                        h.close()
            else:
                result = self.client.images.generate(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc, "images") from exc
        data = list(getattr(result, "data", None) or [])
        if not data:
            return TaskSubmission(task_id=None, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message="images: empty data")
        item = data[0]
        b64 = getattr(item, "b64_json", None)
        if b64:
            task_id = self.store_result(base64.b64decode(b64), suffix=".png", hint=request.name)
            return TaskSubmission(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.FINISHED)
        url = getattr(item, "url", None)
        if url:
            return TaskSubmission(task_id=f"url:{url}", task_type=TaskType.IMAGE, status=TaskState.FINISHED, output_urls=(url,))
        return TaskSubmission(task_id=None, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message="images: no b64_json/url")

    def ping(self) -> str:
        return f"reachable ({probe_models(self)} models), using {self.model}"


class OpenAISpeech(OpenAIBase):
    name = "openai_speech"

    def synthesize(self, text: str, destination: Path, *, voice: str | None = None, model: str | None = None) -> Path:
        chosen_voice = voice or str(self.config.get("voice") or "alloy")
        try:
            response = self.client.audio.speech.create(model=self.pick_model(model), voice=chosen_voice, input=text, response_format="mp3")
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc, "audio.speech") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = response.content if hasattr(response, "content") else bytes(response)
        destination.write_bytes(payload)
        return destination

    def ping(self) -> str:
        # 语音接口没有免费的「最小请求」（audio.speech 一调就计费），跟生图一样用 GET /models 验可达与密钥；
        # 之前只拼配置字符串、从不构造 client，假域名假密钥也报 ✓（ADP-01）
        count = probe_models(self)
        return f"reachable ({count} models), using {self.model}, voice {self.config.get('voice') or 'alloy'}"


def build_chat(config: Mapping[str, Any], **kw: Any) -> OpenAIChat:
    return OpenAIChat(config, **kw)


def build_images(config: Mapping[str, Any], **kw: Any) -> OpenAIImages:
    return OpenAIImages(config, **kw)


def build_speech(config: Mapping[str, Any], **kw: Any) -> OpenAISpeech:
    return OpenAISpeech(config, **kw)
