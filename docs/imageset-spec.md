# 图片集引擎 + 两个模块：「表情与头像」「纪念写真」（2026-09-09）

需求：表情包 / 贴图 / 头像放一个模块；满月 / 百天 / 周岁 / 婚纱照放一个模块。两者本质相同——**按模板出一组图，挑选，后处理，打包**——所以先做一个共享的图片集引擎 `src/imageset/`，两个模块只是模板包 + 各自的路由前缀与页面。共用：模型设置（生图角色）、参考图上传、对比试跑思路、海报的 Pillow 叠字、任务、导出、i18n。

## 1. 项目文件

`content/sticker/<ID>.json` 与 `content/portrait/<ID>.json`，同一形状：

```json
{
  "id": "ST-20260909-1",
  "module": "sticker",                 // sticker | portrait
  "title": "小满表情包",
  "template": "emoji_pack",            // 见 §3 / §4
  "subject": {
    "kind": "character",               // character（文字身份块 + 可选定妆图）| photo（真人参考照，纪念写真用）
    "name": "小满",
    "block": "二十六七岁，齐肩黑发低马尾，圆框眼镜，米白工装外套",
    "assets": ["content/assets/ST-20260909-1/ref1.png"],   // 参考图（定妆 / 真人照片），最多 6 张
    "fidelity": 85                     // 与参考图相似度百分比，进提示词（docs/prompt-patterns.md P3）
  },
  "style": "flat_cute",                // 模板给的风格预设 id，或自写风格锚
  "style_anchor": "",                  // 自写时用
  "options": {},                       // 模板自定义项：表情清单 / 头像风格清单 / 场景与道具 / 日期文字 …
  "image_params": {"model": "@image", "image_size": "2K"},
  "items": [                           // 引擎按模板展开的「每张图」清单（generate 时写入，可手改 prompt 再重出）
    {"id": "I01", "label": "开心", "prompt": "…", "aspect_ratio": "1:1", "status": "todo", "current": null, "attempts": []}
  ],
  "picks": ["I01", "I03"]              // 挑选结果（导出只带这些；空 = 全部）
}
```

`items[].attempts` 每次出图一条 `{file, created_at, model, request_hash}`；`current` 指向采用的那张；文件在 `output/<module>/<ID>/01_raw/<item>-<n>.png`，**从不覆盖**。

## 2. 引擎（`src/imageset/`）

| 文件 | 职责 |
|---|---|
| `spec.py` | 归一化 / 读写 / `next_id(module)`（`ST-` / `PT-` 前缀）/ 归档删除；`subject.assets` 用 `storyvideo.spec._resolve_local_asset` 解析 |
| `templates.py` | 模板注册：`Template(id, module, name, english, description, styles[], options_schema, expand(spec)->items[], post(spec, item, path)->outputs[], export_layout)`；`expand` 只产出 items 的 prompt/aspect/label，不调模型 |
| `prompts.py` | 提示词骨架：风格锚 + 主体身份块（角色）或「和参考照片是同一个人，相貌相似度约 N%」（真人）+ 单张画面 + 模板固定约束（白底 / 无文字 / 排他锁）+ NO_TEXT；负面词 |
| `generate.py` | 后台任务：按 items 逐张请求（`build_provider()`，同步直接落盘，异步在任务内轮询 20 分钟上限），请求哈希不变且已有 current 则跳过（`force` 重出），单张失败不拖累整批，任务记录 `done/skipped/failed` |
| `post.py` | Pillow 后处理（`templates.post` 调用）：白底/纯色底转透明（角落取样 + 容差泛洪，不用 rembg）、居中裁切到目标尺寸、微信表情规格（主图 240×240 PNG ≤ 500 KB，缩略 120×120，面板图标 50×50，横幅 750×400）、头像 1:1 多尺寸（1080 / 512 / 200）、写真 3:4 与 4:3 两版裁切、日期/祝语叠字（复用 `storyvideo.poster` 的字体解析与投影文字） |
| `export.py` | 打包 `09_export/<ID>-<时间>.zip`：按模板 `export_layout` 组织（表情包：`stickers/`、`banner.png`、`icon.png`、`manifest.json`；写真：`album/`、`album_4x3/`、`contact_sheet.jpg` 九宫格）+ sha256 |

