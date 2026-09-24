from pathlib import Path

import pytest

from storyvideo.qc import (
    QCError,
    gate_accepted,
    invalidate,
    load_qc,
    record_technical_results,
    review,
)


def artifact(tmp_path: Path, name: str, payload: bytes = b"artifact") -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def test_technical_qc_requires_an_explicit_human_decision(tmp_path):
    qc_path = tmp_path / "qc.json"
    frame = artifact(tmp_path, "S1.png")

    qc = record_technical_results(
        qc_path,
        "frames",
        {"S1": (frame, {"width": 1080, "height": 1920})},
    )

    assert qc["gates"]["frames"]["status"] == "pending_review"
    assert gate_accepted(qc_path, "frames") is False

    accepted = review(qc_path, "frames", "pass", reviewer="director")
    assert accepted["gates"]["frames"]["status"] == "accepted"
    assert gate_accepted(qc_path, "frames") is True


def test_changed_artifact_invalidates_previous_acceptance(tmp_path):
    qc_path = tmp_path / "qc.json"
    frame = artifact(tmp_path, "S1.png", b"take-one")
    record_technical_results(qc_path, "frames", {"S1": (frame, {})})
    review(qc_path, "frames", "pass")

    frame.write_bytes(b"take-two")
    changed = record_technical_results(qc_path, "frames", {"S1": (frame, {})})
    item = changed["gates"]["frames"]["items"]["S1"]

    assert changed["gates"]["frames"]["status"] == "pending_review"
    assert item["decision"] is None
    assert item["stale"] is False


@pytest.mark.parametrize("decision", ["retake", "edit_rescue", "redesign", "waive"])
def test_nontrivial_qc_decisions_require_a_reason(tmp_path, decision):
    qc_path = tmp_path / "qc.json"
    frame = artifact(tmp_path, "S1.png")
    record_technical_results(qc_path, "frames", {"S1": (frame, {})})

    with pytest.raises(QCError, match="requires a reason"):
        review(qc_path, "frames", decision)


def test_partial_review_does_not_open_the_gate(tmp_path):
    qc_path = tmp_path / "qc.json"
    first = artifact(tmp_path, "S1.png", b"one")
    second = artifact(tmp_path, "S2.png", b"two")
    record_technical_results(
        qc_path,
        "frames",
        {"S1": (first, {}), "S2": (second, {})},
    )

    review(qc_path, "frames", "pass", item_ids=["S1"])

    assert load_qc(qc_path)["gates"]["frames"]["status"] == "pending_review"


def test_invalidation_requires_fresh_technical_qc_and_review_even_for_same_bytes(
    tmp_path,
):
    qc_path = tmp_path / "qc.json"
    frame = artifact(tmp_path, "S1.png", b"same-take")
    record_technical_results(qc_path, "frames", {"S1": (frame, {})})
    review(qc_path, "frames", "pass")

    invalidated = invalidate(
        qc_path,
        "frames",
        item_ids=["S1"],
        reason="upstream retake",
    )
    item = invalidated["gates"]["frames"]["items"]["S1"]
    assert invalidated["gates"]["frames"]["status"] == "not_ready"
    assert item["technical_passed"] is False
    assert item["stale"] is True
    with pytest.raises(QCError, match="technical QC"):
        review(qc_path, "frames", "pass")

    refreshed = record_technical_results(qc_path, "frames", {"S1": (frame, {})})
    refreshed_item = refreshed["gates"]["frames"]["items"]["S1"]
    assert refreshed["gates"]["frames"]["status"] == "pending_review"
    assert refreshed_item["decision"] is None
    assert refreshed_item["stale"] is False
    assert gate_accepted(qc_path, "frames") is False


def test_incremental_technical_qc_preserves_items_and_never_accepts_a_partial_gate(
    tmp_path,
):
    qc_path = tmp_path / "qc.json"
    first = artifact(tmp_path, "P01.png", b"one")
    second = artifact(tmp_path, "P02.png", b"two")

    partial = record_technical_results(
        qc_path,
        "frames",
        {"P01": (first, {})},
        expected_item_ids={"P01", "P02"},
    )
    reviewed = review(
        qc_path, "frames", "pass", item_ids={"P01"}, reviewer="director"
    )

    assert partial["gates"]["frames"]["expected_items"] == ["P01", "P02"]
    assert reviewed["gates"]["frames"]["status"] == "pending_review"
    assert set(reviewed["gates"]["frames"]["items"]) == {"P01"}

    merged = record_technical_results(
        qc_path,
        "frames",
        {"P02": (second, {})},
        expected_item_ids={"P01", "P02"},
    )

    assert set(merged["gates"]["frames"]["items"]) == {"P01", "P02"}
    assert merged["gates"]["frames"]["items"]["P01"]["decision"] == "pass"
    assert merged["gates"]["frames"]["items"]["P02"]["decision"] is None
    assert merged["gates"]["frames"]["status"] == "pending_review"
