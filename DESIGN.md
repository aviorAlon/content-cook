# 产品设计：Content Cook · 内容创作工作台（本地版）

status: v0.1 设计基线｜2026-09-07｜2026-09-09 定位调整为多模块内容创作工具，短剧是第一个模块（§38）

## 1. 一句话

给一个人或一个小团队用的**本地 AI 内容创作工具**，多个功能模块共用一套模型设置与音色库；第一个模块是短剧生产台：把「剧本 → 分镜 → 首帧 → 视频 → 质检 → 装配 → 后期」这条链装进一个桌面应用，模型由用户自己接，数据不出本机。

## 2. 给谁用、解决什么

| 用户 | 现状痛点 | 本产品给的 |
|---|---|---|
| 独立短剧/短视频创作者 | 在多个网站之间搬图搬视频，每一镜的提示词、参考图、版本靠记忆和文件夹管理；生成了才发现台词念错、镜头接不上 | 一个项目 = 一个 spec，逐镜卡片，三道质检闸 + 台词识别闸，成片前就知道哪镜不对 |
| 小工作室的内容负责人 | 不同人用不同模型账号，成本和质量不可比；换模型要重写所有提示词 | 模型是「角色」不是「厂商」：四类角色各配一个来源，提示词编译与模型解耦 |
| 会写脚本的技术型创作者 | 想自动化，但不想自己维护一堆脚本 | 全部能力有命令行（storyvideo），桌面壳只是同一套代码的界面 |

不做：多人协作与权限、云端渲染队列、素材商城、账号体系、任何厂商专属的「独家功能」。

## 3. 产品原则

1. **数据留在本机。** spec、产物、账本、密钥都在用户目录；只有提示词和参考图发给用户自己选的模型。
2. **模型是可替换的角色。** 文本 / 生图 / 语音 / 视频四个角色，每个角色一张适配器表；新增厂商只加一个适配器，不动流水线。
3. **每一步都可见、可回退。** 冻结（prepare）之后每一笔付费请求都有账本记录，可复用、可重摇、可开新版本；失败与审核拦截有人话原因。
4. **质检前置。** 首帧看完再出视频，视频听完（ASR）再装配；「拼接顿挫、台词听不清、镜头接不上」这三类观众投诉在成片前就有对应的检查。
5. **有成熟库就用库。** 厂商 SDK、TTS、HTTP、打包全部用官方或主流开源库；自写只留在没有 SDK 的接口。
6. **一份文档制。** 一件事只在一个地方记录：spec 记生产事实，剧本记故事，设计文档记产品。

## 4. 信息架构（界面）

```
左栏                      主区
├ + 新建项目（AI 起草）    项目详情：
├ ⚙ 基础配置               ├ 头部：id / 状态 / 复制为新版本 / 归档删除
├ 🔌 模型设置              ├ 生成条：生成首帧（全部）· 生成视频（全部）· 生成成片 · 批量通过
├ 搜索 + 状态筛选          │        · 本地两步（不花钱）：台词识别闸 · 生成后期合成版
│                    ├ 成片面板：后期合成版优先，播放 / 下载 / 通过 / 重摇
│                    ├ 后期层面板：旁白声线与逐句 · 整屏插卡 · 画面文字，逐条改/删/加；
│                    │            未冻结直接写，已冻结走 amend（后端只放行后期块）
└ 项目列表（按系列折叠）    ├ 告警折叠区：时长预算 / 运镜 / 台词清晰度 / 接缝·表演·景别
                           ├ 镜次导航条（可搜镜号或台词，颜色 = QC 状态）
                           └ 逐镜卡片：首帧 + 视频（下挂上次台词识别结果）+ 台词编辑（含 tone）+ 单镜生成 / 通过 / 重摇 + 提示词折叠
```

页面：
- **空状态引导**：一个项目都没有时主区显示三步（配模型 → 拿到一个项目 → 出首帧），第一步按 `/api/health` 的角色配置状态盖章，第二步内嵌示例剧集卡片与「用 AI 起草」按钮。
- **模型设置**：顶部显示版本、设置文件、项目目录、ffmpeg 与台词识别闸的可用性；四个角色卡片，每卡「来源」下拉 → 按适配器表生成字段（切换来源保留已填字段）→ 保存 → 测试连接（带耗时）。密钥回显打码。
- **基础配置**：镜头语言库、风格骨架库的逐条编辑（所有项目共用）。
- **新建项目**：顶部「从示例开始」（随程序附带的 `examples/episodes/*.json`，复制成新项目不花钱）；下方「AI 起草」：方向一句话 + 目标时长 + 风格档 → 模型设置里的文本模型起草整集 spec → 进入项目详情逐镜改。
- **环境提示**：ffmpeg / ffprobe 不在 PATH 时 header 下方常驻一条提示；项目页生成面板在生图/视频角色未配置时给出直达模型设置的提示。

spec 里的模型字段写 `"@image"` / `"@video"` 表示"用模型设置里该角色的模型"（AI 起草与示例剧集都这么写）；写具体模型 id 则以 spec 为准，便于单个项目指定模型。

## 5. 核心流程

### 5.1 一集的生命周期

```
起草/手写 spec ──prepare（冻结、建账本、编译提示词）──► frames ──人看 + 技术QC──► videos ──ASR 闸 + 人看──► final（装配）──► post（旁白/插卡/画面文字/混音）
      ▲                                                     │                        │
      └──── 开新 revision（改了生成字段） ◄──── retake（单镜重摇）／amend（只改后期块）／retry-failed（平台瞬时失败）
```

- **冻结**是产品的核心承诺：冻结后每一笔付费请求可追溯；改到会进提示词/参数的字段必须开新版本（复制 spec，`import-legacy` 认领可复用产物，只出新镜）。
- **参考链**：镜次可指定 `ref_frame`，首帧按依赖波次生成；链头错则整链错，所以对话段长链先看前两张再放行（写进了分镜规范）。
- **本地长任务**：台词识别闸与后期合成在界面里是后台任务（`/api/tasks/{id}` 轮询，记录落在 `.webui_draft_cache/tasks/`），同一项目同一种任务同时只跑一个；两步都不花钱，但提交前做就绪检查（ffmpeg、字体、视频是否齐、faster-whisper 是否装了），缺什么直接点名。
- **字体**：后期层的三种用途（本子字、插卡、字幕）各有一组按平台排列的候选字体，取第一个存在的；`post_graphics.fonts` 可逐项覆盖。

### 5.2 一镜的提示词从哪来

spec 的 `frame_style / scene_anchor / camera / cast.block / shot / art` 编译成首帧提示词；`motion / lines(tone) / camera_move / sfx / 锁句` 编译成视频提示词。镜头库（camera_library）与风格库（style_library）是编译器的数据，用户在「基础配置」里改。可选字段：入口/出口状态、beats 时码段落、表演锁、视觉风格四要素。提示词编译有黄金夹具（tests/canary），改编译器必须过。

### 5.3 质检

| 闸 | 谁判 | 判什么 |
|---|---|---|
| 首帧技术 QC | 程序 | 分辨率、文件有效、参考链完整 |
| 首帧人工 QC | 人 | 身份、构图、道具位置、可读文字 |
| 视频技术 QC | 程序 | 时长、流有效 |
| 台词识别闸 | 程序（faster-whisper） | 字错率 ≤0.3 过，≤0.45 待复核，其余自动记重摇；无台词镜查多余语音 |
| 视频人工 QC | 人 | 动作、表情、接缝 |
| 静态检查 | 程序（prepare 与详情页告警） | 时长预算、运镜合法、tone 含糊词、接缝状态缺失、情绪形容词、景别缺失、beats 时序 |

## 6. 数据模型

- **spec**（`content/story_video/<ID>.json`）：id、output_root、camera_profile、frame_style、scene_anchor、camera、neg、锁句、image_params、video_params、subtitle、delivery、cast{block, name, voice, asset=本地锚图路径}、shots[]、voice_over、post_graphics、design_notes。
- **账本**（sqlite）：runs（input_hash 冻结指纹）、jobs（step_id、kind、request_hash、status、task_id、artifact）；qc.json 三道闸的决策与失效原因。
- **产物**（`output/<ID>/`）：01_frames、02_video、07_vo、08_ui、manifests（snapshot、qc、run_state、asr_qc、post）、`<ID>_final.mp4`、`<ID>_post.mp4`。
- **设置**（`~/.storystudio/settings.json`）：roles{text,image,voice,video}{adapter, base_url, api_key, model, voice}、output_root、cache_dir。
- **缓存**（`~/.storystudio/cache`）：同步型接口的结果先落这里，再由账本安装到产物目录。

## 7. 模型适配层

见 ARCHITECTURE.md。要点：runner 只认 `GenerationProvider` 协议；`RoutedProvider` 按角色装配适配器；任务 id 前缀（file: / url: / openrouter: / ark: / minimax:）决定查询路径；参考图以本地令牌传递、由适配器编码；同步接口结果落盘为 `file:`。

当前来源：OpenRouter（图/视频/语音，官方 SDK）、OpenAI 兼容（文本/图/语音，官方 SDK）、火山方舟（图/视频，官方 SDK）、MiniMax（视频，REST）、Edge TTS（语音，库）。内置之外的来源由仓库外的插件登记（§57）。

## 8. 打包与分发

- 开发态：`storystudio` 命令（或 `python -m studio.desktop`）起 uvicorn + pywebview 窗口。
- 桌面包：PyInstaller 单文件夹（`desktop/storystudio.spec`），代码资源（镜头库、风格库、静态页、示例剧集）随包。
- 目录：用户数据在 `--project-root` > `STORYSTUDIO_PROJECT_ROOT` > 打包版 `~/StoryStudio` > 开发态当前目录；设置与缓存在 `~/.storystudio/`；日志在 `~/.storystudio/logs/`。打包版不把用户数据写在 exe 旁边。
- ffmpeg/ffprobe：打包版把 exe 所在目录及其 `ffmpeg/` 子目录加进 PATH；缺失时界面顶部提示并给下载链接。
- 本机访问令牌：启动器生成随机 token，窗口以 `/?token=…` 打开并种 cookie，之后 `/api/*` 必须带 cookie 或 `X-StoryStudio-Token` 头；防的是同机其他进程/网页调用这个能花钱的本地端口。
- 版本：单一来源 `src/genkit/version.py`（pyproject 动态读取），`storystudio --version` 与设置页、`/api/health` 显示同一个号；分发前签名。

### 8.1 技术栈决策（2026-09-07）

**结论：后端不换，桌面壳现阶段不换；等需要内置自动更新时再评估 Tauri 壳，不选 Electron。**

- 后端（Python：FastAPI + storyvideo 运行时 + genkit 适配器 + sqlite 账本）是这个产品的资产：约 1.2 万行、216+ 测试、ffmpeg / faster-whisper / 各家 SDK 的集成都在 Python 生态里最省事。任何壳都要把它作为 sidecar 打包，换壳不省掉 PyInstaller。
- 界面在 pywebview / Tauri / Electron 三种壳下都是同一份 HTML/CSS/JS：Windows 上 pywebview 用的就是 WebView2（Chromium），macOS 上 pywebview 与 Tauri 都是 WKWebView。"UI 好不好看"由前端代码决定，不由壳决定；"快不快"由后端与模型接口决定，壳的开销对这个应用可以忽略。
- 壳真正的差别在分发：安装包、自动更新、签名流水线、崩溃处理。真正需要这些之前，不值得同时维护两套构建工具链（Rust/Node + Python）。
- 真要换壳时首选 Tauri v2：壳体几 MB，内置 updater 与 bundler，sidecar 机制成熟；Electron 的优势（自带 Chromium 保证一致性）对以 Windows 为主、WebView2 本身就是 Chromium 的场景意义不大，还多 150MB。
- 为将来换壳预留的约束（现在就遵守）：界面只通过 HTTP 与后端通信、不依赖 pywebview 的 JS 桥；本机令牌已在后端实现；前端保持纯静态 SPA，后续引入 Vite 构建也不影响后端。

## 9. 质量门槛（发布前必须满足）

1. `pytest` 全绿；提示词金丝雀无漂移。
2. 用真实账号跑通「一集 ≥10 镜」全流程：首帧 → 视频 → ASR → 装配 → 后期，至少覆盖 OpenRouter 与一种直连厂商。
3. 新用户 10 分钟内：装好 → 配好一个 key → 用示例剧集出一条首帧。
4. 任何失败都有人话原因（配置缺失 / 余额 / 审核 / 平台超时）并给出下一步。
5. 密钥不出现在日志、清单、错误信息里。

## 11. 以资源为中心的工作台（重构计划，2026-09-07 立项）

从个人创作者的角度复盘 v0.1 界面：后端对象模型是对的，界面把工程模型直接搬给了用户——只有「镜」是一等公民，角色、场景、锚图藏在 JSON 里；绑定关系散在四个面板；术语全是工程词；项目页是一条长文档。重构分三步，后端流水线、账本、质检闸、提示词编译、命令行都不动。

### 11.1 目标形态

- **六类资源各有卡片与详情**：角色、场景、镜头、旁白、插卡与画面文字、成片。每张卡都能看到"我连着谁"并点过去：角色 ↔ 出现在哪些镜；镜 ↔ 参考了哪一镜 / 被哪些镜参考；镜 ↔ 挂了哪些旁白、插卡、画面文字。
- **三栏工作台**替代长文档：左栏项目内导航（概览 / 角色与场景 / 镜头 / 旁白与插卡 / 成片 / 高级），中间只显示当前选中的一个对象，右栏是检查器（关联、问题、状态、操作）。顶部一条进度加一个永远指向"下一步该做什么"的主按钮。
- **术语翻译层**：spec → 项目；锁定/冻结 → 开始制作；revision → 版本 v2；amend → 保存即生效；retake → 重拍；QC 闸 → 审片；import-legacy → 自动识别已有文件；camera_profile → 镜头风格；ref_frame → 参考上一镜画面；`@image` 不出现。
- **"制作中也能改"**：开始制作后仍可改任何字段，保存时预告后果（"会让第 3、4 镜的首帧和视频作废，重新生成约 N 次请求"），确认后自动走版本加重拍；只改后期块直接生效。
- **一镜的操作收拢到四个**：生成首帧、生成视频、重拍（自动完成记重拍加重新提交）、审片。
- **高级内容收纳**：镜头库、风格库、整份 JSON、提示词原文、账本、任务日志进"高级"，默认折叠；风格在项目里是带示例图的预设。
- **项目身份**：侧栏显示标题与第一镜首帧封面，id 退到详情。

### 11.2 三步落地

1. **后端接口（已完成）**：`routers/project.py`——角色与场景增删改（`/cast`）、锚图上传与预览（`/cast/{id}/asset`，存 `content/assets/<项目>/`，spec 记相对路径，归一化时解析为绝对路径给参考图）、引用关系图（`/graph`）、概览与下一步（`/summary`）；项目列表带标题、封面、进度；镜次详情带反向引用（`links`）；`webui/humanize.py` 把运行时英文守卫文案翻成人话。开始制作后改角色或锚图会被拒绝并列出受影响镜次——这是第 3 步"预告费用"的地基。
2. **前端重建（已完成第一版）**：`frontend/` 是 Vite + Vue 3 项目，构建产物落在 `src/webui/static/app/`，由 `/` 提供；旧单页保留在 `/legacy`（镜头库/风格库的逐条编辑暂时只在那里）。视图按资源组织：概览（进度 + 下一步）、角色与场景（列表 + 编辑 + 锚图上传 + 出现在哪些镜）、镜头（列表 + 编辑器 + 媒体 + 审片 + 关联检查器）、旁白与插卡（结构化表单，非 JSON）、成片、高级（版本、关系图、归档）。术语按 11.1 替换。还没迁的：镜头库/风格库编辑（在 /legacy）。
3. **"制作中也能改"（已完成）**：`webui/revise.py`。制作中的项目在镜头编辑器、角色页、配音卡里照常编辑，点保存时先 `revise-edit/preview`：把这次编辑应用到 spec 副本，逐镜比较编译后的首帧请求与视频请求（提示词、负向词、模型、画幅、参考图集合、时长、分辨率），任何一项变了就作废；参考链会传染（参考镜首帧变了，引用它的镜也重出）。弹窗列出要重出的首帧/视频镜数、可复用数、原因，用户确认新版本 id 后 `revise-edit/apply`：写新 spec、复制可复用的首帧/视频到新 output_root、把它们的审片裁决（技术检查 + 人工决定，按文件哈希）带过去、`import_legacy` 登记进新账本并进入制作。旧版本一字不动。用户视角是一个动作："保存并预告费用"。

### 11.5 id 是程序的事（2026-09-07）

用户只填名字。音色、角色/场景、镜次、项目的 id 都由后端生成（`genkit/ids.py`）：名字里有英文就压成 slug（"Girl Calm" → `girl_calm`），纯中文退到 `voice1` / `cast1`，撞了追加 `-2`；镜次按 `S<下一个序号>`；项目按 `EP-<日期>-<序号>`（复制示例是 `DEMO-<序号>`），表单里预填、可改。接口的 `id` 字段保留为可选：传了照旧校验（给脚本和高级用户），不传就生成。

### 11.4 失败的任务（2026-09-07）

生成失败不能只在账本里躺着。运行时把提交阶段的异常类名记进 `job.error`（`QuotaExhausted: …` / `ProviderRejected: …`），轮询到的失败保留服务商原话；`webui/humanize.classify_failure` 按文本分四类——余额或配额不足、内容被审核拦截、网络超时或服务不可用、其他——各配一句"怎么办"。`GET /failures` 列清单，`POST /retry-failed` 把失败任务重置回待提交（不花钱），审核类默认跳过并提示改写提示词（`force` 才重提），提交结果不明的任务要求先对账。预估接口标出失败镜，生成轮询遇到"只剩失败镜、没有进行中"就停下来把原因摆出来；概览页「失败的任务」卡有原因、原文、建议和一键"重提并重新生成"，顶部下一步在有失败时指向它；镜头列表标 ✗。

### 11.3 模型能力表

`genkit/capabilities.py`：(角色, 适配器, 模型前缀) → 能接受的时长、分辨率、画幅、是否收参考图/首帧/出原生对白。来源分 `verified`（实际提交验证过）与 `doc`（按文档）。`GET /capability-check` 按当前模型设置检查 spec；「开始制作」前先过一遍，verified 组合下的越界当错误拦（可强制），doc 级只提醒，没记录的模型只提示"提交时才会知道"。概览页常驻一张检查卡。表是数据，加模型加一行。

**模型目录**（`genkit/model_catalog.py`，2026-09-07）：静态表只能记我们跑过或读过文档的组合，服务商的模型在不停上下线。设置页可一键拉 OpenRouter 的公开列表（`/models`、`/images/models`、`/videos/models`，不需要密钥）缓存到 `~/.storystudio/model_catalog.json`：模型 id 输入框有补全；专用接口给出的时长/分辨率/画幅/首帧/原生音频/参考图上限压成和能力表同形的 `caps`，能力检查以 `source=catalog` 覆盖静态表（提醒级别——服务商公布的和实际接受的仍可能不一致，不硬拦）；目录里查不到的 id、模态不对（选了不出图的模型做生图）、不吃参考图/首帧的模型都会被点名。不自动刷新，没拉过目录时所有目录检查静默跳过。

## 12. 音色库（2026-09-07）

跨项目复用的声音档案（`~/.storystudio/voices.json`，试听样本在 `~/.storystudio/voices/`），一条档案两部分：

- **音色提示词**：按固定维度（性别、年龄段、音色关键词、语速、音量、口音/语言、情绪基调）拼成中英文两版，结尾固定"口齿清楚"；也可切到手写。选了 TTS 音色后可从目录的性别、口音、性格标签反推一版维度初稿，让"听到的"和"描述的"对齐。绑给角色后按项目提示词语言取一版拷进 `cast.<id>.voice`，进每一镜的视频提示词——视频模型直出台词时只能接受文字，同一段描述让各镜的声音"像同一个人"，维度越全差异越小，但不保证一模一样（模型限制）。
- **TTS 参数**：`engine`（`edge` 免费在线音色 / `settings` 模型设置里的语音模型）+ `voice` + `rate` + `pitch`。绑给旁白后拷进 `voice_over.engine` / `voice_over.edge`，旁白轨用它合成，这条路的音色是真正稳定的；同时拷进 `cast.<id>.voice_tts`，留给"台词 TTS 配音"的兜底路径（未做）。

**绑定是拷贝，不是引用**：改音色库不会悄悄改已冻结项目的提示词（账本的冻结承诺不能被一个全局文件绕过）。要同步就在项目里重新绑一次——绑给角色属于生成字段，开始制作后拒绝；绑给旁白是后期块，制作中也能改。

界面：侧栏「🎙 音色库」（列表、编辑、按引擎从目录选音色、试听）；角色页「音色」区（绑定/解绑、试听）；旁白与插卡页的声线可从库里选。目录：edge 走 edge-tts 在线表（需联网），OpenAI 兼容用固定表，其他来源手填。

**台词配音（TTS 兜底）**：`post_graphics.dubbing = {mode: off|all|shots, shots, duck}`，是后期块，制作中也能开关。开启的镜在后期合成时把每句台词用说话角色的 `voice_tts` 逐句合成（缓存按文本 + 参数），按字幕同一套时间分配铺进去（一句放不下顺延，超出本镜记进清单 `dub_overflow`），该镜时间段内原声压到 `duck`（默认 0.15）。这是角色台词音色真正稳定的路径；嘴型对不上的风险由镜头写法解决——就绪检查会点名"近景/特写且不是画外说话"的配音镜，以及没绑 TTS 音色的说话角色（后期合成前置拦截）。

## 57. 插件来源（2026-09-23）

内置来源只放通用的接口形状（OpenAI 兼容、各家官方 SDK、本机服务）。只在某个环境里能用的来源——私有服务、要本机登录的命令行工具、实验性接口——放进核心，等于让所有用户的设置页、能力表、翻译表都背着它，还把那个环境的细节带进开源仓库。所以核心只提供一套通用的登记机制，具体来源写成插件、放在仓库之外。

- **从哪加载**：用户主目录下的 `~/.storystudio/plugins/<名字>/`（跟设置文件同一个主目录规则），另可用环境变量 `STORYSTUDIO_PLUGIN_PATH` 追加目录；`STORYSTUDIO_PLUGINS=off` 或启动器的 `--no-plugins` 一个都不加载（排查问题时用）。**有意不从**设置文件、项目数据目录、exe 所在目录加载：设置页能改设置文件，项目目录里放的是别人发来或下载的东西，从那里加载代码等于让一个网页或一个项目包在本机执行代码。开发态、打包版、命令行三处按同一规则加载；插件不在安装包里。
- **登记什么**：适配器（角色、id、表单字段、默认值）、异步任务前缀、能力记录、英文翻译规则、失败分类的措辞。登记先进暂存区，全部校验通过才一次写入；跟内置来源或别的插件撞名、接口版本不对、前缀不合法，整个插件不加载，内置来源照常工作。
- **出错时应用照常启动**：插件目录不在、导入失败、登记失败，都只记一条原因（健康检查与设置页能看到）；设置页把指向它的角色显示成「X（未加载）」并给出原因，生成类接口在起任务之前就拒绝，不会把项目卡住；账本里它提交的在飞任务查不了进度时保持「等待远端」、不记失败，装好插件重启后接着等。保存设置时不会悄悄清掉指向它的角色。
- **核心里不出现任何插件的名字**：通用机制的测试用一个进程内的替身插件和几个故意写坏的插件。
- **接口是实验性的**：插件接口 v1 在 1.0 之前可能调整，改动记进 CHANGELOG；版本号对不上的插件拒绝加载并说明原因，不会带着对不上的接口跑。

契约、目录约定与写法见 [docs/plugins.md](docs/plugins.md)。

## 56. 长片需要的后期层与大首帧上传（2026-09-22）

几分钟、几十镜的竖屏短片，后期常要静音一段、全程挂一个角标、插一张纯色字幕卡、让旁白换声线并带字幕；这些以前只能在应用外用 ffmpeg 补，下次点「后期合成」就被冲掉。所以补成后期层，外加大首帧上传的一处修正。

**后期层四样（`storyvideo/post_layers.py`、`webui/routers/post.py`，都是后期块，锁定后也能改，不动生成请求指纹）**：
- `post_graphics.silence: [{shot, at, dur, label?}]`：数字静音。接在 loudnorm **之后**，用 `aeval` 逐采样置零（`volume=…:enable=` 在 loudnorm 按大块吐帧时一帧都判不中）；`manifests/post.json` 记换算后的绝对起止。删镜时它也算关联，改短镜头超出时进问题卡提醒。
- `post_graphics.labels: [{text, start, end, position, size, color, opacity, box}]`：文字角标（全程「AI 生成」、片头标题）。Pillow 画透明 PNG 再 overlay（Windows 的 ffmpeg drawtext 会因 fontconfig 崩），默认位置避开平台遮挡区；放不下时按比例缩字号并记 `shrunk`，支持多行。
- `phone_ui_inserts[].layout: "card"`：整屏纯色字幕卡（没有状态栏与手机外观），默认极缓推（`perspective` 做的亚像素推，`zoompan` 会抖）；`after: "__start__"` 允许插在第一镜之前。
- 旁白轨：`voice_over.lines[i].edge` 逐句声线（一个旁白轨里能有两种声线，比如女声画外音与男声画外音）；`voice_over.subtitles: true` 按真实 TTS 时长出字幕，同镜的台词字幕自动让路（旁白在台词字幕亮着时开口 → 台词字幕提前 0.1 秒收；台词字幕本该在旁白字幕亮着时出现 → 等它收了再出；不足 0.3 秒的不画，`manifests/post.json` 的 `subtitle_cuts` 记下来）；原来只要有一句旁白全片原声就乘 0.55，改成只在旁白时段压、边界是斜坡。没有这些字段时滤镜图与改动前逐字一致（快照测试钉着）。

