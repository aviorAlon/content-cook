# 待做项（按里程碑）

status: 2026-09-07｜每项带验收标准；「已验证」指本机测试或真实账号跑过，「未验证」指只有替身测试或只写了配置。

## M8 · 内容创作工具（2026-09-09 定位调整）

需求：定位从 AI 短剧平台调整为内容创作工具，短剧只是其中一个功能模块。设计见 DESIGN §38。

第一阶段（外壳多模块化，短剧行为不动）：
- [x] 模块注册表 `webui/modules.py` + `GET /api/modules`（产品名、每模块项目数、planned 模块 `available: false`）；项目列表 / 详情 / 概览带 `module`；任务记录带 `module`，`GET /api/tasks?module=` 过滤。验收：`tests/test_modules.py`。
- [x] 产品名唯一来源 `webui/branding.py`（工作名「Content Cook · 内容创作工作台」），头部 / 窗口 / FastAPI 标题 / `document.title` 都从它读；包名、CLI、设置目录不改。
- [x] 前端外壳：工作台首页 = 模块卡 + 短剧区；短剧页面搬到 `/drama`（`/drama/new`、`/drama/libraries`、`/drama/p/:id/…`），旧路径重定向；侧栏分「全局」与「短剧」两块。验收：`frontend/tests/workbench.test.js`（模块卡、侧栏分组、重定向）。
- [x] 模块首页独立于工作台首页（2026-09-10 定：**分开**）。七个模块之后再把短剧首页嵌在 `/` 上就是把一个模块摆在别人头上：`/` 改成启动台（产品名 + 一句定位、模块卡、跨模块「最近」、在跑的任务、模块无关的「第一次用」三步），`/drama` 保持短剧模块首页不动，短剧那三步引导只留在 `DramaHome.vue`。验收：`frontend/tests/workbench.test.js`、`tests/test_recent.py`。说明见 DESIGN §38「工作台首页与模块首页分家 + 任务中心」。
- [ ] 待定：最终产品名（现在是工作名）。

第二阶段（按需要挑一个做，各自复用什么见 DESIGN §38 的表）：
- [x] 图片工作台 `image`（`docs/imagestudio-spec.md`，DESIGN §42）：图片集引擎的第三个模块（`MODULES` 加 `image`，编号前缀 `IM`），**没有新引擎**——只加三个模板 `freeform` / `model_compare` / `text_card`，列表 / 出图 / 挑选 / 后处理 / 打包 / 从不覆盖 / 任务全部复用。引擎唯一的改动是 `generate.py` 认 `item.meta.model`（多模型对比的 item 级模型覆盖，别的模板不受影响）；图卡叠字复用 `storyvideo.poster.render_poster`（多一个 `canvas_size` 参数，海报输出不变）。示例 `examples/image/lantern_night.json`，风格卡加了 `image.*` 的 `fits`。验收：`tests/test_image_studio.py`（25 条）、`frontend/tests/imagestudio.test.js`（9 条）、`tests/test_modules.py` 计数、`tests/test_webui_i18n.py` 英文覆盖、`tests/test_packaging_files.py` 示例随包。未验证：真实模型上的候选质量与多模型对比（没有 key）。
- [x] 单条视频 `clip`（`docs/clip-spec.md`，DESIGN §43）：一条提示词一段视频，【全片设定】【分段时间线】【衔接与收尾】三段式（判据来自 `docs/prompt-patterns.md` 的 P1–P3）。2026-09-10 前后端都落地：`src/clip/`（spec / prompts / lint / generate）+ `routers/clip.py` 全部契约接口（列表 / 编号 / 新建 / 示例 / 读写删归档 / lint / 首帧上传与生成 `clip-frame` / 出片 `clip-generate` / 采用 / 产物流）+ `/clip` 三个子页（写法三段式编辑器带时间条与实时提示词预览、出片带首帧三模式与历次片子采用、高级）。从不覆盖 + 请求哈希跳过 + 断点续等（服务重启接着等，只轮询不提交）。示例在 `examples/clip/`（一条 30 秒示例，零告警，随包）。验收：`tests/test_clip_{spec,prompts,lint,generate,router}.py`（88 条）、`frontend/tests/clip.test.js`（14 条）、`tests/test_modules.py` 计数、`tests/test_packaging_files.py` 示例随包。未验证：没有对着真实视频模型跑过——写法判据来自长单镜视频提示词的常见写法（`docs/prompt-patterns.md`），不是我们自己跑出来的。
- [x] 绘本 / 有声书 `picturebook`：统一画风的插画 + 按朗读长度排页的时间线 + 双语字幕。2026-09-09 前后端都落地并真机冒烟（示例书 → edge-tts 朗读 → 装配 102.8 s 成片）；未验证：真实插画模型、专业音色的时长与语速。
  - [x] 绘本模块前端：`/picturebook`（首页 / 新建 / 书页六个子页）、侧栏「绘本」块、模块注册表 `ready` 与项目计数。验收：`frontend/tests/picturebook.test.js`、`tests/test_modules.py`。说明见 DESIGN §38「前端（绘本模块）」。
  - [x] 绘本模块后端（2026-09-09，DESIGN §39）：`src/picturebook/`（spec / lint / prompts / pages / narration / timeline / assembly / draft / scriptsheet）+ `routers/picturebook.py` 全部契约接口（列表 / 编号 / 新建 / 读写删归档 / lint 问题卡 / 起草 / 插画 / 朗读 / 装配三个后台任务 `pb-*` / 产物流 / 上传 / 朗读稿导出 / 概览），示例书 `examples/picturebook/fox_three_socks.json`（零告警）。验收：`tests/test_picturebook_*.py`（真 ffmpeg 三页出片 1080×1920 + 1920×1080、假生图来源 / 假合成器 / 假文本模型）。未验证：真实插画模型、真实 TTS 时长手感（见 DESIGN §39）。
- [x] 表情与头像 `sticker` / 纪念写真 `portrait`（`docs/imageset-spec.md`）：共享图片集引擎——按模板出一组图、挑选、后处理、打包。2026-09-09 前后端都落地（七个模板、两个前缀共用一套接口）；未验证：没有对着真实生图模型跑过提示词。
  - [x] 表情与头像 / 纪念写真 前端（2026-09-09）：`/sticker`、`/portrait` 共用 `views/imageset/*`（首页 / 新建 / 清单 / 产物 / 高级），模块来自路由 `meta.module`；侧栏通用块、启动拉列表。验收：`frontend/tests/imageset.test.js`（15 条，api 全 mock）。说明见 DESIGN §38「前端（表情与头像 / 纪念写真）」。未验证：没有对着真实后端点过。
  - [x] 表情与头像 / 纪念写真 后端（2026-09-09，DESIGN §40）：`src/imageset/`（spec / templates / prompts / generate / post / export）+ `routers/imageset.py`（`make_router(module)` 一套处理器供两个前缀用），模块注册表两条改 `ready`，示例 `examples/sticker/xiaoman_emoji.json`、`examples/portrait/baby_100_demo.json`。验收：`tests/test_imageset_{spec,templates,post,generate,export,router}.py`（真 Pillow 的透明底 / 微信表情与头像各档尺寸 / 3:4 与 4:3 / 叠字，假生图来源的跳过与 force 与失败隔离，zip 布局 + sha256，两个前缀参数化跑同一套接口）、`tests/test_modules.py` 计数、`tests/test_webui_i18n.py` 英文覆盖。未验证：没有对着真实生图模型跑过提示词（见 DESIGN §40）。
