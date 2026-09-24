"""按用户配置装配生成后端：一个对象同时满足 runner 的 ``GenerationProvider`` 协议，内部按角色分派。

* 参考图/首帧的「上传」不再联网：``upload_asset`` 返回 ``local:<路径>`` 令牌，适配器按需编码
* 同步型生图把结果落到缓存目录，任务 id 是 ``file:<路径>``；``get_task`` 直接给 ``file://`` URL
* 异步型任务 id 带适配器前缀（``ark:`` / ``minimax:`` / ``openrouter:`` / ``oaivid:`` / ``comfy:`` / 插件登记的前缀），
  ``get_task`` 按前缀分派（前缀表在 ``genkit.adapters.ASYNC_PREFIXES``）
* 配乐（DESIGN §44）走第五个角色 ``music``；不会出音乐的适配器抛 ``ProviderUnsupported``
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from . import plugins
from . import settings as settings_mod
from .adapters import adapter_for_task_id, build_adapter
from .adapters.base import local_token
from .providers import (
    DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    DEFAULT_MAX_DOWNLOAD_BYTES,
    ImageRequest,
    MusicRequest,
    ProviderCommandError,
    ProviderNotConfigured,
    ProviderUnavailable,
    TaskSnapshot,
    TaskState,
    TaskSubmission,
    TaskType,
    VideoRequest,
    download_url,
    path_to_file_url,
)

# 形状像「前缀:远端 id」的任务 id：前缀没人登记时说清楚是哪个来源没加载，而不是「认不出任务 id」
_PREFIX_SHAPE = re.compile(r"^([a-z][a-z0-9-]{1,23}:)")
# 任务类型 → 该任务归哪个角色（get_task 要按角色装配适配器）
_TASK_ROLE = {TaskType.VIDEO: "video", TaskType.MUSIC: "music", TaskType.IMAGE: "image"}
_ROLE_LABEL = {"video": "视频", "music": "配乐", "image": "生图"}


def _with_file_url(submission: TaskSubmission) -> TaskSubmission:
    """同步型适配器把结果落到缓存后只给 ``file:<路径>`` 任务 id：运行时"完成即下载"那一步要的是结果地址，
    这里统一补上 file:// URL。网关返回 base64 这类同步出图，以前就卡在这一步（完成了却没有结果地址）。"""

    task_id = str(submission.task_id or "")
    if submission.status is TaskState.FINISHED and not submission.output_urls and task_id.startswith("file:"):
        path = Path(task_id[len("file:"):])
        if path.is_file():
            return TaskSubmission(task_id=submission.task_id, task_type=submission.task_type, status=submission.status,
                                  output_urls=(path_to_file_url(path),), error_message=submission.error_message)
        return TaskSubmission(task_id=submission.task_id, task_type=submission.task_type, status=TaskState.FAILED,
                              error_message=f"cached result missing: {path}")
    return submission