**前端别冲掉新字段**：后期层页面保存时展开原对象再覆盖已知字段（`frontend/src/postlayers.js`），界面不认识的 silence / labels / 逐句声线不会丢。这一轮不做它们的编辑界面。

**大首帧上传代理图**（`storyvideo/runtime.py`）：大于 2.5MB 的首帧 / 参考镜先缩成上传代理图 `01_frames/_upload/<镜>-<源内容 sha8>.jpg`（长边 ≤1920：9:16 竖屏就是宽 ≤1080，16:9 横屏也不会缩到比 1080p 的成片还小；JPEG 画质逐档往下试到 ≤2.5MB，同一张图复用同一份）。代理图必须持久：路由来源的 `upload_asset` 只登记 `local:<路径>` 令牌、提交时才读文件；以前代理图放在临时目录里，到提交时已被删掉，提交失败又被当成结果未知，整次运行卡在 `blocked_submission_unknown`。代理图只换上传的字节，请求指纹不变。提交前还会查一次：已登记上传、还没提交的 job，`local:` 令牌指向的文件不在了（老版本的临时代理图、被清掉的 `_upload`），就丢掉这次上传（`discard_unsubmitted_job`，不碰付过费的尝试）重新上传。

**Seedance 2.5 的 1080p**：spec 的分辨率白名单放开 1080p（不是按厂商文档放的）；能力表里火山直连那条没有依据，不跟着改。**溯源**：`ai_disclosure` 补上旁白整块与逐句声线、各角色配音声线、配乐。

验收：`tests/test_post_layers_vo_silence.py`（25 条，含真 ffmpeg 用 silencedetect 量静音边界）、`tests/test_post_layers_visual.py`（24 条）、`tests/test_webui_post_visual.py`、`tests/test_storyvideo_runtime.py`（代理图持久、失效令牌重传）、`tests/test_export.py`、`frontend/tests/post-layers-keep.test.js`。改动经过一轮独立复审（12 条意见全部属实并修掉）后合并；后端 2202 条、前端 294 条通过。

## 55. 剧本工作台（2026-09-20）

需求：播客和短剧眼下最弱的都是台词与剧本，要一个单独的页面来调试、优化、修改剧本，调好之后给播客或短剧引用。台词原来只能在各自模块里原地改（短剧进项目 → 剧本页 / 镜头页，播客进那期 → 脚本页），想「同一段换三种写法再挑一版」没地方放，改坏了也回不去。新模块 `script`（✍️，路由 `/script`，项目文件 `content/script/<ID>.json`，编号 `SC-YYYYMMDD-n`）把**写 → 试 → 比 → 挑 → 用**独立出来，契约在 `docs/scriptlab-spec.md`。

- **版本从不覆盖**：每次生成、每次只重写几段、每次手改另存都是一版（`source: model / rewrite / hand / import`），`current` 指向采用的那一版。生成完不自动采用（要人比过再挑）——**只有第一版例外**（`spec.adopt_if_first`）：第一版没有可比的对象，不认它的话「用」页会一直说「还没挑一版」。
- **一条判据也不新造**（契约 §10）：把工作台的某一版拼成已有模块认的形状再调它们的函数。`podcast` 目标借 `podcast.lint` 的台词类判据与 `humanlike_checklist.json`；`drama_*` 借 `storyvideo.screenplay` 的单句字数 / 开口人数、`storyvideo.preview.shot_duration_budget` 的一镜窗口与 `craft_checklist.json`；`generic` 取两边交集。借来的判据没有英文口径的（按汉字定的单句字数、按 4.5 字/秒定的密度）就只判中文稿，不照搬——照搬等于每句英文都报太长。
- **投放与导入是拷贝**：投放把采用的那一版某几段角色对角色地写进那期播客的环节 / 某集剧本的场次 / 某一镜，写回一律过目标模块自己的归一化；导入是反向，把现有台词拿过来当新的一版。两边各存各的文字，改这边不会偷偷改那边。

真用起来发现两处，都不是工作台自己的毛病，是**把判据喂错了输入**：

- **段种类要人标，不能按台词猜**（`spec.BLOCK_KINDS = talk / qa`）。播客的「回答太短 / 复述问题 / 没标回答」三条只对听友问答成立。原来按台词自己的 `kind` 猜段种类，而起草提示词专门要求「短接话（嗯 / 对 / 啊？）单独成句」，模型顺手把对谈里的一问一答标成 question / answer——一次真模型重写之后，一段正常的冷开场被整段报成三条「回答只有 2 字」。现在段骨架带 `kind`，播客稿在「写 / 新建」页一个下拉，从播客导入时照抄环节种类（只有 qa 带过来）；提示词也跟着改成「`kind` 只在问答段里用」。
- **问题卡分类**：「回答只有 N 字——听众要的是结论加原因」这句里没有「台词」二字，落进了「其他」。`webui/problems.py` 的 `dialogue` 关键词加「具体例子」「结论加原因」两个词；这两条播客模块自己也会出，一起跟着正。

验收：`tests/test_script_{spec,craft,lint,review,use,router}.py` 116 条、`frontend/tests/scriptlab.test.js`，全量 2038 条通过。用一期已有的播客稿走过一遍（文本模型经 OpenAI 兼容接口）：导入 → 只重写一段 → 采用 → 检查零告警 → 投放回原来的播客（音频自动标过时，那一段原有的「机械轮流」告警消失）。模型审稿这条路没用真模型审过，仍是未验证。

## 54. 用真实文本模型走通播客全流程（2026-09-18）

用一个推理型文本模型（经 OpenAI 兼容接口）把播客从新建到导出完整走了一遍：新建（双人对谈、5 分钟、带素材）→ 起草 → 静态检查 → 只重写一段 → 真人感审稿 → edge-tts 合成 + 混音 → 导出交付包。结论与数字记在 `docs/podcast-spec.md` §7。这一趟修了三处，都不只影响播客：

- **文本模型改成流式接收**（`genkit/adapters/openai_compat.py`）。推理型模型先推理几分钟再出正文，原来一次性等整包，180 秒读超时必失败，SDK 还白白重试一次、共等 6 分钟才报错。现在默认 `stream=True` 只拼 `delta.content`，推理片段只用来保持连接；单次读超时变成「两个片段之间最多等多久」，另设总时长上限 15 分钟。网关不认 `stream_options` 就去掉再试，连 `stream` 都不认（400 且报错提到 stream）就退回一次性请求，网关忽略 stream 直接回整包也照收。短剧起草、单镜重写、题材 / 工艺 / 真人感三种审稿都走这条路。`.env` 里的代理路径（`_call_openai_proxy`）没改，那是开发用的老路径。
- **播客提示词里的轮流约束改成能数的**：只写「禁止机械轮流（一句一换人）」时，真实模型写出的环节仍会每一句都换人；改成「每个环节里至少有两处同一个人连着说两三句，短接话单独成句」后重写的那一段没再被报。样本只有一段，是经验改法。
- **音色重复按真正的音色比**：`podcast.lint.check_voices` 接受 `audio.host_profiles` 解析后的档案，接口层 `_lint` 传进去；读不到音色库退回按档案 id 比。
- **混音做响度标准化**：`podcast/audio.py` 的混音图最后加 `loudnorm=I=-16:TP=-1.5:LRA=11`，成品从 -23.1 LUFS 到 -16.5 LUFS。

验收：`tests/test_genkit.py` 加流式三条（只拼正文与用量、网关不认流式时两级回退、总时长上限）、`tests/test_podcast_lint.py` 加真正音色重复一条、`tests/test_podcast_audio.py` 的混音命令断言响度段。

## 53. 第三批待办：播客真人感审稿、播客交付包、英文词表与拒绝点定序（2026-09-17）

三件事同样并行做，各自经过一轮独立复审，合并后按剩余意见补修。

**播客真人感审稿**（`podcast/review.py`，契约 `docs/podcast-spec.md` §3.5）。静态检查只能数词；「两个人观点一致到分不清谁在说」「回答敷衍没结论」「台词里的数据不在素材里」是语义判断。做法与 §49 / §52 的两张审稿卡同一套：随包清单 `skills/ai-podcast/scripts/humanlike_checklist.json`（三组 11 条：口语 / 对谈动态 / 内容，全部由 `podcast/craft.py` 的八条约束与 lint 判据改写，置信度低），模型逐条判、程序逐字核对引文，核对复用 `genre_review.verify_items`，把环节当「场」传进去。播客特有的一点：清单里「机械轮流」「各说各话」「分不清谁在说」要引相邻两句才说得清，「嗯 / 对」这类接话又不够 4 字，所以核对字段除了每一句台词、节拍、听众问题，还放相邻两句拼成的「合句」（只给核对用，不进提示词也不进哈希）；合句是裸拼接、核对是子串匹配，只跨句界的碎片（前一句尾 + 后一句头）也会对上，所以核对之后再补一道：没落在任何单句里的引文必须整段包含合句中的某一整句，否则按「引文找不到」降级。落盘 `manifests/humanlike_review.json`，正文改过即过时；告警句「segment G03: 按真人感清单审稿（模型判断）：违反口语「…」，原文「…」…」归问题卡「播客」。接口 `POST/GET /api/podcast/{id}/humanlike-review`，任务 `pc-review`。前端 `ModelReviewCard` 泛化（接口前缀 / 引用号链接 / 刷新函数 / 「没对上」说明由 props 决定），脚本页永远显示这张卡，环节号链到本页环节卡。未验证：没有真模型审过真稿。

**播客交付包**（`podcast/export.py`）。照图片集交付包的样板：只拷白名单（spec.json 磁盘原样、mix.mp3、shownotes / transcript / chapters、lines/ 逐句、ai_disclosure.md），每次拷贝都验 resolve 后在 `output_root` 之内且不跟符号链接，0 字节的残文件不算合成过；manifest.json + checksums.sha256；包名带到分钟的时间戳，同一分钟已导出就报错不覆盖。溯源口径与短剧不同：播客不走账本，起草模型取项目文件 `draft`，主播音色取 `audio_manifest.json` 的 `hosts`，老清单退回导出时设置并在口径里标明。接口 `POST/GET /api/podcast/{id}/export`、`GET …/export/{name}`，任务 `pc-export`；配音页底部一张卡。未验证：没在真机上用真 TTS 合成后导出。

**英文词表与拒绝点定序**（`storyvideo/lint.py`、`podcast/lint.py`）。关系场景的八张中文词表各配一份英文表（小写、词边界匹配，"back" 不算 "background"），中英混写两边都查，正文没字才跳过；播客检查按 `spec.language` 选中英词表，英文稿的独白与填充词占比按词数。拒绝场先找「拒绝点」——正文里第一次出现拒绝表态或挡开动作的字符位置，有拒绝点时只报它之后的强行接触；强拒绝词（别碰我 / 放开 / let go…）任何位置都算，弱词（不要 / 不行 / no / sorry…）在别的句子里太常见，只在台词里直接算、动作正文里要写成开口的话（说话动词或引号 + 弱词 + 句读 / 句尾）才算，各段之间用换行拼接让句尾成立；跟强行接触词有重叠的候选一律不算。局限写在 docstring 与 `docs/script-craft-gap.md`：按字符位置的粗定位，不是语义顺序；词表自拟、置信度低。

验收：`tests/test_podcast_review.py`（24 条）、`tests/test_podcast_export.py`、`frontend/tests/podcast-review.test.js`、`frontend/tests/podcast-export.test.js`、`tests/test_relation_scene_en.py`、`tests/test_podcast_lint.py` 英文用例、`tests/test_webui_i18n.py` 批量表加句。

## 52. 第二批待办：剧本工艺审稿、账本记来源、关系场景与留白、配乐投放播客（2026-09-16）

四件事并行做，每件实现后经过一轮独立复审、修完再合并；共同点是都不需要 key 就能跑通替身测试。

**剧本工艺审稿。** 按题材审稿（§49）只在选了题材时能用；`docs/script-craft-gap.md` 里那批字符串判不了的语义判据（张力、情绪可见、节奏、合规四组，条目见 craft_checklist.json）做成一张不分题材的「剧本工艺清单」（`skills/ai-short-film/scripts/craft_checklist.json`，id `craft-v1`，随包），走同一套「模型逐条判 + 程序逐字核对引文」的闸门。清单四组各 4 条：张力 / 情绪可见 / 节奏 / 合规，每条写明判 ok / risk / violated 的条件，`basis` 注明依据与置信度——依据是编剧 / 表演 / 剪辑常识与网络微短剧常见的审核红线（多数置信度中），秒数、镜数、句数这类阈值明写是经验值、没有测量（置信度低）；顶层 `confidence` 写「低」。清单由三个视角（剧作 / 表演 / 平台审核）各起草一份再由评委合成，合成稿不引用任何中间稿。合规组四条，红线不做正则、只做模型审稿的一组，violated 是一票否决级别的风险。`genre_review.verify_review` 的核对逻辑抽成与清单无关的 `verify_items`，题材审稿变薄壳；新文件 `storyvideo/craft_review.py`（提示词 / `run_review` 写 `manifests/craft_review.json` / 过时判定 / `review_problems`），接口 `POST/GET …/craft-review`，任务 `craft-review`，问题卡新分类「工艺」（句头「按工艺清单审稿」，与题材两行排在播客前面：模型写的理由里几乎必然有「分歧」这类词）。前端把题材卡和工艺卡抽成共用组件 `components/ModelReviewCard.vue`，工艺卡永远显示；横幅的话以接口的 `note` 为准（清单文件读不出来时说清单的事，不说「剧本改过」），没审过但清单缺失也要出横幅。验收：`tests/test_craft_review.py` 22 条、`frontend/tests/overview-craft-review.test.js` 9 条。未验证：没有真模型审过真剧本，条目比题材卡长得多，模型会不会老实按条件判、通读全集才能判的条目会不会编引文、合规组会不会过度报 risk，都要拿 key 跑过才知道。

**账本记来源与模型。** 账本 jobs 表加 `provider` / `model` 两列（schema 2）：每个 job 在 `mark_submitting` 落库时记下真实用的适配器 id 与解析后的模型名（`@image` / `@video` 别名按适配器的 `pick_model` 解析），问不到记空串不猜；每个 job 只留最近一次提交。`RunStore.sources_for(run_id)` 按（来源, 模型）合并计数。账本路径只有一条规则 `storyvideo.runtime.ledger_path(project_root)`，四处（runner、详情页 / 在飞任务、启动续等、导出溯源）都按它找。老账本第一次可写打开时在一个写事务里 `ALTER TABLE` 补列（并发打开的后到者等前者提交后跳过；迁移中途抛错整体回滚）；只读打开不迁移，所以网页服务启动时先可写打开一次。交付包的溯源改成优先按账本：首帧 / 视频行带 `sources` 与 `from=ledger`，`ai_disclosure.md` 列出每个「来源 / 模型 ×job 数（按账本）」；剧本起草和账本里没记来源的类别按导出时的设置并标注；账本不存在 / 老账本 / 没这个 run / 整集都是老行 → 整段退回按设置，`basis` 分两种口径。读账本失败绝不让导出失败。未验证：没有用真实来源提交过一镜看账本落下的值；迁移与并发是手搓的 schema 1 库和裸连接模拟的。

**关系场景边界 + 留白豁免 + 单镜时长上限。** shot 新增可选字段 `scene_type`（confession / reunion / forgiveness / rejection / breakup 五种，只给 lint 看、不进提示词，镜头页有下拉）：`lint.check_relation_boundaries` 只对标了的镜判——表白看被表白的人有没有动静、重逢看凭什么认出来、原谅看两人往后算什么且亲近要两边都动、拒绝看怎么把人挡开、挡开之后不能再硬贴、分手看有没有撤不回的举动；词表是按 §51 那批判据自拟的经验词，置信度低，英文正文不判（词表只有中文）。`episode_contract.must_pay_off` 条目末尾写「（有意留白）」/「（留白）」= 作者明确不在本集兑现：`payoff_status` 改成三元组 `(paid, unpaid, deferred)`，`check_contract` 不报它，系列记忆记进未决并标「（有意留白）」；句号写在标记前后都认。`lint.check_shot_duration_ceiling`：单镜 >10 秒提示拆段（选没选形态都判，`check_form` 只看全集平均）。验收：`tests/test_script_craft_low.py`、`frontend/tests/shots-scene-type.test.js`。

**配乐投放到播客。** 配乐模块「放进项目」多一个目标：播客的 `music.intro / bed / outro` 三个槽之一（`slot` 是槽名，默认 bed），拷进 `content/assets/<播客 id>/music/`，整份过 `podcast.spec.normalize` 再写回，不绕过；槽名与中文名只在 `podcast.spec.MUSIC_SLOT_LABELS` 定义一次；成功提示按槽说「已放进《…》的垫乐 / 片头 / 片尾」。前端目标下拉多一组「AI 播客」，目标是播客时槽下拉代替起始秒。验收：`tests/test_music_use.py`、`tests/test_music_router.py`、`frontend/tests/music.test.js`。

## 51. 剧本工艺清单：能数的判据（2026-09-16）

把剧本与分镜常见的质控判据对照本仓库已有的检查过一遍，整理成「剧本工艺清单」。逐条对照见 `docs/script-craft-gap.md`，原则同 §18：取判据不取语法。关键词计分能被凑词绕过，**不做分数**；合规红线的正则在正当剧情里也会命中，**不做正则**，留给带引文核验的模型审稿（待做）。真正落地的是七条能数的判据和一份交付义务：

- 分镜层五条（`storyvideo/lint.py`）：画面文字载体没声明（`check_text_carriers`）、生产说明混进提示词（并入 `check_prompt_hygiene`）、一镜两种主运镜（`check_camera_motion_single`）、冲击 / 转折之后没有反应镜（`check_reaction_after_impact`，两镜都写了 purpose 才判）、旁白与对白同一时段 / 旁白超出本镜 / 旁白指到不存在的镜（`check_voice_over`，语速沿用 4.5 字/秒）。都走现有告警通道与问题卡分类（正文 / 镜头 / 表演 / 台词），老项目不写这些字段零变化；示例剧集的收尾旁白被这条查出本来会截断 1.6 秒，已改时间。
- 剧本层两条（`storyvideo/screenplay.py`）：一句台词 >25 字、开口的人 >3（旁白不算），都是软提示。
- 交付包（`webui/export.py`）：`manifest.ai_disclosure` + `ai_disclosure.md`——AI 生成声明（中英）、剧本 / 首帧 / 视频 / 旁白 / 台词配音各自的来源与模型、一句备案说明（md 里三段标题是【AI 生成说明】【用到的生成工具】【备案】，文件标题「AI 生成说明与工具清单」）。口径见 §52：首帧 / 视频优先按账本逐 job 记录（账本没记的退回按导出时的设置），每行标「（按账本）」或「（按导出时的设置）」，说不清的写 `unknown`。声音与配乐（2026-09-22 补全）按导出时的项目文件记、口径单独一句（`basis_audio`）：旁白整块声线（跟后期合成同一条规则判定实际走哪条合成路：没写引擎时「模型设置」里配了能用的非 edge 语音就记 settings 与导出时设置里的来源 / 模型、这一行标「（按导出时的设置）」，否则记 edge 并把没写的项按合成时的默认补上；记的都是合成时实际传过去的值）+ 逐句换声线（`voice_over.lines[i].edge`，旁白引擎是 edge 或没写时才算、总是走 edge-tts，没写的语速 / 音调沿用整块的）、每个角色的配音声线（`cast.<id>.voice_tts`，标这一集配音镜里用没用到；配音关着时 md 不列）、每条配乐的 label 与文件（项目内相对路径；写成本机绝对路径的只留文件名）。

验收：`tests/test_script_craft_checks.py`（12 条）、`tests/test_export.py` 改 9 份内容文件、`tests/test_webui_i18n.py` 加 9 句。未验证：阈值（25 字、3 人、0.3 秒）是经验值，没有抽样测量。

## 50. AI 播客模块（2026-09-16）

需求：加一个 AI 播客模块，包含场景、台词与问答，稿子的质量要尽量接近真人播客。契约在 `docs/podcast-spec.md`。这是第八个模块（🎙 `podcast`，`/podcast`，`content/podcast/<ID>.json`，前缀 `PC`），只依赖文本模型和 TTS，不碰任何图片 / 视频模型。

**一期播客的形状。** 场景 = 环节（`segments[]`，`cold_open / intro / topic / qa / outro` 五种），每个环节有目标、节拍、听众问题（只有 qa 用）和台词（`lines[]`：说话人 id / 一句话 / cue / talk|question|answer）；主播表 `hosts[]`（1–4 人，名字 / 角色 / 人设 / 立场 / 音色）；`brief`（选题 / 角度 / 听众 / 要点 / 素材 / 语气 / 避免）；`music`（片头 / 垫乐 / 片尾 + 增益 / 压低）与 `pause`（句间 / 环节间秒）。形式 `solo / duo / interview / roundtable` 决定人数与角色约束。说话人写错直接拒绝落盘，跟剧本层同一原则。

**「靠近真人播客」怎么落到代码上。** 不是形容词，是三处：
1. **起草提示词**（`podcast/craft.py`）把真人播客 vs AI 直出稿的差别写成八条硬约束：口语不是书面语、有人不同意、具体压倒观点（素材只从 brief 里取，不许编数据）、每个人是一个人、问答要像回答、冷开场不自我介绍、禁止清单（排比 / 升华 / 「今天聊了三点」/ 机械轮流 / 心理描写）、时长可数（4.5 字/秒）。一次 `_dispatch` 请求，JSON Schema 约束返回；说话人只认主播表 id（名字也认，换成 id）；`redraft` 只重写一个环节，其余原文进提示词当上下文。种类 / 标题 / 听众问题以人写的为准，模型改不动。
2. **静态检查**（`podcast/lint.py`，不花钱）用同一套判据反过来查稿子：结构（缺冷开场 / 开场 / 收尾、问答没问题、环节没台词）、书面语词表、独白超 220 字、机械轮流（≥8 句每句换人）、一人独大（>70% 字数）、全程没分歧、填充词密度（>8% 像结巴；≥20 句零填充词是播音腔）、话题环节没有具体例子（没数字也没「有一次 / 上周 / 我记得」）、回答太短或复述问题、收尾总结全篇、估算时长偏离目标 25%、两位主播同音色 / 没绑音色。告警是「segment G02: 影响——改法」/「全期：…」，问题卡新分类「播客」。阈值是可调默认值，不是行业标准。
3. **混音的停顿**（`podcast/audio.py`）：句间 0.35s、环节间 1.2s、cue「停顿」再加 0.6s；片头曲放在冷开场之后、开场之前（真人节目的惯例），片尾曲在最后一句后；垫乐循环、按增益铺底、说话时段按 `duck` 压低、末尾渐弱。

**配音。** 逐句 TTS 到 `07_audio/<环节>_<序号>.mp3`，清单记文本 + 音色参数的哈希，没变就跳过（绘本朗读同一套）；主播音色是音色库档案 id 就走 `synthesize_with_profile`，内联档案照用，空则按语言轮流分配占位音色（两个人尽量不同声）。混音一条 ffmpeg `filter_complex` → `08_mix/<ID>.mp3`（句子多于 64 句先分组预混，一条命令的输入数与命令行长度不随句数涨；成品先写临时文件再原子替换），同时写 `chapters.json`（每个环节的起点）、`transcript.txt`（`[mm:ss] 老周：…`）、`shownotes.md`（标题 / 一句话 / 主播 / 章节 / 要点 / 听众问题 / AI 合成声明）。没装 ffmpeg 时逐句音频照样出，混音那步把原因写进任务结果（不算任务失败）。

**接口与前端**（契约 §5 / §6）：`/api/podcast` 列表 / 编号 / 形式 / 新建 / 示例 / 读写删 / `draft` / `segments/{sid}/redraft` / `synthesize` / `assets`（片头垫乐片尾上传）/ 产物流；任务 `pc-draft`「播客起草」、`pc-audio`「播客配音」。前端 `/podcast`（首页 / 新建 / 脚本 / 配音 / 高级）、侧栏块、任务中心种类名、最近记录路由。示例 `examples/podcast/short_video_demo.json`（双人对谈 4 分钟 41 句，手写稿；除「主播没绑音色」外零告警，复制即可用 edge-tts 合成整期）。

验收：`tests/test_podcast_{spec,craft,lint,audio,router}.py`（62 条：形式与人数校验、提示词带八条约束与骨架、回填只认主播、假模型起草落盘、每条判据一个坏例子触发且示例干净、逐句合成哈希跳过与失败隔离、时间线停顿与片头片尾、ffmpeg 命令形状、假 runner 混音落盘三份文字、接口 400/404/409 与产物流路径安全）、`frontend/tests/podcast.test.js`（14 条）、`tests/test_webui_i18n.py` 英文批量表加 50 句、`tests/test_modules.py` / `test_recent.py` / `test_packaging_files.py` 登记。

来源与置信度：八条约束和判据阈值是按播客制作常识与对 AI 直出稿的观察归纳的经验规则（契约 §7），没有抽样测量。**未验证**：文本模型按这套提示词出的稿子像不像真人（要拿 key 听过）；edge-tts 中文对谈的语气与停顿手感（本机能跑，没做主观评测）；minimax 克隆音色走的是音色库现成的路。

## 49. 按题材审稿（2026-09-11）

选了题材之后，要有按题材的专业检查。此前没有：题材卡（§26）只进起草的系统提示词，写完没人再拿它对照剧本。更糟的是，选的题材根本没存进项目文件（`assemble_spec` 写了 `form`，没写 `genre`），事后想查也没有依据。

**先把题材存下来。** `spec.genre` 是可选顶层字段，存卡 id：id / 中文名 / 别名在归一化时统一成 id，不认识就 `SpecError`。起草用了题材卡时，`assemble_spec(genre=)` 把它写进去。`revise.GLOBAL_KEYS` 和 `/globals` PUT 接受 `genre`：不认识返回 400「不认识的题材：…」，空值删键。概览页头部「形态」旁边加「题材」下拉，保存路径跟形态一样（制作中走 revise）。genre 不进任何提示词：老项目零变化，金丝雀无漂移，制作中改题材 impact 是 0 镜。

