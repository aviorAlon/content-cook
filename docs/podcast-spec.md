# AI 播客模块 `podcast`（🎙，路由 `/podcast`，2026-09-16）

一期播客 = 一份**对谈稿**（按环节分场景，每句台词标说话人）+ 每位主播一个音色 + 逐句配音混成一条音频，附节目单（章节时间码、要点、听众问题）。目标不是「能出声」，是**听起来像真人在聊**：起草提示词、静态检查、混音停顿三处都围着这一点做。

共用：模型设置（文本模型起草）、音色库（主播音色）、任务中心、导出。**不依赖任何图片 / 视频模型**；没配文本模型也能手写稿子直接合成（TTS 默认走免费的 edge-tts）。

## 1. 项目文件 `content/podcast/<ID>.json`

```json
{
  "id": "PC-20260916-1",
  "title": "为什么我们戒不掉短视频",
  "format": "duo",                       // solo 单人独白 / duo 双人对谈 / interview 访谈 / roundtable 圆桌（3–4 人）
  "language": "zh",                      // zh / en：稿子与合成语言
  "brief": {
    "topic": "刷短视频停不下来，到底是我们意志力差还是产品设计使然",
    "angle": "不做道德批判，从产品机制和个人经历两边聊",
    "audience": "25–35 岁、自己也在刷的上班族",
    "takeaways": ["无限流是怎么设计的", "两个亲测有效的收手办法"],
    "materials": "老周去年戒了三个月又复发；一份 2025 年的屏幕时间数据……",   // 自由文本：数据、案例、亲历——起草时必须用上
    "tone": "轻松但不水，允许互相拆台",
    "avoid": ["说教", "念数据不给来源"]
  },
  "target_minutes": 12,
  "hosts": [                             // 1–4 人；说话人只能是这里的 id
    {"id": "zhou", "name": "老周", "role": "host",     // host 主播 / cohost 搭档 / guest 嘉宾
     "persona": "做了十年产品，爱举例子，说话慢，喜欢反问", "stance": "认为是设计的锅",
     "voice": "zhou-voice"},             // 音色库档案 id；或内联 {"engine": "edge", "voice": "zh-CN-YunxiNeural", "rate": "-2%"}；空 = 占位音色
    {"id": "xiao", "name": "小夏", "role": "cohost", "persona": "语速快，爱打断，会认错", "stance": "觉得主要还是自己的问题", "voice": ""}
  ],
  "segments": [                          // 场景 = 环节，按顺序播；起草会填满，也可以手写
    {"id": "G01", "kind": "cold_open", "title": "冷开场", "goal": "一个具体场景把人拽进来",
     "beats": ["小夏凌晨两点还在刷"], "questions": [],
     "lines": [{"who": "xiao", "text": "……我看了眼时间，两点十七。", "cue": "轻笑"}]},
    {"id": "G03", "kind": "qa", "title": "听友问答", "goal": "",
     "beats": [], "questions": [{"from": "听友阿May", "text": "把手机设成黑白真的有用吗？"}],
     "lines": [{"who": "zhou", "text": "阿May 问：把手机设成黑白有用吗？", "kind": "question"},
               {"who": "xiao", "text": "有用，三天。第四天我就调回来了。", "kind": "answer"}]}
  ],
  "music": {"intro": "", "bed": "", "outro": "", "bed_gain": 0.18, "duck": 0.45},   // 项目内相对路径（content/assets/<ID>/…），可空；除了上传，也能从配乐模块「放进项目」投放进来（docs/music-spec.md §3）
  "pause": {"line": 0.35, "segment": 1.2},        // 句间 / 环节间停顿秒；cue 是「停顿」的句前再加 0.6s
  "draft": {"model": "", "backend": "", "created_at": null, "usage_tokens": null, "note": ""}   // 最近一次起草的记录
}
```

