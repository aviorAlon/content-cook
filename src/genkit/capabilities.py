"""模型能力表：每个来源（适配器）× 模型前缀能接受什么参数，用来在开始制作前提示越界，
而不是提交后才被服务商 400 打回来（那时钱和时间都已经花了）。

来源分两档：``verified``（在真实生产里跑过的组合）与 ``doc``（按服务商文档写、没真实跑过）。
verified 的越界当错误拦，doc 的只提醒——文档和线上实际经常不一致，不该拿它硬拦人。
没有记录的模型标 ``known=False``，只提示"参数越界只会在提交时报错"。

表是数据不是逻辑：加一个模型加一行；适配器级别的通用能力用空模型前缀表示。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

# (role, adapter, model_prefix) → 能力。model_prefix 大小写不敏感，最长匹配优先；"" = 适配器通用。
CAPABILITIES: dict[tuple[str, str, str], dict[str, Any]] = {
    # ---- 生图
    ("image", "openrouter_images", ""): {
        "reference_images": True, "aspect_ratios": ["9:16", "16:9", "1:1", "3:4", "4:3"], "image_sizes": ["1K", "2K", "4K"], "source": "doc",
    },
    # OpenAI 兼容图片接口：适配器只发 1024 系尺寸（adapters/openai_compat._SIZE_BY_RATIO），gpt-image-1 / 1.5 官方也只收
    # 1024x1024 / 1536x1024 / 1024x1536——所以尺寸档只记 1K。之前写着 1K / 2K，spec 写 2K 能力检查一声不吭、
    # 实际拿到的是 1K（ADP-10）。gpt-image-2 更大的尺寸按文档能到 2560×1440，但没在真密钥下跑过，先不进默认路径；
    # 要更大的可以在请求的 extra_params.size 里明写。
    ("image", "openai_images", ""): {
        "reference_images": True, "aspect_ratios": ["9:16", "16:9", "1:1", "3:4", "4:3"], "image_sizes": ["1K"], "source": "doc",
    },
    ("image", "ark_images", ""): {
        "reference_images": True, "aspect_ratios": ["9:16", "16:9", "1:1", "3:4", "4:3"], "image_sizes": ["2K"], "source": "doc",
    },
    # 本机 SD WebUI / Forge：短边固定 1024（SDXL 原生，见 adapters/sdwebui.py 的尺寸规则），画幅任意——
    # 只要是 8 的倍数就出得来，这里列的是界面上会用到的几档。img2img 的 init_images 一次只重绘一张底图。
    ("image", "sdwebui", ""): {
        "reference_images": True, "max_reference_images": 1,
        "aspect_ratios": ["9:16", "16:9", "1:1", "3:4", "4:3", "2:3", "3:2", "21:9"],
        "image_sizes": ["512", "1K", "2K"], "local": True, "cost": "free", "source": "doc",
    },
    # 本机 ComfyUI：工作流千差万别，所以**只记画幅与尺寸区间**，不吹参考图与原生音频
    # （docs/local-models-spec.md §4.2）——能不能吃参考图取决于用户选的那份工作流有没有输入图节点。
    ("image", "comfyui", ""): {
        "aspect_ratios": ["9:16", "16:9", "1:1", "3:4", "4:3", "2:3", "3:2"],
        "image_sizes": ["512", "1K", "2K"], "local": True, "cost": "free", "source": "doc",
    },
    ("video", "comfyui", ""): {
        "aspect_ratios": ["9:16", "16:9", "1:1"], "resolutions": ["480p", "720p"],
        "durations": {"min": 2, "max": 10}, "local": True, "cost": "free", "source": "doc",
    },
    # ---- 生图：参考图上限（max_reference_images，lint.check_reference_roles 与能力检查用；DESIGN §30）。
    # 只记公开文档写明的数字，不确定的不写（Flux Kontext 官方 API 单张 input_image、Kontext max 另有实验性多图，
    # 加上 OpenRouter 的 id 写法没核实，先不记）。
    # OpenAI Images API 参考（POST /v1/images/edits，image 参数）：gpt-image 系列「You can provide up to 16 images」。
    ("image", "openai_images", "gpt-image"): {
        "reference_images": True, "max_reference_images": 16, "aspect_ratios": ["9:16", "16:9", "1:1", "3:4", "4:3"], "image_sizes": ["1K"], "source": "doc",
    },
    ("image", "openrouter_images", "openai/gpt-image"): {
        "reference_images": True, "max_reference_images": 16, "aspect_ratios": ["9:16", "16:9", "1:1", "3:4", "4:3"], "image_sizes": ["1K", "2K", "4K"], "source": "doc",
    },
    # 火山方舟 Seedream 4.0 图片生成 API（image 参数）：图生图最多输入 10 张图片。
    ("image", "ark_images", "doubao-seedream-4"): {
        "reference_images": True, "max_reference_images": 10, "aspect_ratios": ["9:16", "16:9", "1:1", "3:4", "4:3"], "image_sizes": ["2K"], "source": "doc",
    },
    # 火山方舟 Seedream 3.0 是纯文生图接口（doubao-seedream-3-0-t2i），不收图；SeedEdit 3.0（doubao-seededit-3-0-i2i）单图编辑。
    ("image", "ark_images", "doubao-seedream-3-0-t2i"): {
        "reference_images": False, "aspect_ratios": ["9:16", "16:9", "1:1", "3:4", "4:3"], "image_sizes": ["2K"], "source": "doc",
    },
    ("image", "ark_images", "doubao-seededit-3-0-i2i"): {
        "reference_images": True, "max_reference_images": 1, "aspect_ratios": ["9:16", "16:9", "1:1", "3:4", "4:3"], "image_sizes": ["2K"], "source": "doc",
    },
    # ---- 视频（时长区间与分辨率来自 spec.normalize 里的"生产验证过的组合"，以及各家文档）
    ("video", "ark_video", "doubao-seedance-2-0-fast"): {
        "durations": {"min": 4, "max": 15}, "resolutions": ["720p"], "aspect_ratios": ["9:16", "16:9", "1:1"], "first_frame": True, "audio": True, "source": "verified",
    },
    ("video", "ark_video", "doubao-seedance-2-0"): {
        "durations": {"min": 4, "max": 15}, "resolutions": ["1080p"], "aspect_ratios": ["9:16", "16:9", "1:1"], "first_frame": True, "audio": True, "source": "verified",
    },
    ("video", "ark_video", "doubao-seedance-2-5"): {
        "durations": {"min": 4, "max": 30}, "resolutions": ["720p"], "aspect_ratios": ["9:16", "16:9", "1:1"], "first_frame": True, "audio": True, "source": "doc",
    },
    ("video", "ark_video", ""): {"first_frame": True, "audio": True, "source": "doc"},
    ("video", "minimax_video", "MiniMax-H3"): {
        "durations": {"min": 4, "max": 15}, "resolutions": ["768P"], "aspect_ratios": ["adaptive"], "first_frame": True, "audio": True, "source": "verified",
    },
    ("video", "minimax_video", "MiniMax-Hailuo-02"): {
        "durations": {"choices": [6, 10]}, "resolutions": ["768P", "1080P"], "aspect_ratios": ["adaptive", "9:16", "16:9"], "first_frame": True, "audio": False, "source": "doc",
    },
    ("video", "minimax_video", ""): {"first_frame": True, "source": "doc"},
    ("video", "openrouter_video", "minimax/hailuo"): {
        "durations": {"choices": [6, 10]}, "resolutions": ["768p", "1080p"], "first_frame": True, "audio": True, "source": "doc",
    },
    ("video", "openrouter_video", "bytedance-seed/seedance"): {
        "durations": {"min": 4, "max": 15}, "resolutions": ["720p", "1080p"], "first_frame": True, "audio": True, "source": "doc",
    },
    ("video", "openrouter_video", "google/veo"): {
        "durations": {"choices": [4, 6, 8]}, "resolutions": ["720p", "1080p"], "aspect_ratios": ["9:16", "16:9"], "first_frame": True, "audio": True, "source": "doc",
    },
    ("video", "openrouter_video", ""): {"first_frame": True, "source": "doc"},
    ("video", "openai_video", ""): {"first_frame": True, "audio": True, "source": "doc"},
}


# 只会跑在本机的适配器（不看 base_url 也知道是本地）。别的来源靠 base_url 判断：把
# openai_chat / openai_speech 指到 127.0.0.1 就是本地文本 / 本地语音那条路（DESIGN §45）。
# 插件用 add_adapter(local=True) 往里加。
LOCAL_ADAPTERS: set[str] = {"sdwebui", "comfyui"}


def is_local(adapter: str, base_url: str = "") -> bool:
    """这条来源跑在本机吗——本机 = 不花钱，预估与 cost_note 改说「本地生成，不花钱」。"""

    if adapter in LOCAL_ADAPTERS:
        return True
    from .local import is_local_url

    return is_local_url(base_url)


def lookup(role: str, adapter: str, model: str, *, use_catalog: bool = True, base_url: str = "") -> dict[str, Any]:
    """先看模型目录（服务商此刻公布的参数区间，source=catalog），再按静态表最长模型前缀匹配；
    找不到就退到适配器通用条目；再没有就 known=False。

    本机来源（``is_local``）无论走哪条都会带上 ``local: True`` 与 ``cost: "free"``——本地语音
    （openai_speech 指到 127.0.0.1）没有自己的适配器 id，只能由地址推导。"""

    from . import plugins

    plugins.ensure_loaded()

    def _mark(caps: dict[str, Any]) -> dict[str, Any]:
        return {**caps, "local": True, "cost": "free"} if is_local(adapter, base_url) else caps

    if use_catalog:
        from . import model_catalog

        dynamic = model_catalog.dynamic_caps(role, adapter, model, base_url=base_url)
        if dynamic:
            return _mark({"known": True, "adapter": adapter, "model": model, **dynamic})
    model_l = (model or "").lower()
    best: tuple[int, dict[str, Any]] | None = None
    for (r, a, prefix), caps in CAPABILITIES.items():
        if r != role or a != adapter:
            continue
        if prefix and not model_l.startswith(prefix.lower()):
            continue
        score = len(prefix)
        if best is None or score > best[0]:
            best = (score, caps)
    if best is None:
        return _mark({"known": False, "adapter": adapter, "model": model, "source": None})
    # 有任何一项参数范围（画幅 / 尺寸档 / 时长 / 分辨率）就算有记录——适配器级的通用记录也算；
    # 只写了"收首帧/出音频"这种布尔项的不算，那不足以判断参数越不越界
    _RANGE_KEYS = ("durations", "resolutions", "aspect_ratios", "image_sizes", "counts")
    known = bool(best[0]) or any(k in best[1] for k in _RANGE_KEYS)
    return _mark({"known": known, "adapter": adapter, "model": model, **best[1]})


def max_reference_images(model: str, *, adapter: str = "", base_url: str = "") -> int | None:
    """出图模型一次最多收几张参考图；表里没写（或写的不是正整数）就 None。

    给了适配器走 ``lookup``（目录优先）；没给——只知道模型 id、不知道它配在哪个来源——就在静态表
    所有生图记录里按最长模型前缀找（适配器通用记录不算，那上面没有上限）。这个跨来源的猜法不能当成确定事实：
    lint 的参考图上限只按「模型设置」里的来源查，没来源就不报（ADP-09，见 storyvideo.lint._max_reference_images）。"""

    model_l = (model or "").strip().lower()
    if not model_l or model_l.startswith("@"):
        return None
    from . import plugins

    plugins.ensure_loaded()
    if adapter:
        caps = lookup("image", adapter, model, base_url=base_url)
    else:
        best: tuple[int, Mapping[str, Any]] | None = None
        for (role, _adapter, prefix), entry in CAPABILITIES.items():
            if role != "image" or not prefix or not model_l.startswith(prefix.lower()):
                continue
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), entry)
        caps = best[1] if best else {}
    value = caps.get("max_reference_images")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _duration_ok(caps: Mapping[str, Any], seconds: float) -> bool:
    spec = caps.get("durations")
    if not spec:
        return True
    if "choices" in spec:
        return int(seconds) in spec["choices"]
    return float(spec.get("min", 0)) <= seconds <= float(spec.get("max", 10**9))


def _duration_text(caps: Mapping[str, Any]) -> str:
    spec = caps.get("durations") or {}
    if "choices" in spec:
        return " / ".join(str(x) for x in spec["choices"]) + " 秒"
    return f"{spec.get('min')}–{spec.get('max')} 秒"


def _pixels(value: str) -> int | None:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return None
    n = int(digits)
    # 1K / 2K / 4K 这类档位按短边像素折算
    if str(value).strip().upper().endswith("K") and n <= 8:
        return {1: 1080, 2: 1440, 4: 2160}.get(n, n * 1000)
    return n


def snap_resolution(supported: Iterable[str] | None, requested: str | None) -> str | None:
    """把 spec 里的分辨率换成模型支持的最接近档位（720p → MiniMax 的 768P）。
    已经支持（不分大小写）就原样返回支持列表里的写法；没有列表或解析不了返回 None。"""

    options = [str(x) for x in (supported or ()) if str(x).strip()]
    wanted = str(requested or "").strip()
    if not options or not wanted:
        return None
    for opt in options:
        if opt.lower() == wanted.lower():
            return opt
    target = _pixels(wanted)
    if target is None:
        return None
    scored = [(abs((_pixels(opt) or 0) - target), i, opt) for i, opt in enumerate(options) if _pixels(opt) is not None]
    if not scored:
        return None
    return min(scored)[2]


def resolution_for(role: str, adapter: str, model: str, requested: str | None, *, base_url: str = "") -> str:
    """适配器提交前调用：按能力表把分辨率对齐到模型支持的档位；没有记录就原样。"""

    caps = lookup(role, adapter, model, base_url=base_url)
    snapped = snap_resolution(caps.get("resolutions"), requested)
    return snapped or str(requested or "")


def effective_model(spec_model: str | None, role_model: str) -> str:
    """spec 里写 ``@image`` / ``@video`` 时用模型设置里的模型；写了具体 id 以 spec 为准。"""

    value = str(spec_model or "").strip()
    if value and not value.startswith("@"):
        return value
    return role_model


def check_spec(spec: Mapping[str, Any], settings: Any) -> dict[str, Any]:
    """按当前模型设置检查一份 spec：返回图/视频两侧的能力记录与问题列表。

    问题分级：``error``（verified 记录下的越界，开始制作前应当改）、``warn``（doc 记录或不影响成败的
    参数）、``info``（没有能力记录）。不抛异常，调用方决定拦不拦。"""

    from . import model_catalog
    from . import settings as settings_mod

    issues: list[dict[str, Any]] = []
    out: dict[str, Any] = {"issues": issues}

    for role, params_key in (("image", "image_params"), ("video", "video_params")):
        cfg = settings.role(role)
        params = spec.get(params_key) or {}
        if not cfg.adapter:
            issues.append({"level": "warn", "role": role, "message": f"{'生图' if role == 'image' else '视频'}模型还没配置——去「模型设置」"})
            out[role] = {"known": False, "adapter": "", "model": ""}
            continue
        model = effective_model(params.get("model"), settings_mod.effective(cfg, role).model)
        caps = lookup(role, cfg.adapter, model, base_url=cfg.base_url)
        out[role] = caps
        if not caps.get("known"):
            issues.append({"level": "info", "role": role,
                           "message": f"{'生图' if role == 'image' else '视频'}已绑定 {model or '（未填模型）'}，但没有它的参数范围记录（画幅 / 尺寸 / 时长），越界只会在提交时被服务商报错——不影响开始制作"})
            issues.extend(model_catalog.catalog_issues(role, cfg.adapter, model, base_url=cfg.base_url))
            continue
        level = "error" if caps.get("source") == "verified" else "warn"
        label = f"{model}" + {"verified": "", "catalog": "（按服务商目录）"}.get(str(caps.get("source")), "（按文档，未实测）")
        if role == "video":
            bad = [str(s.get("id")) for s in spec.get("shots", ()) if isinstance(s, dict) and s.get("dur") is not None and not _duration_ok(caps, float(s["dur"]))]
            if bad:
                issues.append({"level": level, "role": role, "shots": bad,
                               "message": f"{label} 只接受 {_duration_text(caps)} 的镜次时长，这些镜越界：{', '.join(bad)}"})
            resolution = str(params.get("resolution") or "")
            if resolution and caps.get("resolutions") and resolution.lower() not in [r.lower() for r in caps["resolutions"]]:
                snapped = snap_resolution(caps["resolutions"], resolution)
                if snapped:
                    issues.append({"level": "info", "role": role,
                                   "message": f"{label} 支持的分辨率是 {' / '.join(caps['resolutions'])}，spec 写的是 {resolution}，提交时会自动换成 {snapped}"})
                else:
                    issues.append({"level": level, "role": role,
                                   "message": f"{label} 支持的分辨率是 {' / '.join(caps['resolutions'])}，spec 写的是 {resolution}"})
            ratio = str(params.get("aspect_ratio") or "")
            if ratio and caps.get("aspect_ratios") and ratio not in caps["aspect_ratios"]:
                issues.append({"level": level, "role": role,
                               "message": f"{label} 支持的画幅是 {' / '.join(caps['aspect_ratios'])}，spec 写的是 {ratio}"})
            if caps.get("audio") is False and any(
                isinstance(s, dict) and s.get("lines") for s in spec.get("shots", ())
            ):
                issues.append({"level": "warn", "role": role,
                               "message": f"{label} 不生成原生对白，有台词的镜要靠「台词配音」（旁白与插卡页）"})
            if caps.get("first_frame") is False:
                issues.append({"level": "warn", "role": role, "message": f"{label} 不接受首帧图，只能文生视频——首帧会被忽略，画面与定妆图对不上"})
            issues.extend(model_catalog.catalog_issues(role, cfg.adapter, model, uses_refs=True, base_url=cfg.base_url))
        else:
            ratio = str(params.get("aspect_ratio") or "")
            if ratio and caps.get("aspect_ratios") and ratio not in caps["aspect_ratios"]:
                issues.append({"level": level, "role": role,
                               "message": f"{label} 支持的画幅是 {' / '.join(caps['aspect_ratios'])}，spec 写的是 {ratio}"})
            size = str(params.get("image_size") or "")
            if size and caps.get("image_sizes") and size.upper() not in [x.upper() for x in caps["image_sizes"]]:
                issues.append({"level": level, "role": role,
                               "message": f"{label} 支持的尺寸档是 {' / '.join(caps['image_sizes'])}，spec 写的是 {size}"})
            uses_refs = any(isinstance(m, dict) and m.get("asset") for m in (spec.get("cast") or {}).values()) or any(
                isinstance(s, dict) and s.get("ref_frame") for s in spec.get("shots", ()))
            if uses_refs and caps.get("reference_images") is False:
                issues.append({"level": "warn", "role": role, "message": f"{label} 不接受参考图，角色锚图和参考镜会被忽略"})
            issues.extend(model_catalog.catalog_issues(role, cfg.adapter, model, uses_refs=uses_refs, base_url=cfg.base_url))
    out["errors"] = sum(1 for i in issues if i["level"] == "error")
    out["warnings"] = sum(1 for i in issues if i["level"] == "warn")
    return out


__all__ = ["CAPABILITIES", "lookup", "max_reference_images", "check_spec", "effective_model", "snap_resolution", "resolution_for"]
