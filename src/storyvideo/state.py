"""Small StoryVideo persistence helpers built on :mod:`genkit.store`.

The durable job ledger is deliberately *not* implemented here.  StoryVideo
uses ``genkit.store.RunStore`` for that purpose, including its SQLite/WAL
transactions, submission-intent guard, attempts, errors, and immediately
persisted remote task ids.  This module only supplies the StoryVideo naming,
fingerprinting, and atomic human-readable exports around that store.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol
import uuid

from .spec import stable_json_hash


SCHEMA_VERSION = 1
PUBLIC_STATUSES = frozenset(
    {
        "planned",
        "submitted",
        "downloading",
        "done",
        "failed",
        "awaiting_qc",
        "approved",
    }
)

_SHOT_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?\Z")
_STAGE_ALIASES = {
    "frame": "frame",
    "frames": "frame",
    "video": "video",
    "videos": "video",
    "assemble": "assemble",
    "assembly": "assemble",
}
_STAGE_EXPORT_NAMES = {
    "frame": "frames",
    "video": "videos",
    "assemble": "assembly",
}


class StateExportError(RuntimeError):
    """A human-readable state or QC export is malformed or cannot be read."""


class SnapshotStore(Protocol):
    """The small part of ``RunStore`` required by :func:`export_state`."""

    def snapshot(self, run_id: str) -> dict[str, Any]: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def make_step_id(stage: str, shot_id: str | None = None) -> str:
    """Return the stable ``RunStore`` step id for a StoryVideo operation.

    ``frames``/``frame`` and ``videos``/``video`` accept a shot id and produce
    e.g. ``frame-S1`` and ``video-S1``. ``assembly``/``assemble`` has no shot
    component and always produces ``assemble``.
    """

    canonical = _STAGE_ALIASES.get(str(stage).strip().lower())
    if canonical is None:
        raise ValueError(f"unsupported StoryVideo stage: {stage!r}")
    if canonical == "assemble":
        if shot_id is not None and str(shot_id).strip():
            raise ValueError("the assembly stage must not have a shot_id")
        return "assemble"
    if not isinstance(shot_id, str) or not _SHOT_ID.fullmatch(shot_id):
        raise ValueError("shot_id must be a safe 1-64 character slug")
    step_id = f"{canonical}-{shot_id}"
    if len(step_id) > 128:  # Keep this aligned with RunStore's id boundary.
        raise ValueError("StoryVideo step id exceeds 128 characters")
    return step_id


def parse_step_id(step_id: str) -> tuple[str, str | None]:
    """Return ``(export_stage, shot_id)`` for a StoryVideo step id."""

    if step_id == "assemble":
        return "assembly", None
    for prefix, stage in (("frame-", "frames"), ("video-", "videos")):
        if step_id.startswith(prefix):
            shot_id = step_id[len(prefix) :]
            if not _SHOT_ID.fullmatch(shot_id):
                break
            return stage, shot_id
    raise ValueError(f"not a StoryVideo step id: {step_id!r}")


def spec_hash(spec: Mapping[str, Any] | str | os.PathLike[str]) -> str:
    """Hash an in-memory spec or the JSON value stored at *spec*.

    Hashing the parsed JSON makes the fingerprint independent of indentation,
    line endings, object-key order, and platform encoding defaults.
    """

    value: Any = spec
    if isinstance(spec, (str, os.PathLike)):
        path = Path(spec)
        try:
            with path.open("r", encoding="utf-8") as stream:
                value = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise StateExportError(f"cannot read StoryVideo spec {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise TypeError("StoryVideo spec must be a JSON object or a path to one")
    return stable_json_hash(value)


def input_fingerprint(*parts: Any) -> str:
    """Return a domain-separated stable hash for inputs to one logical job."""

    return stable_json_hash(
        {"fingerprint_schema": "storyvideo-input-v1", "parts": list(parts)}
    )


def atomic_write_json(path: str | os.PathLike[str], value: Any) -> Path:
    """Durably write JSON through a sibling temporary file and ``os.replace``."""

    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n"
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def read_json(
    path: str | os.PathLike[str], *, missing: Any = None
) -> Any:
    """Read an exported JSON file, returning *missing* if it does not exist."""

    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return missing
    except (OSError, json.JSONDecodeError) as exc:
        raise StateExportError(f"cannot read state export {source}: {exc}") from exc


def write_qc(path: str | os.PathLike[str], qc: Mapping[str, Any]) -> dict[str, Any]:
    """Write the QC document atomically and return the actual saved value.

    Gate and decision semantics live in ``storyvideo.qc``.  This helper only
    provides a version marker and a trustworthy replacement write.
    """

    if not isinstance(qc, Mapping):
        raise TypeError("qc must be a mapping")
    saved = dict(qc)
    version = saved.get("schema_version", SCHEMA_VERSION)
    if version not in (0, SCHEMA_VERSION):
        raise StateExportError(f"unsupported QC schema_version: {version!r}")
    saved["schema_version"] = SCHEMA_VERSION
    saved["updated_at"] = _utc_now()
    atomic_write_json(path, saved)
    return saved


def _qc_status(qc: Mapping[str, Any] | None, step_id: str, shot_id: str | None) -> str | None:
    if not qc:
        return None
    gates = qc.get("gates")
    if isinstance(gates, Mapping):
        try:
            export_stage, parsed_shot = parse_step_id(step_id)
        except ValueError:
            export_stage, parsed_shot = "", shot_id
        gate_name = {
            "frames": "frames",
            "videos": "videos",
            "assembly": "final",
        }.get(export_stage)
        gate = gates.get(gate_name) if gate_name else None
        if isinstance(gate, Mapping):
            items = gate.get("items")
            item_id = parsed_shot if parsed_shot is not None else "final"
            if isinstance(items, Mapping):
                item = items.get(item_id)
                if isinstance(item, Mapping):
                    if item.get("stale") is True:
                        return "stale"
                    decision = str(item.get("decision") or "").lower()
                    if decision:
                        return decision
            status = str(gate.get("status") or "").lower()
            if status:
                return status
    steps = qc.get("steps")
    if isinstance(steps, Mapping):
        entry = steps.get(step_id)
        if isinstance(entry, Mapping):
            return str(entry.get("status") or entry.get("decision") or "").lower() or None
        if isinstance(entry, str):
            return entry.lower()
    if shot_id is not None:
        shots = qc.get("shots")
        if isinstance(shots, Mapping):
            entry = shots.get(shot_id)
            if isinstance(entry, Mapping):
                return str(entry.get("status") or entry.get("decision") or "").lower() or None
            if isinstance(entry, str):
                return entry.lower()
    return None


def public_status(job_status: str, qc_status: str | None = None) -> str:
    """Translate ``RunStore`` and QC states to the seven operator states."""

    local = str(job_status).strip().lower()
    if local == "succeeded" and qc_status:
        review = qc_status.strip().lower()
        if review in {"approved", "accepted", "accept", "pass", "waive"}:
            return "approved"
        if review in {
            "awaiting_qc",
            "pending",
            "pending_review",
            "pending_visual_review",
        }:
            return "awaiting_qc"
        if review in {
            "failed",
            "rejected",
            "retake",
            "revision_requested",
            "stale",
        }:
            return "failed"
    translations = {
        "prepared": "planned",
        "asset_uploaded": "planned",
        "submitting": "submitted",
        "submission_unknown": "submitted",
        "submitted": "submitted",
        "processing": "downloading",
        "finished": "downloading",
        "succeeded": "done",
        "failed": "failed",
    }
    try:
        return translations[local]
    except KeyError as exc:
        raise StateExportError(f"unsupported RunStore job status: {job_status!r}") from exc


def _stage_status(records: Sequence[Mapping[str, Any]]) -> str:
    statuses = [str(record["status"]) for record in records]
    if not statuses:
        return "planned"
    if "failed" in statuses:
        return "failed"
    if "downloading" in statuses:
        return "downloading"
    if "submitted" in statuses:
        return "submitted"
    if "planned" in statuses:
        return "planned"
    if "awaiting_qc" in statuses:
        return "awaiting_qc"
    if all(status == "approved" for status in statuses):
        return "approved"
    return "done"


def build_state_view(
    snapshot: Mapping[str, Any], *, qc: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Build a compact stage/shot view without replacing the RunStore ledger."""

    stages: dict[str, dict[str, Any]] = {
        "frames": {"status": "planned", "shots": {}},
        "videos": {"status": "planned", "shots": {}},
        "assembly": {"status": "planned", "step": None},
    }
    raw_jobs = snapshot.get("jobs", [])
    if not isinstance(raw_jobs, Sequence) or isinstance(raw_jobs, (str, bytes)):
        raise StateExportError("RunStore snapshot jobs must be an array")
    for raw_job in raw_jobs:
        if not isinstance(raw_job, Mapping):
            raise StateExportError("RunStore snapshot contains a non-object job")
        step_id = str(raw_job.get("step_id") or "")
        try:
            stage, shot_id = parse_step_id(step_id)
        except ValueError:
            continue  # Preserve foreign jobs in the unmodified RunStore snapshot only.
        review = _qc_status(qc, step_id, shot_id)
        record = {
            "step_id": step_id,
            "status": public_status(str(raw_job.get("status") or ""), review),
            "attempt": int(raw_job.get("attempt") or 0),
            "task_id": raw_job.get("task_id"),
            "input_fingerprint": raw_job.get("request_hash"),
            "output_path": raw_job.get("output_path"),
            "error": raw_job.get("error"),
            "updated_at": raw_job.get("updated_at"),
        }
        if stage == "assembly":
            stages[stage]["step"] = record
        else:
            stages[stage]["shots"][shot_id] = record

    for name, stage in stages.items():
        records = (
            list(stage["shots"].values())
            if name != "assembly"
            else ([stage["step"]] if stage["step"] is not None else [])
        )
        stage["status"] = _stage_status(records)
    return {"schema_version": SCHEMA_VERSION, "stages": stages}


def export_state(
    store: SnapshotStore,
    run_id: str,
    path: str | os.PathLike[str],
    *,
    qc: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically export the authoritative RunStore snapshot plus operator view."""

    snapshot = store.snapshot(run_id)
    exported = dict(snapshot)
    exported["schema_version"] = SCHEMA_VERSION
    exported["storyvideo"] = build_state_view(snapshot, qc=qc)
    atomic_write_json(path, exported)
    return exported


__all__ = [
    "PUBLIC_STATUSES",
    "SCHEMA_VERSION",
    "SnapshotStore",
    "StateExportError",
    "atomic_write_json",
    "build_state_view",
    "export_state",
    "input_fingerprint",
    "make_step_id",
    "parse_step_id",
    "public_status",
    "read_json",
    "spec_hash",
    "write_qc",
]
