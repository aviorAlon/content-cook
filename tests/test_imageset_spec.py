"""图片集项目文件（DESIGN §40，契约 docs/imageset-spec.md §1）：归一化的收与拒、编号前缀、读写往返、归档。

两个模块（sticker / portrait）共用一份 spec，所以每条校验都按模块参数化跑一遍——``module`` 由路由前缀决定，
文件里写别的模块要拒绝。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from imageset import spec as S
from imageset import templates as T

TODAY = date(2026, 9, 9)


def sticker_data(**extra) -> dict:
    data = {
        "title": "小满表情包",
        "template": "emoji_pack",
        "subject": {"kind": "character", "name": "小满", "block": "齐肩黑发，圆框眼镜", "assets": [], "fidelity": 85},
        "style": "flat_cute",
        "options": {"expressions": ["开心", "哭"], "with_caption": True},
    }
    data.update(extra)
    return data


def portrait_data(**extra) -> dict:
    data = {
        "title": "百天写真",
        "template": "baby_100",
        "subject": {"kind": "photo", "name": "宝宝", "block": "", "assets": [], "fidelity": 90},
        "style": "studio_soft",
        "options": {"scenes": ["小马甲"], "count_per_scene": 1, "text": "百天快乐|2026.09.09", "aspect": "3:4"},
    }
    data.update(extra)
    return data


DATA = {"sticker": sticker_data, "portrait": portrait_data}
IDS = {"sticker": "ST-T-1", "portrait": "PT-T-1"}


def norm(module: str, project_root, **extra) -> dict:
    return S.normalize(DATA[module](**extra), module=module, project_root=project_root, spec_id=IDS[module])


# ---------------------------------------------------------------------------- 归一化：收


@pytest.mark.parametrize("module", ["sticker", "portrait"])
def test_normalize_fills_defaults_and_runtime_keys(module, tmp_path):
    spec = norm(module, tmp_path)
    assert spec["id"] == IDS[module] and spec["module"] == module
    assert spec["prompt_language"] == "zh"
    assert spec["image_params"] == {"model": "@image", "image_size": "2K"}
    assert spec["items"] == [] and spec["picks"] == []
    # 运行时键：写盘时去掉
    assert spec["output_root"] == str((tmp_path / "output" / module / IDS[module]).resolve(strict=False))
    assert spec["project_root"] == str(tmp_path.resolve())
    on_disk = S.to_disk(spec)
    assert "output_root" not in on_disk and "project_root" not in on_disk
    assert S.raw_dir(spec).name == "01_raw" and S.out_dir(spec).name == "05_out" and S.export_dir(spec).name == "09_export"
    # 模板的 options 默认值补齐（没写的键按 options_schema 的 default）
    template = T.get_template(module, spec["template"])
    for field in template.options_schema:
        assert field["key"] in spec["options"]


@pytest.mark.parametrize("module", ["sticker", "portrait"])
def test_normalize_accepts_custom_style_and_rejects_empty_anchor(module, tmp_path):
    spec = norm(module, tmp_path, style="custom", style_anchor="我自己写的风格锚：铜版画质感")
    assert spec["style"] == "custom" and spec["style_anchor"].startswith("我自己写的风格锚")
    with pytest.raises(S.SetSpecError, match="style_anchor"):
        norm(module, tmp_path, style="custom")
    with pytest.raises(S.SetSpecError, match="style"):
        norm(module, tmp_path, style="没有这个风格")


def test_normalize_subject_defaults_kind_per_module(tmp_path):
    sticker = S.normalize({"title": "无 kind", "template": "emoji_pack", "subject": {"name": "小满"}},
                          module="sticker", project_root=tmp_path, spec_id="ST-T-2")
    assert sticker["subject"]["kind"] == "character" and sticker["subject"]["fidelity"] == S.DEFAULT_FIDELITY
    portrait = S.normalize({"title": "无 kind", "template": "baby_100", "subject": {}},
                           module="portrait", project_root=tmp_path, spec_id="PT-T-2")
    assert portrait["subject"]["kind"] == "photo"


def test_normalize_assets_take_plain_paths_and_who_tags(tmp_path):
    spec = S.normalize({"title": "婚纱", "template": "wedding",
                        "subject": {"kind": "photo", "assets": [
                            "content/assets/PT-T-1/a.png",
                            {"file": "content/assets/PT-T-1/b.png", "who": "bride"},
                            {"file": "content/assets/PT-T-1/c.png", "who": ""},
                        ]}},
                       module="portrait", project_root=tmp_path, spec_id="PT-T-1")
    assert spec["subject"]["assets"] == ["content/assets/PT-T-1/a.png",
                                         {"file": "content/assets/PT-T-1/b.png", "who": "bride"},
                                         "content/assets/PT-T-1/c.png"]
    # 文件都不存在 → asset_entries 的 path 是 None（提示词与参考图会跳过它们）
    assert [e["path"] for e in S.asset_entries(spec)] == [None, None, None]
    real = tmp_path / "content" / "assets" / "PT-T-1"
    real.mkdir(parents=True)
    (real / "a.png").write_bytes(b"x")
    assert S.asset_entries(spec)[0]["path"] == str(real / "a.png")
    assert S.resolve_asset(spec, "content/assets/PT-T-1/nope.png") is None


# ---------------------------------------------------------------------------- 归一化：拒


def test_normalize_rejects_wrong_module(tmp_path):
    with pytest.raises(S.SetSpecError, match="module"):
        S.check_module("nope")
    with pytest.raises(S.SetSpecError, match="对不上"):
        S.normalize(portrait_data(module="portrait"), module="sticker", project_root=tmp_path, spec_id="ST-T-1")
    # 模板属于另一个模块 → 也拒
    with pytest.raises(S.SetSpecError, match="template"):
        S.normalize({"title": "x", "template": "baby_100"}, module="sticker", project_root=tmp_path, spec_id="ST-T-1")


@pytest.mark.parametrize("module", ["sticker", "portrait"])
def test_normalize_rejects_bad_shapes(module, tmp_path):
    with pytest.raises(S.SetSpecError, match="title"):
        norm(module, tmp_path, title="   ")
    with pytest.raises(S.SetSpecError, match="template"):
        S.normalize({k: v for k, v in DATA[module]().items() if k != "template"},
                    module=module, project_root=tmp_path, spec_id=IDS[module])
    with pytest.raises(S.SetSpecError, match="id"):
        S.normalize(DATA[module](), module=module, project_root=tmp_path, spec_id="不合法 id")
    with pytest.raises(S.SetSpecError, match="subject.kind"):
        norm(module, tmp_path, subject={"kind": "cartoon"})
    with pytest.raises(S.SetSpecError, match="fidelity"):
        norm(module, tmp_path, subject={"fidelity": 200})
    with pytest.raises(S.SetSpecError, match="fidelity"):
        norm(module, tmp_path, subject={"fidelity": "很像"})
    with pytest.raises(S.SetSpecError, match="prompt_language"):
        norm(module, tmp_path, prompt_language="fr")


@pytest.mark.parametrize("module", ["sticker", "portrait"])
def test_normalize_asset_limit_and_paths(module, tmp_path):
    six = [f"content/assets/x/{i}.png" for i in range(S.MAX_ASSETS)]
    assert len(norm(module, tmp_path, subject={"assets": six})["subject"]["assets"]) == S.MAX_ASSETS
    with pytest.raises(S.SetSpecError, match=f"最多 {S.MAX_ASSETS} 张"):
        norm(module, tmp_path, subject={"assets": six + ["content/assets/x/6.png"]})
    for bad in ("/etc/passwd", "../outside.png", "C:/x.png", "content\\assets\\x.png"):
        with pytest.raises(S.SetSpecError, match="相对路径"):
            norm(module, tmp_path, subject={"assets": [bad]})
    with pytest.raises(S.SetSpecError, match="subject.assets"):
        norm(module, tmp_path, subject={"assets": "content/assets/x.png"})
    with pytest.raises(S.SetSpecError, match="who"):
        norm(module, tmp_path, subject={"assets": [{"file": "content/assets/x.png", "who": "witness"}]})


@pytest.mark.parametrize("module", ["sticker", "portrait"])
def test_normalize_item_ids_and_picks(module, tmp_path):
    ok = norm(module, tmp_path, items=[{"id": "I01", "label": "开心", "prompt": "p"}, {"id": "I02", "label": "哭", "prompt": "p"}],
              picks=["I02", "I02"])
    assert S.item_ids(ok) == ["I01", "I02"] and ok["picks"] == ["I02"]
    assert ok["items"][0]["status"] == "todo" and ok["items"][0]["aspect_ratio"] == "1:1" and ok["items"][0]["attempts"] == []
    with pytest.raises(S.SetSpecError, match="I01 这样的形状"):
        norm(module, tmp_path, items=[{"id": "X1", "label": "开心"}])
    with pytest.raises(S.SetSpecError, match="重复"):
        norm(module, tmp_path, items=[{"id": "I01"}, {"id": "I01"}])
    with pytest.raises(S.SetSpecError, match="不是这个项目的图片 id"):
        norm(module, tmp_path, items=[{"id": "I01"}], picks=["I09"])
    with pytest.raises(S.SetSpecError, match="items"):
        norm(module, tmp_path, items={"I01": {}})
    with pytest.raises(S.SetSpecError, match="picks"):
        norm(module, tmp_path, items=[{"id": "I01"}], picks="I01")
    with pytest.raises(S.SetSpecError, match="status"):
        norm(module, tmp_path, items=[{"id": "I01", "status": "半成品"}])


@pytest.mark.parametrize("module", ["sticker", "portrait"])
def test_normalize_options_follow_template_schema(module, tmp_path):
    key = "expressions" if module == "sticker" else "scenes"
    with pytest.raises(S.SetSpecError, match=f"options.{key} 要是数组"):
        norm(module, tmp_path, options={key: "开心"})
    with pytest.raises(S.SetSpecError, match=f"options.{key} 至少要有一项"):
        norm(module, tmp_path, options={key: ["  "]})
    # 不认识的键原样留着（前端可以先写、后端不删）
    spec = norm(module, tmp_path, options={"我的备注": "留着"})
    assert spec["options"]["我的备注"] == "留着"


def test_normalize_options_int_and_choice(tmp_path):
    spec = S.normalize(portrait_data(options={"scenes": ["小马甲"], "count_per_scene": 3, "aspect": "1:1"}),
                       module="portrait", project_root=tmp_path, spec_id="PT-T-1")
    assert spec["options"]["count_per_scene"] == 3 and spec["options"]["aspect"] == "1:1"
    with pytest.raises(S.SetSpecError, match="count_per_scene"):
        S.normalize(portrait_data(options={"scenes": ["小马甲"], "count_per_scene": 9}),
                    module="portrait", project_root=tmp_path, spec_id="PT-T-1")
    with pytest.raises(S.SetSpecError, match="aspect"):
        S.normalize(portrait_data(options={"scenes": ["小马甲"], "aspect": "16:9"}),
                    module="portrait", project_root=tmp_path, spec_id="PT-T-1")


# ---------------------------------------------------------------------------- 编号


def test_next_id_prefixes_per_module():
    assert S.next_id("sticker", [], today=TODAY) == "ST-20260909-1"
    assert S.next_id("portrait", [], today=TODAY) == "PT-20260909-1"
    assert S.next_id("sticker", ["ST-20260909-1", "ST-20260909-2"], today=TODAY) == "ST-20260909-3"
    # 别的模块的编号不占位
    assert S.next_id("portrait", ["ST-20260909-1"], today=TODAY) == "PT-20260909-1"
    assert S.next_id("image", [], today=TODAY) == "IM-20260909-1"
    assert S.ID_PREFIX == {"sticker": "ST", "portrait": "PT", "image": "IM"}
    with pytest.raises(S.SetSpecError):
        S.next_id("nope", [], today=TODAY)


@pytest.mark.parametrize("module", ["sticker", "portrait"])
def test_existing_ids_reads_content_dir(module, tmp_path):
    assert S.existing_ids(tmp_path, module) == []
    root = S.content_dir(tmp_path, module)
    root.mkdir(parents=True)
    (root / "A-2.json").write_text("{}", encoding="utf-8")
    (root / "A-1.json").write_text("{}", encoding="utf-8")
    (root / "notes.txt").write_text("x", encoding="utf-8")
    assert S.existing_ids(tmp_path, module) == ["A-1", "A-2"]


# ---------------------------------------------------------------------------- 读写往返


@pytest.mark.parametrize("module", ["sticker", "portrait"])
def test_save_load_round_trip(module, tmp_path):
    spec = norm(module, tmp_path, items=[{"id": "I01", "label": "开心", "prompt": "p", "current": "I01-1.png",
                                          "request_hash": "abc", "status": "done",
                                          "attempts": [{"file": "I01-1.png", "model": "@image", "request_hash": "abc"}]}])
    path = S.save(spec)
    assert path == S.spec_path(tmp_path, module, IDS[module]) and path.is_file()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["module"] == module and "output_root" not in on_disk
    again = S.load(module, IDS[module], project_root=tmp_path)
    assert again == spec
    assert S.item_by_id(again, "I01")["label"] == "开心"
    with pytest.raises(S.SetSpecError, match="没有这张图"):
        S.item_by_id(again, "I99")
    # current 指向的文件不在 → picked_items 空
    assert S.picked_items(again) == [] and S.current_path(again, again["items"][0]) is None
    raw = S.raw_dir(again)
    raw.mkdir(parents=True)
    (raw / "I01-1.png").write_bytes(b"x")
    assert [i["id"] for i in S.picked_items(again)] == ["I01"]


@pytest.mark.parametrize("module", ["sticker", "portrait"])
def test_load_rejects_missing_bad_json_and_id_mismatch(module, tmp_path):
    with pytest.raises(S.SetSpecError, match="没有这个项目"):
        S.load(module, "NOPE-1", project_root=tmp_path)
    with pytest.raises(S.SetSpecError, match="没有这个项目"):
        S.load(module, "../etc/passwd", project_root=tmp_path)
    path = S.spec_path(tmp_path, module, IDS[module])
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(S.SetSpecError, match="不是合法的 JSON"):
        S.load(module, IDS[module], project_root=tmp_path)
    path.write_text(json.dumps({**DATA[module](), "id": "OTHER-1"}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(S.SetSpecError, match="对不上"):
        S.load(module, IDS[module], project_root=tmp_path)


# ---------------------------------------------------------------------------- 归档


@pytest.mark.parametrize("module", ["sticker", "portrait"])
def test_archive_moves_spec_and_output(module, tmp_path):
    spec = norm(module, tmp_path)
    S.save(spec)
    raw = S.raw_dir(spec)
    raw.mkdir(parents=True)
    (raw / "I01-1.png").write_bytes(b"png")
    result = S.archive(module, IDS[module], project_root=tmp_path)
    archived = tmp_path / ".webui_deleted" / module
    moved = list(archived.iterdir())
    assert len(moved) == 1 and moved[0].name.startswith(f"{IDS[module]}__")
    assert result["archived_to"] == str(moved[0]) and result["output_moved"] is True
    assert (moved[0] / "spec.json").is_file() and (moved[0] / "output" / "01_raw" / "I01-1.png").is_file()
    assert not S.spec_path(tmp_path, module, IDS[module]).exists()
    assert not (tmp_path / "output" / module / IDS[module]).exists()
    with pytest.raises(S.SetSpecError, match="没有这个项目"):
        S.archive(module, IDS[module], project_root=tmp_path)


@pytest.mark.parametrize("module", ["sticker", "portrait"])
def test_archive_without_output_dir(module, tmp_path):
    S.save(norm(module, tmp_path))
    assert S.archive(module, IDS[module], project_root=tmp_path)["output_moved"] is False
