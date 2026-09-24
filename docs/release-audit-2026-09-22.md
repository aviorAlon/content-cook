# 发布前体检报告（2026-09-22，v0.1.0 打 tag 前）

十个维度并行审计（在临时数据根里起一个空模型设置的服务，真调接口 / 真跑 ffmpeg 与 edge-tts / 读代码），87 条发现逐条单独复现核实：**86 条成立**（blocker 2、major 42、minor 42），1 条不成立。安全这一维这一轮没有单独跑完，路径类问题由别的维度抓到（DRAMA-07 / DRAMA-08 / FAM-2）；安全另做了一轮专项自查，见 [security-review-2026-09-23.md](security-review-2026-09-23.md)。

**86 条全部已修**（分 9 组实现 → 独立复审 → 返工，合并后后端 2807 条、前端 345 条测试通过）。下表只列问题与状态，修法落在对应模块的代码与回归测试里。

| 严重度 | 编号 | 模块 | 问题 | 状态 |
|---|---|---|---|---|
| blocker | DRAMA-01 | drama | 没配模型时点「生成」：项目被锁定、首帧任务被记成「提交结果不明」，之后 generate 永远 409，界面无任何出路 | 已修 |
| blocker | POST-1 | post | 后期合成把全部镜头塞进一条 ffmpeg 命令的 xfade 链，内存随镜数线性涨（≈0.88 GB/镜@1080x1920×5s），60 镜要 53 GB；普通 16–32 GB 机器十几到三十镜就 OOM | 已修 |
| major | ADP-01 | adapters | 「测试连接」对 openai_speech / openrouter_tts / ark_images / ark_video 根本不联网，假密钥、不存在的域名也报 ok:true | 已修 |
| major | ADP-02 | adapters | 火山方舟任何提到 `content` 参数的错误（参数越界、任务不存在等）都被分类成 ProviderRejected「审核拦截」，界面据此隐藏「重提」按钮 | 已修 |
| major | ADP-03 | adapters | 手填地址「用这个地址扫」→「应用」会把 host:port 原样写成 base_url，Ollama / LM Studio / vLLM / llama.cpp / 本地语音的 /v1 路径丢了，应用后文本角色打 404 | 已修 |
| major | ADP-04 | adapters | 设置页换来源后旧密钥跟着新来源走：从 OpenAI 换成本机 SD WebUI，旧的 sk- 密钥会以 Bearer 头发给本机/局域网的 WebUI | 已修 |
| major | ADP-05 | adapters | settings.json 坏了（含 UTF-8 BOM）时 GET/PUT /api/settings、/api/voices/catalog、/api/system/local-hint 全部 500，设置页打不开、也没法在程序里修 | 已修 |
| major | ADP-06 | adapters | 环境变量覆盖（STORYSTUDIO_<ROLE>_API_KEY 等）会被任何一次 PUT /api/settings 写进 settings.json 落盘 | 已修 |
| major | COMPAT-01 | compat | 删除项目后用同一个 id 重建，「开始制作」永远被旧账本锁死，且报错文案指向一个从没发生过的事 | 已修 |
| major | COMPAT-02 | compat | 删除项目不搬 <id>.screenplay.json（和 content/assets/<id>/），同 id 新项目会「继承」旧项目的剧本 | 已修 |
| major | COMPAT-05 | compat | 英文界面：概览页问题卡的 impact / fix 正文露中文——整句 warning 有英文规则，但问题卡拆成两半后没有对应规则；「偏紧」这条连整句都没有规则 | 已修 |
| major | COMPAT-11 | compat | 没有列出/恢复归档项目的接口，界面承诺「想找回手动搬回来即可」但归档时文件被改名，用户照着搬会搬错；draft_cache 也没有任何清理入口 | 已修 |
| major | DRAMA-02 | drama | 归档（DELETE）一个已开始制作的项目后，用同一个 id 新建项目永远无法「开始制作」；输出目录被清空同样把项目永久卡死 | 已修 |
| major | DRAMA-03 | drama | 概览/详情把 stale（已作废）的裁决当成「已通过」，runner 却拒绝放行——两边打架，用户看到「全部通过」但成片/审片一律被拦 | 已修 |
| major | DRAMA-05 | drama | 插卡 after 指向自己或互相引用能存盘，合成时这些卡被静默丢掉，成片里没有、清单还说有 | 已修 |
| major | DRAMA-06 | drama | voice_over 的 lines[].at、整块 edge.rate/pitch、engine 都不校验，逐句 edge 却严格校验——写坏了要到合成时才炸 | 已修 |
| major | DRAMA-07 | drama | 保存草稿不校验 episode_id：能写到 content/story_video 之外；spec.id 与 episode_id 不一致也能存，产物链接与账本都对不上 | 已修 |
| major | DRAMA-08 | drama | PUT cast 接受绝对路径/穿越路径作为 asset，GET /cast/{id}/asset 会把本机任意文件发出去 | 已修 |
| major | DRAMA-09 | drama | 审片/生成的多条错误文案误导或英文原样漏出 | 已修 |
| major | DRAMA-13 | drama | import-legacy 可对任何项目重复调用：空草稿会被静默锁定，已审片的项目 run 状态倒退成 awaiting_frames_qc | 已修 |
| major | FAM-1 | family | 任务目录里一个坏记录让 GET /api/tasks 整个 500（数组/字符串 JSON、created_at 是字符串） | 已修 |
| major | FAM-2 | family | 配乐投放到短剧时 target 不校验 id：`../picturebook/<book>` 先把 mp3 拷进别的模块目录，再用内部英文异常拒绝 | 已修 |
| major | FAM-3 | family | 没配模型时出图/插画/朗读/配乐接口照样 200 起任务，任务以 succeeded 结束、任务中心显示「成功」，失败尝试还写进项目文件 | 已修 |
| major | FE-01 | frontend | refreshProject() 一刷新就把用户没保存的表单整个覆盖掉（生成期间每 8 秒一次） | 已修 |
| major | FE-02 | frontend | 没配生图模型时点「生成首帧」不是 400 拒绝，而是把第一镜记成「提交结果不明」，之后这镜在界面上永远卡住、无法重提 | 已修 |
| major | PKG-1 | packaging | 桌面版 ASR 安装提示不可执行：冻结解释器忽略 PYTHONPATH，faster-whisper 在打包版永远装不上 | 已修 |
| major | PKG-5 | packaging | 没配模型时批量生成任务 0 成功、全部失败，任务记录 status 仍为 succeeded（不确定是否算设计如此） | 已修 |
| major | PC-01 | podcast | 没配文本模型时起草、重写、真人感审稿不返回 400，而是起任务后失败 | 已修 |
| major | PC-02 | podcast | 同一播客并发点「合成」互斥失效：多个 pc-audio 任务同时跑、同时写同一批 mp3 和 audio_manifest.json | 已修 |
| major | PC-03 | podcast | 0 字节（或残损）的逐句 mp3 被当成「已合成、不过时」：合成跳过、混音每次失败，只有「全部重合成」能救 | 已修 |
| major | PC-04 | podcast | 混音成品没有新鲜度：改停顿 / 换音乐 / 混音失败之后，详情仍 mixed=true、mix_url 指向旧成品，导出包把旧成品当交付物 | 已修 |
| major | PC-05 | podcast | 上传的音乐不验内容：16 字节垃圾当 mp3 上传 200；片头 / 片尾探不出时长就静默从成品里消失，垫乐则混音失败 | 已修 |
| major | PC-06 | podcast | 音色引用三种静默退化：档案 id 不存在 → 占位音色；档案只有描述没 TTS 音色 → 默认 YunxiNeural；内联 engine 乱写 → 走 edge；保存都 200、无告警 | 已修 |
| major | PC-09 | podcast | 归档不搬 content/assets/<ID>；合成进行中允许删除，任务继续写并把 output/podcast/<ID>/07_audio 重建出来 | 已修 |
| major | POST-2 | post | 后期合成直接写 <id>_post.mp4，没有临时文件+原子替换：超时/OOM/中途出错会把上一次好的成片截成半个文件，而界面和导出又优先拿 _post.mp4 当交付物 | 已修 |
| major | POST-3 | post | 后期版字幕不折行、忽略 subtitle.language 和 subtitle.display：长台词/长旁白两边出画，双语/英文项目在后期版只有中文或没有字幕，和装配版成片不一致 | 已修 |
| major | POST-4 | post | 后期块的数值字段不校验：插卡 dur、旁白 at、画面文字 at/until/y/size 写坏了都能保存，点合成才炸成裸 ValueError，或者悄悄出错的成片 | 已修 |
| major | POST-7 | post | 横屏交付（如 1920x1080）时插卡/字幕卡固定按 1080x1920 画再缩放，成片里变成中间一条竖条、两侧黑边 | 已修 |
| major | POST-8 | post | 后期版只认 delivery.width/height/fps，装配版还认 delivery.resolution / video_params.resolution / fps：两份成片可能尺寸帧率不同 | 已修 |
| major | SC-1 | script | 没配文本模型时 POST /generate、/review 返回 200 并起任务，任务在后台失败；契约要求 400，也不该留失败任务记录 | 已修 |
| major | SC-2 | script | 「写」页删掉一段后保存永远 400：只要项目有任何一版，段落骨架里就删不掉段 | 已修 |
| major | SC-3 | script | 投放到短剧场次 / 镜时界面不给选写入方式，后端默认 replace 整段覆盖目标原有台词；落点选项文案却说「会按上面的方式写入」 | 已修 |
| major | SC-5 | script | 短剧项目 cast 里的地点条目（如 office，只有 block 没有 name）被当成可映射的「人物」，界面按顺序自动映射会把说话人映到地点，后端照写 who: office | 已修 |
| major | SC-7 | script | 导入时目标角色 id 与工作台已有的、名字不同的角色撞 id，台词被静默记到已有那个人名下，返回体 speakers 为空、没有任何提示 | 已修 |
| minor | ADP-07 | adapters | HTTP 429 限流一律当成 QuotaExhausted「余额耗尽」：整批停在 blocked_quota，界面提示去充值 | 已修 |
| minor | ADP-08 | adapters | 一个来源的 ping 在上游返回空输出（且没报错）时 IndexError，「测试连接」直接显示 "IndexError: list index out of range" | 已修 |
| minor | ADP-09 | adapters | lint 的参考图上限只按模型 id 在所有来源里找最长前缀：gpt-image-2 配在另一个来源上也套 OpenAI 的 16 张；那个来源的记录本身没有上限，两处口径不一致 | 已修 |
| minor | ADP-10 | adapters | 能力表说 openai_images 支持 1K / 2K 两档，适配器实际忽略 image_size，只按画幅发固定 1024 边尺寸 | 已修 |
| minor | ADP-11 | adapters | video_params.count 对 Seedance 等模型不校验、原样透传给视频来源；提交/查询只取第一条地址，count>1 多出的视频白花钱 | 已修 |
| minor | ADP-12 | adapters | 9~12 位的密钥打码后只遮 1~4 个字符，接口/设置页几乎回明文 | 已修 |
| minor | COMPAT-03 | compat | 任务/起草记录目录里只要有一个「合法 JSON 但不是对象」或 created_at 非数字的文件，/api/tasks 与 /api/scripts/jobs 整页 500 | 已修 |
| minor | COMPAT-04 | compat | jobs 目录里有一个非对象 job 文件时，POST /api/scripts/save 先把项目写盘再 500——界面报「失败」但项目已建成，重试变成 409「已经存在」 | 已修 |
| minor | COMPAT-06 | compat | 任务记录的 error（以及 result.failed[].error）不在翻译键里，英文界面任务中心整列中文，还带 Python 异常类名前缀 | 已修 |
| minor | COMPAT-07 | compat | 多字段 422 文案只翻了前缀：「Invalid request — 字段 target_seconds：必填，没有给；字段 camera_profile：必填，没有给」 | 已修 |
| minor | COMPAT-08 | compat | 带 UTF-8 BOM 的项目文件在 /api/recent 里标题退回 id，而各模块列表接口能正常读出标题 | 已修 |
| minor | COMPAT-09 | compat | 静态扫描：844 条中文 raise 文案里 98 条没有英文规则（会经 400 detail / 任务 error 到达英文界面） | 已修 |
| minor | COMPAT-10 | compat | 英文界面里镜头库备注、配乐简报、播客形式说明、表情包模板项名等静态数据没有英文版 | 已修 |
| minor | DRAMA-04 | drama | 插卡 dur 不校验：缺失/0/负数/字符串都能存盘，后期合成时以原始 Python 异常失败 | 已修 |
| minor | DRAMA-10 | drama | 后台任务失败时把异常类名和 traceback 原样落盘并返回给界面 | 已修 |
| minor | DRAMA-11 | drama | 一批字段能写进任意值，问题推迟到生成/合成时才暴露 | 已修 |
| minor | DRAMA-12 | drama | 项目文件被外部改动或输出目录被清空时，详情/概览不提示不一致，next_step 指向必然失败的动作 | 已修 |
| minor | DRAMA-14 | drama | 并发写没有互斥：两个同名 revise-edit/apply 都返回 200（后者覆盖前者的改动），两个逐镜 PUT 丢更新 | 已修 |
| minor | DRAMA-15 | drama | 没配生图模型时对比试跑任务状态是 succeeded，只有候选列表里藏着错误 | 已修 |
| minor | FAM-4 | family | 任务失败原因带内部异常类名（ClipGenerationError: / ScriptGenError: / ProviderCommandError:），模块弹窗原样展示 | 已修 |
| minor | FAM-5 | family | 上传只看扩展名不看内容：假 png、png 内容存成 .jpg、16 字节文本存成 BGM 都 200，后面一步才用带绝对路径的 PIL 错误失败 | 已修 |
| minor | FAM-6 | family | 同一张参考图重复上传会在 subject.assets / reference_images 里重复记录，占用 6/10 张的名额 | 已修 |
| minor | FAM-7 | family | 档案 engine=settings 但没配语音模型时「试听」返回 502，把本机配置问题报成网关错误 | 已修 |
| minor | FAM-8 | family | model_compare 出图响应里的 model / cost_note 写的是 @image，而每张实际用的是 items[].meta.model | 已修 |
| minor | FE-03 | frontend | 离开任务中心时若有一次 load 在飞，卸载后仍每 3 秒轮询 /api/tasks 不停 | 已修 |
| minor | FE-04 | frontend | 三个动态标签没有英文翻译：情绪 / 乐器（配乐页）、硬切（绘本分页表） | 已修 |
| minor | FE-05 | frontend | 英文界面里概览「检查提醒」的 impact、任务中心的失败原因、生成 409 的 detail 仍是中文或中英混排 | 已修 |
| minor | FE-06 | frontend | Post / Shots / Final 页的 pollTask 没有 catch：服务重启或任务记录 404 时只剩控制台报错，用户没有任何提示 | 已修 |
| minor | FE-07 | frontend | 插卡预览图的缓存戳只用 JSON 长度，改成等长内容后缩略图不更新 | 已修 |
| minor | FE-08 | frontend | 「用它配 …」/「应用到勾选的角色」成功后把五个角色卡的表单全部重灌，其他角色卡里没保存的输入静默丢掉 | 已修 |
| minor | PKG-2 | packaging | tasks 目录里一条「合法 JSON 但不是对象」的记录会让 /api/tasks 整个 500，所有模块的任务列表一起不可用 | 已修 |
| minor | PKG-3 | packaging | 安装包与 macOS bundle 的产品名仍是「AI 短剧工作台」，与 branding.py 的 Content Cook 不一致 | 已修 |
| minor | PKG-4 | packaging | exe、安装包、开始菜单和浏览器标签都没有应用图标（PyInstaller 默认图标 + /favicon.ico 404） | 已修 |
| minor | PC-07 | podcast | language=en 的播客，节目单 / 逐字稿 / AI 标识全是中文标签 | 已修 |
| minor | PC-08 | podcast | X-StoryStudio-Lang: en 下若干播客文案漏中文 | 已修 |
| minor | PC-10 | podcast | 交付包溯源写错音色来源：内联档案被标成「音色库档案」，音色库档案只记档案 id、真正的 TTS 音色名不进包 | 已修 |
| minor | PC-12 | podcast | target_minutes 传浮点 3.7 静默截成 3，传字符串 "3.7" 却 400「要是整数」 | 已修 |
| minor | POST-5 | post | 配乐文件不是音频时，报错正文是一屏滤镜图，真正原因被截掉；上传配乐只看扩展名不验内容 | 已修 |
| minor | POST-6 | post | 清单静默漏项：after 指向不存在项的插卡被丢掉却仍列在 ui_inserts；start ≥ 片尾的配乐既不在 music 也不在 music_missing；角标之间不互相避让 | 已修 |
| minor | POST-9 | post | PUT post-layers 的 subtitle 是整块替换：漏掉 language 会被当成 bilingual，报错却说 "shot S3.lines[0].en must be a non-empty string"；插卡预览的错误是英文 | 已修 |
| minor | SC-4 | script | 英文界面里目标形态名仍是中文：/api/script/options 的 targets[].name 与列表的 target_name 不翻译，前端字典也没这几个键 | 已修 |
| minor | SC-6 | script | 契约写「生成 / 另存 / 导入后 current 不动、刚生成完第一版时 current 必然为空」，代码却把第一版自动采用 | 已修 |

