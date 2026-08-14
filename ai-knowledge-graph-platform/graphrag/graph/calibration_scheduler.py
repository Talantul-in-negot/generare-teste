"""Threshold-triggered scheduling for GNN calibration runs."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

from graphrag.graph.corpus_revision import CorpusMutation


class GNNCalibrationScheduler:
    def __init__(self, neo4j_client, threshold: int = 100, runner=None):
        self._neo4j = neo4j_client
        self._threshold = max(1, threshold)
        self._runner = runner

    async def maybe_schedule(self, tenant: str = "default", *, execute: bool = False) -> dict:
        rows = await self._neo4j.run(
            """
            MATCH (d:Document)
            WHERE (d.tenant = $tenant)
            WITH count(d) AS documents
            OPTIONAL MATCH (r:GNNCalibrationRun)
            WHERE (r.tenant = $tenant)
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
        if execute:
            asyncio.create_task(self.run_job(job_id, tenant))
        return {"scheduled": True, "job_id": job_id, "document_count": document_count,
                "execution_started": execute}

    async def run_job(self, job_id: str, tenant: str) -> None:
        """Execute the calibration script and persist success/failure state."""
        await self._neo4j.run(
            "MATCH (r:GNNCalibrationRun {id: $job_id, tenant: $tenant}) "
            "SET r.status = 'running', r.started_at = datetime()",
            job_id=job_id, tenant=tenant,
        )
        try:
            if self._runner is not None:
                result = await self._runner(job_id=job_id, tenant=tenant)
            else:
                root = Path(__file__).parents[2]
                eval_path = os.getenv("GNN_CALIBRATION_EVAL_SET", str(root / "eval_data" / "calibration_set.json"))
                process = await asyncio.create_subprocess_exec(
                    sys.executable, str(root / "scripts" / "calibrate_gnn.py"),
                    "--eval-set", eval_path,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()
                if process.returncode != 0:
                    raise RuntimeError(stderr.decode(errors="replace")[-1000:])
                result = {"model_version": "gnn-calibrated", "score": 0.0,
                          "data_version": eval_path, "output": stdout.decode(errors="replace")[-1000:]}
            await self.record_completion(
                job_id, tenant=tenant,
                alpha=float(result.get("alpha", 0.0)), beta=float(result.get("beta", 0.0)),
                score=float(result.get("score", 0.0)), model_version=result.get("model_version", "gnn-calibrated"),
                data_version=result.get("data_version", "calibration"),
            )
        except Exception as exc:
            await self._neo4j.run(
                "MATCH (r:GNNCalibrationRun {id: $job_id, tenant: $tenant}) "
                "SET r.status = 'failed', r.error = $error, r.completed_at = datetime()",
                job_id=job_id, tenant=tenant, error=str(exc)[:2000],
            )

    async def record_completion(
        self, job_id: str, *, tenant: str, alpha: float, beta: float, score: float,
        model_version: str, data_version: str,
    ) -> None:
        async with CorpusMutation(self._neo4j, tenant, "gnn_calibration"):
            await self._neo4j.run(
                """
                MATCH (r:GNNCalibrationRun {id: $job_id, tenant: $tenant})
                SET r.status = 'completed', r.alpha = $alpha, r.beta = $beta,
                    r.score = $score, r.model_version = $model_version,
                    r.data_version = $data_version, r.completed_at = datetime()
                """,
                job_id=job_id, tenant=tenant, alpha=alpha, beta=beta, score=score,
                model_version=model_version, data_version=data_version,
            )
