"""Increment 19 — supersession + true point-in-time queries against live
Neo4j: ClaimRepository.close_claim_interval/list_claims_as_of, the full
ConflictsUseCase.resolve() loop (auto arbitration, manual override, undecided
stays open), and the two new routes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from api.main import app
from src.domain.assertion import Claim, Conflict
from src.domain.enums import AdjudicationStatus, ConflictStatus, ConflictType, Polarity, SpeakerRole
from src.domain.identity import conflict_id
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conflict_repository import ConflictRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.usecases.conflicts import ConflictsUseCase
from src.usecases.qa.as_of import AsOfUseCase
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _claim(
    workspace_id: str, claim_id: str, *, confidence: float = 0.9,
    source_timestamp: datetime = _T0, object_value: str = "pricing",
) -> Claim:
    return Claim(
        claim_id=claim_id, workspace_id=workspace_id, subject_id="spk_1", predicate="RAISED_OBJECTION",
        object_value=object_value, polarity=Polarity.AFFIRMED, source_type="transcript",
        evidence_char_start=0, evidence_char_end=5, source_timestamp=source_timestamp,
        speaker_role=SpeakerRole.BUYER, confidence=confidence, valid_from=source_timestamp,
        transaction_from=source_timestamp, adjudication_status=AdjudicationStatus.UNREVIEWED,
        retention_class="standard", created_at=source_timestamp,
    )


# ── ClaimRepository: close_claim_interval / list_claims_as_of ───────────────

async def test_close_claim_interval_marks_superseded_and_sets_both_fields(executor):
    workspace_id = f"ws-asof-close-{uuid4().hex[:8]}"
    claim_repo = ClaimRepository(executor)
    await claim_repo.create_claim(_claim(workspace_id, "claim-1"))

    valid_to = _T0 + timedelta(days=5)
    transaction_to = _T0 + timedelta(days=6)
    await claim_repo.close_claim_interval(workspace_id, "claim-1", valid_to=valid_to, transaction_to=transaction_to)

    reloaded = await claim_repo.get_claim(workspace_id, "claim-1")
    assert reloaded.is_superseded is True
    assert reloaded.valid_to == valid_to
    assert reloaded.transaction_to == transaction_to


async def test_as_of_boundary_is_correctly_inclusive_and_exclusive(executor):
    """The defining test for this increment: a claim closed at T2 is visible
    at T1 (before closure) and invisible at T3 (after closure) — this is the
    exact scenario the prior 'Known measurement gap' said would silently
    return wrong answers before this wiring existed."""
    workspace_id = f"ws-asof-boundary-{uuid4().hex[:8]}"
    claim_repo = ClaimRepository(executor)
    t1 = _T0
    t2 = _T0 + timedelta(days=10)
    t3 = _T0 + timedelta(days=20)

    await claim_repo.create_claim(_claim(workspace_id, "claim-open", source_timestamp=t1))
    await claim_repo.create_claim(_claim(workspace_id, "claim-closed", source_timestamp=t1))
    await claim_repo.close_claim_interval(workspace_id, "claim-closed", valid_to=t2, transaction_to=t2)

    as_of_t1 = {c.claim_id for c in await claim_repo.list_claims_as_of(workspace_id, "spk_1", t1)}
    as_of_between = {c.claim_id for c in await claim_repo.list_claims_as_of(workspace_id, "spk_1", t1 + timedelta(days=5))}
    as_of_t2 = {c.claim_id for c in await claim_repo.list_claims_as_of(workspace_id, "spk_1", t2)}
    as_of_t3 = {c.claim_id for c in await claim_repo.list_claims_as_of(workspace_id, "spk_1", t3)}

    assert as_of_t1 == {"claim-open", "claim-closed"}
    assert as_of_between == {"claim-open", "claim-closed"}
    assert as_of_t2 == {"claim-open"}, "transaction_to is exclusive at the exact closure instant"
    assert as_of_t3 == {"claim-open"}


async def test_never_superseded_claims_appear_at_every_as_of(executor):
    """Regression guard: a Claim that was never closed must not accidentally
    disappear at some future as_of due to an off-by-something in the filter."""
    workspace_id = f"ws-asof-neversup-{uuid4().hex[:8]}"
    claim_repo = ClaimRepository(executor)
    await claim_repo.create_claim(_claim(workspace_id, "claim-forever", source_timestamp=_T0))

    far_future = _T0 + timedelta(days=3650)
    found = await claim_repo.list_claims_as_of(workspace_id, "spk_1", far_future)
    assert {c.claim_id for c in found} == {"claim-forever"}


# ── ConflictsUseCase.resolve ─────────────────────────────────────────────────

async def _seed_conflict(executor, workspace_id: str, claim_a: Claim, claim_b: Claim) -> str:
    claim_repo = ClaimRepository(executor)
    conflict_repo = ConflictRepository(executor)
    await claim_repo.create_claim(claim_a)
    await claim_repo.create_claim(claim_b)

    cid = conflict_id(workspace_id, claim_a.claim_id, claim_b.claim_id, ConflictType.CONTRADICTORY_CLAIM.value)
    await conflict_repo.create_conflict(Conflict(
        conflict_id=cid, workspace_id=workspace_id, claim_id_a=claim_a.claim_id, claim_id_b=claim_b.claim_id,
        conflict_type=ConflictType.CONTRADICTORY_CLAIM, status=ConflictStatus.OPEN, detected_at=_T0,
    ))
    return cid


async def test_resolve_auto_arbitrates_by_confidence_and_closes_the_loser(executor):
    workspace_id = f"ws-asof-resolve-{uuid4().hex[:8]}"
    strong = _claim(workspace_id, "claim-strong", confidence=0.9, object_value="pricing")
    weak = _claim(workspace_id, "claim-weak", confidence=0.4, object_value="security")
    cid = await _seed_conflict(executor, workspace_id, strong, weak)

    usecase = ConflictsUseCase(ClaimRepository(executor), ConflictRepository(executor))
    resolution = await usecase.resolve(workspace_id, cid)

    assert resolution.resolved is True
    assert resolution.winner_claim_id == "claim-strong"
    assert resolution.loser_claim_id == "claim-weak"

    loser = await ClaimRepository(executor).get_claim(workspace_id, "claim-weak")
    assert loser.is_superseded is True
    winner = await ClaimRepository(executor).get_claim(workspace_id, "claim-strong")
    assert winner.is_superseded is False

    conflict = await ConflictRepository(executor).get_conflict(workspace_id, cid)
    assert conflict.status == ConflictStatus.RESOLVED


async def test_resolve_undecided_leaves_the_conflict_open(executor):
    workspace_id = f"ws-asof-undecided-{uuid4().hex[:8]}"
    a = _claim(workspace_id, "claim-tie-a", confidence=0.7, object_value="pricing")
    b = _claim(workspace_id, "claim-tie-b", confidence=0.7, object_value="security")
    cid = await _seed_conflict(executor, workspace_id, a, b)

    usecase = ConflictsUseCase(ClaimRepository(executor), ConflictRepository(executor))
    resolution = await usecase.resolve(workspace_id, cid)

    assert resolution.resolved is False
    assert resolution.winner_claim_id is None

    conflict = await ConflictRepository(executor).get_conflict(workspace_id, cid)
    assert conflict.status == ConflictStatus.OPEN
    a_reloaded = await ClaimRepository(executor).get_claim(workspace_id, "claim-tie-a")
    assert a_reloaded.is_superseded is False


async def test_resolve_with_explicit_manual_winner_overrides_arbitration(executor):
    """The auto path would pick claim-strong (higher confidence) — the manual
    override must still be honored even though it disagrees."""
    workspace_id = f"ws-asof-manual-{uuid4().hex[:8]}"
    strong = _claim(workspace_id, "claim-strong2", confidence=0.9, object_value="pricing")
    weak = _claim(workspace_id, "claim-weak2", confidence=0.4, object_value="security")
    cid = await _seed_conflict(executor, workspace_id, strong, weak)

    usecase = ConflictsUseCase(ClaimRepository(executor), ConflictRepository(executor))
    resolution = await usecase.resolve(workspace_id, cid, winner_claim_id="claim-weak2")

    assert resolution.winner_claim_id == "claim-weak2"
    assert resolution.loser_claim_id == "claim-strong2"
    loser = await ClaimRepository(executor).get_claim(workspace_id, "claim-strong2")
    assert loser.is_superseded is True


async def test_resolve_rejects_a_winner_not_part_of_the_conflict(executor):
    workspace_id = f"ws-asof-badwinner-{uuid4().hex[:8]}"
    a = _claim(workspace_id, "claim-a3", object_value="pricing")
    b = _claim(workspace_id, "claim-b3", object_value="security")
    cid = await _seed_conflict(executor, workspace_id, a, b)

    usecase = ConflictsUseCase(ClaimRepository(executor), ConflictRepository(executor))
    with pytest.raises(ValueError, match="is not one of this conflict's claims"):
        await usecase.resolve(workspace_id, cid, winner_claim_id="claim-nonexistent")


async def test_resolve_unknown_conflict_id_raises(executor):
    usecase = ConflictsUseCase(ClaimRepository(executor), ConflictRepository(executor))
    with pytest.raises(ValueError, match="no conflict"):
        await usecase.resolve(f"ws-asof-{uuid4().hex[:8]}", "does-not-exist")


# ── end-to-end: resolve then as-of ──────────────────────────────────────────

async def test_as_of_reflects_a_real_conflict_resolution_end_to_end(executor):
    workspace_id = f"ws-asof-e2e-{uuid4().hex[:8]}"
    t1 = _T0
    strong = _claim(workspace_id, "claim-strong3", confidence=0.9, source_timestamp=t1, object_value="pricing")
    weak = _claim(workspace_id, "claim-weak3", confidence=0.4, source_timestamp=t1, object_value="security")
    cid = await _seed_conflict(executor, workspace_id, strong, weak)

    conflicts_usecase = ConflictsUseCase(ClaimRepository(executor), ConflictRepository(executor))
    resolution = await conflicts_usecase.resolve(workspace_id, cid)
    assert resolution.resolved is True

    as_of_usecase = AsOfUseCase(ClaimRepository(executor), ConversationRepository(executor))
    before = await as_of_usecase.as_of(workspace_id, "spk_1", t1)
    after = await as_of_usecase.as_of(workspace_id, "spk_1", datetime.now(timezone.utc) + timedelta(days=1))

    assert {c.claim_id for c in before.claims} == {"claim-strong3", "claim-weak3"}
    assert {c.claim_id for c in after.claims} == {"claim-strong3"}


# ── routes ────────────────────────────────────────────────────────────────────

async def test_resolve_route_auto_arbitration(executor, monkeypatch):
    workspace_id = f"ws-asof-route-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    strong = _claim(workspace_id, "claim-r-strong", confidence=0.9, object_value="pricing")
    weak = _claim(workspace_id, "claim-r-weak", confidence=0.4, object_value="security")
    cid = await _seed_conflict(executor, workspace_id, strong, weak)

    async with _client() as client:
        resp = await client.post(
            f"/api/v1/opportunities/opp-x/conflicts/{cid}/resolve", headers=headers, json={},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved"] is True
    assert body["winner_claim_id"] == "claim-r-strong"


async def test_resolve_route_unknown_conflict_is_404(executor, monkeypatch):
    workspace_id = f"ws-asof-404-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)

    async with _client() as client:
        resp = await client.post(
            "/api/v1/opportunities/opp-x/conflicts/does-not-exist/resolve", headers=headers, json={},
        )
    assert resp.status_code == 404


async def test_qa_as_of_route(executor, monkeypatch):
    workspace_id = f"ws-asof-qa-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    claim_repo = ClaimRepository(executor)
    t1 = _T0
    t2 = _T0 + timedelta(days=10)
    await claim_repo.create_claim(_claim(workspace_id, "claim-qa-open", source_timestamp=t1))
    await claim_repo.create_claim(_claim(workspace_id, "claim-qa-closed", source_timestamp=t1))
    await claim_repo.close_claim_interval(workspace_id, "claim-qa-closed", valid_to=t2, transaction_to=t2)

    async with _client() as client:
        resp = await client.post(
            "/api/v1/qa/as-of", headers=headers,
            json={"subject_id": "spk_1", "as_of": (t2 + timedelta(days=1)).isoformat()},
        )
    assert resp.status_code == 200
    claim_ids = {c["claim_id"] for c in resp.json()["claims"]}
    assert claim_ids == {"claim-qa-open"}
