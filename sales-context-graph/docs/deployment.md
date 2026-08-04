# Deployment (Fly.io)

MVP topology: the Fly app runs only the FastAPI container and is fully
stateless. Neo4j runs on managed **AuraDB Free** (not a self-hosted Neo4j on a
Fly volume — Neo4j is a stateful JVM process needing backup/upgrade
management this MVP doesn't want to operate itself). The ingestion job store
runs on Fly-managed **Redis** (`fly redis create`, Upstash-backed).

Auth is API-key-per-workspace (`X-Api-Key` header, checked against
`WORKSPACE_API_KEYS` — see `api/dependencies.py::verify_api_key` and
`docs/security-and-tenancy.md`). `/health` and `/ready` stay unauthenticated
so Fly's health-check prober can reach them.

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
     WORKSPACE_API_KEYS='{"ws-demo":"<generated key from step 3>"}'
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
```

## Rotating or adding a workspace key

Edit the `WORKSPACE_API_KEYS` JSON map and re-run `fly secrets set` with the
full updated map, then `fly deploy` (or `fly secrets set` alone triggers a
restart with the new values, depending on your Fly plan). There's no
self-serve rotation or per-key revocation UI at this MVP stage — see
`docs/security-and-tenancy.md` for the honest scope of what this auth model
does and doesn't cover.

## What's explicitly out of scope for this MVP deploy

- No autoscaling beyond `min_machines_running = 1` — revisit once there's
  real traffic data.
- No CDN/WAF in front of the app.
- No structured logging shipped anywhere beyond Fly's own log capture
  (`fly logs`).
- No automated Neo4j/Redis backup verification beyond what Aura/Upstash
  provide by default.
