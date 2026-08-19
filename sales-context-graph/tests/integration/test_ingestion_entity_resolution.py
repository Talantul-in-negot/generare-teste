"""Entity resolution wired into transcript ingestion, graph-backed.

Covers the flow: transcript -> extraction -> speaker mention -> candidate
generation -> scoring -> decision policy -> Claim carrying resolved identity.

One behaviour worth stating up front, because the tests below assert it
rather than working around it: a *name-only* match can never AUTO_LINK. The
existing policy (src/resolution/policy.py) requires
`min_relational_signals >= 1`, and every relational signal source in
`gather_relational_signals` yields Account candidates, so a Contact resolved
from a display name alone tops out at PENDING_REVIEW by construction. That is
the safe direction and is deliberately not worked around by inventing new
thresholds -- an exact email (deterministic, tier 1) is what auto-links a
person.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.domain.crm import Contact
from src.domain.enums import ResolutionStatus, SpeakerRole
from src.extraction.fixture_provider import FixtureExtractionProvider
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.extraction_run_repository import ExtractionRunRepository
from src.graph.repositories.review_repository import ReviewRepository
from src.graph.repositories.source_repository import SourceRepository
from src.ingestion.adapters.gong import GongAdapter
from src.ingestion.transcript_pipeline import TranscriptIngestionPipeline
from src.resolution.candidates import CandidateGenerator
from src.review.service import ReviewService

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 15, tzinfo=timezone.utc)


def _ws() -> str:
    return f"ws-er-{uuid4().hex[:8]}"


def _pipeline(executor, *, with_resolution: bool = True) -> TranscriptIngestionPipeline:
    """Fixture extraction provider on purpose -- deterministic, no LLM, no key."""
    return TranscriptIngestionPipeline(
        ConversationRepository(executor), SourceRepository(executor), ClaimRepository(executor),
        GongAdapter(), FixtureExtractionProvider(),
        candidate_generator=CandidateGenerator(executor) if with_resolution else None,
        review_repo=ReviewRepository(executor) if with_resolution else None,
    )


def _raw_call(call_id: str, *, speaker_name: str | None, email: str | None) -> dict:
    party: dict = {"speakerId": "spk_1"}
    if speaker_name is not None:
        party["name"] = speaker_name
    if email is not None:
        party["emailAddress"] = email
    return {
        "id": call_id, "started": "2026-06-15T14:00:00Z", "deleted": False,
        "parties": [party],
        "transcript": [
            {"speakerId": "spk_1", "sentences": [
                {"text": "We are concerned about pricing.", "start": 0, "end": 2000},
            ]},
        ],
    }


async def _seed_contact(executor, workspace_id: str, contact_id: str, name: str, email: str | None = None):
    await CrmRepository(executor).upsert_contact(Contact(
        contact_id=contact_id, workspace_id=workspace_id, source_record_id=f"rec-{contact_id}",
        name=name, email=email,
    ))


async def _claims(executor, workspace_id: str, conversation_id: str):
    return await ClaimRepository(executor).list_claims_for_conversation(workspace_id, conversation_id)


# --- tier 1: deterministic email match --------------------------------------

async def test_speaker_label_resolves_to_a_real_entity_id(executor):
    """An exact email match auto-links, and the Claim carries the entity."""
    workspace_id = _ws()
    await _seed_contact(executor, workspace_id, "contact-elena", "Elena Popescu", "elena@vw.com")

    result = await _pipeline(executor).ingest_call(
        workspace_id, _raw_call("call-er-1", speaker_name="Elena Popescu", email="elena@vw.com"),
        ingestion_run_id="run-1", observed_at=_T0,
        email_to_contact_id={"elena@vw.com": "contact-elena"},
    )

    claims = await _claims(executor, workspace_id, result.conversation_id)
    assert claims
    claim = claims[0]
    assert claim.resolved_entity_id == "contact-elena"
    assert claim.resolved_entity_type == "Contact"
    assert claim.resolution_status == ResolutionStatus.AUTO_LINKED
    assert claim.resolution_score == 1.0
    assert claim.speaker_role == SpeakerRole.BUYER


async def test_speaker_label_is_kept_as_provenance_after_resolution(executor):
    """Resolution must not erase where the claim actually came from."""
    workspace_id = _ws()
    await _seed_contact(executor, workspace_id, "contact-elena", "Elena Popescu", "elena@vw.com")

    result = await _pipeline(executor).ingest_call(
        workspace_id, _raw_call("call-er-prov", speaker_name="Elena Popescu", email="elena@vw.com"),
        ingestion_run_id="run-1", observed_at=_T0,
        email_to_contact_id={"elena@vw.com": "contact-elena"},
    )

    claim = (await _claims(executor, workspace_id, result.conversation_id))[0]
    # The opaque transcript label survives on speaker_id even though the claim
    # is now linked to a real Contact, and the evidence span still points at
    # the original segment.
    assert claim.speaker_id == "spk_1"
    assert claim.source_segment_id is not None
    assert claim.evidence_char_end > claim.evidence_char_start
    assert claim.resolved_entity_id == "contact-elena"


# --- tier 2: fuzzy name match -----------------------------------------------

async def test_named_speaker_matching_a_contact_goes_to_pending_review(executor):
    """A name-only match is scored but never auto-linked (no relational signal
    source exists for Contacts), so it lands in the human-review queue."""
    workspace_id = _ws()
    await _seed_contact(executor, workspace_id, "contact-elena", "Elena Popescu")

    result = await _pipeline(executor).ingest_call(
        workspace_id, _raw_call("call-er-2", speaker_name="Elena Popescu", email=None),
        ingestion_run_id="run-1", observed_at=_T0,
    )

    claim = (await _claims(executor, workspace_id, result.conversation_id))[0]
    assert claim.resolution_status == ResolutionStatus.PENDING_REVIEW
    # Safety invariant: an unconfirmed match must not present as a real link.
    assert claim.resolved_entity_id is None
    # subject_id stays the opaque label: it is the join key buying_committee
    # attributes claims by, and what review later rewrites in place.
    assert claim.subject_id == "spk_1"


async def test_pending_review_speaker_is_reachable_by_the_review_service(executor):
    workspace_id = _ws()
    await _seed_contact(executor, workspace_id, "contact-elena", "Elena Popescu")

    await _pipeline(executor).ingest_call(
        workspace_id, _raw_call("call-er-3", speaker_name="Elena Popescu", email=None),
        ingestion_run_id="run-1", observed_at=_T0,
    )

    pending = await ReviewService(ReviewRepository(executor)).list_pending(workspace_id)
    assert len(pending) == 1
    # surface_text is the human-readable name the reviewer judges; the
    # normalized surface is the opaque label claims are keyed by.
    assert pending[0].surface_text == "Elena Popescu"
    assert pending[0].normalized_surface == "spk_1"


async def test_unknown_name_with_no_similar_contact_is_unresolved(executor):
    """Low score -> UNRESOLVED, and the claim still exists."""
    workspace_id = _ws()
    await _seed_contact(executor, workspace_id, "contact-other", "Zbigniew Kowalczyk")

    result = await _pipeline(executor).ingest_call(
        workspace_id, _raw_call("call-er-4", speaker_name="Amelia Fairweather", email=None),
        ingestion_run_id="run-1", observed_at=_T0,
    )

    claim = (await _claims(executor, workspace_id, result.conversation_id))[0]
    assert claim.resolution_status == ResolutionStatus.UNRESOLVED
    assert claim.resolved_entity_id is None


# --- tier 3: nothing to resolve ---------------------------------------------

async def test_opaque_speaker_still_produces_an_unlinked_claim(executor):
    """§15: an opaque speaker must degrade to the label, never drop the Claim."""
    workspace_id = _ws()

    result = await _pipeline(executor).ingest_call(
        workspace_id, _raw_call("call-er-5", speaker_name=None, email=None),
        ingestion_run_id="run-1", observed_at=_T0,
    )

    claim = (await _claims(executor, workspace_id, result.conversation_id))[0]
    assert claim.subject_id == "spk_1"
    assert claim.resolved_entity_id is None
    assert claim.resolution_status is None  # never attempted, not "attempted and failed"


async def test_pipeline_without_a_candidate_generator_behaves_exactly_as_before(executor):
    """Resolution is additive: unwired, the pipeline is byte-for-byte the old one."""
    workspace_id = _ws()
    await _seed_contact(executor, workspace_id, "contact-elena", "Elena Popescu")

    result = await _pipeline(executor, with_resolution=False).ingest_call(
        workspace_id, _raw_call("call-er-6", speaker_name="Elena Popescu", email=None),
        ingestion_run_id="run-1", observed_at=_T0,
    )

    claim = (await _claims(executor, workspace_id, result.conversation_id))[0]
    assert claim.subject_id == "spk_1"
    assert claim.resolution_status is None


# --- idempotency and reconciliation -----------------------------------------

async def test_reingesting_does_not_duplicate_resolved_claims(executor):
    workspace_id = _ws()
    await _seed_contact(executor, workspace_id, "contact-elena", "Elena Popescu", "elena@vw.com")
    pipeline = _pipeline(executor)
    call = _raw_call("call-er-idem", speaker_name="Elena Popescu", email="elena@vw.com")

    first = await pipeline.ingest_call(
        workspace_id, call, ingestion_run_id="run-1", observed_at=_T0,
        email_to_contact_id={"elena@vw.com": "contact-elena"},
    )
    before = await _claims(executor, workspace_id, first.conversation_id)

    await pipeline.ingest_call(
        workspace_id, call, ingestion_run_id="run-2", observed_at=_T0,
        email_to_contact_id={"elena@vw.com": "contact-elena"},
    )
    after = await _claims(executor, workspace_id, first.conversation_id)

    assert len(after) == len(before)
    assert {c.claim_id for c in after} == {c.claim_id for c in before}


async def test_claim_id_is_unaffected_by_a_change_in_resolution_outcome(executor):
    """The stable-key guarantee. CRM data arriving later changes *what the
    claim links to*, never the claim's identity -- otherwise the same sentence
    would exist twice, once per resolution outcome."""
    workspace_id = _ws()
    call = _raw_call("call-er-stable", speaker_name="Elena Popescu", email="elena@vw.com")
    pipeline = _pipeline(executor)

    # First pass: CRM mapping is not known yet.
    unresolved_run = await pipeline.ingest_call(
        workspace_id, call, ingestion_run_id="run-1", observed_at=_T0,
    )
    unresolved_ids = {c.claim_id for c in await _claims(executor, workspace_id, unresolved_run.conversation_id)}

    # Second pass: same transcript, but now the contact is mappable. The
    # content is unchanged, so reconciliation short-circuits -- proving a
    # changed resolution outcome cannot mint a second claim id.
    await _seed_contact(executor, workspace_id, "contact-elena", "Elena Popescu", "elena@vw.com")
    await pipeline.ingest_call(
        workspace_id, call, ingestion_run_id="run-2", observed_at=_T0,
        email_to_contact_id={"elena@vw.com": "contact-elena"},
    )
    after_ids = {c.claim_id for c in await _claims(executor, workspace_id, unresolved_run.conversation_id)}

    assert after_ids == unresolved_ids


async def test_changed_transcript_supersedes_the_source_record(executor):
    """A modified call re-extracts; an unchanged one does not."""
    workspace_id = _ws()
    pipeline = _pipeline(executor)

    first = await pipeline.ingest_call(
        workspace_id, _raw_call("call-er-changed", speaker_name=None, email=None),
        ingestion_run_id="run-1", observed_at=_T0,
    )
    assert first.outcome.value == "CREATED"

    changed = _raw_call("call-er-changed", speaker_name=None, email=None)
    changed["transcript"][0]["sentences"].append(
        {"text": "We also need SOC2 before signing.", "start": 2000, "end": 4000}
    )
    second = await pipeline.ingest_call(
        workspace_id, changed, ingestion_run_id="run-2", observed_at=_T0,
    )

    assert second.outcome.value == "SUPERSEDED"
    assert second.windows_total > 0  # really re-extracted, not skipped


async def test_deleted_call_is_tombstoned_without_extraction(executor):
    workspace_id = _ws()
    pipeline = _pipeline(executor)

    await pipeline.ingest_call(
        workspace_id, _raw_call("call-er-del", speaker_name=None, email=None),
        ingestion_run_id="run-1", observed_at=_T0,
    )
    deleted = _raw_call("call-er-del", speaker_name=None, email=None)
    deleted["deleted"] = True

    result = await pipeline.ingest_call(
        workspace_id, deleted, ingestion_run_id="run-2", observed_at=_T0,
    )

    assert result.outcome.value == "TOMBSTONED"
    assert result.claims_created == 0


# --- human review -----------------------------------------------------------

async def test_reviewer_confirmation_links_ingested_claims_to_the_entity(executor):
    """The full loop: ambiguous at ingest -> reviewer confirms -> claims point
    at the confirmed entity, with no duplicate claims created."""
    workspace_id = _ws()
    await _seed_contact(executor, workspace_id, "contact-elena", "Elena Popescu")
    review_repo = ReviewRepository(executor)
    claim_repo = ClaimRepository(executor)

    result = await _pipeline(executor).ingest_call(
        workspace_id, _raw_call("call-er-review", speaker_name="Elena Popescu", email=None),
        ingestion_run_id="run-1", observed_at=_T0,
    )
    before = await _claims(executor, workspace_id, result.conversation_id)
    assert before[0].subject_id == "spk_1"

    pending = await ReviewService(review_repo, claim_repo).list_pending(workspace_id)
    decision = await ReviewService(review_repo, claim_repo).resolve(
        workspace_id=workspace_id, mention_id=pending[0].mention_id,
        reviewer_id="reviewer@example.com", decided_at=_T0,
        selected_entity_id="contact-elena", rejected=False,
        candidates_shown=["contact-elena"], original_scores={},
    )

    assert decision.selected_entity_id == "contact-elena"
    after = await _claims(executor, workspace_id, result.conversation_id)
    assert len(after) == len(before)  # confirmation reconciles, never duplicates
    assert after[0].subject_id == "contact-elena"


async def test_resolution_logs_carry_no_transcript_text_pii_or_secrets(executor, monkeypatch):
    """Requirement: identifiers and scores only -- never the utterance, the
    speaker's name, or the email that drove the match."""
    import src.ingestion.transcript_pipeline as pipeline_module

    captured: list[tuple[str, dict]] = []

    class _SpyLog:
        def info(self, event, **kw):
            captured.append((event, kw))

        def __getattr__(self, _name):  # warning/error/debug are no-ops here
            return lambda *a, **k: None

    monkeypatch.setattr(pipeline_module, "log", _SpyLog())

    workspace_id = _ws()
    await _seed_contact(executor, workspace_id, "contact-elena", "Elena Popescu", "elena@vw.com")
    await _pipeline(executor).ingest_call(
        workspace_id,
        _raw_call("call-er-logs", speaker_name="Elena Popescu", email="elena@vw.com"),
        ingestion_run_id="run-1", observed_at=_T0,
    )

    resolution_logs = [kw for event, kw in captured if event == "ingestion.speaker_resolution"]
    assert resolution_logs, "expected a resolution log line"

    forbidden = (
        "We are concerned about pricing",  # transcript text
        "elena@vw.com",                    # PII / the matching identifier
        "Elena Popescu",                   # PII: the person's name
    )
    for entry in resolution_logs:
        rendered = repr(entry)
        for secret in forbidden:
            assert secret not in rendered, f"log leaked {secret!r}: {rendered}"
        # ...and the safe fields really are present, so this isn't passing by
        # virtue of logging nothing at all.
        assert entry["speaker_label"] == "spk_1"
        assert "resolution_status" in entry
        assert "candidates" in entry