## 不成立

- podcast/PC-11「界面上没有任何入口能建英文播客」：新建页确实把 `language` 写死成 `zh`、没有语言下拉，但项目页的「高级」可以整份改 JSON（含 `language`），接口也收英文播客，所以「没有任何入口」不成立。

## 各维度覆盖范围

每一路都在临时数据根 + 空模型设置下真跑（没配任何付费模型），复现用例先单独写好、修好后并入正式测试。下面只记覆盖面、没覆盖到的地方，以及没单列成发现的观察。

### packaging

- 跑过：打包版（`dist/StoryStudio`）在独立数据根和空设置下起服务，与源码版逐接口比对——基础接口、静态资源、每个模块的示例创建与详情、随包资源（skills 数据文件与 examples 逐文件比对）；免费能力真跑（播客逐句 edge-tts 合成 + ffmpeg 混音 + 交付包，绘本朗读 + 装配，短剧交付包，绘本的 xlsx 导出）；角色未配置时各付费任务的失败文案；ASR 的四个入口；英文界面。
- 仓库里没有字体文件，字幕 / 海报 / 插卡都走系统字体路径表，不需要进包。
- 观察：`webui/tasks.py` 不打日志——打包版唯一能看到服务端报错的日志文件里跑了十几个失败任务后只有启动那一行，失败原因只在任务记录里。不算 bug（任务记录带 traceback），但排障时容易误以为日志坏了。

