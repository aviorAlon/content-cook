# 本地模型支持（2026-09-10）

需求：有显卡的用户可能在本机部署模型，设置里要能配置本地模型。

本地模型跟云端来源的差别只有三件事，其余（角色配置、能力表、任务、账本、重投纪律）全部照旧：

1. **不要 key**——本机服务通常没有鉴权，现在的表单和校验都把 `api_key` 当必填；
2. **不花钱**——预估、`cost_note`、成本闸都该说「本地生成，不花钱」，而不是报一个假的美元数；
3. **要能被发现**——用户不该去记 `http://127.0.0.1:11434/v1`，应该有个「扫描本机」把在跑的服务列出来。

## 1. 本地服务预设 `src/genkit/local.py`

```python
LocalService(
  id="ollama", name="Ollama", roles=("text",),
  base_url="http://127.0.0.1:11434/v1",      # 给适配器用的
  probe="http://127.0.0.1:11434/api/tags",   # 探活兼取模型列表
  adapter={"text": "openai_chat"},           # 应用到角色时填哪个适配器
  needs_key=False, doc="…",
)
```

要覆盖的服务（端口是各自的默认值，用户可改）：

| id | 名字 | 角色 | 默认地址 | 探活 / 取模型 |
|---|---|---|---|---|
| `ollama` | Ollama | text | `http://127.0.0.1:11434/v1` | `GET /api/tags` |
| `lmstudio` | LM Studio | text | `http://127.0.0.1:1234/v1` | `GET /v1/models` |
| `vllm` | vLLM | text | `http://127.0.0.1:8000/v1` | `GET /v1/models` |
| `llamacpp` | llama.cpp server | text | `http://127.0.0.1:8080/v1` | `GET /v1/models` |
| `comfyui` | ComfyUI | image, video | `http://127.0.0.1:8188` | `GET /system_stats`；模型列表 `GET /object_info/CheckpointLoaderSimple` |
| `sdwebui` | Stable Diffusion WebUI / Forge | image | `http://127.0.0.1:7860` | `GET /sdapi/v1/sd-models` |
| `openai_speech_local` | 本地语音（OpenAI 兼容 `/v1/audio/speech`） | voice | `http://127.0.0.1:8880/v1` | `GET /v1/models` |

`scan(timeout=1.2)`：并发探活所有预设（只连 localhost，不发任何请求到公网），返回 `[{id, name, base_url, alive, models[], error}]`。**只探本机地址**，用户自填的地址也必须是 `localhost` / `127.0.0.1` / `::1` / 私有网段，否则拒绝（避免把内网扫描做成功能）。

## 2. 不要 key

- `RoleConfig` 加 `local: bool`（或由 `base_url` 是否本机地址推导——**用推导**，少一个字段少一处不同步）：`genkit/local.py::is_local_url(url)`。
- 校验：`api_key` 为空且 `base_url` 是本机地址 → 放行；适配器发请求时给一个占位 key（`"local"`），因为 openai SDK 要求非空。
- 设置页的 key 输入框在选了本地预设 / 填了本机地址时显示「本机服务通常不需要」并允许留空。

## 3. 不花钱

- `capabilities` 每条加 `local: True` 与 `cost: "free"`（本地那几条）；`webui/generate.py` 的预估、图片集 / 单条视频 / 配乐的 `cost_note`，在角色配置是本地时改成「本地生成，不花钱（N 次请求）」。
- 成本闸（`scriptgen_max_budget_usd` 那条路）对本地来源不拦。

## 4. 两个本地生图 / 生视频适配器

### 4.1 `sdwebui`（A1111 / Forge，最常见、最好接）

- 文生图 `POST /sdapi/v1/txt2img`：`{prompt, negative_prompt, width, height, steps, cfg_scale, sampler_name, seed, n_iter, batch_size}` → `{images: ["<base64>"], info}`。
- 图生图 `POST /sdapi/v1/img2img`：多 `init_images: ["<base64>"]`、`denoising_strength`。参考图就走这条。
- 模型列表 `GET /sdapi/v1/sd-models`；切模型 `POST /sdapi/v1/options` `{sd_model_checkpoint}`。
- 同步返回，直接落盘；`aspect_ratio` → `width/height`（按 `image_size` 给的短边算，SDXL 默认 1024）。

### 4.2 `comfyui`（工作流最灵活，也最难标准化）

