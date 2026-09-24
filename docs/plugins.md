# 插件：接入仓库外的生成来源（插件接口 v1，实验性）

> **实验性（experimental）**：插件接口 v1（`STORYSTUDIO_API = 1`、`genkit.plugin_sdk` 导出的名字、`api` 的登记方法）
> 在 1.0 之前可能调整。改动会写进 [CHANGELOG.md](../CHANGELOG.md)；接口版本号变了之后，写着旧版本号的插件会被拒绝加载并说明原因（见 §5），
> 不会带着对不上的接口跑起来。

核心只内置公开的来源（OpenAI 兼容接口、OpenRouter、火山方舟、MiniMax、本机 SD WebUI / ComfyUI、Edge TTS）。
别的来源——私有部署的接口、只在本机能跑的命令行工具、还没公开的服务——做成**插件**：放在仓库外、装在自己机器上，
开发态、打包好的桌面版、命令行三处自动加载，核心代码一行不改。配乐这一档没有内置来源，要出配乐就得装一个提供它的插件。

本文的例子都来自测试用的示例插件 `tests/fixtures/plugins/example_async/`：一个进程内的异步来源（生图 / 视频 / 配乐三档），
不联网、不起子进程，可以照着它写自己的插件。

## 1. 放在哪、怎么开关

| 位置 | 说明 |
|---|---|
| `<STORYSTUDIO_HOME 或 ~/.storystudio>/plugins/<名字>/` | 默认目录，每个子目录一个插件。Windows 上就是 `%USERPROFILE%\.storystudio\plugins\<名字>\` |
| 环境变量 `STORYSTUDIO_PLUGIN_PATH` | 额外目录，多个用系统路径分隔符（Windows `;`，其他 `:`）。每一项本身是插件目录（里面有 `__init__.py`），或是装着多个插件的父目录 |

- 插件名就是目录名。以 `.` 或 `_` 开头、以 `.disabled` 结尾的目录跳过——把 `demo` 改名成 `demo.disabled` 就停用了它。
- 父目录下只有带 `__init__.py` 或 `plugin.json` 的子目录才算插件；别的子目录（`tools/`、`docs/`、数据目录）不理会。
- 同名插件只加载第一个（默认目录在前，环境变量里的按顺序），其余记为「跳过」。
- 全部关掉：环境变量 `STORYSTUDIO_PLUGINS=off`（也认 `0` / `false` / `no`），或桌面启动器加 `--no-plugins`（排查问题时用）。
- **有意不认**的地方：`settings.json` 里的任何键（设置接口改得到它）、项目数据目录（里面可能是别人发来的项目）、exe 所在目录。
  插件目录就是执行代码的入口，只能由本机用户自己放进上面两处。

把自己的插件仓库挂进默认目录的一种做法（Windows，目录联接，不用管理员权限）：

```powershell
New-Item -ItemType Junction -Path "$env:USERPROFILE\.storystudio\plugins\demo" -Target "<你的插件仓库>\demo"
```

## 2. 插件长什么样

```
demo/
├── plugin.json      可选：名字、版本、声明提供的来源与前缀
├── __init__.py      必填：STORYSTUDIO_API、register(api)
├── adapter.py       适配器实现（只 import genkit.plugin_sdk）
└── vendor/          可选：纯 Python 的第三方依赖，加载器追加进 sys.path
```

`__init__.py` 的约定：

```python
STORYSTUDIO_API = 1          # 必填；跟程序的插件接口版本不一致就拒绝加载
__version__ = "0.1.0"        # 可选，在设置页和健康检查里显示