引擎路由 `src/webui/routers/imageset.py`，两个前缀共用一套处理器（`/api/sticker/...` 与 `/api/portrait/...`，`module` 由前缀决定）：

| 方法 | 路径（`{m}` = sticker / portrait） | 说明 |
|---|---|---|
| GET | `/api/{m}` · `/api/{m}/next-id` · `/api/{m}/templates` | 列表（id/title/template/进度/封面）· 编号 · 模板与风格预设（含每模板的 options 说明） |
| POST | `/api/{m}` | 新建（title, template, subject, style, options） |
| GET/PUT/DELETE | `/api/{m}/{id}` | 详情（spec + items 状态 + 每张 url）/ 保存 / 归档 |
| POST | `/api/{m}/{id}/assets` | 参考图上传（base64，不覆盖；≤ 6 张） |
| POST | `/api/{m}/{id}/expand` | 按模板展开 items（保留已有 current；prompt 可在前端改后 PUT 回来） |
| POST | `/api/{m}/{id}/generate` | `{only?, force?}` 出图任务（种类 `set-generate`，`module` 按前缀），返回 `cost_note`（N 次生图请求） |
| POST | `/api/{m}/{id}/pick` | `{item, attempt}` 采用某次 / `{picks:[…]}` 挑选 |
| POST | `/api/{m}/{id}/post` | 跑后处理（本机，不花钱），返回每张的产物清单 |
| POST | `/api/{m}/{id}/export` | 打包 zip，返回下载 url |
| GET | `/api/{m}/{id}/artifact/raw/{name}` · `/out/{name}` · `/asset/{name}` · `/export/{name}` | 产物流（路径安全） |
| POST | `/api/{m}/examples/create` | 复制示例（每模块一个） |

## 3. 模块「表情与头像」`sticker`（🙂，路由 `/sticker`）

模板：

- **emoji_pack 表情包**：options `expressions[]`（默认 16 个：开心 / 大笑 / 哭 / 委屈 / 生气 / 惊讶 / 疑惑 / 害羞 / 无语 / 困 / 点赞 / 比心 / 加油 / 谢谢 / 晚安 / 再见，可增删改）、`with_caption`（默认 false；true 时用叠字写中文短语，不让模型画字）。expand：每个表情一张 1:1，提示词 = 风格锚 + 身份块 + 「同一角色、同一头身比、同一线宽」+ 该表情的脸部/肢体描写 + 「纯白背景、留边、无文字、无阴影」；风格预设：flat_cute（扁平可爱）/ line_doodle（线稿涂鸦）/ chibi_3d（Q 版 3D）/ pixel（像素）。post：白底转透明 → 240×240 主图 + 120×120 缩略；另出 750×400 横幅（第一张放大 + 叠标题）与 50×50 图标（第一张裁头部）。export：微信表情包目录结构。
- **sticker_set 贴图**：options `themes[]`（如 咖啡 / 加班 / 周末，默认 8 个）、`shape`（圆形 / 方形 / 异形）；每主题一张 1:1，白底转透明，输出 512 与 1024。
- **avatar_set 头像**：options `styles[]`（默认 9：写实 / 油画 / 水彩 / 赛博 / 国风 / 像素 / 黏土 / 剪纸 / 线稿）、`crop`（头肩 / 半身）；每风格一张 1:1，输出 1080 / 512 / 200，可选圆形蒙版版本。

示例：`examples/sticker/xiaoman_emoji.json`（角色沿用示例剧集的林小满，8 个表情，flat_cute）。

## 4. 模块「纪念写真」`portrait`（📷，路由 `/portrait`）

