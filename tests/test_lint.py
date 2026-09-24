"""静态 lint：点名覆盖、可生成性、正文卫生、镜头功能重复、机位高度、beats 留白/重叠、提示词长度。"""

from __future__ import annotations

from storyvideo import lint as L


def _spec(**shots_extra):
    base = {
        "cast": {"lin": {"name": "林小满", "block": "黑发。"}, "cat": {"name": "招财猫", "block": "金色。", "aliases": ["小猫"]},
                 "office": {"block": "办公区。"}},
        "shots": [
            {"id": "S1", "dur": 5, "cast": ["lin"], "shot": "中景，平视", "art": "林小满坐在工位", "motion": "她抬头", "purpose": "establish"},
            {"id": "S2", "dur": 5, "cast": ["lin"], "shot": "中景，平视", "art": "林小满看着招财猫", "motion": "她伸手", "purpose": "establish"},
        ],
    }
    for k, v in shots_extra.items():
        base[k] = v
    return base


def test_cast_mentions():
    spec = _spec()
    msgs = L.check_cast_mentions(spec)
    assert any("S2" in m and "招财猫" in m and "cast 没有 cat" in m for m in msgs)
    assert not any("S1" in m for m in msgs)
    # 别名也算点名；列了没提要提醒
    spec["shots"][1]["art"] = "她看着那只小猫"
    assert any("S2" in m and "cast 没有 cat" in m for m in L.check_cast_mentions(spec))
    # 用"她"指代算提到了林小满；招财猫既没点名也没代词才提醒——正文完全没有人称时
    spec["shots"][1]["cast"] = ["lin", "cat"]; spec["shots"][1]["art"] = "工位上只有键盘和奶茶"; spec["shots"][1]["motion"] = "键盘灯闪一下"
    msgs = L.check_cast_mentions(spec)
    assert any("S2" in m and "列了「招财猫」" in m for m in msgs) and any("列了「林小满」" in m for m in msgs)
    spec["shots"][1]["motion"] = "她伸手"
    assert not L.check_cast_mentions(spec)   # 有代词就不吵
    # 关闭按名字核对
    spec["cast"]["cat"]["name_in_prompt"] = False
    assert not any("招财猫" in m for m in L.check_cast_mentions(spec))


def test_generability():
    spec = _spec()
    spec["shots"][0]["motion"] = "她像是听见了什么，没有接话，一把抢过手机"
    msgs = L.check_generability(spec)
    joined = " ".join(msgs)
    assert "内部状态" in joined and "否定式动作" in joined and "精确拦截" in joined
    spec["shots"][1]["motion"] = "她握住杯子，拧开盖子，递给对方，再接过纸巾"
    spec["shots"][1]["dur"] = 4
    assert any("S2" in m and "手部动作" in m for m in L.check_generability(spec))
    spec["shots"][1]["dur"] = 12
    assert not any("S2" in m and "手部动作" in m for m in L.check_generability(spec))


def test_prompt_hygiene():
    spec = _spec()
    spec["shots"][1]["art"] = "同上，她拿着 S1 里那件外套，见 frame_v2.png"
    joined = " ".join(L.check_prompt_hygiene(spec))
    assert "镜号「S1」" in joined and "文件名" in joined and "同上" in joined
    assert "版本号" not in joined
    spec["shots"][1]["art"] = "第 2 版的外套"
    assert any("版本号" in m for m in L.check_prompt_hygiene(spec))


def test_purpose_and_camera_columns():
    spec = _spec()
    msgs = L.check_purpose(spec)
    assert any("S2" in m and "功能重复" in m and "中景" in m for m in msgs)
    spec["shots"][1]["shot"] = "特写，平视"
    assert not L.check_purpose(spec)
    spec["shots"][1]["purpose"] = "magic"
    assert any("不在清单" in m for m in L.check_purpose(spec))
    # 机位高度
    spec = _spec()
    spec["shots"][0]["shot"] = "中景"
    msgs = L.check_camera_columns(spec)
    assert any("S1" in m and "机位高度" in m for m in msgs) and not any("S2" in m for m in msgs)
    # 没人物的空镜不查
    spec["shots"][0]["cast"] = []
    assert not L.check_camera_columns(spec)


def test_beat_coverage():
    spec = _spec()
    spec["shots"][0]["beats"] = [{"t": "0-2", "picture": "a"}, {"t": "2-3", "picture": "b"}]
    msgs = L.check_beat_coverage(spec)
    assert any("只覆盖 3s / 5s" in m for m in msgs)
    spec["shots"][0]["beats"] = [{"t": "0-3", "picture": "a"}, {"t": "2-5", "picture": "b"}]
    msgs = L.check_beat_coverage(spec)
    assert any("重叠" in m for m in msgs) and not any("只覆盖" in m for m in msgs)
    spec["shots"][0]["beats"][1]["parallel"] = True
    assert not L.check_beat_coverage(spec)


def test_check_all_runs_on_example(tmp_path):
    import json
    from pathlib import Path

    from storyvideo.spec import load_spec

    raw = json.loads((Path(__file__).resolve().parents[1] / "examples" / "episodes" / "first_episode.json").read_text(encoding="utf-8-sig"))
    raw["id"] = "LINT-T"; raw["output_root"] = "output/story_video/LINT-T"
    content = tmp_path / "content" / "story_video"; content.mkdir(parents=True)
    (content / "LINT-T.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    spec = load_spec("LINT-T", project_root=tmp_path)
    msgs = L.check_all(spec)
    assert isinstance(msgs, list)
    assert not any("镜号" in m for m in msgs)   # 示例剧集正文里没有镜号


def test_shot_ids_that_differ_only_in_case_are_flagged():
    """S1 / s1 在 Windows / macOS 上写到同一个文件、互相覆盖；只差大小写才告警，一条告警列全这一组。"""

    from webui.i18n import tr

    spec = _spec()
    assert L.check_shot_id_case(spec) == []
    spec["shots"].append({"id": "s1", "dur": 5, "cast": ["lin"], "shot": "中景，平视", "art": "林小满", "motion": "她起身"})
    msgs = L.check_shot_id_case(spec)
    assert len(msgs) == 1 and msgs[0].startswith("全集：镜号 S1、s1 只差大小写") and msgs[0] in L.check_all(spec)
    en = tr(msgs[0], "en")
    assert en.startswith("Whole episode: shot ids S1、s1 differ only in letter case") and "rename them to distinct shot ids" in en
