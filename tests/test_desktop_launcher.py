"""启动器的端口处理：端口被占、端口上是别人、服务线程挂了，各自要说人话而不是干等。

实际遇到过：端口被另一个程序占着，老实现只会等三十秒然后说 "server did not come up"；
占端口的程序要是对 ``/`` 返回 200，启动器还会把别人的界面当自己的打开。

另：插件在 extra-site 之后、起服务线程之前加载；``--no-plugins`` 一个都不加载。
"""

from __future__ import annotations

import os
import socket
import urllib.error

import pytest

from studio import desktop


@pytest.fixture(autouse=True)
def _clear_serve_error():
    desktop._SERVE_ERROR.clear()
    yield
    desktop._SERVE_ERROR.clear()


def test_port_in_use_detects_a_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert desktop.port_in_use(port) is True
    # 关掉之后（TIME_WAIT 只影响 bind，不影响连接）不再算被占
    assert desktop.port_in_use(port) is False


def test_port_owner_never_raises():
    """拿不到占用者只影响报错措辞，不该抛异常。"""

    assert isinstance(desktop.port_owner(9), str)


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _urlopen(monkeypatch, result):
    def fake(url, timeout=None):   # noqa: ARG001
        if isinstance(result, BaseException):
            raise result
        return _Response(result)

    monkeypatch.setattr(desktop.urllib.request, "urlopen", fake)


def test_probe_identity_ok_foreign_and_down(monkeypatch):
    _urlopen(monkeypatch, b'{"app": "storystudio", "version": "0.1.0"}')
    assert desktop._probe_identity("http://127.0.0.1:8799/") == "ok"

    # 别的 FastAPI 应用：200 但不是我们
    _urlopen(monkeypatch, b'{"detail": "Not Found"}')
    assert desktop._probe_identity("http://127.0.0.1:8799/") == "foreign"

    # 有人应答但接口不存在
    _urlopen(monkeypatch, urllib.error.HTTPError("u", 404, "Not Found", {}, None))
    assert desktop._probe_identity("http://127.0.0.1:8799/") == "foreign"

    # 没人听
    _urlopen(monkeypatch, ConnectionRefusedError())
    assert desktop._probe_identity("http://127.0.0.1:8799/") == ""


def test_wait_ready_states(monkeypatch):
    _urlopen(monkeypatch, b'{"app": "storystudio"}')
    assert desktop._wait_ready("http://127.0.0.1:1/", timeout=1) == "ok"

    _urlopen(monkeypatch, b'{"detail": "Not Found"}')
    assert desktop._wait_ready("http://127.0.0.1:1/", timeout=1) == "foreign"

    # 服务线程挂了就立刻报，不等满超时
    _urlopen(monkeypatch, ConnectionRefusedError())
    desktop._SERVE_ERROR.append(OSError("[Errno 10048] 端口只允许使用一次"))
    assert desktop._wait_ready("http://127.0.0.1:1/", timeout=30) == "dead"

    desktop._SERVE_ERROR.clear()
    assert desktop._wait_ready("http://127.0.0.1:1/", timeout=0.2) == "timeout"


def test_serve_records_bind_failure(monkeypatch):
    """uvicorn 抛出来的异常必须留给主线程，不能在后台线程里静静地死掉。"""

    import uvicorn

    def boom(*_a, **_k):
        raise OSError("[Errno 10048] address already in use")

    monkeypatch.setattr(uvicorn, "run", boom)
    desktop._serve(12345)
    assert desktop._SERVE_ERROR and "10048" in str(desktop._SERVE_ERROR[0])


