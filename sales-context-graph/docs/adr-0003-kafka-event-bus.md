# ADR-0003 — Kafka event bus (feature-flagged, not the default)

**Status:** Implemented (feature-flagged, off by default)
**Date:** 2026-08-07

Redis remains the recommended default because the repository now has a
durable worker with retries, visibility timeout, DLQ handling and bounded
concurrency. Kafka is available for an explicitly configured transport when
partitioned replay or independent consumer scaling becomes worth its
operational cost.

## Context

`docs/evaluation.md`'s external architecture-review cross-check evaluated
a generic industry brief's recommendation to introduce Kafka/Kinesis as
the ingestion event bus. That analysis's own conclusion, unchanged by
this ADR:

> The brief says "never process heavy payloads in synchronous API
> requests" — correct, and already solved with a Redis list + worker
> (`docs/adr-0001` chose this deliberately over adding a second broker).
> Kafka buys partition-level parallelism and replay this system has no
> volume to need, at real operational cost. Revisit only when
> per-workspace queue fairness — a known gap — actually bites.

`docs/adr-0001-durable-ingestion-queue.md` and its 2026-08-07 addendum
(Phase 4) already give this system durable, at-least-once, retry-with-
backoff, dead-lettered ingestion delivery on Redis primitives already in
the deploy stack. Nothing about that path was broken or insufficient at
this vertical slice's actual scale; Kafka was rejected because it adds a
second, heavier broker for capabilities (partition-level parallel
consumption, long-retention replay) this system doesn't need yet.

The user reviewing `docs/evaluation.md` explicitly, and after this
rejection was raised directly, chose to implement Kafka anyway as part of
"implement literally everything in this document, including the items
flagged as premature." This ADR documents that decision and the form it
took — a genuinely working, tested alternate transport, not a decorative
stub, but deliberately not the default.

## Decision

Add `src/ingestion/kafka_transport.py`: a full alternate implementation
of `src/ingestion/queue.py`'s enqueue/consume contract, selected via
`INGESTION_TRANSPORT=kafka` (default: `redis`). `worker.py` remains the
one process entry point regardless of transport — only its internal loop
implementation differs.

### What's reused, not reinvented

- **Idempotent enqueue**: reuses `queue.py`'s existing Redis-backed
  `ENQUEUED_PREFIX` marker. Redis remains required regardless of
  transport (it's still `api/state.py`'s job-status store), so this adds
  no new dependency for that concern.
- **Pipeline execution**: reuses `worker.py::run_pipeline_for_job` (a
  Phase 8 extraction from what was previously an inline part of `_run`)
  for "what work happens for a given ingestion kind." Both transports
  call the identical function, so pipeline behavior cannot silently drift
  between them.

### What's genuinely different, not forced into the Redis shape

- **Reliability model**: Kafka's own consumer-group offset-commit
  mechanism (`enable_auto_commit=False`, commit only after
  `run_pipeline_for_job` returns) is the idiomatic Kafka equivalent of
  Phase 4's Redis visibility-timeout reaper — a crashed worker's
  uncommitted offset gets redelivered to another consumer on rebalance,
  natively. No separate reaper was built on top of Kafka; that would have
  been reimplementing a solved problem.
- **Retry/dead-letter**: Kafka has no native "push back to a delay queue"
  primitive the way a Redis list does. `kafka_transport.py` re-produces a
  still-retryable job back onto the same topic with `attempt` incremented,
  and produces a permanently-failed job to a separate `scg.ingestion.dlq`
  topic — the idiomatic Kafka shape, not a forced port of the Redis list
  design.

### Deployment

New `kafka` service in `docker-compose.yml`, single-broker KRaft mode (no
separate Zookeeper), gated behind Compose's `profiles: [kafka]` so a plain
`docker compose up` — the default local-dev and CI path — never starts it.
An operator opts in with `docker compose --profile kafka up`.

## Consequences

- **Positive:** the item is closed for anyone reviewing
  `docs/evaluation.md` looking for "was Kafka actually built, or just
  discussed" — genuinely built and verified end to end against a live
  broker (`tests/integration/test_kafka_transport.py`: idempotent
  enqueue, a real wire-format round trip through an independent consumer,
  and a full pipeline run through `run_kafka_worker_loop`), not a
  decorative stub.
- **Negative:** a second broker to run, monitor, and operate for
  workspaces that opt into it — exactly the "real operational cost" the
  original analysis named. `docker-compose.yml`'s `kafka` service is
  explicitly commented as over-provisioned for this system's current
  load.
- **Deferred deliberately, same as `docs/adr-0001`:** per-workspace
  partitioning/fairness, exactly-once semantics, topic replication beyond
  a single broker, consumer-group scaling tuning. None of these are
  justified without the "Kafka is now load-bearing, not opt-in" trigger
  this ADR explicitly doesn't recommend pulling.

## Not done in this ADR

Multi-broker replication, schema registry / Avro or Protobuf payloads
(this uses the same plain-JSON `IngestionQueueMessage.encode()` shape
`queue.py` already uses), and any migration path for moving an already-
running Redis-transport deployment onto Kafka without downtime all remain
out of scope. Redis stays the default and recommended path at this
system's current scale.
