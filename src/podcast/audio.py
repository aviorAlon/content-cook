"""配音与混音（契约 docs/podcast-spec.md §4）：逐句 TTS → 时间线 → ffmpeg 混音 → 节目单 / 文字稿 / 章节。

* 逐句合成到 ``07_audio/<环节>_<序号>.mp3``，清单 ``audio_manifest.json`` 记文本 + 音色参数的哈希，没变就跳过
  （跟绘本朗读 ``picturebook.narration`` 同一套；``synthesizer`` / ``prober`` 可注入）。
* 主播 ``voice`` 是音色库档案 id 就走 ``genkit.voices.synthesize_with_profile``；内联档案照用；空 → 按语言的占位
  音色轮着分（尽量不让两个人同声）。
* 时间线：句间 ``pause.line``，环节间 ``pause.segment``，cue 含「停顿」句前再加 0.6s；``music.intro`` 在冷开场之后、
  开场之前整段播（没有冷开场就放最前面），``music.outro`` 在最后一句之后；章节 = 每个环节的起点。
* 混音：静音底轨定总长 + 每句 ``adelay`` + 垫乐循环、``bed_gain``、说话时段按 ``duck`` 压低（短剧后期同一套
  ``volume='if(...)'`` 表达式）、末尾渐弱 → ``08_mix/<ID>.mp3``。``runner`` 可注入（测试不装 ffmpeg 也能验命令）。
  句子超过 ``storyvideo.segmented.MIX_GROUP`` 句时先分组预混再进这一条（见 ``_mix_to``）；成品先写临时文件再原子替换。
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import spec as S
from .craft import PAUSE_CUE_EXTRA

TASK_KIND = "pc-audio"
MANIFEST_NAME = "audio_manifest.json"
CHAPTERS_NAME = "chapters.json"
TRANSCRIPT_NAME = "transcript.txt"
SHOWNOTES_NAME = "shownotes.md"
BED_FADE_OUT = 3.0
# 成品响度：Apple Podcasts 的建议是整体 -16 LKFS（±1）、真峰值不超过 -1 dBFS，这里真峰值再留 0.5 dB 余量。
# 实测：不做标准化时 edge-tts 双人对谈混出来整体 -23.1 LUFS，比常见播客小声约 7 dB。
LOUDNESS_TARGET_LUFS = -16.0
TRUE_PEAK_DBTP = -1.5
INTRO_GAP = 0.4               # 片头曲前后各留的空隙
TAIL_SECONDS = 1.0            # 最后一句之后留的尾巴
PLACEHOLDER_VOICES = {
    "zh": ("zh-CN-YunxiNeural", "zh-CN-XiaoxiaoNeural", "zh-CN-YunyangNeural", "zh-CN-XiaoyiNeural"),
    "en": ("en-US-GuyNeural", "en-US-JennyNeural", "en-US-DavisNeural", "en-US-AriaNeural"),
}

Synthesizer = Callable[[dict[str, Any], str, Path], Any]
Prober = Callable[[Path], float]
Runner = Callable[[Sequence[str]], Any]


class AudioError(RuntimeError):
    """合成或混音失败（原因写在消息里）。"""


# ---------------------------------------------------------------------------- 音色档案


def _profile_has_tts(found: Mapping[str, Any]) -> bool:
    """音色库档案能不能出声：有 TTS 音色名，或克隆已经成功（minimax 克隆档案的 voice 可以是空的）。"""

    if found.get("voice"):
        return True
    clone = found.get("clone")
    return isinstance(clone, Mapping) and clone.get("status") == "ready" and bool(clone.get("voice_id"))


def host_profiles(spec: Mapping[str, Any], *, lookup: Callable[[str], dict[str, Any] | None] | None = None) -> dict[str, dict[str, Any]]:
    """每位主播的合成档案：音色库档案 / 内联档案 / 占位（按语言轮着分）。带 ``placeholder`` 与 ``label``；
    音色库档案另带 ``profile_id``（档案 id，``voice`` 是真正送去 TTS 的音色名）。

    档案引用坏了不再静默：档案 id 找不到 → 占位 + ``missing``；档案只有文字描述、没有 TTS 音色 → 占位 + ``no_tts``
    （以前原样接受，合成时 voice 为空退到 edge 默认的 YunxiNeural，恰好跟占位池第一个同声）。两种都算占位，
    轮换照样避开同声；lint / 详情 / 交付包按标记说清。
    """

    language = str(spec.get("language") or "zh")
    pool = PLACEHOLDER_VOICES.get(language, PLACEHOLDER_VOICES["zh"])
    out: dict[str, dict[str, Any]] = {}
    slot = 0
    for host in spec.get("hosts") or ():
        if not isinstance(host, Mapping) or not host.get("id"):
            continue
        voice = host.get("voice")
        profile: dict[str, Any] | None = None
        broken = ""          # missing / no_tts：档案引用坏了，退到占位并标出来
        broken_name = ""
        if isinstance(voice, str) and voice.strip():
            if lookup is None:
                from genkit.voices import get_profile

                lookup = get_profile
            found = lookup(voice.strip())
            if not found:
                broken = "missing"
            elif not _profile_has_tts(found):
                broken = "no_tts"
                broken_name = str(found.get("name") or found.get("id") or voice.strip())
            else:
                profile = dict(found)
                profile["label"] = str(found.get("name") or found.get("id") or voice)
                profile["profile_id"] = str(found.get("id") or voice.strip())
        elif isinstance(voice, Mapping) and voice.get("voice"):
            profile = {k: str(voice.get(k) or "") for k in ("engine", "voice", "rate", "pitch")}
            profile["engine"] = profile["engine"] or "edge"
            profile["label"] = profile["voice"]
        if profile is None:
            profile = {"engine": "edge", "voice": pool[slot % len(pool)], "rate": "", "pitch": "", "placeholder": True}
            if broken == "missing":
                profile.update({"missing": True, "profile_id": str(voice).strip(),
                                "label": f"档案「{str(voice).strip()}」已不存在，暂用占位音色 {profile['voice']}"})
            elif broken == "no_tts":
                profile.update({"no_tts": True, "profile_id": str(voice).strip(),
                                "label": f"档案「{broken_name}」只有描述，暂用占位音色 {profile['voice']}"})
            else:
                profile["label"] = f"占位音色 {profile['voice']}"
            slot += 1
        profile.setdefault("placeholder", False)
        out[str(host["id"])] = profile
    return out


def segment_hash(text: str, profile: Mapping[str, Any]) -> str:
    clone = profile.get("clone")
    key = "|".join([
        text.strip(), str(profile.get("engine") or ""), str(profile.get("voice") or ""), str(profile.get("rate") or ""),
        str(profile.get("pitch") or ""), str(clone.get("voice_id") or "") if isinstance(clone, Mapping) else "",
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _clean_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in profile.items() if k not in ("placeholder", "label", "missing", "no_tts")}


def _host_record(profile: Mapping[str, Any]) -> dict[str, Any]:
    """清单里记的主播音色：``voice`` 是真正送去 TTS 的音色名，档案 id 单独记在 ``profile_id``——以前有档案 id 就只记 id，
    交付包里找不到实际用的音色（2026-09-22 审计）。"""

    return {"voice": str(profile.get("voice") or ""), "engine": str(profile.get("engine") or ""), "placeholder": bool(profile.get("placeholder")),
            "label": str(profile.get("label") or ""), "profile_id": str(profile.get("profile_id") or ""),
            "missing": bool(profile.get("missing")), "no_tts": bool(profile.get("no_tts"))}


# ---------------------------------------------------------------------------- 清单


def manifest_path(spec: Mapping[str, Any]) -> Path:
    return S.audio_dir(spec) / MANIFEST_NAME


def load_manifest(spec: Mapping[str, Any]) -> dict[str, Any]:
    path = manifest_path(spec)
    if not path.is_file():
        return {"lines": [], "updated_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"lines": [], "updated_at": None}
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        return {"lines": [], "updated_at": None}
    return data


def _save_manifest(spec: Mapping[str, Any], manifest: dict[str, Any]) -> None:
    from storyvideo.state import atomic_write_json

    manifest["updated_at"] = time.time()
    atomic_write_json(manifest_path(spec), manifest)


def line_filename(segment_id: str, index: int) -> str:
    return f"{segment_id}_{index:02d}.mp3"


def plan(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """要合成的句子，按播放顺序：``{segment, index, who, text, cue, kind, file}``。"""

    out = []
    for segment, index, line in S.all_lines(spec):
        out.append({
            "segment": str(segment["id"]), "index": index, "who": str(line["who"]), "text": str(line["text"]).strip(),
            "cue": str(line.get("cue") or ""), "kind": str(line.get("kind") or "talk"),
            "file": line_filename(str(segment["id"]), index),
        })
    return out


def probe_duration(path: Path, *, ffprobe: str = "ffprobe") -> float:
    from storyvideo.media import probe_media

    return float(probe_media(path, ffprobe=ffprobe).duration_seconds)


def _line_file_ok(path: Path, entry: Mapping[str, Any] | None = None) -> bool:
    """这句的音频文件还能用：在、不是符号链接、不是 0 字节，清单记了合成时的大小就还得对得上。

    合成到一半被打断（关窗口、磁盘满、并发写）会留下 0 字节或半截的 mp3，清单里的哈希却还对得上文本——以前只查
    ``is_file()``，这种句子就一直显示「已合成」、再合成也跳过、混音每次撞 ffmpeg 原话，只有「全部重合成」能救
    （2026-09-22 审计）。跟 export.synthesized_lines 一个口径。
    """

    try:
        if not path.is_file() or path.is_symlink():
            return False
        size = path.stat().st_size
    except OSError:
        return False
    if size <= 0:
        return False
    recorded = entry.get("size") if entry else None
    if isinstance(recorded, (int, float)) and not isinstance(recorded, bool) and int(recorded) != size:
        return False
    return True


def _entry_ok(entry: Mapping[str, Any] | None, digest: str, root: Path) -> bool:
    return bool(entry) and entry.get("hash") == digest and bool(entry.get("file")) and _line_file_ok(root / str(entry["file"]), entry)


def manifest_index(manifest: Mapping[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(str(e.get("segment")), int(e.get("index") or 0)): e for e in manifest.get("lines") or () if isinstance(e, Mapping)}


def is_stale(spec: Mapping[str, Any], manifest: Mapping[str, Any] | None = None, *, profiles: Mapping[str, Mapping[str, Any]] | None = None,
             lookup: Callable[[str], dict[str, Any] | None] | None = None) -> bool:
    """稿子或音色改过、清单里的哈希对不上、文件不在 = 要重新合成。没合成过也算过时。"""

    manifest = manifest if manifest is not None else load_manifest(spec)
    profiles = profiles if profiles is not None else host_profiles(spec, lookup=lookup)
    index = manifest_index(manifest)
    root = S.audio_dir(spec)
    rows = plan(spec)
    if not rows:
        return False
    for row in rows:
        profile = profiles.get(row["who"]) or {}
        if not _entry_ok(index.get((row["segment"], row["index"])), segment_hash(row["text"], profile), root):
            return True
    return False


def synthesize_lines(spec: Mapping[str, Any], *, force: bool = False, synthesizer: Synthesizer | None = None,
                     prober: Prober | None = None, lookup: Callable[[str], dict[str, Any] | None] | None = None,
                     note: Callable[..., None] | None = None) -> dict[str, Any]:
    """逐句合成。返回 ``{synthesized, skipped, failed, placeholder, manifest}``；一句失败不拦其他句。"""

    if synthesizer is None:
        from genkit.voices import synthesize_with_profile

        synthesizer = synthesize_with_profile
    prober = prober or probe_duration
    root = S.audio_dir(spec)
    root.mkdir(parents=True, exist_ok=True)
    profiles = host_profiles(spec, lookup=lookup)
    manifest = load_manifest(spec)
    index = manifest_index(manifest)
    rows = plan(spec)
    done: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []
    for number, row in enumerate(rows, 1):
        profile = profiles.get(row["who"]) or {"engine": "edge", "voice": PLACEHOLDER_VOICES["zh"][0], "placeholder": True, "label": ""}
        digest = segment_hash(row["text"], profile)
        key = (row["segment"], row["index"])
        label = row["file"]
        existing = index.get(key)
        if not force and _entry_ok(existing, digest, root):
            skipped.append(label)
            # cue / kind 不进哈希（不用重合成），但时间线按清单里的 cue 加停顿：跳过时也把当前值带上
            fresh.append({**existing, "cue": row["cue"], "kind": row["kind"]})
            continue
        if note:
            note(progress=f"{number}/{len(rows)}", current=label)
        destination = root / row["file"]
        try:
            synthesizer(_clean_profile(profile), row["text"], destination)
            if not destination.is_file() or destination.stat().st_size == 0:
                raise AudioError(f"{label} 没合成出音频文件")
            duration = round(float(prober(destination)), 3)
            if duration <= 0:
                raise AudioError(f"{label} 的音频探不出时长——文件可能没写完整")
            size = destination.stat().st_size
        except Exception as exc:  # noqa: BLE001 - 一句失败不拦其他句；原因进结果
            failed.append({"segment": row["segment"], "index": row["index"], "error": f"{type(exc).__name__}: {exc}"[:500]})
            continue
        entry = {
            "segment": row["segment"], "index": row["index"], "who": row["who"], "text": row["text"], "cue": row["cue"],
            "kind": row["kind"], "file": row["file"], "duration": duration, "size": size, "hash": digest,
            "voice": str(profile.get("voice") or ""), "profile_id": str(profile.get("profile_id") or ""),
            "engine": str(profile.get("engine") or ""), "placeholder": bool(profile.get("placeholder")),
        }
        fresh.append(entry)
        done.append(label)
    manifest["lines"] = fresh
    manifest["hosts"] = {hid: _host_record(p) for hid, p in profiles.items()}
    _save_manifest(spec, manifest)
    return {"synthesized": done, "skipped": skipped, "failed": failed,
            "placeholder": {hid: bool(p.get("placeholder")) for hid, p in profiles.items()}, "manifest": str(manifest_path(spec))}


# ---------------------------------------------------------------------------- 时间线


def music_paths(spec: Mapping[str, Any]) -> dict[str, Path | None]:
    music = spec.get("music") or {}
    return {slot: S.resolve_asset(spec, music.get(slot)) for slot in S.MUSIC_SLOTS}


def build_timeline(spec: Mapping[str, Any], manifest: Mapping[str, Any], *, music_durations: Mapping[str, float] | None = None) -> dict[str, Any]:
    """绝对时间线：``{items:[{kind:line|music, segment, index, who, text, file, start, duration}], chapters:[{id,title,kind,start}], total}``。

    清单里缺句子、或句子的音频文件坏了（0 字节 / 跟合成时的大小对不上）就抛 ``AudioError``（先合成再混）。
    """

    index = manifest_index(manifest)
    root = S.audio_dir(spec)
    kind_labels = S.labels(spec)["kinds"]
    pause = spec.get("pause") or S.DEFAULT_PAUSE
    line_gap = float(pause.get("line", 0.35))
    segment_gap = float(pause.get("segment", 1.2))
    durations = dict(music_durations or {})
    items: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    t = 0.0
    segments = [s for s in spec.get("segments") or () if isinstance(s, Mapping)]
    kinds = [str(s.get("kind") or "") for s in segments]
    intro_after = 0 if "cold_open" in kinds else -1
    if intro_after == 0:
        intro_after = kinds.index("cold_open")
    intro_dur = float(durations.get("intro") or 0.0)
    outro_dur = float(durations.get("outro") or 0.0)

    def place_music(slot: str, duration: float) -> None:
        nonlocal t
        if duration <= 0:
            return
        t += INTRO_GAP
        items.append({"kind": "music", "slot": slot, "start": round(t, 3), "duration": round(duration, 3)})
        t += duration + INTRO_GAP

    if intro_after < 0:
        place_music("intro", intro_dur)
    for si, segment in enumerate(segments):
        rows = [l for l in segment.get("lines") or () if isinstance(l, Mapping) and str(l.get("text") or "").strip()]
        if si > 0 and items:
            t += segment_gap
        chapters.append({"id": str(segment["id"]), "title": str(segment.get("title") or kind_labels.get(str(segment.get("kind")), "")),
                         "kind": str(segment.get("kind") or ""), "start": round(t, 3)})
        for li, _line in enumerate(rows, 1):
            entry = index.get((str(segment["id"]), li))
            if not entry or not entry.get("file"):
                raise AudioError(f"环节 {segment['id']} 第 {li} 句还没合成——先合成再混音")
            line_path = root / str(entry["file"])
            if not _line_file_ok(line_path, entry):
                # 坏文件在这里拦住，给人能照做的话；不然 ffmpeg 报的是「Failed to read frame size」原话
                size = line_path.stat().st_size if line_path.is_file() else 0
                raise AudioError(f"环节 {segment['id']} 第 {li} 句的音频文件损坏（{size} 字节）——再点一次「合成整期」会只重合成这一句")
            if li > 1:
                t += line_gap
            if "停顿" in str(entry.get("cue") or ""):
                t += PAUSE_CUE_EXTRA
            duration = float(entry.get("duration") or 0.0)
            items.append({"kind": "line", "segment": str(segment["id"]), "index": li, "who": str(entry.get("who") or ""),
                          "text": str(entry.get("text") or ""), "file": str(entry["file"]), "start": round(t, 3), "duration": round(duration, 3)})
            t += duration
        if si == intro_after:
            place_music("intro", intro_dur)
    if outro_dur > 0:
        place_music("outro", outro_dur)
    else:
        t += TAIL_SECONDS
    return {"items": items, "chapters": chapters, "total": round(t, 3)}


# ---------------------------------------------------------------------------- 混音


# 一条混音命令的时限：至少 10 分钟；长节目按成品时长的一半放宽（响度归一要把整期过一遍，90 分钟一期在多核机器上实测
# 最后那条命令 155 秒，慢机器上几倍于此，固定 600 秒会把能混完的长节目掐掉）
MIX_TIMEOUT_SECONDS = 600
SAMPLE_RATE = 44100


def _mix_timeout(timeline: Mapping[str, Any]) -> float:
    return max(float(MIX_TIMEOUT_SECONDS), float(timeline.get("total") or 0.0) * 0.5)


def _run(command: Sequence[str], *, purpose: str = "mix podcast", timeout_seconds: float = MIX_TIMEOUT_SECONDS) -> None:
    """跑一条混音命令。失败时只抛一行原因（``storyvideo.assembly.explain_ffmpeg_failure``）；完整命令、滤镜图、stderr 进日志——
    以前把 stderr 的最后 1000 字原样塞进 mix_error，界面上是一屏 ffmpeg 原话。"""

    from storyvideo.assembly import FFMPEG_LOG_HINT, explain_ffmpeg_failure, log_ffmpeg_failure

    try:
        completed = subprocess.run([str(c) for c in command], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout_seconds, shell=False, check=False)
    except subprocess.TimeoutExpired as exc:
        log_ffmpeg_failure(command, purpose, exc.stderr)
        raise AudioError(f"混音超过 {timeout_seconds:.0f} 秒还没跑完，已中止——{FFMPEG_LOG_HINT}") from exc
    except OSError as exc:
        log_ffmpeg_failure(command, purpose, str(exc))
        # WinError 206 / E2BIG：命令行超长。滤镜图已经进了脚本文件、句子分组预混，正常到不了这里；真到了也说人话
        if getattr(exc, "winerror", None) == 206 or getattr(exc, "errno", None) == errno.E2BIG:
            raise AudioError("混音：命令行超出了系统的长度上限——请把这条任务记录发给开发者") from exc
        raise AudioError(f"混音：跑不起 {command[0]}：{exc}") from exc
    if completed.returncode != 0:
        diagnostic = completed.stderr or completed.stdout or ""
        log_ffmpeg_failure(command, purpose, diagnostic)
        raise AudioError(f"混音失败：{explain_ffmpeg_failure(command, diagnostic)}——{FFMPEG_LOG_HINT}")


def mix_command(spec: Mapping[str, Any], timeline: Mapping[str, Any], *, ffmpeg: str = "ffmpeg", music: Mapping[str, Path | None] | None = None,
                destination: Path | None = None, premixed: Sequence[Any] | None = None) -> list[str]:
    """拼 ffmpeg 命令（纯函数，测试直接看它）。

    ``premixed``：句子超过 ``storyvideo.segmented.MIX_GROUP`` 句时先分组预混出的中间文件（``PlacedAudio``：文件 + 起点
    采样数），代替逐句输入铺进来；垫乐的压低时段照旧按每一句算。为 None 时与分组之前逐字一样。"""

    from storyvideo.segmented import adelay_samples

    music = music or {}
    root = S.audio_dir(spec)
    dest = destination or (S.mix_dir(spec) / f"{spec['id']}.mp3")
    total = float(timeline["total"])
    cmd: list[str] = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                      "-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
    filters: list[str] = []
    labels: list[str] = ["[0:a]"]
    n = 1
    speech: list[tuple[float, float]] = []
    for clip in premixed or ():
        cmd += ["-i", str(clip.path)]
        filters.append(f"[{n}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,{adelay_samples(clip.start_sample)}[l{n}]")
        labels.append(f"[l{n}]")
        n += 1
    for item in timeline["items"]:
        if item["kind"] == "line":
            speech.append((float(item["start"]), float(item["start"]) + float(item["duration"])))
            if premixed is not None:
                continue
            cmd += ["-i", str(root / item["file"])]
            ms = int(round(float(item["start"]) * 1000))
            filters.append(f"[{n}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,adelay={ms}|{ms}[l{n}]")
            labels.append(f"[l{n}]")
            n += 1
        elif item["kind"] == "music":
            path = music.get(item["slot"])
            if not path:
                continue
            cmd += ["-i", str(path)]
            ms = int(round(float(item["start"]) * 1000))
            length = float(item["duration"])
            fade = min(1.5, length / 2)
            filters.append(f"[{n}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,atrim=0:{length:.3f},asetpts=PTS-STARTPTS,"
                           f"afade=t=out:st={max(0.0, length - fade):.3f}:d={fade:.3f},adelay={ms}|{ms}[m{n}]")
            labels.append(f"[m{n}]")
            n += 1
    bed = music.get("bed")
    if bed and speech:
        settings = spec.get("music") or {}
        gain = float(settings.get("bed_gain", S.DEFAULT_MUSIC["bed_gain"]))
        duck = float(settings.get("duck", S.DEFAULT_MUSIC["duck"]))
        first = speech[0][0]
        last = speech[-1][1]
        length = max(0.5, last + TAIL_SECONDS - first)
        cmd += ["-stream_loop", "-1", "-i", str(bed)]
        chain = [f"[{n}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo", f"atrim=0:{length:.3f}", "asetpts=PTS-STARTPTS",
                 "afade=t=in:st=0:d=1.0", f"afade=t=out:st={max(0.0, length - BED_FADE_OUT):.3f}:d={min(BED_FADE_OUT, length):.3f}",
                 f"volume={gain:.3f}", f"adelay={int(round(first * 1000))}|{int(round(first * 1000))}"]
        if duck < 1.0:
            spans = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in speech)
            chain.append(f"volume='if({spans},{duck:.3f},1)':eval=frame")
        filters.append(",".join(chain) + f"[bed]")
        labels.append("[bed]")
    filters.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0:dropout_transition=0,"
                   f"loudnorm=I={LOUDNESS_TARGET_LUFS:g}:TP={TRUE_PEAK_DBTP:g}:LRA=11,aresample=44100[out]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", str(dest)]
    return cmd


def _mmss(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def shownotes_markdown(spec: Mapping[str, Any], timeline: Mapping[str, Any]) -> str:
    """节目单。标签按 ``spec.language`` 选（``S.labels``）：英文期用英文标签、半角冒号——这份是要随交付包发出去的。"""

    L = S.labels(spec)
    names = {str(h["id"]): str(h.get("name") or h["id"]) for h in spec.get("hosts") or ()}
    brief = spec.get("brief") or {}
    lines = [f"# {spec.get('title') or spec.get('id')}", ""]
    if brief.get("topic"):
        lines += [str(brief["topic"]), ""]
    lines.append(f"- {L['format']}{L['colon']}{S.format_name(spec)}{L['dot']}{L['duration']} {_mmss(float(timeline['total']))}")
    open_, close = L["paren"]
    hosts = L["sep"].join(f"{names[h['id']]}{open_}{L['roles'].get(h.get('role'), h.get('role'))}{close}" for h in spec.get("hosts") or ())
    if hosts:
        lines.append(f"- {L['hosts']}{L['colon']}{hosts}")
    lines += ["", f"## {L['chapters']}", ""]
    for chapter in timeline["chapters"]:
        lines.append(f"- {_mmss(float(chapter['start']))} {chapter['title'] or L['kinds'].get(chapter['kind'], chapter['kind'])}")
    if brief.get("takeaways"):
        lines += ["", f"## {L['takeaways']}", ""] + [f"- {t}" for t in brief["takeaways"]]
    questions = [(q.get("from"), q.get("text")) for s in spec.get("segments") or () if s.get("kind") == "qa" for q in s.get("questions") or ()]
    if questions:
        colon = L["colon"]
        lines += ["", f"## {L['questions']}", ""] + [f"- {who}{colon}{text}" if who else f"- {text}" for who, text in questions]
    lines += ["", "---", L["footer"], ""]
    return "\n".join(lines)


def transcript_text(spec: Mapping[str, Any], timeline: Mapping[str, Any]) -> str:
    L = S.labels(spec)
    names = {str(h["id"]): str(h.get("name") or h["id"]) for h in spec.get("hosts") or ()}
    rows = []
    chapters = {c["id"]: c for c in timeline["chapters"]}
    seen: set[str] = set()
    for item in timeline["items"]:
        if item["kind"] != "line":
            continue
        if item["segment"] not in seen:
            seen.add(item["segment"])
            chapter = chapters.get(item["segment"], {})
            rows.append(f"\n## [{_mmss(float(chapter.get('start', item['start'])))}] {chapter.get('title') or item['segment']}\n")
        rows.append(f"[{_mmss(float(item['start']))}] {names.get(item['who'], item['who'])}{L['colon']}{item['text']}")
    return "\n".join(rows).strip() + "\n"


def mix_fingerprint(spec: Mapping[str, Any], manifest: Mapping[str, Any]) -> str:
    """成品的输入指纹：每句（顺序、哈希、时长、cue）+ 句间 / 环节间停顿 + 三个音乐槽（路径、大小、mtime）+（挂了垫乐时）bed_gain + duck。

    混音时写进 ``08_mix/chapters.json`` 的 ``mix_hash``，详情拿当前 spec 再算一次比对：只改停顿 / 换音乐 / 混音失败留下的
    旧成品，以前一律显示「已混音」，导出包里 spec.json 与 mix.mp3 互相矛盾（2026-09-22 审计）。输入没变而 ffmpeg 临时失败
    （没装）时旧成品仍是对的——指纹法比「失败就删」准在这里。
    """

    index = manifest_index(manifest)
    parts: list[str] = []
    for row in plan(spec):
        entry = index.get((row["segment"], row["index"])) or {}
        parts.append(f"{row['segment']}/{row['index']}:{entry.get('hash') or ''}:{entry.get('duration') or ''}:{row['cue']}")
    pause = spec.get("pause") or S.DEFAULT_PAUSE
    parts.append(f"pause:{float(pause.get('line', S.DEFAULT_PAUSE['line'])):.3f}/{float(pause.get('segment', S.DEFAULT_PAUSE['segment'])):.3f}")
    music = spec.get("music") or {}
    for slot in S.MUSIC_SLOTS:
        path = S.resolve_asset(spec, music.get(slot))
        if path is None:
            parts.append(f"{slot}:-")
            continue
        try:
            stat = path.stat()
            parts.append(f"{slot}:{music.get(slot)}:{stat.st_size}:{int(stat.st_mtime)}")
        except OSError:
            parts.append(f"{slot}:{music.get(slot)}:?")
    # bed_gain / duck 只在挂了垫乐时才进指纹——跟 mix_command 同一个条件（没垫乐时它们根本不进 ffmpeg 命令）。
    # 以前无条件算进去，没垫乐的项目拖一下音量滑块就被标成「成品按旧参数混的」，导出包的 AI 声明也跟着写错
    if S.resolve_asset(spec, music.get("bed")) is not None:
        parts.append(f"gain:{float(music.get('bed_gain', S.DEFAULT_MUSIC['bed_gain'])):.3f}")
        parts.append(f"duck:{float(music.get('duck', S.DEFAULT_MUSIC['duck'])):.3f}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _mix_to(spec: Mapping[str, Any], timeline: Mapping[str, Any], dest: Path, *, ffmpeg: str, music: Mapping[str, Path | None],
            runner: Runner | None) -> None:
    """混成 ``dest``（2026-09-22 发第一个 tag 前改的）。

    * 先写同目录的临时文件，ffmpeg 成功、文件非空才原子替换：以前直接写成品，``-y`` 一开跑就把上一版截断，混到一半
      失败 / 超时留下半截 mp3，详情照样显示「已混音」、导出包也带着它。上一版正被页面上的播放器开着读时（Windows 不让
      替换），``replace_final`` 等几秒再放弃，原因进 mix_error（AudioError），任务不算失败。
    * 句子超过 ``MIX_GROUP`` 句先分组预混（``storyvideo.segmented.premix_audio``），滤镜图写进文件：一条命令的输入数与
      命令行长度不再随句数涨。以前 150 句左右（十来分钟）命令行就超过 Windows 的 32767 字符上限，ffmpeg 根本起不来
      （WinError 206），90 分钟上限的一期一定混不出来。"""

    from storyvideo.assembly import _temporary_destination, replace_final
    from storyvideo.segmented import premix_audio

    limit = _mix_timeout(timeline)

    def run(command: list[str], purpose: str = "mix podcast", timeout_seconds: float = limit) -> None:
        if runner is not None:
            runner(command)
        else:
            _run(command, purpose=purpose, timeout_seconds=timeout_seconds)

    root = S.audio_dir(spec)
    lines = [(root / str(item["file"]), float(item["start"])) for item in timeline["items"] if item["kind"] == "line"]
    # 上次删不掉留下的临时目录（Windows 上杀毒 / 索引进程短暂占着刚写出的中间文件），这次开混前顺手清掉；
    # 同一期同时只会有一个混音任务
    for stale in dest.parent.glob(".mix-*"):
        if stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)
    temporary = _temporary_destination(dest)
    try:
        # 预混中间文件与滤镜图都在这个临时目录里，混完就清；清不掉（杀毒 / 预览进程占着）不该让混好的成品算失败
        with tempfile.TemporaryDirectory(prefix=".mix-", dir=dest.parent, ignore_cleanup_errors=True) as work_name:
            work = Path(work_name)
            premixed = premix_audio(lines, work, ffmpeg=ffmpeg, sample_rate=SAMPLE_RATE, run=run, timeout_seconds=limit)
            command = mix_command(spec, timeline, ffmpeg=ffmpeg, music=music, destination=temporary, premixed=premixed)
            # 垫乐压低段每句一项：内联在命令行里会随句数涨，写成文件交给 -filter_complex_script
            at = command.index("-filter_complex")
            graph = work / "mix_filtergraph.txt"
            graph.write_text(command[at + 1], encoding="utf-8")
            command[at:at + 2] = ["-filter_complex_script", str(graph)]
            run(command)
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise AudioError("混音没有产出文件")
        try:
            with temporary.open("r+b") as stream:
                os.fsync(stream.fileno())
        except OSError as exc:
            raise AudioError(f"混音结果没能写进磁盘：{exc}") from exc
        replace_final(temporary, dest, error=AudioError)
    finally:
        temporary.unlink(missing_ok=True)


def mix(spec: Mapping[str, Any], manifest: Mapping[str, Any] | None = None, *, ffmpeg: str | None = None, ffprobe: str = "ffprobe",
        runner: Runner | None = None, prober: Prober | None = None) -> dict[str, Any]:
    """混成一条 + 写章节 / 文字稿 / 节目单。返回 ``{mix, duration, chapters, shownotes, transcript}``。"""

    manifest = manifest if manifest is not None else load_manifest(spec)
    music = music_paths(spec)
    prober = prober or (lambda p: probe_duration(p, ffprobe=ffprobe))
    # 先查 ffmpeg 再探音乐：没装 ffmpeg 的机器一般也没有 ffprobe，要是先探音乐，挂了片头的项目报的就是「片头读不出」，
    # 而不是「装好 ffmpeg 再点一次」这句能照做的话
    binary = ffmpeg or shutil.which("ffmpeg")
    if not binary:
        raise AudioError("没找到 ffmpeg——逐句音频已经出了，装好 ffmpeg 再点一次「合成」就能混成一条")
    durations: dict[str, float] = {}
    # 三个槽都探一遍（含垫乐）。探不出就报错，不再「当没有这段音乐」静默跳过：以前 16 字节的垃圾文件放片头，成品悄悄
    # 短了几秒、任务 succeeded、界面上片头文件还挂着；同一文件放垫乐则 ffmpeg 硬报错——两条路的错误口径统一成一条
    for slot in S.MUSIC_SLOTS:
        path = music.get(slot)
        if not path:
            continue
        try:
            duration = float(prober(path))
        except Exception as exc:  # noqa: BLE001 - 探测器抛什么都归成一句人话（ffprobe 的原话多行、带绝对路径：压成一行、只留文件名、取尾巴）
            reason = " ".join(str(exc).replace(str(path), path.name).split())[-200:]
            raise AudioError(f"{S.MUSIC_SLOT_LABELS.get(slot, slot)}音乐文件读不出（{path.name}）：{reason}——换一个文件，或在「音乐」里清掉这个槽") from exc
        if duration <= 0:
            raise AudioError(f"{S.MUSIC_SLOT_LABELS.get(slot, slot)}音乐文件读不出（{path.name}）：时长为 0——换一个文件，或在「音乐」里清掉这个槽")
        durations[slot] = duration
    timeline = build_timeline(spec, manifest, music_durations=durations)
    out_dir = S.mix_dir(spec)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{spec['id']}.mp3"
    _mix_to(spec, timeline, dest, ffmpeg=binary, music=music, runner=runner)
    from storyvideo.state import atomic_write_json

    atomic_write_json(out_dir / CHAPTERS_NAME, {"total": timeline["total"], "chapters": timeline["chapters"], "created_at": time.time(),
                                                "mix_hash": mix_fingerprint(spec, manifest)})
    (out_dir / TRANSCRIPT_NAME).write_text(transcript_text(spec, timeline), encoding="utf-8")
    (out_dir / SHOWNOTES_NAME).write_text(shownotes_markdown(spec, timeline), encoding="utf-8")
    return {"mix": str(dest), "duration": timeline["total"], "chapters": timeline["chapters"],
            "shownotes": str(out_dir / SHOWNOTES_NAME), "transcript": str(out_dir / TRANSCRIPT_NAME)}


def run_synthesize(spec: Mapping[str, Any], *, force: bool = False, synthesizer: Synthesizer | None = None, prober: Prober | None = None,
                   lookup: Callable[[str], dict[str, Any] | None] | None = None, ffmpeg: str | None = None, runner: Runner | None = None,
                   note: Callable[..., None] | None = None) -> dict[str, Any]:
    """任务体：逐句合成 + 混音一次跑完。混音失败不算任务失败（逐句音频照样在），原因放 ``mix_error``。"""

    result = synthesize_lines(spec, force=force, synthesizer=synthesizer, prober=prober, lookup=lookup, note=note)
    result["mixed"] = False
    result["mix_error"] = ""
    if result["failed"]:
        result["mix_error"] = f"{len(result['failed'])} 句没合成出来，没有混音"
        return result
    if note:
        note(progress="mix", current="混音")
    try:
        mixed = mix(spec, ffmpeg=ffmpeg, runner=runner, prober=prober)
    except AudioError as exc:
        result["mix_error"] = str(exc)
        return result
    result.update({"mixed": True, "mix": mixed["mix"], "duration": mixed["duration"], "chapters": mixed["chapters"]})
    return result


# ---------------------------------------------------------------------------- 状态（接口用）


def status(spec: Mapping[str, Any], *, lookup: Callable[[str], dict[str, Any] | None] | None = None) -> dict[str, Any]:
    manifest = load_manifest(spec)
    profiles = host_profiles(spec, lookup=lookup)
    index = manifest_index(manifest)
    root = S.audio_dir(spec)
    rows = []
    for row in plan(spec):
        entry = index.get((row["segment"], row["index"]))
        ok = _entry_ok(entry, segment_hash(row["text"], profiles.get(row["who"]) or {}), root)
        rows.append({**row, "file": row["file"] if entry and entry.get("file") and _line_file_ok(root / str(entry["file"]), entry) else None,
                     "duration": float(entry.get("duration") or 0.0) if entry else 0.0, "stale": not ok})
    mix_path = S.mix_dir(spec) / f"{spec['id']}.mp3"
    chapters_path = S.mix_dir(spec) / CHAPTERS_NAME
    chapters: dict[str, Any] = {}
    if chapters_path.is_file():
        try:
            chapters = json.loads(chapters_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            chapters = {}
    mixed = mix_path.is_file() and mix_path.stat().st_size > 0
    return {
        "lines": rows,
        "synthesized": sum(1 for r in rows if r["file"]),
        "total": len(rows),
        "stale": is_stale(spec, manifest, profiles=profiles),
        "mixed": mixed,
        # 成品是按别的输入混的（停顿 / 音乐 / 某句重合成之后没重混；老 chapters.json 没记指纹也算）：仍可试听，但要标明是旧的
        "mix_stale": bool(mixed) and str(chapters.get("mix_hash") or "") != mix_fingerprint(spec, manifest),
        "mix_file": mix_path.name if mix_path.is_file() else None,
        "mix_duration": float(chapters.get("total") or 0.0),
        "chapters": list(chapters.get("chapters") or []),
        "hosts": {hid: {"label": str(p.get("label") or ""), "placeholder": bool(p.get("placeholder")), "engine": str(p.get("engine") or ""),
                        "missing": bool(p.get("missing")), "no_tts": bool(p.get("no_tts")), "profile_id": str(p.get("profile_id") or "")}
                  for hid, p in profiles.items()},
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "texts": {name: (S.mix_dir(spec) / name).is_file() for name in (SHOWNOTES_NAME, TRANSCRIPT_NAME, CHAPTERS_NAME)},
    }


__all__ = ["TASK_KIND", "MANIFEST_NAME", "CHAPTERS_NAME", "TRANSCRIPT_NAME", "SHOWNOTES_NAME", "PLACEHOLDER_VOICES", "AudioError",
           "host_profiles", "segment_hash", "manifest_path", "load_manifest", "line_filename", "plan", "probe_duration", "is_stale",
           "synthesize_lines", "music_paths", "build_timeline", "mix_command", "shownotes_markdown", "transcript_text", "mix_fingerprint",
           "mix", "run_synthesize", "status"]