def register(api):           # 必填；只做登记——不联网、不起子进程、不读写用户数据
    from . import adapter as A

    api.add_adapter("image", "example_async_images", A.build_images,
                    label="示例异步生图（测试用示例，不联网）", hint="提交后给远端任务 id，查一次就完成",
                    fields=["model"], defaults={"model": "demo-image"})
    api.add_async_prefix("exasync-img:", "example_async_images")
    api.add_capabilities({("image", "example_async_images", ""): {"aspect_ratios": ["9:16", "16:9", "1:1"], "source": "doc"}})
    api.add_i18n(exact={"示例异步生图（测试用示例，不联网）": "Example async images (test sample, offline)"},
                 patterns=[(r"^找不到示例命令 (\S+)：示例来源需要本机装好它$", r"Example command \1 not found: ...")])
    api.add_failure_hints("config", ["找不到示例命令"])
```

`plugin.json`（可选）只在插件**导入失败**时派上用场：程序据此还能说出「这个来源 / 这个任务前缀本来属于哪个插件、为什么没加载」。
成功加载后跟实际登记的内容对不上只记一条 warning。

```json
{"name": "example_async", "version": "0.1.0", "api": 1,
 "provides": {"adapters": {"image": ["example_async_images"]}, "prefixes": ["exasync-img:"]}}
