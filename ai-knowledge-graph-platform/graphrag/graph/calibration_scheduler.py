"""Threshold-triggered scheduling for GNN calibration runs."""

from __future__ import annotations

from uuid import uuid4


class GNNCalibrationScheduler:
    def __init__(self, neo4j_client, threshold: int = 100):
        self._neo4j = neo4j_client
        self._threshold = max(1, threshold)

    async def maybe_schedule(self, tenant: str = "default") -> dict:
        rows = await self._neo4j.run(
            """
            MATCH (d:Document)
            WHERE ($tenant = 'default' OR d.tenant = $tenant)
            WITH count(d) AS documents
            OPTIONAL MATCH (r:GNNCalibrationRun)
            WHERE ($tenant = 'default' OR r.tenant = $tenant)
            RETURN documents, coalesce(max(r.document_count), 0) AS last_count
            """,
            tenant=tenant,
        )
        row = rows[0] if rows else {}
        document_count = int(row.get("documents", 0) or 0)
        last_count = int(row.get("last_count", 0) or 0)
        due = document_count - last_count >= self._threshold
        if not due:
            return {"scheduled": False, "document_count": document_count, "last_count": last_count}
        job_id = str(uuid4())
        await self._neo4j.run(
            """
            CREATE (:GNNCalibrationRun {
                id: $id, tenant: $tenant, status: 'scheduled',
                document_count: $document_count, data_version: $data_version,
                model_version: $model_version, scheduled_at: datetime()
            })
            """,
            id=job_id, tenant=tenant, document_count=document_count,
            data_version=f"documents:{document_count}", model_version="pending",
        )
        return {"scheduled": True, "job_id": job_id, "document_count": document_count}

    async def record_completion(
        self, job_id: str, *, alpha: float, beta: float, score: float,
        model_version: str, data_version: str,
    ) -> None:
        await self._neo4j.run(
            """
            MATCH (r:GNNCalibrationRun {id: $job_id})
            SET r.status = 'completed', r.alpha = $alpha, r.beta = $beta,
                r.score = $score, r.model_version = $model_version,
                r.data_version = $data_version, r.completed_at = datetime()
            """,
            job_id=job_id, alpha=alpha, beta=beta, score=score,
            model_version=model_version, data_version=data_version,
        )
