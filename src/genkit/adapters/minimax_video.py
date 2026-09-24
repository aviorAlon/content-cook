"""视频：MiniMax（/video_generation 提交 → /query/video_generation 轮询 → /files/retrieve 取下载地址）。异步接口。"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..providers import TaskSnapshot, TaskState, TaskSubmission, TaskType, VideoRequest
from .base import Adapter, raise_for_status, to_data_url

PREFIX = "minimax:"


class MiniMaxVideo(Adapter):
    name = "minimax_video"

    def submit_video(self, request: VideoRequest) -> TaskSubmission:
        model = self.pick_model(request.model)
        from ..capabilities import resolution_for

        body: dict[str, Any] = {"model": model, "prompt": request.prompt, "duration": int(request.duration),
                                "resolution": resolution_for("video", "minimax_video", model, request.resolution).upper()}
        if request.negative_prompt:
            body["prompt"] += f"\n不要出现：{request.negative_prompt}"
        refs = list(request.references)
        if refs:
            body["first_frame_image"] = to_data_url(refs[0].asset_id)
        if request.extra_params.get("prompt_optimizer") is not None:
            body["prompt_optimizer"] = bool(request.extra_params["prompt_optimizer"])
        response = self.http("POST", f"{self.base_url}/video_generation", headers=self.auth_headers(), json_body=body, timeout=120.0)
        raise_for_status(response, what="minimax submit")
        data = response.json()
        base = data.get("base_resp") or {}
        if base.get("status_code") not in (None, 0):
            return TaskSubmission(task_id=None, task_type=TaskType.VIDEO, status=TaskState.FAILED,
                                  error_message=f"minimax {base.get('status_code')}: {base.get('status_msg')}")
        task_id = data.get("task_id")
        if not task_id:
            return TaskSubmission(task_id=None, task_type=TaskType.VIDEO, status=TaskState.FAILED, error_message=f"minimax response has no task_id: {json.dumps(data, ensure_ascii=False)[:200]}")
        return TaskSubmission(task_id=f"{PREFIX}{task_id}", task_type=TaskType.VIDEO, status=TaskState.PENDING)

    def get_task(self, task_id: str) -> TaskSnapshot:
        remote_id = task_id[len(PREFIX):] if task_id.startswith(PREFIX) else task_id
        response = self.http("GET", f"{self.base_url}/query/video_generation?task_id={remote_id}", headers=self.auth_headers(), timeout=60.0)
        raise_for_status(response, what="minimax status")
        data = response.json()
        status = str(data.get("status") or "").lower()
        if status == "success":
            file_id = data.get("file_id")
            if not file_id:
                return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.PROCESSING)
            url = self._download_url(str(file_id))
            return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.FINISHED if url else TaskState.PROCESSING, output_urls=(url,) if url else ())
        if status == "fail":
            base = data.get("base_resp") or {}
            return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.FAILED, error_message=str(base.get("status_msg") or data.get("error") or "minimax task failed"))
        return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.PROCESSING if status in {"processing", "preparing"} else TaskState.PENDING)

    def _download_url(self, file_id: str) -> str:
        response = self.http("GET", f"{self.base_url}/files/retrieve?file_id={file_id}", headers=self.auth_headers(), timeout=60.0)
        raise_for_status(response, what="minimax files/retrieve")
        data = response.json()
        return str(((data.get("file") or {}).get("download_url")) or "")

    def ping(self) -> str:
        response = self.http("GET", f"{self.base_url}/query/video_generation?task_id=0", headers=self.auth_headers(), timeout=30.0)
        if response.status in {401, 403}:
            raise_for_status(response, what="minimax auth")
        return f"reachable (HTTP {response.status}), model {self.model}"


def build(config: Mapping[str, Any], **kw: Any) -> MiniMaxVideo:
    return MiniMaxVideo(config, **kw)
