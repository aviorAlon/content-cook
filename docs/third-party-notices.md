# 第三方声明

这份文件随安装包一起分发（打包白名单见 `desktop/bundle_manifest.py` 的 `NOTICE_FILES`）。

## zenstory-ai/drama-skills（MIT）

来源：https://github.com/zenstory-ai/drama-skills

本仓库短剧模块的题材卡、形态卡、连续性锁、参考图用途、观众可见性、交付面、重拍处置等判据参考了这个项目，对照与取舍见 [drama-skills-gap.md](drama-skills-gap.md)。以取判据为主、不取它的语法，但下面这些地方直接用了它的文字：

- `skills/ai-short-film/scripts/genre_cards.json` 的 12 张题材卡是原卡的节选与压缩：多数字段直接摘录原卡原句（有删节与合并），每张卡的 `confidence` 与 `basis` 照录原文（出处写在该文件的 `_meta` 与各卡的 `source_file`）。
- 短剧模块 `src/storyvideo/` 各文件（起草与视频提示词、静态检查、连续性、重拍处置、形态卡、题材审稿、系列记忆等）里的一些句子、示例与代码注释，界面文案与英文翻译规则（`src/webui/`、`frontend/src/`），`skills/ai-short-film/scripts/form_cards.json`、`skills/ai-short-film/scripts/genre_camera_pairing.json` 的 `why`、`tests/` 与 `frontend/tests/` 下的部分测试用例与夹具（含 `tests/canary/expected_prompts.json`），以及设计文档（`DESIGN.md`、`docs/drama-skills-gap.md`）里也有原句。

这些文字的版权属于 drama-skills contributors，按下面的 MIT 许可使用。原项目的许可如下：

```text
MIT License

Copyright (c) 2026 drama-skills contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 只借思路、没有使用代码或文字的项目

- [waoowaoo](https://github.com/waooAI/waoowaoo)（Elastic License 2.0）：短剧的接缝（入口 / 出口状态）、表演与视线、镜头设计几条规则的思路参考了它的视频导演规范，提示词金丝雀（`scripts/refresh_prompt_canary.py`）借了它的 prompt-canary 做法；没有使用它的代码、提示词或文字，所以只作致谢。