- 提交 `POST /prompt` `{prompt: <API 格式工作流>, client_id}` → `{prompt_id}`；轮询 `GET /history/{prompt_id}`；取图 `GET /view?filename=&subfolder=&type=output`；传参考图 `POST /upload/image`（multipart）。
- **工作流模板**：`skills/local-models/workflows/*.json` 放三份 API 格式工作流（`txt2img_sdxl.json`、`img2img_sdxl.json`、`txt2video_wan.json`），每份配一份同名 `.map.json` 说明哪个节点的哪个字段是提示词 / 负面 / 宽 / 高 / 步数 / 种子 / 输入图 / 输出节点：

  ```json
  {"prompt": ["6", "inputs", "text"], "negative": ["7", "inputs", "text"],
   "width": ["5", "inputs", "width"], "height": ["5", "inputs", "height"],
   "seed": ["3", "inputs", "seed"], "steps": ["3", "inputs", "steps"],
   "image": ["10", "inputs", "image"], "output": "9"}
  ```

- 用户可以把自己在 ComfyUI 里「导出（API）」的工作流丢进 `<项目根>/local_workflows/`，配一份 `.map.json`（设置页有个「检查映射」按钮：把映射套到工作流上，报哪个路径找不到）。
- `extra.workflow` 记选用的工作流名；没选就用内置的第一份。
- 因为工作流千差万别，**能力记录只写画幅与尺寸区间，不吹参考图与原生音频**。

## 5. 接口

- `GET /api/system/local-scan`（可带 `?ids=` 只扫某几个）：返回 §1 的结构；不写设置。
- `POST /api/settings/local/apply` `{service, role, model, base_url?}`：把某个探到的服务应用到某个角色（写 `adapter` / `base_url` / `model`，`api_key` 留空）。
- `GET /api/system/local-workflows`：内置 + 用户目录里的工作流清单（含映射是否合法）。
- `POST /api/system/local-workflows/check` `{name}`：把映射套到工作流上做一次干跑，报缺失路径。

## 6. 前端

设置页顶部加一张「本地模型」卡：

- 一句话说明「有显卡就能全程本地跑，不花钱、不出网；先在本机把 Ollama / ComfyUI / SD WebUI 起起来」；
- 「扫描本机」按钮 → 结果表（服务、地址、状态、模型数、「用它配 <角色>」按钮，多角色的服务给多个按钮）；
- 没扫到时给一行提示：常见端口没有响应，可以手填地址再扫；
- ComfyUI 那行多一个「工作流」下拉与「检查映射」；
- 角色卡的 key 输入框在本机地址下显示「本机服务通常不需要」。

## 7. 未验证

没有在真实的显卡和这些服务上跑过，**所有本地适配器都只有假 HTTP / 假子进程测试**：ComfyUI 的 `/prompt` 与 `/history` 形状、SD WebUI 的 `txt2img` 字段名、Ollama 的 `/api/tags`、内置工作流能不能在真实 ComfyUI 里跑通，全部按各自公开文档实现，真实验收留给有显卡的用户。

## 8. 实现偏差

按模块记「实现出来的和上面写的哪里不一样、哪些字段是从哪抄的」。

### 8.1 `sdwebui`（§4.1，`src/genkit/adapters/sdwebui.py`）

**没有对着真实的 Stable Diffusion WebUI / Forge 跑过一次。** 所有测试都是假 HTTP（`tests/test_sdwebui_adapter.py`、`tests/fixtures/adapters/sdwebui.json`）。下面每个字段名都来自 A1111 WebUI 自带的 `/docs`（FastAPI 生成的 OpenAPI 页，`StableDiffusionTxt2ImgProcessingAPI` / `StableDiffusionImg2ImgProcessingAPI`）与其 wiki 的 API 页，真实验收留给有显卡的用户。

从公开文档抄来、未验证的字段：

| 位置 | 字段 |
|---|---|
| `POST /sdapi/v1/txt2img` 请求 | `prompt`、`negative_prompt`、`width`、`height`、`steps`、`cfg_scale`、`sampler_name`、`seed`、`batch_size`、`n_iter` |
| `POST /sdapi/v1/img2img` 请求 | 以上全部，加 `init_images`（base64 字符串数组）、`denoising_strength` |
| 两者响应 | `images`（base64 数组，取第 0 张）、`info`（**JSON 字符串**，解出来放进产物旁的 `.info.json`）、`parameters`（没用） |
| `GET`/`POST /sdapi/v1/options` | `sd_model_checkpoint` |
| `GET /sdapi/v1/sd-models` | 数组元素的 `title`、`model_name` |
| 报错正文 | `error`、`errors`、`detail`（FastAPI 422 时 `detail` 是数组）、`msg`、`message` |

