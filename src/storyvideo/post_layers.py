"""后期层：旁白 VO + 整屏插卡 + 画面上的后期文字 + 混音 → 成片。

为旁白驱动的短剧建：旁白是固定 TTS 声线、部分场景只用手机整屏
UI 插卡表现、画面上的手写字后期叠——三样都不进生成，也不在 ``assembly.assemble_episode`` 的职责里
（它按镜拼接、裁头、烧台词字幕、响度归一）。这层读 spec 里的三个后期块：

* ``spec.voice_over``   — ``{voice, edge:{voice,rate,pitch}, subtitles?, lines:[{shot, at, zh, edge?}]}``
* ``spec.post_graphics.phone_ui_inserts`` — ``[{id, after, dur, screen | lines}]``
* ``spec.post_graphics.notebook_text``    — ``[{shot, text, style, at?, until?, y?}]``
* ``spec.post_graphics.silence``          — ``[{shot, at, dur, label?}]``（绝对静音区间，响度归一之后置零）

时间线由本层自己排：按 spec 镜次顺序，插卡按 ``after`` 插进去，相邻两项之间做 ``delivery.crossfade_seconds``
的交叉溶解；不裁头（这条线是旁白驱动节奏，生成镜的开口本来就要求在 0.8s 内）。画内台词仍烧字幕
（底部条），旁白默认不烧字幕（旁白是画外的声音，观众要听不是要读）；``voice_over.subtitles=true`` 时
按每句真实 TTS 时长烧，样式同台词字幕（有画外音需要让观众看清说了什么时打开）。

TTS 走用户「模型设置」里的语音角色（genkit），没配就用 edge-tts 库；可注入替身做测试。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping, Sequence

from .assembly import JOIN_GROUP, AssemblyError, _filter_path, _INTERMEDIATE_ENCODE, _run, _subtitle_filters, _sync_file, _text_file
from .assembly import _temporary_destination, delivery_settings, join_inputs, replace_final
from .media import MediaValidationError, probe_media
from .segmented import adelay_samples, premix_audio
from .segmented import concat_entry as _concat_entry
from .state import atomic_write_json

DEFAULT_EDGE = {"voice": "zh-CN-YunxiNeural", "rate": "-6%", "pitch": "-8Hz"}
# 三种用途各一组候选字体，按平台顺序找第一个存在的。spec.post_graphics.fonts 可逐项覆盖。
# notebook：越像手写越好；ui / subtitle：清晰的中文无衬线。找不到任何一个时 font_status()
# 报 ok=False，界面在点「生成后期合成版」之前就能提示，而不是等 ffmpeg 报一段看不懂的错。
_FONT_CANDIDATES = {
    "notebook": (
        "C:/Windows/Fonts/STXINGKA.TTF", "C:/Windows/Fonts/STKAITI.TTF", "C:/Windows/Fonts/simkai.ttf",
        "/System/Library/Fonts/Supplemental/Kaiti.ttc", "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ),
    "ui": (
        "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc", "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    ),
}
_FONT_CANDIDATES["subtitle"] = _FONT_CANDIDATES["ui"]


def default_fonts() -> dict[str, str]:
    """每种用途第一个真实存在的候选字体；一个都没有就保留首选路径（报错时能看出缺什么）。"""

    out: dict[str, str] = {}
    for kind, candidates in _FONT_CANDIDATES.items():
        found = next((p for p in candidates if Path(p).is_file()), None)
        out[kind] = found or candidates[0]
    return out


def font_status(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """给 /api/health 与后期面板用：三种用途各自找到了哪个字体，缺哪个。

    ``overrides`` 是 spec.post_graphics.fonts：合成和插卡预览用的就是覆盖后的路径，就绪检查也得按它查——
    只查默认字体的话，界面显示「✓ 中文字体」而预览 425、合成必失败。"""

    fonts = default_fonts()
    for kind, path in (overrides or {}).items():
        if kind in fonts and str(path or "").strip():
            fonts[kind] = str(path)
    missing = [kind for kind, path in fonts.items() if not Path(path).is_file()]
    return {"ok": not missing, "fonts": fonts, "missing": missing}


DEFAULT_FONTS = default_fonts()
UI_SIZE = (1080, 1920)
# 插卡的 after 写这个 = 插在第一镜之前（片头卡）；镜号与插卡 id 都不能叫这个名字
START_ANCHOR = "__start__"
# 插卡时长的上限（秒）：接口层写盘前拦；留空 = 4 秒
INSERT_MAX_SECONDS = 60.0
INSERT_DEFAULT_SECONDS = 4.0
# 插卡版式：界面和示例里写的是单数 notification / message / text，渲染表用的是 notifications / sms / plain；空 = plain
INSERT_LAYOUTS = ("", "notification", "notifications", "sms", "message", "shop", "plain", "text", "card")


class PostLayerError(AssemblyError):
    """后期层失败——都是可读的、带上下文的错误。"""


# --------------------------------------------------------------------------- VO

# 逐句声线：同一条旁白轨里要有两种声音（女声 VO + 男声 VO），整块只有一个
# voice_over.edge 装不下。lines[i].edge 形状同整块 {voice, rate?, pitch?}，只覆盖这一句；没写的 rate / pitch
# 沿用整块的。只有 edge 引擎认——settings / minimax 引擎的 voice 是别家的音色名或克隆 id，拿 edge 音色名去调会错。
VO_EDGE_KEYS = ("voice", "rate", "pitch")
VO_EDGE_ENGINES = ("", "edge")          # 空 = 老规则（没配别的语音角色时就是 edge-tts）
# 旁白引擎：空 = 老规则；别的值 default_tts 会静默当成 edge，所以写盘前只认这几个（与 genkit.voices.ENGINES 同）
VO_ENGINES = ("", "edge", "settings", "minimax")
_EDGE_RATE = re.compile(r"[+-]\d+%")     # edge-tts 自己的校验就是这两个形状，写错了要到合成那一句才炸
_EDGE_PITCH = re.compile(r"[+-]\d+Hz")
_EDGE_RULES = (("rate", _EDGE_RATE, "+10% / -6%"), ("pitch", _EDGE_PITCH, "+5Hz / -8Hz"))


def _line_edge(line: Mapping[str, Any]) -> dict[str, str]:
    """这一句的逐句声线（去掉空值）；没写返回空 dict。"""

    edge = line.get("edge")
    if not isinstance(edge, Mapping):
        return {}
    return {k: str(edge[k]).strip() for k in VO_EDGE_KEYS if edge.get(k) not in (None, "") and str(edge[k]).strip()}


def validate_voice_over_extras(vo: Mapping[str, Any], shots: Sequence[Mapping[str, Any]] | None = None) -> list[str]:
    """``voice_over`` 里除 zh / shot 之外的形状校验（后期块写盘前由 routers/post.py 调用）：
    ``subtitles`` 开关、整块 ``edge`` 与逐句 ``lines[i].edge``（同一套 rate / pitch 规则）、``engine`` 取值、
    每句 ``at``（不小于 0 的秒数；传入 ``shots`` 时还要落在这一镜的计划时长里——起点就在镜外的旁白一定是写错了）。"""

    errors: list[str] = []
    subtitles = vo.get("subtitles")
    if subtitles is not None and not isinstance(subtitles, bool):
        errors.append("voice_over.subtitles 只能是 true 或 false")
    block_edge = vo.get("edge")
    if block_edge is not None:
        if not isinstance(block_edge, Mapping):
            errors.append("voice_over.edge 必须是对象：{voice, rate, pitch}")
        else:
            unknown = [str(k) for k in block_edge if k not in VO_EDGE_KEYS]
            if unknown:
                errors.append(f"voice_over.edge 只认 voice / rate / pitch，多了：{'、'.join(unknown)}")
            for key, pattern, example in _EDGE_RULES:
                value = block_edge.get(key)
                if value in (None, ""):
                    continue
                if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
                    errors.append(f"voice_over.edge.{key} 要写成 {example} 这样，现在是「{value}」")
    engine = str(vo.get("engine") or "").strip()
    if engine not in VO_ENGINES:
        errors.append(f"voice_over.engine 只能是 edge / settings / minimax（留空 = 老规则），现在是「{engine}」")
    by_id = {str(s.get("id")): s for s in (shots or ()) if isinstance(s, Mapping)}
    lines = vo.get("lines")
    for i, line in enumerate(lines if isinstance(lines, list) else ()):
        if not isinstance(line, Mapping):
            continue
        at = line.get("at")
        if at not in (None, ""):
            seconds = _seconds(at)
            if seconds is None or seconds < 0:
                errors.append(f"voice_over.lines[{i}].at 要是不小于 0 的秒数，现在是「{at}」")
            else:
                planned = _seconds((by_id.get(str(line.get("shot"))) or {}).get("dur"))
                if planned is not None and seconds >= planned:
                    errors.append(f"voice_over.lines[{i}].at = {seconds:g}s 不在镜 {line.get('shot')} 里：这一镜只有 {planned:g}s")
        if line.get("edge") is None:
            continue
        edge = line["edge"]
        if not isinstance(edge, Mapping):
            errors.append(f"voice_over.lines[{i}].edge 必须是对象：{{voice, rate, pitch}}")
            continue
        if engine not in VO_EDGE_ENGINES:
            errors.append(f"voice_over.lines[{i}] 写了逐句声线 edge，但旁白引擎是「{engine}」——逐句声线只对 edge 引擎生效："
                          "删掉这句的 edge，或把旁白换成 edge 音色")
        unknown = [str(k) for k in edge if k not in VO_EDGE_KEYS]
        if unknown:
            errors.append(f"voice_over.lines[{i}].edge 只认 voice / rate / pitch，多了：{'、'.join(unknown)}")
        voice = edge.get("voice")
        if not isinstance(voice, str) or not voice.strip():
            errors.append(f"voice_over.lines[{i}].edge 缺 voice：写这一句用的 edge 音色名，如 zh-CN-YunyangNeural")
        for key, pattern, example in _EDGE_RULES:
            value = edge.get(key)
            if value in (None, ""):
                continue
            if not isinstance(value, str) or not pattern.fullmatch(value.strip()):
                errors.append(f"voice_over.lines[{i}].edge.{key} 要写成 {example} 这样，现在是「{value}」")
    return errors


def _synth_once(path: Path, render: Callable[[Path], Any]) -> None:
    """合成一句到 ``path``：先写同目录的临时文件（后缀不变，按后缀选格式的语音接口照样认），写成了才换名。

    旁白 / 配音按「文件在不在」缓存：以前直接写 ``path``，TTS 半路断网或进程被杀会留下半截 mp3，下一次合成时因为文件在
    就直接复用——成片里这句被截掉，而且不报错。"""

    temporary = _temporary_destination(path)
    try:
        render(temporary)
        if temporary.is_file() and temporary.stat().st_size > 0:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def render_vo_lines(
    spec: Mapping[str, Any],
    out_dir: str | os.PathLike[str],
    *,
    tts: Callable[..., None] | None = None,
    ffprobe: str = "ffprobe",
) -> list[dict[str, Any]]:
    """把 ``spec.voice_over.lines`` 逐句渲染成 mp3，返回 ``[{index, shot, at, zh, path, duration}]``
    （写了逐句声线的句子多一个 ``edge``：这句实际用的声线）。

    同一句（文本 + 这句实际的声线参数）已渲染过就复用；``tts`` 可注入（测试用），默认走 edge-tts。
    注入的 ``tts(text, destination)``：写了逐句声线的句子会多传第三个参数 ``edge``（合并后的 voice/rate/pitch）。
    """

    vo = spec.get("voice_over") or {}
    lines = [line for line in (vo.get("lines") or ()) if isinstance(line, Mapping) and str(line.get("zh") or "").strip()]
    cfg = dict(DEFAULT_EDGE)
    cfg.update({k: str(v) for k, v in (vo.get("edge") or {}).items()})
    engine = str(vo.get("engine") or "").strip()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    def default_tts(text: str, destination: Path, edge: Mapping[str, str] | None = None) -> None:
        """旁白引擎：``voice_over.engine`` 写了就按它（音色库绑定时写入：edge 或 settings）；
        没写则沿用老规则——「模型设置」里配了非 edge 的语音角色就用它，否则 edge-tts（免费）。
        spec 的 voice_over.edge 里的音色/语速/音调仍生效。
        ``edge`` 是逐句声线：这句明写了 edge 音色，就直接走 edge-tts，不看「模型设置」的语音角色。"""

        from genkit.adapters.speech import EdgeSpeech

        if edge is not None:
            EdgeSpeech({"model": edge.get("voice")}).synthesize(
                text, destination, voice=edge.get("voice"), rate=edge.get("rate") or None, pitch=edge.get("pitch") or None,
            )
            return

        from genkit import settings as _settings
        from genkit.router import build_provider

        if engine == "minimax":
            # 音色库克隆出来的音色（DESIGN §33）：voice 就是克隆 id，走 genkit.voices 同一条合成路
            from genkit.voices import synthesize_with_profile

            synthesize_with_profile({"engine": "minimax", "voice": cfg.get("voice") or "", "rate": cfg.get("rate") or ""}, text, destination)
            return
        loaded = _settings.load_settings()
        role = loaded.role("voice")
        use_settings = (engine == "settings") if engine else (
            bool(role.adapter) and role.adapter != "edge_tts" and not _settings.validate_role(loaded, "voice")
        )
        if use_settings:
            build_provider(loaded).speech().synthesize(text, destination, voice=cfg.get("voice") or None)
            return
        EdgeSpeech({"model": cfg.get("voice") or role.model}).synthesize(
            text, destination, voice=cfg.get("voice"), rate=cfg.get("rate"), pitch=cfg.get("pitch"),
        )

    synth = tts or default_tts
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        text = str(line["zh"]).strip()
        override = _line_edge(line)
        if override and engine not in VO_EDGE_ENGINES:
            # 写盘前的校验会拦；这里兜的是手改 spec 文件的情况——静默忽略的话听到的是整块声线，更难查
            raise PostLayerError(f"旁白第 {index} 句「{text[:12]}」写了逐句声线 edge，但旁白引擎是「{engine}」——"
                                 "逐句声线只对 edge 引擎生效：删掉这句的 edge，或把旁白换成 edge 音色")
        # 缓存键 = 文本 + 这句实际用的声线：没写逐句声线的句子键不变（老项目已渲染的 mp3 照样复用），
        # 写了的换声线就换文件。逐句声线的键再多带一个路径标记 "line-edge"：这类句子一定走 edge-tts，而整块的句子
        # 在引擎没写时可能走「模型设置」里的别家语音——合并后的声线恰好等于整块时，没有标记就会复用别家合成的那个文件
        voice_cfg = {**cfg, **override} if override else cfg
        key: list[Any] = [text, voice_cfg, "line-edge"] if override else [text, voice_cfg]
        digest = hashlib.sha1(json.dumps(key, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:10]
        path = out / f"vo_{index:02d}_{line.get('shot', 'x')}_{digest}.mp3"
        if not path.is_file():
            if override:
                _synth_once(path, lambda dest: synth(text, dest, dict(voice_cfg)))
            else:
                _synth_once(path, lambda dest: synth(text, dest))
        duration = float(probe_media(path, ffprobe=ffprobe).duration_seconds)
        # 写盘前的校验会拦；这里兜的是手改 spec 文件：裸的 float("abc") 会把 Python 异常原样送进任务面板
        raw_at = line.get("at")
        at = 0.0 if raw_at in (None, "") else _seconds(raw_at)
        if at is None or at < 0:
            raise PostLayerError(f"旁白第 {index} 句「{text[:12]}」的 at 要是不小于 0 的秒数，现在是「{raw_at}」")
        row = {"index": index, "shot": str(line.get("shot")), "at": at,
               "zh": text, "path": str(path), "duration": duration}
        if override:
            row["edge"] = dict(voice_cfg)
        rows.append(row)
    (out / "vo_manifest.json").write_text(json.dumps({"edge": cfg, "lines": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


# --------------------------------------------------------------------------- 台词配音（TTS 兜底）


DUB_MODES = ("off", "all", "shots")


# --------------------------------------------------------------------------- 配乐（时间线层）
# drama-skills VID-14：单镜产物不自带配乐，配乐是时间线层，逐镜只标进入/退出/让路给对白。
# 这里的实现：post_graphics.music = [{file, start, end, gain, duck, fade_in, fade_out, label}]，本地音频文件按绝对时间铺进后期版；
# 有旁白/配音/台词的时段自动压到 duck（默认 0.3）；结尾按时间线截断，不会把成片拖长。
DEFAULT_MUSIC = {"gain": 0.6, "duck": 0.3, "fade_in": 1.0, "fade_out": 1.5}


def music_file_problem(raw: str, project_root: Path) -> str | None:
    """配乐文件路径不能用的原因；能用返回 None。只收数据目录 content/ 下的相对路径（上传接口就存在
    content/assets/<id>/music/）。先按字面拒（~、绝对路径、盘符、UNC、..——不碰文件系统，UNC 不去探），再 resolve
    核一次边界（挡住指向外面的链接）。存草稿、手改项目文件都绕得过写盘校验，所以读文件的地方也用它（PB-5）。"""

    text = str(raw or "").strip()
    if not text:
        return "没有写文件"
    windows = PureWindowsPath(text)
    if text.startswith("~") or windows.drive or windows.root or Path(text).is_absolute() or ".." in windows.parts:
        return "不是数据目录里的相对路径"
    root = Path(project_root)
    if not (root / text).resolve().is_relative_to((root / "content").resolve()):
        return "不在数据目录 content/ 里"
    return None


def music_tracks(spec: Mapping[str, Any], *, project_root: Path | None = None) -> list[dict[str, Any]]:
    """spec 里的配乐条目合上默认值并解析文件路径（相对项目根，只认 content/ 下的文件，见 :func:`music_file_problem`）。
    用不了的条目 ``missing=True`` 并带 ``reason``，合成时跳过并写进清单。"""

    out: list[dict[str, Any]] = []
    root = Path(project_root) if project_root else None
    for i, item in enumerate((spec.get("post_graphics") or {}).get("music") or ()):
        if not isinstance(item, Mapping):
            continue
        raw_file = str(item.get("file") or "").strip()
        problem = music_file_problem(raw_file, root) if root is not None else "不知道数据目录在哪"
        path = (root / raw_file).resolve() if problem is None else None
        if problem is None and not path.is_file():
            problem = "文件不存在"
        row = {**DEFAULT_MUSIC, **{k: v for k, v in item.items() if v is not None}}
        row.update({"index": i, "path": str(path) if problem is None else "", "missing": problem is not None,
                    "start": max(0.0, float(row.get("start") or 0.0)), "label": str(item.get("label") or f"配乐 {i + 1}")})
        if problem is not None:
            row["reason"] = problem
        end = row.get("end")
        row["end"] = float(end) if isinstance(end, (int, float)) and not isinstance(end, bool) and float(end) > row["start"] else None
        for key in ("gain", "duck", "fade_in", "fade_out"):
            try:
                row[key] = max(0.0, float(row[key]))
            except (TypeError, ValueError):
                row[key] = DEFAULT_MUSIC[key]
        out.append(row)
    return out


def _speech_spans(items: Sequence["TimelineItem"], spec: Mapping[str, Any], vo_rows: Sequence[Mapping[str, Any]],
                  dub_sched: Sequence[Mapping[str, Any]]) -> list[tuple[float, float]]:
    """有人说话的时间段：有台词的镜整段 + 旁白句 + 配音句。配乐在这些段里压低。"""

    spans: list[tuple[float, float]] = []
    lines_by_shot = {str(s.get("id")): bool(s.get("lines")) for s in spec.get("shots", ()) if isinstance(s, Mapping)}
    for item in items:
        if item.kind == "shot" and lines_by_shot.get(item.item_id):
            spans.append((item.start, item.start + item.duration))
    for row in vo_rows:
        start = absolute_time(items, row["shot"], row["at"])
        spans.append((start, start + float(row.get("duration") or 2.0)))
    for row in dub_sched:
        spans.append((float(row["abs_start"]), float(row["abs_end"])))
    return spans
DEFAULT_DUCK = 0.15
_LIP_RISK_WORDS = ("特写", "近景", "脸", "嘴", "close-up", "closeup", "close up", "face", "mouth")


def dubbing_plan(spec: Mapping[str, Any]) -> dict[str, Any]:
    """按 ``post_graphics.dubbing`` 算出：哪些镜配音、每镜的说话角色有没有绑 TTS 音色、哪些镜有嘴型对不上的风险。

    配音是后期层的事（不进付费请求），所以制作中也能开关。嘴型风险靠镜头写法解决：
    配音镜用画外说话方式（speech_mode）或不露嘴的机位，这里只提醒不强制。"""

    pg = spec.get("post_graphics") or {}
    cfg = pg.get("dubbing") if isinstance(pg.get("dubbing"), Mapping) else {}
    mode = str(cfg.get("mode") or "off")
    wanted = {str(x) for x in (cfg.get("shots") or ())} if mode == "shots" else None
    duck = float(cfg.get("duck") if cfg.get("duck") is not None else DEFAULT_DUCK)
    cast = spec.get("cast") or {}
    shots: list[str] = []
    missing: dict[str, list[str]] = {}
    lip_risk: list[str] = []
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        sid = str(shot.get("id"))
        lines = [ln for ln in (shot.get("lines") or ()) if isinstance(ln, Mapping) and str(ln.get("zh") or "").strip()]
        if mode == "off" or not lines or (wanted is not None and sid not in wanted):
            continue
        shots.append(sid)
        for ln in lines:
            who = str(ln.get("who") or "")
            member = cast.get(who) if isinstance(cast.get(who), Mapping) else {}
            tts = member.get("voice_tts") if isinstance(member, Mapping) else None
            if not (isinstance(tts, Mapping) and tts.get("voice")):
                missing.setdefault(who or "（未指定说话人）", []).append(sid)
        offscreen = str(shot.get("speech_mode") or "") in {"narration", "offscreen_narration"}
        text = f"{shot.get('shot') or ''} {shot.get('art') or ''}".lower()
        if not offscreen and any(w in text for w in _LIP_RISK_WORDS):
            lip_risk.append(sid)
    return {"mode": mode, "duck": max(0.0, min(1.0, duck)), "shots": shots,
            "missing_tts_cast": {k: sorted(set(v)) for k, v in missing.items()}, "lip_risk": lip_risk}


def render_dub_lines(
    spec: Mapping[str, Any],
    out_dir: str | os.PathLike[str],
    *,
    tts: Callable[[str, Path, Mapping[str, Any]], None] | None = None,
    ffprobe: str = "ffprobe",
) -> list[dict[str, Any]]:
    """把配音镜的每句台词按说话角色的 ``voice_tts`` 渲染成 mp3。

    返回 ``[{shot, index, who, zh, path, duration}]``，顺序 = 镜序 × 句序。同一句（文本 + TTS 参数）已渲染过就复用。
    ``tts(text, destination, profile)`` 可注入（测试用）；默认走音色库的合成器（edge 或模型设置的语音）。"""

    plan = dubbing_plan(spec)
    if not plan["shots"]:
        return []
    if plan["missing_tts_cast"]:
        detail = "；".join(f"{who}（{', '.join(sids)}）" for who, sids in plan["missing_tts_cast"].items())
        raise PostLayerError(f"这些说话角色没有绑定 TTS 音色，无法配音：{detail}——去角色页从音色库绑一个")
    cast = spec.get("cast") or {}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    def default_tts(text: str, destination: Path, profile: Mapping[str, Any]) -> None:
        from genkit.voices import synthesize_with_profile

        synthesize_with_profile(dict(profile), text, destination)

    synth = tts or default_tts
    rows: list[dict[str, Any]] = []
    for shot in spec.get("shots", ()):
        sid = str(shot.get("id"))
        if sid not in plan["shots"]:
            continue
        lines = [ln for ln in (shot.get("lines") or ()) if isinstance(ln, Mapping) and str(ln.get("zh") or "").strip()]
        for index, ln in enumerate(lines):
            who = str(ln.get("who") or "")
            profile = dict((cast.get(who) or {}).get("voice_tts") or {})
            text = str(ln["zh"]).strip()
            digest = hashlib.sha1(json.dumps([text, profile], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:10]
            path = out / f"dub_{sid}_{index:02d}_{digest}.mp3"
            if not path.is_file():
                _synth_once(path, lambda dest: synth(text, dest, profile))
            duration = float(probe_media(path, ffprobe=ffprobe).duration_seconds)
            rows.append({"shot": sid, "index": index, "who": who, "zh": text, "path": str(path), "duration": duration})
    return rows


def _dub_schedule(items: Sequence["TimelineItem"], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """给每句配音定绝对起点：跟字幕同一套时间分配（0.3s 起、均分），一句放不下就顺延 0.15s；
    超出本镜时长的记进 overflow（不裁，人看清单决定加时长还是删词）。"""

    scheduled: list[dict[str, Any]] = []
    overflow: list[str] = []
    by_shot: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_shot.setdefault(row["shot"], []).append(row)
    for item in items:
        if item.kind != "shot" or item.item_id not in by_shot:
            continue
        lines = by_shot[item.item_id]
        usable = max(0.5, item.duration - 0.5)
        slot = usable / len(lines)
        prev_end = item.start
        for k, row in enumerate(lines):
            nominal = item.start + 0.3 + k * slot
            start = max(nominal, prev_end + (0.15 if k else 0.0))
            end = start + row["duration"]
            if end > item.start + item.duration + 0.2:
                overflow.append(f"{item.item_id} 第 {k + 1} 句「{row['zh']}」到 {end - item.start:.1f}s，超过本镜 {item.duration:.1f}s")
            scheduled.append({**row, "at": round(start - item.start, 3), "abs_start": round(start, 3), "abs_end": round(end, 3)})
            prev_end = end
    return scheduled, overflow


# --------------------------------------------------------------------------- UI 插卡


def _split_screen_text(screen: str) -> list[str]:
    parts: list[str] = []
    for chunk in screen.replace("→", "\n").replace("｜", "\n").replace("；", "\n").split("\n"):
        chunk = chunk.strip().strip("「」")
        if chunk:
            parts.append(chunk)
    return parts


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    out: list[str] = []
    current = ""
    for ch in str(text):
        probe = current + ch
        if draw.textlength(probe, font=font) > max_w and current:
            out.append(current)
            current = ch
        else:
            current = probe
    if current:
        out.append(current)
    return out or [""]


def _text_block(draw, xy, text: str, font, fill, max_w: int, line_gap: float = 1.4) -> int:
    x, y = xy
    for line in _wrap(draw, text, font, max_w):
        draw.text((x, y), line, font=font, fill=fill)
        y += int(font.size * line_gap)
    return y


def render_ui_card(
    insert: Mapping[str, Any],
    destination: str | os.PathLike[str],
    *,
    font_path: str | os.PathLike[str] | None = None,
    size: tuple[int, int] = UI_SIZE,
) -> Path:
    """把一条 ``phone_ui_inserts`` 画成整屏 PNG（剧中的手机界面）。

    三种版式，按 ``layout`` 选：``notifications``（锁屏推送堆叠）、``sms``（短信气泡）、``shop``（商品页 + 底部绑定弹层）。
    没写 ``layout`` 时退化成 ``plain``：把 ``lines``（或 ``screen`` 拆行）直接排。仿的是「手机屏」这个结构，不模仿任何真实平台。
    ``layout: "card"`` 另走 ``render_text_card``：纯色整屏 + 居中文字，没有任何手机外观。
    """

    from PIL import Image, ImageDraw, ImageFont

    width, height = size
    font_file = str(font_path or DEFAULT_FONTS["ui"])
    if str(insert.get("layout") or "") == "card":
        # 纯色字幕卡不是「手机屏」：不画状态栏 / 电量 / 圆角卡片，单独一条路，下面四种版式一个像素都不碰
        return render_text_card(insert, destination, font_path=font_file, size=size)
    # 版式里的字号 / 边距 / 坐标都是按 1080x1920 写死的像素：交付画幅不是 9:16 时按短边等比缩（横屏 1920x1080 → 0.5625），
    # 卡才能铺满整屏而不是缩成中间一条竖条、两侧黑边。1080x1920 时 k = 1，每个像素与以前一样
    s = _ui_scaler(width, height)
    try:
        f = {k: ImageFont.truetype(font_file, s(v)) for k, v in {"xl": 64, "l": 52, "m": 42, "s": 34, "xs": 28}.items()}
    except OSError as exc:  # 没有中文字体就别静默出方块
        raise PostLayerError(f"UI font unavailable: {font_file}: {exc}") from exc

    bg, card, line_c = (14, 14, 18), (30, 30, 36), (62, 62, 72)
    white, grey, dim, red, accent = (245, 245, 245), (200, 200, 210), (140, 140, 150), (230, 70, 60), (90, 160, 255)
    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    margin = s(64)
    inner = width - 2 * margin
    # 状态栏：时间 + 电量形状
    draw.text((margin, s(96)), str(insert.get("time") or "23:41"), font=f["s"], fill=grey)
    draw.rounded_rectangle((width - margin - s(70), s(100), width - margin - s(4), s(132)), radius=s(6), outline=grey, width=s(3))
    draw.rectangle((width - margin - s(66), s(104), width - margin - s(26), s(128)), fill=grey)

    # 版式名容错：界面和示例里写的是单数 notification，渲染表用的是 notifications
    layout = {"notification": "notifications", "message": "sms", "text": "plain"}.get(
        str(insert.get("layout") or "plain"), str(insert.get("layout") or "plain")
    )
    if layout == "notifications":
        title = str(insert.get("title") or "通知")
        y = s(260)
        draw.text((margin, y), title, font=f["s"], fill=dim)
        y += s(70)
        for item in insert.get("items") or ():
            item_text = str(item)
            lines = _wrap(draw, item_text, f["m"], inner - s(96))
            h = s(120) + int(f["m"].size * 1.4) * len(lines)
            draw.rounded_rectangle((margin, y, width - margin, y + h), radius=s(36), fill=card)
            draw.ellipse((margin + s(36), y + s(38), margin + s(84), y + s(86)), fill=accent)
            draw.text((margin + s(108), y + s(40)), str(insert.get("app") or title), font=f["xs"], fill=dim)
            draw.text((width - margin - s(120), y + s(40)), "刚刚", font=f["xs"], fill=dim)
            _text_block(draw, (margin + s(48), y + s(96)), item_text, f["m"], white, inner - s(96))
            y += h + s(24)
    elif layout == "sms":
        sender = str(insert.get("sender") or "短信")
        draw.rectangle((0, s(190), width, s(300)), fill=card)
        draw.text(((width - draw.textlength(sender, font=f["l"])) / 2, s(216)), sender, font=f["l"], fill=white)
        y = s(380)
        stamp = str(insert.get("stamp") or "今天 09:00")
        draw.text(((width - draw.textlength(stamp, font=f["xs"])) / 2, y), stamp, font=f["xs"], fill=dim)
        y += s(70)
        msg = str(insert.get("message") or "")
        lines = _wrap(draw, msg, f["m"], inner - s(160))
        h = s(72) + int(f["m"].size * 1.45) * len(lines)
        draw.rounded_rectangle((margin, y, width - margin - s(80), y + h), radius=s(40), fill=card)
        _text_block(draw, (margin + s(44), y + s(36)), msg, f["m"], white, inner - s(160), 1.45)
    elif layout == "shop":
        app = str(insert.get("app") or "商城")
        draw.rectangle((0, s(190), width, s(300)), fill=card)
        draw.text((margin, s(218)), app, font=f["l"], fill=white)
        # 商品图：一块空的圆角占位（商品剪影）
        draw.rounded_rectangle((margin, s(340), width - margin, s(780)), radius=s(36), fill=(40, 40, 48))
        draw.rounded_rectangle((width // 2 - s(150), s(400), width // 2 + s(150), s(720)), radius=s(18), fill=(22, 22, 26), outline=line_c, width=s(4))
        draw.line((width // 2 + s(190), s(430), width // 2 + s(206), s(690)), fill=grey, width=s(10))
        y = s(820)
        y = _text_block(draw, (margin, y), str(insert.get("title") or ""), f["xl"], white, inner, 1.3) + s(8)
        y = _text_block(draw, (margin, y), str(insert.get("subtitle") or ""), f["s"], grey, inner) + s(24)
        draw.text((margin, y), str(insert.get("price") or ""), font=f["l"], fill=red)
        y += s(80)
        draw.text((margin, y), str(insert.get("sold") or ""), font=f["xs"], fill=dim)
        button = str(insert.get("button") or "立即购买")
        draw.rounded_rectangle((margin, height - s(330), width - margin, height - s(220)), radius=s(55), fill=red)
        draw.text(((width - draw.textlength(button, font=f["l"])) / 2, height - s(306)), button, font=f["l"], fill=white)
        sheet = insert.get("sheet")
        if isinstance(sheet, Mapping):
            top = height - s(760)
            draw.rounded_rectangle((0, top, width, height), radius=s(60), fill=(36, 36, 44))
            draw.text((margin, top + s(56)), str(sheet.get("title") or ""), font=f["m"], fill=white)
            draw.rounded_rectangle((margin, top + s(140), width - margin, top + s(240)), radius=s(24), fill=(20, 20, 24), outline=line_c, width=s(3))
            draw.text((margin + s(36), top + s(166)), str(sheet.get("field") or ""), font=f["m"], fill=white)
            draw.rounded_rectangle((margin, top + s(300), width - margin, top + s(400)), radius=s(50), fill=accent)
            sb = str(sheet.get("button") or "绑定")
            draw.text(((width - draw.textlength(sb, font=f["l"])) / 2, top + s(322)), sb, font=f["l"], fill=white)
    else:
        lines = [str(x) for x in (insert.get("lines") or _split_screen_text(str(insert.get("screen") or "")))]
        if not lines:
            raise PostLayerError(f"插卡 {insert.get('id')} 没有文字：写 lines 或 screen")
        draw.rounded_rectangle((margin, s(220), width - margin, height - s(260)), radius=s(48), fill=card, outline=line_c, width=s(3))
        y = s(290)
        for i, line in enumerate(lines):
            y = _text_block(draw, (margin + s(56), y), line, f["xl"] if i == 0 else f["m"], white if i == 0 else grey, inner - s(112)) + s(16)
            if y > height - s(340):
                break
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    return dest


def _ui_scaler(width: int, height: int) -> Callable[[float], int]:
    """插卡版式的像素常量都以 1080x1920 为基准：按短边等比缩到交付画幅（横屏取高的那一边），1080x1920 时原样返回。"""

    k = min(width / UI_SIZE[0], height / UI_SIZE[1])
    if abs(k - 1.0) < 1e-9:
        return lambda v: int(v)
    return lambda v: int(round(v * k))


# --------------------------------------------------------------------------- 纯色字幕卡
# 片头「AI 生成」全屏标识、结尾字幕卡、黑底字幕板都要「设备消失」——plain 版式会画成手机屏
# （状态栏、电量、圆角卡片），跟全片的设计意图冲突。card 版式只有纯色底 + 居中文字；静帧放几秒会像死机，
# 所以默认 motion=push：合成时从 1.00 极缓推到 1.03（在滤镜里做，PNG 本身是静的，预览图与成片同一张）。

CARD_MOTIONS = ("push", "none")
CARD_PUSH_TO = 1.03
_CARD_FONT_SIZE = 56       # 以 1080 宽为基准；整张卡同一字号，结尾字幕卡的每一行分量相同
_CARD_LINE_GAP = 1.6
_CARD_MARGIN = 120


def _hex_rgb(value: Any, default: str) -> tuple[int, int, int]:
    """``#RRGGBB`` → (r, g, b)；写坏了回落默认值（接口层另有校验，这里只保证不崩）。"""

    text = str(value or "").strip().lstrip("#")
    if len(text) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        text = default.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def card_lines(insert: Mapping[str, Any]) -> list[str]:
    """字幕卡的文字行：``lines`` 优先，否则 ``screen`` 拆行（同 plain）。空串行保留，当作行间空白。"""

    raw = insert.get("lines")
    if isinstance(raw, (list, tuple)):
        return [str(x if x is not None else "").strip() for x in raw]
    return _split_screen_text(str(insert.get("screen") or ""))


