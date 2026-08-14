"""Deterministic, tenant-safe natural-language graph fact queries.

This is intentionally not an LLM-to-Cypher executor. It recognizes a small
allowlist of graph-fact intents and binds values into fixed read-only Cypher
templates. Agents never provide Cypher, labels, relationship syntax or clauses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MAX_QUESTION_LENGTH = 500
_MAX_LIMIT = 100
_TYPE = re.compile(r"^[A-Z][A-Z0-9_]{1,49}$")
_RELATION_VERBS = {
    "supply": "SUPPLIES",
    "supplies": "SUPPLIES",
    "operate": "OPERATES",
    "operates": "OPERATES",
    "report": "REPORTED",
    "reports": "REPORTED",
}
_VERB_QUERY = re.compile(
    r"^(?:what does|show|list)\s+(?P<name>.+?)\s+(?P<verb>supply|supplies|operate|operates|report|reports)\??$",
    re.IGNORECASE,
)
_RELATIONS_QUERY = re.compile(
    r"^(?:show|list|what are)\s+(?:the\s+)?relations?(?:hips)?\s+(?:for|of)\s+(?P<name>.+?)\??$",
    re.IGNORECASE,
)
_TYPE_QUERY = re.compile(
    r"^(?:show|list)\s+(?:all\s+)?(?P<type>[A-Za-z][A-Za-z0-9_ ]*?)(?:\s+entities)?\??$",
    re.IGNORECASE,
)


class ControlledQueryError(ValueError):
    """Raised when a request is not one of the supported safe intents."""


@dataclass(frozen=True)
class ControlledQueryPlan:
    intent: str
    cypher: str
    params: dict[str, object]


_ENTITY_RELATIONS_CYPHER = """
MATCH (s:Entity {tenant: $tenant, name: $name})-[r:RELATES_TO]->(t:Entity {tenant: $tenant})
WHERE ($relation = '' OR r.relation = $relation)
RETURN s.name AS source, s.type AS source_type, r.relation AS relation,
       t.name AS target, t.type AS target_type, r.confidence AS confidence,
       r.source_doc_id AS source_doc_id
ORDER BY confidence DESC, target ASC
LIMIT $limit
"""

_TYPE_ENTITIES_CYPHER = """
MATCH (e:Entity {tenant: $tenant, type: $entity_type})
RETURN e.name AS name, e.type AS type, e.description AS description,
       e.source_doc_id AS source_doc_id
ORDER BY name ASC
LIMIT $limit
"""


def plan_controlled_query(question: str, *, tenant: str, limit: int = 25) -> ControlledQueryPlan:
    """Translate a supported fact question into an immutable query template."""
    normalized = " ".join(question.split())
    if not tenant.strip():
        raise ControlledQueryError("tenant is required")
    if not normalized or len(normalized) > _MAX_QUESTION_LENGTH:
        raise ControlledQueryError("question must contain 1 to 500 characters")
    bounded_limit = max(1, min(int(limit), _MAX_LIMIT))

    match = _VERB_QUERY.fullmatch(normalized)
    if match:
        return ControlledQueryPlan(
            intent="entity_relation",
            cypher=_ENTITY_RELATIONS_CYPHER,
            params={
                "tenant": tenant,
                "name": match.group("name").strip(),
                "relation": _RELATION_VERBS[match.group("verb").lower()],
                "limit": bounded_limit,
            },
        )

    match = _RELATIONS_QUERY.fullmatch(normalized)
    if match:
        return ControlledQueryPlan(
            intent="entity_relations",
            cypher=_ENTITY_RELATIONS_CYPHER,
            params={"tenant": tenant, "name": match.group("name").strip(), "relation": "", "limit": bounded_limit},
        )

    match = _TYPE_QUERY.fullmatch(normalized)
    if match:
        entity_type = match.group("type").strip().upper().replace(" ", "_")
        if _TYPE.fullmatch(entity_type):
            return ControlledQueryPlan(
                intent="entities_by_type",
                cypher=_TYPE_ENTITIES_CYPHER,
                params={"tenant": tenant, "entity_type": entity_type, "limit": bounded_limit},
            )

    raise ControlledQueryError(
        "Unsupported graph-fact question. Use a relation question, for example "
        "'What does Northwind Components supply?', or ask GraphRAG."
    )


async def execute_controlled_query(
    neo4j_client, question: str, *, tenant: str, limit: int = 25,
) -> dict:
    plan = plan_controlled_query(question, tenant=tenant, limit=limit)
    rows = await neo4j_client.run(plan.cypher, **plan.params)
    return {
        "intent": plan.intent,
        "tenant": tenant,
        "rows": rows,
        "count": len(rows),
        "safety": "fixed read-only template; tenant-scoped; parameterized; max 100 rows",
    }


__all__ = ["ControlledQueryError", "ControlledQueryPlan", "execute_controlled_query", "plan_controlled_query"]