- `segments[].kind` 只能是 `cold_open / intro / topic / qa / outro`；`lines[].kind` 是 `talk / question / answer`（省略 = talk）；`cue` 只认 TTS 与剪辑能用的：`笑 / 轻笑 / 停顿 / 打断 / 叹气 / 加快 / 放慢 / 低声 / 强调`（空 = 无）。
- 说话人 `who` 必须是 `hosts[].id`；`solo` 只能有 1 人，`duo` 恰好 2 人，`interview` 至少 1 位 `host` + 1 位 `guest`，`roundtable` 3–4 人。
- 归一化（`src/podcast/spec.py`）：环节 id 缺了按 `G01…` 补、不许重复；说话人不在人物表 → 报错不落盘（跟剧本层同一原则：写错名字等于没人说）；`target_minutes` 1–90；`pause` 0–5 秒。运行时键 `output_root`（`output/podcast/<ID>`）与 `project_root` 写盘时去掉。

## 2. 真人感：起草（`src/podcast/craft.py`）

一次文本模型请求，JSON Schema 约束返回 `segments`。系统提示词把「真人播客 vs AI 直出稿」的差别写成**可执行约束**（这是经验规则，不是测量结论；来源标在 §7）：

1. **口语不是书面语**：短句、半句、接话、自我修正（「不对，我说错了」）；不用「首先 / 其次 / 综上 / 值得注意的是」这类连接词；不列点。
2. **有人不同意**：双人以上至少一处明确分歧，而且不马上和解；有追问（「你说的『上瘾』具体指什么？」）。
3. **具体压倒观点**：每个 `topic` 环节至少一段亲历或案例，带时间 / 地点 / 数字，材料从 `brief.materials` 里取，不许编数据。
4. **每个人是一个人**：按 `persona` / `stance` 说话，口癖有但不密；两人不许观点一致到分不清谁在说。
5. **问答要像回答**：把听众的称呼和问题念出来；先给结论再讲原因；允许「这个我不确定」；不复述问题。
6. **冷开场不自我介绍**：从一个具体场景或对话中段切入；`intro` 再介绍人与题；`outro` 不总结全篇，只留一句 + 互动号召 / 下期预告。
7. **禁止**：排比、每段末尾升华、「今天我们聊了三点」、机械轮流（一句一换人）、心理描写；`cue` 只标能演的。
8. **时长可数**：中文按 4.5 字 / 秒估，目标分钟 ±20%。

`POST /{id}/draft` 起草整期（覆盖所有环节的 `lines`，环节骨架没有时连骨架一起生成）；`POST /{id}/segments/{sid}/redraft` 带 `note` 只重写一个环节（其余环节原文进提示词当上下文，不改）。两者都是后台任务 `pc-draft`、1 次文本模型请求；`draft` 记录写进项目文件。

## 3. 真人感：静态检查（`src/podcast/lint.py`，不花钱）

告警句形状跟别的模块一样：`segment G02: 影响——改法` / `全期：影响——改法`，接口层拆成问题卡（分类「播客」）。每条都能落到稿子上：

| 判据 | 触发 |
|---|---|
| 结构 | 没有 `cold_open` / `intro` / `outro`；`qa` 环节没有听众问题；环节没有台词（起草后） |
| 书面语 / 口播腔 | 台词里出现 首先 / 其次 / 最后 / 综上所述 / 总而言之 / 值得注意的是 / 不难发现 / 总的来说 / 一方面…另一方面 / 让我们 |
| 独白太长 | 双人以上：一句超过 220 字（≈ 50 秒没人插话） |
| 机械轮流 | 环节 ≥ 8 句且每一句都换人 |
| 一人独大 | duo / roundtable：一个人的字数占比 > 70% |
| 全程没分歧 | 双人以上整期没有 不同意 / 不对 / 我倒觉得 / 不见得 / 未必 / 可是 / 等等 这类反对 |
| 没有具体例子 | `topic` 环节全文没有数字，也没有 有一次 / 上周 / 去年 / 那天 / 我记得 / 比如 |
| 填充词 | 嗯 / 啊 / 对对对 / 就是说 / 那个 / 然后 占比 > 8%（TTS 念出来像结巴）；双人以上 ≥ 20 句一个填充词都没有（播音腔，软提示） |
| 问答 | `answer` 少于 20 字，或开头 12 字与问题重合（复述问题） |
| 结尾总结全篇 | `outro` 里出现 今天我们聊了 / 总结一下 / 三点 / 回顾一下 |
| 时长 | 估算分钟（字数 ÷ 4.5 ÷ 60 + 停顿）与 `target_minutes` 偏差 > 25% |
| 音色 | 两位主播同一个音色；主播没绑音色（占位音色能出声但分不清人） |
| cue | 不在允许清单里 |

