# 架构

```
用户 ── webui（FastAPI + 原生 JS）── storyvideo 运行时 ── genkit 路由 ── 适配器 ── 你的模型账号
             │                          │                      │
        模型设置页                 sqlite 账本 / QC 闸       ~/.storystudio/settings.json
```

## 分层

| 层 | 包 | 职责 | 不做什么 |
|---|---|---|---|
| 契约 | `genkit.providers` | `ImageRequest` / `VideoRequest` / `TaskSubmission` / `TaskSnapshot`、`GenerationProvider` 协议、通用下载（http / file / data URL） | 不认识任何具体服务商 |
| 配置 | `genkit.settings` | 五个角色（text / image / voice / video / music）的适配器选择与凭据；打码；环境变量覆盖；`ADAPTERS` 表驱动 UI | 不持有网络连接 |
| 适配器 | `genkit.adapters.*` | 每个方法只做一次远端操作；同步接口把结果落盘并返回 `file:` 任务 id，异步接口返回带前缀的远端 id | 不轮询、不重试、不持久化 |
| 插件 | `genkit.plugins` | 启动时从 `~/.storystudio/plugins/` 加载仓库外的来源：登记适配器、异步前缀、能力记录与翻译；加载失败只记原因，应用照常启动（[docs/plugins.md](docs/plugins.md)） | 不从项目数据目录或设置文件加载代码 |
| 路由 | `genkit.router.RoutedProvider` | runner 眼里唯一的 provider：按角色装配适配器，`upload_asset` 返回本地令牌，`get_task` 按 id 前缀分派 | 不做业务判断 |
| 账本 | `genkit.store.RunStore` | sqlite/WAL：run、job、attempt、request_hash、artifact；崩溃可恢复 | — |
| 运行时 | `storyvideo.runtime` | prepare/run/review/reconcile/amend/retry-failed；参考链波次；三道 QC 闸；装配 | 不知道模型是谁 |
| 编译 | `storyvideo.prompts` + `skills/ai-short-film/scripts/*.json` | 镜头库 / 风格库 → 首帧与视频提示词；opt-in 字段（入口/出口状态、beats、表演锁、视觉风格） | — |
| 质检 | `storyvideo.qc` / `asr_qc` / `continuity` / `preview` | 技术 QC、人工决策、台词识别、接缝/表演/景别静态检查 | — |
| 后期 | `storyvideo.post_layers` | 旁白 VO、整屏插卡、画面文字、交叉溶解时间线、混音 | 不改生成产物 |
| 新界面 | `frontend/`（Vite + Vue 3）→ `src/webui/static/app/` | 资源为中心的三栏工作台（DESIGN §11）；只通过 HTTP 调后端，不依赖桌面壳的 JS 桥；hash 路由 | 不含业务规则，所有校验在后端 |
| 旧界面 | `webui` | 项目列表、逐镜卡片、QC、生成、成片面板、基础配置（镜头库/风格库）、模型设置、空状态引导；`routers/system.py` 的 `/api/health` 给界面报版本、ffmpeg、角色配置状态；`routers/scripts.py` 的 `/api/examples` 提供示例剧集复制 | — |
| 模块注册表 | `webui.modules` + `webui.branding` + `webui.routers.modules` | 内容创作工作台的功能模块表（短剧、图片工作台、单条视频、绘本、表情与头像、纪念写真、配乐、AI 播客、剧本工作台）、产品名唯一来源、`/api/modules`；列表 / 概览 / 任务带 `module` 标签（DESIGN §38） | 不搬内容目录，不改任何产线接口 |
| 制作中也能改 | `webui.revise` + `webui.routers.revise` | 一次编辑 → 逐镜比较编译后的请求算出作废/可复用 → 复制成新版本并带走产物与裁决 → import_legacy 进入制作 | 不改旧版本、不碰运行时 |
| 模型能力表 | `genkit.capabilities` | (角色, 适配器, 模型前缀) → 参数范围；开始制作前检查 spec | 表是数据，不做网络查询 |
| 模型目录 | `genkit.model_catalog` | 拉 OpenRouter 公开模型列表缓存到本机；参数区间覆盖能力表，id/模态/参考图检查 | 只在用户点「拉取」时联网 |
| 界面语言 | `webui.i18n` / `frontend/src/i18n.js` | 中文源文案 + 英文字典/正则模板；请求头选语言，中间件翻 JSON 文案键 | 缺翻译回落中文 |
| 统一网关 | `genkit.settings.Gateway` / `apply_gateway`、`adapters.openai_video` | 一份地址+密钥应用到四个 OpenAI 兼容适配器；视频按 OpenAI Videos 形状 | 角色配置仍是运行时唯一读的东西 |
| 音色库 | `genkit.voices` + `webui.routers.voices` | 声音档案（描述 + TTS 参数）、目录、试听、绑定给角色/旁白（拷贝语义，见 DESIGN §12） | 不改提示词编译；不碰账本 |
| 资源视图 | `webui.routers.project` + `webui.humanize` | 角色/场景增删改与锚图、引用关系图、概览与下一步；运行时英文守卫文案 → 人话 | 不改 spec 模型，不碰流水线 |
| 后期与识别 | `webui.routers.post` + `webui.tasks` | 后期块读写（冻结后经 `amend`）、台词识别闸与后期合成的后台任务与轮询、就绪检查 | 不重写判定与合成逻辑，只包 `asr_qc` / `post_layers` |
| 本机令牌 | `webui.app` 中间件 | 环境变量 `STORYSTUDIO_TOKEN` 存在时：`/?token=` 种 cookie，`/api/*` 校验 cookie 或 `X-StoryStudio-Token` 头；未设置则不拦（开发态、测试） | 不做用户体系 |
| 桌面 | `studio.desktop` | 生成令牌、起 uvicorn + pywebview 窗口；决定用户数据目录（`--project-root` > `STORYSTUDIO_PROJECT_ROOT` > 打包版 `~/StoryStudio` > 当前目录）；日志到 `~/.storystudio/logs/`；打包版把 exe 目录加进 PATH 以便放 ffmpeg | — |

