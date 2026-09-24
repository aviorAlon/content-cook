"""起草与重写（docs/scriptlab-spec.md §3）：估算复用播客的算法、写法按 target 选、回填的规矩、假模型跑通。"""

from __future__ import annotations

import pytest

from podcast import craft as PC
from script import craft as C
from script import spec as S
from storyvideo.screenplay import LINE_MAX_CHARS, SPEAKING_CAST_MAX


def make(tmp_path, spec_id="SC-C", **extra) -> dict:
    data = {
        "title": "下班后的消息",
        "target": "podcast",
        "brief": {"topic": "回还是不回", "angle": "不站队", "audience": "刚工作两三年的人", "tone": "像朋友吐槽",
                  "materials": "阿杰上个月周六晚上十点被拉进新群", "avoid": ["说教", "编造调查数据"]},
        "speakers": [{"id": "jie", "name": "阿杰", "persona": "大厂产品经理", "stance": "边界感是必须的"},
                     {"id": "lin", "name": "小林", "persona": "语速快", "stance": "客户在等就得回"}],
        "blocks": [{"id": "B01", "title": "冷开场", "goal": "把人拽进来", "note": "别自我介绍"},
                   {"id": "B02", "title": "分歧"}],
        "versions": [{"id": "V1", "blocks": [{"id": "B01", "lines": [{"who": "jie", "text": "两点十七。"}]},
                                             {"id": "B02", "lines": [{"who": "lin", "text": "那也得回。"}]}]}],
        "current": "V1",
    }
    data.update(extra)
    return S.normalize(data, project_root=tmp_path, spec_id=spec_id)


# ---- 估算


def test_estimate_reuses_podcast_algorithm(tmp_path):
    spec = make(tmp_path)
    est = C.estimate(spec)
    assert est["lines"] == 2 and est["blocks"] == 2 and est["chars"] == 10
    mirror = PC.estimate({"language": "zh", "segments": [{"lines": [{"text": "两点十七。"}]}, {"lines": [{"text": "那也得回。"}]}]})
    assert est["seconds"] == mirror["seconds"] and est["minutes"] == mirror["minutes"]
    assert "target_minutes" not in est
    # 没有版本时段数照骨架数，字数是 0
    empty = C.estimate(make(tmp_path, spec_id="SC-C2", versions=[], current=""))
    assert empty == {"chars": 0, "lines": 0, "blocks": 2, "seconds": 0.0, "minutes": 0.0}


def test_estimate_counts_the_latest_version_when_nothing_is_picked(tmp_path):
    """有版本但 current 是空的（第一版会自动采用，但整体保存时 current 可能被清空，契约 §3）——估算要照样算，不能报 0 分钟。"""

    spec = make(tmp_path, spec_id="SC-C3", current="")
    assert C.estimate(spec)["chars"] == 10 == C.estimate(spec, "V1")["chars"]


# ---- 写法按 target 选


def test_rules_for_each_target_come_from_existing_modules():
    podcast = C.rules_for("podcast")
    assert tuple(PC.CRAFT_RULES) == podcast[:len(PC.CRAFT_RULES)] and podcast[-1] == C.PODCAST_TURN_RULE
    assert C.rules_for("drama_screenplay") == C.DRAMA_RULES == C.rules_for("drama_shot")
    assert C.rules_for("generic") == C.GENERIC_RULES
    # generic 是两套的交集：播客三条（照句首取，不抄正文）+ 短剧最后那条（不用台词交代前情）
    assert len(C.GENERIC_RULES) == 4
    assert all(r in PC.CRAFT_RULES for r in C.GENERIC_RULES[:3])
    assert C.GENERIC_RULES[3] is C.DRAMA_RULES[-1]
    # 短剧那两条的数字直接引常量，不在句子里写死
    assert f"{LINE_MAX_CHARS} 字" in C.DRAMA_RULES[3] and f"{SPEAKING_CAST_MAX} 个" in C.DRAMA_RULES[4]


