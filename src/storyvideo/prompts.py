"""Pure prompt/request compilation for the story-video runtime.

Keeping compilation free of transport and filesystem side effects makes the
paid request fingerprint deterministic and testable before a task is sent.
"""

from __future__ import annotations

from genkit.paths import code_root as _code_root
from storyvideo.spec import split_cast_ref

import functools
import json
from pathlib import Path
from typing import Any, Mapping


FRAME_STYLE = (
    "A frame from a modern vertical animated drama - semi-realistic animated-film "
    "quality, cinematic and believable, with real materials, real light and real "
    "depth. Smooth polished render, not a cartoon, not a painting, not "
    "photorealistic-uncanny. "
)

DEFAULT_VIDEO_ENV_LOCK = (
    " Keep the room, the furniture, the light direction and every character "
    "EXACTLY as in the first frame - same clothing, same colours, nothing else "
    "in the room changes."
)

DEFAULT_VIDEO_TAIL_LOCK = (
    " Keep every character's identity, face, body proportions, clothing, colours "
    "and screen geography consistent with the first frame while allowing one motivated "
    "camera move that supports the declared action: a gentle pan, tilt, push-in, pull-back "
    "or track is allowed, but no abrupt reframing, whip pan, camera shake or internal cut. "
    "Keep motion smooth, believable and cinematic; no jitter, morphing, extra limbs or "
    "duplicate characters."
)

TALK_LOCK = (
    " The speaking character's mouth opens and moves naturally as they talk, with "
    "matching facial expression."
)
# 中文提示词项目（prompt_language 以 zh 开头）用这一套：
# 编译器里写死的英文尾句/标签不能混进去（同一件事只能有一个出处）。
TALK_LOCK_CN = " 说话角色的嘴随台词自然开合，表情与台词匹配。"
NO_TEXT_TAIL_EN = "Absolutely NO text, letters, numbers or symbols anywhere in the image. "
NO_TEXT_TAIL_CN = "画面中绝对不出现任何文字、字母、数字或符号。"

# 2026-09-08 画面文字政策（融合 zenstory drama-skills 的文字政策 / IMG-07：必须可读的文字与全局「不要文字」不能
# 同时激活；只取判断，不取它的语法）。短剧常要观众读到证据文字——手机屏上的「剩余 4 天」、合同签名栏、榜单——
# 以前只能走后期叠字。shot.readable_text 非空时：首帧尾句从「绝对不出现任何文字」换成「唯一允许出现的文字是……」，
# 视频提示词加一句保持清晰可读、内容不变；方言里的字幕禁令仍在（字幕是另一层），但措辞改成「不叠字幕」而不是
# 「画面里没有任何文字」。没声明的镜头一个字都不变（提示词金丝雀守着）。
READABLE_TEXT_LANGUAGES = ("zh", "en", "digits")
_READABLE_LANG_LABEL_CN = {"en": "英文", "digits": "数字"}
_READABLE_LANG_LABEL_EN = {"zh": "Chinese characters", "digits": "digits"}


def readable_text_entries(shot: Mapping[str, Any]) -> list[dict[str, str]]:
    """shot.readable_text 里合法的条目（surface + text 非空）；不合法的静默跳过，API 边界另有校验。"""

    out: list[dict[str, str]] = []
    for item in shot.get("readable_text") or ():
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        surface = str(item.get("surface") or "").strip()
        if not text or not surface:
            continue
        lang = str(item.get("language") or "zh").strip().lower()
        out.append({
            "surface": surface, "text": text,
            "language": lang if lang in READABLE_TEXT_LANGUAGES else "zh",
            "position": str(item.get("position") or "").strip(),
        })
    return out


def readable_text_frame_tail(entries: list[dict[str, str]], zh: bool) -> str:
    """首帧尾句：没声明 = 老的「绝对不出现任何文字」；声明了 = 正面写出唯一允许的文字、载体、位置。"""

    if not entries:
        return NO_TEXT_TAIL_CN if zh else NO_TEXT_TAIL_EN
    items: list[str] = []
    if zh:
        for e in entries:
            lang = _READABLE_LANG_LABEL_CN.get(e["language"], "")
            where = e["surface"] + (f"（{e['position']}）" if e["position"] else "")
            items.append(f"《{e['text']}》" + (f"（{lang}）" if lang else "") + f"，位于{where}")
        return "画面里唯一允许出现的文字：" + "；".join(items) + "，字迹清晰可读；除此之外画面任何位置不出现其他文字、字母、数字或符号。"
    for e in entries:
        lang = _READABLE_LANG_LABEL_EN.get(e["language"], "")
        where = e["surface"] + (f" ({e['position']})" if e["position"] else "")
        items.append(f'"{e["text"]}"' + (f" ({lang})" if lang else "") + f" on the {where}")
    return ("The only text allowed in the image: " + "; ".join(items)
            + ", clearly legible; no other text, letters, numbers or symbols anywhere else in the image. ")


def readable_text_video_clause(entries: list[dict[str, str]], zh: bool) -> str:
    """视频提示词的 readable_text 部件：载体上的文字保持清晰可读、内容不变。没声明 = 空串（方言渲染时跳过）。"""

    if not entries:
        return ""
    if zh:
        return "、".join(f"{e['surface']}上的文字《{e['text']}》" for e in entries) + "保持清晰可读、内容不变。"
    items = [f'the text "{e["text"]}" on the {e["surface"]}' for e in entries]
    joined = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
    return joined[0].upper() + joined[1:] + (" stays" if len(items) == 1 else " stay") + " clearly legible and unchanged."


# 2026-09-08 观众可见性 / 揭示保护（DESIGN §28，融合 drama-skills「观众可见性」判据，不取它的语法）：
# ``shot.withhold = [{"fact": "凶手是陈默", "carrier": "脸", "until": "S8"}]``——fact 是观众此刻还不能知道的事实，
# carrier 是会泄露它的载体，until 是从哪一镜起可以揭示（空 = 整集都不揭示）。声明镜本身和它之后、until 之前
# 的每一镜，首帧与视频提示词都多一句排除句；到了 until 及之后不再注入。排除句只是提示词文本，不是保证——
# 模型会不会真的把脸藏好要看真实出片。没声明的 spec 一个字不变（金丝雀守着）。
def withhold_entries(shot: Mapping[str, Any]) -> list[dict[str, str]]:
    """shot.withhold 里合法的条目（fact 非空）；不合法的静默跳过，spec 校验与 API 边界另有把关。"""

    out: list[dict[str, str]] = []
    for item in shot.get("withhold") or ():
        if not isinstance(item, Mapping):
            continue
        fact = str(item.get("fact") or "").strip()
        if not fact:
            continue
        out.append({
            "fact": fact,
            "carrier": str(item.get("carrier") or "").strip(),
            "until": str(item.get("until") or "").strip(),
        })
    return out


