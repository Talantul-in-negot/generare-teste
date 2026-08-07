"""§8 candidate generation — tenant-safe prefix/fulltext/vector/relational
candidate sources, unioned and deduplicated, capped.

'Trigram or fuzzy-capable name lookup' is implemented as a tenant-scoped fetch
of the full name pool (all_names_in_workspace) scored in Python via RapidFuzz
(src/resolution/scoring.py) rather than a DB-native trigram index — acceptable
at this vertical slice's data scale; swap for a real trigram/APOC index if
workspace entity counts ever make the full fetch too slow, without changing
this module's return shape.

Full-text and vector candidates use GraphExecutor.tenant_query()'s WHERE-
equality scoping form (node.workspace_id = $workspace_id inside the query,
before ORDER BY/LIMIT) — §10: 'do not obtain a global top-k and merely discard
other workspaces afterward.'
"""

from __future__ import annotations

from dataclasses import dataclass

from src.graph.execution import GraphExecutor, scoped_match

DEFAULT_CAP = 50

_ID_FIELD = {"Account": "account_id", "Contact": "contact_id"}

# vector_candidates() over-fetch multiplier -- see the docstring on that
# method for why this exists. 20x plus a 200-row floor is generous enough
# that, at this vertical slice's data scale, a workspace's own top-`limit`
# matches are essentially never crowded out of the pre-filter window by
# other tenants; it is not a scale-proof fix (see docs/evaluation.md's B5
# note: this whole path stays behind reranker_enabled=False and an
# unpopulated index until a real workspace-partitioned or pre-filtered
# vector query is available).
_VECTOR_OVERFETCH_MULTIPLIER = 20
_VECTOR_OVERFETCH_FLOOR = 200


@dataclass(frozen=True)
class Candidate:
    entity_id: str
    entity_type: str
    name: str
    sources: frozenset[str]