def render_text_card(
    insert: Mapping[str, Any],
    destination: str | os.PathLike[str],
    *,
    font_path: str | os.PathLike[str] | None = None,
    size: tuple[int, int] = UI_SIZE,
) -> Path:
    """``layout: "card"``：整屏纯色底（``background``，默认 #000000）+ 居中文字（``color``，默认 #FFFFFF）。"""

    from PIL import Image, ImageDraw, ImageFont

    width, height = size
    font_file = str(font_path or DEFAULT_FONTS["ui"])
    lines = card_lines(insert)
    if not any(lines):
        raise PostLayerError(f"字幕卡 {insert.get('id')} 没有文字：写 lines 或 screen")
    # 字号与边距按短边等比缩到交付画幅（同 render_ui_card）：横屏交付时整屏都是底色，不再是中间一条竖条
    s = _ui_scaler(width, height)
    font_size, margin = s(_CARD_FONT_SIZE), s(_CARD_MARGIN)
    try:
        font = ImageFont.truetype(font_file, font_size)
    except OSError as exc:
        raise PostLayerError(f"后期文字字体不可用：{font_file}：{exc}") from exc
    image = Image.new("RGB", (width, height), _hex_rgb(insert.get("background"), "#000000"))
    draw = ImageDraw.Draw(image)
    fill = _hex_rgb(insert.get("color"), "#FFFFFF")
    rows: list[str] = []
    for line in lines:
        rows.extend(_wrap(draw, line, font, width - 2 * margin) if line else [""])
    step = int(font_size * _CARD_LINE_GAP)
    top = (height - step * len(rows)) / 2
    for k, row in enumerate(rows):
        if row:
            # anchor=mm：按字形的几何中心对齐，行块整体上下居中，不受字体上行/下行高度差影响
            draw.text((width / 2, top + (k + 0.5) * step), row, font=font, fill=fill, anchor="mm")
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    return dest


