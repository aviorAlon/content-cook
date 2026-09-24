"""src/webui/routers/episodes.py：项目读取/逐镜编辑/删除归档(#7)/开新
revision(#8)。之前完全没有测试覆盖（app.py 拆分前更是一个 912 行的单文件）。

路由模块在 import 时把 deps.py 的路径常量（PROJECT_ROOT/STORYVIDEO_DIR）
以及自己算出来的 DELETED_DIR 都绑成了自己的模块级全局——
要在不碰真实 content/story_video 目录的前提下测，得用 monkeypatch 直接改
这个路由模块自己的这几个属性（Python 按名字在调用时查全局，patch 模块属性
对后续调用都生效），不能只 patch deps.py 那边（早就 import 绝了值，patch
deps 不会传导过来）。这样隔离是为了保证测试永远不碰真实数据目录——直接拿真实
项目手动验证报错文案，可能碰坏它的 run_state.json。
"""

import json

import pytest
from fastapi.testclient import TestClient

from webui import deps
from webui.app import app
from webui.routers import episodes as episodes_router

client = TestClient(app)


@pytest.fixture
def isolated_project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    monkeypatch.setattr(episodes_router, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "DELETED_DIR", tmp_path / ".webui_deleted")
    # episodes_router 只是「导入了」deps.py 里的几个函数（resolve_storyvideo_path/
    # is_locked/episode_detail/shot_detail/list_dir 等）——这几个函数的函数体
    # 引用 PROJECT_ROOT 时，Python 按它们「定义所在模块」的全局命名空间查找，
    # 也就是 deps 模块自己的 PROJECT_ROOT，不是 episodes_router 的。只 patch
    # episodes_router.PROJECT_ROOT 拦不住这些函数内部继续读真实项目根目录——
    # 两边都要 patch，这是 Python「函数携带定义处全局作用域」的经典坑。
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    return tmp_path


def valid_shot(shot_id="S1", dur=5, zh="嗨。"):
    return {
        "id": shot_id, "dur": dur, "cast": ["su"],
        "lines": [{"who": "su", "en": "Hi.", "zh": zh}],
        "shot": "wide", "art": "a room", "motion": "su waves",
    }


def write_spec(root, episode_id, **overrides):
    data = {
        "id": episode_id,
        "output_root": str(root / "output" / "story_video" / episode_id),
        "cast": {"su": {"block": "Su"}},
        "shots": [valid_shot("S1", dur=5)],
    }
    data.update(overrides)
    path = root / "content" / "story_video" / f"{episode_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def lock(root, episode_id):
    manifests = root / "output" / "story_video" / episode_id / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "episode.snapshot.json").write_text("{}", encoding="utf-8")


# ---- list_episodes / get_storyvideo_episode ----


