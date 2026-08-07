"""§10 — 'do not obtain a global top-k and merely discard other workspaces
afterward.' Proves the fix to src/resolution/candidates.py::vector_candidates()
(docs/evaluation.md's Showpad-compatibility analysis, item 1).

Before the fix, `db.index.vector.queryNodes('contact_embeddings_v1', $limit,
$embedding)` computed its `numberOfNearestNeighbours` window *before* the
`WHERE node.workspace_id = $workspace_id` filter ran. This test constructs
exactly the scenario that made that a live leak, not just a theoretical one:
one workspace with an overwhelming number of near-identical (cosine ~1.0)
vectors, and a second, unrelated workspace with a handful of real matches
that are meaningfully less similar (cosine ~0.5) but still the closest
things that workspace actually has. Pre-fix, the first workspace's vectors
would fill the entire pre-filter window and the second workspace's own
query would return zero candidates -- not merely "less relevant" results,
an outright false negative caused by another tenant's data. Post-fix, the
over-fetch (`_VECTOR_OVERFETCH_MULTIPLIER`/`_VECTOR_OVERFETCH_FLOOR`) plus
the same tenant WHERE means workspace B's own matches survive regardless of
how much larger or closer workspace A's unrelated vector pool is.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.resolution.candidates import DEFAULT_CAP, CandidateGenerator

_DIM = 1536  # must match schema.py's contact_embeddings_v1 declaration


def _unit_vector_close_to_axis0(magnitude_on_axis0: float) -> list[float]:
    """A vector whose cosine similarity to [1, 0, 0, ...] is driven almost
    entirely by `magnitude_on_axis0` -- everything else held at a small,
    fixed value so results are deterministic across runs."""
    vec = [0.01] * _DIM
    vec[0] = magnitude_on_axis0
    return vec


_QUERY_EMBEDDING = _unit_vector_close_to_axis0(1.0)  # cosine ~1.0 to itself


async def _seed_contacts(executor, *, workspace_id: str, count: int, axis0: float, id_prefix: str) -> list[str]:
    # id_prefix is per-test-run-unique (embeds a uuid4) precisely so this
    # data doesn't collide with, or get counted alongside, another test
    # run's leftovers in this long-lived dev Neo4j instance -- the whole
    # point of this test is that vector_candidates()'s pre-filter window is
    # sensitive to *total* matching-shape data in the index, so accumulated
    # cross-run pollution would silently invalidate it otherwise.
    contact_ids = [f"{id_prefix}-{i}" for i in range(count)]
    rows = [
        {"contact_id": cid, "name": cid, "embedding": _unit_vector_close_to_axis0(axis0)}
        for cid in contact_ids
    ]
    await executor.tenant_query(
        "UNWIND $rows AS row "
        "CREATE (n:Contact {workspace_id: $workspace_id, contact_id: row.contact_id, "
        "name: row.name, embedding: row.embedding})",
        workspace_id=workspace_id, rows=rows,
    )
    return contact_ids


async def _delete_contacts(executor, *, workspace_id: str, id_prefix: str) -> None:
    await executor.tenant_query(
        "MATCH (n:Contact {workspace_id: $workspace_id}) WHERE n.contact_id STARTS WITH $id_prefix DETACH DELETE n",
        workspace_id=workspace_id, id_prefix=id_prefix,
    )


@pytest.mark.asyncio
async def test_a_small_workspace_is_not_crowded_out_by_a_larger_more_similar_one(executor):
    run_id = uuid4().hex[:8]
    workspace_a, workspace_b = f"ws-vec-a-{run_id}", f"ws-vec-b-{run_id}"
    prefix_a, prefix_b = f"a-{run_id}", f"b-{run_id}"
    try:
        # Workspace B: comfortably more vectors than the default cap,
        # essentially identical to the query vector (cosine ~1.0) -- the
        # shape that would fill an *unfiltered* top-k window entirely, but
        # still well under this fix's overfetch window (limit=5 -> 200,
        # see _VECTOR_OVERFETCH_FLOOR) so the fix's own stated guarantee is
        # what's under test here, not an unbounded worst case.
        await _seed_contacts(executor, workspace_id=workspace_b, count=DEFAULT_CAP * 3, axis0=1.0, id_prefix=prefix_b)

        # Workspace A: a handful of real, meaningfully-less-similar (cosine
        # ~0.5) contacts -- its own actual best matches.
        a_ids = await _seed_contacts(executor, workspace_id=workspace_a, count=3, axis0=0.5, id_prefix=prefix_a)

        generator = CandidateGenerator(executor)
        results = await generator.vector_candidates(workspace_a, _QUERY_EMBEDDING, limit=5)

        returned_ids = {c.entity_id for c in results}
        assert returned_ids, "workspace A's own candidates were crowded out by workspace B's unrelated vectors"
        assert returned_ids <= set(a_ids), f"leaked candidates from another workspace: {returned_ids - set(a_ids)}"
        assert returned_ids == set(a_ids)  # all 3 of A's contacts survive; A never had more than `limit`
    finally:
        await _delete_contacts(executor, workspace_id=workspace_a, id_prefix=prefix_a)
        await _delete_contacts(executor, workspace_id=workspace_b, id_prefix=prefix_b)


@pytest.mark.asyncio
async def test_workspace_with_no_vectors_gets_no_candidates_even_when_another_workspace_has_many(executor):
    run_id = uuid4().hex[:8]
    workspace_a, workspace_b = f"ws-vec-empty-{run_id}", f"ws-vec-b-{run_id}"
    prefix_b = f"b-{run_id}"
    try:
        await _seed_contacts(executor, workspace_id=workspace_b, count=DEFAULT_CAP * 3, axis0=1.0, id_prefix=prefix_b)

        generator = CandidateGenerator(executor)
        results = await generator.vector_candidates(workspace_a, _QUERY_EMBEDDING, limit=5)

        assert results == []
    finally:
        await _delete_contacts(executor, workspace_id=workspace_b, id_prefix=prefix_b)
