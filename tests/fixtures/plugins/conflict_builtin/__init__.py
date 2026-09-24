"""坏插件：想登记一个内置来源已经占用的 id——整个插件不加载，内置的照常工作。"""

STORYSTUDIO_API = 1


def _build(config, **kw):  # pragma: no cover - 不会被装配
    raise AssertionError("unreachable")


def register(api):
    api.add_adapter("image", "conflict_extra_images", _build, label="冲突插件的另一个来源")
    api.add_adapter("image", "openai_images", _build, label="冒名的内置来源")
