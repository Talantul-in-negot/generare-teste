"""OpenTelemetry GenAI semantic conventions for model calls.

Why a separate module
---------------------
The platform already records model *cost* (`cost_attribution`) and *stage*
latency. What it did not emit is the vendor-neutral shape every GenAI-aware
backend now keys on — `gen_ai.operation.name`, `gen_ai.system`,
`gen_ai.request.model`, `gen_ai.response.model`, token usage. Without those
attribute names, an LLM span in Grafana/Tempo/Datadog is an anonymous
`http.request`-shaped blob and none of the built-in GenAI views light up.

Scope, deliberately narrow
--------------------------
Only the attributes this platform can populate *truthfully* are emitted. The
convention defines many more (top_p, seed, finish reasons, per-choice events);
emitting placeholder or guessed values for them would be worse than omitting
them, because a dashboard cannot tell a real zero from a fabricated one.

Token usage is recorded only when the provider actually returns it. Several
clients in this codebase return a bare string with no usage block, and
inferring counts from `len(text) / 4` would put invented numbers into a
cost-adjacent metric.

Prompt and completion content are **never** attached to spans. The convention
makes that opt-in for good reason: the prompts here carry customer document
text, and a trace backend is not a system of record with the retention,
tenancy, and erasure guarantees that data needs. `graphrag/graph/gdpr.py`
exists precisely because that content is subject to deletion requests.

Stability
---------
The GenAI conventions are still evolving. This module pins the attribute names
in one place so a convention change is a single edit here rather than a sweep
through provider clients — which is the reason the roadmap listed "adopt the
stable fields" rather than "instrument every call site".
"""

from __future__ import annotations

import time
from contextlib import contextmanager

import structlog

log = structlog.get_logger(__name__)

try:
    from prometheus_client import Counter, Histogram
except ImportError:  # pragma: no cover - optional dependency
    Counter = Histogram = None

# Attribute names, pinned in one place. Values follow the OpenTelemetry GenAI
# semantic conventions.
ATTR_OPERATION = "gen_ai.operation.name"
ATTR_SYSTEM = "gen_ai.system"
ATTR_REQUEST_MODEL = "gen_ai.request.model"
ATTR_RESPONSE_MODEL = "gen_ai.response.model"
ATTR_TEMPERATURE = "gen_ai.request.temperature"
ATTR_MAX_TOKENS = "gen_ai.request.max_tokens"
ATTR_INPUT_TOKENS = "gen_ai.usage.input_tokens"
ATTR_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ATTR_ERROR_TYPE = "error.type"

# `gen_ai.system` is a closed vocabulary; map this platform's provider names
# onto it so a backend groups them with everyone else's spans instead of
# inventing a private system name.
_SYSTEM_BY_PROVIDER = {
    "groq": "groq",
    "deepseek": "deepseek",
    "openai": "openai",
    "openrouter": "openrouter",
    "cerebras": "cerebras",
    "gemini": "gcp.gemini",
}

_llm_calls = Counter(
    "graphrag_gen_ai_calls_total",
    "Model calls by system, operation and outcome",
    ["system", "operation", "outcome"],
) if Counter else None

_llm_duration = Histogram(
    "graphrag_gen_ai_client_operation_duration_seconds",
    "Model call duration",
    ["system", "operation"],
    buckets=(0.05, 0.25, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
) if Histogram else None

_llm_tokens = Counter(
    "graphrag_gen_ai_client_token_usage_total",
    "Tokens reported by the provider, by direction",
    ["system", "direction"],
) if Counter else None


def system_for(provider: str) -> str:
    """Map an internal provider name onto the GenAI `system` vocabulary."""
    return _SYSTEM_BY_PROVIDER.get((provider or "").lower(), provider or "unknown")


def record_token_usage(provider: str, input_tokens: int | None, output_tokens: int | None) -> None:
    """Record provider-reported token counts.

    Silently ignores None: a provider that does not report usage must not be
    represented as having used zero tokens, because zero is a number a budget
    alert will happily believe.
    """
    if not _llm_tokens:
        return
    system = system_for(provider)
    try:
        if input_tokens is not None:
            _llm_tokens.labels(system=system, direction="input").inc(max(0, int(input_tokens)))
        if output_tokens is not None:
            _llm_tokens.labels(system=system, direction="output").inc(max(0, int(output_tokens)))
    except Exception as exc:  # noqa: BLE001 - telemetry must never break a call
        log.debug("genai_telemetry.token_record_failed", error=str(exc))


@contextmanager
def llm_call_span(
    *,
    provider: str,
    model: str,
    operation: str = "chat",
    temperature: float | None = None,
    max_tokens: int | None = None,
):
    """Wrap one model call in a GenAI-conventional span and metrics.

    Yields a small mutable dict the caller may update with what only becomes
    known after the response arrives (`response_model`, `input_tokens`,
    `output_tokens`). Anything left unset is simply not emitted.
    """
    system = system_for(provider)
    attributes: dict[str, object] = {
        ATTR_OPERATION: operation,
        ATTR_SYSTEM: system,
        ATTR_REQUEST_MODEL: model or "",
    }
    if temperature is not None:
        attributes[ATTR_TEMPERATURE] = float(temperature)
    if max_tokens is not None:
        attributes[ATTR_MAX_TOKENS] = int(max_tokens)

    response: dict[str, object] = {}
    started = time.perf_counter()
    outcome = "success"

    # Imported lazily and tolerantly: tracing is optional everywhere else in
    # this package, and a model call must not depend on it.
    try:
        from graphrag.observability.tracing import trace_span
    except ImportError:  # pragma: no cover
        trace_span = None

    if trace_span is None:
        try:
            yield response
        except BaseException as exc:
            outcome = type(exc).__name__
            raise
        finally:
            _finish(system, operation, outcome, time.perf_counter() - started, provider, response)
        return

    # `gen_ai.{operation} {model}` is the conventional span name; it keeps
    # spans groupable by operation without exploding on free-text prompts.
    with trace_span(f"{operation} {model}".strip(), **attributes) as span:
        try:
            yield response
        except BaseException as exc:
            outcome = type(exc).__name__
            if span is not None:
                try:
                    span.set_attribute(ATTR_ERROR_TYPE, outcome)
                except Exception:  # noqa: BLE001
                    pass
            raise
        finally:
            if span is not None:
                _apply_response_attributes(span, response)
            _finish(system, operation, outcome, time.perf_counter() - started, provider, response)


def _apply_response_attributes(span, response: dict) -> None:
    mapping = (
        ("response_model", ATTR_RESPONSE_MODEL),
        ("input_tokens", ATTR_INPUT_TOKENS),
        ("output_tokens", ATTR_OUTPUT_TOKENS),
    )
    for key, attribute in mapping:
        value = response.get(key)
        if value is None:
            continue
        try:
            span.set_attribute(attribute, value)
        except Exception as exc:  # noqa: BLE001
            log.debug("genai_telemetry.attribute_failed", attribute=attribute, error=str(exc))


def _finish(
    system: str, operation: str, outcome: str, elapsed: float,
    provider: str, response: dict,
) -> None:
    try:
        if _llm_calls:
            _llm_calls.labels(system=system, operation=operation, outcome=outcome).inc()
        if _llm_duration:
            _llm_duration.labels(system=system, operation=operation).observe(max(0.0, elapsed))
    except Exception as exc:  # noqa: BLE001
        log.debug("genai_telemetry.metric_failed", error=str(exc))
    record_token_usage(
        provider, response.get("input_tokens"), response.get("output_tokens"),
    )
