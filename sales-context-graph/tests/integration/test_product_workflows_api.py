"""Executable vertical slices for the formerly missing product pillars."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from api.main import app
from src.domain.crm import Opportunity
from src.domain.product_workflows import BuyerSpace, BuyerSpaceUpload
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.product_workflow_repository import ProductWorkflowRepository
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
        check = await client.post(
            "/api/v1/readiness/knowledge-checks", headers=headers,
            json={"curriculum_id": curriculum.json()["curriculum_id"], "title": "Discovery quiz"},
        )
        assert check.status_code == 201
        attempt = await client.post(
            f"/api/v1/readiness/knowledge-checks/{check.json()['check_id']}/attempts", headers=headers,
            json={"seller_id": "seller-workflow", "score": 90},
        )
        assert attempt.status_code == 201 and attempt.json()["passed"] is True
        roleplay = await client.post(
            "/api/v1/readiness/roleplays", headers=headers,
            json={"curriculum_id": curriculum.json()["curriculum_id"], "seller_id": "seller-workflow", "scenario": "Discovery call", "transcript": "Seller: What matters most?"},
        )
        assert roleplay.status_code == 201
        scored_roleplay = await client.post(
            f"/api/v1/readiness/roleplays/{roleplay.json()['session_id']}/score", headers=headers,
            json={"score": 88, "feedback": "Good discovery framing.", "passed": True},
        )
        assert scored_roleplay.status_code == 200
        assert scored_roleplay.json()["status"] == "PASSED"
        learning_resource = await client.post(
            "/api/v1/readiness/learning-resources", headers=headers,
            json={
                "curriculum_id": curriculum.json()["curriculum_id"],
                "title": "Discovery playbook", "resource_type": "PLAYBOOK",
                "url": "https://example.test/discovery", "required": True,
            },
        )
        assert learning_resource.status_code == 201
        resources = await client.get(
            f"/api/v1/readiness/curricula/{curriculum.json()['curriculum_id']}/learning-resources",
            headers=headers,
        )
        assert resources.status_code == 200 and resources.json()[0]["required"] is True
        cohort = await client.get(
            f"/api/v1/readiness/cohorts/{curriculum.json()['curriculum_id']}", headers=headers
        )
        assert cohort.status_code == 200
        assert cohort.json()["roleplay_scored"] == 1
        coaching = await client.post(
            "/api/v1/readiness/coaching-reviews", headers=headers,
            json={"seller_id": "seller-workflow", "subject": "Discovery", "note": "Ask one follow-up question."},
        )
        assert coaching.status_code == 201
        certification = await client.post(
            "/api/v1/readiness/certifications", headers=headers,
            json={"curriculum_id": curriculum.json()["curriculum_id"], "seller_id": "seller-workflow"},
        )
        assert certification.status_code == 201

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
        invitation = await client.post(
            f"/api/v1/buyer-spaces/{space_id}/participants", headers=headers,
            json={"email": "buyer@example.test", "role": "EDITOR"},
        )
        assert invitation.status_code == 201
        buyer_token = invitation.json()["buyer_token"]
        accepted = await client.post("/api/v1/buyer-portal/accept", params={"token": buyer_token})
        assert accepted.status_code == 200
        upload = await client.post(
            f"/api/v1/buyer-portal/{space_id}/uploads", params={"token": buyer_token},
            json={"filename": "security-notes.txt", "content_type": "text/plain", "content_text": "SOC2 evidence requested"},
        )
        assert upload.status_code == 201
        portal = await client.get(f"/api/v1/buyer-portal/{space_id}", params={"token": buyer_token})
        assert portal.status_code == 200
        assert portal.json()["uploads"][0]["filename"] == "security-notes.txt"
        detail_after_portal = await client.get(f"/api/v1/buyer-spaces/{space_id}", headers=headers)
        assert detail_after_portal.status_code == 200
        assert detail_after_portal.json()["engagement"][0]["event_type"] in {"PORTAL_VIEW", "UPLOAD"}
        revoked = await client.patch(
            f"/api/v1/buyer-spaces/{space_id}/participants/{invitation.json()['participant']['participant_id']}",
            headers=headers, json={"status": "REVOKED"},
        )
        assert revoked.status_code == 200
        denied_portal = await client.get(f"/api/v1/buyer-portal/{space_id}", params={"token": buyer_token})
        assert denied_portal.status_code == 401

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
        agent = await client.post(
            "/api/v1/agents", headers=headers,
            json={"name": "Meeting assistant", "version": 1, "allowed_actions": ["CREATE_FOLLOW_UP"]},
        )
        assert agent.status_code == 201
        action = await client.post(
            "/api/v1/assistant-actions", headers=headers,
            json={"agent_id": agent.json()["agent_id"], "action_type": "CREATE_FOLLOW_UP", "payload": {"opportunity_id": opportunity_id, "title": "Send approved recap"}},
        )
        assert action.status_code == 201
        approved = await client.post(f"/api/v1/assistant-actions/{action.json()['action_id']}/approve", headers=headers)
        assert approved.status_code == 200
        executed = await client.post(f"/api/v1/assistant-actions/{action.json()['action_id']}/execute", headers=headers)
        assert executed.status_code == 200 and executed.json()["status"] == "EXECUTED"
        hold = await client.post(
            "/api/v1/legal-holds", headers=headers,
            json={"subject_type": "Contact", "subject_id": "contact-on-hold", "reason": "Litigation hold"},
        )
        assert hold.status_code == 201
        blocked_erasure = await client.post(
            "/api/v1/erasure", headers=headers,
            json={"subject_type": "Contact", "subject_id": "contact-on-hold"},
        )
        assert blocked_erasure.status_code == 409
        exported = await client.get("/api/v1/audit-export", headers=headers)
        assert exported.status_code == 200
        assert any(event["action"] == "assistant_action.executed" for event in exported.json()["events"])


async def test_assignment_rejects_unknown_curriculum(executor, monkeypatch):
    workspace_id = f"ws-product-missing-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    async with _client() as client:
        response = await client.post(
            "/api/v1/readiness/assignments", headers=headers,
            json={"curriculum_id": "not-present", "seller_id": "seller"},
        )
    assert response.status_code == 404


async def test_buyer_upload_retention_sweep_respects_legal_hold(executor, monkeypatch):
    workspace_id = f"ws-retention-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    opportunity_id = await _seed_opportunity(executor, workspace_id)
    repo = ProductWorkflowRepository(executor)
    now = datetime.now(timezone.utc)
    space = BuyerSpace(
        space_id=f"space-{uuid4().hex}", workspace_id=workspace_id, opportunity_id=opportunity_id,
        title="Retention room", created_by="seller", created_at=now,
    )
    await repo.upsert_space(space)
    expired = BuyerSpaceUpload(
        upload_id=f"upload-{uuid4().hex}", workspace_id=workspace_id, space_id=space.space_id,
        filename="expired.txt", content_type="text/plain", content_text="delete this",
        retention_until=now - timedelta(days=1), uploaded_by="buyer", uploaded_at=now,
    )
    await repo.add_upload(expired)

    async with _client() as client:
        sweep = await client.post("/api/v1/retention/buyer-uploads/sweep", headers=headers)
        assert sweep.status_code == 200
        assert expired.upload_id in sweep.json()["erased_upload_ids"]

        held = BuyerSpaceUpload(
            upload_id=f"upload-{uuid4().hex}", workspace_id=workspace_id, space_id=space.space_id,
            filename="held.txt", content_type="text/plain", content_text="preserve this",
            retention_until=now - timedelta(days=1), uploaded_by="buyer", uploaded_at=now,
        )
        await repo.add_upload(held)
        hold = await client.post(
            "/api/v1/legal-holds", headers=headers,
            json={"subject_type": "BuyerSpace", "subject_id": space.space_id, "reason": "Preservation"},
        )
        assert hold.status_code == 201
        held_sweep = await client.post("/api/v1/retention/buyer-uploads/sweep", headers=headers)
        assert held_sweep.status_code == 200
        assert held.upload_id in held_sweep.json()["held_upload_ids"]