def test_list_episodes_survives_one_broken_json(isolated_project):
    write_spec(isolated_project, "GOOD-001")
    (isolated_project / "content" / "story_video" / "BROKEN-002.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    r = client.get("/api/episodes")
    assert r.status_code == 200
    by_id = {e["id"]: e for e in r.json()}
    assert by_id["GOOD-001"]["status"] == "ok"
    assert by_id["BROKEN-002"]["status"] == "error"


def test_get_storyvideo_episode_not_found_is_404(isolated_project):
    r = client.get("/api/episodes/storyvideo/DOES-NOT-EXIST")
    assert r.status_code == 404


def test_get_storyvideo_episode_unlocked_is_editable(isolated_project):
    write_spec(isolated_project, "GOOD-001")
    r = client.get("/api/episodes/storyvideo/GOOD-001")
    assert r.status_code == 200
    body = r.json()
    assert body["locked"] is False
    assert body["editable"] is True
    assert len(body["shots"]) == 1


def test_get_storyvideo_episode_locked_is_not_editable(isolated_project):
    write_spec(isolated_project, "GOOD-001")
    lock(isolated_project, "GOOD-001")
    r = client.get("/api/episodes/storyvideo/GOOD-001")
    body = r.json()
    assert body["locked"] is True
    assert body["editable"] is False


# ---- edit_storyvideo_shot ----


def test_edit_shot_updates_line_and_persists(isolated_project):
    write_spec(isolated_project, "GOOD-001")
    r = client.put(
        "/api/episodes/storyvideo/GOOD-001/shots/S1",
        json={"lines": [{"who": "su", "zh": "新台词"}]},
    )
    assert r.status_code == 200
    assert r.json()["shot"]["lines"][0]["zh"] == "新台词"
    on_disk = json.loads(
        (isolated_project / "content" / "story_video" / "GOOD-001.json").read_text(encoding="utf-8")
    )
    assert on_disk["shots"][0]["lines"][0]["zh"] == "新台词"
    assert on_disk["shots"][0]["lines"][0]["en"] == "Hi."  # 双语字段没被冲掉（回归测试）


def test_edit_shot_on_locked_episode_is_rejected(isolated_project):
    write_spec(isolated_project, "GOOD-001")
    lock(isolated_project, "GOOD-001")
    r = client.put(
        "/api/episodes/storyvideo/GOOD-001/shots/S1",
        json={"lines": [{"who": "su", "zh": "不该改成功"}]},
    )
    assert r.status_code == 409
    on_disk = json.loads(
        (isolated_project / "content" / "story_video" / "GOOD-001.json").read_text(encoding="utf-8")
    )
    assert on_disk["shots"][0]["lines"][0]["zh"] == "嗨。"  # 磁盘内容原封不动


def test_edit_shot_invalid_dur_is_rejected_and_not_written(isolated_project):
    write_spec(isolated_project, "GOOD-001")
    r = client.put("/api/episodes/storyvideo/GOOD-001/shots/S1", json={"dur": 999})
    assert r.status_code == 400
    on_disk = json.loads(
        (isolated_project / "content" / "story_video" / "GOOD-001.json").read_text(encoding="utf-8")
    )
    assert on_disk["shots"][0]["dur"] == 5  # 校验没过，没有写盘


# ---- delete_episode（#7：归档不是真删）----


def test_delete_episode_archives_spec_and_output(isolated_project):
    write_spec(isolated_project, "GOOD-001")
    output_dir = isolated_project / "output" / "story_video" / "GOOD-001"
    output_dir.mkdir(parents=True)
    (output_dir / "01_frames").mkdir()
    (output_dir / "01_frames" / "S1.png").write_bytes(b"fake png")

    r = client.delete("/api/episodes/storyvideo/GOOD-001")
    assert r.status_code == 200
    assert r.json()["output_moved"] is True

    assert not (isolated_project / "content" / "story_video" / "GOOD-001.json").exists()
    assert not output_dir.exists()
    archived = list((isolated_project / ".webui_deleted" / "storyvideo").glob("GOOD-001__*"))
    assert len(archived) == 1
    assert (archived[0] / "spec.json").is_file()
    assert (archived[0] / "output" / "01_frames" / "S1.png").is_file()


def test_delete_episode_without_output_dir_only_archives_spec(isolated_project):
    write_spec(isolated_project, "GOOD-001")
    r = client.delete("/api/episodes/storyvideo/GOOD-001")
    assert r.status_code == 200
    assert r.json()["output_moved"] is False


def test_delete_episode_not_found_is_404(isolated_project):
    r = client.delete("/api/episodes/storyvideo/DOES-NOT-EXIST")
    assert r.status_code == 404


def test_delete_episode_unknown_pipeline_is_404(isolated_project):
    r = client.delete("/api/episodes/not_a_real_pipeline/GOOD-001")
    assert r.status_code == 404


# ---- revise_storyvideo_episode（#8：开新 revision）----


def test_revise_creates_unlocked_copy_with_new_output_root(isolated_project):
    write_spec(isolated_project, "GOOD-001")
    lock(isolated_project, "GOOD-001")

    r = client.post("/api/episodes/storyvideo/GOOD-001/revise", json={"new_episode_id": "GOOD-001-v2"})
    assert r.status_code == 200

    detail = client.get("/api/episodes/storyvideo/GOOD-001-v2").json()
    assert detail["locked"] is False
    assert detail["editable"] is True
    assert detail["shots"][0]["lines"][0]["zh"] == "嗨。"  # 内容照原样复制过来了

    on_disk = json.loads(
        (isolated_project / "content" / "story_video" / "GOOD-001-v2.json").read_text(encoding="utf-8")
    )
    assert on_disk["output_root"] == "output/story_video/GOOD-001-v2"  # 指向全新目录，不跟旧的共享产物


def test_revise_duplicate_new_id_is_409(isolated_project):
    write_spec(isolated_project, "GOOD-001")
    write_spec(isolated_project, "GOOD-001-v2")
    r = client.post("/api/episodes/storyvideo/GOOD-001/revise", json={"new_episode_id": "GOOD-001-v2"})
    assert r.status_code == 409


def test_revise_missing_source_is_404(isolated_project):
    r = client.post("/api/episodes/storyvideo/DOES-NOT-EXIST/revise", json={"new_episode_id": "X"})
    assert r.status_code == 404


def test_final_artifact_route_and_detail_fields(isolated_project):
    """成片入口（2026-09-06）：detail 带 final_url/final_path/final_qc_decision；artifact/final/final 能下载。"""
    import json
    spec = {"id": "EP-F", "output_root": "output/story_video/EP-F", "cast": {"su": {"block": "su"}},
            "shots": [valid_shot()], "subtitle": {"language": "zh"}}
    (isolated_project / "content" / "story_video" / "EP-F.json").write_text(json.dumps(spec), encoding="utf-8")
    r = client.get("/api/episodes/storyvideo/EP-F")
    assert r.status_code == 200 and r.json()["final_url"] is None
    assert client.get("/api/episodes/storyvideo/EP-F/artifact/final/final").status_code == 404
    out = isolated_project / "output" / "story_video" / "EP-F"; out.mkdir(parents=True)
    (out / "EP-F_final.mp4").write_bytes(b"\x00\x00fake")
    body = client.get("/api/episodes/storyvideo/EP-F").json()
    assert body["final_url"].endswith("/artifact/final/final") and body["final_size_bytes"] == 6
    got = client.get("/api/episodes/storyvideo/EP-F/artifact/final/final")
    assert got.status_code == 200 and got.headers["content-type"].startswith("video/mp4")
    assert "EP-F_final.mp4" in got.headers.get("content-disposition", "")


def test_deliverable_prefers_post_over_final(tmp_path):
    # storyvideo post 出的 <id>_post.mp4（旁白/插卡/画面文字已叠）存在时，成片面板与下载都指向它
    from webui.deps import _deliverable_path
    spec = {"id": "EP", "output_root": str(tmp_path)}
    path, kind = _deliverable_path(spec)
    assert kind == "final" and path.name == "EP_final.mp4"
    (tmp_path / "EP_post.mp4").write_bytes(b"x")
    path, kind = _deliverable_path(spec)
    assert kind == "post" and path.name == "EP_post.mp4"