class RoutedProvider:
    """runner 眼里的唯一 provider；真正干活的是按角色装配的适配器。"""

    name = "local"

    def __init__(self, settings: settings_mod.Settings, *, adapters: Mapping[str, Any] | None = None) -> None:
        self.settings = settings
        self._adapters: dict[str, Any] = dict(adapters or {})
        self.cache_dir = settings_mod.cache_dir(settings)

    # ---- 装配
    def adapter(self, role: str) -> Any:
        if role in self._adapters:
            return self._adapters[role]
        unavailable = settings_mod.adapter_unavailable(self.settings, role)
        if unavailable:
            # 选的来源由插件提供、插件没装上：明确失败（可重提），话里说清楚装插件还是换来源
            raise ProviderNotConfigured(unavailable)
        missing = settings_mod.validate_role(self.settings, role)
        if missing:
            # 专门的异常类：runner 靠它把「没配模型」归成明确失败（可重提），而不是「提交结果不明」把项目卡死
            raise ProviderNotConfigured(f"{role} 模型未配置：{', '.join(missing)}（在「模型设置」里填好再生成）")
        cfg = settings_mod.effective(self.settings.role(role), role)
        instance = build_adapter(role, cfg.adapter, cfg.as_dict(), cache_dir=self.cache_dir)
        self._adapters[role] = instance
        return instance

    @staticmethod
    def request_role(request: ImageRequest | VideoRequest | MusicRequest) -> str:
        return "image" if isinstance(request, ImageRequest) else "video" if isinstance(request, VideoRequest) else "music"

    def preflight(self, request: ImageRequest | VideoRequest | MusicRequest | str) -> None:
        """提交前的本地预检（runner 在把提交意图写进账本之前调）：这个请求（或直接给角色名）要交给的角色配好了没有。

        没配就抛 ``ProviderNotConfigured``——一次请求都不发、账本里一笔都不记。2026-09-22 审计 DRAMA-01：以前
        「模型没配」要到 submit 才由 ``adapter()`` 抛出，那时账本里已经写了 submitting，异常又被兜底当成网络类
        结果不明，整个项目永久卡在 blocked_submission_unknown，界面与命令行都没有出路。
        """

        self.adapter(request if isinstance(request, str) else self.request_role(request))

    def submission_source(self, request: ImageRequest | VideoRequest | MusicRequest) -> tuple[str, str]:
        """账本要记的真实来源：这个请求会交给哪个适配器（适配器自报的 id，与设置里该角色的 adapter 一致）、
        用哪个模型（``@image`` / ``@video`` 这类别名按适配器的 ``pick_model`` 解析成设置里的模型名，写了具体
        模型名就是那个名）。角色没配、适配器装不起来就记设置里的 adapter 与空模型——提交本身会给出人话错误，
        这里不猜、也不抛。"""

        role = self.request_role(request)
        adapter_id = str(self.settings.role(role).adapter or "")
        model = ""
        try:
            adapter = self.adapter(role)
            adapter_id = str(getattr(adapter, "name", "") or adapter_id)
            model = str(adapter.pick_model(getattr(request, "model", None)) or "")
        except Exception:  # noqa: BLE001 - 来源只是记录，装不起来的原因由 submit 报
            pass
        return adapter_id, model

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for role in settings_mod.ROLES:
            cfg = self.settings.role(role)
            out[role] = {"adapter": cfg.adapter, "model": settings_mod.effective(cfg, role).model if cfg.adapter else "",
                         "missing": settings_mod.validate_role(self.settings, role)}
        return out

    # ---- GenerationProvider 协议
    def upload_asset(self, path: str | Path, *, asset_type: str = "image", workspace_id: str | None = None) -> str:
        target = Path(path).expanduser().resolve()
        if not target.is_file():
            raise ProviderCommandError(f"asset does not exist: {target}")
        return local_token(target)

    def submit(self, request: ImageRequest | VideoRequest | MusicRequest) -> TaskSubmission:
        if isinstance(request, ImageRequest):
            return self.submit_image(request)
        if isinstance(request, VideoRequest):
            return self.submit_video(request)
        if isinstance(request, MusicRequest):
            return self.submit_music(request)
        raise TypeError(f"unsupported generation request: {type(request).__name__}")

    def submit_image(self, request: ImageRequest) -> TaskSubmission:
        return _with_file_url(self.adapter("image").submit_image(request))

    def submit_video(self, request: VideoRequest) -> TaskSubmission:
        return _with_file_url(self.adapter("video").submit_video(request))

    def submit_music(self, request: MusicRequest) -> TaskSubmission:
        return _with_file_url(self.adapter("music").submit_music(request))

    def get_task(self, task_id: str, task_type: TaskType | str) -> TaskSnapshot:
        kind = TaskType(str(task_type.value if isinstance(task_type, TaskType) else task_type).lower())
        if task_id.startswith("file:"):
            path = Path(task_id[len("file:"):])
            if not path.is_file():
                return TaskSnapshot(task_id=task_id, task_type=kind, status=TaskState.FAILED, error_message=f"cached result missing: {path}")
            return TaskSnapshot(task_id=task_id, task_type=kind, status=TaskState.FINISHED, output_urls=(path_to_file_url(path),))
        if task_id.startswith("url:"):
            return TaskSnapshot(task_id=task_id, task_type=kind, status=TaskState.FINISHED, output_urls=(task_id[len("url:"):],))
        owner = adapter_for_task_id(task_id)
        if owner is not None:
            _prefix, adapter_name = owner
            role = _TASK_ROLE.get(kind, "image")
            adapter = self.adapter(role)
            if getattr(adapter, "name", "") != adapter_name:
                # 设置被改过：任务是旧适配器提的。当前角色配的是另一家，手里没有旧那家的凭据——
                # 拿现在的 key 去查只会得到一个莫名其妙的 401，不如直接说清楚下一步
                current = self.settings.role(role).adapter
                raise ProviderCommandError(
                    f"这个任务是 {adapter_name} 提交的，但「模型设置」里{_ROLE_LABEL.get(role, role)}来源现在是 {current or '未配置'}，"
                    f"没有 {adapter_name} 的凭据可查——把来源切回 {adapter_name} 再刷新，或者对这一镜点「重拍」用当前来源重新生成"
                )
            return adapter.get_task(task_id)
        shape = _PREFIX_SHAPE.match(task_id)
        if shape:
            # 前缀没人登记：提交它的来源由插件提供、插件这次没加载。ProviderUnavailable 让 runner 把任务留在
            # 「等远端」（不记失败、账本不动），装好插件重启后点「继续等待」接着查
            prefix = shape.group(1)
            raise ProviderUnavailable(
                f"任务 {task_id[:60]} 是一个没加载的来源提交的（前缀 {prefix}）{plugins.prefix_unavailable_reason(prefix)}，"
                "查不了进度——装好提供它的插件并重启后再刷新；任务留在账本里，不会被当成失败"
            )
        raise ProviderCommandError(f"unknown task id scheme: {task_id[:40]}")

    def download(self, url: str, destination: str | Path, *, max_bytes: int | None = None, timeout_seconds: float | None = None) -> Path:
        return download_url(url, destination, max_bytes=max_bytes or DEFAULT_MAX_DOWNLOAD_BYTES,
                            timeout_seconds=timeout_seconds or DEFAULT_DOWNLOAD_TIMEOUT_SECONDS)

    # ---- 文本 / 语音：不走 runner，直接给调用方
    def text(self):
        return self.adapter("text")

    def speech(self):
        return self.adapter("voice")


def build_provider(settings: settings_mod.Settings | None = None) -> RoutedProvider:
    return RoutedProvider(settings or settings_mod.load_settings())


def test_role(role: str, settings: settings_mod.Settings | None = None) -> str:
    """「测试连接」：装配该角色的适配器并 ping 一次，返回一句人话。"""

    provider = build_provider(settings)
    adapter = provider.adapter(role)
    ping = getattr(adapter, "ping", None)
    if not callable(ping):
        return f"{adapter.name} ready"
    return str(ping())


__all__ = ["RoutedProvider", "build_provider", "test_role"]
