[English](../en/configuration.md) | 中文

# 配置参考

这份文档列出 Content Cook 所有可以配置的地方：两个目录、设置文件、五个模型角色与内置来源、统一网关、本机模型、
环境变量、插件目录、数据目录结构和日志。
安装步骤见 [安装与部署](installation.md)，常见问题见 [FAQ](faq.md)。

> 命名说明：产品名是「Content Cook」，但 Python 包、命令（`storystudio` / `storyvideo`）、设置目录（`~/.storystudio`）
> 和环境变量前缀（`STORYSTUDIO_`）是技术标识，沿用 `storystudio` 这个名字，不随产品名变。
> 下文的 `~` 指用户主目录（Windows 上是 `%USERPROFILE%`）。

## 1. 两个目录

程序把东西分成两处放：

| 目录 | 放什么 | 默认位置 | 怎么改 |
|---|---|---|---|
| **数据目录**（data root） | 项目文件、生成的图片 / 视频 / 音频、短剧账本、回收站、后台任务记录 | 见下表 | `--project-root` 或 `STORYSTUDIO_PROJECT_ROOT` |
| **设置目录**（home） | 模型设置与密钥、缓存、日志、音色库、模型目录缓存、插件、打包版的可选组件 | `~/.storystudio` | `STORYSTUDIO_HOME` |

数据目录按下面的优先级决定：

| 启动方式 | 优先级（从高到低） |
|---|---|
| 桌面启动器 `storystudio`（含打包版） | `--project-root` → `STORYSTUDIO_PROJECT_ROOT` → 打包版为 `~/StoryStudio`，源码运行为当前目录 |
| 命令行 `storyvideo`、直接起服务 `webui.app:app` | `--project-root`（仅 `storyvideo`）→ `STORYSTUDIO_PROJECT_ROOT` → 代码根目录（源码安装就是仓库根） |

启动器定下数据目录后会把它写进 `STORYSTUDIO_PROJECT_ROOT`、切换到这个目录，并建好 `content/story_video/` 和 `output/`。
界面用的目录和命令行默认的不一样时（比如桌面版用 `~/StoryStudio`），推荐先 `export STORYSTUDIO_PROJECT_ROOT=<数据目录>`
再跑 `storyvideo`。也可以每条命令加 `--project-root <数据目录>`，但它只决定项目和账本在哪；
ComfyUI 的 `local_workflows/`、起草兜底用的 `.env` 等仍按 `STORYSTUDIO_PROJECT_ROOT`（没设就是代码根目录）去找。

## 2. 设置文件 settings.json

### 2.1 位置

1. 环境变量 `STORYSTUDIO_SETTINGS` 指向的文件；
2. 否则是 `<设置目录>/settings.json`，也就是默认的 `~/.storystudio/settings.json`。

平时在界面左栏「模型设置」里改就行，不需要手动编辑这个文件。

### 2.2 结构

```json
{
  "roles": {
    "text":  {"adapter": "openai_chat", "base_url": "https://openrouter.ai/api/v1",
              "api_key": "<你的密钥>", "model": "<模型 id>", "voice": "", "extra": {}},
    "image": {"adapter": "", "base_url": "", "api_key": "", "model": "", "voice": "", "extra": {}},
    "voice": {"adapter": "edge_tts", "base_url": "", "api_key": "", "model": "zh-CN-YunxiNeural", "voice": "", "extra": {}},
    "video": {"adapter": "", "base_url": "", "api_key": "", "model": "", "voice": "", "extra": {}},
    "music": {"adapter": "", "base_url": "", "api_key": "", "model": "", "voice": "", "extra": {}}
  },
  "output_root": "",
  "cache_dir": "",
  "update_url": "",
  "gateway": {"base_url": "", "api_key": ""},
  "local_hints_dismissed": []
}
```

顶层字段：

