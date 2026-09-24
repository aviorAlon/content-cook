# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包：python -m PyInstaller desktop/storystudio.spec

产物 dist/StoryStudio/（单文件夹，启动快、便于附带 ffmpeg）。代码资源（镜头库/风格库/静态页）随包；
用户数据走 STORYSTUDIO_PROJECT_ROOT（启动器默认当前目录），设置在 ~/.storystudio。
ffmpeg/ffprobe 不打进包：用户机器 PATH 里要有（README 有说明），或把可执行文件放进 dist/StoryStudio/ 旁边。

包里只放运行要用的东西：skills/ 与 examples/ 按 desktop/bundle_manifest.py 的白名单逐个文件带（不整目录带），
随包数据要求附带的许可声明（docs/third-party-notices.md）也按同一份清单逐个带，
前端静态目录只带构建产物 app/ 与顶层白名单文件，
测试框架与测试模块一律排除；打完包跑 desktop/verify_bundle.py 核对（build_windows.ps1 / build_macos.sh / CI 都会跑）。
"""
import sys as _sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
# 应用图标（desktop/make_icon.py 画的）：exe / .app 嵌一份，随包再带一份给 /favicon.ico 路由（浏览器模式的标签页）
ICON_ICO = str(ROOT / "desktop" / "storystudio.ico")
ICON_ICNS = str(ROOT / "desktop" / "storystudio.icns")

# 产品名唯一来源在 src/webui/branding.py（DESIGN §38）：.app 的显示名从那里读，读不到才退回同一个字面量
_sys.path.insert(0, str(ROOT / "src"))
try:
    from webui.branding import PRODUCT_NAME as _product_name
except Exception:  # noqa: BLE001
    _product_name = "Content Cook · 内容创作工作台"

# 运行时数据白名单（skills/ 与 examples/ 下的文件、许可声明、前端静态文件）的唯一来源；verify_bundle.py 与测试也读它
_sys.path.insert(0, str(ROOT / "desktop"))
from bundle_manifest import datas as _runtime_datas, static_datas as _static_datas  # noqa: E402
from bundle_manifest import notice_datas as _notice_datas  # noqa: E402

datas = [(ICON_ICO, "desktop")]
datas += _static_datas(ROOT)       # 前端构建产物 app/（vite 每次清空重建）+ 顶层白名单文件，不整个 static/ 目录带
datas += _runtime_datas(ROOT)
datas += _notice_datas(ROOT)       # 第三方许可声明：随包数据取自 MIT 项目，声明要跟着副本走
datas += collect_data_files("openrouter")
datas += collect_data_files("volcenginesdkarkruntime")
datas += collect_data_files("edge_tts")

# 不进包的模块：测试框架、测试客户端、测试代码本身，以及只在开发机上跑的回溯审计脚本
_NO_SHIP = [
    "tests", "conftest", "pytest", "_pytest", "pluggy", "iniconfig", "anyio.pytest_plugin",
    "fastapi.testclient", "starlette.testclient", "storyvideo.audit_camera",
]


def _ships(name):
    return not any(name == x or name.startswith(x + ".") for x in _NO_SHIP)


hiddenimports = (
    collect_submodules("webui") + collect_submodules("storyvideo") + collect_submodules("genkit") + collect_submodules("picturebook") + collect_submodules("imageset") + collect_submodules("imagecraft") + collect_submodules("clip") + collect_submodules("music") + collect_submodules("podcast") + collect_submodules("script")
    + collect_submodules("openpyxl")
    + collect_submodules("uvicorn") + collect_submodules("fastapi") + ["webview", "edge_tts", "openai", "openrouter", "volcenginesdkarkruntime"]
)
hiddenimports = [m for m in hiddenimports if _ships(m)]

a = Analysis(
    [str(ROOT / "src" / "studio" / "desktop.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "faster_whisper", "torch", "ctranslate2", "onnxruntime", "onnx", "numpy",
        "sqlalchemy", "IPython", "jedi", "matplotlib", "pandas", "scipy", "agents", "PyQt5", "PySide6", "wx",
        *_NO_SHIP,
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StoryStudio",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON_ICO,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="StoryStudio")

# macOS：再包一层 .app（Finder 里双击、Dock、签名/公证都认它）。用户数据目录默认 ~/StoryStudio（启动器里定的）。
if _sys.platform == "darwin":
    from PyInstaller.building.osx import BUNDLE

    _version = "0.1.0"
    try:
        import re as _re
        _version = _re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "src" / "genkit" / "version.py").read_text(encoding="utf-8")).group(1)
    except Exception:  # noqa: BLE001
        pass
    app = BUNDLE(
        coll,
        name="StoryStudio.app",
        icon=ICON_ICNS,
        bundle_identifier="ai.avioralon.desktop",
        info_plist={
            "CFBundleName": "StoryStudio",
            "CFBundleDisplayName": _product_name,
            "CFBundleShortVersionString": _version,
            "CFBundleVersion": _version,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
