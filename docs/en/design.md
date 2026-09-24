English | [中文](../zh/design.md)

# Design principles and features

This page explains the principles behind Content Cook and the key design of each module: what you see as a user,
and why it works that way. Every point maps to real code, and the main locations are listed so you can check.

- The full design log (the reasoning behind each change, acceptance tests, open questions) is
  [DESIGN.md](../../DESIGN.md), a long document in Chinese. This page summarises it by topic and links to the relevant sections.
- For code layers and data flow see [Architecture](architecture.md); for roles, sources and environment variables see
  [Configuration](configuration.md); for installing and packaging see [Installation](installation.md).

> **About the names.** "Content Cook · Content creation workbench" is a working product name, defined in one place only
> (`src/webui/branding.py`). The Python package `storystudio`, the commands `storystudio` / `storyvideo`, the settings folder
> `~/.storystudio` and the desktop program StoryStudio (`StoryStudio.exe` on Windows, `StoryStudio.app` from the macOS
> packaging script) are technical identifiers and deliberately keep their old names.

## Contents

- [Design principles](#design-principles)
- [Module highlights](#module-highlights)
- [Technical trade-offs](#technical-trade-offs)
- [Non-goals](#non-goals)
- [What is verified and what is not](#what-is-verified-and-what-is-not)
- [Further reading](#further-reading)

## Design principles

| # | Principle | What you see | Main code |
|---|---|---|---|
| 1 | Local first | The server runs on your machine; projects, outputs, ledger and keys stay there | `src/studio/desktop.py`, `src/genkit/paths.py` |
| 2 | Bring your own models, per role | Five roles (text, image, voice, video, music), each with one source | `src/genkit/settings.py`, `src/genkit/adapters/` |
| 3 | Cost transparency | Estimate, confirm, then generate; nothing is paid for without confirmation | `src/webui/generate.py`, `src/webui/routers/episodes.py` |
| 4 | Outputs are never overwritten | Every generation writes a new file; older ones are kept | `src/clip/generate.py`, `src/imageset/generate.py`, `src/music/generate.py`, `src/picturebook/pages.py` |
| 5 | Frozen runs and revisions | Once production starts, changes that affect prompts need a new version | `src/genkit/store.py`, `src/webui/revise.py` |
| 6 | Review gates between stages | Videos only after frames pass; assembly only after every gate passes | `src/storyvideo/qc.py`, `src/storyvideo/asr_qc.py` |
| 7 | Checks explain the fix | Problem cards say "impact — fix"; quotes in model reviews are checked by code | `src/webui/problems.py`, `src/storyvideo/genre_review.py` |
| 8 | Prompts are compiled from structured data | Per-model prompt dialects; changes guarded by a prompt canary | `src/storyvideo/prompts.py`, `scripts/refresh_prompt_canary.py` |
| 9 | Graceful degradation | A missing model, plugin or ffmpeg gets a clear message; the app keeps working | `src/webui/deps.py`, `src/genkit/plugins.py` |
| 10 | Bilingual | UI, server messages and prompt language support Chinese and English | `frontend/src/i18n.js`, `src/webui/i18n.py` |
| 11 | Safe by default | Local access token, Host / Origin checks, masked keys | `src/webui/app.py`, `src/genkit/logmask.py` |

### 1. Local first: your data stays on your machine

- The server binds to `127.0.0.1` only (`src/studio/desktop.py`). LAN and remote access are not supported.
- Project files, outputs and the ledger live in the **data folder**: the current directory when running from source,
  `~/StoryStudio` for the desktop build, or whatever you set with `--project-root` or the `STORYSTUDIO_PROJECT_ROOT`
  environment variable.
- Settings, keys, logs and the voice library live in `~/.storystudio` by default (the `STORYSTUDIO_HOME` environment
  variable moves it). The full layout is in [Configuration](configuration.md).
- What leaves your machine:
  - When you generate: prompts, reference images / reference audio and parameters, sent to **the model sources you configured**.
  - Edge TTS (Microsoft's online speech service) is used as a placeholder for picture-book reading and podcast voices when
    no voice is chosen. Short-drama narration without an explicit engine uses the voice source from Model settings if that
    is a non-Edge source, and Edge TTS otherwise. Voice-library profiles whose engine is `edge` also use Edge TTS. The text
    to be read is sent to whichever service is used.
  - Voice cloning uploads your reference recording to MiniMax.
- These network calls are triggered by something you do:
  - refreshing the public OpenRouter model list;
  - checking for updates (only if an update URL is set; it checks and never downloads);
  - opening the voice library page, which fetches the Edge TTS voice list once (and again when you switch engine or language);
  - one-click install of speech recognition (from PyPI);
  - downloading the speech-recognition model (from Hugging Face; you can pre-download it on the settings page, otherwise the
    first speech-recognition run downloads it);
  - opening the old-style page (linked from a short-drama project's Advanced page), which loads web fonts from Google Fonts
    (the main UI loads no external fonts).
- There is no telemetry in the code and no usage reporting.

**Why:** scripts, photos of people and voice recordings are often private. The tool is meant for one person or a small team
on one computer, so it needs no server. See [DESIGN.md §3](../../DESIGN.md#3-产品原则) (Chinese).

### 2. Bring your own models, configured per role

- Models are configured by **role**, not by vendor: text (scripts, drafting, reviews), image, voice, video and music.
  Each role picks one source (an adapter) with an address, a key and a model.
- There are four kinds of source, and you can mix them:
  - Direct: OpenAI-compatible APIs, OpenRouter, Volcengine Ark, MiniMax, Edge TTS.
  - A unified gateway: an OpenAI-compatible gateway such as LiteLLM, new-api or one-api. Enter its address and key once in
    the settings page and apply it to text, image, voice and video in one click (music has no common API shape, so it is left out).
  - Local models: "Scan this machine" finds running Ollama, LM Studio, vLLM, llama.cpp, ComfyUI, SD WebUI and local
    speech servers. Local addresses need no key and cost nothing. This path is optional; cloud sources work without it.
  - Plugins: sources beyond the built-in ones are written as plugins and live outside the repository
    (see [docs/plugins.md](../plugins.md), Chinese).
- The pipelines only know roles. A project that says `"@image"` / `"@video"` means "use the model from Model settings";
  a concrete model id in the project wins.
- A capability table (`src/genkit/capabilities.py`) records the durations, resolutions and aspect ratios each model accepts.
  It is checked before production starts, and resolutions are snapped to the nearest tier the model supports.
- Official SDKs are used wherever they exist (OpenAI, OpenRouter, Volcengine Ark). Plain HTTP is written only where there
  is no SDK (MiniMax, SD WebUI, ComfyUI, and video APIs in the OpenAI Videos shape).

**Why:** models change quickly and everyone has different accounts. Switching models should not mean rewriting projects,
and adding a source should not mean touching the pipelines. See DESIGN.md
[§14 gateway](../../DESIGN.md#14-统一网关2026-09-07), [§45 local models](../../DESIGN.md#45-本地模型2026-09-10) and
[§57 plugins](../../DESIGN.md#57-插件来源2026-09-23).

### 3. Cost transparency: estimate → confirm → generate

- **Short drama** generation takes two calls:
  1. Estimate (`GET …/generate-estimate`, read-only and free): which shots will be submitted in this batch and with which models,
     which already have output, which are still in progress, which failed, plus a `plan_token` (a hash of the project,
     the stage, the pending shots and the models).
  2. Generate (`POST …/generate`): must carry the same `plan_token`. The server recomputes it on the spot; if it differs
     (the files on disk changed after you saw the estimate) it returns 409 and submits nothing. It also checks that the
     needed roles are configured and returns 400 if not, without locking the draft.
- **Other modules** show a confirmation that states the number of requests, for example
  "Generate a first frame? That sends one image request (it costs money)."
- **Local sources** are described as "Runs locally and costs nothing (N requests)". If settings cannot be read, the app
  assumes the source costs money rather than calling a paid source free.
- The app counts requests; it does not know vendor prices, so it shows no money estimate.
- Drafting and reviews start exactly one generation per click: the app does not retry and does not "generate several and
  keep the best". (With an OpenAI-compatible text source, the underlying OpenAI SDK retries at most once by itself on
  connection errors, rate limits or 5xx responses, and a request is re-sent without streaming options if a gateway rejects
  them.)
- Only the steps that call a model cost money. Static checks, prompt preview, assembly, speech recognition, image
  post-processing, packaging and copying samples all run locally for free.
- Post composition itself runs locally, but narration and dubbed lines are synthesised with TTS first. If the voice source
  used is a paid one (a paid voice source in Model settings, or a voice profile that uses MiniMax), that step calls it;
  Edge TTS or a local speech server costs nothing.

**Why:** the `plan_token` is not about malicious callers. It stops an accidental double click or repeated request from
spending money before you have seen how many jobs will be submitted. Preventing duplicate submissions is the ledger's job
(see principle 5).

### 4. Outputs are never overwritten: keep every attempt, pick one

Every generation writes a new file, and picking one never deletes the others. Single clips, image sets, picture books,
music and the script lab mark the one in use with a `current` pointer. Short drama keeps the old file under `takes/` on a
retake (the UI cannot switch back to a kept take yet), and the newest poster is the current one.

| Module | Where new outputs go |
|---|---|
| Short drama | before a retake, the old frame / video is kept under `01_frames/takes/<shot>/` or `02_video/takes/<shot>/` |
| Single clip | `01_video/<ID>-<n>.mp4`, with `attempts[]` and `current` in the project file |
| Image sets (stickers and avatars, milestone portraits, image studio) | `01_raw/<item>-<n>.png`; post-processed files in `05_out/` with a run stamp in the name |
| Picture book | `05_pages/P01-<n>.png` |
| Music | `01_raw/<ID>-<n>.<ext>` |
| Poster | `07_poster/base-<ms>.png`, `poster-<ms>.png` |
| Script lab | every generation, partial rewrite, hand edit and import is a new version |

- Failures are recorded too (with the reason) and never replace the last good result.
- Final renders and mixes are written to a temporary file first and then swapped in atomically.
- If the request hash (prompt, model, parameters, reference-image content) is unchanged and the file still exists,
  the step is skipped; regenerating needs an explicit "force".

**Why:** generative models give a different result each time, and "the previous one was better" happens a lot.
Something you paid for should not be overwritten by the next attempt.

### 5. Frozen runs and revisions: changes after production starts leave a trail

This applies to short drama.

- "Start production" (`storyvideo prepare` on the command line) freezes the project into
  `<project output folder>/manifests/episode.snapshot.json`, creates the ledger
  (SQLite, `<data folder>/output/story_video/storyvideo.sqlite3`) and compiles the prompts.
- Each job in the ledger records a request fingerprint, `request_hash`. A "submitting" intent is written **before** the
  provider is called. If the process crashes midway, what remains is a row marked "submission result unknown" for a person
  to reconcile. It is never silently resubmitted.
- Within one version, changed inputs or a changed plan are refused (`already has different inputs`), and so is a changed
  request for the same job (`already has different request metadata`).
- After production starts, changing anything that goes into prompts or generation parameters (characters, visuals,
  dialogue, models, …) needs a new version. In the UI this is a single action, "Save and preview cost":
  - It compares the compiled frame and video requests shot by shot and works out which shots must be redone and which can be
    reused. Reference chains propagate: if a referenced shot's frame changes, the shots that reference it are redone too.
  - After you confirm, it copies the project into a new version, carries over reusable outputs and their review decisions,
    and regenerates only the affected shots. The old version is left untouched.
- Editing only "post blocks" (narration, insert cards, on-screen text, subtitle style, titles and so on) does not change any
  fingerprint and takes effect immediately, even during production.
- Once the same prompt has been generated twice, a third identical generation is blocked (409, can be forced). The message
  suggests editing the text, comparing other models, or accepting the current result.
- Known limitation: no built-in source supports task-history lookup, so a job whose submission result is unknown cannot be
  resolved in place yet. The workaround is to check the provider's console and open a new version (see "已知问题"
  (known issues) in [BACKLOG.md](../../BACKLOG.md) and §6 of [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md)).

**Why:** every paid request must be traceable to its inputs, and changing one word should not quietly regenerate a whole episode.
See DESIGN.md [§5.1](../../DESIGN.md#51-一集的生命周期), [§11.2](../../DESIGN.md#112-三步落地) and
[§24](../../DESIGN.md#24-重拍处置与重投纪律2026-09-08融合-drama-skills).

### 6. Review gates between stages

- Short drama has three human review gates: frames, videos and final. A decision is `pass`, `retake`, `edit_rescue`,
  `redesign` or `waive`.
- The order is enforced: no videos until the frames pass, no assembly until every gate passes.
- Before the human review, the program runs technical checks (resolution, valid files, duration, streams).
- The optional speech-recognition gate (local faster-whisper, free) transcribes each video and compares it with the script:
  - a character error rate of 0.3 or less passes (the default; `--threshold` changes it on the command line), up to 0.45
    needs a human listen, anything higher fails;
  - while a project is in production the UI records a retake for failing shots automatically; on the command line add `--record-retakes`;
  - for shots without dialogue the report flags stray speech; only the command-line flag `--include-stray` counts it as a
    failure, the UI does not.
- A retake records what defect was seen (identity drift, broken seam, wrong line, leftover text, wrong motion, moderation,
  quality, …) and what you plan to do (keep / fix in post / edit the text and regenerate / resubmit as is / compare models).
  "Keep" is recorded as a pass and "fix in post" as a waiver: accepted, but not pretending it passed.

**Why:** check early. Look at frames before paying for the more expensive videos, and listen to the dialogue before
assembling. Choppy cuts, unclear lines and shots that don't connect should be caught before the final cut.
See [DESIGN.md §5.3](../../DESIGN.md#53-质检).

### 7. Checks explain the fix, not just the problem

- **Static checks** exist in short drama, AI podcast, script lab, single clip and picture book. They run locally and cost
  nothing. Each warning has the form "location: impact — fix" and becomes a problem card in the UI (where, what it affects,
  how to fix it), coloured and filterable by category. Image sets (stickers and avatars, milestone portraits, image studio)
  and music have no problem-card checks yet, only validation on save; music refuses a brief that states no intent before
  submitting it.
- **Failure reasons** are grouped into six kinds: model not configured, rate limited, out of balance or quota, blocked by
  moderation, network timeout or service unavailable, and other provider errors. Each kind comes with one line of advice.
  Moderation failures are not resubmitted as is by default, because they would most likely be blocked again.
- **Model reviews** (one text-model request each): the genre review and the screenplay craft review for short drama,
  the human-likeness review for podcasts, and the script lab review.
  - The model must quote the original text word for word and give the shot or scene number.
  - The program checks every quote. Items whose quote cannot be found are demoted and never become warnings.
  - Once the text changes, the review is marked stale.
- Thresholds in the checks (lengths, ratios, seconds) are adjustable rules of thumb, not industry standards; the review
  checklists state the basis and confidence of each item.

**Why:** a check that only says "something is wrong" gets ignored. Model reviews can invent problems, so quotes are verified by code.
See DESIGN.md [§11.4](../../DESIGN.md#114-失败的任务2026-09-07) and [§49](../../DESIGN.md#49-按题材审稿2026-09-11).

### 8. Prompts are compiled from structured data

- A project stores structured fields (style skeleton, scene anchor, characters, shots, action, dialogue, camera moves,
  sound effects, continuity locks, …). A compiler assembles them into prompts in a fixed order. The camera library and the
  style library are the compiler's data; you can edit them entry by entry on the libraries page, and they are validated
  before being written.
- **Dialects** rearrange the same shot facts into the wording a target model expects:
  - video (short drama): `generic` (the default when unset), `minimax-h3`, `seedance`; `auto` picks one from the video model;
  - image: `generic`, `gpt_image`, `nano_banana`; if unset (or `auto`) it is guessed from the image model id, falling back
    to `generic`;
  - single clip: `generic`, `nano_banana`.
- **By default the model does not draw text.** Image-style prompts (image sets, picture-book pages, poster backgrounds,
  short-drama first frames) end with "Absolutely NO text, letters, numbers or symbols anywhere in the image." The text you
  do want (poster titles, sticker phrases, greetings and dates on portraits, text-card titles) is drawn locally with Pillow.
  The exceptions are deliberate: when a short-drama shot really needs legible text (a phone screen, a sign), you declare it
  in that shot's `readable_text`, the first-frame prompt then names it as the only text allowed, and a static check warns if
  it is too long. A single clip follows the text policy you write in [Whole clip].
- **Prompt canary:** `scripts/refresh_prompt_canary.py` compiles a fixed mini project (plus every form card in both
  languages) and compares the result with golden output. If a change to the compiler or the libraries alters any prompt,
  it reports drift; `--update` accepts the new output only after you confirm the change was intended.
- New fields follow "absent means no change": adding a field does not change the prompts of projects that don't use it,
  which snapshot tests enforce. Deliberate wording changes are listed separately in the CHANGELOG (for example "提示词措辞调整"
  (prompt wording changes) on 2026-09-23) and do change the request fingerprints of existing projects; a short drama already
  in production needs a new version after upgrading.

**Why:** the frozen ledger relies on request fingerprints. Change one word of a prompt and every project's requests change
with it, so compiler output must be reproducible and any change must be deliberate. Switching models only changes the
wording; the project remains the source of truth. See DESIGN.md [§5.2](../../DESIGN.md#52-一镜的提示词从哪来),
[§17](../../DESIGN.md#17-视频提示词方言2026-09-08融合-zenstory-drama-skills) and
[§41](../../DESIGN.md#41-出图风格卡与图像方言2026-09-09).

> Note: the libraries page edits the library files that ship with the program. In a source install they are under
> `skills/` in the repository; in the packaged build they sit in the install folder and are replaced when you reinstall.

### 9. Graceful degradation

| What is missing | What happens |
|---|---|
| No model configured for a role | A 400 **before** any task starts, naming the role to set up in Model settings; no task is started and no draft is locked |
| No source for the music role | A message that built-in sources do not cover music and a plugin is needed; uploading your own audio as music still works everywhere |
| A plugin is missing or fails to load | The app still starts; Model settings shows roles that point to it as "not loaded" with the reason, and so does the health check; in-flight jobs keep waiting instead of being marked failed; saving settings does not silently clear that role |
| `ffmpeg` / `ffprobe` not on PATH | A permanent banner with a download link; steps that need them say what is missing; podcast per-line audio is still produced and only the mix step explains why it stopped |
| No CJK font | The health check reports the font status; in image sets the one image whose overlay failed is marked failed and the rest continue |
| faster-whisper not installed | The speech-recognition gate explains how to install it; nothing else is affected |
| pywebview not installed | The launcher opens your system browser instead |
| The port given with `--port` is taken | The launcher exits at once and suggests a free port; on Windows it also names the program holding it (without `--port` it picks a free port from 8756 up) |
| A corrupt settings file | On the next save the broken file is renamed to `settings.json.broken-<timestamp>` as a backup |

**Why:** one missing component should not stop the whole app from opening, let alone leave a project stuck halfway.

### 10. Bilingual

- The UI is written in Chinese first, and English is a translation layer. Missing translations fall back to Chinese, so the
  UI never shows blanks or raw keys.
- The language choice is kept in the browser's `localStorage` (`storystudio_lang`). The first choice follows the browser
  language, and the switch sits at the bottom of the sidebar.
- Server messages follow the UI language: every request from the frontend carries `X-StoryStudio-Lang` (otherwise
  `Accept-Language` is used), and a middleware translates the message fields of JSON responses. Your own content is never translated.
- Content can be bilingual too: prompt language can be Chinese or English, picture books get Chinese and English subtitles by default,
  and the relationship-scene and podcast checks use English word lists for English text.
- Not translated: compiled prompts, library content, samples and logs.

**Why:** with a single source text there are no two sets of keys drifting apart.
See [DESIGN.md §13](../../DESIGN.md#13-界面语言2026-09-07).

### 11. Safe by default

- The server binds to `127.0.0.1` only.
- When the app is started through the launcher (`storystudio` or `python -m studio.desktop`), each start creates a random
  access token. The window opens `/?token=…`, which sets an `HttpOnly`, `SameSite=Strict` cookie; after that every `/api/*`
  request needs the cookie or an `X-StoryStudio-Token` header, otherwise it gets 401. `--no-token` turns the token off; it is
  meant only for debugging the API, and the launcher prints a warning. The Host check and the cross-site check below still
  apply, but `Origin` is then accepted from any loopback address.
- Requests whose `Host` is not a loopback address get 403 (DNS-rebinding protection). API requests that the browser marks as
  coming from another site, or that carry the wrong `Origin`, also get 403.
- The packaged build does not serve `/docs` or `/openapi.json`.
- Keys:
  - keys returned by the API are always masked;
  - switching a role to another source clears its key, and pointing it at a different site without entering a new key also
    clears the old one, so a key is never sent to an address it was not meant for;
  - logs and task records mask keys, `Bearer` tokens and `key=`-style URL parameters.
- "Scan this machine" only contacts local and private-network addresses. Public addresses that merely look local
  (such as `127.0.0.1.evil.com`) are rejected before any request, so the scan cannot become a network scanner.
- Plugins are loaded only from `~/.storystudio/plugins/` and `STORYSTUDIO_PLUGIN_PATH`, never from the settings file,
  the data folder or the program folder. A web page or a project someone sends you cannot use them to run code on your machine.
  Plugins run with your user permissions, so install only plugins you trust.
- Paths written in a project are confined to the data folder, and delete / restore never move files outside it.
- Two reviews were done before release: a release audit on 2026-09-22 (87 findings, 86 confirmed and fixed; see
  [release-audit](../release-audit-2026-09-22.md)) and a security self-review on 2026-09-23 (19 findings, all fixed
  with regression tests; see [security-review](../security-review-2026-09-23.md)). Both are in Chinese.

**Why:** this local port can spend money. Other programs and web pages on the same computer should not be able to call it.
To report a security issue, see [SECURITY.md](../../SECURITY.md).

## Module highlights

| Module | UI route | In one line | Contract / notes (Chinese) |
|---|---|---|---|
| Short drama | `/drama` | Script → storyboard → first frames → videos → review → post → final cut → poster | [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md) |
| AI podcast | `/podcast` | Talk script → per-line voices → one mixed episode with show notes | [podcast-spec](../podcast-spec.md) |
| Script lab | `/script` | Try several takes on a block of dialogue, compare, pick one, hand it off | [scriptlab-spec](../scriptlab-spec.md) |
| Single clip | `/clip` | One prompt, one clip | [clip-spec](../clip-spec.md) |
| Picture book / audiobook | `/picturebook` | Illustrations, narration and subtitles (Chinese and English by default); the narration sets each page's length | [picturebook-spec](../picturebook-spec.md) |
| Stickers and avatars | `/sticker` | Template-driven sets: emoji packs, stickers, avatars | [imageset-spec](../imageset-spec.md) |
| Milestone portraits | `/portrait` | Full-moon, 100-day, first-birthday and wedding sets from a scene list | [imageset-spec](../imageset-spec.md) |
| Image studio | `/image` | Single or batch images, multi-model comparison, text cards | [imagestudio-spec](../imagestudio-spec.md) |
| Music | `/music` | Generate music from intent and drop it into another project | [music-spec](../music-spec.md) |

Around the modules sit shared services: Model settings, the voice library, image style cards, the camera and style
libraries, the task centre, trash, recent projects, exports and the bilingual UI. How modules and shared services are split
is described in [DESIGN.md §38](../../DESIGN.md#38-从短剧平台到内容创作工具2026-09-09).

### Short drama

Flow: screenplay (by scene) → shot breakdown → start production → first frames → videos → review → assembly →
post composition → poster and delivery package. The `storyvideo` command and the UI share the same runtime; the commands
are documented in [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md).

- **Screenplay before shots.** Drafting can take two steps: first a scene-by-scene screenplay that only tells the story,
  edited in a scene editor, then a shot breakdown from that screenplay. Breaking down from the project page creates a
  new project, so existing shots (which may already have cost money) are never overwritten.
- **Episode contract and series handoff.** The opening hook, the ending, what this episode must pay off and the facts left
  for the next one. The contract does not go into prompts; static checks verify that the text delivers it. Series memory
  keeps character descriptions verbatim so the next episode's draft does not drift.
- **Shot cards and reference chains.** A shot can reference another shot's first frame, and frames are generated wave by
  wave in dependency order. If the head of a chain is wrong the whole chain is wrong, so for long chains check the first two
  frames before releasing the rest.
- **Capability check before production.** Durations, resolutions and aspect ratios outside a model's range are caught before
  any money is spent (you can force the start).
- **Freezing, the ledger, versions and review gates:** see principles 5 and 6 above.
- **Post layers:** narration (voice per line, subtitles timed to the real TTS audio), dubbed dialogue (a TTS fallback), music,
  full-screen insert cards, on-screen text, corner labels, silences and subtitle style. All of these are post blocks: they
  can be edited during production and never touch generation requests.
- **Segmented rendering.** Post composition renders in segments and joins them, so a computer with ordinary memory can
  render a long film (it used to put the whole film into one ffmpeg command, and memory grew with the number of shots; see
  [CHANGELOG](../../CHANGELOG.md)).
- **Poster.** The background comes from the image model or, for free, from one shot's first frame; the title is drawn locally.
- **Delivery package.** Only whitelisted files are copied, together with `manifest.json`, `checksums.sha256` and
  `ai_disclosure.md` (an AI-generation statement and tool list; frames and videos are attributed to the source and model
  recorded in the ledger). Settings, keys, the ledger and uploaded inputs never go into the package.
- **Background polling.** After submission the server asks the provider every 15 seconds, for up to 60 minutes, and stops
  only after the same error repeats 3 times. It only polls, downloads and records; it never submits. After a restart it
  picks up where it left off.

**Why:** splitting an episode into shots that can be reviewed and retaken one by one makes every step visible, before and
after you spend money. See DESIGN.md [§5](../../DESIGN.md#5-核心流程),
[§11](../../DESIGN.md#11-以资源为中心的工作台重构计划2026-09-07-立项),
[§32](../../DESIGN.md#32-剧本层单集契约与系列交接2026-09-08融合-drama-skills),
[§34](../../DESIGN.md#34-后台轮询与断点续等2026-09-08), [§36](../../DESIGN.md#36-海报2026-09-08) and
[§56](../../DESIGN.md#56-长片需要的后期层与大首帧上传2026-09-22). Some of the short-drama criteria are adapted from the
MIT-licensed drama-skills project; see the [third-party notices](../third-party-notices.md).

### AI podcast

- **Shape of an episode:** segments (cold open, intro, topic, listener Q&A, outro) and 1 to 4 hosts. The format (solo, two
  hosts, interview, roundtable) sets the number of hosts and their roles. A line with an unknown speaker is rejected on save
  rather than silently dropped.
- **"Sounds like a real podcast" is implemented in three places:**
  1. Eight rules in the drafting prompt: spoken rather than written language; someone disagrees; specifics beat opinions
     (material comes only from the brief, no invented data); each host is a distinct person; answers sound like answers;
     the cold open does not introduce the show; no parallel lists or "today we covered three points"; duration you can count.
  2. Static checks apply the same rules in reverse: written-style words, long monologues, mechanical turn-taking, one host
     dominating, no disagreement at all, too many or zero filler words, topics without concrete examples, answers that are
     too short or just repeat the question, a duration far from the target, and more.
  3. Pauses in the mix: 0.35 s between lines, 1.2 s between segments, 0.6 s extra before a line marked as a pause; the intro
     music comes after the cold open.
- **Rewrite a single segment** while the others stay as they are and serve as context.
- **Human-likeness review:** an 11-item checklist judged by the model, with quotes verified by the program.
- **Voices and mix:** TTS per line, skipped when the text and voice parameters are unchanged; the mix is loudness-normalised
  (-16 LUFS); more than 64 lines are pre-mixed in groups so the command stays within the Windows command-line limit.
- **Delivery package:** the mixed audio, show notes, transcript, chapters, per-line audio and an AI-generation statement.

**Why:** AI-drafted conversations often read like essays. Turning "sounds real" into rules that can be followed and checked
gives the rewrite a direction. This is the only module so far that has been run end to end with a real text model (through
an OpenAI-compatible API) plus edge-tts. See DESIGN.md [§50](../../DESIGN.md#50-ai-播客模块2026-09-16) and
[§54](../../DESIGN.md#54-用真实文本模型走通播客全流程2026-09-18).

### Script lab

- **Write → try → compare → pick → use.** Every generation, partial rewrite, hand edit and import is saved as a new version,
  and `current` points to the adopted one. New versions are not adopted automatically, because a person should compare them
  first. The only exception is the very first version, since there is nothing to compare it with.
- **People write the block skeleton** (kind, title, goal); each version stores only the dialogue for each block. Because block
  numbers line up, two versions can be compared block by block.
- **No new criteria.** Checks and reviews reuse the existing podcast and short-drama rules. Thresholds defined in Chinese
  characters (such as line length) are applied to Chinese text only and are not forced onto English.
- **Hand-off and import are copies.** The adopted version is written into a podcast segment, a short-drama scene or a single
  shot, always through the target module's own validation. A short drama that is already in production does not accept new
  dialogue in its shots (dialogue goes into prompts). Blocks with no dialogue in this version are skipped, so they never wipe
  existing lines on the other side.

**Why:** dialogue is the weakest part of both podcasts and short dramas. You need a place to try three versions of the same
block and pick one, and to go back when an edit makes it worse.
See [DESIGN.md §55](../../DESIGN.md#55-剧本工作台2026-09-20).

### Single clip

- **One project is one clip:** no shot breakdown and no ledger.
- **Three-part prompt:** [Whole clip] (look, palette, light, aspect ratio, safe area, text policy, audio), [Beat by beat]
  (one line per beat on an absolute timeline) and [Transitions & ending] (one take or cuts, the last frame, what to avoid).
  The aspect ratio is stated once, in one sentence that also excludes the other orientation, so the prompt cannot
  contradict itself.
- **Timeline gaps are warnings.** Gaps, overlaps and a total that does not match are warnings, not errors, so a half-written
  project still opens.
- **Three first-frame modes:** none (text to video), upload an image, or generate one first (one extra image request).
- **Never overwritten, resumable.** Async sources are polled for up to 45 minutes per clip; after a server restart the app
  resumes waiting for clips that have not come back, polling only and never resubmitting.

**Why:** a 15–30 second clip generated in one go needs a different way of writing; the shot-by-shot drama pipeline is too
heavy here. See [DESIGN.md §43](../../DESIGN.md#43-单条视频模块2026-09-10) and [prompt-patterns](../prompt-patterns.md).

### Picture book / audiobook

- **Narration sets the page length.** A page lasts as long as its narration audio (measured with ffprobe), plus pauses on entering the page,
  between the two languages and on leaving. The 2–4 age band pauses longer; the cover and back cover hold a few extra
  seconds. Without audio, a length is estimated from the text for preview only; assembly needs real audio.
- **Fixed order in illustration prompts:** style anchor (identical on every page) → scene anchor → characters on the page →
  the scene → composition (bottom third kept clear for subtitles) → exclusivity sentence (only these characters are in the
  picture) → no text.
- **Checks by age band** (2–4, 4–6, 6–8): the Chinese character count per page and the page count of the book have a range
  per age band, and the younger the child, the stricter it is (ages 2–4: at most 20 characters per page and 8–10 pages;
  4–6: 20–40 characters and 10–14 pages; 6–8: 40–70 characters and 12–16 pages). Page-turn hooks, onomatopoeia, one action
  per page, a calm ending and similar are checked too. When drafting, the model is told to write the English like a native
  English children's book rather than a word-for-word copy of the Chinese.
- **Rendering:** bilingual by default (sentence by sentence; page by page, Chinese only or English only are also options);
  in bilingual books Chinese subtitles sit on top and English below, with the line being read highlighted; loudness is
  normalised to -14 LUFS for the whole book; vertical 9:16 by default, with an optional 16:9 version. Pages are rendered one
  by one and joined by stream copy, so memory does not grow with the page count.
- **No ledger.** Each book's state is three manifests (illustrations, narration, timeline). Illustrations are never overwritten either.
- **Voices:** without a chosen voice, edge-tts is used as a placeholder and marked as such; real voices come from the voice
  library. You can also export the reading script (xlsx) for a human narrator and bring the audio back in.

**Why:** in a narrated picture book the reading sets the pace, not the other way round.
See [DESIGN.md §39](../../DESIGN.md#39-绘本--有声书模块2026-09-09).

### Image sets: stickers and avatars, milestone portraits

- **One engine plus template packs:** expand a template into a list of images (no model call, free) → generate image by image
  → pick → post-process locally → package as a zip.
- **Seven templates:** emoji pack, sticker set, avatar set; newborn (30 days), 100 days, first birthday, wedding. Each template
  carries a form description that the frontend renders directly, so adding a template is one registration.
- **Models never draw the text.** Sticker phrases, the "100" on a 100-day photo, and greetings and dates on portraits are all
  overlaid locally.
- **Safety rules are not optional:**
  - the three baby templates carry fixed safety wording on every image (nose and mouth clear and uncovered, no accessories on
    the face, only soft fabric around the face), and cake scenes state that there are no flames;
  - portraits of real people keep the original face shape and proportions and natural skin, and the negative prompt blocks
    heavy whitening and over-smoothing;
  - the wedding template is the only one that names people one by one: when reference photos are tagged bride / groom,
    each person is named and the prompt says nobody else is in the picture; other portraits carry a single-person lock
    ("only this one person in the frame"). Known inconsistency: the family scenes in the 100-day and first-birthday
    templates show both parents and the baby, but currently carry the same single-person lock.
- **Local post-processing:** white backgrounds become transparent through a flood fill from the edges (no background-removal
  model); sizes follow the target specs, for example 240×240 PNG of at most 500 KB for the main WeChat sticker image.
- **Never overwritten:** a `current` pointer marks the pick, and one failed image does not stop the batch.
- **Package:** includes `manifest.json` and `checksums.sha256`, and only the picked images.

**Why:** the two lines of work really differ in three things only: which list to generate, the fixed constraints in the
prompt, and how outputs are cropped and laid out. Putting those three into templates means the engine is written once.
See [DESIGN.md §40](../../DESIGN.md#40-图片集引擎与表情与头像纪念写真2026-09-09).

### Image studio

- The third module on the image-set engine, with three templates:
  - Freeform: one sentence, one or several images.
  - Model comparison: the same prompt for each model, one image each, so differences come only from the model.
  - Text card: a title drawn on an image, reusing the poster's text overlay.
- The subject can be left empty entirely, describing only the scene.
- Candidate models for comparison come from this machine: the configured model → the cached model catalog → the last local
  scan. Only caches are read and no request is sent to list them; if all are empty, the template's default list is used.

**Why:** sharing the engine brings picking, never-overwrite, post-processing, packaging and background tasks for free.
See [DESIGN.md §42](../../DESIGN.md#42-图片工作台2026-09-10).

### Music

- **The product is a placement, not an audio file:** generate a few candidates from an intent → listen and pick one → drop it
  into a short drama's music layer, a picture book's background music, or a podcast's intro, bed or outro in one click.
- **Say what you want.** A brief covers usage, mood, genre, instruments, tempo, structure and things to avoid; 10 preset briefs
  can be used as a starting point. A brief that only sets a duration and says nothing about the music is refused, so you
  don't pay for generic filler.
- **One request per candidate:** a failure does not affect the others, and nothing is overwritten.
- **Placement is a copy.** The audio is copied into the target project's asset folder (the file name includes a content
  hash, so repeated placements don't pile up duplicates) and written back through the target module's validation.
  For short drama this goes through the post blocks, so it works during production.
- **No built-in source.** Music APIs have no common shape, so generating needs a plugin that provides the `music` role.
  Uploading your own audio as music still works in every module.

**Why:** a copy rather than a reference means deleting the music project never removes a track from someone else's film.
See [DESIGN.md §44](../../DESIGN.md#44-配乐模块2026-09-10).

### Voice library

- **A voice profile has two parts:**
  - a voice description built from gender, age group, timbre, pace, volume, accent and mood, in Chinese and English;
  - TTS parameters: engine (`edge` for free online voices, `settings` for the voice model in Model settings, or `minimax`),
    voice, rate and pitch.
- **Two uses:**
  - bound to a character, the description goes into every shot's video prompt. When a video model speaks the lines itself it
    only accepts text; one shared description makes the voice sound more like the same person across shots, but not identical;
  - bound to narration or dubbing, the TTS parameters synthesise the audio, which gives a truly stable voice.
- **Binding is a copy, not a reference.** Editing the library never silently changes the prompts of a frozen project. To sync,
  bind again in the project: binding to a character is a generation field and needs a new version once production has
  started; narration is a post block and can change at any time.
- Voice previews are not overwritten in place: a parameter change writes a new file name (parameter hash plus timestamp),
  and older samples are then cleaned up so only the newest one is kept.
- A profile can carry reference audio for voice cloning (MiniMax only for now; not tested with a real account).

**Why:** the frozen-run promise must not be bypassed by one global file. See DESIGN.md
[§12](../../DESIGN.md#12-音色库2026-09-07) and [§33](../../DESIGN.md#33-音色克隆与参考音频2026-09-08).

### Image style cards

- 24 shared cards in 8 categories: portrait, illustration, character, product, fashion, food, poster, UI / social.
- Each card has an anchor sentence in Chinese and English, lighting, a palette (5–7 concrete colour names), materials, a camera
  look, constraints, and "fits" (which templates it suits). "Fits" only filters the list; it is not a hard limit.
- The camera look describes how the image should feel and never uses numbers such as focal length or aperture, because those
  numbers don't reliably produce the corresponding image.
- Cards are used by image sets (stickers, portraits, image studio), picture books, single clips and posters.
- If you pick no card and leave the dialect on "auto", these keys are not written to the project, so the prompts do not
  change just because style cards exist (deliberate wording changes are covered under principle 8).
- The card texts were written for this repository from common knowledge of photographic lighting, illustration technique and
  layout; the basis is recorded in the card library.

**Why:** styles used to be three or four presets per template, scattered through the code. One shared library keeps styles
consistent across modules and is easy to extend. See [DESIGN.md §41](../../DESIGN.md#41-出图风格卡与图像方言2026-09-09)
and [image-style-cards](../image-style-cards.md).

### Trash

- Deleting in any module moves the project file, its outputs in `output/<module>/<id>/` and its uploads in
  `content/assets/<id>/` into `.webui_deleted/<module>/<id>__<time>/`, together with a `manifest.json`. Short-drama archives
  also include the screenplay and the ledger rows.
- Restore puts files back according to a fixed layout rather than the paths in the manifest, so a tampered manifest cannot move
  files outside the data folder. Restore never overwrites existing files and is all or nothing.
- If files are locked or a task is still running, the delete is refused as a whole instead of moving half the files.
- Automatic numbering never hands out an ID that is still in the trash; if you type an ID that collides, restoring the old
  project returns 409 with an explanation.
- Permanent deletion only happens on the Trash page.

**Why:** deleting never means just removing files. Outputs you paid for should not be lost to one mistaken click.

### Task centre

- Long jobs such as waiting for generation results, speech recognition, post composition, image generation, voice synthesis
  and exports run in the background: the API returns a task id immediately and the UI polls for progress.
- Task records are stored in `<data folder>/.webui_draft_cache/tasks/` and never deleted.
- Only one task of the same kind runs per project at a time. A repeated click is refused (409) rather than queued, because a
  queue makes people think the first click did nothing and click again.
- The Task centre lists tasks across modules: module, kind (in plain words), project, status, start time, duration and failure
  reason. The page is read-only and never starts a generation.
- While tasks are running it refreshes every 3 seconds; when everything has stopped, it stops polling.
- After a server restart, short-drama and single-clip jobs that have not returned yet are picked up again (poll, download and
  record only; nothing is submitted and nothing is paid for).

**Why:** a video can take minutes or tens of minutes. An HTTP request should not hang that long, and closing the page should
not lose progress. See the task centre section of
[DESIGN.md](../../DESIGN.md#工作台首页与模块首页分家--任务中心2026-09-10).

### The workbench shell

- The launchpad (`/`) shows, from top to bottom: the product name, module cards, recently edited projects, "N tasks running",
  and three first-use steps (set up a model → pick a module → you are always asked before anything costs money). Recent
  projects, running tasks and the first-use steps are hidden when there is nothing to show.
- The sidebar is for navigation only; "New" is an action and lives on each module's home page.
- Every module home except the script lab has a "copy the sample" button (free), so you can see a complete project before
  configuring any model.

## Technical trade-offs

- **Python backend, a native window through pywebview, packaged with PyInstaller.** Electron was ruled out and Tauri is
  postponed. pywebview uses WebView2 on Windows and WKWebView on macOS. The UI talks to the backend over HTTP only and does
  not use pywebview's JavaScript bridge, so the shell can be swapped later if auto-update is needed (Tauri v2 is the first
  candidate). See [DESIGN.md §8.1](../../DESIGN.md#81-技术栈决策2026-09-07).
- **The ledger uses SQLite from the standard library** in WAL mode. The schema is versioned and migrates automatically;
  an older program refuses to open a ledger written by a newer one.
- **Audio and video work goes through ffmpeg / ffprobe** (assembly, post composition, mixing, picture-book rendering).
  Posters, text cards, image-set overlays and corner labels are drawn with Pillow.
- **The frontend is a Vue 3 single-page app** with hash routing. The built files are committed to the repository, so running
  from source needs no Node.
- The version number has a single source: `src/genkit/version.py`.

More in [Architecture](architecture.md).

## Non-goals

- Multi-user collaboration and permissions, a cloud render queue, an asset store, user accounts, vendor-exclusive features.
- Real-time collaboration: one project folder is worked on by one person at a time (production holds a file lock).
- LAN or remote access: the server binds to the local machine only.
- No promises about any vendor's moderation policy: when a request is blocked, rewrite the prompt or change the shot.

See DESIGN.md [§2](../../DESIGN.md#2-给谁用解决什么) and [§10](../../DESIGN.md#10-非目标与风险).

## What is verified and what is not

| Item | Status |
|---|---|
| The podcast flow end to end (draft → checks → rewrite one segment → human-likeness review → edge-tts voices and mix → export), with a real text model through an OpenAI-compatible API | Run with a real model |
| Script lab: import a podcast script → rewrite one block with a real text model (OpenAI-compatible API) → adopt → hand back to the podcast | Run once (the model review path not verified) |
| Picture-book sample: edge-tts narration → assembled video | Smoke-tested (illustration model not verified) |
| Local logic such as static checks, prompt compilation, the ledger, review gates, trash and background tasks | Covered by automated tests (most adapters only against fake clients) |
| Real image / video / music models (short-drama frames and videos, single clips, image sets and image studio, picture-book illustrations, music), whether direct or through a gateway | Not verified |
| A full-length short drama rendered end to end, including post composition | Not verified |
| Direct sources (OpenRouter, Volcengine Ark, MiniMax, OpenAI images / speech / videos) against real accounts | Not verified; written from official docs and SDK signatures |
| Genre and craft reviews for short drama, and the script lab review, on real scripts with a real model | Not verified |
| The effect of prompt dialects and image style cards on real models | Not verified; never compared |
| Local models on real GPU hardware | Not verified; fake HTTP tests only |
| Voice cloning | Not verified |
| macOS packaging, signing and notarisation | Not verified (the script exists but has never run on a Mac) |
| Check thresholds and the genre / camera-style pairing table | Rules of thumb and human judgement, not measured |

Sources: "已验证 / 未验证" (verified / not verified) in [CHANGELOG.md](../../CHANGELOG.md), [BACKLOG.md](../../BACKLOG.md),
and the "未验证" (not verified) notes in each DESIGN.md section.

## Further reading

- [DESIGN.md](../../DESIGN.md): the full design log (Chinese)
- [ARCHITECTURE.md](../../ARCHITECTURE.md) (Chinese) and [Architecture](architecture.md): code layers
- [Configuration](configuration.md): roles, sources, environment variables, file locations
- [Installation](installation.md) and [FAQ](faq.md)
- [STORYVIDEO_RUNTIME.md](../../STORYVIDEO_RUNTIME.md) (Chinese): the short-drama command line and runtime
- [docs/plugins.md](../plugins.md) (Chinese): the plugin interface (experimental)
- [Documentation index](../README.md): every document, including the module contracts
