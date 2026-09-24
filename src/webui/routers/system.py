"""运行环境自检：版本、ffmpeg、模型角色配置状态、数据目录。

给三个地方用：界面头部（版本号、ffmpeg 缺失横幅）、空状态引导（三步里哪一步
已经完成）、项目页生成按钮旁的「这个角色还没配」提示。全部只读，不碰网络，
不碰付费接口。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from typing import Any

from fastapi import APIRouter

from genkit import plugins
from genkit import settings as settings_mod
from genkit.version import __version__
from storyvideo.post_layers import font_status

from ..branding import product_payload
from ..deps import PROJECT_ROOT, STORYVIDEO_DIR

router = APIRouter(tags=["system"])

FFMPEG_DOWNLOAD_URL = "https://ffmpeg.org/download.html"


def roles_status() -> dict[str, dict[str, Any]]:
    """五个角色各自：选了哪个来源、用哪个模型、还缺什么字段。密钥不出现。

    ``unavailable``：选的来源现在不在表里（提供它的插件没装上）时的人话；``sources``：这一档此刻有几个可选来源
    （配乐目前是 0——界面据此说「要装插件」，而不是「去选一个来源」）。"""

    try:
        current = settings_mod.load_settings()
    except ValueError:
        # 设置文件坏了也不该让自检接口整个 500——按「全部未配置」报，设置页会给出真实错误
        current = settings_mod.Settings()
    presets = settings_mod.adapter_presets()
    out: dict[str, dict[str, Any]] = {}
    for role in settings_mod.ROLES:
        cfg = current.role(role)
        missing = settings_mod.validate_role(current, role)
        effective = settings_mod.effective(cfg, role) if cfg.adapter else cfg
        out[role] = {
            "adapter": cfg.adapter,
            "model": effective.model if cfg.adapter else "",
            "configured": not missing,
            "missing": missing,
            # 跑在本机吗（DESIGN §45）：本机来源不要 key、不花钱，界面据此改文案
            "local": settings_mod.role_is_local(current, role),
            "sources": len(presets.get(role) or {}),
            "unavailable": settings_mod.adapter_unavailable(current, role) or "",
        }
    return out


@router.get("/api/health")
def health() -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    return {
        "app": "storystudio",
        "product": product_payload(),   # 产品名唯一来源在 branding.py（DESIGN §38）；头部标题从这里读
        "version": __version__,
        "frozen": bool(getattr(sys, "_MEIPASS", None)),
        "platform": sys.platform,
        "python": ".".join(str(x) for x in sys.version_info[:3]),
        "project_root": str(PROJECT_ROOT),
        "episodes_dir": str(STORYVIDEO_DIR),
        "settings_path": str(settings_mod.settings_path()),
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "ffmpeg_ok": bool(ffmpeg and ffprobe),
        "ffmpeg_download_url": FFMPEG_DOWNLOAD_URL,
        "asr_available": importlib.util.find_spec("faster_whisper") is not None,
        "fonts": font_status(),
        "roles": roles_status(),
        # 插件（genkit.plugins）：每个的名字、版本、状态（loaded / failed / skipped）与原因
        "plugins": [record.public() for record in plugins.records()],
        "plugin_dir": str(plugins.default_dir()),
    }
