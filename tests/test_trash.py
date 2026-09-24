"""回收站（全模块）：每个模块「建 → 删 → 回收站里有 → 恢复 → 详情正常」、同 id 已存在 / 原位被占 409、
manifest、素材目录一起归档（被别的项目引用就留下）、删除时有任务在跑 409、彻底删除、老归档（没有 manifest）照样能恢复。

以前只有短剧能在界面上恢复；其它模块删除后确认框只能教人「手动搬回来」，而归档时项目文件已被改名成 spec.json、
content/assets/<id>/ 还留在原地——这里用 TestClient + 临时数据根把每个模块真走一遍。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import trash
from webui import deps, tasks
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router
from webui.routers import scripts as scripts_router

client = TestClient(app)

# 模块 → (示例建项目的接口, 示例请求里 id 的字段名)
MODULES = {
    "clip": ("/api/clip/examples/create", "id"),
    "music": ("/api/music/examples/create", "id"),
    "podcast": ("/api/podcast/examples/create", "id"),
    "script": ("/api/script/examples/create", "id"),
    "sticker": ("/api/sticker/examples/create", "id"),
    "portrait": ("/api/portrait/examples/create", "id"),
    "image": ("/api/image/examples/create", "id"),
    "picturebook": ("/api/picturebook/examples/create", "book_id"),
}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "settings.json").write_text('{"roles": {}}', encoding="utf-8")
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    # 路由模块在 import 时把 deps 的路径常量绑成了自己的全局：短剧相关的每个模块都要 patch（见 test_drama_state_release）
    for module in (deps, episodes_router, post_router, project_router, scripts_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    for module in (deps, episodes_router, scripts_router):
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "DELETED_DIR", tmp_path / ".webui_deleted")
    tasks._running.clear()
    return tmp_path


def create(module: str, project_id: str | None = None) -> str:
    url, id_key = MODULES[module]
    r = client.post(url, json={id_key: project_id} if project_id else {})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def api(module: str, project_id: str) -> str:
    return f"/api/{module}/{project_id}"


def seed_files(root: Path, module: str, project_id: str) -> None:
    """产物目录 + 上传的素材各放一个文件：归档要把它们一起带走，恢复要原样放回。"""

    (root / "output" / module / project_id / "01_raw").mkdir(parents=True, exist_ok=True)
    (root / "output" / module / project_id / "01_raw" / "take-1.bin").write_bytes(b"x" * 2048)
    (root / "content" / "assets" / project_id).mkdir(parents=True, exist_ok=True)
    (root / "content" / "assets" / project_id / "ref.png").write_bytes(b"p" * 100)


def listed(module: str | None = None) -> list[dict]:
    r = client.get("/api/deleted", params={"module": module} if module else None)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------- 每个模块：建 → 删 → 列表里有 → 恢复 → 详情正常


@pytest.mark.parametrize("module", sorted(MODULES))
def test_delete_list_restore_round_trip(root, module):
    pid = create(module)
    before = client.get(api(module, pid))
    assert before.status_code == 200, before.text
    title = before.json().get("title") or ""
    seed_files(root, module, pid)

    r = client.delete(api(module, pid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archived_to"].startswith(f".webui_deleted/{module}/{pid}__")
    assert body["output_moved"] is True and body["assets_moved"] is True
    # 原位什么都不剩：项目文件、产物目录、上传的素材（留着的话同 id 重建会「继承」上一个项目的参考图 / 音乐）
    assert not (root / "content" / module / f"{pid}.json").exists()
    assert not (root / "output" / module / pid).exists() and not (root / "content" / "assets" / pid).exists()
    assert client.get(api(module, pid)).status_code == 404

    archived = root / body["archived_to"]
    manifest = json.loads((archived / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["module"] == module and manifest["id"] == pid and manifest["title"] == title
    assert manifest["deleted_at"].endswith("Z")
    assert {(i["from"], i["to"]) for i in manifest["items"]} == {
        (f"content/{module}/{pid}.json", "spec.json"), (f"output/{module}/{pid}", "output"), (f"content/assets/{pid}", "assets")}

    rows = listed(module)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == pid and row["module"] == module and row["title"] == title
    assert row["restorable"] is True and row["id_taken"] is False and row["has_output"] and row["has_assets"]
    assert row["bytes"] >= 2048 + 100 and row["deleted_at"] == manifest["deleted_at"]
    assert row["entry"] == archived.name and row["archived_to"] == body["archived_to"]
    assert any(x["entry"] == archived.name for x in listed())   # 不带 module 列全部模块

    r = client.post(f"/api/deleted/{module}/{row['entry']}/restore")
    assert r.status_code == 200, r.text
    assert r.json() == {"module": module, "id": pid, "restored": {"spec": True, "output": True, "assets": True},
                        "path": f"content/{module}/{pid}.json", "spec_broken": False}
    after = client.get(api(module, pid))
    assert after.status_code == 200, after.text
    assert after.json()["id"] == pid and (after.json().get("title") or "") == title
    assert (root / "output" / module / pid / "01_raw" / "take-1.bin").read_bytes() == b"x" * 2048
    assert (root / "content" / "assets" / pid / "ref.png").is_file()
    # 归档目录连同 manifest 一起清掉，回收站里不再有这一条
    assert not archived.exists() and listed(module) == []
    assert client.post(f"/api/deleted/{module}/{row['entry']}/restore").status_code == 404


# ---------------------------------------------------------------------------- 同 id 已存在 / 原位被占：409，什么都不动


@pytest.mark.parametrize("module", ["clip", "image", "picturebook"])
def test_restore_refuses_when_same_id_exists(root, module):
    pid = create(module)
    seed_files(root, module, pid)
    entry = Path(client.delete(api(module, pid)).json()["archived_to"]).name
    assert create(module, pid) == pid     # 同 id 重建了一个新的

    assert listed(module)[0]["id_taken"] is True
    r = client.post(f"/api/deleted/{module}/{entry}/restore")
    assert r.status_code == 409
    assert r.json()["detail"] == f"没有恢复：项目 {pid} 已经存在——先删除现在这个同 id 的项目（它也会进回收站），再恢复这一份"
    # 归档原样、新项目原样
    archived = root / ".webui_deleted" / module / entry
    assert (archived / "spec.json").is_file() and (archived / "output" / "01_raw" / "take-1.bin").is_file()
    assert (archived / "assets" / "ref.png").is_file()
    assert client.get(api(module, pid)).status_code == 200
    assert not (root / "output" / module / pid).exists()


def test_restore_refuses_when_output_or_assets_dir_is_occupied(root):
    pid = create("music")
    seed_files(root, "music", pid)
    entry = Path(client.delete(api("music", pid)).json()["archived_to"]).name
    stray = root / "output" / "music" / pid
    stray.mkdir(parents=True)
    (stray / "someone-else.mp3").write_bytes(b"ID3")
    r = client.post(f"/api/deleted/music/{entry}/restore")
    assert r.status_code == 409 and "同 id 的产物目录已经存在（output/music/" in r.json()["detail"]
    assert not (root / "content" / "music" / f"{pid}.json").exists()   # 项目文件也没放回：不只恢复一半
    assert (stray / "someone-else.mp3").read_bytes() == b"ID3"          # 不覆盖原位的东西

    stray.rename(root / "parked")
    (root / "content" / "assets" / pid).mkdir(parents=True)
    r = client.post(f"/api/deleted/music/{entry}/restore")
    assert r.status_code == 409 and "同 id 的素材目录已经存在（content/assets/" in r.json()["detail"]
    (root / "content" / "assets" / pid).rmdir()
    assert client.post(f"/api/deleted/music/{entry}/restore").status_code == 200


# ---------------------------------------------------------------------------- 归档细节


def test_assets_dir_still_used_by_another_project_stays_in_place(root):
    pid = create("image")
    seed_files(root, "image", pid)
    other = root / "content" / "image" / "IM-OTHER.json"
    other.write_text(json.dumps({"id": "IM-OTHER", "assets": [{"path": f"content/assets/{pid}/ref.png"}]}), encoding="utf-8")

    body = client.delete(api("image", pid)).json()
    assert body["assets_moved"] is False and body["output_moved"] is True
    assert (root / "content" / "assets" / pid / "ref.png").is_file()   # 别的项目还用着，不搬
    manifest = json.loads((root / body["archived_to"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["kept"] == [{"path": f"content/assets/{pid}", "used_by": ["image/IM-OTHER"]}]
    # 归档里没有 assets 的条目照常恢复（目录本来就在原位）
    entry = Path(body["archived_to"]).name
    r = client.post(f"/api/deleted/image/{entry}/restore")
    assert r.status_code == 200 and r.json()["restored"] == {"spec": True, "output": True, "assets": False}


def test_delete_refuses_while_a_task_is_running(root):
    pid = create("clip")
    task_id = "t-busy-clip"
    tasks._running[(pid, "clip-generate")] = task_id
    tasks._status[task_id] = "running"
    try:
        r = client.delete(api("clip", pid))
        assert r.status_code == 409 and r.json()["detail"] == trash.TASKS_RUNNING
        assert client.get(api("clip", pid)).status_code == 200 and listed("clip") == []
    finally:
        tasks._status[task_id] = "succeeded"
        tasks._running.clear()
    assert client.delete(api("clip", pid)).status_code == 200


def test_archive_same_id_twice_in_one_second_keeps_two_entries(root):
    now = datetime(2026, 9, 23, 1, 2, 3, tzinfo=timezone.utc)
    first, _ = trash.new_entry_dir(root, "clip", "CL-1", now=now)
    second, _ = trash.new_entry_dir(root, "clip", "CL-1", now=now)
    assert first.name == "CL-1__20260923T010203Z" and second.name == "CL-1__20260923T010203Z-2"
    assert trash.ENTRY_RE.fullmatch(second.name)


def test_legacy_archive_without_manifest_still_lists_and_restores(root):
    """这次之前删的项目：归档里只有 spec.json + output，没有 manifest、素材目录还在原地。"""

    pid = create("script")
    title = client.get(api("script", pid)).json()["title"]
    (root / "output" / "script" / pid).mkdir(parents=True)
    (root / "output" / "script" / pid / "review.json").write_text("{}", encoding="utf-8")
    legacy = root / ".webui_deleted" / "script" / f"{pid}__20260901T080000Z"
    legacy.mkdir(parents=True)
    (root / "content" / "script" / f"{pid}.json").rename(legacy / "spec.json")
    (root / "output" / "script" / pid).rename(legacy / "output")

    row = listed("script")[0]
    assert row["title"] == title and row["restorable"] is True and row["has_manifest"] is False
    assert row["deleted_at"] == "2026-09-01T08:00:00Z"
    r = client.post(f"/api/deleted/script/{legacy.name}/restore")
    assert r.status_code == 200, r.text
    assert client.get(api("script", pid)).status_code == 200 and (root / "output" / "script" / pid / "review.json").is_file()
    assert not legacy.exists()


def test_unknown_module_is_listed_but_not_restorable(root):
    odd = root / ".webui_deleted" / "oldmodule" / "OLD-1__20260901T080000Z"
    odd.mkdir(parents=True)
    (odd / "spec.json").write_text(json.dumps({"id": "OLD-1", "title": "旧模块"}), encoding="utf-8")
    row = listed("oldmodule")[0]
    assert row["restorable"] is False and row["title"] == "旧模块"
    r = client.post(f"/api/deleted/oldmodule/{odd.name}/restore")
    assert r.status_code == 400 and "暂不支持恢复这个模块的归档" in r.json()["detail"]
    # 恢复不了的也能从回收站里彻底删掉
    assert client.delete(f"/api/deleted/oldmodule/{odd.name}").status_code == 200 and not odd.exists()


# ---------------------------------------------------------------------------- 彻底删除


def test_purge_removes_the_entry_for_good(root):
    pid = create("podcast")
    seed_files(root, "podcast", pid)
    entry = Path(client.delete(api("podcast", pid)).json()["archived_to"]).name
    r = client.delete(f"/api/deleted/podcast/{entry}")
    assert r.status_code == 200, r.text
    assert r.json()["entry"] == entry and r.json()["freed_bytes"] >= 2048 + 100
    assert listed("podcast") == [] and not (root / ".webui_deleted" / "podcast" / entry).exists()
    assert client.delete(f"/api/deleted/podcast/{entry}").status_code == 404
    assert client.post(f"/api/deleted/podcast/{entry}/restore").status_code == 404
    # 名字形状不对（穿越、模块名带路径）一律 404，不碰回收站以外的东西
    assert client.delete("/api/deleted/podcast/..%2F..%2Fcontent").status_code == 404
    assert client.delete(f"/api/deleted/..%2Fcontent/{entry}").status_code == 404
    assert (root / "content").is_dir()


def test_purge_clears_a_leftover_tombstone_and_hides_it_from_the_list(root):
    """上次彻底删除删到一半留下的 .purging-<归档名>：列表里不出现，再删同名归档时先清掉它，不因为改名撞名失败。"""

    pid = create("clip")
    entry = Path(client.delete(api("clip", pid)).json()["archived_to"]).name
    tomb = root / ".webui_deleted" / "clip" / f".purging-{entry}"
    (tomb / "output").mkdir(parents=True)
    (tomb / "output" / "half.bin").write_bytes(b"x")
    assert [x["entry"] for x in listed("clip")] == [entry]
    assert client.delete(f"/api/deleted/clip/{entry}").status_code == 200
    assert not tomb.exists() and listed("clip") == []


@pytest.mark.skipif(sys.platform != "win32", reason="只有 Windows 会因为文件句柄被占用而搬不动 / 删不掉整个目录")
def test_purge_and_restore_with_an_open_file_change_nothing(root):
    pid = create("clip")
    seed_files(root, "clip", pid)
    entry = Path(client.delete(api("clip", pid)).json()["archived_to"]).name
    archived = root / ".webui_deleted" / "clip" / entry
    with (archived / "output" / "01_raw" / "take-1.bin").open("ab") as handle:
        handle.write(b"still playing")
        r = client.delete(f"/api/deleted/clip/{entry}")
        assert r.status_code == 409 and r.json()["detail"].startswith("有文件正被占用，没删除：")
        r = client.post(f"/api/deleted/clip/{entry}/restore")
        assert r.status_code == 409 and r.json()["detail"].startswith("有文件正被占用，没恢复：")
    assert (archived / "spec.json").is_file() and (archived / "output" / "01_raw" / "take-1.bin").is_file()
    assert not (root / "content" / "clip" / f"{pid}.json").exists() and not (root / "output" / "clip" / pid).exists()
    assert client.post(f"/api/deleted/clip/{entry}/restore").status_code == 200


@pytest.mark.skipif(sys.platform != "win32", reason="只有 Windows 会因为文件句柄被占用而搬不动整个目录")
def test_delete_with_an_open_output_file_moves_nothing(root):
    pid = create("image")
    seed_files(root, "image", pid)
    with (root / "output" / "image" / pid / "01_raw" / "take-1.bin").open("ab") as handle:
        handle.write(b"still writing")
        r = client.delete(api("image", pid))
        assert r.status_code == 409 and r.json()["detail"].startswith("有文件正被占用，没删除：")
    assert client.get(api("image", pid)).status_code == 200 and (root / "content" / "assets" / pid / "ref.png").is_file()
    assert listed("image") == []


# ---------------------------------------------------------------------------- 短剧：原有的特殊处理不变，补 manifest / 列表字段 / 彻底删除


def test_drama_archive_gets_a_manifest_and_can_be_purged(root):
    assert client.post("/api/examples/first_episode/create", json={"episode_id": "drama-T1"}).status_code == 200
    body = client.delete("/api/episodes/storyvideo/drama-T1").json()
    archived = root / body["archived_to"]
    manifest = json.loads((archived / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["module"] == "storyvideo" and manifest["id"] == "drama-T1" and manifest["title"]
    assert ("content/story_video/drama-T1.json", "spec.json") in {(i["from"], i["to"]) for i in manifest["items"]}

    row = listed("storyvideo")[0]
    assert row["entry"] == archived.name and row["title"] == manifest["title"] and row["bytes"] > 0
    assert row["restorable"] is True and row["id_taken"] is False and row["deleted_at"] == manifest["deleted_at"]
    assert [x["entry"] for x in listed() if x["module"] == "storyvideo"] == [archived.name]

    r = client.post(f"/api/deleted/storyvideo/{archived.name}/restore")
    assert r.status_code == 200 and r.json()["module"] == "storyvideo" and r.json()["id"] == "drama-T1"
    assert not archived.exists()     # manifest 跟空目录一起清掉

    body = client.delete("/api/episodes/storyvideo/drama-T1").json()
    entry = Path(body["archived_to"]).name
    assert client.delete(f"/api/deleted/storyvideo/{entry}").status_code == 200
    assert listed("storyvideo") == []


# ---------------------------------------------------------------------------- 自动编号不复用回收站里的 id


@pytest.mark.parametrize("module", sorted(MODULES))
def test_new_project_does_not_reuse_an_id_still_in_the_trash(root, module):
    """删一个 → 当天再建一个：自动编号以前只看 content/<模块>/，把刚删的 id 原样再发出去——回收站里那一份从此
    id 被占、恢复只能 409，409 又让人先删掉新的，新的进了回收站同样卡住，两份永远没法同时要回来。"""

    first = create(module)
    entry = Path(client.delete(api(module, first)).json()["archived_to"]).name
    suggested = client.get(f"/api/{module}/next-id").json()["id"]
    second = create(module)
    assert suggested != first and second != first

    row = listed(module)[0]
    assert row["id"] == first and row["id_taken"] is False
    r = client.post(f"/api/deleted/{module}/{entry}/restore")
    assert r.status_code == 200, r.text
    assert client.get(api(module, first)).status_code == 200 and client.get(api(module, second)).status_code == 200


def test_drama_auto_id_skips_ids_still_in_the_trash(root):
    first = client.post("/api/examples/first_episode/create", json={}).json()["id"]
    entry = Path(client.delete(f"/api/episodes/storyvideo/{first}").json()["archived_to"]).name
    assert client.get("/api/episodes/next-id", params={"prefix": "DEMO"}).json()["id"] != first
    second = client.post("/api/examples/first_episode/create", json={}).json()["id"]
    assert second != first
    # 新建表单预填的 EP- 编号同样跳过回收站里的
    ep = client.get("/api/episodes/next-id", params={"prefix": "EP"}).json()["id"]
    assert client.post("/api/examples/first_episode/create", json={"episode_id": ep}).status_code == 200
    assert client.delete(f"/api/episodes/storyvideo/{ep}").status_code == 200
    assert client.get("/api/episodes/next-id", params={"prefix": "EP"}).json()["id"] != ep

    r = client.post(f"/api/deleted/storyvideo/{entry}/restore")
    assert r.status_code == 200, r.text
    ids = {x["id"] for x in client.get("/api/episodes").json()}
    assert {first, second} <= ids


# ---------------------------------------------------------------------------- 项目文件是坏的：删得掉，就得原样恢复得回来


def _break_spec(root: Path, module: str, pid: str) -> str:
    content = "story_video" if module == "storyvideo" else module
    broken = '{"id": "%s", "title": "写到一半' % pid
    (root / "content" / content / f"{pid}.json").write_text(broken, encoding="utf-8")
    return broken


@pytest.mark.parametrize("module", ["script", "picturebook"])
def test_corrupt_project_file_round_trips_through_the_trash(root, module):
    """项目文件坏了（列表里 status=error）照样能删；回收站就不能再列成「能恢复」、点了却 400 读不出来——
    原样放回去，跟删除前一样在列表里标成出错，让人能拿回文件自己修。"""

    pid = create(module)
    seed_files(root, module, pid)
    broken = _break_spec(root, module, pid)
    list_url = f"/api/{module}"
    assert next(x for x in client.get(list_url).json() if x["id"] == pid)["status"] == "error"
    assert client.delete(api(module, pid)).status_code == 200

    row = listed(module)[0]
    assert row["id"] == pid and row["restorable"] is True and row["spec_broken"] is True
    r = client.post(f"/api/deleted/{module}/{row['entry']}/restore")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == pid and r.json()["spec_broken"] is True
    assert r.json()["restored"] == {"spec": True, "output": True, "assets": True}
    assert (root / "content" / module / f"{pid}.json").read_text(encoding="utf-8") == broken
    assert (root / "output" / module / pid / "01_raw" / "take-1.bin").is_file()
    assert next(x for x in client.get(list_url).json() if x["id"] == pid)["status"] == "error"
    assert listed(module) == []


def test_readable_project_is_not_flagged_broken(root):
    pid = create("music")
    assert client.delete(api("music", pid)).status_code == 200
    row = listed("music")[0]
    assert row["spec_broken"] is False
    assert client.post(f"/api/deleted/music/{row['entry']}/restore").json()["spec_broken"] is False


def test_drama_corrupt_project_file_round_trips_through_the_trash(root):
    assert client.post("/api/examples/first_episode/create", json={"episode_id": "drama-C1"}).status_code == 200
    broken = _break_spec(root, "storyvideo", "drama-C1")
    assert client.delete("/api/episodes/storyvideo/drama-C1").status_code == 200

    row = listed("storyvideo")[0]
    assert row["id"] == "drama-C1" and row["restorable"] is True and row["spec_broken"] is True
    r = client.post(f"/api/deleted/storyvideo/{row['entry']}/restore")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "drama-C1" and r.json()["spec_broken"] is True
    assert (root / "content" / "story_video" / "drama-C1.json").read_text(encoding="utf-8") == broken
    assert listed("storyvideo") == []


# ---------------------------------------------------------------------------- 列表：每条归档只走一遍磁盘


def test_unfiltered_list_walks_each_archive_once(root, monkeypatch):
    """不带 module 的列表以前把短剧归档走两遍（短剧那一路走一遍，通用那一路又走一遍再丢掉）；
    短剧的归档里是首帧和视频，回收站页每次打开、侧栏列表每变一次都要列一遍。"""

    assert client.post("/api/examples/first_episode/create", json={"episode_id": "drama-W1"}).status_code == 200
    assert client.delete("/api/episodes/storyvideo/drama-W1").status_code == 200
    pid = create("clip")
    assert client.delete(api("clip", pid)).status_code == 200

    walked: list[str] = []
    real = trash._dir_size
    monkeypatch.setattr(trash, "_dir_size", lambda path: walked.append(Path(path).name) or real(path))
    rows = listed()
    assert sorted(x["module"] for x in rows) == ["clip", "storyvideo"]
    assert sorted(walked) == sorted(x["entry"] for x in rows)


def test_entry_whose_spec_id_was_hand_edited_to_an_invalid_one_is_not_restorable(root):
    """归档里的 spec.json 读得出来、但 id 被手改成不合法的（带 /）：恢复接口会拒，列表就别标成能恢复。"""

    pid = create("clip")
    r = client.delete(api("clip", pid))
    assert r.status_code == 200, r.text
    spec_file = root / r.json()["archived_to"] / "spec.json"
    raw = json.loads(spec_file.read_text(encoding="utf-8"))
    raw["id"] = "../evil"
    spec_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    row = listed("clip")[0]
    assert row["restorable"] is False
    assert client.post(f"/api/deleted/clip/{row['entry']}/restore").status_code == 400
