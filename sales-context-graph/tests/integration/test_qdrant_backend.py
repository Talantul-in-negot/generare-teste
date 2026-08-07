"""Phase 8 (feature-flagged, off by default) — src/embedding/qdrant_backend.py
against a real Qdrant instance (docker compose --profile qdrant up qdrant,
localhost:6335). Skips (not fails) when unreachable, same pattern as
tests/integration/test_kafka_transport.py -- docs/adr-0004-qdrant-
secondary-vector-store.md's own framing: Neo4j-native remains the default;
this is available, not required.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def qdrant(monkeypatch):
    pytest.importorskip("qdrant_client")

    monkeypatch.setenv("QDRANT_URL", "http://localhost:6335")
    from src.core.config import get_settings
    get_settings.cache_clear()

    import src.embedding.qdrant_backend as qb
    client = qb.get_client()
    try:
        await client.get_collections()
    except Exception as exc:
        await client.close()
        pytest.skip(f"no reachable Qdrant at localhost:6335 ({exc}); "
                     f"`docker compose --profile qdrant up -d qdrant` to run this test")

    yield qb, client
    await client.close()
    get_settings.cache_clear()


class _StubProvider:
    model_name = "stub-1536"
    dimension = 1536

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float((hash(t) % 1000) / 1000.0)] * 1536 for t in texts]


async def test_upsert_and_search_round_trip(qdrant):
    qb, client = qdrant
    workspace_id = f"ws-qdrant-{uuid4().hex[:8]}"
    vector = [0.5] * 1536

    await qb.upsert_contact_embedding(client, workspace_id, "contact-1", "Volkswagen Group", vector)

    results = await qb.search_contacts(client, workspace_id, vector, limit=10)

    assert len(results) == 1
    assert results[0]["contact_id"] == "contact-1"
    assert results[0]["name"] == "Volkswagen Group"


async def test_search_is_tenant_filtered_at_the_database_level(qdrant):
    """The actual point of this phase: a workspace's search never returns
    another workspace's points, even when the other workspace's vectors
    are identical/closer -- filtered *during* the HNSW search itself
    (query_filter), not post-hoc in Python."""
    qb, client = qdrant
    workspace_a = f"ws-qdrant-a-{uuid4().hex[:8]}"
    workspace_b = f"ws-qdrant-b-{uuid4().hex[:8]}"
    vector = [0.5] * 1536

    await qb.upsert_contact_embedding(client, workspace_a, "contact-a1", "Alice", vector)
    for i in range(20):
        await qb.upsert_contact_embedding(client, workspace_b, f"contact-b{i}", f"Person {i}", vector)

    results = await qb.search_contacts(client, workspace_a, vector, limit=10)

    assert len(results) == 1
    assert results[0]["contact_id"] == "contact-a1"


async def test_re_upserting_the_same_contact_updates_not_duplicates(qdrant):
    qb, client = qdrant
    workspace_id = f"ws-qdrant-dup-{uuid4().hex[:8]}"
    vector_a = [0.1] * 1536
    vector_b = [0.9] * 1536

    await qb.upsert_contact_embedding(client, workspace_id, "contact-1", "Old Name", vector_a)
    await qb.upsert_contact_embedding(client, workspace_id, "contact-1", "New Name", vector_b)

    results = await qb.search_contacts(client, workspace_id, vector_b, limit=10)

    assert len(results) == 1  # one point, not two -- the second upsert replaced the first
    assert results[0]["name"] == "New Name"


async def test_backfill_workspace_qdrant_populates_and_is_searchable(qdrant, executor):
    from src.domain.crm import Contact
    from src.graph.repositories.crm_repository import CrmRepository

    qb, client = qdrant
    workspace_id = f"ws-qdrant-backfill-{uuid4().hex[:8]}"
    repo = CrmRepository(executor)
    for i in range(3):
        await repo.upsert_contact(Contact(
            contact_id=f"contact-{i:03d}", workspace_id=workspace_id, source_record_id=f"sr-{i}",
            account_id="acc-1", name=f"Person {i}", email=f"person{i}@example.com",
        ))

    embedded = await qb.backfill_workspace_qdrant(workspace_id, executor=executor, provider=_StubProvider())

    assert embedded == 3
    results = await qb.search_contacts(client, workspace_id, [0.5] * 1536, limit=10)
    assert len(results) == 3