### adapters

- 跑过：设置读写与打码（回传打码值 / 空串不回写）、未知来源 / 角色 400、换来源后的密钥去留、「测试连接」（假密钥、不存在的域名、拒连）、`settings.json` 写坏 / 带 BOM、本机扫描的拒绝路径（公网地址、`127.0.0.1.evil.com`、未知 id、只给地址不给 id）、本地提示的关闭与重置、音色目录、统一网关的保存 / 测试 / 应用；外加一组按「期望行为」写的探针（环境变量落盘、BOM、打码长度、ping 不联网、方舟错误分类、429 分类、humanize 分类、ping 遇到空输出、count 透传、参考图上限跨来源、`image_size`、手填地址应用、换来源带旧密钥）。
- 静态审阅：settings / router / capabilities / local / voices / model_catalog / providers / store、各适配器的错误处理段、相关路由与设置页的扫描 / 应用 / 表单回传。
- 没覆盖：任何要密钥或登录的真实调用（OpenRouter、方舟、MiniMax 的真实响应形状与错误文案——方舟的 InvalidParameter 措辞是按 SDK 结构与文档构造的，没真实触发）；seed / aigc_watermark 透传给不支持的模型时的真实反应；`resolution_for` 降档在真实提交里的表现；Seedance 2.5 的 480p（能力表按文档列了、spec 白名单没列，没当 bug 报）；comfyui / sdwebui 的完整提交流程（有各自的测试）；模型目录的真实拉取；edge 目录离线时的 502 路径；音色的克隆 / 试听 / 对比接口；store 的并发迁移。
- 观察：ADP-04 与 ADP-06 都源于 `update_settings`「读 → 改 → 整份写回」，一处改法（读文件层、显式清除语义）能同时解决；ADP-01 最该先修——用户配错来源时「测试连接」给了假的绿灯。另外注意到、没列为问题的：测试连接的错误文案带 Python 异常类名前缀；`gateway/apply` 传入 music 角色会被静默忽略；humanize 的审核关键词含裸 "policy"，OpenRouter 的 "No endpoints found matching your data policy" 会被判成审核拦截且不可重提。

