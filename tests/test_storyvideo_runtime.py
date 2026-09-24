from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil

import pytest

from genkit.providers import (
    ImageRequest,
    ProviderCommandError,
    ProviderUnavailable,
    QuotaExhausted,
    RemoteTask,
    TaskSnapshot,
    TaskState,
    TaskSubmission,
    TaskType,
    VideoRequest,
)
from storyvideo.assembly import AssemblyResult
from storyvideo.media import MediaInfo, MediaValidationError
from storyvideo.qc import load_qc
from storyvideo.runtime import (
    StoryVideoBlocked,
    StoryVideoError,
    StoryVideoGatePending,
    StoryVideoRunner,
)


def write_episode(tmp_path: Path, *, shots: list[dict] | None = None) -> Path:
    default = [
        {
            "id": "S1",
            "dur": 4,
            "cast": ["su"],
            "lines": [{"who": "su", "en": "Hello.", "zh": "你好。"}],
            "shot": "medium",
            "art": "A bright room.",
            "motion": "Su waves and speaks.",
        }
    ]
    value = {
        "id": "EP99",
        "output_root": "output/story_video/EP99",
        "cast": {"su": {"block": "Su is a silver-haired man.", "asset": "asset-su"}},
        "scene_anchor": "A bright apartment. ",
        "camera": "Locked vertical camera. ",
        "neg": "text, watermark",
        "shots": shots or default,
        "video_params": {
            "model": "video-model",
            "resolution": "1080p",
            "generate_audio": True,
        },
        "image_params": {"model": "image-model"},
    }
    path = tmp_path / "content" / "story_video" / "EP99.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


class FakeProvider:
    name = "fake"

    def __init__(self, *, pending: bool = False, submit_error: Exception | None = None):
        self.pending = pending
        self.submit_error = submit_error
        self.submissions: list[ImageRequest | VideoRequest] = []
        self.polls: list[tuple[str, TaskType]] = []
        self.uploads: list[Path] = []
        self.upload_workspaces: list[str | None] = []
        self.downloads: list[str] = []

    def upload_asset(self, path, *, asset_type="image", workspace_id=None):
        self.uploads.append(Path(path))
        self.upload_workspaces.append(workspace_id)
        return f"upload-{Path(path).stem}"

    def submit(self, request):
        self.submissions.append(request)
        if self.submit_error is not None:
            raise self.submit_error
        task_type = TaskType.IMAGE if isinstance(request, ImageRequest) else TaskType.VIDEO
        index = len(self.submissions)
        return TaskSubmission(
            task_id=f"{task_type.value}-task-{index}",
            task_type=task_type,
            status=TaskState.PENDING if self.pending else TaskState.FINISHED,
            output_urls=() if self.pending else (f"https://example.test/{index}",),
        )

    def get_task(self, task_id, task_type):
        normalized = TaskType(task_type)
        self.polls.append((task_id, normalized))
        return TaskSnapshot(task_id, normalized, TaskState.PROCESSING)

    def download(self, url, destination, **kwargs):
        self.downloads.append(url)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"generated-media" * 1000)
        return target


def fake_info(path, kind="video", **kwargs):
    target = Path(path).resolve()
    image = str(kind) == "image"
    return MediaInfo(
        path=target,
        duration_seconds=0.0 if image else 4.0,
        width=1080,
        height=1920,
        fps=1.0 if image else 24.0,
        sample_rate=None if image else 48000,
        has_video=True,
        has_audio=not image,
        size_bytes=target.stat().st_size if target.exists() else 20_000,
    )


@pytest.fixture
def fake_media(monkeypatch):
    monkeypatch.setattr("storyvideo.runtime.validate_media", fake_info)

    def sheet(_items, destination, **kwargs):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"qc-sheet")
        return destination

    monkeypatch.setattr("storyvideo.runtime._contact_sheet", sheet)
    monkeypatch.setattr("storyvideo.runtime._video_contact_sheet", sheet)
    return sheet


def test_prepare_freezes_spec_and_builds_all_jobs(tmp_path):
    write_episode(tmp_path)
    with StoryVideoRunner(tmp_path) as runner:
        spec = runner.prepare("EP99")
        snapshot = runner.store.snapshot("EP99")

    assert Path(spec["output_root"]).name == "EP99"
    assert [job["step_id"] for job in snapshot["jobs"]] == [
        "frame-S1",
        "video-S1",
        "assemble",
    ]
    assert (tmp_path / "output/story_video/EP99/manifests/episode.snapshot.json").is_file()
    assert (tmp_path / "output/story_video/EP99/manifests/run_state.json").is_file()


def test_local_postproduction_preflight_fails_before_any_paid_submission(
    tmp_path, fake_media
):
    write_episode(tmp_path)
    provider = FakeProvider()
    with StoryVideoRunner(
        tmp_path,
        provider=provider,
        ffmpeg="definitely-missing-storyvideo-ffmpeg",
    ) as runner:
        with pytest.raises(StoryVideoError, match="preflight"):
            runner.run("EP99")

    assert provider.submissions == []


def test_legacy_import_adopts_local_artifacts_without_remote_submission(
    tmp_path, fake_media
):
    write_episode(tmp_path)
    root = tmp_path / "output/story_video/EP99"
    frame = root / "01_frames/S1.png"
    video = root / "02_video/S1.mp4"
    final = root / "EP99_final.mp4"
    for path in (frame, video, final):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"legacy-media" * 2000)

    with StoryVideoRunner(tmp_path) as runner:
        with pytest.raises(StoryVideoError, match="explicit confirmation"):
            runner.import_legacy("EP99", confirmed_existing_artifacts=False)
        imported = runner.import_legacy(
            "EP99", confirmed_existing_artifacts=True
        )
        jobs = {job["step_id"]: job for job in imported["state"]["jobs"]}

    assert imported["status"] == "awaiting_frames_qc"
    assert all(job["status"] == "succeeded" for job in jobs.values())
    assert all(job["attempt"] == 0 for job in jobs.values())
    assert all(
        imported["qc"]["gates"][gate]["status"] == "pending_review"
        for gate in ("frames", "videos", "final")
    )