- [x] 出图风格卡 + 图像方言（`docs/image-style-cards.md`）：一套共享卡库（本仓库按常见的摄影布光、插画技法与版式常识自写，出处见卡库 `_meta.basis`）+ 一层模型方言；imageset / 绘本 / 海报三处接线，老项目零变化。
  - [x] 前端（2026-09-09）：`components/StyleCardPicker.vue`（分类分组的卡片网格 + 色板小色块 + 「适合」筛选 + 锚句只读 + 「改成自写」）与共享卡库 `src/stylecards.js`（`loadStyleCards()` 只拉一次，方言取值与四条方言说明）；接线四处——图片集新建页（替掉风格预设下拉）、清单页顶部风格行（换风格提示要重出，PUT 整份）、绘本「角色与风格」页（`style_card`）、成片页海报卡（`style_card`）；图片集与绘本显示当前图像方言并可手选覆盖。验收：`frontend/tests/stylecards.test.js`（9 条，api 全 mock）。说明见 DESIGN §40「前端（出图风格卡与图像方言）」（§41 落地后搬过去）。未验证：没有对着真实卡库与后端点过。
  - [x] 后端（2026-09-09）：卡库 24 张 8 类约 36 KB、`src/imagecraft/`（cards / dialects / lint）、`GET /api/style-cards`、模板载荷的 `style_cards`，图片集 / 绘本 / 海报三处接线且不写就零变化。验收：1028 passed，`generic` 输出对四个面逐字节不变（快照 fixture）。未验证：真实出图模型上的效果。
- [x] 配乐 `music`（`docs/music-spec.md`）：按意图出配乐（`music` 角色的来源；目前没有内置来源，由插件提供），产物一键投放到短剧的配乐层或绘本的 BGM。2026-09-10 前后端都落地；未验证：没有用真实音乐来源提交过（全部替身测试）。
  - [x] 适配器与第五个角色（2026-09-10，DESIGN §44）：`MusicRequest` / `TaskType.MUSIC` / `submit_music` 上协议、`ProviderUnsupported`（不出音乐的适配器由基类兜底抛出）、`ROLES` 加 `music` + 设置表 + 设置页角色卡；具体的音乐来源由插件登记（适配器、`<前缀>:` 轮询、能力记录）。验收：`tests/test_music_*.py`、`tests/test_adapter_contracts.py`。
  - [x] 配乐模块后端（2026-09-10）：`src/music/`（spec / prompts / briefs / generate / use）+ `routers/music.py` 全部契约接口（列表 / 编号 / 预设 / 目标 / 新建 / 读写删归档 / 参考图 / 后台任务 `music-generate` / 采用 / 投放 / 产物流 / 示例），预设 brief `skills/ai-music/scripts/music_briefs.json`（10 张），示例 `examples/music/lullaby_demo.json`，模块注册表改 `ready`。投放走 `routers/post.py` 的后期块校验路径，不绕过。验收：`tests/test_music_{spec,prompts,briefs,generate,use,router}.py`、`tests/test_modules.py` 计数、`tests/test_webui_i18n.py` 英文覆盖、`tests/test_packaging_files.py` 打包。
  - [x] 配乐模块前端（2026-09-10）：`/music`（首页 / 新建 / 生成 / 高级）、`src/music.js`、侧栏「配乐」块、启动拉列表；生成页有历次逐条 `<audio>` 播放与采用、「放进项目」下拉（目标 + 起始秒）与点名目标项目的成功提示。验收：`frontend/tests/music.test.js`（13 条，api 全 mock）。未验证：没有对着真实后端点过。
- [x] 本地模型（`docs/local-models-spec.md`，DESIGN §45）：有显卡就能全程本地跑——不要 key、不花钱、不出网。需求：有显卡的用户可能在本机部署模型，设置里要能配置本地模型。
  - [x] 预设 / 扫描 / 设置接线 / 接口 / 前端（2026-09-10）：`src/genkit/local.py`（七个预设、`is_local_url` 只认 localhost 与私有网段、并发 `scan()`、四种模型列表形状、`apply_to_role`、占位 key 与「不花钱」文案）；`settings.validate_role` 在本机地址下放行空 `api_key`（`roleOk()` 与能力检查随之通过）、`RoleConfig.local` 由地址推导；`capabilities.is_local()` 给本机来源打 `local` / `cost: free`，图片集 / 单条视频 / 配乐 / 绘本 / 海报的 `cost_note` 与 `estimate_plan` 改说「本地生成，不花钱（N 次请求）」，`scriptgen_max_budget_usd()` 对本地文本模型不设限；`routers/local_models.py` 四个接口（`local-scan` / `settings/local/apply` / `local-workflows` / `local-workflows/check`，公网地址一律 400，ComfyUI 适配器缺席时给人话不 500）；设置页顶部「本地模型」卡（扫描 / 结果表 / 按角色应用 / 手填地址 / 工作流行）与角色卡的「key 可以留空」提示。验收：`tests/test_local_models.py`（48 条）、`frontend/tests/local-models.test.js`（7 条）、`tests/test_webui_i18n.py` 英文覆盖。未验证：没有在真实的显卡和这些服务上跑过，扫描只有假 HTTP 测试。
- [x] 任务中心页 `/tasks`（2026-09-10，DESIGN §38）：跨模块列 `GET /api/tasks`（后端接口没改，本来就能按 `module` 过滤）。模块过滤走服务端、状态过滤（全部 / 在跑 / 成功 / 失败）在本地筛；一行 = 模块 · 种类（`src/tasks.js` 的 `KIND_LABELS` 把 18 种 kind 翻成人话）· 项目（链到项目页）· 状态 · 开始时间 / 用时 · 失败原因（一行人话 + 展开原文）；有任务在跑时每 3 秒自动刷新、全停就停；只读，不发起任何生成。侧栏全局块加「📋 任务中心」，启动台的「N 个任务在跑」也指这里。同批加 `GET /api/recent?limit=`（`routers/recent.py`）给首页的「最近」。验收：`frontend/tests/tasks-center.test.js`（11 条）、`tests/test_recent.py`（9 条）、`tests/test_modules.py` 的任务过滤。未验证：没有在浏览器里对着真实后端点过。

