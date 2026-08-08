"""POST /api/v1/alerts/check — docs/evaluation.md's Showpad engineering-
rigor assessment (2026-08-08, Band 4): "metrics without alerts." See
src/core/alerting.py for what's actually checked and why (the two Gauge
metrics, not the rate-based Counters -- those need a real Prometheus
deployment, see alerting/prometheus_rules.yml).

Cron-driven, same shape as POST /api/v1/digest/deliver -- an external cron
calls this periodically (docs/operations.md's existing "no in-process
scheduler" stance applies here too, not just to the digest).

Authenticated (verify_api_key) even though the checked metrics
(ingestion queue depth/age) are workspace-*wide*, not scoped to the
caller's own workspace -- this is a system-operational check, not tenant
data, but leaving it unauthenticated would let anyone trigger a Slack post
to this deployment's webhook on demand.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import verify_api_key
from src.core.alerting import check_and_alert
from src.core.config import get_settings

router = APIRouter(prefix="/api/v1", tags=["alerts"])


@router.post("/alerts/check")
async def check_alerts(_workspace_id: str = Depends(verify_api_key)) -> dict:
    settings = get_settings()
    try:
        breaches = await check_and_alert(
            settings.slack_webhook_url or None,
            max_queue_depth=settings.alert_max_queue_depth,
            max_oldest_job_age_seconds=settings.alert_max_oldest_job_age_seconds,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Slack delivery failed: {exc}") from exc
    return {"breach_count": len(breaches), "breaches": breaches, "slack_delivered": bool(breaches and settings.slack_webhook_url)}