### frontend

- 跑过 / 核过：vitest 全过、构建成功（与已提交的产物只差 scopeId 哈希）；逐个读完 `frontend/src` 的 js 与 vue；请求体与后端 pydantic 模型逐个核对字段名，没有不一致；「服务端未知字段被丢掉再 PUT」——后期层、播客、剧本工作台、单条视频 / 配乐 / 图片集 / 绘本都保留未知键；`$t` 字面量与英文字典对账，只差 FE-04 三条动态标签；英文界面下的动态文案（露中文的见 FE-05）；`api.js` 的错误处理。
- 没覆盖：真实浏览器 / 桌面壳里的交互（拖拽排序、文件上传、音视频播放、localStorage 只按代码审读）；edge-tts / ffmpeg / faster-whisper 的真实路径。
- 观察：FE-02 从前端主流程直接可达，而且会让概览失败卡为空、估算弹窗永远显示「生成中，跳过」；FE-01 的契约表单只在 spec 里已有 `episode_contract` 时才会被冲；设置页的密钥回传——后端对含 `*` 或空串的 `api_key` 保留原值，副作用是界面上清不掉已存的密钥（按后端注释是有意为之）。

### podcast

- 跑过：示例 / 新建 / 列表 / 编号 / 形式；PUT 校验矩阵约 47 条（环节种类、cue、台词 kind、说话人、听众问题、时长、音色、主播、四种形式的人数约束、music 路径穿越 / 绝对路径 / 非音频、增益与停顿越界、language、id / module 对不上、各块形状），并在英文界面下抽样复核；lint → 问题卡中英；真人感审稿（无模型、空稿、伪造审稿记录后的过时判定）；起草 / 重写的无模型路径；音色库档案绑定与同底音色告警；逐句 edge-tts 真合成与缓存键（改台词 / 改音色 / 换说话人 / 删中间一句）；ffmpeg 真混音（三槽音乐、loudnorm 实测 -16.4 LUFS / -1.9 dBTP、章节 / 逐字稿 / 节目单）；上传的错误路径；配乐投放到三个槽；过时标记；并发合成；导出与解包核对；删除 / 归档；产物流的路径穿越。
- 没覆盖：真实文本模型的起草 / 重写 / 审稿；settings / minimax 引擎的真实合成；interview / roundtable 形式的合成与混音；30MB 上传上限；ffmpeg 缺失的路径；浏览器里的实际交互。
- 观察：前端兜底的 FORMATS 里 interview 人数是 [2,4]、后端是 (2,3)，只在 `/formats` 拉不到时生效；「全期：预计 X 分钟」的告警落到「其他」分类（timing 词表没有「分钟」）；`tests/test_podcast_router.py` 用返回空串的 `scriptgen_backend` 打桩，正好掩盖了 PC-01；合成中导出可能把写到一半的 mp3 打进包，与 PC-02 同源；Windows 上 urllib 用 `localhost` 会先试 `::1`，每个请求慢 2 秒，改用 `127.0.0.1` 即正常，不是服务端问题。