- [x] AI 播客 `podcast`（2026-09-16，`docs/podcast-spec.md`，DESIGN §50）：需求：加一个 AI 播客模块，包含场景、台词与问答，稿子的质量要尽量接近真人播客。场景 = 环节（冷开场 / 开场 / 话题 / 听友问答 / 收尾），台词按说话人，问答环节念听众问题并标出回答；「像真人」落成三处：起草提示词的八条硬约束、静态检查的十几条可数判据（书面语 / 独白 / 机械轮流 / 一人独大 / 没分歧 / 填充词 / 没具体例子 / 复述问题 / 结尾总结）、混音的停顿与片头片尾位置。`src/podcast/`（spec / craft / lint / audio）+ `routers/podcast.py` + `/podcast` 五个页面 + 示例 `examples/podcast/short_video_demo.json`（4 分钟双人对谈手写稿，零告警）。验收：`tests/test_podcast_*.py`（62 条）、`frontend/tests/podcast.test.js`（14 条）。未验证：真实文本模型出的稿子像不像真人、edge-tts 对谈手感（没做主观评测）。
  - [x] 播客真人感审稿（2026-09-17，DESIGN §53，`docs/podcast-spec.md` §3.5）：随包清单三组 11 条 + 模型逐条判 + 引文核对（复用 `genre_review.verify_items`，相邻两句可连引、跨句界碎片降级），接口 `humanlike-review`、任务 `pc-review`，脚本页审稿卡（`ModelReviewCard` 泛化）。验收：`tests/test_podcast_review.py`、`frontend/tests/podcast-review.test.js`。未验证：没有真模型审过真稿。
  - [x] 播客交付包（2026-09-17，DESIGN §53）：`podcast/export.py` 白名单打包 + manifest + sha256 + ai_disclosure.md，接口 `export` 三个端点、任务 `pc-export`，配音页导出卡。验收：`tests/test_podcast_export.py`、`frontend/tests/podcast-export.test.js`。未验证：没在真机上用真 TTS 合成后导出。
  - [x] 配乐投放到播客（2026-09-16，DESIGN §52）：配乐模块「放进项目」支持播客的片头 / 垫乐 / 片尾三个槽（`music.use` 走 `podcast.spec.normalize` 写回，槽名只在播客模块定义一次），`GET /api/music/targets` 含播客，前端目标下拉多「AI 播客」一组、槽下拉代替起始秒。验收：`tests/test_music_use.py`、`tests/test_music_router.py`、`frontend/tests/music.test.js`。
- [x] 剧本工作台 `script`（2026-09-20，`docs/scriptlab-spec.md`，DESIGN §55）：需求：播客和短剧眼下最弱的都是台词与剧本，要一个单独的页面来调试、优化、修改剧本，调好之后给播客或短剧引用。写 / 试 / 比 / 用 四页 + `src/script/`（spec / craft / lint / review / use）+ `routers/script.py`；版本从不覆盖（`model / rewrite / hand / import` 四种来源，只有第一版自动采用），按 `target`（播客对谈 / 短剧剧本 / 短剧分镜 / 通用）选判据——**一条也不新造**，全部把工作台的版本拼成已有模块认的形状再调它们的函数，没有英文口径的判据只判中文稿；投放与导入跟播客、短剧两边互通（拷贝不是引用，写回一律过目标模块自己的归一化）。示例 `examples/script/after_hours_message.json`。验收：`tests/test_script_{spec,craft,lint,review,use,router}.py`（116 条）、`frontend/tests/scriptlab.test.js`。已验证：用一期已有的播客稿走过一遍（导入 → 只重写一段 → 采用 → 零告警 → 投放回原来的播客，音频自动标过时）。未验证：模型审稿那条路没用真模型审过真稿。
  - [x] 段种类要人标（2026-09-20）：播客的「回答太短 / 复述问题 / 没标回答」三条只对听友问答成立，原来按台词自己的 `kind` 猜段种类，把一段正常冷开场整段报成「回答只有 2 字」——起草提示词本来就要求短接话单独成句，模型会把对谈里的一问一答标成 question / answer。段骨架加 `kind`（`talk / qa`，播客稿在写 / 新建页一个下拉，从播客导入时照抄环节种类），提示词改成「`kind` 只在问答段里用」。顺手把「具体例子」「结论加原因」加进 `webui/problems.py` 的 `dialogue` 关键词（这两条播客模块自己也会出，原来落在「其他」）。验收：`tests/test_script_lint.py` 的「对谈段里的一问一答不是听友问答」、`tests/test_script_use.py` 的导入带种类、`tests/test_problem_cards.py`、`frontend/tests/scriptlab.test.js` 的段种类下拉。

- [x] 长片需要的后期层与大首帧上传（2026-09-22，DESIGN §56）：后期层加数字静音 `silence`、文字角标 `labels`、纯色字幕卡 `layout:"card"` 与片头插卡 `after:"__start__"`、旁白逐句声线与旁白字幕、只在旁白时段压原声；大首帧上传代理图改持久目录（以前提交时已被删）；Seedance 2.5 放开 1080p；AI 标识溯源补声音与配乐；前端保存后期层不丢未知字段。验收见 DESIGN §56。未验证：这些后期层还没在一部完整长度的片子上合成过。

短剧模块自己的改进：
- [x] 剧本逐场编辑器（2026-09-10，DESIGN §32）：剧本不再是裸 JSON 编辑框——`storyvideo/screenplay.py`（归一化 + 校验 + 六条 lint）、`GET/PUT …/screenplay` 返回 `{screenplay, warnings, problems, stats}`、共用组件 `components/ScreenplayEditor.vue`（新建页两步起草 + 项目页新标签 `/drama/p/:id/screenplay`：契约卡、人物表、场次卡的节拍/对白增删改与上下移，告警顶部汇总 + 逐场内联）。验收：`tests/test_screenplay_editor.py`（28 条）、`frontend/tests/screenplay-editor.test.js`（9 条）、`frontend/tests/new-project-screenplay.test.js`（改成逐场编辑器）、`tests/test_webui_i18n.py` 英文覆盖。未验证：没有对着真实文本模型跑过「改完剧本再拆镜」整条链。
- [x] 海报与首帧对比试跑支持异步来源（2026-09-10，DESIGN §36 的 2026-09-10 那条）：这两处写在后台轮询定型之前，碰上异步来源（提交后返回 `<前缀>:<任务 id>`、要轮询的来源）只会报「暂时只支持同步出图的来源」。改成跟绘本 / 图片集 / 单条视频同一套：提交 → 每 5 秒轮询（`MAX_WAIT_SECONDS = 20 分钟`，模块常量，测试调小）→ 下载 → 校验；每轮往任务记录写进度，失败 / 超时记人话原因且不留半张底图或候选；对比试跑每个模型各自轮询，一个失败只废掉它自己那一行、别的候选照常能用。425 那条文案与它的 `i18n_rules` 规则一并删掉。验收：`tests/test_poster.py`、`tests/test_webui_compare.py` 的异步 FakeProvider（processing → processing → finished）、超时、单候选失败三种。未验证：没有在真实的异步来源上跑过，只有替身测试。

