中文 | [English](CONTRIBUTING.en.md)

# 参与贡献

感谢你愿意参与 Content Cook！这份文档说明怎么搭开发环境、仓库里的约定、要跑哪些测试，以及怎么提交改动。

参与之前请先读 [行为准则](CODE_OF_CONDUCT.md)。
想先了解系统：[README](README.md)、[技术架构](docs/zh/architecture.md)、[设计特点](docs/zh/design.md)，
以及项目的工程规则 [AGENTS.md](AGENTS.md)（写给人和编码助手看的，这里的约定以它为准）。

## 报告问题与提建议

- **问题（bug）**：用「问题报告」模板[新建 issue](https://github.com/aviorAlon/content-cook/issues/new/choose)。
  写清版本（`storystudio --version`）、安装方式、操作系统、复现步骤。日志里的路径带用户名，贴之前替换掉，
  也检查有没有别的不想公开的内容，**绝不要贴密钥**。
- **功能建议**：用「功能建议」模板，先说要解决的问题，再说你设想的做法。
- **安全问题**：不要开公开 issue，按 [SECURITY.md](SECURITY.md) 私下报告。
- 大一点的改动（新模块、新的内置来源、改数据格式）建议先开 issue 讨论，再动手写代码。

## 开发环境

需要：

| 工具 | 版本 | 说明 |
|---|---|---|
| Python | 3.10 及以上 | CI 用 3.11，目前也只在 3.11 上测过，建议用 3.11 |
| ffmpeg / ffprobe | 放在 PATH 里 | 没有时，涉及音视频的测试会自动跳过 |
| 中文字体 | — | 没有时，涉及字幕渲染的测试会跳过；Linux 上可装 `fonts-noto-cjk` |
| Node.js | 22 | 只有改界面（`frontend/`）时才需要 |

```bash
git clone https://github.com/aviorAlon/content-cook.git
cd content-cook
python -m venv .venv                 # 建议用虚拟环境
source .venv/bin/activate            # Windows PowerShell：.venv\Scripts\Activate.ps1
pip install -e ".[desktop,dev]"      # 和 CI 装的一样
```

只支持在仓库里以可编辑方式安装（`pip install -e`）：程序运行时要从仓库根读取 `skills/`、`examples/` 等数据。

起一个开发用的服务：

```bash
storystudio                          # 起本地服务并打开窗口
storystudio --browser                # 用系统浏览器
storystudio --project-root <目录>     # 把测试数据放在别处，不弄脏仓库
STORYSTUDIO_HOME=<临时目录> storystudio --project-root <目录>   # 设置、日志、插件也放到别处，不动你自己的 ~/.storystudio
```

从仓库里直接运行时，数据目录默认是当前目录；`content/`、`output/` 等都已在 `.gitignore` 里。

### 界面开发

界面源码在 `frontend/`（Vite + Vue 3），构建产物提交在 `src/webui/static/app/`。

```bash
# 终端 1：后端（固定端口、关掉令牌，给 Vite 反代用）
storystudio --no-open --no-token --port 8765

# 终端 2：前端热更新
cd frontend
npm ci
npm run dev                          # 打开 http://localhost:5173/app/（/api 反代到 8765）
```

改完界面：

```bash
cd frontend
npm test                             # Vitest
npm run build                        # 产物写进 src/webui/static/app/，要一起提交
```

CI 会重新构建一次，发现 `src/webui/static/app/` 和源码对不上就失败。

## 测试

提交之前，按改动范围跑下面这些：

| 改了什么 | 要跑 |
|---|---|
| 任何 Python 代码 | `pytest -q` |
| `frontend/` | `cd frontend && npm test && npm run build`，并提交构建产物 |
| 提示词编译器（`src/storyvideo/prompts.py`）、镜头库、风格库、形态卡 | 改之前和改之后都跑 `python scripts/refresh_prompt_canary.py` |

- 新功能和修 bug 都要带测试。后端测试在 `tests/`，前端测试在 `frontend/tests/`。
- 测试不连外网、不用真实账号：模型来源用假实现（fake client / 假 HTTP）代替。
- `tests/conftest.py` 会关掉插件加载，本机装着的插件不会混进测试。要测插件机制，用 `load_plugin` 夹具加载
  `tests/fixtures/plugins/` 下的夹具插件（`example_async` 是能正常工作的示例，其余几个是故意坏掉的失败用例）。
- **提示词金丝雀**：`refresh_prompt_canary.py` 把一份固定的迷你项目（外加每张形态卡的视频提示词）编译出来，
  跟 `tests/canary/expected_prompts.json` 比对。
  漂了说明你的改动会改变发给模型的文字，也会让已冻结的项目要求开新版本。确认是有意的改动后才跑 `--update`，并在 PR 里说明原因。
- 注意「镜头库与风格库」页面会直接改写仓库里 `skills/` 下的文件。用界面试过之后，别把这些无意的改动一起提交。

## 代码约定

仓库没有配置格式化工具或 lint，请照周围代码的写法。几条明确的约定：

- **中文为主**：代码注释、docstring、界面文案以中文为主（少数底层模块的 docstring 是英文，照所在文件的写法即可）。
  界面文案以中文为源，英文另外配（见下一节）。
- **类型标注**：Python 文件开头用 `from __future__ import annotations`，函数带类型标注。
- **厂商只进适配器**：具体服务商只能以 `src/genkit/adapters/` 里的适配器存在，或由仓库外的插件登记；
  `storyvideo`、`webui` 等不直接 import 任何厂商 SDK。有官方 SDK 的用 SDK，只有没有官方 Python SDK 的接口才手写 HTTP。
- **新增内置来源**：在 `src/genkit/adapters/` 加模块，同时登记到 `genkit.settings.ADAPTERS`（表单）和
  `genkit.adapters.REGISTRY`（装配），两张表必须一致（`tests/test_genkit.py` 守着）；异步来源把任务 id 前缀登记到
  `genkit.adapters.ASYNC_PREFIXES`；带上用假客户端的测试。步骤见 [ARCHITECTURE.md](ARCHITECTURE.md)。
- **目录**：用户数据用 `genkit.paths.data_root()`，程序自带的资源用 `code_root()`，不要用 `Path(__file__).parents[...]` 猜根目录。
- **单文档制**：一件事只在一个地方记录，改动写回原文件，不另起第二份。
- **花钱的调用**：沿用现有做法——发请求之前先告诉用户要发几个请求，程序层面不自动重试，不偷偷多生成
  （见 [设计特点](docs/zh/design.md)）。注意官方 SDK 自带的传输重试目前没有关掉
  （见 [FAQ](docs/zh/faq.md#会不会不小心花很多钱)），新写的适配器不要再额外加重试。

## 界面文案与翻译

界面有中文和英文两种语言。**每一句新的中文界面文案都要配英文**：

- **前端**：模板里写 `$t('中文原文')`，脚本里 `t('还有 {n} 镜', { n })`。在 `frontend/src/locales/` 下的 `en*.js` 里加一条
  「中文原文 → 英文」，占位符（`{n}` 之类）要和中文一致。缺翻译时界面会原样显示中文，没有测试会替你发现，要自己检查。
- **后端**：接口返回给界面的中文（报错、提示）在 `src/webui/i18n_rules.py` 里配英文规则：精确匹配放 `EXACT`，
  带变量的放 `PATTERNS`（正则要用 `^…$` 锚定整句）。`tests/test_webui_i18n.py` 会扫描源码里 `raise` 出来的中文文案，
  没有英文规则的会让测试失败。
- 英文要写自然的英文，不要逐字直译。

## 提交与 Pull Request

- 一个 PR 只做一件事，改动尽量小，方便审阅。
- 提交信息第一行用一句话说清改了什么；需要时在正文里写为什么。仓库历史用中文写，中英文都可以。
- 用户能感知到的变化，在 PR 描述里写清楚，方便整理进 [CHANGELOG.md](CHANGELOG.md)。
- 改了行为的，同步更新相关文档；文档有中英两份的（`docs/zh/` 和 `docs/en/`），两份一起改。

### 不要提交的东西

- **密钥和令牌**：任何真实的 API key、token；日志、清单、测试夹具里也不行。
- **个人和本机信息**：本机绝对路径（比如带用户名的主目录路径）、用户名、邮箱、机器名。
- **真实服务的数据**：抓包的真实响应、真实任务 id、内部或私有服务的地址。测试夹具一律用合成数据。
- **用户数据**：`content/`、`output/`、`.webui_*`、`.env`、`settings.json`、账本等（大多已在 `.gitignore` 里）。
- **特定机构的内部内容**：只对某个私有环境有用的来源，做成插件放在仓库外。
- **第三方内容**：引入第三方代码或文字时，确认许可兼容，并在 [docs/third-party-notices.md](docs/third-party-notices.md) 里写明。

### PR 自查清单

- [ ] `pytest -q` 通过
- [ ] 改了界面：`npm test` 通过，`npm run build` 的产物已提交
- [ ] 改了提示词编译器或库：跑过提示词金丝雀；如果用了 `--update`，PR 里写了原因
- [ ] 新功能或修复带了测试
- [ ] 新的中文界面文案都有英文（前端 `en*.js` / 后端 `i18n_rules.py`）
- [ ] 新的内置来源：`ADAPTERS` 与 `REGISTRY` 一致，异步前缀已登记
- [ ] 没有密钥、个人信息、本机路径、真实服务数据
- [ ] 相关文档已更新（中英两份）

## 许可

提交贡献即表示你同意你的贡献以本项目的 [MIT 许可证](LICENSE) 发布。
