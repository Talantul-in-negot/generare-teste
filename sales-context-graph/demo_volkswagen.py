#!/usr/bin/env python
"""End-to-end demo: entity resolution (P4) + Context Graph recommendation (P4.5).

Seeds workspace "ws-demo" (same default as /viz and WORKSPACE_API_KEYS in
.env.example — override with DEMO_WORKSPACE_ID for an isolated run) with the
Volkswagen fixture (Volkswagen Group, Volkswagen Financial Services
distractor, a "Volks Wagen" transcript mention, a seller-owned open
Opportunity, an affirmed pricing objection, and two ContentAssets — one
already viewed), then:

1. Resolves "Volks Wagen" via src/resolution/pipeline.py, using a real local
   embedding provider (src/embedding/sentence_transformer_provider.py — no
   API key, no network call beyond the one-time model download) for the
   semantic score, printing every candidate considered, each one's component
   scores (lexical/semantic/base/rel_bonus/final), the named relational
   signals that fired, the top-1/top-2 margin, and the final
   AUTO_LINKED/PENDING_REVIEW/UNRESOLVED status.
2. Runs the objection-to-content recommendation use case (§12) and prints the
   recommended (unviewed) asset with its exact transcript evidence and Claim id.

Requires docker-compose's neo4j service running (`docker compose up -d neo4j`;
see docker-compose.yml's neo4j service comment for the non-default host port).

Usage:
    python demo_volkswagen.py
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

from src.core.neo4j_client import Neo4jClient
from src.embedding.sentence_transformer_provider import SentenceTransformerEmbeddingProvider
from src.domain.identity import crm_entity_id, mention_id, segment_id
from src.domain.conversation import Mention
from src.domain.knowledge import AssetView, ContentAsset
from src.extraction.fixture_provider import FixtureExtractionProvider
from src.graph.execution import GraphExecutor
from src.graph.migrations.migration_001_init_schema import run as run_migration
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.conversation_repository import ConversationRepository
from src.graph.repositories.crm_repository import CrmRepository
from src.graph.repositories.source_repository import SourceRepository
from src.ingestion.adapters.gong import GongAdapter
from src.ingestion.adapters.salesforce import SalesforceAdapter
from src.ingestion.adapters.showpad import ShowpadAdapter
from src.ingestion.pipeline import CrmIngestionPipeline
from src.ingestion.transcript_pipeline import TranscriptIngestionPipeline
from src.resolution.candidates import CandidateGenerator
from src.resolution.pipeline import gather_relational_signals, resolve_mention
from src.usecases.objection_content_recommendation import ObjectionContentRecommendationUseCase

_T0 = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
_SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "data", "sample")


def _sample(name: str) -> dict:
    with open(os.path.join(_SAMPLE_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


def _hr(title: str) -> None:
    # Plain ASCII only — Windows consoles without UTF-8 configured (cp1252)
    # mangle em-dashes and other non-ASCII characters in print() output.
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


async def main() -> None:
    client = Neo4jClient()
    executor = GraphExecutor(client)
    await run_migration(executor)

    # Fixed default ("ws-demo") so this lines up with /viz's pre-filled
    # Workspace field and WORKSPACE_API_KEYS' default entry — no separate key
    # to add, no workspace to retype in the UI. Every write below is a MERGE
    # keyed off deterministic ids (crm_entity_id/mention_id/segment_id), so
    # rerunning against the same workspace_id is a no-op on unchanged fields,
    # not a collision (§6 identity, §11 idempotency) — override via
    # DEMO_WORKSPACE_ID if you want an isolated one instead.
    workspace_id = os.environ.get("DEMO_WORKSPACE_ID", "ws-demo")
    print(f"workspace_id = {workspace_id}")
    crm_sample = _sample("salesforce_accounts.json")
    gong_sample = _sample("gong_call.json")
    showpad_sample = _sample("showpad_content.json")

    crm_repo = CrmRepository(executor)
    conv_repo = ConversationRepository(executor)
    claim_repo = ClaimRepository(executor)
    content_repo = ContentRepository(executor)
    source_repo = SourceRepository(executor)
    candidate_generator = CandidateGenerator(executor)

    # ── Seed: Volkswagen Group + distractor, seller-owned open Opportunity ──
    crm_pipeline = CrmIngestionPipeline(crm_repo, source_repo, SalesforceAdapter())
    await crm_pipeline.ingest_accounts(
        workspace_id,
        crm_sample["accounts"],
        ingestion_run_id="demo-run", observed_at=_T0,
    )
    vw_group_id = crm_entity_id(workspace_id, "salesforce", "Account", "001VWGROUP")
    vw_financial_id = crm_entity_id(workspace_id, "salesforce", "Account", "001VWFIN")

    await crm_pipeline.ingest_contacts(
        workspace_id,
        crm_sample["contacts"],
        ingestion_run_id="demo-run", observed_at=_T0,
    )
    elena_id = crm_entity_id(workspace_id, "salesforce", "Contact", "003ELENA")

    await crm_pipeline.ingest_opportunities(
        workspace_id,
        crm_sample["opportunities"],
        ingestion_run_id="demo-run", observed_at=_T0,
    )
    opportunity_id = crm_entity_id(workspace_id, "salesforce", "Opportunity", "006VWDEAL")
    # Opportunity.seller_id is stored as the canonical hashed id (see
    # src/ingestion/adapters/salesforce.py::parse_opportunity) — relational
    # signal lookups must match on that, never the raw external OwnerId.
    seller_id = crm_entity_id(workspace_id, "salesforce", "Seller", "005SAM")

    # ── Transcript: "Volks Wagen" mention + affirmed pricing objection ──
    transcript_pipeline = TranscriptIngestionPipeline(
        conv_repo, source_repo, claim_repo, GongAdapter(), FixtureExtractionProvider()
    )
    raw_call = gong_sample["calls"][0]
    """legacy inline fixture retained below for readable demo docs
    raw_call = {
        "id": "call-vw-demo", "started": "2026-06-15T14:00:00Z", "deleted": False,
        "parties": [
            {"speakerId": "spk_1", "name": "Elena Popescu", "emailAddress": "elena.popescu@vw.com"},
            {"speakerId": "spk_2", "name": "Sam Seller", "emailAddress": "sam@ourcompany.com"},
        ],
        "transcript": [
            {"speakerId": "spk_1", "sentences": [
                {"text": "This is Volks Wagen calling, and we are concerned about pricing this quarter.", "start": 0, "end": 4000},
            ]},
            {"speakerId": "spk_2", "sentences": [
                {"text": "Understood — let's review the numbers together.", "start": 4000, "end": 7000},
            ]},
        ],
    }
    """
    transcript_result = await transcript_pipeline.ingest_call(
        workspace_id, raw_call, ingestion_run_id="demo-run", observed_at=_T0,
        opportunity_id=opportunity_id, account_id=vw_group_id,
        email_to_contact_id={"elena.popescu@vw.com": elena_id},
        email_to_seller_id={"sam@ourcompany.com": "005SAM"},
    )
    conversation_id = transcript_result.conversation_id
    # Keep presenter-facing identifiers visible in the terminal output. The
    # /viz Ask and Browse Intents tabs deliberately accept canonical IDs as
    # optional context; printing them makes the live path repeatable without
    # guessing from a fuzzy name.
    print(f"conversation_id = {conversation_id}")
    print(f"opportunity_id = {opportunity_id}")
    print(f"buyer_contact_id = {elena_id}")
    print(f"seller_id = {seller_id}")

    # ── Content: two assets addressing "pricing", one already viewed ──
    showpad_adapter = ShowpadAdapter()
    parsed_assets = [
        showpad_adapter.parse_content_asset(workspace_id, raw, division_id=showpad_sample.get("division_id"))
        for raw in showpad_sample["content_assets"]
    ]
    for parsed in parsed_assets:
        await content_repo.upsert_content_asset(parsed.entity)
    viewed_asset = next(parsed.entity for parsed in parsed_assets if parsed.external_id == "asset-pricing-guide")
    unviewed_asset = next(parsed.entity for parsed in parsed_assets if parsed.external_id == "asset-roi-calculator")
    await content_repo.upsert_asset_view(AssetView(
        asset_view_id="view-demo-1", workspace_id=workspace_id,
        content_asset_id=viewed_asset.content_asset_id, viewer_contact_id=elena_id, viewed_at=_T0,
    ))

    # ══════════════════════════ Part 1: entity resolution ══════════════════════════
    _hr("PART 1 - Entity resolution: 'Volks Wagen'")

    seg_id = segment_id(conversation_id, 0)
    mention = Mention(
        mention_id=mention_id(seg_id, 15, 26, "volks wagen", "ORG"),
        workspace_id=workspace_id, segment_id=seg_id, char_start=15, char_end=26,
        surface_text="Volks Wagen", normalized_surface="volks wagen", entity_type="ORG",
    )

    signals = await gather_relational_signals(
        candidate_generator, workspace_id=workspace_id,
        conversation_id=conversation_id, seller_id=seller_id, participant_email_domain="vw.com",
    )

    print("\nLoading local embedding model (all-MiniLM-L6-v2, ~80MB, one-time download if not cached)...")
    embedding_provider = SentenceTransformerEmbeddingProvider()

    outcome = await resolve_mention(
        workspace_id=workspace_id, mention=mention, entity_type="Account",
        candidate_generator=candidate_generator, decided_at=_T0,
        relational_signals_by_entity=signals, embedding_provider=embedding_provider,
    )

    print(f"\nMention: {mention.surface_text!r} (mention_id={mention.mention_id[:16]}...)")
    print(f"Candidates shown ({len(outcome.candidates_shown)}):")
    names_by_id = {vw_group_id: "Volkswagen Group", vw_financial_id: "Volkswagen Financial Services"}
    for entity_id in outcome.candidates_shown:
        label = names_by_id.get(entity_id, entity_id[:16] + "...")
        marker = " <-- resolved" if entity_id == outcome.decision.resolved_entity_id else ""
        print(f"  - {label} ({entity_id[:16]}...){marker}")

    print("\nTop candidate component scores:")
    print(f"  lexical         = {outcome.decision.lexical_score}")
    print(f"  semantic        = {outcome.decision.semantic_score}")
    print(f"  base            = {outcome.decision.base_score}")
    print(f"  relational_bonus= {outcome.decision.relational_bonus}")
    print(f"  final           = {outcome.decision.final_score}")
    print(f"  margin (top1-top2) = {outcome.decision.margin}")
    print(f"  relational_signals  = {outcome.decision.relational_signals}")
    print(f"\n>>> STATUS: {outcome.decision.status.value}")
    if outcome.decision.resolved_entity_id:
        print(f">>> Resolved to: {names_by_id.get(outcome.decision.resolved_entity_id)}")

    # ══════════════════════════ Part 2: content recommendation ══════════════════════════
    _hr("PART 2 - Objection-to-content recommendation")

    use_case = ObjectionContentRecommendationUseCase(conv_repo, claim_repo, content_repo)
    recommendation = await use_case.recommend(workspace_id, opportunity_id, elena_id)

    print(f"\nObjection Claim: {recommendation.objection_claim.claim_id}")
    print(f"  predicate = {recommendation.objection_claim.predicate}")
    print(f"  object    = {recommendation.objection_claim.object_value}")
    print(f"  evidence  = {recommendation.evidence_text!r}")
    print(f"  speaker_role = {recommendation.objection_claim.speaker_role.value}")

    print(f"\nMapping source: {recommendation.mapping_source}")
    print(f"Excluded (already viewed): {recommendation.excluded_viewed_asset_ids}")
    print("\nRanked candidates:")
    for ranked in recommendation.ranked_candidates:
        print(f"  - {ranked.asset.title} (score={ranked.rank_score}, tags={ranked.matched_tags})")

    print(f"\n>>> RECOMMENDED ASSET: {recommendation.recommended_asset.title if recommendation.recommended_asset else None}")
    print(f">>> Explanation: {recommendation.explanation}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
