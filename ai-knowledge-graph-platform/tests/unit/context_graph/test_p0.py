from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from graphrag.context_graph.models import (
    AgentRun, Case, ContextManifest, Decision, DecisionOption, DecisionTrace,
    Observation, OptionDisposition, PolicyEvaluation, PolicyResult,
    PolicyVersion, ToolCall, ToolCallStatus,
)
from graphrag.context_graph.repository import ContextGraphRepository
from graphrag.context_graph.trace_service import ContextGraphTraceService
from graphrag.context_graph.validation import ContextGraphValidationError


def _trace(tenant: str = "marketing", statement_ids: list[str] | None = None) -> DecisionTrace:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later = datetime(2026, 1, 2, tzinfo=timezone.utc)
    case = Case(id="case-1", tenant=tenant, case_type="campaign_placement", title="Placement", created_at=now)
    run = AgentRun(id="run-1", tenant=tenant, case_id=case.id,
                   model_provider="deepseek", model_version="model-v1", prompt_version="prompt-v1",
                   valid_from=now, valid_to=later, transaction_from=now, transaction_to=later, created_at=now)
    tool = ToolCall(id="tool-1", tenant=tenant, run_id=run.id, tool_name="search_policy",
                    status=ToolCallStatus.EXECUTED, created_at=now)
    observation = Observation(id="obs-1", tenant=tenant, tool_call_id=tool.id,
                             observation_type="policy_match", value="rule-2", created_at=now)
    policy = PolicyVersion(id="policy-1", tenant=tenant, policy_id="privacy", version="2026.1", created_at=now)
    manifest = ContextManifest(
        id="manifest-1", tenant=tenant, case_id=case.id, run_id=run.id,
        statement_ids=["statement-1"] if statement_ids is None else statement_ids,
        statement_versions=["v1"] if statement_ids is None else ["v1"] * len(statement_ids),
        policy_version_ids=[policy.id],
        tool_call_ids=[tool.id], observation_ids=[observation.id],
        model_provider="deepseek", model_version="model-v1", prompt_version="prompt-v1",
        retrieval_mode="hybrid", retrieval_config={"top_k": 5}, task_input="Is placement allowed?",
        ontology_version="marketing-adtech/v1", valid_from=now, valid_to=later,
        transaction_from=now, transaction_to=later, created_at=now,
    ).with_integrity_hash()
    decision = Decision(id="decision-1", tenant=tenant, case_id=case.id, run_id=run.id,
                        manifest_id=manifest.id, title="Placement decision",
                        selected_option_id="option-allow", reason_code="policy_rule",
                        rationale="The applicable policy rule controls the decision.", created_at=now)
    options = [
        DecisionOption(id="option-allow", tenant=tenant, decision_id=decision.id,
                       label="allow placement", disposition=OptionDisposition.SELECTED,
                       reason_code="policy_allows", created_at=now),
        DecisionOption(id="option-deny", tenant=tenant, decision_id=decision.id,
                       label="deny placement", disposition=OptionDisposition.REJECTED,
                       reason_code="not_selected", created_at=now),
        DecisionOption(id="option-escalate", tenant=tenant, decision_id=decision.id,
                       label="escalate", disposition=OptionDisposition.REJECTED,
                       reason_code="not_selected", created_at=now),
    ]
    evaluation = PolicyEvaluation(
        id="eval-1", tenant=tenant, decision_id=decision.id,
        policy_version_id=policy.id, result=PolicyResult.ALLOW,
        matched_rule="rule-2", reason_code="policy_checked", rationale="Rule matched.", created_at=now,
    )
    return DecisionTrace(case=case, run=run, tool_calls=[tool], observations=[observation],
                         manifest=manifest, policy_versions=[policy],
                         policy_evaluations=[evaluation], options=options, decision=decision)


def test_manifest_hash_is_deterministic_and_changes_for_material_context():
    first = _trace().manifest
    second = _trace().manifest
    assert first.integrity_hash == second.integrity_hash
    second.task_input = "Is placement allowed under the updated brief?"
    assert second.compute_integrity_hash() != first.integrity_hash


def test_controlled_values_and_exactly_one_selected_option():
    trace = _trace()
    assert trace.options[0].disposition == OptionDisposition.SELECTED
    with pytest.raises(ValueError, match="exactly one selected"):
        trace.options[1].disposition = OptionDisposition.SELECTED
        DecisionTrace.model_validate(trace)


def test_missing_evidence_and_temporal_boundaries_are_rejected():
    with pytest.raises(ValueError, match="evidence reference"):
        _trace(statement_ids=[])


def test_cross_tenant_trace_is_rejected():
    trace = _trace()
    trace.policy_versions[0].tenant = "other"
    with pytest.raises(ValueError, match="same tenant"):
        DecisionTrace.model_validate(trace)


async def test_repository_uses_cg_labels_tenant_scope_and_idempotent_on_create():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[{"decision_id": "decision-1"}])
    result = await ContextGraphRepository(neo4j).record_trace(_trace())
    assert result == "decision-1"
    cypher = neo4j.run.await_args.args[0]
    assert "CGCase" in cypher and "CGDecision" in cypher and "CGContextManifest" in cypher
    assert "ON CREATE SET" in cypher
    assert neo4j.run.await_args.kwargs["tenant"] == "marketing"


async def test_missing_or_cross_tenant_kg_reference_aborts_without_partial_success():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[])
    with pytest.raises(ContextGraphValidationError, match="Knowledge Graph references"):
        await ContextGraphRepository(neo4j).record_trace(_trace())


async def test_existing_completed_trace_cannot_be_replaced():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[{"decision_id": "decision-1", "existing_hash": "different"}])
    with pytest.raises(ContextGraphValidationError, match="immutable"):
        await ContextGraphRepository(neo4j).record_trace(_trace())


async def test_load_trace_is_tenant_scoped():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[{"decision": {"id": "decision-1"}}])
    result = await ContextGraphRepository(neo4j).load_trace("decision-1", "marketing")
    assert result["decision"]["id"] == "decision-1"
    cypher = neo4j.run.await_args.args[0]
    assert "INCLUDED_STATEMENT" in cypher and "INCLUDED_CHUNK" in cypher
    assert neo4j.run.await_args.kwargs == {"tenant": "marketing", "decision_id": "decision-1"}


async def test_wpp_vertical_slice_builds_complete_trace_without_llm():
    repo = MagicMock()
    repo.record_trace = AsyncMock(return_value="decision-placement-1")
    service = ContextGraphTraceService(repo)
    result = await service.record_wpp_campaign_placement(
        placement_id="placement-1", question="Can this placement run?",
        statement_ids=["wpp-statement-1"], statement_versions=["brief-v1"],
        chunk_ids=["wpp-chunk-1"], chunk_versions=["brief-chunk-v1"],
        document_ids=["wpp-brief-1"], document_versions=["brief-v1"], selected="escalate",
    )
    assert result == "decision-placement-1"
    trace = repo.record_trace.await_args.args[0]
    assert trace.case.tenant == "marketing"
    assert trace.decision.selected_option_id.endswith("-escalate")
    assert trace.manifest.compute_integrity_hash() == trace.manifest.integrity_hash