def test_status_marks_accepted_final_stale_when_artifact_hash_changes(
    tmp_path, fake_media
):
    write_episode(tmp_path)
    root = tmp_path / "output/story_video/EP99"
    artifacts = (
        root / "01_frames/S1.png",
        root / "02_video/S1.mp4",
        root / "EP99_final.mp4",
    )
    for artifact in artifacts:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"legacy-media" * 2_000)

    with StoryVideoRunner(tmp_path) as runner:
        runner.import_legacy("EP99", confirmed_existing_artifacts=True)
        runner.review("EP99", "frames", "pass")
        runner.review("EP99", "videos", "pass")
        accepted = runner.review("EP99", "final", "pass")
        assert accepted["status"] == "ready_to_deliver"

        artifacts[-1].write_bytes(b"tampered-after-acceptance" * 1_000)
        stale = runner.status("EP99")

    assert stale["status"] == "final_qc_stale"
    assert stale["qc"]["gates"]["final"]["status"] == "stale"
    assert stale["state"]["storyvideo"]["stages"]["assembly"]["status"] == "failed"


def test_pipeline_stops_at_each_human_qc_gate(tmp_path, monkeypatch, fake_media):
    write_episode(tmp_path)
    provider = FakeProvider()

    def assemble(spec, **kwargs):
        final = Path(spec["output_root"]) / "EP99_final.mp4"
        final.write_bytes(b"final-media" * 2000)
        contact = Path(spec["output_root"]) / "x_final_qc.jpg"
        contact.write_bytes(b"contact")
        return AssemblyResult(final, 4.0, 1080, 1920, 24.0, (), contact)

    monkeypatch.setattr("storyvideo.assembly.assemble_episode", assemble)

    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        frames = runner.run("EP99")
        assert frames["status"] == "awaiting_frames_qc"
        assert [type(item) for item in provider.submissions] == [ImageRequest]

        replay = runner.run("EP99")
        assert replay["status"] == "awaiting_frames_qc"
        assert len(provider.submissions) == 1

        runner.review("EP99", "frames", "pass")
        videos = runner.run("EP99")
        assert videos["status"] == "awaiting_videos_qc"
        assert [type(item) for item in provider.submissions] == [ImageRequest, VideoRequest]

        runner.review("EP99", "videos", "pass")
        final = runner.run("EP99")
        assert final["status"] == "awaiting_final_qc"
        assert Path(final["run_dir"], "EP99_final.mp4").is_file()

        accepted = runner.review("EP99", "final", "pass")
        assert accepted["status"] == "ready_to_deliver"


def test_submission_records_real_source_and_model_in_ledger(tmp_path, fake_media):
    """每个 job 提交时账本记下真实来源：老式单来源 provider 记它自报的 name 与项目文件里写死的模型名；
    会答 submission_source 的 provider（RoutedProvider）记它解析后的适配器 id 与模型名；答不上来记空串，不拦提交。"""

    write_episode(tmp_path)
    provider = FakeProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.run("EP99")
        frame = runner.store.get_job("EP99", "frame-S1")
        assert frame["provider"] == "fake" and frame["model"] == "image-model"
        runner.review("EP99", "frames", "pass")
        runner.run("EP99")
        video = runner.store.get_job("EP99", "video-S1")
        assert video["provider"] == "fake" and video["model"] == "video-model"
        assert runner.store.sources_for("EP99") == {
            "image": [{"provider": "fake", "model": "image-model", "count": 1}],
            "video": [{"provider": "fake", "model": "video-model", "count": 1}],
        }

    class RoutedLike(FakeProvider):
        def submission_source(self, request):
            return ("ark_images", "doubao-seedream-4-0-250828") if isinstance(request, ImageRequest) else ("", "")

    class Broken(FakeProvider):
        def submission_source(self, request):
            raise RuntimeError("no settings")

    # 各用一个项目根（各自一本账本）：假 provider 的任务 id 每个实例从 1 数起，同一本账本里会撞 (kind, task_id) 唯一索引
    for name, provider, expected in (("routed", RoutedLike(), ("ark_images", "doubao-seedream-4-0-250828")),
                                     ("broken", Broken(), ("", ""))):
        root = tmp_path / name
        path = write_episode(root)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["image_params"] = {"model": "@image"}   # 别名：老式 provider 记不出模型名，会答的 provider 记解析后的
        path.write_text(json.dumps(value), encoding="utf-8")
        with StoryVideoRunner(root, provider=provider) as runner:
            runner.run("EP99")
            job = runner.store.get_job("EP99", "frame-S1")
            assert (job["provider"], job["model"]) == expected and job["status"] == "succeeded"


