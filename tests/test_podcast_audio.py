"""配音与混音（docs/podcast-spec.md §4）：占位音色轮流分配、逐句合成与哈希跳过、过时判定、时间线（停顿 / 片头片尾）、
ffmpeg 命令形状（垫乐压低）、假 runner 混音落盘节目单 / 文字稿 / 章节、任务体把混音失败当非致命。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast import audio as A
from podcast import spec as S

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "podcast" / "short_video_demo.json"
MP3 = b"ID3" + b"\x00" * 64


class FakeSynth:
    def __init__(self):
        self.calls: list[tuple[dict, str, str]] = []
        self.fail_on: str | None = None

    def __call__(self, profile, text, destination: Path):
        self.calls.append((dict(profile), text, destination.name))
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("tts down")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(MP3 + text.encode("utf-8"))
        return destination


def fake_prober(path: Path) -> float:
    return round(0.5 + len(path.read_bytes()) / 100.0, 3)


def spec_of(tmp_path, **extra) -> dict:
    data = json.loads(EXAMPLE.read_text(encoding="utf-8-sig"))
    data.update(extra)
    return S.normalize(data, project_root=tmp_path, spec_id="PC-A")


def small(tmp_path, **extra) -> dict:
    data = {
        "title": "小", "format": "duo", "target_minutes": 1,
        "hosts": [{"id": "a", "name": "阿一"}, {"id": "b", "name": "阿二"}],
        "segments": [
            {"id": "G01", "kind": "cold_open", "title": "冷", "lines": [{"who": "a", "text": "第一句。"}, {"who": "b", "text": "第二句。", "cue": "停顿"}]},
            {"id": "G02", "kind": "intro", "title": "开", "lines": [{"who": "a", "text": "第三句。"}]},
        ],
    }
    data.update(extra)
    return S.normalize(data, project_root=tmp_path, spec_id="PC-S")


def test_host_profiles_placeholder_rotation_library_and_inline(tmp_path):
    spec = spec_of(tmp_path)
    profiles = A.host_profiles(spec, lookup=lambda _: None)
    assert profiles["zhou"]["placeholder"] and profiles["xiao"]["placeholder"]
    assert profiles["zhou"]["voice"] != profiles["xiao"]["voice"]
    spec["hosts"][0]["voice"] = "lib"
    spec["hosts"][1]["voice"] = {"engine": "edge", "voice": "zh-CN-XiaoyiNeural", "rate": "+2%"}
    profiles = A.host_profiles(spec, lookup=lambda vid: {"id": vid, "name": "库里的", "engine": "minimax", "voice": "mm-1"} if vid == "lib" else None)
    assert profiles["zhou"] == {"id": "lib", "name": "库里的", "engine": "minimax", "voice": "mm-1", "label": "库里的", "placeholder": False,
                                "profile_id": "lib"}
    assert profiles["xiao"]["voice"] == "zh-CN-XiaoyiNeural" and profiles["xiao"]["placeholder"] is False
    en = spec_of(tmp_path, language="en")
    assert A.host_profiles(en, lookup=lambda _: None)["zhou"]["voice"].startswith("en-")


def test_synthesize_skips_unchanged_and_isolates_failures(tmp_path):
    spec = small(tmp_path)
    synth = FakeSynth()
    result = A.synthesize_lines(spec, synthesizer=synth, prober=fake_prober, lookup=lambda _: None)
    assert result["synthesized"] == ["G01_01.mp3", "G01_02.mp3", "G02_01.mp3"] and result["failed"] == []
    assert (tmp_path / "output" / "podcast" / "PC-S" / "07_audio" / "G01_02.mp3").is_file()
    manifest = A.load_manifest(spec)
    assert [e["who"] for e in manifest["lines"]] == ["a", "b", "a"] and manifest["lines"][1]["cue"] == "停顿"
    assert manifest["hosts"]["a"]["placeholder"] is True
    assert not A.is_stale(spec, lookup=lambda _: None)
    # 没变就跳过；改一句只重合成那一句
    again = A.synthesize_lines(spec, synthesizer=synth, prober=fake_prober, lookup=lambda _: None)
    assert again["skipped"] == ["G01_01.mp3", "G01_02.mp3", "G02_01.mp3"] and len(synth.calls) == 3
    spec["segments"][1]["lines"][0]["text"] = "改过的第三句。"
    assert A.is_stale(spec, lookup=lambda _: None)
    third = A.synthesize_lines(spec, synthesizer=synth, prober=fake_prober, lookup=lambda _: None)
    assert third["synthesized"] == ["G02_01.mp3"] and third["skipped"] == ["G01_01.mp3", "G01_02.mp3"]
    # 换音色也算变
    spec["hosts"][0]["voice"] = {"engine": "edge", "voice": "zh-CN-YunyangNeural"}
    assert A.is_stale(spec, lookup=lambda _: None)
    synth.fail_on = "第一句"
    failed = A.synthesize_lines(spec, synthesizer=synth, prober=fake_prober, lookup=lambda _: None, force=True)
    assert failed["failed"][0]["segment"] == "G01" and "tts down" in failed["failed"][0]["error"]
    assert "G02_01.mp3" in failed["synthesized"]


def test_timeline_pauses_chapters_and_music_slots(tmp_path):
    spec = small(tmp_path)
    A.synthesize_lines(spec, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None)
    manifest = A.load_manifest(spec)
    durations = {e["file"]: e["duration"] for e in manifest["lines"]}
    tl = A.build_timeline(spec, manifest)
    items = [i for i in tl["items"] if i["kind"] == "line"]
    assert items[0]["start"] == 0.0
    # 第二句：句间 0.35 + 停顿 0.6
    assert items[1]["start"] == pytest.approx(durations["G01_01.mp3"] + 0.35 + 0.6, abs=1e-3)
    # 第二个环节：环节间 1.2
    assert items[2]["start"] == pytest.approx(items[1]["start"] + durations["G01_02.mp3"] + 1.2, abs=1e-3)
    assert [c["id"] for c in tl["chapters"]] == ["G01", "G02"] and tl["chapters"][1]["start"] == items[2]["start"]
    assert tl["total"] == pytest.approx(items[2]["start"] + durations["G02_01.mp3"] + A.TAIL_SECONDS, abs=1e-3)
    # 片头曲放在冷开场之后、开场之前；片尾曲在最后
    with_music = A.build_timeline(spec, manifest, music_durations={"intro": 5.0, "outro": 3.0})
    kinds = [(i["kind"], i.get("slot")) for i in with_music["items"]]
    assert kinds == [("line", None), ("line", None), ("music", "intro"), ("line", None), ("music", "outro")]
    intro = with_music["items"][2]
    assert with_music["items"][3]["start"] == pytest.approx(intro["start"] + 5.0 + A.INTRO_GAP + 1.2, abs=1e-3)
    # 没有冷开场：片头曲放最前面
    no_cold = small(tmp_path, segments=[{"id": "G02", "kind": "intro", "lines": [{"who": "a", "text": "第三句。"}]}])
    A.synthesize_lines(no_cold, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None)
    first = A.build_timeline(no_cold, A.load_manifest(no_cold), music_durations={"intro": 2.0})
    assert first["items"][0]["kind"] == "music" and first["items"][0]["start"] == A.INTRO_GAP
    # 缺句子先合成
    spec["segments"][1]["lines"].append({"who": "b", "text": "第四句。", "cue": "", "kind": "talk"})
    with pytest.raises(A.AudioError, match="第 2 句还没合成"):
        A.build_timeline(spec, manifest)


def test_mix_command_shape(tmp_path):
    spec = small(tmp_path, music={"bed": "content/assets/PC-S/bed.mp3", "bed_gain": 0.2, "duck": 0.4})
    bed = tmp_path / "content" / "assets" / "PC-S" / "bed.mp3"
    bed.parent.mkdir(parents=True)
    bed.write_bytes(MP3)
    A.synthesize_lines(spec, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None)
    tl = A.build_timeline(spec, A.load_manifest(spec), music_durations={"intro": 2.0})
    cmd = A.mix_command(spec, tl, ffmpeg="ffmpeg", music={"bed": bed, "intro": bed, "outro": None})
    assert cmd[0] == "ffmpeg" and cmd[-1].endswith("PC-S.mp3") and "-stream_loop" in cmd
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.count("adelay=") == 3 + 1 + 1     # 三句 + 片头 + 垫乐
    assert "volume=0.200" in graph and "if(between(t,0.000," in graph and ",0.400,1)" in graph
    assert "amix=inputs=6:duration=first:normalize=0:dropout_transition=0," in graph
    # 成品统一到播客常用响度（-16 LUFS、真峰值 -1.5 dBTP），再回 44.1k（loudnorm 内部会升采样）
    assert graph.endswith("loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100[out]")
    assert "libmp3lame" in cmd and cmd[cmd.index("-t") + 1] == f"{tl['total']:.3f}"


def test_mix_with_fake_runner_writes_notes_transcript_and_chapters(tmp_path):
    spec = spec_of(tmp_path)
    A.synthesize_lines(spec, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None)
    commands = []

    def runner(command):
        commands.append(list(command))
        Path(command[-1]).write_bytes(MP3)

    result = A.mix(spec, ffmpeg="ffmpeg", runner=runner, prober=fake_prober)
    assert len(commands) == 1 and Path(result["mix"]).is_file()
    out = tmp_path / "output" / "podcast" / "PC-A" / "08_mix"
    notes = (out / A.SHOWNOTES_NAME).read_text(encoding="utf-8")
    assert notes.startswith("# 为什么我们戒不掉短视频") and "## 章节" in notes and "- 00:00 凌晨两点十七" in notes
    assert "## 听友问题" in notes and "听友阿May：把手机设成黑白真的有用吗？" in notes and "## 听完带走" in notes
    transcript = (out / A.TRANSCRIPT_NAME).read_text(encoding="utf-8")
    assert "[00:00] 小夏：我昨天晚上看了眼时间，两点十七。" in transcript and "## [" in transcript
    chapters = json.loads((out / A.CHAPTERS_NAME).read_text(encoding="utf-8"))
    assert [c["id"] for c in chapters["chapters"]] == ["G01", "G02", "G03", "G04", "G05", "G06"] and chapters["total"] == result["duration"]
    state = A.status(spec, lookup=lambda _: None)
    assert state["mixed"] and state["synthesized"] == 41 and state["total"] == 41 and not state["stale"]
    assert state["texts"][A.SHOWNOTES_NAME] and state["mix_duration"] == result["duration"]
    assert state["hosts"]["zhou"]["placeholder"]


def test_run_synthesize_treats_missing_ffmpeg_as_soft_failure(tmp_path, monkeypatch):
    spec = small(tmp_path)
    monkeypatch.setattr(A.shutil, "which", lambda _name: None)
    result = A.run_synthesize(spec, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None)
    assert result["synthesized"] and result["mixed"] is False and "没找到 ffmpeg" in result["mix_error"]
    # 一句失败就不混音
    synth = FakeSynth()
    synth.fail_on = "第三句"
    result = A.run_synthesize(spec, synthesizer=synth, prober=fake_prober, lookup=lambda _: None, force=True, ffmpeg="ffmpeg", runner=lambda c: None)
    assert result["mixed"] is False and "1 句没合成出来" in result["mix_error"]
    # 全部成功 + 假 runner → 混好
    ok = A.run_synthesize(spec, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None, force=True, ffmpeg="ffmpeg",
                          runner=lambda c: Path(c[-1]).write_bytes(MP3))
    assert ok["mixed"] and ok["chapters"][0]["id"] == "G01"


# ---------------------------------------------------------------------------- 2026-09-22 发第一个 tag 前的审计修复


def test_zero_byte_or_truncated_line_counts_as_not_synthesized(tmp_path):
    """PC-03：合成到一半留下的 0 字节 / 半截 mp3 不能再算「已合成」：状态、过时、跳过、混音四处一个口径，再合成只重做那一句。"""

    spec = small(tmp_path)
    synth = FakeSynth()
    A.synthesize_lines(spec, synthesizer=synth, prober=fake_prober, lookup=lambda _: None)
    manifest = A.load_manifest(spec)
    assert all(e["size"] > 0 for e in manifest["lines"])
    line = tmp_path / "output" / "podcast" / "PC-S" / "07_audio" / "G01_01.mp3"
    line.write_bytes(b"")
    state = A.status(spec, lookup=lambda _: None)
    assert state["synthesized"] == 2 and state["total"] == 3 and state["stale"] is True
    assert state["lines"][0]["file"] is None and state["lines"][0]["stale"] is True and state["lines"][1]["stale"] is False
    assert A.is_stale(spec, lookup=lambda _: None)
    # 混音前拦住，给人能照做的话，而不是 ffmpeg 的「Failed to read frame size」
    with pytest.raises(A.AudioError, match="环节 G01 第 1 句的音频文件损坏（0 字节）——再点一次「合成整期」会只重合成这一句"):
        A.build_timeline(spec, manifest)
    # 不带 force 再合成：只重做这一句
    again = A.synthesize_lines(spec, synthesizer=synth, prober=fake_prober, lookup=lambda _: None)
    assert again["synthesized"] == ["G01_01.mp3"] and again["skipped"] == ["G01_02.mp3", "G02_01.mp3"]
    assert not A.is_stale(spec, lookup=lambda _: None)
    # 半截文件（大小跟清单记的对不上）同样算坏
    line.write_bytes(line.read_bytes()[:12])
    assert A.status(spec, lookup=lambda _: None)["synthesized"] == 2
    with pytest.raises(A.AudioError, match="损坏（12 字节）"):
        A.build_timeline(spec, A.load_manifest(spec))
    # 探不出时长（时长 0）的合成结果不进清单
    zero = A.synthesize_lines(spec, synthesizer=FakeSynth(), prober=lambda p: 0.0, lookup=lambda _: None, force=True)
    assert len(zero["failed"]) == 3 and "探不出时长" in zero["failed"][0]["error"]


def test_mix_fingerprint_tracks_pause_music_cue_and_lines(tmp_path):
    """PC-04：成品指纹——什么都不改指纹不变；改停顿 / 换垫乐 / 挂着垫乐改 bed_gain·duck / 改 cue / 某句重合成各变一次；
    没挂垫乐时 bed_gain / duck 不进 ffmpeg 命令，改它们指纹也不变。"""

    spec = small(tmp_path)
    A.synthesize_lines(spec, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None)
    manifest = A.load_manifest(spec)
    base = A.mix_fingerprint(spec, manifest)
    assert base == A.mix_fingerprint(spec, A.load_manifest(spec)) and len(base) == 24
    pause = S.normalize({**S.to_disk(spec), "pause": {"line": 2.0, "segment": 1.2}}, project_root=tmp_path, spec_id="PC-S")
    assert A.mix_fingerprint(pause, manifest) != base
    # 没垫乐：拖垫乐音量 / 压低比例不改成品，不该把成品标成过时（审查实撞：音频页拖一下滑块就提示「按旧参数混的」）
    for key, value in (("bed_gain", 0.5), ("duck", 0.9)):
        no_bed = S.normalize({**S.to_disk(spec), "music": {**spec["music"], key: value}}, project_root=tmp_path, spec_id="PC-S")
        assert A.mix_fingerprint(no_bed, manifest) == base, key
    bed = tmp_path / "content" / "assets" / "PC-S" / "bed.mp3"
    bed.parent.mkdir(parents=True)
    bed.write_bytes(MP3)
    with_bed = S.normalize({**S.to_disk(spec), "music": {**spec["music"], "bed": "content/assets/PC-S/bed.mp3"}}, project_root=tmp_path, spec_id="PC-S")
    fp_bed = A.mix_fingerprint(with_bed, manifest)
    assert fp_bed != base
    for key, value in (("bed_gain", 0.3), ("duck", 0.9)):
        tuned = S.normalize({**S.to_disk(with_bed), "music": {**with_bed["music"], key: value}}, project_root=tmp_path, spec_id="PC-S")
        assert A.mix_fingerprint(tuned, manifest) != fp_bed, key
    bed.write_bytes(MP3 + b"longer")            # 同一路径换了内容（大小变了）也算
    assert A.mix_fingerprint(with_bed, manifest) != fp_bed
    cue = S.normalize({**S.to_disk(spec)}, project_root=tmp_path, spec_id="PC-S")
    cue["segments"][0]["lines"][0]["cue"] = "停顿"
    assert A.mix_fingerprint(cue, manifest) != base
    changed = S.normalize({**S.to_disk(spec)}, project_root=tmp_path, spec_id="PC-S")
    changed["segments"][1]["lines"][0]["text"] = "改过的第三句。"
    A.synthesize_lines(changed, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None)
    assert A.mix_fingerprint(changed, A.load_manifest(changed)) != base


def test_status_reports_mix_stale_after_pause_change_until_remixed(tmp_path):
    """PC-04：混音把指纹写进 chapters.json；只改停顿后详情 mix_stale=true（成品仍可试听），再混一次就恢复；
    老 chapters.json 没记指纹也算过时；输入没变而 ffmpeg 临时失败，旧成品仍然是对的。"""

    spec = small(tmp_path)
    runner = lambda c: Path(c[-1]).write_bytes(MP3)  # noqa: E731
    ok = A.run_synthesize(spec, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None, ffmpeg="ffmpeg", runner=runner)
    assert ok["mixed"]
    chapters = json.loads((S.mix_dir(spec) / A.CHAPTERS_NAME).read_text(encoding="utf-8"))
    assert chapters["mix_hash"] == A.mix_fingerprint(spec, A.load_manifest(spec))
    state = A.status(spec, lookup=lambda _: None)
    assert state["mixed"] and state["mix_stale"] is False
    pause = S.normalize({**S.to_disk(spec), "pause": {"line": 2.0, "segment": 1.2}}, project_root=tmp_path, spec_id="PC-S")
    state = A.status(pause, lookup=lambda _: None)
    assert state["mixed"] and state["mix_stale"] is True and state["stale"] is False and state["mix_file"] == "PC-S.mp3"
    again = A.run_synthesize(pause, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None, ffmpeg="ffmpeg", runner=runner)
    assert again["skipped"] == ["G01_01.mp3", "G01_02.mp3", "G02_01.mp3"] and again["mixed"]
    assert A.status(pause, lookup=lambda _: None)["mix_stale"] is False
    chapters = json.loads((S.mix_dir(spec) / A.CHAPTERS_NAME).read_text(encoding="utf-8"))
    chapters.pop("mix_hash")
    (S.mix_dir(spec) / A.CHAPTERS_NAME).write_text(json.dumps(chapters), encoding="utf-8")
    assert A.status(pause, lookup=lambda _: None)["mix_stale"] is True
    A.run_synthesize(pause, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None, ffmpeg="ffmpeg", runner=runner)

    def boom(command):
        raise A.AudioError("混音失败：boom")

    broken = A.run_synthesize(pause, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None, ffmpeg="ffmpeg", runner=boom)
    assert broken["mixed"] is False and A.status(pause, lookup=lambda _: None)["mix_stale"] is False
    (S.mix_dir(spec) / "PC-S.mp3").unlink()
    assert A.status(pause, lookup=lambda _: None)["mix_stale"] is False


def test_unreadable_music_fails_the_mix_with_the_slot_named(tmp_path, monkeypatch):
    """PC-05：片头 / 垫乐 / 片尾任何一个槽探不出时长，混音就报错点名那个槽——不再把片头静默丢掉、任务照样 succeeded。"""

    spec = small(tmp_path, music={"intro": "content/assets/PC-S/intro.mp3", "bed": "content/assets/PC-S/bed.mp3"})
    folder = tmp_path / "content" / "assets" / "PC-S"
    folder.mkdir(parents=True)
    (folder / "intro.mp3").write_bytes(b"not really audio")
    (folder / "bed.mp3").write_bytes(MP3)
    A.synthesize_lines(spec, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None)

    def prober(path: Path) -> float:
        if path.name == "intro.mp3":
            raise RuntimeError("Failed to read frame size: Could not seek to 1039.\nintro.mp3: Invalid argument")
        return fake_prober(path)

    commands = []
    with pytest.raises(A.AudioError) as exc:
        A.mix(spec, ffmpeg="ffmpeg", runner=lambda c: commands.append(c), prober=prober)
    assert str(exc.value) == ("片头音乐文件读不出（intro.mp3）：Failed to read frame size: Could not seek to 1039. intro.mp3: Invalid argument"
                              "——换一个文件，或在「音乐」里清掉这个槽")
    assert commands == []
    result = A.run_synthesize(spec, synthesizer=FakeSynth(), prober=prober, lookup=lambda _: None, ffmpeg="ffmpeg", runner=lambda c: None)
    assert result["mixed"] is False and result["mix_error"].startswith("片头音乐文件读不出（intro.mp3）")
    # 没装 ffmpeg（通常也没 ffprobe）：先说「没找到 ffmpeg」，别把探不了音乐当成音乐坏了
    with monkeypatch.context() as patch:
        patch.setattr(A.shutil, "which", lambda _name: None)
        no_ffmpeg = A.run_synthesize(spec, synthesizer=FakeSynth(), prober=prober, lookup=lambda _: None)
    assert no_ffmpeg["skipped"] and no_ffmpeg["mixed"] is False and no_ffmpeg["mix_error"].startswith("没找到 ffmpeg")
    # 垫乐也探（以前只探片头片尾，坏垫乐直接进 ffmpeg 报原话）；时长 0 也算读不出
    with pytest.raises(A.AudioError, match="垫乐音乐文件读不出（bed.mp3）：时长为 0"):
        A.mix(spec, ffmpeg="ffmpeg", runner=lambda c: None, prober=lambda p: 0.0 if p.name == "bed.mp3" else fake_prober(p))
    # 三个槽都好：片头进时间线
    (folder / "intro.mp3").write_bytes(MP3)

    def runner(command):
        commands.append(list(command))
        Path(command[-1]).write_bytes(MP3)

    fine = A.mix(spec, ffmpeg="ffmpeg", runner=runner, prober=fake_prober)
    assert fine["duration"] > 0 and str(folder / "intro.mp3") in commands[-1]


def test_host_profiles_flag_missing_and_description_only_profiles(tmp_path):
    """PC-06：档案 id 不存在 → 占位 + missing；档案只有描述（voice 空、也没克隆好）→ 占位 + no_tts；两人不同声；克隆好的档案算能出声。"""

    spec = small(tmp_path)
    spec["hosts"][0]["voice"] = "gone"
    spec["hosts"][1]["voice"] = "desc"
    library = {"desc": {"id": "desc", "name": "只有描述", "engine": "edge", "voice": "", "description": "低沉男声"},
               "cloned": {"id": "cloned", "name": "克隆的", "engine": "minimax", "voice": "", "clone": {"status": "ready", "voice_id": "mm-clone"}}}
    profiles = A.host_profiles(spec, lookup=library.get)
    a, b = profiles["a"], profiles["b"]
    assert a["placeholder"] and a["missing"] and a["profile_id"] == "gone" and a["label"] == f"档案「gone」已不存在，暂用占位音色 {a['voice']}"
    assert b["placeholder"] and b["no_tts"] and b["profile_id"] == "desc" and b["label"] == f"档案「只有描述」只有描述，暂用占位音色 {b['voice']}"
    assert a["voice"] != b["voice"] and not a.get("no_tts") and not b.get("missing")
    synth = FakeSynth()
    result = A.synthesize_lines(spec, synthesizer=synth, prober=fake_prober, lookup=library.get)
    assert result["failed"] == [] and result["placeholder"] == {"a": True, "b": True}
    used = {call[0]["voice"] for call in synth.calls}
    assert len(used) == 2 and "" not in used
    hosts = A.load_manifest(spec)["hosts"]
    assert hosts["a"]["missing"] is True and hosts["a"]["profile_id"] == "gone" and hosts["b"]["no_tts"] is True
    assert A.status(spec, lookup=library.get)["hosts"]["a"]["missing"] is True
    spec["hosts"][1]["voice"] = "cloned"
    cloned = A.host_profiles(spec, lookup=library.get)["b"]
    assert cloned["placeholder"] is False and cloned["profile_id"] == "cloned" and not cloned.get("no_tts")


def test_manifest_records_real_voice_and_profile_id(tmp_path):
    """PC-10：清单的 voice 是真正送去 TTS 的音色名，档案 id 单独记 profile_id；内联档案 / 占位的 profile_id 为空。"""

    spec = small(tmp_path)
    spec["hosts"][0]["voice"] = "lib"
    spec["hosts"][1]["voice"] = {"engine": "edge", "voice": "zh-CN-XiaoyiNeural"}

    def lookup(vid: str):
        return {"id": "lib", "name": "库里的", "engine": "edge", "voice": "zh-CN-YunyangNeural", "rate": "+25%"} if vid == "lib" else None

    A.synthesize_lines(spec, synthesizer=FakeSynth(), prober=fake_prober, lookup=lookup)
    manifest = A.load_manifest(spec)
    assert manifest["lines"][0]["voice"] == "zh-CN-YunyangNeural" and manifest["lines"][0]["profile_id"] == "lib"
    assert manifest["lines"][1]["voice"] == "zh-CN-XiaoyiNeural" and manifest["lines"][1]["profile_id"] == ""
    assert manifest["hosts"]["a"] == {"voice": "zh-CN-YunyangNeural", "engine": "edge", "placeholder": False, "label": "库里的", "profile_id": "lib",
                                      "missing": False, "no_tts": False}
    assert manifest["hosts"]["b"]["profile_id"] == "" and manifest["hosts"]["b"]["voice"] == "zh-CN-XiaoyiNeural"


_HAN = __import__("re").compile(r"[一-鿿]")


def test_english_episode_products_use_english_labels(tmp_path):
    """PC-07：language=en 的节目单 / 逐字稿 / 章节全用英文标签、半角冒号；中文期不变。"""

    data = {
        "title": "Why we scroll", "format": "duo", "language": "en", "target_minutes": 1,
        "brief": {"topic": "short video addiction", "takeaways": ["infinite scroll design"]},
        "hosts": [{"id": "a", "name": "Zhou", "role": "host"}, {"id": "b", "name": "Xia", "role": "cohost"}],
        "segments": [{"id": "G01", "kind": "cold_open", "lines": [{"who": "a", "text": "Two seventeen."}, {"who": "b", "text": "Again?"}]},
                     {"id": "G02", "kind": "qa", "title": "Listener mail", "questions": [{"from": "Sam", "text": "Is it the app or me?"}],
                      "lines": [{"who": "a", "text": "Sam asks whether it is the app or him."}]}],
    }
    spec = S.normalize(data, project_root=tmp_path, spec_id="PC-EN")
    A.synthesize_lines(spec, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None)
    result = A.mix(spec, ffmpeg="ffmpeg", runner=lambda c: Path(c[-1]).write_bytes(MP3), prober=fake_prober)
    assert [c["title"] for c in result["chapters"]] == ["Cold open", "Listener mail"]
    out = tmp_path / "output" / "podcast" / "PC-EN" / "08_mix"
    notes = (out / A.SHOWNOTES_NAME).read_text(encoding="utf-8")
    assert not _HAN.search(notes), notes
    assert "- Format: Two hosts · Duration 00:" in notes and "- Hosts: Zhou (Host), Xia (Co-host)" in notes
    assert "## Chapters" in notes and "- 00:00 Cold open" in notes and "## Takeaways" in notes
    assert "## Listener questions" in notes and "- Sam: Is it the app or me?" in notes
    assert notes.rstrip().endswith("the hosts' voices are synthetic.")
    transcript = (out / A.TRANSCRIPT_NAME).read_text(encoding="utf-8")
    assert not _HAN.search(transcript) and "## [00:00] Cold open" in transcript and "[00:00] Zhou: Two seventeen." in transcript
    chapters = json.loads((out / A.CHAPTERS_NAME).read_text(encoding="utf-8"))
    assert chapters["chapters"][0]["title"] == "Cold open"
    # 中文期照旧
    zh = small(tmp_path)
    A.synthesize_lines(zh, synthesizer=FakeSynth(), prober=fake_prober, lookup=lambda _: None)
    A.mix(zh, ffmpeg="ffmpeg", runner=lambda c: Path(c[-1]).write_bytes(MP3), prober=fake_prober)
    zh_notes = (tmp_path / "output" / "podcast" / "PC-S" / "08_mix" / A.SHOWNOTES_NAME).read_text(encoding="utf-8")
    assert "- 形式：双人对谈 · 时长 00:" in zh_notes and "- 主播：阿一（主播）、阿二（搭档）" in zh_notes and "## 章节" in zh_notes
