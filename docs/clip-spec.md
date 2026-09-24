# 单条视频模块 `clip`（🎞，路由 `/clip`，2026-09-10）

一条提示词出一段视频——广告 / 预告 / 单镜素材。跟短剧的区别：**不拆镜、不进账本**，一个项目就是一条片子，写法用 `docs/prompt-patterns.md` 整理的三段式（【全片设定】/【分段时间线】/【衔接与收尾】，长单镜视频提示词的常见形状）。共用：模型设置的视频角色、风格卡库、后台轮询与断点续等、字幕字体、任务、导出。

## 1. 项目文件 `content/clip/<ID>.json`

```json
{
  "id": "CL-20260910-1",
  "title": "城市清晨短片",
  "prompt_language": "zh",
  "style": "cinematic_key_art",          // 风格卡 id（fits 含 clip）或 custom
  "style_anchor": "",
  "global": {                            // 【全片设定】
    "look": "清冷写实的城市纪录片质感，像一段从天亮拍到热闹的长镜头",
    "palette": ["雾蓝", "青灰", "米白", "砖红", "暖橙", "铜金"],
    "light": "日出前后的斜射晨光，街面浮着一层薄雾",
    "aspect_ratio": "16:9",              // 显式声明，并自动带上「排除另一种画幅」那句
    "safe_area": "行人和关键动作落在画面中部，四周留出余量",
    "text_policy": "画面上的字只来自店招与站牌上的几个英文短词；不叠字幕，不加水印",
    "audio": "街道环境声打底，配一段轻柔的钢琴"   // 只是说明；配乐由配乐模块或后期解决
  },
  "beats": [                             // 【分段时间线】：绝对时间，可带小数
    {"id": "B01", "start": 0, "end": 3, "shot": "航拍大远景", "picture": "薄雾里的城市天际线，天边透出一线暖橙", "sound": "远处一声汽笛", "text": "GOOD MORNING"},
    {"id": "B02", "start": 3, "end": 6, "shot": "中远景", "picture": "空荡的十字路口，路灯一盏接一盏熄灭", "sound": "路灯继电器轻响", "text": ""}
  ],
  "continuity": {                        // 【衔接与收尾】
    "one_take": true,                    // 一镜到底；false 时段与段之间硬切
    "transitions": "每次换景都借前景里经过的东西带过去（白汽 / 驶过的车）",
    "last_frame": "停在整条街被阳光照亮的全景上",
    "forbid": ["真实品牌的标识", "夜景霓虹", "延时摄影式的快进"]
  },
  "video_params": {"model": "@video", "duration": 30, "resolution": "1080p", "generate_audio": false},
  "first_frame": {"mode": "none", "asset": null, "prompt": ""},   // none | upload | generate
  "attempts": [],                        // 每次出片一条，从不覆盖
  "current": null
}
```

归一化（`src/clip/spec.py`）：id 按天编号 `CL-<日期>-<n>`；节拍按 `start` 排序，`end > start`，相邻不许留空洞也不许重叠（越界告警不是错误）；节拍总时长与 `video_params.duration` 差超过 1 秒 → 告警；`aspect_ratio` 与 `resolution` 走 `genkit.capabilities.resolution_for` 自动对齐。

## 2. 提示词编译 `src/clip/prompts.py`

三段拼一条（段标题与编译器固定加的句子写在 `clip/prompts.py` 的 `HEAD_*`、`OPENER`、`ONE_TAKE` / `HARD_CUTS` 与 `FORBIDDEN_ASPECT` 常量里；段标题中文是【全片设定】【分段时间线】【衔接与收尾】，英文段标签跟中文一一对应，以 `HEAD_*` 常量为准），按 `prompt_language` 出中英，按图像/视频方言组织（复用 `src/imagecraft/dialects.py` 的数字镜头参数剥离与画幅尾行；视频这边只用 `generic` 与 `nano_banana` 两种就够，未知模型走 `generic`）：

```
【全片设定】视频时长 <duration> 秒，<resolution>。<风格卡锚句><look>。色板：…。<light>。画幅 <aspect_ratio>，<排除另一种画幅>；<safe_area>。<text_policy>。<audio>
【分段时间线】0–3 秒：<shot>，<picture>；声音：<sound>；画面文字："GOOD MORNING"。 …
【衔接与收尾】<一镜到底的说明，或 one_take=false 时的硬切说明>。<transitions>。最后一帧：<last_frame>。禁止：…。
```

判据见 `docs/prompt-patterns.md`：画幅显式 + 排除另一种画幅 + 安全区（P1）、色板 5–7 个具体色名（P2）、画面里要出现的文字用引号原样写。

## 3. 检查 `src/clip/lint.py`

时间轴空洞 / 重叠 / 与总时长对不上；色板不足 5 个具体色名；节拍里出现「然后 / 接着」这类连接词（一个节拍只做一件事）；最后一帧没写；`text` 非空但 `text_policy` 说无文字（自相矛盾）；再加 `imagecraft.lint` 的通用四项。告警形状与其它 lint 一致，进问题卡。

## 4. 出片 `src/clip/generate.py`

- `first_frame.mode`：`none` 直接文生视频；`upload` 用上传的图；`generate` 先按【全片设定】+ 第一个节拍出一张首帧（一次生图请求），再图生视频；首帧是静止图，画幅句用不带"从头到尾"的那一套（`FORBIDDEN_ASPECT_STILL`）。
- 一次请求 = 一条片子，落 `output/clip/<ID>/01_video/<ID>-<n>.mp4`，**从不覆盖**；`attempts[]` 记 `{file, created_at, model, request_hash, task_id, error}`，`current` 指向采用的那条。
- 异步来源走后台任务 `clip-generate`（`module="clip"`），并复用 `webui/generate.py` 的轮询思路：服务重启后能接着等（在 `resume_inflight_watchers` 里带上 clip）。
- 请求哈希不变且文件在 → 跳过，`force` 重出。

