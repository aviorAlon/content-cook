"""后端文案的英文规则表（纯数据，由 i18n.tr 使用）。

EXACT   ：中文原文 → 英文（精确匹配）。
PATTERNS：(中文正则, 英文替换)，替换里用 \\1 \\2 引用捕获组；按顺序第一条命中即用，写具体的放前面。

两处小型展开（仍是数据，只是省得手抄几百行）：
* 能力检查里的模型标签有三种形状（``model`` / ``model（按文档，未实测）`` / ``model（按服务商目录）``），
  ``_labeled`` 把一条模板展开成三条，标签永远是第 1 组。
* 路由层把 ``humanize()`` 的人话套在「生成失败：」这类前缀后面；``tr`` 只做一次匹配、不递归翻里层，
  所以 ``_composed`` 把「前缀 × 人话」预先展开成完整句子的规则，里外一起翻；前缀单独的兜底放最后。

``tr`` 对「人话（原文）」形状只翻最后一个全角括号前的部分：以固定括号结尾、又希望括号也翻的句子
（如「…（旁白与插卡页）」）要写成含括号、以 $ 收尾的整句规则，并且不要再写一条只匹配前半段的规则。
"""

from __future__ import annotations

import re
from itertools import combinations

# ---------------------------------------------------------------------------- 精确表

EXACT: dict[str, str] = {
    "请求参数不对": "Invalid request parameters",
    "请求参数不对——": "Invalid request — ",   # app.validation_message 的前缀，后面逐段拼「字段 X：<毛病>」
    # validation_message 在 pydantic 指着整个请求体（没带 body / body 不是对象，loc 只有 "body"）时的字段名；
    # 「字段 (\S+)：…」规则捕获到它后按组再翻一遍——不能在 validation_message 里直接换成 "request body"，带空格 \S+ 就不认了
    "请求体": "request body",
    # ---- project.py：下一步（label）
    "开始制作": "Start production",
    "处理失败的任务": "Handle failed jobs",
    "等尾帧": "Wait for tail frames",
    # ---- episodes.py：参考槽位用途 / 尾帧续接编辑接口（DESIGN §30）
    "ref_tail 要是镜号，或 {shot, mode} 对象": "ref_tail must be a shot id, or a {shot, mode} object",
    "ref_tail.shot 要是镜号": "ref_tail.shot must be a shot id",
    "ref_tail.mode 只能是 reference（作参考图）/ direct（直接当首帧）":
        "ref_tail.mode must be reference (use as reference image) / direct (use as the first frame)",
    "生成首帧": "Generate first frames",
    "审首帧": "Review first frames",
    "生成视频": "Generate videos",
    "审视频": "Review videos",
    "生成后期合成版": "Generate post-production cut",
    "生成成片": "Generate final cut",
    "审成片": "Review final cut",
    "重新合成成片": "Reassemble final cut",
    "已完成": "Done",
    # ---- routers/picturebook.py：下一步（label，DESIGN §39）
    "起草分页表": "Draft the page table",
    "处理文本问题": "Fix text problems",
    "生成插画": "Generate illustrations",
    "生成朗读": "Generate narration",
    "装配成片": "Assemble the book video",
    # ---- project.py：下一步（hint）
    "确认剧本、角色、镜头之后开始制作；之后每一笔生成都有账本":
        "Confirm the script, characters and shots, then start production; every generation after that is recorded in the ledger",
    "旁白、插卡、画面文字会在这一步合进去": "Narration, insert cards and on-screen text are composited in this step",
    "本地拼接，不花钱": "Assembled locally; costs nothing",
    "看一遍成片，通过或重拍": "Watch the final cut, then pass or retake",
    "成片被记了重拍——改完相关镜次后重新合成": "The final cut was marked for retake — fix the related shots, then reassemble",
    "成片已通过，可以导出": "The final cut passed; ready to export",
    # ---- project.py：角色 / 场景 / 锚图
    "（音色库里已删除）": "(deleted from voice library)",
    "block（外形/场景描述）不能为空——它是进首帧提示词的正文":
        "block (appearance / scene description) cannot be empty — it is the body of the first-frame prompt",
    "id 只能用英文、数字、下划线、点、短横线，且以英文/数字/下划线开头":
        "id may only contain letters, digits, underscores, dots and hyphens, and must start with a letter, digit or underscore",
    "项目已开始制作，不能新增角色/场景——复制成新版本再加":
        "Production has started; characters/scenes cannot be added — copy to a new version first",
    "项目已开始制作，不能删除角色/场景——复制成新版本再改":
        "Production has started; characters/scenes cannot be deleted — copy to a new version first",
    "图片为空": "Image is empty",
    "这个角色没有锚图": "This character has no anchor image",
    "没有失败的任务可以重提": "No failed jobs to resubmit",
    "新 id 不能为空": "The new id cannot be empty",
    "模型能力检查没通过，开始制作前先改参数（或强制开始）":
        "Model capability check failed; fix the parameters before starting production (or force start)",
    "计划已过期（磁盘状态在你看到预估之后变了）——重新拉一次预估再确认，不会因为这次拒绝而多花钱，什么都还没提交。":
        "The plan has expired (disk state changed after you saw the estimate) — refresh the estimate and confirm again; "
        "this rejection costs nothing, nothing was submitted.",
    # ---- episodes.py
    "这一集用的是旧式 speaker/en/zh 平铺字段（不是现代的 lines 数组）。这个工具只知道怎么写现代格式，"
    "写回去会在旧字段边上留一个不完整的 lines，把文件搞乱——所以锁死只读，改台词请直接编辑源 JSON。":
        "This episode uses the legacy flat speaker/en/zh fields (not the modern lines array). This tool only knows how to "
        "write the modern format; writing back would leave a partial lines array next to the old fields and corrupt the file — "
        "so it is locked read-only. Edit the source JSON directly to change lines.",
    "项目已开始制作，镜头的画面、动作、台词、运镜会进提示词，不能原地改——点「复制为新版本」在新版本里改（已生成的产物不受影响）。":
        "Production has started; a shot's visuals, motion, lines and camera moves go into the prompts and cannot be edited in place — "
        "click \"Copy as new version\" and edit there (generated outputs are unaffected).",
    "这一镜是旧式 speaker/en/zh 平铺字段，这个工具不知道怎么安全写回这种格式，拒绝编辑——改台词请直接编辑源 JSON 文件。":
        "This shot uses the legacy flat speaker/en/zh fields; this tool cannot safely write that format back, so editing is refused — "
        "edit the source JSON file directly to change lines.",
    "至少要保留一镜": "At least one shot must remain",
    "order 必须恰好包含现有的每一镜各一次": "order must contain each existing shot exactly once",
    # ---- overlays.py：交付面遮挡区预设与区域标签（DESIGN §27）
    "无（不预留）": "None (no reserved areas)",
    "抖音（竖屏）": "Douyin (portrait)",
    "快手（竖屏）": "Kuaishou (portrait)",
    "视频号（竖屏）": "Channels (portrait)",
    "顶部状态与标题栏": "Top status & title bar",
    "右侧互动栏": "Right-side action column",
    "底部文案与进度条": "Bottom caption & progress bar",
    "顶部导航栏": "Top navigation bar",
    "底部文案与评论框": "Bottom caption & comment box",
    "顶部标题栏": "Top title bar",
    "底部文案区": "Bottom caption area",
    "底部互动栏": "Bottom action bar",
    "遮挡区": "Overlay area",
    "delivery.overlay_regions 必须是数组：[{label, x, y, w, h}]，比例 0–1":
        "delivery.overlay_regions must be an array: [{label, x, y, w, h}], fractions 0–1",
    # ---- post.py
    "voice_over 必须是对象": "voice_over must be an object",
    "voice_over.lines 必须是数组": "voice_over.lines must be an array",
    "post_graphics 必须是对象": "post_graphics must be an object",
    "post_graphics.phone_ui_inserts 必须是数组": "post_graphics.phone_ui_inserts must be an array",
    "post_graphics.dubbing 必须是对象": "post_graphics.dubbing must be an object",
    "dubbing.duck 必须是 0 到 1 之间的数（配音时原声保留的比例）":
        "dubbing.duck must be a number between 0 and 1 (how much of the original audio to keep under dubbing)",
    "post_graphics.notebook_text 必须是数组": "post_graphics.notebook_text must be an array",
    "没有需要改的镜：要么没开配音，要么配音镜已经是画外说话方式":
        "No shots need changing: dubbing is off, or the dubbed shots already use off-screen speech",
    "项目已开始制作，说话方式会改视频提示词——用「保存并预告费用」在新版本里改（前端会自动走这条路）":
        "Production has started and the speech mode changes the video prompt — use \"Save and preview cost\" to change it "
        "in a new version (the UI does this automatically)",
    "台词识别闸需要 faster-whisper：在运行本程序的 Python 里执行 pip install faster-whisper，首次运行会下载识别模型（small 约 500MB）。":
        "The dialogue ASR gate needs faster-whisper: run pip install faster-whisper in the Python that runs this app; "
        "the first run downloads the recognition model (small is about 500MB).",
    "项目还没锁定，无法记录重摇裁决；先锁定，或者关掉「自动记重摇」只看报告。":
        "The project is not locked yet, so retake decisions cannot be recorded; lock it first, or turn off \"Auto-record retakes\" "
        "and just view the report.",
    "后期合成需要 ffmpeg / ffprobe 在 PATH 里（界面顶部有下载提示）。":
        "Post-production compositing needs ffmpeg / ffprobe on PATH (see the download hint at the top of the UI).",
    "（未指定说话人）": "(no speaker specified)",
    # ---- assist.py（打包版的 install_hint 带目录 / 版本号，是 PATTERNS 里的模板）
    "已经装好了": "Already installed",
    "先安装 faster-whisper": "Install faster-whisper first",
    "清单不是 {version, url, notes} 形状的 JSON": "Manifest is not JSON of the shape {version, url, notes}",
    # ---- podcast/spec.py：形式说明（/api/podcast/formats 的 note，新建页下拉旁的一句话）
    "一个人讲：像给朋友讲事，不是念稿": "One voice: tell it like you would to a friend, not read from a script",
    "两个人聊：要有分歧、打断和接话": "Two people talking: there should be disagreement, interruptions and follow-ups",
    "主播问、嘉宾答：问题要追问，回答要有细节": "Host asks, guest answers: questions should probe, answers should have detail",
    "三四个人：每个人立场不同，别让一个人独大": "Three or four people: each with a different stance; do not let one person dominate",
    # ---- imageset/templates.py：模板选项的 hint（/api/{sticker,portrait,image}/templates 的 options_schema，新建页选项旁的一句话）。
    #      表情 / 场景清单本身是可改的用户内容（也是叠字原文和描写表的 key），不翻；这里说清「清单外的按原文出图」
    "可增删改；清单里没有的表情按你写的原文出图":
        "Add, remove or rename freely; an expression not in the built-in list is drawn from exactly what you write",
    "本机叠字（表情名），不让模型画字": "The expression name is overlaid locally; the model does not draw text",
    "可增删改；改了名字就按你写的原文出图": "Add, remove or rename freely; a renamed entry is drawn from exactly what you write",
    "本机叠字，不让模型画；用「|」分隔祝语与日期，留空不叠":
        'Text is overlaid locally, not drawn by the model; separate the wish and the date with "|"; leave empty for no overlay',
    "写具体的材料与颜色，别写「精美 / 高质量」；留空就用标题":
        'Name concrete materials and colors, not "beautiful / high quality"; leave empty to use the title',
    "逗号分隔；文字 / 水印这些已经默认加了": "Comma-separated; text / watermark are already included by default",
    "所有模型用同一句，差别才只来自模型": "Every model gets the same sentence, so differences come only from the model",
    "2–6 个模型 id；@image = 设置里配好的生图模型": "2–6 model ids; @image = the image model configured in Settings",
    "留空就用项目标题；模型不画字": "Leave empty to use the project title; the model does not draw text",
    "超过 24 字会截断": "Truncated beyond 24 characters",
    "自动 = 挑细节少的一端": "Auto = the end with less detail",
    # ---- 静态扫描（tests/test_webui_i18n.py::test_every_raise_literal_translates）补出来的固定句：
    #      clip / revise / compare / sdwebui / model_catalog / picturebook 的 raise 文案
    "也可以写 custom 自写风格锚": "or write custom and set your own style anchor",
    "beats 要是数组": "beats must be an array",
    "这个项目还没开始制作，直接保存即可，不需要开新版本": "This project has not started production yet — just save; no new version is needed",
    "新版本的 id 不能为空": "The new version's id cannot be empty",
    "项目还没开始制作，没有首帧账本可采用——先「开始制作」":
        "Production has not started, so there is no first-frame ledger to adopt into — click \"Start production\" first",
    "sd webui 返回了空图片": "sd webui returned an empty image",
    "先填网关地址": "Enter the gateway address first",
    "UI font unavailable: 没找到可用的中文字体，在字幕设置里指定 font_path":
        "UI font unavailable: no usable Chinese font found; set font_path in the subtitle settings",
    "没找到可用的中文字体，在字幕设置里指定 font_path": "no usable Chinese font found; set font_path in the subtitle settings",
    "未配置": "not configured",
    # ---- voices.py / genkit.voices
    "还没有试听样本，先点「试听」": "No preview sample yet — click \"Preview\" first",
    "traits 必须是对象": "traits must be an object",
    "traits.timbre 必须是数组或用逗号分隔的字符串": "traits.timbre must be an array or a comma-separated string",
    "tags 必须是数组": "tags must be an array",
    "description_mode 只能是 traits / custom": "description_mode must be traits / custom",
    "name 不能为空": "name cannot be empty",
    "至少要有一个 TTS 音色（voice）或一段声音描述（description）":
        "Provide at least a TTS voice (voice) or a voice description (description)",
    "id 只能用英文、数字、下划线、点、短横线": "id may only contain letters, digits, underscores, dots and hyphens",
    "这个音色只有文字描述，没有 TTS 音色，无法试听——它只用于视频模型直出台词的提示词":
        "This voice has only a text description and no TTS voice, so it cannot be previewed — it is only used in prompts "
        "for video models that speak the lines natively",
    "这个音色没有 TTS 音色，不能用于旁白": "This voice has no TTS voice and cannot be used for narration",
    # ---- voices.py / genkit.voices：音色克隆与参考音频（DESIGN §33）
    "没有配置 MiniMax 密钥——在「模型设置」把语音来源选 MiniMax 语音接口（或视频来源选 MiniMax）并填 key":
        "No MiniMax key configured — in Model settings pick the MiniMax speech API as the voice source (or MiniMax as the video source) and enter the key",
    "这个音色还没有参考音频——先上传一段 10 秒到 5 分钟的清晰人声":
        "This voice has no reference audio yet — upload 10 seconds to 5 minutes of clear speech first",
    "这个音色还没有克隆成功，也没填 MiniMax 音色 id": "This voice has not been cloned yet and has no MiniMax voice id",
    "这个音色正在克隆，等它结束": "This voice is being cloned — wait for it to finish",
    "这个音色还没有参考音频": "This voice has no reference audio yet",
    "MiniMax 系统音色（按文档列的一部分）；克隆出来的 id 在档案的「参考音频」里":
        "MiniMax system voices (a subset from the docs); cloned ids live under the profile's \"Reference audio\"",
    "MiniMax 系统音色（按文档列的一部分，未真实核对）；上传参考音频并克隆后，档案自动用克隆出来的 id":
        "MiniMax system voices (a subset from the docs, not verified live); after uploading reference audio and cloning, the profile uses the cloned id automatically",
    # ---- voices.py：音色对比角色（DESIGN §29）
    "这个角色没有台词，填一句试听文字": "This character has no lines — enter a line to audition",
    "没有这个文件": "No such file",
    # ---- compare.py
    "至少给一个模型 id": "Give at least one model id",
    "候选不存在": "Candidate does not exist",
    # ---- poster.py / storyvideo.poster：海报（DESIGN §36）
    "海报标题不能为空": "The poster title cannot be empty",
    "source 只能是 model（用模型生成关键画面）/ frame（用某一镜首帧）":
        "source must be model (generate a key visual with the model) / frame (use a shot's first frame)",
    "没找到 ffmpeg，海报要用它叠字——把 ffmpeg 放进 PATH 再试":
        "ffmpeg not found; the poster needs it to overlay the title — put ffmpeg on PATH and try again",
    "没找到可用的中文字体——在字幕样式里指定字体文件，或把字体装进系统":
        "No usable CJK font found — set a font file in the subtitle style, or install one on the system",
    "还没有海报底图，先生成一张（用模型或用某一镜首帧）":
        "No poster base image yet — generate one first (with the model, or from a shot's first frame)",
    "还没有海报——成片页可以生成一张，交付包会带上":
        "No poster yet — the final page can generate one, and the delivery package will include it",
    "生图来源没有返回结果": "The image source returned no result",
    # ---- scripts.py
    "项目 id 不能为空": "Project id cannot be empty",
    # ---- capabilities.py
    "在「模型设置」里填好再生成": "fill it in under Model settings, then generate",
    "生图模型还没配置——去「模型设置」": "No image model configured — go to Model settings",
    "视频模型还没配置——去「模型设置」": "No video model configured — go to Model settings",
    "（按文档，未实测）": "(per docs, untested)",
    "（按服务商目录）": "(per provider catalog)",
    "（未填模型）": "(no model set)",
    # ---- humanize.py：失败原因分类
    "余额或配额不足": "Insufficient balance or quota",
    "内容被审核拦截": "Blocked by content moderation",
    "网络超时或服务不可用": "Network timeout or service unavailable",
    "服务商报错": "Provider error",
    "服务商账户充值/等配额恢复后，点「重提失败任务」":
        "Top up the provider account or wait for the quota to recover, then click \"Resubmit failed jobs\"",
    "原样重提大概率还会被拦——改写这一镜的画面/台词描述后再重拍":
        "Resubmitting as-is will most likely be blocked again — rewrite this shot's visual/dialogue description, then retake",
    "多半是临时的，直接点「重提失败任务」": "Usually transient — just click \"Resubmit failed jobs\"",
    "看原文；参数越界的先改 spec，偶发错误直接重提":
        "Check the original message; fix the spec if a parameter is out of range, otherwise just resubmit",
    # ---- humanize.py：无变量的人话（也作为「前缀 × 人话」展开的里层）
    "先把首帧审过（通过或跳过）再生成视频": "Get the first frames through review (pass or waive) before generating videos",
    "先把所有视频审过再合成成片": "Review all videos before assembling the final cut",
    "首帧和视频都审过之后才能合成成片": "The final cut can only be assembled after both first frames and videos have been reviewed",
    "开始制作之后项目内容被改过，与冻结时不一致——开一个新版本，或者把改动撤回":
        "The project was edited after production started and no longer matches the frozen snapshot — create a new version or revert the change",
    "冻结快照文件被外部改过，账本对不上——不要手动编辑 manifests 目录":
        "The frozen snapshot was modified externally and no longer matches the ledger — do not hand-edit the manifests directory",
    "这一道审片已经有别的镜在等重拍，先处理完它们":
        "Other shots in this review gate are already waiting for a retake — deal with them first",
    "产物文件变了，需要重新走一遍技术检查": "Output files changed; the technical check needs to run again",
    "技术检查还没跑完，稍后再审": "The technical check has not finished yet — review later",
    "这一道还没有可审的产物——先生成": "This gate has nothing to review yet — generate first",
    "这一道还没有审过的产物": "This gate has no reviewed outputs yet",
    "重拍需要写原因": "A retake requires a reason",
    "本地已有生成过的文件但账本里没登记——先点「导入已有产物」":
        "Generated files exist locally but are not in the ledger — click \"Import existing outputs\" first",
    "导入已有产物需要确认": "Importing existing outputs requires confirmation",
    "生成模型没配置——去「模型设置」": "No generation model configured — go to Model settings",
    "output_root 必须以项目 id 结尾": "output_root must end with the project id",
    # ---- config_validate.py
    "顶层必须是一个 JSON 对象": "Top level must be a JSON object",
    # ---- genkit.settings：适配器 label / hint
    "OpenAI 兼容对话接口（官方 openai SDK，/chat/completions）": "OpenAI-compatible chat API (official openai SDK, /chat/completions)",
    "OpenAI、OpenRouter（base_url 填 https://openrouter.ai/api/v1）、DeepSeek、Moonshot、通义、豆包等兼容网关都走这条":
        "OpenAI, OpenRouter (base_url https://openrouter.ai/api/v1), DeepSeek, Moonshot, Qwen, Doubao and other compatible gateways all use this",
    "OpenRouter 生图（官方 SDK，/api/v1/images，支持参考图）": "OpenRouter image generation (official SDK, /api/v1/images, supports reference images)",
    "一个 key 可同时配图/视频/语音；模型如 bytedance-seed/seedream-4.5、google/gemini-2.5-flash-image":
        "One key can serve image/video/voice; models such as bytedance-seed/seedream-4.5, google/gemini-2.5-flash-image",
    "OpenAI 兼容图片接口（/v1/images/generations · edits）": "OpenAI-compatible image API (/v1/images/generations · edits)",
    "gpt-image 系列或兼容网关；参考图走 /images/edits": "gpt-image series or a compatible gateway; reference images go through /images/edits",
    "火山方舟 Seedream（/api/v3/images/generations）": "Volcengine Ark Seedream (/api/v3/images/generations)",
    "doubao-seedream 系列；参考图以 base64 随请求发送": "doubao-seedream series; reference images are sent as base64 with the request",
    "OpenRouter 语音（官方 SDK，tts）": "OpenRouter speech (official SDK, tts)",
    "模型与音色以 OpenRouter 语音模型列表为准": "Models and voices follow the OpenRouter speech model list",
    "Edge TTS（免费，微软在线音色，edge-tts 库）": "Edge TTS (free, Microsoft online voices, edge-tts library)",
    "model 填音色名，如 zh-CN-YunxiNeural；rate/pitch 由剧集 spec 的 voice_over.edge 决定":
        "Put the voice name in model, e.g. zh-CN-YunxiNeural; rate/pitch come from voice_over.edge in the episode spec",
    "OpenAI 兼容语音接口（/v1/audio/speech）": "OpenAI-compatible speech API (/v1/audio/speech)",
    "tts-1 / gpt-4o-mini-tts 或兼容网关；voice 填音色 id": "tts-1 / gpt-4o-mini-tts or a compatible gateway; put the voice id in voice",
    "MiniMax 语音接口（/v1/t2a_v2，支持音色克隆）": "MiniMax speech API (/v1/t2a_v2, supports voice cloning)",
    "speech-02-hd / speech-02-turbo；voice 填系统音色 id（如 female-shaonv）或音色库里克隆出来的 id。按文档写，未真实调用":
        "speech-02-hd / speech-02-turbo; put a system voice id (e.g. female-shaonv) or an id cloned in the voice library in voice. Written from the docs, not called live",
    "OpenRouter 视频（官方 SDK，/api/v1/videos，支持首帧）": "OpenRouter video (official SDK, /api/v1/videos, supports first frame)",
    "模型如 minimax/hailuo-3、bytedance-seed/seedance-*、google/veo-3.1；异步轮询":
        "Models such as minimax/hailuo-3, bytedance-seed/seedance-*, google/veo-3.1; async polling",
    "火山方舟 Seedance（/api/v3/contents/generations/tasks）": "Volcengine Ark Seedance (/api/v3/contents/generations/tasks)",
    "doubao-seedance 系列；首帧以 base64 随请求发送，异步轮询":
        "doubao-seedance series; the first frame is sent as base64 with the request, async polling",
    "MiniMax 视频接口（/v1/video_generation）": "MiniMax video API (/v1/video_generation)",
    "MiniMax-Hailuo 系列；首帧以 data URL 发送，异步轮询后经 files/retrieve 取下载地址":
        "MiniMax-Hailuo series; the first frame is sent as a data URL, async polling then files/retrieve for the download URL",
    # ---- episodes.py：结构化入口/出口、轴线、可读文字（形状校验）
    "axis 要是对象（anchors / side / screen / break）": "axis must be an object (anchors / side / screen / break)",
    "axis.anchors 要是名字列表": "axis.anchors must be a list of names",
    "axis.anchors 要恰好两个不同的锚点（轴线是两个可命名锚点的连线）":
        "axis.anchors needs exactly two different anchors (the axis is the line between two nameable anchors)",
    "axis.side 只能是 left / right": "axis.side must be left / right",
    "axis.screen 要是 {名字: left/right} 对象": "axis.screen must be a {name: left/right} object",
    "axis.break 只能是 true / false": "axis.break must be true / false",
    # ---- project.py：变体 / 锁
    "variants 必须是对象：{变体键: {label, delta, asset, shots}}": "variants must be an object: {variant key: {label, delta, asset, shots}}",
    "变体键只能用英文、数字、下划线、点、短横线，且以英文/数字/下划线开头（不能有 @）":
        "Variant keys may only contain letters, digits, underscores, dots and hyphens, must start with a letter/digit/underscore, and cannot contain @",
    "locks 必须是列表": "locks must be a list",
    "这个变体没有锚图": "This variant has no anchor image",
    # ---- scripts.py / export.py
    "项目 id 只能用英文、数字、下划线、点、短横线": "Project id may only contain letters, digits, underscores, dots and hyphens",
    "这一分钟已经导出过一个交付包了，稍等一分钟再点，或者直接下载已有的那个。":
        "A delivery package was already exported this minute; wait a minute and try again, or download the existing one.",
    # ---- export.py：交付包的工具清单口径（manifest.json 的 ai_disclosure.basis 与 ai_disclosure.md 的尾注）
    "首帧 / 视频按账本逐 job 记录（每一镜提交时记下的来源与模型，不受导出时的设置影响；重提过的镜只记最近一次）；"
    "剧本 / 分镜起草、以及账本里没有记下来源的类别（没提交过，或提交时账本还是老版本），按导出时的设置汇总——以各行标注为准。":
        "First frames / videos are recorded per job from the ledger (the source and model noted when each shot was submitted, "
        "independent of the settings at export time; a resubmitted shot keeps only its latest submission); script / storyboard "
        "drafting, and any category whose source was never recorded in the ledger (never submitted, or submitted while the ledger "
        "was still the old version), are summarised from the settings at export time — see the note on each line.",
    "工具清单按导出时的设置汇总（账本里没有记录）：来源与模型来自导出时的模型设置与项目文件里的模型别名；生成之后改过设置的，以实际生成时的来源为准。":
        "The tool list is summarised from the settings at export time (the ledger has no record): sources and models come "
        "from the model settings at export time and the model aliases in the project file; if the settings changed after "
        "generation, the source actually used at generation time takes precedence.",
    "按账本": "from the ledger",
    "按导出时的设置": "from the settings at export time",
    # 声音与配乐几行的口径（manifest.json 的 ai_disclosure.basis_audio 与 ai_disclosure.md 的第二句尾注）
    "旁白、台词配音与配乐按导出时的项目文件记声线与曲目；后期层改过之后没重新做后期合成的，以成片为准。":
        "Narration, dialogue dubbing and music are recorded from the project file at export time (voices and tracks); "
        "if the post-production layers changed without a new post-production render, the finished cut takes precedence.",
    # ---- post.py：配乐
    "音频为空": "Audio is empty",
    "没有这个文件": "No such file",
    "先上传音频": "upload the audio first",
    "留空 = 到结尾": "leave empty = to the end",
    "配音时原声保留的比例": "how much of the original audio to keep under dubbing",
    # ---- imageset（DESIGN §40）：以全角括号收尾的校验句，tr 先翻括号前的部分，这里给括号里的补充说明
    "参考图路径": "reference image paths",
    # ---- 配乐（DESIGN §44）：同上，以全角括号收尾的校验句里括号中的补充说明
    "或用顿号分隔的一行": "or a single line separated by commas",
    "图片 id": "image ids",
    "自写风格锚": "write your own style anchor",
    # ---- lint.py / continuity.py：问题卡里单独出现的半句
    "越轴": "Axis crossed",
    # ---- post.py：配乐上传即验内容
    "这个文件里没有音频流（ffprobe 读不出音频）——换一个 mp3 / wav / m4a / aac / ogg / flac 音频文件":
        "This file has no audio stream (ffprobe cannot read audio) — use an mp3 / wav / m4a / aac / ogg / flac audio file",
    # ---- post.py 的后期块校验：同样以全角括号收尾的两条，这里给括号里的补充说明
    "旁白文本": "narration text",
    "画面文字": "on-screen text",
    # ---- 短剧项目状态机与文件边界（发第一个 tag 前的修复）：humanize 的人话（无变量）、括号里的固定补充、路由层整句
    "产物文件变了，需要重新走一遍技术检查——点「生成首帧 / 生成视频」会重新检查已有文件，不花钱":
        'Output files changed; the technical check needs to run again — "Generate first frames / Generate videos" rechecks the existing files, costs nothing',
    "成片不能直接记重拍——回镜头页重拍出问题的镜次，或记 edit_rescue / redesign":
        "The final cut cannot be marked for retake directly — retake the problem shots on the shots page, or record edit_rescue / redesign",
    "没有选中任何镜次": "No shots selected",
    "项目 id 只能用英文、数字、下划线、点、短横线，且首尾得是英文或数字":
        "Project id may only contain letters, digits, underscores, dots and hyphens, and must start and end with a letter or digit",
    "只有 frames / videos / final": "only frames / videos / final",
    "只能是 pass / retake / edit_rescue / redesign / waive": "must be pass / retake / edit_rescue / redesign / waive",
    "目前只有短剧项目能从界面恢复": "only drama projects can be restored from the UI for now",
    "项目已经开始制作，产物都记在账本里了——不用再导入；缺的镜直接点「生成」":
        'Production has started and the outputs are already in the ledger — no import needed; just click "Generate" for the missing shots',
    "没有可导入的产物：输出目录里没有首帧 / 视频 / 成片文件——直接「开始制作」即可":
        'Nothing to import: the output directory has no first-frame / video / final-cut files — just "Start production"',
    "asset 只能是项目内的相对路径（content/assets/…）": "asset must be a relative path inside the project (content/assets/…)",
    "锚图不在项目 content/ 目录下，不能读取——重新上传一张":
        "The anchor image is outside the project's content/ directory and cannot be read — upload it again",
    "同 id 的剧本文件已经存在": "a screenplay file with the same id already exists",
    "同 id 的锚图目录已经存在": "an anchor-image directory with the same id already exists",
    "同 id 的输出目录已经存在": "an output directory with the same id already exists",
    "账本里已经有同 id 的制作记录": "the ledger already has a production record with the same id",
    # 回收站（genkit.trash.TASKS_RUNNING）：各模块的删除接口在这个项目还有后台任务时 409
    "这个项目还有后台任务在跑，等它结束再删——不然任务会把文件接着写回原处；进度在「任务中心」里能看到":
        "This project still has a background task running — wait for it to finish before deleting, otherwise the task keeps writing files "
        "back to the original place; its progress is shown in the Task center",
    # deps.FREEZE_MISMATCH_HINTS：详情 warnings（带「全集：」头）、readonly_reason、概览 freeze.hint 三处都用；问题卡还会拆成两半
    "输出目录不在了，但账本里还有这个项目的制作记录——把目录搬回来（或从归档恢复），或者换一个 id 重建":
        "The output directory is gone, but the ledger still has this project's production record — move the directory back (or restore from the archive), or rebuild under a new id",
    "全集：输出目录不在了，但账本里还有这个项目的制作记录——把目录搬回来（或从归档恢复），或者换一个 id 重建":
        "Whole episode: the output directory is gone, but the ledger still has this project's production record — move the directory back (or restore from the archive), or rebuild under a new id",
    "输出目录不在了，但账本里还有这个项目的制作记录": "The output directory is gone, but the ledger still has this project's production record",
    "把目录搬回来（或从归档恢复），或者换一个 id 重建": "Move the directory back (or restore from the archive), or rebuild under a new id",
    "项目文件在开始制作之后被外部改过，跟冻结快照对不上——撤回改动，或复制成新版本再改":
        "The project file was edited externally after production started and no longer matches the frozen snapshot — revert the change, or copy to a new version and edit there",
    "全集：项目文件在开始制作之后被外部改过，跟冻结快照对不上——撤回改动，或复制成新版本再改":
        "Whole episode: the project file was edited externally after production started and no longer matches the frozen snapshot — revert the change, or copy to a new version and edit there",
    "项目文件在开始制作之后被外部改过，跟冻结快照对不上": "The project file was edited externally after production started and no longer matches the frozen snapshot",
    "撤回改动，或复制成新版本再改": "Revert the change, or copy to a new version and edit there",
    "冻结快照文件被外部改过，账本对不上——不要手动编辑 manifests 目录；改设计请复制成新版本":
        "The frozen snapshot file was edited externally and no longer matches the ledger — do not hand-edit the manifests directory; to change the design, copy to a new version",
    "全集：冻结快照文件被外部改过，账本对不上——不要手动编辑 manifests 目录；改设计请复制成新版本":
        "Whole episode: the frozen snapshot file was edited externally and no longer matches the ledger — do not hand-edit the manifests directory; to change the design, copy to a new version",
    "冻结快照文件被外部改过，账本对不上": "The frozen snapshot file was edited externally and no longer matches the ledger",
    "不要手动编辑 manifests 目录；改设计请复制成新版本": "Do not hand-edit the manifests directory; to change the design, copy to a new version",
    # 同上（审查后补）：账本行丢了（从归档手动搬回来）的冻结提示；概览 next_step 在冻结对不上时的两个 label
    "账本里没有这个项目的制作记录，产物文件却都在——点「导入已有产物」重新登记，不花钱；如果是从归档手动搬回来的，也可以在侧栏「已删除的项目」里恢复它的账本记录":
        'The ledger has no production record for this project, but its output files are all there — click "Import existing outputs" to register them again (costs nothing); '
        'if you moved it back from the archive by hand, you can also restore its ledger record under "Deleted projects" in the sidebar',
    "全集：账本里没有这个项目的制作记录，产物文件却都在——点「导入已有产物」重新登记，不花钱；如果是从归档手动搬回来的，也可以在侧栏「已删除的项目」里恢复它的账本记录":
        'Whole episode: the ledger has no production record for this project, but its output files are all there — click "Import existing outputs" to register them again (costs nothing); '
        'if you moved it back from the archive by hand, you can also restore its ledger record under "Deleted projects" in the sidebar',
    "账本里没有这个项目的制作记录，产物文件却都在": "The ledger has no production record for this project, but its output files are all there",
    "点「导入已有产物」重新登记，不花钱；如果是从归档手动搬回来的，也可以在侧栏「已删除的项目」里恢复它的账本记录":
        'Click "Import existing outputs" to register them again (costs nothing); '
        'if you moved it back from the archive by hand, you can also restore its ledger record under "Deleted projects" in the sidebar',
    "导入已有产物": "Import existing outputs",
    "处理版本冲突": "Resolve the version conflict",
    # ---- deps.py / genkit.settings：起任务前的「没配模型」拦截（以全角括号收尾，括号里是缺项：tr 先翻前半段，
    #      缺项走「(\w+): 没选适配器」那几条单独规则，字段名本身是 ASCII）
    "还没配置剧本文本模型——去「模型设置」给「剧本文本」选一个来源并填密钥":
        'No script text model configured yet — go to Model settings, pick a source for "Script text" and enter its key',
    "还没配置生图模型——去「模型设置」给「生图」选一个来源并填密钥":
        'No image model configured yet — go to Model settings, pick a source for "Image" and enter its key',
    "还没配置语音模型——去「模型设置」给「语音」选一个来源并填密钥":
        'No voice model configured yet — go to Model settings, pick a source for "Voice" and enter its key',
    "还没配置视频模型——去「模型设置」给「视频」选一个来源并填密钥":
        'No video model configured yet — go to Model settings, pick a source for "Video" and enter its key',
    "还没配置配乐模型——去「模型设置」给「配乐」选一个来源并填密钥":
        'No music model configured yet — go to Model settings, pick a source for "Music" and enter its key',
    # ---- humanize.py：失败原因分类（模型没配置 / 被服务商限流）
    "模型没配置": "Model not configured",
    "去「模型设置」把这个角色配好，再点「重提失败任务」": 'Configure this role in Model settings, then click "Resubmit failed jobs"',
    "被服务商限流": "Rate-limited by the provider",
    "等几十秒后点「重提失败任务」": 'Wait a few dozen seconds, then click "Resubmit failed jobs"',
}


# ---------------------------------------------------------------------------- 模板规则

# 能力检查的模型标签：三种形状，标签始终是第 1 组
_LABEL_SHAPES: list[tuple[str, str]] = [
    (r"(\S+?)（按文档，未实测）", r"\1 (per docs, untested)"),
    (r"(\S+?)（按服务商目录）", r"\1 (per provider catalog)"),
    (r"(\S+?)", r"\1"),
]


def _labeled(pattern_tail: str, replacement_tail: str) -> list[tuple[str, str]]:
    return [(f"^{shape} {pattern_tail}", f"{rep} {replacement_tail}") for shape, rep in _LABEL_SHAPES]


# humanize() 输出的人话（中文正则 → 英文），带变量的写在这里；无变量的从 EXACT 里取
_HUMANIZED: list[tuple[str, str]] = [
    (r"开始制作之后改到了会影响生成的字段（(.+?)）——这种改动要开一个新版本",
     r"Generation-affecting fields (\1) were changed after production started — this kind of change needs a new version"),
    (r"另一个进程正在操作这个项目（(.+?)）——等它结束，或者关掉多开的窗口",
     r"Another process is working on this project (\1) — wait for it to finish, or close the extra windows"),
    (r"(\S+) 现在处于「(\S+?)」状态，还不能重拍——等它生成完或失败后再重拍",
     r'\1 is currently in state "\2" and cannot be retaken yet — wait until it finishes or fails'),
    (r"(\S+) 还在生成中，不能重拍——等它结束", r"\1 is still generating and cannot be retaken — wait for it to finish"),
    (r"上一次重拍还没完成（(.+?)）——先重新生成它们", r"The previous retake is not finished yet (\1) — regenerate those first"),
    (r"还有待重拍的镜次（(.+?)），先重新生成它们再审别的",
     r"Some shots are still pending retake (\1) — regenerate them before reviewing others"),
    (r"(\S+) 的文件没通过技术检查（分辨率/时长/文件有效性），先重新生成",
     r"The file for \1 failed the technical check (resolution / duration / file validity) — regenerate it"),
    (r"这些镜次不在待审列表里：(.+)", r"These shots are not in the pending-review list: \1"),
    (r"没有这些镜次：(.+)", r"No such shots: \1"),
    (r"「(\S+?)」需要写原因", r'"\1" requires a reason'),
    (r"只有明确失败的任务才能重提：(.+)", r"Only definitively failed jobs can be resubmitted: \1"),
    # 被拒绝 + 分类建议（humanize 在后面接了「。建议」）
    (r"(\S+) 被服务商拒绝：(.+?)。服务商账户充值/等配额恢复后，点「重提失败任务」",
     r'\1 was rejected by the provider: \2. Top up the provider account or wait for the quota to recover, then click "Resubmit failed jobs"'),
    (r"(\S+) 被服务商拒绝：(.+?)。原样重提大概率还会被拦——改写这一镜的画面/台词描述后再重拍",
     r"\1 was rejected by the provider: \2. Resubmitting as-is will most likely be blocked again — rewrite this shot's visual/dialogue description, then retake"),
    (r"(\S+) 被服务商拒绝：(.+?)。多半是临时的，直接点「重提失败任务」",
     r'\1 was rejected by the provider: \2. Usually transient — just click "Resubmit failed jobs"'),
    (r"(\S+) 被服务商拒绝：(.+?)。看原文；参数越界的先改 spec，偶发错误直接重提",
     r"\1 was rejected by the provider: \2. Check the original message; fix the spec if a parameter is out of range, otherwise just resubmit"),
    (r"(\S+) 被服务商拒绝：(.+?)。等几十秒后点「重提失败任务」",
     r'\1 was rejected by the provider: \2. Wait a few dozen seconds, then click "Resubmit failed jobs"'),
    (r"(\S+) 被服务商拒绝：(.+?)。去「模型设置」把这个角色配好，再点「重提失败任务」",
     r'\1 was rejected by the provider: \2. Configure this role in Model settings, then click "Resubmit failed jobs"'),
    (r"(\S+) 被服务商拒绝：(.+)", r"\1 was rejected by the provider: \2"),
    (r"(\S+) 提交后没拿到结果（(.+?)）——不确定服务商是否收到，不会自动重提；去服务商控制台对账后再重提",
     r"\1 got no result after submission (\2) — unclear whether the provider received it, so it will not be resubmitted automatically; "
     r"reconcile in the provider console, then resubmit"),
    # 大首帧的上传代理图压不进上限（runtime._upload_ready_image）：里层是中文，要排在下面那条通用规则前面
    (r"(\S+) 的参考图没能编码/上传：上传用的代理图压到最低画质仍有 (\d+) 字节，超过 (\d+) 字节的上传上限：(.+?)——检查这张图的尺寸是否异常",
     r"The reference image for \1 could not be encoded/uploaded: even at the lowest quality the upload proxy is still \2 bytes, "
     r"over the \3-byte upload limit: \4 — check whether this image has an unusual size"),
    (r"(\S+) 的参考图没能编码/上传：(.+)", r"The reference image for \1 could not be encoded/uploaded: \2"),
    (r"(\S+) 的结果下载或校验失败：(.+)", r"Downloading or validating the result for \1 failed: \2"),
    (r"查询 (\S+) 的进度失败：(.+)", r"Polling progress for \1 failed: \2"),
    (r"(\S+) 提交后没拿到结果，不确定服务商是否收到——在账本里对账后再重提",
     r"\1 got no result after submission — unclear whether the provider received it; reconcile in the ledger, then resubmit"),
    (r"(\S+) 还没有可用的首帧，先生成首帧", r"\1 has no usable first frame yet — generate the first frame first"),
    (r"缺少视频文件：(.+)", r"Missing video file: \1"),
    (r"成片拼接失败：(.+)", r"Final cut assembly failed: \1"),
    (r"成片拼接前检查失败：(.+)", r"Final cut preflight check failed: \1"),
    (r"本地 ffmpeg 命令失败：(.+)", r"Local ffmpeg command failed: \1"),
    (r"插卡需要的中文字体不可用：(.+)", r"The Chinese font needed for insert cards is unavailable: \1"),
    # genkit.router：「{role} 模型未配置：{缺项}（在「模型设置」里填好再生成）」——整句含括号，以 $ 收尾
    (r"(\w+) 模型未配置：(\w+): 没选适配器（在「模型设置」里填好再生成）",
     r"\1 model not configured: \2: no adapter selected (fill it in under Model settings, then generate)"),
    (r"(\w+) 模型未配置：(\w+): 未知适配器 (\S+?)（在「模型设置」里填好再生成）",
     r"\1 model not configured: \2: unknown adapter \3 (fill it in under Model settings, then generate)"),
    # genkit.router 的「{role} 模型未配置：{缺项}（在「模型设置」里填好再生成）」：tr 只翻最后一个括号前的部分，
    # 括号里的固定提示会原样留下（tr 的形状约定），这里只管前半段
    (r"(\w+) 模型未配置：(\w+): 没选适配器", r"\1 model not configured: \2: no adapter selected"),
    (r"(\w+) 模型未配置：(\w+): 未知适配器 (\S+)", r"\1 model not configured: \2: unknown adapter \3"),
    (r"(\w+) 模型未配置：(.+)", r"\1 model not configured: missing \2"),
    (r"找不到项目文件：(.+)", r"Project file not found: \1"),
    # spec.normalize_spec 的「shot X.lines[i].en must be a non-empty string」（改字幕块漏了 language 时最常撞到）
    (r"镜 (\S+) 的 lines\[(\d+)\] 缺 (en|zh) 文本——字幕语言要求每句台词都有这一栏：双语要 zh 和 en，英文要 en，中文要 zh",
     r"Shot \1 lines[\2] is missing \3 text — the subtitle language requires it on every line: bilingual needs zh and en, English needs en, Chinese needs zh"),
    # spec.normalize_spec 的说话人 / 说话方式校验（DRAMA-11）
    (r"镜 (\S+) 的 lines\[(\d+)\] 的说话人「(.+?)」不在人物表里——从人物表里选一个（用 id 或角色名），旁白写 narrator",
     r'''Shot \1 lines[\2]: speaker "\3" is not in the cast — pick someone from the cast (by id or name), or write narrator for narration'''),
    (r"镜 (\S+) 的 speech_mode 只能是 (.+?)，留空 = 画内说话",
     r"Shot \1 speech_mode must be one of: \2 (leave empty = spoken on camera)"),
    # ---- 短剧项目状态机与文件边界（发第一个 tag 前的修复）：审片拒绝按具体原因分流、归档后同 id 的账本记录、文件不见了
    (r"这个 id（(\S+?)）之前开始制作过，输出目录已不在，账本里还留着记录——从归档恢复、把目录搬回来，或换一个 id",
     r"This id (\1) was in production before; its output directory is gone but the ledger still has the record — restore from the archive, move the directory back, or use a new id"),
    (r"这些镜还没有生成结果，没法审：(.+)", r"These shots have no generated output yet, so they cannot be reviewed: \1"),
    (r"(\S+) 还没生成完（现在是「(.+?)」），生成完再审", r'\1 has not finished generating (currently "\2") — review it once it is done'),
    (r"(\S+) 的文件不见了——重新生成，或把文件放回来后点「生成」重新做技术检查，不花钱",
     r'The file for \1 is missing — regenerate it, or put the file back and click "Generate" to rerun the technical check (costs nothing)'),
    (r"没有这道审片闸门：(.+)", r"No such QC gate: \1"),
    (r"不支持的审片裁决：(.+)", r"Unsupported QC decision: \1"),
    (r"重拍处置「(.+?)」不在可选范围", r'Retake disposition "\1" is not one of the options'),
    (r"重拍缺陷「(.+?)」不在可选范围", r'Retake defect "\1" is not one of the options'),
    (r"可选：(.+)", r"options: \1"),
    (r"文件不见了：(.+?)——重新生成，或重新导入已有产物", r"File missing: \1 — regenerate it, or import the existing outputs again"),
    (r"双语字幕要求每句台词都有英文：镜 (\S+) 第 (\d+) 句缺 en", r"Bilingual subtitles need an English line for every line: shot \1 line \2 has no en"),
]

# humanize() 输出里没有变量的人话：英文从 EXACT 取，这里只列 key（用于「前缀 × 人话」展开）
_HUMANIZED_PLAIN: list[str] = [
    "先把首帧审过（通过或跳过）再生成视频",
    "先把所有视频审过再合成成片",
    "首帧和视频都审过之后才能合成成片",
    "开始制作之后项目内容被改过，与冻结时不一致——开一个新版本，或者把改动撤回",
    "冻结快照文件被外部改过，账本对不上——不要手动编辑 manifests 目录",
    "这一道审片已经有别的镜在等重拍，先处理完它们",
    "产物文件变了，需要重新走一遍技术检查",
    "技术检查还没跑完，稍后再审",
    "这一道还没有可审的产物——先生成",
    "这一道还没有审过的产物",
    "重拍需要写原因",
    "没有失败的任务可以重提",
    "本地已有生成过的文件但账本里没登记——先点「导入已有产物」",
    "导入已有产物需要确认",
    "生成模型没配置——去「模型设置」",
    "output_root 必须以项目 id 结尾",
    # 短剧项目状态机与文件边界（发第一个 tag 前的修复）
    "产物文件变了，需要重新走一遍技术检查——点「生成首帧 / 生成视频」会重新检查已有文件，不花钱",
    "成片不能直接记重拍——回镜头页重拍出问题的镜次，或记 edit_rescue / redesign",
    "没有选中任何镜次",
    "项目 id 只能用英文、数字、下划线、点、短横线，且首尾得是英文或数字",
]

# post.py 后期块校验（单条；多条用「；」拼在前缀后时只翻前缀）
_POST_BLOCK_ERRORS: list[tuple[str, str]] = [
    (r"voice_over\.lines\[(\d+)\] 必须是对象", r"voice_over.lines[\1] must be an object"),
    # 以固定括号收尾的两条（缺 zh（旁白文本）/ 缺 text（画面文字））只写括号前的形状，括号里的补充说明在 EXACT 里
    # 单独有译文——同配乐那两条的写法（见下面 _POST_BLOCK_ERRORS 里 music 的注释）。整条连括号一起写的话，套上
    # 「改动没通过校验，没有写盘：」前缀之后就翻不动了：tr 先走「人话（原文）」分支，拿括号前那半去匹配，命中的是
    # 通用前缀规则（里层原样保留），这条整句规则根本轮不上，英文界面会收到半中半英的一句
    (r"voice_over\.lines\[(\d+)\] 缺 zh", r"voice_over.lines[\1] is missing zh"),
    (r"voice_over\.lines\[(\d+)\] 的 shot「(.+?)」不是本集的镜号", r'voice_over.lines[\1]: shot "\2" is not a shot id in this episode'),
    (r"phone_ui_inserts\[(\d+)\] 必须是对象", r"phone_ui_inserts[\1] must be an object"),
    (r"phone_ui_inserts\[(\d+)\] 缺 id", r"phone_ui_inserts[\1] is missing id"),
    (r"phone_ui_inserts\[(\d+)\] 的 id「(.+?)」重复或与镜号撞名", r'phone_ui_inserts[\1]: id "\2" is duplicated or collides with a shot id'),
    (r"phone_ui_inserts\[(\d+)\] 的 after「(.+?)」既不是镜号也不是别的插卡 id",
     r'phone_ui_inserts[\1]: after "\2" is neither a shot id nor another insert card id'),
    (r"dubbing\.mode 只能是 (.+)", r"dubbing.mode must be one of \1"),
    (r"dubbing\.shots 里的「(.+?)」不是本集的镜号", r'dubbing.shots: "\1" is not a shot id in this episode'),
    (r"notebook_text\[(\d+)\] 必须是对象", r"notebook_text[\1] must be an object"),
    (r"notebook_text\[(\d+)\] 的 shot「(.+?)」不是本集的镜号", r'notebook_text[\1]: shot "\2" is not a shot id in this episode'),
    # 纯色字幕卡（layout=card）、片头插卡（after=__start__）与文字角标（post_graphics.labels）
    (r"phone_ui_inserts\[(\d+)\] 的 id 不能用保留字「(.+?)」", r'phone_ui_inserts[\1]: id cannot be the reserved word "\2"'),
    (r"phone_ui_inserts\[(\d+)\] 是字幕卡，要写 lines 或 screen", r"phone_ui_inserts[\1] is a text card; it needs lines or screen"),
    (r"phone_ui_inserts\[(\d+)\]\.(background|color) 要是 #RRGGBB 形式的颜色，现在是「(.*?)」",
     r'phone_ui_inserts[\1].\2 must be a #RRGGBB color, got "\3"'),
    (r"phone_ui_inserts\[(\d+)\]\.motion 只能是 (.+?)，现在是「(.*?)」", r'phone_ui_inserts[\1].motion must be one of \2, got "\3"'),
    (r"post_graphics\.labels 必须是数组", r"post_graphics.labels must be an array"),
    (r"labels\[(\d+)\] 必须是对象", r"labels[\1] must be an object"),
    (r"labels\[(\d+)\]\.text 不能为空", r"labels[\1].text cannot be empty"),
    (r"labels\[(\d+)\]\.text 最多 (\d+) 个字，现在 (\d+) 个字", r"labels[\1].text allows at most \2 characters, got \3"),
    (r"labels\[(\d+)\]\.position 必填：(.+?) 选一个", r"labels[\1].position is required: pick one of \2"),
    (r"labels\[(\d+)\]\.position 只能是 (.+?)，现在是「(.*?)」", r'labels[\1].position must be one of \2, got "\3"'),
    (r"labels\[(\d+)\]\.start 要是不小于 0 的秒数", r"labels[\1].start must be a number of seconds, 0 or more"),
    (r"labels\[(\d+)\]\.end 要大于 start，留空表示到片尾", r"labels[\1].end must be greater than start; leave it empty to run to the end"),
    (r"labels\[(\d+)\]\.(\w+) 要是 ([\d.]+) 到 ([\d.]+) 之间的数", r"labels[\1].\2 must be a number between \3 and \4"),
    (r"labels\[(\d+)\]\.color 要是 #RRGGBB 形式的颜色，现在是「(.*?)」", r'labels[\1].color must be a #RRGGBB color, got "\2"'),
    (r"labels\[(\d+)\]\.box 要是 true 或 false", r"labels[\1].box must be true or false"),
    # 交付面遮挡区（overlays.validate）
    (r"delivery\.overlay_preset 只能是 (.+?)，现在是「(.+?)」", r'delivery.overlay_preset must be one of \1, got "\2"'),
    (r"delivery\.overlay_regions 最多 (\d+) 块，现在有 (\d+) 块", r"delivery.overlay_regions allows at most \1 regions, got \2"),
    (r"delivery\.overlay_regions\[(\d+)\] 必须是对象：\{label, x, y, w, h\}", r"delivery.overlay_regions[\1] must be an object: {label, x, y, w, h}"),
    (r"delivery\.overlay_regions\[(\d+)\]\.label 要是文字", r"delivery.overlay_regions[\1].label must be text"),
    (r"delivery\.overlay_regions\[(\d+)\]\.(\w) 要是 0 到 1 之间的数（交付面的比例）",
     r"delivery.overlay_regions[\1].\2 must be a number between 0 and 1 (fraction of the delivery frame)"),
    (r"delivery\.overlay_regions\[(\d+)\]\.(\w) 要在 0 到 1 之间，现在是 (.+)", r"delivery.overlay_regions[\1].\2 must be between 0 and 1, got \3"),
    (r"delivery\.overlay_regions\[(\d+)\]\.(\w) 要大于 0（宽高为 0 的区域挡不住任何东西）",
     r"delivery.overlay_regions[\1].\2 must be greater than 0 (a zero-size region covers nothing)"),
    (r"delivery\.overlay_regions\[(\d+)\] 超出交付面右边：x \+ w = (.+?) > 1", r"delivery.overlay_regions[\1] runs past the right edge: x + w = \2 > 1"),
    (r"delivery\.overlay_regions\[(\d+)\] 超出交付面底边：y \+ h = (.+?) > 1", r"delivery.overlay_regions[\1] runs past the bottom edge: y + h = \2 > 1"),
    (r"delivery\.overlay_regions\[(\d+)\] 有不认识的字段：(.+?)（只认 label, x, y, w, h）",
     r"delivery.overlay_regions[\1] has unknown fields: \2 (only label, x, y, w, h are accepted)"),
    # 配乐（post.py）。以固定括号收尾的两条（缺 file（先上传音频）/ end 要大于 start（留空 = 到结尾））：tr 先翻括号前的部分，
    # 括号里的补充说明在 EXACT 里单独有译文，所以这里只写括号前的形状；dubbing.duck 同理
    (r"dubbing\.duck 必须是 0 到 1 之间的数", r"dubbing.duck must be a number between 0 and 1"),
    (r"post_graphics\.music 必须是数组", r"post_graphics.music must be an array"),
    (r"music\[(\d+)\] 必须是对象", r"music[\1] must be an object"),
    (r"music\[(\d+)\] 缺 file", r"music[\1] is missing file"),
    (r"music\[(\d+)\] 的 file 要是项目内的相对路径", r"music[\1]: file must be a relative path inside the project"),
    (r"music\[(\d+)\] 的文件不存在：(.+?)", r"music[\1]: file does not exist: \2"),
    (r"music\[(\d+)\]\.(\w+) 要是 ([\d.]+) 到 ([\d.]+) 之间的数", r"music[\1].\2 must be a number between \3 and \4"),
    (r"music\[(\d+)\]\.end 要大于 start", r"music[\1].end must be greater than start"),
    # 旁白字幕开关 + 逐句声线（post_layers.validate_voice_over_extras）
    (r"voice_over\.subtitles 只能是 true 或 false", r"voice_over.subtitles must be true or false"),
    (r"voice_over\.lines\[(\d+)\]\.edge 必须是对象：\{voice, rate, pitch\}", r"voice_over.lines[\1].edge must be an object: {voice, rate, pitch}"),
    (r"voice_over\.lines\[(\d+)\] 写了逐句声线 edge，但旁白引擎是「(.+?)」——逐句声线只对 edge 引擎生效：删掉这句的 edge，或把旁白换成 edge 音色",
     r'''voice_over.lines[\1] sets a per-line edge voice, but the narration engine is "\2" — per-line voices only work with the edge engine: remove this line's edge, or switch the narration to an edge voice'''),
    (r"voice_over\.lines\[(\d+)\]\.edge 只认 voice / rate / pitch，多了：(.+)", r"voice_over.lines[\1].edge only accepts voice / rate / pitch; unexpected: \2"),
    (r"voice_over\.lines\[(\d+)\]\.edge 缺 voice：写这一句用的 edge 音色名，如 (\S+)",
     r"voice_over.lines[\1].edge is missing voice: give the edge voice name for this line, e.g. \2"),
    (r"voice_over\.lines\[(\d+)\]\.edge\.(rate|pitch) 要写成 (.+?) 这样，现在是「(.*?)」", r'''voice_over.lines[\1].edge.\2 must look like \3, got "\4"'''),
    # 绝对静音区间（post_layers.validate_silence）
    (r"post_graphics\.silence 必须是数组", r"post_graphics.silence must be an array"),
    (r"silence\[(\d+)\] 必须是对象", r"silence[\1] must be an object"),
    (r"silence\[(\d+)\] 的 shot「(.+?)」不是本集的镜号", r'silence[\1]: shot "\2" is not a shot id in this episode'),
    (r"silence\[(\d+)\]\.at 要是不小于 0 的秒数，从这一镜在后期时间轴上的起点算",
     r"silence[\1].at must be seconds ≥ 0, counted from the shot's start on the post timeline"),
    (r"silence\[(\d+)\]\.dur 要大于 0、不超过 ([\d.]+) 秒", r"silence[\1].dur must be greater than 0 and at most \2 seconds"),
    (r"silence\[(\d+)\]\.label 要是文字", r"silence[\1].label must be text"),
    (r"silence\[(\d+)\] 超出了镜 (\S+) 的时长：at \+ dur = ([\d.]+)s，这一镜 ([\d.]+)s",
     r"silence[\1] runs past shot \2: at + dur = \3s, but the shot is \4s"),
    (r"silence\[(\d+)\] 和 silence\[(\d+)\] 在镜 (\S+) 里重叠了", r"silence[\1] and silence[\2] overlap in shot \3"),
    # 发第一个 tag 前补的数值 / 引用校验：插卡 dur / layout / 文字 / after 成环，整块声线与引擎，每句 at，画面文字，字体文件
    (r"phone_ui_inserts\[(\d+)\]\.layout 只能是 notification / sms / shop / plain / card，现在是「(.*?)」",
     r'phone_ui_inserts[\1].layout must be one of notification / sms / shop / plain / card, got "\2"'),
    (r"phone_ui_inserts\[(\d+)\]\.dur 要是大于 0、不小于交叉溶解 ([\d.]+) 秒、不超过 ([\d.]+) 秒的数（留空 = 4 秒），现在是「(.*?)」",
     r'phone_ui_inserts[\1].dur must be a number greater than 0, at least the \2 s crossfade and at most \3 s (blank = 4 s), got "\4"'),
    (r"phone_ui_inserts\[(\d+)\] 没有文字：写 lines 或 screen", r"phone_ui_inserts[\1] has no text: add lines or screen"),
    (r"phone_ui_inserts\[(\d+)\] 的 after 形成了环（(.+?)），没有接到任何镜上",
     r"phone_ui_inserts[\1]: after forms a cycle (\2) and never reaches a shot"),
    (r"voice_over\.edge 必须是对象：\{voice, rate, pitch\}", r"voice_over.edge must be an object: {voice, rate, pitch}"),
    (r"voice_over\.edge 只认 voice / rate / pitch，多了：(.+)", r"voice_over.edge only accepts voice / rate / pitch; unexpected: \1"),
    (r"voice_over\.edge\.(rate|pitch) 要写成 (.+?) 这样，现在是「(.*?)」", r'''voice_over.edge.\1 must look like \2, got "\3"'''),
    (r"voice_over\.engine 只能是 edge / settings / minimax（留空 = 老规则），现在是「(.*?)」",
     r'voice_over.engine must be edge / settings / minimax (blank = the legacy rule), got "\1"'),
    (r"voice_over\.lines\[(\d+)\]\.at 要是不小于 0 的秒数，现在是「(.*?)」", r'voice_over.lines[\1].at must be seconds ≥ 0, got "\2"'),
    (r"voice_over\.lines\[(\d+)\]\.at = ([\d.]+)s 不在镜 (\S+?) 里：这一镜只有 ([\d.]+)s",
     r"voice_over.lines[\1].at = \2s is outside shot \3: the shot is only \4s"),
    (r"notebook_text\[(\d+)\] 缺 text", r"notebook_text[\1] is missing text"),
    (r"notebook_text\[(\d+)\]\.at 要是不小于 0 的秒数，现在是「(.*?)」", r'notebook_text[\1].at must be seconds ≥ 0, got "\2"'),
    (r"notebook_text\[(\d+)\]\.at = ([\d.]+)s 不在镜 (\S+?) 里：这一镜只有 ([\d.]+)s",
     r"notebook_text[\1].at = \2s is outside shot \3: the shot is only \4s"),
    (r"notebook_text\[(\d+)\]\.until 要大于 at、不超过镜 (\S+?) 的时长 ([\d.]+)s，现在是「(.*?)」",
     r'''notebook_text[\1].until must be greater than at and at most shot \2's duration of \3s, got "\4"'''),
    (r"notebook_text\[(\d+)\]\.until 要大于 at，现在是「(.*?)」", r'notebook_text[\1].until must be greater than at, got "\2"'),
    (r"notebook_text\[(\d+)\]\.y 要是 0 到 1 之间的数，现在是「(.*?)」", r'notebook_text[\1].y must be a number between 0 and 1, got "\2"'),
    (r"notebook_text\[(\d+)\]\.size 要是 12 到 300 之间的数，现在是「(.*?)」", r'notebook_text[\1].size must be a number between 12 and 300, got "\2"'),
    (r"post_graphics\.fonts 必须是对象：\{notebook, ui, subtitle\}", r"post_graphics.fonts must be an object: {notebook, ui, subtitle}"),
    (r"post_graphics\.fonts\.(\w+) 指向的字体文件不存在：(.+)", r"post_graphics.fonts.\1 points to a font file that does not exist: \2"),
    (r"字幕 render_mode 只能是 programmatic_only / none / off，现在是「(.*?)」",
     r'subtitle render_mode must be programmatic_only / none / off, got "\1"'),
]

# 路由层套在 humanize() / 校验错误外面的前缀（中文 → 英文）
_PREFIXES: list[tuple[str, str]] = [
    ("生成暂时不能进行（还差一个前置步骤没完成）", "Generation cannot proceed yet (a prerequisite step is incomplete)"),
    ("生成被拦下了（当前项目状态不允许这个操作）", "Generation was blocked (the current project state does not allow this action)"),
    ("生成失败", "Generation failed"),
    ("锁定失败", "Locking failed"),
    ("记录审核结果失败", "Failed to record the review decision"),
    ("导入已有产物失败", "Importing existing outputs failed"),
    ("重提失败", "Resubmit failed"),
    ("改动没通过校验，没有写盘", "The change failed validation and was not saved"),
    ("这次改动没被接受，文件已恢复", "The change was not accepted; the file has been restored"),
    ("修订失败，文件已恢复", "Revision failed; the file has been restored"),
    ("新版本已写出但进入制作失败", "The new version was written but failed to enter production"),
    ("复制出来的新 revision 没通过结构校验，没有创建", "The copied revision failed structural validation and was not created"),
    ("采用失败", "Adoption failed"),
    ("试听合成失败", "Preview synthesis failed"),
    # 剧本层的逐场编辑器（DESIGN §32）：PUT 落盘前校验没过
    ("剧本没通过校验，没有保存", "The screenplay failed validation and was not saved"),
]


def _composed(prefixes: list[tuple[str, str]], inners: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """「前缀：里层」整句规则；里层的组号不变（前缀里没有捕获组）。"""

    return [(f"^{re.escape(zh)}：{inner}$", f"{en}: {rep}") for zh, en in prefixes for inner, rep in inners]


_HUMANIZED_ALL: list[tuple[str, str]] = _HUMANIZED + [(re.escape(zh), EXACT[zh]) for zh in _HUMANIZED_PLAIN]

# 失败原因分类的 label（project.py 的下一步 hint 用「、」拼 sorted 后的集合）：枚举全部有序子集
_FAILURE_LABELS: list[str] = sorted(["余额或配额不足", "内容被审核拦截", "网络超时或服务不可用", "服务商报错", "被服务商限流", "模型没配置"])


def _failure_kind_combos() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for size in range(len(_FAILURE_LABELS), 0, -1):
        for combo in combinations(_FAILURE_LABELS, size):
            zh = "、".join(re.escape(k) for k in combo)
            en = ", ".join(EXACT[k].lower() for k in combo)
            out.append((rf"^1 镜提交失败（{zh}），概览页有原因和重提入口$",
                        f"1 shot failed to submit ({en}); the overview page has the reasons and a resubmit entry"))
            out.append((rf"^(\d+) 镜提交失败（{zh}），概览页有原因和重提入口$",
                        f"\\1 shots failed to submit ({en}); the overview page has the reasons and a resubmit entry"))
    return out


# episodes.py 删镜时的关联清单（固定顺序、用「；」拼）：枚举全部有序子集，组号按出现顺序递增
_SHOT_LINKS: list[tuple[str, str]] = [
    (r"被 (.+?) 当作参考镜", "used as a reference shot by {}"),
    (r"挂着 (\d+) 句旁白", "{} narration lines attached"),
    (r"后面接着插卡 (.+?)", "followed by insert cards {}"),
    (r"叠着 (\d+) 条画面文字", "{} on-screen text entries overlaid"),
    (r"挂着 (\d+) 段静音区间", "{} silence ranges attached"),
]


def _shot_link_combos() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for size in range(len(_SHOT_LINKS), 0, -1):
        for combo in combinations(_SHOT_LINKS, size):
            zh = "；".join(pat for pat, _ in combo)
            en = "; ".join(rep.format(f"\\{i + 2}") for i, (_, rep) in enumerate(combo))
            out.append((rf"^「(.+?)」还有关联：{zh}——先解开这些关联再删$", f'"\\1" still has links: {en} — unlink them before deleting'))
    return out


# ---------------------------------------------------------------------------- lint.py / continuity.py 告警（问题卡三态）
#
# 静态检查的告警是「shot S1: 影响——改法」一句话；接口层（problems.py）把它拆成问题卡：impact = 「——」前半、
# fix = 「——」后半，Overview 只显示这两半，warnings 列表里仍是整句。所以每条告警要三条规则：整句、影响半句、改法半句。
# ``_carded`` 从 (影响正则, 影响英文) + (改法正则, 改法英文) 展开这三条，组号自动挪：整句里镜号是第 1 组，改法的组号
# 再往后挪影响里的组数。没有「——」的告警只给影响。

_HEADS = {"shot": (r"shot (\S+?): ", r"shot \1: "), "cast": (r"cast (\S+?): ", r"cast \1: "), "": ("", ""),
          # 绘本（DESIGN §39）：一页 / 整本
          "page": (r"page (\S+?): ", r"page \1: "), "book": (r"全书：()", r"Book: \1"),
          # 出图（DESIGN §41）：一张图或一张风格卡
          "image": (r"image (\S+?): ", r"image \1: "),
          # 剧本层（DESIGN §32）：一场 / 整集
          "scene": (r"scene (\S+?): ", r"scene \1: "), "episode": (r"全集：()", r"Whole episode: \1"),
          # 单条视频（DESIGN §43）：一个节拍 / 整条片子
          "beat": (r"beat (\S+?): ", r"beat \1: "), "clip": (r"全片：()", r"Whole clip: \1"),
          # AI 播客（DESIGN §50）：一个环节 / 整期
          "segment": (r"segment (\S+?): ", r"segment \1: "), "podcast": (r"全期：()", r"Whole episode: \1"),
          # 剧本工作台（docs/scriptlab-spec.md §4）：一段 / 全篇
          "block": (r"block (\S+?): ", r"block \1: "), "script": (r"全篇：()", r"Whole script: \1")}


def _shift(rep: str, by: int) -> str:
    """替换串里的 \\N 组号整体加 by。"""

    return re.sub(r"\\(\d+)", lambda m: f"\\{int(m.group(1)) + by}", rep) if by else rep


def _carded(impact: tuple[str, str], fix: tuple[str, str] | None = None, head: str = "shot") -> list[tuple[str, str]]:
    ipat, irep = impact
    hpat, hrep = _HEADS[head]
    shift = 1 if hpat else 0
    n_impact = re.compile(ipat).groups
    out: list[tuple[str, str]] = []
    if fix:
        fpat, frep = fix
        out.append((f"^{hpat}{ipat}——{fpat}$", f"{hrep}{_shift(irep, shift)} — {_shift(frep, shift + n_impact)}"))
        out.append((f"^{fpat}$", frep))
    else:
        out.append((f"^{hpat}{ipat}$", f"{hrep}{_shift(irep, shift)}"))
    out.append((f"^{ipat}$", irep))
    return out


_LOCK_KINDS = [("首帧", "first-frame"), ("视频", "video")]
_BOUNDARY_LABELS = [("位置", "positions"), ("朝向", "facing"), ("视线", "gaze"), ("手", "hands"), ("持物", "held props"), ("可见状态", "visible state")]
_SIDES = [("入口", "entry"), ("出口", "exit")]
_LR = [("左", "left"), ("右", "right")]

_LINT_RULES: list[tuple[str, str]] = [
    # 点名覆盖
    # 英文替换串里的撇号：raw 字符串里 \' 是两个字符，re 展开时原样保留，界面上就会露出反斜杠——用 r''' ''' 写
    *_carded((r"正文点名了「(.+?)」，但本镜 cast 没有 (\S+)", r'''the text names "\1" but this shot's cast does not include \2'''),
             (r"它的外形描述和锚图不会进提示词；加进出场角色，或改成不点名",
              r"its appearance description and anchor image will not enter the prompt; add it to the cast, or stop naming it")),
    *_carded((r"cast 列了「(.+?)」，正文一次没提", r'cast lists "\1" but the text never mentions it'),
             (r"确认它在画面里（背影、局部也要写出来），否则从出场角色里去掉",
              r"confirm it is in frame (write it in even if only a back or a partial view), otherwise remove it from the cast")),
    # 可生成性
    *_carded((r"「(.+?)」是看不见的内部状态，模型只能瞎演", r'"\1" is an invisible inner state the model can only guess at'),
             (r"改成可见动作：停住、转头、手僵住、视线落到哪", r"rewrite as visible actions: stopping, turning the head, a hand freezing, where the gaze lands")),
    *_carded((r"否定式动作「(.+?)」模型不会执行「不做」", r'negated action "\1" — the model cannot perform "not doing"'),
             (r"写它此刻在做的事（继续盯着杯子、手停在半空）", r"write what it is doing right now (still staring at the cup, hand stopped mid-air)")),
    *_carded((r"「(.+?)」这类精确拦截动作生成失败率高", r'precise intercept actions like "\1" often fail in generation'),
             (r"换承担者、拆到相邻镜，或让动作在起点/终点已完成",
              r"give it to a different performer, split it into an adjacent shot, or have the action already completed at the start/end")),
    *_carded((r"([\d.]+) 秒里有 (\d+) 个手部动作（(.+?)）", r'\2 hand actions in \1 s ("\3")'),
             (r"精细编排链最容易崩手；留一个主动作，其余拆镜或写成已完成状态",
              r"fine-grained choreography chains are where hands break most; keep one main action and split the rest into other shots or write them as already done")),
    # 正文卫生
    *_carded((r"正文里有镜号「(.+?)」", r'the text contains shot ids "\1"'),
             (r"模型不认识镜号，把那一镜的事实写出来", r"the model does not know shot ids; write out the facts of that shot instead")),
    *_carded((r"正文里有文件名", r"the text contains a file name"),
             (r"参考图走出场角色/参考镜，不在文字里指路径", r"reference images go through the cast / reference shots, not paths in the text")),
    *_carded((r"正文里有版本号", r"the text contains a version number"), (r"那是给人看的，不该进提示词", r"that is for people, not for the prompt")),
    *_carded((r"「(.+?)」要往回翻才解释得通，模型没有上一镜", r'"\1" only makes sense by looking back, and the model has no previous shot'),
             (r"把不变的事实再写一遍（一句即可）", r"restate the unchanged facts (one sentence is enough)")),
    *_carded((r"首帧提示词 (\d+) 字", r"the first-frame prompt is \1 characters"),
             (r"多出的通常是连接词和重复描述；风格与场景固定句已在全局，镜头里只写这一格的事实",
              r"the excess is usually connectives and repeated description; style and scene boilerplate is already global, so the shot should only state the facts of this frame")),
    *_carded((r"视频提示词 (\d+) 字", r"the video prompt is \1 characters"),
             (r"运动段只写起点→唯一动作→终点，删掉任意一句画面没更不确定的就是多的",
              r"the motion part should only be start → single action → end; any sentence whose removal does not make the picture less certain is surplus")),
    # 镜头功能 / 机位
    *_carded((r"purpose「(.+?)」不在清单里（(.+?)）", r'purpose "\1" is not in the list (\2)')),
    *_carded((r"与上一镜 (\S+?) 功能都是「(.+?)」、景别都是「(.+?)」", r'same purpose "\2" and same framing "\3" as the previous shot \1'),
             (r"功能重复，两镜在交代同一件事，看着像一镜被拆成了两段；换景别/角度，或合并",
              r"duplicate function: both shots do the same job and read like one shot cut in two — change the framing/angle, or merge")),
    *_carded((r"有景别没有机位高度（平视/俯拍/仰拍）", r"framing given but no camera height (eye level / high angle / low angle)"),
             (r"「平视」是主动选择，不写等于让模型自选；竖屏里高度变化比横屏更贵，写明",
              r'"eye level" is a deliberate choice; leaving it out lets the model pick, and height changes cost more in vertical than in landscape — state it')),
    # 时长留白
    *_carded((r"beat ([\d.]+)-([\d.]+) 与前一块重叠", r"beat \1-\2 overlaps the previous block"),
             (r"两件事同时发生就标 parallel: true，否则时序写错", r"if the two things happen at once mark parallel: true, otherwise the timing is wrong")),
    *_carded((r"beats 只覆盖 ([\d.]+)s / ([\d.]+)s", r"beats only cover \1s / \2s"),
             (r"剩下 ([\d.]+)s 不会是静止画面，模型会用没批准的动作填满；补一块「保持」或缩短 dur",
              r'the remaining \1s will not be a still frame; the model fills it with unapproved motion — add a "hold" block or shorten dur')),
    # 连续性锁：锁面落进提示词
    *[rule for zh, en in _LOCK_KINDS for rule in (
        *_carded((rf"{zh}提示词里锁面「(.+?)」只出现在「不要出现」之后", rf'in the {en} prompt the lock surface "\1" only appears after "do not show"'),
                 (r"那是在叫模型别画它；正文里不写「不要出现」这类否定，不想要的东西写进负向词（neg / neg_extra）",
                  r'that tells the model not to draw it; keep negations like "do not show" out of the body and put unwanted things in the negative prompt (neg / neg_extra)')),
        *_carded((rf"{zh}提示词里锁面「(.+?)」只出现在「(.+?)」之后", rf'in the {en} prompt the lock surface "\1" only appears after "\2"'),
                 (r"那是在叫模型别画它；正文里不写「不要出现」这类否定，不想要的东西写进负向词（neg / neg_extra）",
                  r'that tells the model not to draw it; keep negations like "do not show" out of the body and put unwanted things in the negative prompt (neg / neg_extra)')),
        *_carded((rf"{zh}提示词里锁面「(.+?)」粘在别的词上（前后紧挨着字母或连字符）",
                  rf'in the {en} prompt the lock surface "\1" is glued to another word (letters or a hyphen right next to it)'),
                 (r"锁面要能作为一个完整词组读出来；改写正文让它独立成词",
                  r"the lock surface must read as a complete phrase; rewrite the text so it stands on its own")),
        *_carded((rf"{zh}提示词里没有锁面「(.+?)」", rf'the {en} prompt does not contain the lock surface "\1"'),
                 (r"外形描述或正文改写了它（少一个词、多一个逗号都算漂移）；让锁面原样出现，或改锁面本身",
                  r"the appearance description or body reworded it (one word less or one comma more counts as drift); keep the surface verbatim, or change the lock itself")),
    )],
    *_carded((r"负向词里出现了锁面「(.+?)」（neg / neg_extra）", r'the lock surface "\1" appears in the negative prompt (neg / neg_extra)'),
             (r"这是在叫模型别画它，与锁的目的相反；从负向词里删掉",
              r"that tells the model not to draw it, the opposite of what the lock is for; remove it from the negative prompt")),
    # 连续性锁：形状 / 数量 / 范围（前缀是 cast.<id>，没有 shot 头）
    *_carded((r'cast\.(\S+?)\.locks 必须是列表：\[\{"surface": "锁面", "shots": "all"\}\]',
              r'cast.\1.locks must be a list: [{"surface": "lock surface", "shots": "all"}]'), head=""),
    *_carded((r"cast\.(\S+?) 有 (\d+) 把锁", r"cast.\1 has \2 locks"),
             (r"锁通常是个位数，每条提示词都会被同一串名词撑满、挤掉本镜真正要执行的动作；只锁观众看得出前后不一致、且不随剧情变化的事实（颜色、材质、形制），状态和情绪不上锁",
              r"locks are usually single digits; every prompt gets stuffed with the same string of nouns, crowding out the action this shot must perform — "
              r"only lock facts the audience would notice changing and that do not change with the story (color, material, form); states and emotions are not locked"),
             head=""),
    *_carded((r"cast\.(\S+?)\.locks\[(\d+)\] 写法不对", r"cast.\1.locks[\2] is malformed"),
             (r'surface 填要逐字进提示词的短语，shots 填 "all" 或镜号列表',
              r'surface takes the phrase that goes into the prompt verbatim, shots takes "all" or a list of shot ids'), head=""),
    *_carded((r"cast\.(\S+?) 的锁面「(.+?)」有 (\d+) 字", r'the lock surface "\2" of cast.\1 is \3 characters'),
             (r"锁面只留最小可辨识名词短语（颜色 \+ 材质/形制 \+ 物体，如「淡蓝色粗针织羊毛毛衣」），动作、状态、数量、剧情不进锁面",
              r'keep the lock surface to the smallest recognizable noun phrase (color + material/form + object, e.g. "pale blue chunky-knit wool sweater"); '
              r"actions, states, counts and plot do not belong in it"), head=""),
    *_carded((r"cast\.(\S+?) 的锁「(.+?)」范围里有不存在的镜：(.+?)", r'the lock "\2" of cast.\1 lists shots that do not exist: \3'),
             (r"改成现有镜号或从范围里删掉", r"use existing shot ids or remove them from the range"), head=""),
    # 角色变体
    *_carded((r"cast\.(\S+?) 的变体「(.+?)」没有任何镜用到", r'the variant "\2" of cast.\1 is not used by any shot'),
             (r"在镜头的「出场」里把该角色切到这个造型，或删掉变体", r"switch the character to this look in a shot's cast, or delete the variant"), head=""),
    # 锚图文件不存在（DRAMA-11）：成员的与变体的两种抬头
    *_carded((r"cast\.(\S+?) 的变体「(.+?)」的锚图文件不存在：(.+?)", r'the anchor image of the variant "\2" of cast.\1 does not exist: \3'),
             (r"重新上传一张，或把 asset 删掉", r"upload it again, or remove the asset"), head=""),
    *_carded((r"cast\.(\S+?) 的锚图文件不存在：(.+?)", r"the anchor image of cast.\1 does not exist: \2"),
             (r"重新上传一张，或把 asset 删掉", r"upload it again, or remove the asset"), head=""),
    # 造型在镜外切换：「基底」是固定词，先写带它的两种形状，再写两边都是变体键的通用形状（基底→基底不会出现）
    *[rule for impact in (
        (r"(.+?) 的造型从「基底」（(\S+?)）切成「(.+?)」，但这一镜没声明边界变化", r'"\1" changes look from "base" (\2) to "\3", but this shot declares no boundary change'),
        (r"(.+?) 的造型从「(.+?)」（(\S+?)）切成「基底」，但这一镜没声明边界变化", r'"\1" changes look from "\2" (\3) to "base", but this shot declares no boundary change'),
        (r"(.+?) 的造型从「(.+?)」（(\S+?)）切成「(.+?)」，但这一镜没声明边界变化", r'"\1" changes look from "\2" (\3) to "\4", but this shot declares no boundary change'),
    ) for rule in _carded(impact, (
        r"造型在镜外切换观众会跳戏；在镜内交代换装/受伤（写进 boundary_change），或在两镜之间插一镜过渡",
        r"a look that switches off-screen breaks the audience's immersion; show the change of clothes/injury within a shot (write it into boundary_change), "
        r"or insert a transition shot between the two"))],
    # 画面文字政策
    *_carded((r"readable_text 里没有一条合法条目（每条要有载体 surface 和文字 text）", r"readable_text has no valid entry (each needs a surface and a text)"),
             (r"补全或删掉，否则这镜仍按「画面无文字」出图", r'complete or delete it, otherwise this shot still renders as "no text in frame"')),
    *_carded((r"可读文字《(.+?)》有 (\d+) 个字", r'readable text "\1" is \2 characters'),
             (r"模型对长文字容易漏字、错字或乱码；拆成几处短文字，或改走后期画面文字",
              r"models drop, misspell or garble long text; split it into several short pieces, or move it to post-production on-screen text")),
    *_carded((r"美术/负向补充里写了「(.+?)」，与本镜声明的可读文字互相打架（IMG-07）",
              r'art / neg_extra says "\1", which conflicts with the readable text declared for this shot (IMG-07)'),
             (r"删掉那句，唯一允许出现的文字已由提示词声明", r"delete that phrase; the only text allowed is already declared by the prompt")),
    *_carded((r"可读文字要靠出图模型渲染，很多模型对中文字形不稳，出图后核对，不行就改走后期画面文字",
              r"readable text depends on the image model's rendering, and many models are unstable with Chinese glyphs; check after generation and fall back to post-production on-screen text if needed")),
    # 轴线与左右
    *_carded((r"axis\.side 只能是 left / right，现在是「(.+?)」", r'axis.side must be left / right; currently "\1"')),
    *_carded((r"axis\.screen 里 (.+?) 的画面侧只能是 left / right", r'in axis.screen the screen side of "\1" must be left / right')),
    *[rule for (p_zh, p_en), (c_zh, c_en) in ((_LR[0], _LR[1]), (_LR[1], _LR[0])) for rule in (
        *_carded((r"越轴", r"axis crossed"),
                 (rf"(.+?) 上一镜 (\S+?) 占画面{p_zh}，本镜占画面{c_zh}，轴线锚点没变也没标 break；正反打要互补（甲在左看向右，乙就在右看向左），要么回到同侧，要么标 break: true 并从这镜起沿用新的机位侧",
                  rf'"\1" was on screen {p_en} in the previous shot \2 and is on screen {c_en} in this one, while the axis anchors are unchanged and break is not set; '
                  r"shot/reverse-shot must be complementary (A on the left looking right, B on the right looking left) — either return to the same side, "
                  r"or set break: true and keep the new camera side from this shot on")),
        *_carded((rf"机位从轴线{p_zh}侧换到了{c_zh}侧（锚点同上一镜 (\S+?)）却没标 break",
                  rf"the camera moved from the {p_en} side of the axis to the {c_en} (same anchors as the previous shot \1) without break"),
                 (r"有意越轴就标 break: true，否则机位回到同侧", r"if crossing the axis is intended set break: true, otherwise return the camera to the same side")),
    )],
    *_carded((r"「(.+?)」", r'"\1"'),
             (r"裸写的左右一律指画面左右；指画面就写「画面左/右」，指人物自身写「她的右手」",
              r'''a bare left/right always means screen left/right; write "screen left/right" for the frame and "her right hand" for the character's own body''')),
    # continuity.py：结构化入口/出口
    *[rule for s_zh, s_en in _SIDES for l_zh, l_en in _BOUNDARY_LABELS for rule in _carded(
        (rf"{s_zh}的{l_zh}「(.+?)」用了「(.+?)」", rf'{s_en} {l_en} "\1" uses "\2"'),
        (r"每个字段写成不读上一镜也能理解的绝对事实；真的没变就勾「与上一镜相同」，编译器会替你写",
         r'write each field as an absolute fact that makes sense without reading the previous shot; if it really did not change, tick "same as previous shot" and the compiler writes it for you'))],
    *_carded((r"same_as_previous 里的「(.+?)」不是边界字段（只认 (.+?)）", r'"\1" in same_as_previous is not a boundary field (only \2 are accepted)')),
    *[rule for l_zh, l_en in _BOUNDARY_LABELS for rule in (
        *_carded((rf"第一镜勾了「与上一镜相同：{l_zh}」，但它没有上一镜", rf'the first shot ticks "same as previous shot: {l_en}" but has no previous shot'),
                 (rf"把{l_zh}写成绝对事实", rf"write {l_en} as an absolute fact")),
        *_carded((rf"勾了「与上一镜相同：{l_zh}」，但上一镜 (\S+?) 的出口没写{l_zh}",
                  rf'ticks "same as previous shot: {l_en}", but the exit of the previous shot \1 has no {l_en}'),
                 (r"先把上一镜出口补上，否则「相同」没有参照", r'''fill in the previous shot's exit first, otherwise "same" has nothing to refer to''')),
        *_carded((rf"勾了「与上一镜相同：{l_zh}」，但本镜入口写的「(.+?)」和上一镜出口「(.+?)」不一样",
                  rf'''ticks "same as previous shot: {l_en}", but this shot's entry says "\1" and the previous shot's exit says "\2"'''),
                 (r"二选一", r"pick one")),
    )],
    *[rule for l_zh, l_en in _BOUNDARY_LABELS[3:5] for rule in _carded(
        (rf"持物/手部状态在镜外变了：上一镜 (\S+?) 出口{l_zh}「(.+?)」，本镜入口「(.+?)」",
         rf'hands / held props changed off-screen: the previous shot \1 exits with {l_en} "\2", this shot enters with "\3"'),
        (r"要么在上一镜的唯一动作里完成，要么写 boundary_change 说明来源",
         r"either complete it within the previous shot's single action, or explain the source in boundary_change"))],
    *[rule for l_zh, l_en in _BOUNDARY_LABELS[:2] for rule in _carded(
        (rf"{l_zh}在镜外跳变：上一镜 (\S+?) 出口「(.+?)」，本镜入口「(.+?)」",
         rf'{l_en} jumps off-screen: the previous shot \1 exits with "\2", this shot enters with "\3"'),
        (r"有意的切换就写 boundary_change 说明，否则对齐上一镜出口",
         r"if the cut is intentional explain it in boundary_change, otherwise match the previous shot's exit"))],
    # ---- lint.py：形态卡（DESIGN §31）。整集告警带「全集：」前缀，问题卡里只剩两半，所以整句 + 两半各一条
    *[rule for o_zh, o_en in (("竖屏", "portrait"), ("横屏", "landscape"), ("方形", "square"))
      for b_zh, b_en in (("生图参数", "image parameters"), ("视频参数", "video parameters"))
      for rule in (
          (rf"^全集：形态「(.+?)」是{o_zh}（(.+?)），但{b_zh}的画幅是 (.+?)——改画幅，或换形态$",
           rf'Whole episode: form "\1" is {o_en} (\2), but the {b_en} aspect ratio is \3 — change the aspect ratio, or switch form'),
          *_carded((rf"形态「(.+?)」是{o_zh}（(.+?)），但{b_zh}的画幅是 (.+?)", rf'form "\1" is {o_en} (\2), but the {b_en} aspect ratio is \3'),
                   (r"改画幅，或换形态", r"change the aspect ratio, or switch form"), head=""),
      )],
    *[rule for o_zh, o_en in (("竖屏", "portrait"), ("横屏", "landscape"), ("方形", "square")) for rule in (
        (rf"^全集：形态「(.+?)」是{o_zh}（(.+?)），但交付尺寸是 (.+?)——改交付尺寸，或换形态$",
         rf'Whole episode: form "\1" is {o_en} (\2), but the delivery size is \3 — change the delivery size, or switch form'),
        *_carded((rf"形态「(.+?)」是{o_zh}（(.+?)），但交付尺寸是 (.+?)", rf'form "\1" is {o_en} (\2), but the delivery size is \3'),
                 (r"改交付尺寸，或换形态", r"change the delivery size, or switch form"), head=""),
    )],
    *_carded((r"首镜 (.+?)s，前 (.+?) 秒既没开口也没节拍", r"the first shot runs \1s and nothing happens in the first \2 s — no line, no beat"),
             (r"形态「(.+?)」要求前 (.+?) 秒有钩子；把开口或动作提前，或把首镜拆短",
              r'form "\1" requires a hook inside the first \2 s; move the first line or action earlier, or split the first shot')),
    *_carded((r"首镜 (.+?)s，前 (.+?) 秒第一件事在 (.+?)s 才发生", r"the first shot runs \1s and the first event only happens at \3s, outside the first \2 s"),
             (r"形态「(.+?)」要求前 (.+?) 秒有钩子；把开口或动作提前，或把首镜拆短",
              r'form "\1" requires a hook inside the first \2 s; move the first line or action earlier, or split the first shot')),
    (r"^全集：平均每镜 (.+?)s，超出形态「(.+?)」的 (.+?)–(.+?)s——把长镜拆开，或换形态$",
     r'Whole episode: the average shot is \1s, above the \3–\4s range of form "\2" — split the long shots, or switch form'),
    *_carded((r"平均每镜 (.+?)s，超出形态「(.+?)」的 (.+?)–(.+?)s", r'the average shot is \1s, above the \3–\4s range of form "\2"'),
             (r"把长镜拆开，或换形态", r"split the long shots, or switch form"), head=""),
    (r"^全集：平均每镜 (.+?)s，低于形态「(.+?)」的 (.+?)–(.+?)s——合并碎镜，或换形态$",
     r'Whole episode: the average shot is \1s, below the \3–\4s range of form "\2" — merge the fragments, or switch form'),
    *_carded((r"平均每镜 (.+?)s，低于形态「(.+?)」的 (.+?)–(.+?)s", r'the average shot is \1s, below the \3–\4s range of form "\2"'),
             (r"合并碎镜，或换形态", r"merge the fragments, or switch form"), head=""),
    (r"^全集：每分钟 (\d+) 镜，超过形态「(.+?)」的上限 (\d+) 镜——合并碎镜，或换形态$",
     r'Whole episode: \1 shots per minute, over the limit of \3 for form "\2" — merge the fragments, or switch form'),
    *_carded((r"每分钟 (\d+) 镜，超过形态「(.+?)」的上限 (\d+) 镜", r'\1 shots per minute, over the limit of \3 for form "\2"'),
             (r"合并碎镜，或换形态", r"merge the fragments, or switch form"), head=""),
    (r"^全集：形态「(.+?)」要求字幕常开，但字幕样式里关掉了字幕——打开字幕，或换形态$",
     r'Whole episode: form "\1" requires subtitles always on, but the subtitle style turns them off — turn subtitles on, or switch form'),
    *_carded((r"形态「(.+?)」要求字幕常开，但字幕样式里关掉了字幕", r'form "\1" requires subtitles always on, but the subtitle style turns them off'),
             (r"打开字幕，或换形态", r"turn subtitles on, or switch form"), head=""),
    *_carded((r"竖屏形态「(.+?)」里用了远景，之后 (\d+) 镜内没有更近的景别", r'a wide shot in portrait form "\1" with nothing closer within the next \2 shots'),
             (r"竖屏上远景里人脸认不出，紧接一个近景或特写", r"faces are unreadable in a portrait wide shot; follow it with a medium or close-up")),
    # ---- lint.check_contract：单集契约（DESIGN §32）。整集告警带「全集：」前缀，问题卡里只剩两半，所以整句 + 两半各一条
    (r"^全集：episode_contract 要是对象（premise / hook / cliffhanger / must_pay_off / carry_over）——改成对象，或删掉$",
     r"Whole episode: episode_contract must be an object (premise / hook / cliffhanger / must_pay_off / carry_over) — make it an object, or remove it"),
    *_carded((r"episode_contract 要是对象（premise / hook / cliffhanger / must_pay_off / carry_over）",
              r"episode_contract must be an object (premise / hook / cliffhanger / must_pay_off / carry_over)"),
             (r"改成对象，或删掉", r"make it an object, or remove it"), head=""),
    (r"^全集：契约里的钩子「(.+?)」在前 (\d+) 镜的正文里找不到——开场要让观众立刻看到这个未稳定局面；把它写进前两镜的画面/动作/台词，或改契约$",
     "Whole episode: the contract's hook \"\\1\" does not appear in the text of the first \\2 shots — the opening must show this unstable situation right away; write it into the first two shots' picture/action/lines, or change the contract"),
    *_carded((r"契约里的钩子「(.+?)」在前 (\d+) 镜的正文里找不到", "the contract's hook \"\\1\" does not appear in the text of the first \\2 shots"),
             (r"开场要让观众立刻看到这个未稳定局面；把它写进前两镜的画面/动作/台词，或改契约",
              r"the opening must show this unstable situation right away; write it into the first two shots' picture/action/lines, or change the contract"), head=""),
    (r"^全集：契约里的集尾「(.+?)」在最后一镜 (\S+?) 的正文里找不到——出去的压力要落在最后一镜上；把它写进最后一镜，或改契约$",
     "Whole episode: the contract's cliffhanger \"\\1\" does not appear in the text of the last shot \\2 — the outgoing pressure must land on the last shot; write it into the last shot, or change the contract"),
    *_carded((r"契约里的集尾「(.+?)」在最后一镜 (\S+?) 的正文里找不到", "the contract's cliffhanger \"\\1\" does not appear in the text of the last shot \\2"),
             (r"出去的压力要落在最后一镜上；把它写进最后一镜，或改契约",
              r"the outgoing pressure must land on the last shot; write it into the last shot, or change the contract"), head=""),
    (r"^全集：契约承诺本集兑现的「(.+?)」在任何一镜的正文里都找不到——本集自己的局部结果不能全推到下一集；写进某一镜，或从契约里删掉$",
     "Whole episode: the contract promises to pay off \"\\1\" this episode, but no shot's text contains it — an episode's own local result cannot all be pushed to the next one; write it into a shot, or drop it from the contract"),
    *_carded((r"契约承诺本集兑现的「(.+?)」在任何一镜的正文里都找不到", "the contract promises to pay off \"\\1\" this episode, but no shot's text contains it"),
             (r"本集自己的局部结果不能全推到下一集；写进某一镜，或从契约里删掉",
              r"an episode's own local result cannot all be pushed to the next one; write it into a shot, or drop it from the contract"), head=""),
]

# ---- lint.check_genre_camera：题材 × 镜头风格搭配（DESIGN §48）。理由逐字取自 genre_camera_pairing.json 的 why——
#      改了 why 就要改这里（tests/test_genre_camera_pairing.py 对每张卡核对整句和问题卡两半都翻得干净）。
#      (题材名, 英文名, 理由, 英文理由)
_PAIRING_WHY: list[tuple[str, str, str, str]] = [
    ("亲子隐秘", "Hidden parentage",
     "戏在孩子一句话把大人逼到表态，要靠正反打、反应插入和画、录音这类物件特写落地；动作番的环绕、甩镜、变速把家事拍成对决，孩子入画时转身换脸的风险也最高",
     "the drama is a child's single sentence forcing the adults to take a stand, which lands through shot-reverse-shot, reaction inserts and close-ups on objects such as a drawing or a recording; anime action's orbits, whip pans and speed ramps turn a family matter into a showdown, and the risk of a face changing on a turn is highest with a child in frame"),
    ("仙侠修真", "Xianxia cultivation",
     "力量差要落到位置、界线和旁人退让的距离上，动作番的低机位出场、剪影、急推适合少数几次高价值出手，经典电影的克制调度适合其余靠余波承载的场次",
     "power gaps must land on position, boundaries and how far bystanders step back; anime action's low-angle entrances, silhouettes and fast push-ins suit the few high-value strikes, and classic film's restrained staging suits the scenes carried by aftermath"),
    ("动作任务", "Action mission",
     "看点是越收越窄的路径和损耗而不是动作幅度，动作番的低空前冲、急停、急拉见全局留给少数几场追逃，经典电影的克制调度守住每镜一个起止状态和轴线",
     "the draw is a route that keeps narrowing and what it costs, not the size of the action; anime action's low forward rushes, sudden stops and quick pull-backs to the whole picture are for the few chase set pieces, and classic film's restrained staging keeps one start and end state per shot and holds the axis"),
    ("古装权谋", "Period power play",
     "权力靠座次、站位、文书和通传这些规矩里的位置说话，电视剧档的过肩对质、定机位群像和经典电影的慢推最能让这一格的移动看得清",
     "power speaks through seating, standing position, documents and announcements inside the rules; the TV drama style's over-the-shoulder confrontations and locked-off group shots and classic film's slow push-ins make each shift in who decides easiest to read"),
    ("复仇打脸", "Revenge and comeback",
     "每次翻盘要让观众当场看清对手失去了什么，竖屏短剧的正反打、目击者反应插入和每集一次的急推或突停正好承载证据亮出与地位反转",
     "every reversal must let the audience see on the spot what the opponent just lost; the vertical short drama's shot-reverse-shot, witness reaction inserts and one crash zoom or hard stop per episode carry the evidence reveal and the status flip"),
    ("家庭关系", "Family relations",
     "少数几个日常空间里靠饭桌位置、门的开合和表演承载变化，要稳的双人镜和定机位；动作番的环绕、甩镜、变速会把家事拍成对决，表演反被运镜盖住",
     "change is carried by seating at the dinner table, doors opening and closing, and performance in a few everyday spaces, which needs steady two-shots and locked-off framing; anime action's orbits, whip pans and speed ramps turn a family matter into a showdown and bury the performance under camera moves"),
    ("悬疑规则", "Rule-based suspense",
     "恐惧来自可验证的限制，观众也在推理，悬疑档的窥视、尾随、穿堂和慢摇揭示能让线索先被观众看见",
     "fear comes from verifiable limits and the audience is reasoning too; the suspense style's peeking, tailing, corridor depth and slow revealing pans let the audience see the clue first"),
    ("生活流", "Slice of life",
     "小事靠物件状态和距离慢慢改变，要稳的定机位、慢移和人走后的空镜；题材卡明说不用密集大特写和群体震惊提速，竖屏短剧的大脸反应、急推突停和动作番的高速运镜都会把日常硬提成爆点",
     "small things change slowly through the state of objects and distance, which needs locked-off framing, slow moves and the empty frame left after people go; the genre card explicitly rules out dense big close-ups and crowd-shock speed-ups, and both the vertical short drama's big-face reactions, crash zooms and hard stops and anime action's high-speed moves force everyday life into spikes"),
    ("破镜重圆", "Second-chance romance",
     "情绪货币是证明不是甜，电视剧档的前后景移焦、人走后的空镜和竖屏短剧的缓推近脸、微距特写都能让当下的一件信物承担回忆",
     "the emotional currency is proof, not sweetness; the TV drama style's rack focus and the empty frame left after people go, and the vertical short drama's slow push-in on a face and extreme close-ups, all let one keepsake in the present carry the memory"),
    ("职场喜剧", "Workplace comedy",
     "笑点来自机构慌乱与个人克制的落差，要靠正反打、定格反应和第三方核对时清楚可读的画面；悬疑档的倾斜、手持、窥视和慢摇揭示会把办公室拍成惊悚，落差就没了",
     "the laughs come from the gap between institutional panic and personal restraint, which relies on shot-reverse-shot, held reactions and a clearly readable picture when a third party checks the result; the suspense style's dutch angles, handheld shots, peeking and slow revealing pans turn the office into a thriller and the gap disappears"),
    ("豪门婚恋", "Wealthy-family romance",
     "戏在谁能决定另一个人的生活，靠少数高辨识度空间里的光线和人物位置变化，竖屏短剧的正反打、反应插入和电视剧档的双人镜最能把这条不对等线拍清楚",
     "the drama is who gets to decide the other person's life, shown through light and positions in a few highly recognisable spaces; the vertical short drama's shot-reverse-shot and reaction inserts and the TV drama style's two-shots show that imbalance most clearly"),
    ("身份错位", "Mistaken identity",
     "观众要一直分得清画面上的人此刻占着哪个身份，要靠镜子、证件特写和稳的双人镜；动作番的环绕、包抄、甩镜正是转身换脸的高发区，身份一漂就分不清是谁",
     "the audience must always be able to tell which identity the person on screen currently holds, which relies on mirrors, close-ups of IDs and steady two-shots; anime action's orbits, flanking moves and whip pans are exactly where faces change on a turn, and once identity drifts no one can tell who is who"),
]
_PAIRING_FIXES: list[tuple[str, str]] = [
    (r"换成 (.+?)，或确认这就是想要的反差", r"switch to \1, or confirm the contrast is intended"),
    (r"换一档镜头风格，或确认这就是想要的反差", r"switch to another camera style, or confirm the contrast is intended"),
]
_PAIRING_RULES: list[tuple[str, str]] = [
    rule for g_zh, g_en, w_zh, w_en in _PAIRING_WHY for f_zh, f_en in _PAIRING_FIXES for rule in (
        (rf"^全集：题材「{re.escape(g_zh)}」配镜头风格「(\S+?)」容易打架：{re.escape(w_zh)}——{f_zh}$",
         rf'Whole episode: genre "{g_en}" with camera style "\1" tends to clash: {w_en} — {_shift(f_en, 1)}'),
        *_carded((rf"题材「{re.escape(g_zh)}」配镜头风格「(\S+?)」容易打架：{re.escape(w_zh)}",
                  rf'genre "{g_en}" with camera style "\1" tends to clash: {w_en}'),
                 (f_zh, f_en), head=""),
    )
]

# episodes.py（结构化入口/出口、轴线、可读文字）、project.py（变体/锁）、scripts.py（题材）、export.py、post.py（配乐上传）的 400/404/409
# ---------------------------------------------------------------------------- 绘本 / 有声书（DESIGN §39）：lint 告警（整句 + 问题卡两半）、
#      项目文件校验、三个后台任务、装配、朗读稿、起草、接口 400/404/409、下一步

_PB_LINT_RULES: list[tuple[str, str]] = [
    *_carded((r"还没有分页", r"there are no pages yet"), (r"先起草或手填分页表", r"draft with AI or fill in the page table by hand"), head="book"),
    *_carded((r"(\S+) 岁的书要 (\d+)–(\d+) 页（含封面封底），现在是 (\d+) 页", r"a book for ages \1 needs \2–\3 pages (cover and back included); it has \4"),
             (r"增删正文页到这个范围", r"add or remove body pages to land in that range"), head="book"),
    *_carded((r"(\S+) 岁每页中文不超过 (\d+) 字，这页 (\d+) 字", r"for ages \1 each page holds at most \2 Chinese characters; this page has \3"),
             (r"拆成两页，或删掉修饰词只留主谓宾", r"split it into two pages, or drop the modifiers and keep subject-verb-object"), head="page"),
    *_carded((r"(\S+) 岁每页中文要 (\d+)–(\d+) 字，这页只有 (\d+) 字", r"for ages \1 each page needs \2–\3 Chinese characters; this page has only \4"),
             (r"补一句动作或感受，或跟相邻页合并", r"add an action or a feeling, or merge with a neighbouring page"), head="page"),
    *_carded((r"连续 (\d+) 页没有翻页钩", r"\1 pages in a row without a page-turn hook"),
             (r"隔一两页就在页尾留一个小问题或没做完的事，翻过去再揭晓",
              r"every page or two, end the page on a small question or something left unfinished and reveal it after the page turn"), head="page"),
    *_carded((r"结尾页留了翻页钩", r"the last page has a page-turn hook"),
             (r"最后一页不再吊胃口，让故事安稳落地", r"the last page should not tease anything; let the story settle"), head="page"),
    *_carded((r"翻页钩里有恐怖词「(.+?)」", r'the hook contains the scary word "\1"'),
             (r"换成让孩子好奇而不是害怕的问题（比如「它会飘到哪儿去？」）", r'use a question that makes a child curious rather than scared (e.g. "where will it float to?")'), head="page"),
    *_carded((r"KB 方向跟上一页一样（(.+?)），连着两页往一个方向动，看着发晕",
              r"the Ken Burns direction repeats the previous page (\1); two pages moving the same way in a row feel dizzy"),
             (r"换个方向：push / pull / left / right / up / down 交替", r"change direction: alternate push / pull / left / right / up / down"), head="page"),
    *_carded((r"一页里有 (\d+) 组拟声词（(.+?)）", r"\1 onomatopoeia groups on one page (\2)"),
             (r"一页只留一组，其余写成动作", r"keep one group per page and turn the rest into action"), head="page"),
    *_carded((r"全书用了 (\d+) 组拟声词（(.+?)）", r"the book uses \1 onomatopoeia groups (\2)"),
             (r"全书来回用固定的几组，最多 4 组", r"reuse a fixed few across the book, at most 4 groups"), head="book"),
    *_carded((r"画面描述里有「(.+?)」，插画里要同时画好几个动作", r'the scene description contains "\1"; the illustration would have to show several actions at once'),
             (r"每页只画一个瞬间：留一个动作，其余挪到下一页", r"draw a single moment per page: keep one action and move the rest to the next page"), head="page"),
    *_carded((r"画面描述里出现「(.+?)」，插画里不能有文字", r'the scene description mentions "\1"; illustrations must contain no text'),
             (r"删掉要画出来的字：插画本身不带字，字幕由程序另外叠上去", r"remove the words to be drawn: illustrations carry no text, and subtitles are overlaid separately"), head="page"),
    *_carded((r"群像页用了指代词「(.+?)」，插画模型可能画出没写的角色", r'a group page uses the pronoun "\1"; the illustration model may add characters nobody wrote'),
             (r"把每个角色的名字和位置都写出来", r"write out every character by name, with where each one is"), head="page"),
    *_carded((r"结尾不够安稳：(\S+) 岁的书最后一页中文里没有 晚安 / 睡 / 抱 / 回家 之一",
              r"the ending does not settle: the last page of a book for ages \1 has none of good night / sleep / hug / home in its Chinese text"),
             (r"最后一句写到入睡或到家，读完能踏实合上书", r"let the last line reach sleep or home so the book can be closed calmly"), head="page"),
    *_carded((r"目标词「(.+?)」全书只出现 (\d+) 次（要 ≥(\d+) 次）", r'the target word "\1" appears only \2 times in the book (needs ≥\3)'),
             (r"把它放进重复句式里，每次重复都带上", r"put it inside the repeated sentence so every repetition carries it"), head="book"),
    *_carded((r"重复母题 (\d+) 组（(.+?)）", r"\1 repeated motif group(s) (\2)"), (r"最后一次出现时记得换个结果", r"remember to change the outcome the last time it appears"), head="book"),
    *_carded((r"没有出现 ≥3 次的重复句式", r"no sentence pattern repeats 3 or more times"),
             (r"小孩子喜欢提前猜到下一句：可以让一句话在三页里原样出现，最后一次换个结果",
              r"young children love guessing the next line: repeat one sentence word for word on three pages and change the outcome the last time"), head="book"),
]

_IMAGE_LINT_RULES: list[tuple[str, str]] = [
    # imagecraft/lint.py：方言两条 + 否定式 + 画面文字没加引号 + 卡的色板（DESIGN §41）
    *_carded((r"GPT Image 方言里出现了空话「(.+?)」", r'the GPT Image dialect contains the filler words "\1"'),
             (r"这些词只夸不描述画面，删掉，换成具体的光线、材质或颜色",
              r"these words praise without describing anything; delete them and name the actual light, material or colour"), head="image"),
    *_carded((r"Nano Banana 方言里出现了数字镜头参数「(.+?)」", r'the Nano Banana dialect contains numeric lens parameters "\1"'),
             (r"这类数值换不来对应的画面，改成直接描述想要的样子（背景虚到什么程度、离主体多近、透视夸不夸张）",
              r"numbers like these do not buy the matching picture; describe the look you want instead (how soft the background is, "
              r"how close the camera is, how strong the perspective is)"), head="image"),
    *_carded((r"提示词里有否定式描述「(.+?)」", r'the prompt uses negative phrasing "\1"'),
             (r"反着写：说清画面里有什么，别列没有什么；不想要的东西写进负向词",
              r"turn it around: say what is in the frame instead of listing what is not; put what you do not want into the negative words"), head="image"),
    *_carded((r"要出现在画面里的文字「(.+?)」没加引号", r'the text "\1" meant to appear in the image is not quoted'),
             (r"要出现的文字用引号原样写出来，模型才知道逐字渲染哪几个字",
              r"put the text to render in quotes exactly as written so the model knows which characters to draw"), head="image"),
    *_carded((r"风格卡「(.+?)」的色板只有 (\d+) 个色名", r'the palette of the style card "\1" has only \2 colour names'),
             (r"色板写 5–7 个具体色名（雾蓝、砖红、米白），模型才有的抓",
              r"write 5–7 concrete colour names (misty blue, brick red, off-white) so the model has something to hold on to"), head="image"),
]

# ---------------------------------------------------------------------------- 剧本层的逐场编辑器（DESIGN §32）：
#      storyvideo/screenplay.py 的 lint 六条（整句 + 问题卡两半）与 PUT 落盘前的校验

# 场景标题缺的是地点、时间还是两个都缺：三种收尾各展开一条（英文里不能留中文字段名）
_SCENE_MISSING = [("地点", "where it happens"), ("时间", "when it happens"), ("地点、时间", "where or when it happens")]

_SCREENPLAY_LINT_RULES: list[tuple[str, str]] = [
    *_carded((r"这一场既没有节拍也没有对白", r"this scene has neither beats nor dialogue"),
             (r"写出这一场发生了什么（谁做了什么），或者删掉这一场",
              r"write what happens in it (who does what), or delete the scene"), head="scene"),
    *[rule for zh, en in _SCENE_MISSING for rule in _carded(
        (rf"场景标题「(.+?)」没写{zh}", rf'the scene heading "\1" does not say {en}'),
        (r"按「内/外 · 地点 · 时间」写全，拆镜时才知道这一场在哪、什么光线",
         r"write it out in full as interior/exterior - place - time, so the shot breakdown knows where this scene is and what the light is"),
        head="scene")],
    *_carded((r"「(.+?)」在剧本里一句台词也没有，节拍里也没点名",
              r'"\1" never speaks in the screenplay and is never named in a beat'),
             (r"给他一场真的要做点什么的戏，或者从人物表里删掉",
              r"give them a scene where they actually do something, or drop them from the cast"), head="cast"),
    *_carded((r"开场钩子「(.+?)」在第一场看不见", r'the opening hook "\1" is not visible in the first scene'),
             (r"把它写进第一场的节拍或对白，观众开头就要看见这个未稳定的局面",
              r"write it into the first scene's beats or dialogue; the audience must see this unstable situation right away"), head="episode"),
    *_carded((r"集尾压力「(.+?)」没有落在最后一场", r'the closing pressure "\1" does not land on the last scene'),
             (r"最后一场要写出某人已经决定做什么、或者某个后果已经启动",
              r"the last scene must show that someone has decided to act, or that a consequence is already under way"), head="episode"),
    *_carded((r"一集写了 (\d+) 场", r"\1 scenes in one episode"),
             (r"竖屏短剧一集通常不超过 (\d+) 场，场次太碎每一场都来不及改变什么；把同地点同时间的场次合并",
              r"a vertical short-drama episode usually stays under \1 scenes; when they are this fragmented none of them has time to change anything; "
              r"merge scenes that share a place and a time"), head="episode"),
]

# storyvideo/screenplay.py 的 normalize_screenplay（PUT 落盘前）：单独出现，也跟「剧本没通过校验，没有保存：」拼成整句
_SCREENPLAY_SPEC_ERRORS: list[tuple[str, str]] = [
    (r"剧本要是对象（title / logline / contract / cast / scenes）",
     r"the screenplay must be an object (title / logline / contract / cast / scenes)"),
    (r"剧本至少要有一场（scenes 列表不能为空）", r"a screenplay needs at least one scene (the scenes list cannot be empty)"),
    (r"人物表（cast）要是列表，每个角色一条", r"the cast must be a list, one entry per character"),
    (r"人物表第 (\d+) 条要是对象（id / name / block）", r"cast entry \1 must be an object (id / name / block)"),
    (r"人物表里「(.+?)」没有 id——拆镜时角色靠 id 对上，给它一个简短的英文 id",
     r'the cast entry "\1" has no id — shots match characters by id; give it a short latin id'),
    (r"人物表里 id 重复了：(\S+?)——每个角色一个 id，台词才知道是谁说的",
     r"duplicate cast id: \1 — one id per character, otherwise a line does not know who says it"),
    (r"第 (\d+) 场要是对象（id / heading / beats / dialogue）", r"scene \1 must be an object (id / heading / beats / dialogue)"),
    (r"场次 id 重复了：(\S+?)——每一场一个 id，告警和拆镜都靠它对上",
     r"duplicate scene id: \1 — one id per scene; warnings and the shot breakdown both match on it"),
    (r"场 (\S+?) 没有场景标题——按「内/外 · 地点 · 时间」写一行",
     r"scene \1 has no heading — write one line as interior/exterior - place - time"),
    (r"场 (\S+?) 的对白要是列表，每句一条", r"the dialogue of scene \1 must be a list, one entry per line"),
    (r"场 (\S+?) 的第 (\d+) 句台词要是对象（who / zh）", r"line \2 of scene \1 must be an object (who / zh)"),
    (r"场 (\S+?) 的第 (\d+) 句台词是空的——填上台词，或者删掉这一行",
     r"line \2 of scene \1 is empty — write the line, or delete the row"),
    (r"场 (\S+?) 的第 (\d+) 句台词没写谁说的——从人物表里选一个人，旁白写 narrator",
     r"line \2 of scene \1 does not say who speaks — pick someone from the cast, or write narrator for voice-over"),
    (r"场 (\S+?) 的第 (\d+) 句台词的说话人「(.+?)」不在人物表里——从人物表里选一个（用 id 或角色名），或者先把这个人加进人物表",
     r'the speaker "\3" of line \2 in scene \1 is not in the cast — pick someone from the cast (by id or name), or add this person to it first'),
    (r"场 (\S+?) 的节拍要是列表，每条一件事", r"the beats of scene \1 must be a list, one thing per entry"),
    (r"场 (\S+?) 的第 (\d+) 条节拍要是一句话（谁做了什么）", r"beat \2 of scene \1 must be a sentence (who does what)"),
    (r"「(.+?)」要是一段文字", r'"\1" must be a piece of text'),
]

_IMAGE_ROUTER_RULES: list[tuple[str, str]] = [
    # routers/style_cards.py、routers/poster.py、imageset / picturebook 的项目文件校验（DESIGN §41）
    (r"^没有这张风格卡：(.+)$", r"No such style card: \1"),
    (r"^没有这个风格分类：(.+)$", r"No such style category: \1"),
    (r"^style 只能是模板预设 id（(.+?)）、风格卡 id 或 custom，收到「(.+?)」$",
     r'style must be a template preset id (\1), a style card id, or custom; received "\2"'),
]

_PB_SPEC_ERRORS: list[tuple[str, str]] = [
    (r"(\S+) 不能为空，只能是 (.+)", r"\1 cannot be empty; must be one of \2"),
    (r"(\S+) 只能是 (.+?)，收到「(.+?)」", r'\1 must be one of \2; received "\3"'),
    (r"(\S+) 只能是项目内的相对路径（content/assets/…）", r"\1 must be a relative path inside the project (content/assets/…)"),
    (r"(\S+) 要是对象（(.+?)）", r"\1 must be an object (\2)"),
    (r"(\S+) 要是对象（(.+?)）或留空", r"\1 must be an object (\2) or empty"),
    (r"education\.vocab 要是数组（如「袜子/sock」）", r'education.vocab must be an array (e.g. "袜子/sock")'),
    (r"(\S+) 的 id「(.+?)」只能用英文、数字、下划线、点、短横线", r'the id "\2" in \1 may only contain letters, digits, underscores, dots and hyphens'),
    (r"narration\.en_rate 要写成百分比，如 -8%", r"narration.en_rate must be a percentage such as -8%"),
    (r"subtitle\.highlight 要是 #RRGGBB", r"subtitle.highlight must be #RRGGBB"),
    (r"page (\S+)\.kb\.scale 要是两个数字 \[起, 止\]，如 \[1\.0, 1\.08\]", r"page \1.kb.scale must be two numbers [from, to], e.g. [1.0, 1.08]"),
    (r"pages 要是数组", r"pages must be an array"),
    (r"pages\[(\d+)\] 要是对象", r"pages[\1] must be an object"),
    (r"pages\[(\d+)\]\.id 要写成 P01 这样的形状，收到「(.+?)」", r'pages[\1].id must look like P01; received "\2"'),
    (r"页 id 重复：(.+)", r"Duplicate page id: \1"),
    (r"page (\S+)\.zh 不能为空——双语策略 (\S+) 需要中文", r"page \1.zh cannot be empty — bilingual mode \2 needs Chinese"),
    (r"page (\S+)\.en 不能为空——双语策略 (\S+) 需要英文", r"page \1.en cannot be empty — bilingual mode \2 needs English"),
    (r"page (\S+)\.characters 要是数组", r"page \1.characters must be an array"),
    (r"page (\S+) 的出场角色「(.+?)」不在 characters 表里", r'page \1 lists the character "\2", which is not in the characters table'),
    (r"page (\S+) 的场景锚「(.+?)」不在 scenes 表里", r'page \1 references the scene anchor "\2", which is not in the scenes table'),
    (r"项目文件要是一个 JSON 对象", r"the project file must be a JSON object"),
    (r"id 不能为空——新建时由服务端编号", r"id cannot be empty — the server assigns one when creating"),
    (r"title_zh / title_en 至少填一个", r"fill in at least one of title_zh / title_en"),
    (r"项目文件里的 id「(.+?)」跟文件名 (\S+) 对不上", r'the id "\1" in the project file does not match the file name \2'),
    (r"项目文件不是合法的 JSON（(.+?) 第 (\d+) 行）", r"the project file is not valid JSON (\1, line \2)"),
    (r"读不了项目文件 (.+?)：(.+)", r"could not read the project file \1: \2"),
    (r"(\S+) 要是文本", r"\1 must be text"),
    (r"(\S+) 不能为空", r"\1 cannot be empty"),
]

_PB_ROUTER_RULES: list[tuple[str, str]] = [
    (r"^没有这本绘本：(.+)$", r"No such picture book: \1"),
    (r"^没有这一页：(.+)$", r"No such page: \1"),
    (r"^没有这些页：(.+)$", r"No such pages: \1"),
    (r"^请求体要是项目文件对象$", r"The request body must be a project-file object"),
    (r"^绘本 (\S+) 已经存在，换一个 id$", r"Picture book \1 already exists; pick another id"),
    (r"^绘本 (\S+) 已经存在，起草结果没有写盘——从任务结果里取草稿另建$", r"Picture book \1 already exists; the draft was not saved — take the draft from the task result and create it separately"),
    (r"^项目文件里的 id「(.+?)」跟地址里的 (\S+) 对不上$", r'The id "\1" in the body does not match \2 in the URL'),
    (r"^故事方向不能为空$", r"The story direction cannot be empty"),
    (r"^年龄段只能是 (.+)$", r"age_band must be one of \1"),
    (r"^双语策略只能是 (.+)$", r"bilingual_mode must be one of \1"),
    (r"^页数要在 4 到 24 之间$", r"page_count must be between 4 and 24"),
    (r"^起草结果没通过结构校验：(.+)$", r"The draft failed structural validation: \1"),
    (r"^模型没有给出分页表（pages 为空）$", r"The model returned no page table (pages is empty)"),
    (r"^模型返回的不是 JSON：(.+)$", r"The model did not return JSON: \1"),
    (r"^这本书还没有分页，没有可生成的插画$", r"This book has no pages yet, so there are no illustrations to generate"),
    (r"^这本书还没有分页，没有可朗读的文本$", r"This book has no pages yet, so there is no text to narrate"),
    (r"^这本书还没有分页$", r"This book has no pages yet"),
    (r"^最多 (\d+) 次生图请求（(.+?)）；请求没变且图已在的页会跳过$", r"Up to \1 image request(s) (\2); pages whose request is unchanged and already have an image are skipped"),
    (r"^languages 只认 zh / en$", r"languages accepts only zh / en"),
    (r"^还有 (\d+) 页没有插画，先生成插画$", r"\1 page(s) still have no illustration; generate illustrations first"),
    (r"^还有 (\d+) 页没有朗读，先生成朗读$", r"\1 page(s) still have no narration; generate narration first"),
    (r"^还没有成片$", r"No final cut yet"),
    (r"^kind 只能是 character / scene / music / sfx$", r"kind must be character / scene / music / sfx"),
    (r"^没有这个角色：(.+)$", r"No such character: \1"),
    (r"^没有这个场景：(.+)$", r"No such scene: \1"),
    (r"^只接受 (.+?) 文件，收到的是 \.(.+)$", r"Only \1 files are accepted; received .\2"),
    (r"^文件数据不是合法的 base64：(.+)$", r"The file data is not valid base64: \1"),
    (r"^文件为空$", r"The file is empty"),
    (r"^文件超过 (\d+)MB$", r"The file exceeds \1MB"),
    (r"^lang 只能是 zh / en$", r"lang must be zh / en"),
    (r"^这本书的双语策略是 (\S+)，没有中文朗读稿$", r"This book's bilingual mode is \1; it has no Chinese reading script"),
    (r"^这本书的双语策略是 (\S+)，没有英文朗读稿$", r"This book's bilingual mode is \1; it has no English reading script"),
    (r"^openpyxl 没装：(.+)$", r"openpyxl is not installed: \1"),
    # narration.py / pages.py / assembly.py（任务记录里的失败原因）
    (r"^测不出音频时长：(.+?)——(.+)$", r"Could not measure the audio duration of \1 — \2"),
    (r"^(\S+) 没合成出音频文件$", r"\1 produced no audio file"),
    (r"^生图来源说完成了，但没有给结果地址$", r"The image source reported completion but returned no result URL"),
    (r"^生图来源返回失败$", r"The image source returned a failure"),
    (r"^生图来源没有返回结果$", r"The image source returned no result"),
    (r"^等了 (\d+) 分钟还没出图（远端任务 (.+?)），先放弃这一页$", r"Waited \1 minutes with no image (remote task \2); giving up on this page"),
    (r"^下载下来的插画是空文件$", r"The downloaded illustration is an empty file"),
    (r"^第 (\S+) 页还没有插画，先生成插画$", r"Page \1 has no illustration yet; generate illustrations first"),
    (r"^第 (\S+) 页的中文朗读还没合成，先生成朗读$", r"The Chinese narration of page \1 is not synthesized yet; generate narration first"),
    (r"^第 (\S+) 页的英文朗读还没合成，先生成朗读$", r"The English narration of page \1 is not synthesized yet; generate narration first"),
    (r"^朗读文件不见了：(.+?)——重新生成朗读$", r"Narration file missing: \1 — regenerate the narration"),
    (r"^这些朗读还没合成：(.+?)——先生成朗读$", r"These narrations are not synthesized yet: \1 — generate narration first"),
    (r"^没找到可用的中文字体，在字幕设置里指定 font_path$", r"No usable Chinese font found; set font_path in the subtitle settings"),
    (r"^配乐文件不存在：(.+)$", r"Music file does not exist: \1"),
    # 下一步（summary）
    (r"^用 AI 起草，或在分页表里手填$", r"Draft with AI, or fill in the page table by hand"),
    (r"^还有 (\d+) 条文本检查告警——不拦生成，但成片前最好清掉$", r"\1 text-check warning(s) remain — generation is not blocked, but clear them before the final cut"),
    (r"^还有 (\d+) 页没有插画$", r"\1 page(s) still have no illustration"),
    (r"^还有 (\d+) 页没有朗读$", r"\1 page(s) still have no narration"),
    (r"^本地 ffmpeg 拼接，不花钱$", r"Assembled locally with ffmpeg; costs nothing"),
    (r"^可以下载成片，或导出朗读稿交给真人朗读$", r"Download the final cut, or export the reading script for a human narrator"),
    (r"^示例没通过结构校验（这是示例文件本身的问题）：(.+)$", r"The example failed structural validation (a problem in the example file itself): \1"),
    # 路由包装：一页 / 一段都没出时任务记 failed（deps.all_failed_message）。里层最常见的是「模型未配置」，具体的写在前面
    (r"^(\d+) 页插画全部失败：(\w+) 模型未配置：(\w+): 没选适配器$", r"All \1 illustrations failed: \2 model not configured: \3: no adapter selected"),
    (r"^(\d+) 页插画全部失败：(.+)$", r"All \1 illustrations failed: \2"),
    (r"^(\d+) 段朗读全部失败：(\w+) 模型未配置：(\w+): 没选适配器$", r"All \1 narration segments failed: \2 model not configured: \3: no adapter selected"),
    (r"^(\d+) 段朗读全部失败：(.+)$", r"All \1 narration segments failed: \2"),
    # webui/uploads.py：BGM / 翻页音效按文件头验
    (r"^不是可用的音频文件：认不出格式，mp3 / wav / m4a / aac / ogg / flac 的文件头都对不上$",
     r"Not a usable audio file: the format could not be identified — none of the mp3 / wav / m4a / aac / ogg / flac headers match"),
]

# ---------------------------------------------------------------------------- 图片集引擎（DESIGN §40）：表情与头像 / 纪念写真
#
# spec.py 的形状校验既单独出现（pick / 示例复制的 400），也套在「改动没通过校验，没有写盘：」后面，所以写成不带
# ^$ 的片段，下面既进 _composed 也进单独规则。以全角括号收尾的几条只写括号前的部分——tr 会先翻前半段，
# 括号里的补充说明在 EXACT 里（参考图路径 / 图片 id / 自写风格锚）。具体的写在前面，通用的写在后面。

_SET_SPEC_ERRORS: list[tuple[str, str]] = [
    (r"项目文件的 module 是「(.+?)」，跟这个模块（(.+?)）对不上",
     r'the project file says module "\1", which does not match this module (\2)'),
    (r"id 只能用英文、数字、下划线、点、短横线", r"id may only contain letters, digits, underscores, dots and hyphens"),
    (r"(subject|image_params) 要是对象", r"\1 must be an object"),
    (r"subject\.assets 要是数组", r"subject.assets must be an array"),
    (r"(\S+) 只能是项目内的相对路径", r"\1 must be a relative path inside the project"),
    (r"参考图最多 (\d+) 张，现在有 (\d+) 张", r"at most \1 reference images; there are \2"),
    (r"style 选 custom 时 style_anchor 不能为空", r"style_anchor cannot be empty when style is custom"),
    (r"options 要是对象", r"options must be an object"),
    (r"options\.(\S+) 要是数组", r"options.\1 must be an array"),
    (r"options\.(\S+) 至少要有一项", r"options.\1 needs at least one entry"),
    (r"items 要是数组", r"items must be an array"),
    (r"items\[(\d+)\] 要是对象", r"items[\1] must be an object"),
    (r"items\[(\d+)\]\.id 要写成 I01 这样的形状，收到「(.+?)」", r'items[\1].id must look like I01; received "\2"'),
    (r"items\[(\d+)\]\.(\w+) 要是文本", r"items[\1].\2 must be text"),
    (r"图片 id 重复：(.+)", r"Duplicate image id: \1"),
    (r"picks 要是数组", r"picks must be an array"),
    (r"picks 里的「(.+?)」不是这个项目的图片 id", r'picks contains "\1", which is not an image id in this project'),
    (r"item (\S+)\.attempts\[(\d+)\] 要是对象", r"item \1.attempts[\2] must be an object"),
    (r"item (\S+)\.attempts\[(\d+)\]\.(\w+) 要是文本", r"item \1.attempts[\2].\3 must be text"),
    (r"item (\S+)\.(\w+) 只能是 (.+?)，收到「(.+?)」", r'item \1.\2 must be one of \3; received "\4"'),
    (r"item (\S+)\.(\w+) 要是文本", r"item \1.\2 must be text"),
    (r"没有这张图：(.+)", r"No such image: \1"),
    (r"(\S+) 要是整数", r"\1 must be an integer"),
    (r"(\S+) 要在 (\d+) 到 (\d+) 之间", r"\1 must be between \2 and \3"),
]

# ---------------------------------------------------------------------------- 单条视频（DESIGN §43）：
#      clip/lint.py 的七条告警 + 两条提示（整句 + 问题卡两半）、routers/clip.py 的 400/404/409/413/425 与
#      cost_note、clip/generate.py 里会进任务记录的失败原因、clip/spec.py 的项目文件校验。
_CLIP_LINT_RULES: list[tuple[str, str]] = [
    *_carded((r"时间轴在 (\S+?)–(\S+?) 秒是空的（(\S+?) 与 (\S+?) 之间）",
              r"the timeline is empty between \1 s and \2 s (between \3 and \4)"),
             (r"节拍首尾相接（上一拍的 end = 下一拍的 start）；空档里模型会自己填一段没人批准的画面",
              r"beats must meet end to start (the previous end is the next start); the model fills any gap with footage nobody approved"),
             head="clip"),
    *_carded((r"与 (\S+?) 在 (\S+?)–(\S+?) 秒重叠", r"overlaps \1 between \2 s and \3 s"),
             (r"一个时间点只归一拍，改 start / end 让它们首尾相接",
              r"a moment belongs to one beat only; adjust start / end so they meet end to start"), head="beat"),
    *_carded((r"节拍覆盖 (\S+?) 秒，出片时长写的是 (\S+?) 秒", r"the beats cover \1 s but the render duration says \2 s"),
             (r"把最后一拍的 end 或出片时长改成一样；对不上的那几秒由模型自己决定演什么",
              r"make the last beat end or the duration match; whatever is left over is the model deciding on its own"), head="clip"),
    *_carded((r"色板只有 (\d+) 个色名", r"the palette has only \1 colour names"),
             (r"色板写 5–7 个具体色名（雾蓝、砖红、米白），模型才有的抓",
              r"write 5–7 concrete colour names (misty blue, brick red, off-white) so the model has something to hold on to"), head="clip"),
    *_carded((r"画面里有连接词「(.+?)」", r'the shot description contains the connective "\1"'),
             (r"一个节拍只做一件事：两件事拆成两拍，各自给自己的时间段",
              r"one beat does one thing: split two actions into two beats, each with its own time span"), head="beat"),
    *_carded((r"【衔接与收尾】没写最后一帧", r"the transitions & ending part does not say what the last frame is"),
             (r"写清片子最后停在什么画面上、画面主体是谁——观众带走的就是这一帧，不写模型会随手收尾",
              r"say which picture the clip ends on and what its subject is — that is the frame the audience takes away; "
              r"leave it out and the model ends however it likes"), head="clip"),
    *_carded((r"要在画面里出现文字「(.+?)」，文字政策却写着全片无文字",
              r'the beat asks for the on-screen text "\1" while the text policy says the clip has no text'),
             (r"两处对不上模型只会挑一处听：改文字政策，或把这一拍的画面文字删掉",
              r"the model will only follow one of them: change the text policy, or drop the on-screen text of this beat"), head="beat"),
    *_carded((r"还没有节拍", r"there are no beats yet"),
             (r"时间轴是这条片子的骨架：先按秒把画面切成 4–10 拍，再逐拍写画面",
              r"the timeline is the skeleton of the clip: cut it into 4–10 beats by the second first, then write each one"), head="clip"),
    *_carded((r"没写转场载体", r"no transition carrier is written"),
             (r"借前景里本来就有的东西挡一下镜头再露出下一拍（门框、路过的人、树枝），比后期叠的特效自然",
              r"let something already in the foreground pass in front of the lens before the next beat (a door frame, a passer-by, "
              r"a branch); it looks more natural than an effect added in post"), head="clip"),
    # imagecraft.lint 的通用项挂在「全片：」下（整句形；两半在 _IMAGE_LINT_RULES 里已有）
    (r"^全片：提示词里有否定式描述「(.+?)」——反着写：说清画面里有什么，别列没有什么；不想要的东西写进负向词$",
     r'Whole clip: the prompt uses negative phrasing "\1" — turn it around: say what is in the frame instead of listing what is not; '
     r"put what you do not want into the negative words"),
    (r"^全片：要出现在画面里的文字「(.+?)」没加引号——要出现的文字用引号原样写出来，模型才知道逐字渲染哪几个字$",
     r'Whole clip: the text "\1" meant to appear in the frame is not quoted — put the text to render in quotes exactly as written '
     r"so the model knows which characters to draw"),
    (r"^全片：Nano Banana 方言里出现了数字镜头参数「(.+?)」——这类数值换不来对应的画面，改成直接描述想要的样子（背景虚到什么程度、离主体多近、透视夸不夸张）$",
     r'Whole clip: the Nano Banana dialect contains numeric lens parameters "\1" — numbers like these do not buy the matching picture; '
     r"describe the look you want instead (how soft the background is, how close the camera is, how strong the perspective is)"),
]

# clip/spec.py：项目文件校验（单独出现，或挂在「改动没通过校验，没有写盘：」这类前缀后面）
_CLIP_SPEC_ERRORS: list[tuple[str, str]] = [
    (r"没有这张风格卡：(.+?)（也可以写 custom 自写风格锚）", r"No such style card: \1 (or write custom and set your own style anchor)"),
    (r"current「(.+?)」不在 attempts 里——采用的必须是出过的那几条之一",
     r'current "\1" is not in attempts — the take in use must be one that was actually rendered'),
    (r"style 选 custom 时 style_anchor 或 global\.look 至少写一个（风格锚是提示词的第一句）",
     r"when style is custom, fill in either style_anchor or global.look (the style anchor is the first sentence of the prompt)"),
    (r"global 里不认识的字段：(.+)", r"Unknown fields in global: \1"),
    (r"beats\[(\d+)\]\.id 要写成 B01 这样的形状，收到「(.+?)」", r'beats[\1].id must look like B01; received "\2"'),
    (r"节拍 id 重复：(.+)", r"Duplicate beat id: \1"),
    (r"beat (\S+?) 的 end 要大于 start：现在 start=(\S+?)、end=(\S+?)", r"beat \1: end must be greater than start (start=\2, end=\3)"),
    (r"first_frame\.asset 只能是项目内的相对路径（content/assets/… 或 output/clip/…）",
     r"first_frame.asset must be a relative path inside the project (content/assets/… or output/clip/…)"),
]

_CLIP_ROUTER_RULES: list[tuple[str, str]] = [
    (r"^没有这条片子：(.+)$", r"No such clip: \1"),
    (r"^还没有节拍，先在「写法」里写时间轴$", r"There are no beats yet — lay out the timeline on the Writing page first"),
    (r"^首帧模式是「用上传的图」，但还没有上传首帧$", r"The first-frame mode is set to an uploaded image, but nothing has been uploaded yet"),
    (r"^attempt 要是这条片子成功出过的文件名之一$", r"attempt must be one of the file names this clip was successfully rendered as"),
    (r"^first_frame\.mode 只能是 (.+?)，收到「(.+?)」$", r'first_frame.mode must be one of \1; received "\2"'),
    (r"^没有这个节拍：(.+)$", r"No such beat: \1"),
    # cost_note（点「出片」之前那一句）
    (r"^1 次视频请求（(.+?)，(\d+) 秒）；请求没变且片子已在的会跳过$",
     r"1 video request (\1, \2 s); a run whose request has not changed and whose file is already there is skipped"),
    (r"^1 次视频请求（(.+?)，(\d+) 秒）；force 会重出一条新的（旧的不删）$",
     r"1 video request (\1, \2 s); force renders a new take (the old ones are kept)"),
    (r"^1 次生图请求（首帧，(.+?)）\+ 1 次视频请求（(.+?)，(\d+) 秒）；请求没变且片子已在的会跳过$",
     r"1 image request (first frame, \1) + 1 video request (\2, \3 s); a run whose request has not changed and whose file is already there is skipped"),
    (r"^1 次生图请求（首帧，(.+?)）；已经有首帧且没勾「重出」的会跳过$",
     r"1 image request (first frame, \1); skipped when a first frame is already there and you did not tick regenerate"),
    # clip/generate.py：进任务记录的失败原因
    (r"^首帧没出来，视频没有提交：(.+)$", r"The first frame failed, so the video was never submitted: \1"),
    (r"^下载下来的片子是空文件$", r"The downloaded clip is an empty file"),
    (r"^下载下来的首帧是空文件$", r"The downloaded first frame is an empty file"),
    (r"^等了 (\d+) 分钟还没出片（远端任务 (.+?)），先放弃这一次$", r"Waited \1 minutes with no clip (remote task \2); giving up on this run"),
    (r"^来源说完成了，但没有给结果地址$", r"The source reported success but gave no result URL"),
    (r"^来源返回失败$", r"The source returned a failure"),
    (r"^视频来源没有返回结果$", r"The video source returned no result"),
]

# routers/imageset.py 的 400/404/409/413/425 与 cost_note；generate.py / post.py / export.py 里会进 HTTP 响应
# （或任务记录的失败原因）的人话
_SET_ROUTER_RULES: list[tuple[str, str]] = [
    # 「没有这个项目：…」「没有这个示例：…」跟短剧 / 绘本同一句，下面已有规则，这里不重复
    (r"^项目 (\S+) 已经存在，换一个 id$", r"Project \1 already exists; pick another id"),
    (r"^who 只能是 bride / groom$", r"who must be bride / groom"),
    (r"^还没有展开清单，先按模板展开$", r"The item list has not been expanded yet — expand it from the template first"),
    (r"^还没有展开清单，没有可出的图$", r"The item list has not been expanded yet, so there is nothing to generate"),
    (r"^没有这些图：(.+)$", r"No such images: \1"),
    (r"^最多 (\d+) 次生图请求（(.+?)）；请求没变且图已在的会跳过$",
     r"Up to \1 image request(s) (\2); images whose request is unchanged and already on disk are skipped"),
    (r"^attempt 要是这张图成功出过的文件名之一$", r"attempt must be one of the file names this image was successfully generated as"),
    (r"^还没有可后处理的图——先出图，再挑选$", r"No images to post-process yet — generate them first, then pick"),
    (r"^还没有后处理产物，先跑后处理$", r"No post-processing outputs yet; run post-processing first"),
    (r"^后处理产物里没有可打包的文件$", r"The post-processing outputs contain no packable files"),
    # generate.py：逐张的失败原因（进任务结果与 items[].error）
    (r"^提示词为空$", r"The prompt is empty"),
    (r"^下载下来的图是空文件$", r"The downloaded image is an empty file"),
    (r"^等了 (\d+) 分钟还没出图（远端任务 (.+?)），先放弃这一张$", r"Waited \1 minutes with no image (remote task \2); giving up on this one"),
    # post.py：后处理运行记录里的 failed
    (r"^图片不存在：(.+)$", r"Image does not exist: \1"),
    (r"^图片读不出来：(.+)$", r"Could not read the image: \1"),
    (r"^画幅要写成 3:4 这样的形状，收到「(.+?)」$", r'The aspect ratio must look like 3:4; received "\1"'),
    (r"^图片写盘失败：(.+)$", r"Failed to write the image: \1"),
    (r"^(\S+) 量化后仍超过 (\d+) KB$", r"\1 still exceeds \2 KB after quantization"),
    (r"^叠字位置只认 top / bottom$", r"Text overlay position must be top / bottom"),
    (r"^没有可拼样片的图片$", r"No images to build a contact sheet from"),
    # 路由包装：一张都没出时任务记 failed（deps.all_failed_message）。里层最常见的是「模型未配置」，具体的写在前面
    (r"^(\d+) 张全部失败：(\w+) 模型未配置：(\w+): 没选适配器$", r"All \1 images failed: \2 model not configured: \3: no adapter selected"),
    (r"^(\d+) 张全部失败：(.+)$", r"All \1 images failed: \2"),
    # webui/uploads.py：按内容验图（图片集 / 单条视频 / 绘本 / 配乐参考图共用）；要排在 _ROUTER_RULES 里只翻前缀的那条前面
    (r"^不是可读的图片文件：认不出图片格式$", r"Not a readable image file: the image format could not be identified"),
]

# ---------------------------------------------------------------------------- 配乐（DESIGN §44）
#
# music/spec.py 的形状校验既单独出现（示例复制 / pick 的 400），也套在「改动没通过校验，没有写盘：」后面，
# 所以写成不带 ^$ 的片段，下面既进 _composed 也进单独规则。

_MUSIC_SPEC_ERRORS: list[tuple[str, str]] = [
    # 以全角括号收尾的几条只写括号前的部分——tr 会先翻前半段，括号里的补充说明是 ASCII 或在 EXACT 里
    (r"brief 要是对象", r"brief must be an object"),
    (r"music_params\.extra_params 要是对象", r"music_params.extra_params must be an object"),
    (r"music_params 要是对象", r"music_params must be an object"),
    (r"reference_images 要是数组", r"reference_images must be an array"),
    (r"reference_images\[(\d+)\] 只接受 jpg / png 参考图", r"reference_images[\1] only accepts jpg / png reference images"),
    (r"参考图最多 (\d+) 张，现在有 (\d+) 张", r"at most \1 reference images; there are \2"),
    (r"current「(.+?)」不在 attempts 里——只能采用出过的那几条",
     r'current "\1" is not in attempts — only tracks that were actually generated can be adopted'),
    (r"attempts 要是数组", r"attempts must be an array"),
    (r"attempts\[(\d+)\] 要是对象", r"attempts[\1] must be an object"),
    (r"attempts\[(\d+)\]\.(\w+) 要是文本", r"attempts[\1].\2 must be text"),
    (r"used_in 要是数组", r"used_in must be an array"),
    (r"used_in\[(\d+)\] 要是对象", r"used_in[\1] must be an object"),
    (r"used_in\[(\d+)\]\.module 只能是 (.+?)，收到「(.+?)」", r'used_in[\1].module must be one of \2; received "\3"'),
    (r"(brief\.\w+) 要是数组", r"\1 must be an array"),
    (r"(brief\.\w+|duration|count) 要是整数", r"\1 must be an integer"),
    (r"(brief\.\w+|duration|count) 要在 (\d+) 到 (\d+) 之间", r"\1 must be between \2 and \3"),
    (r"没有这个配乐：(.+)", r"No such music project: \1"),
]

# routers/music.py 的 400/404/409/413 与 cost_note；generate.py / use.py 里会进 HTTP 响应
# （或任务记录的失败原因）的人话
_MUSIC_ROUTER_RULES: list[tuple[str, str]] = [
    (r"^配乐 (\S+) 已经存在，换一个 id$", r"Music project \1 already exists; pick another id"),
    (r"^参考图只接受 jpg / png，收到的是 (.+)$", r"Reference images must be jpg / png; received \1"),
    (r"^一次只能出 (\d+) 到 (\d+) 条$", r"Only \1 to \2 tracks can be generated at a time"),
    (r"^当前的配乐来源不收参考图——在「高级」里删掉参考图，或换一个收参考图的来源$",
     r'The current music source takes no reference images — remove them under "Advanced", or switch to a source that takes them'),
    (r"^提示词为空——先把 brief 里的用途 / 体裁 / 情绪填上一项$",
     r"The prompt is empty — fill in at least one of use / genre / mood in the brief first"),
    (r"^(\d+) 次配乐请求（(.+?)），每次出 1 条；请求没变且已有采用的会跳过$",
     r"\1 music request(s) (\2), one track each; skipped when the request is unchanged and a track is already adopted"),
    (r"^attempt 要是这个配乐成功出过的文件名之一$", r"attempt must be one of the file names this project was successfully generated as"),
    # use.py（§3 投放）
    (r"^module 只能是 (.+?)，收到「(.+?)」$", r'module must be one of \1; received "\2"'),
    (r"^target 不能为空——先选一个目标项目$", r"target cannot be empty — pick a destination project first"),
    (r"^还没有采用的配乐——先生成，再在历次里点「采用」$",
     'No track has been adopted yet — generate first, then click "Adopt" on one of the takes'),
    (r"^slot 要是秒数（0 到 3600 之间的数）$", r"slot must be a number of seconds (between 0 and 3600)"),
    (r"^没有这个短剧项目：(.+)$", r"No such short-drama project: \1"),
    (r"^目标项目没接受这条配乐：(.+)$", r"The destination project rejected this track: \1"),
    (r"^目标绘本没接受这条配乐：(.+)$", r"The destination picture book rejected this track: \1"),
    (r"^目标绘本读不出来：(.+)$", r"The destination picture book could not be read: \1"),
    (r"^已放进《(.+?)》的配乐层$", r'Dropped into the music layer of "\1"'),
    # generate.py：逐条的失败原因（进任务结果与 attempts[].error）
    (r"^配乐来源说完成了，但没有给结果地址$", r"The music provider reported success but returned no result URL"),
    (r"^配乐来源返回失败$", r"The music provider returned a failure"),
    (r"^配乐来源没有返回结果$", r"The music provider returned no result"),
    (r"^下载下来的音频是空文件$", r"The downloaded audio is an empty file"),
    (r"^等了 (\d+) 分钟还没出配乐（远端任务 (.+?)），先放弃这一条$",
     r"Waited \1 minutes with no music (remote task \2); giving up on this track"),
    (r"^参考图只接受 jpg / png：(.+)$", r"Reference images must be jpg / png: \1"),
    (r"^(\S+) 不支持生成配乐——在「模型设置」里给「配乐」角色选一个会出音乐的来源$",
     '\\1 cannot generate music — pick a source that can for the "Music" role in Model settings'),
    # 路由包装：一条都没出时任务记 failed（deps.all_failed_message）。里层最常见的是「模型未配置」，具体的写在前面
    (r"^(\d+) 条配乐全部失败：(\w+) 模型未配置：(\w+): 没选适配器$", r"All \1 tracks failed: \2 model not configured: \3: no adapter selected"),
    (r"^(\d+) 条配乐全部失败：(.+)$", r"All \1 tracks failed: \2"),
    # use.py：目标 id 不合法（带 .. / 斜杠）统一按「没有这个目标」；短剧目标文件在但读不出来
    (r"^没有这个目标项目：(.+)$", r"No such destination project: \1"),
    (r"^目标项目读不出来：(.+)$", r"The destination project could not be read: \1"),
]

_ROUTER_RULES: list[tuple[str, str]] = [
    (r"^(entry|exit) 要是对象（positions / facing / gaze / hands / held_props / visible_state / same_as_previous）$",
     r"\1 must be an object (positions / facing / gaze / hands / held_props / visible_state / same_as_previous)"),
    (r"^(entry|exit) 里不认识的字段：(.+)$", r"Unknown fields in \1: \2"),
    (r"^(entry|exit)\.(\w+) 要是一句话（字符串）$", r"\1.\2 must be a sentence (string)"),
    (r"^(entry|exit)\.same_as_previous 要是字段名列表（只认 (.+?)）$", r"\1.same_as_previous must be a list of field names (only \2)"),
    (r"^axis 里不认识的字段：(.+)$", r"Unknown fields in axis: \1"),
    (r"^axis\.side 只能是 left / right，收到「(.+?)」$", r'axis.side must be left / right; received "\1"'),
    (r"^axis\.screen 里「(.+?)」的画面侧只能是 left / right$", r'In axis.screen the screen side of "\1" must be left / right'),
    (r"^readable_text\[(\d+)\] 要是对象（surface / text / language / position）$", r"readable_text[\1] must be an object (surface / text / language / position)"),
    (r"^readable_text\[(\d+)\]：载体（surface）和文字（text）都不能为空$", r"readable_text[\1]: neither surface nor text may be empty"),
    (r"^readable_text\[(\d+)\]\.language 只能是 (.+?)，收到「(.+?)」$", r'readable_text[\1].language must be one of \2; received "\3"'),
    (r"^variants\.(\S+?) 必须是对象：\{label, delta, asset, shots\}$", r"variants.\1 must be an object: {label, delta, asset, shots}"),
    (r"^variants\.(\S+?)\.delta（相对基底的差异）不能为空——只写换了什么：服装、发型状态、伤、湿污、伪装$",
     r"variants.\1.delta (the difference from the base look) cannot be empty — write only what changed: wardrobe, hair state, injuries, wet/dirty, disguise"),
    (r"^variants\.(\S+?)\.asset 只能是项目内的相对路径（content/assets/…）$", r"variants.\1.asset must be a relative path inside the project (content/assets/…)"),
    (r"^variants\.(\S+?)\.shots 必须是镜号列表$", r"variants.\1.shots must be a list of shot ids"),
    (r"^locks\[(\d+)\] 必须是对象：\{surface, shots, note\}$", r"locks[\1] must be an object: {surface, shots, note}"),
    (r"^locks\[(\d+)\]\.surface（锁面）不能为空——填要逐字进提示词的短语$",
     r"locks[\1].surface (the lock surface) cannot be empty — fill in the phrase that goes into the prompt verbatim"),
    (r'^locks\[(\d+)\]\.shots 指定镜时至少要选一镜，或写 "all"$', r'locks[\1].shots must list at least one shot, or be "all"'),
    (r'^locks\[(\d+)\]\.shots 只能是 "all" 或镜号列表$', r'locks[\1].shots must be "all" or a list of shot ids'),
    (r"^「(.+?)」没有变体：(.+)$", r'"\1" has no variant \2'),
    (r"^不认识的题材：(.+)$", r'Unknown genre "\1"'),
    (r"^不认识的形态：(.+)$", r'Unknown form "\1"'),
    (r"^没有这个交付包：(.+?)——只能下载 09_export 目录里已导出的 zip。$",
     r'No such delivery package "\1" — only zips already exported into the 09_export directory can be downloaded.'),
    # export.py：ai_disclosure.md 的溯源行（括号里的口径「按账本 / 按导出时的设置」由 tr 的「人话（原文）」分支单独翻）
    (r"^- 剧本 / 分镜起草：(.+)$", r"- Script / storyboard drafting: \1"),
    (r"^- 首帧：(.+)$", r"- First frames: \1"),
    (r"^- 视频：(.+)$", r"- Videos: \1"),
    # export.py：ai_disclosure.md 的声音与配乐行。声线后的「（语速 x，音调 y）」由 tr 的「人话（原文）」分支单独翻；
    # 角色名与配乐 label 是用户内容，md 里用「」括着，这里原样留在引号里。具体的写在通用的「  - 「…」：…」前面
    (r"^- 旁白：本集没有旁白$", r"- Narration: none in this episode"),
    (r"^- 旁白：(.+)$", r"- Narration: \1"),
    (r"^  - 第 (\d+) 句（(\S+?)）换声线：(.+)$", r"  - Line \1 (\2) uses its own voice: \3"),
    (r"^  - 第 (\d+) 句换声线：(.+)$", r"  - Line \1 uses its own voice: \2"),
    (r"^- 台词配音：(.+)$", r"- Dialogue dubbing: \1"),
    (r"^  - 「(.+)」（(\S+?)，本集没用到）：(.+)$", r'  - "\1" (\2, not used in this episode): \3'),
    (r"^  - 「(.+)」（(\S+?)）：(.+)$", r'  - "\1" (\2): \3'),
    (r"^  - (\S+?)（本集没用到）：(.+)$", r"  - \1 (not used in this episode): \2"),
    (r"^- 配乐：本集没有配乐$", r"- Music: none in this episode"),
    (r"^- 配乐：(\d+) 条$", r"- Music: \1 tracks"),
    (r"^  - 「配乐 (\d+)」：(.+)$", r"  - Track \1: \2"),
    (r"^  - 「(.+)」：(.+)$", r'  - "\1": \2'),
    (r"^  - ([\w.-]+)：(.+)$", r"  - \1: \2"),
    (r"^语速 (\S+)，音调 (\S+)$", r"rate \1, pitch \2"),
    (r"^语速 (\S+)$", r"rate \1"),
    (r"^音调 (\S+)$", r"pitch \1"),
    (r"^只接受 (.+?) 音频，收到的是 (.+)$", r"Only \1 audio is accepted; received \2"),
    (r"^音频数据不是合法的 base64：(.+)$", r"Audio data is not valid base64: \1"),
    (r"^音频超过 (\d+)MB$", r"Audio exceeds \1MB"),
    # ---- 单集契约 / 系列 / 剧本层（DESIGN §32）：contract.py 的形状校验、scripts.py / series.py 的 400/404
    (r"^episode_contract 要是对象（premise / hook / cliffhanger / must_pay_off / carry_over）$",
     r"episode_contract must be an object (premise / hook / cliffhanger / must_pay_off / carry_over)"),
    (r"^episode_contract 里不认识的字段：(.+)$", r"Unknown fields in episode_contract: \1"),
    (r"^episode_contract\.(\w+) 要是一句话（字符串）$", r"episode_contract.\1 must be a sentence (string)"),
    (r"^episode_contract\.(\w+) 要是字符串列表（一行一条）$", r"episode_contract.\1 must be a list of strings (one per line)"),
    (r"^episode_contract\.(\w+) 里每一条都要是字符串$", r"Every item in episode_contract.\1 must be a string"),
    (r"^series 要是对象（id / episode_no）$", r"series must be an object (id / episode_no)"),
    (r"^series 里不认识的字段：(.+)$", r"Unknown fields in series: \1"),
    (r"^series\.id 不能为空$", r"series.id cannot be empty"),
    (r"^series\.id 只能用英文、数字、下划线、点、短横线，且以英文/数字/下划线开头$",
     r"series.id may only contain letters, digits, underscores, dots and hyphens, and must start with a letter, digit or underscore"),
    (r"^series\.episode_no 要是正整数$", r"series.episode_no must be a positive integer"),
    (r"^这个项目没有填系列（series\.id）——在概览页「单集契约」卡里填好再交接$",
     "This project has no series (series.id) — fill it in on the overview page's \"Episode contract\" card, then hand off"),
    (r"^没有这个系列：(.+)$", r"No such series: \1"),
    (r"^项目 (\S+?) 还没有剧本——先出剧本，再拆镜$", r"Project \1 has no screenplay yet — write the screenplay first, then break it into shots"),
    (r"^项目 (\S+?) 没有剧本——用「两步起草」先出剧本$", r'Project \1 has no screenplay — use "two-step drafting" to write one first'),
    (r"^剧本至少要有一场（scenes 列表不能为空）$", r"The screenplay needs at least one scene (the scenes list cannot be empty)"),
    (r"^scenes 里每一场都要是对象（id / heading / beats / dialogue）$", r"Every scene in scenes must be an object (id / heading / beats / dialogue)"),
    (r"^出剧本失败：(.+)$", r"Screenplay failed: \1"),
    (r"^出剧本遇到没预料到的错误（请把这条完整信息发给开发者）：(.+)$",
     r"Screenplay hit an unexpected error (please send this full message to the developer): \1"),
    (r"^剧本写盘失败：(.+)$", r"Failed to write the screenplay to disk: \1"),
    (r"^模型没有按剧本结构返回（缺 scenes 列表）——再试一次，或换文本模型$",
     r"The model did not return the screenplay structure (no scenes list) — try again, or switch the text model"),
    # ---- 短剧项目状态机与文件边界（发第一个 tag 前的修复）：归档 / 恢复、建项目的 id 校验、并发保存、概览的 stale 提示
    (r"^这个 id 在账本里还有旧的制作记录（(\S+?)）——换一个 id，或先从归档把它恢复回来$",
     r"The ledger still has an old production record for this id (\1) — use another id, or restore it from the archive first"),
    (r"^项目文件正在被另一次保存改着（(.+?)）——等它完成，刷新后再改$",
     r"The project file is being changed by another save (\1) — wait for it to finish, then refresh and edit again"),
    (r"^暂不支持恢复这个模块的归档：(.+)$", r"Restoring archives of this module is not supported yet: \1"),
    (r"^没有这条归档：(.+)$", r"No such archive: \1"),
    (r"^归档里的项目文件读不出来：(.+)$", r"The project file in the archive could not be read: \1"),
    (r"^归档里的项目 id 不合法：(.+)$", r"The project id in the archive is invalid: \1"),
    (r"^没有恢复：项目 (\S+?) 已经存在——先把现有的同 id 项目归档或改名$",
     r"Not restored: project \1 already exists — archive or rename the existing project with the same id first"),
    (r"^没有恢复：同 id 的剧本文件已经存在——先把现有的同 id 项目归档或改名$",
     r"Not restored: a screenplay file with the same id already exists — archive or rename the existing project with the same id first"),
    (r"^没有恢复：同 id 的锚图目录已经存在——先把现有的同 id 项目归档或改名$",
     r"Not restored: an anchor-image directory with the same id already exists — archive or rename the existing project with the same id first"),
    (r"^没有恢复：同 id 的输出目录已经存在——先把现有的同 id 项目归档或改名$",
     r"Not restored: an output directory with the same id already exists — archive or rename the existing project with the same id first"),
    (r"^没有恢复：账本里已经有同 id 的制作记录——先把现有的同 id 项目归档或改名$",
     r"Not restored: the ledger already has a production record with the same id — archive or rename the existing project with the same id first"),
    (r"^草稿里的 id「(.+?)」跟要保存的项目 id「(.+?)」对不上——两边要一致$",
     r'The id in the draft ("\1") does not match the project id to save under ("\2") — they must be the same'),
    (r"^草稿里的 output_root「(.+?)」没有以项目 id「(.+?)」结尾——产物目录必须跟项目 id 对应$",
     r'The output_root in the draft ("\1") does not end with the project id ("\2") — the output directory must correspond to the project id'),
    (r"^1 镜待审；1 镜的检查结果已作废（文件变了或上游重拍过），点「生成首帧」会重新做技术检查，不花钱$",
     r'1 shot pending review; 1 shot whose check result is stale (file changed or upstream retaken) — "Generate first frames" reruns the technical check, costs nothing'),
    (r"^1 镜待审；(\d+) 镜的检查结果已作废（文件变了或上游重拍过），点「生成首帧」会重新做技术检查，不花钱$",
     r'1 shot pending review; \1 shots whose check results are stale (file changed or upstream retaken) — "Generate first frames" reruns the technical check, costs nothing'),
    (r"^(\d+) 镜待审；1 镜的检查结果已作废（文件变了或上游重拍过），点「生成首帧」会重新做技术检查，不花钱$",
     r'\1 shots pending review; 1 shot whose check result is stale (file changed or upstream retaken) — "Generate first frames" reruns the technical check, costs nothing'),
    (r"^(\d+) 镜待审；(\d+) 镜的检查结果已作废（文件变了或上游重拍过），点「生成首帧」会重新做技术检查，不花钱$",
     r'\1 shots pending review; \2 shots whose check results are stale (file changed or upstream retaken) — "Generate first frames" reruns the technical check, costs nothing'),
    (r"^1 镜待审；有台词的镜可以先跑台词识别闸；1 镜的检查结果已作废（文件变了或上游重拍过），点「生成视频」会重新做技术检查，不花钱$",
     r'1 shot pending review; shots with lines can run the dialogue ASR gate first; 1 shot whose check result is stale (file changed or upstream retaken) — "Generate videos" reruns the technical check, costs nothing'),
    (r"^1 镜待审；有台词的镜可以先跑台词识别闸；(\d+) 镜的检查结果已作废（文件变了或上游重拍过），点「生成视频」会重新做技术检查，不花钱$",
     r'1 shot pending review; shots with lines can run the dialogue ASR gate first; \1 shots whose check results are stale (file changed or upstream retaken) — "Generate videos" reruns the technical check, costs nothing'),
    (r"^(\d+) 镜待审；有台词的镜可以先跑台词识别闸；1 镜的检查结果已作废（文件变了或上游重拍过），点「生成视频」会重新做技术检查，不花钱$",
     r'\1 shots pending review; shots with lines can run the dialogue ASR gate first; 1 shot whose check result is stale (file changed or upstream retaken) — "Generate videos" reruns the technical check, costs nothing'),
    (r"^(\d+) 镜待审；有台词的镜可以先跑台词识别闸；(\d+) 镜的检查结果已作废（文件变了或上游重拍过），点「生成视频」会重新做技术检查，不花钱$",
     r'\1 shots pending review; shots with lines can run the dialogue ASR gate first; \2 shots whose check results are stale (file changed or upstream retaken) — "Generate videos" reruns the technical check, costs nothing'),
    # 同上（审查后补）：只剩账本记录的归档（项目文件和输出目录已手动搬回）恢复时的几种拒绝；最后一条是兜底，放在具体句子后面
    (r"^归档里的账本记录读不出来：(.+)$", r"The ledger record in the archive could not be read: \1"),
    (r"^没有恢复：归档里只剩账本记录，项目 (\S+?) 的项目文件或输出目录还不在原位——先把它们搬回来，再恢复账本记录$",
     r"Not restored: the archive only holds the ledger record, and the project file or output directory of \1 is not back in place yet — move them back first, then restore the ledger record"),
    (r"^没有恢复：现在的项目 (\S+?) 跟归档里的制作记录对不上（不是同一份内容）——点「导入已有产物」重新登记$",
     r'Not restored: the current project \1 does not match the production record in the archive (not the same content) — click "Import existing outputs" to register it again'),
    # 回收站（genkit.trash）：通用模块恢复被拦的三种原因、有文件被占用时删除 / 恢复 / 彻底删除整体没动
    (r"^没有恢复：项目 (\S+?) 已经存在——先删除现在这个同 id 的项目（它也会进回收站），再恢复这一份$",
     r"Not restored: project \1 already exists — delete the current project with the same id first (it goes to the Trash too), then restore this one"),
    (r"^没有恢复：同 id 的产物目录已经存在（(.+?)）——先把它挪走或改名，再恢复这一份$",
     r"Not restored: an output directory with the same id already exists (\1) — move or rename it first, then restore this one"),
    (r"^没有恢复：同 id 的素材目录已经存在（(.+?)）——先把它挪走或改名，再恢复这一份$",
     r"Not restored: an asset directory with the same id already exists (\1) — move or rename it first, then restore this one"),
    (r"^有文件正被占用，没删除：(.+?)——等正在进行的生成 / 导出结束，或关掉正在打开它的程序再删$",
     r"A file is in use, so nothing was deleted: \1 — wait for the running generation / export to finish, or close the program that has it open, then delete again"),
    (r"^有文件正被占用，没恢复：(.+?)——关掉正在打开归档里文件的程序再试$",
     r"A file is in use, so nothing was restored: \1 — close the program that has a file from the archive open, then try again"),
    (r"^有文件正被占用，没删除：(.+?)——关掉正在打开归档里文件的程序再试$",
     r"A file is in use, so nothing was deleted: \1 — close the program that has a file from the archive open, then try again"),
    (r"^有文件正被占用，没删干净：(.+?)——关掉正在打开归档里文件的程序再试$",
     r"A file is in use, so the archive was not fully deleted: \1 — close the program that has a file from the archive open, then try again"),
    (r"^没有恢复：(.+)$", r"Not restored: \1"),
    # compare.py：一张候选都没出时任务记 failed。里层最常见的是「模型未配置」，具体的写在前面
    (r"^(\d+) 个候选全部失败：(\w+) 模型未配置：(\w+): 没选适配器$", r"All \1 candidates failed: \2 model not configured: \3: no adapter selected"),
    (r"^(\d+) 个候选全部失败：(.+)$", r"All \1 candidates failed: \2"),
]


# ---------------------------------------------------------------------------- AI 播客（DESIGN §50）：
#      podcast/lint.py 的真人感告警（整句 + 问题卡两半）、routers/podcast.py 的 400/404/409 与 cost_note、
#      craft.py / audio.py 里会进任务记录的失败原因、spec.py 的项目文件校验。
_PODCAST_LINT_RULES: list[tuple[str, str]] = [
    *_carded((r"没有冷开场环节", r"there is no cold-open segment"),
             (r"加一个环节：从一个具体场景或对话中段切入，不自我介绍",
              r"add one: cut in from a concrete scene or mid-conversation, no self-introduction"), head="podcast"),
    *_carded((r"没有开场环节", r"there is no intro segment"),
             (r"加一个环节：介绍人和今天聊什么，一句话说清为什么值得听",
              r"add one: introduce the people and the topic, one sentence on why it is worth listening"), head="podcast"),
    *_carded((r"没有收尾环节", r"there is no outro segment"),
             (r"加一个环节：留一句话和一个互动号召，不总结全篇",
              r"add one: leave one line and a call to action, do not recap the episode"), head="podcast"),
    # 环节种类名进了句子（spec.SEGMENT_KIND_LABELS 是中文）：先按种类各出一条，英文名跟 podcast/spec.LABELS["en"]["kinds"] 一致；
    # 通用的那条留给不认识的种类
    *[rule for zh, en in (("冷开场", "Cold open"), ("开场", "Intro"), ("话题", "Topic"), ("听友问答", "Listener Q&A"), ("收尾", "Outro"))
      for rule in _carded((rf"第一个环节是「{zh}」", rf'the first segment is "{en}"'),
                          (r"冷开场或开场要放在最前面，听众前 30 秒决定走不走",
                           r"the cold open or intro must come first; listeners decide in the first 30 seconds"), head="segment")],
    *_carded((r"第一个环节是「(.+?)」", r'the first segment is "\1"'),
             (r"冷开场或开场要放在最前面，听众前 30 秒决定走不走",
              r"the cold open or intro must come first; listeners decide in the first 30 seconds"), head="segment"),
    *_carded((r"听友问答环节没有一条听众问题", r"the listener Q&A segment has no listener questions"),
             (r"填两三条真的会被问的问题（带称呼），起草才有东西可答",
              r"add two or three questions people would actually ask (with a name), so the draft has something to answer"), head="segment"),
    *_carded((r"这个环节还没有台词", r"this segment has no lines yet"),
             (r"点「起草整期」或「重写这一段」，或者手写几句",
              r'click "Draft episode" or "Rewrite this segment", or write a few lines by hand'), head="segment"),
    *_carded((r"「(.+?)」的台词里有书面语「(.+?)」", r'"\1" uses written-register words "\2"'),
             (r"真人聊天不这么说；删掉连接词，直接说事",
              r"nobody talks like that; drop the connectives and say the thing"), head="segment"),
    *_carded((r"「(.+?)」一句 (\d+) 字", r'"\1" has a single line of \2 characters'),
             (r"约 (\d+) 秒没人插话，真人对谈里别人早接话了；拆开，让另一个人接一句或追问",
              r"about \1 seconds with nobody cutting in; in a real conversation someone would have; split it and let the other person respond or push back"), head="segment"),
    *_carded((r"cue「(.+?)」不在清单里", r'cue "\1" is not in the list'),
             (r"换成「(.+?)」里的一个，或留空", r'use one of "\1", or leave it empty'), head="segment"),
    *_carded((r"(\d+) 句每一句都换人", r"\1 lines and every single one switches speaker"),
             (r"机械轮流像排队发言；让一个人连说两三句、另一个人只接一个「嗯」或打断",
              r'mechanical turn-taking sounds like people queueing to speak; let one person say two or three lines while the other only adds an "mm" or interrupts'), head="segment"),
    *_carded((r"「(.+?)」说了 (\d+)% 的字", r'"\1" speaks \2% of the words'),
             (r"一人独大另一个人就成了捧哏；把一部分例子和观点分给别人，或让别人反驳",
              r"when one host dominates the other becomes a sidekick; hand some examples and opinions to the others, or let them push back"), head="podcast"),
    *_carded((r"整期没有一处反对或分歧", r"nobody disagrees anywhere in the episode"),
             (r"真人对谈总有人不同意；加一处明确的不同意见，而且不要马上和解",
              r"real conversations always have someone disagreeing; add one clear disagreement and do not resolve it right away"), head="podcast"),
    *_carded((r"填充词（嗯 / 啊 / 那个 / 然后…）占了 (\d+)%", r"filler words (mm / ah / that / then…) make up \1%"),
             (r"TTS 念出来像结巴；留在接话处一两个，其余删掉",
              r"TTS will read it as stuttering; keep one or two where someone picks up the thread and delete the rest"), head="podcast"),
    *_carded((r"一个填充词都没有", r"there is not a single filler word"),
             (r"略显播音腔；在接话和被打断的地方加一两个「嗯」「对」",
              r'it sounds slightly like a newsreader; add an "mm" or "right" where someone picks up or gets interrupted'), head="podcast"),
    *_carded((r"整段没有一个具体例子", r"the whole segment has no concrete example"),
             (r"没有数字，也没有「有一次 / 上周 / 我记得 / 比如」；真人播客靠具体经历撑，不靠观点，从素材里拿一件事讲细",
              r'no numbers and no "one time / last week / I remember / for example"; real podcasts run on concrete experience, not opinions — take one thing from the materials and tell it in detail'), head="segment"),
    *_carded((r"有听众问题但没有一句标成回答（kind=answer）", r"there are listener questions but no line is marked as an answer (kind=answer)"),
             (r"把回答的句子标出来，检查和节目单都靠它",
              r"mark the answering lines; the checks and the show notes depend on it"), head="segment"),
    *_carded((r"回答「(.+?)」只有 (\d+) 字", r'the answer "\1" is only \2 characters'),
             (r"听众要的是结论加原因，不是一句敷衍；先给结论，再讲一个为什么",
              r"listeners want a conclusion plus a reason, not a brush-off; give the conclusion first, then one why"), head="segment"),
    *_carded((r"回答开头「(.+?)…」在复述问题", r'the answer opens with "\1…", restating the question'),
             (r"直接给结论，问题刚念过听众记得",
              r"give the conclusion directly; the question was just read out and listeners remember it"), head="segment"),
    *_carded((r"收尾在总结全篇（「(.+?)」）", r'the outro recaps the whole episode ("\1")'),
             (r"真人播客结尾只留一句和互动号召；删掉回顾，留下一期预告",
              r"real podcasts end on one line and a call to action; drop the recap and leave a teaser for next time"), head="segment"),
    *_carded((r"预计 (\S+) 分钟，目标 (\d+) 分钟，偏短", r"estimated \1 minutes against a target of \2, too short"),
             (r"让起草多写一个话题或补一段亲历", r"have the draft add one more topic or a personal story"), head="podcast"),
    *_carded((r"预计 (\S+) 分钟，目标 (\d+) 分钟，偏长", r"estimated \1 minutes against a target of \2, too long"),
             (r"砍掉最弱的一个话题，或把重复的观点删掉", r"cut the weakest topic, or delete the repeated opinions"), head="podcast"),
    *_carded((r"「(.+?)」和「(.+?)」绑的是同一个音色", r'"\1" and "\2" are bound to the same voice'),
             (r"听众分不清谁在说；给其中一位换一个音色", r"listeners cannot tell who is speaking; give one of them a different voice"), head="podcast"),
    *_carded((r"「(.+?)」还没绑音色", r'"\1" has no voice bound yet'),
             (r"会用占位音色出声，能听但和别人不好区分；去音色库挑一个",
              r"a placeholder voice will be used — audible, but hard to tell apart from the others; pick one in the voice library"), head="podcast"),
    *_carded((r"主播都还没绑音色", r"none of the hosts has a voice bound yet"),
             (r"会按语言轮流分配占位音色，能出声；正式出片前去音色库给每个人挑一个",
              r"placeholder voices will be assigned per language in rotation, which is audible; pick one per host in the voice library before the real release"), head="podcast"),
    # 音色档案引用坏了（audio.host_profiles 的 missing / no_tts 标记）
    *_carded((r"主播「(.+?)」绑的音色档案「(.+?)」已不存在", r'the voice profile "\2" bound to host "\1" no longer exists'),
             (r"去音色库重新挑一个，或先留空用占位音色", r"pick another one in the voice library, or leave it empty to use a placeholder voice"), head="podcast"),
    *_carded((r"主播「(.+?)」绑的档案「(.+?)」只有文字描述，不能出声", r'the profile "\2" bound to host "\1" is description-only and cannot speak'),
             (r"换一个带 TTS 音色的档案", r"switch to a profile that has a TTS voice"), head="podcast"),
]

# 形式名进了校验句（spec.FORMATS[...]["name"] 是中文）：按形式各出一条，英文名用 FORMATS[...]["english"]；通用的留给兜底
_PODCAST_FORMAT_NAMES = (("单人独白", "Solo"), ("双人对谈", "Two hosts"), ("访谈", "Interview"), ("圆桌", "Roundtable"))

_PODCAST_SPEC_ERRORS: list[tuple[str, str]] = [
    *[(rf"形式「{zh}」要 (\d+) 人，现在有 (\d+) 人", rf'format "{en}" needs \1 host(s); there are \2') for zh, en in _PODCAST_FORMAT_NAMES],
    *[(rf"形式「{zh}」要 (\d+) 到 (\d+) 人，现在有 (\d+) 人", rf'format "{en}" needs \1 to \2 hosts; there are \3') for zh, en in _PODCAST_FORMAT_NAMES],
    (r"(\S+) 要是文本", r"\1 must be text"),
    (r"(\S+) 要是数字", r"\1 must be a number"),
    (r"(\S+) 要在 ([\d.]+) 到 ([\d.]+) 之间", r"\1 must be between \2 and \3"),
    (r"(\S+) 要是数组（或用顿号分隔的一行）", r"\1 must be an array (or one line separated by commas)"),
    (r"(\S+) 只能是项目内的相对路径（content/assets/…）", r"\1 must be a relative path inside the project (content/assets/…)"),
    (r"(\S+) 要是音频文件：(.+)", r"\1 must be an audio file: \2"),
    (r"brief 要是对象（(.+?)）", r"brief must be an object (\1)"),
    (r"(\S+) 要是音色库档案 id 或 \{engine, voice, rate, pitch\} 对象", r"\1 must be a voice-library profile id or an {engine, voice, rate, pitch} object"),
    (r"hosts 要是数组（主播列表）", r"hosts must be an array (the host list)"),
    (r"(hosts\[\d+\]) 要是对象（(.+?)）", r"\1 must be an object (\2)"),
    (r"(\S+) 只能用英文、数字、下划线、点、短横线", r"\1 may only contain letters, digits, underscore, dot and hyphen"),
    (r"主播 id 重复了：(\S+)——每个人一个 id，台词的说话人靠它对上", r"duplicate host id: \1 — one id per host; lines are matched to speakers by it"),
    (r"形式「(.+?)」要 (\d+) 人，现在有 (\d+) 人", r'format "\1" needs \2 host(s); there are \3'),
    (r"形式「(.+?)」要 (\d+) 到 (\d+) 人，现在有 (\d+) 人", r'format "\1" needs \2 to \3 hosts; there are \4'),
    (r"主播最多 (\d+) 人", r"at most \1 hosts"),
    (r"访谈要至少一位 host 和一位 guest——在主播表里把角色选对", r"an interview needs at least one host and one guest — set the roles in the host table"),
    (r"(\S+) 要是数组（听众问题）", r"\1 must be an array (listener questions)"),
    (r"(\S+) 要是对象（from / text）", r"\1 must be an object (from / text)"),
    (r"(\S+) 要是数组（台词）", r"\1 must be an array (lines)"),
    (r"(\S+) 要是对象（who / text / cue / kind）", r"\1 must be an object (who / text / cue / kind)"),
    (r"(\S+) 的说话人「(.+?)」不在主播表里——从主播表里选一个（用 id 或名字），或者先把这个人加进主播表",
     r'the speaker "\2" of \1 is not in the host table — pick one from the host table (by id or name), or add this person first'),
    (r"segments 要是数组（环节列表）", r"segments must be an array (the segment list)"),
    (r"(\S+) 要是对象（id / kind / title / goal / beats / questions / lines）", r"\1 must be an object (id / kind / title / goal / beats / questions / lines)"),
    (r"环节 id 重复了：(\S+)——每个环节一个 id，告警和重写都靠它对上", r"duplicate segment id: \1 — one id per segment; warnings and rewrites are matched by it"),
    (r"music 要是对象（(.+?)）", r"music must be an object (\1)"),
    (r"pause 要是对象（line / segment，单位秒）", r"pause must be an object (line / segment, in seconds)"),
    (r"draft 要是对象", r"draft must be an object"),
    (r"没有这期播客：(.+)", r"No such podcast: \1"),
    # target_minutes 传 3.7 现在跟 "3.7" 一样拒（「X 要是整数」）、内联档案的 engine 乱写 400（「X 只能是 … 收到「…」」）：
    # 这两种句子前面别的模块已经配了通用规则，不重复加
]

_PODCAST_BUSY_NAMES = (("起草", "drafting"), ("合成", "synthesizing"), ("审稿", "reviewing"), ("导出", "exporting"))


def _podcast_busy_delete_rules() -> list[tuple[str, str]]:
    """「这期播客正在起草 / 合成，等它结束再删」：正在跑的任务种类按固定顺序用「 / 」拼，枚举全部有序子集。"""

    out: list[tuple[str, str]] = []
    for size in range(len(_PODCAST_BUSY_NAMES), 0, -1):
        for combo in combinations(_PODCAST_BUSY_NAMES, size):
            zh = " / ".join(re.escape(z) for z, _ in combo)
            en = " / ".join(e for _, e in combo)
            out.append((rf"^这期播客正在{zh}，等它结束再删$", f"This podcast is busy ({en}) — wait for it to finish, then delete"))
    return out


_PODCAST_ROUTER_RULES: list[tuple[str, str]] = [
    *_podcast_busy_delete_rules(),
    (r"^产物目录有文件正被占用，没归档：(.+?)——等合成 / 导出结束，或关掉正在播放它的程序再删$",
     r"A file in the output folder is in use, so nothing was archived: \1 — wait for synthesis / export to finish, or close the program playing it, then delete again"),
    # 「时长为 0」是自家写的中文原因（图片改了音频后缀、文本文件叫 .flac 时 ffprobe 读得出但没有时长），要排在把原因
    # 原样放回去的通用句前面——否则英文界面上就剩一截中文
    (r"^上传的文件不是能读的音频（(.+?)）：时长为 0——换一个 mp3 / wav / m4a 试试$",
     r"The uploaded file is not readable audio (\1): its duration is 0 — try another mp3 / wav / m4a"),
    (r"^上传的文件不是能读的音频（(.+?)）：(.+?)——换一个 mp3 / wav / m4a 试试$",
     r"The uploaded file is not readable audio (\1): \2 — try another mp3 / wav / m4a"),
    (r"^这个音频文件是空的（合成没写完整）——再点一次「合成整期」会重合成它$",
     r'This audio file is empty (synthesis did not finish writing it) — click "Synthesize the episode" once more and it will be regenerated'),
    # 合成前拦下坏掉的音色档案引用
    (r"^没开始合成：主播「(.+?)」绑的音色档案「(.+?)」已不存在——去音色库重新挑一个，或把这位主播的音色清空（用占位音色）再合成$",
     r'Synthesis not started: the voice profile "\2" bound to host "\1" no longer exists — pick another one in the voice library, '
     r"or clear this host's voice (to use a placeholder voice) and synthesize again"),
    (r"^没开始合成：主播「(.+?)」绑的档案「(.+?)」只有文字描述，不能出声——换一个带 TTS 音色的档案，或把这位主播的音色清空（用占位音色）再合成$",
     r'Synthesis not started: the profile "\2" bound to host "\1" is description-only and cannot speak — switch to a profile that has a TTS voice, '
     r"or clear this host's voice (to use a placeholder voice) and synthesize again"),
    # audio.py：坏文件 / 读不出的音乐（进任务结果的 mix_error 与合成失败原因）
    (r"^(\S+) 的音频探不出时长——文件可能没写完整$", r"\1: could not read the audio duration — the file may be incomplete"),
    (r"^环节 (\S+) 第 (\d+) 句的音频文件损坏（(\d+) 字节）——再点一次「合成整期」会只重合成这一句$",
     r'The audio file for line \2 of segment \1 is damaged (\3 bytes) — click "Synthesize the episode" once more and only this line will be regenerated'),
    *[(rf"^{zh}音乐文件读不出（(.+?)）：时长为 0——换一个文件，或在「音乐」里清掉这个槽$",
       rf'The {en} music file could not be read (\1): its duration is 0 — use another file, or clear this slot under "Music"')
      for zh, en in (("片头", "intro"), ("垫乐", "bed"), ("片尾", "outro"))],
    *[(rf"^{zh}音乐文件读不出（(.+?)）：(.+?)——换一个文件，或在「音乐」里清掉这个槽$",
       rf'The {en} music file could not be read (\1): \2 — use another file, or clear this slot under "Music"')
      for zh, en in (("片头", "intro"), ("垫乐", "bed"), ("片尾", "outro"))],
    # audio.host_profiles 的音色标签（详情 hosts[].voice_label）
    (r"^占位音色 (\S+)$", r"placeholder voice \1"),
    (r"^档案「(.+?)」已不存在，暂用占位音色 (\S+)$", r'profile "\1" no longer exists; using placeholder voice \2 for now'),
    (r"^档案「(.+?)」只有描述，暂用占位音色 (\S+)$", r'profile "\1" is description-only; using placeholder voice \2 for now'),
    # 起草任务失败原因（storyvideo/scriptgen.py 的 ScriptGenError，任务记录里带异常类名前缀）
    (r"^(ScriptGenError: )?还没有可用的剧本文本模型：在「模型设置」里给「剧本文本」选一个来源并填密钥（或在 \.env 里配 (.+?)）。$",
     r'\1No script text model is available yet: pick a source for "Script text" in Model settings and enter its key (or set \2 in .env).'),
    (r"^播客 (\S+) 已经存在，换一个 id$", r"Podcast \1 already exists; pick another id"),
    (r"^主播表是空的——先加至少一位主播（名字 \+ 人设），稿子才知道谁在说$",
     r"The host table is empty — add at least one host (name + persona) so the script knows who is speaking"),
    (r"^还没有可用的文本模型：在「模型设置」里给「剧本文本」选一个来源并填密钥$",
     r'No text model is available yet: pick a source for "Script text" in Model settings and enter its key'),
    (r"^没有这个环节：(.+)$", r"No such segment: \1"),
    (r"^稿子里还没有台词——先起草或手写几句，再合成$", r"The script has no lines yet — draft or write a few lines first, then synthesize"),
    (r"^(\d+) 句走 edge-tts（免费）\+ 本机混音，不花钱；文本和音色没变的句子会跳过$",
     r"\1 line(s) via edge-tts (free) + local mixing, no cost; lines whose text and voice are unchanged are skipped"),
    (r"^(\d+) 句语音合成（(.+?)），没变的句子会跳过；混音在本机$",
     r"\1 line(s) of speech synthesis (\2); unchanged lines are skipped; mixing runs locally"),
    (r"^slot 只能是 (.+?)，收到「(.+?)」$", r'slot must be one of \1; received "\2"'),
    (r"^只接受音频文件（(.+?)），收到的是 (.+)$", r"Only audio files are accepted (\1); received \2"),
    (r"^还没有这份文件——先合成一次$", r"This file does not exist yet — synthesize once first"),
    # craft.py：起草失败原因（进任务记录）
    (r"^模型没有按稿子结构返回（缺 segments 列表）——再试一次，或换文本模型$",
     r"The model did not return the script structure (no segments list) — try again, or switch the text model"),
    (r"^模型在环节 (\S+) 里让「(.+?)」说话，但主播表里没有这个人——重新起草，或把这个人加进主播表$",
     r'The model had "\2" speak in segment \1, but there is no such host — draft again, or add this person to the host table'),
    (r"^模型没有返回环节 (\S+)——再试一次$", r"The model did not return segment \1 — try again"),
    # audio.py：合成 / 混音失败原因
    (r"^(\S+) 没合成出音频文件$", r"\1 produced no audio file"),
    (r"^环节 (\S+) 第 (\d+) 句还没合成——先合成再混音$", r"Line \2 of segment \1 has not been synthesized — synthesize before mixing"),
    (r"^混音：跑不起 (\S+)：(.+)$", r"Mixing: could not run \1: \2"),
    # (?s)：后面接的是 ffmpeg stderr 的尾巴，几乎总是多行；不加的话 . 不跨行、整句对不上，英文界面的 mix_error 原样是中文
    (r"(?s)^混音失败：(.+)$", r"Mixing failed: \1"),
    (r"^没找到 ffmpeg——逐句音频已经出了，装好 ffmpeg 再点一次「合成」就能混成一条$",
     r'ffmpeg was not found — the per-line audio is done; install ffmpeg and click "Synthesize" once more to mix it into one file'),
    (r"^混音没有产出文件$", r"Mixing produced no file"),
    (r"^(\d+) 句没合成出来，没有混音$", r"\1 line(s) failed to synthesize, so nothing was mixed"),
]


# ---------------------------------------------------------------------------- 剧本工艺清单的静态判据（docs/script-craft-gap.md）：
#      lint.py 的带字物件 / 生产说明 / 一镜多运镜 / 冲击后无反应 / 旁白两条，screenplay.py 的台词超长 / 开口人数
_CRAFT_LINT_RULES: list[tuple[str, str]] = [
    *_carded((r"正文里有文字载体「(.+?)」，但没声明可读文字，也没写无文字", r'the text mentions a text carrier "\1" but declares neither readable text nor no-text'),
             (r"模型会自己编字，中文字形常乱；用 readable_text 声明该出现的字，或在美术里写明载体上无可辨文字",
              r"the model will invent lettering and Chinese glyphs often come out garbled; declare the intended text in readable_text, or state in the art that the carrier shows no legible text")),
    *_carded((r"正文里有生产说明「(.+?)」", r'the text contains production notes "\1"'),
             (r"那是写给人的，模型会把它当画面内容画进去或念出来；从提示词里删掉，要交代的写进 design_notes",
              r"those are for people; the model will paint or speak them as content; remove them from the prompt and put what needs saying in design_notes")),
    *_carded((r"一镜里同时写了「(.+?)」这几种运镜", r'one shot asks for several camera moves at once: "\1"'),
             (r"模型会二选一或来回晃；挑一个，另一个挪到下一镜",
              r"the model will pick one or wobble between them; keep one and move the other to the next shot")),
    *_carded((r"这一镜是「(.+?)」，下一镜 (\S+) 是「(.+?)」，中间没有反应镜", r'this shot is "\1" and the next shot \2 is "\3", with no reaction shot between them'),
             (r"一句重话说完，观众要看到听的人身上的表演（愣住 / 手上一停 / 别开脸 / 退一步），一个形容词顶替不了；加一镜反应，或把反应写进下一镜的开头",
              r"after a heavy line the audience needs to see the listener react physically (freezing / hands stopping / looking away / a step back); an adjective cannot stand in for it; add a reaction shot, or open the next shot with the reaction")),
    *_carded((r"旁白第 (\d+) 句「(.+?)…」指到不存在的镜 (\S+)", r'narration line \1 "\2…" points at a non-existent shot \3'),
             (r"这句不会被铺进成片；改成现有的镜号", r"this line will not be laid into the final cut; use an existing shot id"), head="episode"),
    *_carded((r"旁白「(.+?)…」≈(\S+)s，从 (\S+)s 起会超出本镜 (\S+)s", r'narration "\1…" ≈\2 s, starting at \3 s, runs past this shot of \4 s'),
             (r"语速按 4\.5 字/秒算，这句会盖到下一镜或被截断；缩短、提前，或挪到下一镜",
              r"at 4.5 characters per second it will spill into the next shot or get cut off; shorten it, start earlier, or move it to the next shot")),
    *_carded((r"旁白「(.+?)…」（(\S+)–(\S+)s）与对白（约 (\S+)–(\S+)s）同一时段", r'narration "\1…" (\2–\3 s) overlaps the dialogue (about \4–\5 s)'),
             (r"两条人声叠在一起谁都听不清；旁白挪到对白说完之后（≥ (\S+)s），或放到没台词的镜",
              r"two voices on top of each other and neither is intelligible; move the narration after the dialogue ends (≥ \1 s), or to a shot without lines")),
    *_carded((r"「(.+?)」的一句台词 (\d+) 字", r'one line of "\1" is \2 characters'),
             (r"一口气说不完，拆镜时要占满一整镜；拆成两句，或把说明性的半句改成动作",
              r"it cannot be said in one breath and will fill a whole shot; split it in two, or turn the explanatory half into an action"), head="scene"),
    *_carded((r"剧本里有 (\d+) 个人物开口说话（(.+?)）", r'\1 characters speak in the screenplay ("\2")'),
             (r"竖屏一集观众只记得住三张脸，模型也更容易把人认混；把只说一两句的人改成不开口的在场者，或合并成一个人",
              r"in one vertical episode the audience remembers three faces at most, and the model confuses people more easily; make those with one or two lines silent bystanders, or merge them into one character"), head="episode"),
]


# ---------------------------------------------------------------------------- 剧本工艺清单的低优判据（docs/script-craft-gap.md）：
#      lint.py 的单镜时长上限、关系场景（表白 / 重逢 / 原谅 / 拒绝 / 分手），告警句头「这一镜是「X」场景，」的 X 是五个中文标签
_SCENE_LABELS = [("表白", "confession"), ("重逢", "reunion"), ("原谅", "forgiveness"), ("拒绝", "rejection"), ("分手", "breakup")]


def _scene_rules() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    bodies: list[tuple[str, str, str, str]] = [
        (r"正文里看不出被表白的一方接没接住：对方没有任何动静，也没交代是故意不答",
         r"the text does not show whether the confession is taken in: the other person shows no reaction, and nothing says the silence is deliberate",
         r"成没成，观众得从对方身上看出来；给对方写一个表演动作（愣住、别过脸、点头、退后），或把不答写成看得见的停顿（站着不动、手悬在半空）",
         r"whether it lands has to show on the other person; give them one performed action (freezing, turning the face away, nodding, stepping back), or make the non-answer a visible pause (standing still, a hand hanging in mid-air)"),
        (r"正文里看不出是凭什么认出来的（停步 / 一件旧物 / 一道疤 / 一个老习惯 / 背影）",
         r"the text does not show what the recognition hinges on (stopping short / an old object / a scar / an old habit / the back view)",
         r"观众要跟角色一起认出对方，不能靠事后解释；给认出的一瞬写一个表演动作：停步、看到旧物、看到侧脸",
         r"the audience must recognise the person together with the character, not from an explanation afterwards; give the moment one performed action: stopping, seeing an old object, seeing the profile"),
        (r"正文里没交代两人往后算什么（和好 / 不和好 / 先隔开一段 / 再看看）",
         r"the text does not say where the two stand from now on (making up / not making up / keeping apart for a while / wait and see)",
         r"原谅了也不一定回到从前，观众要知道两人之后怎么处；用一句开口的话或一个表演动作把结果写明",
         r"forgiving does not mean going back to how things were; the audience needs to know how the two carry on; state it in one spoken line or one performed action"),
        (r"正文里有「(.+?)」，但只有一方在靠近，受伤的一方没有回应的动作", r'the text has "\1" but only one side moves in; the hurt side makes no answering gesture',
         r"单方面的亲密接触读起来像施压不像和解；写受伤方的回抱 / 回握，或两人同时靠近的表演",
         r"one-sided intimacy reads as pressure, not reconciliation; write the hurt side hugging or gripping back, or both moving closer at once"),
        (r"正文里看不到拒绝的一方怎么把人挡开（退半步 / 抽手 / 转身 / 走开）",
         r"the text does not show how the refusing side keeps the other away (half a step back / pulling the hand free / turning away / walking off)",
         r"光嘴上说不行不够，观众得看见人往外撤；给拒绝的一方写一个表演动作（退半步、把手抽回来、侧过身让开）",
         r"saying no is not enough, the audience has to see them pull away; give the refusing side one performed action (half a step back, pulling the hand back, turning aside to make room)"),
        (r"正文里还有「(.+?)」", r'the text still contains "\1"',
         r"对方已经说了不，之后再硬贴上去就是冒犯，不能当深情写；删掉这些动作，或让对方接受边界（松手、退开）；真要写越界，就写成冒犯的表演，镜头站在拒绝的一方这边",
         r"once the other person has said no, pressing on is an offence and cannot be played as devotion; remove these actions, or let the other side accept the boundary (letting go, stepping back); if the line-crossing is intended, stage it as an offence with the camera on the refusing side"),
        (r"正文里没有一个做了就撤不回的举动（拎起行李 / 关上门 / 交回钥匙 / 把对方的东西还回去 / 头也不回）",
         r"the text has no step that cannot be taken back (picking up the luggage / closing the door / handing back the keys / returning the other person's things / leaving without looking back)",
         r"光吵架观众分不清是真分还是气话；把一件东西还回去、关一扇门、拎起行李，写成表演动作",
         r"from a quarrel alone the audience cannot tell a real breakup from words said in anger; return an object, close a door, pick up the luggage, written as performance"),
    ]
    for zh_label, en_label in _SCENE_LABELS:
        head_zh, head_en = rf"这一镜是「{zh_label}」场景，", f'this shot is a "{en_label}" scene, '
        for ipat, irep, fpat, frep in bodies:
            out += _carded((head_zh + ipat, head_en + irep), (fpat, frep))
    return out


_CRAFT_LINT_LOW_RULES: list[tuple[str, str]] = [
    # 镜号只差大小写（lint.check_shot_id_case，2026-09-23）
    *_carded((r"镜号 (\S+) 只差大小写，Windows / macOS 上这几镜的首帧和视频会写到同一个文件、互相覆盖",
              r"shot ids \1 differ only in letter case; on Windows / macOS their first frames and videos are written to the same file and overwrite each other"),
             (r"生成前把它们改成互不相同的镜号", r"rename them to distinct shot ids before generating"), head="episode"),
    *_carded((r"单镜时长 (\S+) 秒", r"the shot lasts \1 seconds"),
             (r"视频模型单次生成多在 5–10 秒内稳，再长动作容易散；拆成两镜，每镜只管一件事",
              r"video models are usually stable for 5–10 seconds per generation and motion drifts beyond that; split it into two shots, each doing one thing")),
    *_scene_rules(),
]

# 配乐投放到播客（docs/music-spec.md §3）：routers/music.py / music/use.py 的成功提示与 400
_MUSIC_PODCAST_RULES: list[tuple[str, str]] = [
    (r"^已放进《(.+?)》的垫乐$", r'Placed into the bed music of "\1"'),
    (r"^已放进《(.+?)》的片头$", r'Placed into the intro music of "\1"'),
    (r"^已放进《(.+?)》的片尾$", r'Placed into the outro music of "\1"'),
    # music/use.py 用 " / ".join 拼槽名，斜杠两侧的空格按实际文案可有可无
    (r"^播客的 slot 是槽名，只能是 intro（片头）\s*/\s*bed（垫乐）\s*/\s*outro（片尾），收到「(.+?)」$",
     r'For a podcast, slot is a slot name and must be one of intro / bed / outro (intro music / bed music / outro music); received "\1"'),
]


def _genre_review_rules() -> list[tuple[str, str]]:
    """按题材审稿（DESIGN §49）的告警句：``[shot X: | scene X: | 全集：]按题材卡「卡」审稿（模型判断）：<违反|有风险><常见坑|禁止漂移|题材核心>「条目」
    [（见 号、号）]，原文「引文」[，理由「…」][——建议「…」]``。模型写的字都在「」里（genre_review._clean 保证里面没有「」和「——」），
    英文替换里原样放进引号；判定 × 来源 × 三个可选段展开成整句规则，另加问题卡两半（没有头、没有建议段的影响半句 + 「建议「…」」）。"""

    q = "「([^」]*)」"
    verdicts = [("违反", "violates"), ("有风险", "is at risk on")]
    sources = [("常见坑", "pitfall"), ("禁止漂移", "forbidden drift"), ("题材核心", "genre core")]
    heads = [(r"((?:shot|scene) \S+?: )", None), ("全集：", "Whole episode: "), ("", "")]
    out: list[tuple[str, str]] = []
    for head_zh, head_en in heads:
        for v_zh, v_en in verdicts:
            for s_zh, s_en in sources:
                for ids in (False, True):
                    for why in (False, True):
                        for fix in (False, True):
                            if head_zh == "" and fix:
                                continue    # 问题卡的影响半句不带建议段
                            n = 0

                            def g() -> str:
                                nonlocal n
                                n += 1
                                return f"\\g<{n}>"

                            zh, en = "^" + head_zh, (g() if head_en is None else head_en)
                            zh += f"按题材卡{q}审稿（模型判断）：{v_zh}{s_zh}{q}"
                            en += f'Genre card "{g()}" review (model judgement): {v_en} {s_en} "{g()}"'
                            if ids:
                                zh += "（见 ([^）]+)）"
                                en += f" (see {g()})"
                            zh += f"，原文{q}"
                            en += f', quote "{g()}"'
                            if why:
                                zh += f"，理由{q}"
                                en += f', reason "{g()}"'
                            if fix:
                                zh += f"——建议{q}"
                                en += f' — suggestion "{g()}"'
                            out.append((zh + "$", en))
    out.append((f"^建议{q}$", 'Suggestion: "\\g<1>"'))
    out += [
        (r"^这一集没有选题材，先在概览里选一个$", r"This episode has no genre yet — pick one on the overview page first"),
        (r"^发 1 次文本模型请求$", r"Sends 1 text model request"),
        (r"^剧本改过，这份审稿可能过时——重新审一次$", r"The script changed after this review, so it may be out of date — run the review again"),
        (r"^模型没有按审稿结构返回（缺 items 列表）——再试一次，或换文本模型$",
         r"The model did not return the review structure (no items list) — try again, or switch the text model"),
    ]
    return out


_GENRE_REVIEW_RULES = _genre_review_rules()


def _craft_review_rules() -> list[tuple[str, str]]:
    """剧本工艺审稿的告警句，形状跟题材审稿一样只换句头与组名：``[shot X: | scene X: | 全集：]按工艺清单审稿（模型判断）：
    <违反|有风险><张力|情绪可见|节奏|合规>「条目」[（见 号、号）]，原文「引文」[，理由「…」][——建议「…」]``。
    「建议「…」」那半句复用题材审稿的规则；另加清单读不出来的 400。"""

    q = "「([^」]*)」"
    verdicts = [("违反", "violates"), ("有风险", "is at risk on")]
    groups = [("张力", "tension"), ("情绪可见", "visible emotion"), ("节奏", "rhythm"), ("合规", "compliance")]
    heads = [(r"((?:shot|scene) \S+?: )", None), ("全集：", "Whole episode: "), ("", "")]
    out: list[tuple[str, str]] = []
    for head_zh, head_en in heads:
        for v_zh, v_en in verdicts:
            for g_zh, g_en in groups:
                for ids in (False, True):
                    for why in (False, True):
                        for fix in (False, True):
                            if head_zh == "" and fix:
                                continue    # 问题卡的影响半句不带建议段
                            n = 0

                            def g() -> str:
                                nonlocal n
                                n += 1
                                return f"\\g<{n}>"

                            zh, en = "^" + head_zh, (g() if head_en is None else head_en)
                            zh += f"按工艺清单审稿（模型判断）：{v_zh}{g_zh}{q}"
                            en += f'Craft checklist review (model judgement): {v_en} {g_en} "{g()}"'
                            if ids:
                                zh += "（见 ([^）]+)）"
                                en += f" (see {g()})"
                            zh += f"，原文{q}"
                            en += f', quote "{g()}"'
                            if why:
                                zh += f"，理由{q}"
                                en += f', reason "{g()}"'
                            if fix:
                                zh += f"——建议{q}"
                                en += f' — suggestion "{g()}"'
                            out.append((zh + "$", en))
    out.append((r"^工艺清单文件读不出来——重装一次程序，或检查 skills/ai-short-film/scripts/craft_checklist\.json$",
                r"The craft checklist file could not be read — reinstall the app, or check skills/ai-short-film/scripts/craft_checklist.json"))
    return out


_CRAFT_REVIEW_RULES = _craft_review_rules()

# 播客英文稿（podcast/lint.py 按 spec.language 选词表）：独白按词数报「N 词」、填充词例子换成英文；其余告警句沿用中文稿的
_PODCAST_EN_RULES: list[tuple[str, str]] = [
    *_carded((r"「(.+?)」一句 (\d+) 词", r'"\1" has a single line of \2 words'),
             (r"约 (\d+) 秒没人插话，真人对谈里别人早接话了；拆开，让另一个人接一句或追问",
              r"about \1 seconds with nobody cutting in; in a real conversation someone would have; split it and let the other person respond or push back"), head="segment"),
    *_carded((r"填充词（um / uh / like / you know…）占了 (\d+)%", r"filler words (um / uh / like / you know…) make up \1%"),
             (r"TTS 念出来像结巴；留在接话处一两个，其余删掉",
              r"TTS will read it as stuttering; keep one or two where someone picks up the thread and delete the rest"), head="podcast"),
    *_carded((r"「(.+?)」说了 (\d+)% 的词", r'"\1" speaks \2% of the words'),
             (r"一人独大另一个人就成了捧哏；把一部分例子和观点分给别人，或让别人反驳",
              r"when one host dominates the other becomes a sidekick; hand some examples and opinions to the others, or let them push back"), head="podcast"),
    *_carded((r"一个填充词都没有", r"there is not a single filler word"),
             (r"略显播音腔；在接话和被打断的地方加一两个「um」「right」",
              r'it sounds slightly like a newsreader; add an "um" or "right" where someone picks up or gets interrupted'), head="podcast"),
]


def _podcast_review_rules() -> list[tuple[str, str]]:
    """播客真人感审稿（podcast/review.py）的告警句，形状跟工艺审稿一样只换句头与组名：``[segment X: | 全期：]按真人感清单审稿（模型判断）：
    <违反|有风险><口语|对谈动态|内容>「条目」[（见 号、号）]，原文「引文」[，理由「…」][——建议「…」]``。
    「建议「…」」那半句复用题材审稿的规则；另加过时提示、清单读不出来与没台词的 400。"""

    q = "「([^」]*)」"
    verdicts = [("违反", "violates"), ("有风险", "is at risk on")]
    groups = [("口语", "spoken language"), ("对谈动态", "conversation dynamics"), ("内容", "substance")]
    heads = [(r"(segment \S+?: )", None), ("全期：", "Whole episode: "), ("", "")]
    out: list[tuple[str, str]] = []
    for head_zh, head_en in heads:
        for v_zh, v_en in verdicts:
            for g_zh, g_en in groups:
                for ids in (False, True):
                    for why in (False, True):
                        for fix in (False, True):
                            if head_zh == "" and fix:
                                continue    # 问题卡的影响半句不带建议段
                            n = 0

                            def g() -> str:
                                nonlocal n
                                n += 1
                                return f"\\g<{n}>"

                            zh, en = "^" + head_zh, (g() if head_en is None else head_en)
                            zh += f"按真人感清单审稿（模型判断）：{v_zh}{g_zh}{q}"
                            en += f'Humanlike checklist review (model judgement): {v_en} {g_en} "{g()}"'
                            if ids:
                                zh += "（见 ([^）]+)）"
                                en += f" (see {g()})"
                            zh += f"，原文{q}"
                            en += f', quote "{g()}"'
                            if why:
                                zh += f"，理由{q}"
                                en += f', reason "{g()}"'
                            if fix:
                                zh += f"——建议{q}"
                                en += f' — suggestion "{g()}"'
                            out.append((zh + "$", en))
    out += [
        (r"^稿子改过，这份审稿可能过时——重新审一次$", r"The script changed after this review, so it may be out of date — run the review again"),
        (r"^真人感清单文件读不出来——重装一次程序，或检查 skills/ai-podcast/scripts/humanlike_checklist\.json$",
         r"The humanlike checklist file could not be read — reinstall the app, or check skills/ai-podcast/scripts/humanlike_checklist.json"),
        (r"^稿子里还没有台词——先起草或手写几句，再审$", r"The script has no lines yet — draft or write a few lines first, then review"),
    ]
    return out


_PODCAST_REVIEW_RULES = _podcast_review_rules()
# ---------------------------------------------------------------------------- 播客交付包（podcast/export.py + routers/podcast.py 的 export 三个端点）：
#      接口的 400 与任务失败原因；409（这一分钟已经导出过）在 EXACT 里、404（没有这个交付包）在 _ROUTER_RULES 里，跟短剧共用。
#      manifest.json / ai_disclosure.md 是文件内容，不翻。
_PODCAST_EXPORT_RULES: list[tuple[str, str]] = [
    (r"^还没有可以导出的东西——先合成$", r"Nothing to export yet — synthesize first"),
]


# ---------------------------------------------------------------------------- 剧本工作台（docs/scriptlab-spec.md）：
#      script/lint.py 的告警是从 podcast/lint.py 与 storyvideo/screenplay.py 借来的，正文一个字没改、只换了句头
#      （``block B01: `` / ``全篇：``），所以这里只补**整句**；问题卡的影响 / 改法两半已经有规则了（播客与剧本层的块里），
#      重复写只会多出两条一模一样的。工作台自己新写的那条（一镜的对白密度）连两半一起出。
#      **代价要知道**：两半走的是播客 / 剧本层的旧规则，翻出来仍是那两个模块的措辞——同一张卡的标题（整句，这里的规则）
#      说 script / block，正文（两半）却说 episode / segment / host / screenplay。例如整句「全篇：整期没有一处反对或分歧——…」
#      翻成 “Whole script: nobody disagrees anywhere in the script — …”，拆出来的影响半句「整期没有一处反对或分歧」
#      却是 “nobody disagrees anywhere in the episode”。这是「借来的正文一个字不改」的必然结果，不是漏了规则：要统一措辞，
#      得先决定工作台是自己一套文案还是继续共用 podcast.lint 的句子（产品决定），再动这里。
_SCRIPT_HEADS = {"block": (r"block (\S+?): ", r"block \1: "), "script": (r"全篇：()", r"Whole script: \1")}


def _script_whole(impact: tuple[str, str], fix: tuple[str, str] | None = None, head: str = "block",
                  halves: bool = False) -> list[tuple[str, str]]:
    ipat, irep = impact
    hpat, hrep = _SCRIPT_HEADS[head]
    n_impact = re.compile(ipat).groups
    out: list[tuple[str, str]] = []
    if fix:
        fpat, frep = fix
        out.append((f"^{hpat}{ipat}——{fpat}$", f"{hrep}{_shift(irep, 1)} — {_shift(frep, 1 + n_impact)}"))
        if halves:
            out.append((f"^{fpat}$", frep))
    else:
        out.append((f"^{hpat}{ipat}$", f"{hrep}{_shift(irep, 1)}"))
    if halves:
        out.append((f"^{ipat}$", irep))
    return out


_SCRIPT_LINT_RULES: list[tuple[str, str]] = [
    # 借 podcast/lint.py 的台词类判据（中文稿 + 英文稿两套词表）
    *_script_whole((r"「(.+?)」的台词里有书面语「(.+?)」", r'"\1" uses written-register words "\2"'),
                   (r"真人聊天不这么说；删掉连接词，直接说事", r"nobody talks like that; drop the connectives and say the thing")),
    *_script_whole((r"「(.+?)」一句 (\d+) 字", r'"\1" has a single line of \2 characters'),
                   (r"约 (\d+) 秒没人插话，真人对谈里别人早接话了；拆开，让另一个人接一句或追问",
                    r"about \1 seconds with nobody cutting in; in a real conversation someone would have; split it and let the other person respond or push back")),
    *_script_whole((r"「(.+?)」一句 (\d+) 词", r'"\1" has a single line of \2 words'),
                   (r"约 (\d+) 秒没人插话，真人对谈里别人早接话了；拆开，让另一个人接一句或追问",
                    r"about \1 seconds with nobody cutting in; in a real conversation someone would have; split it and let the other person respond or push back")),
    *_script_whole((r"(\d+) 句每一句都换人", r"\1 lines and every single one switches speaker"),
                   (r"机械轮流像排队发言；让一个人连说两三句、另一个人只接一个「嗯」或打断",
                    r'mechanical turn-taking sounds like people queueing to speak; let one person say two or three lines while the other only adds an "mm" or interrupts')),
    *_script_whole((r"整段没有一个具体例子", r"the whole block has no concrete example"),
                   (r"没有数字，也没有「有一次 / 上周 / 我记得 / 比如」；真人播客靠具体经历撑，不靠观点，从素材里拿一件事讲细",
                    r'no numbers and no "one time / last week / I remember / for example"; real talk runs on concrete experience, not opinions — take one thing from the materials and tell it in detail')),
    *_script_whole((r"有听众问题但没有一句标成回答（kind=answer）", r"there are questions but no line is marked as an answer (kind=answer)"),
                   (r"把回答的句子标出来，检查和节目单都靠它", r"mark the answering lines; the checks and the show notes depend on it")),
    *_script_whole((r"回答「(.+?)」只有 (\d+) 字", r'the answer "\1" is only \2 characters'),
                   (r"听众要的是结论加原因，不是一句敷衍；先给结论，再讲一个为什么",
                    r"listeners want a conclusion plus a reason, not a brush-off; give the conclusion first, then one why")),
    *_script_whole((r"回答开头「(.+?)…」在复述问题", r'the answer opens with "\1…", restating the question'),
                   (r"直接给结论，问题刚念过听众记得", r"give the conclusion directly; the question was just read out and listeners remember it")),
    *_script_whole((r"「(.+?)」说了 (\d+)% 的字", r'"\1" speaks \2% of the words'),
                   (r"一人独大另一个人就成了捧哏；把一部分例子和观点分给别人，或让别人反驳",
                    r"when one speaker dominates the other becomes a sidekick; hand some examples and opinions to the others, or let them push back"), head="script"),
    *_script_whole((r"「(.+?)」说了 (\d+)% 的词", r'"\1" speaks \2% of the words'),
                   (r"一人独大另一个人就成了捧哏；把一部分例子和观点分给别人，或让别人反驳",
                    r"when one speaker dominates the other becomes a sidekick; hand some examples and opinions to the others, or let them push back"), head="script"),
    *_script_whole((r"整期没有一处反对或分歧", r"nobody disagrees anywhere in the script"),
                   (r"真人对谈总有人不同意；加一处明确的不同意见，而且不要马上和解",
                    r"real conversations always have someone disagreeing; add one clear disagreement and do not resolve it right away"), head="script"),
    *_script_whole((r"填充词（嗯 / 啊 / 那个 / 然后…）占了 (\d+)%", r"filler words (mm / ah / that / then…) make up \1%"),
                   (r"TTS 念出来像结巴；留在接话处一两个，其余删掉",
                    r"TTS will read it as stuttering; keep one or two where someone picks up the thread and delete the rest"), head="script"),
    *_script_whole((r"填充词（um / uh / like / you know…）占了 (\d+)%", r"filler words (um / uh / like / you know…) make up \1%"),
                   (r"TTS 念出来像结巴；留在接话处一两个，其余删掉",
                    r"TTS will read it as stuttering; keep one or two where someone picks up the thread and delete the rest"), head="script"),
    *_script_whole((r"一个填充词都没有", r"there is not a single filler word"),
                   (r"略显播音腔；在接话和被打断的地方加一两个「嗯」「对」",
                    r'it sounds slightly like a newsreader; add an "mm" or "right" where someone picks up or gets interrupted'), head="script"),
    *_script_whole((r"一个填充词都没有", r"there is not a single filler word"),
                   (r"略显播音腔；在接话和被打断的地方加一两个「um」「right」",
                    r'it sounds slightly like a newsreader; add an "um" or "right" where someone picks up or gets interrupted'), head="script"),
    # 借 storyvideo/screenplay.py 的两条
    *_script_whole((r"「(.+?)」的一句台词 (\d+) 字", r'one line of "\1" is \2 characters'),
                   (r"一口气说不完，拆镜时要占满一整镜；拆成两句，或把说明性的半句改成动作",
                    r"it cannot be said in one breath and will fill a whole shot; split it in two, or turn the explanatory half into an action")),
    *_script_whole((r"剧本里有 (\d+) 个人物开口说话（(.+?)）", r'\1 characters speak in the script ("\2")'),
                   (r"竖屏一集观众只记得住三张脸，模型也更容易把人认混；把只说一两句的人改成不开口的在场者，或合并成一个人",
                    r"in one vertical episode the audience remembers three faces at most, and the model confuses people more easily; make those with one or two lines silent bystanders, or merge them into one character"),
                   head="script"),
    # 一段就是一镜时的对白密度：数与判断全借 storyvideo.preview.shot_duration_budget（窗口、快端 / 慢端秒数、
    # 装不下 / 偏紧），句子是工作台自己的——preview 那句说的是「声明 Xs」「加时长」，段没有 dur 也加不了时长，
    # 照搬会给出做不到的改法。只判中文稿，所以没有「词」那一版。
    *_script_whole((r"台词 (\d+) 字 ≈(\S+) 秒纯语音，超过一镜的可用发声窗口 (\S+) 秒（单镜按 (\S+) 秒算，扣掉开口等待 (\S+) 秒和末尾反应 (\S+) 秒）",
                    r"\1 characters of dialogue ≈\2 s of pure speech, more than the \3 s speaking window of one shot "
                    r"(\4 s per shot, minus \5 s before the first word and \6 s of reaction at the end)"),
                   (r"这一镜装不下这些台词，拆成两段，或者把说明性的半句改成动作",
                    r"one shot cannot hold these lines; split the block in two, or turn the explanatory half into an action"),
                   halves=True),
    *_script_whole((r"台词 (\d+) 字按偏慢语速要 ≈(\S+) 秒，一镜的可用发声窗口只有 (\S+) 秒（单镜按 (\S+) 秒算，扣掉开口等待 (\S+) 秒和末尾反应 (\S+) 秒）",
                    r"at the slower speech rate these \1 characters of dialogue need ≈\2 s, while one shot's speaking "
                    r"window is only \3 s (\4 s per shot, minus \5 s before the first word and \6 s of reaction at the end)"),
                   (r"偏紧，语气一慢就赶词；减一句，或者把说明性的半句改成动作",
                    r"that is tight: any slower delivery and the lines get rushed; drop one line, "
                    r"or turn the explanatory half into an action"),
                   halves=True),
]


def _script_review_rules() -> list[tuple[str, str]]:
    """剧本工作台的审稿告警：清单按 target 选，句子跟工艺 / 真人感审稿一模一样，只有句头换成
    ``block X: `` / ``全篇：``（问题卡的影响半句已经有规则，这里只出这两个句头的整句）。"""

    q = "「([^」]*)」"
    verdicts = [("违反", "violates"), ("有风险", "is at risk on")]
    sheets = [("按工艺清单审稿（模型判断）", "Craft checklist review (model judgement)",
               [("张力", "tension"), ("情绪可见", "visible emotion"), ("节奏", "rhythm"), ("合规", "compliance")]),
              ("按真人感清单审稿（模型判断）", "Humanlike checklist review (model judgement)",
               [("口语", "spoken language"), ("对谈动态", "conversation dynamics"), ("内容", "substance")])]
    heads = [(r"(block \S+?: )", None), ("全篇：", "Whole script: ")]
    out: list[tuple[str, str]] = []
    for head_zh, head_en in heads:
        for lead_zh, lead_en, groups in sheets:
            for v_zh, v_en in verdicts:
                for g_zh, g_en in groups:
                    for ids in (False, True):
                        for why in (False, True):
                            for fix in (False, True):
                                n = 0

                                def g() -> str:
                                    nonlocal n
                                    n += 1
                                    return f"\\g<{n}>"

                                zh, en = "^" + head_zh, (g() if head_en is None else head_en)
                                zh += f"{lead_zh}：{v_zh}{g_zh}{q}"
                                en += f'{lead_en}: {v_en} {g_en} "{g()}"'
                                if ids:
                                    zh += "（见 ([^）]+)）"
                                    en += f" (see {g()})"
                                zh += f"，原文{q}"
                                en += f', quote "{g()}"'
                                if why:
                                    zh += f"，理由{q}"
                                    en += f', reason "{g()}"'
                                if fix:
                                    zh += f"——建议{q}"
                                    en += f' — suggestion "{g()}"'
                                out.append((zh + "$", en))
    out += [
        (r"^这一版的台词改过，这份审稿可能过时——重新审一次$",
         r"The lines of this version changed after the review, so it may be out of date — run the review again"),
        (r"^这一版还没有台词——先生成或手写几句，再审$",
         r"This version has no lines yet — generate or write a few by hand first, then review"),
        (r"^还没有任何版本——先在「试」里生成一版，或者手写一版$",
         r'There is no version at all yet — generate one under "Try", or write one by hand'),
    ]
    return out


_SCRIPT_REVIEW_RULES = _script_review_rules()

# script/craft.py：起草 / 重写的失败原因与提示；script/spec.py 的目标形态说明与版本来源标签
_SCRIPT_CRAFT_RULES: list[tuple[str, str]] = [
    (r"^模型没有按稿子结构返回（缺 blocks 列表）——再试一次，或换文本模型$",
     r"The model did not return the script structure (no blocks list) — try again, or switch the text model"),
    (r"^模型在段 (\S+?) 里让「(.+?)」说话，但角色表里没有这个人——重新生成，或把这个人加进角色表$",
     r'In block \1 the model gave lines to "\2", who is not in the cast — generate again, or add this person to the cast'),
    (r"^模型没有返回段 (\S+?)——再试一次$", r"The model did not return block \1 — try again"),
    (r"^没有这些段：(.+?)——从段落骨架里选$", r"No such blocks: \1 — pick from the block outline"),
    (r"^还没有可以基于的版本——先生成或手写一版，再只重写其中几段$",
     r"There is no version to build on yet — generate or write one first, then rewrite just a few blocks of it"),
    (r"^还没有段落骨架——先加至少一段（标题 \+ 目标），再生成$",
     r"There is no block outline yet — add at least one block (title + goal), then generate"),
    (r"^第 (\d+) 版$", r"Version \1"),
    (r"^按播客真人感那八条写：口语、有分歧、具体压倒观点；检查数书面语、独白、轮流、填充词$",
     r"Written to the eight podcast humanlike rules: spoken register, real disagreement, specifics over opinions; "
     r"the checks count written-register words, monologues, turn-taking and fillers"),
    (r"^按短剧台词那几条写：留潜台词、嘴上和身上各演一半、只写拍得到的；检查数单句字数和开口人数$",
     r"Written to the short-drama dialogue rules: leave subtext, let words and body tell different halves, only what a camera can catch; "
     r"the checks count characters per line and how many people speak"),
    (r"^一段就是一镜：写法同剧本，检查另外按一镜的发声窗口数这一段的台词装不装得下$",
     r"One block is one shot: same writing rules as the screenplay, plus a check on whether the block's dialogue fits one shot's speaking window"),
    (r"^两边的交集：口语、有分歧、具体、不用台词交代前情；检查只数书面语、独白、一人独大、具体例子$",
     r"The intersection of both: spoken register, real disagreement, specifics, no exposition in dialogue; "
     r"the checks only count written-register words, monologues, one speaker dominating and concrete examples"),
    (r"^模型生成$", r"Generated by the model"),
    (r"^手写另存$", r"Saved by hand"),
    (r"^重写几段$", r"A few blocks rewritten"),
    (r"^从项目导入$", r"Imported from a project"),
]

# script/spec.py：项目文件校验（带「改动没通过校验，没有写盘：」这类前缀的整句 + 单独）。
# 跟播客同形状的几条（X 要是文本 / X 不能为空 / X 只能是 A / B，收到「C」…）在播客与绘本的块里已经有了，这里只补工作台独有的。
_SCRIPT_SPEC_ERRORS: list[tuple[str, str]] = [
    (r"speakers 要是数组（角色表）", r"speakers must be an array (the cast table)"),
    (r"(speakers\[\d+\]) 要是对象（id / name / persona / stance）", r"\1 must be an object (id / name / persona / stance)"),
    (r"角色 id 重复了：(\S+)——每个人一个 id，台词的说话人靠它对上",
     r"duplicate speaker id: \1 — one id per person; lines are matched to speakers by it"),
    (r"角色名字重复了：「(.+?)」——台词按名字写时分不清是谁，给其中一个换个名字",
     r'duplicate speaker name "\1" — when a line names its speaker there is no telling them apart; rename one of them'),
    (r"角色表是空的——先加至少一个人（名字 \+ 人设），台词才知道谁在说",
     r"The cast table is empty — add at least one person (name + persona) so the lines know who is speaking"),
    (r"角色表最多 (\d+) 人，现在有 (\d+) 人——人再多观众记不住，也映射不到目标项目",
     r"at most \1 speakers; there are \2 — more than that and the audience loses track, and they cannot all be mapped to the destination project"),
    (r"blocks 要是数组（段落骨架）", r"blocks must be an array (the block outline)"),
    (r"(blocks\[\d+\]) 要是对象（id / title / goal / note）", r"\1 must be an object (id / title / goal / note)"),
    (r"段 id 重复了：(\S+)——每段一个 id，告警、重写和投放都靠它对上",
     r"duplicate block id: \1 — one id per block; warnings, rewrites and hand-offs are all matched by it"),
    (r"versions 要是数组（版本列表）", r"versions must be an array (the version list)"),
    (r"(versions\[\d+\]) 要是对象（id / label / source / blocks）", r"\1 must be an object (id / label / source / blocks)"),
    (r"版本 id 重复了：(\S+)——每一版一个 id，比较、采用和审稿都靠它对上",
     r"duplicate version id: \1 — one id per version; comparing, picking and reviewing are all matched by it"),
    (r"(\S+) 要是数组（每段的台词）", r"\1 must be an array (the lines of each block)"),
    (r"(\S+) 要是对象（id / lines）", r"\1 must be an object (id / lines)"),
    (r"版本 (\S+?) 里的段「(.+?)」不在段落骨架里——先在骨架里加这一段，或者把它删掉",
     r'block "\2" of version \1 is not in the block outline — add it to the outline first, or delete it'),
    (r"版本 (\S+?) 里的段 id 重复了：(\S+)——一版里每段只写一次",
     r"duplicate block id \2 in version \1 — each block appears once per version"),
    (r"版本 (\S+?) 说是基于「(.+?)」改的，但没有这一版——把 from_version 改对或留空",
     r'version \1 says it was based on "\2", but there is no such version — fix from_version or leave it empty'),
    (r"(\S+) 要是数组（台词）", r"\1 must be an array (lines)"),
    (r"(\S+) 要是对象（who / text / cue / kind）", r"\1 must be an object (who / text / cue / kind)"),
    (r"(\S+) 的说话人「(.+?)」不在角色表里——从角色表里选一个（用 id 或名字），或者先把这个人加进角色表",
     r'the speaker "\2" of \1 is not in the cast table — pick one from the cast table (by id or name), or add this person first'),
    (r"current「(.+?)」不是这个项目的版本 id——只能采用列在版本里的那几版",
     r'current "\1" is not a version id of this project — only the versions in the list can be picked'),
    (r"used_in 要是数组（投放记录）", r"used_in must be an array (hand-off records)"),
    (r"(used_in\[\d+\]) 要是对象（module / id / target / at）", r"\1 must be an object (module / id / target / at)"),
    (r"(\S+) 只能是「(.+?)」里的一个，或留空，收到「(.+?)」", r'\1 must be one of "\2", or empty; received "\3"'),
    # 以「（…）」收尾的那几条：``tr`` 先按「人话（原文）」把括号切开，所以括号前的半句和括号里的中文各要一条规则，
    # 不然翻出来会留着「要是数组」「角色表」（imageset 的 subject / image_params 也是这么补的）
    (r"speakers 要是数组", r"speakers must be an array"),
    (r"(speakers\[\d+\]) 要是对象", r"\1 must be an object"),
    (r"blocks 要是数组", r"blocks must be an array"),
    (r"(blocks\[\d+\]) 要是对象", r"\1 must be an object"),
    (r"versions 要是数组", r"versions must be an array"),
    (r"(versions\[\d+\]) 要是对象", r"\1 must be an object"),
    (r"(versions\[\d+\]\.blocks) 要是数组", r"\1 must be an array"),
    (r"(versions\[\d+\]\.blocks\[\d+\]) 要是对象", r"\1 must be an object"),
    (r"(versions\[\d+\]\.blocks\[\d+\]\.lines) 要是数组", r"\1 must be an array"),
    (r"(versions\[\d+\]\.blocks\[\d+\]\.lines\[\d+\]) 要是对象", r"\1 must be an object"),
    (r"used_in 要是数组", r"used_in must be an array"),
    (r"(used_in\[\d+\]) 要是对象", r"\1 must be an object"),
    (r"brief 要是对象", r"brief must be an object"),
    (r"角色表", r"the cast table"),
    (r"段落骨架", r"the block outline"),
    (r"版本列表", r"the version list"),
    (r"每段的台词", r"the lines of each block"),
    (r"投放记录", r"hand-off records"),
    (r"台词", r"lines"),
    (r"没有这个剧本：(.+)", r"No such script: \1"),
    (r"没有这一版：(\S+?)——这个剧本还没有任何版本", r"No such version: \1 — this script has no versions at all yet"),
    (r"没有这一版：(\S+?)——版本列表里只有 (.+)", r"No such version: \1 — the version list only has \2"),
    (r"brief 要是对象（(.+?)）", r"brief must be an object (\1)"),
]

# ---------------------------------------------------------------------------- 剧本工作台的接口层与投放 / 导入
#      （script/use.py + webui/routers/script.py，契约 docs/scriptlab-spec.md §7 §8）。
#      这些句子是工作台自己写的（不是从播客 / 剧本层借来的），所以整句与问题卡两半都在这里出。
#      「目标 X 没接受这些台词：<人话>」是**里层带人话**的形状，跟配乐投放到播客一样要 ``_composed``
#      （``tr`` 只匹配一次、不递归翻里层），composed 在 PATTERNS 里排在下面的裸前缀兜底之前。
_SCRIPTLAB_USE_PREFIXES: list[tuple[str, str]] = [
    ("目标播客没接受这些台词", "The destination podcast rejected these lines"),
    ("目标剧本没接受这些台词", "The destination screenplay rejected these lines"),
    ("目标项目没接受这些台词", "The destination project rejected these lines"),
    ("目标播客读不出来", "The destination podcast could not be read"),
    ("导进来的台词没通过校验", "The imported lines failed validation"),
]

# 「目标播客读不出来：<里层>」走的是 ``podcast.spec.load``：除了归一化那些（``_PODCAST_SPEC_ERRORS``），
# 它还会抛读文件那一层的三句，这三句不在归一化的表里。单独出现时仓库里也没有规则（既有漏网，不在这次的范围里），
# 这里只补自己这条 composed 用得上的，不动别人的表。
_PODCAST_LOAD_ERRORS: list[tuple[str, str]] = [
    (r"项目文件不是合法的 JSON（(.+?) 第 (\d+) 行）", r"the project file is not valid JSON (\1, line \2)"),
    # 上面那条整句在带前缀时走不到：``tr`` 对「……（……）」形状会先拆括号（「人话（原文）」那条分支），
    # 括号前的半句单独送进来，所以这里还要有一条只管前半句的（括号里的「X 第 N 行」由 _SCRIPTLAB_RULES 兜）
    (r"项目文件不是合法的 JSON", r"the project file is not valid JSON"),
    (r"项目文件里的 id「(.+?)」跟文件名 (\S+) 对不上", r'the id "\1" in the project file does not match the file name \2'),
    (r"读不了项目文件 (.+?)：(.+)", r"could not read the project file \1: \2"),
]

_SCRIPTLAB_RULES: list[tuple[str, str]] = [
    # ---- 投放 / 导入的入参与落点（script/use.py）
    (r"^target_id 不能为空——先选一个目标项目$", r"target_id cannot be empty — pick a destination project first"),
    (r"^没有这个项目：(.+)$", r"No such project: \1"),
    (r"^mode 只能是 replace（整段替换）/ append（追加在后面），收到「(.+?)」$",
     r'mode must be replace (replace the whole spot) / append (add after what is already there); received "\1"'),
    (r"^blocks 要是对象——每段一个落点，键是段 id、值是落点 id$",
     r"blocks must be an object — one landing spot per block, keyed by block id with the spot id as the value"),
    (r"^投放的段不在段落骨架里：「(.+?)」——从骨架里选$",
     r'these blocks are not in the block outline: "\1" — pick from the outline'),
    (r"^没有要投放的段——至少给一段选一个落点$", r"No block was picked — give at least one block a landing spot"),
    (r"^这一版还没有台词——先生成或手写几句，再投放$",
     r"This version has no lines yet — generate or write a few by hand first, then hand it off"),
    (r"^这些说话人还没有映射：「(.+?)」——每个开口的人都要对上目标项目里的一个角色$",
     r'these speakers are not mapped yet: "\1" — everyone who speaks has to match a character in the destination project'),
    (r"^映射到了目标项目里没有的角色：「(.+?)」——从下拉里重新选一个$",
     r'mapped onto characters the destination project does not have: "\1" — pick again from the dropdown'),
    (r"^没有这个环节：(.+)$", r"No such segment: \1"),
    (r"^没有这个场次：(.+)$", r"No such scene: \1"),
    (r"^没有这个镜号：(.+)$", r"No such shot: \1"),
    (r"^没有这一场：(.+)$", r"No such scene: \1"),
    (r"^没有这一镜：(.+)$", r"No such shot: \1"),
    (r"^这一集还没有剧本：(\S+?)——先在短剧的「剧本」页出一份，再投放$",
     r'''This episode has no screenplay yet: \1 — produce one on the short drama's "Screenplay" page first, then hand off'''),
    (r"^这一集还没有剧本：(\S+?)——先在短剧的「剧本」页出一份，再导入$",
     r'''This episode has no screenplay yet: \1 — produce one on the short drama's "Screenplay" page first, then import'''),
    (r"^段 (\S+?) 还没有标题——新建场次要有场景标题（内/外 · 地点 · 时间），先给这一段起个标题$",
     r"block \1 has no title yet — a new scene needs a scene heading (int/ext · place · time), so give this block a title first"),
    (r"^这一集已经开始制作，台词是生成字段——去项目里走改写重出，或复制一个新 revision$",
     r"Production has started on this episode and its lines feed the prompts — rewrite and regenerate inside the project, "
     r"or copy it to a new revision"),
    (r"^分镜不能新建镜号——镜次是拆镜的产物，先去项目里加一镜，再回来投放$",
     r"Shots cannot be created here — a shot comes out of storyboarding; add one inside the project first, then come back"),
    (r"^镜 (\S+?) 是旧式 speaker/en/zh 平铺字段，这个工具不知道怎么安全写回——改台词请直接编辑源 JSON 文件$",
     r"Shot \1 uses the legacy flat speaker/en/zh fields and this tool cannot safely write them back — "
     r"edit the source JSON file directly to change the lines"),
    (r"^这个项目里还没有台词可导——先在那边写几句，再来导入$",
     r"That project has no lines to import yet — write a few over there first, then come back"),
    (r"^《(.+?)》里开口的人有 (\d+) 个，跟工作台已有的角色合起来 (\d+) 人，超过角色表上限 (\d+) 人"
     r"——先少导几段，或者去角色表里删掉用不上的人$",
     r'"\1" has \2 speaking characters, \3 together with the ones already in the script lab, over the limit of \4 — '
     r"import fewer blocks at a time, or delete the characters you do not need"),
    # ---- 接口层（webui/routers/script.py）
    (r"^剧本 (\S+?) 已经存在，换一个 id$", r"Script \1 already exists; use a different id"),
    (r"^不能删掉采用中的那一版——先采用另一版，再删它$",
     r"The version in use cannot be deleted — pick another version first, then delete this one"),
    (r"^这是最后一版，删了就什么都不剩了——先另存一版，再删它$",
     r"This is the last version; deleting it would leave nothing behind — save another version first, then delete it"),
    (r"^没有这一版：(\S+)$", r"No such version: \1"),
    (r"^没说要采用哪一版——在版本列表里点一版再采用$",
     r"No version was named — pick one in the version list first"),
    (r"^已放进《(.+?)》的环节 (.+)$", r'Dropped into "\1" — segments \2'),
    (r"^已放进《(.+?)》的场次 (.+)$", r'Dropped into "\1" — scenes \2'),
    (r"^已放进《(.+?)》的镜号 (.+)$", r'Dropped into "\1" — shots \2'),
    (r"^已从《(.+?)》导入 (\d+) 句$", r'Imported \2 lines from "\1"'),
    # 导入时 id 撞上名字不同的已有角色 → 换号并点名；「」里是「名字：旧 id → 新 id（旧 id = 占号的人）」，全是用户内容，照抄
    (r"^已从《(.+?)》导入 (\d+) 句；撞 id 的角色已换号：「(.+?)」$",
     r'Imported \2 lines from "\1"; characters whose id was already taken got a new id: "\3"'),
    (r"^从《(.+?)》导入$", r'Imported from "\1"'),
    # ---- 目标形态名与落点名（``/api/script/options`` 与列表行；前端也有自己那份对照表，两边措辞保持一致）
    (r"^播客对谈$", r"Podcast"),
    (r"^短剧剧本（场）$", r"Short drama scene"),
    (r"^短剧分镜（镜）$", r"Short drama shot"),
    (r"^通用台词$", r"Generic dialogue"),
    (r"^环节$", r"Segment"),
    (r"^场次$", r"Scene"),
    (r"^镜号$", r"Shot"),
    (r"^剧本工作台$", r"Script lab"),
    # ---- 「文件 第 N 行」：``tr`` 拆「人话（原文）」时括号里的那半句会单独送进来（见 _PODCAST_LOAD_ERRORS 的注释）
    (r"^(\S+) 第 (\d+) 行$", r"\1, line \2"),
    # ---- 「目标 X 没接受这些台词：<人话>」的裸前缀兜底：里层没规则时至少把前缀翻掉（composed 排在它前面）
    *[(f"^{re.escape(zh)}：(.+)$", f"{en}: \\1") for zh, en in _SCRIPTLAB_USE_PREFIXES],
]

# 静态扫描（tests/test_webui_i18n.py::test_every_raise_literal_translates）扫出来的 raise 文案：经 400 detail、任务 error、
# 生成失败原因到达界面，但之前没有规则。写成模板，字段名 / 镜号 / 原因用捕获组；tr 会把捕获组里剩下的中文再翻一遍，
# 所以「前缀：<里层>」只写前缀即可。排在各模块自己的规则之后、路由前缀兜底之前：这里有几条 (\S+) 开头的泛化形状
_RAISE_RULES: list[tuple[str, str]] = [
    # ---- storyvideo/scriptgen.py：没配模型、代理层错误（起草任务的 error）。「不是合法 JSON」两条要排在 comfyui 的泛化规则前面
    (r"^还没有可用的剧本文本模型：在「模型设置」里给「剧本文本」选一个来源并填密钥（或在 \.env 里配 (.+?)）。$",
     r'No script text model is available yet: pick a source for "Script text" in Model settings and enter its key (or set \1 in .env).'),
    (r"^SCRIPTGEN_BACKEND=openai_proxy 但没配全 SCRIPTGEN_LLM_BASE_URL / SCRIPTGEN_LLM_API_KEY，在 \.env 里补上。$",
     r"SCRIPTGEN_BACKEND=openai_proxy but SCRIPTGEN_LLM_BASE_URL / SCRIPTGEN_LLM_API_KEY are not fully set; add them in .env."),
    (r"^没配 SCRIPTGEN_LLM_MODEL——代理暴露的模型 id 是什么，在 \.env 里写清楚。$",
     r"SCRIPTGEN_LLM_MODEL is not set — put the model id the proxy exposes in .env."),
    (r"^未知的 camera_profile: (.+?)，已知：(.+)$", r"Unknown camera_profile: \1; known: \2"),
    (r"^未知的 camera_profile: (.+)$", r"Unknown camera_profile: \1"),
    (r"(?s)^LLM 代理调用超时（(.+?)s）：(.+)$", r"LLM proxy call timed out (\1s): \2"),
    (r"(?s)^LLM 代理调用失败（网络层）：(.+)$", r"LLM proxy call failed (network layer): \1"),
    (r"(?s)^LLM 代理返回 (\d+)：(.+)$", r"LLM proxy returned HTTP \1: \2"),
    (r"(?s)^LLM 代理返回的不是合法 JSON：(.+)$", r"LLM proxy response is not valid JSON: \1"),
    (r"(?s)^LLM 代理返回结构不是预期的 chat\.completions 格式：(.+)$", r"LLM proxy response is not in the expected chat.completions shape: \1"),
    (r"(?s)^LLM 代理返回的 content 不是合法 JSON：(.+)$", r"LLM proxy content is not valid JSON: \1"),
    # ---- storyvideo/prompts.py：运镜校验（编译提示词时抛，经「生成失败：」到界面；句头是 shot X:，顺手按问题卡形状展开）
    *_carded((r"camera_move 与 camera_combo 只能二选一", r"camera_move and camera_combo are mutually exclusive")),
    *_carded((r"镜头语言库里没有 (.+)", r"the camera language library has no \1")),
    (r"(?s)^shot (\S+?): (.+?) 不在产线 profile (.+?) 的白名单里。该 profile 的理由：(.*)$",
     r"shot \1: \2 is not on the allowlist of the camera profile \3. The profile's rationale: \4"),
    # ---- storyvideo/qc.py / tail.py / runtime.py / genkit/store.py
    (r"^unsupported retake defect: (.+?)（可选：(.+?)）$", r"unsupported retake defect: \1 (choices: \2)"),
    (r"^unsupported retake disposition: (.+?)（可选：(.+?)）$", r"unsupported retake disposition: \1 (choices: \2)"),
    (r"(?s)^抽尾帧失败（ffmpeg）：(.+)$", r"Failed to extract the tail frame (ffmpeg): \1"),
    (r"^上传用的代理图压到最低画质仍有 (\d+) 字节，超过 (\d+) 字节的上传上限：(.+?)——检查这张图的尺寸是否异常$",
     r"even at the lowest quality the upload proxy is still \1 bytes, over the \2-byte upload limit: \3 — check whether this image has an unusual size"),
    (r"^unsupported store schema: (\d+)（程序需要 (\d+)；新版程序写的账本旧版打不开）$",
     r"unsupported store schema: \1 (this build needs \2; a ledger written by a newer build cannot be opened by an older one)"),
    (r"^没有从 schema (\d+) 升到 (\d+) 的迁移脚本$", r"No migration script from schema \1 to \2"),
    # ---- genkit/model_catalog.py / router.py / adapters：模型目录、本地模型的失败原因
    (r"^(\S+) 不是 \{data: \[\.\.\.\]\} 形状$", r"\1 is not of the shape {data: [...]}"),
    *[(rf"^这个任务是 (\S+) 提交的，但「模型设置」里{zh}来源现在是 (.+?)，没有 (\S+) 的凭据可查——把来源切回 (\S+) 再刷新，或者对这一镜点「重拍」用当前来源重新生成$",
       rf'This job was submitted by \1, but the {en} source in Model settings is now \2, so there are no \3 credentials to poll with — '
       rf'switch the source back to \4 and refresh, or click "Retake" on this shot to regenerate with the current source')
      for zh, en in (("视频", "video"), ("配乐", "music"), ("生图", "image"))],
    (r"^chat\.completions: 流式接收超过 (\d+) 秒还没结束，已放弃$", r"chat.completions: streaming did not finish within \1 seconds; gave up"),
    (r"(?s)^sd webui 返回的图片不是合法 base64：(.+)$", r"sd webui returned an image that is not valid base64: \1"),
    # ---- genkit/adapters/comfyui.py：工作流文件 / 映射 / 提交 / 取件
    (r"^找不到工作流 (\S+)：内置的和 (.+?) 里都没有。现有的是 (.+)$", r"Workflow \1 not found: neither built-in nor in \2. Available: \3"),
    (r"^没有可用的视频工作流：内置的 (.+?) 不在，(.+?) 里也没有$", r"No usable video workflow: the built-in \1 is missing and \2 has none either"),
    (r"^没有可用的生图工作流：内置的 (.+?) 不在，(.+?) 里也没有$", r"No usable image workflow: the built-in \1 is missing and \2 has none either"),
    (r"^工作流 (\S+) 里没有节点——要的是 ComfyUI 的「保存（API 格式）」导出，不是界面里那份带 nodes/links 的 \.json$",
     r'''Workflow \1 has no nodes — it must be ComfyUI's "Save (API format)" export, not the UI's nodes/links .json'''),
    (r"^工作流名不合法：(.+?)（只能是字母、数字、下划线、点和短横）$", r"Invalid workflow name: \1 (only letters, digits, underscores, dots and hyphens)"),
    (r"^工作流 (\S+) 少了映射文件 (\S+)：要一份同名的 \.map\.json 说明哪个节点的哪个字段是提示词 / 负面 / 宽 / 高 / 种子 / 输入图 / 输出节点$",
     r"Workflow \1 is missing its mapping file \2: a same-named .map.json must say which node field is the prompt / negative / width / height / seed / input image / output node"),
    (r"^工作流 (\S+) 与它的映射都必须是 JSON 对象$", r"Workflow \1 and its mapping must both be JSON objects"),
    (r"^工作流 (\S+) 的映射里 task 只能是 image 或 video，现在是 (.+)$", r"In the mapping of workflow \1, task must be image or video; currently \2"),
    (r"^工作流 (\S+) 的映射里 (\S+) 指向 (.+?)，(.+?)——在 ComfyUI 里重新「保存（API 格式）」导出，或改 (\S+)\.map\.json$",
     r'''In the mapping of workflow \1, \2 points to \3, \4 — re-export with "Save (API format)" in ComfyUI, or edit \5.map.json'''),
    (r'^但路径要写成 \[节点号, "inputs", 字段名\]$', r'but the path must be written as [node id, "inputs", field name]'),
    (r"^但这份工作流里没有 (\S+) 号节点$", r"but this workflow has no node \1"),
    (r"^但 (\S+) 号节点（(.+?)）下面没有 (\S+)$", r"but node \1 (\2) has no \3"),
    (r"^但 (\S+) 号节点（(.+?)）的 (\S+) 里没有 (\S+) 这一项$", r"but node \1 (\2) has no \4 entry under \3"),
    (r"^工作流 (\S+) 的映射$", r"the mapping of workflow \1"),
    (r"^工作流 (\S+)$", r"workflow \1"),
    (r"(?s)^读不到(.+?)：(.+)$", r"Cannot read \1: \2"),
    (r"(?s)^(.+?)不是合法 JSON：(.+)$", r"\1 is not valid JSON: \2"),
    (r"^参考图不存在：(.+)$", r"Reference image does not exist: \1"),
    (r"(?s)^ComfyUI 的 /upload/image 没有返回文件名：(.+)$", r"ComfyUI's /upload/image returned no file name: \1"),
    (r"(?s)^ComfyUI 不接受这份工作流（(.+?)）：(.+)$", r"ComfyUI rejected this workflow (\1): \2"),
    (r"^工作流 (\S+) 是视频工作流，不能用来出图——在「模型设置」里给生图角色换一份$",
     r"Workflow \1 is a video workflow and cannot generate images — pick another one for the image role in Model settings"),
    (r"^工作流 (\S+) 是(\S+?)工作流，不能用来出图——在「模型设置」里给生图角色换一份$",
     r"Workflow \1 is a \2 workflow and cannot generate images — pick another one for the image role in Model settings"),
    (r"^工作流 (\S+) 是生图工作流，不能用来出视频——在「模型设置」里给视频角色换一份$",
     r"Workflow \1 is an image workflow and cannot generate videos — pick another one for the video role in Model settings"),
    (r"^工作流 (\S+) 要一张输入图（LoadImage），但这一张没有参考图——换成文生图工作流，或给这一镜配一张参考图$",
     r"Workflow \1 needs an input image (LoadImage), but this image has no reference — switch to a text-to-image workflow, or give this shot a reference image"),
    (r"^工作流 (\S+) 要一张首帧图（LoadImage），但这一镜没有——换成文生视频工作流，或给这一镜配首帧$",
     r"Workflow \1 needs a first-frame image (LoadImage), but this shot has none — switch to a text-to-video workflow, or give this shot a first frame"),
    (r"^ComfyUI 取件是空的：(.+)$", r"ComfyUI returned an empty file: \1"),
    (r"^成品超过 (\d+) 字节：(.+)$", r"Output exceeds \1 bytes: \2"),
    (r"^种子要是整数，现在是 (.+)$", r"The seed must be an integer; currently \1"),
    # ---- webui/revise.py（新版本编辑）/ compare.py（首帧对比）/ export.py
    (r"^不认识的编辑类型：(.+)$", r"Unknown edit type: \1"),
    (r"(?s)^改动没通过校验：(.+)$", r"The change failed validation: \1"),
    (r"^新镜需要填 (\S+)$", r"A new shot needs \1"),
    (r"^「(.+?)」还被这些镜引用：(.+)$", r'"\1" is still referenced by these shots: \2'),
    (r"^不允许改的全局字段：(.+)$", r"This global field cannot be changed here: \1"),
    (r"^候选文件不存在：(.+)$", r"Candidate file does not exist: \1"),
    (r"^导出目录已存在：(.+)$", r"Export directory already exists: \1"),
    # ---- 各模块 spec 的形状校验（clip / music / imageset / storyvideo contract / episodes 边界）：字段名是第 1 组
    (r"^(\S+) 只能是项目内的相对路径（content/assets/… 或 output/clip/…）$",
     r"\1 must be a relative path inside the project (content/assets/… or output/clip/…)"),
    (r"^(\S+) 要是数组（一行一条）$", r"\1 must be an array (one per line)"),
    (r"^(\S+) 要是字符串列表（一行一条）$", r"\1 must be a list of strings (one per line)"),
    (r"^(\S+) 里每一条都要是字符串$", r"Every entry in \1 must be a string"),
    (r"^(\S+) 要是一句话（字符串）$", r"\1 must be a sentence (string)"),
    (r"^(\S+) 要是对象$", r"\1 must be an object"),
    (r"^(\S+) 只接受 jpg / png 参考图$", r"\1 only accepts jpg / png reference images"),
    (r"^(\S+) 里不认识的字段：(.+)$", r"Unknown fields in \1: \2"),
    (r"^(\S+)\.same_as_previous 要是字段名列表（只认 (.+?)）$", r"\1.same_as_previous must be a list of field names (only \2 are accepted)"),
    (r"^page (\S+)\.kb 要是对象（dir / scale）$", r"page \1.kb must be an object (dir / scale)"),
    # ---- 「前缀：<里层人话>」的裸前缀（里层由 tr 再翻一遍）
    (r"(?s)^模型返回的稿子没通过校验：(.+)$", r"The script returned by the model failed validation: \1"),
    (r"(?s)^目标播客没接受这条配乐：(.+)$", r"The destination podcast rejected this track: \1"),
]


# 发第一个 tag 前的修复里新增的文案（podcast/audio.py、post_layers.py、routers/episodes.py、routers/podcast.py、uploads.py）
_RELEASE_FIX_RULES: list[tuple[str, str]] = [
    (r"^画面文字 notebook_text\[(.+?)\] 的 (\w+) (.+?)，现在是「(.+?)」$",
     r'On-screen text notebook_text[\1]: \2 \3; received "\4"'),
    (r"^这期播客正在(.+?)，等它结束再删$",
     r'This podcast is currently \1; wait for it to finish before deleting'),
    # ---- 发布前加固（零碎体验）：上传按内容认（webui/uploads.py：HEIC 提示、音频读不出的原因、转不了的格式）、
    #      音色参考音频转 wav 后超限、设置文件跨进程写的「写不进去 / 另一个窗口正在保存」
    (r"^这是 HEIC 格式的照片（苹果手机默认的格式），这里读不了——请在手机上导出为 JPG 再传（iPhone 可在「设置 → 相机 → 格式」里选「兼容性最佳」，之后拍的就是 JPG）$",
     r'This is a HEIC photo (the default format on iPhones) and cannot be read here — export it as JPG on the phone and upload again '
     r'(on an iPhone, Settings → Camera → Formats → "Most Compatible" makes new photos JPG)'),
    (r"^里面没有音频流$", r"it has no audio stream"),
    (r"^读不出来$", r"it cannot be read"),
    (r"^ffprobe 的输出不是 JSON$", r"ffprobe did not return JSON"),
    (r"^转成 (\w+) 失败$", r"converting it to \1 failed"),
    (r"^这种音频格式（(.+?)）这里用不了——先转成 (.+?) 再传$", r"This audio format (\1) cannot be used here — convert it to \2 first"),
    (r"^这段音频（(.+?)）要转成 (.+?) 才能用，转完会超过 (.+?)MB——剪短一些，或先转成 mp3 再传$",
     r"This audio (\1) has to be converted to \2 to be used here, and would exceed \3MB once converted — trim it, or convert it to mp3 first"),
    (r"^参考音频要转换格式才能用，转完会超过 (\d+)MB——剪短一些（克隆只要 10 秒到 5 分钟），或先转成 mp3 再传$",
     r"The reference audio has to be converted to be used, and would exceed \1MB once converted — trim it "
     r"(cloning only needs 10 seconds to 5 minutes), or convert it to mp3 first"),
    (r"^设置文件写不进去：(.+?)：(.+?)——检查设置目录的权限、磁盘空间，或是不是被别的程序（杀毒软件、同步盘）占着$",
     r"Cannot write the settings file: \1: \2 — check the settings folder permissions and free disk space, "
     r"or whether another program (antivirus, a sync drive) has it open"),
    (r"^没有写权限，或文件正被别的程序占着$", r"no write permission, or another program has the file open"),
    (r"^磁盘空间不够$", r"not enough disk space"),
    (r"^所在的盘是只读的$", r"the drive is read-only"),
    (r"^另一个 StoryStudio 窗口正在保存设置，等几秒再保存一次（(.+)）$",
     r"Another StoryStudio window is saving the settings; wait a few seconds and save again (\1)"),
]

# 合成路径（后期版 / 装配版 / 绘本 / 播客混音）ffmpeg 失败时给人看的一行原因：storyvideo.assembly 的
# ffmpeg_failure_message / ffmpeg_reason / _launch_reason 与 podcast.audio._run、_mix_to 拼出来的几种形状。
# 原因本身多是 ffmpeg 的英文原话；「内存不够：」「磁盘空间不够：」这类中文前缀由里层规则再翻一遍
_RENDER_RULES: list[tuple[str, str]] = [
    (r"(?s)^ffmpeg 出错（(.+?)）：(.+)——完整报错写进了日志 storystudio\.log$",
     r"ffmpeg failed (\1): \2 — the full error output is in the log (storystudio.log)"),
    (r"(?s)^混音超过 (\S+) 秒还没跑完，已中止——(.+)$", r"Mixing was still running after \1 s and was stopped — \2"),
    # 装配版失败时 runtime 包的英文外壳（humanize 把它放在括号里当原文）：外壳照抄，里层那句 ffmpeg 报错由 tr 再翻一遍
    (r"(?s)^assembly failed: (.+)$", r"assembly failed: \1"),
    (r"(?s)^(.+)——完整报错写进了日志 storystudio\.log$", r"\1 — the full error output is in the log (storystudio.log)"),
    (r"^完整报错写进了日志 storystudio\.log$", r"the full error output is in the log (storystudio.log)"),
    (r"(?s)^内存不够：(.+)$", r"out of memory: \1"),
    (r"(?s)^磁盘空间不够：(.+)$", r"out of disk space: \1"),
    (r"^超过 (\S+) 秒还没跑完，已中止$", r"still running after \1 s, stopped"),
    (r"^找不到 ffmpeg 程序：(.+)$", r"ffmpeg executable not found: \1"),
    (r"^命令行超过了 Windows 的长度上限$", r"the command line exceeds the Windows length limit"),
    (r"(?s)^启动失败：(.+)$", r"could not start: \1"),
    (r"^ffmpeg 没有给出原因$", r"ffmpeg gave no reason"),
    (r"(?s)^混音结果没能写进磁盘：(.+)$", r"The mixed file could not be written to disk: \1"),
    # 输入里缺流（坏文件 / 不是音频）：storyvideo.assembly.ffmpeg_reason / explain_ffmpeg_failure，查得出是哪个文件就点名
    (r"^有一个输入文件读不出音频（文件坏了，或者不是音频文件）$", r"one of the input files has no readable audio (it is damaged, or not an audio file)"),
    (r"^有一个输入文件读不出画面（文件坏了，或者不是视频 / 图片）$", r"one of the input files has no readable video (it is damaged, or not a video / image)"),
    (r"^有一个输入文件读不出要用的音视频（文件坏了，或者格式不对）$", r"one of the input files has no usable audio / video (it is damaged, or in the wrong format)"),
    (r"^(.+?) 读不出音频（文件坏了，或者不是音频文件）$", r"\1 has no readable audio (it is damaged, or not an audio file)"),
    (r"^(.+?) 读不出画面（文件坏了，或者不是视频 / 图片）$", r"\1 has no readable video (it is damaged, or not a video / image)"),
    (r"^(.+?) 读不出要用的音视频（文件坏了，或者格式不对）$", r"\1 has no usable audio / video (it is damaged, or in the wrong format)"),
    (r"^滤镜图拼写有误（程序的问题，不是素材的问题）$", r"the filter graph is malformed (a program bug, not a problem with your material)"),
    # storyvideo.assembly.replace_final：上一版成品正被播放器开着（Windows 不让替换）
    (r"^上一版成品正被占用（多半是页面上的播放器还开着它），新版没能换上去——刷新页面或关掉播放器后再合成一次$",
     r"The previous version is still in use (most likely the player on the page still has it open), so the new version could not "
     r"replace it — refresh the page or close the player, then render again"),
    # picturebook.assembly.frame_plan：停顿设成 0 时一页可能短到放不下前后两次翻页
    (r"^第 (\S+) 页只有 (\S+) 秒，放不下前后两次翻页（各 0\.4 秒）——把这一页前后的翻页改成硬切，或把朗读停顿调长$",
     r"Page \1 is only \2 s long, too short for a page turn on both sides (0.4 s each) — change the page turns around this page to "
     r"hard cuts, or lengthen the narration pauses"),
]


# 2026-09-23 发布前安全自查新增的文案（app.py 的来路 / 令牌拦截、episodes.py 删除与恢复、deps / humanize 的 404、
# storyvideo/spec.py 的路径与 id、runtime.py 出片前检查、post.py / scripts.py 的插卡 id 与配乐、podcast/audio.py 混音）
_SECURITY_REVIEW_RULES: list[tuple[str, str]] = [
    (r"^这个本地服务只接受从本机窗口里发起的请求——这次请求来自别的网页或别的地址，已拒绝。$",
     r"This local service only accepts requests made from its own window on this computer — this request came from another web page or address and was rejected."),
    (r"^这个本地服务只接受从桌面窗口（或启动时打印的带 token 的地址）发起的请求。$",
     r"This local service only accepts requests from the desktop window (or from the address with a token printed at startup)."),
    (r"^项目文件里的输出目录（(.+?)）不在数据目录里、或不是以项目 id 结尾，没有搬动它，留在原处；只归档了项目文件$",
     r"The output folder named in the project file (\1) is outside the data folder or does not end with the project id, "
     r"so it was left where it is; only the project file was archived"),
    (r"^项目文件里的输出目录（(.+?)）不在数据目录里、或不是以项目 id 结尾，没有搬动它，留在原处；只归档了项目文件；(.+)$",
     r"The output folder named in the project file (\1) is outside the data folder or does not end with the project id, "
     r"so it was left where it is; only the project file was archived; \2"),
    (r"^账本记录没能一起归档：(.+)$", r"The ledger records could not be archived along with it: \1"),
    (r"^文件已恢复，但账本记录没能放回：(.+)$", r"The files were restored, but the ledger records could not be put back: \1"),
    (r"^归档里的项目文件把输出目录写成了数据目录外、或不以项目 id 结尾的位置（(.+?)）——把归档里 spec\.json 的 output_root 改成 (\S+) 再恢复$",
     r"The archived project file puts the output folder outside the data folder or somewhere not ending with the project id (\1) — "
     r"change output_root in the archived spec.json to \2, then restore"),
    (r"^项目文件里的 (\S+) 指向数据目录之外，已拒绝读取——改成数据目录内的相对路径$",
     r"The project file's \1 points outside the data folder and was not read — change it to a relative path inside the data folder"),
    (r"^项目文件不是合法的 JSON：(.+?) 第 (\d+) 行第 (\d+) 列$", r"The project file is not valid JSON: \1, line \2, column \3"),
    (r"^读不了项目文件 (\S+?)：(.+)$", r"Cannot read the project file \1: \2"),
    (r"^(\S+) 只能是数据目录里的相对路径（如 content/assets/…），收到「(.*?)」——把文件放进数据目录再引用，或删掉这一栏$",
     r'\1 must be a relative path inside the data folder (e.g. content/assets/…); received "\2" — '
     r"put the file in the data folder and reference it there, or remove this field"),
    (r"^(\S+) 指向数据目录之外（或不在允许的子目录里），收到「(.*?)」——把文件放进数据目录再引用，或删掉这一栏$",
     r'\1 points outside the data folder (or outside the allowed subfolders); received "\2" — '
     r"put the file in the data folder and reference it there, or remove this field"),
    (r"^(.+?) 的 id 不能含 / \\ \? # % @ 或控制字符，也不能是 \. 或 \.\.——改个名字$",
     r"The id of \1 must not contain / \\ ? # % @ or control characters, and cannot be . or .. — rename it"),
    (r"^参考音频只能是数据目录里的相对路径，收到「(.*?)」——改成 content/assets/… 再出片$",
     r'The reference audio must be a relative path inside the data folder; received "\1" — change it to content/assets/… and generate again'),
    (r"^锚图不在数据目录 content/ 里：(.+?)——在角色页重新上传一张$",
     r"The anchor image is not inside the data folder's content/: \1 — upload it again on the character page"),
    (r"^锚图不是读得出来的图片：(.+?)——在角色页重新上传一张$",
     r"The anchor image is not a readable picture: \1 — upload it again on the character page"),
    (r"^锚图文件名不合法$", r"Invalid anchor image file name"),
    (r'^phone_ui_inserts\[(\d+)\] 的 id「(.*?)」不能超过 (\d+) 个字，也不能含 / \\ : \* \? " < > \| % 或控制字符——改成 UI_2 这样的写法$',
     r'phone_ui_inserts[\1]: id "\2" must be at most \3 characters and must not contain / \\ : * ? " < > | % '
     r"or control characters — use something like UI_2"),
    # 配乐文件用不了的原因（post_layers.music_file_problem）：后期页就绪检查的 reason、写盘校验括号里的补充说明
    (r"^不是数据目录里的相对路径$", r"not a relative path inside the data folder"),
    (r"^不在数据目录 content/ 里$", r"not inside the data folder's content/"),
    (r"^文件不存在$", r"file not found"),
    (r"^没有写文件$", r"no file given"),
    (r"^不知道数据目录在哪$", r"the data folder is unknown"),
    (r"^混音：命令行超出了系统的长度上限——请把这条任务记录发给开发者$",
     r"Mixing: the command line exceeded the system length limit — please send this task record to the developer"),
    # 配乐文件用不了（后期层 PUT / 存草稿）：直说规则和怎么办，原因（music_file_problem）单独再翻
    (r"^music\[(\d+)\] 的 file 用不了：(.+?)——只能用上传到数据目录 content/ 下的音频，在「配乐」里重新上传$",
     r"music[\1]: this file cannot be used (\2) — only audio uploaded into the data folder's content/ works; upload it again under Music"),
    (r"^草稿里的配乐 music\[(\d+)\] 用不了：(.+?)——只能用上传到数据目录 content/ 下的音频；删掉这一条，建好项目后在「后期层」的「配乐」里上传。没有保存$",
     r"Draft music[\1] cannot be used (\2) — only audio uploaded into the data folder's content/ works; remove this entry and "
     r"upload it under Music in Post layers once the project exists. Not saved"),
]


PATTERNS: list[tuple[str, str]] = [
    # ---- 按题材审稿（DESIGN §49）、剧本工艺审稿与播客真人感审稿：整句全部带 ^$，句头「按题材卡「」「按工艺清单审稿」「按真人感清单审稿」
    #      很特别，放最前面不会误伤别的规则
    *_GENRE_REVIEW_RULES,
    *_CRAFT_REVIEW_RULES,
    *_PODCAST_REVIEW_RULES,
    # ---- 图片集引擎（DESIGN §40）：接口 / 出图 / 后处理 / 打包的人话 + 项目文件校验（带前缀的整句 + 单独）。
    #      整句规则全部带 ^$，排在最前面——只翻前缀的兜底（「示例没通过结构校验…：(.+)」）在下面，先命中就会把里层的中文留下
    *_SET_ROUTER_RULES,
    *_composed([("改动没通过校验，没有写盘", "The change failed validation and was not saved"),
                ("示例没通过结构校验（这是示例文件本身的问题）", "The example failed structural validation (a problem in the example file itself)")],
               _SET_SPEC_ERRORS),
    *[(f"^{pat}$", rep) for pat, rep in _SET_SPEC_ERRORS],
    # ---- 配乐（DESIGN §44）：接口 / 出配乐 / 投放的人话 + 项目文件校验（带前缀的整句 + 单独）。
    #      投放被目标项目挡下来时，里层是 post.py / picturebook 的那几条，先展开成整句
    *_composed([("目标项目没接受这条配乐", "The destination project rejected this track")], _POST_BLOCK_ERRORS),
    *_composed([("目标绘本读不出来", "The destination picture book could not be read"),
                ("目标绘本没接受这条配乐", "The destination picture book rejected this track")], _PB_SPEC_ERRORS),
    *_MUSIC_ROUTER_RULES,
    *_composed([("改动没通过校验，没有写盘", "The change failed validation and was not saved"),
                ("示例没通过结构校验（这是示例文件本身的问题）", "The example failed structural validation (a problem in the example file itself)")],
               _MUSIC_SPEC_ERRORS),
    *[(f"^{pat}$", rep) for pat, rep in _MUSIC_SPEC_ERRORS],
    (r"^生图已绑定 (.+?)，但没有它的参数范围记录（画幅 / 尺寸 / 时长），越界只会在提交时被服务商报错——不影响开始制作$",
     r"Image generation is bound to \1, but there is no parameter-range record for it (aspect ratio / size / duration); out-of-range values are only reported by the provider at submit time — this does not block starting production"),
    (r"^视频已绑定 (.+?)，但没有它的参数范围记录（画幅 / 尺寸 / 时长），越界只会在提交时被服务商报错——不影响开始制作$",
     r"Video is bound to \1, but there is no parameter-range record for it (aspect ratio / size / duration); out-of-range values are only reported by the provider at submit time — this does not block starting production"),
    # episodes.py / runtime.py：重投纪律（DESIGN §24）
    (r"^这一镜（(.+?)）的首帧同一份提示词已经出过 (\d+) 次了——原样重提第 (\d+) 次通常还是一样；改画面/动作文字（改文字重出）、换模型对比（多模型对比），或接受当前结果$",
     r"Shot \1: the same first-frame prompt has already been generated \2 times — resubmitting it unchanged (attempt \3) usually gives the same result; edit the picture/motion text (edit & regenerate), compare models (multi-model comparison), or accept the current result"),
    (r"^这一镜（(.+?)）的视频同一份提示词已经出过 (\d+) 次了——原样重提第 (\d+) 次通常还是一样；改画面/动作文字（改文字重出）、换模型对比（多模型对比），或接受当前结果$",
     r"Shot \1: the same video prompt has already been generated \2 times — resubmitting it unchanged (attempt \3) usually gives the same result; edit the picture/motion text (edit & regenerate), compare models (multi-model comparison), or accept the current result"),
    (r"^换模型对比不记重拍——在镜头页「首帧对比试跑」里出候选，满意再采用$",
     r'''Model comparison is not recorded as a retake — generate candidates in the shot page's "First-frame comparison" section and adopt one you like'''),
    # app.py：422 校验错误。validation_message 逐段翻「字段 X：<毛病>」再拼（多字段用「；」连），所以规则按段写；
    # 整句形状的几条留着给直接过 tr 的整句（单字段时两种形状一样）
    (r"^请求参数不对——字段 (\S+)：必填，没有给$", r"Invalid request — field \1 is required"),
    (r"^请求参数不对——字段 (\S+)：要是文本$", r"Invalid request — field \1 must be text"),
    (r"^请求参数不对——字段 (\S+)：要是整数$", r"Invalid request — field \1 must be an integer"),
    (r"^请求参数不对——字段 (\S+)：要是数字$", r"Invalid request — field \1 must be a number"),
    (r"^请求参数不对——字段 (\S+)：要是数组$", r"Invalid request — field \1 must be an array"),
    (r"^请求参数不对——字段 (\S+)：要是对象$", r"Invalid request — field \1 must be an object"),
    (r"^请求参数不对——(.+)$", r"Invalid request — \1"),
    (r"^字段 (\S+)：必填，没有给$", r"field \1 is required"),
    (r"^字段 (\S+)：要是文本$", r"field \1 must be text"),
    (r"^字段 (\S+)：要是整数$", r"field \1 must be an integer"),
    (r"^字段 (\S+)：要是数字$", r"field \1 must be a number"),
    (r"^字段 (\S+)：要是 true / false$", r"field \1 must be true / false"),
    (r"^字段 (\S+)：要是数组$", r"field \1 must be an array"),
    (r"^字段 (\S+)：要是对象$", r"field \1 must be an object"),
    (r"^字段 (\S+)：不是合法的 JSON$", r"field \1 is not valid JSON"),
    (r"^字段 (\S+)：(.+)$", r"field \1: \2"),   # 其余的是 pydantic 自己的英文 msg
    # ---- project.py：下一步 hint
    *_failure_kind_combos(),
    (r"^(\d+) 镜提交失败（(.+?)），概览页有原因和重提入口$",
     r"\1 shots failed to submit (\2); the overview page has the reasons and a resubmit entry"),
    (r"^还有 1 镜没有可用首帧$", r"1 shot still lacks a usable first frame"),
    (r"^还有 (\d+) 镜没有可用首帧$", r"\1 shots still lack a usable first frame"),
    (r"^1 镜待审；有台词的镜可以先跑台词识别闸$", r"1 shot pending review; shots with lines can run the dialogue ASR gate first"),
    (r"^(\d+) 镜待审；有台词的镜可以先跑台词识别闸$", r"\1 shots pending review; shots with lines can run the dialogue ASR gate first"),
    (r"^1 镜待审$", r"1 shot pending review"),
    (r"^(\d+) 镜待审$", r"\1 shots pending review"),
    (r"^还有 1 镜没有可用视频$", r"1 shot still lacks a usable video"),
    (r"^还有 (\d+) 镜没有可用视频$", r"\1 shots still lack a usable video"),
    # ---- project.py：角色 / 场景 / 锚图
    (r"^项目已开始制作，改「(.+?)」会改变引用它的镜次的提示词或参考图（当前没有镜引用它）——要改就复制成新版本再改；只想改声音描述以外的备注类内容，请用后期层。$",
     r'Production has started; editing "\1" would change the prompts or reference images of the shots that reference it (no shot currently references it) — '
     r"copy to a new version to make the change; for note-type content other than the voice description, use post layers."),
    (r"^项目已开始制作，改「(.+?)」会改变引用它的镜次的提示词或参考图（(.+?)）——要改就复制成新版本再改；只想改声音描述以外的备注类内容，请用后期层。$",
     r'Production has started; editing "\1" would change the prompts or reference images of the shots that reference it (\2) — '
     r"copy to a new version to make the change; for note-type content other than the voice description, use post layers."),
    (r"^项目已开始制作，给「(.+?)」换锚图会改变引用它的镜次的提示词或参考图（当前没有镜引用它）——要改就复制成新版本再改；只想改声音描述以外的备注类内容，请用后期层。$",
     r'Production has started; changing the anchor image of "\1" would change the prompts or reference images of the shots that reference it (no shot currently references it) — '
     r"copy to a new version to make the change; for note-type content other than the voice description, use post layers."),
    (r"^项目已开始制作，给「(.+?)」换锚图会改变引用它的镜次的提示词或参考图（(.+?)）——要改就复制成新版本再改；只想改声音描述以外的备注类内容，请用后期层。$",
     r'Production has started; changing the anchor image of "\1" would change the prompts or reference images of the shots that reference it (\2) — '
     r"copy to a new version to make the change; for note-type content other than the voice description, use post layers."),
    (r"^项目已开始制作，给「(.+?)」换声音会改变引用它的镜次的提示词或参考图（当前没有镜引用它）——要改就复制成新版本再改；只想改声音描述以外的备注类内容，请用后期层。$",
     r'Production has started; changing the voice of "\1" would change the prompts or reference images of the shots that reference it (no shot currently references it) — '
     r"copy to a new version to make the change; for note-type content other than the voice description, use post layers."),
    (r"^项目已开始制作，给「(.+?)」换声音会改变引用它的镜次的提示词或参考图（(.+?)）——要改就复制成新版本再改；只想改声音描述以外的备注类内容，请用后期层。$",
     r'Production has started; changing the voice of "\1" would change the prompts or reference images of the shots that reference it (\2) — '
     r"copy to a new version to make the change; for note-type content other than the voice description, use post layers."),
    (r"^项目已开始制作，(.+?)会改变引用它的镜次的提示词或参考图（(.+?)）——要改就复制成新版本再改；只想改声音描述以外的备注类内容，请用后期层。$",
     r"Production has started; \1 would change the prompts or reference images of the shots that reference it (\2) — "
     r"copy to a new version to make the change; for note-type content other than the voice description, use post layers."),
    (r"^kind 只能是 (.+)$", r"kind must be one of \1"),
    (r"^已经有一个「(.+?)」$", r'"\1" already exists'),
    (r"^没有这个角色/场景：(.+)$", r"No such character/scene: \1"),
    (r"^「(.+?)」还被这些镜引用：(.+?)——先把它从这些镜里移掉$", r'"\1" is still referenced by these shots: \2 — remove it from those shots first'),
    (r"^图片数据不是合法的 base64：(.+)$", r"Image data is not valid base64: \1"),
    (r"^图片超过 (\d+)MB$", r"Image exceeds \1MB"),
    (r"^不是可读的图片文件：(.+)$", r"Not a readable image file: \1"),
    (r"^只接受 PNG / JPEG / WebP，收到的是 未知格式$", r"Only PNG / JPEG / WebP are accepted; received an unknown format"),
    (r"^只接受 PNG / JPEG / WebP，收到的是 (.+)$", r"Only PNG / JPEG / WebP are accepted; received \1"),
    (r"^锚图文件不存在：(.+)$", r"Anchor image file does not exist: \1"),
    # ---- project.py：重提失败任务
    (r"^(.+?) 是内容被审核拦截，没有重提：改写这些镜的画面/台词描述后再重拍（或勾选「强制重提」）$",
     r'''\1 were blocked by content moderation and not resubmitted: rewrite those shots' visual/dialogue descriptions, then retake (or tick "Force resubmit")'''),
    (r"^提交结果不明的任务要先对账：(.+)$", r"Jobs with an unknown submission outcome must be reconciled first: \1"),
    # ---- project.py / scripts.py：项目已存在
    (r"^项目 (.+?) 已经存在，换一个 id，或者去编辑现有项目。$", r"Project \1 already exists; choose another id, or edit the existing project."),
    (r"^项目 (.+?) 已经存在，换一个 id。$", r"Project \1 already exists; choose another id."),
    (r"^项目 (.+?) 已经存在，不会覆盖。$", r"Project \1 already exists and will not be overwritten."),
    # ---- episodes.py
    (r"^没有这个项目：(.+)$", r"No such project: \1"),
    (r"^(.+?) 不能为空——它是首帧/视频提示词的正文$", r"\1 cannot be empty — it is the body of the first-frame/video prompt"),
    (r"^已经有一镜叫「(.+?)」$", r'A shot named "\1" already exists'),
    (r"^新镜需要填 (.+?)（画面 / 美术 / 动作）$", r"A new shot needs \1 (shot / art / motion)"),
    (r"^没有这一镜：(.+)$", r"No such shot: \1"),
    # ---- poster.py：海报（DESIGN §36）
    (r"^字体文件不存在：(.+)$", r"Font file does not exist: \1"),
    (r"^这一镜还没有首帧或视频，没法当海报底图：(.+)$", r"This shot has no first frame or video yet, so it cannot be the poster base: \1"),
    (r"^海报底图不存在：(.+)$", r"Poster base image does not exist: \1"),
    (r"^海报文件已存在，不覆盖：(.+)$", r"Poster file already exists and will not be overwritten: \1"),
    (r"^海报叠字失败（ffmpeg）：(.+)$", r"Poster text overlay failed (ffmpeg): \1"),
    (r"^抽取视频首帧失败（ffmpeg）：(.+)$", r"Failed to extract the video's first frame (ffmpeg): \1"),
    (r"^视频不存在：(.+)$", r"Video does not exist: \1"),
    (r"^海报底图生成失败：(.+)$", r"Poster base image generation failed: \1"),
    (r"^1 次生图请求（(.+?)）$", r"1 image request (\1)"),
    *_shot_link_combos(),
    (r"^「(.+?)」还有关联：(.+?)——先解开这些关联再删$", r'"\1" still has links: \2 — unlink them before deleting'),
    # ---- scripts.py
    (r"^⚠ 没通过结构校验，保存前必须先在下面的 JSON 里改掉：(.+)$", r"⚠ Failed structural validation; fix it in the JSON below before saving: \1"),
    (r"^草稿里没有这一镜：(.+)$", r"No such shot in the draft: \1"),
    (r"^起草遇到没预料到的错误（请把这条完整信息发给开发者）：(.+)$",
     r"Drafting hit an unexpected error (please send this full message to the developer): \1"),
    (r"^起草失败：项目 (\S+?) 还没有剧本——先出剧本，再拆镜$",
     r"Drafting failed: project \1 has no screenplay yet — write the screenplay first, then break it into shots"),
    (r"^起草失败：不认识的题材：(.+)$", r'Drafting failed: unknown genre "\1"'),
    (r"^起草失败：不认识的形态：(.+)$", r'Drafting failed: unknown form "\1"'),
    (r"^起草失败：(.+)$", r"Drafting failed: \1"),
    (r"^重写这一镜遇到没预料到的错误（请把这条完整信息发给开发者）：(.+)$",
     r"Rewriting this shot hit an unexpected error (please send this full message to the developer): \1"),
    (r"^重写这一镜失败：(.+)$", r"Rewriting this shot failed: \1"),
    (r"^没有这个 job：(.+)$", r"No such job: \1"),
    (r"^job 记录损坏：(.+)$", r"Job record is corrupted: \1"),
    (r"^没有这个示例：(.+)$", r"No such example: \1"),
    (r"^示例没通过结构校验（这是示例文件本身的问题）：(.+)$", r"The example failed structural validation (a problem in the example file itself): \1"),
    (r"^示例里的运镜不在当前镜头库里：(.+)$", r"The example's camera moves are not in the current camera library: \1"),
    (r"^草稿没通过结构校验，没有保存：(.+)$", r"The draft failed structural validation and was not saved: \1"),
    (r"^草稿里的运镜不合法，没有保存：(.+)$", r"The draft has invalid camera moves and was not saved: \1"),
    # ---- post.py / tasks.py
    *[(f"^{pat}$", rep) for pat, rep in _POST_BLOCK_ERRORS],
    (r"^这里只能改交付面遮挡区（(.+?)），不能改 delivery 的 (.+)$",
     r"Only the delivery-surface overlay areas (\1) can be changed here, not delivery's \2"),
    # 字幕卡 / 角标渲染（post_layers，插卡预览的 425 与后期合成失败都会带出来）
    (r"^字幕卡 (.+?) 没有文字：写 lines 或 screen$", r"Text card \1 has no text: add lines or screen"),
    (r"^后期文字字体不可用：(.+?)：(.+)$", r"Post-production text font unavailable: \1: \2"),
    # ---- 参考槽位用途 / 尾帧续接（DESIGN §30）：lint、编辑接口、预估/概览。lint 告警在概览页是问题卡（两半各自过 tr），
    #      所以用 _carded 展开成「整句 + 两半」
    *_carded((r"reference_roles 必须是列表：(.+)", r"reference_roles must be a list: \1")),
    *_carded((r"reference_roles\[(\d+)\] 写法不对", r"reference_roles[\1] is malformed"),
             (r"target 填参考图（prev_frame / tail / prop / cast:<id>），use 填取它什么，control / ignore 填控制与不得控制的方面",
              r"target names the reference image (prev_frame / tail / prop / cast:<id>), use says what to take from it, control / ignore list the aspects it may and may not control")),
    *_carded((r"参考图 (\S+?) 的「(.+?)」既在控制里又在不得控制里", r'for reference \1, "\2" is listed both under control and under ignore'),
             (r"留一边，模型没法同时听两句", r"keep one; the model cannot obey both")),
    *_carded((r"参考图用途写了 prev_frame，但本镜没有 ref_frame / ref_tail", r"a reference role targets prev_frame, but this shot has no ref_frame / ref_tail"),
             (r"没有上一镜的参考槽，这条说明会指向不存在的图；删掉它或给本镜选参考镜",
              r"there is no previous-shot slot, so the note points at a non-existent image; delete it or pick a reference shot")),
    *_carded((r"参考图用途写了 tail，但本镜没有作参考图的尾帧续接", r"a reference role targets tail, but this shot has no tail relay in reference mode"),
             (r"删掉它，或把尾帧续接设成「作参考图」", r'delete it, or set the tail relay to "use as reference image"')),
    *_carded((r"参考图 (\S+?) 写了两条用途", r"reference \1 has two roles"),
             (r"合成一条，后一条会盖住前一条的意思", r"merge them into one; the later one overrides the earlier")),
    *_carded((r"写了 (\d+) 条参考图用途，本镜实际只带 (\d+) 张参考图", r"\1 reference roles are written, but this shot only carries \2 reference images"),
             (r"多出的说明指向不存在的图；按依赖槽 → 角色定妆 → 道具的顺序核对",
              r"the extra notes point at images that do not exist; check the order: dependency slot → character anchors → prop")),
    *_carded((r"本镜要带 (\d+) 张参考图，出图模型 (\S+?) 最多收 (\d+) 张", r"this shot carries \1 reference images, but the image model \2 accepts at most \3"),
             (r"超出的会被丢掉，槽位编号也就不准；减少出场锚图或去掉道具参考",
              r"the extras are dropped and the slot numbering is no longer truthful; use fewer anchor images or drop the prop reference")),
    *_carded((r"ref_tail 写法不对", r"ref_tail is malformed"), (r"填源镜镜号，或 (.+)", r"write the source shot id, or \1")),
    *_carded((r"尾帧续接的源镜 (\S+?) 不存在", r"tail relay source shot \1 does not exist"), (r"改成现有镜号", r"use an existing shot id")),
    *_carded((r"尾帧续接的源镜 (\S+?) 不在本镜之前", r"tail relay source shot \1 is not before this shot"),
             (r"尾帧要等源镜的视频过检，只能接前面的镜", r"the tail frame waits for the source video to pass QC, so only earlier shots can be relayed")),
    *_carded((r"尾帧续接接的是 (\S+?)，参考镜（ref_frame）却是 (\S+?)", r"the tail relay follows \1 but the reference shot (ref_frame) is \2"),
             (r"两者要指向同一镜，尾帧会取代参考镜的首帧", r"both must point at the same shot; the tail frame replaces the reference shot's first frame")),
    *_carded((r"源镜 (\S+?) 收在「(.+?)」，本镜是「(.+?)」，尾帧直接当首帧时脸在画面里太小、五官看不清，长相容易走样",
              r'source shot \1 ends on a "\2" and this shot is a "\3"; used directly as the first frame, the tail frame shows the face too small to read its features, so the likeness tends to drift'),
             (r"先在两镜中间出一张中景桥接帧再接这一镜：场景照源镜最后一帧，脸照定妆图",
              r"first render a medium-shot bridging frame between the two shots, then continue into this one: the setting follows the source shot's last frame, the face follows the anchor image")),
    *_carded((r"源镜 (\S+?) 收在「(.+?)」，本镜是「(.+?)」，尾帧作参考图时脸在画面里太小、五官看不清，长相容易走样",
              r'source shot \1 ends on a "\2" and this shot is a "\3"; used as a reference image, the tail frame shows the face too small to read its features, so the likeness tends to drift'),
             (r"先在两镜中间出一张中景桥接帧再接这一镜：场景照源镜最后一帧，脸照定妆图",
              r"first render a medium-shot bridging frame between the two shots, then continue into this one: the setting follows the source shot's last frame, the face follows the anchor image")),
    (r"^reference_roles\[(\d+)\] 要是对象（target / use / control / ignore）$", r"reference_roles[\1] must be an object (target / use / control / ignore)"),
    (r"^reference_roles\[(\d+)\] 里不认识的字段：(.+)$", r"reference_roles[\1] has unknown fields: \2"),
    (r"^reference_roles\[(\d+)\]：target（哪张参考图）不能为空$", r"reference_roles[\1]: target (which reference image) cannot be empty"),
    (r"^reference_roles\[(\d+)\]\.use 要是一句话（字符串）$", r"reference_roles[\1].use must be text (a string)"),
    (r"^reference_roles\[(\d+)\]\.(\w+) 要是方面词列表（脸 / 发型 / 服装 / 姿势 / 机位 / 光线 / 场景 / 色调）$",
     r"reference_roles[\1].\2 must be a list of aspects (face / hairstyle / wardrobe / pose / camera position / lighting / setting / colour grade)"),
    (r"^reference_roles\[(\d+)\]：use / control / ignore / fidelity 至少写一项，不然这条说明是空的$",
     r"reference_roles[\1]: write at least one of use / control / ignore / fidelity, otherwise the note is empty"),
    (r"^reference_roles\[(\d+)\]\.fidelity 要是 1–100 的百分数（相似度约多少）$",
     r"reference_roles[\1].fidelity must be a percentage between 1 and 100 (roughly how much likeness)"),
    (r"^海报布局只认 auto / top / bottom，给的是 (.+)$", r"Poster layout must be auto / top / bottom, got \1"),
    (r"^字体读不出来：(.+?)（(.+)）$", r"Cannot read font \1 (\2)"),
    (r"^海报底图读不出来：(.+?)（(.+)）$", r"Cannot read the poster base image \1 (\2)"),
    (r"^海报写盘失败：(.+)$", r"Failed to write the poster file: \1"),
    (r"^没找到 ffmpeg，抽视频首帧要用它——把 ffmpeg 放进 PATH 再试$",
     r"ffmpeg not found; it is needed to extract the first frame of a video — put ffmpeg on PATH and retry"),
    (r"^ref_tail 里不认识的字段：(.+)$", r"ref_tail has unknown fields: \1"),
    (r"^ref_tail\.mode 只能是 reference（作参考图）/ direct（直接当首帧），收到「(.+?)」$",
     r'ref_tail.mode must be reference (use as reference image) / direct (use as the first frame); received "\1"'),
    *_carded((r"有尾帧续接但没写 entry_state", r"has a tail relay but no entry_state"),
             (r"接尾帧的镜要写清入口（即源镜收尾那一刻：谁站在哪、手里有什么、脸朝哪看着什么）",
              r"a shot that relays a tail frame must spell out its entry (the moment the source shot ends: who stands where, what is in their hands, which way they face and what they look at)")),
    (r"^等 (\S+?) 视频过检后抽尾帧$", r"Waiting for the \1 video to pass QC before extracting its tail frame"),
    (r"^(\d+) 镜在等 (.+?) 的视频过检后抽尾帧$", r"\1 shot(s) are waiting for the \2 video(s) to pass QC before their tail frames are extracted"),
    # ---- lint.check_overlays（DESIGN §27）：问题卡，整句 + 两半
    *_carded((r"可读文字「(.+?)」放在「(.+?)」，会被交付面的「(.+?)」挡住",
              r'''readable text "\1" placed at "\2" will be covered by the delivery surface's "\3"'''),
             (r"挪到安全区或换载体", r"move it into the safe area or change the carrier")),
    *_carded((r"字幕在画面底部，落在交付面的「(.+?)」里", r'''subtitles sit at the bottom of the frame, inside the delivery surface's "\1"'''),
             (r"把字幕改到顶部，或把底部安全边距调到 (\d+)% 以上（现在 (.+?)%），或换遮挡区预设",
              r"move subtitles to the top, raise the bottom safe margin above \1% (now \2%), or switch the overlay preset"), head="episode"),
    *_carded((r"字幕在画面顶部，落在交付面的「(.+?)」里", r'''subtitles sit at the top of the frame, inside the delivery surface's "\1"'''),
             (r"把字幕改到底部，或调大 top_margin_ratio 避开，或换遮挡区预设",
              r"move subtitles to the bottom, raise top_margin_ratio to clear it, or switch the overlay preset"), head="episode"),
    *_carded((r"字幕在画面底部，落在交付面的「(.+?)」里", r'''subtitles sit at the bottom of the frame, inside the delivery surface's "\1"'''),
             (r"缩窄字幕（减小字号或行宽），或换遮挡区预设", r"narrow the subtitles (smaller font or line width), or switch the overlay preset"), head="episode"),
    *_carded((r"字幕在画面顶部，落在交付面的「(.+?)」里", r'''subtitles sit at the top of the frame, inside the delivery surface's "\1"'''),
             (r"缩窄字幕（减小字号或行宽），或换遮挡区预设", r"narrow the subtitles (smaller font or line width), or switch the overlay preset"), head="episode"),
    (r"^没有这张插卡：(.+)$", r"No such insert card: \1"),
    (r"^这些镜次还没有视频，没东西可识别：(.+)$", r"These shots have no video yet; nothing to recognize: \1"),
    (r"^还有镜次没有视频，后期合成要求全部镜次都已出片：(.+)$", r"Some shots have no video yet; post-production requires every shot to be rendered: \1"),
    (r"^没找到中文字体（(.+?)）——在 spec 的 post_graphics\.fonts 里指定字体文件路径。$",
     r"No Chinese font found (\1) — set font file paths in post_graphics.fonts in the spec."),
    (r"^台词配音已开启，但这些说话角色没有绑定 TTS 音色：(.+?)——去角色页从音色库绑一个，或关掉配音。$",
     r"Dialogue dubbing is on, but these speaking characters have no TTS voice bound: \1 — bind one from the voice library on the Characters page, or turn dubbing off."),
    (r"^没有这个任务：(.+)$", r"No such task: \1"),
    # ---- routers/modules.py / post.py：模块注册表（DESIGN §38）
    (r"^没有这个模块：(.+)$", r"No such module: \1"),
    (r"^(.+?) 的「(.+?)」已经在跑（task (.+?)），等它结束再点。$", r'"\2" for \1 is already running (task \3); wait for it to finish.'),
    # ---- generate.py：后台轮询收工（DESIGN §34）
    (r"^等了 (\d+) 分钟服务商还没返回：(.+?)——下次打开项目或点「生成」会接着等$",
     r"Waited \1 minutes and the provider still has not returned: \2 — reopening the project or clicking Generate will keep waiting"),
    # ---- asr_qc.py：重摇原因
    (r"^ASR 质检：没有识别到语音（期望「(.+?)」）$", r'ASR check: no speech recognized (expected "\1")'),
    (r"^ASR 质检：无台词镜里出现语音「(.+?)」$", r'ASR check: speech found in a shot with no lines: "\1"'),
    (r"^ASR 质检：字错率 (.+?)（期望「(.+?)」，听到「(.+?)」）$", r'ASR check: character error rate \1 (expected "\2", heard "\3")'),
    # ---- post_layers.py
    (r"^这些说话角色没有绑定 TTS 音色，无法配音：(.+?)——去角色页从音色库绑一个$",
     r"These speaking characters have no TTS voice bound, so they cannot be dubbed: \1 — bind one from the voice library on the Characters page"),
    (r"^(\S+) 第 (\d+) 句「(.+?)」到 ([\d.]+)s，超过本镜 ([\d.]+)s$", r'''\1 line \2 "\3" ends at \4s, exceeding this shot's \5s'''),
    (r"^旁白第 (\d+) 句「(.+?)」写了逐句声线 edge，但旁白引擎是「(.+?)」——逐句声线只对 edge 引擎生效：删掉这句的 edge，或把旁白换成 edge 音色$",
     r'''Narration line \1 "\2" sets a per-line edge voice, but the narration engine is "\3" — per-line voices only work with the edge engine: remove this line's edge, or switch the narration to an edge voice'''),
    (r"^静音区间 silence\[(\d+)\] 写得不对：要是 \{shot, at, dur\} 对象，at 不小于 0，dur 大于 0、不超过 ([\d.]+) 秒$",
     r"Silence range silence[\1] is malformed: it must be a {shot, at, dur} object with at ≥ 0 and dur greater than 0 and at most \2 seconds"),
    (r"^静音区间 silence\[(\d+)\] 的镜号「(.+?)」不在时间线里$", r'''Silence range silence[\1]: shot "\2" is not on the timeline'''),
    (r"^静音区间 silence\[(\d+)\] 从镜 (\S+) 的 ([\d.]+)s 开始，但这一镜成片只有 ([\d.]+)s——改小 at 或删掉这条$",
     r"Silence range silence[\1] starts \3s into shot \2, but the rendered shot is only \4s long — lower at or delete this entry"),
    # 合成侧兜手改 spec 的人话（分段流式合成一并补的）与清单里的 skipped 原因（GET post-layers 的 post 块带出来）
    (r"^插卡「(.+?)」的 dur 要是大于 0 的秒数，现在是「(.*?)」$", r'''Insert card "\1": dur must be a number of seconds greater than 0, got "\2"'''),
    (r"^插卡 (.+?) 的 after 形成了环（自引用或互相引用），没有接到任何镜上$",
     r"Insert cards \1: after forms a cycle (self-reference or mutual reference) and never reaches a shot"),
    (r"^插卡 (.+?) 没有文字：写 lines 或 screen$", r"Insert card \1 has no text: add lines or screen"),
    (r"^旁白第 (\d+) 句「(.+?)」的 at 要是不小于 0 的秒数，现在是「(.*?)」$", r'''Narration line \1 "\2": at must be seconds ≥ 0, got "\3"'''),
    (r"^旁白 / 画面文字挂在的「(.+?)」不在时间线上（镜号不存在，或插卡没接到任何镜上）$",
     r'''"\1", which narration / on-screen text is attached to, is not on the timeline (no such shot, or the insert card never reaches a shot)'''),
    (r"^配乐「(.+?)」不是可读的音频文件：(.+)$", r'''Music track "\1" is not a readable audio file: \2'''),
    (r"^画面文字 notebook_text\[(\d+)\] 的 (at|until) 要是不小于 0 的秒数，现在是「(.*?)」$",
     r'''On-screen text notebook_text[\1]: \2 must be seconds ≥ 0, got "\3"'''),
    (r"^画面文字 notebook_text\[(\d+)\] 的 y 要是 0 到 1 之间的数，现在是「(.*?)」$",
     r'''On-screen text notebook_text[\1]: y must be a number between 0 and 1, got "\2"'''),
    (r"^画面文字 notebook_text\[(\d+)\] 的 size 要是不小于 1 的数，现在是「(.*?)」$",
     r'''On-screen text notebook_text[\1]: size must be a number of at least 1, got "\2"'''),
    (r"^画面文字 notebook_text\[(\d+)\] 的 until 要大于 at，现在 at = ([\d.]+)，until = ([\d.]+)$",
     r"On-screen text notebook_text[\1]: until must be greater than at; at = \2, until = \3"),
    (r"^画面文字 notebook_text\[(\d+)\] 从镜 (\S+) 的 ([\d.]+)s 开始，但这一镜成片只有 ([\d.]+)s——改小 at 或删掉这条$",
     r"On-screen text notebook_text[\1] starts \3s into shot \2, but the rendered shot is only \4s long — lower at or delete this entry"),
    (r"^后期合成写出的成片读不出来：(.+)$", r"The composited output cannot be read: \1"),
    (r"^后期合成的成片时长 ([\d.]+)s 与时间线 ([\d.]+)s 不符$", r"Composited output duration \1s does not match the timeline \2s"),
    (r"^旁白第 (\d+) 句「(.+?)」到 ([\d.]+)s，超过片尾 ([\d.]+)s，尾巴被截掉$",
     r'''Narration line \1 "\2" ends at \3s, past the film end \4s; its tail is cut off'''),
    (r"^起点 ([\d.]+)s 不早于片尾 ([\d.]+)s，没有混进成片$", r"Starts at \1s, not before the film end \2s; not mixed into the output"),
    (r"^start = ([\d.]+)s 不早于片尾 ([\d.]+)s，没有铺进成片$", r"start = \1s is not before the film end \2s; not laid into the output"),
    (r"^after「(.*?)」既不是镜号也不是别的插卡 id，没有接到时间线上$",
     r'''after "\1" is neither a shot id nor another insert card id; not placed on the timeline'''),
    (r"^after「(.*?)」指向的插卡也没有接到时间线上$", r'''after "\1" points to an insert card that is not on the timeline either'''),
    # 镜头改短后静音区间超出新时长（post_layers.silence_overrun_warnings）：问题卡，整句 + 两半
    *_carded((r"静音区间 silence\[(\d+)\] 到 ([\d.]+)s 才结束，超出了这一镜的时长 ([\d.]+)s",
              r"silence range silence[\1] ends at \2s, past this shot's duration of \3s"),
             (r"把这一镜改长，或改小 post_graphics\.silence 里这一条的 at / dur；不改的话后期块再保存会被拦下",
              r"lengthen this shot, or lower this entry's at / dur in post_graphics.silence; otherwise saving post layers will be blocked")),
    # ---- preview.py：时长预算 / 台词清晰度。这些告警在概览页拆成问题卡显示（impact / fix 两半各自过 tr），
    #      所以要用 _carded 展开成「整句 + 两半」；措辞跟 storyvideo/preview.py 现行的告警文案一致（对白预算按可用发声窗口核容量）
    *_carded((r"台词 (\d+) 字 ≈(.+?)s 纯语音，超过可用发声窗口 (.+?)s（声明 (.+?)s 扣掉开口等待 (.+?)s 和末尾反应 (.+?)s）",
              r"\1 characters of dialogue ≈ \2s of pure speech, exceeding the usable speaking window of \3s (declared \4s minus \5s lead-in and \6s tail reaction)"),
             (r"这镜装不下这些台词，加时长、拆镜或删词；不要靠更快语速硬凑。",
              r"this shot cannot hold these lines; add duration, split the shot or cut words — do not rely on faster delivery.")),
    *_carded((r"台词 (\d+) 字按偏慢语速要 ≈(.+?)s，可用发声窗口只有 (.+?)s",
              r"\1 characters of dialogue need ≈ \2s at a slow pace, but the usable speaking window is only \3s"),
             (r"偏紧：语气一慢就赶词，建议加 1–2 秒或把台词减一句。",
              r"tight: any slower delivery will rush the words; add 1–2 seconds or cut one line.")),
    *_carded((r"台词只占声明时长的 (\d+)%（(.+?)s / (.+?)s），确认这镜是有意留白/反应镜，不是漏写台词。",
              r"dialogue fills only \1% of the declared duration (\2s / \3s) — confirm this shot is an intentional pause/reaction shot, not missing lines.")),
    *_carded((r"(\d+) 镜台词共 (\d+) 字 ≈(.+?)s 纯语音，比声明的总时长 (.+?)s 多出 (\d+)%",
              r"\1 shots with \2 characters of dialogue ≈ \3s of pure speech, \5% over the declared total of \4s"),
             (r"报时长前按这个数字来，不要按段落拍脑袋估。",
              r"use this number when reporting duration instead of guessing by paragraph."),
             head="episode"),
    *_carded((r"台词「(.+?)」的 tone 含「(.+?)」", r'the tone of line "\1" contains "\2"'),
             (r"模型会字面执行成乱语，改成用音量/语速/情绪描述，并保留口齿清楚",
              r"the model will render it literally as mumbling; describe volume/pace/emotion instead and keep the diction clear")),
    *_carded((r"台词「(.+?)」只有 (\d+) 个字", r'line "\1" has only \2 character(s)'),
             (r"一两个字的台词模型经常不念或念错，合并进相邻句或改成 3 字以上",
              r"one- or two-character lines are often skipped or misread; merge into a neighboring line or extend to 3+ characters")),
    *_carded((r"画外音短句「(.+?)」", r'short off-screen line "\1"'),
             (r"画外 \+ 短句是丢句高发组合，改成看得见嘴的镜或加长到 5 字以上",
              r"off-screen plus short is the most common dropped-line combination; use a shot where the mouth is visible or extend to 5+ characters")),
    # ---- continuity.py：问题卡，整句 + 两半
    *_carded((r"有 ref_frame 但没写 entry_state", r"has ref_frame but no entry_state"),
             (r"参考链上的镜次要写清入口（动作做到了哪一步、谁站在哪、手里有什么、脸朝哪看着什么）",
              r"shots on the reference chain must state their entry (how far the action has got, who stands where, what is in their hands, which way they face and what they look at)")),
    *_carded((r"写了 entry_state，但上一镜 (\S+?) 没有 exit_state，接缝只有一半",
              r"has entry_state, but the previous shot \1 has no exit_state — the seam is only half defined")),
    *_carded((r"entry_state 与上一镜 exit_state 一字不差", r"entry_state is identical to the previous shot's exit_state"),
             (r"入口要在出口之后（时间前进），不是复制", r"the entry must come after the exit (time moves forward), not copy it")),
    *_carded((r"有 entry_state 没有 exit_state，下一镜没法接", r"has entry_state but no exit_state; the next shot has nothing to connect to")),
    *_carded((r"表演描述里有情绪形容词「(.+?)」", r'the performance description contains emotion adjectives "\1"'),
             (r"视频模型会放大成夸张表情；改写成镜头拍得到的动作（目光落在哪、停多久、一个停顿、手上正做的事）",
              r"the video model will exaggerate them into over-acting; rewrite as actions the camera can see (where the eyes land and for how long, a pause, what the hands are doing)")),
    *_carded((r"shot 里读不出景别（特写/近景/中景/全景…）", r"no framing size can be read from shot (close-up/medium close-up/medium/wide…)"),
             (r"相邻镜同景别硬切是顿挫的第一来源", r"adjacent shots hard-cutting at the same size is the top cause of choppy edits")),
    *_carded((r"角色描述里有「(.+?)」", r'the character description contains "\1"'),
             (r"表情逐镜写，角色块只写长相/发型/服装/身材", r"write expressions per shot; the character block should only cover looks/hair/wardrobe/build"), head="cast"),
    *_carded((r"beat 的 t 写法要像「0-3」或「0–3」，现在是 (.+)", r'beat t should look like "0-3" or "0–3"; currently \1')),
    *_carded((r"beat (.+?) 回到了更早的时间", r"beat \1 goes back to an earlier time"), (r"时间只能前进", r"time can only move forward")),
    *_carded((r"beat (.+?) 超过了 dur (.+?)s", r"beat \1 exceeds dur \2s")),
    # ---- lint.py：观众可见性 / 揭示保护（DESIGN §28）：问题卡，整句 + 两半
    *_carded((r"正文写到了「(.+?)」的内容，但揭示要等到 (\S+?)", r'the text gives away "\1", but the reveal must wait until \2'),
             (r"删掉或改成不露信息的写法", r"cut it or rewrite it so nothing is disclosed")),
    *_carded((r"正文写到了「(.+?)」的内容，但这一集都不揭示", r'the text gives away "\1", which is not revealed anywhere in this episode'),
             (r"删掉或改成不露信息的写法", r"cut it or rewrite it so nothing is disclosed")),
    *_carded((r"「(.+?)」的揭示点 until 指向不存在的镜 (\S+?)", r'the reveal point (until) for "\1" refers to a non-existent shot \2'),
             (r"改成现有镜号，或留空表示整集不揭示", r"use an existing shot id, or leave it empty to withhold for the whole episode")),
    *_carded((r"「(.+?)」的揭示点 until（(\S+?)）不在本镜之后", r'the reveal point (until = \2) for "\1" is not after this shot'),
             (r"揭示要发生在保护开始之后；改成后面的镜号，或留空表示整集不揭示",
              r"the reveal must come after the withholding starts; use a later shot id, or leave it empty to withhold for the whole episode")),
    *_carded((r"「(.+?)」既是「(.+?)」的泄露载体（(.+?)），又被声明为画面里必须可读的文字",
              r'"\1" is both the carrier that would leak "\2" (\3) and declared as text that must be legible in the frame'),
             (r"观众会直接读到；删掉这条可读文字，或换一个载体", r"the audience will simply read it; remove that readable text, or pick another carrier")),
    *_carded((r"withhold\[(\d+)\] 写法不对", r"withhold[\1] is malformed"),
             (r"fact 填观众此刻还不能知道的事实，carrier 填会泄露它的载体（脸 / 戒指 / 台词），until 填从哪一镜起可以揭示（留空 = 整集不揭示）",
              r"fact is what the audience must not know yet, carrier is what would leak it (face / ring / a line), until is the shot from which it may be revealed (empty = whole episode)")),
    *_carded((r"withhold 必须是列表：(.+)", r"withhold must be a list: \1")),
    # ---- episodes.py：withhold 编辑接口
    (r"^withhold\[(\d+)\] 要是对象（fact / carrier / until）$", r"withhold[\1] must be an object (fact / carrier / until)"),
    (r"^withhold\[(\d+)\] 里不认识的字段：(.+)$", r"withhold[\1] has unknown fields: \2"),
    (r"^withhold\[(\d+)\]\.(\w+) 要是一句话（字符串）$", r"withhold[\1].\2 must be text (a string)"),
    (r"^withhold\[(\d+)\]：fact（先不揭示的事实）不能为空$", r"withhold[\1]: fact (what is being withheld) cannot be empty"),
    # ---- capabilities.py（标签三种形状各一条）
    *_labeled(r"只接受 (.+?) 秒 的镜次时长，这些镜越界：(.+)$", r"only accepts shot durations of \2 s; these shots are out of range: \3"),
    *_labeled(r"支持的分辨率是 (.+?)，spec 写的是 (.+)$", r"supports resolutions \2, but the spec says \3"),
    *_labeled(r"支持的画幅是 (.+?)，spec 写的是 (.+)$", r"supports aspect ratios \2, but the spec says \3"),
    *_labeled(r"不生成原生对白，有台词的镜要靠「台词配音」（旁白与插卡页）$",
              r"does not generate native dialogue; shots with lines must rely on dialogue dubbing (Narration & insert cards page)"),
    *_labeled(r"不接受首帧图，只能文生视频——首帧会被忽略，画面与定妆图对不上$",
              r"does not accept a first-frame image (text-to-video only) — the first frame will be ignored and the footage will not match the anchor look"),
    *_labeled(r"支持的尺寸档是 (.+?)，spec 写的是 (.+)$", r"supports image sizes \2, but the spec says \3"),
    *_labeled(r"不接受参考图，角色锚图和参考镜会被忽略$", r"does not accept reference images; character anchor images and reference shots will be ignored"),
    # 参考图上限（DESIGN §30）：能力检查接口逐镜数参考图
    *_labeled(r"最多收 (\d+) 张参考图，这些镜带的更多，超出的会被丢掉：(.+?)——减少出场锚图或去掉道具参考$",
              r"accepts at most \2 reference images; these shots carry more and the extras are dropped: \3 — use fewer anchor images or drop the prop reference"),
    *_labeled(r"最多收 (\d+) 张参考图（上一镜首帧/尾帧 → 出场定妆 → 道具），本集每镜都在上限内$",
              r"accepts at most \2 reference images (previous first/tail frame → character anchors → prop); every shot in this episode is within the limit"),
    (r"^没有 (.+?) / （未填模型） 的能力记录，参数越界只会在提交时被服务商报错$",
     r"No capability record for \1 / (no model set) — out-of-range parameters will only be reported by the provider at submission"),
    (r"^没有 (.+?) / (.+?) 的能力记录，参数越界只会在提交时被服务商报错$",
     r"No capability record for \1 / \2 — out-of-range parameters will only be reported by the provider at submission"),
    # ---- model_catalog.py
    (r"^(.+?) 目录（(.+?) 拉取）里没有模型 (.+?)——id 写错或已下线，提交会被拒绝$",
     r"The \1 catalog (fetched \2) has no model \3 — the id is wrong or the model was retired; submission will be rejected"),
    (r"^(.+?) 目录里 (\S+) 不出图（输出模态：(.+?)）$", r"In the \1 catalog, \2 does not output images (output modalities: \3)"),
    (r"^(.+?) 目录里 (\S+) 不出视频（输出模态：(.+?)）$", r"In the \1 catalog, \2 does not output video (output modalities: \3)"),
    (r"^(.+?) 目录里 (\S+) 不出语音（输出模态：(.+?)）$", r"In the \1 catalog, \2 does not output speech (output modalities: \3)"),
    (r"^(.+?) 目录里 (\S+) 不出文本（输出模态：(.+?)）$", r"In the \1 catalog, \2 does not output text (output modalities: \3)"),
    (r"^(.+?) 目录里 (\S+) 不(.+?)（输出模态：(.+?)）$", r"In the \1 catalog, \2 does not output \3 (output modalities: \4)"),
    (r"^(.+?) 目录里 (\S+) 不接受图片输入，首帧会被忽略$", r"In the \1 catalog, \2 does not accept image input; the first frame will be ignored"),
    (r"^(.+?) 目录里 (\S+) 不接受图片输入，角色锚图和参考镜会被忽略$",
     r"In the \1 catalog, \2 does not accept image input; character anchor images and reference shots will be ignored"),
    # ---- 本地模型（DESIGN §45）：本地来源的 cost_note + routers/local_models.py 的 400 / 425 / 探活原因
    (r"^本地生成，不花钱（(\d+) 次请求）$", r"Runs locally and costs nothing (\1 requests)"),
    (r"^只能扫本机地址（localhost / 127\.0\.0\.1 / ::1 / 私有网段），收到：(.+)$",
     r"Only local addresses can be scanned (localhost / 127.0.0.1 / ::1 / private ranges); got \1"),
    (r"^只能应用本机地址（localhost / 127\.0\.0\.1 / ::1 / 私有网段），收到：(.+)$",
     r"Only local addresses can be applied (localhost / 127.0.0.1 / ::1 / private ranges); got \1"),
    (r"^只能扫本机地址（localhost / 127\.0\.0\.1 / ::1 / 私有网段）$",
     r"Only local addresses can be scanned (localhost / 127.0.0.1 / ::1 / private ranges)"),
    (r"^手填地址要跟 ids 一起给：哪个服务在这个地址上$",
     r"A manual address must come with ids: say which service lives at that address"),
    (r"^不认识的本地服务：(.+)$", r"Unknown local service: \1"),
    (r"^(.+?) 不能配「(\w+)」这个角色（它只能配 (.+?)）$", '\\1 cannot fill the "\\2" role (it can only fill \\3)'),
    (r"^先选一份工作流$", r"Pick a workflow first"),
    (r"^ComfyUI 适配器还没装上（genkit/adapters/comfyui\.py），工作流清单暂时空着——装上它之后会自动出现，自己导出的工作流放进 <项目根>/local_workflows/ 也算$",
     r"The ComfyUI adapter is not installed yet (genkit/adapters/comfyui.py), so the workflow list is empty — it shows up once "
     r"the adapter is there, and workflows you exported yourself count too if they sit in <project root>/local_workflows/"),
    # ---- 推荐而不强制（DESIGN §45）：被点名时那一句「为什么是它」
    (r"^出图推荐：接口固定、装好就能用$", r"Recommended for images: a fixed API that works out of the box"),
    (r"^本地生视频 / Flux 走它$", r"Local video / Flux runs through this one"),
    (r"^本地文本推荐：接口就是 OpenAI 那套$", r"Recommended for text: it speaks the OpenAI API"),
    (r"^桌面客户端自带服务端，点一下就在跑$", r"The desktop app ships its own server; one click and it is running"),
    (r"^有显卡时吞吐最好的一档$", r"The best throughput when you have a GPU"),
    (r"^一个 gguf 文件就是一个模型$", r"One gguf file is one model"),
    (r"^本地语音走它（/v1/audio/speech）$", r"Local speech runs through this one (/v1/audio/speech)"),
    (r"^检查映射失败：(.+)$", r"Mapping check failed: \1"),
    (r"^读工作流失败：(.+)$", r"Failed to read the workflows: \1"),
    (r"^探到了，但取模型列表失败：(.+)$", r"Reachable, but fetching the model list failed: \1"),
    # ---- assist.py / settings.py / voices.py / genkit.settings
    (r"^会在当前 Python（(.+?)）里执行 pip install faster-whisper$", r"Will run pip install faster-whisper in the current Python (\1)"),
    # 打包版：借本机同版本 Python 装进外部 site 目录（目录 / 版本号 / 命令是变量）
    (r"^桌面版没有随包带 faster-whisper（体积原因）。点「一键安装」会用本机的 Python (\S+) 把它装进 (.+?)；没有 Python (\S+) 就先装一个，或自己执行：(.+?)，装完重启本程序。$",
     r'''The desktop build does not bundle faster-whisper (size). "Install" uses this machine's Python \1 to install it into \2; '''
     r"if Python \3 is not installed, install it first, or run it yourself: \4, then restart this app."),
    (r"^(.+?) 里的包是给 Python (\S+) 装的，本程序内置的是 Python (\S+)：删掉这个目录，用 Python (\S+) 重装$",
     r"The packages in \1 were installed for Python \2, but this build runs Python \3: delete that directory and reinstall with Python \4"),
    (r"^本机没找到 Python (\S+)（打包版借它来装）：先装一个 Python (\S+)，或自己执行：(.+?)，装完重启本程序。$",
     r"Python \1 was not found on this machine (the desktop build borrows it to install): install Python \2 first, or run it yourself: \3, then restart this app."),
    # pip 的输出尾巴是多行的（任务 error 里整段带着），要 (?s) 才命得中
    (r"(?s)^pip 退出码 (\d+)：(.+)$", r"pip exit code \1: \2"),
    (r"^模型只能是 (.+)$", r"Model must be one of \1"),
    (r"^(\S+) 已经在本机缓存里$", r"\1 is already in the local cache"),
    (r"^读取更新清单失败：(.+)$", r"Failed to read the update manifest: \1"),
    (r"^不认识的目录来源：(.+)$", r"Unknown catalog source: \1"),
    (r"^拉取 (.+?) 模型列表失败：(.+)$", r"Failed to fetch the \1 model list: \2"),
    (r"^未知角色：(.+)$", r"Unknown role: \1"),
    (r"^(\S+) 没有适配器 (\S+)$", r"Role \1 has no adapter \2"),
    (r"^(\w+): 没选适配器$", r"\1: no adapter selected"),
    (r"^(\w+): 未知适配器 (\S+)$", r"\1: unknown adapter \2"),
    (r"^读取音色目录失败（需要联网）：(.+)$", r"Failed to read the voice catalog (needs internet): \1"),
    (r"^没有这个角色：(.+)$", r"No such character: \1"),
    (r"^音色库里没有：(.+)$", r"Not in the voice library: \1"),
    (r"^没有这个音色：(.+)$", r"No such voice: \1"),
    (r"^已经有一个音色叫「(.+?)」$", r'A voice named "\1" already exists'),
    (r"^traits\.(\w+) 只能是 (.+)$", r"traits.\1 must be one of \2"),
    (r"^engine 只能是 (.+)$", r"engine must be one of \1"),
    # ---- voices.py / genkit.voices：音色克隆与参考音频（DESIGN §33）
    (r"^参考音频只接受 (.+?)，收到的是 (.+)$", r"Reference audio must be \1; got \2"),
    (r"^参考音频文件不存在：(.+)$", r"Reference audio file does not exist: \1"),
    (r"^参考音频超过 (\d+)MB：(.+)$", r"Reference audio exceeds \1MB: \2"),
    (r"^参考音频超过 (\d+)MB$", r"Reference audio exceeds \1MB"),
    (r"^克隆失败：(.+)$", r"Cloning failed: \1"),
    # ---- voices.py：音色对比角色（DESIGN §29）
    (r"^音色重复了：(.+)$", r"Duplicate voice: \1"),
    (r"^对比试听要选 (\d+) 到 (\d+) 个音色，现在是 (\d+) 个$", r"Pick \1 to \2 voices to compare; \3 selected"),
    (r"^合成超时（(\d+) 秒）$", r"Synthesis timed out (\1 s)"),
    # ---- config_validate.py
    (r"^(\S+) 字段必须是数组$", r"Field \1 must be an array"),
    (r"^(\S+) 字段必须是对象$", r"Field \1 must be an object"),
    (r"^(\S+) 里有重复 id：(.+)$", r"Duplicate ids in \1: \2"),
    (r"^profile (.+?)\.allow 引用了不存在的镜头 id：(.+)$", r"profile \1.allow references non-existent shot ids: \2"),
    (r"^profile (.+?)\.combos 引用了不存在于 combos_within_shot 的 id：(.+?)——是不是把 combos_sequence 的 id 填错地方了？跨镜次节拍请放进 recommended_sequences。$",
     r"profile \1.combos references ids that do not exist in combos_within_shot: \2 — did a combos_sequence id end up in the wrong place? "
     r"Cross-shot beats belong in recommended_sequences."),
    (r"^profile (.+?)\.recommended_sequences 引用了不存在的镜头组：(.+)$", r"profile \1.recommended_sequences references non-existent shot groups: \2"),
    (r"^profile (.+?) 必须是对象$", r"profile \1 must be an object"),
    (r"^frame_style_skeletons 和 scene_anchor_checklists 的 profile 集合对不上：差异 (.+?)——两边要覆盖同一组 profile，不然某个 profile 起草时会有一半骨架缺失。$",
     r"The profile sets of frame_style_skeletons and scene_anchor_checklists do not match: difference \1 — both must cover the same profiles, "
     r"otherwise a profile will draft with half its skeleton missing."),
    (r"^scene_anchor_checklists\[(.+?)\] 必须是数组$", r"scene_anchor_checklists[\1] must be an array"),
    (r"^scene_anchor_checklists\[(.+?)\] 检查项 id 重复：(.+)$", r"scene_anchor_checklists[\1] has duplicate check-item ids: \2"),
    # ---- 出图风格卡与图像方言（DESIGN §41）：lint 五条（整句 + 问题卡两半）、卡库接口与项目文件校验。
    #      排在 _LINT_RULES 前面——它的告警里有「提示词」「文字」这些短剧 lint 也用的词
    *_IMAGE_LINT_RULES,
    *_IMAGE_ROUTER_RULES,
    # ---- 单条视频（DESIGN §43）：lint 九条（整句 + 问题卡两半）、接口 / 任务文案、项目文件校验。
    #      排在 _LINT_RULES 前面——它的告警里有「时长」「重叠」「文字」这些短剧 lint 也用的词
    *_CLIP_LINT_RULES,
    # 「首帧没出来，视频没有提交：<人话>」：前缀 × 人话一起翻（tr 只匹配一次，不递归翻里层）
    *_composed([("首帧没出来，视频没有提交", "The first frame failed, so the video was never submitted")],
               _HUMANIZED_ALL + [(re.escape(zh), EXACT[zh]) for zh in _FAILURE_LABELS]),
    *_CLIP_ROUTER_RULES,
    # ---- AI 播客（DESIGN §50）：真人感告警（整句 + 问题卡两半）、接口 / 任务文案、项目文件校验（带前缀的整句 + 单独）。
    #      排在 _LINT_RULES 前面——它的告警里有「台词」「时长」「音色」这些短剧 lint 也用的词
    *_PODCAST_LINT_RULES,
    *_PODCAST_EN_RULES,
    *_PODCAST_ROUTER_RULES,
    # ---- 播客交付包：接口 400（整句带 ^$，跟播客其余规则放一起）
    *_PODCAST_EXPORT_RULES,
    # ---- 剧本工作台（docs/scriptlab-spec.md）：审稿告警（句头 block / 全篇）、借来的检查告警整句、起草提示与项目文件校验。
    #      排在播客与剧本层的块之后——借来的那些句子两半仍由它们翻；排在 _PB_SPEC_ERRORS 之前——不然通用的
    #      「X 要是对象（…）」会先把工作台自己的几条吃掉
    *_SCRIPT_REVIEW_RULES,
    *_SCRIPT_LINT_RULES,
    *_SCRIPT_CRAFT_RULES,
    *_composed([("改动没通过校验，没有写盘", "The change failed validation and was not saved"),
                ("模型返回的稿子没通过校验", "The script returned by the model failed validation")],
               _SCRIPT_SPEC_ERRORS),
    *[(f"^{pat}$", rep) for pat, rep in _SCRIPT_SPEC_ERRORS],
    # ---- 工作台的投放 / 导入（§7）与接口层（§8）：带人话里层的几条先 composed（里外一起翻），
    #      再是自己的整句；裸前缀兜底在 _SCRIPTLAB_RULES 末尾
    # 「示例没通过结构校验（…）：<人话>」不在这里 composed：上面（图片集那一段）已经有一条只翻前缀的裸规则，
    # 先命中就把里层的中文留下了。示例文件坏掉是开发期的事，不值得为它去动别人的规则顺序
    *_composed([("导进来的台词没通过校验", "The imported lines failed validation")], _SCRIPT_SPEC_ERRORS),
    *_composed([("目标播客没接受这些台词", "The destination podcast rejected these lines"),
                ("目标播客读不出来", "The destination podcast could not be read")], _PODCAST_SPEC_ERRORS),
    # 「读不出来」走的是 PC.load：里层除了归一化那些，还有读文件那一层的三句
    *_composed([("目标播客读不出来", "The destination podcast could not be read")], _PODCAST_LOAD_ERRORS),
    *_composed([("目标剧本没接受这些台词", "The destination screenplay rejected these lines")], _SCREENPLAY_SPEC_ERRORS),
    *_SCRIPTLAB_RULES,
    # ---- 剧本工艺清单的静态判据（docs/script-craft-gap.md）：lint 五条 + 剧本层两条（整句 + 问题卡两半）
    *_CRAFT_LINT_RULES,
    # ---- 剧本工艺清单的低优判据：单镜时长上限 + 关系场景（整句 + 问题卡两半）
    *_CRAFT_LINT_LOW_RULES,
    # ---- 配乐投放到播客（docs/music-spec.md §3）：带前缀的整句要放在裸的播客规则之前，不然裸规则从句子中间命中
    *_composed([("目标播客读不出来", "The destination podcast could not be read"),
                ("目标播客没接受这条配乐", "The destination podcast rejected this track")], _PODCAST_SPEC_ERRORS),
    *_MUSIC_PODCAST_RULES,
    *_composed([("改动没通过校验，没有写盘", "The change failed validation and was not saved"),
                ("示例没通过结构校验（这是示例文件本身的问题）", "The example failed structural validation (a problem in the example file itself)"),
                ("模型返回的稿子没通过校验", "The script returned by the model failed validation")],
               _PODCAST_SPEC_ERRORS),
    *[(f"^{pat}$", rep) for pat, rep in _PODCAST_SPEC_ERRORS],
    *_composed([("改动没通过校验，没有写盘", "The change failed validation and was not saved"),
                ("示例没通过结构校验（这是示例文件本身的问题）", "The example failed structural validation (a problem in the example file itself)")],
               _CLIP_SPEC_ERRORS),
    *[(f"^{pat}$", rep) for pat, rep in _CLIP_SPEC_ERRORS],
    # ---- 剧本层的逐场编辑器（DESIGN §32）：lint 六条 + PUT 落盘前的校验（整句 + 单独）。排在 _LINT_RULES 前面——
    #      它的告警里有「台词」「时间」这些镜头 lint 也在用的词
    *_SCREENPLAY_LINT_RULES,
    *_composed([("剧本没通过校验，没有保存", "The screenplay failed validation and was not saved")], _SCREENPLAY_SPEC_ERRORS),
    *[(f"^{pat}$", rep) for pat, rep in _SCREENPLAY_SPEC_ERRORS],
    # ---- lint.py / continuity.py 告警（整句 + 问题卡两半）；episodes / project / scripts / export / post 的形状校验
    *_PAIRING_RULES,
    *_LINT_RULES,
    *_ROUTER_RULES,
    # ---- 绘本（DESIGN §39）：lint 三态、接口 / 任务文案、项目文件校验（带「改动没通过校验」等前缀的整句 + 单独）
    *_PB_LINT_RULES,
    *_PB_ROUTER_RULES,
    *_composed([("改动没通过校验，没有写盘", "The change failed validation and was not saved"),
                ("起草结果没通过结构校验", "The draft failed structural validation"),
                ("示例没通过结构校验（这是示例文件本身的问题）", "The example failed structural validation (a problem in the example file itself)")],
               _PB_SPEC_ERRORS),
    *[(f"^{pat}$", rep) for pat, rep in _PB_SPEC_ERRORS],
    # ---- 路由前缀 × 人话 / 后期块校验：整句。必须排在单独的人话规则前面——人话规则里的 (\S+) 会把
    #      「生成失败：S1」整个吞成镜号（全角冒号不是空白）
    *_composed(_PREFIXES, _HUMANIZED_ALL),
    *_composed([("改动没通过校验，没有写盘", "The change failed validation and was not saved")], _POST_BLOCK_ERRORS),
    # ---- humanize.py：人话单独出现（「人话（原文）」形状下 tr 只拿前半段来匹配）
    *[(f"^{pat}$", rep) for pat, rep in _HUMANIZED],
    # 「人话（原文）」括号里的原文：runtime 的守卫文案一般是英文、原样留下；大首帧代理图这条的里层是中文，得单独翻
    (r"^failed to upload dependency for (\w+)/(\S+): 上传用的代理图压到最低画质仍有 (\d+) 字节，超过 (\d+) 字节的上传上限：(.+?)——检查这张图的尺寸是否异常$",
     r"failed to upload dependency for \1/\2: even at the lowest quality the upload proxy is still \3 bytes, "
     r"over the \4-byte upload limit: \5 — check whether this image has an unusual size"),
    # ---- 发第一个 tag 前的适配层 / 设置修复（ADP-01 / 05 / 08 / 11）：「测试连接」真探针的人话（带异常类名前缀是
    #      settings 路由的形状，裸的是适配器原话）、设置文件损坏 / 读不了、video_params.count 的 lint 提醒。
    #      放在路由前缀兜底之前，免得带前缀的整句被裸前缀规则先吃掉
    (r"^(\w+): 连不上 (.+?)：(.+?)——检查接口地址、网络或代理$", r"\1: cannot reach \2: \3 — check the endpoint address, network or proxy"),
    (r"^连不上 (.+?)：(.+?)——检查接口地址、网络或代理$", r"Cannot reach \1: \2 — check the endpoint address, network or proxy"),
    (r"^(\w+): (.+?) 拒绝了这个密钥（(\d+)）：(.+?)——检查 api_key$", r"\1: \2 rejected this key (\3): \4 — check the api_key"),
    (r"^(.+?) 拒绝了这个密钥（(\d+)）：(.+?)——检查 api_key$", r"\1 rejected this key (\2): \3 — check the api_key"),
    (r"^(\w+): (.+?)/models 不存在（404）——接口地址是不是少了 /v1 或写错了$",
     r"\1: \2/models does not exist (404) — is the base URL missing /v1, or misspelled?"),
    (r"^(.+?)/models 不存在（404）——接口地址是不是少了 /v1 或写错了$",
     r"\1/models does not exist (404) — is the base URL missing /v1, or misspelled?"),
    (r"^(\w+): 连不上 Edge 在线语音服务：(.+?)——检查网络或代理$", r"\1: cannot reach the Edge online speech service: \2 — check the network or proxy"),
    (r"^连不上 Edge 在线语音服务：(.+?)——检查网络或代理$", r"Cannot reach the Edge online speech service: \1 — check the network or proxy"),
    (r"^设置文件损坏：(.+?)：(.+?)。保存一次会自动备份坏文件并重建$",
     r"Settings file is corrupt: \1: \2. Saving once will back up the broken file and rebuild it"),
    (r"^设置文件读不了：(.+?)：(.+?)——检查文件权限，或是不是被别的程序占着$",
     r"Cannot read the settings file: \1: \2 — check its permissions, or whether another program has it open"),
    (r"^video_params\.count 写的是 (.+?)：流水线每镜只保留一条结果，提交时一律只要 1 条，这一项不起作用，可以删掉$",
     r"video_params.count is \1: the pipeline keeps one result per shot and always requests 1, so this setting has no effect and can be removed"),
    # 「提交后没拿到结果」的原文：英文外壳里套着服务商 / 适配器的原因（没配模型时是中文）——外壳照抄，
    # 原因那一组由 tr 再翻一遍，生成 409 的括号里就不再夹中文
    (r"(?s)^submission outcome for (\S+) is unknown; refusing retry: (.+)$", r"submission outcome for \1 is unknown; refusing retry: \2"),
    # ---- 静态扫描补出来的 raise 文案（模板；有几条 (\S+) 开头的泛化形状，所以排在各模块自己的规则之后）
    *_RAISE_RULES,
    # ---- 路由前缀兜底（里层没规则时至少把前缀翻掉）
    *[(f"^{re.escape(zh)}：(.+)$", f"{en}: \\1") for zh, en in _PREFIXES],
    # 兜底：发第一个 tag 前新增的几条文案，放在所有具体规则之后
    *_RELEASE_FIX_RULES,
    # 发布前安全自查（2026-09-23）新增的文案
    *_SECURITY_REVIEW_RULES,
    # 合成路径 ffmpeg 失败的一行原因
    *_RENDER_RULES,
    # ---- 插件机制（genkit.plugins）：来源没装上 / 插件加载失败 / 在飞任务的来源没加载 / 设置保存被拒。整句全部 ^…$；
    #      角色名按五个角色各展开一条，最后是给静态扫描样本兜底的泛化形状（角色名当用户内容加引号）
    *[rule for zh, en in (("剧本文本", "script text"), ("生图", "image"), ("语音", "voice"), ("视频", "video"), ("配乐", "music"))
      for rule in (
          (rf"^「{zh}」的来源 (\S+) 现在用不了：提供它的插件没能加载（(.+)）——修好插件后重启程序，"
           rf"或去「模型设置」给「{zh}」换一个来源$",
           rf"The {en} source \1 is unavailable: the plugin that provides it failed to load (\2) — "
           rf"fix the plugin and restart the program, or pick another {en} source in Model settings"),
          (rf"^「{zh}」的来源 (\S+) 现在用不了：它不是内置来源，提供它的插件没有加载（(.+)）——去掉这个开关后重启程序，"
           rf"或去「模型设置」给「{zh}」换一个来源$",
           rf"The {en} source \1 is unavailable: it is not built in and the plugin that provides it is not loaded (\2) — "
           rf"remove that switch and restart the program, or pick another {en} source in Model settings"),
          (rf"^「{zh}」的来源 (\S+) 现在用不了：它不是内置来源，提供它的插件没有加载——把插件放进 (.+?) 后重启程序，"
           rf"或去「模型设置」给「{zh}」换一个来源$",
           rf"The {en} source \1 is unavailable: it is not built in and the plugin that provides it is not loaded — "
           rf"put the plugin into \2 and restart the program, or pick another {en} source in Model settings"),
          (rf"^「{zh}」目前没有可选的来源：内置来源不覆盖这一档，要装一个提供它的插件，装好后在「模型设置」里选上它$",
           rf"There is no {en} source to choose yet: the built-in sources do not cover this role; install a plugin that provides one, then pick it in Model settings"),
          (rf"^「{zh}」没有来源 (.+?)：它不是内置来源，提供它的插件也没加载——没有保存$",
           rf"The {en} role has no source \1: it is not built in and the plugin that provides it is not loaded — nothing was saved"),
      )],
    (r"^「(.+?)」没有来源 (.+?)：它不是内置来源，提供它的插件也没加载——没有保存$",
     r'Role "\1" has no source \2: it is not built in and the plugin that provides it is not loaded — nothing was saved'),
    (r"^(\w+): 来源 (\S+) 没有装上$", r"\1: source \2 is not installed"),
    (r"^任务 (\S+) 是一个没加载的来源提交的（前缀 (\S+)）（(.+)），查不了进度——装好提供它的插件并重启后再刷新；任务留在账本里，不会被当成失败$",
     r"Task \1 was submitted by a source that is not loaded (prefix \2; \3), so its progress cannot be checked — install the "
     r"plugin that provides it, restart, then refresh; the task stays in the ledger and is not counted as failed"),
    (r"^任务 (\S+) 是一个没加载的来源提交的（前缀 (\S+)），查不了进度——装好提供它的插件并重启后再刷新；任务留在账本里，不会被当成失败$",
     r"Task \1 was submitted by a source that is not loaded (prefix \2), so its progress cannot be checked — install the "
     r"plugin that provides it, restart, then refresh; the task stays in the ledger and is not counted as failed"),
    (r"^插件加载已关闭：STORYSTUDIO_PLUGINS=off 或桌面启动器的 --no-plugins$",
     r"plugin loading is turned off: STORYSTUDIO_PLUGINS=off or the desktop launcher's --no-plugins"),
    # 「查询进度失败」括号里的原文：英文外壳照抄，里层（上面那句中文）由 tr 再翻一遍
    (r"(?s)^could not poll (\S+): (.+)$", r"could not poll \1: \2"),
    (r"(?s)^插件 (\S+) 加载失败：(.+)$", r"Plugin \1 failed to load: \2"),
    (r"^插件 (\S+) 已经从 (.+) 加载过，这一份没有加载$", r"Plugin \1 was already loaded from \2; this copy was not loaded"),
    (r"^插件 (\S+) 的目录里没有 __init__\.py：(.+)$", r"Plugin \1 has no __init__.py in its folder: \2"),
    (r"^插件 (\S+) 的 vendor 目录是给 Python (\S+) 装的，本程序是 (\S+)——用同一版本的 Python 重装$",
     r"Plugin \1's vendor folder was installed for Python \2, but this program runs \3 — reinstall it with the same Python version"),
    (r"^插件 (\S+) 的 __init__\.py 导入不了$", r"Plugin \1's __init__.py cannot be imported"),
    (r"^插件 (\S+) 没有写 STORYSTUDIO_API（插件接口版本）$", r"Plugin \1 does not declare STORYSTUDIO_API (the plugin API version)"),
    (r"^插件 (\S+) 需要插件接口 v(\S+)，本程序提供 v(\S+)——更新插件或程序$",
     r"Plugin \1 needs plugin API v\2, but this program provides v\3 — update the plugin or the program"),
    (r"^插件 (\S+) 没有 register\(api\) 函数$", r"Plugin \1 has no register(api) function"),
    (r"^插件 (\S+) 想登记的来源 (\S+) 已经存在（内置），整个插件没有加载$",
     r"Plugin \1 tried to register source \2, which already exists (built in); the whole plugin was not loaded"),
    (r"^插件 (\S+) 想登记的来源 (\S+) 已经存在（插件 (\S+)），整个插件没有加载$",
     r"Plugin \1 tried to register source \2, which already exists (plugin \3); the whole plugin was not loaded"),
    (r"^插件 (\S+) 想登记的来源 (\S+) 已经存在（(.+?)），整个插件没有加载$",
     r"Plugin \1 tried to register source \2, which already exists (\3); the whole plugin was not loaded"),
]