```

## 3. `api` 能做什么

`register` 里的每一次登记先进暂存区，`register` 正常返回后一次写进核心的表；任何一步不合规矩（抛 `PluginError`）或
`register` 自己抛异常，整个插件都不加载，核心表里一条都不留。

| 方法 | 作用 | 规矩 |
|---|---|---|
| `add_adapter(role, adapter_id, builder, *, label, hint="", fields=("model",), defaults=None, local=False)` | 登记一个来源：设置页的下拉、路由装配都认它。`local=True` 表示只跑在本机（不花钱） | `role` 是 text / image / voice / video / music 之一；id 匹配 `^[a-z][a-z0-9_]{1,47}$`，不能跟内置来源或别的插件重名；`fields` 只能取 base_url / api_key / model / voice；`label` 不能为空 |
| `add_async_prefix(prefix, adapter_id)` | 异步任务 id 的前缀 → 由哪个来源查进度 | 匹配 `^[a-z][a-z0-9-]{1,23}:$`；`file:` / `url:` / `local:` / `tail:` 保留；不能跟已有前缀重复；来源必须是本插件登记的 |
| `add_capabilities({(role, adapter_id, model_prefix): caps})` | 能力表条目（画幅、尺寸档、时长、分辨率、参考图上限……），开始制作前的能力检查和分辨率对齐都用它；配乐这一档的 `counts` / `reference_images` / `max_reference_images` 会收紧配乐模块的条数与参考图上限（见 [music-spec.md](music-spec.md) §1） | 来源必须是本插件登记的；键不能已存在 |
| `add_i18n(exact={}, patterns=[])` | 英文界面的翻译：精确表（中文原文 → 英文）和正则表 | 正则必须用 `^…$` 锚定整句（可带 `(?s)` 之类的前缀标志）；精确表碰到核心已有的条目时核心优先 |
| `add_failure_hints(kind, needles)` | 给失败分类补来源自己的措辞（正则，不分大小写） | `kind` 是 config / ratelimit / quota / moderation / timeout 之一 |
| 只读属性 `name` / `plugin_dir` / `home_dir` / `api_version` / `core_version` / `log` | 日志器叫 `storystudio.plugins.<名字>` | — |

插件的翻译正则排在核心规则前面（这样不会被核心的宽泛规则抢先），所以必须锚定整句，免得盖住核心文案。

## 4. 写适配器

只从 `genkit.plugin_sdk` 导入——插件接口 v1 对插件公开的就是这些名字（v1 是实验性的，1.0 之前可能调整，见文首）：

`Adapter, HttpResponse, default_http, raise_for_status, local_token, token_path, to_data_url,
ImageRequest, VideoRequest, MusicRequest, AssetReference, TaskSubmission, TaskSnapshot, TaskState, TaskType, RemoteTask,
TaskHistoryProvider, RemoteIdentityMatcher,
ProviderError, ProviderUnavailable, ProviderSetupRequired, ProviderCommandError, ProviderRejected, ProviderNotConfigured,
ProviderTimeout, ProviderProtocolError, ProviderUnsupported, QuotaExhausted, ProviderRateLimited, lookup, resolution_for,
API_VERSION, PluginAPI, PluginError`

约定：

- `builder(config, *, cache_dir=...)` 返回适配器实例；`config` 是这个角色在设置里的一行（`adapter / base_url / api_key /
  model / voice / extra`），自己需要的额外配置放 `extra`。
- 实例的 `name` 必须等于登记用的 id：路由靠它判断「这个任务是谁提交的」，账本也按它记来源。
- 每个方法只做一次远端操作；轮询、重试、持久化都在核心。同步出结果的，把结果写进 `cache_dir`、任务 id 用 `file:<路径>`；
  异步的，任务 id 用登记过的前缀开头（`exasync-img:0001`），`get_task(task_id)` 返回 `TaskSnapshot`。
- 参考图拿到的是 `local:<路径>` 令牌（`token_path` 取路径），自己编码或上传，**不要**把本地路径原样发给服务商。
- 出错按异常类型区分，核心据此决定「明确失败、可重提」还是「结果不明、先对账」：
  - `ProviderSetupRequired`：本机缺少来源需要的程序 / 登录，一次请求都没发出去——界面归到「模型没配置」；
  - `ProviderRejected`（审核拦截）、`QuotaExhausted` / `ProviderRateLimited`（余额 / 限流）、`ProviderNotConfigured`：明确失败；
  - 其他异常在提交阶段一律按「结果不明」处理，项目会停下来等对账，不会自动重提（防止重复花钱）。
- 可选 `ping()`：设置页「测试连接」调它，返回一句人话。
- 可选历史对账：provider 有 `list_tasks(**filters)` 时，「结果不明」的提交会先去来源那边的历史里找。核心按关键字传过滤条件
  （提交时的任务名、任务类型、提交意图那天、分页等，完整签名见 `genkit.providers.TaskHistoryProvider`），实现只用自己
  认得的几个、其余收进 `**filters` 忽略即可；返回 `RemoteTask` 列表（`request_id` / `name` / `task_type` / `status`，可带
  `source` / `created_at`），没有就返回空列表。核心只按任务 id（或任务名）与类型核对，来源自己的身份规则（比如只认本程序
  提交的、别处提交的同名任务不算）写在可选的 `match_remote(item, *, expected_name, task_type, task_id=None) -> bool` 里。
  只接管唯一匹配，绝不猜；找不到或不止一条就留给人工对账。

## 5. 插件没装上 / 坏了会怎样

程序永远照常启动。

| 情况 | 行为 |
|---|---|
| 插件导入或 `register` 抛异常 | 核心表里一条都不写；设置页和 `/api/health` 的插件一栏显示「插件 X 加载失败：<原因>」 |
| 接口版本不一致 | 拒绝加载：「插件 X 需要插件接口 vN，本程序提供 v1——更新插件或程序」 |
| 来源 id 已被内置或别的插件占用 | 整个插件不加载，已有的来源照常工作 |
| 设置里选的来源不在表里（插件没装 / 加载失败） | 设置文件原样保留；设置页把它显示成选中的「X（未加载）」并给出原因，保存时原样发回、不会被清掉；需要这个角色的生成接口在起任务之前 400，说清下一步：没装就「把插件放进 ~/.storystudio/plugins 后重启」，装了但没加载成就给出原因、叫人修好插件，插件加载关着就说是关掉了；都可以改去换一个来源。这句会进账本，路径只给不带用户名的写法 |
| 在飞任务的前缀没人登记 | 查进度时报「任务 … 是一个没加载的来源提交的（前缀 …），查不了进度」，任务留在账本里、不记失败；装好插件重启后「继续等待」接着查 |

## 6. 测试自己的插件

```python
# conftest.py
import os, pytest
os.environ["STORYSTUDIO_PLUGINS"] = "off"          # 不自动加载本机装着的插件

@pytest.fixture(scope="session", autouse=True)
def plugin():
    from genkit import plugins
    record = plugins.load_from("<插件目录>")
    assert record.status == "loaded", record.message
    yield record
    plugins.unload_all()
