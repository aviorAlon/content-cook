English | [中文](../zh/configuration.md)

# Configuration reference

This page lists everything in Content Cook that you can configure: the two directories, the settings file,
the five model roles and their built-in sources, the unified gateway, local models, environment variables,
plugin directories, the data directory layout and logs.
For installation see [Installation and deployment](installation.md); for common questions see the [FAQ](faq.md).

> A note on names: the product is called "Content Cook", but the Python package, the commands
> (`storystudio` / `storyvideo`), the settings directory (`~/.storystudio`) and the environment variable prefix
> (`STORYSTUDIO_`) are technical identifiers that keep the `storystudio` name.
> Below, `~` means your home directory (`%USERPROFILE%` on Windows).

## 1. Two directories

The app keeps things in two places:

| Directory | What goes there | Default | How to change |
|---|---|---|---|
| **Data root** | Project files, generated images / videos / audio, the drama ledger, trash, background task records | See below | `--project-root` or `STORYSTUDIO_PROJECT_ROOT` |
| **Home** | Model settings and keys, cache, logs, voice library, model catalog cache, plugins, optional components of the packaged app | `~/.storystudio` | `STORYSTUDIO_HOME` |

How the data root is chosen:

| How you start it | Order of precedence (highest first) |
|---|---|
| Desktop launcher `storystudio` (including the packaged app) | `--project-root` → `STORYSTUDIO_PROJECT_ROOT` → `~/StoryStudio` for the packaged app, the current directory when run from source |
| CLI `storyvideo`, or serving `webui.app:app` directly | `--project-root` (`storyvideo` only) → `STORYSTUDIO_PROJECT_ROOT` → the code root (the repository root for a source install) |

Once the launcher has picked the data root, it exports it as `STORYSTUDIO_PROJECT_ROOT`, changes into it and creates
`content/story_video/` and `output/`.
If the UI uses a different directory from the CLI default (for example `~/StoryStudio` in the packaged app),
the recommended way is to `export STORYSTUDIO_PROJECT_ROOT=<data root>` before running `storyvideo`.
You can also pass `--project-root <data root>` to each command, but that only decides where projects and the ledger
live; ComfyUI's `local_workflows/`, the drafting fallback's `.env` and similar are still looked up through
`STORYSTUDIO_PROJECT_ROOT` (or the code root when it is not set).

## 2. The settings file (settings.json)

### 2.1 Location

1. The file named by the `STORYSTUDIO_SETTINGS` environment variable;
2. otherwise `<home>/settings.json`, which is `~/.storystudio/settings.json` by default.

Normally you edit it through **Model settings** in the left sidebar; there is no need to touch the file by hand.

### 2.2 Structure

