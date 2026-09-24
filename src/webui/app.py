"""FastAPI 后端：本地预览/编辑/生产台。

只暴露已有的纯函数和已有的 StoryVideoRunner 状态机，不重新实现 spec 解析、
提示词编译或付费提交/去重逻辑。写回文件只发生在 storyvideo 项目的「未冻结」
草稿上，而且写之前先用 ``normalize_spec`` 校验一遍，校验不过直接拒绝、不落盘。

真正花钱的只有一个端点（``POST .../generate``），而且要求先拿到一份跟当前
磁盘状态匹配的 ``plan_token``——防止界面上一次意外点击/重复请求在人没看清楚
「这次要新提交几个」之前就把钱花出去。见 generate.py 的模块说明。

这个文件只负责组装 app（挂路由、挂静态资源、首页、本机访问令牌）——实际的
端点实现按职责拆到 ``routers/`` 下（episodes / config / scripts / settings /
system），共用的路径常量和纯函数辅助在 ``deps.py``。

本机访问令牌
------------
服务只绑 127.0.0.1，但同一台机器上的其他进程（浏览器里打开的任意网页发起的
请求也算）仍然能打到这个端口——而这里的端点能花钱、能读写用户目录。桌面启动器
会生成一个随机令牌放进环境变量 ``STORYSTUDIO_TOKEN``，首次打开 ``/?token=…``
时种成 cookie，之后所有 ``/api/*`` 请求都必须带这个 cookie（或
``X-StoryStudio-Token`` 头）。没设这个环境变量（开发态、测试）就不拦。

令牌开没开都先查来路（2026-09-23 发布前自查 SEC-2 / SEC-4）：Host 不是回环地址（DNS rebinding）一律 403；
``/api/*`` 请求被浏览器标成别的页面发起（Sec-Fetch-Site 为 cross-site / same-site，或 Origin 不对：开着令牌时要跟本服务的源
逐字相同，没开令牌的开发态只要求是回环地址——vite 开发代理改 Host 不改 Origin）也 403。
cookie 名带端口（``storystudio_token_<端口>``）：cookie 不分端口，同机两个实例不再互相覆盖。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
import secrets
import sys
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles

from genkit import settings as settings_mod
from genkit.paths import resource

from .branding import PRODUCT_NAME
from .deps import STATIC_DIR
from .i18n import get_lang, lang_from_headers, set_lang, tr, translate_json
from .routers import assist as assist_router
from .routers import clip as clip_router
from .routers import compare as compare_router
from .routers import config as config_router
from .routers import episodes as episodes_router
from .routers import export as export_router
from .routers import modules as modules_router
from .routers import music as music_router
from .routers import podcast as podcast_router
from .routers import picturebook as picturebook_router
from .routers import imageset as imageset_router
from .routers import local_models as local_models_router
from .routers import post as post_router
from .routers import poster as poster_router
from .routers import project as project_router
from .routers import recent as recent_router
from .routers import revise as revise_router
from .routers import script as script_router
from .routers import scripts as scripts_router
from .routers import series as series_router
from .routers import settings as settings_router
from .routers import style_cards as style_cards_router
from .routers import system as system_router
from .routers import trash as trash_router
from .routers import voices as voices_router

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """断点续等（DESIGN §34）：上次退出时还在服务商那边的首帧/视频任务，服务一起来就接着轮询。

    只在账本文件存在且真有在飞任务时起后台线程；测试用不带 ``with`` 的 TestClient，不会触发这里。
    """

    from genkit import plugins

    from .generate import resume_inflight_watchers

    # 插件先于续等：服务一起来就要接着查的在飞任务，可能正是插件提供的来源提交的（uvicorn webui.app:app 这种起法
    # 不经过桌面启动器，也在这里加载）。ensure_loaded 自己不抛，这里再兜一层：插件怎么坏都不能拦住服务启动
    try:
        plugins.ensure_loaded()
    except Exception:  # noqa: BLE001
        pass
    try:
        resume_inflight_watchers()
    except Exception:  # noqa: BLE001 - 续等失败不能拦住服务启动；界面上还能手动 generate-resume
        pass
    yield


_FROZEN = bool(getattr(sys, "_MEIPASS", None))
# 接口文档（/docs、/redoc、/openapi.json）把两百多个接口的结构全摆出来，打包版用不上，直接不挂（swagger 页还会从 CDN
# 拉脚本进本机源）；开发态留着，但跟 /api 一样在令牌门后面（见 local_token_guard）。_MEIPASS 在 import 时就定了，不会跟测试抢环境变量
app = FastAPI(title=f"{PRODUCT_NAME}（本地版）", lifespan=_lifespan,
              **({"docs_url": None, "redoc_url": None, "openapi_url": None} if _FROZEN else {}))

TOKEN_ENV = "STORYSTUDIO_TOKEN"
TOKEN_COOKIE = "storystudio_token"
TOKEN_HEADER = "x-storystudio-token"
_DOC_PATHS = frozenset(p for p in (app.openapi_url, app.docs_url, app.redoc_url, app.swagger_ui_oauth2_redirect_url) if p)
# 只认回环地址的主机名：DNS rebinding 的页面发来的 Host 是攻击者自己的域名（a.rebind.example:8756）。testserver 是
# starlette TestClient 的默认 Host（单段名，公网页面没法让浏览器把它解析到本机），打包版不认
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"} | (set() if _FROZEN else {"testserver"}))
_FOREIGN_DETAIL = "这个本地服务只接受从本机窗口里发起的请求——这次请求来自别的网页或别的地址，已拒绝。"


def _required_token() -> str:
    return os.environ.get(TOKEN_ENV, "").strip()


def _hostname(host_port: str) -> str:
    """Host / Origin 里的主机名：小写，去掉端口；``[::1]:8756`` → ``::1``。"""

    text = str(host_port or "").strip().lower()
    if text.startswith("["):
        return text[1:text.find("]")] if "]" in text else text[1:]
    return text.rsplit(":", 1)[0] if text.count(":") == 1 else text


def _from_other_origin(request: Request, *, strict: bool) -> bool:
    """浏览器标明这个请求是别的页面发起的：Sec-Fetch-Site 是 cross-site / same-site，或 Origin 不对。

    same-site 也拦：cookie 不分端口，127.0.0.1 上别的端口的页面对浏览器来说跟本服务同站，SameSite=Strict 挡不住它带上令牌
    cookie（SEC-4）。没带这两个头的（curl、启动器探活、同源 GET）照常放行——本服务自己的界面全是同源请求。

    Origin 怎么算「对」分两档：开着令牌（``strict``，打包版默认）要跟本服务的源逐字相同——不认 Fetch Metadata 的老浏览器上，
    127.0.0.1 别的端口的页面也带着 cookie，只能靠这一条拦；没开令牌（开发态）只要求是回环地址的 http(s) 源：vite 开发代理
    （``proxy: {'/api': 'http://127.0.0.1:8765'}`` 这种字符串写法）会把 Host 改成后端地址、Origin 却还是
    ``http://localhost:5173``，逐字比的话开发态所有写请求都被 403。"""

    if request.headers.get("sec-fetch-site", "").lower() in ("cross-site", "same-site"):
        return True
    origin = request.headers.get("origin")
    if origin is None:
        return False
    origin = origin.strip().lower()
    if strict:
        return origin != f"{request.url.scheme}://{request.headers.get('host', '')}".lower()
    try:
        parts = urlsplit(origin)
    except ValueError:
        return True
    return parts.scheme not in ("http", "https") or _hostname(parts.netloc) not in _LOOPBACK_HOSTS


