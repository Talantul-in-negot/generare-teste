# ADR-0005 — LLM gateway with provider fallback

**Status:** Implemented (optional, disabled by default)  
**Date:** 2026-08-11

## Decision

The project provides `src/llm/gateway.py::build_gateway_chat_fn()` as an
optional wrapper around the normal LLM client. It can call a secondary
provider when the primary provider fails with a transient availability error.

The default configuration remains primary-provider only:

```env
LLM_FALLBACK_ENABLED=false
```

To configure the optional fallback, all of these settings are supplied:

```env
LLM_FALLBACK_ENABLED=true
LLM_FALLBACK_PROVIDER=openai
LLM_FALLBACK_API_KEY=...
LLM_FALLBACK_MODEL=...
```

The supported primary and fallback providers are the providers implemented in
`src/llm/chat.py` (currently Anthropic and OpenAI). The gateway preserves the
same `prompt -> text` `ChatFn` contract.

## Fallback rules

Fallback is attempted only for transient provider failures such as timeout,
connection failure, rate limiting or provider 5xx errors. Authentication,
permission, malformed-request and other 4xx errors are returned to the caller
and do not trigger a fallback.

The gateway does not handle JSON/schema repair. That remains the responsibility
of `src/llm/json_completion.py::complete_json()`, which has its own bounded
three-attempt validation/repair loop. Provider fallback and JSON repair are
separate layers.

Every fallback is observable: `llm.gateway_fallback` is logged at warning
level and `scg_llm_fallback_total` is exported through `/metrics` with source,
destination and reason labels. If fallback is disabled, missing or fails, the
primary exception is propagated and the service keeps its fail-loud behavior.

## Current wiring

The gateway is a drop-in capability but is not automatically substituted into
the existing route call sites. Routes that explicitly need fallback must call
`build_gateway_chat_fn()` instead of `build_chat_fn()`. The current default
request path therefore continues to use `build_chat_fn()` and the configured
primary provider.

## Consequences and boundaries

- A second provider key and model are optional operational dependencies.
- There is no automatic provider health-based selection or circuit breaker.
- The primary provider is retried first on every request; sustained outages
  can therefore cause one fallback attempt per request.
- Only one secondary provider is supported; there are no per-request provider
  overrides.
- There are no live cross-vendor integration tests; provider behavior is
  covered with unit tests and synthetic SDK exceptions.

The gateway is intended for explicit availability resilience. It does not
silently change prompts, schemas or answer semantics, and it is not enabled by
default in the current deployment.
