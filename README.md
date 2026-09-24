# Content Cook · Content creation workbench

English | [中文](README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/aviorAlon/content-cook/actions/workflows/ci.yml/badge.svg)](https://github.com/aviorAlon/content-cook/actions/workflows/ci.yml)

**An AI content creation workbench that runs on your own computer: you bring the models, your data stays
local, and every paid run tells you how many requests it will send before anything is spent.**

Short dramas, picture books, podcasts, single video clips, sticker packs, portrait sets, music and script
polishing: nine modules share one workbench, with common model settings, a voice library, a unified
gateway, a task centre, a trash bin and exports. You connect the models yourself (cloud accounts,
OpenAI-compatible gateways or models running on your machine). Projects, outputs, ledgers and API keys are
stored only on your computer; what is sent to model providers is listed under
[Data and privacy](#data-and-privacy).

> Current version: **v0.1.0**, the first open-source release. The desktop build (PyInstaller folder) has only
> been built and run on Windows x64; the Inno Setup installer step has never actually run. Many model sources
> are covered by fake-client tests only. Please read
> [Project status and known limitations](#project-status-and-known-limitations) before you start.

The UI is available in Chinese and English (switch at the bottom of the sidebar). Most of the in-depth
design and runtime documents in this repository are written in Chinese; the English guides are listed in
[Documentation](#documentation).

## Contents

- [What it is and who it is for](#what-it-is-and-who-it-is-for)
- [Modules](#modules)
- [Design highlights](#design-highlights)
- [Data and privacy](#data-and-privacy)
- [Quick start](#quick-start)
- [Running and launch options](#running-and-launch-options)
- [Model sources](#model-sources)
- [The storyvideo CLI](#the-storyvideo-cli)
- [Building the desktop app](#building-the-desktop-app)
- [Development](#development)
- [Documentation](#documentation)
- [Project status and known limitations](#project-status-and-known-limitations)
- [Contributing](#contributing)
- [Acknowledgements](#acknowledgements)
- [License](#license)

## What it is and who it is for

Content Cook is a **local-first** AI content creation tool. It packs pipelines such as
"write → generate images / videos / voices → review → compose → deliver" into one desktop app. The UI is a
local web page (in a native window or your browser), the backend is Python (FastAPI), and generation goes
through whatever model sources you configure.

| Who | Typical pain | What you get |
|---|---|---|
| Independent short-drama and short-video creators | Images and clips shuffled between websites; prompts, reference images and versions tracked by memory and folders; wrong lines or broken continuity found only after generation | One spec per project, shot-by-shot cards, three review gates plus a dialogue-recognition gate, so you know which shot is wrong before the final cut |
| Content leads in small studios | Different people on different model accounts, so cost and quality cannot be compared; switching models means rewriting prompts | Models are *roles*, not vendors: pick one source per role, and prompt compilation is decoupled from any specific model |
| Technical creators who script | Want automation without maintaining a pile of scripts | The drama production pipeline (freeze, frames, videos, review, dialogue recognition, assembly, post-production, crash recovery) is also available as a CLI (`storyvideo`) that shares the UI's runtime |

**Out of scope:** multi-user collaboration and permissions, cloud render queues, an asset store, user
accounts, and vendor-exclusive features. The server only listens on the loopback address; LAN or remote
access is not supported.

### About the names

"Content Cook" is a working product name. The UI, window title and API read it from `src/webui/branding.py`;
the installer script `desktop/installer.iss` keeps its own default AppName, which a test requires to match,
so change both when renaming. The technical identifiers below **intentionally do not follow the product name** and will not change if it is renamed:

| Identifier | Value |
|---|---|
| Python distribution (pyproject) | `storystudio` |
| Commands | `storystudio` (desktop launcher), `storyvideo` (short-drama CLI) |
| Settings directory | `~/.storystudio` |
| Desktop build | `StoryStudio.exe`, build folder `dist/StoryStudio/`, macOS bundle `StoryStudio.app` |
| Default data directory of the desktop build | `~/StoryStudio` |
| Frontend npm package | `storystudio-frontend` |

## Modules

| Module | What it does |
|---|---|
| **Short drama** | Screenplay (by scene) → per-shot storyboard and prompt compilation → first frames → videos → three review gates (frames / videos / final) plus a dialogue-recognition gate → assembly → post layers (narration, dubbing, music, cards, on-screen text, labels, silence, subtitles) → poster and delivery pack. The production pipeline is also available as the `storyvideo` CLI. |
| **Image studio** | Single or batch images, multi-model comparison, text cards. |
| **Single clip** | One prompt, one video clip, written in three parts: global setup / segmented timeline / continuity and ending. |
| **Picture book / audiobook** | Illustrations in one consistent style plus bilingual narration and subtitles; each page stays on screen as long as its narration audio actually runs. |
| **Stickers & avatars** | Template-driven image sets: emoji packs (WeChat sizes), stickers, multi-style avatars. |
| **Milestone portraits** | Full-moon, 100-day, first-birthday and wedding sets from a scene list, with optional greeting and date overlay. |
| **Music** | Generates music from an intent brief and drops it into a drama's music layer, a picture book's BGM, or a podcast's intro / bed / outro. Needs a source that provides the `music` role (there is no built-in one yet; see [Model sources](#model-sources)). |
| **AI podcast** | Talk script (segments / lines / listener Q&A) → static checks and a human-likeness review → per-line voices → mix → delivery pack. |
| **Script lab** | Write dialogue on its own, keep versions, compare them side by side and pick one, then hand it to a podcast segment or a drama scene / shot, or import from them. |

Stickers & avatars, milestone portraits and the image studio share one "image set" engine: generate a set
from a template → pick → local post-processing → package.

Shared across modules:

- **Model settings**: five roles (text, image, voice, video, music), one source each, with
  "Test connection", a unified gateway and a scan for local model servers.
- **Voice library**: audition and save voices, bind them to drama characters or narration (binding copies
  the parameters); podcast lines also pick their voices here.
- **Image style cards**: 24 cards shared by image sets, picture books, posters and single clips.
- **Shot & style libraries**: the data behind drama prompt compilation, editable entry by entry in the UI
  (validated before it is written).
- **Task centre**, **Trash** (deletions can be restored), recent projects, per-module delivery exports, and a
  bilingual Chinese / English UI.
- **Sample projects**: nine ship with the app. Every module home page except the script lab can copy one
  into your own project, free of charge.

See the [architecture guide](docs/en/architecture.md) for how modules and shared services are layered.

## Design highlights

| Highlight | How it works |
|---|---|
| **Local-first** | The server binds to `127.0.0.1` only, creates a random access token on every start, and checks Host / Origin. Projects, outputs, ledgers and keys stay on your machine. No telemetry. |
| **Bring your own models** | Models are configured per role, one source per role. Built in: OpenAI-compatible APIs, OpenRouter, Volcengine Ark, MiniMax, Edge TTS and local servers (Ollama, ComfyUI, SD WebUI and others); anything else comes from plugins. Writing `"@image"` / `"@video"` in a spec means "use the model from settings". |
| **Estimate first, then spend** | Before drama generation you get an estimate (how many requests, which shots, which failed or lack upstream frames). Confirming sends the estimate's fingerprint back; the server recomputes it from disk and refuses with 409 if it no longer matches, without sending a single request. Other modules state the request count in the confirm dialog, and local sources say "free". AI drafting never retries automatically or quietly generates extra candidates. |
| **Freeze and ledger** | "Start production" freezes the drama spec. Every paid drama request is recorded in a local SQLite ledger together with a request fingerprint. The "submitting" intent is written before the provider is called, so a crash leaves a row to reconcile, never a silent resubmission or double charge. Single clips, image sets, picture books and music record every generation attempt in their own project files. |
| **Never overwrite** | Every retake is saved as a new file (numbered or time-stamped), and a failure never replaces the last good result. Final renders are written atomically, so a failed render leaves no half file. Deleting moves a project to the trash, from which it can be restored as a whole. |
| **Review before spending more** | Three human review gates for drama (frames / videos / final), technical QC, and a dialogue-recognition gate (local faster-whisper: character error rate ≤ 0.30 passes, ≤ 0.45 needs a re-listen). Most modules also have free local static checks (duration budget, camera moves, dialogue clarity, continuity, podcast human-likeness and more); text-model reviews send one request each. |
| **Memory-friendly long renders** | Post-production is rendered in streamed segments instead of one giant ffmpeg command; picture books are rendered page by page and joined without re-encoding. |
| **Extensible** | Plugins load from your user directory and can register new model sources, async task prefixes, capability records and translations without touching the core. If a plugin fails, the app still starts and explains why. Plugin API v1 is experimental. |
| **Use mature libraries** | Official SDKs where they exist (openai, openrouter, the Volcengine SDK); Edge TTS goes through the community library edge-tts (not an official Microsoft API); MiniMax, SD WebUI, ComfyUI and OpenAI-compatible videos use hand-written HTTP. The desktop shell is pywebview + PyInstaller, and the UI talks to the backend over HTTP only. |

The reasoning behind these choices is in the [design guide](docs/en/design.md); the full design log is
[DESIGN.md](DESIGN.md) (Chinese).

## Data and privacy

- The server binds to `127.0.0.1` only and requires an access token that is generated randomly on each
  start. It also checks Host / Origin, so other web pages cannot use your browser to drive the local service.
- Project files, outputs and ledgers live in the **data directory** (in development: the current directory
  at launch; desktop build: `~/StoryStudio`; override with `--project-root` or `STORYSTUDIO_PROJECT_ROOT`).
- Model settings and keys live in `~/.storystudio/settings.json` (move the directory with
  `STORYSTUDIO_HOME`). The API only ever returns masked keys, and keys are masked in logs and task records.
- What leaves your machine during generation: prompts, reference images / reference audio and parameters,
  sent to the model sources you configured. Voice cloning uploads your reference recording to MiniMax.
- Narration, voice-over and podcast voices default to Edge TTS (Microsoft's online speech service) when no
  other voice is chosen, so the text to be spoken is sent there.
- Everything else only happens when you click it: fetching OpenRouter's public model list, checking for
  updates (only if you set an update URL; it checks but never downloads), listing Edge TTS voices,
  installing dialogue recognition (from PyPI), and downloading the faster-whisper model on the first
  recognition run (from Hugging Face). No telemetry.

| Location | Contents | Default |
|---|---|---|
| Data directory | `content/` (project files), `output/` (outputs and the drama ledger), task records, trash, custom ComfyUI workflows | Development: current directory at launch; desktop build: `~/StoryStudio` |
| Settings directory | `settings.json` (model settings and keys), `logs/`, `cache/`, voice library, model catalogue cache, `plugins/` | `~/.storystudio` |

The full layout and every environment variable are in the [configuration guide](docs/en/configuration.md).

## Quick start

### Requirements

| Item | Requirement |
|---|---|
| Operating system | The desktop build (PyInstaller folder) has only been built and run on **Windows x64**; the Inno Setup installer step has never actually run. The macOS packaging script exists but has never been run on a Mac; there is no Linux package. |
| Python | 3.10 or newer (declared in `pyproject.toml`); only **3.11** has actually been tested. |
| ffmpeg / ffprobe | Must be on `PATH`. Used for drama assembly and post-production, picture-book and podcast rendering, and dialogue recognition. Download: <https://ffmpeg.org/download.html> |
| CJK font | Burned-in subtitles, cards and on-screen text need a CJK font. Windows uses its built-in Microsoft YaHei / SimHei / SimSun; macOS uses PingFang and similar; on Linux install Noto CJK (for example `fonts-noto-cjk` on Debian / Ubuntu). |
| Node.js | Only needed to change the UI (22 LTS recommended); the built UI is committed to the repository. |
| Optional | `faster-whisper` (dialogue-recognition gate); local model servers (Ollama, LM Studio, ComfyUI, SD WebUI and others). |

### Option 1: run from source (recommended)

```bash
git clone https://github.com/aviorAlon/content-cook.git
cd content-cook
python -m venv .venv                 # optional, but recommended; use python3 on macOS / Linux
# Windows: .venv\Scripts\activate    macOS / Linux: source .venv/bin/activate
python -m pip install -U pip         # editable installs need a recent pip (there is no setup.py)
pip install -e ".[desktop]"          # includes pywebview (native window) and PyInstaller (packaging)
pip install -e ".[asr]"              # optional: faster-whisper for the dialogue-recognition gate
storystudio                          # start the local server and open the window
```

- Linux: the native window is untested; install with `pip install -e .` (without the desktop extra) and start
  with `storystudio --browser`. If `.[desktop]` is installed but the Qt / GTK Python bindings are missing, the
  launcher reports that the native window could not start and falls back to the system browser.
- Only an **editable install inside the clone** (`pip install -e`) is supported: runtime data such as the
  shot library, style library and sample projects is read from the repository directory and is not part of
  a wheel. There is no PyPI package.
- The built UI is committed in `src/webui/static/app/`, so running the Python side does not need Node.
- Without `--project-root`, data is written to the current directory at launch. Running from the
  repository root is fine (`content/`, `output/` and friends are in `.gitignore`); to keep data apart from
  the code, pass `--project-root <data dir>`.

### Option 2: desktop build (Windows, build it yourself)

There are **no prebuilt downloads** at the moment. You can build one yourself (after installing
`.[desktop]` as in option 1):

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build_windows.ps1
```

The result is the folder `dist/StoryStudio/` (double-click `StoryStudio.exe`); with Inno Setup 6 installed
the script also produces the installer `dist/installer/StoryStudio-<version>-setup.exe` (this step has never
actually run). CI also builds the Windows package when a `v*` tag is pushed or the workflow is started
manually, and uploads it as the Actions artifact `StoryStudio-windows`. It does not publish a release, and
as of the v0.1.0 release (2026-09-24) the workflow had not run on GitHub yet. Neither the executable nor the installer is signed, so Windows may
show a warning.

Step-by-step instructions for each platform, ffmpeg, dialogue recognition, upgrading and uninstalling are
in the [installation guide](docs/en/installation.md).

### First steps

On first launch (before any project exists) the workbench home shows a "First time here" guide:
**Set up models → Pick a module to start → You are asked before anything costs money**. Taking short drama
as the example: when the Short drama module has no projects yet, it shows a three-step guide:
**Set up models → Get a project → Generate first frame**.

1. "🔌 Model settings" in the sidebar: pick a source for each role → enter the URL and key → save →
   "Test connection". Image, voice and video each have an OpenRouter source; for text pick
   "OpenAI-compatible chat API" and set its base URL to `https://openrouter.ai/api/v1`, so one OpenRouter
   key covers all four. Voices can also start with the free Edge TTS.
2. Get a project: "Copy the sample episode (free)" on the drama home page, or "Start from an example" at
   the top of the new-project page, copies the bundled sample episode (5 shots, with dialogue, narration and
   one card) into your own project. Or use "Draft an episode with AI" to let the text model write a whole
   episode from one sentence. The other module home pages (image studio, single clip, picture book,
   stickers & avatars, milestone portraits, music, podcast; not the script lab) also offer
   "Copy the sample (free)".
3. Open the project → "Start production" → generate a first frame for one shot to check the style, then
   run the whole batch. Before anything is spent you are told how many requests will be sent, and nothing is
   submitted until you confirm.
4. Once all videos are in: "Dialogue recognition" (local faster-whisper, optional) checks that each shot
   says its lines; "Generate post-production cut" composes narration, cards, on-screen text, subtitles and
   the mix in one go.

A spec model of `"@image"` / `"@video"` means "use the model from Model settings" (the samples and AI
drafts do this); a concrete model id in the spec takes precedence.

## Running and launch options

```bash
storystudio                            # start the local server and open a native window (falls back to the browser without pywebview)
storystudio --browser                  # use the system browser
storystudio --project-root <data dir>  # keep projects and outputs in a given directory
storystudio --version
```

`python -m studio.desktop` is equivalent to `storystudio`. All options:

| Option | Effect |
|---|---|
| `--version` | Print the version (for example `storystudio 0.1.0`) |
| `--port PORT` | Use a fixed port; without it the first free port in 8756–8799 is used. If the given port is taken, the launcher exits with an error and suggests a free port (on Windows it also names the process holding it) |
| `--browser` | Open in the system browser instead of a native window |
| `--no-open` | Start the server only, without any window; stop with Ctrl+C |
| `--no-token` | Disable the local access token (only for debugging the API) |
| `--project-root DIR` | Data directory; default `~/StoryStudio` for the desktop build and the current directory in development |
| `--no-plugins` | Do not load plugins (for troubleshooting) |

The launcher opens the UI at `http://127.0.0.1:<port>/?token=…`; after that, API calls are allowed through
a cookie. To call the API with curl, send the `X-StoryStudio-Token` header with the token printed at start-up,
or start with `--no-token` (debugging only). The log is `~/.storystudio/logs/storystudio.log`; the desktop
build has no console, so look there first when something goes wrong.

## Model sources

Each of the five roles uses one source, with your own URL and key:

| Role | Built-in sources |
|---|---|
| Text (scripts, drafting, reviews) | Any OpenAI-compatible chat API (OpenAI / OpenRouter / DeepSeek / Tongyi / Doubao / local Ollama, LM Studio and others) |
| Image | OpenRouter (with reference images) · OpenAI-compatible images · Volcengine Ark Seedream · local SD WebUI / Forge · local ComfyUI |
| Voice | Edge TTS (free) · OpenAI-compatible speech · OpenRouter · MiniMax (voice cloning from reference audio) |
| Video | OpenRouter (first frame) · Volcengine Ark Seedance · MiniMax · OpenAI-compatible videos (gateways such as LiteLLM) · local ComfyUI |
| Music | No built-in source: there is no common API shape for music, so you need a plugin that provides the `music` role |

- **Unified gateway**: with an OpenAI-compatible gateway such as LiteLLM, new-api or one-api, enter the
  gateway URL and key once at the top of Model settings, test it, and apply it to the text / image / voice /
  video roles in one click.
- **Local models**: with a capable GPU you can run everything locally. "Scan this machine" lists running
  Ollama / LM Studio / vLLM / llama.cpp / ComfyUI / SD WebUI servers and local OpenAI-compatible speech
  servers; local addresses need no key and cost nothing. The scan only probes these services' default local
  ports (manually entered addresses must be loopback or private-network addresses); it is not a network
  scanner. This path is optional; cloud sources keep working unchanged.
- **Plugins**: sources beyond the built-in ones (self-hosted private APIs, command-line tools that only run
  on your machine, services that are not public yet, and so on) come from plugins. Put a plugin in `~/.storystudio/plugins/<name>/` and it
  loads at start-up, with no change to the core code. If a plugin is missing or fails to load, the app still
  starts and the UI explains which source is unavailable and why. Plugins are ordinary Python code running
  with your user permissions, so only install plugins you trust. To skip plugins temporarily, set
  `STORYSTUDIO_PLUGINS=off` or start with `--no-plugins`. See [docs/plugins.md](docs/plugins.md) (Chinese).
- **Adding a built-in source**: add an adapter under `src/genkit/adapters/` and register it in both
  `settings.ADAPTERS` and `adapters.REGISTRY` (the two tables must match, and a test enforces it); async
  sources also register a task-id prefix in `genkit.adapters.ASYNC_PREFIXES`. See the
  [architecture guide](docs/en/architecture.md).
- **Headless use / CI**: environment variables `STORYSTUDIO_<ROLE>_<FIELD>` override settings for the
  current process and are never written back. `ROLE` is `TEXT`, `IMAGE`, `VOICE`, `VIDEO` or `MUSIC`;
  `FIELD` is `ADAPTER`, `BASE_URL`, `API_KEY`, `MODEL` or `VOICE` (for example
  `STORYSTUDIO_TEXT_ADAPTER=openai_chat`). See [.env.example](.env.example).

Default URLs, fields and all environment variables are listed in the
[configuration guide](docs/en/configuration.md). For the real-account status of each source, see "现状盘点"
(status overview) in [BACKLOG.md](BACKLOG.md): many direct adapters only have fake-client tests, written
from official documentation and SDK signatures, and have not been run against real accounts.

## The storyvideo CLI

The drama production pipeline is also available on the command line (AI drafting, posters, delivery packs
and craft reviews are UI-only for now). An episode id maps to
`content/story_video/<id>.json` in the data directory (the first episode copied with "Start from an example"
in the UI is `DEMO-1`):

```bash
storyvideo preview DEMO-1                    # render each shot's compiled prompt and duration budget; no lock, no cost
storyvideo prepare DEMO-1                    # validate, plan and freeze
storyvideo run DEMO-1 --through frames       # first frames (--no-wait submits without waiting)
storyvideo review DEMO-1 frames --decision pass
storyvideo run DEMO-1 --through videos
storyvideo asr-qc DEMO-1 --record-retakes    # dialogue-recognition gate (optional, needs faster-whisper)
storyvideo review DEMO-1 videos --decision pass
storyvideo run DEMO-1 --through assemble     # assemble the cut locally; the final review gate comes next
storyvideo review DEMO-1 final --decision pass
storyvideo post DEMO-1                       # post layers: narration + cards + on-screen text + mix
storyvideo amend DEMO-1                      # accept a post-freeze edit limited to post-production blocks
storyvideo retry-failed DEMO-1               # reset jobs that failed for transient platform reasons; the next run resubmits them
storyvideo status DEMO-1 --json
```

- `run` never skips a review gate that has not passed: for example, `run --through assemble` before
  `review … videos --decision pass` stops with exit code `4`.
- Only `run` submits paid generation jobs (recorded in the ledger). `post` calls the voice source for
  narration: if a voice source other than Edge TTS is configured it is normally used and billed as usual;
  otherwise the free Edge TTS is used (needs the network). `reconcile` / `abandon` query the provider's task
  history, and the first `asr-qc` run downloads the recognition model. All other commands are local.
- Exit codes: `0` success, `2` input or runtime error, `3` blocked by a safety gate, `4` stopped at a
  review gate that has not passed, `130` interrupted.
- The CLI's default data directory is `STORYSTUDIO_PROJECT_ROOT`, or the code root when it is unset (the
  repository root for a source install). If the UI uses another directory (for example `~/StoryStudio` for
  the desktop build), pass `--project-root <data dir>` to every command.

Crash recovery, double-charge protection, review decisions and revisions are covered in
[STORYVIDEO_RUNTIME.md](STORYVIDEO_RUNTIME.md) (Chinese); the bundled data (shot library, style library,
genre cards and so on) is described in [skills/README.md](skills/README.md) (Chinese).

## Building the desktop app

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build_windows.ps1
```

The script builds the frontend (when npm is on `PATH`) → runs PyInstaller to produce the folder
`dist/StoryStudio/` → checks the bundle contents (`desktop/verify_bundle.py`) → runs Inno Setup to produce
`dist/installer/StoryStudio-<version>-setup.exe` (only if Inno Setup 6 is installed). If you only need the
folder, run `python -m PyInstaller desktop/storystudio.spec --noconfirm`. CI
(`.github/workflows/ci.yml`) builds the Windows package on `v*` tags or manual runs.

- The package only contains what the app needs at run time: the code under `src/`, the built UI, bundled
  skill data (shot library, style library, style cards, review checklists and so on), the sample projects,
  `LICENSE` and the third-party notices (`docs/third-party-notices.md`). The whitelist is
  `desktop/bundle_manifest.py`. `tests/`, the `frontend/` sources and plugins are never bundled (plugins
  live in the user directory and load from disk at start-up).
- `ffmpeg.exe` / `ffprobe.exe` are not bundled by default: put them into `dist/StoryStudio/` or its
  `ffmpeg/` subfolder, or install them on the system `PATH`. To ship them in the installer, place them in
  `desktop/ffmpeg/` before building (mind ffmpeg's own licence).
- The desktop build keeps user data in `~/StoryStudio` and settings in `~/.storystudio`; uninstalling
  never touches either.
- Sign the binaries yourself before distributing them. The macOS script `desktop/build_macos.sh` exists but
  has never been run on a Mac.

Details (optional dialogue-recognition install, headless mode, signing) are in
[desktop/README.md](desktop/README.md) (Chinese) and the [installation guide](docs/en/installation.md).

## Development

```bash
pip install -e ".[desktop,dev]"
pytest -q
python scripts/refresh_prompt_canary.py      # check golden prompt output after changing the prompt compilers
```

The UI lives in `frontend/` (Vite + Vue 3) and its build output is committed to `src/webui/static/app/`.
After changing the UI, rebuild and commit the output (CI checks that the two match):

```bash
cd frontend
npm ci
npm test          # Vitest
npm run build
```

Hot reload: start the backend with `storystudio --no-open --no-token --port 8765`, then run `npm run dev` in
`frontend/` and open <http://localhost:5173/app/> (`/api` is proxied to port 8765). The old single-page UI is
still served at `/legacy`.

UI language: switch between Chinese and English at the bottom of the sidebar. English strings live in
`frontend/src/locales/`, backend text rules in `src/webui/i18n_rules.py`; a missing translation shows the
Chinese original.

Repository layout:

| Path | Contents |
|---|---|
| `src/genkit` | Model contracts, settings, adapters, routing, ledger, plugin loading, version |
| `src/storyvideo` | Short drama: spec, prompt compilation, runtime, reviews, dialogue recognition, assembly, post layers |
| `src/webui` | FastAPI server and the per-module APIs |
| `src/studio` | Desktop launcher |
| `src/podcast` / `src/script` / `src/picturebook` / `src/imageset` / `src/clip` / `src/music` | The other modules |
| `src/imagecraft` | Image style cards and image prompt dialects (shared by image sets, picture books, single clips and posters) |
| `frontend/` | UI sources |
| `skills/` | Bundled data: shot library, style library, genre cards, style cards, review checklists and writing rules |
| `examples/` | Sample projects shipped with the app |
| `tests/` | Backend tests (not bundled) |
| `desktop/` | Packaging scripts, PyInstaller spec, installer script, bundle checks |

The version number lives only in `src/genkit/version.py`; see [CHANGELOG.md](CHANGELOG.md) (Chinese) for
changes. Before sending code, read the [contributing guide](CONTRIBUTING.md) and the repository rules in
[AGENTS.md](AGENTS.md).

## Documentation

The bilingual index of all documents is [docs/README.md](docs/README.md).

| Document | Contents |
|---|---|
| [Installation](docs/en/installation.md) | Requirements, source install, desktop builds, ffmpeg and dialogue recognition, upgrading and uninstalling |
| [Architecture](docs/en/architecture.md) | Layers, modules, data flow, ledger and tasks |
| [Design](docs/en/design.md) | Design principles and key trade-offs |
| [Configuration](docs/en/configuration.md) | Model roles and sources, settings file, environment variables, directory layout |
| [FAQ](docs/en/faq.md) | Common questions about usage and troubleshooting |
| [STORYVIDEO_RUNTIME.md](STORYVIDEO_RUNTIME.md) | Short-drama CLI and runtime manual (Chinese) |
| [docs/plugins.md](docs/plugins.md) | Writing plugins (API v1, experimental; Chinese) |
| Module specs in `docs/` (indexed in [docs/README.md](docs/README.md)) | Per-module contracts: single clip, image sets, image studio, picture book, music, podcast, script lab, local models, plus image style cards and long single-shot prompt patterns (Chinese) |
| [desktop/README.md](desktop/README.md) | Packaging details (Chinese) |
| [DESIGN.md](DESIGN.md) / [ARCHITECTURE.md](ARCHITECTURE.md) | Full design log and architecture notes (Chinese) |
| [CHANGELOG.md](CHANGELOG.md) / [BACKLOG.md](BACKLOG.md) | Change log; backlog and status overview (Chinese) |

## Project status and known limitations

v0.1.0 is the first tag. This is what can honestly be said about it:

**Verified**

- The podcast pipeline has been run end to end with a real text model (via an OpenAI-compatible API) and
  edge-tts voices: create, draft, review, voice, mix and export.
- A PyInstaller package has been built on Windows x64; the packaged app opens its window and the headless
  mode serves the API.
- Edge TTS synthesis (Microsoft's online service) has been run successfully; drama assembly and
  post-production have been run with real ffmpeg (in tests).

**Not verified, or limited**

- A full-length short drama has not yet been generated and post-produced end to end with real models.
- Most direct sources (OpenRouter, Volcengine Ark, MiniMax, OpenAI images / speech / videos) only have
  fake-client tests written from official documentation and SDK signatures; voice cloning has never run for
  real; local models have not been run on real GPUs.
- The prompt dialects (such as `gpt_image`, `nano_banana`, `minimax-h3`, `seedance`) have not been compared
  against real models.
- faster-whisper and post-production inside the packaged app have not been verified on real videos.
- The macOS packaging script has never run on a Mac; there is no Linux package and pywebview on Linux is
  untested; Python 3.10 and 3.12+ are untested.
- CI is configured for Linux / Windows tests and Windows packaging but, as of the v0.1.0 release (2026-09-24), had not run on GitHub yet; the Inno
  Setup step has never run locally; installers and executables are unsigned.
- The music role has no built-in source, so the Music module's "generate" needs a plugin; uploading your own
  audio as music in other modules works regardless.
- Plugin API v1 is experimental and may change before 1.0.
- Built-in sources cannot look up task history, so a drama job whose submission result is unknown (for
  example a network timeout mid-submission) cannot be reconciled or abandoned in place. Check the provider's
  console and open a new revision instead (see [STORYVIDEO_RUNTIME.md](STORYVIDEO_RUNTIME.md) §6).

**Tests:** over 3,000 backend pytest cases, more than 50 Vitest test files for the frontend, and a prompt
canary guarding the output of the prompt compilers. The full run before release (2026-09-24, Windows +
Python 3.11): 3,214 pytest and 387 Vitest tests, all passing. As of the release, CI had not run on GitHub yet.

Two self-reviews were done before release: a release audit in which 86 of 87 findings were confirmed and
fixed ([docs/release-audit-2026-09-22.md](docs/release-audit-2026-09-22.md)), and a security review whose
19 findings were all fixed with regression tests
([docs/security-review-2026-09-23.md](docs/security-review-2026-09-23.md)); both are in Chinese. Item-by-item
verified / unverified notes are in [CHANGELOG.md](CHANGELOG.md) and [BACKLOG.md](BACKLOG.md).

## Contributing

Issues and pull requests are welcome. Before you start, please read:

- The [contributing guide](CONTRIBUTING.md) ([中文](CONTRIBUTING.zh-CN.md)): development setup, tests, the
  prompt canary, frontend builds and commit rules.
- The [security policy](SECURITY.md): report vulnerabilities privately through the repository's GitHub
  private vulnerability reporting (Security Advisories), not in a public issue.
- The [code of conduct](CODE_OF_CONDUCT.md): Contributor Covenant 2.1.

## Acknowledgements

The drama module's genre cards, form cards, continuity locks, reference-image roles, audience visibility,
delivery surfaces, retake handling and similar criteria draw on
[zenstory-ai/drama-skills](https://github.com/zenstory-ai/drama-skills) (MIT); the comparison and the choices made are in [docs/drama-skills-gap.md](docs/drama-skills-gap.md)
(Chinese). The project mainly adopts its criteria, not its syntax. Most fields of the genre cards are
excerpts of the original cards' sentences (abridged and condensed), with confidence and basis copied
verbatim; some original sentences also appear in the drafting and video prompts, static checks, UI text,
test fixtures and design documents (itemised in the notices). Its MIT licence and copyright notice are in
[docs/third-party-notices.md](docs/third-party-notices.md), which also ships with the desktop package.

The ideas behind the drama rules for seams (entry / exit state), performance and eyelines, and shot design
come from the video-director guidelines of [waoowaoo](https://github.com/waooAI/waoowaoo) (Elastic License
2.0), and the prompt canary borrows its prompt-canary approach. None of its code, prompts or text is used, so
it is credited here only.

Where each bundled data file comes from, and which ones are excerpts versus written for this project, is
listed under "来源与许可" (sources and licences) in [skills/README.md](skills/README.md).

This project also builds on many open-source projects, including FastAPI, Uvicorn, Pydantic, Vue, Vite,
pywebview, PyInstaller, Pillow, edge-tts, faster-whisper and FFmpeg (FFmpeg is not bundled).

## License

[MIT](LICENSE), Copyright © 2026 avioralon. Third-party content shipped with the app and its licence notices
are listed in [docs/third-party-notices.md](docs/third-party-notices.md).