## 现状盘点

| 模块 | 状态 |
|---|---|
| genkit 契约 / 设置 / 路由 / 账本 | 已验证（单元测试） |
| 适配器：OpenAI 兼容文本·图·语音 | 替身测试通过；真实账号未联调 |
| 适配器：OpenRouter 图·视频·语音 | 替身测试通过；真实账号未联调；视频轮询与 `get_video_content` 返回结构按 SDK 签名写的 |
| 适配器：火山方舟 图·视频（SDK） | 替身测试通过；SDK 的 `images.generate` / `content_generation.tasks` 参数按文档写，未真实调用 |
| 适配器：MiniMax 视频（REST） | 替身测试通过；未真实调用 |
| 适配器：Edge TTS | 已验证（本机合成成功） |
| storyvideo 流水线（prepare/run/review/asr/final/post/amend/retry-failed） | 已验证（单元测试；装配与后期合成用真 ffmpeg 跑过） |
| webui 项目页 / 基础配置 / 模型设置 | 模型设置页已验证（保存 + 测试连接 + 切换来源保留字段）；项目页用示例剧集在浏览器里跑过「复制 → 锁定 → 生成面板」（未真实生成） |
| 空状态引导 / 示例剧集 / `/api/health` / 本机访问令牌 | 已验证（单元测试 + 浏览器点击） |
| 桌面启动器 | 已验证（`--no-open` 起服务、`--version`、打包版目录约定有单元测试）；pywebview 窗口未在本机打开过 |
| PyInstaller 打包 | 已执行（Windows，约 76MB 单文件夹，含全部 2026-09-07 功能），打包版开窗 1.6 秒、无窗口模式接口可用；安装包脚本已写，Inno Setup 那一步还没在本地跑过（CI 里跑）；未签名 |
| 文档 | README / ARCHITECTURE / DESIGN / AGENTS / 运行时手册已重写；DESIGN §8.1 记录技术栈决策 |

## M0 · 真实联调（先做，其他都建立在它上面）

- [ ] **OpenRouter 全链路**：一个 key 配文本+图+视频+语音，用示例剧集跑 3 镜：首帧 → 视频 → ASR → 装配 → 后期。验收：`output/<ID>/<ID>_post.mp4` 生成，账本里每个 job 有 task_id 与 artifact，失败原因可读。
- [ ] **火山方舟直连**：Seedream 首帧（含参考图 base64）、Seedance 首帧视频。验收：同上 2 镜；确认 `--ratio/--duration/--resolution` 尾参数被接受，`generate_audio` 是否需要。
- [ ] **MiniMax 直连**：Hailuo 首帧视频 + 原生对白。验收：ASR 闸通过 1 句台词；`files/retrieve` 的下载地址能被 `download_url` 拉下来。
- [ ] **参考图链路**：角色锚图（`cast.asset` 本地路径）+ 上一镜首帧作为参考同时生效。验收：连续 3 镜身份一致，QC 通过。
- [ ] **错误翻译**：余额不足、审核拦截、超时三类各制造一次，验收：webui 与 CLI 显示的都是人话，且 `retry-failed` 对超时有效、对审核拦截给出「改写提示词」提示。**替身部分已做**（2026-09-07）：`humanize.classify_failure` 按账本里的错误文本分四类（余额/审核/超时/其他）给建议；运行时把异常类名记进账本；`/failures` 清单、`/retry-failed`（审核类默认跳过并提示改写，`force` 可强制）；预估里标出失败镜，生成轮询遇到明确失败会停下来把原因摆出来；概览页「失败的任务」卡带一键重提+重新生成，顶部下一步指向它；镜头列表标 ✗。剩下的是拿真实账号各制造一次，确认各家的原话能被分类命中。

## M1 · 首次使用体验

- [x] **示例剧集** `examples/episodes/first_episode.json`：5 镜、有台词、有旁白、有一张插卡；「新建项目」页与空状态引导都提供「复制成新项目」（`/api/examples`）。2026-09-07 完成；「10 分钟内出首帧」这条验收要等 M0 真实联调。
- [x] **空状态引导**：项目列表为空时主区显示三步（配模型 → 拿到一个项目 → 出首帧），第一步按角色配置状态盖章。2026-09-07 完成。
- [x] **模型设置页**：切换来源保留已填字段；「测试连接」带耗时；项目页生成面板与新建项目页在角色未配置时给出直达模型设置的提示。2026-09-07 完成。
- [x] **成本提示**：预估弹窗改成「本次会发起 N 次生图/视频生成请求，视频总时长约 Ns」；全文不出现具体平台的计费单位（「认领」只出现在 import-legacy 的技术描述里，保留）。2026-09-07 完成。
- [x] **新建项目向导文案**：去掉「本机命令行」措辞，改成「用模型设置里的剧本文本模型」；头部标签从「不触发付费生成」改成「本地版 · 数据不出本机」（前者不实）。2026-09-07 完成。

## M2 · 桌面客户端

- [x] 执行 `python -m PyInstaller desktop/storystudio.spec`（Windows）：打包版 `--no-open` 起服务后 health / examples / episodes 接口可用，示例剧集随包。2026-09-07 完成。
- [x] 打包版开窗实机验证（2026-09-07）：`StoryStudio.exe` 直接启动 1.6 秒出窗（标题「AI 短剧工作台」，WebView2），无窗口模式 1 秒内 `/api/health` 可用，`/`、examples、episodes、voices、asr、settings 都 200。
- [x] ffmpeg 处理：`/api/health` 检测 PATH，缺失时界面顶部提示并给下载链接；打包版把 exe 目录及 `ffmpeg/` 子目录加进 PATH，用户放两个 exe 进去即可。2026-09-07 完成。随包附带 ffmpeg 未做（许可证与体积，先不做）。
- [ ] macOS：spec 已加 `.app` BUNDLE、`build_macos.sh`（dmg + 可选签名/公证）、entitlements 已写（2026-09-07）；还没在 macOS 上跑过。
- [x] 版本与更新：`storystudio --version`；设置页与头部标签显示版本；版本单一来源 `src/genkit/version.py`；设置页可填版本清单地址并手动「检查更新」（`/api/system/update-check`，不自动下载）。2026-09-07 完成。
- [x] Windows 安装包（2026-09-07）：`desktop/installer.iss` + `build_windows.ps1`；CI 在 tag 时产出。ISCC 这一步还没在本地跑过（CI 里会跑）。签名待证书。
- [x] 打包版体积（2026-09-07）：excludes 加 numpy/onnxruntime/ctranslate2/sqlalchemy/pytest 等后 106MB → 78MB。剩下大头：PIL 13MB（插卡渲染要用）、cryptography 9.5MB（火山 SDK 的 key agreement 要用）、pythonnet 3.5MB（pywebview Windows 后端）。

