"""交付包：把一个项目「拿得出手」的东西打成一个自包含的文件夹 + zip。

融合自 zenstory drama-skills 的 ``project_tool.py export``：创作文档 + 生产产物 + 一份
manifest.json + 一份 checksums.sha256，输入与凭据一律不进包。这里的取舍：

* **只拷白名单**：spec 快照、编译后的提示词、首帧、视频、成片、审片裁决、旁白/配音 mp3。
  每个文件都按 ``<output_root>`` 内的固定位置去找，不扫目录、不跟符号链接——
  ``~/.storystudio``（设置、密钥、音色库）、``content/assets``（锚图输入）、账本（sqlite）、
  ``manifests/`` 里带本机绝对路径的文件都不会被碰到。
* **manifest 是给收件人看的**：id、标题、导出时间、每一镜有没有首帧/视频、交付物是后期版
  还是装配版、文件清单与大小、程序版本；checksums 让对方能核对文件没坏。
* **不动原文件**：只读 + 复制；导出目录固定在 ``<output_root>/09_export/``。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from genkit.version import __version__
from storyvideo.post_layers import DEFAULT_EDGE, dubbing_plan
from storyvideo.poster import latest as latest_poster_entry
from storyvideo.poster import poster_dir
from storyvideo.prompts import compile_frame_prompt, compile_negative_prompt, compile_video_prompt
from storyvideo.qc import QCError, load_qc
from storyvideo.runtime import ledger_path

from . import deps as _deps
from .deps import _deliverable_path

EXPORT_SUBDIR = "09_export"
VOICE_SUBDIR = "07_vo"


def export_root(spec: Mapping[str, Any]) -> Path:
    return Path(str(spec["output_root"])) / EXPORT_SUBDIR


def package_name(spec: Mapping[str, Any], now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"{spec.get('id')}-{stamp}"


def _inside(path: Path, boundary: Path) -> bool:
    """``path`` 真实落在 ``boundary`` 之内（解析符号链接后）——每个拷贝动作前都过一遍。"""

    try:
        return path.resolve().is_relative_to(boundary.resolve())
    except OSError:
        return False


def _copy(src: Path, dst: Path, boundary: Path) -> bool:
    if not src.is_file() or src.is_symlink() or not _inside(src, boundary):
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prompts_markdown(spec: Mapping[str, Any]) -> str:
    """每一镜：镜号、时长、出场、首帧提示词、负向词、视频提示词，各用围栏块——收件人复制即用。"""

    lines = [f"# {spec.get('title_zh') or spec.get('title_en') or spec.get('id')}", ""]
    lines.append(f"- id: `{spec.get('id')}`")
    if spec.get("logline"):
        lines.append(f"- logline: {spec.get('logline')}")
    if spec.get("camera_profile"):
        lines.append(f"- camera_profile: {spec.get('camera_profile')}")
    lines.append("")
    for shot in spec.get("shots", ()):
        if not isinstance(shot, Mapping):
            continue
        sid = str(shot.get("id"))
        cast = ", ".join(str(c) for c in (shot.get("cast") or ())) or "-"
        lines.append(f"## {sid}")
        lines.append("")
        lines.append(f"- dur: {shot.get('dur')}")
        lines.append(f"- cast: {cast}")
        if shot.get("ref_frame"):
            lines.append(f"- ref_frame: {shot.get('ref_frame')}")
        lines.append("")
        for label, compiler in (
            ("首帧提示词 / first-frame prompt", compile_frame_prompt),
            ("负向词 / negative prompt", compile_negative_prompt),
            ("视频提示词 / video prompt", compile_video_prompt),
        ):
            try:
                text = compiler(spec, sid) or ""
            except Exception as exc:  # noqa: BLE001 - 一镜编译失败不该让整包失败，写明原因即可
                text = f"(compile failed: {type(exc).__name__}: {exc})"
            lines.append(f"### {label}")
            lines.append("")
            lines.append("```text")
            lines.append(text.replace("```", "'''"))
            lines.append("```")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _qc_decisions(output_root: Path) -> dict[str, Any] | None:
    """审片裁决的精简版：只带决定/审片人/原因/时间/产物哈希，不带本机绝对路径。"""

    path = output_root / "manifests" / "qc.json"
    if not path.is_file():
        return None
    try:
        qc = load_qc(path)
    except QCError:
        return None
    gates: dict[str, Any] = {}
    for gate, data in (qc.get("gates") or {}).items():
        items = {}
        for item_id, item in ((data or {}).get("items") or {}).items():
            if not isinstance(item, Mapping):
                continue
            items[str(item_id)] = {
                "decision": item.get("decision"),
                "reviewer": item.get("reviewer"),
                "reason": item.get("reason") or "",
                "reviewed_at": item.get("reviewed_at"),
                "artifact_sha256": item.get("artifact_sha256"),
                "technical_passed": item.get("technical_passed"),
            }
        gates[str(gate)] = {"status": (data or {}).get("status"), "items": items}
    return {"schema_version": 1, "updated_at": qc.get("updated_at"), "gates": gates}


AI_STATEMENT = "本片由 AI 辅助生成：首帧与视频由 AI 模型生成，旁白 / 配音为合成音；发布前按平台规定在显眼处标明 AI 生成，备案按投资额度走对应的类别。"
AI_STATEMENT_EN = "This episode was made with AI assistance: first frames and videos are AI-generated and the narration is synthetic speech; label it as required by the platform before release."
# 工具清单的口径（basis）只有两种：账本里有这一集的提交记录就按账本报首帧 / 视频（剧本起草与没提交过的类别仍按设置，
# 所以这句写成混合口径，以各行标注为准）；没有（没提交过、老账本、账本读不到）才整段按导出时的设置汇总
DISCLOSURE_BASIS_LEDGER = ("首帧 / 视频按账本逐 job 记录（每一镜提交时记下的来源与模型，不受导出时的设置影响；重提过的镜只记最近一次）；"
                           "剧本 / 分镜起草、以及账本里没有记下来源的类别（没提交过，或提交时账本还是老版本），按导出时的设置汇总——以各行标注为准。")
DISCLOSURE_BASIS_SETTINGS = "工具清单按导出时的设置汇总（账本里没有记录）：来源与模型来自导出时的模型设置与项目文件里的模型别名；生成之后改过设置的，以实际生成时的来源为准。"
# 声音与配乐那几行的口径：不走账本，按导出时的项目文件
DISCLOSURE_BASIS_AUDIO = "旁白、台词配音与配乐按导出时的项目文件记声线与曲目；后期层改过之后没重新做后期合成的，以成片为准。"
_DISCLOSURE_ROLES = (("script", "text", "剧本 / 分镜起草"), ("frames", "image", "首帧"), ("videos", "video", "视频"))
# 溯源行 ↔ 账本里 job 的 kind（剧本起草不走账本）
_LEDGER_KINDS = {"frames": "image", "videos": "video"}


def _role_tool(role_name: str, spec_model: str = "") -> dict[str, Any]:
    """一个角色当前的来源与模型：项目文件写了具体模型名就用它，写的是 ``@image`` 这类别名就按设置解析。"""

    try:
        from genkit import settings as settings_mod

        role = settings_mod.effective(settings_mod.load_settings().role(role_name), role_name)
        adapter, model = role.adapter or "", role.model or ""
    except Exception:  # noqa: BLE001 - 读不到设置不该让导出失败
        adapter, model = "", ""
    literal = str(spec_model or "").strip()
    if literal and not literal.startswith("@"):
        model = literal
    return {"adapter": adapter or "unknown", "model": model or "unknown", "configured": bool(adapter)}


def _ledger_sources(spec: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]] | None:
    """从账本读这一集每类 job 真实提交给了谁（``RunStore.sources_for``）。

    账本按 runtime 的唯一规则找（``ledger_path(project_root)``），项目根用 spec 自带的 ``project_root``（runner 正是按它开账本；
    CLI / 测试传别的项目根时才能读对本），没有才退到服务的 ``PROJECT_ROOT``。只读打开；账本不存在、
    版本对不上（老账本还没被可写打开过一次）、没有这个 run、任何异常都返回 ``None``——读账本失败绝不能让导出失败。
    """

    try:
        from genkit.store import RunStore

        run_id = str(spec.get("id") or "").strip()
        if not run_id:
            return None
        db_path = ledger_path(spec.get("project_root") or _deps.PROJECT_ROOT)
        if not db_path.is_file():
            return None
        with RunStore(db_path, readonly=True) as store:
            return store.sources_for(run_id)
    except Exception:  # noqa: BLE001 - 溯源是附带信息，读不到就退回按设置汇总
        return None


def ai_disclosure(spec: Mapping[str, Any]) -> dict[str, Any]:
    """生成内容标注 + 用了哪些工具（微短剧上线的现实要求：AI 生成的内容要标明、用了哪些工具要说得清，docs/script-craft-gap.md）。

    工具清单优先按账本：每个 job 提交时记下的适配器 id 与解析后的模型名，按 (来源, 模型) 合并计数放进
    ``tools.frames / tools.videos`` 的 ``sources``；账本里没有这一集的提交记录（或读不到）才退回按导出时的设置
    与项目文件汇总。哪种口径写在 ``basis`` 里，每一行也带 ``from``（``ledger`` / ``settings``）。说不清的字段写 ``unknown``。
    声音与配乐（``tools.narration / dubbing / music``）不走账本、按项目文件记：旁白整块与逐句的声线、每个角色的
    配音声线（``cast.<id>.voice_tts``）、每条配乐的 label 与文件。后期层在制作中也能改，所以记的是导出时项目文件里的样子；
    改过之后没重新做后期合成的，以成片为准。旁白走「模型设置」里的语音时，来源与模型按导出时的设置记（那一行带 ``from: settings``）。
    """

    ledger = _ledger_sources(spec)
    from_ledger = False
    tools: dict[str, Any] = {}
    for key, role_name, label in _DISCLOSURE_ROLES:
        params = spec.get("image_params") if key == "frames" else spec.get("video_params") if key == "videos" else None
        spec_model = str((params or {}).get("model") or "") if isinstance(params, Mapping) else ""
        row: dict[str, Any] = {"label": label, **_role_tool(role_name, spec_model), "from": "settings"}
        kind = _LEDGER_KINDS.get(key)
        if kind:
            sources = [dict(s) for s in ((ledger or {}).get(kind) or ()) if isinstance(s, Mapping)]
            row["sources"] = sources
            # 老账本的老行两列是空串：有提交但没记来源，算不上「账本里有记录」
            recorded = [s for s in sources if s.get("provider") or s.get("model")]
            if recorded:
                from_ledger = True
                top = recorded[0]   # sources_for 按 count 降序，最多的那家放在 adapter / model 里
                row.update({"adapter": str(top.get("provider") or "unknown"), "model": str(top.get("model") or "unknown"),
                            "from": "ledger"})
                row.pop("configured", None)   # 「设置里配没配」只对按设置的行有意义，按账本的行不带这个字段
        tools[key] = row
    tools["narration"] = _narration_tool(spec)
    tools["dubbing"] = _dubbing_tool(spec)
    tools["music"] = _music_tool(spec)
    return {"statement": AI_STATEMENT, "statement_en": AI_STATEMENT_EN, "tools": tools,
            "basis": DISCLOSURE_BASIS_LEDGER if from_ledger else DISCLOSURE_BASIS_SETTINGS,
            "basis_audio": DISCLOSURE_BASIS_AUDIO}


def _voice_fields(values: Mapping[str, Any]) -> dict[str, str]:
    """声线三项（音色 / 语速 / 音调），没写的是空串。"""

    return {key: str(values.get(key) or "") for key in ("voice", "rate", "pitch")}


# 逐句换声线认的三项（跟 post_layers 的 VO_EDGE_KEYS 同一组）
_EDGE_VOICE_KEYS = ("voice", "rate", "pitch")


def _narration_engine(declared: str) -> tuple[str, dict[str, str]]:
    """整块旁白实际走哪条合成路：``(engine, 设置里的 {adapter, model})``，跟 ``post_layers.render_vo_lines`` 同一条规则。

    写了 engine 就按它；没写时「模型设置」里配了非 edge_tts、校验不缺项的语音角色就走设置，否则走 edge-tts。
    以前没写就一律记成 edge，手写的 spec（没经过音色库绑定、不带 engine）配上网关语音时，溯源写的是一把片子里没用过的声线。
    走设置时像首帧 / 视频那样记导出时设置里的来源与模型；读设置出错说不清，记 ``unknown``。
    """

    if declared and declared != "settings":
        return declared, {}
    try:
        from genkit import settings as settings_mod

        loaded = settings_mod.load_settings()
        role = loaded.role("voice")
        adapter = str(getattr(role, "adapter", "") or "")
        if not declared and (not adapter or adapter == "edge_tts" or settings_mod.validate_role(loaded, "voice")):
            return "edge", {}
        model = str(getattr(role, "model", "") or "")
        try:
            model = str(settings_mod.effective(role, "voice").model or model)   # 没填模型的按适配器默认补
        except Exception:  # noqa: BLE001 - 补默认模型名只是锦上添花
            pass
        return "settings", {"adapter": adapter or "unknown", "model": model or "unknown"}
    except Exception:  # noqa: BLE001 - 读不到设置不该让导出失败
        return ("settings", {"adapter": "unknown", "model": "unknown"}) if declared else ("unknown", {})


def _narration_tool(spec: Mapping[str, Any]) -> dict[str, Any]:
    """旁白声线：整块一套 + 逐句覆盖（``voice_over.lines[i].edge``），都记合成时实际用的值。

    合成用的 edge 声线是 ``post_layers.DEFAULT_EDGE`` 上叠整块 ``voice_over.edge``（post_layers 里的 cfg）：
    整块走 edge 时念的就是它；走设置里的语音时只把其中的音色传过去（语速 / 音调不传，记空串），另记设置里的来源与模型；
    走克隆音色（minimax）时传音色与语速。逐句覆盖在旁白引擎是 edge 或没写时才生效、总是走 edge-tts，
    没写的语速 / 音调沿用整块的（叠在同一个 cfg 上），所以每一句也带 ``engine: edge``。
    ``index`` 是它在 ``voice_over.lines`` 里的下标（跟校验报错的 ``voice_over.lines[i]`` 同一个数）。
    """

    vo = spec.get("voice_over") if isinstance(spec.get("voice_over"), Mapping) else {}
    edge = vo.get("edge") if isinstance(vo.get("edge"), Mapping) else {}
    raw_lines = vo.get("lines") if isinstance(vo.get("lines"), list) else []
    spoken = [(i, line) for i, line in enumerate(raw_lines) if isinstance(line, Mapping) and str(line.get("zh") or "").strip()]
    declared = str(vo.get("engine") or "").strip()
    cfg = {**DEFAULT_EDGE, **{k: str(v) for k, v in edge.items() if v is not None}}
    engine, source = _narration_engine(declared) if spoken else ("none", {})
    if engine == "edge":
        block = _voice_fields(cfg)
    elif engine == "settings":
        block = {"voice": cfg["voice"], "rate": "", "pitch": ""}
    elif engine == "minimax":
        block = {"voice": cfg["voice"], "rate": cfg["rate"], "pitch": ""}
    else:
        block = _voice_fields(edge)   # 没有旁白 / 说不清 / 不认识的引擎：按项目文件原样
    overrides: list[dict[str, Any]] = []
    if declared in ("", "edge"):
        for index, line in spoken:
            own = line.get("edge") if isinstance(line.get("edge"), Mapping) else {}
            own = {k: str(own[k]).strip() for k in _EDGE_VOICE_KEYS if own.get(k) not in (None, "") and str(own[k]).strip()}
            if own:
                overrides.append({"index": index, "shot": str(line.get("shot") or ""), "engine": "edge",
                                  **_voice_fields({**cfg, **own})})
    row: dict[str, Any] = {"label": "旁白", "engine": engine, **block, "used": bool(spoken), "line_voices": overrides}
    if engine == "settings":
        row.update(source, **{"from": "settings"})
    return row


def _dubbing_tool(spec: Mapping[str, Any]) -> dict[str, Any]:
    """台词配音：模式 + 每个角色绑定的配音声线（``cast.<id>.voice_tts``）。

    ``used`` = 这个角色在配音镜里开口了（配音镜按 ``post_layers.dubbing_plan`` 算，跟后期合成同一个口径）；
    绑了声线却没用上的也列出来，标 ``used: false``——收件人看得到绑定，也不会误以为片子里有这把声音。
    """

    graphics = spec.get("post_graphics") if isinstance(spec.get("post_graphics"), Mapping) else {}
    dubbing = graphics.get("dubbing")
    mode = str(dubbing.get("mode") or "off") if isinstance(dubbing, Mapping) else "off"
    speaking: set[str] = set()
    if mode != "off":
        try:
            dubbed = set(dubbing_plan(spec)["shots"])
        except Exception:  # noqa: BLE001 - 配置写坏了（duck 不是数之类）只是认不出谁开口，不该让导出失败
            dubbed = set()
        for shot in spec.get("shots") or ():
            if isinstance(shot, Mapping) and str(shot.get("id")) in dubbed:
                speaking.update(str(line.get("who") or "") for line in (shot.get("lines") or ())
                                if isinstance(line, Mapping) and str(line.get("zh") or "").strip())
    cast = spec.get("cast") if isinstance(spec.get("cast"), Mapping) else {}
    voices: list[dict[str, Any]] = []
    for cast_id, member in cast.items():
        tts = member.get("voice_tts") if isinstance(member, Mapping) else None
        if not (isinstance(tts, Mapping) and str(tts.get("voice") or "").strip()):
            continue
        voices.append({"cast": str(cast_id), "name": str(member.get("name") or ""),
                       "engine": str(tts.get("engine") or "edge"),   # 没写引擎时合成走 edge（genkit.voices.synthesize_with_profile）
                       **_voice_fields(tts), "used": str(cast_id) in speaking})
    return {"label": "台词配音", "mode": mode, "voices": voices}


def _portable_file(raw: str) -> str:
    """配乐文件按项目文件原样记项目内相对路径；万一写成了本机绝对路径就只留文件名——本机路径不进包。"""

    text = str(raw or "").strip()
    if not text:
        return ""
    if PurePosixPath(text).is_absolute() or PureWindowsPath(text).drive or text.startswith(("\\", "~")):
        return PureWindowsPath(text).name   # Windows 路径两种斜杠都认，取文件名对 / 开头的路径也成立
    return text.replace("\\", "/")


def _music_tool(spec: Mapping[str, Any]) -> dict[str, Any]:
    """配乐：``post_graphics.music`` 的每一条记 label 与 file；没写 label 的按合成时的叫法「配乐 N」。"""

    graphics = spec.get("post_graphics") if isinstance(spec.get("post_graphics"), Mapping) else {}
    items = graphics.get("music") if isinstance(graphics.get("music"), list) else []
    tracks = [{"label": str(item.get("label") or f"配乐 {i + 1}"), "file": _portable_file(item.get("file"))}
              for i, item in enumerate(items) if isinstance(item, Mapping)]
    return {"label": "配乐", "used": bool(tracks), "tracks": tracks}


def _voice_text(row: Mapping[str, Any]) -> str:
    """「引擎 / 音色（语速 x，音调 y）」，走设置里的语音时引擎后面多「来源 / 模型」；没写的部分不出现。"""

    head = " / ".join(str(row[key]) for key in ("engine", "adapter", "model", "voice") if row.get(key)) or "unknown"
    extras = "，".join(f"{name} {row[key]}" for key, name in (("rate", "语速"), ("pitch", "音调")) if row.get(key))
    return head + (f"（{extras}）" if extras else "")


def _disclosure_tool_line(row: Mapping[str, Any]) -> str:
    """溯源一行：按账本的列出每个 (来源 / 模型 ×job 数)，按设置的只有一对；括号里标口径。"""

    label = row.get("label") or "unknown"
    if row.get("from") == "ledger":
        parts = [f"{s.get('provider') or 'unknown'} / {s.get('model') or 'unknown'} ×{int(s.get('count') or 0)}"
                 for s in (row.get("sources") or ()) if isinstance(s, Mapping)]
        return f"- {label}：{', '.join(parts)}（按账本）"
    return f"- {label}：{row.get('adapter', 'unknown')} / {row.get('model', 'unknown')}（按导出时的设置）"


def _disclosure_markdown(disclosure: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    tools = disclosure.get("tools") or {}
    lines = [f"# AI 生成说明与工具清单 · {spec.get('title_zh') or spec.get('title_en') or spec.get('id')}", "",
             f"【AI 生成说明】{disclosure['statement']}", "", "【用到的生成工具】", ""]
    for key in ("script", "frames", "videos"):
        lines.append(_disclosure_tool_line(tools.get(key) or {"label": key}))
    narration = tools.get("narration") or {}
    if narration.get("used"):
        basis = "（按导出时的设置）" if narration.get("from") == "settings" else ""   # 走设置里的语音：口径同首帧 / 视频
        lines.append(f"- 旁白：{_voice_text(narration)}{basis}")
        for row in narration.get("line_voices") or ():
            where = f"第 {int(row.get('index') or 0) + 1} 句" + (f"（{row['shot']}）" if row.get("shot") else "")
            lines.append(f"  - {where}换声线：{_voice_text(row)}")
    else:
        lines.append("- 旁白：本集没有旁白")
    dubbing = tools.get("dubbing") or {}
    lines.append(f"- 台词配音：{dubbing.get('mode', 'off')}")
    if dubbing.get("mode", "off") != "off":
        # 配音关着时角色绑的声线不会出现在片子里，不列，免得读的人以为片子里有这把声音（manifest 里仍有）。
        # 角色名、配乐 label 是用户写的，用「」括起来（英文规则靠它认出哪段原样留下）
        for row in dubbing.get("voices") or ():
            note = "" if row.get("used") else "本集没用到"
            cast_id = str(row.get("cast") or "")
            if row.get("name"):
                who = f"「{row['name']}」（{cast_id}，{note}）" if note else f"「{row['name']}」（{cast_id}）"
            else:
                who = f"{cast_id}（{note}）" if note else cast_id
            lines.append(f"  - {who}：{_voice_text(row)}")
    music = tools.get("music") or {}
    if music.get("used"):
        lines.append(f"- 配乐：{len(music.get('tracks') or ())} 条")
        for row in music.get("tracks") or ():
            lines.append(f"  - 「{row.get('label') or 'unknown'}」：{row.get('file') or 'unknown'}")
    else:
        lines.append("- 配乐：本集没有配乐")
    lines += ["", "【备案】上线前办完备案，备案号挂在开头；走哪一档由投资额度决定。", "", f"> {disclosure['basis']}"]
    if disclosure.get("basis_audio"):
        lines += [">", f"> {disclosure['basis_audio']}"]
    lines.append("")
    return "\n".join(lines)


def build_package(spec: Mapping[str, Any], raw_spec: Mapping[str, Any], dest_dir: Path) -> dict[str, Any]:
    """在 ``dest_dir``（通常是 ``<output_root>/09_export/<id>-<时间>``）里建交付包并打成 zip。

    返回 ``{"folder", "zip", "name", "files", "bytes", "deliverable", "shots"}``。
    ``spec`` 是归一化后的（编译提示词要它），``raw_spec`` 是磁盘上的原样（快照进包）。
    """

    output_root = Path(str(spec["output_root"]))
    dest = Path(dest_dir)
    if dest.exists():
        raise FileExistsError(f"导出目录已存在：{dest}")
    dest.mkdir(parents=True)

    shots = [s for s in spec.get("shots", ()) if isinstance(s, Mapping)]
    shot_rows: list[dict[str, Any]] = []
    for shot in shots:
        sid = str(shot.get("id"))
        has_frame = _copy(output_root / "01_frames" / f"{sid}.png", dest / "frames" / f"{sid}.png", output_root)
        has_video = _copy(output_root / "02_video" / f"{sid}.mp4", dest / "videos" / f"{sid}.mp4", output_root)
        shot_rows.append({"id": sid, "frame": has_frame, "video": has_video})

    deliverable_path, deliverable_kind = _deliverable_path(spec)
    deliverable = None
    if _copy(deliverable_path, dest / "final.mp4", output_root):
        deliverable = {"kind": deliverable_kind, "source": deliverable_path.name, "file": "final.mp4"}

    voice_dir = output_root / VOICE_SUBDIR
    voices: list[str] = []
    if voice_dir.is_dir():
        for mp3 in sorted(voice_dir.glob("*.mp3")):
            if _copy(mp3, dest / "voices" / mp3.name, output_root):
                voices.append(mp3.name)

    # 海报（DESIGN §36）：07_poster 里最新一张成品进包，命名固定 poster.png
    poster = None
    poster_entry = latest_poster_entry(spec)
    if poster_entry and _copy(poster_dir(spec) / str(poster_entry["poster"]), dest / "poster.png", output_root):
        poster = {"source": str(poster_entry["poster"]), "file": "poster.png", "title": poster_entry.get("title") or "",
                  "made_from": poster_entry.get("source") or ""}

    (dest / "spec.json").write_text(json.dumps(raw_spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (dest / "prompts.md").write_text(_prompts_markdown(spec), encoding="utf-8")
    disclosure = ai_disclosure(spec)
    (dest / "ai_disclosure.md").write_text(_disclosure_markdown(disclosure, spec), encoding="utf-8")
    qc = _qc_decisions(output_root)
    if qc is not None:
        (dest / "qc.json").write_text(json.dumps(qc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def listing() -> list[dict[str, Any]]:
        rows = []
        for path in sorted(p for p in dest.rglob("*") if p.is_file()):
            rows.append({"path": path.relative_to(dest).as_posix(), "bytes": path.stat().st_size})
        return rows

    content_files = listing()
    manifest = {
        "schema_version": 1,
        "id": spec.get("id"),
        "title": spec.get("title_zh") or spec.get("title_en") or "",
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "app_version": __version__,
        "deliverable": deliverable,
        "shots": shot_rows,
        "voices": voices,
        "poster": poster,
        "has_qc": qc is not None,
        "ai_disclosure": disclosure,
        "files": content_files,
        "file_count": len(content_files),
        "total_bytes": sum(f["bytes"] for f in content_files),
        "excluded": ["settings / api keys (~/.storystudio)", "input assets (content/assets)", "ledger (sqlite)", "manifests/"],
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    checksum_lines = []
    for path in sorted(p for p in dest.rglob("*") if p.is_file()):
        checksum_lines.append(f"{_sha256(path)}  {path.relative_to(dest).as_posix()}")
    (dest / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    zip_path = Path(shutil.make_archive(str(dest.parent / dest.name), "zip", root_dir=str(dest.parent), base_dir=dest.name))
    # files/bytes 跟 manifest 同口径：内容文件（不含 manifest.json 与 checksums.sha256 这两份说明）
    return {
        "folder": str(dest),
        "zip": str(zip_path),
        "name": zip_path.name,
        "files": manifest["file_count"],
        "bytes": manifest["total_bytes"],
        "zip_bytes": zip_path.stat().st_size,
        "deliverable": deliverable_kind if deliverable else None,
        "poster": bool(poster),
        "shots": shot_rows,
    }


def verify_checksums(folder: Path) -> list[str]:
    """重算 checksums.sha256 里每一行，返回不匹配/缺失的相对路径（空 = 全部正确）。测试与收件人核对用。"""

    root = Path(folder)
    bad: list[str] = []
    for line in (root / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _sep, rel = line.partition("  ")
        target = root / rel
        if not target.is_file() or _sha256(target) != digest:
            bad.append(rel)
    return bad


def list_packages(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``09_export/`` 下已有的 zip（新到旧），附 manifest 摘要（文件夹还在时）。"""

    root = export_root(spec)
    if not root.is_dir():
        return []
    rows = []
    for zip_path in root.glob("*.zip"):
        if not zip_path.is_file():
            continue
        stat = zip_path.stat()
        row: dict[str, Any] = {
            "name": zip_path.name,
            "size": stat.st_size,
            "created": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
            "files": None,
            "bytes": None,
            "deliverable": None,
        }
        manifest_path = root / zip_path.stem / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                row["files"] = manifest.get("file_count")
                row["bytes"] = manifest.get("total_bytes")
                row["deliverable"] = (manifest.get("deliverable") or {}).get("kind")
            except (OSError, ValueError):
                pass
        rows.append(row)
    rows.sort(key=lambda r: r["created"], reverse=True)
    return rows


def resolve_zip(spec: Mapping[str, Any], name: str) -> Path | None:
    """``name`` 必须是 ``09_export/`` 里一个纯文件名的 zip；带路径分隔符、``..``、不存在都返回 None。"""

    if not name or name != Path(name).name or "/" in name or "\\" in name or name in (".", "..") or not name.endswith(".zip"):
        return None
    root = export_root(spec)
    target = root / name
    if not target.is_file() or target.is_symlink() or not _inside(target, root):
        return None
    return target


__all__ = ["build_package", "export_root", "package_name", "list_packages", "resolve_zip", "verify_checksums"]
