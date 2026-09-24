"""剧本工作台的项目文件（契约 docs/scriptlab-spec.md §2）：归一化 / 读 / 写 / 编号 / 归档。

形状与错误文案照 ``podcast.spec``：一处边界把形状校验完，后面的模块（``craft`` / ``lint`` / ``review``）
只认归一化后的字典。归一化后多两个运行时键 ``output_root``（``output/script/<ID>``，只放审稿记录）与
``project_root``，写盘时去掉（``to_disk``）。

形状要点：

* ``speakers`` 是工作台自己的角色表（最多 6 人，台词的说话人只能是这里的 id 或名字）；投放时再映射到目标项目的
  角色 / 主播，所以这里不管对面叫什么。id 和**名字**都不许重复——台词的说话人可以按名字写，两个人同名就分不清是谁。
  下限（契约 §2 的「1–6 人」里的 1）不在归一化拦：刚新建的项目还没填角色也要能存；要角色的那几步（起草、审稿、
  投放）调 ``require_speakers`` 拦，别各自手写一句 400。
* ``blocks`` 是段落骨架（人写的：种类 / 标题 / 目标 / 备注），顺序就是播放顺序；``versions`` 里每一版只存每段的台词。
  ``kind`` 只有 ``talk`` / ``qa`` 两种，默认 ``talk``：听友问答那三条判据只认 ``qa``（见 ``BLOCK_KINDS``）。
* **一版的段永远跟骨架对齐**：按骨架顺序补齐，骨架里有、这一版没写的段留空台词；骨架里没有的段号直接报错。
  「两版并排比」要能逐段对上，段号是唯一的锚，对不齐就比不了。
* ``current`` 指向采用的那一版；一版也没有时是空串。项目还没采用任何一版时新加的那一版自动采用、之后的版本
  不自动顶替（``adopt_if_first``，契约 §3）。**没采用时默认看最新一版**（``pick_version``）——整体保存（PUT）时
  ``current`` 可能被清空，这时检查 / 估算 / 审稿要是都当「没有稿子」，最新的那一版在界面上就成了零告警、0 分钟、
  没法审。``current_version`` 仍然只认真正采用的那一版。
* 说话人写错等于这句话没人说，所以归一化直接拒绝，不静默丢（跟播客、剧本层同一原则）。
* ``cue`` 只认播客那套（``podcast.spec.CUES``），``kind`` 认 ``talk / question / answer``——两个都在这里拦下来，
  检查（``lint.py``）就不用再判一遍写错的 cue。
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
from podcast.spec import CUES, LINE_KINDS
from storyvideo.spec import SpecError

MODULE = "script"
ID_PREFIX = "SC"
CONTENT_DIR = Path("content") / "script"
OUTPUT_DIR = Path("output") / "script"

LANGUAGES = ("zh", "en")
# 目标形态 → (中文名, 英文名, 一句话：用哪套写法与检查)。新建页四选一，提示词与检查都按它选。
# english 与 webui/i18n_rules.py 里这四个名字的规则是同一套措辞：接口层按语言翻 name，前端英文时直接用 english，
# 两边翻出来必须一样，不然首页 pill 与新建页下拉会因为「谁翻的」而叫法不同。
TARGETS: dict[str, dict[str, Any]] = {
    "podcast": {"name": "播客对谈", "english": "Podcast",
                "note": "按播客真人感那八条写：口语、有分歧、具体压倒观点；检查数书面语、独白、轮流、填充词"},
    "drama_screenplay": {"name": "短剧剧本（场）", "english": "Short drama scene",
                         "note": "按短剧台词那几条写：留潜台词、嘴上和身上各演一半、只写拍得到的；检查数单句字数和开口人数"},
    "drama_shot": {"name": "短剧分镜（镜）", "english": "Short drama shot",
                   "note": "一段就是一镜：写法同剧本，检查另外按一镜的发声窗口数这一段的台词装不装得下"},
    "generic": {"name": "通用台词", "english": "Generic dialogue",
                "note": "两边的交集：口语、有分歧、具体、不用台词交代前情；检查只数书面语、独白、一人独大、具体例子"},
}
# 一版是怎么来的
SOURCES = ("model", "hand", "rewrite", "import")
SOURCE_LABELS = {"model": "模型生成", "hand": "手写另存", "rewrite": "重写几段", "import": "从项目导入"}
# 段种类：借来的判据里有三条（回答没标 / 回答太短 / 回答复述问题）只对**听友问答**成立，得有人告诉工作台
# 哪一段是问答段。不能按台词自己的 kind 猜：对谈里的追问也是问句、「嗯」「对」这种短接话也会被标成回答
# （提示词还专门要求这么写），猜的结果是把一段正常对谈全报成「回答太短」。默认 talk，问答段要显式选。
BLOCK_KINDS = ("talk", "qa")
BLOCK_KIND_LABELS = {"talk": "对谈", "qa": "听友问答"}
# 投放去向（§7）：一份稿子能落到播客的环节、短剧的场次或某一镜
USE_MODULES = ("podcast", "drama_screenplay", "drama_shot")

SPEAKERS_MIN, SPEAKERS_MAX = 1, 6
_VERSION_NUM = re.compile(r"[Vv](\d+)\Z")


class ScriptSpecError(SpecError):
    """剧本工作台的项目文件缺字段或形状不对。"""


# ---------------------------------------------------------------------------- 路径与编号


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
    """SC-20260920-1、SC-20260920-2…（按天编号，跟播客 / 短剧同一套）。"""

    return next_episode_id(existing, prefix=ID_PREFIX, today=today)


def output_root(spec: Mapping[str, Any]) -> Path:
    return Path(str(spec["output_root"]))


def default_block_id(index: int) -> str:
    """第 ``index`` 段（从 1 数）的默认 id：B01、B02…"""

    return f"B{index:02d}"


def default_version_id(index: int) -> str:
    """第 ``index`` 版（从 1 数）的默认 id：V1、V2…"""

    return f"V{index}"


def next_version_id(existing: Iterable[str]) -> str:
    """现有版本 id 里 V 后面数字的最大值 + 1（别的形状忽略，撞了再往后找）。"""

    taken = {str(v) for v in existing}
    highest = 0
    for vid in taken:
        match = _VERSION_NUM.match(vid)
        if match:
            highest = max(highest, int(match.group(1)))
    n = highest + 1
    while default_version_id(n) in taken:
        n += 1
    return default_version_id(n)


# ---------------------------------------------------------------------------- 小校验（形状照 podcast.spec）


def _text(value: Any, field: str, *, required: bool = False, default: str = "") -> str:
    if value is None:
        if required:
            raise ScriptSpecError(f"{field} 不能为空")
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise ScriptSpecError(f"{field} 要是文本")
    out = value.strip()
    if required and not out:
        raise ScriptSpecError(f"{field} 不能为空")
    return out


def _choice(value: Any, field: str, choices: tuple[str, ...], *, default: str | None = None) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            raise ScriptSpecError(f"{field} 不能为空，只能是 {' / '.join(choices)}")
        return default
    text = str(value).strip()
    if text not in choices:
        raise ScriptSpecError(f"{field} 只能是 {' / '.join(choices)}，收到「{text}」")
    return text


def _str_list(raw: Any, field: str) -> list[str]:
    """数组或「顿号 / 逗号 / 换行分隔的一段」都认（跟播客的 brief 一样宽松）。"""

    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        parts: list[Any] = re.split(r"[、,，;；\n]", raw)
    elif isinstance(raw, list):
        parts = [str(v) for v in raw]
    else:
        raise ScriptSpecError(f"{field} 要是数组（或用顿号分隔的一行）")
    return [str(p).strip() for p in parts if str(p).strip()]


def _number(value: Any, field: str) -> float | None:
    """时间戳这类「有就记、没有就算了」的数；不是数就报错，不静默改成 0。"""

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ScriptSpecError(f"{field} 要是数字")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ScriptSpecError(f"{field} 要是数字") from exc


def _brief(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise ScriptSpecError("brief 要是对象（topic / angle / audience / materials / tone / avoid）")
    return {
        "topic": _text(raw.get("topic"), "brief.topic"),
        "angle": _text(raw.get("angle"), "brief.angle"),
        "audience": _text(raw.get("audience"), "brief.audience"),
        "materials": _text(raw.get("materials"), "brief.materials"),
        "tone": _text(raw.get("tone"), "brief.tone"),
        "avoid": _str_list(raw.get("avoid"), "brief.avoid"),
    }


def _speakers(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ScriptSpecError("speakers 要是数组（角色表）")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_names: set[str] = set()
    for index, entry in enumerate(raw):
        field = f"speakers[{index}]"
        if not isinstance(entry, Mapping):
            raise ScriptSpecError(f"{field} 要是对象（id / name / persona / stance）")
        name = _text(entry.get("name"), f"{field}.name", required=True)
        sid = _text(entry.get("id"), f"{field}.id") or f"s{index + 1}"
        if not ID_RE.fullmatch(sid):
            raise ScriptSpecError(f"{field}.id 只能用英文、数字、下划线、点、短横线")
        if sid in seen:
            raise ScriptSpecError(f"角色 id 重复了：{sid}——每个人一个 id，台词的说话人靠它对上")
        # 名字也要查重：台词的 who 可以按名字写（``_lines`` 会换成 id），两个人同名就会静默归到后一个人名下
        if name in seen_names:
            raise ScriptSpecError(f"角色名字重复了：「{name}」——台词按名字写时分不清是谁，给其中一个换个名字")
        seen.add(sid)
        seen_names.add(name)
        out.append({
            "id": sid,
            "name": name,
            "persona": _text(entry.get("persona"), f"{field}.persona"),
            "stance": _text(entry.get("stance"), f"{field}.stance"),
        })
    # 下限不在这里拦：刚建的项目还没填角色是正常的（要角色的那几步调 require_speakers）
    if len(out) > SPEAKERS_MAX:
        raise ScriptSpecError(f"角色表最多 {SPEAKERS_MAX} 人，现在有 {len(out)} 人——人再多观众记不住，也映射不到目标项目")
    return out


def _blocks(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ScriptSpecError("blocks 要是数组（段落骨架）")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, 1):
        field = f"blocks[{index - 1}]"
        if not isinstance(entry, Mapping):
            raise ScriptSpecError(f"{field} 要是对象（id / kind / title / goal / note）")
        bid = _text(entry.get("id"), f"{field}.id") or default_block_id(index)
        if not ID_RE.fullmatch(bid):
            raise ScriptSpecError(f"{field}.id 只能用英文、数字、下划线、点、短横线")
        if bid in seen:
            raise ScriptSpecError(f"段 id 重复了：{bid}——每段一个 id，告警、重写和投放都靠它对上")
        seen.add(bid)
        kind = _text(entry.get("kind"), f"{field}.kind") or "talk"
        if kind not in BLOCK_KINDS:
            raise ScriptSpecError(f"{field}.kind 只能是「{' / '.join(BLOCK_KINDS)}」里的一个，或留空，收到「{kind}」")
        out.append({
            "id": bid,
            "kind": kind,
            "title": _text(entry.get("title"), f"{field}.title"),
            "goal": _text(entry.get("goal"), f"{field}.goal"),
            "note": _text(entry.get("note"), f"{field}.note"),
        })
    return out


def _cue(value: Any, field: str) -> str:
    cue = _text(value, field)
    if cue and cue not in CUES:
        # 清单放进「」里：接口层翻英文时引号内的原文照抄，省得为九个 cue 各写一条规则
        raise ScriptSpecError(f"{field} 只能是「{' / '.join(CUES)}」里的一个，或留空，收到「{cue}」")
    return cue


def _lines(raw: Any, field: str, speaker_ids: set[str], speaker_names: dict[str, str]) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ScriptSpecError(f"{field} 要是数组（台词）")
    out: list[dict[str, Any]] = []
    for i, line in enumerate(raw):
        lf = f"{field}[{i}]"
        if not isinstance(line, Mapping):
            raise ScriptSpecError(f"{lf} 要是对象（who / text / cue / kind）")
        text = _text(line.get("text"), f"{lf}.text")
        if not text:
            continue
        who = _text(line.get("who"), f"{lf}.who", required=True)
        if who not in speaker_ids:
            if who in speaker_names:
                who = speaker_names[who]
            else:
                raise ScriptSpecError(
                    f"{lf} 的说话人「{who}」不在角色表里——从角色表里选一个（用 id 或名字），或者先把这个人加进角色表")
        out.append({
            "who": who,
            "text": text,
            "cue": _cue(line.get("cue"), f"{lf}.cue"),
            "kind": _choice(line.get("kind"), f"{lf}.kind", LINE_KINDS, default="talk"),
        })
    return out


def _version_blocks(raw: Any, field: str, blocks: list[dict[str, Any]], vid: str,
                    speaker_ids: set[str], speaker_names: dict[str, str]) -> list[dict[str, Any]]:
    """一版的台词：按骨架顺序对齐。骨架里没有的段号报错（写错段号 = 这段台词永远显示不出来）。"""

    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ScriptSpecError(f"{field} 要是数组（每段的台词）")
    known = [b["id"] for b in blocks]
    picked: dict[str, list[dict[str, Any]]] = {}
    for index, entry in enumerate(raw, 1):
        bf = f"{field}[{index - 1}]"
        if not isinstance(entry, Mapping):
            raise ScriptSpecError(f"{bf} 要是对象（id / lines）")
        bid = _text(entry.get("id"), f"{bf}.id") or (known[index - 1] if index <= len(known) else default_block_id(index))
        if bid not in known:
            raise ScriptSpecError(f"版本 {vid} 里的段「{bid}」不在段落骨架里——先在骨架里加这一段，或者把它删掉")
        if bid in picked:
            raise ScriptSpecError(f"版本 {vid} 里的段 id 重复了：{bid}——一版里每段只写一次")
        picked[bid] = _lines(entry.get("lines"), f"{bf}.lines", speaker_ids, speaker_names)
    return [{"id": bid, "lines": picked.get(bid, [])} for bid in known]


def _versions(raw: Any, blocks: list[dict[str, Any]], speakers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ScriptSpecError("versions 要是数组（版本列表）")
    speaker_ids = {s["id"] for s in speakers}
    speaker_names = {s["name"]: s["id"] for s in speakers}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, 1):
        field = f"versions[{index - 1}]"
        if not isinstance(entry, Mapping):
            raise ScriptSpecError(f"{field} 要是对象（id / label / source / blocks）")
        vid = _text(entry.get("id"), f"{field}.id") or default_version_id(index)
        if not ID_RE.fullmatch(vid):
            raise ScriptSpecError(f"{field}.id 只能用英文、数字、下划线、点、短横线")
        if vid in seen:
            raise ScriptSpecError(f"版本 id 重复了：{vid}——每一版一个 id，比较、采用和审稿都靠它对上")
        seen.add(vid)
        out.append({
            "id": vid,
            "label": _text(entry.get("label"), f"{field}.label") or f"第 {index} 版",
            "source": _choice(entry.get("source"), f"{field}.source", SOURCES, default="hand"),
            "model": _text(entry.get("model"), f"{field}.model"),
            "backend": _text(entry.get("backend"), f"{field}.backend"),
            "note": _text(entry.get("note"), f"{field}.note"),
            "created_at": _number(entry.get("created_at"), f"{field}.created_at"),
            "from_version": _text(entry.get("from_version"), f"{field}.from_version"),
            "blocks": _version_blocks(entry.get("blocks"), f"{field}.blocks", blocks, vid, speaker_ids, speaker_names),
        })
    known = {v["id"] for v in out}
    for version in out:
        base = version["from_version"]
        if base and base not in known:
            raise ScriptSpecError(f"版本 {version['id']} 说是基于「{base}」改的，但没有这一版——把 from_version 改对或留空")
    return out


def _used_in(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ScriptSpecError("used_in 要是数组（投放记录）")
    out: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        field = f"used_in[{index}]"
        if not isinstance(entry, Mapping):
            raise ScriptSpecError(f"{field} 要是对象（module / id / target / at）")
        out.append({
            "module": _choice(entry.get("module"), f"{field}.module", USE_MODULES),
            "id": _text(entry.get("id"), f"{field}.id", required=True),
            "target": _text(entry.get("target"), f"{field}.target"),
            "at": _text(entry.get("at"), f"{field}.at"),
        })
    return out


# ---------------------------------------------------------------------------- 归一化 / 读写


def normalize(data: Mapping[str, Any], *, project_root: str | os.PathLike[str] | None = None,
              spec_id: str | None = None) -> dict[str, Any]:
    """校验 + 补默认值。``spec_id`` 给了就覆盖 / 补上 id（新建时用）。"""

    if not isinstance(data, Mapping):
        raise ScriptSpecError("项目文件要是一个 JSON 对象")
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else _data_root()
    raw = copy.deepcopy(dict(data))
    identifier = _text(spec_id if spec_id is not None else raw.get("id"), "id")
    if not identifier:
        raise ScriptSpecError("id 不能为空——新建时由服务端编号")
    if not ID_RE.fullmatch(identifier):
        raise ScriptSpecError("id 只能用英文、数字、下划线、点、短横线")
    file_module = _text(raw.get("module"), "module")
    if file_module and file_module != MODULE:
        raise ScriptSpecError(f"项目文件的 module 是「{file_module}」，跟这个模块（{MODULE}）对不上")
    speakers = _speakers(raw.get("speakers"))
    blocks = _blocks(raw.get("blocks"))
    versions = _versions(raw.get("versions"), blocks, speakers)
    current = _text(raw.get("current"), "current")
    if current and current not in {v["id"] for v in versions}:
        raise ScriptSpecError(f"current「{current}」不是这个项目的版本 id——只能采用列在版本里的那几版")
    normalized: dict[str, Any] = {
        "id": identifier,
        "module": MODULE,
        "title": _text(raw.get("title"), "title", required=True),
        "target": _choice(raw.get("target"), "target", tuple(TARGETS), default="generic"),
        "language": _choice(raw.get("language"), "language", LANGUAGES, default="zh"),
        "brief": _brief(raw.get("brief")),
        "speakers": speakers,
        "blocks": blocks,
        "versions": versions,
        "current": current,
        "used_in": _used_in(raw.get("used_in")),
    }
    normalized["output_root"] = str((root / OUTPUT_DIR / identifier).resolve(strict=False))
    normalized["project_root"] = str(root)
    return normalized


def to_disk(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in spec.items() if k not in ("output_root", "project_root")}


def load(spec_id: str, *, project_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve(strict=False) if project_root is not None else _data_root()
    if not ID_RE.fullmatch(str(spec_id or "")):
        raise ScriptSpecError(f"没有这个剧本：{spec_id}")
    path = spec_path(root, spec_id)
    if not path.is_file():
        raise ScriptSpecError(f"没有这个剧本：{spec_id}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ScriptSpecError(f"读不了项目文件 {path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ScriptSpecError(f"项目文件不是合法的 JSON（{path} 第 {exc.lineno} 行）") from exc
    if isinstance(data, Mapping) and str(data.get("id") or "") not in ("", spec_id):
        raise ScriptSpecError(f"项目文件里的 id「{data.get('id')}」跟文件名 {spec_id} 对不上")
    return normalize(data, project_root=root, spec_id=spec_id)


def save(spec: Mapping[str, Any]) -> Path:
    """归一化后的 spec 原子写盘（``to_disk`` 形状）；返回路径。"""

    from storyvideo.state import atomic_write_json

    target = spec_path(Path(str(spec["project_root"])), str(spec["id"]))
    atomic_write_json(target, to_disk(spec))
    return target


def archive(spec_id: str, *, project_root: Path) -> dict[str, Any]:
    """归档到 ``.webui_deleted/script/<ID>__<时间>/``（项目文件 + 审稿记录一起搬，附 manifest），不是 ``os.remove``。

    实现在 ``genkit.trash``：回收站页按它列出、恢复；有文件被占用时抛 ``trash.FilesBusy``，什么都不动。"""

    path = spec_path(project_root, spec_id)
    if not ID_RE.fullmatch(str(spec_id or "")) or not path.is_file():
        raise ScriptSpecError(f"没有这个剧本：{spec_id}")
    return trash.archive(Path(project_root), MODULE, spec_id)


# ---------------------------------------------------------------------------- 只读辅助


def adopt_if_first(raw: dict[str, Any]) -> dict[str, Any]:
    """第一版自动采用：还没采用过任何一版时，刚加的这一版就是当前版（就地改并返回 ``raw``）。

    之后再生成的版本**不会**自动顶替——契约 §3 要求「比过再挑」。但第一版没有可比的对象，不认它的话
    「用」页会一直说「还没挑一版」，还得先回「试」页点一次采用。

    调用方（生成 / 手写另存 / 导入）都是先把新版本追加到 ``versions`` 末尾再调这里，所以采用的是**最后一版**
    而不是 ``versions[0]``：两者只在项目原本一版都没有时相同；``current`` 被整体保存（PUT）清空、版本表里
    已经有旧版时，取第一版会把最老的那版认成当前版——界面刚说完「这一版会直接采用」，结果采用的是另一版。
    """

    versions = [v for v in raw.get("versions") or () if isinstance(v, Mapping) and v.get("id")]
    if versions and not str(raw.get("current") or "").strip():
        raw["current"] = str(versions[-1]["id"])
    return raw


def speaker_by_id(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(s["id"]): s for s in spec.get("speakers") or () if isinstance(s, Mapping) and s.get("id")}


def speaker_names(spec: Mapping[str, Any]) -> dict[str, str]:
    """``{id: 名字}``，告警和提示词里把 id 换成人能读的名字。"""

    return {str(s["id"]): str(s.get("name") or s["id"]) for s in spec.get("speakers") or ()
            if isinstance(s, Mapping) and s.get("id")}


def block_by_id(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(b["id"]): b for b in spec.get("blocks") or () if isinstance(b, Mapping) and b.get("id")}


def version_by_id(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(v["id"]): v for v in spec.get("versions") or () if isinstance(v, Mapping) and v.get("id")}


def current_version(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    """采用的那一版；没采用 / 没有版本 = None。"""

    return version_by_id(spec).get(str(spec.get("current") or ""))


def pick_version(spec: Mapping[str, Any], version: str | None = None) -> dict[str, Any] | None:
    """``version`` 给了就找那一版（找不到报错），不给就是 ``current``；**还没采用就看最新一版**；一版也没有时 None。

    第一版会自动采用（``adopt_if_first``），但 ``current`` 仍可能是空的（整体保存时清掉了）：检查、估算、
    审稿这时要是当「没有稿子」，界面上就是零告警、0 分钟、没法审。要「到底采用了没有」问 ``current_version``。
    """

    if version:
        known = version_by_id(spec)
        found = known.get(str(version))
        if found is None:
            # 一版也没有时不能说「版本列表里只有 空」：那个「空」是正文里的中文，英文界面翻不掉（引号外不许剩中文）
            if not known:
                raise ScriptSpecError(f"没有这一版：{version}——这个剧本还没有任何版本")
            raise ScriptSpecError(f"没有这一版：{version}——版本列表里只有 {' / '.join(known)}")
        return found
    picked = current_version(spec)
    if picked is not None:
        return picked
    versions = [v for v in spec.get("versions") or () if isinstance(v, Mapping) and v.get("id")]
    return versions[-1] if versions else None


def require_speakers(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """要角色的那几步（起草 / 审稿 / 投放）调它：角色表空着就报错。

    契约 §2 写的是「speakers 1–6 人」，上限在归一化拦，下限拦在这里——新建的空项目要能存盘（界面上先建再填），
    所以归一化那一层不能要求至少一个人。措辞照播客「主播表是空的——…」那条。
    """

    speakers = [s for s in spec.get("speakers") or () if isinstance(s, Mapping) and s.get("id")]
    if len(speakers) < SPEAKERS_MIN:
        raise ScriptSpecError("角色表是空的——先加至少一个人（名字 + 人设），台词才知道谁在说")
    return speakers


def all_lines(spec: Mapping[str, Any], version: str | None = None) -> list[tuple[dict[str, Any], int, dict[str, Any]]]:
    """``[(段, 序号从 1, 台词)]``，按段按句。段是**骨架**里那一条（带标题 / 目标 / 备注），不是版本里只有 id 的那条。"""

    picked = pick_version(spec, version)
    if picked is None:
        return []
    known = block_by_id(spec)
    out: list[tuple[dict[str, Any], int, dict[str, Any]]] = []
    for entry in picked.get("blocks") or ():
        block = known.get(str(entry.get("id")), {"id": str(entry.get("id")), "title": "", "goal": "", "note": ""})
        for index, line in enumerate(entry.get("lines") or (), 1):
            out.append((block, index, line))
    return out


def targets_payload() -> list[dict[str, Any]]:
    """``/api/script/options`` 用：四种目标形态 + 一句话说明。"""

    return [{"id": tid, "name": t["name"], "english": t["english"], "note": t["note"]} for tid, t in TARGETS.items()]


__all__ = [
    "MODULE", "ID_PREFIX", "CONTENT_DIR", "OUTPUT_DIR", "LANGUAGES", "TARGETS", "SOURCES", "SOURCE_LABELS", "BLOCK_KINDS", "BLOCK_KIND_LABELS",
    "USE_MODULES", "CUES", "LINE_KINDS", "SPEAKERS_MIN", "SPEAKERS_MAX", "ScriptSpecError", "content_dir", "spec_path",
    "existing_ids", "next_id", "output_root", "default_block_id", "default_version_id", "next_version_id",
    "normalize", "to_disk", "load", "save", "archive", "adopt_if_first", "speaker_by_id", "speaker_names", "require_speakers",
    "block_by_id", "version_by_id", "current_version", "pick_version", "all_lines", "targets_payload",
]
