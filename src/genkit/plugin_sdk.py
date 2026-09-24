"""插件接口 v1 的导出面（实验性）：插件实现来源时只从这里 import，不直接碰核心内部模块。

    from genkit.plugin_sdk import Adapter, ImageRequest, TaskSubmission, TaskState, TaskType, ProviderSetupRequired

v1 是实验性的：这里列出的名字在 1.0 之前可能调整，改动写进 CHANGELOG；``API_VERSION`` 变了之后，写着旧版本号的插件
会被拒绝加载并说明原因（见 docs/plugins.md）。不在这里的核心模块随时可能改。
写来源时的两条约定：

* 适配器实例的 ``name`` 必须等于 ``api.add_adapter`` 登记的 id——路由靠它判断「这个任务是谁提交的」，账本也按它记来源；
* 每个方法只做一次远端操作；轮询、重试、持久化都在 runner。异步任务 id 用登记过的前缀开头（``<前缀><远端 id>``）。

本机缺少来源需要的程序（命令行、运行时、登录）时抛 :class:`ProviderSetupRequired`：一次请求都没发出去，runner 记成
明确失败、可以重提，界面归到「模型没配置」那一档。
"""

from __future__ import annotations

from .adapters.base import Adapter, HttpResponse, default_http, local_token, raise_for_status, to_data_url, token_path
from .capabilities import lookup, resolution_for
from .plugins import API_VERSION, PluginAPI, PluginError
from .providers import (
    AssetReference,
    ImageRequest,
    MusicRequest,
    ProviderCommandError,
    ProviderError,
    ProviderNotConfigured,
    ProviderProtocolError,
    ProviderRateLimited,
    ProviderRejected,
    ProviderSetupRequired,
    ProviderTimeout,
    ProviderUnavailable,
    ProviderUnsupported,
    QuotaExhausted,
    RemoteIdentityMatcher,
    RemoteTask,
    TaskHistoryProvider,
    TaskSnapshot,
    TaskState,
    TaskSubmission,
    TaskType,
    VideoRequest,
)

__all__ = [
    "API_VERSION", "PluginAPI", "PluginError",
    "Adapter", "HttpResponse", "default_http", "raise_for_status", "local_token", "token_path", "to_data_url",
    "ImageRequest", "VideoRequest", "MusicRequest", "AssetReference", "TaskSubmission", "TaskSnapshot", "TaskState", "TaskType",
    "RemoteTask", "TaskHistoryProvider", "RemoteIdentityMatcher",
    "ProviderError", "ProviderUnavailable", "ProviderSetupRequired", "ProviderCommandError", "ProviderRejected",
    "ProviderNotConfigured", "ProviderTimeout", "ProviderProtocolError", "ProviderUnsupported", "QuotaExhausted",
    "ProviderRateLimited",
    "lookup", "resolution_for",
]