| 字段 | 含义 |
|---|---|
| `roles` | 五个角色各自的来源配置，见 [§3](#3-模型角色与内置来源) |
| `cache_dir` | 适配器缓存目录；留空 = `<设置目录>/cache` |
| `update_url` | 版本清单地址，内容是 `{"version": "...", "url": "...", "notes": "..."}` 形状的 JSON。留空就从不检查更新；填了也只在你点「检查更新」时查一次，只告诉你有没有新版和去哪下载，不会自动下载或安装 |
| `gateway` | 统一网关的地址和密钥，见 [§4](#4-统一网关) |
| `local_hints_dismissed` | 你关掉过哪些角色的「本机可以跑」提示（角色名，或 `"all"`） |
| `output_root` | 预留字段：会被读写和保存，但目前没有任何代码使用它。产物位置由各项目决定（短剧看项目文件里的 `output_root`，默认 `output/story_video/<ID>`），都限制在数据目录之内 |

界面上能改的是角色的来源和表单字段、网关、更新地址和本机提示；`cache_dir` 和各角色的 `extra` 只能手动编辑文件（改完重启程序）。

每个角色的字段：

| 字段 | 含义 |
|---|---|
| `adapter` | 来源 id（见 §3 的表）；空 = 没配置 |
| `base_url` | 接口地址；留空用来源的默认地址 |
| `api_key` | 密钥；有效地址是本机 / 局域网地址时可以留空 |
| `model` | 模型 id；留空用来源的默认模型（有默认值的才行） |
| `voice` | 音色（只有语音来源用） |
| `extra` | 来源自己的额外选项，比如 ComfyUI 的工作流和种子、SD WebUI 的采样参数（见 §3.2） |

### 2.3 写盘与损坏处理

- **原子写入**：先写同目录下的临时文件，再整体替换，读的一方永远看到完整的旧文件或新文件。
- **加锁**：进程内有读写锁，进程之间用旁边的 `settings.json.lock` 排队；两个窗口同时保存，谁的改动都不丢。
  保存时如果发现文件在这期间被别处改过，只把这次真正改到的字段合并上去。
- **损坏**：文件不是合法 JSON 时，设置页照常打开并提示；下一次保存会先把坏文件改名为
  `settings.json.broken-<时间戳>` 留底，再写新文件。新文件从空配置开始，只含这次保存的内容，
  其他角色要重新填（可以从 `.broken-*` 备份里抄）。
- **权限**：每次保存后收紧权限——macOS / Linux 上 `chmod 600`，Windows 上用 `icacls` 去掉继承、只授当前用户。
  这是尽力而为，失败不会报错。
- 读取时接受带 BOM 的 UTF-8（记事本等编辑器保存的文件常带 BOM）。

### 2.4 密钥怎么处理

- 密钥只存在 `settings.json` 里，**是明文**，靠文件权限保护；不会写进项目文件、账本或日志。
- 接口返回给界面的密钥一律打码：8 位及以下全遮；9–15 位只露首尾各 2 位；16 位及以上露首尾各 4 位。
- 保存时：传回打码值或空串 = 保留原密钥；传 `null` = 清空；其他文字 = 换成新密钥。
- **换了来源（`adapter` 变了），旧密钥自动清掉**；同一个来源把地址改到别的主机 / 端口 / 协议、又没填新密钥时，旧密钥也清掉。
  这样一个服务商的密钥不会被发到另一台机器上。只改路径（比如加减 `/v1`）不算换地方。网关的密钥同样处理。
- 用环境变量覆盖的值（§6）只在运行时生效，永远不会写回文件。

## 3. 模型角色与内置来源

### 3.1 五个角色

| 角色 | 设置页卡片 | 用在哪 |
|---|---|---|
| `text` | 剧本文本模型 | AI 起草、改写、各类文本审稿（短剧、播客、剧本工作台） |
| `image` | 生图模型（首帧 / 定妆） | 首帧、定妆图、图片集、绘本插画、海报 |
| `voice` | 语音模型（旁白） | 旁白、播客逐句配音、有声书朗读 |
| `video` | 视频模型 | 短剧每一镜的视频、单条视频 |
| `music` | 配乐模型 | 配乐模块的「生成」 |

每个角色选一个来源（`adapter`），空着的字段用来源的默认值。
新装的程序五个角色都是空的；需要某个角色的功能在发起任务之前就会拦下并告诉你去哪配。

只有一个 OpenRouter 密钥时的最简配置：文本选 `openai_chat` 并把地址填 `https://openrouter.ai/api/v1`，
生图、视频、语音都选 OpenRouter 那一项，填同一个密钥。
文本角色还要填模型 id，语音角色还要填模型和音色（OpenRouter 语音没有默认值）；生图和视频留空用默认模型即可。

### 3.2 内置来源一览

表中「字段」是设置页表单里出现的栏目，「默认值」是对应栏目留空时用的值。

**文本（text）**

| 来源 id | 说明 | 字段 | 默认值 |
|---|---|---|---|
| `openai_chat` | 任何 OpenAI 兼容的对话接口（官方 openai SDK，`/chat/completions`）。OpenAI、OpenRouter、DeepSeek、Moonshot、通义、豆包等兼容接口，以及本机的 Ollama / LM Studio / vLLM / llama.cpp 都走这一条 | base_url, api_key, model | base_url `https://api.openai.com/v1`；model 没有默认值，必须填 |

**生图（image）**

| 来源 id | 说明 | 字段 | 默认值 |
|---|---|---|---|
| `openrouter_images` | OpenRouter 生图（官方 SDK），支持参考图 | api_key, model | 地址 `https://openrouter.ai/api/v1`；model `bytedance-seed/seedream-4.5` |
| `openai_images` | OpenAI 兼容图片接口：`/v1/images/generations`，带参考图时走 `/v1/images/edits` | base_url, api_key, model | `https://api.openai.com/v1`；`gpt-image-1` |
| `ark_images` | 火山方舟 Seedream（官方 volcengine SDK），参考图以 base64 随请求发送 | base_url, api_key, model | `https://ark.cn-beijing.volces.com/api/v3`；`doubao-seedream-4-0-250828` |
| `sdwebui` | 本机 Stable Diffusion WebUI / Forge（`/sdapi/v1/txt2img`）。WebUI 要带 `--api` 启动；model 填 checkpoint 文件名，和当前加载的不一致时先切换；参考图走 img2img，一次一张 | base_url, model | `http://127.0.0.1:7860` |
| `comfyui` | 本机 ComfyUI，按一份「API 格式」工作流出图；内置 `txt2img_sdxl` / `img2img_sdxl` | base_url, model | `http://127.0.0.1:8188`；`sd_xl_base_1.0.safetensors` |

**语音（voice）**

| 来源 id | 说明 | 字段 | 默认值 |
|---|---|---|---|
| `edge_tts` | Edge TTS（edge-tts 库，微软的在线朗读服务，免费）。model 填音色名；语速、音调由项目里的 `voice_over.edge` 决定 | model | `zh-CN-YunxiNeural` |
| `openai_speech` | OpenAI 兼容语音接口（`/v1/audio/speech`） | base_url, api_key, model, voice | `https://api.openai.com/v1`；`gpt-4o-mini-tts`；`alloy` |
| `openrouter_tts` | OpenRouter 语音（官方 SDK） | api_key, model, voice | 地址 `https://openrouter.ai/api/v1`；model、voice 没有默认值 |
| `minimax_speech` | MiniMax 语音（`/v1/t2a_v2`），可以用音色库里克隆出来的音色。按公开文档写成，还没用真实账号调用过 | base_url, api_key, model, voice | `https://api.minimax.chat/v1`；`speech-02-hd`；`female-shaonv` |

旁白、朗读、播客配音没有指定音色时：「模型设置」里配了非 Edge 的语音来源就用它，否则用 Edge TTS。

**视频（video）**

| 来源 id | 说明 | 字段 | 默认值 |
|---|---|---|---|
| `openrouter_video` | OpenRouter 视频（官方 SDK），支持首帧，异步轮询 | api_key, model | 地址 `https://openrouter.ai/api/v1`；model `minimax/hailuo-3` |
| `ark_video` | 火山方舟 Seedance（官方 volcengine SDK），首帧以 base64 发送，异步轮询 | base_url, api_key, model | `https://ark.cn-beijing.volces.com/api/v3`；`doubao-seedance-1-0-pro-250528` |
| `openai_video` | OpenAI 兼容视频接口（`/v1/videos`，Sora 的请求形状），给 LiteLLM 这类把别家视频模型按这个形状转发的网关用；首帧走 `input_reference` | base_url, api_key, model | `https://api.openai.com/v1`；`sora-2` |
| `minimax_video` | MiniMax 视频（`/v1/video_generation`），首帧以 data URL 发送，异步轮询 | base_url, api_key, model | `https://api.minimax.chat/v1`；`MiniMax-Hailuo-02` |
| `comfyui` | 本机 ComfyUI 视频工作流。内置 `txt2video_wan` 是 Wan 2.1 文生视频，需要本机装好 Wan 的模型文件和 ComfyUI-VideoHelperSuite 节点；镜头时长按工作流帧率折算成 4n+1 帧 | base_url, model | `http://127.0.0.1:8188`；`wan2.1_t2v_1.3B_fp16.safetensors` |

**配乐（music）**：没有内置来源。音乐生成没有通用的接口形状，要用配乐模块的「生成」，需要装一个提供 `music` 角色的[插件](../plugins.md)。
短剧、播客、绘本各自上传本地音频做配乐不受影响。

**ComfyUI 的额外选项**（写在该角色的 `extra` 里，只能手动编辑 `settings.json`）：

| 键 | 作用 |
|---|---|
| `workflow` | 用哪一份工作流。不填就用该任务类型的第一份内置工作流（生图带参考图时，用第一份能收输入图的内置工作流 `img2img_sdxl`）。设置页的「ComfyUI 工作流」下拉框只用来「检查映射」，不会保存选择；要换工作流，在 `settings.json` 里给该角色写 `"extra": {"workflow": "<名字>"}` |
| `seed` | 固定种子（整数）；不填每次随机，随机值记在运行记录里 |
| `steps` / `cfg` / `denoise` | `steps` / `cfg` 覆盖工作流里的对应参数；`denoise` 只对生图工作流有效 |
| `fps` | 视频工作流的帧率（用于把时长折算成帧数）；不填读工作流里的值 |

**SD WebUI 的额外选项**（`extra`，只能手动编辑）：`steps`（默认 30）、`cfg_scale`（7.0）、`sampler_name`（`DPM++ 2M Karras`）、
`seed`（-1 = 随机）、`denoising_strength`（只在带参考图走 img2img 时用，0.6）、`width` / `height`（对齐到 8 的倍数）。

**MiniMax 语音**：`extra.group_id` 有值时，会作为 `GroupId` 查询参数带在请求地址上。

自己导出的工作流放在 `<数据目录>/local_workflows/`，每份配一份同名的 `.map.json`，说明哪个节点的哪个字段是提示词、宽高、种子、输出等。
细节见 [local-models-spec.md](../local-models-spec.md)。

### 3.3 项目里的模型别名

项目（spec）里的模型写 `"@image"` / `"@video"` 表示「用模型设置里这个角色配的模型」，示例项目和 AI 起草默认这样写；
写具体的模型 id 则以项目里写的为准。

### 3.4 参数检查与模型目录

- 开始制作之前，程序按一张静态的能力表（来源 × 模型前缀）检查画幅、尺寸、时长等参数，
  分辨率会自动对齐到模型支持的最接近档位。表里没有的模型只提示「越界要到提交时才会报错」。
- 设置页可以手动刷新模型目录（OpenRouter 的公开列表，或网关的 `/models`），结果缓存在 `<设置目录>/model_catalog.json`，只作参考。

### 3.5 联调状态

多数直连来源只用假客户端（fake client）测试过，按官方文档和 SDK 签名写成，还没用真实账号跑过；
本机的 SD WebUI 和 ComfyUI 适配器也只有假 HTTP 测试。Edge TTS 真实合成过。
逐项状态见 [BACKLOG.md](../../BACKLOG.md) 的「现状盘点」和 [CHANGELOG.md](../../CHANGELOG.md) 的「已验证 / 未验证」。

## 4. 统一网关

用 LiteLLM、new-api、one-api 这类 OpenAI 兼容网关时，不用逐个角色填：

1. 在「模型设置」顶部的「统一网关（可选）」填一次网关地址和网关的密钥；
2. 点测试连接（`POST /api/settings/gateway/test`）；
3. 为每个角色选好模型，一键应用（`POST /api/settings/gateway/apply`）。

应用后四个角色分别变成：

| 角色 | 来源 |
|---|---|
| text | `openai_chat` |
| image | `openai_images` |
| voice | `openai_speech` |
| video | `openai_video` |

网关只是「批量填表」：应用时把网关的地址和密钥抄进各角色，运行时仍然只读各角色的配置。
配乐不在网关范围内（没有 OpenAI 兼容的音乐接口），要单独配。

## 5. 本机模型

有显卡的话可以让部分或全部角色跑在自己电脑上。这是可选的，云端来源不受影响。

「模型设置」里的「扫描本机」（`GET /api/system/local-scan`）会探测这些常见服务：

| 服务 | 角色 | 默认地址 |
|---|---|---|
| Ollama | text | `http://127.0.0.1:11434/v1` |
| LM Studio | text | `http://127.0.0.1:1234/v1` |
| vLLM | text | `http://127.0.0.1:8000/v1` |
| llama.cpp server | text | `http://127.0.0.1:8080/v1` |
| ComfyUI | image、video | `http://127.0.0.1:8188` |
| Stable Diffusion WebUI / Forge | image | `http://127.0.0.1:7860` |
| 本机 OpenAI 兼容语音（如 Kokoro-FastAPI） | voice | `http://127.0.0.1:8880/v1` |

- 「应用到角色」会填好来源、地址和模型，并清掉这个角色原来的密钥。
- **只连本机或局域网地址**：`localhost`、`127.0.0.0/8`、`::1`、`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、
  `169.254.0.0/16`、`fc00::/7`、`fe80::/10`。`127.0.0.1.example.com` 这类「长得像本机」的域名一律不算。
  扫描只探上面几个固定地址，不是网段扫描器。
- 地址属于上面这些网段时，密钥可以留空；费用提示改成「本地生成，不花钱（N 次请求）」。
- 「本机可以跑」的推荐提示可以关掉，关掉的记录存在 `local_hints_dismissed` 里。
- 这些本机适配器还没在真实硬件上验证过，见 §3.5。

## 6. 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `STORYSTUDIO_PROJECT_ROOT` | 数据目录，优先级见 §1 | 启动器：打包版 `~/StoryStudio`、源码运行为当前目录；命令行和直接起服务：代码根目录 |
| `STORYSTUDIO_HOME` | 设置目录：设置、缓存、日志、音色库、模型目录、插件、可选组件都在这里 | `~/.storystudio` |
| `STORYSTUDIO_SETTINGS` | 设置文件的完整路径 | `<设置目录>/settings.json` |
| `STORYSTUDIO_<ROLE>_<FIELD>` | 运行时覆盖某个角色的某个字段，给无头部署和 CI 用。`ROLE` 是 `TEXT` / `IMAGE` / `VOICE` / `VIDEO` / `MUSIC`，`FIELD` 是 `ADAPTER` / `BASE_URL` / `API_KEY` / `MODEL` / `VOICE`。只认非空值，不写回文件 | 不设 |
| `STORYSTUDIO_PLUGIN_PATH` | 额外的插件目录，多个用系统路径分隔符（Windows `;`，其他系统 `:`） | 不设 |
| `STORYSTUDIO_PLUGINS` | 设为 `off` / `0` / `false` / `no` 时不加载任何插件；启动器的 `--no-plugins` 就是设它 | 加载 |
| `STORYSTUDIO_EXTRA_SITE` | 只对打包版有效：可选组件（faster-whisper 等）的安装目录，启动时接进 `sys.path`；给别的 Python 版本装的会被忽略 | `<设置目录>/site-packages` |
| `STORYSTUDIO_TOKEN` | 内部使用：启动器把本次的访问令牌放在这里，服务据此检查令牌。不要手动设置 | 由启动器设置 |
| `SCRIPTGEN_LLM_BASE_URL` / `SCRIPTGEN_LLM_API_KEY` / `SCRIPTGEN_LLM_MODEL` | AI 起草的兜底：没配文本角色时，改用这个 OpenAI 兼容代理 | 不设 |
| `SCRIPTGEN_BACKEND` | 强制起草用哪条路：`settings`（模型设置里的文本角色）或 `openai_proxy`（上面的代理） | 自动：先 `settings`，再代理，都没有就报错 |
| `SCRIPTGEN_PROXY_TIMEOUT_SECONDS` | 起草时文本请求的超时秒数（两条路共用） | `180` |
| `HF_HUB_CACHE` | faster-whisper 模型的缓存目录 | `~/.cache/huggingface/hub` |

说明：

- `SCRIPTGEN_*` 先读环境变量，再读 `<数据目录>/.env` 文件。别的变量**只**从真正的环境变量读，写在 `.env` 里不生效。
  仓库里的 [.env.example](../../.env.example) 是示例；其中的 `STORYSTUDIO_*` 行也只有设成真正的环境变量才生效，放在 `.env` 里不起作用。
- 代码里还读 `SCRIPTGEN_MAX_BUDGET_USD`，但目前没有任何地方按它拦截请求，设了也不起作用。
- Hugging Face 的缓存位置请用 `HF_HUB_CACHE` 指定。已知问题：装了 faster-whisper 之后，如果只设 `HF_HOME`、不设 `HF_HUB_CACHE`，
  设置页「台词识别闸（可选）」卡片里的状态和按钮加载不出来，从设置页下载模型也会失败；命令行 `storyvideo asr-qc` 不受影响。
- 打包脚本另外用到：`BUNDLE_DENYLIST`（包内容自检的私有词表）、`CODESIGN_IDENTITY` 和 `NOTARY_PROFILE`（macOS 签名与公证）。
  见 [desktop/README.md](../../desktop/README.md)。

无头部署的例子（bash）：

```bash
export STORYSTUDIO_PROJECT_ROOT="$HOME/content-cook-data"
export STORYSTUDIO_IMAGE_ADAPTER=openrouter_images
export STORYSTUDIO_IMAGE_API_KEY="<你的密钥>"
export STORYSTUDIO_VIDEO_ADAPTER=openrouter_video
export STORYSTUDIO_VIDEO_API_KEY="<你的密钥>"
storyvideo preview DEMO-1                # 只编译提示词和时长预算，不调模型、不花钱
storyvideo run DEMO-1 --through frames   # 冻结项目，用上面的生图来源出首帧（会花钱）
```

`DEMO-1` 要先存在于 `$STORYSTUDIO_PROJECT_ROOT/content/story_video/DEMO-1.json`，比如用同一个数据目录启动界面、
从示例复制一份得到。`storyvideo` 只用生图和视频两个角色；文本角色是给界面里的 AI 起草和审稿用的。

## 7. 启动器参数

`storystudio`（或 `python -m studio.desktop`）的全部参数：

| 参数 | 作用 |
|---|---|
| `--version` | 打印版本号后退出 |
| `--port PORT` | 指定端口。不指定时从 8756 开始，依次试 8757–8799，用第一个空闲的。指定的端口被占用时，打印占用者（能查到时）和一个可用端口，以退出码 2 退出 |
| `--browser` | 用系统浏览器打开，不开原生窗口 |
| `--no-open` | 只起服务，不打开任何窗口，直到 Ctrl+C |
| `--no-token` | 关掉本机访问令牌（会打印警告）。只建议调试接口时用 |
| `--project-root DIR` | 数据目录 |
| `--no-plugins` | 不加载插件（排查问题时用） |

服务只绑定 `127.0.0.1`。默认每次启动生成一个随机令牌，用 `http://127.0.0.1:<端口>/?token=<令牌>` 打开界面，之后靠 cookie 放行；
用 curl 调接口时带上请求头 `X-StoryStudio-Token: <令牌>`。
界面语言在侧栏底部切换，选择保存在浏览器的 `localStorage`（键名 `storystudio_lang`）。

## 8. 插件目录

内置来源之外的来源由插件提供。程序只从这两处加载插件：

1. `<设置目录>/plugins/<名字>/`，默认就是 `~/.storystudio/plugins/<名字>/`；
2. `STORYSTUDIO_PLUGIN_PATH` 列出的目录（每一项本身是插件目录，或装着多个插件的父目录）。

- 同名插件只加载第一个（默认目录在前）；目录名以 `.` 或 `_` 开头、或以 `.disabled` 结尾的跳过。
- 程序**有意不**从 `settings.json`、数据目录或程序所在目录加载插件。
- 插件加载失败时程序照常启动，设置页和 `/api/health` 会说明原因；设置里选了一个没加载的来源时，设置原样保留，装好插件重启就能继续用。
- 插件是普通 Python 代码，以你的用户权限运行，只装你信任的插件。插件接口 v1 是实验性的，1.0 之前可能调整。

写法见 [插件文档](../plugins.md)（中文）。

## 9. 数据目录结构

```text
<数据目录>/
├── content/                              项目文件（你的内容）
│   ├── story_video/<ID>.json             短剧项目（spec）
│   ├── story_video/<ID>.screenplay.json  短剧的分场剧本
│   ├── series/<系列 id>.json              系列记忆
│   ├── <模块>/<id>.json                   其他模块：image、clip、picturebook、sticker、portrait、music、podcast、script
│   └── assets/<id>/                      上传的素材（参考图、音频等）
├── output/                               生成的产物
│   ├── story_video/storyvideo.sqlite3    短剧账本（SQLite，WAL 模式）
│   ├── story_video/<ID>/                 短剧产物：01_frames/、02_video/、manifests/、<ID>_final.mp4、<ID>_post.mp4
│   └── <模块>/<id>/                       其他模块的产物
├── .webui_draft_cache/                   后台任务与起草记录（jobs/、tasks/），不会自动删除
├── .webui_deleted/                       回收站
├── local_workflows/                      你自己的 ComfyUI 工作流和同名 .map.json
└── .env                                  可选：SCRIPTGEN_* 起草兜底
```

- 删除项目时，项目文件、产物和素材一起移进 `.webui_deleted/<模块>/<id>__<UTC 时间>/`，可以在「回收站」恢复或彻底删除。
- 短剧产物目录的细节见 [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md) §7。
- 在仓库目录里直接运行时（启动器用当前目录，命令行用代码根目录），数据目录就是仓库根；
  `content/`、`output/`、`.webui_*`、`local_workflows/`、`.env` 都在 `.gitignore` 里。
- 「镜头库与风格库」页面的修改不写进数据目录，而是写进程序自带的 `skills/` 数据文件：
  源码安装会直接改动仓库里的文件（改到金丝雀用到的条目时，提示词金丝雀会报漂移）；
  打包版会尝试写进程序目录，这条路径没有测过——装在 Program Files 这类需要管理员权限的目录时很可能保存失败，
  写进去了重装也会覆盖。

## 10. 设置目录结构

```text
~/.storystudio/                    （STORYSTUDIO_HOME）
├── settings.json                  模型设置与密钥（STORYSTUDIO_SETTINGS 可改位置）
├── settings.json.lock             保存设置时的跨进程锁
├── settings.json.broken-<时间戳>   损坏文件的备份（只在出现过损坏时才有）
├── cache/                         适配器缓存（settings.json 的 cache_dir 可改）
├── logs/storystudio.log           日志
├── voices.json                    音色库
├── voices/                        试听样本、参考音频
├── model_catalog.json             模型目录缓存（手动刷新时才更新）
├── plugins/<名字>/                 插件
└── site-packages/                 打包版的可选组件（STORYSTUDIO_EXTRA_SITE 可改）
```

按安装脚本 `desktop/installer.iss` 的配置，Windows 卸载程序只删程序目录，不碰这个目录，也不碰 `~/StoryStudio`
（安装包还没有实际跑过）；不需要时自己删除。

## 11. 日志

- 位置：`<设置目录>/logs/storystudio.log`，默认 `~/.storystudio/logs/storystudio.log`。
- 按大小轮转：单个文件约 2 MB，另外保留 3 份旧日志；级别 INFO。
- 打包版没有控制台，日志是看服务端报错的唯一地方；源码运行时 WARNING 及以上还会打印到终端。
- 写日志前会遮掉密钥：`sk-…`、`AKIA…`、`AIza…`、JWT 形状的串、`Bearer` 头、URL 里的 `key=` / `api_key=` / `access_token=` / `token=`、
  名为 api_key / access_token / token / secret / password / authorization 的键值，以及 data URL 里的 base64 内容。
- 日志里会有本机的绝对路径，包括带用户名的主目录路径（比如启动时记下的数据目录）。
  界面上的部分报错（比如插件加载失败的原因）会把主目录写成 `~`，但日志文件不做这种替换。
- 贴日志到 issue 之前，先把路径里的用户名和其他不想公开的内容换掉。
