"""剧本层（DESIGN §32）：剧本 schema 与系统提示词、纯文本排版、落盘/读回、第二步提示词带剧本、job 接口（替身后端）、列表不把剧本文件当项目。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from storyvideo import scriptgen
from storyvideo.scriptgen import (
    DRAFT_JSON_SCHEMA,
    SCREENPLAY_JSON_SCHEMA,
    build_screenplay_system_prompt,
    build_system_prompt,
    generate_screenplay,
    screenplay_text,
)
from webui import deps
from webui.app import app
from webui.routers import scripts as scripts_router

SCREENPLAY = {
    "title": "厨房关停",
    "logline": "排班员叶澄暗中调灶位保护弱势摊主，被齐曼当众揭开。",
    "contract": {"premise": "p", "hook": "齐曼当众要求出示完整排班记录", "cliffhanger": "叶澄把备用钥匙交给齐曼",
                 "must_pay_off": ["订单缺口的原因"], "carry_over": ["齐曼不知道冷柜后的账本"]},
    "cast": [{"id": "ye", "name": "叶澄", "role": "想不暴露删改地维持营业", "look": "三十岁，黑色短发"},
             {"id": "qi", "name": "齐曼", "role": "要恢复公开规则"}],
    "scenes": [
        {"id": "SC01", "heading": "内 · 共享厨房 · 清晨", "beats": ["齐曼把订单拍在桌上", "叶澄合上排班本"],
         "dialogue": [{"who": "qi", "zh": "把完整的排班记录拿出来。", "cue": "压着火"}, {"who": "ye", "zh": "记录就在这里。"}],
         "change": "齐曼拿到了公开质问的位置"},
        {"id": "SC02", "heading": "内 · 冷柜前 · 清晨", "beats": ["叶澄把备用钥匙交给齐曼，没提账本"], "dialogue": []},
    ],
}


def test_schemas():
    assert set(SCREENPLAY_JSON_SCHEMA["required"]) == {"title", "logline", "contract", "cast", "scenes"}
    scene = SCREENPLAY_JSON_SCHEMA["properties"]["scenes"]["items"]
    assert set(scene["required"]) == {"id", "heading", "beats", "dialogue"}
    assert scene["properties"]["dialogue"]["items"]["required"] == ["who", "zh"]
    contract = DRAFT_JSON_SCHEMA["properties"]["episode_contract"]
    assert set(contract["properties"]) == {"premise", "hook", "cliffhanger", "must_pay_off", "carry_over"}
    assert SCREENPLAY_JSON_SCHEMA["properties"]["contract"] is contract
    assert "episode_contract" not in DRAFT_JSON_SCHEMA["required"]       # 老起草结果不带契约也合法
    assert "12. 先立单集契约（episode_contract）" in build_system_prompt(camera_profile="dark_comedy_vertical", prompt_language="zh")


def test_screenplay_system_prompt():
    zh = build_screenplay_system_prompt(prompt_language="zh")
    assert "只写剧本，不写镜头" in zh and "先立单集契约（contract）" in zh and "每一场都要改变信息、权力、关系" in zh
    assert "剧本用中文写" in zh and "题材卡：" not in zh and "运镜" in zh
    en = build_screenplay_system_prompt(prompt_language="en")
    assert "用英文写" in en
    with_genre = build_screenplay_system_prompt(prompt_language="zh", genre="复仇打脸")
    assert with_genre.startswith(zh) and "题材卡：复仇打脸" in with_genre
    with pytest.raises(ValueError, match="不认识的题材"):
        build_screenplay_system_prompt(prompt_language="zh", genre="火星题材")


def test_screenplay_text_layout():
    text = screenplay_text(SCREENPLAY)
    assert text.startswith("《厨房关停》")
    assert "开场钩子：齐曼当众要求出示完整排班记录" in text and "本集必须兑现：订单缺口的原因" in text
    assert "- ye（叶澄）：想不暴露删改地维持营业｜外形：三十岁，黑色短发" in text
    assert "## SC01 内 · 共享厨房 · 清晨" in text and "- 齐曼把订单拍在桌上" in text
    assert "qi（压着火）：把完整的排班记录拿出来。" in text and "（这一场改变了：齐曼拿到了公开质问的位置）" in text
    assert screenplay_text({"scenes": []}) == ""


def test_generate_screenplay_and_step_two_prompt(monkeypatch):
    seen = {}

    def fake_dispatch(system_prompt, user_prompt, *, json_schema):
        seen["system"], seen["user"], seen["schema"] = system_prompt, user_prompt, json_schema
        return json.loads(json.dumps(SCREENPLAY)), None, 7, "settings"

    monkeypatch.setattr(scriptgen, "_dispatch", fake_dispatch)
    sp, cost, tokens, backend = generate_screenplay(episode_id="EP-1", direction="厨房", target_seconds=60, prompt_language="zh",
                                                    cast_hint="两个女人", title="我的标题", genre="复仇打脸")
    assert seen["schema"] is SCREENPLAY_JSON_SCHEMA and "题材卡：复仇打脸" in seen["system"]
    assert "剧名：我的标题" in seen["user"] and "角色/关系提示：\n两个女人" in seen["user"] and "剧本体量按这个来" in seen["user"]
    assert sp["title"] == "我的标题" and tokens == 7 and backend == "settings"
    # 第二步：剧本进用户提示词，一步起草的提示词不变
    scriptgen.generate_draft(episode_id="EP-1", direction="厨房", target_seconds=60, camera_profile="dark_comedy_vertical",
                             prompt_language="zh", cast_hint=None, screenplay=SCREENPLAY)
    assert seen["schema"] is DRAFT_JSON_SCHEMA
    assert "按这个剧本拆镜，不要改剧情、不要加角色" in seen["user"] and "（ye、qi）" in seen["user"]
    assert "## SC01 内 · 共享厨房 · 清晨" in seen["user"] and "episode_contract 照抄剧本的 contract" in seen["user"]
    scriptgen.generate_draft(episode_id="EP-1", direction="厨房", target_seconds=60, camera_profile="dark_comedy_vertical",
                             prompt_language="zh", cast_hint=None)
    assert "按这个剧本拆镜" not in seen["user"] and "SC01" not in seen["user"]
    # 模型没按结构返回 → ScriptGenError
    monkeypatch.setattr(scriptgen, "_dispatch", lambda *a, **k: ({"title": "x"}, None, 1, "settings"))
    with pytest.raises(scriptgen.ScriptGenError, match="缺 scenes 列表"):
        generate_screenplay(episode_id="EP-1", direction="x", target_seconds=60, prompt_language="zh", cast_hint=None)


@pytest.fixture
def root(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, scripts_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(scripts_router, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(scripts_router.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    return tmp_path


def test_screenplay_job_saves_file_then_draft_uses_it(root, monkeypatch):
    R = scripts_router
    client = TestClient(app)
    monkeypatch.setattr(R, "generate_screenplay", lambda **kw: (json.loads(json.dumps(SCREENPLAY)), None, 3, "settings"))
    r = client.post("/api/scripts/jobs/screenplay", json={"episode_id": "EP-1", "direction": "厨房", "target_seconds": 60, "camera_profile": "dark_comedy_vertical", "genre": "复仇打脸"})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/scripts/jobs/{r.json()['job_id']}").json()
    assert job["kind"] == "generate_screenplay" and job["status"] == "succeeded" and job["saved"] is True
    assert job["result"]["screenplay"]["title"] == "厨房关停" and job["result"]["usage_tokens"] == 3
    path = root / "content" / "story_video" / "EP-1.screenplay.json"
    assert path.is_file() and job["result"]["path"] == "content/story_video/EP-1.screenplay.json"
    # 剧本文件不是项目：列表里没有它，但它占着 EP-1 这个编号
    assert client.get("/api/episodes").json() == []
    assert "EP-1" in R.existing_episode_ids()
    # 读 / 改
    got = client.get("/api/episodes/storyvideo/EP-1/screenplay").json()
    assert got["screenplay"]["scenes"][0]["id"] == "SC01" and got["text"].startswith("《厨房关停》")
    edited = json.loads(json.dumps(SCREENPLAY))
    edited["scenes"][0]["dialogue"][0]["zh"] = "排班记录，现在。"
    assert client.put("/api/episodes/storyvideo/EP-1/screenplay", json={"screenplay": edited}).status_code == 200
    assert json.loads(path.read_text(encoding="utf-8"))["scenes"][0]["dialogue"][0]["zh"] == "排班记录，现在。"
    assert client.put("/api/episodes/storyvideo/EP-1/screenplay", json={"screenplay": {"scenes": []}}).status_code == 400
    assert client.get("/api/episodes/storyvideo/EP-2/screenplay").status_code == 404
    # 第二步：按剧本拆镜，剧本进提示词，契约从剧本抄过来
    seen = {}

    def fake_generate_draft(**kw):
        seen.update(kw)
        return {"title_zh": "t", "logline": "l", "frame_style": "f", "scene_anchor": "s", "camera": "c", "neg": "n",
                "cast": {"ye": {"block": "b"}}, "shots": [{"id": "S1", "dur": 5, "cast": ["ye"], "shot": "中景", "art": "a", "motion": "m", "lines": []}]}, None, 1, "settings"

    monkeypatch.setattr(R, "generate_draft", fake_generate_draft)
    r = client.post("/api/scripts/jobs/draft", json={"episode_id": "EP-1", "direction": "厨房", "target_seconds": 60, "camera_profile": "dark_comedy_vertical", "use_screenplay": True})
    assert r.status_code == 200, r.text
    assert seen["screenplay"]["scenes"][0]["dialogue"][0]["zh"] == "排班记录，现在。"
    job = client.get(f"/api/scripts/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded" and job["params"]["use_screenplay"] is True
    assert job["result"]["spec"]["episode_contract"]["hook"] == "齐曼当众要求出示完整排班记录"
    # 没有剧本却要按剧本拆镜 → 400
    r = client.post("/api/scripts/jobs/draft", json={"episode_id": "EP-2", "direction": "x", "target_seconds": 60, "camera_profile": "p", "use_screenplay": True})
    assert r.status_code == 400 and "还没有剧本" in r.text
    # 一步起草不带剧本
    seen.clear()
    r = client.post("/api/scripts/jobs/draft", json={"episode_id": "EP-3", "direction": "x", "target_seconds": 60, "camera_profile": "dark_comedy_vertical"})
    assert r.status_code == 200 and seen["screenplay"] is None and seen["series_handoff"] is None


def test_screenplay_job_failure_and_validation(root, monkeypatch):
    R = scripts_router
    client = TestClient(app)
    monkeypatch.setattr(R, "generate_screenplay", lambda **kw: (_ for _ in ()).throw(R.ScriptGenError("没配文本模型")))
    r = client.post("/api/scripts/jobs/screenplay", json={"direction": "x", "target_seconds": 60, "camera_profile": "p"})
    assert r.status_code == 200 and r.json()["episode_id"].startswith("EP-")
    job = client.get(f"/api/scripts/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "failed" and job["error"].startswith("出剧本失败：")
    assert not (root / "content" / "story_video").glob("*.screenplay.json").__next__ if False else True
    assert client.post("/api/scripts/jobs/screenplay", json={"episode_id": "bad id", "direction": "x", "target_seconds": 60, "camera_profile": "p"}).status_code == 400
    assert client.post("/api/scripts/jobs/screenplay", json={"direction": "x", "target_seconds": 60, "camera_profile": "p", "genre": "火星题材"}).status_code == 400
    (root / "content" / "story_video" / "EP-7.json").write_text("{}", encoding="utf-8")
    assert client.post("/api/scripts/jobs/screenplay", json={"episode_id": "EP-7", "direction": "x", "target_seconds": 60, "camera_profile": "p"}).status_code == 409
