# Installation and deployment

English | [中文](../zh/installation.md)

This guide covers installing and running Content Cook, setting up a development environment,
building the Windows package yourself, and what to keep in mind for deployment, backups, upgrades,
uninstalling and troubleshooting. For connecting models, see the [configuration guide](configuration.md).

> **About the names**: the product is called "Content Cook · 内容创作工作台" (the English UI shows it as
> "Content Cook · Content creation workbench"). The name lives in one place, `src/webui/branding.py`, and
> "Content Cook" is still a working name. The Python package `storystudio`, the commands `storystudio` / `storyvideo`, the
> settings folder `~/.storystudio` and the desktop program `StoryStudio.exe` are technical identifiers and
> deliberately do not follow the product name. When this guide says StoryStudio, it means this program.

## Contents

1. [Choose an installation option](#1-choose-an-installation-option)
2. [Requirements](#2-requirements)
3. [Option A: Windows installer](#3-option-a-windows-installer)
4. [Option B: Run from source](#4-option-b-run-from-source)
5. [Option C: Development setup](#5-option-c-development-setup)
6. [Building the Windows package](#6-building-the-windows-package)
7. [macOS packaging](#7-macos-packaging)
8. [Deployment notes](#8-deployment-notes)
9. [Configuring models](#9-configuring-models)
10. [Installing plugins](#10-installing-plugins)
11. [Upgrading](#11-upgrading)
12. [Uninstalling](#12-uninstalling)
13. [Troubleshooting](#13-troubleshooting)
14. [What has been verified](#14-what-has-been-verified)

## 1. Choose an installation option

| Option | Who it is for | Platform | What you need |
|---|---|---|---|
| A. Windows installer | You just want to use it, without the command line | Windows x64 | The installer (download it from Releases if one is published, otherwise build it, see §6) and ffmpeg |
| B. Run from source | You want the latest code, the `storyvideo` CLI, or to run on macOS / Linux | Windows / macOS / Linux (the last two with limited verification, see §2) | Git, Python 3.10+, ffmpeg |
| C. Development setup | You want to change the code or the UI and run the tests | Same as B | Same as B, plus Node.js 22 if you change the UI |

All three run the same program: a local server that listens only on `127.0.0.1`, plus a native window
(or your browser) showing the UI. It is a single-user desktop application with no accounts, and it is not
meant to be hosted on a server for other people (see §8.1).

## 2. Requirements

| Item | Requirement | Notes |
|---|---|---|
| Operating system | Windows x64 | Windows is the only platform where the package has actually been built and run. Linux: CI is set up to run the tests on Ubuntu, but as of v0.1.0 the workflow had not run on GitHub; the native window is untested and there is no Linux package. macOS: the packaging script exists but has never been run on a Mac, and running from source has not been tested there either. |
| Python | 3.10 or newer | `pyproject.toml` declares `>=3.10`, but only 3.11 has been tested, locally (CI is configured for 3.11 but has not run on GitHub yet), so 3.11 is recommended. Needed for options B and C and for building the package; the installer ships its own Python. |
| ffmpeg / ffprobe | On `PATH` | Used for assembling the final cut, post-production, rendering picture books and podcasts, and the dialogue ASR check. The drama module also checks every downloaded first frame and video with ffprobe (and uses ffmpeg for the review contact sheets), so install them before you generate anything there. The image studio, single-clip and similar modules do not depend on them. The UI shows a banner when they are missing (in v0.1.0 the banner says frame and video generation still work, which is not accurate for the drama module). |
| CJK font | At least one installed | Needed for burned-in subtitles, cards and handwriting-style text. The fonts that ship with Windows and macOS are normally enough; for Linux see §4.2. |
| Node.js | 22 (only to change the UI) | The built UI is committed in `src/webui/static/app/`, so running the program does not need Node. |
| faster-whisper | Optional | Used by the dialogue ASR check. The model is downloaded from Hugging Face the first time it runs (the UI says about 500 MB for the default model). |
| Network | For cloud models | What is sent where is described in the [configuration guide](configuration.md). Narration without a configured voice uses Edge TTS (an online Microsoft service), which also needs a connection. |
| GPU | Only for local models | Hardware needs are set by the local tools themselves (Ollama, ComfyUI and so on). The local-model path has not been verified on real hardware. |

## 3. Option A: Windows installer

### 3.1 Getting the installer

- If the repository's [Releases page](https://github.com/aviorAlon/content-cook/releases) has a
  `StoryStudio-<version>-setup.exe`, download it.
- There is no automated release process yet. CI only uploads the Windows package as a build artifact
  (named `StoryStudio-windows`) of a workflow run started by a `v*` tag or by hand. Downloading an Actions
  artifact requires a GitHub sign-in, and artifacts are deleted after the retention period (90 days by default).
  If Releases has nothing,
  build the package yourself as described in [§6](#6-building-the-windows-package).
- Neither the installer nor the exe is **code-signed**. Windows SmartScreen may show "Windows protected your PC"
  and antivirus software may block it. Only run packages from this repository's Releases page or ones you built
  yourself; once you trust the source, choose "More info → Run anyway".

### 3.2 Installing

The setup wizard (Inno Setup 6):

- offers Simplified Chinese and English as setup languages;
- asks whether to install for all users (needs administrator rights) or only for you;
- installs to `Program Files\StoryStudio` by default, or, for a per-user install, to your own programs folder
  (usually `%LOCALAPPDATA%\Programs\StoryStudio`);
- creates Start-menu shortcuts and an uninstall entry, with an optional desktop shortcut;
- can start the app when it finishes.

Some texts inside the installer, such as the shortcut names and the desktop-shortcut option, are in Chinese only.
The program folder holds only the program; your projects, settings and keys live elsewhere (see §3.4).

**ffmpeg**: the installer does not include ffmpeg unless whoever built it chose to add it (see §6.4).
Either:

- put `ffmpeg.exe` and `ffprobe.exe` into the install folder or its `ffmpeg\` subfolder (`ffmpeg\bin\` also
  works); the launcher puts these folders at the front of `PATH`; or
- install ffmpeg on your system `PATH`.

Restart the app afterwards. The banner at the top of the UI links to <https://ffmpeg.org/download.html>.

### 3.3 First launch

Open "Content Cook · 内容创作工作台" from the Start menu or the desktop. The program then:

1. starts a local server on `127.0.0.1`, on the first free port starting at 8756 and then 8757–8799;
2. creates a random access token that is valid only for this run;
3. opens a native window (1440×920, using WebView2 on Windows). The window carries the token for you.

The packaged app has no console window. When something goes wrong, check the log first:
`%USERPROFILE%\.storystudio\logs\storystudio.log`.

The first time, the home page shows a "First time here" guide: **Set up models → Pick a module to start → You
are asked before anything costs money**. The drama module's home page, while it has no projects, shows "Your
first frame in three steps": Set up models → Get a project → Generate first frame.

1. "Model settings" in the sidebar: pick a source for each role you need, enter the address and key, save, then
   click "Test connection". See the [configuration guide](configuration.md).
2. To practise on a sample: "Copy the sample episode (free)" on the drama home page, or "Start from an example"
   on the drama new-project page, copies a bundled sample into your projects at no cost. Most other modules have
   a "Copy the sample (free)" button on their home pages.
3. According to the home-page guide, before each run the UI tells you how many requests it will send (local sources
   are marked as free), and nothing goes out until you confirm.

### 3.4 Where your data lives

| Location | Contents |
|---|---|
| `~/StoryStudio` (that is, `%USERPROFILE%\StoryStudio`) | The **data folder**: project files of every module, uploaded assets, generated output, the drama ledger and the trash. On first launch the app creates `content/`, `content/story_video/` and `output/`. |
| `~/.storystudio` | Model settings and keys (`settings.json`), logs, cache, voice library, model catalogue cache, plugins and optional components (`site-packages/`). |

To use a different data folder, in order of precedence:

1. the `--project-root` option, for example appended to a shortcut's target:
   `--project-root "D:\StoryStudioData"`;
2. the user environment variable `STORYSTUDIO_PROJECT_ROOT`.

Without either, the default `~/StoryStudio` is used. The settings folder can be moved with the environment
variable `STORYSTUDIO_HOME`, and `settings.json` itself can be pointed elsewhere with `STORYSTUDIO_SETTINGS`.
The full layout and backup advice are in §8.5.

### 3.5 Optional: dialogue ASR (faster-whisper)

To keep the package small, faster-whisper is not included (nor ctranslate2, numpy, onnxruntime and similar).
On the "Model settings" page, "Install faster-whisper" does the following:

1. looks for a local Python **with the same minor version as the packaged app** and with pip installed.
   On Windows it tries `py -3.X`, `python3.X`, `python3` and `python`, in that order;
2. runs `pip install --target ~/.storystudio/site-packages faster-whisper`;
3. makes it available right away; on later starts the launcher adds that folder automatically.

Keep in mind:

- The packaged app embeds the Python version it was built with (CI uses 3.11), so you need a matching Python
  with pip on the machine. The manual command shown on the settings page uses the Python it found.
- Packages installed with a different Python version are detected, and the settings page explains why they are
  ignored.
- The folder can be changed with the environment variable `STORYSTUDIO_EXTRA_SITE`.
- The first check downloads a model from Hugging Face (`tiny` / `base` / `small` / `medium` / `large-v3`;
  default `small`, run on the CPU as int8). It is cached in the Hugging Face cache: by default
  `~/.cache/huggingface/hub`, or wherever `HF_HUB_CACHE` / `HF_HOME` point.

## 4. Option B: Run from source

### 4.1 Clone and install

```bash
git clone https://github.com/aviorAlon/content-cook.git
cd content-cook
python -m venv .venv
```

Activate the virtual environment:

```powershell
.venv\Scripts\Activate.ps1      # Windows PowerShell
```

```bat
REM Windows cmd
.venv\Scripts\activate.bat
```

```bash
source .venv/bin/activate       # macOS / Linux
```

- The commands below assume the environment is active. If you do not activate it, replace `python` / `pip` with
  `.venv\Scripts\python -m pip` (macOS / Linux: `.venv/bin/python -m pip`); otherwise they install into your
  system Python.
- If PowerShell refuses to run `Activate.ps1` because running scripts is disabled, use
  `.venv\Scripts\activate.bat` from cmd instead.

Install:

```bash
python -m pip install --upgrade pip
pip install -e ".[desktop]"     # the app + pywebview (native window) + PyInstaller (packaging)
pip install -e ".[asr]"         # optional: faster-whisper for the dialogue ASR check
```

Things to know:

- **Only an editable install from the clone (`pip install -e`) is supported.** At run time the app reads the
  repository's `skills/` folder (camera library, style library, style cards, review checklists, ComfyUI
  workflows) and `examples/` (sample projects). These are not in the wheel or sdist, so `pip install .` or
  installing a wheel is not supported. The project is not published on PyPI.
- All dependencies are listed in `pyproject.toml`. Runtime: requests, fastapi, uvicorn, pydantic, openai,
  openrouter, volcengine-python-sdk, edge-tts, Pillow, openpyxl. Extras: `desktop` (pywebview, PyInstaller),
  `asr` (faster-whisper), `dev` (pytest).
- The native window (pywebview) has not been tested on Linux. You can install just `pip install -e .` and start
  with `--browser` to use your system browser.

### 4.2 Install ffmpeg and a CJK font

| System | ffmpeg | CJK font |
|---|---|---|
| Windows | Download from <https://ffmpeg.org/download.html> and add its `bin` folder to `PATH`, or `choco install ffmpeg` (what CI uses) | The built-in Microsoft YaHei / SimHei / SimSun are fine |
| macOS | `brew install ffmpeg` (needs Homebrew) | The built-in PingFang / STHeiti are fine |
| Debian / Ubuntu | `sudo apt-get install ffmpeg` | `sudo apt-get install fonts-noto-cjk` (what CI uses), or WenQuanYi Micro Hei |

Check that both commands work:

```bash
ffmpeg -version
ffprobe -version
```

- The app finds both commands through `PATH`; restart the app after changing `PATH`.
- For each purpose (UI text, subtitles, handwriting-style text) the app picks the first font that exists from a
  fixed list of paths, for example `C:/Windows/Fonts/msyh.ttc` on Windows, `/System/Library/Fonts/PingFang.ttc`
  on macOS and `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc` on Debian / Ubuntu. Other Linux
  distributions that keep fonts elsewhere may not be detected.
- If nothing is found, `fonts.ok` in `/api/health` is `false` and the post-production panel warns you in
  advance. You can also point a project at a font file: `post_graphics.fonts` for drama, the subtitle
  `font_path` setting for picture books.

### 4.3 Start the app

```bash
storystudio --project-root ~/StoryStudio
```

- A native window opens by default. Without pywebview the launcher prints a hint and opens your system browser.
- **When running from source, the data folder defaults to the current directory** (only the packaged app
  defaults to `~/StoryStudio`). If you run `storystudio` in the repository root, projects and output are written
  into the repository (`content/`, `output/` and friends are in `.gitignore`). Using `--project-root` with a fixed
  folder is recommended.
- The `storystudio` command lives in the virtual environment's `Scripts` / `bin` folder. Without activating the
  environment, run that environment's `python -m studio.desktop` instead (same options).

On success the terminal prints something like this (the token changes on every start; the labels are Chinese:
项目目录 = data folder, 日志 = log file):

```text
Content Cook · 内容创作工作台 v0.1.0: http://127.0.0.1:8756/?token=<random token>
  项目目录: <data folder>
  日志: <settings folder>/logs/storystudio.log
```

With the native window, closing the window quits the app. With `--browser` or `--no-open` the server keeps
running until you press Ctrl+C in the terminal.

### 4.4 Launcher options

| Option | What it does |
|---|---|
| `--version` | Prints the version (for example `storystudio 0.1.0`) and exits |
| `--port PORT` | Uses this port. Without it, the first free port in 8756–8799 is used. If the given port is taken, the launcher says who holds it and suggests a free one, then exits with code 2 |
| `--browser` | Opens your default browser instead of a native window |
| `--no-open` | Starts only the server, without any window (the printed address contains the token) |
| `--no-token` | Turns off the local access token. **For debugging the API only**, see §8.2 |
| `--project-root DIR` | The data folder. Precedence: this option > `STORYSTUDIO_PROJECT_ROOT` > `~/StoryStudio` (packaged) / current directory (source) |
| `--no-plugins` | Loads no plugins (same as `STORYSTUDIO_PLUGINS=off`); useful for troubleshooting |

Apart from `-h` / `--help`, there are no other options. If the server does not come up (another program
answers on the port, the server thread fails, or it is not ready within 30 seconds), the launcher prints the
reason and exits with code 1.
The launcher's messages are currently in Chinese only.

### 4.5 Token, browser and curl

- The launcher opens `http://127.0.0.1:<port>/?token=<token>`. That request sets the cookie
  `storystudio_token_<port>` (HttpOnly, SameSite=Strict), which authorises the UI's later requests.
- Every `/api/*` request needs that cookie or the header `X-StoryStudio-Token: <token>`; otherwise it gets 401.
- A new token is created on every start, so after a restart use the newly printed address.
- Calling the API with curl (in Windows PowerShell 5.1, `curl` is an alias of `Invoke-WebRequest`; type
  `curl.exe` instead):

```bash
curl -H "X-StoryStudio-Token: <token>" http://127.0.0.1:8756/api/health
```

- Requests whose `Host` is not `127.0.0.1`, `localhost` or `::1`, and `/api` requests that the browser marks as
  coming from another site, are rejected with 403 (protection against DNS rebinding and cross-site requests).
  Opening the app through a LAN IP or another host name therefore does not work, by design.

### 4.6 The storyvideo CLI

Every production step of the drama module is also available on the command line. The CLI exists only in source
installs; the Windows package contains just the graphical app. For example:

```bash
storyvideo preview DEMO-1 --project-root "$HOME/StoryStudio"        # compile prompts and estimate duration; no lock, no cost
storyvideo status DEMO-1 --project-root "$HOME/StoryStudio" --json  # read-only status
```

- `storyvideo` does not expand `~` itself (the `storystudio` launcher does). Windows PowerShell 5.1 and cmd pass
  `~` through literally, so the path would point at a folder named `~` inside the current directory. That is why
  the examples use `"$HOME/StoryStudio"`, which works in bash, zsh and PowerShell; in cmd use
  `%USERPROFILE%\StoryStudio`.
- The episode id `DEMO-1` maps to `<data folder>/content/story_video/DEMO-1.json`. The first project copied with
  "Start from an example" in the UI is `DEMO-1`.
- The CLI's default data folder is `STORYSTUDIO_PROJECT_ROOT`, or, if unset, **the code root (the repository
  root)**, which differs from the launcher's default. If the UI uses another folder, pass the same
  `--project-root` to every command.
- `run` is the only command that submits paid generation jobs; `post` calls the voice source for narration (a paid
  voice source is billed; Edge TTS needs a connection). Exit codes: 0 success; 2 input or runtime error; 3 blocked
  by a safety gate; 4 stopped at a review gate that has not passed yet; 130 interrupted.
- All subcommands and the full workflow are documented (in Chinese) in
  [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md).

## 5. Option C: Development setup

### 5.1 Install

```bash
git clone https://github.com/aviorAlon/content-cook.git
cd content-cook
python -m venv .venv            # activate it as in §4.1
pip install -e ".[desktop,dev]"   # the same set CI installs
```

Changing the UI also needs Node.js. CI uses Node 22, and the locked dev dependencies set minimum versions
(Vite 8: `^20.19.0 || >=22.12.0`; Vitest 5: `^22.12.0 || ^24.0.0 || >=26.0.0`;
jsdom 30: `^22.22.2 || ^24.15.0 || >=26.0.0`), so use a recent Node 22 LTS (22.22.2 or later).

### 5.2 Backend plus hot-reloading UI

Terminal 1, in the repository root:

```bash
storystudio --no-open --no-token --port 8765
```

Terminal 2:

```bash
cd frontend
npm ci
npm run dev
```

Then open <http://localhost:5173/app/>. Vite's `base` is `/app/`, and Vite proxies `/api` to
`http://127.0.0.1:8765`.

- The backend must run on port 8765 with `--no-token`: the proxy target is hard-coded in `frontend/vite.config.js`,
  and with the token on, the backend requires the request's Origin to match its own address exactly, so write
  requests from the Vite page (`http://localhost:5173`) get 403. Without a token, the backend accepts requests
  from any loopback origin, which is what lets the Vite page work. Use this combination for development only (§8.2).
- Started in the repository root, the backend uses the repository root as its data folder; add `--project-root`
  to keep data elsewhere.
- In development the FastAPI docs are available at <http://127.0.0.1:8765/docs> (when the backend is started
  without `--no-token`, they need the token just like `/api`; the packaged app does not serve them).

### 5.3 Tests

```bash
pytest -q                                   # backend tests (testpaths = tests, pythonpath = src)
python scripts/refresh_prompt_canary.py     # prompt canary
```

```bash
cd frontend
npm test                                    # Vitest (vitest run)
npm run build                               # builds into src/webui/static/app/
```

- There are about 3,200 backend tests (3,214 collected at v0.1.0). `tests/conftest.py` switches plugin loading
  off (`STORYSTUDIO_PLUGINS=off`), so plugins installed on your machine do not leak in. Media tests that need
  ffmpeg / ffprobe or a CJK font are skipped on machines without them.
- The canary compiles `tests/canary/mini_spec.json` (plus every form card in Chinese and English) and compares
  the result with `tests/canary/expected_prompts.json`, exiting non-zero on any difference. Run it before and
  after changing the prompt compiler, the camera library or the style library. Only use `--update` once you have
  confirmed the change is intended, and explain why in the commit message.
- After changing UI sources, run `npm run build` again and commit `src/webui/static/app/` as well: CI rebuilds it
  and fails if it does not match the sources.
- Other contribution rules: [CONTRIBUTING.md](../../CONTRIBUTING.md) and [AGENTS.md](../../AGENTS.md).

### 5.4 CI

`.github/workflows/ci.yml` (workflow name `ci`) runs on pushes to `main`, `v*` tags, pull requests and manual
runs:

| Job | What it does |
|---|---|
| `pytest (ubuntu-latest)` / `pytest (windows-latest)` | Python 3.11; installs ffmpeg (plus `fonts-noto-cjk` on Linux); `pip install -e ".[desktop,dev]"`; `pytest -q`; prompt canary |
| `frontend build + vitest` | Node 22; `npm ci`, `npm test`, `npm run build`; checks that the built files are committed |
| `Windows 包` (Windows package) | Only for `v*` tags or manual runs, see §6.5 |

The workflow is written but, as of v0.1.0, has not yet run on GitHub.

## 6. Building the Windows package

### 6.1 Prerequisites

- Windows x64 and Python (CI uses 3.11). The package embeds the Python version you build with, and that version
  also decides which Python the in-app faster-whisper install needs (§3.5).
- In the repository root, with the virtual environment active: `pip install -e ".[desktop]"`. The build script
  calls whatever `python` is on `PATH`.
- Optional: Node.js. If npm is on `PATH`, the script rebuilds the UI first; otherwise it uses the committed build.
- Optional: Inno Setup 6.3 or later (`installer.iss` uses `x64compatible`, which was added in 6.3), for example
  `choco install innosetup`. Without it you get no setup.exe.
- Optional: to ship ffmpeg inside the installer, put `ffmpeg.exe` / `ffprobe.exe` into `desktop/ffmpeg/`
  (ignored by git). Mind ffmpeg's own licence, which depends on whether your build is LGPL or GPL.

### 6.2 One-command build

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build_windows.ps1
```

What the script does:

| Step | Details |
|---|---|
| 1. Read the version | From `src/genkit/version.py` |
| 2. Build the UI | If npm is on `PATH`: `npm ci` when `node_modules` is missing, then `npm run build`; skipped without npm |
| 3. PyInstaller | `python -m PyInstaller desktop/storystudio.spec --noconfirm --workpath <system temp>/storystudio-pyi-build`; fails if `dist/StoryStudio/StoryStudio.exe` is missing |
| 4. Bundle self-check | `python desktop/verify_bundle.py dist/StoryStudio --ffmpeg desktop/ffmpeg`; stops without an installer if it fails |
| 5. Inno Setup | If `ISCC.exe` is found in `C:\Program Files (x86)\Inno Setup 6\` or `C:\Program Files\Inno Setup 6\`, compiles `desktop/installer.iss`; otherwise only the folder is produced |

Output:

| Path | Description |
|---|---|
| `dist/StoryStudio/` | One-folder app: `StoryStudio.exe` plus `_internal/`; copy the whole folder to use it. Around 80 MB (builds so far measured 76–87 MB), depending on dependency versions |
| `dist/installer/StoryStudio-<version>-setup.exe` | The installer (only with Inno Setup) |

If you only need the folder, you can also run:

```powershell
python -m PyInstaller desktop/storystudio.spec --noconfirm
```

PyInstaller then leaves its intermediate files in the repository's `build/` folder (ignored by git). Running
`python desktop/verify_bundle.py dist/StoryStudio` afterwards is recommended.

### 6.3 What goes into the package

`desktop/storystudio.spec` builds a one-folder, windowed (`console=False`) app without UPX; the entry point is
`src/studio/desktop.py`.

- Large optional pieces are excluded: faster-whisper, ctranslate2, onnxruntime, numpy, torch and others
  (install ASR separately as in §3.5). tkinter and test frameworks are left out too.
- From `skills/` and `examples/`, only the files on the whitelist in `desktop/bundle_manifest.py` are included:
  camera library, style library, style cards, review checklists, three built-in ComfyUI workflows and nine sample
  projects, plus the licence files `LICENSE` and `docs/third-party-notices.md`.
- Plugins, tests and the `frontend/` sources are never bundled.

`desktop/verify_bundle.py` checks the output after the build and exits with code 1 if it finds any of:

- test code or test frameworks;
- data files outside the whitelist, or whitelisted files (including licence files) that are missing;
- local data: `.env`, `settings.json`, `*.db` / `*.sqlite` / `*.sqlite3`, `*.log`, `logs/`, `content/`,
  `output/` and similar;
- plugins;
- absolute paths from the build machine (the repository folder, the home folder and so on);
- optional private words, read from the `BUNDLE_DENYLIST` environment variable or an untracked
  `.release-denylist` file in the repository root; hits are reported by index only.

With `--ffmpeg desktop/ffmpeg` it also checks that the folder contains only ffmpeg / ffprobe, their libraries and
licence notes.

### 6.4 The installer (Inno Setup)

Key points of `desktop/installer.iss`:

- the app name defaults to the product name and the publisher is `avioralon`; the AppId is fixed, so a newer
  installer upgrades an older installation in place;
- the default folder is `{autopf}\StoryStudio`, and the wizard allows a per-user install; installs as a 64-bit
  app;
- setup languages: Simplified Chinese and English;
- Start-menu shortcut and uninstall entry, optional desktop shortcut, option to launch after setup;
- if `desktop/ffmpeg/` has files, they are installed into `{app}\ffmpeg`, which the app adds to `PATH` at start;
- uninstalling removes only program files (plus `{app}\_internal\__pycache__`) and never touches
  `~/StoryStudio` or `~/.storystudio`.

To compile by hand (the version defaults to `0.1.0` when not passed):

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "/DAppVersion=0.1.0" desktop\installer.iss
```

> **Not verified**: the Inno Setup step is only configured in CI and has never been run locally. The script uses
> `compiler:Languages\ChineseSimplified.isl`; if your Inno Setup installation lacks that language file, compiling
> fails, and you need to add the file or remove that line from `installer.iss`.

### 6.5 The Windows package in CI

For `v*` tags and manual runs, the `Windows 包` job runs after both test jobs pass:

1. `pip install -e ".[desktop]"` and the PyInstaller build;
2. two windowless smoke tests: in `--no-token` mode, `/api/health` on port 8791 must return 200; in the default
   token mode on port 8792, the process must still be running and a request without the token must get 401;
3. the bundle self-check (`BUNDLE_DENYLIST` comes from a repository secret);
4. `choco install innosetup` and compiling the installer;
5. uploading the build artifact `StoryStudio-windows` (`dist/StoryStudio` and `dist/installer/*.exe`).

It does not publish a release.

### 6.6 Signing

The exe and the installer are currently unsigned. If you distribute them, sign them with your own code-signing
certificate (for example with signtool); unsigned files are more likely to be blocked by SmartScreen and
antivirus software.

## 7. macOS packaging

> **Not verified**: `desktop/build_macos.sh` is written but has never been run on a Mac.

The script only runs on macOS. Prerequisite: the script calls `python3`, so first run
`python3 -m pip install -e ".[desktop]"` in the repository root, in the environment that provides `python3`.

```bash
bash desktop/build_macos.sh
```

Steps:

1. builds the UI first if npm is on `PATH`;
2. PyInstaller creates `dist/StoryStudio.app` (bundle id `ai.avioralon.desktop`, minimum macOS 12.0), with
   intermediate files in `${TMPDIR:-/tmp}/storystudio-pyi-build`;
3. bundle self-check;
4. signs with `desktop/entitlements.plist` if `CODESIGN_IDENTITY` is set;
5. creates `dist/installer/StoryStudio-<version>.dmg` with `hdiutil`;
6. notarises and staples if `NOTARY_PROFILE` is set (a profile saved with `xcrun notarytool store-credentials`).

- Unsigned apps are blocked by Gatekeeper until the user allows them.
- [desktop/README.md](../../desktop/README.md) recommends building separately for Apple Silicon and Intel.
- pywebview uses WKWebView on macOS; that combination has not been verified either.

## 8. Deployment notes

### 8.1 A single-user local application

- The server is always bound to `127.0.0.1`; there is no option to change the listen address.
- There are no accounts and no permission model. Anyone who can call the API can spend money on your model
  accounts, read and write your projects, and change your model settings.
- So **do not** expose it on a network: no port forwarding, reverse proxies or tunnels, and no shared server for
  several people. Requests whose `Host` is not a loopback address are rejected (403); remote and LAN access are
  not supported.
- The token and the Host / Origin checks protect against other web pages and processes on the same computer.
  They are not designed to defend against attacks from the network. See [SECURITY.md](../../SECURITY.md) and
  the [architecture guide](architecture.md).

### 8.2 --no-token is for debugging only

- Without the token, any program on the same computer can call the API, spend money and read or write projects.
  The launcher prints a prominent warning when you use it.
- The Host / Origin checks stay on, but the origin check is relaxed to "any loopback address" (so the Vite dev
  server works).
- Use it only while debugging the API or developing the UI, and stop the server when you are done. For everyday
  curl calls, send the `X-StoryStudio-Token` header instead (§4.5).

The packaged app has no console, so you cannot see the tokenized address it prints. If you just want the UI in
a browser, `--browser` opens your system browser with the token, so there is no need to turn the token off. Only
for windowless troubleshooting (`--no-open`) with the packaged app do you need `--no-token`:

```powershell
& "<install folder>\StoryStudio.exe" --no-open --no-token --port 8756
```

- In `--no-open` mode the packaged app has neither a window nor a console. Stop it from Task Manager
  (`StoryStudio.exe`) or with `Stop-Process -Name StoryStudio`.
- In the packaged app the no-token warning goes only to the log (§3.3); nothing appears on screen.

### 8.3 Data folders

- Each data folder is an independent set of projects, output and ledger. You can switch between folders with
  `--project-root` (for example one per client or genre); settings and keys (`~/.storystudio`) are shared.
- Several instances can run at once: each uses its own port, token cookies are per port, and saving settings is
  protected by a cross-process lock. Still, let only one instance work on a given data folder: the rule "one
  background task of a kind per project at a time" is enforced only inside a single process.
- Keep the data folder on a local disk with a short path. Folders synced by cloud-drive clients can hold files
  open (when the settings file cannot be written, the app itself suggests checking antivirus software and sync
  drives).

### 8.4 Headless use and environment variables

In environments without a UI (such as CI), environment variables named `STORYSTUDIO_<ROLE>_<FIELD>` override
the model settings for that process, for example `STORYSTUDIO_TEXT_ADAPTER=openai_chat`. They are never written
back to `settings.json`.

- `.env.example` in the repository root shows examples.
- These `STORYSTUDIO_*` variables are read from the process environment only; a `.env` file in the data folder is
  used only for the `SCRIPTGEN_*` drafting fallback.
- All environment variables are listed in the [configuration guide](configuration.md).

### 8.5 Folder layout and backups

Data folder (`--project-root`):

| Path | Contents | Back up? |
|---|---|---|
| `content/` | Project files of every module (such as `content/story_video/<ID>.json` and `content/<module>/<ID>.json`) and uploaded assets in `content/assets/` | Yes |
| `output/` | Generated images, videos, audio and final cuts; `output/story_video/storyvideo.sqlite3` is the drama ledger (SQLite in WAL mode) | Yes (the largest part) |
| `local_workflows/` | Your own ComfyUI workflows and their `.map.json` files | If you have any |
| `.webui_deleted/` | Trash | As needed |
| `.webui_draft_cache/` | Background task records (`tasks/`, `jobs/`) | Optional |
| `.env` | Optional `SCRIPTGEN_*` drafting fallback; may contain keys | If present; treat as secret |

Settings folder (`~/.storystudio`, or `STORYSTUDIO_HOME`):

| Path | Contents | Back up? |
|---|---|---|
| `settings.json` | Model settings and **API keys stored in plain text** (the app tries to restrict the file to your user account). If `STORYSTUDIO_SETTINGS` is set, back it up from that location | Recommended; treat as secret |
| `voices.json`, `voices/` | Voice library, voice samples and reference recordings | Recommended |
| `plugins/` | Plugins; for junctions or symlinks, back up the folder they point to | As needed |
| `logs/` | Logs (rotated at about 2 MB, three old files kept) | No |
| `cache/`, `model_catalog.json`, `site-packages/` | Cache, model catalogue cache, optional components | No (regenerated or reinstalled) |

When backing up:

- Close the app first, or at least make sure no generation is running. The drama ledger is a SQLite database; a
  copy taken while it is in use may be inconsistent. Copy the `-wal` / `-shm` files next to it as well.
- Edits made on the "Shot & style libraries" page are written back into the **program's own library files**, not
  into the data folder:
  - from source, these are `skills/ai-short-film/scripts/camera_library.json` and `style_library.json` in the
    repository (keep them under git);
  - in the packaged app, they live in `<install folder>\_internal\skills\ai-short-film\scripts\`. With an
    all-users install under `Program Files`, the app runs without administrator rights and cannot write there, so
    saving will most likely fail (inferred from the code and the default Windows permissions, not tested). A
    per-user install can save, but a reinstall or upgrade overwrites the files, so keep a copy. If you want to
    maintain your own libraries, install for the current user only, or run from source.

### 8.6 Moving to another computer

1. On the old computer, close the app and copy the whole data folder plus `~/.storystudio` (at least
   `settings.json`, `voices.json` and `voices/`).
2. On the new computer, install the app and ffmpeg, put both copies in place, and start with `--project-root`
   pointing at the data folder. Use the same or a newer version of the app (an older version cannot open a
   ledger written by a newer one).
3. **Drama projects already in production (frozen)**: the ledger stores the output folder's absolute path. If the
   absolute path changes, preparing or generating again is refused (`already has different inputs`). To continue
   such a project, keep the data folder at the same absolute path on the new computer; otherwise open a new
   revision as described in §5 of [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md). Drama projects that have
   not started production are not affected. Moving the other modules' projects between computers has not been
   verified.

## 9. Configuring models

After installing, the first thing to do is pick a source for each of the five roles in "Model settings":

| Role | Used for |
|---|---|
| Script text | Scripts, drafting, reviews |
| Image | First frames, illustrations, image sets |
| Voice | Narration, voice-over, read-aloud |
| Video | Shot videos, single clips |
| Music | Generation in the music module (no built-in source; needs a plugin) |

- Choose one source per role, enter the address, key and model, save, then click "Test connection".
- With an OpenAI-compatible gateway such as LiteLLM, new-api or one-api, you can enter the gateway once at the top
  of the page and apply it to the text, image, voice and video roles in one go.
- If you have a GPU, "Scan this machine" lists local services such as Ollama, LM Studio, vLLM, llama.cpp, ComfyUI
  and SD WebUI that are currently running. Local addresses need no key and cost nothing.

Fields, defaults, environment variables and data flows for every source are in the
[configuration guide](configuration.md).

## 10. Installing plugins

Sources beyond the built-in ones come from plugins. A plugin is ordinary Python code that runs with your user
permissions, so **only install plugins you trust**.

Plugins are loaded from exactly two places:

1. `~/.storystudio/plugins/<name>/` (or `plugins/` under `STORYSTUDIO_HOME` when that is set);
2. the folders listed in the environment variable `STORYSTUDIO_PLUGIN_PATH`, separated by `;` on Windows and `:`
   elsewhere. Each entry is either a plugin folder itself or a parent folder containing several plugins.

Plugins in `settings.json`, in the data folder or next to the exe are never loaded.

Linking a plugin repository into the default folder on Windows (a directory junction, no administrator rights
needed):

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.storystudio\plugins" | Out-Null
New-Item -ItemType Junction -Path "$env:USERPROFILE\.storystudio\plugins\demo" -Target "<plugin repo>\demo"
```

On macOS / Linux, use a symlink:

```bash
mkdir -p ~/.storystudio/plugins
ln -s /path/to/plugin-repo/demo ~/.storystudio/plugins/demo
```

Restart the app, then check each plugin's status and message on the "Model settings" page or under `plugins`
in `/api/health`.

To try the mechanism, use the offline sample plugin in the repository, `tests/fixtures/plugins/example_async`:

```bash
STORYSTUDIO_PLUGIN_PATH="$PWD/tests/fixtures/plugins/example_async" storystudio --project-root /tmp/cc-plugin-demo
```

```powershell
$env:STORYSTUDIO_PLUGIN_PATH = "$PWD\tests\fixtures\plugins\example_async"
storystudio --project-root "$env:TEMP\cc-plugin-demo"
```

Point at `example_async` itself, not at its parent `tests/fixtures/plugins/`, which also holds several plugins
that are broken on purpose for the tests.

- To disable one plugin, rename its folder to `<name>.disabled` (folders starting with `.` or `_` are skipped too).
- To disable all plugins, set `STORYSTUDIO_PLUGINS=off` (`0`, `false` and `no` also work) or start with
  `--no-plugins`.
- In the packaged app, put a plugin's pure-Python third-party packages in the plugin's `vendor/` folder; compiled
  extensions must match the app's Python version.
- Plugin API v1 is experimental and may change before 1.0. How to write one: [docs/plugins.md](../plugins.md)
  (in Chinese).

## 11. Upgrading

**Source install**:

```bash
git pull
pip install -e ".[desktop]"     # dependencies may have changed, so install again
```

No Node is needed: the built UI is committed together with the code.

**Installer**: run the newer installer. Because the AppId is fixed, it overwrites program files of the same name in
place and leaves your data alone. Files that the old version installed but the new one no longer ships stay in the
install folder; for a clean folder, uninstall the old version first (uninstalling does not touch your data either).

What to watch for:

- **The drama ledger** (`storyvideo.sqlite3`) is migrated automatically the first time a newer version opens it
  for writing; after that, **older versions cannot open it**. To keep a way back, close the app and back up
  `output/story_video/storyvideo.sqlite3` (with its `-wal` file) before upgrading.
- **Changes to prompt wording change request fingerprints**: frozen drama projects are then refused
  (`already has different inputs`) and need a new revision; single-clip projects are marked `stale` and the next
  render submits the new request; image-set style projects pick up the new wording only after you expand the list
  again. [CHANGELOG.md](../../CHANGELOG.md) lists what changed in each version (for v0.1.0, see the section on
  prompt wording changes, 提示词措辞调整).
- If a source selected in your settings no longer exists (for example one that came from a plugin that is not
  installed), the setting is kept as is and the UI shows it as "(not loaded)".
- Packaged app: edits from the "Shot & style libraries" page are overwritten, and with an all-users install that
  page may not be able to save at all (see §8.5).
- The app never updates itself. You can enter an update manifest URL in the settings; it is only checked when you
  click "Check for updates", and nothing is downloaded or installed automatically.

## 12. Uninstalling

**Installer**:

- Uninstall from Windows Settings → Apps, or with the uninstall shortcut in the Start menu (its name is in Chinese:
  卸载 Content Cook · 内容创作工作台).
- Uninstalling removes only the program files that setup installed, not your data. Files you added to the install
  folder yourself (such as a hand-placed `ffmpeg.exe`) are not removed, so the folder may remain; delete it by
  hand.
- For a full clean-up, delete by hand:
  - the data folder (`~/StoryStudio` by default, or the folder you chose);
  - `~/.storystudio` (settings and keys, logs, cache, voices, plugins, optional components), plus the settings
    file named by `STORYSTUDIO_SETTINGS` if you set it;
  - the faster-whisper models, if you used the ASR check: only the `models--Systran--faster-whisper-*` folders
    in the Hugging Face cache (`~/.cache/huggingface/hub/` by default, or under `HF_HUB_CACHE` / `HF_HOME`).
    **Leave the rest of `hub` alone**; other programs may keep their models there.
- If plugins were linked in with junctions or symlinks, remove the links themselves first, so you do not delete
  files in the plugin's source folder by accident.

**Source install**:

```bash
pip uninstall storystudio
```

Then delete the clone. If you ran the app in the repository root, your data is inside the repository as well
(`content/`, `output/`, `.webui_*` and so on), so make sure you no longer need it. Delete `~/.storystudio` by hand
as above.

## 13. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| At start: port N already has another program listening (端口 N 已经有别的程序在听), exit code 2 | The port given with `--port` is taken. The message names the owning process (Windows only) and a free port. Use another port or stop that program. Without `--port`, a free port in 8756–8799 is chosen automatically. |
| Another program answers on port N (端口上是别的程序在应答), or the server never comes up; exit code 1 | Try another port; check the log at `~/.storystudio/logs/storystudio.log`. |
| API returns 401: "This local service only accepts requests from the desktop window (or from the address with a token printed at startup)." | The address had no token, the app was restarted (new token), or curl did not send the header. Reopen the `/?token=…` address printed by the current run; add `X-StoryStudio-Token` to curl. curl receives the Chinese original (这个本地服务只接受从桌面窗口…) unless it sends `X-StoryStudio-Lang: en` or `Accept-Language: en`. |
| API returns 403: "This local service only accepts requests made from its own window on this computer…" | You used a LAN IP or another host name, or the request came from another web page. Only `127.0.0.1` / `localhost` on the same computer work. As with 401, curl gets the Chinese text unless it asks for English. |
| Banner: ffmpeg / ffprobe not found on PATH; or a drama first frame or video fails its check after download with an error that mentions ffprobe | Install them as in §4.2; with the packaged app you can also put both exes into the install folder or its `ffmpeg\` subfolder. Restart the app afterwards. |
| Saving on the "Shot & style libraries" page fails in the packaged app | With an all-users install under `Program Files` the app cannot write to its install folder (inferred from the code, not tested). Install for the current user only, or run from source (§8.5). |
| Post-production or a picture book reports a missing CJK font (`UI font unavailable`) | Install a CJK font (Linux: `fonts-noto-cjk`) or point the project at a font file (§4.2). The `fonts` field of `/api/health` shows which kind is missing. |
| A plugin is not loaded | Check its status and message on the "Model settings" page or under `plugins` in `/api/health`. Make sure the folder is in `~/.storystudio/plugins/` or on `STORYSTUDIO_PLUGIN_PATH`; it has `__init__.py` declaring `STORYSTUDIO_API = 1` and `register(api)`; its name does not start with `.` / `_` or end with `.disabled`; and neither `STORYSTUDIO_PLUGINS=off` nor `--no-plugins` is in effect. Restart after fixing. |
| The native window does not open, or the app exits with an error | Start with `--browser` to use your system browser. On Windows the native window needs WebView2. Check the log. |
| SmartScreen or antivirus blocks the installer or the exe | The files are unsigned. Allow them once you trust the source, or build from source yourself. |
| Saving settings fails because the settings file cannot be written | Check the folder's permissions and free disk space, and whether antivirus software or a sync client holds the file. If another instance is saving at the same moment, wait a few seconds and retry. |
| File-not-found or write errors caused by long paths | The code has no special handling for the 260-character path limit on Windows. Keep the data folder and the clone on short paths (such as `D:\StoryStudio`) and avoid very long project ids. |
| "Install faster-whisper" fails | The packaged app needs a local Python of the same version it was built with (CI: 3.11) that has pip. Use the manual command shown on the settings page, then restart. |
| `storyvideo` cannot find projects that the UI shows | The CLI defaults to `STORYSTUDIO_PROJECT_ROOT` or the repository root, not the UI's folder. Pass `--project-root`. |
| After an upgrade or a move, a drama project reports `already has different inputs` | The prompt wording or the data folder's absolute path changed. Open a new revision as in §5 of [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md). |

## 14. What has been verified

| Item | Status |
|---|---|
| Running from source on Windows; building and launching the PyInstaller folder | Done in practice |
| Windows installer (Inno Setup) | Script written, configured only in CI, never run locally |
| CI workflow | Written; had not run on GitHub as of v0.1.0 |
| Linux | Only the CI test configuration (not run as of v0.1.0); no package; native window untested |
| macOS | Packaging script never run; running from source untested |
| Python 3.10 / 3.12+ | Declared but untested; only 3.11 has been tested |
| Code signing | Neither the exe nor the installer is signed |
| Real model providers | Only the full podcast flow has been run end to end with a real text model (through an OpenAI-compatible endpoint) plus edge-tts. Direct vendor keys, voice cloning, local models on real hardware, and a full-length drama with post-production are unverified |

More detail is in the "已验证 / 未验证" (verified / unverified) section of [CHANGELOG.md](../../CHANGELOG.md)
and in [BACKLOG.md](../../BACKLOG.md), both in Chinese. The index of all documentation is
[docs/README.md](../README.md).
