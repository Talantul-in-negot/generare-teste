"""§10 — this repo's own composite/full-text/versioned-vector indexes.

This is NOT a fork of the legacy neo4j_client.py's init_schema() (that encodes
graphrag's own Document/Chunk/Community RAG-ingestion shape, entirely irrelevant
here — see Increment 1's fork-scope notes). Every statement below targets this
repo's own sales-domain labels.

(workspace_id, external_id) from §10's index list is deliberately not created:
every CRM entity id in this repo (account_id, contact_id, ...) is itself
`crm_entity_id(workspace, source_system, object_type, external_id)` — a
deterministic hash of external_id — so looking an entity up by external_id never
needs an index scan; the caller just recomputes the hash and looks up by id
directly (see src/domain/identity.py). Add a denormalized external_id property +
index only if a future phase needs raw-external-id lookup without the
source_system on hand to recompute the hash.
"""

from __future__ import annotations

INDEX_STATEMENTS: list[str] = [
    # Composite identity indexes are supplied by the uniqueness constraints
    # below. Keeping a second non-unique index on the same key prevents Neo4j
    # from creating the constraint and adds no query value.
    # (workspace_id, canonical_name) — candidate-generation prefix lookups (P4).
    "CREATE INDEX account_workspace_name IF NOT EXISTS FOR (n:Account) ON (n.workspace_id, n.name)",
    "CREATE INDEX contact_workspace_name IF NOT EXISTS FOR (n:Contact) ON (n.workspace_id, n.name)",
    # (workspace_id, normalized_email) — Contact repository denormalizes the
    # Pydantic-computed normalized_email onto the node at write time (P4/P2).
    "CREATE INDEX contact_workspace_normalized_email IF NOT EXISTS FOR (n:Contact) ON (n.workspace_id, n.normalized_email)",
    # (workspace_id, resolution_status)
    "CREATE INDEX mention_workspace_resolution_status IF NOT EXISTS FOR (n:Mention) ON (n.workspace_id, n.resolution_status)",
    # (workspace_id, started_at) — this repo's Conversation field is `occurred_at`.
    "CREATE INDEX conversation_workspace_occurred_at IF NOT EXISTS FOR (n:Conversation) ON (n.workspace_id, n.occurred_at)",
    # (workspace_id, adjudication_status, is_superseded)
    "CREATE INDEX claim_workspace_adjudication IF NOT EXISTS FOR (n:Claim) ON (n.workspace_id, n.adjudication_status, n.is_superseded)",
    # Historical identity corrections are queried by subject and transaction
    # interval for point-in-time reconstruction.
    "CREATE INDEX claim_revision_workspace_subject_time IF NOT EXISTS FOR (n:ClaimRevision) ON (n.workspace_id, n.subject_id, n.transaction_from, n.transaction_to)",
    # Six added 2026-08-07 (docs/evaluation.md's Showpad-compatibility
    # analysis, item 5) for repository methods that previously had no
    # supporting index for their MATCH predicate:
    # (workspace_id, subject_id) — ClaimRepository.list_claims_by_subject.
    "CREATE INDEX claim_workspace_subject_id IF NOT EXISTS FOR (n:Claim) ON (n.workspace_id, n.subject_id)",
    # (workspace_id, predicate) — ClaimRepository.list_claims_by_predicate_for_seller
    # and list_claims_by_opportunity_and_predicate.
    "CREATE INDEX claim_workspace_predicate IF NOT EXISTS FOR (n:Claim) ON (n.workspace_id, n.predicate)",
    # (workspace_id, seller_id, is_open) — CrmRepository.list_open_opportunities,
    # the digest use case's own driving query.
    "CREATE INDEX opportunity_workspace_seller_open IF NOT EXISTS FOR (n:Opportunity) ON (n.workspace_id, n.seller_id, n.is_open)",
    # (workspace_id, opportunity_id) — ConversationRepository.list_conversations_by_opportunity.
    "CREATE INDEX conversation_workspace_opportunity_id IF NOT EXISTS FOR (n:Conversation) ON (n.workspace_id, n.opportunity_id)",
    # (workspace_id, opportunity_id) — ContentRepository.list_shares_for_opportunity.
    "CREATE INDEX share_workspace_opportunity_id IF NOT EXISTS FOR (n:Share) ON (n.workspace_id, n.opportunity_id)",
    # (workspace_id, viewer_contact_id) — ContentRepository.list_viewed_asset_ids
    # and list_views_for_asset_and_contact.
    "CREATE INDEX asset_view_workspace_viewer_contact_id IF NOT EXISTS FOR (n:AssetView) ON (n.workspace_id, n.viewer_contact_id)",
    "CREATE INDEX readiness_assignment_workspace_seller IF NOT EXISTS FOR (n:ReadinessAssignment) ON (n.workspace_id, n.seller_id)",
    "CREATE INDEX buyer_space_workspace_opportunity IF NOT EXISTS FOR (n:BuyerSpace) ON (n.workspace_id, n.opportunity_id)",
    "CREATE INDEX revenue_outcome_workspace_recorded IF NOT EXISTS FOR (n:RevenueOutcome) ON (n.workspace_id, n.recorded_at)",
    "CREATE INDEX buyer_participant_workspace_space IF NOT EXISTS FOR (n:BuyerSpaceParticipant) ON (n.workspace_id, n.space_id)",
    "CREATE INDEX notification_workspace_recipient IF NOT EXISTS FOR (n:Notification) ON (n.workspace_id, n.recipient_id)",
    "CREATE INDEX audit_event_workspace_occurred IF NOT EXISTS FOR (n:WorkflowAuditEvent) ON (n.workspace_id, n.occurred_at)",
    "CREATE INDEX content_revision_workspace_asset IF NOT EXISTS FOR (n:ContentAssetRevision) ON (n.workspace_id, n.content_asset_id)",
    "CREATE INDEX review_assignment_workspace_reviewer IF NOT EXISTS FOR (n:ReviewAssignment) ON (n.workspace_id, n.reviewer_id, n.due_at)",
    "CREATE INDEX buyer_engagement_workspace_space IF NOT EXISTS FOR (n:BuyerSpaceEngagement) ON (n.workspace_id, n.space_id, n.occurred_at)",
]

