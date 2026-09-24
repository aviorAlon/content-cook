"""播客项目文件：归一化 / 读 / 写 / 编号 / 归档（契约 docs/podcast-spec.md §1）。

跟配乐 ``music.spec`` 一个思路：一处边界把形状校验完，后面的模块只认归一化后的字典。归一化后多两个运行时键
``output_root``（``output/podcast/<ID>``）与 ``project_root``，写盘时去掉（``to_disk``）。

形状要点：``hosts`` 是人物表（1–4 人，说话人只能是这里的 id）；``segments`` 是环节（场景），每个环节有节拍、
听众问题（只有 qa 用）和台词；台词的说话人写错等于没人说，所以归一化直接拒绝，不静默丢。
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
from storyvideo.spec import SpecError

MODULE = "podcast"
ID_PREFIX = "PC"
CONTENT_DIR = Path("content") / "podcast"
OUTPUT_DIR = Path("output") / "podcast"
ASSETS_DIR = Path("content") / "assets"

LANGUAGES = ("zh", "en")
# 形式 → (中文名, 英文名, 主播人数下限, 上限, 一句话)
FORMATS: dict[str, dict[str, Any]] = {
    "solo": {"name": "单人独白", "english": "Solo", "hosts": (1, 1), "note": "一个人讲：像给朋友讲事，不是念稿"},
    "duo": {"name": "双人对谈", "english": "Two hosts", "hosts": (2, 2), "note": "两个人聊：要有分歧、打断和接话"},
    "interview": {"name": "访谈", "english": "Interview", "hosts": (2, 3), "note": "主播问、嘉宾答：问题要追问，回答要有细节"},
    "roundtable": {"name": "圆桌", "english": "Roundtable", "hosts": (3, 4), "note": "三四个人：每个人立场不同，别让一个人独大"},
}
ROLES = ("host", "cohost", "guest")
SEGMENT_KINDS = ("cold_open", "intro", "topic", "qa", "outro")
SEGMENT_KIND_LABELS = {"cold_open": "冷开场", "intro": "开场", "topic": "话题", "qa": "听友问答", "outro": "收尾"}
LINE_KINDS = ("talk", "question", "answer")
CUES = ("笑", "轻笑", "停顿", "打断", "叹气", "加快", "放慢", "低声", "强调")
MUSIC_SLOTS = ("intro", "bed", "outro")
MUSIC_SLOT_LABELS = {"intro": "片头", "bed": "垫乐", "outro": "片尾"}   # 配乐模块投放时按槽说话，也从这里取
AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
# 随交付包发出去的产物（节目单 / 逐字稿 / 章节 / AI 生成说明）按 ``language`` 选一套标签：英文期以前全是中文标签、全角冒号
# （2026-09-22 审计）。界面文案不在这里——那是前端 i18n 的事；这里只管写进文件的字。
LABELS: dict[str, dict[str, Any]] = {
    "zh": {
        "format": "形式", "duration": "时长", "hosts": "主播", "chapters": "章节", "takeaways": "听完带走", "questions": "听友问题",
        "footer": "本期由 AI 辅助起草与合成；主播音色为合成音。", "colon": "：", "sep": "、", "dot": " · ", "paren": ("（", "）"),
        "roles": {"host": "主播", "cohost": "搭档", "guest": "嘉宾"}, "kinds": SEGMENT_KIND_LABELS,
    },
    "en": {
        "format": "Format", "duration": "Duration", "hosts": "Hosts", "chapters": "Chapters", "takeaways": "Takeaways",
        "questions": "Listener questions", "footer": "This episode was drafted and synthesized with AI assistance; the hosts' voices are synthetic.",
        "colon": ": ", "sep": ", ", "dot": " · ", "paren": (" (", ")"),
        "roles": {"host": "Host", "cohost": "Co-host", "guest": "Guest"},
        "kinds": {"cold_open": "Cold open", "intro": "Intro", "topic": "Topic", "qa": "Listener Q&A", "outro": "Outro"},
    },
}


def labels(spec: Mapping[str, Any]) -> dict[str, Any]:
    """产物文件用的标签表：``spec.language`` 是 en 就英文，其余中文。"""

    return LABELS.get(str(spec.get("language") or "zh"), LABELS["zh"])


def format_name(spec: Mapping[str, Any]) -> str:
    """形式的名字，按 ``spec.language``：中文期「双人对谈」、英文期 "Two hosts"。"""

    fmt = FORMATS.get(str(spec.get("format") or "duo"), FORMATS["duo"])
    return str(fmt["english"] if str(spec.get("language") or "zh") == "en" else fmt["name"])


MINUTES_MIN, MINUTES_MAX = 1, 90
HOSTS_MIN, HOSTS_MAX = 1, 4
PAUSE_MIN, PAUSE_MAX = 0.0, 5.0
DEFAULT_MINUTES = 12
DEFAULT_PAUSE = {"line": 0.35, "segment": 1.2}
DEFAULT_MUSIC = {"intro": "", "bed": "", "outro": "", "bed_gain": 0.18, "duck": 0.45}
GAIN_MIN, GAIN_MAX = 0.0, 1.0


class PodcastSpecError(SpecError):
    """播客项目文件缺字段或形状不对。"""


class PodcastArchiveError(PodcastSpecError):
    """归档搬不动（产物目录里有文件正被占用）——什么都没搬，项目还在。"""


# ---------------------------------------------------------------------------- 路径


def content_dir(project_root: Path) -> Path:
    return Path(project_root) / CONTENT_DIR


def spec_path(project_root: Path, spec_id: str) -> Path:
    return content_dir(project_root) / f"{spec_id}.json"


def existing_ids(project_root: Path) -> list[str]:
    root = content_dir(project_root)
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.json") if p.is_file())


def next_id(existing: Iterable[str], *, today: date | None = None) -> str:
    """PC-20260916-1、PC-20260916-2…（按天编号）。"""

    return next_episode_id(existing, prefix=ID_PREFIX, today=today)


def output_root(spec: Mapping[str, Any]) -> Path:
    return Path(str(spec["output_root"]))


def audio_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "07_audio"


def mix_dir(spec: Mapping[str, Any]) -> Path:
    return output_root(spec) / "08_mix"


def resolve_asset(spec: Mapping[str, Any], value: Any) -> Path | None:
    """项目内相对路径 → 存在的绝对路径；不存在 / 空 / 越界 → None。"""

    text = str(value or "").strip()
    if not text:
        return None
    root = Path(str(spec.get("project_root") or _data_root())).resolve(strict=False)
    candidate = (root / text).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


# ---------------------------------------------------------------------------- 归一化


def _text(value: Any, field: str, *, required: bool = False, default: str = "") -> str:
    if value is None:
        if required:
            raise PodcastSpecError(f"{field} 不能为空")
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise PodcastSpecError(f"{field} 要是文本")
    out = value.strip()
    if required and not out:
        raise PodcastSpecError(f"{field} 不能为空")
    return out


def _int(value: Any, field: str, *, default: int, minimum: int, maximum: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise PodcastSpecError(f"{field} 要是整数")
    # int() 会把 3.7 悄悄截成 3、把 90.5 截成 90 混过范围检查，而字符串 "3.7" 却报错——同一个值两种写法得一个口径：都拒
    if isinstance(value, float) and not value.is_integer():
        raise PodcastSpecError(f"{field} 要是整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PodcastSpecError(f"{field} 要是整数") from exc
    if not minimum <= number <= maximum:
        raise PodcastSpecError(f"{field} 要在 {minimum} 到 {maximum} 之间")
    return number


def _float(value: Any, field: str, *, default: float, minimum: float, maximum: float) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise PodcastSpecError(f"{field} 要是数字")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PodcastSpecError(f"{field} 要是数字") from exc
    if not minimum <= number <= maximum:
        raise PodcastSpecError(f"{field} 要在 {minimum:g} 到 {maximum:g} 之间")
    return round(number, 3)


def _choice(value: Any, field: str, choices: tuple[str, ...], *, default: str | None = None) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            raise PodcastSpecError(f"{field} 不能为空，只能是 {' / '.join(choices)}")
        return default
    text = str(value).strip()
    if text not in choices:
        raise PodcastSpecError(f"{field} 只能是 {' / '.join(choices)}，收到「{text}」")
    return text


def _str_list(raw: Any, field: str) -> list[str]:
    """数组或「顿号 / 逗号 / 换行分隔的一段」都认。"""

    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        parts = re.split(r"[、,，;；\n]", raw)
    elif isinstance(raw, list):
        parts = [str(v) for v in raw]
    else:
        raise PodcastSpecError(f"{field} 要是数组（或用顿号分隔的一行）")
    return [p.strip() for p in parts if str(p).strip()]


def _relative_path(value: Any, field: str) -> str:
    text = _text(value, field)
    if not text:
        return ""
    if "\\" in text or text.startswith("/") or (len(text) > 1 and text[1] == ":") or ".." in Path(text).parts:
        raise PodcastSpecError(f"{field} 只能是项目内的相对路径（content/assets/…）")
    if not text.lower().endswith(AUDIO_SUFFIXES):
        raise PodcastSpecError(f"{field} 要是音频文件：{' / '.join(s.lstrip('.') for s in AUDIO_SUFFIXES)}")
    return text


def _brief(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise PodcastSpecError("brief 要是对象（topic / angle / audience / takeaways / materials / tone / avoid）")
    return {
        "topic": _text(raw.get("topic"), "brief.topic"),
        "angle": _text(raw.get("angle"), "brief.angle"),
        "audience": _text(raw.get("audience"), "brief.audience"),
        "takeaways": _str_list(raw.get("takeaways"), "brief.takeaways"),
        "materials": _text(raw.get("materials"), "brief.materials"),
        "tone": _text(raw.get("tone"), "brief.tone"),
        "avoid": _str_list(raw.get("avoid"), "brief.avoid"),
    }


def _voice(raw: Any, field: str) -> Any:
    """音色：音色库档案 id（字符串）或内联档案 {engine, voice, rate, pitch}；空 = 占位。"""

    if raw is None or raw == "":
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, Mapping):
        out = {k: _text(raw.get(k), f"{field}.{k}") for k in ("engine", "voice", "rate", "pitch")}
        if out["engine"]:
            # 音色库自己（genkit.voices._normalize）校验 engine，内联档案以前不校验：engine 乱写照收、合成时静默走 edge、
            # 报价把它当付费引擎、清单和交付包原样记下——在这里就拒，跟音色库一个口径
            from genkit.voices import ENGINES

            if out["engine"] not in ENGINES:
                raise PodcastSpecError(f"{field}.engine 只能是 {' / '.join(ENGINES)}，收到「{out['engine']}」")
        return {k: v for k, v in out.items() if v}
    raise PodcastSpecError(f"{field} 要是音色库档案 id 或 {{engine, voice, rate, pitch}} 对象")


def _hosts(raw: Any, fmt: str) -> list[dict[str, Any]]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise PodcastSpecError("hosts 要是数组（主播列表）")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        field = f"hosts[{index}]"
        if not isinstance(entry, Mapping):
            raise PodcastSpecError(f"{field} 要是对象（id / name / role / persona / stance / voice）")
        name = _text(entry.get("name"), f"{field}.name", required=True)
        hid = _text(entry.get("id"), f"{field}.id") or f"h{index + 1}"
        if not ID_RE.fullmatch(hid):
            raise PodcastSpecError(f"{field}.id 只能用英文、数字、下划线、点、短横线")
        if hid in seen:
            raise PodcastSpecError(f"主播 id 重复了：{hid}——每个人一个 id，台词的说话人靠它对上")
        seen.add(hid)
        out.append({
            "id": hid,
            "name": name,
            "role": _choice(entry.get("role"), f"{field}.role", ROLES, default="host" if index == 0 else "cohost"),
            "persona": _text(entry.get("persona"), f"{field}.persona"),
            "stance": _text(entry.get("stance"), f"{field}.stance"),
            "voice": _voice(entry.get("voice"), f"{field}.voice"),
        })
    lo, hi = FORMATS[fmt]["hosts"]
    if out and not lo <= len(out) <= hi:
        label = FORMATS[fmt]["name"]
        span = f"{lo} 人" if lo == hi else f"{lo} 到 {hi} 人"
        raise PodcastSpecError(f"形式「{label}」要 {span}，现在有 {len(out)} 人")
    if len(out) > HOSTS_MAX:
        raise PodcastSpecError(f"主播最多 {HOSTS_MAX} 人")
    if fmt == "interview" and out:
        roles = {h["role"] for h in out}
        if "guest" not in roles or "host" not in roles:
            raise PodcastSpecError("访谈要至少一位 host 和一位 guest——在主播表里把角色选对")
    return out


def default_segment_id(index: int) -> str:
    return f"G{index:02d}"


def _questions(raw: Any, field: str) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PodcastSpecError(f"{field} 要是数组（听众问题）")
    out = []
    for i, q in enumerate(raw):
        if isinstance(q, str):
            text = q.strip()
            if text:
                out.append({"from": "", "text": text})
            continue
        if not isinstance(q, Mapping):
            raise PodcastSpecError(f"{field}[{i}] 要是对象（from / text）")
        text = _text(q.get("text"), f"{field}[{i}].text")
        if text:
            out.append({"from": _text(q.get("from"), f"{field}[{i}].from"), "text": text})
    return out


def _lines(raw: Any, field: str, host_ids: set[str], host_names: dict[str, str]) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PodcastSpecError(f"{field} 要是数组（台词）")
    out = []
    for i, line in enumerate(raw):
        lf = f"{field}[{i}]"
        if not isinstance(line, Mapping):
            raise PodcastSpecError(f"{lf} 要是对象（who / text / cue / kind）")
        text = _text(line.get("text"), f"{lf}.text")
        if not text:
            continue
        who = _text(line.get("who"), f"{lf}.who", required=True)
        if who not in host_ids:
            if who in host_names:
                who = host_names[who]
            else:
                raise PodcastSpecError(f"{lf} 的说话人「{who}」不在主播表里——从主播表里选一个（用 id 或名字），或者先把这个人加进主播表")
        cue = _text(line.get("cue"), f"{lf}.cue")
        if cue and cue not in CUES:
            # 不认识的 cue 不拦（lint 会提），但要是文本
            pass
        out.append({
            "who": who,
            "text": text,
            "cue": cue,
            "kind": _choice(line.get("kind"), f"{lf}.kind", LINE_KINDS, default="talk"),
        })
    return out


def _segments(raw: Any, hosts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PodcastSpecError("segments 要是数组（环节列表）")
    host_ids = {h["id"] for h in hosts}
    host_names = {h["name"]: h["id"] for h in hosts}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, 1):
        field = f"segments[{index - 1}]"
        if not isinstance(entry, Mapping):
            raise PodcastSpecError(f"{field} 要是对象（id / kind / title / goal / beats / questions / lines）")
        sid = _text(entry.get("id"), f"{field}.id") or default_segment_id(index)
        if not ID_RE.fullmatch(sid):
            raise PodcastSpecError(f"{field}.id 只能用英文、数字、下划线、点、短横线")
        if sid in seen:
            raise PodcastSpecError(f"环节 id 重复了：{sid}——每个环节一个 id，告警和重写都靠它对上")
        seen.add(sid)
        beats = entry.get("beats")
        if isinstance(beats, str):
            beats = [b for b in beats.splitlines()]
        out.append({
            "id": sid,
            "kind": _choice(entry.get("kind"), f"{field}.kind", SEGMENT_KINDS, default="topic"),
            "title": _text(entry.get("title"), f"{field}.title"),
            "goal": _text(entry.get("goal"), f"{field}.goal"),
            "beats": _str_list(beats, f"{field}.beats") if not isinstance(beats, list) else [str(b).strip() for b in beats if str(b).strip()],
            "questions": _questions(entry.get("questions"), f"{field}.questions"),
            "lines": _lines(entry.get("lines"), f"{field}.lines", host_ids, host_names),
        })
    return out


def _music(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise PodcastSpecError("music 要是对象（intro / bed / outro / bed_gain / duck）")
    out: dict[str, Any] = {slot: _relative_path(raw.get(slot), f"music.{slot}") for slot in MUSIC_SLOTS}
    out["bed_gain"] = _float(raw.get("bed_gain"), "music.bed_gain", default=DEFAULT_MUSIC["bed_gain"], minimum=GAIN_MIN, maximum=GAIN_MAX)
    out["duck"] = _float(raw.get("duck"), "music.duck", default=DEFAULT_MUSIC["duck"], minimum=GAIN_MIN, maximum=GAIN_MAX)
    return out


def _pause(raw: Any) -> dict[str, float]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise PodcastSpecError("pause 要是对象（line / segment，单位秒）")
    return {
        "line": _float(raw.get("line"), "pause.line", default=DEFAULT_PAUSE["line"], minimum=PAUSE_MIN, maximum=PAUSE_MAX),
        "segment": _float(raw.get("segment"), "pause.segment", default=DEFAULT_PAUSE["segment"], minimum=PAUSE_MIN, maximum=PAUSE_MAX),
    }


def _draft(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise PodcastSpecError("draft 要是对象")
    return {
        "model": _text(raw.get("model"), "draft.model"),
        "backend": _text(raw.get("backend"), "draft.backend"),
        "created_at": raw.get("created_at"),
        "usage_tokens": raw.get("usage_tokens"),
        "note": _text(raw.get("note"), "draft.note"),
    }


def normalize(data: Mapping[str, Any], *, project_root: str | os.PathLike[str] | None = None,
              spec_id: str | None = None) -> dict[str, Any]:
    """校验 + 补默认值。``spec_id`` 给了就覆盖 / 补上 id（新建时用）。"""

    if not isinstance(data, Mapping):
        raise PodcastSpecError("项目文件要是一个 JSON 对象")
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else _data_root()
    raw = copy.deepcopy(dict(data))
    identifier = _text(spec_id if spec_id is not None else raw.get("id"), "id")
    if not identifier:
        raise PodcastSpecError("id 不能为空——新建时由服务端编号")
    if not ID_RE.fullmatch(identifier):
        raise PodcastSpecError("id 只能用英文、数字、下划线、点、短横线")
    fmt = _choice(raw.get("format"), "format", tuple(FORMATS), default="duo")
    hosts = _hosts(raw.get("hosts"), fmt)
    normalized: dict[str, Any] = {
        "id": identifier,
        "module": MODULE,
        "title": _text(raw.get("title"), "title", required=True),
        "format": fmt,
        "language": _choice(raw.get("language"), "language", LANGUAGES, default="zh"),
        "brief": _brief(raw.get("brief")),
        "target_minutes": _int(raw.get("target_minutes"), "target_minutes", default=DEFAULT_MINUTES, minimum=MINUTES_MIN, maximum=MINUTES_MAX),
        "hosts": hosts,
        "segments": _segments(raw.get("segments"), hosts),
        "music": _music(raw.get("music")),
        "pause": _pause(raw.get("pause")),
        "draft": _draft(raw.get("draft")),
    }
    file_module = _text(raw.get("module"), "module")
    if file_module and file_module != MODULE:
        raise PodcastSpecError(f"项目文件的 module 是「{file_module}」，跟这个模块（{MODULE}）对不上")
    normalized["output_root"] = str((root / OUTPUT_DIR / identifier).resolve(strict=False))
    normalized["project_root"] = str(root)
    return normalized


def to_disk(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in spec.items() if k not in ("output_root", "project_root")}


def load(spec_id: str, *, project_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else _data_root()
    if not ID_RE.fullmatch(str(spec_id or "")):
        raise PodcastSpecError(f"没有这期播客：{spec_id}")
    path = spec_path(root, spec_id)
    if not path.is_file():
        raise PodcastSpecError(f"没有这期播客：{spec_id}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise PodcastSpecError(f"读不了项目文件 {path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise PodcastSpecError(f"项目文件不是合法的 JSON（{path} 第 {exc.lineno} 行）") from exc
    if isinstance(data, Mapping) and str(data.get("id") or "") not in ("", spec_id):
        raise PodcastSpecError(f"项目文件里的 id「{data.get('id')}」跟文件名 {spec_id} 对不上")
    return normalize(data, project_root=root, spec_id=spec_id)


def save(spec: Mapping[str, Any]) -> Path:
    """归一化后的 spec 原子写盘（``to_disk`` 形状）；返回路径。"""

    from storyvideo.state import atomic_write_json

    target = spec_path(Path(str(spec["project_root"])), str(spec["id"]))
    atomic_write_json(target, to_disk(spec))
    return target


def archive(spec_id: str, *, project_root: Path) -> dict[str, Any]:
    """归档到 ``.webui_deleted/podcast/<ID>__<时间>/``（项目文件 + 产物目录 + 上传的音乐一起搬，附 manifest），不是 ``os.remove``。

    先搬产物目录和 ``content/assets/<ID>``，项目文件最后搬：中途搬不动（产物有文件被占用）就把搬过的搬回去，
    抛 ``PodcastArchiveError``，项目还在列表里。以前是 spec.json 先走、产物目录搬失败直接冒 500，列表里没了、
    产物还在原地、归档里是半截副本；上传的音乐从来不搬，重建同 id 会直接复用。搬家本身在 ``genkit.trash``（各模块共用，
    回收站页按它写的 manifest 列出、恢复）。
    """

    path = spec_path(project_root, spec_id)
    if not ID_RE.fullmatch(str(spec_id or "")) or not path.is_file():
        raise PodcastSpecError(f"没有这期播客：{spec_id}")
    try:
        return trash.archive(Path(project_root), MODULE, spec_id)
    except trash.FilesBusy as exc:
        raise PodcastArchiveError(f"产物目录有文件正被占用，没归档：{exc.cause}——等合成 / 导出结束，或关掉正在播放它的程序再删") from exc


# ---------------------------------------------------------------------------- 只读辅助


def host_by_id(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(h["id"]): h for h in spec.get("hosts") or () if isinstance(h, Mapping) and h.get("id")}


def all_lines(spec: Mapping[str, Any]) -> list[tuple[dict[str, Any], int, dict[str, Any]]]:
    """``[(环节, 序号从 1, 台词)]``，按播放顺序。"""

    out = []
    for segment in spec.get("segments") or ():
        for index, line in enumerate(segment.get("lines") or (), 1):
            out.append((segment, index, line))
    return out


def formats_payload() -> list[dict[str, Any]]:
    return [{"id": fid, "name": f["name"], "english": f["english"], "hosts": list(f["hosts"]), "note": f["note"]} for fid, f in FORMATS.items()]


__all__ = [
    "MODULE", "ID_PREFIX", "CONTENT_DIR", "OUTPUT_DIR", "ASSETS_DIR", "LANGUAGES", "FORMATS", "ROLES", "SEGMENT_KINDS",
    "SEGMENT_KIND_LABELS", "LINE_KINDS", "CUES", "MUSIC_SLOTS", "AUDIO_SUFFIXES", "LABELS", "MINUTES_MIN", "MINUTES_MAX", "HOSTS_MIN",
    "HOSTS_MAX", "DEFAULT_MINUTES", "DEFAULT_PAUSE", "DEFAULT_MUSIC", "PodcastSpecError", "PodcastArchiveError", "content_dir",
    "spec_path", "existing_ids", "next_id", "output_root", "audio_dir", "mix_dir", "resolve_asset", "labels", "format_name",
    "default_segment_id", "normalize", "to_disk", "load", "save", "archive", "host_by_id", "all_lines", "formats_payload",
]
