"""播客接口（docs/podcast-spec.md §5）：列表 / 编号 / 形式 / 新建 / 示例 / 详情（估算 + 问题卡 + 配音状态）/ 保存校验 /
起草任务（假模型）与 400 / 409 / 只重写一段 / 合成任务（假 TTS + 假 runner）/ 音乐上传 / 产物流与路径安全 / 归档。"""

from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from podcast import spec as S
from webui import deps, tasks
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import podcast as podcast_router

client = TestClient(app)
MP3 = b"ID3" + b"\x00" * 64
API = "/api/podcast"


def fake_synth(profile, text, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(MP3 + text.encode("utf-8"))


def fake_prober(path: Path) -> float:
    return round(0.5 + len(path.read_bytes()) / 100.0, 3)


def fake_runner(command):
    Path(command[-1]).write_bytes(MP3)


@pytest.fixture
def project(tmp_path, monkeypatch):
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", tmp_path / "content" / "story_video")
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", tmp_path / "content" / "story_video")
    monkeypatch.setattr(tasks, "tasks_dir", lambda: tmp_path / ".webui_tasks")
    monkeypatch.setattr(podcast_router, "SYNTHESIZER", fake_synth)
    monkeypatch.setattr(podcast_router, "PROBER", fake_prober)
    monkeypatch.setattr(podcast_router, "RUNNER", fake_runner)
    monkeypatch.setattr(podcast_router, "FFMPEG", "ffmpeg")
    monkeypatch.setattr(podcast_router, "VOICE_LOOKUP", lambda vid: {"id": vid, "name": "库里的", "engine": "edge", "voice": "zh-CN-XiaoyiNeural"} if vid == "lib" else None)
    monkeypatch.setattr(podcast_router, "DISPATCH", None)
    return tmp_path


def wait(task_id: str, seconds: float = 10.0) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        task = tasks.get(task_id)
        if task and task["status"] in ("succeeded", "failed"):
            return task
        time.sleep(0.02)
    raise AssertionError("task did not finish")


def body(**extra) -> dict:
    data = {
        "title": "测试期", "format": "duo", "target_minutes": 2,
        "brief": {"topic": "t", "takeaways": ["a", "b"], "materials": "m"},
        "hosts": [{"id": "a", "name": "阿一", "persona": "慢"}, {"id": "b", "name": "阿二", "voice": "lib"}],
        "segments": [{"id": "G01", "kind": "cold_open", "lines": [{"who": "a", "text": "上周我刷了 3 小时。"}, {"who": "b", "text": "不对吧。"}]}],
    }
    data.update(extra)
    return data


def test_list_next_id_and_formats(project):
    assert client.get(API).json() == []
    assert client.get(f"{API}/next-id").json()["id"].startswith("PC-")
    formats = client.get(f"{API}/formats").json()
    assert [f["id"] for f in formats["formats"]] == ["solo", "duo", "interview", "roundtable"]
    assert formats["kinds"][0] == {"id": "cold_open", "name": "冷开场"} and "停顿" in formats["cues"]
    assert len(formats["craft"]) == 8 and formats["examples"] == ["short_video_demo"]
    assert formats["defaults"]["target_minutes"] == 12 and formats["limits"]["hosts"] == [1, 4]


def test_create_detail_update_and_validation(project):
    created = client.post(API, json=body()).json()
    pid = created["id"]
    assert pid.startswith("PC-") and created["module"] == "podcast"
    assert created["estimate"]["lines"] == 2 and created["progress"] == {"drafted": True, "audio": False, "mixed": False}
    assert created["hosts"][1]["voice_label"] == "库里的" and created["hosts"][1]["placeholder"] is False
    assert created["hosts"][0]["placeholder"] is True
    assert created["audio"]["total"] == 2 and created["audio"]["stale"] is True and created["audio"]["mix_url"] is None
    assert any(p["kind"] == "podcast" for p in created["problems"])
    assert (project / "content" / "podcast" / f"{pid}.json").is_file()
    rows = client.get(API).json()
    assert rows[0]["id"] == pid and rows[0]["format_name"] == "双人对谈" and rows[0]["hosts"] == 2 and rows[0]["lines"] == 2
    detail = client.get(f"{API}/{pid}").json()
    assert detail["spec"]["segments"][0]["lines"][1]["who"] == "b"
    bad = client.put(f"{API}/{pid}", json={**detail["spec"], "segments": [{"lines": [{"who": "ghost", "text": "x"}]}]})
    assert bad.status_code == 400 and bad.json()["detail"].startswith("改动没通过校验，没有写盘：")
    mismatch = client.put(f"{API}/{pid}", json={**detail["spec"], "id": "OTHER"})
    assert mismatch.status_code == 400
    ok = client.put(f"{API}/{pid}", json={**detail["spec"], "title": "改名"})
    assert ok.status_code == 200 and ok.json()["title"] == "改名"
    assert client.get(f"{API}/NOPE").status_code == 404
    dup = client.post(API, json={**body(), "id": pid})
    assert dup.status_code == 409
    assert client.post(API, json={**body(), "id": "bad id"}).status_code == 400


def test_example_copy_is_clean_except_voices(project):
    r = client.post(f"{API}/examples/create", json={"name": "short_video_demo"})
    assert r.status_code == 200
    data = r.json()
    assert data["example"] == "short_video_demo" and data["estimate"]["lines"] == 41
    assert [w for w in data["warnings"] if "音色" not in w] == []
    assert client.post(f"{API}/examples/create", json={"name": "nope"}).status_code == 404


def test_draft_task_uses_fake_model_and_rejects_without_hosts_or_model(project, monkeypatch):
    pid = client.post(API, json=body(segments=[])).json()["id"]
    monkeypatch.setattr(podcast_router, "DISPATCH", None)
    from storyvideo import scriptgen

    monkeypatch.setattr(scriptgen, "scriptgen_backend", lambda: "unconfigured")   # 真函数没配时返回的就是这个值，不是 ""
    assert client.post(f"{API}/{pid}/draft", json={}).status_code == 400
    calls = []

    def dispatch(system_prompt, user_prompt, *, json_schema):
        calls.append((system_prompt, user_prompt))
        return ({"segments": [
            {"id": "G01", "kind": "cold_open", "title": "冷", "lines": [{"who": "a", "text": "两点十七。"}]},
            {"id": "G02", "kind": "intro", "title": "开", "lines": [{"who": "阿二", "text": "我是阿二。"}]},
        ]}, None, 99, "settings")

    monkeypatch.setattr(podcast_router, "DISPATCH", dispatch)
    r = client.post(f"{API}/{pid}/draft", json={"note": "短一点"})
    assert r.status_code == 200 and r.json()["cost_note"] == "发 1 次文本模型请求"
    task = wait(r.json()["task_id"])
    assert task["status"] == "succeeded" and task["module"] == "podcast" and task["kind"] == "pc-draft"
    assert task["result"]["lines"] == 2 and "短一点" in calls[0][1]
    detail = client.get(f"{API}/{pid}").json()
    assert detail["spec"]["segments"][0]["lines"][0]["text"] == "两点十七。" and detail["spec"]["segments"][1]["lines"][0]["who"] == "b"
    assert detail["draft"]["backend"] == "settings" and detail["draft"]["usage_tokens"] == 99
    # 只重写一段
    def redraft(system_prompt, user_prompt, *, json_schema):
        assert "【只重写这个环节，其余环节原样不动】" in user_prompt
        return ({"segments": [{"id": "G02", "kind": "intro", "title": "开", "lines": [{"who": "b", "text": "重写的开场。"}]}]}, None, 1, "settings")

    monkeypatch.setattr(podcast_router, "DISPATCH", redraft)
    r = client.post(f"{API}/{pid}/segments/G02/redraft", json={"note": "更短"})
    assert r.status_code == 200 and wait(r.json()["task_id"])["status"] == "succeeded"
    detail = client.get(f"{API}/{pid}").json()
    assert detail["spec"]["segments"][1]["lines"][0]["text"] == "重写的开场。" and detail["spec"]["segments"][0]["lines"][0]["text"] == "两点十七。"
    assert client.post(f"{API}/{pid}/segments/G99/redraft", json={"note": ""}).status_code == 404
    # 没有主播
    empty = client.post(API, json={"title": "无人", "format": "duo", "hosts": [], "segments": []}).json()["id"]
    assert client.post(f"{API}/{empty}/draft", json={}).status_code == 400
    # 模型不按结构返回 → 任务失败，稿子不动
    monkeypatch.setattr(podcast_router, "DISPATCH", lambda *a, **k: ({"nope": 1}, None, 0, "settings"))
    task = wait(client.post(f"{API}/{pid}/draft", json={}).json()["task_id"])
    assert task["status"] == "failed" and "缺 segments" in task["error"]


def test_draft_is_busy_once(project, monkeypatch):
    pid = client.post(API, json=body()).json()["id"]

    def slow(*a, **k):
        time.sleep(0.4)
        return ({"segments": [{"id": "G01", "kind": "cold_open", "title": "", "lines": [{"who": "a", "text": "x"}]}]}, None, 0, "settings")

    monkeypatch.setattr(podcast_router, "DISPATCH", slow)
    first = client.post(f"{API}/{pid}/draft", json={})
    assert first.status_code == 200
    assert client.post(f"{API}/{pid}/draft", json={}).status_code == 409
    assert client.get(f"{API}/{pid}").json()["busy"]["draft"] == first.json()["task_id"]
    wait(first.json()["task_id"])


def test_synthesize_task_artifacts_and_text_files(project):
    pid = client.post(API, json=body()).json()["id"]
    r = client.post(f"{API}/{pid}/synthesize", json={})
    assert r.status_code == 200 and r.json()["lines"] == 2 and "edge-tts（免费）" in r.json()["cost_note"]
    task = wait(r.json()["task_id"])
    assert task["status"] == "succeeded" and task["kind"] == "pc-audio"
    assert task["result"]["mixed"] is True and task["result"]["synthesized"] == ["G01_01.mp3", "G01_02.mp3"]
    detail = client.get(f"{API}/{pid}").json()
    audio = detail["audio"]
    assert audio["stale"] is False and audio["mixed"] and audio["mix_url"] == f"{API}/{pid}/artifact/audio/{pid}.mp3"
    assert audio["lines"][0]["url"] == f"{API}/{pid}/artifact/audio/G01_01.mp3" and audio["chapters"][0]["id"] == "G01"
    assert detail["progress"] == {"drafted": True, "audio": True, "mixed": True}
    assert detail["texts"]["shownotes_url"] == f"{API}/{pid}/artifact/text/shownotes.md"
    assert client.get(audio["mix_url"]).status_code == 200
    line = client.get(audio["lines"][0]["url"])
    assert line.status_code == 200 and line.headers["content-type"].startswith("audio/mpeg")
    notes = client.get(detail["texts"]["shownotes_url"])
    assert notes.status_code == 200 and notes.text.startswith("# 测试期")
    assert client.get(detail["texts"]["transcript_url"]).text.startswith("## [00:00]")
    for bad in ("../x.mp3", "G09_01.mp3", "notes.txt", ".hidden.mp3"):
        assert client.get(f"{API}/{pid}/artifact/audio/{bad}").status_code == 404
    assert client.get(f"{API}/{pid}/artifact/text/secret.json").status_code == 404
    # 稿子改过 → 过时
    spec = detail["spec"]
    spec["segments"][0]["lines"][0]["text"] = "改过了。"
    client.put(f"{API}/{pid}", json=spec)
    again = client.get(f"{API}/{pid}").json()
    assert again["audio"]["stale"] is True and again["progress"]["mixed"] is False and again["audio"]["lines"][0]["stale"] is True


def test_synthesize_rejects_empty_script_and_missing_ffmpeg_is_reported(project, monkeypatch):
    pid = client.post(API, json=body(segments=[])).json()["id"]
    assert client.post(f"{API}/{pid}/synthesize", json={}).status_code == 400
    pid = client.post(API, json=body()).json()["id"]
    monkeypatch.setattr(podcast_router, "FFMPEG", None)
    monkeypatch.setattr(podcast_router.A.shutil, "which", lambda _n: None)
    task = wait(client.post(f"{API}/{pid}/synthesize", json={}).json()["task_id"])
    assert task["status"] == "succeeded" and task["result"]["mixed"] is False and "ffmpeg" in task["result"]["mix_error"]
    detail = client.get(f"{API}/{pid}").json()
    assert detail["audio"]["ffmpeg"] is False and detail["audio"]["synthesized"] == 2 and detail["progress"]["audio"] is True


def test_asset_upload_validation_and_music_slot(project):
    pid = client.post(API, json=body()).json()["id"]
    data = base64.b64encode(MP3).decode()
    r = client.post(f"{API}/{pid}/assets", json={"filename": "bed.mp3", "data_base64": data, "slot": "bed"})
    assert r.status_code == 200
    assert r.json()["file"].startswith(f"content/assets/{pid}/bed-") and r.json()["music"]["bed"] == r.json()["file"]
    assert (project / r.json()["file"]).is_file()
    # 按内容认、不按扩展名拒（发布前加固）：图片字节不管叫什么都 400；mp3 字节叫 .png 照收，落盘后缀按内容是 .mp3
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()
    assert client.post(f"{API}/{pid}/assets", json={"filename": "x.png", "data_base64": png, "slot": "bed"}).status_code == 400
    misnamed = client.post(f"{API}/{pid}/assets", json={"filename": "x.png", "data_base64": data, "slot": "intro"})
    assert misnamed.status_code == 200 and misnamed.json()["file"].endswith(".mp3")
    assert client.post(f"{API}/{pid}/assets", json={"filename": "x.mp3", "data_base64": data, "slot": "loop"}).status_code == 400
    assert client.post(f"{API}/{pid}/assets", json={"filename": "x.mp3", "data_base64": "!!", "slot": "intro"}).status_code == 400
    assert client.post(f"{API}/{pid}/assets", json={"filename": "x.mp3", "data_base64": "", "slot": "intro"}).status_code == 400


def test_archive_and_broken_file_in_list(project):
    pid = client.post(API, json=body()).json()["id"]
    r = client.delete(f"{API}/{pid}")
    assert r.status_code == 200 and r.json()["archived_to"].startswith(".webui_deleted/podcast/")
    assert client.get(API).json() == [] and client.delete(f"{API}/{pid}").status_code == 404
    folder = project / "content" / "podcast"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "PC-BAD.json").write_text("{", encoding="utf-8")
    rows = client.get(API).json()
    assert rows[0]["id"] == "PC-BAD" and rows[0]["status"] == "error"
    # 坏掉的项目文件也得能删（不走归一化）
    assert client.delete(f"{API}/PC-BAD").status_code == 200


