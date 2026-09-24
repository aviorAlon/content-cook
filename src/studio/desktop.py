"""桌面启动器：在本机起 FastAPI（uvicorn）后用 pywebview 开一个原生窗口；没有 pywebview 就退回系统浏览器。

    python -m studio.desktop            # 开发态
    storystudio                          # 安装后的命令
    storystudio --version
打包成桌面客户端见 desktop/README.md（PyInstaller，spec 在 desktop/storystudio.spec）。

目录约定
--------
* 用户数据目录（剧集 spec、output、账本）：``--project-root`` > 环境变量
  ``STORYSTUDIO_PROJECT_ROOT`` > 打包版默认 ``~/StoryStudio`` > 开发态当前目录。
  打包版不能默认写在 exe 旁边——exe 通常在只读或不该放用户数据的位置。
* 设置与缓存：``~/.storystudio/``（见 genkit.settings.home_dir）。
* 插件：``~/.storystudio/plugins/<名字>/``，外加环境变量 ``STORYSTUDIO_PLUGIN_PATH`` 列的目录（见 genkit.plugins）；
  ``--no-plugins`` 一个都不加载。
* 日志：``~/.storystudio/logs/storystudio.log``。打包版没有控制台，这是唯一能看到
  服务端报错的地方。
* ffmpeg：打包版把 exe 所在目录和它下面的 ``ffmpeg/`` 加进 PATH，用户把
  ffmpeg.exe / ffprobe.exe 放进去就能用；缺失时界面顶部会提示。
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# 产品名唯一来源在 webui/branding.py（DESIGN §38）。studio 与 webui 是同一套源码里的两个包（安装态、打包态、
# 从检出目录 python -m 起，三种方式 src 都已在 sys.path），import 失败只可能是环境坏了，兜底一个同样的字符串
# 保证启动器至少还能打印/开窗。
try:
    from webui.branding import PRODUCT_NAME as APP_TITLE
except ImportError:  # pragma: no cover - 只在 webui 不可导入的坏环境里触发
    APP_TITLE = "Content Cook · 内容创作工作台"


def is_frozen() -> bool:
    return bool(getattr(sys, "_MEIPASS", None))


def _free_port(preferred: int = 8756) -> int:
    for port in (preferred, *range(8757, 8800)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free local port between 8756 and 8799")


def _code_root() -> Path:
    """打包后（PyInstaller）代码资源在 sys._MEIPASS；开发态是仓库根。"""

    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parents[2]


def default_project_root() -> Path:
    explicit = os.environ.get("STORYSTUDIO_PROJECT_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if is_frozen():
        return (Path.home() / "StoryStudio").resolve()
    return Path.cwd()


def _home_dir() -> Path:
    return Path(os.environ.get("STORYSTUDIO_HOME") or (Path.home() / ".storystudio")).expanduser()


class _IgnoreClientDisconnect(logging.Filter):
    """Windows 的 asyncio proactor 在客户端（浏览器/健康检查）主动断开时会往日志里
    甩一整段 ConnectionResetError 10054 的堆栈——不是故障，只是对方先挂了。过滤掉，
    免得用户翻日志时把它当成出错原因。"""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, ConnectionResetError):
            return False
        return "10054" not in record.getMessage()


def _setup_logging() -> Path:
    log_dir = _home_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "storystudio.log"
    from genkit.logmask import MaskingFilter, MaskingFormatter

    handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    # 密钥、Bearer、data URL 不落日志：格式化器对整条文本（含整条异常链的堆栈）遮，过滤器只管消息
    handler.setFormatter(MaskingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_IgnoreClientDisconnect())
    handler.addFilter(MaskingFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # 打包版（console=False）没有 stderr；开发态保留控制台输出
    if sys.stderr is not None and not is_frozen():
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(MaskingFormatter())
        console.setLevel(logging.WARNING)
        root.addHandler(console)
    return log_path


def _load_extra_site() -> None:
    """打包版：把可选组件目录（faster-whisper 一族，见 genkit.paths.extra_site_dir）接进 sys.path。

    冻结解释器忽略 PYTHONPATH、exe 里没有 pip，这是桌面版唯一能用上台词识别闸的路——「一键安装」把包装进
    这个目录，下次启动在这里接上（当次安装完由接口自己接）。开发态不接：源码方式跑有 pip，直接装进当前 Python。"""

    if not is_frozen():
        return
    from genkit.paths import activate_extra_site, extra_site_dir, extra_site_python_mismatch

    log = logging.getLogger("storystudio")
    try:
        folder = activate_extra_site()
    except Exception:  # noqa: BLE001 - 可选组件，目录读不了也不能拦住启动；设置页照样显示「未安装」和安装提示
        log.exception("extra site-packages not loaded")
        return
    if folder:
        log.info("extra site-packages loaded: %s", folder)
    elif extra_site_dir().is_dir():
        log.warning("extra site-packages ignored: %s was installed for Python %s, this build runs %d.%d",
                    extra_site_dir(), extra_site_python_mismatch(extra_site_dir()), *sys.version_info[:2])


def _load_plugins(disabled: bool = False) -> None:
    """加载 ``~/.storystudio/plugins`` 里的插件（genkit.plugins），每个插件的结果写进日志。

    要在 :func:`_load_extra_site` 之后（插件可以用那里装的第三方包）、服务线程 import webui 之前：设置页、
    健康检查、服务一起来就续等的在飞任务都要看得到插件登记的来源。``disabled``（--no-plugins）一个都不加载。
    插件怎么坏都只记日志，不拦启动。"""

    log = logging.getLogger("storystudio")
    if disabled:
        os.environ["STORYSTUDIO_PLUGINS"] = "off"
        log.info("plugins disabled (--no-plugins)")
        return
    try:
        from genkit import plugins

        plugins.ensure_loaded()
        for record in plugins.records():
            if record.loaded:
                log.info("plugin %s %s loaded from %s", record.name, record.version, record.path)
            else:
                log.warning("plugin %s %s: %s", record.name, record.status, record.message)
    except Exception:  # noqa: BLE001 - 插件是可选的，加载器本身出错也不能拦住启动
        log.exception("plugins not loaded")


def _extend_path_for_ffmpeg() -> None:
    """打包版：exe 所在目录及其 ffmpeg/ 子目录优先于系统 PATH。"""

    if not is_frozen():
        return
    exe_dir = Path(sys.executable).resolve().parent
    candidates = [exe_dir, exe_dir / "ffmpeg", exe_dir / "ffmpeg" / "bin"]
    current = os.environ.get("PATH", "")
    extra = os.pathsep.join(str(p) for p in candidates if p.is_dir())
    if extra:
        os.environ["PATH"] = extra + os.pathsep + current


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """这个端口此刻有没有人在听。用连接探测而不是 bind 试探：Windows 上别人以
    SO_EXCLUSIVEADDRUSE 占着时 bind 也会失败，但连接能连上，两者都算「被占」。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def port_owner(port: int) -> str:
    """占着这个端口的进程是谁（拿不到就返回空串）。只用于把报错说清楚，失败不影响流程。"""

    try:
        if sys.platform != "win32":
            return ""
        import re
        import subprocess

        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        pid = ""
        for line in out.splitlines():
            if f":{port} " in line and "LISTENING" in line.upper():
                pid = line.split()[-1]
                break
        if not pid:
            return ""
        task = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                              text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        name = re.findall(r'"([^"]+)"', task)
        return f"{name[0]} (PID {pid})" if name else f"PID {pid}"
    except Exception:  # noqa: BLE001 - 诊断信息拿不到就算了
        return ""


