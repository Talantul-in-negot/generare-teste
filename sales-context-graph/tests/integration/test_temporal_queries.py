"""Increment 14 — temporal queries against live Neo4j: the
transaction_from-based recency filter (boundary-inclusive), and the
whats-new Q&A intent end to end.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from api.main import app
from src.domain.assertion import Claim
from src.domain.enums import AdjudicationStatus, Polarity, SpeakerRole
from src.graph.repositories.claim_repository import ClaimRepository
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _claim(workspace_id: str, claim_id: str, transaction_from: datetime, object_value: str = "pricing") -> Claim:
    return Claim(
        claim_id=claim_id, workspace_id=workspace_id, subject_id="spk_1",
        predicate="RAISED_OBJECTION", object_value=object_value, polarity=Polarity.AFFIRMED,
        source_type="transcript", evidence_char_start=0, evidence_char_end=5,
        source_timestamp=_T0, speaker_role=SpeakerRole.BUYER, confidence=0.9,
        valid_from=_T0, transaction_from=transaction_from,
        adjudication_status=AdjudicationStatus.UNREVIEWED, retention_class="standard", created_at=_T0,
    )


async def test_recorded_since_filter_is_boundary_inclusive_and_excludes_earlier(executor):
    workspace_id = f"ws-temporal-{uuid4().hex[:8]}"
    claim_repo = ClaimRepository(executor)
    cutoff = _T0 + timedelta(days=10)

    before = _claim(workspace_id, "claim-before", cutoff - timedelta(days=1), object_value="before")
    at_cutoff = _claim(workspace_id, "claim-at-cutoff", cutoff, object_value="at-cutoff")
    after = _claim(workspace_id, "claim-after", cutoff + timedelta(days=1), object_value="after")
    for c in (before, at_cutoff, after):
        await claim_repo.create_claim(c)

    found = await claim_repo.list_claims_recorded_since(workspace_id, "spk_1", cutoff)
    found_ids = {c.claim_id for c in found}

    assert "claim-before" not in found_ids
    assert "claim-at-cutoff" in found_ids  # inclusive boundary
    assert "claim-after" in found_ids


async def test_whats_new_qa_intent(executor, monkeypatch):
    workspace_id = f"ws-temporal-qa-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    claim_repo = ClaimRepository(executor)
    cutoff = _T0 + timedelta(days=10)

    await claim_repo.create_claim(_claim(workspace_id, "claim-old", cutoff - timedelta(days=5), object_value="old"))
    await claim_repo.create_claim(_claim(workspace_id, "claim-new", cutoff + timedelta(days=1), object_value="new"))

    async with _client() as client:
        resp = await client.post(
            "/api/v1/qa/whats-new", headers=headers,
            json={"subject_id": "spk_1", "since": cutoff.isoformat()},
        )
    assert resp.status_code == 200
    body = resp.json()
    claim_ids = {c["claim_id"] for c in body["claims"]}
    assert "claim-old" not in claim_ids
    assert "claim-new" in claim_ids