估算 `estimate = {chars, lines, seconds, minutes}` 随详情返回。

## 3.5 真人感审稿（`src/podcast/review.py`，1 次文本模型请求，2026-09-17）

静态检查只能数词（书面语词表、填充词占比、换人次数）；「两个人观点一致到分不清谁在说」「回答只是敷衍没有结论」「台词里的数据不在素材里」是语义判断，词表判不了。所以在 lint 之外再加一道模型审，跟短剧的按题材审稿 / 剧本工艺审稿同一套闸门：**模型逐条判 + 程序逐字核对引文**。

- **清单** `skills/ai-podcast/scripts/humanlike_checklist.json`（id `podcast-humanlike-v1`，随包）：三组共 11 条——口语（speech，4 条：书面连接词 / 分点列举、独白太长没人接话（阈值与 lint 的 `MONOLOGUE_CHARS` = 220 同一个数）、机械轮流与填充词堆叠、心理描写与段末升华）、对谈动态（dynamics，4 条：整期没有分歧或马上和解、没有追问、分不清谁在说或说了与立场相反的话、一人独大）、内容（substance，3 条：话题环节没有带时间 / 地点 / 数字的事或编了素材里没有的数据、问答复述问题 / 敷衍没结论、冷开场自我介绍 / 收尾总结全篇）。每条写明判 risk / violated 的条件与该引什么；`basis` 标明由 `craft.py` 第几条约束或 `lint.py` 哪个判据改写、置信度低。
- **正文**：每个环节的种类 / 标题 / 目标 / 节拍 / 听众问题 / 每句台词（「说话人名：文本」），环节号就是 segment id；节目名、主播表、选题 brief 只当上下文进提示词，明说不能引用。提示词要求引文是「一句台词、相邻的两句台词（按原顺序连着抄）、一条节拍或一条听众问题」，去掉标点至少 4 个字，要引的那句太短就连相邻一句一起引并在 why 里说明。
- **核对**：复用 `storyvideo.genre_review.verify_items`，把环节当「场」传进去——可引用的字段是每一句台词、相邻两句台词拼成的「合句」（每对两种写法：不带名、带说话人名；核对前去掉空白标点）、每一条节拍、每一条听众问题；标题与目标不算证据；所列环节号必须存在。合句只给核对用，不进提示词正文也不进哈希。合句是裸拼接、核对是子串匹配，只跨句界的碎片（前一句尾 + 后一句头）也会对上，所以核对之后再补一道：没落在任何单句 / 节拍 / 听众问题里的引文，必须整段包含合句中的某一整句，否则按「引文找不到」降级。对不上的条目保留但 `evidence_verified: false`、violated 降成 risk、永远不进告警；编号对不上清单的直接丢掉。
- **落盘与过时**：`<output_root>/manifests/humanlike_review.json`，`spec_hash` = 上下文（主播表、brief）+ 每个环节正文 + 清单 id 的稳定哈希；改了台词 / 节拍 / 素材 / 主播立场 = 过时（只给一句提示，不出告警），改音乐 / 停顿 / 音色不算。
- **告警**：只有核对过的 risk / violated 进问题卡，分类仍是「播客」：`segment G03: 按真人感清单审稿（模型判断）：违反口语「条目第一句」，原文「…」，理由「…」——建议「…」`；多环节 / 无环节挂「全期：」并在原文前列出「（见 G01、G02）」。详情 `problems` 同时带静态检查与审稿的卡。
- **接口**：`POST /api/podcast/{id}/humanlike-review` → `{task_id, cost_note}`（任务 `pc-review`「播客审稿」；稿子没台词 400、没配文本模型 400、清单读不出来 400、在跑 409）；`GET` 同路径 → `{checklist:{id,name}, review, stale, note, problems, cost_note, task_id}`。detail 的 `busy` 多一个 `review`。
- **前端**：脚本页起草卡之后永远显示「真人感审稿」卡（跟短剧两张卡共用 `ModelReviewCard`，接口前缀 / 引用号链接 / 刷新函数 / 「没对上」说明由 props 决定）：跑之前弹成本确认，结果按 违反 / 有风险 / 没问题 分组，引文高亮，环节号链到本页对应环节卡（hash 锚点 `#segment-G03`），没对上原文的收在折叠组里；页脚「这是模型按真人感清单的判断，不是保证」。

