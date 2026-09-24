"""坏插件：要的是下一版插件接口。"""

STORYSTUDIO_API = 2


def register(api):  # pragma: no cover - 版本不对，走不到
    raise AssertionError("unreachable")
