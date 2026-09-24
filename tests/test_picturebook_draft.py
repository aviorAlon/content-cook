"""LLM 起草（契约 §6 jobs/draft）：系统提示词判据、schema、草稿收拾（kb 交替 / 结尾钩 / 角色映射）、假后端、后台任务。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from picturebook import draft as D
from picturebook import lint as L
from picturebook import spec as S
from storyvideo.scriptgen import ScriptGenError
from webui import deps
from webui.app import app
from webui.routers import picturebook as R
from tests.picturebook_fixtures import example_data, wait_task

client = TestClient(app)


def _draft_from_example() -> dict:
    """示例书 → 模型输出的形状（characters / scenes 是数组，页里是 kb_dir）。"""

    data = example_data()
    return {
        "title_zh": data["title_zh"], "title_en": data["title_en"], "education": data["education"],
        "characters": [{"id": cid, **{k: v for k, v in m.items() if k != "asset"}} for cid, m in data["characters"].items()],
        "scenes": [{"id": sid, "name": m["name"], "block": m["block"]} for sid, m in data["scenes"].items()],
        "pages": [{"kind": p["kind"], "scene": p["scene"], "characters": p["characters"], "scene_ref": p.get("scene_ref"), "zh": p["zh"], "en": p["en"],
                   "hook": p["hook"], "education": p["education"], "kb_dir": p["kb"]["dir"]} for p in data["pages"]],
    }


def test_system_prompt_carries_the_judgement():
    text = D.build_system_prompt("2-4", "sentence_alternate")
    for needle in ("全书 8–10 页，封面和封底算在内", "正文每页中文 不超过 20 字", "至少三页里一字不差", "最后一次出现时，结果要跟前几次不一样",
                   "正文页不要连着三页都没有 hook", "最后一页的 hook 必须是空字符串", "一页最多一组", "逐句交替朗读", "不逐字对照中文",
                   "cognition", "vocab", "emotion", "kind=cover", "kind=back", "一页只放一个地点、一个瞬间、一个动作",
                   "插画里不能有字", "前后两页不能一样", "只输出 JSON"):
        assert needle in text, needle
    older = D.build_system_prompt("6-8", "page_zh_then_en")
    assert "先念完整页中文" in older and "全书 12–16 页" in older and "正文每页中文 40–70 字" in older
    user = D.build_user_prompt(direction="小狐狸晾袜子", age_band="2-4", bilingual_mode="sentence_alternate",
                               education={"vocab": ["袜子/sock"], "emotion": "分享"}, page_count=None, title=None)
    assert "总页数 8–10 页" in user and "目标词：袜子/sock" in user and "情感点：分享" in user


def test_schema_shape_and_style_presets():
    assert set(D.DRAFT_SCHEMA["required"]) == {"title_zh", "title_en", "education", "characters", "pages"}
    page = D.DRAFT_SCHEMA["properties"]["pages"]["items"]
    assert page["properties"]["kb_dir"]["enum"] == list(S.KB_DIRS) and page["properties"]["kind"]["enum"] == ["cover", "page", "back"]
    assert [p["name"] for p in D.STYLE_PRESETS] == ["水彩", "蜡笔", "剪纸", "扁平矢量"]
    assert D.style_anchor_for("剪纸") == D.STYLE_PRESETS[2]["anchor"] and D.style_anchor_for("flat").startswith("Flat vector")
    assert D.style_anchor_for("my own anchor") == "my own anchor" and D.style_anchor_for("") is None


def test_draft_to_spec_cleans_up(tmp_path):
    draft = _draft_from_example()
    draft["pages"][2]["kb_dir"] = "left"          # 跟上一页一样 → 换
    draft["pages"][4]["kb_dir"] = "spin"          # 不合法 → 换
    draft["pages"][-1]["hook"] = "明天呢？"         # 结尾页 → 清空
    draft["pages"][3]["characters"] = ["小狐狸", "bird"]   # 中文名也能映射
    draft["characters"][0]["id"] = "Little Fox"   # 不合法 id → slug
    spec_like = D.draft_to_spec(draft, age_band="2-4", bilingual_mode="sentence_alternate", style_anchor="X anchor")
    assert [p["id"] for p in spec_like["pages"]][:3] == ["P01", "P02", "P03"]
    dirs = [p["kb"]["dir"] for p in spec_like["pages"]]
    assert all(a != b for a, b in zip(dirs, dirs[1:])) and all(d in S.KB_DIRS for d in dirs)
    assert spec_like["pages"][-1]["hook"] == "" and spec_like["pages"][-1]["kind"] == "back" and spec_like["pages"][0]["kind"] == "cover"
    assert "little_fox" in spec_like["characters"] and spec_like["pages"][3]["characters"] == ["little_fox", "bird"]
    assert spec_like["style_anchor"] == "X anchor" and spec_like["scenes"]["tree"]["block"]
    normalized = S.normalize_book(spec_like, project_root=tmp_path, book_id="PB-D-1")
    assert L.lint_book(normalized) == []
    with pytest.raises(ScriptGenError, match="pages 为空"):
        D.draft_to_spec({"pages": []}, age_band="2-4", bilingual_mode="sentence_alternate")


def test_generate_book_draft_with_fake_dispatch(tmp_path):
    seen = {}

    def fake_dispatch(system_prompt, user_prompt, *, json_schema):
        seen.update(system=system_prompt, user=user_prompt, schema=json_schema)
        return _draft_from_example(), 0.01, 1234, "settings"

    out = D.generate_book_draft(direction="小狐狸晾的袜子被风一只只吹走", age_band="2-4", bilingual_mode="sentence_alternate",
                                education={"vocab": ["袜子/sock", "风/wind"]}, style="水彩", dispatch=fake_dispatch)
    assert out["backend"] == "settings" and out["tokens"] == 1234 and seen["schema"] is D.DRAFT_SCHEMA
    assert "小狐狸晾的袜子被风一只只吹走" in seen["user"] and "副歌" in seen["system"]
    spec = S.normalize_book(out["spec"], project_root=tmp_path, book_id="PB-G-1")
    assert L.lint_book(spec) == [] and spec["style_anchor"].startswith("Soft watercolor")
    with pytest.raises(ScriptGenError):
        D.generate_book_draft(direction="", age_band="2-4", bilingual_mode="sentence_alternate", dispatch=fake_dispatch)
    with pytest.raises(ScriptGenError, match="没配文本模型"):
        D.generate_book_draft(direction="x", age_band="2-4", bilingual_mode="sentence_alternate",
                              dispatch=lambda *a, **k: (_ for _ in ()).throw(ScriptGenError("没配文本模型")))


def test_draft_job_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "PROJECT_ROOT", tmp_path)
    (tmp_path / "content" / "picturebook").mkdir(parents=True)
    monkeypatch.setattr(R, "DISPATCH", lambda system_prompt, user_prompt, *, json_schema: (_draft_from_example(), None, 99, "settings"))
    r = client.post("/api/picturebook/jobs/draft", json={"direction": "小狐狸晾袜子", "age_band": "2-4", "bilingual_mode": "sentence_alternate"})
    assert r.status_code == 200, r.text
    task = wait_task(client, r.json()["task_id"])
    assert task["status"] == "succeeded" and task["kind"] == "pb-draft" and task["module"] == "picturebook", task
    assert task["result"]["saved"] is False and task["result"]["warnings"] == [] and len(task["result"]["draft"]["pages"]) == 10
    assert not (tmp_path / "content" / "picturebook" / f"{r.json()['id']}.json").exists()
    # save=True 直接建书
    r = client.post("/api/picturebook/jobs/draft", json={"direction": "小狐狸晾袜子", "save": True, "style": "剪纸"})
    task = wait_task(client, r.json()["task_id"])
    assert task["status"] == "succeeded" and task["result"]["saved"] is True
    assert client.get(f"/api/picturebook/{r.json()['id']}").json()["spec"]["style_anchor"].startswith("Layered paper-cut")
    assert client.post("/api/picturebook/jobs/draft", json={"direction": "", "age_band": "2-4"}).status_code == 400
    assert client.post("/api/picturebook/jobs/draft", json={"direction": "x", "age_band": "9"}).status_code == 400
    assert client.post("/api/picturebook/jobs/draft", json={"direction": "x", "page_count": 99}).status_code == 400
