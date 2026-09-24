"""Machine-readable human quality gates for StoryVideo production."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping


GATES = ("frames", "videos", "final")
DECISIONS = ("pass", "retake", "edit_rescue", "redesign", "waive")
_PASSING_DECISIONS = {"pass", "waive"}

# 重拍处置（DESIGN §24，取 drama-skills 的处置五分法判据）。两个枚举都是可选的裁决元数据：
# 老 qc.json 没有这两个键照常加载；新裁决带上后，界面能看出「同一缺陷是否复现过」和
# 「上次选的是原样重提还是改文字」。缺陷（defect）是看到了什么，处置（disposition）是打算怎么办。
DEFECTS = (
    "identity_drift",   # 身份漂移
    "seam",             # 接缝断裂
    "dialogue",         # 台词错
    "text_residue",     # 文字残留
    "motion",           # 动作不对
    "moderation",       # 审核拦截
    "quality",          # 画质
    "other",
)
DISPOSITIONS = (
    "keep",      # 保留：镜头目的已达成，记进观察，不改文字
    "post",      # 后期处理：缺陷在文字之外，交给后期
    "edit",      # 改文字重出：只改出错的那一层
    "resubmit",  # 原样重提：文字没问题、失败像随机——有上限（同一份提示词不超过两次）
    "model",     # 换模型对比：走多模型对比区，不进账本
)
_META_KEYS = ("defect", "disposition")
_HISTORY_LIMIT = 20


class QCError(RuntimeError):
    """A QC decision is invalid or a gate is not ready."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def artifact_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_qc(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {
            "schema_version": 1,
            "updated_at": _now(),
            "gates": {
                name: {"status": "not_ready", "items": {}} for name in GATES
            },
        }
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QCError(f"invalid QC manifest {target}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise QCError(f"unsupported QC manifest: {target}")
    gates = value.get("gates")
    if not isinstance(gates, dict):
        raise QCError(f"QC manifest has no gates object: {target}")
    for name in GATES:
        gates.setdefault(name, {"status": "not_ready", "items": {}})
    return value


def record_technical_results(
    path: str | Path,
    gate: str,
    artifacts: Mapping[str, tuple[str | Path, Mapping[str, Any]]],
    *,
    evidence: Iterable[str | Path] = (),
    expected_item_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Record local media checks and invalidate decisions when bytes change.

    ``expected_item_ids`` enables incremental technical QC.  In that mode the
    supplied artifacts are merged into the existing item ledger and the gate
    cannot become accepted until every expected item has both technical and
    human approval.  Omitting it retains the original full-replacement API for
    callers that submit one complete gate at a time.
    """

    if gate not in GATES:
        raise QCError(f"unknown QC gate: {gate}")
    qc_path = Path(path)
    qc = load_qc(qc_path)
    previous_gate = qc["gates"].get(gate) or {}
    previous = previous_gate.get("items") or {}
    expected = (
        None
        if expected_item_ids is None
        else {str(item_id) for item_id in expected_item_ids}
    )
    if expected is not None:
        if not expected:
            raise QCError("expected QC item set must not be empty")
        unknown = sorted(set(artifacts) - expected)
        if unknown:
            raise QCError(
                f"unexpected {gate} QC items: {', '.join(unknown)}"
            )
        items: dict[str, Any] = {
            str(item_id): dict(item)
            for item_id, item in previous.items()
            if str(item_id) in expected and isinstance(item, Mapping)
        }
    else:
        items = {}
    for item_id, (artifact, probe) in artifacts.items():
        artifact_path = Path(artifact).resolve()
        digest = artifact_sha256(artifact_path)
        old = previous.get(item_id) if isinstance(previous, dict) else None
        unchanged = (
            isinstance(old, dict)
            and old.get("artifact_sha256") == digest
            and old.get("technical_passed") is True
            and old.get("stale") is not True
        )
        items[item_id] = {
            "artifact": str(artifact_path),
            "artifact_sha256": digest,
            "technical_passed": True,
            "probe": dict(probe),
            "decision": old.get("decision") if unchanged else None,
            "reviewer": old.get("reviewer") if unchanged else None,
            "reason": old.get("reason", "") if unchanged else "",
            "reviewed_at": old.get("reviewed_at") if unchanged else None,
            # 处置元数据跟裁决同生死；历史（history）跨次保留——它记录的是「这一镜之前
            # 为什么被退回」，换了新产物也还是有用的审片上下文（DESIGN §24）。
            **{key: (old.get(key) if unchanged else None) for key in _META_KEYS},
            **({"history": list(old["history"])} if isinstance(old, dict) and isinstance(old.get("history"), list) else {}),
            # This entry is the *new* technical result, so its artifact hash is
            # current.  A changed artifact invalidates the old human decision,
            # but must not force an impossible second technical-QC pass.
            "stale": False,
        }
    complete = bool(items) and (expected is None or set(items) == expected)
    status = (
        "accepted"
        if complete
        and all(
            item.get("technical_passed") is True
            and item.get("decision") in _PASSING_DECISIONS
            for item in items.values()
        )
        else "pending_review"
    )
    previous_evidence = (
        previous_gate.get("evidence") or [] if expected is not None else []
    )
    gate_data = {
        "status": status,
        "items": items,
        "evidence": list(
            dict.fromkeys(
                [
                    *(str(Path(item)) for item in previous_evidence),
                    *(str(Path(item)) for item in evidence),
                ]
            )
        ),
        "technical_checked_at": _now(),
    }
    if expected is not None:
        gate_data["expected_items"] = sorted(expected)
    qc["gates"][gate] = gate_data
    qc["updated_at"] = _now()
    _atomic_json(qc_path, qc)
    return qc


def invalidate(
    path: str | Path,
    gate: str,
    *,
    item_ids: Iterable[str] | None = None,
    reason: str,
) -> dict[str, Any]:
    """Close a QC gate because an upstream artifact is about to change.

    Existing decisions remain visible as audit context, but affected items lose
    their technical pass and become stale.  Even if a replacement happens to
    have identical bytes, :func:`record_technical_results` will require a fresh
    decision instead of silently reopening the gate.
    """

    if gate not in GATES:
        raise QCError(f"unknown QC gate: {gate}")
    normalized_reason = str(reason).strip()
    if not normalized_reason:
        raise QCError("QC invalidation requires a reason")
    qc_path = Path(path)
    qc = load_qc(qc_path)
    gate_data = qc["gates"].get(gate) or {"status": "not_ready", "items": {}}
    items = gate_data.get("items")
    if not isinstance(items, dict):
        items = {}
        gate_data["items"] = items
    selected = set(items) if item_ids is None else {str(item) for item in item_ids}
    invalidated_at = _now()
    for item_id in selected:
        item = items.get(item_id)
        if not isinstance(item, dict):
            continue
        item.update(
            {
                "technical_passed": False,
                "stale": True,
                "invalidated_at": invalidated_at,
                "invalidation_reason": normalized_reason,
            }
        )
    gate_data.update(
        {
            "status": "not_ready",
            "invalidated_at": invalidated_at,
            "invalidation_reason": normalized_reason,
            "invalidated_items": sorted(selected),
        }
    )
    qc["gates"][gate] = gate_data
    qc["updated_at"] = invalidated_at
    _atomic_json(qc_path, qc)
    return qc


def begin_retake(
    path: str | Path,
    gate: str,
    *,
    selected_item_ids: Iterable[str],
    affected_item_ids: Iterable[str],
    reviewer: str,
    reason: str,
) -> dict[str, Any]:
    """Persist a resumable retake intent before any job state is reset."""

    if gate not in {"frames", "videos"}:
        raise QCError("only frame and video gates support retake intents")
    selected = sorted({str(item) for item in selected_item_ids})
    affected = sorted({str(item) for item in affected_item_ids})
    normalized_reason = str(reason).strip()
    normalized_reviewer = str(reviewer).strip()
    if not selected or not set(selected).issubset(affected):
        raise QCError("retake items must be a non-empty subset of affected items")
    if not normalized_reason or not normalized_reviewer:
        raise QCError("retake intent requires a reviewer and reason")
    qc_path = Path(path)
    qc = load_qc(qc_path)
    gate_data = qc["gates"].get(gate) or {}
    items = gate_data.get("items")
    if not isinstance(items, dict):
        raise QCError(f"{gate} gate has no reviewed artifacts")
    for item_id in selected:
        item = items.get(item_id)
        if not isinstance(item, dict) or item.get("decision") != "retake":
            raise QCError(f"{gate}/{item_id} has no recorded retake decision")
    existing = gate_data.get("retake_pending")
    intended = {
        "selected_items": selected,
        "affected_items": affected,
        "reviewer": normalized_reviewer,
        "reason": normalized_reason,
    }
    if isinstance(existing, dict):
        comparable = {key: existing.get(key) for key in intended}
        if comparable != intended:
            raise QCError(f"{gate} already has a different pending retake")
        return qc
    intended["started_at"] = _now()
    gate_data["retake_pending"] = intended
    qc["gates"][gate] = gate_data
    qc["updated_at"] = intended["started_at"]
    _atomic_json(qc_path, qc)
    return qc


def complete_retake(path: str | Path, gate: str) -> dict[str, Any]:
    """Mark a persisted retake intent complete after every reset is durable."""

    if gate not in {"frames", "videos"}:
        raise QCError("only frame and video gates support retake intents")
    qc_path = Path(path)
    qc = load_qc(qc_path)
    gate_data = qc["gates"].get(gate) or {}
    pending = gate_data.pop("retake_pending", None)
    if not isinstance(pending, dict):
        return qc
    completed_at = _now()
    gate_data["last_retake"] = {**pending, "completed_at": completed_at}
    qc["gates"][gate] = gate_data
    qc["updated_at"] = completed_at
    _atomic_json(qc_path, qc)
    return qc


def review(
    path: str | Path,
    gate: str,
    decision: str,
    *,
    item_ids: Iterable[str] | None = None,
    reviewer: str = "human",
    reason: str = "",
    evidence: Iterable[str | Path] = (),
    defect: str | None = None,
    disposition: str | None = None,
) -> dict[str, Any]:
    """Record a human/agent verdict without triggering generation.

    ``defect`` / ``disposition`` are optional structured context for the verdict
    (see :data:`DEFECTS` / :data:`DISPOSITIONS`).  They are stored on the item
    next to ``reason`` and appended to the item's ``history`` so a reviewer can
    see whether the same defect has already come back once.
    """

    if gate not in GATES:
        raise QCError(f"unknown QC gate: {gate}")
    decision = str(decision).strip().lower()
    if decision not in DECISIONS:
        raise QCError(f"unsupported QC decision: {decision}")
    reviewer = str(reviewer).strip()
    if not reviewer:
        raise QCError("reviewer must not be empty")
    reason = str(reason).strip()
    if decision in {"retake", "edit_rescue", "redesign", "waive"} and not reason:
        raise QCError(f"{decision} requires a reason")
    defect = normalize_defect(defect)
    disposition = normalize_disposition(disposition)

    qc_path = Path(path)
    qc = load_qc(qc_path)
    gate_data = qc["gates"].get(gate) or {}
    items = gate_data.get("items")
    if not isinstance(items, dict) or not items:
        raise QCError(f"{gate} gate has no technically validated artifacts")
    selected = list(item_ids or items.keys())
    unknown = [item_id for item_id in selected if item_id not in items]
    if unknown:
        raise QCError(f"unknown {gate} QC items: {', '.join(unknown)}")
    if not selected:
        raise QCError("at least one QC item is required")
    reviewed_at = _now()
    for item_id in selected:
        item = items[item_id]
        if item.get("technical_passed") is not True:
            raise QCError(f"{gate}/{item_id} has not passed technical QC")
        item.update(
            {
                "decision": decision,
                "reviewer": reviewer,
                "reason": reason,
                "reviewed_at": reviewed_at,
                "defect": defect,
                "disposition": disposition,
                "stale": False,
            }
        )
        history = item.get("history")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "decision": decision,
                "defect": defect,
                "disposition": disposition,
                "reason": reason,
                "reviewer": reviewer,
                "reviewed_at": reviewed_at,
                "artifact_sha256": item.get("artifact_sha256"),
            }
        )
        item["history"] = history[-_HISTORY_LIMIT:]
    extra_evidence = [str(Path(item)) for item in evidence]
    gate_data["evidence"] = list(
        dict.fromkeys([*(gate_data.get("evidence") or []), *extra_evidence])
    )
    decisions = {item.get("decision") for item in items.values()}
    raw_expected = gate_data.get("expected_items")
    expected = (
        {str(item_id) for item_id in raw_expected}
        if isinstance(raw_expected, list)
        else None
    )
    complete = bool(items) and (expected is None or set(items) == expected)
    technically_current = complete and all(
        item.get("technical_passed") is True and item.get("stale") is not True
        for item in items.values()
    )
    if technically_current and decisions.issubset(_PASSING_DECISIONS):
        gate_data["status"] = "accepted"
    elif decisions & {"retake", "edit_rescue", "redesign"}:
        gate_data["status"] = "revision_requested"
    else:
        gate_data["status"] = "pending_review"
    gate_data["reviewed_at"] = reviewed_at
    qc["gates"][gate] = gate_data
    qc["updated_at"] = reviewed_at
    _atomic_json(qc_path, qc)
    return qc


def normalize_defect(value: Any) -> str | None:
    """Return a canonical defect id or ``None``; unknown ids are an error."""

    text = str(value or "").strip().lower()
    if not text:
        return None
    if text not in DEFECTS:
        raise QCError(f"unsupported retake defect: {text}（可选：{' / '.join(DEFECTS)}）")
    return text


def normalize_disposition(value: Any) -> str | None:
    """Return a canonical disposition id or ``None``; unknown ids are an error."""

    text = str(value or "").strip().lower()
    if not text:
        return None
    if text not in DISPOSITIONS:
        raise QCError(f"unsupported retake disposition: {text}（可选：{' / '.join(DISPOSITIONS)}）")
    return text


def item_meta(qc: Mapping[str, Any], gate: str, item_id: str) -> dict[str, Any] | None:
    """The structured review context of one QC item (``None`` when never reviewed).

    Old manifests without ``defect`` / ``disposition`` / ``history`` load as
    ``None`` / empty; nothing here writes.
    """

    gate_data = (qc.get("gates") or {}).get(gate) if isinstance(qc, Mapping) else None
    items = gate_data.get("items") if isinstance(gate_data, Mapping) else None
    item = items.get(str(item_id)) if isinstance(items, Mapping) else None
    if not isinstance(item, Mapping):
        return None
    history = [dict(entry) for entry in item.get("history") or () if isinstance(entry, Mapping)]
    if not item.get("decision") and not history:
        return None
    # 刚重出的新产物还没审（decision 为空）时，最有用的上下文正是「上次为什么退回」
    last_retake = next((entry for entry in reversed(history) if entry.get("decision") == "retake"), None)
    last_defect = last_retake.get("defect") if last_retake else None
    return {
        "decision": item.get("decision"),
        "defect": item.get("defect"),
        "disposition": item.get("disposition"),
        "reason": item.get("reason") or "",
        "reviewer": item.get("reviewer"),
        "reviewed_at": item.get("reviewed_at"),
        "stale": bool(item.get("stale")),
        "history": history,
        "last_retake": dict(last_retake) if last_retake else None,
        # 同一缺陷退回了几次：第二次出现就该改结构，不该再原样重提
        "same_defect_retakes": sum(
            1 for entry in history
            if entry.get("decision") == "retake" and last_defect and entry.get("defect") == last_defect
        ),
    }


def gate_status(path: str | Path, gate: str) -> str:
    if gate not in GATES:
        raise QCError(f"unknown QC gate: {gate}")
    return str(load_qc(path)["gates"][gate].get("status") or "not_ready")


def gate_accepted(path: str | Path, gate: str) -> bool:
    return gate_status(path, gate) == "accepted"


__all__ = [
    "DECISIONS",
    "DEFECTS",
    "DISPOSITIONS",
    "GATES",
    "QCError",
    "artifact_sha256",
    "begin_retake",
    "complete_retake",
    "gate_accepted",
    "gate_status",
    "invalidate",
    "item_meta",
    "load_qc",
    "normalize_defect",
    "normalize_disposition",
    "record_technical_results",
    "review",
]
