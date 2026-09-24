"""「制作中也能改」：对已开始制作的项目做一次编辑 → 算出哪些镜的首帧/视频会作废 → 复制成新版本，
把还能用的产物（及其审片裁决）带过去，只重新生成受影响的镜。

冻结承诺不变：旧版本一字不动，账本照旧；新版本是一份新 spec + 新 output_root，用 ``import_legacy``
把复用的产物登记进新账本。用户看到的是"保存并预告费用"一个动作，背后是 revision + 认领 + 只出新镜。

影响判定不猜字段名——直接比较每一镜编译后的首帧请求与视频请求（提示词、负向词、模型、画幅、参考图集合、
时长、分辨率……），任何一项变了就算变。参考链会传染：参考镜的首帧变了，引用它的镜也要重出。
"""

from __future__ import annotations

import copy
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

from storyvideo.prompts import CameraError, frame_request_data, video_request_data
from storyvideo.qc import load_qc
from storyvideo.runtime import StoryVideoRunner
from storyvideo.spec import SpecError, normalize_spec
from storyvideo.state import atomic_write_json, write_qc

from . import deps


class ReviseError(ValueError):
    pass


MUTATION_KINDS = (
    "shot_edit", "shot_create", "shot_delete", "shot_order",
    "cast_edit", "cast_create", "cast_delete", "cast_voice", "cast_asset",
    "dubbing_offscreen", "globals",
)
GLOBAL_KEYS = ("frame_style", "scene_anchor", "camera", "neg", "video_lock", "video_tail_lock", "camera_profile",
               "prompt_language", "dialogue_prompt", "performance_lock", "visual_style", "image_params", "video_params",
               # 单集契约与系列（DESIGN §32）：不进提示词，制作中改也不会让任何镜重出
               "episode_contract", "series",
               # 形态卡（DESIGN §31）：运动预算句进每镜视频提示词，制作中改会让所有镜的视频重出（impact 自己会算出来）
               "form",
               # 题材卡（DESIGN §49）：不进提示词，只给「按题材审稿」对照；制作中改不会让任何镜重出
               "genre")


