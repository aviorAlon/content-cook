"""本地模型：本机在跑的推理服务的预设、探活扫描、模型列表、一键应用到角色。

跟云端来源的差别只有三件事（契约见 ``docs/local-models-spec.md``）：

1. **不要 key**——本机服务通常没有鉴权，``is_local_url`` 判定出来的地址允许 ``api_key`` 留空
   （``settings.validate_role`` 放行；openai SDK 要求非空，由 :func:`api_key_for` 补一个占位值）；
2. **不花钱**——预估与各处 ``cost_note`` 改说 :func:`free_cost_note` 那一句，不报假的美元数；
3. **要能被发现**——:func:`scan` 并发探一遍常见端口，用户不用去记 ``http://127.0.0.1:11434/v1``。

**只探本机地址。** 预设的地址是写死的 localhost，用户自填的地址过 :func:`is_local_url`
（localhost / 127.0.0.0/8 / ::1 / 私有网段）才会被连——不能把「扫描本机」做成一个内网扫描器。

HTTP 走一个可注入的 ``fetch(url, timeout) -> 解析好的 JSON``（默认 urllib，跟 ``model_catalog``
同一套写法），测试用假客户端。
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注，运行时不导入（settings 反过来要导入本模块）
    from .settings import Settings

# openai SDK 构造客户端时要求 api_key 非空。本机服务没有鉴权，用户会把 key 留空——
# 这里显式给一个占位值，它**不是凭据**，只是为了过 SDK 的非空校验。
PLACEHOLDER_API_KEY = "local"

# 模型列表的四种形状（各自的解析在 parse_models）
KIND_OLLAMA = "ollama_tags"            # {"models": [{"name": "...", "model": "..."}]}
KIND_OPENAI = "openai_models"          # {"data": [{"id": "..."}]}
KIND_COMFYUI = "comfyui_object_info"   # {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [[...]]}}}}
KIND_SDWEBUI = "sdwebui_models"        # [{"title": "...", "model_name": "..."}]


@dataclass(frozen=True)
class LocalService:
    """一个本机推理服务的预设。``base_url`` 给适配器用，``probe`` 探活，``models_probe`` 取模型列表
    （空 = 跟 ``probe`` 同一个地址）。用户改地址时只有主机和端口会变，路径按预设里的原样接上。"""

    id: str
    name: str
    roles: tuple[str, ...]
    base_url: str
    probe: str
    kind: str
    adapter: Mapping[str, str] = field(default_factory=dict)
    models_probe: str = ""
    needs_key: bool = False
    doc: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "roles": list(self.roles), "base_url": self.base_url,
                "adapter": dict(self.adapter), "needs_key": self.needs_key, "doc": self.doc}


PRESETS: tuple[LocalService, ...] = (
    LocalService(
        id="ollama", name="Ollama", roles=("text",),
        base_url="http://127.0.0.1:11434/v1",
        probe="http://127.0.0.1:11434/api/tags",
        kind=KIND_OLLAMA,
        adapter={"text": "openai_chat"},
        doc="最省事的本地文本服务：ollama pull qwen2.5 之后就能用；OpenAI 兼容接口在 /v1。",
    ),
    LocalService(
        id="lmstudio", name="LM Studio", roles=("text",),
        base_url="http://127.0.0.1:1234/v1",
        probe="http://127.0.0.1:1234/v1/models",
        kind=KIND_OPENAI,
        adapter={"text": "openai_chat"},
        doc="桌面客户端里点「Start Server」即可；模型列表就是它加载的那几个。",
    ),
    LocalService(
        id="vllm", name="vLLM", roles=("text",),
        base_url="http://127.0.0.1:8000/v1",
        probe="http://127.0.0.1:8000/v1/models",
        kind=KIND_OPENAI,
        adapter={"text": "openai_chat"},
        doc="有显卡时吞吐最好的一档：python -m vllm.entrypoints.openai.api_server --model <路径>。",
    ),
    LocalService(
        id="llamacpp", name="llama.cpp server", roles=("text",),
        base_url="http://127.0.0.1:8080/v1",
        probe="http://127.0.0.1:8080/v1/models",
        kind=KIND_OPENAI,
        adapter={"text": "openai_chat"},
        doc="llama-server -m <gguf 文件> --port 8080；一次只服务一个模型。",
    ),
    LocalService(
        id="comfyui", name="ComfyUI", roles=("image", "video"),
        base_url="http://127.0.0.1:8188",
        probe="http://127.0.0.1:8188/system_stats",
        models_probe="http://127.0.0.1:8188/object_info/CheckpointLoaderSimple",
        kind=KIND_COMFYUI,
        adapter={"image": "comfyui", "video": "comfyui"},
        doc="工作流最灵活：出图和出视频都靠一份 API 格式工作流 + 一份映射，见设置页的「工作流」那一行。",
    ),
    LocalService(
        id="sdwebui", name="Stable Diffusion WebUI / Forge", roles=("image",),
        base_url="http://127.0.0.1:7860",
        probe="http://127.0.0.1:7860/sdapi/v1/sd-models",
        kind=KIND_SDWEBUI,
        adapter={"image": "sdwebui"},
        doc="启动时要带 --api（A1111 / Forge 都一样）；txt2img / img2img 同步返回，直接落盘。",
    ),
    LocalService(
        id="openai_speech_local", name="本地语音（OpenAI 兼容 /v1/audio/speech）", roles=("voice",),
        base_url="http://127.0.0.1:8880/v1",
        probe="http://127.0.0.1:8880/v1/models",
        kind=KIND_OPENAI,
        adapter={"voice": "openai_speech"},
        doc="Kokoro-FastAPI 这类把 /v1/audio/speech 实现在本机的服务；音色 id 以它自己的列表为准。",
    ),
)

PRESETS_BY_ID: dict[str, LocalService] = {s.id: s for s in PRESETS}


# ---------------------------------------------------------------------------- 地址判定


# 允许连的网段，白名单（不用 ipaddress 的 is_private——它把 240.0.0.0/4、100.64.0.0/10 也算私有，
# 而 ::1 又落在 is_reserved 里，语义对不上「本机 / 家里那台机器」这件事）。
_LOCAL_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10",
))


def _with_scheme(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    return text if "://" in text else "http://" + text


def is_local_url(url: str) -> bool:
    """这个地址是不是本机 / 本地网段：``localhost``、``127.0.0.0/8``、``::1``、私有网段。

    只认主机名本身——``http://127.0.0.1.evil.com``、``http://127.0.0.1@evil.com`` 这类
    「长得像本机」的公网地址一律拒绝（``urlsplit`` 解析出来的 hostname 分别是
    ``127.0.0.1.evil.com`` 和 ``evil.com``，都不是 IP 也不是 localhost）。
    """

    text = _with_scheme(url)
    if not text:
        return False
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    try:
        host = parts.hostname
    except ValueError:      # 端口写坏了之类
        return False
    if not host:
        return False
    host = host.strip().rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False        # 不是 IP 又不是 localhost：域名一律不算本机
    return any(ip in net for net in _LOCAL_NETWORKS if ip.version == net.version)


def api_key_for(base_url: str, api_key: str) -> str:
    """适配器构造 openai SDK 客户端时用的 key：填了就用填的；本机地址留空时补占位值
    （SDK 要求非空，占位值不是凭据）；远端留空仍是原来的 ``missing``（那是配错了，让它明确报错）。"""

    if api_key:
        return api_key
    return PLACEHOLDER_API_KEY if is_local_url(base_url) else "missing"


def free_cost_note(count: int) -> str:
    """本地来源的「这次要发几个请求」那一句：不报美元数，只说不花钱和请求次数。"""

    return f"本地生成，不花钱（{int(count)} 次请求）"


def role_local(role: str, settings: "Settings | None" = None) -> bool:
    """这个角色现在配的是本机来源吗。设置文件读不出来就按「不是本地」——
    宁可多说一句"会花钱"，也不能把付费来源说成免费的。"""

    from . import settings as settings_mod

    try:
        current = settings if settings is not None else settings_mod.load_settings()
        return settings_mod.role_is_local(current, role)
    except Exception:  # noqa: BLE001 - 设置文件坏了不该让预估整个报错
        return False


def cost_note_for(role: str, count: int, remote: str, settings: "Settings | None" = None) -> str:
    """各处 ``cost_note`` 的统一入口：本地来源说「本地生成，不花钱（N 次请求）」，
    其余照旧用来源自己那句（模型名 / 跳过规则）。"""

    return free_cost_note(count) if role_local(role, settings) else remote


def _root(url: str) -> str:
    parts = urllib.parse.urlsplit(_with_scheme(url))
    return f"{parts.scheme}://{parts.netloc}"


def _path(url: str) -> str:
    parts = urllib.parse.urlsplit(_with_scheme(url))
    return parts.path or "/"


def probe_url(service: LocalService, base_url: str = "") -> str:
    """探活地址：主机端口跟着用户填的 ``base_url`` 走，路径用预设里的。"""

    return _root(base_url or service.base_url) + _path(service.probe)


def models_url(service: LocalService, base_url: str = "") -> str:
    return _root(base_url or service.base_url) + _path(service.models_probe or service.probe)


def adapter_url(service: LocalService, base_url: str = "") -> str:
    """给适配器用的地址：主机端口跟着用户填的走，路径按预设接上（Ollama / LM Studio / vLLM / llama.cpp /
    本地语音的 OpenAI 兼容接口都在 ``/v1`` 下）。用户自己写了路径就照用户的（``.../v1`` 原样回显，也允许
    反代改了前缀的写法）。探活一直是这么拼的（:func:`probe_url`），适配器地址之前却原样用了用户的 host:port，
    「探得到」和「用得了」脱节——应用后文本角色打 ``/chat/completions`` 根路径 404（ADP-03）。"""

    address = str(base_url or "").strip()
    if not address:
        return service.base_url.rstrip("/")
    path = _path(address)
    if path in ("", "/"):
        path = _path(service.base_url)
    return (_root(address) + path).rstrip("/")


# ---------------------------------------------------------------------------- 取模型列表


Fetch = Callable[[str, float], Any]


def default_fetch(url: str, timeout: float) -> Any:
    """一次 GET + JSON 解析。跟 ``model_catalog._get_json`` 同一套写法，只是不带密钥。"""

    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "storystudio"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 地址已过 is_local_url
        return json.loads(response.read().decode("utf-8"))


def _dedup(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def parse_models(kind: str, payload: Any) -> list[str]:
    """四种列表形状 → 一串模型 id。形状不对就是空列表（探活成功但列不出模型，不算失败）。"""

    if kind == KIND_OLLAMA:
        items = payload.get("models") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            return []
        return _dedup(m.get("name") or m.get("model") for m in items if isinstance(m, Mapping))
    if kind == KIND_OPENAI:
        items = payload.get("data") if isinstance(payload, Mapping) else payload
        if not isinstance(items, list):
            return []
        return _dedup(m.get("id") if isinstance(m, Mapping) else m for m in items)
    if kind == KIND_SDWEBUI:
        items = payload.get("data") if isinstance(payload, Mapping) else payload
        if not isinstance(items, list):
            return []
        return _dedup((m.get("model_name") or m.get("title")) if isinstance(m, Mapping) else m for m in items)
    if kind == KIND_COMFYUI:
        # {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["a.safetensors", …], {}]}}}}
        if not isinstance(payload, Mapping):
            return []
        for node in payload.values():
            if not isinstance(node, Mapping):
                continue
            required = ((node.get("input") or {}) if isinstance(node.get("input"), Mapping) else {}).get("required")
            if not isinstance(required, Mapping):
                continue
            for spec in required.values():
                if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], list):
                    return _dedup(spec[0])
        return []
    return []


def models_for(service: LocalService | str, base_url: str = "", *, timeout: float = 1.2, fetch: Fetch | None = None) -> list[str]:
    """拉一次这个服务的模型列表。地址不是本机的直接拒绝（``ValueError``）。"""

    svc = service if isinstance(service, LocalService) else preset(str(service))
    url = models_url(svc, base_url)
    if not is_local_url(url):
        raise ValueError(f"只能扫本机地址（localhost / 127.0.0.1 / ::1 / 私有网段），收到：{base_url or svc.base_url}")
    payload = (fetch or default_fetch)(url, timeout)
    return parse_models(svc.kind, payload)


def preset(service_id: str) -> LocalService:
    svc = PRESETS_BY_ID.get(str(service_id or "").strip())
    if svc is None:
        raise ValueError(f"不认识的本地服务：{service_id}")
    return svc


# ---------------------------------------------------------------------------- 扫描


def _probe_one(service: LocalService, base_url: str, timeout: float, fetch: Fetch) -> dict[str, Any]:
    address = base_url or service.base_url
    entry: dict[str, Any] = {
        # 行里回显的是适配器真正要用的地址（预设路径已接上），设置页「应用」时原样传回即可
        "id": service.id, "name": service.name, "roles": list(service.roles), "base_url": adapter_url(service, base_url),
        "adapter": dict(service.adapter), "doc": service.doc, "alive": False, "models": [], "error": "",
        # 被推荐时给的那一句（设置页的「推荐」标记用它；不推荐的行也带着，界面自己决定显不显示）
        "reason": RECOMMEND_REASON.get(service.id, ""),
    }
    if not is_local_url(address):
        entry["error"] = "只能扫本机地址（localhost / 127.0.0.1 / ::1 / 私有网段）"
        return entry
    try:
        payload = fetch(probe_url(service, base_url), timeout)
    except Exception as exc:  # noqa: BLE001 - 没起服务是常态，把原因原样留给界面
        entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return entry
    entry["alive"] = True
    if service.models_probe:
        try:
            payload = fetch(models_url(service, base_url), timeout)
        except Exception as exc:  # noqa: BLE001 - 活着但列不出模型：仍算探到了
            entry["error"] = f"探到了，但取模型列表失败：{type(exc).__name__}: {exc}"[:300]
            return entry
    entry["models"] = parse_models(service.kind, payload)
    return entry


def scan(ids: Sequence[str] | None = None, timeout: float = 1.2, *,
         fetch: Fetch | None = None, base_urls: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """并发探一遍预设（或指定的几个）：``[{id, name, roles, base_url, alive, models, error}]``。

    ``base_urls`` 给用户手填的地址（``{服务 id: 地址}``），非本机地址直接 ``ValueError``，
    一个请求都不发。单个服务探失败只写进它自己那条 ``error``，不影响其余。
    """

    wanted = list(PRESETS) if not ids else [preset(i) for i in ids]
    overrides = {str(k): str(v or "").strip() for k, v in (base_urls or {}).items()}
    for service_id, address in overrides.items():
        if address and not is_local_url(address):
            raise ValueError(f"只能扫本机地址（localhost / 127.0.0.1 / ::1 / 私有网段），收到：{address}")
    call = fetch or default_fetch
    if not wanted:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(wanted))) as pool:
        futures = [pool.submit(_probe_one, s, overrides.get(s.id, ""), timeout, call) for s in wanted]
        return [f.result() for f in futures]


# ---------------------------------------------------------------------------- 应用到角色


def apply_to_role(settings: "Settings", service_id: str, role: str, model: str = "", base_url: str = "") -> "Settings":
    """把某个探到的服务应用到某个角色：写 ``adapter`` / ``base_url`` / ``model``，``api_key`` 清空。

    不写盘（调用方决定什么时候 ``save_settings``），返回同一个 ``settings`` 对象方便串起来用。
    """

    from . import settings as settings_mod

    svc = preset(service_id)
    if role not in settings_mod.ROLES:
        raise ValueError(f"未知角色：{role}")
    adapter = svc.adapter.get(role)
    if not adapter:
        raise ValueError(f"{svc.name} 不能配「{role}」这个角色（它只能配 {' / '.join(svc.roles)}）")
    address = (base_url or svc.base_url).strip()
    if not is_local_url(address):
        raise ValueError(f"只能应用本机地址（localhost / 127.0.0.1 / ::1 / 私有网段），收到：{address}")
    cfg = settings.role(role)
    cfg.adapter = adapter
    cfg.base_url = adapter_url(svc, address)      # 主机端口取用户的，路径按预设接上（/v1）
    cfg.api_key = ""      # 本机服务不要 key；留着旧密钥反而会被发到本机服务上去
    if model:
        cfg.model = str(model).strip()
    return settings


# ---------------------------------------------------------------------------- 推荐（不是要求）
#
# 推荐而不强制（DESIGN §45）：本地永远只是一条**可选**的路，
# 云端来源什么都不做也照常能用。这里只回答一个问题——**要是现在真有个本机服务在跑，该点它的名字还是别的？**


# 每个角色按什么顺序点名。生图优先 SD WebUI：接口是固定的 /sdapi/v1/txt2img，装好就能用；
# ComfyUI 要先有一份对得上的工作流，所以只在它是唯一在跑的那个、或者角色是视频（SD WebUI 不出视频）时才被点名。
ROLE_PREFERENCE: dict[str, tuple[str, ...]] = {
    "text": ("ollama", "lmstudio", "vllm", "llamacpp"),
    "image": ("sdwebui", "comfyui"),
    "video": ("comfyui",),
    "voice": ("openai_speech_local",),
    "music": (),        # 没有本机音乐服务的预设：这一档永远不推荐
}

# 被点名时给的那一句「为什么是它」。都是陈述句：说清它是什么，不说「你得先装」。
RECOMMEND_REASON: dict[str, str] = {
    "sdwebui": "出图推荐：接口固定、装好就能用",
    "comfyui": "本地生视频 / Flux 走它",
    "ollama": "本地文本推荐：接口就是 OpenAI 那套",
    "lmstudio": "桌面客户端自带服务端，点一下就在跑",
    "vllm": "有显卡时吞吐最好的一档",
    "llamacpp": "一个 gguf 文件就是一个模型",
    "openai_speech_local": "本地语音走它（/v1/audio/speech）",
}


def presets_for_role(role: str) -> tuple[LocalService, ...]:
    """能配这个角色的预设，按推荐顺序排。角色不认识、或这一档没有本机服务时是空元组。"""

    order = ROLE_PREFERENCE.get(str(role or ""), ())
    return tuple(PRESETS_BY_ID[i] for i in order if i in PRESETS_BY_ID)


def recommend(role: str, alive: Iterable[str]) -> str:
    """在跑的这些服务里，这个角色该点名哪个（按 :data:`ROLE_PREFERENCE`）。没有就是空串。"""

    running = {str(x) for x in alive}
    for service_id in ROLE_PREFERENCE.get(str(role or ""), ()):
        if service_id in running:
            return service_id
    return ""


def reason_for(service_id: str) -> str:
    return RECOMMEND_REASON.get(str(service_id or ""), "")


def hint_dismissed(settings: "Settings", role: str) -> bool:
    """用户关掉过这条提示吗（``Settings.local_hints_dismissed``，存在设置文件里，重启还在）。
    关掉之后不会自己回来——设置页的「重新开启本地提示」是唯一的复位入口。"""

    entries = {str(x) for x in (getattr(settings, "local_hints_dismissed", None) or [])}
    return "all" in entries or str(role) in entries


__all__ = [
    "LocalService", "PRESETS", "PRESETS_BY_ID", "PLACEHOLDER_API_KEY",
    "KIND_OLLAMA", "KIND_OPENAI", "KIND_COMFYUI", "KIND_SDWEBUI",
    "ROLE_PREFERENCE", "RECOMMEND_REASON",
    "is_local_url", "api_key_for", "free_cost_note", "preset", "probe_url", "models_url", "adapter_url",
    "default_fetch", "parse_models", "models_for", "scan", "apply_to_role",
    "presets_for_role", "recommend", "reason_for", "hint_dismissed",
]
