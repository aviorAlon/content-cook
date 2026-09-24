# 出图风格卡 + 图像方言（2026-09-09）

动机：生图的几个模块需要一套能挑的、成套的风格，而不是各模板里散落的预设。

现状的问题：每个模板各写了三四个风格预设（`flat_cute` / `studio_soft` …），字符串散在 `templates.py` 里，绘本另有一套四个预设，海报干脆没有预设。要的是**能挑的、成套的风格**。

做法沿用短剧那套已经验证的形状（题材卡 DESIGN §26、形态卡 §31、提示词方言 §17）：**一套共享卡库 + 一层模型方言**，取判据不取语法。

出处：卡与方言的文字都是本仓库自己写的，依据是常见的摄影布光、插画技法与版式常识；卡库的 `_meta.basis` 写明这一点。

## 1. 卡库 `skills/ai-image/scripts/style_cards.json`（≤ 40 KB）

```json
{
  "_meta": {"schema_version": 1, "basis": "本仓库自写：…", "note": "…"},
  "categories": [
    {"id": "portrait", "name": "人像写真", "english": "Portrait"},
    {"id": "illustration", "name": "插画", "english": "Illustration"},
    {"id": "character", "name": "角色与表情", "english": "Character"},
    {"id": "product", "name": "产品", "english": "Product"},
    {"id": "fashion", "name": "时尚", "english": "Fashion"},
    {"id": "food", "name": "美食", "english": "Food"},
    {"id": "poster", "name": "海报与平面", "english": "Poster"},
    {"id": "social", "name": "界面与社交", "english": "UI / social"}
  ],
  "cards": [
    {
      "id": "studio_soft_portrait",
      "name": "影楼柔光",
      "english": "Soft studio portrait",
      "aliases": ["柔光棚拍", "影楼", "studio"],
      "category": "portrait",
      "summary": "大面积柔光从前侧打过来，背景一色到底，皮肤看得出纹理——拍纪念照最不容易出错",
      "anchor_zh": "柔光棚拍人像：一盏大尺寸柔光灯从前侧方照亮面部，对侧用白板把暗部托起来，背景是一整块纯色，皮肤保留细纹与毛孔。",
      "anchor_en": "Soft-lit studio portrait: one broad diffused lamp from the front side, a white card lifting the far cheek, a single even backdrop colour, skin keeping its fine lines and pores.",
      "lighting": "前侧方大面积柔光作主光，对侧白板回光，影子边缘很软，眼睛里映出一小块灯影",
      "palette": ["象牙白", "燕麦色", "浅杏", "雾粉", "暖灰"],
      "materials": "针织开衫、亚麻衬布、纸质背景卷、原木小凳",
      "camera": "比平视略高一点的机位，胸像到半身，背景轻微虚化",
      "constraints": ["脸上的明暗过渡平缓，下巴下方留一点影子", "背景从头到尾是同一个颜色", "画面里只有人物和背景纸"],
      "fits": ["portrait.newborn_30", "portrait.baby_100", "portrait.first_birthday", "portrait.wedding", "sticker.avatar_set"],
      "model_hint": "any"
    }
  ]
}
```

字段约定：

- `anchor_zh` / `anchor_en` 是**进提示词的那句话**（风格锚），其余字段是提示词的可选补强项，由编译器按方言组织。
- `palette` 必须是 5–7 个**具体色名**（docs/prompt-patterns.md P2 的判据）。
- `camera` 只写观感（「比平视略高一点的机位，胸像到半身」），**不写数字焦段/光圈**——这类数字换不来对应的画面，`nano_banana` 方言还会把它们删掉。
- `constraints` 正面表述优先；编译器把它拆成提示词里的约束句与负面词。
- `fits` 是 `<module>.<template>` 或 `picturebook` / `poster`；用于界面里「适合」筛选，不做硬限制（用户想跨用也允许）。
- `model_hint`：`any` / `gpt_image` / `nano_banana`，只做提示，不拦。

**卡的规模**：每类 2–4 张，共 22–26 张。必须覆盖：人像写真（影楼柔光 / 窗光自然 / 暖调胶片 / 黑白棚拍）、插画（水彩绘本 / 蜡笔童趣 / 剪纸拼贴 / 扁平矢量）、角色与表情（扁平可爱贴纸 / 线稿涂鸦 / Q 版三维 / 像素）、产品（纯色悬浮 / 场景静物）、时尚（街头大片 / 编辑部棚拍）、美食（暗调质感 / 明亮平铺）、海报（极简大字 / 电影质感 / 复古印刷）、界面与社交（干净 UI 展示 / 社交九宫格）。

## 2. 图像方言 `src/imagecraft/dialects.py`

同一张卡 + 同一段画面，按目标模型换组织方式（本仓库按对这几类模型的一般认识自己定的写法，没有对着真实模型做过对比）：

