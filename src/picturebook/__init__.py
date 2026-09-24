"""绘本 / 有声书模块（DESIGN §39，契约见 docs/picturebook-spec.md）。

与短剧模块共用模型设置、音色库、任务、字幕样式、i18n；自己的东西只有：项目文件形状（spec）、
文本静态检查（lint）、插画提示词（prompts）、插画生成（pages）、朗读合成（narration）、
跟着朗读走的时间轴（timeline）、装配（assembly）、朗读稿导出（scriptsheet）、LLM 起草（draft）。
"""
