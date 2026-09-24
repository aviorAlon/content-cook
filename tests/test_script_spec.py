"""剧本工作台的项目文件（docs/scriptlab-spec.md §2）：归一化、角色 / 段 / 版本的校验、版本按骨架对齐、读写归档。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from script import spec as S


def minimal(**extra) -> dict:
    data = {
        "title": "下班后的消息，要不要回",
        "target": "podcast",
        "speakers": [{"id": "jie", "name": "阿杰", "persona": "产品经理", "stance": "边界感是必须的"},
                     {"id": "lin", "name": "小林", "stance": "客户在等就得回"}],
        "blocks": [{"title": "冷开场", "goal": "用一个具体场景把人拽进来"}, {"id": "B02", "title": "话题"}],
        "versions": [{"label": "第一版", "source": "model",
                      "blocks": [{"id": "B01", "lines": [{"who": "jie", "text": "两点十七。", "cue": "停顿"},
                                                         {"who": "小林", "text": "又加班？", "kind": "question"}]}]}],
        "current": "V1",
    }
    data.update(extra)
    return data


def test_normalizes_and_fills_ids(tmp_path):
    spec = S.normalize(minimal(), project_root=tmp_path, spec_id="SC-1")
    assert spec["id"] == "SC-1" and spec["module"] == "script" and spec["target"] == "podcast" and spec["language"] == "zh"
    assert [b["id"] for b in spec["blocks"]] == ["B01", "B02"]
    assert [b["kind"] for b in spec["blocks"]] == ["talk", "talk"]      # 段种类默认对谈，听友问答要显式标
    assert [v["id"] for v in spec["versions"]] == ["V1"] and spec["current"] == "V1"
    lines = spec["versions"][0]["blocks"][0]["lines"]
    assert lines[1]["who"] == "lin"                      # 名字换成 id
    assert lines[0]["cue"] == "停顿" and lines[0]["kind"] == "talk" and lines[1]["kind"] == "question"
    assert spec["output_root"] == str((tmp_path / "output" / "script" / "SC-1").resolve())
    disk = S.to_disk(spec)
    assert "output_root" not in disk and "project_root" not in disk


def test_version_blocks_align_with_skeleton(tmp_path):
    """一版的段永远跟骨架对齐：骨架里有、这一版没写的段补成空台词，顺序照骨架——两版并排比才对得上。"""

    spec = S.normalize(minimal(), project_root=tmp_path, spec_id="SC-2")
    assert [b["id"] for b in spec["versions"][0]["blocks"]] == ["B01", "B02"]
    assert spec["versions"][0]["blocks"][1]["lines"] == []
    # 倒着写也会被摆回骨架顺序
    flipped = minimal(versions=[{"id": "V1", "blocks": [{"id": "B02", "lines": [{"who": "jie", "text": "嗯。"}]},
                                                        {"id": "B01", "lines": []}]}])
    spec = S.normalize(flipped, project_root=tmp_path, spec_id="SC-3")
    assert [b["id"] for b in spec["versions"][0]["blocks"]] == ["B01", "B02"]
    assert spec["versions"][0]["blocks"][1]["lines"][0]["text"] == "嗯。"


@pytest.mark.parametrize("data, message", [
    (minimal(versions=[{"id": "V1", "blocks": [{"id": "B01", "lines": [{"who": "路人", "text": "x"}]}]}]),
     "说话人「路人」不在角色表里"),
    (minimal(speakers=[{"id": "a", "name": "x"}, {"id": "a", "name": "y"}]), "角色 id 重复了"),
    (minimal(speakers=[{"id": "x", "name": "同名"}, {"id": "y", "name": "同名"}], versions=[], current=""),
     "角色名字重复了：「同名」"),
    (minimal(speakers=[{"id": f"s{i}", "name": str(i)} for i in range(7)], versions=[], current=""),
     "角色表最多 6 人，现在有 7 人"),
    (minimal(blocks=[{"id": "B01"}, {"id": "B01"}], versions=[], current=""), "段 id 重复了：B01"),
    (minimal(blocks=[{"id": "B01", "kind": "问答"}], versions=[], current=""),
     "blocks[0].kind 只能是「talk / qa」里的一个，或留空，收到「问答」"),
    (minimal(versions=[{"id": "V1", "blocks": []}, {"id": "V1", "blocks": []}]), "版本 id 重复了：V1"),
    (minimal(versions=[{"id": "V1", "blocks": [{"id": "B09", "lines": []}]}]), "版本 V1 里的段「B09」不在段落骨架里"),
    (minimal(versions=[{"id": "V1", "blocks": [{"id": "B01", "lines": []}, {"id": "B01", "lines": []}]}]),
     "版本 V1 里的段 id 重复了：B01"),
    (minimal(current="V9"), "current「V9」不是这个项目的版本 id"),
    (minimal(versions=[{"id": "V1", "from_version": "V8", "blocks": []}]), "版本 V1 说是基于「V8」改的，但没有这一版"),
    (minimal(versions=[{"id": "V1", "blocks": [{"id": "B01", "lines": [{"who": "jie", "text": "x", "cue": "跳舞"}]}]}]),
     "cue 只能是「笑 / 轻笑"),
    (minimal(versions=[{"id": "V1", "blocks": [{"id": "B01", "lines": [{"who": "jie", "text": "x", "kind": "唱"}]}]}]),
     "kind 只能是 talk / question / answer，收到「唱」"),
    (minimal(target="漫剧"), "target 只能是 podcast / drama_screenplay / drama_shot / generic，收到「漫剧」"),
    (minimal(versions=[{"id": "V1", "source": "别的", "blocks": []}]), "source 只能是 model / hand / rewrite / import"),
    (minimal(used_in=[{"module": "sticker", "id": "PC-1"}]), "used_in[0].module 只能是 podcast / drama_screenplay / drama_shot"),
    (minimal(title=""), "title 不能为空"),
    (minimal(module="podcast"), "项目文件的 module 是「podcast」，跟这个模块（script）对不上"),
    (minimal(speakers=[{"name": ""}]), "speakers[0].name 不能为空"),
    (minimal(blocks="一段"), "blocks 要是数组（段落骨架）"),
])
def test_rejects_bad_shapes(tmp_path, data, message):
    with pytest.raises(S.ScriptSpecError) as exc:
        S.normalize(data, project_root=tmp_path, spec_id="SC-X")
    assert message in str(exc.value)


def test_duplicate_speaker_names_are_rejected_before_they_merge(tmp_path):
    """同名的两个人：台词按名字写时会静默归到后一个人名下——归一化直接拒绝，不让它发生。"""

    data = minimal(speakers=[{"id": "x", "name": "同名"}, {"id": "y", "name": "同名"}],
                   versions=[{"id": "V1", "blocks": [{"id": "B01", "lines": [{"who": "同名", "text": "谁说的？"}]}]}])
    with pytest.raises(S.ScriptSpecError, match="角色名字重复了：「同名」"):
        S.normalize(data, project_root=tmp_path, spec_id="SC-N")


def test_empty_project_normalizes(tmp_path):
    """刚新建的项目：没角色、没段、没版本也要能存下来（界面上先建再填）。"""

    spec = S.normalize({"title": "空的"}, project_root=tmp_path, spec_id="SC-4")
    assert spec["speakers"] == [] and spec["blocks"] == [] and spec["versions"] == [] and spec["current"] == ""
    assert spec["target"] == "generic" and S.all_lines(spec) == [] and S.current_version(spec) is None
    # 角色表的下限不在归一化拦（空项目要能存），要角色的那几步自己调 require_speakers
    with pytest.raises(S.ScriptSpecError, match="角色表是空的"):
        S.require_speakers(spec)
    assert [s["id"] for s in S.require_speakers(S.normalize(minimal(), project_root=tmp_path, spec_id="SC-4b"))] == ["jie", "lin"]


def test_pick_version_falls_back_to_the_latest_when_nothing_is_picked(tmp_path):
    """生成后 current 不动（契约 §3），所以刚生成完第一版时 current 是空的：默认看最新一版，别当成「没有稿子」。"""

    versions = [{"id": "V1", "blocks": [{"id": "B01", "lines": [{"who": "jie", "text": "第一版。"}]}]},
                {"id": "V2", "blocks": [{"id": "B01", "lines": [{"who": "jie", "text": "第二版。"}]}]}]
    spec = S.normalize(minimal(versions=versions, current=""), project_root=tmp_path, spec_id="SC-P")
    assert S.current_version(spec) is None                 # 「到底采用了没有」的答案不变
    assert S.pick_version(spec)["id"] == "V2"              # 但默认看的是最新一版
    assert [line["text"] for _b, _i, line in S.all_lines(spec)] == ["第二版。"]
    assert S.pick_version(spec, "V1")["id"] == "V1"
    picked = S.normalize(minimal(versions=versions, current="V1"), project_root=tmp_path, spec_id="SC-P2")
    assert S.pick_version(picked)["id"] == "V1"            # 采用了就听采用的
    assert S.pick_version(S.normalize({"title": "空的"}, project_root=tmp_path, spec_id="SC-P3")) is None


def test_next_id_and_next_version_id():
    assert S.next_id([]).startswith("SC-")
    assert S.next_version_id([]) == "V1"
    assert S.next_version_id(["V1", "V2"]) == "V3"
    assert S.next_version_id(["V1", "hand-2", "V7"]) == "V8"
    assert S.default_block_id(3) == "B03" and S.default_version_id(3) == "V3"


def test_readonly_helpers(tmp_path):
    spec = S.normalize(minimal(), project_root=tmp_path, spec_id="SC-5")
    assert list(S.speaker_by_id(spec)) == ["jie", "lin"] and S.speaker_names(spec)["lin"] == "小林"
    assert list(S.version_by_id(spec)) == ["V1"] and S.current_version(spec)["id"] == "V1"
    assert S.pick_version(spec, "V1")["id"] == "V1"
    rows = S.all_lines(spec)
    assert [(block["id"], index, line["who"]) for block, index, line in rows] == [("B01", 1, "jie"), ("B01", 2, "lin")]
    assert rows[0][0]["title"] == "冷开场"                # 段是骨架里那一条，带标题
    with pytest.raises(S.ScriptSpecError, match="没有这一版：V9——版本列表里只有 V1"):
        S.pick_version(spec, "V9")


def test_pick_version_says_there_are_no_versions_at_all(tmp_path):
    """一版也没有时不能拼出「版本列表里只有 空」：那个「空」是正文里的中文，英文界面翻不掉。
    带显式 version 的入口（审稿 / 采用 / 删版本 / 重写的 from_version）打在空项目上都会走到这里。"""

    spec = S.normalize({"title": "空的"}, project_root=tmp_path, spec_id="SC-P4")
    with pytest.raises(S.ScriptSpecError, match="没有这一版：V9——这个剧本还没有任何版本"):
        S.pick_version(spec, "V9")


def test_save_load_archive(tmp_path):
    spec = S.normalize(minimal(), project_root=tmp_path, spec_id="SC-6")
    path = S.save(spec)
    assert path == tmp_path / "content" / "script" / "SC-6.json"
    loaded = S.load("SC-6", project_root=tmp_path)
    assert loaded["title"] == spec["title"] and len(S.all_lines(loaded)) == 2
    assert S.existing_ids(tmp_path) == ["SC-6"]
    (tmp_path / "output" / "script" / "SC-6" / "manifests").mkdir(parents=True)
    result = S.archive("SC-6", project_root=tmp_path)
    assert result["output_moved"] and not path.exists()
    assert (Path(result["archived_to"]) / "spec.json").is_file()
    with pytest.raises(S.ScriptSpecError, match="没有这个剧本"):
        S.load("SC-6", project_root=tmp_path)


def test_load_rejects_bad_json_and_mismatched_id(tmp_path):
    folder = tmp_path / "content" / "script"
    folder.mkdir(parents=True)
    (folder / "SC-7.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(S.ScriptSpecError, match="不是合法的 JSON"):
        S.load("SC-7", project_root=tmp_path)
    (folder / "SC-8.json").write_text(json.dumps({**minimal(), "id": "OTHER"}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(S.ScriptSpecError, match="对不上"):
        S.load("SC-8", project_root=tmp_path)


def test_targets_payload_lists_four_targets():
    rows = S.targets_payload()
    assert [r["id"] for r in rows] == ["podcast", "drama_screenplay", "drama_shot", "generic"]
    assert all(r["name"] and r["english"] and r["note"] for r in rows)
