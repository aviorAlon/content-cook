"""投放与导入（契约 docs/scriptlab-spec.md §7）——工作台存在的理由：调好了的台词要能用出去。

**投放**：把采用的那一版里某几段的台词，角色对角色地写进目标项目的落点——

* ``podcast``：写进那期的 ``segments[].lines``（``{who, text, cue, kind}``，工作台的台词形状本来就是播客那套）；
  落点是环节 id，``"new"`` 就新建一个环节（``kind="topic"``、标题取段标题）。整份过 ``podcast.spec.normalize``
  再 ``save``，不绕过。
* ``drama_screenplay``：写进 ``<episode>.screenplay.json`` 的 ``scenes[].dialogue``（``{who, zh}``），
  落点是场次 id，``"new"`` 新建一场（场景标题取段标题——剧本层要求标题非空，段没标题就先拒绝，不替人编一个）。
  整份过 ``storyvideo.screenplay.normalize_screenplay`` 再落盘。
* ``drama_shot``：写进那一镜的 ``shot.lines``（``{who, zh}``），落点是镜号，**没有「新建」**（镜次是拆镜的产物，
  不在这里凭空加）。**剧集已锁定（prepare 过）→ 400**：台词会进提示词，锁定后原地改等于让已经生成的产物和
  提示词对不上。整份过 ``storyvideo.spec.normalize_spec`` 校验再落盘。

三条共同的规矩：

* **写回一律走目标模块自己的归一化**：目标项目的校验说不行就不写盘，把它那句人话原样带回来（去掉里层前缀，
  不套两层）。工作台不复制一份校验，也不用「我这边看着没问题」绕过去。
* **角色映射缺一个就拒绝**：这次真要投出去的段里开口的人，每一个都要对上目标项目里的一个角色 / 主播。
  没开口的人不要求映射（界面上要求填满，那是界面的事；后端只拦真会写坏的那种）。
* **这一版没台词的段不投**：选了落点但这一版这一段还是空的，跳过它（返回体里的 ``skipped``），
  绝不拿空台词去 ``replace`` 掉目标那边原有的台词——那边没有版本留痕，删掉就找不回来。
* **落点不存在 404**，目标项目不存在 404，其余（映射缺失、段号写错、锁定、目标不接受）都是 400。

**导入**是反向：把某期播客的环节 / 某集剧本的场次 / 某一镜的现有台词拿过来当新的一版（``source="import"``），
同时把那边**这次真开口的**角色 / 主播补进 ``speakers``（id 与名字照抄，人设留空；撞号规则见 ``import_script``）
——「现有的稿子不好，拿过来调」。项目还没采用任何一版时导进来的这一版自动采用（``spec.adopt_if_first``，契约 §3）。
段落骨架里没有的落点会按落点 id 补一段（标题与段种类照抄），不然这一版的段对不上骨架、界面上根本显示不出来。

拷贝而不是引用：投放出去的台词是目标项目自己的文字，之后工作台再改也不会偷偷改掉别人的稿子；反过来也一样。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from genkit.ids import ID_RE

from . import spec as S

# 投放时整段台词怎么落：默认整段替换，追加就接在原有台词后面
USE_MODES = ("replace", "append")
DEFAULT_MODE = "replace"
NEW_SPOT = "new"
# 落点在各模块里叫什么（报错与成功提示都按它说话，别让人猜「spot」是什么）
SPOT_LABELS = {"podcast": "环节", "drama_screenplay": "场次", "drama_shot": "镜号"}
# 剧集锁定后不许原地改台词（契约 §7 的原话）
LOCKED_MESSAGE = "这一集已经开始制作，台词是生成字段——去项目里走改写重出，或复制一个新 revision"
NO_LINES_MESSAGE = "这一版还没有台词——先生成或手写几句，再投放"
NOTHING_TO_IMPORT = "这个项目里还没有台词可导——先在那边写几句，再来导入"
# 导入时一个落点：``(落点 id, 标题, 段种类, 台词)``。段种类只有播客的听友问答环节会是 ``qa``（补段时照抄给骨架，
# 检查那边的问答判据才认得出来）；短剧的场与镜没有这个概念，一律 ``talk``
_Spot = tuple[str, str, str, list[dict[str, Any]]]


class ScriptUseError(RuntimeError):
    """投放 / 导入失败（映射缺失、落点写错、目标不接受这次改动）。接口层翻成 400。"""


class UnknownTarget(ScriptUseError):
    """目标项目或落点不存在。接口层翻成 404。"""


class TargetLocked(ScriptUseError):
    """剧集已经开始制作，台词不能原地改（契约 §7：这一条是 400 不是 409——它不是「有人正在改」，
    而是「这条路走不通，换一条」）。"""


# ---------------------------------------------------------------------------- 目标清单


def _story_dir() -> Path:
    """短剧的内容目录从 ``webui.deps`` 取（测试会 monkeypatch 它，跟配乐投放同一个取法）。"""

    from webui import deps

    return deps.STORYVIDEO_DIR


def _read(path: Path) -> dict[str, Any]:
    """只读一眼，不做硬校验——一个坏文件不该让投放页的下拉框整个空掉。"""

    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _peek(data: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def _spot_rows(entries: Any, lines_key: str,
               title_keys: tuple[str, ...] = ("title", "heading")) -> list[dict[str, Any]]:
    """落点行 ``{id, title, lines}``：``lines`` 是那里已经有几句（界面上按所选方式提示「已有 N 句，会被整段替换 / 会追加在后面」）。

    ``title_keys`` 按目标那边的字段名给：环节 / 场次是 ``title`` / ``heading``，**镜次两个都没有**——
    景别那句在 ``shot`` 字段里（导入路径 ``_import_from_shots`` 取的就是它），不把它算进来的话投放页的
    镜号下拉只剩裸镜号，选不出「哪一镜是近景」。
    """

    out: list[dict[str, Any]] = []
    for index, entry in enumerate(entries if isinstance(entries, list) else (), 1):
        if not isinstance(entry, Mapping):
            continue
        sid = str(entry.get("id") or "").strip()
        if not sid:
            continue
        lines = entry.get(lines_key)
        out.append({"id": sid, "title": _peek(entry, title_keys),
                    "lines": len(lines) if isinstance(lines, list) else 0})
    return out


def _roles(entries: Any) -> list[dict[str, str]]:
    """目标项目的角色 / 主播 ``[{id, name}]``：短剧的 ``cast`` 是字典，播客的 ``hosts`` 是数组，两种都认。

    短剧的 ``cast`` 里人物、地点、道具混在一张表里（只有 block 没有 name 的是场景 / 道具，也可以显式写 ``kind``），
    判据用全仓库共用的 ``cast_kind``，这里只留人物：地点要是也列进映射候选，投放页会按顺序把说话人自动配到它头上，
    后端再照写 ``who: office``——地点名就进了付费出片提示词，配音又找不到它的音色（角色页对场景条目不显示音色）。
    """

    out: list[dict[str, str]] = []
    if isinstance(entries, Mapping):
        from webui.routers.project import cast_kind

        for rid, member in entries.items():
            if isinstance(member, Mapping) and cast_kind(member) != "character":
                continue
            name = str((member or {}).get("name") or rid) if isinstance(member, Mapping) else str(rid)
            out.append({"id": str(rid), "name": name})
    elif isinstance(entries, list):
        for member in entries:
            if isinstance(member, Mapping) and str(member.get("id") or "").strip():
                out.append({"id": str(member["id"]).strip(), "name": str(member.get("name") or member["id"])})
    return out


def episode_locked(project_root: Path, raw: Mapping[str, Any], episode_id: str) -> bool:
    """这一集 prepare 过了吗（``deps.is_locked`` 那条判断）。项目文件里的 ``output_root`` 可能是相对路径，
    先按项目根补全，不然会跑到进程的工作目录底下去找。"""

    from webui import deps

    out = str(raw.get("output_root") or "").strip() or f"output/story_video/{episode_id}"
    path = Path(out)
    if not path.is_absolute():
        path = Path(project_root) / out
    return deps.is_locked(str(path))


def list_targets(project_root: Path) -> dict[str, list[dict[str, Any]]]:
    """可投放 / 可导入的项目（契约 §8 的 ``/targets``）：

    * ``podcast``：每期带 ``segments``（环节 id / 标题 / 已有几句）与 ``hosts``；
    * ``drama``：每集带 ``locked``、``scenes``（来自 ``<id>.screenplay.json``，没有剧本就是空表）、
      ``shots``（来自项目文件）与 ``cast``。

    只读，读不出来的文件按 id 当标题列出来，不跳过。
    """

    from podcast import spec as PC

    from webui import deps

    podcasts: list[dict[str, Any]] = []
    podcast_dir = PC.content_dir(Path(project_root))
    if podcast_dir.is_dir():
        for path in sorted(podcast_dir.glob("*.json")):
            data = _read(path)
            podcasts.append({
                "id": path.stem, "title": _peek(data, ("title",)) or path.stem,
                "segments": _spot_rows(data.get("segments"), "lines"), "hosts": _roles(data.get("hosts")),
            })
    drama: list[dict[str, Any]] = []
    story_dir = _story_dir()
    if story_dir.is_dir():
        for path in sorted(story_dir.glob("*.json")):
            if deps.is_screenplay_file(path):
                continue
            data = _read(path)
            screenplay = _read(deps.screenplay_path(path.stem))
            drama.append({
                "id": path.stem, "title": _peek(data, ("title_zh", "title", "title_en")) or path.stem,
                "locked": episode_locked(Path(project_root), data, path.stem),
                "scenes": _spot_rows(screenplay.get("scenes"), "dialogue"),
                "shots": _spot_rows(data.get("shots"), "lines", ("title", "heading", "shot")),
                "cast": _roles(data.get("cast")),
            })
    return {"podcast": podcasts, "drama": drama}


def target_title(project_root: Path, module: str, target_id: str) -> str:
    source = "podcast" if module == "podcast" else "drama"
    for row in list_targets(project_root).get(source) or ():
        if row["id"] == target_id:
            return str(row["title"])
    return target_id


# ---------------------------------------------------------------------------- 共用的入参检查


def _module(module: Any) -> str:
    text = str(module or "").strip()
    if text not in S.USE_MODULES:
        raise ScriptUseError(f"module 只能是 {' / '.join(S.USE_MODULES)}，收到「{text}」")
    return text


def _target_id(target_id: Any) -> str:
    text = str(target_id or "").strip()
    if not text:
        raise ScriptUseError("target_id 不能为空——先选一个目标项目")
    if not ID_RE.fullmatch(text):
        raise UnknownTarget(f"没有这个项目：{text}")
    return text


def _mode(mode: Any) -> str:
    if mode is None or (isinstance(mode, str) and not mode.strip()):
        return DEFAULT_MODE
    text = str(mode).strip()
    if text not in USE_MODES:
        raise ScriptUseError(f"mode 只能是 replace（整段替换）/ append（追加在后面），收到「{text}」")
    return text


def _plan(spec: Mapping[str, Any], blocks: Any,
          version: str | None) -> tuple[list[tuple[dict[str, Any], str, list[dict[str, Any]]]], list[str]]:
    """``{段 id: 落点}`` → ``([(骨架段, 落点, 这一版这一段的台词)], 跳过的段号)``，按骨架顺序。

    两种段不进投放计划：

    * **空落点**（前端「先不投这一段」）——人明确说了这段不投；
    * **这一版这一段还没有台词**——前端的「用」页列的是整份段落骨架，没写台词的段照样能选落点。
      空台词进计划的话，``mode="replace"`` 会把目标那个环节 / 场次 / 镜里原有的台词整段删掉，
      接口还回 200 说「已放进…」；播客与短剧那边没有版本留痕，删掉就找不回来，正好违反这个模块
      「从不覆盖」的立身之本。所以跳过，并把段号带回给调用方（返回体里的 ``skipped``，界面上说明哪几段没投）。

    段号不在骨架里就拒绝——那多半是前端传错了，静默丢掉的话人会以为投了。

    **先数落点再取版本**：一个落点都没选、同时项目又还没有任何版本时，毛病是「没选落点」而不是「还没有台词」，
    先取版本的话报的是后一句，把人引到「去生成一版」那条错路上。
    """

    if not isinstance(blocks, Mapping):
        raise ScriptUseError("blocks 要是对象——每段一个落点，键是段 id、值是落点 id")
    known = S.block_by_id(spec)
    unknown = [str(b) for b in blocks if str(b) not in known]
    if unknown:
        raise ScriptUseError(f"投放的段不在段落骨架里：「{'、'.join(unknown)}」——从骨架里选")
    wanted: list[tuple[dict[str, Any], str]] = []
    for bid, block in known.items():
        spot = str(blocks.get(bid) or "").strip()
        if spot:
            wanted.append((block, spot))
    if not wanted:
        raise ScriptUseError("没有要投放的段——至少给一段选一个落点")
    picked = S.pick_version(spec, version)
    if picked is None:
        raise ScriptUseError(NO_LINES_MESSAGE)
    lines_by_block = {str(b.get("id")): list(b.get("lines") or []) for b in picked.get("blocks") or ()
                      if isinstance(b, Mapping)}
    plan: list[tuple[dict[str, Any], str, list[dict[str, Any]]]] = []
    skipped: list[str] = []
    for block, spot in wanted:
        lines = lines_by_block.get(str(block["id"]), [])
        if not lines:
            skipped.append(str(block["id"]))
            continue
        plan.append((block, spot, lines))
    if not plan:
        raise ScriptUseError(NO_LINES_MESSAGE)
    return plan, skipped


def _mapping(spec: Mapping[str, Any], mapping: Any,
             plan: list[tuple[dict[str, Any], str, list[dict[str, Any]]]],
             roles: list[dict[str, str]]) -> dict[str, str]:
    """``{说话人 id: 目标角色 id}``：这次真要投出去的段里**开口的人**都要有映射，且映射到的角色要真的存在。

    没开口的人不强制（界面上要求填满是界面的事）——后端只拦会把台词写成「没人说」的那种。
    """

    raw = mapping if isinstance(mapping, Mapping) else {}
    names = S.speaker_names(spec)
    known_roles = {str(r["id"]) for r in roles}
    speaking: list[str] = []
    for _block, _spot, lines in plan:
        for line in lines:
            who = str(line.get("who") or "")
            if who and who not in speaking:
                speaking.append(who)
    out: dict[str, str] = {}
    missing: list[str] = []
    stray: list[str] = []
    for who in speaking:
        target = str(raw.get(who) or "").strip()
        if not target:
            missing.append(names.get(who, who))
            continue
        if known_roles and target not in known_roles:
            stray.append(target)
            continue
        out[who] = target
    if missing:
        raise ScriptUseError(f"这些说话人还没有映射：「{'、'.join(missing)}」——每个开口的人都要对上目标项目里的一个角色")
    if stray:
        raise ScriptUseError(f"映射到了目标项目里没有的角色：「{'、'.join(stray)}」——从下拉里重新选一个")
    return out


def _merge(existing: list[Any], incoming: list[dict[str, Any]], mode: str) -> list[Any]:
    return list(existing) + incoming if mode == "append" else list(incoming)


def record_use(spec: dict[str, Any], *, module: str, target_id: str, spots: list[str]) -> list[dict[str, Any]]:
    """往 ``used_in`` 记投放去向，就地改 ``spec``（调用方负责写盘）。同一个目标的同一个落点只留最新一条。"""

    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [dict(row) for row in spec.get("used_in") or () if isinstance(row, Mapping)]
    added: list[dict[str, Any]] = []
    for spot in spots:
        rows = [r for r in rows if not (r.get("module") == module and r.get("id") == target_id
                                        and str(r.get("target") or "") == spot)]
        entry = {"module": module, "id": target_id, "target": spot, "at": at}
        rows.append(entry)
        added.append(entry)
    spec["used_in"] = rows
    return added


# ---------------------------------------------------------------------------- 投放


def use_script(spec: dict[str, Any], *, module: str, target_id: str, blocks: Any, mapping: Any,
               mode: Any = None, version: str | None = None,
               project_root: Path | None = None) -> dict[str, Any]:
    """把这一版里点名的几段投放到目标项目。就地往 ``spec['used_in']`` 追记录（调用方写盘）。"""

    module = _module(module)
    target_id = _target_id(target_id)
    mode_name = _mode(mode)
    root = Path(project_root or spec["project_root"])
    plan, skipped = _plan(spec, blocks, version)
    if module == "podcast":
        result = _use_in_podcast(spec, root, target_id, plan, mapping, mode_name)
    elif module == "drama_screenplay":
        result = _use_in_screenplay(spec, root, target_id, plan, mapping, mode_name)
    else:
        result = _use_in_shot(spec, root, target_id, plan, mapping, mode_name)
    used = record_use(spec, module=module, target_id=target_id, spots=result["spots"])
    return {**result, "module": module, "target": target_id, "mode": mode_name, "used": used,
            "skipped": skipped, "spot_label": SPOT_LABELS[module]}


def _use_in_podcast(spec: Mapping[str, Any], root: Path, podcast_id: str,
                    plan: list[tuple[dict[str, Any], str, list[dict[str, Any]]]],
                    mapping: Any, mode: str) -> dict[str, Any]:
    """写进那期的 ``segments[].lines``：读 → 改 → 整份过 ``podcast.spec.normalize`` → ``save``。"""

    from podcast import spec as PC

    if not PC.spec_path(root, podcast_id).is_file():
        raise UnknownTarget(f"没有这期播客：{podcast_id}")
    try:
        podcast = PC.load(podcast_id, project_root=root)
    except PC.PodcastSpecError as exc:
        raise ScriptUseError(f"目标播客读不出来：{exc}") from exc
    raw = PC.to_disk(podcast)
    segments = [dict(s) for s in raw.get("segments") or () if isinstance(s, Mapping)]
    by_id = {str(s.get("id")): s for s in segments}
    who_map = _mapping(spec, mapping, plan, _roles(raw.get("hosts")))
    spots: list[str] = []
    for block, spot, lines in plan:
        incoming = [{"who": who_map[str(l["who"])], "text": str(l.get("text") or ""),
                     "cue": str(l.get("cue") or ""), "kind": str(l.get("kind") or "talk")} for l in lines]
        if spot == NEW_SPOT:
            sid = _fresh_id(PC.default_segment_id, set(by_id))
            segment = {"id": sid, "kind": "topic", "title": str(block.get("title") or ""),
                       "goal": str(block.get("goal") or ""), "beats": [], "questions": [], "lines": incoming}
            segments.append(segment)
            by_id[sid] = segment
            spots.append(sid)
            continue
        segment = by_id.get(spot)
        if segment is None:
            raise UnknownTarget(f"没有这个环节：{spot}")
        segment["lines"] = _merge(segment.get("lines") or [], incoming, mode)
        spots.append(spot)
    raw["segments"] = segments
    try:
        saved = PC.normalize(raw, project_root=root, spec_id=podcast_id)
    except PC.PodcastSpecError as exc:
        raise ScriptUseError(f"目标播客没接受这些台词：{exc}") from exc
    PC.save(saved)
    return {"title": str(saved.get("title") or podcast_id), "spots": spots,
            "lines": sum(len(lines) for _b, _s, lines in plan)}


def _use_in_screenplay(spec: Mapping[str, Any], root: Path, episode_id: str,
                       plan: list[tuple[dict[str, Any], str, list[dict[str, Any]]]],
                       mapping: Any, mode: str) -> dict[str, Any]:
    """写进 ``<episode>.screenplay.json`` 的 ``scenes[].dialogue``，整份过 ``normalize_screenplay`` 再落盘。"""

    from storyvideo.screenplay import default_scene_id, normalize_screenplay
    from storyvideo.spec import SpecError
    from storyvideo.state import atomic_write_json

    from webui import deps

    if not (_story_dir() / f"{episode_id}.json").is_file():
        raise UnknownTarget(f"没有这个短剧项目：{episode_id}")
    path = deps.screenplay_path(episode_id)
    if not path.is_file():
        raise ScriptUseError(f"这一集还没有剧本：{episode_id}——先在短剧的「剧本」页出一份，再投放")
    screenplay = _read(path)
    scenes = [dict(s) for s in screenplay.get("scenes") or () if isinstance(s, Mapping)]
    by_id = {str(s.get("id")): s for s in scenes}
    who_map = _mapping(spec, mapping, plan, _roles(screenplay.get("cast")))
    spots: list[str] = []
    for block, spot, lines in plan:
        incoming = [{"who": who_map[str(l["who"])], "zh": str(l.get("text") or "")} for l in lines]
        if spot == NEW_SPOT:
            heading = str(block.get("title") or "").strip()
            if not heading:
                raise ScriptUseError(
                    f"段 {block['id']} 还没有标题——新建场次要有场景标题（内/外 · 地点 · 时间），先给这一段起个标题")
            sid = _fresh_id(default_scene_id, set(by_id))
            scene = {"id": sid, "heading": heading, "beats": [], "dialogue": incoming}
            scenes.append(scene)
            by_id[sid] = scene
            spots.append(sid)
            continue
        scene = by_id.get(spot)
        if scene is None:
            raise UnknownTarget(f"没有这一场：{spot}")
        scene["dialogue"] = _merge(scene.get("dialogue") or [], incoming, mode)
        spots.append(spot)
    merged = {**screenplay, "scenes": scenes}
    try:
        validated = normalize_screenplay(merged)
    except SpecError as exc:
        raise ScriptUseError(f"目标剧本没接受这些台词：{exc}") from exc
    atomic_write_json(path, validated)
    return {"title": str(validated.get("title") or episode_id), "spots": spots,
            "lines": sum(len(lines) for _b, _s, lines in plan)}


def _use_in_shot(spec: Mapping[str, Any], root: Path, episode_id: str,
                 plan: list[tuple[dict[str, Any], str, list[dict[str, Any]]]],
                 mapping: Any, mode: str) -> dict[str, Any]:
    """写进某几镜的 ``shot.lines``。锁定的剧集直接拒绝；写盘前整份过 ``storyvideo.spec.normalize_spec``。"""

    from storyvideo.spec import SpecError, normalize_spec
    from storyvideo.state import atomic_write_json

    path = _story_dir() / f"{episode_id}.json"
    if not path.is_file():
        raise UnknownTarget(f"没有这个短剧项目：{episode_id}")
    raw = _read(path)
    if episode_locked(root, raw, episode_id):
        raise TargetLocked(LOCKED_MESSAGE)
    shots = [s for s in raw.get("shots") or () if isinstance(s, dict)]
    by_id = {str(s.get("id")): s for s in shots}
    who_map = _mapping(spec, mapping, plan, _roles(raw.get("cast")))
    spots: list[str] = []
    for _block, spot, lines in plan:
        if spot == NEW_SPOT:
            raise ScriptUseError("分镜不能新建镜号——镜次是拆镜的产物，先去项目里加一镜，再回来投放")
        shot = by_id.get(spot)
        if shot is None:
            raise UnknownTarget(f"没有这一镜：{spot}")
        if "lines" not in shot and any(k in shot for k in ("en", "zh", "speaker")):
            raise ScriptUseError(f"镜 {spot} 是旧式 speaker/en/zh 平铺字段，这个工具不知道怎么安全写回——"
                                 "改台词请直接编辑源 JSON 文件")
        shot["lines"] = _merge(shot.get("lines") or [],
                               [{"who": who_map[str(l["who"])], "zh": str(l.get("text") or "")} for l in lines], mode)
        spots.append(spot)
    try:
        normalize_spec(raw, project_root=root)
    except SpecError as exc:
        raise ScriptUseError(f"目标项目没接受这些台词：{exc}") from exc
    atomic_write_json(path, raw)
    return {"title": _peek(raw, ("title_zh", "title", "title_en")) or episode_id, "spots": spots,
            "lines": sum(len(lines) for _b, _s, lines in plan)}


def _fresh_id(maker: Any, taken: set[str]) -> str:
    """按目标模块自己的编号规则（``G01…`` / ``S01…``）往后找第一个没被占的号。"""

    n = len(taken) + 1
    while maker(n) in taken:
        n += 1
    return str(maker(n))


# ---------------------------------------------------------------------------- 导入


def import_script(spec: Mapping[str, Any], *, module: str, target_id: str, blocks: Any = None,
                  label: str = "", project_root: Path | None = None) -> dict[str, Any]:
    """从目标项目把现有台词导进来当新的一版（``source="import"``）。

    返回 ``{"spec": 归一化后的新 spec, "version": 版本 id, "blocks": 段数, "lines": 句数, "speakers": 新增的角色 id,
    "renamed": 撞 id 换了号的角色 [{name, from, to, taken_by}], "title": 目标标题}``——调用方负责 ``save``
    （跟 ``_write`` 那条路一样，写盘只发生在一个地方）。

    ``blocks`` 给了就是「只导这几个落点」（按目标里的 id 列），不给就全导。骨架里没有的落点按落点 id 补一段，
    标题照抄——不补的话这一版的段对不上骨架，归一化直接拒绝。

    角色表只补**这次真导进来的台词里开口的人**（目标那边挂着 10 个人物、这几场只有 2 个开口，就只补 2 个）：
    角色表只有 ``S.SPEAKERS_MAX`` 个位子，把整张人物表塞进来会让一次正常的导入整个被打回。补的时候跨项目认的是
    **名字**，撞号分三种：id 与名字都对得上 = 同一个人，沿用；名字相同、id 不同 = 记一条重映射，拼版本之前把每句的
    ``who`` 换成已有的那个 id，不然归一化会说「说话人不在角色表里」；id 被**名字不同**的人占了 = 给导进来的角色换一个
    新 id（``xiao_2``…）并记进 ``renamed``，不静默归到别人名下——界面按角色表里的名字显示说话人，归错了小夏的话
    就显示成「别人」说的，而且返回体里一句提示都没有。开口的人实在太多就点名来源报错，不让 ``S.normalize``
    那句「角色表最多 6 人」漏出来——那句会让人以为是自己的角色表有毛病。
    """

    module = _module(module)
    target_id = _target_id(target_id)
    root = Path(project_root or spec["project_root"])
    wanted = [str(b).strip() for b in (blocks or ()) if str(b).strip()]
    if module == "podcast":
        title, roles, spots = _import_from_podcast(root, target_id)
    elif module == "drama_screenplay":
        title, roles, spots = _import_from_screenplay(root, target_id)
    else:
        title, roles, spots = _import_from_shots(root, target_id)
    if wanted:
        known = {sid for sid, *_rest in spots}
        missing = [s for s in wanted if s not in known]
        if missing:
            raise UnknownTarget(f"没有这个{SPOT_LABELS[module]}：{'、'.join(missing)}")
        spots = [row for row in spots if row[0] in wanted]
    spots = [row for row in spots if row[-1]]
    if not spots:
        raise ScriptUseError(NOTHING_TO_IMPORT)
    raw = S.to_disk(spec)
    speaking = {str(line["who"]) for *_head, lines in spots for line in lines}
    speakers = [dict(s) for s in raw.get("speakers") or () if isinstance(s, Mapping)]
    taken_ids = {str(s.get("id")) for s in speakers}
    by_name = {str(s.get("name")): str(s.get("id")) for s in speakers}
    remap: dict[str, str] = {}
    added: list[str] = []
    renamed: list[dict[str, str]] = []
    for role in roles:
        if role["id"] not in speaking:
            # 这次没开口的人不补进角色表（等它真开口了再说，角色表只有 6 个位子）
            continue
        if role["name"] in by_name:
            # 同名 = 同一个人（跨项目认的是名字）：id 不同就记一条重映射，不然 who 写着目标那边的 id，
            # 归一化会说「说话人不在角色表里」。名字要先于 id 查：工作台里已经有「小夏」（id zhou）、来源的小夏叫 xiao、
            # 而 xiao 在工作台里是别人时，先查 id 会把小夏的话记到别人名下
            if by_name[role["name"]] != role["id"]:
                remap[role["id"]] = by_name[role["name"]]
            continue
        sid = role["id"]
        if sid in taken_ids:
            # id 被名字不同的人占了：换个号（xiao_2、xiao_3…），别静默归到别人名下。``remap`` 会把这一版里的 who 换成新号。
            # 原 id 已经顶到 ID_RE 的 63 字上限时先截短再加后缀，不然新号自己过不了归一化
            n = 2
            while True:
                suffix = f"_{n}"
                sid = role["id"][:63 - len(suffix)] + suffix
                if sid not in taken_ids:
                    break
                n += 1
            remap[role["id"]] = sid
            taken_by = next((str(s.get("name") or "") for s in speakers if str(s.get("id")) == role["id"]), "")
            renamed.append({"name": role["name"], "from": role["id"], "to": sid, "taken_by": taken_by})
        speakers.append({"id": sid, "name": role["name"], "persona": "", "stance": ""})
        taken_ids.add(sid)
        by_name[role["name"]] = sid
        added.append(sid)
    if len(speakers) > S.SPEAKERS_MAX:
        # 点名来源，别让 S.normalize 那句「角色表最多 6 人」漏出来——错怪的是工作台自己的角色表，不是来源项目
        raise ScriptUseError(
            f"《{title}》里开口的人有 {len(speaking)} 个，跟工作台已有的角色合起来 {len(speakers)} 人，"
            f"超过角色表上限 {S.SPEAKERS_MAX} 人——先少导几段，或者去角色表里删掉用不上的人")
    raw["speakers"] = speakers
    skeleton = [dict(b) for b in raw.get("blocks") or () if isinstance(b, Mapping)]
    block_ids = {str(b.get("id")) for b in skeleton}
    version_blocks: list[dict[str, Any]] = []
    for sid, spot_title, spot_kind, lines in spots:
        bid = sid if ID_RE.fullmatch(sid) else S.default_block_id(len(skeleton) + 1)
        if bid not in block_ids:
            skeleton.append({"id": bid, "kind": spot_kind, "title": spot_title, "goal": "", "note": ""})
            block_ids.add(bid)
        version_blocks.append({"id": bid, "lines": [
            {**line, "who": remap.get(str(line["who"]), str(line["who"]))} for line in lines]})
    raw["blocks"] = skeleton
    versions = [dict(v) for v in raw.get("versions") or () if isinstance(v, Mapping)]
    vid = S.next_version_id(str(v.get("id") or "") for v in versions)
    versions.append({
        "id": vid,
        "label": label.strip() if label and label.strip() else f"从《{title}》导入",
        "source": "import", "model": "", "backend": "", "note": "",
        "created_at": time.time(), "from_version": "", "blocks": version_blocks,
    })
    raw["versions"] = versions
    S.adopt_if_first(raw)   # 第一版自动采用；之后的版本要人挑
    try:
        fresh = S.normalize(raw, project_root=root, spec_id=str(spec["id"]))
    except S.ScriptSpecError as exc:
        raise ScriptUseError(f"导进来的台词没通过校验：{exc}") from exc
    return {"spec": fresh, "version": vid, "blocks": len(version_blocks),
            "lines": sum(len(lines) for *_head, lines in spots), "speakers": added, "renamed": renamed,
            "title": title, "module": module, "target": target_id}


def _import_from_podcast(root: Path, podcast_id: str) -> tuple[str, list[dict[str, str]], list[_Spot]]:
    from podcast import spec as PC

    if not PC.spec_path(root, podcast_id).is_file():
        raise UnknownTarget(f"没有这期播客：{podcast_id}")
    data = _read(PC.spec_path(root, podcast_id))
    roles = _roles(data.get("hosts"))
    known = {r["id"] for r in roles}
    names = {r["name"]: r["id"] for r in roles}
    spots: list[_Spot] = []
    for segment in data.get("segments") or ():
        if not isinstance(segment, Mapping) or not str(segment.get("id") or "").strip():
            continue
        lines = []
        for line in segment.get("lines") or ():
            if not isinstance(line, Mapping):
                continue
            text = str(line.get("text") or "").strip()
            who = names.get(str(line.get("who") or ""), str(line.get("who") or ""))
            if not text or who not in known:
                continue
            lines.append({"who": who, "text": text, "cue": str(line.get("cue") or ""),
                          "kind": str(line.get("kind") or "talk")})
        # 环节种类只把「听友问答」带过来：工作台的段种类只有这两种，别的环节（冷开场 / 话题 / 收尾）都是对谈
        kind = "qa" if str(segment.get("kind") or "") == "qa" else "talk"
        spots.append((str(segment["id"]).strip(), str(segment.get("title") or ""), kind, lines))
    return _peek(data, ("title",)) or podcast_id, roles, spots


def _import_from_screenplay(root: Path, episode_id: str) -> tuple[str, list[dict[str, str]], list[_Spot]]:
    from webui import deps

    if not (_story_dir() / f"{episode_id}.json").is_file():
        raise UnknownTarget(f"没有这个短剧项目：{episode_id}")
    path = deps.screenplay_path(episode_id)
    if not path.is_file():
        raise ScriptUseError(f"这一集还没有剧本：{episode_id}——先在短剧的「剧本」页出一份，再导入")
    data = _read(path)
    roles = _roles(data.get("cast"))
    spots = [(str(scene["id"]).strip(), str(scene.get("heading") or ""), "talk",
              _dialogue_lines(scene.get("dialogue"), roles))
             for scene in data.get("scenes") or ()
             if isinstance(scene, Mapping) and str(scene.get("id") or "").strip()]
    return _peek(data, ("title",)) or episode_id, roles, spots


def _import_from_shots(root: Path, episode_id: str) -> tuple[str, list[dict[str, str]], list[_Spot]]:
    path = _story_dir() / f"{episode_id}.json"
    if not path.is_file():
        raise UnknownTarget(f"没有这个短剧项目：{episode_id}")
    data = _read(path)
    roles = _roles(data.get("cast"))
    spots = [(str(shot["id"]).strip(), str(shot.get("shot") or ""), "talk", _dialogue_lines(shot.get("lines"), roles))
             for shot in data.get("shots") or ()
             if isinstance(shot, Mapping) and str(shot.get("id") or "").strip()]
    return _peek(data, ("title_zh", "title", "title_en")) or episode_id, roles, spots


def _dialogue_lines(raw: Any, roles: list[dict[str, str]]) -> list[dict[str, Any]]:
    """短剧那边的 ``{who, zh}`` → 工作台的 ``{who, text, cue, kind}``；说话人不在人物表里的（旁白）跳过——
    工作台的台词必须有人说，硬塞进去归一化会整份拒绝。"""

    known = {r["id"] for r in roles}
    names = {r["name"]: r["id"] for r in roles}
    out: list[dict[str, Any]] = []
    for line in raw if isinstance(raw, list) else ():
        if not isinstance(line, Mapping):
            continue
        text = str(line.get("zh") or line.get("text") or "").strip()
        who = names.get(str(line.get("who") or ""), str(line.get("who") or ""))
        if not text or who not in known:
            continue
        out.append({"who": who, "text": text, "cue": "", "kind": "talk"})
    return out


__all__ = [
    "USE_MODES", "DEFAULT_MODE", "NEW_SPOT", "SPOT_LABELS", "LOCKED_MESSAGE", "NO_LINES_MESSAGE", "NOTHING_TO_IMPORT",
    "ScriptUseError", "UnknownTarget", "TargetLocked", "episode_locked", "list_targets", "target_title",
    "record_use", "use_script", "import_script",
]