```json
{
  "roles": {
    "text":  {"adapter": "openai_chat", "base_url": "https://openrouter.ai/api/v1",
              "api_key": "<your key>", "model": "<model id>", "voice": "", "extra": {}},
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

Top-level keys:

| Key | Meaning |
|---|---|
| `roles` | Source configuration for each of the five roles; see [§3](#3-model-roles-and-built-in-sources) |
| `cache_dir` | Adapter cache directory; empty means `<home>/cache` |
| `update_url` | URL of a version manifest, a JSON document shaped like `{"version": "...", "url": "...", "notes": "..."}`. Empty means the app never checks. Even when set, it is only fetched when you click **Check for updates**, and it only tells you whether a newer version exists and where to get it; nothing is downloaded or installed |
| `gateway` | Address and key of the unified gateway; see [§4](#4-unified-gateway) |
| `local_hints_dismissed` | Roles for which you turned off the "this could run locally" hint (role names, or `"all"`) |
| `output_root` | Reserved: it is read and saved, but no code uses it today. Where output goes is decided per project (for dramas, the project's own `output_root`, by default `output/story_video/<ID>`), always inside the data root |

The UI lets you change each role's source and form fields, the gateway, the update URL and the local hints.
`cache_dir` and each role's `extra` can only be set by editing the file (restart the app afterwards).

Fields of each role:

| Field | Meaning |
|---|---|
| `adapter` | Source id (see the tables in §3); empty means not configured |
| `base_url` | Endpoint URL; empty uses the source's default |
| `api_key` | Key; may be left empty when the effective URL is on this machine or the local network |
| `model` | Model id; empty uses the source's default model (only where one exists) |
| `voice` | Voice (voice sources only) |
| `extra` | Source-specific options, such as the ComfyUI workflow and seed, or SD WebUI sampling parameters (see §3.2) |

### 2.3 Writes and corruption

- **Atomic writes**: the app writes a temporary file in the same folder and then replaces the original,
  so readers always see either the complete old file or the complete new one.
- **Locking**: an in-process lock plus a cross-process lock file (`settings.json.lock`) queue concurrent saves,
  so two windows saving at once do not lose each other's changes. If the file changed on disk in the meantime,
  only the fields this save actually changed are merged in.
- **Corruption**: if the file is not valid JSON, the settings page still opens and shows a warning.
  The next save renames the broken file to `settings.json.broken-<timestamp>` before writing a new one.
  The new file starts from an empty configuration with only what you just saved; re-enter the other roles
  (you can copy them from the `.broken-*` backup).
- **Permissions**: after each save the file is restricted to the current user: `chmod 600` on macOS / Linux,
  and on Windows `icacls` removes inherited permissions and grants only the current user. This is best effort;
  a failure is not reported as an error.
- UTF-8 files with a BOM (as saved by Notepad and similar editors) are accepted.

### 2.4 How keys are handled

- Keys live only in `settings.json`, **in plain text**, protected by file permissions.
  They are never written into project files, the ledger or the logs.
- Keys returned by the API to the UI are always masked: 8 characters or fewer are fully masked; 9–15 characters show
  the first and last 2; 16 or more show the first and last 4.
- On save: sending back the masked value or an empty string keeps the stored key; `null` clears it;
  any other text replaces it.
- **Switching a role to another source (`adapter` changes) clears the old key.** Keeping the same source but pointing
  `base_url` at another host, port or scheme without entering a new key also clears it, so one provider's key is never
  sent to a different machine. Changing only the path (for example adding or removing `/v1`) does not count.
  The gateway key follows the same rule.
- Values overridden through environment variables (§6) apply at run time only and are never written back to the file.

## 3. Model roles and built-in sources

### 3.1 The five roles

| Role | Card on the settings page | Used for |
|---|---|---|
| `text` | Script text model | AI drafting, rewriting, and the text-model reviews (drama, podcast, script lab) |
| `image` | Image model (first frames / character looks) | First frames, character reference images, image sets, picture-book illustrations, posters |
| `voice` | Voice model (narration) | Narration, per-line podcast voices, audiobook reading |
| `video` | Video model | Per-shot drama videos, single clips |
| `music` | Music model | **Generate** in the Music module |

Each role picks one source (`adapter`); empty fields fall back to the source's defaults.
On a fresh install all five roles are empty. A feature that needs a role that is not configured stops before any task
starts and tells you where to set it up.

The shortest setup if you have a single OpenRouter key: choose `openai_chat` for text with the URL
`https://openrouter.ai/api/v1`, then choose the OpenRouter entry for image, video and voice, using the same key.
The text role also needs a model id, and the voice role needs a model and a voice (OpenRouter speech has no defaults);
image and video can keep their default models.

### 3.2 Built-in sources

"Fields" are the inputs shown in the settings form; "Defaults" are used when those inputs are left empty.

**Text (text)**

| Source id | Description | Fields | Defaults |
|---|---|---|---|
| `openai_chat` | Any OpenAI-compatible chat endpoint (official openai SDK, `/chat/completions`): OpenAI, OpenRouter, DeepSeek, Moonshot, Tongyi, Doubao and other compatible endpoints, plus local Ollama / LM Studio / vLLM / llama.cpp | base_url, api_key, model | base_url `https://api.openai.com/v1`; no default model, you must enter one |

**Image (image)**

| Source id | Description | Fields | Defaults |
|---|---|---|---|
| `openrouter_images` | OpenRouter images (official SDK), supports reference images | api_key, model | URL `https://openrouter.ai/api/v1`; model `bytedance-seed/seedream-4.5` |
| `openai_images` | OpenAI-compatible images: `/v1/images/generations`, and `/v1/images/edits` when reference images are used | base_url, api_key, model | `https://api.openai.com/v1`; `gpt-image-1` |
| `ark_images` | Volcengine Ark Seedream (official volcengine SDK); reference images are sent as base64 | base_url, api_key, model | `https://ark.cn-beijing.volces.com/api/v3`; `doubao-seedream-4-0-250828` |
| `sdwebui` | Local Stable Diffusion WebUI / Forge (`/sdapi/v1/txt2img`). Start the WebUI with `--api`. `model` is a checkpoint file name; if it differs from the loaded one the app switches first. Reference images use img2img, one at a time | base_url, model | `http://127.0.0.1:7860` |
| `comfyui` | Local ComfyUI, driven by an API-format workflow; built-in `txt2img_sdxl` / `img2img_sdxl` | base_url, model | `http://127.0.0.1:8188`; `sd_xl_base_1.0.safetensors` |