def active_withholds(spec: Mapping[str, Any], shot_id: str) -> list[dict[str, str]]:
    """本镜生效的揭示保护：声明镜 W 及其后、until 之前（不含 until）的镜。按声明顺序、同一 (fact, carrier) 只一次。
    until 指向不存在的镜时按「整集」处理（spec 校验会拒，这里只求不崩）。"""

    shots = [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]
    order = {str(s.get("id")): i for i, s in enumerate(shots)}
    if shot_id not in order:
        return []
    here = order[shot_id]
    out: dict[tuple[str, str], dict[str, str]] = {}
    for shot in shots:
        start = order[str(shot.get("id"))]
        if start > here:
            break
        for entry in withhold_entries(shot):
            end = order.get(entry["until"]) if entry["until"] else None
            if end is not None and here >= end:
                continue
            out.setdefault((entry["fact"], entry["carrier"]), entry)
    return list(out.values())


def withhold_clause(spec: Mapping[str, Any], shot_id: str, zh: bool, *, kind: str = "video") -> str:
    """排除句。中文：「不得揭示：凶手是陈默——脸全程不入画或被遮挡、看不清；…。画面、动作与台词都不透露这一点。」
    英文同义。首帧（kind="frame"）的尾巴只说画面，不提动作与台词。没有生效的保护返回空串。"""

    entries = active_withholds(spec, shot_id)
    if not entries:
        return ""
    items: list[str] = []
    for e in entries:
        if zh:
            items.append(e["fact"] + (f"——{e['carrier']}全程不入画或被遮挡、看不清" if e["carrier"] else ""))
        else:
            items.append(e["fact"] + (f" — the {e['carrier']} stays out of frame or hidden and unreadable" if e["carrier"] else ""))
    plural = len(entries) > 1
    if zh:
        tail = ("画面任何位置都不透露这一点。" if not plural else "画面任何位置都不透露这些信息。") if kind == "frame" \
            else ("画面、动作与台词都不透露这一点。" if not plural else "画面、动作与台词都不透露这些信息。")
        return "不得揭示：" + "；".join(items) + "。" + tail
    what = "it" if not plural else "them"
    tail = (f" Nothing anywhere in the image gives {what} away." if kind == "frame"
            else f" Nothing in the picture, action or dialogue gives {what} away.")
    return "Do not reveal: " + "; ".join(items) + "." + tail


# 2026-09-06 立，思路参考 waooAI/waoowaoo 的视频导演规范（只借思路，句子按本仓库的写法重写）：
# 整镜情绪至多转一次、让它转的事就在本镜画面里发生；情绪从镜头拍得到的小动作里出来；人物各自看着戏里的人或物，不看镜头。
# 三句都是 opt-in：spec.performance_lock=true 才拼进视频提示词，老项目不受影响。
PERFORMANCE_LOCK_CN = (
    " 这一镜里情绪最多转一次，让它转的那件事就在画面里发生。情绪只从看得见的小动作里出来："
    "目光落在哪、停多久，一个停顿，手上正做的事；宁可收着演，要力度就交给动作。"
)
PERFORMANCE_LOCK_EN = (
    " The mood shifts at most once in this shot, and whatever shifts it happens on screen. "
    "Feelings show only through small actions the camera can see: where the eyes settle and for how long, a pause, "
    "what the hands are busy with. Play it small; if a moment needs force, put it into the action."
)
GAZE_LOCK_CN = "人物各自看着这场戏里具体的人或东西（画外的也算），谁也不看镜头。"
GAZE_LOCK_EN = "Each character looks at someone or something in the scene; nobody looks into the camera."
_GAZE_CAMERA_MOVES = {"selfie_arm"}  # 自拍镜看镜头是对的，视线句不加


def _shot_looks_at_camera(shot: Mapping[str, Any]) -> bool:
    return str(shot.get("gaze") or "").strip() == "camera" or str(shot.get("camera_move") or "") in _GAZE_CAMERA_MOVES


def boundary_text(obj: Any, zh: bool) -> str:
    """结构化边界（shot.entry / shot.exit，DESIGN §22）→ 一句绝对事实：
    「位置——…；朝向——…；视线——…；手——…；持物——…；可见状态——…；与上一镜相同：位置、朝向。」
    不是对象、或六个字段和 same_as_previous 全空 = 空串（回落到自由文本）。"""

    from .continuity import BOUNDARY_KEYS, BOUNDARY_LABELS, BOUNDARY_LABELS_EN

    if not isinstance(obj, Mapping):
        return ""
    labels = BOUNDARY_LABELS if zh else BOUNDARY_LABELS_EN
    pieces: list[str] = []
    for key in BOUNDARY_KEYS:
        value = str(obj.get(key) or "").strip().rstrip("。.;； ")
        if value:
            pieces.append(f"{labels[key]}——{value}" if zh else f"{labels[key]} — {value}")
    same = [labels[str(k)] for k in (obj.get("same_as_previous") or ()) if str(k) in labels]
    if same:
        pieces.append(("与上一镜相同：" + "、".join(same)) if zh else ("same as previous shot: " + ", ".join(same)))
    if not pieces:
        return ""
    return ("；" if zh else "; ").join(pieces) + ("。" if zh else ".")


def entry_exit_text(shot: Mapping[str, Any], zh: bool) -> tuple[str, str]:
    """入口/出口的正文：有结构化对象（entry/exit）就按字段渲染，否则用自由文本 entry_state/exit_state（老项目不变）。"""

    entry = boundary_text(shot.get("entry"), zh) or str(shot.get("entry_state") or "").strip()
    exit_ = boundary_text(shot.get("exit"), zh) or str(shot.get("exit_state") or "").strip()
    return entry, exit_


