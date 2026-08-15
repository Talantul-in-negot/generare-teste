# Operations — running the proactive digest

## Why there is no in-process scheduler

`POST /api/v1/digest/deliver` (Increment 17) computes the five signal rules in
`src/signals/rules.py` across a workspace's open pipeline and posts a Slack
digest. It is triggered by an HTTP call, not by a timer this process runs
itself. Ingestion is a separate concern: when `INGESTION_QUEUE_ENABLED=true`,
the API enqueues durable Redis jobs and `python -m src.ingestion.worker` owns
execution with retries, visibility timeout, dead-letter handling and bounded
concurrency.

The external-trigger choice for digest delivery is deliberate, not an
oversight:

- A periodic digest has no measured latency/backlog requirement that a
  synchronous, cron-triggered HTTP call cannot satisfy. Keeping scheduling
  outside the API avoids duplicate runs when the web process scales.
- `fly.toml` runs a single `http_service` with no `[processes]` map. Adding an
  in-process scheduler (APScheduler, a bare `while True: sleep` loop) would be
  new infrastructure with its own failure modes (missed runs on restart,
  duplicate runs across multiple machines if the app ever scales out) for a
  job an external, already-durable scheduler already does correctly.

If a measured need for sub-cron-interval freshness appears, revisit this —
do not add an in-process scheduler preemptively.

## Queue operations

Enable the durable path with `REDIS_URL` and
`INGESTION_QUEUE_ENABLED=true`, then run a worker separately:

```bash
python -m src.ingestion.worker
```

The worker uses one Redis processing list per concurrency slot. A crash is
recovered after `INGESTION_VISIBILITY_TIMEOUT_SECONDS`; retryable failures are
bounded by `INGESTION_QUEUE_MAX_ATTEMPTS` and then sent to the DLQ. Monitor
`/ready`, `/metrics`, queue depth, oldest-job age and DLQ depth. A healthy API
without a heartbeat from the worker is not considered ready when the durable
queue is enabled.

For a repeatable local performance baseline, run `make loadtest`. For a
destructive local backup/restore round-trip, run `make backup-verify` only
against disposable infrastructure.

## Local observability stack

The application exposes Prometheus metrics at `GET /metrics`. In production,
`METRICS_API_KEY` is mandatory and the scrape must use
`Authorization: Bearer <METRICS_API_KEY>`; local Compose can leave it empty.
Set a unique `GRAFANA_ADMIN_PASSWORD` in `.env` before exposing Grafana outside
a local machine, then start the opt-in local stack:

```bash
docker compose --profile observability up -d
```

- Prometheus: `http://localhost:9090` scrapes `api:8000/metrics` every 15
  seconds and loads `alerting/prometheus_rules.yml`.
- Alertmanager: `http://localhost:9093` displays alerts locally. Its committed
  receiver is intentionally log-only; production notification credentials
  belong in deployment-managed secrets.
- Grafana: `http://localhost:3000` uses the configured admin credentials and
  provisions the **Sales Context Graph Overview** dashboard automatically.

Prometheus metrics and Grafana are distinct from OpenTelemetry tracing. A
production deployment must also configure an OTLP collector/exporter with the
standard `OTEL_*` variables; no hosted telemetry credentials are committed.

## Backup and recovery drill

`.github/workflows/dr-drill.yml` runs the disposable local Neo4j
backup/restore round trip weekly and on demand. It proves that the repository's
backup scripts can recover a marker after simulated local data loss. It does
not back up AuraDB or Fly Redis, and it is not evidence of multi-region failover;
those production recovery controls must be supplied and tested with the managed
providers under the agreed RPO/RTO.

## Authenticated MCP operations

Set `MCP_ENABLED=true` to expose `POST /mcp`. Every request is independent and
must send `Authorization: Bearer <workspace API key>` and `X-Workspace-Id` (or
a validated JWT when SSO is enabled). The transport accepts only `tools/list`
and fixed `tools/call` payloads; it does not accept raw Cypher or arbitrary
code. The local CRM adapter is synthetic and writes only to the configured
local JSON state file.

## Triggering the digest

Any scheduler that can make an authenticated HTTPS POST works. Two examples:

### GitHub Actions (scheduled workflow)

```yaml
# .github/workflows/digest.yml
name: sales-digest
on:
  schedule:
    - cron: "0 13 * * 1-5"  # 13:00 UTC, weekdays
  workflow_dispatch: {}
jobs:
  deliver:
    runs-on: ubuntu-latest
    steps:
      - name: POST /api/v1/digest/deliver
        run: |
          curl -sf -X POST "https://sales-context-graph.fly.dev/api/v1/digest/deliver" \
            -H "X-Workspace-Id: ${{ secrets.SCG_WORKSPACE_ID }}" \
            -H "X-Api-Key: ${{ secrets.SCG_API_KEY }}"
```

### Fly.io scheduled machine

```bash
fly machine run curlimages/curl --schedule daily \
  -X POST "https://sales-context-graph.fly.dev/api/v1/digest/deliver" \
  -H "X-Workspace-Id: ws-demo" -H "X-Api-Key: $SCG_API_KEY" \
  -a sales-context-graph
```

Both examples call `/deliver`, which requires `SLACK_WEBHOOK_URL` to be set
(`fly secrets set SLACK_WEBHOOK_URL=...` — see `docs/deployment.md` for the
secrets-setting pattern). `GET /api/v1/digest` returns the same signals as
plain JSON with no delivery step and no webhook requirement, for a UI (see
`/viz`'s Alerts tab, Increment 20) or manual polling.

## Tuning thresholds

`DIGEST_STALE_SHARE_DAYS` (default 7) and `DIGEST_STALLED_DEAL_DAYS` (default
21) — see `.env.example`. Both are read once per request via `get_settings()`,
so changing them via `fly secrets set` takes effect on the next call with no
redeploy.
