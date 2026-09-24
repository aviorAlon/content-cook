"""发布前安全自查（2026-09-23）·本机服务：启动器探活、Host / Origin 来路、令牌 cookie、接口文档、换地址不带走旧密钥、
日志与任务记录脱敏（SEC-1 ~ SEC-6）。

不连外网、不花钱：「对方服务器」是本进程里绑在系统分配端口上的 http.server；uvicorn 也只绑 127.0.0.1 的临时端口。
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import socket
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import settings as S
from genkit.logmask import MaskingFilter, MaskingFormatter, mask
from webui import app as app_module
from webui import deps, tasks
from webui.app import app

FAKE_KEY = "sk-proj-AUDITFAKEKEY-0123456789abcdefghijklmnop"


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    for role in ("TEXT", "IMAGE", "VOICE", "VIDEO", "MUSIC"):
        for key in ("ADAPTER", "BASE_URL", "API_KEY", "MODEL", "VOICE"):
            monkeypatch.delenv(f"STORYSTUDIO_{role}_{key}", raising=False)
    return tmp_path


# --------------------------------------------------------------------------- SEC-1：默认（带令牌）启动时启动器认得出自己


def _serve_on_ephemeral_port():
    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", log_config=None, lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    return server, thread, port


def test_launcher_probe_sends_the_token_to_a_real_token_gated_server(monkeypatch, isolated_settings):
    """真起一个带令牌门的服务：探活带上令牌是 ok；不带令牌拿到自己的 401，还是 foreign（以前启动器就是这么把自己当成别人的）。"""

    from studio import desktop

    desktop._SERVE_ERROR.clear()
    monkeypatch.setenv("STORYSTUDIO_TOKEN", "launcher-token")
    server, thread, port = _serve_on_ephemeral_port()
    base = f"http://127.0.0.1:{port}/"
    try:
        assert desktop._probe_identity(base, "launcher-token") == "ok"
        assert desktop._wait_ready(base, timeout=5, token="launcher-token") == "ok"
        assert desktop._probe_identity(base) == "foreign"
    finally:
        server.should_exit = True
        thread.join(5)


def test_main_hands_its_token_to_the_readiness_probe(monkeypatch, tmp_path, capsys):
    """desktop.main 生成的令牌要原样交给 _wait_ready；--no-token 时醒目地提示一句。"""

    from studio import desktop

    monkeypatch.chdir(tmp_path)                                   # main 会 chdir 到数据目录
    monkeypatch.setenv("STORYSTUDIO_TOKEN", "")                   # main 直接改 os.environ：先登记，测完还原
    monkeypatch.setenv("STORYSTUDIO_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(desktop, "_setup_logging", lambda: tmp_path / "log.txt")
    monkeypatch.setattr(desktop, "_extend_path_for_ffmpeg", lambda: None)
    monkeypatch.setattr(desktop, "port_in_use", lambda port, host="127.0.0.1": False)

    class _Thread:
        def __init__(self, **_kw):
            pass

        def start(self):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(desktop.threading, "Thread", _Thread)
    seen: list[str] = []

    def fake_wait(url, timeout=30.0, token=""):
        seen.append(token)
        return "ok"

    monkeypatch.setattr(desktop, "_wait_ready", fake_wait)
    assert desktop.main(["--no-open", "--port", "8861", "--project-root", str(tmp_path / "root")]) == 0
    assert seen and seen[0] and seen[0] == os.environ["STORYSTUDIO_TOKEN"]
    assert "--no-token" not in capsys.readouterr().err

    seen.clear()
    assert desktop.main(["--no-open", "--no-token", "--port", "8861", "--project-root", str(tmp_path / "root")]) == 0
    assert seen == [""]
    assert "已关闭本机访问令牌" in capsys.readouterr().err


# --------------------------------------------------------------------------- SEC-2：Host / Origin 来路（令牌开没开都查）


@pytest.mark.parametrize("host", ["x.rebind.attacker.example:8756", "127.0.0.1.evil.com", "evil.com"])
def test_foreign_host_is_rejected_everywhere(monkeypatch, isolated_settings, host):
    monkeypatch.delenv("STORYSTUDIO_TOKEN", raising=False)       # --no-token 也要拦
    c = TestClient(app)
    for path in ("/api/health", "/"):
        r = c.get(path, headers={"Host": host})
        assert r.status_code == 403, (path, r.text[:200])
    assert "project_root" not in c.get("/api/health", headers={"Host": host}).text


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "null"},
])
def test_cross_site_writes_are_rejected_and_change_nothing(monkeypatch, isolated_settings, headers):
    monkeypatch.delenv("STORYSTUDIO_TOKEN", raising=False)
    c = TestClient(app)
    ok = c.put("/api/settings", json={"roles": {"text": {"adapter": "openai_chat", "base_url": "https://api.openai.com/v1"}}})
    assert ok.status_code == 200
    r = c.put("/api/settings", json={"roles": {"text": {"base_url": "http://127.0.0.1:9/v1"}}}, headers=headers)
    assert r.status_code == 403, r.text[:200]
    stored = json.loads((isolated_settings / "settings.json").read_text(encoding="utf-8"))
    assert stored["roles"]["text"]["base_url"] == "https://api.openai.com/v1"
    assert c.post("/api/system/local-hint/reset", headers=headers).status_code == 403


@pytest.mark.parametrize("headers", [
    {},                                                                               # curl / 启动器：不带来路头
    {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},                 # 本服务自己的页面
    {"Host": "localhost:5173", "Origin": "http://localhost:5173"},                    # 反代配成 changeOrigin: false
    # vite 开发代理的字符串写法（proxy: {'/api': 'http://127.0.0.1:8765'}）展开成 changeOrigin: true：Host 改成后端地址，
    # Origin 还是 vite 页面的——开发态（没开令牌）必须放行，不然开发时所有写操作都 403
    {"Host": "127.0.0.1:8765", "Origin": "http://localhost:5173", "Sec-Fetch-Site": "same-origin"},
    {"Host": "127.0.0.1:8756", "Origin": "http://127.0.0.1:8756", "Sec-Fetch-Site": "same-origin"},
    {"Origin": "http://[::1]:5173"},
    {"Host": "[::1]:8756"},
    {"Sec-Fetch-Site": "none"},                                                       # 地址栏直接打开
])
def test_legitimate_local_requests_still_work(monkeypatch, isolated_settings, headers):
    monkeypatch.delenv("STORYSTUDIO_TOKEN", raising=False)
    c = TestClient(app)
    assert c.get("/api/health", headers=headers).status_code == 200
    assert c.post("/api/system/local-hint/reset", headers=headers).status_code == 200


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1.evil.com:8765", "null",
                                    "chrome-extension://abcdef", "file://", "http://localhost.evil.example"])
def test_dev_mode_still_rejects_non_loopback_origins(monkeypatch, isolated_settings, origin):
    """没开令牌时 Origin 放宽到「回环地址的 http(s) 源」，别的一律 403（包括 null、扩展页、file://、借名的域名）。"""

    monkeypatch.delenv("STORYSTUDIO_TOKEN", raising=False)
    c = TestClient(app)
    r = c.post("/api/system/local-hint/reset", headers={"Host": "127.0.0.1:8765", "Origin": origin})
    assert r.status_code == 403, (origin, r.text[:200])