## 两个根目录

* **代码资源根** `genkit.paths.code_root()`：镜头库、风格库、静态页、示例剧集。开发态是仓库根，打包后是 PyInstaller 的 `_MEIPASS`。
* **用户数据根** `genkit.paths.data_root()`：`content/story_video/*.json`、`output/`、`.env`、sqlite 账本。由 `STORYSTUDIO_PROJECT_ROOT` 决定，没设时退回代码资源根；桌面启动器启动时按 `--project-root` > 环境变量 > 打包版 `~/StoryStudio` > 当前目录 把它设好。

## 任务 id 约定

| 前缀 | 含义 | `get_task` 行为 |
|---|---|---|
| `file:<路径>` | 同步接口的结果已落盘 | 直接 FINISHED，URL 为 `file://` |
| `url:<http>` | 同步接口给了远端 URL | 直接 FINISHED |
| `openrouter:` / `ark:` / `minimax:` / `oaivid:` / `comfy:` | 内置来源的异步任务 | 交给对应适配器查询；角色现在换成了别的来源时直接报错，提示切回原来源或对这一镜重拍（不拿新来源的凭据去查） |
| 插件登记的前缀 | 插件来源的异步任务 | 同上；提供它的插件没加载时查不了进度，任务留在账本里、不记失败，装好插件重启后接着等 |

参考图与首帧不再「上传」：`upload_asset` 返回 `local:<路径>`，适配器在提交时编码成 base64 / data URL（OpenRouter、方舟、MiniMax 都接受）。

## 冻结与修订

spec 一经 `prepare` 冻结；改到会进提示词/参数的字段必须开新 revision（复制 spec，改 id 与 output_root，`import-legacy` 认领可复用的产物）。只改后期块（`post_graphics` / `voice_over` / 备注）用 `amend`；平台瞬时失败用 `retry-failed`。编译器任何改动先跑 `scripts/refresh_prompt_canary.py`，黄金提示词夹具在 `tests/canary/`。

## 新增一个模型来源

1. `src/genkit/adapters/<name>.py`：实现 `submit_image` / `submit_video` / `synthesize` / `complete_json` 中该角色需要的方法，用官方 SDK 优先；同步结果用 `store_result` 落盘。
2. `settings.ADAPTERS[role][name]`：label / fields / hint / defaults（UI 表单据此生成）。
3. `adapters.REGISTRY[role][name]`：构造函数。
4. 异步任务在 `genkit.adapters.ASYNC_PREFIXES` 登记前缀（插件用 `api.add_async_prefix` 登记）。
5. `tests/test_genkit.py` 用替身客户端加一个用例。

不想改核心的来源（私有服务、只在某个环境里能用的命令行工具、实验性接口）写成插件：放在 `~/.storystudio/plugins/<名字>/`，启动时登记适配器、异步前缀、能力记录与翻译，核心一行不改。插件接口见 [docs/plugins.md](docs/plugins.md)（v1 是实验性的，1.0 之前可能调整）。
