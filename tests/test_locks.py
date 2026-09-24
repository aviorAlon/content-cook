"""连续性锁（2026-09-08，融合 drama-skills continuity-lock 的判据）：注入规则、无锁不变、lint、角色接口、revise。

生效规则（DESIGN §19）：shots="all" → 成员在本镜 cast 里的每一镜；shots=[...] → 恰好列出的那几镜，
成员不在本镜 cast 里也注入。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyvideo import lint as L
from storyvideo.prompts import compile_frame_prompt, compile_negative_prompt, compile_video_prompt, video_prompt_parts
from storyvideo.runtime import StoryVideoRunner
from storyvideo.spec import load_spec
from webui import deps
from webui import revise as R
from webui.app import app
from webui.routers import episodes as episodes_router
from webui.routers import post as post_router
from webui.routers import project as project_router

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

SWEATER = "淡蓝色粗针织羊毛毛衣"
BELL = "红色小铃铛"


def _spec(lang="zh", **extra):
    spec = {
        "id": "LOCK-T", "output_root": "output/story_video/LOCK-T", "prompt_language": lang,
        "frame_style": "S. ", "scene_anchor": "A. ", "camera": "", "neg": "文字",
        "cast": {
            "lin": {"name": "林小满", "block": "林小满：黑发。", "locks": [{"surface": SWEATER, "shots": "all"}]},
            "cat": {"name": "招财猫", "block": "金色招财猫。", "locks": [{"surface": BELL, "shots": ["S2"], "note": "被手挡住也要写"}]},
            "office": {"block": "办公区。"},
        },
        "shots": [
            {"id": "S1", "dur": 4, "cast": ["office", "lin"], "shot": "中景，平视", "art": "她坐着", "motion": "她抬头", "lines": []},
            {"id": "S2", "dur": 4, "cast": ["office", "lin"], "shot": "近景，平视", "art": "她伸手", "motion": "她伸手", "lines": []},
            {"id": "S3", "dur": 4, "cast": ["office"], "shot": "全景，平视", "art": "空镜", "motion": "灯闪", "lines": []},
        ],
    }
    spec.update(extra)
    return spec


def test_injection_rule_all_scope_and_explicit_list():
    spec = _spec()
    # S1：lin 在 cast，all 范围 → 毛衣；cat 的锁只点名 S2 → 铃铛不进
    frame, video = compile_frame_prompt(spec, "S1"), compile_video_prompt(spec, "S1")
    assert f"固定不变：{SWEATER}。" in frame and f"固定不变：{SWEATER}。" in video
    assert BELL not in frame and BELL not in video
    # S2：cat 不在 cast，但锁点名了 S2 → 铃铛也注入；顺序按 cast 顺序，每个锁面只出现一次
    frame, video = compile_frame_prompt(spec, "S2"), compile_video_prompt(spec, "S2")
    assert f"固定不变：{SWEATER}；{BELL}。" in frame and f"固定不变：{SWEATER}；{BELL}。" in video
    assert frame.count(SWEATER) == 1 and video.count(SWEATER) == 1
    # 首帧：锁句紧跟在角色块之后；视频：锁句紧挨在「锁」段之前
    assert frame.index("林小满：黑发。") < frame.index("固定不变") < frame.index("镜位：")
    parts = video_prompt_parts(spec, "S2")
    assert parts["locks"] == f"固定不变：{SWEATER}；{BELL}。"
    assert video.index("固定不变") < video.index(parts["lock"])
    # S3：lin 不在 cast，all 范围不注入
    assert "固定不变" not in compile_frame_prompt(spec, "S3") and "固定不变" not in compile_video_prompt(spec, "S3")
    # 负向词不受影响
    assert compile_negative_prompt(spec, "S1") == "文字"


def test_injection_english_and_dialects():
    spec = _spec(lang="en")
    spec["cast"]["lin"]["locks"] = [{"surface": "pale blue chunky knit wool sweater", "shots": "all"}]
    spec["cast"]["cat"]["locks"] = [{"surface": "small red bell", "shots": ["S2"]}]
    frame = compile_frame_prompt(spec, "S2")
    assert "Fixed across shots: pale blue chunky knit wool sweater; small red bell. SHOT:" in frame
    video = compile_video_prompt(spec, "S2")
    assert " Fixed across shots: pale blue chunky knit wool sweater; small red bell. Keep the room" in video
    # minimax-h3：锁句在画面正文里，紧挨在锁段之前
    spec["video_params"] = {"prompt_dialect": "minimax-h3"}
    h3 = compile_video_prompt(spec, "S2")
    body = h3.splitlines()[0]
    assert body.startswith("integrated_multimodal_description: [Shot 1] ")
    assert "Fixed across shots: pale blue chunky knit wool sweater; small red bell. Keep the room" in body
    # seedance 也带
    spec["video_params"] = {"prompt_dialect": "seedance"}
    assert "Fixed across shots: pale blue chunky knit wool sweater; small red bell." in compile_video_prompt(spec, "S2")
    # 中文 + minimax-h3
    zh = _spec()
    zh["video_params"] = {"prompt_dialect": "minimax-h3"}
    assert f"固定不变：{SWEATER}；{BELL}。" in compile_video_prompt(zh, "S2").splitlines()[0]


def test_no_locks_leaves_every_prompt_untouched(tmp_path):
    raw = json.loads((ROOT / "examples" / "episodes" / "first_episode.json").read_text(encoding="utf-8-sig"))
    raw["id"] = "NOLOCK"; raw["output_root"] = "output/story_video/NOLOCK"
    content = tmp_path / "content" / "story_video"; content.mkdir(parents=True)
    (content / "NOLOCK.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    spec = load_spec("NOLOCK", project_root=tmp_path)
    for shot in spec["shots"]:
        sid = shot["id"]
        assert "固定不变" not in compile_frame_prompt(spec, sid) and "Fixed across shots" not in compile_frame_prompt(spec, sid)
        assert "固定不变" not in compile_video_prompt(spec, sid)
        assert video_prompt_parts(spec, sid)["locks"] == ""
    # 空列表 / 形状不对的锁也一个字不多
    spec["cast"]["lin"]["locks"] = []
    spec["cast"]["cat"]["locks"] = [{"surface": "  "}, "junk", {"shots": "all"}]
    assert "固定不变" not in compile_frame_prompt(spec, spec["shots"][0]["id"])


def test_surface_matching_rules():
    assert L.surface_present("she keeps knitting the pale blue chunky knit wool sweater", "pale blue chunky knit wool sweater")
    assert L.surface_present("PALE BLUE\nsweater.", "pale blue sweater")          # 忽略大小写，换行当空格
    assert not L.surface_present("an unchipped white mug", "chipped white mug")   # 粘在词上不算
    assert not L.surface_present("pale blue knitwear-print fleece", "pale blue knit")
    assert L.surface_present("织着浅蓝色粗棒针毛线", "浅蓝色粗棒针毛线")             # 中文不分词
    assert not L.surface_present("正文。不要出现淡蓝色毛衣", "淡蓝色毛衣")            # 否定之后不算
    assert not L.surface_present("Body. Avoid: pale blue sweater", "pale blue sweater")


def test_lint_passes_when_present_and_reports_breakage():
    spec = _spec()
    assert L.check_locks(spec) == []
    assert not [m for m in L.check_all(spec) if "锁" in m]
    # neg_extra 里写了锁面：这是叫模型别画它
    spec["shots"][0]["neg_extra"] = f"{SWEATER}, 水印"
    msgs = L.check_locks(spec)
    assert any("S1" in m and "负向词里出现了锁面" in m and SWEATER in m for m in msgs)
    # 角色块里写了「不要出现」→ 首帧里的锁句落在否定之后，不算在场，并说清改法
    spec = _spec()
    spec["cast"]["office"]["block"] = "办公区。不要出现路人。"
    msgs = L.check_locks(spec)
    assert any("S1" in m and "首帧" in m and "只出现在「不要出现」之后" in m and "负向词" in m for m in msgs)
    assert not any("视频" in m for m in msgs)
    # 镜头正文里写了「不要出现」→ 视频提示词同理
    spec = _spec()
    spec["shots"][1]["motion"] = "她伸手。不要出现第二个人。"
    msgs = L.check_locks(spec)
    assert any("S2" in m and "视频" in m and "只出现在「不要出现」之后" in m for m in msgs)
    assert not any("首帧" in m for m in msgs)
    # 英文：Avoid: 同理
    en = _spec(lang="en")
    en["cast"]["lin"]["locks"] = [{"surface": "pale blue sweater", "shots": "all"}]
    en["cast"]["cat"]["locks"] = []
    assert L.check_locks(en) == []
    en["shots"][0]["motion"] = "She looks up. Avoid: extra people."
    assert any("S1" in m and "Avoid:" in m for m in L.check_locks(en))


def test_lint_shape_count_length_and_unknown_shots():
    spec = _spec()
    spec["cast"]["lin"]["locks"] = [{"surface": f"锁{i}", "shots": "all"} for i in range(6)]
    assert any("cast.lin 有 6 把锁" in m and "个位数" in m for m in L.check_locks(spec))
    spec = _spec()
    spec["cast"]["lin"]["locks"] = [{"surface": "淡蓝色" * 15, "shots": "all"}]
    assert any("45 字" in m and "最小可辨识名词短语" in m for m in L.check_locks(spec))
    spec = _spec()
    spec["cast"]["cat"]["locks"] = [{"surface": BELL, "shots": ["S2", "S9"]}]
    assert any("不存在的镜：S9" in m for m in L.check_locks(spec))
    spec = _spec()
    spec["cast"]["cat"]["locks"] = [{"surface": "", "shots": "all"}, {"surface": BELL, "shots": "S2"}]
    msgs = L.check_locks(spec)
    assert sum("写法不对" in m for m in msgs) == 2
    spec["cast"]["cat"]["locks"] = {"surface": BELL}
    assert any("必须是列表" in m for m in L.check_locks(spec))


# ---------------------------------------------------------------- 接口 + revise


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    spec = _spec(subtitle={"language": "zh"})
    for member in spec["cast"].values():
        member.pop("locks", None)
    (content_dir / "LOCK-T.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _raw(project: Path) -> dict:
    return json.loads((project / "content" / "story_video" / "LOCK-T.json").read_text(encoding="utf-8"))


def test_cast_api_roundtrip_and_validation(project):
    url = "/api/episodes/storyvideo/LOCK-T/cast/lin"
    locks = [{"surface": f"  {SWEATER} ", "shots": "all", "note": ""}, {"surface": BELL, "shots": ["S2", "S2", " S1"], "note": "备注"}]
    resp = client.put(url, json={"locks": locks})
    assert resp.status_code == 200, resp.text
    assert resp.json()["locks"] == [{"surface": SWEATER, "shots": "all"}, {"surface": BELL, "shots": ["S2", "S1"], "note": "备注"}]
    assert _raw(project)["cast"]["lin"]["locks"] == resp.json()["locks"]
    listed = {c["id"]: c for c in client.get("/api/episodes/storyvideo/LOCK-T/cast").json()}
    assert listed["lin"]["locks"][0]["surface"] == SWEATER and listed["cat"]["locks"] == []
    # 不传 = 不动；改别的字段锁还在
    assert client.put(url, json={"voice": "低沉"}).json()["locks"][1]["note"] == "备注"
    # 校验：锁面空、范围写法不对、指定镜为空、不是列表 → 400，且不写盘
    for bad in ([{"surface": " ", "shots": "all"}], [{"surface": "x", "shots": "S1"}], [{"surface": "x", "shots": []}],
                [{"surface": "x", "shots": [1]}]):
        r = client.put(url, json={"locks": bad})
        assert r.status_code == 400, bad
    # 连列表元素都不是对象的，pydantic 在进函数前就拦下（422）
    assert client.put(url, json={"locks": ["x"]}).status_code == 422
    assert client.put(url, json={"locks": {"surface": "x"}}).status_code == 422
    assert len(_raw(project)["cast"]["lin"]["locks"]) == 2
    # 锁进了提示词；[] 清空
    detail_spec = load_spec("LOCK-T", project_root=project)
    assert f"固定不变：{SWEATER}；{BELL}。" in compile_frame_prompt(detail_spec, "S1")
    assert client.put(url, json={"locks": []}).json()["locks"] == []
    assert "locks" not in _raw(project)["cast"]["lin"]
    # 新建时也能带锁
    created = client.post("/api/episodes/storyvideo/LOCK-T/cast", json={"id": "mug", "block": "白色搪瓷杯。", "locks": [{"surface": "缺口白搪瓷杯"}]})
    assert created.status_code == 200 and created.json()["locks"] == [{"surface": "缺口白搪瓷杯", "shots": "all"}]


def test_cast_locks_refused_on_locked_project_and_revise_handles_it(project):
    with StoryVideoRunner(project) as runner:
        runner.prepare("LOCK-T")
    resp = client.put("/api/episodes/storyvideo/LOCK-T/cast/lin", json={"locks": [{"surface": SWEATER, "shots": "all"}]})
    assert resp.status_code == 409 and "S1" in resp.json()["detail"] and "S2" in resp.json()["detail"]
    assert "locks" not in _raw(project)["cast"]["lin"]
    # revise：cast_edit 带 locks → 只有 lin 出场的镜首帧要重出
    mutation = {"kind": "cast_edit", "cast_id": "lin", "locks": [{"surface": SWEATER, "shots": "all"}]}
    out = R.apply_mutation(_raw(project), mutation)
    assert out["cast"]["lin"]["locks"] == [{"surface": SWEATER, "shots": "all"}]
    body = client.post("/api/episodes/storyvideo/LOCK-T/revise-edit/preview", json={"mutation": mutation}).json()
    assert body["affected_frames"] == ["S1", "S2"] and body["affected_videos"] == ["S1", "S2"]
    assert "prompt" in body["reasons"]["S1"]
    # 只点名 S3 的锁：cat 不在 S3 的 cast 里，也只影响 S3
    mutation = {"kind": "cast_edit", "cast_id": "cat", "locks": [{"surface": BELL, "shots": ["S3"]}]}
    body = client.post("/api/episodes/storyvideo/LOCK-T/revise-edit/preview", json={"mutation": mutation}).json()
    assert body["affected_frames"] == ["S3"]
    # 形状不对的锁在 revise 里也是 400
    bad = {"kind": "cast_edit", "cast_id": "cat", "locks": [{"surface": "", "shots": "all"}]}
    assert client.post("/api/episodes/storyvideo/LOCK-T/revise-edit/preview", json={"mutation": bad}).status_code == 400
