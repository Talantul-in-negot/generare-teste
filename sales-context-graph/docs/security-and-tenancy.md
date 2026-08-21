# Security and Tenancy

## Tenant isolation mechanism

Every stored node carries `workspace_id`. `src/graph/execution.py`'s
`GraphExecutor.tenant_query()` is the only execution mode repositories use for
sales-domain data, and it **structurally rejects** Cypher that doesn't scope a
matched node/relationship by `workspace_id` — checked before any query reaches
the driver:

```python
_WORKSPACE_PROP_MAP_PATTERN = re.compile(r"\{[^{}]*\bworkspace_id\s*:\s*\$workspace_id\b[^{}]*\}")
_WORKSPACE_WHERE_EQUALITY_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.workspace_id\s*=\s*\$workspace_id\b")
```

Two accepted forms:
1. `{workspace_id: $workspace_id, ...}` inside a node/relationship pattern —
   what `scoped_match()` produces and every repository uses for MATCH/MERGE.
2. `x.workspace_id = $workspace_id` — needed for full-text/vector procedure
   calls (`CALL db.index.*.queryNodes(...) YIELD node`), which have no
   property-map MATCH pattern to scope at all; the equality must appear in a
   `WHERE` clause **before** any `ORDER BY`/`LIMIT`, never applied to an
   already-truncated result set.

A query with neither form raises `TenantScopingError` — proven in
`tests/unit/graph/test_execution.py`, including that a bare `$workspace_id`
parameter that's passed but never matched against is rejected.

`schema_query()` and `operational_query()` bypass this guard — allowlisted by
call-site convention (`src/graph/schema.py`/`src/graph/migrations/*` for the
former, `/health`+`/ready`+`SHOW INDEXES` for the latter). These are privileged
infrastructure paths, not user-facing tenant reads. User-facing routes add an
optional deny-by-default resource policy described below.

### Adversarial proof, not just unit-level

`tests/integration/test_tenant_isolation.py` runs two workspaces with
**identical** Account names, Claim subjects, and Mention statuses against the
live database and proves:
- a shared-attribute lookup (e.g. `find_accounts_by_name`) in workspace A never
  returns workspace B's row, even though both exist with the exact same name;
