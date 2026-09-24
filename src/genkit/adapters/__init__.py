"""适配器注册表：role → adapter 名 → 构造函数。settings.ADAPTERS 是 UI 用的表单描述，这里是代码入口，两边的 key 必须一致。
仓库外的来源经 genkit.plugins 同时往这两张表（以及下面的异步任务前缀表）里登记。"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from . import (ark_images, ark_video, comfyui, minimax_speech, minimax_video, openai_compat, openai_video,
               openrouter_adapters, sdwebui, speech)

Builder = Callable[..., Any]

REGISTRY: dict[str, dict[str, Builder]] = {
    "text": {
        "openai_chat": openai_compat.build_chat,
    },
    "image": {
        "openrouter_images": openrouter_adapters.build_images,
        "openai_images": openai_compat.build_images,
        "ark_images": ark_images.build,
        "sdwebui": sdwebui.build,
        "comfyui": comfyui.build,
    },
    "voice": {
        "openrouter_tts": openrouter_adapters.build_tts,
        "edge_tts": speech.build_edge,
        "openai_speech": openai_compat.build_speech,
        "minimax_speech": minimax_speech.build,
    },
    "video": {
        "openrouter_video": openrouter_adapters.build_video,
        "ark_video": ark_video.build,
        "minimax_video": minimax_video.build,
        "openai_video": openai_video.build,
        "comfyui": comfyui.build,
    },
    # 配乐没有内置来源（没有公开统一的音乐接口形状）：由插件登记
    "music": {},
}

# 异步任务 id 的前缀 → 负责查进度的来源。同步型来源的任务 id 是 file: / url:，不在这里
ASYNC_PREFIXES: dict[str, str] = {
    "ark:": "ark_video", "minimax:": "minimax_video", "openrouter:": "openrouter_video", "oaivid:": "openai_video",
    "comfy:": "comfyui",
}


def adapter_for_task_id(task_id: str) -> tuple[str, str] | None:
    """任务 id 属于哪个来源：``(前缀, 来源 id)``；没有登记过的前缀返回 ``None``。"""

    from .. import plugins

    plugins.ensure_loaded()
    text = str(task_id or "")
    for prefix, adapter_name in ASYNC_PREFIXES.items():
        if text.startswith(prefix):
            return prefix, adapter_name
    return None


def build_adapter(role: str, name: str, config: Mapping[str, Any], **kw: Any) -> Any:
    from .. import plugins

    plugins.ensure_loaded()
    try:
        builder = REGISTRY[role][name]
    except KeyError as exc:
        raise KeyError(f"no adapter {name!r} for role {role!r}") from exc
    return builder(config, **kw)


__all__ = ["REGISTRY", "ASYNC_PREFIXES", "adapter_for_task_id", "build_adapter"]
