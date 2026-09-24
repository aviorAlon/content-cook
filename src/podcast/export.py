"""交付包：把一期播客拿得出手的东西打成自包含的文件夹 + 同名 zip（跟短剧 ``webui/export.py``、图片集
``imageset/export.py`` 同一套规矩）。

* **只拷白名单**：``spec.json``（项目文件磁盘原样）、``mix.mp3``（``08_mix/<ID>.mp3`` 成品，没混音就不带并在
  manifest 标明）、``shownotes.md`` / ``transcript.txt`` / ``chapters.json``（有就带）、``lines/<环节>_<序号>.mp3``
  （``07_audio`` 逐句，按 ``audio_manifest.json`` 的清单来、不扫目录；默认带，``include_lines=False`` 不带）、
  ``ai_disclosure.md``。每个文件都按 ``<output_root>`` 内的固定位置去找，不跟符号链接、不出 ``output_root``——
  ``~/.storystudio``（设置、密钥、音色库）、``content/assets``（上传的音乐）、账本、``manifests/`` 都碰不到。
* **AI 生成说明与工具清单**（``ai_disclosure.md``，口径写在里面）：稿子由文本模型起草（``spec.draft`` 的
  ``model`` / ``backend``）或手写；每位主播的音色引擎 / 音色名 / 是否占位来自 ``audio_manifest.json`` 的 ``hosts``；
  合成时间来自清单的 ``updated_at``。全部按项目文件与清单汇总，不查账本。
* ``manifest.json``（schema_version、id、标题、形式、主播、导出时间、程序版本、时长、章节、文件清单与大小、
  excluded 说明）+ ``checksums.sha256`` 覆盖包里每个文件。
* **不动原文件、从不覆盖**：只读 + 复制；导出目录固定在 ``<output_root>/09_export/``，包名带到分钟的时间戳，
  同一分钟已导出过就明确报错。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from genkit.version import __version__

from . import audio as A
from . import spec as S

TASK_KIND = "pc-export"
EXPORT_SUBDIR = "09_export"
MIX_NAME = "mix.mp3"
LINES_SUBDIR = "lines"
TEXT_NAMES = (A.SHOWNOTES_NAME, A.TRANSCRIPT_NAME, A.CHAPTERS_NAME)

NOTHING_TO_EXPORT = "还没有可以导出的东西——先合成"
ALREADY_THIS_MINUTE = "这一分钟已经导出过一个交付包了，稍等一分钟再点，或者直接下载已有的那个。"
# 静态的「不进包」清单（跟短剧交付包同一口径）；动态的（没混音、没带逐句）在打包时追加
EXCLUDED_ALWAYS = ["settings / api keys (~/.storystudio)", "music assets (content/assets)", "ledger (sqlite)",
                   "07_audio/audio_manifest.json (working file)", "manifests/"]
# 动态的两条按 spec.language 选：manifest.json 随包发出去，英文期不能夹着中文（中文期保持原来的写法）
EXCLUDED_NO_MIX = {"zh": "mix.mp3：还没混音（只有逐句音频）", "en": "mix.mp3: not mixed yet (per-line audio only)"}
EXCLUDED_NO_LINES = {"zh": "lines/：按要求没带逐句音频", "en": "lines/: per-line audio left out on request"}

AI_STATEMENT_DRAFTED = "本期播客由 AI 辅助制作：稿子由文本模型起草（之后可能有人工修改，项目文件不记录改动），主播音色为合成音；发布时按平台规定在显眼处标明 AI 生成。"
AI_STATEMENT_HANDWRITTEN = "本期播客由 AI 辅助制作：稿子为手写（项目文件里没有起草记录），主播音色为合成音；发布时按平台规定在显眼处标明 AI 生成。"
AI_STATEMENT_EN = "This episode was made with AI assistance: the hosts' voices are synthetic speech (and the script may have been drafted by a text model); label it as required by the platform before release."
# 英文期（spec.language == "en"）的主声明与口径：ai_disclosure.md 是随包发出去的，以前只有一句英文、其余全是中文标签
AI_STATEMENT_DRAFTED_EN = ("This episode was made with AI assistance: the script was drafted by a text model (it may have been edited by hand "
                           "afterwards; the project file does not record edits) and the hosts' voices are synthetic speech; label it as required "
                           "by the platform before release.")
AI_STATEMENT_HANDWRITTEN_EN = ("This episode was made with AI assistance: the script was written by hand (the project file has no drafting record) "
                               "and the hosts' voices are synthetic speech; label it as required by the platform before release.")
DISCLOSURE_BASIS = ("工具清单按项目文件与合成清单汇总，没有查账本：起草模型来自项目文件的 draft（手写的稿子没有这一项）；"
                    "每位主播的音色引擎与音色名来自 07_audio/audio_manifest.json 的 hosts（最近一次合成时记下的），"
                    "合成之后换过音色的，以清单为准；混音在本机用 ffmpeg 完成。")
DISCLOSURE_BASIS_HOSTS_FALLBACK = "合成清单里没有记下某几位主播的音色（清单是老版本，或这位主播是合成之后加的），标了「按导出时的音色设置」的那几行按导出时的设置汇总。"
DISCLOSURE_BASIS_EN = ("The tool list is summarised from the project file and the synthesis manifest, not from the ledger: the drafting model "
                       "comes from the project file's draft entry (hand-written scripts have none); each host's speech engine and voice name come "
                       "from the hosts recorded in 07_audio/audio_manifest.json at the last synthesis (if a voice was changed after synthesis, "
                       "the manifest wins); mixing was done locally with ffmpeg.")
DISCLOSURE_BASIS_HOSTS_FALLBACK_EN = ("The synthesis manifest has no voice record for some hosts (an older manifest, or a host added after "
                                      "synthesis); the rows marked \"per voice settings at export time\" are summarised from the current settings.")
# ai_disclosure.md 的标签，按语言选（跟 spec.LABELS 一个思路：只管写进文件的字）
_DISCLOSURE_LABELS: dict[str, dict[str, str]] = {
    "zh": {
        "title": "AI 生成说明与工具清单", "statement": "【AI 生成说明】", "provenance": "【用到的生成工具】", "script": "稿子",
        "script_model": "- 稿子：文本模型 {model} 起草（来源 {backend}{when}）", "when": "，{when}", "script_hand": "- 稿子：手写（项目文件里没有起草记录）",
        "placeholder": "占位音色", "missing": "，绑的档案「{profile_id}」已不存在", "no_tts": "，绑的档案「{profile_id}」只有描述",
        "library": "音色库档案「{label}」（{profile_id}）", "library_old": "音色库档案「{label}」", "inline": "内联指定音色",
        "by_manifest": "按合成清单", "by_current": "按导出时的音色设置", "host": "- {name}（{role}）：{engine} / {voice}（{tag}，{basis}）",
        "synthesized_at": "- 合成时间：{when}", "mixed": "- 混音：ffmpeg 本机混音，成品 mix.mp3",
        "mix_stale": "（停顿 / 音乐 / 台词改过之后没重混，包里的成品是按旧参数混的）", "not_mixed": "- 混音：还没混音，包里没有成品",
    },
    "en": {
        "title": "AI disclosure and tool list", "statement": "[AI disclosure] ", "provenance": "[Generation tools used]", "script": "Script",
        "script_model": "- Script: drafted by text model {model} (source {backend}{when})", "when": ", {when}",
        "script_hand": "- Script: written by hand (the project file has no drafting record)",
        "placeholder": "placeholder voice", "missing": ", the bound profile \"{profile_id}\" no longer exists",
        "no_tts": ", the bound profile \"{profile_id}\" is description-only",
        "library": "voice-library profile \"{label}\" ({profile_id})", "library_old": "voice-library profile \"{label}\"", "inline": "inline voice",
        "by_manifest": "per synthesis manifest", "by_current": "per voice settings at export time",
        "host": "- {name} ({role}): {engine} / {voice} ({tag}, {basis})",
        "synthesized_at": "- Synthesized at: {when}", "mixed": "- Mix: mixed locally with ffmpeg, product mix.mp3",
        "mix_stale": " (pauses / music / lines changed since the last mix; the mix.mp3 in this package was made with the old inputs)",
        "not_mixed": "- Mix: not mixed yet, no product in this package",
    },
}


def _is_en(spec: Mapping[str, Any]) -> bool:
    return str(spec.get("language") or "zh") == "en"


def _dl(spec: Mapping[str, Any]) -> dict[str, str]:
    return _DISCLOSURE_LABELS["en" if _is_en(spec) else "zh"]


HostLookup = Callable[[str], dict[str, Any] | None]


class ExportError(RuntimeError):
    """打不了包（原因写在消息里）。"""


# ---------------------------------------------------------------------------- 路径


def export_root(spec: Mapping[str, Any]) -> Path:
    return S.output_root(spec) / EXPORT_SUBDIR


def package_name(spec: Mapping[str, Any], now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return f"{spec.get('id')}-{stamp}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


# ---------------------------------------------------------------------------- 有什么可导


def synthesized_lines(spec: Mapping[str, Any], manifest: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """清单里文件真的在 ``07_audio`` 里的句子（不跟符号链接、不出目录），按清单顺序。"""

    manifest = manifest if manifest is not None else A.load_manifest(spec)
    root = S.audio_dir(spec)
    out: list[dict[str, Any]] = []
    for entry in manifest.get("lines") or ():
        if not isinstance(entry, Mapping) or not entry.get("file"):
            continue
        name = str(entry["file"])
        if name != Path(name).name:
            continue
        path = root / name
        # 0 字节 / 跟清单记的大小对不上的残文件（合成到一半被截断）不算合成过：跟配音页的「已合成」同一个判定
        if A._line_file_ok(path, entry) and _inside(path, root):
            out.append(dict(entry))
    return out


def exportable(spec: Mapping[str, Any]) -> bool:
    """至少合成过一句，或者有混音成品，才有东西可导。"""

    mix = S.mix_dir(spec) / f"{spec['id']}.mp3"
    return bool(synthesized_lines(spec)) or (mix.is_file() and mix.stat().st_size > 0)


# ---------------------------------------------------------------------------- AI 生成说明与工具清单


def _host_rows(spec: Mapping[str, Any], manifest: Mapping[str, Any], *, lookup: HostLookup | None = None) -> tuple[list[dict[str, Any]], str]:
    """每位主播：``{id, name, role, engine, voice, profile_id, label, placeholder, missing, no_tts, from}``；
    ``voice`` 是真正送去 TTS 的音色名、``profile_id`` 是音色库档案 id（内联 / 占位为空）；
    ``from`` = manifest（清单记的）/ current（按导出时的音色设置）。老清单（voice 记的是档案 id、没有 profile_id）照旧显示。"""

    recorded = manifest.get("hosts") if isinstance(manifest.get("hosts"), Mapping) else {}
    current: dict[str, dict[str, Any]] | None = None
    rows: list[dict[str, Any]] = []
    basis = "manifest"
    for host in spec.get("hosts") or ():
        hid = str(host.get("id") or "")
        info = recorded.get(hid) if isinstance(recorded.get(hid), Mapping) else None
        source = "manifest"
        if info is None:
            if current is None:
                try:
                    current = A.host_profiles(spec, lookup=lookup)
                except Exception:  # noqa: BLE001 - 音色库读不到不该让导出失败，写 unknown
                    current = {}
            info = current.get(hid) or {}
            source = "current"
            basis = "current"
        placeholder = bool(info.get("placeholder"))
        voice = str(info.get("voice") or "")
        label = str(info.get("label") or "")
        profile_id = str(info.get("profile_id") or "")
        if not profile_id and "profile_id" not in info and label and voice and label != voice and not placeholder:
            profile_id = voice          # 老清单：有档案 id 就只记 id 在 voice 里，label 是档案名
        row = {
            "id": hid, "name": str(host.get("name") or hid), "role": str(host.get("role") or ""),
            "engine": str(info.get("engine") or "unknown"), "voice": voice or str(info.get("id") or "unknown"), "profile_id": profile_id,
            "label": label, "placeholder": placeholder, "missing": bool(info.get("missing")), "no_tts": bool(info.get("no_tts")), "from": source,
        }
        if placeholder and _is_en(spec):
            # 占位音色的 label 是 audio.host_profiles 拼的中文（「占位音色 …」「档案「…」已不存在，暂用占位音色 …」），而这一行原样
            # 进随包发出去的 manifest.json——英文期按 ai_disclosure.md 同一套英文标签重拼。档案名 / 内联音色名是用户内容，不动
            row["label"] = _placeholder_label(row, _DISCLOSURE_LABELS["en"])
        rows.append(row)
    return rows, basis


def _placeholder_label(host: Mapping[str, Any], labels: Mapping[str, str]) -> str:
    """占位主播的一句标签：「placeholder voice <音色>」，档案坏了的再说坏在哪（跟 ``_host_tag`` 同一套措辞）。"""

    text = f"{labels['placeholder']} {host.get('voice')}"
    if host.get("missing"):
        text += labels["missing"].format(profile_id=host.get("profile_id"))
    elif host.get("no_tts"):
        text += labels["no_tts"].format(profile_id=host.get("profile_id"))
    return text


def ai_disclosure(spec: Mapping[str, Any], manifest: Mapping[str, Any] | None = None, *, mixed: bool = False, mix_stale: bool = False,
                  lookup: HostLookup | None = None) -> dict[str, Any]:
    """生成内容标注 + 用了哪些工具（按项目文件与合成清单汇总，不查账本）。

    ``script``：``draft.model`` 有值就是文本模型起草（``model`` / ``backend`` / ``created_at``），否则算手写；
    ``hosts``：每位主播的引擎 / 音色 / 是否占位（清单记的；老清单没记就按导出时的音色设置，``basis`` 会说明）；
    ``synthesized_at``：清单的 ``updated_at``；``mixed``：有没有混音成品；``mix_stale``：成品是按旧停顿 / 旧音乐混的。
    英文期 ``statement`` / ``basis`` 是英文、中文声明放 ``statement_zh``；``statement_en`` 永远是那句英文声明。
    """

    manifest = manifest if manifest is not None else A.load_manifest(spec)
    en = _is_en(spec)
    draft = spec.get("draft") if isinstance(spec.get("draft"), Mapping) else {}
    model = str(draft.get("model") or "").strip()
    drafted = bool(model)
    created = draft.get("created_at")
    script = {
        "label": _dl(spec)["script"], "drafted_by_model": drafted,
        "model": model or None, "backend": str(draft.get("backend") or "").strip() or None,
        "created_at": _iso(created), "note": str(draft.get("note") or ""),
    }
    hosts, hosts_basis = _host_rows(spec, manifest, lookup=lookup)
    statement_zh = AI_STATEMENT_DRAFTED if drafted else AI_STATEMENT_HANDWRITTEN
    statement_en = AI_STATEMENT_DRAFTED_EN if drafted else AI_STATEMENT_HANDWRITTEN_EN
    if en:
        basis = DISCLOSURE_BASIS_EN if hosts_basis == "manifest" else f"{DISCLOSURE_BASIS_EN} {DISCLOSURE_BASIS_HOSTS_FALLBACK_EN}"
    else:
        basis = DISCLOSURE_BASIS if hosts_basis == "manifest" else f"{DISCLOSURE_BASIS}{DISCLOSURE_BASIS_HOSTS_FALLBACK}"
    return {
        "statement": statement_en if en else statement_zh, "statement_zh": statement_zh, "statement_en": AI_STATEMENT_EN,
        "script": script, "hosts": hosts, "synthesized_at": _iso(manifest.get("updated_at")),
        "mixed": bool(mixed), "mix_stale": bool(mixed) and bool(mix_stale), "mixer": "ffmpeg (local)" if mixed else None, "basis": basis,
    }


def _iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value)).astimezone().isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def _host_tag(host: Mapping[str, Any], labels: Mapping[str, str]) -> str:
    """主播行的来源标签：占位（附上档案坏在哪）/ 音色库档案「名」（id）/ 内联指定音色。
    以前只看 label 有没有——内联档案的 label 就是音色名，全被标成「音色库档案」，「指定音色」是死分支。"""

    if host.get("placeholder"):
        tag = labels["placeholder"]
        if host.get("missing"):
            tag += labels["missing"].format(profile_id=host.get("profile_id"))
        elif host.get("no_tts"):
            tag += labels["no_tts"].format(profile_id=host.get("profile_id"))
        return tag
    if host.get("profile_id"):
        if host.get("label") and host["label"] != host["profile_id"]:
            return labels["library"].format(label=host["label"], profile_id=host["profile_id"])
        return labels["library_old"].format(label=host["profile_id"])
    return labels["inline"]


def disclosure_markdown(disclosure: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    L = _dl(spec)
    roles = S.labels(spec)["roles"]
    script = disclosure.get("script") or {}
    # 主声明按语言；另一种语言的声明作引文附在下面（中文期附英文、英文期附中文），两种读者都看得懂
    quoted = disclosure.get("statement_en", AI_STATEMENT_EN) if not _is_en(spec) else disclosure.get("statement_zh", "")
    lines = [f"# {L['title']} · {spec.get('title') or spec.get('id')}", "",
             f"{L['statement']}{disclosure['statement']}", "", f"> {quoted}", "",
             L["provenance"], ""]
    if script.get("drafted_by_model"):
        when = L["when"].format(when=script["created_at"]) if script.get("created_at") else ""
        lines.append(L["script_model"].format(model=script.get("model") or "unknown", backend=script.get("backend") or "unknown", when=when))
    else:
        lines.append(L["script_hand"])
    for host in disclosure.get("hosts") or ():
        role = roles.get(str(host.get("role") or ""), str(host.get("role") or ""))
        basis = L["by_current"] if host.get("from") == "current" else L["by_manifest"]
        lines.append(L["host"].format(name=host.get("name"), role=role, engine=host.get("engine"), voice=host.get("voice"),
                                      tag=_host_tag(host, L), basis=basis))
    lines.append(L["synthesized_at"].format(when=disclosure.get("synthesized_at") or "unknown"))
    if disclosure.get("mixed"):
        lines.append(L["mixed"] + (L["mix_stale"] if disclosure.get("mix_stale") else ""))
    else:
        lines.append(L["not_mixed"])
    lines += ["", f"> {disclosure['basis']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- 打包


def _spec_snapshot(spec: Mapping[str, Any], dest: Path) -> str:
    """``spec.json``：项目文件磁盘原样（``content/podcast/<ID>.json``）；文件不在（内存里的 spec）就写归一化后的形状。"""

    project_root = Path(str(spec.get("project_root") or ""))
    source = S.spec_path(project_root, str(spec["id"])) if str(spec.get("project_root") or "") else None
    if source is not None and _copy(source, dest / "spec.json", S.content_dir(project_root)):
        return "disk"
    (dest / "spec.json").write_text(json.dumps(S.to_disk(spec), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "memory"


def _read_chapters(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def build_package(spec: Mapping[str, Any], dest: Path | None = None, *, now: datetime | None = None, include_lines: bool = True,
                  lookup: HostLookup | None = None) -> dict[str, Any]:
    """在 ``dest``（默认 ``<output_root>/09_export/<ID>-<时间>``）里建交付包并打成 zip。

    返回 ``{folder, zip, name, files, bytes, zip_bytes, mixed, lines, duration}``。没合成过任何一句也没有成品 →
    ``ExportError``；目录或同名 zip 已存在（同一分钟导出过）→ ``ExportError``，绝不覆盖。
    """

    output_root = S.output_root(spec)
    manifest_src = A.load_manifest(spec)
    lines = synthesized_lines(spec, manifest_src)
    mix_src = S.mix_dir(spec) / f"{spec['id']}.mp3"
    has_mix = mix_src.is_file() and not mix_src.is_symlink() and mix_src.stat().st_size > 0 and _inside(mix_src, output_root)
    if not lines and not has_mix:
        raise ExportError(NOTHING_TO_EXPORT)
    dest = Path(dest) if dest is not None else export_root(spec) / package_name(spec, now)
    # 同名 zip 用「拼接」不用 with_suffix：id 里允许有点，with_suffix 会把点后面那截当后缀换掉
    if dest.exists() or (dest.parent / f"{dest.name}.zip").exists():
        raise ExportError(ALREADY_THIS_MINUTE)
    dest.mkdir(parents=True)

    excluded = list(EXCLUDED_ALWAYS)
    lang = "en" if _is_en(spec) else "zh"
    spec_source = _spec_snapshot(spec, dest)
    mix_row: dict[str, Any] | None = None
    if has_mix and _copy(mix_src, dest / MIX_NAME, output_root):
        mix_row = {"file": MIX_NAME, "source": f"08_mix/{mix_src.name}", "bytes": (dest / MIX_NAME).stat().st_size}
    else:
        excluded.append(EXCLUDED_NO_MIX[lang])
    texts: dict[str, bool] = {}
    for name in TEXT_NAMES:
        texts[name] = _copy(S.mix_dir(spec) / name, dest / name, output_root)
    names = {str(h.get("id")): str(h.get("name") or h.get("id")) for h in spec.get("hosts") or ()}
    line_rows: list[dict[str, Any]] = []
    if include_lines:
        for entry in lines:
            name = str(entry["file"])
            target = dest / LINES_SUBDIR / name
            if not _copy(S.audio_dir(spec) / name, target, output_root):
                continue
            who = str(entry.get("who") or "")
            line_rows.append({"segment": str(entry.get("segment") or ""), "index": int(entry.get("index") or 0), "who": who,
                              "who_name": names.get(who, who), "text": str(entry.get("text") or ""),
                              "duration": float(entry.get("duration") or 0.0), "file": f"{LINES_SUBDIR}/{name}",
                              "bytes": target.stat().st_size})
    else:
        excluded.append(EXCLUDED_NO_LINES[lang])

    chapters = _read_chapters(S.mix_dir(spec) / A.CHAPTERS_NAME)
    # 成品是按旧停顿 / 旧音乐混的（chapters.json 的指纹跟当前对不上）：包照打，但 manifest 与 ai_disclosure 都要说清
    mix_stale = mix_row is not None and str(chapters.get("mix_hash") or "") != A.mix_fingerprint(spec, manifest_src)
    if mix_row is not None:
        mix_row["stale"] = mix_stale
    disclosure = ai_disclosure(spec, manifest_src, mixed=mix_row is not None, mix_stale=mix_stale, lookup=lookup)
    (dest / "ai_disclosure.md").write_text(disclosure_markdown(disclosure, spec), encoding="utf-8")

    content_files = [{"path": p.relative_to(dest).as_posix(), "bytes": p.stat().st_size}
                     for p in sorted(x for x in dest.rglob("*") if x.is_file())]
    manifest = {
        "schema_version": 1, "id": spec.get("id"), "module": S.MODULE, "title": spec.get("title") or "",
        "format": spec.get("format"), "format_name": S.format_name(spec), "language": spec.get("language"),
        "hosts": [{k: h.get(k) for k in ("id", "name", "role", "engine", "voice", "profile_id", "placeholder")} for h in disclosure["hosts"]],
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"), "app_version": __version__,
        "spec_source": spec_source,
        "duration": float(chapters.get("total") or 0.0) if chapters else None,
        "chapters": list(chapters.get("chapters") or []),
        "mix": mix_row, "texts": texts,
        "lines": line_rows, "line_count": len(line_rows), "lines_included": include_lines,
        "audio": {"synthesized_lines": len(lines), "synthesized_at": disclosure["synthesized_at"],
                  "stale": bool(A.is_stale(spec, manifest_src, lookup=lookup)) if lines else None,
                  "mix_stale": mix_stale if mix_row is not None else None},
        "ai_disclosure": disclosure,
        "files": content_files, "file_count": len(content_files), "total_bytes": sum(f["bytes"] for f in content_files),
        "excluded": excluded,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum_lines = [f"{_sha256(p)}  {p.relative_to(dest).as_posix()}" for p in sorted(x for x in dest.rglob("*") if x.is_file())]
    (dest / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    zip_path = Path(shutil.make_archive(str(dest), "zip", root_dir=str(dest.parent), base_dir=dest.name))
    # files/bytes 跟 manifest 同口径：内容文件（不含 manifest.json 与 checksums.sha256 这两份说明）
    return {"folder": str(dest), "zip": str(zip_path), "name": zip_path.name, "files": manifest["file_count"],
            "bytes": manifest["total_bytes"], "zip_bytes": zip_path.stat().st_size, "mixed": mix_row is not None,
            "lines": len(line_rows), "duration": manifest["duration"]}


# ---------------------------------------------------------------------------- 核对 / 历史 / 下载解析


def verify_checksums(folder: Path) -> list[str]:
    """重算 checksums.sha256 里每一行，返回不匹配 / 缺失的相对路径（空 = 全部正确）。"""

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
        if not zip_path.is_file() or zip_path.is_symlink():
            continue
        stat = zip_path.stat()
        row: dict[str, Any] = {"name": zip_path.name, "size": stat.st_size,
                               "created": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
                               "files": None, "bytes": None, "mixed": None, "lines": None, "duration": None}
        manifest_path = root / zip_path.stem / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                row.update({"files": manifest.get("file_count"), "bytes": manifest.get("total_bytes"),
                            "mixed": manifest.get("mix") is not None, "lines": manifest.get("line_count"),
                            "duration": manifest.get("duration")})
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


__all__ = ["TASK_KIND", "EXPORT_SUBDIR", "MIX_NAME", "LINES_SUBDIR", "NOTHING_TO_EXPORT", "ALREADY_THIS_MINUTE", "ExportError",
           "export_root", "package_name", "synthesized_lines", "exportable", "ai_disclosure", "disclosure_markdown", "build_package",
           "verify_checksums", "list_packages", "resolve_zip"]
