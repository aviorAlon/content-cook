# 绘本 / 有声书模块 —— 规格契约（2026-09-09，phase 2 第一个新模块）

插画 + 朗读 + 双语字幕的有声绘本：每页停多久由朗读长度决定。与短剧模块共用模型设置、音色库、任务、导出、字幕样式、i18n。
跟短剧最大的不同在时间轴：短剧按镜头时长排，绘本是先合成朗读、量出时长，再定每页停多久。

## 1. 项目文件 `content/picturebook/<ID>.json`

```json
{
  "id": "PB-20260909-1",
  "title_zh": "小狐狸的三只袜子", "title_en": "Little Fox's Three Socks",
  "age_band": "2-4",                      // 2-4 | 4-6 | 6-8
  "bilingual_mode": "sentence_alternate", // sentence_alternate | page_zh_then_en | zh_only | en_only
  "education": {"cognition": "认颜色：红、黄、蓝", "vocab": ["袜子/sock", "风/wind"], "emotion": "东西被风吹走了也没关系，分给朋友一样开心"},
  "style_anchor": "Soft watercolor picture-book art, rounded friendly shapes, airy pastel washes, visible paper grain",
  "negative": "photo, 3D render, text, letters, watermark, frightening, gloomy",
  "characters": {"fox": {"name": "小狐狸", "name_en": "Little Fox", "block": "橘红色小狐狸，白色尾巴尖，穿一件浅绿色小背心", "asset": "content/assets/PB-20260909-1/fox.png"}},
  "scenes": {"yard": {"name": "小狐狸的院子", "block": "木头小屋前的院子，两根木桩之间拉着一根晾衣绳，午后的阳光", "asset": null}},
  "narration": {"zh_voice": "girl_calm", "en_voice": "en_story", "en_rate": "-8%", "pause": {"lead": 0.4, "between": 0.6, "tail": 0.8}},
  "music": {"file": "content/assets/PB-20260909-1/lullaby.mp3", "gain": 0.25, "fade_out": 3.0},   // 可空
  "page_turn_sfx": "content/assets/PB-20260909-1/flip.mp3",                                        // 可空
  "delivery": {"aspect_ratio": "9:16", "resolution": "1080p", "landscape_variant": false},
  "image_params": {"model": "@image", "aspect_ratio": "3:4", "image_size": "2K"},
  "subtitle": {"font_path": null, "zh_size": 96, "en_size": 64, "highlight": "#FFD27A"},
  "pages": [
    {"id": "P01", "kind": "cover", "scene": "小狐狸站在院子的晾衣绳下，全身，面向读者，双手捧着红、黄、蓝三只袜子", "characters": ["fox"], "scene_ref": "yard",
     "zh": "小狐狸的三只袜子", "en": "Little Fox's Three Socks", "hook": "", "education": "", "kb": {"dir": "push", "scale": [1.0, 1.06]}, "flip": "page_turn"},
    {"id": "P03", "kind": "page", "scene": "一阵风吹过院子，红袜子从晾衣绳上飘起来，小狐狸伸长手臂去够，中景", "characters": ["fox"], "scene_ref": "yard",
     "zh": "呼呼呼，风一吹，红袜子飞走了！", "en": "Whoosh, the wind blows the red sock away!", "hook": "红袜子飞到哪儿去了？", "education": "词汇:风/wind",
     "kb": {"dir": "up", "scale": [1.0, 1.08]}, "flip": "page_turn"},
    {"id": "P10", "kind": "back", "scene": "傍晚，小狐狸脚上穿着蓝袜子，盖着格子被子睡在小床上，中景", "characters": ["fox"], "zh": "小狐狸穿上蓝袜子，暖暖地睡着了。晚安。",
     "en": "Little Fox puts on the blue sock and falls asleep, warm and cozy. Good night.", "hook": "", "kb": {"dir": "pull", "scale": [1.06, 1.0]}, "flip": "cut"}
  ]
}
```

完整的示例书在 `examples/picturebook/fox_three_socks.json`（10 页，「复制示例」用的就是它）。

归一化规则（`picturebook/spec.py`）：id 缺省按 `PB-<日期>-<序号>`；页 id 缺省 `P01…`；`kind` 第一页默认 cover、最后一页默认 back；`kb.dir` ∈ push/pull/left/right/up/down；`bilingual_mode` 决定 `zh`/`en` 哪个必填；`characters[]` 必须在 `characters` 表里；`age_band` 三档。

## 2. 目录

```
output/picturebook/<ID>/
├── 04_assets/      定妆 / 场景锚拷贝
├── 05_pages/       P01.png …（插画，3:4）
├── 07_audio/       P01_zh.mp3 / P01_en.mp3 / bgm / flip
├── 07_edit/        <ID>_book.mp4（9:16 主版）、<ID>_book_16x9.mp4（可选）、时间线 json
├── 09_export/      朗读稿 <ID>_zh_script.xlsx / <ID>_en_script.xlsx
└── manifests/      pages.json（每页请求哈希 / 产物 / 时间）、narration.json（时长实测）、timeline.json、qc.json
```

