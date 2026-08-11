# SLO evidence, capacity model and failure drills

This repository has a repeatable k6 baseline generator, not a production SLO
claim. Before enabling a customer workspace, record a dated run against a
production-shaped anonymised graph and publish p50/p95/p99, error rate,
throughput, queue depth/age, CPU/memory and LLM cost.

## Initial pilot targets

| Service | Target | Evidence source |
|---|---|---|
| Scoped context/answer | p95 <= 3 s; p99 <= 8 s | k6 retrieval + LLM layers |
| Ingestion freshness | p95 <= 5 min after a webhook | queue timestamp to completed job trace |
| API availability | >= 99.9% monthly; <0.5% 5xx | RED metrics and synthetic probe |
| Queue | alert before oldest job exceeds 15 min; DLQ normally zero | Prometheus gauges and Alertmanager |

These are pilot targets, not proof. A release ticket must contain the run
artifact, data volume, worker concurrency, Redis/Neo4j sizing and failures
observed.

## Capacity model

For one tenant/source, start with:

worker slots required = peak jobs/minute × p95 processing seconds / 60 × 1.3.

Use the next whole number, cap each process at
INGESTION_WORKER_CONCURRENCY (1–32), then add replicas only after a
production-shaped load test. Keep per-tenant queue age below the pilot target;
the current global Redis queue is not strict tenant-fair scheduling, so do not
increase tenant count purely by raising worker concurrency.

## Failure drills

Run the scheduled Reliability drills workflow and locally run:

    pytest tests/unit/ingestion/test_queue.py tests/unit/core/test_alerting.py -q

Then perform these controlled environment drills and attach the evidence:

1. Stop a worker while it owns an ingestion job: the visibility reaper must
   requeue it or deliver it to the DLQ after the bounded retry count.
2. Stop Redis: API/worker must surface readiness failure; no accepted job may
   be silently lost.
3. Stop Neo4j during ingestion: retryable jobs must retry and later complete
   or reach DLQ.
4. Force LLM timeout/rate limit through the mock server: answer/extraction
   must fail loud or use the explicitly enabled fallback, with telemetry.
5. Restore the disposable backup marker through the weekly DR drill.

The named owner records the result, RPO/RTO observed, corrective action and
date in the release evidence. Managed-service outage/failover tests require
the production provider credentials and cannot be proved from this repository.