# --- extraction provenance (P3.2) -------------------------------------------

async def test_claim_carries_the_extraction_run_that_produced_it(executor):
    workspace_id = _ws()
    extraction_run_repo = ExtractionRunRepository(executor)
    pipeline = TranscriptIngestionPipeline(
        ConversationRepository(executor), SourceRepository(executor), ClaimRepository(executor),
        GongAdapter(), FixtureExtractionProvider(),
        extraction_run_repo=extraction_run_repo,
        provider_name="fixture", model_name="fixture-model",
        prompt_version="v1", extractor_version="v1",
    )

    result = await pipeline.ingest_call(
        workspace_id, _raw_call("call-er-provenance", speaker_name=None, email=None),
        ingestion_run_id="run-1", observed_at=_T0,
    )

    claim = (await _claims(executor, workspace_id, result.conversation_id))[0]
    assert claim.extraction_run_id is not None

    run = await extraction_run_repo.get_extraction_run(workspace_id, claim.extraction_run_id)
    assert run is not None
    assert run.provider == "fixture"
    assert run.model == "fixture-model"
    assert run.completed_at is not None  # marked complete after extract() returned


async def test_claim_extraction_run_id_is_none_when_repo_unwired(executor):
    """Additive: a pipeline built without extraction_run_repo behaves exactly
    as before -- Claim.extraction_run_id stays the field's own default."""
    workspace_id = _ws()
    result = await _pipeline(executor, with_resolution=False).ingest_call(
        workspace_id, _raw_call("call-er-no-provenance", speaker_name=None, email=None),
        ingestion_run_id="run-1", observed_at=_T0,
    )

    claim = (await _claims(executor, workspace_id, result.conversation_id))[0]
    assert claim.extraction_run_id is None


