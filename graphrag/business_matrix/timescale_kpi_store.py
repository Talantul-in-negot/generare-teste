"""Optional TimescaleDB KPI backend.

Set ``TIMESCALE_DB_URL`` to a PostgreSQL/Timescale SQLAlchemy URL and use
``KPI_BACKEND=timescale``. SQLite remains the default for local development.
"""

from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from graphrag.business_matrix.kpi_store import Base, KPIEventRow
from graphrag.core.models import KPIEvent


class TimescaleKPIStore:
    def __init__(self, url: str | None = None):
        db_url = url or os.getenv("TIMESCALE_DB_URL", "")
        if not db_url:
            raise ValueError("TIMESCALE_DB_URL is required for the Timescale backend")
        self._engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
        self._sessions = sessionmaker(self._engine, class_=AsyncSession, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text(
                """
                DO $$
                BEGIN
                  IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'kpi_events' AND column_name = 'recorded_at'
                      AND data_type = 'timestamp without time zone'
                  ) THEN
                    ALTER TABLE kpi_events
                      ALTER COLUMN recorded_at TYPE TIMESTAMPTZ
                      USING recorded_at AT TIME ZONE 'UTC';
                  END IF;
                END $$;
                """
            ))
            await conn.execute(text(
                "SELECT create_hypertable('kpi_events', 'recorded_at', if_not_exists => TRUE)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_kpi_events_tenant_stage "
                "ON kpi_events (query_id, recorded_at)"
            ))

    async def record(self, event: KPIEvent) -> None:
        async with self._sessions() as session:
            session.add(KPIEventRow(
                event_id=event.event_id, query_id=event.query_id,
                recorded_at=event.recorded_at, latency_ms=event.latency_ms,
                faithfulness=event.faithfulness, answer_relevancy=event.answer_relevancy,
                context_precision=event.context_precision, context_recall=event.context_recall,
                cost_usd=event.cost_usd, retrieval_mode=event.retrieval_mode,
                model_version=event.model_version,
            ))
            await session.commit()

    async def close(self) -> None:
        await self._engine.dispose()