def test_token_mode_requires_the_exact_own_origin(monkeypatch, isolated_settings):
    """开着令牌（打包版默认）时 Origin 要跟本服务的源逐字相同：vite 代理那种「Host 是后端、Origin 是别的回环端口」也拦——
    不认 Fetch Metadata 的老浏览器上，127.0.0.1 别的端口的页面带着 cookie 只能靠这一条挡。"""

    monkeypatch.setenv("STORYSTUDIO_TOKEN", "sekrit")
    c = TestClient(app)
    auth = {"X-StoryStudio-Token": "sekrit", "Host": "127.0.0.1:8765"}
    assert c.post("/api/system/local-hint/reset", headers={**auth, "Origin": "http://127.0.0.1:8765"}).status_code == 200
    for origin in ("http://localhost:5173", "http://127.0.0.1:9999", "http://localhost:8765"):
        r = c.post("/api/system/local-hint/reset", headers={**auth, "Origin": origin, "Sec-Fetch-Site": "same-origin"})
        assert r.status_code == 403, origin


def test_non_json_body_is_422_not_500_and_does_not_echo_the_body(monkeypatch, isolated_settings):
    """text/plain / 表单体打到 JSON 接口：以前 errors 里的 input 是 bytes，JSONResponse 序列化不了，整个请求 500。"""

    monkeypatch.delenv("STORYSTUDIO_TOKEN", raising=False)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.put("/api/settings", content=f"api_key={FAKE_KEY}".encode(), headers={"Content-Type": "text/plain"})
    assert r.status_code == 422 and "请求参数不对" in r.json()["detail"]
    assert FAKE_KEY not in r.text
    r = c.post("/api/voices", content=b"a=1", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 422 and isinstance(r.json()["errors"], list)


def test_hostname_parsing():
    assert app_module._hostname("127.0.0.1:8756") == "127.0.0.1"
    assert app_module._hostname("LOCALHOST") == "localhost"
    assert app_module._hostname("[::1]:8756") == "::1"
    assert app_module._hostname("a.rebind.example:8756") == "a.rebind.example"


# --------------------------------------------------------------------------- SEC-4：令牌 cookie 按端口起名、别的页面带着 cookie 也不行


def test_token_cookie_is_named_per_port_and_other_origins_cannot_ride_it(monkeypatch, isolated_settings):
    monkeypatch.setenv("STORYSTUDIO_TOKEN", "sekrit")
    c = TestClient(app)
    assert c.get("/?token=sekrit").status_code == 200
    assert c.cookies.get("storystudio_token_80") == "sekrit" and c.cookies.get("storystudio_token") is None
    assert c.get("/api/health").status_code == 200
    # 127.0.0.1 上别的端口的页面：对浏览器来说同站，cookie 照带——按来路拦
    for headers in ({"Origin": "http://127.0.0.1:9999", "Sec-Fetch-Site": "same-site"}, {"Origin": "http://127.0.0.1:9999"},
                    {"Sec-Fetch-Site": "same-site"}):
        assert c.post("/api/system/local-hint/reset", headers=headers).status_code == 403, headers
    # 老名字的 cookie 不再认
    fresh = TestClient(app)
    fresh.cookies.set("storystudio_token", "sekrit")
    assert fresh.get("/api/health").status_code == 401


def test_cookie_name_uses_the_listening_port():
    class _Req:
        def __init__(self, port):
            self.scope = {"server": ("127.0.0.1", port)}
            self.url = None

    assert app_module._token_cookie_name(_Req(8756)) != app_module._token_cookie_name(_Req(8757))
    assert app_module._token_cookie_name(_Req(8756)) == "storystudio_token_8756"


def test_junk_token_is_401_not_500(monkeypatch, isolated_settings):
    monkeypatch.setenv("STORYSTUDIO_TOKEN", "sekrit")
    c = TestClient(app)
    # 非 ASCII 的令牌：compare_digest 拿 str 比会抛 TypeError（500），按字节比就是普通的 401
    assert c.get("/api/health", headers={"X-StoryStudio-Token": "令牌".encode("utf-8")}).status_code == 401
    assert c.get("/?token=令牌").status_code == 200 and not c.cookies
    assert app_module._token_matches("sekrit", "sekrit") and not app_module._token_matches("令牌", "sekrit")


# --------------------------------------------------------------------------- SEC-5：接口文档在令牌门后面


def test_api_docs_are_behind_the_token(monkeypatch, isolated_settings):
    monkeypatch.setenv("STORYSTUDIO_TOKEN", "sekrit")
    c = TestClient(app)
    for path in ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"):
        assert c.get(path).status_code == 401, path
    c.get("/?token=sekrit")
    assert c.get("/openapi.json").status_code == 200
    monkeypatch.delenv("STORYSTUDIO_TOKEN")
    assert TestClient(app).get("/openapi.json").status_code == 200


# --------------------------------------------------------------------------- SEC-3：只改地址，旧密钥不跟过去


class _Capture(http.server.BaseHTTPRequestHandler):
    seen: list[str | None] = []

    def _reply(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        _Capture.seen.append(self.headers.get("Authorization"))
        self._reply({"object": "list", "data": []})

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _Capture.seen.append(self.headers.get("Authorization"))
        self._reply({"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                     "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}]})

    def log_message(self, *_a):
        pass


