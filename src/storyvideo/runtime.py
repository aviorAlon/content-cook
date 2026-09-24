"""Resumable, QC-gated production runtime for multi-shot StoryVideo episodes."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any

from genkit.providers import (
    path_to_file_url,
    AssetReference,
    GenerationProvider,
    ImageRequest,
    ProviderError,
    ProviderNotConfigured,
    ProviderRejected,
    ProviderUnavailable,
    QuotaExhausted,
    TaskState,
    TaskType,
    VideoRequest,
)
from genkit.store import (
    RunCollisionError,
    RunStore,
)

from .media import MediaValidationError, validate_media
from .prompts import (
    compile_frame_prompt,
    compile_negative_prompt,
    compile_video_prompt,
    frame_reference_asset_ids,
    frame_request_data,
    video_request_data,
)
from .qc import (
    GATES,
    artifact_sha256,
    begin_retake,
    complete_retake,
    invalidate as invalidate_qc,
    load_qc,
    normalize_defect,
    normalize_disposition,
    record_technical_results,
    review as review_qc,
)
from .spec import load_spec, stable_json_hash
from . import tail as _tail
from .state import (
    atomic_write_json,
    build_state_view,
    export_state,
    input_fingerprint,
    make_step_id,
    read_json,
)


class StoryVideoError(RuntimeError):
    """Base error for safe StoryVideo production."""


class StoryVideoBlocked(StoryVideoError):
    """The runner stopped because automatic retry could duplicate paid work."""


class StoryVideoGatePending(StoryVideoError):
    """A downstream stage was requested before its QC gate was accepted."""


class StoryVideoPollErrors(StoryVideoError):
    """One or more in-flight jobs failed to poll/download; the others were still advanced.

    2026-09-08：后台轮询一轮里 S2 下载失败不该让 S3…S5 这一轮连问都不问。
    ``errors`` 按任务顺序保留每条原始异常，调用方逐条翻成人话。
    """

    def __init__(self, errors: Sequence[StoryVideoError]):
        self.errors = list(errors)
        super().__init__("; ".join(str(error) for error in self.errors))


# 同一份提示词连续原样重投的上限（drama-skills 生产纪律 §五.6：「同一输入连续原样重投不超过两次」）。
# 第 N 次（N > 上限）原样重提要么改文字、要么换模型、要么接受，或者明确 force。
RESUBMIT_LIMIT = 2


class StoryVideoRetryLimit(StoryVideoBlocked):
    """A retake would resubmit the identical request beyond :data:`RESUBMIT_LIMIT`."""

    def __init__(self, message: str, *, shot_id: str, gate: str, attempts: Mapping[str, Any]):
        super().__init__(message)
        self.shot_id = shot_id
        self.gate = gate
        self.attempts = dict(attempts)


def retry_limit_message(shot_id: str, gate: str, same_request: int) -> str:
    what = "首帧" if gate == "frames" else "视频"
    return (
        f"这一镜（{shot_id}）的{what}同一份提示词已经出过 {same_request} 次了——原样重提第 {same_request + 1} 次"
        "通常还是一样；改画面/动作文字（改文字重出）、换模型对比（多模型对比），或接受当前结果"
    )


_LOCK_REGISTRY_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.RLock] = {}
_HELD_LOCKS = threading.local()


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    """Acquire a crash-releasing, non-blocking cross-process file lock."""

    resolved = path.resolve()
    key = str(resolved).casefold() if os.name == "nt" else str(resolved)
    with _LOCK_REGISTRY_GUARD:
        local_lock = _LOCAL_LOCKS.setdefault(key, threading.RLock())
    with local_lock:
        held = getattr(_HELD_LOCKS, "counts", None)
        if held is None:
            held = {}
            _HELD_LOCKS.counts = held
        if held.get(key, 0):
            held[key] += 1
            try:
                yield
            finally:
                held[key] -= 1
            return

        resolved.parent.mkdir(parents=True, exist_ok=True)
        stream = resolved.open("a+b")
        acquired = False
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:  # pragma: no cover - exercised on POSIX CI/development hosts
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StoryVideoBlocked(
                    f"another process owns the production lock: {resolved.name}"
                ) from exc
            acquired = True
            held[key] = 1
            yield
        finally:
            held.pop(key, None)
            if acquired:
                stream.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:  # pragma: no cover - exercised on POSIX CI/development hosts
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            stream.close()


@contextmanager
def production_output_lock(output_root: str | Path) -> Iterator[None]:
    """Serialize preparation, paid execution and QC for one output root."""

    root = Path(output_root).resolve(strict=False)
    with _exclusive_file_lock(root / "manifests" / "production.lock"):
        yield


def _source_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    path = spec.get("spec_path")
    if path:
        try:
            value = json.loads(Path(str(path)).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoryVideoError(f"cannot freeze episode spec {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise StoryVideoError("episode source must be a JSON object")
        return value
    derived = {"project_root", "spec_dir", "spec_path", "waves", "source_hash"}
    value = {key: item for key, item in spec.items() if key not in derived}
    for shot in value.get("shots", []):
        if isinstance(shot, dict):
            shot.pop("source_hash", None)
    return value


def _shot_map(spec: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(shot["id"]): shot for shot in spec["shots"]}


def _safe_ids(values: Iterable[str] | None, known: set[str]) -> set[str] | None:
    if values is None:
        return None
    selected = {str(value).strip() for value in values if str(value).strip()}
    unknown = sorted(selected - known)
    if unknown:
        raise StoryVideoError(f"unknown shot ids: {', '.join(unknown)}")
    if not selected:
        raise StoryVideoError("shot selection must not be empty")
    return selected


def _dependency_closure(spec: Mapping[str, Any], selected: set[str]) -> set[str]:
    shots = _shot_map(spec)
    result = set(selected)
    pending = list(selected)
    while pending:
        shot = shots[pending.pop()]
        ref = shot.get("ref_frame")
        tail = _tail.tail_ref(shot)
        for dep in (str(ref) if ref else None, tail[0] if tail else None):
            if dep and dep not in result:
                result.add(dep)
                pending.append(dep)
    return result


def _dependent_closure(spec: Mapping[str, Any], selected: set[str]) -> set[str]:
    """Return selected shots plus every frame that transitively references them."""

    dependents: dict[str, set[str]] = {shot_id: set() for shot_id in _shot_map(spec)}
    for shot in spec["shots"]:
        ref = shot.get("ref_frame")
        if ref:
            dependents[str(ref)].add(str(shot["id"]))
        # 尾帧续接（DESIGN §30）：源镜首帧重拍 → 源镜视频重出 → 它的尾帧变了，接尾帧的镜也要重来
        tail = _tail.tail_ref(shot)
        if tail and tail[0] in dependents:
            dependents[tail[0]].add(str(shot["id"]))
    result = set(selected)
    pending = list(selected)
    while pending:
        for child in dependents[pending.pop()]:
            if child not in result:
                result.add(child)
                pending.append(child)
    return result


def _attempt_task_name(
    base_name: str,
    job: Mapping[str, Any],
    *,
    next_attempt: bool,
) -> str:
    """Build an attempt-unique remote name for safe history reconciliation."""

    attempt = int(job.get("attempt") or 0) + (1 if next_attempt else 0)
    if attempt < 1:
        raise StoryVideoError("cannot name a remote task before its first attempt")
    fingerprint = str(job.get("request_hash") or "")[:10]
    if not fingerprint:
        raise StoryVideoError("job has no request fingerprint")
    return f"{base_name}-a{attempt:03d}-{fingerprint}"


def _duration_tolerance(seconds: float) -> float:
    return min(2.0, max(0.75, float(seconds) * 0.2))


def _remote_matches(
    provider: Any,
    remote: Iterable[Any],
    *,
    expected_name: str,
    task_type: str,
    task_id: str | None = None,
) -> list[Any]:
    """历史任务里哪些算「这一次提交」：类型一致，给了 ``task_id`` 就要 request_id 精确相等、否则任务名精确相等；
    再问 provider 的 ``match_remote``（可选）——来源自己的身份规则（比如只认本程序提交的）
    由提供方决定。调用方只接管唯一匹配，绝不猜。"""

    matcher = getattr(provider, "match_remote", None)
    out = []
    for item in remote:
        if str(getattr(item, "task_type", "")).lower() != task_type:
            continue
        if task_id is not None:
            if str(getattr(item, "request_id", "")) != task_id:
                continue
        elif str(getattr(item, "name", "")) != expected_name:
            continue
        if callable(matcher) and not matcher(item, expected_name=expected_name, task_type=task_type, task_id=task_id):
            continue
        out.append(item)
    return out


def _run_checked(command: Sequence[str], *, timeout: float = 180.0) -> None:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise StoryVideoError(f"local media command failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "command failed").strip()[-800:]
        raise StoryVideoError(f"local media command failed: {detail}")


_DIRECT_IMAGE_UPLOAD_LIMIT_BYTES = 2_500_000
# 上传代理图放在 01_frames 下的这个子目录（按源文件内容哈希命名，见 _upload_ready_image）
UPLOAD_PROXY_SUBDIR = "_upload"
# 代理图长边最多 1920 = 1080p 这一档的长边（以前固定缩到 768 宽，2K 生图对出片清晰度的好处白丢了）。
# 按长边封顶、不按宽：9:16 竖屏正好是宽 ≤1080（契约写的 min(原宽, 1080)），16:9 横屏也不会被缩成 1080×608、
# 比 1080p 的成片还小。交付比 1080p 更大（2K / 4K）的也只到 1920：上传上限 2.5MB 摆在那，
# 再大的图只能靠往下压画质塞进去，清晰度一样丢
_UPLOAD_PROXY_MAX_LONG_SIDE = 1920
# ffmpeg mjpeg 的 -q:v（2 最好、31 最差）：从最好的一档往下试，第一档落进上限就停——正常照片在第一档就够
_UPLOAD_PROXY_QUALITIES = (2, 3, 4, 6, 8, 11, 15, 20, 31)


def _upload_ready_image(path: Path, *, ffmpeg: str, proxy_dir: Path) -> Path:
    """返回能交给 ``upload_asset`` 的图：不超过上限就是原图；超过就是 ``proxy_dir`` 里缩小的 JPEG 代理图。

    代理图必须落在持久目录、不能放临时目录：路由来源的 ``upload_asset`` 只登记 ``local:<路径>`` 令牌，
    真正读文件在提交时（中断后恢复时甚至在下一次运行里），临时目录那时早没了——提交报
    ``reference image does not exist``，被当成结果未知，整次运行卡在 blocked_submission_unknown。
    文件名带源文件内容哈希的前 8 位：同一张首帧（本镜出片、下一镜 ref_frame 各上传一次）复用同一份代理；
    首帧重拍后内容变了自然换新名字，不会拿旧代理去提交。代理图只改上传的字节，请求指纹按 spec 算，不受影响。
    """

    if path.stat().st_size <= _DIRECT_IMAGE_UPLOAD_LIMIT_BYTES:
        return path
    proxy = proxy_dir / f"{path.stem}-{artifact_sha256(path)[:8]}.jpg"
    if proxy.is_file() and 0 < proxy.stat().st_size <= _DIRECT_IMAGE_UPLOAD_LIMIT_BYTES:
        return proxy
    proxy_dir.mkdir(parents=True, exist_ok=True)
    # 先写同目录下的临时名、成了再换名：ffmpeg 半路失败或进程被杀，不会留下一个半截的「现成代理」被下次复用
    handle, partial_name = tempfile.mkstemp(prefix=f".{proxy.stem}.", suffix=".partial.jpg", dir=proxy_dir)
    os.close(handle)
    partial = Path(partial_name)
    # 长边取 min(原长边, 1920) 再取偶数，短边按比例（-2 = 保持比例并取偶数）；比上限小的图不放大
    cap = _UPLOAD_PROXY_MAX_LONG_SIDE
    scale = (
        f"scale='if(gte(iw,ih),2*trunc(min({cap},iw)/2),-2)'"
        f":'if(gte(iw,ih),-2,2*trunc(min({cap},ih)/2))':flags=lanczos"
    )
    try:
        size = 0
        for quality in _UPLOAD_PROXY_QUALITIES:
            _run_checked(
                (
                    ffmpeg,
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(path),
                    "-vf",
                    scale,
                    "-frames:v",
                    "1",
                    "-q:v",
                    str(quality),
                    str(partial),
                )
            )
            size = partial.stat().st_size if partial.is_file() else 0
            if size <= 0:
                raise StoryVideoError(f"upload proxy was not created: {proxy}")
            if size <= _DIRECT_IMAGE_UPLOAD_LIMIT_BYTES:
                os.replace(partial, proxy)
                return proxy
        # 长边 1920 的 JPEG 在最低画质下还超 2.5MB，只可能是图本身异常（满屏噪点之类）；不再往小里缩，让人看一眼。
        # 句中不用全角括号：界面层 humanize 会在后面接「（原文）」，tr 按最后一个全角括号切分
        raise StoryVideoError(
            f"上传用的代理图压到最低画质仍有 {size} 字节，超过 {_DIRECT_IMAGE_UPLOAD_LIMIT_BYTES} 字节的上传上限："
            f"{path.name}——检查这张图的尺寸是否异常"
        )
    finally:
        partial.unlink(missing_ok=True)


def _contact_sheet(images: Sequence[Path], destination: Path, *, ffmpeg: str) -> Path:
    if not images:
        raise StoryVideoError("cannot build an empty contact sheet")
    columns = min(4, len(images))
    width, height = 240, 426
    filters: list[str] = []
    for index in range(len(images)):
        filters.append(f"[{index}:v]scale={width}:{height}[s{index}]")
    if len(images) == 1:
        filters.append("[s0]null[out]")
    else:
        layout = "|".join(
            f"{(index % columns) * width}_{(index // columns) * height}"
            for index in range(len(images))
        )
        filters.append(
            "".join(f"[s{index}]" for index in range(len(images)))
            + f"xstack=inputs={len(images)}:layout={layout}:fill=black[out]"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y", "-loglevel", "error"]
    for item in images:
        command.extend(("-i", str(item)))
    command.extend(
        (
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-frames:v",
            "1",
            str(destination),
        )
    )
    _run_checked(command)
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise StoryVideoError(f"contact sheet was not created: {destination}")
    return destination


def _video_contact_sheet(videos: Sequence[Path], destination: Path, *, ffmpeg: str) -> Path:
    with tempfile.TemporaryDirectory(prefix="storyvideo-qc-") as directory:
        extracted: list[Path] = []
        for index, video in enumerate(videos):
            info = validate_media(video, "video", require_audio=False)
            for suffix, timestamp in (
                ("m", max(0.0, info.duration_seconds / 2)),
                ("t", max(0.0, info.duration_seconds - 0.15)),
            ):
                frame = Path(directory) / f"{index:03d}-{suffix}.png"
                _run_checked(
                    (
                        ffmpeg,
                        "-y",
                        "-loglevel",
                        "error",
                        "-ss",
                        f"{timestamp:.3f}",
                        "-i",
                        str(video),
                        "-frames:v",
                        "1",
                        str(frame),
                    )
                )
                extracted.append(frame)
        return _contact_sheet(extracted, destination, ffmpeg=ffmpeg)


# 冻结后仍可改的「后期块」：从不进 frame/video 请求，只被 post_layers / 装配 / 人读
POST_ONLY_KEYS = frozenset({
    "post_graphics", "voice_over", "design_notes", "revision_note", "status",
    "title_zh", "title_en", "logline",
    "subtitle",   # 字幕样式只进装配/后期合成，不进付费请求（2026-09-08）
})


def _only_overlays_changed(key: str, source: Mapping[str, Any], snapshot: Mapping[str, Any]) -> bool:
    """``delivery`` 整块不是后期块（宽高/帧率决定装配合同），但里面的交付面遮挡区（DESIGN §27）只给预览和
    静态检查看——两边去掉 overlay_* 之后相同，就当后期块放行。"""

    if key != "delivery":
        return False
    from .overlays import OVERLAY_KEYS

    def stripped(value: Any) -> dict[str, Any]:
        return {k: v for k, v in value.items() if k not in OVERLAY_KEYS} if isinstance(value, Mapping) else {"_": value}

    return stripped(source.get(key)) == stripped(snapshot.get(key))


LEDGER_NAME = "storyvideo.sqlite3"


def ledger_path(project_root: str | Path) -> Path:
    """账本文件的位置：``<project_root>/output/story_video/storyvideo.sqlite3``。
    runner 按这条规则开可写账本；详情页 / 失败任务（``webui.generate``）、启动续等、交付包的工具清单都按同一条规则只读打开
    ——账本路径只能有这一份规则，别再手写 ``output_root.parent / …``。"""

    return Path(project_root).resolve() / "output" / "story_video" / LEDGER_NAME


class StoryVideoRunner:
    """Prepare, execute, resume and review a single declared episode."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        provider: GenerationProvider | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        readonly: bool = False,
    ) -> None:
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        self.output_base = (self.project_root / "output" / "story_video").resolve()
        if not readonly:
            self.output_base.mkdir(parents=True, exist_ok=True)
        self.provider = provider
        self.readonly = bool(readonly)
        self._sleep = sleep
        self._monotonic = monotonic
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self._capabilities: str | None = None
        self.store = RunStore(ledger_path(self.project_root), readonly=readonly)

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "StoryVideoRunner":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    @staticmethod
    def _run_id(spec: Mapping[str, Any]) -> str:
        run_id = str(spec.get("id") or "").strip()
        if not run_id:
            raise StoryVideoError("episode spec.id must not be empty")
        output_root = Path(str(spec["output_root"])).resolve()
        if output_root.name != run_id:
            raise StoryVideoError(
                "output_root must end with the exact episode id so state cannot mix episodes"
            )
        return run_id

    @staticmethod
    def _paths(spec: Mapping[str, Any]) -> dict[str, Path]:
        root = Path(str(spec["output_root"])).resolve()
        return {
            "root": root,
            "frames": root / "01_frames",
            "videos": root / "02_video",
            "manifests": root / "manifests",
            "state": root / "manifests" / "run_state.json",
            "qc": root / "manifests" / "qc.json",
            "assets": root / "manifests" / "assets.json",
            "tail": root / _tail.TAIL_DIR,   # 尾帧续接（DESIGN §30）：源镜视频过检后抽的最后一帧
            "installs": root / "manifests" / "installs",
            "snapshot": root / "manifests" / "episode.snapshot.json",
            "final": root / f"{spec['id']}_final.mp4",
        }

    def prepare(
        self,
        episode: str | Path,
        *,
        allow_legacy_artifacts: bool = False,
    ) -> dict[str, Any]:
        spec = load_spec(episode, project_root=self.project_root)
        with production_output_lock(spec["output_root"]):
            return self._prepare_unlocked(
                spec, allow_legacy_artifacts=allow_legacy_artifacts
            )

    def _prepare_unlocked(
        self,
        spec: Mapping[str, Any],
        *,
        allow_legacy_artifacts: bool,
    ) -> dict[str, Any]:
        run_id = self._run_id(spec)
        paths = self._paths(spec)
        for key in ("frames", "videos", "manifests"):
            paths[key].mkdir(parents=True, exist_ok=True)

        source = _source_spec(spec)
        if stable_json_hash(source) != spec["source_hash"]:
            raise StoryVideoError(
                "episode source changed while preparation was starting; retry preparation"
            )
        plan = {
            "schema_version": 1,
            "episode": run_id,
            "frames": [frame_request_data(spec, shot["id"]) for shot in spec["shots"]],
            "videos": [video_request_data(spec, shot["id"]) for shot in spec["shots"]],
            "waves": spec["waves"],
        }
        existing = self.store.get_run(run_id)
        if existing is None:
            generated = [
                *(paths["frames"] / f"{shot['id']}.png" for shot in spec["shots"]),
                *(paths["videos"] / f"{shot['id']}.mp4" for shot in spec["shots"]),
                paths["final"],
            ]
            if any(path.exists() for path in generated) and not allow_legacy_artifacts:
                raise StoryVideoError(
                    "legacy artifacts exist without durable StoryVideo state; "
                    "run storyvideo import-legacy explicitly before using the new runner"
                )
            atomic_write_json(paths["snapshot"], source)
        elif not paths["snapshot"].is_file():
            # 账本里有 run、冻结快照却不在：不是有人改了快照，是整个输出目录被搬走/删掉了（归档没清账本、
            # 或手动清盘）。要排在 hash 比对之前——同 id 新建的内容不同时会先撞上「内容被改过」，
            # 而那个 run 根本不是这个项目的。
            raise RunCollisionError(
                f"ledger has a run for {run_id} but its frozen snapshot is gone; "
                "restore the output directory or use a new id"
            )
        elif existing["input_hash"] != spec["source_hash"]:
            raise RunCollisionError(
                "episode source changed after the run was frozen; create an explicit revision"
            )
        elif stable_json_hash(read_json(paths["snapshot"], missing={})) != spec["source_hash"]:
            raise RunCollisionError("frozen episode snapshot changed on disk")

        self.store.create_run(
            run_id,
            stage_id="storyvideo",
            topic_id=run_id,
            input_hash=spec["source_hash"],
            plan_hash=stable_json_hash(plan),
            run_dir=paths["root"],
        )
        for shot in spec["shots"]:
            shot_id = shot["id"]
            self.store.ensure_job(
                run_id,
                make_step_id("frames", shot_id),
                "image",
                input_fingerprint("frame", frame_request_data(spec, shot_id)),
                Path("01_frames") / f"{shot_id}.png",
            )
            self.store.ensure_job(
                run_id,
                make_step_id("videos", shot_id),
                "video",
                input_fingerprint("video", video_request_data(spec, shot_id)),
                Path("02_video") / f"{shot_id}.mp4",
            )
        self.store.ensure_job(
            run_id,
            make_step_id("assembly"),
            "assembly",
            input_fingerprint("assembly", spec["source_hash"], spec.get("subtitle") or {}),
            paths["final"].name,
        )
        self._sync(spec)
        return spec

    def retry_failed(
        self,
        episode: str | Path,
        *,
        only: Iterable[str] | None = None,
        reason: str = "retry after failed attempt",
    ) -> dict[str, Any]:
        """Reset ``failed`` frame/video jobs to ``prepared`` so the next run resubmits them.

        平台瞬时错误（如服务端后处理迟迟不进终态）导致的失败任务没有产物、不在 QC 项里，
        review retake 说 unknown item，run 又不碰 failed——以前只能开新 revision。
        这里给失败任务一条原地重提的路：只动 failed、没有远端任务需要认领的任务；有 task_id 的先走
        reconcile。审核类失败（内容被拦）重提也会再失败，这是调用者的判断。
        """

        spec = load_spec(episode, project_root=self.project_root)
        run_id = self._run_id(spec)
        self.store.require_run(run_id)
        wanted = {str(x) for x in only} if only else None
        reset: list[str] = []
        skipped: list[str] = []
        for shot in spec["shots"]:
            shot_id = str(shot["id"])
            if wanted is not None and shot_id not in wanted:
                continue
            for stage in ("frames", "videos"):
                step_id = make_step_id(stage, shot_id)
                job = self.store.get_job(run_id, step_id)
                if job is None or job.get("status") != "failed":
                    continue
                if job.get("ambiguous_submission"):
                    skipped.append(f"{step_id} (ambiguous submission; reconcile first)")
                    continue
                self.store.reset_job(run_id, step_id, reason=reason)
                reset.append(step_id)
        self._sync(spec)
        return {"run_id": run_id, "status": f"retry-failed reset={len(reset)} skipped={len(skipped)}",
                "reset": reset, "skipped": skipped}

    def amend_post_layers(self, episode: str | Path) -> dict[str, Any]:
        """Accept a post-freeze edit that only touches post-production blocks.

        实际遇到过：prepare 之后只改了 ``post_graphics``（插卡版式）和 ``voice_over``
        （旁白声线），run 被「source changed after frozen」挡住，而这两块从不进付费请求。
        这里对比冻结快照与当前源：差异只落在 ``POST_ONLY_KEYS`` 里就重写快照、更新 run 的
        input_hash，然后一切照常；差异碰到任何会进提示词/参数的字段则照旧拒绝。
        """

        spec = load_spec(episode, project_root=self.project_root)
        run_id = self._run_id(spec)
        run = self.store.require_run(run_id)
        paths = self._paths(spec)
        source = _source_spec(spec)
        snapshot = read_json(paths["snapshot"], missing={})
        if run.get("input_hash") == spec["source_hash"] and stable_json_hash(snapshot) == spec["source_hash"]:
            repaired = self._repair_assembly_fingerprint(spec)
            return {"run_id": run_id, "status": "repaired" if repaired else "unchanged", "changed_keys": []}
        if stable_json_hash(snapshot) != run.get("input_hash"):
            raise RunCollisionError("frozen episode snapshot changed on disk")
        changed = sorted(
            key for key in set(source) | set(snapshot)
            if source.get(key) != snapshot.get(key)
        )
        blocked = [key for key in changed if key not in POST_ONLY_KEYS and not _only_overlays_changed(key, source, snapshot)]
        if blocked:
            raise RunCollisionError(
                "episode source changed after the run was frozen in generation-affecting fields "
                f"({', '.join(blocked)}); create an explicit revision"
            )
        atomic_write_json(paths["snapshot"], source)
        self.store.amend_input_hash(run_id, spec["source_hash"])
        self._repair_assembly_fingerprint(spec)
        self._sync(spec)
        return {"run_id": run_id, "status": "amended", "changed_keys": changed}

    def _repair_assembly_fingerprint(self, spec: Mapping[str, Any]) -> bool:
        """装配任务的指纹带 source_hash（字幕/交付合同随源变）：修订后同步换掉；已装配过的成片作废重装。"""

        run_id = self._run_id(spec)
        assembly_step = make_step_id("assembly")
        assembly_job = self.store.get_job(run_id, assembly_step)
        if assembly_job is None:
            return False
        expected = input_fingerprint("assembly", spec["source_hash"], spec.get("subtitle") or {})
        if assembly_job.get("request_hash") == expected:
            return False
        self.store.amend_job_request_hash(run_id, assembly_step, expected)
        if assembly_job.get("status") == "succeeded":
            self._invalidate_stale_gate(spec, "final", reason="post layers amended after assembly")
        return True

    def status(self, episode: str | Path) -> dict[str, Any]:
        spec = load_spec(episode, project_root=self.project_root)
        run_id = self._run_id(spec)
        run = self.store.require_run(run_id)
        if run.get("input_hash") != spec.get("source_hash"):
            raise RunCollisionError(
                "episode source changed after the run was frozen; create an explicit revision"
            )
        snapshot = read_json(self._paths(spec)["snapshot"], missing={})
        if stable_json_hash(snapshot) != spec.get("source_hash"):
            raise RunCollisionError("frozen episode snapshot changed on disk")
        return self._result(spec)

    @contextmanager
    def production_lock(self, episode: str | Path) -> Iterator[dict[str, Any]]:
        """Hold the same lock used by paid execution and media QC decisions."""

        spec = load_spec(episode, project_root=self.project_root)
        lock_path = self._paths(spec)["manifests"] / "production.lock"
        with _exclusive_file_lock(lock_path):
            yield spec

    def import_legacy(
        self,
        episode: str | Path,
        *,
        confirmed_existing_artifacts: bool,
    ) -> dict[str, Any]:
        spec = load_spec(episode, project_root=self.project_root)
        manifests = self._paths(spec)["manifests"]
        with _exclusive_file_lock(manifests / "production.lock"):
            with _exclusive_file_lock(manifests / "qc.lock"):
                return self._import_legacy_unlocked(
                    episode,
                    confirmed_existing_artifacts=confirmed_existing_artifacts,
                )

    def _import_legacy_unlocked(
        self,
        episode: str | Path,
        *,
        confirmed_existing_artifacts: bool,
    ) -> dict[str, Any]:
        """Adopt validated local artifacts without making any remote calls."""

        if confirmed_existing_artifacts is not True:
            raise StoryVideoError(
                "legacy import requires explicit confirmation of existing artifacts"
            )
        spec = self.prepare(episode, allow_legacy_artifacts=True)
        run_id = self._run_id(spec)
        paths = self._paths(spec)
        qc_before = load_qc(paths["qc"])
        # 这次真正新登记成 succeeded 的任务：一个都没有时不改写 run 状态（见文末）
        adopted: list[str] = []
        # 盘上还在、但是被重拍作废掉的旧产物：不登记，任务留在 prepared 等重出
        rejected: dict[str, set[str]] = {"frames": set(), "videos": set()}
        frame_artifacts: dict[str, tuple[Path, Mapping[str, Any]]] = {}
        video_artifacts: dict[str, tuple[Path, Mapping[str, Any]]] = {}
        for shot_id, shot in _shot_map(spec).items():
            frame = paths["frames"] / f"{shot_id}.png"
            frame_step = make_step_id("frames", shot_id)
            if frame.is_file() and self._is_rejected_legacy_file(spec, "frames", shot_id, frame, qc_before):
                rejected["frames"].add(shot_id)
            elif frame.is_file():
                info = validate_media(
                    frame, "image", min_size_bytes=1024, ffprobe=self.ffprobe
                )
                if self._adopt_legacy_job(spec, frame_step, frame):
                    adopted.append(frame_step)
                frame_artifacts[shot_id] = (frame, info.as_dict())
            video = paths["videos"] / f"{shot_id}.mp4"
            video_step = make_step_id("videos", shot_id)
            if video.is_file() and self._is_rejected_legacy_file(spec, "videos", shot_id, video, qc_before):
                rejected["videos"].add(shot_id)
            elif video.is_file():
                info = validate_media(
                    video,
                    "video",
                    require_audio=True,
                    expected_duration_seconds=float(shot["dur"]),
                    duration_tolerance_seconds=_duration_tolerance(float(shot["dur"])),
                    min_size_bytes=10_000,
                    ffprobe=self.ffprobe,
                )
                if self._adopt_legacy_job(spec, video_step, video):
                    adopted.append(video_step)
                video_artifacts[shot_id] = (video, info.as_dict())

        qc = load_qc(paths["qc"])
        shot_ids = set(_shot_map(spec))
        # 有被作废的旧产物时改用增量登记（expected_item_ids）：全量登记会把这些项整个抹掉重写，
        # 重拍裁决 / 作废标记就没了；增量只更新这次登记的项，作废的那几项原样留着等重出
        if frame_artifacts and set(frame_artifacts) | rejected["frames"] == shot_ids:
            evidence = [paths["root"] / "x_frames.png"]
            qc = record_technical_results(
                paths["qc"],
                "frames",
                frame_artifacts,
                evidence=[item for item in evidence if item.exists()],
                expected_item_ids=shot_ids if rejected["frames"] else None,
            )
        if video_artifacts and set(video_artifacts) | rejected["videos"] == shot_ids:
            evidence = [paths["root"] / "x_video_qc.png"]
            qc = record_technical_results(
                paths["qc"],
                "videos",
                video_artifacts,
                evidence=[item for item in evidence if item.exists()],
                expected_item_ids=shot_ids if rejected["videos"] else None,
            )

        final = paths["final"]
        if final.is_file() and not self._is_rejected_legacy_file(spec, "final", "final", final, qc_before):
            from .assembly import delivery_settings

            width, height, _fps, _crossfade = delivery_settings(spec)
            info = validate_media(
                final,
                "video",
                require_audio=True,
                expected_dimensions=(width, height),
                min_size_bytes=10_000,
                ffprobe=self.ffprobe,
            )
            if self._adopt_legacy_job(spec, make_step_id("assembly"), final):
                adopted.append(make_step_id("assembly"))
            evidence = [paths["root"] / "x_final_qc.jpg"]
            qc = record_technical_results(
                paths["qc"],
                "final",
                {"final": (final, info.as_dict())},
                evidence=[item for item in evidence if item.exists()],
            )

        # 什么都没新登记（比如「采用候选首帧」时只剩被重拍作废的旧片）：run 状态原样保留——以前无条件写回
        # awaiting_frames_qc，已审到视频 / 正在重拍的项目状态会倒退。刚建的 run（prepared）照旧写
        previous = str((self.store.get_run(run_id) or {}).get("status") or "")
        if adopted or previous in ("", "prepared"):
            if set(frame_artifacts) == shot_ids:
                status = "awaiting_frames_qc"
            elif rejected["frames"]:
                status = "frames_partial"   # 被作废的旧首帧要重出：跟 run() 出到一半是同一个状态
            else:
                status = "legacy_import_partial"
            self.store.set_run_status(run_id, status)
        self._sync(spec, qc=qc)
        return self._result(spec)

    def _is_rejected_legacy_file(
        self,
        spec: Mapping[str, Any],
        stage: str,
        item_id: str,
        artifact: Path,
        qc: Mapping[str, Any],
    ) -> bool:
        """盘上这份文件是不是被重拍作废掉的旧产物——是的话 import-legacy 不能再把它登记回来。

        重拍（``_archive_and_reset`` / ``_reset_assembly``）只把现文件留底到 takes/、把任务重置回 prepared，
        不删现文件。于是「采用候选首帧」（compare.adopt → import_legacy）会把因首帧重拍而重置的视频任务
        重新标成 succeeded、指向按旧首帧渲染的旧视频，QC 的重拍状态也被抹掉，这一镜的视频再也不会重出。
        两条判据，任一成立就算旧产物：
        * 任务是被重置过的 prepared（``error`` 里记着重置原因；``ensure_job`` 新建的任务没有），且字节等于
          QC 账本里已作废（stale）那一项的 artifact_sha256；
        * 文件字节等于 takes/ 里留底的某一版（只有重拍会往那里留底）。留底优先用硬链接，跟现文件是同一个
          inode 的那份不算：现文件要是被原地覆盖写过（拷贝工具覆盖同名文件就是这样），留底会跟着变，
          内容相等说明不了它还是旧产物——这种情况交给上一条判。
        只有字节真的是新的（对比候选、换进来的新文件）才登记。
        """

        run_id = self._run_id(spec)
        step_id = make_step_id("assembly") if stage == "final" else make_step_id(stage, item_id)
        job = self.store.get_job(run_id, step_id)
        if job is None or job.get("status") != "prepared":
            return False   # 已登记的照旧走 _adopt_legacy_job；缺任务 / 状态不对的让它照旧报错
        try:
            digest = artifact_sha256(artifact)
        except OSError:
            return False
        if job.get("error"):
            item = ((((qc.get("gates") or {}).get(stage) or {}).get("items")) or {}).get(item_id)
            if isinstance(item, Mapping) and item.get("stale") is True and item.get("artifact_sha256") == digest:
                return True
        root = self._paths(spec)["root"]
        if stage == "final":
            takes = root / "takes" / "final"
        else:
            takes = root / ("01_frames" if stage == "frames" else "02_video") / "takes" / item_id
        if takes.is_dir():
            for take in sorted(takes.iterdir()):
                try:
                    if not take.is_file() or os.path.samefile(take, artifact):
                        continue
                    if artifact_sha256(take) == digest:
                        return True
                except OSError:
                    continue
        return False

    def _adopt_legacy_job(
        self, spec: Mapping[str, Any], step_id: str, artifact: Path
    ) -> bool:
        """登记一个本地已有的产物；返回这次是不是新登记（已经 succeeded 的返回 False）。"""

        run_id = self._run_id(spec)
        job = self.store.get_job(run_id, step_id)
        if job is None:
            raise StoryVideoError(f"missing prepared legacy job: {step_id}")
        if job["status"] == "succeeded":
            return False
        if job["status"] != "prepared":
            raise StoryVideoError(
                f"cannot import {step_id} over durable state {job['status']}"
            )
        self.store.mark_succeeded(run_id, step_id, artifact_path=artifact)
        return True

    def run(
        self,
        episode: str | Path,
        *,
        only: Iterable[str] | None = None,
        through: str = "auto",
        wait: bool = True,
    ) -> dict[str, Any]:
        initial = load_spec(episode, project_root=self.project_root)
        lock_path = self._paths(initial)["manifests"] / "production.lock"
        with _exclusive_file_lock(lock_path):
            return self._run_unlocked(
                episode, only=only, through=through, wait=wait
            )

    def _run_unlocked(
        self,
        episode: str | Path,
        *,
        only: Iterable[str] | None,
        through: str,
        wait: bool,
    ) -> dict[str, Any]:
        spec = self.prepare(episode)
        run_id = self._run_id(spec)
        known = set(_shot_map(spec))
        selected = _safe_ids(only, known)
        normalized_through = through.strip().lower()
        if normalized_through not in {"auto", "frames", "videos", "assemble", "final"}:
            raise StoryVideoError("through must be auto, frames, videos, assemble, or final")
        snapshot = self.store.snapshot(run_id)
        if snapshot["ambiguous_submissions"]:
            self._auto_reconcile(spec, snapshot["ambiguous_submissions"])
            snapshot = self.store.snapshot(run_id)
        if snapshot["ambiguous_submissions"]:
            self.store.set_run_status(run_id, "blocked_submission_unknown")
            self._sync(spec)
            raise StoryVideoBlocked(
                "submission outcome is unknown for: "
                + ", ".join(snapshot["ambiguous_submissions"])
                + "; reconcile a remote task id before retrying"
            )
        self._resume_pending_retake(spec)
        # Recovery of already-paid work must never depend on a capability-list
        # call used only to authorize a *new* submission.  Poll/adopt/download
        # every durable remote task first.  If a non-blocking poll still leaves
        # work in flight, return its durable status without starting more work.
        self._recover_inflight_remote_jobs(spec, wait=wait)
        inflight = self._inflight_step_ids_of(self.store.snapshot(run_id))
        if inflight:
            status = (
                "frames_partial"
                if any(step_id.startswith("frame-") for step_id in inflight)
                else "videos_partial"
            )
            self.store.set_run_status(run_id, status)
            self._sync(spec)
            return self._result(spec)

        # 尾帧续接（DESIGN §30）：源镜视频还没过检的 ref_tail 镜这一轮推迟——把它们从本轮选择里拿掉，
        # 剩下的按「按镜选择」的路径走（首帧过检 → 视频），下一轮源镜过检后自动接上。全部推迟就什么都不提交。
        gated = _tail.tail_gated(spec, load_qc(self._paths(spec)["qc"]))
        if gated:
            base = known if selected is None else selected
            selected = {shot_id for shot_id in base if shot_id not in gated}
            if not selected:
                self._sync(spec)
                return self._result(spec)

        from .assembly import AssemblyError, preflight_assembly

        try:
            preflight_assembly(spec, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
        except AssemblyError as exc:
            raise StoryVideoError(f"local assembly preflight failed: {exc}") from exc

        if selected is None:
            frames_ready = self._gate_is_current(spec, "frames")
        else:
            frames_ready, _ = self._qc_items_are_current_and_accepted(
                spec, "frames", selected
            )
        if not frames_ready:
            if self._has_new_remote_work(spec, "frames", selected=selected):
                # 第一张付费首帧之前先查生图角色配好没有（整批一次，早于逐镜的上传 / 提交）。只查这一波真正要用的角色：
                # 只配了生图来源、想先出首帧看效果的人不该被「视频模型没配」拦住；视频角色在视频那一波开始前查
                self._ensure_model_available("image")
            self._advance_frames(spec, selected=selected, wait=wait)
            if normalized_through in {"videos", "assemble", "final"}:
                raise StoryVideoGatePending("frame QC must be accepted before video submission")
            return self._result(spec)
        if normalized_through == "frames":
            return self._result(spec)

        if selected is None:
            videos_ready = self._gate_is_current(spec, "videos")
        else:
            videos_ready, _ = self._qc_items_are_current_and_accepted(
                spec, "videos", selected
            )
        if not videos_ready:
            if self._has_new_remote_work(spec, "videos", selected=selected):
                self._ensure_model_available("video")
            self._advance_videos(spec, selected=selected, wait=wait)
            if normalized_through in {"assemble", "final"}:
                raise StoryVideoGatePending("video QC must be accepted before assembly")
            return self._result(spec)
        if normalized_through == "videos":
            return self._result(spec)

        # Item-scoped production may safely reach selected videos, but final
        # assembly remains an episode-wide operation.  Never assemble while an
        # unselected page is still ungenerated or unapproved.
        if selected is not None and (
            not self._gate_is_current(spec, "frames")
            or not self._gate_is_current(spec, "videos")
        ):
            if normalized_through in {"assemble", "final"}:
                raise StoryVideoGatePending(
                    "all frame and video QC items must be accepted before assembly"
                )
            return self._result(spec)

        if not self._gate_is_current(spec, "final"):
            self._assemble(spec)
        return self._result(spec)

    def review(
        self,
        episode: str | Path,
        gate: str,
        decision: str,
        *,
        shots: Iterable[str] | None = None,
        reason: str = "",
        reviewer: str = "human",
        evidence: Iterable[str | Path] = (),
        defect: str | None = None,
        disposition: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Record a QC verdict.

        ``defect`` / ``disposition`` are optional structured context stored with
        the decision (see :mod:`storyvideo.qc`).  A ``retake`` whose disposition
        is ``resubmit`` (or unset) is refused with :class:`StoryVideoRetryLimit`
        once the identical request has already been submitted
        :data:`RESUBMIT_LIMIT` times, unless ``force`` is true.
        """

        spec = load_spec(episode, project_root=self.project_root)
        paths = self._paths(spec)
        with _exclusive_file_lock(paths["manifests"] / "production.lock"):
            with _exclusive_file_lock(paths["manifests"] / "qc.lock"):
                return self._review_unlocked(
                    episode,
                    gate,
                    decision,
                    shots=shots,
                    reason=reason,
                    reviewer=reviewer,
                    evidence=evidence,
                    defect=defect,
                    disposition=disposition,
                    force=force,
                )

    def shot_attempts(self, episode: str | Path, shot_id: str) -> dict[str, dict[str, Any]]:
        """Submission counts for one shot: ``{"frames": {...}, "videos": {...}}``.

        每项是 ``RunStore.attempts_for`` 的结果（``total`` / ``same_request`` / ``request_hash``），
        用当前 spec 编译出的指纹做防御性核对。还没冻结（没有 run）时全是 0。
        """

        spec = load_spec(episode, project_root=self.project_root)
        return self._shot_attempts(spec, shot_id)

    def _shot_attempts(self, spec: Mapping[str, Any], shot_id: str) -> dict[str, dict[str, Any]]:
        run_id = self._run_id(spec)
        empty = {"total": 0, "same_request": 0, "request_hash": None}
        if shot_id not in _shot_map(spec) or self.store.get_run(run_id) is None:
            return {"frames": dict(empty), "videos": dict(empty)}
        return {
            "frames": self.store.attempts_for(
                run_id,
                make_step_id("frames", shot_id),
                request_hash=input_fingerprint("frame", frame_request_data(spec, shot_id)),
            ),
            "videos": self.store.attempts_for(
                run_id,
                make_step_id("videos", shot_id),
                request_hash=input_fingerprint("video", video_request_data(spec, shot_id)),
            ),
        }

    def _enforce_retry_discipline(
        self,
        spec: Mapping[str, Any],
        gate: str,
        selected: Iterable[str],
        *,
        disposition: str | None,
        force: bool,
    ) -> None:
        """Refuse the (RESUBMIT_LIMIT + 1)-th identical resubmission unless forced."""

        if force or disposition not in (None, "", "resubmit"):
            return
        for shot_id in sorted(selected):
            attempts = self._shot_attempts(spec, shot_id)[gate]
            same = int(attempts.get("same_request") or 0)
            if same >= RESUBMIT_LIMIT:
                raise StoryVideoRetryLimit(
                    retry_limit_message(shot_id, gate, same),
                    shot_id=shot_id,
                    gate=gate,
                    attempts=attempts,
                )

    def _review_unlocked(
        self,
        episode: str | Path,
        gate: str,
        decision: str,
        *,
        shots: Iterable[str] | None = None,
        reason: str = "",
        reviewer: str = "human",
        evidence: Iterable[str | Path] = (),
        defect: str | None = None,
        disposition: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        spec = self.prepare(episode)
        run_id = self._run_id(spec)
        normalized_gate = gate.strip().lower()
        if normalized_gate not in GATES:
            # 写盘之前就拦：不认识的闸名一路走到 _invalidate_stale_gate 会先把 run 状态写成 "<gate>_qc_stale"、
            # 作废 final，然后才在 qc.review 里被拒——一个 400 请求改了账本
            raise StoryVideoError(f"unknown QC gate: {gate}")
        normalized_decision = str(decision).strip().lower()
        defect = normalize_defect(defect)
        disposition = normalize_disposition(disposition)
        selected: set[str] | None
        if normalized_gate == "final":
            selected = {"final"}
        else:
            selected = _safe_ids(shots, set(_shot_map(spec)))
        qc_path = self._paths(spec)["qc"]
        current_qc = load_qc(qc_path)
        pending_gates = {
            name: data.get("retake_pending")
            for name, data in (current_qc.get("gates") or {}).items()
            if isinstance(data, Mapping) and isinstance(data.get("retake_pending"), Mapping)
        }
        affected: set[str] | None = None
        if normalized_decision == "retake":
            if normalized_gate not in {"frames", "videos"}:
                raise StoryVideoError(
                    "final artifacts cannot be retaken; use edit_rescue or redesign"
                )
            if selected is None:
                selected = set(_shot_map(spec))
            affected = (
                _dependent_closure(spec, selected)
                if normalized_gate == "frames"
                else set(selected)
            )
            pending = pending_gates.get(normalized_gate)
            if pending_gates and pending is None:
                raise StoryVideoBlocked(
                    "another retake is still pending: " + ", ".join(sorted(pending_gates))
                )
            if isinstance(pending, Mapping):
                expected = {
                    "selected_items": sorted(selected),
                    "affected_items": sorted(affected),
                    "reviewer": str(reviewer).strip(),
                    "reason": str(reason).strip(),
                }
                comparable = {key: pending.get(key) for key in expected}
                if comparable != expected:
                    raise StoryVideoBlocked(
                        f"{normalized_gate} already has a different pending retake"
                    )
                self._preflight_retake(
                    spec, normalized_gate, affected, allow_prepared=True
                )
                qc = current_qc
            else:
                self._preflight_retake(spec, normalized_gate, affected)
                self._enforce_retry_discipline(
                    spec, normalized_gate, selected, disposition=disposition, force=force
                )
                qc = review_qc(
                    qc_path,
                    normalized_gate,
                    normalized_decision,
                    item_ids=selected,
                    reviewer=reviewer,
                    reason=reason,
                    evidence=evidence,
                    defect=defect,
                    disposition=disposition,
                )
                qc = begin_retake(
                    qc_path,
                    normalized_gate,
                    selected_item_ids=selected,
                    affected_item_ids=affected,
                    reviewer=reviewer,
                    reason=reason,
                )
        else:
            if pending_gates:
                raise StoryVideoBlocked(
                    "finish the pending retake before recording another QC decision: "
                    + ", ".join(sorted(pending_gates))
                )
            if normalized_decision in {"pass", "waive"}:
                item_scoped = (
                    selected is not None
                    and normalized_gate in {"frames", "videos"}
                )
                if item_scoped and normalized_gate == "videos":
                    assert selected is not None
                    upstream_current, upstream_reason = (
                        self._qc_items_are_current_and_accepted(
                            spec, "frames", selected, qc=current_qc
                        )
                    )
                    if not upstream_current:
                        raise StoryVideoGatePending(
                            "videos QC cannot be accepted until the matching "
                            f"frame QC items are accepted and current: {upstream_reason}"
                        )
                elif not item_scoped:
                    required_upstream = {
                        "videos": ("frames",),
                        "final": ("frames", "videos"),
                    }.get(normalized_gate, ())
                    pending_upstream = [
                        upstream
                        for upstream in required_upstream
                        if not self._gate_is_current_locked(spec, upstream)
                    ]
                    if pending_upstream:
                        raise StoryVideoGatePending(
                            f"{normalized_gate} QC cannot be accepted until upstream "
                            + ", ".join(pending_upstream)
                            + " QC gates are accepted and current"
                        )
                    if normalized_gate in {"frames", "videos"}:
                        gate_data = (current_qc.get("gates") or {}).get(
                            normalized_gate
                        )
                        gate_items = (
                            gate_data.get("items")
                            if isinstance(gate_data, Mapping)
                            else None
                        )
                        planned_items = set(_shot_map(spec))
                        present_items = (
                            set(gate_items)
                            if isinstance(gate_items, Mapping)
                            else set()
                        )
                        if present_items != planned_items:
                            missing = sorted(planned_items - present_items)
                            detail = (
                                ": " + ", ".join(missing) if missing else ""
                            )
                            raise StoryVideoGatePending(
                                f"{normalized_gate} technical QC is incomplete"
                                f"{detail}"
                            )
                current, stale_reason = self._technical_qc_current(
                    spec,
                    normalized_gate,
                    qc=current_qc,
                    item_ids=selected if item_scoped else None,
                )
                if not current:
                    invalidation_reason = (
                        stale_reason or "QC artifacts changed before review"
                    )
                    if item_scoped:
                        assert selected is not None
                        self._invalidate_stale_items(
                            spec,
                            normalized_gate,
                            selected,
                            reason=invalidation_reason,
                        )
                    else:
                        self._invalidate_stale_gate(
                            spec,
                            normalized_gate,
                            reason=invalidation_reason,
                        )
                    # 带上 _technical_qc_current 算出的具体原因：「还没有技术检查结果」「文件哈希变了」
                    # 「任务还没成功」是三种完全不同的处境，只说「产物变了」会让一张首帧都没出的项目也被指责改了文件
                    raise StoryVideoGatePending(
                        f"{normalized_gate} artifacts changed; run technical QC again ({invalidation_reason})"
                    )
            qc = review_qc(
                qc_path,
                normalized_gate,
                normalized_decision,
                item_ids=selected,
                reviewer=reviewer,
                reason=reason,
                evidence=evidence,
                defect=defect,
                disposition=disposition,
            )
        gate_status = qc["gates"][normalized_gate]["status"]
        if normalized_decision == "retake":
            assert affected is not None
            qc = self._apply_retake(spec, normalized_gate, affected, reason=reason)
            self.store.set_run_status(run_id, f"{normalized_gate}_retake_requested")
        elif gate_status == "accepted":
            states = {
                "frames": "frames_approved",
                "videos": "videos_approved",
                "final": "ready_to_deliver",
            }
            self.store.set_run_status(run_id, states[normalized_gate])
        elif normalized_decision == "edit_rescue":
            self.store.set_run_status(run_id, "edit_requested")
        elif normalized_decision == "redesign":
            self.store.set_run_status(run_id, "redesign_requested")
        elif selected is not None and normalized_gate in {"frames", "videos"}:
            stage = normalized_gate
            all_done = all(
                (
                    self.store.get_job(
                        run_id, make_step_id(stage, shot_id)
                    )
                    or {}
                ).get("status")
                == "succeeded"
                for shot_id in _shot_map(spec)
            )
            self.store.set_run_status(
                run_id,
                f"awaiting_{normalized_gate}_qc"
                if all_done
                else f"{normalized_gate}_partial",
            )
        else:
            self.store.set_run_status(run_id, f"awaiting_{normalized_gate}_qc")
        self._sync(spec, qc=qc)
        return self._result(spec)

    def reconcile(
        self, episode: str | Path, step_id: str, task_id: str
    ) -> dict[str, Any]:
        initial = load_spec(episode, project_root=self.project_root)
        lock_path = self._paths(initial)["manifests"] / "production.lock"
        with _exclusive_file_lock(lock_path):
            return self._reconcile_unlocked(episode, step_id, task_id)

    def _reconcile_unlocked(
        self, episode: str | Path, step_id: str, task_id: str
    ) -> dict[str, Any]:
        spec = self.prepare(episode)
        run_id = self._run_id(spec)
        job = self.store.get_job(run_id, step_id)
        if job is None:
            raise StoryVideoError(f"unknown StoryVideo job: {step_id}")
        if not job["ambiguous_submission"]:
            raise StoryVideoError(f"job is not awaiting reconciliation: {step_id}")
        expected_name, task_type = self._remote_identity(spec, step_id, job)
        normalized_task_id = task_id.strip()
        remote = self._remote_history(spec, job, expected_name, task_type)
        if remote is None:
            raise StoryVideoError("the generation provider cannot query task history")
        # 名字不作硬条件：有的平台会把任务名改成生成的标题，提交时的 name 在历史里已经找不到。身份靠：
        # keyword=expected_name 的检索结果里、request_id 精确相等、类型一致，再加提供方自己的身份规则（match_remote）
        matches = _remote_matches(
            self._provider(), remote, expected_name=expected_name, task_type=task_type, task_id=normalized_task_id
        )
        if len(matches) != 1:
            raise StoryVideoBlocked(
                "task id does not uniquely match the expected remote submission identity"
            )
        self.store.mark_submitted(run_id, step_id, normalized_task_id, "pending")
        self.store.set_run_status(run_id, "waiting_remote")
        return self._result(spec)

    def abandon(
        self,
        episode: str | Path,
        step_id: str,
        *,
        reason: str,
        confirmed_not_submitted: bool,
    ) -> dict[str, Any]:
        """Clear an ambiguous intent after explicit operator verification."""

        initial = load_spec(episode, project_root=self.project_root)
        lock_path = self._paths(initial)["manifests"] / "production.lock"
        with _exclusive_file_lock(lock_path):
            return self._abandon_unlocked(
                episode,
                step_id,
                reason=reason,
                confirmed_not_submitted=confirmed_not_submitted,
            )

    def _abandon_unlocked(
        self,
        episode: str | Path,
        step_id: str,
        *,
        reason: str,
        confirmed_not_submitted: bool,
    ) -> dict[str, Any]:

        if confirmed_not_submitted is not True:
            raise StoryVideoError(
                "abandon requires explicit confirmation that no remote task exists"
            )
        normalized_reason = str(reason).strip()
        if not normalized_reason:
            raise StoryVideoError("abandon requires a reason")
        spec = self.prepare(episode)
        run_id = self._run_id(spec)
        job = self.store.get_job(run_id, step_id)
        if job is None or not job.get("ambiguous_submission"):
            raise StoryVideoError(f"job is not an ambiguous submission: {step_id}")
        expected_name, task_type = self._remote_identity(spec, step_id, job)
        remote = self._remote_history(spec, job, expected_name, task_type)
        if remote is None:
            raise StoryVideoError("the generation provider cannot query task history")
        matches = _remote_matches(self._provider(), remote, expected_name=expected_name, task_type=task_type)
        if matches:
            raise StoryVideoBlocked(
                "a matching remote task exists; reconcile it instead of abandoning"
            )
        self.store.abandon_ambiguous_submission(
            run_id, step_id, reason=normalized_reason
        )
        self.store.set_run_status(run_id, "submission_abandoned")
        return self._result(spec)

    def retry(
        self,
        episode: str | Path,
        *,
        step_ids: Iterable[str] | None = None,
        reason: str,
    ) -> dict[str, Any]:
        """Explicitly reset definitive failures for another paid attempt."""

        initial = load_spec(episode, project_root=self.project_root)
        lock_path = self._paths(initial)["manifests"] / "production.lock"
        with _exclusive_file_lock(lock_path):
            return self._retry_unlocked(
                episode, step_ids=step_ids, reason=reason
            )

    def _retry_unlocked(
        self,
        episode: str | Path,
        *,
        step_ids: Iterable[str] | None = None,
        reason: str,
    ) -> dict[str, Any]:

        normalized_reason = str(reason).strip()
        if not normalized_reason:
            raise StoryVideoError("retry requires a reason")
        spec = self.prepare(episode)
        run_id = self._run_id(spec)
        snapshot = self.store.snapshot(run_id)
        jobs = {str(job["step_id"]): job for job in snapshot["jobs"]}
        if step_ids is None:
            selected = {step_id for step_id, job in jobs.items() if job["status"] == "failed"}
        else:
            selected = {str(step_id).strip() for step_id in step_ids if str(step_id).strip()}
        if not selected:
            raise StoryVideoError("no failed jobs were selected for retry")
        unknown = sorted(selected - set(jobs))
        if unknown:
            raise StoryVideoError("unknown retry steps: " + ", ".join(unknown))
        not_failed = sorted(
            step_id for step_id in selected if jobs[step_id]["status"] != "failed"
        )
        if not_failed:
            raise StoryVideoError(
                "retry only accepts definitive failed jobs: " + ", ".join(not_failed)
            )
        for step_id in sorted(selected):
            self.store.reset_job(run_id, step_id, reason=normalized_reason)
        self.store.set_run_status(run_id, "retry_requested")
        return self._result(spec)

    def _remote_identity(
        self,
        spec: Mapping[str, Any],
        step_id: str,
        job: Mapping[str, Any],
    ) -> tuple[str, str]:
        if step_id.startswith("frame-"):
            shot_id = step_id[len("frame-") :]
            base_name = frame_request_data(spec, shot_id)["name"]
            task_type = "image"
        elif step_id.startswith("video-"):
            shot_id = step_id[len("video-") :]
            if shot_id not in _shot_map(spec):
                raise StoryVideoError(f"unknown video step: {step_id}")
            base_name = video_request_data(spec, shot_id)["name"]
            task_type = "video"
        else:
            raise StoryVideoError(f"step has no remote task identity: {step_id}")
        return _attempt_task_name(base_name, job, next_attempt=False), task_type

    def _remote_history(
        self,
        spec: Mapping[str, Any],
        job: Mapping[str, Any],
        expected_name: str,
        task_type: str,
    ) -> Sequence[Any] | None:
        """按任务名 / 类型 / 提交意图那天查服务商的历史任务；provider 不会查历史返回 ``None``。"""

        list_tasks = getattr(self._provider(), "list_tasks", None)
        if not callable(list_tasks):
            return None
        intent_date = str(job.get("updated_at") or "")[:10] or None
        return list_tasks(
            keyword=expected_name,
            task_types=(task_type,),
            workspace_id=str(spec.get("workspace_id") or "") or None,
            start_date=intent_date,
            end_date=intent_date,
            page=0,
            page_size=100,
        )

    def _auto_reconcile(
        self, spec: Mapping[str, Any], step_ids: Iterable[str]
    ) -> None:
        """Adopt one exact remote history match; never guess or resubmit."""

        provider = self._provider()
        if not callable(getattr(provider, "list_tasks", None)):
            return
        run_id = self._run_id(spec)
        for step_id in step_ids:
            job = self.store.get_job(run_id, step_id)
            if job is None or not job.get("ambiguous_submission"):
                continue
            try:
                name, task_type = self._remote_identity(spec, step_id, job)
            except StoryVideoError:
                continue
            try:
                remote = self._remote_history(spec, job, name, task_type) or ()
                matches = _remote_matches(provider, remote, expected_name=name, task_type=task_type)
            except Exception:
                continue
            if len(matches) != 1:
                continue
            self.store.mark_submitted(
                run_id,
                step_id,
                str(matches[0].request_id),
                "pending",
            )
            self.store.set_run_status(run_id, "waiting_remote")
            self._sync(spec)

    @staticmethod
    def _inflight_step_ids_of(snapshot: Mapping[str, Any]) -> list[str]:
        return [
            str(job["step_id"])
            for job in snapshot["jobs"]
            if job["status"] in {"submitted", "processing", "finished"}
        ]

    def _recover_inflight_remote_jobs(
        self, spec: Mapping[str, Any], *, wait: bool, collect_errors: bool = False
    ) -> None:
        """Resume durable paid tasks without consulting new-submit capabilities.

        ``collect_errors``：一个任务轮询/下载失败不中断其余任务（后台轮询用），
        结束时把这些失败合成一个 :class:`StoryVideoPollErrors` 抛出；
        :class:`StoryVideoBlocked` 仍然立刻抛（那是账本层面的问题，不是这一条任务的）。
        """

        run_id = self._run_id(spec)
        known = set(_shot_map(spec))
        snapshot = self.store.snapshot(run_id)
        failures: list[StoryVideoError] = []
        for job in snapshot["jobs"]:
            recoverable_success = (
                job["status"] == "succeeded" and bool(job.get("result_url"))
            )
            if (
                job["status"] not in {"submitted", "processing", "finished"}
                and not recoverable_success
            ):
                continue
            step_id = str(job["step_id"])
            if step_id.startswith("frame-"):
                stage = "frames"
                shot_id = step_id[len("frame-") :]
            elif step_id.startswith("video-"):
                stage = "videos"
                shot_id = step_id[len("video-") :]
            else:
                continue
            if shot_id not in known:
                raise StoryVideoBlocked(
                    f"durable remote job references an unknown shot: {step_id}"
                )
            if not collect_errors:
                self._advance_remote_job(spec, stage, shot_id, wait=wait)
                continue
            try:
                self._advance_remote_job(spec, stage, shot_id, wait=wait)
            except StoryVideoBlocked:
                raise
            except StoryVideoError as exc:
                failures.append(exc)
        if failures:
            raise StoryVideoPollErrors(failures)

    def poll(self, episode: str | Path) -> dict[str, Any]:
        """只轮询已经提交出去的远端任务：问进度、完成的下载并校验、记账。**不提交任何新任务、不花钱。**

        为什么要有：界面点「生成视频」后 ``run(wait=False)`` 提交即返回，之后没有任何东西再去问服务商，
        视频在服务商那边早做完了，本地账本却一直停在 processing。这个入口给后台轮询用：
        跟 ``run`` 拿同一把生产锁、同样先对账不明提交，然后只走 ``_recover_inflight_remote_jobs``——
        它只碰 submitted / processing / finished 的任务，prepared 的碰都不碰。全部落地后顺手把这一道的
        技术检查记进 qc.json（本地 ffprobe + 联络表，不花钱），否则审片会被「技术检查还没跑完」挡住。
        返回 ``status()`` 同样的结果，多一项 ``inflight``（还在飞的 step_id）。
        """

        initial = load_spec(episode, project_root=self.project_root)
        lock_path = self._paths(initial)["manifests"] / "production.lock"
        with _exclusive_file_lock(lock_path):
            return self._poll_unlocked(episode)

    def _poll_unlocked(self, episode: str | Path) -> dict[str, Any]:
        spec = self.prepare(episode)
        run_id = self._run_id(spec)
        snapshot = self.store.snapshot(run_id)
        if snapshot["ambiguous_submissions"]:
            self._auto_reconcile(spec, snapshot["ambiguous_submissions"])
            snapshot = self.store.snapshot(run_id)
        if snapshot["ambiguous_submissions"]:
            self.store.set_run_status(run_id, "blocked_submission_unknown")
            self._sync(spec)
            raise StoryVideoBlocked(
                "submission outcome is unknown for: "
                + ", ".join(snapshot["ambiguous_submissions"])
                + "; reconcile a remote task id before retrying"
            )
        try:
            self._recover_inflight_remote_jobs(spec, wait=False, collect_errors=True)
        finally:
            # 有任务失败也要把已经落地的那几条状态摆正（下一轮再来问失败的那条）
            inflight = self._inflight_step_ids_of(self.store.snapshot(run_id))
            if inflight:
                status = (
                    "frames_partial"
                    if any(step_id.startswith("frame-") for step_id in inflight)
                    else "videos_partial"
                )
                self.store.set_run_status(run_id, status)
                self._sync(spec)
        if not inflight:
            for stage in ("frames", "videos"):
                unrecorded = self._succeeded_without_technical_qc(spec, stage)
                if unrecorded:
                    self._record_stage_qc(spec, stage, unrecorded)
        result = self._result(spec)
        result["inflight"] = inflight
        return result

    def _succeeded_without_technical_qc(self, spec: Mapping[str, Any], stage: str) -> set[str]:
        """这一道已经 succeeded、但 qc.json 里还没有（或已过期的）技术检查结果的镜。"""

        run_id = self._run_id(spec)
        qc = load_qc(self._paths(spec)["qc"])
        out: set[str] = set()
        for shot_id in _shot_map(spec):
            job = self.store.get_job(run_id, make_step_id(stage, shot_id))
            if not job or job.get("status") != "succeeded":
                continue
            current, _reason = self._technical_qc_current(spec, stage, qc=qc, item_ids={shot_id})
            if not current:
                out.add(shot_id)
        return out

    def _has_new_remote_work(
        self,
        spec: Mapping[str, Any],
        stage: str,
        *,
        selected: set[str] | None,
    ) -> bool:
        """Return whether this stage can create a new remote submission."""

        run_id = self._run_id(spec)
        known = set(_shot_map(spec))
        if stage == "frames":
            target = _dependency_closure(spec, selected) if selected else known
        elif stage == "videos":
            target = selected or known
        else:  # pragma: no cover - private caller contract
            raise StoryVideoError(f"unsupported remote stage: {stage}")
        for shot_id in target:
            job = self.store.get_job(run_id, make_step_id(stage, shot_id))
            if job is not None and job["status"] in {"prepared", "asset_uploaded"}:
                return True
        return False

    def _technical_qc_current(
        self,
        spec: Mapping[str, Any],
        gate: str,
        *,
        qc: Mapping[str, Any] | None = None,
        item_ids: Iterable[str] | None = None,
    ) -> tuple[bool, str]:
        qc_value = dict(qc) if qc is not None else load_qc(self._paths(spec)["qc"])
        gate_data = (qc_value.get("gates") or {}).get(gate) or {}
        items = gate_data.get("items")
        if not isinstance(items, Mapping):
            return False, f"{gate} QC has no item ledger"
        shot_ids = set(_shot_map(spec))
        planned = {"final"} if gate == "final" else shot_ids
        expected = planned if item_ids is None else {str(item) for item in item_ids}
        unknown = sorted(expected - planned)
        if unknown:
            return False, f"unknown {gate} QC items: {', '.join(unknown)}"
        if item_ids is None and set(items) != planned:
            return False, f"{gate} QC item set differs from the episode plan"
        missing = sorted(expected - set(items))
        if missing:
            return False, f"{gate} QC has no technical result for: {', '.join(missing)}"
        run_id = self._run_id(spec)
        for item_id in sorted(expected):
            step_id = (
                make_step_id("assembly")
                if gate == "final"
                else make_step_id(gate, item_id)
            )
            job = self.store.get_job(run_id, step_id)
            if job is None or job.get("status") != "succeeded":
                state = "missing" if job is None else str(job.get("status"))
                return False, f"{step_id} is {state}, not succeeded"
            artifact_value = job.get("artifact_path") or job.get("output_path")
            if not artifact_value:
                return False, f"{step_id} has no installed artifact"
            artifact = Path(str(artifact_value)).resolve()
            item = items.get(item_id)
            if not isinstance(item, Mapping):
                return False, f"{gate}/{item_id} QC entry is malformed"
            if item.get("technical_passed") is not True or item.get("stale") is True:
                return False, f"{gate}/{item_id} technical QC is stale"
            try:
                digest = artifact_sha256(artifact)
            except OSError as exc:
                # 只写文件名和系统原因：这句会进 qc.json 的 invalidation_reason 和审片接口的 400 文案，
                # OSError 原文带本机绝对路径（用户名、数据根）
                return False, (
                    f"{gate}/{item_id} artifact is unavailable: {artifact.name} "
                    f"({exc.strerror or type(exc).__name__})"
                )
            if digest != item.get("artifact_sha256"):
                return False, f"{gate}/{item_id} artifact hash changed"
        return True, ""

    def _qc_items_are_current_and_accepted(
        self,
        spec: Mapping[str, Any],
        gate: str,
        item_ids: Iterable[str],
        *,
        qc: Mapping[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Check selected items without pretending the aggregate gate passed."""

        selected = {str(item) for item in item_ids}
        qc_value = dict(qc) if qc is not None else load_qc(self._paths(spec)["qc"])
        current, reason = self._technical_qc_current(
            spec, gate, qc=qc_value, item_ids=selected
        )
        if not current:
            return False, reason
        items = ((qc_value.get("gates") or {}).get(gate) or {}).get("items") or {}
        pending = sorted(
            item_id
            for item_id in selected
            if not isinstance(items.get(item_id), Mapping)
            or items[item_id].get("decision") not in {"pass", "waive"}
        )
        if pending:
            return False, f"{gate} QC is not accepted for: {', '.join(pending)}"
        return True, ""

    def _live_qc_view(
        self, spec: Mapping[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        """Return a read-only QC view with accepted artifact hashes rechecked."""

        qc = deepcopy(load_qc(self._paths(spec)["qc"]))
        gates = qc.get("gates") or {}
        order = ("frames", "videos", "final")
        stale_at: int | None = None
        stale_reason = ""
        for index, gate in enumerate(order):
            gate_data = gates.get(gate) or {}
            if gate_data.get("status") != "accepted":
                continue
            current, reason = self._technical_qc_current(spec, gate, qc=qc)
            if not current and stale_at is None:
                stale_at = index
                stale_reason = reason or f"{gate} accepted artifacts changed"

        if stale_at is None:
            return qc, None

        for gate in order[stale_at:]:
            gate_data = gates.get(gate)
            if not isinstance(gate_data, dict) or gate_data.get("status") != "accepted":
                continue
            gate_data["status"] = "stale"
            gate_data["stale_reason"] = (
                stale_reason
                if gate == order[stale_at]
                else f"upstream {order[stale_at]} QC is stale"
            )
            items = gate_data.get("items") or {}
            if isinstance(items, dict):
                for item in items.values():
                    if isinstance(item, dict):
                        item["stale"] = True
        return qc, order[stale_at]

    def _invalidate_stale_gate(
        self, spec: Mapping[str, Any], gate: str, *, reason: str
    ) -> None:
        paths = self._paths(spec)
        with _exclusive_file_lock(paths["manifests"] / "qc.lock"):
            qc_path = paths["qc"]
            shot_ids = set(_shot_map(spec))
            if gate == "frames":
                invalidate_qc(qc_path, "final", item_ids={"final"}, reason=reason)
                invalidate_qc(qc_path, "videos", item_ids=shot_ids, reason=reason)
                qc = invalidate_qc(qc_path, "frames", item_ids=shot_ids, reason=reason)
            elif gate == "videos":
                invalidate_qc(qc_path, "final", item_ids={"final"}, reason=reason)
                qc = invalidate_qc(qc_path, "videos", item_ids=shot_ids, reason=reason)
            else:
                qc = invalidate_qc(qc_path, "final", item_ids={"final"}, reason=reason)
            self.store.set_run_status(self._run_id(spec), f"{gate}_qc_stale")
            self._sync(spec, qc=qc)

    def _invalidate_stale_items(
        self,
        spec: Mapping[str, Any],
        gate: str,
        item_ids: Iterable[str],
        *,
        reason: str,
    ) -> None:
        """Invalidate selected artifacts and their downstream work only."""

        selected = {str(item) for item in item_ids}
        paths = self._paths(spec)
        qc_path = paths["qc"]
        if gate == "frames":
            invalidate_qc(qc_path, "final", item_ids={"final"}, reason=reason)
            invalidate_qc(qc_path, "videos", item_ids=selected, reason=reason)
            qc = invalidate_qc(qc_path, "frames", item_ids=selected, reason=reason)
        elif gate == "videos":
            invalidate_qc(qc_path, "final", item_ids={"final"}, reason=reason)
            qc = invalidate_qc(qc_path, "videos", item_ids=selected, reason=reason)
        else:
            qc = invalidate_qc(qc_path, "final", item_ids={"final"}, reason=reason)
        self.store.set_run_status(self._run_id(spec), f"{gate}_qc_stale")
        self._sync(spec, qc=qc)

    def _gate_is_current(self, spec: Mapping[str, Any], gate: str) -> bool:
        with _exclusive_file_lock(self._paths(spec)["manifests"] / "qc.lock"):
            return self._gate_is_current_locked(spec, gate)

    def _gate_is_current_locked(self, spec: Mapping[str, Any], gate: str) -> bool:
        qc = load_qc(self._paths(spec)["qc"])
        gate_data = (qc.get("gates") or {}).get(gate) or {}
        if gate_data.get("status") != "accepted":
            return False
        current, reason = self._technical_qc_current(spec, gate, qc=qc)
        items = gate_data.get("items") or {}
        decisions_pass = bool(items) and all(
            isinstance(item, Mapping) and item.get("decision") in {"pass", "waive"}
            for item in items.values()
        )
        if current and decisions_pass:
            return True
        self._invalidate_stale_gate(
            spec,
            gate,
            reason=reason or f"{gate} QC decisions are no longer passing",
        )
        return False

    def _advance_frames(
        self,
        spec: Mapping[str, Any],
        *,
        selected: set[str] | None,
        wait: bool,
    ) -> None:
        run_id = self._run_id(spec)
        paths = self._paths(spec)
        target = _dependency_closure(spec, selected) if selected else set(_shot_map(spec))
        qc_now = load_qc(paths["qc"])
        for wave in spec["waves"]:
            for shot_id in wave:
                if shot_id not in target:
                    continue
                shot = _shot_map(spec)[shot_id]
                tail = _tail.tail_ref(shot)
                if tail is not None:
                    # 尾帧续接（DESIGN §30）：源镜视频没过检就不动（run 已把这类镜推迟，这里是防御）
                    if not _tail.source_video_accepted(qc_now, tail[0]):
                        continue
                    if tail[1] == "direct" and self._satisfy_frame_from_tail(spec, shot_id, tail[0]):
                        continue
                ref = shot.get("ref_frame")
                if ref and not (tail is not None and tail[1] == "reference"):
                    dependency = self.store.get_job(
                        run_id, make_step_id("frames", str(ref))
                    )
                    if not dependency or dependency.get("status") != "succeeded":
                        continue
                self._advance_remote_job(spec, "frames", shot_id, wait=wait)
        # Technically validate every artifact produced by this selected wave,
        # even when other episode pages have not been generated yet.  The QC
        # manifest records the full expected set, so this cannot open the
        # aggregate gate early.
        self._record_stage_qc(spec, "frames", target)

    def _advance_videos(
        self,
        spec: Mapping[str, Any],
        *,
        selected: set[str] | None,
        wait: bool,
    ) -> None:
        target = selected or set(_shot_map(spec))
        for shot_id in _shot_map(spec):
            if shot_id in target:
                self._advance_remote_job(spec, "videos", shot_id, wait=wait)
        self._record_stage_qc(spec, "videos", target)

    def _record_stage_qc(
        self, spec: Mapping[str, Any], stage: str, target: set[str]
    ) -> None:
        """Locally validate the succeeded artifacts in ``target``, record the stage's technical QC
        (contact sheet + probe per item, incremental) and set the run status.  Shared by the paid
        advance paths and by :meth:`poll`, which reaches the same point after a background download.
        """

        run_id = self._run_id(spec)
        paths = self._paths(spec)
        shots = _shot_map(spec)
        all_done = all(
            (self.store.get_job(run_id, make_step_id(stage, shot_id)) or {}).get("status")
            == "succeeded"
            for shot_id in shots
        )
        artifacts: dict[str, tuple[Path, Mapping[str, Any]]] = {}
        ordered_paths: list[Path] = []
        suffix = ".png" if stage == "frames" else ".mp4"
        for shot_id in shots:
            if shot_id not in target:
                continue
            job = self.store.get_job(run_id, make_step_id(stage, shot_id)) or {}
            if job.get("status") != "succeeded":
                continue
            artifact = paths[stage] / f"{shot_id}{suffix}"
            info = self._validate_job_artifact(spec, stage, shot_id, artifact)
            artifacts[shot_id] = (artifact, info.as_dict())
            ordered_paths.append(artifact)
        qc: Mapping[str, Any] | None = None
        if artifacts:
            if stage == "frames":
                evidence = _contact_sheet(
                    ordered_paths, paths["root"] / "x_frames.png", ffmpeg=self.ffmpeg
                )
            else:
                evidence = _video_contact_sheet(
                    ordered_paths, paths["root"] / "x_video_qc.png", ffmpeg=self.ffmpeg
                )
            with _exclusive_file_lock(paths["manifests"] / "qc.lock"):
                qc = record_technical_results(
                    paths["qc"],
                    stage,
                    artifacts,
                    evidence=[evidence],
                    expected_item_ids=shots,
                )
        self.store.set_run_status(
            run_id, f"awaiting_{stage}_qc" if all_done else f"{stage}_partial"
        )
        self._sync(spec, qc=qc)

    def _advance_remote_job(
        self, spec: Mapping[str, Any], stage: str, shot_id: str, *, wait: bool
    ) -> None:
        run_id = self._run_id(spec)
        step_id = make_step_id(stage, shot_id)
        job = self.store.get_job(run_id, step_id)
        if job is None:
            raise StoryVideoError(f"missing prepared job: {step_id}")
        destination = Path(str(job["output_path"]))
        if job["status"] == "succeeded":
            try:
                self._validate_job_artifact(spec, stage, shot_id, destination)
                return
            except MediaValidationError:
                if not job.get("result_url"):
                    raise
                job = dict(job)
                job["status"] = "finished"
        if job["status"] == "failed":
            self.store.set_run_status(run_id, f"{stage}_failed")
            self._sync(spec)
            return
        if job["ambiguous_submission"]:
            self.store.set_run_status(run_id, "blocked_submission_unknown")
            self._sync(spec)
            raise StoryVideoBlocked(f"submission outcome is unknown for {step_id}")
        missing = self._missing_local_dependency(job)
        if missing is not None:
            # 登记过的本地参考文件在提交前就不见了：丢掉这次上传（只动没提交的状态，不碰付过费的尝试）重新上传。
            # 不这样做，提交时才读文件、报 reference image does not exist，被当成结果未知 → blocked_submission_unknown；
            # 升级前被「大首帧代理图提前删」卡住的老项目 abandon 之后回到 asset_uploaded、失效令牌原样保留，
            # 重跑又会卡住，成了死循环
            self.store.discard_unsubmitted_job(
                run_id, step_id, reason=f"uploaded dependency is missing before submission: {missing}"
            )
            self._sync(spec)
            job = self.store.get_job(run_id, step_id)
            assert job is not None
        if job["status"] == "prepared" and self._needs_uploaded_dependency(spec, stage, shot_id):
            asset_id = self._upload_dependency(spec, stage, shot_id)
            self.store.mark_asset_uploaded(run_id, step_id, asset_id)
            self._sync(spec)
            job = self.store.get_job(run_id, step_id)
            assert job is not None
        if job["status"] in {"prepared", "asset_uploaded"}:
            request = self._request(spec, stage, shot_id, job)
            self._ensure_model_available(request)
            source_provider, source_model = self._submission_source(request)
            self.store.mark_submitting(run_id, step_id, provider=source_provider, model=source_model)
            self.store.set_run_status(run_id, f"submitting_{stage}")
            self._sync(spec)
            try:
                submission = self._provider().submit(request)
            except ProviderNotConfigured as exc:
                # 配置类错误（角色没配 / 缺密钥 / 缺模型）：一次请求都没发出去，是明确失败——记 failed 让它能原地重提。
                # 绝不能落进下面的兜底当成「结果不明」：那会把整个项目卡死在 blocked_submission_unknown，
                # 而路由来源没有 list_tasks，对账 / 放弃都走不通（2026-09-22 审计 DRAMA-01）。
                # 正常情况下上面的 _ensure_model_available 已经在写账本之前拦下了，这里只兜没有 preflight 的老式来源
                self.store.mark_failed(run_id, step_id, f"{type(exc).__name__}: {exc}")
                self.store.set_run_status(run_id, f"{stage}_failed")
                self._sync(spec)
                raise StoryVideoError(str(exc)) from exc
            except (ProviderRejected, ProviderUnavailable, QuotaExhausted) as exc:
                self.store.mark_failed(run_id, step_id, f"{type(exc).__name__}: {exc}")
                self.store.set_run_status(
                    run_id, "blocked_quota" if isinstance(exc, QuotaExhausted) else f"{stage}_failed"
                )
                self._sync(spec)
                raise StoryVideoError(f"submission rejected for {step_id}: {exc}") from exc
            except Exception as exc:
                self.store.set_run_status(run_id, "blocked_submission_unknown")
                self._sync(spec)
                raise StoryVideoBlocked(
                    f"submission outcome for {step_id} is unknown; refusing retry: {exc}"
                ) from exc
            self.store.mark_submitted(
                run_id,
                step_id,
                submission.task_id,
                submission.status.value,
                submission.output_url,
            )
            self._sync(spec)
            if submission.status is TaskState.FAILED:
                self.store.mark_failed(
                    run_id, step_id, submission.error_message or "provider submission failed"
                )
                self.store.set_run_status(run_id, f"{stage}_failed")
                self._sync(spec)
                return
            job = self.store.get_job(run_id, step_id)
            assert job is not None
        if job["status"] in {"submitted", "processing"}:
            job = self._poll(spec, stage, shot_id, job, wait=wait)
        if job["status"] == "finished":
            if not self._adopt_installed_artifact(spec, stage, shot_id, job):
                self._download(spec, stage, shot_id, job)
        elif job["status"] == "failed":
            self.store.set_run_status(run_id, f"{stage}_failed")
            self._sync(spec)

    def _provider(self) -> GenerationProvider:
        if self.provider is None:
            raise StoryVideoError("a generation provider is required")
        return self.provider

    def _submission_source(self, request: ImageRequest | VideoRequest) -> tuple[str, str]:
        """账本要记的真实来源：这次提交交给哪个适配器、用哪个模型。

        问 provider 的 ``submission_source``（``RoutedProvider`` 答设置里该角色的 adapter id，并把 ``@image`` /
        ``@video`` 别名解析成设置里的模型名）；provider 不会答的（老式单来源 provider）就记它自报的 ``name``，
        模型只记项目文件里写死的具体模型名，别名记空串——不猜。查来源出的任何错都不能拦住提交。
        """

        provider = self._provider()
        hook = getattr(provider, "submission_source", None)
        if callable(hook):
            try:
                adapter_id, model = hook(request)
                return str(adapter_id or "").strip(), str(model or "").strip()
            except Exception:  # noqa: BLE001 - 来源只是记录，不影响提交
                return "", ""
        requested = str(getattr(request, "model", "") or "").strip()
        return str(getattr(provider, "name", "") or "").strip(), "" if requested.startswith("@") else requested

    def _ensure_model_available(self, request: ImageRequest | VideoRequest | str) -> None:
        """提交前的本地预检：这个请求（或直接给角色名 ``image`` / ``video``）要交给的角色配好了没有——没配就在
        **写账本之前**以人话错误停下，job 保持 prepared。

        问 provider 的 ``preflight``（``RoutedProvider`` 按当前设置装配适配器，没配抛 ``ProviderNotConfigured``）；
        老式单来源 provider 没有 preflight 就跳过。2026-09-22 审计 DRAMA-01：这里以前是空实现（「由适配器在提交时判断」），
        而提交时账本里已经记了 submitting、异常又被兜底当成网络类结果不明，全新安装第一次点「生成」就把项目永久锁死。
        """

        hook = getattr(self._provider(), "preflight", None)
        if not callable(hook):
            return
        try:
            hook(request)
        except ProviderNotConfigured as exc:
            raise StoryVideoError(str(exc)) from exc

    @staticmethod
    def _missing_local_dependency(job: Mapping[str, Any]) -> Path | None:
        """已登记上传、还没提交的 job：``local:`` 令牌指向的文件不在了就返回那个路径，否则 ``None``。

        路由来源的 ``upload_asset`` 只登记 ``local:<路径>`` 令牌、提交时才读文件，所以文件在两步之间没了
        （老版本放在临时目录里的代理图、被手动清掉的 ``01_frames/_upload``）只能在提交前查。
        服务商给的远端素材 id 不是本机路径，不在这里判断。
        """

        token = str(job.get("asset_id") or "")
        if job.get("status") != "asset_uploaded" or not token.startswith("local:"):
            return None
        # 用到才导入：genkit.adapters 会把所有适配器一起加载，runtime 平时用不着
        from genkit.adapters.base import token_path

        path = token_path(token)
        return None if path.is_file() else path

    @staticmethod
    def _needs_uploaded_dependency(
        spec: Mapping[str, Any], stage: str, shot_id: str
    ) -> bool:
        if stage == "videos":
            return True
        shot = _shot_map(spec)[shot_id]
        tail = _tail.tail_ref(shot)
        return bool(shot.get("ref_frame")) or (tail is not None and tail[1] == "reference")

    def _upload_dependency(self, spec: Mapping[str, Any], stage: str, shot_id: str) -> str:
        paths = self._paths(spec)
        shot = _shot_map(spec)[shot_id]
        tail = _tail.tail_ref(shot)
        if stage == "videos":
            artifact = paths["frames"] / f"{shot_id}.png"
        elif tail is not None and tail[1] == "reference":
            # 尾帧续接（DESIGN §30）：源镜的尾帧占第一个参考槽（取代 ref_frame 的首帧）
            artifact = self._ensure_tail_frame(spec, tail[0])
        else:
            ref = str(shot["ref_frame"])
            artifact = paths["frames"] / f"{ref}.png"
        validate_media(artifact, "image", min_size_bytes=1024, ffprobe=self.ffprobe)
        try:
            # 代理图（大于上传上限时才有）留在 01_frames/_upload 里不删：令牌指向它，提交时才读
            upload_artifact = _upload_ready_image(
                artifact, ffmpeg=self.ffmpeg, proxy_dir=paths["frames"] / UPLOAD_PROXY_SUBDIR
            )
            return self._provider().upload_asset(
                upload_artifact,
                asset_type="image",
                workspace_id=str(spec.get("workspace_id") or "") or None,
            )
        except Exception as exc:
            raise StoryVideoError(f"failed to upload dependency for {stage}/{shot_id}: {exc}") from exc

    def _tail_size(self, spec: Mapping[str, Any]) -> tuple[int, int] | None:
        """尾帧缩放到交付尺寸（delivery 宽高 = 首帧/成片尺寸）；算不出来就保持原尺寸。"""

        try:
            from .assembly import delivery_settings

            width, height, _fps, _crossfade = delivery_settings(spec)
        except Exception:  # noqa: BLE001 - 尺寸只是锦上添花，装配预检另有报错
            return None
        return int(width), int(height)

    def _ensure_tail_frame(self, spec: Mapping[str, Any], source: str) -> Path:
        """源镜视频的最后一帧 → ``04_tail/<源镜>.png``；旁边的 json 记源视频哈希，源视频换了就重抽。"""

        paths = self._paths(spec)
        video = paths["videos"] / f"{source}.mp4"
        if not video.is_file():
            raise StoryVideoError(f"tail relay source video is missing: {source}")
        digest = artifact_sha256(video)
        png = _tail.tail_png(paths["root"], source)
        sidecar = _tail.tail_sidecar(paths["root"], source)
        meta = read_json(sidecar, missing=None)
        if png.is_file() and isinstance(meta, Mapping) and meta.get("video_sha256") == digest:
            return png
        size = self._tail_size(spec)
        try:
            _tail.extract_tail_frame(video, png, ffmpeg=self.ffmpeg, size=size)
        except RuntimeError as exc:
            raise StoryVideoError(str(exc)) from exc
        validate_media(png, "image", min_size_bytes=1024, ffprobe=self.ffprobe)
        atomic_write_json(sidecar, {
            "schema_version": 1, "source": source, "video": str(video), "video_sha256": digest,
            "size": list(size) if size else None, "seek_seconds": _tail.TAIL_SEEK_SECONDS,
        })
        return png

    def _satisfy_frame_from_tail(self, spec: Mapping[str, Any], shot_id: str, source: str) -> bool:
        """direct 模式：尾帧本身就是本镜首帧——拷成首帧产物、账本记 ``tail:<源镜>``，不出图不花钱。
        任务已不是 prepared（已成功 / 失败）时返回 False，交回常规路径。"""

        run_id = self._run_id(spec)
        step_id = make_step_id("frames", shot_id)
        job = self.store.get_job(run_id, step_id)
        if job is None or job["status"] != "prepared":
            return False
        png = self._ensure_tail_frame(spec, source)
        destination = Path(str(job["output_path"])).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(png, destination)
        self._validate_job_artifact(spec, "frames", shot_id, destination)
        atomic_write_json(self._install_marker(spec, step_id), {
            "schema_version": 1, "step_id": step_id, "task_id": None, "attempt": int(job.get("attempt") or 0),
            "request_hash": job.get("request_hash"), "result_url": f"tail:{source}", "source": f"tail:{source}",
            "artifact": str(destination), "artifact_sha256": artifact_sha256(destination),
        })
        self.store.mark_succeeded(run_id, step_id, result_url=f"tail:{source}", artifact_path=destination)
        self.store.set_run_status(run_id, "running_frames")
        self._sync(spec)
        return True

    def _check_anchor_files(self, refs: Sequence[str]) -> None:
        """出首帧前核一遍本地锚图（归一化后是绝对路径的那些）：得在数据目录 content/ 里、得是读得出来的图片。
        不对就在提交前报错——不上传、不花钱（PB-3）。依赖槽 / 道具图是 local: 令牌或远端 id，不在这里管。"""

        content = (Path(self.project_root) / "content").resolve()
        for ref in refs:
            path = Path(str(ref))
            if not path.is_absolute() or not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(content):
                raise StoryVideoError(f"锚图不在数据目录 content/ 里：{path.name}——在角色页重新上传一张")
            try:
                from PIL import Image

                with Image.open(resolved) as image:
                    image.verify()
            except Exception as exc:  # noqa: BLE001 - 任何解析失败都按「不是图片」处理
                raise StoryVideoError(f"锚图不是读得出来的图片：{path.name}——在角色页重新上传一张") from exc

    def _cached_prop_asset(self, spec: Mapping[str, Any]) -> str | None:
        prop = spec.get("prop_ref")
        if not prop:
            return None
        prop_path = Path(str(prop)).resolve()
        validate_media(prop_path, "image", min_size_bytes=1024, ffprobe=self.ffprobe)
        digest = artifact_sha256(prop_path)
        paths = self._paths(spec)
        cache = read_json(paths["assets"], missing={}) or {}
        cached = (cache.get("files") or {}).get(digest)
        if isinstance(cached, dict) and cached.get("asset_id"):
            return str(cached["asset_id"])
        asset_id = self._provider().upload_asset(
            prop_path,
            asset_type="image",
            workspace_id=str(spec.get("workspace_id") or "") or None,
        )
        files = dict(cache.get("files") or {})
        files[digest] = {"path": str(prop_path), "asset_id": asset_id}
        cache = {"schema_version": 1, "files": files}
        atomic_write_json(paths["assets"], cache)
        return asset_id

    def _request(
        self,
        spec: Mapping[str, Any],
        stage: str,
        shot_id: str,
        job: Mapping[str, Any],
    ) -> ImageRequest | VideoRequest:
        if stage == "frames":
            data = frame_request_data(spec, shot_id)
            task_name = _attempt_task_name(data["name"], job, next_attempt=True)
            refs = frame_reference_asset_ids(
                spec,
                shot_id,
                dependency_asset_id=str(job.get("asset_id") or "") or None,
                prop_asset_id=self._cached_prop_asset(spec),
            )
            self._check_anchor_files(refs)
            return ImageRequest(
                name=task_name,
                prompt=compile_frame_prompt(spec, shot_id),
                negative_prompt=compile_negative_prompt(spec, shot_id) or None,
                model=data["model"],
                aspect_ratio=data["aspect_ratio"],
                image_size=data["image_size"],
                quality=data["quality"],
                reference_asset_ids=refs,
                workspace_id=str(spec.get("workspace_id") or "") or None,
            )
        data = video_request_data(spec, shot_id)
        task_name = _attempt_task_name(data["name"], job, next_attempt=True)
        asset_id = str(job.get("asset_id") or "")
        if not asset_id:
            raise StoryVideoError(f"video job has no persisted first-frame asset: {shot_id}")
        extra_params = dict(data["extra_params"])
        if extra_params.get("reference_audio"):
            # 参考音频（DESIGN §33）：spec 里是项目相对路径（指纹用它），提交前换成素材令牌；只有认得 audio 的适配器会用。
            # 不展开 ~、不认绝对路径，解析后还得在数据目录里（normalize 已经拦过，这里兜手改 / 绕过归一化的路径，PB-4）
            raw_audio = str(extra_params["reference_audio"])
            root = Path(self.project_root).resolve()
            audio_path = (root / raw_audio).resolve()
            if Path(raw_audio).is_absolute() or raw_audio.startswith("~") or not audio_path.is_relative_to(root):
                raise StoryVideoError(f"参考音频只能是数据目录里的相对路径，收到「{raw_audio}」——改成 content/assets/… 再出片")
            if not audio_path.is_file():
                raise StoryVideoError(f"reference audio does not exist: {audio_path}")
            extra_params["reference_audio"] = self._provider().upload_asset(
                audio_path, asset_type="audio", workspace_id=str(spec.get("workspace_id") or "") or None,
            )
        return VideoRequest(
            name=task_name,
            prompt=compile_video_prompt(spec, shot_id),
            model=data["model"],
            duration=data["duration"],
            request_type="first_frame",
            references=(AssetReference(asset_id, "first_frame"),),
            aspect_ratio=data["aspect_ratio"],
            resolution=data["resolution"],
            generate_audio=data["generate_audio"],
            workspace_id=str(spec.get("workspace_id") or "") or None,
            extra_params=extra_params,
        )

    def _poll(
        self,
        spec: Mapping[str, Any],
        stage: str,
        shot_id: str,
        job: Mapping[str, Any],
        *,
        wait: bool,
    ) -> dict[str, Any]:
        run_id = self._run_id(spec)
        step_id = make_step_id(stage, shot_id)
        task_id = str(job.get("task_id") or "")
        if not task_id:
            raise StoryVideoBlocked(f"submitted job has no task id: {step_id}")
        task_type = TaskType.IMAGE if stage == "frames" else TaskType.VIDEO
        timeout_seconds = 20 * 60 if stage == "frames" else 45 * 60
        deadline = self._monotonic() + timeout_seconds
        while True:
            try:
                snapshot = self._provider().get_task(task_id, task_type)
            except ProviderError as exc:
                self.store.set_run_status(run_id, "waiting_remote")
                self._sync(spec)
                raise StoryVideoError(f"could not poll {step_id}: {exc}") from exc
            current = self.store.mark_polled(
                run_id,
                step_id,
                snapshot.status.value,
                snapshot.output_url,
                snapshot.error_message,
            )
            self._sync(spec)
            if snapshot.status is TaskState.FINISHED:
                return current
            if snapshot.status is TaskState.FAILED:
                self.store.mark_failed(
                    run_id, step_id, snapshot.error_message or "provider task failed"
                )
                return self.store.get_job(run_id, step_id) or current
            if not wait or self._monotonic() >= deadline:
                self.store.set_run_status(run_id, "waiting_remote")
                self._sync(spec)
                return current
            self._sleep(min(20.0, max(0.0, deadline - self._monotonic())))

    def _download(
        self,
        spec: Mapping[str, Any],
        stage: str,
        shot_id: str,
        job: Mapping[str, Any],
    ) -> None:
        run_id = self._run_id(spec)
        step_id = make_step_id(stage, shot_id)
        result_url = str(job.get("result_url") or "")
        if not result_url:
            # 老版本的同步适配器只记了 file: 任务 id 没记地址：从任务 id 推出来，不用重拍
            task_id = str(job.get("task_id") or "")
            if task_id.startswith("file:") and Path(task_id[len("file:"):]).is_file():
                result_url = path_to_file_url(Path(task_id[len("file:"):]))
            else:
                raise StoryVideoError(f"finished job has no result URL: {step_id}")
        destination = Path(str(job["output_path"])).resolve()
        # 任务 id 会进临时文件名：``<前缀>:<uuid>``、file:C:\... 这类带冒号/斜杠的 id 在 Windows 上不是合法文件名
        # （实际遇到过：一批异步视频任务的下载全卡在 WinError 87），只留字母数字和 ._-
        task_fragment = re.sub(r"[^A-Za-z0-9._-]+", "-", str(job.get("task_id") or "download")).strip("-.")[-48:] or "download"
        candidate = destination.with_name(
            f".{destination.stem}.{task_fragment}.candidate{destination.suffix}"
        )
        self.store.set_run_status(run_id, f"downloading_{stage}")
        self._sync(spec)
        try:
            artifact = Path(self._provider().download(result_url, candidate)).resolve()
            if artifact != candidate.resolve():
                raise StoryVideoError(f"provider returned an unexpected path for {step_id}")
            self._validate_job_artifact(spec, stage, shot_id, artifact)
            marker = {
                "schema_version": 1,
                "step_id": step_id,
                "task_id": job.get("task_id"),
                "attempt": int(job.get("attempt") or 0),
                "request_hash": job.get("request_hash"),
                "result_url": result_url,
                "artifact": str(destination),
                "artifact_sha256": artifact_sha256(artifact),
            }
            atomic_write_json(self._install_marker(spec, step_id), marker)
            os.replace(artifact, destination)
        except Exception as exc:
            candidate.unlink(missing_ok=True)
            self.store.set_run_status(run_id, "download_failed")
            self._sync(spec)
            raise StoryVideoError(f"failed to download/validate {step_id}: {exc}") from exc
        self.store.mark_succeeded(
            run_id, step_id, result_url=result_url, artifact_path=destination
        )
        self.store.set_run_status(run_id, f"running_{stage}")
        self._sync(spec)

    def _install_marker(self, spec: Mapping[str, Any], step_id: str) -> Path:
        return self._paths(spec)["installs"] / f"{step_id}.json"

    def _adopt_installed_artifact(
        self,
        spec: Mapping[str, Any],
        stage: str,
        shot_id: str,
        job: Mapping[str, Any],
    ) -> bool:
        """Finish the DB commit after a crash that occurred post-install."""

        run_id = self._run_id(spec)
        step_id = make_step_id(stage, shot_id)
        marker = read_json(self._install_marker(spec, step_id), missing=None)
        if not isinstance(marker, Mapping):
            return False
        identity = {
            "step_id": step_id,
            "task_id": job.get("task_id"),
            "attempt": int(job.get("attempt") or 0),
            "request_hash": job.get("request_hash"),
            "result_url": job.get("result_url"),
        }
        if any(marker.get(key) != value for key, value in identity.items()):
            return False
        destination = Path(str(job["output_path"])).resolve()
        try:
            if marker.get("artifact") != str(destination):
                return False
            if artifact_sha256(destination) != marker.get("artifact_sha256"):
                return False
            self._validate_job_artifact(spec, stage, shot_id, destination)
        except (OSError, MediaValidationError):
            return False
        self.store.mark_succeeded(
            run_id,
            step_id,
            result_url=str(job.get("result_url") or "") or None,
            artifact_path=destination,
        )
        self.store.set_run_status(run_id, f"running_{stage}")
        self._sync(spec)
        return True

    def _validate_job_artifact(
        self, spec: Mapping[str, Any], stage: str, shot_id: str, artifact: Path
    ) -> MediaInfo:
        if stage == "frames":
            return validate_media(
                artifact, "image", min_size_bytes=1024, ffprobe=self.ffprobe
            )
        shot = _shot_map(spec)[shot_id]
        return validate_media(
            artifact,
            "video",
            require_audio=bool((spec.get("video_params") or {}).get("generate_audio", True)),
            expected_duration_seconds=float(shot["dur"]),
            duration_tolerance_seconds=_duration_tolerance(float(shot["dur"])),
            min_size_bytes=10_000,
            ffprobe=self.ffprobe,
        )

    def _resume_pending_retake(self, spec: Mapping[str, Any]) -> None:
        qc = load_qc(self._paths(spec)["qc"])
        pending = [
            (gate, data.get("retake_pending"))
            for gate, data in (qc.get("gates") or {}).items()
            if isinstance(data, Mapping) and isinstance(data.get("retake_pending"), Mapping)
        ]
        if not pending:
            return
        if len(pending) != 1:
            raise StoryVideoBlocked("multiple QC retakes are pending; manual repair is required")
        gate, intent = pending[0]
        assert isinstance(intent, Mapping)
        if gate not in {"frames", "videos"}:
            raise StoryVideoBlocked(f"unsupported pending retake gate: {gate}")
        known = set(_shot_map(spec))
        affected = {str(item) for item in intent.get("affected_items") or ()}
        if not affected or not affected.issubset(known):
            raise StoryVideoBlocked(f"invalid pending retake items for {gate}")
        reason = str(intent.get("reason") or "").strip()
        if not reason:
            raise StoryVideoBlocked(f"pending {gate} retake has no reason")
        self._preflight_retake(spec, gate, affected, allow_prepared=True)
        refreshed = self._apply_retake(spec, gate, affected, reason=reason)
        self.store.set_run_status(self._run_id(spec), f"{gate}_retake_requested")
        self._sync(spec, qc=refreshed)

    def _preflight_retake(
        self,
        spec: Mapping[str, Any],
        gate: str,
        affected: set[str],
        *,
        allow_prepared: bool = False,
    ) -> None:
        """Refuse a retake while it could orphan an in-flight paid task."""

        run_id = self._run_id(spec)
        primary_stage = "frames" if gate == "frames" else "videos"
        # prepared = 还没提交、没有远端付费任务可被孤儿化，重摇它的上游是安全的（以前这里把整条链锁死：
        # 参考链中间一镜失败、下游全是 prepared 时只能开新 revision）
        primary_states = {"succeeded", "failed", "prepared"}
        del allow_prepared  # 保留参数签名；语义已并入上一行
        for shot_id in affected:
            step_id = make_step_id(primary_stage, shot_id)
            job = self.store.get_job(run_id, step_id)
            if job is None or job["status"] not in primary_states:
                status = "missing" if job is None else str(job["status"])
                raise StoryVideoBlocked(
                    f"cannot retake {step_id} while its state is {status}; "
                    "finish or reconcile the current task first"
                )

        if gate == "frames":
            for shot_id in affected:
                step_id = make_step_id("videos", shot_id)
                job = self.store.get_job(run_id, step_id)
                if job is None or job["status"] not in {
                    "prepared",
                    "asset_uploaded",
                    "succeeded",
                    "failed",
                }:
                    status = "missing" if job is None else str(job["status"])
                    raise StoryVideoBlocked(
                        f"cannot invalidate {step_id} while its state is {status}; "
                        "finish or reconcile the current task first"
                    )

        assembly = self.store.get_job(run_id, make_step_id("assembly"))
        if assembly is None or assembly["status"] not in {
            "prepared",
            "succeeded",
            "failed",
        }:
            status = "missing" if assembly is None else str(assembly["status"])
            raise StoryVideoBlocked(
                f"cannot invalidate assembly while its state is {status}"
            )

    def _apply_retake(
        self,
        spec: Mapping[str, Any],
        gate: str,
        affected: set[str],
        *,
        reason: str,
    ) -> dict[str, Any]:
        manifests = self._paths(spec)["manifests"]
        with _exclusive_file_lock(manifests / "production.lock"):
            with _exclusive_file_lock(manifests / "qc.lock"):
                return self._apply_retake_locked(
                    spec, gate, affected, reason=reason
                )

    def _apply_retake_locked(
        self,
        spec: Mapping[str, Any],
        gate: str,
        affected: set[str],
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Reset requested work and close every downstream QC gate."""

        run_id = self._run_id(spec)
        reset_reason = str(reason).strip()
        qc_path = self._paths(spec)["qc"]
        # Fail closed before touching the job ledger.  The persisted retake
        # marker lets a later run resume this operation if the process exits
        # between any of these atomic writes.
        invalidate_qc(
            qc_path,
            "final",
            item_ids={"final"},
            reason=f"upstream {gate} retake: {reset_reason}",
        )
        if gate == "frames":
            invalidate_qc(
                qc_path,
                "videos",
                item_ids=affected,
                reason=f"upstream frame retake: {reset_reason}",
            )
            invalidate_qc(
                qc_path,
                "frames",
                item_ids=affected,
                reason=f"frame retake: {reset_reason}",
            )
        else:
            invalidate_qc(
                qc_path,
                "videos",
                item_ids=affected,
                reason=f"video retake: {reset_reason}",
            )

        for shot_id in sorted(affected):
            step_id = make_step_id("frames" if gate == "frames" else "videos", shot_id)
            job = self.store.get_job(run_id, step_id)
            assert job is not None
            if job["status"] in {"succeeded", "failed"}:
                self._archive_and_reset(spec, gate, shot_id, reason=reset_reason)
            elif job["status"] != "prepared":
                raise StoryVideoBlocked(
                    f"cannot resume retake for {step_id} in state {job['status']}"
                )

        if gate == "frames":
            for shot_id in sorted(affected):
                step_id = make_step_id("videos", shot_id)
                job = self.store.get_job(run_id, step_id)
                assert job is not None
                if job["status"] in {"succeeded", "failed"}:
                    self._archive_and_reset(
                        spec,
                        "videos",
                        shot_id,
                        reason=f"upstream frame retake: {reset_reason}",
                    )
                elif job["status"] == "asset_uploaded":
                    self.store.discard_unsubmitted_job(
                        run_id,
                        step_id,
                        reason=f"uploaded frame became stale: {reset_reason}",
                    )

        if gate == "videos":
            # 尾帧续接（DESIGN §30）：源镜视频重拍 → 尾帧作废 → 接它的镜首帧、视频都要重来（逐层传染）
            relay: set[str] = set()
            frontier = set(affected)
            while frontier:
                frontier = _tail.tail_dependents(spec, frontier) - relay
                relay |= frontier
            for shot_id in sorted(relay):
                invalidate_qc(qc_path, "videos", item_ids={shot_id}, reason=f"upstream tail source retake: {reset_reason}")
                invalidate_qc(qc_path, "frames", item_ids={shot_id}, reason=f"upstream tail source retake: {reset_reason}")
                for stage in ("frames", "videos"):
                    step_id = make_step_id(stage, shot_id)
                    job = self.store.get_job(run_id, step_id)
                    if job is None:
                        continue
                    if job["status"] in {"succeeded", "failed"}:
                        self._archive_and_reset(spec, stage, shot_id, reason=f"upstream tail source retake: {reset_reason}")
                    elif job["status"] == "asset_uploaded":
                        self.store.discard_unsubmitted_job(run_id, step_id, reason=f"tail frame became stale: {reset_reason}")

        self._reset_assembly(
            spec,
            reason=f"upstream {gate} retake: {reset_reason}",
        )
        return complete_retake(qc_path, gate)

    def _reset_assembly(self, spec: Mapping[str, Any], *, reason: str) -> None:
        run_id = self._run_id(spec)
        step_id = make_step_id("assembly")
        job = self.store.get_job(run_id, step_id)
        if job is None or job["status"] == "prepared":
            return
        if job["status"] not in {"succeeded", "failed"}:
            raise StoryVideoBlocked(
                f"cannot reset assembly in state {job['status']}"
            )
        source = Path(str(job.get("artifact_path") or job["output_path"]))
        if source.is_file():
            digest = artifact_sha256(source)[:16]
            archive = self._paths(spec)["root"] / "takes" / "final" / (
                f"{digest}{source.suffix or '.mp4'}"
            )
            archive.parent.mkdir(parents=True, exist_ok=True)
            if not archive.exists():
                try:
                    os.link(source, archive)
                except OSError:
                    shutil.copy2(source, archive)
        self.store.reset_job(run_id, step_id, reason=reason)

    def _archive_and_reset(
        self,
        spec: Mapping[str, Any],
        gate: str,
        shot_id: str,
        *,
        reason: str,
    ) -> None:
        run_id = self._run_id(spec)
        stage = "frames" if gate == "frames" else "videos"
        step_id = make_step_id(stage, shot_id)
        job = self.store.get_job(run_id, step_id)
        if job is None or job["status"] not in {"succeeded", "failed"}:
            raise StoryVideoError(f"cannot retake non-terminal job: {step_id}")
        source = Path(str(job.get("artifact_path") or job["output_path"]))
        if source.is_file():
            extension = source.suffix
            take = max(1, int(job.get("attempt") or 1))
            archive = self._paths(spec)["root"] / (
                ("01_frames" if stage == "frames" else "02_video")
            ) / "takes" / shot_id / f"take_{take:03d}{extension}"
            archive.parent.mkdir(parents=True, exist_ok=True)
            if not archive.exists():
                try:
                    os.link(source, archive)
                except OSError:
                    shutil.copy2(source, archive)
        self.store.reset_job(run_id, step_id, reason=reason, preserve_asset=False)

    def _assemble(self, spec: Mapping[str, Any]) -> None:
        from .assembly import AssemblyError

        lock_path = self._paths(spec)["manifests"] / "production.lock"
        with _exclusive_file_lock(lock_path):
            try:
                self._assemble_locked(spec)
            except (AssemblyError, MediaValidationError) as exc:
                self.store.set_run_status(self._run_id(spec), "assembly_failed")
                self._sync(spec)
                raise StoryVideoError(f"assembly failed: {exc}") from exc

    def _assemble_locked(self, spec: Mapping[str, Any]) -> None:
        from .assembly import assemble_episode, delivery_settings

        run_id = self._run_id(spec)
        step_id = make_step_id("assembly")
        job = self.store.get_job(run_id, step_id)
        if job is None:
            raise StoryVideoError("assembly job was not prepared")
        width, height, _fps, _crossfade = delivery_settings(spec)
        planned_duration = (
            sum(float(shot["dur"]) for shot in spec["shots"])
            - float(_crossfade) * (len(spec["shots"]) - 1)
        )
        if job["status"] == "succeeded":
            try:
                info = validate_media(
                    job["artifact_path"],
                    "video",
                    require_audio=True,
                    expected_duration_seconds=planned_duration,
                    duration_tolerance_seconds=max(
                        0.75,
                        planned_duration * 0.02,
                    ),
                    expected_dimensions=(width, height),
                    min_size_bytes=10_000,
                    ffprobe=self.ffprobe,
                )
            except MediaValidationError:
                self.store.reset_job(run_id, step_id, reason="assembled artifact invalid")
            else:
                evidence = [self._paths(spec)["root"] / "x_final_qc.jpg"]
                with _exclusive_file_lock(
                    self._paths(spec)["manifests"] / "qc.lock"
                ):
                    qc = record_technical_results(
                        self._paths(spec)["qc"],
                        "final",
                        {"final": (Path(str(job["artifact_path"])), info.as_dict())},
                        evidence=[item for item in evidence if item.exists()],
                    )
                self.store.set_run_status(run_id, "awaiting_final_qc")
                self._sync(spec, qc=qc)
                return
        self.store.set_run_status(run_id, "assembling")
        self._sync(spec)
        result = assemble_episode(
            spec,
            ffmpeg=self.ffmpeg,
            ffprobe=self.ffprobe,
            create_contact_sheet=True,
        )
        final_path = Path(str(getattr(result, "final_path", result)))
        info = validate_media(
            final_path,
            "video",
            require_audio=True,
            expected_dimensions=(width, height),
            min_size_bytes=10_000,
            ffprobe=self.ffprobe,
        )
        self.store.mark_succeeded(run_id, step_id, artifact_path=final_path)
        evidence = [self._paths(spec)["root"] / "x_final_qc.jpg"]
        with _exclusive_file_lock(self._paths(spec)["manifests"] / "qc.lock"):
            qc = record_technical_results(
                self._paths(spec)["qc"],
                "final",
                {"final": (final_path, info.as_dict())},
                evidence=[item for item in evidence if item.exists()],
            )
        self.store.set_run_status(run_id, "awaiting_final_qc")
        self._sync(spec, qc=qc)

    def _sync(
        self, spec: Mapping[str, Any], *, qc: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        run_id = self._run_id(spec)
        paths = self._paths(spec)
        qc_value = dict(qc) if qc is not None else load_qc(paths["qc"])
        if self.readonly:
            snapshot = self.store.snapshot(run_id)
            exported = dict(snapshot)
            exported["storyvideo"] = build_state_view(snapshot, qc=qc_value)
            return exported
        return export_state(self.store, run_id, paths["state"], qc=qc_value)

    def _result(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        qc, stale_gate = self._live_qc_view(spec)
        state = self._sync(spec, qc=qc)
        status = str(state["run"]["status"])
        if stale_gate is not None:
            status = f"{stale_gate}_qc_stale"
            state["run"] = dict(state["run"])
            state["run"]["status"] = status
        return {
            "run_id": self._run_id(spec),
            "status": status,
            "run_dir": str(self._paths(spec)["root"]),
            "state": state,
            "qc": qc,
            # 尾帧续接（DESIGN §30）：这一轮被推迟的镜与原因（「等 S1 视频过检后抽尾帧」）
            "tail_pending": _tail.tail_gated(spec, qc),
        }


__all__ = [
    "LEDGER_NAME",
    "ledger_path",
    "production_output_lock",
    "RESUBMIT_LIMIT",
    "StoryVideoBlocked",
    "StoryVideoError",
    "StoryVideoGatePending",
    "StoryVideoPollErrors",
    "StoryVideoRetryLimit",
    "StoryVideoRunner",
    "retry_limit_message",
]