与 §4.1 的差异和自己定的规则：

- **尺寸**：§4.1 只说「按 `image_size` 给的短边算，SDXL 默认 1024」。实现取 `1K` / `2K` 都是短边 1024，`4K` 给 1536，写 `512` / `SD15` 才降到 512（`SHORT_SIDE_BY_SIZE`）。理由：SD 的**基础**出图分辨率一旦明显超过 checkpoint 原生尺寸就出「双头 / 重复肢体」，放大是 hires-fix / extras 的活，不该由基础分辨率硬顶；所以 2K 不等于 1440 短边。宽高一律对齐到 8 的倍数（`floor(x/8+0.5)*8`，不用 `round`——银行家舍入会把 900 变成 896）；认不出的画幅（`adaptive` 之类）按 9:16 兜底。要精确尺寸就在 `extra_params` 里直接给 `width` / `height`。
- **采样默认值**：`steps=30`、`cfg_scale=7.0`、`sampler_name="DPM++ 2M Karras"`、`seed=-1`、img2img 的 `denoising_strength=0.6`。文档里没有「推荐值」这回事，这是按社区常用值定的，角色配置的 `extra` 与 spec 的 `extra_params` 都能逐项覆盖。新版 WebUI 把采样器与调度器拆成了 `sampler_name` + `scheduler` 两个字段，`"DPM++ 2M Karras"` 这种合写法据文档仍作为旧写法被接受——**这一条尤其没验证**，用户的 WebUI 要是不认，在设置里把 `extra.sampler_name` 改掉即可。
- **多参考图**：`init_images` 虽然是数组，但 img2img 一次只重绘一张底图，它不是「多参考图」。给多了只发第一张，并把「另外几张是谁、去哪了」记在 `adapter.last_info["notes"]` 和产物旁边的 `<产物名>.info.json` 里——不写进 `TaskSubmission.error_message`（那是失败原因，成功的任务写了会被账本当错误记）。能力表因此写 `max_reference_images: 1`。
- **切模型**：`GET /sdapi/v1/options` 读到的 `sd_model_checkpoint` 是带哈希、可能带子目录的「标题」（`sd_xl_base_1.0.safetensors [31e35c80fc]`），用户填的往往只是文件名，所以比较前先归一化（去哈希、去目录、去扩展名、转小写），否则每张图都要白切一次模型。当前 checkpoint 缓存在适配器实例上：一批图只 `GET` 一次、最多 `POST` 一次。设置里 `model` 留空就用 WebUI 里当前加载的那个，不去切（表单上 `model` 仍是必填项，是给「扫描本机」抄模型名用的）。
- **不要 key**：`fields` 里没有 `api_key`，空 key 不带 `Authorization` 头；填了才带（有人把 WebUI 挡在反向代理后面）。表单里没有密钥栏，这把 key 靠手改设置文件或 `STORYSTUDIO_IMAGE_API_KEY` 给；设置页「保存」只在换来源时清密钥，同来源改模型不动它，并把这种表单里看不见的密钥单独摆出来、可一键清除（换来源漏过来的旧 key 也靠这里发现）。`base_url` 默认 `http://127.0.0.1:7860`，留空也能用。
- **错误分类**：连不上（`ConnectionRefusedError`，或 requests 被 `default_http` 包成 `ProviderTimeout` 后只剩英文的那种）翻成一句中文——「连不上本机 Stable Diffusion WebUI（地址）……确认 WebUI 已经启动，并且启动参数带了 `--api`」，类型是 `ProviderTimeout`，落到 `humanize.classify_failure` 的 `timeout` 桶（可重提）。HTTP 4xx/5xx 把服务端的 `error`/`errors`/`detail` 原话拼出来，再交给公共的 `raise_for_status` 分类——本机服务没有余额，现实中只会落到 `other`（爆显存、模型名写错、没带 `--api` 的 404）。
- **同步落盘**：和其它同步型生图适配器一样，解码 `images[0]` 后写进缓存目录，任务 id 是 `file:<路径>`，由 `router._with_file_url` 补 `file://` 结果地址，运行时不需要为它特判。响应是 JPEG / WebP 时按魔数改后缀。

