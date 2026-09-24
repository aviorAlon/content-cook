# 安装与部署

[English](../en/installation.md) | 中文

本文讲怎样安装和运行 Content Cook（内容创作工作台），怎样搭开发环境、自己打 Windows 安装包，
以及部署、备份、升级、卸载和排障时要注意什么。模型怎么接，见[配置指南](configuration.md)。

> **关于名字**：产品名是「Content Cook · 内容创作工作台」，唯一来源是 `src/webui/branding.py`
> （「Content Cook」目前是工作名）。Python 包名 `storystudio`、命令 `storystudio` / `storyvideo`、
> 设置目录 `~/.storystudio`、桌面程序 `StoryStudio.exe` 都是技术标识，不跟着产品名改。
> 下文说到 StoryStudio，指的就是这个程序。

## 目录

1. [选择安装方式](#1-选择安装方式)
2. [环境要求](#2-环境要求)
3. [方式 A 使用 Windows 安装包](#3-方式-a-使用-windows-安装包)
4. [方式 B 从源码运行](#4-方式-b-从源码运行)
5. [方式 C 开发环境](#5-方式-c-开发环境)
6. [构建 Windows 安装包](#6-构建-windows-安装包)
7. [macOS 打包](#7-macos-打包)
8. [部署说明](#8-部署说明)
9. [配置模型](#9-配置模型)
10. [安装插件](#10-安装插件)
11. [升级](#11-升级)
12. [卸载](#12-卸载)
13. [常见问题排查](#13-常见问题排查)
14. [验证状态](#14-验证状态)

## 1. 选择安装方式

| 方式 | 适合谁 | 平台 | 需要准备 |
|---|---|---|---|
| A. Windows 安装包 | 只想用，不想碰命令行 | Windows x64 | 安装包（Releases 上有就下载，没有就按 §6 自己打）、ffmpeg |
| B. 从源码运行 | 想用最新代码、要用命令行 `storyvideo`，或在 macOS / Linux 上用 | Windows / macOS / Linux（后两者验证有限，见 §2） | Git、Python 3.10+、ffmpeg |
| C. 开发环境 | 要改代码或界面、跑测试 | 同 B | 同 B；改界面时另需 Node.js 22 |

三种方式跑的是同一个程序：在本机起一个只监听 `127.0.0.1` 的服务，再用原生窗口（或浏览器）打开界面。
它是单用户的本机应用，没有账号系统，不能放到服务器上给多人用（原因见 §8.1）。

## 2. 环境要求

| 项目 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows x64 | 目前只有 Windows 上实际打过包、跑过。Linux：CI 里配置了 Ubuntu 上的测试，但截至 v0.1.0 CI 还没在 GitHub 上跑过，原生窗口没测过，也没有 Linux 安装包。macOS：打包脚本写好了但没在 Mac 上跑过，源码运行也没在 Mac 上测过。 |
| Python | 3.10 及以上 | `pyproject.toml` 声明 `>=3.10`，实际只在本地开发环境的 3.11 上测过（CI 配置的也是 3.11，但还没在 GitHub 上跑过），推荐 3.11。方式 B、C 和自己打包时需要；安装包自带 Python。 |
| ffmpeg / ffprobe | 在 PATH 里能找到 | 装配成片、后期合成、绘本和播客的渲染、台词识别都要用。短剧模块的每张首帧、每条视频下载后也要先用 ffprobe 校验（审片用的联络表要用 ffmpeg），所以用短剧出首帧、出视频之前就要装好。图片工作台、单条视频等模块出图出片不依赖它。缺了界面顶部会提示（v0.1.0 的提示文字说「首帧和视频生成不受影响」，这对短剧模块不准确）。 |
| 中文字体 | 系统里有一款 CJK 字体 | 烧进画面的字幕、插卡和手写体文字要用。Windows、macOS 自带的一般就够；Linux 见 §4.2。 |
| Node.js | 22（只在改界面时需要） | 构建好的界面已经提交在 `src/webui/static/app/`，只运行程序不需要 Node。 |
| faster-whisper | 可选 | 台词识别闸要用。第一次识别时从 Hugging Face 下载模型（界面提示默认模型约 500MB）。 |
| 网络 | 用云端模型时需要 | 发出去什么数据、发给谁，见[配置指南](configuration.md)。朗读、旁白没另配音色时默认用 Edge TTS（微软在线服务），也要联网。 |
| 显卡 | 只有本机模型需要 | 硬件要求由 Ollama、ComfyUI 等本机模型自己决定；本机模型这条路还没在真实硬件上验证过。 |

## 3. 方式 A 使用 Windows 安装包

### 3.1 拿到安装包

- 如果仓库的 [Releases 页面](https://github.com/aviorAlon/content-cook/releases) 发布了
  `StoryStudio-<版本>-setup.exe`，下载即可。
- 仓库目前没有自动发布 Release 的流程。CI 只在推送 `v*` 标签或手动触发时，把 Windows 包作为那次运行的
  构建产物（artifact，名为 `StoryStudio-windows`）上传。下载 Actions 构建产物需要登录 GitHub，而且产物过了保留期
  （默认 90 天）就会被删除。Releases 上没有的话，按 [§6](#6-构建-windows-安装包) 自己打。
- 安装包和 exe **都没有代码签名**。Windows SmartScreen 可能提示「Windows 已保护你的电脑」，杀毒软件也可能拦截。
  只运行从本仓库 Releases 页面下载或自己构建的包；确认来源后，可以点「更多信息 → 仍要运行」。

### 3.2 安装

安装向导（Inno Setup 6）会：

- 提供简体中文和英文两种安装语言；
- 让你选为所有用户安装（需要管理员权限）还是只为当前用户安装；
- 默认装到 `Program Files\StoryStudio`；只为当前用户安装时，装在当前用户的程序目录下
  （通常是 `%LOCALAPPDATA%\Programs\StoryStudio`）；
- 在开始菜单建快捷方式和卸载入口，桌面快捷方式可选；
- 装完可以勾选直接启动。

程序目录里只有程序本身，项目、设置和密钥都不放在这里（见 §3.4）。

**ffmpeg**：安装包默认不带 ffmpeg（打包的人可以选择带上，见 §6.4）。二选一：

- 把 `ffmpeg.exe` 和 `ffprobe.exe` 放进安装目录，或它下面的 `ffmpeg\` 子目录（也认 `ffmpeg\bin\`）。
  启动器会把这几个位置加到 PATH 最前面；
- 或者把 ffmpeg 装进系统 PATH。

放好后重启程序。界面顶部的提示里有下载链接（<https://ffmpeg.org/download.html>）。

### 3.3 第一次启动

从开始菜单或桌面打开「Content Cook · 内容创作工作台」，程序会：

1. 在 `127.0.0.1` 上找一个空闲端口起本机服务：先试 8756，再依次试 8757–8799；
2. 生成一个只在本次启动有效的随机访问令牌；
3. 打开一个原生窗口（1440×920，Windows 上用的是 WebView2）。窗口自动带着令牌，你不用管它。

打包版没有控制台窗口，出问题先看日志：`%USERPROFILE%\.storystudio\logs\storystudio.log`。

第一次打开时，首页会显示「第一次用」三步：**配好模型 → 选一个模块开始 → 花钱前都会先问你**。
进入短剧模块、还没有项目时，另有「三步出第一条首帧」：配好模型 → 拿到一个项目 → 出首帧。

1. 左栏「模型设置」：给要用的角色选来源，填地址和密钥，保存后点「测试连接」。详见[配置指南](configuration.md)。
2. 拿示例练手：短剧首页的「复制示例剧集（不花钱）」或「新建项目」页的「从示例开始」，会把随程序附带的示例复制成你的项目，
   不花钱。其他多数模块的首页也有「复制示例（不花钱）」按钮。
3. 按首页引导的说法，每次生成前都会先告诉你这一批要发几次请求（本机来源会写明不花钱），确认了才发。

### 3.4 数据放在哪

| 位置 | 内容 |
|---|---|
| `~/StoryStudio`（即 `%USERPROFILE%\StoryStudio`） | **数据目录**：各模块的项目文件、上传的素材、生成的产物、短剧账本、回收站。第一次启动时自动建好 `content/`、`content/story_video/`、`output/`。 |
| `~/.storystudio` | 模型设置与密钥（`settings.json`）、日志、缓存、音色库、模型目录缓存、插件、可选组件（`site-packages/`）。 |

换数据目录有两种办法，优先级从高到低：

1. 启动参数 `--project-root`，例如在快捷方式的「目标」末尾加上 `--project-root "D:\StoryStudioData"`；
2. 用户环境变量 `STORYSTUDIO_PROJECT_ROOT`。

都没设时用默认的 `~/StoryStudio`。设置目录可以用环境变量 `STORYSTUDIO_HOME` 换；设置文件 `settings.json` 本身
还可以用 `STORYSTUDIO_SETTINGS` 单独指到别处。完整的目录结构和备份建议见 §8.5。

### 3.5 可选：台词识别（faster-whisper）

为了控制体积，安装包没带 faster-whisper（连同 ctranslate2、numpy、onnxruntime 等一起排除了）。
在「模型设置」页点「一键安装 faster-whisper」，程序会：

1. 在本机找一个**跟打包版同一个 Python 小版本**、带 pip 的 Python。Windows 上依次试 `py -3.X`、`python3.X`、
   `python3`、`python`；
2. 执行 `pip install --target ~/.storystudio/site-packages faster-whisper`；
3. 装完当场可用，以后每次启动由启动器自动接上。

要注意：

- 打包版内嵌的 Python 版本就是构建时用的版本（CI 用的是 3.11），所以本机得装一个同版本、带 pip 的 Python。
  设置页给出的手动命令写的就是找到的那个 Python。
- 用别的 Python 版本装进来的包会被识别出来，设置页会说明原因。
- 目录可以用环境变量 `STORYSTUDIO_EXTRA_SITE` 换。
- 第一次识别时从 Hugging Face 下载模型（`tiny` / `base` / `small` / `medium` / `large-v3`，默认 `small`，
  在 CPU 上以 int8 运行），缓存在 Hugging Face 的缓存目录：默认 `~/.cache/huggingface/hub`，
  或 `HF_HUB_CACHE` / `HF_HOME` 指定的位置。

## 4. 方式 B 从源码运行

### 4.1 克隆并安装

```bash
git clone https://github.com/aviorAlon/content-cook.git
cd content-cook
python -m venv .venv
```

激活虚拟环境：

```powershell
.venv\Scripts\Activate.ps1      # Windows PowerShell
```

```bat
REM Windows cmd
.venv\Scripts\activate.bat
```

```bash
source .venv/bin/activate       # macOS / Linux
```

- 下面的命令都假定虚拟环境已经激活。不想激活的话，就把 `python` / `pip` 换成 `.venv\Scripts\python -m pip`
  （macOS / Linux：`.venv/bin/python -m pip`），否则会装进系统的 Python。
- PowerShell 提示「禁止运行脚本」、不让运行 `Activate.ps1` 时，可以改在 cmd 里运行 `.venv\Scripts\activate.bat`。

安装：

```bash
python -m pip install --upgrade pip
pip install -e ".[desktop]"     # 程序本体 + pywebview（原生窗口）+ PyInstaller（打包）
pip install -e ".[asr]"         # 可选：台词识别用的 faster-whisper
```

要点：

- **只支持在克隆目录里做可编辑安装（`pip install -e`）。** 程序运行时要读仓库里的 `skills/`（镜头库、风格库、
  风格卡、审稿清单、ComfyUI 工作流）和 `examples/`（示例项目），这些不在 wheel / sdist 里，
  所以 `pip install .` 或装 wheel 都不受支持。项目也没有发布到 PyPI。
- 依赖都写在 `pyproject.toml` 里。运行依赖：requests、fastapi、uvicorn、pydantic、openai、openrouter、
  volcengine-python-sdk、edge-tts、Pillow、openpyxl。可选组：`desktop`（pywebview、PyInstaller）、
  `asr`（faster-whisper）、`dev`（pytest）。
- Linux 上原生窗口（pywebview）没有测过。可以只装 `pip install -e .`，启动时加 `--browser` 用系统浏览器。

### 4.2 安装 ffmpeg 和中文字体

| 系统 | ffmpeg | 中文字体 |
|---|---|---|
| Windows | 从 <https://ffmpeg.org/download.html> 下载，把 `bin` 目录加进 PATH；或 `choco install ffmpeg`（CI 用的就是它） | 系统自带的微软雅黑 / 黑体 / 宋体即可 |
| macOS | `brew install ffmpeg`（需要 Homebrew） | 系统自带的苹方 / 华文黑体即可 |
| Debian / Ubuntu | `sudo apt-get install ffmpeg` | `sudo apt-get install fonts-noto-cjk`（CI 用的就是它），或文泉驿微米黑 |

装完确认一下：

```bash
ffmpeg -version
ffprobe -version
```

- 程序通过 PATH 找这两个命令；改了 PATH 要重启程序。
- 字体按用途（界面文字、字幕、手写体）在一组固定路径里找第一个存在的，例如 Windows 的
  `C:/Windows/Fonts/msyh.ttc`、macOS 的 `/System/Library/Fonts/PingFang.ttc`、Debian / Ubuntu 的
  `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`。其他 Linux 发行版字体放在别处时可能找不到。
- 找不到时，健康检查 `/api/health` 的 `fonts.ok` 为 `false`，后期面板会提前提示。也可以在项目里直接指定字体文件：
  短剧用 `post_graphics.fonts`，绘本用字幕设置里的 `font_path`。

### 4.3 启动

```bash
storystudio --project-root ~/StoryStudio
```

- 默认打开原生窗口；没装 pywebview 时会提示一句，然后改用系统浏览器。
- **从源码运行时，数据目录默认是启动时所在的当前目录**（打包版才默认 `~/StoryStudio`）。
  在仓库根目录直接跑 `storystudio`，项目和产物就写进仓库（`content/`、`output/` 等已在 `.gitignore` 里）。
  建议用 `--project-root` 固定一个目录。
- `storystudio` 命令装在虚拟环境的 `Scripts` / `bin` 目录里。没激活虚拟环境时，可以用该环境的
  `python -m studio.desktop`（参数相同）。

启动成功后终端会打印这样几行（令牌每次都不一样）：

```text
Content Cook · 内容创作工作台 v0.1.0: http://127.0.0.1:8756/?token=<随机令牌>
  项目目录: <数据目录>
  日志: <设置目录>/logs/storystudio.log
```

用原生窗口时，关掉窗口程序就退出；用 `--browser` 或 `--no-open` 时，服务一直运行，在终端按 Ctrl+C 结束。

### 4.4 启动参数

| 参数 | 作用 |
|---|---|
| `--version` | 打印版本（例如 `storystudio 0.1.0`）后退出 |
| `--port PORT` | 指定端口。不指定时自动选 8756–8799 里第一个空闲的。指定的端口被占用时直接报错（说明是谁占着、可以换哪个端口），退出码 2 |
| `--browser` | 用系统默认浏览器打开，不开原生窗口 |
| `--no-open` | 只起服务，不开任何窗口（终端打印的地址里带令牌） |
| `--no-token` | 关掉本机访问令牌。**只在调试接口时用**，见 §8.2 |
| `--project-root DIR` | 数据目录。优先级：本参数 > `STORYSTUDIO_PROJECT_ROOT` > 打包版的 `~/StoryStudio` / 源码运行时的当前目录 |
| `--no-plugins` | 不加载任何插件（等于设 `STORYSTUDIO_PLUGINS=off`），排查问题时用 |

除了 `-h` / `--help`，没有别的参数。服务没起来时（端口上是别的程序在应答、服务线程出错，或 30 秒内没就绪），会打印原因并以退出码 1 结束。
启动器的提示信息目前只有中文。

### 4.5 令牌、浏览器和 curl

- 启动器用 `http://127.0.0.1:<端口>/?token=<令牌>` 打开界面。这一步会种下 cookie `storystudio_token_<端口>`
  （HttpOnly、SameSite=Strict），之后界面发的请求都靠它放行。
- 所有 `/api/*` 请求都要带这个 cookie，或者请求头 `X-StoryStudio-Token: <令牌>`，否则返回 401。
- 令牌每次启动都重新生成，重启后要用新打印的地址打开。
- 用 curl 调接口（Windows PowerShell 5.1 里 `curl` 是 `Invoke-WebRequest` 的别名，要写 `curl.exe`）：

```bash
curl -H "X-StoryStudio-Token: <令牌>" http://127.0.0.1:8756/api/health
```

- Host 不是 `127.0.0.1` / `localhost` / `::1` 的请求，以及浏览器标明来自别的网站的 `/api` 请求，一律返回 403
  （防 DNS 重绑定和跨站请求）。所以用局域网 IP 或别的主机名访问是打不开的——这是有意为之。

### 4.6 命令行 storyvideo

短剧模块的整套生产步骤也能用命令行完成。命令行只在源码安装时有，安装包里只有图形界面。例如：

```bash
storyvideo preview DEMO-1 --project-root "$HOME/StoryStudio"        # 只编译提示词、估时长，不锁定、不花钱
storyvideo status DEMO-1 --project-root "$HOME/StoryStudio" --json  # 只读状态
```

- `storyvideo` 不会自己展开 `~`（启动器 `storystudio` 会）。Windows PowerShell 5.1 和 cmd 会把 `~` 原样传进去，
  结果指向当前目录下一个叫 `~` 的文件夹。所以这里写 `"$HOME/StoryStudio"`（bash / zsh / PowerShell 都认）；
  cmd 里用 `%USERPROFILE%\StoryStudio`。
- 剧集号 `DEMO-1` 对应 `<数据目录>/content/story_video/DEMO-1.json`。界面里「从示例开始」复制出的第一个项目就是 `DEMO-1`。
- 命令行默认的数据目录是 `STORYSTUDIO_PROJECT_ROOT`，没设时是**代码根目录（也就是仓库根）**，跟启动器的默认值不同。
  界面用的是别的目录时，每条命令都加上同一个 `--project-root`。
- `run` 是唯一会提交付费生成任务的命令；`post` 合成旁白时会调用语音来源（选的是付费语音来源就会计费，Edge TTS 要联网）。
  退出码：0 成功；2 输入或运行错误；3 被安全闸拦下；4 停在还没通过的审片闸；130 被中断。
- 全部子命令和流程见 [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md)。

## 5. 方式 C 开发环境

### 5.1 安装

```bash
git clone https://github.com/aviorAlon/content-cook.git
cd content-cook
python -m venv .venv            # 激活方法见 §4.1
pip install -e ".[desktop,dev]"   # CI 装的就是这一组
```

改界面还需要 Node.js。CI 用 Node 22；当前锁定的开发依赖对 Node 版本有要求（Vite 8：`^20.19.0 || >=22.12.0`；
Vitest 5：`^22.12.0 || ^24.0.0 || >=26.0.0`；jsdom 30：`^22.22.2 || ^24.15.0 || >=26.0.0`），
所以建议用较新的 Node 22 LTS（22.22.2 或更高）。

### 5.2 后端加前端热更新

终端一，在仓库根目录：

```bash
storystudio --no-open --no-token --port 8765
```

终端二：

```bash
cd frontend
npm ci
npm run dev
```

然后打开 <http://localhost:5173/app/>。vite 的 `base` 是 `/app/`，`/api` 请求由 vite 反代到 `http://127.0.0.1:8765`。

- 后端必须用端口 8765 和 `--no-token`：反代地址写死在 `frontend/vite.config.js` 里；开着令牌时，后端要求请求的
  来源（Origin）跟后端自己的地址逐字相同，vite 页面（`http://localhost:5173`）发出的写请求会被 403。
  关掉令牌时，后端接受任何回环地址来源的请求，所以 vite 页面能调通。这个组合只该在开发时用（见 §8.2）。
- 在仓库根目录起后端时，数据目录就是仓库根；想分开就加 `--project-root`。
- 开发态可以打开 FastAPI 自带的接口文档 <http://127.0.0.1:8765/docs>（不加 `--no-token` 启动时，它和 `/api` 一样要令牌；
  打包版不提供）。

### 5.3 测试

```bash
pytest -q                                   # 后端测试（testpaths = tests，pythonpath = src）
python scripts/refresh_prompt_canary.py     # 提示词金丝雀
```

```bash
cd frontend
npm test                                    # Vitest（vitest run）
npm run build                               # 构建到 src/webui/static/app/
```

- 后端测试约 3200 条（v0.1.0 收集到 3214 条）。`tests/conftest.py` 会关掉插件加载（`STORYSTUDIO_PLUGINS=off`），
  你本机装的插件不会混进来。需要 ffmpeg / ffprobe 或中文字体的媒体测试，在缺这些的机器上会自动跳过。
- 金丝雀把 `tests/canary/mini_spec.json`（外加每张形态卡 × 中英）编译一遍，跟 `tests/canary/expected_prompts.json` 比对，
  有差异就以非零退出。改了提示词编译器、镜头库或风格库，前后都要跑；确认是有意的改动后才用 `--update` 接受新输出，
  并在提交说明里写清理由。
- 改了界面源码要重新 `npm run build`，把 `src/webui/static/app/` 一起提交：CI 会重新构建，并检查它跟源码一致。
- 其他贡献规则见 [参与贡献](../../CONTRIBUTING.zh-CN.md) 和 [AGENTS.md](../../AGENTS.md)。

### 5.4 CI

`.github/workflows/ci.yml`（工作流名 `ci`）在推送到 `main`、推送 `v*` 标签、提交 Pull Request 和手动触发时运行：

| 任务 | 内容 |
|---|---|
| `pytest (ubuntu-latest)` / `pytest (windows-latest)` | Python 3.11；装 ffmpeg（Linux 另装 `fonts-noto-cjk`）；`pip install -e ".[desktop,dev]"`；`pytest -q`；提示词金丝雀 |
| `frontend build + vitest` | Node 22；`npm ci`、`npm test`、`npm run build`；检查构建产物已提交 |
| `Windows 包` | 只在 `v*` 标签或手动触发时运行，见 §6.5 |

这个工作流已经写好，但截至 v0.1.0 还没在 GitHub 上实际跑过。

## 6. 构建 Windows 安装包

### 6.1 准备

- Windows x64 和 Python（CI 用 3.11）。打出来的包内嵌的就是构建用的 Python 版本，
  它也决定了 §3.5 一键安装台词识别时需要哪个版本的 Python。
- 在仓库根目录、激活虚拟环境后执行 `pip install -e ".[desktop]"`。构建脚本直接调用 PATH 里的 `python`。
- 可选：Node.js。PATH 里有 npm 时，脚本会先重新构建界面，否则直接用仓库里已提交的构建产物。
- 可选：Inno Setup 6.3 或更高（`installer.iss` 用了 6.3 起才有的 `x64compatible`；例如 `choco install innosetup`）。
  有它才会出 setup.exe。
- 可选：想让安装包带上 ffmpeg，就把 `ffmpeg.exe` / `ffprobe.exe` 放进 `desktop/ffmpeg/`（这个目录在 `.gitignore` 里，不入库）。
  注意 ffmpeg 本身的许可证，取决于你下载的是 LGPL 还是 GPL 构建。

### 6.2 一键构建

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build_windows.ps1
```

脚本的步骤：

| 步骤 | 做什么 |
|---|---|
| 1. 读版本号 | 从 `src/genkit/version.py` 读 |
| 2. 前端构建 | PATH 里有 npm 时：没有 `node_modules` 就先 `npm ci`，再 `npm run build`；没有 npm 就跳过 |
| 3. PyInstaller | `python -m PyInstaller desktop/storystudio.spec --noconfirm --workpath <系统临时目录>/storystudio-pyi-build`；没产出 `dist/StoryStudio/StoryStudio.exe` 就失败 |
| 4. 包内容自检 | `python desktop/verify_bundle.py dist/StoryStudio --ffmpeg desktop/ffmpeg`；不通过就停下，不出安装包 |
| 5. Inno Setup | 在 `C:\Program Files (x86)\Inno Setup 6\` 或 `C:\Program Files\Inno Setup 6\` 找到 `ISCC.exe` 时编译 `desktop/installer.iss`；找不到就只留单文件夹 |

产物：

| 路径 | 说明 |
|---|---|
| `dist/StoryStudio/` | 单文件夹程序：`StoryStudio.exe` 加 `_internal/`，整个文件夹复制过去就能用。体积约 80 MB 上下（目前的记录在 76–87 MB 之间），随依赖版本变化 |
| `dist/installer/StoryStudio-<版本>-setup.exe` | 安装包（装了 Inno Setup 才有） |

只要单文件夹的话，也可以直接：

```powershell
python -m PyInstaller desktop/storystudio.spec --noconfirm
```

这样 PyInstaller 的中间文件会留在仓库的 `build/` 下（已在 `.gitignore` 里）。建议事后再跑一次
`python desktop/verify_bundle.py dist/StoryStudio`。

### 6.3 包里有什么

`desktop/storystudio.spec` 打的是单文件夹、无控制台（`console=False`）、不用 UPX 的程序，入口是 `src/studio/desktop.py`。

- faster-whisper、ctranslate2、onnxruntime、numpy、torch 等大件被排除（台词识别按 §3.5 另装）；tkinter、测试框架等也不进包。
- `skills/` 和 `examples/` 只按 `desktop/bundle_manifest.py` 的白名单逐个文件带：镜头库、风格库、风格卡、审稿清单、
  三个内置 ComfyUI 工作流、9 个示例项目，外加许可文件 `LICENSE` 和 `docs/third-party-notices.md`。
- 插件、测试、`frontend/` 源码都不进包。

`desktop/verify_bundle.py` 在构建后检查产物，出现下面任何一种就以退出码 1 失败：

- 测试代码或测试框架；
- 白名单以外的数据文件，或者白名单里的文件（包括许可文件）缺失；
- 本机数据：`.env`、`settings.json`、`*.db` / `*.sqlite` / `*.sqlite3`、`*.log`、`logs/`、`content/`、`output/` 等；
- 插件；
- 构建机上的绝对路径（仓库目录、用户主目录等）；
- 可选的私有词表命中：从环境变量 `BUNDLE_DENYLIST` 或仓库根目录下不入库的 `.release-denylist` 读，命中时只报序号。

加 `--ffmpeg desktop/ffmpeg` 时，还会检查那个目录里只有 ffmpeg / ffprobe、动态库和许可说明。

### 6.4 安装包（Inno Setup）

`desktop/installer.iss` 的要点：

- 应用名默认取产品名，发布者 `avioralon`；AppId 固定，所以新版安装包会原地覆盖旧版；
- 默认目录 `{autopf}\StoryStudio`，允许在向导里改成只为当前用户安装；安装为 64 位程序；
- 安装语言：简体中文、英文；
- 开始菜单快捷方式和卸载入口，可选桌面快捷方式，装完可以直接启动；
- `desktop/ffmpeg/` 里有文件时，一起装进 `{app}\ffmpeg`，程序启动时会把它加进 PATH；
- 卸载只删程序文件（外加 `{app}\_internal\__pycache__`），不碰 `~/StoryStudio` 和 `~/.storystudio`。

手动编译（不传版本号时默认 `0.1.0`）：

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "/DAppVersion=0.1.0" desktop\installer.iss
```

> **未验证**：Inno Setup 这一步只配置在 CI 里，还没在本地实际跑过。脚本用到了
> `compiler:Languages\ChineseSimplified.isl`，如果你的 Inno Setup 里没有这个语言文件，编译会失败，
> 需要补上这个文件，或者去掉 `installer.iss` 里对应的那一行。

### 6.5 CI 里的 Windows 包

打 `v*` 标签或手动触发时，`Windows 包` 任务在两个测试任务都通过后运行：

1. `pip install -e ".[desktop]"`，PyInstaller 打包；
2. 无窗口冒烟两次：`--no-token` 模式在 8791 端口请求 `/api/health`，要求 200；
   默认的带令牌模式在 8792 端口确认进程没退出、不带令牌请求返回 401；
3. 包内容自检（`BUNDLE_DENYLIST` 从仓库 secret 传入）；
4. `choco install innosetup`，编译安装包；
5. 上传构建产物 `StoryStudio-windows`（`dist/StoryStudio` 和 `dist/installer/*.exe`）。

它不会自动发布 Release。

### 6.6 签名

exe 和安装包目前都没签名。要分发给别人，建议用自己的代码签名证书（例如 signtool）签名，
否则 SmartScreen 和杀毒软件更容易拦截。

## 7. macOS 打包

> **未验证**：`desktop/build_macos.sh` 已经写好，但从没在 Mac 上跑过。

脚本只能在 macOS 上运行。前提：脚本调用的是 `python3`，要先在 `python3` 所在的环境里，于仓库根目录执行过
`python3 -m pip install -e ".[desktop]"`。

```bash
bash desktop/build_macos.sh
```

流程：

1. PATH 里有 npm 时先构建前端；
2. PyInstaller 生成 `dist/StoryStudio.app`（bundle id `ai.avioralon.desktop`，最低系统版本 macOS 12.0），
   中间文件放在 `${TMPDIR:-/tmp}/storystudio-pyi-build`；
3. 包内容自检；
4. 设了 `CODESIGN_IDENTITY` 就用 `desktop/entitlements.plist` 签名；
5. 用 `hdiutil` 生成 `dist/installer/StoryStudio-<版本>.dmg`；
6. 设了 `NOTARY_PROFILE`（`xcrun notarytool store-credentials` 保存的名字）就提交公证并 staple。

- 没签名的包会被 Gatekeeper 拦下，要用户手动放行。
- [desktop/README.md](../../desktop/README.md) 建议 Apple Silicon 和 Intel 各打一次。
- pywebview 在 macOS 上用 WKWebView，兼容性同样没验证。

## 8. 部署说明

### 8.1 这是单用户的本机应用

- 服务固定绑定在 `127.0.0.1`，没有改监听地址的参数。
- 没有账号，也没有权限体系。谁能调用接口，谁就能花你模型账号里的钱、读写你的项目、改模型设置。
- 所以**不要**把它暴露到网络上：不要做端口转发、反向代理或隧道，也不要在服务器上跑起来给多人共用。
  Host 头不是回环地址的请求会直接被拒（403），远程和局域网访问都不受支持。
- 令牌和 Host / Origin 校验防的是同一台电脑上的其他网页和进程，不是为了抵御来自网络的攻击。
  安全方面的说明见 [SECURITY.md](../../SECURITY.md) 和[架构说明](architecture.md)。

### 8.2 --no-token 只用于调试

- 关掉令牌后，同一台电脑上的任何程序都能调用接口、花钱、读写项目。启动时会醒目地打印这条警告。
- Host / Origin 校验仍然有效，只是来源检查放宽为「任何回环地址」（这样 vite 开发服务器才能用）。
- 只在调试接口或开发前端时用，用完就关掉服务。平时想用 curl 调接口，带上 `X-StoryStudio-Token` 头就行（§4.5）。

打包版没有控制台，看不到启动时打印的带令牌地址。只是想换浏览器看界面的话，用 `--browser` 就行：
它会带着令牌打开系统浏览器，不用关令牌。只有要在打包版上用无窗口模式（`--no-open`）排障时，才需要配合 `--no-token`：

```powershell
& "<安装目录>\StoryStudio.exe" --no-open --no-token --port 8756
```

- `--no-open` 模式下打包版既没有窗口也没有控制台：用完在任务管理器里结束 `StoryStudio.exe`，
  或者执行 `Stop-Process -Name StoryStudio`。
- 打包版里，关掉令牌的那条警告只写进日志（§3.3），屏幕上看不到。

### 8.3 数据目录

- 一个数据目录就是一套独立的项目、产物和账本。可以用 `--project-root` 在不同目录之间切换（比如按题材或客户分开）；
  设置和密钥（`~/.storystudio`）是共用的。
- 可以同时开多个实例：各用各的端口，令牌 cookie 按端口区分，保存设置有跨进程锁。但同一个数据目录最好只让一个实例操作——
  「同一项目的同一种后台任务只能跑一个」这条互斥只在单个进程内有效。
- 数据目录放在本地磁盘上，路径尽量短。放在网盘同步目录里可能遇到文件被占用
  （设置文件写不进去时，程序本身也会提示检查杀毒软件或同步盘）。

### 8.4 无头运行与环境变量

没有图形界面的环境（比如 CI）可以用环境变量 `STORYSTUDIO_<角色>_<字段>` 临时覆盖模型设置，
例如 `STORYSTUDIO_TEXT_ADAPTER=openai_chat`。这些值不会写回 `settings.json`。

- 仓库根目录的 `.env.example` 列了示例。
- 这些 `STORYSTUDIO_*` 变量只从进程环境读；数据目录下的 `.env` 文件只用于 `SCRIPTGEN_*` 起草兜底。
- 全部环境变量见[配置指南](configuration.md)。

### 8.5 目录结构与备份

数据目录（`--project-root`）：

| 路径 | 内容 | 要不要备份 |
|---|---|---|
| `content/` | 各模块的项目文件（如 `content/story_video/<ID>.json`、`content/<模块>/<ID>.json`）和上传的素材 `content/assets/` | 必须 |
| `output/` | 生成的图片、视频、音频和成片；`output/story_video/storyvideo.sqlite3` 是短剧账本（SQLite，WAL 模式） | 必须（体积最大） |
| `local_workflows/` | 你自己的 ComfyUI 工作流和同名 `.map.json` | 有就备份 |
| `.webui_deleted/` | 回收站 | 按需 |
| `.webui_draft_cache/` | 后台任务记录（`tasks/`、`jobs/`） | 可选 |
| `.env` | 可选的 `SCRIPTGEN_*` 起草兜底配置，可能含密钥 | 有就备份，按机密处理 |

设置目录（`~/.storystudio`，或 `STORYSTUDIO_HOME` 指定的目录）：

| 路径 | 内容 | 要不要备份 |
|---|---|---|
| `settings.json` | 模型设置和**明文保存的密钥**（程序会尽量把文件权限收紧到当前用户）。设了 `STORYSTUDIO_SETTINGS` 时，按它指的位置备份 | 建议，按机密处理 |
| `voices.json`、`voices/` | 音色库、音色样本和参考录音 | 建议 |
| `plugins/` | 插件；如果是目录联接或符号链接，备份它指向的源目录 | 按需 |
| `logs/` | 日志（每个文件约 2 MB 滚动，另外保留 3 个旧文件） | 不需要 |
| `cache/`、`model_catalog.json`、`site-packages/` | 缓存、模型目录缓存、可选组件 | 不需要（能重新生成或重新安装） |

备份时注意：

- 先关掉程序，至少确认没有正在跑的生成任务，再复制。短剧账本是 SQLite 数据库，运行中复制可能得到不一致的副本；
  复制时连同旁边的 `-wal` / `-shm` 文件一起。
- 「镜头库与风格库」页的改动写回**程序自带的库文件**，不在数据目录里：
  - 源码运行时是仓库里的 `skills/ai-short-film/scripts/camera_library.json` 和 `style_library.json`，用 git 管理即可；
  - 打包版在 `<安装目录>\_internal\skills\ai-short-film\scripts\` 下。为所有用户安装到 `Program Files` 时，
    程序以普通权限运行，写不进这个目录，保存很可能会失败（按代码和系统默认权限推断，没有实际验证过）；
    只为当前用户安装时能保存，但重装或升级会覆盖，升级前自己另存一份。想长期改库，建议只为当前用户安装，或者用源码运行。

### 8.6 换电脑

1. 在旧电脑上关掉程序，复制整个数据目录，以及 `~/.storystudio`（至少 `settings.json`、`voices.json`、`voices/`）。
2. 在新电脑上装好程序和 ffmpeg，把两份拷贝放到对应位置，启动时用 `--project-root` 指向数据目录。
   新电脑上的程序版本要不低于旧电脑（新版写过的账本，旧版打不开）。
3. **已经开始制作（冻结）的短剧项目**：账本里记着输出目录的绝对路径。绝对路径变了，再准备或生成会被拒绝
   （`already has different inputs`）。要继续这类项目，就让数据目录在新电脑上保持同样的绝对路径；
   否则按 [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md) §5 开新 revision。还没开始制作的短剧项目不受影响。
   其他模块没有做过跨机器迁移的验证。

## 9. 配置模型

装好后，第一件事是在「模型设置」里给五个角色选来源：

| 角色 | 用途 |
|---|---|
| 剧本文本 | 剧本、起草、审稿 |
| 生图 | 首帧、插画、图片集 |
| 语音 | 旁白、配音、朗读 |
| 视频 | 镜头视频、单条视频 |
| 配乐 | 配乐模块的生成（没有内置来源，需要插件） |

- 每个角色选一个来源，填地址、密钥和模型，保存后点「测试连接」。
- 用 LiteLLM、new-api、one-api 这类 OpenAI 兼容网关时，可以在页面顶部填一次网关，一键应用到文本、生图、语音、视频四个角色。
- 有显卡的话，「扫描本机」会列出本机正在运行的 Ollama、LM Studio、vLLM、llama.cpp、ComfyUI、SD WebUI 等服务；
  本机地址不需要密钥，也不花钱。

各来源的字段、默认值、环境变量和数据流向见[配置指南](configuration.md)。

## 10. 安装插件

内置来源之外的模型来源由插件提供。插件是普通 Python 代码，以你的用户权限运行，**只装你信任的插件**。

插件只从两个地方加载：

1. `~/.storystudio/plugins/<名字>/`（设了 `STORYSTUDIO_HOME` 时是它下面的 `plugins/`）；
2. 环境变量 `STORYSTUDIO_PLUGIN_PATH` 列出的目录，多个用 `;`（Windows）或 `:`（其他系统）分隔。
   每一项可以是插件目录本身，也可以是装着多个插件的父目录。

`settings.json`、数据目录和 exe 所在目录里的插件一律不加载。

把插件仓库挂进默认目录（Windows，目录联接，不需要管理员权限）：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.storystudio\plugins" | Out-Null
New-Item -ItemType Junction -Path "$env:USERPROFILE\.storystudio\plugins\demo" -Target "<插件仓库>\demo"
```

macOS / Linux 用符号链接：

```bash
mkdir -p ~/.storystudio/plugins
ln -s /path/to/plugin-repo/demo ~/.storystudio/plugins/demo
```

然后重启程序，在「模型设置」页或 `/api/health` 的 `plugins` 里查看每个插件的加载状态和原因。

想先试一下，可以用仓库里不联网的示例插件 `tests/fixtures/plugins/example_async`：

```bash
STORYSTUDIO_PLUGIN_PATH="$PWD/tests/fixtures/plugins/example_async" storystudio --project-root /tmp/cc-plugin-demo
```

```powershell
$env:STORYSTUDIO_PLUGIN_PATH = "$PWD\tests\fixtures\plugins\example_async"
storystudio --project-root "$env:TEMP\cc-plugin-demo"
```

只指向 `example_async` 本身，不要指向父目录 `tests/fixtures/plugins/`——那里还有一批故意写坏、供测试用的插件。

- 停用单个插件：把目录改名为 `<名字>.disabled`（以 `.` 或 `_` 开头的目录也会被跳过）。
- 全部停用：设 `STORYSTUDIO_PLUGINS=off`（也认 `0` / `false` / `no`），或启动时加 `--no-plugins`。
- 打包版：插件用到的纯 Python 第三方包放进插件的 `vendor/` 目录；编译扩展必须跟程序是同一个 Python 版本。
- 插件接口 v1 是实验性的，1.0 之前可能调整。写法见 [docs/plugins.md](../plugins.md)。

## 11. 升级

**源码安装**：

```bash
git pull
pip install -e ".[desktop]"     # 依赖可能有变化，重新装一次
```

不需要 Node：界面的构建产物跟代码一起提交。

**安装包**：直接运行新版安装包。AppId 固定，会原地覆盖同名的程序文件，用户数据不动。
旧版有、新版没有的文件会留在安装目录里；想要干净的安装目录，就先卸载旧版再装新版（卸载同样不动用户数据）。

升级时要留意：

- **短剧账本**（`storyvideo.sqlite3`）会在新版第一次可写打开时自动迁移表结构；迁移后**旧版程序打不开**。
  想留退路，升级前关掉程序，备份 `output/story_video/storyvideo.sqlite3`（连同 `-wal` 文件）。
- **提示词措辞的改动会改变请求指纹**：已经冻结的短剧项目再跑会被拦下（`already has different inputs`），要开新 revision；
  单条视频项目会被标成 `stale`，再出片按新请求提交；图片集类项目要重新展开清单才会用上新写法。
  每个版本具体改了什么，见 [CHANGELOG.md](../../CHANGELOG.md)（比如 v0.1.0 的「提示词措辞调整」一节）。
- 设置里选的来源在新版里不存在（比如来自一个没装上的插件）时，设置会原样保留，界面把它标成「未加载」。
- 打包版：「镜头库与风格库」页的改动会被覆盖；为所有用户安装时这一页可能根本存不进去（见 §8.5）。
- 程序不会自动更新。设置里可以填一个更新清单地址，只有你点「检查更新」时才会去查，查到了也不会自动下载安装。

## 12. 卸载

**安装包**：

- 在 Windows「设置 → 应用」里卸载，或者用开始菜单里的「卸载 Content Cook · 内容创作工作台」。
- 卸载只删安装时装进去的程序文件，不删你的数据。自己放进安装目录的文件（比如手动放的 `ffmpeg.exe`）不会被删，
  安装目录可能因此留下来，手动删掉即可。
- 要彻底清理，手动删除：
  - 数据目录（默认 `~/StoryStudio`，或你指定的目录）；
  - `~/.storystudio`（设置与密钥、日志、缓存、音色、插件、可选组件）；设了 `STORYSTUDIO_SETTINGS` 的话，还有它指的设置文件；
  - 台词识别下载的模型（用过台词识别的话）：Hugging Face 缓存目录（默认 `~/.cache/huggingface/hub/`，
    设了 `HF_HUB_CACHE` / `HF_HOME` 时在那里）下的 `models--Systran--faster-whisper-*` 目录。
    **只删这几个目录**，`hub` 里可能还有别的程序下载的模型。
- 如果插件是用目录联接或符号链接挂进来的，先单独删掉链接本身，免得误删插件源目录里的文件。

**源码安装**：

```bash
pip uninstall storystudio
```

然后删除克隆目录。如果在仓库根目录运行过，数据也在仓库里（`content/`、`output/`、`.webui_*` 等），删之前确认不再需要。
`~/.storystudio` 同样要手动删除。

## 13. 常见问题排查

| 现象 | 原因与处理 |
|---|---|
| 启动时提示「端口 N 已经有别的程序在听」，退出码 2 | `--port` 指定的端口被占了。提示里会写出占用的进程（仅 Windows）和一个可用端口。换个端口，或先停掉那个程序。不指定 `--port` 时会自动在 8756–8799 里找空闲端口。 |
| 提示「N 端口上是别的程序在应答，不是本工作台」，或服务一直没起来，退出码 1 | 换一个端口再试；看日志 `~/.storystudio/logs/storystudio.log`。 |
| 接口返回 401：「这个本地服务只接受从桌面窗口（或启动时打印的带 token 的地址）发起的请求。」 | 打开的地址没带令牌、程序重启过（令牌已换），或 curl 没带请求头。用本次启动打印的 `/?token=…` 地址重新打开；curl 加 `X-StoryStudio-Token`。 |
| 返回 403：「这个本地服务只接受从本机窗口里发起的请求……」 | 用局域网 IP 或别的主机名访问，或请求来自别的网页。只能在本机用 `127.0.0.1` / `localhost` 访问。 |
| 界面顶部提示没在 PATH 里找到 ffmpeg / ffprobe；或者短剧的首帧、视频下载完后校验失败，报错里提到 ffprobe | 按 §4.2 安装；打包版也可以把两个 exe 放进安装目录或它的 `ffmpeg\` 子目录。改完重启程序。 |
| 打包版在「镜头库与风格库」页保存失败 | 为所有用户安装在 `Program Files` 时，程序没有权限写安装目录（按代码推断，未验证）。改成只为当前用户安装，或用源码运行（§8.5）。 |
| 后期合成或绘本提示找不到中文字体（`UI font unavailable`） | 安装一款 CJK 字体（Linux：`fonts-noto-cjk`），或在项目里指定字体文件（§4.2）。`/api/health` 的 `fonts` 字段会列出缺哪一类。 |
| 插件没加载 | 在「模型设置」页或 `/api/health` 的 `plugins` 里看状态和原因。检查：目录在 `~/.storystudio/plugins/` 或 `STORYSTUDIO_PLUGIN_PATH` 里；有 `__init__.py`，声明了 `STORYSTUDIO_API = 1` 和 `register(api)`；目录名不以 `.` / `_` 开头、不以 `.disabled` 结尾；没设 `STORYSTUDIO_PLUGINS=off`，也没加 `--no-plugins`。改完重启。 |
| 原生窗口打不开，或启动后报错退出 | 加 `--browser` 改用系统浏览器。Windows 上原生窗口依赖 WebView2。看日志。 |
| 安装包或 exe 被 SmartScreen / 杀毒软件拦截 | 程序没签名。确认来源可信后放行，或者自己从源码构建。 |
| 保存设置时提示设置文件写不进去 | 检查设置目录的权限和磁盘空间，看看是不是被杀毒软件或同步盘占着；另一个实例正在保存时，等几秒再试。 |
| 路径太长导致找不到文件或写入失败 | 代码没有专门处理 Windows 的 260 字符路径限制。把数据目录和克隆目录放在短路径下（比如 `D:\StoryStudio`），项目 ID 也别起得太长。 |
| 一键安装 faster-whisper 失败 | 打包版要借用本机同版本（按构建时的 Python，CI 是 3.11）且带 pip 的 Python。照设置页给的手动命令装，装完重启。 |
| `storyvideo` 找不到界面里的项目 | 命令行默认的数据目录是 `STORYSTUDIO_PROJECT_ROOT` 或仓库根，跟界面不同。加上 `--project-root`。 |
| 升级或搬家后短剧项目报 `already has different inputs` | 提示词措辞或数据目录的绝对路径变了。按 [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md) §5 开新 revision。 |

## 14. 验证状态

| 项目 | 状态 |
|---|---|
| Windows 上源码运行、PyInstaller 单文件夹打包并启动 | 实际跑过 |
| Windows 安装包（Inno Setup） | 脚本已写，只配置在 CI 里，本地没跑过 |
| CI 工作流 | 已写；截至 v0.1.0 发布时还没在 GitHub 上运行过 |
| Linux | 只有 CI 里的测试配置（截至 v0.1.0 还没运行）；没有安装包，原生窗口没测 |
| macOS | 打包脚本没运行过；源码运行也没测 |
| Python 3.10 / 3.12+ | 声明支持但没测，只测过 3.11 |
| 签名 | exe 和安装包都没签名 |
| 真实模型联调 | 只用真实文本模型（经 OpenAI 兼容接口）加 edge-tts 走通过播客的完整流程；各家直连密钥、音色克隆、本机模型的真实硬件、完整长度短片的出片与后期合成都没验证 |

更多见 [CHANGELOG.md](../../CHANGELOG.md) 的「已验证 / 未验证」和 [BACKLOG.md](../../BACKLOG.md)。
文档总索引见 [docs/README.md](../README.md)。
