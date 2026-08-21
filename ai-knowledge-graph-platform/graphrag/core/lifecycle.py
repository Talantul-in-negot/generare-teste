"""Best-effort shutdown for process-wide database and messaging clients."""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


async def close_shared_resources() -> None:
    """Close initialized singletons and reset them for clean process reuse."""
    from graphrag.graph.neo4j_client import close_neo4j
    from graphrag.messaging.rabbitmq_client import close_rabbitmq
    from graphrag.retrieval.result_store import close_result_store
    from graphrag.retrieval.session_store import close_session_store

    closers = (
        ("rabbitmq", close_rabbitmq),
        ("neo4j", close_neo4j),
        ("result_store", close_result_store),
        ("session_store", close_session_store),
    )
    for component, closer in closers:
        try:
            await closer()
        except Exception as exc:  # shutdown must continue closing other clients
            log.warning(
                "shutdown.resource_close_failed",
                component=component,
                exception_type=type(exc).__name__,
            )

    try:
        from graphrag.observability.tracing import shutdown_tracing

        shutdown_tracing()
    except Exception as exc:
        log.warning("shutdown.tracing_failed", exception_type=type(exc).__name__)
