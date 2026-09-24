# skills/ — 随程序附带的创作数据

这里放的是程序运行时读取的只读数据文件：镜头语言库、风格骨架、题材卡、形态卡、审稿清单、出图风格卡、配乐预设和本地模型工作流。
它们随安装包一起分发（打包白名单见 `desktop/bundle_manifest.py`），在应用里只读，部分可以在「配置」页里逐条增改。
路径是代码写死的，改名或挪位置要同步改读取它们的模块。

## ai-short-film/scripts — 短剧模块

| 文件 | 读取方 | 内容 |
|---|---|---|
| `camera_library.json` | `storyvideo.prompts`、`storyvideo.scriptgen`、配置页 | 运镜术语（每条带 `risk` 与中英提示词短语）、镜内组合、跨镜镜头组、5 条经典产线 profile；`legacy_profiles` 只为兼容旧项目保留，界面不列 |
| `style_library.json` | `storyvideo.scriptgen`、配置页 | 负向词分档、各产线的画风骨架（frame_style 要覆盖哪几块 + 收尾警戒句）、scene_anchor 检查清单、视觉风格四要素模板 |
| `genre_cards.json` | `storyvideo.scriptgen`、新建项目页 | 12 张题材卡：压力来源、场面颗粒、集尾钩子、常见坑 |
| `genre_camera_pairing.json` | `storyvideo.pairing` | 题材卡 × 镜头产线的搭配提示（只做软告警） |
| `form_cards.json` | `storyvideo.forms` | 5 张交付形态卡：默认画幅 / 单镜时长 / 字幕、必带项、运动预算句、构图句 |
| `craft_checklist.json` | `storyvideo.craft_review`、`script.review` | 剧本工艺审稿清单（张力 / 情绪可见 / 节奏 / 合规） |

`camera_library.json` 与 `style_library.json` 就是数据源本身：直接改 JSON，或在应用的「配置」页里改；
写盘前 `storyvideo.config_validate` 会查 id 重复和互相引用。改了进提示词的字段（`prompt_cn` / `prompt_en`、构图句、运动预算句）
之后跑 `python scripts/refresh_prompt_canary.py`，确认提示词的变化是有意的。

## ai-image/scripts — 出图风格卡

`style_cards.json`：图片、表情包、写真、绘本、海报、单条视频共用的风格卡（锚句、光线、色板、材质、观感、约束），
由 `imagecraft.cards` 读取。

## ai-podcast/scripts — 播客

`humanlike_checklist.json`：播客 / 对谈稿的真人感审稿清单（口语 / 对谈动态 / 内容），由 `podcast.review` 与 `script.review` 读取。

## ai-music/scripts — 配乐

`music_briefs.json`：配乐预设 brief，由 `music.briefs` 读取。

## local-models/workflows — 本地 ComfyUI 工作流

三份 API 格式的 ComfyUI 工作流（SDXL 文生图 / 图生图、Wan 2.1 文生视频）和各自的 `*.map.json` 字段映射，
由 `genkit.adapters.comfyui` 读取；用户自己导出的工作流放在数据目录的 `local_workflows/` 下。

## 来源与许可

- `genre_cards.json` 是 zenstory-ai/drama-skills 题材卡的节选与压缩（MIT，多数字段摘录原卡原句），
  每张卡保留原卡的 `confidence` / `basis` / `source_file`；`form_cards.json` 按它的形态卡判据改写，个别句子沿用原文，构图句是本项目自己的写法；
  `genre_camera_pairing.json` 是本仓库人工设定的搭配，`why` 里有几句摘自题材卡原文（MIT）。
- `craft_checklist.json`（剧本工艺清单）是本项目按编剧常识自拟的审稿条目，取舍见 `docs/script-craft-gap.md`。
- `style_cards.json` 是本仓库按常见的摄影布光、插画技法与版式常识自写的出图风格卡（出处见 `_meta.basis`）。
- `style_library.json` 里的镜头设计与接缝规则借鉴 waoowaoo 的视频导演规范，`scripts/refresh_prompt_canary.py` 借它的
  prompt-canary 思路；只借规则与做法，措辞是自己的。
- 其余文件是本项目自己整理的经验规则。各文件都写了 `basis` / `confidence`，全部是判断而不是测量结论。

第三方许可声明见 `docs/third-party-notices.md`（随安装包分发）。