**为什么用模型审，不做词表 lint。** 卡里的「常见坑」「禁止漂移」是语义条目，比如「孩子只负责卖萌、撮合和说金句」「不漂成家庭关系戏：冲突来自尚未被承认的关系」。字符串匹配判不了，硬做只会出噪音。drama-skills 的 short-drama-review 用审稿表交给模型执行，要求每条 finding 带位置和短引文、不给无证据的结论。这里照搬这个做法，再加一道程序核对，让模型没法凭空编问题。

**流程**（`storyvideo/genre_review.py`）：
- `build_review_prompt`：系统提示词定审稿人角色和规则——只按卡的题材核心 / 常见坑 / 禁止漂移判；每条回答 ok / risk / violated；判 risk / violated 必须逐字引原文并列出镜号或场号；找不到原文就判 ok；不评卡外的写作好坏；不编造。用户提示词带编号的卡片条目，加上剧名、梗概、契约，以及每镜的画面 / 美术 / 动作 / 节拍 / 台词（带镜号）；有剧本文件时再带上场次（带场号）。
- 一次 `_dispatch` 文本请求，跑在后台任务 `genre-review` 里：`POST /api/episodes/storyvideo/{id}/genre-review` 返回 `{task_id, cost_note}`；没选题材返回 400，已经在跑返回 409；文本角色是本地模型时说不花钱。不自动重试，也不多审几次挑一个。

**证据核对闸门**（`verify_review`）：
- 编号对不上任何卡片条目、source / verdict 不认识、同一条答了两次：丢掉，计入 `dropped`。
- risk / violated 逐条核对：引文去掉空白、标点、符号后至少 4 个字；必须逐字落在**所列镜 / 场的某一个字段**里（一句台词、一条节拍、画面 / 美术 / 动作之一；没列号就在全集任一字段里找，含契约）；所列号都必须存在。引文带字段名或说话人前缀的，去掉前缀再试一次；带省略号的，按段、按顺序在同一个字段里找。
- 没通过的条目保留，标 `evidence_verified: false` 并写明原因（没给引文 / 找不到 / 太短 / 号不存在），violated 降为 risk，计入 `demoted`，**永远不进告警**。条目原文一律用卡里的，不信模型抄的。
- 结果写入 `<output_root>/manifests/genre_review.json`，字段是 `card_id / card_name / model / backend / created_at / spec_hash / result / verification{checked, verified, demoted, dropped, missing}`。

**进问题卡。** 只有核对过的 risk / violated 会变成告警，句式是「shot S01: 按题材卡「亲子隐秘」审稿（模型判断）：违反常见坑「…」，原文「…」，理由「…」——建议「…」」。只引一镜挂 shot，只引一场挂 scene，引了多处或没引号挂「全集」并列出号。问题卡新增分类「题材」，排在最前面，因为模型写的理由里什么关键词都可能出现。模型写的字进句子之前去掉「——」和「」，保证问题卡的影响 / 改法切分不乱，英文翻译时引号里的内容原样保留。审过的问题同时出现在详情页的问题卡里。

**过时。** `spec_hash` 是「审的那份正文 + 题材卡 id」的稳定哈希，不是整份 spec 的哈希——改负向词、改画幅不算剧本改了。正文、剧本场次或题材变了，`GET` 就返回 `stale: true` 和一句提示；过时的审稿不出告警。概览卡顶部显示「剧本改过，这份审稿可能过时」和重新审稿按钮。

**界面。** 概览页的「按题材审稿」卡只在选了题材时出现，内容依次是：卡名和一句说明；运行前的成本确认；在跑时轮询（重开页面会接上在跑的任务）；结果按 违反 / 有风险 / 没问题 分组，每条列出条目、高亮的引文、镜号链接、理由和改法；没核对上的一组默认折叠；页脚写「这是模型按题材卡的判断，不是保证」。

验收：
- `tests/test_genre_review.py` 20 条：
  - 字段：归一化、起草落盘、globals、impact 为 0。
  - 提示词：卡片条目逐条编号、每个镜号都在。
  - 核对：真引文通过；编造的引文降级且不出告警；引文不在所列镜、太短、镜号不存在都不通过；编号越界和重复被丢；带前缀和省略号的真引文通过。
  - 其他：过时判定；接口 400 / 409、任务结果、GET 形状、英文问题卡；本地文本模型的免费说法。
- `frontend/tests/overview-genre-review.test.js` 8 条。
- `tests/test_webui_i18n.py` 的英文批量表加了 8 句。
- 提示词金丝雀无漂移。

未验证：还没有真模型审过真剧本。以下几件事都要拿文本模型对几集真稿跑过才知道：模型会不会老实按规则逐字引原文；4 个字的门槛和「必须落在所列镜的同一个字段里」会不会误伤真问题（比如模型把跨两句台词的意思合成一句引文）；条目漏答率有多高。

## 48. 题材与镜头风格的出处、置信度与搭配（2026-09-11）

题材和镜头风格各有什么出处、有没有专业的约束或检查？逐项审计下来，分四种情况，外加一处缺口：

- **有来源（节选）——题材卡**。12 张卡（`skills/ai-short-film/scripts/genre_cards.json`）是 zenstory-ai/drama-skills 题材卡的节选与压缩（MIT；原卡见 zenstory-ai/drama-skills 仓库的 `skills/short-drama-develop/references/genre-cards/`），多数字段直接摘录原卡原句，许可与版权声明见 `docs/third-party-notices.md`。原卡每张在 frontmatter 写了 `confidence` / `basis`，并在「证据与置信度」一节写明：例子全部合成虚构，不来自抽样统计、平台热度或任何真实作品；置信度只指套件内交叉印证的程度，不指测量结果。**我们节选时把 confidence 和 basis 丢了。** 现在每张卡补回 `confidence`（抄「证据与置信度」段的原文：7 张「高」、5 张「中」——原卡 frontmatter 里中英混写，亲子隐秘写「中」，其余写 high / medium，意思一致）、`basis`（抄 frontmatter：「套件既有工艺文件与短剧写作经验归纳」，12 张相同）、`source_file`（原卡文件名）；`_meta.provenance` 一句话写明例子是合成的。文件 41.8 KB。
- **本仓库自写、风险是推断——镜头库**。`camera_library.json` 的运镜条目是本仓库自己的文字（运镜名用通用镜头术语；名称、说明、中英提示词短语与 `function` 标签都自写）；**每个运镜的 `risk` 都是 `inferred`**：按已知的失败模式推断，不是逐个运镜测出来的，不证明单个运镜可靠。库顶层新增 `_basis` / `_confidence` 把这句话写成字段，`_note` 同步写明。
- **人工设定——五档镜头风格**。`classic_film` / `short_drama_vertical` / `anime_action` / `tv_drama` / `suspense_thriller` 是 2026-09-08 **按 `refs` 里作品的公认风格人工设定**的，**没有对这些作品做逐镜分析**，也没在本仓库跑过成片对比。每档补 `basis`「按参考作品的公认风格人工设定（2026-09-08），未做逐镜分析」+ `confidence`「低」；`refs` 在库的说明和文档里改称「风格参考」。`legacy_profiles` 补 `basis`「旧版本沿用的档位：白名单与上限为兼容已有项目保留，未做逐镜标定」+ `confidence`「中」。库 JSON 就是数据源本身（直接改，或在「镜头库与风格库」页里改，写盘前过 `config_validate`），`tests/test_camera_profiles.py` 逐档核对 `basis` / `confidence`。
- **说过头的地方，已更正**：镜头库与风格库的说明文字、库 `_usage`，以及本文 §35，都曾把 `refs` 里的作品说成「蒸馏来源」「验证过的作品」。都改成「按公认风格人工设定、未做逐镜分析」；§35 原文保留，只在句内更正并注明日期。
- **缺口：没有任何东西检查题材和镜头风格配不配。** 补一层**提示**（不是约束）：`skills/ai-short-film/scripts/genre_camera_pairing.json`，`_meta.basis`「本仓库人工设定（2026-09-11），不是来源蒸馏」（`why` 里有几句引自题材卡原文，MIT，见 `docs/third-party-notices.md`），按题材卡 id 给 `recommended` / `avoid` / `why`。依据是题材卡自己的 core / pressure / grains / production_notes 与各档 profile 的 why。

| 题材 | 常配（recommended） | 会打架（avoid） |
|---|---|---|
| 亲子隐秘 | short_drama_vertical、tv_drama | anime_action |
| 仙侠修真 | anime_action、classic_film | — |
| 动作任务 | anime_action、classic_film | — |
| 古装权谋 | tv_drama、classic_film | — |
| 复仇打脸 | short_drama_vertical | — |
| 家庭关系 | tv_drama、short_drama_vertical | anime_action |
| 悬疑规则 | suspense_thriller | — |
| 生活流 | tv_drama、classic_film | short_drama_vertical、anime_action（卡里明说「不用密集大特写和群体震惊提速」） |
| 破镜重圆 | tv_drama、short_drama_vertical | — |
| 职场喜剧 | short_drama_vertical、tv_drama | suspense_thriller |
| 豪门婚恋 | short_drama_vertical、tv_drama | — |
| 身份错位 | short_drama_vertical、tv_drama | anime_action（环绕 / 包抄 / 甩镜是转身换脸高发区，这个题材要一直分得清谁是谁） |

  - **lint**：`storyvideo/pairing.py` 读表；`lint.check_genre_camera` 接进 `check_all`——`spec.genre`（另一条改动正在把它存进 spec；没有、认不出就什么都不做）经 `find_genre` 认出、且 `camera_profile` 在该题材的 avoid 里 → 一条「全集：题材「X」配镜头风格「Y」容易打架：理由——换成 常配档，或确认这就是想要的反差」软告警；推荐的、中性的组合都不说。问题卡新分类 `pairing`（「搭配」），排在「按题材审稿」之后、其余分类之前（理由里有「运镜」「表演」这些别的分类的词）。英文在 `i18n_rules._PAIRING_WHY`：每张卡的理由一条英文，整句 + 问题卡两半；测试对 12 张卡逐条核对，改了 why 忘了改英文就挂。
  - **接口**：`GET /api/scripts/genres` 每张卡多 `confidence` / `basis` / `source_file` / `pairing`（页面不用再多发请求）；`GET /api/camera-options` 多 `profile_details`（`english` / `refs` / `basis` / `confidence`，带 `profile=` 查旧档时也带它自己的）。
  - **界面**：新建项目页镜头风格下一行「按《…》等作品的公认风格人工设定 · 置信度 低 · 未做逐镜分析」，题材核心句下一行「出处：drama-skills 题材卡 · 置信度 高 · 例子为合成虚构，非数据统计」（悬停看 basis 与原卡文件名）；两者都选了时：常配 → 「这个题材常配这档镜头风格」，会打架 → 「这个组合容易打架：理由」+ 可点的常配档，其余不出声。镜头库页 profile 表多一列「出处 / 置信度」。
  - 出处字段和搭配都**不进提示词**（`refresh_prompt_canary.py`：no drift）。桌面包带上 `genre_camera_pairing.json`。
- **验收**：`tests/test_genre_camera_pairing.py`（表完整性、lint 命中 / 不命中 / 没有 genre 不动作、问题卡分类、12 张卡的英文）、`tests/test_genre_cards.py`（出处字段、接口字段、出处不进提示词）、`tests/test_camera_profiles.py`（每档 basis / confidence、接口 profile_details）、`tests/test_webui_i18n.py`、`tests/test_packaging_files.py`、`frontend/tests/new-project.test.js`（出处行、三种搭配状态）。后端 1484 passed，前端 215 passed。
- **未验证**：搭配表是判断不是测量，没有任何成片对比；五档 profile 的「低」是诚实标注——要升级得对参考作品逐镜分析，或在本仓库做成片对比。

## 47. 侧栏只管导航（2026-09-10）

侧栏里每个模块展开都有一个新建入口，其实用不着。原来每个模块块里都是「+ 新建 + 搜索框 + 列表」，一个空模块展开后是一个大绿按钮加一个搜索框加一句「还没有」——按钮比内容还多，而且模块首页上本来就有更显眼的新建。

现在的分工：**侧栏只放导航**（模块名 → 模块首页、镜头库这类页面链接、项目列表、每行的删除），**创建是动作，只在模块首页上**。另外搜索框到 8 条（`SEARCH_MIN`）才出现——列表短的时候它只是噪音；空态从「点上面新建」改成一行「还没有 · 去模块首页新建 →」的链接。

验收：前端 205 条全绿（五个模块的侧栏用例改成断言「新建链接不在侧栏里」+「短列表没有搜索框」，搜索行为在 `components.test.js` 里补满 8 条单测）。

## 46. 启动器的端口处理（2026-09-10）

`--port <端口>` 起服务时，那个端口可能正被另一个程序占着。老实现有两个坑：

1. **端口被占没人报**——`_serve` 在守护线程里跑 uvicorn，绑定失败（WinError 10048）的异常死在线程里，主线程只会干等三十秒然后说一句英文的 "server did not come up"。
2. **探活只看「有没有回应」**——`_wait_ready` 拿 `/` 探活，任何 <500 都算起来了。占端口的程序只要对 `/` 返回 200，启动器就会把**别人的界面**当成自己的打开。

现在：

- `port_in_use(port)` 用连接探测（不是 bind 试探——Windows 上 `SO_EXCLUSIVEADDRUSE` 会让 bind 失败但连接成功，两者都算被占）。显式 `--port` 且端口被占 → **立刻**退出码 2，并说清是谁占的（`port_owner` 走 netstat + tasklist，拿不到就省略）和换哪个端口。
- `_probe_identity` 探 `/api/health` 并检查响应里有没有 `"storystudio"`：`ok` / `foreign`（端口上是别的程序）/ `""`（还没起来）。
- `_serve` 把 uvicorn 的异常记进 `_SERVE_ERROR`，`_wait_ready` 看到就立刻返回 `dead` 并把原话摆出来，不再等满超时。
- 四种失败各有一句中文原因，都带「换成 --port <当前空闲端口>」的建议。

验收：`tests/test_desktop_launcher.py` 6 条（连接探测、拿不到占用者不抛异常、四种探活状态、线程异常被记下、显式端口被占直接退出且不起线程）。真机：端口被占时立刻报「端口 <端口> 已经有别的程序在听（python.exe (PID <pid>)）——换一个端口：--port <空闲端口>」；换到空闲端口正常起来，`/api/health` 200。

## 45. 本地模型（2026-09-10）

需求：有显卡的用户可能在本机部署模型，设置里要能配置本地模型。契约与偏差见 `docs/local-models-spec.md`（§8 记实现与契约的偏差）。

本地来源跟云端来源的差别只有三件事，其余（角色配置、能力表、任务、账本、重投纪律）**全部照旧**——不另起一套运行时：

**1. 不要 key。** `RoleConfig.local` 由 `base_url` 推导（`genkit/local.py::is_local_url`），不新增字段（多一个字段就多一处不同步）。`validate_role` 在有效地址（补完适配器默认地址之后）是本机时跳过 `api_key` 必填，所以 Ollama / ComfyUI 这类来源也能算 `configured`，`roleOk()` 与能力检查不再拦人；别的字段照样必填。openai SDK 构造客户端要求 key 非空，`local.api_key_for()` 在本机地址下补一个**显式的占位值 `"local"`**（不是凭据，只为过 SDK 的非空校验），远端留空仍是 `"missing"`——那是真配错了，该让服务商报 401。`GET /api/settings` 的角色里多一个推导出来的 `local`（只在接口视图里给，不写盘）。

**2. 不花钱。** `local.free_cost_note(n)` = 「本地生成，不花钱（N 次请求）」，`local.cost_note_for(role, n, 远端那句)` 是统一入口：图片集 / 单条视频 / 配乐 / 绘本 / 海报的 `cost_note` 与 `webui/generate.estimate_plan`（多返回 `local` 与 `cost_note`）都走它，本地时不报模型名当"要花的钱"。能力表里 `sdwebui` / `comfyui` 的记录带 `local: True` + `cost: "free"`；`capabilities.is_local(adapter, base_url)` 让**任何指到本机的来源**（本地语音 = `openai_speech` 指到 127.0.0.1，本地文本 = `openai_chat` 指到 Ollama）也带上这两项。成本闸 `scriptgen_max_budget_usd()` 在文本角色是本地时返回空串（不设上限）。

**3. 要能被发现。** `genkit/local.py` 七个预设（`ollama` / `lmstudio` / `vllm` / `llamacpp` / `comfyui` / `sdwebui` / `openai_speech_local`），每条写清 `base_url`（给适配器）、`probe`（探活）、`models_probe`（取模型列表，只有 ComfyUI 跟探活地址不同）、`kind`（四种列表形状：Ollama `/api/tags`、OpenAI `/v1/models`、ComfyUI `object_info`、SD WebUI `sd-models`）、`adapter`（应用到角色时填哪个适配器）。`scan()` 并发探一遍，一个服务探失败只写进它自己那条 `error`，不影响其余；活着但列不出模型仍算 `alive`。

**只连本机是硬约束。** 预设地址写死 localhost，用户手填的地址一律过 `is_local_url`（`localhost`、`127.0.0.0/8`、`::1`、`10/8`、`172.16/12`、`192.168/16`、`169.254/16`、`fc00::/7`、`fe80::/10`），**主机名不是 IP 也不是 localhost 的一律拒绝**——`http://127.0.0.1.evil.com`、`http://127.0.0.1@evil.com` 这类"长得像本机"的公网地址在发请求之前就被挡掉。这条不是洁癖：把「扫描本机」做成一个能被诱导的内网扫描器，代价比功能大。

**接口** `webui/routers/local_models.py`：`GET /api/system/local-scan`（`?ids=` 只扫某几个、`?base_url=` 手填地址；只读，不写设置）、`POST /api/settings/local/apply` `{service, role, model?, base_url?}`（写 adapter / base_url / model，`api_key` 清空——留着旧密钥反而会被发到本机服务上去）、`GET /api/system/local-workflows`、`POST /api/system/local-workflows/check` `{name}`。后两条转给 `genkit.adapters.comfyui` 的映射加载器，**懒导入**：适配器不在时返回一句人话而不是 500。

**前端**：设置页顶部「本地模型」卡——扫描按钮 + 结果表（服务 / 地址 / 状态 / 模型下拉 / 每个角色一个「用它配 …」按钮）+ 没扫到时的手填地址行 + ComfyUI 的工作流下拉与「检查映射」；角色卡的 key 输入框在本机地址下显示「本机服务通常不需要，可以留空」（`frontend/src/local.js::isLocalUrl` 跟后端同一套判据）。

**推荐而不强制（2026-09-10 追加）。** 需求：在用得上的时候推荐本地模型，但不强制。本地永远只是一条**可选**的路——什么都不做，云端来源照常能用；这一层只负责在真有用的那三个位置各说**一行**。

*接口*：`GET /api/system/local-hint?role=<角色>` → `{available, current_is_local, suggestion|null, dismissed, scanned}`。页面加载就会问，所以它必须便宜：探活结果**按服务**在内存里缓存 60 秒（`HINT_TTL`），窗口内一个请求都不发；没有缓存时也只**快探**（0.5 秒超时）能顶这个角色的那几个预设，不扫全部七个。设置页的「扫描本机」那一次也写进同一份缓存（扫过就不用再探）。地址仍然只有写死的预设，接口不收手填地址——提示不能变成探测器。

*点名规则*：生图先看 SD WebUI（接口固定、装好就能用），ComfyUI 只在它是**唯一**在跑的那个、或者角色是**视频**（SD WebUI 不出视频）时才被点名；文本按预设顺序取第一个在跑的（ollama → lmstudio → vllm → llamacpp）；语音是本地 `/v1/audio/speech`；配乐没有本机预设，永远不推荐。规则表在 `local.ROLE_PREFERENCE`，理由一行在 `local.RECOMMEND_REASON`。

*三个位置，各一行，都能关*：① **角色还没配好**——引导第一步与各处「还没配置 X 模型」的旁边（`components/LocalTip.vue`）：「有显卡的话可以本地跑，不花钱、不出网 → 扫描本机」。只在**真探到**或**用户从来没扫过**时出现；没扫过时文案不声称本机有什么在跑。② **花钱之前**——预估/确认弹窗（`ModalHost.vue`，`ui.confirm/custom` 多一个 `localRole`）：「本机有 {名字} 在跑，切过去这次不花钱」+「去切换」+「不再提示」。请求是**异步**发的，拿不到就当没有：没有 suggestion 时弹窗与以前逐字节相同，按钮一个不改，也从不拦下这次操作。③ **设置页的本地模型卡**——被点名的那个排在前面、标「推荐」并给一行理由；什么都没探到时**只有一行**，不放安装教程、不放下载链接。

*关掉就不再回来*：`POST /api/system/local-hint/dismiss {role|"all"}` 写进设置文件的 `local_hints_dismissed`（不是浏览器状态：换浏览器、重启程序都还在），关过的角色 `suggestion` 永远是 null。唯一的复位入口是本地模型卡里的「重新开启本地提示」（`POST /api/system/local-hint/reset`）。

*文案纪律*：任何一处都不许读成「先装好 X 才能用」。卡片开头那句改成「有显卡的话可以全程本地跑……这是一条可选的路，云端来源什么都不改也照常能用」；`sdwebui` 适配器提示里的「WebUI 必须带 --api 启动」改成「接口在 WebUI 带 --api 启动时才开」；ComfyUI 适配器缺失那句里的「先装上它」改成「装上它之后会自动出现」。选它需要什么，就陈述需要什么，不发号施令。

**验收**：`tests/test_local_models.py`（48 条：预设形状、地址判定的放行与拒绝、四种列表形状、假 fetch 下的 alive/dead/timeout/部分失败、应用到角色、空密钥放行、文案切换、成本闸、四个接口含公网地址 400）、`tests/test_local_hint.py`（18 条：点名规则、缓存与 TTL、只探本机、关掉后不再出现且重启还在、两个接口形状）、`frontend/tests/local-models.test.js`（7 条）、`frontend/tests/local-hint.test.js`（12 条：三处提示各自出现/不出现、关掉、没有 suggestion 时弹窗不变）、`tests/test_webui_i18n.py` 英文覆盖。**未验证**：没有在真实的显卡和这些服务上跑过，扫描与两个本地适配器全部只有假 HTTP 测试（见 `docs/local-models-spec.md` §7）。

## 44. 配乐模块（2026-09-10）

需求（M8 第二阶段的清单）：「配乐 `music`：按时间线意图生成，落到项目的配乐层」。短剧的后期层早就有 `post_graphics.music`、绘本早就有 `music.file`，缺的只是**那条音频从哪来**——以前只能自己找素材上传。所以这个模块的产品不是"一个音频文件"，而是**一次投放**：按意图出几条候选 → 试听挑一条 → 一键放进别的项目的配乐层。契约与偏差见 `docs/music-spec.md`（§7 记实现与契约的偏差）。

**第五个角色 `music`。** `genkit.settings.ROLES` 从四个（text / image / voice / video）变五个。`MusicRequest`（`name / prompt / model / duration / count / reference_asset_ids / extra_params`）与 `TaskType.MUSIC` 进 `providers.py`，`submit_music` 上 `Provider` 协议；**不会出音乐的适配器抛 `ProviderUnsupported`**（新异常，挂在 `Adapter` 基类上兜底，所以生图/视频适配器不用各写一遍）。统一网关不覆盖这一档——没有 OpenAI 兼容的音乐接口形状，`GATEWAY_ADAPTERS` 里没有它，一键应用会跳过。本机模型也没有配乐预设。

**音乐来源**：实现 `submit_music` 的适配器都能用。目前没有内置的音乐来源——没有通用的接口形状——由插件登记（§57）；这个角色一个可选来源都没有时，生成接口在起任务之前拒绝，并说明「内置来源不覆盖这一档，要装一个提供它的插件」。收不收参考图、最多几张、一次最多几条，由来源登记的能力决定，参数怎么映射由各适配器决定；**时长写进提示词**（有的音乐接口没有时长参数，对不认识的参数还会直接拒绝），`MusicRequest.duration` 也带着，用不用由适配器定。异步来源按 `<前缀>:<任务 id>` 轮询，跟生图 / 视频同一套。

**项目文件** `content/music/<ID>.json`（`src/music/spec.py`，id `MU-<日期>-<n>`）：`brief`（`usage` 一句话用途 / `mood` / `genre` / `instruments` / `bpm` / `structure` / `avoid`）+ `duration` + `count`（一次出几条，上限由来源的能力决定）+ `reference_images`（可空；支不支持、最多几张由来源的能力决定）+ `music_params` + `attempts[]` + `current` + `used_in[]`。情绪 / 乐器 / 避免这三个列表字段也认「顿号分隔的一行」——表单里人常常直接打一行。

**提示词** `src/music/prompts.py`：`用途 → 体裁 → 情绪 → 乐器 → 速度 → 时长 → 结构 → 固定尾句（纯器乐、无人声、可循环、成品混音）→ 不要：…`，中英各一份按 `prompt_language` 取。`has_intent()` 判断 brief 里有没有真说明"要什么音乐"——只填时长、只剩固定尾句的不让提交（那是一句谁都能写的废话，不该拿去花钱）。**预设 brief** `skills/ai-music/scripts/music_briefs.json`（10 张：睡前摇篮曲 / 史诗战鼓 / 都市轻快 / 悬疑低频 / 温情钢琴 / 国风古筝 / 赛博电子 / 纪录片弦乐 / 喜剧诙谐 / 时尚节拍），形状对齐 `brief` 字段，选了整份填进表单再改。