## 4. 配音与混音（`src/podcast/audio.py`）

- **逐句合成**到 `output/podcast/<ID>/07_audio/<环节>_<序号>.mp3`，清单 `audio_manifest.json` 记 `{segment, index, who, text, file, duration, hash, voice, engine, placeholder}`；文本 + 音色参数的哈希没变就跳过（跟绘本朗读同一套）。主播 `voice` 是音色库 id 就走 `genkit.voices.synthesize_with_profile`（edge / settings / minimax 克隆都行），内联档案照用，空 → 按语言的占位音色（zh `zh-CN-YunxiNeural` / `zh-CN-XiaoxiaoNeural` 轮着分给主播，尽量不重复）。
- **时间线**：句间 `pause.line`，环节间 `pause.segment`，`cue` 含「停顿」句前再加 0.6s；`music.intro` 有文件就在 `cold_open` 之后、`intro` 之前整段播（没有 cold_open 就放最前面），`music.outro` 在最后一句之后播；章节 = 每个环节的起点。
- **混音**：ffmpeg 一次 `filter_complex`：静音底轨定总长 + 每句 `adelay` + 垫乐 `bed` 循环、`bed_gain`，说话时段按 `duck` 压低（跟短剧后期同一套 `volume='if(...)'` 表达式）、末尾渐弱 → `08_mix/<ID>.mp3`（128k）。同时写 `08_mix/chapters.json`、`transcript.txt`（`[mm:ss] 老周：……`）、`shownotes.md`（标题、一句话、主播、章节时间码、要点、听众问题）。
- 任务 `pc-audio`（`POST /{id}/synthesize`，`{force?}`）：合成 + 混音一次跑完；没装 ffmpeg 时逐句音频照样出，混音那步报清楚原因。
- **交付包**（`src/podcast/export.py`，任务 `pc-export`，2026-09-17）：把一期拿得出手的东西打进 `output/podcast/<ID>/09_export/<ID>-<YYYYMMDD-HHMM>/` + 同名 zip，跟短剧 / 图片集交付包同一套规矩（只拷白名单、不动原文件、从不覆盖、manifest.json + checksums.sha256）。包里有：`spec.json`（项目文件磁盘原样）、`mix.mp3`（成品，没混音就不带并在 manifest 的 `excluded` 标明）、`shownotes.md` / `transcript.txt` / `chapters.json`（有就带）、`lines/<环节>_<序号>.mp3`（逐句，按 `audio_manifest.json` 的清单来、不扫目录、0 字节的残文件不算；`include_lines=false` 不带）、`ai_disclosure.md`。每个文件按 `output_root` 内的固定位置去找，不跟符号链接、不出 `output_root`；`~/.storystudio`、`content/assets`（上传的音乐）、账本、`manifests/`、`audio_manifest.json` 本身都不进包。`manifest.json`：id、标题、形式、主播（引擎 / 音色 / 是否占位）、导出时间、程序版本、时长、章节、每句的说话人与时长、文件清单与大小、`audio.stale`（稿子改过、清单哈希对不上时为 true——包照打，但说清音频是旧的）、`excluded`。同一分钟已导出过 → 明确报错不覆盖。
- **AI 生成说明与工具清单**（`ai_disclosure.md`，两段标题【AI 生成说明】【用到的生成工具】，口径写在文件尾注里）：稿子由文本模型起草（`spec.draft` 的 `model` / `backend` / `created_at`）或手写（`draft.model` 为空即算手写；起草之后的人工修改项目文件不记录，声明里写明）；每位主播的音色引擎 / 音色名 / 是否占位来自 `audio_manifest.json` 的 `hosts`（最近一次合成时记的），老清单没记或主播是合成之后加的才按导出时的音色设置并在口径里说明。全部按项目文件与清单汇总，不查账本（播客不走账本）。

