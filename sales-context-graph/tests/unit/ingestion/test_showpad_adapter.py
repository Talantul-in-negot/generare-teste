"""ShowpadAdapter.parse_share/parse_asset_view — pure parsing, no DB."""

from __future__ import annotations

from src.domain.identity import crm_entity_id
from src.ingestion.adapters.showpad import ShowpadAdapter


def test_parse_share_computes_deterministic_id_and_carries_optional_links():
    adapter = ShowpadAdapter()
    raw = {"id": "share-ext-1", "shared_at": "2026-06-15T14:00:00Z"}

    share = adapter.parse_share(
        "ws-1", "asset-1", "contact-1", raw,
        shared_by_seller_id="seller-1", opportunity_id="opp-1", triggered_by_claim_id="claim-1",
    )

    assert share.share_id == crm_entity_id("ws-1", "showpad", "Share", "share-ext-1")
    assert share.content_asset_id == "asset-1"
    assert share.shared_with_contact_id == "contact-1"
    assert share.shared_by_seller_id == "seller-1"
    assert share.opportunity_id == "opp-1"
    assert share.triggered_by_claim_id == "claim-1"


def test_parse_share_optional_links_default_to_none():
    adapter = ShowpadAdapter()
    raw = {"id": "share-ext-2", "shared_at": "2026-06-15T14:00:00Z"}

    share = adapter.parse_share("ws-1", "asset-1", "contact-1", raw)

    assert share.shared_by_seller_id is None
    assert share.opportunity_id is None
    assert share.triggered_by_claim_id is None


def test_parse_asset_view_still_computes_deterministic_id():
    adapter = ShowpadAdapter()
    raw = {"id": "view-ext-1", "viewed_at": "2026-06-15T15:00:00Z"}

    view = adapter.parse_asset_view("ws-1", "asset-1", "contact-1", raw)

    assert view.asset_view_id == crm_entity_id("ws-1", "showpad", "AssetView", "view-ext-1")
    assert view.content_asset_id == "asset-1"
    assert view.viewer_contact_id == "contact-1"