def _card_push_filter(insert: Mapping[str, Any] | None, duration: float, fps: float) -> str:
    """字幕卡的极缓推：逐帧从 1.00 放到 1.03，中心不动。非 card 或 motion=none → 空串（滤镜图逐字不变）。

    用 perspective 按四个角的小数坐标重采样，不用 zoompan：zoompan 的裁切框起点和宽高都取整像素，整段推下来字的边缘
    只挪 5px 左右，取整误差和运动本身一样大，字的左沿会隔几帧往回跳 1px，静止的字幕卡上看着就是抖。perspective 的
    坐标精度到 1/256 像素，逐帧单调地放大。它在插卡 PNG 的原尺寸上做（W/H 是输入宽高），后面照常 scale/pad 到交付尺寸，
    交付画幅不是 9:16 时也不会被拉变形。注意 perspective 的 on 从 1 数起，所以第一帧用 on-1 = 0，正好是原图。"""

    if not isinstance(insert, Mapping) or str(insert.get("layout") or "") != "card":
        return ""
    if str(insert.get("motion") or "push") != "push":
        return ""
    frames = max(2, int(round(duration * fps)))
    # 四角各往里收 W/2·(1-1/zoom)：取中间 1/zoom 的一块铺满输出 = 以中心为原点放大 zoom 倍
    k = f"(1-1/(1+{CARD_PUSH_TO - 1:.3f}*min(max(on-1,0)/{frames - 1},1)))"
    near_x, far_x, near_y, far_y = f"W/2*{k}", f"W-W/2*{k}", f"H/2*{k}", f"H-H/2*{k}"
    return (f"perspective=x0='{near_x}':y0='{near_y}':x1='{far_x}':y1='{near_y}':"
            f"x2='{near_x}':y2='{far_y}':x3='{far_x}':y3='{far_y}':interpolation=cubic:eval=frame,")


