from unittest.mock import AsyncMock, patch

from graphrag.core.lifecycle import close_shared_resources


async def test_shutdown_closes_every_initialized_boundary_even_after_one_failure() -> None:
    rabbit = AsyncMock(side_effect=RuntimeError("close failed"))
    neo4j = AsyncMock()
    result_store = AsyncMock()
    session_store = AsyncMock()

    with (
        patch("graphrag.messaging.rabbitmq_client.close_rabbitmq", rabbit),
        patch("graphrag.graph.neo4j_client.close_neo4j", neo4j),
        patch("graphrag.retrieval.result_store.close_result_store", result_store),
        patch("graphrag.retrieval.session_store.close_session_store", session_store),
        patch("graphrag.observability.tracing.shutdown_tracing") as tracing,
    ):
        await close_shared_resources()

    rabbit.assert_awaited_once()
    neo4j.assert_awaited_once()
    result_store.assert_awaited_once()
    session_store.assert_awaited_once()
    tracing.assert_called_once()