主体是真人参考照（`subject.kind = photo`，1–6 张正脸清晰照），提示词写「和参考照片是同一个人，相貌相似度约 N%」+ 场景/服装/道具 + 影楼级布光；**不改脸型、不美白过度、婴儿不加饰品在口鼻**（安全约束进负面词与固定句）。

模板（options 里都有 `scenes[]` 可增删、`count_per_scene` 默认 1、`text`（叠字：祝语 + 日期，可空）、`aspect` 默认 3:4）：

- **newborn_30 满月**：场景默认 6：襁褓 + 小鹿玩偶 / 云朵毯 / 月亮道具 / 姓名字母块（不出字，留空位）/ 花环 / 父母手心；固定句：柔光棚拍、婴儿安睡或安静、无坚硬道具贴脸。
- **baby_100 百天**：场景默认 6：百天蛋糕（无火）/ 数字气球「100」（叠字实现，不让模型画）/ 抓周预演 / 小马甲 / 趴姿抬头 / 全家福。
- **first_birthday 周岁**：场景默认 6：生日帽 + 蛋糕（无火）/ 抓周物件 / 学步 / 气球墙 / 唐装或小西装 / 全家福。
- **wedding 婚纱**：`subject` 允许两人（assets 里标 `who`：新娘 / 新郎；提示词逐人点名 + 排他锁）；场景默认 8：教堂 / 海边 / 城市夜景 / 中式红妆 / 森林 / 复古胶片 / 黑白棚拍 / 雨中伞。

post：3:4 主版 + 4:3 副版裁切、可选叠字（祝语 + 日期，字体与投影复用海报）；export：相册目录 + 九宫格样片。示例：`examples/portrait/baby_100_demo.json`（没有真人照片，示例只带模板与场景，`subject.assets` 空，前端提示先上传）。

## 5. 前端

- `/sticker`、`/portrait` 各自的模块首页（列表 + 新建 + 复制示例）、新建页（标题、模板、风格、主体：角色身份块 + 定妆上传 / 真人照片上传、模板选项编辑）、项目页三个子页：**清单**（items 表：缩略图、标签、prompt 可改、状态、单张重出、采用/挑选）、**产物**（后处理结果预览、导出 zip）、**高级**（JSON、归档）。
- 出图前确认框写清 N 次生图请求；任务用 `pollTask`；侧栏手风琴各加一块（新建 + 搜索 + 列表）；模块注册表两条改 `ready`。
- 字符串 `t()` 中文 key，英文进 en.js `overrides` 末尾。

## 6. 实现偏差

后端按这份契约落地（`src/imageset/`、`src/webui/routers/imageset.py`，2026-09-09），下面是**实现跟上面正文对不上的地方**——以实现为准，正文保留原样当作当时的设计记录。

**项目文件**

- `items[]` 比 §1 的示例多四个字段：`negative`（模板算出的负面词）、`meta`（模板自用，如 `{expression, caption}` / `{scene, take}`）、`request_hash`（当前 `current` 是哪次请求出的）、`error`（最近一次失败原因）。
- `attempts[]` 除了 `{file, created_at, model, request_hash}` 还有 `task_id` 与 `error`——**失败也留一条**（`error` 非空、没有文件），所以历次条能看出"这一张试过三次、两次被拦"。
- 多一个顶层字段 `prompt_language`（`zh` / `en`，默认 `zh`），提示词固定句与画面描写都有中英两版。
- `style` 只接受模板给的预设 id 或字面量 `custom`；选 `custom` 时 `style_anchor` 不能为空。写真的预设 id 是 `studio_soft` / `natural_window` / `film_warm`（婚纱多一个 `editorial`），头像是 `clean_studio` / `soft_gradient` / `scene`，表情与贴图是 `flat_cute` / `line_doodle` / `chibi_3d` / `pixel`。
- `avatar_set` 的选项里多一个 `circle`（布尔，默认开）——§3 只在正文里提了"可选圆形蒙版版本"，没给键名。
- 写真的 `text` 用**竖线**分隔祝语与日期（`"百天快乐|2026.09.09"`），留空不叠字。
- id 按天编号（`ST-20260909-1` / `PT-20260909-1`），不是纯序号。

