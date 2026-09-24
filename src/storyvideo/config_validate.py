"""校验镜头语言库/风格骨架库这两份「基础配置」JSON 在写盘前的内部一致性——

给 webui 的配置管理页用。检查的是两份库自己的结构约定（id 不重复、交叉引用真实存在），
操作对象是「人在 UI 里编辑过、还没写盘」的任意 JSON——所以这里是独立的纯函数，
返回错误列表而不是直接 assert 崩掉：UI 要把错误显示给人看，不是让进程崩溃。
"""

from __future__ import annotations

from typing import Any


def validate_camera_library(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["顶层必须是一个 JSON 对象"]

    shots = data.get("shots")
    if not isinstance(shots, list):
        errors.append("shots 字段必须是数组")
        shots = []
    shot_ids = [s.get("id") for s in shots if isinstance(s, dict)]
    dup = {i for i in shot_ids if shot_ids.count(i) > 1}
    if dup:
        errors.append(f"shots 里有重复 id：{sorted(dup)}")

    combos_within = data.get("combos_within_shot")
    if not isinstance(combos_within, list):
        errors.append("combos_within_shot 字段必须是数组")
        combos_within = []
    combo_within_ids = [c.get("id") for c in combos_within if isinstance(c, dict)]
    dup = {i for i in combo_within_ids if combo_within_ids.count(i) > 1}
    if dup:
        errors.append(f"combos_within_shot 里有重复 id：{sorted(dup)}")

    combos_seq = data.get("combos_sequence")
    if not isinstance(combos_seq, list):
        errors.append("combos_sequence 字段必须是数组")
        combos_seq = []
    seq_ids = [c.get("id") for c in combos_seq if isinstance(c, dict)]
    dup = {i for i in seq_ids if seq_ids.count(i) > 1}
    if dup:
        errors.append(f"combos_sequence 里有重复 id：{sorted(dup)}")

    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        errors.append("profiles 字段必须是对象")
        profiles = {}
    # legacy_profiles（停用的旧档位，只为已有项目兼容保留）可选；有就按同一套规则查，
    # 并且不能跟 profiles 重名——两边都命中时编译器只认 profiles，重名等于悄悄改了旧项目。
    legacy = data.get("legacy_profiles")
    if legacy is None:
        legacy = {}
    elif not isinstance(legacy, dict):
        errors.append("legacy_profiles 字段必须是对象")
        legacy = {}
    clash = sorted(set(profiles) & set(legacy))
    if clash:
        errors.append(f"profiles 与 legacy_profiles 重名：{clash}")
    shot_id_set = set(shot_ids)
    combo_within_set = set(combo_within_ids)
    seq_set = set(seq_ids)
    for name, prof in [*profiles.items(), *legacy.items()]:
        if not isinstance(prof, dict):
            errors.append(f"profile {name!r} 必须是对象")
            continue
        unknown = [m for m in (prof.get("allow") or ()) if m not in shot_id_set]
        if unknown:
            errors.append(f"profile {name!r}.allow 引用了不存在的镜头 id：{unknown}")
        # combos 对照的是 combos_within_shot，不是 combos_sequence——这两个 id
        # 空间长得像、很容易填混（profile 的 combos 只能填镜内组合），
        # 这条检查就是防这种填错。
        bad_combo = [m for m in (prof.get("combos") or ()) if m not in combo_within_set]
        if bad_combo:
            errors.append(
                f"profile {name!r}.combos 引用了不存在于 combos_within_shot 的 id："
                f"{bad_combo}——是不是把 combos_sequence 的 id 填错地方了？跨镜次节拍"
                "请放进 recommended_sequences。"
            )
        unknown_seq = [s for s in (prof.get("recommended_sequences") or ()) if s not in seq_set]
        if unknown_seq:
            errors.append(f"profile {name!r}.recommended_sequences 引用了不存在的镜头组：{unknown_seq}")
    return errors


def validate_style_library(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["顶层必须是一个 JSON 对象"]

    neg_pool = data.get("neg_pool")
    if not isinstance(neg_pool, list):
        errors.append("neg_pool 字段必须是数组")
        neg_pool = []
    neg_ids = [n.get("id") for n in neg_pool if isinstance(n, dict)]
    dup = {i for i in neg_ids if neg_ids.count(i) > 1}
    if dup:
        errors.append(f"neg_pool 里有重复 id：{sorted(dup)}")

    skeletons = data.get("frame_style_skeletons")
    checklists = data.get("scene_anchor_checklists")
    if not isinstance(skeletons, dict):
        errors.append("frame_style_skeletons 字段必须是对象")
        skeletons = {}
    if not isinstance(checklists, dict):
        errors.append("scene_anchor_checklists 字段必须是对象")
        checklists = {}
    if set(skeletons.keys()) != set(checklists.keys()):
        errors.append(
            "frame_style_skeletons 和 scene_anchor_checklists 的 profile 集合对不上："
            f"差异 {sorted(set(skeletons) ^ set(checklists))}——两边要覆盖同一组 profile，"
            "不然某个 profile 起草时会有一半骨架缺失。"
        )
    for profile, checklist in checklists.items():
        if not isinstance(checklist, list):
            errors.append(f"scene_anchor_checklists[{profile!r}] 必须是数组")
            continue
        ids = [c.get("id") for c in checklist if isinstance(c, dict)]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            errors.append(f"scene_anchor_checklists[{profile!r}] 检查项 id 重复：{sorted(dup)}")
    return errors


__all__ = ["validate_camera_library", "validate_style_library"]