# ---------------------------------------------------------------------------- 2026-09-22 发第一个 tag 前的审计修复


def synthesized(pid: str, **body) -> dict:
    task = wait(client.post(f"{API}/{pid}/synthesize", json=body).json()["task_id"])
    assert task["status"] == "succeeded", task.get("error")
    return task["result"]


def test_zero_byte_line_is_reported_stale_and_only_it_is_resynthesized(project):
    """PC-03：0 字节的逐句 mp3 → 详情少算一句、stale、逐句接口 404；不带 force 再合成只重做那一句。"""

    pid = client.post(API, json=body()).json()["id"]
    assert synthesized(pid)["mixed"] is True
    line = project / "output" / "podcast" / pid / "07_audio" / "G01_01.mp3"
    line.write_bytes(b"")
    detail = client.get(f"{API}/{pid}").json()
    audio = detail["audio"]
    assert audio["synthesized"] == 1 and audio["total"] == 2 and audio["stale"] is True
    assert audio["lines"][0]["file"] is None and audio["lines"][0]["url"] is None and audio["lines"][0]["stale"] is True
    assert audio["lines"][1]["stale"] is False
    assert detail["progress"] == {"drafted": True, "audio": False, "mixed": False}
    r = client.get(f"{API}/{pid}/artifact/audio/G01_01.mp3")
    assert r.status_code == 404 and "是空的" in r.json()["detail"]
    result = synthesized(pid)
    assert result["synthesized"] == ["G01_01.mp3"] and result["skipped"] == ["G01_02.mp3"] and result["mixed"] is True
    after = client.get(f"{API}/{pid}").json()
    assert after["audio"]["synthesized"] == 2 and after["audio"]["stale"] is False and after["progress"]["mixed"] is True
    assert client.get(f"{API}/{pid}/artifact/audio/G01_01.mp3").status_code == 200