def test_workspace_id_reaches_paid_requests_and_dependency_upload(
    tmp_path, fake_media
):
    path = write_episode(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["workspace_id"] = "workspace-locked"
    path.write_text(json.dumps(value), encoding="utf-8")
    provider = FakeProvider()

    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.run("EP99", through="frames")
        assert provider.submissions[0].workspace_id == "workspace-locked"
        runner.review("EP99", "frames", "pass")
        runner.run("EP99", through="videos")

    assert provider.upload_workspaces == ["workspace-locked"]
    assert provider.submissions[1].workspace_id == "workspace-locked"


def test_assembly_uses_default_head_trim_and_speech_detection(tmp_path, monkeypatch, fake_media):
    """成片装配不替换开头裁剪 / 台词检测：两个钩子都交给 assembly 的默认实现。"""

    write_episode(tmp_path)
    assembly_kwargs = {}

    def assemble(spec, **kwargs):
        assembly_kwargs.update(kwargs)
        final = Path(spec["output_root"]) / "EP99_final.mp4"
        final.write_bytes(b"final-media" * 2000)
        contact = Path(spec["output_root"]) / "x_final_qc.jpg"
        contact.write_bytes(b"contact")
        return AssemblyResult(final, 4.0, 1080, 1920, 24.0, (), contact)

    monkeypatch.setattr("storyvideo.assembly.assemble_episode", assemble)

    with StoryVideoRunner(tmp_path, provider=FakeProvider()) as runner:
        runner.run("EP99")
        runner.review("EP99", "frames", "pass")
        runner.run("EP99")
        runner.review("EP99", "videos", "pass")
        result = runner.run("EP99")

    assert result["status"] == "awaiting_final_qc"
    assert assembly_kwargs["create_contact_sheet"] is True
    assert assembly_kwargs.get("trim_head_fn") is None
    assert assembly_kwargs.get("speech_segments_fn") is None


def test_item_scoped_qc_only_releases_the_matching_video(
    tmp_path, fake_media
):
    shots = [
        {
            "id": shot_id,
            "dur": 4,
            "cast": ["su"],
            "lines": [{"who": "su", "en": "Hello.", "zh": "你好。"}],
            "shot": "medium",
            "art": f"Page {shot_id} in a bright room.",
            "motion": "Su breathes gently.",
        }
        for shot_id in ("S1", "S2")
    ]
    write_episode(tmp_path, shots=shots)
    provider = FakeProvider()

    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        partial = runner.run(
            "EP99", only=["S1"], through="frames"
        )
        assert partial["status"] == "frames_partial"
        assert partial["qc"]["gates"]["frames"]["status"] == "pending_review"
        assert set(partial["qc"]["gates"]["frames"]["items"]) == {"S1"}

        with pytest.raises(StoryVideoGatePending, match="technical QC is incomplete"):
            runner.review("EP99", "frames", "pass", reviewer="director")
        still_current = runner.status("EP99")
        assert (
            still_current["qc"]["gates"]["frames"]["items"]["S1"][
                "technical_passed"
            ]
            is True
        )

        reviewed_frame = runner.review(
            "EP99", "frames", "pass", shots=["S1"], reviewer="director"
        )
        assert reviewed_frame["status"] == "frames_partial"
        assert reviewed_frame["qc"]["gates"]["frames"]["status"] == "pending_review"
        assert (
            reviewed_frame["qc"]["gates"]["frames"]["items"]["S1"]["decision"]
            == "pass"
        )

        partial_video = runner.run(
            "EP99", only=["S1"], through="videos"
        )
        assert partial_video["status"] == "videos_partial"
        assert set(partial_video["qc"]["gates"]["videos"]["items"]) == {"S1"}

        reviewed_video = runner.review(
            "EP99", "videos", "pass", shots=["S1"], reviewer="director"
        )
        assert reviewed_video["status"] == "videos_partial"
        assert reviewed_video["qc"]["gates"]["videos"]["status"] == "pending_review"

        jobs = {
            job["step_id"]: job
            for job in reviewed_video["state"]["jobs"]
        }

    assert [type(request) for request in provider.submissions] == [
        ImageRequest,
        VideoRequest,
    ]
    assert jobs["frame-S1"]["status"] == "succeeded"
    assert jobs["video-S1"]["status"] == "succeeded"
    assert jobs["frame-S2"]["status"] == "prepared"
    assert jobs["video-S2"]["status"] == "prepared"
    assert "S2" not in reviewed_frame["qc"]["gates"]["frames"]["items"]
    assert "S2" not in reviewed_video["qc"]["gates"]["videos"]["items"]


def test_download_candidate_name_is_sanitized_from_task_id(tmp_path, fake_media):
    """任务 id 会进临时文件名：``<前缀>:<uuid>`` 这类带冒号/斜杠的 id 在 Windows 上不是合法文件名
    （实际遇到过：带冒号的任务 id 让下载全卡在 WinError 87）——只留字母数字和 ._-。"""

    write_episode(tmp_path)

    class CapturingProvider(FakeProvider):
        def __init__(self):
            super().__init__()
            self.destinations: list[Path] = []

        def download(self, url, destination, **kwargs):
            self.destinations.append(Path(destination))
            return super().download(url, destination, **kwargs)

    provider = CapturingProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        spec = runner.prepare("EP99")
        job = dict(runner.store.get_job("EP99", "frame-S1"))
        job.update({"task_id": "exasync-vid:abc/def", "result_url": "https://example.test/1", "attempt": 1})
        runner._download(spec, "frames", "S1", job)

    assert len(provider.destinations) == 1
    name = provider.destinations[0].name
    assert ":" not in name and "/" not in name and "\\" not in name
    assert name.startswith(".S1.") and name.endswith(".candidate.png") and "exasync-vid-abc-def" in name
    assert (tmp_path / "output/story_video/EP99/01_frames/S1.png").is_file()


def test_pending_task_is_polled_on_resume_and_never_resubmitted(
    tmp_path, fake_media
):
    write_episode(tmp_path)
    provider = FakeProvider(pending=True)
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        first = runner.run("EP99", wait=False)
        second = runner.run("EP99", wait=False)

        job = runner.store.get_job("EP99", "frame-S1")

    assert first["status"] == second["status"] == "frames_partial"
    assert len(provider.submissions) == 1
    assert len(provider.polls) == 2
    assert job["task_id"] == "image-task-1"
    assert job["status"] == "processing"


def test_paid_processing_task_recovers_before_new_submit_capability_preflight(
    tmp_path, fake_media
):
    write_episode(tmp_path)

    class CapabilityOutageProvider(FakeProvider):
        name = "fake"

        def __init__(self):
            super().__init__()
            self.capability_calls = 0

        def list_tools(self):
            self.capability_calls += 1
            raise ProviderUnavailable("capability service is unavailable")

        def get_task(self, task_id, task_type):
            normalized = TaskType(task_type)
            self.polls.append((task_id, normalized))
            return TaskSnapshot(
                task_id,
                normalized,
                TaskState.FINISHED,
                output_urls=("https://example.test/already-paid-frame",),
            )

    provider = CapabilityOutageProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.prepare("EP99")
        runner.store.mark_submitting("EP99", "frame-S1")
        runner.store.mark_submitted(
            "EP99", "frame-S1", "paid-image-task", "processing"
        )

        result = runner.run("EP99")
        job = runner.store.get_job("EP99", "frame-S1")

    assert result["status"] == "awaiting_frames_qc"
    assert provider.capability_calls == 0
    assert provider.polls == [("paid-image-task", TaskType.IMAGE)]
    assert provider.submissions == []
    assert job["status"] == "succeeded"


def test_ambiguous_paid_intent_reconciles_before_capability_preflight(
    tmp_path, fake_media
):
    write_episode(tmp_path)

    class CapabilityOutageHistoryProvider(FakeProvider):
        name = "fake"

        def __init__(self):
            super().__init__()
            self.expected_name = ""
            self.capability_calls = 0
            self.history_calls = 0

        def list_tools(self):
            self.capability_calls += 1
            raise ProviderUnavailable("capability service is unavailable")

        def list_tasks(self, **_kwargs):
            self.history_calls += 1
            return (
                RemoteTask(
                    request_id="recovered-paid-task",
                    name=self.expected_name,
                    task_type="image",
                    status="Processing",
                    source="api",
                    created_at="2026-08-05T10:00:00Z",
                ),
            )

        def get_task(self, task_id, task_type):
            normalized = TaskType(task_type)
            self.polls.append((task_id, normalized))
            return TaskSnapshot(
                task_id,
                normalized,
                TaskState.FINISHED,
                output_urls=("https://example.test/recovered-paid-frame",),
            )

    provider = CapabilityOutageHistoryProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        spec = runner.prepare("EP99")
        runner.store.mark_submitting("EP99", "frame-S1")
        ambiguous = runner.store.get_job("EP99", "frame-S1")
        provider.expected_name = runner._remote_identity(
            spec, "frame-S1", ambiguous
        )[0]

        result = runner.run("EP99")
        job = runner.store.get_job("EP99", "frame-S1")

    assert result["status"] == "awaiting_frames_qc"
    assert provider.history_calls == 1
    assert provider.capability_calls == 0
    assert provider.polls == [("recovered-paid-task", TaskType.IMAGE)]
    assert provider.submissions == []
    assert job["status"] == "succeeded"


def test_unknown_submit_outcome_blocks_all_automatic_retries(tmp_path, fake_media):
    write_episode(tmp_path)
    provider = FakeProvider(submit_error=RuntimeError("connection ended after write"))
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        with pytest.raises(StoryVideoBlocked, match="refusing retry"):
            runner.run("EP99")
        job = runner.store.get_job("EP99", "frame-S1")
        assert job["status"] == "submitting"
        assert job["ambiguous_submission"] is True

        with pytest.raises(StoryVideoBlocked, match="reconcile"):
            runner.run("EP99")

    assert len(provider.submissions) == 1


def test_ambiguous_submit_is_adopted_from_unique_remote_history_on_resume(
    tmp_path, fake_media
):
    write_episode(tmp_path)

    class RecoveringProvider(FakeProvider):
        def __init__(self):
            super().__init__(submit_error=RuntimeError("connection ended after write"))
            self.history_queries: list[dict] = []

        def list_tasks(self, **kwargs):
            self.history_queries.append(kwargs)
            return (
                RemoteTask(
                    request_id="recovered-image-task",
                    name=self.submissions[0].name,
                    task_type="image",
                    status="Processing",
                    source="api",
                    created_at="2026-08-05T10:00:00Z",
                ),
                # 同名、但是别处提交的那一条：提供方的身份规则不认它，否则就成了两条匹配、不敢接管
                RemoteTask(
                    request_id="other-client-task",
                    name=self.submissions[0].name,
                    task_type="image",
                    status="Processing",
                    source="other",
                    created_at="2026-08-05T10:00:01Z",
                ),
            )

        def match_remote(self, item, *, expected_name, task_type, task_id=None):
            return item.source == "api"

        def get_task(self, task_id, task_type):
            normalized = TaskType(task_type)
            self.polls.append((task_id, normalized))
            return TaskSnapshot(
                task_id,
                normalized,
                TaskState.FINISHED,
                output_urls=("https://example.test/recovered-frame",),
            )

    provider = RecoveringProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        with pytest.raises(StoryVideoBlocked, match="refusing retry"):
            runner.run("EP99")

    # Simulate a fresh runner process: durable local state must be reconciled before
    # any new submit attempt is allowed.
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        result = runner.run("EP99")
        job = runner.store.get_job("EP99", "frame-S1")

    assert result["status"] == "awaiting_frames_qc"
    assert len(provider.submissions) == 1
    assert provider.polls == [("recovered-image-task", TaskType.IMAGE)]
    assert job["task_id"] == "recovered-image-task"
    assert job["status"] == "succeeded"
    assert len(provider.history_queries) == 1
    query = provider.history_queries[0]
    assert query["keyword"] == provider.submissions[0].name
    assert "-a001-" in query["keyword"]
    assert query["task_types"] == ("image",)
    assert query["start_date"] == query["end_date"]
    assert query["page"] == 0
    assert query["page_size"] == 100


def test_manual_reconcile_verifies_exact_remote_identity(tmp_path, fake_media):
    write_episode(tmp_path)

    class HistoryProvider(FakeProvider):
        def __init__(self):
            super().__init__(submit_error=RuntimeError("connection ended after write"))
            self.remote: tuple[RemoteTask, ...] = ()
            self.matched: list[dict] = []

        def list_tasks(self, **kwargs):
            return self.remote

        def match_remote(self, item, *, expected_name, task_type, task_id=None):
            # 来源自己的身份规则：只认本程序提交的（别处提交的同名任务不算）
            self.matched.append({"expected_name": expected_name, "task_type": task_type, "task_id": task_id})
            return item.source == "api"

    provider = HistoryProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        with pytest.raises(StoryVideoBlocked):
            runner.run("EP99")
        expected_name = provider.submissions[0].name
        provider.remote = (
            RemoteTask("wrong-task", expected_name, "image", "Processing", "other"),
        )
        with pytest.raises(StoryVideoBlocked, match="does not uniquely match"):
            runner.reconcile("EP99", "frame-S1", "wrong-task")
        # 类型对不上的，核心就不认（不用问提供方）
        provider.remote = (
            RemoteTask("video-task", expected_name, "video", "Processing", "api"),
        )
        with pytest.raises(StoryVideoBlocked, match="does not uniquely match"):
            runner.reconcile("EP99", "frame-S1", "video-task")

        provider.remote = (
            RemoteTask("right-task", expected_name, "image", "Processing", "api"),
        )
        reconciled = runner.reconcile("EP99", "frame-S1", "right-task")
        job = runner.store.get_job("EP99", "frame-S1")

    assert reconciled["status"] == "waiting_remote"
    assert job["task_id"] == "right-task"
    assert provider.matched[-1] == {"expected_name": expected_name, "task_type": "image", "task_id": "right-task"}


def test_reconcile_without_a_provider_rule_matches_on_id_and_type(tmp_path, fake_media):
    """provider 没有 match_remote：核心只按任务 id（或任务名）+ 类型认，仍然只接管唯一匹配。"""

    write_episode(tmp_path)

    class PlainHistoryProvider(FakeProvider):
        def __init__(self):
            super().__init__(submit_error=RuntimeError("connection ended after write"))
            self.remote: tuple[RemoteTask, ...] = ()

        def list_tasks(self, **kwargs):
            return self.remote

    provider = PlainHistoryProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        with pytest.raises(StoryVideoBlocked):
            runner.run("EP99")
        expected_name = provider.submissions[0].name
        provider.remote = (RemoteTask("t-1", expected_name, "image", "Processing"),)
        assert runner.reconcile("EP99", "frame-S1", "t-1")["status"] == "waiting_remote"
        assert runner.store.get_job("EP99", "frame-S1")["task_id"] == "t-1"


def test_ambiguous_intent_can_only_be_abandoned_after_empty_history_confirmation(
    tmp_path, fake_media
):
    write_episode(tmp_path)

    class EmptyHistoryProvider(FakeProvider):
        def list_tasks(self, **kwargs):
            return ()

    provider = EmptyHistoryProvider(
        submit_error=RuntimeError("crash before transport confirmation")
    )
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        with pytest.raises(StoryVideoBlocked):
            runner.run("EP99")
        with pytest.raises(StoryVideoError, match="explicit confirmation"):
            runner.abandon(
                "EP99",
                "frame-S1",
                reason="history is empty",
                confirmed_not_submitted=False,
            )
        abandoned = runner.abandon(
            "EP99",
            "frame-S1",
            reason="history search confirmed no remote task",
            confirmed_not_submitted=True,
        )
        job = runner.store.get_job("EP99", "frame-S1")

    assert abandoned["status"] == "submission_abandoned"
    assert job["status"] == "prepared"
    assert job["attempt"] == 1


def test_quota_failure_stops_before_later_shots_are_submitted(tmp_path, fake_media):
    shots = []
    for shot_id in ("S1", "S2"):
        shots.append(
            {
                "id": shot_id,
                "dur": 4,
                "cast": ["su"],
                "lines": [{"en": "Hi.", "zh": "嗨。"}],
                "shot": "medium",
                "art": "A room.",
                "motion": "Su talks.",
            }
        )
    write_episode(tmp_path, shots=shots)
    provider = FakeProvider(submit_error=QuotaExhausted("HTTP 402"))

    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        with pytest.raises(Exception, match="402"):
            runner.run("EP99")
        untouched = runner.store.get_job("EP99", "frame-S2")

    assert len(provider.submissions) == 1
    assert untouched["status"] == "prepared"


def test_definitive_failure_requires_explicit_retry_before_resubmission(
    tmp_path, fake_media
):
    write_episode(tmp_path)
    provider = FakeProvider(submit_error=QuotaExhausted("HTTP 402"))
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        with pytest.raises(StoryVideoError, match="402"):
            runner.run("EP99")
        failed = runner.store.get_job("EP99", "frame-S1")
        assert failed["status"] == "failed"

        # Merely resuming is fail-closed and cannot spend another attempt.
        runner.run("EP99")
        assert len(provider.submissions) == 1

        provider.submit_error = None
        reset = runner.retry(
            "EP99", step_ids=["frame-S1"], reason="balance was replenished"
        )
        assert reset["status"] == "retry_requested"
        completed = runner.run("EP99")
        retried = runner.store.get_job("EP99", "frame-S1")

    assert completed["status"] == "awaiting_frames_qc"
    assert len(provider.submissions) == 2
    assert retried["attempt"] == 2
    assert "-a002-" in provider.submissions[-1].name


def test_invalid_candidate_download_never_overwrites_existing_take(
    tmp_path, monkeypatch, fake_media
):
    write_episode(tmp_path)
    provider = FakeProvider()
    canonical = tmp_path / "output/story_video/EP99/01_frames/S1.png"

    real_fake = fake_info

    def reject_candidate(path, kind="video", **kwargs):
        if ".candidate" in Path(path).name:
            raise MediaValidationError("truncated")
        return real_fake(path, kind, **kwargs)

    monkeypatch.setattr("storyvideo.runtime.validate_media", reject_candidate)
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.prepare("EP99")
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"known-good-old-take")
        job = runner.store.get_job("EP99", "frame-S1")
        runner.store.mark_submitting("EP99", "frame-S1")
        runner.store.mark_submitted(
            "EP99", "frame-S1", "task-existing", "finished", "https://example.test/out"
        )
        job = runner.store.get_job("EP99", "frame-S1")

        with pytest.raises(Exception, match="truncated"):
            runner._download(runner.prepare("EP99"), "frames", "S1", job)

    assert canonical.read_bytes() == b"known-good-old-take"