### family（图片集 / 绘本 / 单条视频 / 配乐 / 音色库 / 风格卡）

- 跑过：sticker / portrait / image 的列表、模板、示例、新建、PUT 校验矩阵 30+ 项、参考图上传（格式、穿越、第 7 张、超大 413）、产物流穿越、pick、后处理（同步与后台）、导出与解包、无模型出图、删除归档；picturebook 的预设、校验矩阵、lint、四种素材上传、产物流、xlsx 导出、3 页示例书 edge-tts 真朗读 + ffmpeg 真装配（9:16 与 16:9）、busy 409；clip 的校验矩阵、lint、首帧三种模式、出片的各条错误路径；music 的校验矩阵、参考图、生成的错误路径、投放到绘本与各种坏目标；voices 的增删改、目录、试听、参考音频、无密钥克隆；风格卡过滤；recent；tasks；英文界面抽查。
- 没覆盖：真实付费生成；配乐投放到短剧 / 播客的成功路径；音色克隆成功路径；绘本起草成功路径；服务启动时的 `resume_clip_watchers`；绘本 / 配乐 / 音色的超大上传；浏览器交互；并发写同一项目文件；打包版。
- 观察：绘本的 `pages/generate` 与 `narration/generate` 必须带 JSON body（不带是 422），别的模块不带也行，前端总是发 `{}` 所以无影响；clip 首帧 `generate` 模式先把 `first_frame.mode` 写盘再起任务，起任务 409 时文件已经改了；imageset / clip / music 归档只搬 spec 和 output，`content/assets/<id>` 留在原地；PUT 会丢掉项目文件里不认识的顶层字段（normalize 设计如此）。

