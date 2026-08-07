from __future__ import annotations

import structlog
from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from api.routes import ask, context, digest, health, ingestions, insights, qa, unresolved_mentions, viz
from src.core.logging import configure_logging

# Must run before any route handler emits its first log line -- see
# src/core/logging.py for why this is the one place configure() is called.
configure_logging()

log = structlog.get_logger(__name__)

app = FastAPI(title="Sales Context Graph API", version="0.1.0")

# Auto-instruments every route below with request spans (method, route,
# status code). No collector is configured here -- an operator wires an
# OTLP exporter via the standard OTEL_* env vars; unconfigured, spans are
# created and dropped, which costs a little CPU but never fails a request.
#
# Imported lazily and defensively: the opentelemetry-instrumentation-*
# contrib packages version-lockstep tightly with opentelemetry-util-http,
# and a shared/non-isolated Python environment with several unrelated
# projects installed into it (as this one is) can easily end up with that
# lockstep broken by some other project's install. Tracing degrades to a
# no-op rather than taking the whole API down over a transitive dependency
# mismatch it doesn't control.
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
except Exception:  # pragma: no cover - exercised only by a broken env
    log.warning("otel.fastapi_instrumentation_unavailable", exc_info=True)

app.include_router(health.router)
app.include_router(ingestions.router)
app.include_router(unresolved_mentions.router)
app.include_router(context.router)
app.include_router(ask.router)
app.include_router(qa.router)
app.include_router(insights.router)
app.include_router(digest.router)
app.include_router(viz.router)


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus scrape target for the metrics in src/core/telemetry.py."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