def test_mix_freshness_pause_music_change_and_failed_mix(project, monkeypatch):
    """PC-04：只改停顿 / 垫乐音量 / 换音乐 → audio.mix_stale=true、progress.mixed=false（成品仍可试听）；重混一次恢复；
    混音失败后旧成品不再冒充新成品。"""

    pid = client.post(API, json=body()).json()["id"]
    assert synthesized(pid)["mixed"] is True
    detail = client.get(f"{API}/{pid}").json()
    assert detail["audio"]["mix_stale"] is False and detail["progress"]["mixed"] is True
    old_duration = detail["audio"]["mix_duration"]
    spec = detail["spec"]
    spec["pause"]["line"] = 2.0
    changed = client.put(f"{API}/{pid}", json=spec).json()
    assert changed["audio"]["stale"] is False and changed["audio"]["mixed"] is True and changed["audio"]["mix_stale"] is True
    assert changed["audio"]["mix_url"] == f"{API}/{pid}/artifact/audio/{pid}.mp3" and changed["audio"]["mix_duration"] == old_duration
    assert changed["progress"] == {"drafted": True, "audio": True, "mixed": False}
    assert [row["progress"]["mixed"] for row in client.get(API).json()] == [False]
    result = synthesized(pid)
    assert result["skipped"] == ["G01_01.mp3", "G01_02.mp3"] and result["mixed"] is True and result["duration"] > old_duration
    fresh = client.get(f"{API}/{pid}").json()
    assert fresh["audio"]["mix_stale"] is False and fresh["progress"]["mixed"] is True
    # 没挂垫乐时只改垫乐音量：ffmpeg 命令里根本没有垫乐，成品没变，不算过时
    spec = fresh["spec"]
    spec["music"]["bed_gain"] = 0.3
    no_bed = client.put(f"{API}/{pid}", json=spec).json()
    assert no_bed["audio"]["mix_stale"] is False and no_bed["progress"]["mixed"] is True
    # 上传垫乐（探得出）→ 过时；重混后再只改垫乐音量 → 过时
    r = client.post(f"{API}/{pid}/assets", json={"filename": "bed.mp3", "data_base64": base64.b64encode(MP3 + b"bed").decode(), "slot": "bed"})
    assert r.status_code == 200, r.text
    assert client.get(f"{API}/{pid}").json()["audio"]["mix_stale"] is True
    assert synthesized(pid)["mixed"] is True
    spec = client.get(f"{API}/{pid}").json()["spec"]
    spec["music"]["bed_gain"] = 0.5
    tuned = client.put(f"{API}/{pid}", json=spec).json()
    assert tuned["audio"]["mix_stale"] is True and tuned["progress"]["mixed"] is False
    # 再让探测器读不出垫乐 → 混音失败，任务说没混音，详情也不再说已混音
    monkeypatch.setattr(podcast_router, "PROBER", lambda p: (_ for _ in ()).throw(RuntimeError("Invalid argument")) if "bed-" in p.name else fake_prober(p))
    task_id = client.post(f"{API}/{pid}/synthesize", json={}).json()["task_id"]
    failed = wait(task_id)["result"]
    assert failed["mixed"] is False and failed["mix_error"].startswith("垫乐音乐文件读不出（bed-")
    broken = client.get(f"{API}/{pid}").json()
    assert broken["audio"]["mix_stale"] is True and broken["progress"]["mixed"] is False
    # 英文界面：任务结果里「没混音」的原因（mix_error，前端直接 toast）也翻
    en = client.get(f"/api/tasks/{task_id}", headers={"X-StoryStudio-Lang": "en"}).json()
    assert en["result"]["mix_error"].startswith("The bed music file could not be read (bed-"), en["result"]["mix_error"]


