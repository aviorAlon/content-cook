# StoryVideo 运行手册（短剧命令行）

`src/storyvideo/` 是短剧模块的生产控制面：剧集 JSON（spec）声明镜头，runner 负责首帧、视频、装配、状态持久化和三道审片闸门。界面（`/drama`）和命令行 `storyvideo` 走的是同一套运行时；这份手册讲命令行，以及两边共用的规则——冻结、审片、防重复扣费、崩溃恢复。

## 1. 使用前提

- Python 3.10+，在仓库根目录执行过 `pip install -e .`（要桌面窗口再加 `[desktop]`）；`ffmpeg` 和 `ffprobe` 在 `PATH` 中。
- 模型：`run` 按「模型设置」（`~/.storystudio/settings.json`）里的生图 / 视频角色提交；spec 里写 `"@image"` / `"@video"` 就用设置里的模型，写具体模型 id 以 spec 为准。先在界面的「模型设置」里配好并「测试连接」。
- 只有 `run` 会提交付费生成任务。`reconcile` / `abandon` 会查询远端任务历史；`post` 合成旁白时调用语音来源（Edge TTS 要联网）；`asr-qc` 第一次运行会下载 faster-whisper 模型，之后离线。其余命令（`preview`、`prepare`、`import-legacy`、`status`、`review`、`amend`、`retry-failed`、`retry`）都是本地操作。
- 数据目录：默认是环境变量 `STORYSTUDIO_PROJECT_ROOT`，没设时是代码根目录（从源码装就是仓库根）；每个子命令都可以用 `--project-root <数据目录>` 指定，也都认 `--json` 输出机器可读的状态。
- 装好后直接用 `storyvideo`；也可以设 `PYTHONPATH=src` 后用 `python -m storyvideo`。

退出码：`0` 成功；`2` 输入或运行错误（spec 不合法、来源报错等）；`3` 被安全闸挡住（比如提交结果不明）；`4` 停在一道还没通过的审片闸门前。

## 2. 剧集 spec

裸剧集号 `DEMO-1` 解析为 `<数据目录>/content/story_video/DEMO-1.json`；也可以直接给文件路径。在界面里「从示例开始」复制出来的就是这样一份文件（示例原件在 `examples/episodes/first_episode.json`）。最小结构：

```json
{
  "id": "DEMO-1",
  "output_root": "output/story_video/DEMO-1",
  "prompt_language": "zh",
  "cast": {
    "lin": {
      "name": "林小满",
      "block": "角色外观与服装锁定描述",
      "asset": "content/assets/DEMO-1/lin.png"
    },
    "office": {
      "block": "场景锚：地点、光线、陈设"
    }
  },
  "image_params": {"model": "@image", "aspect_ratio": "9:16", "image_size": "2K"},
  "video_params": {"model": "@video", "resolution": "1080p", "aspect_ratio": "9:16", "generate_audio": true},
  "subtitle": {"language": "zh"},
  "shots": [
    {
      "id": "S1",
      "dur": 6,
      "cast": ["office", "lin"],
      "shot": "景别与机位",
      "art": "首帧画面描述",
      "motion": "唯一动作、台词、声音和时序",
      "lines": [{"who": "lin", "zh": "中文台词。"}]
    },
    {
      "id": "S2",
      "dur": 6,
      "cast": ["office", "lin"],
      "ref_frame": "S1",
      "shot": "同轴中景",
      "art": "下一镜首帧",
      "motion": "下一镜动作",
      "lines": []
    }
  ]
}
```

约束：

- `output_root` 必须在数据目录内，并且最后一级目录名与 `id` 完全相同。
- 每镜要有唯一的 `id`、整数秒 `dur`、合法的 `cast`、非空的 `shot` / `art` / `motion`。台词的 `who` 必须是人物表里的 id 或名字（旁白除外）。字幕语言由 `subtitle.language` 决定：`bilingual`（缺省）要求每句同时有 `zh` 和 `en`，`zh` / `en` 只要求对应那一种。
- 部分视频模型有时长范围（例如 Seedance 2.0 / MiniMax-H3 是 4—15 秒），越界在付费前就被拒。
- 装配依赖视频的原生音轨：`video_params.generate_audio` 只能省略或设为 `true`，无声模式在付费前就被拒。
- `ref_frame` 只能引用同一集里的镜头，不能成环。runner 按依赖波次生成；用 `--only` 生成首帧时会自动带上它的上游参考镜。
- 角色锚图 `asset` 只认数据目录 `content/` 下的文件（界面上传的就在 `content/assets/<项目>/`）；`prop_ref` 指向 `output_root` 内的本地道具图。真正提交前先做本地媒体校验，并按内容哈希复用上传结果。

