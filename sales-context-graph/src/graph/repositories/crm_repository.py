"""Tenant-safe repository for Account (P1 proof-of-pattern — every other
repository in this package follows this same shape: build patterns via
scoped_match(), never hand-roll a node pattern, always go through
GraphExecutor.tenant_query()).
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.domain.crm import Account, Contact, Lead, Opportunity, OpportunityStageChange
from src.graph.execution import GraphExecutor, scoped_match

_STAGE_CHANGE_RETURN = (
    "chg.opportunity_id AS opportunity_id, chg.workspace_id AS workspace_id, "
    "chg.from_stage AS from_stage, chg.to_stage AS to_stage, chg.changed_at AS changed_at"
)

_ACCOUNT_RETURN = (
    "a.account_id AS account_id, a.workspace_id AS workspace_id, "
    "a.source_record_id AS source_record_id, a.name AS name, a.domain AS domain, "
    "a.merged_into_account_id AS merged_into_account_id"
)

_CONTACT_RETURN = (
    "c.contact_id AS contact_id, c.workspace_id AS workspace_id, "
    "c.source_record_id AS source_record_id, c.account_id AS account_id, "
    "c.name AS name, c.email AS email"
)

_LEAD_RETURN = (
    "l.lead_id AS lead_id, l.workspace_id AS workspace_id, "
    "l.source_record_id AS source_record_id, l.name AS name, l.email AS email, "
    "l.converted_to_type AS converted_to_type, l.converted_to_id AS converted_to_id"
)

_OPPORTUNITY_RETURN = (
    "o.opportunity_id AS opportunity_id, o.workspace_id AS workspace_id, "
    "o.source_record_id AS source_record_id, o.account_id AS account_id, "
    "o.seller_id AS seller_id, o.name AS name, o.stage AS stage, o.is_open AS is_open"
)


class CrmRepository:
    def __init__(self, executor: GraphExecutor | None = None):
        self._executor = executor or GraphExecutor()

    async def upsert_account(self, account: Account) -> None:
        match = scoped_match("Account", "a", account_id="account_id")
        await self._executor.tenant_query(
            f"""
            MERGE {match}
            ON CREATE SET a.created_at = datetime()
            SET a.source_record_id = $source_record_id,
                a.name = $name,
                a.domain = $domain,
                a.merged_into_account_id = $merged_into_account_id,
                a.updated_at = datetime()
            """,
            workspace_id=account.workspace_id,
            account_id=account.account_id,
            source_record_id=account.source_record_id,
            name=account.name,
            domain=account.domain,
            merged_into_account_id=account.merged_into_account_id,
        )

    async def get_account(self, workspace_id: str, account_id: str) -> Account | None:
        match = scoped_match("Account", "a", account_id="account_id")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_ACCOUNT_RETURN}",
            workspace_id=workspace_id,
            account_id=account_id,
        )
        return Account(**rows[0]) if rows else None

    async def find_accounts_by_name(
        self, workspace_id: str, name: str, *, limit: int = 100, offset: int = 0
    ) -> list[Account]:
        """Exact-name lookup, tenant-scoped. Used by the duplicate-exact-names
        isolation fixture — two workspaces may each have an Account named
        identically, and this must never return the other workspace's row."""
        match = scoped_match("Account", "a", name="name")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_ACCOUNT_RETURN} ORDER BY a.account_id SKIP $offset LIMIT $limit",
            workspace_id=workspace_id,
            name=name,
            offset=offset,
            limit=limit,
        )
        return [Account(**row) for row in rows]

    async def list_accounts(self, workspace_id: str, *, limit: int = 100) -> list[Account]:
        match = scoped_match("Account", "a")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_ACCOUNT_RETURN} ORDER BY a.name LIMIT $limit",
            workspace_id=workspace_id,
            limit=limit,
        )
        return [Account(**row) for row in rows]

    async def upsert_contact(self, contact: Contact) -> None:
        match = scoped_match("Contact", "c", contact_id="contact_id")
        await self._executor.tenant_query(
            f"""
            MERGE {match}
            ON CREATE SET c.created_at = datetime()
            SET c.source_record_id = $source_record_id,
                c.account_id = $account_id,
                c.name = $name,
                c.email = $email,
                c.normalized_email = $normalized_email,
                c.updated_at = datetime()
            """,
            workspace_id=contact.workspace_id,
            contact_id=contact.contact_id,
            source_record_id=contact.source_record_id,
            account_id=contact.account_id,
            name=contact.name,
            email=contact.email,
            normalized_email=contact.normalized_email,
        )

    async def get_contact(self, workspace_id: str, contact_id: str) -> Contact | None:
        match = scoped_match("Contact", "c", contact_id="contact_id")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_CONTACT_RETURN}",
            workspace_id=workspace_id,
            contact_id=contact_id,
        )
        return Contact(**rows[0]) if rows else None

    async def upsert_lead(self, lead: Lead) -> None:
        match = scoped_match("Lead", "l", lead_id="lead_id")
        await self._executor.tenant_query(
            f"""
            MERGE {match}
            ON CREATE SET l.created_at = datetime()
            SET l.source_record_id = $source_record_id,
                l.name = $name,
                l.email = $email,
                l.converted_to_type = $converted_to_type,
                l.converted_to_id = $converted_to_id,
                l.updated_at = datetime()
            """,
            workspace_id=lead.workspace_id,
            lead_id=lead.lead_id,
            source_record_id=lead.source_record_id,
            name=lead.name,
            email=lead.email,
            converted_to_type=lead.converted_to_type,
            converted_to_id=lead.converted_to_id,
        )

    async def get_lead(self, workspace_id: str, lead_id: str) -> Lead | None:
        match = scoped_match("Lead", "l", lead_id="lead_id")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_LEAD_RETURN}",
            workspace_id=workspace_id,
            lead_id=lead_id,
        )
        return Lead(**rows[0]) if rows else None

    async def upsert_opportunity(self, opportunity: Opportunity) -> None:
        """Before overwriting `stage`, checks the previously persisted value
        and — if it's genuinely changing — records an append-only
        OpportunityStageChange event first (Increment 10: 'did the deal
        advance after sharing content X' needs the stage at two points in
        time; every prior upsert here silently discarded the old value with
        no history at all). Safe to call unconditionally on every ingest:
        CrmIngestionPipeline only invokes this once reconcile_source_record()
        has already determined the incoming record is genuinely new/changed
        (CREATED/SUPERSEDED outcome) — a retried identical re-ingest never
        reaches this method, so it never records a spurious transition.
        """
        previous = await self.get_opportunity(opportunity.workspace_id, opportunity.opportunity_id)
        if previous is not None and previous.stage != opportunity.stage:
            await self._record_stage_change(
                opportunity.workspace_id, opportunity.opportunity_id,
                from_stage=previous.stage, to_stage=opportunity.stage,
            )

        match = scoped_match("Opportunity", "o", opportunity_id="opportunity_id")
        await self._executor.tenant_query(
            f"""
            MERGE {match}
            ON CREATE SET o.created_at = datetime()
            SET o.source_record_id = $source_record_id,
                o.account_id = $account_id,
                o.seller_id = $seller_id,
                o.name = $name,
                o.stage = $stage,
                o.is_open = $is_open,
                o.updated_at = datetime()
            """,
            workspace_id=opportunity.workspace_id,
            opportunity_id=opportunity.opportunity_id,
            source_record_id=opportunity.source_record_id,
            account_id=opportunity.account_id,
            seller_id=opportunity.seller_id,
            name=opportunity.name,
            stage=opportunity.stage,
            is_open=opportunity.is_open,
        )

    async def _record_stage_change(
        self, workspace_id: str, opportunity_id: str, *, from_stage: str, to_stage: str
    ) -> None:
        # changed_at is computed here in Python and passed as an ISO string
        # parameter, not Cypher's server-side datetime() — every other
        # timestamp field in this codebase's repositories follows that same
        # pattern specifically so reads get back a plain ISO string Pydantic
        # can parse; datetime()-computed properties come back from the driver
        # as neo4j.time.DateTime, which OpportunityStageChange(**row) cannot
        # coerce directly.
        changed_at = datetime.now(timezone.utc).isoformat()
        match = scoped_match("Opportunity", "o", opportunity_id="opportunity_id")
        await self._executor.tenant_query(
            f"""
            MATCH {match}
            CREATE (o)-[:HAS_STAGE_CHANGE]->(chg:OpportunityStageChange {{
                workspace_id: $workspace_id, opportunity_id: $opportunity_id,
                from_stage: $from_stage, to_stage: $to_stage, changed_at: $changed_at
            }})
            """,
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
            from_stage=from_stage,
            to_stage=to_stage,
            changed_at=changed_at,
        )

    async def list_stage_changes(
        self, workspace_id: str, opportunity_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[OpportunityStageChange]:
        match = scoped_match("Opportunity", "o", opportunity_id="opportunity_id")
        rows = await self._executor.tenant_query(
            f"""
            MATCH {match}
            MATCH (o)-[:HAS_STAGE_CHANGE]->(chg:OpportunityStageChange)
            RETURN {_STAGE_CHANGE_RETURN}
            ORDER BY chg.changed_at
            SKIP $offset LIMIT $limit
            """,
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
            offset=offset,
            limit=limit,
        )
        return [OpportunityStageChange(**row) for row in rows]

    async def list_stage_changes_for_opportunities(
        self, workspace_id: str, opportunity_ids: list[str]
    ) -> dict[str, list[OpportunityStageChange]]:
        """Batched sibling of list_stage_changes (Phase 3, docs/evaluation.md's
        digest N+1: DigestUseCase previously fetched stage changes once per
        open opportunity in its outer loop). One round trip for every
        opportunity_id in the given (already-bounded) list, grouped by
        opportunity_id in Python."""
        if not opportunity_ids:
            return {}
        rows = await self._executor.tenant_query(
            f"""
            MATCH (o:Opportunity {{workspace_id: $workspace_id}})
            WHERE o.opportunity_id IN $opportunity_ids
            MATCH (o)-[:HAS_STAGE_CHANGE]->(chg:OpportunityStageChange)
            RETURN {_STAGE_CHANGE_RETURN}
            ORDER BY chg.changed_at
            """,
            workspace_id=workspace_id,
            opportunity_ids=opportunity_ids,
        )
        grouped: dict[str, list[OpportunityStageChange]] = {oid: [] for oid in opportunity_ids}
        for row in rows:
            grouped[row["opportunity_id"]].append(OpportunityStageChange(**row))
        return grouped

    async def list_open_opportunities(
        self, workspace_id: str, *, account_id: str | None = None, seller_id: str | None = None,
        limit: int = 100, offset: int = 0,
    ) -> list[Opportunity]:
        """Open deals, optionally narrowed to one account (Increment 15: mapping
        a company name the seller typed to the deal they meant) or one seller
        (Increment 17: the proactive digest iterates a rep's open pipeline).

        account_id/seller_id are applied as parameterized WHERE clauses rather
        than being folded into scoped_match() — scoped_match builds the
        workspace-scoped node pattern, and adding optional keys to it
        conditionally would make the tenant-scoping guard's input depend on
        caller arguments. The workspace key stays unconditional.
        """
        match = scoped_match("Opportunity", "o")
        filters = ["o.is_open = true"]
        if account_id is not None:
            filters.append("o.account_id = $account_id")
        if seller_id is not None:
            filters.append("o.seller_id = $seller_id")
        rows = await self._executor.tenant_query(
            f"MATCH {match} WHERE {' AND '.join(filters)} RETURN {_OPPORTUNITY_RETURN} "
            "ORDER BY o.name SKIP $offset LIMIT $limit",
            workspace_id=workspace_id,
            account_id=account_id,
            seller_id=seller_id,
            offset=offset,
            limit=limit,
        )
        return [Opportunity(**row) for row in rows]

    async def get_opportunity(self, workspace_id: str, opportunity_id: str) -> Opportunity | None:
        match = scoped_match("Opportunity", "o", opportunity_id="opportunity_id")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_OPPORTUNITY_RETURN}",
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
        )
        return Opportunity(**rows[0]) if rows else None
