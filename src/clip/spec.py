"""单条视频项目文件：归一化 / 读 / 写 / 编号 / 归档（契约 docs/clip-spec.md §1）。

跟绘本的 ``picturebook.spec``、图片集的 ``imageset.spec`` 一个思路：一处边界把形状校验完，后面的模块只认
归一化后的字典。归一化后多两个运行时键 ``output_root``（``output/clip/<ID>``）与 ``project_root``，
写盘时去掉（``to_disk``）。

项目文件同时也是产物清单：``attempts`` 记每次出片（从不覆盖），``current`` 指向采用的那一条，
文件在 ``01_video/<ID>-<n>.mp4``；首帧（上传或生成）在 ``00_frame/`` 下，``first_frame.asset``
是项目内的相对路径。

**归一化只管形状**：节拍按 ``start`` 排序、``end > start``、id 不重复；时间轴的空洞 / 重叠 /
与总时长对不上是**告警**（``lint.py``），不是错误——写到一半的项目也要能打开。
"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from genkit import trash
from genkit.ids import ID_RE, next_episode_id
from genkit.paths import data_root as _data_root
from storyvideo.spec import SpecError, _resolve_local_asset

MODULE = "clip"
CLIPS_DIR = Path("content") / "clip"
OUTPUT_DIR = Path("output") / "clip"
ID_PREFIX = "CL"

PROMPT_LANGUAGES = ("zh", "en")
# 视频提示词方言（契约 §2）：只有两种够用，未知模型走 generic；空串 = 按模型猜
DIALECT_CHOICES = ("auto", "generic", "nano_banana")
ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9")
FIRST_FRAME_MODES = ("none", "upload", "generate")

DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_DURATION = 10
DEFAULT_RESOLUTION = "1080p"
DEFAULT_VIDEO_PARAMS = {"model": "@video", "duration": DEFAULT_DURATION, "resolution": DEFAULT_RESOLUTION, "generate_audio": False}
DEFAULT_IMAGE_PARAMS = {"model": "@image", "image_size": "2K"}
DURATION_MIN, DURATION_MAX = 1, 300

_BEAT_ID_RE = re.compile(r"B\d{2,3}\Z")
_GLOBAL_KEYS = ("look", "palette", "light", "aspect_ratio", "safe_area", "text_policy", "audio")


class ClipSpecError(SpecError):
    """单条视频项目文件缺字段或形状不对。"""


# ---------------------------------------------------------------------------- 路径


def clips_dir(project_root: Path) -> Path:
    return Path(project_root) / CLIPS_DIR


def clip_path(project_root: Path, clip_id: str) -> Path:
    return clips_dir(project_root) / f"{clip_id}.json"


def existing_ids(project_root: Path) -> list[str]:
    root = clips_dir(project_root)
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.json") if p.is_file())


def next_clip_id(existing: Iterable[str], *, today: date | None = None) -> str:
    """CL-20260910-1、CL-20260910-2…（按天编号）。"""

    return next_episode_id(existing, prefix=ID_PREFIX, today=today)


def output_root(spec: Mapping[str, Any]) -> Path:
    return Path(str(spec["output_root"]))


def video_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "01_video"


def frame_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "00_frame"


def resolve_asset(spec: Mapping[str, Any], value: Any) -> Path | None:
    """项目相对的素材路径 → 数据目录 content/ 或 output/ 里存在的绝对路径；不存在 / 空 / 越界 → None。"""

    try:
        resolved = _resolve_local_asset(value, Path(str(spec.get("project_root") or _data_root())))
    except SpecError:
        return None   # 越界的写法（~、绝对路径、盘符、..、指向外面的链接）当没有这个文件：不读数据目录外的东西
    return Path(resolved) if resolved else None


def current_path(spec: Mapping[str, Any]) -> Path | None:
    """采用的那一条片子（``current`` 指针指向的文件）；没有或文件不在 → None。"""

    current = spec.get("current")
    if not current:
        return None
    path = video_dir(spec) / str(current)
    return path if path.is_file() else None


def first_frame_path(spec: Mapping[str, Any]) -> Path | None:
    """首帧图（上传或生成的那一张）的绝对路径；``mode == none`` 或文件不在 → None。"""

    first = spec.get("first_frame") or {}
    if str(first.get("mode") or "none") == "none":
        return None
    return resolve_asset(spec, first.get("asset"))


# ---------------------------------------------------------------------------- 归一化


def _text(value: Any, field: str, *, required: bool = False, default: str = "") -> str:
    if value is None:
        if required:
            raise ClipSpecError(f"{field} 不能为空")
        return default
    if not isinstance(value, str):
        raise ClipSpecError(f"{field} 要是文本")
    out = value.strip()
    if required and not out:
        raise ClipSpecError(f"{field} 不能为空")
    return out


def _number(value: Any, field: str, *, default: float, minimum: float, maximum: float) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ClipSpecError(f"{field} 要是数字")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ClipSpecError(f"{field} 要是数字") from exc
    if not minimum <= number <= maximum:
        raise ClipSpecError(f"{field} 要在 {minimum:g} 到 {maximum:g} 之间")
    return number


def _choice(value: Any, field: str, choices: tuple[str, ...], *, default: str | None = None) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            raise ClipSpecError(f"{field} 不能为空，只能是 {' / '.join(c for c in choices if c)}")
        return default
    text = str(value).strip()
    if text not in choices:
        raise ClipSpecError(f"{field} 只能是 {' / '.join(c for c in choices if c)}，收到「{text}」")
    return text


def _asset(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    text = _text(value, field)
    if not text:
        return None
    if "\\" in text or text.startswith("/") or (len(text) > 1 and text[1] == ":") or ".." in Path(text).parts:
        raise ClipSpecError(f"{field} 只能是项目内的相对路径（content/assets/… 或 output/clip/…）")
    return text.replace("\\", "/")


def _strings(raw: Any, field: str) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        raw = re.split(r"[、,，;；\n]", raw)
    if not isinstance(raw, list):
        raise ClipSpecError(f"{field} 要是数组（一行一条）")
    return [str(v).strip() for v in raw if str(v or "").strip()]


def _style(raw: Any) -> str:
    """``style``：风格卡 id / 名 / 别名（DESIGN §41）或 ``custom``；不填 = custom（用自写的 style_anchor）。"""

    from imagecraft.cards import find_card

    text = _text(raw, "style")
    if not text or text == "custom":
        return "custom"
    card = find_card(text)
    if card is None:
        raise ClipSpecError(f"没有这张风格卡：{text}（也可以写 custom 自写风格锚）")
    return str(card["id"])


def _global(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise ClipSpecError("global 要是对象（look / palette / light / aspect_ratio / safe_area / text_policy / audio）")
    unknown = sorted(set(str(k) for k in raw) - set(_GLOBAL_KEYS))
    if unknown:
        raise ClipSpecError(f"global 里不认识的字段：{'、'.join(unknown)}")
    return {
        "look": _text(raw.get("look"), "global.look"),
        "palette": _strings(raw.get("palette"), "global.palette"),
        "light": _text(raw.get("light"), "global.light"),
        "aspect_ratio": _choice(raw.get("aspect_ratio"), "global.aspect_ratio", ASPECT_RATIOS, default=DEFAULT_ASPECT_RATIO),
        "safe_area": _text(raw.get("safe_area"), "global.safe_area"),
        "text_policy": _text(raw.get("text_policy"), "global.text_policy"),
        "audio": _text(raw.get("audio"), "global.audio"),
    }


def _beats(raw: Any) -> list[dict[str, Any]]:
    """节拍表：校验形状 + 按 ``start`` 排序（空洞 / 重叠是 lint 的告警，不在这里拦）。"""

    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ClipSpecError("beats 要是数组")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, beat in enumerate(raw):
        if not isinstance(beat, Mapping):
            raise ClipSpecError(f"beats[{index}] 要是对象")
        beat_id = _text(beat.get("id"), f"beats[{index}].id") or f"B{index + 1:02d}"
        if not _BEAT_ID_RE.fullmatch(beat_id):
            raise ClipSpecError(f"beats[{index}].id 要写成 B01 这样的形状，收到「{beat_id}」")
        if beat_id in seen:
            raise ClipSpecError(f"节拍 id 重复：{beat_id}")
        seen.add(beat_id)
        start = _number(beat.get("start"), f"beat {beat_id}.start", default=0.0, minimum=0.0, maximum=float(DURATION_MAX))
        end = _number(beat.get("end"), f"beat {beat_id}.end", default=start, minimum=0.0, maximum=float(DURATION_MAX))
        if end <= start:
            # 收尾不带全角括号：以「（…）」结尾的句子会被 i18n 当成「人话（原文）」形状，只翻括号前半截
            raise ClipSpecError(f"beat {beat_id} 的 end 要大于 start：现在 start={start:g}、end={end:g}")
        out.append({
            "id": beat_id,
            "start": round(start, 3),
            "end": round(end, 3),
            "shot": _text(beat.get("shot"), f"beat {beat_id}.shot"),
            "picture": _text(beat.get("picture"), f"beat {beat_id}.picture"),
            "sound": _text(beat.get("sound"), f"beat {beat_id}.sound"),
            "text": _text(beat.get("text"), f"beat {beat_id}.text"),
        })
    out.sort(key=lambda b: (b["start"], b["end"], b["id"]))
    return out


def _continuity(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise ClipSpecError("continuity 要是对象（one_take / transitions / last_frame / forbid）")
    return {
        "one_take": bool(raw.get("one_take", True)),
        "transitions": _text(raw.get("transitions"), "continuity.transitions"),
        "last_frame": _text(raw.get("last_frame"), "continuity.last_frame"),
        "forbid": _strings(raw.get("forbid"), "continuity.forbid"),
    }


def _video_params(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise ClipSpecError("video_params 要是对象（model / duration / resolution / generate_audio）")
    model = _text(raw.get("model"), "video_params.model", default="@video") or "@video"
    duration = int(_number(raw.get("duration"), "video_params.duration", default=float(DEFAULT_DURATION),
                           minimum=float(DURATION_MIN), maximum=float(DURATION_MAX)))
    requested = _text(raw.get("resolution"), "video_params.resolution", default=DEFAULT_RESOLUTION) or DEFAULT_RESOLUTION
    return {
        "model": model,
        "duration": duration,
        # 分辨率按能力表自动对齐（720p → MiniMax 的 768P）；模型写 @video 时对齐留到提交前
        "resolution": align_resolution(model, requested),
        "generate_audio": bool(raw.get("generate_audio", False)),
    }


def _image_params(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise ClipSpecError("image_params 要是对象（model / image_size）")
    return {
        "model": _text(raw.get("model"), "image_params.model", default="@image") or "@image",
        "image_size": _text(raw.get("image_size"), "image_params.image_size", default="2K") or "2K",
    }


def _first_frame(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if isinstance(raw, str):
        raw = {"mode": raw}
    if not isinstance(raw, Mapping):
        raise ClipSpecError("first_frame 要是对象（mode / asset / prompt）")
    mode = _choice(raw.get("mode"), "first_frame.mode", FIRST_FRAME_MODES, default="none")
    return {
        "mode": mode,
        "asset": _asset(raw.get("asset"), "first_frame.asset"),
        "prompt": _text(raw.get("prompt"), "first_frame.prompt"),
        # 上一次出首帧的失败原因（出成功了就清掉）：写进文件，重开界面还看得到
        "error": _text(raw.get("error"), "first_frame.error") or None,
    }


def _attempt(raw: Any, field: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ClipSpecError(f"{field} 要是对象")
    return {
        "file": _text(raw.get("file"), f"{field}.file"),
        "created_at": raw.get("created_at"),
        "model": _text(raw.get("model"), f"{field}.model"),
        "request_hash": _text(raw.get("request_hash"), f"{field}.request_hash"),
        "task_id": raw.get("task_id"),
        "error": _text(raw.get("error"), f"{field}.error") or None,
    }


def _attempts(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ClipSpecError("attempts 要是数组")
    return [_attempt(a, f"attempts[{i}]") for i, a in enumerate(raw)]


def align_resolution(model: str, requested: str, *, adapter: str = "", base_url: str = "") -> str:
    """按模型能力表把分辨率对齐到它支持的档位；不知道来源时在静态表里按最长模型前缀找。

    ``@video`` 这类角色占位符没法查（用哪个模型由「模型设置」决定），原样返回——真正的对齐在适配器提交前
    由 ``genkit.capabilities.resolution_for`` 再做一次。
    """

    from genkit import capabilities as C

    wanted = str(requested or "").strip()
    name = str(model or "").strip()
    if not wanted or not name or name.startswith("@"):
        return wanted
    if adapter:
        return C.resolution_for("video", adapter, name, wanted, base_url=base_url)
    low = name.lower()
    best: tuple[int, Mapping[str, Any]] | None = None
    for (role, _adapter, prefix), caps in C.CAPABILITIES.items():
        if role != "video" or not prefix or not low.startswith(prefix.lower()):
            continue
        if best is None or len(prefix) > best[0]:
            best = (len(prefix), caps)
    if best is None:
        return wanted
    return C.snap_resolution(best[1].get("resolutions"), wanted) or wanted


def normalize(data: Mapping[str, Any], *, project_root: str | os.PathLike[str] | None = None,
              clip_id: str | None = None) -> dict[str, Any]:
    """校验 + 补默认值。``clip_id`` 给了就覆盖 / 补上 id（新建时用）。"""

    if not isinstance(data, Mapping):
        raise ClipSpecError("项目文件要是一个 JSON 对象")
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else _data_root()
    raw = copy.deepcopy(dict(data))
    identifier = _text(clip_id if clip_id is not None else raw.get("id"), "id")
    if not identifier:
        raise ClipSpecError("id 不能为空——新建时由服务端编号")
    if not ID_RE.fullmatch(identifier):
        raise ClipSpecError("id 只能用英文、数字、下划线、点、短横线")
    style_anchor = _text(raw.get("style_anchor"), "style_anchor")
    style = _style(raw.get("style"))
    section = _global(raw.get("global"))
    attempts = _attempts(raw.get("attempts"))
    current = _text(raw.get("current"), "current") or None
    if current and current not in {a["file"] for a in attempts}:
        raise ClipSpecError(f"current「{current}」不在 attempts 里——采用的必须是出过的那几条之一")
    normalized: dict[str, Any] = {
        "id": identifier,
        "module": MODULE,
        "title": _text(raw.get("title"), "title", required=True),
        "prompt_language": _choice(raw.get("prompt_language"), "prompt_language", PROMPT_LANGUAGES, default="zh"),
        "style": style,
        "style_anchor": style_anchor,
        "dialect": _choice(raw.get("dialect"), "dialect", DIALECT_CHOICES, default="auto"),
        "global": section,
        "beats": _beats(raw.get("beats")),
        "continuity": _continuity(raw.get("continuity")),
        "video_params": _video_params(raw.get("video_params")),
        "image_params": _image_params(raw.get("image_params")),
        "first_frame": _first_frame(raw.get("first_frame")),
        "attempts": attempts,
        "current": current,
    }
    if style == "custom" and not style_anchor and not section["look"]:
        raise ClipSpecError("style 选 custom 时 style_anchor 或 global.look 至少写一个（风格锚是提示词的第一句）")
    normalized["output_root"] = str((root / OUTPUT_DIR / identifier).resolve(strict=False))
    normalized["project_root"] = str(root)
    return normalized


def to_disk(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in spec.items() if k not in ("output_root", "project_root")}


def load(clip_id: str, *, project_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else _data_root()
    if not ID_RE.fullmatch(str(clip_id or "")):
        raise ClipSpecError(f"没有这条片子：{clip_id}")
    path = clip_path(root, clip_id)
    if not path.is_file():
        raise ClipSpecError(f"没有这条片子：{clip_id}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ClipSpecError(f"读不了项目文件 {path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ClipSpecError(f"项目文件不是合法的 JSON（{path} 第 {exc.lineno} 行）") from exc
    if isinstance(data, Mapping) and str(data.get("id") or "") not in ("", clip_id):
        raise ClipSpecError(f"项目文件里的 id「{data.get('id')}」跟文件名 {clip_id} 对不上")
    return normalize(data, project_root=root, clip_id=clip_id)


def save(spec: Mapping[str, Any]) -> Path:
    """归一化后的 spec 原子写盘（``to_disk`` 形状）；返回路径。"""

    from storyvideo.state import atomic_write_json

    target = clip_path(Path(str(spec["project_root"])), str(spec["id"]))
    atomic_write_json(target, to_disk(spec))
    return target


def archive(clip_id: str, *, project_root: Path) -> dict[str, Any]:
    """归档到 ``.webui_deleted/clip/<ID>__<时间>/``（项目文件 + 产物目录 + 上传的素材一起搬，附 manifest），不是 ``os.remove``。

    实现在 ``genkit.trash``：回收站页按它列出、恢复；有文件被占用时抛 ``trash.FilesBusy``，什么都不动。"""

    path = clip_path(project_root, clip_id)
    if not ID_RE.fullmatch(str(clip_id or "")) or not path.is_file():
        raise ClipSpecError(f"没有这条片子：{clip_id}")
    return trash.archive(Path(project_root), MODULE, clip_id)


# ---------------------------------------------------------------------------- 只读辅助


def beat_by_id(spec: Mapping[str, Any], beat_id: str) -> dict[str, Any]:
    for beat in spec.get("beats") or ():
        if str(beat.get("id")) == beat_id:
            return beat
    raise ClipSpecError(f"没有这个节拍：{beat_id}")


def beat_ids(spec: Mapping[str, Any]) -> list[str]:
    return [str(b["id"]) for b in spec.get("beats") or ()]


def covered_seconds(spec: Mapping[str, Any]) -> float:
    """节拍覆盖的总时长（首拍 start → 末拍 end）；没有节拍 = 0。"""

    beats = spec.get("beats") or []
    if not beats:
        return 0.0
    return round(float(beats[-1]["end"]) - float(beats[0]["start"]), 3)


def attempt_by_file(spec: Mapping[str, Any], file: str) -> dict[str, Any] | None:
    for attempt in spec.get("attempts") or ():
        if str(attempt.get("file")) == str(file):
            return attempt
    return None


__all__ = [
    "MODULE", "CLIPS_DIR", "OUTPUT_DIR", "ID_PREFIX", "PROMPT_LANGUAGES", "DIALECT_CHOICES", "ASPECT_RATIOS",
    "FIRST_FRAME_MODES", "DEFAULT_ASPECT_RATIO", "DEFAULT_DURATION", "DEFAULT_RESOLUTION", "DEFAULT_VIDEO_PARAMS",
    "DEFAULT_IMAGE_PARAMS", "DURATION_MIN", "DURATION_MAX", "ClipSpecError",
    "clips_dir", "clip_path", "existing_ids", "next_clip_id", "output_root", "video_dir", "frame_dir",
    "resolve_asset", "current_path", "first_frame_path", "align_resolution",
    "normalize", "to_disk", "load", "save", "archive", "beat_by_id", "beat_ids", "covered_seconds", "attempt_by_file",
]