`prepare` 第一次运行会把原始 JSON 冻结到 `manifests/episode.snapshot.json`。之后对这份 JSON 的任何原地修改，下一次 `prepare`、`run`、`review` 或 `status` 都会报冲突并停下：只改了后期块就先 `amend`（见 §5.1）接受修订；改到会进提示词或参数的字段，要复制成新的 revision（见 §5）。

先看一眼编译结果再冻结：`storyvideo preview DEMO-1` 把每镜编译后的首帧 / 视频提示词和时长预算写进 `<output_root>/manifests/preview.md`（`--stdout` 直接打印，`--only S1,S2` 只看几镜），不锁定、不花钱，改了 spec 随时重跑。

### 2.1 可选字段（全部 opt-in，不写的老 spec 编译结果零变化）

| 字段 | 位置 | 作用 |
|---|---|---|
| `entry_state` / `exit_state` | shot | 入口 / 出口状态句，拼进视频提示词头尾（入口也进首帧）；`storyvideo.continuity` 查接缝 |
| `beats` | shot | `[{t:"0-2", picture, performance, sound}]` 时码段落，motion 保留作总述；查时间前进与不超 dur |
| `performance_lock` | spec | `true` 时每镜视频提示词带一句表演说明（情绪交给看得见的小动作，整镜情绪至多转一次）和一句视线说明（人物不看镜头）；自拍镜或 `gaze: "camera"` 的镜不加视线句 |
| `visual_style` | spec | `{medium, palette:[{hex, role}], edge, finish}` 四要素拼进首帧提示词；模板见 `style_library.json` 的 `visual_style_template` |
| `gaze` | shot | `"camera"` = 这镜看镜头是对的 |

更多可选字段（结构化起止边界、轴线、连续性锁、角色变体、参考图用途、尾帧续接、揭示保护、画面文字、形态卡等）各自的规则见 [DESIGN.md](DESIGN.md) 对应章节。改动编译器前后跑 `python scripts/refresh_prompt_canary.py`：黄金提示词夹具（`tests/canary/`）一漂就报，确认是有意改动再 `--update`。

### 2.2 提示词语言

`prompt_language` 取 `zh` 或 `en`。新建项目与示例默认中文，这时下面这些字段都用中文写：`frame_style`、`scene_anchor`、`camera`、`neg`、`video_lock`、`video_tail_lock`（顶层）；`cast[].anchor_prompt`、`cast[].block`；每镜的 `shot`、`art`、`motion`、`neg_extra`。**顶层一定要显式写 `"prompt_language": "zh"`**，否则镜头库（`camera_library.json`）编译 `camera_move` / `camera_combo` 时会退回英文短语，跟其余全中文的提示词对不上。

已有的英文提示词项目不需要、也不要回头翻译：已经冻结或交付的东西改语言没有收益，只有把原本过了审片的表达改跑偏的风险。

中文默认的理由：中文单起一行写音效，比埋在英文散文里更容易生成独立可辨的音效；中文提示词也更方便在界面里人工核对和改。身份锁、材质 / 光线描述、负向词这类大段自由文本，没有专门测过中文整段写的服从度——第一次全中文起稿时，首帧审片要比平时更细看角色一致性和禁止项。

## 3. 标准生产流程