def test_upload_rejects_unreadable_audio_and_leaves_no_file_behind(project, monkeypatch):
    """PC-05：上传时探一次时长，探不出 400、不落盘、不进 music 槽；探得出照常。"""

    pid = client.post(API, json=body()).json()["id"]

    def prober(path: Path) -> float:
        data = path.read_bytes()
        if b"junk" in data:
            raise RuntimeError("Failed to read frame size: Could not seek to 1039.\nfake.mp3: Invalid argument")
        if b"silent" in data:
            return 0.0
        return 1.0

    monkeypatch.setattr(podcast_router, "PROBER", prober)
    bad = client.post(f"{API}/{pid}/assets", json={"filename": "fake.mp3", "data_base64": base64.b64encode(b"not really audio junk").decode(), "slot": "intro"})
    assert bad.status_code == 400
    assert bad.json()["detail"] == ("上传的文件不是能读的音频（fake.mp3）：Failed to read frame size: Could not seek to 1039. fake.mp3: Invalid argument"
                                    "——换一个 mp3 / wav / m4a 试试")
    zero = client.post(f"{API}/{pid}/assets", json={"filename": "silent.mp3", "data_base64": base64.b64encode(MP3 + b"silent").decode(), "slot": "intro"})
    assert zero.status_code == 400 and "时长为 0" in zero.json()["detail"]
    folder = project / "content" / "assets" / pid
    assert not folder.exists() or list(folder.iterdir()) == []
    assert client.get(f"{API}/{pid}").json()["spec"]["music"]["intro"] == ""
    ok = client.post(f"{API}/{pid}/assets", json={"filename": "intro.mp3", "data_base64": base64.b64encode(MP3).decode(), "slot": "intro"})
    assert ok.status_code == 200 and (project / ok.json()["file"]).is_file() and not list(folder.glob(".*uploading*"))
    # 英文界面
    en = client.post(f"{API}/{pid}/assets", json={"filename": "fake.mp3", "data_base64": base64.b64encode(b"junk").decode(), "slot": "bed"},
                     headers={"X-StoryStudio-Lang": "en"})
    assert en.status_code == 400 and en.json()["detail"].startswith("The uploaded file is not readable audio (fake.mp3): ")
    # 「时长为 0」是自家写的中文原因，英文界面上不能剩这一截（审查实撞：图片改成 .mp3 上传）
    en_zero = client.post(f"{API}/{pid}/assets", json={"filename": "odd_img.mp3", "data_base64": base64.b64encode(MP3 + b"silent").decode(),
                                                        "slot": "bed"}, headers={"X-StoryStudio-Lang": "en"})
    assert en_zero.status_code == 400 and en_zero.json()["detail"] == (
        "The uploaded file is not readable audio (odd_img.mp3): its duration is 0 — try another mp3 / wav / m4a")


