"""后期层与台词识别闸：桌面版里把流水线走完的最后两步。

* ``GET  /api/episodes/storyvideo/{id}/post-layers``  后期块（旁白、插卡、画面文字）+ 上次 ASR 报告 + 后期清单 + 就绪检查
* ``PUT  /api/episodes/storyvideo/{id}/post-layers``  改后期块；未冻结直接写，已冻结走 ``amend``（只允许后期块变化）
* ``POST /api/episodes/storyvideo/{id}/asr-qc``       台词识别闸（faster-whisper，本地，不花钱），后台任务
* ``POST /api/episodes/storyvideo/{id}/post``         后期合成（旁白 TTS + 插卡 + 画面文字 + 混音，本地 ffmpeg），后台任务
* ``GET  /api/tasks/{task_id}``                        轮询

两个长任务都只是把 CLI 的 ``asr-qc`` / ``post`` 包成接口，不重新实现；判定规则、
时间线、混音全在 ``storyvideo.asr_qc`` / ``storyvideo.post_layers``。
"""

from __future__ import annotations

import copy
import importlib.util
import math
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from genkit.store import RunCollisionError
from storyvideo.asr_qc import failed_shot_ids, retake_reason, run_asr_qc
from storyvideo import overlays, subtitles
from storyvideo.assembly import AssemblyError, delivery_settings
from storyvideo.post_layers import DUB_MODES, compose_post, dubbing_plan, font_status, music_file_problem, music_tracks
from storyvideo.post_layers import validate_silence, validate_voice_over_extras
from storyvideo.post_layers import CARD_MOTIONS, LABEL_MAX_CHARS, LABEL_POSITIONS, START_ANCHOR, card_lines, label_text
from storyvideo.post_layers import INSERT_LAYOUTS, INSERT_MAX_SECONDS, UI_SIZE
from storyvideo.runtime import POST_ONLY_KEYS, StoryVideoRunner
from storyvideo.spec import SpecError
from storyvideo.spec import load_spec as load_storyvideo_spec
from storyvideo.spec import normalize_spec as normalize_storyvideo_spec
from storyvideo.state import atomic_write_json, read_json

from .. import tasks
from ..deps import (PROJECT_ROOT, RUNNER_ERRORS, is_locked, post_output_intact, read_json_bom, resolve_storyvideo_path,
                    spec_not_found_detail)
from ..humanize import humanize
from ..uploads import UploadContentError, accept_audio

router = APIRouter(tags=["post"])

EDITABLE_KEYS = ("voice_over", "post_graphics", "design_notes", "subtitle", "delivery")
# delivery 只收交付面遮挡区两个键（DESIGN §27）；宽高/帧率是装配合同，不在这里改
DELIVERY_EDITABLE = overlays.OVERLAY_KEYS


def _asr_report(spec: dict[str, Any]) -> dict[str, Any] | None:
    path = Path(spec["output_root"]) / "manifests" / "asr_qc.json"
    return read_json(path, missing=None) if path.is_file() else None


def _post_manifest(spec: dict[str, Any]) -> dict[str, Any] | None:
    path = Path(spec["output_root"]) / "manifests" / "post.json"
    if not path.is_file():
        return None
    manifest = read_json(path, missing=None) or {}
    output = Path(str(manifest.get("output") or ""))
    # 文件在、而且是清单描述的那一份（字节数对得上）才算有成片：改成原子替换之前留下的残片不能报成成片
    manifest["output_exists"] = output.is_file() and post_output_intact(Path(spec["output_root"]), output)
    return manifest


def readiness(spec: dict[str, Any]) -> dict[str, Any]:
    output_root = Path(spec["output_root"])
    shot_ids = [str(s["id"]) for s in spec.get("shots", ()) if isinstance(s, dict)]
    missing = [sid for sid in shot_ids if not (output_root / "02_video" / f"{sid}.mp4").is_file()]
    vo = spec.get("voice_over") or {}
    pg = spec.get("post_graphics") or {}
    # 合成用的是 post_graphics.fonts 覆盖后的字体：就绪检查也按它查，别显示「✓ 中文字体」然后合成才报字体不可用
    fonts = font_status(pg.get("fonts") if isinstance(pg.get("fonts"), dict) else None)
    return {
        "ffmpeg_ok": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        "asr_available": importlib.util.find_spec("faster_whisper") is not None,
        "fonts_ok": fonts["ok"],
        "fonts_missing": fonts["missing"],
        "videos_total": len(shot_ids),
        "missing_videos": missing,
        # 只有角标（全程「AI 生成」）也要走后期合成才叠得上
        "has_post_layers": bool(vo.get("lines") or pg.get("phone_ui_inserts") or pg.get("notebook_text") or pg.get("labels")),
        "dubbing": dubbing_plan(spec),
        # reason：用不了的原因（不是数据目录里的相对路径 / 不在 content/ 里 / 文件不存在），界面照着说
        "music": [{"label": m["label"], "file": m.get("file", ""), "missing": m["missing"], "start": m["start"], "end": m["end"],
                   "reason": m.get("reason", "")}
                  for m in music_tracks(spec, project_root=PROJECT_ROOT)],
    }