def _token_cookie_name(request: Request) -> str:
    """令牌 cookie 按端口起名：cookie 不分端口，同一台机器开两个实例时后打开的会把前一个的令牌盖掉。端口取服务真正在听的
    socket（不信客户端发来的 Host）。"""

    server = request.scope.get("server") or ()
    port = server[1] if len(server) > 1 and server[1] else request.url.port
    return f"{TOKEN_COOKIE}_{port}" if port else TOKEN_COOKIE


def _token_matches(presented: str, token: str) -> bool:
    """常量时间比较；按字节比——compare_digest 遇到非 ASCII 的 str 会抛 TypeError，乱填的令牌就成了 500。"""

    return secrets.compare_digest(str(presented or "").encode("utf-8", "surrogatepass"), token.encode("utf-8"))


@app.middleware("http")
async def local_token_guard(request: Request, call_next):
    # 先查来路，令牌开没开都查（--no-token 调试时同样可能开着浏览器）：Host 不是回环地址 = DNS rebinding；
    # /api 请求由别的网页发起 = 跨站请求（无 body 的 POST 不触发预检，浏览器直接送达）
    if _hostname(request.headers.get("host", "")) not in _LOOPBACK_HOSTS:
        return JSONResponse({"detail": _FOREIGN_DETAIL}, status_code=403)
    path = request.url.path
    guarded = path.startswith("/api/") or path in _DOC_PATHS
    token = _required_token()
    if guarded and _from_other_origin(request, strict=bool(token)):
        return JSONResponse({"detail": _FOREIGN_DETAIL}, status_code=403)
    if not token:
        return await call_next(request)
    cookie = _token_cookie_name(request)
    if path == "/" and _token_matches(request.query_params.get("token", ""), token):
        response = await call_next(request)
        response.set_cookie(cookie, token, httponly=True, samesite="strict")
        return response
    if guarded:
        presented = request.cookies.get(cookie) or request.headers.get(TOKEN_HEADER, "")
        if not _token_matches(presented, token):
            return JSONResponse(
                {"detail": "这个本地服务只接受从桌面窗口（或启动时打印的带 token 的地址）发起的请求。"},
                status_code=401,
            )
    return await call_next(request)