def test_english_task_shows_multiline_ffmpeg_mix_failure_in_english(project, monkeypatch):
    """PC-08 审查：mix_error 最常见的是「混音失败：<ffmpeg stderr 尾巴>」，而 stderr 几乎总是多行——英文界面也要翻出来。"""

    from podcast.audio import AudioError

    pid = client.post(API, json=body()).json()["id"]

    def broken_ffmpeg(command):
        raise AudioError("混音失败：[mp3 @ 0000021f] Failed to read frame size: Could not seek to 1026.\n"
                         "G01_01.mp3: Invalid argument\n")

    monkeypatch.setattr(podcast_router, "RUNNER", broken_ffmpeg)
    task_id = client.post(f"{API}/{pid}/synthesize", json={}).json()["task_id"]
    result = wait(task_id)["result"]
    assert result["mixed"] is False and result["mix_error"].startswith("混音失败：[mp3 @")
    en = client.get(f"/api/tasks/{task_id}", headers={"X-StoryStudio-Lang": "en"}).json()
    assert en["result"]["mix_error"] == ("Mixing failed: [mp3 @ 0000021f] Failed to read frame size: Could not seek to 1026.\n"
                                         "G01_01.mp3: Invalid argument\n")


def test_broken_voice_references_are_reported_not_silently_degraded(project, monkeypatch):
    """PC-06：档案不存在 / 只有描述 → 详情打标、告警点名；内联 engine 乱写 → 400；合成时两位主播不同声。"""

    library = {"lib": {"id": "lib", "name": "库里的", "engine": "edge", "voice": "zh-CN-XiaoyiNeural"},
               "desc": {"id": "desc", "name": "只有描述", "engine": "edge", "voice": "", "description": "低沉男声"}}
    monkeypatch.setattr(podcast_router, "VOICE_LOOKUP", library.get)
    created = client.post(API, json=body(hosts=[{"id": "a", "name": "阿一", "voice": "gone"}, {"id": "b", "name": "阿二", "voice": "desc"}])).json()
    pid = created["id"]
    a, b = created["hosts"]
    assert a["placeholder"] is True and a["missing"] is True and a["no_tts"] is False and a["voice_label"].startswith("档案「gone」已不存在，暂用占位音色 ")
    assert b["placeholder"] is True and b["no_tts"] is True and b["missing"] is False and b["voice_label"].startswith("档案「只有描述」只有描述，暂用占位音色 ")
    assert any("主播「阿一」绑的音色档案「gone」已不存在" in w for w in created["warnings"])
    assert any("主播「阿二」绑的档案「desc」只有文字描述，不能出声" in w for w in created["warnings"])
    assert not any("同一个音色" in w for w in created["warnings"])
    assert {p["kind"] for p in created["problems"] if "档案" in p["impact"]} == {"podcast"}
    # 保存照收，但合成前拦下：人挑了音色、整期却按占位音色出声，别等合成完才发现
    refused = client.post(f"{API}/{pid}/synthesize", json={})
    assert refused.status_code == 400 and refused.json()["detail"] == (
        "没开始合成：主播「阿一」绑的音色档案「gone」已不存在——去音色库重新挑一个，或把这位主播的音色清空（用占位音色）再合成")
    assert tasks.active_task_id("pc-audio", pid) is None
    en_refused = client.post(f"{API}/{pid}/synthesize", json={}, headers={"X-StoryStudio-Lang": "en"})
    assert en_refused.status_code == 400 and en_refused.json()["detail"].startswith(
        'Synthesis not started: the voice profile "gone" bound to host "阿一" no longer exists')
    spec = created["spec"]
    spec["hosts"][0]["voice"] = ""
    no_tts = client.put(f"{API}/{pid}", json=spec)
    assert no_tts.status_code == 200
    refused = client.post(f"{API}/{pid}/synthesize", json={})
    assert refused.status_code == 400 and refused.json()["detail"].startswith("没开始合成：主播「阿二」绑的档案「desc」只有文字描述，不能出声")
    # 清空音色（明说要占位音色）就能合成，两位主播不同声
    spec["hosts"][1]["voice"] = ""
    assert client.put(f"{API}/{pid}", json=spec).status_code == 200
    result = synthesized(pid)
    assert result["failed"] == [] and result["placeholder"] == {"a": True, "b": True}
    hosts = json.loads((project / "output" / "podcast" / pid / "07_audio" / "audio_manifest.json").read_text(encoding="utf-8"))["hosts"]
    assert hosts["a"]["voice"] != hosts["b"]["voice"] and not hosts["a"]["missing"] and not hosts["b"]["no_tts"]
    spec["hosts"][0]["voice"] = {"engine": "nope", "voice": "zh-CN-XiaoxiaoNeural"}
    bad = client.put(f"{API}/{pid}", json=spec)
    assert bad.status_code == 400 and bad.json()["detail"] == "改动没通过校验，没有写盘：hosts[0].voice.engine 只能是 edge / settings / minimax，收到「nope」"
    # 英文界面：标签与告警都翻
    spec["hosts"][0]["voice"] = "gone"
    assert client.put(f"{API}/{pid}", json=spec).status_code == 200
    en = client.get(f"{API}/{pid}", headers={"X-StoryStudio-Lang": "en"}).json()
    assert en["hosts"][0]["voice_label"].startswith('profile "gone" no longer exists; using placeholder voice ')
    assert any(w.startswith('Whole episode: the voice profile "gone" bound to host "阿一" no longer exists') for w in en["warnings"])


