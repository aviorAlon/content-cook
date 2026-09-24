"""发第一个 tag 前的短剧项目状态机与文件边界修复（DRAMA-02/03/07/08/09/10/12/13/14、COMPAT-01/02/11）。

每条都是审计里按接口真实复现过的场景，这里用 TestClient + 临时数据根原样重放：归档要把账本行 / 剧本 /
锚图一起搬走且同 id 重建后能开工、归档能列出能恢复、作废的裁决不算通过、import-legacy 只对未锁定且有产物
的项目开放、建项目的 id 校验、锚图路径边界、错误文案、并发保存互斥、冻结三方核对的提示。
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit.store import RunCollisionError, RunStore
from storyvideo.runtime import StoryVideoRunner, ledger_path
from webui import deps, tasks
from webui import revise as R
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router
from webui.routers import scripts as scripts_router

client = TestClient(app)
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "episodes" / "first_episode.json"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "settings.json").write_text('{"roles": {}}', encoding="utf-8")
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    # 路由模块在 import 时把 deps 的路径常量绑成了自己的全局：每个模块都要 patch（见 test_webui_episodes_router 的说明）
    for module in (deps, episodes_router, post_router, project_router, scripts_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    for module in (deps, episodes_router, scripts_router):
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "DELETED_DIR", tmp_path / ".webui_deleted")
    return tmp_path


def create(episode_id: str):
    return client.post("/api/examples/first_episode/create", json={"episode_id": episode_id})


def prepare(episode_id: str):
    return client.post(f"/api/episodes/storyvideo/{episode_id}/prepare")


def detail(episode_id: str) -> dict:
    r = client.get(f"/api/episodes/storyvideo/{episode_id}")
    assert r.status_code == 200, r.text
    return r.json()


def example_spec() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8-sig"))


def shot_ids() -> list[str]:
    return [s["id"] for s in example_spec()["shots"]]


def png_base64() -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


SCREENPLAY = {"cast": [{"id": "a", "name": "甲", "block": "三十岁"}],
              "scenes": [{"id": "S01", "heading": "办公室 夜 内", "beats": ["她抬头"], "dialogue": [{"who": "a", "zh": "旧项目的剧本"}]}]}


# ---- DRAMA-02 / COMPAT-01 / COMPAT-02：归档带走账本行、剧本、锚图；同 id 重建后能开工 ----


def test_archive_takes_ledger_screenplay_and_assets_so_same_id_can_restart(root):
    assert create("drama-R2").status_code == 200
    assert client.put("/api/episodes/storyvideo/drama-R2/screenplay", json={"screenplay": SCREENPLAY}).status_code == 200
    assets = root / "content" / "assets" / "drama-R2"
    assets.mkdir(parents=True)
    (assets / "lin-abcd.png").write_bytes(b"x")
    assert prepare("drama-R2").status_code == 200
    assert detail("drama-R2")["locked"] is True

    r = client.delete("/api/episodes/storyvideo/drama-R2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["output_moved"] and body["screenplay_moved"] and body["assets_moved"] and body["ledger_archived"]
    archived = root / body["archived_to"]
    assert (archived / "spec.json").is_file() and (archived / "screenplay.json").is_file()
    assert (archived / "assets" / "lin-abcd.png").is_file() and (archived / "output" / "manifests" / "episode.snapshot.json").is_file()
    ledger_copy = json.loads((archived / "ledger.json").read_text(encoding="utf-8"))
    assert ledger_copy["run"]["run_id"] == "drama-R2" and len(ledger_copy["jobs"]) == 2 * len(shot_ids()) + 1
    # 原位什么都不剩：剧本、锚图目录、账本行
    assert not list((root / "content" / "story_video").glob("drama-R2*")) and not assets.exists()
    with RunStore(ledger_path(root), readonly=True) as store:
        assert store.get_run("drama-R2") is None

    # 同 id 重建：是一个干净的草稿，剧本不「继承」，开始制作直接过
    assert create("drama-R2").status_code == 200
    d = detail("drama-R2")
    assert d["locked"] is False and d["editable"] is True and d["has_screenplay"] is False
    assert client.get("/api/episodes/storyvideo/drama-R2/screenplay").status_code == 404
    r = prepare("drama-R2")
    assert r.status_code == 200, r.text


def test_delete_without_ledger_file_still_archives(root):
    assert create("drama-R0").status_code == 200
    r = client.delete("/api/episodes/storyvideo/drama-R0")
    assert r.status_code == 200 and r.json()["ledger_archived"] is False and "warning" not in r.json()


def test_archiving_same_id_twice_within_a_second_keeps_two_archives(root):
    """删 → 重建 → 再删发生在同一秒：两份归档不能混进同一个目录（第二份会覆盖第一份的 spec、output 被套进去）。"""

    assert create("drama-T").status_code == 200
    assert prepare("drama-T").status_code == 200
    first = client.delete("/api/episodes/storyvideo/drama-T").json()["archived_to"]
    assert create("drama-T").status_code == 200
    second = client.delete("/api/episodes/storyvideo/drama-T").json()["archived_to"]
    assert first != second
    assert (root / first / "output" / "manifests" / "episode.snapshot.json").is_file() and (root / first / "ledger.json").is_file()
    assert (root / second / "spec.json").is_file() and not (root / second / "output").exists()
    entries = client.get("/api/deleted", params={"module": "storyvideo"}).json()
    assert {e["archived_to"] for e in entries} == {first, second}
    # 带序号的目录名也能恢复
    r = client.post(f"/api/deleted/storyvideo/{Path(second).name}/restore")
    assert r.status_code == 200 and r.json()["restored"]["output"] is False
    assert detail("drama-T")["locked"] is False


# ---- COMPAT-11：列出 / 恢复归档 ----


def test_list_and_restore_archive_round_trip(root):
    assert create("drama-R5").status_code == 200
    assert client.put("/api/episodes/storyvideo/drama-R5/screenplay", json={"screenplay": SCREENPLAY}).status_code == 200
    assert prepare("drama-R5").status_code == 200
    archived_to = client.delete("/api/episodes/storyvideo/drama-R5").json()["archived_to"]
    entry = Path(archived_to).name

    listed = client.get("/api/deleted").json()
    assert [e["entry"] for e in listed] == [entry]
    assert listed[0]["id"] == "drama-R5" and listed[0]["has_output"] and listed[0]["has_ledger"] and listed[0]["has_screenplay"]
    assert client.get("/api/deleted", params={"module": "clip"}).json() == []

    r = client.post(f"/api/deleted/storyvideo/{entry}/restore")
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == {"spec": True, "screenplay": True, "assets": False, "output": True, "ledger": True}
    assert not (root / archived_to).exists()
    d = detail("drama-R5")
    assert d["locked"] is True and d["has_screenplay"] is True and d["freeze"]["mismatch"] is None
    assert prepare("drama-R5").status_code == 200   # 幂等重放：账本行 + 快照都回来了
    from storyvideo.state import make_step_id

    with RunStore(ledger_path(root), readonly=True) as store:
        assert store.get_run("drama-R5")["status"] == "prepared"
        assert store.get_job("drama-R5", make_step_id("assembly"))["status"] == "prepared"
        assert store.get_job("drama-R5", make_step_id("frames", "S1_OVERTIME"))["status"] == "prepared"

    # 归档已经搬空：再恢复一次 404；目录名不合法（穿越）也是 404
    assert client.post(f"/api/deleted/storyvideo/{entry}/restore").status_code == 404
    assert client.post("/api/deleted/storyvideo/..%2Fx/restore").status_code == 404
    # 其它模块现在也能恢复（回收站）：短剧的归档名拿到单条视频下面是「没有这条归档」；不认识的模块仍是 400
    assert client.post(f"/api/deleted/clip/{entry}/restore").status_code == 404
    assert client.post(f"/api/deleted/oldmodule/{entry}/restore").status_code == 400


def test_restore_refuses_when_same_id_exists(root):
    assert create("drama-R7").status_code == 200
    assert prepare("drama-R7").status_code == 200
    entry = Path(client.delete("/api/episodes/storyvideo/drama-R7").json()["archived_to"]).name
    assert create("drama-R7").status_code == 200
    r = client.post(f"/api/deleted/storyvideo/{entry}/restore")
    assert r.status_code == 409 and "已经存在" in r.json()["detail"]
    # 什么都没动：归档还在，新项目还在
    assert (root / ".webui_deleted" / "storyvideo" / entry / "spec.json").is_file()
    assert detail("drama-R7")["locked"] is False


def test_store_archive_and_restore_run_round_trip(tmp_path):
    with RunStore(tmp_path / "s.sqlite3") as store:
        run_dir = tmp_path / "out" / "EP1"
        store.create_run("EP1", stage_id="s", topic_id="EP1", input_hash="h", plan_hash="p", run_dir=run_dir)
        store.ensure_job("EP1", "frames-S1", "image", "req", Path("01_frames") / "S1.png")
        store.set_run_status("EP1", "awaiting_frames_qc")
        record = store.archive_run("EP1")
        assert record["run"]["status"] == "awaiting_frames_qc" and [j["step_id"] for j in record["jobs"]] == ["frames-S1"]
        assert store.get_run("EP1") is None and store.get_job("EP1", "frames-S1") is None
        assert store.archive_run("EP1") is None
        restored = store.restore_run(record)
        assert restored["status"] == "awaiting_frames_qc"
        assert store.get_job("EP1", "frames-S1")["kind"] == "image"
        with pytest.raises(RunCollisionError):
            store.restore_run(record)


# ---- DRAMA-02(B) / DRAMA-12：输出目录被清空、项目文件被外改 ----


def test_wiped_output_dir_shows_locked_with_reason_and_refuses_edits(root):
    assert create("drama-R3").status_code == 200
    assert prepare("drama-R3").status_code == 200
    shutil.rmtree(root / "output" / "story_video" / "drama-R3")
    d = detail("drama-R3")
    assert d["locked"] is True and d["editable"] is False
    assert d["freeze"] == {"locked": True, "run": True, "snapshot": False, "mismatch": "snapshot_missing"}
    assert "输出目录不在了" in d["readonly_reason"]
    assert any(w.startswith("全集：输出目录不在了") for w in d["warnings"])
    assert client.put("/api/episodes/storyvideo/drama-R3/shots/S1_OVERTIME", json={"dur": 5}).status_code == 409
    r = prepare("drama-R3")
    assert r.status_code == 400 and "之前开始制作过，输出目录已不在" in r.json()["detail"]
    assert "被外部改过" not in r.json()["detail"]
    # 列表 / 概览也按账本算已锁定
    assert next(e for e in client.get("/api/episodes").json() if e["id"] == "drama-R3")["locked"] is True
    s = client.get("/api/episodes/storyvideo/drama-R3/summary").json()
    assert s["locked"] is True and s["freeze"]["mismatch"] == "snapshot_missing" and s["freeze"]["hint"]


def test_external_edit_after_freeze_is_reported_in_detail_and_summary(root):
    assert create("drama-R12").status_code == 200
    assert prepare("drama-R12").status_code == 200
    assert detail("drama-R12")["freeze"]["mismatch"] is None
    path = root / "content" / "story_video" / "drama-R12.json"
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    raw["shots"][1]["art"] = "EXTERNALLY EDITED art"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    d = detail("drama-R12")
    assert d["locked"] is True and d["freeze"]["mismatch"] == "source_changed"
    assert any(w.startswith("全集：项目文件在开始制作之后被外部改过") for w in d["warnings"])
    assert any(c["scope"] == "episode" and "外部改过" in c["impact"] for c in d["problems"])
    s = client.get("/api/episodes/storyvideo/drama-R12/summary").json()
    assert s["freeze"]["mismatch"] == "source_changed" and "外部改过" in s["freeze"]["hint"]


# ---- DRAMA-03：作废（stale）的裁决不算通过 ----


def _write_qc(root: Path, episode_id: str, gates: dict) -> None:
    qc_path = root / "output" / "story_video" / episode_id / "manifests" / "qc.json"
    qc_path.parent.mkdir(parents=True, exist_ok=True)
    qc_path.write_text(json.dumps({"schema_version": 1, "gates": gates}), encoding="utf-8")


def _mark_all_succeeded(root: Path, episode_id: str) -> None:
    from storyvideo.state import make_step_id

    out = root / "output" / "story_video" / episode_id
    with RunStore(ledger_path(root)) as store:
        for sid in shot_ids():
            store.mark_succeeded(episode_id, make_step_id("frames", sid), artifact_path=out / "01_frames" / f"{sid}.png")
            store.mark_succeeded(episode_id, make_step_id("videos", sid), artifact_path=out / "02_video" / f"{sid}.mp4")


def _item(decision: str, *, stale: bool = False) -> dict:
    return {"artifact": "x", "artifact_sha256": "y", "technical_passed": not stale, "probe": {}, "decision": decision,
            "reviewer": "t", "reason": "", "reviewed_at": "t", "stale": stale}


def test_stale_decisions_are_not_counted_as_passed(root):
    assert create("drama-R4").status_code == 200
    assert prepare("drama-R4").status_code == 200
    out = root / "output" / "story_video" / "drama-R4"
    ids = shot_ids()
    for sid in ids:
        (out / "01_frames").mkdir(exist_ok=True)
        (out / "02_video").mkdir(exist_ok=True)
        (out / "01_frames" / f"{sid}.png").write_bytes(b"p")
        (out / "02_video" / f"{sid}.mp4").write_bytes(b"v")
    (out / "drama-R4_final.mp4").write_bytes(b"f")
    # 账本也要是「都生成过」：概览把账本里还是 prepared 的镜算成要重出（重拍重置过的旧文件不算产物），不算待审
    _mark_all_succeeded(root, "drama-R4")
    videos = {sid: _item("pass") for sid in ids}
    videos[ids[-1]] = _item("pass", stale=True)
    _write_qc(root, "drama-R4", {
        "frames": {"status": "accepted", "items": {sid: _item("pass") for sid in ids}},
        "videos": {"status": "not_ready", "items": videos},
        "final": {"status": "not_ready", "items": {"final": _item("pass", stale=True)}},
    })
    d = detail("drama-R4")
    last = next(s for s in d["shots"] if s["id"] == ids[-1])
    assert last["video_qc_decision"] is None and last["qc_meta"]["videos"]["stale"] is True
    assert last["qc_meta"]["videos"]["decision"] == "pass"   # 审计上下文仍在，只是不算通过
    assert next(s for s in d["shots"] if s["id"] == ids[0])["video_qc_decision"] == "pass"
    assert d["final_qc_decision"] is None
    s = client.get("/api/episodes/storyvideo/drama-R4/summary").json()
    assert ids[-1] not in s["videos"]["passed"] and s["videos"]["pending"] == [ids[-1]] and s["videos"]["stale"] == [ids[-1]]
    assert s["next_step"]["key"] == "review_videos" and "已作废" in s["next_step"]["hint"]
    assert s["final"]["decision"] is None


# ---- DRAMA-13：import-legacy 只对未锁定且有本地产物的项目开放 ----


def test_import_legacy_refuses_empty_draft_and_locked_project(root):
    assert create("drama-R6").status_code == 200
    r = client.post("/api/episodes/storyvideo/drama-R6/import-legacy")
    assert r.status_code == 409 and "没有可导入的产物" in r.json()["detail"]
    assert detail("drama-R6")["locked"] is False   # 没被静默锁定
    assert prepare("drama-R6").status_code == 200
    r = client.post("/api/episodes/storyvideo/drama-R6/import-legacy")
    assert r.status_code == 409 and "已经开始制作" in r.json()["detail"]
    assert client.post("/api/episodes/storyvideo/nope/import-legacy").status_code == 404


# ---- DRAMA-07：/api/scripts/save 的 id 校验与 spec.id 一致 ----


def _save(episode_id: str, spec_id: str | None, output_root: str | None = "auto"):
    spec = example_spec()
    if spec_id is None:
        spec.pop("id", None)
    else:
        spec["id"] = spec_id
    if output_root == "auto":
        spec["output_root"] = f"output/story_video/{spec_id or episode_id}"
    elif output_root is None:
        spec.pop("output_root", None)
    else:
        spec["output_root"] = output_root
    return client.post("/api/scripts/save", json={"episode_id": episode_id, "spec": spec})


@pytest.mark.parametrize("bad", ["../escaped", "../../escaped", "   ", "a b/c", "drama/x"])
def test_save_script_rejects_invalid_ids_and_writes_nothing(root, bad):
    before = sorted(str(p) for p in root.rglob("*.json"))
    r = _save(bad, "drama-trav")
    assert r.status_code == 400 and "项目 id 只能用" in r.json()["detail"]
    assert sorted(str(p) for p in root.rglob("*.json")) == before


def test_save_script_requires_spec_id_and_output_root_to_match(root):
    r = _save("drama-mismatch-outer", "drama-mismatch-inner")
    assert r.status_code == 400 and "对不上" in r.json()["detail"]
    r = _save("drama-ok", "drama-ok", output_root="output/story_video/other")
    assert r.status_code == 400 and "output_root" in r.json()["detail"]
    assert not (root / "content" / "story_video" / "drama-ok.json").exists()
    # spec 里没写 id / output_root：按 episode_id 补上
    r = _save("drama-ok", None, output_root=None)
    assert r.status_code == 200, r.text
    raw = json.loads((root / "content" / "story_video" / "drama-ok.json").read_text(encoding="utf-8"))
    assert raw["id"] == "drama-ok" and raw["output_root"] == "output/story_video/drama-ok"
    assert detail("drama-ok")["id"] == "drama-ok"
    assert _save("drama-ok", "drama-ok").status_code == 409


def test_revise_rejects_invalid_new_id(root):
    assert create("drama-RV").status_code == 200
    r = client.post("/api/episodes/storyvideo/drama-RV/revise", json={"new_episode_id": "../drama-RV-v2"})
    assert r.status_code == 400 and not (root / "content" / "drama-RV-v2.json").exists()


def test_create_refuses_id_still_owned_by_ledger(root):
    """项目文件被手动删了、账本行还在（归档没走接口）：建同 id 的项目在入口就 409，而不是建出来之后永远开不了工。"""

    assert create("drama-L").status_code == 200
    assert prepare("drama-L").status_code == 200
    (root / "content" / "story_video" / "drama-L.json").unlink()
    r = create("drama-L")
    assert r.status_code == 409 and "账本里还有旧的制作记录" in r.json()["detail"]
    assert _save("drama-L", "drama-L").status_code == 409


# ---- DRAMA-08：锚图路径只收项目 content/ 之下 ----


def test_cast_asset_paths_are_confined_to_project_content(root):
    assert create("drama-D").status_code == 200
    for bad in ("C:/Windows/win.ini", "../secret.txt", "local:C:/Windows/win.ini", "/etc/passwd", "content/assets/../../x.png"):
        r = client.put("/api/episodes/storyvideo/drama-D/cast/lin", json={"asset": bad})
        assert r.status_code == 400 and "相对路径" in r.json()["detail"], bad
    raw = json.loads((root / "content" / "story_video" / "drama-D.json").read_text(encoding="utf-8-sig"))
    assert "asset" not in raw["cast"]["lin"]
    # 合法上传照常
    up = client.post("/api/episodes/storyvideo/drama-D/cast/lin/asset", json={"filename": "a.png", "data_base64": png_base64()})
    assert up.status_code == 200 and up.json()["asset"].startswith("content/assets/drama-D/")
    assert client.get("/api/episodes/storyvideo/drama-D/cast/lin/asset").status_code == 200
    assert client.put("/api/episodes/storyvideo/drama-D/cast/lin", json={"asset": up.json()["asset"]}).status_code == 200
    # spec 文件里手写绝对路径 / 穿越路径：GET 不再把本机文件发出去。2026-09-23 起归一化本身就拒这种锚图（出首帧时会被
    # 读出来随请求上传，PB-3）：项目打不开、报出是哪个字段、怎么改，而不是悄悄当没有锚图
    secret = root.parent / "drama-D-secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    for bad in (str(secret), "../drama-D-secret.txt", f"local:{secret}"):
        raw = json.loads((root / "content" / "story_video" / "drama-D.json").read_text(encoding="utf-8-sig"))
        raw["cast"]["lin"]["asset"] = bad
        raw["cast"]["lin"]["variants"] = {"rain": {"delta": "雨衣", "asset": bad}}
        (root / "content" / "story_video" / "drama-D.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        r = client.get("/api/episodes/storyvideo/drama-D/cast/lin/asset")
        assert r.status_code == 404 and b"TOP-SECRET" not in r.content, bad
        r = client.get("/api/episodes/storyvideo/drama-D/cast/lin/variants/rain/asset")
        assert r.status_code == 404 and b"TOP-SECRET" not in r.content, bad
        r = client.get("/api/episodes/storyvideo/drama-D/cast")
        assert r.status_code == 404 and "cast.lin.asset" in r.json()["detail"] and "数据目录" in r.json()["detail"], bad


def test_revise_cast_asset_mutation_is_confined(root):
    assert create("drama-DR").status_code == 200
    assert prepare("drama-DR").status_code == 200
    r = client.post("/api/episodes/storyvideo/drama-DR/revise-edit/preview",
                    json={"mutation": {"kind": "cast_asset", "cast_id": "lin", "asset": "C:/Windows/win.ini"}})
    assert r.status_code == 400 and "相对路径" in r.json()["detail"]
    r = client.post("/api/episodes/storyvideo/drama-DR/revise-edit/preview",
                    json={"mutation": {"kind": "cast_edit", "cast_id": "lin", "asset": "../x.png"}})
    assert r.status_code == 400 and "相对路径" in r.json()["detail"]


# ---- DRAMA-09 / DRAMA-10：错误文案 ----


def test_review_errors_are_chinese_and_unknown_gate_leaves_ledger_alone(root):
    assert create("drama-R9").status_code == 200
    assert prepare("drama-R9").status_code == 200
    E = "/api/episodes/storyvideo/drama-R9"
    state = root / "output" / "story_video" / "drama-R9" / "manifests" / "run_state.json"

    r = client.post(f"{E}/review", json={"gate": "bogus", "decision": "pass"})
    assert r.status_code == 400 and "没有这道审片闸门：bogus" in r.json()["detail"]
    assert json.loads(state.read_text(encoding="utf-8"))["run"]["status"] == "prepared"   # 400 不改账本
    r = client.post(f"{E}/review", json={"gate": "frames", "decision": "pass", "shots": ["S1_OVERTIME"]})
    assert r.status_code == 400 and "还没有生成结果，没法审：S1_OVERTIME" in r.json()["detail"]
    assert "产物文件变了" not in r.json()["detail"]
    cases = [
        ({"gate": "frames", "decision": "bogus"}, "不支持的审片裁决：bogus"),
        ({"gate": "final", "decision": "retake", "reason": "x"}, "成片不能直接记重拍"),
        ({"gate": "frames", "decision": "pass", "shots": []}, "没有选中任何镜次"),
        ({"gate": "frames", "decision": "retake", "reason": "x", "disposition": "bogus"}, "重拍处置「bogus」不在可选范围"),
    ]
    for body, expected in cases:
        r = client.post(f"{E}/review", json=body)
        assert r.status_code == 400 and expected in r.json()["detail"], (body, r.text)
        assert "unsupported" not in r.json()["detail"].split("（")[0] and "cannot" not in r.json()["detail"].split("（")[0]
    r = client.put(f"{E}/post-layers", json={"subtitle": {"language": "bilingual"}})
    assert r.status_code == 400 and "双语字幕要求每句台词都有英文：镜 S2_WISH 第 0 句缺 en" in r.json()["detail"]
    for url in ("/api/episodes/storyvideo/nope", "/api/episodes/storyvideo/nope/artifact/frame/S1",
                "/api/episodes/storyvideo/nope/cast", "/api/episodes/storyvideo/nope/summary"):
        r = client.get(url)
        assert r.status_code == 404 and r.json()["detail"] == "没有这个项目：storyvideo/nope", url


def test_humanize_hides_local_paths_for_missing_media():
    from webui.humanize import humanize

    text = humanize("media artifact does not exist: C:\\Users\\someone\\root\\output\\story_video\\EP\\02_video\\S5.mp4")
    assert text == "文件不见了：S5.mp4——重新生成，或重新导入已有产物"
    assert humanize("unsupported retake defect: bogus（可选：a / b）") == "重拍缺陷「bogus」不在可选范围（可选：a / b）"
    assert "项目 id 只能用" in humanize("run_id must be a 1-128 character slug containing only letters, digits, dot, underscore, and hyphen")


def _wait_failed(task_id: str) -> dict:
    for _ in range(100):
        rec = tasks.get(task_id)
        if rec and rec["status"] in ("failed", "succeeded"):
            return rec
        time.sleep(0.02)
    raise AssertionError("task did not finish")


def test_task_failure_is_humanized_and_traceback_stays_out_of_the_api(root):
    class DemoDomainError(RuntimeError):
        pass

    def boom():
        float("abc")

    def domain():
        raise DemoDomainError("还没有可用的剧本文本模型：在「模型设置」里给「剧本文本」选一个来源")

    rec = _wait_failed(tasks.start("t-boom", "EP-X", boom))
    assert rec["error"].startswith("遇到没预料到的错误：ValueError: could not convert string to float") and rec["error_type"] == "ValueError"
    # 类名留在人话里（离了它看不懂），但不再是裸的「类名: 消息」开头；堆栈只进记录的调试字段
    assert not rec["error"].startswith("ValueError:") and "Traceback" in rec["traceback"]
    body = client.get(f"/api/tasks/{rec['task_id']}").json()
    assert "traceback" not in body and body["error"] == rec["error"]
    assert "traceback" in client.get(f"/api/tasks/{rec['task_id']}", params={"debug": "true"}).json()
    assert all("traceback" not in t for t in client.get("/api/tasks", params={"episode_id": "EP-X"}).json())
    rec = _wait_failed(tasks.start("t-domain", "EP-X", domain))
    assert rec["error"] == "还没有可用的剧本文本模型：在「模型设置」里给「剧本文本」选一个来源" and rec["error_type"] == "DemoDomainError"


# ---- DRAMA-14：并发保存互斥 ----


def test_concurrent_shot_edits_second_request_is_409(root):
    assert create("drama-R14").status_code == 200
    path = root / "content" / "story_video" / "drama-R14.json"
    # 直接占着锁 = 另一次保存正在进行
    with deps.spec_write_lock(path):
        r = client.put("/api/episodes/storyvideo/drama-R14/shots/S1_OVERTIME", json={"dur": 6})
        assert r.status_code == 409 and "另一次保存" in r.json()["detail"]
    assert client.put("/api/episodes/storyvideo/drama-R14/shots/S1_OVERTIME", json={"dur": 6}).status_code == 200

    # 真的两个请求：A 在写盘前停住，B 此时进来 → 409；A 继续 → 200，且 A 的改动在盘上
    inside, release = threading.Event(), threading.Event()
    original = episodes_router._validate_and_write

    def slow(path_, mutated, shot_id):
        inside.set()
        assert release.wait(timeout=5)
        return original(path_, mutated, shot_id)

    episodes_router._validate_and_write = slow
    results: dict[str, int] = {}
    try:
        worker = threading.Thread(target=lambda: results.__setitem__(
            "a", client.put("/api/episodes/storyvideo/drama-R14/shots/S1_OVERTIME", json={"dur": 7}).status_code))
        worker.start()
        assert inside.wait(timeout=5)
        results["b"] = client.put("/api/episodes/storyvideo/drama-R14/shots/S1_OVERTIME", json={"motion": "m-b"}).status_code
        release.set()
        worker.join(timeout=5)
    finally:
        episodes_router._validate_and_write = original
    assert results == {"a": 200, "b": 409}
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["shots"][0]["dur"] == 7 and raw["shots"][0]["motion"] != "m-b"


def test_concurrent_revise_apply_with_same_new_id_only_one_succeeds(root):
    assert create("drama-RA").status_code == 200
    assert prepare("drama-RA").status_code == 200
    inside, release = threading.Event(), threading.Event()
    original = R.impact

    def slow(old, new):
        inside.set()
        assert release.wait(timeout=5)
        return original(old, new)

    R.impact = slow
    results: dict[str, int] = {}
    body = {"mutation": {"kind": "shot_edit", "shot_id": "S3_CAT_SPEAKS", "dur": 7}, "new_episode_id": "drama-RA-v2"}
    try:
        worker = threading.Thread(target=lambda: results.__setitem__(
            "a", client.post("/api/episodes/storyvideo/drama-RA/revise-edit/apply", json=body).status_code))
        worker.start()
        assert inside.wait(timeout=5)
        r = client.post("/api/episodes/storyvideo/drama-RA/revise-edit/apply", json={**body, "mutation": {**body["mutation"], "dur": 8}})
        results["b"] = r.status_code
        release.set()
        worker.join(timeout=10)
    finally:
        R.impact = original
    assert results == {"a": 200, "b": 409}
    assert "另一次保存" in r.json()["detail"]
    raw = json.loads((root / "content" / "story_video" / "drama-RA-v2.json").read_text(encoding="utf-8"))
    assert next(s for s in raw["shots"] if s["id"] == "S3_CAT_SPEAKS")["dur"] == 7
    # 顺序再来一次同 id：照旧是「已经存在」
    r = client.post("/api/episodes/storyvideo/drama-RA/revise-edit/apply", json=body)
    assert r.status_code == 400 and "已经存在" in r.json()["detail"]


# ================================================================ 审查后补的回归（第二轮）
# 下面每条对应一条审查意见：删旧版本不能带走新版本的锚图、按老提示手动搬回来之后有出路、「采用候选首帧」
# 不能把被重拍作废的旧视频登记回来、审片 400 不带本机路径、账本孤儿 id 在花钱前拦、冻结对不上时的下一步 / 预估、
# globals 也在保存互斥里。


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_media = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="需要 ffmpeg / ffprobe 造可过技术检查的首帧和视频")


def frame_png(seed: int = 0) -> bytes:
    """过得了技术检查（≥1 KB、ffprobe 认得）的首帧：带噪点，字节随 seed 变。"""

    import random

    from PIL import Image

    rng = random.Random(seed)
    img = Image.new("RGB", (320, 568), (20 + seed % 200, 60, 90))
    px = img.load()
    for x in range(0, 320, 4):
        for y in range(0, 568, 4):
            px[x, y] = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_video(path: Path, seconds: float) -> None:
    import subprocess

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=24",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", str(seconds),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


def write_frames(root: Path, episode_id: str, shots: list[str] | None = None) -> Path:
    out = root / "output" / "story_video" / episode_id
    (out / "01_frames").mkdir(parents=True, exist_ok=True)
    for n, sid in enumerate(shots or shot_ids()):
        (out / "01_frames" / f"{sid}.png").write_bytes(frame_png(n))
    return out


def write_videos(root: Path, episode_id: str) -> Path:
    out = root / "output" / "story_video" / episode_id
    for shot in example_spec()["shots"]:
        make_video(out / "02_video" / f"{shot['id']}.mp4", float(shot["dur"]))
    return out


def ledger_job(root: Path, episode_id: str, stage: str, shot_id: str) -> dict:
    from storyvideo.state import make_step_id

    with RunStore(ledger_path(root), readonly=True) as store:
        return store.get_job(episode_id, make_step_id(stage, shot_id))


# ---- COMPAT-02（审查意见 1）：「复制为新版本」还引用着旧版本的锚图目录时，删旧版本不能把它搬走


def test_delete_keeps_assets_dir_still_referenced_by_revisions(root):
    assert create("drama-R20").status_code == 200
    up = client.post("/api/episodes/storyvideo/drama-R20/cast/lin/asset", json={"filename": "a.png", "data_base64": png_base64()})
    assert up.status_code == 200
    asset_rel = up.json()["asset"]
    assert asset_rel.startswith("content/assets/drama-R20/")
    assert prepare("drama-R20").status_code == 200
    # 草稿态的新版本（revise）与已开始制作的新版本（revise-edit/apply）都还写着 content/assets/drama-R20/…
    assert client.post("/api/episodes/storyvideo/drama-R20/revise", json={"new_episode_id": "drama-R20-v2"}).status_code == 200
    r = client.post("/api/episodes/storyvideo/drama-R20/revise-edit/apply",
                    json={"mutation": {"kind": "shot_edit", "shot_id": "S3_CAT_SPEAKS", "dur": 7}, "new_episode_id": "drama-R20-v3"})
    assert r.status_code == 200, r.text
    assert detail("drama-R20-v3")["locked"] is True

    r = client.delete("/api/episodes/storyvideo/drama-R20")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assets_moved"] is False and body["assets_kept_for"] == ["drama-R20-v2", "drama-R20-v3"]
    assert (root / asset_rel).is_file() and not (root / body["archived_to"] / "assets").exists()
    for vid in ("drama-R20-v2", "drama-R20-v3"):
        lin = next(c for c in client.get(f"/api/episodes/storyvideo/{vid}/cast").json() if c["id"] == "lin")
        assert lin["asset_url"], vid
        assert client.get(f"/api/episodes/storyvideo/{vid}/cast/lin/asset").status_code == 200
    # 已开始制作的新版本：plan 没变，prepare 幂等、冻结三方对得上（以前是「already has different inputs」400）
    r = prepare("drama-R20-v3")
    assert r.status_code == 200, r.text
    assert detail("drama-R20-v3")["freeze"]["mismatch"] is None
    assert prepare("drama-R20-v2").status_code == 200

    # 归档里没有 assets 的条目照常恢复（目录本来就在原位）
    entry = Path(body["archived_to"]).name
    r = client.post(f"/api/deleted/storyvideo/{entry}/restore")
    assert r.status_code == 200 and r.json()["restored"]["assets"] is False
    assert next(c for c in client.get("/api/episodes/storyvideo/drama-R20/cast").json() if c["id"] == "lin")["asset_url"]

    # 没人再引用时照旧整个搬进归档
    assert client.delete("/api/episodes/storyvideo/drama-R20-v2").status_code == 200
    assert client.delete("/api/episodes/storyvideo/drama-R20-v3").status_code == 200
    body = client.delete("/api/episodes/storyvideo/drama-R20").json()
    assert body["assets_moved"] is True and body["assets_kept_for"] == []
    assert not (root / "content" / "assets" / "drama-R20").exists()


# ---- DRAMA-02 / COMPAT-01 / DRAMA-13（审查意见 2）：照老的删除提示把 spec.json / output 手动搬回来之后要有出路


def _delete_then_move_back(root: Path, episode_id: str, *, spec_back: bool = True) -> Path:
    r = client.delete(f"/api/episodes/storyvideo/{episode_id}")
    assert r.status_code == 200 and r.json()["ledger_archived"] is True
    archived = root / r.json()["archived_to"]
    shutil.move(str(archived / "output"), str(root / "output" / "story_video" / episode_id))
    if spec_back:
        shutil.move(str(archived / "spec.json"), str(root / "content" / "story_video" / f"{episode_id}.json"))
    return archived


def test_manual_moveback_shows_ledger_missing_and_restores_the_ledger_rows(root):
    assert create("drama-R31").status_code == 200
    write_frames(root, "drama-R31", ["S1_OVERTIME", "S2_WISH"])
    assert client.post("/api/episodes/storyvideo/drama-R31/import-legacy").status_code == 200
    archived = _delete_then_move_back(root, "drama-R31")
    # manifest.json 是回收站的清单（原路径 → 归档名、删除时间、标题），恢复完跟空目录一起删掉
    assert sorted(p.name for p in archived.iterdir()) == ["ledger.json", "manifest.json"]

    d = detail("drama-R31")
    assert d["locked"] is True and d["needs_import_legacy"] is True
    assert d["freeze"]["mismatch"] == "ledger_missing"
    assert any(w.startswith("全集：账本里没有这个项目的制作记录") for w in d["warnings"])
    s = client.get("/api/episodes/storyvideo/drama-R31/summary").json()
    assert s["next_step"]["key"] == "import_legacy" and s["next_step"]["label"] == "导入已有产物"
    r = client.get("/api/episodes/storyvideo/drama-R31/generate-estimate", params={"through": "frames"})
    assert r.status_code == 409 and "导入已有产物" in r.json()["detail"]

    # 回收站把这条只剩账本记录的归档列成可恢复，恢复只放回账本行
    listed = client.get("/api/deleted", params={"module": "storyvideo"}).json()
    assert [(e["entry"], e["ledger_only"], e["restorable"]) for e in listed] == [(archived.name, True, True)]
    r = client.post(f"/api/deleted/storyvideo/{archived.name}/restore")
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == {"spec": False, "screenplay": False, "assets": False, "output": False, "ledger": True}
    assert not archived.exists()
    d = detail("drama-R31")
    assert d["freeze"]["mismatch"] is None and "needs_import_legacy" not in d
    assert prepare("drama-R31").status_code == 200
    assert ledger_job(root, "drama-R31", "frames", "S1_OVERTIME")["status"] == "succeeded"


def test_manual_moveback_can_reimport_when_ledger_rows_are_gone(root):
    assert create("drama-R32").status_code == 200
    write_frames(root, "drama-R32", ["S1_OVERTIME", "S2_WISH"])
    assert client.post("/api/episodes/storyvideo/drama-R32/import-legacy").status_code == 200
    archived = _delete_then_move_back(root, "drama-R32")
    assert prepare("drama-R32").status_code == 400   # 账本没登记：prepare 要求先导入
    # 以前这里被「已经开始制作」409 拦死；判断改成看账本之后可以重新登记
    r = client.post("/api/episodes/storyvideo/drama-R32/import-legacy")
    assert r.status_code == 200, r.text
    assert prepare("drama-R32").status_code == 200
    assert ledger_job(root, "drama-R32", "frames", "S1_OVERTIME")["status"] == "succeeded"
    assert detail("drama-R32")["freeze"]["mismatch"] is None
    # 重新登记之后，归档里的账本记录就不能再放回去了（同 id 已有 run）
    r = client.post(f"/api/deleted/storyvideo/{archived.name}/restore")
    assert r.status_code == 409 and "已经有同 id 的制作记录" in r.json()["detail"]
    # 账本里有 run 的项目照旧 409（DRAMA-13 的原意不变）
    r = client.post("/api/episodes/storyvideo/drama-R32/import-legacy")
    assert r.status_code == 409 and "已经开始制作" in r.json()["detail"]


def test_ledger_only_restore_requires_the_same_files_back_in_place(root):
    assert create("drama-R33").status_code == 200
    write_frames(root, "drama-R33", ["S1_OVERTIME"])
    assert client.post("/api/episodes/storyvideo/drama-R33/import-legacy").status_code == 200
    archived = _delete_then_move_back(root, "drama-R33", spec_back=False)
    parked = root / "parked-spec.json"
    shutil.move(str(archived / "spec.json"), str(parked))   # 项目文件被挪到了别处：归档里只剩 ledger.json
    r = client.post(f"/api/deleted/storyvideo/{archived.name}/restore")
    assert r.status_code == 409 and "还不在原位" in r.json()["detail"]
    # 搬回来的是改过的内容：不能把旧 run 接上去
    raw = json.loads(parked.read_text(encoding="utf-8-sig"))
    raw["shots"][1]["art"] = "moved back but edited"
    target = root / "content" / "story_video" / "drama-R33.json"
    target.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    r = client.post(f"/api/deleted/storyvideo/{archived.name}/restore")
    assert r.status_code == 409 and "对不上" in r.json()["detail"]
    shutil.copyfile(parked, target)
    r = client.post(f"/api/deleted/storyvideo/{archived.name}/restore")
    assert r.status_code == 200, r.text
    assert prepare("drama-R33").status_code == 200


# ---- DRAMA-13（审查意见 3）：「采用候选首帧」不能把因首帧重拍而重置的视频任务登记回旧视频


@needs_media
def test_compare_adopt_keeps_the_reset_video_job_pending_regeneration(root):
    from storyvideo.spec import load_spec
    from webui import compare as C

    assert create("drama-R13").status_code == 200
    write_frames(root, "drama-R13")
    out = write_videos(root, "drama-R13")
    assert client.post("/api/episodes/storyvideo/drama-R13/import-legacy").status_code == 200
    E = "/api/episodes/storyvideo/drama-R13"
    assert client.post(f"{E}/review", json={"gate": "frames", "decision": "pass"}).status_code == 200
    assert client.post(f"{E}/review", json={"gate": "videos", "decision": "pass"}).status_code == 200
    old_video = (out / "02_video" / "S3_CAT_SPEAKS.mp4").read_bytes()
    old_frame = (out / "01_frames" / "S3_CAT_SPEAKS.png").read_bytes()

    cand = C.compare_dir(load_spec("drama-R13", project_root=root), "S3_CAT_SPEAKS")
    cand.mkdir(parents=True, exist_ok=True)
    (cand / "cand.png").write_bytes(frame_png(99))
    r = client.post(f"{E}/compare/frames/S3_CAT_SPEAKS/adopt", json={"name": "cand.png", "reason": "换个构图"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "awaiting_frames_qc"

    # 新首帧登记了；按旧首帧渲染的旧视频还在盘上，但任务留在 prepared、QC 仍是作废状态——要重出，不是待审
    assert ledger_job(root, "drama-R13", "frames", "S3_CAT_SPEAKS")["status"] == "succeeded"
    assert (out / "01_frames" / "S3_CAT_SPEAKS.png").read_bytes() == frame_png(99)
    # 重拍的留底（硬链接）没被候选图原地覆盖写掉
    assert [p.read_bytes() == old_frame for p in (out / "01_frames" / "takes" / "S3_CAT_SPEAKS").iterdir()] == [True]
    job = ledger_job(root, "drama-R13", "videos", "S3_CAT_SPEAKS")
    assert job["status"] == "prepared" and job["artifact_path"] is None
    assert (out / "02_video" / "S3_CAT_SPEAKS.mp4").read_bytes() == old_video
    qc = json.loads((out / "manifests" / "qc.json").read_text(encoding="utf-8"))
    assert qc["gates"]["videos"]["items"]["S3_CAT_SPEAKS"]["stale"] is True
    assert qc["gates"]["videos"]["items"]["S1_OVERTIME"]["decision"] == "pass"
    assert qc["gates"]["frames"]["items"]["S3_CAT_SPEAKS"]["decision"] is None      # 新候选待审
    assert qc["gates"]["frames"]["items"]["S3_CAT_SPEAKS"]["stale"] is False
    s = client.get(f"{E}/summary").json()
    assert s["frames"]["pending"] == ["S3_CAT_SPEAKS"]
    assert "S3_CAT_SPEAKS" in s["videos"]["missing"] and "S3_CAT_SPEAKS" not in s["videos"]["pending"]
    assert "S3_CAT_SPEAKS" not in s["videos"]["stale"]
    est = client.get(f"{E}/generate-estimate", params={"through": "videos"}).json()
    assert est["pending_count"] == 1
    assert next(i for i in est["items"] if i["shot_id"] == "S3_CAT_SPEAKS")["already_have"] is False
    # 审过首帧之后，下一步是重出这一镜的视频
    assert client.post(f"{E}/review", json={"gate": "frames", "decision": "pass", "shots": ["S3_CAT_SPEAKS"]}).status_code == 200
    s = client.get(f"{E}/summary").json()
    assert s["next_step"]["key"] == "videos" and s["next_step"]["shots"] == ["S3_CAT_SPEAKS"]


@needs_media
def test_reimport_after_video_retake_keeps_the_retake(root):
    """直接调 runner.import_legacy（CLI 同一条路）：被重拍的视频不会被登记回来，重拍裁决和 run 状态都不倒退。"""

    assert create("drama-R13b").status_code == 200
    write_frames(root, "drama-R13b")
    write_videos(root, "drama-R13b")
    assert client.post("/api/episodes/storyvideo/drama-R13b/import-legacy").status_code == 200
    E = "/api/episodes/storyvideo/drama-R13b"
    assert client.post(f"{E}/review", json={"gate": "frames", "decision": "pass"}).status_code == 200
    assert client.post(f"{E}/review", json={"gate": "videos", "decision": "pass"}).status_code == 200
    r = client.post(f"{E}/review", json={"gate": "videos", "decision": "retake", "shots": ["S5_PUNCHLINE"],
                                         "reason": "口型对不上", "disposition": "resubmit"})
    assert r.status_code == 200, r.text
    with StoryVideoRunner(str(root)) as runner:
        result = runner.import_legacy("drama-R13b", confirmed_existing_artifacts=True)
    assert result["status"] == "videos_retake_requested"
    assert ledger_job(root, "drama-R13b", "videos", "S5_PUNCHLINE")["status"] == "prepared"
    qc = json.loads((root / "output" / "story_video" / "drama-R13b" / "manifests" / "qc.json").read_text(encoding="utf-8"))
    assert qc["gates"]["videos"]["items"]["S5_PUNCHLINE"]["decision"] == "retake"
    s = client.get(f"{E}/summary").json()
    assert s["videos"]["retake"] == ["S5_PUNCHLINE"] and s["next_step"]["key"] == "videos"


# ---- DRAMA-09（审查意见 4）：文件不见了时审片 400 不带本机绝对路径


def test_missing_artifact_review_error_has_no_local_path(root):
    assert create("drama-R4p").status_code == 200
    out = write_frames(root, "drama-R4p")
    assert client.post("/api/episodes/storyvideo/drama-R4p/import-legacy").status_code == 200
    (out / "01_frames" / "S1_OVERTIME.png").rename(out / "S1_aside.png")
    r = client.post("/api/episodes/storyvideo/drama-R4p/review", json={"gate": "frames", "decision": "pass", "shots": ["S1_OVERTIME"]})
    assert r.status_code == 400
    text = r.json()["detail"]
    assert "S1_OVERTIME 的文件不见了" in text and "S1_OVERTIME.png" in text
    for leak in (str(root), str(root).replace("\\", "/"), "Errno"):
        assert leak not in text, leak
    reason = json.loads((out / "manifests" / "qc.json").read_text(encoding="utf-8"))["gates"]["frames"]["items"]["S1_OVERTIME"]["invalidation_reason"]
    assert str(root) not in reason and "S1_OVERTIME.png" in reason


# ---- DRAMA-02 fix_plan(a)（审查意见 5）：账本还占着的 id 不再发出去，付费起草 / 出剧本在花钱前 409


def test_orphan_ledger_id_is_skipped_by_next_id_and_refused_before_paid_steps(root):
    x = client.get("/api/episodes/next-id", params={"prefix": "OR"}).json()["id"]
    assert create(x).status_code == 200 and prepare(x).status_code == 200
    (root / "content" / "story_video" / f"{x}.json").unlink()      # 项目文件没了，账本行还在
    assert x in scripts_router.existing_episode_ids()
    assert client.get("/api/episodes/next-id", params={"prefix": "OR"}).json()["id"] != x
    body = {"episode_id": x, "direction": "test", "target_seconds": 30, "camera_profile": "short_drama_vertical"}
    for url in ("/api/scripts/jobs/draft", "/api/scripts/jobs/screenplay"):
        r = client.post(url, json=body)
        assert r.status_code == 409 and "账本里还有旧的制作记录" in r.json()["detail"], url
    r = client.put(f"/api/episodes/storyvideo/{x}/screenplay", json={"screenplay": SCREENPLAY})
    assert r.status_code == 409 and not deps.screenplay_path(x).exists()
    # 项目在的时候改剧本照常（账本里有它自己的 run 天经地义）
    assert create("drama-SP").status_code == 200 and prepare("drama-SP").status_code == 200
    assert client.put("/api/episodes/storyvideo/drama-SP/screenplay", json={"screenplay": SCREENPLAY}).status_code == 200


# ---- DRAMA-12（审查意见 6）：冻结对不上时 next_step 不再指向必然失败的生成，预估直接 409


def test_freeze_mismatch_redirects_next_step_and_estimate(root):
    assert create("drama-R12b").status_code == 200
    assert prepare("drama-R12b").status_code == 200
    path = root / "content" / "story_video" / "drama-R12b.json"
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    raw["shots"][1]["art"] = "EXTERNALLY EDITED art"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    s = client.get("/api/episodes/storyvideo/drama-R12b/summary").json()
    assert s["next_step"]["key"] == "freeze" and s["next_step"]["label"] == "处理版本冲突"
    assert "外部改过" in s["next_step"]["hint"]
    r = client.get("/api/episodes/storyvideo/drama-R12b/generate-estimate", params={"through": "frames"})
    assert r.status_code == 409 and "外部改过" in r.json()["detail"] and "plan_token" not in r.text

    assert create("drama-R3b").status_code == 200 and prepare("drama-R3b").status_code == 200
    shutil.rmtree(root / "output" / "story_video" / "drama-R3b")
    s = client.get("/api/episodes/storyvideo/drama-R3b/summary").json()
    assert s["next_step"]["key"] == "freeze" and "输出目录不在了" in s["next_step"]["hint"]
    assert client.get("/api/episodes/storyvideo/drama-R3b/generate-estimate", params={"through": "videos"}).status_code == 409
    # 一致的项目不受影响
    assert create("drama-OK").status_code == 200 and prepare("drama-OK").status_code == 200
    assert client.get("/api/episodes/storyvideo/drama-OK/summary").json()["next_step"]["key"] == "frames"
    assert client.get("/api/episodes/storyvideo/drama-OK/generate-estimate", params={"through": "frames"}).status_code == 200


# ---- DRAMA-14（审查意见 7）：globals PUT 跟逐镜 PUT 同一把锁


def test_globals_put_is_serialized_with_shot_edits(root):
    assert create("drama-R14g").status_code == 200
    path = root / "content" / "story_video" / "drama-R14g.json"
    with deps.spec_write_lock(path):
        r = client.put("/api/episodes/storyvideo/drama-R14g/globals", json={"values": {"neg": "neg-x"}})
        assert r.status_code == 409 and "另一次保存" in r.json()["detail"]

    inside, release = threading.Event(), threading.Event()
    original = episodes_router._validate_and_write

    def slow(path_, mutated, shot_id):
        inside.set()
        assert release.wait(timeout=5)
        return original(path_, mutated, shot_id)

    episodes_router._validate_and_write = slow
    results: dict[str, int] = {}
    try:
        worker = threading.Thread(target=lambda: results.__setitem__(
            "shot", client.put("/api/episodes/storyvideo/drama-R14g/shots/S1_OVERTIME", json={"motion": "motion-a"}).status_code))
        worker.start()
        assert inside.wait(timeout=5)
        results["globals"] = client.put("/api/episodes/storyvideo/drama-R14g/globals", json={"values": {"neg": "neg-b"}}).status_code
        release.set()
        worker.join(timeout=5)
    finally:
        episodes_router._validate_and_write = original
    assert results == {"shot": 200, "globals": 409}
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["shots"][0]["motion"] == "motion-a" and raw.get("neg") != "neg-b"
    assert client.put("/api/episodes/storyvideo/drama-R14g/globals", json={"values": {"neg": "neg-b"}}).status_code == 200
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["neg"] == "neg-b" and raw["shots"][0]["motion"] == "motion-a"