```powershell
# 只校验、建计划并冻结 spec；不调用任何生成接口
storyvideo prepare DEMO-1

# 可选：只生成钩子镜首帧，先人工看方向
storyvideo run DEMO-1 --only S1 --through frames
storyvideo status DEMO-1

# 生成其余首帧；全部首帧完成后才会建立 frames 审片闸门
storyvideo run DEMO-1 --through frames
storyvideo review DEMO-1 frames --decision pass --reviewer your-name

# 生成视频；只有 frames 闸门通过后才会提交
storyvideo run DEMO-1 --through videos
storyvideo asr-qc DEMO-1 --record-retakes          # 可选：台词识别闸（需要 faster-whisper）
storyvideo review DEMO-1 videos --decision pass --reviewer your-name

# 本地装配成片并等待 final 审片
storyvideo run DEMO-1 --through assemble
storyvideo review DEMO-1 final --decision pass --reviewer your-name

# 后期层：旁白、整屏插卡、画面文字、角标、静音、配乐、混音
storyvideo post DEMO-1

# 只读查看最终状态
storyvideo status DEMO-1 --json
```

`run` 默认等远端任务结束。加 `--no-wait` 时只推进当前状态就返回，之后重复同一条 `run` 即可继续轮询和下载，不会因为「再运行一次」而重新提交已有的远端任务。

`--through auto` 是默认值：推进到当前第一道还没通过的审片闸门为止。`--through frames|videos|assemble|final` 声明本次希望到达的阶段；上游闸门没通过时以「闸门未通过」退出，不会越级提交。

`--only S1,S2` 用于局部推进。只有一整个阶段的全部镜头都通过技术校验后，才会生成该阶段的审片清单；所以 `--only S1` 适合低成本验证钩子，但这时直接看 `01_frames/S1.png`，它不会单独开启可 `review` 的 frames 闸门。

`asr-qc` 用本机 faster-whisper 把视频转写后逐句对台词：字错率 ≤ `--threshold`（默认 0.3）算过，稍高的待复核，其余失败；`--record-retakes` 给失败的镜记一条 videos 闸门的重摇，`--include-stray` 把无台词镜里多出来的人声也算失败，`--model` 选模型大小（默认 `small`）。

## 4. 三道审片闸门

| 闸门 | 自动技术检查 | 人工重点 | 证据文件 |
|---|---|---|---|
| `frames` | 图片可解码、文件有效、参考链完整 | 角色、道具、构图、左右锚、连续性 | `x_frames.png` |
| `videos` | 视频可解码、时长容差、音轨等 | 台词、口型、动作、镜头连续性 | `x_video_qc.png` |
| `final` | delivery 声明的尺寸、音轨、成片可解码 | 字幕、节奏、转场、响度、完整性 | `x_final_qc.jpg` |

审片记录保存在 `manifests/qc.json`，支持五种裁决：

- `pass`：通过；
- `waive`：带理由豁免，也计为通过；
- `retake`：带理由重摇指定首帧或视频；
- `edit_rescue`：记录剪辑救援请求，当前不会自动执行剪辑分支；
- `redesign`：记录改设计请求，当前不会自动创建新 revision。

除 `pass` 外都必须写 `--reason`；`--evidence` 可重复传入证据路径。每个镜头都是 `pass` 或 `waive`，整道闸门才会变成 `accepted`。素材字节变了，旧裁决自动失效，必须重新审。

```powershell
storyvideo review DEMO-1 frames --decision pass --shots S1,S2 --reviewer director
storyvideo review DEMO-1 videos --decision edit_rescue --shots S4 --reason "尾部停顿可在剪辑中裁掉"
storyvideo review DEMO-1 final --decision waive --reason "已确认保留环境底噪" --evidence output/story_video/DEMO-1/review-note.txt
```

## 5. 重摇与 revision

重摇通过 `review --decision retake` 发起，没有单独的 `retake` 子命令：

```powershell
storyvideo review DEMO-1 videos --decision retake --shots S4 --reason "人物口型与台词不一致"
storyvideo run DEMO-1 --only S4 --through videos
storyvideo review DEMO-1 videos --decision pass --shots S4 --reviewer director
```

旧素材先归档到 `01_frames/takes/<镜>/` 或 `02_video/takes/<镜>/`，随后该 job 才被明确重置。runner 不会自己重摇失败或质量不佳的素材。

首帧重摇会自动带上所有传递依赖它的 `ref_frame` 镜头，并级联关闭对应的 videos 与 final 闸门；已经生成的受影响视频和成片先归档 / 失效。视频重摇会关闭 final 闸门。整个操作先持久化重摇意图，中途崩溃后由下一次 `run` 幂等完成，不会留下「旧视频仍通过」的闸门。

