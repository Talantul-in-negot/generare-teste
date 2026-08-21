"""Increment 17 — GET /api/v1/digest (JSON, always available) and
POST /api/v1/digest/deliver (build + post to Slack; 503 if no webhook is
configured, matching /api/v1/ask's honest-refusal shape for an unconfigured
dependency). Intended caller for /deliver: an external cron (this repo
deliberately has no in-process scheduler — see docker-compose.yml's own
comment and docs/operations.md for the reasoning).
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import verify_api_key
from src.core.config import get_settings
from src.delivery.slack import build_slack_blocks, post_digest
from src.graph.execution import GraphExecutor
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.stakeholder_repository import StakeholderRepository
from src.usecases.digest import DigestUseCase

router = APIRouter(prefix="/api/v1", tags=["digest"])


def _build_usecase(executor: GraphExecutor) -> DigestUseCase:
    settings = get_settings()
    return DigestUseCase(
        CrmRepository(executor), ClaimRepository(executor), ConversationRepository(executor),
        ContentRepository(executor), ConflictRepository(executor), StakeholderRepository(executor),
        stale_share_days=settings.digest_stale_share_days,
        stalled_deal_days=settings.digest_stalled_deal_days,
    )


@router.get("/digest")
async def digest(seller_id: str | None = None, workspace_id: str = Depends(verify_api_key)) -> dict:
    # A digest is workspace-wide and can disclose other opportunities.  It is
    # intentionally unavailable to an opportunity-scoped panel token.
    executor = GraphExecutor()
    usecase = _build_usecase(executor)
    result = await usecase.build(workspace_id, seller_id=seller_id)
    return result.model_dump(mode="json")


@router.post("/digest/deliver")
async def deliver_digest(seller_id: str | None = None, workspace_id: str = Depends(verify_api_key)) -> dict:
    settings = get_settings()
    if not settings.slack_webhook_url:
        raise HTTPException(
            status_code=503,
            detail="SLACK_WEBHOOK_URL is not set; GET /api/v1/digest still works without delivery.",
        )

    executor = GraphExecutor()
    usecase = _build_usecase(executor)
    result = await usecase.build(workspace_id, seller_id=seller_id)
    payload = build_slack_blocks(result)

    try:
        await post_digest(settings.slack_webhook_url, payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Slack delivery failed: {exc}") from exc

    return {"delivered": True, "signal_count": len(result.signals), "opportunity_count": result.opportunity_count}
