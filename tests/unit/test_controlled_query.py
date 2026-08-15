from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from graphrag.graph.controlled_query import (
    ControlledQueryError,
    execute_controlled_query,
    plan_controlled_query,
)
from mcp_server.tools import query_graph_facts


def test_supply_question_uses_fixed_parameterized_template():
    plan = plan_controlled_query(
        "What does Northwind Components supply?", tenant="sustainability", limit=500,
    )

    assert plan.intent == "entity_relation"
    assert plan.params == {
        "tenant": "sustainability", "name": "Northwind Components",
        "relation": "SUPPLIES", "limit": 100,
    }
    assert "MATCH (s:Entity {tenant: $tenant, name: $name})" in plan.cypher
    assert "CREATE" not in plan.cypher and "DELETE" not in plan.cypher


def test_entity_type_question_normalizes_type_without_executable_query_input():
    plan = plan_controlled_query("List commercial content", tenant="pharma")

    assert plan.intent == "entities_by_type"
    assert plan.params["entity_type"] == "COMMERCIAL_CONTENT"
    assert "$entity_type" in plan.cypher


def test_evidence_gap_question_uses_the_tenant_scoped_fixed_template():
    plan = plan_controlled_query("Which suppliers lack verified emissions evidence?", tenant="sustainability")

    assert plan.intent == "suppliers_missing_emissions_evidence"
    assert plan.params == {"tenant": "sustainability", "limit": 25}
    assert "HAS_EVIDENCE" in plan.cypher and "tenant: $tenant" in plan.cypher


def test_unsupported_question_fails_explicitly():
    with pytest.raises(ControlledQueryError, match="Unsupported graph-fact"):
        plan_controlled_query("MATCH (n) DETACH DELETE n", tenant="sustainability")


@pytest.mark.asyncio
async def test_execution_threads_tenant_and_bounded_limit():
    neo4j = AsyncMock()
    neo4j.run.return_value = [{"source": "Northwind Components", "relation": "SUPPLIES"}]

    result = await execute_controlled_query(
        neo4j, "What does Northwind Components supply?", tenant="sustainability", limit=1000,
    )

    _, params = neo4j.run.call_args
    assert params["tenant"] == "sustainability"
    assert params["limit"] == 100
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_mcp_tool_returns_an_explicit_fallback_for_unsupported_question():
    with patch("mcp_server.tools.get_neo4j"):
        result = await query_graph_facts("How should we decarbonize procurement?", tenant="sustainability")

    assert result["supported"] is False
    assert result["tenant"] == "sustainability"
