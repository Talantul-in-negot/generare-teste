"""Qdrant secondary vector backend (Phase 8, feature-flagged, off by
default) -- an alternate write/read target for the same Contact
embeddings src/embedding/backfill.py computes for Neo4j, selected via
VECTOR_BACKEND=qdrant (default: neo4j). Neo4j's native vector index
(contact_embeddings_v1) remains primary -- this exists per explicit
stakeholder direction (docs/adr-0004-qdrant-secondary-vector-store.md),
not because Neo4j-native was found lacking, and it preserves the single-
query multi-hop graph+vector retrieval a separate store would fragment.

Deliberately standalone, not wired into src/resolution/candidates.py's
CandidateGenerator: that module is the security-critical file Phase 1
fixed a real cross-tenant leak in, and adding a second read path into it
for an explicitly-optional, non-default capability would risk that
already-tested, already-correct code for no measured benefit. Call
search_contacts() directly if you want to exercise Qdrant.

Tenant isolation is structural here too, not an afterthought: every point
carries workspace_id in its payload, a payload index exists on that
field, and every search passes a `must` filter on it. Qdrant applies
payload filters *during* HNSW search (this is one of its actual design
advantages over a naive vector index), so this doesn't need Phase 1's
over-fetch-then-truncate workaround -- that fix was specifically for
Neo4j's db.index.vector.queryNodes computing a global top-k before any
filter runs, a limitation Qdrant's filtered search doesn't share.
"""

from __future__ import annotations

import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from src.core.config import get_settings

COLLECTION_NAME = "contact_embeddings_v1"  # mirrors src/graph/schema.py's Neo4j index name

_UUID_NAMESPACE = uuid.UUID("6f6a4d2e-6e1a-4b3a-9b6a-9c1a2e3f4a5b")  # fixed, arbitrary; stable across runs


def get_client() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=get_settings().qdrant_url)


def _point_id(workspace_id: str, contact_id: str) -> str:
    """Qdrant point ids must be an unsigned int or a UUID -- not an
    arbitrary string. Deterministic (uuid5) so re-upserting the same
    contact_id updates the same point rather than creating a duplicate."""
    return str(uuid.uuid5(_UUID_NAMESPACE, f"{workspace_id}:{contact_id}"))


async def ensure_collection(client: AsyncQdrantClient, *, dimension: int) -> None:
    """Idempotent -- same IF NOT EXISTS shape as
    src/graph/migrations/migration_001_init_schema.py."""
    existing = await client.get_collections()
    if COLLECTION_NAME in {c.name for c in existing.collections}:
        return
    await client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=qmodels.VectorParams(size=dimension, distance=qmodels.Distance.COSINE),
    )
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="workspace_id",
        field_schema=qmodels.PayloadSchemaType.KEYWORD,
    )


async def upsert_contact_embedding(
    client: AsyncQdrantClient, workspace_id: str, contact_id: str, name: str, embedding: list[float]
) -> None:
    await ensure_collection(client, dimension=len(embedding))
    await client.upsert(
        collection_name=COLLECTION_NAME,
        points=[qmodels.PointStruct(
            id=_point_id(workspace_id, contact_id),
            vector=embedding,
            payload={"workspace_id": workspace_id, "contact_id": contact_id, "name": name},
        )],
    )


async def search_contacts(
    client: AsyncQdrantClient, workspace_id: str, query_vector: list[float], *, limit: int = 50
) -> list[dict]:
    """Tenant-filtered at the database level via `query_filter` -- applied
    during the search itself, not after (see module docstring)."""
    response = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=qmodels.Filter(
            must=[qmodels.FieldCondition(key="workspace_id", match=qmodels.MatchValue(value=workspace_id))]
        ),
        limit=limit,
    )
    results = []
    for point in response.points:
        # payload is genuinely Optional in Qdrant's own client typing, but
        # every point this module ever writes gets one
        # (upsert_contact_embedding always sets contact_id/name) -- a point
        # with none would mean something outside this module wrote into
        # the collection, worth failing loud on rather than silently
        # dropping.
        assert point.payload is not None, f"Qdrant point {point.id} has no payload"  # noqa: S101 -- data-integrity check on our own writes, not a stripped-under-`-O` security gate
        results.append({"contact_id": point.payload["contact_id"], "name": point.payload["name"], "score": point.score})
    return results


async def delete_contact_embedding(
    client: AsyncQdrantClient, workspace_id: str, contact_id: str
) -> None:
    """Delete one tenant-scoped point during GDPR erasure.

    The deterministic point id makes this operation idempotent.  Qdrant's
    delete call is safe when the point does not exist, which is important for
    retries of an already-completed erasure event.
    """
    await client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=qmodels.PointIdsList(points=[_point_id(workspace_id, contact_id)]),
        wait=True,
    )


async def backfill_workspace_qdrant(workspace_id: str, *, executor=None, provider=None) -> int:
    """Qdrant-equivalent of src/embedding/backfill.py::backfill_workspace --
    same one-workspace-at-a-time, explicit-operator-run shape, same
    injectable `provider` for testability. Independent of the Neo4j
    backfill; run this separately if you want Qdrant populated too."""
    from src.embedding.backfill import _PAGE_SIZE
    from src.embedding.openai_embedding_provider import OpenAIEmbeddingProvider
    from src.graph.execution import GraphExecutor
    from src.graph.repositories.crm_repository import CrmRepository

    provider = provider or OpenAIEmbeddingProvider(api_key=get_settings().embedding_api_key)
    repo = CrmRepository(executor or GraphExecutor())
    client = get_client()

    embedded = 0
    offset = 0
    while True:
        contacts = await repo.list_contacts(workspace_id, limit=_PAGE_SIZE, offset=offset)
        if not contacts:
            break
        vectors = await provider.embed([c.name for c in contacts])
        for contact, vector in zip(contacts, vectors, strict=True):
            await upsert_contact_embedding(client, workspace_id, contact.contact_id, contact.name, vector)
        embedded += len(contacts)
        if len(contacts) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return embedded
