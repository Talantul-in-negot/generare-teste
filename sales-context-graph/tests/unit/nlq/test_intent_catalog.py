"""Increment 15 — structural guards on the intent catalog.

Same spirit as tests/unit/domain/test_roundtrip.py's
test_all_domain_models_are_covered: the catalog is only a useful single source
of truth if it cannot silently fall behind the API. These tests fail the moment
someone adds a query route without cataloguing it, or catalogues an intent the
dispatcher cannot run.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from api.main import app
from src.nlq.catalog import INTENT_CATALOG, INTENT_IDS, classifier_intents, get_intent
from src.usecases.nlq.dispatch import IntentDispatcher

# Route prefixes whose members are user-answerable questions.
_QUERY_PREFIXES = ("/api/v1/qa/", "/api/v1/opportunities/", "/api/v1/sellers/")

# Routes under those prefixes that are deliberately not catalog intents.
# /qa/intents serves the catalog itself; cataloguing it would be circular.
# The conflict-resolve route is a side-effecting action (closes a Claim's
# bitemporal interval), not a question — the NL layer only ever dispatches
# read-only intents (src/usecases/nlq/ask.py never mutates), so it's excluded
# by design, not oversight.
_NOT_INTENTS = {"/api/v1/qa/intents"}
_NOT_INTENT_ROUTES = {
    ("POST", "/api/v1/opportunities/{opportunity_id}/conflicts/{conflict_id}/resolve"),
    # Product-workflow routes are stateful seller/buyer operations, not
    # read-only questions the NL dispatcher is permitted to execute.
    ("GET", "/api/v1/opportunities/{opportunity_id}/buyer-spaces"),
    ("POST", "/api/v1/opportunities/{opportunity_id}/buyer-spaces"),
    ("GET", "/api/v1/opportunities/{opportunity_id}/meeting-brief"),
    ("GET", "/api/v1/opportunities/{opportunity_id}/meeting-follow-ups"),
    ("POST", "/api/v1/opportunities/{opportunity_id}/meeting-follow-ups"),
    ("POST", "/api/v1/opportunities/{opportunity_id}/revenue-outcomes"),
}


def _query_routes() -> set[tuple[str, str]]:
    found = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith(_QUERY_PREFIXES) or route.path in _NOT_INTENTS:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            found.add((method, route.path))
    return found


def test_every_query_route_has_a_catalog_entry():
    catalogued = {(spec.method, spec.path) for spec in INTENT_CATALOG}
    missing = _query_routes() - catalogued - _NOT_INTENT_ROUTES
    assert missing == set(), f"routes missing from src/nlq/catalog.py: {sorted(missing)}"


def test_every_catalog_entry_points_at_a_real_route():
    catalogued = {(spec.method, spec.path) for spec in INTENT_CATALOG}
    dangling = catalogued - _query_routes()
    assert dangling == set(), f"catalog entries with no matching route: {sorted(dangling)}"


def test_every_catalog_entry_has_a_dispatcher():
    dispatcher = IntentDispatcher.__dict__
    for spec in INTENT_CATALOG:
        handler = f"_run_{spec.intent_id.replace('-', '_')}"
        assert handler in dispatcher, f"{spec.intent_id} has no {handler} on IntentDispatcher"


def test_intent_ids_are_unique():
    assert len(INTENT_IDS) == len(INTENT_CATALOG)


def test_classifier_sees_fewer_intents_than_the_full_catalog():
    """The two GET aliases are hidden from the classifier — offering the model
    two functionally identical options would make its pick arbitrary."""
    visible = classifier_intents()
    assert len(visible) < len(INTENT_CATALOG)
    hidden = {s.intent_id for s in INTENT_CATALOG} - {s.intent_id for s in visible}
    assert hidden == {"opportunity-conflicts", "buying-committee"}


def test_get_intent_rejects_unknown_ids():
    try:
        get_intent("not-a-real-intent")
    except KeyError as exc:
        assert "not-a-real-intent" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected KeyError")


def test_required_params_is_a_strict_subset_view():
    briefing = get_intent("call-briefing")
    # Both of call-briefing's params are optional individually (XOR is enforced
    # in the use case), so required_params is empty here — the property must not
    # silently treat "optional" as "required".
    assert briefing.required_params == ()
    objections = get_intent("account-objections")
    assert [p.name for p in objections.required_params] == ["opportunity_id"]
