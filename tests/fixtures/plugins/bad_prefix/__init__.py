"""坏插件：想占用保留的任务前缀 file:。"""

STORYSTUDIO_API = 1


def _build(config, **kw):  # pragma: no cover - 不会被装配
    raise AssertionError("unreachable")


def register(api):
    api.add_adapter("video", "bad_prefix_video", _build, label="前缀不合规矩的来源")
    api.add_async_prefix("file:", "bad_prefix_video")
