"""坏插件：导入时就缺依赖。plugin.json 声明了它本来提供的来源 / 前缀，好让提示说得出是谁没装上。"""

import storystudio_example_missing_dependency  # noqa: F401 - 故意不存在

STORYSTUDIO_API = 1


def register(api):  # pragma: no cover - 走不到
    raise AssertionError("unreachable")