**出配乐** `src/music/generate.py`：一轮出 N 条，**逐条**提交（每次请求 `count=1`），一条失败不拖累其余；请求哈希（提示词 / 模型 / 时长 + 参考图内容摘要）不变且已有采用的整轮跳过；从不覆盖（`01_raw/<ID>-<n>.<ext>`，扩展名跟着结果地址走，一般 `.wav` 或 `.mp3`）；异步来源在任务内轮询，单条最多等 20 分钟。

**投放（这个模块存在的理由）** `src/music/use.py`：`POST /api/music/{id}/use {module, target, slot?}`。短剧那条**走 `webui/routers/post.py` 的 `put_post_layers`**——后期块校验、冻结后的 amend 都在那条路上，不自己写 spec 文件；绘本那条走 `picturebook.spec.save_book` 的归一化。两条都是先把音频**拷进**目标项目的 `content/assets/<目标>/music/`（文件名带内容摘要，重复投放不堆副本），再写进配乐层。拷贝而不是引用：之后删掉配乐项目，别人的成片照常能出。成功后往 `used_in` 追一条，返回里带目标项目的名字，前端提示「已放进《…》的配乐层」。

**前端** `/music`：首页（列表 + 新建 + 复制示例）、新建（标题、预设下拉、用途、情绪 / 乐器 / 避免 chips、BPM、结构、时长、条数）、项目页两个子页——**生成**（brief 表单 + 编译出的提示词预览 + 生成按钮写清 N 条请求 + 历次逐条 `<audio>` 播放与「采用」+ 「放进项目」下拉（目标 + 起始秒，只有短剧有起始秒））与**高级**（JSON、参考图、归档）。侧栏多一块手风琴。

**验收**：1283 passed、vitest 167、金丝雀无漂移。**未验证**：没有用真实音乐来源提交过，全部用替身来源测试覆盖。

## 43. 单条视频模块（2026-09-10）

需求（M8 第二阶段的清单）：「单条视频 `clip`：一条提示词一段视频」。判据是 `docs/prompt-patterns.md` 里的 P1–P3；15–30 秒一条生成到底的提示词按三段写：全片不变的设定、按绝对时间分段的时间线、衔接与收尾。短剧那条产线（4–10 秒逐镜 + 账本 + QC + 拼接）解决的是另一个问题，所以这里**不拆镜、不进账本**，一个项目就是一条片子。契约与偏差见 `docs/clip-spec.md`。

**项目文件** `content/clip/<ID>.json`（`src/clip/spec.py`，id `CL-<日期>-<n>`）：`global`（look / palette / light / aspect_ratio / safe_area / text_policy / audio）+ `beats[]`（绝对时间 `start`–`end`，景别 / 画面 / 声音 / 画面文字）+ `continuity`（one_take / transitions / last_frame / forbid）+ `video_params` + `first_frame` + `attempts[]` + `current`。归一化只管形状（按 `start` 排序、`end > start`、id 不重复），**时间轴的空洞 / 重叠 / 与总时长对不上是告警不是错误**——写到一半的项目也要能打开。分辨率按 `genkit.capabilities` 对齐到模型支持的档位（720p → MiniMax 的 768P）。

**三段式编译** `src/clip/prompts.py`：

```
【全片设定】<时长与分辨率句>。<风格卡锚句>。<look>。色板：雾蓝、青灰、米白、…。<light>。<画幅句：画幅 + 排除另一种画幅>；<safe_area>。<text_policy>。<audio>。
【分段时间线】0–3 秒：<景别>，<画面>；声音：<声音>；画面文字："GOOD MORNING"。（一拍一行）
【衔接与收尾】<一镜到底句>。<transitions>。最后一帧：<last_frame>。禁止：…。
```

P1（时长 + 画幅显式 + **排除另一种画幅** + 安全区）写在同一句里，画幅只出现一次——尾部再拼一句画幅很容易跟前面自相矛盾，这是长提示词最贵的一类疏漏。P2 的色板、P3 的画面文字加引号也在这一段里落地。中英两套；方言只用 `generic` 与 `nano_banana`（后者剥掉数字镜头参数、结尾单独一行 `Aspect ratio: 16:9`），`gpt_image` 那套五段带标签是给出图的、视频这边没有对应判据，一律折回 `generic`。

**检查** `src/clip/lint.py`：空洞 / 重叠 / 与总时长差超过 1 秒 / 色板不足 5 个色名 / 节拍里出现「然后 接着」这类连接词（一拍只做一件事）/ 没写最后一帧 / 画面文字与文字政策自相矛盾，再加 `imagecraft.lint` 的通用项。告警形状 `beat B02: 影响——怎么做` / `全片：…`，`webui.problems` 多认一个 `beat` 前缀与 `clip` 分类。

**出片** `src/clip/generate.py`：`first_frame.mode` 三选一（`none` 文生视频 / `upload` 用上传的图 / `generate` 先出一张首帧再图生视频，**一次生图请求**）；一次请求 = 一条片子，落 `output/clip/<ID>/01_video/<ID>-<n>.mp4`，**从不覆盖**；请求哈希（提示词 / 模型 / 时长 / 画幅 / 分辨率 / 音频 + 首帧内容摘要）不变且文件在就跳过，`force` 重出；失败只是一条记了原因的 attempt，`current` 还指着上一条能播的片子。**断点续等**沿用 §34 的做法但不走账本：提交拿到 task_id 就先写盘，`resume_clip_watchers()` 在服务启动时扫 `content/clip/`，把「有 task_id、没错误、文件还没落盘」的那次接回来接着轮询（只轮询 + 下载，从不提交，所以起服务这一下不会花钱）。

**接口** `/api/clip/...`：列表 / 编号 / 新建 / 示例 / 读写删归档 / `lint` / `first-frame`（上传同步、生成走任务 `clip-frame`）/ `generate`（任务 `clip-generate`，返回 `cost_note`）/ `pick` / `artifact/video|frame/{name}`（路径安全：只放行 `attempts` 里记过的文件名）。任务 `module="clip"`。

**前端** `/clip`：首页（列表 + 新建 + 复制示例）、新建、片子页三个子页——**写法**（左边三段式编辑器：【全片设定】表单 + 【分段时间线】节拍表（时间条 + 增删排序 + 「贴住上一拍」）+ 【衔接与收尾】表单，右边编译出来的提示词与问题卡；整条提示词由后端编译、保存后刷新，每一拍那一行在本地实时算）、**出片**（首帧三种模式、确认花钱、`pollTask` 进度、历次片子 `<video>` 播放与「采用」、下载）、**高级**（JSON + 归档）。侧栏手风琴多一块。

**验收**：`tests/test_clip_{spec,prompts,lint,generate,router}.py`（88 条：形状与归档、三段与中英与方言、每条规则命中与不命中、假来源的同步 / 异步 / 失败隔离 / 从不覆盖 / 哈希跳过 / force / 三种首帧模式 / 断点续等、接口全套与路径安全）、`frontend/tests/clip.test.js`（14 条）、`examples/clip/` 的示例零告警。**未验证**：没有对着真实视频模型跑过——这套写法的判据来自对长单镜视频提示词常见写法的总结，不是我们自己跑出来的。

## 42. 图片工作台（2026-09-10）

需求（M8 第二阶段的清单）：「图片工作台 `image`：单张 / 成组出图、多模型对比、海报叠字」。**没有新引擎**——它是 `src/imageset/` 的第三个模块（`MODULES = ("sticker", "portrait", "image")`，编号前缀 `IM`），只加自己的模板包，于是列表 / 出图 / 挑选 / 从不覆盖 / 后处理 / 打包 / 后台任务 / 产物流 / 归档、以及三个前缀共用的那一套 `make_router(module)` 全部照用。契约与偏差见 `docs/imagestudio-spec.md`。

**三个模板**（`templates.py`，`module="image"`，风格预设 `IMAGE_STYLES` 四条 + 命中 `fits` 的风格卡）：

| 模板 | expand | post | export |
|---|---|---|---|
| `freeform` 自由出图 | `brief` 一句话 + `variants`（1–8）+ `aspect`（1:1 / 3:4 / 4:3 / 9:16 / 16:9）+ `negative`：N 个 item 同一句提示词、同一画幅，`meta.variant` 记序号 | 按画幅居中裁一版 + 长边 2048 一版 | `images/{item}.png` + `images/2048/{item}.png` |
| `model_compare` 多模型对比 | `models` 每个模型一个 item，`meta.model` 覆盖模型；提示词只有一句，差别只来自模型 | 同上 | `compare/{模型名}.png`（标签压成文件名） |
| `text_card` 图卡 | `brief` / `title` / `subtitle` / `layout`（auto·上·下）/ `variants`（1–4），默认 3:4；提示词给叠字那一端留干净 | 复用 `storyvideo.poster.render_poster` 叠字 | `cards/{item}.png` |

**主体可以整个留空**：`prompts.has_subject()` / `subject_block()`——名字、身份块、参考图都空时提示词里既没有身份锁也没有排他锁（自由出图常常只写画面）；填了主体就跟表情 / 写真走同一套身份块。

**引擎只为它开一个口子**：`generate.request_data()` 的模型改成 `item_model(spec, item)`——`item.meta.model` 非空就用它，否则还是 `image_params.model`。别的模板不写这个键，行为逐字不变（有测试盯着）。`cost_note` 照旧按 item 数算。

**叠字复用海报**：`render_poster` 多一个 `canvas_size` 参数（默认还是 1080×1920），内部的裁切 / 忙碌度 / 压暗层改成按画布尺寸算；`imageset.post.render_card()` 按画幅给出画布（短边 1080，3:4 → 1080×1440）并把 `PosterError` 翻成 `PostError`，一张失败不拖累整批。海报那边一个字没改，输出不变。

**接线**：模块注册表 `image` 改 `ready`（`content/image`、`/image`、`project_kinds=("image",)`）；`EXAMPLE_DEFAULTS["image"] = "lantern_night"`，示例 `examples/image/lantern_night.json`（4 张候选 + 一张风格卡，随包）；风格卡库每张卡的 `fits` 加上 `image.freeform`，另按分类挑了 8 张进 `image.model_compare`、11 张进 `image.text_card`。

**前端**：`IMAGESET_MODULES` 加 `image`（🖼 / `IM` / 主体可空），路由 `/image`、`/image/new`、`/image/s/:id/{items,outputs,advanced}`，侧栏手风琴多一块；`SetNew.vue` 把 `models` 渲染成多选（候选 = 已选 + `@image` + 设置里的生图模型 + `/api/system/model-catalog` 的生图模型，也能手写加一个），主体卡写明可留空、不再催参考图；`SetItems.vue` 每行显示这一张用的模型。其余页面（清单 / 产物 / 高级 / 外壳）一行没改。

**验收**：`tests/test_image_studio.py`（25 条：三个模板的展开与选项、主体为空的提示词、item 级模型覆盖进请求且别的模板不受影响、3:4 图卡真把标题画上去、三种包内布局、`/api/image` 的 CRUD 与示例）+ 既有 imageset 全套跑通，`frontend/tests/imagestudio.test.js`（9 条）。**未验证**：真实模型上的候选质量与多模型对比（没有 key）；`text_card` 的叠字是海报那段代码，已在海报那边真机验证过。

**2026-09-10 模型候选来自这台机器**：多模型对比原来给的候选是模板里写死的三个 id（`docs/imagestudio-spec.md` §6 第 5 条记的那个偏差——模板注册是静态数据，读不到设置）。现在后端多一个 `webui.routers.imageset.model_candidates(role="image")`，把候选算出来跟 `GET /api/{m}/templates` 与项目详情一起下发（顶层 `model_candidates`，`[{id, label, source}]`）：顺序 **`configured`（`@image` 别名 + 设置里那个模型的真实 id，永远最前）→ `catalog`（模型目录缓存里出图的 id）→ `local`（最近一次本机扫描里 SD WebUI / ComfyUI 报的模型名）**，同一个 id 先到先得去重，`@image` 只出现一次。**只读缓存，一个请求都不发**——目录要用户点过「刷新模型目录」才有，本机那段要扫过才有（`local_models.cached_services()` 直接读探活缓存，不看 TTL、不补探），设置文件坏了当没配；三段都空时退回模板那份静态默认清单（source `default`），新装的机器上照样能展开。前端 `SetNew.vue` 的多选按来源分组（已配置 / 模型目录 / 本机 / 模板默认），候选里没有的 id 手写加进来落进「手写」一组，发出去的请求体一个字没变。

## 41. 出图风格卡与图像方言（2026-09-09）

需求：出图的几个模块要有一套固定的风格，或者几种可选的风格。原来每个模板各写三四个风格预设，字符串散在 `templates.py` 里，绘本另有一套，海报没有。现在收成**一套共享卡库 + 一层模型方言**，形状沿用短剧那套（题材卡 §26、形态卡 §31、提示词方言 §17）：取判据，不取语法。契约与偏差见 `docs/image-style-cards.md`。

**卡库** `skills/ai-image/scripts/style_cards.json`（24 张、8 类）：人像写真 / 插画 / 角色与表情 / 产品 / 时尚 / 美食 / 海报与平面 / 界面与社交。每张卡带 `anchor_zh`/`anchor_en`（进提示词的那句话）、`lighting`、`palette`（5–7 个具体色名，判据来自 `docs/prompt-patterns.md` P2）、`materials`、`camera`（只写观感，**不写数字焦段光圈**——这类数字换不来对应的画面，`nano_banana` 方言还会把它们删掉）、`constraints`、`fits`（`<module>.<template>` / `picturebook` / `poster`，只用于筛选不做硬限制）、`model_hint`。卡的文字是本仓库按常见的摄影布光、插画技法与版式常识自写的，出处写在卡库的 `_meta.basis`。

**方言** `src/imagecraft/dialects.py`：`generic`（默认，老项目逐字节不变）/ `gpt_image`（五行带标签 Scene / Subject / Key details / Intended use / Constraints，删掉 `stunning` / `masterpiece` / `8k` 一类只夸不描述的词）/ `nano_banana`（自然语言段落，删掉数字镜头参数，最后单独一行 `Aspect ratio: <画幅>`）。`resolve_image_dialect(model, override)`：显式覆盖 > 按模型 id 猜 > `generic`。

**检查** `src/imagecraft/lint.py`：只夸不描述的空话词（gpt_image）、数字镜头参数（nano_banana）、反着写的否定表述（NO_TEXT 尾句是既定例外）、要出现的画面文字没加引号、卡的色板不足 5 个具体色名（提示不是错误）。告警形状与其他 lint 一致，进问题卡。

**接线**：图片集 `style` 除模板预设与 `custom` 外接受卡 id，`image_dialect` 可选；绘本 `style_card` 可选；海报请求 `style_card` 可选；`GET /api/style-cards`（`?category=` / `?fits=`）与 `GET /api/style-cards/{id}`；`GET /api/{m}/templates` 每个模板多返回命中 `fits` 的 `style_cards`。三处都保证**不写就零变化**。

**前端**：`components/StyleCardPicker.vue` 分类分组的卡片网格（名字 + 一句话 + 色板小色块 + 「适合」标签 + 「只看适合本模板的」开关），选中显示锚句全文与「改成自写」；共享缓存 `src/stylecards.js` 只拉一次卡库，并把四条方言说明摆出来。接线四处：图片集新建页、清单页顶部风格行（换风格提示要重出）、绘本「角色与风格」页、成片页海报卡。

**验收**：1028 passed（+57）、vitest 122、金丝雀无漂移；`generic` 方言对四个面（表情 / 写真 / 绘本 / 海报）的输出与改动前逐字节一致（快照 `tests/fixtures/image_prompts_generic.json`）。**未验证**：这些卡与方言对真实出图模型的实际效果——没有 key 跑过；方言写法是本仓库按对这几类模型的一般认识定的，未对真实模型做过对比。

## 40. 图片集引擎与「表情与头像」「纪念写真」（2026-09-09）

两条新产线：表情包 / 贴图 / 头像放一块，满月 / 百天 / 周岁 / 婚纱照放一块。拆开看它们是两个模块，做起来是同一件事——**按模板展开一组图 → 逐张出图 → 挑选 → 本机后处理 → 打包**。所以只写一个引擎 `src/imageset/`，两个模块是它的**模板包 + 路由前缀 + 页面命名空间**；模块注册表 `sticker` / `portrait` 两条改 `ready`（`content/sticker`、`content/portrait`）。契约在 `docs/imageset-spec.md`（§6 记实现与契约的偏差）。

**为什么是一个引擎 + 模板包，不是两个模块。** 两条产线不一样的只有三件事：出什么清单（表情 / 场景）、提示词里的固定约束（白底无影 / 婴儿安全）、产物怎么切怎么摆（微信表情规格 / 相册 3:4）。这三件事全部收进 `Template`：

```
Template(id, module, name, english, description, styles[], options_schema,
         expand(spec) -> items[], post(spec, item, path, ctx) -> outputs[],
         post_extra(spec, items, ctx) -> outputs[], export_layout, scene_table)
```

`expand` 只产出每张图的 label / prompt / negative / aspect_ratio / meta，**不调模型**（展开是免费的，出图才花钱）；`post` / `post_extra` 是 Pillow 后处理；`export_layout` 是 `role → 包内路径`（`{item}` = 图片 id）。`options_schema` 是前端能直接渲染的小表单描述（`{key, label, label_en, type: list|bool|string|int, default, choices?, min?, max?, hint?}`），所以加一个模板 = 加一条注册，前后端都不用改。接口层因此也只有一套：`routers/imageset.py` 的 `make_router(module)` 造出 `/api/sticker/...` 与 `/api/portrait/...` 两份同形状的路由，`module` 由前缀决定，文件里写别的模块就拒绝。

**项目文件。** `content/<module>/<ID>.json`（`ST-20260909-1` / `PT-20260909-1`，按天编号），归一化后多两个运行时键 `output_root` / `project_root`，写盘时去掉：

```json
{"id": "ST-20260909-1", "module": "sticker", "title": "小满表情包", "template": "emoji_pack",
 "subject": {"kind": "character|photo", "name": "小满", "block": "外形描述",
             "assets": ["content/assets/<ID>/ref1.png", {"file": "…", "who": "bride"}], "fidelity": 85},
 "style": "flat_cute", "style_anchor": "", "prompt_language": "zh",
 "options": {"expressions": ["开心", "哭"], "with_caption": true},
 "image_params": {"model": "@image", "image_size": "2K"},
 "items": [{"id": "I01", "label": "开心", "prompt": "…", "negative": "…", "aspect_ratio": "1:1",
            "meta": {}, "status": "todo", "current": null, "request_hash": null, "error": null, "attempts": []}],
 "picks": ["I01"]}
```

`subject.kind` 两种：`character`（文字身份块 + 可选定妆图，表情与头像用）与 `photo`（真人参考照 + 相似度百分比，纪念写真用）；参考图最多 6 张，婚纱照每张可标 `who`（新娘 / 新郎）。`items` 是引擎按模板展开的清单，也是产物清单——`prompt` 展开后可以在前端手改再 `PUT` 回来，出图只认 `items[].prompt`。

**从不覆盖 + `current` 指针。** 每次出图写 `output/<module>/<ID>/01_raw/<item>-<n>.png`（`n` 递增，同名文件已存在就顺延），追加一条 `attempts[] = {file, created_at, model, request_hash, task_id, error}`，`current` 指向采用的那张。请求哈希（提示词 / 负面 / 模型 / 画幅 / 尺寸 + 参考图**内容**摘要）不变且 `current` 文件还在 → 跳过；`force` 才重出。改了提示词但图还是旧请求出的，接口层报 `stale`。挑选是两步：`pick {item, attempt}` 采用某一次（把 `current` 拨回去，历次都留着），`pick {picks: […]}` 决定哪些进后处理与打包（空 = 全部）。单张失败不拖累整批：失败也写一条 attempt（带 `error`）并逐张原子写盘，任务中途挂了不丢已出的图。异步来源在任务内轮询，单张最多等 20 分钟。

**七个模板。**

| 模块 | 模板 | 展开 | 固定约束 |
|---|---|---|---|
| sticker | `emoji_pack` 表情包 | `expressions[]`（默认 16 个）每个一张 1:1 | 同一角色锁 + 纯白底 #FFFFFF、留 12% 边、主体外无投影 |
| sticker | `sticker_set` 贴图 | `themes[]`（默认 8 个）× `shape`（圆 / 方 / 异形） | 同上 + 贴纸白描边 |
| sticker | `avatar_set` 头像 | `styles[]`（默认 9 种画风）× `crop`（头肩 / 半身） | 单人头像、视线看镜头、主体占七成 |
| portrait | `newborn_30` 满月 | `scenes[]`（默认 6）× `count_per_scene` | 柔光棚拍、婴儿安睡、道具离脸一掌以上 + 婴儿安全句 |
| portrait | `baby_100` 百天 | 同上（默认 6 场景） | 蛋糕与道具无火焰 + 婴儿安全句 |
| portrait | `first_birthday` 周岁 | 同上（默认 6 场景） | 宝宝坐稳或有人扶、无火焰 + 婴儿安全句 |
| portrait | `wedding` 婚纱 | 同上（默认 8 场景） | 面料质感、两人逐人点名 + 排他锁 |

风格预设各模板一组（表情四种画风、头像三种背景、写真三种布光 + 婚纱多一种杂志感），`style` 选预设 id 或 `custom` + 自写 `style_anchor`。清单类选项（表情 / 主题 / 画风 / 场景）都能增删改，清单里没有的名字按用户写的原文进提示词——内置描写只是省事，不是限制。

**提示词的判据（`prompts.py`）。** 一张图 = 风格锚 + 主体身份块 + 单张画面 + 模板固定约束 + NO_TEXT 尾句（复用短剧的 `NO_TEXT_TAIL_*`）；负面词 = 模板负面 + 文字 / 水印词。角色走「同一角色、同一头身比、同一线宽」的身份锁（有定妆图再加一句逐字对照参考图），真人走「和参考照片是同一个人，相貌相似度约 N%」（`docs/prompt-patterns.md` P3，跟短剧 `reference_roles_clause` 同一写法：相似度写成百分数）。**文字一律不让模型画**：表情包的中文短语、百天的「100」、写真的祝语与日期都在本机叠字，需要留白的场景在提示词里写「画面上三分之一干净、低细节」。

**安全约束不是可选项。** 婴儿三个模板（满月 / 百天 / 周岁）每张都带 `BABY_SAFETY`：口鼻周围干净无遮挡、脸上与口鼻处没有饰品、脸周只有柔软织物，负面词补「口鼻处饰品 / 坚硬道具贴脸 / 遮住口鼻」；蛋糕场景固定写「没有火焰」。真人写真统一带「保持原本的脸型与五官比例、肤色自然真实、保留皮肤质感」，负面词拦「改变脸型 / 过度美白 / 过度磨皮 / 塑料感皮肤」——生成式修图最容易在这两处越界，交给模型自由发挥就会出问题。婚纱是唯一的多人画面：参考图标了 `who` 就逐人点名（「新娘（参考图 1）：…」「新郎（参考图 2）：…」）并加排他锁「画面里只有这些人：新娘、新郎」（跟绘本群像页的排他锁同一判断），只有一人时退回单人锁。

**后处理在本机，不花钱（`post.py`）。** 白底转透明不用 rembg：模板已经让模型出纯白底、主体外无投影，所以四角 + 四边中点取样定底色，从边缘做容差泛洪（`ImageDraw.floodfill` 的 `thresh`），泛洪到的像素透明，再收 1 像素 + 轻羽化去白边——主体内部同色的封闭区域不受影响（泛洪只从边缘进）。尺寸：微信表情主图 240×240 PNG ≤ 500 KB（超了逐级量化 256 → 32 色）、缩略 120×120、面板图标 50×50（第一张裁头部）、横幅 750×400（第一张放大 + 叠标题）；贴图 512 / 1024；头像 1080 / 512 / 200 + 可选圆形蒙版；写真 `aspect` 主版（默认 3:4）+ 4:3 副版居中裁切，可选叠字（`text` 写成「祝语｜日期」）。叠字复用海报的字体解析、字距与柔和投影（`storyvideo.poster`），没有可用中文字体时那一张记进 `failed`，其余照出。产物在 `05_out/`，文件名带运行戳，**从不覆盖**；`post.json` 每次运行追加一条，最新一条就是当前产物。

**打包（`export.py`）。** `09_export/<ID>-<时间>/` + 同名 zip，按模板 `export_layout` 摆：表情包 `stickers/{item}.png` + `thumbs/{item}.png` + `banner.png` + `icon.png`；贴图 `stickers/512|1024/`；头像 `avatars/1080|512|200|circle/`；写真 `album/` + `album_4x3/` + `contact_sheet.jpg`（九宫格样片）。再加 `manifest.json`（id / 标题 / 模板 / 风格 / 每个文件的角色与尺寸 / 不含 items 的 spec）与 `checksums.sha256`。只读 + 复制，产物目录之外的文件一个都不碰，`picks` 非空时只带挑中的那些（跟交付包同一规矩）。

**验收**：`tests/test_imageset_{spec,templates,post,generate,export,router}.py`——项目文件的收与拒 / 编号 / 归档、七个模板的展开条数与画幅与固定句、真 Pillow 的透明底与各档尺寸与叠字、假来源的跳过与 force 与失败隔离、zip 布局与 sha256、两个前缀参数化跑同一套接口；`tests/test_modules.py` 补两个模块的计数。

**未验证**：没有对着真实生图模型跑过这些提示词——白底转透明依赖模型真的给纯白底与无投影，参考图相似度、婴儿安全句、婚纱逐人点名的实际服从度都只有替身来源验证过（本机测试用真 Pillow 出图、假来源出「图」）；微信表情包的目录结构按公开规格摆，没有真提交过审核。

### 前端（2026-09-09，表情与头像 / 纪念写真）

两个模块共用一套页面（契约 `docs/imageset-spec.md` §5；后端引擎 `src/imageset/` 并行落地，前端只认契约里的接口形状，取值处留了兜底）：

