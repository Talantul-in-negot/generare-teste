"""docs/evaluation.md's Showpad engineering-rigor assessment (2026-08-08,
Band 4: "metrics without alerts") -- src/core/alerting.py's Gauge
threshold checks. Sets the three Gauges to known values before each
assertion (they're module-level singletons shared across the whole test
session, same reasoning tests/unit/core/test_telemetry.py's own module
docstring already documents) and resets them afterward so this file's
state doesn't leak into unrelated tests.
"""

from __future__ import annotations

import pytest

from src.core.alerting import build_alert_blocks, check_and_alert, check_gauge_thresholds
from src.core.telemetry import (
    INGESTION_DLQ_DEPTH,
    INGESTION_QUEUE_DEPTH,
    INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS,
)


@pytest.fixture(autouse=True)
def reset_gauges():
    yield
    INGESTION_QUEUE_DEPTH.set(0)
    INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(0)
    INGESTION_DLQ_DEPTH.set(0)


def _check(**overrides):
    defaults = {"max_queue_depth": 100, "max_oldest_job_age_seconds": 900, "max_dlq_depth": 5}
    return check_gauge_thresholds(**{**defaults, **overrides})


def test_no_breach_when_all_three_gauges_are_under_threshold():
    INGESTION_QUEUE_DEPTH.set(5)
    INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(10)
    INGESTION_DLQ_DEPTH.set(0)

    assert _check() == []


def test_breach_reported_when_queue_depth_exceeds_threshold():
    INGESTION_QUEUE_DEPTH.set(150)

    breaches = _check()

    assert len(breaches) == 1
    assert breaches[0]["metric"] == "scg_ingestion_queue_depth"
    assert breaches[0]["value"] == 150


def test_breach_reported_when_oldest_job_age_exceeds_threshold():
    INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(1200)

    breaches = _check()

    assert len(breaches) == 1
    assert breaches[0]["metric"] == "scg_ingestion_queue_oldest_job_age_seconds"


def test_breach_reported_when_dlq_depth_exceeds_threshold():
    INGESTION_DLQ_DEPTH.set(12)

    breaches = _check()

    assert len(breaches) == 1
    assert breaches[0]["metric"] == "scg_ingestion_dlq_depth"
    assert breaches[0]["value"] == 12
    assert "dead-letter" in breaches[0]["message"]


def test_all_three_gauges_can_breach_independently():
    INGESTION_QUEUE_DEPTH.set(150)
    INGESTION_QUEUE_OLDEST_JOB_AGE_SECONDS.set(1200)
    INGESTION_DLQ_DEPTH.set(12)

    breaches = _check()

    assert len(breaches) == 3
    metrics = {b["metric"] for b in breaches}
    assert metrics == {
        "scg_ingestion_queue_depth",
        "scg_ingestion_queue_oldest_job_age_seconds",
        "scg_ingestion_dlq_depth",
    }


def test_build_alert_blocks_includes_every_breach_message():
    breaches = [
        {"metric": "a", "value": 1, "threshold": 0, "message": "first breach"},
        {"metric": "b", "value": 2, "threshold": 0, "message": "second breach"},
    ]

    payload = build_alert_blocks(breaches)

    texts = [block["text"]["text"] for block in payload["blocks"] if block["type"] == "section"]
    assert "first breach" in texts
    assert "second breach" in texts


@pytest.mark.asyncio
async def test_check_and_alert_returns_breaches_without_a_webhook_configured():
    INGESTION_QUEUE_DEPTH.set(150)

    breaches = await check_and_alert(
        None, max_queue_depth=100, max_oldest_job_age_seconds=900, max_dlq_depth=5
    )

    assert len(breaches) == 1


@pytest.mark.asyncio
async def test_check_and_alert_posts_to_slack_only_when_there_are_breaches(monkeypatch):
    posted = []

    async def fake_post_digest(webhook_url, payload):
        posted.append((webhook_url, payload))

    import src.core.alerting as alerting_mod

    monkeypatch.setattr(alerting_mod, "post_digest", fake_post_digest)

    # No breach -- must not post.
    await check_and_alert(
        "https://hooks.slack.example/x", max_queue_depth=100, max_oldest_job_age_seconds=900, max_dlq_depth=5
    )
    assert posted == []

    # DLQ breach alone -- must post exactly once.
    INGESTION_DLQ_DEPTH.set(12)
    await check_and_alert(
        "https://hooks.slack.example/x", max_queue_depth=100, max_oldest_job_age_seconds=900, max_dlq_depth=5
    )
    assert len(posted) == 1
    assert posted[0][0] == "https://hooks.slack.example/x"