同一份请求原样重摇有上限：同一镜同一份提示词已经出过 2 次，第 3 次原样重摇会被拒（命令行以退出码 3 停下；界面上确认后可以强制），该改文字、换模型或接受当前结果（DESIGN §24）。

要改 cast、提示词、时长、字幕结构或其他会进请求的 spec 内容时，不要改已冻结的剧集：复制成新的剧集 ID（例如 `DEMO-1_v2`），同步修改 `id` 与 `output_root`，再 `prepare`。可复用的产物用 `import-legacy` 认领（§8）；界面里的「制作中也能改」就是自动做这一串。

### 5.1 三条不用开 revision 的路

| 情况 | 命令 | 说明 |
|---|---|---|
| 冻结后只改了后期块（`post_graphics` / `voice_over` / `subtitle` / 标题与备注类字段） | `storyvideo amend <剧集>` | 对比冻结快照与当前源，差异只落在 `runtime.POST_ONLY_KEYS`（外加 `delivery` 里的遮挡区设置）就重写快照、更新 run 哈希，并同步装配任务指纹（已装配的成片作废重装）；碰到任何会进提示词 / 参数的字段则拒绝并点名 |
| 任务因平台瞬时错误失败（超时、服务端处理失败等） | `storyvideo retry-failed <剧集> [--only S4]` | 把 `failed` 的首帧 / 视频任务重置为 `prepared`，下一次 `run` 原地重提。失败任务没有产物、不在审片项里，`review --decision retake` 会报 unknown item，这条是唯一的原地路。审核拦截类失败重提也会再失败，要改写提示词 |
| 参考链上游要重摇、下游还是 `prepared` | 直接 `review --decision retake` | `prepared` 任务没有付费的远端任务可被孤儿化，预检放行 |

后期层走 `storyvideo post <剧集>`：从 `02_video/` 的镜次直接合成到 `<output_root>/<id>_post.mp4`（`--destination` 可改），不经过 `assemble`；时间线、旁白、插卡、字幕让路等清单写在 `manifests/post.json`。成品先写临时文件、成功才原子替换。

## 6. 崩溃恢复与避免重复扣费

权威状态保存在 `<数据目录>/output/story_video/storyvideo.sqlite3`。它用 SQLite WAL 和同步写入；每集的 `manifests/run_state.json` 是便于查看的原子导出，不是另一套状态源。账本表结构有版本号（`PRAGMA user_version`，当前 2：jobs 表记每次提交真实用的来源与模型）；老账本第一次可写打开（`prepare` / `run` / `review` / `retry-failed`、网页服务启动等）时自动升级；只读打开（`status`、网页详情页与导出）不迁移、照旧报版本不一致，所以网页服务启动时会先可写打开一次；新版程序写的账本旧版打不开。

一次付费提交按以下顺序落盘：

1. 先把 job 写成 `submitting` 并增加 attempt；
2. 再调用远端生成；
3. 收到 task ID 后立即写入数据库；
4. 之后只轮询这个 task ID；下载到隐藏的候选文件，技术校验通过后才原子替换正式素材。

所以已经记下 task ID 的任务在进程中断后会继续轮询，不会再次提交；无效下载也不会覆盖上一次可用的素材。402 / 额度不足会停止当前批次并记录失败，不会继续提交后续镜头，也不会静默重试。配置类错误（角色没配、缺密钥、缺模型）在写账本之前就被拦下、记成明确失败，可以原地重提。

最危险的窗口是：远端可能已经收到请求，但本地还没拿到 task ID。这时 job 保持「提交结果未知」（运行状态 `blocked_submission_unknown`），runner 拒绝自动重提。每次付费任务的名字带 attempt 与请求指纹；来源支持查询任务历史时，下一次 `run` 会按任务名查远端历史，只有一个精确匹配才接管，零个或多个匹配都继续阻塞。

确认了远端任务可以手动关联；确认请求从没到达远端，可以显式放弃这次 intent（判断错了会产生重复任务，所以要确认开关，命令还会再查一次历史）：

