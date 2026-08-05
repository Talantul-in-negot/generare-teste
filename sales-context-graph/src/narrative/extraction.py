"""Pulls citable claims out of an arbitrary intent-result dict.

Every serializer in src/usecases/serialization.py shapes its output differently
(objections/commitments/claims/conflicts/...), but every one of them nests
plain dicts carrying a "claim_id" key somewhere. Rather than teach the
narrative layer eleven different result shapes, this walks the structure once
and collects every such dict — a generic, low-maintenance seam that keeps
working as new intents are added, the same trade this repo already made for
/viz's generic JSON renderer.

`text` prefers evidence_text (the real transcript excerpt most intents already
compute via src/usecases/qa/common.py::evidence_excerpt), falling back to
object_value, then headline, then the claim_id itself so a claim with neither
field is still citable rather than silently dropped.
"""

from __future__ import annotations

_TEXT_FIELDS = ("evidence_text", "object_value", "headline")


def extract_citable_claims(result: dict) -> list[dict]:
    found: dict[str, dict] = {}
    _walk(result, found)
    return list(found.values())


def _walk(node, found: dict[str, dict]) -> None:
    if isinstance(node, dict):
        claim_id = node.get("claim_id")
        if isinstance(claim_id, str) and claim_id not in found:
            text = next((node[f] for f in _TEXT_FIELDS if node.get(f)), claim_id)
            found[claim_id] = {
                "claim_id": claim_id, "predicate": node.get("predicate", ""), "text": text,
            }
        for value in node.values():
            _walk(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found)
