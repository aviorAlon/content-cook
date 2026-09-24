"""本地模型接口（DESIGN §45，契约 ``docs/local-models-spec.md`` §5、§9）：

* ``GET  /api/system/local-scan``              扫本机常见端口（``?ids=`` 只扫某几个、``?base_url=`` 手填地址）；只读
* ``POST /api/settings/local/apply``           ``{service, role, model?, base_url?}`` 把探到的服务应用到某个角色
* ``GET  /api/system/local-hint?role=``        「本机现在有没有能顶这个角色的服务」——页面加载就能问，带缓存
* ``POST /api/system/local-hint/dismiss``      ``{role | "all"}`` 关掉这条提示（存进设置文件，重启还在）
* ``POST /api/system/local-hint/reset``        重新开启本地提示（设置页那个按钮）
* ``GET  /api/system/local-workflows``         内置 + 用户目录里的 ComfyUI 工作流清单（含映射是否合法）
* ``POST /api/system/local-workflows/check``   ``{name}`` 把映射套到工作流上干跑一次，报缺失路径

**只连本机。** 地址一律过 ``genkit.local.is_local_url``，公网地址在发请求之前就 400 挡掉——
「扫描本机」不能变成一个内网/公网扫描器。提示那条连手填地址都不收，只探写死的预设。

**推荐，不强制。** ``local-hint`` 只回答「有没有」，从不拦路：没探到、角色已经是本机、用户关过提示，
一律 ``suggestion: null``，界面上那一行就不出现。

工作流那两条走 ``genkit.adapters.comfyui``（ComfyUI 适配器自己的映射加载器），**懒导入**：
适配器还没装上时不 500，返回一句人话说清楚缺什么。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Sequence

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from genkit import local as local_mod
from genkit import settings as settings_mod

from ..deps import PROJECT_ROOT

router = APIRouter(tags=["local-models"])

COMFY_MISSING = "ComfyUI 适配器还没装上（genkit/adapters/comfyui.py），工作流清单暂时空着——装上它之后会自动出现，自己导出的工作流放进 <项目根>/local_workflows/ 也算"


def _comfyui():
    """懒导入 ComfyUI 适配器；不在就 None（调用方给人话，不 500）。"""

    try:
        from genkit.adapters import comfyui
    except Exception:  # noqa: BLE001 - 还没落地 / 依赖缺失都算「没装上」
        return None
    return comfyui


# ---------------------------------------------------------------------------- 扫描


@router.get("/api/system/local-scan")
def local_scan(ids: str = "", base_url: str = "", timeout: float = 1.2) -> dict[str, Any]:
    """并发探一遍本机预设。不写任何设置。

    ``ids``：逗号分隔的服务 id（不给就全扫）。``base_url``：手填地址，套在 ``ids`` 列出的服务上
    （常见端口没响应时用；必须是本机地址）。
    """

    wanted = [x.strip() for x in str(ids or "").split(",") if x.strip()]
    address = str(base_url or "").strip()
    if address and not wanted:
        raise HTTPException(400, "手填地址要跟 ids 一起给：哪个服务在这个地址上")
    overrides = {i: address for i in wanted} if address else {}
    try:
        rows = local_mod.scan(wanted or None, timeout=max(0.1, min(float(timeout), 10.0)), base_urls=overrides)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    remember_rows(rows, full=not wanted)
    alive = [r["id"] for r in rows if r["alive"]]
    return {
        "services": rows,
        "alive": alive,
        # 每个角色在这批在跑的服务里该点名谁（设置页据此标「推荐」；理由在每行自己的 reason 里）
        "recommend": {role: sid for role in settings_mod.ROLES if (sid := local_mod.recommend(role, alive))},
        "presets": [s.as_dict() for s in local_mod.PRESETS],
        "roles": list(settings_mod.ROLES),
    }


# ---------------------------------------------------------------------------- 「本机可以跑」提示
#
# 页面加载就会问这一条，所以它必须便宜：探活结果在内存里缓存 HINT_TTL 秒（**按服务分别记时刻**），
# 窗口内一个请求都不发；没有缓存时也只快探**能顶这个角色的那几个**预设，不扫全部七个。

HINT_TTL = 60.0        # 缓存多久（秒）。够一次页面加载里的好几处提示共用一次探活
HINT_TIMEOUT = 0.5     # 快探的超时：起没起服务，半秒之内就该有答案（设置页那次正式扫描仍是 1.2 秒）

_hint_cache: dict[str, tuple[float, dict[str, Any]]] = {}   # 服务 id → (探到的时刻, 那一行)
_hint_lock = threading.Lock()
_scanned = False       # 用户在设置页点过「扫描本机」吗（只记在本进程里，不写盘）


def _clock() -> float:
    return time.monotonic()


def remember_rows(rows: list[dict[str, Any]], *, full: bool = False) -> None:
    """把一次探活的结果记进缓存（设置页的正式扫描也走这里：扫过一次，提示就不用再探）。"""

    global _scanned
    now = _clock()
    with _hint_lock:
        for row in rows:
            service_id = str(row.get("id") or "")
            if service_id:
                _hint_cache[service_id] = (now, dict(row))
        if full:
            _scanned = True


def reset_hint_cache() -> None:
    """清空缓存与「扫过没有」（测试与手动刷新用）。"""

    global _scanned
    with _hint_lock:
        _hint_cache.clear()
        _scanned = False


def cached_services(ids: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """缓存里的探活结果，**一个请求都不发**（从来没扫过就是空列表）。

    给「候选清单」这类只读用途：填个下拉不值得去连本机服务，所以这里不看 :data:`HINT_TTL`——
    宁可给一份可能过期的模型名（用户一眼认得出），也不为它引入一次会卡住接口的探活。
    """

    with _hint_lock:
        rows = {key: dict(value[1]) for key, value in _hint_cache.items()}
    keys = [str(i) for i in ids] if ids is not None else list(rows)
    return [rows[k] for k in keys if k in rows]


def _cached(ids: list[str], now: float) -> tuple[dict[str, dict[str, Any]], list[str]]:
    fresh: dict[str, dict[str, Any]] = {}
    stale: list[str] = []
    with _hint_lock:
        for service_id in ids:
            hit = _hint_cache.get(service_id)
            if hit and now - hit[0] < HINT_TTL:
                fresh[service_id] = hit[1]
            else:
                stale.append(service_id)
    return fresh, stale


def probe_for_hint(role: str, *, fetch: Any = None) -> list[dict[str, Any]]:
    """能顶这个角色的那几个预设，按推荐顺序返回它们最近一次的探活结果（缓存过期才重探）。"""

    services = local_mod.presets_for_role(role)
    if not services:
        return []
    ids = [s.id for s in services]
    rows, stale = _cached(ids, _clock())
    if stale:
        probed = local_mod.scan(stale, timeout=HINT_TIMEOUT, fetch=fetch)
        remember_rows(probed)
        rows.update({str(r["id"]): r for r in probed})
    return [rows[i] for i in ids if i in rows]


def hint_payload(role: str, *, fetch: Any = None, settings: settings_mod.Settings | None = None) -> dict[str, Any]:
    """``GET /api/system/local-hint`` 的内容。``suggestion`` 是 null 就代表「这里不该出现任何提示」。"""

    # 只读查询：设置文件坏了按空配置算（设置页会给出真实错误），不让「本机可以跑」提示把页面 500 掉
    current = settings if settings is not None else settings_mod.load_settings_or_default()[0]
    alive = [r for r in probe_for_hint(role, fetch=fetch) if r.get("alive")]
    current_is_local = settings_mod.role_is_local(current, role)
    dismissed = local_mod.hint_dismissed(current, role)
    pick = local_mod.recommend(role, [str(r["id"]) for r in alive])
    suggestion = None
    if pick and not current_is_local and not dismissed:
        suggestion = {"service": pick, "name": local_mod.preset(pick).name, "reason": local_mod.reason_for(pick)}
    return {
        "role": role,
        "available": [{"service": r["id"], "name": r["name"], "base_url": r["base_url"],
                       "models": list(r.get("models") or []), "roles": list(r.get("roles") or [])} for r in alive],
        "current_is_local": current_is_local,
        "suggestion": suggestion,
        "dismissed": dismissed,
        # 用户还从来没点过「扫描本机」：引导里那一行可以说「有显卡的话可以本地跑」，但不能说「本机有 X 在跑」
        "scanned": _scanned,
    }


@router.get("/api/system/local-hint")
def local_hint(role: str = "image") -> dict[str, Any]:
    name = str(role or "").strip()
    if name not in settings_mod.ROLES:
        raise HTTPException(400, f"未知角色：{name}")
    return hint_payload(name)


class HintDismiss(BaseModel):
    role: str = "all"


@router.post("/api/system/local-hint/dismiss")
def dismiss_local_hint(req: HintDismiss) -> dict[str, Any]:
    """关掉这条提示：写进设置文件，重启也不会回来。设置页的「重新开启本地提示」是唯一的复位入口。"""

    name = str(req.role or "all").strip()
    if name != "all" and name not in settings_mod.ROLES:
        raise HTTPException(400, f"未知角色：{name}")
    # 写盘路径一律从文件层起、整段持锁（editing_settings）：关一个提示不能把环境变量里的密钥顺手写进文件，
    # 跟设置页的「保存」同时到也不能互相覆盖
    with settings_mod.editing_settings() as current:
        entries = ["all"] if name == "all" else [*current.local_hints_dismissed, name]
        current.local_hints_dismissed = settings_mod.normalize_hint_dismissals(entries)
    return {"dismissed": current.local_hints_dismissed}


@router.post("/api/system/local-hint/reset")
def reset_local_hint() -> dict[str, Any]:
    with settings_mod.editing_settings() as current:
        current.local_hints_dismissed = []
    return {"dismissed": []}


# ---------------------------------------------------------------------------- 应用到角色


class LocalApply(BaseModel):
    service: str
    role: str
    model: str = ""
    base_url: str | None = None


@router.post("/api/settings/local/apply")
def apply_local(req: LocalApply) -> dict[str, Any]:
    """把某个本机服务应用到某个角色：写 adapter / base_url / model，api_key 留空（本机不要 key）。"""

    # 被 400 拒掉（不认识的服务 / 角色）就不写盘：editing_settings 的 with 块里抛异常不存，坏文件也原样留着
    with settings_mod.editing_settings() as current:
        try:
            local_mod.apply_to_role(current, req.service, req.role, req.model, str(req.base_url or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    current = settings_mod.load_settings()      # 回给界面的是生效配置（含环境变量覆盖）
    return {
        "changed": [req.role],
        "settings": current.as_dict(mask_secrets=True),
        "status": {role: settings_mod.validate_role(current, role) for role in settings_mod.ROLES},
    }


# ---------------------------------------------------------------------------- ComfyUI 工作流


@router.get("/api/system/local-workflows")
def local_workflows() -> dict[str, Any]:
    """内置（``skills/local-models/workflows/``）+ 用户目录（``<项目根>/local_workflows/``）的工作流清单。"""

    comfyui = _comfyui()
    if comfyui is None:
        return {"workflows": [], "user_dir": str(PROJECT_ROOT / "local_workflows"), "note": COMFY_MISSING}
    try:
        rows = comfyui.list_workflows(PROJECT_ROOT)
    except Exception as exc:  # noqa: BLE001 - 工作流目录坏了不该让设置页打不开
        return {"workflows": [], "user_dir": str(comfyui.user_dir(PROJECT_ROOT)), "note": f"读工作流失败：{exc}"[:300]}
    return {"workflows": rows, "user_dir": str(comfyui.user_dir(PROJECT_ROOT)), "note": ""}


class WorkflowCheck(BaseModel):
    name: str


@router.post("/api/system/local-workflows/check")
def check_workflow(req: WorkflowCheck) -> dict[str, Any]:
    """把映射套到工作流上做一次干跑：哪个键指的路径在工作流里找不到。不提交任何任务。"""

    name = str(req.name or "").strip()
    if not name:
        raise HTTPException(400, "先选一份工作流")
    comfyui = _comfyui()
    if comfyui is None:
        raise HTTPException(425, COMFY_MISSING)
    try:
        return comfyui.check_mapping(name, PROJECT_ROOT)
    except Exception as exc:  # noqa: BLE001 - 干跑失败也要给人话
        raise HTTPException(400, f"检查映射失败：{exc}"[:300]) from exc