```powershell
storyvideo status DEMO-1 --json
storyvideo reconcile DEMO-1 frame-S3 --task-id <远端任务 id>
storyvideo abandon DEMO-1 frame-S3 --reason "远端确认无此 attempt" --confirm-no-remote-task
storyvideo run DEMO-1
```

合法的 step ID 是 `frame-<镜>`、`video-<镜>` 和 `assemble`。`reconcile` 会再次核对任务 id、任务类型，以及来源自己的身份规则（有的话），核对不上不写状态。

**注意**：自动接管、`reconcile`、`abandon` 都要求来源实现任务历史查询（`genkit.providers.TaskHistoryProvider.list_tasks`）。当前内置来源经路由后都不提供：自动接管不会发生，`reconcile` / `abandon` 会直接报 `the generation provider cannot query task history`，这几条路都走不通。

用内置来源时遇到「提交结果未知」，出路是开新的 revision：先到来源的控制台核对这次请求有没有真的提交（提交过再重提会重复扣费）；确认后按 §5 复制成新的剧集 ID（改 `id` 与 `output_root`），把已有的 `01_frames/<镜>.png`、`02_video/<镜>.mp4` 拷到新的 `output_root` 下，`import-legacy` 认领（§8），卡住的那一镜在新 revision 里是一个新任务。

确定失败的任务不会自动重提。查清并修复原因后，显式开启新 attempt：

```powershell
storyvideo retry DEMO-1 --steps frame-S3 --reason "余额已补充，确认重新提交"
storyvideo run DEMO-1
```

## 7. 产物布局

```text
<数据目录>/output/story_video/
├── storyvideo.sqlite3              # 全部剧集的权威运行账本
└── DEMO-1/
    ├── 01_frames/S1.png
    ├── 01_frames/takes/S1/...
    ├── 01_frames/_upload/          # 大于上传上限的首帧 / 参考镜的上传代理图（持久）
    ├── 02_video/S1.mp4
    ├── 02_video/takes/S1/...
    ├── manifests/
    │   ├── episode.snapshot.json   # 冻结 spec
    │   ├── run_state.json          # 状态导出
    │   ├── qc.json                 # 三道审片记录
    │   ├── assets.json             # 本地素材上传缓存
    │   ├── post.json               # 后期合成清单
    │   └── installs/               # 落盘后、账本提交前的崩溃恢复标记
    ├── x_frames.png
    ├── x_video_qc.png
    ├── x_final_qc.jpg
    ├── DEMO-1_final.mp4            # 装配版
    └── DEMO-1_post.mp4             # 后期合成版
```

装配默认输出 1080×1920、24 fps、48 kHz 音频（`delivery` 可改尺寸与帧率），对拼好的原生音轨做 -16 LUFS 响度归一化；不默认降噪、EQ 或强压缩。默认视频码率上限 12 Mbps，优先保住动作细节和特效边缘。装配和最终媒体校验失败时，旧的正式成片不会被半成品覆盖。

## 8. 已有产物与交付边界

已经有产物、但账本里没有记录的剧集（手工放进去的首帧 / 视频、从别处拷来的项目），`prepare` / `run` 会先拒绝，免得把已有素材误判成「未生成」而重复扣费。需要接着做时显式导入；这条命令只做本地校验、建立 attempt=0 的记录和待人工复核的三道审片，不访问任何生成接口：

```powershell
storyvideo import-legacy DEMO-1 --confirm-existing-artifacts
storyvideo review DEMO-1 frames --decision pass --reviewer director
storyvideo review DEMO-1 videos --decision pass --reviewer director
storyvideo review DEMO-1 final --decision pass --reviewer director
```

要改 spec 本身，仍然开新的 revision ID 和输出目录。新剧集不要先手工放素材，先 `prepare` 建账。

final 审片的 `pass` 只把本地状态设为 `ready_to_deliver`，不会自动发消息或上传文件，也不会把状态改成 `delivered`。要交给别人，用成片页的「导出交付包」：白名单打包 spec 快照、每镜提示词、首帧、视频、成片、旁白、审片裁决、manifest 与 sha256（DESIGN §21），设置、密钥、锚图输入和账本永不进包。
