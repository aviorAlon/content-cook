# 剧本工作台 `script`（✍️，路由 `/script`，2026-09-20）

需求：播客和短剧眼下最弱的都是台词与剧本，要一个单独的页面来调试、优化、修改剧本，调好之后给播客或短剧引用。

## 1. 为什么单独一个模块

台词现在只能在各自模块里原地改：短剧要进项目 → 剧本页 / 镜头页，播客要进那期 → 脚本页。想「同一段换三种写法再挑一版」没有地方放，改坏了也回不去（改了就是改了，只能靠自己记得原来怎么写的）。工作台把**写 → 试 → 比 → 挑 → 用**独立出来：

- **版本留痕**：每次生成、每次重写、每次手改另存都是一版，从不覆盖；随时回到上一版。
- **检查随时跑**：静态检查不花钱，按目标形态选判据；模型审稿复用已有的两张清单（短剧工艺 / 播客真人感），引文照样由程序逐字核对。
- **两版并排比**：字数、秒数、告警数、审稿结论摆在一起，挑一版当「采用」。
- **挑好了再投放**：把采用的那一版灌进某一期播客的某个环节、某集短剧的某一场或某一镜，角色对角色地映射。

共用：模型设置（文本模型）、任务中心、问题卡、导出（不适用）。**这个模块不出图、不出声、不装配**，只管文字。

## 2. 项目文件 `content/script/<ID>.json`

```json
{
  "id": "SC-20260920-1",
  "module": "script",
  "title": "下班后的消息，要不要回",
  "target": "podcast",                 // podcast / drama_screenplay / drama_shot / generic：决定用哪套写法与检查
  "language": "zh",                    // zh / en
  "brief": {
    "topic": "下班后收到工作消息，回还是不回",
    "angle": "不站队，两个处境不同的人各讲各的",
    "audience": "刚工作两三年的人",
    "materials": "阿杰上个月周六晚上十点被拉进新群……（只能用这里的事实和数字，不许编）",
    "tone": "像朋友下班后吐槽",
    "avoid": ["说教", "编造调查数据"]
  },
  "speakers": [                        // 工作台自己的角色表（1–6 人），投放时映射到目标项目的角色 / 主播
    {"id": "jie", "name": "阿杰", "persona": "大厂产品经理，说话慢", "stance": "边界感是必须的"},
    {"id": "lin", "name": "小林", "persona": "三人小公司合伙人，语速快", "stance": "客户在等就得回"}
  ],
  "blocks": [                          // 段：对应播客的一个环节 / 短剧的一场或一镜；顺序就是播放顺序
    {"id": "B01", "kind": "talk",      // talk / qa：只有标成 qa（听友问答）的段才判回答那三条（§4）
     "title": "冷开场", "goal": "用一个具体场景把人拽进来", "note": "给模型的额外提示（可空）"}
  ],
  "versions": [                        // 从不覆盖；按时间从旧到新
    {
      "id": "V1", "label": "第一版", "source": "model",      // model / hand / rewrite / import
      "model": "gpt-4o-mini", "backend": "settings", "note": "给模型的一句要求", "created_at": 1789,
      "from_version": "",                                    // 基于哪一版改的（hand / rewrite / 重新生成时填）
      "blocks": [{"id": "B01", "lines": [{"who": "jie", "text": "……", "cue": "", "kind": "talk"}]}]
    }
  ],
  "current": "V1",                     // 采用的那一版（投放、检查、审稿都看它）
  "used_in": [{"module": "podcast", "id": "PC-1", "target": "G01", "at": "2026-09-20T…"}]
}
```

归一化（`src/script/spec.py`，形状照 `podcast.spec`）：`speakers` 最多 6 人、id 与**名字**都不许重复；`blocks` id 缺了按 `B01…` 补、不许重复，`kind` 认 `talk / qa`（留空 = `talk`）；版本 id 缺了按 `V1…` 补、不许重复；台词的 `who` 必须是 speakers 的 id 或名字（名字换成 id，写错就 `SpecError`，跟播客同一原则）；`cue` 只认播客那套（`podcast.spec.CUES`），台词的 `kind` 认 `talk / question / answer`；`current` 必须指向存在的版本（没有版本时为空）。运行时键 `output_root`（`output/script/<ID>`，只放审稿记录）与 `project_root` 写盘时去掉。

两条不在归一化里拦：

- **角色表的下限（1 人）**由 `spec.require_speakers` 在真要用到角色的三处（起草、审稿、投放）拦——刚新建、还没填角色的项目也要能存下来。
- **段种类靠人标**：`kind=qa` 不按台词猜。对谈里的追问也是问句、「嗯」「对」这种短接话也会被标成 `answer`（§3 的提示词还专门要求这么写），猜的结果是把一段正常对谈整段报成「回答太短」。