## 5. 接口 `/api/podcast/...`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/podcast` | 列表：`[{id, module:"podcast", pipeline:"podcast", title, format, hosts, minutes, segments, lines, progress:{drafted, audio, mixed}, path}]` |
| GET | `/api/podcast/next-id` | `{id}`（`PC-YYYYMMDD-n`） |
| GET | `/api/podcast/formats` | `{formats:[{id,name,english,hosts:[min,max],note}], kinds:[…], cues:[…], defaults:{target_minutes, pause, music}, limits:{target_minutes:[1,90], hosts:[1,4]}, examples:[…], craft:[八条约束的中文]}` |
| POST | `/api/podcast` | 新建（body = 项目文件，不填 id 由服务端编号）→ detail |
| POST | `/api/podcast/examples/create` | `{name?, id?}` 复制示例 → detail |
| GET / PUT / DELETE | `/api/podcast/{id}` | 详情 / 整体保存（校验不过 400「改动没通过校验，没有写盘：…」）/ 归档到 `.webui_deleted/podcast/` |
| POST | `/api/podcast/{id}/draft` | `{note?}` → `{task_id, cost_note}`；没配文本模型 400；在跑 409 |
| POST | `/api/podcast/{id}/segments/{sid}/redraft` | `{note}` → 同上 |
| POST | `/api/podcast/{id}/synthesize` | `{force?}` → `{task_id, cost_note}`（edge 免费；settings / minimax 按音色库的引擎算） |
| POST | `/api/podcast/{id}/assets` | `{filename, data_base64, slot: intro|bed|outro}` 上传音乐（mp3/wav/m4a ≤ 30MB）→ 写进 `music.<slot>` |
| GET | `/api/podcast/{id}/artifact/audio/{name}` | 逐句 mp3 或混音成品（只认清单里的文件名） |
| GET | `/api/podcast/{id}/artifact/text/{name}` | `shownotes.md` / `transcript.txt` / `chapters.json` |
| POST / GET | `/api/podcast/{id}/humanlike-review` | 真人感审稿（§3.5）：POST 起 `pc-review` 任务（1 次文本模型请求；没台词 / 没文本模型 / 清单缺失 400，在跑 409）；GET 返回 `{checklist, review, stale, note, problems, cost_note, task_id}` |
| POST | `/api/podcast/{id}/export` | `{include_lines?}` → `{task_id, name}`（后台任务 `pc-export`）；一句都没合成过 400「还没有可以导出的东西——先合成」；这一分钟已导出过 409；在跑 409 |
| GET | `/api/podcast/{id}/export` | `{id, packages:[{name, size, created, files, bytes, mixed, lines, duration, url}], export_dir}`（新到旧） |
| GET | `/api/podcast/{id}/export/{name}` | 下载 zip；只认 `09_export` 里的纯文件名，`..`、子路径、不是 zip、不存在一律 404 |

**detail** = `{id, module, title, spec, estimate, warnings, problems, hosts:[{…, voice_label, placeholder, missing, no_tts}], audio:{manifest, lines:[{segment,index,who,text,file,url,duration,stale}], mix_url, mix_duration, chapters, stale, mix_stale, ffmpeg}, texts:{shownotes_url, transcript_url}, draft, busy:{draft, audio}, limits}`。`audio.stale` = 稿子改过、清单里的哈希对不上，或某句的 mp3 是 0 字节 / 跟合成时的大小对不上（要重新合成）。`audio.mix_stale` = 成品在、但混音时记在 `08_mix/chapters.json` 里的输入指纹（各句哈希与时长、停顿、三个音乐槽的路径与大小；挂了垫乐时再加 `bed_gain`、`duck`——没垫乐时它们不进 ffmpeg 命令，改了也不算过时）跟当前 spec 对不上——成品仍可试听，`progress.mixed` 为 false，再合成一次会重混；交付包的 `manifest.audio.mix_stale` / `mix.stale` 同口径。`hosts[].missing` / `no_tts` = 绑的音色库档案已不存在 / 只有文字描述，详情里按占位音色显示，`warnings` 里会点名；保存照收，但稿子里有台词的主播绑着坏档案时 `POST …/synthesize` 400（换一个档案，或把这位主播的音色清空、明说用占位音色再合成）；还没台词的主播（访谈 / 圆桌里没轮到的嘉宾）只告警、不拦。`DELETE` 在有任务跑着（起草 / 合成 / 审稿 / 导出）时 409；归档把 `content/assets/<ID>` 一起搬走，产物目录里有文件被占用就整体不搬、409。上传音乐时用 ffprobe 探一次时长，探不出 400。

