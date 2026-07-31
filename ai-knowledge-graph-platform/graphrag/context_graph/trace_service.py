"""Narrow application service for the first WPP governed decision flow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from graphrag.context_graph.models import (
    AgentRun, Case, ContextManifest, Decision, DecisionOption, DecisionTrace,
    Observation, OptionDisposition, PolicyEvaluation, PolicyResult,
    PolicyVersion, ToolCall, ToolCallStatus,
)
from graphrag.context_graph.repository import ContextGraphRepository


class ContextGraphTraceService:
    def __init__(self, repository: ContextGraphRepository):
        self._repository = repository

    async def record_wpp_campaign_placement(
        self,
        *,
        placement_id: str,
        question: str,
        statement_ids: list[str],
        statement_versions: list[str],
        chunk_ids: list[str],
        chunk_versions: list[str],
        document_ids: list[str],
        document_versions: list[str],
        policy_id: str = "data-privacy-policy",
        policy_version: str = "2024.1",
        selected: str = "escalate",
        tenant: str = "marketing",
        model_provider: str = "deepseek",
        model_version: str = "deepseek-v4-pro",
        prompt_version: str = "campaign-policy-v1",
        retrieval_mode: str = "hybrid",
        retrieval_config: dict | None = None,
    ) -> str:
        if selected not in {"allow", "deny", "escalate"}:
            raise ValueError("selected must be allow, deny, or escalate")
        now = datetime.now(timezone.utc)
        later = now + timedelta(days=1)
        case = Case(
            id=f"case-{placement_id}", tenant=tenant, case_type="campaign_placement",
            title="Determine whether a campaign placement is allowed", description=question,
        )
        run = AgentRun(
            id=f"run-{placement_id}", tenant=tenant, case_id=case.id,
            model_provider=model_provider, model_version=model_version,
            prompt_version=prompt_version, valid_from=now, valid_to=later,
            transaction_from=now, transaction_to=later,
        )
        tool_call = ToolCall(
            id=f"tool-{placement_id}", tenant=tenant, run_id=run.id,
            tool_name="search_campaign_policy", status=ToolCallStatus.EXECUTED,
            input_digest=placement_id, valid_from=now, valid_to=later,
            transaction_from=now, transaction_to=later,
        )
        observation = Observation(
            id=f"observation-{placement_id}", tenant=tenant,
            tool_call_id=tool_call.id, observation_type="policy_evidence",
            value="Campaign brief and data privacy policy references retrieved.",
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        )
        policy = PolicyVersion(
            id=f"policy-{policy_id}-{policy_version}", tenant=tenant,
            policy_id=policy_id, version=policy_version, title="Data Privacy Policy",
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        )
        manifest = ContextManifest(
            id=f"manifest-{placement_id}", tenant=tenant, case_id=case.id, run_id=run.id,
            statement_ids=statement_ids, chunk_ids=chunk_ids, document_ids=document_ids,
            statement_versions=statement_versions, chunk_versions=chunk_versions,
            document_versions=document_versions,
            policy_version_ids=[policy.id], tool_call_ids=[tool_call.id],
            observation_ids=[observation.id], model_provider=model_provider,
            model_version=model_version, prompt_version=prompt_version,
            retrieval_mode=retrieval_mode, retrieval_config=retrieval_config or {},
            task_input=question, ontology_version="marketing-adtech/v1",
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        ).with_integrity_hash()
        decision = Decision(
            id=f"decision-{placement_id}", tenant=tenant, case_id=case.id, run_id=run.id,
            manifest_id=manifest.id, title="Campaign placement policy decision",
            selected_option_id=f"option-{placement_id}-{selected}",
            reason_code="controlling_policy_rule", rationale="The controlling policy rule was applied.",
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        )
        options = [
            DecisionOption(
                id=f"option-{placement_id}-{label}", tenant=tenant, decision_id=decision.id,
                label=label, disposition=(OptionDisposition.SELECTED if label == selected else OptionDisposition.REJECTED),
                reason_code=("controlling_policy_rule" if label == selected else "policy_not_selected"),
                rationale=("Selected from the policy evaluation result." if label == selected else "Rejected after applying the controlling policy rule."),
                valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
            )
            for label in ("allow", "deny", "escalate")
        ]
        evaluation = PolicyEvaluation(
            id=f"policy-evaluation-{placement_id}", tenant=tenant, decision_id=decision.id,
            policy_version_id=policy.id, result=PolicyResult(selected),
            matched_rule="campaign placement controlling rule", reason_code="policy_checked",
            rationale="Campaign Brief evidence was evaluated against the applicable privacy policy.",
            valid_from=now, valid_to=later, transaction_from=now, transaction_to=later,
        )
        trace = DecisionTrace(
            case=case, run=run, tool_calls=[tool_call], observations=[observation],
            manifest=manifest, policy_versions=[policy], policy_evaluations=[evaluation],
            options=options, decision=decision,
        )
        return await self._repository.record_trace(trace)
