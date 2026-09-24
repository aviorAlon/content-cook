"""可选组件与更新：台词识别闸（faster-whisper）的安装与模型预下载，版本更新检查。

都是"帮用户少跑一趟命令行"的接口，不是核心流水线。安装走后台任务（pip 要几分钟）。
打包版没有 pip（sys.executable 是 exe），也不认 PYTHONPATH（冻结解释器是 isolated 模式）：借本机同一
major.minor 的 Python 把包装进外部 site 目录（genkit.paths.extra_site_dir），装完当场接进 sys.path，
下次启动由启动器接（studio.desktop._load_extra_site）。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from genkit import model_catalog
from genkit import settings as settings_mod
from genkit.paths import activate_extra_site, extra_site_dir, extra_site_python_mismatch
from genkit.version import __version__

from .. import tasks

router = APIRouter(tags=["assist"])

ASR_MODELS = ("tiny", "base", "small", "medium", "large-v3")
DEFAULT_ASR_MODEL = "small"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _frozen() -> bool:
    return bool(getattr(sys, "_MEIPASS", None))


def python_version() -> str:
    return f"{sys.version_info[0]}.{sys.version_info[1]}"


def external_python_candidates() -> list[list[str]]:
    """本机上可能是同版本 Python 的启动方式，按优先级。Windows 有 py 启动器可以点名版本。"""

    ver = python_version()
    if sys.platform == "win32":
        return [["py", f"-{ver}"], [f"python{ver}"], ["python3"], ["python"]]
    return [[f"python{ver}"], ["python3"], ["python"]]


def external_python() -> list[str] | None:
    """本机上跟内置解释器同一 major.minor 的 Python 的启动命令；找不到返回 None。

    必须同版本：pip 装的编译扩展（ctranslate2 / numpy）按 cp3XY 打 ABI 标签，版本不对 import 时直接崩。
    还得带 pip：uv / 精简发行版装的 Python 默认没有 pip，版本对得上也装不了——这种就跳过、接着试下一个候选，
    不然「一键安装」只会报 No module named pip。"""

    want = python_version()
    for cmd in external_python_candidates():
        try:
            proc = subprocess.run([*cmd, "-c", "import sys, pip; print('%d.%d' % sys.version_info[:2])"], capture_output=True,
                                  text=True, timeout=20, check=False, creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0 and proc.stdout.strip() == want:
            return cmd
    return None


def manual_install_command(site: Path, python: list[str] | None = None) -> str:
    """给用户自己在命令行里执行的那一句（跟「一键安装」做的事一样）。

    ``python`` 是本机探到的同版本 Python（external_python）：有就照它写——scoop / uv 装的 Python 不在 py 启动器的
    登记表里，py -3.11 会报 No suitable Python runtime found；没探到才写官方安装包会登记的 py -X.Y。"""

    if python:
        launcher = subprocess.list2cmdline(python) if sys.platform == "win32" else " ".join(python)
    else:
        launcher = f"py -{python_version()}" if sys.platform == "win32" else f"python{python_version()}"
    return f'{launcher} -m pip install --target "{site}" faster-whisper'


def frozen_install_hint(site: Path) -> str:
    mismatch = extra_site_python_mismatch(site) if site.is_dir() else None
    if mismatch:
        return f"{site} 里的包是给 Python {mismatch} 装的，本程序内置的是 Python {python_version()}：删掉这个目录，用 Python {python_version()} 重装"
    return (f"桌面版没有随包带 faster-whisper（体积原因）。点「一键安装」会用本机的 Python {python_version()} 把它装进 {site}；"
            f"没有 Python {python_version()} 就先装一个，或自己执行：{manual_install_command(site, external_python())}，装完重启本程序。")


def _hf_cache_dir() -> Path:
    import os

    # 环境变量是字符串：先转成 Path 再拼 hub（以前设了 HF_HOME 就是 str / "hub" 的 TypeError）
    hub_cache = os.environ.get("HF_HUB_CACHE")
    if hub_cache:
        return Path(hub_cache)
    return Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"


def asr_model_cached(model: str) -> bool:
    """faster-whisper 的模型来自 HF hub：Systran/faster-whisper-<model>；有 snapshots 目录就算下过。"""

    folder = _hf_cache_dir() / f"models--Systran--faster-whisper-{model}"
    return (folder / "snapshots").is_dir() and any((folder / "snapshots").iterdir())


@router.get("/api/system/asr")
def asr_status() -> dict[str, Any]:
    installed = importlib.util.find_spec("faster_whisper") is not None
    site = extra_site_dir()
    return {
        "installed": installed,
        "frozen": _frozen(),
        "python": sys.executable,
        "python_version": python_version(),
        "extra_site": str(site) if _frozen() else "",
        "models": [{"name": m, "cached": asr_model_cached(m) if installed else False} for m in ASR_MODELS],
        "default_model": DEFAULT_ASR_MODEL,
        # 打包版装好了就不给提示：拼提示要去探本机的 Python（起子进程）、扫外部 site 目录，界面装好后也不再显示它
        "install_hint": (
            ("" if installed else frozen_install_hint(site)) if _frozen()
            else f"会在当前 Python（{sys.executable}）里执行 pip install faster-whisper"
        ),
    }


def run_pip_install(package: str, *, python: list[str] | None = None, target: Path | None = None) -> dict[str, Any]:
    """``python`` 缺省用当前解释器；``target`` 给了就 pip install --target 装到那个目录（打包版借外部 Python 时）。"""

    cmd = [*(python or [sys.executable]), "-m", "pip", "install", package]
    if target is not None:
        cmd += ["--upgrade", "--target", str(target)]   # 目录已存在时 pip 要 --upgrade 才肯覆盖
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=False, creationflags=_NO_WINDOW)
    tail = (proc.stdout + "\n" + proc.stderr)[-4000:]
    if proc.returncode != 0:
        raise RuntimeError(f"pip 退出码 {proc.returncode}：{tail}")
    return {"package": package, "log": tail, "target": str(target) if target else ""}


def install_asr_into_extra_site(python: list[str], site: Path) -> dict[str, Any]:
    """打包版：借外部 Python 装进外部 site 目录，装完当场接进 sys.path（不用重启就能过闸）。"""

    site.mkdir(parents=True, exist_ok=True)
    result = run_pip_install("faster-whisper", python=python, target=site)
    result["activated"] = activate_extra_site() is not None
    result["installed"] = importlib.util.find_spec("faster_whisper") is not None
    return result


@router.post("/api/system/asr/install")
def install_asr() -> dict[str, Any]:
    if importlib.util.find_spec("faster_whisper") is not None:
        return {"task_id": None, "note": "已经装好了"}
    if _frozen():
        site = extra_site_dir()
        if site.is_dir() and extra_site_python_mismatch(site):
            raise HTTPException(425, frozen_install_hint(site))
        python = external_python()
        if python is None:
            raise HTTPException(425, f"本机没找到 Python {python_version()}（打包版借它来装）：先装一个 Python {python_version()}，"
                                     f"或自己执行：{manual_install_command(site)}，装完重启本程序。")
        fn = lambda: install_asr_into_extra_site(python, site)  # noqa: E731
    else:
        fn = lambda: run_pip_install("faster-whisper")  # noqa: E731
    try:
        task_id = tasks.start("install-asr", "-", fn)
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id}


class ModelRequest(BaseModel):
    model: str = DEFAULT_ASR_MODEL


def download_asr_model(model: str) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    WhisperModel(model, device="cpu", compute_type="int8")   # 构造即下载（HF hub 缓存）
    return {"model": model, "cached": asr_model_cached(model)}


@router.post("/api/system/asr/model")
def download_model(req: ModelRequest) -> dict[str, Any]:
    if req.model not in ASR_MODELS:
        raise HTTPException(400, f"模型只能是 {' / '.join(ASR_MODELS)}")
    if importlib.util.find_spec("faster_whisper") is None:
        raise HTTPException(425, "先安装 faster-whisper")
    if asr_model_cached(req.model):
        return {"task_id": None, "note": f"{req.model} 已经在本机缓存里"}
    try:
        task_id = tasks.start("asr-model", req.model, lambda: download_asr_model(req.model), params={"model": req.model})
    except tasks.TaskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id}


# ---------------------------------------------------------------------------- 版本更新


def parse_version(text: str) -> tuple[int, ...]:
    parts = []
    for chunk in str(text).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def fetch_manifest(url: str, timeout: float = 8.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - 用户自己填的地址
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict) or not data.get("version"):
        raise ValueError("清单不是 {version, url, notes} 形状的 JSON")
    return data


@router.get("/api/system/update-check")
def update_check() -> dict[str, Any]:
    """按设置里的清单地址检查新版本。不自动下载、不自动安装——只告诉你有没有、去哪拿。"""

    current = settings_mod.load_settings()
    url = current.update_url.strip()
    if not url:
        return {"configured": False, "current": __version__, "update_available": False}
    try:
        manifest = fetch_manifest(url)
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "current": __version__, "update_available": False, "error": f"读取更新清单失败：{exc}"}
    latest = str(manifest.get("version"))
    return {
        "configured": True,
        "current": __version__,
        "latest": latest,
        "update_available": parse_version(latest) > parse_version(__version__),
        "url": manifest.get("url") or "",
        "notes": manifest.get("notes") or "",
    }


# ---------------------------------------------------------------------------- 模型目录


@router.get("/api/system/model-catalog")
def model_catalog_get(source: str = "openrouter") -> dict[str, Any]:
    """已缓存的模型目录（按角色分组）；没拉过时 count=0、fetched_at=None。"""

    if source not in model_catalog.SOURCES:
        raise HTTPException(404, f"不认识的目录来源：{source}")
    return model_catalog.summary(source)


class CatalogRequest(BaseModel):
    source: str = "openrouter"


@router.post("/api/system/model-catalog/refresh")
def model_catalog_refresh(req: CatalogRequest) -> dict[str, Any]:
    """从服务商拉一次模型列表并缓存。OpenRouter 的列表是公开的，没配密钥也能拉。"""

    if req.source not in model_catalog.SOURCES:
        raise HTTPException(404, f"不认识的目录来源：{req.source}")
    try:
        model_catalog.refresh(req.source)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"拉取 {model_catalog.SOURCES[req.source]['label']} 模型列表失败：{exc}") from exc
    return model_catalog.summary(req.source)