def test_english_drama_rules_drop_the_character_limit():
    """「一句不超过 N 字」数的是汉字，英文稿的提示词里不放——检查那边（lint.check_drama_lines）
    对英文稿也同样不判这条，两处口径要一致，不然模型守着一条谁也不检查的规矩。"""

    en = C.rules_for("drama_shot", "en")
    assert en == C.DRAMA_RULES_EN and len(en) == len(C.DRAMA_RULES) - 1
    assert all(f"{LINE_MAX_CHARS} 字" not in r for r in en)
    assert f"{SPEAKING_CAST_MAX} 个" in "\n".join(en)         # 开口人数那条留着（数的是人，跟语言无关）
    assert C.rules_for("podcast", "en") == C.rules_for("podcast")
    assert C.rules_for("generic", "en") == C.GENERIC_RULES


def test_prompt_carries_rules_speakers_brief_and_skeleton(tmp_path):
    system_prompt, user_prompt = C.build_prompt(make(tmp_path))
    for rule in PC.CRAFT_RULES:
        assert rule in system_prompt
    assert C.PODCAST_TURN_RULE in system_prompt
    assert "who 只能是角色表里的 id" in system_prompt and "播客对谈" in system_prompt
    assert "- jie：阿杰；人设：大厂产品经理；立场：边界感是必须的" in user_prompt
    assert "素材（只能用这里的事实和数字，不许编）" in user_prompt and "不要：说教；编造调查数据" in user_prompt
    assert "## 段 B01｜冷开场" in user_prompt and "目标：把人拽进来" in user_prompt and "备注：别自我介绍" in user_prompt
    # 整版新的：骨架不带台词
    assert "两点十七。" not in user_prompt
    drama = C.build_prompt(make(tmp_path, target="drama_shot"))[0]
    assert C.DRAMA_RULES[0] in drama and PC.CRAFT_RULES[0] not in drama
    drama_en = C.build_prompt(make(tmp_path, target="drama_shot", language="en"))[0]
    assert C.DRAMA_RULES[0] in drama_en and C.DRAMA_RULES[3] not in drama_en    # 英文稿不放「一句不超过 N 字」


def test_prompt_refuses_an_empty_cast_or_skeleton(tmp_path):
    """两种空都在发请求之前拦下来（接口层照旧 400）：角色表空着报的不能是「角色表里没有这个人」那句误导的话，
    骨架空着更不能静默追加一个 blocks 为空的废版本。"""

    with pytest.raises(C.ScriptLabError, match="角色表是空的"):
        C.build_prompt(make(tmp_path, spec_id="SC-C6", speakers=[], versions=[], current=""))
    with pytest.raises(C.ScriptLabError, match="还没有段落骨架"):
        C.build_prompt(make(tmp_path, spec_id="SC-C7", blocks=[], versions=[], current=""))

    def boom(system_prompt, user_prompt, *, json_schema):    # 不该被调用
        raise AssertionError("不该发请求")

    empty = make(tmp_path, spec_id="SC-C8", speakers=[], blocks=[], versions=[], current="")
    S.save(empty)
    with pytest.raises(C.ScriptLabError, match="角色表是空的"):
        C.run_generate(empty, dispatch=boom)
    assert S.load("SC-C8", project_root=tmp_path)["versions"] == []    # 没有写下废版本


def test_prompt_only_rewrites_the_picked_blocks(tmp_path):
    _system, user = C.build_prompt(make(tmp_path), blocks=["B02"], note="更硬一点")
    assert "【只重写这一段，其余段原样不动】" in user and "只返回段 B02" in user
    assert "阿杰：两点十七。" in user and "小林：那也得回。" in user       # 其余段带台词当上下文
    assert "这次的额外要求：更硬一点" in user
    with pytest.raises(C.ScriptLabError, match="没有这些段：B09"):
        C.build_prompt(make(tmp_path), blocks=["B09"])


# ---- 回填