def test_main_refuses_a_busy_explicit_port(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(desktop, "_setup_logging", lambda: tmp_path / "log.txt")
    monkeypatch.setattr(desktop, "_extend_path_for_ffmpeg", lambda: None)
    monkeypatch.setattr(desktop, "port_in_use", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(desktop, "port_owner", lambda port: "python.exe (PID 1234)")
    monkeypatch.setattr(desktop, "_free_port", lambda preferred=8756: 8756)
    started: list[int] = []
    monkeypatch.setattr(desktop.threading, "Thread", lambda **kw: started.append(1))   # 不该走到起线程

    code = desktop.main(["--no-open", "--no-token", "--port", "8799", "--project-root", str(tmp_path / "root")])
    err = capsys.readouterr().err
    assert code == 2 and not started
    assert "8799" in err and "python.exe (PID 1234)" in err and "--port 8756" in err


# ---------------------------------------------------------------------------- 插件


def _stub_startup(monkeypatch, tmp_path, order):
    """把 main 里起服务之前的几步换成记录：看插件是不是在 extra-site 之后、服务线程之前加载。"""

    # main 会 chdir 到数据目录、改几个环境变量：先登记，测完原样恢复
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STORYSTUDIO_PROJECT_ROOT", str(tmp_path / "root"))
    monkeypatch.delenv("STORYSTUDIO_TOKEN", raising=False)
    monkeypatch.setattr(desktop, "_setup_logging", lambda: tmp_path / "log.txt")
    monkeypatch.setattr(desktop, "_extend_path_for_ffmpeg", lambda: None)
    monkeypatch.setattr(desktop, "_load_extra_site", lambda: order.append("extra-site"))
    monkeypatch.setattr(desktop, "_free_port", lambda preferred=8756: 8799)

    class FakeThread:
        def __init__(self, **kw):
            pass

        def start(self):
            order.append("server-thread")

    monkeypatch.setattr(desktop.threading, "Thread", FakeThread)
    monkeypatch.setattr(desktop, "_wait_ready", lambda *a, **k: "dead")   # 起不来就返回 1，测试到此为止


def test_plugins_load_after_extra_site_and_before_the_server(tmp_path, monkeypatch):
    from pathlib import Path

    from genkit import plugins

    order: list[str] = []
    _stub_startup(monkeypatch, tmp_path, order)
    fixture = Path(__file__).resolve().parent / "fixtures" / "plugins" / "example_async"
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_PLUGINS", "on")
    monkeypatch.setenv("STORYSTUDIO_PLUGIN_PATH", str(fixture))
    real = plugins.ensure_loaded
    monkeypatch.setattr(plugins, "ensure_loaded", lambda: (order.append("plugins"), real())[1])
    plugins.reset()
    try:
        code = desktop.main(["--no-open", "--no-token", "--project-root", str(tmp_path / "root")])
        assert code == 1
        assert order == ["extra-site", "plugins", "server-thread"]
        assert [(r.name, r.status) for r in plugins.records()] == [("example_async", "loaded")]
    finally:
        plugins.reset()


def test_no_plugins_flag_skips_loading(tmp_path, monkeypatch):
    from genkit import plugins

    order: list[str] = []
    _stub_startup(monkeypatch, tmp_path, order)
    monkeypatch.setenv("STORYSTUDIO_PLUGINS", "on")
    monkeypatch.setattr(plugins, "ensure_loaded", lambda: order.append("plugins"))
    code = desktop.main(["--no-open", "--no-token", "--no-plugins", "--project-root", str(tmp_path / "root")])
    assert code == 1
    assert order == ["extra-site", "server-thread"]
    assert os.environ["STORYSTUDIO_PLUGINS"] == "off" and plugins.disabled()


def test_native_window_failure_falls_back_to_the_browser(tmp_path, monkeypatch, capsys):
    """装了 pywebview 但原生窗口起不来（Linux 上缺 Qt / GTK 绑定时 webview.start 抛异常）：改用系统浏览器，不整个退出。"""

    import sys
    import types

    _stub_startup(monkeypatch, tmp_path, [])
    monkeypatch.setattr(desktop, "_wait_ready", lambda *a, **k: "ok")

    class FakeThread:
        def __init__(self, **kw):
            pass

        def start(self):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(desktop.threading, "Thread", FakeThread)
    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: None

    def start(**kw):
        raise RuntimeError("no GUI backend")

    fake.start = start
    monkeypatch.setitem(sys.modules, "webview", fake)
    opened: list[str] = []
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)
    code = desktop.main(["--no-token", "--project-root", str(tmp_path / "root")])
    assert code == 0 and opened and opened[0].startswith("http://127.0.0.1:8799")
    assert "改用系统浏览器" in capsys.readouterr().err
