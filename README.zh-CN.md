# Content Cook · 内容创作工作台

[English](README.md) | 中文

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/aviorAlon/content-cook/actions/workflows/ci.yml/badge.svg)](https://github.com/aviorAlon/content-cook/actions/workflows/ci.yml)

**一个在自己电脑上跑的 AI 内容创作工作台：模型自己接，数据留在本机，花钱之前先告诉你要发几次请求。**

短剧、绘本、播客、单条视频、表情包、写真、配乐、剧本打磨——九个功能模块挂在同一个工作台上，共用模型设置、音色库、统一网关、任务中心、回收站与导出。模型由你自己接：云端账号、OpenAI 兼容网关或本机模型都行；项目、产物、账本和密钥都存在本机。

> 当前版本 **v0.1.0**，是第一个公开发布的版本。桌面版（PyInstaller 单文件夹）只在 Windows x64 上实际打过、跑过，Inno Setup 安装包那一步还没实际跑过；不少模型来源只有模拟测试（假客户端），没用真实账号联调过。动手之前请先看 [项目状态与已知限制](#项目状态与已知限制)。

## 目录

- [这是什么、给谁用](#这是什么给谁用)
- [功能模块](#功能模块)
- [设计特点](#设计特点)
- [数据与隐私](#数据与隐私)
- [快速开始](#快速开始)
- [运行与启动参数](#运行与启动参数)
- [模型来源](#模型来源)
- [命令行 storyvideo](#命令行-storyvideo)
- [打包桌面客户端](#打包桌面客户端)
- [开发](#开发)
- [文档导航](#文档导航)
- [项目状态与已知限制](#项目状态与已知限制)
- [参与贡献](#参与贡献)
- [致谢](#致谢)
- [许可证](#许可证)

## 这是什么、给谁用

Content Cook 是一个**本地优先**的 AI 内容创作工具。它把「写稿 → 出图 / 出视频 / 配音 → 质检 → 合成 → 交付」这类流程装进一个桌面应用：界面是本机的一个网页（原生窗口或系统浏览器），后端是 Python（FastAPI），生成用你自己配置的模型来源。

| 适合谁 | 常见痛点 | 这里给的 |
|---|---|---|
| 独立短剧 / 短视频创作者 | 在多个网站之间搬图搬视频，提示词、参考图、版本靠记忆和文件夹管理；生成完才发现台词念错、镜头接不上 | 一个项目一份 spec，逐镜卡片，三道审片闸 + 台词识别闸，成片之前就知道哪镜不对 |
| 小工作室的内容负责人 | 不同人用不同模型账号，成本和质量没法比；换模型要重写提示词 | 模型是「角色」而不是「厂商」：每个角色选一个来源，提示词编译与具体模型解耦 |
| 会写脚本的技术型创作者 | 想自动化，又不想自己维护一堆脚本 | 短剧的生产流程（校验冻结、首帧 / 视频、审片、台词识别、装配、后期合成、崩溃恢复）都有命令行（`storyvideo`），和界面走同一套运行时 |

**不做**：多人协作与权限、云端渲染队列、素材商城、账号体系、任何厂商独占的功能。服务只绑本机地址，不支持从局域网或远程访问。

### 关于名字

「Content Cook」是产品的工作名：界面、窗口标题和接口里的产品名都从 `src/webui/branding.py` 读（安装包脚本 `desktop/installer.iss` 另有一份默认 AppName，有测试守着必须和它一致，改名时要一起改）。下面这些是技术标识，**有意不跟产品名走**，以后改产品名也不会动它们：

| 标识 | 取值 |
|---|---|
| Python 包名（pyproject） | `storystudio` |
| 命令行 | `storystudio`（桌面启动器）、`storyvideo`（短剧命令行） |
| 设置目录 | `~/.storystudio` |
| 桌面版 | `StoryStudio.exe`、打包目录 `dist/StoryStudio/`、macOS 包 `StoryStudio.app` |
| 桌面版默认数据目录 | `~/StoryStudio` |
| 前端 npm 包 | `storystudio-frontend` |

## 功能模块

| 模块 | 做什么 |
|---|---|
| **短剧** | 剧本（按场）→ 逐镜分镜与提示词编译 → 首帧 → 视频 → 三道审片（首帧 / 视频 / 成片）+ 台词识别闸 → 装配 → 后期层（旁白、配音、配乐、插卡、画面文字、角标、静音、字幕）→ 海报与交付包。生产流程也有命令行 `storyvideo`。 |
| **图片工作台** | 单张 / 成组出图、多模型对比、图卡叠字。 |
| **单条视频** | 一条提示词出一段视频，按「全片设定 / 分段时间线 / 衔接与收尾」三段来写。 |
| **绘本 / 有声书** | 统一画风的插画 + 双语朗读与字幕；每页停多久按朗读音频的实际长度排。 |
| **表情与头像** | 按模板出一组图：表情包（微信规格）、贴图、多风格头像。 |
| **纪念写真** | 满月 / 百天 / 周岁 / 婚纱：按场景清单出一组照片，可叠祝语与日期。 |
| **配乐** | 按意图出配乐，一键投放到短剧的配乐层、绘本的 BGM 或播客的片头 / 垫乐 / 片尾。需要一个提供 `music` 角色的来源（目前没有内置来源，见 [模型来源](#模型来源)）。 |
| **AI 播客** | 对谈稿（环节 / 台词 / 听友问答）→ 静态检查与真人感审稿 → 逐句配音 → 混音 → 交付包。 |
| **剧本工作台** | 台词单独写、留版本、并排比一比再挑一版，投放到播客环节 / 短剧场次 / 镜，或从它们导入。 |

表情与头像、纪念写真、图片工作台共用同一套「图片集」引擎：按模板出一组图 → 挑选 → 本机后处理 → 打包。

各模块共用的部分：

- **模型设置**：五个角色（文本、生图、语音、视频、配乐）各选来源，带「测试连接」、统一网关和本机模型扫描。
- **音色库**：试听并保存音色，绑定给短剧的角色或旁白（绑定时拷贝参数）；播客逐句配音也从这里选音色。
- **出图风格卡**：24 张卡，图片集、绘本、海报、单条视频共用。
- **镜头库与风格库**：短剧提示词编译用的数据，可在界面里逐条改（写盘前会校验）。
- **任务中心**、**回收站**（删除可恢复）、最近项目、各模块的交付导出、中英双语界面。
- **示例项目**：随程序附带 9 个示例；除剧本工作台外，各模块首页都能一键复制成自己的项目，不花钱。

模块与服务的分层见 [架构说明](docs/zh/architecture.md)。

## 设计特点

| 特点 | 具体做法 |
|---|---|
| **本地优先** | 服务只绑 `127.0.0.1`，每次启动生成随机访问令牌，并校验 Host / Origin；项目、产物、账本、密钥都在本机；没有遥测。 |
| **模型自带、可替换** | 模型按「角色」配置，每个角色一个来源；内置 OpenAI 兼容、OpenRouter、火山方舟、MiniMax、Edge TTS 和本机服务（Ollama、ComfyUI、SD WebUI 等）；其余来源由插件提供。spec 里写 `"@image"` / `"@video"` 就用模型设置里的模型。 |
| **先预估，再花钱** | 短剧生成前先出预估（要发几次请求、哪些镜、失败和缺上游的镜），确认时带上预估指纹，服务端按磁盘状态重算，对不上就拒绝（409），一个请求都不发；其他模块在确认框里写明请求次数，本机来源直接写「不花钱」。AI 起草不自动重试、不偷偷多出候选。 |
| **冻结与账本** | 短剧「开始制作」时冻结 spec；短剧的每一笔付费请求都记进本地 SQLite 账本（带请求指纹）。提交前先记下「正在提交」，崩溃后只会留下待核对的记录，不会悄悄重复提交、重复扣费。单条视频、图片集、绘本、配乐把每次生成记在各自的项目文件里。 |
| **从不覆盖** | 每次重出都存成新文件（带序号或时间戳），失败不会顶掉上一次的好结果；成片原子写入，失败不留半个文件；删除进回收站，可以整体恢复。 |
| **质检前置** | 短剧三道人工审片闸（首帧 / 视频 / 成片）+ 技术 QC + 台词识别闸（本机 faster-whisper，字错率 ≤0.30 通过，≤0.45 待复核）；多数模块都有免费的本机静态检查（时长预算、运镜、台词清晰度、连续性、真人感等），文本模型审稿一次只发一个请求。 |
| **为长片省内存** | 后期合成按段流式渲染，不再把全片塞进一条 ffmpeg 命令；绘本逐页渲染再无损拼接；成片原子写入。 |
| **可扩展** | 插件从用户目录加载，登记新的模型来源、异步任务前缀、能力记录与翻译，不用改核心代码；插件出错时应用照常启动并说清原因。插件接口 v1 仍是实验性的。 |
| **有成熟库就用库** | 各家接口优先用官方 SDK（openai、openrouter、火山引擎 SDK），Edge TTS 用社区库 edge-tts（不是微软官方接口）；MiniMax、SD WebUI、ComfyUI 和 OpenAI 兼容 videos 走手写的 HTTP。桌面壳是 pywebview + PyInstaller，界面只通过 HTTP 和后端通信。 |

设计取舍与原因见 [设计说明](docs/zh/design.md)，完整的设计记录在 [DESIGN.md](DESIGN.md)。

## 数据与隐私

- 服务只绑 `127.0.0.1`，并带一个每次启动随机生成的访问令牌；还会校验 Host / Origin，挡住别的网页借浏览器操纵本机服务。
- 项目文件、产物、账本在**数据目录**（开发态默认启动时的当前目录，桌面版默认 `~/StoryStudio`，可用 `--project-root` 或 `STORYSTUDIO_PROJECT_ROOT` 指定）。
- 模型设置与密钥在 `~/.storystudio/settings.json`（可用 `STORYSTUDIO_HOME` 换目录），接口回传一律打码，日志与任务记录也会遮掉密钥。
- 生成时发出去的是提示词、参考图 / 参考音频和参数，收件方是你配置的模型来源（音色克隆会把你上传的参考录音发给 MiniMax）。
- 朗读、旁白、播客配音没指定音色时默认用 Edge TTS（微软的在线朗读服务），要念的文字会发给它。
- 其余联网都要你点了才发：拉 OpenRouter 公开模型目录、检查更新（填了更新地址才查，只查不下载）、Edge TTS 音色列表、一键安装台词识别（从 PyPI 下载）、第一次跑台词识别时下载 faster-whisper 模型（从 Hugging Face）。没有遥测。

| 位置 | 放什么 | 默认 |
|---|---|---|
| 数据目录 | `content/`（项目文件）、`output/`（产物与短剧账本）、任务记录、回收站、自定义 ComfyUI 工作流 | 开发态：启动时的当前目录；桌面版：`~/StoryStudio` |
| 设置目录 | `settings.json`（模型设置与密钥）、`logs/`、`cache/`、音色库、模型目录缓存、`plugins/` | `~/.storystudio` |

完整的目录结构和环境变量见 [配置说明](docs/zh/configuration.md)。

## 快速开始

### 环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | 桌面版（PyInstaller 单文件夹）目前只在 **Windows x64** 上打过、跑过；Inno Setup 安装包那一步还没实际跑过。macOS 打包脚本已写好但没在 Mac 上跑过；Linux 没有打包脚本。 |
| Python | 3.10 及以上（`pyproject.toml` 声明）；实际只在 **3.11** 上测过。 |
| ffmpeg / ffprobe | 要在 `PATH` 里。短剧装配与后期合成、绘本与播客出片、台词识别都用它们。下载：<https://ffmpeg.org/download.html> |
| 中文字体 | 烧字幕、插卡、画面文字要用 CJK 字体。Windows 用系统自带的微软雅黑 / 黑体 / 宋体；macOS 用苹方等；Linux 可装 Noto CJK（如 Debian / Ubuntu 的 `fonts-noto-cjk`）。 |
| Node.js | 只在修改界面时需要（建议 22 LTS）；构建好的界面已经提交在仓库里。 |
| 可选 | `faster-whisper`（台词识别闸）；本机模型服务（Ollama、LM Studio、ComfyUI、SD WebUI 等）。 |

### 方式一：从源码运行（推荐）

```bash
git clone https://github.com/aviorAlon/content-cook.git
cd content-cook
python -m venv .venv                 # 可选，建议用虚拟环境；macOS / Linux 上用 python3
# Windows：.venv\Scripts\activate    macOS / Linux：source .venv/bin/activate
python -m pip install -U pip         # 可编辑安装需要较新的 pip（仓库没有 setup.py）
pip install -e ".[desktop]"          # 含 pywebview（原生窗口）与 PyInstaller（打包）
pip install -e ".[asr]"              # 可选：台词识别闸用的 faster-whisper
storystudio                          # 起本地服务并打开窗口
```

- Linux：原生窗口没测过，建议 `pip install -e .`（不装 desktop 组）后用 `storystudio --browser`；装了 `.[desktop]` 但缺 Qt / GTK 的 Python 绑定时，启动器会提示原生窗口起不来并改用系统浏览器。
- 只支持**在克隆目录里的可编辑安装**（`pip install -e`）：镜头库、风格库、示例项目等运行时数据从仓库目录读取，不在 wheel 里。目前没有 PyPI 包。
- 界面的构建产物已经提交在 `src/webui/static/app/`，只跑 Python 不需要 Node。
- 不加 `--project-root` 时，数据写在启动时的当前目录。在仓库根目录直接跑也可以（`content/`、`output/` 等已在 `.gitignore` 里）；想和代码分开，就加 `--project-root <数据目录>`。

### 方式二：桌面版（Windows，自行构建）

仓库目前**不提供预编译的下载**。可以自己构建（需要先按方式一装好 `.[desktop]`）：

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build_windows.ps1
```

产物是单文件夹 `dist/StoryStudio/`（双击 `StoryStudio.exe`）；装了 Inno Setup 6 时脚本还会出安装包 `dist/installer/StoryStudio-<版本>-setup.exe`（这一步还没实际跑过）。CI 在推送 `v*` tag 或手动触发时也会构建 Windows 包，作为 Actions 产物 `StoryStudio-windows` 上传（不会自动发布 Release；截至 v0.1.0 发布时 CI 还没在 GitHub 上实际跑过）。exe 与安装包都未签名，Windows 可能会提示风险。

更详细的步骤（各平台、ffmpeg、台词识别、升级与卸载）见 [安装与部署指南](docs/zh/installation.md)。

### 第一次使用

第一次打开（还没有任何项目）时，工作台首页会显示「第一次用」：**配好模型 → 选一个模块开始 → 花钱前都会先问你**。以短剧为例，短剧模块在还没有项目时会显示三步引导：**配好模型 → 拿到一个项目 → 出首帧**。

1. 左栏「🔌 模型设置」：每个角色选来源 → 填地址与密钥 → 保存 → 「测试连接」。生图、语音、视频三个角色都有 OpenRouter 来源；文本角色选「OpenAI 兼容对话接口」，base_url 填 `https://openrouter.ai/api/v1`。这样一个 OpenRouter key 就能配齐四个角色；语音也可以先用免费的 Edge TTS。
2. 拿到一个项目：短剧首页的「复制示例剧集（不花钱）」或新建项目页顶部的「从示例开始」，把随程序附带的示例剧集（5 镜、有台词、有旁白、有一张插卡）复制成你的项目；也可以「用 AI 起草一集」，让文本模型根据一句话起草整集。其他模块（图片工作台、单条视频、绘本、表情与头像、纪念写真、配乐、播客；剧本工作台除外）的首页也有「复制示例（不花钱）」。
3. 打开项目 →「开始制作」→ 先给一镜「生成首帧」看风格，满意再整批生成。每次花钱之前都会先告诉你要发几次请求，确认了才提交。
4. 视频都出来后：「台词识别」（本机 faster-whisper，可选）核对每镜念的是不是台词；「生成后期合成版」把旁白、插卡、画面文字、字幕、混音一次合成。

spec 里的模型写 `"@image"` / `"@video"` 表示用模型设置里的模型（示例与 AI 起草默认如此）；写具体模型 id 则以 spec 为准。

## 运行与启动参数

```bash
storystudio                            # 起本地服务并打开原生窗口（没装 pywebview 时打开系统浏览器）
storystudio --browser                  # 用系统浏览器
storystudio --project-root <数据目录>   # 项目与产物放在指定目录
storystudio --version
```

`python -m studio.desktop` 与 `storystudio` 等价。全部参数：

| 参数 | 作用 |
|---|---|
| `--version` | 打印版本号（如 `storystudio 0.1.0`） |
| `--port PORT` | 指定端口；不指定时在 8756–8799 里自动找空闲端口。指定的端口被占用时报错退出，并给出一个空闲端口（Windows 上还会说明是哪个进程占着） |
| `--browser` | 用系统浏览器打开，不开原生窗口 |
| `--no-open` | 只起服务，不打开任何窗口，按 Ctrl+C 结束 |
| `--no-token` | 不启用本机访问令牌（只建议在调试接口时用） |
| `--project-root DIR` | 数据目录；桌面版默认 `~/StoryStudio`，开发态默认当前目录 |
| `--no-plugins` | 不加载插件（排查问题时用） |

启动器以 `http://127.0.0.1:<端口>/?token=…` 打开界面，之后接口调用靠 cookie 放行。想用 curl 调接口，加 `X-StoryStudio-Token` 头（值是启动时打印的令牌），或启动时加 `--no-token`（只建议调试时用）。日志在 `~/.storystudio/logs/storystudio.log`（桌面版没有控制台，出问题先看这里）。

## 模型来源

五个角色各选一个来源，填自己的地址与密钥：

| 角色 | 内置来源 |
|---|---|
| 文本（剧本、起草、审稿） | 任何 OpenAI 兼容对话接口（OpenAI / OpenRouter / DeepSeek / 通义 / 豆包 / 本机 Ollama、LM Studio 等） |
| 生图 | OpenRouter（支持参考图）· OpenAI 兼容 images · 火山方舟 Seedream · 本机 SD WebUI / Forge · 本机 ComfyUI |
| 语音 | Edge TTS（免费）· OpenAI 兼容 speech · OpenRouter · MiniMax（支持用参考音频克隆音色） |
| 视频 | OpenRouter（支持首帧）· 火山方舟 Seedance · MiniMax · OpenAI 兼容 videos（LiteLLM 这类网关）· 本机 ComfyUI |
| 配乐 | 没有内置来源：没有通用的音乐接口形状，需要装一个提供 `music` 角色的插件 |

- **统一网关**：用 LiteLLM、new-api、one-api 这类 OpenAI 兼容网关时，在「模型设置」顶部填一次网关地址和 key，测试连接后一键应用到文本 / 生图 / 语音 / 视频四个角色。
- **本机模型**：有显卡的话可以全程本地跑——「扫描本机」会列出在跑的 Ollama / LM Studio / vLLM / llama.cpp / ComfyUI / SD WebUI 和本机 OpenAI 兼容语音服务，本机地址不要 key、不花钱。扫描只探这些服务在本机的默认端口（手填地址也只接受本机或私有网段），不是网络扫描器。这是可选的路，云端来源什么都不改也照常能用。
- **插件**：内置来源之外的来源（私有部署的接口、只在本机能跑的命令行工具、还没公开的服务等）由插件提供。插件放在 `~/.storystudio/plugins/<名字>/`，启动时自动加载，核心代码不用改；插件缺失或加载失败时应用照常启动，界面会说清哪个来源用不了、为什么。插件是普通 Python 代码，以你的用户权限运行——只装你信任的插件；想临时不加载插件，设环境变量 `STORYSTUDIO_PLUGINS=off` 或启动时加 `--no-plugins`。写法见 [docs/plugins.md](docs/plugins.md)。
- **新增内置来源**：在 `src/genkit/adapters/` 加一个适配器并登记到 `settings.ADAPTERS` 与 `adapters.REGISTRY`（两张表必须一致，有测试守着）；异步来源还要在 `genkit.adapters.ASYNC_PREFIXES` 登记任务前缀。步骤见 [架构说明](docs/zh/architecture.md)。
- **无头部署 / CI**：可以用环境变量 `STORYSTUDIO_<ROLE>_<FIELD>` 临时覆盖设置（ROLE 为 `TEXT` / `IMAGE` / `VOICE` / `VIDEO` / `MUSIC`，FIELD 为 `ADAPTER` / `BASE_URL` / `API_KEY` / `MODEL` / `VOICE`，如 `STORYSTUDIO_TEXT_ADAPTER=openai_chat`），不会写回设置文件；示例见 [.env.example](.env.example)。

各来源的默认地址、字段与全部环境变量见 [配置说明](docs/zh/configuration.md)。各来源的真实联调状态见 [BACKLOG.md](BACKLOG.md) 的「现状盘点」：不少直连适配器只有模拟测试（假客户端），按官方文档与 SDK 签名写成，还没拿真实账号跑过。

## 命令行 storyvideo

短剧的生产流程都有命令行（AI 起草、海报、交付包、工艺审稿目前只在界面里）。剧集号对应数据目录下的 `content/story_video/<剧集号>.json`（在界面里「从示例开始」复制出来的第一个是 `DEMO-1`）：

```bash
storyvideo preview DEMO-1                    # 只渲染每镜编译后的提示词与时长预算，不锁定、不花钱
storyvideo prepare DEMO-1                    # 校验、建计划、冻结
storyvideo run DEMO-1 --through frames       # 出首帧（--no-wait 只提交不等）
storyvideo review DEMO-1 frames --decision pass
storyvideo run DEMO-1 --through videos
storyvideo asr-qc DEMO-1 --record-retakes    # 台词识别闸（可选，需要 faster-whisper）
storyvideo review DEMO-1 videos --decision pass
storyvideo run DEMO-1 --through assemble     # 本地装配成片，接下来是成片审片闸
storyvideo review DEMO-1 final --decision pass
storyvideo post DEMO-1                       # 后期层：旁白 + 插卡 + 画面文字 + 混音
storyvideo amend DEMO-1                      # 冻结后只改了后期块时接受修订
storyvideo retry-failed DEMO-1               # 把平台瞬时失败的任务重置，下次 run 重新提交
storyvideo status DEMO-1 --json
```

- 审片闸没通过时，`run` 不会越过它：比如视频还没 `review … videos --decision pass` 就 `run --through assemble`，会以退出码 `4` 停下。
- 只有 `run` 会提交付费的生成任务（记进账本）。`post` 合成旁白时调用语音来源：配了 Edge TTS 以外的语音来源时一般用它（按该来源计费），没配时用免费的 Edge TTS（要联网）；`reconcile` / `abandon` 会查询来源的任务历史；`asr-qc` 第一次运行会下载识别模型。其余命令都是本地操作。
- 退出码：`0` 成功，`2` 输入或运行错误，`3` 被安全闸拦下，`4` 停在未通过的审片闸，`130` 被中断。
- 命令行默认的数据目录是环境变量 `STORYSTUDIO_PROJECT_ROOT`，没设时是代码根目录（从源码装就是仓库根）；界面用的是别的目录（比如桌面版的 `~/StoryStudio`）时，每条命令加 `--project-root <数据目录>`。

崩溃恢复、防重复扣费、审片裁决与修订的细节见 [STORYVIDEO_RUNTIME.md](STORYVIDEO_RUNTIME.md)；镜头库、风格库、题材卡等随包数据的说明见 [skills/README.md](skills/README.md)。

## 打包桌面客户端

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build_windows.ps1
```

脚本依次做：前端构建（`PATH` 里有 npm 时）→ PyInstaller 出单文件夹 `dist/StoryStudio/` → 包内容自检（`desktop/verify_bundle.py`）→ Inno Setup 出安装包 `dist/installer/StoryStudio-<版本>-setup.exe`（装了 Inno Setup 6 才有）。只要单文件夹也可以直接 `python -m PyInstaller desktop/storystudio.spec --noconfirm`。CI（`.github/workflows/ci.yml`）在打 `v*` tag 或手动触发时产出 Windows 包。

- 包里只放运行要用的东西：`src/` 下的代码、已构建的界面、随包的技能数据（镜头库、风格库、风格卡、审稿清单等）、示例项目、`LICENSE` 与第三方声明（`docs/third-party-notices.md`）；白名单在 `desktop/bundle_manifest.py`。`tests/`、`frontend/` 源码、插件都不进包（插件在用户目录里，启动时从磁盘加载）。
- `ffmpeg.exe` / `ffprobe.exe` 默认不随包：放进 `dist/StoryStudio/` 或它下面的 `ffmpeg/` 子目录，或装到系统 `PATH`。想让安装包带上，打包前放进 `desktop/ffmpeg/`（注意 ffmpeg 自己的许可证）。
- 打包版的用户数据默认在 `~/StoryStudio`，设置在 `~/.storystudio`，卸载不碰这两处。
- 分发前需要自己签名；macOS 脚本 `desktop/build_macos.sh` 已写好但还没在 Mac 上跑过。

细节（台词识别的可选安装、无窗口模式、签名）见 [desktop/README.md](desktop/README.md) 和 [安装与部署指南](docs/zh/installation.md)。

## 开发

```bash
pip install -e ".[desktop,dev]"
pytest -q
python scripts/refresh_prompt_canary.py      # 改了提示词编译器后核对黄金输出
```

界面在 `frontend/`（Vite + Vue 3），构建产物提交在 `src/webui/static/app/`。改了界面要重新构建再提交（CI 会检查两者一致）：

```bash
cd frontend
npm ci
npm test          # Vitest
npm run build
```

前端热更新：先 `storystudio --no-open --no-token --port 8765` 起后端，再在 `frontend/` 下 `npm run dev`，打开 <http://localhost:5173/app/>（`/api` 反代到 8765）。旧版单页界面仍在 `/legacy`。

界面语言：侧栏底部可切中文 / English；英文字典在 `frontend/src/locales/`，后端文案规则在 `src/webui/i18n_rules.py`，缺翻译会原样显示中文。

目录：

| 路径 | 内容 |
|---|---|
| `src/genkit` | 模型契约、设置、适配器、路由、账本、插件加载、版本 |
| `src/storyvideo` | 短剧：spec、提示词编译、运行时、审片、台词识别、装配、后期层 |
| `src/webui` | FastAPI 服务与各模块接口 |
| `src/studio` | 桌面启动器 |
| `src/podcast` / `src/script` / `src/picturebook` / `src/imageset` / `src/clip` / `src/music` | 其余模块 |
| `src/imagecraft` | 出图风格卡与图像方言（图片集、绘本、单条视频、海报共用） |
| `frontend/` | 界面源码 |
| `skills/` | 随包的镜头库、风格库、题材卡、风格卡、审稿清单等数据与写作规范 |
| `examples/` | 随程序附带的示例项目 |
| `tests/` | 后端测试（不进安装包） |
| `desktop/` | 打包脚本、PyInstaller spec、安装包脚本、包内容自检 |

版本号只在 `src/genkit/version.py` 改一处，更新记录见 [CHANGELOG.md](CHANGELOG.md)。提交代码前请看 [参与贡献](CONTRIBUTING.zh-CN.md) 和 [AGENTS.md](AGENTS.md) 里的仓库规则。

## 文档导航

全部文档的中英索引在 [docs/README.md](docs/README.md)。

| 文档 | 内容 |
|---|---|
| [安装与部署](docs/zh/installation.md) | 环境要求、从源码安装、桌面包构建、ffmpeg 与台词识别、升级与卸载 |
| [架构说明](docs/zh/architecture.md) | 分层、模块、数据流、账本与任务 |
| [设计说明](docs/zh/design.md) | 设计原则与关键取舍 |
| [配置说明](docs/zh/configuration.md) | 模型角色与来源、设置文件、环境变量、目录结构 |
| [常见问题](docs/zh/faq.md) | 使用和排障中的常见问题 |
| [STORYVIDEO_RUNTIME.md](STORYVIDEO_RUNTIME.md) | 短剧命令行与运行时手册 |
| [docs/plugins.md](docs/plugins.md) | 插件写法（接口 v1，实验性） |
| `docs/*-spec.md` 等（索引见 [docs/README.md](docs/README.md)） | 各模块的规格契约：单条视频、图片集、图片工作台、绘本、配乐、播客、剧本工作台、本机模型，以及出图风格卡、长单镜提示词写法 |
| [desktop/README.md](desktop/README.md) | 打包细节 |
| [DESIGN.md](DESIGN.md) / [ARCHITECTURE.md](ARCHITECTURE.md) | 完整的设计记录与架构原文 |
| [CHANGELOG.md](CHANGELOG.md) / [BACKLOG.md](BACKLOG.md) | 更新记录；待做项与现状盘点 |

## 项目状态与已知限制

v0.1.0 是第一个 tag。能说清楚的现状如下：

**已验证**

- 用真实文本模型走通了播客从新建、起草、审稿到配音混音与导出的整条流程（文本模型经 OpenAI 兼容接口，配音用 edge-tts）。
- Windows x64 上打过 PyInstaller 包，打包版能开窗，无窗口模式接口可用。
- Edge TTS（微软的在线服务）实际合成成功；短剧的装配与后期合成用真实 ffmpeg 跑过（测试里）。

**未验证或有限制**

- 一部完整长度短片的端到端出片与后期合成还没用真实模型跑过。
- 多数直连来源（OpenRouter、火山方舟、MiniMax、OpenAI 的 images / speech / videos）只有模拟测试（假客户端），按官方文档与 SDK 签名写成；音色克隆还没用真实账号试过；本机模型没在真实显卡上验证过。
- 各种提示词方言（如 `gpt_image`、`nano_banana`、`minimax-h3`、`seedance`）没有对着真实模型做过对比。
- 打包版里的 faster-whisper 与后期合成没在真实视频上验证过。
- macOS 打包脚本没在 Mac 上跑过；Linux 没有打包，Linux 上的 pywebview 窗口没测过；Python 3.10 与 3.12+ 没测过。
- CI 配置了 Linux / Windows 的测试与 Windows 打包，截至 v0.1.0 发布时（2026-09-24）还没在 GitHub 上实际跑过；Inno Setup 那一步没在本地跑过；安装包与 exe 都没签名。
- 配乐角色没有内置来源，要用配乐模块的「生成」需要插件；各模块上传本地音频做配乐不受影响。
- 插件接口 v1 是实验性的，1.0 之前可能调整。
- 内置来源不支持查询任务历史：短剧里「提交结果未知」的任务（比如提交途中网络超时）没法原地核对或放弃，只能到来源控制台核对后开新版本（见 [STORYVIDEO_RUNTIME.md](STORYVIDEO_RUNTIME.md) §6）。

**测试**：后端 3000 多个 pytest 用例、前端 50 多个 Vitest 测试文件，另有提示词金丝雀守着提示词编译的输出。发布前（2026-09-24）在 Windows + Python 3.11 上跑的全量结果：后端 pytest 3214 条、前端 vitest 387 条全部通过；截至发布时 CI 还没在 GitHub 上跑过。

发布前做过两轮自查：发布前体检 87 条发现中 86 条成立并已修复（[docs/release-audit-2026-09-22.md](docs/release-audit-2026-09-22.md)），安全自查 19 条发现全部修复并补了回归测试（[docs/security-review-2026-09-23.md](docs/security-review-2026-09-23.md)）。逐项的已验证 / 未验证见 [CHANGELOG.md](CHANGELOG.md) 和 [BACKLOG.md](BACKLOG.md)。

## 参与贡献

欢迎提 issue 和 PR。开始之前请看：

- [参与贡献指南](CONTRIBUTING.zh-CN.md)（[English](CONTRIBUTING.md)）：开发环境、测试、提示词金丝雀、前端构建与提交规则。
- [安全策略](SECURITY.md)：发现安全问题请通过仓库的 GitHub 私密漏洞报告（Security Advisories）提交，不要开公开 issue。
- [行为准则](CODE_OF_CONDUCT.md)：采用 Contributor Covenant 2.1。

## 致谢

短剧模块的题材卡、形态卡、连续性锁、参考图用途、观众可见性、交付面、重拍处置等判据参考了 [zenstory-ai/drama-skills](https://github.com/zenstory-ai/drama-skills)（MIT），对照与取舍见 [docs/drama-skills-gap.md](docs/drama-skills-gap.md)。以取判据为主、不取它的语法；题材卡多数字段摘录自原卡原句（节选与压缩），置信度与依据照录原文，起草与视频提示词、静态检查、界面文案、测试夹具与设计文档里也用了一些原句（逐项见声明）。它的 MIT 许可与版权声明见 [docs/third-party-notices.md](docs/third-party-notices.md)（这份声明也随安装包分发）。

短剧的接缝（入口 / 出口状态）、表演与视线、镜头设计几条规则的思路参考了 [waoowaoo](https://github.com/waooAI/waoowaoo)（Elastic License 2.0）的视频导演规范，提示词金丝雀借了它的 prompt-canary 做法；没有使用它的代码、提示词或文字，所以只作致谢。

随包数据各文件的来源与许可（哪些是摘录、哪些是本项目自己写的）见 [skills/README.md](skills/README.md) 的「来源与许可」。

本项目还建立在许多开源项目之上，包括 FastAPI、Uvicorn、Pydantic、Vue、Vite、pywebview、PyInstaller、Pillow、edge-tts、faster-whisper 和 FFmpeg（FFmpeg 不随包分发）。

## 许可证

[MIT](LICENSE)，版权所有 © 2026 avioralon。随包分发的第三方内容及其许可声明见 [docs/third-party-notices.md](docs/third-party-notices.md)。
