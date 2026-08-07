"""§8/§15 — the honest limitation stated in test_blocking_recall.py: at that
test's scale (10 accounts), every candidate trivially fits under cap=50, so
recall measures near-100% "close to vacuous" (its own docstring's words) —
not a real test of blocking quality once a workspace has more entities than
the cap. This file closes that gap with a real, non-labeled-corpus-dependent
measurement: no human annotation is needed to know whether a specific,
deliberately-seeded entity name is or isn't present in the returned
candidate pool — that's a mechanical check, not a judgment call, so it's
honestly measurable without a labeled dataset.

Design: seed 600 Account names (an order of magnitude past DEFAULT_CAP=50)
into one workspace, with 5 "expected" target names deliberately controlled
for position — this is the part that makes the measurement meaningful rather
than another accidental 100%.

Two scenarios are measured, not one, because the fix landed mid-investigation
(src/resolution/candidates.py's union_candidates() gained an optional
`mention_surface` parameter that lexically sorts the merged pool before
capping, and src/resolution/pipeline.py::resolve_mention wires the real
mention text through it):

1. **No mention context** (`union_candidates(pool, cap=CAP)`, no
   `mention_surface`): truncation still follows plain merge/insertion order
   — this is the historical bug, still reachable by any caller that doesn't
   supply mention text, and still measured honestly here rather than only
   testing the now-fixed path.
2. **With mention context** (`mention_surface=<the exact target name>`,
   what `resolve_mention` actually does on every real resolution call): the
   pool is sorted by lexical similarity to the mention before the cap is
   applied, so a near-exact-match candidate survives the cap regardless of
   where in the workspace it was created.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.domain.crm import Account
from src.domain.identity import crm_entity_id
from src.graph.repositories.crm_repository import CrmRepository
from src.resolution.candidates import CandidateGenerator, union_candidates

pytestmark = pytest.mark.asyncio

_POOL_SIZE = 600
_CAP = 50

# 5 expected entities at controlled positions in a 600-name pool:
# indices 0, 1 (created first — best case for insertion-order truncation),
# 598, 599 (created last — worst case), 300 (created in the middle).
_EXPECTED_INDICES = {0, 1, 300, 598, 599}


def _synthetic_name(i: int) -> str:
    # Plausible-looking, deterministic, not drawn from any real company —
    # avoids implying this is real-world labeled data.
    suffixes = ("Corp", "Industries", "Holdings", "Group", "Partners", "Logistics", "Systems")
    return f"Synthetic Vendor {i:04d} {suffixes[i % len(suffixes)]}"


async def test_blocking_recall_at_600_entities_position_dependent(executor):
    workspace_id = f"ws-recall-scale-{uuid4().hex[:8]}"
    crm_repo = CrmRepository(executor)

    for i in range(_POOL_SIZE):
        await crm_repo.upsert_account(Account(
            account_id=crm_entity_id(workspace_id, "salesforce", "Account", f"acc-{i}"),
            workspace_id=workspace_id, source_record_id=f"rec-{i}", name=_synthetic_name(i),
        ))

    generator = CandidateGenerator(executor)
    pool = await generator.all_names_in_workspace(workspace_id, "Account")
    assert len(pool) == _POOL_SIZE  # sanity: every seeded Account is actually retrievable pre-cap

    # --- Scenario 1: no mention context — the historical bug, still real ---
    unordered_capped = union_candidates(pool, cap=_CAP)
    assert len(unordered_capped) == _CAP
    names_in_unordered_pool = {c.name for c in unordered_capped}
    unordered_hits = {idx: _synthetic_name(idx) in names_in_unordered_pool for idx in sorted(_EXPECTED_INDICES)}
    unordered_hit_count = sum(unordered_hits.values())
    early = {i for i in _EXPECTED_INDICES if i < 300}
    late = {i for i in _EXPECTED_INDICES if i >= 300}
    print(
        f"\n[no mention context] blocking_recall@{_CAP} on a {_POOL_SIZE}-entity pool: "
        f"{unordered_hit_count}/{len(_EXPECTED_INDICES)} = {unordered_hit_count / len(_EXPECTED_INDICES):.2f} "
        f"(per-index hits: {unordered_hits})\n"
        f"  early-created (idx<300) recall: {sum(unordered_hits[i] for i in early) / len(early):.2f}; "
        f"mid/late-created (idx>=300) recall: {sum(unordered_hits[i] for i in late) / len(late):.2f}"
    )
    # Document the residual limitation plainly — this is not asserted away.
    # Any caller of union_candidates() that omits mention_surface still hits
    # this. resolve_mention (the only real caller) no longer does, per
    # Scenario 2 below.

    # --- Scenario 2: with mention context — what resolve_mention actually does ---
    ordered_hits: dict[int, bool] = {}
    for idx in sorted(_EXPECTED_INDICES):
        target_name = _synthetic_name(idx)
        ordered_capped = union_candidates(pool, cap=_CAP, mention_surface=target_name)
        ordered_hits[idx] = target_name in {c.name for c in ordered_capped}
    ordered_hit_count = sum(ordered_hits.values())
    print(
        f"[with mention context, as resolve_mention wires it] "
        f"blocking_recall@{_CAP} on a {_POOL_SIZE}-entity pool: "
        f"{ordered_hit_count}/{len(_EXPECTED_INDICES)} = {ordered_hit_count / len(_EXPECTED_INDICES):.2f} "
        f"(per-index hits: {ordered_hits})"
    )

    # The regression guard this test exists to enforce: relevance-ordered
    # candidate generation (Scenario 2) must find every deliberately-seeded
    # target regardless of creation position — the actual fix for the gap
    # this file discovered, verified here, not just claimed in a docstring.
    assert ordered_hit_count == len(_EXPECTED_INDICES)
