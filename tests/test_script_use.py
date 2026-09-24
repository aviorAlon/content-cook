"""投放与导入（契约 docs/scriptlab-spec.md §7）：三种目标各自的落点与写回路径（一律过目标模块自己的归一化）、
``used_in`` 记去向、角色映射缺失 400、剧集锁定 400、目标 / 落点不存在 404，以及反向从三种目标导入。

写回路径是这里的重点：播客走 ``podcast.spec.normalize`` + ``save``，剧本层走 ``normalize_screenplay``，
分镜走 ``storyvideo.spec.normalize_spec``——目标项目说不行就不落盘。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from script import spec as S
from script import use as U
from webui import deps, tasks
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import script as script_router

client = TestClient(app)
API = "/api/script"

EPISODE = {
    "id": "EP-1", "title_zh": "加班夜", "output_root": "output/story_video/EP-1", "prompt_language": "zh",
    "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
    # office 只有 block（场景锚）、desk 显式标了 kind=prop：两条都不是人物，投放 / 导入的角色表里不该出现
    "cast": {"lin": {"name": "林小满", "block": "黑发。"}, "jie": {"name": "阿杰", "block": "灰衬衫。"},
             "office": {"block": "开放式办公区。"}, "desk": {"kind": "prop", "name": "工位", "block": "灰色工位。"}},
    "shots": [{"id": "S1", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m", "lines": []},
              {"id": "S2", "dur": 4, "cast": ["jie"], "shot": "中景", "art": "a", "motion": "m",
               "lines": [{"who": "jie", "zh": "原来就有的一句。"}]}],
}
SCREENPLAY = {
    "title": "加班夜", "logline": "一句梗概",
    "cast": [{"id": "lin", "name": "林小满"}, {"id": "jie", "name": "阿杰"}],
    "scenes": [{"id": "S01", "heading": "内 · 办公室 · 夜", "beats": ["林小满关掉台灯"],
                "dialogue": [{"who": "lin", "zh": "你先走。"}]},
               {"id": "S02", "heading": "内 · 走廊 · 夜", "beats": ["两人并排走"], "dialogue": []}],
}
PODCAST = {
    "id": "PC-1", "title": "为什么我们戒不掉短视频", "format": "duo",
    "hosts": [{"id": "zhou", "name": "老周"}, {"id": "xia", "name": "小夏"}],
    "segments": [{"id": "G01", "kind": "cold_open", "title": "冷开场",
                  "lines": [{"who": "zhou", "text": "先说个事。"}]},
                 {"id": "G02", "kind": "topic", "title": "话题", "lines": []}],
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    story = tmp_path / "content" / "story_video"
    story.mkdir(parents=True)
    (tmp_path / "content" / "podcast").mkdir(parents=True)
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "STORYVIDEO_DIR", story)
    monkeypatch.setattr(tasks, "tasks_dir", lambda: tmp_path / ".webui_tasks")
    monkeypatch.setattr(script_router, "DISPATCH", None)
    (story / "EP-1.json").write_text(json.dumps(EPISODE, ensure_ascii=False), encoding="utf-8")
    (story / "EP-1.screenplay.json").write_text(json.dumps(SCREENPLAY, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "content" / "podcast" / "PC-1.json").write_text(json.dumps(PODCAST, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _script(root, target="podcast") -> dict:
    spec = S.normalize({
        "title": "下班后的消息，要不要回", "target": target, "language": "zh",
        "speakers": [{"id": "jie", "name": "阿杰"}, {"id": "lin", "name": "小林"}],
        "blocks": [{"id": "B01", "title": "冷开场", "goal": "拽进来"}, {"id": "B02", "title": "分歧", "goal": "不和解"}],
        "versions": [{"id": "V1", "source": "hand", "blocks": [
            {"id": "B01", "lines": [{"who": "jie", "text": "上周六晚上十点。"}, {"who": "lin", "text": "然后呢？"}]},
            {"id": "B02", "lines": [{"who": "lin", "text": "可是你那套行不通。"}]},
        ]}],
        "current": "V1",
    }, project_root=root, spec_id="SC-1")
    S.save(spec)
    return spec


def _podcast(root) -> dict:
    return json.loads((root / "content" / "podcast" / "PC-1.json").read_text(encoding="utf-8"))


def _screenplay(root) -> dict:
    return json.loads((root / "content" / "story_video" / "EP-1.screenplay.json").read_text(encoding="utf-8"))


def _episode(root) -> dict:
    return json.loads((root / "content" / "story_video" / "EP-1.json").read_text(encoding="utf-8"))


def _lock(root) -> None:
    manifests = root / "output" / "story_video" / "EP-1" / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / "episode.snapshot.json").write_text("{}", encoding="utf-8")


# ---------------------------------------------------------------------------- 目标清单


def test_targets_lists_spots_roles_and_lock(project):
    targets = client.get(f"{API}/targets").json()
    pc = targets["podcast"][0]
    assert pc["id"] == "PC-1" and pc["title"] == "为什么我们戒不掉短视频"
    assert pc["segments"] == [{"id": "G01", "title": "冷开场", "lines": 1}, {"id": "G02", "title": "话题", "lines": 0}]
    assert [h["id"] for h in pc["hosts"]] == ["zhou", "xia"]
    ep = targets["drama"][0]
    assert ep["id"] == "EP-1" and ep["title"] == "加班夜" and ep["locked"] is False
    assert [s["id"] for s in ep["scenes"]] == ["S01", "S02"] and ep["scenes"][0]["lines"] == 1
    assert [s["id"] for s in ep["shots"]] == ["S1", "S2"] and ep["shots"][1]["lines"] == 1
    # 镜次没有 title / heading，景别那句在 shot 字段里——取它，不然投放页的镜号下拉只剩裸镜号
    assert [s["title"] for s in ep["shots"]] == ["近景", "中景"]
    assert [c["id"] for c in ep["cast"]] == ["lin", "jie"]     # 场景 office / 道具 desk 不是人物，不进映射候选
    _lock(project)
    assert client.get(f"{API}/targets").json()["drama"][0]["locked"] is True


def test_use_refuses_mapping_a_speaker_onto_a_scene_or_prop(project):
    """短剧 cast 里的地点 / 道具（只有 block 没有 name，或显式 kind）不是人物：映射到它 400、文件一个字不动。

    放行的话 ``who: office`` 会照写进镜里——地点名进付费出片提示词，配音又找不到它的音色（角色页对场景条目不显示音色）。"""

    _script(project, target="drama_shot")
    before = _episode(project)
    for stray in ("office", "desk"):
        r = client.post(f"{API}/SC-1/use", json={"module": "drama_shot", "target_id": "EP-1",
                                                 "blocks": {"B01": "S1"}, "mapping": {"jie": stray, "lin": "lin"}})
        assert r.status_code == 400 and f"「{stray}」" in r.json()["detail"], r.json()
    assert _episode(project) == before
    # 导入同样只认人物：地点「开口」的那一句跳过，角色表里也不会多出 office
    episode = json.loads(json.dumps(EPISODE))
    episode["shots"][0]["lines"] = [{"who": "office", "zh": "环境音。"}, {"who": "lin", "zh": "你先走。"}]
    (project / "content" / "story_video" / "EP-1.json").write_text(json.dumps(episode, ensure_ascii=False), encoding="utf-8")
    S.save(S.normalize({"title": "拿过来调", "target": "drama_shot", "speakers": [], "blocks": [], "versions": []},
                       project_root=project, spec_id="SC-9"))
    r = client.post(f"{API}/SC-9/import", json={"module": "drama_shot", "target_id": "EP-1", "blocks": ["S1"]})
    assert r.status_code == 200, r.json()
    saved = r.json()["detail"]["spec"]
    assert [s["id"] for s in saved["speakers"]] == ["lin"]
    assert [l["text"] for l in saved["versions"][0]["blocks"][0]["lines"]] == ["你先走。"]


# ---------------------------------------------------------------------------- 投放：播客


def test_use_in_podcast_writes_segments_and_records_used_in(project):
    _script(project)
    r = client.post(f"{API}/SC-1/use", json={
        "module": "podcast", "target_id": "PC-1",
        "blocks": {"B01": "G01", "B02": "new"}, "mapping": {"jie": "zhou", "lin": "xia"}, "mode": "replace"})
    assert r.status_code == 200, r.json()
    out = r.json()
    assert out["target_title"] == "为什么我们戒不掉短视频" and out["spots"] == ["G01", "G03"]
    assert out["message"] == "已放进《为什么我们戒不掉短视频》的环节 G01、G03"
    saved = _podcast(project)
    g01 = next(s for s in saved["segments"] if s["id"] == "G01")
    assert [l["text"] for l in g01["lines"]] == ["上周六晚上十点。", "然后呢？"]   # replace：原来那句被换掉
    assert [l["who"] for l in g01["lines"]] == ["zhou", "xia"]
    g03 = next(s for s in saved["segments"] if s["id"] == "G03")
    assert g03["kind"] == "topic" and g03["title"] == "分歧" and g03["lines"][0]["who"] == "xia"
    # used_in 记去向；同一落点再投一次只留最新一条
    used = out["detail"]["spec"]["used_in"]
    assert [(u["module"], u["id"], u["target"]) for u in used] == [("podcast", "PC-1", "G01"), ("podcast", "PC-1", "G03")]
    again = client.post(f"{API}/SC-1/use", json={"module": "podcast", "target_id": "PC-1", "blocks": {"B01": "G01"},
                                                 "mapping": {"jie": "zhou", "lin": "xia"}, "mode": "append"})
    assert again.status_code == 200
    assert len(again.json()["detail"]["spec"]["used_in"]) == 2
    assert len(_podcast(project)["segments"][0]["lines"]) == 4    # append：接在后面


def test_use_in_podcast_checks_mapping_and_spots(project):
    _script(project)
    missing = client.post(f"{API}/SC-1/use", json={"module": "podcast", "target_id": "PC-1",
                                                   "blocks": {"B01": "G01"}, "mapping": {"jie": "zhou"}})
    assert missing.status_code == 400 and "「小林」" in missing.json()["detail"]
    stray = client.post(f"{API}/SC-1/use", json={"module": "podcast", "target_id": "PC-1", "blocks": {"B01": "G01"},
                                                 "mapping": {"jie": "zhou", "lin": "ghost"}})
    assert stray.status_code == 400 and "ghost" in stray.json()["detail"]
    nowhere = client.post(f"{API}/SC-1/use", json={"module": "podcast", "target_id": "PC-9", "blocks": {"B01": "G01"},
                                                   "mapping": {"jie": "zhou", "lin": "xia"}})
    assert nowhere.status_code == 404 and "没有这期播客" in nowhere.json()["detail"]
    bad_spot = client.post(f"{API}/SC-1/use", json={"module": "podcast", "target_id": "PC-1", "blocks": {"B01": "G09"},
                                                    "mapping": {"jie": "zhou", "lin": "xia"}})
    assert bad_spot.status_code == 404 and "没有这个环节：G09" in bad_spot.json()["detail"]
    bad_block = client.post(f"{API}/SC-1/use", json={"module": "podcast", "target_id": "PC-1", "blocks": {"B09": "G01"},
                                                     "mapping": {"jie": "zhou", "lin": "xia"}})
    assert bad_block.status_code == 400 and "B09" in bad_block.json()["detail"]
    nothing = client.post(f"{API}/SC-1/use", json={"module": "podcast", "target_id": "PC-1", "blocks": {},
                                                   "mapping": {"jie": "zhou", "lin": "xia"}})
    assert nothing.status_code == 400 and "至少给一段选一个落点" in nothing.json()["detail"]
    bad_mode = client.post(f"{API}/SC-1/use", json={"module": "podcast", "target_id": "PC-1", "blocks": {"B01": "G01"},
                                                    "mapping": {"jie": "zhou", "lin": "xia"}, "mode": "overwrite"})
    assert bad_mode.status_code == 400 and "mode 只能是" in bad_mode.json()["detail"]
    assert _podcast(project)["segments"][0]["lines"][0]["text"] == "先说个事。"    # 一次都没写盘


def test_use_skips_blocks_without_lines_instead_of_wiping_the_spot(project):
    """选了落点但这一版这一段还没写台词：跳过它，**绝不**拿空台词 replace 掉目标那边原有的台词——
    播客 / 短剧没有版本留痕，删掉就找不回来。"""

    spec = _script(project)
    raw = S.to_disk(spec)
    raw["versions"][0]["blocks"] = [b for b in raw["versions"][0]["blocks"] if b["id"] != "B01"]
    S.save(S.normalize(raw, project_root=project, spec_id="SC-1"))
    before = [l["text"] for l in _podcast(project)["segments"][0]["lines"]]
    r = client.post(f"{API}/SC-1/use", json={
        "module": "podcast", "target_id": "PC-1", "blocks": {"B01": "G01", "B02": "G02"},
        "mapping": {"jie": "zhou", "lin": "xia"}})
    assert r.status_code == 200, r.json()
    out = r.json()
    assert out["spots"] == ["G02"] and out["skipped"] == ["B01"]
    assert out["message"] == "已放进《为什么我们戒不掉短视频》的环节 G02"
    saved = _podcast(project)
    assert [l["text"] for l in saved["segments"][0]["lines"]] == before   # G01 原有的台词一个字没动
    assert [l["text"] for l in saved["segments"][1]["lines"]] == ["可是你那套行不通。"]
    assert [(u["module"], u["target"]) for u in out["detail"]["spec"]["used_in"]] == [("podcast", "G02")]
    # 选中的段全都没台词 = 什么也不投（还是那句「这一版还没有台词」），目标一个字不动
    empty = client.post(f"{API}/SC-1/use", json={"module": "podcast", "target_id": "PC-1", "blocks": {"B01": "G01"},
                                                 "mapping": {"jie": "zhou", "lin": "xia"}})
    assert empty.status_code == 400 and empty.json()["detail"] == U.NO_LINES_MESSAGE
    assert [l["text"] for l in _podcast(project)["segments"][0]["lines"]] == before


def test_use_without_any_spot_says_so_even_before_there_is_a_version(project):
    """一个落点都没选、同时项目又还没有任何版本：毛病是「没选落点」，不是「还没有台词」——
    先报后一句会把人引到「去生成一版」那条错路上。"""

    S.save(S.normalize({"title": "还没写", "target": "podcast", "speakers": [{"id": "jie", "name": "阿杰"}],
                        "blocks": [{"id": "B01", "title": "冷开场"}], "versions": []},
                       project_root=project, spec_id="SC-9"))
    nothing = client.post(f"{API}/SC-9/use", json={"module": "podcast", "target_id": "PC-1",
                                                   "blocks": {}, "mapping": {}})
    assert nothing.status_code == 400 and "至少给一段选一个落点" in nothing.json()["detail"]
    # 选了落点才轮到「这一版还没有台词」
    picked = client.post(f"{API}/SC-9/use", json={"module": "podcast", "target_id": "PC-1",
                                                  "blocks": {"B01": "G01"}, "mapping": {}})
    assert picked.status_code == 400 and picked.json()["detail"] == U.NO_LINES_MESSAGE


# ---------------------------------------------------------------------------- 投放：剧本层


def test_use_in_screenplay_writes_scenes_through_normalize(project):
    _script(project, target="drama_screenplay")
    r = client.post(f"{API}/SC-1/use", json={"module": "drama_screenplay", "target_id": "EP-1",
                                             "blocks": {"B01": "S01", "B02": "new"},
                                             "mapping": {"jie": "jie", "lin": "lin"}})
    assert r.status_code == 200, r.json()
    assert r.json()["spots"] == ["S01", "S03"]
    assert r.json()["message"] == "已放进《加班夜》的场次 S01、S03"
    saved = _screenplay(project)
    s01 = next(s for s in saved["scenes"] if s["id"] == "S01")
    assert s01["dialogue"] == [{"who": "jie", "zh": "上周六晚上十点。"}, {"who": "lin", "zh": "然后呢？"}]
    assert s01["beats"] == ["林小满关掉台灯"]              # 归一化保留原有字段
    s03 = next(s for s in saved["scenes"] if s["id"] == "S03")
    assert s03["heading"] == "分歧" and s03["dialogue"][0]["who"] == "lin"
    # 落点 404 / 目标 404
    assert client.post(f"{API}/SC-1/use", json={"module": "drama_screenplay", "target_id": "EP-1",
                                                "blocks": {"B01": "S09"},
                                                "mapping": {"jie": "jie", "lin": "lin"}}).status_code == 404
    assert client.post(f"{API}/SC-1/use", json={"module": "drama_screenplay", "target_id": "EP-9",
                                                "blocks": {"B01": "S01"},
                                                "mapping": {"jie": "jie", "lin": "lin"}}).status_code == 404


def test_use_in_screenplay_needs_a_title_for_a_new_scene_and_a_screenplay_file(project):
    spec = _script(project, target="drama_screenplay")
    raw = S.to_disk(spec)
    raw["blocks"][1]["title"] = ""
    S.save(S.normalize(raw, project_root=project, spec_id="SC-1"))
    r = client.post(f"{API}/SC-1/use", json={"module": "drama_screenplay", "target_id": "EP-1",
                                             "blocks": {"B02": "new"}, "mapping": {"lin": "lin"}})
    assert r.status_code == 400 and "还没有标题" in r.json()["detail"]
    (project / "content" / "story_video" / "EP-1.screenplay.json").unlink()
    r = client.post(f"{API}/SC-1/use", json={"module": "drama_screenplay", "target_id": "EP-1",
                                             "blocks": {"B01": "S01"}, "mapping": {"jie": "jie", "lin": "lin"}})
    assert r.status_code == 400 and "还没有剧本" in r.json()["detail"]


# ---------------------------------------------------------------------------- 投放：分镜


def test_use_in_shot_writes_lines_and_refuses_a_locked_episode(project):
    _script(project, target="drama_shot")
    r = client.post(f"{API}/SC-1/use", json={"module": "drama_shot", "target_id": "EP-1",
                                             "blocks": {"B01": "S1", "B02": "S2"},
                                             "mapping": {"jie": "jie", "lin": "lin"}})
    assert r.status_code == 200, r.json()
    assert r.json()["spots"] == ["S1", "S2"] and r.json()["message"] == "已放进《加班夜》的镜号 S1、S2"
    shots = {s["id"]: s for s in _episode(project)["shots"]}
    assert shots["S1"]["lines"] == [{"who": "jie", "zh": "上周六晚上十点。"}, {"who": "lin", "zh": "然后呢？"}]
    assert shots["S2"]["lines"] == [{"who": "lin", "zh": "可是你那套行不通。"}]   # replace 把原来那句换掉
    # 没有「新建」这个落点
    new_spot = client.post(f"{API}/SC-1/use", json={"module": "drama_shot", "target_id": "EP-1",
                                                    "blocks": {"B01": "new"}, "mapping": {"jie": "jie", "lin": "lin"}})
    assert new_spot.status_code == 400 and "不能新建镜号" in new_spot.json()["detail"]
    assert client.post(f"{API}/SC-1/use", json={"module": "drama_shot", "target_id": "EP-1",
                                                "blocks": {"B01": "S9"},
                                                "mapping": {"jie": "jie", "lin": "lin"}}).status_code == 404
    # 锁定后 400，文件一个字不动
    _lock(project)
    before = _episode(project)
    locked = client.post(f"{API}/SC-1/use", json={"module": "drama_shot", "target_id": "EP-1",
                                                  "blocks": {"B01": "S1"}, "mapping": {"jie": "jie", "lin": "lin"}})
    assert locked.status_code == 400 and locked.json()["detail"] == U.LOCKED_MESSAGE
    assert _episode(project) == before


def test_use_in_shot_is_refused_when_the_episode_would_not_validate(project):
    """双语字幕的剧集要求每句都有 en——工作台只给 zh，目标模块的归一化说不行，这边就不落盘。"""

    raw = dict(EPISODE, subtitle={"language": "bilingual"})
    (project / "content" / "story_video" / "EP-1.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    _script(project, target="drama_shot")
    r = client.post(f"{API}/SC-1/use", json={"module": "drama_shot", "target_id": "EP-1",
                                             "blocks": {"B01": "S1"}, "mapping": {"jie": "jie", "lin": "lin"}})
    assert r.status_code == 400 and r.json()["detail"].startswith("目标项目没接受这些台词：")
    assert _episode(project)["shots"][0]["lines"] == []


# ---------------------------------------------------------------------------- 导入


def test_import_from_podcast_brings_hosts_blocks_and_a_new_version(project):
    spec = S.normalize({"title": "拿过来调", "target": "podcast", "speakers": [], "blocks": [], "versions": []},
                       project_root=project, spec_id="SC-2")
    S.save(spec)
    r = client.post(f"{API}/SC-2/import", json={"module": "podcast", "target_id": "PC-1"})
    assert r.status_code == 200, r.json()
    out = r.json()
    # 只补这次真开口的人：小夏挂在 hosts 上但这一句不是她说的，等她真开口了再补
    assert out["imported"] == 1 and out["version"] == "V1" and out["speakers"] == ["zhou"]
    assert out["message"] == "已从《为什么我们戒不掉短视频》导入 1 句"
    saved = out["detail"]["spec"]
    assert [s["id"] for s in saved["speakers"]] == ["zhou"]
    assert [b["id"] for b in saved["blocks"]] == ["G01"]        # 空环节不导，骨架按落点 id 补
    assert saved["blocks"][0]["title"] == "冷开场"
    assert saved["versions"][0]["source"] == "import"
    assert saved["versions"][0]["label"] == "从《为什么我们戒不掉短视频》导入"
    assert saved["versions"][0]["blocks"][0]["lines"] == [{"who": "zhou", "text": "先说个事。", "cue": "", "kind": "talk"}]
    assert saved["current"] == "V1"                             # 第一版自动采用（之后的版本才要人比过再挑）
    # 只导某几个落点；落点写错 404
    assert client.post(f"{API}/SC-2/import", json={"module": "podcast", "target_id": "PC-1",
                                                   "blocks": ["G09"]}).status_code == 404
    empty = client.post(f"{API}/SC-2/import", json={"module": "podcast", "target_id": "PC-1", "blocks": ["G02"]})
    assert empty.status_code == 400 and empty.json()["detail"] == U.NOTHING_TO_IMPORT


def test_import_marks_a_listener_qa_segment_as_a_qa_block(project):
    """播客的听友问答环节导进来还是问答段：回答太短那三条判据只认段种类，种类丢在门口它们就再也判不到了。"""

    path = project / "content" / "podcast" / "PC-1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["segments"].append({"id": "G03", "kind": "qa", "title": "听友问答",
                             "questions": [{"from": "小王", "text": "怎么戒？"}],
                             "lines": [{"who": "zhou", "text": "小王问怎么戒。", "kind": "question"},
                                       {"who": "xia", "text": "别指望意志力。", "kind": "answer"}]})
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    spec = S.normalize({"title": "拿过来调", "target": "podcast", "speakers": [], "blocks": [], "versions": []},
                       project_root=project, spec_id="SC-3")
    S.save(spec)
    r = client.post(f"{API}/SC-3/import", json={"module": "podcast", "target_id": "PC-1"})
    assert r.status_code == 200, r.json()
    assert {b["id"]: b["kind"] for b in r.json()["detail"]["spec"]["blocks"]} == {"G01": "talk", "G03": "qa"}


def test_import_from_screenplay_and_from_shots(project):
    # 人物表挂着 lin / jie 两个人，可这一场 / 这一镜只有一个开口——只补开口的那个
    for spec_id, module, expect_block, expect_text, expect_speakers in (
            ("SC-3", "drama_screenplay", "S01", "你先走。", ["lin"]),
            ("SC-4", "drama_shot", "S2", "原来就有的一句。", ["jie"])):
        S.save(S.normalize({"title": "拿过来调", "target": module, "speakers": [], "blocks": [], "versions": []},
                           project_root=project, spec_id=spec_id))
        r = client.post(f"{API}/{spec_id}/import", json={"module": module, "target_id": "EP-1"})
        assert r.status_code == 200, r.json()
        saved = r.json()["detail"]["spec"]
        assert [s["id"] for s in saved["speakers"]] == expect_speakers
        assert [b["id"] for b in saved["blocks"]] == [expect_block]
        assert saved["versions"][0]["blocks"][0]["lines"][0]["text"] == expect_text
        assert saved["versions"][0]["source"] == "import"
    assert client.post(f"{API}/SC-3/import", json={"module": "drama_screenplay",
                                                   "target_id": "EP-9"}).status_code == 404
    assert client.post(f"{API}/SC-3/import", json={"module": "nope", "target_id": "EP-1"}).status_code == 400


def test_import_skips_lines_whose_speaker_is_not_in_the_cast(project):
    """旁白（narrator）在剧本层是合法的说话人，工作台的角色表里没有它——跳过，不要整份拒绝。"""

    screenplay = json.loads(json.dumps(SCREENPLAY))
    screenplay["scenes"][0]["dialogue"] = [{"who": "narrator", "zh": "画外音。"}, {"who": "lin", "zh": "你先走。"}]
    (project / "content" / "story_video" / "EP-1.screenplay.json").write_text(
        json.dumps(screenplay, ensure_ascii=False), encoding="utf-8")
    S.save(S.normalize({"title": "拿过来调", "target": "drama_screenplay", "speakers": [], "blocks": [], "versions": []},
                       project_root=project, spec_id="SC-5"))
    r = client.post(f"{API}/SC-5/import", json={"module": "drama_screenplay", "target_id": "EP-1"})
    assert r.status_code == 200 and r.json()["imported"] == 1
    lines = r.json()["detail"]["spec"]["versions"][0]["blocks"][0]["lines"]
    assert [l["text"] for l in lines] == ["你先走。"]


def test_import_only_brings_speakers_who_actually_speak(project):
    """目标那边挂着 7 个人物、这一镜只有 1 个开口：只补开口的那个。

    整张人物表塞进来的话，``S.normalize`` 的「角色表最多 6 人」会把整次导入打回，而且错话怪的是工作台
    自己的角色表，不是来源项目——人看了只会去删自己的角色，删不掉毛病。"""

    episode = json.loads(json.dumps(EPISODE))
    episode["cast"] = {f"c{i}": {"name": f"人物{i}", "block": "黑发。"} for i in range(1, 8)}
    episode["shots"] = [{"id": "S1", "dur": 4, "cast": ["c1"], "shot": "近景", "art": "a", "motion": "m",
                         "lines": [{"who": "c1", "zh": "就这一句。"}]}]
    (project / "content" / "story_video" / "EP-1.json").write_text(
        json.dumps(episode, ensure_ascii=False), encoding="utf-8")
    S.save(S.normalize({"title": "拿过来调", "target": "drama_shot", "speakers": [], "blocks": [], "versions": []},
                       project_root=project, spec_id="SC-6"))
    r = client.post(f"{API}/SC-6/import", json={"module": "drama_shot", "target_id": "EP-1"})
    assert r.status_code == 200, r.json()
    saved = r.json()["detail"]["spec"]
    assert [s["id"] for s in saved["speakers"]] == ["c1"]
    assert saved["versions"][0]["blocks"][0]["lines"][0]["who"] == "c1"

    # 开口的人本身就超过上限：点名来源，不让「角色表最多 6 人」那句漏出来
    episode["shots"] = [{"id": f"S{i}", "dur": 4, "cast": [f"c{i}"], "shot": "近景", "art": "a", "motion": "m",
                         "lines": [{"who": f"c{i}", "zh": f"第 {i} 句。"}]} for i in range(1, 8)]
    (project / "content" / "story_video" / "EP-1.json").write_text(
        json.dumps(episode, ensure_ascii=False), encoding="utf-8")
    S.save(S.normalize({"title": "拿过来调", "target": "drama_shot", "speakers": [], "blocks": [], "versions": []},
                       project_root=project, spec_id="SC-7"))
    too_many = client.post(f"{API}/SC-7/import", json={"module": "drama_shot", "target_id": "EP-1"})
    assert too_many.status_code == 400
    detail = too_many.json()["detail"]
    assert detail.startswith("《加班夜》里开口的人有 7 个") and f"上限 {S.SPEAKERS_MAX} 人" in detail
    assert "角色表最多" not in detail
    # 只导其中几镜就能过
    ok = client.post(f"{API}/SC-7/import", json={"module": "drama_shot", "target_id": "EP-1",
                                                 "blocks": ["S1", "S2"]})
    assert ok.status_code == 200 and [s["id"] for s in ok.json()["detail"]["spec"]["speakers"]] == ["c1", "c2"]


def test_import_folds_a_target_role_onto_the_speaker_with_the_same_name(project):
    """工作台里已经有「老周」（id 是 laozhou），从 hosts 为 ``zhou`` / 老周 的 PC-1 导入：

    那一句记到 laozhou 名下。不重映射的话 ``who`` 还写着目标那边的 ``zhou``，``S.normalize`` 会说
    「说话人不在角色表里」，整次导入 400。"""

    S.save(S.normalize({"title": "拿过来调", "target": "podcast", "speakers": [{"id": "laozhou", "name": "老周"}],
                        "blocks": [], "versions": []}, project_root=project, spec_id="SC-8"))
    r = client.post(f"{API}/SC-8/import", json={"module": "podcast", "target_id": "PC-1"})
    assert r.status_code == 200, r.json()
    out = r.json()
    assert out["speakers"] == [] and out["renamed"] == {}          # 撞名字的折进已有的那个，没有新角色、没有换号
    saved = out["detail"]["spec"]
    assert [s["id"] for s in saved["speakers"]] == ["laozhou"]
    assert saved["versions"][0]["blocks"][0]["lines"][0]["who"] == "laozhou"


def test_import_renumbers_a_role_whose_id_is_taken_by_a_different_person(project):
    """工作台里 ``xiao`` 是「别人」，来源播客的 ``xiao`` 是「小夏」：给小夏换个号（xiao_2），返回体与提示里点名。

    以前是静默 continue：小夏的话记在 xiao 名下，界面按角色表的名字显示成「别人」说的，返回体一句提示都没有。"""

    path = project / "content" / "podcast" / "PC-1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["segments"][0]["lines"].append({"who": "xia", "text": "小夏说第二句。"})
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    S.save(S.normalize({"title": "拿过来调", "target": "podcast", "speakers": [{"id": "xia", "name": "别人"}],
                        "blocks": [], "versions": []}, project_root=project, spec_id="SC-10"))
    r = client.post(f"{API}/SC-10/import", json={"module": "podcast", "target_id": "PC-1"})
    assert r.status_code == 200, r.json()
    out = r.json()
    assert out["speakers"] == ["zhou", "xia_2"] and out["renamed"] == {"xia": "xia_2"}
    assert out["message"] == "已从《为什么我们戒不掉短视频》导入 2 句；撞 id 的角色已换号：「小夏：xia → xia_2（xia = 别人）」"
    saved = out["detail"]["spec"]
    assert [(s["id"], s["name"]) for s in saved["speakers"]] == [("xia", "别人"), ("zhou", "老周"), ("xia_2", "小夏")]
    assert [l["who"] for l in saved["versions"][0]["blocks"][0]["lines"]] == ["zhou", "xia_2"]
    # 再导一次：xia_2 = 小夏 已经在了（同名折进去），不会再变出 xia_3
    again = client.post(f"{API}/SC-10/import", json={"module": "podcast", "target_id": "PC-1"})
    assert again.status_code == 200 and again.json()["speakers"] == [] and again.json()["renamed"] == {}
    assert [l["who"] for l in again.json()["detail"]["spec"]["versions"][1]["blocks"][0]["lines"]] == ["zhou", "xia_2"]

    # 名字优先于 id：工作台里已经有「小夏」（id zhou）、且 xia 是别人 → 小夏的话记到 zhou 名下，不是别人名下、也不换号
    S.save(S.normalize({"title": "拿过来调", "target": "podcast",
                        "speakers": [{"id": "xia", "name": "别人"}, {"id": "zhou", "name": "小夏"}],
                        "blocks": [], "versions": []}, project_root=project, spec_id="SC-11"))
    r = client.post(f"{API}/SC-11/import", json={"module": "podcast", "target_id": "PC-1"})
    assert r.status_code == 200, r.json()
    lines = r.json()["detail"]["spec"]["versions"][0]["blocks"][0]["lines"]
    assert [(l["who"], l["text"]) for l in lines] == [("zhou_2", "先说个事。"), ("zhou", "小夏说第二句。")]
    # 换号的只有老周：他的 zhou 被工作台里的小夏占着；小夏本人按名字折进 zhou，不换号
    assert r.json()["speakers"] == ["zhou_2"] and r.json()["renamed"] == {"zhou": "zhou_2"}

    # 原 id 已经顶到 63 字上限：先截短再加后缀，新号自己也得过得了 ID_RE，不然整次导入被归一化打回
    long_id = "z" * 63
    data["hosts"][0]["id"] = long_id
    for line in data["segments"][0]["lines"]:
        if line["who"] == "zhou":
            line["who"] = long_id
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    S.save(S.normalize({"title": "拿过来调", "target": "podcast", "speakers": [{"id": long_id, "name": "别人"}],
                        "blocks": [], "versions": []}, project_root=project, spec_id="SC-12"))
    r = client.post(f"{API}/SC-12/import", json={"module": "podcast", "target_id": "PC-1"})
    assert r.status_code == 200, r.json()
    assert r.json()["renamed"] == {long_id: "z" * 61 + "_2"}


def test_first_version_is_adopted_but_later_ones_are_not(tmp_path, monkeypatch):
    """第一版自动采用（不然「用」页一直说还没挑一版）；第二版起要人自己挑——契约 §3「比过再挑」。"""

    raw = {"id": "SC-A", "versions": [{"id": "V1"}], "current": ""}
    assert S.adopt_if_first(raw)["current"] == "V1"
    raw["versions"].append({"id": "V2"})
    assert S.adopt_if_first(raw)["current"] == "V1"      # 已经有采用的，不顶替
    assert S.adopt_if_first({"id": "SC-A", "versions": [], "current": ""})["current"] == ""


def test_adopt_when_nothing_is_adopted_takes_the_version_just_added_not_the_oldest():
    """``current`` 被整体保存清空、版本表里已经有旧版时再加一版：采用的是刚加的这一版（调用方都是先追加再调），
    不是 ``versions[0]``——界面在这种状态下说「这一版会直接采用」，采用成最老那版就是说一套做一套。"""

    raw = {"id": "SC-A", "versions": [{"id": "V1"}, {"id": "V2"}], "current": ""}
    raw["versions"].append({"id": "V3"})
    assert S.adopt_if_first(raw)["current"] == "V3"
