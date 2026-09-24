"""设置文件跨进程写（发布前加固）：两个 StoryStudio 窗口同时保存设置以前会整份互相覆盖——进程内的 RLock 管不到另一个进程。

* 跨进程文件锁（Windows msvcrt / 其它 fcntl）包住「读 → 改 → 存」：两个进程各改各的角色，谁的改动都不丢；
  另一个进程一直占着锁，等一会儿给「等几秒再保存」（409），不一直转圈。
* 写前比对文件指纹：读完到写之间被不认锁的写入方（旧版本、手工编辑）改过，就以最新文件为底、只盖本次改到的字段。
* 设置文件读不了 / 写不进去：写盘接口给人话 500，而不是裸的 PermissionError。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genkit import settings as S
from webui.app import app

client = TestClient(app)
SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STORYSTUDIO_HOME", str(tmp_path))
    monkeypatch.setenv("STORYSTUDIO_SETTINGS", str(tmp_path / "settings.json"))
    return tmp_path


def _spawn(code: str, *args: str) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": SRC + os.pathsep + os.environ.get("PYTHONPATH", "")}
    return subprocess.Popen([sys.executable, "-c", code, *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


_SAVER = """
import sys
from genkit import settings as S
path, role, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
for i in range(n):
    S.update_settings({"roles": {role: {"model": f"{role}-{i}"}}}, path)
"""

_HOLDER = """
import sys, time
from pathlib import Path
from genkit import settings as S
path, flag, hold = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
with S._LOCK, S._process_lock(path):
    flag.write_text("held", encoding="utf-8")
    time.sleep(hold)