## 3. 生成与重写（`src/script/craft.py`，1 次文本模型请求）

- `POST /{id}/generate` body `{note?, from_version?, blocks?}`：
  - 不给 `blocks` = 出一整版新的（`source: "model"`）。
  - 给了 `blocks`（段 id 列表）= 只重写这几段，其余段从 `from_version`（不给就是 `current`）原样抄过来（`source: "rewrite"`，`from_version` 记上）。
  - 新版本追加在 `versions` 末尾。**项目还没有采用任何一版时，新加的这一版自动采用**（通常就是第一版；`current` 被整体保存清空、版本表里已有旧版时，采用的也是刚加的这一版，不是最老那版），已经有采用的版本时**不自动顶替**（`current` 不动，界面上让人比过再挑）。生成、手写另存（`POST /{id}/versions`）、导入（`POST /{id}/import`）三条路都走 `spec.adopt_if_first`。这是 2026-09-20 有意定的：第一版没有可比的对象，不认它的话「用」页会一直说「还没挑一版」，还得先回「试」页点一次采用；从第二版起才有「比过再挑」这件事。
- 提示词按 `target` 选写法，全部复用已有的经验规则，不另起一套：
  - `podcast`：`podcast.craft.CRAFT_RULES` 八条（口语不是书面语、有人不同意、具体压倒观点、每个人是一个人、问答要像回答、不自我介绍、禁止清单、时长可数）+ 轮流那条能数的要求。
  - `drama_screenplay` / `drama_shot`：短剧台词写法——话里留一层没说出口的、嘴上和身上各演一半、只写镜头拍得到话筒录得到的、单句不超过 25 字（`storyvideo.screenplay.LINE_MAX_CHARS`）、开口的人不超过 3 个、不让人物给知情的人讲他们都知道的事。来源是剧本工艺清单（`docs/script-craft-gap.md`）里的判据与本仓库经验规则。
  - `generic`：两者的交集（口语、有分歧、具体、不用台词交代前情）。
  - 一律带上：角色表（人设 / 立场）、brief（素材里没有的事实数字不许编）、段落骨架（标题 / 目标 / 备注）、语言、`who` 只能用 speakers 的 id。
  - 台词的 `kind` 只在 `kind=qa` 的段里用（念听众问题的那句标 `question`、正面回答它的标 `answer`），其余段一律 `talk`——对谈里的追问和短接话都是 `talk`。
- 回填照 `podcast.craft.apply_draft` 的规矩：名字换成 id、不认识的说话人报错（`ScriptLabError`）、`cue` 不在清单里就清空、模型漏掉的段保留上一版的内容。
- 任务 `sc-draft`「台词起草」，`cost_note` 走 `local_mod.cost_note_for("text", 1, "发 1 次文本模型请求")`；没配文本模型 400，在跑 409。

## 4. 检查（`src/script/lint.py`，不花钱）

按 `target` 选判据，全部复用已有的检查函数，只把工作台的版本喂进去（不复制词表）：

| target | 用哪些 |
|---|---|
| `podcast` | `podcast.lint` 的台词类判据：书面语、独白太长、机械轮流、一人独大、全程没分歧、填充词密度、没有具体例子；**回答没标 / 太短 / 复述问题这三条只判 `kind=qa` 的段**（§2）。结构类（缺冷开场 / 收尾、音色）跳过——工作台没有音色，环节种类也只留「是不是听友问答」这一个区分 |
| `drama_screenplay` / `drama_shot` | `storyvideo.screenplay.check_line_length`（单句 >25 字，**只判中文稿**：这是按汉字定的阈值，英文没有对应口径，照搬会把几乎每句都报成太长）、`check_speaking_cast`（开口的人 >3，不分语言）、对白密度（**只有 `drama_shot` 出告警**：把一段当一镜交给 `storyvideo.preview.shot_duration_budget`，窗口与快慢两端的秒数全用它回的数，同样只判中文稿；`drama_screenplay` 仓库里没有「一场该多长」的判据，就不带阈值，只在详情里报每段的字数与两种秒数，判断留给人） |
| `generic` | 书面语 + 独白太长 + 一人独大 + 没有具体例子 |

告警句形状：`block B01: 影响——改法` / `全篇：影响——改法`。`webui/problems.py` 加 `block` 头（`_BLOCK` 正则 + `_HEADS`），分类落在已有的 `dialogue` / `podcast`。估算 `{chars, lines, blocks, seconds, minutes}` 沿用 `podcast.craft.estimate` 的算法（中文 4.5 字/秒、英文 2.6 词/秒）。

## 5. 审稿（`src/script/review.py`，1 次文本模型请求）

