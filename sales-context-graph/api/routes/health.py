"""§11 — '/health is process liveness. /ready checks Neo4j connectivity, schema
migration state, and required online indexes.'"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.core.config import get_settings
from src.core.neo4j_client import get_neo4j
from src.graph.execution import GraphExecutor
from src.graph.schema import ALL_CONSTRAINT_NAMES, ALL_INDEX_NAMES
from src.ingestion.queue import queue_health

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    executor = GraphExecutor(get_neo4j())
    try:
        rows = await executor.operational_query("SHOW INDEXES YIELD name, state RETURN name, state")
        constraint_rows = await executor.operational_query("SHOW CONSTRAINTS YIELD name RETURN name")
    except Exception as exc:  # neo4j.exceptions.* — connectivity/auth failures
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": f"neo4j unreachable: {exc}"},
        )

    states = {row["name"]: row["state"] for row in rows}
    missing = [name for name in ALL_INDEX_NAMES if name not in states]
    not_online = [name for name in ALL_INDEX_NAMES if name in states and states[name] != "ONLINE"]
    constraint_names = {row["name"] for row in constraint_rows}
    missing_constraints = [name for name in ALL_CONSTRAINT_NAMES if name not in constraint_names]

    if missing or not_online or missing_constraints:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "missing_indexes": missing,
                "indexes_not_online": not_online,
                "missing_constraints": missing_constraints,
            },
        )
    if get_settings().ingestion_queue_enabled:
        try:
            health = await queue_health()
        except Exception as exc:
            return JSONResponse(status_code=503, content={"status": "not_ready", "reason": f"redis unavailable: {exc}"})
        if not health["redis_available"] or not health["worker_alive"]:
            return JSONResponse(status_code=503, content={"status": "not_ready", "ingestion_queue": health})
    return JSONResponse(status_code=200, content={"status": "ready"})
