"""本地长任务（台词识别、后期合成）的后台执行 + 落盘记录。

这两步都是本地计算（faster-whisper 跑几分钟、ffmpeg 合成几十秒），不花钱，但
不能用一个阻塞的 HTTP 请求硬扛：桌面窗口里请求一挂十分钟，人会以为死了。
所以跟起草 job（routers/scripts.py）同一个思路：提交即返回 task_id，后台线程跑，
状态写进 ``<项目根>/.webui_draft_cache/tasks/<task_id>.json``，前端轮询。

记录从不删除；同一个项目同一种任务同一时间只允许一个在跑（重复点按钮直接
拒绝，而不是排队——排队会让人以为第一次没生效再点几次）。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from genkit.logmask import mask
from storyvideo.state import atomic_write_json

from . import deps

log = logging.getLogger(__name__)
_log = logging.getLogger("storystudio.tasks")
_lock = threading.Lock()
_HAN = re.compile(r"[一-鿿]")


_running: dict[tuple[str, str], str] = {}   # (episode_id, kind) → task_id
_current = threading.local()                 # 后台线程里：正在跑的 task_id（fn 想往自己的记录里写进度时用）


class TaskBusy(RuntimeError):
    """同一项目同一种任务已经在跑。"""


def current_task_id() -> str | None:
    """在 ``start()`` 起的后台线程里返回自己的 task_id；别处返回 None。"""

    return getattr(_current, "task_id", None)


# task_id → pending / running / succeeded / failed 的内存镜像。忙判定以它为准、磁盘记录只作兜底：
# 以前只看磁盘，第一条 pending 记录又是在锁外落盘的，几个请求同时点「合成」时第二个进锁读文件还没写出来，
# 就当没在跑、再起一个——同一批 mp3 被几个线程一起写（2026-09-22 审计实撞）。测试直接往 _running 里塞 id +
# 写一条磁盘记录的老办法照样认（镜像里没有就读磁盘）。
_status: dict[str, str] = {}


def _is_active(task_id: str) -> bool:
    """pending / running 算在跑：先看内存镜像，镜像里没有（进程重启前的记录、测试手工塞的）再读磁盘。"""

    status = _status.get(task_id)
    if status is None:
        status = (get(task_id) or {}).get("status")
    return status in ("pending", "running")


def _tracked(task_id: str, fn: Callable[[], dict[str, Any]]) -> Callable[[], dict[str, Any]]:
    """包一层 ``fn``：一开跑记 running，一返回 / 一抛出就把内存镜像改成终态。

    终态放在自己的 try/finally 里，而不是跟 ``run()`` 里写失败记录的那几行挨着：那几行（error 文案、类名、
    日志）别的改动也常动，挨在一起合并时容易被整块替换掉——漏了这一句，失败的任务在内存里永远是 running，
    这个项目这种任务就一直 409 到重启。顺序上也是先改内存再落盘：fn 已经结束，这一瞬间放行下一个任务没有
    冲突；反过来会多一个假 409 的窗口。连 KeyboardInterrupt 这类 ``run()`` 不接的异常也记成 failed。"""

    def call() -> dict[str, Any]:
        _status[task_id] = "running"
        outcome = "failed"
        try:
            result = fn()
            outcome = "succeeded"
            return result
        finally:
            _status[task_id] = outcome

    return call


def active_task_id(kind: str, episode_id: str) -> str | None:
    """这个项目这种任务正在跑（pending/running）的 task_id；没有返回 None。"""

    with _lock:
        active = _running.get((episode_id, kind))
    if active and _is_active(active):
        return active
    return None


def note(task_id: str, **fields: Any) -> None:
    """往一条任务记录里追加/更新字段（进度、最近一次错误），不改 status。"""

    _write(task_id, **fields)


def tasks_dir() -> Path:
    # 运行时从 deps 取（不是 import 时拷一份）——测试会 monkeypatch deps.PROJECT_ROOT
    return deps.PROJECT_ROOT / ".webui_draft_cache" / "tasks"


def _path(task_id: str) -> Path:
    return tasks_dir() / f"{task_id}.json"


# 一条记录同时被后台线程写、被轮询接口读。Windows 上「另一边正在 os.replace 这个文件」的一瞬间，读会撞
# PermissionError、写会撞同样的共享冲突——文件其实好好的，只是这一刻打不开。以前读失败当「没有这个任务」、
# 写失败直接丢掉，结果就是轮询偶发 404（界面以为任务凭空没了）或整条记录不落盘。撞到 OSError 就短暂重试。
_IO_RETRIES = 5
_IO_PAUSE = 0.02

# 记过一次警告的坏文件名：任务中心和首页会反复轮询列表，同一个坏文件每次都记一行会把日志刷满
_warned: set[str] = set()


def _warn_once(path: Path, reason: str) -> None:
    key = str(path)
    if key in _warned:
        return
    _warned.add(key)
    _log.warning("跳过坏的任务记录 %s：%s（程序自己只会写对象，这个文件多半是外面放进来的，删掉即可）", path, reason)


def _parse_record(path: Path, raw: bytes) -> dict[str, Any] | None:
    """一条记录必须是 JSON 对象。数组 / 字符串 / null 这种「合法 JSON 但不是对象」以前会原样透出去，
    列表接口在 ``item.get`` 处炸成 500、单条接口撞响应模型校验也是 500——一个外来的坏文件让整个任务中心
    打不开。这里统一当「坏文件」：返回 None，并记一行日志让人能找到它。

    解码也放在这里兜：手改过的记录可能不是 UTF-8（中文 Windows 的记事本常存成 GBK，记录里又多是中文失败
    原因），``UnicodeDecodeError`` 既不是 OSError 也不是 JSONDecodeError，不接住照样把列表炸成 500。
    显式按 UTF-8 解码而不是把 bytes 直接交给 json.loads——后者会自己猜 UTF-16 / 32，猜错了更难查。
    用 utf-8-sig：老记事本「UTF-8」存盘会带 BOM，那种记录内容是好的，跟项目文件（``deps.read_json_bom``）一样照读。"""

    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        _warn_once(path, "不是 UTF-8 编码")
        return None
    except json.JSONDecodeError as exc:
        _warn_once(path, f"不是合法 JSON（{exc.msg}）")
        return None
    if not isinstance(data, dict):
        _warn_once(path, f"顶层是 {type(data).__name__}，不是对象")
        return None
    return data


def _read_record(path: Path) -> dict[str, Any] | None:
    for attempt in range(_IO_RETRIES):
        if not path.is_file():
            return None
        try:
            raw = path.read_bytes()
        except OSError:
            if attempt == _IO_RETRIES - 1:
                return None
            time.sleep(_IO_PAUSE)
            continue
        return _parse_record(path, raw)      # 写盘是原子替换，读到半截 JSON / 解不了码只可能是文件真坏了，不重试
    return None


# 「人话」的判据：本项目写给用户看的报错都是中文源文案（英文界面由 i18n 层再翻），消息里有汉字就是写给人看的
_CJK = re.compile(r"[一-鿿]")


def _error_text(exc: BaseException) -> str:
    """落进 task.error 的那句话——各模块弹窗把它原样拼在「xx 失败：」后面给用户看。

    自己抛的人话异常（「ClipGenerationError: video 模型未配置…」）去掉类名前缀：类名是给开发者的，类名另存
    error_type、堆栈另存 traceback，日志里照旧记带类名的一行。KeyError / AttributeError / ValueError 这类程序 bug
    的消息只剩「'shots'」「could not convert string to float」，离了上下文谁都看不懂，加一句「遇到没预料到的错误」——
    用户至少知道这不是自己的操作有问题，是程序没兜住。KeyError 再例外一次：它的消息是键的 repr（「'林夏'」这种），
    有汉字也不是一句话。消息为空时退回类名，不留空串。"""

    name = type(exc).__name__
    message = str(exc).strip()
    if not message:
        return name
    if _CJK.search(message) and not isinstance(exc, KeyError):
        return message
    return f"遇到没预料到的错误：{name}: {message}——请把这条任务记录发给开发者"


def _created_at(item: Mapping[str, Any]) -> float:
    """排序键：``created_at`` 不是数字（字符串、null、缺失）的记录排到最后，而不是让整个 sort 抛 TypeError。"""

    value = item.get("created_at")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _write(tid: str, **fields: Any) -> None:  # 首参不叫 task_id：fields 里也要塞一个 task_id 字段
    path = _path(tid)
    current: dict[str, Any] = _read_record(path) or {}
    current.update(fields)
    current["updated_at"] = time.time()
    for attempt in range(_IO_RETRIES):
        try:
            atomic_write_json(path, current)
            return
        except OSError:
            if attempt == _IO_RETRIES - 1:
                return
            time.sleep(_IO_PAUSE)


def get(task_id: str) -> dict[str, Any] | None:
    return _read_record(_path(task_id))


# 任务属于哪个功能模块（DESIGN §38）。现在所有任务都是短剧产线的（台词识别、后期合成、海报、导出、
# 后台轮询……），所以默认 drama；老记录没有这个字段也按 drama 算。以后别的模块起任务时传自己的 id，
# 任务中心页按它分组。
DEFAULT_MODULE = "drama"


def task_module(item: Mapping[str, Any]) -> str:
    return str(item.get("module") or DEFAULT_MODULE)


def list_tasks(episode_id: str | None = None, module: str | None = None) -> list[dict[str, Any]]:
    root = tasks_dir()
    if not root.is_dir():
        return []
    out = []
    for path in root.glob("*.json"):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        item = _parse_record(path, raw)
        if item is None:
            continue                         # 一个坏文件不能拖垮整个列表
        if episode_id and item.get("episode_id") != episode_id:
            continue
        if module and task_module(item) != module:
            continue
        out.append(item)
    out.sort(key=_created_at, reverse=True)
    return out


def start(kind: str, episode_id: str, fn: Callable[[], dict[str, Any]], *, params: dict[str, Any] | None = None,
          module: str = DEFAULT_MODULE) -> str:
    """提交一个后台任务。``fn`` 返回的字典作为 result 落盘；抛异常则记 failed 与人话原因。"""

    key = (episode_id, kind)
    with _lock:
        active = _running.get(key)
        if active and _is_active(active):
            raise TaskBusy(f"{episode_id} 的「{kind}」已经在跑（task {active}），等它结束再点。")
        task_id = uuid.uuid4().hex
        _running[key] = task_id
        _status[task_id] = "pending"
        # pending 记录也在锁内落盘：并发的第二个请求进锁时，内存镜像和磁盘都已经是 pending，两边都拦得住
        _write(task_id, task_id=task_id, kind=kind, episode_id=episode_id, module=module, status="pending",
               created_at=time.time(), params=params or {})
    fn = _tracked(task_id, fn)                   # 内存镜像的 running / 终态都在这层里改，下面的 run() 不用管

    def run() -> None:
        _current.task_id = task_id
        _write(task_id, status="running", started_at=time.time())
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - 后台线程，任何异常都不能让任务卡在 running
            # error 只放人话——各模块的弹窗把它原样给用户看，「ClipGenerationError: …」这种类名前缀是给开发者的
            # （程序 bug 的类名照旧留着，见 _error_text）；类名另存 error_type，traceback 也单独落盘，
            # 日志里照旧记带类名的一行，排障信息一点不少
            log.exception("task %s (%s / %s) failed", task_id, kind, episode_id)
            _log.warning("后台任务 %s（%s / %s）失败：%s: %s", task_id, kind, episode_id, type(exc).__name__, exc)
            # 落盘的记录先脱敏再截断：失败文案叫用户「把这条任务记录发给开发者」，适配器 raise … from exc 包进来的上游原话
            # （401 回显的密钥）会同时出现在 error 和整条异常链的 traceback 里。先截再遮的话，截断点可能正好切开一个密钥
            _write(task_id, status="failed", error=mask(_error_text(exc))[:2000],
                   error_type=type(exc).__name__, traceback=mask(traceback.format_exc())[-4000:], finished_at=time.time())
            return
        _write(task_id, status="succeeded", result=result, finished_at=time.time())

    threading.Thread(target=run, daemon=True, name=f"task-{kind}-{episode_id}").start()
    return task_id


def public_record(item: Mapping[str, Any], *, debug: bool = False) -> dict[str, Any]:
    """给接口返回的记录：默认去掉 traceback（里面有本机绝对路径和 Python 堆栈，界面上没用），``debug`` 时原样。"""

    if debug:
        return dict(item)
    return {k: v for k, v in item.items() if k != "traceback"}


__all__ = ["DEFAULT_MODULE", "TaskBusy", "start", "get", "list_tasks", "task_module", "tasks_dir", "current_task_id",
           "active_task_id", "note", "public_record"]
