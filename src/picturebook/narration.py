"""朗读合成（契约 §5）：每页每语言、按句一段，走音色库的 ``synthesize_with_profile``。

* 文件：单句页 ``07_audio/P01_zh.mp3``；多句页 ``P01_zh_1.mp3``、``P01_zh_2.mp3``…（逐句交替 / 当前句高亮都要按句的实测时长）
* 去重：同文本 + 同音色档案（引擎 / voice / rate / pitch）+ 同实际语速 → 同哈希，文件在就不重合成
* 时长：ffprobe 实测，记进 ``manifests/narration.json``；英文语速 = 档案 rate 叠加 ``narration.en_rate``
* 没在音色库里选音色时用 edge-tts 占位音色（``placeholder: true``），只为估时间轴，成片前该换成正式音色
* 手动放回的真人朗读（``P01_zh.mp3`` 直接放进 07_audio）：``sync_imported`` 认领成整页一段
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from storyvideo.state import atomic_write_json, read_json

from .spec import audio_dir, manifests_dir, page_ids
from .timeline import languages_for, split_sentences

Synthesizer = Callable[[dict[str, Any], str, Path], Any]
Prober = Callable[[Path], float]

PLACEHOLDER_PROFILES = {
    "zh": {"id": "", "name": "占位（edge-tts）", "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural", "rate": "-5%", "pitch": ""},
    "en": {"id": "", "name": "placeholder (edge-tts)", "engine": "edge", "voice": "en-US-AnaNeural", "rate": "", "pitch": ""},
}
_RATE = re.compile(r"^([+-]?)(\d{1,3})%$")
MAX_WAIT_PER_SEGMENT = 300


class NarrationError(RuntimeError):
    pass


def manifest_path(spec: Mapping[str, Any]) -> Path:
    return manifests_dir(spec) / "narration.json"


def load_manifest(spec: Mapping[str, Any]) -> dict[str, Any]:
    data = read_json(manifest_path(spec), missing=None)
    if not isinstance(data, dict):
        return {"pages": {}}
    data.setdefault("pages", {})
    return data


def _save_manifest(spec: Mapping[str, Any], manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = time.time()
    atomic_write_json(manifest_path(spec), manifest)


def combine_rate(base: str | None, extra: str | None) -> str:
    """两个百分比语速叠加：'+5%' + '-8%' → '-3%'；都空 → ''。不合法的当 0。"""

    total = 0
    for value in (base, extra):
        match = _RATE.match(str(value or "").strip())
        if match:
            total += int(match.group(2)) * (-1 if match.group(1) == "-" else 1)
    if total == 0:
        return ""
    return f"{'+' if total > 0 else '-'}{abs(total)}%"


def voice_profile(spec: Mapping[str, Any], lang: str, *, lookup: Callable[[str], dict[str, Any] | None] | None = None) -> dict[str, Any]:
    """这本书某语言的合成档案：音色库档案（英文叠加 en_rate）；没选或找不到 → 占位档案。"""

    narration = spec.get("narration") or {}
    voice_id = str(narration.get(f"{lang}_voice") or "").strip()
    profile: dict[str, Any] | None = None
    if voice_id:
        if lookup is None:
            from genkit.voices import get_profile

            lookup = get_profile
        profile = lookup(voice_id)
    placeholder = profile is None
    out = dict(profile) if profile else dict(PLACEHOLDER_PROFILES[lang])
    out["placeholder"] = placeholder
    if lang == "en":
        out["rate"] = combine_rate(out.get("rate"), narration.get("en_rate"))
    return out


def segment_hash(text: str, profile: Mapping[str, Any]) -> str:
    key = "|".join([
        text.strip(), str(profile.get("engine") or ""), str(profile.get("voice") or ""), str(profile.get("rate") or ""),
        str(profile.get("pitch") or ""), str((profile.get("clone") or {}).get("voice_id") or "") if isinstance(profile.get("clone"), Mapping) else "",
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def segment_filename(page_id: str, lang: str, index: int, count: int) -> str:
    return f"{page_id}_{lang}.mp3" if count == 1 else f"{page_id}_{lang}_{index}.mp3"


def probe_duration(path: Path, *, ffprobe: str = "ffprobe") -> float:
    from storyvideo.media import MediaValidationError, probe_media

    try:
        return float(probe_media(path, ffprobe=ffprobe).duration_seconds)
    except MediaValidationError as exc:
        raise NarrationError(f"测不出音频时长：{path.name}——{exc}") from exc


def plan(spec: Mapping[str, Any], *, only: list[str] | None = None, languages: list[str] | None = None) -> list[dict[str, Any]]:
    """要合成的 (页, 语言) 清单，每项带句列表。"""

    langs = [l for l in languages_for(str(spec.get("bilingual_mode") or "")) if not languages or l in languages]
    wanted = set(only) if only else None
    unknown = sorted(wanted - set(page_ids(spec))) if wanted else []
    if unknown:
        raise NarrationError(f"没有这些页：{', '.join(unknown)}")
    out: list[dict[str, Any]] = []
    for page in spec.get("pages") or ():
        pid = str(page["id"])
        if wanted and pid not in wanted:
            continue
        for lang in langs:
            sentences = split_sentences(str(page.get(lang) or ""), lang)
            if sentences:
                out.append({"page": pid, "lang": lang, "sentences": sentences})
    return out


def _entry_complete(entry: Mapping[str, Any] | None, hashes: list[str], root: Path) -> bool:
    if not entry or not isinstance(entry.get("segments"), list):
        return False
    segs = entry["segments"]
    if [str(s.get("hash") or "") for s in segs] != hashes:
        return False
    return all((root / str(s.get("file") or "")).is_file() for s in segs)


def synthesize_narration(spec: Mapping[str, Any], *, only: list[str] | None = None, languages: list[str] | None = None,
                         force: bool = False, synthesizer: Synthesizer | None = None, prober: Prober | None = None,
                         lookup: Callable[[str], dict[str, Any] | None] | None = None,
                         note: Callable[..., None] | None = None) -> dict[str, Any]:
    """后台任务体。``synthesizer(profile, text, destination)`` / ``prober(path) -> 秒`` 可注入（测试）。"""

    if synthesizer is None:
        from genkit.voices import synthesize_with_profile

        synthesizer = synthesize_with_profile
    prober = prober or probe_duration
    root = audio_dir(spec)
    root.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(spec)
    profiles = {lang: voice_profile(spec, lang, lookup=lookup) for lang in ("zh", "en")}
    items = plan(spec, only=only, languages=languages)
    done: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, Any]] = []
    for number, item in enumerate(items, 1):
        pid, lang, sentences = item["page"], item["lang"], item["sentences"]
        profile = profiles[lang]
        hashes = [segment_hash(s, profile) for s in sentences]
        page_entry = manifest["pages"].setdefault(pid, {})
        label = f"{pid}_{lang}"
        if not force and _entry_complete(page_entry.get(lang), hashes, root):
            skipped.append(label)
            continue
        if note:
            note(progress=f"{number}/{len(items)}", current=label)
        segments: list[dict[str, Any]] = []
        try:
            for index, (text, digest) in enumerate(zip(sentences, hashes), 1):
                filename = segment_filename(pid, lang, index, len(sentences))
                destination = root / filename
                synthesizer({k: v for k, v in profile.items() if k != "placeholder"}, text, destination)
                if not destination.is_file() or destination.stat().st_size == 0:
                    raise NarrationError(f"{label} 没合成出音频文件")
                segments.append({
                    "index": index, "text": text, "file": filename, "duration": round(float(prober(destination)), 3), "hash": digest,
                    "source": "tts", "voice": profile.get("id") or profile.get("voice") or "", "engine": profile.get("engine") or "",
                    "rate": profile.get("rate") or "", "placeholder": bool(profile.get("placeholder")),
                })
        except Exception as exc:  # noqa: BLE001 - 一页失败不拦其他页；原因进结果
            # 原因只记人话（跟插画 / 图片集一致），异常类名只在没有文案时兜底——「ProviderCommandError: voice 模型未配置」
            # 这种前缀界面上没人看得懂
            failed.append({"page": pid, "lang": lang, "error": (str(exc) or type(exc).__name__)[:500]})
            continue
        page_entry[lang] = {"segments": segments, "total": round(sum(s["duration"] for s in segments), 3), "updated_at": time.time()}
        done.append(label)
        _save_manifest(spec, manifest)
    _save_manifest(spec, manifest)
    return {"synthesized": done, "skipped": skipped, "failed": failed,
            "placeholder": {lang: bool(p.get("placeholder")) for lang, p in profiles.items()},
            "manifest": str(manifest_path(spec))}


def sync_imported(spec: Mapping[str, Any], *, prober: Prober | None = None) -> dict[str, Any]:
    """认领手动放进 07_audio 的整页音频（``P01_zh.mp3``）：清单里没记录、或记录的文件已不在 → 记成整页一段。"""

    prober = prober or probe_duration
    root = audio_dir(spec)
    manifest = load_manifest(spec)
    changed = False
    for page in spec.get("pages") or ():
        pid = str(page["id"])
        for lang in languages_for(str(spec.get("bilingual_mode") or "")):
            entry = (manifest["pages"].get(pid) or {}).get(lang)
            if entry and all((root / str(s.get("file") or "")).is_file() for s in entry.get("segments") or ()) and entry.get("segments"):
                continue
            candidate = root / f"{pid}_{lang}.mp3"
            if not candidate.is_file():
                continue
            text = str(page.get(lang) or "").strip()
            if not text:
                continue
            try:
                duration = round(float(prober(candidate)), 3)
            except NarrationError:
                continue
            manifest["pages"].setdefault(pid, {})[lang] = {
                "segments": [{"index": 1, "text": text, "file": candidate.name, "duration": duration, "hash": "", "source": "import",
                              "voice": "", "engine": "", "rate": "", "placeholder": False}],
                "total": duration, "updated_at": time.time(),
            }
            changed = True
    if changed:
        _save_manifest(spec, manifest)
    return manifest


def segments_for_timeline(spec: Mapping[str, Any], manifest: Mapping[str, Any] | None = None) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """narration.json → build_timeline 的输入；只带文件还在的条目。"""

    manifest = manifest or load_manifest(spec)
    root = audio_dir(spec)
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for pid, langs in (manifest.get("pages") or {}).items():
        for lang, entry in (langs or {}).items():
            segs = [s for s in (entry or {}).get("segments") or () if (root / str(s.get("file") or "")).is_file()]
            if segs and len(segs) == len((entry or {}).get("segments") or ()):
                out.setdefault(str(pid), {})[str(lang)] = [dict(s) for s in segs]
    return out


def status(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """每页每语言：有没有音频、总时长、是不是占位音色（给概览 / 朗读页）。"""

    ready = segments_for_timeline(spec)
    langs = languages_for(str(spec.get("bilingual_mode") or ""))
    out: dict[str, dict[str, Any]] = {}
    for pid in page_ids(spec):
        row: dict[str, Any] = {}
        for lang in langs:
            segs = (ready.get(pid) or {}).get(lang) or []
            row[lang] = {
                "done": bool(segs), "duration": round(sum(float(s.get("duration") or 0) for s in segs), 3) if segs else None,
                "segments": len(segs), "placeholder": any(s.get("placeholder") for s in segs), "source": segs[0].get("source") if segs else None,
                "file": segs[0].get("file") if segs else None,
            }
        out[pid] = row
    return out


__all__ = ["NarrationError", "PLACEHOLDER_PROFILES", "manifest_path", "load_manifest", "combine_rate", "voice_profile", "segment_hash",
           "segment_filename", "probe_duration", "plan", "synthesize_narration", "sync_imported", "segments_for_timeline", "status"]