| 方言 | 何时用 | 输出形状 |
|---|---|---|
| `generic`（默认，老项目不变） | 未知模型 | 现在的写法：风格锚 + 主体 + 画面 + 约束 + 无文字尾句，一段连写 |
| `gpt_image` | 模型 id 含 `gpt-image` | 按「场景 → 主体 → 细节 → 用途 → 约束」分五行，每行带标签：`Scene:` / `Subject:` / `Key details:` / `Intended use:` / `Constraints:`；删掉 `masterpiece` / `8k` / `stunning` 这类只夸不描述画面的空话；画面文字用引号原样给 |
| `nano_banana` | 模型 id 含 `nano-banana` / `gemini-image` | 自然语言 1–2 段；**不写数字焦段光圈**；最后单独一行 `Aspect ratio: 1:1`；多参考图按序号说明各自管什么 |

`resolve_image_dialect(model, override)`：显式 `image_dialect` 优先，否则按模型 id 猜，猜不到 `generic`。**没写 `image_dialect` 且模型不在识别表里的老项目，输出逐字节不变**（金丝雀守住）。

## 3. 提示词检查 `src/imagecraft/lint.py`

按方言检查一段图像提示词，返回和其他 lint 同形状的告警（进问题卡）：

- `gpt_image` 方言里出现 `masterpiece / 8k / ultra detailed / stunning / 大师级 / 惊艳 / 史诗` → 「这些词只夸不描述画面，删掉，换成具体的光线、材质或颜色」。
- `nano_banana` 方言里出现 `50mm / f/2.8 / ISO 800` 这类数字参数 → 「这类数值换不来对应的画面，改成直接描述想要的样子」。
- 负面表述（`no people` / 「不要出现路人」）→ 「反着写：说清画面里有什么，别列没有什么」（画面文字政策那句 NO_TEXT 尾句是既定例外，不告警）。
- 画面里要出现的文字没加引号 → 「要出现的文字用引号原样写出来」。
- 色板少于 5 个具体色名（卡里带 `palette` 时）→ 提示（不是错误）。

## 4. 接线

- **imageset**（表情 / 写真）：`spec.style` 除模板预设 id 与 `custom` 外，**接受卡 id**；卡的锚句替代预设锚句，`palette` / `lighting` / `materials` / `camera` 按方言补进提示词，`constraints` 进约束句与负面词。模板原有预设**保留且行为不变**（老项目零变化）。`GET /api/{m}/templates` 的每个模板多返回 `style_cards`（`fits` 命中的卡）。
- **绘本**：`spec.style_card`（可空）——填了就用卡的锚句替换 `style_anchor` 的默认预设；没填零变化。
- **海报**：`poster` 请求支持 `style_card`，进关键画面提示词。
- **新增只读接口** `GET /api/style-cards`：`{categories, cards}`，支持 `?category=` / `?fits=` 过滤；`GET /api/style-cards/{id}` 单张。

## 5. 前端

- 新组件 `StyleCardPicker.vue`：按分类分组的卡片网格（名字 + 一句话 + 色板小色块 + 「适合」标签），选中显示锚句全文（只读）与「改成自写」按钮（切到 `custom` 并把锚句填进文本框当起点）。
- 用在：图片集新建页与「清单」页顶部的风格行（换卡后提示「换风格要重出才生效」）、绘本「角色与风格」页、成片页海报卡。
- 图像方言：图片集与绘本的高级区显示当前方言（自动/手选），一行说明它改了什么。
- 文案 `t()` 中文 key，英文进 en.js `overrides` 末尾。

## 6. 实现偏差（2026-09-09 落地时记）

以实现为准，正文保留当时的设计记录。

- **卡库 24 张**（契约写 22–26），约 36 KB。八类齐全，覆盖清单里的每一项都在。
- `src/imagecraft/` 分三个文件：`cards.py`（按 id / 名字 / 别名查、`cards_for(fits=, category=)`、`card_payload` 给接口用）、`dialects.py`（`resolve_image_dialect` + 三个渲染器 + `strip_anti_slop` / `strip_lens_numbers` + 四种连接风格 `JOIN_STYLES`，因为四个面的句子拼法本来就不同）、`lint.py`（五项检查 + `check_items` 批量版）。
- `image_dialect` 的取值多一个 `auto`（按模型猜）；**留空**才是老项目那条零变化的路（按 `generic` 写）。
- 图片集的 `style` 现在三选一：模板预设 id、卡 id、`custom`；卡 id 与预设 id 不重名（卡都是 `<形容>_<名词>` 双词以上）。
- 绘本只加 `style_card`（不加方言字段的独立开关，方言跟 `image_params.model` 走）。
- 海报的 `style_card` 走请求参数，不落进项目文件——海报本来就是一次性产物。
- `generic` 的逐字节不变由快照测试守着：`tests/fixtures/image_prompts_generic.json` 存了表情 / 写真 / 绘本 / 海报四个面改动前的输出。
- 2026-09-10：图片工作台（DESIGN §42）落地后，`fits` 多了三个目标 `image.freeform` / `image.model_compare` / `image.text_card`。自由出图对画面没有限定，24 张卡全都进了 `image.freeform`；`image.model_compare` 按分类挑了 8 张（每类一张有代表性的），`image.text_card` 挑了 11 张背景干净、留得住大字的。卡库仍在 40 KB 以内。