def test_installed_artifact_is_adopted_after_db_commit_crash(
    tmp_path, monkeypatch, fake_media
):
    write_episode(tmp_path)
    provider = FakeProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        original = runner.store.mark_succeeded
        failed_once = False

        def crash_after_install(*args, **kwargs):
            nonlocal failed_once
            if not failed_once and args[1] == "frame-S1":
                failed_once = True
                raise RuntimeError("simulated crash before DB commit")
            return original(*args, **kwargs)

        monkeypatch.setattr(runner.store, "mark_succeeded", crash_after_install)
        with pytest.raises(RuntimeError, match="simulated crash"):
            runner.run("EP99")
        assert len(provider.downloads) == 1
        assert runner.store.get_job("EP99", "frame-S1")["status"] == "finished"

        monkeypatch.setattr(runner.store, "mark_succeeded", original)
        resumed = runner.run("EP99")
        job = runner.store.get_job("EP99", "frame-S1")

    assert resumed["status"] == "awaiting_frames_qc"
    assert job["status"] == "succeeded"
    assert len(provider.downloads) == 1


def test_multihop_ref_frames_wait_for_each_dependency_wave(tmp_path, fake_media):
    base = {
        "dur": 4,
        "cast": ["su"],
        "lines": [{"en": "Hi.", "zh": "嗨。"}],
        "shot": "medium",
        "art": "A room.",
        "motion": "Su talks.",
    }
    shots = [
        {"id": "S1", **base},
        {"id": "S2", "ref_frame": "S1", **base},
        {"id": "S3", "ref_frame": "S2", **base},
    ]
    write_episode(tmp_path, shots=shots)
    provider = FakeProvider(pending=True)

    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.run("EP99", wait=False)

    assert len(provider.submissions) == 1
    assert provider.submissions[0].name.startswith("EP99-S1-frame-a001-")


