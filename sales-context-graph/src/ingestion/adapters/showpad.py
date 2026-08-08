"""Showpad-style content adapter (docs/plan.md §4 initial adapters).

Showpad-derived nodes may carry division_id in addition to workspace_id (§4) —
division is the Showpad organizational/permission dimension inside a workspace,
not itself a tenant-isolation boundary.
"""

from __future__ import annotations

from src.domain.identity import crm_entity_id
from src.domain.knowledge import AssetView, ContentAsset, Share
from src.ingestion.adapters.base import ParsedRecord, compute_content_hash


def _as_bool(value: object, default: bool) -> bool:
    """Parse JSON booleans and common CSV/string representations safely."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
    return bool(value)


class ShowpadAdapter:
    source_system = "showpad"
    # This fixture models a point-in-time content export with no explicit
    # deletion/archival flag — unlike Salesforce's IsDeleted, there is nothing
    # trustworthy to tombstone on here (§6).
    supports_deletion_signal = False

    def parse_content_asset(self, workspace_id: str, raw: dict, *, division_id: str | None = None) -> ParsedRecord:
        external_id = raw["id"]
        content_asset_id = crm_entity_id(workspace_id, self.source_system, "ContentAsset", external_id)
        permissions = raw.get("permissions") or {}
        asset = ContentAsset(
            content_asset_id=content_asset_id,
            workspace_id=workspace_id,
            division_id=division_id,
            title=raw["title"],
            url=raw["url"],
            content_type=raw.get("type"),
            tags=list(raw.get("tags", [])),
            version=int(raw.get("version", raw.get("versionNumber", 1))),
            approval_status=str(raw.get("approvalStatus", raw.get("approval_status", "approved"))),
            is_archived=_as_bool(raw.get("isArchived", raw.get("is_archived")), False),
            is_sensitive=_as_bool(raw.get("isSensitive", raw.get("is_sensitive")), False),
            is_shareable=_as_bool(raw.get("isShareable", permissions.get("isShareable")), True),
            languages=list(raw.get("languages", [])),
            countries=list(raw.get("countries", [])),
            channels=list(raw.get("channels", [])),
            effective_from=raw.get("effectiveFrom", raw.get("effective_from")),
            expires_at=raw.get("expiresAt", raw.get("expires_at")),
        )
        return ParsedRecord(
            entity=asset,
            external_id=external_id,
            object_type="ContentAsset",
            content_hash=compute_content_hash(raw),
        )

    def parse_asset_view(
        self, workspace_id: str, content_asset_id: str, viewer_contact_id: str, raw: dict
    ) -> AssetView:
        """AssetViews are append-only engagement events, not versioned source
        records — no reconciliation needed, each raw view maps 1:1 to one
        AssetView node keyed by its own deterministic id."""
        external_id = raw["id"]
        asset_view_id = crm_entity_id(workspace_id, self.source_system, "AssetView", external_id)
        return AssetView(
            asset_view_id=asset_view_id,
            workspace_id=workspace_id,
            content_asset_id=content_asset_id,
            viewer_contact_id=viewer_contact_id,
            viewed_at=raw["viewed_at"],
        )

    def parse_share(
        self, workspace_id: str, content_asset_id: str, shared_with_contact_id: str, raw: dict,
        *, shared_by_seller_id: str | None = None, opportunity_id: str | None = None,
        triggered_by_claim_id: str | None = None,
    ) -> Share:
        """Same append-only-event shape as parse_asset_view — a Share is a
        thing that happened, not a versioned CRM-style record, so no
        reconciliation is needed here either."""
        external_id = raw["id"]
        share_id = crm_entity_id(workspace_id, self.source_system, "Share", external_id)
        return Share(
            share_id=share_id,
            workspace_id=workspace_id,
            content_asset_id=content_asset_id,
            shared_with_contact_id=shared_with_contact_id,
            shared_by_seller_id=shared_by_seller_id,
            shared_at=raw["shared_at"],
            opportunity_id=opportunity_id,
            triggered_by_claim_id=triggered_by_claim_id,
        )
