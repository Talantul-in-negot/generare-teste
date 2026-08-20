"""Regression tests for current-project findings in context_graph_gap_plan.md."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def test_ingestion_consumer_schedules_calibration_for_document_tenant():
    from graphrag.messaging.consumers import IngestionConsumer

    mq = MagicMock()
    captured = {}

    async def consume(*_args):
        captured["handler"] = _args[-1]

    mq.consume = consume
    agent = MagicMock()
    agent.run = AsyncMock()
    scheduler = MagicMock()
    scheduler.maybe_schedule = AsyncMock()

    with (
        patch("graphrag.agents.ingestion_agent.IngestionAgent", return_value=agent),
        patch("graphrag.messaging.consumers.get_rabbitmq", AsyncMock(return_value=mq)),
        patch("graphrag.graph.calibration_scheduler.GNNCalibrationScheduler", return_value=scheduler),
        patch("graphrag.graph.neo4j_client.get_neo4j", return_value=MagicMock()),
    ):
        await IngestionConsumer().start()
        await captured["handler"]({
            "job_id": "job-1",
            "document": {
                "filename": "source.txt", "source_path": "source.txt", "raw_text": "x",
                "tenant": "tenant-a",
            },
        })

    scheduler.maybe_schedule.assert_awaited_once_with("tenant-a", execute=True)


def test_inference_route_adds_tenant_ontology_rules():
    from api.routes.kg.inference import _engine_for_tenant

    with patch("graphrag.graph.neo4j_client.get_neo4j", return_value=MagicMock()):
        engine = _engine_for_tenant("aerospace")

    assert any(rule.name == "supersedes_transitivity" for rule in engine._rules)


def test_post_write_validator_uses_the_canonical_relation_rules():
    from graphrag.graph.ingestion_validator import RELATION_RULES
    from graphrag.graph.ontology_registry import _RELATION_RULES

    assert RELATION_RULES is _RELATION_RULES
    assert "PART_OF" in RELATION_RULES


class _Result:
    def all(self):
        return []


class _Session:
    def __init__(self):
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result()


async def test_kpi_timeseries_filters_by_tenant():
    from graphrag.business_matrix.kpi_tracker import KPITracker

    session = _Session()

    async def get_session():
        return session

    with patch("graphrag.business_matrix.kpi_tracker.get_session", get_session):
        await KPITracker().get_timeseries(tenant="tenant-a")

    sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "kpi_events.tenant = 'tenant-a'" in sql


async def test_kpi_queries_require_a_nonempty_tenant():
    from graphrag.business_matrix.kpi_tracker import KPITracker

    with pytest.raises(ValueError, match="tenant is required"):
        await KPITracker().get_timeseries(tenant="")
