"""Threshold-based alerting on the metrics src/core/telemetry.py already
exposes.

docs/evaluation.md's Showpad engineering-rigor assessment (2026-08-08,
Band 4) found this precisely: "Metrics without alerts... nothing consumes
them. No alert rules, no thresholds, no on-call rotation, no error
budget." This module is the part of that gap closeable without standing
up a full Prometheus + Alertmanager deployment; alerting/prometheus_rules.yml
(a real, valid Alertmanager rules file) covers the rate-based Counter
metrics (job failure rate, guardrail flags, LLM fallbacks, ...) that
correctly need a real Prometheus time-series backend to evaluate --
`rate()`/`increase()` over a Counter can't be faithfully reproduced by an
in-process, no-history Python check the way an instantaneous Gauge
threshold can.

Scoped to the two Gauge metrics that ARE meaningfully checkable this way --
current queue depth and oldest-job age (src/core/telemetry.py) -- both
already "current state," not a rate needing history, and arguably the two
most actionable pages an on-call engineer could get anyway (a growing
backlog or a stuck queue).

Cron-driven, not an in-process scheduler: this repo has an explicit,
existing "no in-process scheduler" stance (docker-compose.yml's own
comment, docs/operations.md) already applied to the digest feature's
POST /api/v1/digest/deliver -- an external cron calls that periodically.
POST /api/v1/alerts/check (api/routes/alerts.py) follows the exact same
shape rather than introducing a second, inconsistent scheduling model.
"""

from __future__ import annotations

import structlog

from src.core.telemetry import INGESTION_QUEUE_DEPTH, INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS
from src.delivery.slack import post_digest

log = structlog.get_logger(__name__)


def check_gauge_thresholds(*, max_queue_depth: int, max_oldest_job_age_seconds: int) -> list[dict]:
    """Pure -- reads the two gauges' current values and returns a breach
    per threshold exceeded. Separated from the Slack-posting side (below)
    the same way build_slack_blocks/post_digest already split pure
    formatting from network I/O, so this half is trivially unit-testable
    without a live webhook."""
    breaches = []
    depth = INGESTION_QUEUE_DEPTH._value.get()
    if depth > max_queue_depth:
        breaches.append({
            "metric": "scg_ingestion_queue_depth", "value": depth, "threshold": max_queue_depth,
            "message": f"Ingestion queue depth is {depth:.0f}, above the {max_queue_depth} threshold.",
        })

    oldest_age = INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS._value.get()
    if oldest_age > max_oldest_job_age_seconds:
        breaches.append({
            "metric": "scg_ingestion_queue_oldest_job_age_seconds", "value": oldest_age,
            "threshold": max_oldest_job_age_seconds,
            "message": f"Oldest queued ingestion job is {oldest_age:.0f}s old, "
                       f"above the {max_oldest_job_age_seconds}s threshold.",
        })
    return breaches


def build_alert_blocks(breaches: list[dict]) -> dict:
    """Slack Block Kit payload -- same shape as src/delivery/slack.py's
    build_slack_blocks, reusing post_digest() below to actually send it
    rather than adding a second HTTP-posting function for what's really
    the same "POST a Block Kit payload to a webhook" operation."""
    header_text = f"⚠️ {len(breaches)} threshold alert(s)"
    blocks: list[dict] = [{"type": "header", "text": {"type": "plain_text", "text": header_text}}]
    for breach in breaches:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": breach["message"]}})
    return {"blocks": blocks}


async def check_and_alert(webhook_url: str | None, *, max_queue_depth: int, max_oldest_job_age_seconds: int) -> list[dict]:
    """Returns the breach list regardless of whether a webhook is
    configured -- same "JSON always available" framing as GET
    /api/v1/digest, so this endpoint is useful for a human/monitoring
    system polling it directly even without Slack wired up. Only posts to
    Slack (reusing src/delivery/slack.py::post_digest -- same function,
    a Block Kit payload is a Block Kit payload) when there's something to
    report AND a webhook is configured."""
    breaches = check_gauge_thresholds(
        max_queue_depth=max_queue_depth, max_oldest_job_age_seconds=max_oldest_job_age_seconds
    )
    if breaches:
        log.warning("alerting.thresholds_breached", breaches=breaches)
        if webhook_url:
            await post_digest(webhook_url, build_alert_blocks(breaches))
    return breaches
