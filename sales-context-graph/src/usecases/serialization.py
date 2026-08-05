"""JSON-ready shapes for every intent's result.

Extracted in Increment 15 so the HTTP routes (api/routes/qa.py,
api/routes/insights.py) and the natural-language dispatcher
(src/usecases/nlq/dispatch.py) produce byte-identical payloads from one
definition. Before this, each route inlined its own dict literal; adding a
second caller would have meant eleven pairs of hand-kept-in-sync serializers.

Pure functions over already-computed use-case results — no I/O, no repositories.
"""

from __future__ import annotations


def serialize_claim_summaries(items, *, key: str) -> dict:
    """AccountObjections and OpenCommitments share one row shape."""
    return {
        key: [
            {
                "claim_id": i.claim_id, "object_value": i.object_value,
                "evidence_text": i.evidence_text, "speaker_role": i.speaker_role.value,
                "source_timestamp": i.source_timestamp.isoformat(),
            }
            for i in items
        ],
    }


def serialize_account_objections(result) -> dict:
    return {"opportunity_id": result.opportunity_id, **serialize_claim_summaries(result.objections, key="objections")}


def serialize_open_commitments(result) -> dict:
    return {"opportunity_id": result.opportunity_id, **serialize_claim_summaries(result.commitments, key="commitments")}


def serialize_call_briefing(briefing) -> dict:
    return {
        "conversation_id": briefing.conversation_id,
        "subject_id": briefing.subject_id,
        "objections": [c.model_dump(mode="json") for c in briefing.objections],
        "blockers": [c.model_dump(mode="json") for c in briefing.blockers],
        "action_items": [c.model_dump(mode="json") for c in briefing.action_items],
        "other_claims": [c.model_dump(mode="json") for c in briefing.other_claims],
        "unresolved_mention_ids": briefing.unresolved_mention_ids,
        "conflicts": [c.model_dump(mode="json") for c in briefing.conflicts],
        "truncated": briefing.truncated,
    }


def serialize_conflicts(opportunity_id: str, conflicts) -> dict:
    return {
        "opportunity_id": opportunity_id,
        "conflicts": [c.model_dump(mode="json") for c in conflicts],
    }


def serialize_buying_committee(inference) -> dict:
    return {
        "opportunity_id": inference.opportunity_id,
        "distinct_buyer_contact_ids": inference.distinct_buyer_contact_ids,
        "single_threaded": inference.single_threaded,
        "no_resolved_buyer_contacts": inference.no_resolved_buyer_contacts,
        "assignments": [a.model_dump(mode="json") for a in inference.assignments],
    }


def serialize_whats_new(result) -> dict:
    return {
        "subject_id": result.subject_id,
        "since": result.since.isoformat(),
        "claims": [
            {
                "claim_id": c.claim_id, "predicate": c.predicate, "object_value": c.object_value,
                "evidence_text": c.evidence_text, "source_timestamp": c.source_timestamp.isoformat(),
                "transaction_from": c.transaction_from.isoformat(),
            }
            for c in result.claims
        ],
    }


def serialize_recommendation(rec) -> dict:
    return {
        "opportunity_id": rec.opportunity_id,
        "conversation_id": rec.conversation_id,
        "objection_claim_id": rec.objection_claim.claim_id,
        "evidence_text": rec.evidence_text,
        "recommended_asset": rec.recommended_asset.model_dump(mode="json") if rec.recommended_asset else None,
        "ranked_candidates": [
            {"asset": r.asset.model_dump(mode="json"), "matched_tags": r.matched_tags, "rank_score": r.rank_score}
            for r in rec.ranked_candidates
        ],
        "excluded_viewed_asset_ids": rec.excluded_viewed_asset_ids,
        "mapping_source": rec.mapping_source,
        "explanation": rec.explanation,
    }


def serialize_content_effectiveness(report) -> dict:
    return {
        "opportunity_id": report.opportunity_id,
        "shares": [
            {
                "share_id": s.share_id, "content_asset_id": s.content_asset_id,
                "shared_at": s.shared_at.isoformat(), "triggered_by_claim_id": s.triggered_by_claim_id,
                "opened": s.opened, "opened_at": s.opened_at.isoformat() if s.opened_at else None,
                "stage_at_share_time": s.stage_at_share_time, "latest_stage": s.latest_stage,
                "stage_changed_after_share": s.stage_changed_after_share,
            }
            for s in report.shares
        ],
    }


def serialize_as_of(result) -> dict:
    return {
        "subject_id": result.subject_id,
        "as_of": result.as_of.isoformat(),
        "claims": [
            {
                "claim_id": c.claim_id, "predicate": c.predicate, "object_value": c.object_value,
                "evidence_text": c.evidence_text, "source_timestamp": c.source_timestamp.isoformat(),
                "is_superseded": c.is_superseded,
            }
            for c in result.claims
        ],
    }


def serialize_conflict_resolution(resolution) -> dict:
    return {
        "conflict_id": resolution.conflict_id,
        "resolved": resolution.resolved,
        "reason": resolution.reason,
        "winner_claim_id": resolution.winner_claim_id,
        "loser_claim_id": resolution.loser_claim_id,
    }


def serialize_top_objections(report) -> dict:
    return {
        "seller_id": report.seller_id,
        "groups": [
            {"object_value": g.object_value, "count": g.count, "example_claim_ids": g.example_claim_ids}
            for g in report.groups
        ],
    }
