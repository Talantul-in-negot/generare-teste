from __future__ import annotations

from unittest.mock import AsyncMock

from graphrag.graph.reification import ReificationService


async def test_statement_metadata_update_is_tenant_scoped() -> None:
    neo4j = AsyncMock()
    neo4j.run = AsyncMock(return_value=[])

    await ReificationService(neo4j).add_meta(
        stmt_id="stmt-1", key="review_state", value="approved", tenant="marketing"
    )

    cypher = neo4j.run.await_args.args[0]
    assert "Statement {id: $stmt_id, tenant: $tenant}" in cypher
    assert neo4j.run.await_args.kwargs["tenant"] == "marketing"


async def test_statement_endorsement_cannot_merge_an_unscoped_endorser() -> None:
    neo4j = AsyncMock()
    neo4j.run = AsyncMock(return_value=[])

    await ReificationService(neo4j).endorse(
        stmt_id="stmt-1",
        endorser_id="doc-1",
        endorser_type="Document",
        tenant="marketing",
    )

    cypher = neo4j.run.await_args.args[0]
    assert "MATCH (endorser {id: $endorser_id, tenant: $tenant})" in cypher
    assert "MERGE (endorser" not in cypher
    assert neo4j.run.await_args.kwargs["tenant"] == "marketing"


async def test_statement_contradiction_requires_a_single_tenant() -> None:
    neo4j = AsyncMock()
    neo4j.run = AsyncMock(return_value=[])

    await ReificationService(neo4j).contradict(
        stmt_a_id="stmt-a", stmt_b_id="stmt-b", tenant="marketing"
    )

    cypher = neo4j.run.await_args.args[0]
    assert "Statement {id: $a_id, tenant: $tenant}" in cypher
    assert "Statement {id: $b_id, tenant: $tenant}" in cypher
    assert neo4j.run.await_args.kwargs["tenant"] == "marketing"
