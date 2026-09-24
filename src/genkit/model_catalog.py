"""模型目录：从服务商的公开模型列表拉一份「现在到底有哪些模型、各自吃什么出什么、参数区间是多少」，
补能力表（capabilities.py）静态之不足——能力表里手写的是验证过/按文档的组合，目录是服务商此刻的真话。

目前只做 OpenRouter，三个公开接口都不需要密钥（有密钥也一并带上）：

* ``GET /models``         ：对话/文本模型（也含少数出图、出语音的多模态模型），按 ``architecture`` 的输入输出模态分角色
* ``GET /images/models``  ：生图模型，带 ``supported_parameters``（画幅枚举、参考图张数上限）
* ``GET /videos/models``  ：视频模型，带 ``supported_durations`` / ``supported_resolutions`` / ``supported_aspect_ratios`` /
  ``supported_frame_images`` / ``generate_audio``

后两者的参数区间被压成和能力表同形的 ``caps``（durations / resolutions / aspect_ratios / first_frame / audio /
reference_images），能力检查会拿它覆盖静态表（source = ``catalog``，按提醒级别处理，不硬拦）。

缓存：``~/.storystudio/model_catalog.json``（按来源分节，带拉取时间）。不自动刷新——用户在设置页点一下；
没拉过目录时所有检查都静默跳过，绝不因为目录缺席拦人。
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from . import settings as settings_mod

SOURCES: dict[str, dict[str, str]] = {
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
    # 统一网关：地址来自模型设置里的 gateway，列表是 OpenAI 形状的 GET /models（没有模态信息，按 id 猜角色）
    "gateway": {"label": "统一网关", "base_url": ""},
}
ROLES = ("text", "image", "video", "voice")
# 适配器 → 目录来源；不在表里的适配器不做目录检查
ADAPTER_SOURCE = {"openrouter_images": "openrouter", "openrouter_video": "openrouter", "openrouter_tts": "openrouter"}


def catalog_path() -> Path:
    return settings_mod.home_dir() / "model_catalog.json"


def load_catalog() -> dict[str, Any]:
    path = catalog_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_catalog(data: Mapping[str, Any]) -> Path:
    path = catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(data), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


# ---------------------------------------------------------------------------- 解析


def _modalities(entry: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    arch = entry.get("architecture") if isinstance(entry.get("architecture"), Mapping) else {}
    inputs = [str(x).lower() for x in (arch.get("input_modalities") or [])]
    outputs = [str(x).lower() for x in (arch.get("output_modalities") or [])]
    if not inputs and not outputs:
        # 老字段：modality "text+image->text"
        modality = str(arch.get("modality") or "")
        if "->" in modality:
            left, right = modality.split("->", 1)
            inputs = [x.strip() for x in left.split("+") if x.strip()]
            outputs = [x.strip() for x in right.split("+") if x.strip()]
    return inputs, outputs


def classify(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """``/models`` 的一条记录 → 我们关心的形状；没有 id 的丢掉。"""

    model_id = str(entry.get("id") or "").strip()
    if not model_id:
        return None
    inputs, outputs = _modalities(entry)
    roles: list[str] = []
    if "image" in outputs:
        roles.append("image")
    if "video" in outputs:
        roles.append("video")
    if "audio" in outputs:
        roles.append("voice")
    if outputs == ["text"] or (not roles and "text" in outputs):
        roles.append("text")
    pricing = entry.get("pricing") if isinstance(entry.get("pricing"), Mapping) else {}
    return {
        "id": model_id,
        "name": str(entry.get("name") or model_id),
        "input": inputs,
        "output": outputs,
        "roles": roles,
        "context_length": entry.get("context_length"),
        "pricing": {k: pricing[k] for k in ("prompt", "completion", "image", "request") if k in pricing},
    }


def classify_image(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """``/images/models`` 的一条记录：模态 + supported_parameters 压成 caps。"""

    model_id = str(entry.get("id") or "").strip()
    if not model_id:
        return None
    inputs, outputs = _modalities(entry)
    params = entry.get("supported_parameters") if isinstance(entry.get("supported_parameters"), Mapping) else {}
    caps: dict[str, Any] = {}
    ratio = params.get("aspect_ratio") if isinstance(params.get("aspect_ratio"), Mapping) else {}
    if isinstance(ratio.get("values"), list) and ratio["values"]:
        caps["aspect_ratios"] = [str(v) for v in ratio["values"]]
    refs = params.get("input_references") if isinstance(params.get("input_references"), Mapping) else None
    if refs is not None:
        caps["reference_images"] = int(refs.get("max") or 0) > 0
        caps["max_reference_images"] = int(refs.get("max") or 0)
    elif inputs:
        caps["reference_images"] = "image" in inputs
    return {
        "id": model_id,
        "name": str(entry.get("name") or model_id),
        "input": inputs or ["text"],
        "output": outputs or ["image"],
        "roles": ["image"],
        "caps": caps,
    }


def classify_video(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """``/videos/models`` 的一条记录：supported_* 压成 caps。"""

    model_id = str(entry.get("id") or "").strip()
    if not model_id:
        return None
    caps: dict[str, Any] = {}
    durations = entry.get("supported_durations")
    if isinstance(durations, list) and durations:
        nums = sorted({int(x) for x in durations if isinstance(x, (int, float))})
        if nums:
            caps["durations"] = {"choices": nums}
    for src, dst in (("supported_resolutions", "resolutions"), ("supported_aspect_ratios", "aspect_ratios")):
        values = entry.get(src)
        if isinstance(values, list) and values:
            caps[dst] = [str(v) for v in values]
    frames = entry.get("supported_frame_images")
    if isinstance(frames, list):
        caps["first_frame"] = "first_frame" in [str(f) for f in frames]
    if isinstance(entry.get("generate_audio"), bool):
        caps["audio"] = bool(entry["generate_audio"])
    first_frame = caps.get("first_frame", True)
    return {
        "id": model_id,
        "name": str(entry.get("name") or model_id),
        "input": ["text", "image"] if first_frame else ["text"],
        "output": ["video"],
        "roles": ["video"],
        "caps": caps,
    }


def merge_models(chat: list[dict[str, Any]], images: list[dict[str, Any]], videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """三份列表合成一份：专用接口（images / videos）的记录带 caps，覆盖 /models 里的同 id 条目。"""

    by_id: dict[str, dict[str, Any]] = {}
    for m in chat:
        by_id[m["id"].lower()] = dict(m)
    for m in images + videos:
        key = m["id"].lower()
        base = by_id.get(key, {})
        merged = {**base, **m}
        merged["roles"] = sorted(set(base.get("roles", [])) | set(m["roles"]))
        by_id[key] = merged
    models = list(by_id.values())
    models.sort(key=lambda m: m["id"])
    return models


# ---------------------------------------------------------------------------- 拉取


def _get_json(url: str, api_key: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "storystudio"})
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 固定的服务商地址
        return json.loads(response.read().decode("utf-8"))


def _items(payload: Any, what: str) -> list[Mapping[str, Any]]:
    items = payload.get("data") if isinstance(payload, Mapping) else payload
    if not isinstance(items, list):
        raise ValueError(f"{what} 不是 {{data: [...]}} 形状")
    return [x for x in items if isinstance(x, Mapping)]


_ID_HINTS: dict[str, tuple[str, ...]] = {
    "video": ("video", "sora", "seedance", "hailuo", "kling", "veo", "runway", "wan-", "wan2", "wan3", "pika", "luma", "minimax-h"),
    "image": ("image", "dall-e", "seedream", "flux", "imagen", "stable-diffusion", "sdxl", "midjourney", "recraft", "ideogram", "qwen-image", "gpt-image"),
    "voice": ("tts", "speech", "voice", "audio"),
}


def classify_by_id(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """OpenAI 形状的 /models 只有 id：按 id 里的词猜是出图 / 出视频 / 出语音，其余算文本。"""

    model_id = str(entry.get("id") or "").strip()
    if not model_id:
        return None
    low = model_id.lower()
    roles: list[str] = []
    for role, needles in _ID_HINTS.items():
        if any(n in low for n in needles):
            roles.append(role)
            break
    if not roles:
        roles = ["text"]
    inputs = ["text"] + (["image"] if roles[0] in ("image", "video") else [])
    return {"id": model_id, "name": model_id, "input": inputs, "output": [{"text": "text", "image": "image", "video": "video", "voice": "audio"}[roles[0]]],
            "roles": roles, "guessed": True}


def fetch_openai_models(base_url: str, api_key: str = "", timeout: float = 20.0) -> list[dict[str, Any]]:
    """任何 OpenAI 兼容网关的 GET /models。"""

    base = str(base_url or "").rstrip("/")
    if not base:
        raise ValueError("先填网关地址")
    items = _items(_get_json(f"{base}/models", api_key, timeout), "/models")
    models = [m for m in (classify_by_id(x) for x in items) if m]
    models.sort(key=lambda m: m["id"])
    return models


def fetch_openrouter(base_url: str | None = None, api_key: str = "", timeout: float = 20.0) -> list[dict[str, Any]]:
    """拉 /models、/images/models、/videos/models 三份并合并。后两个接口拉不到不算失败（只少了参数区间）。"""

    base = str(base_url or SOURCES["openrouter"]["base_url"]).rstrip("/")
    chat = [m for m in (classify(x) for x in _items(_get_json(f"{base}/models", api_key, timeout), "/models")) if m]
    images: list[dict[str, Any]] = []
    videos: list[dict[str, Any]] = []
    try:
        images = [m for m in (classify_image(x) for x in _items(_get_json(f"{base}/images/models", api_key, timeout), "/images/models")) if m]
    except Exception:  # noqa: BLE001 - 专用接口是锦上添花
        pass
    try:
        videos = [m for m in (classify_video(x) for x in _items(_get_json(f"{base}/videos/models", api_key, timeout), "/videos/models")) if m]
    except Exception:  # noqa: BLE001
        pass
    return merge_models(chat, images, videos)


def refresh(source: str = "openrouter", settings: settings_mod.Settings | None = None, fetcher: Any = None) -> dict[str, Any]:
    """拉一次目录并写缓存；返回该来源的节。密钥从模型设置里任一个用该来源的角色借。"""

    if source not in SOURCES:
        raise ValueError(f"不认识的目录来源：{source}")
    current = settings or settings_mod.load_settings()
    if source == "gateway":
        models = (fetcher or fetch_openai_models)(current.gateway.base_url, current.gateway.api_key)
    else:
        api_key = ""
        for role in settings_mod.ROLES:
            cfg = current.role(role)
            if ADAPTER_SOURCE.get(cfg.adapter) == source and cfg.api_key:
                api_key = cfg.api_key
                break
        models = (fetcher or fetch_openrouter)(SOURCES[source]["base_url"], api_key)
    part = {"fetched_at": int(time.time()), "models": models}
    data = load_catalog()
    data[source] = part
    save_catalog(data)
    return part


# ---------------------------------------------------------------------------- 查询


def section(source: str) -> dict[str, Any]:
    part = load_catalog().get(source)
    return part if isinstance(part, dict) and isinstance(part.get("models"), list) else {}


def models_for(role: str, source: str = "openrouter") -> list[dict[str, Any]]:
    return [m for m in section(source).get("models", []) if role in (m.get("roles") or ())]


def summary(source: str = "openrouter") -> dict[str, Any]:
    part = section(source)
    models = part.get("models", [])
    return {
        "source": source,
        "label": SOURCES.get(source, {}).get("label", source),
        "fetched_at": part.get("fetched_at"),
        "count": len(models),
        "roles": {
            role: [
                {"id": m["id"], "name": m["name"], "input": m.get("input", []), **({"caps": m["caps"]} if m.get("caps") else {})}
                for m in models if role in m.get("roles", ())
            ]
            for role in ROLES
        },
    }


def find(source: str, model_id: str) -> dict[str, Any] | None:
    wanted = (model_id or "").strip().lower()
    for m in section(source).get("models", []):
        if str(m.get("id", "")).lower() == wanted:
            return m
    return None


def source_for(adapter: str, base_url: str = "", settings: settings_mod.Settings | None = None) -> str | None:
    """这个角色配置该查哪份目录：OpenRouter 适配器 → openrouter；OpenAI 兼容适配器且地址是网关 → gateway；
    OpenAI 兼容对话把地址指到 openrouter.ai 也算 openrouter。都不是就 None（不做目录检查）。"""

    if adapter in ADAPTER_SOURCE:
        return ADAPTER_SOURCE[adapter]
    if adapter in settings_mod.GATEWAY_ADAPTERS.values():
        if "openrouter.ai" in str(base_url or ""):
            return "openrouter"
        current = settings or settings_mod.load_settings()
        if current.gateway.configured and str(base_url or "").rstrip("/") == current.gateway.base_url.rstrip("/"):
            return "gateway"
    return None


def dynamic_caps(role: str, adapter: str, model: str, *, base_url: str = "") -> dict[str, Any] | None:
    """能力表用：目录里这个模型的参数区间（只有专用接口来的记录才有）。没有就 None，能力表退回静态记录。"""

    source = source_for(adapter, base_url)
    if not source:
        return None
    entry = find(source, model)
    if not entry or role not in entry.get("roles", ()) or not entry.get("caps"):
        return None
    return {**entry["caps"], "source": "catalog"}


def catalog_issues(role: str, adapter: str, model: str, *, uses_refs: bool = False, base_url: str = "") -> list[dict[str, Any]]:
    """能力检查用：模型 id 不在目录里 / 模态不对 / 要参考图但模型不吃图。目录没拉过就什么都不说。
    网关目录的角色是按 id 猜的，只在 id 不存在时提醒，不拿猜的模态拦人。"""

    source = source_for(adapter, base_url)
    if not source:
        return []
    part = section(source)
    if not part:
        return []
    label = SOURCES[source]["label"]
    fetched = time.strftime("%Y-%m-%d", time.localtime(part.get("fetched_at") or 0))
    entry = find(source, model)
    if entry is None:
        return [{"level": "warn", "role": role, "message": f"{label} 目录（{fetched} 拉取）里没有模型 {model}——id 写错或已下线，提交会被拒绝"}]
    if entry.get("guessed"):
        return []
    role_word = {"image": "出图", "video": "出视频", "voice": "出语音", "text": "出文本"}.get(role, role)
    if role not in entry.get("roles", ()):
        return [{"level": "warn", "role": role, "message": f"{label} 目录里 {model} 不{role_word}（输出模态：{' / '.join(entry.get('output') or ['?'])}）"}]
    caps = entry.get("caps") or {}
    accepts_image = caps.get("reference_images") if "reference_images" in caps else ("image" in (entry.get("input") or ()))
    if uses_refs and accepts_image is False:
        what = "首帧" if role == "video" else "角色锚图和参考镜"
        return [{"level": "warn", "role": role, "message": f"{label} 目录里 {model} 不接受图片输入，{what}会被忽略"}]
    return []


__all__ = ["SOURCES", "ADAPTER_SOURCE", "catalog_path", "load_catalog", "save_catalog", "classify", "classify_image",
           "classify_video", "classify_by_id", "merge_models", "fetch_openrouter", "fetch_openai_models", "refresh",
           "section", "models_for", "summary", "find", "source_for", "dynamic_caps", "catalog_issues"]