def test_frame_retake_cascades_to_dependent_frames_videos_and_final(
    tmp_path, monkeypatch, fake_media
):
    base = {
        "dur": 4,
        "cast": ["su"],
        "lines": [{"en": "Hi.", "zh": "嗨。"}],
        "shot": "medium",
        "art": "A room.",
        "motion": "Su talks.",
    }
    write_episode(
        tmp_path,
        shots=[{"id": "S1", **base}, {"id": "S2", "ref_frame": "S1", **base}],
    )
    provider = FakeProvider()

    def assemble(spec, **kwargs):
        final = Path(spec["output_root"]) / "EP99_final.mp4"
        final.write_bytes(b"final-media" * 2000)
        contact = Path(spec["output_root"]) / "x_final_qc.jpg"
        contact.write_bytes(b"contact")
        return AssemblyResult(final, 8.0, 1080, 1920, 24.0, (), contact)

    monkeypatch.setattr("storyvideo.assembly.assemble_episode", assemble)
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.run("EP99")
        runner.review("EP99", "frames", "pass")
        runner.run("EP99")
        runner.review("EP99", "videos", "pass")
        runner.run("EP99")
        runner.review("EP99", "final", "pass")

        result = runner.review(
            "EP99",
            "frames",
            "retake",
            shots=["S1"],
            reason="reference pose drifted",
        )
        jobs = {job["step_id"]: job for job in result["state"]["jobs"]}

        assert jobs["frame-S1"]["status"] == "prepared"
        assert jobs["frame-S2"]["status"] == "prepared"
        assert jobs["video-S1"]["status"] == "prepared"
        assert jobs["video-S2"]["status"] == "prepared"
        assert jobs["assemble"]["status"] == "prepared"
        qc = load_qc(Path(result["run_dir"]) / "manifests" / "qc.json")
        assert qc["gates"]["frames"]["status"] == "not_ready"
        assert qc["gates"]["videos"]["status"] == "not_ready"
        assert qc["gates"]["final"]["status"] == "not_ready"
        assert "retake_pending" not in qc["gates"]["frames"]
        assert qc["gates"]["frames"]["items"]["S1"]["technical_passed"] is False
        assert qc["gates"]["frames"]["items"]["S2"]["technical_passed"] is False
        with pytest.raises(StoryVideoGatePending, match="run technical QC"):
            runner.review("EP99", "frames", "pass")


