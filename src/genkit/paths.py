"""两种根目录：代码资源根（镜头库/风格库/静态页，随程序打包）与用户数据根（剧集 spec、output、.env）。

开发态两者都是仓库根；打包成桌面客户端后代码资源在 PyInstaller 的临时目录，用户数据在
``STORYSTUDIO_PROJECT_ROOT``（桌面启动器默认设为启动时的工作目录）。
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path


def code_root() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parents[2]


def data_root() -> Path:
    explicit = os.environ.get("STORYSTUDIO_PROJECT_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return code_root()


def resource(*parts: str) -> Path:
    return code_root().joinpath(*parts)


# ---------------------------------------------------------------------------- 打包版的外部 site 目录
#
# 桌面版（PyInstaller）不随包带 faster-whisper 一族（ctranslate2 / numpy / onnxruntime，几百 MB），而冻结解释器以 isolated
# config 启动、PYTHONPATH 一律忽略、exe 里也没有 pip——所以「装到系统 Python 再加 PYTHONPATH」这条路走不通。可行的做法：
# 用本机同一 major.minor 的 Python 执行 pip install --target <这个目录>，启动器起服务前把它接进 sys.path
# （追加在末尾：包内的 pydantic / fastapi 仍优先，只补上包里没有的）。目录默认 ~/.storystudio/site-packages，
# 环境变量 STORYSTUDIO_EXTRA_SITE 可改。

# 编译扩展文件名里的 CPython 版本：Windows 是 _ext.cp312-win_amd64.pyd，macOS / Linux 是 _ext.cpython-312-darwin.so /
# …cpython-312-x86_64-linux-gnu.so（spec 也打 darwin 的 .app）；abi3 的稳定 ABI 扩展不带版本，不拦
_ABI_TAG = re.compile(r"\.cp(?:ython-)?(\d)(\d+)-")


def extra_site_dir() -> Path:
    explicit = os.environ.get("STORYSTUDIO_EXTRA_SITE")
    if explicit:
        return Path(explicit).expanduser()
    from .settings import home_dir   # settings 不反向依赖 paths，放函数里只是避免 import 时就拉起它

    return home_dir() / "site-packages"


def extra_site_python_mismatch(folder: Path) -> str | None:
    """目录里的编译扩展（.cp3XY-…pyd / .cpython-3XY-….so）是给哪个 Python 装的；与当前解释器不一致时返回那个版本号（如 "3.12"），
    一致或目录里没有编译扩展返回 None。装错版本的扩展在 import 时直接崩（DLL 加载失败），不如提前说清楚。"""

    want = (sys.version_info[0], sys.version_info[1])
    for ext in ("*/*.pyd", "*/*.so", "*/*/*.pyd", "*/*/*.so"):
        for path in folder.glob(ext):
            m = _ABI_TAG.search(path.name)
            if m and (int(m.group(1)), int(m.group(2))) != want:
                return f"{m.group(1)}.{m.group(2)}"
    return None


def activate_extra_site() -> Path | None:
    """把外部 site 目录接进 sys.path（存在且版本对得上才接）。返回接进去的目录，没接返回 None。"""

    folder = extra_site_dir()
    if not folder.is_dir() or extra_site_python_mismatch(folder):
        return None
    text = str(folder)
    if text not in sys.path:
        try:
            import site

            site.addsitedir(text)      # 追加在 sys.path 末尾，顺带处理 .pth
        except Exception:  # noqa: BLE001 - 冻结环境里 site 模块缺了就退回裸追加
            sys.path.append(text)
    importlib.invalidate_caches()    # 装完当场就能 find_spec 到，不用重启
    return folder


__all__ = ["code_root", "data_root", "resource", "extra_site_dir", "extra_site_python_mismatch", "activate_extra_site"]