任务种类：`pc-draft`「播客起草」、`pc-audio`「播客配音」、`pc-review`「播客审稿」、`pc-export`「播客导出」。`module="podcast"`。detail 多 `exports`（交付包列表）、`busy` 多 `review` / `export`。测试注入点：`DISPATCH`（文本模型）、`SYNTHESIZER` / `PROBER` / `FFMPEG`（配音混音）。

## 6. 前端 `/podcast`

- **首页** `/podcast`：列表（标题 / 形式 / 主播数 / 预计分钟 / 进度：已起草 · 已配音 · 已混音）+「+ 新建播客」+「复制示例（不花钱）」。
- **新建** `/podcast/new`：标题、形式下拉（四种，切换时按人数预置主播行）、选题 / 角度 / 听众 / 要点 / 素材 / 语气 / 避免、目标分钟、主播表（名字 / 角色 / 人设 / 立场 / 音色下拉，音色来自 `GET /api/voices`）。POST → 进「脚本」页。
- **项目页** `/podcast/p/:id/{script,audio,advanced}`：
  - **脚本**：顶部「起草整期」（确认框写清 1 次文本模型请求）+ 估算（字数 / 分钟 vs 目标）+ 问题卡；环节按顺序一张卡：种类 / 标题 / 目标 / 节拍（一行一条）/ 听众问题（qa）/ 台词表（说话人下拉 · 台词 · cue 下拉 · 种类），每张卡有「重写这一段」（带一句要求）与增删移动；整页一个「保存」（PUT 整份）。
  - **配音**：主播音色绑定（下拉 + 试听链接到音色库）、停顿两档、三段音乐上传、「合成整期」按钮（cost_note）、逐句列表（`<audio>`）+ 成品播放器 + 章节时间码 + 节目单预览 + 下载（shownotes / transcript / mp3）。稿子改过显示「稿子改过，重新合成」。底部「导出交付包」卡：一句说明包里有什么、按钮（不花钱不问确认，起任务并轮询；没合成过禁用并提示；在跑禁用）、已有交付包列表（名字 / 大小 / 时间 / 下载，没混音的带标记）。脚本页起草卡之后是「真人感审稿」卡（§3.5）。
  - **高级**：JSON 整份改、归档。
- 侧栏模块块、任务中心种类名、最近记录路由 `/podcast/p/{id}/script`。

## 7. 来源与置信度

- 「真人播客 vs AI 稿」的八条约束是按播客制作常识与本仓库对 AI 直出稿的观察归纳的**经验规则**，没有抽样测量；分歧 / 具体例子 / 填充词这些阈值（220 字、70%、8%）是可调默认值，不是行业标准。
- 4.5 字 / 秒的语速估算沿用短剧（`storyvideo.preview.CHARS_PER_SECOND`），是经验值，不是抽样测量。
- **真实文本模型跑通一次（推理型文本模型经 OpenAI 兼容接口，双人对谈、目标 5 分钟）**：起草约 5 分钟，稿子口语化、有分歧、用上了素材里的具体事、问答先给结论、收尾没有总结全篇；静态检查准确报出好几个环节「每一句都换人」。据此把提示词里「禁止机械轮流」改成能数的要求（每个环节至少两处同一个人连说两三句，短接话单独成句），用「只重写这一段」重写其中一段后这条提醒消失。真人感审稿判出一条有风险，引文是相邻两句连引、核对通过。edge-tts 合成与混音走通，成品时长跟估算的差不多。
- 同一次暴露并修掉的三个问题：① 推理型模型可能先推理几分钟才出正文，一次性等整包的请求会在 180 秒超时——文本适配器改成流式接收（`genkit.adapters.openai_compat`，短剧起草与三种审稿同样受益）；② 两个名字不同的音色库档案底下是同一个 edge 音色，只比档案 id 查不出来——音色重复改成按解析后的真正音色比；③ 混音成品整体只有 -23.1 LUFS，比常见播客小声约 7 dB——混音最后加响度标准化到 -16 LUFS（Apple Podcasts 的建议值），实测 -16.5 LUFS。
- **仍未验证**：只跑了一个题目、一个模型，样本是一；推理型模型写一期要五分钟左右，换非推理模型会快很多但稿子质量没比过；edge-tts 的中文对谈感（语气、停顿）没做主观评测；minimax 克隆音色走的是音色库现成的路。