def test_accepted_gate_is_closed_when_artifact_hash_changes(
    tmp_path, fake_media
):
    write_episode(tmp_path)
    provider = FakeProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.run("EP99")
        runner.review("EP99", "frames", "pass")
        frame = tmp_path / "output/story_video/EP99/01_frames/S1.png"
        frame.write_bytes(b"externally-edited-frame" * 1000)

        result = runner.run("EP99")
        qc = result["qc"]

    assert result["status"] == "awaiting_frames_qc"
    assert len(provider.submissions) == 1
    assert qc["gates"]["frames"]["status"] == "pending_review"
    assert qc["gates"]["frames"]["items"]["S1"]["decision"] is None
    assert qc["gates"]["videos"]["status"] == "not_ready"
    assert qc["gates"]["final"]["status"] == "not_ready"


def test_final_retake_is_rejected_before_qc_is_changed(
    tmp_path, monkeypatch, fake_media
):
    write_episode(tmp_path)
    provider = FakeProvider()

    def assemble(spec, **kwargs):
        final = Path(spec["output_root"]) / "EP99_final.mp4"
        final.write_bytes(b"final-media" * 2000)
        return AssemblyResult(final, 4.0, 1080, 1920, 24.0, (), None)

    monkeypatch.setattr("storyvideo.assembly.assemble_episode", assemble)
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.run("EP99")
        runner.review("EP99", "frames", "pass")
        runner.run("EP99")
        runner.review("EP99", "videos", "pass")
        runner.run("EP99")
        before = runner.status("EP99")
        with pytest.raises(StoryVideoError, match="cannot be retaken"):
            runner.review("EP99", "final", "retake", reason="bad ending")
        after = runner.status("EP99")

    assert before["state"]["storyvideo"] == after["state"]["storyvideo"]


# --------------------------------------------------------------------------- 大首帧的上传代理图（契约 E）
# 路由来源的 upload_asset 只登记 local:<路径> 令牌、不读文件，真正读首帧在提交时（适配器自己上传）；
# 以前 >2.5MB 的首帧在临时目录里缩成代理图，with 块一结束代理图就没了，提交报 reference image does not exist，
# 被当成结果未知 → 整次运行 blocked_submission_unknown。

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")


def _noise_png(path: Path, size: tuple[int, int] = (1300, 2000)) -> Path:
    """随机噪声几乎压不动：1300×2000 的 PNG 约 8MB，缩到长边 1920 的 JPEG 在最好画质下也超过 2.5MB——正好逼出逐档降画质。"""

    from PIL import Image

    width, height = size
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGB", size, os.urandom(width * height * 3)).save(path, compress_level=1)
    return path


class LocalTokenProvider(FakeProvider):
    """学 RoutedProvider：上传只发 local: 令牌，提交视频时才按令牌读首帧；首帧下载成一张 >2.5MB 的真 PNG。"""

    def upload_asset(self, path, *, asset_type="image", workspace_id=None):
        self.uploads.append(Path(path))
        self.upload_workspaces.append(workspace_id)
        return f"local:{Path(path).resolve()}"

    def submit(self, request):
        if isinstance(request, VideoRequest):
            for ref in request.references:
                target = Path(ref.asset_id[len("local:"):])
                if not target.is_file():
                    raise ProviderCommandError(f"reference image does not exist: {target}")
        return super().submit(request)

    def download(self, url, destination, **kwargs):
        target = Path(destination)
        if target.suffix != ".png":
            return super().download(url, destination, **kwargs)
        self.downloads.append(url)
        return _noise_png(target)