- **路由** `router.js`：`/sticker`、`/portrait` 由同一组 `imagesetRoutes(m)` 生成——`/{m}`（`views/imageset/SetHome.vue`）、`/{m}/new`（`SetNew.vue`）、`/{m}/s/:id/{items,outputs,advanced}`（`Set.vue` 外壳 + `SetItems.vue` / `SetOutputs.vue` / `SetAdvanced.vue`），模块名放在 `meta.module`，页面从 `route.meta.module` 取模块，不各写一份。
- **胶水** `imageset.js`：`IMAGESET_MODULES`（图标 / 名字 / 主体种类 / 示例名）、`setApi` / `setUrl` / `setArtifact.{raw,out,asset,export}`、`loadSets(m)` → `store.sets[m]`、`loadSet(m, id)` → `store.set`（= detail 的 `spec`）+ `store.setDetail`（逐张行 `status` / `current_url` / `attempts[].url` / `outputs`、`exports`）、`runSetTask`（起任务 → `pollTask` → 刷新；没有 `task_id` 的同步接口直接返回并刷新；`cost_note` 用 toast 提示）、`loadTemplates(m)`（按模块缓存 `/templates`，`options_schema` 归一化成 `list / bool / int / choice / string` 五种字段，两种写法都认）、`progressOf`（`{done,total,picked,exported}`）、`countRequests`（出图前算 N 次请求）。
- **页面行为**：首页列表 = 封面 / 标题 / 模板名（列表行自带 `template_name`，否则查 `/templates`）/ 进度药丸「出图 n/N · 挑选 n · 导出」+「新建」「复制示例」。新建页：模板说明与风格预设（换预设换锚，可自写）、主体按模板种类切换——角色 = 名字 + 身份块 + 定妆图（≤ 6 张 base64），真人 = 参考照（婚纱模板每张标 `who` 新娘 / 新郎）+ 相似度滑杆——模板选项按 schema 渲染（清单 = chips）；建 = `POST /api/{m}` → 逐张 `POST /assets` → `POST /expand`（不调模型）→ 进清单页；没有照片的写真先确认。清单页：缩略图 / 标签 / prompt 可改（`PUT` 整份项目文件）/ 状态 / 「重出这一张」（`generate {only, force}`）/ 历次条「采用」（`pick {item, attempt: 文件名}`）/ 挑选勾（`pick {picks}`）；头部「展开清单」「生成缺的」「全部重出」，出图前确认框写清 **N 次生图请求**（缺的 = 没有 current 的张数，全部重出 = 全部；N 在前端算，后端返回的 `cost_note` 提交后再 toast）。产物页：「后处理」（同步或后台任务都认，产物优先取 detail 的 `outputs`，否则取接口返回）按张列产物与尺寸，「导出 zip」给下载链接，导出历史来自 detail `exports`。高级页：整份 JSON 直改、补传参考图、删除（归档到 `.webui_deleted/{m}/`）。
- **外壳**：侧栏 `MODULE_LABEL` / `MODULE_HOME` 加 `sticker` / `portrait`，手风琴 `routeModule` 按 `MODULE_HOME` 前缀识别；两个模块渲染同一个通用块（新建 + 搜索 + 列表 + 删除 ✕），只在注册表说模块可用时出现；`App.vue` 启动时模块可用才拉列表。工作台首页的模块卡不用改（读注册表的 `route`）。
- **验收** `frontend/tests/imageset.test.js`（15 条：两个模块的首页与新建、外壳、清单表的保存 / 重出 / 采用 / 挑选 / 头部三键 / 失败、产物同步与后台两条路、高级、侧栏块与手风琴、首页卡、路由 meta、helpers）。**未验证**：没有对着真实后端点过；`pick` 的 `attempt` 按文件名发（后端若按序号认要对齐）；`current` 同时认文件名 / 序号 / 对象三种写法。

### 前端（2026-09-09，出图风格卡与图像方言）

契约 `docs/image-style-cards.md` §5。后端的卡库与方言并行落地（`GET /api/style-cards`、模板载荷里的 `style_cards`、spec 的 `style` 收卡 id / `style_card` / 海报请求的 `style_card`），前端只认契约里的形状，接口没落地时界面只少了卡片区。**这一节等 §41「出图风格卡 + 图像方言」写出来就搬过去。**

- **共享卡库** `frontend/src/stylecards.js`：`loadStyleCards()` 全局只拉一次（并发调用共用同一个请求），归一化成 `{categories, cards, byId}` 存进 `store.styleCards`；分类里没写的 `category` 自己补一组。`swatchStyle(色名)` 只认色名表里的色，认不出给中性虚线圆点——**不猜十六进制**（判据在 §1）。`cardAnchor` 按界面语言取 `anchor_zh` / `anchor_en`。图像方言（§2）也在这里：`IMAGE_DIALECTS` 四条（自动 / 通用 / GPT Image / Nano Banana，各带一行「它改了什么」）、`dialectOf(detail, spec)`（项目文件里手写的 `image_dialect` 优先，其次后端自动判出来的，都没有 = 自动）、`dialectOptions(detail)`（后端给了 `image_dialects` 就以它为准）。
- **组件** `frontend/src/components/StyleCardPicker.vue`：一个下拉（`optgroup`：模板自带的预设 → 按分类分组的卡 → 自写）+ 按分类分组的卡片网格（名字 + 一句话 + 5–7 个色板小色块 + 「适合」标签），「只看适合本模板的」默认开着（判据 `fits` = `<模块>.<模板>` / `picturebook` / `poster`，不做硬限制，取消勾选能看全部；当前选中的卡即使被筛掉也留在下拉里）。选中的卡把锚句全文只读摆出来 + 色板 / 布光 / 镜头观感 / 材质 / 约束 / `source`，「改成自写」切到 `custom` 并把锚句发给父组件当文本框起点。样式全部复用 `card` / `module-card` / `pill` / `chips` / `btn` / `hint`。
- **四个接线点**：图片集新建页（替掉原来的风格预设下拉，`style` 直接发卡 id，换卡时清空自写锚）、图片集清单页顶部的风格行（当前风格名 + 图像方言 + 「换风格」展开挑选器，换过之后提示「换风格要重出才生效」，`PUT` 整份项目文件时带上 `style` / `style_anchor` / `image_dialect`）、绘本「角色与风格」页（挑卡进 `style_card`，本模块自带的四个预设与自写风格锚都还在）、成片页海报卡（只列适合海报的卡的紧凑下拉，进 `poster/generate` 与 `poster/render` 的 `style_card`）。**选「自动」方言、不挑卡时都不往项目文件 / 请求里写这个键**——老项目逐字节不变。
- **验收** `frontend/tests/stylecards.test.js`（9 条：库只拉一次与缓存、分类分组与「适合」筛选、色名表认不出时给中性块、接口挂了不炸、方言取值、卡片网格与色块与标签、选卡 emit 与锚句只读、「改成自写」的种子文案、四个接线点的载荷）。**未验证**：没有对着真实卡库点过——卡的数量与分类名以后端 `style_cards.json` 为准，界面按分类分组自适应。

## 39. 绘本 / 有声书模块（2026-09-09）

第二个功能模块（§38 的 `picturebook`），后端在 `src/picturebook/`，接口 `src/webui/routers/picturebook.py`，契约 `docs/picturebook-spec.md`。儿童有声绘本的经验规则落成四样东西：项目字段、静态检查、插画提示词的拼接顺序、出片参数。

**落在哪里**
- 项目文件 `content/picturebook/<ID>.json` 的 `pages[]` 是分页表的机读形，字段见 `docs/picturebook-spec.md`；封面和封底也各占一页。
- 静态检查（`picturebook/lint.py`，告警形状 `page P03: 影响——改法` / `全书：…`，问题卡多了 `page` 作用域）与起草系统提示词（`picturebook/draft.py`）用同一组判据：字数、句式与页数按年龄段收紧；中英文各按自己的语言写，不互相直译；教育点分认知、词汇、情感三类；拟声词全书有上限；同一句式重复到第三次要变个花样；翻页钩隔几页出现一次，最后一页不留。静态检查另外会看相邻两页的缓动是不是朝同一个方向。具体阈值以代码为准。
- 插画提示词（`picturebook/prompts.py`）按固定顺序拼：风格锚 → 场景锚 → 每个出场角色 block → scene → 构图句（下三分之一留给字幕）→ 排他句（有出场角色的页点名画面里只有这几个角色）→ NO_TEXT 尾句（复用短剧的 `NO_TEXT_TAIL_*`）。风格锚每页逐字相同，画面里一个字都不让模型画。交给外部配音时导出朗读稿（xlsx，一种语言一份，列是页 / 说话人 / 文本 / 语气提示）。
- 出片参数：页时长取这一页朗读音频的实测长度（算法见下面「时间轴」一条）；全书响度归一到 -14 LUFS、峰值 -2 dB；竖版 9:16 为主，横版 16:9 可选，都是 24 fps、yuv420p、faststart。画面的缓推缓移用 `scale` + `crop` 逐帧求值（`eval=frame`），不用 zoompan；页与页之间 0.4 秒 slideleft 过渡，翻页音效落在过渡正中；双语字幕中文在上、英文在下，正在念的那句高亮。出片（`picturebook/assembly.py`）：先混全书的声音（两个画幅共用，朗读坏了几秒内就报），再按 24 fps 的绝对帧网格每页一条命令（翻页那几帧归前一页，直接按交付口径编码，几页并行），最后 concat 流复制接片——内存与页数无关，硬切再多画面也不往后漂。

**几处取舍**
- 不用一份文档记全书状态、不做账本：每本书的状态就是三份清单——`manifests/pages.json`（每页请求哈希 / 历次产物 / `current` 指针，从不覆盖：`P01-1.png`、`P01-2.png`…）、`manifests/narration.json`（每段实测时长）、`manifests/timeline.json`（装配时算出的时间轴）。插画请求哈希不变且文件在就跳过，跟短剧的去重是同一个思想但没有 SQLite。
- 朗读按句一段：多句页合成 `P01_zh_1.mp3`、`P01_zh_2.mp3`（单句页仍是 `P01_zh.mp3`），因为逐句交替和当前句高亮都要每句的实测时长。手动回收的专业配音（整页一个 `P01_zh.mp3` 放进 `07_audio/`）由 `sync_imported` 认领成整页一段，高亮退化成整页。
- 音色：edge-tts 本来只该当占位。这里允许 edge-tts 占位出成片（`placeholder: true` 会带在任务结果和状态里），正式音色从**音色库**选（`narration.zh_voice / en_voice` 是音色档案 id，走 `genkit.voices.synthesize_with_profile`，MiniMax / 设置里的语音引擎都能用，英文语速在档案 rate 上叠加 `en_rate`）；交给外部配音时走「导出朗读稿 → 回收音频」。
- 时间轴是纯函数（`picturebook/timeline.py`）：一页的时长 = 这一页朗读音频的实测时长 + 几段停顿（进页、两种语言之间、出页；逐句交替时每个句对内外各停一下，同语言多句之间 0.3 秒），停顿值是模块里的常量，2-4 岁档进页、两种语言之间、出页这三段各多停 0.2 秒，封面与封底额外多留几秒；翻页重叠 0.4（前页 tail ≥ 0.4、后页 lead ≥ 0.4，语音不撞转场），`cut` 不重叠。没有音频时按字数估一个只为预览（`estimated: true`），装配前必须有真音频。
- 翻页钩的「连续几页没钩」不数封面（封面是书名页）。
- 起草走 `storyvideo.scriptgen._dispatch`（跟短剧同一个文本模型入口），结果经 `draft_to_spec` 收拾（页 id、kb 交替、结尾钩清空、角色名映射 id）再过 `normalize_book`；`jobs/draft` 是后台任务 `pb-draft`，草稿在任务结果里，`save: true` 才直接建书。四个风格预设（水彩 / 蜡笔 / 剪纸 / 扁平矢量）的英文锚在 `draft.STYLE_PRESETS`。

**未验证**（没有各家直连 key、没跑真机）
- 真实插画模型对排他锁 / 参考图（定妆 + 场景锚）/ 3:4 出图的响应；异步来源的任务内轮询只用假来源验证过（20 分钟上限）。
- 真实 TTS 的时长与停顿手感（本机测试用正弦波替身 + 真 ffprobe / 真 ffmpeg 出片，1080×1920 与 1920×1080 都验证过时长与尺寸）；MiniMax 音色的 `rate` 叠加是否跟 edge 的百分比语义一致。
- 圆润字体：字幕字体仍走短剧的 `subtitles.resolve_font` 候选表（微软雅黑等），没有单独找圆体。

## 38. 从短剧平台到内容创作工具（2026-09-09）

需求：产品定位从 AI 短剧平台调整为内容创作工具，短剧只是其中一个功能模块。到这一天为止整个应用就是短剧产线（storyvideo）；这一节记第一阶段的改法——把外壳变成多模块的内容创作工作台，短剧的每一页、每一个接口的行为都不动。

**模块 vs 共享服务。** 模块 = 有自己的项目形态、自己的内容目录、自己的页面命名空间的一条产线；共享服务 = 所有模块都用、不属于任何一条产线的东西。现在的划分：

| | 是什么 | 现有的 |
|---|---|---|
| 模块 | 短剧（`drama`） | 剧本 → 分镜 → 首帧 → 视频 → 审片 → 后期 → 成片 → 海报，全部现有页面与接口 |
| 共享服务 | 模型设置、音色库、统一网关、任务、导出、界面语言、`/api/health` | 行为不变，路由仍在根上 |

**注册表。** `webui/modules.py` 一张纯数据表：`Module(id, name, english, description, icon, content_dir, route, status, project_kinds)`。`drama` 是唯一 `status="ready"` 的；`image`（图片工作台）、`clip`（单条视频）、`picturebook`（绘本 / 有声书）、`music`（配乐）以 `status="planned"` 占位——只有名字和一句话，没有路由、没有目录。`GET /api/modules` 返回产品名 + 每个模块（含 `available`、`project_count`；短剧的项目数 = `content/story_video` 下的 spec 数，跳过剧本层文件）。项目列表 `/api/episodes` 每行、项目详情与概览都带 `module: "drama"`（由 `deps.module_for_pipeline(pipeline)` 给，pipeline → 模块的映射只在注册表里写一次）。后台任务记录加 `module` 字段（默认 `drama`，老记录缺省也按 drama），`GET /api/tasks?module=` 可过滤，为以后的任务中心页留口。

**产品名。** 工作名「Content Cook · 内容创作工作台」，只在 `webui/branding.py` 一处（`PRODUCT_NAME` / `PRODUCT_NAME_EN`），`/api/health` 与 `/api/modules` 都带 `product`；界面头部、`document.title`、FastAPI 标题、桌面窗口标题都从它读。Python 包名、命令行 `storystudio`、设置目录 `~/.storystudio`、文件布局不改——那些是技术标识，改名不该牵动它们。

**路由命名空间。** 短剧的页面全部搬到 `/drama` 下：`/drama`（模块首页 = 原来的首页正文：项目提示 / 三步引导）、`/drama/new`、`/drama/libraries`（镜头库与风格库是短剧专属）、`/drama/p/:id/…`。旧路径 `/new`、`/libraries`、`/p/:id/*` 保留重定向（子路径与查询串一起带过去），已有链接和书签照常打开。`/settings`、`/voices` 是共享服务，留在根上。工作台首页 `/` = 一排模块卡（可用的点进去，规划中的灰掉带「规划中」与一句说明）+ 短剧区（嵌模块首页——短剧是唯一可用的模块，首页不该让人多点一层）。侧栏分两块：上块全局（工作台首页、模型设置、音色库、语言），下块「短剧」（新建项目、镜头库、搜索、项目列表，删除 ✕ 保留）。

**不动的。** 内容目录（`content/story_video`、`content/series`、`content/assets`）、账本、spec 模型、提示词编译（金丝雀无漂移）、所有 `/api/episodes/storyvideo/...` 接口、CLI、设置文件。

**候选模块与各自能复用的。** 第二阶段按需要挑一个做，每个都是在共享服务之上加一层薄薄的项目形态：

| 模块 | 复用 | 新写的 |
|---|---|---|
| 图片工作台 `image` | 生图角色与适配器（`genkit.router` / `capabilities`）、多模型对比（`webui/compare.py` 的候选试跑，异步来源逐个模型轮询见 §36 的 2026-09-10 那条）、海报叠字（`storyvideo.poster` 的 Pillow 叠字与满幅裁切） | 单张 / 成组的项目文件与目录、提示词编辑页 |
| 单条视频 `clip` | 视频适配器与后台轮询（`webui/generate.py` 的 watcher）、能力表、`docs/prompt-patterns.md` 整理的三段式提示词写法 | 一条提示词一段视频的项目形态、三段式编辑器 |
| 绘本 / 有声书 `picturebook` | 音色库与 TTS（`genkit.voices`、语音角色）、装配与字幕（`storyvideo.assembly` / `subtitles`）、后期层的旁白铺排 | 按朗读音频长度排页的时间线、统一画风的插画项目形态 |
| 配乐 `music` | 音乐生成来源（`music` 角色的适配器）、配乐时间线层（§25） | 按时间线意图生成并落到项目配乐层的页面 |

### 前端（2026-09-09，绘本模块）

绘本 / 有声书模块的前端已按 `docs/picturebook-spec.md` §6–§7 落在 `/picturebook` 命名空间下（§39 写好后这一段搬过去）：

- **路由** `router.js`：`/picturebook`（模块首页 `views/picturebook/PbHome.vue`：书列表 = 封面 / 标题 / 年龄段 / 页数 / 进度药丸「插画 n/N · 朗读 n/N · 成片」，「新建绘本」「复制示例」）、`/picturebook/new`（`PbNew.vue`：方向一句话、年龄段 2-4 / 4-6 / 6-8 与页数提示、双语策略四选、教育三点、风格预设水彩 / 蜡笔 / 剪纸 / 扁平矢量四段可改文案；「AI 起草」= `POST /jobs/draft` 后台任务 → `pollTask` → 用 `result.draft` `POST /api/picturebook` 建书；「空白开始」= 只有书名、`pages: []`）、`/picturebook/b/:id/{overview,pages,characters,narration,final,advanced}`（`Book.vue` 外壳：左栏导航 + 顶部进度 + 概览的 `next`；子页分别是概览 / 分页表 / 角色与风格 / 朗读 / 成片 / 高级）。
- **胶水** `picturebook.js`：接口前缀与产物地址、`loadBooks` → `store.books`、`loadBook` → `store.book`（= detail 的 `spec`）+ `store.bookDetail`（逐页行 `image_url` / `audio.{lang}` / `timeline` / `final_url`）+ `store.bookSummary` + `store.bookLint`、`runBookTask`（起任务 → `pollTask` → 刷新，`store.gen` 走顶部进度条）、`toCards`（后端四要素卡认不出「page P02:」前缀，前端从原文补页号）。
- **页面行为**：分页表整表编辑（每页的画面、出场角色、场景锚、中英两种文本、翻页钩、教育点、缓动方向与翻页方式都能改，行可增删与上下移）保存走 `PUT` 整本，返回的 `warnings` 按页贴在行下；每行缩略图 + 「重出」= `pages/generate {only:[pid], force:true}`。角色页定妆 / 场景锚图走 `POST /assets/{character|scene}/{key}`（base64，先保存再上传）；成片页 BGM / 翻页音效走 `music` / `sfx`。朗读页音色从 `/api/voices` 选（拉不到退回 detail 的 `voices`），页时长与全书时长取 `detail.timeline`。成片页预览 `final_url`、下载、导出中 / 英朗读稿、16:9 勾选。高级页整本 JSON 直改 + 删除（归档）。
- **外壳**：`modules.py` 里 `picturebook` 已 `ready`（`content/picturebook`，`/picturebook`，`project_kinds=("picturebook",)`），`/api/modules` 数 `content/picturebook/*.json`；侧栏在短剧块下多一块「绘本」（新建 / 搜索 / 书列表 / 删除 ✕），只在注册表说模块可用时出现；启动时模块可用才拉书列表。
- **验收** `frontend/tests/picturebook.test.js`（首页与新建两条路、分页表保存 + lint + 重出、朗读音色与试听、成片装配轮询与失败、侧栏块与路由、helpers）；`tests/test_modules.py` 补绘本计数。**未验证**：没有在浏览器里对着真实后端点过（后端同日落地）；定妆图预览地址按 `artifact/asset/{kind}/{文件名}` 拼，上传后靠 `refreshBook` 回读。

### 工作台首页与模块首页分家 + 任务中心（2026-09-10）

上面那条「工作台首页 `/` = 模块卡 + 短剧区（嵌模块首页）」是短剧还是唯一可用模块时的权宜之计。现在有七个模块，首页再嵌一整个短剧首页就是把一个模块摆在别人头上，所以拆开：**`/` 是启动台，`/drama` 是短剧模块首页（一行没改）**。BACKLOG 里「模块首页要不要独立」这个待定问题按这个答案关掉。

**启动台 `/`（`views/Home.vue`）** 自上而下五块，每块都在没东西时自己消失，不留空壳：

1. 产品名（`store.productName()`）+ 一句定位。
2. 模块卡（跟原来一样：可用的点进模块首页、带项目数；规划中的灰掉带「规划中」与一句说明）。
3. **最近**：跨模块最近改过的项目，模块图标 / 标题 / id / 相对时间（「3 分钟前」「昨天」），点一行直接进那个项目的页面。空就整块不出现。
4. **正在跑的任务**：只在真有 pending/running 时出现一行「N 个任务在跑」，链到任务中心。
5. **第一次用**：只在所有可用模块都还没有项目、也没有最近记录时出现。三步是**模块无关**的（配好模型 → 选一个模块开始 → 花钱前都会先问你），短剧那三步（示例剧集 / 出首帧）留在 `DramaHome.vue`，不重复。

**`GET /api/recent?limit=`（`routers/recent.py`）** 支撑第 3 块：走每个 ready 模块的内容目录（短剧用 `deps.STORYVIDEO_DIR`，其余是 `content/<module>`），`*.json` 就是一个项目，`stat()` 取 mtime，只读出标题（`title_zh → title → title_en`），按 mtime 倒序返回 `[{module, module_name, module_name_en, icon, id, title, url, mtime, path}]`。三条纪律：坏 JSON / 读不出的文件退回「用 id 当标题」而不是让整张表 500；超过 1 MB 的文件只在头部正则找标题，不整份解析；`<id>.screenplay.json` 剧本层文件跟列表接口一样跳过。`url` 是前端路由，跟 `router.js` 一一对应（`/drama/p/<id>/overview`、`/picturebook/b/<id>/overview`、`/{sticker|portrait|image}/s/<id>/items`、`/clip/c/<id>/write`、`/music/m/<id>/generate`），前端 `src/tasks.js` 里有同一张表，测试两边都比对。只读、不联网、不花钱。

**任务中心 `/tasks`（`views/Tasks.vue` + `src/tasks.js`）**：跨模块列 `GET /api/tasks`（`?module=` 过滤，接口没改）。模块过滤走服务端，状态过滤（全部 / 在跑 / 成功 / 失败，「在跑」= pending + running）在本地筛。一行 = 模块 · 种类 · 项目 · 状态 · 开始时间（相对时间，鼠标停上去是绝对时间）· 用时 · 失败原因。**种类是人话**：`src/tasks.js` 的 `KIND_LABELS` 把 `generate-watch` / `asr-qc` / `post` / `poster` / `export` / `compare` / `install-asr` / `asr-model` / `voice-clone` / `clip-frame` / `clip-generate` / `set-generate` / `set-post` / `music-generate` / `pb-*` 全翻一遍，认不出的原样显示 kind、不猜。项目列链到项目页；`compare` 的 `episode_id` 是「剧集:镜号」取前半，`voice-clone` 的是「voice:<id>」链到音色库，`install-asr` / `asr-model` 的根本不是项目 id，就不给一个打不开的链接。失败原因去掉异常类型名只留第一行，「展开」看原文。**有任务在跑时每 3 秒刷一次，全停就把定时器撤掉**（不给后端做无用轮询）。这一页只读：从不发起任何生成。侧栏全局块多一个「📋 任务中心」，启动台的「N 个任务在跑」也指这里。

**验收**：`tests/test_recent.py`（9 条：mtime 排序、坏 JSON 容错、剧本层跳过、limit、每个模块的地址、大文件走头部）、`tests/test_modules.py` 补一条任务列表形状与 `?episode_id=` / `?module=` 叠加过滤、`frontend/tests/workbench.test.js` 改写成启动台（含「首页上没有短剧区」这条反向断言）、`frontend/tests/tasks-center.test.js`（11 条）。**未验证**：没有在浏览器里对着真实后端点过；「最近」按文件 mtime 排，编辑器格式化或外部工具碰过文件也会把它顶到前面。

## 37. 长单镜视频提示词的写法对照（2026-09-08）

判据是 `docs/prompt-patterns.md` 里的 P1–P3（长单镜视频提示词的写法，编号在代码注释、测试和其它文档里引用）。那类提示词面向"一条 15–30 秒生成到底"的模型，跟我们逐镜产线不是一回事，所以逐镜产线只取三条判据、不取三段结构，都只在写了对应字段的项目上生效（canary 无漂移）：

- **P1 画幅显式 + 排除另一种画幅 + 主体落位**：形态卡加 `composition` 句（中英；先写主体落在画面哪一块，句末写原生画幅并排除另一种），随运动预算句一起进视频提示词形态段（`forms.form_prompt_sentences`）。
- **P2 色板写具体色名**：五张新风格骨架的「调色/光线」项要求 5–7 个具体色名，起草时进系统提示词。
- **P3 与参考图相似度写百分比**：`shot.reference_roles[].fidelity`（1–100）→ 首帧提示词里多一句带百分比的相似度说明（`storyvideo.prompts.reference_roles_clause`）；镜头页参考图用途行加相似度百分比输入。

**未验证**：三句话对真实模型的约束力，等 key。

