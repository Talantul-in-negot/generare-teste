"""P0 trace validation and tenant-safe Knowledge Graph reference checks."""

from __future__ import annotations

from graphrag.context_graph.models import DecisionTrace


class ContextGraphValidationError(ValueError):
    """Raised before persistence when a governed trace is incomplete."""


def validate_trace(trace: DecisionTrace) -> DecisionTrace:
    """Run model-level validation and return the validated trace."""
    try:
        trace.model_validate(trace)
    except ValueError as exc:
        raise ContextGraphValidationError(str(exc)) from exc
    return trace


def validate_kg_reference_tenant(reference_tenant: str, trace_tenant: str) -> None:
    if reference_tenant != trace_tenant:
        raise ContextGraphValidationError(
            f"cross-tenant Knowledge Graph reference: {reference_tenant!r} != {trace_tenant!r}"
        )
