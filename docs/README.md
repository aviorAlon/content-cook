中文 · English — 本页中英双语 / this page is bilingual

# 文档索引 / Documentation index

Content Cook 的全部文档。标「中文」的文档目前只有中文。
All documentation for Content Cook. Documents marked "中文" are currently available in Chinese only.

## 入门 / Getting started

| 中文 | English | 内容 / Contents |
|---|---|---|
| [README.md](../README.md) | [README.en.md](../README.en.md) | 项目介绍、快速开始 / Overview and quick start |
| [安装与部署](zh/installation.md) | [Installation and deployment](en/installation.md) | 环境要求、从源码安装、运行、打包桌面版、升级与卸载 / Requirements, installing from source, running, building the desktop app, upgrading and uninstalling |
| [配置参考](zh/configuration.md) | [Configuration reference](en/configuration.md) | 设置文件、模型角色与来源、统一网关、本机模型、环境变量、目录结构、日志 / Settings file, model roles and sources, unified gateway, local models, environment variables, directory layout, logs |
| [常见问题](zh/faq.md) | [FAQ](en/faq.md) | 费用、隐私、模型、文件位置、项目冻结、升级、插件、平台支持 / Costs, privacy, models, file locations, frozen projects, upgrades, plugins, platform support |

## 了解系统 / How it works

| 中文 | English | 内容 / Contents |
|---|---|---|
| [技术架构](zh/architecture.md) | [Architecture](en/architecture.md) | 分层、模块、技术栈、数据流 / Layers, modules, tech stack, data flow |
| [设计特点](zh/design.md) | [Design](en/design.md) | 设计原则与关键取舍 / Design principles and key trade-offs |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | 中文 | 面向开发者的架构说明，含新增内置来源的步骤 / Developer-facing architecture notes, including how to add a built-in source |
| [DESIGN.md](../DESIGN.md) | 中文 | 完整的产品设计记录（按章节编号，其他文档常引用「DESIGN §N」）/ The full product design log; other documents cite its sections as "DESIGN §N" |

## 使用手册 / Manuals

| 文档 / Document | 语言 / Language | 内容 / Contents |
|---|---|---|
| [STORYVIDEO_RUNTIME.md](../STORYVIDEO_RUNTIME.md) | 中文 | 短剧命令行 `storyvideo` 手册：冻结、审片闸门、重拍与修订、崩溃恢复与防重复扣费、产物布局 / Drama CLI manual: freezing, review gates, retakes and revisions, crash recovery and duplicate-charge protection, output layout |
| [desktop/README.md](../desktop/README.md) | 中文 | 打包 Windows / macOS 桌面版、安装包、可选的台词识别组件、签名 / Building the Windows / macOS desktop app, installers, optional speech-recognition component, signing |
| [plugins.md](plugins.md) | 中文 | 插件接口 v1（实验性）：目录、约定、写适配器、测试、打包版注意事项 / Plugin API v1 (experimental): locations, contract, writing adapters, testing, packaged-app notes |
| [skills/README.md](../skills/README.md) | 中文 | 随程序附带的数据：镜头库、风格库、题材卡、风格卡、审稿清单、本机工作流 / Bundled data: shot library, style library, genre cards, style cards, review checklists, local workflows |

## 模块规格 / Module specs

各模块的契约文档（字段、接口、规则），均为中文。
Contracts for each module (fields, APIs, rules); all in Chinese.

| 文档 / Document | 内容 / Contents |
|---|---|
| [clip-spec.md](clip-spec.md) | 单条视频模块 / Single-clip module |
| [imageset-spec.md](imageset-spec.md) | 图片集引擎，以及「表情与头像」「纪念写真」两个模块 / Image-set engine and the sticker / avatar and portrait modules |
| [imagestudio-spec.md](imagestudio-spec.md) | 图片工作台模块 / Image workbench module |
| [picturebook-spec.md](picturebook-spec.md) | 绘本 / 有声书模块 / Picture book and audiobook module |
| [music-spec.md](music-spec.md) | 配乐模块 / Music module |
| [podcast-spec.md](podcast-spec.md) | AI 播客模块 / AI podcast module |
| [scriptlab-spec.md](scriptlab-spec.md) | 剧本工作台模块 / Script lab module |
| [local-models-spec.md](local-models-spec.md) | 本机模型支持：扫描、适配器、ComfyUI 工作流映射 / Local model support: scanning, adapters, ComfyUI workflow mapping |
| [image-style-cards.md](image-style-cards.md) | 出图风格卡与图像方言 / Image style cards and image prompt dialects |
| [prompt-patterns.md](prompt-patterns.md) | 长单镜视频提示词的写法 / How to write prompts for long single-shot videos |

## 研究与对照 / Research notes

| 文档 / Document | 语言 / Language | 内容 / Contents |
|---|---|---|
| [drama-skills-gap.md](drama-skills-gap.md) | 中文 | 与 zenstory-ai/drama-skills 的逐项对照和取舍 / Item-by-item comparison with zenstory-ai/drama-skills and what was adopted |
| [script-craft-gap.md](script-craft-gap.md) | 中文 | 剧本工艺清单：哪些判据做成静态检查、哪些交给模型审稿 / Script craft checklist: which criteria became static checks and which go to model review |

## 质量与安全 / Quality and security

| 文档 / Document | 语言 / Language | 内容 / Contents |
|---|---|---|
| [SECURITY.md](../SECURITY.md) | English + 中文 | 安全策略与漏洞报告方式 / Security policy and how to report vulnerabilities |
| [security-review-2026-09-23.md](security-review-2026-09-23.md) | 中文 | 发布前安全自查：本机服务与打包、文件与路径边界、子进程与 ffmpeg 参数 / Pre-release security self-review: local server and packaging, file and path boundaries, subprocesses and ffmpeg arguments |
| [release-audit-2026-09-22.md](release-audit-2026-09-22.md) | 中文 | v0.1.0 发布前体检：87 条发现及处理状态 / Pre-release audit for v0.1.0: 87 findings and their status |

## 项目与社区 / Project and community

| 中文 | English | 内容 / Contents |
|---|---|---|
| [CONTRIBUTING.md](../CONTRIBUTING.md) | [CONTRIBUTING.en.md](../CONTRIBUTING.en.md) | 开发环境、代码约定、测试、提交与 PR / Development setup, conventions, tests, commits and pull requests |
| [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | 同左（英文，附官方中文译本链接）/ Same file (English, with a link to the official Chinese translation) | 行为准则 / Code of conduct |
| [AGENTS.md](../AGENTS.md) | 中文 | 项目工程规则（给人和编码助手看）/ Engineering rules for the project (for humans and coding agents) |
| [CHANGELOG.md](../CHANGELOG.md) | 中文 | 更新记录，含「已验证 / 未验证」和提示词改动的影响 / Release notes, including what is verified and how prompt changes affect projects |
| [BACKLOG.md](../BACKLOG.md) | 中文 | 待做项、现状盘点、已知问题 / Backlog, status overview, known issues |
| [LICENSE](../LICENSE) | English | MIT 许可证 / MIT licence |
| [third-party-notices.md](third-party-notices.md) | 中文（许可原文为英文）/ Chinese (licence text in English) | 第三方内容的许可声明（随安装包分发）/ Third-party licence notices (shipped with the desktop app) |