def test_apply_draft_maps_names_rejects_strangers_and_ignores_unknown_blocks(tmp_path):
    spec = make(tmp_path)
    structured = {"blocks": [
        {"id": "B01", "lines": [{"who": "阿杰", "text": "新的冷开场。", "cue": "笑", "kind": "talk"},
                                {"who": "lin", "text": "什么？", "cue": "不存在的", "kind": "question"}]},
        {"id": "B09", "lines": [{"who": "jie", "text": "模型自己加的段。"}]},
    ]}
    out = C.apply_draft(spec, structured)
    assert [b["id"] for b in out] == ["B01", "B02"]          # 骨架里没有的 B09 忽略
    assert out[0]["lines"][0] == {"who": "jie", "text": "新的冷开场。", "cue": "笑", "kind": "talk"}
    assert out[0]["lines"][1]["cue"] == "" and out[0]["lines"][1]["kind"] == "question"
    assert out[1]["lines"] == []                              # 模型没返回、又没给 base = 空
    with pytest.raises(C.ScriptLabError, match="角色表里没有这个人"):
        C.apply_draft(spec, {"blocks": [{"id": "B01", "lines": [{"who": "路人", "text": "x"}]}]})
    with pytest.raises(C.ScriptLabError, match="缺 blocks"):
        C.apply_draft(spec, {"items": []})


def test_apply_draft_keeps_the_other_blocks_when_rewriting(tmp_path):
    spec = make(tmp_path)
    base = S.current_version(spec)
    out = C.apply_draft(spec, {"blocks": [{"id": "B02", "lines": [{"who": "lin", "text": "重写了。"}]}]},
                        blocks=["B02"], base=base)
    assert out[0]["lines"][0]["text"] == "两点十七。"          # 没重写的段原样抄过来
    assert out[1]["lines"] == [{"who": "lin", "text": "重写了。", "cue": "", "kind": "talk"}]
    with pytest.raises(C.ScriptLabError, match="模型没有返回段 B01"):
        C.apply_draft(spec, {"blocks": [{"id": "B02", "lines": []}]}, blocks=["B01"], base=base)
    # 返回了这一段、但台词是空的（或 text 全是空白被清干净）也算没返回：被点名重写的那段不能静默变空
    with pytest.raises(C.ScriptLabError, match="模型没有返回段 B02"):
        C.apply_draft(spec, {"blocks": [{"id": "B02", "lines": []}]}, blocks=["B02"], base=base)
    with pytest.raises(C.ScriptLabError, match="模型没有返回段 B02"):
        C.apply_draft(spec, {"blocks": [{"id": "B02", "lines": [{"who": "lin", "text": "  "}]}]},
                      blocks=["B02"], base=base)
    # 整版重来时，模型漏掉的段保留上一版
    kept = C.apply_draft(spec, {"blocks": [{"id": "B01", "lines": [{"who": "jie", "text": "换了。"}]}]}, base=base)
    assert kept[1]["lines"][0]["text"] == "那也得回。"


# ---- 跑一次


def test_run_generate_appends_a_version_and_leaves_current_alone(tmp_path):
    spec = make(tmp_path)
    S.save(spec)
    seen: dict = {}

    def fake_dispatch(system_prompt, user_prompt, *, json_schema):
        seen["schema"] = json_schema
        seen["user"] = user_prompt
        return ({"blocks": [{"id": "B01", "lines": [{"who": "jie", "text": "有一次我半夜两点被拉进群。"}]},
                            {"id": "B02", "lines": [{"who": "lin", "text": "我倒觉得该回。"}]}]}, None, 321, "settings")

    fresh = C.run_generate(spec, note="狠一点", dispatch=fake_dispatch)
    assert seen["schema"] is C.DRAFT_JSON_SCHEMA and "狠一点" in seen["user"]
    assert [v["id"] for v in fresh["versions"]] == ["V1", "V2"]
    assert fresh["current"] == "V1"                            # 不自动采用
    v2 = fresh["versions"][1]
    assert v2["source"] == "model" and v2["backend"] == "settings" and v2["note"] == "狠一点"
    assert v2["label"] == "第 2 版" and v2["from_version"] == "" and v2["created_at"]
    on_disk = S.load("SC-C", project_root=tmp_path)
    assert on_disk["versions"][1]["blocks"][0]["lines"][0]["text"] == "有一次我半夜两点被拉进群。"