def _state_clauses(shot: Mapping[str, Any], zh: bool) -> tuple[str, str]:
    """入口/出口状态：写了才拼；开头一句、结尾一句。"""

    entry, exit_ = entry_exit_text(shot, zh)
    head = (f"入口状态：{entry} " if zh else f"ENTRY STATE: {entry} ") if entry else ""
    tail = (f" 出口状态：{exit_}" if zh else f" EXIT STATE: {exit_}") if exit_ else ""
    return head, tail


AXIS_SIDES = ("left", "right")
_SIDE_CN = {"left": "左", "right": "右"}


def _axis_name(spec: Mapping[str, Any], key: str) -> str:
    member = (spec.get("cast") or {}).get(key)
    if isinstance(member, Mapping) and member.get("name"):
        return str(member["name"])
    return key


def axis_clause(spec: Mapping[str, Any], shot: Mapping[str, Any], zh: bool) -> str:
    """轴线句（shot.axis = {anchors, side, screen, break}，DESIGN §22）：
    「轴线：机位在甲—乙连线的左侧；甲固定占画面左，乙占画面右。」没声明 = 空串，老项目提示词零变化。"""

    axis = shot.get("axis")
    if not isinstance(axis, Mapping):
        return ""
    anchors = [str(a).strip() for a in (axis.get("anchors") or ()) if str(a).strip()]
    side = str(axis.get("side") or "").strip().lower()
    screen = axis.get("screen") if isinstance(axis.get("screen"), Mapping) else {}
    pieces: list[str] = []
    if anchors:
        line = "—".join(_axis_name(spec, a) for a in anchors)
        if side in AXIS_SIDES:
            pieces.append(f"机位在{line}连线的{_SIDE_CN[side]}侧" if zh else f"camera stays on the {side} side of the {line} line")
        else:
            pieces.append(f"轴线取{line}连线" if zh else f"the axis is the {line} line")
    holds: list[str] = []
    for key, value in screen.items():
        v = str(value or "").strip().lower()
        if v not in AXIS_SIDES:
            continue
        name = _axis_name(spec, str(key))
        if zh:
            holds.append(f"{name}{'固定占' if not holds else '占'}画面{_SIDE_CN[v]}")
        else:
            holds.append(f"{name} {'always holds ' if not holds else ''}screen {v}")
    if holds:
        pieces.append(("，" if zh else ", ").join(holds))
    if not pieces:
        return ""
    text = ("轴线：" if zh else "AXIS: ") + ("；" if zh else "; ").join(pieces) + ("。" if zh else ".")
    if axis.get("break") is True:
        text += ("本镜有意越轴，新的机位侧从这一镜起沿用。" if zh
                 else " This shot deliberately crosses the line; the new camera side holds from here on.")
    return text


def _beats_block(shot: Mapping[str, Any], zh: bool) -> str:
    """可选的时间块：beats=[{t:'0-3', picture, performance, sound}] → 逐块带时码；motion 保留作总述。"""

    beats = [b for b in (shot.get("beats") or ()) if isinstance(b, Mapping)]
    if not beats:
        return ""
    lines: list[str] = []
    for beat in beats:
        t = str(beat.get("t") or "").replace("-", "–")
        parts = []
        if beat.get("picture"):
            parts.append(("画面：" if zh else "PICTURE: ") + str(beat["picture"]))
        if beat.get("performance"):
            parts.append(("表演：" if zh else "PERFORMANCE: ") + str(beat["performance"]))
        if beat.get("sound"):
            parts.append(("声音：" if zh else "SOUND: ") + str(beat["sound"]))
        lines.append(f"[{t}s] " + ("；" if zh else "; ").join(parts))
    return " " + " ".join(lines)


def _visual_style_clause(spec: Mapping[str, Any], zh: bool) -> str:
    """视觉风格四要素（媒介技法 / 带角色的色板 / 线与边缘 / 完成度），spec.visual_style 写了才拼进首帧提示词。"""

    vs = spec.get("visual_style")
    if not isinstance(vs, Mapping):
        return ""
    palette = vs.get("palette") or ()
    swatches = []
    for item in palette:
        if isinstance(item, Mapping) and item.get("hex"):
            role = str(item.get("role") or "").strip()
            swatches.append(f"{item['hex']}（{role}）" if role and zh else (f"{item['hex']} ({role})" if role else str(item["hex"])))
    pieces = []
    if vs.get("medium"):
        pieces.append(("媒介与技法：" if zh else "Medium: ") + str(vs["medium"]))
    if swatches:
        pieces.append(("色板：" if zh else "Palette: ") + ("、" if zh else ", ").join(swatches))
    if vs.get("edge"):
        pieces.append(("线与边缘：" if zh else "Edges: ") + str(vs["edge"]))
    if vs.get("finish"):
        pieces.append(("完成度：" if zh else "Finish: ") + str(vs["finish"]))
    if not pieces:
        return ""
    return ("视觉风格——" if zh else "VISUAL STYLE — ") + ("；" if zh else "; ").join(pieces) + ("。" if zh else ". ")


def _is_zh(spec: Mapping[str, Any]) -> bool:
    return str(spec.get("prompt_language") or "en").strip().lower().startswith("zh")


CAMERA_LIBRARY_PATH = _code_root() / "skills" / "ai-short-film" / "scripts" / "camera_library.json"


class CameraError(ValueError):
    """镜头语言用法违规——不合法的镜头 id、越过产线 profile、或超出用量上限。"""