# Existing deployments created non-unique identity indexes before uniqueness
# constraints were introduced. Drop only those exact, redundant indexes before
# creating constraints; Neo4j constraints create their own backing indexes.
LEGACY_IDENTITY_INDEX_DROPS: list[str] = [
    "DROP INDEX account_workspace_id IF EXISTS",
    "DROP INDEX contact_workspace_id IF EXISTS",
    "DROP INDEX opportunity_workspace_id IF EXISTS",
    "DROP INDEX conversation_workspace_id IF EXISTS",
    "DROP INDEX claim_workspace_id IF EXISTS",
    "DROP INDEX mention_workspace_id IF EXISTS",
    "DROP INDEX content_asset_workspace_id IF EXISTS",
]

FULLTEXT_STATEMENTS: list[str] = [
    "CREATE FULLTEXT INDEX account_contact_names IF NOT EXISTS "
    "FOR (n:Account|Contact) ON EACH [n.name]",
]

# Versioned per §10 ("Use versioned vector-index names and support backfill plus
# temporary dual-read during model migration"). Dimension 1536 is a provisional
# placeholder — no embedding provider is pinned yet (pyproject.toml's own open
# item), so this index exists but stays unpopulated until that's decided.
VECTOR_STATEMENTS: list[str] = [
    "CREATE VECTOR INDEX contact_embeddings_v1 IF NOT EXISTS "
    "FOR (n:Contact) ON n.embedding "
    "OPTIONS {indexConfig: {`vector.dimensions`: 1536, `vector.similarity_function`: 'cosine'}}",
]

