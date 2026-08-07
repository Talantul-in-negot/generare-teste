"""Phase 7 (docs/evaluation.md's B5 item) — src/embedding/backfill.py
against live Neo4j. Uses a stub EmbeddingProvider (injected, no real
OpenAI call) but every Neo4j read/write is real, including the final
proof: contact_embeddings_v1 (populated here) actually returns the
backfilled contacts via CandidateGenerator.vector_candidates(), the exact
path Phase 1's tenant-isolation fix protects.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.domain.crm import Contact
from src.embedding.backfill import backfill_workspace
from src.graph.repositories.crm_repository import CrmRepository
from src.resolution.candidates import CandidateGenerator

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _cleanup_backfilled_contacts(executor):
    """This file is the only thing in the whole test suite that writes
    Contact.embedding. Without cleanup, every run leaves real vectors
    behind in this long-lived dev Neo4j instance -- and because
    vector_candidates()'s tenant filter runs *after* a global top-k
    pre-filter (src/resolution/candidates.py, Phase 1's fix), enough
    accumulated leftover vectors from earlier runs can crowd a later run's
    own workspace out of its own query results entirely (observed: 240
    leftover embedded Contacts from repeated local runs made
    test_backfilled_embeddings_are_queryable_via_the_vector_index return
    zero results). Scoped tightly to this file's own "ws-backfill*"
    workspace-id prefix so it can never touch unrelated data.
    """
    yield
    await executor.schema_query(
        "MATCH (n:Contact) WHERE n.workspace_id STARTS WITH 'ws-backfill' AND n.embedding IS NOT NULL "
        "DETACH DELETE n"
    )


class _StubProvider:
    model_name = "stub-1536"
    dimension = 1536

    def __init__(self):
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        # Deterministic, distinguishable per-text vector -- not meant to be
        # semantically meaningful, just real 1536-dim vectors that round-trip.
        return [[float((hash(t) % 1000) / 1000.0)] * 1536 for t in texts]


async def _seed_contacts(executor, workspace_id: str, count: int) -> list[str]:
    repo = CrmRepository(executor)
    ids = []
    for i in range(count):
        contact_id = f"contact-{i:03d}"
        ids.append(contact_id)
        await repo.upsert_contact(Contact(
            contact_id=contact_id, workspace_id=workspace_id, source_record_id=f"sr-{i}",
            account_id="acc-1", name=f"Person {i}", email=f"person{i}@example.com",
        ))
    return ids


async def test_backfill_embeds_every_contact_in_one_page(executor):
    workspace_id = f"ws-backfill-{uuid4().hex[:8]}"
    await _seed_contacts(executor, workspace_id, count=5)
    provider = _StubProvider()

    embedded = await backfill_workspace(workspace_id, executor=executor, provider=provider)

    assert embedded == 5
    assert len(provider.calls) == 1  # one page, one batched embed() call -- not one per contact

    rows = await executor.tenant_query(
        "MATCH (c:Contact {workspace_id: $workspace_id}) "
        "WHERE c.embedding IS NOT NULL RETURN count(c) AS n",
        workspace_id=workspace_id,
    )
    assert rows[0]["n"] == 5


async def test_backfill_pages_across_more_than_one_batch(executor):
    """_PAGE_SIZE is 100 -- 105 contacts forces the while-loop in
    backfill_workspace to actually page (two embed() calls: 100 then 5),
    not just handle the single-page case every other test here covers."""
    workspace_id = f"ws-backfill-page-{uuid4().hex[:8]}"
    await _seed_contacts(executor, workspace_id, count=105)
    provider = _StubProvider()

    embedded = await backfill_workspace(workspace_id, executor=executor, provider=provider)

    assert embedded == 105
    assert len(provider.calls) == 2
    assert len(provider.calls[0]) == 100
    assert len(provider.calls[1]) == 5


async def test_backfilled_embeddings_are_queryable_via_the_vector_index(executor):
    """The actual point of this phase: contact_embeddings_v1, populated by
    this backfill, is queryable through the same
    CandidateGenerator.vector_candidates() Phase 1's tenant-isolation fix
    protects."""
    workspace_id = f"ws-backfill-query-{uuid4().hex[:8]}"
    await _seed_contacts(executor, workspace_id, count=3)
    provider = _StubProvider()
    await backfill_workspace(workspace_id, executor=executor, provider=provider)

    generator = CandidateGenerator(executor)
    query_vector = [0.5] * 1536
    results = await generator.vector_candidates(workspace_id, query_vector, limit=10)

    assert len(results) == 3
    assert {c.entity_id for c in results} == {"contact-000", "contact-001", "contact-002"}


async def test_backfill_only_touches_the_given_workspace(executor):
    workspace_a = f"ws-backfill-a-{uuid4().hex[:8]}"
    workspace_b = f"ws-backfill-b-{uuid4().hex[:8]}"
    await _seed_contacts(executor, workspace_a, count=2)
    await _seed_contacts(executor, workspace_b, count=2)
    provider = _StubProvider()

    embedded = await backfill_workspace(workspace_a, executor=executor, provider=provider)

    assert embedded == 2
    rows = await executor.tenant_query(
        "MATCH (c:Contact {workspace_id: $workspace_id}) "
        "WHERE c.embedding IS NOT NULL RETURN count(c) AS n",
        workspace_id=workspace_b,
    )
    assert rows[0]["n"] == 0  # workspace_b untouched
