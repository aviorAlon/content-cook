English | [中文](../zh/faq.md)

# FAQ

For setup see [Installation and deployment](installation.md); for every setting see the [Configuration reference](configuration.md).

- [Costs](#costs)
- [Privacy and data](#privacy-and-data)
- [Models](#models)
- [Files and projects](#files-and-projects)
- [Upgrading](#upgrading)
- [Plugins](#plugins)
- [Platform support](#platform-support)
- [Troubleshooting](#troubleshooting)
- [Feedback and contributing](#feedback-and-contributing)

## Costs

### Does the software cost anything?

No. Content Cook is open source under the [MIT licence](../../LICENSE).
What costs money is the model services you connect: generation requests go straight to the sources you configure and
are billed at their prices. Local models are free, and so is Edge TTS (Microsoft's online text-to-speech service).

### Could I spend a lot of money by accident?

The app has several guards against spending more than you meant to, but it does **not** know any provider's prices;
it can only tell you how many requests it is about to send:

- **Estimate, then confirm**: before a batch of generations, the app tells you how many requests it will send.
  For dramas the estimate carries a plan fingerprint that is recomputed from disk when you confirm; if the project
  changed after the estimate, submission is refused and nothing is sent.
- **No retries by the app**: AI drafting is never retried and never generates several candidates behind your back;
  tasks that failed for a clear reason are not resubmitted until you decide to. The vendor SDKs do add their own
  transport retries, which the app does not currently switch off: `openai_chat` / `openai_images` / `openai_speech`
  (OpenAI SDK, `max_retries=1`) resend once on timeouts, network errors, 429 and 5xx; the Volcengine Ark sources
  resend up to twice (SDK default); the OpenRouter sources back off and retry on 5xx and network errors (SDK default).
  In rare cases a provider may receive one submission twice, so check its console.
- **No duplicate submissions**: before each paid drama submission the app records "submitting" in its ledger, so a crash
  does not lead to a second submission. If it cannot tell whether the provider received a request, the task stops and
  waits for you instead of sending it again. (This guards against crashes; the SDK retries inside a single submission
  described above are not covered.)
- **A cap on identical retakes**: once the same shot has been generated twice from the same prompt, a third identical
  retake is refused, with a suggestion to change the text, try another model or accept the result
  (the UI lets you force it after confirming).
- **Local sources**: for roles on local services the cost note says "Runs locally and costs nothing (N requests)".

Check your provider's console for actual prices.

## Privacy and data

### Where does my data go?

- The server only binds to `127.0.0.1`, creates a random access token on each start, and checks the Host and Origin
  headers so other web pages cannot drive the local server through your browser.
- When generating, the app sends prompts, reference images / reference audio and parameters to the sources **you** configured.
- Voice cloning sends the reference recording you upload to MiniMax.
- Narration, reading and podcast lines without an explicit voice, when no other voice source is configured, use Edge TTS,
  which means the text is sent to Microsoft's service.
- On start, first-frame and video tasks that were still in flight at the provider when you last quit are polled again
  automatically (status and download only, never a new submission), using the source that took them.
- These only go online when you do something: refreshing the public OpenRouter model catalog, testing the unified
  gateway (a successful test also fetches the gateway's `/models`), checking for updates (only if you set an update URL),
  opening the **Voice library** page (fetches the Edge TTS voice list), one-click install of the speech-recognition
  component (downloads from PyPI), and downloading a speech-recognition model on the settings page or using one for
  the first time (the faster-whisper model comes from Hugging Face).
- There is no telemetry; no usage data is collected.

### Where are my API keys stored? Are they safe?

Only in `~/.storystudio/settings.json` on your machine. They are stored **in plain text**; after every save the app
restricts the file so only the current user can read and write it.
Keys returned to the UI are always masked, and logs and task records mask keys too.
Switching to another source, or pointing a source at a different host, clears the old key so it is never sent elsewhere.
Details: [Configuration reference §2.4](configuration.md#24-how-keys-are-handled).

### Can I use it from my phone or another computer?

No. The server only listens on this machine and rejects requests whose Host is not local. This is deliberate;
we do not recommend working around it with a reverse proxy or port forwarding.

## Models

### Which models should I use?

There is no recommended list, and no comparison between models has been done.

- The text role works with any OpenAI-compatible chat endpoint, cloud or local.
- Image, voice and video each have several built-in sources. The default model shown in each form is a starting point,
  not a recommendation.
- For known models the app keeps a capability table and checks aspect ratio, duration and similar parameters before
  production starts. Models not in the table still work; out-of-range values are just reported at submission time.
- Prompt wording ("dialects") is picked automatically per model, but these dialects have not been compared against real models yet.

The full list of sources is in [Configuration reference §3](configuration.md#3-model-roles-and-built-in-sources).

### Have all these sources been tested?

No. As of this writing (v0.1.0):

- the podcast flow, from drafting to export, has been run end to end with a real text model (through an
  OpenAI-compatible endpoint) plus Edge TTS;
- Edge TTS has been run for real;
- most direct-vendor sources (OpenRouter, Volcengine Ark, MiniMax, OpenAI images / speech / videos) have only been tested
  against fake clients, not with real accounts;
- a full-length drama render with post-production, voice cloning, and local models on real hardware have not been verified.

Item-by-item status is in "已验证 / 未验证" in [CHANGELOG.md](../../CHANGELOG.md) and "现状盘点" in
[BACKLOG.md](../../BACKLOG.md) (both in Chinese). If you get a source working, or broken, with a real account,
please [open an issue](#how-do-i-report-a-bug) and tell us.

### Can it run fully offline?

In principle yes, but this has not been verified on real hardware. You would need all of the following:

- every role you use pointed at a local service (for example Ollama for text, ComfyUI for images and video,
  a local OpenAI-compatible speech server for voices);
- narration and voices not using Edge TTS (it is an online service);
- if you want the speech-recognition gate, faster-whisper and its model installed and downloaded while you are online.

### Why can't I generate anything in the Music module?

The music role has no built-in source: there is no common API shape for music generation, so you need a plugin that
provides the `music` role. Uploading your own audio as background music in dramas, podcasts and picture books still works.

### Do I need a GPU?

Not for cloud sources. Speech recognition (faster-whisper) runs on the CPU. You only need a suitable GPU if you want
to run generation models locally.

## Files and projects

### Where are my files?

| What | Where |
|---|---|
| Projects, generated images / videos / audio, the drama ledger, trash | The **data root**: `~/StoryStudio` for the desktop app; when run from source, the current directory for `storystudio` and the repository root for the `storyvideo` CLI; set it with `--project-root` or `STORYSTUDIO_PROJECT_ROOT` |
| Model settings and keys, logs, cache, voice library, plugins | `~/.storystudio` (set it with `STORYSTUDIO_HOME`) |
| Log file | `~/.storystudio/logs/storystudio.log` |

The full layout is in [Configuration reference §9 and §10](configuration.md#9-data-root-layout).

Note that edits on the **Shot & style libraries** page go into the app's bundled data files, not the data root.
In a source install they change files in your checkout; in the packaged app this is untested (under Program Files
the save will most likely fail, and a reinstall replaces the files anyway).

### Why is my project "locked" so some things can't be edited directly?

When you click **Start production** on a drama project, the project is **frozen**: from then on every generation is
recorded in the ledger, so results you already paid for are never mixed up with requests built from a changed project.

- **Post-production content** such as narration, insert cards, on-screen text and subtitles can be edited at any time,
  without a new version (on the CLI: `storyvideo amend`).
- When you change something that **goes into prompts**, such as characters, shot visuals or dialogue, the UI first shows
  which shots need regenerating and then, once you confirm, **saves a new version**, carrying over the output that can be reused.
- Tasks that failed because of a temporary provider error can be resubmitted in place (`storyvideo retry-failed`)
  without a new version.

If you edit a frozen project on the command line you will see
`episode source changed after the run was frozen; create an explicit revision`; the answer is the same:
create a new version. See [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md) §5 (Chinese).

### A task says it "got no result after submission". What now?

The request was sent but the app never received the provider's task id (for example, the network dropped mid-submission).
To avoid paying twice, the app will not resubmit it automatically. None of the built-in sources can query task history
yet, so such a task cannot be resolved inside the same project (known issue).
What to do: check the provider's console to see whether the request really went through, then create a new version;
the stuck shot becomes a fresh task there. See [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md) §6 (Chinese).

### Can I get a deleted project back?

Yes. Deleting moves the project file, its output and its uploads into the trash (`.webui_deleted/` in the data root).
The Trash page can restore it or delete it permanently.

## Upgrading

### After upgrading, prompts changed and my project wants a new version. Why?

A new release sometimes rewords the prompts sent to models. When a prompt changes, the request fingerprint changes too:

- frozen drama projects refuse to continue with the new prompts (`already has different inputs`) and need a new version;
- single-clip projects are marked as changed (`stale`) and the next render submits the new request;
- modules that build prompts at render time, such as picture books and posters, use the new wording on the next render;
  modules that write prompts into the project file when you expand the item list, such as image sets and portraits,
  keep the old wording for lists already expanded and pick up the new wording once you expand again.

Such changes are recorded by convention in [CHANGELOG.md](../../CHANGELOG.md) (Chinese; for v0.1.0 see the
prompt-wording section), together with the projects they affect.

### Does upgrading touch my data?

No. For the desktop app, run the newer installer over the old one (it uses a fixed app id, so it is designed to upgrade
in place; the installer itself has not actually been run yet, see [Platform support](#platform-support));
`~/StoryStudio` and `~/.storystudio` are left alone. For a source install, pull the new code and run
`pip install -e ".[desktop]"` again.
The drama ledger is upgraded automatically the first time a newer version opens it for writing (the UI server does
this once at start-up); after that, older versions cannot open it. If your settings name a source the new version does not know, the setting is kept as is.

## Plugins

### What are plugins, and are they safe?

Plugins connect model services beyond the built-in sources. They live in `~/.storystudio/plugins/<name>/` and are
loaded at start-up without any change to the core code.

A plugin is ordinary Python code that **runs with your user permissions** and can do anything you can.
Only install plugins you trust and whose code you have looked at. Plugin API v1 is experimental and may change before 1.0.

To start without plugins, pass `--no-plugins` or set `STORYSTUDIO_PLUGINS=off`. If a plugin fails to load, the app
still starts and the settings page explains why. How to write one: [docs/plugins.md](../plugins.md) (Chinese).

## Platform support

### Which operating systems are supported?

The actual state as of this writing (v0.1.0):

| OS | Status |
|---|---|
| Windows x64 | The packaged app (PyInstaller one-folder build) has been built and run during development. The installer (Inno Setup) is only built by the CI configuration and has not actually been run yet. Nothing is code-signed |
| Linux | Designed to run from source (font lookup and so on have Linux branches), and the CI workflow is set up to run the backend tests on Ubuntu (it had not run on GitHub yet at the time of writing), but the full workflow has not been tried by hand. There is no package; the native window (pywebview) is untested, so `storystudio --browser` is recommended |
| macOS | The packaging script `desktop/build_macos.sh` exists but **has never been run on a Mac**; running from source has not been verified either |

Python 3.10 or newer is required, but only 3.11 has been tested.

### Why is the command called storystudio and not content-cook?

"Content Cook" is the product name (currently a working name). It is defined in `src/webui/branding.py`,
and the UI and window title read it from there (the Windows installer script `desktop/installer.iss` keeps its own
copy of the same default name, and a test keeps the two in sync). The Python package, the commands (`storystudio`, `storyvideo`),
the settings directory (`~/.storystudio`) and the desktop program (`StoryStudio.exe`) are technical identifiers,
not the product name, and deliberately do not follow it.

## Troubleshooting

### The launcher says the port is in use

Without `--port`, the launcher tries 8756 and then each port up to 8799 until one is free.
If you pass `--port` with a port that is taken, the launcher prints which program holds it (when it can tell) and a free port,
then exits with code 2. Pick another port.

### The UI shows a banner about missing ffmpeg

Joining clips, post-production, picture-book and podcast rendering, and speech recognition all need `ffmpeg` and
`ffprobe`. Install them on your system PATH (downloads: <https://ffmpeg.org/download.html>).
With the desktop app you can also put `ffmpeg.exe` and `ffprobe.exe` in the program folder or in an `ffmpeg/`
subfolder of it.

### Chinese text in subtitles or cards does not render

Burned-in subtitles and text need a CJK font. The app looks for common fonts per system (Microsoft YaHei / SimHei /
SimSun on Windows, PingFang and others on macOS, Noto Sans CJK / WenQuanYi and others on Linux) and the health check
and post-production panel tell you when none is found. On Linux, installing `fonts-noto-cjk` works.

### curl gets 401 from the API

The local access token is on by default. Send the header `X-StoryStudio-Token: <token>` (the token is in the URL the
launcher prints), or start with `storystudio --no-token` while debugging. Do not turn the token off for everyday use.

### The native window does not open or looks wrong

Use the system browser instead: `storystudio --browser`. If pywebview is not installed, the launcher falls back to
the browser on its own. Errors are in the log, `~/.storystudio/logs/storystudio.log`.

## Feedback and contributing

### How do I report a bug?

[Open an issue](https://github.com/aviorAlon/content-cook/issues/new/choose) on GitHub, choose the bug report
template, and include:

- the version (`storystudio --version`, or the version shown on the settings page), how you installed it
  (source / desktop app), your OS and Python version;
- which role and which source you used (the source id is enough; **never paste keys**);
- steps to reproduce, what you expected and what happened;
- the relevant lines from the log (log paths contain your user name, so replace it before pasting, and remove
  anything else you would rather not publish).

**Do not open public issues for security problems**; report them privately as described in [SECURITY.md](../../SECURITY.md).

### I'd like to contribute

See [CONTRIBUTING.md](../../CONTRIBUTING.md), and please read the [Code of Conduct](../../CODE_OF_CONDUCT.md) first.
