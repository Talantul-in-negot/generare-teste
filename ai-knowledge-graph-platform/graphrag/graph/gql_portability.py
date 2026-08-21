"""Read-only query contracts shared between Cypher and ISO GQL deployments.

This module deliberately does not expose raw database queries to callers.  It
documents the small, bounded graph-read surface that can be verified against a
GQL implementation during a portability evaluation while production remains
on parameterised Cypher.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReadQueryContract:
    name: str
    parameters: tuple[str, ...]
    cypher: str
    gql: str


READ_QUERY_CONTRACTS: tuple[ReadQueryContract, ...] = (
    ReadQueryContract(
        name="entity_by_id",
        parameters=("tenant", "entity_id"),
        cypher=(
            "MATCH (e:Entity {tenant: $tenant, id: $entity_id}) "
            "RETURN e.id AS id, e.name AS name, e.type AS type LIMIT 1"
        ),
        gql=(
            "MATCH (e:Entity {tenant: $tenant, id: $entity_id}) "
            "RETURN e.id AS id, e.name AS name, e.type AS type LIMIT 1"
        ),
    ),
    ReadQueryContract(
        name="bounded_neighbors",
        parameters=("tenant", "entity_id", "limit"),
        cypher=(
            "MATCH (e:Entity {tenant: $tenant, id: $entity_id})-"
            "[r:RELATES_TO {tenant: $tenant}]-(n:Entity {tenant: $tenant}) "
            "RETURN n.id AS id, n.name AS name, r.relation AS relation "
            "ORDER BY coalesce(r.confidence, 0) DESC LIMIT $limit"
        ),
        gql=(
            "MATCH (e:Entity {tenant: $tenant, id: $entity_id})-"
            "[r:RELATES_TO {tenant: $tenant}]-(n:Entity {tenant: $tenant}) "
            "RETURN n.id AS id, n.name AS name, r.relation AS relation "
            "ORDER BY coalesce(r.confidence, 0) DESC LIMIT $limit"
        ),
    ),
)

_WRITE_KEYWORDS = ("CREATE", "MERGE", "SET", "DELETE", "REMOVE", "DROP", "LOAD", "CALL")


def validate_read_contracts(
    contracts: tuple[ReadQueryContract, ...] = READ_QUERY_CONTRACTS,
) -> list[str]:
    """Return validation errors; an empty list means safe portability scope."""
    errors: list[str] = []
    names: set[str] = set()
    for contract in contracts:
        if contract.name in names:
            errors.append(f"duplicate contract name: {contract.name}")
        names.add(contract.name)
        for language, query in (("cypher", contract.cypher), ("gql", contract.gql)):
            upper = query.upper()
            if "LIMIT" not in upper:
                errors.append(f"{contract.name}/{language} must bound results")
            for keyword in _WRITE_KEYWORDS:
                if keyword in upper:
                    errors.append(f"{contract.name}/{language} contains forbidden {keyword}")
            for parameter in contract.parameters:
                if f"${parameter}" not in query:
                    errors.append(f"{contract.name}/{language} does not use ${parameter}")
    return errors
