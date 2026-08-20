"""Time-series KPI storage — SQLite (portable, zero external dependency)."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Column, DateTime, Float, String, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class KPIEventRow(Base):
    __tablename__ = "kpi_events"

    # Timescale hypertables require every unique constraint to include the
    # partitioning column. Event IDs remain unique per recorded event while
    # allowing the same schema to serve SQLite and TimescaleDB.
    event_id = Column(String, primary_key=True)
    query_id = Column(String, nullable=False, index=True)
    tenant = Column(String, nullable=False, default="default", index=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, primary_key=True, index=True)
    latency_ms = Column(Float, nullable=False)
    faithfulness = Column(Float, default=0.0)
    answer_relevancy = Column(Float, default=0.0)
    context_precision = Column(Float, default=0.0)
    context_recall = Column(Float, default=0.0)
    cost_usd = Column(Float, default=0.0)
    retrieval_mode = Column(String, default="hybrid")
    model_version = Column(String, default="")


_engine: AsyncEngine | None = None
_session_factory = None


async def ensure_tenant_column(conn) -> None:
    """Backfill the tenant column for pre-isolation KPI databases."""
    has_tenant = await conn.run_sync(
        lambda sync_conn: "tenant" in {
            column["name"] for column in inspect(sync_conn).get_columns("kpi_events")
        }
    )
    if not has_tenant:
        await conn.execute(
            text("ALTER TABLE kpi_events ADD COLUMN tenant VARCHAR NOT NULL DEFAULT 'default'")
        )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_kpi_events_tenant_recorded_at "
             "ON kpi_events (tenant, recorded_at)")
    )


def _get_db_url() -> str:
    timescale_url = os.getenv("TIMESCALE_DB_URL")
    if timescale_url and os.getenv("KPI_BACKEND", "sqlite").lower() == "timescale":
        return timescale_url
    db_path = Path(os.getenv("KPI_DB_PATH", "results/kpi_snapshots/kpis.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path}"


async def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        url = _get_db_url()
        _engine = create_async_engine(url, echo=False)
        _session_factory = sessionmaker(
            _engine, class_=AsyncSession, expire_on_commit=False
        )
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await ensure_tenant_column(conn)
    return _engine


async def get_session() -> AsyncSession:
    await get_engine()
    return _session_factory()
