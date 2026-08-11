"""Executable vertical slices for the formerly missing product pillars."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from api.main import app
from src.domain.crm import Opportunity
from src.graph.repositories.crm_repository import CrmRepository
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed_opportunity(executor, workspace_id: str) -> str:
    opportunity_id = f"opp-workflow-{uuid4().hex}"
    await CrmRepository(executor).upsert_opportunity(Opportunity(
        opportunity_id=opportunity_id,
        workspace_id=workspace_id,
        source_record_id="source-workflow",
        account_id="account-workflow",
        seller_id="seller-workflow",
        name="Workflow deal",
        stage="Discovery",
    ))
    return opportunity_id


async def test_readiness_buyer_engagement_revenue_and_meeting_workflow(executor, monkeypatch):
    workspace_id = f"ws-product-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    opportunity_id = await _seed_opportunity(executor, workspace_id)

    async with _client() as client:
        curriculum = await client.post(
            "/api/v1/readiness/curricula", headers=headers,
            json={"title": "Discovery certification", "required_role": "seller"},
        )
        assert curriculum.status_code == 201
        assignment = await client.post(
            "/api/v1/readiness/assignments", headers=headers,
            json={"curriculum_id": curriculum.json()["curriculum_id"], "seller_id": "seller-workflow"},
        )
        assert assignment.status_code == 201
        completed = await client.patch(
            f"/api/v1/readiness/assignments/{assignment.json()['assignment_id']}", headers=headers,
            json={"status": "COMPLETED", "score": 92},
        )
        assert completed.status_code == 200
        readiness = await client.get("/api/v1/readiness/sellers/seller-workflow", headers=headers)
        assert readiness.status_code == 200
        assert readiness.json()["assignments"][0]["assignment_id"] == assignment.json()["assignment_id"]
        assert readiness.json()["completion_rate"] == 1
        assert readiness.json()["average_score"] == 92

        space = await client.post(
            f"/api/v1/opportunities/{opportunity_id}/buyer-spaces", headers=headers,
            json={"title": "Mutual plan"},
        )
        assert space.status_code == 201
        space_id = space.json()["space_id"]
        step = await client.post(
            f"/api/v1/buyer-spaces/{space_id}/next-steps", headers=headers,
            json={"title": "Confirm security review", "owner_label": "Buyer security"},
        )
        assert step.status_code == 201
        comment = await client.post(
            f"/api/v1/buyer-spaces/{space_id}/comments", headers=headers,
            json={"body": "Security review planned for Thursday."},
        )
        assert comment.status_code == 201
        detail = await client.get(f"/api/v1/buyer-spaces/{space_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["next_steps"][0]["title"] == "Confirm security review"
        assert detail.json()["comments"][0]["body"] == "Security review planned for Thursday."

        outcome = await client.post(
            f"/api/v1/opportunities/{opportunity_id}/revenue-outcomes", headers=headers,
            json={"outcome_type": "WON", "amount_cents": 125000, "attributed_space_id": space_id},
        )
        assert outcome.status_code == 201
        revenue = await client.get("/api/v1/revenue/summary", headers=headers)
        assert revenue.status_code == 200
        assert revenue.json()["outcome_counts"] == {"WON": 1}
        assert revenue.json()["won_amount_cents"] == 125000

        brief = await client.get(f"/api/v1/opportunities/{opportunity_id}/meeting-brief", headers=headers)
        assert brief.status_code == 200
        assert brief.json()["opportunity"]["opportunity_id"] == opportunity_id
        follow_up = await client.post(
            f"/api/v1/opportunities/{opportunity_id}/meeting-follow-ups", headers=headers,
            json={"title": "Send recap", "status": "CONFIRMED"},
        )
        assert follow_up.status_code == 201
        follow_ups = await client.get(f"/api/v1/opportunities/{opportunity_id}/meeting-follow-ups", headers=headers)
        assert follow_ups.status_code == 200
        assert follow_ups.json()[0]["title"] == "Send recap"


async def test_assignment_rejects_unknown_curriculum(executor, monkeypatch):
    workspace_id = f"ws-product-missing-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    async with _client() as client:
        response = await client.post(
            "/api/v1/readiness/assignments", headers=headers,
            json={"curriculum_id": "not-present", "seller_id": "seller"},
        )
    assert response.status_code == 404
