"""Opt-in live Neo4j Context Graph round-trip.

Run with ``RUN_LIVE_NEO4J=1`` and a reachable Neo4j instance. The default
suite skips this test so ordinary CI does not require infrastructure.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from graphrag.context_graph.repository import ContextGraphRepository
from graphrag.context_graph.trace_service import ContextGraphTraceService
from graphrag.context_graph.models import CGApproval, ApprovalStatus


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_NEO4J") != "1", reason="set RUN_LIVE_NEO4J=1 for live Neo4j integration"
)


class _DriverAdapter:
    def __init__(self, driver):
        self.driver = driver

    async def run(self, query: str, **params):
        async with self.driver.session() as session:
            result = await session.run(query, **params)
            return [dict(record) async for record in result]


async def test_live_wpp_trace_writes_and_reads_from_neo4j():
    from neo4j import AsyncGraphDatabase

    uri = os.getenv("NEO4J_LIVE_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_LIVE_USER", "neo4j")
    password = os.getenv("NEO4J_LIVE_PASSWORD", "graphrag_dev")
    tenant = f"live-cg-{uuid.uuid4().hex[:10]}"
    chunk_id = f"chunk-{tenant}"
    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    adapter = _DriverAdapter(driver)
    try:
        async with driver.session() as session:
            await session.run(
                "CREATE (c:Chunk {id: $id, tenant: $tenant, text: 'live evidence'})",
                id=chunk_id, tenant=tenant,
            )
        service = ContextGraphTraceService(ContextGraphRepository(adapter))
        decision_id = await service.record_wpp_campaign_placement(
            placement_id=f"live-{tenant}", question="Can the placement run?",
            statement_ids=[], statement_versions=[], chunk_ids=[chunk_id],
            chunk_versions=["live-v1"], document_ids=[], document_versions=[],
            tenant=tenant, selected="allow",
        )
        loaded = await ContextGraphRepository(adapter).load_trace(decision_id, tenant)
        assert loaded["decision"]["id"] == decision_id
        assert loaded["chunks"][0]["id"] == chunk_id
        replayed = await ContextGraphRepository(adapter).replay_trace(
            decision_id, tenant, datetime.now(timezone.utc).isoformat()
        )
        assert replayed["decision"]["id"] == decision_id
        repository = ContextGraphRepository(adapter)
        await repository.append_governance_event(CGApproval(
            id=f"approval-{tenant}", tenant=tenant, decision_id=decision_id,
            actor_id="integration-reviewer", status=ApprovalStatus.APPROVED,
            reason_code="live_expiry_check",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        ))
        governance = await repository.effective_governance(decision_id, tenant)
        assert governance["approval_effective"] is False
        retention = await repository.apply_retention_policy(
            tenant, datetime.now(timezone.utc) + timedelta(days=1),
            "integration-test", dry_run=False,
        )
        assert retention["marked"] == 1
    finally:
        async with driver.session() as session:
            await session.run("MATCH (n {tenant: $tenant}) DETACH DELETE n", tenant=tenant)
        await driver.close()
