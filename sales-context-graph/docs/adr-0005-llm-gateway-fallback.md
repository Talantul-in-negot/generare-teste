# ADR-0005 — LLM gateway with multi-provider fallback (feature-flagged, off by default)

**Status:** Implemented (feature-flagged, off by default)
**Date:** 2026-08-07

The fallback gateway remains opt-in. It retries only transient availability
failures, never validation/schema failures, and emits telemetry for every
fallback. Routes continue to fail loudly when no provider is configured.

## Context

`docs/evaluation.md`'s external architecture-review cross-check evaluated
a generic industry brief's recommendation to add an LLM gateway with
multi-provider fallback. That analysis's own conclusion, unchanged by this
ADR:

> Premature here, where the honest behaviour on an unconfigured or failing
> provider is already a 503 (`src/llm/chat.py::LlmNotConfiguredError`). A
> fallback chain adds a silent-degradation path — exactly the failure mode
> this codebase has consistently refused.

The user reviewing `docs/evaluation.md` explicitly, and after this
rejection was raised directly, chose to implement it anyway as part of
"implement literally everything in this document, including the items
flagged as premature." This ADR documents that decision and the specific
design constraints that keep the stated risk (silent degradation) from
actually landing.

## Decision

Add `src/llm/gateway.py::build_gateway_chat_fn()`, a wrapper around
`src/llm/chat.py::build_chat_fn()` that falls back from the primary LLM
provider to a configured secondary provider, selected via
`LLM_FALLBACK_ENABLED=true` (+ `LLM_FALLBACK_PROVIDER` /
`LLM_FALLBACK_API_KEY` / `LLM_FALLBACK_MODEL`, all new `Settings` fields,
default off/`""`).

### The silent-degradation risk, and how this mitigates it

Three constraints, non-negotiable in the implementation:

1. **Fallback triggers only on a transient/availability error** from the
   provider SDK call itself — timeout, connection error, rate limit, or a
   5xx (`src/llm/gateway.py::_is_transient()`, matched against each
   provider's own exception hierarchy: `anthropic.APIConnectionError` /
   `RateLimitError` / `InternalServerError`, and the `openai` equivalents).
   A 4xx-shaped error (bad request, auth, permission, not-found,
   unprocessable) is explicitly excluded — that's a configuration or
   request problem a *different* provider wouldn't fix, and silently
   routing around a broken prompt or a revoked key forever is a worse
   outcome than the request failing loudly.
2. **Never triggers on a validation/schema failure.** Those are raised and
   retried entirely inside `src/llm/json_completion.py::complete_json()`'s
   own bounded repair loop, which calls the gateway's `chat_fn` as an
   opaque `prompt -> text` function and never sees — and doesn't need to
   know — that a gateway exists. The two retry loops (JSON-repair,
   provider-fallback) operate at different layers and never interact.
3. **Every fallback event is loud.** Logged at `warning`
   (`llm.gateway_fallback`, with `from_provider`/`to_provider`/`reason`)
   and counted via the new `scg_llm_fallback_total{from_provider,
   to_provider,reason}` counter (`src/core/telemetry.py`) — visible in
   logs, traces, and `/metrics` the moment it happens, never a quiet
   reroute an operator only discovers after the fact.

If fallback is disabled (the default), unconfigured, or itself fails, the
*original* exception from the primary provider propagates unchanged —
callers keep exactly the "fail loud with 503" behavior
`build_chat_fn()`/`LlmNotConfiguredError` already established. A
misconfigured fallback (enabled but missing a provider/key, or pointing at
an unsupported provider name) raises `LlmNotConfiguredError` at gateway
*construction* time, not silently at the first real outage.

### `openai` added to `SUPPORTED_PROVIDERS`

`src/llm/chat.py::SUPPORTED_PROVIDERS` gained `"openai"` — a real,
implemented branch (`_build_openai_chat_fn`, an actual
`openai.AsyncOpenAI` call), not a placeholder name. `build_chat_fn()` also
gained optional `provider`/`api_key`/`model` override parameters so the
gateway can construct a *second* `ChatFn` (the fallback) without a second
`Settings` instance; every existing caller (`build_chat_fn()` or
`build_chat_fn(settings)`) is unaffected — the overrides default to
`settings`'s own fields.

### Deliberately not wired into route call sites

`api/routes/qa.py`, `insights.py`, `ask.py`, and `context.py` all call
`build_chat_fn()` directly today and each has working, tested
monkeypatch-based coverage keyed to that exact name in the route module's
own namespace (`tests/integration/test_context_api.py`,
`test_narrative_summary_route.py`, `test_stakeholder_role_classification.py`).
Swapping a call site to `build_gateway_chat_fn()` for an explicitly-
optional, disabled-by-default capability would risk that already-correct
coverage for no measured benefit — the same reasoning
`docs/adr-0004-qdrant-secondary-vector-store.md` gives for not wiring
Qdrant into `CandidateGenerator`. A route (or a future phase) that wants
fallback swaps `build_chat_fn()` for `build_gateway_chat_fn()` directly —
identical `ChatFn`-in-`ChatFn`-out contract, a drop-in replacement.

## Consequences

- **Positive:** the item is closed for anyone reviewing
  `docs/evaluation.md` looking for "was the LLM gateway actually built" —
  genuinely built and unit-tested (`tests/unit/llm/test_gateway.py`):
  transient-error fallback fires and is counted, a validation-shaped
  error never triggers fallback, a disabled/unconfigured gateway is a
  true no-op, and a misconfigured fallback fails loud at construction.
- **Negative:** a second provider's API key becomes a thing to provision
  and rotate, if ever actually enabled — off by default, so this cost is
  opt-in.
- **Deferred deliberately:** no live-vendor integration test (unlike
  Kafka/Qdrant, there's no free local Docker equivalent for a second real
  LLM provider) — coverage is unit-level against mocked `ChatFn`s and
  synthetic provider-exception instances, not a real second vendor
  round-trip. No circuit breaker / half-open retry-the-primary-later
  logic — every call re-attempts the primary first; a sustained primary
  outage means a fallback call on every single request for as long as it
  lasts, which is the simplest correct behavior until real traffic
  justifies more.

## Not done in this ADR

Circuit-breaking, health-check-based provider selection, more than two
providers, and any per-request override of which provider to use all
remain out of scope. The primary provider (`LLM_PROVIDER`) stays the
default and recommended path — this gateway exists as an opt-in capability
under explicit stakeholder direction, not because the primary-only design
was found lacking.
