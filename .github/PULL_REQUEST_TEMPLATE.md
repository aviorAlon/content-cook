<!--
Thanks for contributing! Please read CONTRIBUTING.md first. / 感谢贡献！提交前请看 CONTRIBUTING.zh-CN.md。
-->

## What changed / 改了什么

<!-- Briefly describe the change and why; link issues with "Closes #123". / 简要说明改动和原因；关联的 issue 写 "Closes #123"。 -->

## How it was tested / 怎么验证的

<!-- Which tests you ran and what you tried by hand. / 跑了哪些测试、手动试了什么。 -->

## User-visible changes / 用户能感知到的变化

<!-- Write "none" if there are none. Anything for the CHANGELOG goes here. / 没有就写「无」。会进 CHANGELOG 的内容写在这里。 -->

## Checklist / 自查清单

- [ ] `pytest -q` passes / 通过
- [ ] UI changed: `npm test` passes and the `npm run build` output is committed / 改了界面：`npm test` 通过，`npm run build` 的产物已提交
- [ ] Prompt compiler or libraries changed: prompt canary run (`python scripts/refresh_prompt_canary.py`); if `--update` was used, the reason is given above / 改了提示词编译器或镜头库 / 风格库：跑过金丝雀；用了 `--update` 的，上面写了原因
- [ ] New features or fixes come with tests / 新功能或修复带了测试
- [ ] Every new Chinese UI string has English (`frontend/src/locales/en*.js` / `src/webui/i18n_rules.py`) / 新的中文界面文案都有英文
- [ ] New built-in source: `settings.ADAPTERS` and `adapters.REGISTRY` match, async prefix registered / 新的内置来源：两张表一致，异步前缀已登记
- [ ] No keys, personal information, local paths or real service data / 没有密钥、个人信息、本机路径、真实服务数据
- [ ] Related docs updated (English and Chinese) / 相关文档已更新（中英两份）
