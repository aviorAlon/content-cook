English · 中文 — this page is bilingual / 本页中英双语

# Documentation index / 文档索引

All documentation for Content Cook. A "—" in the English column means the document is Chinese only; a "—" in the 中文 column means it is English only.
Content Cook 的全部文档。English 一列是「—」的只有中文版；中文一列是「—」的只有英文版。

## Getting started / 入门

| English | 中文 | Contents / 内容 |
|---|---|---|
| [README.md](../README.md) | [README.zh-CN.md](../README.zh-CN.md) | Overview and quick start / 项目介绍、快速开始 |
| [Installation and deployment](en/installation.md) | [安装与部署](zh/installation.md) | Requirements, installing from source, running, building the desktop app, upgrading and uninstalling / 环境要求、从源码安装、运行、打包桌面版、升级与卸载 |
| [Configuration reference](en/configuration.md) | [配置参考](zh/configuration.md) | Settings file, model roles and sources, unified gateway, local models, environment variables, directory layout, logs / 设置文件、模型角色与来源、统一网关、本机模型、环境变量、目录结构、日志 |
| [FAQ](en/faq.md) | [常见问题](zh/faq.md) | Costs, privacy, models, file locations, frozen projects, upgrades, plugins, platform support / 费用、隐私、模型、文件位置、项目冻结、升级、插件、平台支持 |

## How it works / 了解系统

| English | 中文 | Contents / 内容 |
|---|---|---|
| [Architecture](en/architecture.md) | [技术架构](zh/architecture.md) | Layers, modules, tech stack, data flow / 分层、模块、技术栈、数据流 |
| [Design](en/design.md) | [设计特点](zh/design.md) | Design principles and key trade-offs / 设计原则与关键取舍 |
| — | [ARCHITECTURE.md](../ARCHITECTURE.md) | Developer-facing architecture notes, including how to add a built-in source / 面向开发者的架构说明，含新增内置来源的步骤 |
| — | [DESIGN.md](../DESIGN.md) | The full product design log; other documents cite its sections as "DESIGN §N" / 完整的产品设计记录（按章节编号，其他文档常引用「DESIGN §N」） |

## Manuals / 使用手册

| Document / 文档 | Language / 语言 | Contents / 内容 |
|---|---|---|
| [STORYVIDEO_RUNTIME.md](../STORYVIDEO_RUNTIME.md) | 中文 | Drama CLI manual: freezing, review gates, retakes and revisions, crash recovery and duplicate-charge protection, output layout / 短剧命令行 `storyvideo` 手册：冻结、审片闸门、重拍与修订、崩溃恢复与防重复扣费、产物布局 |
| [desktop/README.md](../desktop/README.md) | 中文 | Building the Windows / macOS desktop app, installers, optional speech-recognition component, signing / 打包 Windows / macOS 桌面版、安装包、可选的台词识别组件、签名 |
| [plugins.md](plugins.md) | 中文 | Plugin API v1 (experimental): locations, contract, writing adapters, testing, packaged-app notes / 插件接口 v1（实验性）：目录、约定、写适配器、测试、打包版注意事项 |
| [skills/README.md](../skills/README.md) | 中文 | Bundled data: shot library, style library, genre cards, style cards, review checklists, local workflows / 随程序附带的数据：镜头库、风格库、题材卡、风格卡、审稿清单、本机工作流 |

## Module specs / 模块规格

Contracts for each module (fields, APIs, rules); all in Chinese.
各模块的契约文档（字段、接口、规则），均为中文。

| Document / 文档 | Contents / 内容 |
|---|---|
| [clip-spec.md](clip-spec.md) | Single-clip module / 单条视频模块 |
| [imageset-spec.md](imageset-spec.md) | Image-set engine and the sticker / avatar and portrait modules / 图片集引擎，以及「表情与头像」「纪念写真」两个模块 |
| [imagestudio-spec.md](imagestudio-spec.md) | Image workbench module / 图片工作台模块 |
| [picturebook-spec.md](picturebook-spec.md) | Picture book and audiobook module / 绘本 / 有声书模块 |
| [music-spec.md](music-spec.md) | Music module / 配乐模块 |
| [podcast-spec.md](podcast-spec.md) | AI podcast module / AI 播客模块 |
| [scriptlab-spec.md](scriptlab-spec.md) | Script lab module / 剧本工作台模块 |
| [local-models-spec.md](local-models-spec.md) | Local model support: scanning, adapters, ComfyUI workflow mapping / 本机模型支持：扫描、适配器、ComfyUI 工作流映射 |
| [image-style-cards.md](image-style-cards.md) | Image style cards and image prompt dialects / 出图风格卡与图像方言 |
| [prompt-patterns.md](prompt-patterns.md) | How to write prompts for long single-shot videos / 长单镜视频提示词的写法 |

## Research notes / 研究与对照

| Document / 文档 | Language / 语言 | Contents / 内容 |
|---|---|---|
| [drama-skills-gap.md](drama-skills-gap.md) | 中文 | Item-by-item comparison with zenstory-ai/drama-skills and what was adopted / 与 zenstory-ai/drama-skills 的逐项对照和取舍 |
| [script-craft-gap.md](script-craft-gap.md) | 中文 | Script craft checklist: which criteria became static checks and which go to model review / 剧本工艺清单：哪些判据做成静态检查、哪些交给模型审稿 |

## Quality and security / 质量与安全

| Document / 文档 | Language / 语言 | Contents / 内容 |
|---|---|---|
| [SECURITY.md](../SECURITY.md) | English + 中文 | Security policy and how to report vulnerabilities / 安全策略与漏洞报告方式 |
| [security-review-2026-09-23.md](security-review-2026-09-23.md) | 中文 | Pre-release security self-review: local server and packaging, file and path boundaries, subprocesses and ffmpeg arguments / 发布前安全自查：本机服务与打包、文件与路径边界、子进程与 ffmpeg 参数 |
| [release-audit-2026-09-22.md](release-audit-2026-09-22.md) | 中文 | Pre-release audit for v0.1.0: 87 findings and their status / v0.1.0 发布前体检：87 条发现及处理状态 |

## Project and community / 项目与社区

| English | 中文 | Contents / 内容 |
|---|---|---|
| [CONTRIBUTING.md](../CONTRIBUTING.md) | [CONTRIBUTING.zh-CN.md](../CONTRIBUTING.zh-CN.md) | Development setup, conventions, tests, commits and pull requests / 开发环境、代码约定、测试、提交与 PR |
| [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | — (the file links the official Chinese translation / 文内有官方中文译本链接) | Code of conduct / 行为准则 |
| — | [AGENTS.md](../AGENTS.md) | Engineering rules for the project (for humans and coding agents) / 项目工程规则（给人和编码助手看） |
| — | [CHANGELOG.md](../CHANGELOG.md) | Release notes, including what is verified and how prompt changes affect projects / 更新记录，含「已验证 / 未验证」和提示词改动的影响 |
| — | [BACKLOG.md](../BACKLOG.md) | Backlog, status overview, known issues / 待做项、现状盘点、已知问题 |
| [LICENSE](../LICENSE) | — | MIT licence / MIT 许可证 |
| — (licence texts inside are in English / 文内的许可原文为英文) | [third-party-notices.md](third-party-notices.md) | Third-party licence notices (shipped with the desktop app) / 第三方内容的许可声明（随安装包分发） |
