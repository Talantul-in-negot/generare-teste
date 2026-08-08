"""Tenant-safe repository for ContentAsset/AssetView — same pattern as
crm_repository.py."""

from __future__ import annotations

from datetime import datetime, timezone

from src.domain.knowledge import AssetView, ContentAsset, Share
from src.graph.execution import GraphExecutor, scoped_match

_ASSET_RETURN = (
    "n.content_asset_id AS content_asset_id, n.workspace_id AS workspace_id, "
    "n.division_id AS division_id, n.title AS title, n.url AS url, "
    "n.content_type AS content_type, n.tags AS tags, "
    "coalesce(n.version, 1) AS version, "
    "coalesce(n.approval_status, 'approved') AS approval_status, "
    "coalesce(n.is_archived, false) AS is_archived, "
    "coalesce(n.is_sensitive, false) AS is_sensitive, "
    "coalesce(n.is_shareable, true) AS is_shareable, "
    "coalesce(n.languages, []) AS languages, coalesce(n.countries, []) AS countries, "
    "coalesce(n.channels, []) AS channels, n.effective_from AS effective_from, "
    "n.expires_at AS expires_at"
)

_VIEW_RETURN = (
    "v.asset_view_id AS asset_view_id, v.workspace_id AS workspace_id, "
    "v.content_asset_id AS content_asset_id, v.viewer_contact_id AS viewer_contact_id, "
    "v.viewed_at AS viewed_at"
)

_SHARE_RETURN = (
    "s.share_id AS share_id, s.workspace_id AS workspace_id, "
    "s.content_asset_id AS content_asset_id, s.shared_with_contact_id AS shared_with_contact_id, "
    "s.shared_by_seller_id AS shared_by_seller_id, s.shared_at AS shared_at, "
    "s.opportunity_id AS opportunity_id, s.triggered_by_claim_id AS triggered_by_claim_id"
)


