"""Provider-neutral generation contracts (requests, task snapshots, protocol).

这里只放与任何具体服务商无关的数据类与协议；具体后端在 ``genkit.adapters`` 里按用户配置装配
（见 ``genkit.router``），仓库外的来源经 ``genkit.plugins`` 登记进来。

The provider deliberately performs only one remote operation per method call.
Polling, retries, attempt budgets, and persistence belong to the runner so a
remote task id can be committed before the first wait.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024


class ProviderError(RuntimeError):
    """Base error for generation provider operations."""


class ProviderUnavailable(ProviderError):
    """The provider (or a program it needs) is not available right now."""


class ProviderSetupRequired(ProviderUnavailable):
    """本机缺少这个来源需要的程序或登录——一次请求都没发出去，明确失败、装好后可重提。

    跟父类 ``ProviderUnavailable``（网关 / 服务暂时不可用，按超时类处理）分开：失败分类把它归到「模型没配置」，
    提示去装程序或换来源，而不是「多半是临时的，直接重提」。
    """


class ProviderCommandError(ProviderError):
    """The provider returned an error."""


class ProviderRejected(ProviderCommandError):
    """The provider rejected the request before creating a task."""


class ProviderNotConfigured(ProviderCommandError):
    """角色没配好（没选适配器 / 缺密钥 / 缺模型）：还没发出任何请求就能判定的本地配置错误。

    runner 把它当明确失败（可原地重提），绝不归入「提交结果不明」：要是它只是裸的 ``ProviderCommandError``，
    就会落进兜底 except 被当成网络类结果不明，整个项目卡在 blocked_submission_unknown。
    """


class ProviderTimeout(ProviderError):
    """A single provider call timed out."""


class ProviderProtocolError(ProviderError):
    """The provider returned a response that cannot be interpreted safely."""


class ProviderUnsupported(ProviderError):
    """This adapter cannot perform the requested capability (e.g. music on an image-only backend)."""


class QuotaExhausted(ProviderError):
    """The remote provider rejected work because its quota is exhausted."""


class ProviderRateLimited(QuotaExhausted):
    """服务商瞬时限流（HTTP 429 / 限流码，不是余额耗尽）。

    做成 ``QuotaExhausted`` 的子类：runner 对它走同一条「明确失败、可一键重提」的路（改成超时类会落进
    「结果不明」、重提不可用）；只是给用户的话不同——等几十秒再提，不是去充值。
    """


class DownloadError(ProviderError):
    """A remote artifact could not be downloaded completely."""


class DownloadTooLarge(DownloadError):
    """A remote artifact exceeded the configured byte limit."""


class TaskType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    MUSIC = "music"


class TaskState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    FINISHED = "finished"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in {TaskState.FINISHED, TaskState.FAILED}


@dataclass(frozen=True)
class AssetReference:
    """A previously uploaded provider asset and its semantic role."""

    asset_id: str
    type: str = "image"

    def __post_init__(self) -> None:
        if not self.asset_id.strip():
            raise ValueError("asset_id must not be empty")
        if not self.type.strip():
            raise ValueError("reference type must not be empty")

    def as_dict(self) -> dict[str, str]:
        return {"asset_id": self.asset_id, "type": self.type}


@dataclass(frozen=True)
class ImageRequest:
    """Provider-neutral image generation request."""

    name: str
    prompt: str
    negative_prompt: str | None = None
    model: str = "gpt-image-2"
    aspect_ratio: str = "9:16"
    image_size: str = "2K"
    quality: str | None = "high"
    reference_asset_ids: tuple[str, ...] = ()
    workspace_id: str | None = None
    extra_params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("name", self.name)
        _require_text("prompt", self.prompt)
        if self.negative_prompt is not None:
            _require_text("negative_prompt", self.negative_prompt)
        _require_text("model", self.model)
        _require_text("aspect_ratio", self.aspect_ratio)
        _require_text("image_size", self.image_size)
        refs = tuple(self.reference_asset_ids)
        if any(not str(asset_id).strip() for asset_id in refs):
            raise ValueError("reference_asset_ids must not contain empty ids")
        object.__setattr__(self, "reference_asset_ids", refs)
        object.__setattr__(self, "extra_params", dict(self.extra_params))


@dataclass(frozen=True)
class VideoRequest:
    """Provider-neutral video generation request."""

    name: str
    prompt: str
    duration: int
    negative_prompt: str | None = None
    model: str = "doubao-seedance-2-0-fast-260128"
    request_type: str = "text_prompt"
    references: tuple[AssetReference, ...] = ()
    aspect_ratio: str = "9:16"
    resolution: str = "720p"
    generate_audio: bool = True
    workspace_id: str | None = None
    extra_params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("name", self.name)
        _require_text("prompt", self.prompt)
        if self.negative_prompt is not None:
            _require_text("negative_prompt", self.negative_prompt)
        _require_text("model", self.model)
        _require_text("request_type", self.request_type)
        _require_text("aspect_ratio", self.aspect_ratio)
        _require_text("resolution", self.resolution)
        if isinstance(self.duration, bool) or int(self.duration) <= 0:
            raise ValueError("duration must be a positive integer")
        references = tuple(self.references)
        if any(not isinstance(reference, AssetReference) for reference in references):
            raise TypeError("references must contain AssetReference values")
        roles = {reference.type for reference in references}
        if self.request_type == "first_frame" and "first_frame" not in roles:
            raise ValueError("first_frame requests require a first_frame reference")
        if self.request_type == "first_last_frame" and not {
            "first_frame",
            "last_frame",
        }.issubset(roles):
            raise ValueError(
                "first_last_frame requests require first_frame and last_frame references"
            )
        if self.request_type == "reference_images" and not references:
            raise ValueError("reference_images requests require at least one reference")
        object.__setattr__(self, "duration", int(self.duration))
        object.__setattr__(self, "references", references)
        object.__setattr__(self, "extra_params", dict(self.extra_params))


@dataclass(frozen=True)
class MusicRequest:
    """Provider-neutral music generation request（DESIGN §44 / docs/music-spec.md §1）。

    ``count`` 是一次要出几条（服务商侧的 n）；``duration`` 是想要的秒数——音乐生成接口大多没有
    时长参数，所以时长由编译提示词的一句话表达，适配器不会把它塞进 ``extra_params``
    （真要传，写进项目文件的 ``music_params.extra_params``）。
    ``reference_asset_ids`` 是参考图的本地令牌，适配器按自己的接口上传或内联。
    """

    name: str
    prompt: str
    model: str = "@music"
    duration: int = 60
    count: int = 1
    reference_asset_ids: tuple[str, ...] = ()
    workspace_id: str | None = None
    extra_params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("name", self.name)
        _require_text("prompt", self.prompt)
        _require_text("model", self.model)
        if isinstance(self.duration, bool) or int(self.duration) <= 0:
            raise ValueError("duration must be a positive integer")
        if isinstance(self.count, bool) or not 1 <= int(self.count) <= 4:
            raise ValueError("count must be between 1 and 4")
        refs = tuple(self.reference_asset_ids)
        if any(not str(asset_id).strip() for asset_id in refs):
            raise ValueError("reference_asset_ids must not contain empty ids")
        object.__setattr__(self, "duration", int(self.duration))
        object.__setattr__(self, "count", int(self.count))
        object.__setattr__(self, "reference_asset_ids", refs)
        object.__setattr__(self, "extra_params", dict(self.extra_params))


@dataclass(frozen=True)
class TaskSubmission:
    """Normalized response from a paid generation submission."""

    task_id: str | None
    task_type: TaskType
    status: TaskState
    output_urls: tuple[str, ...] = ()
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_type", _task_type(self.task_type))
        object.__setattr__(self, "status", _task_state(self.status))
        object.__setattr__(self, "output_urls", tuple(self.output_urls))

    @property
    def terminal(self) -> bool:
        return self.status.terminal

    @property
    def succeeded(self) -> bool:
        return self.status is TaskState.FINISHED

    @property
    def result_urls(self) -> tuple[str, ...]:
        return self.output_urls

    @property
    def output_url(self) -> str | None:
        return self.output_urls[0] if self.output_urls else None

    @property
    def error(self) -> str | None:
        return self.error_message


@dataclass(frozen=True)
class TaskSnapshot:
    """Normalized result of one remote task-status probe."""

    task_id: str
    task_type: TaskType
    status: TaskState
    output_urls: tuple[str, ...] = ()
    error_message: str | None = None

    def __post_init__(self) -> None:
        _require_text("task_id", self.task_id)
        object.__setattr__(self, "task_type", _task_type(self.task_type))
        object.__setattr__(self, "status", _task_state(self.status))
        object.__setattr__(self, "output_urls", tuple(self.output_urls))

    @property
    def terminal(self) -> bool:
        return self.status.terminal

    @property
    def succeeded(self) -> bool:
        return self.status is TaskState.FINISHED

    @property
    def result_urls(self) -> tuple[str, ...]:
        return self.output_urls

    @property
    def output_url(self) -> str | None:
        return self.output_urls[0] if self.output_urls else None

    @property
    def error(self) -> str | None:
        return self.error_message


@dataclass(frozen=True)
class RemoteTask:
    """One historical provider task used to reconcile ambiguous submissions."""

    request_id: str
    name: str
    task_type: str
    status: str
    source: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        _require_text("request_id", self.request_id)
        _require_text("name", self.name)
        _require_text("task_type", self.task_type)
        _require_text("status", self.status)


@runtime_checkable
class GenerationProvider(Protocol):
    """Narrow boundary consumed by the resumable production runner."""

    name: str

    def upload_asset(self, path: str | Path, *, asset_type: str = "image", workspace_id: str | None = None) -> str: ...

    def submit(self, request: ImageRequest | VideoRequest | MusicRequest) -> TaskSubmission: ...

    def submit_image(self, request: ImageRequest) -> TaskSubmission: ...

    def submit_video(self, request: VideoRequest) -> TaskSubmission: ...

    def submit_music(self, request: MusicRequest) -> TaskSubmission: ...

    def get_task(self, task_id: str, task_type: TaskType | str) -> TaskSnapshot: ...

    def download(self, url: str, destination: str | Path, *, max_bytes: int | None = None,
                 timeout_seconds: float | None = None) -> Path: ...


@runtime_checkable
class TaskHistoryProvider(Protocol):
    """可选能力：查服务商那边的历史任务，只用于对账「提交结果不明」的任务。

    runner 用 ``list_tasks`` 按任务名 / 类型 / 日期检索，核心只核对类型与任务 id（或任务名）；来源自己的身份规则
    （比如只认本程序提交的）另见可选的 :class:`RemoteIdentityMatcher`。只接管唯一匹配，绝不猜。
    """

    def list_tasks(self, *, keyword: str | None = None, task_types: Sequence[str] = (), start_date: str | None = None,
                   end_date: str | None = None, workspace_id: str | None = None, page: int = 0,
                   page_size: int = 100) -> Sequence[RemoteTask]: ...


@runtime_checkable
class RemoteIdentityMatcher(Protocol):
    """可选能力（跟 :class:`TaskHistoryProvider` 分开，只有 ``list_tasks`` 的提供方照样算会查历史）：
    历史里一条类型、任务 id（或任务名）都对上的任务，按来源自己的身份规则还算不算本次提交。"""

    def match_remote(self, item: RemoteTask, *, expected_name: str, task_type: str,
                     task_id: str | None = None) -> bool: ...


# --------------------------------------------------------------------------- 通用下载


def download_url(
    url: str,
    destination: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    timeout_seconds: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    headers: Mapping[str, str] | None = None,
) -> Path:
    """把 http(s)/file/data URL 落到 ``destination``（先写临时文件再原子替换）。

    适配器统一用它：同步型后端（OpenAI Images 这类）把结果先落成本地文件，
    快照里给 ``file://`` URL；异步型后端给厂商的 http(s) 结果地址。
    """

    import base64
    import shutil as _shutil
    import tempfile as _tempfile

    dest = Path(destination).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    normalized = url.replace("\\u0026", "&")
    parsed = urlsplit(normalized)
    scheme = parsed.scheme.lower()
    tmp_fd, tmp_name = _tempfile.mkstemp(prefix=f".{dest.name}.", dir=dest.parent)
    os.close(tmp_fd)
    tmp = Path(tmp_name)
    try:
        if scheme == "file":
            source = Path(_file_url_to_path(normalized))
            if not source.is_file():
                raise DownloadError(f"local artifact does not exist: {source}")
            if source.stat().st_size > max_bytes:
                raise DownloadTooLarge(f"artifact exceeds {max_bytes} bytes: {source}")
            _shutil.copyfile(source, tmp)
        elif scheme == "data":
            header, _, payload = normalized.partition(",")
            raw = base64.b64decode(payload) if ";base64" in header else payload.encode("utf-8")
            if len(raw) > max_bytes:
                raise DownloadTooLarge(f"artifact exceeds {max_bytes} bytes")
            tmp.write_bytes(raw)
        elif scheme in {"http", "https"} and parsed.netloc:
            request = Request(normalized, headers=dict(headers or {}))
            with urlopen(request, timeout=timeout_seconds) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise DownloadTooLarge(f"artifact exceeds {max_bytes} bytes: {length}")
                written = 0
                with tmp.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > max_bytes:
                            raise DownloadTooLarge(f"artifact exceeds {max_bytes} bytes")
                        handle.write(chunk)
        else:
            raise DownloadError(f"unsupported artifact URL: {url[:80]}")
        if tmp.stat().st_size == 0:
            raise DownloadError(f"downloaded artifact is empty: {url[:80]}")
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return dest


def _file_url_to_path(url: str) -> str:
    from urllib.parse import unquote

    parsed = urlsplit(url)
    path = unquote(parsed.path)
    # Windows: file:///D:/x → /D:/x
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        path = f"//{parsed.netloc}{path}"
    return path


def path_to_file_url(path: str | Path) -> str:
    return Path(path).resolve().as_uri()


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _task_type(value: "TaskType | str") -> "TaskType":
    if isinstance(value, TaskType):
        return value
    try:
        return TaskType(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(f"unknown task type: {value!r}") from exc


def _task_state(value: "TaskState | str") -> "TaskState":
    if isinstance(value, TaskState):
        return value
    try:
        return TaskState(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(f"unknown task state: {value!r}") from exc
