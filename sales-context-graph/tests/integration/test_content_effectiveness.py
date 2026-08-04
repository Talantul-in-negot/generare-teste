"""Increment 10 — content-effectiveness loop, against live Neo4j.

Covers: (1) CrmRepository.upsert_opportunity records a stage change exactly
once per genuine transition and not on a same-value re-ingest; (2)
ContentIngestionPipeline.ingest_shares/ingest_asset_views actually persist
(previously entirely unwired for asset views, non-existent for shares); (3)
ContentEffectivenessUseCase correctly reports opened/stage-changed vs. not,
citing share_id and claim_id; (4) the HTTP route end to end.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from api.main import app
from src.domain.crm import Opportunity
from src.domain.knowledge import ContentAsset
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.ingestion.adapters.showpad import ShowpadAdapter
from src.ingestion.pipeline import ContentIngestionPipeline
from src.usecases.content_effectiveness import ContentEffectivenessUseCase
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _opportunity(workspace_id: str, opportunity_id: str, stage: str) -> Opportunity:
    return Opportunity(
        opportunity_id=opportunity_id, workspace_id=workspace_id, source_record_id="sr-1",
        account_id="acc-1", seller_id="seller-1", name="Test Deal", stage=stage, is_open=True,
    )


async def test_stage_change_recorded_once_per_genuine_transition(executor):
    workspace_id = f"ws-stage-{uuid4().hex[:8]}"
    crm_repo = CrmRepository(executor)
    opportunity_id = "opp-stage-test"

    await crm_repo.upsert_opportunity(_opportunity(workspace_id, opportunity_id, "Discovery"))
    changes = await crm_repo.list_stage_changes(workspace_id, opportunity_id)
    assert changes == []  # first write ever — nothing to compare against

    await crm_repo.upsert_opportunity(_opportunity(workspace_id, opportunity_id, "Discovery"))
    changes = await crm_repo.list_stage_changes(workspace_id, opportunity_id)
    assert changes == []  # same stage re-ingested — no spurious transition

    await crm_repo.upsert_opportunity(_opportunity(workspace_id, opportunity_id, "Negotiation"))
    changes = await crm_repo.list_stage_changes(workspace_id, opportunity_id)
    assert len(changes) == 1
    assert changes[0].from_stage == "Discovery"
    assert changes[0].to_stage == "Negotiation"

    await crm_repo.upsert_opportunity(_opportunity(workspace_id, opportunity_id, "Closed Won"))
    changes = await crm_repo.list_stage_changes(workspace_id, opportunity_id)
    assert len(changes) == 2
    assert changes[1].from_stage == "Negotiation"
    assert changes[1].to_stage == "Closed Won"


async def test_ingest_shares_and_asset_views_pipeline_wiring(executor):
    workspace_id = f"ws-engage-{uuid4().hex[:8]}"
    content_repo = ContentRepository(executor)
    pipeline = ContentIngestionPipeline(content_repo, source_repo=None, adapter=ShowpadAdapter())

    asset = ContentAsset(
        content_asset_id="asset-engage-1", workspace_id=workspace_id,
        title="ROI Calculator", url="https://showpad.example/roi", tags=["pricing"],
    )
    await content_repo.upsert_content_asset(asset)

    await pipeline.ingest_shares(
        workspace_id,
        [{"id": "share-ext-1", "content_asset_id": asset.content_asset_id,
          "shared_with_contact_id": "contact-1", "shared_at": _T0.isoformat(),
          "opportunity_id": "opp-engage-1", "triggered_by_claim_id": "claim-1"}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    shares = await content_repo.list_shares_for_opportunity(workspace_id, "opp-engage-1")
    assert len(shares) == 1
    assert shares[0].triggered_by_claim_id == "claim-1"

    view_time = _T0 + timedelta(hours=2)
    await pipeline.ingest_asset_views(
        workspace_id,
        [{"id": "view-ext-1", "content_asset_id": asset.content_asset_id,
          "viewer_contact_id": "contact-1", "viewed_at": view_time.isoformat()}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    views = await content_repo.list_views_for_asset_and_contact(workspace_id, asset.content_asset_id, "contact-1")
    assert len(views) == 1
    assert views[0].viewed_at == view_time


async def test_content_effectiveness_use_case_distinguishes_effective_from_not(executor):
    workspace_id = f"ws-eff-{uuid4().hex[:8]}"
    content_repo = ContentRepository(executor)
    crm_repo = CrmRepository(executor)
    pipeline = ContentIngestionPipeline(content_repo, source_repo=None, adapter=ShowpadAdapter())
    opportunity_id = "opp-effectiveness"

    asset_effective = ContentAsset(
        content_asset_id="asset-effective", workspace_id=workspace_id,
        title="Pricing Guide", url="https://showpad.example/pricing", tags=["pricing"],
    )
    asset_ineffective = ContentAsset(
        content_asset_id="asset-ineffective", workspace_id=workspace_id,
        title="Security Whitepaper", url="https://showpad.example/security", tags=["security"],
    )
    await content_repo.upsert_content_asset(asset_effective)
    await content_repo.upsert_content_asset(asset_ineffective)

    await crm_repo.upsert_opportunity(_opportunity(workspace_id, opportunity_id, "Negotiation"))

    # effective: shared, opened afterward, stage advanced afterward
    await pipeline.ingest_shares(
        workspace_id,
        [{"id": "share-effective", "content_asset_id": asset_effective.content_asset_id,
          "shared_with_contact_id": "buyer-1", "shared_at": _T0.isoformat(),
          "opportunity_id": opportunity_id, "triggered_by_claim_id": "claim-pricing"}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    await pipeline.ingest_asset_views(
        workspace_id,
        [{"id": "view-effective", "content_asset_id": asset_effective.content_asset_id,
          "viewer_contact_id": "buyer-1", "viewed_at": (_T0 + timedelta(hours=1)).isoformat()}],
        ingestion_run_id="run-1", observed_at=_T0,
    )
    await crm_repo.upsert_opportunity(_opportunity(workspace_id, opportunity_id, "Closed Won"))

    # ineffective: shared, never opened, no stage change after the share.
    # CrmRepository._record_stage_change stamps changed_at with the real
    # wall clock (like every other created_at/updated_at in this codebase),
    # not the fixture's fictional _T0 — so "after the share" must be
    # anchored to real now(), not to _T0-relative fixture time.
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    await pipeline.ingest_shares(
        workspace_id,
        [{"id": "share-ineffective", "content_asset_id": asset_ineffective.content_asset_id,
          "shared_with_contact_id": "buyer-1", "shared_at": later.isoformat(),
          "opportunity_id": opportunity_id}],
        ingestion_run_id="run-1", observed_at=_T0,
    )

    usecase = ContentEffectivenessUseCase(content_repo, crm_repo)
    report = await usecase.analyze(workspace_id, opportunity_id)

    assert len(report.shares) == 2

    effective = next(s for s in report.shares if s.content_asset_id == asset_effective.content_asset_id)
    assert effective.opened is True
    assert effective.opened_at is not None
    assert effective.stage_changed_after_share is True
    assert effective.stage_at_share_time == "Negotiation"
    assert effective.latest_stage == "Closed Won"
    assert effective.triggered_by_claim_id == "claim-pricing"

    ineffective = next(s for s in report.shares if s.content_asset_id == asset_ineffective.content_asset_id)
    assert ineffective.opened is False
    assert ineffective.opened_at is None
    assert ineffective.stage_changed_after_share is False  # the only change happened before this share
    assert ineffective.stage_at_share_time == "Closed Won"


async def test_content_effectiveness_route_returns_report(executor, monkeypatch):
    workspace_id = f"ws-eff-api-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    content_repo = ContentRepository(executor)
    crm_repo = CrmRepository(executor)
    pipeline = ContentIngestionPipeline(content_repo, source_repo=None, adapter=ShowpadAdapter())
    opportunity_id = "opp-eff-api"

    asset = ContentAsset(
        content_asset_id="asset-api-1", workspace_id=workspace_id,
        title="Deck", url="https://showpad.example/deck", tags=["pricing"],
    )
    await content_repo.upsert_content_asset(asset)
    await crm_repo.upsert_opportunity(_opportunity(workspace_id, opportunity_id, "Discovery"))
    await pipeline.ingest_shares(
        workspace_id,
        [{"id": "share-api-1", "content_asset_id": asset.content_asset_id,
          "shared_with_contact_id": "buyer-api", "shared_at": _T0.isoformat(),
          "opportunity_id": opportunity_id}],
        ingestion_run_id="run-1", observed_at=_T0,
    )

    async with _client() as client:
        resp = await client.get(f"/api/v1/opportunities/{opportunity_id}/content-effectiveness", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["opportunity_id"] == opportunity_id
    assert len(body["shares"]) == 1
    assert body["shares"][0]["opened"] is False