class ContentRepository:
    def __init__(self, executor: GraphExecutor | None = None):
        self._executor = executor or GraphExecutor()

    async def upsert_content_asset(self, asset: ContentAsset) -> None:
        match = scoped_match("ContentAsset", "n", content_asset_id="content_asset_id")
        await self._executor.tenant_query(
            f"""
            MERGE {match}
            ON CREATE SET n.created_at = datetime()
            SET n.division_id = $division_id,
                n.title = $title,
                n.url = $url,
                n.content_type = $content_type,
                n.tags = $tags,
                n.version = $version,
                n.approval_status = $approval_status,
                n.is_archived = $is_archived,
                n.is_sensitive = $is_sensitive,
                n.is_shareable = $is_shareable,
                n.languages = $languages,
                n.countries = $countries,
                n.channels = $channels,
                n.effective_from = $effective_from,
                n.expires_at = $expires_at,
                n.updated_at = datetime()
            """,
            workspace_id=asset.workspace_id,
            content_asset_id=asset.content_asset_id,
            division_id=asset.division_id,
            title=asset.title,
            url=asset.url,
            content_type=asset.content_type,
            tags=asset.tags,
            version=asset.version,
            approval_status=asset.approval_status,
            is_archived=asset.is_archived,
            is_sensitive=asset.is_sensitive,
            is_shareable=asset.is_shareable,
            languages=asset.languages,
            countries=asset.countries,
            channels=asset.channels,
            effective_from=asset.effective_from.isoformat() if asset.effective_from else None,
            expires_at=asset.expires_at.isoformat() if asset.expires_at else None,
        )

    async def get_content_asset(
        self, workspace_id: str, content_asset_id: str, *, division_id: str | None = None
    ) -> ContentAsset | None:
        """division_id, when given, additionally requires the asset belong to
        that division — a content-scoping filter, not a tenant/security
        boundary (docs/security-and-tenancy.md's "Workspace vs. division"
        section: workspace_id alone is the security boundary). Omitted
        (default) means no division narrowing, matching every existing
        caller's behavior unchanged."""
        match = scoped_match("ContentAsset", "n", content_asset_id="content_asset_id")
        filters = []
        if division_id is not None:
            filters.append("n.division_id = $division_id")
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        rows = await self._executor.tenant_query(
            f"MATCH {match}{where} RETURN {_ASSET_RETURN}",
            workspace_id=workspace_id,
            content_asset_id=content_asset_id,
            division_id=division_id,
        )
        return ContentAsset(**rows[0]) if rows else None

    async def list_content_assets(
        self, workspace_id: str, *, division_id: str | None = None, limit: int = 100, offset: int = 0,
        only_servable: bool = False, as_of: datetime | None = None,
    ) -> list[ContentAsset]:
        """See get_content_asset's docstring for what division_id does and
        does not scope."""
        match = scoped_match("ContentAsset", "n")
        filters = []
        if division_id is not None:
            filters.append("n.division_id = $division_id")
        if only_servable:
            filters.extend([
                "coalesce(n.is_archived, false) = false",
                "coalesce(n.is_sensitive, false) = false",
                "coalesce(n.is_shareable, true) = true",
                "coalesce(n.approval_status, 'approved') = 'approved'",
                "(n.effective_from IS NULL OR n.effective_from <= datetime($as_of))",
                "(n.expires_at IS NULL OR n.expires_at > datetime($as_of))",
            ])
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        effective_as_of = as_of or datetime.now(timezone.utc)
        rows = await self._executor.tenant_query(
            f"MATCH {match}{where} RETURN {_ASSET_RETURN} "
            "ORDER BY n.content_asset_id SKIP $offset LIMIT $limit",
            workspace_id=workspace_id,
            division_id=division_id,
            as_of=effective_as_of.isoformat(),
            offset=offset,
            limit=limit,
        )
        return [ContentAsset(**row) for row in rows]

    async def upsert_asset_view(self, view: AssetView) -> None:
        match = scoped_match("AssetView", "v", asset_view_id="asset_view_id")
        await self._executor.tenant_query(
            f"""
            MERGE {match}
            SET v.content_asset_id = $content_asset_id,
                v.viewer_contact_id = $viewer_contact_id,
                v.viewed_at = $viewed_at
            """,
            workspace_id=view.workspace_id,
            asset_view_id=view.asset_view_id,
            content_asset_id=view.content_asset_id,
            viewer_contact_id=view.viewer_contact_id,
            viewed_at=view.viewed_at.isoformat(),
        )

    async def list_viewed_asset_ids(
        self, workspace_id: str, viewer_contact_id: str, *, limit: int = 100, offset: int = 0
    ) -> set[str]:
        """§12 — 'exclude assets already viewed by that buyer.'"""
        match = scoped_match("AssetView", "v", viewer_contact_id="viewer_contact_id")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN DISTINCT v.content_asset_id AS content_asset_id "
            "ORDER BY v.content_asset_id SKIP $offset LIMIT $limit",
            workspace_id=workspace_id,
            viewer_contact_id=viewer_contact_id,
            offset=offset,
            limit=limit,
        )
        return {row["content_asset_id"] for row in rows}

    async def list_views_for_asset_and_contact(
        self, workspace_id: str, content_asset_id: str, viewer_contact_id: str,
        *, limit: int = 100, offset: int = 0,
    ) -> list[AssetView]:
        """Increment 10 — every view of one asset by one contact, oldest first.
        Used by ContentEffectivenessUseCase to find the first view *after* a
        given Share's shared_at (a view predating the share can't have been
        caused by it)."""
        match = scoped_match(
            "AssetView", "v", content_asset_id="content_asset_id", viewer_contact_id="viewer_contact_id"
        )
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_VIEW_RETURN} ORDER BY v.viewed_at SKIP $offset LIMIT $limit",
            workspace_id=workspace_id,
            content_asset_id=content_asset_id,
            viewer_contact_id=viewer_contact_id,
            offset=offset,
            limit=limit,
        )
        return [AssetView(**row) for row in rows]

    async def upsert_share(self, share: Share) -> None:
        match = scoped_match("Share", "s", share_id="share_id")
        await self._executor.tenant_query(
            f"""
            MERGE {match}
            SET s.content_asset_id = $content_asset_id,
                s.shared_with_contact_id = $shared_with_contact_id,
                s.shared_by_seller_id = $shared_by_seller_id,
                s.shared_at = $shared_at,
                s.opportunity_id = $opportunity_id,
                s.triggered_by_claim_id = $triggered_by_claim_id
            """,
            workspace_id=share.workspace_id,
            share_id=share.share_id,
            content_asset_id=share.content_asset_id,
            shared_with_contact_id=share.shared_with_contact_id,
            shared_by_seller_id=share.shared_by_seller_id,
            shared_at=share.shared_at.isoformat(),
            opportunity_id=share.opportunity_id,
            triggered_by_claim_id=share.triggered_by_claim_id,
        )

    async def list_shares_for_opportunity(
        self, workspace_id: str, opportunity_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[Share]:
        match = scoped_match("Share", "s", opportunity_id="opportunity_id")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN {_SHARE_RETURN} ORDER BY s.shared_at SKIP $offset LIMIT $limit",
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
            offset=offset,
            limit=limit,
        )
        return [Share(**row) for row in rows]

    async def list_shares_for_opportunities(
        self, workspace_id: str, opportunity_ids: list[str]
    ) -> dict[str, list[Share]]:
        """Batched sibling of list_shares_for_opportunity (Phase 3,
        docs/evaluation.md's digest N+1: DigestUseCase previously fetched
        shares once per open opportunity in its outer loop). One round trip
        for every opportunity_id in the given (already-bounded) list,
        grouped by opportunity_id in Python."""
        if not opportunity_ids:
            return {}
        rows = await self._executor.tenant_query(
            f"""
            MATCH (s:Share {{workspace_id: $workspace_id}})
            WHERE s.opportunity_id IN $opportunity_ids
            RETURN {_SHARE_RETURN}
            """,
            workspace_id=workspace_id,
            opportunity_ids=opportunity_ids,
        )
        grouped: dict[str, list[Share]] = {oid: [] for oid in opportunity_ids}
        for row in rows:
            grouped[row["opportunity_id"]].append(Share(**row))
        return grouped
