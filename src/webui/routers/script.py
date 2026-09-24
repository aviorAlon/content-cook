"""剧本工作台接口（契约 docs/scriptlab-spec.md §8）。

* ``GET  /api/script``                        列表（id / 标题 / 目标形态 / 角色数 / 段数 / 版本数 / 采用的是哪一版 / 分钟 / 投放几处）
* ``GET  /api/script/next-id``                自动编号（SC-）
* ``GET  /api/script/targets``                可投放 / 可导入的项目：播客期 + 短剧集（带落点与角色，给前端下拉）
* ``GET  /api/script/options``                目标形态清单 / cue / 台词种类 / 写法约束 / 版本来源名 / 限额 / 示例
* ``POST /api/script``                        新建（不填 id 由服务端编号）
* ``POST /api/script/examples/create``        复制示例
* ``GET/PUT/DELETE /api/script/{id}``         详情 / 整体保存（校验不过 400）/ 归档到 .webui_deleted/script/
* ``POST /api/script/{id}/generate``          ``{note?, from_version?, blocks?}`` 起草或只重写几段（后台任务 sc-draft，1 次文本模型请求）
* ``POST /api/script/{id}/review``            ``{version?}`` 按清单审稿（后台任务 sc-review，1 次文本模型请求）；``GET`` 同路径拿上次结果
* ``POST /api/script/{id}/pick``              ``{version}`` 采用某一版
* ``POST /api/script/{id}/versions``          ``{label?, from_version?, blocks}`` 手写另存为新版本（source=hand）
* ``DELETE /api/script/{id}/versions/{vid}``  删掉一版（不能删采用中的那一版，不能删最后一版）
* ``POST /api/script/{id}/use``               ``{module, target_id, blocks, mapping, mode?}`` 投放（§7）
* ``POST /api/script/{id}/import``            ``{module, target_id, blocks?}`` 从目标项目导入现有台词当新的一版（§7）

所有任务 ``module="script"``。这个模块不出图、不出声、不装配，所以测试注入点只有 ``DISPATCH``（文本模型），
跟音色 / ffmpeg 无关。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from genkit import local as local_mod
from genkit import trash
from genkit.ids import is_valid_id
from genkit.paths import resource as _resource
from script import craft as C
from script import lint as L
from script import review as R
from script import spec as S
from script import use as U

from .. import deps, tasks
from ..i18n import tr
from ..problems import problem_cards

router = APIRouter(prefix="/api/script", tags=["script"])

MODULE = "script"
BASE = "/api/script"
EXAMPLES_DIR = _resource("examples", "script")
EXAMPLE_DEFAULT = "after_hours_message"
KINDS = {"draft": C.TASK_KIND, "review": R.TASK_KIND}
NO_TEXT_MODEL = deps.NO_TEXT_MODEL   # 与播客 / 短剧审稿同一句（deps 里唯一一份）

# 测试注入点（None = 用真实实现）。文本模型是这个模块唯一会花钱的地方。
DISPATCH: Callable[..., Any] | None = None


# ---------------------------------------------------------------------------- 辅助


def _root() -> Path:
    return deps.PROJECT_ROOT


def _relative(path: Path) -> str:
    try:
        return path.relative_to(_root()).as_posix()
    except ValueError:
        return str(path)


def _load(spec_id: str) -> dict[str, Any]:
    try:
        return S.load(spec_id, project_root=_root())
    except S.ScriptSpecError as exc:
        raise HTTPException(404, str(exc)) from exc


def _write(body: dict[str, Any], *, spec_id: str) -> dict[str, Any]:
    try:
        normalized = S.normalize(body, project_root=_root(), spec_id=spec_id)
    except S.ScriptSpecError as exc:
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
        raise HTTPException(409, f"剧本 {spec_id} 已经存在，换一个 id")
    return spec_id


def _busy(spec_id: str) -> dict[str, str | None]:
    return {name: tasks.active_task_id(kind, spec_id) for name, kind in KINDS.items()}


def _lint(spec: dict[str, Any], version: str | None = None) -> list[str]:
    """静态检查不花钱；判据按 target 选（``script.lint``），一条也不新造。"""

    try:
        return L.lint_script(spec, version)
    except Exception:  # noqa: BLE001 - 检查炸了不能拦住详情页，稿子还得能打开改
        return []


def _review_summary(spec: dict[str, Any], version: str) -> dict[str, Any] | None:
    """某一版的审稿结论摘要（契约 §6 的并排对比要它）：``{verdicts: {violated, risk, ok}, stale}``；没审过 = None。"""

    try:
        record = R.load_review(spec, version)
        if not record:
            return None
        items = ((record.get("result") or {}).get("items") or ())
        counts = {"violated": 0, "risk": 0, "ok": 0}
        for item in items:
            verdict = str(item.get("verdict") or "")
            if verdict == "ok":
                counts["ok"] += 1
            elif verdict in counts and item.get("evidence_verified") is not False:
                counts[verdict] += 1
        return {"verdicts": counts, "stale": R.is_stale(spec, record, version)}
    except Exception:  # noqa: BLE001 - 审稿文件坏了不拦详情页
        return None


def _version_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """版本摘要（契约 §6）：字数 / 秒数 / 分钟、告警数、审稿结论，前端拿两版并排比；逐行差异在前端算。"""

    rows: list[dict[str, Any]] = []
    for version in spec.get("versions") or ():
        vid = str(version["id"])
        rows.append({
            "id": vid, "label": version.get("label") or vid, "source": version.get("source") or "hand",
            "source_label": S.SOURCE_LABELS.get(str(version.get("source") or ""), ""),
            "model": version.get("model") or "", "note": version.get("note") or "",
            "from_version": version.get("from_version") or "", "created_at": version.get("created_at"),
            "estimate": C.estimate(spec, vid), "warnings_count": len(_lint(spec, vid)),
            "review": _review_summary(spec, vid),
            "current": vid == str(spec.get("current") or ""),
        })
    return rows


def _targets_hint(spec: dict[str, Any]) -> dict[str, int]:
    """「用」页的入口提示：这台机器上有几期播客、几集短剧可以投。数不出来就按 0（首页不该因为它挂掉）。"""

    try:
        targets = U.list_targets(_root())
    except Exception:  # noqa: BLE001
        return {"podcast": 0, "drama": 0}
    return {"podcast": len(targets["podcast"]), "drama": len(targets["drama"])}


def _detail(spec: dict[str, Any]) -> dict[str, Any]:
    """契约 §8 末尾那一份。检查与估算看的是 ``pick_version``（采用的那一版；还没采用就是最新一版），
    ``current_version`` 仍然只在真的采用了之后才非空——第一版自动采用、之后的版本要人挑（契约 §3），
    整体保存时 ``current`` 也可能被清空。"""

    warnings = _lint(spec)
    try:
        warnings = warnings + R.review_problems(spec)
    except Exception:  # noqa: BLE001 - 审稿文件坏了不拦详情页
        pass
    picked = S.pick_version(spec)
    return {
        "id": spec["id"], "module": MODULE, "title": spec["title"],
        "spec": S.to_disk(spec),
        "current_version": S.current_version(spec),
        "picked_version": str(picked["id"]) if picked else "",
        "estimate": C.estimate(spec),
        "block_estimates": L.block_estimates(spec),
        "warnings": warnings,
        "problems": problem_cards(warnings),
        "versions": _version_rows(spec),
        "review": R.review_state(spec),
        "used_in": list(spec.get("used_in") or []),
        "targets_hint": _targets_hint(spec),
        "busy": _busy(spec["id"]),
        "limits": {"speakers": [S.SPEAKERS_MIN, S.SPEAKERS_MAX]},
        "path": f"content/script/{spec['id']}.json",
    }


class GenerateRequest(BaseModel):
    note: str | None = None
    from_version: str | None = None
    blocks: list[str] | None = None
    label: str | None = None


class ReviewRequest(BaseModel):
    version: str | None = None


class PickRequest(BaseModel):
    version: str


class VersionRequest(BaseModel):
    blocks: list[dict[str, Any]]
    label: str | None = None
    from_version: str | None = None


class UseRequest(BaseModel):
    module: str
    target_id: str
    blocks: dict[str, str] = {}
    mapping: dict[str, str] = {}
    mode: str | None = None
    version: str | None = None


class ImportRequest(BaseModel):
    module: str
    target_id: str
    blocks: list[str] | None = None
    label: str | None = None


class ExampleCreateRequest(BaseModel):
    name: str | None = None
    id: str | None = None


# ---------------------------------------------------------------------------- 列表 / 编号 / 目标 / 选项 / 新建


@router.get("")
def list_scripts() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec_id in S.existing_ids(_root()):
        entry: dict[str, Any] = {"id": spec_id, "module": MODULE, "pipeline": MODULE,
                                 "path": f"content/script/{spec_id}.json"}
        try:
            spec = S.load(spec_id, project_root=_root())
        except Exception as exc:  # noqa: BLE001 - 一个坏文件不能让列表挂掉
            entry.update({"status": "error", "error": str(exc)})
            rows.append(entry)
            continue
        est = C.estimate(spec)
        entry.update({
            "status": "ok", "title": spec["title"], "target": spec["target"],
            # 形态名不在中间件翻译的键里（name / target_name 也承载用户内容，不能整键翻），这里点名翻
            "target_name": tr(S.TARGETS[spec["target"]]["name"]), "language": spec["language"],
            "speakers": len(spec["speakers"]), "blocks": len(spec["blocks"]),
            "versions": len(spec["versions"]), "current": spec["current"],
            "chars": est["chars"], "lines": est["lines"], "minutes": est["minutes"],
            "used_in": len(spec["used_in"]),
        })
        rows.append(entry)
    return rows


@router.get("/next-id")
def next_id() -> dict[str, str]:
    return {"id": S.next_id(_taken_ids())}


@router.get("/targets")
def list_targets() -> dict[str, Any]:
    """可投放 / 可导入的项目（§7）：播客期带环节与主播，短剧集带场次 / 镜号 / 人物与「是否已锁定」。只读。"""

    return {"modules": list(S.USE_MODULES), **U.list_targets(_root())}


@router.get("/options")
def list_options() -> dict[str, Any]:
    """新建页与说明要的那几张清单。写法约束按**项目语言**取——短剧那条「一句不超过 N 字」数的是汉字，
    英文稿里既不进提示词也不进检查，界面上也就不该列它。"""

    examples = sorted(p.stem for p in EXAMPLES_DIR.glob("*.json")) if EXAMPLES_DIR.is_dir() else []
    return {
        # 形态名按界面语言翻（中间件只翻 label / note 这类键，name 承载用户内容不能整键翻）；英文规则与 english 字段同一套措辞
        "targets": [{**row, "name": tr(row["name"])} for row in S.targets_payload()],
        "languages": list(S.LANGUAGES),
        "cues": list(S.CUES),
        "kinds": list(S.LINE_KINDS),
        "sources": [{"id": s, "name": S.SOURCE_LABELS[s]} for s in S.SOURCES],
        "use_modules": [{"id": m, "spot": U.SPOT_LABELS[m]} for m in S.USE_MODULES],
        "modes": list(U.USE_MODES),
        "craft": {target: {lang: list(C.rules_for(target, lang)) for lang in S.LANGUAGES} for target in S.TARGETS},
        "limits": {"speakers": [S.SPEAKERS_MIN, S.SPEAKERS_MAX]},
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
    except S.ScriptSpecError as exc:
        raise HTTPException(400, f"示例没通过结构校验（这是示例文件本身的问题）：{exc}") from exc
    S.save(spec)
    return {"example": name, "path": f"content/script/{spec_id}.json", **_detail(spec)}


# ---------------------------------------------------------------------------- 读 / 写 / 删


@router.get("/{spec_id}")
def get_script(spec_id: str) -> dict[str, Any]:
    return _detail(_load(spec_id))


@router.put("/{spec_id}")
def put_script(spec_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _load(spec_id)
    if str(body.get("id") or spec_id) != spec_id:
        raise HTTPException(400, f"项目文件里的 id「{body.get('id')}」跟地址里的 {spec_id} 对不上")
    return _detail(_write(body, spec_id=spec_id))


@router.delete("/{spec_id}")
def delete_script(spec_id: str) -> dict[str, Any]:
    """归档（回收站里能恢复）。有任务在跑（起草 / 审稿）、或有文件被占用时 409，什么都不动。"""

    if is_valid_id(spec_id) and any(_busy(spec_id).values()):
        raise HTTPException(409, trash.TASKS_RUNNING)
    try:
        result = S.archive(spec_id, project_root=_root())
    except S.ScriptSpecError as exc:
        raise HTTPException(404, str(exc)) from exc
    except trash.FilesBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"archived_to": _relative(Path(result["archived_to"])), "output_moved": result["output_moved"],
            "assets_moved": result["assets_moved"]}


# ---------------------------------------------------------------------------- 起草 / 重写


def _text_model_ready() -> bool:
    if DISPATCH is not None:
        return True
    # 不能写 bool(scriptgen_backend())：没配时它返回 "unconfigured"（非空），400 闸会永远不生效（2026-09-22 审计 SC-1）
    from storyvideo.scriptgen import text_model_available

    return text_model_available()


def _draft_cost_note() -> str:
    return local_mod.cost_note_for("text", 1, C.REMOTE_COST_NOTE)


def _review_cost_note() -> str:
    return local_mod.cost_note_for("text", 1, R.REMOTE_COST_NOTE)


def _run_generate(spec_id: str, note: str, blocks: list[str] | None, from_version: str, label: str) -> dict[str, Any]:
    spec = S.load(spec_id, project_root=_root())
    fresh = C.run_generate(spec, note=note, blocks=blocks, from_version=from_version or None,
                           label=label, dispatch=DISPATCH)
    version = fresh["versions"][-1]
    est = C.estimate(fresh, str(version["id"]))
    return {"version": str(version["id"]), "label": version["label"], "source": version["source"],
            "blocks": est["blocks"], "lines": est["lines"], "minutes": est["minutes"],
            "warnings": len(_lint(fresh, str(version["id"])))}


@router.post("/{spec_id}/generate")
def generate(spec_id: str, req: GenerateRequest | None = None) -> dict[str, Any]:
    """不给 ``blocks`` = 出一整版新的；给了 = 只重写这几段，其余段从 ``from_version``（不给就是采用的那一版）
    原样抄过来。新版本追加在末尾；项目还没采用任何一版时自动采用，否则**不自动顶替**（``adopt_if_first``，契约 §3）。
    发 1 次文本模型请求；没配文本模型 400，在跑 409。"""

    req = req or GenerateRequest()
    spec = _load(spec_id)
    wanted = [str(b).strip() for b in (req.blocks or ()) if str(b).strip()]
    if not _text_model_ready():
        raise HTTPException(400, NO_TEXT_MODEL)
    from_version = str(req.from_version or "").strip()
    # 三道拦截（角色表空 / 骨架空 / 只重写却没有垫底的版本）都在 craft.build_prompt 里，这里先跑一遍：
    # 拦下来就一次模型请求都不发，也不会留一个失败的任务记录让人以为花了钱
    try:
        if from_version:
            S.pick_version(spec, from_version)
        C.build_prompt(spec, note=(req.note or "").strip(), blocks=wanted or None, from_version=from_version or None)
    except S.ScriptSpecError as exc:
        raise HTTPException(404, str(exc)) from exc
    except C.ScriptLabError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        task_id = tasks.start(KINDS["draft"], spec_id,
                              lambda: _run_generate(spec_id, (req.note or "").strip(), wanted or None,
                                                    from_version, (req.label or "").strip()),
                              params={"note": (req.note or "").strip(), "blocks": wanted,
                                      "from_version": from_version}, module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id, "cost_note": _draft_cost_note()}


# ---------------------------------------------------------------------------- 审稿


def _run_review(spec_id: str, version: str) -> dict[str, Any]:
    spec = S.load(spec_id, project_root=_root())
    record = R.run_review(spec, version=version or None, dispatch=DISPATCH)
    items = record["result"]["items"]
    return {
        "version": record["version"], "checklist_id": record["checklist_id"],
        "checklist_name": record["checklist_name"], "created_at": record["created_at"],
        "verification": record["verification"],
        "counts": {
            "violated": sum(1 for it in items if it["verdict"] == "violated"),
            "risk": sum(1 for it in items if it["verdict"] == "risk" and it.get("evidence_verified")),
            "ok": sum(1 for it in items if it["verdict"] == "ok"),
            "unverified": sum(1 for it in items if it.get("evidence_verified") is False),
        },
    }


@router.post("/{spec_id}/review")
def review(spec_id: str, req: ReviewRequest | None = None) -> dict[str, Any]:
    """按清单审这一版（``target=podcast`` 用真人感清单，其余用工艺清单）。发 1 次文本模型请求；
    没有版本 / 没有台词 / 没配文本模型 / 清单读不出来都是 400，已经在审 409。不改项目文件。"""

    req = req or ReviewRequest()
    spec = _load(spec_id)
    version = str(req.version or "").strip()
    try:
        picked = S.pick_version(spec, version or None)
    except S.ScriptSpecError as exc:
        raise HTTPException(404, str(exc)) from exc
    if picked is None:
        raise HTTPException(400, R.NO_VERSION_MESSAGE)
    if not R.has_lines(spec, version or None):
        raise HTTPException(400, R.NO_LINES_MESSAGE)
    if not _text_model_ready():
        raise HTTPException(400, NO_TEXT_MODEL)
    info = R.checklist_info(spec)
    if info is None:
        raise HTTPException(400, R.missing_checklist_message(spec))
    try:
        task_id = tasks.start(KINDS["review"], spec_id, lambda: _run_review(spec_id, version),
                              params={"checklist": info["id"], "version": str(picked["id"])}, module=MODULE)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id, "version": str(picked["id"]), "cost_note": _review_cost_note()}


@router.get("/{spec_id}/review")
def get_review(spec_id: str, version: str | None = None) -> dict[str, Any]:
    """上次审稿的结果 + 是否过时 + 核对过的条目变成的问题卡。没审过 ``review: null``。只读，不花钱。"""

    spec = _load(spec_id)
    wanted = str(version or "").strip()
    try:
        state = R.review_state(spec, wanted or None)
    except S.ScriptSpecError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "version": state["version"],
        "checklist": R.checklist_info(spec),
        "review": state["review"],
        "stale": state["stale"],
        "note": state["note"],
        "problems": problem_cards(state["warnings"]),
        "cost_note": _review_cost_note(),
        "task_id": tasks.active_task_id(KINDS["review"], spec_id),
    }


# ---------------------------------------------------------------------------- 采用 / 手写另存 / 删版本


@router.post("/{spec_id}/pick")
def pick(spec_id: str, req: PickRequest) -> dict[str, Any]:
    """采用某一版：投放、检查、审稿默认都看它。

    ``version`` 是必填字段，但空串能过 pydantic——空串传进 ``pick_version`` 走的是「不给版本」那条回落路径
    （回落到 ``current`` / 最新一版，一版都没有时 ``None``），采用一个没说清是哪一版的东西没有意义，直接 400。
    """

    spec = _load(spec_id)
    version = str(req.version or "").strip()
    if not version:
        raise HTTPException(400, "没说要采用哪一版——在版本列表里点一版再采用")
    try:
        picked = S.pick_version(spec, version)
    except S.ScriptSpecError as exc:
        raise HTTPException(404, str(exc)) from exc
    if picked is None:                      # 兜底：``pick_version`` 只在回落路径上回 None，这里不该发生
        raise HTTPException(404, f"没有这一版：{version}")
    spec["current"] = str(picked["id"])
    S.save(spec)
    return _detail(spec)


@router.post("/{spec_id}/versions")
def save_version(spec_id: str, req: VersionRequest) -> dict[str, Any]:
    """手写另存为新版本（``source="hand"``）：界面上改完当前版本点「另存为新版本」，从不覆盖旧版。"""

    spec = _load(spec_id)
    base = str(req.from_version or "").strip()
    if base:
        try:
            S.pick_version(spec, base)
        except S.ScriptSpecError as exc:
            raise HTTPException(404, str(exc)) from exc
    raw = S.to_disk(spec)
    versions = list(raw.get("versions") or [])
    vid = S.next_version_id(str(v.get("id") or "") for v in versions)
    versions.append({
        "id": vid, "label": (req.label or "").strip() or f"第 {len(versions) + 1} 版", "source": "hand",
        "model": "", "backend": "", "note": "", "created_at": time.time(), "from_version": base,
        "blocks": req.blocks,
    })
    raw["versions"] = versions
    S.adopt_if_first(raw)   # 第一版自动采用；之后的版本要人挑
    fresh = _write(raw, spec_id=spec_id)
    return {"version": vid, **_detail(fresh)}


@router.delete("/{spec_id}/versions/{version_id}")
def delete_version(spec_id: str, version_id: str) -> dict[str, Any]:
    """删掉一版。采用中的那一版不能删（删了就没有「采用的是哪一版」了），最后一版也不能删
    （版本留痕是这个模块存在的理由，删空了就退回到「改了就是改了」）。

    **这一版的审稿记录跟着删**：``next_version_id`` 是「现有 id 里 V 后面最大的数 + 1」，删掉最新一版之后
    新的一版会拿到同一个 id；审稿记录是按版本 id 存文件的（``manifests/review_<vid>.json``），留在盘上
    就会被一版全新的稿子当成自己的审稿结论（界面上显示「这一版的台词改过，这份审稿可能过时」，
    可它根本没审过）。
    """

    spec = _load(spec_id)
    known = S.version_by_id(spec)
    if version_id not in known:
        raise HTTPException(404, f"没有这一版：{version_id}")
    if version_id == str(spec.get("current") or ""):
        raise HTTPException(400, "不能删掉采用中的那一版——先采用另一版，再删它")
    if len(known) <= 1:
        raise HTTPException(400, "这是最后一版，删了就什么都不剩了——先另存一版，再删它")
    based_on = [vid for vid, v in known.items() if str(v.get("from_version") or "") == version_id]
    raw = S.to_disk(spec)
    raw["versions"] = [v for v in raw.get("versions") or [] if str(v.get("id")) != version_id]
    for version in raw["versions"]:
        if str(version.get("from_version") or "") == version_id:
            version["from_version"] = ""     # 基于它改的那几版留着，只是「基于哪一版」这条线断了
    review_file = S.output_root(spec) / "manifests" / R.review_filename(version_id)
    try:
        review_file.unlink(missing_ok=True)
    except OSError:
        pass                                 # 删不掉（占用 / 权限）不该让删版本本身失败，下一次审稿会覆盖它
    fresh = _write(raw, spec_id=spec_id)
    return {"deleted": version_id, "unlinked": based_on, **_detail(fresh)}


# ---------------------------------------------------------------------------- 投放 / 导入


@router.post("/{spec_id}/use")
def use(spec_id: str, req: UseRequest) -> dict[str, Any]:
    """投放（§7）：把这一版里点名的几段写进目标项目的落点，再往 ``used_in`` 记一条。

    目标项目 / 落点不存在 404；角色映射缺失、段号写错、剧集已锁定、目标项目不接受这次改动都是 400。
    选了落点但这一版没台词的段会被跳过（回传 ``skipped``，界面上说明哪几段没投），绝不拿空台词
    把目标那边原有的台词整段删掉。
    """

    spec = _load(spec_id)
    try:
        result = U.use_script(spec, module=req.module, target_id=req.target_id, blocks=req.blocks,
                              mapping=req.mapping, mode=req.mode, version=req.version, project_root=_root())
    except U.UnknownTarget as exc:
        raise HTTPException(404, str(exc)) from exc
    except S.ScriptSpecError as exc:
        raise HTTPException(404, str(exc)) from exc
    except U.ScriptUseError as exc:
        raise HTTPException(400, str(exc)) from exc
    S.save(spec)
    title = result.get("title") or result["target"]
    spots = "、".join(result["spots"])
    return {"ok": True, "module": result["module"], "target": result["target"], "target_title": title,
            "spots": result["spots"], "skipped": result["skipped"], "lines": result["lines"], "mode": result["mode"],
            "message": f"已放进《{title}》的{result['spot_label']} {spots}", "detail": _detail(spec)}


@router.post("/{spec_id}/import")
def import_lines(spec_id: str, req: ImportRequest) -> dict[str, Any]:
    """导入（§7）：把目标项目里现有的台词拿过来当新的一版（``source="import"``），
    这次开口的角色 / 主播一起补进角色表；id 撞上名字不同的已有角色就换号（``renamed: {旧 id: 新 id}``），
    并在 ``message`` 里点名，不静默归到别人名下。不改目标项目一个字。"""

    spec = _load(spec_id)
    try:
        result = U.import_script(spec, module=req.module, target_id=req.target_id, blocks=req.blocks,
                                 label=(req.label or "").strip(), project_root=_root())
    except U.UnknownTarget as exc:
        raise HTTPException(404, str(exc)) from exc
    except U.ScriptUseError as exc:
        raise HTTPException(400, str(exc)) from exc
    fresh = result["spec"]
    S.save(fresh)
    message = f"已从《{result['title']}》导入 {result['lines']} 句"
    renamed = list(result.get("renamed") or ())
    if renamed:
        # 换了号的点名说清是谁、原来的号被谁占了：不然界面上只看到角色表多了个 xiao_2，不知道它是小夏、为什么不叫 xiao。
        # 明细放在一对「」里（人名 / id 是用户内容，英文界面照抄），括号里只有 id 与名字、不夹中文词
        items = "、".join(f"{r['name']}：{r['from']} → {r['to']}（{r['from']} = {r['taken_by']}）" for r in renamed)
        message += f"；撞 id 的角色已换号：「{items}」"
    return {"ok": True, "module": result["module"], "target": result["target"], "target_title": result["title"],
            "version": result["version"], "imported": result["lines"], "blocks": result["blocks"],
            "speakers": result["speakers"], "renamed": {r["from"]: r["to"] for r in renamed},
            "message": message, "detail": _detail(fresh)}


__all__ = ["router", "KINDS", "DISPATCH", "EXAMPLE_DEFAULT"]
