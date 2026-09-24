"""剧本层的结构与静态检查（DESIGN §32）：两步起草第一步产出的 ``<id>.screenplay.json``，
在界面上是一个逐场编辑器，不再是一个裸 JSON 编辑框。

一份剧本长这样（人物表是列表，场次按时间顺序）：

    {
      "title": "厨房关停",
      "logline": "一到三句话的故事梗概",
      "contract": {"premise": …, "hook": …, "cliffhanger": …, "must_pay_off": [...], "carry_over": [...]},
      "cast":   [{"id": "ye", "name": "叶澄", "block": "三十岁，黑色短发"}, …],
      "scenes": [{"id": "S01", "heading": "内 · 共享厨房 · 清晨", "place": "共享厨房", "time": "清晨",
                  "beats": ["齐曼把订单拍在桌上"],
                  "dialogue": [{"who": "qi", "zh": "把完整的排班记录拿出来。"}],
                  "note": "给拆镜的备注"}, …]
    }

``normalize_screenplay`` 是保存前的硬校验（跟 spec 一样抛 ``SpecError``）：场次 id 缺了按 ``S01…`` 补、
不许重复；场景标题不能空；说话人必须是人物表里的 id 或名字（写错名字等于这句台词没人说，拆镜时会静默丢
掉）；节拍每条都要是有内容的一句话。``role``/``look``/``change``/``cue`` 这些老字段原样留着——剧本文件早就
在磁盘上了，归一化不许把人写过的东西吃掉。

``lint_screenplay`` 是告警不是门禁（跟 ``lint.py`` 一样是「scene S02: 影响——改法」一句话，接口层
``problems.py`` 再拆成问题卡）。判据取剧本层自己的：每一场要真的发生点什么、场景标题要能看出在哪什么时候、
契约的开场钩子要在第一场看得见、集尾压力要落在最后一场、人物表里的人要真的出场、一集别写成长片。
它对没归一化过的原始文件也成立（模型刚吐出来的剧本可能什么形状都有），任何一处形状不对只是跳过那条判据。
"""

from __future__ import annotations

from typing import Any, Mapping

from .contract import CONTRACT_KEYS, CONTRACT_LIST_KEYS, CONTRACT_TEXT_KEYS, ContractError, mentioned, normalize_contract
from .spec import NARRATOR_SPEAKERS, SpecError

# 竖屏短剧一集的场次上限（软提醒）：再多就是每场都来不及改变什么，观众只看到一串地点
MAX_SCENES = 12
# 一句台词的字数上限与开口人数上限（剧本工艺清单的剧本层判据，docs/script-craft-gap.md）：都是软提醒，数字是经验值
LINE_MAX_CHARS = 25
SPEAKING_CAST_MAX = 3
# 说话人可以不是人物表里的人：旁白/画外音本来就没有角色条目。与 spec 层的说话人校验共用一份名字
NARRATORS = NARRATOR_SPEAKERS
# 场景标题里的地点标记（内/外景）与时间标记：写全了拆镜才知道这一场在哪、什么时候
PLACE_MARKERS = ("内", "外", "int", "ext")
TIME_MARKERS = ("日", "夜", "晨", "晚", "午", "昏", "白天", "凌晨", "傍晚", "黄昏", "清晨", "深夜",
                "day", "night", "morning", "evening", "afternoon", "dusk", "dawn")
# 人物表 / 场次里除 id、name、heading 之外照抄的字段（老剧本文件里就有，别在归一化时吃掉）
CAST_EXTRA_KEYS = ("block", "role", "look")
SCENE_EXTRA_KEYS = ("place", "time", "note", "change")
LINE_EXTRA_KEYS = ("en", "cue")


def _text(value: Any, label: str) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value).strip()
    if not isinstance(value, str):
        # 字段名放进「」：接口层翻英文时引号内的原文照抄，不用为每个字段各写一条规则
        raise SpecError(f"「{label}」要是一段文字")
    return value.strip()


def default_scene_id(index: int) -> str:
    """第 ``index`` 场（从 1 数）的默认 id：S01、S02…"""

    return f"S{index:02d}"


def _normalize_contract(raw: Any) -> dict[str, Any]:
    """契约跟 spec 的 ``episode_contract`` 同一套形状（复用 ``contract.normalize_contract``），
    但剧本里五个键永远都在——编辑器要有稳定的输入框，不能因为某一项是空的就整块消失。"""

    try:
        cleaned = normalize_contract(raw) if raw not in (None, "") else None
    except ContractError as exc:
        raise SpecError(str(exc).replace("episode_contract", "contract")) from exc
    out: dict[str, Any] = {k: "" for k in CONTRACT_TEXT_KEYS}
    out.update({k: [] for k in CONTRACT_LIST_KEYS})
    if cleaned:
        out.update({k: cleaned.get(k) for k in CONTRACT_KEYS})
    return out