# Composite uniqueness is the database-level backstop for the application
# identity convention.  Every id is only unique inside a workspace; keeping
# workspace_id in the constraint prevents one customer's deterministic ids
# from colliding with another customer's data and makes accidental duplicate
# MERGE keys fail loudly during ingestion.
CONSTRAINT_STATEMENTS: list[str] = [
    "CREATE CONSTRAINT account_workspace_id_unique IF NOT EXISTS FOR (n:Account) REQUIRE (n.workspace_id, n.account_id) IS UNIQUE",
    "CREATE CONSTRAINT contact_workspace_id_unique IF NOT EXISTS FOR (n:Contact) REQUIRE (n.workspace_id, n.contact_id) IS UNIQUE",
    "CREATE CONSTRAINT lead_workspace_id_unique IF NOT EXISTS FOR (n:Lead) REQUIRE (n.workspace_id, n.lead_id) IS UNIQUE",
    "CREATE CONSTRAINT opportunity_workspace_id_unique IF NOT EXISTS FOR (n:Opportunity) REQUIRE (n.workspace_id, n.opportunity_id) IS UNIQUE",
    "CREATE CONSTRAINT conversation_workspace_id_unique IF NOT EXISTS FOR (n:Conversation) REQUIRE (n.workspace_id, n.conversation_id) IS UNIQUE",
    "CREATE CONSTRAINT segment_workspace_id_unique IF NOT EXISTS FOR (n:TranscriptSegment) REQUIRE (n.workspace_id, n.segment_id) IS UNIQUE",
    "CREATE CONSTRAINT claim_workspace_id_unique IF NOT EXISTS FOR (n:Claim) REQUIRE (n.workspace_id, n.claim_id) IS UNIQUE",
    "CREATE CONSTRAINT mention_workspace_id_unique IF NOT EXISTS FOR (n:Mention) REQUIRE (n.workspace_id, n.mention_id) IS UNIQUE",
    "CREATE CONSTRAINT content_asset_workspace_id_unique IF NOT EXISTS FOR (n:ContentAsset) REQUIRE (n.workspace_id, n.content_asset_id) IS UNIQUE",
    "CREATE CONSTRAINT asset_view_workspace_id_unique IF NOT EXISTS FOR (n:AssetView) REQUIRE (n.workspace_id, n.asset_view_id) IS UNIQUE",
    "CREATE CONSTRAINT share_workspace_id_unique IF NOT EXISTS FOR (n:Share) REQUIRE (n.workspace_id, n.share_id) IS UNIQUE",
    "CREATE CONSTRAINT source_record_workspace_id_unique IF NOT EXISTS FOR (n:SourceRecord) REQUIRE (n.workspace_id, n.source_record_id) IS UNIQUE",
    "CREATE CONSTRAINT claim_revision_workspace_id_unique IF NOT EXISTS FOR (n:ClaimRevision) REQUIRE (n.workspace_id, n.revision_id) IS UNIQUE",
    "CREATE CONSTRAINT curriculum_workspace_id_unique IF NOT EXISTS FOR (n:Curriculum) REQUIRE (n.workspace_id, n.curriculum_id) IS UNIQUE",
    "CREATE CONSTRAINT readiness_assignment_workspace_id_unique IF NOT EXISTS FOR (n:ReadinessAssignment) REQUIRE (n.workspace_id, n.assignment_id) IS UNIQUE",
    "CREATE CONSTRAINT buyer_space_workspace_id_unique IF NOT EXISTS FOR (n:BuyerSpace) REQUIRE (n.workspace_id, n.space_id) IS UNIQUE",
    "CREATE CONSTRAINT buyer_space_step_workspace_id_unique IF NOT EXISTS FOR (n:BuyerSpaceNextStep) REQUIRE (n.workspace_id, n.next_step_id) IS UNIQUE",
    "CREATE CONSTRAINT buyer_space_comment_workspace_id_unique IF NOT EXISTS FOR (n:BuyerSpaceComment) REQUIRE (n.workspace_id, n.comment_id) IS UNIQUE",
    "CREATE CONSTRAINT revenue_outcome_workspace_id_unique IF NOT EXISTS FOR (n:RevenueOutcome) REQUIRE (n.workspace_id, n.outcome_id) IS UNIQUE",
    "CREATE CONSTRAINT meeting_follow_up_workspace_id_unique IF NOT EXISTS FOR (n:MeetingFollowUp) REQUIRE (n.workspace_id, n.follow_up_id) IS UNIQUE",
    "CREATE CONSTRAINT knowledge_check_workspace_id_unique IF NOT EXISTS FOR (n:KnowledgeCheck) REQUIRE (n.workspace_id, n.check_id) IS UNIQUE",
    "CREATE CONSTRAINT knowledge_attempt_workspace_id_unique IF NOT EXISTS FOR (n:KnowledgeCheckAttempt) REQUIRE (n.workspace_id, n.attempt_id) IS UNIQUE",
    "CREATE CONSTRAINT roleplay_workspace_id_unique IF NOT EXISTS FOR (n:RoleplaySession) REQUIRE (n.workspace_id, n.session_id) IS UNIQUE",
    "CREATE CONSTRAINT coaching_review_workspace_id_unique IF NOT EXISTS FOR (n:CoachingReview) REQUIRE (n.workspace_id, n.review_id) IS UNIQUE",
    "CREATE CONSTRAINT certification_workspace_id_unique IF NOT EXISTS FOR (n:Certification) REQUIRE (n.workspace_id, n.certification_id) IS UNIQUE",
    "CREATE CONSTRAINT buyer_participant_workspace_id_unique IF NOT EXISTS FOR (n:BuyerSpaceParticipant) REQUIRE (n.workspace_id, n.participant_id) IS UNIQUE",
    "CREATE CONSTRAINT buyer_upload_workspace_id_unique IF NOT EXISTS FOR (n:BuyerSpaceUpload) REQUIRE (n.workspace_id, n.upload_id) IS UNIQUE",
    "CREATE CONSTRAINT notification_workspace_id_unique IF NOT EXISTS FOR (n:Notification) REQUIRE (n.workspace_id, n.notification_id) IS UNIQUE",
    "CREATE CONSTRAINT agent_definition_workspace_id_unique IF NOT EXISTS FOR (n:AgentDefinition) REQUIRE (n.workspace_id, n.agent_id) IS UNIQUE",
    "CREATE CONSTRAINT assistant_action_workspace_id_unique IF NOT EXISTS FOR (n:AssistantAction) REQUIRE (n.workspace_id, n.action_id) IS UNIQUE",
    "CREATE CONSTRAINT legal_hold_workspace_id_unique IF NOT EXISTS FOR (n:LegalHold) REQUIRE (n.workspace_id, n.hold_id) IS UNIQUE",
    "CREATE CONSTRAINT workflow_audit_workspace_id_unique IF NOT EXISTS FOR (n:WorkflowAuditEvent) REQUIRE (n.workspace_id, n.audit_event_id) IS UNIQUE",
    "CREATE CONSTRAINT content_revision_workspace_id_unique IF NOT EXISTS FOR (n:ContentAssetRevision) REQUIRE (n.workspace_id, n.revision_id) IS UNIQUE",
    "CREATE CONSTRAINT review_assignment_workspace_id_unique IF NOT EXISTS FOR (n:ReviewAssignment) REQUIRE (n.workspace_id, n.assignment_id) IS UNIQUE",
    "CREATE CONSTRAINT learning_resource_workspace_id_unique IF NOT EXISTS FOR (n:LearningResource) REQUIRE (n.workspace_id, n.resource_id) IS UNIQUE",
    "CREATE CONSTRAINT buyer_engagement_workspace_id_unique IF NOT EXISTS FOR (n:BuyerSpaceEngagement) REQUIRE (n.workspace_id, n.engagement_id) IS UNIQUE",
]

