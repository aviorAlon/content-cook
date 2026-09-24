"""打包版的台词识别闸（PKG-1）：faster-whisper 不随包，冻结解释器又忽略 PYTHONPATH、exe 里没有 pip——
可行的路是借本机同版本 Python 装进外部 site 目录（genkit.paths.extra_site_dir），启动器起服务前接进 sys.path，
设置页「一键安装」在打包版也走这条路。界面上给的提示必须是照做就能成的那句。"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import paths
from webui import deps, tasks
from webui.app import app
from webui.routers import assist, post as post_router

client = TestClient(app)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "home" / "settings.json"))
    monkeypatch.delenv("STORYSTUDIO_EXTRA_SITE", raising=False)
    before = list(sys.path)
    yield tmp_path / "home"
    sys.path[:] = before      # activate_extra_site 会追加 sys.path，别漏到别的测试


@pytest.fixture
def frozen(monkeypatch):
    """装成打包版：sys._MEIPASS 存在，且当前进程里找不到 faster_whisper（开发机上可能真装了）。"""

    monkeypatch.setattr(sys, "_MEIPASS", str(ROOT), raising=False)
    real = importlib.util.find_spec

    def fake(name, *args, **kwargs):
        if name == "faster_whisper":
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake)
    yield


def test_extra_site_dir_lives_under_home(home, monkeypatch):
    assert paths.extra_site_dir() == home / "site-packages"
    monkeypatch.setenv("STORYSTUDIO_EXTRA_SITE", str(home / "elsewhere"))
    assert paths.extra_site_dir() == home / "elsewhere"


def test_activate_extra_site_makes_packages_importable(home):
    site = home / "site-packages"
    (site / "fake_whisper_probe").mkdir(parents=True)
    (site / "fake_whisper_probe" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert importlib.util.find_spec("fake_whisper_probe") is None
    assert paths.activate_extra_site() == site
    assert str(site) in sys.path and sys.path.index(str(site)) > 0, "追加在末尾：包内的 pydantic / fastapi 仍优先"
    assert importlib.util.find_spec("fake_whisper_probe") is not None
    assert paths.activate_extra_site() == site and sys.path.count(str(site)) == 1   # 幂等


def test_activate_extra_site_refuses_packages_built_for_another_python(home):
    """装错版本的编译扩展 import 时直接崩（DLL 加载失败），目录里有 cp3XY 标签对不上就不接、并说清楚是哪个版本装的。"""

    site = home / "site-packages"
    (site / "ctranslate2").mkdir(parents=True)
    (site / "ctranslate2" / "_ext.cp399-win_amd64.pyd").write_bytes(b"")
    assert paths.extra_site_python_mismatch(site) == "3.99"
    assert paths.activate_extra_site() is None and str(site) not in sys.path
    assert paths.extra_site_dir().is_dir()
    (home / "empty").mkdir()
    assert paths.extra_site_python_mismatch(home / "empty") is None   # 空目录 / 没有编译扩展：看不出来就不拦


@pytest.mark.parametrize("name", ["_ext.cpython-399-darwin.so", "_multiarray_umath.cpython-399-x86_64-linux-gnu.so"])
def test_mismatch_check_reads_macos_and_linux_extension_names(home, name):
    """macOS / Linux 的编译扩展叫 *.cpython-3XY-<平台>.so（spec 也打 darwin 的 .app）：一样认出是哪个 Python 装的、拒绝接；
    同版本的、abi3 稳定 ABI 的不拦。"""

    site = home / "site-packages"
    (site / "numpy" / "_core").mkdir(parents=True)
    (site / "numpy" / "_core" / name).write_bytes(b"")
    assert paths.extra_site_python_mismatch(site) == "3.99"
    assert paths.activate_extra_site() is None and str(site) not in sys.path
    ok = home / "ok"
    (ok / "pkg").mkdir(parents=True)
    (ok / "pkg" / f"_ext.cpython-{sys.version_info[0]}{sys.version_info[1]}-darwin.so").write_bytes(b"")
    (ok / "pkg" / "_stable.abi3.so").write_bytes(b"")
    assert paths.extra_site_python_mismatch(ok) is None


def test_frozen_asr_status_hint_is_actionable(home, frozen):
    """打包版的 install_hint 要能照做：写明借哪个版本的 Python、装进哪个目录、手动执行哪条命令；不再提 PYTHONPATH。"""

    r = client.get("/api/system/asr").json()
    assert r["frozen"] is True and r["installed"] is False
    site = str(home / "site-packages")
    assert r["extra_site"] == site and r["python_version"] == assist.python_version()
    hint = r["install_hint"]
    assert site in hint and "pip install --target" in hint and f"Python {assist.python_version()}" in hint, hint
    assert "PYTHONPATH" not in hint
    # 英文界面同一句也翻干净
    en = client.get("/api/system/asr", headers={"X-StoryStudio-Lang": "en"}).json()["install_hint"]
    assert not re.search(r"[一-鿿]", en) and site in en, en
    # 目录里的包是别的 Python 装的：提示改成「删掉重装」
    (home / "site-packages" / "numpy").mkdir(parents=True)
    (home / "site-packages" / "numpy" / "core.cp399-win_amd64.pyd").write_bytes(b"")
    hint2 = client.get("/api/system/asr").json()["install_hint"]
    assert "3.99" in hint2 and "删掉这个目录" in hint2, hint2
    assert client.post("/api/system/asr/install").status_code == 425


def test_frozen_install_without_matching_python_says_what_to_do(home, frozen, monkeypatch):
    monkeypatch.setattr(assist, "external_python", lambda: None)
    r = client.post("/api/system/asr/install")
    assert r.status_code == 425
    assert assist.manual_install_command(home / "site-packages") in r.json()["detail"], r.json()["detail"]


def test_frozen_install_borrows_external_python_and_activates(home, frozen, monkeypatch):
    """打包版「一键安装」：借外部 Python 跑 pip install --target <外部 site 目录>，装完当场接进 sys.path。"""

    calls: list[dict] = []

    def fake_pip(package, *, python=None, target=None):
        calls.append({"package": package, "python": python, "target": target})
        (target / "faster_whisper").mkdir(parents=True, exist_ok=True)
        (target / "faster_whisper" / "__init__.py").write_text("", encoding="utf-8")
        return {"package": package, "log": "ok", "target": str(target)}

    monkeypatch.setattr(assist, "external_python", lambda: ["py", "-3.11"])
    monkeypatch.setattr(assist, "run_pip_install", fake_pip)
    monkeypatch.setattr(deps, "PROJECT_ROOT", home.parent)
    tasks._running.clear()
    task_id = client.post("/api/system/asr/install").json()["task_id"]
    for _ in range(200):
        if (tasks.get(task_id) or {}).get("status") in ("succeeded", "failed"):
            break
        time.sleep(0.02)
    record = tasks.get(task_id)
    assert record["status"] == "succeeded", record.get("error")
    assert calls == [{"package": "faster-whisper", "python": ["py", "-3.11"], "target": home / "site-packages"}]
    assert record["result"]["activated"] is True and str(home / "site-packages") in sys.path


def test_external_python_needs_same_version_and_pip(monkeypatch):
    """同版本还得带 pip（uv 装的 Python 默认没有）；探不到的候选（py 启动器里没登记这个版本）接着试下一个。"""

    ver = assist.python_version()
    answers = {
        "py": (103, ""),                  # py -3.11：No suitable Python runtime found
        f"python{ver}": FileNotFoundError,
        "python3": (1, ""),               # 版本对但 import pip 失败
        "python": (0, ver + "\n"),
    }

    class _Proc:
        def __init__(self, rc, out):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def fake_run(cmd, **kwargs):
        assert "import sys, pip" in cmd[-1], cmd
        answer = answers[cmd[0]]
        if answer is FileNotFoundError:
            raise FileNotFoundError(cmd[0])
        return _Proc(*answer)

    monkeypatch.setattr(assist.subprocess, "run", fake_run)
    monkeypatch.setattr(assist, "external_python_candidates", lambda: [["py", f"-{ver}"], [f"python{ver}"], ["python3"], ["python"]])
    assert assist.external_python() == ["python"]
    answers["python"] = (0, "3.99\n")
    assert assist.external_python() is None


def test_manual_install_command_uses_the_python_that_was_found(tmp_path):
    """手动那一句要能照抄：探到了哪个 Python 就写哪个（scoop / uv 的 Python 不在 py 启动器里，py -3.11 会失败）。"""

    cmd = assist.manual_install_command(tmp_path, ["python"])
    assert cmd == f'python -m pip install --target "{tmp_path}" faster-whisper'
    assert assist.manual_install_command(tmp_path).endswith(f'-m pip install --target "{tmp_path}" faster-whisper')


def test_run_pip_install_targets_directory_when_borrowing(monkeypatch, tmp_path):
    seen = {}

    class _Proc:
        returncode, stdout, stderr = 0, "done", ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(assist.subprocess, "run", fake_run)
    assist.run_pip_install("faster-whisper", python=["py", "-3.11"], target=tmp_path)
    assert seen["cmd"] == ["py", "-3.11", "-m", "pip", "install", "faster-whisper", "--upgrade", "--target", str(tmp_path)]
    assist.run_pip_install("faster-whisper")
    assert seen["cmd"] == [sys.executable, "-m", "pip", "install", "faster-whisper"]


def test_frozen_asr_qc_gate_uses_the_same_hint(home, frozen, tmp_path, monkeypatch):
    """asr-qc 的 425 在打包版也要给能照做的话（以前写的「在运行本程序的 Python 里 pip install」对 exe 无意义）。"""

    from webui.routers import episodes as episodes_router
    from webui.routers import project as project_router

    content_dir = tmp_path / "content" / "story_video"
    content_dir.mkdir(parents=True)
    for module in (deps, episodes_router, post_router, project_router):
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deps, "STORYVIDEO_DIR", content_dir)
    monkeypatch.setattr(episodes_router, "STORYVIDEO_DIR", content_dir)
    spec = {
        "id": "ASR-T", "output_root": str(tmp_path / "output" / "ASR-T"), "prompt_language": "zh",
        "frame_style": "x", "scene_anchor": "y", "camera": "z", "neg": "n", "subtitle": {"language": "zh"},
        "cast": {"a": {"name": "阿一", "block": "黑发。"}},
        "shots": [{"id": "S1", "dur": 4, "cast": ["a"], "shot": "近景", "art": "a", "motion": "m", "lines": [{"who": "a", "zh": "你好"}]}],
    }
    (content_dir / "ASR-T.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    r = client.post("/api/episodes/storyvideo/ASR-T/asr-qc", json={})
    assert r.status_code == 425
    assert r.json()["detail"] == assist.frozen_install_hint(home / "site-packages")


def test_desktop_launcher_loads_extra_site_before_serving(home, monkeypatch):
    from studio import desktop

    site = home / "site-packages"
    (site / "fake_whisper_probe2").mkdir(parents=True)
    (site / "fake_whisper_probe2" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(desktop, "is_frozen", lambda: True)
    desktop._load_extra_site()
    assert str(site) in sys.path
    source = (ROOT / "src" / "studio" / "desktop.py").read_text(encoding="utf-8")
    assert source.index("_load_extra_site()") < source.index("threading.Thread(target=_serve"), "要在服务线程 import webui 之前接上"
    # 开发态不接：源码方式跑有 pip，直接装进当前 Python
    sys.path.remove(str(site))
    monkeypatch.setattr(desktop, "is_frozen", lambda: False)
    desktop._load_extra_site()
    assert str(site) not in sys.path