_VALIDATION_WORDS = {
    "missing": "必填，没有给",
    "string_type": "要是文本",
    "int_parsing": "要是整数",
    "float_parsing": "要是数字",
    "bool_parsing": "要是 true / false",
    "list_type": "要是数组",
    "dict_type": "要是对象",
    "json_invalid": "不是合法的 JSON",
}


def validation_message(errors: list) -> str:
    """把 pydantic 的 422 明细翻成一句人话：「字段 id：必填，没有给」。原始明细不再直接摆给用户。

    英文在这里逐段翻再拼：多个字段用「；」连成一句之后，i18n 的整句规则一条也命不中（只会翻掉前缀，
    尾巴原样中文）；逐段翻出来的英文串再经中间件过一遍 tr 也不会变。
    """

    lang = get_lang()
    parts = []
    for err in errors:
        loc = [str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path")]
        field = ".".join(loc) or "请求体"
        kind = str(err.get("type") or "")
        word = _VALIDATION_WORDS.get(kind) or str(err.get("msg") or kind)
        parts.append(tr(f"字段 {field}：{word}", lang))
    if not parts:
        return tr("请求参数不对", lang)
    return tr("请求参数不对——", lang) + ("; " if lang == "en" else "；").join(parts)


@app.exception_handler(RequestValidationError)
async def humanize_validation_error(request: Request, exc: RequestValidationError):
    # 明细里的 input 是请求体原样（text/plain、表单体时是 bytes，JSONResponse 序列化不了，整个请求变成 500；设置页的体里
    # 还有密钥），不回给调用方；其余字段过 jsonable_encoder（ctx 里可能夹着异常对象）
    errors = jsonable_encoder([{k: v for k, v in err.items() if k != "input"} for err in exc.errors()])
    return JSONResponse({"detail": validation_message(exc.errors()), "errors": errors}, status_code=422)


@app.exception_handler(settings_mod.SettingsFileError)
@app.exception_handler(settings_mod.SettingsWriteError)
async def settings_file_trouble(request: Request, exc: Exception):
    """设置文件读不了 / 写不进去：给人话而不是裸 500（设置页保存、网关应用、本地提示开关这些写盘接口都会撞到）。
    另一个窗口一直占着保存锁是 409（等几秒再存就好）；其余是 500——本机文件权限 / 磁盘的问题，原样重试没用。
    文案经下面的语言中间件照常翻译。"""

    status = 409 if getattr(exc, "busy", False) else 500
    return JSONResponse({"detail": settings_mod.settings_error_message(exc)}, status_code=status)


@app.middleware("http")
async def ui_language(request: Request, call_next):
    """前端每个请求带 X-StoryStudio-Lang；英文时把 JSON 响应里用户可见的文案键翻掉（见 i18n.py）。"""

    lang = lang_from_headers(request.headers)
    set_lang(lang)
    response = await call_next(request)
    if lang == "en" and request.url.path.startswith("/api/") and "application/json" in (response.headers.get("content-type") or ""):
        body = b"".join([chunk async for chunk in response.body_iterator])
        translated = translate_json(body)
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(content=translated if translated is not None else body, status_code=response.status_code,
                        headers=headers, media_type="application/json", background=response.background)
    return response


app.include_router(episodes_router.router)
app.include_router(config_router.router)
app.include_router(scripts_router.router)
app.include_router(settings_router.router)
app.include_router(system_router.router)
app.include_router(post_router.router)
app.include_router(project_router.router)
app.include_router(voices_router.router)
app.include_router(revise_router.router)
app.include_router(assist_router.router)
app.include_router(compare_router.router)
app.include_router(export_router.router)
app.include_router(series_router.router)
app.include_router(poster_router.router)
app.include_router(modules_router.router)   # 模块注册表 + 产品名（DESIGN §38）
app.include_router(recent_router.router)   # 跨模块「最近改过的项目」（DESIGN §38）
app.include_router(picturebook_router.router)   # 绘本 / 有声书模块（DESIGN §39）
app.include_router(imageset_router.router)   # 表情与头像 / 纪念写真（DESIGN §40）
app.include_router(style_cards_router.router)   # 出图风格卡库（DESIGN §41）
app.include_router(clip_router.router)   # 单条视频模块（DESIGN §43）
app.include_router(music_router.router)   # 配乐模块（DESIGN §44）
app.include_router(podcast_router.router)   # AI 播客模块（DESIGN §50）
app.include_router(script_router.router)   # 剧本工作台（docs/scriptlab-spec.md）
app.include_router(local_models_router.router)   # 本地模型：扫描本机 / 应用到角色 / ComfyUI 工作流（DESIGN §45）
app.include_router(trash_router.router)   # 回收站：跨模块列出 / 恢复 / 彻底删除归档


APP_DIR = STATIC_DIR / "app"          # Vite 构建产物（frontend/ → npm run build）


@app.get("/")
def index() -> FileResponse:
    """新界面（资源为中心的工作台）；没构建过前端时退回旧页面，开发环境不至于白屏。"""

    built = APP_DIR / "index.html"
    return FileResponse(built if built.is_file() else STATIC_DIR / "index.html")


@app.get("/legacy")
def legacy_index() -> FileResponse:
    """v0.1 的单页界面：镜头库/风格库的逐条编辑暂时只在这里有，新界面「高级」页链到它。"""

    return FileResponse(STATIC_DIR / "index.html")


# 应用图标（desktop/make_icon.py 画的那份 .ico，打包时也随包进 desktop/）：浏览器模式（--browser）的标签页默认就来要
# /favicon.ico，没有这个路由就是 404、标签页空图标
FAVICON = resource("desktop", "storystudio.ico")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    if not FAVICON.is_file():
        return Response(status_code=404)
    return FileResponse(FAVICON, media_type="image/x-icon")


if APP_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(APP_DIR)), name="app")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
