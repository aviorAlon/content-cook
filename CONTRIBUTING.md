English | [中文](CONTRIBUTING.zh-CN.md)

# Contributing

Thanks for your interest in Content Cook! This guide covers the development setup, the conventions used in the
repository, which tests to run, and how to submit changes.

Please read the [Code of Conduct](CODE_OF_CONDUCT.md) before taking part.
To get to know the system, start with the [README](README.md), [Architecture](docs/en/architecture.md) and
[Design](docs/en/design.md), plus the project's engineering rules in [AGENTS.md](AGENTS.md) (Chinese; written for both
humans and coding agents, and the authority on the conventions below).

## Reporting bugs and suggesting features

- **Bugs**: [open an issue](https://github.com/aviorAlon/content-cook/issues/new/choose) with the bug report template.
  Include the version (`storystudio --version`), how you installed it, your OS and the steps to reproduce.
  Log paths contain your user name, so replace it before pasting, check for anything else private,
  and **never paste keys**.
- **Feature ideas**: use the feature request template. Describe the problem first, then the solution you have in mind.
- **Security issues**: do not open a public issue; report privately as described in [SECURITY.md](SECURITY.md).
- For larger changes (a new module, a new built-in source, a data format change), please open an issue to discuss
  before writing code.

## Development setup

You need:

| Tool | Version | Notes |
|---|---|---|
| Python | 3.10 or newer | CI uses 3.11, and 3.11 is the only version tested so far; 3.11 is recommended |
| ffmpeg / ffprobe | on your PATH | Without them, audio/video tests are skipped automatically |
| A CJK font | — | Without one, subtitle rendering tests are skipped; on Linux, `fonts-noto-cjk` works |
| Node.js | 22 | Only needed to change the UI (`frontend/`) |

```bash
git clone https://github.com/aviorAlon/content-cook.git
cd content-cook
python -m venv .venv                 # a virtual environment is recommended
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[desktop,dev]"      # the same extras CI installs
```

Only an editable install from the repository (`pip install -e`) is supported: at run time the app reads data such as
`skills/` and `examples/` from the repository root.

Start a development server:

```bash
storystudio                          # local server plus a native window
storystudio --browser                # use the system browser
storystudio --project-root <dir>     # keep test data out of the repository
STORYSTUDIO_HOME=<tmp dir> storystudio --project-root <dir>   # keep settings, logs and plugins away from your real ~/.storystudio
```

When run from the repository, the data root defaults to the current directory; `content/`, `output/` and friends are
already in `.gitignore`.

### Working on the UI

The UI source lives in `frontend/` (Vite + Vue 3); the built output is committed in `src/webui/static/app/`.

```bash
# Terminal 1: backend (fixed port, token off, so Vite can proxy to it)
storystudio --no-open --no-token --port 8765

# Terminal 2: frontend with hot reload
cd frontend
npm ci
npm run dev                          # open http://localhost:5173/app/ (/api is proxied to 8765)
```

When you are done:

```bash
cd frontend
npm test                             # Vitest
npm run build                        # writes to src/webui/static/app/; commit the result
```

CI rebuilds the UI and fails if `src/webui/static/app/` does not match the sources.

## Tests

Before submitting, run what applies to your change:

| What you changed | Run |
|---|---|
| Any Python code | `pytest -q` |
| `frontend/` | `cd frontend && npm test && npm run build`, and commit the build output |
| The prompt compiler (`src/storyvideo/prompts.py`), the shot library, the style library, form cards | `python scripts/refresh_prompt_canary.py` before and after the change |

- New features and bug fixes need tests. Backend tests live in `tests/`, frontend tests in `frontend/tests/`.
- Tests do not go online or use real accounts: model sources are replaced by fakes (fake clients / fake HTTP).
- `tests/conftest.py` turns plugin loading off, so plugins installed on your machine never leak into tests.
  To test the plugin mechanism, use the `load_plugin` fixture with the fixture plugins in `tests/fixtures/plugins/`
  (`example_async` is the working sample; the others are deliberately broken cases).
- **Prompt canary**: `refresh_prompt_canary.py` compiles a fixed mini project (plus the video prompt for each form card)
  and compares the result with `tests/canary/expected_prompts.json`. Drift means your change alters the text sent to models, and frozen projects
  will need a new version. Run `--update` only once you are sure the change is intended, and explain why in the PR.
- The **Shot & style libraries** page writes directly to files under `skills/` in the repository. If you tried it out
  through the UI, do not commit those accidental changes.

## Code conventions

The repository has no formatter or linter configured; follow the style of the surrounding code. The explicit conventions:

- **Chinese first**: code comments, docstrings and UI text are mostly in Chinese (a few core modules use English
  docstrings; follow the file you are editing). UI text uses Chinese as the source, with English provided separately
  (see the next section).
- **Type hints**: Python files start with `from __future__ import annotations`, and functions carry type hints.
- **Vendors only in adapters**: a specific provider may only exist as an adapter in `src/genkit/adapters/` or be
  registered by a plugin outside the repository; `storyvideo`, `webui` and the rest never import a vendor SDK directly.
  Use the official SDK where one exists; hand-written HTTP is only for APIs without an official Python SDK.
- **Adding a built-in source**: add a module under `src/genkit/adapters/` and register it in both
  `genkit.settings.ADAPTERS` (the form) and `genkit.adapters.REGISTRY` (the builders). The two tables must match
  (guarded by `tests/test_genkit.py`). Asynchronous sources register their task-id prefix in
  `genkit.adapters.ASYNC_PREFIXES`. Include fake-client tests. Steps: [ARCHITECTURE.md](ARCHITECTURE.md) (Chinese).
- **Directories**: use `genkit.paths.data_root()` for user data and `code_root()` for bundled resources;
  never guess the root with `Path(__file__).parents[...]`.
- **One document per thing**: each thing is recorded in exactly one place; edits go back into that file rather than
  into a second copy.
- **Calls that cost money**: follow the existing pattern: tell the user how many requests will be sent before sending
  them, never add app-level retries, and never generate extra results behind the user's back
  (see [Design](docs/en/design.md)). Note that the vendor SDKs' own transport retries are currently left on
  (see the [FAQ](docs/en/faq.md#could-i-spend-a-lot-of-money-by-accident)); do not add further retries in new adapters.

## UI text and translations

The UI comes in Chinese and English. **Every new Chinese UI string needs an English counterpart**:

- **Frontend**: use `$t('中文原文')` in templates and `t('还有 {n} 镜', { n })` in scripts. Add a
  "Chinese source → English" entry to one of the `en*.js` files in `frontend/src/locales/`, keeping placeholders such as
  `{n}` identical. A missing entry simply shows the Chinese text, and no test will catch it, so check it yourself.
- **Backend**: Chinese text returned to the UI (errors, notices) gets an English rule in `src/webui/i18n_rules.py`:
  exact strings go in `EXACT`, strings with variables in `PATTERNS` (anchor each regex to the whole sentence with `^…$`).
  `tests/test_webui_i18n.py` scans the source for Chinese messages passed to `raise` and fails when one has no English rule.
- Write natural English rather than a word-for-word translation.

## Commits and pull requests

- Keep each PR to one topic and as small as practical, so it is easy to review.
- Start the commit message with one line saying what changed; add the why in the body when useful.
  The history is written in Chinese; either Chinese or English is fine.
- Describe user-visible changes in the PR so they can go into [CHANGELOG.md](CHANGELOG.md).
- If you change behaviour, update the related docs; for docs that exist in both `docs/zh/` and `docs/en/`, update both.

### Never commit

- **Keys and tokens**: no real API key or token anywhere, including logs, manifests and test fixtures.
- **Personal or machine details**: absolute local paths (for example a home directory containing your user name),
  user names, email addresses, machine names.
- **Data from real services**: captured real responses, real task ids, addresses of internal or private services.
  Test fixtures use synthetic data only.
- **User data**: `content/`, `output/`, `.webui_*`, `.env`, `settings.json`, ledgers and so on (most are already in `.gitignore`).
- **Organisation-internal material**: a source that is only useful in one private environment belongs in a plugin
  outside the repository.
- **Third-party material**: when bringing in third-party code or text, make sure the licence is compatible and record
  it in [docs/third-party-notices.md](docs/third-party-notices.md).

### PR checklist

- [ ] `pytest -q` passes
- [ ] UI changed: `npm test` passes and the `npm run build` output is committed
- [ ] Prompt compiler or libraries changed: prompt canary run; if `--update` was used, the PR explains why
- [ ] New features or fixes come with tests
- [ ] Every new Chinese UI string has English (frontend `en*.js` / backend `i18n_rules.py`)
- [ ] New built-in source: `ADAPTERS` and `REGISTRY` match, async prefix registered
- [ ] No keys, personal information, local paths or real service data
- [ ] Related docs updated (both Chinese and English)

## Licence

By contributing, you agree that your contributions are released under the project's [MIT licence](LICENSE).