```

加载后插件的模块叫 `storystudio_plugin_<名字>`，可以直接 `import storystudio_plugin_demo.adapter`。核心仓库的
`tests/test_adapter_contracts.run_contract_case(case, tmp_path, fakes=None)` 能拿同一套契约（提交 → 查询 → 结果地址、
参考图不裸发）跑插件自己的夹具。它自带的假实现只认 SDK（夹具键 `responses`）和 http（`http`）两种传输；别的传输方式用
`fakes={"夹具键": ("builder 的参数名", 造假实现的函数)}` 接上自己的假实现（带 `seen` 的话也参与参考图检查）。
核心自己的测试一律关着插件（`tests/conftest.py`）。

## 7. 打包版要注意

- 桌面版的 Python 是冻结的：只带核心用到的标准库模块，也不认 `PYTHONPATH`。插件用到的第三方纯 Python 包放进插件的
  `vendor/`；编译扩展要跟程序同一个 Python 版本（不一致时拒绝加载并说明）。导入失败会记成「加载失败」，不会崩。
- 加载顺序：桌面启动器先接上可选组件目录（`~/.storystudio/site-packages`），再加载插件，最后起服务——服务一起来就续等的
  在飞任务已经能找到插件的来源。直接 `uvicorn webui.app:app` 或命令行 `python -m storyvideo.cli` 起的也会加载。

## 8. 在插件里调用命令行工具（Windows）

有的来源只提供命令行工具。插件去调它时，提示词、台词、标题这些文字来自剧本和模型输出，等于**不可信的输入**；
在 Windows 上要特别小心 cmd.exe。

- **npm 装的命令行是 `.cmd` 外壳**。`shutil.which("工具名")` 在 Windows 上找到的多半是 `工具名.cmd`；起 `.cmd` / `.bat`
  时，Windows 一定会交给 cmd.exe 解释整条命令行——`subprocess.run([...], shell=False)` 也躲不开。于是参数里的
  `&`、`|`、`<`、`>`、`^`、`%` 会被当成 cmd 的语法：轻则提交失败或文字被吞，重则 `&` 后面的内容在用户电脑上当命令执行。
  靠加引号、加 `^` 转义是补不全的。
- **做法：绕开外壳，直接起 node。** 读外壳文件，找出它实际调用的入口脚本（npm 生成的外壳里有一行指向
  `node_modules` 下那个 `.js` 的路径），再找到 `node.exe`（外壳旁边有就用旁边的，否则用 PATH 里的），
  用参数列表起 `[node.exe, 入口脚本, *参数]`、`shell=False`。这样参数原样交给 node，不经过 cmd.exe。
- **外壳认不出来就报错，别退回 cmd.exe。** 外壳格式不认识、入口脚本不存在、找不到 node 时抛 `ProviderSetupRequired`，
  说清缺什么、怎么办——这时一次请求都还没发出去，核心会把它当成「没配置好」、可以重提。
- **不可信的文字永远不经过 cmd.exe**：不用 `shell=True`，不拼命令行字符串，不用 `cmd /c`、`os.system`。工具本身只能用
  `.bat` 跑的，把长文字写进临时文件（或走标准输入）再让工具去读，命令行上只放文件路径。长提示词这样做还能避开
  Windows 命令行的长度上限（约 32K 个字符）。这个路径同样会经过 cmd.exe，而用户目录下的默认临时目录里可能带
  `%`、`^`、`&`、`!`：临时文件放在插件自己建的、路径里只有字母数字和 `\ : . _ -` 的目录里；路径里出现 cmd 元字符
  （`& | < > ^ % ! "`）就拒绝并抛 `ProviderSetupRequired`，不要试图转义。
- 子进程只在适配器方法里起，`register` 里不起；给每次调用设超时、捕获输出，按退出码和输出里的措辞映射成
  `genkit.plugin_sdk` 里的异常（见 §4），报错里不要带完整命令行（里面可能有密钥或本机路径）。
