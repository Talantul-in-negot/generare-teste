"""Phase 2 of docs/evaluation.md's full implementation plan — division_id
content-scoping (content_repository.py) and limit/offset pagination on the
21 previously-unbounded repository listing methods (docs/evaluation.md's
Showpad-compatibility analysis: "reads are almost entirely unbounded").

Not exhaustive over all 21 methods — every one uses the identical `ORDER BY
... SKIP $offset LIMIT $limit` shape (template: CrmRepository.list_accounts),
so this covers one representative method per repository file plus the two
methods that also gained a division_id filter, rather than mechanically
re-proving the same Cypher pattern 21 times.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.domain.assertion import Claim
from src.domain.crm import Account, Opportunity
from src.domain.enums import AdjudicationStatus, ErasureStatus, Polarity, SpeakerRole
from src.domain.knowledge import ContentAsset
from src.graph.repositories.claim_repository import ClaimRepository
from src.graph.repositories.content_repository import ContentRepository
from src.graph.repositories.crm_repository import CrmRepository

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _content_asset(workspace_id: str, content_asset_id: str, *, division_id: str | None) -> ContentAsset:
    return ContentAsset(
        content_asset_id=content_asset_id, workspace_id=workspace_id, division_id=division_id,
        title=content_asset_id, url=f"https://example.test/{content_asset_id}",
    )


def _claim(workspace_id: str, claim_id: str, subject_id: str) -> Claim:
    return Claim(
        claim_id=claim_id, workspace_id=workspace_id, subject_id=subject_id,
        predicate="HAS_BLOCKER", object_value="budget", polarity=Polarity.AFFIRMED,
        source_type="transcript", source_record_id=None, source_segment_id=None,
        evidence_char_start=0, evidence_char_end=5, source_timestamp=_T0,
        speaker_id=None, speaker_role=SpeakerRole.BUYER, confidence=0.9,
        valid_from=_T0, valid_to=None, transaction_from=_T0, transaction_to=None,
        is_superseded=False, adjudication_status=AdjudicationStatus.UNREVIEWED,
        retention_class="standard", erasure_status=ErasureStatus.ACTIVE, created_at=_T0,
    )


def _opportunity(workspace_id: str, opportunity_id: str) -> Opportunity:
    return Opportunity(
        opportunity_id=opportunity_id, workspace_id=workspace_id, source_record_id="sr-1",
        account_id="acc-1", seller_id="seller-1", name=opportunity_id, stage="Discovery", is_open=True,
    )


async def test_content_asset_division_id_filter(executor):
    workspace_id = f"ws-div-{uuid4().hex[:8]}"
    repo = ContentRepository(executor)
    await repo.upsert_content_asset(_content_asset(workspace_id, "asset-a", division_id="division-a"))
    await repo.upsert_content_asset(_content_asset(workspace_id, "asset-b", division_id="division-b"))
    await repo.upsert_content_asset(_content_asset(workspace_id, "asset-c", division_id=None))

    # No division_id -- every existing caller's behavior, unchanged: sees all 3.
    all_assets = await repo.list_content_assets(workspace_id)
    assert {a.content_asset_id for a in all_assets} == {"asset-a", "asset-b", "asset-c"}

    # division_id set -- narrows to only that division's assets.
    division_a_only = await repo.list_content_assets(workspace_id, division_id="division-a")
    assert {a.content_asset_id for a in division_a_only} == {"asset-a"}

    # get_content_asset: matching division_id returns the asset...
    got = await repo.get_content_asset(workspace_id, "asset-a", division_id="division-a")
    assert got is not None and got.content_asset_id == "asset-a"

    # ...a mismatched division_id returns None, not the other division's asset.
    got_wrong_division = await repo.get_content_asset(workspace_id, "asset-a", division_id="division-b")
    assert got_wrong_division is None

    # no division_id filter -- unchanged, returns the asset regardless.
    got_unfiltered = await repo.get_content_asset(workspace_id, "asset-a")
    assert got_unfiltered is not None


async def test_content_asset_pagination(executor):
    workspace_id = f"ws-page-content-{uuid4().hex[:8]}"
    repo = ContentRepository(executor)
    ids = [f"asset-{i:03d}" for i in range(5)]
    for content_asset_id in ids:
        await repo.upsert_content_asset(_content_asset(workspace_id, content_asset_id, division_id=None))

    page1 = await repo.list_content_assets(workspace_id, limit=2, offset=0)
    page2 = await repo.list_content_assets(workspace_id, limit=2, offset=2)
    page3 = await repo.list_content_assets(workspace_id, limit=2, offset=4)

    assert [a.content_asset_id for a in page1] == ids[0:2]
    assert [a.content_asset_id for a in page2] == ids[2:4]
    assert [a.content_asset_id for a in page3] == ids[4:5]
    # pages don't overlap and together reconstruct the full set
    seen = {a.content_asset_id for a in page1 + page2 + page3}
    assert seen == set(ids)


async def test_claim_repository_pagination(executor):
    workspace_id = f"ws-page-claim-{uuid4().hex[:8]}"
    repo = ClaimRepository(executor)
    subject_id = "contact-1"
    for i in range(4):
        await repo.create_claim(_claim(workspace_id, f"claim-{i:03d}", subject_id))

    default_page = await repo.list_claims_by_subject(workspace_id, subject_id)
    assert len(default_page) == 4  # default limit (1000) doesn't truncate a small real result

    capped = await repo.list_claims_by_subject(workspace_id, subject_id, limit=2, offset=0)
    next_page = await repo.list_claims_by_subject(workspace_id, subject_id, limit=2, offset=2)
    assert len(capped) == 2
    assert {c.claim_id for c in capped} | {c.claim_id for c in next_page} == {
        f"claim-{i:03d}" for i in range(4)
    }
    assert not ({c.claim_id for c in capped} & {c.claim_id for c in next_page})  # no overlap


async def test_crm_repository_pagination(executor):
    workspace_id = f"ws-page-crm-{uuid4().hex[:8]}"
    repo = CrmRepository(executor)
    for i in range(4):
        await repo.upsert_opportunity(_opportunity(workspace_id, f"opp-{i:03d}"))

    all_open = await repo.list_open_opportunities(workspace_id)
    assert len(all_open) == 4

    capped = await repo.list_open_opportunities(workspace_id, limit=2, offset=0)
    remainder = await repo.list_open_opportunities(workspace_id, limit=2, offset=2)
    assert len(capped) == 2
    assert len(remainder) == 2
    assert {o.opportunity_id for o in capped} | {o.opportunity_id for o in remainder} == {
        f"opp-{i:03d}" for i in range(4)
    }


async def test_find_accounts_by_name_pagination(executor):
    workspace_id = f"ws-page-accounts-{uuid4().hex[:8]}"
    repo = CrmRepository(executor)
    # Two different accounts sharing the exact same name -- the scenario
    # find_accounts_by_name's own docstring calls out as its reason to exist.
    await repo.upsert_account(
        Account(account_id="acc-1", workspace_id=workspace_id, source_record_id="sr-1", name="Acme Corp")
    )
    await repo.upsert_account(
        Account(account_id="acc-2", workspace_id=workspace_id, source_record_id="sr-2", name="Acme Corp")
    )

    both = await repo.find_accounts_by_name(workspace_id, "Acme Corp")
    assert len(both) == 2

    first_only = await repo.find_accounts_by_name(workspace_id, "Acme Corp", limit=1, offset=0)
    assert len(first_only) == 1