- reading a **real, valid** id from workspace B while scoped to workspace A
  returns nothing — proving the `MATCH` pattern's `workspace_id` property
  actually gates access, not merely that ids happen not to collide (they
  don't, by construction — every id is `crm_entity_id(workspace, ...)`, a hash
  that already includes `workspace`, so this test deliberately targets the one
  class of query that *doesn't* go through a unique hash: name/status lookups).

## Workspace vs. division

`workspace_id` is the tenant/security boundary. Showpad-derived nodes
(`ContentAsset`) additionally carry `division_id` — Showpad's own
organizational/permission dimension *inside* a workspace, not itself a tenant
boundary. They are not interchangeable. When authorization enforcement is
enabled, division and opportunity claims are applied on top of workspace
isolation; a division field alone is never treated as proof of access.

## Authentication — MVP API-key-per-workspace, not a real identity provider

`api/dependencies.py::get_workspace_id` reads a trusted `X-Workspace-Id`
header. On its own that's not authentication — it exists so every route
depends on one function (never reads a header or, worse, a request-body field
directly), which is what makes `workspace_id` "come from trusted request/
authentication context, not a user-controlled body field."

`api/dependencies.py::verify_api_key` composes on top of it and is what
authenticated routes actually depend on: it additionally requires an
`X-Api-Key` header matching the claimed workspace's configured secret
(`Settings.workspace_api_keys`, a `WORKSPACE_API_KEYS` JSON map env var,
compared with `secrets.compare_digest` for timing-safety). `/health` and
`/ready` deliberately stay on no auth at all (Fly.io's health-check prober
can't attach secrets, and both return only process/schema status, no tenant
data) — see `docs/deployment.md`.

This is still not a complete identity-provider deployment: API keys have no
self-serve rotation UI, and SSO/SCIM require an external IdP contract. The
repository does contain the application policy layer and route wiring. Enable
`AUTHZ_ENFORCEMENT_ENABLED=true` only with real SSO or
`AUTHZ_TRUSTED_GATEWAY_ENABLED=true` behind an ingress that validates and
overwrites actor claims. Otherwise the service returns 503 rather than trust
client-supplied roles.

## Current authorization enforcement

`src/auth/policy.py` defines pure, tested `AccessContext` rules for roles,
divisions, opportunities and sensitive content. API middleware denies
out-of-scope opportunity path requests before a handler runs. Body-scoped Ask,
Context, Q&A and ingestion routes apply the same policy after validation.
Ingestion additionally requires an ingestion/content-admin role when
enforcement is active.

The embedded panel is handled separately but consistently: a signed,
revocable panel token becomes a synthetic access context containing only its
workspace and opportunity. It cannot be used as a general user identity or a
workspace-wide credential: it is accepted only by the panel's two
opportunity-scoped reads (buying committee and objections), and both apply
`require_opportunity()` even when general authorization enforcement is off.
The workspace-wide digest requires a regular API key/JWT, so filtering a broad
response in browser JavaScript cannot leak other deals through a panel embed.
Audit events include optional actor id and roles, while workspace API keys
remain the authentication boundary until SSO is connected.

## PII and secrets

- Secrets (`NEO4J_PASSWORD`, `LLM_API_KEY`, `EMBEDDING_API_KEY`) come from
  environment variables only (`src/core/config.py`, `pydantic-settings`,
  `.env` — never committed; see `.env.example`).
- `src/core/config.py`'s production-secrets validator fails fast if
  `env=production` and `neo4j_password` is still the insecure default.
- Extraction prompts (`src/extraction/prompt.py`) delimit the transcript as
  data, explicitly instruct the model to treat embedded text as content to
  extract *from*, never instructions to follow, and grant no tool access — see
  `tests/security/test_prompt_injection_fixture.py`.
- No transcript text or email appears in `structlog` INFO-level log calls
  anywhere in this codebase (all logging is IDs/counts/enums — spot-check any
  `log.info(...)` call in `src/`). As of Phase 6 (2026-08-07) this is also a
  mechanical safeguard, not only a code-review discipline — see "PII
  handling" below.
- `Claim.retention_class` and `Claim.erasure_status`
  (`src/domain/enums.py::ErasureStatus`) exist on the model; `ErasureEvent`
  exists as an audit-record type. Contact erasure now removes the Neo4j
  embedding and, when Qdrant is configured, the tenant-scoped Qdrant point
  before the event is marked complete. Retention/legal-hold policy and
  third-party/object-store propagation remain deployment-level work.
- Buyer-space retention honours persisted legal holds before deleting upload
  bytes; the release and sweep APIs require an authorized manager when
  fine-grained authorization is enabled.

## PII handling — raw at rest, redacted at egress (added Phase 6, 2026-08-07)

A deliberate choice, not an oversight: `TranscriptSegment` (`src/domain/
conversation.py`) stays verbatim in Neo4j. This system's entire evidence
model depends on it — a `Claim`'s `evidence_char_start`/`evidence_char_end`
index into the real segment text, and a reviewer adjudicating a Claim (or a
future erasure-propagation implementation, see above) needs the actual
original span, not a redacted stand-in. Redacting before persistence would
silently break that contract.

Instead, redaction (`src/redaction/pii.py`, regex-based — email, phone,
SSN- and credit-card-shaped patterns) applies only at the two points raw
text actually leaves the system boundary:

- **LLM prompts** — `src/extraction/llm_provider.py::_extract_one` redacts
  `window_text` immediately before `src/extraction/prompt.py::
  build_extraction_prompt()` is called. The extraction LLM never sees an
  unredacted transcript.
- **Logs** — `src/core/logging.py`'s central `structlog.configure()` runs
  every string-valued log field through the same `redact_pii()`, blanket
  (every field, not a hand-maintained list of "fields known to carry raw
  text" — that list rots the moment a new log call is added elsewhere).

Both are gated by `PII_REDACTION_ENABLED` (default `true`). Regex-only,
not NER: this vertical slice's fixture-driven test corpus gives no signal
on whether a name/org NER pass would meaningfully improve recall over
these structured-PII patterns, and adding one (e.g. spaCy) speculatively —
a real new dependency, a downloaded model — isn't justified without that
measurement. See `src/redaction/pii.py`'s own docstring for the full
reasoning; this is a documented deferral, not a silent gap.

## What an adversarial reviewer should check next

1. Confirm every new repository method added after this document was written
   still routes through `tenant_query()` (grep for `operational_query(` /
   `schema_query(` outside `src/graph/schema.py`, `src/graph/migrations/`, and
   `/health`+`/ready` — any other call site is a policy violation).
2. Confirm no route reads `workspace_id` from `request.json()`/a Pydantic
   request body field — every `ContextBuildRequest`/`CrmIngestionRequest`/etc.
   deliberately has no `workspace_id` field (proven for one route in
   `tests/integration/test_context_api.py::
   test_context_build_workspace_id_comes_from_header_not_body`).
3. Production identity: replace or compose `verify_api_key`'s static-env-var
   key map with a revocable, rotatable, ideally hashed key store and connect a
   real JWT/session-claim IdP plus SCIM before onboarding beyond a controlled
   pilot. Keep `AUTHZ_ENFORCEMENT_ENABLED` fail-closed while that contract is
   being deployed.

## Panel-token scope repair — 2026-08-21

An authorization review found that the earlier panel design could retrieve a
workspace-wide digest and filter it in browser JavaScript, even though its
token was minted for one opportunity. Its two intended opportunity reads also
checked scope only when the general authorization feature flag was enabled.
Those are BOLA risks: a client-controlled identifier or an over-broad response
can disclose another deal in the same workspace.

The panel token capability is now allow-listed to buying-committee and
account-objections only. Both routes enforce `require_opportunity()` for the
synthetic panel principal regardless of global authorization mode; the digest
requires an API key/JWT and the panel no longer fetches it. Middleware also
recognizes a panel path, not an arbitrary `X-Panel-Token` header, so a fake
header cannot bypass actor checks. Panel-token minting also enforces the
caller's opportunity scope. Finally, API-key/JWT traffic through these
token-compatible routes uses the standard access-context builder, preserving
verified SSO claim precedence over caller-provided headers.

This follows [OWASP API1:2023 Broken Object Level Authorization](https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/)
and [OWASP API5:2023 Broken Function Level Authorization](https://owasp.org/API-Security/editions/2023/en/0xa5-broken-function-level-authorization/).
Regression coverage is in `tests/unit/api/test_authz_middleware.py`,
`tests/unit/api/test_viz_route.py`, and `tests/unit/api/test_auth_dependency.py`.
