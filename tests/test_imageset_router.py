"""图片集接口（DESIGN §40，契约 §2 的接口表）：两个前缀共用一套处理器，所以整套 CRUD / 展开 / 出图 /
挑选 / 后处理 / 打包都按模块参数化跑两遍——`/api/sticker/...` 与 `/api/portrait/...` 行为必须一致。

出图走假来源（`R.PROVIDER_FACTORY` 注入），后处理与打包是本机 Pillow，不联网、不花钱。
"""

from __future__ import annotations

import base64
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from imageset import post as PO
from imageset import spec as S
from webui import deps, tasks
from webui.app import app
from webui.routers import imageset as R
from tests.test_imageset_generate import FakeProvider

client = TestClient(app)
MODULES = ["sticker", "portrait"]
TEMPLATE = {"sticker": "emoji_pack", "portrait": "baby_100"}
EXAMPLE = {"sticker": "xiaoman_emoji", "portrait": "baby_100_demo"}
PREFIX = {"sticker": "ST-", "portrait": "PT-"}


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


def png_bytes(color=(200, 120, 90), size=(300, 300)) -> bytes:
    """白底 + 居中方块：转透明后角上是透明、中间是主体。"""

    from PIL import Image, ImageDraw

    im = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(im).ellipse((size[0] * 0.2, size[1] * 0.2, size[0] * 0.8, size[1] * 0.8), fill=color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def body(module: str, **extra) -> dict:
    subject = {"kind": "character", "name": "小满", "block": "齐肩黑发低马尾"} if module == "sticker" else \
        {"kind": "photo", "name": "宝宝", "block": "", "fidelity": 88}
    data = {"title": "接口测试集", "template": TEMPLATE[module], "subject": subject,
            "options": {"expressions": ["开心", "哭"], "with_caption": False} if module == "sticker"
            else {"scenes": ["小马甲", "全家福"], "text": ""}}
    data.update(extra)
    return data


def create(module: str, **extra) -> dict:
    r = client.post(f"/api/{module}", json=body(module, **extra))
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


def generate(module: str, spec_id: str, project: Path, monkeypatch, **payload) -> dict:
    monkeypatch.setattr(R, "PROVIDER_FACTORY", lambda: FakeProvider(project / "cache"))
    r = client.post(f"/api/{module}/{spec_id}/generate", json=payload)
    assert r.status_code == 200, r.text
    task = wait_task(r.json()["task_id"])
    assert task["status"] == "succeeded", task
    return task


# ---------------------------------------------------------------------------- 编号 / 新建 / 列表 / 模板


@pytest.mark.parametrize("module", MODULES)
def test_next_id_create_and_list(module, project):
    nid = client.get(f"/api/{module}/next-id").json()["id"]
    assert nid.startswith(PREFIX[module]) and nid.endswith("-1")
    created = create(module)
    assert created["id"] == nid and created["module"] == module and created["title"] == "接口测试集"
    assert created["template"] == TEMPLATE[module] and created["template_name"]
    assert "output_root" not in created["spec"] and "project_root" not in created["spec"]
    assert [i["id"] for i in created["items"]] == ["I01", "I02"]            # 新建即按模板展开
    assert created["progress"] == {"done": 0, "total": 2, "picked": 0, "posted": False, "exported": False}
    assert created["outputs"] is None and created["exports"] == [] and created["cover_url"] is None
    assert created["busy"] == {"generate": None, "post": None}
    assert created["template_info"]["id"] == TEMPLATE[module] and created["template_info"]["options_schema"]
    assert isinstance(created["font_available"], bool)
    assert (project / "content" / module / f"{nid}.json").is_file()

    second = create(module)
    assert second["id"].endswith("-2")
    assert client.post(f"/api/{module}", json=body(module, id=nid)).status_code == 409
    assert client.post(f"/api/{module}", json=body(module, id="bad id")).status_code == 400
    assert client.post(f"/api/{module}", json=body(module, template="nope")).status_code == 400

    rows = client.get(f"/api/{module}").json()
    assert [r["id"] for r in rows] == [nid, second["id"]]
    assert rows[0]["module"] == module and rows[0]["pipeline"] == module and rows[0]["status"] == "ok"
    assert rows[0]["path"] == f"content/{module}/{nid}.json" and rows[0]["template_name"] and rows[0]["cover_url"] is None
    # 坏文件不让列表挂掉
    (project / "content" / module / "BAD-1.json").write_text("{not json", encoding="utf-8")
    broken = next(r for r in client.get(f"/api/{module}").json() if r["id"] == "BAD-1")
    assert broken["status"] == "error" and broken["error"]


@pytest.mark.parametrize("module", MODULES)
def test_templates_endpoint(module, project):
    payload = client.get(f"/api/{module}/templates").json()
    assert payload["module"] == module
    ids = [t["id"] for t in payload["templates"]]
    assert TEMPLATE[module] in ids and len(ids) == (3 if module == "sticker" else 4)
    assert all(t["module"] == module and t["styles"] and t["options_schema"] for t in payload["templates"])
    assert payload["subject_kinds"] == list(S.SUBJECT_KINDS) and payload["max_assets"] == S.MAX_ASSETS
    assert payload["who_choices"] == ["bride", "groom"] and payload["default_fidelity"] == S.DEFAULT_FIDELITY
    assert payload["default_image_params"] == {"model": "@image", "image_size": "2K"}
    assert EXAMPLE[module] in payload["examples"]
    assert isinstance(payload["font_available"], bool)


@pytest.mark.parametrize("module", MODULES)
def test_example_create(module, project):
    r = client.post(f"/api/{module}/examples/create", json={"name": EXAMPLE[module]})
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["example"] == EXAMPLE[module] and (project / payload["path"]).is_file()
    assert payload["id"].startswith(PREFIX[module]) and payload["items"]
    assert len(payload["items"]) == (8 if module == "sticker" else 6)
    assert client.post(f"/api/{module}/examples/create", json={"name": "nope"}).status_code == 404
    assert client.post(f"/api/{module}/examples/create", json={"name": "../picturebook/fox_three_socks"}).status_code == 404
    # 不给名字 → 用该模块的默认示例
    assert client.post(f"/api/{module}/examples/create", json={}).json()["example"] == EXAMPLE[module]


# ---------------------------------------------------------------------------- 读 / 写 / 删


@pytest.mark.parametrize("module", MODULES)
def test_get_put_and_validation_rejects_without_writing(module, project):
    spec_id = create(module)["id"]
    path = project / "content" / module / f"{spec_id}.json"
    before = path.read_text(encoding="utf-8")

    bad = json.loads(before)
    bad["picks"] = ["I99"]
    r = client.put(f"/api/{module}/{spec_id}", json=bad)
    assert r.status_code == 400 and "改动没通过校验，没有写盘" in r.json()["detail"]
    assert path.read_text(encoding="utf-8") == before

    good = json.loads(before)
    good["items"][0]["prompt"] = "我自己改过的提示词"
    r = client.put(f"/api/{module}/{spec_id}", json=good)
    assert r.status_code == 200 and r.json()["items"][0]["prompt"] == "我自己改过的提示词"
    assert client.get(f"/api/{module}/{spec_id}").json()["items"][0]["prompt"] == "我自己改过的提示词"
    assert client.put(f"/api/{module}/{spec_id}", json={**good, "id": "OTHER-1"}).status_code == 400
    assert client.put(f"/api/{module}/NOPE-1", json=good).status_code == 404
    assert client.get(f"/api/{module}/NOPE-1").status_code == 404


@pytest.mark.parametrize("module", MODULES)
def test_expand_rebuilds_items_and_drops_stale_picks(module, project):
    spec_id = create(module)["id"]
    detail = client.post(f"/api/{module}/{spec_id}/pick", json={"picks": ["I01"]}).json()
    assert detail["picks"] == ["I01"]
    raw = json.loads((project / "content" / module / f"{spec_id}.json").read_text(encoding="utf-8"))
    key = "expressions" if module == "sticker" else "scenes"
    raw["options"][key] = [raw["options"][key][1]]
    assert client.put(f"/api/{module}/{spec_id}", json=raw).status_code == 200
    after = client.post(f"/api/{module}/{spec_id}/expand", json={}).json()
    assert [i["id"] for i in after["items"]] == ["I01"] and after["picks"] == ["I01"]
    assert after["items"][0]["label"] == raw["options"][key][0]
    assert client.post(f"/api/{module}/NOPE-1/expand", json={}).status_code == 404


@pytest.mark.parametrize("module", MODULES)
def test_delete_archives_spec_and_output(module, project, monkeypatch):
    spec_id = create(module)["id"]
    generate(module, spec_id, project, monkeypatch)
    r = client.delete(f"/api/{module}/{spec_id}")
    assert r.status_code == 200 and r.json()["output_moved"] is True
    archived = project / r.json()["archived_to"]
    assert (archived / "spec.json").is_file() and (archived / "output" / "01_raw" / "I01-1.png").is_file()
    assert archived.parent == project / ".webui_deleted" / module
    assert not (project / "content" / module / f"{spec_id}.json").exists()
    assert client.delete(f"/api/{module}/{spec_id}").status_code == 404
    assert client.get(f"/api/{module}").json() == []


# ---------------------------------------------------------------------------- 参考图上传


@pytest.mark.parametrize("module", MODULES)
def test_asset_upload_is_base64_and_never_overwrites(module, project):
    spec_id = create(module)["id"]
    payload = {"filename": "ref.png", "data_base64": base64.b64encode(png_bytes()).decode()}
    r = client.post(f"/api/{module}/{spec_id}/assets", json=payload)
    assert r.status_code == 200, r.text
    file = r.json()["file"]
    assert file.startswith(f"content/assets/{spec_id}/ref-") and (project / file).is_file()
    assert r.json()["who"] == "" and r.json()["bytes"] > 0
    assert client.get(r.json()["url"]).status_code == 200
    detail = client.get(f"/api/{module}/{spec_id}").json()
    assert [a["file"] for a in detail["assets"]] == [file] and detail["assets"][0]["url"]
    assert detail["spec"]["subject"]["assets"] == [file]

    # 同内容再传：同名同文件（不覆盖，也不写第二份）；不同内容 → 新文件
    again = client.post(f"/api/{module}/{spec_id}/assets", json=payload)
    assert again.json()["file"] == file
    other = {"filename": "ref.png", "data_base64": base64.b64encode(png_bytes((10, 20, 30))).decode()}
    assert client.post(f"/api/{module}/{spec_id}/assets", json=other).json()["file"] != file
    assert len(list((project / "content" / "assets" / spec_id).glob("*.png"))) == 2

    assert client.post(f"/api/{module}/{spec_id}/assets",
                       json={"filename": "ref.exe", "data_base64": "AA=="}).status_code == 400
    assert client.post(f"/api/{module}/{spec_id}/assets",
                       json={"filename": "ref.png", "data_base64": "not base64!!"}).status_code == 400
    assert client.post(f"/api/{module}/{spec_id}/assets",
                       json={"filename": "ref.png", "data_base64": ""}).status_code == 400
    big = base64.b64encode(b"\x00" * (R.MAX_UPLOAD_BYTES + 1)).decode()
    assert client.post(f"/api/{module}/{spec_id}/assets", json={"filename": "ref.png", "data_base64": big}).status_code == 413
    assert client.post(f"/api/{module}/NOPE-1/assets", json=payload).status_code == 404


@pytest.mark.parametrize("module", MODULES)
def test_asset_upload_stops_at_six(module, project):
    spec_id = create(module)["id"]
    for n in range(S.MAX_ASSETS):
        payload = {"filename": f"ref{n}.png", "data_base64": base64.b64encode(png_bytes((n * 20, 40, 60))).decode()}
        assert client.post(f"/api/{module}/{spec_id}/assets", json=payload).status_code == 200
    extra = {"filename": "ref7.png", "data_base64": base64.b64encode(png_bytes((7, 7, 7))).decode()}
    r = client.post(f"/api/{module}/{spec_id}/assets", json=extra)
    assert r.status_code == 400 and f"最多 {S.MAX_ASSETS} 张" in r.json()["detail"]
    assert len(client.get(f"/api/{module}/{spec_id}").json()["assets"]) == S.MAX_ASSETS


def test_wedding_asset_carries_who(project):
    spec_id = client.post("/api/portrait", json={"title": "婚纱", "template": "wedding",
                                                 "subject": {"kind": "photo", "fidelity": 90},
                                                 "options": {"scenes": ["教堂"]}}).json()["id"]
    bride = {"filename": "bride.png", "data_base64": base64.b64encode(png_bytes()).decode(), "who": "bride"}
    groom = {"filename": "groom.png", "data_base64": base64.b64encode(png_bytes((30, 40, 50))).decode(), "who": "groom"}
    assert client.post(f"/api/portrait/{spec_id}/assets", json=bride).json()["who"] == "bride"
    assert client.post(f"/api/portrait/{spec_id}/assets", json=groom).json()["who"] == "groom"
    detail = client.get(f"/api/portrait/{spec_id}").json()
    assert [a["who"] for a in detail["assets"]] == ["bride", "groom"]
    # 标了 who 之后，展开的提示词逐人点名 + 排他锁
    prompt = client.post(f"/api/portrait/{spec_id}/expand", json={}).json()["items"][0]["prompt"]
    assert "新娘（参考图 1）" in prompt and "新郎（参考图 2）" in prompt and "画面里只有这些人：新娘、新郎" in prompt
    bad = {"filename": "x.png", "data_base64": base64.b64encode(png_bytes()).decode(), "who": "witness"}
    assert client.post(f"/api/portrait/{spec_id}/assets", json=bad).status_code == 400


# ---------------------------------------------------------------------------- 出图


@pytest.mark.parametrize("module", MODULES)
def test_generate_task_cost_note_and_artifacts(module, project, monkeypatch):
    spec_id = create(module)["id"]
    monkeypatch.setattr(R, "PROVIDER_FACTORY", lambda: FakeProvider(project / "cache"))
    r = client.post(f"/api/{module}/{spec_id}/generate", json={})
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["count"] == 2 and payload["model"] == "@image"
    assert payload["cost_note"] == "最多 2 次生图请求（@image）；请求没变且图已在的会跳过"
    task = wait_task(payload["task_id"])
    assert task["status"] == "succeeded" and task["kind"] == "set-generate" and task["module"] == module
    assert task["result"]["generated"] == ["I01", "I02"] and task["result"]["failed"] == []
    assert (project / "output" / module / spec_id / "01_raw" / "I01-1.png").is_file()

    detail = client.get(f"/api/{module}/{spec_id}").json()
    row = detail["items"][0]
    assert row["done"] and row["status"] == "done" and row["attempts"][0]["url"]
    assert row["url"] == f"/api/{module}/{spec_id}/artifact/raw/I01-1.png"
    assert client.get(row["url"]).status_code == 200
    assert detail["progress"]["done"] == 2 and detail["cover_url"] == row["url"]
    assert client.get(f"/api/{module}").json()[0]["cover_url"] == row["url"]

    # 第二次：请求没变 → 全部跳过
    again = wait_task(client.post(f"/api/{module}/{spec_id}/generate", json={}).json()["task_id"])
    assert again["result"]["skipped"] == ["I01", "I02"] and again["result"]["generated"] == []
    # 单张重出
    forced = wait_task(client.post(f"/api/{module}/{spec_id}/generate", json={"only": ["I01"], "force": True}).json()["task_id"])
    assert forced["result"]["generated"] == ["I01"]
    assert client.get(f"/api/{module}/{spec_id}").json()["items"][0]["current"] == "I01-2.png"
    assert client.post(f"/api/{module}/{spec_id}/generate", json={"only": ["I09"]}).status_code == 404
    assert [t["kind"] for t in client.get("/api/tasks", params={"module": module}).json()] == ["set-generate"] * 3


@pytest.mark.parametrize("module", MODULES)
def test_generate_needs_items(module, project):
    spec_id = create(module)["id"]
    raw = json.loads((project / "content" / module / f"{spec_id}.json").read_text(encoding="utf-8"))
    raw["items"] = []
    raw["picks"] = []
    assert client.put(f"/api/{module}/{spec_id}", json=raw).status_code == 200
    r = client.post(f"/api/{module}/{spec_id}/generate", json={})
    assert r.status_code == 400 and "先按模板展开" in r.json()["detail"]
    assert client.post(f"/api/{module}/NOPE-1/generate", json={}).status_code == 404


@pytest.mark.parametrize("module", MODULES)
def test_second_generate_while_running_is_409(module, project, monkeypatch):
    spec_id = create(module)["id"]
    entered, release = threading.Event(), threading.Event()

    class BlockingProvider(FakeProvider):
        def submit_image(self, request):
            entered.set()
            release.wait(timeout=10)
            return super().submit_image(request)

    monkeypatch.setattr(R, "PROVIDER_FACTORY", lambda: BlockingProvider(project / "cache"))
    first = client.post(f"/api/{module}/{spec_id}/generate", json={})
    assert first.status_code == 200
    assert entered.wait(timeout=10)
    try:
        busy = client.post(f"/api/{module}/{spec_id}/generate", json={})
        assert busy.status_code == 409 and busy.json()["detail"]
        assert client.get(f"/api/{module}/{spec_id}").json()["busy"]["generate"] == first.json()["task_id"]
    finally:
        release.set()
    assert wait_task(first.json()["task_id"])["status"] == "succeeded"
    assert client.get(f"/api/{module}/{spec_id}").json()["busy"]["generate"] is None


# ---------------------------------------------------------------------------- 挑选


@pytest.mark.parametrize("module", MODULES)
def test_pick_attempt_and_picks(module, project, monkeypatch):
    spec_id = create(module)["id"]
    generate(module, spec_id, project, monkeypatch)
    wait_task(client.post(f"/api/{module}/{spec_id}/generate", json={"only": ["I01"], "force": True}).json()["task_id"])
    assert client.get(f"/api/{module}/{spec_id}").json()["items"][0]["current"] == "I01-2.png"

    # 采用上一次
    r = client.post(f"/api/{module}/{spec_id}/pick", json={"item": "I01", "attempt": "I01-1.png"})
    assert r.status_code == 200, r.text
    row = r.json()["items"][0]
    assert row["current"] == "I01-1.png" and row["status"] == "done" and row["error"] is None
    assert r.json()["items"][0]["url"].endswith("I01-1.png")

    # 挑选
    picked = client.post(f"/api/{module}/{spec_id}/pick", json={"picks": ["I02"]}).json()
    assert picked["picks"] == ["I02"] and [i["picked"] for i in picked["items"]] == [False, True]
    assert picked["progress"]["picked"] == 1
    assert client.post(f"/api/{module}/{spec_id}/pick", json={"picks": []}).json()["picks"] == []

    assert client.post(f"/api/{module}/{spec_id}/pick", json={"item": "I01", "attempt": "I01-9.png"}).status_code == 400
    assert client.post(f"/api/{module}/{spec_id}/pick", json={"item": "I01"}).status_code == 400
    assert client.post(f"/api/{module}/{spec_id}/pick", json={"item": "I99", "attempt": "I01-1.png"}).status_code == 404
    assert client.post(f"/api/{module}/{spec_id}/pick", json={"picks": ["I99"]}).status_code == 400
    assert client.post(f"/api/{module}/NOPE-1/pick", json={"picks": []}).status_code == 404


# ---------------------------------------------------------------------------- 后处理 / 打包 / 产物流


@pytest.mark.parametrize("module", MODULES)
def test_post_sync_then_export(module, project, monkeypatch):
    spec_id = create(module)["id"]
    assert client.post(f"/api/{module}/{spec_id}/post", json={}).status_code == 425     # 还没出图
    assert client.post(f"/api/{module}/{spec_id}/export", json={}).status_code == 425   # 还没后处理
    generate(module, spec_id, project, monkeypatch)

    r = client.post(f"/api/{module}/{spec_id}/post", json={})
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    assert run["items"] == ["I01", "I02"] and run["outputs"]
    roles = {o["role"] for o in run["outputs"]}
    assert ({"main", "thumb"} <= roles) if module == "sticker" else ({"album", "album_4x3"} <= roles)
    first = run["outputs"][0]
    assert first["url"] == f"/api/{module}/{spec_id}/artifact/out/{first['file']}"
    assert client.get(first["url"]).status_code == 200
    detail = client.get(f"/api/{module}/{spec_id}").json()
    assert detail["progress"]["posted"] is True and detail["outputs"]["stamp"] == run["stamp"]

    exported = client.post(f"/api/{module}/{spec_id}/export", json={})
    assert exported.status_code == 200, exported.text
    pkg = exported.json()
    assert pkg["url"] == f"/api/{module}/{spec_id}/artifact/export/{pkg['name']}" and pkg["files"] >= 2
    download = client.get(pkg["url"])
    assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        names = zf.namelist()
    assert any(n.endswith("manifest.json") for n in names) and any(n.endswith("checksums.sha256") for n in names)
    assert [e["name"] for e in pkg["exports"]] == [pkg["name"]]
    assert client.get(f"/api/{module}/{spec_id}").json()["progress"]["exported"] is True


@pytest.mark.parametrize("module", MODULES)
def test_post_in_background_is_a_task(module, project, monkeypatch):
    spec_id = create(module)["id"]
    generate(module, spec_id, project, monkeypatch)
    r = client.post(f"/api/{module}/{spec_id}/post", json={"background": True})
    assert r.status_code == 200 and r.json()["task_id"]
    task = wait_task(r.json()["task_id"])
    assert task["status"] == "succeeded" and task["kind"] == "set-post" and task["module"] == module
    assert task["result"]["outputs"] and task["result"]["outputs"][0]["url"]
    assert PO.latest_run(S.load(module, spec_id, project_root=project)) is not None


@pytest.mark.parametrize("module", MODULES)
def test_artifact_paths_are_safe(module, project, monkeypatch):
    spec_id = create(module)["id"]
    generate(module, spec_id, project, monkeypatch)
    client.post(f"/api/{module}/{spec_id}/post", json={})
    (project / "content" / "assets" / spec_id).mkdir(parents=True, exist_ok=True)
    (project / "secret.png").write_bytes(png_bytes())
    base = f"/api/{module}/{spec_id}/artifact"
    for kind in ("raw", "out", "asset", "export"):
        for name in ("..%2F..%2Fsecret.png", "..%5Csecret.png", "sub%2Fx.png", ".hidden.png", "nope.png", "%2Fetc%2Fpasswd"):
            assert client.get(f"{base}/{kind}/{name}").status_code == 404, (kind, name)
    # 目录里确实有的、但不在项目清单里的原图也不给
    stray = project / "output" / module / spec_id / "01_raw" / "stray.png"
    stray.write_bytes(png_bytes())
    assert client.get(f"{base}/raw/stray.png").status_code == 404
    assert client.get(f"{base}/raw/I01-1.png").status_code == 200
    assert client.get(f"{base}/out/../../content/{module}/{spec_id}.json").status_code == 404
    assert client.get(f"/api/{module}/NOPE-1/artifact/raw/I01-1.png").status_code == 404


# ---------------------------------------------------------------------------- 前缀


def test_unknown_module_prefix_is_404(project):
    assert client.get("/api/avatarset").status_code == 404
    assert client.get("/api/sticker/templates").status_code == 200
    assert client.get("/api/portrait/templates").status_code == 200
    # 两个前缀是各自的命名空间：一个模块的项目在另一个模块里不存在
    spec_id = create("sticker")["id"]
    assert client.get(f"/api/sticker/{spec_id}").status_code == 200
    assert client.get(f"/api/portrait/{spec_id}").status_code == 404
    with pytest.raises(S.SetSpecError):
        R.make_router("nope")