**Voice (voice)**

| Source id | Description | Fields | Defaults |
|---|---|---|---|
| `edge_tts` | Edge TTS (the edge-tts library; Microsoft's free online voices). `model` is the voice name; rate and pitch come from `voice_over.edge` in the project | model | `zh-CN-YunxiNeural` |
| `openai_speech` | OpenAI-compatible speech (`/v1/audio/speech`) | base_url, api_key, model, voice | `https://api.openai.com/v1`; `gpt-4o-mini-tts`; `alloy` |
| `openrouter_tts` | OpenRouter speech (official SDK) | api_key, model, voice | URL `https://openrouter.ai/api/v1`; no default model or voice |
| `minimax_speech` | MiniMax speech (`/v1/t2a_v2`), can use voices cloned in the voice library. Written from the public docs; not yet called with a real account | base_url, api_key, model, voice | `https://api.minimax.chat/v1`; `speech-02-hd`; `female-shaonv` |

When narration, reading or podcast lines have no explicit voice, the app uses the voice source from Model settings
if it is something other than Edge TTS, and Edge TTS otherwise.

**Video (video)**

| Source id | Description | Fields | Defaults |
|---|---|---|---|
| `openrouter_video` | OpenRouter video (official SDK), supports a first frame, polled asynchronously | api_key, model | URL `https://openrouter.ai/api/v1`; model `minimax/hailuo-3` |
| `ark_video` | Volcengine Ark Seedance (official volcengine SDK); first frame sent as base64, polled asynchronously | base_url, api_key, model | `https://ark.cn-beijing.volces.com/api/v3`; `doubao-seedance-1-0-pro-250528` |
| `openai_video` | OpenAI-compatible videos (`/v1/videos`, the Sora request shape), for gateways such as LiteLLM that forward other vendors' video models in this shape; first frame goes in `input_reference` | base_url, api_key, model | `https://api.openai.com/v1`; `sora-2` |
| `minimax_video` | MiniMax video (`/v1/video_generation`); first frame sent as a data URL, polled asynchronously | base_url, api_key, model | `https://api.minimax.chat/v1`; `MiniMax-Hailuo-02` |
| `comfyui` | Local ComfyUI video workflow. The built-in `txt2video_wan` is Wan 2.1 text-to-video and needs the Wan model files and the ComfyUI-VideoHelperSuite nodes installed; shot length is converted to 4n+1 frames at the workflow's frame rate | base_url, model | `http://127.0.0.1:8188`; `wan2.1_t2v_1.3B_fp16.safetensors` |

**Music (music)**: no built-in source. There is no common API shape for music generation, so **Generate** in the Music
module needs a [plugin](../plugins.md) that provides the `music` role.
Uploading your own audio as background music in dramas, podcasts and picture books works without one.

**ComfyUI options** (stored in the role's `extra`; edit `settings.json` by hand to set them):

| Key | Effect |
|---|---|
| `workflow` | Which workflow to use. Empty uses the first built-in workflow for the task type (for images with reference images, the first built-in workflow that takes an input image, `img2img_sdxl`). The **ComfyUI workflow** drop-down on the settings page is only for **Check mapping** and does not save your choice; to switch workflows, set `"extra": {"workflow": "<name>"}` for the role in `settings.json` |
| `seed` | Fixed seed (integer); empty means a random seed each time, recorded in the run record |
| `steps` / `cfg` / `denoise` | `steps` / `cfg` override the matching workflow parameters; `denoise` applies to image workflows only |
| `fps` | Frame rate of a video workflow (used to turn duration into frames); empty reads it from the workflow |

**SD WebUI options** (`extra`, file only): `steps` (default 30), `cfg_scale` (7.0), `sampler_name` (`DPM++ 2M Karras`),
`seed` (-1 = random), `denoising_strength` (used only for img2img with a reference image; 0.6), and `width` / `height`
(rounded to a multiple of 8).

**MiniMax speech**: when `extra.group_id` is set, it is added to request URLs as the `GroupId` query parameter.

Put your own exported workflows in `<data root>/local_workflows/`, each with a `.map.json` of the same name that says
which node field is the prompt, width, height, seed, output and so on.
See [local-models-spec.md](../local-models-spec.md) (Chinese) for details.

### 3.3 Model aliases in projects

In a project spec, the model `"@image"` / `"@video"` means "use the model configured for that role in Model settings".
The examples and AI drafting use these aliases by default. A concrete model id in the spec takes precedence.

### 3.4 Parameter checks and the model catalog

- Before production starts, parameters such as aspect ratio, size and duration are checked against a static capability
  table (source × model prefix), and resolutions are aligned to the nearest tier the model supports.
  Models not in the table only get a note that out-of-range values will be reported at submission time.
- The settings page can refresh a model catalog on demand (OpenRouter's public list, or the gateway's `/models`).
  It is cached in `<home>/model_catalog.json` and is advisory only.

### 3.5 What has been tested

Most direct-vendor sources have only been tested against fake clients; they follow the official docs and SDK signatures
but have not been run with real accounts. The local SD WebUI and ComfyUI adapters only have fake-HTTP tests.
Edge TTS has been run for real.
See "现状盘点" (status overview) in [BACKLOG.md](../../BACKLOG.md) and "已验证 / 未验证" (verified / unverified)
in [CHANGELOG.md](../../CHANGELOG.md); both are in Chinese.

## 4. Unified gateway

With an OpenAI-compatible gateway such as LiteLLM, new-api or one-api you do not have to fill in each role:

1. Under **Unified gateway (optional)** at the top of Model settings, enter the gateway URL and the gateway key once;
2. test the connection (`POST /api/settings/gateway/test`);
3. pick a model for each role and apply (`POST /api/settings/gateway/apply`).

After applying, the four roles use:

| Role | Source |
|---|---|
| text | `openai_chat` |
| image | `openai_images` |
| voice | `openai_speech` |
| video | `openai_video` |

The gateway is just a way to fill in several forms at once: applying copies the gateway URL and key into each role,
and at run time only the role configuration is read. Music is not covered (there is no OpenAI-compatible music API)
and has to be configured separately.

## 5. Local models

If you have a GPU, some or all roles can run on your own machine. This is optional; cloud sources keep working.

**Scan this machine** in Model settings (`GET /api/system/local-scan`) probes these common services:

| Service | Roles | Default URL |
|---|---|---|
| Ollama | text | `http://127.0.0.1:11434/v1` |
| LM Studio | text | `http://127.0.0.1:1234/v1` |
| vLLM | text | `http://127.0.0.1:8000/v1` |
| llama.cpp server | text | `http://127.0.0.1:8080/v1` |
| ComfyUI | image, video | `http://127.0.0.1:8188` |
| Stable Diffusion WebUI / Forge | image | `http://127.0.0.1:7860` |
| Local OpenAI-compatible speech (e.g. Kokoro-FastAPI) | voice | `http://127.0.0.1:8880/v1` |

- **Apply to a role** fills in the source, URL and model, and clears the role's previous key.
- **Only local addresses are contacted**: `localhost`, `127.0.0.0/8`, `::1`, `10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`, `169.254.0.0/16`, `fc00::/7`, `fe80::/10`. Look-alike host names such as
  `127.0.0.1.example.com` are rejected. The scan only probes the fixed addresses above; it is not a network scanner.
- For addresses in those ranges the key may be left empty, and cost notes read "Runs locally and costs nothing (N requests)".
- The "this could run locally" suggestions can be turned off; the choice is stored in `local_hints_dismissed`.
- These local adapters have not been verified on real hardware yet (see §3.5).

## 6. Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `STORYSTUDIO_PROJECT_ROOT` | Data root; precedence in §1 | Launcher: `~/StoryStudio` when packaged, the current directory from source. CLI and direct server: the code root |
| `STORYSTUDIO_HOME` | Home directory for settings, cache, logs, voice library, model catalog, plugins and optional components | `~/.storystudio` |
| `STORYSTUDIO_SETTINGS` | Full path of the settings file | `<home>/settings.json` |
| `STORYSTUDIO_<ROLE>_<FIELD>` | Run-time override of one field of one role, for headless use and CI. `ROLE` is `TEXT` / `IMAGE` / `VOICE` / `VIDEO` / `MUSIC`; `FIELD` is `ADAPTER` / `BASE_URL` / `API_KEY` / `MODEL` / `VOICE`. Only non-empty values count; never written back | Unset |
| `STORYSTUDIO_PLUGIN_PATH` | Extra plugin directories, separated by the OS path separator (`;` on Windows, `:` elsewhere) | Unset |
| `STORYSTUDIO_PLUGINS` | `off` / `0` / `false` / `no` disables all plugins; the launcher's `--no-plugins` sets this | Plugins load |
| `STORYSTUDIO_EXTRA_SITE` | Packaged app only: folder of optional components (faster-whisper and friends) added to `sys.path` at start; ignored if built for a different Python version | `<home>/site-packages` |
| `STORYSTUDIO_TOKEN` | Internal: the launcher puts the access token for this run here and the server checks against it. Do not set it yourself | Set by the launcher |
| `SCRIPTGEN_LLM_BASE_URL` / `SCRIPTGEN_LLM_API_KEY` / `SCRIPTGEN_LLM_MODEL` | Fallback for AI drafting: an OpenAI-compatible proxy used when no text role is configured | Unset |
| `SCRIPTGEN_BACKEND` | Forces the drafting path: `settings` (the text role) or `openai_proxy` (the proxy above) | Automatic: `settings`, then the proxy, otherwise an error |
| `SCRIPTGEN_PROXY_TIMEOUT_SECONDS` | Timeout in seconds for drafting requests (both paths) | `180` |
| `HF_HUB_CACHE` | Cache directory for faster-whisper models | `~/.cache/huggingface/hub` |

Notes:

- `SCRIPTGEN_*` values are read from the environment first and then from `<data root>/.env`. All other variables are
  read **only** from the real environment; putting them in `.env` has no effect. See [.env.example](../../.env.example);
  its `STORYSTUDIO_*` lines likewise only work as real environment variables, not from `.env`.
- The code also reads `SCRIPTGEN_MAX_BUDGET_USD`, but nothing currently enforces it, so setting it has no effect.
- Use `HF_HUB_CACHE` to move the Hugging Face cache. Known issue: once faster-whisper is installed, setting `HF_HOME`
  without `HF_HUB_CACHE` stops the status and buttons of the **Dialogue ASR gate (optional)** card on the settings page
  from loading, and downloading a model from the settings page fails too. The `storyvideo asr-qc` command is not affected.
- The packaging scripts also use `BUNDLE_DENYLIST` (a private word list for the bundle self-check), and
  `CODESIGN_IDENTITY` and `NOTARY_PROFILE` (macOS signing and notarization). See [desktop/README.md](../../desktop/README.md) (Chinese).

Headless example (bash):

```bash
export STORYSTUDIO_PROJECT_ROOT="$HOME/content-cook-data"
export STORYSTUDIO_IMAGE_ADAPTER=openrouter_images
export STORYSTUDIO_IMAGE_API_KEY="<your key>"
export STORYSTUDIO_VIDEO_ADAPTER=openrouter_video
export STORYSTUDIO_VIDEO_API_KEY="<your key>"
storyvideo preview DEMO-1                # compiles prompts and the duration budget only; no model call, no cost
storyvideo run DEMO-1 --through frames   # freezes the project, renders first frames (costs money)
```

`DEMO-1` must already exist at `$STORYSTUDIO_PROJECT_ROOT/content/story_video/DEMO-1.json` (for example, start the UI
with the same data root and copy an example). `storyvideo` only uses the image and video roles; the text role is used
for AI drafting and reviews in the UI.

## 7. Launcher options

All options of `storystudio` (or `python -m studio.desktop`):

| Option | Effect |
|---|---|
| `--version` | Print the version and exit |
| `--port PORT` | Use this port. Without it, the launcher tries 8756 and then 8757–8799 and takes the first free one. If an explicit port is taken, it prints who holds it (when it can tell) plus a free port, and exits with code 2 |
| `--browser` | Open the system browser instead of a native window |
| `--no-open` | Start the server only, without opening anything, until Ctrl+C |
| `--no-token` | Disable the local access token (prints a warning). For debugging the API only |
| `--project-root DIR` | Data root |
| `--no-plugins` | Do not load plugins (for troubleshooting) |

The server only binds to `127.0.0.1`. By default each start creates a random token and opens
`http://127.0.0.1:<port>/?token=<token>`; after that a cookie lets the UI through.
To call the API with curl, send the header `X-StoryStudio-Token: <token>`.
The UI language is switched at the bottom of the sidebar and stored in the browser's `localStorage`
(key `storystudio_lang`).

## 8. Plugin directories

Sources beyond the built-in ones come from plugins. Plugins are loaded only from:

1. `<home>/plugins/<name>/`, which is `~/.storystudio/plugins/<name>/` by default;
2. the directories listed in `STORYSTUDIO_PLUGIN_PATH` (each entry is either a plugin directory or a parent directory
   holding several plugins).

- If two plugins share a name, only the first is loaded (the default directory comes first). Directories whose names
  start with `.` or `_`, or end in `.disabled`, are skipped.
- Plugins are **deliberately not** loaded from `settings.json`, the data root or the program directory.
- A plugin that fails to load never stops the app; the settings page and `/api/health` show why. If a role is set to a
  source whose plugin is not loaded, the setting is kept as is and works again once the plugin is fixed and the app restarted.
- Plugins are ordinary Python code running with your user permissions. Install only plugins you trust.
  Plugin API v1 is experimental and may change before 1.0.

How to write one: [docs/plugins.md](../plugins.md) (Chinese).

## 9. Data root layout

```text
<data root>/
├── content/                              project files (your content)
│   ├── story_video/<ID>.json             drama project (spec)
│   ├── story_video/<ID>.screenplay.json  drama screenplay (scenes)
│   ├── series/<series id>.json           series memory
│   ├── <module>/<id>.json                other modules: image, clip, picturebook, sticker, portrait, music, podcast, script
│   └── assets/<id>/                      uploads (reference images, audio, ...)
├── output/                               generated output
│   ├── story_video/storyvideo.sqlite3    drama ledger (SQLite, WAL mode)
│   ├── story_video/<ID>/                 drama output: 01_frames/, 02_video/, manifests/, <ID>_final.mp4, <ID>_post.mp4
│   └── <module>/<id>/                    output of other modules
├── .webui_draft_cache/                   background task and drafting records (jobs/, tasks/); never deleted automatically
├── .webui_deleted/                       trash
├── local_workflows/                      your own ComfyUI workflows and matching .map.json files
└── .env                                  optional: SCRIPTGEN_* drafting fallback
```

- Deleting a project moves its project file, output and uploads into `.webui_deleted/<module>/<id>__<UTC time>/`;
  the Trash page can restore it or delete it permanently.
- Details of the drama output folder: [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md) §7 (Chinese).
- When you run from inside the repository (the launcher uses the current directory, the CLI uses the code root),
  the data root is the repository root; `content/`, `output/`, `.webui_*`, `local_workflows/` and `.env` are all in
  `.gitignore`.
- Edits made on the **Shot & style libraries** page do not go into the data root. They are written into the bundled
  `skills/` data files. In a source install that changes files in your checkout (and the prompt canary reports drift
  if you changed an entry it uses). In the packaged app the app tries to write into the program folder. This path is
  untested: under a folder that needs admin rights (such as Program Files) the save will most likely fail, and even
  when it succeeds a reinstall replaces the files.

## 10. Home directory layout

```text
~/.storystudio/                    (STORYSTUDIO_HOME)
├── settings.json                  model settings and keys (STORYSTUDIO_SETTINGS moves it)
├── settings.json.lock             cross-process lock used while saving
├── settings.json.broken-<stamp>   backup of a corrupt file (only if that ever happened)
├── cache/                         adapter cache (cache_dir in settings.json moves it)
├── logs/storystudio.log           log file
├── voices.json                    voice library
├── voices/                        preview samples and reference audio
├── model_catalog.json             model catalog cache (updated only when you refresh)
├── plugins/<name>/                plugins
└── site-packages/                 optional components of the packaged app (STORYSTUDIO_EXTRA_SITE moves it)
```

As configured in `desktop/installer.iss`, the Windows uninstaller removes only the program folder and leaves this
directory and `~/StoryStudio` alone (the installer has not actually been run yet); delete them yourself if you no
longer need them.

## 11. Logs

- Location: `<home>/logs/storystudio.log`, i.e. `~/.storystudio/logs/storystudio.log` by default.
- Rotated by size: about 2 MB per file, with 3 older files kept; level INFO.
- The packaged app has no console, so the log is the only place to see server errors. When run from source,
  WARNING and above are also printed to the terminal.
- Secrets are masked before they reach the log: `sk-…`, `AKIA…`, `AIza…`, JWT-shaped strings, `Bearer` headers,
  `key=` / `api_key=` / `access_token=` / `token=` in URLs, values of keys named api_key / access_token / token /
  secret / password / authorization, and base64 bodies of data URLs.
- The log contains absolute local paths, including your home directory and user name (for example the data root
  logged at start-up). Some messages in the UI (such as why a plugin failed to load) shorten the home directory to `~`,
  but the log file does not.
- Before pasting a log into an issue, replace your user name in paths and remove anything else you would rather not publish.