## 3. 文本静态检查（`picturebook/lint.py`，儿童有声绘本的经验规则）

| 项 | 规则 |
|---|---|
| 分级 | 2-4：中文 ≤20 字/页、8–10 页；4-6：20–40 字、10–14 页；6-8：40–70 字、12–16 页（封面封底计入页数；字数只查正文页） |
| 翻页钩 | 正文页不能连着 3 页都没有 `hook`（隔一两页就该有一个）；最后一页的 `hook` 要留空；钩里出现恐怖词（怪物、鬼、血、黑影等）告警 |
| KB 方向 | 相邻页 `kb.dir` 必须变化 |
| 拟声词 | 一页至多一组（呼呼 / 哗啦 / whoosh…），全书 ≤4 组 |
| 一页一个动作 | `scene` 里出现「然后/接着/之后」类连接词告警 |
| 插画无字 | `scene` 里出现「写着/标题/字」告警 |
| 群像页 | `characters` ≥2 时提示词把每个角色都写上名字 + 排他锁（编译器保证，lint 检查 `scene` 没用「大家/他们/动物们」这类总称） |
| 结尾安稳 | 结尾页 `zh` 含「晚安/睡/抱/回家」之一，否则告警（`en_only` 不查） |
| 教育点 | `education.vocab` 每个目标词在全书 `zh`+`en` 出现 ≥3 次，否则告警 |
| 重复结构 | 同一句式（去掉数字/名词后的模板）出现 ≥3 次视为有母题；没有时提示（不是错误） |

## 4. 插画提示词（`picturebook/prompts.py`）

`style_anchor + 场景锚 block(有则) + 每个出场角色的 block（一个个写名字，含定妆图作参考）+ scene + 构图句（底部三分之一留给字幕、一页只画一个动作、给小孩子看的柔和气氛）+ 排他锁（只出现这几个角色，别的人和动物都不画）+ NO_TEXT 尾句`；负面 = `negative` + 文字/水印词。参考图 = 出场角色定妆 + 场景锚。请求画幅 3:4，两版交付都从中裁切。

## 5. 朗读与时间轴（`picturebook/narration.py`、`picturebook/assembly.py`）

- 每页每语言一段：`07_audio/P01_zh.mp3`、`P01_en.mp3`，走 `genkit.voices.synthesize_with_profile`（音色库里的档案；英文 rate 叠加 `en_rate`）；同文本同音色同参数哈希不重合成；时长用 ffprobe 实测记进 `manifests/narration.json`。
- 页时长 = lead(0.4) + 中文 + between(0.6) + 英文 + tail(0.8)；2-4 岁各停顿 +0.2；封面多停 2 s 让人看清书名；封底多留 3 s 给 BGM 收尾；`sentence_alternate` 一句中文接一句英文（中→0.3 s→英→0.5 s→下一句）。
- 画面：每页一条 Ken Burns（scale+crop，`fps=24`, `yuv420p`, 幅度随时长 1.05–1.12），方向按 `kb.dir`；`page_turn` 的两页之间 `xfade=slideleft` 0.4 s，翻页音效在 xfade 开始后 0.2 s 响起；`flip: cut` 硬切。
- 字幕：中文在上、英文在下，字号取 `subtitle.zh_size` / `en_size`（默认 96 / 64，以 1920 高为基准缩放）；正在念的那句用 `subtitle.highlight` 着色，其余时段白色；每句的起止取自时间轴；字体按字幕样式解析。
- 混音：静音底轨定总长，上面叠逐段朗读、翻页音效和 BGM（gain 默认 0.25，末尾按 `music.fade_out` 淡出），最后整体 loudnorm（I=-14、TP=-2），成片开 faststart。
- 主版 9:16；`delivery.landscape_variant` 为真再出 16:9。

