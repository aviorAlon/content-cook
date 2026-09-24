"""示例异步来源的适配器：只用 genkit.plugin_sdk 里的名字（插件接口 v1，实验性）。

「远端」就是缓存目录里的一份 json：提交时把结果文件写好、记下任务，``get_task`` 查到第 ``polls_before_done`` 次
之后才说完成。配置（``extra``）：

* ``missing_tool``：假装本机缺了这个命令——提交前就抛 ``ProviderSetupRequired``（一次请求都没发出去）；
* ``polls_before_done``：前几次查询返回 processing（默认 0 = 第一次查就完成）；
* ``fail_with``：任务完成时报失败，原因是这句话。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from genkit.plugin_sdk import (
    Adapter,
    ImageRequest,
    MusicRequest,
    ProviderCommandError,
    ProviderSetupRequired,
    TaskSnapshot,
    TaskState,
    TaskSubmission,
    TaskType,
    VideoRequest,
    token_path,
)

PREFIX_IMAGE = "exasync-img:"
PREFIX_VIDEO = "exasync-vid:"
PREFIX_MUSIC = "exasync-mus:"
STATE_FILE = "example_async_tasks.json"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da6364"
                    "60f85f0f0002870180eb47ba920000000049454e44ae426082")


class ExampleAsyncBase(Adapter):
    task_type = TaskType.IMAGE
    prefix = PREFIX_IMAGE
    suffix = ".png"

    def __init__(self, config: Mapping[str, Any], **kw: Any) -> None:
        super().__init__(config, **kw)
        extra = dict(self.config.get("extra") or {})
        self.missing_tool = str(extra.get("missing_tool") or "")
        self.polls_before_done = int(extra.get("polls_before_done") or 0)
        self.fail_with = str(extra.get("fail_with") or "")

    # ---- 「远端」
    def _state_path(self) -> Path:
        return self.cache_dir / STATE_FILE

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, table: Mapping[str, Any]) -> None:
        self._state_path().write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")

    def _upload(self, token: str) -> str:
        """参考图：读文件换一个内容摘要（真实来源在这里上传换远端素材 id），绝不把本地路径发出去。"""

        path = token_path(token)
        if not path.is_file():
            raise ProviderCommandError(f"reference does not exist: {path.name}")
        return "asset-" + hashlib.sha1(path.read_bytes()).hexdigest()[:12]

    def _submit(self, name: str, model: str, payload: bytes, assets: list[str]) -> TaskSubmission:
        if self.missing_tool:
            raise ProviderSetupRequired(f"找不到示例命令 {self.missing_tool}：示例来源需要本机装好它")
        table = self._read()
        remote_id = f"{len(table) + 1:04d}"
        task_id = f"{self.prefix}{remote_id}"
        output = self.cache_dir / f"example_async_{self.task_type.value}_{remote_id}{self.suffix}"
        output.write_bytes(payload)
        table[task_id] = {"name": name, "model": model, "assets": assets, "polls": 0, "output": str(output.resolve())}
        self._write(table)
        return TaskSubmission(task_id=task_id, task_type=self.task_type, status=TaskState.PENDING)

    def get_task(self, task_id: str) -> TaskSnapshot:
        table = self._read()
        task = table.get(task_id)
        if task is None:
            return TaskSnapshot(task_id=task_id, task_type=self.task_type, status=TaskState.FAILED,
                                error_message=f"example-async: unknown task {task_id}")
        task["polls"] = int(task.get("polls") or 0) + 1
        self._write(table)
        if task["polls"] <= self.polls_before_done:
            return TaskSnapshot(task_id=task_id, task_type=self.task_type, status=TaskState.PROCESSING)
        if self.fail_with:
            return TaskSnapshot(task_id=task_id, task_type=self.task_type, status=TaskState.FAILED, error_message=self.fail_with)
        return TaskSnapshot(task_id=task_id, task_type=self.task_type, status=TaskState.FINISHED,
                            output_urls=(Path(task["output"]).as_uri(),))

    def ping(self) -> str:
        if self.missing_tool:
            raise ProviderSetupRequired(f"找不到示例命令 {self.missing_tool}：示例来源需要本机装好它")
        return f"example-async ready; using {self.model}"


class ExampleAsyncImages(ExampleAsyncBase):
    name = "example_async_images"

    def submit_image(self, request: ImageRequest) -> TaskSubmission:
        model = self.pick_model(request.model)
        assets = [self._upload(t) for t in request.reference_asset_ids]
        return self._submit(request.name, model, PNG, assets)


class ExampleAsyncVideo(ExampleAsyncBase):
    name = "example_async_video"
    task_type = TaskType.VIDEO
    prefix = PREFIX_VIDEO
    suffix = ".mp4"

    def submit_video(self, request: VideoRequest) -> TaskSubmission:
        model = self.pick_model(request.model)
        assets = [self._upload(r.asset_id) for r in request.references]
        return self._submit(request.name, model, b"example-video" * 200, assets)


class ExampleAsyncMusic(ExampleAsyncBase):
    name = "example_async_music"
    task_type = TaskType.MUSIC
    prefix = PREFIX_MUSIC
    suffix = ".mp3"

    def submit_music(self, request: MusicRequest) -> TaskSubmission:
        model = self.pick_model(request.model)
        assets = []
        for token in request.reference_asset_ids:
            if not str(token_path(token)).lower().endswith((".jpg", ".jpeg", ".png")):
                raise ProviderCommandError(f"example-async: reference must be jpg / png: {token_path(token).name}")
            assets.append(self._upload(token))
        return self._submit(request.name, model, b"ID3example-music" * 100, assets)


def build_images(config: Mapping[str, Any], **kw: Any) -> ExampleAsyncImages:
    return ExampleAsyncImages(config, **kw)


def build_video(config: Mapping[str, Any], **kw: Any) -> ExampleAsyncVideo:
    return ExampleAsyncVideo(config, **kw)


def build_music(config: Mapping[str, Any], **kw: Any) -> ExampleAsyncMusic:
    return ExampleAsyncMusic(config, **kw)