# --- rejection suppression (P4.6) --------------------------------------------

async def test_rejected_speaker_candidate_is_not_reproposed_on_reingest(executor):
    """A reviewer rejects every candidate shown for an ambiguous speaker
    mention; re-ingesting the same call must not resolve that speaker back to
    one of the rejected candidates."""
    workspace_id = _ws()
    await _seed_contact(executor, workspace_id, "contact-elena", "Elena Popescu")
    review_repo = ReviewRepository(executor)
    service = ReviewService(review_repo, ClaimRepository(executor))
    pipeline = _pipeline(executor)
    call = _raw_call("call-er-rejected", speaker_name="Elena Popescu", email=None)

    first = await pipeline.ingest_call(
        workspace_id, call, ingestion_run_id="run-1", observed_at=_T0,
    )
    pending = await service.list_pending(workspace_id)
    assert len(pending) == 1
    rejected_mention_id = pending[0].mention_id

    await service.resolve(
        workspace_id=workspace_id, mention_id=rejected_mention_id,
        reviewer_id="reviewer@example.com", decided_at=_T0,
        selected_entity_id=None, rejected=True,
        candidates_shown=["contact-elena"], original_scores={},
        reason="Wrong person -- different Elena.",
    )

    # Re-ingesting the same call re-runs speaker resolution for spk_1 against
    # the same deterministic mention_id. It must not re-resolve to the
    # rejected contact, deterministically or via scoring.
    second_call = _raw_call("call-er-rejected", speaker_name="Elena Popescu", email=None)
    second_call["transcript"][0]["sentences"].append(
        {"text": "Following up on pricing.", "start": 2000, "end": 4000}
    )
    second = await pipeline.ingest_call(
        workspace_id, second_call, ingestion_run_id="run-2", observed_at=_T0,
    )
    assert second.outcome.value == "SUPERSEDED"  # really re-extracted, not skipped

    claims = await _claims(executor, workspace_id, first.conversation_id)
    assert all(c.resolved_entity_id != "contact-elena" for c in claims)

    still_pending = await service.list_pending(workspace_id)
    assert all(m.resolved_entity_id != "contact-elena" for m in still_pending)


async def test_reresolution_after_review_does_not_duplicate_claims(executor):
    workspace_id = _ws()
    await _seed_contact(executor, workspace_id, "contact-elena", "Elena Popescu")
    review_repo = ReviewRepository(executor)
    service = ReviewService(review_repo, ClaimRepository(executor))

    result = await _pipeline(executor).ingest_call(
        workspace_id, _raw_call("call-er-rereview", speaker_name="Elena Popescu", email=None),
        ingestion_run_id="run-1", observed_at=_T0,
    )
    pending = await service.list_pending(workspace_id)
    mention_id = pending[0].mention_id

    for _ in range(2):
        await service.resolve(
            workspace_id=workspace_id, mention_id=mention_id,
            reviewer_id="reviewer@example.com", decided_at=_T0,
            selected_entity_id="contact-elena", rejected=False,
            candidates_shown=["contact-elena"], original_scores={},
        )

    claims = await _claims(executor, workspace_id, result.conversation_id)
    assert len({c.claim_id for c in claims}) == len(claims)
