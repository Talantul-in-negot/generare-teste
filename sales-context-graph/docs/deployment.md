# Deployment (Fly.io)

The Fly app runs separate `app` and `worker` process groups. The API is
stateless; ingestion execution is Redis-backed and restart-safe when
`INGESTION_QUEUE_ENABLED=true`. Neo4j runs on managed **AuraDB Free** (not a self-hosted Neo4j on a
Fly volume — Neo4j is a stateful JVM process needing backup/upgrade
management this MVP doesn't want to operate itself). The ingestion job store
runs on Fly-managed **Redis** (`fly redis create`, Upstash-backed).

Authentication is API-key-per-workspace (`X-Api-Key` header, checked against
`WORKSPACE_API_KEYS` — see `api/dependencies.py::verify_api_key`).
`/health` and `/ready` stay unauthenticated so Fly's health-check prober can
reach them. For a real pilot, enable `AUTHZ_ENFORCEMENT_ENABLED=true` only
with a configured OIDC/SSO path or an ingress that validates and overwrites
the actor claims; set `AUTHZ_TRUSTED_GATEWAY_ENABLED=true` only for that
trusted ingress. The API fails closed with 503 if enforcement is enabled
without either boundary.

## One-time setup

1. **Neo4j AuraDB Free** — create an instance at
   [console.neo4j.io](https://console.neo4j.io). Note the `neo4j+s://...`
   connection URI, the username, and the one-time-shown generated password.
   `src/core/neo4j_client.py` passes `neo4j_uri` straight to the driver, which
   already supports the `neo4j+s://` scheme Aura requires — no code changes.

2. **Fly app + managed Redis**:
   ```bash
   fly apps create sales-context-graph   # must match `app` in fly.toml, or edit fly.toml to match
   fly redis create                      # note the connection string it prints
   ```

3. **Generate a per-workspace API key**:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

4. **Set secrets** (fill in values from steps 1-3; nothing here goes in `fly.toml` or git):
   ```bash
   fly secrets set \
     NEO4J_URI="neo4j+s://<your-instance-id>.databases.neo4j.io" \
     NEO4J_USER="neo4j" \
     NEO4J_PASSWORD="<from Aura console>" \
     REDIS_URL="<from fly redis create>" \
     WORKSPACE_API_KEYS='{"ws-demo":"<generated key from step 3>"}' \
     INGESTION_QUEUE_ENABLED="true" \
     INGESTION_WORKER_CONCURRENCY="2" \
     AUTHZ_ENFORCEMENT_ENABLED="true" \
     AUTHZ_TRUSTED_GATEWAY_ENABLED="true"
   ```

## Deploy

```bash
fly deploy
```

## Verify

```bash
fly status
fly logs
curl https://<your-app>.fly.dev/health
curl https://<your-app>.fly.dev/ready

# should be 401 — proves the auth gate is real, not just unit-tested
curl -X POST https://<your-app>.fly.dev/api/v1/context/build \
  -H "X-Workspace-Id: ws-demo" -H "Content-Type: application/json" -d '{}'

# should succeed
curl -X POST https://<your-app>.fly.dev/api/v1/context/build \
  -H "X-Workspace-Id: ws-demo" -H "X-Api-Key: <generated key>" \
  -H "Content-Type: application/json" -d '{}'

# with AUTHZ_ENFORCEMENT_ENABLED=true behind a trusted claims gateway,
# resource claims are forwarded as verified request context
curl -X GET "https://<your-app>.fly.dev/api/v1/opportunities/<opp-id>/conflicts" \
  -H "X-Workspace-Id: ws-demo" -H "X-Api-Key: <generated key>" \
  -H "X-User-Id: <verified-subject>" -H "X-User-Roles: seller" \
  -H "X-Authorized-Opportunities: <opp-id>"
```

When `INGESTION_QUEUE_ENABLED=true`, `/ready` also verifies Redis and a
short-lived worker heartbeat. A green API without a running worker is therefore
reported as `503`, rather than silently accepting ingest requests that will not
be processed.

The worker is a separate Fly process group. Verify it explicitly after deploy:

```bash
fly status
fly logs --process-group worker
```

`INGESTION_WORKER_CONCURRENCY` controls independent Redis claim slots per
worker process (1–32). It improves local throughput but is not a tenant-fair
capacity guarantee; publish measured load results before increasing it.

## Rotating or adding a workspace key

Edit the `WORKSPACE_API_KEYS` JSON map and re-run `fly secrets set` with the
full updated map, then `fly deploy` (or `fly secrets set` alone triggers a
restart with the new values, depending on your Fly plan). There's no
self-serve rotation or per-key revocation UI at this MVP stage — see
`docs/security-and-tenancy.md` for the honest scope of what this auth model
does and doesn't cover.

## Optional voice output

TTS is disabled by default. To enable the text-first audio enhancement, set
the following server-side secrets/configuration and redeploy:

```bash
fly secrets set TTS_PROVIDER="openai" TTS_API_KEY="<provider-key>"
fly secrets set TTS_MODEL="gpt-4o-mini-tts" TTS_VOICE="alloy" TTS_TIMEOUT_SECONDS="2"
```

The `POST /api/v1/tts` route returns MP3 only when explicitly requested by the
client. The Ask response is not blocked by audio generation; after the
two-second timeout the UI keeps the text answer and reports the audio fallback.

## What's explicitly out of scope for this MVP deploy

- No autoscaling beyond `min_machines_running = 1` — revisit once there's
  real traffic data.
- No CDN/WAF in front of the app.
- No structured logging shipped anywhere beyond Fly's own log capture
  (`fly logs`).
- The repository includes `make backup-verify` for a destructive local
  Neo4j round-trip check. Run it only against an explicitly disposable local
  volume; production restore evidence must be performed under the customer's
  approved backup/RPO/RTO procedure.
- No live Showpad OAuth/API connector or CRM write-back is created by this
  deployment recipe; those require external credentials and contract tests.