def test_broken_profile_on_a_host_without_lines_does_not_block_synthesis(project):
    """PC-06 审查：访谈里还没台词的嘉宾绑了坏档案——这次合成用不上他的音色，不拦；告警照样点名。等他有了台词再拦。"""

    hosts = [{"id": "a", "name": "阿一", "role": "host"}, {"id": "b", "name": "阿二", "role": "guest"},
             {"id": "c", "name": "阿三", "role": "guest", "voice": "gone-profile"}]
    created = client.post(API, json=body(format="interview", hosts=hosts)).json()
    pid = created["id"]
    assert created["hosts"][2]["missing"] is True
    assert any("主播「阿三」绑的音色档案「gone-profile」已不存在" in w for w in created["warnings"])
    result = synthesized(pid)
    assert result["failed"] == [] and len(result["synthesized"]) == 2
    spec = client.get(f"{API}/{pid}").json()["spec"]
    spec["segments"][0]["lines"].append({"who": "c", "text": "我也说一句。"})
    assert client.put(f"{API}/{pid}", json=spec).status_code == 200
    refused = client.post(f"{API}/{pid}/synthesize", json={})
    assert refused.status_code == 400 and refused.json()["detail"].startswith("没开始合成：主播「阿三」绑的音色档案「gone-profile」已不存在")