def test_changing_base_url_does_not_ship_the_old_key_to_the_new_host(monkeypatch, isolated_settings):
    monkeypatch.delenv("STORYSTUDIO_TOKEN", raising=False)
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Capture.seen = []
    c = TestClient(app)
    try:
        c.put("/api/settings", json={"roles": {"text": {"adapter": "openai_chat", "base_url": "https://api.openai.com/v1",
                                                        "api_key": FAKE_KEY, "model": "gpt-4o-mini"}}})
        masked = c.get("/api/settings").json()["settings"]["roles"]["text"]["api_key"]
        assert masked and masked != FAKE_KEY
        # 表单回传打码值 + 新地址（设置页的「保存」就是这个形状）
        c.put("/api/settings", json={"roles": {"text": {"adapter": "openai_chat", "base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1",
                                                        "api_key": masked, "model": "gpt-4o-mini"}}})
        c.post("/api/settings/test/text")
    finally:
        srv.shutdown()
    stored = json.loads((isolated_settings / "settings.json").read_text(encoding="utf-8"))["roles"]["text"]["api_key"]
    assert f"Bearer {FAKE_KEY}" not in _Capture.seen
    assert stored == ""


def _role(target: Path) -> S.RoleConfig:
    return S.load_settings(target, apply_env=False).role("text")


