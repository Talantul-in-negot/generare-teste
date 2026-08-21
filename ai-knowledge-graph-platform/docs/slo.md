# Service Level Objectives and Error Budgets

Status: **implemented, with provisional targets.** Every SLI below is computed
from a metric that exists and is emitted today. The *targets* are a different
matter — see
[Which targets are grounded](#which-targets-are-grounded) before quoting any
number in a contract. This document is deliberately explicit about that
distinction, because an SLO presented as measured when it was chosen by
intuition is worse than having none: it manufactures confidence that nothing
supports.

## Why these four

An SLI is only worth defining if a human would act differently when it breaches.
The platform emits far more than four measurable quantities; these are the ones
where the action is unambiguous.

| SLI | What breaks for the user | Owner action on breach |
|---|---|---|
| **Query availability** | Questions return 5xx | Restore the failing dependency named by `/health/ready` |
| **Query latency** | Answers arrive too late to be useful | Shed load, or route to the fast model tier |
| **Ingestion completeness** | Documents silently never enter the graph | Drain the DLQ; fix the handler before replaying |
| **Answer groundedness** | Answers are confidently wrong | Freeze retrieval config; run the golden eval |

Groundedness is included because this is a RAG platform: an available, fast,
confidently-wrong answer is a worse outcome than an error, and it is the only
one of the four that no infrastructure metric can detect.

## SLI definitions

Each is a ratio of good events to valid events, computed from metrics defined in
`graphrag/observability/`. The PromQL is the definition — if it does not match
what a dashboard shows, the dashboard is wrong.

### 1. Query availability

```promql
sum(rate(http_requests_total{handler="/query", status!~"4..|5.."}[30d]))
/
sum(rate(http_requests_total{handler="/query", status!~"4.."}[30d]))
```

**Valid events** exclude 4xx. A 429 from a rate limit or quota is the system
working correctly, and a 401 is a client credential problem; counting either as
unavailability would mean a tenant hitting its own quota degrades the platform's
published reliability.

The numerator is the same valid-event denominator with 5xx responses excluded;
the query above is abbreviated for readability. The dashboard and burn-rate
rules use the explicit `status!~"4..|5.."` numerator.

### 2. Query latency

```promql
histogram_quantile(
  0.95,
  sum(rate(http_request_duration_seconds_bucket{handler="/query"}[30d])) by (le)
)
```

Measured at the API boundary, not at the retriever: the user waits for the whole
request, including queueing behind a saturated Neo4j pool.

### 3. Ingestion completeness

```promql
1 - (
  sum(rate(graphrag_broker_dlq_messages_total{queue=~".*ingest.*"}[30d]))
  /
  sum(rate(graphrag_broker_messages_consumed_total{queue=~".*ingest.*"}[30d]))
)
```

A dead-lettered document is one that will never appear in the graph. This is the
SLI for the platform's quietest failure: the API returned 202, the user believes
the document is indexed, and it is not.

### 4. Answer groundedness

RAGAS faithfulness over the golden set, from `GraphHealthSnapshot` — not a
Prometheus rate, because it is a periodic evaluation rather than a per-request
measurement. `graphrag/monitoring/alerts.py` already thresholds it at 0.8.

## Targets and error budgets

A 30-day window. Error budget is `(1 - target) x valid events`; the useful form
is the time-equivalent, since that is what an operator can reason about.

| SLI | Target | Error budget (30d) | Grounding |
|---|---:|---|---|
| Query availability | 99.5% | ~3h 39m of full unavailability | **Provisional** — no production error-rate history |
| Query latency p95 | ≤ 30s | 5% of queries may exceed | **Weakly grounded** — see below |
| Ingestion completeness | 99.9% | 1 document in 1,000 may dead-letter | **Provisional** |
| Answer groundedness | ≥ 0.80 faithfulness | n/a (quality gate, not a budget) | **Grounded** — matches the existing alert threshold |

### Which targets are grounded

**Latency is the only one with recorded data, and the data does not support a
confident number.** `docs/performance-metrics-inventory.md` records:

- n=44, p95 **26.4s** — the measurement that motivated raising the alert
  threshold from 3s to 30s on 2026-07-30;
- n=10, p50 20.7s, p95 **33.9s** — and the document itself notes that with ten
  samples "p95" and "max" are the same point, so it is an order-of-magnitude
  correction rather than an SLA input.

The 30s target is therefore set to match the *existing alert threshold* so the
two cannot disagree, not because 30s was independently chosen as the right user
experience. It is a placeholder with a paper trail.

**Availability and completeness have no history at all.** The numbers above are
conventional defaults, recorded so the arithmetic and the review process exist.
They must be replaced after the load and soak runs on the roadmap.

Do not publish these externally or write them into a customer agreement until
the [Exit criteria](#exit-criteria-for-removing-provisional) below are met.

## Error-budget policy

The point of a budget is that it converts an argument about risk into
arithmetic. It only works if the consequences are agreed before it is spent.

| Budget consumed | Consequence |
|---|---|
| < 50% | Normal delivery. Ship features. |
| 50–90% | Reliability work is prioritised over features in the next cycle. Any change touching retrieval, the broker, or the graph needs a named reviewer. |
| > 90% | Feature freeze on the affected surface. Only fixes that reduce burn, plus the drills that prove they did. |
| Exhausted | Freeze holds until a full window passes within target. |

Burn is reviewed weekly against the dashboard in
`monitoring/grafana/graphrag-overview.json`, not computed by hand.

### Fast-burn alerting

A monthly budget can be spent in an afternoon. Burn-rate alerting catches that
before the window ends — a 14.4x burn rate exhausts a 30-day budget in ~2 days,
which is why it is the standard fast-burn multiplier:

```promql
(
  sum(rate(http_requests_total{handler="/query", status=~"5.."}[1h]))
  /
  clamp_min(sum(rate(http_requests_total{handler="/query"}[1h])), 0.001)
) > (14.4 * 0.005)
```

The fast and slow burn-rate rules are implemented in
`monitoring/prometheus/alerts.yml`. They are labelled as SLO alerts and use the
same target constants as this document. Because the targets remain provisional,
the alerts are a ticket/page signal for review, not evidence that a production
SLA has been achieved.

## Operational wiring

- Prometheus scrape and rule wiring: `monitoring/prometheus/prometheus.yml`.
- Importable/provisioned dashboard: `monitoring/grafana/graphrag-overview.json`.
- Grafana datasource and dashboard provisioning: `monitoring/grafana/provisioning/`.
- Local Docker Compose endpoints: Prometheus at `http://localhost:9090` and
  Grafana at `http://localhost:3000` after `docker compose up -d`.
- Set `PROMETHEUS_METRICS_TOKEN` in `.env` before starting Compose. Prometheus
  uses that token for the protected API `/metrics` endpoint; `/metrics` is not
  made public for dashboard convenience.
- Groundedness is exported as `graphrag_evaluation_faithfulness{source="ragas"}`
  from the evaluation worker. Reference-only judge scores are not used for the
  groundedness SLO.

## What is not an SLO here

- **Cost.** Bounded by per-tenant quotas (`graphrag/core/tenant_quota.py`), which
  are a control rather than an objective. Exceeding a quota is a policy outcome,
  not a reliability failure.
- **Freshness.** No target for graph-staleness after ingestion, because
  community rebuild and PageRank recompute are triggered by drift thresholds
  rather than a clock. Defining one requires deciding what "current" means for a
  corpus that changes continuously.
- **MCP transport availability.** Same shape as query availability, omitted only
  because there is no traffic history to derive a target from.

## Exit criteria for removing "provisional"

1. A representative load run producing at least a **1,000-sample** latency
   distribution per retrieval mode — enough that p95 is not the maximum.
2. Seven consecutive days of production or soak traffic with error-rate and
   DLQ-rate history.
3. A restore drill establishing RTO/RPO, so availability targets account for
   recovery rather than assuming it is instant.
4. Targets re-derived from (1)–(3) and this table rewritten, with the
   burn-rate alert added to `alerts.yml` at that point.

All four are open roadmap items and none can be closed from a development
machine — they need a deployment carrying real traffic.

## References

- `monitoring/prometheus/alerts.yml` — the alerts that fire before a budget is
  meaningfully spent
- `monitoring/prometheus/prometheus.yml` — scrape and rule-file wiring
- `monitoring/grafana/graphrag-overview.json` — SLO and error-budget dashboard
- `graphrag/observability/operational_metrics.py` — SLI metric definitions
- `docs/performance-metrics-inventory.md` — recorded latency measurements and
  their sample-size caveats
- `docs/roadmap.md` — the load, soak, and restore work that closes the gaps above
