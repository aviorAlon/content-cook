"""安装包里的数据文件白名单：运行时数据（skills/ 与 examples/）和随包的许可声明（docs/ 下的声明文件）。

打包只带这里列出的文件，不整目录带：目录整个带，开发机上临时丢进去的草稿、导出、测试产物、复制来的真实项目
会跟着进包。加一个运行时要读的数据文件（新的卡库、新的示例项目）就在 ``RUNTIME_DATA`` 加一行；
随包数据里有取自第三方项目的内容、许可要求声明跟着副本走时，声明文件加进 ``NOTICE_FILES``。

三处共用这一份清单：
* ``desktop/storystudio.spec``：按它组 ``datas``（前端静态文件按 ``STATIC_TOP`` + ``app/``）；
* ``desktop/verify_bundle.py``：打包后核对 dist 里 skills/ examples/ docs/ 下恰好是这些文件，webui/static/ 下没有别的；
* ``tests/test_packaging_files.py``：核对每一项都在仓库里、都有代码在读（声明文件除外），仓库里 skills/ 与 examples/
  也没有清单外的文件。
"""
from __future__ import annotations

RUNTIME_DATA = (
    # 短剧：镜头语言库 / 风格骨架 / 题材卡 / 题材 × 镜头搭配 / 形态卡 / 剧本工艺清单
    "skills/ai-short-film/scripts/camera_library.json",
    "skills/ai-short-film/scripts/style_library.json",
    "skills/ai-short-film/scripts/genre_cards.json",
    "skills/ai-short-film/scripts/genre_camera_pairing.json",
    "skills/ai-short-film/scripts/form_cards.json",
    "skills/ai-short-film/scripts/craft_checklist.json",
    # 出图风格卡、配乐预设、播客真人感清单
    "skills/ai-image/scripts/style_cards.json",
    "skills/ai-music/scripts/music_briefs.json",
    "skills/ai-podcast/scripts/humanlike_checklist.json",
    # 本地 ComfyUI 内置工作流（每份配一个 .map.json）
    "skills/local-models/workflows/txt2img_sdxl.json",
    "skills/local-models/workflows/txt2img_sdxl.map.json",
    "skills/local-models/workflows/img2img_sdxl.json",
    "skills/local-models/workflows/img2img_sdxl.map.json",
    "skills/local-models/workflows/txt2video_wan.json",
    "skills/local-models/workflows/txt2video_wan.map.json",
    # 各模块「复制示例」用的示例项目
    "examples/episodes/first_episode.json",
    "examples/picturebook/fox_three_socks.json",
    "examples/sticker/xiaoman_emoji.json",
    "examples/portrait/baby_100_demo.json",
    "examples/image/lantern_night.json",
    "examples/clip/city_morning.json",
    "examples/music/lullaby_demo.json",
    "examples/podcast/short_video_demo.json",
    "examples/script/after_hours_message.json",
)

# 随包的许可与第三方声明（包内路径跟仓库相同）：题材卡等随包数据取自 MIT 项目，许可要求版权与许可声明跟着副本走。
# 程序不读它们，只是给拿到安装包的人看；docs/ 下别的文档不进包
NOTICE_FILES = (
    "LICENSE",
    "docs/third-party-notices.md",
)

# 包里全部白名单文件；下面这几个目录在包里只能有这些文件
BUNDLED_FILES = RUNTIME_DATA + NOTICE_FILES
WHITELISTED_DIRS = ("skills", "examples", "docs")

# 前端静态目录 src/webui/static：app/ 是 vite 的构建产物（每次清空重建，按目录带），
# 顶层只带这几个文件（有才带）；目录里别的东西（截图、草稿、旧产物）不进包
STATIC_SOURCE = "src/webui/static"
STATIC_TOP = ("index.html", "settings.js")


def datas(root) -> list[tuple[str, str]]:
    """PyInstaller ``datas`` 条目：(源文件绝对路径, 包内目录)。"""

    from pathlib import Path

    base = Path(root)
    return [(str(base / rel), rel.rsplit("/", 1)[0]) for rel in RUNTIME_DATA]


def notice_datas(root) -> list[tuple[str, str]]:
    """许可声明的 ``datas`` 条目：(源文件绝对路径, 包内目录)，包内路径跟仓库相同。"""

    from pathlib import Path

    base = Path(root)
    # 仓库根目录的文件（LICENSE）进包根目录，其余照原路径
    return [(str(base / rel), rel.rsplit("/", 1)[0] if "/" in rel else ".") for rel in NOTICE_FILES]


def static_datas(root) -> list[tuple[str, str]]:
    """前端静态文件的 ``datas`` 条目：app/ 整个目录 + 顶层白名单里存在的文件。"""

    from pathlib import Path

    static = Path(root) / STATIC_SOURCE
    entries = [(str(static / "app"), "webui/static/app")]
    entries += [(str(static / name), "webui/static") for name in STATIC_TOP if (static / name).is_file()]
    return entries