## M2.5 · 桌面版走完流水线（2026-09-07 完成）

- [x] **台词识别闸进界面**：生成面板「台词识别闸」→ 后台任务跑 faster-whisper → 逐镜结果挂在视频下面，失败镜自动记重摇（未锁定项目只看报告不记裁决）。
- [x] **后期合成进界面**：「生成后期合成版」→ 就绪检查（ffmpeg / 字体 / 视频齐否）→ 后台任务 `compose_post` → 成片面板显示后期版。
- [x] **后期块编辑**：项目页「后期层」面板逐条改/删/加旁白句、声线、插卡、画面文字；写盘前做形状校验（镜号存在、插卡 id 唯一、after 合法）；已冻结项目走 `amend`，失败自动恢复文件。
- [x] **字体跨平台**：后期层字体按平台候选表自动找，缺失时 `/api/health` 与后期面板都报出来。
- 未验证：真实视频上的 faster-whisper 识别与 compose_post 在打包版里的表现（打包时排除了 faster-whisper；ffmpeg 靠用户提供）。

## M5 · 以资源为中心的工作台（DESIGN §11）

- [x] **第 1 步 · 后端接口**（2026-09-07）：角色与场景增删改、锚图上传与预览、引用关系图、概览与下一步、项目列表带标题/封面/进度、镜次反向引用、错误文案翻译层（`webui/humanize.py`）。验收：`tests/test_webui_project.py` 覆盖草稿态增删改、开始制作后拒绝并点名受影响镜次、锚图相对路径在归一化时解析为参考图、next_step 沿流水线逐步推进。
- [x] **第 2 步 · 前端重建**（2026-09-07 第一版）：`frontend/` Vite + Vue 3，三栏工作台，按资源组织视图，术语按 DESIGN §11.1 替换；构建产物提交在 `src/webui/static/app/`，`/` 提供新界面，`/legacy` 保留旧页。浏览器里验证过：示例复制、概览、镜头编辑器保存、角色页、旁白与插卡、设置页。未迁：镜头库/风格库逐条编辑（高级页链到 /legacy）。
- [x] 镜头的新增/删除（带关联检查）/排序接口与全字段编辑（画面、美术、动作、音效、接缝状态、出场、参考镜、台词语气）。
- [x] **第 3 步 · 制作中也能改**（2026-09-07）：`webui/revise.py` + `/revise-edit/preview|apply`；影响判定按编译后的请求逐镜比较，参考链传染；复制成新版本时带走可复用产物与裁决并 `import_legacy`；镜头编辑器、角色页（含锚图 stage 上传）、配音卡的保存在制作中都走这条路。真实 ffprobe 下的复制流程有测试；未验证：带走的裁决在后续增量技术 QC 里被保留（逻辑上 `record_technical_results` 按哈希保留，没跑真实生成）。
- [x] 镜头列表拖拽排序、角色锚图在镜头编辑器出场 chip 上预览（2026-09-07）。
- [x] id 自动生成（2026-09-07）：新增音色/角色/镜次不再要求填 id，只填名字；项目编号预填可改。DESIGN §11.5。
- [x] 镜头库/风格库编辑迁入新界面（侧栏「🎞 镜头库与风格库」，逐条 JSON 编辑 + 整份兜底，2026-09-07）。
- [ ] 风格预设的示例图：需要真实出图，等 M0。
- [x] 前端 Vitest（2026-09-07）：`frontend/tests/`，14 个用例覆盖 api 封装、轮询、弹窗服务、术语表、QcBadge、ModalHost 三种视图、侧栏；`npm test`。
- [x] **画面文字政策**（2026-09-08，融合 drama-skills 文字政策 / IMG-07）：`shot.readable_text` 声明画面里必须可读的文字（载体/文字/语言/位置），首帧尾句从「绝对不出现任何文字」换成「唯一允许出现的文字是…」，视频提示词加「保持清晰可读、内容不变」，方言字幕禁令改成只禁叠加层；编辑器列表编辑；lint 查过长、与「无文字」打架、整集提醒中文字形风险；没声明的镜提示词零变化（金丝雀）。DESIGN §20。未验证：真实出图模型的中文字形成功率。

## M7 · 融合 drama-skills 的待办（对照见 docs/drama-skills-gap.md）

- [x] 剧本工艺清单里能数的判据（2026-09-16，对照见 `docs/script-craft-gap.md`，DESIGN §51）：分镜层五条静态检查（画面文字载体 / 生产说明 / 一镜两种主运镜 / 冲击后无反应镜 / 旁白与对白重叠或超出本镜）、剧本层两条（台词 >25 字 / 开口的人 >3）、交付包的 AI 生成声明与来源记录（`manifest.ai_disclosure` + `ai_disclosure.md`）。不做：关键词计分、合规红线正则、相邻镜运镜相同的告警。验收：`tests/test_script_craft_checks.py`。
- [x] 剧本工艺审稿（2026-09-16，DESIGN §52）：与题材无关的工艺清单（`skills/ai-short-film/scripts/craft_checklist.json`，张力 / 情绪可见 / 节奏 / 合规四组 16 条，出处与置信度逐条标注）+ 模型逐条判 + 程序逐字核对引文（`storyvideo/craft_review.py`，闸门抽成 `genre_review.verify_items` 与题材审稿共用）；接口 `craft-review`、问题卡「工艺」、概览页共用组件 `ModelReviewCard.vue`。验收：`tests/test_craft_review.py`（22 条）、`frontend/tests/overview-craft-review.test.js`（9 条）。未验证：没有真模型审过真剧本。
- [x] 账本逐 job 记来源与模型，交付包溯源按账本（2026-09-16，DESIGN §52）：jobs 表加 provider / model（schema 2，老账本首次可写打开时一个事务里迁移），`RunStore.sources_for`，账本路径统一 `runtime.ledger_path`，`ai_disclosure` 首帧 / 视频按账本、没记的退回按设置并逐行标注。验收：`tests/test_small_fixes.py`、`test_storyvideo_runtime.py`、`test_generate_watch.py`、`test_script_craft_checks.py`。未验证：没有用真实来源提交过一镜。
- [x] 关系场景边界 / 「有意留白」豁免 / 单镜时长上限（2026-09-16，DESIGN §52）：`shot.scene_type` + `lint.check_relation_boundaries`（词表自拟、置信度低、英文正文不判）、`must_pay_off` 条目「（有意留白）」→ `payoff_status` 三元组、`lint.check_shot_duration_ceiling`。验收：`tests/test_script_craft_low.py`、`frontend/tests/shots-scene-type.test.js`。
- [x] 关系场景词表的英文版 + 拒绝点定序 + 播客检查的英文词表（2026-09-17，DESIGN §53）：八张词表各配 `*_EN`（词边界匹配），拒绝场按字符位置找「拒绝点」只报其后的强行接触（强拒绝词任何位置算，弱词要写成开口的话），播客 lint 按 `spec.language` 选表、英文稿按词数。验收：`tests/test_relation_scene_en.py`、`tests/test_podcast_lint.py`。
- [ ] 剧本工艺审稿的真模型效果（要 key）；拒绝点定序仍是按字符位置的粗定位（强拒绝词写的是别的意思时会漏报），没有语料可校准词表。