def apply_mutation(raw: dict[str, Any], mutation: Mapping[str, Any]) -> dict[str, Any]:
    """把一次编辑应用到原始 spec 的深拷贝上。复用草稿态各接口的合并函数，语义完全一致。"""

    from genkit import voices as V

    from .routers import episodes as ep_router
    from .routers import post as post_router
    from .routers import project as project_router

    kind = str(mutation.get("kind") or "")
    if kind not in MUTATION_KINDS:
        raise ReviseError(f"不认识的编辑类型：{kind}")
    out = copy.deepcopy(raw)
    payload = {k: v for k, v in mutation.items() if k != "kind"}
    shots = out.setdefault("shots", [])
    by_id = {str(s.get("id")): s for s in shots if isinstance(s, dict)}

    if kind == "shot_edit":
        sid = str(payload.pop("shot_id", ""))
        if sid not in by_id:
            raise ReviseError(f"没有这一镜：{sid}")
        ep_router._apply_shot_fields(by_id[sid], ep_router.ShotEdit(**payload))
    elif kind == "shot_create":
        req = ep_router.ShotCreate(**payload)
        sid = ep_router.allocate_shot_id(by_id.keys(), req)
        if sid in by_id:
            raise ReviseError(f"已经有一镜叫「{sid}」")
        new_shot: dict[str, Any] = {"id": sid, "dur": req.dur or 5, "cast": [], "lines": []}
        ep_router._apply_shot_fields(new_shot, req)
        for key in ep_router._SHOT_REQUIRED_TEXT:
            if not str(new_shot.get(key) or "").strip():
                raise ReviseError(f"新镜需要填 {key}")
        new_shot.setdefault("generation_role", "source_clip")
        new_shot.setdefault("request_type", "first_frame")
        if req.after is None:
            shots.append(new_shot)
        elif req.after == "":
            shots.insert(0, new_shot)
        else:
            idx = next((i for i, s in enumerate(shots) if isinstance(s, dict) and s.get("id") == req.after), None)
            if idx is None:
                raise ReviseError(f"没有这一镜：{req.after}")
            shots.insert(idx + 1, new_shot)
    elif kind == "shot_delete":
        sid = str(payload.get("shot_id", ""))
        if sid not in by_id:
            raise ReviseError(f"没有这一镜：{sid}")
        if len(shots) <= 1:
            raise ReviseError("至少要保留一镜")
        out["shots"] = [s for s in shots if not (isinstance(s, dict) and s.get("id") == sid)]
    elif kind == "shot_order":
        order = [str(x) for x in payload.get("order") or ()]
        if sorted(order) != sorted(by_id) or len(order) != len(by_id):
            raise ReviseError("order 必须恰好包含现有的每一镜各一次")
        out["shots"] = [by_id[sid] for sid in order]
    elif kind in ("cast_edit", "cast_create", "cast_delete", "cast_voice", "cast_asset"):
        cast = out.setdefault("cast", {})
        if kind == "cast_create":
            req = project_router.CastCreate(**payload)
            try:
                cid = project_router.allocate_cast_id(cast, req)
            except ValueError as exc:
                raise ReviseError(str(exc)) from exc
            except KeyError as exc:
                raise ReviseError(f"已经有一个「{exc.args[0]}」") from exc
            member = project_router._apply_edit({}, req)
            member.setdefault("kind", "character" if (member.get("name") or member.get("voice")) else "scene")
            cast[cid] = member
        else:
            cid = str(payload.pop("cast_id", ""))
            if cid not in cast or not isinstance(cast[cid], dict):
                raise ReviseError(f"没有这个角色/场景：{cid}")
            if kind == "cast_edit":
                cast[cid] = project_router._apply_edit(dict(cast[cid]), project_router.CastEdit(**payload))
            elif kind == "cast_delete":
                used = project_router.shots_using(out, cid)
                if used:
                    raise ReviseError(f"「{cid}」还被这些镜引用：{', '.join(used)}")
                del cast[cid]
            elif kind == "cast_voice":
                vid = payload.get("voice_id")
                profile = V.get_profile(str(vid)) if vid else None
                if vid and profile is None:
                    raise ReviseError(f"音色库里没有：{vid}")
                cast[cid] = V.bind_cast(cast[cid], profile, lang=str(out.get("prompt_language") or "zh"))
            else:
                asset = str(payload.get("asset") or "").strip()
                if asset:
                    # 跟草稿态 PUT cast 同一条边界：只收项目内相对路径，不然新版本的 spec 里就能写进任意本机文件
                    try:
                        cast[cid]["asset"] = project_router.normalize_asset_path(asset, "asset")
                    except ValueError as exc:
                        raise ReviseError(str(exc)) from exc
                else:
                    cast[cid].pop("asset", None)
    elif kind == "dubbing_offscreen":
        targets = post_router.offscreen_targets(normalize_spec(out, project_root=deps.PROJECT_ROOT), payload.get("shots"))
        for s in shots:
            if isinstance(s, dict) and str(s.get("id")) in targets:
                s["speech_mode"] = "offscreen_narration"
    elif kind == "globals":
        for key, value in (payload.get("values") or {}).items():
            if key not in GLOBAL_KEYS:
                raise ReviseError(f"不允许改的全局字段：{key}")
            if value is None or value == "":
                out.pop(key, None)
            elif key == "form":
                # 形态：id / 中文名 / 英文名 / 别名都认，落盘统一成 id；不认识的在这里就拦（和起草接口同一句话）
                from storyvideo.forms import normalize_form

                try:
                    out[key] = normalize_form(value)
                except ValueError as exc:
                    raise ReviseError(f"不认识的形态：{value}") from exc
            elif key == "genre":
                # 题材：id / 中文名 / 别名都认，落盘统一成 id；不认识的在这里就拦（和起草接口同一句话）
                from storyvideo.scriptgen import find_genre

                card = find_genre(value) if isinstance(value, str) else None
                if card is None:
                    raise ReviseError(f"不认识的题材：{value}")
                out[key] = str(card["id"])
            else:
                out[key] = value
    return out


