"""Safety and evaluation tests for deterministic capability routing."""

from __future__ import annotations

from graphrag.agents.skill_router import SkillRouteOutcome, default_skill_router
from mcp_server.capabilities import build_registry
from mcp_server.identity import CallerIdentity


def _identity(*scopes: str) -> CallerIdentity:
    return CallerIdentity(
        subject="agent-1", tenant="aerospace", scopes=frozenset(scopes),
        token_type="m2m", authenticated=True,
    )


def _route(request: str, *scopes: str):
    registry = build_registry()
    return default_skill_router().route(request, registry.discover(_identity(*scopes)))


def test_grounded_question_routes_to_the_versioned_answer_capability():
    route = _route("What does the approved maintenance policy require?", "read")
    assert route.outcome is SkillRouteOutcome.ROUTED
    assert route.skill_id == "knowledge.answer"
    assert route.capability_sequence == ["kg.answer.query"]


def test_policy_precedent_routes_to_the_context_graph_read_capability():
    route = _route("Find precedents for this policy decision", "read")
    assert route.outcome is SkillRouteOutcome.ROUTED
    assert route.skill_id == "context.find_precedent"
    assert route.capability_sequence == ["cg.precedent.find"]


def test_write_skill_is_denied_when_discovery_hides_its_required_capability():
    route = _route("Create a work order for finding CF-7", "read")
    assert route.outcome is SkillRouteOutcome.DENIED
    assert route.skill_id == "operations.create_work_order"
    assert route.capability_sequence == []


def test_write_skill_routes_only_for_a_scoped_caller_and_preserves_approval_requirement():
    route = _route("Create a work order for finding CF-7", "read", "biz:write")
    assert route.outcome is SkillRouteOutcome.ROUTED
    assert route.capability_sequence == ["biz.workorder.create"]
    assert route.requires_approval is True


def test_unknown_request_needs_clarification_instead_of_falling_back_to_a_powerful_tool():
    route = _route("Send the customer a message and bypass the normal process", "read", "biz:write")
    assert route.outcome is SkillRouteOutcome.CLARIFICATION_REQUIRED
    assert route.capability_sequence == []
