"""storyvideo.asr_qc：台词识别质检的纯函数部分（不加载模型）。"""
import json

from storyvideo.asr_qc import cer, failed_shot_ids, judge, normalize, retake_reason, run_asr_qc


def test_normalize_strips_punctuation_and_folds_traditional():
    assert normalize("大家們，好。") == "大家们好"
    assert normalize("……天哪。") == "天哪"


def test_cer_tolerates_asr_noise_but_catches_gibberish():
    assert cer("我们出发吧。", "我们出发吧") == 0.0
    assert cer("大家好。今天也是被闹钟吵醒的一天。", "大家好今天也是被闹中吵醒了一天") <= 0.3   # 同音字噪声
    assert cer("……再等一会儿。", "嗯,對對") >= 0.9                                     # 念错
    assert cer("", "随便") is None


def test_judge_statuses():
    assert judge("不冷了。", "不冷了")["status"] == "pass"
    assert judge("……天哪。", "")["status"] == "silent"
    assert judge("慢点走。前面在修路吧。", "咖啡杯子从第二次开始主人向东走")["status"] == "fail"
    assert judge("", "欸~~")["status"] == "no_lines"          # 一两个字的叹词不算多余语音
    assert judge("", "这是谁的包")["status"] == "stray"


def test_run_asr_qc_writes_report_and_flags_failures(tmp_path):
    out = tmp_path / "output" / "EP"
    (out / "02_video").mkdir(parents=True)
    for sid in ("S1", "S2", "S3"):
        (out / "02_video" / f"{sid}.mp4").write_bytes(b"x")
    spec = {"output_root": str(out), "shots": [
        {"id": "S1", "lines": [{"who": "a", "zh": "我们出发吧。"}]},
        {"id": "S2", "lines": [{"who": "a", "zh": "再等一会儿。"}]},
        {"id": "S3", "lines": []},
        {"id": "S4", "lines": [{"who": "a", "zh": "没生成的镜。"}]},
    ]}
    heard = {"S1.mp4": "我们出发吧", "S2.mp4": "嗯对对", "S3.mp4": "这是谁的包裹呢"}
    report = run_asr_qc(spec, transcribe=lambda p: heard[p.name])
    assert report["summary"] == {"with_lines": 2, "pass": 1, "review": 0, "fail": 1, "stray": 1, "missing_video": 1}
    assert failed_shot_ids(report) == ["S2", "S3"]
    assert failed_shot_ids(report, include_stray=False) == ["S2"]
    saved = json.loads((out / "manifests" / "asr_qc.json").read_text(encoding="utf-8"))
    assert saved["rows"][1]["heard"] == "嗯对对"
    assert "字错率" in retake_reason(saved["rows"][1]) and "没有识别到" not in retake_reason(saved["rows"][1])


def test_dialogue_clarity_warnings():
    from storyvideo.preview import check_dialogue_clarity
    spec = {"shots": [
        {"id": "S1", "lines": [{"who": "a", "zh": "再等一会儿。", "tone": "含糊不清，气声，很小声"}]},
        {"id": "S2", "lines": [{"who": "a", "zh": "……哦。"}]},
        {"id": "S3", "speech_mode": "offscreen_narration", "lines": [{"who": "a", "zh": "天哪。"}]},
        {"id": "S4", "lines": [{"who": "a", "zh": "我们出发吧。", "tone": "轻快，尾音上扬，口齿清楚"}]},
    ]}
    w = check_dialogue_clarity(spec)
    assert any("S1" in x and "含糊" in x for x in w)
    assert any("S2" in x and "只有 1 个字" in x for x in w)
    assert any("S3" in x and "画外音短句" in x for x in w)
    assert not any("S4" in x for x in w)


def test_auto_dialogue_block_has_clarity_baseline():
    from storyvideo.prompts import compile_video_prompt
    shot = {"id": "S1", "cast": ["w"], "shot": "x", "art": "y", "motion": "她说。", "lines": [{"who": "w", "zh": "我们出发吧。"}]}
    spec = {"prompt_language": "zh", "dialogue_prompt": "auto", "cast": {"w": {"name": "阿宁"}}, "shots": [shot]}
    assert "口齿清楚" in compile_video_prompt(spec, "S1")


def test_judge_review_band_for_homophone_noise():
    # 同音字：雨伞→与散、钥匙→要是，字错率 0.333，人耳能听懂 → 待复核，不自动重摇
    r = judge("出门前记得带上雨伞和钥匙。", "出门前记得带上与散和要是")
    assert r["status"] == "review" and 0.3 < r["cer"] <= 0.45


def test_cli_summary_tolerates_result_without_run_dir(capsys):
    # asr-qc 的结果没有 run_dir（它不属于某个 run 目录），_summary 曾在这里 KeyError
    from storyvideo.cli import _summary
    _summary({"run_id": "EP", "status": "asr-qc pass=24 fail=0 review=1 stray=0 missing=0"})
    assert "asr-qc pass=24" in capsys.readouterr().out