def test_delete_is_refused_while_a_task_runs_and_archive_takes_assets(project, monkeypatch):
    """PC-09：合成进行中 DELETE 409（任务不会往搬走的目录里继续写）；归档把 content/assets/<ID> 一起搬走。"""

    pid = client.post(API, json=body()).json()["id"]
    up = client.post(f"{API}/{pid}/assets", json={"filename": "intro.mp3", "data_base64": base64.b64encode(MP3).decode(), "slot": "intro"})
    assert up.status_code == 200
    asset = project / up.json()["file"]
    gate = threading.Event()

    def slow_synth(profile, text, destination: Path):
        gate.wait(5)
        fake_synth(profile, text, destination)

    monkeypatch.setattr(podcast_router, "SYNTHESIZER", slow_synth)
    started = client.post(f"{API}/{pid}/synthesize", json={})
    assert started.status_code == 200
    try:
        refused = client.delete(f"{API}/{pid}")
        assert refused.status_code == 409 and refused.json()["detail"] == "这期播客正在合成，等它结束再删"
        en = client.delete(f"{API}/{pid}", headers={"X-StoryStudio-Lang": "en"})
        assert en.status_code == 409 and en.json()["detail"] == "This podcast is busy (synthesizing) — wait for it to finish, then delete"
        assert S.spec_path(project, pid).is_file() and asset.is_file()
    finally:
        gate.set()
    assert wait(started.json()["task_id"])["status"] == "succeeded"
    output = project / "output" / "podcast" / pid
    assert (output / "07_audio" / "G01_01.mp3").is_file()
    done = client.delete(f"{API}/{pid}")
    assert done.status_code == 200 and done.json()["output_moved"] is True and done.json()["assets_moved"] is True
    archived = project / done.json()["archived_to"]
    assert (archived / "spec.json").is_file() and (archived / "output" / "07_audio" / "G01_01.mp3").is_file()
    assert (archived / "assets" / asset.name).is_file()
    assert not asset.parent.exists() and not output.exists() and not S.spec_path(project, pid).exists()
    assert client.get(API).json() == []
    # 任务跑完后 output 没有被重建
    time.sleep(0.1)
    assert not output.exists()


