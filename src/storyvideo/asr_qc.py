"""台词识别质检（ASR QC）——视频出来之后，把每镜的音轨识别成文字，跟 spec 里的台词比对。

有台词的镜里常有相当一部分被模型念错、念成乱语或干脆没念；
成片装配前没有任何一道闸能发现这件事，看成片才听出来。这一层把它变成机器闸：
字错率超过阈值 → 自动记「重摇」，附上「期望 / 听到」，人只需要重出，不用逐条听。

引擎：faster-whisper（离线，CPU int8）；
模型第一次用会从 HuggingFace 拉一次（small 约 460MB），之后走本地缓存。
纯函数（cer / normalize / judge）不依赖模型，可单测；transcribe 才需要模型。
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

DEFAULT_MODEL = "small"
DEFAULT_THRESHOLD = 0.3   # 字错率 ≤ 30% 判通过；ASR 自身对同音字/繁简有噪声，20% 以内基本都是识别噪声
REVIEW_BAND = 0.45        # 30%—45% 判「待人耳复核」（错字以同音字为主、人耳能听懂的那种），不自动记重摇
STRAY_SPEECH_MIN_CHARS = 3  # 无台词镜里识别出 ≥3 个字 → 多余语音（模型自己编了台词）

_STRIP_RE = re.compile(r"[^一-鿿0-9A-Za-z]")


def normalize(text: str) -> str:
    """只留汉字/数字/字母，去标点空格；繁体按常见对照折成简体后再比（ASR 常吐繁体）。"""

    return _STRIP_RE.sub("", _to_simplified(text or ""))


_T2S = str.maketrans(
    "們這說時們來個為裡後開關點東西與過還沒對會麼樣經現們兒問題頭見聽發長間動氣認識覺實體業從嗎啊喔們車門電話錢買賣紅綠燈狗媽爸謝寶貝",
    "们这说时们来个为里后开关点东西与过还没对会么样经现们儿问题头见听发长间动气认识觉实体业从吗啊哦们车门电话钱买卖红绿灯狗妈爸谢宝贝",
)


def _to_simplified(text: str) -> str:
    return text.translate(_T2S)


def cer(expected: str, heard: str) -> float | None:
    """字错率：1 - 最长公共子串块匹配数 / 期望字数。期望为空返回 None。"""

    exp, got = normalize(expected), normalize(heard)
    if not exp:
        return None
    matcher = difflib.SequenceMatcher(None, exp, got, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return round(1.0 - matched / len(exp), 3)


def judge(expected: str, heard: str, *, threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """一镜的判定。返回 {status: pass|fail|stray|silent|no_lines, cer, expected, heard}。"""

    value = cer(expected, heard)
    if value is None:
        stray = len(normalize(heard)) >= STRAY_SPEECH_MIN_CHARS
        return {"status": "stray" if stray else "no_lines", "cer": None, "expected": expected, "heard": heard}
    if not normalize(heard):
        return {"status": "silent", "cer": 1.0, "expected": expected, "heard": heard}
    if value <= threshold:
        status = "pass"
    elif value <= REVIEW_BAND:
        status = "review"
    else:
        status = "fail"
    return {"status": status, "cer": value, "expected": expected, "heard": heard}


def transcribe_file(path: str | Path, *, model: str = DEFAULT_MODEL, language: str = "zh") -> str:
    """用 faster-whisper 把一个媒体文件的语音识别成一段文字（各段拼接）。"""

    from faster_whisper import WhisperModel  # 延迟导入：没装也不影响其他功能

    engine = _model_cache.setdefault(model, WhisperModel(model, device="cpu", compute_type="int8"))
    segments, _info = engine.transcribe(str(path), language=language, beam_size=5, vad_filter=True)
    return "".join(segment.text for segment in segments)


_model_cache: dict[str, Any] = {}


def run_asr_qc(
    spec: Mapping[str, Any],
    *,
    shots: Iterable[str] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    model: str = DEFAULT_MODEL,
    transcribe: Callable[[Path], str] | None = None,
) -> dict[str, Any]:
    """对一集已生成的视频逐镜识别并判定。不改任何状态，只返回报告并写 manifests/asr_qc.json。

    ``transcribe`` 可注入（测试用）；默认走 faster-whisper。
    """

    output_root = Path(spec["output_root"])
    wanted = set(shots) if shots else None
    fn = transcribe or (lambda p: transcribe_file(p, model=model))
    rows: list[dict[str, Any]] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        shot_id = str(shot.get("id"))
        if wanted is not None and shot_id not in wanted:
            continue
        video = output_root / "02_video" / f"{shot_id}.mp4"
        expected = "".join(str(line.get("zh") or "") for line in (shot.get("lines") or ()) if isinstance(line, Mapping))
        if not video.is_file():
            rows.append({"shot_id": shot_id, "status": "missing_video", "cer": None, "expected": expected, "heard": ""})
            continue
        heard = fn(video)
        rows.append({"shot_id": shot_id, **judge(expected, heard, threshold=threshold)})
    report = {
        "schema_version": 1,
        "engine": "faster-whisper",
        "model": model,
        "threshold": threshold,
        "rows": rows,
        "summary": {
            "with_lines": sum(1 for r in rows if r["status"] in {"pass", "review", "fail", "silent"}),
            "pass": sum(1 for r in rows if r["status"] == "pass"),
            "review": sum(1 for r in rows if r["status"] == "review"),
            "fail": sum(1 for r in rows if r["status"] in {"fail", "silent"}),
            "stray": sum(1 for r in rows if r["status"] == "stray"),
            "missing_video": sum(1 for r in rows if r["status"] == "missing_video"),
        },
    }
    manifests = output_root / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / "asr_qc.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def failed_shot_ids(report: Mapping[str, Any], *, include_stray: bool = True) -> list[str]:
    bad = {"fail", "silent"} | ({"stray"} if include_stray else set())
    return [r["shot_id"] for r in report.get("rows", ()) if r.get("status") in bad]


def retake_reason(row: Mapping[str, Any]) -> str:
    status = row.get("status")
    if status == "silent":
        return f"ASR 质检：没有识别到语音（期望「{row.get('expected')}」）"
    if status == "stray":
        return f"ASR 质检：无台词镜里出现语音「{row.get('heard')}」"
    return f"ASR 质检：字错率 {row.get('cer')}（期望「{row.get('expected')}」，听到「{row.get('heard')}」）"


__all__ = ["cer", "judge", "normalize", "run_asr_qc", "failed_shot_ids", "retake_reason", "transcribe_file"]
