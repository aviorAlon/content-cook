"""AI 播客接口（DESIGN §50，契约 docs/podcast-spec.md §5）。

* ``GET  /api/podcast``                         列表（id / 标题 / 形式 / 主播数 / 预计分钟 / 进度）
* ``GET  /api/podcast/next-id``                 自动编号（PC-）
* ``GET  /api/podcast/formats``                 形式 / 环节种类 / cue 清单 / 默认值 / 八条约束 / 示例
* ``POST /api/podcast``                         新建（不填 id 由服务端编号）
* ``POST /api/podcast/examples/create``         复制示例
* ``GET/PUT/DELETE /api/podcast/{id}``          详情（spec + 估算 + 告警 + 问题卡 + 配音状态）/ 保存 / 归档
* ``POST /api/podcast/{id}/draft``              ``{note?}`` 起草整期（后台任务 pc-draft，1 次文本模型请求）
* ``POST /api/podcast/{id}/segments/{sid}/redraft``  ``{note}`` 只重写一个环节（同一任务种类）
* ``POST /api/podcast/{id}/humanlike-review``   真人感审稿（后台任务 pc-review，1 次文本模型请求）；``GET`` 上次结果 + 过时 + 问题卡
* ``POST /api/podcast/{id}/synthesize``         ``{force?}`` 逐句配音 + 混音（后台任务 pc-audio）
* ``POST /api/podcast/{id}/assets``             ``{filename, data_base64, slot}`` 上传片头 / 垫乐 / 片尾
* ``GET  /api/podcast/{id}/artifact/audio/{name}``   逐句 mp3 或混音成品（只认清单里的文件名）
* ``GET  /api/podcast/{id}/artifact/text/{name}``    shownotes.md / transcript.txt / chapters.json
* ``POST /api/podcast/{id}/export``             ``{include_lines?}`` 打交付包（后台任务 pc-export）；一句都没合成过 400，同一分钟已导出 409
* ``GET  /api/podcast/{id}/export``             已有交付包列表（名字 / 大小 / 时间 / manifest 摘要 / 下载地址）
* ``GET  /api/podcast/{id}/export/{name}``      下载 zip（只认 09_export 里的纯文件名）

所有任务 ``module="podcast"``。测试注入点：``DISPATCH``（文本模型）、``SYNTHESIZER`` / ``PROBER`` / ``RUNNER`` / ``FFMPEG``、``VOICE_LOOKUP``。
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from genkit import local as local_mod
from genkit import trash
from genkit.ids import is_valid_id, slugify
from genkit.paths import resource as _resource
from podcast import audio as A
from podcast import craft as C
from podcast import review as R
from podcast import export as E
from podcast import spec as S
from podcast.lint import lint_podcast

from .. import deps, tasks
from ..problems import problem_cards
from ..uploads import UploadContentError, accept_audio

router = APIRouter(prefix="/api/podcast", tags=["podcast"])

MODULE = "podcast"
BASE = "/api/podcast"
EXAMPLES_DIR = _resource("examples", "podcast")
EXAMPLE_DEFAULT = "short_video_demo"
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
KINDS = {"draft": C.TASK_KIND, "audio": A.TASK_KIND, "review": R.TASK_KIND, "export": E.TASK_KIND}
MEDIA_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
               ".ogg": "audio/ogg", ".flac": "audio/flac"}
TEXT_FILES = {A.SHOWNOTES_NAME: "text/markdown", A.TRANSCRIPT_NAME: "text/plain", A.CHAPTERS_NAME: "application/json"}

# 测试注入点（None = 用真实实现）
DISPATCH: Callable[..., Any] | None = None
SYNTHESIZER: Callable[..., Any] | None = None
PROBER: Callable[..., float] | None = None
RUNNER: Callable[..., Any] | None = None
FFMPEG: str | None = None
VOICE_LOOKUP: Callable[[str], dict[str, Any] | None] | None = None


# ---------------------------------------------------------------------------- 辅助


def _root() -> Path:
    return deps.PROJECT_ROOT


def _note(**fields: Any) -> None:
    task_id = tasks.current_task_id()
    if task_id:
        tasks.note(task_id, **fields)


def _relative(path: Path) -> str:
    try:
        return path.relative_to(_root()).as_posix()
    except ValueError:
        return str(path)


def _load(spec_id: str) -> dict[str, Any]:
    try:
        return S.load(spec_id, project_root=_root())
    except S.SpecError as exc:
        raise HTTPException(404, str(exc)) from exc


def _write(body: dict[str, Any], *, spec_id: str) -> dict[str, Any]:
    try:
        normalized = S.normalize(body, project_root=_root(), spec_id=spec_id)
    except S.SpecError as exc:
        raise HTTPException(400, f"改动没通过校验，没有写盘：{exc}") from exc
    S.save(normalized)
    return normalized


def _taken_ids() -> list[str]:
    # 自动编号时回收站里的 id 也算占用：删一个再建一个，新项目要是拿到刚删的 id，回收站里那一份就再也恢复不回来
    return S.existing_ids(_root()) + trash.archived_ids(_root(), MODULE)


def _fresh_id(requested: str | None) -> str:
    spec_id = str(requested or "").strip() or S.next_id(_taken_ids())
    if not is_valid_id(spec_id):
        raise HTTPException(400, "项目 id 只能用英文、数字、下划线、点、短横线")
    if S.spec_path(_root(), spec_id).exists():
        raise HTTPException(409, f"播客 {spec_id} 已经存在，换一个 id")
    return spec_id


def _audio_url(spec_id: str, name: str) -> str:
    return f"{BASE}/{spec_id}/artifact/audio/{name}"


def _text_url(spec_id: str, name: str) -> str:
    return f"{BASE}/{spec_id}/artifact/text/{name}"


def _export_url(spec_id: str, name: str) -> str:
    return f"{BASE}/{spec_id}/export/{name}"


def _exports_payload(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**row, "url": _export_url(spec["id"], row["name"])} for row in E.list_packages(spec)]


def _busy(spec_id: str) -> dict[str, str | None]:
    return {name: tasks.active_task_id(kind, spec_id) for name, kind in KINDS.items()}


def _progress(spec: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = state if state is not None else A.status(spec, lookup=VOICE_LOOKUP)
    drafted = any(s.get("lines") for s in spec.get("segments") or ())
    # 「已混音」= 成品在、台词没过时、成品也不是按旧停顿 / 旧音乐混的
    return {"drafted": drafted, "audio": state["total"] > 0 and state["synthesized"] >= state["total"] and not state["stale"],
            "mixed": bool(state["mixed"]) and not state["stale"] and not state.get("mix_stale")}


def _lint(spec: dict[str, Any]) -> list[str]:
    """静态检查，音色重复按音色库解析后的真正音色比（读不到音色库就退回按档案 id 比）。"""

    try:
        profiles = A.host_profiles(spec, lookup=VOICE_LOOKUP)
    except Exception:  # noqa: BLE001 - 音色库坏了不拦详情页
        profiles = None
    return lint_podcast(spec, profiles)


def _detail(spec: dict[str, Any]) -> dict[str, Any]:
    warnings = _lint(spec)
    # 真人感审稿：只有核对过引文、且没过时的 risk / violated 条目进问题卡；审稿文件坏了不拦详情页
    try:
        warnings = warnings + R.review_problems(spec)
    except Exception:  # noqa: BLE001
        pass
    state = A.status(spec, lookup=VOICE_LOOKUP)
    sid = spec["id"]
    lines = [{**row, "url": _audio_url(sid, row["file"]) if row.get("file") else None} for row in state["lines"]]
    hosts = []
    for host in spec["hosts"]:
        info = state["hosts"].get(host["id"], {})
        hosts.append({**host, "voice_label": info.get("label", ""), "placeholder": bool(info.get("placeholder", True)),
                      "missing": bool(info.get("missing")), "no_tts": bool(info.get("no_tts"))})
    return {
        "id": sid, "module": MODULE, "title": spec["title"],
        "spec": S.to_disk(spec),
        "estimate": C.estimate(spec),
        "warnings": warnings,
        "problems": problem_cards(warnings),
        "hosts": hosts,
        "audio": {
            "lines": lines, "synthesized": state["synthesized"], "total": state["total"], "stale": state["stale"],
            "mixed": state["mixed"], "mix_stale": state["mix_stale"],
            "mix_url": _audio_url(sid, state["mix_file"]) if state["mixed"] and state["mix_file"] else None,
            "mix_duration": state["mix_duration"], "chapters": state["chapters"], "ffmpeg": state["ffmpeg"],
        },
        "texts": {
            "shownotes_url": _text_url(sid, A.SHOWNOTES_NAME) if state["texts"].get(A.SHOWNOTES_NAME) else None,
            "transcript_url": _text_url(sid, A.TRANSCRIPT_NAME) if state["texts"].get(A.TRANSCRIPT_NAME) else None,
            "chapters_url": _text_url(sid, A.CHAPTERS_NAME) if state["texts"].get(A.CHAPTERS_NAME) else None,
        },
        "draft": dict(spec.get("draft") or {}),
        "exports": _exports_payload(spec),
        "progress": _progress(spec, state),
        "busy": _busy(sid),
        "limits": {"target_minutes": [S.MINUTES_MIN, S.MINUTES_MAX], "hosts": [S.HOSTS_MIN, S.HOSTS_MAX]},
    }


class DraftRequest(BaseModel):
    note: str | None = None


class SynthesizeRequest(BaseModel):
    force: bool = False


class ExportRequest(BaseModel):
    include_lines: bool = True


class AssetUpload(BaseModel):
    filename: str
    data_base64: str
    slot: str


class ExampleCreateRequest(BaseModel):
    name: str | None = None
    id: str | None = None


# ---------------------------------------------------------------------------- 列表 / 编号 / 形式 / 新建


@router.get("")
def list_podcasts() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec_id in S.existing_ids(_root()):
        entry: dict[str, Any] = {"id": spec_id, "module": MODULE, "pipeline": MODULE, "path": f"content/podcast/{spec_id}.json"}
        try:
            spec = S.load(spec_id, project_root=_root())
        except Exception as exc:  # noqa: BLE001 - 一个坏文件不能让列表挂掉
            entry.update({"status": "error", "error": str(exc)})
            rows.append(entry)
            continue
        est = C.estimate(spec)
        entry.update({
            "status": "ok", "title": spec["title"], "format": spec["format"],
            "format_name": S.FORMATS[spec["format"]]["name"], "hosts": len(spec["hosts"]),
            "minutes": est["minutes"] if est["lines"] else spec["target_minutes"], "target_minutes": spec["target_minutes"],
            "segments": est["segments"], "lines": est["lines"], "progress": _progress(spec),
        })
        rows.append(entry)
    return rows


@router.get("/next-id")
def next_id() -> dict[str, str]:
    return {"id": S.next_id(_taken_ids())}


@router.get("/formats")
def list_formats() -> dict[str, Any]:
    examples = sorted(p.stem for p in EXAMPLES_DIR.glob("*.json")) if EXAMPLES_DIR.is_dir() else []
    return {
        "formats": S.formats_payload(),
        "kinds": [{"id": k, "name": S.SEGMENT_KIND_LABELS[k]} for k in S.SEGMENT_KINDS],
        "line_kinds": list(S.LINE_KINDS),
        "cues": list(S.CUES),
        "roles": list(S.ROLES),
        "languages": list(S.LANGUAGES),
        "defaults": {"target_minutes": S.DEFAULT_MINUTES, "pause": dict(S.DEFAULT_PAUSE), "music": dict(S.DEFAULT_MUSIC), "format": "duo"},
        "limits": {"target_minutes": [S.MINUTES_MIN, S.MINUTES_MAX], "hosts": [S.HOSTS_MIN, S.HOSTS_MAX]},
        "craft": list(C.CRAFT_RULES),
        "examples": examples,
    }


@router.post("")
def create(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体要是项目文件对象")
    spec_id = _fresh_id(body.get("id"))
    return _detail(_write(body, spec_id=spec_id))


@router.post("/examples/create")
def create_from_example(req: ExampleCreateRequest | None = None) -> dict[str, Any]:
    req = req or ExampleCreateRequest()
    name = Path(req.name or EXAMPLE_DEFAULT).name
    source = EXAMPLES_DIR / f"{name}.json"
    if not source.is_file():
        raise HTTPException(404, f"没有这个示例：{name}")
    spec_id = _fresh_id(req.id)
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    try:
        spec = S.normalize(data, project_root=_root(), spec_id=spec_id)
    except S.SpecError as exc:
        raise HTTPException(400, f"示例没通过结构校验（这是示例文件本身的问题）：{exc}") from exc
    S.save(spec)
    return {"example": name, "path": f"content/podcast/{spec_id}.json", **_detail(spec)}


# ---------------------------------------------------------------------------- 读 / 写 / 删


@router.get("/{spec_id}")
def get_podcast(spec_id: str) -> dict[str, Any]:
    return _detail(_load(spec_id))


@router.put("/{spec_id}")
def put_podcast(spec_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _load(spec_id)
    if str(body.get("id") or spec_id) != spec_id:
        raise HTTPException(400, f"项目文件里的 id「{body.get('id')}」跟地址里的 {spec_id} 对不上")
    return _detail(_write(body, spec_id=spec_id))


BUSY_NAMES = {"draft": "起草", "audio": "合成", "review": "审稿", "export": "导出"}


@router.delete("/{spec_id}")
def delete_podcast(spec_id: str) -> dict[str, Any]:
    """归档。有任务在跑（起草 / 合成 / 审稿 / 导出）409：不然任务线程接着往被搬走的目录里写、把 output 甚至项目文件重建出来。"""

    # 不走 _load：坏掉的项目文件（列表里 status=error 的）也得能删
    if not is_valid_id(spec_id) or not S.spec_path(_root(), spec_id).is_file():
        raise HTTPException(404, f"没有这期播客：{spec_id}")
    running = [BUSY_NAMES[name] for name, task_id in _busy(spec_id).items() if task_id]
    if running:
        raise HTTPException(409, f"这期播客正在{' / '.join(running)}，等它结束再删")
    try:
        result = S.archive(spec_id, project_root=_root())
    except S.PodcastArchiveError as exc:
        raise HTTPException(409, str(exc)) from exc
    except S.SpecError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"archived_to": _relative(Path(result["archived_to"])), "output_moved": result["output_moved"],
            "assets_moved": result["assets_moved"]}


# ---------------------------------------------------------------------------- 起草


def _draft_cost_note() -> str:
    return local_mod.cost_note_for("text", 1, C.REMOTE_COST_NOTE)


def _text_model_ready() -> bool:
    if DISPATCH is not None:
        return True
    # 不能写 bool(scriptgen_backend())：没配时它返回 "unconfigured"（非空），400 闸会永远不生效（2026-09-22 审计 PC-01）
    from storyvideo.scriptgen import text_model_available

    return text_model_available()


def _run_draft(spec_id: str, note: str, only_segment: str | None) -> dict[str, Any]:
    spec = S.load(spec_id, project_root=_root())
    fresh = C.run_draft(spec, note=note, only_segment=only_segment, dispatch=DISPATCH)
    est = C.estimate(fresh)
    return {"segments": est["segments"], "lines": est["lines"], "minutes": est["minutes"], "only_segment": only_segment,
            "warnings": len(_lint(fresh))}


def _start_draft(spec_id: str, note: str, only_segment: str | None) -> dict[str, Any]:
    spec = _load(spec_id)
    if not spec["hosts"]:
        raise HTTPException(400, "主播表是空的——先加至少一位主播（名字 + 人设），稿子才知道谁在说")
    if not _text_model_ready():
        raise HTTPException(400, deps.NO_TEXT_MODEL)
    if only_segment and only_segment not in {s["id"] for s in spec["segments"]}:
        raise HTTPException(404, f"没有这个环节：{only_segment}")
    try:
        task_id = tasks.start(KINDS["draft"], spec_id, lambda: _run_draft(spec_id, note, only_segment),
                              params={"note": note, "segment": only_segment}, module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id, "cost_note": _draft_cost_note()}


@router.post("/{spec_id}/draft")
def draft(spec_id: str, req: DraftRequest | None = None) -> dict[str, Any]:
    req = req or DraftRequest()
    return _start_draft(spec_id, (req.note or "").strip(), None)


@router.post("/{spec_id}/segments/{segment_id}/redraft")
def redraft_segment(spec_id: str, segment_id: str, req: DraftRequest | None = None) -> dict[str, Any]:
    req = req or DraftRequest()
    return _start_draft(spec_id, (req.note or "").strip(), segment_id)


# ---------------------------------------------------------------------------- 真人感审稿


def _review_cost_note() -> str:
    return local_mod.cost_note_for("text", 1, R.REMOTE_COST_NOTE)


def _run_review(spec_id: str) -> dict[str, Any]:
    spec = S.load(spec_id, project_root=_root())
    record = R.run_review(spec, dispatch=DISPATCH)
    items = record["result"]["items"]
    return {
        "checklist_id": record["checklist_id"], "checklist_name": record["checklist_name"], "created_at": record["created_at"],
        "verification": record["verification"],
        "counts": {
            "violated": sum(1 for it in items if it["verdict"] == "violated"),
            "risk": sum(1 for it in items if it["verdict"] == "risk" and it.get("evidence_verified")),
            "ok": sum(1 for it in items if it["verdict"] == "ok"),
            "unverified": sum(1 for it in items if it.get("evidence_verified") is False),
        },
    }


@router.post("/{spec_id}/humanlike-review")
def humanlike_review(spec_id: str) -> dict[str, Any]:
    """真人感审稿：跟短剧的按题材审稿 / 工艺审稿同一套闸门（模型逐条判 + 程序逐字核对引文）。后台任务 ``pc-review``，
    发 1 次文本模型请求；稿子里没台词 400，没配文本模型 400，清单文件读不出来 400，已经在跑 409。
    不改项目文件，结果写在 manifests/humanlike_review.json。"""

    spec = _load(spec_id)
    if not A.plan(spec):
        raise HTTPException(400, R.NO_LINES_MESSAGE)
    if not _text_model_ready():
        raise HTTPException(400, deps.NO_TEXT_MODEL)
    info = R.checklist_info()
    if info is None:
        raise HTTPException(400, R.MISSING_CHECKLIST_MESSAGE)
    try:
        task_id = tasks.start(KINDS["review"], spec_id, lambda: _run_review(spec_id), params={"checklist": info["id"]}, module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id, "cost_note": _review_cost_note()}


@router.get("/{spec_id}/humanlike-review")
def get_humanlike_review(spec_id: str) -> dict[str, Any]:
    """上次真人感审稿的结果 + 是否过时 + 核对过的条目变成的问题卡。没审过 ``review: null``。只读，不花钱。"""

    spec = _load(spec_id)
    state = R.review_state(spec)
    return {
        "checklist": R.checklist_info(),
        "review": state["review"],
        "stale": state["stale"],
        "note": state["note"],
        "problems": problem_cards(state["warnings"]),
        "cost_note": _review_cost_note(),
        "task_id": tasks.active_task_id(KINDS["review"], spec_id),
    }


# ---------------------------------------------------------------------------- 配音 + 混音


def _audio_cost_note(spec: dict[str, Any]) -> str:
    profiles = A.host_profiles(spec, lookup=VOICE_LOOKUP)
    n = len(A.plan(spec))
    engines = {str(p.get("engine") or "edge") for p in profiles.values()}
    if engines <= {"edge", ""}:
        return f"{n} 句走 edge-tts（免费）+ 本机混音，不花钱；文本和音色没变的句子会跳过"
    return local_mod.cost_note_for("voice", n, f"{n} 句语音合成（{' / '.join(sorted(engines))}），没变的句子会跳过；混音在本机")


def _run_synthesize(spec_id: str, force: bool) -> dict[str, Any]:
    spec = S.load(spec_id, project_root=_root())
    return A.run_synthesize(spec, force=force, synthesizer=SYNTHESIZER, prober=PROBER, lookup=VOICE_LOOKUP,
                            ffmpeg=FFMPEG, runner=RUNNER, note=_note)


def _broken_voice_ref(spec: dict[str, Any]) -> str | None:
    """主播绑的音色档案坏了（不存在 / 只有描述）就说第一个是谁、怎么改；都好返回 None。

    保存照收（档案可能是事后被删的，不能因此让整期存不了盘），详情和告警会点名；但真去合成时拦下：人明明挑了音色，
    整期却按占位音色出声，事后才发现要返工。想用占位音色就把这位主播的音色清空，意图说清楚再合成。
    只拦稿子里真有台词的主播：访谈 / 圆桌里还没轮到说话的嘉宾，他的档案这次合成根本用不上，拦了反而整期合成不了
    （lint 的告警照样对所有主播出）。
    """

    profiles = A.host_profiles(spec, lookup=VOICE_LOOKUP)
    speakers = {row["who"] for row in A.plan(spec)}
    for host in spec.get("hosts") or ():
        if str(host.get("id") or "") not in speakers:
            continue
        info = profiles.get(str(host.get("id") or "")) or {}
        name = host.get("name") or host.get("id")
        if info.get("missing"):
            return f"没开始合成：主播「{name}」绑的音色档案「{info.get('profile_id')}」已不存在——去音色库重新挑一个，或把这位主播的音色清空（用占位音色）再合成"
        if info.get("no_tts"):
            return f"没开始合成：主播「{name}」绑的档案「{info.get('profile_id')}」只有文字描述，不能出声——换一个带 TTS 音色的档案，或把这位主播的音色清空（用占位音色）再合成"
    return None


@router.post("/{spec_id}/synthesize")
def synthesize(spec_id: str, req: SynthesizeRequest | None = None) -> dict[str, Any]:
    req = req or SynthesizeRequest()
    spec = _load(spec_id)
    if not A.plan(spec):
        raise HTTPException(400, "稿子里还没有台词——先起草或手写几句，再合成")
    broken = _broken_voice_ref(spec)
    if broken:
        raise HTTPException(400, broken)
    try:
        task_id = tasks.start(KINDS["audio"], spec_id, lambda: _run_synthesize(spec_id, req.force),
                              params=req.model_dump(), module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id, "lines": len(A.plan(spec)), "cost_note": _audio_cost_note(spec)}


# ---------------------------------------------------------------------------- 交付包


def _run_export(spec_id: str, dest: Path, include_lines: bool) -> dict[str, Any]:
    spec = S.load(spec_id, project_root=_root())
    _note(progress="pack", current="打包")
    result = E.build_package(spec, dest, include_lines=include_lines, lookup=VOICE_LOOKUP)
    result["url"] = _export_url(spec_id, result["name"])
    return result


@router.post("/{spec_id}/export")
def export_package(spec_id: str, req: ExportRequest | None = None) -> dict[str, Any]:
    req = req or ExportRequest()
    spec = _load(spec_id)
    if not E.exportable(spec):
        raise HTTPException(400, E.NOTHING_TO_EXPORT)
    dest = E.export_root(spec) / E.package_name(spec)
    if dest.exists() or (dest.parent / f"{dest.name}.zip").exists():
        raise HTTPException(409, E.ALREADY_THIS_MINUTE)
    try:
        task_id = tasks.start(KINDS["export"], spec_id, lambda: _run_export(spec_id, dest, req.include_lines),
                              params={"dest": _relative(dest), "include_lines": req.include_lines}, module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id, "name": f"{dest.name}.zip"}


@router.get("/{spec_id}/export")
def list_export_packages(spec_id: str) -> dict[str, Any]:
    spec = _load(spec_id)
    return {"id": spec["id"], "packages": _exports_payload(spec), "export_dir": _relative(E.export_root(spec))}


@router.get("/{spec_id}/export/{name}")
def download_export_package(spec_id: str, name: str) -> FileResponse:
    spec = _load(spec_id)
    target = E.resolve_zip(spec, name)
    if target is None:
        raise HTTPException(404, f"没有这个交付包：{name or '(空)'}——只能下载 09_export 目录里已导出的 zip。")
    return FileResponse(target, media_type="application/zip", filename=target.name)


# ---------------------------------------------------------------------------- 音乐上传


@router.post("/{spec_id}/assets")
def upload_asset(spec_id: str, req: AssetUpload) -> dict[str, Any]:
    spec = _load(spec_id)
    if req.slot not in S.MUSIC_SLOTS:
        raise HTTPException(400, f"slot 只能是 {' / '.join(S.MUSIC_SLOTS)}，收到「{req.slot}」")
    try:
        data = base64.b64decode(req.data_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, f"文件数据不是合法的 base64：{exc}") from exc
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
    # 按内容认（uploads.accept_audio）：以前先按扩展名拒、再只探时长——.aiff 叫对了名字也被拒，视频 / 图片改成 .mp3
    # 探得出「时长」就收下，合成时片头静默消失 / 垫乐硬失败。现在要 ffprobe 读得出音频流且时长 > 0；容器 / 编码不在范围内的
    # （AIFF、webm 录音、带画面的视频…）只取音轨转成 wav / m4a 收（转出来也不超过上传上限，超了 413），后缀跟着真实格式走。
    # 测试注入的 PROBER 顶替 ffprobe 只量时长
    try:
        accepted = accept_audio(data, req.filename, allowed=S.AUDIO_SUFFIXES, max_bytes=MAX_UPLOAD_BYTES, duration_probe=PROBER)
    except UploadContentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    stem = slugify(Path(req.filename).stem, fallback=req.slot)
    digest = hashlib.sha256(data).hexdigest()[:8]
    target_dir = _root() / S.ASSETS_DIR / spec_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{stem}-{digest}{accepted.suffix}"
    if not target.exists():
        # 先写临时名再改名落位：写到一半被打断（关窗口、磁盘满）不会在正式名下留半截文件
        staging = target_dir / f".{stem}-{digest}.uploading{accepted.suffix}"
        staging.write_bytes(accepted.data)
        try:
            staging.replace(target)
        finally:
            staging.unlink(missing_ok=True)
    relative = target.relative_to(_root()).as_posix()
    raw = S.to_disk(spec)
    raw.setdefault("music", {})[req.slot] = relative
    spec = _write(raw, spec_id=spec_id)
    return {"slot": req.slot, "file": relative, "bytes": len(accepted.data), "music": spec["music"]}


# ---------------------------------------------------------------------------- 产物流


def _safe_name(name: str) -> str:
    if not name or "/" in name or "\\" in name or name.startswith(".") or name != Path(name).name:
        raise HTTPException(404, "没有这个文件")
    return name


@router.get("/{spec_id}/artifact/audio/{name}")
def artifact_audio(spec_id: str, name: str) -> FileResponse:
    spec = _load(spec_id)
    name = _safe_name(name)
    if not name.lower().endswith(".mp3"):
        raise HTTPException(404, "没有这个文件")
    mix_path = S.mix_dir(spec) / f"{spec['id']}.mp3"
    if name == mix_path.name and mix_path.is_file():
        path = mix_path
    else:
        known = {str(e.get("file")) for e in A.load_manifest(spec).get("lines") or () if isinstance(e, Mapping) and e.get("file")}
        if name not in known:
            raise HTTPException(404, "没有这个文件")
        path = S.audio_dir(spec) / name
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, "没有这个文件")
    if path.stat().st_size == 0:
        # 合成到一半留下的空文件：以前照样 200 流出 0 字节的 audio/mpeg
        raise HTTPException(404, "这个音频文件是空的（合成没写完整）——再点一次「合成整期」会重合成它")
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"))


@router.get("/{spec_id}/artifact/text/{name}")
def artifact_text(spec_id: str, name: str) -> PlainTextResponse:
    spec = _load(spec_id)
    name = _safe_name(name)
    if name not in TEXT_FILES:
        raise HTTPException(404, "没有这个文件")
    path = S.mix_dir(spec) / name
    if not path.is_file():
        raise HTTPException(404, "还没有这份文件——先合成一次")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type=TEXT_FILES[name] + "; charset=utf-8")


from typing import Mapping  # noqa: E402  - 只有 artifact_audio 用到，放底部免得挤上面的 import 段

__all__ = ["router", "KINDS", "DISPATCH", "SYNTHESIZER", "PROBER", "RUNNER", "FFMPEG", "VOICE_LOOKUP"]
