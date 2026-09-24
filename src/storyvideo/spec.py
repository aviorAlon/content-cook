"""Loading and validation for StoryVideo episode specifications.

The production scripts historically opened episode JSON files directly.  This
module provides one boundary where paths and the small amount of schema shared
by every stage can be validated before any paid work is submitted.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from . import overlays
from . import contract as _contract


class SpecError(ValueError):
    """Raised when an episode specification is missing or invalid."""


from genkit.paths import data_root as _data_root

_PROJECT_ROOT = _data_root()
_SHOT_TEXT_FIELDS = ("shot", "art", "motion")
_SUBTITLE_LANGUAGES = frozenset({"bilingual", "zh", "en"})
_RUN_ID_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z")
_SHOT_ID_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?\Z")


def stable_json_hash(value: Any) -> str:
    """Return a deterministic SHA-256 hash for a JSON-compatible value.

    The serialization deliberately has no platform-dependent path handling or
    incidental whitespace.  Callers can therefore use this for resumable task
    request keys.
    """

    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpecError(f"value is not canonical JSON: {exc}") from exc
    return hashlib.sha256(payload).hexdigest()


def _resolved(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _infer_project_root(spec_path: Path) -> Path:
    """Infer a useful root for an explicitly supplied absolute spec path."""

    for parent in (spec_path.parent, *spec_path.parents):
        if (parent / "pyproject.toml").is_file() or (parent / ".git").exists():
            return parent.resolve(strict=False)

    # Preserve the conventional ``<root>/content/story_video/EPxx.json``
    # layout even in small temporary projects without repository markers.
    parent = spec_path.parent
    if parent.name == "story_video" and parent.parent.name == "content":
        return parent.parent.parent.resolve(strict=False)
    return parent.resolve(strict=False)


def _inside(path: Path, boundary: Path, field: str) -> Path:
    try:
        path.relative_to(boundary)
    except ValueError as exc:
        raise SpecError(
            f"{field} resolves outside project boundary: {path} (root: {boundary})"
        ) from exc
    return path


def _resolve_within(
    value: str | os.PathLike[str],
    *,
    base: Path,
    boundary: Path,
    field: str,
) -> Path:
    raw = Path(value).expanduser()
    candidate = raw if raw.is_absolute() else base / raw
    return _inside(candidate.resolve(strict=False), boundary, field)


def resolve_spec_path(
    source: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str] | None = None,
) -> tuple[Path, Path]:
    """Resolve *source* and return ``(spec_path, project_root)``.

    A bare project id such as ``DEMO-1`` resolves to
    ``content/story_video/DEMO-1.json``.  A path is explicit and resolves from
    ``project_root``.  An absolute explicit path may infer its project root
    when no root was supplied.
    """

    if not isinstance(source, (str, os.PathLike)):
        raise TypeError("source must be a path or episode name")
    if not str(source).strip():
        raise SpecError("source must not be empty")

    raw = Path(source).expanduser()
    explicit_absolute = raw.is_absolute()
    if project_root is not None:
        root = _resolved(project_root)
    elif explicit_absolute:
        root = _infer_project_root(raw.resolve(strict=False))
    else:
        root = _PROJECT_ROOT

    # A bare identifier is the common CLI form.  A bare .json filename is
    # treated as a root-relative explicit path if it exists, otherwise as an
    # episode filename in the conventional directory.
    is_bare = raw.parent == Path(".")
    if is_bare and raw.suffix == "":
        candidate = root / "content" / "story_video" / f"{raw.name}.json"
    elif is_bare and raw.suffix.lower() == ".json" and not (root / raw).is_file():
        candidate = root / "content" / "story_video" / raw.name
    else:
        candidate = raw if explicit_absolute else root / raw

    candidate = _inside(candidate.resolve(strict=False), root, "spec path")
    if not candidate.is_file():
        raise SpecError(f"episode spec not found: {candidate}")
    return candidate, root


# 角色变体（2026-09-08，融合 drama-skills「身份 vs 版本」判据）：shot.cast 里的一项可以写成
# ``"lin@rain"``——成员 id + "@" + cast.lin.variants 的键。身份（脸/体态，block）只写一份，变体只写
# 相对基底的差异（换装/受伤/湿污/伪装）；表情、姿势这类一镜的事交给镜头文本。所有按 id 找成员的
# 代码一律先经过这个函数，"@"后面的东西不会漏进声音/台词/连续性查找。
def split_cast_ref(value: Any) -> tuple[str, str | None]:
    """``"lin@rain"`` → ``("lin", "rain")``；``"lin"`` → ``("lin", None)``。空的变体键当没写。"""

    text = str(value or "")
    if "@" not in text:
        return text, None
    member_id, _, variant_key = text.partition("@")
    return member_id, (variant_key or None)


_TAIL_MODES = ("reference", "direct")
REFERENCE_TARGET_FIXED = ("prev_frame", "tail", "prop")
# 关系场景类型（剧本工艺清单的关系场景判据）：表白 / 重逢 / 原谅 / 拒绝 / 分手。定义在这里而不是 lint.py，
# 因为 lint 已经从 spec 导入，反过来导入会绕圈。
SCENE_TYPES = ("confession", "reunion", "forgiveness", "rejection", "breakup")
# 不用落在人物表里的说话人：旁白 / 画外音。screenplay 层的说话人校验（_speaker_ok）用的是同一份，
# 起草的 JSON schema 也是这么写的（scriptgen「旁白用 narrator」）——两层认的名字必须一样，所以定在这里，
# screenplay 从这里导入（它已经从 spec 导入 SpecError，反过来导入会绕圈）。
NARRATOR_SPEAKERS = ("narrator", "旁白", "画外音", "vo", "v.o.", "o.s.")
# 说话方式：空 = 画内说话（提示词要求嘴型对上台词），narration / offscreen_narration = 画外。
# prompts.py 与 post_layers / preview 都只认这两个值，别的字符串等同「画内说话」——不认识的值在这里就拒，
# 免得手改 / 接口写进 "bogus" 之后，配音叠上去穿帮要等出片才看得出来。
SPEECH_MODES = ("narration", "offscreen_narration")


def _check_reference_target(
    target: str, shot: Mapping[str, Any], cast: Mapping[str, Any], *, has_prop: bool, label: str
) -> None:
    """参考槽位用途的 target 必须落在本镜真的会带的参考图上（DESIGN §30）。"""

    tail = shot.get("ref_tail")
    has_tail = isinstance(tail, Mapping) and str(tail.get("mode") or "") == "reference"
    has_ref_frame = bool(str(shot.get("ref_frame") or "").strip())
    if target == "prev_frame":
        if not (has_ref_frame or has_tail):
            raise SpecError(f"{label}.target prev_frame needs ref_frame or ref_tail on this shot")
        return
    if target == "tail":
        if not has_tail:
            raise SpecError(f"{label}.target tail needs ref_tail in reference mode on this shot")
        return
    if target == "prop":
        if not has_prop:
            raise SpecError(f"{label}.target prop needs prop_ref on the episode")
        return
    if target.startswith("cast:"):
        ref = target[len("cast:"):].strip()
        member_id, variant_key = split_cast_ref(ref)
        listed = [str(c) for c in (shot.get("cast") or ())]
        matched = ref in listed or (variant_key is None and any(split_cast_ref(c)[0] == member_id for c in listed))
        if not ref or not matched:
            raise SpecError(f"{label}.target references a cast entry not in this shot: {ref}")
        member = cast.get(member_id) if isinstance(cast, Mapping) else None
        member = member if isinstance(member, Mapping) else {}
        asset = ""
        if variant_key:
            variant = (member.get("variants") or {}).get(variant_key)
            asset = str((variant or {}).get("asset") or "") if isinstance(variant, Mapping) else ""
        if not asset:
            asset = str(member.get("asset") or "")
        if not asset.strip():
            raise SpecError(f"{label}.target {ref} has no anchor image, so it occupies no reference slot")
        return
    raise SpecError(f"{label}.target must be prev_frame, tail, prop, or cast:<id>[@variant]; got {target}")


def project_file(value: Any, root: Path, *, under: Sequence[str] = ("content",), field: str) -> Path | None:
    """项目文件里记的本地文件路径 → 数据目录 ``under`` 子目录里**存在**的那个文件（绝对路径）；文件不在 → None。

    只收数据目录内的相对路径（``"."`` 表示整个数据目录）：``local:`` 令牌、``~``、绝对路径、盘符（含 ``C:x.png`` 这种
    盘符相对写法——Windows 上它不算绝对路径）、UNC、``..`` 一律 SpecError，而且只看字面、不碰文件系统（UNC 不去探）。
    这些值写进项目文件（手改、模型起草、从别的机器拷来）之后，出图 / 出片时会被读出来当参考图、参考音频随请求上传，
    后期合成会把它读进成片（2026-09-23 发布前自查 PB-3 / PB-4）。文件存在时 resolve 之后再核一次边界，挡住指向外面的
    符号链接 / 目录联接。不存在的相对串原样当「没有这个文件」——历史上的远端 asset id（``asset-su``）就是这个形状。"""

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("\\", "/")
    windows = PureWindowsPath(text)
    if (text.startswith(("local:", "~", "/")) or windows.drive or windows.root or Path(text).is_absolute()
            or ".." in PurePosixPath(text).parts):
        raise SpecError(f"{field} 只能是数据目录里的相对路径（如 content/assets/…），收到「{value}」——"
                        "把文件放进数据目录再引用，或删掉这一栏")
    candidate = (root / text).resolve(strict=False)
    if not candidate.is_file():
        return None
    bounds = [(root / sub).resolve(strict=False) for sub in under]
    if not any(candidate.is_relative_to(bound) for bound in bounds):
        raise SpecError(f"{field} 指向数据目录之外（或不在允许的子目录里），收到「{value}」——把文件放进数据目录再引用，或删掉这一栏")
    return candidate


def _resolve_local_asset(asset: Any, root: Path, *, under: Sequence[str] = ("content", "output"),
                         field: str = "asset") -> str | None:
    """相对项目根的素材路径 → 绝对路径（文件存在、而且在数据目录 ``under`` 里时）；空值、远端 id、文件不在返回 None
    （调用方原样保留）。越界的写法（``local:``、``~``、绝对路径、盘符、``..``、指向外面的链接）抛 SpecError，
    见 :func:`project_file`。"""

    resolved = project_file(asset, root, under=under, field=field)
    return str(resolved) if resolved is not None else None


_UNSAFE_KEY_CHARS = frozenset('/\\?#%@')


def _check_member_key(key: str, label: str) -> None:
    """角色 / 变体 id 只拦不安全的写法：/ \\ ? # % @、控制字符、恰好是 . 或 ..。

    这些 id 会拼进锚图文件名（content/assets/<项目>/<id>-<摘要>.png，Windows 上 \\ 是路径分隔符，「..\\..\\x」就写到了
    数据目录外）和接口路径（/cast/<id>/asset，? # % 会被当成查询串 / 片段 / 转义）；@ 是「成员@变体」的分隔符（PB-6）。
    不按 CAST_ID_RE 那种英文 slug 收紧：每次打开项目都要过这里，「林小满」这类中文 id 的老项目会直接打不开。"""

    text = key.strip()
    if text in (".", "..") or any(ch in _UNSAFE_KEY_CHARS or ord(ch) < 32 or ord(ch) == 127 for ch in key):
        raise SpecError(f"{label} 的 id 不能含 / \\ ? # % @ 或控制字符，也不能是 . 或 ..——改个名字")


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{field} must be a non-empty string")
    return value


def _check_speaker(who: str, label: str, cast: Mapping[str, Any]) -> None:
    """台词的说话人必须落在人物表里（id、``id@变体``、角色名），或者是旁白一类的画外说话人。

    不核对的话，写错的 id 会一路走到付费的视频提示词里：``_auto_dialogue_block`` 拿 ``cast.get(who)`` 兜底成空字典，
    把 who 的原文当说话人名写进提示词（「ghost用普通话说：…」）、声线那一半也空着；配音计划那边则报「未绑定音色」。
    screenplay 层（``_speaker_ok``）早就是这么拦的，两层的口径要一致。"""

    text = who.strip()
    if text.lower() in NARRATOR_SPEAKERS:
        return
    member_id, _variant = split_cast_ref(text)
    if member_id in cast:
        return
    for member in cast.values():
        if isinstance(member, Mapping) and str(member.get("name") or "").strip() == text:
            return
    raise SpecError(f"{label} is not in cast: {text}")


def _normalize_lines(
    shot: dict[str, Any],
    shot_id: str,
    *,
    duration_seconds: float,
    subtitle_language: str,
    cast: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Normalize modern ``lines`` and the legacy speaker/en/zh schema."""

    required_languages = (
        ("en", "zh") if subtitle_language == "bilingual" else (subtitle_language,)
    )

    if "lines" in shot:
        raw_lines = shot["lines"]
        if not isinstance(raw_lines, list):
            raise SpecError(f"shot {shot_id}.lines must be a list")
        lines: list[dict[str, Any]] = []
        for index, raw_line in enumerate(raw_lines):
            label = f"shot {shot_id}.lines[{index}]"
            if not isinstance(raw_line, Mapping):
                raise SpecError(f"{label} must be an object")
            line = copy.deepcopy(dict(raw_line))
            for language in required_languages:
                _nonempty_text(line.get(language), f"{label}.{language}")
            for language in ("en", "zh"):
                if language not in required_languages and language in line:
                    _nonempty_text(line[language], f"{label}.{language}")
            if "who" in line:
                _check_speaker(_nonempty_text(line["who"], f"{label}.who"), f"{label}.who", cast)
            lines.append(line)

        timed = [
            "subtitle_start_seconds" in line or "subtitle_end_seconds" in line
            for line in lines
        ]
        if any(timed) and not all(timed):
            raise SpecError(
                f"shot {shot_id}.lines must either all declare subtitle timing "
                "or all omit it"
            )
        previous_end = 0.0
        for index, line in enumerate(lines):
            if not timed[index]:
                continue
            label = f"shot {shot_id}.lines[{index}]"
            if (
                "subtitle_start_seconds" not in line
                or "subtitle_end_seconds" not in line
            ):
                raise SpecError(
                    f"{label} must declare subtitle_start_seconds and "
                    "subtitle_end_seconds together"
                )
            values: dict[str, float] = {}
            for field in ("subtitle_start_seconds", "subtitle_end_seconds"):
                raw_value = line[field]
                if (
                    isinstance(raw_value, bool)
                    or not isinstance(raw_value, (int, float))
                    or not math.isfinite(float(raw_value))
                ):
                    raise SpecError(f"{label}.{field} must be a finite number")
                values[field] = float(raw_value)
            start = values["subtitle_start_seconds"]
            end = values["subtitle_end_seconds"]
            if start < 0 or end > duration_seconds or start >= end:
                raise SpecError(
                    f"{label} subtitle timing must satisfy "
                    "0 <= subtitle_start_seconds < subtitle_end_seconds "
                    f"<= {duration_seconds:g}"
                )
            if index and start < previous_end:
                raise SpecError(
                    f"{label} subtitle timing overlaps or precedes the previous line"
                )
            line["subtitle_start_seconds"] = start
            line["subtitle_end_seconds"] = end
            previous_end = end
        return lines

    en = shot.get("en", "")
    zh = shot.get("zh", "")
    speaker = shot.get("speaker")
    has_dialogue = bool(
        (isinstance(en, str) and en.strip())
        or (isinstance(zh, str) and zh.strip())
        or speaker is not None
    )
    if not has_dialogue:
        # A legacy silent shot represents that with empty en/zh values.
        return []

    values = {"en": en, "zh": zh}
    for language in required_languages:
        _nonempty_text(values[language], f"shot {shot_id}.{language}")
    line: dict[str, Any] = {}
    for language in ("en", "zh"):
        value = values[language]
        if language in required_languages or (isinstance(value, str) and value.strip()):
            line[language] = _nonempty_text(value, f"shot {shot_id}.{language}")
    if speaker is not None:
        # 老写法的 speaker 不核对人物表：它认 "both" 这种「两个人一起说」的写法（见 test_legacy_both_speaker…），
        # 是老项目打得开的前提。接口写进来的是上面的 lines[].who，那一条才拦
        line["who"] = _nonempty_text(speaker, f"shot {shot_id}.speaker")
    return [line]