- [x] G1 结构化起止边界（positions/facing/gaze/hands/held_props/visible_state）+ 相邻镜逐字段比对。（2026-09-08：`shot.entry/exit` + `same_as_previous` + `boundary_change`，编译进首帧/视频/两种方言，连续性逐字段告警，镜头页结构化开关；老项目零变化。DESIGN §22）
- [x] G6 角色变体（换装/受伤）与 `shot.cast: ["lin@rain"]`（2026-09-08）：`cast.<id>.variants` 只写差异，首帧提示词 block + delta、变体锚图优先，`split_cast_ref` 统一拆引用，lint 查未用变体与相邻镜镜外换装，角色页/镜头页编辑，制作中走 revise。DESIGN §23。
- [x] G8 轴线与正反打配对检查。（2026-09-08：`shot.axis{anchors, side, screen, break}` 编译成轴线句，`lint.check_axis` 越轴无声明告警 + 裸左右提示，镜头页轴线小表单。DESIGN §22）
- [x] G5 参考槽位用途进提示词 + 视频通过 QC 后取最后一帧给下一镜作参考。2026-09-08 完成（DESIGN §30）：`reference_roles` 按真实槽位顺序编号进首帧提示词；`ref_tail` 源镜视频过检后用 ffmpeg 取最后一帧，作参考图 / 直接当首帧，未过检整轮推迟。本机真实 ffmpeg 抽帧（`-sseof -0.15`、缩放到交付尺寸）有测试验证；运行时调度用假供应商验证。未验证（需 key）：说明句对真实出图模型的约束力、尾帧续接的身份保持效果。
- [x] G15 同提示词重投计数、重拍原因枚举与处置五分法（2026-09-08）：裁决带 `defect`/`disposition` + 历史，`RunStore.attempts_for`，review 接口第 3 次原样重提 409（force 放行），重拍弹窗五分法路由（保留=通过、后期=跳过、改文字只记不重出、换模型去对比区），列表/失败卡「第 N 次」。DESIGN §24。
- [x] G10 交付面遮挡区预览（2026-09-08）：`delivery.overlay_preset` / `overlay_regions` + 预设表（抖音/快手/视频号），`lint.check_overlays` 点名被挡的可读文字与字幕，首帧预览叠遮罩（可开关），后期页「交付面遮挡区」卡，制作中按后期块 amend。DESIGN §27。
- [x] G20 观众可见性/揭示保护（2026-09-08）：`shot.withhold[{fact, carrier, until}]`，声明镜到揭示点之前的首帧/视频提示词（三种方言）多一句「不得揭示：…」排除句，`lint.check_withhold` 查揭示点之前正文泄露、until 错位、载体与可读文字冲突，镜头页「先不揭示」列表，问题卡分类「揭示」；没声明的镜提示词零变化（金丝雀）。DESIGN §28。未验证：排除句只是提示词文本，真实模型会不会真把载体藏住需要 key 跑片核对。
- [x] G17/G18 剧本层 + 单集契约 + 系列交接（2026-09-08）：两步起草（`POST /api/scripts/jobs/screenplay` → `<id>.screenplay.json` 可读可改 → `use_screenplay` 拆镜，一步起草仍是默认）；`spec.episode_contract` 五字段不进提示词、`lint.check_contract` 核对钩子/集尾/兑现、问题卡「契约」、概览页契约卡走 globals；`spec.series` + `content/series/<id>.json` 记忆、`/series/handoff`（导出时顺手）、起草下一集注入「上一集交接」并用记忆覆盖同 id 角色 block、`GET /api/series`。DESIGN §32。未验证：没有 key，两步起草与交接注入的真实效果没跑过。
- [x] G21 配乐时间线层（2026-09-08）：`post_graphics.music` 本地音频按时间铺进后期版、说话时段自动压低、按成片截断；旁白与插卡页「配乐」卡。DESIGN §25。
- [x] G19 形态卡默认值（2026-09-08）：`spec.form` + `form_cards.json` 五张卡（竖屏短剧 / 横屏短片 / 竖屏动画 / 口播 / 品牌情景），`forms.apply_form_defaults` 只补缺省的画幅 / 分辨率 / 字幕块，运动预算句进三种方言的视频提示词，起草提示词「形态卡」段，`lint.check_form`（画幅方向 / 首镜钩子 / 单镜时长与每分钟镜数 / 字幕关掉 / 竖屏远景后不回近景），`GET /api/scripts/forms`，新建项目页「形态（可选）」下拉，概览页形态标签。没写 form 零变化（金丝雀）。DESIGN §31。未验证：运动预算句下模型的真实表现需要 key 跑片。
- [x] G23 音色对比角色（2026-09-08）：`POST .../cast/<id>/voice-compare` 同一句台词 2–6 个音色并行合成、一个失败不拖累整批、文件永不覆盖，角色页「对比试听」面板，「用这个」走现有绑定。DESIGN §29。
- [x] G25 题材卡注入（2026-09-08）：12 张题材卡节选压缩成 `genre_cards.json`（多数字段摘录原卡原句，MIT，声明见 `docs/third-party-notices.md`），起草时按 id/名/别名注入系统提示词「题材卡」段（不给时逐字节不变），新建项目页「题材（可选）」下拉，`GET /api/scripts/genres`。DESIGN §26。
- [x] 题材与镜头风格的出处、置信度与搭配（2026-09-11）：审计结论写进 DESIGN §48——题材卡补回原卡的 `confidence` / `basis` / `source_file`（原卡说例子是合成虚构的）；五档镜头风格标 `basis`「按参考作品的公认风格人工设定，未做逐镜分析」+ `confidence`「低」，legacy 档标「中」（旧版本沿用、为兼容已有项目保留）；更正库说明与 DESIGN §35 里「从作品蒸馏」的说法。新增 `genre_camera_pairing.json`（本仓库人工设定）+ `lint.check_genre_camera`（只在 avoid 时软告警，问题卡「搭配」）+ 新建项目页出处行与搭配提示 + 镜头库页「出处 / 置信度」列。验收：`tests/test_genre_camera_pairing.py`、`tests/test_genre_cards.py`、`tests/test_camera_profiles.py`、`frontend/tests/new-project.test.js`；提示词金丝雀无漂移。未验证：搭配表是判断不是测量，没有成片对比。
- [x] 按题材审稿（2026-09-11，DESIGN §49）：选的题材终于落盘——`spec.genre`（id / 名 / 别名归一成 id，不认识 `SpecError`），`assemble_spec(genre=)` 起草时写入，`/globals` 与 revise 接受 `genre`（不进提示词，impact 0 镜），概览页「题材」下拉。`storyvideo/genre_review.py`：一次文本模型请求按题材卡的题材核心 / 常见坑 / 禁止漂移逐条判 ok / risk / violated，risk / violated 必须逐字引原文并列镜号 / 场号；`verify_review` 程序核对（引文去空白标点 ≥4 字且落在所列镜 / 场的某个字段里、号必须存在，否则降级为未核对、不进告警；编号对不上卡片的丢掉），结果写 `manifests/genre_review.json` 带 `spec_hash`（审的正文 + 卡 id），正文改过即过时、不出告警。`POST/GET /api/episodes/storyvideo/{id}/genre-review`（后台任务 `genre-review`，400 没选题材 / 409 在跑，本地文本模型说不花钱）；核对过的条目进问题卡「题材」（「按题材卡「…」审稿（模型判断）：…」）；概览页「按题材审稿」卡（成本确认、轮询、违反 / 有风险 / 没问题分组、未核对组折叠、过时横幅）。验收：`tests/test_genre_review.py`（20 条）、`frontend/tests/overview-genre-review.test.js`（8 条）、`tests/test_webui_i18n.py` 英文批量；提示词金丝雀无漂移。未验证：没有真模型审过真剧本——引文纪律的遵守率、4 字门槛与「同一字段」规则的误伤率、漏答率都要真稿跑过才知道。

