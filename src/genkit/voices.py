"""音色库：跨项目复用的声音档案，绑定给角色（画内台词）或旁白。

一条音色档案 = 两样东西：
* ``description``——一段声音描述文字（性别、年龄、音色、语速、音量）。视频模型直出台词时
  只能接受文字，绑定给角色后这段话进视频提示词；同一段描述让各镜的声音"像同一个人"。
* ``engine`` + ``voice`` + ``rate`` + ``pitch``——TTS 参数。绑定给旁白后，旁白轨用它合成，
  这条路的音色是真正稳定的。``engine`` 是 ``edge``（免费的 edge-tts 音色）或 ``settings``
  （「模型设置」里配的语音模型，voice 填该模型的音色 id）。

绑定是"拷贝"，不是"引用"：绑定时把描述/TTS 参数写进项目 spec（并记下 voice_ref），之后
改音色库不会悄悄改已冻结项目的提示词——账本的冻结承诺不能被一个全局文件绕过。要同步
就在项目里重新绑一次（角色描述属于生成字段，开始制作后要开新版本）。

存放：``~/.storystudio/voices.json``；试听样本 ``~/.storystudio/voices/<id>-<参数哈希>-<时间戳>.mp3``（永不覆盖，旧的尽力清理）。

音色克隆（DESIGN §33，2026-09-08）：档案可带 ``reference_audio``（本地 wav/mp3/m4a，相对路径先按音色目录、再按当前目录解析）
和 ``clone = {provider, voice_id, status, cloned_at, error}``；``clone_voice`` 把参考音频交给支持克隆的来源（现在只有 MiniMax），
拿回的 ``voice_id`` 写进档案，``engine == "minimax"`` 时合成用它。没有这两个字段的老档案一个字节不变。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping

from . import settings as settings_mod

ENGINES = ("edge", "settings", "minimax")
CLONE_STATUSES = ("none", "cloning", "ready", "failed")
ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}\Z")
DEFAULT_SAMPLE_TEXT = "你好，我是这个角色的声音。今天的天气不错，我们开始吧。"
# OpenAI 语音接口的固定音色表；OpenAI 兼容网关通常照抄这份
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse")


class VoiceError(ValueError):
    pass


# ---------------------------------------------------------------------------- 结构化音色提示词
# 一个音色定了，它的提示词就定了：按固定维度拼出中英文两版，绑给角色时按项目的提示词语言取一版。
# 维度齐全比措辞漂亮重要——视频模型每镜重新"演"，约束的维度越全，各镜之间的差异越小。

TRAIT_FIELDS = ("gender", "age", "timbre", "pace", "volume", "accent", "mood")
TRAIT_OPTIONS: dict[str, dict[str, tuple[str, str]]] = {
    "gender": {"female": ("女声", "female voice"), "male": ("男声", "male voice"), "neutral": ("中性声", "gender-neutral voice")},
    "age": {
        "child": ("六到十岁孩子的", "a child of six to ten"), "teen": ("十几岁少年的", "a teenager's"),
        "young": ("二十多岁的", "in their twenties"), "adult": ("三十多岁的", "in their thirties"),
        "middle": ("四五十岁的", "in their forties or fifties"), "senior": ("六十岁以上的", "over sixty"),
    },
    "pace": {"slow": ("语速慢", "slow pace"), "medium": ("语速中等", "medium pace"), "fast": ("语速偏快", "brisk pace")},
    "volume": {"soft": ("音量偏小但清楚", "soft but clear"), "normal": ("音量正常", "normal volume"), "loud": ("音量偏大", "loud")},
}
# 常见音色/性格关键词的英文；不在表里的原样保留
TIMBRE_EN = {
    "清亮": "clear and bright", "沙哑": "husky", "略带沙哑": "slightly husky", "低沉": "deep", "浑厚": "rich and full",
    "温暖": "warm", "温柔": "gentle", "亲切": "friendly", "明快": "upbeat", "活泼": "lively", "甜": "sweet",
    "开朗": "cheerful", "平静": "calm", "沉稳": "composed", "严肃": "serious", "自信": "confident",
    "有激情": "passionate", "理性": "rational", "幽默": "humorous", "阳光": "sunny", "冷静": "cool-headed",
    "慵懒": "lazy and relaxed", "干脆": "crisp", "柔和": "soft", "尖": "high-pitched", "磁性": "resonant",
    "带电子感": "slightly synthetic", "电子感": "synthetic", "少年感": "youthful", "成熟": "mature",
}
ACCENT_EN = {"标准普通话": "standard Mandarin", "普通话": "Mandarin", "台湾腔": "Taiwanese Mandarin", "粤语": "Cantonese",
             "美式英语": "American English", "英式英语": "British English", "东北话": "Northeastern Mandarin accent",
             "四川话": "Sichuanese accent", "轻微口音": "a light regional accent"}
# edge-tts 目录里的 VoicePersonalities → 中文关键词
PERSONALITY_ZH = {
    "Warm": "温暖", "Friendly": "亲切", "Positive": "明快", "Lively": "活泼", "Cute": "甜", "Cheerful": "开朗",
    "Bright": "清亮", "Calm": "平静", "Gentle": "温柔", "Professional": "沉稳", "Serious": "严肃", "Confident": "自信",
    "Passion": "有激情", "Passionate": "有激情", "Rational": "理性", "Humorous": "幽默", "Sunshine": "阳光",
    "Deep": "低沉", "Mature": "成熟", "Youthful": "少年感", "Casual": "随和", "Soft": "柔和", "Clear": "清亮",
    "Sincere": "真诚", "Comfort": "舒缓", "Authority": "权威", "Pleasant": "悦耳", "Considerate": "体贴",
}
LOCALE_ACCENT = {"zh-CN": "标准普通话", "zh-TW": "台湾腔", "zh-HK": "粤语", "en-US": "美式英语", "en-GB": "英式英语"}


def normalize_traits(traits: Any) -> dict[str, Any]:
    if not isinstance(traits, dict):
        raise VoiceError("traits 必须是对象")
    out: dict[str, Any] = {}
    for key in ("gender", "age", "pace", "volume"):
        value = str(traits.get(key) or "").strip()
        if value and value not in TRAIT_OPTIONS[key]:
            raise VoiceError(f"traits.{key} 只能是 {' / '.join(TRAIT_OPTIONS[key])}")
        out[key] = value
    timbre = traits.get("timbre") or []
    if isinstance(timbre, str):
        timbre = [t for t in re.split(r"[,，、;；/\s]+", timbre) if t]
    if not isinstance(timbre, list):
        raise VoiceError("traits.timbre 必须是数组或用逗号分隔的字符串")
    out["timbre"] = [str(t).strip() for t in timbre if str(t).strip()][:8]
    out["accent"] = str(traits.get("accent") or "").strip()
    out["mood"] = str(traits.get("mood") or "").strip()
    return out


def compile_description(traits: dict[str, Any], lang: str = "zh") -> str:
    """把维度拼成一段进视频提示词的声音描述。空维度跳过；结尾固定加"口齿清楚"（台词清晰度基线）。"""

    t = normalize_traits(traits)
    zh = lang != "en"
    idx = 0 if zh else 1
    parts: list[str] = []
    gender = TRAIT_OPTIONS["gender"].get(t["gender"], ("", ""))[idx]
    age = TRAIT_OPTIONS["age"].get(t["age"], ("", ""))[idx]
    if zh:
        head = f"{age}{gender}" if gender else (f"{age}声音" if age else "")
        if head:
            parts.append(head)
        if t["timbre"]:
            parts.append("、".join(t["timbre"]))
        for key in ("pace", "volume"):
            if t[key]:
                parts.append(TRAIT_OPTIONS[key][t[key]][0])
        if t["accent"]:
            parts.append(t["accent"])
        if t["mood"]:
            parts.append(f"情绪基调{t['mood']}")
        parts.append("口齿清楚")
        return "，".join(parts)
    head = ""
    if gender and age:
        head = f"a {gender} {age}"
    elif gender:
        head = f"a {gender}"
    elif age:
        head = f"a voice of {age}" if t["age"] in ("child", "teen") else f"a voice {age}"
    if head:
        parts.append(head)
    if t["timbre"]:
        parts.append(", ".join(TIMBRE_EN.get(x, x) for x in t["timbre"]) + " timbre")
    for key in ("pace", "volume"):
        if t[key]:
            parts.append(TRAIT_OPTIONS[key][t[key]][1])
    if t["accent"]:
        parts.append(ACCENT_EN.get(t["accent"], t["accent"]))
    if t["mood"]:
        parts.append(f"{TIMBRE_EN.get(t['mood'], t['mood'])} baseline")
    text = ", ".join(parts)
    text = text[0].upper() + text[1:] if text else ""
    return f"{text}; every word clearly articulated" if text else "Every word clearly articulated"


def traits_from_catalog(entry: dict[str, Any]) -> dict[str, Any]:
    """从目录条目（性别、语言、性格标签）反推一版维度初稿；年龄留空让人填。"""

    gender = {"Female": "female", "Male": "male"}.get(str(entry.get("gender") or ""), "")
    personalities = [PERSONALITY_ZH.get(p, "") for p in (entry.get("personalities") or [])]
    timbre = [p for p in personalities if p][:4]
    return normalize_traits({
        "gender": gender, "age": "", "timbre": timbre, "pace": "medium", "volume": "normal",
        "accent": LOCALE_ACCENT.get(str(entry.get("locale") or ""), ""), "mood": "",
    })


def voices_path() -> Path:
    return settings_mod.home_dir() / "voices.json"


def samples_dir() -> Path:
    path = settings_mod.home_dir() / "voices"
    path.mkdir(parents=True, exist_ok=True)
    return path


def reference_dir() -> Path:
    """参考音频目录：放在样本目录的子目录里，样本清理的 glob（``<id>-*.mp3``）碰不到它。"""

    path = samples_dir() / "reference"
    path.mkdir(parents=True, exist_ok=True)
    return path


def reference_audio_path(profile: Mapping[str, Any] | None) -> Path | None:
    """档案里的 ``reference_audio`` → 绝对路径：绝对路径原样；相对路径先按音色目录、再按当前目录解析。找不到也返回解析结果（调用方判断）。"""

    raw = str((profile or {}).get("reference_audio") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    for base in (samples_dir(), Path.cwd()):
        candidate = (base / path).resolve()
        if candidate.is_file():
            return candidate
    return (samples_dir() / path).resolve()


def _normalize_reference_audio(raw: Any) -> str:
    from .adapters.minimax_speech import MAX_REFERENCE_BYTES, REFERENCE_SUFFIXES

    text = str(raw or "").strip()
    if not text:
        return ""
    if Path(text).suffix.lower() not in REFERENCE_SUFFIXES:
        raise VoiceError(f"参考音频只接受 {' / '.join(s.lstrip('.') for s in REFERENCE_SUFFIXES)}，收到的是 {Path(text).suffix or '无后缀'}")
    path = reference_audio_path({"reference_audio": text})
    if path is None or not path.is_file():
        raise VoiceError(f"参考音频文件不存在：{text}")
    if path.stat().st_size > MAX_REFERENCE_BYTES:
        raise VoiceError(f"参考音频超过 {MAX_REFERENCE_BYTES // 1024 // 1024}MB：{path.name}")
    return text


def _empty_clone() -> dict[str, Any]:
    return {"provider": "", "voice_id": "", "status": "none", "cloned_at": None, "error": ""}


# 试听样本文件名：<id>-<参数哈希>-<时间戳>.mp3。
# Windows 上浏览器的 <audio> 还握着旧样本时，改参数触发的 unlink 抛 WinError 32，整个保存 500。
# 所以永远不覆盖、不强删：参数变了就是另一个文件名，旧文件尽力清理、清不掉下次再清。
def _sample_key(item: Mapping[str, Any]) -> str:
    import hashlib

    raw = "|".join(str(item.get(k) or "") for k in ("engine", "voice", "rate", "pitch"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def current_sample(voice_id: str, item: Mapping[str, Any] | None = None) -> Path | None:
    """当前参数下最新的一个样本文件；没有就 None。"""

    if item is None:
        item = _load().get(voice_id)
        if item is None:
            return None
    key = _sample_key(item)
    candidates = sorted(samples_dir().glob(f"{voice_id}-{key}-*.mp3"))
    return candidates[-1] if candidates else None


def _new_sample_path(voice_id: str, item: Mapping[str, Any]) -> Path:
    return samples_dir() / f"{voice_id}-{_sample_key(item)}-{int(time.time() * 1000)}.mp3"


def _sweep_samples(voice_id: str, keep: Path | None = None) -> None:
    """删掉这个音色除 keep 之外的样本；被占用的跳过（下次再删），绝不让保存/删除因为这个失败。"""

    for path in samples_dir().glob(f"{voice_id}-*.mp3"):
        if keep is not None and path == keep:
            continue
        try:
            path.unlink()
        except OSError:
            continue


def _load() -> dict[str, dict[str, Any]]:
    path = voices_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = data.get("voices") if isinstance(data, dict) else None
    return {str(k): dict(v) for k, v in (items or {}).items() if isinstance(v, dict)}


def _save(items: dict[str, dict[str, Any]]) -> None:
    path = voices_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"schema_version": 1, "voices": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _normalize(profile: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(existing or {})
    for key in ("name", "description", "voice", "rate", "pitch", "sample_text"):
        if key in profile and profile[key] is not None:
            out[key] = str(profile[key]).strip()
    if "engine" in profile and profile["engine"] is not None:
        engine = str(profile["engine"]).strip()
        if engine not in ENGINES:
            raise VoiceError(f"engine 只能是 {' / '.join(ENGINES)}")
        out["engine"] = engine
    if "tags" in profile and profile["tags"] is not None:
        tags = profile["tags"]
        if not isinstance(tags, list):
            raise VoiceError("tags 必须是数组")
        out["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    if "traits" in profile and profile["traits"] is not None:
        out["traits"] = normalize_traits(profile["traits"])
    if "description_mode" in profile and profile["description_mode"] is not None:
        mode = str(profile["description_mode"]).strip()
        if mode not in ("traits", "custom"):
            raise VoiceError("description_mode 只能是 traits / custom")
        out["description_mode"] = mode
    # 参考音频（DESIGN §33）：换了文件或清空 → 克隆状态归零（旧的克隆 id 对应的是旧音频）。clone 本身只由 clone_voice 写。
    if "reference_audio" in profile and profile["reference_audio"] is not None:
        reference = _normalize_reference_audio(profile["reference_audio"])
        if reference != str(out.get("reference_audio") or ""):
            out["clone"] = _empty_clone()
        if reference:
            out["reference_audio"] = reference
        else:
            out.pop("reference_audio", None)
            out.pop("clone", None)
    out.setdefault("description_mode", "traits" if out.get("traits") else "custom")
    # 维度模式：描述由维度拼出（中英两版）；手写模式：description 原样，英文版留空（绑定时退回中文）
    if out.get("description_mode") == "traits" and out.get("traits"):
        out["description"] = compile_description(out["traits"], "zh")
        out["description_en"] = compile_description(out["traits"], "en")
    elif out.get("description_mode") == "custom":
        out.pop("description_en", None)
    out.setdefault("engine", "edge")
    out.setdefault("tags", [])
    out.setdefault("traits", {})
    out.setdefault("description", "")
    out.setdefault("voice", "")
    out.setdefault("rate", "")
    out.setdefault("pitch", "")
    if not out.get("name"):
        raise VoiceError("name 不能为空")
    if not out.get("voice") and not out.get("description") and not out.get("reference_audio"):
        raise VoiceError("至少要有一个 TTS 音色（voice）或一段声音描述（description）")
    return out


def _view(voice_id: str, item: Mapping[str, Any]) -> dict[str, Any]:
    row = {"id": voice_id, **item}
    row["has_sample"] = current_sample(voice_id, item) is not None
    if item.get("reference_audio"):
        path = reference_audio_path(item)
        row["has_reference_audio"] = bool(path and path.is_file())
        row["clone"] = {**_empty_clone(), **(item.get("clone") or {})}
    return row


def list_profiles() -> list[dict[str, Any]]:
    items = _load()
    out = [_view(vid, item) for vid, item in items.items()]
    out.sort(key=lambda r: r.get("name", ""))
    return out


def get_profile(voice_id: str) -> dict[str, Any] | None:
    item = _load().get(voice_id)
    if item is None:
        return None
    return _view(voice_id, item)


def create_profile(voice_id: str | None, profile: dict[str, Any]) -> dict[str, Any]:
    """不给 id 就按名字生成（纯中文名退到 voice1、voice2…）；给了 id 才校验它。"""

    from .ids import id_from_name

    items = _load()
    voice_id = str(voice_id or "").strip()
    if not voice_id:
        voice_id = id_from_name(str(profile.get("name") or ""), items.keys(), prefix="voice")
    if not ID_RE.fullmatch(voice_id):
        raise VoiceError("id 只能用英文、数字、下划线、点、短横线")
    if voice_id in items:
        raise VoiceError(f"已经有一个音色叫「{voice_id}」")
    item = _normalize(profile)
    item["updated_at"] = time.time()
    items[voice_id] = item
    _save(items)
    return get_profile(voice_id)  # type: ignore[return-value]


def update_profile(voice_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    items = _load()
    if voice_id not in items:
        raise VoiceError(f"没有这个音色：{voice_id}")
    item = _normalize(profile, existing=items[voice_id])
    item["updated_at"] = time.time()
    items[voice_id] = item
    _save(items)
    # TTS 参数变了就是另一个样本文件名（旧样本自然作废）；旧文件尽力清理
    _sweep_samples(voice_id, keep=current_sample(voice_id, item))
    return get_profile(voice_id)  # type: ignore[return-value]


def delete_profile(voice_id: str) -> None:
    items = _load()
    if voice_id not in items:
        raise VoiceError(f"没有这个音色：{voice_id}")
    del items[voice_id]
    _save(items)
    _sweep_samples(voice_id)
    sweep_reference_audio(voice_id)


def sweep_reference_audio(voice_id: str, keep: Path | None = None) -> None:
    """删掉这个音色除 keep 之外的参考音频（只动参考目录里按命名规则放的文件）；被占用的跳过。"""

    for path in reference_dir().glob(f"{voice_id}-*"):
        if keep is not None and path.resolve() == keep.resolve():
            continue
        try:
            path.unlink()
        except OSError:
            continue


# ---------------------------------------------------------------------------- 音色克隆（DESIGN §33）


def minimax_speech_config(settings: settings_mod.Settings | None = None) -> dict[str, Any] | None:
    """能用来克隆/合成的 MiniMax 配置：语音角色选了 minimax_speech 优先；否则借视频角色 minimax_video 的密钥
    （同一把 key 两个接口都能用）。都没有 → None。"""

    current = settings or settings_mod.load_settings_or_default()[0]     # 音色目录 / 引擎列表要在设置文件坏了时也能开
    voice_role = current.role("voice")
    if voice_role.adapter == "minimax_speech" and voice_role.api_key:
        return settings_mod.effective(voice_role, "voice").as_dict()
    video_role = current.role("video")
    if video_role.adapter == "minimax_video" and video_role.api_key:
        cfg = settings_mod.effective(video_role, "video").as_dict()
        defaults = settings_mod.ADAPTERS["voice"]["minimax_speech"]["defaults"]
        return {**cfg, "adapter": "minimax_speech", "model": defaults["model"], "voice": defaults["voice"]}
    return None


def build_minimax_speech(settings: settings_mod.Settings | None = None) -> Any:
    from .adapters.minimax_speech import build

    cfg = minimax_speech_config(settings)
    if cfg is None:
        raise VoiceError("没有配置 MiniMax 密钥——在「模型设置」把语音来源选 MiniMax 语音接口（或视频来源选 MiniMax）并填 key")
    return build(cfg, cache_dir=settings_mod.cache_dir(settings))


def _write_clone(voice_id: str, clone: dict[str, Any], **fields: Any) -> None:
    items = _load()
    if voice_id not in items:
        return
    items[voice_id]["clone"] = {**_empty_clone(), **clone}
    items[voice_id].update(fields)
    items[voice_id]["updated_at"] = time.time()
    _save(items)


def clone_voice(profile: Mapping[str, Any], settings: settings_mod.Settings | None = None, *, adapter: Any = None) -> dict[str, Any]:
    """把档案的参考音频交给 MiniMax 克隆；成功后 ``clone`` 记 ``ready`` + 服务商音色 id，``engine`` 切到 minimax、``voice`` 填克隆 id。
    失败把原因记进 ``clone.error``（状态 failed）再抛出，界面轮档案就能看到。"""

    from .adapters.minimax_speech import clone_voice_id, reference_digest

    voice_id = str(profile.get("id") or "")
    if not voice_id or voice_id not in _load():
        raise VoiceError(f"没有这个音色：{voice_id}")
    reference = reference_audio_path(profile)
    if reference is None or not reference.is_file():
        raise VoiceError("这个音色还没有参考音频——先上传一段 10 秒到 5 分钟的清晰人声")
    if adapter is None:
        adapter = build_minimax_speech(settings)
    remote_id = clone_voice_id(voice_id, reference_digest(reference))
    _write_clone(voice_id, {"provider": "minimax", "voice_id": remote_id, "status": "cloning", "error": ""})
    preview = str(profile.get("sample_text") or DEFAULT_SAMPLE_TEXT)
    try:
        result = adapter.clone_voice(reference, voice_id=remote_id, preview_text=preview)
    except Exception as exc:  # noqa: BLE001 - 原因要落到档案里，界面靠它显示「失败：…」
        _write_clone(voice_id, {"provider": "minimax", "voice_id": remote_id, "status": "failed", "error": str(exc)[:500]})
        raise
    clone = {"provider": "minimax", "voice_id": str(result.get("voice_id") or remote_id), "status": "ready",
             "cloned_at": time.time(), "error": "", "demo_audio": str(result.get("demo_audio") or "")}
    _write_clone(voice_id, clone, engine="minimax", voice=clone["voice_id"])
    return clone


def record_clone_error(voice_id: str, message: str) -> None:
    """界面层把失败原因翻成人话后覆盖档案里的 ``clone.error``（状态 failed），状态药丸直接显示它。"""

    items = _load()
    if voice_id not in items:
        return
    clone = {**_empty_clone(), **(items[voice_id].get("clone") or {})}
    _write_clone(voice_id, {**clone, "status": "failed", "error": str(message)[:500]})


def cloned_voice_id(profile: Mapping[str, Any]) -> str:
    clone = profile.get("clone") or {}
    if clone.get("status") == "ready" and clone.get("voice_id"):
        return str(clone["voice_id"])
    return ""


# ---------------------------------------------------------------------------- 可选音色目录


def edge_catalog(locale_prefixes: tuple[str, ...] = ("zh-", "en-")) -> list[dict[str, Any]]:
    """edge-tts 在线音色表（需要联网）；按语言前缀过滤，默认中英文。"""

    import asyncio

    import edge_tts

    async def _list() -> list[dict[str, Any]]:
        return await edge_tts.list_voices()

    try:
        voices = asyncio.run(_list())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            voices = loop.run_until_complete(_list())
        finally:
            loop.close()
    out = []
    for v in voices:
        locale = str(v.get("Locale") or "")
        if locale_prefixes and not locale.startswith(locale_prefixes):
            continue
        tag = v.get("VoiceTag") or {}
        out.append({
            "voice": v.get("ShortName"),
            "gender": v.get("Gender"),
            "locale": locale,
            "label": v.get("FriendlyName") or v.get("ShortName"),
            "personalities": list(tag.get("VoicePersonalities") or []),
            "categories": list(tag.get("ContentCategories") or []),
        })
    out.sort(key=lambda r: (r["locale"], r["gender"] or "", r["voice"] or ""))
    return out


def settings_catalog() -> dict[str, Any]:
    """「模型设置」里语音角色能用的音色：edge → 在线表；OpenAI 兼容 → 固定表；其他 → 手填。"""

    current, _ = settings_mod.load_settings_or_default()      # 设置文件坏了按「没配」给表，不 500
    role = current.role("voice")
    if not role.adapter:
        return {"adapter": "", "voices": [], "note": "还没在「模型设置」里配语音模型"}
    if role.adapter == "edge_tts":
        return {"adapter": role.adapter, "voices": edge_catalog(), "note": ""}
    if role.adapter == "openai_speech":
        return {"adapter": role.adapter, "voices": [{"voice": v, "gender": "", "locale": "", "label": v, "personalities": [], "categories": []} for v in OPENAI_VOICES],
                "note": "OpenAI 兼容网关通常支持这几个音色；不支持的会在试听时报错"}
    if role.adapter == "minimax_speech":
        return {"adapter": role.adapter, "voices": _minimax_voice_rows(), "note": "MiniMax 系统音色（按文档列的一部分）；克隆出来的 id 在档案的「参考音频」里"}
    return {"adapter": role.adapter, "voices": [], "note": "这个来源没有可枚举的音色表，按服务商文档手填音色 id"}


def _minimax_voice_rows() -> list[dict[str, Any]]:
    from .adapters.minimax_speech import SYSTEM_VOICES

    return [{"voice": vid, "gender": gender, "locale": "zh-CN", "label": label, "personalities": [], "categories": []}
            for vid, gender, label in SYSTEM_VOICES]


def available_engines(settings: settings_mod.Settings | None = None) -> list[str]:
    """档案能选的引擎：edge / settings 永远有；minimax 只在配了 MiniMax 密钥时出现。"""

    engines = ["edge", "settings"]
    if minimax_speech_config(settings) is not None:
        engines.append("minimax")
    return engines


def minimax_catalog(settings: settings_mod.Settings | None = None) -> dict[str, Any]:
    """``engine=minimax`` 的目录：没配密钥时给空表和说明（不是错误）。"""

    if minimax_speech_config(settings) is None:
        return {"adapter": "minimax_speech", "voices": [], "note": "没有配置 MiniMax 密钥——在「模型设置」把语音来源选 MiniMax 语音接口（或视频来源选 MiniMax）并填 key"}
    return {"adapter": "minimax_speech", "voices": _minimax_voice_rows(), "note": "MiniMax 系统音色（按文档列的一部分，未真实核对）；上传参考音频并克隆后，档案自动用克隆出来的 id"}


# ---------------------------------------------------------------------------- 试听


def synthesize_sample(voice_id: str, text: str | None = None) -> Path:
    profile = get_profile(voice_id)
    if profile is None:
        raise VoiceError(f"没有这个音色：{voice_id}")
    if not profile.get("voice"):
        raise VoiceError("这个音色只有文字描述，没有 TTS 音色，无法试听——它只用于视频模型直出台词的提示词")
    sample_text = (text or profile.get("sample_text") or DEFAULT_SAMPLE_TEXT).strip()
    destination = _new_sample_path(voice_id, profile)
    synthesize_with_profile(profile, sample_text, destination)
    items = _load()
    if voice_id in items and text:
        items[voice_id]["sample_text"] = sample_text
        _save(items)
    _sweep_samples(voice_id, keep=destination)
    return destination


def synthesize_with_profile(profile: dict[str, Any], text: str, destination: Path) -> Path:
    """按档案的引擎合成一段文字。旁白轨（post_layers）也走这里，保证试听与成片一致。"""

    from .adapters.speech import EdgeSpeech
    from .router import build_provider

    engine = profile.get("engine") or "edge"
    voice = str(profile.get("voice") or "")
    if engine == "settings":
        return build_provider().speech().synthesize(text, destination, voice=voice or None)
    if engine == "minimax":
        # 克隆成功的 id 优先（档案的 voice 一般也已经是它；手改过 voice 就以克隆为准，克隆是这条档案的意义）
        chosen = cloned_voice_id(profile) or voice
        if not chosen:
            raise VoiceError("这个音色还没有克隆成功，也没填 MiniMax 音色 id")
        return build_minimax_speech().synthesize(text, destination, voice=chosen, rate=profile.get("rate") or None)
    return EdgeSpeech({"model": voice or "zh-CN-YunxiNeural"}).synthesize(
        text, destination, voice=voice or None, rate=profile.get("rate") or None, pitch=profile.get("pitch") or None,
    )


# ---------------------------------------------------------------------------- 绑定（拷贝语义）


def description_for(profile: dict[str, Any], lang: str = "zh") -> str:
    """按项目的提示词语言取一版描述；英文版不存在（手写模式）时退回中文。"""

    if lang == "en" and profile.get("description_en"):
        return str(profile["description_en"])
    return str(profile.get("description") or "")


def bind_cast(member: dict[str, Any], profile: dict[str, Any] | None, *, lang: str = "zh") -> dict[str, Any]:
    """绑定给角色：描述进 ``voice``（视频提示词用），TTS 参数进 ``voice_tts``（台词 TTS 兜底用）。"""

    out = dict(member)
    if profile is None:
        out.pop("voice_ref", None)
        out.pop("voice_tts", None)
        return out
    out["voice_ref"] = profile["id"]
    text = description_for(profile, lang)
    if text:
        out["voice"] = text
    tts = {k: profile[k] for k in ("engine", "voice", "rate", "pitch") if profile.get(k)}
    if tts.get("voice"):
        out["voice_tts"] = tts
    else:
        out.pop("voice_tts", None)
    return out


def bind_voice_over(voice_over: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    """绑定给旁白：写 ``engine`` 与 ``edge`` 块（voice / rate / pitch），render_vo_lines 直接用。"""

    out = dict(voice_over)
    if profile is None:
        out.pop("voice_ref", None)
        out.pop("engine", None)
        return out
    if not profile.get("voice"):
        raise VoiceError("这个音色没有 TTS 音色，不能用于旁白")
    out["voice_ref"] = profile["id"]
    out["engine"] = profile.get("engine") or "edge"
    out["edge"] = {k: profile[k] for k in ("voice", "rate", "pitch") if profile.get(k)}
    return out


__all__ = [
    "ENGINES", "CLONE_STATUSES", "VoiceError", "DEFAULT_SAMPLE_TEXT", "TRAIT_FIELDS", "TRAIT_OPTIONS",
    "normalize_traits", "compile_description", "traits_from_catalog", "description_for",
    "voices_path", "samples_dir", "reference_dir", "reference_audio_path", "sweep_reference_audio",
    "list_profiles", "get_profile", "create_profile", "update_profile", "delete_profile",
    "edge_catalog", "settings_catalog", "minimax_catalog", "available_engines", "synthesize_sample", "synthesize_with_profile",
    "minimax_speech_config", "build_minimax_speech", "clone_voice", "cloned_voice_id",
    "bind_cast", "bind_voice_over",
]
