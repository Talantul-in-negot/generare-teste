"""Runtime validation for the sales ontology (config/ontologies/sales.yml):
Claim predicates (the evidence-assertion vocabulary) and relation_rules (the
graph-*edge* vocabulary — a separate axis, see that file's header comment).

The YAML is the source of truth for both. Keeping this module small and
dependency-free (no Neo4j import) makes validate_claim_predicate usable by
extractors that shouldn't need a DB connection; validate_relation is used by
repositories immediately before a MERGE/CREATE writes a relationship.

Coverage note (docs/evaluation.md's "Known measurement gaps" has the full
reasoning): of relation_rules' 5 entries, only HAS_ASSIGNMENT/ASSIGNS are
currently written anywhere in this codebase
(src/graph/repositories/stakeholder_repository.py). ADDRESSES_OBJECTION,
CONVERTED_TO, MERGED_INTO have no materializing write path yet — validating
them here is correct and ready for when one exists, but has nothing to
guard today.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from src.core.config import ROOT


class UnknownClaimPredicate(ValueError):
    """Raised when extraction attempts to create an ungoverned Claim."""


class UnknownGraphRelation(ValueError):
    """Raised when code attempts to write a relationship type relation_rules
    doesn't define at all."""


class InvalidRelationEndpoint(ValueError):
    """Raised when a relationship type is known, but the node label on its
    domain (start) or target (end) side isn't one relation_rules allows for
    it — including via type_hierarchy ancestry (e.g. ACCOUNT satisfies a
    rule whose domain lists ORG)."""


@lru_cache(maxsize=1)
def _document(path: str | None = None) -> dict:
    ontology_path = Path(path) if path else ROOT / "config" / "ontologies" / "sales.yml"
    with ontology_path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


@lru_cache(maxsize=1)
def allowed_claim_predicates(path: str | None = None) -> frozenset[str]:
    values = _document(path).get("claim_predicates") or []
    predicates = frozenset(str(value).strip().upper() for value in values if str(value).strip())
    if not predicates:
        raise ValueError("sales ontology has no claim_predicates")
    return predicates


def validate_claim_predicate(predicate: str) -> str:
    normalized = str(predicate).strip().upper()
    if normalized not in allowed_claim_predicates():
        raise UnknownClaimPredicate(
            f"predicate {predicate!r} is not in the active sales ontology "
            f"({', '.join(sorted(allowed_claim_predicates()))})"
        )
    return normalized


@lru_cache(maxsize=1)
def _relation_rules(path: str | None = None) -> dict[str, dict[str, frozenset[str]]]:
    raw = _document(path).get("relation_rules") or {}
    rules: dict[str, dict[str, frozenset[str]]] = {}
    for relation_type, spec in raw.items():
        rules[str(relation_type).strip().upper()] = {
            "domain": frozenset(str(t).strip().upper() for t in (spec or {}).get("domain") or []),
            "target": frozenset(str(t).strip().upper() for t in (spec or {}).get("target") or []),
        }
    return rules


@lru_cache(maxsize=1)
def _type_ancestors(path: str | None = None) -> dict[str, frozenset[str]]:
    """type_hierarchy is a flat list of [SUBTYPE, SUPERTYPE] pairs (e.g.
    [ACCOUNT, ORG]) — single-level in the YAML today, but resolved
    transitively here so a future multi-level chain (e.g. [LEAD, PERSON],
    [PERSON, PARTY]) still works without touching this function."""
    pairs = _document(path).get("type_hierarchy") or []
    direct: dict[str, str] = {}
    for pair in pairs:
        if len(pair) == 2:
            direct[str(pair[0]).strip().upper()] = str(pair[1]).strip().upper()

    ancestors: dict[str, frozenset[str]] = {}
    for subtype in direct:
        chain: set[str] = set()
        current = subtype
        seen: set[str] = set()
        while current in direct and current not in seen:
            seen.add(current)
            current = direct[current]
            chain.add(current)
        ancestors[subtype] = frozenset(chain)
    return ancestors


def _satisfies(label: str, allowed: frozenset[str], path: str | None = None) -> bool:
    label = label.strip().upper()
    if label in allowed:
        return True
    return bool(_type_ancestors(path).get(label, frozenset()) & allowed)


def validate_relation(relation_type: str, domain_label: str, target_label: str, *, path: str | None = None) -> str:
    """Raise unless `(domain_label)-[:relation_type]->(target_label)` is an
    edge shape relation_rules actually allows (with type_hierarchy ancestry
    resolved on both sides). Returns the normalized relation_type on
    success, so a call site can inline it: `rel = validate_relation(...)`."""
    normalized = str(relation_type).strip().upper()
    rules = _relation_rules(path)
    if normalized not in rules:
        raise UnknownGraphRelation(
            f"relation {relation_type!r} is not in the active sales ontology's "
            f"relation_rules ({', '.join(sorted(rules))})"
        )
    rule = rules[normalized]
    if not _satisfies(domain_label, rule["domain"], path):
        raise InvalidRelationEndpoint(
            f"{relation_type!r} domain must be one of {sorted(rule['domain'])} "
            f"(or a subtype thereof), got {domain_label!r}"
        )
    if not _satisfies(target_label, rule["target"], path):
        raise InvalidRelationEndpoint(
            f"{relation_type!r} target must be one of {sorted(rule['target'])} "
            f"(or a subtype thereof), got {target_label!r}"
        )
    return normalized
