"""用户模型配置：五个角色（剧本文本 / 生图 / 语音 / 视频 / 配乐），每个角色选一个适配器 + 填自己的接口地址、密钥、模型名。

存放位置（按优先级）：
1. 环境变量 ``STORYSTUDIO_SETTINGS`` 指向的 json 文件
2. ``<STORYSTUDIO_HOME 或 ~/.storystudio>/settings.json``

密钥只存在本机这个文件里，绝不进仓库；webui 的设置接口读出来时会打码。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

from .local import is_local_url

ROLES = ("text", "image", "voice", "video", "music")

# 设置文件的进程内读写锁。webui 的同步端点跑在线程池里，设置页连点两张卡的「保存」、保存与「关掉本地提示」
# 会同时到：不串行的话两个「读 → 改 → 存」互相覆盖（后写的丢掉先写的改动），Windows 上读和 os.replace / icacls
# 撞在一起还会 PermissionError。读只在读文件那一下持锁；写从读到存整段持锁（见 editing_settings）。可重入：
# update_settings 持锁时还要调 load_settings / save_settings。
_LOCK = threading.RLock()
_READ_ATTEMPTS = 3          # 读失败先短暂重读再下结论：别的进程（另一个 StoryStudio / 杀毒软件）正占着文件时是瞬时的
# 跨进程锁（见 _process_lock）：_LOCK 只管住本进程的线程，两个 StoryStudio 窗口同时保存设置还是会整份互相覆盖。
# 等另一个进程保存完的上限（秒）；只在 _LOCK 之内读写 _PROCESS_HELD（本进程已持有的锁文件 → 重入层数）
_PROCESS_LOCK_WAIT = 10.0
_PROCESS_HELD: dict[str, int] = {}

# 每个角色可选的适配器与它们需要的字段。UI 按这张表画表单，路由按它装配。插件登记的来源多带一个 "plugin"（插件名）。
# 读这张表请走 adapter_spec / adapter_presets：它们先确保插件已加载。
ADAPTERS: dict[str, dict[str, dict[str, Any]]] = {
    "text": {
        "openai_chat": {
            "label": "OpenAI 兼容对话接口（官方 openai SDK，/chat/completions）",
            "fields": ["base_url", "api_key", "model"],
            "hint": "OpenAI、OpenRouter（base_url 填 https://openrouter.ai/api/v1）、DeepSeek、Moonshot、通义、豆包等兼容网关都走这条",
            "defaults": {"base_url": "https://api.openai.com/v1"},
        },
    },
    "image": {
        "openrouter_images": {
            "label": "OpenRouter 生图（官方 SDK，/api/v1/images，支持参考图）",
            "fields": ["api_key", "model"],
            "hint": "一个 key 可同时配图/视频/语音；模型如 bytedance-seed/seedream-4.5、google/gemini-2.5-flash-image",
            "defaults": {"base_url": "https://openrouter.ai/api/v1", "model": "bytedance-seed/seedream-4.5"},
        },
        "openai_images": {
            "label": "OpenAI 兼容图片接口（/v1/images/generations · edits）",
            "fields": ["base_url", "api_key", "model"],
            "hint": "gpt-image 系列或兼容网关；参考图走 /images/edits",
            "defaults": {"base_url": "https://api.openai.com/v1", "model": "gpt-image-1"},
        },
        "sdwebui": {
            "label": "本机 Stable Diffusion WebUI / Forge（/sdapi/v1/txt2img）",
            "fields": ["base_url", "model"],
            "hint": "本机服务，不要密钥、不花钱；接口在 WebUI 带 --api 启动时才开（webui-user.bat 的 COMMANDLINE_ARGS 里加 --api）。"
                    "model 填 checkpoint 名（如 sd_xl_base_1.0.safetensors，可在「扫描本机」的模型清单里抄），"
                    "跟 WebUI 当前加载的不一致时会先 POST /sdapi/v1/options 切过去；参考图走 img2img，一次只吃一张",
            "defaults": {"base_url": "http://127.0.0.1:7860"},
        },
        "comfyui": {
            "label": "本机 ComfyUI（工作流模板，/prompt → /history → /view）",
            "fields": ["base_url", "model"],
            "hint": "本机服务，不要密钥、不花钱；出图按一份「API 格式」工作流跑。工作流在下面的下拉里选（内置 txt2img_sdxl / "
                    "img2img_sdxl，自己导出的放 <项目根>/local_workflows/ 并配一份同名 .map.json，可点「检查映射」干跑一次）；"
                    "选了带输入图的那份才吃参考图。model 填工作流里 checkpoint 的文件名，留空就用工作流自带的。种子每次随机，"
                    "记在缓存的运行记录里可复现",
            "defaults": {"base_url": "http://127.0.0.1:8188", "model": "sd_xl_base_1.0.safetensors"},
        },
        "ark_images": {
            "label": "火山方舟 Seedream（/api/v3/images/generations）",
            "fields": ["base_url", "api_key", "model"],
            "hint": "doubao-seedream 系列；参考图以 base64 随请求发送",
            "defaults": {"base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-seedream-4-0-250828"},
        },
    },
    "voice": {
        "openrouter_tts": {
            "label": "OpenRouter 语音（官方 SDK，tts）",
            "fields": ["api_key", "model", "voice"],
            "hint": "模型与音色以 OpenRouter 语音模型列表为准",
            "defaults": {"base_url": "https://openrouter.ai/api/v1"},
        },
        "edge_tts": {
            "label": "Edge TTS（免费，微软在线音色，edge-tts 库）",
            "fields": ["model"],
            "hint": "model 填音色名，如 zh-CN-YunxiNeural；rate/pitch 由剧集 spec 的 voice_over.edge 决定",
            "defaults": {"model": "zh-CN-YunxiNeural"},
        },
        "openai_speech": {
            "label": "OpenAI 兼容语音接口（/v1/audio/speech）",
            "fields": ["base_url", "api_key", "model", "voice"],
            "hint": "tts-1 / gpt-4o-mini-tts 或兼容网关；voice 填音色 id",
            "defaults": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini-tts", "voice": "alloy"},
        },
        "minimax_speech": {
            "label": "MiniMax 语音接口（/v1/t2a_v2，支持音色克隆）",
            "fields": ["base_url", "api_key", "model", "voice"],
            "hint": "speech-02-hd / speech-02-turbo；voice 填系统音色 id（如 female-shaonv）或音色库里克隆出来的 id。按文档写，未真实调用",
            "defaults": {"base_url": "https://api.minimax.chat/v1", "model": "speech-02-hd", "voice": "female-shaonv"},
        },
    },
    "video": {
        "openrouter_video": {
            "label": "OpenRouter 视频（官方 SDK，/api/v1/videos，支持首帧）",
            "fields": ["api_key", "model"],
            "hint": "模型如 minimax/hailuo-3、bytedance-seed/seedance-*、google/veo-3.1；异步轮询",
            "defaults": {"base_url": "https://openrouter.ai/api/v1", "model": "minimax/hailuo-3"},
        },
        "ark_video": {
            "label": "火山方舟 Seedance（/api/v3/contents/generations/tasks）",
            "fields": ["base_url", "api_key", "model"],
            "hint": "doubao-seedance 系列；首帧以 base64 随请求发送，异步轮询",
            "defaults": {"base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-seedance-1-0-pro-250528"},
        },
        "openai_video": {
            "label": "OpenAI 兼容视频接口（/v1/videos，Sora 格式；LiteLLM 等统一网关）",
            "fields": ["base_url", "api_key", "model"],
            "hint": "网关把 Veo / Runway / Kling 等按 OpenAI Videos 形状转发时用这条；首帧走 input_reference，成品由适配器带密钥拉回本机",
            "defaults": {"base_url": "https://api.openai.com/v1", "model": "sora-2"},
        },
        "comfyui": {
            "label": "本机 ComfyUI 视频（工作流模板，/prompt → /history → /view）",
            "fields": ["base_url", "model"],
            "hint": "本机服务，不要密钥、不花钱；内置 txt2video_wan 是 Wan 2.1 文生视频，需要本机装好 Wan 的模型文件和 "
                    "ComfyUI-VideoHelperSuite（出 mp4 的 VHS_VideoCombine 节点）。自己导出的工作流放 <项目根>/local_workflows/ "
                    "并配一份同名 .map.json；镜头时长按工作流的帧率折算成帧数（4n+1）。model 填 unet 文件名，留空就用工作流自带的",
            "defaults": {"base_url": "http://127.0.0.1:8188", "model": "wan2.1_t2v_1.3B_fp16.safetensors"},
        },
        "minimax_video": {
            "label": "MiniMax 视频接口（/v1/video_generation）",
            "fields": ["base_url", "api_key", "model"],
            "hint": "MiniMax-Hailuo 系列；首帧以 data URL 发送，异步轮询后经 files/retrieve 取下载地址",
            "defaults": {"base_url": "https://api.minimax.chat/v1", "model": "MiniMax-Hailuo-02"},
        },
    },
    # 配乐没有内置来源：由插件登记（见 genkit.plugins / docs/plugins.md）
    "music": {},
}

SECRET_FIELDS = ("api_key",)


@dataclass
class RoleConfig:
    adapter: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    voice: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, *, mask_secrets: bool = False) -> dict[str, Any]:
        data = {"adapter": self.adapter, "base_url": self.base_url, "api_key": self.api_key,
                "model": self.model, "voice": self.voice, "extra": dict(self.extra)}
        if mask_secrets:
            for key in SECRET_FIELDS:
                if data.get(key):
                    data[key] = _mask(data[key])
        return data

    @property
    def configured(self) -> bool:
        return bool(self.adapter)

    @property
    def local(self) -> bool:
        """跑在本机吗（DESIGN §45）。由 ``base_url`` 推导，不额外存字段——多一个字段就多一处不同步。
        判断补默认地址之后的「有效」地址用 :func:`role_is_local`。"""

        return is_local_url(self.base_url)



def _mask(value: str) -> str:
    """打码：露出的字符数按长度算，至少遮掉一半（9 位密钥固定露首 4 尾 4 就只剩 1 个星号，等于回明文）。
    8 位及以下全遮；9–15 位只露首 2 尾 2；16 位以上才露首 4 尾 4。永远至少有一个星号——
    update_settings 靠「含 *」认出回传的打码值不覆盖原密钥。"""

    value = str(value)
    n = len(value)
    if n <= 8:
        return "*" * n
    keep = 4 if n >= 16 else 2
    return value[:keep] + "*" * (n - 2 * keep) + value[-keep:]


# 统一网关（LiteLLM / new-api / one-api 这类 OpenAI 兼容网关）：填一次地址和密钥，一键应用到四个角色。
# 角色配置仍是运行时唯一读的东西——网关只是"批量填表"的来源，不改运行时的装配逻辑。
# 配乐角色不在这张表里：没有 OpenAI 兼容的音乐接口形状，它只能单独配来源。
GATEWAY_ADAPTERS = {"text": "openai_chat", "image": "openai_images", "voice": "openai_speech", "video": "openai_video"}


@dataclass
class Gateway:
    base_url: str = ""
    api_key: str = ""

    def as_dict(self, *, mask_secrets: bool = False) -> dict[str, str]:
        return {"base_url": self.base_url, "api_key": _mask(self.api_key) if mask_secrets and self.api_key else self.api_key}

    @property
    def configured(self) -> bool:
        return bool(self.base_url)


@dataclass
class Settings:
    roles: dict[str, RoleConfig] = field(default_factory=lambda: {r: RoleConfig() for r in ROLES})
    output_root: str = ""          # 空 = 项目目录下 output/
    cache_dir: str = ""            # 空 = <home>/cache
    update_url: str = ""           # 版本清单 JSON 的地址（{"version": "...", "url": "...", "notes": "..."}）；空 = 不检查更新
    gateway: Gateway = field(default_factory=Gateway)
    # 用户关掉过哪些角色的「本机可以跑」提示（角色名，或 "all" = 全部关掉）。存在设置文件里而不是浏览器里：
    # 关一次就永远别再冒出来，换个浏览器 / 重启程序也一样（DESIGN §45「推荐而不强制」）。
    local_hints_dismissed: list[str] = field(default_factory=list)

    def role(self, name: str) -> RoleConfig:
        if name not in ROLES:
            raise KeyError(f"unknown role: {name}")
        return self.roles.setdefault(name, RoleConfig())

    def as_dict(self, *, mask_secrets: bool = False) -> dict[str, Any]:
        # mask_secrets=True 是「给接口看」的那一份：多带一个推导出来的 local（本机来源不要 key、不花钱），
        # 设置页据此显示「本机服务通常不需要」。写盘那一份（mask_secrets=False）不带，免得把推导值存进文件。
        return {
            "roles": {r: {**self.role(r).as_dict(mask_secrets=mask_secrets),
                          **({"local": role_is_local(self, r)} if mask_secrets else {})} for r in ROLES},
            "output_root": self.output_root,
            "cache_dir": self.cache_dir,
            "update_url": self.update_url,
            "gateway": self.gateway.as_dict(mask_secrets=mask_secrets),
            "local_hints_dismissed": list(self.local_hints_dismissed),
        }


def home_dir() -> Path:
    return Path(os.environ.get("STORYSTUDIO_HOME") or (Path.home() / ".storystudio")).expanduser()


def settings_path() -> Path:
    explicit = os.environ.get("STORYSTUDIO_SETTINGS")
    return Path(explicit).expanduser() if explicit else home_dir() / "settings.json"


def cache_dir(settings: Settings | None = None) -> Path:
    base = Path(settings.cache_dir).expanduser() if settings and settings.cache_dir else home_dir() / "cache"
    base.mkdir(parents=True, exist_ok=True)
    return base


class SettingsFileError(ValueError):
    """设置文件读不出来。``path`` 与 ``reason`` 留给接口层拼人话。

    ``unreadable=True`` 是「读不了」（权限 / 被别的程序占着），内容未必坏——这种不能当坏文件备份重建，
    否则一次瞬时的占用就会把一份好好的配置挪走、再用空配置覆盖。``False`` 是内容坏了（不是 JSON / 顶层不是对象）。"""

    def __init__(self, path: Path, reason: str, *, unreadable: bool = False) -> None:
        super().__init__(f"settings file is not valid JSON: {path}: {reason}")
        self.path = path
        self.reason = reason
        self.unreadable = unreadable


class SettingsWriteError(OSError):
    """设置文件写不进去（目录没权限、磁盘满、被别的程序占着换不掉），或 ``busy``：另一个进程一直占着保存锁。
    消息就是给界面看的人话（接口层原样作 detail）；是 OSError 的子类，老的 ``except OSError`` 照样接得住。"""

    def __init__(self, path: Path, reason: str, *, busy: bool = False) -> None:
        if busy:
            message = f"另一个 StoryStudio 窗口正在保存设置，等几秒再保存一次（{path}）"
        else:
            message = f"设置文件写不进去：{path}：{reason}——检查设置目录的权限、磁盘空间，或是不是被别的程序（杀毒软件、同步盘）占着"
        super().__init__(message)
        self.path = path
        self.reason = reason
        self.busy = busy


def _write_reason(exc: OSError) -> str:
    """写盘失败的 OSError → 一小句原因。不用 str(exc)：它带着临时文件和设置文件的完整路径（设置文件的路径消息里已经有了），
    Windows 上是「[WinError 5] 拒绝访问。: 'C:\\\\…tmp' -> 'C:\\\\…'」这种反斜杠加倍、还跟着系统语言走的原话。
    常见的几种换成自家的话（英文界面照样翻得了），其余只取系统给的那句原因。"""

    import errno

    if isinstance(exc, PermissionError):
        return "没有写权限，或文件正被别的程序占着"
    if exc.errno == errno.ENOSPC:
        return "磁盘空间不够"
    if exc.errno == errno.EROFS:
        return "所在的盘是只读的"
    return str(exc.strerror or type(exc).__name__).strip()


def settings_error_message(exc: BaseException) -> str:
    """设置文件读不了 / 写不进去时给界面的一句人话（接口层的异常处理器用）。"""

    if isinstance(exc, SettingsFileError):
        return broken_settings_message(exc)
    return str(exc)


def _lock_byte(handle: Any) -> None:
    """非阻塞地锁住锁文件的第一个字节；锁不上抛 OSError。"""

    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_byte(handle: Any) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass        # 关句柄时系统也会放锁


@contextmanager
def _process_lock(target: Path) -> Iterator[None]:
    """跨进程的保存锁：两个 StoryStudio 窗口（或桌面版 + 命令行）同时保存设置时一个一个来，谁的改动都不丢。

    锁的是旁边的 ``<设置文件>.lock``，不是设置文件本身——Windows 上锁住设置文件，os.replace 换新文件会 PermissionError。
    Windows 用 msvcrt.locking，其它平台 fcntl.flock，都是非阻塞 + 轮询：另一个进程卡死一直不放，等
    ``_PROCESS_LOCK_WAIT`` 秒后抛 ``SettingsWriteError(busy)``，界面说「等几秒再保存」而不是一直转圈。
    同一进程里可重入（editing_settings 持锁时还要调 save_settings）；调用方必须已经持有 ``_LOCK``。
    锁文件建不了（设置目录只读）就不加锁往下走——紧接着的写盘会以「写不进去」把真正的原因报出来。"""

    lock_path = target.with_name(f"{target.name}.lock")
    key = str(lock_path)
    if _PROCESS_HELD.get(key):
        _PROCESS_HELD[key] += 1
        try:
            yield
        finally:
            _PROCESS_HELD[key] -= 1
        return
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")  # noqa: SIM115 - 句柄要活过整个 with 块
    except OSError:
        yield
        return
    try:
        deadline = time.monotonic() + _PROCESS_LOCK_WAIT
        while True:
            try:
                _lock_byte(handle)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise SettingsWriteError(target, str(exc), busy=True) from exc
                time.sleep(0.05)
        _PROCESS_HELD[key] = 1
        try:
            yield
        finally:
            _PROCESS_HELD.pop(key, None)
            _unlock_byte(handle)
    finally:
        handle.close()


def _file_stamp(target: Path) -> tuple[int, int, str] | None:
    """设置文件此刻的「指纹」：修改时间 + 大小 + 内容摘要（有的文件系统时间精度只到秒，同一秒里改两次光看时间看不出来）。
    不存在 → None；读不了 → 每次都不一样的值，按「被改过」处理（合并前会重读，还读不了就照常报「设置文件读不了」）。"""

    import hashlib

    try:
        stat = target.stat()
        digest = hashlib.sha1(target.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        return (-1, -1, f"unreadable-{time.monotonic_ns()}")
    return (stat.st_mtime_ns, stat.st_size, digest)


def _merge_changes(before: Mapping[str, Any], mine: Mapping[str, Any], latest: Settings) -> Settings:
    """别处在我们「读 → 改」的空档里改过文件：以最新的文件为底，只把这次真正改到的字段（角色的某一栏、网关的某一栏、
    顶层某一项）盖上去——不整份覆盖，别人刚存的其它角色不会被这次保存抹掉。``before`` / ``mine`` 是改之前 / 之后的文件层。"""

    merged = latest.as_dict()
    for role, fields in (mine.get("roles") or {}).items():
        was = (before.get("roles") or {}).get(role) or {}
        for key, value in fields.items():
            if was.get(key) != value:
                merged["roles"].setdefault(role, {})[key] = value
    for key, value in (mine.get("gateway") or {}).items():
        if (before.get("gateway") or {}).get(key) != value:
            merged["gateway"][key] = value
    for key in ("output_root", "cache_dir", "update_url", "local_hints_dismissed"):
        if before.get(key) != mine.get(key):
            merged[key] = mine.get(key)
    out = Settings()
    _apply(out, merged)
    return out


def _read_file_layer(target: Path) -> Mapping[str, Any] | None:
    """读设置文件里的 JSON 对象；文件不存在 → None。读失败、解析失败都短暂重读几次再下结论
    （持锁的只有读文件那一下，重读之间不持锁，让正在写的那一方写完）；仍失败抛 :class:`SettingsFileError`。"""

    error: SettingsFileError | None = None
    cause: Exception | None = None
    for attempt in range(_READ_ATTEMPTS):
        if attempt:
            time.sleep(0.05 * attempt)
        with _LOCK:
            if not target.is_file():
                return None
            try:
                # utf-8-sig：记事本 / PowerShell 5.1 的 Set-Content 手改过的文件常带 BOM，仓库里别的 JSON 读取都吃它
                raw = target.read_text(encoding="utf-8-sig")
            except OSError as exc:
                error, cause = SettingsFileError(target, str(exc), unreadable=True), exc
                continue
        try:
            data = json.loads(raw)
        except ValueError as exc:
            error, cause = SettingsFileError(target, str(exc)), exc
            continue
        if not isinstance(data, Mapping):
            raise SettingsFileError(target, f"top-level value is a {type(data).__name__}, not an object")
        return data
    assert error is not None
    raise error from cause


def load_settings(path: str | Path | None = None, *, apply_env: bool = True) -> Settings:
    """读设置。``apply_env=True`` 是运行时生效的那一份（叠上 STORYSTUDIO_<ROLE>_* 环境变量）；
    写盘路径要用 ``apply_env=False`` 拿文件层，不然环境变量里的临时覆盖会跟着一起落盘。
    文件坏了 / 读不了抛 :class:`SettingsFileError`（ValueError 的子类，老的 ``except ValueError`` 照样接得住）。"""

    target = Path(path) if path else settings_path()
    settings = Settings()
    data = _read_file_layer(target)
    if data is not None:
        _apply(settings, data)
    if apply_env:
        _apply_env(settings)
    return settings


def broken_settings_message(exc: SettingsFileError) -> str:
    """设置文件坏了时给界面看的一句话：说清是哪个文件、为什么，以及怎么在程序里修好。
    读不了（权限 / 被占用）的另说一句：那种保存也修不好，而且不会去备份重建。"""

    if exc.unreadable:
        return f"设置文件读不了：{exc.path}：{exc.reason}——检查文件权限，或是不是被别的程序占着"
    return f"设置文件损坏：{exc.path}：{exc.reason}。保存一次会自动备份坏文件并重建"


def load_settings_or_default(path: str | Path | None = None) -> tuple[Settings, str]:
    """只读接口用的读法：文件坏了退回空配置，并把人话一起给回去（空串 = 正常）——设置页得能打开，
    用户才有地方修；裸 500 只会让人去翻文件。"""

    try:
        return load_settings(path), ""
    except SettingsFileError as exc:
        return Settings(), broken_settings_message(exc)


def quarantine_broken_settings(path: Path) -> Path | None:
    """把坏掉的设置文件改名成 ``settings.json.broken-<时间戳>`` 留底（用户手改坏的内容不丢），
    返回备份路径；文件不存在或改不动就 None。"""

    if not path.is_file():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.broken-{stamp}")
    n = 1
    while backup.exists():          # 同一秒内坏两次也不能把上一份备份盖掉
        n += 1
        backup = path.with_name(f"{path.name}.broken-{stamp}-{n}")
    try:
        os.replace(path, backup)
    except OSError:
        return None
    return backup


def load_settings_for_update(path: str | Path | None = None) -> Settings:
    """写盘路径（改一个字段再 save）的读法，跟 :func:`load_settings` 有两处不同：
    只取文件层（环境变量覆盖是运行时的临时值，叠上再存就永久落盘、还把密钥从进程环境写进磁盘）；
    内容坏了从空配置开始（不然设置页的每一次保存都 500，用户在程序里没法修）。

    这里**不动**坏文件：备份挪到 :func:`save_settings` 真正写盘前一刻——请求校验没过、被 400 拒掉时
    什么都没写，坏文件也得原样留着（设置页的「设置文件损坏」提示才不会平白消失）。
    读不了（``unreadable``：权限 / 被占用）照样抛：这时从空配置开始再存，等于把一份好好的配置清空。"""

    target = Path(path) if path else settings_path()
    try:
        return load_settings(target, apply_env=False)
    except SettingsFileError as exc:
        if exc.unreadable:
            raise
        return Settings()


def _backup_if_corrupt(target: Path) -> None:
    """写盘前：原文件内容坏了（多次重读仍解析不了）就先改名留底，别让用户手改坏的内容被新文件直接盖掉。
    读不了的不算坏文件，不挪。"""

    try:
        _read_file_layer(target)
    except SettingsFileError as exc:
        if not exc.unreadable:
            quarantine_broken_settings(target)


def _replace(tmp: Path, target: Path) -> None:
    """os.replace，Windows 上目标文件正被别的进程（另一个 StoryStudio、杀毒软件）打开时会 PermissionError，
    短暂重试几次；进程内的读写已经由 _LOCK 串行，不会撞上。"""

    for attempt in range(_READ_ATTEMPTS):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == _READ_ATTEMPTS - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def save_settings(settings: Settings, path: str | Path | None = None) -> Path:
    """原子写：同目录下的唯一临时文件 + os.replace（读的一方看到的永远是完整的旧文件或新文件）。
    临时文件不能共用一个固定名字——几个写入方同时写同一个 .json.tmp，内容会交错成「Extra data」的坏 JSON。
    原文件内容坏了先备份（见 :func:`_backup_if_corrupt`）。"""

    target = Path(path) if path else settings_path()
    text = json.dumps(settings.as_dict(), ensure_ascii=False, indent=2)
    with _LOCK, _process_lock(target):
        _backup_if_corrupt(target)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=target.parent, prefix=f"{target.name}.", suffix=".tmp")
        except OSError as exc:
            # 设置目录建不了 / 没有写权限 / 磁盘满：给人话，不让接口吐一个裸的 PermissionError
            raise SettingsWriteError(target, _write_reason(exc)) from exc
        tmp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            _replace(tmp, target)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise SettingsWriteError(target, _write_reason(exc)) from exc
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        # 收紧权限也在锁里：icacls 改 ACL 的那一下别的线程来读会 PermissionError
        restrict_to_owner(target)
    return target


@contextmanager
def editing_settings(path: str | Path | None = None) -> Iterator[Settings]:
    """「读文件层 → 改 → 存」整段持锁：几个保存同时到时一个一个来，后一个读到的是前一个写完的文件，谁的改动都不丢。
    with 块里抛异常（校验没过、请求被拒）就不写盘——坏文件也原样留着。用法::

        with editing_settings() as current:
            current.local_hints_dismissed = [...]
    """

    target = Path(path) if path else settings_path()
    # 进程内（_LOCK）+ 跨进程（_process_lock）两把锁都拿住，另一个 StoryStudio 窗口的保存也得排队
    with _LOCK, _process_lock(target):
        stamp = _file_stamp(target)
        current = load_settings_for_update(target)
        before = current.as_dict()
        yield current
        if _file_stamp(target) != stamp:
            # 读完到现在文件被别处改过——不认这把锁的写入方（旧版本、手工用编辑器改）：以最新的文件为底、只盖这次
            # 改到的字段，而不是拿读时的旧内容整份覆盖
            current = _merge_changes(before, current.as_dict(), load_settings_for_update(target))
        save_settings(current, target)


def restrict_to_owner(path: Path) -> bool:
    """设置文件只给当前用户读写：POSIX chmod 600；Windows 用 icacls 去掉继承、只授当前用户。
    尽力而为，失败不抛（密钥仍在，只是权限没收紧）；返回是否成功。"""

    try:
        if os.name == "nt":
            import subprocess

            user = os.environ.get("USERNAME") or ""
            if not user:
                return False
            result = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            return result.returncode == 0
        os.chmod(path, 0o600)
        return True
    except Exception:  # noqa: BLE001
        return False


def _merge_api_key(current_key: str, values: Mapping[str, Any]) -> str:
    """密钥字段的合并语义：``null`` = 清空；空串 / 打码值 = 保留（表单回传的就是打码值）；其余 = 新密钥。"""

    if "api_key" not in values:
        return current_key
    if values["api_key"] is None:
        return ""
    incoming = str(values["api_key"] or "").strip()
    if incoming and "*" not in incoming:
        return incoming
    return current_key


def _origin(url: str) -> tuple[str, str, int | None]:
    """接口地址的「来源」：(scheme, 小写主机, 端口——没写按 scheme 的默认端口)。没写 scheme 按 http 算；
    解析不了的地址返回一个跟任何合法地址都不相等的值（换成解析不了的地址也算换了地方）。"""

    from urllib.parse import urlsplit

    text = str(url or "").strip()
    if text and "://" not in text:
        text = "http://" + text
    try:
        parts = urlsplit(text)
        scheme = parts.scheme.lower()
        port = parts.port or {"http": 80, "https": 443}.get(scheme)
        return scheme, (parts.hostname or "").lower(), port
    except ValueError:
        return "invalid", text, None


def update_settings(patch: Mapping[str, Any], path: str | Path | None = None) -> Settings:
    """合并式更新：只覆盖给到的字段；api_key 传打码值或空串时保留原值，传 ``null`` 清空。

    密钥跟着「适配器 + 接口地址的来源（scheme、主机、端口）」走：同一个 OpenAI 兼容适配器，地址从 api.openai.com
    改到别的主机，旧密钥下一次请求就会以 Bearer 头发到新主机上（2026-09-23 发布前自查 SEC-3）。所以地址换了来源、
    而这次保存没带新密钥（表单回传的是打码值 / 空串 / 干脆没传）时，旧密钥清掉，界面提示重新填；只改路径（加减 /v1、
    尾斜杠、主机大小写）、留空退回适配器默认地址但来源不变的，密钥照留。之前没填过地址的不清（密钥还没绑到任何主机上）。

    换来源（adapter 变了，包括置空）时旧密钥一并清掉：密钥是跟来源走的，OpenAI 的 sk- 留到本机 SD WebUI 上，
    每个请求都会把它以 Bearer 头发到那台机器（局域网显卡机可能不是自己的）。来源没变就不动密钥——
    哪怕这个来源的表单里没有密钥栏：SD WebUI 挡在反向代理后面时手配的密钥（docs/local-models-spec.md §8.1
    「填了才带」）不能被同一张卡的一次普通保存（改个模型）删掉；设置页对这种「表单里看不见的密钥」
    单独给了提示和「清除密钥」。

    以文件层为基底（不叠环境变量），返回的是写盘后重新读出的生效配置（含环境变量），
    接口回给界面的仍是实际会用的值。整段「读 → 改 → 存」持锁（见 :func:`editing_settings`）。"""

    with editing_settings(path) as current:
        roles = patch.get("roles") or {}
        for name, values in roles.items():
            if name not in ROLES or not isinstance(values, Mapping):
                continue
            role = current.role(name)
            previous_adapter = role.adapter
            # 按生效地址比（空地址补上适配器默认值）：留空退回默认地址、而默认地址就是原来那台主机时，不算换了地方；
            # 原来没填地址、用的是默认地址（api.openai.com）时，改成别的主机同样要清
            previous_base = effective(role, name).base_url
            for key in ("adapter", "base_url", "model", "voice"):
                if key in values and values[key] is not None:
                    setattr(role, key, str(values[key]).strip())
            if "adapter" in values and values["adapter"] is not None and role.adapter != previous_adapter:
                role.api_key = ""
            elif previous_base and _origin(effective(role, name).base_url) != _origin(previous_base):
                role.api_key = ""   # 同一个适配器换了主机 / 端口 / 协议：旧密钥不发到新地址（这次带了新明文密钥的，下面照常写入）
            role.api_key = _merge_api_key(role.api_key, values)
            if isinstance(values.get("extra"), Mapping):
                role.extra = dict(values["extra"])
        for key in ("output_root", "cache_dir", "update_url"):
            if key in patch and patch[key] is not None:
                setattr(current, key, str(patch[key]).strip())
        if patch.get("local_hints_dismissed") is not None:
            current.local_hints_dismissed = normalize_hint_dismissals(patch["local_hints_dismissed"])
        gateway = patch.get("gateway")
        if isinstance(gateway, Mapping):
            if "base_url" in gateway and gateway["base_url"] is not None:
                new_base = str(gateway["base_url"]).strip().rstrip("/")
                # 网关同一条规则：换了来源，旧网关密钥不跟过去（已经应用过网关的角色各自抄了地址和密钥，不受影响）
                if current.gateway.base_url and _origin(new_base) != _origin(current.gateway.base_url):
                    current.gateway.api_key = ""
                current.gateway.base_url = new_base
            current.gateway.api_key = _merge_api_key(current.gateway.api_key, gateway)
    return load_settings(path)


def apply_gateway(settings: Settings, models: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """把网关应用到给出的角色：adapter 换成 OpenAI 兼容那一档，地址和密钥用网关的，模型按给的填。
    只动 models 里出现的角色；返回改了哪些。"""

    if not settings.gateway.configured:
        raise ValueError("先填网关地址")
    changed: list[str] = []
    for name, values in models.items():
        if name not in GATEWAY_ADAPTERS or not isinstance(values, Mapping):
            continue
        role = settings.role(name)
        role.adapter = GATEWAY_ADAPTERS[name]
        role.base_url = settings.gateway.base_url
        role.api_key = settings.gateway.api_key
        if values.get("model") is not None:
            role.model = str(values["model"]).strip()
        if name == "voice" and values.get("voice") is not None:
            role.voice = str(values["voice"]).strip()
        changed.append(name)
    return changed


def uses_gateway(settings: Settings, role: RoleConfig) -> bool:
    """这个角色是不是走网关（地址一致且是 OpenAI 兼容那一档）。模型目录按此决定查哪份列表。"""

    if not settings.gateway.configured or not role.base_url:
        return False
    return role.base_url.rstrip("/") == settings.gateway.base_url.rstrip("/") and role.adapter in GATEWAY_ADAPTERS.values()


def adapter_spec(name: str, adapter: str) -> dict[str, Any] | None:
    """某个角色下某个来源的表单描述（label / hint / fields / defaults）；不在表里（没有这个内置来源、提供它的插件也没加载）
    返回 ``None``。先确保插件已加载——插件登记的来源也在这张表里。"""

    from . import plugins

    plugins.ensure_loaded()
    return ADAPTERS.get(name, {}).get(adapter)


def adapter_presets() -> dict[str, dict[str, dict[str, Any]]]:
    """设置页要的整张来源表（内置 + 已加载插件登记的）。"""

    from . import plugins

    plugins.ensure_loaded()
    return ADAPTERS


def adapter_unavailable(settings: Settings, name: str) -> str | None:
    """角色选了一个现在不在表里的来源（提供它的插件没装上 / 加载失败 / 插件加载关着）时给一句人话；来源可用或没选返回 ``None``。

    设置文件原样保留这个来源（读设置不校验来源名），装好插件重启后什么都不用改就能接着用。这句会进账本和接口的 400，
    所以路径只给不带用户名的写法（``~/.storystudio/plugins``）。"""

    role = settings.role(name)
    if not role.adapter or adapter_spec(name, role.adapter) is not None:
        return None
    from . import plugins

    label = ROLE_LABELS.get(name, name)
    head = f"「{label}」的来源 {role.adapter} 现在用不了："
    switch = f"或去「模型设置」给「{label}」换一个来源"
    reason = plugins.failure_reason(plugins.owner_of_adapter(name, role.adapter))
    if reason:
        # 插件在、但没加载成：叫人「把插件放进目录」是错的（它可能就在环境变量指的目录里），说清为什么没加载成
        return f"{head}提供它的插件没能加载（{reason}）——修好插件后重启程序，{switch}"
    if plugins.disabled():
        return f"{head}它不是内置来源，提供它的插件没有加载（{plugins.DISABLED_NOTE}）——去掉这个开关后重启程序，{switch}"
    return f"{head}它不是内置来源，提供它的插件没有加载——把插件放进 {plugins.display_dir()} 后重启程序，{switch}"


def validate_role(settings: Settings, name: str) -> list[str]:
    """返回缺失项；空列表 = 可用。"""

    role = settings.role(name)
    if not role.adapter:
        return [f"{name}: 没选适配器"]
    spec = adapter_spec(name, role.adapter)
    if spec is None:
        return [f"{name}: 来源 {role.adapter} 没有装上"]
    # 本机服务（localhost / 私有网段）通常没有鉴权，密钥留空要放行——否则 Ollama / ComfyUI 这类
    # 来源永远算「未配置」，roleOk() 和能力检查都会拦人（DESIGN §45）。远端仍然必填。
    local = role_is_local(settings, name)
    missing = []
    for key in spec["fields"]:
        if key == "api_key" and local:
            continue
        if not getattr(role, key, "") and not spec.get("defaults", {}).get(key):
            missing.append(f"{name}.{key}")
    return missing


# 角色在「模型设置」页上的名字：拦人的 400 文案要说清去配哪个角色
ROLE_LABELS = {"text": "剧本文本", "image": "生图", "voice": "语音", "video": "视频", "music": "配乐"}


def role_missing_message(settings: Settings, name: str) -> str | None:
    """角色没配好时给一句能直接当接口 400 的人话（哪个角色、去哪配、缺什么）；配好了返回 ``None``。

    「角色配好没有」只有 ``validate_role`` 这一处判断——路由层起任务前的拦截、``RoutedProvider`` 装配适配器、
    能力检查都问它，各处口径才一致。2026-09-22 审计：设置为空（全新安装）时各模块有的 400、有的起任务再
    逐项失败却记成「成功」、短剧直接把项目卡死；现在统一为「起任务之前 400」。
    """

    unavailable = adapter_unavailable(settings, name)
    if unavailable:
        return unavailable
    missing = validate_role(settings, name)
    if not missing:
        return None
    label = ROLE_LABELS.get(name, name)
    if not settings.role(name).adapter and not adapter_presets().get(name):
        # 这一档一个可选的来源都没有（比如配乐：内置来源不覆盖这一档）：叫人「选一个来源」没有意义
        return f"「{label}」目前没有可选的来源：内置来源不覆盖这一档，要装一个提供它的插件，装好后在「模型设置」里选上它"
    return f"还没配置{label}模型——去「模型设置」给「{label}」选一个来源并填密钥（{', '.join(missing)}）"


def role_is_local(settings: Settings, name: str) -> bool:
    """这个角色跑在本机吗——补上适配器默认地址之后再判断（地址栏留空、默认就是 127.0.0.1 的适配器也算）。
    本地 = 不要 key、不花钱（预估与各处 cost_note 改说「本地生成，不花钱」）。"""

    if name not in ROLES:
        return False
    role = settings.role(name)
    if not role.adapter:
        return False
    return is_local_url(effective(role, name).base_url)


def effective(role: RoleConfig, name: str) -> RoleConfig:
    """把适配器默认值补进空字段后的副本。"""

    spec = adapter_spec(name, role.adapter) or {}
    merged = RoleConfig(adapter=role.adapter, base_url=role.base_url, api_key=role.api_key, model=role.model,
                        voice=role.voice, extra=dict(role.extra))
    for key, value in (spec.get("defaults") or {}).items():
        if not getattr(merged, key, ""):
            setattr(merged, key, value)
    return merged


def _apply(settings: Settings, data: Mapping[str, Any]) -> None:
    for name, values in (data.get("roles") or {}).items():
        if name in ROLES and isinstance(values, Mapping):
            role = settings.role(name)
            for key in ("adapter", "base_url", "api_key", "model", "voice"):
                setattr(role, key, str(values.get(key) or ""))
            role.extra = dict(values.get("extra") or {})
    settings.output_root = str(data.get("output_root") or "")
    settings.cache_dir = str(data.get("cache_dir") or "")
    settings.update_url = str(data.get("update_url") or "")
    gw = data.get("gateway") if isinstance(data.get("gateway"), Mapping) else {}
    settings.gateway = Gateway(base_url=str(gw.get("base_url") or "").rstrip("/"), api_key=str(gw.get("api_key") or ""))
    settings.local_hints_dismissed = normalize_hint_dismissals(data.get("local_hints_dismissed"))


def normalize_hint_dismissals(value: Any) -> list[str]:
    """本地提示的关闭名单：只认角色名和 ``"all"``，去重保序；有 ``"all"`` 就只留它。"""

    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        name = str(item or "").strip()
        if name in ("all", *ROLES) and name not in out:
            out.append(name)
    return ["all"] if "all" in out else out


def _apply_env(settings: Settings) -> None:
    """环境变量覆盖（CI / 无头部署用）：STORYSTUDIO_<ROLE>_<FIELD>，如 STORYSTUDIO_IMAGE_API_KEY。"""

    for name in ROLES:
        role = settings.role(name)
        for key in ("adapter", "base_url", "api_key", "model", "voice"):
            value = os.environ.get(f"STORYSTUDIO_{name.upper()}_{key.upper()}")
            if value:
                setattr(role, key, value)


__all__ = ["ROLES", "ADAPTERS", "RoleConfig", "Settings", "SettingsFileError", "SettingsWriteError", "settings_error_message",
           "load_settings", "load_settings_or_default",
           "load_settings_for_update", "editing_settings", "broken_settings_message", "quarantine_broken_settings", "save_settings", "update_settings",
           "validate_role", "role_is_local", "effective", "settings_path", "home_dir", "cache_dir", "restrict_to_owner",
           "normalize_hint_dismissals", "ROLE_LABELS", "role_missing_message", "adapter_spec", "adapter_presets",
           "adapter_unavailable"]
