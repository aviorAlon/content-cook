"""投放（docs/music-spec.md §3）：短剧的那条**走 post-layers 校验路径**落进 ``post_graphics.music``，
绘本的写进 ``music.file``，播客的写进 ``music.<slot>``（intro / bed / outro，默认 bed，过 podcast.spec 归一化）；
``used_in`` 记去向；目标不存在 404；不合法的投放被目标项目的校验挡下来。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from music import spec as S
from music import use as U
from webui import deps
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router

EPISODE = {
    "id": "EP-MU", "title_zh": "加班夜", "output_root": "output/story_video/EP-MU", "prompt_language": "zh",
    "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
    "cast": {"lin": {"name": "林小满", "block": "黑发。"}},
    "shots": [{"id": "S1", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m", "lines": []}],
}
BOOK = {
    "id": "PB-MU", "title_zh": "小狐狸的三只袜子", "age_band": "2-4", "bilingual_mode": "zh_only",
    "characters": {"fox": {"name": "小狐狸", "name_en": "Little Fox", "block": "橘红色小狐狸，浅绿色小背心"}},
    "pages": [
        {"id": "P01", "kind": "cover", "scene": "小狐狸站在院子里，全身", "characters": ["fox"], "zh": "小狐狸的三只袜子"},
        {"id": "P02", "kind": "page", "scene": "风吹起晾衣绳上的袜子，中景", "characters": ["fox"], "zh": "呼呼呼，风一吹，红袜子飞走了！"},
    ],
}
PODCAST = {
    "id": "PC-MU", "title": "为什么我们戒不掉短视频", "format": "duo",
    "hosts": [{"id": "zhou", "name": "老周"}, {"id": "xia", "name": "小夏"}],
    "segments": [{"id": "G01", "kind": "topic", "title": "话题", "lines": [{"who": "zhou", "text": "先说个事。"}, {"who": "xia", "text": "你说。"}]}],
    "music": {"bed_gain": 0.2},
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "content" / "story_video").mkdir(parents=True)
    (tmp_path / "content" / "picturebook").mkdir(parents=True)
    (tmp_path / "content" / "podcast").mkdir(parents=True)
    for module in (deps, episodes_router, post_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", tmp_path / "content" / "story_video")
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", tmp_path / "content" / "story_video")
    (tmp_path / "content" / "story_video" / "EP-MU.json").write_text(json.dumps(EPISODE, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "content" / "picturebook" / "PB-MU.json").write_text(json.dumps(BOOK, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "content" / "podcast" / "PC-MU.json").write_text(json.dumps(PODCAST, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _music(root, *, current="MU-1-1.mp3") -> dict:
    spec = S.normalize({"title": "睡前摇篮曲", "brief": {"usage": "睡前底噪"},
                        "attempts": [{"file": "MU-1-1.mp3", "model": "lyria", "request_hash": "h"}],
                        "current": current}, project_root=root, spec_id="MU-1")
    S.raw_dir(spec).mkdir(parents=True, exist_ok=True)
    (S.raw_dir(spec) / "MU-1-1.mp3").write_bytes(b"ID3-fake-audio")
    S.save(spec)
    return spec


def _episode(root) -> dict:
    return json.loads((root / "content" / "story_video" / "EP-MU.json").read_text(encoding="utf-8"))


def _podcast(root) -> dict:
    return json.loads((root / "content" / "podcast" / "PC-MU.json").read_text(encoding="utf-8"))


def test_drama_drop_in_lands_in_post_graphics_music(project):
    spec = _music(project)
    out = U.use_music(spec, module="drama", target_id="EP-MU", slot=12.5, project_root=project)
    assert out["title"] == "加班夜" and out["tracks"] == 1
    relative = out["file"]
    assert relative.startswith("content/assets/EP-MU/music/") and relative.endswith(".mp3")
    assert (project / relative).read_bytes() == b"ID3-fake-audio"
    tracks = _episode(project)["post_graphics"]["music"]
    assert len(tracks) == 1
    assert tracks[0]["file"] == relative and tracks[0]["start"] == 12.5 and tracks[0]["label"] == "MU-1-1"
    # used_in 记了去向
    assert spec["used_in"] == [{"module": "drama", "id": "EP-MU", "slot": "12.5", "at": spec["used_in"][0]["at"]}]
    # 走的是 post-layers 那条路：读回来仍然通过校验（后期就绪检查看得到这条轨）
    ready = post_router.readiness(post_router._load("EP-MU")[1])
    assert [m["file"] for m in ready["music"]] == [relative]
    assert ready["music"][0]["missing"] is False and ready["music"][0]["start"] == 12.5


def test_drama_drop_in_survives_the_post_layers_validator(project):
    """校验路径不是摆设：手工把不合法的值塞进去，put_post_layers 会拒绝。"""

    spec = _music(project)
    U.use_music(spec, module="drama", target_id="EP-MU", project_root=project)
    with pytest.raises(U.MusicUseError):
        U.use_music(spec, module="drama", target_id="EP-MU", slot=99_999, project_root=project)
    with pytest.raises(U.MusicUseError):
        U.use_music(spec, module="drama", target_id="EP-MU", slot="不是数字", project_root=project)
    # 目标项目里已有一条坏轨时，这次投放整体被拒（校验在写盘前）
    raw = _episode(project)
    raw["post_graphics"] = {"music": [{"file": "content/assets/EP-MU/music/nope.mp3"}]}
    (project / "content" / "story_video" / "EP-MU.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(U.MusicUseError) as info:
        U.use_music(spec, module="drama", target_id="EP-MU", project_root=project)
    assert "没接受" in str(info.value) and "文件不存在" in str(info.value)


def test_drama_drop_in_twice_replaces_the_same_track(project):
    spec = _music(project)
    U.use_music(spec, module="drama", target_id="EP-MU", slot=0, project_root=project)
    U.use_music(spec, module="drama", target_id="EP-MU", slot=30, project_root=project)
    tracks = _episode(project)["post_graphics"]["music"]
    assert len(tracks) == 1 and tracks[0]["start"] == 30.0
    assert len(spec["used_in"]) == 2       # 位置不同算两条投放记录


def test_drama_drop_in_keeps_other_post_layers(project):
    raw = _episode(project)
    raw["post_graphics"] = {"notebook_text": [{"shot": "S1", "text": "便签"}]}
    raw["design_notes"] = "别动我"
    (project / "content" / "story_video" / "EP-MU.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    spec = _music(project)
    U.use_music(spec, module="drama", target_id="EP-MU", project_root=project)
    after = _episode(project)
    assert after["post_graphics"]["notebook_text"] == [{"shot": "S1", "text": "便签"}]
    assert after["design_notes"] == "别动我"
    assert after["post_graphics"]["music"][0]["file"].startswith("content/assets/EP-MU/music/")


def test_picturebook_drop_in_sets_music_file(project):
    from picturebook import spec as PB

    spec = _music(project)
    out = U.use_music(spec, module="picturebook", target_id="PB-MU", project_root=project)
    assert out["title"] == "小狐狸的三只袜子"
    book = PB.load_book("PB-MU", project_root=project)
    assert book["music"]["file"] == out["file"]
    assert book["music"]["gain"] == PB.DEFAULT_MUSIC_GAIN and book["music"]["fade_out"] == PB.DEFAULT_MUSIC_FADE
    assert (project / out["file"]).is_file()
    assert spec["used_in"][0] == {"module": "picturebook", "id": "PB-MU", "slot": "", "at": spec["used_in"][0]["at"]}
    # 页与标题都没被这次投放改坏
    assert [p["id"] for p in book["pages"]] == ["P01", "P02"] and book["title_zh"] == "小狐狸的三只袜子"


def test_podcast_drop_in_defaults_to_bed_and_lands_in_music_slot(project):
    from podcast import spec as PC

    spec = _music(project)
    out = U.use_music(spec, module="podcast", target_id="PC-MU", project_root=project)
    assert out["title"] == "为什么我们戒不掉短视频"
    assert out["slot"] == "bed" and out["slot_label"] == "垫乐" and out["tracks"] == 1 and out["slots"] == ["bed"]
    relative = out["file"]
    assert relative.startswith("content/assets/PC-MU/music/") and relative.endswith(".mp3")
    assert (project / relative).read_bytes() == b"ID3-fake-audio"
    # 项目文件：只动了 bed，别的槽与音量参数原样；读回来仍过归一化，resolve_asset 能找到文件
    raw = _podcast(project)
    assert raw["music"]["bed"] == relative and raw["music"]["intro"] == "" and raw["music"]["outro"] == ""
    assert raw["music"]["bed_gain"] == 0.2 and raw["music"]["duck"] == PC.DEFAULT_MUSIC["duck"]
    assert [h["id"] for h in raw["hosts"]] == ["zhou", "xia"] and raw["segments"][0]["lines"][1]["who"] == "xia"
    loaded = PC.load("PC-MU", project_root=project)
    assert PC.resolve_asset(loaded, loaded["music"]["bed"]) == (project / relative).resolve()
    # used_in 记的是槽名
    assert spec["used_in"] == [{"module": "podcast", "id": "PC-MU", "slot": "bed", "at": spec["used_in"][0]["at"]}]


def test_podcast_drop_in_named_slots_and_overwrite(project):
    spec = _music(project)
    intro = U.use_music(spec, module="podcast", target_id="PC-MU", slot="intro", project_root=project)
    assert intro["slot"] == "intro" and intro["slot_label"] == "片头"
    outro = U.use_music(spec, module="podcast", target_id="PC-MU", slot=" outro ", project_root=project)
    assert outro["slot"] == "outro" and outro["slot_label"] == "片尾" and outro["tracks"] == 2 and outro["slots"] == ["intro", "outro"]
    raw = _podcast(project)
    assert raw["music"]["intro"] == intro["file"] and raw["music"]["outro"] == outro["file"] and raw["music"]["bed"] == ""
    # 同一个槽再投一次：覆盖，used_in 同槽只留最新一条；同一条文件不堆副本
    again = U.use_music(spec, module="podcast", target_id="PC-MU", slot="intro", project_root=project)
    assert _podcast(project)["music"]["intro"] == again["file"]
    assert [(r["id"], r["slot"]) for r in spec["used_in"]] == [("PC-MU", "outro"), ("PC-MU", "intro")]
    assert len(list((project / "content" / "assets" / "PC-MU" / "music").glob("*"))) == 1


def test_podcast_drop_in_rejects_bad_slot_before_touching_anything(project):
    spec = _music(project)
    before = _podcast(project)
    for bad in ("loop", 12, 12.5, "bgm"):
        with pytest.raises(U.MusicUseError) as info:
            U.use_music(spec, module="podcast", target_id="PC-MU", slot=bad, project_root=project)
        assert "槽名" in str(info.value) and "intro（片头）" in str(info.value) and f"收到「{bad}」" in str(info.value)
    assert _podcast(project) == before
    assert not (project / "content" / "assets" / "PC-MU").exists()
    assert spec["used_in"] == []


def test_podcast_drop_in_goes_through_podcast_normalize(project):
    """不绕过归一化：目标播客本身形状不对（说话人不在主播表里）→ 读不出来，什么都不写。"""

    spec = _music(project)
    raw = _podcast(project)
    raw["segments"][0]["lines"].append({"who": "nobody", "text": "我是谁"})
    (project / "content" / "podcast" / "PC-MU.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(U.MusicUseError) as info:
        U.use_music(spec, module="podcast", target_id="PC-MU", project_root=project)
    assert "目标播客读不出来" in str(info.value) and "nobody" in str(info.value)
    assert _podcast(project) == raw and spec["used_in"] == []


def test_unknown_target_and_module(project):
    spec = _music(project)
    with pytest.raises(U.UnknownTarget):
        U.use_music(spec, module="drama", target_id="NOPE", project_root=project)
    with pytest.raises(U.UnknownTarget):
        U.use_music(spec, module="picturebook", target_id="NOPE", project_root=project)
    with pytest.raises(U.UnknownTarget) as info:
        U.use_music(spec, module="podcast", target_id="NOPE", project_root=project)
    assert "没有这期播客：NOPE" in str(info.value)
    with pytest.raises(U.UnknownTarget):
        U.use_music(spec, module="podcast", target_id="../PC-MU", project_root=project)
    with pytest.raises(U.MusicUseError):
        U.use_music(spec, module="sticker", target_id="ST-1", project_root=project)
    with pytest.raises(U.MusicUseError):
        U.use_music(spec, module="drama", target_id="", project_root=project)
    assert spec["used_in"] == []


def test_nothing_adopted_yet(project):
    spec = _music(project)
    spec["current"] = None
    with pytest.raises(U.MusicUseError) as info:
        U.use_music(spec, module="drama", target_id="EP-MU", project_root=project)
    assert "还没有采用" in str(info.value)


def test_list_targets_lists_episodes_and_books(project):
    targets = U.list_targets(project)
    assert [r["id"] for r in targets["drama"]] == ["EP-MU"]
    assert targets["drama"][0]["title"] == "加班夜" and targets["drama"][0]["tracks"] == 0
    assert [r["id"] for r in targets["picturebook"]] == ["PB-MU"]
    assert targets["picturebook"][0]["title"] == "小狐狸的三只袜子" and targets["picturebook"][0]["tracks"] == 0
    assert targets["podcast"] == [{"module": "podcast", "id": "PC-MU", "title": "为什么我们戒不掉短视频", "tracks": 0, "slots": []}]
    # 剧本层文件不是项目；坏文件不让下拉框空掉
    (project / "content" / "story_video" / "EP-MU.screenplay.json").write_text("{}", encoding="utf-8")
    (project / "content" / "story_video" / "BAD.json").write_text("{not json", encoding="utf-8")
    again = U.list_targets(project)
    assert [r["id"] for r in again["drama"]] == ["BAD", "EP-MU"]
    assert next(r for r in again["drama"] if r["id"] == "BAD")["title"] == "BAD"
    # 投放之后目标行显示已有几条
    spec = _music(project)
    U.use_music(spec, module="drama", target_id="EP-MU", project_root=project)
    U.use_music(spec, module="picturebook", target_id="PB-MU", project_root=project)
    U.use_music(spec, module="podcast", target_id="PC-MU", slot="outro", project_root=project)
    U.use_music(spec, module="podcast", target_id="PC-MU", project_root=project)
    after = U.list_targets(project)
    assert next(r for r in after["drama"] if r["id"] == "EP-MU")["tracks"] == 1
    assert after["picturebook"][0]["tracks"] == 1
    assert after["podcast"][0]["tracks"] == 2 and after["podcast"][0]["slots"] == ["bed", "outro"]
    assert U.target_title(project, "podcast", "PC-MU") == "为什么我们戒不掉短视频"


def test_same_file_is_not_copied_twice(project):
    spec = _music(project)
    U.use_music(spec, module="drama", target_id="EP-MU", project_root=project)
    U.use_music(spec, module="drama", target_id="EP-MU", slot=5, project_root=project)
    files = sorted(p.name for p in (project / "content" / "assets" / "EP-MU" / "music").glob("*"))
    assert len(files) == 1


# ---------------------------------------------------------------------------- 目标 id 校验：拷文件之前就拒


def _stray_copies(root) -> list[str]:
    """项目根下所有投放出来的副本（content/assets/… 之外出现的 mp3 就是穿越了）。"""

    return [str(p.relative_to(root)) for p in root.rglob("*.mp3") if p.parent.name == "music"]


def test_drama_traversal_target_is_rejected_before_any_copy(project):
    """以前 target 只查「拼出来的 .json 在不在」：``../picturebook/PB-MU`` 先把 mp3 拷进绘本的 content 目录，
    再被 post-layers 的边界检查用一句带本机绝对路径的英文拒绝。"""

    spec = _music(project)
    with pytest.raises(U.UnknownTarget) as info:
        U.use_music(spec, module="drama", target_id="../picturebook/PB-MU", project_root=project)
    assert str(info.value) == "没有这个目标项目：../picturebook/PB-MU"
    assert str(project) not in str(info.value)
    assert _stray_copies(project) == []
    assert not (project / "content" / "picturebook" / "PB-MU").exists()
    assert spec["used_in"] == []
    assert "music" not in json.loads((project / "content" / "picturebook" / "PB-MU.json").read_text(encoding="utf-8"))


def test_drama_target_cannot_write_outside_the_project_root(project):
    (project / "settings.json").write_text("{}", encoding="utf-8")       # content/story_video/../../settings.json 存在
    spec = _music(project)
    with pytest.raises(U.UnknownTarget):
        U.use_music(spec, module="drama", target_id="../../settings", project_root=project)
    assert not (project / "settings").exists() and _stray_copies(project) == []


@pytest.mark.parametrize("module", ["drama", "picturebook", "podcast"])
@pytest.mark.parametrize("target", ["../EP-MU", "a/b", ".hidden", "EP MU", "x" * 64])
def test_every_module_validates_the_target_id_first(project, module, target):
    spec = _music(project)
    with pytest.raises(U.UnknownTarget):
        U.use_music(spec, module=module, target_id=target, project_root=project)
    assert _stray_copies(project) == [] and spec["used_in"] == []


def test_copy_into_refuses_to_leave_the_assets_dir(project):
    source = S.raw_dir(_music(project)) / "MU-1-1.mp3"
    with pytest.raises(U.MusicUseError):
        U._copy_into(source, project, "../evil")
    assert not (project / "content" / "evil").exists() and _stray_copies(project) == []


def test_drama_bad_slot_or_unreadable_target_leaves_no_copy(project):
    spec = _music(project)
    with pytest.raises(U.MusicUseError):
        U.use_music(spec, module="drama", target_id="EP-MU", slot=99999, project_root=project)
    assert not (project / "content" / "assets" / "EP-MU").exists()
    (project / "content" / "story_video" / "EP-BAD.json").write_text('{"id": "EP-BAD"}', encoding="utf-8")
    with pytest.raises(U.MusicUseError) as info:
        U.use_music(spec, module="drama", target_id="EP-BAD", project_root=project)
    assert str(info.value).startswith("目标项目读不出来：")
    assert not (project / "content" / "assets" / "EP-BAD").exists()
    assert spec["used_in"] == []


def test_unreadable_drama_target_message_has_no_absolute_path(project):
    """剧集文件坏成半截 JSON：校验原文是「invalid JSON in episode spec <绝对路径> …」，报给界面前去掉项目根。"""

    spec = _music(project)
    (project / "content" / "story_video" / "EP-HALF.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(U.MusicUseError) as info:
        U.use_music(spec, module="drama", target_id="EP-HALF", project_root=project)
    message = str(info.value)
    assert message.startswith("目标项目读不出来：") and "EP-HALF.json" in message
    assert str(project) not in message and str(project.resolve()) not in message
    assert not (project / "content" / "assets" / "EP-HALF").exists() and spec["used_in"] == []