@pytest.mark.parametrize("new_base", [
    "https://api.openai.com/v1/",          # 尾斜杠
    "https://API.openai.com/v1",           # 主机大小写
    "https://api.openai.com",              # 去掉 /v1（只改路径）
    "",                                    # 留空 = 适配器默认地址，还是同一台主机
])
def test_same_origin_edits_keep_the_key(isolated_settings, new_base):
    target = isolated_settings / "settings.json"
    S.update_settings({"roles": {"text": {"adapter": "openai_chat", "base_url": "https://api.openai.com/v1", "api_key": FAKE_KEY}}}, target)
    S.update_settings({"roles": {"text": {"base_url": new_base, "api_key": "sk-p***mnop"}}}, target)
    assert _role(target).api_key == FAKE_KEY
    # 不带 api_key 的保存（只改模型）也不动
    S.update_settings({"roles": {"text": {"model": "gpt-4o"}}}, target)
    assert _role(target).api_key == FAKE_KEY


@pytest.mark.parametrize("new_base", ["https://evil.example/v1", "http://api.openai.com/v1", "https://api.openai.com:8443/v1"])
@pytest.mark.parametrize("sent", [{"api_key": "sk-p***mnop"}, {"api_key": ""}, {}])
def test_other_origin_clears_the_key_unless_a_new_one_is_given(isolated_settings, new_base, sent):
    target = isolated_settings / "settings.json"
    S.update_settings({"roles": {"text": {"adapter": "openai_chat", "base_url": "https://api.openai.com/v1", "api_key": FAKE_KEY}}}, target)
    S.update_settings({"roles": {"text": {"base_url": new_base, **sent}}}, target)
    assert _role(target).api_key == ""
    S.update_settings({"roles": {"text": {"base_url": new_base, "api_key": "sk-new-key-0123456789"}}}, target)
    assert _role(target).api_key == "sk-new-key-0123456789"


def test_default_address_counts_as_the_previous_host(isolated_settings):
    """没填地址（用适配器默认的 api.openai.com）时存的密钥，改成别的主机同样要清。"""

    target = isolated_settings / "settings.json"
    S.update_settings({"roles": {"text": {"adapter": "openai_chat", "api_key": FAKE_KEY}}}, target)
    S.update_settings({"roles": {"text": {"base_url": "https://evil.example/v1", "api_key": "sk-p***mnop"}}}, target)
    assert _role(target).api_key == ""


def test_gateway_host_change_clears_its_key(isolated_settings):
    target = isolated_settings / "settings.json"
    S.update_settings({"gateway": {"base_url": "http://127.0.0.1:4000/v1", "api_key": FAKE_KEY}}, target)   # 第一次连地址带密钥
    assert S.load_settings(target, apply_env=False).gateway.api_key == FAKE_KEY
    S.update_settings({"gateway": {"base_url": "http://127.0.0.1:4000/v1/", "api_key": "sk-p***mnop"}}, target)
    assert S.load_settings(target, apply_env=False).gateway.api_key == FAKE_KEY
    S.update_settings({"gateway": {"base_url": "https://evil.example/v1", "api_key": "sk-p***mnop"}}, target)
    assert S.load_settings(target, apply_env=False).gateway.api_key == ""