### 8.2 `comfyui`（§4.2，`src/genkit/adapters/comfyui.py`）

**没有对着真实的 ComfyUI 发过一次请求。** 测试全是假 HTTP（`tests/test_comfyui_adapter.py`、`tests/fixtures/adapters/comfyui.json`）。接口形状按 ComfyUI 的公开文档 / 官方示例脚本（`script_examples/websockets_api_example.py`）与前端的 `api.js` 抄的，**内置工作流能不能在真实 ComfyUI 里跑通同样没验证**，真实验收留给有显卡的用户。

从公开文档抄来、未验证的接口与字段：

| 位置 | 字段 |
|---|---|
| `POST /prompt` 请求 | `prompt`（API 格式工作流：节点号 → `{class_type, inputs}`）、`client_id` |
| `POST /prompt` 200 | `prompt_id`（还有 `number` / `node_errors`，没用） |
| `POST /prompt` 400 | `error.message`、`node_errors[节点号].class_type` 与 `.errors[].message`（用来拼一句「几号节点哪个字段不对」） |
| `GET /history/{prompt_id}` | `{prompt_id: {status: {status_str, completed, messages}, outputs: {节点号: {images|gifs|videos: [{filename, subfolder, type}]}}}}`；`messages` 里 `["execution_error", {node_type, exception_message}]` 用来报失败原因 |
| `GET /queue` | `queue_running` / `queue_pending`，每项是 `[序号, prompt_id, prompt, extra_data, outputs]` |
| `GET /view` | 查询参数 `filename`、`subfolder`、`type`（`output`），返回二进制 |
| `POST /upload/image` | multipart 字段 `image`（文件）、`overwrite=true`、`type=input`；响应 `{name, subfolder, type}` |
| `GET /system_stats` | `system.comfyui_version`、`devices[0].name`（只用于「测试连接」的一句话） |

与 §4.2 的差异和自己定的规则：