### compat

- 跑过：9 个示例文件逐个 normalize → to_disk → normalize → to_disk 往返：7 个模块完全幂等，短剧 spec 原字段一个不改（只加 `source_hash` 与绝对 `output_root`）；核对 webui 里所有 `atomic_write_json` 调用点都写回原始 dict、从不写归一化结果，所以镜头的 `source_hash` 不会被写盘漂移；用示例接口在各模块建项目并跑全部详情 / lint / summary / 审稿 / 导出等接口，中英各一遍，没有 5xx；老文件缺新字段时详情正常；付费 / 本地 POST 在空设置下的错误路径（起后台任务的全部以「X 模型未配置」失败，没有挂住）；ast 扫全部 844 条中文 raise 文案与英文规则对账、响应里的 CJK 在线扫描、前端 key 对账；往数据根写各种坏文件（半截 JSON、`[]`、字符串、`null`、BOM、非对象）探 tasks / jobs / recent / 各模块列表与详情。
- 观察：storyvideo 的结构校验（SpecError）文案全是英文且带本机绝对路径，跟别的模块不一致（随 COMPAT-09 处理）；prepare 对重建 id 失败后 output 下仍会建出空目录；静态扫描的 98 条里有 28 条只在本地模型与个别适配器的路径出现，可以按优先级先补会经 400 detail 到界面的那 70 条；tasks / jobs 记录损坏的现实概率低（写盘是原子的，非对象 JSON 只会来自手改），但一旦出现，任务中心和新建项目页整个打不开、修法又便宜，所以按 major 报。