def _request_keys(spec: Mapping[str, Any], shot_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    frame = dict(frame_request_data(spec, shot_id)); frame.pop("name", None)
    video = dict(video_request_data(spec, shot_id)); video.pop("name", None)
    return frame, video


def impact(old_norm: Mapping[str, Any], new_norm: Mapping[str, Any]) -> dict[str, Any]:
    """比较两份归一化 spec：哪些镜的首帧/视频请求变了（含参考链传染），哪些产物还能复用。"""

    old_ids = {str(s["id"]) for s in old_norm.get("shots", ())}
    new_shots = [s for s in new_norm.get("shots", ()) if isinstance(s, dict)]
    new_ids = [str(s["id"]) for s in new_shots]
    old_root = Path(str(old_norm["output_root"]))
    frame_changed: set[str] = set()
    video_changed: set[str] = set()
    reasons: dict[str, str] = {}
    for wave in new_norm.get("waves") or [new_ids]:
        for sid in wave:
            shot = next(s for s in new_shots if str(s["id"]) == sid)
            if sid not in old_ids:
                frame_changed.add(sid); video_changed.add(sid); reasons[sid] = "新镜"
                continue
            fo, vo = _request_keys(old_norm, sid)
            fn, vn = _request_keys(new_norm, sid)
            ref = str(shot.get("ref_frame") or "")
            if ref and ref in frame_changed:
                frame_changed.add(sid); reasons[sid] = f"参考镜 {ref} 的首帧变了"
            elif fo != fn:
                frame_changed.add(sid)
                diff = sorted(k for k in set(fo) | set(fn) if fo.get(k) != fn.get(k))
                reasons[sid] = "首帧请求变了：" + "、".join(diff)
            if sid in frame_changed:
                video_changed.add(sid)
            elif vo != vn:
                video_changed.add(sid)
                diff = sorted(k for k in set(vo) | set(vn) if vo.get(k) != vn.get(k))
                reasons[sid] = "视频请求变了：" + "、".join(diff)
    reusable_frames = [sid for sid in new_ids if sid not in frame_changed and (old_root / "01_frames" / f"{sid}.png").is_file()]
    reusable_videos = [sid for sid in new_ids if sid not in video_changed and (old_root / "02_video" / f"{sid}.mp4").is_file()]
    return {
        "affected_frames": [sid for sid in new_ids if sid in frame_changed],
        "affected_videos": [sid for sid in new_ids if sid in video_changed],
        "reusable_frames": reusable_frames,
        "reusable_videos": reusable_videos,
        "removed_shots": sorted(old_ids - set(new_ids)),
        "reasons": reasons,
        "requests": {"frames": sum(1 for sid in new_ids if sid in frame_changed),
                     "videos": sum(1 for sid in new_ids if sid in video_changed)},
    }


def suggest_revision_id(episode_id: str, content_dir: Path) -> str:
    match = re.fullmatch(r"(.*)-v(\d+)", episode_id)
    base, n = (match.group(1), int(match.group(2))) if match else (episode_id, 1)
    while True:
        n += 1
        candidate = f"{base}-v{n}"
        if not (content_dir / f"{candidate}.json").exists():
            return candidate


def _validate(mutated: dict[str, Any]) -> dict[str, Any]:
    try:
        norm = normalize_spec(mutated, project_root=deps.PROJECT_ROOT)
        for s in norm["shots"]:
            _request_keys(norm, str(s["id"]))   # 编译每一镜：非法运镜/引用在这里现形
    except (SpecError, CameraError) as exc:
        raise ReviseError(f"改动没通过校验：{exc}") from exc
    return norm


def preview(episode_id: str, mutation: Mapping[str, Any]) -> dict[str, Any]:
    path, _root = deps.resolve_storyvideo_path(episode_id)
    raw = deps.read_json_bom(path)
    old_norm = _validate(copy.deepcopy(raw))
    mutated = apply_mutation(raw, mutation)
    new_norm = _validate(mutated)
    result = impact(old_norm, new_norm)
    result["suggested_id"] = suggest_revision_id(episode_id, path.parent)
    result["locked"] = deps.is_locked(old_norm["output_root"])
    return result


def apply(episode_id: str, mutation: Mapping[str, Any], new_episode_id: str) -> dict[str, Any]:
    """复制成新版本并带上可复用的产物与裁决，然后进入制作状态。"""

    path, _root = deps.resolve_storyvideo_path(episode_id)
    raw = deps.read_json_bom(path)
    old_norm = _validate(copy.deepcopy(raw))
    if not deps.is_locked(old_norm["output_root"]):
        raise ReviseError("这个项目还没开始制作，直接保存即可，不需要开新版本")
    new_id = new_episode_id.strip()
    if not new_id:
        raise ReviseError("新版本的 id 不能为空")
    from genkit.ids import is_valid_id

    if not is_valid_id(new_id):
        raise ReviseError("项目 id 只能用英文、数字、下划线、点、短横线")
    new_path = path.parent / f"{new_id}.json"
    # 从「查 new_path 存不存在」到「写盘 + 进制作」整段按新版本的文件路径互斥：两个同 new_id 的请求并发时
    # 都能过 exists() 检查、各写一次，后写覆盖先写，两边都 200 且各自报告自己的改动进了新版本。
    # 拿不到锁的那个直接 SpecWriteBusy（接口层 409），不排队——排队之后它照样会撞「已经存在」
    with deps.spec_write_lock(new_path):
        return _apply_locked(episode_id, mutation, new_id, path, raw, old_norm, new_path)


def _apply_locked(episode_id: str, mutation: Mapping[str, Any], new_id: str, path: Path, raw: dict[str, Any],
                  old_norm: dict[str, Any], new_path: Path) -> dict[str, Any]:
    if new_path.exists():
        raise ReviseError(f"项目 {new_id} 已经存在，换一个 id")
    if deps.ledger_run(new_id) is not None:
        raise ReviseError(f"这个 id 在账本里还有旧的制作记录（{new_id}）——换一个 id，或先从归档把它恢复回来")
    mutated = apply_mutation(raw, mutation)
    mutated["id"] = new_id
    mutated["output_root"] = f"output/story_video/{new_id}"
    mutated["revision_note"] = f"从 {episode_id} 复制：{mutation.get('kind')}"
    new_norm = _validate(mutated)
    result = impact(old_norm, new_norm)

    old_root = Path(str(old_norm["output_root"]))
    new_root = Path(str(new_norm["output_root"]))
    atomic_write_json(new_path, mutated)
    (new_root / "01_frames").mkdir(parents=True, exist_ok=True)
    (new_root / "02_video").mkdir(parents=True, exist_ok=True)
    (new_root / "manifests").mkdir(parents=True, exist_ok=True)
    copied = {"frames": [], "videos": []}
    for sid in result["reusable_frames"]:
        shutil.copy2(old_root / "01_frames" / f"{sid}.png", new_root / "01_frames" / f"{sid}.png")
        copied["frames"].append(sid)
    for sid in result["reusable_videos"]:
        shutil.copy2(old_root / "02_video" / f"{sid}.mp4", new_root / "02_video" / f"{sid}.mp4")
        copied["videos"].append(sid)

    # 审片裁决跟着产物走：文件字节没变，人看过的就不用再看一遍
    old_qc_path = old_root / "manifests" / "qc.json"
    if old_qc_path.is_file() and (copied["frames"] or copied["videos"]):
        old_qc = load_qc(old_qc_path)
        new_qc = load_qc(new_root / "manifests" / "qc.json")
        for gate, sids, sub, suffix in (("frames", copied["frames"], "01_frames", ".png"), ("videos", copied["videos"], "02_video", ".mp4")):
            items = (old_qc["gates"].get(gate) or {}).get("items") or {}
            kept = {}
            for sid in sids:
                item = items.get(sid)
                if isinstance(item, dict) and item.get("technical_passed"):
                    kept[sid] = {**item, "artifact": str((new_root / sub / f"{sid}{suffix}").resolve())}
            if kept:
                new_qc["gates"][gate] = {"status": "pending_review", "items": kept, "evidence": [],
                                        "expected_items": [str(s["id"]) for s in new_norm["shots"]]}
        write_qc(new_root / "manifests" / "qc.json", new_qc)

    with StoryVideoRunner(str(deps.PROJECT_ROOT)) as runner:
        if copied["frames"] or copied["videos"]:
            runner.import_legacy(new_id, confirmed_existing_artifacts=True)
        else:
            runner.prepare(new_id)
    result["id"] = new_id
    result["copied"] = copied
    result["from"] = episode_id
    return result


__all__ = ["ReviseError", "MUTATION_KINDS", "apply_mutation", "impact", "preview", "apply", "suggest_revision_id"]
