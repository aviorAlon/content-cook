# 配乐模块 `music`（🎵，路由 `/music`，2026-09-10）

按意图生成配乐，产物直接落进短剧的配乐层或绘本的 BGM。共用：模型设置、任务、风格卡（不适用）、导出。

## 1. 来源：任何实现 `submit_music` 的适配器（`music` 角色）

- `src/genkit/providers.py` 加 `TaskType.MUSIC` 与 `MusicRequest`（`name / prompt / model / duration / count / reference_asset_ids / extra_params`），`submit_music` 走 `Provider` 协议；不出音乐的适配器抛 `ProviderUnsupported`（挂在 `Adapter` 基类上兜底，生图 / 视频适配器不用各写一遍）。
- 设置里加第五个角色 `music`（`ROLES` 原来是 text / image / voice / video），沿用现有角色配置形状。
- 核心**没有内置的音乐来源**：没有通用的音乐接口形状（统一网关与本机模型也不覆盖这一档）。音乐来源由插件登记（适配器、`<前缀>:` 异步轮询、能力记录，见 [plugins.md](plugins.md)）；没装时「生成」在起任务之前就拒绝，并说明「内置来源不覆盖这一档，要装一个提供它的插件」。
- 参考图、条数、模型名这些参数怎么映射由各适配器决定；异步来源的轮询与生图 / 视频同一套（提交 → `<前缀>:<任务 id>` → 轮询 → 下载）。

### 上限从哪来

核心只设防手滑的上限，不替任何来源假设能力：一轮最多出 5 条（每条单独一次请求）、参考图最多 16 张（格式用 jpg / png，图像接口普遍都认）、时长 5–600 秒、BPM 30–240（`music/spec.py` 的常量）。来源可以在自己的能力记录里（插件用 `add_capabilities` 登记，键是 `("music", 来源 id, 模型前缀)`）写上更紧的数：

| 能力记录的键 | 含义 | 没写时 |
|---|---|---|
| `counts: {min, max}` | 一轮能出几条 | 核心的 1–5 |
| `reference_images: false` | 不收参考图 | 收 |
| `max_reference_images` | 一次最多几张参考图 | 核心的 16 |

登记的数只会收紧、不会放宽（`music.spec.limits_from_caps` / `source_limits`，按「模型设置」里配乐角色当前的来源查）。`generate_music` 在提交之前按它检查条数与参考图张数，不合就拒绝，一条请求都不发。

## 2. 项目文件 `content/music/<ID>.json`

```json
{
  "id": "MU-20260910-1",
  "title": "睡前摇篮曲",
  "brief": {
    "usage": "绘本睡前朗读的底噪",           // 一句话用途，进提示词开头
    "mood": ["温柔", "安定"],
    "genre": "摇篮曲",
    "instruments": ["音乐盒", "轻钢琴", "弦乐衬底"],
    "bpm": 70,
    "structure": "前 8 秒渐入，中段平稳，最后 3 秒渐弱到无",
    "avoid": ["鼓组", "人声", "强节拍"]
  },
  "duration": 120,
  "count": 2,                                  // 一次出几条（默认 1，上限见 §1「上限从哪来」）
  "reference_images": [],                      // 可空：参考图（收不收、最多几张由所选来源决定，见 §1）
  "music_params": {"model": "@music"},
  "attempts": [],                              // {file, created_at, model, request_hash, task_id, error}
  "current": null,
  "used_in": []                                // [{module, id, slot, at}] 记它被投放到哪些项目
}
```

提示词编译（`src/music/prompts.py`）：`usage + genre + mood + instruments + bpm + structure + 「不要：avoid」`，中英各一份，按 `prompt_language`。**预设 brief**：`skills/ai-music/scripts/music_briefs.json`（8–10 张，睡前摇篮曲 / 史诗战鼓 / 都市轻快 / 悬疑低频 / 温情钢琴 / 国风古筝 / 赛博电子 / 纪录片弦乐 / 喜剧诙谐 / 时尚节拍），形状对齐 `brief` 字段，选了就填进表单可再改。

## 3. 投放（这个模块存在的理由）

`POST /api/music/{id}/use` body `{module, target, slot}`：

- `module=drama`：把当前采用的那条拷进 `<project>/content/assets/<episode>/music/` 并追加到该剧集的 `post_graphics.music`（走 `webui/routers/post.py` 的校验路径，不绕过），`slot` 可选 `start` 秒；
- `module=picturebook`：拷贝并写进绘本的 `music.file`；
- `module=podcast`（2026-09-16）：把当前采用的那条拷进 `<project>/content/assets/<播客 id>/music/`（文件名带内容摘要，重复投放不堆副本），写进播客项目文件的 `music.<slot>`；`slot` 是槽名，只认 `intro / bed / outro`（片头 / 垫乐 / 片尾），不给就是 `bed`；写回前整份过 `podcast.spec.normalize` 再 `save`，不绕过（目标本身形状坏了 → 「目标播客读不出来：…」400，改完不过 → 「目标播客没接受这条配乐：…」400）；槽名不对在拷贝之前就拒绝；播客不存在 → 「没有这期播客：…」404。成功提示按槽说「已放进《…》的垫乐 / 片头 / 片尾」，`used_in` 记 `{module: "podcast", id, slot: 槽名}`，同一播客同一槽只留最新一条。`GET /api/music/targets` 多返回 `podcast: [{module, id, title, tracks, slots}]`（`slots` = 已有文件的槽名），`POST …/use` 的返回多带 `slot_label`；前端目标下拉多一组「AI 播客」，目标是播客时「放到哪个槽」下拉代替起始秒，已有文件的槽标「（已有，会覆盖）」。
- 成功后往 `used_in` 追一条，并在返回里带上目标项目的名字，前端提示「已放进《…》的配乐层」。

