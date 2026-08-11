# ADR-0001 — Durable ingestion queue

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

## Decision (implemented, feature-flagged)

Introduce a Redis-backed queue between the API and the pipelines,
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

### Library choice: Redis primitives by default, not RQ or Celery

The default implementation uses `redis.asyncio` directly: `RPUSH`/`BLMOVE` for
delivery, per-worker processing lists, timestamps for visibility and a DLQ
list. This keeps the worker async with the existing FastAPI/Neo4j stack and
avoids adding a second job abstraction. Celery/RQ scheduling is intentionally
not part of this service; external schedulers own periodic digest delivery.

An optional Kafka transport is also implemented behind
`INGESTION_TRANSPORT=kafka` for deployments that already operate Kafka. It
reuses the same pipeline, idempotency marker and job-state contract; Redis
remains required for the job store and deduplication. The default remains
`INGESTION_TRANSPORT=redis`.

RabbitMQ was considered but is not implemented as a transport in this
service. It would provide a dedicated task broker with explicit acknowledgements,
exchange-based routing, retry queues and native dead-letter exchanges. Those
capabilities are useful when the platform already standardises on RabbitMQ or
needs complex routing and priority queues, but adding it here would introduce
another stateful infrastructure dependency without changing the ingestion
pipeline contract. The current Redis transport already provides the required
at-least-once delivery, visibility-timeout recovery, bounded retries and
dead-letter handling for this system's scale. If Showpad standardises on
RabbitMQ, it can be added behind the same transport seam later; it should not
be enabled by setting `INGESTION_TRANSPORT=rabbitmq` today.

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

### What changed

- `api/routes/ingestions.py` now enqueues when `INGESTION_QUEUE_ENABLED=true`
  and retains synchronous execution when the flag is false.
- `src/ingestion/worker.py` is a separate process entry point using Redis
  primitives already in the stack.
- Delivery is at-least-once with idempotent reconciliation, bounded retries,
  a dead-letter list and visibility-timeout recovery.
- `INGESTION_WORKER_CONCURRENCY` creates independent claim slots per worker
  process (clamped to 1–32).

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
bounded retry, dead-letter handling, visibility-timeout recovery and bounded
worker concurrency. `INGESTION_QUEUE_ENABLED=false` keeps local development
synchronous. Exactly-once delivery and strict per-workspace fairness remain
operational follow-ups; graph writes remain safe under at-least-once delivery
because reconciliation/MERGE paths are idempotent.

## Addendum, 2026-08-07 — visibility timeout (docs/evaluation.md)

Closes one specific gap this ADR left open: **a worker crashing mid-job used
to lose that job entirely.** `dequeue()` used `BLPOP`, which deletes a job
from the queue the instant it's claimed — if the worker then crashed before
finishing (OOM, process kill, infra restart), nothing put the job back;
`RedisIngestionStore` would show it permanently stuck at whatever state it
last reached, with no queue entry left to retry it. Same architectural
decision as the rest of this ADR (reuse Redis primitives already in the
stack, no new broker), extended:

- `dequeue()` now uses `BLMOVE ... LEFT LEFT` instead of `BLPOP` — the job
  moves atomically from the shared queue into the claiming worker's own
  processing list (`scg:ingestion:processing:{worker_id}`) rather than
  being deleted outright, plus a claim timestamp
  (`scg:ingestion:claimed_at:{worker_id}`).
- `complete()` (new) clears the claim on success;
  `retry_or_dead_letter()` clears it before its existing retry/dead-letter
  decision.
- `reap_stale_processing_lists()` (new), called every iteration of every
  worker's own poll loop: any processing list whose claim has sat past
  `INGESTION_VISIBILITY_TIMEOUT_SECONDS` (default 300s) is assumed to
  belong to a crashed worker and goes back through the same bounded
  retry/dead-letter path an ordinary failure would — a job that reliably
  crashes its worker still reaches the DLQ eventually, not an infinite
  reap loop.
- `worker_id` is a per-process UUID generated once in `run_worker()` —
  correct if multiple worker replicas are ever run (each gets its own
  processing list, never sharing or corrupting another's claim), though
  this repo's own `docker-compose.yml`/`fly.toml` still run exactly one
  `worker` process today.

Still deferred: exactly-once delivery, strict tenant-fair scheduling and
measured horizontal capacity. The code supports multiple replicas and local
concurrency, but production fairness/SLO claims require a target workload.
