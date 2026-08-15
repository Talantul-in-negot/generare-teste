"""Shared Neo4j node-property flattening helper.

Neo4j node properties cannot contain nested maps. This is the one
implementation of "flatten a Pydantic model to Neo4j-safe properties" --
`graphrag/context_graph/repository.py` and `graphrag/business/repository.py`
both import it rather than keeping their own copies.
"""

from __future__ import annotations

import json
from typing import Any


def props(model: Any) -> dict[str, Any]:
    """Flatten a Pydantic model to Neo4j node properties.

    Dicts, and lists containing dicts, are serialized to a deterministic
    JSON string (sorted keys, compact separators) so repeated writes of an
    unchanged value produce byte-identical properties.
    """
    dumped = model.model_dump(mode="json")
    return {
        key: json.dumps(value, sort_keys=True, separators=(",", ":"))
        if isinstance(value, dict) or (
            isinstance(value, list) and any(isinstance(item, dict) for item in value)
        ) else value
        for key, value in dumped.items()
    }
