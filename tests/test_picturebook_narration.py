"""朗读合成（契约 §5）：假合成器注入、按句分文件、哈希去重、en_rate 叠加、失败隔离、手动回收认领。"""

from __future__ import annotations

from pathlib import Path

from picturebook import narration as N
from tests.picturebook_fixtures import small_book

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


def test_combine_rate():
    assert N.combine_rate("+5%", "-8%") == "-3%"
    assert N.combine_rate("", "-8%") == "-8%"
    assert N.combine_rate("-8%", "+8%") == ""
    assert N.combine_rate("fast", None) == ""


def test_voice_profile_applies_en_rate_and_placeholder(tmp_path):
    spec = small_book(tmp_path)
    spec["narration"].update({"zh_voice": "girl", "en_voice": "story", "en_rate": "-8%"})
    profiles = {"girl": {"id": "girl", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "+5%"},
                "story": {"id": "story", "engine": "edge", "voice": "en-US-AnaNeural", "rate": "+5%"}}
    zh = N.voice_profile(spec, "zh", lookup=profiles.get)
    en = N.voice_profile(spec, "en", lookup=profiles.get)
    assert zh["rate"] == "+5%" and zh["placeholder"] is False
    assert en["rate"] == "-3%" and en["placeholder"] is False
    spec["narration"]["en_voice"] = "missing"
    fallback = N.voice_profile(spec, "en", lookup=profiles.get)
    assert fallback["placeholder"] is True and fallback["engine"] == "edge" and fallback["rate"] == "-8%"


def test_synthesize_splits_sentences_dedupes_and_measures(tmp_path):
    spec = small_book(tmp_path)
    synth = FakeSynth()
    result = N.synthesize_narration(spec, synthesizer=synth, prober=fake_prober, lookup=lambda _: None)
    assert result["synthesized"] == ["P01_zh", "P01_en", "P02_zh", "P02_en", "P03_zh", "P03_en"] and result["failed"] == []
    assert result["placeholder"] == {"zh": True, "en": True}
    audio = tmp_path / "output" / "picturebook" / "PB-S-1" / "07_audio"
    assert (audio / "P01_zh.mp3").is_file() and (audio / "P02_en.mp3").is_file()
    assert (audio / "P03_zh_1.mp3").is_file() and (audio / "P03_zh_2.mp3").is_file() and not (audio / "P03_zh.mp3").exists()
    manifest = N.load_manifest(spec)
    p3 = manifest["pages"]["P03"]["zh"]
    assert [s["text"] for s in p3["segments"]] == ["小狐狸回家了。", "晚安。"] and p3["total"] > 0
    assert all(s["duration"] == fake_prober(audio / s["file"]) for s in p3["segments"])
    # en 用了 en_rate；zh 没用
    en_call = next(c for c in synth.calls if c[2] == "P01_en.mp3")
    assert en_call[0]["rate"] == "-8%" and next(c for c in synth.calls if c[2] == "P01_zh.mp3")[0]["rate"] == "-5%"

    # 第二次：全部跳过，合成器一次都不叫
    synth.calls.clear()
    again = N.synthesize_narration(spec, synthesizer=synth, prober=fake_prober, lookup=lambda _: None)
    assert again["synthesized"] == [] and len(again["skipped"]) == 6 and synth.calls == []

    # 改一页文本 → 只重合成那一页那一语言；force → 全部重来
    spec["pages"][1]["zh"] = "呼呼呼，小狐狸追着袜子跑。"
    third = N.synthesize_narration(spec, synthesizer=synth, prober=fake_prober, lookup=lambda _: None)
    assert third["synthesized"] == ["P02_zh"] and len(synth.calls) == 1
    synth.calls.clear()
    forced = N.synthesize_narration(spec, only=["P01"], languages=["en"], force=True, synthesizer=synth, prober=fake_prober, lookup=lambda _: None)
    assert forced["synthesized"] == ["P01_en"] and [c[2] for c in synth.calls] == ["P01_en.mp3"]

    status = N.status(spec)
    assert status["P03"]["zh"]["segments"] == 2 and status["P03"]["zh"]["done"] and status["P03"]["zh"]["placeholder"] is True
    segs = N.segments_for_timeline(spec)
    assert set(segs) == {"P01", "P02", "P03"} and len(segs["P03"]["zh"]) == 2


def test_failure_isolated_per_page(tmp_path):
    spec = small_book(tmp_path)
    synth = FakeSynth()
    synth.fail_on = "晚安"
    result = N.synthesize_narration(spec, synthesizer=synth, prober=fake_prober, lookup=lambda _: None)
    assert [f["page"] + "_" + f["lang"] for f in result["failed"]] == ["P03_zh"] and "tts down" in result["failed"][0]["error"]
    assert "P03_en" in result["synthesized"] and "P03" in N.load_manifest(spec)["pages"] and "zh" not in N.load_manifest(spec)["pages"]["P03"]


def test_sync_imported_claims_manual_page_audio(tmp_path):
    spec = small_book(tmp_path)
    audio = tmp_path / "output" / "picturebook" / "PB-S-1" / "07_audio"
    audio.mkdir(parents=True)
    (audio / "P02_zh.mp3").write_bytes(MP3)
    manifest = N.sync_imported(spec, prober=lambda p: 1.75)
    entry = manifest["pages"]["P02"]["zh"]
    assert entry["segments"][0]["source"] == "import" and entry["segments"][0]["duration"] == 1.75 and entry["segments"][0]["text"] == "呼呼呼，红袜子飞到哪儿去了？"
    assert N.status(spec)["P02"]["zh"]["done"] and not N.status(spec)["P02"]["en"]["done"]
    # 文件没了 → segments_for_timeline 不再带它
    (audio / "P02_zh.mp3").unlink()
    assert "P02" not in N.segments_for_timeline(spec)


def test_plan_rejects_unknown_pages(tmp_path):
    spec = small_book(tmp_path)
    try:
        N.plan(spec, only=["P09"])
    except N.NarrationError as exc:
        assert "没有这些页：P09" in str(exc)
    else:
        raise AssertionError("expected NarrationError")
    assert [(p["page"], p["lang"]) for p in N.plan(spec, languages=["en"])] == [("P01", "en"), ("P02", "en"), ("P03", "en")]