ALL_STATEMENTS: list[str] = [
    *LEGACY_IDENTITY_INDEX_DROPS,
    *INDEX_STATEMENTS,
    *FULLTEXT_STATEMENTS,
    *VECTOR_STATEMENTS,
    *CONSTRAINT_STATEMENTS,
]

# Names as reported by `SHOW INDEXES YIELD name` — used by the readiness check.
ALL_INDEX_NAMES: list[str] = [
    "account_workspace_name",
    "contact_workspace_name",
    "contact_workspace_normalized_email",
    "mention_workspace_resolution_status",
    "conversation_workspace_occurred_at",
    "claim_workspace_adjudication",
    "claim_revision_workspace_subject_time",
    "claim_workspace_subject_id",
    "claim_workspace_predicate",
    "opportunity_workspace_seller_open",
    "conversation_workspace_opportunity_id",
    "share_workspace_opportunity_id",
    "asset_view_workspace_viewer_contact_id",
    "readiness_assignment_workspace_seller",
    "buyer_space_workspace_opportunity",
    "revenue_outcome_workspace_recorded",
    "buyer_participant_workspace_space",
    "notification_workspace_recipient",
    "audit_event_workspace_occurred",
    "content_revision_workspace_asset",
    "review_assignment_workspace_reviewer",
    "buyer_engagement_workspace_space",
    "account_contact_names",
    "contact_embeddings_v1",
]

ALL_CONSTRAINT_NAMES: list[str] = [
    "account_workspace_id_unique",
    "contact_workspace_id_unique",
    "lead_workspace_id_unique",
    "opportunity_workspace_id_unique",
    "conversation_workspace_id_unique",
    "segment_workspace_id_unique",
    "claim_workspace_id_unique",
    "mention_workspace_id_unique",
    "content_asset_workspace_id_unique",
    "asset_view_workspace_id_unique",
    "share_workspace_id_unique",
    "source_record_workspace_id_unique",
    "claim_revision_workspace_id_unique",
    "curriculum_workspace_id_unique",
    "readiness_assignment_workspace_id_unique",
    "buyer_space_workspace_id_unique",
    "buyer_space_step_workspace_id_unique",
    "buyer_space_comment_workspace_id_unique",
    "revenue_outcome_workspace_id_unique",
    "meeting_follow_up_workspace_id_unique",
    "knowledge_check_workspace_id_unique",
    "knowledge_attempt_workspace_id_unique",
    "roleplay_workspace_id_unique",
    "coaching_review_workspace_id_unique",
    "certification_workspace_id_unique",
    "buyer_participant_workspace_id_unique",
    "buyer_upload_workspace_id_unique",
    "notification_workspace_id_unique",
    "agent_definition_workspace_id_unique",
    "assistant_action_workspace_id_unique",
    "legal_hold_workspace_id_unique",
    "workflow_audit_workspace_id_unique",
    "content_revision_workspace_id_unique",
    "review_assignment_workspace_id_unique",
    "learning_resource_workspace_id_unique",
    "buyer_engagement_workspace_id_unique",
]
