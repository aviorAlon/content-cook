# 图片工作台模块 `image`（🖼，路由 `/image`，2026-09-10）

自由出图：一句话 + 风格卡 + 参考图 → 一组候选；多模型同题对比；挑中的可以叠字做成图卡。**不新写引擎**——它是 `src/imageset/` 的第三个模块（`MODULES = ("sticker", "portrait", "image")`），只加自己的模板包，因此列表 / 出图 / 挑选 / 后处理 / 打包 / 从不覆盖 / 任务全部复用。

## 1. 三个模板（`src/imageset/templates.py` 追加，`module="image"`）

| 模板 | 用途 | options | expand | post | export |
|---|---|---|---|---|---|
| `freeform` 自由出图 | 一句话出 N 张候选，挑一张 | `brief`（画面一句话，必填）、`variants`（1–8，默认 4）、`aspect`（1:1 / 3:4 / 4:3 / 9:16 / 16:9，默认 1:1）、`negative`（可空） | N 个 item，同一提示词、同一画幅；第 i 张在 `meta.variant` 上记序号（供应商各自随机） | 按 `aspect` 居中裁一版 + 长边 2048 一版 | `images/{item}.png` + `manifest.json` |
| `model_compare` 多模型对比 | 同一提示词换模型各出一张，横向比 | `brief`、`models`（2–6 个模型 id，默认取设置里的生图模型 + 目录里的候选）、`aspect` | 每个模型一个 item，`meta.model` 覆盖 `image_params.model` | 同上 | `compare/{model}.png` + `manifest.json`（每条记模型） |
| `text_card` 图卡 | 出底图后本机叠标题（海报那套字体与投影） | `brief`、`title`、`subtitle`、`aspect`（默认 3:4）、`layout`（auto/top/bottom）、`variants`（1–4） | N 个 item | 复用 `storyvideo.poster.render_poster` 的叠字（底图满幅裁切、渐变压暗、粗体中文字体、副标题限 24 字） | `cards/{item}.png` |

- `model_compare` 的 item 级模型覆盖：`generate.py` 请求时若 `item.meta.model` 非空就用它，`cost_note` 照样按 item 数算。这是引擎唯一需要改的地方（其余模板不受影响）。
- 三个模板的 `fits` 让风格卡库里所有卡都能选（`image.freeform` / `image.model_compare` / `image.text_card` 进各卡的 `fits`，或按分类合理挑）。

## 2. 主体

`subject.kind` 沿用 `character`（写身份块，可传定妆图）与 `photo`（传参考照）；两者都可留空——自由出图允许没有主体，只有画面描述。提示词骨架复用 `src/imageset/prompts.py`，主体为空时跳过身份块与排他锁。

## 3. 模块注册与示例

`src/webui/modules.py` 里 `image` 改 `ready`：`content_dir="content/image"`、`route="/image"`、`project_kinds=("image",)`，描述写「单张 / 成组出图、多模型对比、图卡叠字」。示例 `examples/image/lantern_night.json`（freeform，4 张候选，一句话画面 + 一张风格卡）。

## 4. 前端

复用 `frontend/src/views/imageset/*`（同一套 SetHome / SetNew / Set / SetItems / SetOutputs / SetAdvanced，模块从路由 meta 取），只加：

- `IMAGESET_MODULES` 加 `image`（图标 🖼、名字「图片工作台」、subject kind 默认 `character` 但允许空）；
- 路由 `/image`、`/image/new`、`/image/s/:id/{items,outputs,advanced}`；
- 侧栏手风琴加一块；`SetNew.vue` 对 `model_compare` 模板把 `models` 选项渲染成多选（从 `/api/system/model-catalog` 或设置里的生图模型给候选）；
- `SetItems.vue` 对 `model_compare` 在每行显示模型名。

## 5. 未验证

真实模型上的候选质量与多模型对比；`text_card` 的叠字已在海报那边真机验证过（同一段代码）。

## 6. 实现偏差（2026-09-10 落地时记）