def test_target_minutes_float_and_string_are_validated_alike(project):
    """PC-12：target_minutes 传 3.7 以前静默存成 3、传 "3.7" 却 400——现在都 400；3.0 / "3" 照常。"""

    created = client.post(API, json=body()).json()
    pid, spec = created["id"], created["spec"]
    for value in (3.7, "3.7", 90.5):
        r = client.put(f"{API}/{pid}", json={**spec, "target_minutes": value})
        assert r.status_code == 400 and r.json()["detail"] == "改动没通过校验，没有写盘：target_minutes 要是整数", value
    assert client.get(f"{API}/{pid}").json()["spec"]["target_minutes"] == 2
    for value in (3.0, "3"):
        assert client.put(f"{API}/{pid}", json={**spec, "target_minutes": value}).json()["spec"]["target_minutes"] == 3
    assert client.post(API, json=body(target_minutes=3.7)).status_code == 400
    en = client.put(f"{API}/{pid}", json={**spec, "target_minutes": 3.7}, headers={"X-StoryStudio-Lang": "en"})
    assert en.json()["detail"] == "The change failed validation and was not saved: target_minutes must be an integer"


def test_english_ui_gets_cost_note_voice_label_and_task_error_translated(project, monkeypatch):
    """PC-08：cost_note / voice_label / 失败任务的 error 也是给人看的键，英文界面下要翻。"""

    pid = client.post(API, json=body()).json()["id"]
    en = {"X-StoryStudio-Lang": "en"}
    detail = client.get(f"{API}/{pid}", headers=en).json()
    assert detail["hosts"][0]["voice_label"] == "placeholder voice zh-CN-YunxiNeural"
    r = client.post(f"{API}/{pid}/synthesize", json={}, headers=en)
    assert r.status_code == 200 and r.json()["cost_note"].startswith("2 line(s) via edge-tts (free) + local mixing")
    wait(r.json()["task_id"])
    # 起草任务失败原因（ScriptGenError）进任务记录的 error，任务接口在英文下也翻
    monkeypatch.setattr(podcast_router, "DISPATCH", None)
    from storyvideo import scriptgen

    def no_model(*a, **k):
        raise scriptgen.ScriptGenError("还没有可用的剧本文本模型：在「模型设置」里给「剧本文本」选一个来源并填密钥"
                                       "（或在 .env 里配 SCRIPTGEN_LLM_BASE_URL / SCRIPTGEN_LLM_API_KEY / SCRIPTGEN_LLM_MODEL）。")

    monkeypatch.setattr(scriptgen, "scriptgen_backend", lambda: "settings")
    monkeypatch.setattr(scriptgen, "_dispatch", no_model)
    draft = client.post(f"{API}/{pid}/draft", json={}, headers=en)
    assert draft.status_code == 200 and draft.json()["cost_note"] == "Sends 1 text model request"
    task = wait(draft.json()["task_id"])
    # error 前面带不带「ScriptGenError: 」类名前缀由 tasks.start 决定（同批别的改动会把类名挪进 error_type），
    # 翻译规则两种都认，这里只看正文，不绑前缀
    assert task["status"] == "failed" and "还没有可用的剧本文本模型" in task["error"]
    got = client.get(f"/api/tasks/{task['task_id']}", headers=en).json()
    assert ('No script text model is available yet: pick a source for "Script text" in Model settings and enter its key '
            '(or set SCRIPTGEN_LLM_BASE_URL / SCRIPTGEN_LLM_API_KEY / SCRIPTGEN_LLM_MODEL in .env).') in got["error"]
    assert "剧本文本模型" not in got["error"]
    assert client.get(f"/api/tasks/{task['task_id']}").json()["error"] == task["error"]