@functools.lru_cache(maxsize=1)
def camera_library() -> dict[str, Any]:
    """公共镜头语言库（单镜运镜 + 镜内组合 + 跨镜头组 + 产线 profile，条目文字本仓库自写）。"""

    with open(CAMERA_LIBRARY_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _camera_index() -> tuple[dict[str, Any], dict[str, Any]]:
    library = camera_library()
    shots = {entry["id"]: entry for entry in library.get("shots", ())}
    combos = {entry["id"]: entry for entry in library.get("combos_within_shot", ())}
    return shots, combos


def find_camera_profile(profile_id: str, library: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """按 id 找产线 profile：先查 ``profiles``（2026-09-08 起的 5 条经典产线），再查
    ``legacy_profiles``（停用的旧档位，只为已有项目兼容保留）。找不到返回 None。

    旧项目的 spec 仍写着 ``camera_profile: "ghost_pov"`` 这类 id，所以旧定义留在库里、照旧能编译
    （金丝雀守着编译结果）；只是界面不再把它们列给新项目。2026-09-23 起 ghost_pov / vlog_handheld
    两档里几个镜位的提示词改成了通用的手持 / 第一人称写法，用到它们的旧项目请求指纹变了一次。
    """

    lib = library if library is not None else camera_library()
    key = str(profile_id or "").strip()
    if not key:
        return None
    found = (lib.get("profiles") or {}).get(key)
    if found is None:
        found = (lib.get("legacy_profiles") or {}).get(key)
    return found


def compile_camera_phrase(spec: Mapping[str, Any], shot_id: str) -> str:
    """把镜次的 ``camera_move`` / ``camera_combo`` 编译成提示词短语。

    镜头语言此前完全埋在 ``art`` / ``motion`` 的散文里：不可寻址、不可复用、不可 QC。
    一集十几个镜头共用同一句全局 ``camera``——**结构上就注定是同一种覆盖方式**。
    改成查表后，运镜才能按产线 profile 约束、按戏剧功能选取。

    未声明 ``camera_move`` / ``camera_combo`` 时返回空串，既有剧集行为完全不变。
    """

    shot = _shot(spec, shot_id)
    move_id = str(shot.get("camera_move") or "").strip()
    combo_id = str(shot.get("camera_combo") or "").strip()
    if not move_id and not combo_id:
        return ""
    if move_id and combo_id:
        raise CameraError(
            f"shot {shot_id}: camera_move 与 camera_combo 只能二选一"
        )

    shots, combos = _camera_index()
    entry = combos.get(combo_id) if combo_id else shots.get(move_id)
    if entry is None:
        raise CameraError(
            f"shot {shot_id}: 镜头语言库里没有 {combo_id or move_id!r}"
        )

    profile_id = str(spec.get("camera_profile") or "").strip()
    if profile_id:
        profile = find_camera_profile(profile_id)
        if profile is None:
            raise CameraError(f"未知的 camera_profile: {profile_id!r}")
        allowed = profile.get("combos" if combo_id else "allow") or ()
        if (combo_id or move_id) not in allowed:
            raise CameraError(
                f"shot {shot_id}: {entry['cn']} 不在产线 profile {profile_id!r} 的白名单里。"
                f"该 profile 的理由：{profile.get('why', '')}"
            )

    language = str(spec.get("prompt_language") or "en").strip().lower()
    key = "prompt_cn" if language.startswith("zh") else "prompt_en"
    phrase = str(entry.get(key) or entry.get("prompt_en") or "").strip()
    if not phrase:
        return ""
    # 幅度必须写死：不写幅度的话，模型常把一个「缓摇」拍成幅度很大的推近。
    # 库里少数镜头（vlog 的 yank_jolt）本身就是「突发」，amplitude="sudden" 换尾句——
    # 否则「猛晃甩镜」后面跟一句 slow and small，两句打架，模型二选一还是拍成缓摇。
    sudden = str(entry.get("amplitude") or "").strip() == "sudden"
    if language.startswith("zh"):
        if sudden:
            return f" 镜头：{phrase}。幅度突发且短促，随即稳住；没有其他镜头运动。"
        return f" 镜头：{phrase}。幅度小、慢、有动机；没有其他镜头运动。"
    if sudden:
        return (
            f" CAMERA: {phrase}. The jolt is sudden and brief, then the frame settles;"
            " no other camera motion."
        )
    return f" CAMERA: {phrase}. The move is slow, small and motivated; no other camera motion."


def validate_camera_usage(spec: Mapping[str, Any]) -> list[str]:
    """整集校验：逐镜合法性 + 产线的运镜用量上限。返回告警列表。"""

    warnings: list[str] = []
    used: list[str] = []
    for shot in spec.get("shots", ()):  # type: ignore[assignment]
        if not isinstance(shot, Mapping):
            continue
        shot_id = str(shot.get("id") or "")
        phrase = compile_camera_phrase(spec, shot_id)
        if phrase:
            used.append(shot_id)

    profile_id = str(spec.get("camera_profile") or "").strip()
    if profile_id:
        profile = find_camera_profile(profile_id) or {}
        cap = profile.get("max_moves_per_episode")
        if isinstance(cap, int) and len(used) > cap:
            warnings.append(
                f"运镜镜次 {len(used)} 个，超过 profile {profile_id!r} 的上限 {cap}："
                f"{', '.join(used)}。运镜是重音，全都重音等于没有重音。"
            )
    return warnings


def _shot(spec: Mapping[str, Any], shot_id: str) -> Mapping[str, Any]:
    for shot in spec.get("shots", ()):  # type: ignore[assignment]
        if isinstance(shot, Mapping) and shot.get("id") == shot_id:
            return shot
    raise KeyError(f"unknown shot: {shot_id}")


# 2026-09-08 融合 drama-skills 的「连续性锁」（只取判据，不取它的 markdown 语法）：
# ``cast.<id>.locks = [{"surface": "淡蓝色粗针织羊毛毛衣", "shots": "all" | ["S1", "S2"], "note": "…"}]``。
# 锁面是跨镜不变的那几个字，必须**原样**进每条生效提示词（首帧和视频都进）——颜色 + 材质 + 物体这类
# 事实不能靠模型记忆，也不能以「参考帧已经交代过」为由删掉。
# 生效规则：shots="all" → 成员在本镜 cast 里的每一镜；shots=[...] → 恰好列出的那几镜，成员不在本镜
# cast 里也注入（被手挡住的道具、搭在椅背上的外套，常常不在出场名单里）。没有锁的 spec 一个字都不多。
def _lock_applies(lock: Mapping[str, Any], member_id: str, shot: Mapping[str, Any]) -> bool:
    scope = lock.get("shots", "all")
    if scope == "all" or scope is None:
        return member_id in shot_cast_ids(shot)
    if isinstance(scope, (list, tuple)):
        return str(shot.get("id")) in {str(s) for s in scope}
    return False


def shot_lock_surfaces(spec: Mapping[str, Any], shot_id: str) -> list[str]:
    """本镜生效的锁面，按 cast 顺序、每个锁面只出现一次。形状不对的锁悄悄跳过（lint 会报）。"""

    shot = _shot(spec, shot_id)
    surfaces: dict[str, None] = {}
    for member_id, member in (spec.get("cast") or {}).items():
        if not isinstance(member, Mapping):
            continue
        for lock in member.get("locks") or ():
            if not isinstance(lock, Mapping):
                continue
            surface = str(lock.get("surface") or "").strip()
            if surface and _lock_applies(lock, str(member_id), shot):
                surfaces.setdefault(surface, None)
    return list(surfaces)


def lock_clause(spec: Mapping[str, Any], shot_id: str) -> str:
    """「固定不变：A；B。」/ ``Fixed across shots: A; B.``；没有生效的锁返回空串。"""

    surfaces = shot_lock_surfaces(spec, shot_id)
    if not surfaces:
        return ""
    if _is_zh(spec):
        return "固定不变：" + "；".join(surfaces) + "。"
    return "Fixed across shots: " + "; ".join(surfaces) + "."


# 角色变体（2026-09-08，融合 drama-skills「身份 vs 版本」判据，DESIGN §23）：``shot.cast`` 里的 ``"lin@rain"``
# 取 ``cast.lin.block``（身份基底）+ ``cast.lin.variants.rain.delta``（只写差异：换装/受伤/湿污/伪装），
# 锚图优先用变体自己的、没有就用成员的。没写变体的 spec 一个字不变。
def shot_cast_ids(shot: Mapping[str, Any]) -> list[str]:
    """本镜出场的成员 id（去掉 ``@变体`` 后缀，保持顺序、不去重）。"""

    return [split_cast_ref(ref)[0] for ref in (shot.get("cast") or ())]


def _resolve_cast_ref(spec: Mapping[str, Any], ref: Any) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
    member_id, variant_key = split_cast_ref(ref)
    member = (spec.get("cast") or {}).get(member_id) or {}
    if not isinstance(member, Mapping):
        member = {}
    variant = None
    if variant_key:
        candidate = (member.get("variants") or {}).get(variant_key)
        variant = candidate if isinstance(candidate, Mapping) else None
    return member, variant


def cast_block_text(spec: Mapping[str, Any], ref: Any) -> str:
    """一条 cast 引用进首帧提示词的正文：基底 block；带变体时接上 delta（句号衔接，不重复标点）。"""

    member, variant = _resolve_cast_ref(spec, ref)
    block = str(member.get("block") or "")
    delta = str((variant or {}).get("delta") or "").strip()
    if not delta:
        return block
    base = block.rstrip()
    if _is_zh(spec):
        joiner = "" if base.endswith(("。", "；", "！", "？", "，")) else "。"
        return f"{base}{joiner}{delta}" + ("" if delta.endswith(("。", "！", "？")) else "。")
    joiner = " " if base.endswith((".", ";", "!", "?", ",")) else ". "
    return f"{base}{joiner}{delta}" + ("" if delta.endswith((".", "!", "?")) else ".") + " "


def cast_anchor_asset(spec: Mapping[str, Any], ref: Any) -> str:
    """一条 cast 引用的锚图：变体自己有就用变体的，否则成员的；都没有返回空串。"""

    member, variant = _resolve_cast_ref(spec, ref)
    variant_asset = str((variant or {}).get("asset") or "").strip()
    return variant_asset or str(member.get("asset") or "").strip()


# 参考槽位用途 + 尾帧续接（2026-09-08，DESIGN §30，融合 drama-skills「参考图四态」的判据：每张参考图要说清
# **取什么、控制什么、不得控制什么**，不取它的标记语法）。参考图进请求的顺序是 frame_reference_asset_ids 定的
# （依赖槽 → 角色定妆 → 道具），这里按同一顺序排出槽位，编号才是真的；没写 reference_roles 的镜一个字不变。
REFERENCE_ASPECTS = ("脸", "发型", "服装", "姿势", "机位", "光线", "场景", "色调")
_ASPECT_EN = {"脸": "face", "发型": "hairstyle", "服装": "wardrobe", "姿势": "pose", "机位": "camera position",
              "光线": "lighting", "场景": "setting", "色调": "colour grade", "构图": "composition", "表情": "expression",
              "道具": "props", "位置": "positions"}


def _tail_reference(shot: Mapping[str, Any]) -> str | None:
    """本镜以 ref_tail（reference 模式）占依赖槽时返回源镜 id，否则 None。"""

    from .tail import tail_ref

    ref = tail_ref(shot)
    return ref[0] if ref is not None and ref[1] == "reference" else None


def reference_slot_plan(spec: Mapping[str, Any], shot_id: str) -> list[dict[str, Any]]:
    """本镜首帧请求的参考槽位，顺序与 ``frame_reference_asset_ids`` 一致：
    ``[{"index": 0, "target": "tail"|"prev_frame"|"cast:lin@rain"|"prop", "source": …, "label_zh": …, "label_en": …}]``。
    同一张锚图被两个出场引用共用时只占一个槽（和请求里的去重一致），后一个引用挂在同一个 index 上。"""

    shot = _shot(spec, shot_id)
    slots: list[dict[str, Any]] = []
    tail_source = _tail_reference(shot)
    ref_frame = str(shot.get("ref_frame") or "").strip()
    if tail_source:
        slots.append({"index": 0, "target": "tail", "source": tail_source,
                      "label_zh": f"上一镜 {tail_source} 尾帧", "label_en": f"tail frame of {tail_source}",
                      "aliases": ["prev_frame"]})
    elif ref_frame:
        slots.append({"index": 0, "target": "prev_frame", "source": ref_frame,
                      "label_zh": f"上一镜 {ref_frame} 首帧", "label_en": f"first frame of {ref_frame}", "aliases": []})
    seen_assets: dict[str, int] = {}
    for cast_ref in shot.get("cast") or ():
        asset = cast_anchor_asset(spec, cast_ref)
        if not asset:
            continue
        member, variant = _resolve_cast_ref(spec, cast_ref)
        name = str(member.get("name") or split_cast_ref(cast_ref)[0])
        variant_label = str((variant or {}).get("label") or split_cast_ref(cast_ref)[1] or "") if variant is not None else ""
        if asset in seen_assets:
            index = seen_assets[asset]
        else:
            index = len({s["index"] for s in slots})
            seen_assets[asset] = index
        slots.append({"index": index, "target": f"cast:{cast_ref}", "source": str(cast_ref),
                      "label_zh": f"{name}定妆" + (f"·{variant_label}" if variant_label else ""),
                      "label_en": f"{name} anchor" + (f" ({variant_label})" if variant_label else ""), "aliases": []})
    if spec.get("prop_ref"):
        slots.append({"index": len({s["index"] for s in slots}), "target": "prop", "source": "prop_ref",
                      "label_zh": "道具参考图", "label_en": "prop reference", "aliases": []})
    return slots


def reference_role_entries(shot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """shot.reference_roles 里合法的条目（target 非空）；use 字符串，control / ignore 去空去重。"""

    out: list[dict[str, Any]] = []
    for item in shot.get("reference_roles") or ():
        if not isinstance(item, Mapping):
            continue
        target = str(item.get("target") or "").strip()
        if not target:
            continue
        aspects = {}
        for key in ("control", "ignore"):
            raw = item.get(key) or ()
            aspects[key] = list(dict.fromkeys(str(a).strip() for a in raw if str(a).strip())) if isinstance(raw, (list, tuple)) else []
        entry = {"target": target, "use": str(item.get("use") or "").strip(), **aspects}
        fidelity = _fidelity_value(item.get("fidelity"))
        if fidelity:
            entry["fidelity"] = fidelity
        out.append(entry)
    return out


def _fidelity_value(raw: Any) -> int | None:
    """相似度百分比：1–100 的整数；空 / 非数 / 越界 = 没写。"""

    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(round(float(raw)))
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 100 else None


def reference_roles_by_slot(spec: Mapping[str, Any], shot_id: str) -> list[tuple[int, str, dict[str, Any]]]:
    """``[(槽位序号, 槽位标签, role)]``，按槽位顺序；``prev_frame`` 在尾帧占依赖槽时也落在第 0 槽。
    找不到槽的 role 跳过（spec 校验会拒，这里只求编号诚实）。"""

    shot = _shot(spec, shot_id)
    zh = _is_zh(spec)
    plan = reference_slot_plan(spec, shot_id)
    by_target: dict[str, dict[str, Any]] = {}
    for slot in plan:
        by_target.setdefault(slot["target"], slot)
        for alias in slot.get("aliases") or ():
            by_target.setdefault(alias, slot)
        if slot["target"].startswith("cast:"):
            bare = split_cast_ref(slot["target"][len("cast:"):])[0]
            by_target.setdefault(f"cast:{bare}", slot)
    placed: list[tuple[int, str, dict[str, Any]]] = []
    for role in reference_role_entries(shot):
        slot = by_target.get(role["target"])
        if slot is None:
            continue
        placed.append((int(slot["index"]), str(slot["label_zh"] if zh else slot["label_en"]), role))
    placed.sort(key=lambda item: item[0])
    return placed


def _aspects_en(items: list[str]) -> str:
    return ", ".join(_ASPECT_EN.get(a, a) for a in items)


def reference_roles_clause(spec: Mapping[str, Any], shot_id: str) -> str:
    """「参考图1（上一镜首帧）：只取场景与光线；不得控制姿势、机位。参考图2（林小满定妆）：脸、发型逐字对照；不得控制服装。」
    英文 ``Reference 1 (first frame of S1): scene and light only; must not control pose, camera position.``
    没写 reference_roles = 空串。"""

    zh = _is_zh(spec)
    pieces: list[str] = []
    for index, label, role in reference_roles_by_slot(spec, shot_id):
        parts: list[str] = []
        if role["use"]:
            parts.append(role["use"].rstrip("。.;； "))
        if role["control"]:
            parts.append(("、".join(role["control"]) + "逐字对照") if zh else ("follow " + _aspects_en(role["control"]) + " exactly"))
        if role["ignore"]:
            parts.append(("不得控制" + "、".join(role["ignore"])) if zh else ("must not control " + _aspects_en(role["ignore"])))
        if role.get("fidelity"):
            # 相似度百分比：像到什么程度写成一个数（如 80%），比「尽量像」更可控
            parts.append((f"与这张参考图的相似度约 {int(role['fidelity'])}%") if zh
                         else (f"roughly {int(role['fidelity'])}% likeness to this reference"))
        if not parts:
            continue
        if zh:
            pieces.append(f"参考图{index + 1}（{label}）：" + "；".join(parts) + "。")
        else:
            pieces.append(f"Reference {index + 1} ({label}): " + "; ".join(parts) + ".")
    return "".join(pieces) if zh else " ".join(pieces)


def compile_frame_prompt(spec: Mapping[str, Any], shot_id: str) -> str:
    shot = _shot(spec, shot_id)
    anchor = shot.get("anchor") or spec.get("scene_anchor")
    parts = [str(spec.get("frame_style") or FRAME_STYLE), str(anchor or "")]
    for cast_ref in shot.get("cast") or ():
        parts.append(cast_block_text(spec, cast_ref))
    locks = lock_clause(spec, shot_id)
    if locks:
        parts.append(locks if _is_zh(spec) else locks + " ")
    zh = _is_zh(spec)
    entry_head, _ = _state_clauses(shot, zh)
    axis = axis_clause(spec, shot, zh)
    withhold = withhold_clause(spec, shot_id, zh, kind="frame")
    parts.extend(
        [
            _visual_style_clause(spec, zh),
            (f"镜位：{shot.get('shot', '')}。{shot.get('art', '')} " if zh
             else f"SHOT: {shot.get('shot', '')}. {shot.get('art', '')} "),
            entry_head,  # 首帧就是入口那一刻
            (axis + " ") if axis else "",   # 机位在轴线哪一侧、谁占画面哪边（DESIGN §22）
            (withhold + " ") if withhold else "",   # 观众此刻不能知道的事实与它的载体（DESIGN §28）
            str(spec.get("camera") or ""),
            readable_text_frame_tail(readable_text_entries(shot), zh),
        ]
    )
    roles = reference_roles_clause(spec, shot_id)   # 参考图逐槽说明：取什么、控制什么、不得控制什么（DESIGN §30）
    if roles:
        parts.append(roles if zh else roles + " ")
    return "".join(parts)


def compile_negative_prompt(spec: Mapping[str, Any], shot_id: str) -> str:
    shot = _shot(spec, shot_id)
    values = [str(spec.get("neg") or "").strip(), str(shot.get("neg_extra") or "").strip()]
    return ",".join(value for value in values if value)


def frame_reference_asset_ids(
    spec: Mapping[str, Any],
    shot_id: str,
    *,
    dependency_asset_id: str | None = None,
    prop_asset_id: str | None = None,
) -> tuple[str, ...]:
    """Return provider asset IDs in constraint priority order."""

    shot = _shot(spec, shot_id)
    refs: list[str] = []
    if dependency_asset_id:
        refs.append(dependency_asset_id)
    for cast_ref in shot.get("cast") or ():
        asset_id = cast_anchor_asset(spec, cast_ref)
        if asset_id:
            refs.append(asset_id)
    if prop_asset_id:
        refs.append(prop_asset_id)
    # Preserve ordering but never spend a reference slot twice.
    return tuple(dict.fromkeys(refs))


def _form_budget(spec: Mapping[str, Any]) -> str:
    """形态卡的运动预算句（DESIGN §31）。惰性导入：forms 只在写了 form 的 spec 上才有事做。"""

    if not spec.get("form"):
        return ""
    from .forms import form_prompt_sentences

    return form_prompt_sentences(spec)   # 运动预算 + 构图/安全区句


def video_prompt_parts(spec: Mapping[str, Any], shot_id: str) -> dict[str, Any]:
    """一镜视频提示词的各个部件（方言渲染用）。generic 方言把它们按老顺序原样拼接；其他方言按各家语法重排。"""

    shot = _shot(spec, shot_id)
    environment_lock = str(spec.get("video_lock") or DEFAULT_VIDEO_ENV_LOCK)
    tail_lock = str(spec.get("video_tail_lock") or DEFAULT_VIDEO_TAIL_LOCK)
    per_shot_lock = shot.get("lock")
    lock = (str(per_shot_lock) + tail_lock) if per_shot_lock else (environment_lock + tail_lock)
    speech_mode = str(shot.get("speech_mode") or spec.get("speech_mode") or "").strip()
    offscreen = speech_mode in {"narration", "offscreen_narration"}
    has_dialogue = bool(shot.get("lines") or shot.get("speaker")) and not offscreen
    camera = compile_camera_phrase(spec, shot_id)
    sfx = str(shot.get("sfx") or "").strip()
    zh = _is_zh(spec)
    # 音效必须独立成行，不能埋进画面描述——实测：中文提示词里单起一行写音效，
    # 高频带 0.53s 就出现明确刮擦；埋在散文里的对照组整条没有独立音效。
    sfx_line = (f" 音效：{sfx}。" if zh else f" SOUND: {sfx}.") if sfx else ""
    # dialogue_prompt="auto"（spec 级，shot 级可覆盖）：台词段由 lines 自动生成拼进视频提示词，
    # motion 里不再手写台词——否则 UI 改了 lines 只改字幕，模型念的还是 motion 里的旧句子
    # （2026-09-05 立的规矩）。老项目不设这个字段，行为不变。
    dialogue_block = _auto_dialogue_block(spec, shot, speech_mode=speech_mode, zh=zh)
    entry_head, exit_tail = _state_clauses(shot, zh)
    performance = ""
    if spec.get("performance_lock") is True:
        performance = PERFORMANCE_LOCK_CN if zh else PERFORMANCE_LOCK_EN
        cast_map = spec.get("cast") or {}
        has_person = any((cast_map.get(c) or {}).get("name") for c in shot_cast_ids(shot))  # 场景/道具块没有 name
        if has_person and not _shot_looks_at_camera(shot):
            performance += GAZE_LOCK_CN if zh else " " + GAZE_LOCK_EN   # 英文两句之间要一个空格，中文句号后不用
    entry_text, exit_text = entry_exit_text(shot, zh)   # 结构化边界渲染后的正文也流进方言
    return {
        "zh": zh,
        "entry": entry_text,
        "entry_head": entry_head,
        "axis": axis_clause(spec, shot, zh),
        "motion": str(shot.get("motion") or ""),
        "beats": _beats_block(shot, zh).strip(),
        "dialogue_block": dialogue_block,
        "lines": _dialogue_lines(spec, shot, zh=zh),
        "offscreen": offscreen,
        "has_dialogue": has_dialogue,
        "camera": camera.strip(),
        "sfx": sfx,
        "sfx_line": sfx_line,
        "locks": lock_clause(spec, shot_id),
        "lock": lock.strip(),
        "readable_text": readable_text_video_clause(readable_text_entries(shot), zh),
        "withhold": withhold_clause(spec, shot_id, zh, kind="video"),
        "form_budget": _form_budget(spec),   # 形态卡的运动预算句（DESIGN §31）；没写 form = 空串
        "performance": performance.strip(),
        "talk_lock": ((TALK_LOCK_CN if zh else TALK_LOCK) if has_dialogue else ""),
        "exit": exit_text,
        "exit_tail": exit_tail,
    }


def _dialogue_lines(spec: Mapping[str, Any], shot: Mapping[str, Any], *, zh: bool) -> list[dict[str, str]]:
    """方言渲染用的结构化台词：谁、说什么、什么语气、什么声线。只在 dialogue_prompt=auto 时给。"""

    mode = str(shot.get("dialogue_prompt") or spec.get("dialogue_prompt") or "").strip().lower()
    lines = [l for l in (shot.get("lines") or ()) if isinstance(l, Mapping)]
    if mode != "auto" or not lines:
        return []
    cast = spec.get("cast") or {}
    voices = spec.get("voice_cast") or {}
    key = "zh" if zh else ("en" if any("en" in l for l in lines) else "zh")
    out: list[dict[str, str]] = []
    for line in lines:
        text = str(line.get(key) or line.get("zh") or line.get("en") or "").strip()
        if not text:
            continue
        who = str(line.get("who") or "").strip()
        member = cast.get(who) or {}
        out.append({
            "who": who,
            "name": str(member.get("name") or who),
            "text": text,
            "tone": str(line.get("tone") or "").strip(),
            "voice": str(member.get("voice") or voices.get(who) or "").strip(),
            "lang": "zh" if (key == "zh" or not line.get("en")) else "en",
        })
    return out


def compile_video_prompt(spec: Mapping[str, Any], shot_id: str) -> str:
    from .dialects import RENDERERS, resolve_dialect

    parts = video_prompt_parts(spec, shot_id)
    dialect = resolve_dialect(spec)
    renderer = RENDERERS.get(dialect)
    if renderer is not None:
        return renderer(parts, spec)
    return (
        parts["entry_head"]
        + (parts["axis"] + " " if parts["axis"] else "")
        + parts["motion"]
        + (" " + parts["beats"] if parts["beats"] else "")
        + parts["dialogue_block"]
        + (" " + parts["camera"] if parts["camera"] else "")
        + parts["sfx_line"]
        + (" " + parts["locks"] if parts["locks"] else "")
        + (" " + parts["lock"] if parts["lock"] else "")
        + (" " + parts["readable_text"] if parts["readable_text"] else "")
        + (" " + parts["withhold"] if parts.get("withhold") else "")
        + (" " + parts["form_budget"] if parts.get("form_budget") else "")
        + (" " + parts["performance"] if parts["performance"] else "")
        + parts["talk_lock"]
        + parts["exit_tail"]
    )


def _auto_dialogue_block(
    spec: Mapping[str, Any], shot: Mapping[str, Any], *, speech_mode: str, zh: bool
) -> str:
    mode = str(shot.get("dialogue_prompt") or spec.get("dialogue_prompt") or "").strip().lower()
    lines = [l for l in (shot.get("lines") or ()) if isinstance(l, Mapping)]
    if mode != "auto" or not lines:
        return ""
    cast = spec.get("cast") or {}
    voices = spec.get("voice_cast") or {}
    who = str(lines[0].get("who") or "").strip()
    member = cast.get(who) or {}
    name = str(member.get("name") or who or ("说话人" if zh else "the speaker"))
    voice = str(member.get("voice") or voices.get(who) or "").strip()
    key = "zh" if zh else ("en" if any("en" in l for l in lines) else "zh")
    # 每句可带 tone：语气 / 音量 / 语速 / 当下状态（说话要符合场景和氛围，
    # 不能全集一个声线）。写在台词后面的括号里，紧跟原句，模型最容易对上。
    pairs = [
        (str(l.get(key) or l.get("zh") or l.get("en") or "").strip(), str(l.get("tone") or "").strip())
        for l in lines
    ]
    pairs = [(t, tone) for t, tone in pairs if t]
    texts = [t for t, _ in pairs]
    offscreen = speech_mode in {"narration", "offscreen_narration"}
    if zh:
        quoted = "，然后：".join(f"「{t}」" + (f"（{tone}）" if tone else "") for t, tone in pairs)
        speaker = f"画外的{name}（不在画面里）" if offscreen else name
        voice_part = f"——{voice}" if voice else ""
        mouth = "" if offscreen else "嘴型与台词一致。"
        return (f" 台词：{speaker}用普通话说：{quoted}{voice_part}。{mouth}"
                "每个字都口齿清楚、发音标准、正常音量以上，不含糊、不吞字。"
                "本镜只有这几句话——没有其他对白、没有旁白、没有配乐、没有背景人声。台词必须在开头 0.8 秒内开口。")
    quoted = ", then: ".join(f'"{t}"' + (f" ({tone})" if tone else "") for t, tone in pairs)
    speaker = f"{name} (off-camera, not in frame)" if offscreen else name
    voice_part = f" - {voice}" if voice else ""
    mouth = "" if offscreen else " Lip movement matches the words."
    return (f" LINES: {speaker} says: {quoted}{voice_part}.{mouth}"
            " Every word is articulated clearly at normal volume or louder - no mumbling, no swallowed syllables."
            " Those are the ONLY words spoken in the shot - no other dialogue, no narration, no music, no background chatter."
            " The first line must BEGIN within the first 0.8 second.")


def frame_request_data(spec: Mapping[str, Any], shot_id: str) -> dict[str, Any]:
    shot = _shot(spec, shot_id)
    video_params = spec.get("image_params") or {}
    return {
        "name": f"{spec.get('id', 'story')}-{shot_id}-frame",
        "prompt": compile_frame_prompt(spec, shot_id),
        "negative_prompt": compile_negative_prompt(spec, shot_id),
        "model": str(video_params.get("model") or "gpt-image-2"),
        "aspect_ratio": str(video_params.get("aspect_ratio") or "9:16"),
        "image_size": str(video_params.get("image_size") or "2K"),
        # Seedream 不认 gpt-image 专属的 quality 参数，传了直接 HTTP 400；
        # 其他模型保持原来的默认 high。
        "quality": (
            None if str(video_params.get("model") or "").startswith(("doubao-seedream", "@"))
            else str(video_params.get("quality") or "high")
        ),
        "ref_frame": shot.get("ref_frame"),
        "cast_assets": list(frame_reference_asset_ids(spec, shot_id)),
        "prop_ref": spec.get("prop_ref"),
        # 尾帧续接（DESIGN §30）只在声明了才进指纹，老项目的 request_hash 一个字节不变
        **({"ref_tail": dict(shot["ref_tail"])} if isinstance(shot.get("ref_tail"), Mapping) else {}),
    }


def video_request_data(spec: Mapping[str, Any], shot_id: str) -> dict[str, Any]:
    shot = _shot(spec, shot_id)
    parameters = spec.get("video_params") or {}
    extra_params = {
        key: parameters[key]
        for key in ("count", "aigc_watermark", "target_fps", "seed")
        if key in parameters
    }
    # 参考音频（DESIGN §33）：只在声明了才进指纹；运行时提交前把路径换成素材令牌，指纹里始终是 spec 写的相对路径
    if str(parameters.get("reference_audio") or "").strip():
        extra_params["reference_audio"] = str(parameters["reference_audio"]).strip()
    return {
        "name": f"{spec.get('id', 'story')}-{shot_id}-video",
        "prompt": compile_video_prompt(spec, shot_id),
        "model": str(parameters.get("model") or "doubao-seedance-2-0-260128"),
        "duration": int(shot["dur"]),
        "request_type": "first_frame",
        "aspect_ratio": str(parameters.get("aspect_ratio") or "9:16"),
        "resolution": str(parameters.get("resolution") or "1080p"),
        "generate_audio": bool(parameters.get("generate_audio", True)),
        "extra_params": extra_params,
    }


__all__ = [
    "compile_frame_prompt",
    "compile_negative_prompt",
    "compile_video_prompt",
    "video_prompt_parts",
    "shot_lock_surfaces",
    "lock_clause",
    "frame_reference_asset_ids",
    "shot_cast_ids",
    "boundary_text",
    "entry_exit_text",
    "axis_clause",
    "AXIS_SIDES",
    "withhold_entries",
    "active_withholds",
    "withhold_clause",
    "cast_block_text",
    "cast_anchor_asset",
    "REFERENCE_ASPECTS",
    "reference_slot_plan",
    "reference_role_entries",
    "reference_roles_by_slot",
    "reference_roles_clause",
    "frame_request_data",
    "video_request_data",
]
