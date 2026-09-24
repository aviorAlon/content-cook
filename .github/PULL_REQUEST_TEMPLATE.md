<!--
感谢贡献！提交前请看 CONTRIBUTING.md。/ Thanks for contributing! Please read CONTRIBUTING.en.md first.
-->

## 改了什么 / What changed

<!-- 简要说明改动和原因；关联的 issue 写 "Closes #123"。/ Briefly describe the change and why; link issues with "Closes #123". -->

## 怎么验证的 / How it was tested

<!-- 跑了哪些测试、手动试了什么。/ Which tests you ran and what you tried by hand. -->

## 用户能感知到的变化 / User-visible changes

<!-- 没有就写「无」。会进 CHANGELOG 的内容写在这里。/ Write "none" if there are none. Anything for the CHANGELOG goes here. -->

## 自查清单 / Checklist

- [ ] `pytest -q` 通过 / passes
- [ ] 改了界面：`npm test` 通过，`npm run build` 的产物已提交 / UI changed: `npm test` passes and the `npm run build` output is committed
- [ ] 改了提示词编译器或镜头库 / 风格库：跑过 `python scripts/refresh_prompt_canary.py`；用了 `--update` 的，上面写了原因 / Prompt compiler or libraries changed: prompt canary run; if `--update` was used, the reason is given above
- [ ] 新功能或修复带了测试 / New features or fixes come with tests
- [ ] 新的中文界面文案都有英文（`frontend/src/locales/en*.js` / `src/webui/i18n_rules.py`）/ Every new Chinese UI string has English
- [ ] 新的内置来源：`settings.ADAPTERS` 与 `adapters.REGISTRY` 一致，异步前缀已登记 / New built-in source: `ADAPTERS` and `REGISTRY` match, async prefix registered
- [ ] 没有密钥、个人信息、本机路径、真实服务数据 / No keys, personal information, local paths or real service data
- [ ] 相关文档已更新（中英两份）/ Related docs updated (Chinese and English)