## 6. 接口（`/api/picturebook/...`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/picturebook` | 列表（id/title/age_band/页数/进度/封面） |
| GET | `/api/picturebook/next-id` | 自动编号 |
| POST | `/api/picturebook` | 新建（body = spec 子集；只填名字不填 id） |
| GET/PUT/DELETE | `/api/picturebook/{id}` | 读 / 整体保存（校验 + lint 结果一起返回）/ 归档到 `.webui_deleted/picturebook/` |
| POST | `/api/picturebook/jobs/draft` | LLM 起草：`{direction, age_band, bilingual_mode, education, page_count?}` → 分页表草稿（后台任务；系统提示词 = 输出字段说明 + 按页位置与全书的写作约束，篇幅取自 `lint.AGE_RULES`） |
| GET | `/api/picturebook/{id}/lint` | 文本检查 + 问题卡（复用 `webui/problems.py` 的四要素形状） |
| POST | `/api/picturebook/{id}/pages/generate` | `{only?: [P01…], force?: bool}` 生成插画（后台任务；同步出图来源直接落盘，异步来源在任务内轮询；请求哈希不变且文件在则跳过） |
| POST | `/api/picturebook/{id}/narration/generate` | `{only?, languages?}` 合成朗读（后台任务，哈希去重，实测时长） |
| POST | `/api/picturebook/{id}/assemble` | 装配成片（后台任务） |
| GET | `/api/picturebook/{id}/artifact/page/{pid}` `/audio/{pid}_{lang}` `/final` | 产物流（路径安全） |
| POST | `/api/picturebook/{id}/assets/{kind}/{key}` | 定妆 / 场景锚 / BGM / 翻页音效上传（base64，不覆盖） |
| GET | `/api/picturebook/{id}/script-sheet?lang=zh` | 朗读稿 .xlsx（交给真人朗读或别的 TTS）：文件名 `<ID>_<zh\|en>_script.xlsx`，工作表「朗读稿」，一页一行，列 `页 / 说话人 / 文本 / 语气提示`（英文稿的表头是 `Page / Speaker / Text / Delivery`）；录好的音频按 `P01_zh.mp3` 放回 `07_audio/`，由 `sync_imported` 认领 |
| GET | `/api/picturebook/{id}/summary` | 概览：进度（页图 n/N、朗读 n/N、成片有无）、问题数、下一步 |

任务种类：`pb-draft` / `pb-pages` / `pb-narration` / `pb-assemble`，`module="picturebook"`。

## 8. 实现偏差（2026-09-09 后端落地时记）

- **朗读按句一段**：多句页合成 `07_audio/P01_zh_1.mp3`、`P01_zh_2.mp3`…（单句页仍是 `P01_zh.mp3`）——逐句交替与当前句高亮都要每句实测时长。`manifests/narration.json` 每页每语言记 `segments[]`。手动放进 `07_audio/` 的整页音频 `P01_zh.mp3` 由 `sync_imported` 认领成一段（高亮退化为整页）。产物流 `artifact/audio/{name}` 的 `name` 是不带扩展名的文件名（`P01_zh` / `P01_zh_2`）。
- **插画从不覆盖**：写 `05_pages/P01-<第几次>.png`，`manifests/pages.json` 的 `current` 指针指向当前那张；`artifact/page/{pid}` 按指针取，手动放进去的 `P01.png` 作兜底。
- **翻页钩间隔不数封面**（封面是书名页，本来就没钩）。
- **起草**：`POST /api/picturebook/jobs/draft` 是后台任务 `pb-draft`，草稿在任务结果 `result.draft`（已归一化、附 lint 结果）；`save: true` 直接建书，`book_id` 可指定；`style` 收预设 id / 中文名 / 自写风格锚。
- **多出来的两个只读接口**：`GET /api/picturebook/presets`（风格预设 / 年龄段 / 双语策略 / 音色库 / 示例名，给新建页）和 `GET /api/picturebook/{id}/artifact/asset/{kind}/{name}`（定妆 / 场景锚 / BGM / 音效的预览流）。上传接口顺手把路径写进项目文件对应字段（`characters.<id>.asset` / `scenes.<id>.asset` / `music.file` / `page_turn_sfx`）。
- **`GET /api/picturebook/{id}` 的形状**：`{spec（项目文件内容）, warnings, notes（重复母题提示，不是错误）, problems, stats, pages[]（每页插画 / 朗读状态与 URL）, timeline（预览总长，`estimated` 标记）, final_url, landscape_url, busy, fonts, voices}`。
- **openpyxl** 原契约写「已在依赖里」，实际没有，已加进 `pyproject.toml`。
- 装配的翻页音效没配时用 ffmpeg 现合成一声短促的纸页声（`07_audio/flip.wav`）；BGM 淡入 1 s、按 `music.fade_out` 尾段渐弱；混音用静音底轨定总长（`amix duration=first`）。

（§8 记在 §7 之前是为了让读接口的人先看到偏差；前端契约不变。）

## 7. 前端（`/picturebook`）

- 模块首页：绘本列表 + 新建；
- 新建：方向一句话、年龄段与双语策略、要教的三件事（education）、风格预设（水彩 / 蜡笔 / 剪纸 / 扁平矢量四个预设文案，可改）、起草或空白；
- 书页：概览（进度、问题卡、下一步）/ 分页表（表格编辑：画面、中文、English、翻页钩、教育点、KB 方向；增删排序；每行缩略图 + 单页重出）/ 角色与风格（风格锚、角色与定妆上传、场景锚）/ 朗读（中英音色从音色库选、英文语速、生成、逐页试听与时长）/ 成片（装配、预览、下载、朗读稿导出、可选 16:9）/ 高级（JSON）。