## M6 · 音色库（DESIGN §12，2026-09-07 第一版）

- [x] 音色档案增删改、目录（edge 在线表 / OpenAI 固定表）、试听样本；绑定给角色（拷贝描述 + TTS 参数，开始制作后拒绝）与旁白（后期块，制作中可改）；界面三处入口。
- [x] **台词 TTS 配音兜底**（2026-09-07）：`post_graphics.dubbing` 三种模式，逐句按 `cast.voice_tts` 合成、按字幕时间分配铺进后期版、原声压低；就绪检查点名缺 TTS 的角色与嘴型风险镜；真实 ffmpeg 混音有测试。未验证：真实 edge-tts 合成的配音与模型原声叠加后的听感（需要真实视频）。
- [x] 配音镜一键切"画外说话方式"（2026-09-07）：`/dubbing/offscreen`；草稿直接写，制作中走 revise 流程（mutation `dubbing_offscreen`）。
- [x] 音色克隆（2026-09-08，**替身已做、未验证**）：`minimax_speech` 适配器（T2A v2 合成 + `/files/upload` → `/voice_clone` 克隆，按公开文档写，假 HTTP 回放），档案加 `reference_audio` + `clone{provider, voice_id, status, error}`，上传 / 克隆 / 流式读取接口，音色页「参考音频与克隆」卡，`engine=minimax` 合成挑克隆 id；密钥可借视频角色的 MiniMax key。DESIGN §33。没有 MiniMax key，一次真实克隆都没跑过；火山 / 阿里的克隆接口没接。
- [x] 视频模型的参考音频（2026-09-08，**替身已做、未验证**）：`spec.video_params.reference_audio`（项目相对路径）→ 运行时提交前解析并按 `asset_type=audio` 上传成令牌 → 支持音频参考的视频来源从 `extra_params.reference_audio` 取（内置适配器都不转发 `extra_params`，自然忽略；插件来源可以用）。没声明的项目指纹零变化。DESIGN §33。没有界面入口；没真实提交过带音频参考的任务。

## M3 · 产品化打磨

- [x] **模型能力表**（2026-09-07）：`genkit/capabilities.py` + `/capability-check` + 开始制作前拦截（verified 越界可强制）+ 概览页检查卡。静态表里是 spec.normalize 里验证过的组合和几家文档值；**模型目录**（2026-09-07）：设置页一键拉 OpenRouter 的 `/models`、`/images/models`、`/videos/models`（公开接口，不需要密钥），模型 id 输入框补全，目录里的参数区间（时长/分辨率/画幅/首帧/原生音频/参考图上限）以 `source=catalog` 覆盖静态表（提醒级别），并指出 id 不存在、模型不出图/不出视频、不吃参考图。本机实拉过一次：430 个模型，其中生图 50、视频 28。
- [x] **正文静态 lint**（2026-09-08，融合 drama-skills G4/G12/G13/G24/G9/G2）：`storyvideo/lint.py` 七项检查 + `shot.purpose` 字段与编辑器下拉；对白预算加可用窗口与「偏紧」。DESIGN §18。
- [x] **视频提示词方言**（2026-09-08，融合 drama-skills）：MiniMax H3 三段结构 / Seedance 语法 / 通用，按模型自动选；概览页显示并可一键切换；`PUT /globals` 草稿改全局字段。DESIGN §17。未验证：H3 上三段结构相对散文的实际出片差异（需真实生成对比）。
- [x] **字幕样式**（2026-09-08）：字体/字号/颜色/描边/位置/底色带/显示方式一处默认（`storyvideo/subtitles.py`），装配与后期合成同用；`subtitle` 成为后期块；「旁白与插卡」页字幕样式卡带预览。未验证：真实视频上的观感（需要成片）。
- [x] **连续性锁**（2026-09-08，融合 drama-skills continuity-lock）：`cast.<id>.locks` 锁面原样进生效镜的首帧与视频提示词（三种方言），`lint.check_locks` 校验在场（大小写/词边界/否定之后不算/负向词反着报），角色页「跨镜锁定项」，制作中走 revise。DESIGN §19。
- [x] 界面溢出治理（2026-09-08）：列表项 title/sub 强制块级截断，全局 overflow-wrap 兜底，pill 截断，表格单元格限宽。
- [x] **交付包**（2026-09-08，融合 drama-skills `project_tool.py export`）：`webui/export.py` 白名单打包（spec 快照、每镜提示词 md、首帧/视频/成片/旁白、审片裁决、manifest、sha256）到 `09_export/` 并压成 zip；`POST/GET /export`、`/export/download`（纯文件名校验）；成片页「导出交付包」卡带任务进度与历史包下载。DESIGN §21。设置/密钥/锚图/账本永不进包（有测试）。
- [x] 起草不再填项目 id：只填可选的剧名，编号后端生成；复制示例也不再要填编号（2026-09-07）。
- [x] **插件来源**（2026-09-23，DESIGN §57，`docs/plugins.md`）：内置之外的来源由仓库外的插件登记——放在 `~/.storystudio/plugins/<名字>/`，启动时登记适配器、异步任务前缀、能力记录、翻译与失败分类措辞；插件缺失或加载失败时应用照常启动，指向它的角色说清「这个来源现在用不了、为什么」，在飞任务留在账本里等插件装回来，不记失败。核心代码里不带任何插件的名字。
- [x] **统一网关**（2026-09-07）：设置页填一次 LiteLLM / new-api / one-api 这类网关的地址和 key，测试连接拉 `/models`，一键应用到四个角色；新增 `openai_video` 适配器（OpenAI Videos 形状，按文档写，未真实跑过）；模型目录多一份网关来源（角色按 id 猜）。DESIGN §14。未验证：真实网关上的四个角色，尤其视频转发。
- [x] **多模型对比**（2026-09-07）：`webui/compare.py`，同一镜同一套提示词与参考图换几个模型各出一张候选（后台任务，不进账本，放 09_compare/）；「采用」= 记重拍 + 拷成首帧 + import_legacy 认领，未变的裁决按哈希保留。限制：只支持同步出图的来源、同一来源下换模型 id（换来源要改模型设置）。
- [x] **语音角色扩展**：由 M6 的台词 TTS 配音 + 配音镜一键切画外覆盖（2026-09-07）。
- [x] **ASR 闸可选安装**（2026-09-07）：设置页一键 pip 安装（后台任务）、模型预下载；打包版给说明。进度只有耗时，没有百分比（faster-whisper 的下载不暴露进度）。
- [x] **后期层可视化**（2026-09-07）：插卡在「旁白与插卡」页有预览缩略图（后端按内容哈希缓存渲染）；画面文字按 y 比例叠在镜头编辑器的首帧上；"后接插卡"角标。
- [x] **英文界面**（2026-09-07）：前端 `t()` 中文原文当 key + 英文字典分片、侧栏语言开关、`X-StoryStudio-Lang` 请求头；后端中间件按精确表 + 正则模板翻译 JSON 响应里的文案键（DESIGN §13）。未验证：真人英文用户通读一遍的措辞质量；后端零散错误文案可能有漏网的中文（缺翻译会原样露出中文，不会坏）。