## 36. 海报（2026-09-08）

每个项目成型时出一张海报。两件事分开做，因为可靠性完全不同：**底图**（无文字的关键画面）走生图模型或直接拿某一镜首帧；**标题**在本机用 ffmpeg `drawtext` 叠，字体走字幕那一套解析（`subtitles.resolve_font`），中文一定渲染得出，输出固定 1080×1920 PNG。模型渲染文字不可靠，片名一旦画歪整张作废——所以底图提示词跟首帧一样明令「画面里不出现任何文字」（§20 的文字政策），标题**绝不进提示词**。

- **模块** `storyvideo/poster.py`：`poster_prompt(spec, base_shot=)` = 风格骨架 + 场景锚 + 出镜最多的 1–2 个人物成员的角色块（按镜数计，变体不分开算，场景/道具块不当主体）+ 钩子画面（指定底图镜的 art → 单集契约 `hook` → 第一镜 art）+ 海报构图句（竖版、主体大、上三分之一留给标题）+ 禁字尾句；zh / en 各一份，负向词在项目 `neg` 后追加「文字, 水印, logo…」。`poster_request_data` 沿用 `image_params` 的模型 / 尺寸 / `workspace_id`，画幅固定 9:16，参考图 = 入选角色的锚图（跟首帧请求同一来源 `cast_anchor_asset`，规范化后已是绝对路径，适配器自己上传 / 转 data URL）。`render_poster(base, out, title, subtitle, font)`：等比缩放 + 暗色补边到 1080×1920，标题背后六层 drawbox 叠出暗色渐变带保证可读，超过 9 字自动两行；`extract_first_frame` 抽视频第一帧当底图。
- **产物** `<output_root>/07_poster/`：`base-<毫秒>.png`、`poster-<毫秒>.png`、`poster.json`（只追加：提示词、模型、底图来源、标题、字体、时间）。**从不覆盖**：毫秒戳被占用就顺延，`render_poster` 遇到已存在的目标文件直接拒绝；`latest()` 只认成品文件还在的最新一条。
- **接口** `routers/poster.py`：`GET …/poster`（当前海报、历史、建议标题 = `title_zh → title_en → id`、建议副标题 = 契约 `premise → logline → 英文片名`、可用字体、有首帧或视频的镜、提示词、ffmpeg 是否可用、正在跑的任务）；`POST …/poster/generate {source: model|frame, shot_id?, title?, subtitle?, font?}`——`model` 起后台任务 kind `poster`（`tasks.start`，同项目同时只跑一个），任务体用 `build_provider()` 发**恰好一次**生图请求、同步与异步来源都支持（异步在任务里轮询，见下面 2026-09-10 那条）、不进账本、返回 `{task_id, model, cost_note}`；`frame` 拷贝 `01_frames/<id>.png`（没有就抽 `02_video/<id>.mp4` 第一帧），不花钱，同步返回成品；`POST …/poster/render {title, subtitle, font}` 用最新底图只重排文字；`GET …/poster/file/{name}` 流 PNG，只接受 `07_poster` 里的纯文件名（含 `/`、`\`、以 `.` 开头、非 `.png`、符号链接一律 404）。ffmpeg / 字体缺失、底图不存在等都是 425 + 中文原因，英文界面走 `i18n_rules`。
- **联动**：侧栏列表（`deps.cover_url`）与概览 `summary.cover_url` 有海报就用海报，否则第一张首帧；`summary.poster = {exists, title, hint}`——成片已合成、还没有海报时 `hint` 提醒去成片页生成，`next_step` 文案不变（「成片已通过，可以导出」）；交付包（`export.build_package`）把最新成品拷成 `poster.png`，manifest 记 `poster = {source, file, title, made_from}`。成片页「海报」卡：预填标题 / 副标题、字体下拉、来源单选（模型来源先弹确认再发 1 次生图并轮询任务；首帧来源直接出）、预览 / 下载 / 历史、只重排文字。
- **未验证**：真实生图来源上没跑过（没有 key）；`FakeProvider` 覆盖了同步出图、异步轮询、远端失败、等到上限四条路。

**2026-09-09 返工（第一版观感太粗糙）**：叠字从 ffmpeg drawtext 改成 Pillow。规则：底图满幅裁切到 9:16 不补黑边；标题放在细节少的一端（边缘强度 + 亮度判断，可指定 top/bottom），背后是取自画面暗部颜色的二次缓动渐变压暗层，不是平涂黑带；标题用粗体中文字体（微软雅黑粗体 / 黑体 / 思源黑体 Bold 候选，再退到字幕字体），按宽度定字号、最多两行、6% 字距、高斯模糊投影；副标题限 24 字（英文 60 字母），超出在标点处截断，两行时对半分不留孤字；可选小标（系列集数「第 N 集」> 形态名）+ 金色细线。接口加 `layout` / `kicker`，记录 `layout_used`。ffmpeg 只在抽视频首帧时才需要。

**2026-09-10 异步来源（海报 + 首帧对比试跑）**：这两处是在后台轮询那套定型之前写的，碰上异步来源（提交后返回 `<前缀>:<任务 id>`、要轮询的来源）只会报「这个来源的生图是异步返回的，暂时只支持同步出图的来源」——现在跟绘本插画 / 图片集 / 单条视频同一套纪律：**提交 → 轮询（有上限）→ 下载 → 校验**。

- **海报** `routers/poster.py`：`run_model_poster` 提交后，拿到的不是「已完成」就进 `_wait_for_result`——每 `POLL_SECONDS`（5 秒）问一次 `provider.get_task(task_id, IMAGE)`，每轮往自己这条任务记录里写 `{current: "poster", waiting_rounds: N}`（界面轮询任务时看得到「还在等」），最多等 `MAX_WAIT_SECONDS`（20 分钟，跟兄弟模块同一口径）。完成 → 下载 → 空文件也算失败 → 叠字落盘，跟以前一模一样；远端失败 / 等到上限 → 抛 `PosterError`（人话原因，后台任务原样记进任务记录），并且**把已经落下的半张底图删掉**（叠字那一步失败时底图与半张成品一起删）——`07_poster/` 里不留任何没有记录指向的文件。两个上限是模块常量（不写死在默认参数里），测试 monkeypatch 它们把 20 分钟调成 0。同步来源（网关返回 base64 / `file:` 任务）一步不变：提交就是完成，不进轮询。425 那条「异步来源不支持」的文案与它的 `i18n_rules` 规则一起删掉。
- **首帧对比试跑** `webui/compare.py`：`run_candidates` 同样的写法，只是**每个模型各自独立轮询**——一个模型远端失败或等到上限，只在它自己那一行记 `error`（清单行多记一个 `task_id`），别的模型照常出、照常能「采用」，整批不中断；半张候选同样删掉，每跑完一个模型就把 `manifests/compare.json` 写一次（任务中途挂了也不丢）。进度按 `{progress: "2/3", current: 模型名}` 写进任务记录。
- **验收**：`tests/test_poster.py`（异步轮询到完成、等到上限 / 远端失败后 `07_poster` 里没有残留文件）、`tests/test_webui_compare.py`（三个模型独立轮询、中间那个失败不拖累两侧、超时只废掉那一行且之后还能继续出候选）。**未验证**：真实的异步来源上没跑过，假 provider 只模拟了 processing → processing → finished 这条时序。

## 35. 镜头风格 profile 改为经典产线（2026-09-08）

镜头语言库的 profile 此前是六条早期档位（`用于` 写的是具体项目的名字），新用户看不懂也选不了。改成 5 条**市面上公认的经典产线风格**，每条按参考作品的公认风格人工设定镜头语法（2026-09-11 更正：原文写的是「市面上验证过的经典产线，每条从公开作品蒸馏镜头语法」，不准确——没有对这些作品做逐镜分析，见 §48）：`classic_film` 经典电影正剧（《教父》《肖申克的救赎》《花样年华》：慢推慢拉、过肩反打、剪影，一集 4 个重音）、`short_drama_vertical` 竖屏爆款短剧（《我在八零年代当后妈》《无双》《闪婚》类：大脸、反应插入、快钩子，单镜 3—5 秒，一集 5 个）、`anime_action` 动画动作番（《鬼灭之刃》《进击的巨人》《咒术回战》：低机位推进、环绕、急推变焦、变速、剪影，一集 6 个）、`tv_drama` 电视剧长剧情（《琅琊榜》《甄嬛传》《请回答1988》：稳、过肩对话、环境建置、轻推、双人镜，一集 3 个）、`suspense_thriller` 悬疑惊悚（《隐秘的角落》《沉默的真相》《七宗罪》：倾斜、手持、窥视、穿门推进、穿堂、尾随、慢摇揭示，一集 4 个）。每条多 `refs`（风格参考作品，不是蒸馏来源；2026-09-11 起另带 `basis` / `confidence`，见 §48）和 `english` 两个字段；`shots` / `combos_within_shot` / `combos_sequence` 三张表一条没动。

- **兼容**：旧六档原样挪进 `camera_library.json` 的 `legacy_profiles`（每条 `"legacy": true`，`用于` 改成「旧项目兼容：…」，不再出现旧项目名）。`prompts.find_camera_profile` 按 `profiles → legacy_profiles` 查找，`compile_camera_phrase` / `validate_camera_usage` / `scriptgen._camera_allowlist_text` / `/api/camera-options?profile=` 都走它——已有项目和金丝雀夹具（`ghost_pov`）的提示词逐字节不变（2026-09-23 起 `ghost_pov` / `vlog_handheld` 的几个镜位与风格骨架改成通用写法，用到它们的旧项目请求指纹变过一次，金丝雀也换了一段新的迷你故事，见 CHANGELOG「提示词措辞调整」）。`/api/camera-options` 的 `profiles` 列表和「基础配置」页只列新五条；带 `profile=` 查旧档时多返回 `legacy: true`。`validate_camera_library` 对 `legacy_profiles` 跑同一套引用检查，并拒绝与 `profiles` 重名。
- **风格库**：`frame_style_skeletons` / `scene_anchor_checklists` 为五条新产线各加一档（标 `inferred`：没有本仓库自己的成片样本，是旧骨架 + 按参考作品的公认视觉风格人工推的，没做逐镜分析；2026-09-11 更正了原文的「蒸馏」一词），旧六档保留（legacy 项目起草仍能用），`用于` 同样不再点名旧项目。
- **默认与示例**：新建项目页默认 `short_drama_vertical`；`examples/episodes/first_episode.json` 改用它（五个运镜都在白名单里）。
- **未验证**：五档的白名单和上限是按参考作品的公开镜头语言推的，没有在本仓库跑过成片对比；`max_moves_per_episode` 只是告警不是拦截。

## 34. 后台轮询与断点续等（2026-09-08）

**故障**：异步任务型的服务商上点「生成视频」，几条视频都提交成功之后，界面会一直停在「正在生成中…，不用再点」，哪怕服务商早已做完。三处叠在一起：

1. `POST …/generate` 跑 `runner.run(wait=False)`：提交即返回，服务端之后没有任何东西再去问服务商。
2. 前端轮询看 `generate-estimate` 的 `pending_count`，为 0 就报「这一批生成完了」——而 `pending` 不含已提交还没回来的镜（那些在 `in_progress`），所以提交一返回循环就结束。
3. 之后再点：`pending_count === 0 && in_progress.length` 只弹一句提示，从不轮询。运行时其实会续（`_run_unlocked` 先 `_recover_inflight_remote_jobs`），但没有任何入口在不花钱的前提下只跑这一段。

**修法**：轮询搬到服务端，前端只盯状态。

- **运行时** `StoryVideoRunner.poll(episode)`：拿同一把生产锁、同样对账不明提交，然后只走 `_recover_inflight_remote_jobs(wait=False, collect_errors=True)`——它只碰 `submitted / processing / finished` 的任务，`prepared` 碰都不碰，所以**绝不提交、绝不花钱**。一轮里某条任务轮询/下载失败不拦别的任务：收集起来抛 `StoryVideoPollErrors`（每条原始异常都在 `.errors`）。有任务还在飞就照 `run` 的规矩记 `frames_partial / videos_partial`；全部落地就把这一道的技术检查记进 `qc.json`（`_record_stage_qc`，从 `_advance_frames / _advance_videos` 抽出来共用：本地 ffprobe + 联络表，不花钱）——否则审片会被「技术检查还没跑完」挡住，而异步服务商下的 `run()` 只会在下一次点生成时才记。返回 `status()` 同样的结果，多一项 `inflight`。临时下载文件名里的任务 id 只留 `[A-Za-z0-9._-]`（`<前缀>:<uuid>` 这类带冒号的 id 在 Windows 上是 WinError 87）。
- **后台任务** `webui/generate.py`：`ensure_watcher(episode_id)` 起一个 `generate-watch` 任务（`tasks.start`；已经在跑就返回那个的 task_id、`started: false`；没有在飞的任务返回 `inflight: []`、不起）。任务体 `watch_loop`：`poll` → 没有在飞的就收工（`{inflight: [], rounds}`），否则睡 `SLEEP_SECONDS`（15 秒，测试改 0）再来；`MAX_WAIT_SECONDS`（60 分钟）到了收工并记下还没回来的任务。一轮里的 `StoryVideoError` 不吞：人话原因写进任务记录（`error` / `errors` / `error_repeats`），继续；同一条错误连着 `REPEAT_ERROR_LIMIT`（3）次才停、任务记 failed。服务商用生成端点同一个 `build_provider()`（`generate._provider`，测试替换）。`tasks.py` 补 `current_task_id()`（线程内拿自己的 id）、`active_task_id(kind, episode_id)`、`note(task_id, **fields)`。
- **接口**：`POST …/generate` 提交后调 `ensure_watcher`，响应多 `watch_task_id` / `inflight`；新 `POST …/generate-resume`（不要 plan_token，不花钱）返回 `{task_id, started, inflight, in_progress}`，空闲时 200 + `inflight: []`；概览 `summary` 多 `in_progress`（在飞的镜号）与 `watching`（有 generate-watch 在跑）。
- **断点续等**：`resume_inflight_watchers()` 扫 `output/story_video/storyvideo.sqlite3`（`RunStore.list_run_ids`），哪个 run 还有 `submitted / processing / finished` 的任务就 `ensure_watcher`；账本文件不存在什么都不做。挂在 FastAPI lifespan 上（`app.py`）；测试用不带 `with` 的 `TestClient`，不会触发。
- **前端** `store.js`：轮询抽成 `watchGenerate(id, through, onlyQ, started)`，收工条件是 `pending_count === 0 && !in_progress.length`；有在飞的就刷新概览，概览说 `watching === false`（服务重启过、或任务报错停了）就补发一次 `generate-resume`；没有在飞的、还有待生成（下一波依赖上一波首帧 / 有失败要重提）沿用按新 token 再提交；全部失败停下摆原因。`runGenerate` 里 `pending 为 0 且有在飞的` 不再只弹提示：`generate-resume` + toast「上次提交的还在生成（…），后台在等结果」+ 进入 `watchGenerate`。超过轮询上限提示「服务商还没返回，后台会继续等」并清掉进度条。概览页（`Overview.vue`）有在飞的镜时显示「生成中：S1、S2（后台在等服务商）」药丸 + 「刷新」（后台没在等时先补发 `generate-resume`）。
- **仍然没有的**：轮询间隔与上限不可配置；`generate-watch` 任务失败三次后不会自动重起（要人回到项目页、前端补发一次）；每轮 `poll` 都 `prepare()` 一次（幂等，但会核对冻结快照，快照坏了任务会以那条错误停下——这是对的）。

## 33. 音色克隆与参考音频（2026-09-08）

M6 留下的两条：音色档案带"参考音频"并接一个支持克隆的 TTS 来源；视频模型若吃音频参考，在适配器层加 `reference_audio`。没有可克隆来源的 key，两条都按「替身」纪律做：真实适配器代码对着公开接口形状写，测试用假 HTTP / 假子进程回放，全部标**未验证**。

- **适配器**（`genkit/adapters/minimax_speech.py`，注册名 `minimax_speech`，语音角色）：MiniMax T2A v2——`POST /t2a_v2`，`model: speech-02-hd`、`voice_setting.voice_id`、`audio_setting.format: mp3`，返回 `data.audio` 是 **hex**（不是 base64），`base_resp.status_code` 非 0 按码分类（1008 余额 → 配额；1002 限流 → 限流（配额的子类，同样可重提，提示等一会而不是去充值）；1026/1027 敏感 → 审核拦截；其他 → 一般错误），HTTP 层沿用 `raise_for_status`。克隆两步：`POST /files/upload`（multipart，`purpose=voice_clone`）拿 `file.file_id`，再 `POST /voice_clone` 带 `file_id` + **客户端自选** `voice_id`（`ss_<档案 id slug>_<参考音频 sha1 前 8 位>`：字母开头、≥ 8 位、只用字母数字下划线，满足 MiniMax 的规则；同一段音频克隆两次得同一个 id）+ 可选 `text/model` 让服务端顺手出试听；`input_sensitive` 为真按审核拦截。`extra.group_id` 有值时地址带 `?GroupId=`（老版接口要）。语速 `-4%` 这类 edge 写法折成 `speed`（0.5–2.0），音调不映射。模型设置表多一档「MiniMax 语音接口」（`base_url / api_key / model / voice`）；契约夹具 `tests/fixtures/adapters/minimax_speech.json`。
- **档案**（`genkit/voices.py`）：`engine` 多一个 `minimax`；`reference_audio`（wav/mp3/m4a，≤ 20MB；绝对路径原样，相对路径先按 `~/.storystudio/voices/`、再按当前目录解析；保存时查扩展名、存在、大小）与 `clone = {provider, voice_id, status: none|cloning|ready|failed, cloned_at, error}`。换参考音频或清空 → `clone` 归零（旧 id 对应旧音频）；`clone` 只由 `clone_voice` 写，请求体里给了也不认。只有参考音频、还没克隆的档案也能存（既无 voice 也无描述）。`clone_voice(profile)`：先记 `cloning`，成功后 `ready` + 服务商 id，并把 `engine` 切到 `minimax`、`voice` 填克隆 id（绑给角色 / 旁白拷走的就是它）；失败把原因写进 `clone.error` 再抛。`synthesize_with_profile` 的 `minimax` 分支挑 `clone.voice_id`（没有才用 `voice`）；旁白轨 `render_vo_lines` 认 `voice_over.engine == "minimax"`。MiniMax 密钥来源：语音角色选了 `minimax_speech` 优先，否则借视频角色 `minimax_video` 的 key（同一把 key 两个接口都能用）。没有这两个字段的老档案一个字节不变。
- **接口**（`webui/routers/voices.py`）：`POST /api/voices/<id>/reference-audio`（base64，同配乐上传的形状；存 `<音色目录>/reference/<id>-<sha256 前 8 位>-<毫秒>.<ext>`，**永不覆盖**、旧的尽力清理；样本清理的 glob 碰不到这个子目录）；`GET .../reference-audio` 流文件（档案指向的路径必须是 wav/mp3/m4a 且存在，id 不合法直接 404）；`POST .../clone` 后台任务（`tasks.start("voice-clone", "voice:<id>")`，正在克隆 409、没密钥 400 说清楚去哪配），失败原因经 `humanize` 翻成人话写回 `clone.error`；进度看档案（`GET /api/voices` 里的 `clone.status`）。目录接口 `GET /api/voices/catalog` 多返回 `engines`（配了 MiniMax 密钥才含 `minimax`），`engine=minimax` 给文档里的系统音色表（没密钥给空表 + 说明，不是错误）。
- **界面**（`Voices.vue`）：「参考音频与克隆（MiniMax）」卡——文件选择（`.wav/.mp3/.m4a` → base64 上传）、当前文件名 + `<audio>`、「克隆音色」按钮（先保存表单再提交，轮档案直到不再 `cloning`）+ 状态药丸（未克隆 / 克隆中… / 已克隆：id / 失败：原因）；没密钥时按钮禁用并提示去模型设置。引擎下拉按 `engines` 多出 MiniMax（已是 minimax 的档案在没密钥时也显示当前值）；列表里克隆成功的档案带「已克隆」药丸。
- **视频模型的参考音频**：有的视频来源接受音频参考（参考素材里多一项音频，上传时 `asset_type=audio`）；内置适配器都不认，插件来源可以用。落法：`spec.video_params.reference_audio`（项目相对路径，mp3/wav/m4a；`normalize_spec` 只查类型和扩展名）→ `video_request_data` 只在声明了才把它放进 `extra_params`（指纹里是相对路径，机器无关；没声明的项目指纹零变化，金丝雀守着）→ 运行时提交前解析成绝对路径（不存在直接 `StoryVideoError`）、经 `provider.upload_asset(asset_type="audio")` 换成 `local:` 令牌放回 `extra_params.reference_audio`。**不进 `references`**：火山 / OpenRouter 适配器按下标取首帧、尾帧，多一项音频会被当成尾帧。支持它的来源从 `extra_params.reference_audio` 取令牌、按 `asset_type=audio` 上传（缓存键跟图片分开）；内置适配器不转发 `extra_params`，自然忽略。没有界面入口（先适配器层 + spec 字段）。
- **未验证**（没有 MiniMax key，也没实际提交过带音频参考的任务）：T2A v2 的 `data.audio` 是否仍是 hex、`file_id` 是数字还是字符串（两种都发得出去）、`voice_id` 命名规则与 `GroupId` 是否必需、克隆后多久可用、各视频模型对音频参考的真实支持面（文档写的是形状，没写哪些模型认）。这些在 M0 真实联调时用录制响应替换夹具即可（录制的响应先脱敏：去掉签名地址、任务 / 请求 id、账号与余额字段），测试本身不用改。

## 32. 剧本层、单集契约与系列交接（2026-09-08，融合 drama-skills）

取 zenstory drama-skills 三条判据，不取它的 Markdown 剧本语法与五文档目录：**先有剧本再拆镜**（剧本层只管故事：每场改变信息/权力/关系/物理状态，对白在争取/回避/试探/逼迫，场尾有可交接的状态）；**单集契约**（写节拍前先答：进入状态、开场钩子是观众能立即理解的未稳定局面、集尾写成「某人已经决定做什么 / 某个后果已经启动」、本集自己必须兑现的局部结果、留给下一集的事实）；**集间交接写成事实不写气氛词**（谁知道了什么、谁拿着什么、哪个危险已经开始），跨集记忆只存后续决策所需、角色身份原样延续。

- **剧本层（两步起草）**：`scriptgen.generate_screenplay` → `SCREENPLAY_JSON_SCHEMA`：`{title, logline, contract, cast[{id, name, role, look}], scenes[{id, heading, beats[], dialogue[{who, zh, en?, cue?}], change?}]}`，系统提示词 `build_screenplay_system_prompt`（只写剧本不写镜头；题材卡 / 形态卡段照旧追加）。成功后落盘 `content/story_video/<id>.screenplay.json`（`deps.screenplay_path`；列项目与分配新编号时按 `.screenplay` 后缀跳过 / 占号）。第二步 `generate_draft(screenplay=)` 把 `screenplay_text` 排成人读的文本进用户提示词：「按这个剧本拆镜，不要改剧情、不要加角色、不要删场次；台词按原文分到各镜；cast id 沿用人物表；episode_contract 照抄剧本的 contract」；不给剧本时提示词一个字不变（一步起草仍是默认）。接口：`POST /api/scripts/jobs/screenplay`（后台 job，kind `generate_screenplay`，成功即 `saved`），`GET/PUT /api/episodes/storyvideo/<id>/screenplay`（读 / 整份替换；2026-09-10 起 PUT 会归一化 + 校验，见下一条），`POST /api/scripts/jobs/draft` 带 `use_screenplay: true`（没有剧本 400）。新建项目页「两步起草」勾选 → 「出剧本」→ 逐场编辑器（保存）→「拆镜」。
- **单集契约**：`spec.episode_contract = {premise, hook, cliffhanger, must_pay_off[], carry_over[]}`（`contract.normalize_contract`：字符串 / 列表（一行一条或分号分隔的字符串也收），不认识的键 SpecError，五项全空归一成没写；老项目零变化）。起草 schema 加 `episode_contract`（非必填），系统提示词加第 12 条「先立契约再写镜」；`assemble_spec` 宽松清洗后带上。**不进任何提示词**（金丝雀守着），事后由 `lint.check_contract` 核对正文：钩子在前两镜的画面/美术/动作/台词/beats 里找不到、集尾在最后一镜找不到、`must_pay_off` 的某条任何一镜都找不到 → 「全集：…——…」告警。判据 `contract.mentioned`：原句或按标点拆的 ≥2 字词原样出现，否则中文二字片段 / 英文词的命中比例 ≥ 0.35（契约是概括句、正文是具体动作，同义改写只剩三四成重合；宁可漏报）。问题卡分类 `contract`（「契约」），排在遮挡区之前。`revise.GLOBAL_KEYS` 加 `episode_contract` / `series`：草稿 `PUT …/globals` 直接改，制作中走 revise（不编译进请求，任何镜都不会重出）。详情接口带 `episode_contract` / `series`；概览页「单集契约」卡（五个字段 + 系列 id / 集数，「保存契约」）。
- **系列交接**：`spec.series = {id, episode_no}`（`normalize_series`：文件名安全的 slug、正整数集数）。记忆文件 `content/series/<series_id>.json = {id, episodes[{episode_id, episode_no, title, contract, cast{id: {name, block}}, established[], open_threads[], updated_at}]}`，`established` = `must_pay_off` 里 lint 在正文找得到的（`contract.payoff_status`），`open_threads` = `carry_over`，cast 收所有有 block 的成员。`POST /api/episodes/storyvideo/<id>/series/handoff` 写入/替换本集条目（按 episode_id），导出交付包时若填了系列顺手做一次（失败不拖累导出）；`GET /api/series`、`GET /api/series/<id>`。起草（剧本或拆镜）带 `series_id`（+ `episode_no`）时，`series.previous_entry`（集数比它小的最大一集；没给集数取最新）压成用户提示词「上一集交接」段：上一集结尾、未决事实（要接住）、已成立的事实（不得矛盾）、延续的角色（**block 原样照抄**）；草稿回来后 `apply_cast_memory` 把同 id 角色的 block 再用记忆覆盖一次——模型改写了也拉回来；`spec.series` 由路由写入。新建项目页「系列（可选）」下拉（`/api/series` 或新建，选已有系列集数预填最新 + 1）；概览页「系列交接」按钮。
- **剧本编辑器（2026-09-10）**：剧本不再是一个裸 JSON 编辑框——`storyvideo/screenplay.py` 给它一套结构与静态检查，前端 `components/ScreenplayEditor.vue` 是新建页两步起草和项目页「剧本」标签（`/drama/p/:id/screenplay`）共用的同一个逐场编辑器。
    - **归一化 / 校验**（`normalize_screenplay`，落盘前跑，形状不对抛 `SpecError`、接口层 400「剧本没通过校验，没有保存：…」）：场次 id 缺了按 `S01…` 补、不许重复；场景标题不能空；对白的 `who` 必须是人物表里的 id 或角色名（`narrator` / 旁白除外）——写错名字等于这句台词没人说，拆镜时会静默丢掉；节拍每条都是有内容的一句话（空行直接丢，界面上刚加出来的行不算错）；契约复用 `contract.normalize_contract`，但五个键永远都在（编辑器要稳定的输入框）；`role` / `look` / `change` / `cue` 这些老字段原样留着。出剧本的 job 落盘前也跑一次，归一化失败就存原样——那次调用是真花过钱的，绝不能丢。
    - **lint**（`lint_screenplay`，告警不是门禁，形状是「scene S02: 影响——改法」/「cast ye: …」/「全集：…」，问题卡分类 `screenplay`「剧本」）：① 一场既没有节拍也没有对白；② 场景标题看不出地点 / 时间（`place` / `time` 单独写了也算，内/外与日夜词都认）；③ 人物表里的人一句台词也没有、节拍里也没点名；④ 契约的开场钩子在第一场看不见；⑤ 集尾压力没落在最后一场（④⑤ 判据复用 `contract.mentioned`）；⑥ 超过 12 场的软提醒。lint 对没归一化过的原始文件也成立（哪一处形状不对就跳过那条判据），所以 GET 从不因为文件脏而打不开。
    - **接口**：`GET/PUT …/screenplay` 都返回 `{episode_id, screenplay, warnings, problems, stats{scenes, dialogue_lines, words}, text, path}`；出剧本 job 的 result 也带 `warnings` / `problems`；项目详情多一个 `has_screenplay`（项目页「剧本」标签据此显示 ✓ 还是空状态）。
    - **界面**：剧名 / 梗概、契约卡（前提 / 钩子 / 集尾 + 两组清单）、人物表（id / 名 / 外形）、场次卡（标题、地点、时间、节拍增删改与上下移、对白表格（说话人从人物表里选）、备注，场次本身可上下移 / 在下面插一场 / 删）。告警顶部汇总一份、逐场的再贴在那一场里。项目页那颗「拆镜」按钮按这份剧本起**一个新项目**（剧本先复制到新编号下，拆完存盘跳到新项目的镜头页）——当前项目已经有拆好的镜头、可能还花过钱，不会被覆盖。
- **未验证**：没有 key，两步起草的真实质量（剧本→拆镜是否真的不改剧情、不加角色）、契约填得准不准、上一集交接注入后角色是否真的不漂，都没跑过；`mentioned` 的片段比例是启发式，会漏报同义改写、也可能把碰巧共用二字词的句子当成已兑现。

## 31. 形态卡（2026-09-08，融合 drama-skills）

drama-skills 的形态卡回答"选了这种形态之后，可执行文本变成什么样"——它的六张卡按制作形态分（实拍 / 二维动态漫 / 国漫二次元 / 风格化三维 / 水墨 / Q 版），每张的核心是**运动预算**（哪些必须全动、哪些用 hold / 局部循环 / 剪辑承接）和**必须逐镜传递的东西**；交付档案那一篇只定槽位语义、不定数字。我们取判据不取语法：卡按**交付形态**分（观众在哪种屏上看到什么），把"运动预算"压成一句话进每镜视频提示词，"必带项"进起草提示词并由静态检查核对，"默认值"只补缺省的生成参数。**五张卡是本项目按它的判据加我们自己的字幕 / 安全区经验推出来的，不是它原文的蒸馏**（它没有"竖屏短剧 / 口播"这一层；个别句子沿用了它的原文，见 `docs/third-party-notices.md`）。

- **数据**：`skills/ai-short-film/scripts/form_cards.json`（≤ 15 KB，`_meta` + `cards[]`，随包打进 `desktop/storystudio.spec`）。每张卡：`id / name / aliases / defaults{aspect_ratio, resolution, shot_seconds[lo,hi], subtitles, max_shots_per_minute, hook_seconds, portrait} / must_have[] / motion_budget / english{name, motion_budget, must_have[]}`。五张：`vertical_drama` 竖屏短剧、`horizontal_short` 横屏短片、`anime_vertical` 竖屏动画、`talking_head` 口播 / vlog、`product_story` 品牌情景。
- **字段**：`spec.form`，可选，id / 中文名 / 英文名 / 别名都认，`normalize_spec` 统一成 id；空串、非字符串、找不到卡 → `SpecError`（`form references unknown form card: …`）。没写的老项目零变化。
- **模块**（`storyvideo/forms.py`）：`form_cards()` / `find_form()` / `normalize_form()` / `form_defaults(spec)` / `apply_form_defaults(draft)`（只补 `image_params.aspect_ratio`、`video_params.aspect_ratio` / `resolution`，卡要求字幕且 draft 完全没有 `subtitle` 块时补默认样式；**明写的值永远不动**）/ `motion_budget_sentence(spec)`（按 `prompt_language` 选中英文）/ `subtitles_off()` / `card_summary()`。
- **编译**：`video_prompt_parts` 多一个 `form_budget` 部件；通用方言接在揭示句之后、表演句之前，MiniMax H3 / Seedance 方言同样紧跟揭示句。首帧不带（它管的是动，不是画）。没写 form 的 spec 逐字节不变（金丝雀守着）。
- **起草**：`build_system_prompt(form=)` / `generate_draft(form=)` 在题材卡段之后再追加「形态卡：<名>」——画幅与分辨率 / 单镜时长与每分钟镜数 / 钩子窗口 / 字幕 / 必带项 / 运动预算，收尾一句「按这张卡定画幅、镜次节奏和必带项；运动预算句会自动进每一镜的视频提示词，motion 里不用重复写；卡里的内容不出现在交付物里」。不给时逐字节不变；给了找不到 `ValueError("不认识的形态：…")`，接口层建 job 前 400。`assemble_spec(form=)` 写 `spec.form` 并让通用默认里的画幅 / 分辨率先让位、再由 `apply_form_defaults` 补——所以横屏短片起草出来就是 16:9。
- **lint**（`lint.check_form`，问题卡分类 `form`「形态」）：① 画幅与形态不符（`image_params` / `video_params` 的 `aspect_ratio` 方向、`delivery` 宽高方向；`adaptive` 不判）；② 首镜长于钩子窗口且窗口内既没开口（字幕起点，没写按 0.8s 估）也没 beat；③ 平均单镜时长跑出卡的区间、每分钟镜数超上限（全集不到 30 秒不外推）；④ 卡要求字幕而 `subtitle.render_mode` / `display` 写成 `none` / `off`；⑤ 竖屏形态里远景 / 大全景之后两镜内没有更近的景别（中英景别词都认）。都是「全集：…——…」/「shot S1: …——…」形状，英文规则在 `i18n_rules`。
- **接口与界面**：`GET /api/scripts/forms` → `[{id, name, aliases, defaults, must_have, summary}]`；`POST /api/scripts/jobs/draft` 接受 `form`，存进 job `params`；概览 summary 多 `form: {id, name} | null`。新建项目页「形态（可选）」下拉（首项「（不指定）」，选中后一行摘要：画幅 · 单镜时长 · 每分钟镜数 · 字幕）；概览页头部的「形态」标签是个下拉（选项来自 `/api/scripts/forms`，首项「（不指定）」= 删键），改了走 `PUT …/globals`（`revise.GLOBAL_KEYS` 含 `form`，id / 名 / 别名都认、落盘统一成 id，不认识的 400「不认识的形态：…」），制作中走 revise——运动预算句进视频提示词，impact 会算出所有镜的视频要重出；题材（genre）只在起草时用、不存进 spec，所以没有对应的下拉。问题卡分类多「形态」。
- **未验证 / 没做**：运动预算句只是提示词文本，模型会不会真按"每镜一次机位运动、人物幅度小于机位"来动，要拿 key 跑片核对；装配层目前没有"关字幕"的开关（只有 `display` 两种），④ 只查显式声明；形态不进镜头编辑器（改形态在概览页头部的下拉）。

## 30. 参考槽位用途与尾帧续接（2026-09-08，融合 drama-skills）

两件事都关于「参考图到底在管什么」。以前首帧请求把参考图按固定顺序塞进去（上一镜首帧 → 出场角色定妆 → 道具），提示词里一个字不说——模型不知道第 1 张只该借场景、第 2 张只该借脸，常见事故是把上一镜的姿势和机位一起抄过来。取 zenstory drama-skills「参考图四态」的判据（每张参考图说清**取什么、控制什么、不得控制什么**），不取它的标记语法；再把尾帧续接落成运行时（视频过检后自动抽最后一帧给下一镜用，连同抽帧位置和「远景尾帧不直接接近景」两条约束）。

- **参考槽位用途**：`shot.reference_roles = [{"target": "cast:lin", "use": "只取脸", "control": ["脸", "发型"], "ignore": ["服装"]}]`。`target` 只能是本镜真的会带的参考图：`prev_frame`（`ref_frame` 的首帧，或尾帧占的依赖槽）、`tail`（`ref_tail` 作参考图）、`cast:<id>` / `cast:<id>@<变体>`（有锚图的出场引用；`cast:lin` 也能落到本镜写的 `lin@rain` 上）、`prop`（`prop_ref`）；场景块没锚图不占槽，写了就是 `SpecError`（措辞与本文件其他校验一致）。`use` 自由文本，`control` / `ignore` 是方面词列表（脸 / 发型 / 服装 / 姿势 / 机位 / 光线 / 场景 / 色调，可自定义）。
- **编号必须是真的**：`prompts.reference_slot_plan` 按 `frame_reference_asset_ids` 的同一顺序排槽（依赖槽 → 出场顺序的定妆 → 道具），两个出场共用一张锚图只占一个槽（请求里 `dict.fromkeys` 去重，计划里同一个 index）；`reference_roles_by_slot` 给 `[(槽位序号, 标签, role)]`。编译成「参考图1（上一镜 S1 首帧）：只取场景与光线；不得控制姿势、机位。参考图2（林小满定妆）：脸、发型逐字对照；不得控制服装。」追加在首帧提示词末尾（英文 `Reference 1 (first frame of S1): …; must not control pose, camera position.`）。没写的镜逐字节不变，金丝雀守着；`frame_request_data` 只在声明了 `ref_tail` 时多一个键，老项目 `request_hash` 不动。
- **尾帧续接**：`shot.ref_tail = {"shot": "S1", "mode": "reference" | "direct"}`（裸字符串 = reference）。源镜必须在本镜之前（尾帧要等它的视频）；同时写 `ref_frame` 时两者必须同镜（尾帧取代参考镜的首帧）。源镜**视频过检**（technical 通过、pass / waive、未 stale）之后：`runtime._ensure_tail_frame` 用 `ffmpeg -sseof -0.15 -frames:v 1` 抽到 `<output_root>/04_tail/<源镜>.png`（缩放到交付宽高；旁边 `<源镜>.json` 记源视频 sha256，源视频换了才重抽）。`reference`：这张 PNG 走 `_upload_dependency` 成为首帧请求的**第一个参考槽**（在定妆之前）；`direct`：`_satisfy_frame_from_tail` 把它拷成 `01_frames/<镜>.png`，账本 `mark_succeeded(result_url="tail:S1")`、安装标记带 `source`，不出图、`attempt` 不加，视频阶段照常。
- **调度**：runtime 各阶段仍严格按序，但 `_run_unlocked` 在恢复在途任务之后、任何新提交之前算 `tail.tail_gated(spec, qc)`——源镜视频没过检的 `ref_tail` 镜从本轮选择里拿掉，剩下的按现有「按镜选择」路径走（首帧过检 → 该镜视频），全部被推迟就什么都不提交；`_result` / 预估 / 概览都带 `tail_pending = {镜: {source, mode, reason: "等 S1 视频过检后抽尾帧"}}`，预估里这些镜既不算待生成也不算已有，弹窗按行标出；概览「下一步」在缺首帧全在等尾帧时改成「等尾帧」，概览页在失败卡旁多一张「等尾帧」卡（每镜一枚「S3 ← 等 S1 视频过检」标签，直接当首帧的标出来，点了跳到镜头页；没有在等的镜就不显示）。下一轮源镜过检后自动接上。**传染**：`_dependent_closure` 把接尾帧的镜算进源镜首帧重拍的影响范围；源镜**视频**重拍时 `_apply_retake_locked` 逐层把接它尾帧的镜的首帧与视频作废并归档重置（尾帧变了，接它的首帧就是旧的）。
- **lint**：`check_reference_roles`——`control` 与 `ignore` 同一方面、`prev_frame` / `tail` 指向本镜没有的依赖槽、同一参考图两条用途、条数超过本镜实际会带的参考图数、能力表给了 `max_reference_images` 时超上限（`genkit.capabilities.max_reference_images(model)`：静态表按模型前缀记公开文档写明的数字——gpt-image 16、Seedream 4.0 10、SeedEdit 3.0 1，Seedream 3.0 t2i 不收图；不确定的（Flux Kontext 等）不记；OpenRouter 目录的 `input_references.max` 也进同一个键。spec 写了具体出图模型 id 才响，`@image` 不解析；告警点名模型和上限。能力检查接口另按设置里的模型逐镜数参考图：超的 warn 点名镜与张数，没超但有镜带参考图给一行 info）；`check_tail_relay`——形状、源镜不存在 / 不在前面、与 `ref_frame` 不同镜，以及远景接近景的景别检查：源镜 `shot` 是大全景 / 远景而本镜是近景 / 特写 → 提示在两镜之间加一张中景桥接帧：远景尾帧里的脸太小，直接拿来接近景容易换脸（尾帧本身糊不糊查不了，只能按景别提醒）。`continuity.check_continuity` 把 `ref_tail` 当参考链的一环：没写入口状态提醒（入口就是源镜出口那一刻）；**接缝的「上一镜」按尾帧关系取**（`continuity.previous_for(spec, shot)`：写了 `ref_tail` 且源镜在前面 → 源镜，否则列表顺序的前一镜）——起止边界逐字段比对、自由文本接缝、`lint.check_axis` 的越轴比对都走它，S3 接 S1 尾帧时和 S1 的出口 / 画面侧比，不和夹在中间的 S2 比。问题卡分类 `reference`（「参考图」）。
- **接口与界面**：`ShotEdit.reference_roles`（None 不动，`[]` 清；每条 target 非空、use / control / ignore 至少一项、不认识的键 400，target 是否真的落在参考图上由写盘前的 spec 校验把关，同样 400 不写盘）、`ShotEdit.ref_tail`（镜号或 `{shot, mode}`，`""` / `{}` 清；切成 direct 时 `tail` 用途要一起清）；`shot_detail` 带 `ref_tail`（归一化对象或 null）、`reference_roles`、`reference_slots`（本镜首帧请求会带的参考图清单，给编辑器列下拉）。镜头页「尾帧续接」（源镜下拉只列前面的镜 + 模式单选）和「参考图用途」（参考图下拉跟着表单里的尾帧 / 参考镜选择走，其余沿用后端算的槽位；取什么 + 控制 / 不得控制 chip，可自定义）。
- **不是保证，且未验证**：说明句只是提示词文本——模型会不会真「只取场景不取姿势」，尾帧作参考 / 直接当首帧的身份保持效果，都要拿 key 跑真实模型核对。本机真实 ffmpeg 抽帧（`-sseof -0.15`、缩放到交付尺寸、临时名落盘再改名）有测试；运行时推迟 / 接上 / direct 不花钱 / 视频重拍传染用假供应商验证。参考图上限只有静态表里那几个按文档记的数字，没实测过服务商真会丢掉超出的图还是直接报错。

## 29. 音色对比角色（2026-09-08，融合 drama-skills）

取 drama-skills 音色规格里的一条判据：**选音色要拿角色自己的台词并排听，不是各听各的默认样句**——音色库的「试听」念的是通用样句，听起来都行，绑到角色上念台词才知道气质对不对。不取它的"音色卡"Markdown 写法；落成一个不改 spec 的对比接口 + 角色页一块面板。

- **接口**（`webui/routers/voices.py`）：`POST /api/episodes/storyvideo/<id>/cast/<cast_id>/voice-compare`，body `{voices: [音色 id × 2–6], text?}`。`text` 空就取该角色在剧本里的第一句台词（`shots[*].lines[*]` 里 `who == cast_id`，按 `prompt_language` 取 `zh` / `en`，另一种兜底）；角色没台词又没给文字 → 400「这个角色没有台词，填一句试听文字」。数量不在 2–6、重复 id → 400。每个音色走 `genkit.voices.synthesize_with_profile`（和音色库试听、旁白轨同一条路：edge 免 key，`settings` 用模型设置里的语音来源），线程池并行、每个音色单独超时（`COMPARE_TIMEOUT_SECONDS = 90`）；**一个失败不拖累整批**——失败条目带 `error`（库里没有 / 只有描述没 TTS / 网关错误 / 超时，英文界面翻译），返回 `{cast_id, text, items[{voice_id, name, url | error}]}`。
- **文件**：`<output_root>/09_voice_compare/<cast_id>/<voice_id>-<hash8>-<毫秒>.mp3`，hash 取文字 + engine/voice/rate/pitch，**永不覆盖**（和试听样本同一条纪律：Windows 上 `<audio>` 握着旧文件时删不掉）；同目录 `index.json` 记每批 `{at, text, items}`（最多 50 批）。`GET .../voice-compare` 列已有批次（文件被清掉的条目跳过，库里已删的音色标 `in_library: false`）并带 `default_text`（第一句台词）与当前 `bound`；`GET .../voice-compare/file/<name>` 流 mp3，文件名必须匹配 `<id>-<hash8>-<数字>.mp3`，含路径分隔符或以点开头一律 404（和配乐文件 GET 同一套防穿越）。
- **采用 = 绑定**：不加新接口，「用这个」调现有 `POST .../cast/<cast_id>/voice`（拷贝语义、开始制作后拒绝 / 走 revise 都照旧）。对比本身不写 spec、不进账本，制作中也能用。
- **界面**：角色页「音色」卡下方「对比试听」——只列带 TTS 音色的档案（复选，最多 6 个）、可选自定义文字（占位符提示会用哪一句台词）、结果按批次一行卡片（名字 + `<audio controls>` + 「用这个」；失败卡显示原因、按钮禁用；已绑定的卡描绿边）。
- **未验证**：真实 edge-tts 下 6 路并行合成的耗时与限速（测试注入合成器）；`settings` 引擎的网关并发限制可能让部分条目报错——按设计是逐条报错不整批失败。

## 28. 观众可见性与揭示保护（2026-09-08，融合 drama-skills）

短剧靠「观众此刻还不知道」吃饭：凶手的脸、无名指上的戒指、一句不能提前说出口的台词。以前这件事只能靠人写正文时自己记着——一镜写了「陈默背对镜头」，下一镜的美术随手写「陈默转身」，揭示就提前泄了；出图模型更不知道什么该藏。取 zenstory drama-skills「观众可见性」的判据（每个未揭示的事实要点名**它是什么、靠什么泄露、到哪一镜才能露**），不取它的 markdown 标记；落成镜头字段 + 编译规则 + 静态检查。

- **字段**：`shot.withhold = [{"fact": "凶手是陈默", "carrier": "脸", "until": "S8"}]`。`fact` 是观众此刻还不能知道的事实（中文自由文本，必填）；`carrier` 是会泄露它的载体（脸 / 手上的戒指 / 台词，可选）；`until` 是从哪一镜起可以揭示（可选，空 = 整集都不揭示）。`normalize_spec` 校验：必须是列表、每条是对象、`fact` 非空、`carrier` / `until` 是字符串、`until` 指向存在的镜（收集完所有镜再核对，因为它通常指向后面的镜）——错了 `SpecError`，措辞与同文件其他校验一致。
- **注入范围**（`prompts.active_withholds`）：声明镜 W 本身及其后、`until` 之前（不含 `until`）的每一镜；到了揭示镜及之后不再注入；两镜各自声明同一件事，一镜只出现一次；`until` 早于 W 时编译器什么都不注入（lint 会报）。
- **排除句**（`prompts.withhold_clause`）：中文首帧「不得揭示：凶手是陈默——脸全程不入画或被遮挡、看不清。画面任何位置都不透露这一点。」，视频「…画面、动作与台词都不透露这一点。」；多条用「；」连，尾句改成「这些信息」；没写载体只说事实。英文 `Do not reveal: the killer is Chen Mo — the face stays out of frame or hidden and unreadable. Nothing anywhere in the image gives it away.` / `Nothing in the picture, action or dialogue gives it away.`。位置：首帧在轴线句之后、全局机位句之前；视频是 `video_prompt_parts` 的 `withhold` 部件，通用方言接在可读文字句之后，MiniMax H3 / Seedance 方言都放在表演句之后、文字政策句之前。没声明的 spec 一个字不变（金丝雀守着）。
- **lint**（`lint.check_withhold`）：① `until` 指向不存在的镜（按整集查）或不在本镜之后（只报不查）；② 揭示点之前的镜（含声明镜）的 `art` / `motion` / 台词（中英）/ beats 三栏里出现了 `fact` 原文或它有辨识度的词（按标点/空格拆，≥2 字）→「shot S3: 正文写到了「凶手是陈默」的内容，但揭示要等到 S8——删掉或改成不露信息的写法」（一镜一事实只报一次；到了 `until` 及之后不查）；③ `carrier` 与揭示点之前某镜的 `readable_text` 载体相同或互相包含 → 「既是泄露载体又被声明为必须可读的文字——观众会直接读到」；④ 形状不对。问题卡分类 `withhold`（「揭示」），排在最前面，因为这些告警里会出现「正文」「台词」「可读文字」这些别的分类的词。
- **接口与界面**：`ShotEdit.withhold`（None 不动，`[]` 清掉；`fact` 非空、不认识的键 / 非字符串 400；`until` 是否存在由写盘前的 spec 校验把关，同样 400 不写盘）；`shot_detail` 原样带出；制作中经 revise 的 `shot_edit` 复用 `_apply_shot_fields`。镜头页「先不揭示」列表：事实 / 载体 / 揭示点（下拉只列本镜之后的镜，首项「整集不揭示」）；提示里写明这只是提示词文本。
- **不是保证**：排除句只是提示词里的一句话，模型会不会真的把脸藏好、把戒指挡住，要看真实出片——没有 key 验证过。它替代不了「背对镜头」「手插在口袋里」这类写进正文的可见事实；正文与揭示保护要一起写，lint 只能查泄露，查不出「藏得够不够」。

## 27. 交付面遮挡区（2026-09-08，融合 drama-skills）

竖屏短视频真正给观众看的不是整张 9:16：平台的状态/标题栏压着顶部，点赞/评论/分享一列压着右侧，文案与进度条压着底部。首帧里放在这些位置的可读文字（手机屏上的倒计时、合同签名栏）和烧进去的字幕，到了平台上就被挡住。取 zenstory drama-skills「交付面」的判据（先知道哪里会被挡，再决定文字与字幕放哪），不取它的语法；落成 spec 字段 + 预设表 + 静态检查 + 预览遮罩，不生成任何东西。

- **字段**：`delivery.overlay_preset`（`douyin` / `kuaishou` / `shipinhao` / `none`）或 `delivery.overlay_regions: [{label, x, y, w, h}]`（交付面的比例，0–1，x/y 是左上角）。`overlays.regions_for(spec)`：显式区域优先，其次预设，都没有 = 空——老项目两个字段都没有，编译结果与 `source_hash` 逐字节不变（金丝雀守着）。预设是典型竖屏布局的粗略比例（`overlays.PRESETS`，抖音：顶部 9% / 右侧 16%×36% / 底部 22%），平台改版只改这张表。校验在 `spec.normalize_spec`（`overlays.validate`：预设名、数值 0–1、宽高 > 0、不出画面、最多 12 块、只认五个键），错了 SpecError；后期块接口同一套校验先报。
- **lint**（`lint.check_overlays`）：`readable_text.position` 的方位词（顶部/底部/上方/下方/左/右/居中/中央偏上/right、bottom…）粗略映射成矩形（`overlays.position_rect`：只写横向时纵向取中段，只写纵向时横向取中段，一个方位词都没有的不判），与遮挡区相交就告警「可读文字「…」放在「底部」，会被交付面的「底部文案与进度条」挡住——挪到安全区或换载体」；字幕块按 `subtitles.effective_style` 算位置（底部：1 − 安全边距 − 块高；双语更高），落进遮挡区报整集一条，并给出要调到多少的底部安全边距。问题卡分类 `overlay`（「遮挡区」）。
- **接口**：`GET …/post-layers` 多一个 `overlays = {preset, custom, regions, presets:[{id,label}]}`；`PUT …/post-layers` 的 `delivery` 只收 `overlay_preset` / `overlay_regions` 两个键（空串 / 空数组 = 删掉该键，宽高帧率原样保留；带别的键 400）。制作中同字幕样式一样走 amend：`delivery` 整块不是后期块（宽高/帧率是装配合同），但 `runtime._only_overlays_changed` 在两边去掉 overlay_* 之后相同时放行。
- **界面**：旁白与插卡页「交付面遮挡区」卡（`OverlayRegionsEditor`）：布局下拉（预设 + 自定义），自定义是逐块的名称/x/y/宽/高百分比表（从预设切过去时把预设的块拷进来当起点，越界禁止保存），右侧 9:16 小预览；镜头页首帧预览叠半透明斜纹矩形（`OverlayMask`），「显示遮挡区」开关记在 localStorage（读写都兜住异常）。
- **未验证**：各平台的真实遮挡比例随版本与机型变，预设只是"够提醒"的粗略值；位置描述的映射是启发式，「屏幕上」这类不表示画面方位的词不会触发。

## 26. 题材卡（2026-09-08，融合 drama-skills）

取 zenstory drama-skills 的 12 张题材卡（MIT），节选压缩成 `skills/ai-short-film/scripts/genre_cards.json`（`_meta` + `cards[]`，每张卡：`id/name/aliases/core/pressure/strategies/payoff/grains/hooks/phases{early,mid,late}/pitfalls/disallowed_drift/production_notes`，每张约 600–900 字；多数字段直接摘录原卡原句，有删节与合并，许可与版权声明见 `docs/third-party-notices.md`）。十二张：亲子隐秘、仙侠修真、动作任务、古装权谋、复仇打脸、家庭关系、悬疑规则、生活流、破镜重圆、职场喜剧、豪门婚恋、身份错位。

**只进系统提示词，不进交付物**：`scriptgen.build_system_prompt(genre=)` / `generate_draft(genre=)` 给了题材（id、中文名或别名，`find_genre` 解析）就在提示词末尾追加一段「题材卡：<名>」——核心 / 压力来源 / 人物策略与信息权限 / 观众回报 / 场面颗粒 / 集尾钩子 / 前期·中期·后期 / 常见坑 / 不能漂成，每项一行，收尾一句固定指令「按这张卡定压力来源、场面颗粒和集尾钩子；不要漂移到别的题材；卡里的内容不出现在交付物里，只用来指导写作」。不给题材时提示词逐字节不变（金丝雀与老项目 request_hash 不受影响）；给了但找不到抛 `ValueError("不认识的题材：…")`，接口层在建 job 前就 400，不静默忽略。`production_notes` 不注入（它是给制作排期看的，不是给起草模型的）。

接口：`GET /api/scripts/genres` 返回 `[{id, name, aliases, core}]` 一行摘要；`POST /api/scripts/jobs/draft` 接受 `genre`，存进 job `params`。界面：新建项目页「题材（可选）」下拉（首项「（不指定）」），选中后在下拉下方显示该卡的 `core` 一句话。题材不写进 spec——它是起草时的写作指导，项目建成后改题材等于重新起草。

## 25. 配乐时间线层（2026-09-08，融合 drama-skills VID-14）

单镜视频不带配乐；配乐是后期时间线层：`post_graphics.music = [{file, start, end, gain, duck, fade_in, fade_out, label}]`，本地音频（上传到 `content/assets/<项目>/music/`）按绝对时间铺进后期合成版，有台词的镜、旁白句、配音句的时段自动压到 `duck`（默认 0.3），淡入淡出，结尾按时间线截断（`amix duration=first`，配乐再长也不拖长成片）。后期块，制作中可改。音乐生成不做，先只接本地文件。

## 24. 重拍处置与重投纪律（2026-09-08，融合 drama-skills）

取 zenstory drama-skills 两条判据：审查校准的**处置五分法**（保留 / 后期处理 / 局部编辑 / 重新提交 / 改写——先定处置再谈修订，"不要用增加形容词的方式回应重复缺陷"）和生产纪律 §五.6 的**重投上限**（"同一输入连续原样重投不超过两次，之后改内容或退回负责环节"）。不取它的 Markdown 记账语法；落成裁决字段 + 账本计数 + 一道 409。

- **裁决元数据**（`storyvideo/qc.py`）：`review()` 多两个可选参数 `defect`（身份漂移 identity_drift / 接缝断裂 seam / 台词错 dialogue / 文字残留 text_residue / 动作不对 motion / 审核拦截 moderation / 画质 quality / other）和 `disposition`（保留 keep / 后期处理 post / 改文字重出 edit / 原样重提 resubmit / 换模型对比 model）。写进 qc item（跟 decision 同生死），并追加到 item 的 `history[]`（最多 20 条，换了新产物也保留——"上次为什么退回"是审新产物时最有用的上下文）。`item_meta()` 给界面：当前裁决 + `last_retake` + `same_defect_retakes`（同一缺陷退回过几次，第二次就该改结构）。老 qc.json 没这些键照常加载。
- **处置 → 裁决**（`webui/routers/episodes.py` review 接口）：`keep` 记 `pass`、`post` 记 `waive`（都带缺陷/原因，"接受但不伪装成通过"）；只有 `edit` / `resubmit` 是真的重拍；`model` 不落账（400，去镜头页「首帧对比试跑」出候选、采用时才进账本）。
- **重投计数**（`RunStore.attempts_for(run_id, step_id, request_hash=None)` → `{total, same_request, request_hash}`）：账本没有逐次提交的历史表，也不需要——`ensure_job` 把 `request_hash` 钉死在 (run, step) 上（元数据不同即 `RunCollisionError`），文字改动永远走「修订 → 新版本 → 新 run」，所以一个 run 里同一步的每次 `attempt` 都是同一份请求，`same_request == attempt`；调用方把当前编译出的指纹传进来做防御性核对，对不上算 0（宁可少拦）。`StoryVideoRunner.shot_attempts()` / `webui.generate.shot_attempts()` 分别在写侧和只读侧算首帧/视频两项；详情页每镜带 `attempts` 与 `qc_meta`（`generate.job_records()` 整集一次读账本）。**已知边界**：计数按 run（版本）算，修订到新版本后从 0 重新数，即使那一镜的文字没变。
- **重投纪律**（`runtime.RESUBMIT_LIMIT = 2`，`StoryVideoRetryLimit`）：重拍且处置是 `resubmit` 或没写处置、且 `same_request ≥ 2` → 在改动任何账本/QC 状态之前抛错；接口回 409 `{kind: "retry_limit", message, shot_id, gate, attempts}`，人话：「这一镜（S1）的首帧同一份提示词已经出过 2 次了——原样重提第 3 次通常还是一样；改画面/动作文字（改文字重出）、换模型对比（多模型对比），或接受当前结果」（英文界面按 `i18n_rules` 翻）。`force: true` 放行；`edit` 不拦（用户声明要改文字，改动本身走修订流程）。
- **界面**：镜头页「重拍」不再是一个裸 prompt，而是弹窗（`ModalHost` 的 `retake` 视图）：缺陷下拉 + 处置下拉 + 原因 + 「已出过 N 次（同一份提示词 M 次）· 上次退回：接缝断裂（原样重提）」。同一份提示词已出过 2 次、或上次退回记了缺陷时，默认处置改成「改文字重出」；切回「原样重提」时就地提醒（叠加形容词不算改文字）。`edit` 只记重拍、不马上重出，提示回编辑器改那一层（保存后走修订预告重出）；`resubmit` 记重拍并重新生成，遇 409 弹「仍然重提」确认后带 force；`model` 滚到右侧对比区。镜头列表与概览失败卡给同一份提示词 ≥ 2 次的镜打「首帧第 N 次 / 第 N 次」小标签。
- **未验证**：真实模型下"第三次原样重提确实还是一样"只是经验判断（drama-skills 也这么定），上限 2 是可改常量，没有做成设置。

## 23. 角色变体（2026-09-08，融合 drama-skills）

取 drama-skills `identity-vs-variant.md` 的判据（不取它的 markdown 语法）：**持续身份**（同一张脸、体态、永久特征）只写一份；**版本/状态**（服装、发型整理、妆容、伤势、污湿、年龄阶段、伪装）是同一身份下的变体，只写相对基底的差异；**一镜的事**（表情、姿势、视线、景别）交给镜头文本，不建资产。以前换一套衣服要复制一个 cast 成员——两份身份块、两张锚图、台词/配音/连续性各认各的 id，正是它警告的做法。

数据：`cast.<id>.variants = {<键>: {label, delta, asset?, shots?}}`；`shot.cast` 里写 `"lin@rain"`（成员 id + `@` + 变体键）表示这一镜用该造型。`delta` 必填、只写差异；`asset` 是变体自己的锚图（相对项目根，和成员锚图同一套解析）；`shots` 只是备注。校验：引用不存在的成员/变体、空变体键、键里带 `@` 都是 SpecError；归一化后的 `shot.cast` 原样保留字符串。

编译：首帧提示词里该成员的位置渲染 `block + "。" + delta`（英文 `block + ". " + delta`；基底已带句末标点就不重复），delta 恰好出现一次；参考图取变体 `asset`，没有就回落到成员的。视频提示词不带外形块，所以不受影响。所有按 id 找成员的代码（连续性锁的 `shots="all"` 范围、表演锁的"有人物"、lint 的点名/机位检查、`shots_using`、关系图、配音计划里的 `lines[].who`——`who` 永远是裸 id）统一经 `storyvideo.spec.split_cast_ref()` 拆掉后缀。没写变体的 spec 编译结果逐字节不变（金丝雀）。

检查（`lint.check_variants`）：定义了却没有任何镜用到的变体 → 提示；同一成员在**相邻**两镜用了不同造型、后一镜又没声明 `boundary_change` → 「造型在镜外切换」告警（中间隔了一镜的换装是正常剪辑，不报）。

接口与界面：`CastEdit.variants`（None 不动、`{}` 清空；delta 非空、asset 只收项目内相对路径、shots 是镜号列表）；`cast_view` 每个变体带 `asset_url` 和实际用到它的 `used_in`；`POST …/cast/{id}/variants/{key}/asset` 上传变体锚图（存 `content/assets/<项目>/<角色>_<键>-<摘要>.<ext>`，`stage=true` 只存文件）。变体是生成字段：制作中和 `block` 一样拒绝直接改，走 revise 的 `cast_edit`。角色页「造型变体」区（标题→按 id 规则生成键、差异、锚图、用到的镜）；镜头页出场芯片旁的小下拉在「基底 / 各变体」间切换，`form.cast` 存 `lin@rain`。

## 22. 结构化起止边界与轴线（2026-09-08，融合 drama-skills）

取 zenstory drama-skills 分镜模板的 `start_boundary / end_boundary` 六栏、关键帧可渲染检查表的"哪些与上一镜相同"和 blocking-playbooks §1b 的轴线记法——取判据，不取它的 Markdown 语法。自由文本 `entry_state / exit_state` 原样保留（老项目提示词零变化，金丝雀守着）；结构化对象在时它才让位。

**边界**：`shot.entry / shot.exit = {positions, facing, gaze, hands, held_props, visible_state, same_as_previous:[key…]}`。每格是"不读上一镜也能理解的绝对事实"（谁在哪个锚点的哪一侧、朝谁、看哪、手在做什么、谁拿着什么、衣服/道具的可见状态）；真的没变不写「同上」，把 key 放进 `same_as_previous`。编译（`prompts.boundary_text / entry_exit_text`）成一句：`入口状态：位置——…；朝向——…；视线——…；手——…；持物——…；可见状态——…；与上一镜相同：位置、朝向。`（英文 `ENTRY STATE: positions — …; same as previous shot: positions, facing.`），首帧带入口句、视频首尾各一句，三种方言（generic / MiniMax H3「开场/收尾」/ Seedance）都从同一份 `parts["entry"] / parts["exit"]` 拿。`shot.boundary_change` 是一句来源说明（"伞在镜外已递给她"），只给检查看，不进提示词。

**逐字段比对**（`continuity.check_continuity`，上一镜 `exit` 与本镜 `entry` 都是结构化时）：`hands / held_props` 不同且没写 `boundary_change` → "持物/手部状态在镜外变了……要么在上一镜的唯一动作里完成，要么写 boundary_change 说明来源"（道具凭空易手是硬伤）；`positions / facing` 不同 → 软一档的"跳变"提醒；勾了「相同」两边却写得不一样、`same_as_previous` 指向上一镜出口没写的字段、第一镜勾「相同」、值里出现 同上/不变/同前/见上 都告警。结构化入口算入口（`ref_frame` 缺入口、接缝只有一半的老规则照用）。

**轴线**：`shot.axis = {anchors:[甲, 乙], side: left|right, screen:{甲: left, 乙: right}, break: bool}`——轴线是两个可命名锚点的连线，锚点写 cast id（编译时换成名字）或直接写名字。编译成 `轴线：机位在林小满—招财猫连线的左侧；林小满固定占画面左，招财猫占画面右。`（`break` 再加"本镜有意越轴，新的机位侧从这一镜起沿用"），首帧放在入口句后、全局机位句前，视频放在入口句后。`lint.check_axis`：相邻两镜锚点集合相同、某人画面侧翻了或机位侧换了而没标 `break` → 越轴告警（正反打要互补：甲在左看向右，乙就在右看向左）；`side / screen` 取值不对告警；`shot / art / motion` 里裸写的方向短语（从右侧、向左、左边的…）→ 提示"裸写的左右一律指画面左右；指人物自身写「她的右手」"——正则只抓 向/往/朝/从/偏/靠+左右 与 左右+侧/边，身体部位（左手、右肩）、「左右为难」、已带主体（画面左、她的右、镜头右侧、屏幕左）的都不抓。

**接口与编辑器**：`ShotEdit` 加 `entry / exit / axis`（对象，None = 没动，`{}` = 清掉）和 `boundary_change`（文本，`""` 清掉）；形状校验 400（未知字段、值不是字符串、`same_as_previous` 不是已知字段名、锚点不是恰好两个不同的、side/screen 不是 left/right、break 不是布尔）；`shot_detail` 原样带出（没写 = null）。制作中的修改经 revise 的 `shot_edit` 复用同一个 `_apply_shot_fields`。镜头页「接缝状态」块：「结构化填写」开关（镜次已有 entry/exit 对象时默认开，否则仍是两块自由文本），入口/出口两列六格 + 入口的「与上一镜相同」勾选行 + 边界变化说明；「轴线」小表单：锚点甲/乙从本镜出场里选、机位侧、每人画面左/右 chip、有意越轴勾选。关掉结构化再保存会清掉对象，让自由文本重新权威。

## 21. 交付包（2026-09-08，融合 drama-skills）

取 zenstory drama-skills `project_tool.py export` 的思路：一个项目"拿得出手"的东西打成一个自包含的文件夹 + zip，输入与凭据一律不进包。`webui/export.py`，成片页「导出交付包」卡，后台任务（本地拷贝，不花钱）。

包的布局固定在 `<output_root>/09_export/<id>-<YYYYMMDD-HHMM>/`（同名 zip 在旁边）：`spec.json`（磁盘原样快照）、`prompts.md`（每镜：镜号/时长/出场 + 首帧提示词、负向词、视频提示词三段围栏块，复制即用）、`frames/<镜>.png`、`videos/<镜>.mp4`、`final.mp4`（后期版优先，否则装配版）、`voices/*.mp3`（07_vo 的旁白与配音）、`qc.json`（三道审片的裁决精简版：决定/审片人/原因/时间/哈希，不带本机路径）、`manifest.json`（id、标题、导出时间、程序版本、交付物种类、每镜有无首帧/视频、文件清单与大小）、`checksums.sha256`（包里每个文件一行 `<sha256>  <相对路径>`，含 manifest）。

**只拷白名单，不扫目录**：每个文件按 output_root 内的固定位置取，解析后必须落在 output_root 内、不跟符号链接。`~/.storystudio`（设置、密钥、音色库）、`content/assets`（锚图输入）、账本 sqlite、`manifests/`（带绝对路径的运行状态）都碰不到。同一分钟重复导出拒绝（不覆盖已交付的包）。下载端点只接受 09_export 里的纯文件名 zip，带路径分隔符或 `..` 一律 404。接口：`POST /export`（任务）、`GET /export`（列表 + manifest 摘要）、`GET /export/download?name=`。

## 20. 画面文字政策（2026-09-08，融合 drama-skills）

短剧常要观众读到证据文字：手机屏上的「剩余 4 天 23:59:58」、合同签名栏、榜单。以前编译器给每一镜的首帧都硬拼
「画面中绝对不出现任何文字」，视频提示词也一律禁文字，唯一出路是后期叠字。取 zenstory drama-skills 文字政策与
IMG-07 的判断（必须可读的文字与全局「不要文字」不能同时激活），不取它的语法：

- **声明**：`shot.readable_text: [{surface, text, language: zh|en|digits, position?}]`，镜头编辑器里「画面里必须可读的文字」
  列表；`[]` = 回到画面无文字。API（`ShotEdit.readable_text`）校验形状：载体与文字非空、语言在三选一里，错了 400。
- **首帧**：非空时尾句换成正面声明——「画面里唯一允许出现的文字：《…》，位于手机屏幕（画面中央偏上），字迹清晰可读；
  除此之外画面任何位置不出现其他文字、字母、数字或符号。」（多条用「；」连；英文项目同义英文）。
- **视频**：`video_prompt_parts` 多一个 `readable_text` 部件——「手机屏幕上的文字《…》保持清晰可读、内容不变。」
  通用方言接在锁句之后；MiniMax H3 / Seedance 方言的字幕禁令仍在（字幕是另一层），但改写成「不叠字幕；除上面指明的
  文字外没有其他可读文字」，不再说「画面里没有任何可读文字」。
- **不变量**：没声明的镜头，编译结果一个字都不变——`scripts/refresh_prompt_canary.py` 守着。
- **lint**（`lint.check_readable_text`）：单条超过 24 字（模型对长文字容易乱码，拆短或改后期）；`art`/`neg_extra` 里的
  「无文字 / no text」与声明打架（IMG-07）；整集一次提醒「可读文字要靠出图模型渲染，很多模型对中文字形不稳，出图后核对，
  不行就改走后期画面文字」——出图模型会不会渲染中文没法静态判断，所以只提醒不判定。
- **未验证**：真实出图模型上中文字形的成功率；「后期画面文字」（`notebook_text`）仍是兜底路径。

## 19. 连续性锁（2026-09-08，融合 drama-skills）

跨镜不变的事实（服装主色与形制、道具主色与材质、伤口位置）不能靠模型记忆，也不能以「参考帧已经交代过」为由从运动提示词里删掉——同一件毛衣第一镜蓝第二镜红，通常不是资产写错，而是没有任何一条提示词把颜色带进去。锁把这几个字固定下来，要求它**原样**出现在每条生效提示词里。只取判据，不取它的 markdown 语法。

- **字段**：`cast.<id>.locks = [{"surface": "淡蓝色粗针织羊毛毛衣", "shots": "all" | ["S1", "S2"], "note": "可选"}]`。锁属于角色 / 场景 / 道具；锁面用项目的提示词语言写（`prompt_language`），只留最小可辨识名词短语（颜色 + 材质/形制 + 物体），不含动作、状态、数量、剧情；会变的是状态，状态不上锁；一集通常只有几把。
- **注入规则**（`prompts.lock_clause`）：`shots="all"` → 成员在本镜 `cast` 里的每一镜；`shots=[...]` → 恰好列出的那几镜，成员不在本镜 cast 里也注入（被手挡住的道具、搭在椅背上的外套常不在出场名单里）。每个锁面一镜只出现一次，按 cast 顺序。首帧提示词：紧跟角色块之后一句「固定不变：A；B。」/ `Fixed across shots: A; B.`；视频提示词：`locks` 部件在三种方言里都紧挨在「锁」段之前。没有锁的 spec 一个字不变（提示词金丝雀守着）。
- **lint**（`lint.check_locks`）：逐镜编译首帧 + 视频，锁面必须在场——忽略大小写、换行当空格；英文锁面不能粘在别的词上（`chipped white mug` 不被 `unchipped white mug` 满足）；写在「不要出现」/ `Avoid:` 之后的不算；出现在负向词（`neg` / `neg_extra`）里反着报。另提醒锁面过长（> 40 字）、一个成员超过 5 把、范围里的镜不存在、形状不对。
- **接口与界面**：`CastEdit.locks`（None 不动，[] 清空；形状不对 400）；`cast_view` 带 `locks`。锁改的是提示词，是生成字段：草稿直接 PUT，制作中走 `_refuse_if_locked` → revise 的 `cast_edit`，影响判定按编译后的请求比较自然算出哪些镜要重出。角色页「跨镜锁定项」：锁面 / 全集或指定镜（镜号 chip 多选）/ 备注。

## 18. 正文静态 lint（2026-09-08，融合 drama-skills 的判据）

`storyvideo/lint.py`，全部是告警，走 prepare / 详情页现有通道：点名覆盖（正文点名了角色但 cast 没列，或列了没提；`cast.<id>.aliases` / `name_in_prompt:false`）、可生成性（内部状态词、否定式动作、精确拦截、一镜过多手部动作）、正文卫生（镜号、文件名、版本号、"同上"）、镜头功能重复（新增 `shot.purpose` 枚举，相邻镜同功能同景别）、机位高度缺失、beats 留白与未声明重叠、编译后提示词过长。取它的判据，不取它的语法。

## 17. 视频提示词方言（2026-09-08，融合 zenstory drama-skills）

同一份镜头事实（入口状态、唯一动作、台词、运镜、音效、锁、出口状态）按目标模型的官方提示词语法重排：`storyvideo/dialects.py`。
- `generic`：现在的散文段落，老项目缺省，提示词金丝雀守它。
- `minimax-h3`：官方三段结构 `integrated_multimodal_description: [Shot 1] …` / `overall_soundscape` / `non_diegetic_music: N/A`；说话人按 cast 顺序用稳定 ID `(S1)`，中文台词 `<d>[Chinese] 逐字</d>`，音效只进 soundscape，字幕禁令写成直接指令。
- `seedance`：`{角色用中文说："逐字台词"}`、音效 `<声音>`、默认「整段视频不加字幕」。

`video_params.prompt_dialect`：`auto` 按生效的视频模型选（`@video` 看模型设置），显式值覆盖，缺省 = generic。新建/示例默认 `auto`。概览页的能力检查卡显示当前语法；老项目一键切到自动（草稿直改，制作中走 revise 预告影响）。内容权威仍是 spec，方言只管写法。

## 16. 字幕样式（2026-09-08）

够做视频字幕就行：字体、字号、颜色、描边、位置、底色带、显示方式。`storyvideo/subtitles.py` 是唯一一份默认值和校验，装配（`assembly`）与后期合成（`post_layers`）都从这里取样式，起草与示例的默认也用它。`spec.subtitle` 是后期块（`POST_ONLY_KEYS`），不进任何付费请求，制作中也能改，改完重跑装配/后期合成即可。「旁白与插卡」页的「字幕样式」卡有本机可用字体下拉和一个按 1080 宽等比缩放的 CSS 预览（只画有字幕的那一段画面）。

## 14. 统一网关（2026-09-07）

需求：要一个已经对接好各家 API 的库，用自己的 key、按官方价付费。文本模型这层早有现成的（LiteLLM、new-api、one-api 这类 OpenAI 兼容网关），生图和视频没有成熟的统一库——所以产品里两条路并存：

- **直连适配器**（`genkit/adapters`）：火山方舟、MiniMax、OpenRouter、OpenAI 兼容各一份，一套契约，用户自己的 key，官方价。
- **统一网关**（`settings.gateway`）：设置页填一次网关地址和 key，「测试连接」拉网关的 `/models`，然后一键应用到勾选的角色——四个角色分别切成 OpenAI 兼容那一档（`openai_chat` / `openai_images` / `openai_speech` / `openai_video`），地址和密钥用网关的。角色配置仍是运行时唯一读的东西；网关只是批量填表的来源，不改装配逻辑。
- **视频角色**走新加的 `openai_video` 适配器：OpenAI Videos 形状（`POST /videos` → `GET /videos/{id}` → `GET /videos/{id}/content`），LiteLLM 等网关按这个形状转发 Sora / Veo / Runway 等。成品要带密钥才能拉，所以适配器自己拉进缓存，给运行时 `file:` 地址。按文档写的，没有真实跑过。
- **模型目录**多一份 `gateway` 来源：OpenAI 形状的列表没有模态信息，按 id 里的词猜角色（video / image / voice / text）；猜的角色只用于补全，能力检查只在 id 不存在时提醒，不拿猜的模态拦人。

## 13. 界面语言（2026-09-07）

中文是源文案，英文是翻译层，两边都不引入 key 体系：

- **前端** `frontend/src/i18n.js`：`t('中文原文', {占位})`，中文时原样返回，英文查 `locales/en.js`（按视图分片 `en.core / en.settings / en.shots / en.post`），缺翻译回落中文——界面永远不会出现空白或 key。模板里用全局 `$t`；常量表里的中文在使用处包一层，语言可以运行时切换。语言存 `localStorage`，侧栏有开关，首次按浏览器语言。
- **后端** `webui/i18n.py` + `i18n_rules.py`：前端每个请求带 `X-StoryStudio-Lang`，中间件放进 contextvar；英文时把 `/api/*` JSON 响应里承载文案的键（label / hint / message / advice / detail / note / reason / warnings / status…）按精确表 + 正则模板翻译，用户自己的内容键（title / text / zh / block…）不碰。「人话（原文）」形状只翻前半段，括号里的原文留给开发者。账本、spec、日志里存的仍是中文源文案。
- **不翻的**：提示词编译产物、镜头库/风格库内容、示例剧集、音色描述（那是发给模型的内容，跟界面语言无关），日志。

## 10. 非目标与风险

- 不承诺任何厂商的审核策略；审核拦截按「改写提示词或换镜」处理，规范里有清单。
- 不做实时协作；一个项目目录同一时间一个人操作（账本有文件锁）。
- 模型接口变动是最大的外部风险：适配器层薄、有替身测试，但真实联调必须周期性做。
- 桌面包在 macOS 的签名/公证与 pywebview 的 WebKit 兼容性未验证。