复用已有的两张清单与那套闸门，不新写清单：

- `target=podcast` → `skills/ai-podcast/scripts/humanlike_checklist.json`（口语 / 对谈动态 / 内容）。
- 其余 → `skills/ai-short-film/scripts/craft_checklist.json`（张力 / 情绪可见 / 节奏 / 合规）。
- 引文核对复用 `storyvideo.genre_review.verify_items`：把**段当「场」**传进去，可引用的字段是每一句台词、相邻两句拼成的合句（照 `podcast.review._adjacent_pairs`，同样要过「碎片不算证据」那道补充检查）。
- 审的是 `current` 指向的那一版；**一版也没采用时审最新的那一版**（`spec.pick_version`）——第一版会自动采用（§3），但 `current` 仍可能是空的（例如整体保存 `PUT` 时把它清掉了），这时要是当「没有稿子」，最新的那一版就没法审。记录写 `output/script/<ID>/manifests/review_<版本 id>.json`，带 `spec_hash`（那一版的台词 + 清单 id）。换版本 = 各自一份；那一版的台词改了 = 过时。
- 告警句 `block B01: 按真人感清单审稿（模型判断）：…` / `按工艺清单审稿（模型判断）：…`，跟两个模块现有的句式一致，问题卡分类不变。
- 任务 `sc-review`「台词审稿」。

## 6. 对比

`GET /{id}` 的详情里带每一版的 `{id, label, source, model, note, created_at, estimate, warnings_count, review: {verdicts, stale} | null}`，前端拿两版并排显示；逐行差异在前端算（同一段按行比，相同行淡显、不同行高亮），后端不做 diff。

## 7. 投放与导入

`POST /{id}/use` body `{module, target_id, blocks, mapping, mode?}`：

- `mode`（三种去处共用）：`"replace"`（默认）把落点里原有的台词整段换掉，`"append"` 接在原有台词后面。目标项目没有版本留痕，替换掉的台词找不回来，所以界面上三种去处都给选，落点下拉按所选方式说明「已有 N 句，会被整段替换 / 会追加在后面」；`/options` 的 `modes` 列出可选值。
- `module=podcast`：`blocks` 是 `{段 id: 环节 id | "new"}`，`mapping` 是 `{speaker id: 主播 id}`；把该段台词写进那期的 `segments[].lines`；`"new"` 就新建一个环节（`kind: "topic"`、标题取段标题）。写回前整份过 `podcast.spec.normalize` 再 `save`，不绕过。
- `module=drama_screenplay`：`blocks` 是 `{段 id: 场次 id | "new"}`，`mapping` 是 `{speaker id: 人物 id}`；写进 `<episode>.screenplay.json` 的 `scenes[].dialogue`（`{who, zh}`），整份过 `storyvideo.screenplay.normalize_screenplay` 再落盘。
- `module=drama_shot`：`blocks` 是 `{段 id: 镜号}`，写进 `shot.lines`（`{who, zh}`）；**剧集已锁定（prepare 过）→ 400**：「这一集已经开始制作，台词是生成字段——去项目里走改写重出，或复制一个新 revision」。
- 短剧的 `cast` 里只有**人物**（`cast_kind` 判定为 character 的条目：有 name / voice，或显式 `kind: "character"`）能当映射目标：`/targets` 的 `drama[].cast` 不列地点 / 道具，映射到它们 400「映射到了目标项目里没有的角色」——不然地点名会进付费出片提示词、配音又找不到它的音色。
- 角色映射缺一个就 400，说清哪个说话人没映射；目标项目 / 环节 / 场次 / 镜号不存在 404。
- 成功后 `used_in` 追一条（同一目标同一落点只留最新一条），返回目标标题与落点，提示「已放进《…》的〈环节 / 场次 / 镜号〉」。

`POST /{id}/import` body `{module, target_id, blocks?}`：反向——从某期播客的环节 / 某集剧本的场次 / 某一镜把现有台词导进来当新的一版（`source: "import"`），同时**只把这次导进来的台词里真开口的人**补进 `speakers`（id 与名字照抄，人设留空；角色表只有 6 个位子，整张人物表塞进来会把正常的导入打回）。跨项目认的是名字：id 与名字都对得上的沿用；名字相同、id 不同的折进已有的那个人（台词的 `who` 换成已有 id）；id 被**名字不同**的人占了则给导进来的角色换一个新 id（`xiao_2`…），返回体 `renamed: {旧 id: 新 id}` 与 `message` 里点名，不静默归到别人名下；开口的人合计超过 6 人报错并点名来源。项目还没采用任何一版时导进来的这一版自动采用（§3）。用于「现有的稿子不好，拿过来调」。

