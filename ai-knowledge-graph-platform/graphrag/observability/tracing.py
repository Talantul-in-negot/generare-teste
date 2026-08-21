"""Small OpenTelemetry boundary that degrades to no-op when not configured."""

from __future__ import annotations

import os
from contextlib import contextmanager


_configured = False


def configure_tracing(service_name: str) -> None:
    global _configured
    if _configured or not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        _configured = True
    except ImportError:
        return


def shutdown_tracing() -> None:
    """Flush queued spans before a worker or API process exits."""
    if not _configured:
        return
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        shutdown = getattr(provider, "shutdown", None)
        if shutdown:
            shutdown()
    except ImportError:
        return


@contextmanager
def trace_span(name: str, **attributes):
    """Start a span, recording *any* exception that escapes the body.

    The OpenTelemetry SDK's own ``use_span`` already records and marks
    ``Exception``. It catches nothing broader, so a span interrupted by
    ``asyncio.CancelledError`` -- which is a ``BaseException`` since Python
    3.8 -- was exported with the default UNSET status and no exception event,
    indistinguishable in a trace backend from one that completed successfully.
    That matters here specifically: request timeouts, budget aborts, and
    worker shutdown all unwind through cancellation, so the spans an operator
    most wants to find during an incident were the ones recorded as healthy.

    Only the cases the SDK does not already cover are annotated here, so its
    richer ``"Type: message"`` status description is left intact for ordinary
    errors. Everything is re-raised unchanged.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode
        span = trace.get_tracer("graphrag").start_as_current_span(name)
    except ImportError:
        span = None
    if span is None:
        yield None
        return
    with span as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        try:
            yield current
        except Exception:
            raise  # already recorded and marked ERROR by the SDK's use_span
        except BaseException as exc:
            current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
