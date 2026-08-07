"""Runtime validation for sales Claim predicates.

The YAML ontology is the source of truth for the evidence vocabulary. Keeping
this check small and dependency-free makes it usable by both fixture and LLM
extractors without requiring a Neo4j connection in the extraction path.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from src.core.config import ROOT


class UnknownClaimPredicate(ValueError):
    """Raised when extraction attempts to create an ungoverned Claim."""


@lru_cache(maxsize=1)
def allowed_claim_predicates(path: str | None = None) -> frozenset[str]:
    ontology_path = Path(path) if path else ROOT / "config" / "ontologies" / "sales.yml"
    with ontology_path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    values = document.get("claim_predicates") or []
    predicates = frozenset(str(value).strip().upper() for value in values if str(value).strip())
    if not predicates:
        raise ValueError(f"sales ontology has no claim_predicates: {ontology_path}")
    return predicates


def validate_claim_predicate(predicate: str) -> str:
    normalized = str(predicate).strip().upper()
    if normalized not in allowed_claim_predicates():
        raise UnknownClaimPredicate(
            f"predicate {predicate!r} is not in the active sales ontology "
            f"({', '.join(sorted(allowed_claim_predicates()))})"
        )
    return normalized
