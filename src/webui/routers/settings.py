"""「模型设置」接口：读（密钥打码）、改（合并式）、测试连接。密钥只落在本机设置文件，永不回传明文。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from genkit import plugins
from genkit import settings as settings_mod
from genkit.router import test_role

from ..i18n import tr

router = APIRouter(tags=["settings"])


@router.get("/api/settings")
def get_settings() -> dict[str, Any]:
    # 设置文件坏了（手改成非法 JSON、带 BOM 之类）也要 200：退回空配置并带上 warning，设置页照常渲染、
    # 用户保存一次就能在程序里修好（PUT 会备份坏文件再重建）；500 只会让人去翻文件
    current, warning = settings_mod.load_settings_or_default()
    return {
        "settings": current.as_dict(mask_secrets=True),
        # 内置来源 + 已加载插件登记的来源（插件的那几条多带一个 "plugin"）
        "adapters": settings_mod.adapter_presets(),
        "roles": list(settings_mod.ROLES),
        "path": str(settings_mod.settings_path()),
        "status": {role: settings_mod.validate_role(current, role) for role in settings_mod.ROLES},
        # 选了、但现在不在表里的来源（提供它的插件没装上）：界面照样显示成选中的「X（未加载）」，保存时原样发回，不会被清掉
        "unavailable": unavailable_roles(current),
        "plugins": [record.public() for record in plugins.records()],
        "plugin_dir": str(plugins.default_dir()),
        "warning": tr(warning),
    }


def unavailable_roles(current: settings_mod.Settings) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for role in settings_mod.ROLES:
        message = settings_mod.adapter_unavailable(current, role)
        if message:
            out[role] = {"adapter": current.role(role).adapter, "message": message}
    return out


def _stored_adapter(role: str) -> str:
    try:
        return settings_mod.load_settings_for_update().role(role).adapter
    except Exception:  # noqa: BLE001 - 读不出来就当没存过：未知来源一律拒
        return ""


class SettingsPatch(BaseModel):
    roles: dict[str, dict[str, Any]] | None = None
    output_root: str | None = None
    cache_dir: str | None = None
    update_url: str | None = None
    gateway: dict[str, Any] | None = None


@router.put("/api/settings")
def put_settings(patch: SettingsPatch) -> dict[str, Any]:
    for role, values in (patch.roles or {}).items():
        if role not in settings_mod.ROLES:
            raise HTTPException(400, f"未知角色：{role}")
        adapter = values.get("adapter")
        if adapter and settings_mod.adapter_spec(role, adapter) is None and adapter != _stored_adapter(role):
            # 不在表里、也不是原来存着的那个：不让选一个不存在的来源。原来存着的（插件暂时没装上）原样放行——
            # 改个模型名、存一下别的字段都不该把它清掉
            label = settings_mod.ROLE_LABELS.get(role, role)
            raise HTTPException(400, f"「{label}」没有来源 {adapter}：它不是内置来源，提供它的插件也没加载——没有保存")
    current = settings_mod.update_settings(patch.model_dump(exclude_none=True))
    return {
        "settings": current.as_dict(mask_secrets=True),
        "status": {role: settings_mod.validate_role(current, role) for role in settings_mod.ROLES},
        "unavailable": unavailable_roles(current),
    }


@router.post("/api/settings/test/{role}")
def test_settings_role(role: str) -> dict[str, Any]:
    if role not in settings_mod.ROLES:
        raise HTTPException(404, f"未知角色：{role}")
    try:
        message = test_role(role)
    except Exception as exc:  # noqa: BLE001 - 测试连接就是要把任何失败原样告诉用户
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"[:600]}
    return {"ok": True, "message": message}


# ---------------------------------------------------------------------------- 统一网关


@router.post("/api/settings/gateway/test")
def test_gateway() -> dict[str, Any]:
    """GET {网关}/models：通不通、有几个模型。不写任何东西。"""

    from genkit import model_catalog

    current, _ = settings_mod.load_settings_or_default()
    if not current.gateway.configured:
        return {"ok": False, "message": "先填网关地址并保存"}
    try:
        models = model_catalog.fetch_openai_models(current.gateway.base_url, current.gateway.api_key)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"[:600]}
    return {"ok": True, "message": f"通了，网关列出 {len(models)} 个模型", "count": len(models)}


class GatewayApply(BaseModel):
    roles: dict[str, dict[str, Any]]      # {"text": {"model": "..."}, "voice": {"model": "...", "voice": "..."}, ...}


@router.post("/api/settings/gateway/apply")
def apply_gateway(req: GatewayApply) -> dict[str, Any]:
    """把网关一键应用到给出的角色（adapter 换成 OpenAI 兼容档，地址密钥用网关的）。"""

    # 先校验请求再碰文件；写盘基底取文件层（editing_settings）：环境变量覆盖不能借这次保存落盘，
    # 几个保存同时到时整段串行；被 400 拒掉就不写盘、坏文件原样留着。写完再读一次生效配置回给界面
    for role in req.roles:
        if role not in settings_mod.ROLES:
            raise HTTPException(400, f"未知角色：{role}")
    with settings_mod.editing_settings() as current:
        try:
            changed = settings_mod.apply_gateway(current, req.roles)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    current = settings_mod.load_settings()
    return {
        "changed": changed,
        "settings": current.as_dict(mask_secrets=True),
        "status": {role: settings_mod.validate_role(current, role) for role in settings_mod.ROLES},
    }
