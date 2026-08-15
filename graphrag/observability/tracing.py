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


@contextmanager
def trace_span(name: str, **attributes):
    try:
        from opentelemetry import trace
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
        yield current
