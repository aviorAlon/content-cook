[English](#english) | [中文](#中文)

# Security Policy / 安全策略

## English

### Supported versions

Content Cook is at an early stage. Security fixes are made on the `main` branch and go into the next release;
older releases do not get separate patches.

| Version | Supported |
|---|---|
| 0.1.x (current version) | Yes |
| Anything older | No |

### Reporting a vulnerability

**Please do not report security problems in public issues, pull requests or discussions.**

Report them privately through GitHub's private vulnerability reporting:

1. Open the repository's **Security** tab and click **Report a vulnerability**, or go directly to
   <https://github.com/aviorAlon/content-cook/security/advisories/new>.
2. Describe the problem. Helpful details:
   - the version (`storystudio --version`) and how you installed it (from source or the desktop app);
   - your operating system;
   - steps to reproduce or a proof of concept;
   - what an attacker could achieve, and under which conditions.

If the **Report a vulnerability** button is not available, open a short public issue asking the maintainers to
enable private reporting, **without any technical details**, and wait for a reply there.

Please do not include real API keys, personal data or private files in a report. Logs mask keys but still contain
local paths with your user name, so check them before attaching.

### What to expect

This project is maintained by its author in their spare time, and there is no fixed response time. Your report will
be acknowledged as soon as possible; progress updates, the fix and the disclosure timing are then coordinated with you
in the private advisory. With your permission you will be credited in the advisory. There is no bug bounty.

### Security model and scope

Content Cook is a single-user app that runs on your own computer. The design assumes that you are the only user of
your account on that machine.

What the app does to protect you:

- the local server binds to `127.0.0.1` only;
- each start creates a random access token; API calls need it (as an HttpOnly, SameSite=Strict cookie or the
  `X-StoryStudio-Token` header), compared in constant time;
- requests whose `Host` is not a loopback name are rejected (DNS rebinding protection), and API requests that browsers
  mark as coming from another site, or that carry a non-matching `Origin`, are refused;
- API keys are stored only in `~/.storystudio/settings.json` (plain text, file restricted to the current user),
  masked in API responses, logs and task records, and cleared when a role switches to another source or host;
- file paths from project files are confined to the data root, and text passed to ffmpeg filters is escaped.

**In scope**, for example:

- a web page (or another site in your browser) that can read data from, or trigger actions in, the local server;
- bypassing the access token, the Host check or the Origin check;
- reading or writing files outside the data root through project files, uploads, trash or restore;
- command injection through ffmpeg or other subprocess arguments;
- API keys leaking into logs, API responses, task records, ledgers or exports;
- the packaged app shipping files it should not (local data, tests, build-machine paths).

**Out of scope**:

- running with `--no-token`, or exposing the local port to other machines through a proxy or port forwarding
  (both unsupported);
- plugins: they are ordinary Python code that runs with your permissions by design, so only install plugins you trust;
- attacks that require an attacker who already controls your user account or can edit files in your home directory;
- vulnerabilities in model providers or third-party packages themselves (please report those upstream; do tell us
  if the way Content Cook uses them makes a problem exploitable);
- the content that models generate.

### Previous reviews

Before the first public release the project went through a security self-review and a broad release audit.
Both reports are in Chinese:

- [docs/security-review-2026-09-23.md](docs/security-review-2026-09-23.md): local server and packaging,
  file and path boundaries, subprocesses and ffmpeg arguments;
- [docs/release-audit-2026-09-22.md](docs/release-audit-2026-09-22.md): the full pre-release audit.

---

## 中文

### 支持的版本

Content Cook 还在早期阶段。安全修复在 `main` 分支上进行，随下一个版本发布；旧版本不单独打补丁。

| 版本 | 是否支持 |
|---|---|
| 0.1.x（当前版本） | 支持 |
| 更早的版本 | 不支持 |

### 报告漏洞

**请不要在公开的 issue、PR 或讨论区报告安全问题。**

请通过 GitHub 的私密漏洞报告功能提交：

1. 打开仓库的 **Security** 标签页，点 **Report a vulnerability**；或者直接打开
   <https://github.com/aviorAlon/content-cook/security/advisories/new>。
2. 描述问题。最好包括：
   - 版本（`storystudio --version`）和安装方式（源码 / 桌面版）；
   - 操作系统；
   - 复现步骤或概念验证；
   - 攻击者能做到什么、需要什么条件。

如果看不到 **Report a vulnerability** 按钮，请开一个简短的公开 issue，请维护者开启私密报告，
**不要写任何技术细节**，然后在那里等回复。

报告里请不要附真实的 API 密钥、个人数据或私人文件。日志会遮掉密钥，但里面的本机路径带用户名，附上之前请自己再检查一遍。

### 之后会怎样

这个项目由作者业余维护，没有固定的响应时限。收到报告后会尽快确认，在私密的安全公告（advisory）里跟你同步进展，
并和你商量修复与公开的时间。征得你同意后，会在公告里致谢。本项目没有漏洞赏金。

### 安全模型与范围

Content Cook 是跑在你自己电脑上的单用户程序，设计前提是：这台电脑上你的账户只有你自己在用。

程序做了这些保护：

- 本地服务只绑定 `127.0.0.1`；
- 每次启动生成一个随机访问令牌，接口调用必须带上它（HttpOnly、SameSite=Strict 的 cookie，或 `X-StoryStudio-Token` 请求头），用常量时间比较；
- `Host` 不是回环地址的请求一律拒绝（防 DNS rebinding）；浏览器标明来自别的站点、或 `Origin` 对不上的接口请求一律拒绝；
- API 密钥只存在 `~/.storystudio/settings.json`（明文，文件权限收紧到当前用户），接口回传、日志、任务记录里都打码；
  角色换了来源或主机时旧密钥自动清掉；
- 项目文件里的路径限制在数据目录之内，传给 ffmpeg 滤镜的文字会转义。

**在范围内**，例如：

- 某个网页（或浏览器里的其他站点）能读取本机服务的数据，或触发它的操作；
- 绕过访问令牌、Host 检查或 Origin 检查；
- 通过项目文件、上传、回收站或恢复，读写数据目录之外的文件；
- 通过 ffmpeg 或其他子进程参数注入命令；
- 密钥泄漏到日志、接口返回、任务记录、账本或导出文件里；
- 打包版带进了不该带的文件（本机数据、测试、打包机路径）。

**不在范围内**：

- 用 `--no-token` 启动，或者通过代理、端口转发把本机端口暴露给其他机器（这两种用法都不支持）；
- 插件：插件按设计就是以你的权限运行的普通 Python 代码，只装你信任的插件；
- 需要攻击者已经控制你的用户账户、或者能改你主目录下文件的攻击；
- 模型服务商或第三方库本身的漏洞（请向上游报告；如果是 Content Cook 的用法让它变得可被利用，请告诉我们）；
- 模型生成的内容。

### 以往的审查

首次公开发布前，项目做过一次安全自查和一次全面的发布前体检（报告为中文）：

- [docs/security-review-2026-09-23.md](docs/security-review-2026-09-23.md)：本机服务与打包、文件与路径边界、子进程与 ffmpeg 参数；
- [docs/release-audit-2026-09-22.md](docs/release-audit-2026-09-22.md)：发布前体检全文。
