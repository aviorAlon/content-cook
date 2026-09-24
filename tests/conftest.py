"""核心测试一律不加载插件：本机 ``~/.storystudio/plugins`` 里装着的插件不能混进来（会改来源表、起子进程、左右结果）。

要测插件机制就用 ``load_plugin`` 夹具，显式加载 ``tests/fixtures/plugins/`` 下的替身插件，测完全部撤回。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ["STORYSTUDIO_PLUGINS"] = "off"
os.environ.pop("STORYSTUDIO_PLUGIN_PATH", None)   # 开发机上设过的额外插件目录也不认：测试里打开开关时只看临时目录

PLUGIN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plugins"


@pytest.fixture
def load_plugin():
    """``load_plugin("example_async")`` → PluginRecord；结束时撤回所有插件登记（核心表恢复原样）。"""

    from genkit import plugins

    def _load(name: str):
        return plugins.load_from(PLUGIN_FIXTURES / name)

    yield _load
    plugins.unload_all()
