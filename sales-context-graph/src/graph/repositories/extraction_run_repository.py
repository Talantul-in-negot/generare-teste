"""Tenant-safe repository for ExtractionRun — same pattern as claim_repository.py.

P3.2: ExtractionRun (src/domain/assertion.py) previously had no repository at
all, so "which model/prompt version produced this specific assertion" could
not be queried even though the model existed. This closes that gap.
"""

from __future__ import annotations

from datetime import datetime

from src.domain.assertion import ExtractionRun
from src.graph.execution import GraphExecutor, scoped_match

_EXTRACTION_RUN_RETURN = (
    "er.extraction_run_id AS extraction_run_id, er.workspace_id AS workspace_id, "
    "er.provider AS provider, er.model AS model, er.prompt_version AS prompt_version, "
    "er.extractor_version AS extractor_version, er.run_nonce AS run_nonce, "
    "er.started_at AS started_at, er.completed_at AS completed_at"
)


class ExtractionRunRepository:
    def __init__(self, executor: GraphExecutor | None = None):
        self._executor = executor or GraphExecutor()

    async def create_extraction_run(self, run: ExtractionRun) -> None:
        match = scoped_match("ExtractionRun", "er", extraction_run_id="extraction_run_id")
        await self._executor.tenant_query(
            f"""
            MERGE {match}
            SET er.provider = $provider,
                er.model = $model,
                er.prompt_version = $prompt_version,
                er.extractor_version = $extractor_version,
                er.run_nonce = $run_nonce,
                er.started_at = $started_at,
                er.completed_at = $completed_at
            """,
            workspace_id=run.workspace_id,
            extraction_run_id=run.extraction_run_id,
            provider=run.provider,
            model=run.model,
            prompt_version=run.prompt_version,
            extractor_version=run.extractor_version,
            run_nonce=run.run_nonce,
            started_at=run.started_at.isoformat(),
            completed_at=run.completed_at.isoformat() if run.completed_at else None,
        )

    async def complete_extraction_run(
        self, workspace_id: str, extraction_run_id: str, completed_at: datetime
    ) -> None:
        match = scoped_match("ExtractionRun", "er", extraction_run_id="extraction_run_id")
        await self._executor.tenant_query(
            f"MATCH {match} SET er.completed_at = $completed_at",
            workspace_id=workspace_id,
            extraction_run_id=extraction_run_id,
            completed_at=completed_at.isoformat(),
        )

    async def get_extraction_run(self, workspace_id: str, extraction_run_id: str) -> ExtractionRun | None:
        match = scoped_match("ExtractionRun", "er", extraction_run_id="extraction_run_id")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_EXTRACTION_RUN_RETURN}",
            workspace_id=workspace_id,
            extraction_run_id=extraction_run_id,
        )
        return ExtractionRun(**rows[0]) if rows else None
