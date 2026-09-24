"""视频：OpenAI Videos 接口形状（POST /videos → GET /videos/{id} → GET /videos/{id}/content）。

OpenAI 自己的 Sora 走这个形状，LiteLLM 这类统一网关也按它转发到 Veo / Runway / Kling 等——所以这是
「统一网关」能覆盖视频角色的那条路。首帧用 multipart 的 ``input_reference`` 字段上传；成品要带密钥才能下载，
所以完成时适配器自己把内容拉进缓存，给运行时的是 file: 地址（和同步出图的适配器一样）。

按 OpenAI 文档写的，没有真实跑过（source=doc）。
"""

from __future__ import annotations

import json
import mimetypes
import uuid
from typing import Any, Mapping

from ..providers import TaskSnapshot, TaskState, TaskSubmission, TaskType, VideoRequest
from .base import Adapter, raise_for_status, token_path

PREFIX = "oaivid:"

# (画幅, 分辨率档) → OpenAI 的 size 字符串；不在表里的按 720p 竖版
_SIZES: dict[tuple[str, str], str] = {
    ("9:16", "720p"): "720x1280", ("16:9", "720p"): "1280x720", ("1:1", "720p"): "720x720",
    ("9:16", "1080p"): "1080x1920", ("16:9", "1080p"): "1920x1080", ("1:1", "1080p"): "1080x1080",
    ("9:16", "480p"): "480x854", ("16:9", "480p"): "854x480",
}


def size_for(aspect_ratio: str, resolution: str) -> str:
    key = (str(aspect_ratio or "9:16"), str(resolution or "720p").lower())
    return _SIZES.get(key, "720x1280")


def _multipart(fields: Mapping[str, str], file_field: str, filename: str, content: bytes, mime: str) -> tuple[bytes, str]:
    boundary = f"----storystudio{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {mime}\r\n\r\n".encode("utf-8") + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class OpenAIVideo(Adapter):
    name = "openai_video"

    def submit_video(self, request: VideoRequest) -> TaskSubmission:
        model = self.pick_model(request.model)
        prompt = request.prompt + (f"\n\nAvoid: {request.negative_prompt}" if request.negative_prompt else "")
        fields: dict[str, str] = {"model": model, "prompt": prompt, "seconds": str(int(request.duration)),
                                  "size": size_for(request.aspect_ratio, request.resolution)}
        refs = list(request.references)
        headers = self.auth_headers()
        if refs:
            path = token_path(refs[0].asset_id)
            if not path.is_file():
                return TaskSubmission(task_id=None, task_type=TaskType.VIDEO, status=TaskState.FAILED, error_message=f"reference image does not exist: {path}")
            body, content_type = _multipart(fields, "input_reference", path.name, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/png")
            response = self.http("POST", f"{self.base_url}/videos", headers={**headers, "Content-Type": content_type}, data=body, timeout=120.0)
        else:
            response = self.http("POST", f"{self.base_url}/videos", headers=headers, json_body={**fields, "seconds": int(request.duration)}, timeout=120.0)
        raise_for_status(response, what="openai videos submit")
        data = response.json()
        video_id = data.get("id")
        if not video_id:
            return TaskSubmission(task_id=None, task_type=TaskType.VIDEO, status=TaskState.FAILED, error_message=f"videos response has no id: {json.dumps(data, ensure_ascii=False)[:200]}")
        status = self._state(str(data.get("status") or "queued"))
        if status is TaskState.FAILED:
            return TaskSubmission(task_id=f"{PREFIX}{video_id}", task_type=TaskType.VIDEO, status=TaskState.FAILED, error_message=self._error(data))
        return TaskSubmission(task_id=f"{PREFIX}{video_id}", task_type=TaskType.VIDEO, status=TaskState.PENDING)

    def get_task(self, task_id: str) -> TaskSnapshot:
        remote_id = task_id[len(PREFIX):] if task_id.startswith(PREFIX) else task_id
        response = self.http("GET", f"{self.base_url}/videos/{remote_id}", headers=self.auth_headers(), timeout=60.0)
        raise_for_status(response, what="openai videos status")
        data = response.json()
        status = self._state(str(data.get("status") or ""))
        if status is TaskState.FAILED:
            return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.FAILED, error_message=self._error(data))
        if status is not TaskState.FINISHED:
            return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=status)
        # 成品要带密钥才能拉，运行时的下载器不带——这里拉进缓存，交 file: 地址
        content = self.http("GET", f"{self.base_url}/videos/{remote_id}/content", headers=self.auth_headers(), timeout=600.0)
        raise_for_status(content, what="openai videos content")
        url = self.store_result(content.body, suffix=".mp4", hint=f"oaivid_{remote_id}")
        if not url:
            return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.FAILED, error_message="videos content is empty")
        return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.FINISHED, output_urls=(url,))

    @staticmethod
    def _state(status: str) -> TaskState:
        status = status.lower()
        if status in {"completed", "succeeded", "success"}:
            return TaskState.FINISHED
        if status in {"failed", "error", "cancelled", "canceled"}:
            return TaskState.FAILED
        if status in {"in_progress", "processing", "running"}:
            return TaskState.PROCESSING
        return TaskState.PENDING

    @staticmethod
    def _error(data: Mapping[str, Any]) -> str:
        err = data.get("error")
        if isinstance(err, Mapping):
            return str(err.get("message") or err.get("code") or "video generation failed")
        return str(err or "video generation failed")

    def ping(self) -> str:
        response = self.http("GET", f"{self.base_url}/models", headers=self.auth_headers(), timeout=30.0)
        raise_for_status(response, what="openai models")
        data = response.json()
        count = len(data.get("data") or []) if isinstance(data, Mapping) else 0
        return f"reachable ({count} models), using {self.model}"


def build(config: Mapping[str, Any], **kw: Any) -> OpenAIVideo:
    return OpenAIVideo(config, **kw)