## 5. 接口 `/api/clip/...`

`GET /api/clip` · `GET /api/clip/next-id` · `POST /api/clip` · `GET/PUT/DELETE /api/clip/{id}` · `GET /api/clip/{id}/lint` · `POST /api/clip/{id}/first-frame`（出首帧或上传，base64） · `POST /api/clip/{id}/generate`（`{force?}`，返回 `cost_note`） · `POST /api/clip/{id}/pick`（`{attempt}`） · `GET /api/clip/{id}/artifact/video/{name}` · `/frame/{name}`（路径安全） · `POST /api/clip/examples/create`。

## 6. 前端 `/clip`

模块首页（列表 + 新建 + 复制示例）；新建（标题、风格卡、画幅、时长、模型）；项目页三个子页：**写法**（三段式编辑器：全片设定表单 + 节拍表格（时间条可视化，增删排序，每行景别/画面/声音/画面文字）+ 衔接与收尾表单 + 右侧实时预览编译出的提示词）、**出片**（首帧模式、生成、进度、历次片子播放与采用、下载）、**高级**（JSON、归档）。示例：`examples/clip/city_morning.json`（原创的 30 秒城市清晨短片：10 拍、6 色色板、16:9，零告警）。

## 7. 实现偏差（2026-09-10 落地记录）

契约按上面六节实现，下面几处跟字面不一样，都是落地时才看见的问题：

| # | 契约怎么写 | 实际怎么做 | 为什么 |
|---|---|---|---|
| 1 | §1「节拍……相邻不许留空洞也不许重叠」 | 归一化只拦**形状**（按 `start` 排序、`end > start`、id 不重复）；空洞 / 重叠 / 与总时长对不上是 §3 的**告警** | 写到一半的项目也要能打开、能保存。§3 本来就把这三件事列成 lint 规则，两处都拦等于同一件事说两遍，而且会让「先摆四拍再调时间」这种正常写法保存不了 |
| 2 | §1「`aspect_ratio` 与 `resolution` 走 `resolution_for` 自动对齐」 | 只对齐 `resolution`；`spec.align_resolution(model, requested)` 在**知道适配器时**调 `capabilities.resolution_for`，不知道时在静态表里按最长模型前缀找。模型写 `@video` 时原样留着（用哪个模型由「模型设置」决定，提交前适配器会再对齐一次） | `resolution_for` 需要 `(role, adapter, model)`，而项目文件不知道自己会被哪个来源跑。画幅不是「对齐」的对象——它是用户的交付要求，不合适的画幅由能力检查提示，不该被悄悄改掉 |
| 3 | §1 的 `video_params` 示例 | 字段就是 `{model, duration, resolution, generate_audio}`，**没有** `aspect_ratio`——画幅只写在 `global.aspect_ratio` 一处 | P1 的判据就是「画幅只写一次」。两处画幅一旦写得不一样，模型只会挑一处听，是最贵的疏漏 |
| 4 | §2 的三段模板 | 【全片设定】第一句写明时长与分辨率（如 30 秒、1080p）；【分段时间线】一拍一行（不是一整段连写）；节拍之间不写「然后」 | 时长与分辨率显式写出是 P1 的一部分；一拍一行在编辑器右侧和模型那边都更好读 |
| 5 | §3「再加 `imagecraft.lint` 的通用四项」 | 通用项跑在**用户写的正文**（look / light / safe_area / text_policy / audio / 每拍的景别画面声音 / 转场 / 最后一帧）上，不跑在编译后的整条提示词上 | 编译器自己加的「排除另一种画幅」那句与「no transition effects」会命中「否定式描述」那条规则——那是产品决定，不是写法问题，自己告自己没有意义 |
| 6 | §3「`text` 非空但 `text_policy` 说无文字」 | 只认「无文字 / 不出现文字 / no text」这类说法；「无水印」「无字幕」不算矛盾 | 「无水印 + 画面里有片名」是很常见的写法，把水印当成文字会把正常写法全告一遍 |
| 7 | §1 `style` 注释「风格卡 id（fits 含 clip）」 | 接受任何风格卡 id（`imagecraft.cards`），但**没有**给卡库加 `clip` 这个 `fits` 目标 | 卡库的 `fits` 有测试盯着（每个目标都要有卡、每张卡的目标都要能解析），改它属于 §41 那摊事；前端的风格卡挑选器对 clip 不做「只看适合的」筛选，全部卡都能选 |
| 8 | §6「右侧实时预览编译出的提示词」 | 整条提示词由**后端**编译，保存后刷新；每一拍那一行在前端本地实时算（`clip.js::previewLine`），没保存时预览区标一句「保存后更新」 | 提示词编译有一份实现就够了（`clip/prompts.py`）。在前端再写一遍三段拼接，两边迟早漂移；而用户真正边打字边要看的是「这一拍会变成什么样」，那一行本地算够用 |
| 9 | §4「在 `resume_inflight_watchers` 里带上 clip」 | `webui/generate.py` 拆成 `_resume_storyvideo_watchers`（账本）+ `_resume_clip_watchers`（懒加载 `routers.clip.resume_clip_watchers`），入口函数不变 | 原来的实现在「没有账本文件」时直接 `return []`，clip 的续等会被短剧的账本挡住 |

另外补了两个契约没写、但落地必须有的东西：`first_frame.error`（上一次出首帧的失败原因，写进文件，重开界面还看得见）与 `image_params`（首帧那次生图请求用什么模型 / 尺寸）。
