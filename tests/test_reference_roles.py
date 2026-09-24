"""参考槽位用途（DESIGN §30，融合 drama-skills「参考图四态」的判据）：``shot.reference_roles = [{target, use, control, ignore}]``。
归一化拒绝指向本镜没有的参考图；编译按 ``frame_reference_asset_ids`` 的顺序编号（依赖槽 → 角色定妆 → 道具，共用锚图只占一槽）；
没写的镜逐字节不变；lint 五类；编辑接口校验与回读；英文规则表覆盖。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import lint
from storyvideo.prompts import (
    compile_frame_prompt,
    frame_reference_asset_ids,
    frame_request_data,
    reference_roles_by_slot,
    reference_roles_clause,
    reference_slot_plan,
)
from storyvideo.spec import SpecError, load_spec
from webui import deps, i18n
from webui.app import app
from webui.problems import problem_card
from webui.routers import episodes as episodes_router


def _raw(language: str = "zh") -> dict:
    return {
        "id": "RR-T", "output_root": "output/story_video/RR-T", "prompt_language": language,
        "frame_style": "风格。", "scene_anchor": "场景。", "camera": "机位。", "neg": "n",
        "subtitle": {"language": "zh"},
        "cast": {
            "lin": {"name": "林小满", "block": "林小满：黑发。", "asset": "content/assets/lin.png",
                    "variants": {"rain": {"label": "淋雨", "delta": "头发湿了。", "asset": "content/assets/lin_rain.png"},
                                 "coat": {"label": "外套", "delta": "披了外套。"}}},
            "chen": {"name": "陈默", "block": "陈默：短发。", "asset": "content/assets/chen.png"},
            "office": {"block": "办公区。"},
        },
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["office", "lin"], "shot": "中景", "art": "a", "motion": "m", "lines": []},
            {"id": "S2", "dur": 4, "cast": ["lin", "chen", "office"], "shot": "近景", "art": "b", "motion": "m2", "lines": [], "ref_frame": "S1"},
            {"id": "S3", "dur": 4, "cast": ["lin@rain", "chen"], "shot": "特写", "art": "c", "motion": "m3", "lines": []},
        ],
    }


def _spec(tmp_path: Path, raw: dict | None = None):
    raw = raw or _raw()
    content = tmp_path / "content" / "story_video"
    content.mkdir(parents=True, exist_ok=True)
    (content / "RR-T.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return load_spec("RR-T", project_root=tmp_path)


ROLES = [
    {"target": "cast:lin", "control": ["脸", "发型"], "ignore": ["服装"]},
    {"target": "prev_frame", "use": "只取场景与光线", "ignore": ["姿势", "机位"]},
]


# ---------------------------------------------------------------- 归一化


def test_normalize_accepts_and_rejects(tmp_path):
    raw = _raw()
    raw["shots"][1]["reference_roles"] = ROLES
    spec = _spec(tmp_path, raw)
    assert next(s for s in spec["shots"] if s["id"] == "S2")["reference_roles"] == ROLES   # 原样保留
    for bad, needle in (
        ([{"target": "prev_frame"}], "shots[0]"),                       # S1 没有 ref_frame（放到 S1 上试）
        ([{"target": "cast:chen"}], "not in this shot"),               # S1 出场里没有陈默
        ([{"target": "cast:office"}], "has no anchor image"),          # 场景块没锚图，不占槽
        ([{"target": "prop"}], "needs prop_ref"),
        ([{"target": "tail"}], "needs ref_tail"),
        ([{"target": "nope"}], "must be prev_frame, tail, prop, or cast"),
        ([{"use": "x"}], "target must be a non-empty string"),
        ([{"target": "cast:lin", "control": "脸"}], "control must be a list of non-empty strings"),
        ([{"target": "cast:lin", "use": 3}], "use must be a string"),
        ("cast:lin", "reference_roles must be a list"),
        (["cast:lin"], "reference_roles[0] must be an object"),
    ):
        raw = _raw()
        raw["shots"][0]["reference_roles"] = bad
        with pytest.raises(SpecError, match=re.escape(needle) if needle != "shots[0]" else "prev_frame needs ref_frame or ref_tail"):
            _spec(tmp_path, raw)
    # 变体引用：cast 里写的是 lin@rain，cast:lin 与 cast:lin@rain 都能落到它的槽；cast:lin@coat 不在本镜
    raw = _raw()
    raw["shots"][2]["reference_roles"] = [{"target": "cast:lin@rain", "use": "湿发"}, {"target": "cast:lin", "use": "脸"}]
    _spec(tmp_path, raw)
    raw["shots"][2]["reference_roles"] = [{"target": "cast:lin@coat", "use": "x"}]
    with pytest.raises(SpecError, match="not in this shot"):
        _spec(tmp_path, raw)


# ---------------------------------------------------------------- 槽位与编译


def test_slot_plan_matches_request_order_and_dedup(tmp_path):
    spec = _spec(tmp_path)
    plan = reference_slot_plan(spec, "S2")
    assert [(p["index"], p["target"]) for p in plan] == [(0, "prev_frame"), (1, "cast:lin"), (2, "cast:chen")]
    refs = frame_reference_asset_ids(spec, "S2", dependency_asset_id="dep")
    assert len(refs) == 3 and refs[0] == "dep"
    # 两个出场共用一张锚图 → 请求里只占一槽，计划里也是同一个 index
    raw = _raw()
    raw["cast"]["chen"]["asset"] = raw["cast"]["lin"]["asset"]
    spec = _spec(tmp_path, raw)
    plan = reference_slot_plan(spec, "S2")
    assert [(p["index"], p["target"]) for p in plan] == [(0, "prev_frame"), (1, "cast:lin"), (1, "cast:chen")]
    assert len(frame_reference_asset_ids(spec, "S2", dependency_asset_id="dep")) == 2
    # 变体自己的锚图优先；标签带变体名
    plan = reference_slot_plan(spec, "S3")
    assert plan[0]["target"] == "cast:lin@rain" and plan[0]["label_zh"] == "林小满定妆·淋雨" and plan[0]["index"] == 0


def test_clause_zh_en_numbering_and_byte_identical_without_roles(tmp_path):
    base = compile_frame_prompt(_spec(tmp_path), "S2")
    raw = _raw()
    raw["shots"][1]["reference_roles"] = ROLES
    spec = _spec(tmp_path, raw)
    placed = reference_roles_by_slot(spec, "S2")
    assert [(i, label) for i, label, _ in placed] == [(0, "上一镜 S1 首帧"), (1, "林小满定妆")]
    clause = reference_roles_clause(spec, "S2")
    assert clause == "参考图1（上一镜 S1 首帧）：只取场景与光线；不得控制姿势、机位。参考图2（林小满定妆）：脸、发型逐字对照；不得控制服装。"
    prompt = compile_frame_prompt(spec, "S2")
    assert prompt == base + clause and prompt.endswith(clause)
    assert compile_frame_prompt(spec, "S1") == compile_frame_prompt(_spec(tmp_path), "S1")   # 没写的镜不变
    assert frame_request_data(spec, "S1") == frame_request_data(_spec(tmp_path), "S1")
    raw["prompt_language"] = "en"
    spec = _spec(tmp_path, raw)
    en = reference_roles_clause(spec, "S2")
    assert en == ("Reference 1 (first frame of S1): 只取场景与光线; must not control pose, camera position. "
                  "Reference 2 (林小满 anchor): follow face, hairstyle exactly; must not control wardrobe.")
    assert compile_frame_prompt(spec, "S2").endswith(en + " ")


def test_role_without_slot_is_skipped_by_compiler():
    # 编译器不校验（spec 校验才拒）：找不到槽的 role 不编号，编号仍然诚实
    spec = {"prompt_language": "zh", "cast": {"lin": {"name": "林小满", "block": "x", "asset": "a.png"}},
            "shots": [{"id": "S1", "cast": ["lin"], "shot": "中景", "art": "a", "motion": "m",
                       "reference_roles": [{"target": "prop", "use": "x"}, {"target": "cast:lin", "use": "脸"}]}]}
    assert reference_roles_clause(spec, "S1") == "参考图1（林小满定妆）：脸。"


# ---------------------------------------------------------------- lint


def _lint_spec(**shot_extra):
    return {"cast": {"lin": {"name": "林小满", "block": "x", "asset": "a.png"}, "office": {"block": "o"}},
            "shots": [{"id": "S1", "dur": 4, "cast": ["lin"], "shot": "中景", "art": "a", "motion": "m"},
                      {"id": "S2", "dur": 4, "cast": ["lin", "office"], "shot": "近景", "art": "b", "motion": "m", **shot_extra}]}


def test_lint_cases():
    assert lint.check_reference_roles(_lint_spec()) == []
    msgs = lint.check_reference_roles(_lint_spec(reference_roles=[{"target": "cast:lin", "control": ["脸", "服装"], "ignore": ["服装"]}]))
    assert msgs == ["shot S2: 参考图 cast:lin 的「服装」既在控制里又在不得控制里——留一边，模型没法同时听两句"]
    msgs = lint.check_reference_roles(_lint_spec(reference_roles=[{"target": "prev_frame", "use": "x"}, {"target": "cast:lin", "use": "y"}]))
    assert len(msgs) == 2 and "没有 ref_frame / ref_tail" in msgs[0] and "本镜实际只带 1 张参考图" in msgs[1]
    msgs = lint.check_reference_roles(_lint_spec(reference_roles=[{"target": "tail", "use": "x"}], ref_frame="S1"))
    assert any("没有作参考图的尾帧续接" in m for m in msgs)
    msgs = lint.check_reference_roles(_lint_spec(reference_roles=[{"target": "cast:lin", "use": "x"}, {"target": "cast:lin", "use": "y"}]))
    assert any("写了两条用途" in m for m in msgs)
    msgs = lint.check_reference_roles(_lint_spec(reference_roles="cast:lin"))
    assert msgs and "必须是列表" in msgs[0]
    msgs = lint.check_reference_roles(_lint_spec(reference_roles=[{"use": "x"}]))
    assert msgs and "reference_roles[0] 写法不对" in msgs[0]
    # 能力表给了上限才报上限
    spec = _lint_spec(reference_roles=[{"target": "cast:lin", "use": "x"}], ref_frame="S1")
    spec["image_params"] = {"model": "some-model"}
    assert not any("最多收" in m for m in lint.check_reference_roles(spec))
    import storyvideo.lint as L
    original = L._max_reference_images
    L._max_reference_images = lambda _spec, _adapter="": 1
    try:
        assert any("最多收 1 张" in m for m in lint.check_reference_roles(spec))
    finally:
        L._max_reference_images = original
    assert "check_reference_roles" in lint.__all__
    assert any("cast:lin" in m for m in lint.check_all(_lint_spec(reference_roles=[{"target": "cast:lin", "control": ["脸"], "ignore": ["脸"]}])))


def test_lint_reference_cap_from_static_capability_table():
    """静态能力表按模型前缀记了 max_reference_images（DESIGN §30）：spec 写了具体出图模型 id、且调用方带了
    「模型设置」里的生图来源（ADP-09：上限按 (角色, 来源, 模型) 查，不再跨来源猜）就会响，点名模型和上限。"""
    from genkit.capabilities import max_reference_images

    assert max_reference_images("doubao-seededit-3-0-i2i-250628") == 1
    assert max_reference_images("doubao-seedream-4-0-250828") == 10
    assert max_reference_images("gpt-image-1") == 16 and max_reference_images("openai/gpt-image-1") == 16
    assert max_reference_images("doubao-seedream-3-0-t2i-250415") is None      # 纯文生图，没有上限一说
    assert max_reference_images("@image") is None and max_reference_images("") is None and max_reference_images("mystery") is None
    assert max_reference_images("gpt-image-1", adapter="openai_images") == 16
    # 参考镜首帧 + 林小满定妆 = 2 张，SeedEdit 只收 1 张
    spec = _lint_spec(reference_roles=[{"target": "cast:lin", "use": "只取脸"}], ref_frame="S1")
    spec["image_params"] = {"model": "doubao-seededit-3-0-i2i-250628"}
    msgs = lint.check_reference_roles(spec, image_adapter="ark_images")
    assert msgs == ["shot S2: 本镜要带 2 张参考图，出图模型 doubao-seededit-3-0-i2i-250628 最多收 1 张——超出的会被丢掉，槽位编号也就不准；减少出场锚图或去掉道具参考"]
    # 没带来源（CLI / 没配生图）：不猜上限
    assert lint.check_reference_roles(spec) == []
    i18n._compiled = None
    assert i18n.tr(msgs[0], "en") == ("shot S2: this shot carries 2 reference images, but the image model doubao-seededit-3-0-i2i-250628 accepts at most 1"
                                      " — the extras are dropped and the slot numbering is no longer truthful; use fewer anchor images or drop the prop reference")
    # 上限够用（Seedream 4.0 收 10 张）/ 指向设置的 @image：不响
    spec["image_params"] = {"model": "doubao-seedream-4-0-250828"}
    assert lint.check_reference_roles(spec) == []
    spec["image_params"] = {"model": "@image"}
    assert lint.check_reference_roles(spec) == []


def test_problem_card_kind_and_english_rules():
    i18n._compiled = None
    samples = [
        "shot S2: 参考图 cast:lin 的「服装」既在控制里又在不得控制里——留一边，模型没法同时听两句",
        "shot S2: 参考图用途写了 prev_frame，但本镜没有 ref_frame / ref_tail——没有上一镜的参考槽，这条说明会指向不存在的图；删掉它或给本镜选参考镜",
        "shot S2: 参考图用途写了 tail，但本镜没有作参考图的尾帧续接——删掉它，或把尾帧续接设成「作参考图」",
        "shot S2: 参考图 cast:lin 写了两条用途——合成一条，后一条会盖住前一条的意思",
        "shot S2: 写了 2 条参考图用途，本镜实际只带 1 张参考图——多出的说明指向不存在的图；按依赖槽 → 角色定妆 → 道具的顺序核对",
        "shot S2: 本镜要带 3 张参考图，出图模型 doubao-seededit-3-0-i2i 最多收 2 张——超出的会被丢掉，槽位编号也就不准；减少出场锚图或去掉道具参考",
        "shot S2: reference_roles[0] 写法不对——target 填参考图（prev_frame / tail / prop / cast:<id>），use 填取它什么，control / ignore 填控制与不得控制的方面",
        "reference_roles[0] 要是对象（target / use / control / ignore）",
        "reference_roles[0]：target（哪张参考图）不能为空",
        "reference_roles[1].control 要是方面词列表（脸 / 发型 / 服装 / 姿势 / 机位 / 光线 / 场景 / 色调）",
        "reference_roles[0]：use / control / ignore / fidelity 至少写一项，不然这条说明是空的",
        "reference_roles[2].fidelity 要是 1–100 的百分数（相似度约多少）",
    ]
    han = re.compile(r"[一-鿿]")
    quoted = re.compile(r"「[^」]*」|（[^）]*）|\"[^\"]*\"|\([^)]*\)")
    leftover = {s: i18n.tr(s, "en") for s in samples if han.search(quoted.sub("", i18n.tr(s, "en")))}
    assert not leftover, leftover
    assert problem_card(samples[0])["kind"] == "reference"
    assert problem_card(samples[1])["target"] == "S2"


# ---------------------------------------------------------------- 编辑接口

client = TestClient(app)


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    raw = _raw()
    raw["id"] = "RR-API"
    raw["output_root"] = "output/story_video/RR-API"
    raw["camera_profile"] = "dark_comedy_vertical"
    path = content_dir / "RR-API.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def test_api_roundtrip_and_validation(project):
    url = "/api/episodes/storyvideo/RR-API/shots/S2"
    resp = client.put(url, json={"reference_roles": [
        {"target": " cast:lin ", "use": "", "control": ["脸", " 脸", "发型"], "ignore": ["服装"]},
        {"target": "prev_frame", "use": " 只取场景与光线 ", "control": [], "ignore": ["姿势"]},
    ]})
    assert resp.status_code == 200, resp.text
    shot = resp.json()["shot"]
    assert shot["reference_roles"] == [{"target": "cast:lin", "control": ["脸", "发型"], "ignore": ["服装"]},
                                       {"target": "prev_frame", "use": "只取场景与光线", "ignore": ["姿势"]}]
    assert [(s["index"], s["target"]) for s in shot["reference_slots"]] == [(0, "prev_frame"), (1, "cast:lin"), (2, "cast:chen")]
    assert "参考图1（上一镜 S1 首帧）：只取场景与光线；不得控制姿势。参考图2（林小满定妆）：脸、发型逐字对照；不得控制服装。" in shot["frame_prompt"]
    saved = json.loads(project.read_text(encoding="utf-8"))
    assert saved["shots"][1]["reference_roles"][1] == {"target": "prev_frame", "use": "只取场景与光线", "ignore": ["姿势"]}
    detail = client.get("/api/episodes/storyvideo/RR-API").json()
    assert next(s for s in detail["shots"] if s["id"] == "S2")["reference_roles"][0]["target"] == "cast:lin"
    # 没动别的字段时保留；[] 清掉
    assert client.put(url, json={"sfx": "铃铛"}).status_code == 200
    assert "reference_roles" in json.loads(project.read_text(encoding="utf-8"))["shots"][1]
    assert client.put(url, json={"reference_roles": []}).status_code == 200
    assert "reference_roles" not in json.loads(project.read_text(encoding="utf-8"))["shots"][1]
    for bad, needle in (
        ([{"target": "cast:lin", "nope": 1}], "不认识的字段"),
        ([{"use": "x"}], "target（哪张参考图）不能为空"),
        ([{"target": "cast:lin", "use": 3}], "use 要是一句话"),
        ([{"target": "cast:lin", "control": "脸"}], "control 要是方面词列表"),
        ([{"target": "cast:lin"}], "至少写一项"),
        ([{"target": "cast:chen", "use": "x"}], "not in this shot"),        # S2 有 chen —— 换到 S1 试
    ):
        target_url = url if "cast:chen" not in json.dumps(bad) else "/api/episodes/storyvideo/RR-API/shots/S1"
        r = client.put(target_url, json={"reference_roles": bad})
        assert r.status_code == 400, (bad, r.text)
        assert needle in r.json()["detail"], (bad, r.json()["detail"])
    assert "reference_roles" not in json.loads(project.read_text(encoding="utf-8"))["shots"][0]   # 400 不写盘