"""


def test_two_processes_saving_different_roles_keep_every_change(tmp_path):
    """复现：原先的进程内锁挡不住另一个进程，三个进程各改自己的角色 40 次，最后常有角色被别人的整份写回旧值。"""

    path = tmp_path / "settings.json"
    rounds = 12
    roles = ("text", "image", "video")
    procs = [_spawn(_SAVER, str(path), role, str(rounds)) for role in roles]
    for proc in procs:
        _out, err = proc.communicate(timeout=180)
        assert proc.returncode == 0, err
    final = S.load_settings(path, apply_env=False)
    assert {r: final.role(r).model for r in roles} == {r: f"{r}-{rounds - 1}" for r in roles}
    assert list(tmp_path.glob("*.tmp")) == [] and list(tmp_path.glob("settings.json.broken-*")) == []


def test_save_waits_for_another_process_then_says_busy(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    S.update_settings({"roles": {"text": {"model": "before"}}}, path)
    flag = tmp_path / "held.flag"
    holder = _spawn(_HOLDER, str(path), str(flag), "1.5")
    deadline = time.monotonic() + 30
    while not flag.exists():
        assert holder.poll() is None, holder.communicate()[1]
        assert time.monotonic() < deadline
        time.sleep(0.02)
    # 等不到：给人话（busy），文件原样
    monkeypatch.setattr(S, "_PROCESS_LOCK_WAIT", 0.2)
    with pytest.raises(S.SettingsWriteError) as info:
        S.update_settings({"roles": {"text": {"model": "blocked"}}}, path)
    assert info.value.busy and str(info.value).startswith("另一个 StoryStudio 窗口正在保存设置")
    assert S.load_settings(path, apply_env=False).role("text").model == "before"
    # 等得起：对方放锁后照常写进去
    monkeypatch.setattr(S, "_PROCESS_LOCK_WAIT", 20.0)
    started = time.monotonic()
    S.update_settings({"roles": {"text": {"model": "after"}}}, path)
    assert time.monotonic() - started > 0.3            # 真的排队等了对方
    holder.communicate(timeout=30)
    assert S.load_settings(path, apply_env=False).role("text").model == "after"


def test_foreign_write_during_an_edit_is_merged_not_overwritten(tmp_path):
    """读完到写之间文件被不认锁的写入方改了：别人改的其它角色 / 网关留着，本次改到的字段盖上去（同一栏两边都改，以本次为准）。"""

    path = tmp_path / "settings.json"
    S.update_settings({"roles": {"text": {"adapter": "openai_chat", "api_key": "sk-textKEY0000000000", "model": "a"},
                                 "image": {"adapter": "openai_images", "model": "old"}}}, path)
    with S.editing_settings(path) as current:
        current.role("image").model = "mine"
        current.role("voice").adapter = "edge_tts"
        # 旧版本 / 手工编辑在空档里改了 text 角色、网关，也改了 image 的 model
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["roles"]["text"]["model"] = "theirs"
        raw["roles"]["image"]["model"] = "theirs-too"
        raw["gateway"]["base_url"] = "http://gw.local/v1"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    got = S.load_settings(path, apply_env=False)
    assert got.role("text").model == "theirs" and got.role("text").api_key == "sk-textKEY0000000000"
    assert got.gateway.base_url == "http://gw.local/v1"
    assert got.role("image").model == "mine" and got.role("image").adapter == "openai_images"
    assert got.role("voice").adapter == "edge_tts"


def test_edit_without_foreign_writes_still_saves_the_whole_change(tmp_path):
    path = tmp_path / "settings.json"
    with S.editing_settings(path) as current:
        current.local_hints_dismissed = ["image"]
        current.role("text").model = "m"
    got = S.load_settings(path, apply_env=False)
    assert got.local_hints_dismissed == ["image"] and got.role("text").model == "m"
    assert (tmp_path / "settings.json.lock").exists()         # 锁文件留在旁边，下次复用


def test_unreadable_settings_file_gives_a_human_error_on_write_endpoints(home, monkeypatch):
    import pathlib

    path = home / "settings.json"
    S.update_settings({"roles": {"text": {"adapter": "openai_chat", "api_key": "sk-textKEY0000000000", "model": "m"}}}, path)
    original = path.read_bytes()
    real_read = pathlib.Path.read_text

    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", denied)
    for method, url, body in (("put", "/api/settings", {"roles": {"text": {"model": "x"}}}),
                              ("post", "/api/system/local-hint/dismiss", {"role": "image"}),
                              ("post", "/api/system/local-hint/reset", None)):
        r = getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)
        assert r.status_code == 500, (url, r.text)
        assert r.json()["detail"].startswith("设置文件读不了：") and "检查文件权限" in r.json()["detail"], r.text
    en = client.put("/api/settings", json={"roles": {"text": {"model": "x"}}}, headers={"X-StoryStudio-Lang": "en"})
    assert en.status_code == 500 and en.json()["detail"].startswith("Cannot read the settings file: ")
    monkeypatch.setattr(pathlib.Path, "read_text", real_read)
    assert path.read_bytes() == original


def test_unwritable_settings_gives_a_human_error_and_leaves_no_temp_file(home, monkeypatch):
    path = home / "settings.json"
    S.update_settings({"roles": {"text": {"model": "keep"}}}, path)

    def refuse(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(S.os, "replace", refuse)
    r = client.put("/api/settings", json={"roles": {"text": {"model": "new"}}})
    assert r.status_code == 500 and r.json()["detail"] == (
        f"设置文件写不进去：{path}：没有写权限，或文件正被别的程序占着——检查设置目录的权限、磁盘空间，或是不是被别的程序（杀毒软件、同步盘）占着")
    en = client.put("/api/settings", json={"roles": {"text": {"model": "new"}}}, headers={"X-StoryStudio-Lang": "en"})
    assert en.json()["detail"].startswith("Cannot write the settings file: "), en.text
    assert "no write permission" in en.json()["detail"] and not __import__("webui.i18n").i18n._HAN.search(en.json()["detail"])
    assert list(home.glob("*.tmp")) == []
    monkeypatch.undo()
    assert S.load_settings(path, apply_env=False).role("text").model == "keep"


def test_write_error_reason_is_one_short_phrase_not_the_raw_oserror(home, monkeypatch):
    """审查：原因以前是 str(OSError)——带着临时文件和设置文件的完整路径、Windows 上反斜杠加倍，还是系统语言的原话
    （「[WinError 5] 拒绝访问。: 'C:\\\\…tmp' -> 'C:\\\\…'」），英文界面照样一截中文。现在只留一小句原因。"""

    path = home / "settings.json"
    path.mkdir()          # 设置文件的位置被一个目录占着：换新文件那一下失败（Windows PermissionError / 其它 IsADirectoryError）
    r = client.put("/api/settings", json={"roles": {"text": {"model": "x"}}})
    detail = r.json()["detail"]
    assert r.status_code == 500 and detail.startswith(f"设置文件写不进去：{path}：")
    assert ".tmp" not in detail and "\\\\" not in detail and "->" not in detail and detail.count(str(path)) == 1
    assert list(home.glob("*.tmp")) == []
    path.rmdir()
    import errno

    def full(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device", str(src), None, str(dst))

    monkeypatch.setattr(S.os, "replace", full)
    r = client.put("/api/settings", json={"roles": {"text": {"model": "x"}}})
    assert r.status_code == 500 and f"{path}：磁盘空间不够——" in r.json()["detail"]
    en = client.put("/api/settings", json={"roles": {"text": {"model": "x"}}}, headers={"X-StoryStudio-Lang": "en"})
    assert f"{path}: not enough disk space — " in en.json()["detail"], en.text


def test_busy_lock_is_a_409_with_a_retry_hint(home, monkeypatch):
    def always_taken(handle):
        raise OSError(36, "Resource deadlock avoided")

    monkeypatch.setattr(S, "_lock_byte", always_taken)
    monkeypatch.setattr(S, "_PROCESS_LOCK_WAIT", 0.1)
    r = client.put("/api/settings", json={"roles": {"text": {"model": "x"}}})
    assert r.status_code == 409 and r.json()["detail"].startswith("另一个 StoryStudio 窗口正在保存设置，等几秒再保存一次")
    en = client.post("/api/system/local-hint/dismiss", json={"role": "all"}, headers={"X-StoryStudio-Lang": "en"})
    assert en.status_code == 409 and en.json()["detail"].startswith("Another StoryStudio window is saving the settings")
