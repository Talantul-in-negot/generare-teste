"""A span whose body was cancelled must be exported as an error.

The OpenTelemetry SDK marks spans that raise ``Exception``; it does not catch
``BaseException``, so ``asyncio.CancelledError`` escaped unrecorded. Request
timeouts, budget aborts, and worker shutdown all unwind through cancellation
in this codebase, so those spans were exported as UNSET -- indistinguishable
from success in any trace backend. Both halves are pinned here: the SDK's own
behaviour for ordinary errors, and the cancellation case this wrapper adds.
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode  # noqa: E402

from graphrag.observability.tracing import trace_span  # noqa: E402


@pytest.fixture
def exported_spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # set_tracer_provider() is once-per-process and warns on re-entry, so patch
    # the module global the API reads instead of fighting that.
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider, raising=False)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER_SET_ONCE", trace.Once(), raising=False)
    yield exporter


class TestSpanErrorStatus:
    def test_successful_span_is_not_marked_as_an_error(self, exported_spans):
        with trace_span("http.request", http_method="GET"):
            pass
        span = exported_spans.get_finished_spans()[0]
        assert span.status.status_code is not StatusCode.ERROR
        assert span.attributes["http_method"] == "GET"

    def test_failing_span_records_the_exception_and_sets_error_status(self, exported_spans):
        with pytest.raises(RuntimeError, match="neo4j unreachable"):
            with trace_span("graph.query", tenant="aerospace"):
                raise RuntimeError("neo4j unreachable")

        span = exported_spans.get_finished_spans()[0]
        assert span.status.status_code is StatusCode.ERROR
        # The SDK's own description is richer than a bare type name; the
        # wrapper must not overwrite it.
        assert span.status.description == "RuntimeError: neo4j unreachable"
        assert [event.name for event in span.events] == ["exception"]
        assert span.attributes["tenant"] == "aerospace"

    def test_cancellation_is_attributed_to_the_span_it_interrupted(self, exported_spans):
        import asyncio

        with pytest.raises(asyncio.CancelledError):
            with trace_span("query.answer"):
                raise asyncio.CancelledError()

        span = exported_spans.get_finished_spans()[0]
        assert span.status.status_code is StatusCode.ERROR
        assert span.status.description == "CancelledError"

    def test_none_valued_attributes_are_dropped(self, exported_spans):
        with trace_span("http.request", correlation_id=None, http_route="/query"):
            pass
        span = exported_spans.get_finished_spans()[0]
        assert "correlation_id" not in span.attributes
        assert span.attributes["http_route"] == "/query"
