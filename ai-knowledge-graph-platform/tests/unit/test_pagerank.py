"""Unit tests for PageRankComputer.check_staleness() — the recompute triggers.

Covers the three triggers added 2026-07-25 (growth drift, re-ingestion,
decay-conditional time ceiling) after a live check found real, uneven
PageRank coverage across tenants with no automatic refresh mechanism.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch


from graphrag.graph.pagerank import PageRankComputer


def _make_computer(
    graph_cfg_overrides: dict | None = None,
    retrieval_cfg_overrides: dict | None = None,
) -> tuple[PageRankComputer, AsyncMock]:
    graph_cfg = {
        "pagerank_damping_factor": 0.85,
        "pagerank_iterations": 20,
        "pagerank_recompute_on_ingest": True,
        "pagerank_growth_threshold": 0.15,
        "pagerank_time_ceiling_days": 60,
    }
    if graph_cfg_overrides:
        graph_cfg.update(graph_cfg_overrides)

    retrieval_cfg = {"gnn_confidence_half_life_days": 0}
    if retrieval_cfg_overrides:
        retrieval_cfg.update(retrieval_cfg_overrides)

    with (
        patch("graphrag.graph.pagerank.get_settings") as mock_settings,
        patch("graphrag.graph.pagerank.get_neo4j") as mock_get_neo4j,
    ):
        mock_settings.return_value.graph = graph_cfg
        mock_settings.return_value.retrieval = retrieval_cfg
        neo4j = AsyncMock()
        mock_get_neo4j.return_value = neo4j
        computer = PageRankComputer(tenant="acme")

    return computer, neo4j


def _snapshot_row(entity_count: int, edge_count: int, recorded_at) -> dict:
    return {"entity_count": entity_count, "edge_count": edge_count, "recorded_at": recorded_at}


class TestReingestTrigger:
    async def test_reingest_forces_recompute_regardless_of_drift(self):
        """Even with zero drift (or no snapshot query run at all), is_reingest=True
        must short-circuit straight to should_recompute=True."""
        computer, neo4j = _make_computer()

        result = await computer.check_staleness(is_reingest=True)

        assert result["should_recompute"] is True
        assert result["reason"] == "reingest"
        neo4j.run.assert_not_called()  # bypassed the drift calculation entirely


class TestNeverComputedTrigger:
    async def test_no_snapshot_yet_always_recomputes(self):
        computer, neo4j = _make_computer()
        neo4j.run = AsyncMock(return_value=[])  # no PageRankSnapshot found

        result = await computer.check_staleness(is_reingest=False)

        assert result["should_recompute"] is True
        assert result["reason"] == "never_computed"


class TestGrowthDriftTrigger:
    async def test_drift_above_threshold_triggers_recompute(self):
        computer, neo4j = _make_computer({"pagerank_growth_threshold": 0.15})
        now = datetime.now(timezone.utc)
        # Snapshot: 100 entities/edges. Current: 130 entities/edges = 30% drift.
        neo4j.run = AsyncMock(side_effect=[
            [_snapshot_row(100, 100, now)],
            [{"entities": 130, "edges": 130}],
        ])

        result = await computer.check_staleness(is_reingest=False)

        assert result["should_recompute"] is True
        assert result["reason"] == "growth_drift"

    async def test_drift_below_threshold_does_not_trigger(self):
        computer, neo4j = _make_computer({"pagerank_growth_threshold": 0.15})
        now = datetime.now(timezone.utc)
        # 5% drift — well under the 15% threshold.
        neo4j.run = AsyncMock(side_effect=[
            [_snapshot_row(100, 100, now)],
            [{"entities": 105, "edges": 105}],
        ])

        result = await computer.check_staleness(is_reingest=False)

        assert result["should_recompute"] is False
        assert result["reason"] == "up_to_date"


class TestDecayTimeCeiling:
    async def test_disabled_decay_never_triggers_time_ceiling(self):
        """gnn_confidence_half_life_days=0 (decay disabled) — even a very old
        snapshot with zero drift must NOT trigger a recompute."""
        computer, neo4j = _make_computer(
            {"pagerank_time_ceiling_days": 60},
            {"gnn_confidence_half_life_days": 0},
        )
        very_old = datetime.now(timezone.utc) - timedelta(days=400)
        neo4j.run = AsyncMock(side_effect=[
            [_snapshot_row(100, 100, very_old)],
            [{"entities": 100, "edges": 100}],  # zero drift
        ])

        result = await computer.check_staleness(is_reingest=False)

        assert result["should_recompute"] is False
        assert result["reason"] == "up_to_date"

    async def test_enabled_decay_triggers_time_ceiling_when_old(self):
        """gnn_confidence_half_life_days>0 (decay enabled) and the snapshot is
        older than the ceiling — must trigger even with zero drift."""
        computer, neo4j = _make_computer(
            {"pagerank_time_ceiling_days": 60},
            {"gnn_confidence_half_life_days": 180},
        )
        old = datetime.now(timezone.utc) - timedelta(days=90)
        neo4j.run = AsyncMock(side_effect=[
            [_snapshot_row(100, 100, old)],
            [{"entities": 100, "edges": 100}],  # zero drift
        ])

        result = await computer.check_staleness(is_reingest=False)

        assert result["should_recompute"] is True
        assert result["reason"] == "decay_time_ceiling"

    async def test_enabled_decay_but_within_ceiling_does_not_trigger(self):
        computer, neo4j = _make_computer(
            {"pagerank_time_ceiling_days": 60},
            {"gnn_confidence_half_life_days": 180},
        )
        recent = datetime.now(timezone.utc) - timedelta(days=10)
        neo4j.run = AsyncMock(side_effect=[
            [_snapshot_row(100, 100, recent)],
            [{"entities": 100, "edges": 100}],
        ])

        result = await computer.check_staleness(is_reingest=False)

        assert result["should_recompute"] is False
        assert result["reason"] == "up_to_date"
