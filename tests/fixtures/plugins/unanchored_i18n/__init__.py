"""坏插件：翻译正则没有 ^…$ 锚定整句——排在核心规则前面会盖住核心文案，必须拒绝。"""

STORYSTUDIO_API = 1


def register(api):
    api.add_i18n(patterns=[(r"失败：(.+)", r"failed: \1")])
