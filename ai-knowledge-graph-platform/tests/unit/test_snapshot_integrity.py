"""Snapshot integrity verification — the proof behind the backup claim.

`GraphSnapshotService.verify_integrity` is what makes "the restored graph is
the graph we backed up" a checkable statement rather than an assumption. It had
no test at all, while `docs/public-local-evaluation-report.md` published a
"backup-restore-integrity" control as passing.

The failure mode is the dangerous kind: a verifier that returns True for
everything looks identical to a working one until the day a restore is
silently wrong. So these tests care less about the happy path than about
whether the verifier can actually *say no* — to a mutated field, a missing
hash, and a hash computed over different content.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from graphrag.graph.graph_snapshots import GraphSnapshotService


def _snapshot(**overrides) -> dict:
    """A snapshot row shaped like the node `create_snapshot` writes."""
    snapshot = {
        "tenant": "aerospace",
        "label": "nightly",
        "entity_count": 1200,
        "edge_count": 4300,
        "negative_count": 12,
        "conflict_count": 3,
        "community_count": 55,
        "orphan_count": 7,
        "avg_confidence": 0.82,
        "alias_coverage": 0.91,
        "high_conf_rate": 0.74,
        "contradiction_rate": 0.01,
        "orphan_rate": 0.006,
        "community_coherence": 0.66,
    }
    snapshot.update(overrides)
    snapshot["integrity_hash"] = _expected_hash(snapshot)
    return snapshot


def _expected_hash(snapshot: dict) -> str:
    """Recompute the hash the way `create_snapshot` does at write time.

    Deliberately mirrors the producer rather than calling the verifier's own
    helper: a test that derived the expected value from the code under test
    would pass even if both sides drifted together.
    """
    payload = {
        "tenant": snapshot["tenant"],
        "label": snapshot["label"],
        "stats": {
            "entity_count": snapshot["entity_count"],
            "edge_count": snapshot["edge_count"],
            "negative_count": snapshot["negative_count"],
            "conflict_count": snapshot["conflict_count"],
            "community_count": snapshot["community_count"],
            "orphan_count": snapshot["orphan_count"],
            "avg_confidence": snapshot["avg_confidence"],
        },
        "health": {
            "alias_coverage": snapshot["alias_coverage"],
            "high_conf_rate": snapshot["high_conf_rate"],
            "contradiction_rate": snapshot["contradiction_rate"],
            "orphan_rate": snapshot["orphan_rate"],
            "community_coherence": snapshot["community_coherence"],
        },
        "schema_version": "graph-snapshot/v1",
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class TestIntactSnapshotVerifies:
    def test_an_unmodified_snapshot_passes(self):
        assert GraphSnapshotService.verify_integrity(_snapshot()) is True

    def test_verification_is_independent_of_key_order(self):
        snapshot = _snapshot()
        reordered = dict(reversed(list(snapshot.items())))
        assert GraphSnapshotService.verify_integrity(reordered) is True


class TestTamperingIsDetected:
    @pytest.mark.parametrize("field, mutated", [
        ("entity_count", 1201),          # one entity lost in transport
        ("edge_count", 0),               # catastrophic, must not pass
        ("tenant", "automotive"),        # restored into the wrong tenant
        ("label", "nightly-2"),
        ("avg_confidence", 0.83),
        ("conflict_count", 4),
        ("community_count", 54),
        ("orphan_count", 8),
        ("negative_count", 13),
    ])
    def test_a_mutated_field_fails_verification(self, field, mutated):
        snapshot = _snapshot()
        assert snapshot[field] != mutated, "parametrised value must differ"
        snapshot[field] = mutated  # hash now describes the pre-mutation content
        assert GraphSnapshotService.verify_integrity(snapshot) is False

    @pytest.mark.parametrize("field, mutated", [
        ("alias_coverage", 0.92),
        ("high_conf_rate", 0.75),
        ("contradiction_rate", 0.02),
        ("orphan_rate", 0.007),
        ("community_coherence", 0.67),
    ])
    def test_health_fields_are_covered_by_the_hash_too(self, field, mutated):
        # Health metrics are part of what a restore is expected to reproduce;
        # excluding them from the hash would let quality silently regress
        # across a restore while integrity still reported clean.
        snapshot = _snapshot()
        snapshot[field] = mutated
        assert GraphSnapshotService.verify_integrity(snapshot) is False

    def test_a_forged_hash_fails(self):
        snapshot = _snapshot()
        snapshot["integrity_hash"] = "0" * 64
        assert GraphSnapshotService.verify_integrity(snapshot) is False


class TestMissingHashIsNotTrusted:
    @pytest.mark.parametrize("value", ["", None])
    def test_absent_hash_fails_closed(self, value):
        # A snapshot with no hash is unverifiable, not verified. Returning True
        # here would mean any snapshot could opt out of integrity by omitting
        # the field -- the one shape an attacker or a lossy exporter produces.
        snapshot = _snapshot()
        snapshot["integrity_hash"] = value
        assert GraphSnapshotService.verify_integrity(snapshot) is False

    def test_snapshot_without_the_key_at_all_fails_closed(self):
        snapshot = _snapshot()
        del snapshot["integrity_hash"]
        assert GraphSnapshotService.verify_integrity(snapshot) is False

    def test_empty_snapshot_fails_closed(self):
        assert GraphSnapshotService.verify_integrity({}) is False


class TestVerifierCanActuallySayNo:
    def test_it_is_not_a_constant_true(self):
        """Guards against the verifier degrading into `return True`.

        Every other test here would still pass against a stub that always
        returned True for well-formed input, so this asserts the negative
        directly: at least one realistic corruption must be rejected.
        """
        intact = _snapshot()
        corrupted = _snapshot()
        corrupted["entity_count"] += 1

        assert GraphSnapshotService.verify_integrity(intact) is True
        assert GraphSnapshotService.verify_integrity(corrupted) is False
