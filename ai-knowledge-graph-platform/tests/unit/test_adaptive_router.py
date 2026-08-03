from unittest.mock import AsyncMock, MagicMock

from graphrag.retrieval.adaptive_router import AdaptiveRetrievalRouter


async def test_router_uses_planner_during_cold_start():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[])
    router = AdaptiveRetrievalRouter(neo4j, exploration_rate=0.0)

    decision = await router.choose("What is the approved limit?", "marketing")

    assert decision.mode == "local"
    assert decision.reason == "planner_cold_start"
    assert neo4j.run.await_args.kwargs["tenant"] == "marketing"


async def test_router_selects_best_mature_measured_route():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[
        {"mode": "local", "sample_count": 20, "quality_ewma": 0.91, "latency_ewma_ms": 500},
        {"mode": "global", "sample_count": 20, "quality_ewma": 0.72, "latency_ewma_ms": 4000},
    ])
    router = AdaptiveRetrievalRouter(neo4j, exploration_rate=0.0)

    decision = await router.choose("What is the approved limit?", "marketing")

    assert decision.mode == "local"
    assert decision.reason == "measured_utility"


async def test_router_observation_is_tenant_scoped_and_bounded():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[])
    router = AdaptiveRetrievalRouter(neo4j)

    await router.observe(
        tenant="marketing", question="Compare multiple policies", mode="hybrid",
        latency_ms=-3, quality=4.0,
    )

    kwargs = neo4j.run.await_args.kwargs
    assert kwargs["tenant"] == "marketing"
    assert kwargs["latency_ms"] == 0.0
    assert kwargs["quality"] == 1.0
    assert kwargs["query_class"] == "multi_hop"
