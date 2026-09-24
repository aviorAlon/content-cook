# Project agent rules

## 范围

- 本仓库是「Content Cook · 内容创作工作台（本地版）」的产品代码：本地运行、模型由用户自己接、数据不出本机。
- 任何具体服务商都只能以 `src/genkit/adapters/` 里的适配器形式存在，或者由仓库外的插件登记（见 [docs/plugins.md](docs/plugins.md)）；`storyvideo`、`webui` 不得直接 import 任何厂商 SDK。
- 仓库里不放任何特定机构的内部服务、账号、数据或交付流程；只对某个私有环境有用的来源做成插件，放在仓库之外。
- 有成熟开源库的地方用库（官方 SDK 优先），不自己手写协议；自写只允许在没有官方 Python SDK 的接口（目前是 MiniMax 与本机的 SD WebUI / ComfyUI）。

## 工程规则

- 改 `src/storyvideo/prompts.py`、镜头库、风格库前后必须跑 `python scripts/refresh_prompt_canary.py`，黄金提示词漂了要说明原因再 `--update`。
- `genkit.settings.ADAPTERS` 与 `genkit.adapters.REGISTRY` 两张表必须一致（有测试守着）。
- 密钥只存 `~/.storystudio/settings.json`；接口回传一律打码；日志、清单、测试夹具里不得出现真实密钥。
- 测试夹具只用合成数据：不放真实服务的抓包响应、真实任务 id、内部地址或本机路径。`tests/` 与 `frontend/tests/` 只留在仓库里，不进安装包。
- 用户数据（spec、output、账本）在 `genkit.paths.data_root()`，代码资源在 `code_root()`；新代码不要再用 `Path(__file__).parents[2]` 猜根目录。
- 单文档制：一件事只在一个地方记录（spec 的后期块、圣经、剧本），改动写回原文件，不另起第二份。