# 服务线程里 uvicorn 抛出来的异常（端口被占、证书、权限……）。主线程只能靠它知道服务没起来。
_SERVE_ERROR: list[BaseException] = []


def _serve(port: int) -> None:
    import uvicorn

    from webui.app import app

    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
    except BaseException as exc:  # noqa: BLE001 - 记下来给主线程报错，别在后台线程里静静地死掉
        _SERVE_ERROR.append(exc)
        logging.getLogger("storystudio").exception("server thread died on port %s", port)


def _probe_identity(base_url: str, token: str = "") -> str:
    """探一次 ``/api/health``：``"ok"`` = 是我们；``"foreign"`` = 端口上是别的程序；``""`` = 还没起来。

    只看「有没有回应」是不够的——端口可能被另一个程序占着，老实现会一直等到超时；
    占端口的程序要是对 ``/`` 返回 200，启动器还会把别人的界面当成自己的打开。

    ``token``：本机访问令牌。/api/health 在令牌门后面，默认（带令牌）启动时探活不带它，拿到的是自己服务的 401，
    会被当成「端口上是别的程序」直接退出——打包版每次启动都这样（2026-09-23 发布前自查 SEC-1）。"""

    headers = {"X-StoryStudio-Token": token} if token else {}
    request = urllib.request.Request(base_url.rstrip("/") + "/api/health", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            body = response.read(4096).decode("utf-8", "replace")
        return "ok" if '"storystudio"' in body else "foreign"
    except urllib.error.HTTPError:
        return "foreign"           # 有人应答，但不是我们的接口
    except Exception:  # noqa: BLE001 - 连不上 = 还没起来
        return ""


def _wait_ready(url: str, timeout: float = 30.0, token: str = "") -> str:
    """等服务起来。返回 ``"ok"`` / ``"foreign"``（端口被别人占）/ ``"dead"``（服务线程挂了）/ ``"timeout"``。
    ``token`` 原样交给 :func:`_probe_identity`。"""

    deadline = time.time() + timeout
    while time.time() < deadline:
        state = _probe_identity(url, token)
        if state:
            return state
        if _SERVE_ERROR:
            return "dead"
        time.sleep(0.3)
    return "timeout"


def main(argv: list[str] | None = None) -> int:
    from genkit.version import __version__

    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--version", action="version", version=f"storystudio {__version__}")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--browser", action="store_true", help="用系统浏览器打开，不开原生窗口")
    parser.add_argument("--no-open", action="store_true", help="只起服务，不打开任何窗口")
    parser.add_argument("--no-token", action="store_true", help="不启用本机访问令牌（只建议在调试接口时用）")
    parser.add_argument("--project-root", default=None, help="项目数据目录（剧集 spec、output）；打包版默认 ~/StoryStudio，开发态默认当前目录")
    parser.add_argument("--no-plugins", action="store_true", help="不加载 ~/.storystudio/plugins 里的插件（排查问题时用）")
    args = parser.parse_args(argv)

    log_path = _setup_logging()
    _extend_path_for_ffmpeg()

    root = Path(args.project_root).expanduser().resolve() if args.project_root else default_project_root()
    os.environ["STORYSTUDIO_PROJECT_ROOT"] = str(root)
    src = _code_root() / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    _load_extra_site()   # 要在服务线程 import webui 之前：readiness / health 起来第一眼就要看得到 faster-whisper
    _load_plugins(args.no_plugins)   # extra-site 之后（插件能用那里的包）、服务线程之前（续等的在飞任务要找得到来源）
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    (root / "content" / "story_video").mkdir(parents=True, exist_ok=True)
    (root / "output").mkdir(parents=True, exist_ok=True)

    token = "" if args.no_token else secrets.token_urlsafe(24)
    if token:
        os.environ["STORYSTUDIO_TOKEN"] = token
    else:
        os.environ.pop("STORYSTUDIO_TOKEN", None)
        # 关掉令牌之后本机任何进程都能调接口（花钱、读写项目、读设置）：只该在调试接口时用，醒目地说一声
        warning = "已关闭本机访问令牌（--no-token）：同一台电脑上的任何程序都能调用接口、花钱、读写项目——只在调试接口时这样用"
        print(warning, file=sys.stderr)
        logging.getLogger("storystudio").warning(warning)

    port = args.port or _free_port()
    # 端口被占：指定了 --port 就直接报清楚（谁占的、换哪个端口），不要干等三十秒
    if args.port and port_in_use(port):
        owner = port_owner(port)
        spare = _free_port()
        who = f"（{owner}）" if owner else ""
        print(f"端口 {port} 已经有别的程序在听{who}——换一个端口：--port {spare}，或者先停掉那个程序。",
              file=sys.stderr)
        logging.getLogger("storystudio").error("port %s busy: %s", port, owner or "unknown")
        return 2
    base_url = f"http://127.0.0.1:{port}/"
    url = f"{base_url}?token={token}" if token else base_url
    logging.getLogger("storystudio").info("starting v%s on %s (project root: %s)", __version__, base_url, root)
    thread = threading.Thread(target=_serve, args=(port,), daemon=True, name="storystudio-server")
    thread.start()
    state = _wait_ready(base_url, token=token)
    if state != "ok":
        spare = _free_port()
        reason = {
            "foreign": f"{port} 端口上是别的程序在应答，不是本工作台——换一个端口：--port {spare}，或者先停掉那个程序。",
            "dead": f"服务没能起来：{_SERVE_ERROR[0] if _SERVE_ERROR else '未知原因'}（换个端口试试：--port {spare}）",
            "timeout": f"服务在 {base_url} 上一直没起来（日志：{log_path}）",
        }[state]
        logging.getLogger("storystudio").error("server not ready at %s: %s", base_url, state)
        print(reason, file=sys.stderr)
        return 1
    print(f"{APP_TITLE} v{__version__}: {url}\n  项目目录: {root}\n  日志: {log_path}")
    if args.no_open:
        try:
            while thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0
    if not args.browser:
        try:
            import webview  # pywebview

            webview.create_window(APP_TITLE, url, width=1440, height=920, min_size=(1024, 700))
            # 窗口 / 任务栏图标：打包版 pywebview 会从 exe 里抽（PyInstaller 已嵌入），开发态 sys.executable 是
            # python.exe，得自己指一份，不然标题栏顶着 Python 的图标
            icon = _code_root() / "desktop" / "storystudio.ico"
            webview.start(icon=str(icon) if icon.is_file() else None)
            return 0
        except ImportError:
            print("pywebview 未安装，改用系统浏览器。pip install pywebview 可得到原生窗口。", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - 原生窗口起不来（如 Linux 上缺 Qt / GTK 的 Python 绑定）就改用系统浏览器，别让启动器整个退出
            logging.getLogger("storystudio").warning("native window failed, falling back to the browser: %s", exc)
            print(f"原生窗口起不来（{exc}），改用系统浏览器。", file=sys.stderr)
    webbrowser.open(url)
    try:
        while thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
