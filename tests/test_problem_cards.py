"""问题卡：告警字符串 → {scope, target, kind, impact, fix}；详情接口带 problems。"""

from __future__ import annotations

from webui.problems import HEAD_NAMES, classify, group_by_target, problem_card, problem_cards


def test_problem_card_parsing():
    c = problem_card("shot S3_CAT_SPEAKS: 台词 20 字 ≈4.44s 纯语音，超过可用发声窗口 3.7s——这镜装不下这些台词，加时长、拆镜或删词")
    assert c["scope"] == "shot" and c["target"] == "S3_CAT_SPEAKS" and c["kind"] == "dialogue"
    assert c["impact"].startswith("台词 20 字") and c["fix"].startswith("这镜装不下")
    c = problem_card("cast lin: 角色描述里有「微笑」——表情逐镜写，角色块只写长相")
    assert c["scope"] == "cast" and c["target"] == "lin" and c["fix"].startswith("表情逐镜写")
    c = problem_card("全集：5 镜台词共 300 字 ≈66.7s 纯语音，比声明的总时长 40s 多出 66%——报时长前按这个数字来")
    assert c["scope"] == "episode" and c["target"] == "" and c["kind"] == "dialogue"
    c = problem_card("shot S1: 有 ref_frame 但没写 entry_state")
    assert c["kind"] == "continuity" and c["fix"] == "" and c["impact"].startswith("有 ref_frame")
    # 剧本工作台（docs/scriptlab-spec.md §4）：段是 block，整篇是「全篇」，分类仍落在已有的 dialogue / podcast 上
    c = problem_card("block B01: 这一段全是书面语——换成日常说法")
    assert c["scope"] == "block" and c["target"] == "B01" and c["kind"] == "podcast"
    assert c["impact"] == "这一段全是书面语" and c["fix"] == "换成日常说法"
    c = problem_card("全篇：一人独大，小林只有两句——给小林多两句反对的话")
    assert c["scope"] == "episode" and c["target"] == "" and c["impact"].startswith("一人独大")
    # 「回答只有 N 字」说的是台词本身，句里没有「台词」二字，不点名就落进「其他」
    assert classify("block B01: 回答「没回。」只有 3 字——听众要的是结论加原因，不是一句敷衍") == "dialogue"
    assert classify("shot S1: 正文里有镜号「S2」") == "text"
    assert classify("shot S1: 表演描述里有情绪形容词") == "performance"
    assert classify("shot S1: 有景别没有机位高度") == "camera"
    assert classify("完全不认识的话") == "other"


def test_every_head_has_an_english_rule():
    """认了一种新头就要给它备一条英文规则，不然英文界面上这一类卡的正文整句回落中文。"""

    from webui.i18n_rules import _HEADS

    missing = sorted(set(HEAD_NAMES) - set(_HEADS))
    assert not missing, f"i18n_rules._HEADS 缺这些头的英文对照：{missing}"
    heads = "".join(pattern for pattern, _ in _HEADS.values())
    missing_whole = [w for w in ("全集", "全书", "全片", "全期", "全篇") if f"{w}：" not in heads]
    assert not missing_whole, f"i18n_rules._HEADS 缺这些整篇级头的英文对照：{missing_whole}"


def test_grouping():
    cards = problem_cards(["shot S1: a——b", "shot S1: c", "cast x: d", "全集：e", ""])
    groups = group_by_target(cards)
    assert list(groups) == ["shot:S1", "cast:x", "episode"] and len(groups["shot:S1"]) == 2