### drama

- 跑过：建项目（示例、存草稿含路径穿越与 id 不一致）；草稿编辑矩阵（镜头增删改与排序、globals、cast、锚图上传、变体引用、锁、lint 与问题卡）；post-layers 校验矩阵（静音、角标、插卡 card 版式、旁白逐句声线、字幕、遮挡区、配音、配乐、画面文字）；生命周期（prepare、锁定后各编辑接口的 409 矩阵、锁定后 amend、revise、review 的 gate × decision × disposition × defect 组合、import-legacy、retry-failed、generate-resume、没配模型时的 generate、归档与同 id 重建、外部改文件 / 清空输出目录后的账本不一致、并发）；本地链路真跑（ffmpeg 造假产物 → import-legacy → 首帧 / 视频审片 → 文件缺失再恢复 → 装配 → final 审片 → 导出与下载 → 后期合成 → asr-qc）；付费入口在没配模型时的错误路径。
- 没覆盖：配置真实模型后的能力检查与提交 / 轮询 / 下载 / 失败分类；reconcile / abandon；尾帧续接的生成流；asr-qc 自动记重摇；导出包逐项核对；海报；系列交接；音色绑定成功路径；前端页面；英文界面。
- 严重度说明：DRAMA-01 定为 blocker——「忘了配模型就点生成」是首发版本最常见的操作，结果是项目被锁死、界面无解；DRAMA-02 / 03 影响主流程但有绕法（换 id / 再点一次生成），定为 major；DRAMA-08 是本地任意文件读，局域网或关掉令牌时才有实际风险；DRAMA-06 里「`lines.at='abc'` 合成崩」是代码推断（同类的 `dur='abc'` 已真跑崩溃）。