## 8. 接口 `/api/script/...`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/script` | 列表：`[{id, title, target, speakers, blocks, versions, current, minutes, used_in, path}]` |
| GET | `/api/script/next-id` | `{id}`（`SC-YYYYMMDD-n`） |
| GET | `/api/script/targets` | 可投放 / 可导入的项目：`{podcast: [{id,title,segments:[{id,title,lines}]}], drama: [{id,title,locked,scenes:[…],shots:[…]}]}` |
| GET | `/api/script/options` | 目标形态清单、cue 清单、写法约束（给新建页与说明用） |
| POST | `/api/script` | 新建（不填 id 由服务端编号） |
| GET / PUT / DELETE | `/api/script/{id}` | 详情 / 整体保存（校验不过 400「改动没通过校验，没有写盘：…」）/ 归档到 `.webui_deleted/script/` |
| POST | `/api/script/{id}/generate` | `{note?, from_version?, blocks?}` → `{task_id, cost_note}`（任务 `sc-draft`） |
| POST | `/api/script/{id}/review` | `{version?}` → `{task_id, cost_note}`（任务 `sc-review`）；`GET` 同路径拿上次结果 + 过时 + 问题卡 |
| POST | `/api/script/{id}/pick` | `{version}` 采用某一版 |
| POST | `/api/script/{id}/versions` | `{label?, from_version?, blocks}` 手写另存为新版本（`source: "hand"`）；项目还没采用任何一版时这一版自动采用（§3） |
| DELETE | `/api/script/{id}/versions/{version}` | 删掉一版（不能删 `current`，不能删最后一版） |
| POST | `/api/script/{id}/use` | 投放（§7），`{module, target_id, blocks, mapping, mode?}` |
| POST | `/api/script/{id}/import` | 导入（§7）→ `{version, imported, blocks, speakers, renamed, message, detail}`；项目还没采用任何一版时导进来的这一版自动采用（§3） |

详情 `GET /api/script/{id}` 返回 `{id, module, title, spec, current_version, estimate, warnings, problems, versions:[…摘要], review:{…} , targets_hint, busy:{draft, review}, limits}`。

## 9. 前端 `/script`

- **首页** `/script`：列表（标题 / 目标形态 / 段数 / 版本数 / 采用的是第几版 / 已投放几处）+「+ 新建」+「从现有项目导入」（选播客期 / 短剧集 → 建一个工作台项目）。
- **新建** `/script/new`：标题、目标形态（四选一，一句话说明各自的写法与检查）、语言、brief 五项、角色表（名字 / 人设 / 立场）、段落骨架（标题 / 目标，可先留一段；播客稿每段多一个「段种类」下拉：对谈 / 听友问答）。
- **项目页** `/script/p/:id/{write,try,compare,use}`：
  - **写**：当前版本逐段逐句编辑（说话人下拉、台词、cue、种类），加句 / 删句 / 上下移、加段 / 删段（骨架是项目级的，删段会把那一段从**每一版**里抹掉——确认框按全部版本数句，「保存段落骨架」的 PUT 同步从各版里剔掉被删的段）；段的标题 / 目标 / 备注与段种类（播客稿才显示）改了单独「保存段落骨架」；顶部估算与问题卡；「另存为新版本」。
  - **试**：「生成一版」（整篇，写清 1 次文本请求）、「只重写这几段」（勾选段 + 一句要求）、「跑检查」（本地即时）、「审一审」（复用清单，1 次请求）；下面是版本列表（标签 / 来源 / 模型 / 时间 / 字数 / 分钟 / 告警数 / 审稿结论 / 采用 / 删除）。
  - **比**：左右两个版本下拉，逐段逐行并排，不同的行高亮；右上角「用左边这版」/「用右边这版」。
  - **用**：选目标（播客期 / 短剧集）→「怎么写进去」（整段替换 / 追加在后面，三种去处都有）→ 段落落点下拉（环节 / 场次 / 镜号 / 新建，已有台词的落点按所选方式说明后果）→ 角色映射（每个说话人一个下拉，只列人物）→「投放」；下面列已投放去向。
- 侧栏模块块、任务中心种类名（`sc-draft`「台词起草」/ `sc-review`「台词审稿」）、最近记录路由 `/script/p/{id}/write`。

## 10. 来源与置信度

- 写法约束、检查判据、两张审稿清单全部复用已有模块（`podcast.craft` / `podcast.lint` / `storyvideo.screenplay` / 两张清单），**这个模块不新造判据**；它们的出处与置信度见 `docs/podcast-spec.md` §7 与 `docs/script-craft-gap.md`。
- **未验证**：投放到三种目标之后，短剧拆镜 / 播客合成会不会因为台词换了而需要重跑，属于各模块原有规则（改台词 = 音频过时、拆镜要重来），工作台只负责写进去。