- **任务 id 与轮询**：任务 id 是 `comfy:<prompt_id>`（`genkit.adapters.ASYNC_PREFIXES` 里登记）。`/history/{id}` 空是常态（还没排到），所以空的时候**再看一眼 `/queue`**：在 `queue_running` 里算 processing、在 `queue_pending` 里算 pending，两边都没有就直接判失败（「ComfyUI 重启过或队列被清空了，这一条重新生成即可」）——否则 ComfyUI 一重启，这一条会一直轮询到重投预算耗尽。历史里有记录但输出节点没有图/视频，且 `status.completed` 为真，也判失败并点名输出节点。
- **取件**：快照给的是 `http://127.0.0.1:8188/view?...`，本机接口不带密钥，运行时的通用下载器直接就能拉，不像 `openai_video` 那样要先拉进缓存。适配器另有一个 `download()`，给想经适配器落盘的调用方用（也是测试里验证「取件真的写出字节」的那条路）。
- **输出节点**：`/history` 的 `outputs` 按映射里的 `output` 节点取；找不到那个节点（工作流被改过、缓存的运行记录丢了）就扫所有输出节点。`images` / `gifs` / `videos` / `audio` 四个键都认——核心的 `SaveImage` 出 `images`，`VHS_VideoCombine` 出 `gifs`（是的，mp4 也放在 `gifs` 里）。
- **运行记录**：每次提交往缓存目录写一份 `comfyui_run_<prompt_id>.json`（工作流名、**种子**、输出节点、宽高/帧数、模型、提示词前 200 字），同时挂在 `adapter.last_info` 上。种子每次随机（`SystemRandom`，`extra_params.seed` 或角色配置的 `extra.seed` 能钉住），出了好结果照着记录把种子填回去就能复现；查询时也靠这份记录知道输出节点和这是图还是视频任务。
- **工作流的选择**：`extra.workflow` 选用哪一份；没选时按任务类型取第一份内置的（生图 `txt2img_sdxl`、视频 `txt2video_wan`）。**没选又带了参考图，自动改用第一份带 `image` 映射的内置工作流**（`img2img_sdxl`）——否则默认工作流没有输入图节点，参考图会被无声丢掉。反过来，选中的工作流有 `image` 映射却没给参考图，直接报错说清楚，不拿工作流里那张占位图去出活。
- **映射**：路径写成 `[节点号, "inputs", 字段名]`，`output` 只写节点号（也接受写成数组，取第一项）。支持的键：`prompt` / `negative` / `width` / `height` / `steps` / `seed` / `image` / `model` / `cfg` / `denoise` / `length` / `fps`，比 §4.2 的例子多了后面几个（视频要帧数和帧率，图生图要重绘强度）。映射里没有的键**跳过**（`img2img_sdxl` 就没有 `width`/`height`，尺寸跟着输入图走）；值是 `None` 的也跳过。映射指到不存在的位置时报一句点名「哪份工作流、哪个键、哪条路径、几号节点是什么类型」的中文错误。`.map.json` 里的 `task`（`image`/`video`）、`title`、`_comment` 是元信息，不当映射项；工作流 JSON 顶层的 `_comment` 在发给 `/prompt` 之前会被剥掉（ComfyUI 只认 `{class_type, inputs}`，多一个键就是 400）。
- **用户工作流**：`<项目根>/local_workflows/`，**同名时压过内置那份**。`builtin_workflows()`（只列内置）、`list_workflows(project_root)`（内置 + 用户）、`load_workflow(name, project_root)`、`check_mapping(name, project_root)` 都是纯函数、import 时不做任何事，供设置页的 `GET /api/system/local-workflows` 与「检查映射」调用。工作流名只允许字母数字下划线点和短横（不接受路径分隔符，免得「检查映射」变成任意文件读取）。
- **尺寸与帧数**：画幅 + `image_size` 短边（`1K`→1024、`2K`→1440、`4K`→2048）算宽高，对齐 8 的倍数；视频的短边取 `resolution` 里的数字（`480p`→480），认不出按 480（Wan 1.3B 的原生档）。镜头时长 × 工作流自己的帧率 → 帧数，取 `4n+1`（Wan / Hunyuan 这类视频潜空间的要求）。
- **模型名**：只有**看着像本机权重文件**（`.safetensors` / `.ckpt` / `.gguf` 等后缀）的模型名才会被填进 `ckpt_name` / `unet_name`；spec 里默认写的是云端模型 id（`doubao-seedance-…`），那种一律忽略、用设置里的 `model`。设置里 `model` 留空就用工作流自带的。
- **参考图上传缓存**：按文件 sha1 缓存在 `comfyui_uploads.json`，键带 `base_url`（换一台 ComfyUI 就是另一份缓存，因为文件是传到那台机器的 `input/` 目录里的）。`LoadImage` 填的值是 `subfolder/name`（没有子目录就只有 `name`）。
- **能力表**：按 §4.2 的要求**只写画幅与尺寸区间**（外加视频的时长与分辨率档），`local: True`、`cost: "free"`、`source: "doc"`；不写 `reference_images`、不写 `audio`、不写 `first_frame`——能不能吃参考图、有没有声音，取决于用户选的那份工作流。

内置工作流（`skills/local-models/workflows/`，随包）用到的节点：

| 工作流 | 节点 | 需要的模型 / 自定义节点 |
|---|---|---|
| `txt2img_sdxl` | `CheckpointLoaderSimple`、`CLIPTextEncode`×2、`EmptyLatentImage`、`KSampler`、`VAEDecode`、`SaveImage` | 全是核心节点；`models/checkpoints/sd_xl_base_1.0.safetensors` |
| `img2img_sdxl` | 上面那套，去掉 `EmptyLatentImage`，加 `LoadImage` + `VAEEncode` | 全是核心节点；同上 |
| `txt2video_wan` | `UNETLoader`、`CLIPLoader`、`VAELoader`、`CLIPTextEncode`×2、`EmptyHunyuanLatentVideo`、`ModelSamplingSD3`、`KSampler`、`VAEDecode`、**`VHS_VideoCombine`** | `diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors`、`text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`、`vae/wan_2.1_vae.safetensors`；**`VHS_VideoCombine` 来自自定义节点包 ComfyUI-VideoHelperSuite**——没装就把 10 号节点换成核心的 `SaveAnimatedWEBP` 并把 `.map.json` 的 `output` 改成新节点号（工作流的 `_comment` 里也写了这句）

### 8.3 预设 / 扫描 / 设置接线 / 接口 / 前端（§1、§2、§3、§5、§6，`src/genkit/local.py`、`src/webui/routers/local_models.py`）