**接口**

- `POST /{id}/post` 多一个 `{background: true}`：走后台任务 `set-post`；同步那条路返回 `{run, failed}`（`run.outputs[]` 每条带 `url`）。没有可后处理的图返回 **425**，`export` 在没有后处理产物时同样 425。
- `POST /{id}/pick` 的 `attempt` 是**文件名**（`"I01-1.png"`），不是序号；`{item, attempt}` 与 `{picks: […]}` 可以在同一次请求里一起发。attempt 不是这张图成功出过的文件名 → 400。
- 详情（`GET /{id}`）比"spec + items 状态 + 每张 url"多给：`template_info`（模板 payload：风格预设 + `options_schema` + `export_layout`，前端不用再查 `/templates`）、`font_available`（本机有没有中文字体，决定叠字能不能用）、`progress`、`busy`（两种任务各自的 task_id）、`outputs`（最近一次后处理）、`exports`、`cover_url`、`assets`、`model_candidates`（同下）。
- `GET /{m}/templates` 除模板外还给 `subject_kinds` / `max_assets` / `who_choices` / `default_image_params` / `default_fidelity` / `examples` / `font_available`，以及 `model_candidates`（`[{id, label, source}]`，source = `configured` / `catalog` / `local` / `default`；只读缓存算出来的生图模型候选，见 `docs/imagestudio-spec.md` §6 第 5 条）。
- `GET /{m}` 每行多 `module` / `pipeline` / `path` / `style`；读不出来的项目文件不让整张列表挂掉，那一行是 `{status: "error", error}`。
- `generate` 的 `cost_note` 原话是「最多 N 次生图请求（{model}）；请求没变且图已在的会跳过」。同一项目同一种任务已经在跑 → 409。
- `POST /{id}/assets` 的文件名带内容摘要（`<名字>-<sha256 前 8 位>.<后缀>`），同内容重传返回同一个文件、不写第二份；返回 `{file, bytes, who, url, assets}`。
- `artifact/raw/{name}` 只放行项目文件认识的文件名（`current` 与各次 `attempts[].file`）——产物目录里的其他 png 一律 404。

**产物与打包**

- 表情包的横幅与图标由 `post_extra` 产出（整套一张，产物记录里 `item: null`），不属于任何一张图。
- `sticker_set` 的两档尺寸在包里分目录：`stickers/512/{item}.png` 与 `stickers/1024/{item}.png`；头像是 `avatars/{1080,512,200,circle}/{item}.png`。
- 打包出的是 `09_export/<ID>-<时间>/` 目录 **+** 同名 zip，目录里除产物外还有 `manifest.json` 与 `checksums.sha256`（逐文件 sha256，不是单个校验值）。同一分钟再导一次不覆盖，顺延成 `<ID>-<时间>-2`。
- 后处理产物在 `05_out/`，文件名是 `<item>-<role>-<运行戳>.png`，`post.json` 每次运行追加一条；最新一条里文件还在的那些就是"当前产物"。

**内置清单的字面**

- 满月的场景键是「姓名字母块（留空位）」、百天是「数字气球「100」（留空位）」（§4 里写的是括号里的说明文字，实现把"留空位"当成触发词——场景名里带"留空位 / 留空 / 叠字"就自动补一句"画面上三分之一干净，留给后期叠字"）。
- 示例 `examples/portrait/baby_100_demo.json` 的场景名是简写（「百天蛋糕」而不是「百天蛋糕（无火）」），所以走"清单外按原文出图"的路径，内置的画面描写不生效——示例本来就是给人改的。
- 示例 `examples/sticker/xiaoman_emoji.json` 的角色叫「小满」（不是示例剧集里的「林小满」），8 个表情、`with_caption: true`。
