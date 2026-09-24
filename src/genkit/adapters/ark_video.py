"""视频：火山方舟 Seedance，走官方开源 SDK（``Ark.content_generation.tasks.create / get``）。异步接口。"""

from __future__ import annotations

from typing import Any, Mapping

from ..providers import TaskSnapshot, TaskState, TaskSubmission, TaskType, VideoRequest
from .ark_images import ArkBase, probe_ark, submit_failure, translate_ark_error
from .base import to_data_url

PREFIX = "ark:"


class ArkVideo(ArkBase):
    name = "ark_video"

    def submit_video(self, request: VideoRequest) -> TaskSubmission:
        model = self.pick_model(request.model)
        ratio = "adaptive" if request.references else request.aspect_ratio
        from ..capabilities import resolution_for

        resolution = resolution_for("video", "ark_video", model, request.resolution)
        text = f"{request.prompt} --ratio {ratio} --duration {int(request.duration)} --resolution {resolution}"
        if request.negative_prompt:
            text += f"\n不要出现：{request.negative_prompt}"
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        refs = list(request.references)
        if request.request_type in {"first_frame", "first_last_frame"} and refs:
            content.append({"type": "image_url", "image_url": {"url": to_data_url(refs[0].asset_id)}, "role": "first_frame"})
            if request.request_type == "first_last_frame" and len(refs) > 1:
                content.append({"type": "image_url", "image_url": {"url": to_data_url(refs[1].asset_id)}, "role": "last_frame"})
        else:
            for ref in refs:
                content.append({"type": "image_url", "image_url": {"url": to_data_url(ref.asset_id)}, "role": "reference_image"})
        try:
            result = self.client.content_generation.tasks.create(model=model, content=content)
        except Exception as exc:  # noqa: BLE001
            return submit_failure(exc, "ark video submit", TaskType.VIDEO)
        task_id = getattr(result, "id", None)
        if not task_id:
            return TaskSubmission(task_id=None, task_type=TaskType.VIDEO, status=TaskState.FAILED, error_message=f"ark video: no task id ({result})")
        return TaskSubmission(task_id=f"{PREFIX}{task_id}", task_type=TaskType.VIDEO, status=TaskState.PENDING)

    def get_task(self, task_id: str) -> TaskSnapshot:
        remote_id = task_id[len(PREFIX):] if task_id.startswith(PREFIX) else task_id
        try:
            result = self.client.content_generation.tasks.get(task_id=remote_id)
        except Exception as exc:  # noqa: BLE001
            raise translate_ark_error(exc, "ark video status") from exc
        status = str(getattr(result, "status", "") or "").lower()
        if status == "succeeded":
            content = getattr(result, "content", None)
            url = getattr(content, "video_url", None) if content is not None else None
            return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.FINISHED if url else TaskState.PROCESSING, output_urls=(url,) if url else ())
        if status in {"failed", "cancelled", "expired"}:
            err = getattr(result, "error", None)
            message = getattr(err, "message", None) or str(err or status)
            return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.FAILED, error_message=message)
        return TaskSnapshot(task_id=task_id, task_type=TaskType.VIDEO, status=TaskState.PROCESSING if status == "running" else TaskState.PENDING)

    def ping(self) -> str:
        return probe_ark(self)


def build(config: Mapping[str, Any], **kw: Any) -> ArkVideo:
    return ArkVideo(config, **kw)
