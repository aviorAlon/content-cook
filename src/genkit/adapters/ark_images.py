"""生图：火山方舟 Seedream，走官方开源 SDK ``volcengine-python-sdk``（``volcenginesdkarkruntime.Ark``）。同步接口，结果落盘。"""

from __future__ import annotations

import base64
from typing import Any, Mapping

from ..providers import ImageRequest, ProviderCommandError, ProviderRejected, QuotaExhausted, TaskState, TaskSubmission, TaskType
from .base import Adapter, to_data_url

_SIZE_BY_RATIO = {"9:16": "1440x2560", "16:9": "2560x1440", "1:1": "2048x2048", "3:4": "1728x2304", "4:3": "2304x1728"}


# 审核拦截的判据：先看 SDK 解析出的结构化错误码（body.error.code，如 SensitiveContent / OutputContentBlocked /
# InputContentBlocked），再看文案里明确的审核字眼。**不能**拿裸的 "content" 当关键词——视频接口的参数就叫
# content、任务叫 content generation task，InvalidParameter / NotFound 的文案几乎都带它；而 SDK 的
# ArkAPIStatusError.__str__ 固定以 "Error code: N" 开头，"error" 永远在，等于只剩 "content" 一个条件在判
# （ADP-02：参数越界 / 任务不存在都被记成 ProviderRejected，界面按「内容被审核拦截」藏掉重提按钮）。
_REJECT_CODES = ("sensitive", "contentblocked", "content_blocked", "moderation")
_REJECT_WORDS = ("sensitive", "moderation", "content_policy", "content policy", "flagged", "违规", "敏感", "审核")
_QUOTA_CODES = ("overdue", "quota", "balance", "insufficient")
_QUOTA_WORDS = ("quota", "balance", "insufficient", "欠费", "余额")


def translate_ark_error(exc: Exception, what: str) -> Exception:
    text = str(exc)
    low = text.lower()
    code = str(getattr(exc, "code", "") or "").lower()
    status = getattr(exc, "status_code", None)
    # 余额：按状态码 402 与错误码判，不再在整段文案里找 "402"——request_id 是一长串数字，随手就含 402
    if status == 402 or any(k in code for k in _QUOTA_CODES) or any(k in low for k in _QUOTA_WORDS):
        return QuotaExhausted(f"{what}: {text[:300]}")
    if any(k in code for k in _REJECT_CODES) or any(k in low for k in _REJECT_WORDS):
        return ProviderRejected(f"{what}: {text[:300]}")
    return ProviderCommandError(f"{what}: {text[:300]}")


def submit_failure(exc: Exception, what: str, task_type: TaskType) -> TaskSubmission:
    """提交时的 SDK 异常 → 抛出去，或记成一次明确失败的提交。

    审核 / 欠费照旧抛 ProviderRejected / QuotaExhausted（运行时各有去处）。服务商给了明确 4xx 的其余错误
    （InvalidParameter 参数越界、模型不存在、没权限、限流）说明任务根本没建起来：返回 FAILED 的提交，
    账本记「提交失败」、可改 spec 后重提——不再抛 ProviderCommandError 让运行时当成「不知道服务商收没收到」
    把整条流水线卡在待对账（ADP-02 收紧审核判据之后，这类 400 不再被误记成审核拦截，得有个对的去处）。
    连接错误、5xx 仍抛：那才是真的不确定任务建没建。"""

    translated = translate_ark_error(exc, what)
    status = getattr(exc, "status_code", None)
    if (isinstance(status, int) and 400 <= status < 500
            and not isinstance(translated, (ProviderRejected, QuotaExhausted))):
        return TaskSubmission(task_id=None, task_type=task_type, status=TaskState.FAILED,
                              error_message=f"{type(translated).__name__}: {translated}")
    raise translated from exc


def probe_ark(adapter: "ArkBase") -> str:
    """「测试连接」真正探一次：列一页任务（GET /contents/generations/tasks，账号级、不产生费用）——
    假域名报连接错误、假密钥报 401；之前只拼字符串、连 SDK client 都不构造（ADP-01）。"""

    try:
        # 30 秒封顶：client 的 300 秒是给出图出片用的，测试连接要马上给答案
        adapter.client.content_generation.tasks.list(page_size=1, timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        base = adapter.base_url or "（默认地址）"
        status = getattr(exc, "status_code", None)
        # 连接层失败（ArkAPIConnectionError / ArkAPITimeoutError）没有状态码；别的无状态码异常照原样翻，不硬说成连不上
        if status is None and ("connection" in type(exc).__name__.lower() or "timeout" in type(exc).__name__.lower()
                               or isinstance(exc, OSError)):
            raise ProviderCommandError(f"连不上 {base}：{str(exc)[:200]}——检查接口地址、网络或代理") from exc
        translated = translate_ark_error(exc, "ark ping (tasks.list)")
        # 欠费先认（方舟对欠费账号回 403 AccountOverdueError）：密钥是对的，不能说成「拒绝了这个密钥」
        if status in (401, 403) and not isinstance(translated, QuotaExhausted):
            raise ProviderCommandError(f"{base} 拒绝了这个密钥（{status}）：{str(exc)[:200]}——检查 api_key") from exc
        raise translated from exc
    return f"reachable, using {adapter.model} (base {adapter.base_url or 'default'})"


class ArkBase(Adapter):
    def __init__(self, config: Mapping[str, Any], *, client: Any = None, **kw: Any) -> None:
        super().__init__(config, **kw)
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from volcenginesdkarkruntime import Ark

            kwargs: dict[str, Any] = {"api_key": self.api_key or "missing", "timeout": 300}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = Ark(**kwargs)
        return self._client


class ArkImages(ArkBase):
    name = "ark_images"

    def submit_image(self, request: ImageRequest) -> TaskSubmission:
        model = self.pick_model(request.model)
        prompt = request.prompt + (f"\n\n不要出现：{request.negative_prompt}" if request.negative_prompt else "")
        kwargs: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": str(request.extra_params.get("size") or _SIZE_BY_RATIO.get(request.aspect_ratio, "1440x2560")),
            "response_format": "b64_json",
            "watermark": False,
        }
        refs = [to_data_url(t) for t in request.reference_asset_ids]
        if refs:
            kwargs["image"] = refs if len(refs) > 1 else refs[0]
        try:
            result = self.client.images.generate(**kwargs)
        except Exception as exc:  # noqa: BLE001
            return submit_failure(exc, "ark images", TaskType.IMAGE)
        data = list(getattr(result, "data", None) or [])
        if not data:
            return TaskSubmission(task_id=None, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message=f"ark images: empty data ({getattr(result, 'error', None)})")
        item = data[0]
        b64 = getattr(item, "b64_json", None)
        if b64:
            task_id = self.store_result(base64.b64decode(b64), suffix=".png", hint=request.name)
            return TaskSubmission(task_id=task_id, task_type=TaskType.IMAGE, status=TaskState.FINISHED)
        url = getattr(item, "url", None)
        if url:
            return TaskSubmission(task_id=f"url:{url}", task_type=TaskType.IMAGE, status=TaskState.FINISHED, output_urls=(url,))
        return TaskSubmission(task_id=None, task_type=TaskType.IMAGE, status=TaskState.FAILED, error_message="ark images: no b64_json/url")

    def ping(self) -> str:
        return probe_ark(self)


def build(config: Mapping[str, Any], **kw: Any) -> ArkImages:
    return ArkImages(config, **kw)
