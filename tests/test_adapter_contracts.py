"""适配器契约测试：一套通用的「提交 → 查询 → 落盘/下载地址」检查，每个适配器用 tests/fixtures/adapters/*.json
里的响应回放。夹具是按 SDK 签名与公开文档手写的；换成录制的真实响应前先脱敏（去掉签名 URL、任务 / 请求 id、账号与余额字段），测试本身不用改。

契约（所有适配器都要满足）：
* submit_* 返回 TaskSubmission；同步型 task_id 以 file: 开头且文件存在；异步型 task_id 带自己的前缀
* get_task 对异步 id 返回 TaskSnapshot；完成时 output_urls 非空，且是 http(s) 或 file:// URL
* 参考图（local: 令牌）被编码进请求，不以裸路径发出去
* 同一份夹具下，适配器不做网络访问（假 SDK / 假 http 记录所有调用）

夹具二选一（也可并存）：``responses``（假 SDK 的点路径 → 响应）、``http``（URL 片段 → 响应）。
插件的契约夹具不放这里（这里只放内置来源的）：插件自己的测试加载插件后调 :func:`run_contract_case`，
内置假实现不认的传输方式（比如子进程）用 ``fakes`` 参数接上自己的假实现。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping

import pytest

from genkit.adapters import REGISTRY
from genkit.adapters.base import HttpResponse, local_token
from genkit.providers import AssetReference, ImageRequest, MusicRequest, TaskState, TaskSubmission, TaskSnapshot, VideoRequest

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "adapters").glob("*.json"))
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def materialize(value: Any) -> Any:
    """夹具里的占位符：$PNG_B64 → 1×1 PNG 的 base64；$BYTES:xxx → bytes（插件自己的假实现也用它展开夹具）。"""

    if isinstance(value, str):
        if value == "$PNG_B64":
            return base64.b64encode(PNG).decode()
        if value.startswith("$BYTES:"):
            return value[len("$BYTES:"):].encode()
        return value
    if isinstance(value, list):
        return [materialize(v) for v in value]
    if isinstance(value, dict):
        return {k: materialize(v) for k, v in value.items()}
    return value


def _ns(value: Any) -> Any:
    """dict → SimpleNamespace（递归），模仿 SDK 返回的对象；bytes / str / list 原样。"""

    if isinstance(value, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_ns(v) for v in value]
    return value


class FakeSDK:
    """按夹具的 ``responses``（点路径 → 响应）搭一棵可调用的对象树，记录每次调用。"""

    def __init__(self, responses: dict[str, Any]):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._responses = responses
        tree: dict[str, Any] = {}
        for dotted, payload in responses.items():
            parts = dotted.split(".")
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = self._make(dotted, payload)
        for name, node in tree.items():
            setattr(self, name, self._build(node))

    def _make(self, dotted: str, payload: Any):
        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((dotted, kwargs))
            return _ns(materialize(payload))
        return call

    def _build(self, node: Any) -> Any:
        if isinstance(node, dict):
            return SimpleNamespace(**{k: self._build(v) for k, v in node.items()})
        return node


def _fake_http(routes: list[dict[str, Any]]):
    seen: list[tuple[str, str, Any]] = []

    def http(method: str, url: str, headers=None, json_body=None, data=None, timeout=0):
        # multipart 正文也要参与「参考图不裸发」和 payload_contains 检查（按 latin-1 解码，二进制部分不影响）
        seen.append((method, url, json_body if json_body is not None else (data.decode("latin-1") if isinstance(data, bytes) else data)))
        for route in routes:
            if route["match"] in url:
                if "bytes" in route:
                    return HttpResponse(int(route.get("status", 200)), materialize(route["bytes"]))
                return HttpResponse(int(route.get("status", 200)), json.dumps(materialize(route["json"])).encode())
        raise AssertionError(f"no fixture route for {url}")

    http.seen = seen  # type: ignore[attr-defined]
    return http


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("fixture", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_adapter_contract(fixture: Path, tmp_path: Path):
    run_contract_case(_load(fixture), tmp_path)


def run_contract_case(case: dict[str, Any], tmp_path: Path, *,
                      fakes: Mapping[str, tuple[str, Callable[[Any], Any]]] | None = None) -> None:
    """跑一份契约夹具（内置来源的在上面参数化；插件的测试加载插件后直接调它）。

    ``fakes``：夹具里内置假实现不认的键 → ``(builder 的关键字参数名, 造假实现的函数)``。造假实现的函数拿到夹具里那一项，
    返回的对象按参数名传给 builder；它若有 ``seen``（记下的请求），也参与「参考图不裸发」和 ``payload_contains`` 检查。
    """

    role, name = case["role"], case["adapter"]
    factory = REGISTRY[role][name]
    ref = tmp_path / "ref.png"
    ref.write_bytes(PNG)
    kwargs: dict[str, Any] = {"cache_dir": tmp_path}
    sdk = None
    http = None
    extra_seen: list[Any] = []
    if "responses" in case:
        sdk = FakeSDK(case["responses"])
        kwargs["client"] = sdk
    if "http" in case:
        http = _fake_http(case["http"])
        kwargs["http"] = http
    for key, (kwarg, make) in (fakes or {}).items():
        if key in case:
            fake = make(case[key])
            kwargs[kwarg] = fake
            extra_seen.append(fake)
    adapter = factory(case["config"], **kwargs)
    expect = case["expect"]

    if role == "image":
        request = ImageRequest(name="frame", prompt="一个人站在窗前", negative_prompt="文字", model="@image",
                               aspect_ratio="9:16", image_size="2K", quality=None, reference_asset_ids=(local_token(ref),))
        submission = adapter.submit_image(request)
        assert isinstance(submission, TaskSubmission)
        assert submission.status is TaskState[expect["submit_status"]]
        assert submission.task_id and submission.task_id.startswith(expect["task_id_prefix"])
        if submission.task_id.startswith("file:"):
            assert Path(submission.task_id[5:]).read_bytes() == PNG
    elif role == "video":
        request = VideoRequest(name="clip", prompt="她转头", duration=int(expect.get("duration", 6)), request_type="first_frame",
                               resolution=expect.get("resolution", "1080p"), aspect_ratio=expect.get("aspect_ratio", "9:16"),
                               references=(AssetReference(local_token(ref), "first_frame"),))
        submission = adapter.submit_video(request)
        assert isinstance(submission, TaskSubmission)
        assert submission.task_id and submission.task_id.startswith(expect["task_id_prefix"])
        snapshot = adapter.get_task(submission.task_id)
        assert isinstance(snapshot, TaskSnapshot)
        assert snapshot.status is TaskState[expect["final_status"]]
        if snapshot.status is TaskState.FINISHED:
            assert snapshot.output_urls and snapshot.output_urls[0].startswith(("http://", "https://", "file:"))
    elif role == "text":
        data, meta = adapter.complete_json("系统", "用户")
        assert data == expect["json"] and meta.get("model")
    elif role == "voice":
        out = adapter.synthesize("你好", tmp_path / "out.mp3", voice=case["config"].get("voice") or None)
        assert Path(out).read_bytes() == b"MP3DATA"
    elif role == "music":
        request = MusicRequest(name="bgm", prompt="温柔的摇篮曲", model="@music", duration=int(expect.get("duration", 120)),
                               count=int(expect.get("count", 1)), reference_asset_ids=(local_token(ref),))
        submission = adapter.submit_music(request)
        assert isinstance(submission, TaskSubmission)
        assert submission.task_id and submission.task_id.startswith(expect["task_id_prefix"])
        snapshot = adapter.get_task(submission.task_id)
        assert isinstance(snapshot, TaskSnapshot)
        assert snapshot.status is TaskState[expect["final_status"]]
        if snapshot.status is TaskState.FINISHED:
            assert snapshot.output_urls and snapshot.output_urls[0].startswith(("http://", "https://", "file:"))

    # 参考图必须被编码，不能把本地路径原样发出去
    payloads = json.dumps([kw for _, kw in (sdk.calls if sdk else [])] + [body for _, _, body in (http.seen if http else [])]
                          + [item for fake in extra_seen for item in getattr(fake, "seen", [])], default=str)
    assert "local:" not in payloads, "本地参考图令牌不该原样发给服务商"
    for needle in expect.get("payload_contains", []):
        assert needle in payloads, f"请求里缺 {needle!r}"