def _cycle_path(
    ids: Sequence[str], dependencies: Mapping[str, str | None]
) -> list[str]:
    """Return one dependency cycle, including its repeated first node."""

    for start in ids:
        positions: dict[str, int] = {}
        chain: list[str] = []
        current: str | None = start
        while current is not None:
            if current in positions:
                return chain[positions[current] :] + [current]
            positions[current] = len(chain)
            chain.append(current)
            current = dependencies[current]
    return []


def topological_waves(shots: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    """Return stable dependency waves for shot ``ref_frame`` relationships."""

    ids: list[str] = []
    dependencies: dict[str, str | None] = {}
    for index, shot in enumerate(shots):
        shot_id = shot.get("id")
        if not isinstance(shot_id, str) or not shot_id.strip():
            raise SpecError(f"shots[{index}].id must be a non-empty string")
        if shot_id in dependencies:
            raise SpecError(f"duplicate shot id: {shot_id}")
        ids.append(shot_id)
        ref = shot.get("ref_frame")
        if ref is not None and (not isinstance(ref, str) or not ref.strip()):
            raise SpecError(f"shot {shot_id}.ref_frame must be a non-empty shot id")
        dependencies[shot_id] = ref

    known = set(ids)
    for shot_id, ref in dependencies.items():
        if ref is not None and ref not in known:
            raise SpecError(f"shot {shot_id}.ref_frame references unknown shot: {ref}")

    remaining = set(ids)
    waves: list[list[str]] = []
    while remaining:
        ready = [
            shot_id
            for shot_id in ids
            if shot_id in remaining and dependencies[shot_id] not in remaining
        ]
        if not ready:
            cycle = _cycle_path(ids, dependencies)
            detail = " -> ".join(cycle) if cycle else ", ".join(sorted(remaining))
            raise SpecError(f"ref_frame cycle detected: {detail}")
        waves.append(ready)
        remaining.difference_update(ready)
    return waves


def normalize_spec(
    data: Mapping[str, Any],
    *,
    spec_path: str | os.PathLike[str] | None = None,
    project_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Validate and normalize a decoded episode specification.

    Derived absolute paths and dependency waves are added to the returned copy;
    ``source_hash`` values are computed from the untouched JSON fields first so
    those machine-specific derived values cannot perturb resumable request keys.
    """

    if not isinstance(data, Mapping):
        raise SpecError("episode spec must be a JSON object")

    original = copy.deepcopy(dict(data))

    if project_root is not None:
        root = _resolved(project_root)
    elif spec_path is not None and Path(spec_path).expanduser().is_absolute():
        root = _infer_project_root(_resolved(spec_path))
    else:
        root = _PROJECT_ROOT

    normalized = copy.deepcopy(original)
    episode_id = _nonempty_text(normalized.get("id"), "id")
    if not _RUN_ID_RE.fullmatch(episode_id):
        raise SpecError(
            "id must be a 1-128 character slug with alphanumeric first and last characters"
        )
    if spec_path is None:
        resolved_spec_path: Path | None = None
        spec_dir = (root / "content" / "story_video").resolve(strict=False)
    else:
        raw_spec_path = Path(spec_path).expanduser()
        resolved_spec_path = _resolve_within(
            raw_spec_path,
            base=root,
            boundary=root,
            field="spec path",
        )
        spec_dir = resolved_spec_path.parent

    if "output_root" not in normalized:
        raise SpecError("missing required top-level field: output_root")
    output_value = normalized["output_root"]
    if not isinstance(output_value, (str, os.PathLike)) or not str(output_value).strip():
        raise SpecError("output_root must be a non-empty path")
    output_root = _resolve_within(
        output_value,
        base=root,
        boundary=root,
        field="output_root",
    )
    if output_root.name != episode_id:
        raise SpecError("output_root must end with the exact episode id")

    if "workspace_id" in normalized:
        workspace_id = normalized.get("workspace_id")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise SpecError("workspace_id must be a non-empty string when provided")
        normalized["workspace_id"] = workspace_id.strip()

    # 形态卡（DESIGN §31）：form 是卡的 id / 名 / 别名，这里统一成 id；没写的老项目什么都不做
    if "form" in normalized and normalized["form"] is not None:
        from .forms import find_form

        form_value = normalized["form"]
        if not isinstance(form_value, str) or not form_value.strip():
            raise SpecError("form must be a non-empty string when provided")
        form_card = find_form(form_value)
        if form_card is None:
            raise SpecError(f"form references unknown form card: {form_value}")
        normalized["form"] = str(form_card["id"])

    # 题材卡（DESIGN §49）：genre 是卡的 id / 名 / 别名，这里统一成 id；只给「按题材审稿」和题材 × 镜头检查用，不进提示词；
    # 没写的老项目什么都不做
    if "genre" in normalized and normalized["genre"] is not None:
        from .scriptgen import find_genre

        genre_value = normalized["genre"]
        if not isinstance(genre_value, str) or not genre_value.strip():
            raise SpecError("genre must be a non-empty string when provided")
        genre_card = find_genre(genre_value)
        if genre_card is None:
            raise SpecError(f"genre references unknown genre card: {genre_value}")
        normalized["genre"] = str(genre_card["id"])

    for field in ("image_params", "video_params", "delivery", "subtitle"):
        value = normalized.get(field)
        if value is not None and not isinstance(value, Mapping):
            raise SpecError(f"{field} must be an object")
    # 交付面遮挡区（DESIGN §27）：只校验 delivery 里的两个可选字段；没写的老项目这里什么都不做
    overlay_problems = overlays.validate(normalized.get("delivery"))
    if overlay_problems:
        raise SpecError("；".join(overlay_problems))
    subtitle_language = "bilingual"
    subtitle = normalized.get("subtitle")
    if subtitle is not None:
        configured_language = subtitle.get("language", "bilingual")
        if (
            not isinstance(configured_language, str)
            or configured_language not in _SUBTITLE_LANGUAGES
        ):
            raise SpecError(
                "subtitle.language must be bilingual, zh, or en"
            )
        subtitle_language = configured_language
    image_params = dict(normalized.get("image_params") or {})
    image_params.setdefault("model", "gpt-image-2")
    normalized["image_params"] = image_params
    video_params = dict(normalized.get("video_params") or {})
    if "generate_audio" in video_params and not isinstance(
        video_params.get("generate_audio"), bool
    ):
        raise SpecError("video_params.generate_audio must be a boolean")
    if video_params.get("generate_audio") is False:
        raise SpecError(
            "video_params.generate_audio=false is unsupported: StoryVideo delivery "
            "requires a dialogue/audio track (provider audio switches are model-specific)"
        )
    video_model = str(
        video_params.get("model") or "doubao-seedance-2-0-260128"
    )
    video_resolution = str(
        video_params.get("resolution")
        or ("768P" if video_model == "MiniMax-H3" else "1080p")
    )
    # Seedance 2.5 的 1080p 按服务商的模型清单放开（仍标按文档）；
    # 放开只多认一档，已写 720p 的项目归一化结果不变，请求指纹也不变
    validated_resolutions = {
        "doubao-seedance-2-0-fast-260128": {"720p"},
        "doubao-seedance-2-0-260128": {"1080p"},
        "doubao-seedance-2-5-260628": {"720p", "1080p"},
        "MiniMax-H3": {"768P"},
    }
    if (
        video_model in validated_resolutions
        and video_resolution not in validated_resolutions[video_model]
    ):
        raise SpecError(
            f"{video_model} has no production-verified {video_resolution} combination"
        )
    video_params["model"] = video_model
    video_params["resolution"] = video_resolution
    if video_model == "MiniMax-H3":
        video_aspect_ratio = str(video_params.get("aspect_ratio") or "adaptive")
        if video_aspect_ratio != "adaptive":
            raise SpecError(
                "MiniMax-H3 first-frame requests require aspect_ratio=adaptive"
            )
        video_params["aspect_ratio"] = video_aspect_ratio
        if "aigc_watermark" in video_params and not isinstance(
            video_params["aigc_watermark"], bool
        ):
            raise SpecError("MiniMax-H3 video_params.aigc_watermark must be a boolean")
    elif "aigc_watermark" in video_params:
        raise SpecError("video_params.aigc_watermark is only supported by MiniMax-H3")
    # count 不在这里拦（ADP-11）：运行时每镜只保留一条结果，适配器按约定只要 1 条，
    # 写了几都不会多花钱；这里报错的话，之前按 MiniMax-H3 放行过 2 / 4 的老项目连详情都打不开、在界面里也改不了。
    # 原值原样保留（也不改成 1：它在出片请求的指纹里，改了会让已出的镜头平白变「过时」），由 lint.check_video_count 提醒删掉
    if video_model.startswith("doubao-seedance-"):
        if "target_fps" in video_params and video_params["target_fps"] != 60:
            raise SpecError("Seedance video_params.target_fps must be 60 when set")
        if "seed" in video_params:
            seed = video_params["seed"]
            if (
                isinstance(seed, bool)
                or not isinstance(seed, int)
                or not 0 <= seed <= 4294967295
            ):
                raise SpecError(
                    "Seedance video_params.seed must be an integer from 0 to 4294967295"
                )
    if "reference_audio" in video_params:
        reference_audio = video_params["reference_audio"]
        if reference_audio in (None, ""):
            video_params.pop("reference_audio")
        elif not isinstance(reference_audio, str):
            raise SpecError("video_params.reference_audio must be a file path string")
        elif Path(reference_audio).suffix.lower() not in (".mp3", ".wav", ".m4a"):
            raise SpecError("video_params.reference_audio must be an mp3 / wav / m4a file")
        else:
            # 出片时会把这个文件上传给视频服务（DESIGN §33「项目相对路径」）：只收数据目录内的相对路径（content/ 或
            # output/ 都行，生成出来的音频在 output/ 下），越界的写法直接拒（PB-4）。存的值原样不动，请求指纹不变
            project_file(reference_audio, root, under=(".",), field="video_params.reference_audio")
    video_params.setdefault("generate_audio", True)
    normalized["video_params"] = video_params

    cast = normalized.get("cast")
    if not isinstance(cast, Mapping):
        raise SpecError("cast must be an object")
    cast_names: set[str] = set()
    for name, definition in cast.items():
        if not isinstance(name, str) or not name.strip():
            raise SpecError("cast keys must be non-empty strings")
        _check_member_key(name, f"cast.{name}")
        if not isinstance(definition, Mapping):
            raise SpecError(f"cast.{name} must be an object")
        cast_names.add(name)
        member: dict[str, Any] | None = None
        # 角色锚图：spec 里存相对项目根的路径（项目目录整体搬走也不失效）；这里在
        # 归一化副本里换成绝对路径，适配器直接读文件编码。只认数据目录 content/ 下的文件（跟 GET …/asset
        # 的读出侧同一条边界）：local: 令牌、~、绝对路径、盘符、.. 直接拒——它们会在出首帧时被读出来随请求
        # 上传（PB-3）。不像本地文件的字符串（历史上的远端 asset id）原样保留。
        resolved_asset = _resolve_local_asset(definition.get("asset"), root, under=("content",),
                                              field=f"cast.{name}.asset")
        if resolved_asset is not None:
            member = dict(definition)
            member["asset"] = resolved_asset
        # 变体：{"rain": {"label", "delta", "asset"?, "shots"?}}；delta 必填，asset 按成员锚图同样的规则解析。
        variants = definition.get("variants")
        if variants is not None:
            if not isinstance(variants, Mapping):
                raise SpecError(f"cast.{name}.variants must be an object")
            resolved_variants: dict[str, Any] = {}
            for key, variant in variants.items():
                if not isinstance(key, str) or not key.strip() or "@" in key:
                    raise SpecError(f"cast.{name}.variants keys must be non-empty strings without '@'")
                _check_member_key(key, f"cast.{name}.variants.{key}")
                if not isinstance(variant, Mapping):
                    raise SpecError(f"cast.{name}.variants.{key} must be an object")
                if not str(variant.get("delta") or "").strip():
                    raise SpecError(f"cast.{name}.variants.{key}.delta must be a non-empty string")
                variant_copy = dict(variant)
                resolved_variant_asset = _resolve_local_asset(variant.get("asset"), root, under=("content",),
                                                              field=f"cast.{name}.variants.{key}.asset")
                if resolved_variant_asset is not None:
                    variant_copy["asset"] = resolved_variant_asset
                resolved_variants[key] = variant_copy
            if member is None:
                member = dict(definition)
            member["variants"] = resolved_variants
        if member is not None:
            normalized["cast"] = dict(normalized["cast"])
            normalized["cast"][name] = member

    raw_shots = normalized.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots:
        raise SpecError("shots must be a non-empty list")

    shots: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_shot in enumerate(raw_shots):
        if not isinstance(raw_shot, Mapping):
            raise SpecError(f"shots[{index}] must be an object")
        raw_shot_copy = copy.deepcopy(dict(raw_shot))
        shot = copy.deepcopy(raw_shot_copy)
        shot_id = _nonempty_text(shot.get("id"), f"shots[{index}].id")
        if not _SHOT_ID_RE.fullmatch(shot_id):
            raise SpecError(
                f"shots[{index}].id must be a 1-64 character safe slug"
            )
        if shot_id in seen_ids:
            raise SpecError(f"duplicate shot id: {shot_id}")
        seen_ids.add(shot_id)

        duration = shot.get("dur")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(float(duration))
            or duration <= 0
        ):
            raise SpecError(f"shot {shot_id}.dur must be a finite number greater than zero")
        if not float(duration).is_integer():
            raise SpecError(
                f"shot {shot_id}.dur must be a whole number of seconds; "
                "fractional values would change the paid request"
            )
        if video_model == "doubao-seedance-2-5-260628" and not 4 <= int(duration) <= 30:
            raise SpecError(
                f"shot {shot_id}.dur must be 4-30 seconds for {video_model}"
            )
        if (
            video_model.startswith("doubao-seedance-2-0")
            or video_model == "MiniMax-H3"
        ) and not 4 <= int(duration) <= 15:
            raise SpecError(
                f"shot {shot_id}.dur must be 4-15 seconds for {video_model}"
            )

        shot_cast = shot.get("cast")
        if not isinstance(shot_cast, list):
            raise SpecError(f"shot {shot_id}.cast must be a list")
        for cast_index, cast_name in enumerate(shot_cast):
            if not isinstance(cast_name, str) or not cast_name.strip():
                raise SpecError(
                    f"shot {shot_id}.cast[{cast_index}] must be a non-empty string"
                )
            member_id, variant_key = split_cast_ref(cast_name)
            if member_id not in cast_names:
                raise SpecError(f"shot {shot_id}.cast references unknown cast: {cast_name}")
            if variant_key is not None:
                member_variants = (normalized["cast"].get(member_id) or {}).get("variants") or {}
                if variant_key not in member_variants:
                    raise SpecError(
                        f"shot {shot_id}.cast references unknown variant: {cast_name}"
                        f" (cast.{member_id}.variants has {', '.join(member_variants) or 'none'})"
                    )
            elif "@" in cast_name:
                raise SpecError(f"shot {shot_id}.cast[{cast_index}] has an empty variant key: {cast_name}")

        for field in _SHOT_TEXT_FIELDS:
            _nonempty_text(shot.get(field), f"shot {shot_id}.{field}")

        # 关系场景类型（剧本工艺清单的关系场景判据，docs/script-craft-gap.md）：可选枚举，写了才有
        # lint.check_relation_boundaries；不认识的值拒绝，空串等于没写。不进提示词。
        scene_type = shot.get("scene_type")
        if scene_type is not None:
            if not isinstance(scene_type, str):
                raise SpecError(f"shot {shot_id}.scene_type must be one of: {', '.join(SCENE_TYPES)}")
            cleaned_scene = scene_type.strip().lower()
            if not cleaned_scene:
                shot.pop("scene_type", None)
            elif cleaned_scene not in SCENE_TYPES:
                raise SpecError(f"shot {shot_id}.scene_type must be one of: {', '.join(SCENE_TYPES)}")
            else:
                shot["scene_type"] = cleaned_scene

        # 说话方式：可选枚举，空串等于没写（画内说话）。不认识的值在这里拒——prompts 只把 SPEECH_MODES 里的当画外，
        # 别的字符串一律按画内编提示词（要求嘴型跟台词对上），配音叠上去穿帮要等出片才看得出来。
        speech_mode = shot.get("speech_mode")
        if speech_mode is not None:
            if not isinstance(speech_mode, str):
                raise SpecError(f"shot {shot_id}.speech_mode must be one of: {', '.join(SPEECH_MODES)}")
            cleaned_speech = speech_mode.strip()
            if not cleaned_speech:
                shot.pop("speech_mode", None)
            elif cleaned_speech not in SPEECH_MODES:
                raise SpecError(f"shot {shot_id}.speech_mode must be one of: {', '.join(SPEECH_MODES)}")
            else:
                shot["speech_mode"] = cleaned_speech

        # 观众可见性 / 揭示保护（DESIGN §28）：withhold = [{fact, carrier?, until?}]。fact 是观众此刻还不能
        # 知道的事实，carrier 是会泄露它的载体（脸 / 戒指 / 台词），until 是从哪一镜起可以揭示（空 = 整集）。
        # until 要指向存在的镜，但它通常指向后面的镜，所以放到所有镜收集完之后再核对。
        withhold = shot.get("withhold")
        if withhold is not None:
            if not isinstance(withhold, list):
                raise SpecError(f"shot {shot_id}.withhold must be a list")
            for w_index, item in enumerate(withhold):
                label = f"shot {shot_id}.withhold[{w_index}]"
                if not isinstance(item, Mapping):
                    raise SpecError(f"{label} must be an object")
                _nonempty_text(item.get("fact"), f"{label}.fact")
                for key in ("carrier", "until"):
                    value = item.get(key)
                    if value is not None and not isinstance(value, str):
                        raise SpecError(f"{label}.{key} must be a string")

        # 尾帧续接（DESIGN §30）：ref_tail = {"shot": 源镜, "mode": reference|direct}，裸字符串 = reference。
        # 源镜必须是**前面**的镜（尾帧要等它的视频过检）；同时写了 ref_frame 时两者必须指向同一镜。
        # 归一化副本里统一成对象形状；source_hash 仍按原样算。
        tail_raw = shot.get("ref_tail")
        if tail_raw is not None:
            if isinstance(tail_raw, str):
                tail_obj: dict[str, Any] = {"shot": tail_raw, "mode": "reference"}
            elif isinstance(tail_raw, Mapping):
                tail_obj = {"shot": tail_raw.get("shot"), "mode": tail_raw.get("mode") or "reference"}
                unknown_keys = [k for k in tail_raw if k not in ("shot", "mode")]
                if unknown_keys:
                    raise SpecError(f"shot {shot_id}.ref_tail has unknown fields: {', '.join(map(str, unknown_keys))}")
            else:
                raise SpecError(f"shot {shot_id}.ref_tail must be a shot id or an object with shot and mode")
            _nonempty_text(tail_obj["shot"], f"shot {shot_id}.ref_tail.shot")
            tail_source = str(tail_obj["shot"]).strip()
            if not isinstance(tail_obj["mode"], str) or tail_obj["mode"].strip().lower() not in _TAIL_MODES:
                raise SpecError(f"shot {shot_id}.ref_tail.mode must be reference or direct")
            if tail_source == shot_id:
                raise SpecError(f"shot {shot_id}.ref_tail must not reference itself")
            if tail_source not in seen_ids:
                raise SpecError(
                    f"shot {shot_id}.ref_tail references a shot that is not earlier in the episode: {tail_source}"
                )
            ref_frame_value = shot.get("ref_frame")
            if isinstance(ref_frame_value, str) and ref_frame_value.strip() and ref_frame_value.strip() != tail_source:
                raise SpecError(
                    f"shot {shot_id}.ref_tail ({tail_source}) and ref_frame ({ref_frame_value.strip()}) "
                    "must point to the same shot"
                )
            shot["ref_tail"] = {"shot": tail_source, "mode": tail_obj["mode"].strip().lower()}

        # 参考槽位用途（DESIGN §30）：reference_roles = [{target, use, control, ignore}]。target 必须是本镜真的会带的
        # 参考图：prev_frame（ref_frame / 尾帧占的依赖槽）、tail（ref_tail 作参考图）、cast:<id>[@变体]（有锚图的出场
        # 角色）、prop（spec.prop_ref）。control / ignore 是方面词列表。
        roles = shot.get("reference_roles")
        if roles is not None:
            if not isinstance(roles, list):
                raise SpecError(f"shot {shot_id}.reference_roles must be a list")
            for r_index, role in enumerate(roles):
                label = f"shot {shot_id}.reference_roles[{r_index}]"
                if not isinstance(role, Mapping):
                    raise SpecError(f"{label} must be an object")
                target = _nonempty_text(role.get("target"), f"{label}.target").strip()
                use = role.get("use")
                if use is not None and not isinstance(use, str):
                    raise SpecError(f"{label}.use must be a string")
                for key in ("control", "ignore"):
                    aspects = role.get(key)
                    if aspects is None:
                        continue
                    if not isinstance(aspects, list) or any(not isinstance(a, str) or not a.strip() for a in aspects):
                        raise SpecError(f"{label}.{key} must be a list of non-empty strings")
                fidelity = role.get("fidelity")
                if fidelity is not None and (isinstance(fidelity, bool) or not isinstance(fidelity, (int, float)) or not 1 <= float(fidelity) <= 100):
                    raise SpecError(f"{label}.fidelity must be a number between 1 and 100")
                _check_reference_target(target, shot, normalized["cast"], has_prop=bool(normalized.get("prop_ref")), label=label)

        shot["lines"] = _normalize_lines(
            shot,
            shot_id,
            duration_seconds=float(duration),
            subtitle_language=subtitle_language,
            cast=normalized["cast"],
        )
        shot["source_hash"] = stable_json_hash(raw_shot_copy)
        shots.append(shot)

    waves = topological_waves(shots)

    for shot in shots:
        for w_index, item in enumerate(shot.get("withhold") or ()):
            until = str(item.get("until") or "").strip()
            if until and until not in seen_ids:
                raise SpecError(
                    f"shot {shot['id']}.withhold[{w_index}].until references unknown shot: {until}"
                )

    # 单集契约与系列（DESIGN §32）：两个可选的顶层对象，只归一化形状（字符串 / 字符串列表 / 正整数集数），
    # 不进提示词；老项目没写就一个字不动。空契约（五个字段都空）归一成没写。
    for field, normalizer in (("episode_contract", _contract.normalize_contract), ("series", _contract.normalize_series)):
        if field in normalized:
            try:
                cleaned = normalizer(normalized.get(field))
            except _contract.ContractError as exc:
                raise SpecError(str(exc)) from exc
            if cleaned is None:
                normalized.pop(field, None)
            else:
                normalized[field] = cleaned

    if "prop_ref" in normalized and normalized["prop_ref"] is not None:
        prop_ref = normalized["prop_ref"]
        if not isinstance(prop_ref, (str, os.PathLike)) or not str(prop_ref).strip():
            raise SpecError("prop_ref must be a non-empty path")
        normalized["prop_ref"] = str(
            _resolve_within(
                prop_ref,
                base=output_root,
                boundary=output_root,
                field="prop_ref",
            )
        )

    normalized["shots"] = shots
    normalized["output_root"] = str(output_root)
    normalized["project_root"] = str(root)
    normalized["spec_dir"] = str(spec_dir)
    normalized["spec_path"] = str(resolved_spec_path) if resolved_spec_path else None
    normalized["waves"] = waves
    normalized["source_hash"] = stable_json_hash(original)
    return normalized


def load_spec(
    source: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Load, validate, and normalize an episode spec by id or explicit path."""

    path, root = resolve_spec_path(source, project_root=project_root)
    try:
        raw_text = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw_text)
    except OSError as exc:
        raise SpecError(f"could not read episode spec {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SpecError(
            f"invalid JSON in episode spec {path} at line {exc.lineno}, column {exc.colno}"
        ) from exc
    return normalize_spec(data, spec_path=path, project_root=root)


# A descriptive alias makes call sites self-documenting while retaining the
# compact ``load_spec`` API used by scripts and tests.
load_episode_spec = load_spec
validate_spec = normalize_spec


__all__ = [
    "SCENE_TYPES",
    "SpecError",
    "load_episode_spec",
    "load_spec",
    "normalize_spec",
    "resolve_spec_path",
    "split_cast_ref",
    "stable_json_hash",
    "topological_waves",
    "validate_spec",
]