## 4. 接口 `/api/music/...`

`GET /api/music` · `next-id` · `POST /api/music` · `GET/PUT/DELETE /api/music/{id}` · `GET /api/music/briefs`（预设） · `POST /api/music/{id}/references`（base64 上传，张数上限见 §1） · `POST /api/music/{id}/generate`（后台任务 `music-generate`，`cost_note` 写清 N 条） · `POST /api/music/{id}/pick`（`{attempt}`） · `POST /api/music/{id}/use` · `GET /api/music/{id}/artifact/audio/{name}`（路径安全） · `POST /api/music/examples/create` · `GET /api/music/targets`（可投放的项目列表：短剧剧集 + 绘本，给前端下拉）。

## 5. 前端 `/music`

模块首页（列表 + 新建 + 复制示例）；新建（标题、预设 brief 下拉、用途一句话、情绪 / 乐器 chips、BPM、结构、避免、时长、条数）；项目页两个子页：**生成**（brief 表单 + 编译出的提示词预览 + 生成按钮（写清 N 条请求）+ 历次条目 `<audio>` 播放与采用 + 「放进项目」下拉（目标 + 起始秒）），**高级**（JSON、归档）。

## 6. 未验证

没有用真实音乐来源提交过：生成、轮询、下载、失败隔离全部用测试用的示例来源覆盖，真实效果要接上一个音乐来源之后才知道。

## 7. 实现偏差

契约与落地不一致的地方。

### 契约里没写、实现补上的

- **时长写进提示词。** 契约 §2 有 `duration`，但常见的音乐接口没有时长参数，有的对不认识的参数还会直接拒绝，所以时长进提示词的一句话（中文「时长：约 N 秒」/ 英文「Length: about N seconds」）；`MusicRequest.duration` 也带着，用不用由适配器决定。真要传给某个支持它的模型，写在项目文件的 `music_params.extra_params` 里，由适配器原样透传。
- **提示词的固定尾句。** §2 列的编译顺序是 `usage + genre + mood + instruments + bpm + structure + 不要`，实现在「结构」与「不要」之间补了一句固定的「纯器乐、无人声、可循环、成品混音」——配乐层要的就是这个，写在提示词里比每个人自己在 `avoid` 里补一遍可靠。
- **`has_intent()` 闸。** 只填了时长（或什么都没填）时，编译出来的仍是一句有固定尾句的废话。`POST /{id}/generate` 在这种情况下返回 400，不让它花钱。判据：`usage` / `genre` / `mood` / `instruments` / `structure` 里至少有一项非空（只填 `avoid` 或 `bpm` 不算）。
- **一轮 N 条 = N 次请求。** §2 的 `count` 是「一次出几条」。实现按**逐条提交**做（每次请求 `count=1`，跑 N 次），因为这样一条失败不拖累其余、每条各自有 attempt 记录与文件名。代价是 N 条 = N 次请求，`cost_note` 里写清楚了。
- **`current` 的自动采用。** 契约没说一轮跑完谁是「当前」。实现取**本轮第一条成功的**；其余留给 `POST /{id}/pick`。
- **失败的 attempt 不占文件名。** 失败条目的 `file` 是空串（半截文件会被删掉），所以 `attempts` 里能同时看到成功与失败，而 `current` 只能指向真的在盘上的那几条（`normalize` 会拦）。
- **`brief` 的列表字段也认一行。** `mood` / `instruments` / `avoid` 除了数组，也接受「顿号 / 逗号分隔的一行」——表单里人常常直接打一行。
- **`used_in` 去重。** 同一个目标项目 + 同一个 `slot` 只留最新一条，换了起始秒算另一条。

### 投放（§3）的实现细节

- 短剧那条**没有自己写 spec 文件**：`music/use.py` 直接调 `webui/routers/post.py` 的 `put_post_layers(episode_id, PostLayersEdit(post_graphics=…))`，所以后期块校验（文件必须在、`start` / `gain` / `duck` / `fade` 的区间、`end > start`）和**冻结后的 `amend`** 都在那条路上。目标项目拒绝时，配乐项目的 `used_in` 不会被写脏。
- 拷贝进 `content/assets/<目标>/music/`，文件名带内容摘要（`<原名>-<sha256前8位>.<ext>`），同一条重复投放不堆副本。**拷贝而不是引用**：之后删掉配乐项目，目标项目照常能出片。
- `slot` 只有短剧用得上（写成 `post_graphics.music[].start`），绘本的 `music.file` 没有起始秒，前端也就只在短剧目标下显示那个输入框。
- `GET /api/music/targets` 读文件系统列剧集与绘本，坏掉的 json 不跳过而是按 id 当标题列出来——一个坏文件不该让下拉框空掉。

### 其他

- 契约 §4 的接口全部实现，另外多了 `GET /api/music/{id}` 详情里的 `prompts`（中英两版一起给，前端预览用）与 `limits`（条数 / 时长 / BPM / 参考图上限，前端表单直接用；形状与 `music.spec.source_limits()` 的返回值一致）。
- 统一网关（LiteLLM 那类 OpenAI 兼容网关）**不覆盖** `music` 角色：没有 OpenAI 兼容的音乐接口形状，`GATEWAY_ADAPTERS` 里没有它，「一键应用到角色」会跳过 music 并在 `changed` 里不返回它。
