"""插件：把仓库外的来源（适配器）接进来，核心代码里不写任何具体厂商。

插件从哪里加载（只认这两处）：

1. ``<STORYSTUDIO_HOME 或 ~/.storystudio>/plugins/<名字>/``（:func:`default_dir`）；
2. 环境变量 ``STORYSTUDIO_PLUGIN_PATH`` 列出的目录（``os.pathsep`` 分隔）：每一项本身是插件目录（里面有
   ``__init__.py``），或是装着多个插件的父目录。

有意**不**认的地方：settings.json 里的键（设置接口改得到它，本机网页就能借此让程序执行代码）、项目数据根
（放的可能是别人发来的项目）、exe 旁边。关掉：``STORYSTUDIO_PLUGINS=off``（或 0 / false / no），桌面启动器
``--no-plugins``。目录名以 ``.`` / ``_`` 开头或以 ``.disabled`` 结尾的跳过。

插件约定（插件接口 v1，完整说明见 docs/plugins.md）::

    STORYSTUDIO_API = 1          # 必填；跟 API_VERSION 不一致就拒绝加载
    __version__ = "1.0.0"        # 可选
    def register(api): ...       # 必填；只做登记——不联网、不起子进程、不读写用户数据

``register`` 里的登记先存进暂存区，全部校验通过才一次写进核心的几张表（来源表 ``adapters.REGISTRY`` /
``settings.ADAPTERS``、能力表、异步任务前缀、翻译、失败分类）；任何一步出错整个插件不加载，核心表里一条不留。
加载永远不让程序起不来：失败记成 :class:`PluginRecord`，设置页和健康检查里能看到原因。

分层：本模块不依赖 webui。翻译规则与失败分类提示先存在这里，由 ``webui.i18n`` / ``webui.humanize`` 按
:func:`generation` 取过去合并。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

API_VERSION = 1
ENV_PATH = "STORYSTUDIO_PLUGIN_PATH"
ENV_SWITCH = "STORYSTUDIO_PLUGINS"
MODULE_PREFIX = "storystudio_plugin_"
FIELDS = ("base_url", "api_key", "model", "voice")
FAILURE_KINDS = ("config", "ratelimit", "quota", "moderation", "timeout")
RESERVED_PREFIXES = ("file:", "url:", "local:", "tail:")
_OFF = {"off", "0", "false", "no"}
_ADAPTER_ID = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
_PREFIX = re.compile(r"^[a-z][a-z0-9-]{1,23}:$")
_ANCHORED = re.compile(r"^(?:\(\?[aiLmsux]+\))?\^.*\$$", re.DOTALL)

log = logging.getLogger("storystudio.plugins")

_LOCK = threading.RLock()
_DONE = False
_IN_PROGRESS = False
_GENERATION = 0
_RECORDS: list["PluginRecord"] = []


class PluginError(Exception):
    """插件登记不合规矩：整个插件不加载。``sentence=True`` 的消息本身就是给人看的整句，不再套「加载失败：」。"""

    def __init__(self, message: str, *, sentence: bool = False) -> None:
        super().__init__(message)
        self.sentence = sentence


@dataclass
class PluginRecord:
    """一个插件的加载结果与它登记过的全部内容（撤回时按这里逐条删）。"""

    name: str
    path: str
    status: str = "pending"        # loaded / failed / skipped
    version: str = ""
    message: str = ""               # 给人看的一句（失败 / 跳过的原因）
    error: str = ""                 # 原始异常「类名: 原文」
    declared: dict[str, Any] = field(default_factory=dict)   # plugin.json 的 provides：导入失败时也知道它本来提供什么
    module: str = ""
    adapters: dict[str, list[str]] = field(default_factory=dict)
    prefixes: list[str] = field(default_factory=list)
    cap_keys: list[tuple[str, str, str]] = field(default_factory=list)
    local_ids: list[str] = field(default_factory=list)
    i18n_exact: dict[str, str] = field(default_factory=dict)
    i18n_patterns: list[tuple[str, str]] = field(default_factory=list)
    hints: list[tuple[str, str]] = field(default_factory=list)
    vendor: str = ""

    @property
    def loaded(self) -> bool:
        return self.status == "loaded"

    def public(self) -> dict[str, Any]:
        """健康检查 / 设置页用的那一份：不带模块对象，路径原样（本机界面才看得到）。"""

        return {
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "message": self.message,
            "error": self.error,
            "path": self.path,
            "adapters": {role: list(ids) for role, ids in self.adapters.items()},
            "prefixes": list(self.prefixes),
        }


# ---------------------------------------------------------------------------- 位置与开关


def default_dir() -> Path:
    from .settings import home_dir

    return home_dir() / "plugins"


# 路径里的一段：到空白、括号、逗号、分号、冒号、引号为止
_PATH_CHARS = r"[^\s（）()，,；;：「」\"']"


def _home_forms() -> list[tuple[str, str]]:
    """（本机写法, 换成的写法）：``STORYSTUDIO_HOME`` → ``<STORYSTUDIO_HOME>``，用户主目录 → ``~``；长的排前面先换。"""

    bases: list[tuple[Path, str]] = []
    explicit = os.environ.get("STORYSTUDIO_HOME")
    if explicit:
        bases.append((Path(explicit).expanduser(), "<STORYSTUDIO_HOME>"))
    try:
        bases.append((Path.home(), "~"))
    except (RuntimeError, KeyError, OSError):
        pass
    forms: set[tuple[str, str]] = set()
    for base, label in bases:
        variants = {str(base), base.as_posix()}
        try:
            resolved = base.resolve()
            variants |= {str(resolved), resolved.as_posix()}
        except (OSError, RuntimeError):
            pass
        for form in variants:
            form = form.rstrip("\\/")
            if len(form) > 3:          # 根目录、盘符根不换
                forms.add((form, label))
    return sorted(forms, key=lambda item: len(item[0]), reverse=True)


def portable_text(text: str) -> str:
    """会进账本 / 任务记录 / 接口报错原文的话：本机绝对路径里的 ``STORYSTUDIO_HOME`` 收成 ``<STORYSTUDIO_HOME>``、
    用户主目录收成 ``~``（不带出本机用户名），收过的那段路径统一用正斜杠。别处的路径原样。"""

    out = str(text)
    flags = re.IGNORECASE if os.name == "nt" else 0
    for form, label in _home_forms():
        pattern = re.compile(re.escape(form) + rf"(?P<tail>[\\/]{_PATH_CHARS}*)?(?![\\/]|{_PATH_CHARS})", flags)
        out = pattern.sub(lambda m, label=label: label + (m.group("tail") or "").replace("\\", "/"), out)
    return out


def display_dir() -> str:
    """默认插件目录给人看的写法（``~/.storystudio/plugins``）；设置页 / 健康检查的 ``plugin_dir`` 字段另给绝对路径。"""

    return portable_text(str(default_dir()))


def disabled() -> bool:
    return os.environ.get(ENV_SWITCH, "").strip().lower() in _OFF


def plugin_dirs() -> list[Path]:
    """要找插件的目录：默认目录 + ``STORYSTUDIO_PLUGIN_PATH`` 里的每一项（顺序即优先级，同名只认第一个）。"""

    out = [default_dir()]
    for part in os.environ.get(ENV_PATH, "").split(os.pathsep):
        part = part.strip().strip('"')
        if part:
            out.append(Path(part).expanduser())
    return out


def _skipped_name(name: str) -> bool:
    return name.startswith((".", "_")) or name.endswith(".disabled")


def _looks_like_plugin(folder: Path) -> bool:
    """有 ``__init__.py`` 或 ``plugin.json`` 才算插件目录；只有 plugin.json、缺 __init__.py 的照样算，加载时报清楚缺什么。"""

    return (folder / "__init__.py").is_file() or (folder / "plugin.json").is_file()


def discover() -> list[Path]:
    """列出候选插件目录（不导入）。默认目录只看子目录；环境变量里的一项本身有 ``__init__.py`` 就当一个插件。

    子目录里既没有 ``__init__.py`` 也没有 ``plugin.json`` 的（插件仓库里的 tools/、docs/，随手放的数据目录）不算插件、
    不进记录——免得设置页和健康检查里冒出一条假的「加载失败」。"""

    found: list[Path] = []
    for index, base in enumerate(plugin_dirs()):
        try:
            if not base.is_dir():
                continue
            if index > 0 and (base / "__init__.py").is_file():
                found.append(base)
                continue
            children = sorted((c for c in base.iterdir() if c.is_dir() and not _skipped_name(c.name)), key=lambda p: p.name)
            for child in children:
                if _looks_like_plugin(child):
                    found.append(child)
                else:
                    log.debug("not a plugin folder (no __init__.py / plugin.json): %s", child)
        except OSError as exc:
            log.warning("plugin folder not readable: %s (%s)", base, exc)
            continue
    return found


# ---------------------------------------------------------------------------- 加载


def ensure_loaded() -> None:
    """幂等：第一次调用时加载全部插件，之后只是一次布尔判断。自己永远不抛异常。

    加载期间同一线程再调进来（插件导入时回调核心）直接返回，不会递归；别的线程等这一轮加载完再往下走。"""

    global _DONE, _IN_PROGRESS
    if _DONE:
        return
    with _LOCK:
        if _DONE or _IN_PROGRESS:
            return
        _IN_PROGRESS = True
        try:
            if not disabled():
                for folder in discover():
                    try:
                        _load(folder)
                    except Exception:  # noqa: BLE001 - _load 自己兜底；这里再兜一层，免得一个插件拦住其余的
                        log.exception("plugin loader crashed on %s", folder)
        except Exception:  # noqa: BLE001 - 找插件出错也不能让程序起不来
            log.exception("plugin discovery failed")
        finally:
            _IN_PROGRESS = False
            _DONE = True


def load_from(folder: str | Path) -> PluginRecord:
    """加载一个插件目录（测试与手动加载用）；结果同样记进 :func:`records`。"""

    with _LOCK:
        return _load(Path(folder))


def _module_name(name: str) -> str:
    return MODULE_PREFIX + re.sub(r"\W", "_", name)


def _read_manifest(folder: Path) -> dict[str, Any]:
    path = folder / "plugin.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        log.warning("plugin manifest unreadable: %s (%s)", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _failure_message(name: str, exc: BaseException) -> str:
    if isinstance(exc, PluginError) and exc.sentence:
        return str(exc)[:500]
    detail = str(exc) if isinstance(exc, PluginError) else f"{type(exc).__name__}: {exc}"
    return f"插件 {name} 加载失败：{detail}"[:500]


def _load(folder: Path) -> PluginRecord:
    folder = Path(folder).expanduser()
    name = folder.name
    record = PluginRecord(name=name, path=str(folder), module=_module_name(name))
    previous = next((r for r in _RECORDS if r.name == name and r.status == "loaded"), None)
    if previous is not None:
        record.status = "skipped"
        record.message = f"插件 {name} 已经从 {previous.path} 加载过，这一份没有加载"
        _RECORDS.append(record)
        log.warning("plugin %s skipped: already loaded from %s", name, previous.path)
        return record
    manifest = _read_manifest(folder)
    provides = manifest.get("provides")
    record.declared = dict(provides) if isinstance(provides, Mapping) else {}
    record.version = str(manifest.get("version") or "")
    _RECORDS.append(record)
    added_path = ""
    try:
        init = folder / "__init__.py"
        if not init.is_file():
            raise PluginError(f"插件 {name} 的目录里没有 __init__.py：{folder}", sentence=True)
        vendor = folder / "vendor"
        if vendor.is_dir():
            from .paths import extra_site_python_mismatch

            wanted = extra_site_python_mismatch(vendor)
            if wanted:
                raise PluginError(f"插件 {name} 的 vendor 目录是给 Python {wanted} 装的，本程序是 "
                                  f"{sys.version_info[0]}.{sys.version_info[1]}——用同一版本的 Python 重装", sentence=True)
            if str(vendor) not in sys.path:
                sys.path.append(str(vendor))
                added_path = str(vendor)
                record.vendor = added_path
        spec = importlib.util.spec_from_file_location(record.module, init, submodule_search_locations=[str(folder)])
        if spec is None or spec.loader is None:
            raise PluginError(f"插件 {name} 的 __init__.py 导入不了", sentence=True)
        module = importlib.util.module_from_spec(spec)
        sys.modules[record.module] = module
        spec.loader.exec_module(module)
        wanted_api = getattr(module, "STORYSTUDIO_API", None)
        if wanted_api is None:
            raise PluginError(f"插件 {name} 没有写 STORYSTUDIO_API（插件接口版本）", sentence=True)
        if wanted_api != API_VERSION:
            raise PluginError(f"插件 {name} 需要插件接口 v{wanted_api}，本程序提供 v{API_VERSION}——更新插件或程序", sentence=True)
        register = getattr(module, "register", None)
        if not callable(register):
            raise PluginError(f"插件 {name} 没有 register(api) 函数", sentence=True)
        record.version = str(getattr(module, "__version__", "") or record.version)
        api = PluginAPI(name, folder)
        register(api)
        api._commit(record)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - 插件怎么坏都只坏它自己
        record.status = "failed"
        record.message = _failure_message(name, exc)
        record.error = f"{type(exc).__name__}: {exc}"[:500]
        _forget_modules(record.module)
        if added_path and added_path in sys.path:
            sys.path.remove(added_path)
        record.vendor = ""
        log.error("plugin %s failed to load from %s: %s", name, folder, record.error, exc_info=True)
        return record
    record.status = "loaded"
    _check_manifest(record)
    log.info("plugin %s %s loaded from %s: %s", name, record.version or "", folder,
             ", ".join(f"{role}={','.join(ids)}" for role, ids in record.adapters.items()) or "no sources")
    return record


def _forget_modules(module: str) -> None:
    for key in [k for k in sys.modules if k == module or k.startswith(module + ".")]:
        sys.modules.pop(key, None)


def _check_manifest(record: PluginRecord) -> None:
    """plugin.json 声明的来源 / 前缀跟实际登记的对不上：只记一条 warning（清单只用于导入失败时的提示）。"""

    declared = record.declared
    if not declared:
        return
    want_adapters = {role: sorted(ids) for role, ids in (declared.get("adapters") or {}).items() if isinstance(ids, list)}
    have_adapters = {role: sorted(ids) for role, ids in record.adapters.items()}
    if want_adapters and want_adapters != have_adapters:
        log.warning("plugin %s: plugin.json declares adapters %s but registered %s", record.name, want_adapters, have_adapters)
    want_prefixes = sorted(declared.get("prefixes") or [])
    if want_prefixes and want_prefixes != sorted(record.prefixes):
        log.warning("plugin %s: plugin.json declares prefixes %s but registered %s", record.name, want_prefixes, record.prefixes)


# ---------------------------------------------------------------------------- 撤回（测试用）


def unload_all() -> None:
    """按归属记录撤回所有插件登记过的内容，清空记录。测试 teardown 用；运行中的程序不需要它。"""

    global _GENERATION
    from . import adapters as adapters_mod
    from . import capabilities as caps_mod
    from . import settings as settings_mod

    with _LOCK:
        for record in _RECORDS:
            if not record.loaded:
                continue
            for role, ids in record.adapters.items():
                for adapter_id in ids:
                    adapters_mod.REGISTRY.get(role, {}).pop(adapter_id, None)
                    settings_mod.ADAPTERS.get(role, {}).pop(adapter_id, None)
            for key in record.cap_keys:
                caps_mod.CAPABILITIES.pop(key, None)
            for adapter_id in record.local_ids:
                caps_mod.LOCAL_ADAPTERS.discard(adapter_id)
            for prefix in record.prefixes:
                adapters_mod.ASYNC_PREFIXES.pop(prefix, None)
            _forget_modules(record.module)
            if record.vendor and record.vendor in sys.path:
                sys.path.remove(record.vendor)
        _RECORDS.clear()
        _GENERATION += 1


def reset() -> None:
    """撤回全部插件并允许 :func:`ensure_loaded` 再跑一遍（测试用）。"""

    global _DONE
    with _LOCK:
        unload_all()
        _DONE = False


# ---------------------------------------------------------------------------- 查询


def records() -> list[PluginRecord]:
    return list(_RECORDS)


def generation() -> int:
    """每次有插件登记或撤回就加一；翻译 / 失败分类的缓存据此失效。"""

    return _GENERATION


def i18n_exact() -> dict[str, str]:
    out: dict[str, str] = {}
    for record in _RECORDS:
        if record.loaded:
            out.update(record.i18n_exact)
    return out


def i18n_patterns() -> list[tuple[str, str]]:
    return [rule for record in _RECORDS if record.loaded for rule in record.i18n_patterns]


def failure_hints() -> list[tuple[str, str]]:
    return [hint for record in _RECORDS if record.loaded for hint in record.hints]


def _declares_adapter(record: PluginRecord, role: str, adapter_id: str) -> bool:
    ids = (record.declared.get("adapters") or {}).get(role) if isinstance(record.declared.get("adapters"), Mapping) else None
    return isinstance(ids, list) and adapter_id in ids


def owner_of_adapter(role: str, adapter_id: str) -> PluginRecord | None:
    """这个来源归哪个插件：先看成功登记的，再看 plugin.json 声明过它、但没加载成的。"""

    for record in _RECORDS:
        if record.loaded and adapter_id in record.adapters.get(role, ()):
            return record
    for record in _RECORDS:
        if not record.loaded and _declares_adapter(record, role, adapter_id):
            return record
    return None


def owner_of_prefix(prefix: str) -> PluginRecord | None:
    for record in _RECORDS:
        if record.loaded and prefix in record.prefixes:
            return record
    for record in _RECORDS:
        if not record.loaded and prefix in (record.declared.get("prefixes") or ()):
            return record
    return None


REASON_LIMIT = 200
DISABLED_NOTE = "插件加载已关闭：STORYSTUDIO_PLUGINS=off 或桌面启动器的 --no-plugins"


def failure_reason(record: PluginRecord | None) -> str:
    """没加载成的插件为什么没加载：一句人话，截到 :data:`REASON_LIMIT` 字、本机路径收成 ``~``；加载成了 / 没有记录给空串。

    这句会嵌进「来源用不了」的整句（进账本、接口 400、「测试连接」截过长度的文案）：太长会把整句的尾巴截掉，英文界面
    就对不上整句规则了。完整原文仍在 :meth:`PluginRecord.public` 的 message / error 里。"""

    if record is None or record.loaded or not record.message:
        return ""
    message = portable_text(record.message)
    return message if len(message) <= REASON_LIMIT else message[:REASON_LIMIT - 1] + "…"


def unavailable_reason(role: str, adapter_id: str) -> str:
    """来源不在表里时补一句为什么：「（插件 X 加载失败：…）」；插件加载关着时「（插件加载已关闭：…）」；都不是给空串。"""

    reason = failure_reason(owner_of_adapter(role, adapter_id))
    if reason:
        return f"（{reason}）"
    return f"（{DISABLED_NOTE}）" if disabled() else ""


def prefix_unavailable_reason(prefix: str) -> str:
    reason = failure_reason(owner_of_prefix(prefix))
    if reason:
        return f"（{reason}）"
    return f"（{DISABLED_NOTE}）" if disabled() else ""


# ---------------------------------------------------------------------------- 插件拿到的 api


class PluginAPI:
    """``register(api)`` 里的 ``api``：登记先进暂存区，``register`` 正常返回后由加载器一次提交。"""

    api_version = API_VERSION

    def __init__(self, name: str, plugin_dir: str | Path) -> None:
        self.name = name
        self.plugin_dir = Path(plugin_dir)
        self.log = logging.getLogger(f"storystudio.plugins.{name}")
        self._adapters: list[tuple[str, str, Callable[..., Any], dict[str, Any], bool]] = []
        self._caps: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._prefixes: list[tuple[str, str]] = []
        self._exact: dict[str, str] = {}
        self._patterns: list[tuple[str, str]] = []
        self._hints: list[tuple[str, str]] = []

    @property
    def home_dir(self) -> Path:
        from .settings import home_dir

        return home_dir()

    @property
    def core_version(self) -> str:
        from .version import __version__

        return __version__

    # ---- 来源
    def add_adapter(self, role: str, adapter_id: str, builder: Callable[..., Any], *, label: str, hint: str = "",
                    fields: Iterable[str] = ("model",), defaults: Mapping[str, str] | None = None, local: bool = False) -> None:
        """登记一个来源：``builder(config, *, cache_dir=...)`` 返回适配器实例，实例的 ``name`` 必须等于 ``adapter_id``。"""

        from .settings import ROLES

        if role not in ROLES:
            raise PluginError(f"adapter {adapter_id!r}: unknown role {role!r} (one of {', '.join(ROLES)})")
        if not isinstance(adapter_id, str) or not _ADAPTER_ID.match(adapter_id):
            raise PluginError(f"adapter id {adapter_id!r} must match {_ADAPTER_ID.pattern}")
        if not callable(builder):
            raise PluginError(f"adapter {adapter_id!r}: builder is not callable")
        if not isinstance(label, str) or not label.strip():
            raise PluginError(f"adapter {adapter_id!r}: label must be a non-empty string")
        field_list = [str(f) for f in fields]
        bad = [f for f in field_list if f not in FIELDS]
        if bad:
            raise PluginError(f"adapter {adapter_id!r}: unknown fields {bad} (allowed: {', '.join(FIELDS)})")
        default_map = dict(defaults or {})
        if any(k not in FIELDS or not isinstance(v, str) for k, v in default_map.items()):
            raise PluginError(f"adapter {adapter_id!r}: defaults must map {', '.join(FIELDS)} to strings")
        if any(r == role and a == adapter_id for r, a, *_ in self._adapters):
            raise PluginError(f"adapter {adapter_id!r} registered twice for role {role!r}")
        self._check_adapter_free(role, adapter_id)
        meta = {"label": label.strip(), "fields": field_list, "hint": str(hint or ""), "defaults": default_map, "plugin": self.name}
        self._adapters.append((role, adapter_id, builder, meta, bool(local)))

    def _check_adapter_free(self, role: str, adapter_id: str) -> None:
        from . import adapters as adapters_mod
        from . import settings as settings_mod

        if adapter_id in adapters_mod.REGISTRY.get(role, {}) or adapter_id in settings_mod.ADAPTERS.get(role, {}):
            other = owner_of_adapter(role, adapter_id)
            owner = f"插件 {other.name}" if other is not None and other.loaded else "内置"
            raise PluginError(f"插件 {self.name} 想登记的来源 {adapter_id} 已经存在（{owner}），整个插件没有加载", sentence=True)

    def _owns(self, role: str | None, adapter_id: str) -> bool:
        return any(a == adapter_id and (role is None or r == role) for r, a, *_ in self._adapters)

    # ---- 能力表
    def add_capabilities(self, table: Mapping[tuple[str, str, str], Mapping[str, Any]]) -> None:
        """合并进 ``capabilities.CAPABILITIES``：键是 ``(角色, 来源, 模型前缀)``，只能用本插件登记过的来源。"""

        from . import capabilities as caps_mod

        for key, caps in dict(table).items():
            if not (isinstance(key, tuple) and len(key) == 3 and all(isinstance(k, str) for k in key)):
                raise PluginError(f"capability key {key!r} must be (role, adapter_id, model_prefix)")
            if not isinstance(caps, Mapping):
                raise PluginError(f"capability {key!r} must map to a dict")
            if not self._owns(key[0], key[1]):
                raise PluginError(f"capability {key!r}: adapter {key[1]!r} is not registered by this plugin for role {key[0]!r}")
            if key in caps_mod.CAPABILITIES or key in self._caps:
                raise PluginError(f"capability {key!r} already exists")
            self._caps[key] = dict(caps)

    # ---- 异步任务前缀
    def add_async_prefix(self, prefix: str, adapter_id: str) -> None:
        """异步任务 id 的前缀（如 ``abc-img:``）→ 这个来源负责查进度。"""

        from . import adapters as adapters_mod

        if not isinstance(prefix, str) or not _PREFIX.match(prefix):
            raise PluginError(f"task prefix {prefix!r} must match {_PREFIX.pattern}")
        if prefix in RESERVED_PREFIXES:
            raise PluginError(f"task prefix {prefix!r} is reserved")
        for other in [*adapters_mod.ASYNC_PREFIXES, *(p for p, _ in self._prefixes)]:
            if other.startswith(prefix) or prefix.startswith(other):
                raise PluginError(f"task prefix {prefix!r} clashes with existing prefix {other!r}")
        if not self._owns(None, adapter_id):
            raise PluginError(f"task prefix {prefix!r}: adapter {adapter_id!r} is not registered by this plugin")
        self._prefixes.append((prefix, adapter_id))

    # ---- 翻译
    def add_i18n(self, exact: Mapping[str, str] | None = None, patterns: Iterable[tuple[str, str]] | None = None) -> None:
        """英文界面的翻译：精确表（中文原文 → 英文）与正则表（必须 ``^…$`` 锚定整句）。核心已有的精确条目优先。"""

        for zh, en in dict(exact or {}).items():
            if not isinstance(zh, str) or not isinstance(en, str) or not zh:
                raise PluginError("i18n exact entries must map non-empty str to str")
            self._exact[zh] = en
        for rule in list(patterns or ()):
            if not (isinstance(rule, tuple) and len(rule) == 2 and all(isinstance(x, str) for x in rule)):
                raise PluginError(f"i18n pattern {rule!r} must be a (regex, replacement) pair of strings")
            pattern, replacement = rule
            if not _ANCHORED.match(pattern):
                raise PluginError(f"i18n pattern must be anchored with ^...$: {pattern[:80]!r}")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise PluginError(f"i18n pattern does not compile: {pattern[:80]!r}: {exc}") from exc
            self._patterns.append((pattern, replacement))

    # ---- 失败分类
    def add_failure_hints(self, kind: str, needles: Iterable[str]) -> None:
        """给失败分类补来源自己的措辞（正则，不分大小写）。kind：config / ratelimit / quota / moderation / timeout。"""

        if kind not in FAILURE_KINDS:
            raise PluginError(f"failure kind {kind!r} must be one of {', '.join(FAILURE_KINDS)}")
        for needle in needles:
            if not isinstance(needle, str) or not needle:
                raise PluginError("failure hints must be non-empty strings")
            try:
                re.compile(needle)
            except re.error as exc:
                raise PluginError(f"failure hint does not compile: {needle[:80]!r}: {exc}") from exc
            self._hints.append((kind, needle))

    # ---- 提交（加载器调）
    def _commit(self, record: PluginRecord) -> None:
        """先按此刻的核心表把暂存的每一项再核一遍，全部通过才写入；写入这一步不会半途而废。"""

        global _GENERATION
        from . import adapters as adapters_mod
        from . import capabilities as caps_mod
        from . import settings as settings_mod

        for role, adapter_id, *_ in self._adapters:
            self._check_adapter_free(role, adapter_id)
        for key in self._caps:
            if key in caps_mod.CAPABILITIES:
                raise PluginError(f"capability {key!r} already exists")
        for prefix, _ in self._prefixes:
            if any(o.startswith(prefix) or prefix.startswith(o) for o in adapters_mod.ASYNC_PREFIXES):
                raise PluginError(f"task prefix {prefix!r} clashes with an existing prefix")
        for role, adapter_id, builder, meta, local in self._adapters:
            adapters_mod.REGISTRY.setdefault(role, {})[adapter_id] = builder
            settings_mod.ADAPTERS.setdefault(role, {})[adapter_id] = meta
            record.adapters.setdefault(role, []).append(adapter_id)
            if local and adapter_id not in caps_mod.LOCAL_ADAPTERS:
                caps_mod.LOCAL_ADAPTERS.add(adapter_id)
                record.local_ids.append(adapter_id)
        caps_mod.CAPABILITIES.update(self._caps)
        record.cap_keys = list(self._caps)
        for prefix, adapter_id in self._prefixes:
            adapters_mod.ASYNC_PREFIXES[prefix] = adapter_id
            record.prefixes.append(prefix)
        record.i18n_exact = dict(self._exact)
        record.i18n_patterns = list(self._patterns)
        record.hints = list(self._hints)
        _GENERATION += 1


__all__ = [
    "API_VERSION", "ENV_PATH", "ENV_SWITCH", "FIELDS", "FAILURE_KINDS", "PluginAPI", "PluginError", "PluginRecord",
    "default_dir", "display_dir", "portable_text", "disabled", "plugin_dirs", "discover", "ensure_loaded", "load_from",
    "unload_all", "reset", "records", "generation", "i18n_exact", "i18n_patterns", "failure_hints", "owner_of_adapter",
    "owner_of_prefix", "failure_reason", "unavailable_reason", "prefix_unavailable_reason", "DISABLED_NOTE",
]
