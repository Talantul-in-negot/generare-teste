"""Agent that runs RAGAS evaluation and logs KPIs.

After each evaluation the (context_precision → faithfulness) pair is written
to the CalibrationService so the dashboard Calibration tab reflects real data
without a separate manual step.
"""

from __future__ import annotations

import time

import structlog

from graphrag.agents.base_agent import BaseGraphRAGAgent
from graphrag.business_matrix.kpi_tracker import KPITracker
from graphrag.core.config import get_settings
from graphrag.core.models import EvalJob, EvalResult, KPIEvent
from graphrag.evaluation.ragas_evaluator import RagasEvaluator
from graphrag.observability.agent_telemetry import record_evaluation_job
from graphrag.observability.correlation import correlation_context
from graphrag.observability.tracing import trace_span

log = structlog.get_logger(__name__)


class EvaluationAgent(BaseGraphRAGAgent):
    def __init__(self):
        self._evaluator = RagasEvaluator()
        self._tracker = KPITracker()
        super().__init__("evaluation_agent")

    def _model(self) -> str:
        return get_settings().groq_model  # Groq: provenance stamping for ADK scaffold

    def _instruction(self) -> str:
        return (
            "You are an evaluation agent. Given a completed query turn, "
            "run RAGAS metrics (faithfulness, answer_relevancy, context_precision, "
            "context_recall) and log all KPIs to the business matrix store."
        )

    async def run(self, job: EvalJob) -> EvalResult:
        started_at = time.monotonic()
        outcome = "failed"
        log.info("evaluation_agent.start", job_id=job.job_id, tenant=job.tenant)

        try:
            with correlation_context(job.correlation_id), trace_span(
                "evaluation.run", job_id=job.job_id, query_id=job.query_result.query_id,
                tenant=job.tenant,
            ):
                qr = job.query_result
                eval_result = await self._evaluator.evaluate_single(
                    query_id=qr.query_id,
                    question=qr.question,
                    answer=qr.answer,
                    contexts=qr.contexts,
                    ground_truth=job.ground_truth,
                )
                # Evaluators naturally key a score by query; preserve the
                # durable queue job ID as well so retries and traces have one
                # unambiguous evaluation identity.
                eval_result.job_id = job.job_id

                # A stable event ID makes a redelivered job visible and gives
                # the KPI backend a deterministic key for deduplication.
                kpi = KPIEvent(
                    event_id=job.job_id,
                    query_id=qr.query_id,
                    latency_ms=qr.latency_ms,
                    faithfulness=eval_result.faithfulness,
                    answer_relevancy=eval_result.answer_relevancy,
                    context_precision=eval_result.context_precision,
                    context_recall=eval_result.context_recall,
                    retrieval_mode=qr.retrieval_mode,
                    model_version=qr.model_version,
                )
                await self._tracker.record(kpi)

        # ── Wire calibration sample ────────────────────────────────────────────
        # predicted_confidence = context_precision (how confident the retrieval was)
        # actual_outcome       = faithfulness      (how correct the answer was)
        # This populates the dashboard Calibration tab automatically after each run.
                try:
                    from graphrag.graph.confidence_calibration import CalibrationService
                    from graphrag.graph.neo4j_client import get_neo4j
                    cal_svc = CalibrationService(get_neo4j())
                    await cal_svc.add_sample(
                        predicted_confidence=eval_result.context_precision,
                        actual_outcome=eval_result.faithfulness,
                        relation=qr.retrieval_mode,
                        source_doc_id=qr.query_id,
                        prompt_version=qr.model_version,
                        tenant=job.tenant,
                        verified_by="ragas",
                    )
                    log.debug("evaluation_agent.calibration_sample_added", tenant=job.tenant)
                except Exception as exc:
                    # Calibration is downstream learning data, never a reason
                    # to drop an otherwise measured evaluation result.
                    log.warning("evaluation_agent.calibration_sample_failed", error=str(exc))

            outcome = "completed"
            log.info(
                "evaluation_agent.done",
                job_id=job.job_id,
                faithfulness=round(eval_result.faithfulness, 3),
                answer_relevancy=round(eval_result.answer_relevancy, 3),
            )
            return eval_result
        finally:
            record_evaluation_job(
                outcome=outcome, tenant=job.tenant, job_id=job.job_id, started_at=started_at,
            )
