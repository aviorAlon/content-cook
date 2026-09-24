"""配乐接口（docs/music-spec.md §4）：CRUD、预设、参考图上传、生成任务与 cost_note / 409、采用、投放、
产物流与路径安全、归档、示例复制、目标列表。生成走假 provider（不联网、不花钱）。"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit.providers import TaskSnapshot, TaskState, TaskSubmission, TaskType
from music import spec as S
from webui import deps, tasks
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import music as music_router
from webui.routers import post as post_router

client = TestClient(app)

def _png_bytes(color=(200, 120, 90)) -> bytes:
    """真的 1×1 PNG——上传接口现在按内容验图，手拼的字节串（IDAT 校验和不对）过不了。"""

    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color).save(buf, format="PNG")
    return buf.getvalue()


PNG = _png_bytes()
EPISODE = {
    "id": "EP-MU", "title_zh": "加班夜", "output_root": "output/story_video/EP-MU", "prompt_language": "zh",
    "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
    "cast": {"lin": {"name": "林小满", "block": "黑发。"}},
    "shots": [{"id": "S1", "dur": 4, "cast": ["lin"], "shot": "近景", "art": "a", "motion": "m", "lines": []}],
}
BOOK = {
    "id": "PB-MU", "title_zh": "小狐狸的三只袜子", "age_band": "2-4", "bilingual_mode": "zh_only",
    "characters": {"fox": {"name": "小狐狸", "name_en": "Little Fox", "block": "橘红色小狐狸"}},
    "pages": [{"id": "P01", "kind": "cover", "scene": "小狐狸站在院子里", "characters": ["fox"], "zh": "小狐狸的三只袜子"}],
}
PODCAST = {
    "id": "PC-MU", "title": "为什么我们戒不掉短视频", "format": "duo",
    "hosts": [{"id": "zhou", "name": "老周"}, {"id": "xia", "name": "小夏"}],
    "segments": [{"id": "G01", "kind": "topic", "title": "话题", "lines": [{"who": "zhou", "text": "先说个事。"}]}],
}


class FakeProvider:
    def __init__(self):
        self.requests = []

    def submit_music(self, request):
        self.requests.append(request)
        return TaskSubmission(task_id="url:x", task_type=TaskType.MUSIC, status=TaskState.FINISHED,
                              output_urls=("https://cdn.example.com/a.mp3",))

    def get_task(self, task_id, task_type):
        return TaskSnapshot(task_id=task_id, task_type=TaskType.MUSIC, status=TaskState.FINISHED,
                            output_urls=("https://cdn.example.com/a.mp3",))

    def download(self, url, destination):
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(b"ID3-fake-audio")
        return Path(destination)


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "content" / "music").mkdir(parents=True)
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
    provider = FakeProvider()
    monkeypatch.setattr(music_router, "PROVIDER_FACTORY", lambda: provider)
    # 生成前按「模型设置」里配乐来源的上限检查：设置指到空文件，本机配着的来源不掺进来（要测来源的用例自己再指）
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "no-settings.json"))
    for key in ("ADAPTER", "BASE_URL", "API_KEY", "MODEL", "VOICE"):
        monkeypatch.delenv(f"STORYSTUDIO_MUSIC_{key}", raising=False)
    tasks._running.clear()
    return tmp_path


def _create(**over) -> dict:
    body = {"title": "睡前摇篮曲", "brief": {"usage": "绘本睡前朗读的底噪", "genre": "摇篮曲", "mood": ["温柔"],
                                              "instruments": ["音乐盒"], "bpm": 70, "avoid": ["鼓组"]},
            "duration": 120, "count": 2, **over}
    r = client.post("/api/music", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _wait(task_id: str) -> dict:
    for _ in range(400):
        item = tasks.get(task_id)
        if item and item.get("status") in ("succeeded", "failed"):
            return item
        time.sleep(0.01)
    raise AssertionError("task did not finish")


def _generate(spec_id: str, **body) -> dict:
    r = client.post(f"/api/music/{spec_id}/generate", json=body)
    assert r.status_code == 200, r.text
    task = _wait(r.json()["task_id"])
    assert task["status"] == "succeeded", task.get("error")
    return {**r.json(), "result": task["result"]}


# ---------------------------------------------------------------------------- 列表 / 编号 / 预设 / 新建


def test_empty_list_next_id_and_briefs(project):
    assert client.get("/api/music").json() == []
    assert client.get("/api/music/next-id").json()["id"].startswith("MU-")
    briefs = client.get("/api/music/briefs").json()
    assert 8 <= len(briefs["briefs"]) <= 10
    assert briefs["briefs"][0]["brief"]["usage"]
    assert briefs["limits"]["count"] == [S.COUNT_MIN, S.COUNT_MAX] and briefs["defaults"]["duration"] == S.DEFAULT_DURATION
    assert "lullaby_demo" in briefs["examples"]


def test_create_read_update_and_prompt_preview(project):
    detail = _create()
    spec_id = detail["id"]
    assert spec_id.startswith("MU-") and detail["module"] == "music"
    assert detail["spec"]["count"] == 2 and detail["spec"]["duration"] == 120
    assert detail["prompt"].startswith("用途：绘本睡前朗读的底噪")
    assert detail["prompts"]["en"].startswith("Use: ")
    assert detail["progress"] == {"done": False, "attempts": 0, "ok_attempts": 0, "stale": False, "used": 0, "count": 2}
    assert detail["busy"] == {"generate": None}
    got = client.get(f"/api/music/{spec_id}").json()
    assert got["prompt"] == detail["prompt"]
    body = {**got["spec"], "count": 3, "brief": {**got["spec"]["brief"], "genre": "氛围"}}
    saved = client.put(f"/api/music/{spec_id}", json=body).json()
    assert saved["spec"]["count"] == 3 and "体裁：氛围" in saved["prompt"]
    # id 对不上 / 校验不过
    assert client.put(f"/api/music/{spec_id}", json={**body, "id": "OTHER"}).status_code == 400
    bad = client.put(f"/api/music/{spec_id}", json={**body, "count": 9})
    assert bad.status_code == 400 and f"1 到 {S.COUNT_MAX}" in bad.json()["detail"]
    assert client.get("/api/music/NOPE").status_code == 404
    rows = client.get("/api/music").json()
    assert [r["id"] for r in rows] == [spec_id] and rows[0]["title"] == "睡前摇篮曲"


def test_create_rejects_duplicate_and_bad_id(project):
    spec_id = _create()["id"]
    dup = client.post("/api/music", json={"id": spec_id, "title": "x", "brief": {"usage": "y"}})
    assert dup.status_code == 409 and spec_id in dup.json()["detail"]
    assert client.post("/api/music", json={"id": "不行 的", "title": "x", "brief": {}}).status_code == 400
    assert client.post("/api/music", json={"brief": {}}).status_code == 400   # 没标题


def test_examples_create(project):
    r = client.post("/api/music/examples/create", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["example"] == "lullaby_demo" and body["spec"]["title"] == "睡前摇篮曲"
    assert body["spec"]["count"] == 2 and body["spec"]["duration"] == 120
    assert body["path"].startswith("content/music/")
    assert client.post("/api/music/examples/create", json={"name": "nope"}).status_code == 404
    assert len(client.get("/api/music").json()) == 1


# ---------------------------------------------------------------------------- 参考图


def test_reference_upload_validation(project):
    spec_id = _create()["id"]
    bad = client.post(f"/api/music/{spec_id}/references", json={"filename": "a.webp", "data_base64": base64.b64encode(PNG).decode()})
    assert bad.status_code == 400 and "jpg" in bad.json()["detail"]
    assert client.post(f"/api/music/{spec_id}/references", json={"filename": "a.png", "data_base64": "!!!"}).status_code == 400
    assert client.post(f"/api/music/{spec_id}/references", json={"filename": "a.png", "data_base64": ""}).status_code == 400
    ok = client.post(f"/api/music/{spec_id}/references", json={"filename": "封面 图.png", "data_base64": base64.b64encode(PNG).decode()})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["file"].startswith(f"content/assets/{spec_id}/") and body["file"].endswith(".png")
    assert body["references"] == [{"file": body["file"], "exists": True}]
    assert (project / body["file"]).read_bytes() == PNG
    # 核心上限 S.MAX_REFERENCES 张
    for n in range(S.MAX_REFERENCES - 1):
        assert client.post(f"/api/music/{spec_id}/references",
                           json={"filename": f"r{n}.jpg", "data_base64": base64.b64encode(PNG + str(n).encode()).decode()}).status_code == 200
    over = client.post(f"/api/music/{spec_id}/references", json={"filename": "x.jpg", "data_base64": base64.b64encode(_png_bytes((1, 2, 3))).decode()})
    assert over.status_code == 400 and f"最多 {S.MAX_REFERENCES} 张" in over.json()["detail"]


# ---------------------------------------------------------------------------- 生成 / 采用 / 产物


def test_generate_cost_note_and_tracks(project):
    spec_id = _create()["id"]
    started = client.post(f"/api/music/{spec_id}/generate", json={})
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["count"] == 2 and body["model"] == "@music"
    assert "2 次配乐请求" in body["cost_note"] and "@music" in body["cost_note"]
    task = _wait(body["task_id"])
    assert task["status"] == "succeeded" and task["module"] == "music" and task["kind"] == "music-generate"
    assert len(task["result"]["generated"]) == 2
    detail = client.get(f"/api/music/{spec_id}").json()
    assert detail["progress"]["ok_attempts"] == 2 and detail["progress"]["done"] is True
    assert len(detail["attempts"]) == 2 and all(a["url"] for a in detail["attempts"])
    assert detail["attempts"][0]["current"] is True and detail["attempts"][1]["current"] is False
    assert detail["current_url"].endswith(detail["spec"]["current"])
    # 第二轮：请求没变 → 跳过
    again = _generate(spec_id)
    assert again["result"]["skipped"] and again["result"]["generated"] == []


def test_generate_rejects_bad_count_and_empty_brief(project):
    spec_id = _create(brief={"usage": "", "genre": "", "mood": [], "instruments": [], "avoid": []})["id"]
    empty = client.post(f"/api/music/{spec_id}/generate", json={})
    assert empty.status_code == 400 and "提示词为空" in empty.json()["detail"]
    other = _create()["id"]
    assert client.post(f"/api/music/{other}/generate", json={"count": 9}).status_code == 400


def test_generate_is_busy_once(project, monkeypatch, load_plugin):
    # 走真实的设置检查（PROVIDER_FACTORY=None）：设置指到临时目录，配乐角色选示例插件的来源——
    # 核心不内置配乐来源，不能靠本机 ~/.storystudio/settings.json 里碰巧配着的那个
    assert load_plugin("example_async").status == "loaded"
    home = project / "home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"roles": {"music": {"adapter": "example_async_music"}}}), encoding="utf-8")
    monkeypatch.setenv("STORYSTUDIO_HOME", str(home))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(home / "settings.json"))
    for key in ("ADAPTER", "BASE_URL", "API_KEY", "MODEL", "VOICE"):
        monkeypatch.delenv(f"STORYSTUDIO_MUSIC_{key}", raising=False)
    spec_id = _create()["id"]
    monkeypatch.setattr(music_router, "PROVIDER_FACTORY", None)

    def slow(*_a, **_kw):
        time.sleep(1.0)
        return {"generated": [], "skipped": [], "failed": []}

    monkeypatch.setattr(music_router.G, "generate_music", slow)
    first = client.post(f"/api/music/{spec_id}/generate", json={})
    assert first.status_code == 200
    second = client.post(f"/api/music/{spec_id}/generate", json={})
    assert second.status_code == 409 and "已经在跑" in second.json()["detail"]
    assert client.get(f"/api/music/{spec_id}").json()["busy"]["generate"] == first.json()["task_id"]
    _wait(first.json()["task_id"])


def test_pick_and_artifact_streaming(project):
    spec_id = _create()["id"]
    _generate(spec_id)
    detail = client.get(f"/api/music/{spec_id}").json()
    names = [a["file"] for a in detail["attempts"]]
    assert detail["spec"]["current"] == names[0]
    picked = client.post(f"/api/music/{spec_id}/pick", json={"attempt": names[1]})
    assert picked.status_code == 200 and picked.json()["spec"]["current"] == names[1]
    bad = client.post(f"/api/music/{spec_id}/pick", json={"attempt": "nope.mp3"})
    assert bad.status_code == 400 and "成功出过" in bad.json()["detail"]
    # 产物流
    audio = client.get(f"/api/music/{spec_id}/artifact/audio/{names[0]}")
    assert audio.status_code == 200 and audio.content == b"ID3-fake-audio"
    assert audio.headers["content-type"].startswith("audio/")
    for evil in ("..%2F..%2Fsecret.mp3", "nope.mp3", "..", ".hidden.mp3"):
        assert client.get(f"/api/music/{spec_id}/artifact/audio/{evil}").status_code in (404, 422)
    # 项目根之外的真实文件也拿不到
    (project / "secret.mp3").write_bytes(b"secret")
    assert client.get(f"/api/music/{spec_id}/artifact/audio/secret.mp3").status_code == 404


# ---------------------------------------------------------------------------- 投放


def test_targets_and_use(project):
    targets = client.get("/api/music/targets").json()
    assert targets["modules"] == ["drama", "picturebook", "podcast"]
    assert [r["id"] for r in targets["drama"]] == ["EP-MU"]
    assert [r["id"] for r in targets["picturebook"]] == ["PB-MU"]
    assert targets["podcast"] == [{"module": "podcast", "id": "PC-MU", "title": "为什么我们戒不掉短视频", "tracks": 0, "slots": []}]
    spec_id = _create()["id"]
    _generate(spec_id)
    r = client.post(f"/api/music/{spec_id}/use", json={"module": "drama", "target": "EP-MU", "slot": 12})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_title"] == "加班夜" and "加班夜" in body["message"] and "配乐层" in body["message"]
    assert body["file"].startswith("content/assets/EP-MU/music/")
    episode = json.loads((project / "content" / "story_video" / "EP-MU.json").read_text(encoding="utf-8"))
    assert episode["post_graphics"]["music"][0]["start"] == 12.0
    assert body["detail"]["used_in"][0]["id"] == "EP-MU"
    # 绘本
    r2 = client.post(f"/api/music/{spec_id}/use", json={"module": "picturebook", "target": "PB-MU"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["target_title"] == "小狐狸的三只袜子"
    book = json.loads((project / "content" / "picturebook" / "PB-MU.json").read_text(encoding="utf-8"))
    assert book["music"]["file"].startswith("content/assets/PB-MU/music/")
    assert len(client.get(f"/api/music/{spec_id}").json()["used_in"]) == 2
    assert client.get("/api/music").json()[0]["progress"]["used"] == 2
    # 播客：slot 是槽名；提示按槽说话；不给槽 = 垫乐
    r3 = client.post(f"/api/music/{spec_id}/use", json={"module": "podcast", "target": "PC-MU", "slot": "intro"})
    assert r3.status_code == 200, r3.text
    body3 = r3.json()
    assert body3["target_title"] == "为什么我们戒不掉短视频" and body3["slot"] == "intro" and body3["slot_label"] == "片头"
    assert body3["message"] == "已放进《为什么我们戒不掉短视频》的片头"
    assert body3["file"].startswith("content/assets/PC-MU/music/") and body3["tracks"] == 1
    r4 = client.post(f"/api/music/{spec_id}/use", json={"module": "podcast", "target": "PC-MU"})
    assert r4.status_code == 200, r4.text
    assert r4.json()["slot"] == "bed" and r4.json()["message"] == "已放进《为什么我们戒不掉短视频》的垫乐" and r4.json()["tracks"] == 2
    podcast = json.loads((project / "content" / "podcast" / "PC-MU.json").read_text(encoding="utf-8"))
    assert podcast["music"]["intro"] == body3["file"] and podcast["music"]["bed"] == r4.json()["file"] and podcast["music"]["outro"] == ""
    assert podcast["music"]["bed_gain"] == 0.18   # 归一化补的默认值，说明走的是 podcast.spec 那条路
    used = client.get(f"/api/music/{spec_id}").json()["used_in"]
    assert [(u["module"], u["slot"]) for u in used[2:]] == [("podcast", "intro"), ("podcast", "bed")]
    after = client.get("/api/music/targets").json()["podcast"][0]
    assert after["tracks"] == 2 and after["slots"] == ["intro", "bed"]


def test_use_errors(project):
    spec_id = _create()["id"]
    nothing = client.post(f"/api/music/{spec_id}/use", json={"module": "drama", "target": "EP-MU"})
    assert nothing.status_code == 400 and "还没有采用" in nothing.json()["detail"]
    _generate(spec_id)
    missing = client.post(f"/api/music/{spec_id}/use", json={"module": "drama", "target": "NOPE"})
    assert missing.status_code == 404
    assert client.post(f"/api/music/{spec_id}/use", json={"module": "picturebook", "target": "NOPE"}).status_code == 404
    assert client.post(f"/api/music/{spec_id}/use", json={"module": "sticker", "target": "ST-1"}).status_code == 400
    assert client.post(f"/api/music/{spec_id}/use", json={"module": "drama", "target": "EP-MU", "slot": 99999}).status_code == 400
    # 播客：不存在 404；槽名不对 400（人话里列出三个槽），也不接受短剧那种秒数
    gone = client.post(f"/api/music/{spec_id}/use", json={"module": "podcast", "target": "NOPE"})
    assert gone.status_code == 404 and gone.json()["detail"] == "没有这期播客：NOPE"
    bad = client.post(f"/api/music/{spec_id}/use", json={"module": "podcast", "target": "PC-MU", "slot": "loop"})
    assert bad.status_code == 400 and "槽名" in bad.json()["detail"] and "bed（垫乐）" in bad.json()["detail"] and "「loop」" in bad.json()["detail"]
    assert client.post(f"/api/music/{spec_id}/use", json={"module": "podcast", "target": "PC-MU", "slot": 12}).status_code == 400
    assert client.get(f"/api/music/{spec_id}").json()["used_in"] == []


# ---------------------------------------------------------------------------- 归档


def test_archive_moves_and_disappears_from_the_list(project):
    spec_id = _create()["id"]
    _generate(spec_id)
    r = client.delete(f"/api/music/{spec_id}")
    assert r.status_code == 200 and r.json()["output_moved"] is True
    assert r.json()["archived_to"].startswith(".webui_deleted/music/")
    assert client.get("/api/music").json() == []
    assert client.get(f"/api/music/{spec_id}").status_code == 404
    assert client.delete(f"/api/music/{spec_id}").status_code == 404


def test_broken_project_file_does_not_break_the_list(project):
    _create()
    (project / "content" / "music" / "BAD.json").write_text("{not json", encoding="utf-8")
    rows = client.get("/api/music").json()
    assert len(rows) == 2
    broken = next(r for r in rows if r["id"] == "BAD")
    assert broken["status"] == "error" and broken["error"]