@needs_ffmpeg
def test_large_first_frame_proxy_survives_until_video_submission(tmp_path, fake_media):
    from PIL import Image

    from storyvideo.prompts import video_request_data
    from storyvideo.state import input_fingerprint

    write_episode(tmp_path)
    provider = LocalTokenProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.run("EP99", through="frames")
        frame = tmp_path / "output/story_video/EP99/01_frames/S1.png"
        assert frame.stat().st_size > 2_500_000
        runner.review("EP99", "frames", "pass")
        result = runner.run("EP99", through="videos")
        spec = runner.prepare("EP99")
        job = runner.store.get_job("EP99", "video-S1")

    assert result["status"] == "awaiting_videos_qc"
    video = provider.submissions[-1]
    assert isinstance(video, VideoRequest)
    proxy = Path(video.references[0].asset_id[len("local:"):])
    # 代理图落在持久目录、按源文件内容哈希命名，提交之后也还在（中断后恢复的下一次运行还要按令牌读它）
    assert proxy.is_file() and proxy.parent == frame.parent / "_upload"
    assert re.fullmatch(r"S1-[0-9a-f]{8}\.jpg", proxy.name)
    assert proxy.stat().st_size <= 2_500_000
    with Image.open(proxy) as image:
        assert max(image.size) <= 1920
    # 代理图只换上传的字节：账本里的请求指纹仍是按 spec 算的那一个
    assert job["request_hash"] == input_fingerprint("video", video_request_data(spec, "S1"))


def _spy_ffmpeg(monkeypatch) -> list[list[str]]:
    from storyvideo import runtime as R

    calls: list[list[str]] = []
    real = R._run_checked

    def spy(command, **kwargs):
        calls.append(list(command))
        return real(command, **kwargs)

    monkeypatch.setattr(R, "_run_checked", spy)
    return calls


@needs_ffmpeg
def test_upload_proxy_is_persistent_capped_and_reused(tmp_path, monkeypatch):
    from PIL import Image

    from storyvideo import runtime as R
    from storyvideo.qc import artifact_sha256

    source = _noise_png(tmp_path / "01_frames" / "S1.png")
    assert source.stat().st_size > R._DIRECT_IMAGE_UPLOAD_LIMIT_BYTES
    proxy_dir = tmp_path / "01_frames" / "_upload"
    calls = _spy_ffmpeg(monkeypatch)

    proxy = R._upload_ready_image(source, ffmpeg="ffmpeg", proxy_dir=proxy_dir)

    # 函数返回之后文件还在（以前是 with 块里的临时文件）；按源内容哈希命名
    assert proxy.is_file() and proxy.parent == proxy_dir
    assert proxy.name == f"S1-{artifact_sha256(source)[:8]}.jpg"
    assert 0 < proxy.stat().st_size <= 2_500_000
    with Image.open(proxy) as image:
        assert image.format == "JPEG" and image.size == (1248, 1920)   # 长边封顶 1920，比例不变
    # 噪声图在最好画质下超上限，逐档往下试到落进上限为止
    qualities = [cmd[cmd.index("-q:v") + 1] for cmd in calls]
    assert len(qualities) > 1 and qualities == [str(q) for q in R._UPLOAD_PROXY_QUALITIES[: len(qualities)]]
    assert sorted(p.name for p in proxy_dir.iterdir()) == [proxy.name]   # 临时写的半成品不留下

    # 同一张图第二次上传（下一镜 ref_frame）直接复用，不再跑 ffmpeg
    calls.clear()
    assert R._upload_ready_image(source, ffmpeg="ffmpeg", proxy_dir=proxy_dir) == proxy
    assert calls == []

    # 首帧重拍、内容变了：换新名字，不拿旧代理去提交
    _noise_png(source)
    again = R._upload_ready_image(source, ffmpeg="ffmpeg", proxy_dir=proxy_dir)
    assert again != proxy and again.name == f"S1-{artifact_sha256(source)[:8]}.jpg" and again.is_file()


@needs_ffmpeg
def test_upload_proxy_keeps_narrow_width_and_small_frames_pass_through(tmp_path, monkeypatch):
    from PIL import Image

    from storyvideo import runtime as R

    proxy_dir = tmp_path / "_upload"
    small = tmp_path / "small.png"
    Image.new("RGB", (1080, 1920), (40, 80, 120)).save(small)
    assert small.stat().st_size <= R._DIRECT_IMAGE_UPLOAD_LIMIT_BYTES
    assert R._upload_ready_image(small, ffmpeg="ffmpeg", proxy_dir=proxy_dir) == small
    assert not proxy_dir.exists()   # 小图原样上传，不建代理目录

    # 原图长边不到 1920 就不放大；上限调小了逼出逐档降画质
    monkeypatch.setattr(R, "_DIRECT_IMAGE_UPLOAD_LIMIT_BYTES", 200_000)
    narrow = _noise_png(tmp_path / "narrow.png", (600, 900))
    proxy = R._upload_ready_image(narrow, ffmpeg="ffmpeg", proxy_dir=proxy_dir)
    assert proxy.stat().st_size <= 200_000
    with Image.open(proxy) as image:
        assert image.size == (600, 900)


@needs_ffmpeg
@pytest.mark.parametrize(("size", "expected"), [((2560, 1440), (1920, 1080)), ((1440, 2560), (1080, 1920))])
def test_upload_proxy_caps_the_long_side_so_landscape_keeps_1080p(tmp_path, size, expected):
    """按长边封顶 1920：9:16 竖屏跟「宽 ≤1080」一样，16:9 横屏不会被缩成 1080×608、比 1080p 的成片还小。"""

    from PIL import Image

    from storyvideo import runtime as R

    source = _noise_png(tmp_path / "01_frames" / "S1.png", size)
    assert source.stat().st_size > R._DIRECT_IMAGE_UPLOAD_LIMIT_BYTES
    proxy = R._upload_ready_image(source, ffmpeg="ffmpeg", proxy_dir=tmp_path / "01_frames" / "_upload")
    assert proxy.stat().st_size <= R._DIRECT_IMAGE_UPLOAD_LIMIT_BYTES
    with Image.open(proxy) as image:
        assert image.size == expected