**没有对着任何真实的本地服务扫过一次。** 探活与模型列表全部是注入的假 fetch（`tests/test_local_models.py`）。四种列表形状（Ollama `/api/tags` 的 `models[].name`、OpenAI `/v1/models` 的 `data[].id`、ComfyUI `object_info/CheckpointLoaderSimple` 的 `input.required.ckpt_name[0]`、SD WebUI `sd-models` 的 `model_name` / `title`）都按各自公开文档写。

与 §1–§6 的差异和自己定的规则：

- **`LocalService` 多两个字段**：§1 的示例只有 `probe`，但「探活」和「取模型列表」在 ComfyUI 那条是两个不同地址，而四种列表的解析各不相同——所以加了 `models_probe`（空 = 跟 `probe` 同一个）与 `kind`（`ollama_tags` / `openai_models` / `comfyui_object_info` / `sdwebui_models`）。用户改地址时只有 scheme + host + port 会被换掉，路径按预设里的原样接上（`probe_url` / `models_url`）；适配器地址（扫描行的 `base_url`、`apply` 写进角色的 `base_url`）同样按此规则（`adapter_url`）——用户只填 host:port 时补预设路径（`/v1`），自己写了路径就照用户的。
- **本机网段用白名单，不用 `ipaddress.is_private`**：标准库把 `240.0.0.0/4`、`100.64.0.0/10` 也算私有，而 `::1` 又落在 `is_reserved` 里，语义对不上「本机 / 家里那台机器」。实现按 §1 的原话列白名单：`127.0.0.0/8`、`10/8`、`172.16/12`、`192.168/16`、`169.254/16`、`::1/128`、`fc00::/7`、`fe80::/10`，外加主机名恰好是 `localhost`。**主机名不是 IP 也不是 `localhost` 的一律拒绝**（`http://127.0.0.1.evil.com`、`http://127.0.0.1@evil.com`），协议只认 http / https。
- **`local` 是推导的，没有新字段**（§2 里点名要「用推导」）：`RoleConfig.local` 看原始 `base_url`；`settings.role_is_local()` 看**补完适配器默认地址之后**的地址——`sdwebui` / `comfyui` 默认就是 127.0.0.1，地址栏留空也算本地。`GET /api/settings` 的角色里多带一个 `local`，只在接口视图（`mask_secrets=True`）里给，不写进 `settings.json`。
- **占位 key 只在本机地址下补**：`local.api_key_for()` 本机留空 → `"local"`（显式常量 `PLACEHOLDER_API_KEY`，代码里写清「不是凭据，只为过 openai SDK 的非空校验」），远端留空仍是原来的 `"missing"`。`apply_to_role` 会把 `api_key` **清空**——留着上一个远端来源的密钥，下一次请求就会把它发到本机服务上去。
- **§3 的能力记录**：`sdwebui` / `comfyui` 的静态记录带 `local: True` + `cost: "free"`。**本地语音那条没有自己的适配器 id**（预设 `openai_speech_local` 的适配器就是 `openai_speech`），给它单写一行会把远端 OpenAI 语音也标成免费——所以改成 `capabilities.is_local(adapter, base_url)`：`LOCAL_ADAPTERS` 里的永远算本地，其余按地址推导，`lookup()` 出口统一贴上 `local` / `cost`。本地文本（`openai_chat` 指到 Ollama）同理。
- **`cost_note` 走一个统一入口**：`local.cost_note_for(role, n, 远端那句)`。图片集 / 单条视频 / 配乐 / 绘本 / 海报五处 + `webui/generate.estimate_plan`（多返回 `local` 与 `cost_note` 两个字段，远端时 `cost_note` 是 `null`，前端文案不变）。读不出设置文件时按「不是本地」——宁可多说一句"会花钱"，也不能把付费来源说成免费的。
- **成本闸**：`scriptgen_max_budget_usd()` 现在在文本角色是本地时返回**空串**（不设上限），并新增 `scriptgen.text_role_is_local()`。注意这个函数现在可能返回空串，调用方判断真假即可、不要直接 `float()`（当前仓库里没有别的调用方）。
- **接口单独一个路由模块** `webui/routers/local_models.py`（不塞进 `assist.py` / `settings.py`）：`GET /api/system/local-scan` 多两个可选参数——`ids`（逗号分隔）与 `base_url`（手填地址，必须跟 `ids` 一起给，否则 400「哪个服务在这个地址上」）；公网地址在发请求之前 400。工作流那两条**懒导入** `genkit.adapters.comfyui` 并直接复用它的 `list_workflows()` / `check_mapping()`：适配器不在时清单返回空 + 一句人话（`note`），`check` 返回 425，不 500。
- **前端**：`frontend/src/local.js::isLocalUrl` 是后端判据的一份 JS 副本，只用来决定角色卡的 key 输入框要不要显示「本机服务通常不需要，可以留空」——真正的拦截在后端。手填地址扫描时结果**并进**上一次的表（只换那一行），不把整张表冲掉。

