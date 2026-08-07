# ADR-0001 — Durable ingestion queue (design, not yet implemented)

**Status:** Implemented (feature-flagged)
**Date:** 2026-08-05

## Context

`api/routes/ingestions.py` runs every ingestion pipeline call
(`CrmIngestionPipeline`, `TranscriptIngestionPipeline`,
`ContentIngestionPipeline`) synchronously, in-process, inside the HTTP
request handler. §11 of `docs/plan.md` explicitly permits this for the MVP:

> "For the MVP, an in-process bounded worker may be used, but its interface
> must support later replacement with a durable queue."

This is already half-true today: `api/state.py`'s job **status** is durable
(`RedisIngestionStore`, wired since the cloud-readiness pass — see
`get_ingestion_store()`). What's *not* durable is the **execution** — if the
API process crashes mid-ingestion, the in-flight pipeline call is lost; only
whatever state was written before the crash survives. There is no retry, no
backpressure, and one slow/large ingestion request blocks that worker
process for its full duration.

In production (per the "how would this actually run" discussion, not
written elsewhere in `docs/`), ingestion is triggered by external
integrations — a Salesforce sync job, a Gong webhook, a Showpad webhook —
calling `POST /api/v1/ingestions/*` server-to-server, not by a human. That
model needs the request to return fast and the actual work to survive a
process restart, which is exactly what a real queue provides and synchronous
in-process execution does not.

## Decision (proposed)

Introduce a real broker-backed queue between the API and the pipelines,
reusing the Redis instance already in `docker-compose.yml` — no new
infrastructure dependency.

### Shape

```
POST /api/v1/ingestions/{kind}
  → validate request, write IngestionJob{state: ACCEPTED} to RedisIngestionStore
  → enqueue {ingestion_id, workspace_id, kind, payload} onto a Redis-backed queue
  → return {ingestion_id} immediately (already true today — no route contract change)

worker.py (new, separate process)
  → pulls one job at a time from the queue
  → runs the same CrmIngestionPipeline / TranscriptIngestionPipeline /
    ContentIngestionPipeline calls api/routes/ingestions.py calls today —
    the pipeline layer does not change, only what calls it
  → updates IngestionJob state through the existing
    ACCEPTED → NORMALIZING → EXTRACTING → RESOLVING → PERSISTING →
    COMPLETED / COMPLETED_WITH_REVIEW / FAILED_RETRYABLE / FAILED_PERMANENT
    machine (src/domain/enums.py::IngestionState — unchanged)

GET /api/v1/ingestions/{id}
  → unchanged: reads RedisIngestionStore, same as today
```

### Library choice: RQ (Redis Queue), not Celery

- Already depending on Redis; RQ needs nothing else.
- Celery's broker/backend/beat machinery is more than this needs — one
  queue, no scheduled tasks, no complex routing.
- `rq-scheduler` or a simple retry-with-backoff wrapper covers
  `FAILED_RETRYABLE` without Celery's full feature surface.

### Idempotency and retry

Nothing new to invent here — §6/§7 of `docs/plan.md` already define
idempotent ingestion at the pipeline level (`reconciliation.py`'s
CREATED/NO_OP/SUPERSEDED/TOMBSTONED). A retried job re-running the same
pipeline call against the same source data is a no-op on the unchanged
parts, by design. The queue only needs:

- an idempotency key per job (`ingestion_id`, already generated) so a
  duplicate enqueue (e.g. a retried HTTP request from the caller) doesn't
  double-enqueue — check `RedisIngestionStore` for an existing job first;
- bounded retry count on `FAILED_RETRYABLE` (transient Neo4j/LLM
  errors) before giving up to `FAILED_PERMANENT` — §11 already names this
  requirement, just unimplemented;
- no retry on validation errors (`FAILED_PERMANENT` immediately) — same
  rule already stated in §11.

### What does NOT change

- `src/ingestion/`, `src/extraction/`, `src/resolution/` — the pipeline
  layer is queue-agnostic today (`api/routes/ingestions.py` already calls it
  as a plain async function); the worker calls the exact same functions.
- The `IngestionState` state machine — already the right shape for a real
  async worker, per its own docstring in `src/domain/enums.py`.
- The HTTP contract — `POST` still returns `{ingestion_id}` immediately,
  `GET .../{id}` still polls the same store.

### What DOES change

- `api/routes/ingestions.py`'s route handlers: replace the direct
  `await pipeline.ingest_*(...)` call with an enqueue call.
- New `worker.py` (or `src/worker/`) entry point, run as a separate process
  (`rq worker` or a thin wrapper).
- `docker-compose.yml`: new `worker` service, same image as `api`, running
  `worker.py` instead of `uvicorn`.
- `requirements`/`pyproject`: add `rq`.

## Consequences

- **Positive:** ingestion survives an API process restart mid-job; slow
  ingestions no longer block an API worker process; retry/backpressure
  become real instead of TODO comments in §11.
- **Negative:** one more process to run and monitor locally
  (`docker compose up` grows a `worker` service); local dev without Redis
  configured (`InMemoryIngestionStore` fallback) has no queue at all — that
  fallback would need to keep working synchronously as it does today, or be
  retired in favor of requiring Redis even locally.
- **Deferred deliberately:** exactly-once delivery semantics, dead-letter
  queue tooling, multi-worker horizontal scaling tuning — none of these are
  needed at this vertical slice's scale and would be premature to design
  before there's a second real consumer of the queue.

## Not done in this ADR

The implemented seam is `src/ingestion/queue.py` plus
`src/ingestion/worker.py`: Redis list delivery, idempotent enqueue markers,
bounded retry, and a dead-letter list. `INGESTION_QUEUE_ENABLED=false` keeps
local development synchronous; Compose and Fly enable it and run a separate
worker. Exactly-once delivery, per-workspace fairness and horizontal worker
tuning remain operational follow-ups, while graph writes remain safe under
at-least-once delivery because the existing reconciliation/MERGE paths are
idempotent.