# --------------------------------------------------------------------------- 文字角标
# 全程「AI 生成」角标、片头居中标识。post_graphics.labels =
# [{text, start?=0, end?=null(到片尾), position, size?=34, color?="#FFFFFF", opacity?=0.9, box?=true}]。
# 用 Pillow 把文字画成透明 PNG 再 overlay（enable=between），不用 drawtext：Windows 上 drawtext 走 fontconfig
# 可能段错误，角标又是全程叠的，出事就是整片失败。
# 位置按 delivery.overlay_preset / overlay_regions 的平台遮挡区避让：top_* 落在顶栏之下，bottom_center 落在底栏之上；
# center 是有意压在画面正中的片头标识，不避让。文字可以换行（两行的片头标识）；字号大到放不进画面时自动缩小，不出画。

LABEL_POSITIONS = ("top_right", "top_left", "top_center", "center", "bottom_center")
DEFAULT_LABEL = {"start": 0.0, "end": None, "size": 34, "color": "#FFFFFF", "opacity": 0.9, "box": True}
LABEL_MAX_CHARS = 24
_LABEL_MARGIN = 48         # 以 1080 宽为基准：离画面边缘的距离
_LABEL_GAP = 16            # 以 1080 宽为基准：离遮挡区边缘的距离
_LABEL_MIN_PX = 8          # 缩字号放进画面时的下限（像素）