def _load(episode_id: str) -> tuple[Path, dict[str, Any]]:
    try:
        path, _root = resolve_storyvideo_path(episode_id)
        spec = load_storyvideo_spec(episode_id, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(404, spec_not_found_detail(exc, episode_id)) from exc
    return path, spec


@router.get("/api/episodes/storyvideo/{episode_id}/post-layers")
def get_post_layers(episode_id: str) -> dict[str, Any]:
    path, spec = _load(episode_id)
    raw = read_json_bom(path)
    return {
        "id": episode_id,
        "locked": is_locked(spec["output_root"]),
        "voice_over": raw.get("voice_over") or {},
        "post_graphics": raw.get("post_graphics") or {},
        "design_notes": raw.get("design_notes") or "",
        "subtitle": raw.get("subtitle") or {},
        "subtitle_effective": subtitles.effective_style(raw.get("subtitle") or {}),
        "subtitle_defaults": dict(subtitles.DEFAULT_STYLE),
        "fonts_available": subtitles.available_fonts(),
        "overlays": overlays.payload(raw.get("delivery") or {}),
        "shot_ids": [str(s["id"]) for s in spec.get("shots", ()) if isinstance(s, dict)],
        "asr": _asr_report(spec),
        "post": _post_manifest(spec),
        "readiness": readiness(spec),
        "editable_keys": list(EDITABLE_KEYS),
        "post_only_keys": sorted(POST_ONLY_KEYS),
    }


class PostLayersEdit(BaseModel):
    voice_over: dict[str, Any] | None = None
    post_graphics: dict[str, Any] | None = None
    design_notes: str | None = None
    subtitle: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None   # 只认 overlay_preset / overlay_regions


def _merge_delivery(current: Any, patch: dict[str, Any]) -> dict[str, Any]:
    """把遮挡区两个键合进现有 delivery：空串 / None / 空数组 = 删掉这个键；别的键原样保留。"""

    merged = dict(current) if isinstance(current, dict) else {}
    for key in DELIVERY_EDITABLE:
        if key not in patch:
            continue
        value = patch[key]
        if value in (None, "", []):
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
# 插卡 id：中文照收（界面让人随便起名），只拦路径分隔符、Windows 文件名保留字符、% 和控制字符
INSERT_ID_MAX = 64
INSERT_ID_FORBIDDEN = frozenset('/\\:*?"<>|%')


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _validate_card(index: int, insert: dict[str, Any]) -> list[str]:
    """纯色字幕卡（layout=card）的字段：要有字；底色 / 字色是 #RRGGBB；motion 只认 push / none。别的版式不查这些键。"""

    if str(insert.get("layout") or "") != "card":
        return []
    errors: list[str] = []
    if not any(card_lines(insert)):
        errors.append(f"phone_ui_inserts[{index}] 是字幕卡，要写 lines 或 screen")
    for key in ("background", "color"):
        value = insert.get(key)
        if value is not None and not (isinstance(value, str) and _HEX_COLOR.fullmatch(value)):
            errors.append(f"phone_ui_inserts[{index}].{key} 要是 #RRGGBB 形式的颜色，现在是「{value}」")
    motion = insert.get("motion")
    if motion is not None and motion not in CARD_MOTIONS:
        errors.append(f"phone_ui_inserts[{index}].motion 只能是 {' / '.join(CARD_MOTIONS)}，现在是「{motion}」")
    return errors


def _validate_labels(labels: Any) -> list[str]:
    """文字角标 post_graphics.labels：文字 1–24 字（可以换行，换行不算字）、位置五选一、起止合理、颜色 #RRGGBB、不透明度 0–1。

    字多字号大、一行放不进画面的不在这里拦：合成时会自动缩字号（post_layers.fit_label_size），manifest 里记 shrunk。"""

    if labels is None:
        return []
    if not isinstance(labels, list):
        return ["post_graphics.labels 必须是数组"]
    errors: list[str] = []
    for i, label in enumerate(labels):
        if not isinstance(label, dict):
            errors.append(f"labels[{i}] 必须是对象")
            continue
        chars = len(label_text(label.get("text")).replace("\n", ""))
        if not chars:
            errors.append(f"labels[{i}].text 不能为空")
        elif chars > LABEL_MAX_CHARS:
            errors.append(f"labels[{i}].text 最多 {LABEL_MAX_CHARS} 个字，现在 {chars} 个字")
        position = label.get("position")
        if position is None or position == "":
            errors.append(f"labels[{i}].position 必填：{' / '.join(LABEL_POSITIONS)} 选一个")
        elif position not in LABEL_POSITIONS:
            errors.append(f"labels[{i}].position 只能是 {' / '.join(LABEL_POSITIONS)}，现在是「{position}」")
        start, end = label.get("start"), label.get("end")
        if start is not None and not (_is_number(start) and float(start) >= 0):
            errors.append(f"labels[{i}].start 要是不小于 0 的秒数")
        if end is not None and not (_is_number(end) and float(end) > float(start if _is_number(start) else 0)):
            errors.append(f"labels[{i}].end 要大于 start，留空表示到片尾")
        size = label.get("size")
        if size is not None and not (_is_number(size) and 12 <= float(size) <= 200):
            errors.append(f"labels[{i}].size 要是 12 到 200 之间的数")
        color = label.get("color")
        if color is not None and not (isinstance(color, str) and _HEX_COLOR.fullmatch(color)):
            errors.append(f"labels[{i}].color 要是 #RRGGBB 形式的颜色，现在是「{color}」")
        opacity = label.get("opacity")
        if opacity is not None and not (_is_number(opacity) and 0 <= float(opacity) <= 1):
            errors.append(f"labels[{i}].opacity 要是 0 到 1 之间的数")
        box = label.get("box")
        if box is not None and not isinstance(box, bool):
            errors.append(f"labels[{i}].box 要是 true 或 false")
    return errors


def _crossfade_seconds(spec: dict[str, Any]) -> float:
    """交付合同里的交叉溶解秒数（video_params + delivery 合并）；delivery 写坏了这里不报（那是合成时的事），按默认算。"""

    try:
        return delivery_settings(spec)[3]
    except AssemblyError:
        return 0.25


def _validate_inserts(inserts: list[Any], shot_ids: set[str], crossfade: float) -> list[str]:
    """插卡：id / after 的引用、版式、时长、文字，以及 after 链的可达性（自引用 / 互相引用的卡从任何镜出发都到不了，
    合成时会被静默丢掉、清单却说有）。"""

    errors: list[str] = []
    insert_ids: set[str] = set()
    # 撞名按不分大小写比：NTFS 上 UI_A / ui_a 是同一个文件名，界面上也分不清
    taken = {sid.casefold() for sid in shot_ids}
    for i, insert in enumerate(inserts):
        if not isinstance(insert, dict):
            errors.append(f"phone_ui_inserts[{i}] 必须是对象")
            continue
        iid = str(insert.get("id") or "").strip()
        if not iid:
            errors.append(f"phone_ui_inserts[{i}] 缺 id")
        elif iid == START_ANCHOR:
            errors.append(f"phone_ui_inserts[{i}] 的 id 不能用保留字「{START_ANCHOR}」")
        elif len(iid) > INSERT_ID_MAX or any(ch in INSERT_ID_FORBIDDEN or ord(ch) < 32 or ord(ch) == 127 for ch in iid):
            # id 以前会拼进文件名和 ffmpeg 的输入路径：../、盘符、% 都出过事（F1 / F6）。现在文件名不再用 id，
            # 这里照样拦——id 还会出现在接口路径（插卡预览）和清单里，写成这样的 id 本来也是误填
            errors.append(f"phone_ui_inserts[{i}] 的 id「{iid}」不能超过 {INSERT_ID_MAX} 个字，也不能含 / \\ : * ? \" < > | % "
                          "或控制字符——改成 UI_2 这样的写法")
        elif iid in insert_ids or iid.casefold() in taken:
            errors.append(f"phone_ui_inserts[{i}] 的 id「{iid}」重复或与镜号撞名")
        insert_ids.add(iid)
        taken.add(iid.casefold())
        layout = insert.get("layout")
        if layout is not None and str(layout) not in INSERT_LAYOUTS:
            errors.append(f"phone_ui_inserts[{i}].layout 只能是 notification / sms / shop / plain / card，现在是「{layout}」")
        # 时长：留空 = 4 秒；写了就得是数，不短于交叉溶解（否则下一项比这张卡先开始、ffmpeg 的 xfade 没帧可溶挂到超时），
        # 不超过上限。0 不当留空——界面显示的是 4s，写 0 的人多半以为是 0 秒
        dur = insert.get("dur")
        if dur not in (None, ""):
            if not (_is_number(dur) and crossfade <= float(dur) <= INSERT_MAX_SECONDS and float(dur) > 0):
                errors.append(f"phone_ui_inserts[{i}].dur 要是大于 0、不小于交叉溶解 {crossfade:g} 秒、不超过 {INSERT_MAX_SECONDS:g} 秒的数"
                              f"（留空 = 4 秒），现在是「{dur}」")
        # 纯文字版式（没写 layout / plain / text）的文字：合成时 render_ui_card 没字就报错，和字幕卡一样在写盘前拦
        if str(layout or "") in ("", "plain", "text") and not any(card_lines(insert)):
            errors.append(f"phone_ui_inserts[{i}] 没有文字：写 lines 或 screen")
        errors.extend(_validate_card(i, insert))
    by_id = {str(x.get("id")): x for x in inserts if isinstance(x, dict) and str(x.get("id") or "").strip()}
    for i, insert in enumerate(inserts):
        if not isinstance(insert, dict):
            continue
        after = str(insert.get("after") or "")
        # __start__ = 插在第一镜之前（片头卡）
        if after not in shot_ids and after not in insert_ids and after != START_ANCHOR:
            errors.append(f"phone_ui_inserts[{i}] 的 after「{after}」既不是镜号也不是别的插卡 id")
            continue
        chain = [str(insert.get("id"))]
        cursor = after
        while cursor in by_id:
            chain.append(cursor)
            if cursor in chain[:-1]:
                errors.append(f"phone_ui_inserts[{i}] 的 after 形成了环（{' → '.join(chain)}），没有接到任何镜上")
                break
            cursor = str(by_id[cursor].get("after") or "")
    return errors


def _validate_notebook(notes: list[Any], shots_by_id: dict[str, dict[str, Any]]) -> list[str]:
    """画面文字：镜号、文字非空、at / until / y / size 的范围（until 还要落在这一镜的计划时长里）。"""

    errors: list[str] = []
    for i, entry in enumerate(notes):
        if not isinstance(entry, dict):
            errors.append(f"notebook_text[{i}] 必须是对象")
            continue
        shot = shots_by_id.get(str(entry.get("shot")))
        if shot is None:
            errors.append(f"notebook_text[{i}] 的 shot「{entry.get('shot')}」不是本集的镜号")
        if not str(entry.get("text") or "").strip():
            errors.append(f"notebook_text[{i}] 缺 text（画面文字）")
        planned = shot.get("dur") if shot is not None else None
        ceiling = float(planned) if _is_number(planned) else None
        at = entry.get("at")
        at_ok = at in (None, "") or (_is_number(at) and float(at) >= 0)
        if not at_ok:
            errors.append(f"notebook_text[{i}].at 要是不小于 0 的秒数，现在是「{at}」")
        elif at not in (None, "") and ceiling is not None and float(at) >= ceiling:
            # 起点就在镜外：显示区间是空的，这行字不会出现
            errors.append(f"notebook_text[{i}].at = {float(at):g}s 不在镜 {entry.get('shot')} 里：这一镜只有 {ceiling:g}s")
            at_ok = False
        until = entry.get("until")
        if until not in (None, ""):
            floor = float(at) if at_ok and at not in (None, "") else 0.0
            if not (_is_number(until) and float(until) > floor and (ceiling is None or float(until) <= ceiling)):
                limit = f"、不超过镜 {entry.get('shot')} 的时长 {ceiling:g}s" if ceiling is not None else ""
                errors.append(f"notebook_text[{i}].until 要大于 at{limit}，现在是「{until}」")
        y = entry.get("y")
        if y not in (None, "") and not (_is_number(y) and 0 <= float(y) <= 1):
            errors.append(f"notebook_text[{i}].y 要是 0 到 1 之间的数，现在是「{y}」")
        size = entry.get("size")
        if size not in (None, "") and not (_is_number(size) and 12 <= float(size) <= 300):
            errors.append(f"notebook_text[{i}].size 要是 12 到 300 之间的数，现在是「{size}」")
    return errors


def _validate_fonts(fonts: Any) -> list[str]:
    """post_graphics.fonts：三种用途各自指向的字体文件必须存在（合成和插卡预览用的就是它，就绪检查也按它查）。"""

    if fonts is None:
        return []
    if not isinstance(fonts, dict):
        return ["post_graphics.fonts 必须是对象：{notebook, ui, subtitle}"]
    errors: list[str] = []
    for kind, path in fonts.items():
        if path in (None, ""):
            continue
        if not isinstance(path, str) or not Path(path).expanduser().is_file():
            errors.append(f"post_graphics.fonts.{kind} 指向的字体文件不存在：{path}")
    return errors


def _validate_post_blocks(spec: dict[str, Any]) -> list[str]:
    """后期块的形状校验——normalize_spec 不管这两块（它们不进付费请求），但写坏了
    post_layers 会在合成时才报错，那时已经等了一轮。这里在写盘前把错拦住：
    旁白句/画面文字引用的镜号必须存在，插卡必须有 id 且 after 指向镜号或另一张插卡（且不成环），
    每个数值字段（插卡 dur、旁白 at、整块声线 rate/pitch、画面文字 at/until/y/size）都在范围里。"""

    errors: list[str] = []
    shots = [s for s in spec.get("shots", ()) if isinstance(s, dict)]
    shot_ids = {str(s.get("id")) for s in shots}
    shots_by_id = {str(s.get("id")): s for s in shots}
    errors.extend(subtitles.validate(spec.get("subtitle")))
    errors.extend(overlays.validate(spec.get("delivery")))
    vo = spec.get("voice_over")
    if vo is not None:
        if not isinstance(vo, dict):
            errors.append("voice_over 必须是对象")
        else:
            lines = vo.get("lines", [])
            if not isinstance(lines, list):
                errors.append("voice_over.lines 必须是数组")
            else:
                for i, line in enumerate(lines):
                    if not isinstance(line, dict):
                        errors.append(f"voice_over.lines[{i}] 必须是对象")
                        continue
                    if not str(line.get("zh") or "").strip():
                        errors.append(f"voice_over.lines[{i}] 缺 zh（旁白文本）")
                    if str(line.get("shot")) not in shot_ids:
                        errors.append(f"voice_over.lines[{i}] 的 shot「{line.get('shot')}」不是本集的镜号")
            # 旁白字幕开关、整块 / 逐句声线、引擎、每句 at：形状规则放在 post_layers 里跟合成挨着，免得两边各写一套
            errors.extend(validate_voice_over_extras(vo, shots))
    pg = spec.get("post_graphics")
    if pg is not None:
        if not isinstance(pg, dict):
            errors.append("post_graphics 必须是对象")
        else:
            inserts = pg.get("phone_ui_inserts", [])
            if not isinstance(inserts, list):
                errors.append("post_graphics.phone_ui_inserts 必须是数组")
            else:
                errors.extend(_validate_inserts(inserts, shot_ids, _crossfade_seconds(spec)))
            errors.extend(_validate_fonts(pg.get("fonts")))
            errors.extend(_validate_labels(pg.get("labels")))
            dub = pg.get("dubbing")
            if dub is not None:
                if not isinstance(dub, dict):
                    errors.append("post_graphics.dubbing 必须是对象")
                else:
                    if str(dub.get("mode") or "off") not in DUB_MODES:
                        errors.append(f"dubbing.mode 只能是 {' / '.join(DUB_MODES)}")
                    for sid in dub.get("shots") or ():
                        if str(sid) not in shot_ids:
                            errors.append(f"dubbing.shots 里的「{sid}」不是本集的镜号")
                    duck = dub.get("duck")
                    if duck is not None and (isinstance(duck, bool) or not isinstance(duck, (int, float)) or not 0 <= float(duck) <= 1):
                        errors.append("dubbing.duck 必须是 0 到 1 之间的数（配音时原声保留的比例）")
            music = pg.get("music")
            if music is not None:
                if not isinstance(music, list):
                    errors.append("post_graphics.music 必须是数组")
                else:
                    for i, track in enumerate(music):
                        if not isinstance(track, dict):
                            errors.append(f"music[{i}] 必须是对象")
                            continue
                        file = str(track.get("file") or "").strip()
                        problem = music_file_problem(file, PROJECT_ROOT) if file else None
                        if not file:
                            errors.append(f"music[{i}] 缺 file（先上传音频）")
                        elif problem:
                            # 跟合成时读文件的那一处同一条规则（music_file_problem）：~、盘符相对、指向外面的链接以前漏过。
                            # 文案直说规则（只收 content/ 下上传的音频）和怎么办；以前套成「要是项目内的相对路径（原因）」，
                            # output/… 这种本来就是项目内相对路径的值读起来自相矛盾
                            errors.append(f"music[{i}] 的 file 用不了：{problem}——只能用上传到数据目录 content/ 下的音频，"
                                          "在「配乐」里重新上传")
                        elif not (PROJECT_ROOT / file).resolve().is_file():
                            errors.append(f"music[{i}] 的文件不存在：{file}")
                        for key, lo, hi in (("start", 0, 3600), ("gain", 0, 2), ("duck", 0, 1), ("fade_in", 0, 30), ("fade_out", 0, 30)):
                            val = track.get(key)
                            if val is None:
                                continue
                            if isinstance(val, bool) or not isinstance(val, (int, float)) or not lo <= float(val) <= hi:
                                errors.append(f"music[{i}].{key} 要是 {lo} 到 {hi} 之间的数")
                        end = track.get("end")
                        if end is not None and (isinstance(end, bool) or not isinstance(end, (int, float)) or float(end) <= float(track.get("start") or 0)):
                            errors.append(f"music[{i}].end 要大于 start（留空 = 到结尾）")
            notes = pg.get("notebook_text", [])
            if not isinstance(notes, list):
                errors.append("post_graphics.notebook_text 必须是数组")
            else:
                errors.extend(_validate_notebook(notes, shots_by_id))
            # 绝对静音区间：镜号、at / dur 的范围、同镜不重叠（实际成片时长合成时再核一次）
            errors.extend(validate_silence(pg.get("silence"), spec.get("shots")))
    return errors


@router.put("/api/episodes/storyvideo/{episode_id}/post-layers")
def put_post_layers(episode_id: str, edit: PostLayersEdit) -> dict[str, Any]:
    """只碰后期块。未冻结：校验后写盘。已冻结：写盘后走 ``amend``——它会核对本次
    改动确实只落在 ``POST_ONLY_KEYS``，否则拒绝并把文件恢复原样。"""

    path, spec = _load(episode_id)
    raw = read_json_bom(path)
    original = copy.deepcopy(raw)
    mutated = copy.deepcopy(raw)
    changed = []
    for key in EDITABLE_KEYS:
        value = getattr(edit, key)
        if value is None:
            continue
        if key == "design_notes":
            if str(value).strip():
                mutated[key] = str(value)
            else:
                mutated.pop(key, None)
        elif key == "delivery":
            unknown = [k for k in value if k not in DELIVERY_EDITABLE]
            if unknown:
                raise HTTPException(400, f"这里只能改交付面遮挡区（{' / '.join(DELIVERY_EDITABLE)}），不能改 delivery 的 {'、'.join(unknown)}")
            merged = _merge_delivery(original.get("delivery"), value)
            if merged:
                mutated[key] = merged
            else:
                mutated.pop(key, None)
        elif key == "subtitle":
            # 整块替换（界面每次把所有键都带上；漏掉的键 = 恢复默认），但 language 例外：没带就沿用原值——
            # normalize_spec 把缺 language 当成 bilingual，会要求每句台词都有 en，报错还指向台词字段，跟改字号毫无关系
            block = dict(value) if isinstance(value, dict) else {}
            current = original.get("subtitle") if isinstance(original.get("subtitle"), dict) else {}
            if "language" not in block and current.get("language"):
                block["language"] = current["language"]
            if block:
                mutated[key] = block
            else:
                mutated.pop(key, None)
        else:
            if value:
                mutated[key] = value
            else:
                mutated.pop(key, None)
        if mutated.get(key) != original.get(key):
            changed.append(key)
    problems = _validate_post_blocks(mutated)
    if problems:
        raise HTTPException(400, "改动没通过校验，没有写盘：" + "；".join(problems))
    try:
        normalize_storyvideo_spec(mutated, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(400, f"改动没通过校验，没有写盘：{humanize(exc)}") from exc
    if not changed:
        return {"status": "unchanged", "changed_keys": [], "locked": is_locked(spec["output_root"])}

    atomic_write_json(path, mutated)
    locked = is_locked(spec["output_root"])
    amend: dict[str, Any] | None = None
    if locked:
        try:
            with StoryVideoRunner(str(PROJECT_ROOT)) as runner:
                amend = runner.amend_post_layers(episode_id)
        except RunCollisionError as exc:
            atomic_write_json(path, original)
            raise HTTPException(409, f"这次改动没被接受，文件已恢复：{humanize(exc)}") from exc
        except RUNNER_ERRORS as exc:
            atomic_write_json(path, original)
            raise HTTPException(400, f"修订失败，文件已恢复：{humanize(exc)}") from exc
    return {"status": (amend or {}).get("status", "saved"), "changed_keys": changed, "locked": locked, "amend": amend}


@router.get("/api/episodes/storyvideo/{episode_id}/post-layers/insert-preview/{insert_id}")
def insert_preview(episode_id: str, insert_id: str):
    """整屏插卡的预览图（跟后期合成用的是同一个渲染函数）。按插卡内容哈希缓存，改了内容自动重画。"""

    import hashlib
    import json as _json

    from fastapi.responses import FileResponse
    from storyvideo.post_layers import DEFAULT_FONTS, PostLayerError, render_ui_card

    path, spec = _load(episode_id)
    raw = read_json_bom(path)
    pg = raw.get("post_graphics") or {}
    insert = next((x for x in (pg.get("phone_ui_inserts") or ()) if isinstance(x, dict) and str(x.get("id")) == insert_id), None)
    if insert is None:
        raise HTTPException(404, f"没有这张插卡：{insert_id}")
    font = str((pg.get("fonts") or {}).get("ui") or DEFAULT_FONTS["ui"])
    # 按交付宽高画（与合成同一尺寸）：横屏项目的预览图也是横的；交付合同写坏了退回 9:16，预览不为此报错
    try:
        size = tuple(delivery_settings(spec)[:2])
    except AssemblyError:
        size = UI_SIZE
    digest = hashlib.sha1(_json.dumps([insert, font, list(size)], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    # 文件名只用内容摘要（摘要里已经含了整张卡，包括 id）：插卡 id 是自由文本，带反斜杠 / 盘符时拼进文件名会写出目录（F1）
    out = Path(spec["output_root"]) / "08_ui_preview" / f"{digest}.png"
    if not out.is_file():
        try:
            render_ui_card(insert, out, font_path=font, size=size)
        except PostLayerError as exc:
            raise HTTPException(425, str(exc)) from exc
    return FileResponse(out, media_type="image/png")


class OffscreenRequest(BaseModel):
    shots: list[str] | None = None   # None = 当前所有配音镜里还不是画外的


def offscreen_targets(spec: dict[str, Any], shots: list[str] | None) -> list[str]:
    plan = dubbing_plan(spec)
    wanted = shots if shots is not None else plan["shots"]
    by_id = {str(s.get("id")): s for s in spec.get("shots", ()) if isinstance(s, dict)}
    return [sid for sid in wanted if sid in by_id and str(by_id[sid].get("speech_mode") or "") not in {"narration", "offscreen_narration"}]


@router.post("/api/episodes/storyvideo/{episode_id}/dubbing/offscreen")
def set_dubbed_shots_offscreen(episode_id: str, req: OffscreenRequest) -> dict[str, Any]:
    """把配音镜的说话方式一键切成"画外"（speech_mode=offscreen_narration）：视频提示词不再要求嘴型
    跟台词对上，配音叠上去就不会穿帮。speech_mode 进视频提示词，是生成字段——草稿直接写；
    制作中走「保存并预告费用」（/revise-edit，mutation kind=dubbing_offscreen）。"""

    path, spec = _load(episode_id)
    targets = offscreen_targets(spec, req.shots)
    if not targets:
        return {"changed": [], "note": "没有需要改的镜：要么没开配音，要么配音镜已经是画外说话方式"}
    if is_locked(spec["output_root"]):
        raise HTTPException(409, "项目已开始制作，说话方式会改视频提示词——用「保存并预告费用」在新版本里改（前端会自动走这条路）")
    raw = read_json_bom(path)
    for shot in raw.get("shots", ()):
        if isinstance(shot, dict) and str(shot.get("id")) in targets:
            shot["speech_mode"] = "offscreen_narration"
    try:
        normalize_storyvideo_spec(raw, project_root=PROJECT_ROOT)
    except SpecError as exc:
        raise HTTPException(400, f"改动没通过校验，没有写盘：{humanize(exc)}") from exc
    atomic_write_json(path, raw)
    return {"changed": targets}


class AsrRequest(BaseModel):
    only: list[str] | None = None
    threshold: float = 0.3
    model: str = "small"
    record_retakes: bool = True
    include_stray: bool = False


@router.post("/api/episodes/storyvideo/{episode_id}/asr-qc")
def start_asr_qc(episode_id: str, req: AsrRequest) -> dict[str, Any]:
    _path, spec = _load(episode_id)
    ready = readiness(spec)
    if not ready["asr_available"]:
        if getattr(sys, "_MEIPASS", None):
            # 打包版没有 pip、不认 PYTHONPATH：给的是设置页那条能照做的话（借本机同版本 Python 装进外部 site 目录）
            from .assist import extra_site_dir, frozen_install_hint

            raise HTTPException(425, frozen_install_hint(extra_site_dir()))
        raise HTTPException(425, "台词识别闸需要 faster-whisper：在运行本程序的 Python 里执行 pip install faster-whisper，"
                                 "首次运行会下载识别模型（small 约 500MB）。")
    targets = req.only or [str(s["id"]) for s in spec.get("shots", ()) if isinstance(s, dict)]
    missing = [sid for sid in targets if sid in ready["missing_videos"]]
    if len(missing) == len(targets):
        raise HTTPException(425, f"这些镜次还没有视频，没东西可识别：{', '.join(missing)}")
    if not is_locked(spec["output_root"]) and req.record_retakes:
        raise HTTPException(409, "项目还没锁定，无法记录重摇裁决；先锁定，或者关掉「自动记重摇」只看报告。")

    def work() -> dict[str, Any]:
        report = run_asr_qc(spec, shots=req.only, threshold=req.threshold, model=req.model)
        failing = failed_shot_ids(report, include_stray=req.include_stray)
        recorded: list[str] = []
        errors: list[str] = []
        if req.record_retakes and failing:
            rows = {r["shot_id"]: r for r in report["rows"]}
            with StoryVideoRunner(str(PROJECT_ROOT)) as runner:
                for shot_id in failing:
                    try:
                        runner.review(episode_id, "videos", "retake", shots=[shot_id],
                                      reason=retake_reason(rows[shot_id]), reviewer="asr-qc")
                        recorded.append(shot_id)
                    except RUNNER_ERRORS as exc:
                        errors.append(f"{shot_id}: {exc}")
        return {"summary": report["summary"], "rows": report["rows"], "failing": failing,
                "recorded_retakes": recorded, "record_errors": errors, "threshold": req.threshold, "model": req.model}

    try:
        task_id = tasks.start("asr-qc", episode_id, work, params=req.model_dump())
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id}


@router.post("/api/episodes/storyvideo/{episode_id}/post")
def start_post(episode_id: str) -> dict[str, Any]:
    _path, spec = _load(episode_id)
    ready = readiness(spec)
    if not ready["ffmpeg_ok"]:
        raise HTTPException(425, "后期合成需要 ffmpeg / ffprobe 在 PATH 里（界面顶部有下载提示）。")
    if ready["missing_videos"]:
        raise HTTPException(425, f"还有镜次没有视频，后期合成要求全部镜次都已出片：{', '.join(ready['missing_videos'])}")
    if not ready["fonts_ok"]:
        raise HTTPException(425, f"没找到中文字体（{', '.join(ready['fonts_missing'])}）——在 spec 的 post_graphics.fonts 里指定字体文件路径。")
    missing_tts = (ready.get("dubbing") or {}).get("missing_tts_cast") or {}
    if missing_tts:
        detail = "；".join(f"{who}（{', '.join(sids)}）" for who, sids in missing_tts.items())
        raise HTTPException(425, f"台词配音已开启，但这些说话角色没有绑定 TTS 音色：{detail}——去角色页从音色库绑一个，或关掉配音。")

    def work() -> dict[str, Any]:
        manifest = compose_post(spec, project_root=PROJECT_ROOT)
        output = Path(str(manifest["output"]))
        return {
            "output": str(output),
            "duration_seconds": manifest["duration_seconds"],
            "timeline": manifest["timeline"],
            "vo_count": len(manifest.get("vo") or []),
            "ui_inserts": len(manifest.get("ui_inserts") or []),
        }

    try:
        task_id = tasks.start("post", episode_id, work)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id}


@router.get("/api/tasks/{task_id}")
def get_task(task_id: str, debug: bool = False) -> dict[str, Any]:
    """``debug=true`` 才带 traceback（本机路径 + Python 堆栈只给开发者看，界面默认拿到的是人话 error）。"""

    item = tasks.get(task_id)
    if item is None:
        raise HTTPException(404, f"没有这个任务：{task_id}")
    return tasks.public_record(item, debug=debug)


@router.get("/api/tasks")
def list_tasks(episode_id: str | None = None, module: str | None = None, debug: bool = False) -> list[dict[str, Any]]:
    """``module`` 按功能模块过滤（DESIGN §38）：不认识的模块 id 直接 404，而不是静默返回空列表。"""

    from ..modules import get_module

    if module and get_module(module) is None:
        raise HTTPException(404, f"没有这个模块：{module}")
    return [tasks.public_record(item, debug=debug) for item in tasks.list_tasks(episode_id, module)]


# ---------------------------------------------------------------------------- 配乐文件


MUSIC_SUFFIXES = {"mp3": ".mp3", "wav": ".wav", "m4a": ".m4a", "aac": ".aac", "ogg": ".ogg", "flac": ".flac"}
MAX_MUSIC_BYTES = 40 * 1024 * 1024


class MusicUpload(BaseModel):
    filename: str
    data_base64: str


@router.post("/api/episodes/storyvideo/{episode_id}/music")
def upload_music(episode_id: str, req: MusicUpload) -> dict[str, Any]:
    """配乐文件存进 content/assets/<项目>/music/，返回项目相对路径；spec 里 post_graphics.music[].file 记它。
    只存文件不写 spec（保存配乐设置时一起写），所以制作中也能用。"""

    import base64
    import hashlib

    _load(episode_id)
    try:
        data = base64.b64decode(req.data_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"音频数据不是合法的 base64：{exc}") from exc
    if not data:
        raise HTTPException(400, "音频为空")
    if len(data) > MAX_MUSIC_BYTES:
        raise HTTPException(413, f"音频超过 {MAX_MUSIC_BYTES // 1024 // 1024}MB")
    # 按内容认（uploads.accept_audio）：.mp3 里塞任意字节以前也能存，到合成时才以一屏滤镜图的形式炸出来；而 AIFF 这类
    # 读得出的音频反倒因为扩展名被拒。现在 ffprobe 读得出音频流、时长 > 0 就收（容器 / 编码不在范围内的只取音轨转成
    # wav / m4a，转出来超过上限 413），后缀按真实格式
    try:
        accepted = accept_audio(data, req.filename, allowed=tuple(MUSIC_SUFFIXES.values()), max_bytes=MAX_MUSIC_BYTES)
    except UploadContentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    from genkit.ids import slugify

    stem = slugify(Path(req.filename).stem, fallback="music")
    digest = hashlib.sha256(data).hexdigest()[:8]
    music_dir = PROJECT_ROOT / "content" / "assets" / episode_id / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    target = music_dir / f"{stem}-{digest}{accepted.suffix}"
    target.write_bytes(accepted.data)
    relative = target.relative_to(PROJECT_ROOT).as_posix()
    return {"file": relative, "bytes": len(accepted.data), "url": f"/api/episodes/storyvideo/{episode_id}/music/{target.name}"}


@router.get("/api/episodes/storyvideo/{episode_id}/music/{name}")
def get_music(episode_id: str, name: str) -> FileResponse:
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(404, "没有这个文件")
    path = PROJECT_ROOT / "content" / "assets" / episode_id / "music" / name
    if not path.is_file():
        raise HTTPException(404, "没有这个文件")
    return FileResponse(path)