### script

- 跑过：列表 / 编号 / options / targets；示例创建（含重复 id、不存在、路径穿越）；四种 target 新建与各种 400 / 409；PUT 校验矩阵（角色、段、版本、台词、used_in、形状、从骨架删段）；手写另存、pick、删版本；详情字段；伪造审稿记录后的计数、过时与问题卡；四种 target 的 lint（含英文稿口径、单人播客）；起草 / 审稿的无模型路径；投放到播客环节 / 剧本场次 / 镜的成功与错误路径；从播客与短剧导入（含角色撞名、撞 id、超人数）；英文界面的错误文案。
- 观察：SC-1 的根因在 `scriptgen_backend` 返回哨兵字符串 "unconfigured"，播客路由是同一写法，一起修；借来的播客判据对英文稿的「回答太短」按字符数判（"Depends." 只有 8 个字符），是 podcast.lint 的口径问题，不是工作台新造的，没单列。

### post

- 跑过（真跑 ffmpeg 6.0 + edge-tts 7.2.8）：用 testsrc + 正弦波造 4 镜混合尺寸素材，直接调 `compose_post`——真 edge-tts 旁白（逐句声线、旁白字幕让路）、配音、4 段静音（silencedetect 与清单误差 ≤1 ms）、7 条角标的各种边界、3 张插卡（片头 card、连续 card、plain）、4 条配乐（含缺文件、起点超片尾）、本子字、字幕样式与遮挡预设，抽帧核对位置；边界组（720×1280、crossfade 0、长文本出画、横屏插卡、数值写坏、合成失败后上一版成片的状态、特殊字符）；音量实测（旁白压原声 -5.1 dB ≈ 0.55、配乐 duck -10.1 dB ≈ 0.3、配音 duck -18.5 dB ≈ 0.1）；60 镜 1080p 与 10 / 20 / 30 镜的耗时和峰值内存、限内存下的失败形态；尾帧抽取；直接调 `assemble_episode`；接口层 PUT 16 种非法载荷 400、9 种应拦没拦、POST /post 真跑与重复点击。
- 没覆盖：asr-qc；已冻结项目的 PUT → amend（有既有单测）；runner 驱动的装配。
- 观察：装配版把比计划长的镜截到计划时长、后期版按实际时长铺，两份成片长度可能差 0.5 秒（`validate_silence` 按计划时长校验、`silence_windows` 按实际时长截，是同一根源的口径差）；旁白落在配音镜上时两个声音叠着，没有提示；配乐 duck 按音频帧硬切、没有斜坡（旁白压原声有斜坡）；60 镜 1080p 在内存够的机器上约 60 秒，内存不够开始换页才会撞 900 秒超时——而超时又会触发 POST-2 的残片。