## 9. 推荐而不强制（2026-09-10 追加）

需求：在用得上的时候推荐本地模型，但不强制。本地是一条**可选**的路：什么都不做，云端来源照常能用。这一层只在三个位置各说**一行**，每一行都能永久关掉。

**接口**

- `GET /api/system/local-hint?role=<角色>` → `{available: [{service, name, base_url, models, roles}], current_is_local, suggestion: {service, name, reason} | null, dismissed, scanned}`。
  只探写死的预设，**不收手填地址**（提示不能变成探测器）。探活结果**按服务**在内存里缓存 60 秒（`HINT_TTL`），窗口内一个请求都不发；没有缓存时只**快探**（0.5 秒超时）能顶这个角色的那几个预设，不扫全部七个。`GET /api/system/local-scan` 的结果也写进同一份缓存，并多返回 `recommend: {角色: 服务 id}`，每行多一个 `reason`。
- `POST /api/system/local-hint/dismiss` `{role | "all"}`：关掉这条提示，写进设置文件的 `local_hints_dismissed`（服务端，不是浏览器状态）。
- `POST /api/system/local-hint/reset`：清空关闭名单（设置页「重新开启本地提示」按钮）。

**点名规则**（`local.ROLE_PREFERENCE`）

| 角色 | 顺序 | 说明 |
|---|---|---|
| `image` | `sdwebui` → `comfyui` | SD WebUI 的接口是固定的 `/sdapi/v1/txt2img`，装好就能用；ComfyUI 要先有一份对得上的工作流，所以只在它是唯一在跑的那个时才被点名 |
| `video` | `comfyui` | SD WebUI 不出视频 |
| `text` | `ollama` → `lmstudio` → `vllm` → `llamacpp` | 第一个在跑的赢 |
| `voice` | `openai_speech_local` | |
| `music` | —— | 没有本机预设，永远不推荐 |

`suggestion` 在三种情况下是 `null`：这个角色已经是本机来源（`current_is_local`）、一个都没探到、用户关过（`dismissed`）。

**三个位置**

1. **角色还没配好**（`frontend/src/components/LocalTip.vue`，用在短剧引导第一步与各处「还没配置 X 模型」旁边）：「有显卡的话可以本地跑，不花钱、不出网 → 扫描本机」。只在**真探到**在跑的服务、或**用户从来没扫过**（`scanned: false`）时出现；没扫过时文案不声称本机有什么在跑。
2. **花钱之前**（`ModalHost.vue`；`ui.confirm` / `ui.custom` 多一个 `localRole` 选项，短剧预估、图片集生图、单条视频出片/出首帧、配乐生成都传）：「本机有 {名字} 在跑，切过去这次不花钱」+「去切换」+「不再提示」。请求是**异步**发的，不挡弹窗；没有 `suggestion` 时弹窗与以前完全一样，按钮一个不改，也从不拦下这次操作。
3. **设置页的本地模型卡**：被点名的服务排在前面、标「推荐」并给一行理由（如 sdwebui「出图推荐：接口固定、装好就能用」、comfyui「本地生视频 / Flux 走它」）；什么都没探到时**只有一行**，不放安装教程、不放下载页链接。

**文案纪律**：任何一处都不许读成「先装好 X 才能用」。已经改掉的三处——本地模型卡的开头那句（现在写「这是一条可选的路，云端来源什么都不改也照常能用」）、`settings.ADAPTERS["image"]["sdwebui"]` 的「WebUI 必须带 --api 启动」→「接口在 WebUI 带 --api 启动时才开」、`local_models.COMFY_MISSING` 的「先装上它」→「装上它之后会自动出现」。选它需要什么就陈述需要什么，不发号施令。

**验收**：`tests/test_local_hint.py`（18 条）、`frontend/tests/local-hint.test.js`（12 条）。**未验证**：同 §7，开发时没有这些服务，缓存与快探全部走假 fetch。