def label_text(value: Any) -> str:
    """角标文字的规范写法：统一换行符，每行去首尾空白，去掉首尾空行（中间的空行留着当行距）。

    允许换行——片头居中标识常写成两行（中文片名 / 英文片名）。字数上限按去掉换行后的字数算。"""

    lines = [ln.strip() for ln in str(value if value is not None else "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def _label_align(position: Any) -> str:
    """多行角标的对齐：靠左的左对齐、靠右的右对齐，其余居中——和角标贴的那条边一致，看着才像一块。"""

    position = str(position or "")
    return "left" if position.endswith("_left") else "right" if position.endswith("_right") else "center"


def _label_spacing(px: int) -> int:
    """多行角标的行间距：量和画必须用同一个值，否则画布高度和实际字块对不上。"""

    return int(round(px * 0.25))


def _label_font(font_file: str, px: int) -> Any:
    from PIL import ImageFont

    try:
        return ImageFont.truetype(font_file, px)
    except OSError as exc:
        raise PostLayerError(f"后期文字字体不可用：{font_file}：{exc}") from exc


def _label_geometry(text: str, font: Any, px: int, box: bool, align: str) -> tuple[int, int, int, int, int, int, int]:
    """量一条角标：(描边宽, 字形左, 字形上, 左右留白, 上下留白, 画布宽, 画布高)。

    多行用 multiline_textbbox 量整块：font.getbbox 只当一行量，画布只有一行高，第二行会画到画布外被裁掉。
    单行仍走 getbbox（与多行支持加进来之前逐像素一致）。"""

    from PIL import Image, ImageDraw

    stroke = max(1, int(round(px / 17)))
    if "\n" in text:
        box_f = ImageDraw.Draw(Image.new("L", (1, 1))).multiline_textbbox(
            (0, 0), text, font=font, spacing=_label_spacing(px), align=align, stroke_width=stroke)
        # 新版 Pillow 这里可能给小数：往外取整，宁可多 1px 透明边也别切掉字的边
        left, top, right, bottom = math.floor(box_f[0]), math.floor(box_f[1]), math.ceil(box_f[2]), math.ceil(box_f[3])
    else:
        left, top, right, bottom = font.getbbox(text, stroke_width=stroke)
    pad_x, pad_y = (int(round(px * 0.45)), int(round(px * 0.28))) if box else (0, 0)
    return stroke, left, top, pad_x, pad_y, (right - left) + 2 * pad_x, (bottom - top) + 2 * pad_y


def fit_label_size(
    label: Mapping[str, Any],
    *,
    max_width: float,
    max_height: float,
    font_path: str | os.PathLike[str] | None = None,
    scale: float = 1.0,
) -> float:
    """角标放得进 ``max_width × max_height`` 像素时原样返回 size；放不下就按比例缩字号直到放得下（最小 8px），
    返回缩后的 size（仍以 1080 宽为基准）。

    接口层允许 24 个字、字号到 200，这两样一起用时一行字能有几千像素宽；不缩的话右上角的角标会从左边出画，
    居中的片头标识两头都被切掉，而且没有任何报错。"""

    cfg = {**DEFAULT_LABEL, **{k: v for k, v in label.items() if v is not None}}
    text = label_text(cfg.get("text"))
    font_file = str(font_path or DEFAULT_FONTS["ui"])
    align = _label_align(cfg.get("position"))

    def measure(px: int) -> tuple[int, int]:
        w, h = _label_geometry(text, _label_font(font_file, px), px, bool(cfg["box"]), align)[5:]
        return w, h

    wanted = max(_LABEL_MIN_PX, int(round(float(cfg["size"]) * scale)))
    px = wanted
    w, h = measure(px)
    if w <= max_width and h <= max_height:
        return float(cfg["size"])
    while px > _LABEL_MIN_PX and (w > max_width or h > max_height):
        # 宽高都随字号近似线性变化，按超出最多的那一边一步缩到位；取整后仍超就再少 1px
        px = max(_LABEL_MIN_PX, min(px - 1, int(px * min(max_width / w, max_height / h))))
        w, h = measure(px)
    # 留白、描边宽度按字号取整，一步缩可能多缩了 1–2px：往回找放得下的最大字号
    while px + 1 < wanted:
        w, h = measure(px + 1)
        if w > max_width or h > max_height:
            break
        px += 1
    return px / scale


def render_label(
    label: Mapping[str, Any],
    destination: str | os.PathLike[str],
    *,
    font_path: str | os.PathLike[str] | None = None,
    scale: float = 1.0,
) -> tuple[Path, int, int]:
    """把一条角标画成紧贴文字的透明 PNG（半透明圆角底 + 细描边，整体再乘 opacity），返回 (路径, 宽, 高)。

    文字可以换行：多行按 ``position`` 贴的那条边对齐（见 ``_label_align``）。"""

    from PIL import Image, ImageDraw

    cfg = {**DEFAULT_LABEL, **{k: v for k, v in label.items() if v is not None}}
    text = label_text(cfg.get("text"))
    font_file = str(font_path or DEFAULT_FONTS["ui"])
    px = max(_LABEL_MIN_PX, int(round(float(cfg["size"]) * scale)))
    font = _label_font(font_file, px)
    align = _label_align(cfg.get("position"))
    stroke, left, top, pad_x, pad_y, w, h = _label_geometry(text, font, px, bool(cfg["box"]), align)
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if cfg["box"]:
        ImageDraw.Draw(base).rounded_rectangle((0, 0, w - 1, h - 1), radius=int(round(px * 0.3)), fill=(0, 0, 0, 90))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    fill = (*_hex_rgb(cfg["color"], "#FFFFFF"), 255)
    if "\n" in text:
        ImageDraw.Draw(layer).multiline_text((pad_x - left, pad_y - top), text, font=font, fill=fill, spacing=_label_spacing(px),
                                             align=align, stroke_width=stroke, stroke_fill=(0, 0, 0, 180))
    else:
        ImageDraw.Draw(layer).text((pad_x - left, pad_y - top), text, font=font, fill=fill,
                                   stroke_width=stroke, stroke_fill=(0, 0, 0, 180))
    image = Image.alpha_composite(base, layer)
    opacity = max(0.0, min(1.0, float(cfg["opacity"])))
    if opacity < 1.0:
        image.putalpha(image.getchannel("A").point(lambda a: int(round(a * opacity))))
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    return dest, w, h


def label_xy(position: str, size: tuple[int, int], frame: tuple[int, int], regions: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """角标左上角坐标。``regions`` 是交付面遮挡区（overlays.regions_for，比例 0–1）：

    top_* 从上边距起，碰到遮挡区就挪到它下沿之下（抖音右上角 → 顶栏之下、右侧互动栏之上）；bottom_center 反过来往上挪；
    center 不避让。横竖都夹在画面里——自定义遮挡区写得再怪、角标再宽（右上角的 x 本来会算成负数）也不会出画。
    正常情况下 ``label_layers`` 已经把字号缩到放得进页边距以内，这里的夹是最后一道保险。"""

    w, h = size
    width, height = frame
    margin = int(round(_LABEL_MARGIN * width / 1080))
    gap = int(round(_LABEL_GAP * width / 1080))
    if position.endswith("_left"):
        x = margin
    elif position.endswith("_right"):
        x = width - margin - w
    else:
        x = (width - w) // 2
    x = max(0, min(width - w, x))
    if position == "center":
        return x, max(0, (height - h) // 2)
    boxes = [(r["x"] * width, r["y"] * height, (r["x"] + r["w"]) * width, (r["y"] + r["h"]) * height) for r in regions]

    def hit(y: float) -> list[tuple[float, float, float, float]]:
        return [b for b in boxes if b[0] < x + w and x < b[2] and b[1] < y + h and y < b[3]]

    upward = position == "bottom_center"
    y = float(height - margin - h) if upward else float(margin)
    for _ in range(len(boxes) + 1):
        blocking = hit(y)
        if not blocking:
            break
        y = (min(b[1] for b in blocking) - gap - h) if upward else (max(b[3] for b in blocking) + gap)
    return x, int(round(max(0.0, min(float(height - h), y))))


def label_layers(
    spec: Mapping[str, Any],
    out_dir: str | os.PathLike[str],
    *,
    width: int,
    height: int,
    timeline_end: float,
    font_path: str | os.PathLike[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """把 ``post_graphics.labels`` 逐条画成 PNG 并定好位置与起止（end 留空 = 片尾，超过片尾截断）。

    字号放不进页边距以内（宽 = 画面宽 - 两侧边距，高同理）时自动缩小（``fit_label_size``），缩过的在行里记
    ``shrunk: true`` 和实际 ``size``，写进 manifest，事后查得到为什么字比写的小。
    角标之间也互相避让：先放好的角标当作一块遮挡区传给后面的（只算时段有重叠的），同一行里一条超长的左上角
    角标不会压在右上角的「AI 生成」上——两条各自缩到页边距以内还是能撞在同一个 y 上。
    返回 (要叠的行, 跳过的文字)：起点已经在片尾之后的角标没东西可叠，记进跳过清单而不是报错。"""

    from .overlays import regions_for

    regions = regions_for(spec)
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    placed: list[dict[str, Any]] = []      # 已放好的角标矩形（比例坐标 + 起止），给后面的角标避让
    labels = (spec.get("post_graphics") or {}).get("labels") or ()
    scale = width / 1080
    margin = int(round(_LABEL_MARGIN * scale))
    for i, label in enumerate(labels):
        if not isinstance(label, Mapping) or not label_text(label.get("text")):
            continue
        cfg = {**DEFAULT_LABEL, **{k: v for k, v in label.items() if v is not None}}
        text = label_text(cfg["text"])
        start = max(0.0, float(cfg["start"] or 0.0))
        end = min(float(cfg["end"]), timeline_end) if cfg["end"] is not None else timeline_end
        if start >= timeline_end or end <= start:
            skipped.append(text)
            continue
        position = str(cfg.get("position") or "top_right")
        cfg["position"] = position
        wanted = float(cfg["size"])
        cfg["size"] = fit_label_size(cfg, max_width=width - 2 * margin, max_height=height - 2 * margin,
                                     font_path=font_path, scale=scale)
        path, w, h = render_label(cfg, Path(out_dir) / f"label_{i + 1:02d}.png", font_path=font_path, scale=scale)
        overlapping = [b for b in placed if b["start"] < end and start < b["end"]]
        x, y = label_xy(position, (w, h), (width, height), [*regions, *overlapping])
        placed.append({"x": x / width, "y": y / height, "w": w / width, "h": h / height, "start": start, "end": end})
        rows.append({"index": i, "text": text, "position": position, "start": round(start, 3), "end": round(end, 3),
                     "x": x, "y": y, "width": w, "height": h, "size": round(float(cfg["size"]), 1),
                     "shrunk": float(cfg["size"]) < wanted, "path": str(path)})
    return rows, skipped


# --------------------------------------------------------------------------- 时间线


@dataclass(frozen=True)
class TimelineItem:
    kind: str            # "shot" | "ui"
    item_id: str
    duration: float
    start: float
    source: Path         # mp4（shot）或 png（ui）


def _shot_durations(spec: Mapping[str, Any], video_dir: Path, ffprobe: str) -> dict[str, float]:
    durations: dict[str, float] = {}
    for shot in spec.get("shots", ()):
        shot_id = str(shot["id"])
        path = video_dir / f"{shot_id}.mp4"
        if not path.is_file():
            raise PostLayerError(f"missing rendered shot: {path}")
        durations[shot_id] = float(probe_media(path, ffprobe=ffprobe).duration_seconds)
    return durations


def build_timeline(
    spec: Mapping[str, Any],
    *,
    video_dir: str | os.PathLike[str],
    ui_dir: str | os.PathLike[str],
    ffprobe: str = "ffprobe",
    durations: Mapping[str, float] | None = None,
) -> list[TimelineItem]:
    """镜次顺序 + 插卡（按 ``after``；``__start__`` = 第一镜之前）+ 交叉溶解重叠 → 每一项的绝对起点。

    交叉溶解的量取自 ``assembly.delivery_settings``（video_params + delivery 合并后的交付合同，与装配版同一口径）。
    每个接缝的实际重叠夹成 ``min(交叉溶解, 前一项时长/2, 本项时长/2)``：插卡比溶解时长还短时，不夹的话下一项的
    起点会早于插卡的起点，ffmpeg 的 xfade 第二路没帧可溶，不出帧也不退出，直到超时。相邻两项的重叠量可以
    从 ``items[k-1].start + duration - items[k].start`` 算回来，合成时按接缝取。

    插卡的 ``after`` 自引用或互相引用（环）在这里报错：环里的卡从任何镜出发都到不了，不报的话成片里没有它、
    清单却说有。``after`` 指向不存在的 id 的卡不进时间线（``unplaced_inserts`` 列出来，合成时写进清单）。"""

    video_root, ui_root = Path(video_dir), Path(ui_dir)
    shot_durations = dict(durations) if durations is not None else _shot_durations(spec, video_root, ffprobe)
    inserts = [x for x in ((spec.get("post_graphics") or {}).get("phone_ui_inserts") or ()) if isinstance(x, Mapping)]
    by_id = {str(x.get("id")): x for x in inserts}
    for insert in inserts:
        chain = [str(insert.get("id"))]
        cursor_id = str(insert.get("after"))
        while cursor_id in by_id:
            chain.append(cursor_id)
            if cursor_id in chain[:-1]:
                raise PostLayerError(f"插卡 {' → '.join(chain)} 的 after 形成了环（自引用或互相引用），没有接到任何镜上")
            cursor_id = str(by_id[cursor_id].get("after"))
    by_after: dict[str, list[Mapping[str, Any]]] = {}
    for insert in inserts:
        by_after.setdefault(str(insert.get("after")), []).append(insert)
    xfade = delivery_settings(spec)[3]

    ordered: list[tuple[str, str, float, Path]] = []
    ui_count = 0

    def push_inserts(after_id: str) -> None:
        nonlocal ui_count
        for insert in by_after.get(after_id, ()):
            iid = str(insert.get("id"))
            # 卡图按时间线顺序编号起名，不拿 id 当文件名：id 是自由文本，「../../x」「D:/p/f」会把 PNG 写到 08_ui 外面、
            # 覆盖同名文件（F1）；% 会被 ffmpeg 的 image2 当成序号模板（F6）；NTFS 不分大小写，UI_A / ui_a 会写成同一个文件
            source = ui_root / f"ui_{ui_count:03d}.png"
            ui_count += 1
            ordered.append(("ui", iid, insert_duration(insert), source))
            push_inserts(iid)  # 插卡后面还可以接插卡

    # after="__start__" 的片头卡排在第一镜之前：第一镜的起点随之后移，后面所有按 item.start 算的绝对时间
    # （字幕、配音、旁白、画面文字、配乐压低段、静音区间）自动跟着走，不用逐层改
    push_inserts(START_ANCHOR)
    for shot in spec.get("shots", ()):
        shot_id = str(shot["id"])
        ordered.append(("shot", shot_id, shot_durations[shot_id], video_root / f"{shot_id}.mp4"))
        push_inserts(shot_id)

    items: list[TimelineItem] = []
    cursor = 0.0
    for index, (kind, iid, dur, source) in enumerate(ordered):
        start = 0.0 if index == 0 else cursor - min(xfade, ordered[index - 1][2] / 2, dur / 2)
        items.append(TimelineItem(kind=kind, item_id=iid, duration=dur, start=start, source=source))
        cursor = start + dur
    return items


def insert_duration(insert: Mapping[str, Any]) -> float:
    """插卡的时长：留空 = 4 秒；别的必须是大于 0 的数（写盘前的校验会拦，这里兜手改 spec 文件的情况——
    以前 ``float(insert.get("dur") or 4.0)`` 把 "abc" 的 ValueError 原样送进任务面板）。"""

    raw = insert.get("dur")
    if raw in (None, ""):
        return INSERT_DEFAULT_SECONDS
    dur = _seconds(raw)
    if dur is None or not math.isfinite(dur) or dur <= 0:
        raise PostLayerError(f"插卡「{insert.get('id')}」的 dur 要是大于 0 的秒数，现在是「{raw}」")
    return dur


def unplaced_inserts(spec: Mapping[str, Any], items: Sequence[TimelineItem]) -> list[dict[str, Any]]:
    """``after`` 指向不存在的镜号 / 插卡 id、没进时间线的插卡（接口写盘前会拦；手改 / 老项目文件才会有）。"""

    placed = {x.item_id for x in items if x.kind == "ui"}
    inserts = [x for x in ((spec.get("post_graphics") or {}).get("phone_ui_inserts") or ()) if isinstance(x, Mapping)]
    insert_ids = {str(x.get("id")) for x in inserts}
    out: list[dict[str, Any]] = []
    for insert in inserts:
        if str(insert.get("id")) in placed:
            continue
        after = str(insert.get("after") or "")
        # 接在另一张没进时间线的卡后面：那张卡的 after 才是写错的地方，这张只是跟着掉了
        reason = (f"after「{after}」指向的插卡也没有接到时间线上" if after in insert_ids
                  else f"after「{after}」既不是镜号也不是别的插卡 id，没有接到时间线上")
        out.append({"id": str(insert.get("id")), "after": after, "reason": reason})
    return out


def seam_overlaps(items: Sequence[TimelineItem]) -> list[float]:
    """相邻两项的实际重叠秒数（第 k 项与第 k-1 项之间；第 0 项记 0）——合成时每个接缝按它做交叉溶解。"""

    out = [0.0]
    for prev, cur in zip(items, items[1:]):
        out.append(max(0.0, round(prev.start + prev.duration - cur.start, 3)))
    return out


def absolute_time(items: Sequence[TimelineItem], item_id: str, offset: float) -> float:
    for item in items:
        if item.item_id == item_id:
            return item.start + max(0.0, float(offset))
    raise PostLayerError(f"旁白 / 画面文字挂在的「{item_id}」不在时间线上（镜号不存在，或插卡没接到任何镜上）")


# --------------------------------------------------------------------------- 旁白压原声 / 绝对静音（混音层）

# 旁白在时压原声（2026-09-22 起）：以前只要有一句旁白，全片原声都乘 0.55，连没旁白的几分钟环境声也一起掉，
# 原声和配乐的配比整片都变了。现在只压旁白那几秒：每句占 [起点-LEAD, 终点+TAIL]，前 LEAD 秒线性压到 0.55、
# 旁白发声的整段保持 0.55、后 TAIL 秒线性放回来；时段外原声一点不动。斜坡是为了不在句首句尾听到音量跳一下。
VO_DUCK = 0.55
VO_DUCK_LEAD = 0.15
# 旁白字幕开着时，同一镜的台词字幕在旁白开口前多少秒让出（两条字幕画在同一个 y 上，叠着不能看）
SUBTITLE_YIELD_GAP = 0.1
VO_DUCK_TAIL = 0.25
SILENCE_MAX_SECONDS = 5.0
# 静音区间的秒数比较留的容差：人按小数写的秒，两段首尾相接时浮点加法会差一点点（0.1 + 0.2 = 0.30000000000000004），
# 「超出本镜」和「互相重叠」都按这个容差判，别把合法的写法拒掉
_SILENCE_EPS = 1e-6


def _seconds(value: Any) -> float | None:
    """有限的数字（不含布尔、inf、nan）→ float；别的一律 None（at=1e400 以前会把「at + dur = infs」写进文案）。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _minus(value: float) -> str:
    """``t{_minus(a)}`` → ``t-1.250`` / ``t+0.050``：别写出 ``t--0.050`` 这种要靠表达式解析器宽容的串。"""

    return f"-{value:.3f}" if value >= 0 else f"+{-value:.3f}"


def vo_duck_volume(spans: Sequence[tuple[float, float]]) -> str:
    """旁白时段压原声的 volume 表达式（配 ``eval=frame`` 逐帧求值）。

    每句一个梯形权重：``[起点-LEAD, 起点]`` 从 0 线性升到 1，``[起点, 终点]`` 保持 1，``[终点, 终点+TAIL]`` 线性回到 0；
    增益 = 1 − (1 − VO_DUCK) × 权重。两句的压低时段挨上或重叠（上一句的放回斜坡碰到下一句的压下斜坡）就并成一段，
    中间整段保持压低——否则两句之间原声会冒一下又被压回去。"""

    merged: list[list[float]] = []
    for start, end in sorted((float(a), float(b)) for a, b in spans):
        if merged and start - VO_DUCK_LEAD <= merged[-1][1] + VO_DUCK_TAIL:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    terms = [
        f"clip(min((t{_minus(start - VO_DUCK_LEAD)})/{VO_DUCK_LEAD:.3f},({end + VO_DUCK_TAIL:.3f}-t)/{VO_DUCK_TAIL:.3f}),0,1)"
        for start, end in merged
    ]
    return f"1-{1 - VO_DUCK:.3f}*clip({'+'.join(terms)},0,1)"


def validate_silence(silence: Any, shots: Sequence[Mapping[str, Any]] | None) -> list[str]:
    """``post_graphics.silence`` 的形状校验（后期块写盘前由 routers/post.py 调用）：镜号存在；at ≥ 0；
    0 < dur ≤ 5；at + dur 不超过这一镜的计划时长（成片的实际时长合成时再核）；同一镜里的几条不重叠。"""

    if silence is None:
        return []
    if not isinstance(silence, list):
        return ["post_graphics.silence 必须是数组"]
    by_id = {str(s.get("id")): s for s in (shots or ()) if isinstance(s, Mapping)}
    errors: list[str] = []
    taken: dict[str, list[tuple[float, float, int]]] = {}
    for i, entry in enumerate(silence):
        if not isinstance(entry, Mapping):
            errors.append(f"silence[{i}] 必须是对象")
            continue
        sid = str(entry.get("shot"))
        if sid not in by_id:
            errors.append(f"silence[{i}] 的 shot「{entry.get('shot')}」不是本集的镜号")
        at, dur = _seconds(entry.get("at")), _seconds(entry.get("dur"))
        if at is None or at < 0:
            errors.append(f"silence[{i}].at 要是不小于 0 的秒数，从这一镜在后期时间轴上的起点算")
            at = None
        if dur is None or not 0 < dur <= SILENCE_MAX_SECONDS:
            errors.append(f"silence[{i}].dur 要大于 0、不超过 {SILENCE_MAX_SECONDS:g} 秒")
            dur = None
        if entry.get("label") is not None and not isinstance(entry.get("label"), str):
            errors.append(f"silence[{i}].label 要是文字")
        if sid not in by_id or at is None or dur is None:
            continue
        planned = _seconds(by_id[sid].get("dur"))
        if planned is not None and at + dur > planned + _SILENCE_EPS:
            errors.append(f"silence[{i}] 超出了镜 {sid} 的时长：at + dur = {at + dur:g}s，这一镜 {planned:g}s")
        for lo, hi, j in taken.get(sid, ()):
            # 首尾相接（这一段的 at 恰好是上一段的 at + dur）不算重叠；两边都让出容差，小数秒的加法误差不误判
            if at < hi - _SILENCE_EPS and lo < at + dur - _SILENCE_EPS:
                errors.append(f"silence[{i}] 和 silence[{j}] 在镜 {sid} 里重叠了")
        taken.setdefault(sid, []).append((at, at + dur, i))
    return errors


def silence_overrun_warnings(spec: Mapping[str, Any]) -> list[str]:
    """镜头改短以后，挂在它上面的静音区间超出了新时长：给一条 ``shot X: 影响——改法`` 的提醒（镜头编辑的响应和
    概览的问题卡都带上）。

    镜头编辑不看后期块，所以改短不拦；但后期块写盘前的校验（validate_silence）每次查整份 spec，这条不改，
    之后旁白、插卡、配乐、给旁白绑音色……任何一次后期块保存都会 400。在改短的当下就说出来，别等到别处才撞上。
    只报「超出」这一种——镜号不存在的由删镜时的关联检查拦，形状不对的写盘时就进不来。"""

    pg = spec.get("post_graphics")
    silence = pg.get("silence") if isinstance(pg, Mapping) else None
    if not isinstance(silence, list):
        return []
    by_id = {str(s.get("id")): s for s in spec.get("shots", ()) if isinstance(s, Mapping)}
    out: list[str] = []
    for i, entry in enumerate(silence):
        if not isinstance(entry, Mapping) or str(entry.get("shot")) not in by_id:
            continue
        sid = str(entry.get("shot"))
        at, dur, planned = _seconds(entry.get("at")), _seconds(entry.get("dur")), _seconds(by_id[sid].get("dur"))
        if at is None or dur is None or planned is None or at + dur <= planned + _SILENCE_EPS:
            continue
        out.append(f"shot {sid}: 静音区间 silence[{i}] 到 {at + dur:g}s 才结束，超出了这一镜的时长 {planned:g}s——"
                   "把这一镜改长，或改小 post_graphics.silence 里这一条的 at / dur；不改的话后期块再保存会被拦下")
    return out


def silence_windows(spec: Mapping[str, Any], items: Sequence[TimelineItem]) -> list[dict[str, Any]]:
    """``post_graphics.silence`` → 时间线上的绝对区间 ``[{shot, at, dur, label, start, end}]``（写进 post.json）。

    ``at`` 相对这一镜在后期时间轴上的起点（交叉溶解算进去了，所以跟剧本的名义时间码可能差零点几秒）。
    末端超出这一镜实际时长的截到镜尾——生成出来的镜头常比计划短零点几秒，这是误差；起点就在镜外的报错，那是写错了。"""

    entries = (spec.get("post_graphics") or {}).get("silence") or ()
    shots = {x.item_id: x for x in items if x.kind == "shot"}
    rows: list[dict[str, Any]] = []
    for i, entry in enumerate(entries if isinstance(entries, (list, tuple)) else ()):
        at = entry.get("at") if isinstance(entry, Mapping) else None
        dur = entry.get("dur") if isinstance(entry, Mapping) else None
        if (not isinstance(at, (int, float)) or isinstance(at, bool) or at < 0
                or not isinstance(dur, (int, float)) or isinstance(dur, bool) or not 0 < dur <= SILENCE_MAX_SECONDS):
            raise PostLayerError(f"静音区间 silence[{i}] 写得不对：要是 {{shot, at, dur}} 对象，at 不小于 0，"
                                 f"dur 大于 0、不超过 {SILENCE_MAX_SECONDS:g} 秒")
        sid = str(entry.get("shot"))
        item = shots.get(sid)
        if item is None:
            raise PostLayerError(f"静音区间 silence[{i}] 的镜号「{sid}」不在时间线里")
        if at >= item.duration:
            raise PostLayerError(f"静音区间 silence[{i}] 从镜 {sid} 的 {float(at):g}s 开始，但这一镜成片只有 "
                                 f"{item.duration:.2f}s——改小 at 或删掉这条")
        start = item.start + float(at)
        end = item.start + min(float(at) + float(dur), item.duration)
        rows.append({"shot": sid, "at": float(at), "dur": float(dur), "label": str(entry.get("label") or ""),
                     "start": round(start, 3), "end": round(end, 3)})
    return rows


def silence_filter(rows: Sequence[Mapping[str, Any]]) -> str:
    """接在 loudnorm 后面的静音段：整条混音（原声、配音、旁白、配乐）在这些区间里乘 0——数字静音。

    放在响度归一之后，是因为单遍 loudnorm 会把安静段往上抬，放在前面等于白压。为什么不用
    ``volume=0:enable='between(t,…)'``：loudnorm 动态模式先攒 3 秒再按大块吐帧，enable 按帧起点判断，
    边界会差出一整块（实测整段一帧都没被静音）；aeval 逐采样算 t，边界精确到采样。先 aformat 回 48k，
    省得在 loudnorm 输出的 192k 上逐采样求值（编码时本来也要转 48k）。没有静音区间返回空串，滤镜图不变。"""

    if not rows:
        return ""
    spans = "+".join(f"between(t,{float(r['start']):.3f},{float(r['end']):.3f})" for r in rows)
    return (",aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"aeval=exprs='val(ch)*not({spans})':channel_layout=same")


# --------------------------------------------------------------------------- 合成
#
# 分段流式合成（2026-09-22 发第一个 tag 前改的）。以前把时间线上每一项都作为一路 ``-i`` 接进同一条 ffmpeg 命令、串成一条
# 线性 xfade / concat 链：ffmpeg 会把全部输入并行解码，后面各路的帧在滤镜图里排队等链前面的节点消费，峰值内存随镜数
# 线性涨（实测 ≈0.83 GB/镜 @1080x1920，60 镜要 50 GB，16 GB 的机器十几镜就 OOM，还留下半个成片）。现在分三步：
#
# 1. 每一项（镜 mp4 / 插卡 PNG）各自归一化成统一规格的中间片（缩放补边、fps、yuv420p、截到该项时长、48k 立体声音轨）——
#    一条命令只解码一路；
# 2. 拼接：交叉溶解为 0 时用 ``-f concat`` 演示器顺序读、流复制（内存与镜数无关）；交叉溶解 >0 时按 ``JOIN_GROUP`` 个一组
#    做 xfade 链出段文件，段文件再按同样的组大小往上接，直到只剩一个（层级式）。一条命令并行解码的画面最多一组，
#    内存被组大小封顶、与镜数无关（24 条 1080x1920×5 s 实测峰值：交叉溶解 0.25 从 20.1 GB 降到 2.2 GB，交叉溶解 0
#    从 8.6 GB 降到 1.5 GB）。声音从同一批中间片的第二次打开里读（见 ``assembly.join_inputs``：音画同一个输入时
#    ffmpeg 6.0 的 xfade 会在接缝硬切）；
# 3. 最后一遍对拼好的单路视频叠画面文字（手写字、台词 / 旁白字幕、角标）并混音（旁白、配音、配乐、响度归一、静音），
#    按交付口径编码——这一遍的视频输入只有一路，帧进来就被消费，内存是平的。
#
# 成片先写到同目录的临时文件，ffmpeg 成功、ffprobe 核过时长之后才 os.replace 到 <id>_post.mp4，清单随后原子落盘：
# 超时 / OOM / 中途出错都不碰上一版好的成片和 post.json（以前 ``-y`` 一开跑就把旧成片截成无 moov 的残片，界面还当它是成片）。

# 中间片的编码（JOIN_GROUP 与 _INTERMEDIATE_ENCODE 与装配版的分组拼接共用，定义在 assembly）：veryfast + crf 16 视觉上
# 近乎无损；音频 24 位 PCM，容器 mov。成片这一遍才按交付口径编（libx264 medium crf 19 + aac 192k），与分段之前完全一样。
_AUDIO_FORMAT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
SUBTITLE_LANGUAGES = ("zh", "en", "bilingual")


@dataclass(frozen=True)
class _Segment:
    """一段已归一化的中间片：在时间线上的绝对起点与时长（相邻两段的重叠 = 前一段终点 − 本段起点）。"""

    path: Path
    start: float
    duration: float


def _normalize_item_command(item: TimelineItem, insert: Mapping[str, Any] | None, dest: Path, *,
                            ffmpeg: str, width: int, height: int, fps: float) -> list[str]:
    """把时间线上的一项归一化成中间片的 ffmpeg 命令：镜 mp4 直接读；插卡 PNG 按时长循环、配一路静音。
    视频链与分段之前逐字一样（字幕卡的推镜也在这里做），只是不再和别的项挤在同一条命令里。"""

    push = _card_push_filter(insert, item.duration, fps) if item.kind == "ui" else ""
    # 画面定格补到这一项的时长（tpad=clone）再截：item.duration 是**容器**时长，音轨比画面长的片（导入的外部片子、
    # 别家模型的出片）画面会短这一截。不补的话每段中间片的画面比声音短，而接缝的 xfade offset 是按时间线算的——
    # 音尾比交叉溶解还长时 offset 就落在前一段画面 EOF 之后，这一组剩下的画面整段丢掉，层级式拼接每一级再丢一次
    # （12 镜、音尾 +0.3s 实测：61.4s 的时间线只剩 35.0s 有画面，而按容器时长做的时长校验照样过）。画面本来就够长时
    # tpad 补出来的帧全被后面的 trim 截掉，命令的产物与补之前逐帧相同
    video = (f"{push}scale={width}:{height}:force_original_aspect_ratio=decrease,"
             f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:g},format=yuv420p,"
             f"tpad=stop_mode=clone:stop_duration={item.duration:.3f},"
             f"trim=duration={item.duration:.3f},setpts=PTS-STARTPTS")
    # 音轨补静音到这一项的时长（apad）再截：声音比画面短的片（生成的镜多半是声音长，别的来源不一定），拼接时每一段的
    # 长度都跟着时间线走——不补的话 concat 演示器按段里最长的那条流接下一段，短出来的声音缺口在成片里被挤掉，
    # 越往后声音越超前画面（30 段、每段声音短 0.05s，片尾差 2.3s）
    audio = f"{_AUDIO_FORMAT},apad,atrim=duration={item.duration:.3f},asetpts=PTS-STARTPTS"
    cmd: list[str] = [ffmpeg, "-y", "-loglevel", "error"]
    if item.kind == "shot":
        cmd += ["-i", str(item.source)]
        graph = f"[0:v]{video}[v];[0:a]{audio}[a]"
    else:
        # -pattern_type none：image2 默认把路径里的 %d / %03d 当序号模板（找 card0.png…），数据目录路径里带这种字样时
        # 卡图读不出来、合成失败（F6）
        cmd += ["-f", "image2", "-pattern_type", "none", "-loop", "1", "-t", f"{item.duration:.3f}", "-framerate", f"{fps:g}",
                "-i", str(item.source),
                "-f", "lavfi", "-t", f"{item.duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        graph = f"[0:v]{video}[v];[1:a]{audio}[a]"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", *_INTERMEDIATE_ENCODE, "-r", f"{fps:g}", str(dest)]
    return cmd


def _xfade_group_command(group: Sequence[_Segment], dest: Path, *, ffmpeg: str, fps: float) -> tuple[list[str], str]:
    """把一组（≤ JOIN_GROUP 段）中间片按各自的接缝重叠做 xfade / acrossfade 链，出一段新的中间片。返回 (命令, 滤镜图)。

    画面从前一半输入读、声音从后一半读（同一批文件开两遍，见 ``assembly.join_inputs``：音画同一个输入时
    ffmpeg 6.0 的 xfade 从每组第 5 个接缝起就硬切）。"""

    cmd: list[str] = [ffmpeg, "-y", "-loglevel", "error", *join_inputs([seg.path for seg in group], crossfade=True)]
    filters: list[str] = []
    audio_base = len(group)
    prev_v, prev_a = "[0:v]", f"[{audio_base}:a]"
    total = group[-1].start + group[-1].duration - group[0].start
    for k in range(1, len(group)):
        overlap = group[k - 1].start + group[k - 1].duration - group[k].start
        offset = group[k].start - group[0].start
        out_v = "[vout]" if k == len(group) - 1 else f"[vx{k}]"
        out_a = "[aout]" if k == len(group) - 1 else f"[ax{k}]"
        # 链尾截到这一组在时间线上的长度：xfade 的结束时间可能比最后一帧晚，写进中间片时恒定帧率会在末尾补一帧，
        # 分几级就多几帧（音画同一个输入时实测每组多一帧）；截到时间线长度，分几级都不累积
        tail = f",trim=duration={total:.3f}" if k == len(group) - 1 else ""
        filters.append(f"{prev_v}[{k}:v]xfade=transition=fade:duration={overlap:.3f}:offset={offset:.3f}{tail}{out_v}")
        filters.append(f"{prev_a}[{audio_base + k}:a]acrossfade=d={overlap:.3f}:c1=tri:c2=tri{out_a}")
        prev_v, prev_a = out_v, out_a
    graph = ";".join(filters)
    cmd += ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]", *_INTERMEDIATE_ENCODE, "-r", f"{fps:g}", str(dest)]
    return cmd, graph


def _join_segments(segments: Sequence[_Segment], work: Path, *, ffmpeg: str, fps: float, timeout_seconds: float,
                   log: list[str]) -> Path:
    """把归一化后的中间片按时间线接成一路：无交叉溶解走 concat 演示器（流复制），有交叉溶解按 JOIN_GROUP 一组层级式 xfade。"""

    if len(segments) == 1:
        return segments[0].path
    overlaps = [prev.start + prev.duration - cur.start for prev, cur in zip(segments, segments[1:])]
    if all(abs(x) < 1e-6 for x in overlaps):
        listing = work / "concat.txt"
        listing.write_text("".join(_concat_entry(seg.path) for seg in segments), encoding="utf-8")
        joined = work / "joined.mov"
        log.append(f"concat demuxer: {len(segments)} segments")
        _run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)],
             purpose="join post segments", timeout_seconds=timeout_seconds)
        return joined
    level = list(segments)
    depth = 0
    while len(level) > 1:
        depth += 1
        next_level: list[_Segment] = []
        for g, k in enumerate(range(0, len(level), JOIN_GROUP)):
            group = level[k:k + JOIN_GROUP]
            if len(group) == 1:
                next_level.append(group[0])
                continue
            dest = work / f"join_{depth}_{g:03d}.mov"
            cmd, graph = _xfade_group_command(group, dest, ffmpeg=ffmpeg, fps=fps)
            log.append(f"level {depth} group {g + 1}: {len(group)} inputs\n{graph}")
            _run(cmd, purpose=f"join post segments (level {depth}, group {g + 1})", timeout_seconds=timeout_seconds)
            next_level.append(_Segment(dest, group[0].start, group[-1].start + group[-1].duration - group[0].start))
        # 这一级接完的中间片用不上了：马上删，临时盘占用最多两份片长（逐项中间片 + 这一级），不随级数叠
        for seg in level:
            if seg not in next_level:
                seg.path.unlink(missing_ok=True)
        level = next_level
    return level[0].path


def _require_audio_file(row: Mapping[str, Any], ffprobe: str) -> None:
    """配乐文件在接进滤镜图之前先用 ffprobe 确认有音频流：不是音频（拖错文件 / 改了扩展名 / 手改 spec 指向图片）时
    ffmpeg 的报错是「Stream specifier ':a' in filtergraph description <整张滤镜图> matches no streams」，截尾之后只剩一屏滤镜图。"""

    try:
        info = probe_media(row["path"], ffprobe=ffprobe)
    except MediaValidationError as exc:
        raise PostLayerError(f"配乐「{row['label']}」不是可读的音频文件：{row['path']}") from exc
    if not getattr(info, "has_audio", True):
        raise PostLayerError(f"配乐「{row['label']}」不是可读的音频文件：{row['path']}")


def _notebook_number(entry: Mapping[str, Any], index: int, key: str, default: float, *, lo: float, hi: float | None,
                     rule: str) -> float:
    """画面文字的数值字段：留空 = 默认；别的必须是范围内的数（写盘前校验会拦，这里兜手改 spec 的情况）。
    ``rule`` 是整句要求（「要是 0 到 1 之间的数」），直接接在字段名后面。"""

    raw = entry.get(key)
    if raw in (None, ""):
        return default
    value = _seconds(raw)
    if value is None or value < lo or (hi is not None and value > hi):
        raise PostLayerError(f"画面文字 notebook_text[{index}] 的 {key} {rule}，现在是「{raw}」")
    return value


def compose_post(
    spec: Mapping[str, Any],
    *,
    output_root: str | os.PathLike[str] | None = None,
    destination: str | os.PathLike[str] | None = None,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    tts: Callable[..., None] | None = None,
    dub_tts: Callable[[str, Path, Mapping[str, Any]], None] | None = None,
    fonts: Mapping[str, str] | None = None,
    timeout_seconds: float = 900.0,
    project_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """出成片：VO → 台词配音 → 插卡 → 时间线 → 逐项归一化 → 分段拼接 → 文字 + 混音 + 响度 → 原子替换。
    返回清单并写 manifests/post.json。``timeout_seconds`` 是每一条 ffmpeg 命令的上限（分段之后是多条）。

    交付宽高 / 帧率 / 交叉溶解走 ``assembly.delivery_settings``（video_params + delivery 合并，与装配版同一口径）；
    字幕按 ``spec.subtitle`` 的 language / display / 字号折行规则画，与装配版的 ``_subtitle_filters`` 同一个函数。"""

    from .subtitles import effective_style, resolve_font

    root = Path(output_root) if output_root is not None else Path(str(spec["output_root"]))
    # 配乐文件是项目相对路径；没显式给项目根就从 <root>/output/story_video/<id> 往上推
    project_root = Path(project_root) if project_root is not None else (root.parents[2] if len(root.parents) >= 3 else root)
    video_dir = root / "02_video"
    vo_dir = root / "07_vo"
    ui_dir = root / "08_ui"
    font_cfg = dict(DEFAULT_FONTS)
    font_cfg.update({k: str(v) for k, v in ((spec.get("post_graphics") or {}).get("fonts") or {}).items()})
    font_cfg.update({k: str(v) for k, v in (fonts or {}).items()})
    dest = Path(destination) if destination is not None else root / f"{spec['id']}_post.mp4"
    width, height, fps, xfade = delivery_settings(spec)

    vo_rows = render_vo_lines(spec, vo_dir, tts=tts, ffprobe=ffprobe)
    dub_rows = render_dub_lines(spec, vo_dir, tts=dub_tts, ffprobe=ffprobe)
    dub_plan = dubbing_plan(spec)
    items = build_timeline(spec, video_dir=video_dir, ui_dir=ui_dir, ffprobe=ffprobe)   # 插卡 dur 写坏 / after 成环在这里报
    inserts = [x for x in ((spec.get("post_graphics") or {}).get("phone_ui_inserts") or ()) if isinstance(x, Mapping)]
    inserts_by_id = {str(x.get("id")): x for x in inserts}
    ui_inserts_skipped = unplaced_inserts(spec, items)
    # 只画进了时间线的插卡；按交付宽高画，横屏交付时卡也铺满整屏（不再是 9:16 的卡缩在中间、两侧黑边）
    for item in items:
        if item.kind == "ui":
            render_ui_card(inserts_by_id[item.item_id], item.source, font_path=font_cfg["ui"], size=(width, height))
    silence_rows = silence_windows(spec, items)   # 写错了在跑 ffmpeg 之前就报
    timeline_end = items[-1].start + items[-1].duration

    # 旁白：起点不早于片尾的整句混不进去（amix duration=first 会把它截掉），记进 vo_skipped 而不是悄悄没了；
    # 起点在片内、尾巴超出片尾的照混，记进 vo_overflow（对齐配音的 dub_overflow）
    vo_mixed: list[tuple[dict[str, Any], float]] = []
    vo_skipped: list[dict[str, Any]] = []
    vo_overflow: list[str] = []
    for row in vo_rows:
        vo_start = absolute_time(items, row["shot"], row["at"])
        vo_end = vo_start + float(row["duration"])
        if vo_start >= timeline_end:
            vo_skipped.append({"index": row["index"], "shot": row["shot"], "at": row["at"], "zh": row["zh"], "start": round(vo_start, 3),
                               "reason": f"起点 {vo_start:.2f}s 不早于片尾 {timeline_end:.2f}s，没有混进成片"})
            continue
        if vo_end > timeline_end + 0.05:
            vo_overflow.append(f"旁白第 {row['index']} 句「{row['zh']}」到 {vo_end:.1f}s，超过片尾 {timeline_end:.1f}s，尾巴被截掉")
        vo_mixed.append((row, vo_start))
    mixed_rows = [row for row, _ in vo_mixed]
    dub_sched, dub_overflow = _dub_schedule(items, dub_rows)
    tracks = music_tracks(spec, project_root=project_root)
    music_rows = [m for m in tracks if not m["missing"] and m["start"] < timeline_end]
    music_missing = [m["label"] for m in tracks if m["missing"]]
    music_skipped = [{"label": m["label"], "start": m["start"], "reason": f"start = {m['start']:g}s 不早于片尾 {timeline_end:.2f}s，没有铺进成片"}
                     for m in tracks if not m["missing"] and m["start"] >= timeline_end]
    for row in music_rows:
        _require_audio_file(row, ffprobe)
    label_rows, labels_skipped = label_layers(spec, ui_dir / "labels", width=width, height=height,
                                              timeline_end=timeline_end, font_path=font_cfg["ui"])

    # ---- 画面文字：手写字 + 台词字幕 + 旁白字幕（都叠在最后一遍的单路视频上）
    # 文字一律写成 UTF-8 文本文件、drawtext 用 textfile= 读，并关掉 drawtext 的模板展开（expansion=none）：用户写的字
    # 是数据不是模板。以前画面文字走 text= 加一层转义，% 报 Stray % 后整条不画（ffmpeg 仍退出 0）、' 把引号提前闭合，
    # 后半句被当成滤镜图语法，「it's」「don't」这种画面文字整个合成失败（F2 / F3）
    text_dir = root / "manifests" / "subtitles"
    shutil.rmtree(text_dir, ignore_errors=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    draw_chain: list[str] = []
    notebook_font = _filter_path(Path(font_cfg["notebook"]))
    for i, entry in enumerate((spec.get("post_graphics") or {}).get("notebook_text") or ()):
        if not isinstance(entry, Mapping) or not str(entry.get("text") or "").strip():
            continue
        shot_id = str(entry.get("shot"))
        item = next((x for x in items if x.item_id == shot_id), None)
        if item is None:
            # 以前静默跳过：成片里没有这行字、清单也不提。与旁白挂错镜同一句报错（写盘前的校验会拦，这里兜手改 spec）
            raise PostLayerError(f"旁白 / 画面文字挂在的「{shot_id}」不在时间线上（镜号不存在，或插卡没接到任何镜上）")
        at = _notebook_number(entry, i, "at", 0.4, lo=0.0, hi=None, rule="要是不小于 0 的秒数")
        if at >= item.duration:
            # 起点就在镜外：显示区间是空的，这行字悄悄不出现（同静音区间的规则：末端超出截到镜尾，起点在镜外报错）
            raise PostLayerError(f"画面文字 notebook_text[{i}] 从镜 {shot_id} 的 {at:g}s 开始，但这一镜成片只有 "
                                 f"{item.duration:.2f}s——改小 at 或删掉这条")
        until = _notebook_number(entry, i, "until", item.duration, lo=0.0, hi=None, rule="要是不小于 0 的秒数")
        if until <= at:
            raise PostLayerError(f"画面文字 notebook_text[{i}] 的 until 要大于 at，现在 at = {at:g}，until = {until:g}")
        start = item.start + at
        end = item.start + min(until, item.duration) - 0.05      # 超出本镜的截到镜尾：不能压到后面每一镜上
        y_frac = _notebook_number(entry, i, "y", 0.56, lo=0.0, hi=1.0, rule="要是 0 到 1 之间的数")
        text_all = str(entry["text"]).replace("\n", "")
        # 两个大字 vs 一行/多行小字；字号取整后不能是 0（drawtext 画不出来）
        size = int(_notebook_number(entry, i, "size", 88 if len(text_all) <= 2 else 46, lo=1.0, hi=None, rule="要是不小于 1 的数"))
        for k, text_line in enumerate(str(entry["text"]).split("\n")):
            y = int(height * y_frac) + k * int(size * 1.25)
            text_path = _filter_path(_text_file(text_dir, f"notebook.{i}.{k}.txt", text_line))
            draw_chain.append(
                f"drawtext=fontfile='{notebook_font}':textfile='{text_path}':expansion=none:fontsize={size}:"
                f"fontcolor=0x1a1a1a@0.92:x=(w-text_w)/2:y={y}:enable='between(t,{start:.3f},{end:.3f})'"
            )

    # 台词字幕：样式、折行（38 单位一行、最多两行）、双语两层、display=full_shot、逐句显式窗口——全部走装配版的
    # _subtitle_filters，两版成片的字幕一致；以前后期版只画 zh、整句一行，长句两头出画，双语 / 英文项目没有英文
    sub_cfg = effective_style(spec.get("subtitle") or {})
    language = str(sub_cfg.get("language") or "zh")
    if language not in SUBTITLE_LANGUAGES:
        language = "zh"
    display = str(sub_cfg.get("display") or "speech")
    chosen_font = resolve_font(sub_cfg, fallback=font_cfg["subtitle"])

    def style_number(key: str, default: float) -> float:
        """样式里的数值：写了 0 也算（``or`` 会把 0 当成没写）；写坏的回落默认值（接口层另有校验，这里只保证不崩）。"""

        value = sub_cfg.get(key)
        if isinstance(value, bool) or value in (None, ""):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def subtitle_filters(tag: str, lines: Sequence[Mapping[str, Any]], windows: Sequence[tuple[float, float]], lang: str) -> list[str]:
        return _subtitle_filters(
            shot_id=tag, lines=lines, windows=windows, text_directory=text_dir, font_path=Path(str(chosen_font)),
            width=width, height=height, band_alpha=style_number("band_alpha", 0.0), language=lang,
            position=str(sub_cfg.get("position") or "bottom"),
            english_font_size=style_number("english_font_size", 64), chinese_font_size=style_number("chinese_font_size", 46),
            english_color=str(sub_cfg.get("english_color") or "0xFFD84D"), chinese_color=str(sub_cfg.get("chinese_color") or "0xFFFFFF"),
            top_margin_ratio=style_number("top_margin_ratio", 0.12), outline_color=str(sub_cfg.get("outline_color") or "0x111111"),
            outline_width=style_number("outline_width", 4), safe_bottom_percent=style_number("safe_bottom_percent", 7),
        )

    def has_text(line: Mapping[str, Any]) -> bool:
        needed = ("zh", "en") if language == "bilingual" else (language,)
        return all(str(line.get(key) or "").strip() for key in needed)

    def explicit_window(line: Mapping[str, Any]) -> tuple[float, float] | None:
        s, e = _seconds(line.get("subtitle_start_seconds")), _seconds(line.get("subtitle_end_seconds"))
        return (s, e) if s is not None and e is not None and 0 <= s < e else None

    # 旁白字幕开着时，同一镜里的台词字幕要给旁白让路：台词字幕按均分槽会一直亮到镜尾，旁白一开口两条就叠在
    # 同一个 y 上。台词字幕的结束点截到这一镜第一句旁白开口前 SUBTITLE_YIELD_GAP 秒；截完不足 0.3 秒的不画
    # （这一镜的旁白排在台词之后才会出现这种情况，台词本身由视频原声念出，只是少一条字幕）
    vo_subtitles = (spec.get("voice_over") or {}).get("subtitles") is True
    vo_spans_by_shot: dict[str, list[tuple[float, float]]] = {}
    if vo_subtitles:
        for row, vo_start in vo_mixed:
            vo_spans_by_shot.setdefault(str(row["shot"]), []).append((vo_start, vo_start + float(row["duration"])))
    subtitle_cuts: list[dict[str, Any]] = []
    shots_by_id = {str(s["id"]): s for s in spec.get("shots", ())}
    for item in items:
        if item.kind != "shot":
            continue
        lines = [ln for ln in (shots_by_id[item.item_id].get("lines") or ()) if isinstance(ln, Mapping) and has_text(ln)]
        if not lines:
            continue
        usable = max(0.5, item.duration - 0.5)
        slot = usable / len(lines)
        drawn_lines: list[Mapping[str, Any]] = []
        drawn_windows: list[tuple[float, float]] = []
        for k, ln in enumerate(lines):
            window = explicit_window(ln)
            if display == "full_shot":
                s, e = item.start, item.start + item.duration
            elif window is not None:
                s, e = item.start + window[0], item.start + min(window[1], item.duration)
            else:
                s = item.start + 0.3 + k * slot
                e = s + slot - 0.05
            s0, e0 = s, e
            for a, b in sorted(vo_spans_by_shot.get(item.item_id, ())):
                if s < a - SUBTITLE_YIELD_GAP < e:
                    e = a - SUBTITLE_YIELD_GAP          # 旁白在这条台词字幕亮着时开口：台词字幕提前收
                elif a - SUBTITLE_YIELD_GAP <= s < b + SUBTITLE_YIELD_GAP:
                    s = b + SUBTITLE_YIELD_GAP          # 台词字幕本该在旁白字幕亮着时出现：等旁白字幕收了再出
            if (s, e) != (s0, e0):
                subtitle_cuts.append({"shot": item.item_id, "line": k, "zh": str(ln.get("zh") or ln.get("en") or ""),
                                      "was": [round(s0, 3), round(e0, 3)], "now": [round(s, 3), round(e, 3)], "drawn": e - s >= 0.3})
                if e - s < 0.3:
                    continue
            drawn_lines.append(ln)
            drawn_windows.append((s, e))
        draw_chain.extend(subtitle_filters(item.item_id, drawn_lines, drawn_windows, language))
    # 旁白字幕（voice_over.subtitles=true 才出）：每句从它的绝对起点开始、按真实 TTS 时长显示——
    # 不像台词那样均分槽位，因为旁白的起点是人按秒写的，时长是合成出来量的，都是准的；旁白只有中文，按 zh 层画
    if vo_subtitles:
        for row, vo_start in vo_mixed:
            draw_chain.extend(subtitle_filters(f"vo{row['index']}", [{"zh": row["zh"]}], [(vo_start, vo_start + float(row["duration"]))], "zh"))

    manifests = root / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    join_log: list[str] = []
    final_tmp = _temporary_destination(dest)
    try:
        # 中间片清不掉（Windows 上偶尔有杀毒 / 预览进程占着）不该让已经合成好的成片算失败
        with tempfile.TemporaryDirectory(prefix=".post-", dir=root, ignore_cleanup_errors=True) as work_name:
            work = Path(work_name)
            # ---- 0. 旁白 + 配音的句子多于 MIX_GROUP 句时先分组预混（与播客、绘本朗读同一套）：最后一遍的输入数与命令行长度
            # 不随句数涨。放在画面之前，出问题几秒内就报。不超过时 premixed 是 None，最后一遍每句一路输入，与分组之前逐字一样
            premixed = premix_audio([(Path(row["path"]), start) for row, start in vo_mixed]
                                    + [(Path(row["path"]), float(row["abs_start"])) for row in dub_sched],
                                    work, ffmpeg=ffmpeg, sample_rate=48000, run=_run, timeout_seconds=timeout_seconds)
            # ---- 1. 逐项归一化（每条命令只解码一路）
            segments: list[_Segment] = []
            for i, item in enumerate(items):
                seg = work / f"seg_{i:03d}.mov"
                _run(_normalize_item_command(item, inserts_by_id.get(item.item_id) if item.kind == "ui" else None, seg,
                                             ffmpeg=ffmpeg, width=width, height=height, fps=fps),
                     purpose=f"normalise {item.kind} {item.item_id}", timeout_seconds=timeout_seconds)
                segments.append(_Segment(seg, item.start, item.duration))
            # ---- 2. 拼接成一路
            joined = _join_segments(segments, work, ffmpeg=ffmpeg, fps=fps, timeout_seconds=timeout_seconds, log=join_log)
            (manifests / "post_join.txt").write_text("\n\n".join(join_log) or "single segment", encoding="utf-8")

            # ---- 3. 文字 + 混音 + 响度，按交付口径编码
            cmd: list[str] = [ffmpeg, "-y", "-loglevel", "error", "-i", str(joined)]
            filters: list[str] = []
            input_count = 1
            vo_inputs: list[int] = []
            for row, _start in (vo_mixed if premixed is None else ()):
                cmd += ["-i", row["path"]]
                vo_inputs.append(input_count)
                input_count += 1
            dub_inputs: list[int] = []
            for row in (dub_sched if premixed is None else ()):
                cmd += ["-i", row["path"]]
                dub_inputs.append(input_count)
                input_count += 1
            premix_inputs: list[int] = []
            for clip in premixed or ():
                cmd += ["-i", str(clip.path)]
                premix_inputs.append(input_count)
                input_count += 1
            music_inputs: list[int] = []
            for row in music_rows:
                cmd += ["-i", row["path"]]
                music_inputs.append(input_count)
                input_count += 1
            # 文字角标：每条一张透明 PNG，单帧输入（overlay 默认 eof_action=repeat，整片一直有这一帧可叠）
            label_inputs: list[int] = []
            for row in label_rows:
                cmd += ["-f", "image2", "-pattern_type", "none", "-i", row["path"]]   # 同插卡：路径里的 %d 不当序号模板
                label_inputs.append(input_count)
                input_count += 1

            # 有角标时文字链先出到 [vtxt]，角标逐条叠在最上层再出 [vout]
            text_out = "[vtxt]" if label_rows else "[vout]"
            filters.append("[0:v]" + ",".join(draw_chain) + text_out if draw_chain else f"[0:v]null{text_out}")
            prev_v = text_out
            for k, (row, idx) in enumerate(zip(label_rows, label_inputs)):
                out_v = "[vout]" if k == len(label_rows) - 1 else f"[vlb{k}]"
                filters.append(f"{prev_v}[{idx}:v]overlay=x={row['x']}:y={row['y']}:"
                               f"enable='between(t,{row['start']:.3f},{row['end']:.3f})'{out_v}")
                prev_v = out_v

            # 混音：旁白那几秒压原声到 0.55（带斜坡，见 vo_duck_volume）；配音镜的时间段内原声再压到 duck（默认 0.15，
            # 让模型自己念的台词退到底噪）；VO / 配音都按绝对时间 adelay，amix 不归一；最后响度归一、再按 silence 置零
            vo_labels: list[str] = []
            # 旁白压原声按每一句的时段算，预混了也一样
            vo_spans = [(vo_start, vo_start + float(row["duration"])) for row, vo_start in vo_mixed]
            for (row, vo_start), idx in zip(vo_mixed, vo_inputs):
                t_ms = int(round(vo_start * 1000))
                filters.append(f"[{idx}:a]{_AUDIO_FORMAT},adelay={t_ms}|{t_ms},volume=1.0[vo{idx}]")
                vo_labels.append(f"[vo{idx}]")
            dub_labels: list[str] = []
            for row, idx in zip(dub_sched, dub_inputs):
                t_ms = int(round(row["abs_start"] * 1000))
                filters.append(f"[{idx}:a]{_AUDIO_FORMAT},adelay={t_ms}|{t_ms},volume=1.0[dub{idx}]")
                dub_labels.append(f"[dub{idx}]")
            premix_labels: list[str] = []
            for clip, idx in zip(premixed or (), premix_inputs):
                filters.append(f"[{idx}:a]{_AUDIO_FORMAT},{adelay_samples(clip.start_sample)}[sp{idx}]")
                premix_labels.append(f"[sp{idx}]")
            music_labels: list[str] = []
            if music_rows:
                speech = _speech_spans(items, spec, mixed_rows, dub_sched)
                for row, idx in zip(music_rows, music_inputs):
                    end = min(row["end"] if row["end"] is not None else timeline_end, timeline_end)
                    length = max(0.1, end - row["start"])
                    chain = [f"[{idx}:a]{_AUDIO_FORMAT}", f"atrim=0:{length:.3f}", "asetpts=PTS-STARTPTS"]
                    if row["fade_in"] > 0:
                        chain.append(f"afade=t=in:st=0:d={min(row['fade_in'], length):.3f}")
                    if row["fade_out"] > 0 and length > row["fade_out"]:
                        chain.append(f"afade=t=out:st={length - row['fade_out']:.3f}:d={row['fade_out']:.3f}")
                    chain.append(f"volume={row['gain']:.3f}")
                    if speech and row["duck"] < 1.0:
                        # 压低按绝对时间算，所以放在 adelay 之后
                        chain.append(f"adelay={int(round(row['start'] * 1000))}|{int(round(row['start'] * 1000))}")
                        spans = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in speech)
                        chain.append(f"volume='if({spans},{row['duck']:.3f},1)':eval=frame")
                    else:
                        chain.append(f"adelay={int(round(row['start'] * 1000))}|{int(round(row['start'] * 1000))}")
                    filters.append(",".join(chain) + f"[mus{idx}]")
                    music_labels.append(f"[mus{idx}]")
            native = "[0:a]"
            if dub_sched:
                dubbed = set(dub_plan["shots"])
                spans = "+".join(
                    f"between(t,{item.start:.3f},{item.start + item.duration:.3f})"
                    for item in items if item.kind == "shot" and item.item_id in dubbed
                )
                filters.append(f"[0:a]volume='if({spans},{dub_plan['duck']:.3f},1)':eval=frame[axd]")
                native = "[axd]"
            overlays = vo_labels + dub_labels + premix_labels + music_labels
            master = "loudnorm=I=-16:TP=-1.5:LRA=9" + silence_filter(silence_rows)
            if overlays:
                if vo_spans:
                    filters.append(f"{native}volume='{vo_duck_volume(vo_spans)}':eval=frame[axq]")
                    native = "[axq]"
                # duration=first：以原声（时间线）为准，配乐再长也不把成片拖长
                filters.append(f"{native}{''.join(overlays)}amix=inputs={len(overlays) + 1}:duration=first:normalize=0:dropout_transition=0,"
                               f"{master}[aout]")
            else:
                filters.append(f"{native}{master}[aout]")

            graph_path = manifests / "post_filtergraph.txt"
            graph_path.write_text(";\n".join(filters), encoding="utf-8")
            cmd += ["-filter_complex_script", str(graph_path), "-map", "[vout]", "-map", "[aout]",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", f"{fps:g}",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(final_tmp)]
            _run(cmd, purpose="compose post layers", timeout_seconds=timeout_seconds)
        # ---- 核对之后才替换：上一版好的成片在这之前一直原样在
        try:
            info = probe_media(final_tmp, ffprobe=ffprobe)
        except MediaValidationError as exc:
            raise PostLayerError(f"后期合成写出的成片读不出来：{exc}") from exc
        expected = timeline_end
        if abs(float(info.duration_seconds) - expected) > 1.0:
            raise PostLayerError(f"后期合成的成片时长 {float(info.duration_seconds):.2f}s 与时间线 {expected:.2f}s 不符")
        _sync_file(final_tmp)
        replace_final(final_tmp, dest, error=PostLayerError)
    finally:
        final_tmp.unlink(missing_ok=True)

    manifest = {
        "schema_version": 1,
        "output": str(dest),
        "duration_seconds": float(info.duration_seconds),
        "expected_duration_seconds": round(expected, 3),
        "size_bytes": int(getattr(info, "size_bytes", 0) or 0),   # 成片的字节数：界面据此分辨成片是不是清单描述的那一份
        "delivery": {"width": width, "height": height, "fps": fps, "crossfade_seconds": xfade},
        "timeline": [{"kind": x.kind, "id": x.item_id, "start": round(x.start, 3), "duration": round(x.duration, 3)} for x in items],
        "vo": vo_rows,
        "vo_skipped": vo_skipped,           # 起点不早于片尾、没混进成片的旁白句
        "vo_overflow": vo_overflow,         # 尾巴超出片尾被截掉的旁白句（对齐 dub_overflow）
        "dub": dub_sched,
        "dub_shots": dub_plan["shots"],
        "dub_overflow": dub_overflow,
        "music": [{k: row[k] for k in ("label", "path", "start", "end", "gain", "duck")} for row in music_rows],
        "music_missing": music_missing,
        "music_skipped": music_skipped,     # start 不早于片尾、没铺进成片的配乐
        "ui_inserts": [str(x.source) for x in items if x.kind == "ui"],   # 按时间线记：清单里有的插卡一定在成片里
        "ui_inserts_skipped": ui_inserts_skipped,
        "labels": [{k: row[k] for k in ("text", "position", "start", "end", "x", "y", "size", "shrunk", "path")} for row in label_rows],
        "labels_skipped": labels_skipped,
        "fonts": font_cfg,
        "silence": silence_rows,   # 换算后的绝对起止秒：对剧本时间码、复核静音落点都看这里
        "subtitle_cuts": subtitle_cuts,   # 给旁白字幕让路而截短 / 不画的台词字幕（没开旁白字幕时是空表）
        "subtitle": {"language": language, "display": display},
        "join": {"segments": len(items), "group_size": JOIN_GROUP, "commands": len(join_log)},
    }
    atomic_write_json(manifests / "post.json", manifest)
    return manifest


__all__ = ["render_vo_lines", "render_ui_card", "build_timeline", "absolute_time", "compose_post", "TimelineItem", "PostLayerError",
           "default_fonts", "font_status", "DEFAULT_FONTS", "dubbing_plan", "render_dub_lines", "DUB_MODES", "DEFAULT_DUCK",
           "music_tracks", "DEFAULT_MUSIC", "VO_EDGE_KEYS", "validate_voice_over_extras", "VO_DUCK", "vo_duck_volume",
           "SILENCE_MAX_SECONDS", "validate_silence", "silence_overrun_warnings", "silence_windows", "silence_filter",
           "START_ANCHOR", "CARD_MOTIONS", "render_text_card", "card_lines",
           "LABEL_POSITIONS", "DEFAULT_LABEL", "LABEL_MAX_CHARS", "label_text", "fit_label_size", "render_label", "label_xy",
           "label_layers", "INSERT_MAX_SECONDS", "INSERT_DEFAULT_SECONDS", "INSERT_LAYOUTS", "JOIN_GROUP", "VO_ENGINES",
           "insert_duration", "unplaced_inserts", "seam_overlaps", "SUBTITLE_LANGUAGES"]