def _normalize_cast(raw: Any) -> list[dict[str, Any]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise SpecError("人物表（cast）要是列表，每个角色一条")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, member in enumerate(raw, 1):
        if not isinstance(member, Mapping):
            raise SpecError(f"人物表第 {i} 条要是对象（id / name / block）")
        cid = _text(member.get("id"), f"人物表第 {i} 条的 id")
        name = _text(member.get("name"), f"人物表第 {i} 条的名字")
        if not cid and not name:
            continue    # 界面上刚加出来还没填的空行：丢掉，不当错误
        if not cid:
            raise SpecError(f"人物表里「{name}」没有 id——拆镜时角色靠 id 对上，给它一个简短的英文 id")
        if cid in seen:
            raise SpecError(f"人物表里 id 重复了：{cid}——每个角色一个 id，台词才知道是谁说的")
        seen.add(cid)
        entry: dict[str, Any] = {"id": cid, "name": name or cid}
        for key in CAST_EXTRA_KEYS:
            value = _text(member.get(key), f"人物表「{cid}」的 {key}")
            if value:
                entry[key] = value
        out.append(entry)
    return out


def _speaker_ok(who: str, cast: list[dict[str, Any]]) -> bool:
    if who.lower() in NARRATORS:
        return True
    return any(who == m["id"] or who == m.get("name") for m in cast)


def _normalize_dialogue(raw: Any, sid: str, cast: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise SpecError(f"场 {sid} 的对白要是列表，每句一条")
    out: list[dict[str, Any]] = []
    for i, line in enumerate(raw, 1):
        if not isinstance(line, Mapping):
            raise SpecError(f"场 {sid} 的第 {i} 句台词要是对象（who / zh）")
        who = _text(line.get("who"), f"场 {sid} 第 {i} 句台词的说话人")
        zh = _text(line.get("zh"), f"场 {sid} 第 {i} 句台词")
        if not zh and not who:
            continue    # 界面上刚加出来还没填的空行
        if not zh:
            raise SpecError(f"场 {sid} 的第 {i} 句台词是空的——填上台词，或者删掉这一行")
        if not who:
            raise SpecError(f"场 {sid} 的第 {i} 句台词没写谁说的——从人物表里选一个人，旁白写 narrator")
        if not _speaker_ok(who, cast):
            raise SpecError(
                f"场 {sid} 的第 {i} 句台词的说话人「{who}」不在人物表里——"
                "从人物表里选一个（用 id 或角色名），或者先把这个人加进人物表"
            )
        entry: dict[str, Any] = {"who": who, "zh": zh}
        for key in LINE_EXTRA_KEYS:
            value = _text(line.get(key), f"场 {sid} 第 {i} 句台词的 {key}")
            if value:
                entry[key] = value
        out.append(entry)
    return out


def _normalize_beats(raw: Any, sid: str) -> list[str]:
    if raw in (None, ""):
        return []
    items = raw.splitlines() if isinstance(raw, str) else raw
    if not isinstance(items, list):
        raise SpecError(f"场 {sid} 的节拍要是列表，每条一件事")
    out: list[str] = []
    for i, beat in enumerate(items, 1):
        if beat is None or (isinstance(beat, str) and not beat.strip()):
            continue    # 界面上刚加出来还没填的空行
        if not isinstance(beat, str):
            raise SpecError(f"场 {sid} 的第 {i} 条节拍要是一句话（谁做了什么）")
        out.append(beat.strip())
    return out


def normalize_screenplay(doc: Any) -> dict[str, Any]:
    """保存前的硬校验 + 补默认值。形状不对抛 ``SpecError``（措辞是给操作者看的人话）。"""

    if not isinstance(doc, Mapping):
        raise SpecError("剧本要是对象（title / logline / contract / cast / scenes）")
    scenes_raw = doc.get("scenes")
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise SpecError("剧本至少要有一场（scenes 列表不能为空）")
    cast = _normalize_cast(doc.get("cast"))
    scenes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, raw in enumerate(scenes_raw, 1):
        if not isinstance(raw, Mapping):
            raise SpecError(f"第 {i} 场要是对象（id / heading / beats / dialogue）")
        sid = _text(raw.get("id"), f"第 {i} 场的 id") or default_scene_id(i)
        if sid in seen:
            raise SpecError(f"场次 id 重复了：{sid}——每一场一个 id，告警和拆镜都靠它对上")
        seen.add(sid)
        heading = _text(raw.get("heading"), f"场 {sid} 的场景标题")
        if not heading:
            raise SpecError(f"场 {sid} 没有场景标题——按「内/外 · 地点 · 时间」写一行")
        scene: dict[str, Any] = {"id": sid, "heading": heading}
        for key in SCENE_EXTRA_KEYS:
            value = _text(raw.get(key), f"场 {sid} 的 {key}")
            if value:
                scene[key] = value
        scene["beats"] = _normalize_beats(raw.get("beats"), sid)
        scene["dialogue"] = _normalize_dialogue(raw.get("dialogue"), sid, cast)
        scenes.append(scene)
    return {
        "title": _text(doc.get("title"), "剧名"),
        "logline": _text(doc.get("logline"), "梗概"),
        "contract": _normalize_contract(doc.get("contract")),
        "cast": cast,
        "scenes": scenes,
    }


# ---- 静态检查：对没归一化过的原始剧本也成立（形状不对就跳过那条判据）

def _scenes(doc: Any) -> list[Mapping[str, Any]]:
    if not isinstance(doc, Mapping):
        return []
    return [s for s in (doc.get("scenes") or ()) if isinstance(s, Mapping)]


def _cast(doc: Any) -> list[Mapping[str, Any]]:
    if not isinstance(doc, Mapping):
        return []
    return [m for m in (doc.get("cast") or ()) if isinstance(m, Mapping) and str(m.get("id") or "").strip()]


def _beats(scene: Mapping[str, Any]) -> list[str]:
    raw = scene.get("beats")
    items = raw.splitlines() if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    return [b.strip() for b in items if isinstance(b, str) and b.strip()]


def _lines(scene: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = scene.get("dialogue")
    return [l for l in raw if isinstance(l, Mapping)] if isinstance(raw, list) else []


def scene_id(scene: Mapping[str, Any], index: int) -> str:
    return str(scene.get("id") or "").strip() or default_scene_id(index)


def scene_text(scene: Mapping[str, Any]) -> str:
    """一场的可核对正文：标题、节拍、台词（中英）、备注与「这一场改变了什么」。"""

    parts = [str(scene.get(k) or "") for k in ("heading", "place", "time", "note", "change")]
    parts += _beats(scene)
    for line in _lines(scene):
        parts += [str(line.get(k) or "") for k in ("zh", "en")]
    return " ".join(p for p in parts if p)


def _has_marker(scene: Mapping[str, Any], field: str, markers: tuple[str, ...]) -> bool:
    if str(scene.get(field) or "").strip():
        return True
    heading = str(scene.get("heading") or "").lower()
    return any(m in heading for m in markers)


def check_scene_content(doc: Any) -> list[str]:
    """一场既没有节拍也没有对白 = 这一场什么都没发生。"""

    out: list[str] = []
    for i, scene in enumerate(_scenes(doc), 1):
        if not _beats(scene) and not _lines(scene):
            out.append(
                f"scene {scene_id(scene, i)}: 这一场既没有节拍也没有对白——"
                "写出这一场发生了什么（谁做了什么），或者删掉这一场"
            )
    return out


def check_headings(doc: Any) -> list[str]:
    """场景标题要能看出在哪、什么时候：拆镜时的地点/光线全靠它。"""

    out: list[str] = []
    for i, scene in enumerate(_scenes(doc), 1):
        heading = str(scene.get("heading") or "").strip()
        if not heading:
            continue
        missing = []
        if not _has_marker(scene, "place", PLACE_MARKERS):
            missing.append("地点")
        if not _has_marker(scene, "time", TIME_MARKERS):
            missing.append("时间")
        if missing:
            out.append(
                f"scene {scene_id(scene, i)}: 场景标题「{heading}」没写{'、'.join(missing)}——"
                "按「内/外 · 地点 · 时间」写全，拆镜时才知道这一场在哪、什么光线"
            )
    return out


def check_contract_echo(doc: Any) -> list[str]:
    """开场钩子要在第一场看得见、集尾压力要落在最后一场（判据跟 ``contract.mentioned`` 同一套）。"""

    scenes = _scenes(doc)
    contract = doc.get("contract") if isinstance(doc, Mapping) else None
    if not scenes or not isinstance(contract, Mapping):
        return []
    out: list[str] = []
    hook = str(contract.get("hook") or "").strip()
    if hook and not mentioned(hook, scene_text(scenes[0])):
        out.append(
            f"全集：开场钩子「{hook}」在第一场看不见——"
            "把它写进第一场的节拍或对白，观众开头就要看见这个未稳定的局面"
        )
    cliff = str(contract.get("cliffhanger") or "").strip()
    if cliff and not mentioned(cliff, scene_text(scenes[-1])):
        out.append(
            f"全集：集尾压力「{cliff}」没有落在最后一场——"
            "最后一场要写出某人已经决定做什么、或者某个后果已经启动"
        )
    return out


def check_cast_usage(doc: Any) -> list[str]:
    """人物表里的人一句台词没有、节拍里也没点名 = 这个人其实不在本集里。"""

    scenes = _scenes(doc)
    blob = " ".join(" ".join(_beats(s)) for s in scenes)
    speakers = {str(l.get("who") or "").strip() for s in scenes for l in _lines(s)}
    out: list[str] = []
    for member in _cast(doc):
        cid = str(member["id"]).strip()
        name = str(member.get("name") or "").strip()
        if cid in speakers or (name and name in speakers):
            continue
        if (name and name in blob) or (cid and cid in blob):
            continue
        out.append(
            f"cast {cid}: 「{name or cid}」在剧本里一句台词也没有，节拍里也没点名——"
            "给他一场真的要做点什么的戏，或者从人物表里删掉"
        )
    return out


def check_scene_count(doc: Any) -> list[str]:
    """一集场次太多：软提醒，不是门禁。"""

    n = len(_scenes(doc))
    if n <= MAX_SCENES:
        return []
    return [
        f"全集：一集写了 {n} 场——竖屏短剧一集通常不超过 {MAX_SCENES} 场，"
        "场次太碎每一场都来不及改变什么；把同地点同时间的场次合并"
    ]


def _speaker_name(who: str, cast: list[Mapping[str, Any]]) -> str:
    for member in cast:
        if str(member.get("id") or "").strip() == who:
            return str(member.get("name") or who).strip() or who
    return who


def check_line_length(doc: Any) -> list[str]:
    """一句台词太长（超过 ``LINE_MAX_CHARS`` 字，docs/script-craft-gap.md）：一口气说不完，拆镜时要占满一整镜。"""

    out: list[str] = []
    cast = _cast(doc)
    for i, scene in enumerate(_scenes(doc), 1):
        for line in _lines(scene):
            text = str(line.get("zh") or "").strip()
            if len(text) > LINE_MAX_CHARS:
                who = _speaker_name(str(line.get("who") or "").strip(), cast)
                out.append(
                    f"scene {scene_id(scene, i)}: 「{who}」的一句台词 {len(text)} 字——一口气说不完，拆镜时要占满一整镜；"
                    "拆成两句，或把说明性的半句改成动作"
                )
    return out


def check_speaking_cast(doc: Any) -> list[str]:
    """开口的人太多（超过 ``SPEAKING_CAST_MAX`` 个，竖屏一集观众记不住更多张脸）：软提示，旁白不算。"""

    cast = _cast(doc)
    speakers: list[str] = []
    for scene in _scenes(doc):
        for line in _lines(scene):
            who = str(line.get("who") or "").strip()
            if not who or who.lower() in NARRATORS:
                continue
            name = _speaker_name(who, cast)
            if name not in speakers:
                speakers.append(name)
    if len(speakers) <= SPEAKING_CAST_MAX:
        return []
    return [
        f"全集：剧本里有 {len(speakers)} 个人物开口说话（{'、'.join(speakers)}）——竖屏一集观众只记得住三张脸，模型也更容易把人认混；"
        "把只说一两句的人改成不开口的在场者，或合并成一个人"
    ]


def lint_screenplay(doc: Any) -> list[str]:
    """剧本的全部告警（顺序固定：逐场的在前，整集的在后）。"""

    return (
        check_scene_content(doc)
        + check_headings(doc)
        + check_line_length(doc)
        + check_cast_usage(doc)
        + check_contract_echo(doc)
        + check_scene_count(doc)
        + check_speaking_cast(doc)
    )


def screenplay_stats(doc: Any) -> dict[str, int]:
    """界面顶部的三个数：场次、台词句数、正文字数（节拍 + 台词，按字符数粗算）。"""

    scenes = _scenes(doc)
    lines = [l for s in scenes for l in _lines(s)]
    words = sum(len(b) for s in scenes for b in _beats(s))
    words += sum(len(str(l.get("zh") or "").strip()) for l in lines)
    return {"scenes": len(scenes), "dialogue_lines": len(lines), "words": words}


__all__ = [
    "MAX_SCENES",
    "default_scene_id",
    "normalize_screenplay",
    "lint_screenplay",
    "screenplay_stats",
    "scene_text",
    "check_scene_content",
    "check_headings",
    "check_contract_echo",
    "check_cast_usage",
    "check_scene_count",
    "check_line_length",
    "check_speaking_cast",
    "LINE_MAX_CHARS",
    "SPEAKING_CAST_MAX",
]
