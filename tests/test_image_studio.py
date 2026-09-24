"""图片工作台 `image`（DESIGN §42，契约 docs/imagestudio-spec.md）：图片集引擎的第三个模块。

它**不是新引擎**——列表 / 出图 / 挑选 / 后处理 / 打包 / 从不覆盖 / 任务全部来自 `src/imageset/`，这里只核对
自己那三个模板与引擎里唯一为它开的口子（`item.meta.model` 覆盖模型）：

* `freeform` / `model_compare` / `text_card` 的展开条数、画幅、候选编号、每个模型一条；
* 主体为空时提示词跳过身份块与排他锁（自由出图允许只写画面），填了主体又要写回来；
* item 级模型覆盖真的进了生图请求，**别的模板不受影响**；
* `text_card` 的后处理出 3:4 图卡并且真把标题画上去（海报那段叠字代码）；
* 三个模板各自的包内布局；
* `/api/image/...` 的 CRUD、模板表、示例复制。

出图走假来源（`tests/test_imageset_generate.FakeProvider`），后处理是本机 Pillow：不联网、不花钱。
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import local as local_mod
from genkit import model_catalog
from imageset import export as E
from imageset import generate as G
from imageset import post as P
from imageset import prompts as PR
from imageset import spec as S
from imageset import templates as T
from storyvideo.prompts import NO_TEXT_TAIL_CN
from tests.test_imageset_generate import FakeProvider
from webui import deps, tasks
from webui.app import app
from webui.routers import imageset as R
from webui.routers import local_models as LM

client = TestClient(app)
IMAGE_TEMPLATES = ("freeform", "model_compare", "text_card")
needs_font = pytest.mark.skipif(not P.font_available(), reason="needs a CJK font on this machine")


def build(template_id: str, tmp_path, *, options=None, subject=None, **extra) -> dict:
    data = {"title": "河灯夜", "template": template_id, "subject": subject or {}, "options": options or {}}
    data.update(extra)
    return S.normalize(data, module="image", project_root=tmp_path, spec_id="IM-T-1")


def base_png(path: Path, *, size=(1600, 2000)) -> Path:
    """上半干净、下半有细节的「底图」：图卡的自动布局要能挑出干净的那一端。"""

    from PIL import Image, ImageDraw

    im = Image.new("RGB", size, (38, 52, 74))
    draw = ImageDraw.Draw(im)
    for x in range(0, size[0], 9):
        draw.line((x, size[1] * 0.55, x, size[1]), fill=(214, 186, 130))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, format="PNG")
    return path


def with_images(spec: dict, tmp_path: Path) -> dict:
    """每张都有 current 文件（后处理 / 打包的入口条件）。"""

    raw = S.raw_dir(spec)
    for item in spec["items"]:
        name = f"{item['id']}-1.png"
        base_png(raw / name)
        item.update({"current": name, "status": "done",
                     "attempts": [{"file": name, "model": "@image", "request_hash": "h", "created_at": 1,
                                   "task_id": None, "error": None}]})
    S.save(spec)
    return spec


# ---------------------------------------------------------------------------- 注册


def test_module_is_the_third_imageset_module():
    assert S.MODULES == ("sticker", "portrait", "image")
    assert S.ID_PREFIX["image"] == "IM"
    assert sorted(T.template_ids("image")) == sorted(IMAGE_TEMPLATES)
    assert all(T.get_template("image", t).module == "image" for t in IMAGE_TEMPLATES)
    assert T.get_template("image", "emoji_pack") is None and T.get_template("sticker", "freeform") is None


@pytest.mark.parametrize("template_id", IMAGE_TEMPLATES)
def test_options_schema_is_renderable(template_id):
    """前端按 schema 渲染小表单：每条都要有 key / label / label_en / type / default；choices 里每项有 id。"""

    template = T.get_template("image", template_id)
    assert template.name and template.english and template.description and template.description_en
    keys = set()
    for field in template.options_schema:
        assert set(("key", "label", "label_en", "type", "default")) <= set(field), field
        assert field["type"] in ("list", "bool", "string", "int")
        assert field["key"] and field["key"] not in keys
        keys.add(field["key"])
        if field["type"] == "list":
            assert isinstance(field["default"], list) and field["default"]
        if field["type"] == "int":
            assert isinstance(field["default"], int) and field["min"] <= field["default"] <= field["max"]
        for choice in field.get("choices") or ():
            assert choice["id"] and choice["name"]
    assert "brief" in keys
    payload = template.payload()
    assert payload["styles"] and all(s["id"] and s["name"] and s["anchor"] and s["anchor_en"] for s in payload["styles"])
    assert payload["export_layout"] and all(payload["export_layout"].values())
    # 每个模板都有「适合」它的风格卡（DESIGN §41 的 fits）
    assert T.style_cards_for(template)


# ---------------------------------------------------------------------------- 展开


def test_freeform_expands_variants_with_one_prompt(tmp_path):
    spec = build("freeform", tmp_path, options={"brief": "夜里的石桥下有人放河灯", "variants": 4, "aspect": "3:4"})
    items = T.expand_items(spec)
    assert [i["id"] for i in items] == ["I01", "I02", "I03", "I04"]
    assert [i["label"] for i in items] == ["候选 1", "候选 2", "候选 3", "候选 4"]
    assert [i["meta"]["variant"] for i in items] == [1, 2, 3, 4]
    assert {i["aspect_ratio"] for i in items} == {"3:4"}
    assert len({i["prompt"] for i in items}) == 1                      # 同一句提示词，差别交给供应商的随机
    assert "夜里的石桥下有人放河灯" in items[0]["prompt"] and items[0]["prompt"].endswith(NO_TEXT_TAIL_CN)
    assert "按 3:4 画幅构图" in items[0]["prompt"]
    assert "文字" in items[0]["negative"] and "水印" in items[0]["negative"]
    # 张数与画幅跟着选项走；画面留空就用标题
    one = T.expand_items(build("freeform", tmp_path, options={"brief": "", "variants": 1, "aspect": "16:9"}))
    assert len(one) == 1 and one[0]["aspect_ratio"] == "16:9" and "河灯夜" in one[0]["prompt"]


def test_freeform_defaults_and_option_ranges(tmp_path):
    spec = build("freeform", tmp_path)
    assert spec["options"] == {"brief": "", "variants": 4, "aspect": "1:1", "negative": ""}
    assert len(T.expand_items(spec)) == 4
    with pytest.raises(S.SetSpecError, match="variants"):
        build("freeform", tmp_path, options={"variants": 9})
    with pytest.raises(S.SetSpecError, match="aspect"):
        build("freeform", tmp_path, options={"aspect": "2:5"})
    extra = T.expand_items(build("freeform", tmp_path, options={"negative": "霓虹,高饱和"}))[0]
    assert extra["negative"].startswith("霓虹,高饱和,")


def test_subjectless_prompt_skips_identity_and_exclusivity(tmp_path):
    """自由出图允许没有主体：不写身份锁、不写排他锁；填了主体两条都要回来（契约 §2）。"""

    plain = T.expand_items(build("freeform", tmp_path, options={"brief": "一只三花猫坐在窗台"}))[0]
    assert PR.CHARACTER_LOCK_ZH not in plain["prompt"]
    assert PR.SOLO_ZH not in plain["prompt"] and PR.EXCLUSIVITY_ZH.split("{")[0] not in plain["prompt"]
    assert not PR.has_subject(build("freeform", tmp_path))
    with_subject = build("freeform", tmp_path, options={"brief": "一只三花猫坐在窗台"},
                         subject={"kind": "character", "name": "小满", "block": "齐肩黑发低马尾"})
    assert PR.has_subject(with_subject)
    item = T.expand_items(with_subject)[0]
    assert "小满：齐肩黑发低马尾" in item["prompt"] and PR.CHARACTER_LOCK_ZH in item["prompt"]
    assert PR.SOLO_ZH in item["prompt"]
    # 真人参考照主体同样成立
    photo = build("freeform", tmp_path, options={"brief": "海边"}, subject={"kind": "photo", "name": "我", "fidelity": 90})
    assert PR.FIDELITY_ZH.format(n=90) in T.expand_items(photo)[0]["prompt"]


def test_model_compare_makes_one_item_per_model(tmp_path):
    spec = build("model_compare", tmp_path,
                 options={"brief": "一只三花猫坐在窗台", "models": ["@image", "gpt-image-2", "nano-banana"], "aspect": "4:3"})
    items = T.expand_items(spec)
    assert [i["id"] for i in items] == ["I01", "I02", "I03"]
    assert [i["label"] for i in items] == ["@image", "gpt-image-2", "nano-banana"]
    assert [i["meta"]["model"] for i in items] == ["@image", "gpt-image-2", "nano-banana"]
    assert len({i["prompt"] for i in items}) == 1                       # 同一句提示词，差别只来自模型
    assert {i["aspect_ratio"] for i in items} == {"4:3"}
    # 默认清单直接可用；重复的模型只算一次
    assert T.get_template("image", "model_compare").options_schema[1]["default"] == T.DEFAULT_COMPARE_MODELS
    assert len(T.expand_items(build("model_compare", tmp_path))) == len(T.DEFAULT_COMPARE_MODELS)
    twice = T.expand_items(build("model_compare", tmp_path, options={"models": ["a", "a", "b"]}))
    assert [i["label"] for i in twice] == ["a", "b"]
    with pytest.raises(S.SetSpecError, match="models"):
        build("model_compare", tmp_path, options={"models": []})


def test_text_card_expands_bases_and_reserves_text_space(tmp_path):
    spec = build("text_card", tmp_path, options={"brief": "清晨的山脊", "title": "早安", "subtitle": "今天也慢慢来", "variants": 3})
    items = T.expand_items(spec)
    assert [i["label"] for i in items] == ["底图 1", "底图 2", "底图 3"]
    assert {i["aspect_ratio"] for i in items} == {"3:4"}                  # 图卡默认 3:4
    assert [i["meta"]["title"] for i in items] == ["早安"] * 3
    assert PR.TEXT_SPACE_ZH in items[0]["prompt"]                        # 给叠字留干净的一端
    assert items[0]["prompt"].endswith(NO_TEXT_TAIL_CN)                  # 文字一律不让模型画
    bottom = T.expand_items(build("text_card", tmp_path, options={"brief": "山", "layout": "bottom"}))[0]
    assert PR.TEXT_SPACE_BOTTOM_ZH in bottom["prompt"] and PR.TEXT_SPACE_ZH not in bottom["prompt"]
    assert bottom["meta"]["layout"] == "bottom"
    # 标题留空就用项目标题
    assert T.expand_items(build("text_card", tmp_path, options={"brief": "山"}))[0]["meta"]["title"] == "河灯夜"


@pytest.mark.parametrize("template_id", IMAGE_TEMPLATES)
def test_english_prompts_use_english_sentences(template_id, tmp_path):
    item = T.expand_items(build(template_id, tmp_path, options={"brief": "a cat on a windowsill"}, prompt_language="en"))[0]
    assert "a cat on a windowsill" in item["prompt"]
    assert "text,letters" in item["negative"]
    assert not any("一" <= ch <= "鿿" for ch in item["prompt"])


def test_style_card_and_custom_anchor_reach_the_prompt(tmp_path):
    card = T.expand_items(build("freeform", tmp_path, options={"brief": "河灯"}, style="cinematic_key_art"))[0]
    assert "电影海报主视觉" in card["prompt"]
    custom = T.expand_items(build("freeform", tmp_path, options={"brief": "河灯"},
                                  style="custom", style_anchor="铜版画质感，只有黑白两色"))[0]
    assert custom["prompt"].startswith("铜版画质感，只有黑白两色")
    preset = T.expand_items(build("freeform", tmp_path, options={"brief": "河灯"}, style="illustration"))[0]
    assert preset["prompt"].startswith("手绘插画")


# ---------------------------------------------------------------------------- item 级模型覆盖


def test_item_model_override_reaches_the_provider(tmp_path):
    spec = build("model_compare", tmp_path, options={"brief": "猫", "models": ["gpt-image-2", "nano-banana"]},
                 image_params={"model": "@image", "image_size": "2K"})
    spec["items"] = T.expand_items(spec)
    S.save(spec)
    assert [G.item_model(spec, i) for i in spec["items"]] == ["gpt-image-2", "nano-banana"]
    provider = FakeProvider(tmp_path / "cache")
    result = G.generate_set(spec, provider=provider, sleep=lambda s: None)
    assert result["generated"] == ["I01", "I02"] and not result["failed"]
    assert [r.model for r in provider.requests] == ["gpt-image-2", "nano-banana"]
    assert [a["model"] for i in spec["items"] for a in i["attempts"]] == ["gpt-image-2", "nano-banana"]
    # 覆盖进请求哈希：换了模型就不算「请求没变」
    assert G.request_hash(G.request_data(spec, spec["items"][0])) != G.request_hash(G.request_data(spec, spec["items"][1]))


def test_other_templates_are_untouched_by_the_override(tmp_path):
    """引擎唯一的模块专属分支只在 ``meta.model`` 非空时生效——别的模板照旧用 ``image_params.model``。"""

    for module, template_id, options in (("sticker", "emoji_pack", {"expressions": ["开心"]}),
                                         ("portrait", "baby_100", {"scenes": ["小马甲"]}),
                                         ("image", "freeform", {"brief": "河灯", "variants": 1})):
        spec = S.normalize({"title": "x", "template": template_id, "options": options,
                            "image_params": {"model": "sd-3.5", "image_size": "2K"}},
                           module=module, project_root=tmp_path / module, spec_id=f"{S.ID_PREFIX[module]}-M-1")
        spec["items"] = T.expand_items(spec)
        assert all("model" not in (i["meta"] or {}) for i in spec["items"])
        assert [G.item_model(spec, i) for i in spec["items"]] == ["sd-3.5"] * len(spec["items"])
        provider = FakeProvider(tmp_path / module / "cache")
        G.generate_set(spec, provider=provider, sleep=lambda s: None, write=False)
        assert {r.model for r in provider.requests} == {"sd-3.5"}
    # 手写的 meta.model 空串也不算覆盖
    spec["items"][0]["meta"]["model"] = "  "
    assert G.item_model(spec, spec["items"][0]) == "sd-3.5"


# ---------------------------------------------------------------------------- 后处理 / 打包


@pytest.mark.parametrize("aspect,ratio", [("1:1", 1.0), ("3:4", 0.75), ("16:9", 16 / 3 / 3)])
def test_freeform_post_crops_to_aspect_and_adds_2048(aspect, ratio, tmp_path):
    spec = build("freeform", tmp_path / aspect.replace(":", "x"), options={"brief": "河灯", "variants": 2, "aspect": aspect})
    spec["items"] = T.expand_items(spec)
    with_images(spec, tmp_path)
    run = P.run_post(spec, T.template_for_spec(spec))
    assert not run["failed"] and run["items"] == ["I01", "I02"]
    by_role: dict[str, list[dict]] = {}
    for row in run["outputs"]:
        by_role.setdefault(row["role"], []).append(row)
    assert sorted(by_role) == ["image", "w2048"]
    for row in by_role["image"] + by_role["w2048"]:
        assert row["width"] / row["height"] == pytest.approx(ratio, rel=0.01)
    assert all(max(r["width"], r["height"]) <= P.IMAGE_LONG_SIDE for r in by_role["w2048"])


def test_export_layouts_per_template(tmp_path):
    spec = build("freeform", tmp_path, options={"brief": "河灯", "variants": 2})
    spec["items"] = T.expand_items(spec)
    with_images(spec, tmp_path)
    P.run_post(spec, T.template_for_spec(spec))
    folder = Path(E.build_package(spec)["folder"])
    names = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    assert "images/I01.png" in names and "images/2048/I01.png" in names
    assert "images/I02.png" in names and "manifest.json" in names and "checksums.sha256" in names


def test_model_compare_package_is_named_by_model(tmp_path):
    spec = build("model_compare", tmp_path, options={"brief": "猫", "models": ["@image", "openai/gpt-image-2"]})
    spec["items"] = T.expand_items(spec)
    with_images(spec, tmp_path)
    P.run_post(spec, T.template_for_spec(spec))
    pkg = E.build_package(spec)
    folder = Path(pkg["folder"])
    names = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    assert "compare/openai_gpt_image_2.png" in names and "compare/2048/openai_gpt_image_2.png" in names
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    # 每条产物记着是哪个模型出的
    assert {r["label"] for r in manifest["files"]} == {"@image", "openai/gpt-image-2"}


@needs_font
def test_text_card_post_draws_the_title_on_a_3x4_card(tmp_path):
    spec = build("text_card", tmp_path, options={"brief": "清晨的山脊", "title": "早安", "subtitle": "今天也慢慢来", "variants": 1})
    spec["items"] = T.expand_items(spec)
    with_images(spec, tmp_path)
    run = P.run_post(spec, T.template_for_spec(spec))
    assert not run["failed"] and [o["role"] for o in run["outputs"]] == ["card"]
    card = run["outputs"][0]
    assert (card["width"], card["height"]) == (1080, 1440)               # 短边 1080 的 3:4 图卡
    assert card["width"] / card["height"] == 0.75
    drawn = P.open_rgba(S.out_dir(spec) / card["file"])
    plain = P.crop_aspect(P.open_rgba(S.raw_dir(spec) / spec["items"][0]["current"]), "3:4").resize(drawn.size)
    assert drawn.tobytes() != plain.tobytes()
    # 换个标题，同一张底图出来的图卡不一样（字真的画上去了，不只是压暗层）
    other = P.render_card(S.raw_dir(spec) / spec["items"][0]["current"], S.out_dir(spec) / "other.png",
                          title="晚安", subtitle="今天也慢慢来", aspect="3:4", layout="top")
    same = P.render_card(S.raw_dir(spec) / spec["items"][0]["current"], S.out_dir(spec) / "same.png",
                         title="早安", subtitle="今天也慢慢来", aspect="3:4", layout="top")
    assert P.open_rgba(other).tobytes() != P.open_rgba(same).tobytes()
    assert P.card_canvas("16:9") == (1920, 1080) and P.card_canvas("1:1") == (1080, 1080)
    # 包内是 cards/{item}.png
    pkg = E.build_package(spec)
    with zipfile.ZipFile(pkg["zip"]) as zf:
        assert any(n.endswith("cards/I01.png") for n in zf.namelist())


def test_text_card_without_a_font_is_a_readable_failure(tmp_path, monkeypatch):
    """没有中文字体时一张失败不拖累整批：报人话原因，不抛海报模块的异常。"""

    monkeypatch.setattr("storyvideo.poster._font_candidates", lambda font, style: (None, None))
    spec = build("text_card", tmp_path, options={"brief": "山", "variants": 1})
    spec["items"] = T.expand_items(spec)
    with_images(spec, tmp_path)
    run = P.run_post(spec, T.template_for_spec(spec))
    assert run["outputs"] == [] and run["failed"][0]["item"] == "I01"
    assert "中文字体" in run["failed"][0]["error"]


# ---------------------------------------------------------------------------- 接口


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(R, "PROVIDER_FACTORY", None)
    for module in S.MODULES:
        (tmp_path / "content" / module).mkdir(parents=True)
    tasks._running.clear()
    return tmp_path


def create(**extra) -> dict:
    body = {"title": "河灯夜", "template": "freeform", "options": {"brief": "石桥下的河灯", "variants": 2}}
    body.update(extra)
    r = client.post("/api/image", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def wait_task(task_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = client.get(f"/api/tasks/{task_id}").json()
        if item["status"] in ("succeeded", "failed"):
            return item
        time.sleep(0.02)
    raise AssertionError("task did not finish")


def test_api_image_crud_and_templates(project):
    nid = client.get("/api/image/next-id").json()["id"]
    assert nid.startswith("IM-") and nid.endswith("-1")
    created = create()
    assert created["id"] == nid and created["module"] == "image"
    assert [i["id"] for i in created["items"]] == ["I01", "I02"]           # 新建即按模板展开
    assert created["progress"] == {"done": 0, "total": 2, "picked": 0, "posted": False, "exported": False}
    assert (project / "content" / "image" / f"{nid}.json").is_file()
    assert [r["id"] for r in client.get("/api/image").json()] == [nid]

    payload = client.get("/api/image/templates").json()
    assert payload["module"] == "image"
    assert sorted(t["id"] for t in payload["templates"]) == sorted(IMAGE_TEMPLATES)
    assert all(t["style_cards"] for t in payload["templates"])            # 每个模板都有「适合」的风格卡
    assert "lantern_night" in payload["examples"]

    raw = json.loads((project / "content" / "image" / f"{nid}.json").read_text(encoding="utf-8"))
    raw["items"][0]["prompt"] = "我自己改过的提示词"
    assert client.put(f"/api/image/{nid}", json=raw).json()["items"][0]["prompt"] == "我自己改过的提示词"
    assert client.post("/api/image", json={"title": "x", "template": "nope"}).status_code == 400
    # 三个前缀是各自的命名空间
    assert client.get(f"/api/sticker/{nid}").status_code == 404
    assert client.delete(f"/api/image/{nid}").status_code == 200
    assert client.get("/api/image").json() == []


def test_api_image_example_create(project):
    r = client.post("/api/image/examples/create", json={})
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["example"] == "lantern_night" and payload["id"].startswith("IM-")
    assert (project / payload["path"]).is_file()
    assert len(payload["items"]) == 4 and payload["spec"]["template"] == "freeform"
    assert payload["spec"]["style"] == "cinematic_key_art"               # 示例挑了一张风格卡
    assert all(i["aspect_ratio"] == "3:4" for i in payload["items"])


def test_api_image_generate_pick_post_export(project, monkeypatch):
    spec_id = create(template="model_compare",
                     options={"brief": "石桥下的河灯", "models": ["@image", "gpt-image-2"]})["id"]
    monkeypatch.setattr(R, "PROVIDER_FACTORY", lambda: FakeProvider(project / "cache"))
    started = client.post(f"/api/image/{spec_id}/generate", json={})
    assert started.status_code == 200, started.text
    assert started.json()["count"] == 2                                   # cost_note 照样按 item 数算
    # 多模型对比：每张实际用的模型都列出来（以前只报项目级的 @image，跟网格里每张的标签对不上）
    assert started.json()["cost_note"] == "最多 2 次生图请求（@image、gpt-image-2）；请求没变且图已在的会跳过"
    assert started.json()["model"] == "@image、gpt-image-2" and started.json()["models"] == ["@image", "gpt-image-2"]
    task = wait_task(started.json()["task_id"])
    assert task["status"] == "succeeded" and task["module"] == "image"
    assert task["result"]["generated"] == ["I01", "I02"]

    detail = client.get(f"/api/image/{spec_id}").json()
    assert detail["items"][1]["meta"]["model"] == "gpt-image-2"
    assert detail["items"][1]["attempts"][0]["model"] == "gpt-image-2"    # 逐张的模型进了 attempts
    assert client.get(detail["items"][0]["url"]).status_code == 200

    run = client.post(f"/api/image/{spec_id}/post", json={}).json()["run"]
    assert {o["role"] for o in run["outputs"]} == {"image", "w2048"}
    pkg = client.post(f"/api/image/{spec_id}/export", json={}).json()
    download = client.get(pkg["url"])
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        names = zf.namelist()
    assert any(n.endswith("compare/image.png") for n in names)            # @image → 文件名 image
    assert any(n.endswith("compare/gpt_image_2.png") for n in names)
    assert client.get(f"/api/image/{spec_id}").json()["progress"]["exported"] is True


def test_api_generate_reports_the_models_actually_submitted(project, monkeypatch):
    """多模型对比：响应里的 model / cost_note 按目标 items 汇总每张实际用的模型；``only`` 只出一张时只报那一张的。"""

    spec_id = create(template="model_compare", options={"brief": "一只猫", "models": ["gpt-image-2", "nano-banana"]})["id"]
    provider = FakeProvider(project / "cache")
    monkeypatch.setattr(R, "PROVIDER_FACTORY", lambda: provider)
    started = client.post(f"/api/image/{spec_id}/generate", json={})
    assert started.status_code == 200, started.text
    assert started.json()["models"] == ["gpt-image-2", "nano-banana"] and started.json()["model"] == "gpt-image-2、nano-banana"
    assert started.json()["cost_note"] == "最多 2 次生图请求（gpt-image-2、nano-banana）；请求没变且图已在的会跳过"
    wait_task(started.json()["task_id"])
    assert [r.model for r in provider.requests] == ["gpt-image-2", "nano-banana"]   # 提示说的就是真提交的
    only = client.post(f"/api/image/{spec_id}/generate", json={"only": ["I02"], "force": True})
    assert only.status_code == 200, only.text
    assert only.json()["models"] == ["nano-banana"] and only.json()["count"] == 1
    assert only.json()["cost_note"] == "最多 1 次生图请求（nano-banana）；请求没变且图已在的会跳过"
    wait_task(only.json()["task_id"])
    assert provider.requests[-1].model == "nano-banana"


# ---------------------------------------------------------------------------- 模型候选（docs/imagestudio-spec.md §6.5）


@pytest.fixture
def no_network(monkeypatch):
    """任何一次真去连网 / 探本机的调用都当场失败：候选清单只准读缓存。"""

    def boom(*a, **k):
        raise AssertionError("model_candidates 不许发请求")

    monkeypatch.setattr(model_catalog, "_get_json", boom)
    monkeypatch.setattr(model_catalog, "fetch_openrouter", boom)
    monkeypatch.setattr(model_catalog, "fetch_openai_models", boom)
    monkeypatch.setattr(local_mod, "default_fetch", boom)
    monkeypatch.setattr(local_mod, "scan", boom)
    return boom


@pytest.fixture
def caches(project, monkeypatch):
    """一台「配好了、目录拉过、本机扫过」的机器。返回 home 目录，测试自己决定往里放什么。"""

    for role in ("image", "video", "text", "voice", "music"):
        for field in ("adapter", "base_url", "api_key", "model", "voice"):
            monkeypatch.delenv(f"STORYSTUDIO_{role.upper()}_{field.upper()}", raising=False)
    LM.reset_hint_cache()
    home = project / "home"
    home.mkdir(parents=True, exist_ok=True)
    yield home
    LM.reset_hint_cache()


def write_settings(home: Path, model: str) -> None:
    (home / "settings.json").write_text(json.dumps(
        {"roles": {"image": {"adapter": "openai_images", "base_url": "https://api.openai.com/v1", "model": model}}},
        ensure_ascii=False), encoding="utf-8")


def write_catalog(home: Path, **sections) -> None:
    (home / "model_catalog.json").write_text(json.dumps(
        {source: {"fetched_at": 1, "models": models} for source, models in sections.items()},
        ensure_ascii=False), encoding="utf-8")


def test_model_candidates_orders_configured_then_catalog_then_local(caches, no_network):
    write_settings(caches, "gpt-image-1")
    write_catalog(caches,
                  openrouter=[{"id": "openai/gpt-image-2", "name": "GPT Image 2", "roles": ["image"]},
                              {"id": "gpt-image-1", "name": "GPT Image 1", "roles": ["image"]},   # 跟设置里同一个
                              {"id": "some/chat-model", "name": "Chat", "roles": ["text"]},       # 不出图，不要
                              {"id": "some/video", "name": "Video", "roles": ["video"]}],
                  gateway=[{"id": "seedream-4", "name": "seedream-4", "roles": ["image"], "guessed": True}])
    LM.remember_rows([
        {"id": "sdwebui", "name": "Stable Diffusion WebUI / Forge", "alive": True,
         "models": ["sd_xl_base_1.0.safetensors", "gpt-image-1"]},
        {"id": "comfyui", "name": "ComfyUI", "alive": False, "models": ["never-alive.safetensors"]},
        {"id": "ollama", "name": "Ollama", "alive": True, "models": ["qwen2.5"]},                 # 文本服务，不进生图候选
    ])
    rows = R.model_candidates("image")
    assert [(r["id"], r["source"]) for r in rows] == [
        ("@image", "configured"),
        ("gpt-image-1", "configured"),
        ("openai/gpt-image-2", "catalog"),
        ("seedream-4", "catalog"),
        ("sd_xl_base_1.0.safetensors", "local"),
    ]
    assert [r["id"] for r in rows].count("@image") == 1                   # 别名只出现一次
    assert rows[0]["label"] == "@image → gpt-image-1"                     # 别名标签点出它现在是谁
    assert rows[-1]["label"].endswith("Stable Diffusion WebUI / Forge")   # 本机那条说清是哪个服务
    assert len({r["id"] for r in rows}) == len(rows)                      # 去重（gpt-image-1 只算配置那一条）


def test_model_candidates_without_caches_falls_back_to_the_template_default(caches, no_network):
    rows = R.model_candidates("image")
    assert [r["id"] for r in rows] == list(T.DEFAULT_COMPARE_MODELS)
    assert [r["source"] for r in rows] == ["configured", "default", "default"]
    assert rows[0]["label"] == "@image"                                   # 还没配模型：别名后面不跟谁


def test_model_candidates_keeps_local_only_machine(caches, no_network):
    """没配模型、目录没拉过，但本机有 SD WebUI 在跑：候选就是别名 + 本机那几个，不再补静态默认。"""

    LM.remember_rows([{"id": "sdwebui", "name": "SD WebUI", "alive": True, "models": ["dreamshaper.safetensors"]}])
    rows = R.model_candidates("image")
    assert [(r["id"], r["source"]) for r in rows] == [
        ("@image", "configured"), ("dreamshaper.safetensors", "local")]


def test_model_candidates_survives_a_broken_settings_file(caches, no_network):
    (caches / "settings.json").write_text("{ 这不是 json", encoding="utf-8")
    assert [r["id"] for r in R.model_candidates("image")] == list(T.DEFAULT_COMPARE_MODELS)


def test_templates_and_detail_payloads_carry_model_candidates(caches, no_network):
    write_settings(caches, "gpt-image-1")
    write_catalog(caches, openrouter=[{"id": "openai/gpt-image-2", "name": "GPT Image 2", "roles": ["image"]}])
    payload = client.get("/api/image/templates").json()
    assert [r["id"] for r in payload["model_candidates"]] == ["@image", "gpt-image-1", "openai/gpt-image-2"]
    # 模板自己的静态默认清单没变：新装的机器上「多模型对比」照样展开
    models = next(f for t in payload["templates"] if t["id"] == "model_compare"
                  for f in t["options_schema"] if f["key"] == "models")
    assert models["default"] == list(T.DEFAULT_COMPARE_MODELS)
    detail = create(template="model_compare", options={"brief": "石桥下的河灯", "models": ["@image", "gpt-image-2"]})
    assert [r["id"] for r in detail["model_candidates"]] == ["@image", "gpt-image-1", "openai/gpt-image-2"]
    assert [i["label"] for i in detail["items"]] == ["@image", "gpt-image-2"]