def test_detail_carries_problems(tmp_path, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from webui import deps
    from webui.app import app
    from webui.routers import episodes as episodes_router
    from webui.routers import post as post_router
    from webui.routers import project as project_router

    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    spec = {
        "id": "PC-T", "output_root": "output/story_video/PC-T", "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"a": {"name": "阿一", "block": "黑发。"}},
        "shots": [{"id": "S1", "dur": 4, "cast": ["a"], "shot": "近景", "art": "阿一像是听见了什么", "motion": "她很悲伤", "lines": [{"who": "a", "zh": "一" * 20}]}],
    }
    (content_dir / "PC-T.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    d = TestClient(app).get("/api/episodes/storyvideo/PC-T").json()
    assert d["problems"] and all(c["scope"] == "shot" and c["target"] == "S1" for c in d["problems"] if c["scope"] == "shot")
    kinds = {c["kind"] for c in d["problems"]}
    assert {"dialogue", "performance"} <= kinds
    assert len(d["problems"]) == len(d["warnings"])
    # 英文界面：Overview 渲染的是每张卡的 impact / fix（不是 text），两半都不能露中文（引号里的用户内容除外）
    en = TestClient(app).get("/api/episodes/storyvideo/PC-T", headers={"X-StoryStudio-Lang": "en"}).json()
    for card in en["problems"]:
        for half in (card["impact"], card["fix"]):
            assert not _HAN.search(_QUOTED.sub("", half)), (card["text"], half)


# ---- 英文界面的问题卡两半（COMPAT-05 / FE-05）
_HAN = __import__("re").compile(r"[一-鿿]")
_QUOTED = __import__("re").compile(r"「[^」]*」|《[^》]*》|\"[^\"]*\"")
_PREVIEW_SPEC = {
    "shots": [
        {"id": "S_OVER", "dur": 4, "lines": [{"speaker": "a", "zh": "这是一句很长很长很长的台词一共要念很多很多字才能念完它真的很长"}]},   # 装不下
        {"id": "S_TIGHT", "dur": 6, "lines": [{"speaker": "a", "zh": "这句台词二十一个字按慢速正好偏紧一点点"}]},                       # 偏紧
        {"id": "S_SLACK", "dur": 6, "lines": []},                                                                                     # 占比过低
        {"id": "S_SHORT", "dur": 3, "lines": [{"speaker": "a", "zh": "嗯。"}]},                                                        # 一个字
    ]
}


def test_preview_warning_cards_translate():
    """preview.py 的四类台词告警（措辞改过一轮）：整句 warning 和拆出来的 impact / fix 两半在 en 下都不剩中文。
    以前这几条只写成整句规则，英文界面 warnings 是英文、概览页的问题卡却是中文（示例项目每个都命中）。"""

    from storyvideo.preview import check_dialogue_clarity, check_duration_budget
    from webui.i18n import tr

    warnings = check_duration_budget(_PREVIEW_SPEC) + check_dialogue_clarity(_PREVIEW_SPEC)
    for needle in ("装不下", "偏紧", "台词只占声明时长", "只有 1 个字"):
        assert any(needle in w for w in warnings), (needle, warnings)
    for w in warnings:
        whole = tr(w, "en")
        assert whole != w and not _HAN.search(_QUOTED.sub("", whole)), whole
        card = problem_card(w)
        for half in (card["impact"], card["fix"]):
            if half:
                got = tr(half, "en")
                assert not _HAN.search(_QUOTED.sub("", got)), (w, half, got)


_GROUP = __import__("re").compile(r"\((?:\?:)?([^()]*(?:\([^()]*\)[^()]*)*)\)(\?|\*|\+)?")
_HEADED = __import__("re").compile(r"^\^(?:(?:shot|cast|page|image|scene|beat|segment|block) \(\\S\+\??\): |(?:全集|全书|全片|全期|全篇)：(?:\(\))?)")


def _sample_from_pattern(pattern: str) -> str:
    """按规则的正则反推一个样本串：捕获组按形状代入镜号 / 数字 / 文字，正则转义还原成字面。"""

    body = pattern[1:] if pattern.startswith("^") else pattern
    body = body[:-1] if body.endswith("$") else body

    def fill(m):
        inner = m.group(1)
        if inner == "":
            return ""
        if "\\d" in inner and "." in inner:
            return "2.5"
        if "\\d" in inner:
            return "3"
        if "\\S" in inner or "\\w" in inner:
            return "S1"
        if "|" in inner and "\\" not in inner and "." not in inner:
            return inner.split("|")[0]
        return "abc"

    text = _GROUP.sub(fill, body)
    return __import__("re").sub(r"\\(.)", r"\1", text)


def test_every_headed_rule_translates_as_a_card():
    """规则表里每条带句头（shot X: / cast X: / 全集：…）的整句规则，都对应概览页的一张问题卡：
    按正则反推样本 → problem_card 拆两半 → 两半各自过 tr 不剩中文。只写整句、没用 _carded 展开两半的规则在这里挂。"""

    from webui import i18n_rules
    from webui.i18n import tr

    checked, bad = 0, []
    for pattern, _ in i18n_rules.PATTERNS:
        if not _HEADED.match(pattern):
            continue
        checked += 1
        sample = _sample_from_pattern(pattern)
        whole = tr(sample, "en")
        if whole == sample:
            bad.append(("整句", pattern[:100], sample[:80]))
            continue
        card = problem_card(sample)
        for which in ("impact", "fix"):
            half = card[which]
            if half and _HAN.search(half):
                got = tr(half, "en")
                if got == half or _HAN.search(_QUOTED.sub("", got)):
                    bad.append((which, pattern[:100], got[:80]))
    assert checked > 400, checked
    assert not bad, "\n".join(" | ".join(b) for b in bad)
