# Operations — running the proactive digest

## Why there is no in-process scheduler

`POST /api/v1/digest/deliver` (Increment 17) computes the five signal rules in
`src/signals/rules.py` across a workspace's open pipeline and posts a Slack
digest. It is triggered by an HTTP call, not by a timer this process runs
itself. This is a deliberate choice, not an oversight:

- `docker-compose.yml`'s `api` service comment already states the repo's
  standing position: *"No RabbitMQ/worker/queue services yet — ingestion
  starts as a synchronous API call... add a queue when transcript backlog
  exceeds the freshness SLA, not before there's a measured reason."* The same
  reasoning applies here — a periodic digest has no measured latency/backlog
  requirement that a synchronous, cron-triggered HTTP call can't satisfy.
- `fly.toml` runs a single `http_service` with no `[processes]` map. Adding an
  in-process scheduler (APScheduler, a bare `while True: sleep` loop) would be
  new infrastructure with its own failure modes (missed runs on restart,
  duplicate runs across multiple machines if the app ever scales out) for a
  job an external, already-durable scheduler already does correctly.

If a measured need for sub-cron-interval freshness appears, revisit this —
but do not add a second execution model preemptively.

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