# --------------------------------------------------------------------------- SEC-6：日志与任务记录脱敏


def _chained_provider_error():
    from genkit.providers import ProviderCommandError

    try:
        try:
            raise RuntimeError(f"401 {{'error': 'Incorrect API key provided: {FAKE_KEY}'}}")
        except RuntimeError as exc:
            # 跟 openai_compat.py / ark_images.py 同一写法：上游报错原文拼进人话，再 from exc
            raise ProviderCommandError(f"https://gw.example/v1 拒绝了这个密钥（401）：{exc}——检查 api_key") from exc
    except ProviderCommandError as outer:
        return outer


def test_desktop_log_file_masks_the_whole_exception_chain(monkeypatch, tmp_path):
    from studio import desktop

    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    log_path = desktop._setup_logging()
    added = [h for h in root.handlers if h not in before]
    exc = _chained_provider_error()
    try:
        try:
            raise exc
        except Exception:
            logging.getLogger("audit.chain").exception("task failed with %s", FAKE_KEY)
    finally:
        for handler in added:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(level)
    text = log_path.read_text(encoding="utf-8")
    assert "task failed" in text and "The above exception was the direct cause" in text
    assert FAKE_KEY not in text
    # 过滤器不再改业务对象：调用方拿着的异常原文不变
    assert FAKE_KEY in str(exc.__cause__)


def test_masking_formatter_and_filter_on_a_plain_handler():
    import io

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(MaskingFormatter("%(levelname)s %(message)s"))
    handler.addFilter(MaskingFilter())
    logger = logging.getLogger("audit.formatter")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        try:
            raise _chained_provider_error()
        except Exception:
            logger.exception("boom")
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
    assert FAKE_KEY not in buf.getvalue() and "boom" in buf.getvalue()


@pytest.mark.parametrize("line, secret", [
    ("GET https://generativelanguage.googleapis.com/v1beta/models?key=AIzaSyA1234567890abcdefghijklmnopqrstu",
     "AIzaSyA1234567890abcdefghijklmnopqrstu"),
    ("invalid api key: 3f2b9c1e-7a4d-4e8b-9c3f-1a2b3c4d5e6f", "3f2b9c1e-7a4d-4e8b-9c3f-1a2b3c4d5e6f"),
    ("{'api_key': 'ark-0123456789abcdef', 'model': 'x'}", "ark-0123456789abcdef"),
    ('{"api_key": "ark-0123456789abcdef"}', "ark-0123456789abcdef"),
    ("https://x.example/v1/files?access_token=abcdef0123456789&x=1", "abcdef0123456789"),
    ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
     "eyJzdWIiOiIxMjM0NTY3ODkwIn0"),
])
def test_mask_covers_documented_key_shapes(line, secret):
    assert secret not in mask(line)


@pytest.mark.parametrize("text", [
    "task 0123456789abcdef0123456789abcdef failed",       # 本地任务 id
    "cgt-20260923123456-abcde remote task",                # 远端任务 id
    "C:/Users/someone/StoryStudio/output/story_video/EP1/02_video/S1.mp4",
    "cache_key=abcdef0123456789",
    "3f2b9c1e-7a4d-4e8b-9c3f-1a2b3c4d5e6f",                # 单独出现的 UUID 多半是素材 / 任务 id，不遮
])
def test_mask_leaves_troubleshooting_ids_alone(text):
    assert mask(text) == text


def test_failed_task_record_does_not_keep_the_key(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)

    def fail():
        raise _chained_provider_error()

    task_id = tasks.start("audit-mask", "AUDIT-1", fail)
    deadline = time.time() + 10
    while (tasks.get(task_id) or {}).get("status") not in ("failed", "succeeded") and time.time() < deadline:
        time.sleep(0.05)
    raw = (tmp_path / ".webui_draft_cache" / "tasks" / f"{task_id}.json").read_text(encoding="utf-8")
    record = json.loads(raw)
    assert record["status"] == "failed" and "拒绝了这个密钥" in record["error"] and record["traceback"]
    assert FAKE_KEY not in raw