## M4 · 工程与质量

- [x] 适配器契约测试（2026-09-07）：`tests/test_adapter_contracts.py` 按 `tests/fixtures/adapters/*.json` 回放，9 个适配器共用一套契约断言（前缀、落盘、参考图不裸发）。夹具是按 SDK 签名手写的，M0 联调后用录制响应替换（先脱敏：去掉签名地址、任务 / 请求 id、账号与余额字段）。
- [x] 账本迁移（2026-09-07）：`SCHEMA_VERSION` + `MIGRATIONS` 逐版本升级框架；只读打开不迁移并说明；来自更新版本的账本拒绝打开。
- [x] 日志：桌面启动器写 `~/.storystudio/logs/storystudio.log`（滚动，2MB×3）；`genkit/logmask.py` 脱敏过滤器遮掉密钥、Bearer、data URL（2026-09-07）。
- [x] CI（2026-09-07）：`.github/workflows/ci.yml`——pytest（Ubuntu + Windows）+ 金丝雀 + 前端 vitest/build + 构建产物一致性检查；tag 时打 Windows 包并冒烟。还没推到 GitHub 跑过。
- [x] 安全：webui 只绑 127.0.0.1 并加随机 token（`STORYSTUDIO_TOKEN`，首页 `?token=` 种 cookie，`/api/*` 校验；`--no-token` 可关）。2026-09-07 完成。设置文件权限：保存后 POSIX chmod 600 / Windows icacls 只授当前用户（2026-09-07）。
- [x] 前端工程化：已拆成 Vite 项目（M5 第 2 步）。

## 已知问题

- [ ] 纪念写真百天 / 周岁模板里的「全家福」场景画的是三个人，提示词却带单人锁（`SOLO_ZH` / `SOLO_EN`，`src/imageset/templates.py`），会和场景打架；应按场景人数换成多人锁。
- [ ] `imagecraft.lint.check_items` 已实现，但图片集 / 图片工作台的接口与界面都没用上它：逐张的提示词检查目前只在单张编辑时跑。
- [ ] 剧本工作台有「从示例建项目」的接口（`POST /api/script/examples/create`），界面上没有对应按钮；首页引导里「每个模块都能复制示例」这句对剧本工作台不成立。
- [ ] ComfyUI 来源的设置提示说「工作流在下面的下拉里选」，但设置页没有保存 `extra.workflow` 的控件，只能手改 settings.json。
- [ ] DESIGN.md 里有几处说后期合成「不花钱」：旁白 / 配音在「模型设置」里配了非 Edge 的语音来源时会走那个来源，可能计费，措辞要改成「默认不花钱」。
- [ ] 待核对：各来源的付费提交是否还带着 SDK 自己的传输层重试（文本来源的 OpenAI 兼容客户端设了 `max_retries=1`）。极端网络下这会让同一请求被服务商收到两次，也是 `blocked_submission_unknown` 的可能来源；逐个核对后在提交时关掉自动重试。

- [ ] 内置来源不支持任务历史查询：短剧里「提交结果未知」（`blocked_submission_unknown`，比如提交途中网络超时）的任务没有原地出口——自动接管、`reconcile`、`abandon` 都要 `list_tasks`，路由来源和内置适配器都没实现，界面也把这类任务标成不可重提。现在只能核对来源控制台后开新 revision（STORYVIDEO_RUNTIME §6）。做法二选一：给能查历史的内置来源在路由上补 `list_tasks`；或加一个带确认的本地出口「放弃这次提交」（用户确认远端没有这次任务后清掉 intent，不查历史）。验收：内置来源下一个卡住的 job 能在原项目里解开并重提，不会自动重复提交。

- ~~端口被别的程序占着时，启动器要么干等三十秒、要么把别人的界面当成自己的打开~~ 2026-09-10：显式端口被占直接报「谁占的 + 换哪个端口」；探活改看 `/api/health` 里的 `storystudio` 标记；服务线程的异常不再静静地死掉。DESIGN §46。

- ~~spec 的分辨率（720p/1080p）和所选模型的档位（MiniMax 768P/2K）对不上，提交被拒~~ 2026-09-08：`capabilities.resolution_for` 提交前自动对齐到最接近的支持档位（MiniMax / 火山等视频适配器都走），能力检查改为「会自动换成 768P」提示。

- ~~Windows 上改音色参数时删试听样本报 WinError 32（浏览器还在播）~~ 2026-09-07：样本改成「id-参数哈希-时间戳.mp3」永不覆盖，旧文件尽力清理，保存/删除不再因此失败。
- ~~422 校验错误把 pydantic 原始 JSON 摆给用户~~ 2026-09-07：统一翻成「请求参数不对——字段 id：必填，没有给」。

- ~~`openrouter_video` 完成后整包进内存再落盘~~ 2026-09-07：改为 `store_stream` 分块写盘、不再二次拷贝；SDK 返回的是整包 httpx.Response，真正的流式要等 SDK 暴露 stream 接口。
- ~~换过适配器的旧任务查询用当前凭据会失败~~ 2026-09-07：改为明确报错，指出任务是哪家提交的、把来源切回去或对该镜重拍。
- ~~`scriptgen` 的命令行兜底路径~~ 2026-09-07 已删除：起草只认模型设置里的文本角色或 .env 代理，都没配就明确报错。
- ~~静态检查「景别缺失」对英文 spec 不生效~~ 2026-09-07：加了英文景别词表。
