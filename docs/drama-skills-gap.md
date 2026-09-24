# drama-skills（zenstory-ai）对照与融合记录

status: 2026-09-08。对照对象是 zenstory-ai/drama-skills 的 `short-drama*` 十个 skill（MIT）。它是"给 agent 读的创作规格"，我们是"排队生成 + 账本 + QC 的工具"，它自己的话：**"现在留在自建工具里的，只剩排队抽卡。"** 所以融合原则：**取判据，不取语法**——它的 REF/PLAN/LOCK 标记语法、四态参考图、130 多条规则表都是给手写 Markdown 打补丁的，我们把同一件事做成 spec 字段 + 编译规则 + 静态检查。

## 已融合（2026-09-08）

| 它的技巧 | 我们的落地 | 位置 |
|---|---|---|
| 目标模型方言（MiniMax H3 三段结构、`<d>[Chinese]`、`(S1)`；Seedance `{角色用中文说}` `<声音>`） | `video_params.prompt_dialect`，按模型自动选，老项目缺省不变；概览页一键切换 | `storyvideo/dialects.py`，DESIGN §17 |
| 对白预算：窗口扣开口等待与末尾反应，按慢语速核容量 | `preview.shot_duration_budget` 加 `window_seconds` / `tight` | `storyvideo/preview.py` |
| 视觉依据：正文点名的角色必须在本镜 cast | `lint.check_cast_mentions`（别名、`name_in_prompt:false`、代词算提到） | `storyvideo/lint.py`，DESIGN §18 |
| 可生成性四类高风险写法 | `lint.check_generability` | 同上 |
| 可复制正文卫生（镜号/文件名/版本号/同上） | `lint.check_prompt_hygiene`、`check_prompt_length` | 同上 |
| 切镜必须带来变化（SHT-04） | `shot.purpose` 枚举 + `lint.check_purpose` 相邻同功能同景别 | 同上 + 镜头编辑器 |
| 机位三栏（高度是主动选择） | `lint.check_camera_columns` | 同上 |
| 分段时长双向算术（VID-04） | `lint.check_beat_coverage`（留白、未声明重叠） | 同上 |
| 连续性锁（锁面逐字进提示词、锁面核对） | `cast.<id>.locks[{surface, shots}]`，首帧/视频/两种方言都注入，`lint.check_locks` 词边界+否定上下文核对，角色页编辑 | DESIGN §19 |
| 文字政策（可读文字与全局无文字不共存，IMG-07） | `shot.readable_text[{surface, text, language, position}]` 替换 NO_TEXT 尾句、视频保持句、方言字幕句改口、lint | DESIGN §20 |
| 身份 vs 版本（换装/受伤/湿污是同一身份的变体，表情姿势是一镜的事） | `cast.<id>.variants{label, delta, asset, shots}` + `shot.cast: ["lin@rain"]`，首帧 block + delta、变体锚图优先，`split_cast_ref` 统一拆引用，`lint.check_variants` 查未用变体与相邻镜镜外换装 | DESIGN §23 |
| 导出交付包（manifest + checksums，排除凭据） | `09_export/<id>-<时间>/` + zip，成片页一键导出下载 | DESIGN §21 |
| 配乐归时间线层（VID-14） | `post_graphics.music` 本地音频铺进后期版，说话时段压低，按成片截断 | DESIGN §25 |
| Problem card 四要素 | 检查告警解析成 `{scope, target, kind, impact, fix}`，概览页按镜/角色分组、可点跳转 | `webui/problems.py` |
| 处置五分法（保留/后期/局部编辑/重新提交/改写，先定处置再谈修订；不用加形容词回应重复缺陷）+ 重投上限（同一输入原样重投不超过两次） | 裁决带 `defect`/`disposition` 与历史（`qc.item_meta`），`RunStore.attempts_for` 按 (run, step) 数同一份请求，review 接口第 3 次原样重提 409（`force` 放行、`edit` 不拦），重拍弹窗按处置路由（保留=通过、后期=跳过、改文字只记不重出、换模型去对比区），列表/失败卡「第 N 次」 | `storyvideo/qc.py`、`genkit/store.py`、`webui/routers/episodes.py`，DESIGN §24 |
| 起止边界六栏（start/end_boundary）+ "哪些与上一镜相同" | `shot.entry/exit{positions, facing, gaze, hands, held_props, visible_state, same_as_previous}` + `boundary_change`，编译成「入口状态：位置——…；与上一镜相同：位置、朝向。」进首帧/视频/两种方言；`continuity.check_continuity` 逐字段比对（镜外换手硬告警、位置/朝向跳变软告警、同上/不变、相同勾选无参照）；自由文本 `entry_state/exit_state` 老项目零变化 | `storyvideo/prompts.py`、`storyvideo/continuity.py`，DESIGN §22 |
| 题材卡（G25：12 张题材卡的压力来源 / 场面颗粒 / 集尾钩子 / 前中后期 / 禁止漂移） | `skills/ai-short-film/scripts/genre_cards.json`（原卡的节选与压缩，多数字段摘录原句，MIT，声明见 [third-party-notices.md](third-party-notices.md)）；`scriptgen.build_system_prompt(genre=)` 追加「题材卡」段 + 固定不漂移指令（不给时逐字节不变，卡不进交付物）；`GET /api/scripts/genres`，新建项目页「题材（可选）」下拉 | `storyvideo/scriptgen.py`，DESIGN §26 |
| 交付面遮挡区（平台状态栏 / 右侧互动栏 / 底部文案区压住画面，可读文字与字幕要避开） | `delivery.overlay_preset`（抖音/快手/视频号/无）或 `delivery.overlay_regions[{label,x,y,w,h}]` 比例矩形，`overlays.regions_for` 显式优先于预设；`lint.check_overlays` 把可读文字 `position` 的方位词粗略映射成矩形、字幕块按样式算位置，落进遮挡区就点名；首帧预览叠斜纹遮罩（可开关），后期页「交付面遮挡区」卡；老项目零变化，制作中当后期块 amend | `storyvideo/overlays.py`，DESIGN §27 |
| 轴线记法（两锚点连线 / working side / 屏幕方向）、裸左右 = 画面左右、正反打互补 | `shot.axis{anchors, side, screen, break}` 编译成轴线句；`lint.check_axis` 相邻镜画面侧翻转或机位侧换边且无 `break` → 越轴告警，正文裸写方向短语 → 提示 | `storyvideo/lint.py`，DESIGN §22 |
| 观众可见性 / 揭示保护（G20：每个未揭示的事实点名它是什么、靠什么泄露、到哪一镜才能露） | `shot.withhold[{fact, carrier, until}]`；声明镜到揭示点之前的首帧/视频提示词（三种方言）多一句「不得揭示：凶手是陈默——脸全程不入画或被遮挡、看不清。…」；`lint.check_withhold` 查揭示点之前正文（美术/动作/台词/beats）泄露事实或它有辨识度的词、until 不存在或不在本镜之后、泄露载体又被声明成可读文字；镜头页「先不揭示」列表；问题卡分类「揭示」；没声明的镜零变化。**排除句只是提示词文本，不是保证**——模型会不会真把脸藏住要拿 key 跑片核对，还没验证 | `storyvideo/prompts.py`、`storyvideo/lint.py`，DESIGN §28 |
| 参考槽位用途（G5：每张参考图说清取什么 / 控制什么 / 不得控制什么）+ 尾帧续接（这一条不来自 drama-skills） | `shot.reference_roles[{target, use, control, ignore}]`，target 只能落在本镜真的会带的参考图上（`prev_frame` / `tail` / `cast:<id>[@变体]` / `prop`），按 `frame_reference_asset_ids` 的真实顺序编号成「参考图1（上一镜 S1 首帧）：只取场景与光线；不得控制姿势、机位。」追加进首帧提示词（英文同义）；`shot.ref_tail{shot, mode}`：源镜视频过检后 ffmpeg `-sseof -0.15` 抽尾帧到 `04_tail/`，`reference` 占首帧请求第一个参考槽、`direct` 直接当首帧（不花钱、账本记 `tail:<源镜>`），源镜没过检的镜整轮推迟并给原因，源镜视频重拍逐层传染；`lint.check_reference_roles` / `check_tail_relay`（大全景尾帧接近景时提示加桥接帧）；镜头页「尾帧续接」「参考图用途」。**仍需 key 验证真实模型行为**：说明句能不能真让模型「只取场景不取姿势」、尾帧作参考/直接当首帧的身份保持效果 | `storyvideo/tail.py`、`storyvideo/prompts.py`、`storyvideo/runtime.py`，DESIGN §30 |
| 音色对比角色（G23：拿角色自己的台词并排试听候选音色，再定绑谁） | `POST .../cast/<id>/voice-compare` 同一句台词 2–6 个音色并行合成（走音色库试听同一条路，一个失败不拖累整批），文件 `09_voice_compare/<角色>/` 永不覆盖；角色页「对比试听」面板，「用这个」走现有绑定接口 | `webui/routers/voices.py`，DESIGN §29 |
| 形态卡默认值（G19：形态决定运动预算与必带项；它的卡按制作形态分，运动预算是"哪些全动、哪些 hold / 局部循环 / 剪辑承接"的分配） | `spec.form` + `skills/ai-short-film/scripts/form_cards.json` 五张按交付形态分的卡（竖屏短剧 / 横屏短片 / 竖屏动画 / 口播 / 品牌情景，按它的判据加我们自己的字幕 / 安全区经验推出，不是原文蒸馏，只有个别句子沿用原文）；`forms.apply_form_defaults` 只补缺省的画幅 / 分辨率 / 字幕块；运动预算句作 `form_budget` 部件进三种方言的视频提示词（紧跟揭示句）；起草提示词追加「形态卡」段；`lint.check_form` 查画幅方向、首镜钩子窗口、单镜时长区间与每分钟镜数、字幕显式关掉、竖屏远景后两镜内不回近景；`GET /api/scripts/forms`，新建项目页「形态（可选）」下拉，概览页形态标签与问题卡分类「形态」；没写 form 零变化。**运动预算句下模型的真实表现没有 key 验证** | `storyvideo/forms.py`、`storyvideo/lint.py`，DESIGN §31 |
| 剧本层 + 单集契约 + 系列交接（G17/G18：先有剧本再拆镜；写节拍前先答进入状态 / 钩子 / 集尾 / 当集兑现 / 交接；集间交接写成事实，跨集记忆只存后续决策所需） | 两步起草：`generate_screenplay` → `<id>.screenplay.json`（场次 / 节拍 / 对白 / 契约 / 人物表），人改过后 `generate_draft(screenplay=)` 按剧本拆镜（不改剧情、不加角色）；一步起草仍是默认、提示词不变。`spec.episode_contract{premise, hook, cliffhanger, must_pay_off, carry_over}` 不进提示词，`lint.check_contract` 核对钩子在前两镜、集尾在最后一镜、承诺在某一镜（判据「正文里提到了吗」：原句 / 有辨识度的词 / 片段命中比例）；问题卡「契约」；概览页契约卡走 globals。`spec.series{id, episode_no}` + `content/series/<id>.json` 记忆（契约、角色 block 原文、已兑现 = lint 找得到的、未决 = carry_over），`/series/handoff` 与导出时写入，起草下一集注入「上一集交接」并用记忆覆盖同 id 角色的 block。**没有 key：两步起草的真实质量、交接注入后身份是否真不漂，都没验证** | `storyvideo/contract.py`、`storyvideo/series.py`、`storyvideo/scriptgen.py`、`webui/routers/series.py`，DESIGN §32 |

## 待融合（按价值/工作量排序）

（2026-09-08：对照清单里的条目都已融合；剩下的是各条「未验证」项，要拿 key 跑片核对。）

## 不照搬的

- 它没有账本、成本闸、媒体 QC、修订传染——这些是我们的核心，不动。
- 它拒绝内置语速/切镜配额/形容词禁词表；我们的 4.5 字/秒、`EMOTION_WORDS` 是本仓库的经验默认值，保留为可覆盖默认。
- 它"一页、不显示严重度、不显示路径"的产品原则只用于默认视图；高级页保留 JSON/账本。
- 参考图的"控制/不得控制"最终只是提示词里的说明文字，不是一致性保证——不宣传成保证。