1. **`model_compare` 的包内路径**：契约写 `compare/{model}.png`，但 `export.py` 的路径模板只喂 `{item}`。没有为一个模板写第二套打包逻辑，而是给引擎的 `export_layout` 多认一个 `{label}`（标签压成 ASCII 文件名，`genkit.ids.slugify`），于是 `compare/{label}.png` 就是按模型名归档（`@image` → `image`，`openai/gpt-image-2` → `openai_gpt_image_2`）；模型原文在 `manifest.json` 每条产物的 `label` 里。老模板的布局串不含 `{label}`，行为不变。
2. **长边 2048 那版也进包**：契约的 export 栏只写了 `images/{item}.png`。后处理出了两版，只打包一版等于本机白做，所以包里是 `images/{item}.png`（原分辨率按画幅裁）+ `images/2048/{item}.png`；`model_compare` 同理多一层 `compare/2048/`。
3. **`render_poster` 多一个 `canvas_size` 参数**：图卡要 3:4，海报是写死的 1080×1920。没有复制一份叠字代码，而是把海报里的裁切 / 忙碌度判定 / 压暗层从模块常量改成按画布尺寸算，`canvas_size` 缺省仍是 `(POSTER_WIDTH, POSTER_HEIGHT)`——海报的输出逐字节不变（`tests/test_poster.py` 全绿）。图卡画布短边固定 1080（3:4 → 1080×1440，16:9 → 1920×1080）。字号常量（标题 176 / 副标 42）没有按画布缩放：1080 短边下这套度量正合适。
4. **`brief` 留空不报错**：契约写「必填」，但 `options_schema` 没有「必填」这个概念（`spec._options` 只校验类型与区间）。留空时用项目标题当画面描述，而不是拒绝保存——新建页先建后填是常态。
5. **`model_compare` 的默认模型清单**（2026-09-10 补齐）：模板注册仍是静态数据，读不到设置，所以 `options_schema` 里的默认值还是写死的 `["@image", "gpt-image-2", "nano-banana"]`——**它现在只是兜底**。真正给人挑的候选由后端的 `webui.routers.imageset.model_candidates(role="image")` 算出来，跟在 `GET /api/{m}/templates` 与项目详情里一起下发（顶层 `model_candidates`，`[{id, label, source}]`）：

   - 顺序 **`configured` → `catalog` → `local`**，同一个 id 先到先得去重（`@image` 永远第一条，且只出现一次）：
     - `configured`：`@image` 这个别名（标签写成 `@image → <设置里的模型>`）+ 设置里那个模型的真实 id；
     - `catalog`：模型目录缓存（`~/.storystudio/model_catalog.json`，openrouter / gateway 两节）里 `roles` 含 `image` 的 id；
     - `local`：最近一次本机扫描留在内存里的 SD WebUI / ComfyUI 模型名（标签带服务名）。
   - **只读缓存，一个请求都不发**：目录要用户在设置页点过「刷新模型目录」才有，本机那段要扫过才有；设置文件坏了当没配。三段都空时（新装的机器）退回模板那份静态默认清单，source 记 `default`，`model_compare` 照样能展开成三张图。
   - 新建页的 `models` 多选按 `source` 分组显示（已配置 / 模型目录 / 本机 / 模板默认），候选里没有的 id 手写加进来落进「手写」一组；发出去的请求体一个字没变，还是一串模型 id。
6. **`text_card` 的「留干净」句分上下两版**：`layout=bottom` 时用新加的 `TEXT_SPACE_BOTTOM_ZH/EN`（下三分之一），其余用原来的上三分之一版本——否则提示词让模型留上边、叠字却压在下边。
7. **主体为空**：新增 `prompts.has_subject()` / `subject_block()`，三个 image 模板用后者取身份块；空主体时跳过身份块与排他锁（契约 §2），非空时与表情 / 写真逐字同源。表情 / 写真仍直接调 `identity_block()`，输出不变。