@needs_ffmpeg
def test_upload_proxy_that_never_fits_fails_in_chinese_and_leaves_nothing(tmp_path, monkeypatch):
    from storyvideo import runtime as R

    monkeypatch.setattr(R, "_DIRECT_IMAGE_UPLOAD_LIMIT_BYTES", 1_000)
    monkeypatch.setattr(R, "_UPLOAD_PROXY_QUALITIES", (31,))
    source = _noise_png(tmp_path / "S9.png", (400, 400))
    proxy_dir = tmp_path / "_upload"

    with pytest.raises(StoryVideoError, match="上传用的代理图压到最低画质仍有 \\d+ 字节，超过 1000 字节的上传上限：S9.png") as info:
        R._upload_ready_image(source, ffmpeg="ffmpeg", proxy_dir=proxy_dir)
    assert list(proxy_dir.iterdir()) == []   # 半成品删掉，也没有留下一个超限的「代理」

    # 界面上看到的是 _upload_dependency 包一层、humanize 成「人话（原文）」的样子；英文界面整句不剩中文
    from webui import i18n
    from webui.humanize import humanize

    shown = humanize(f"failed to upload dependency for videos/S9: {info.value}")
    assert shown.startswith("S9 的参考图没能编码/上传：上传用的代理图压到最低画质仍有")
    i18n._compiled = None
    english = i18n.tr(shown, "en")
    assert not re.search(r"[一-鿿]", english), english


# 升级前已经被卡住的项目：老代码把指向临时目录的令牌记进了账本（asset_uploaded），提交时文件早没了 → 结果未知。
# 放弃（abandon）之后 job 回到 asset_uploaded、失效令牌原样保留；重跑必须在提交前认出来、丢掉重传，否则放弃—重跑—又卡住是死循环。


class _StaleTokenProvider(LocalTokenProvider):
    """``stale`` 时学老代码：上传登记一个指向已删除临时目录的 local: 令牌；首帧用 FakeProvider 的小文件（不需要 ffmpeg）。"""

    def __init__(self, gone: Path):
        super().__init__()
        self.gone = gone
        self.stale = True

    def list_tasks(self, **kwargs):
        return ()   # 服务商那边查不到这次提交：本地读文件就失败了，请求根本没发出去

    def upload_asset(self, path, *, asset_type="image", workspace_id=None):
        if self.stale:
            self.uploads.append(Path(path))
            return f"local:{self.gone.resolve()}"
        return super().upload_asset(path, asset_type=asset_type, workspace_id=workspace_id)

    def download(self, url, destination, **kwargs):
        return FakeProvider.download(self, url, destination, **kwargs)


def test_abandoned_job_with_stale_temp_proxy_token_is_reuploaded(tmp_path, fake_media):
    write_episode(tmp_path)
    provider = _StaleTokenProvider(tmp_path / "gone" / "storyvideo-upload-x" / "S1.jpg")
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.run("EP99", through="frames")
        runner.review("EP99", "frames", "pass")
        with pytest.raises(StoryVideoBlocked, match="reference image does not exist"):
            runner.run("EP99", through="videos")
        runner.abandon("EP99", "video-S1", reason="history empty", confirmed_not_submitted=True)
        stuck = runner.store.get_job("EP99", "video-S1")
        assert stuck["status"] == "asset_uploaded" and "storyvideo-upload-x" in stuck["asset_id"]

        provider.stale = False   # 升级到修好的代码后重跑
        result = runner.run("EP99", through="videos")
        job = runner.store.get_job("EP99", "video-S1")

    assert result["status"] == "awaiting_videos_qc", result["status"]
    frame = (tmp_path / "output/story_video/EP99/01_frames/S1.png").resolve()
    # 提交前丢掉失效令牌、重传；提交用的是还在的首帧
    assert provider.uploads[-1] == frame
    video = provider.submissions[-1]
    assert isinstance(video, VideoRequest) and video.references[0].asset_id == f"local:{frame}"
    assert job["status"] == "succeeded" and job["attempt"] == 2   # 放弃掉的那次仍计数，下一次换新的任务名


def test_missing_local_dependency_only_flags_vanished_local_files(tmp_path):
    present = tmp_path / "S1.jpg"
    present.write_bytes(b"x")
    gone = tmp_path / "gone.jpg"
    check = StoryVideoRunner._missing_local_dependency
    assert check({"status": "asset_uploaded", "asset_id": f"local:{gone}"}) == gone
    assert check({"status": "asset_uploaded", "asset_id": f"local:{present}"}) is None
    # 远端素材 id 不是本机路径；还没上传 / 已经提交的 job 不归这里管
    assert check({"status": "asset_uploaded", "asset_id": "upload-S1"}) is None
    assert check({"status": "prepared", "asset_id": None}) is None
    assert check({"status": "submitting", "asset_id": f"local:{gone}"}) is None


def test_local_dependency_deleted_between_upload_and_submit_is_reuploaded(tmp_path, fake_media, monkeypatch):
    """登记上传之后、写下提交意图之前进程被杀，之后有人清掉了令牌指向的文件（例如删了 01_frames/_upload）：
    下一次运行在提交前丢掉失效的上传、重传，不会拿失效令牌去提交、卡成 blocked_submission_unknown。"""

    write_episode(tmp_path)
    proxies = tmp_path / "proxies"

    class ProxyingProvider(LocalTokenProvider):
        def upload_asset(self, path, *, asset_type="image", workspace_id=None):
            # 像代理图一样：令牌指向的是另一份文件，不是首帧本身
            proxies.mkdir(exist_ok=True)
            copy = proxies / f"{Path(path).stem}.jpg"
            shutil.copyfile(path, copy)
            return super().upload_asset(copy, asset_type=asset_type, workspace_id=workspace_id)

        def download(self, url, destination, **kwargs):
            return FakeProvider.download(self, url, destination, **kwargs)

    provider = ProxyingProvider()
    with StoryVideoRunner(tmp_path, provider=provider) as runner:
        runner.run("EP99", through="frames")
        runner.review("EP99", "frames", "pass")

        def killed(*args, **kwargs):
            raise KeyboardInterrupt   # 进程停在「登记上传」和「写提交意图」之间

        with monkeypatch.context() as patch:
            patch.setattr(runner.store, "mark_submitting", killed)
            with pytest.raises(KeyboardInterrupt):
                runner.run("EP99", through="videos")
        stuck = runner.store.get_job("EP99", "video-S1")
        assert stuck["status"] == "asset_uploaded" and stuck["attempt"] == 0
        shutil.rmtree(proxies)

        result = runner.run("EP99", through="videos")
        job = runner.store.get_job("EP99", "video-S1")

    assert result["status"] == "awaiting_videos_qc", result["status"]
    video = provider.submissions[-1]
    assert isinstance(video, VideoRequest)
    assert Path(video.references[0].asset_id[len("local:"):]).is_file()
    assert job["status"] == "succeeded" and job["attempt"] == 1   # 丢掉的只是没提交的上传，不占一次付费尝试
