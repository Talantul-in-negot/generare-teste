"""Measure entity resolution on synthetic supply-chain identity variants.

No real suppliers, facilities, ERP identifiers or coordinates are included.
Run: python scripts/benchmark_sustainability_entity_resolution.py
"""

from __future__ import annotations

import json

from graphrag.evaluation.entity_resolution import EntityResolutionCase, evaluate_entity_resolution
from graphrag.graph.alias_registry import AliasRegistry, _normalize, _normalize_ro

_CANONICAL = [
    ("Northwind Components", "SUPPLIER"),
    ("Apex Alloy", "SUPPLIER"),
    ("Recycled Aluminium", "MATERIAL"),
    ("Barcelona Assembly Site", "FACILITY"),
]

_CASES = [
    EntityResolutionCase("Northwind Components", "SUPPLIER", "matched", "Northwind Components", "SUPPLIER", "exact"),
    EntityResolutionCase("Northwind Components Ltd", "SUPPLIER", "matched", "Northwind Components", "SUPPLIER", "vendor-name-variant"),
    EntityResolutionCase("Apex Alloys", "SUPPLIER", "matched", "Apex Alloy", "SUPPLIER", "vendor-name-variant"),
    EntityResolutionCase("Recycled Aluminum", "MATERIAL", "matched", "Recycled Aluminium", "MATERIAL", "sku-spelling-variant"),
    EntityResolutionCase("Barcelona Assembly Siet", "FACILITY", "matched", "Barcelona Assembly Site", "FACILITY", "facility-typo"),
    EntityResolutionCase("Northwind Industrial Components", "SUPPLIER", "quarantined", category="ambiguous-vendor-name"),
    EntityResolutionCase("Contoso Logistics", "SUPPLIER", "new", category="new-supplier"),
]


def _registry() -> AliasRegistry:
    registry = AliasRegistry(None, tenant="sustainability")
    for name, entity_type in _CANONICAL:
        registry._exact[_normalize(name)] = (name, entity_type)
        registry._stemmed[_normalize_ro(name)] = (name, entity_type)
    return registry


def main() -> None:
    report = evaluate_entity_resolution(_CASES, _registry().resolve)
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    for result in report["results"]:
        print(f"{result['actual']:11} {'OK' if result['correct'] else 'MISS'}  {result['raw_name']}")


if __name__ == "__main__":
    main()