class CandidateGenerator:
    def __init__(self, executor: GraphExecutor):
        self._executor = executor

    async def exact_name_candidates(self, workspace_id: str, entity_type: str, name: str) -> list[Candidate]:
        id_field = _ID_FIELD[entity_type]
        match = scoped_match(entity_type, "n", name="name")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN n.{id_field} AS entity_id, n.name AS name",
            workspace_id=workspace_id, name=name,
        )
        return [
            Candidate(entity_id=r["entity_id"], entity_type=entity_type, name=r["name"], sources=frozenset({"exact_name"}))
            for r in rows
        ]

    async def all_names_in_workspace(self, workspace_id: str, entity_type: str) -> list[Candidate]:
        id_field = _ID_FIELD[entity_type]
        match = scoped_match(entity_type, "n")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN n.{id_field} AS entity_id, n.name AS name",
            workspace_id=workspace_id,
        )
        return [
            Candidate(entity_id=r["entity_id"], entity_type=entity_type, name=r["name"], sources=frozenset({"fuzzy_pool"}))
            for r in rows
        ]

    async def fulltext_candidates(self, workspace_id: str, query_text: str, *, limit: int = DEFAULT_CAP) -> list[Candidate]:
        rows = await self._executor.tenant_query(
            "CALL db.index.fulltext.queryNodes('account_contact_names', $query_text) YIELD node, score "
            "WHERE node.workspace_id = $workspace_id "
            "RETURN coalesce(node.account_id, node.contact_id) AS entity_id, "
            "labels(node) AS labels, node.name AS name, score "
            "ORDER BY score DESC LIMIT $limit",
            workspace_id=workspace_id, query_text=query_text, limit=limit,
        )
        candidates = []
        for row in rows:
            entity_type = "Account" if "Account" in row["labels"] else "Contact"
            candidates.append(Candidate(
                entity_id=row["entity_id"], entity_type=entity_type, name=row["name"], sources=frozenset({"fulltext"})
            ))
        return candidates

    async def vector_candidates(
        self, workspace_id: str, embedding: list[float], *, limit: int = DEFAULT_CAP
    ) -> list[Candidate]:
        """Tenant-isolation fix (docs/evaluation.md's Showpad-compatibility
        analysis, item 1): `db.index.vector.queryNodes`'s second argument is
        `numberOfNearestNeighbours`, computed by Neo4j *before* any WHERE
        clause runs -- passing `limit` directly there, as this method used
        to, meant the procedure could return another tenant's closer
        matches, fill the entire top-`limit` window with them, and leave
        this workspace's own candidates filtered out to zero rows, not
        merely reordered. That is a live cross-tenant leak once the index
        is populated, unlike fulltext_candidates() above, whose `LIMIT`
        already runs after the tenant `WHERE`.

        Fix: ask the procedure for a much larger candidate pool
        (`_VECTOR_OVERFETCH_MULTIPLIER`x `limit`, floor
        `_VECTOR_OVERFETCH_FLOOR`), apply the same tenant `WHERE` as before,
        then truncate to `limit` in Python -- the tenant filter now narrows
        a pool large enough that this workspace's real matches survive it.
        See tests/security/test_vector_candidates_tenant_isolation.py.
        """
        overfetch = max(limit * _VECTOR_OVERFETCH_MULTIPLIER, _VECTOR_OVERFETCH_FLOOR)
        rows = await self._executor.tenant_query(
            "CALL db.index.vector.queryNodes('contact_embeddings_v1', $overfetch, $embedding) YIELD node, score "
            "WHERE node.workspace_id = $workspace_id "
            "RETURN node.contact_id AS entity_id, node.name AS name, score "
            "ORDER BY score DESC LIMIT $limit",
            workspace_id=workspace_id, embedding=embedding, overfetch=overfetch, limit=limit,
        )
        return [
            Candidate(entity_id=r["entity_id"], entity_type="Contact", name=r["name"], sources=frozenset({"vector"}))
            for r in rows
        ]

    async def relational_candidates_via_conversation_account(
        self, workspace_id: str, conversation_id: str
    ) -> list[Candidate]:
        """A known participant in this call belongs to candidate Account — the
        conversation's own linked account, if any (§8 relational signal 1)."""
        match = scoped_match("Conversation", "c", conversation_id="conversation_id")
        rows = await self._executor.tenant_query(
            f"""
            MATCH {match}
            MATCH (acc:Account {{workspace_id: $workspace_id, account_id: c.account_id}})
            RETURN acc.account_id AS entity_id, acc.name AS name
            """,
            workspace_id=workspace_id, conversation_id=conversation_id,
        )
        return [
            Candidate(entity_id=r["entity_id"], entity_type="Account", name=r["name"],
                      sources=frozenset({"relational:participant_account"}))
            for r in rows
        ]

    async def relational_candidates_via_domain_match(
        self, workspace_id: str, email_domain: str
    ) -> list[Candidate]:
        """Participant email domain agrees with the Account domain (§8 relational
        signal 4) — 'Email-domain equality is not deterministic. It is
        relational evidence only', and per §16 it must never be sufficient
        alone for auto-link (enforced in policy.py's decide(), not here — this
        only ever contributes one signal among several)."""
        match = scoped_match("Account", "n", domain="domain")
        rows = await self._executor.tenant_query(
            f"MATCH {match} RETURN n.account_id AS entity_id, n.name AS name",
            workspace_id=workspace_id, domain=email_domain,
        )
        return [
            Candidate(entity_id=r["entity_id"], entity_type="Account", name=r["name"],
                      sources=frozenset({"relational:domain_match"}))
            for r in rows
        ]

    async def relational_candidates_via_seller_open_opportunity(
        self, workspace_id: str, seller_id: str
    ) -> list[Candidate]:
        """A seller owns an open Opportunity for candidate Account (§8 relational
        signal 2)."""
        match = scoped_match("Opportunity", "o", seller_id="seller_id")
        rows = await self._executor.tenant_query(
            f"""
            MATCH {match}
            WHERE o.is_open = true
            MATCH (acc:Account {{workspace_id: $workspace_id, account_id: o.account_id}})
            RETURN DISTINCT acc.account_id AS entity_id, acc.name AS name
            """,
            workspace_id=workspace_id, seller_id=seller_id,
        )
        return [
            Candidate(entity_id=r["entity_id"], entity_type="Account", name=r["name"],
                      sources=frozenset({"relational:seller_open_opportunity"}))
            for r in rows
        ]


def union_candidates(
    *candidate_lists: list[Candidate], cap: int = DEFAULT_CAP, mention_surface: str | None = None
) -> list[Candidate]:
    """Merge and cap.

    `mention_surface`, when given, orders the merged pool by lexical
    similarity to it before truncating to `cap` — see
    tests/eval/test_blocking_recall_at_scale.py's measured finding: without
    this, truncation follows whatever order the union happened to build in
    (effectively Neo4j's unordered MATCH return order for the
    all_names_in_workspace pool, which correlates with entity creation
    order for MERGE-created nodes), so an entity created after roughly the
    first `cap` accounts in a workspace could be entirely invisible to
    candidate generation regardless of how close a lexical match it is —
    measured at blocking_recall@50=0.00 for such entities on a 600-entity
    pool before this fix. Callers with no mention context (none currently)
    keep the old insertion-order behavior by omitting this argument — it
    does not change candidate *scoring* (src/resolution/scoring.py runs
    afterward on whatever survives this cap), only which candidates survive
    the cap to be scored at all.
    """
    merged: dict[str, Candidate] = {}
    for candidates in candidate_lists:
        for c in candidates:
            if c.entity_id in merged:
                merged[c.entity_id] = Candidate(
                    entity_id=c.entity_id, entity_type=c.entity_type, name=c.name,
                    sources=merged[c.entity_id].sources | c.sources,
                )
            else:
                merged[c.entity_id] = c
    ordered = list(merged.values())
    if mention_surface is not None:
        from src.resolution.scoring import lexical_score  # local: avoid import at module load for callers that never sort

        ordered.sort(key=lambda c: lexical_score(mention_surface, c.name), reverse=True)
    return ordered[:cap]