def test_run_generate_rewrite_records_the_base_version(tmp_path):
    spec = make(tmp_path)
    S.save(spec)

    def fake_dispatch(system_prompt, user_prompt, *, json_schema):
        assert "只返回段 B02" in user_prompt
        return ({"blocks": [{"id": "B02", "lines": [{"who": "lin", "text": "那也不一定。"}]}]}, None, 10, "settings")

    fresh = C.run_generate(spec, blocks=["B02"], label="只改第二段", dispatch=fake_dispatch)
    v2 = fresh["versions"][1]
    assert v2["source"] == "rewrite" and v2["from_version"] == "V1" and v2["label"] == "只改第二段"
    assert v2["blocks"][0]["lines"][0]["text"] == "两点十七。"
    assert v2["blocks"][1]["lines"][0]["text"] == "那也不一定。"


def test_rewrite_uses_the_latest_version_when_nothing_is_picked(tmp_path):
    """有版本但 current 是空的（第一版会自动采用，但整体保存时 current 可能被清空，契约 §3），这时点「只重写这几段」——
    垫底的那一版要回落到最新一版：没选中的段原样留着、提示词里带着现有台词，不能悄悄清空。"""

    spec = make(tmp_path, spec_id="SC-C4", current="")
    S.save(spec)
    seen: dict = {}

    def fake_dispatch(system_prompt, user_prompt, *, json_schema):
        seen["user"] = user_prompt
        return ({"blocks": [{"id": "B02", "lines": [{"who": "lin", "text": "那也不一定。"}]}]}, None, 10, "settings")

    fresh = C.run_generate(spec, blocks=["B02"], dispatch=fake_dispatch)
    assert "阿杰：两点十七。" in seen["user"]                 # 模型看得见现有的稿子
    v2 = fresh["versions"][1]
    assert v2["source"] == "rewrite" and v2["from_version"] == "V1"
    assert v2["blocks"][0]["lines"][0]["text"] == "两点十七。"  # 没选中的段没被清空
    assert v2["blocks"][1]["lines"][0]["text"] == "那也不一定。"


def test_rewrite_without_any_version_is_refused(tmp_path):
    """一版也没有时「只重写这几段」直接拒绝：既不静默产出空段，也不浪费一次模型请求。"""

    spec = make(tmp_path, spec_id="SC-C5", versions=[], current="")
    S.save(spec)

    def boom(system_prompt, user_prompt, *, json_schema):    # 不该被调用
        raise AssertionError("不该发请求")

    with pytest.raises(C.ScriptLabError, match="还没有可以基于的版本"):
        C.run_generate(spec, blocks=["B01"], dispatch=boom)
    with pytest.raises(C.ScriptLabError, match="还没有可以基于的版本"):
        C.build_prompt(spec, blocks=["B01"])
    # 整版新写不需要垫底：白纸一张照样能生成
    assert C.build_prompt(spec)[1]


def test_run_generate_reports_validation_failures(tmp_path):
    spec = make(tmp_path)
    S.save(spec)

    def bad_dispatch(system_prompt, user_prompt, *, json_schema):
        return ({"blocks": [{"id": "B01", "lines": [{"who": "jie", "text": "x", "cue": "跳舞"}]}]}, None, 1, "settings")

    # cue 在回填时就清空了，落不到校验——这里改成模型返回的形状本身不对
    def worse_dispatch(system_prompt, user_prompt, *, json_schema):
        return ({"blocks": "一段"}, None, 1, "settings")

    assert C.run_generate(spec, dispatch=bad_dispatch)["versions"][1]["blocks"][0]["lines"][0]["cue"] == ""
    with pytest.raises(C.ScriptLabError, match="缺 blocks"):
        C.run_generate(spec, dispatch=worse_dispatch)
