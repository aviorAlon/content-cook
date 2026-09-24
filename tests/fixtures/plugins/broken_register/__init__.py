"""坏插件：登记到一半抛异常——暂存的那一半也不能写进核心表。"""

STORYSTUDIO_API = 1


def _build(config, **kw):  # pragma: no cover - 不会被装配
    raise AssertionError("unreachable")


def register(api):
    api.add_adapter("image", "half_images", _build, label="登记一半的来源")
    api.add_async_prefix("half-img:", "half_images")
    raise RuntimeError("register blew up halfway")
