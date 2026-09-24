"""尾帧续接（DESIGN §30 的运行时实现）：``shot.ref_tail = {shot, mode}``。
归一化（裸字符串 = reference、源镜要在前面、与 ref_frame 同镜）；ffmpeg 抽尾帧；预估/运行时把源镜视频没过检的镜推迟，
过检后 reference 模式上传尾帧作第一参考槽、direct 模式尾帧直接当首帧（不花钱、账本记 tail:<源镜>）；源镜视频重拍传染；
lint（景别下限 → 桥接帧）；编辑接口。"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from genkit.providers import ImageRequest, VideoRequest

from storyvideo import lint, tail
from storyvideo.prompts import frame_request_data, reference_slot_plan
from storyvideo.runtime import StoryVideoRunner
from storyvideo.spec import SpecError, load_spec
from tests.test_storyvideo_runtime import FakeProvider, fake_media, write_episode  # noqa: F401 - fixture re-export
from webui import deps, generate, i18n
from webui.app import app
from webui.routers import episodes as episodes_router


# ---------------------------------------------------------------- 归一化


def _spec(tmp_path, shots):
    raw = {"id": "TR-T", "output_root": "output/story_video/TR-T", "prompt_language": "zh", "neg": "n",
           "cast": {"lin": {"name": "林小满", "block": "林。", "asset": "content/assets/lin.png"}}, "shots": shots}
    content = tmp_path / "content" / "story_video"
    content.mkdir(parents=True, exist_ok=True)
    (content / "TR-T.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return load_spec("TR-T", project_root=tmp_path)


def _shot(sid, **extra):
    return {"id": sid, "dur": 4, "cast": ["lin"], "shot": "中景", "art": "a", "motion": "m", "lines": [], **extra}


def test_normalize_shapes_and_rejections(tmp_path):
    spec = _spec(tmp_path, [_shot("S1"), _shot("S2", ref_tail="S1"), _shot("S3", ref_tail={"shot": "S1", "mode": "direct"}),
                            _shot("S4", ref_tail={"shot": "S3"}, ref_frame="S3")])
    shots = {s["id"]: s for s in spec["shots"]}
    assert shots["S2"]["ref_tail"] == {"shot": "S1", "mode": "reference"}      # 裸字符串 = reference
    assert shots["S3"]["ref_tail"] == {"shot": "S1", "mode": "direct"}
    assert shots["S4"]["ref_tail"] == {"shot": "S3", "mode": "reference"}
    assert tail.tail_ref(shots["S3"]) == ("S1", "direct") and tail.tail_ref(shots["S1"]) is None
    assert reference_slot_plan(spec, "S2")[0]["target"] == "tail" and reference_slot_plan(spec, "S3")[0]["target"] == "cast:lin"
    assert "ref_tail" in frame_request_data(spec, "S2") and "ref_tail" not in frame_request_data(spec, "S1")
    for shots_bad, needle in (
        ([_shot("S1", ref_tail="S1")], "must not reference itself"),
        ([_shot("S1", ref_tail="S2"), _shot("S2")], "not earlier in the episode"),
        ([_shot("S1"), _shot("S2", ref_tail="S9")], "not earlier in the episode"),
        ([_shot("S1"), _shot("S2", ref_tail={"shot": "S1", "mode": "copy"})], "mode must be reference or direct"),
        ([_shot("S1"), _shot("S2", ref_tail={"shot": "S1", "x": 1})], "unknown fields"),
        ([_shot("S1"), _shot("S2", ref_tail=3)], "must be a shot id or an object"),
        ([_shot("S1"), _shot("S2"), _shot("S3", ref_tail="S1", ref_frame="S2")], "must point to the same shot"),
    ):
        with pytest.raises(SpecError, match=needle):
            _spec(tmp_path, shots_bad)


# ---------------------------------------------------------------- ffmpeg 抽帧


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_extract_tail_frame_with_real_ffmpeg(tmp_path):
    video = tmp_path / "S1.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=64x96:d=1:r=10",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    out = tail.extract_tail_frame(video, tmp_path / "04_tail" / "S1.png", size=(32, 48))
    assert out.is_file() and out.stat().st_size > 0
    assert _png_size(out) == (32, 48)
    native = tail.extract_tail_frame(video, tmp_path / "04_tail" / "S1-native.png")
    assert _png_size(native) == (64, 96)
    assert not list((tmp_path / "04_tail").glob(".*partial*"))
    with pytest.raises(RuntimeError, match="抽尾帧失败"):
        tail.extract_tail_frame(tmp_path / "missing.mp4", tmp_path / "04_tail" / "x.png")


# ---------------------------------------------------------------- 预估


def test_estimate_marks_tail_gated_shots(tmp_path):
    spec = _spec(tmp_path, [_shot("S1"), _shot("S2", ref_tail="S1")])
    est = generate.estimate_plan(spec, through="frames", only=None)
    assert est["pending_count"] == 1 and est["already_have_count"] == 0
    gated = next(i for i in est["items"] if i["shot_id"] == "S2")
    assert gated["waiting_tail"] == {"source": "S1", "mode": "reference", "reason": "等 S1 视频过检后抽尾帧"}
    assert est["tail_pending"] == [{"shot_id": "S2", "source": "S1", "mode": "reference", "reason": "等 S1 视频过检后抽尾帧"}]
    assert i18n.tr(gated["waiting_tail"]["reason"], "en") == "Waiting for the S1 video to pass QC before extracting its tail frame"


# ---------------------------------------------------------------- 运行时


def _relay_shots():
    base = {"dur": 4, "cast": ["su"], "lines": [{"who": "su", "en": "Hello.", "zh": "你好。"}], "shot": "medium",
            "art": "A bright room.", "motion": "Su breathes."}
    return [{"id": "S1", **base}, {"id": "S2", **base, "ref_tail": "S1"},
            {"id": "S3", **base, "ref_tail": {"shot": "S1", "mode": "direct"}}]


@pytest.fixture
def fake_extract(monkeypatch):
    calls: list[tuple[Path, Path, tuple | None]] = []

    def extract(video, destination, *, ffmpeg="ffmpeg", size=None, **kwargs):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"tail-frame-" + Path(video).read_bytes()[:64] + b"\0" * 2048)
        calls.append((Path(video), destination, size))
        return destination

    monkeypatch.setattr("storyvideo.tail.extract_tail_frame", extract)
    return calls


def _jobs(result):
    return {job["step_id"]: job for job in result["state"]["jobs"]}


def test_runtime_defers_until_source_video_passes_then_relays(tmp_path, fake_media, fake_extract):
    write_episode(tmp_path, shots=_relay_shots())
    provider = FakeProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        first = runner.run("EP99")
        # 只有 S1 出了首帧；S2 / S3 推迟并给出原因
        assert [type(r) for r in provider.submissions] == [ImageRequest]
        assert set(first["tail_pending"]) == {"S2", "S3"}
        assert first["tail_pending"]["S2"] == {"source": "S1", "mode": "reference", "reason": "等 S1 视频过检后抽尾帧"}
        assert runner.run("EP99")["status"] == first["status"] and len(provider.submissions) == 1
        runner.review("EP99", "frames", "pass", shots=["S1"])
        videos = runner.run("EP99")
        assert [type(r) for r in provider.submissions] == [ImageRequest, VideoRequest]
        assert set(videos["tail_pending"]) == {"S2", "S3"}
        assert runner.run("EP99")["status"] == videos["status"] and len(provider.submissions) == 2
        assert not fake_extract   # 过检前不抽帧
        runner.review("EP99", "videos", "pass", shots=["S1"])
        relay = runner.run("EP99")
        assert relay["tail_pending"] == {}
        jobs = _jobs(relay)
        # direct：尾帧直接成为 S3 的首帧，不出图，账本记来源；reference：尾帧上传为 S2 首帧的第一参考槽
        assert jobs["frame-S3"]["status"] == "succeeded" and jobs["frame-S3"]["result_url"] == "tail:S1"
        assert int(jobs["frame-S3"]["attempt"] or 0) == 0
        assert (Path(relay["run_dir"]) / "01_frames" / "S3.png").read_bytes().startswith(b"tail-frame-")
        marker = json.loads((Path(relay["run_dir"]) / "manifests" / "installs" / "frame-S3.json").read_text(encoding="utf-8"))
        assert marker["source"] == "tail:S1"
        sidecar = json.loads((Path(relay["run_dir"]) / "04_tail" / "S1.json").read_text(encoding="utf-8"))
        assert sidecar["source"] == "S1" and sidecar["video_sha256"]
        assert len(fake_extract) == 1 and fake_extract[0][1].name == "S1.png" and fake_extract[0][1].parent.name == "04_tail"
        image_requests = [r for r in provider.submissions if isinstance(r, ImageRequest)]
        assert [r.name.split("-")[1] for r in image_requests] == ["S1", "S2"]
        assert image_requests[1].reference_asset_ids[0] == "upload-S1"
        assert any(p.parent.name == "04_tail" for p in provider.uploads)
        assert jobs["frame-S2"]["status"] == "succeeded"
        assert set(relay["qc"]["gates"]["frames"]["items"]) == {"S1", "S2", "S3"}
        # 两镜首帧过检 → 视频
        runner.review("EP99", "frames", "pass", shots=["S2", "S3"])
        after = runner.run("EP99")
        jobs = _jobs(after)
        assert jobs["video-S2"]["status"] == "succeeded" and jobs["video-S3"]["status"] == "succeeded"
        assert after["status"] == "awaiting_videos_qc"
        # 源镜视频重拍 → 尾帧作废 → S2 / S3 首帧与视频都回到 prepared
        runner.review("EP99", "videos", "pass", shots=["S2", "S3"])
        retaken = runner.review("EP99", "videos", "retake", shots=["S1"], reason="接缝断裂")
        jobs = _jobs(retaken)
        assert jobs["video-S1"]["status"] == "prepared"
        assert {jobs[f"{stage}-{sid}"]["status"] for stage in ("frame", "video") for sid in ("S2", "S3")} == {"prepared"}
        assert retaken["qc"]["gates"]["frames"]["items"]["S2"]["stale"] is True
        assert set(retaken["tail_pending"]) == {"S2", "S3"}


# ---------------------------------------------------------------- lint


def test_lint_tail_relay():
    def spec(src_shot="大全景", dst_shot="特写", **extra):
        return {"cast": {"lin": {"name": "林小满", "block": "x"}},
                "shots": [{"id": "S1", "dur": 4, "cast": ["lin"], "shot": src_shot, "art": "a", "motion": "m"},
                          {"id": "S2", "dur": 4, "cast": ["lin"], "shot": dst_shot, "art": "b", "motion": "m", **extra}]}

    assert lint.check_tail_relay(spec()) == []
    msgs = lint.check_tail_relay(spec(ref_tail="S1"))
    assert len(msgs) == 1 and "源镜 S1 收在「大全景」" in msgs[0] and "本镜是「特写」" in msgs[0] and "桥接帧" in msgs[0]
    assert "直接当首帧" in lint.check_tail_relay(spec(ref_tail={"shot": "S1", "mode": "direct"}))[0]
    assert lint.check_tail_relay(spec("中景", "近景", ref_tail="S1")) == []
    assert lint.check_tail_relay(spec("wide shot", "close-up", ref_tail="S1")) and "wide shot" in lint.check_tail_relay(spec("wide shot", "close-up", ref_tail="S1"))[0]
    assert "不存在" in lint.check_tail_relay(spec("中景", "近景", ref_tail="S9"))[0]
    assert "不在本镜之前" in lint.check_tail_relay({"cast": {}, "shots": [{"id": "S1", "shot": "中景", "ref_tail": "S2"}, {"id": "S2", "shot": "中景"}]})[0]
    assert "写法不对" in lint.check_tail_relay(spec("中景", "近景", ref_tail={"mode": "direct"}))[0]
    assert "两者要指向同一镜" in lint.check_tail_relay({"cast": {}, "shots": [{"id": "S0", "shot": "中景"}, {"id": "S1", "shot": "中景"},
                                                                          {"id": "S2", "shot": "中景", "ref_tail": "S1", "ref_frame": "S0"}]})[0]
    assert "check_tail_relay" in lint.__all__ and lint.check_all(spec(ref_tail="S1"))
    i18n._compiled = None
    for m in lint.check_tail_relay(spec(ref_tail="S1")) + lint.check_tail_relay(spec("中景", "近景", ref_tail="S9")):
        assert "shot S2: " in i18n.tr(m, "en") and "桥接" not in i18n.tr(m, "en") and "不存在" not in i18n.tr(m, "en")
    # continuity：接尾帧的镜算参考链的一环，用了入口/出口字段的项目里没写入口就提醒（老项目没用这套字段不吵）
    from storyvideo import continuity
    relay = spec("中景", "近景", ref_tail="S1")
    assert continuity.check_continuity(relay) == []
    relay["shots"][0]["exit_state"] = "她站在门口"
    msgs = continuity.check_continuity(relay)
    assert msgs == ["shot S2: 有尾帧续接但没写 entry_state——接尾帧的镜要写清入口（即源镜收尾那一刻：谁站在哪、手里有什么、脸朝哪看着什么）"]
    assert i18n.tr(msgs[0], "en").startswith("shot S2: has a tail relay but no entry_state")
    # 隔镜续接：S3 接 S1 尾帧，S2 夹在中间——S3 的「上一镜」是 S1（接缝另一半），S2 的仍是 S1（列表顺序）
    relay["shots"].insert(1, {"id": "S1b", "dur": 4, "cast": ["lin"], "shot": "全景", "art": "x", "motion": "m", "exit_state": "他在窗边"})
    relay["shots"][2]["entry_state"] = "她从门口转身走进来"
    assert continuity.previous_for(relay, relay["shots"][2]) is relay["shots"][0]
    assert continuity.previous_for(relay, relay["shots"][1]) is relay["shots"][0]
    assert not any("S2" in m and ("接缝只有一半" in m or "一字不差" in m) for m in continuity.check_continuity(relay))


# ---------------------------------------------------------------- 编辑接口

client = TestClient(app)


@pytest.fixture
def project(tmp_path, monkeypatch):
    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(module, "STORYVIDEO_DIR", content_dir)
    raw = {"id": "TR-API", "output_root": "output/story_video/TR-API", "prompt_language": "zh", "camera_profile": "dark_comedy_vertical",
           "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
           "cast": {"lin": {"name": "林小满", "block": "林小满：黑发。", "asset": "content/assets/lin.png"}},
           "shots": [_shot("S1"), _shot("S2"), _shot("S3")]}
    path = content_dir / "TR-API.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def test_api_roundtrip_and_validation(project):
    url = "/api/episodes/storyvideo/TR-API/shots/S2"
    resp = client.put(url, json={"ref_tail": " S1 ", "reference_roles": [{"target": "tail", "use": "只取场景"}]})
    assert resp.status_code == 200, resp.text
    shot = resp.json()["shot"]
    assert shot["ref_tail"] == {"shot": "S1", "mode": "reference"}
    assert shot["reference_slots"][0]["target"] == "tail" and shot["reference_slots"][0]["source"] == "S1"
    assert "参考图1（上一镜 S1 尾帧）：只取场景。" in shot["frame_prompt"]
    saved = json.loads(project.read_text(encoding="utf-8"))
    assert saved["shots"][1]["ref_tail"] == {"shot": "S1", "mode": "reference"}
    # 切成 direct：尾帧不再占参考槽，tail 用途要一起清；不清 → spec 校验拒、400 不写盘
    r = client.put(url, json={"ref_tail": {"shot": "S1", "mode": "direct"}})
    assert r.status_code == 400 and "needs ref_tail in reference mode" in r.json()["detail"]
    assert json.loads(project.read_text(encoding="utf-8"))["shots"][1]["ref_tail"]["mode"] == "reference"
    resp = client.put(url, json={"ref_tail": {"shot": "S1", "mode": "direct"}, "reference_roles": []})
    assert resp.status_code == 200 and resp.json()["shot"]["ref_tail"] == {"shot": "S1", "mode": "direct"}
    assert resp.json()["shot"]["reference_slots"][0]["target"] == "cast:lin"
    detail = client.get("/api/episodes/storyvideo/TR-API").json()
    assert next(s for s in detail["shots"] if s["id"] == "S2")["ref_tail"] == {"shot": "S1", "mode": "direct"}
    assert next(s for s in detail["shots"] if s["id"] == "S1")["ref_tail"] is None
    # 清掉：{} 或 ""
    assert client.put(url, json={"ref_tail": ""}).status_code == 200
    assert "ref_tail" not in json.loads(project.read_text(encoding="utf-8"))["shots"][1]
    for bad, needle, code in (
        ({"shot": "S1", "mode": "copy"}, "ref_tail.mode 只能是", 400),
        ({"shot": "S1", "nope": 1}, "ref_tail 里不认识的字段", 400),
        ({"shot": 3}, "ref_tail.shot 要是镜号", 400),
        ("S3", "not earlier in the episode", 400),        # 源镜在本镜之后
        ("S2", "must not reference itself", 400),
        (["S1"], "请求参数不对", 422),
    ):
        r = client.put(url, json={"ref_tail": bad})
        assert r.status_code == code, (bad, r.text)
        assert needle in r.json()["detail"], (bad, r.json()["detail"])
    assert "ref_tail" not in json.loads(project.read_text(encoding="utf-8"))["shots"][1]
    assert i18n.tr("ref_tail.mode 只能是 reference（作参考图）/ direct（直接当首帧），收到「copy」", "en").startswith("ref_tail.mode must be")
