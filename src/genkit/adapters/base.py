"""适配器公共底座：一个可注入的 HTTP 客户端、参考图编码、同步结果落盘。

设计约束（与 runner 的契约一致）：每个方法只做一次远端操作；轮询、重试、持久化都在 runner。
同步型接口（一次请求直接返回图）把结果先写进缓存目录，任务 id 用 ``file:<path>`` 表示，
``get_task`` 不再联网；异步型接口任务 id 用 ``<adapter>:<remote_id>``。
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from ..providers import (
    ProviderCommandError,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderRejected,
    ProviderTimeout,
    ProviderUnsupported,
    QuotaExhausted,
)

HttpFn = Callable[..., "HttpResponse"]


class HttpResponse:
    def __init__(self, status: int, body: bytes, headers: Mapping[str, str] | None = None) -> None:
        self.status = status
        self.body = body
        self.headers = dict(headers or {})

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except ValueError as exc:
            raise ProviderCommandError(f"response is not JSON: {self.body[:200]!r}") from exc


def default_http(method: str, url: str, *, headers: Mapping[str, str] | None = None, json_body: Any = None,
                 data: bytes | None = None, timeout: float = 120.0) -> HttpResponse:
    """基于 requests 的一次 HTTP 调用；HTTP 错误码不抛异常，交给 raise_for_status 统一翻译。"""

    import requests

    try:
        response = requests.request(method.upper(), url, headers=dict(headers or {}), json=json_body, data=data, timeout=timeout)
    except requests.Timeout as exc:
        raise ProviderTimeout(f"{method} {url}: timeout") from exc
    except requests.RequestException as exc:
        raise ProviderTimeout(f"{method} {url}: {exc}") from exc
    return HttpResponse(response.status_code, response.content, dict(response.headers))


def raise_for_status(response: HttpResponse, *, what: str) -> None:
    if 200 <= response.status < 300:
        return
    text = response.body[:400].decode("utf-8", "replace")
    quota_words = "insufficient" in text.lower() or "quota" in text.lower() or "balance" in text.lower()
    if response.status == 429 and not quota_words:
        # 瞬时限流：等几十秒再提就好，不是余额问题——余额耗尽的 429 响应体会说 insufficient_quota / balance
        raise ProviderRateLimited(f"{what}: HTTP {response.status}: {text}")
    if response.status in {402, 429} or quota_words:
        raise QuotaExhausted(f"{what}: HTTP {response.status}: {text}")
    if response.status in {400, 403, 422} and any(k in text.lower() for k in ("moderation", "sensitive", "policy", "违规", "敏感", "审核")):
        raise ProviderRejected(f"{what}: HTTP {response.status}: {text}")
    raise ProviderCommandError(f"{what}: HTTP {response.status}: {text}")


class Adapter:
    """所有适配器的基类：持有角色配置与 http 函数。"""

    name = "adapter"

    def __init__(self, config: Mapping[str, Any], *, http: HttpFn | None = None, cache_dir: Path | None = None) -> None:
        self.config = dict(config)
        self.http = http or default_http
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".storystudio" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or "").rstrip("/")

    @property
    def api_key(self) -> str:
        return str(self.config.get("api_key") or "")

    @property
    def model(self) -> str:
        return str(self.config.get("model") or "")

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    # ---- 能力兜底：适配器只实现自己会的那几件事，别的一律说清楚"这家不做"
    def submit_music(self, request: Any) -> Any:
        raise ProviderUnsupported(f"{self.name} 不支持生成配乐——在「模型设置」里给「配乐」角色选一个会出音乐的来源")

    def pick_model(self, requested: str | None) -> str:
        """spec 里写了模型名就用 spec 的，否则用设置里的默认模型。"""

        requested = str(requested or "").strip()
        if requested and not requested.startswith("@"):
            return requested
        if not self.model:
            raise ProviderNotConfigured(f"{self.name}: no model configured")
        return self.model

    # ---- 同步结果落盘 → file: 任务 id
    def store_result(self, data: bytes, *, suffix: str, hint: str) -> str:
        digest = hashlib.sha1(data).hexdigest()[:16]
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in hint)[:60]
        path = self.cache_dir / f"{safe}_{digest}{suffix}"
        if not path.exists():
            path.write_bytes(data)
        return f"file:{path.resolve()}"

    def store_stream(self, chunks: Any, *, suffix: str, hint: str) -> str:
        """分块落盘：先写临时文件边写边算哈希，再按哈希改名。给整包已在内存里的响应用也有意义——
        不再 bytes(payload) 二次拷贝；真正的流式要等 SDK 暴露 stream 接口。"""

        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in hint)[:60]
        tmp = self.cache_dir / f"{safe}_{now_ms()}.part"
        sha = hashlib.sha1()
        size = 0
        with tmp.open("wb") as fh:
            for chunk in chunks:
                if not chunk:
                    continue
                fh.write(chunk)
                sha.update(chunk)
                size += len(chunk)
        if size == 0:
            tmp.unlink(missing_ok=True)
            return ""
        path = self.cache_dir / f"{safe}_{sha.hexdigest()[:16]}{suffix}"
        if path.exists():
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(path)
        return f"file:{path.resolve()}"


# --------------------------------------------------------------------------- 参考图


def local_token(path: str | Path) -> str:
    """``upload_asset`` 的返回值：本地文件的引用令牌。适配器按需编码成 base64 / data URL。"""

    return f"local:{Path(path).resolve()}"


def token_path(token: str) -> Path:
    if token.startswith("local:"):
        return Path(token[len("local:"):])
    return Path(token)


def to_data_url(token_or_path: str | Path) -> str:
    path = token_path(str(token_or_path))
    if not path.is_file():
        raise ProviderCommandError(f"reference image does not exist: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def now_ms() -> int:
    return int(time.time() * 1000)
