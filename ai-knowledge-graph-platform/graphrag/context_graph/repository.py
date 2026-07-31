"""Neo4j repository for the P0 Context Graph contract."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from graphrag.context_graph.models import (
    AgentRun, Case, ContextManifest, Decision, DecisionOption,
    DecisionTrace, Observation, PolicyEvaluation, PolicyVersion, ToolCall,
    CGAction, CGApproval, CGCorrection, CGExceptionGrant, CGFeedback, CGOutcome,
)
from graphrag.context_graph.validation import ContextGraphValidationError, validate_trace


def _props(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _trace_hash(trace: DecisionTrace) -> str:
    payload = json.dumps(trace.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ContextGraphRepository:
    """Tenant-scoped, idempotent, append-only P0 graph persistence."""

    def __init__(self, neo4j_client):
        self._neo4j = neo4j_client

    async def create_case(self, case: Case) -> str:
        await self._neo4j.run(
            "MERGE (n:CGCase {tenant: $tenant, id: $id}) ON CREATE SET n += $props",
            tenant=case.tenant, id=case.id, props=_props(case),
        )
        return case.id

    async def start_agent_run(self, run: AgentRun) -> str:
        await self._neo4j.run(
            """
            MATCH (c:CGCase {tenant: $tenant, id: $case_id})
            MERGE (r:CGAgentRun {tenant: $tenant, id: $id}) ON CREATE SET r += $props
            MERGE (r)-[:ADDRESSES]->(c)
            """,
            tenant=run.tenant, case_id=run.case_id, id=run.id, props=_props(run),
        )
        return run.id

    async def complete_agent_run(self, run_id: str, tenant: str, status: str = "completed") -> None:
        await self._neo4j.run(
            """
            MATCH (r:CGAgentRun {tenant: $tenant, id: $id})
            SET r.status = $status
            """,
            tenant=tenant, id=run_id, status=status,
        )

    async def record_tool_call(self, call: ToolCall) -> str:
        await self._neo4j.run(
            """
            MATCH (r:CGAgentRun {tenant: $tenant, id: $run_id})
            MERGE (t:CGToolCall {tenant: $tenant, id: $id}) ON CREATE SET t += $props
            MERGE (r)-[:MADE_TOOL_CALL]->(t)
            """,
            tenant=call.tenant, run_id=call.run_id, id=call.id, props=_props(call),
        )
        return call.id

    async def record_observation(self, observation: Observation) -> str:
        await self._neo4j.run(
            """
            MATCH (t:CGToolCall {tenant: $tenant, id: $tool_call_id})
            MERGE (o:CGObservation {tenant: $tenant, id: $id}) ON CREATE SET o += $props
            MERGE (t)-[:PRODUCED]->(o)
            """,
            tenant=observation.tenant, tool_call_id=observation.tool_call_id,
            id=observation.id, props=_props(observation),
        )
        return observation.id

    async def _assert_kg_references(self, tenant: str, manifest: ContextManifest) -> None:
        for label, ids in (
            ("Statement", manifest.statement_ids),
            ("Chunk", manifest.chunk_ids),
            ("Document", manifest.document_ids),
        ):
            if not ids:
                continue
            rows = await self._neo4j.run(
                f"MATCH (n:{label}) WHERE n.tenant = $tenant AND n.id IN $ids "
                "RETURN count(n) AS found",
                tenant=tenant, ids=list(set(ids)),
            )
            found = rows[0].get("found", 0) if rows else 0
            if found != len(set(ids)):
                raise ContextGraphValidationError(
                    "one or more Knowledge Graph references are missing or cross-tenant"
                )

    async def persist_manifest(self, manifest: ContextManifest) -> str:
        if manifest.compute_integrity_hash() != manifest.integrity_hash:
            raise ContextGraphValidationError("manifest integrity hash does not match")
        await self._assert_kg_references(manifest.tenant, manifest)
        await self._neo4j.run(
            """
            MATCH (r:CGAgentRun {tenant: $tenant, id: $run_id})
            MERGE (m:CGContextManifest {tenant: $tenant, id: $id})
            ON CREATE SET m += $props
            MERGE (r)-[:USED_CONTEXT]->(m)
            WITH m
            UNWIND $statement_ids AS statement_id
            OPTIONAL MATCH (s:Statement {tenant: $tenant, id: statement_id})
            FOREACH (x IN CASE WHEN s IS NULL THEN [] ELSE [s] END |
              MERGE (m)-[:INCLUDED_STATEMENT]->(x))
            WITH m
            UNWIND $chunk_ids AS chunk_id
            OPTIONAL MATCH (c:Chunk {tenant: $tenant, id: chunk_id})
            FOREACH (x IN CASE WHEN c IS NULL THEN [] ELSE [c] END |
              MERGE (m)-[:INCLUDED_CHUNK]->(x))
            WITH m
            UNWIND $document_ids AS document_id
            OPTIONAL MATCH (d:Document {tenant: $tenant, id: document_id})
            FOREACH (x IN CASE WHEN d IS NULL THEN [] ELSE [d] END |
              MERGE (m)-[:INCLUDED_DOCUMENT]->(x))
            WITH m
            UNWIND $policy_version_ids AS policy_id
            MATCH (p:CGPolicyVersion {tenant: $tenant, id: policy_id})
            MERGE (m)-[:INCLUDED_POLICY]->(p)
            RETURN m.id AS manifest_id
            """,
            tenant=manifest.tenant, run_id=manifest.run_id, id=manifest.id,
            props=_props(manifest), statement_ids=manifest.statement_ids,
            chunk_ids=manifest.chunk_ids, document_ids=manifest.document_ids,
            policy_version_ids=manifest.policy_version_ids,
        )
        return manifest.id

    async def record_policy_version(self, policy: PolicyVersion) -> str:
        await self._neo4j.run(
            "MERGE (p:CGPolicyVersion {tenant: $tenant, id: $id}) ON CREATE SET p += $props",
            tenant=policy.tenant, id=policy.id, props=_props(policy),
        )
        return policy.id

    async def record_option(self, option: DecisionOption) -> str:
        await self._neo4j.run(
            """
            MATCH (d:CGDecision {tenant: $tenant, id: $decision_id})
            MERGE (o:CGOption {tenant: $tenant, id: $id}) ON CREATE SET o += $props
            MERGE (d)-[:CONSIDERED]->(o)
            FOREACH (_ IN CASE WHEN $disposition = 'selected' THEN [1] ELSE [] END |
              MERGE (d)-[:SELECTED]->(o))
            FOREACH (_ IN CASE WHEN $disposition = 'rejected' THEN [1] ELSE [] END |
              MERGE (d)-[:REJECTED]->(o))
            """,
            tenant=option.tenant, decision_id=option.decision_id, id=option.id,
            props=_props(option), disposition=option.disposition.value,
        )
        return option.id

    async def record_policy_evaluation(self, evaluation: PolicyEvaluation) -> str:
        await self._neo4j.run(
            """
            MATCH (d:CGDecision {tenant: $tenant, id: $decision_id}),
                  (p:CGPolicyVersion {tenant: $tenant, id: $policy_version_id})
            MERGE (e:CGPolicyEvaluation {tenant: $tenant, id: $id}) ON CREATE SET e += $props
            MERGE (d)-[:HAS_POLICY_EVALUATION]->(e)
            MERGE (d)-[:APPLIED_POLICY]->(p)
            """,
            tenant=evaluation.tenant, decision_id=evaluation.decision_id,
            policy_version_id=evaluation.policy_version_id, id=evaluation.id,
            props=_props(evaluation),
        )
        return evaluation.id

    async def record_decision(self, decision: Decision) -> str:
        await self._neo4j.run(
            """
            MATCH (r:CGAgentRun {tenant: $tenant, id: $run_id}),
                  (m:CGContextManifest {tenant: $tenant, id: $manifest_id})
            MERGE (d:CGDecision {tenant: $tenant, id: $id}) ON CREATE SET d += $props
            MERGE (r)-[:PRODUCED_DECISION]->(d)
            """,
            tenant=decision.tenant, run_id=decision.run_id,
            manifest_id=decision.manifest_id, id=decision.id, props=_props(decision),
        )
        return decision.id

    async def record_trace(self, trace: DecisionTrace) -> str:
        """Validate everything, then persist through one atomic Cypher write."""
        validate_trace(trace)
        kg_refs = {
            "statement_ids": trace.manifest.statement_ids,
            "chunk_ids": trace.manifest.chunk_ids,
            "document_ids": trace.manifest.document_ids,
        }
        rows = await self._neo4j.run(
            """
            CALL {
              WITH $tenant AS tenant, $statement_ids AS ids
              UNWIND CASE WHEN size(ids) = 0 THEN [null] ELSE ids END AS id
              OPTIONAL MATCH (n:Statement {tenant: tenant, id: id})
              RETURN count(id) AS requested, count(n) AS found
            }
            WITH tenant, requested, found WHERE requested = found
            CALL {
              WITH tenant, $chunk_ids AS ids
              UNWIND CASE WHEN size(ids) = 0 THEN [null] ELSE ids END AS id
              OPTIONAL MATCH (n:Chunk {tenant: tenant, id: id})
              RETURN count(id) AS requested, count(n) AS found
            }
            WITH tenant, requested, found WHERE requested = found
            CALL {
              WITH tenant, $document_ids AS ids
              UNWIND CASE WHEN size(ids) = 0 THEN [null] ELSE ids END AS id
              OPTIONAL MATCH (n:Document {tenant: tenant, id: id})
              RETURN count(id) AS requested, count(n) AS found
            }
            WITH tenant, requested, found WHERE requested = found
            MERGE (c:CGCase {tenant: tenant, id: $case.id}) ON CREATE SET c += $case
            MERGE (r:CGAgentRun {tenant: tenant, id: $run.id}) ON CREATE SET r += $run
            MERGE (r)-[:ADDRESSES]->(c)
            FOREACH (item IN $policies |
              MERGE (p:CGPolicyVersion {tenant: tenant, id: item.id}) ON CREATE SET p += item)
            MERGE (m:CGContextManifest {tenant: tenant, id: $manifest.id}) ON CREATE SET m += $manifest
            MERGE (r)-[:USED_CONTEXT]->(m)
            WITH c, r, m
            CALL {
              WITH m, tenant, $statement_ids AS ids
              UNWIND CASE WHEN size(ids) = 0 THEN [null] ELSE ids END AS id
              OPTIONAL MATCH (s:Statement {tenant: tenant, id: id})
              FOREACH (x IN CASE WHEN s IS NULL THEN [] ELSE [s] END |
                MERGE (m)-[:INCLUDED_STATEMENT]->(x))
              RETURN count(*) AS linked_statements
            }
            WITH c, r, m
            CALL {
              WITH m, tenant, $chunk_ids AS ids
              UNWIND CASE WHEN size(ids) = 0 THEN [null] ELSE ids END AS id
              OPTIONAL MATCH (x:Chunk {tenant: tenant, id: id})
              FOREACH (item IN CASE WHEN x IS NULL THEN [] ELSE [x] END |
                MERGE (m)-[:INCLUDED_CHUNK]->(item))
              RETURN count(*) AS linked_chunks
            }
            WITH c, r, m
            CALL {
              WITH m, tenant, $document_ids AS ids
              UNWIND CASE WHEN size(ids) = 0 THEN [null] ELSE ids END AS id
              OPTIONAL MATCH (x:Document {tenant: tenant, id: id})
              FOREACH (item IN CASE WHEN x IS NULL THEN [] ELSE [x] END |
                MERGE (m)-[:INCLUDED_DOCUMENT]->(item))
              RETURN count(*) AS linked_documents
            }
            WITH c, r, m
            FOREACH (item IN $policies |
              MERGE (p:CGPolicyVersion {tenant: tenant, id: item.id})
              MERGE (m)-[:INCLUDED_POLICY]->(p))
            MERGE (d:CGDecision {tenant: tenant, id: $decision.id}) ON CREATE SET d += $decision
            MERGE (r)-[:PRODUCED_DECISION]->(d)
            WITH c, r, m, d
            CALL {
              WITH d, tenant, $statement_ids AS ids
              UNWIND CASE WHEN size(ids) = 0 THEN [null] ELSE ids END AS id
              OPTIONAL MATCH (s:Statement {tenant: tenant, id: id})
              FOREACH (x IN CASE WHEN s IS NULL THEN [] ELSE [s] END |
                MERGE (d)-[:SUPPORTED_BY]->(x))
              RETURN count(id) AS supported_statements
            }
            FOREACH (item IN $tool_calls |
              MERGE (t:CGToolCall {tenant: tenant, id: item.id}) ON CREATE SET t += item
              MERGE (r)-[:MADE_TOOL_CALL]->(t))
            FOREACH (item IN $observations |
              MERGE (o:CGObservation {tenant: tenant, id: item.id}) ON CREATE SET o += item
              MERGE (t:CGToolCall {tenant: tenant, id: item.tool_call_id})
              MERGE (t)-[:PRODUCED]->(o))
            FOREACH (item IN $options |
              MERGE (o:CGOption {tenant: tenant, id: item.id}) ON CREATE SET o += item
              MERGE (d)-[:CONSIDERED]->(o)
              FOREACH (_ IN CASE WHEN item.disposition = 'selected' THEN [1] ELSE [] END |
                MERGE (d)-[:SELECTED]->(o))
              FOREACH (_ IN CASE WHEN item.disposition = 'rejected' THEN [1] ELSE [] END |
                MERGE (d)-[:REJECTED]->(o)))
            FOREACH (item IN $evaluations |
              MERGE (e:CGPolicyEvaluation {tenant: tenant, id: item.id}) ON CREATE SET e += item
              MERGE (p:CGPolicyVersion {tenant: tenant, id: item.policy_version_id})
              MERGE (d)-[:HAS_POLICY_EVALUATION]->(e)
              MERGE (d)-[:APPLIED_POLICY]->(p))
            RETURN d.id AS decision_id, d.trace_hash AS existing_hash
            """,
            tenant=trace.case.tenant, case=_props(trace.case), run=_props(trace.run),
            manifest=_props(trace.manifest), decision={**_props(trace.decision), "trace_hash": _trace_hash(trace)},
            policies=[_props(p) for p in trace.policy_versions],
            tool_calls=[_props(t) for t in trace.tool_calls],
            observations=[_props(o) for o in trace.observations],
            options=[_props(o) for o in trace.options],
            evaluations=[_props(e) for e in trace.policy_evaluations],
            **kg_refs,
        )
        if not rows:
            raise ContextGraphValidationError("one or more Knowledge Graph references are missing or cross-tenant")
        existing_hash = rows[0].get("existing_hash")
        if existing_hash and existing_hash != _trace_hash(trace):
            raise ContextGraphValidationError("completed Context Graph decision is immutable")
        return trace.decision.id

    async def load_trace(self, decision_id: str, tenant: str) -> dict:
        rows = await self._neo4j.run(
            """
            MATCH (d:CGDecision {tenant: $tenant, id: $decision_id})
            MATCH (r:CGAgentRun {tenant: $tenant})-[:PRODUCED_DECISION]->(d)
            MATCH (r)-[:ADDRESSES]->(c:CGCase {tenant: $tenant})
            OPTIONAL MATCH (r)-[:USED_CONTEXT]->(m:CGContextManifest)
            OPTIONAL MATCH (r)-[:MADE_TOOL_CALL]->(t:CGToolCall)
            OPTIONAL MATCH (t)-[:PRODUCED]->(o:CGObservation {tenant: $tenant})
            OPTIONAL MATCH (d)-[:CONSIDERED]->(op:CGOption {tenant: $tenant})
            OPTIONAL MATCH (d)-[:HAS_POLICY_EVALUATION]->(e:CGPolicyEvaluation {tenant: $tenant})
            OPTIONAL MATCH (d)-[:APPLIED_POLICY]->(p:CGPolicyVersion {tenant: $tenant})
            OPTIONAL MATCH (m)-[:INCLUDED_STATEMENT]->(s:Statement {tenant: $tenant})
            OPTIONAL MATCH (m)-[:INCLUDED_CHUNK]->(ch:Chunk {tenant: $tenant})
            OPTIONAL MATCH (m)-[:INCLUDED_DOCUMENT]->(doc:Document {tenant: $tenant})
            RETURN c {.*} AS case, r {.*} AS run, m {.*} AS manifest,
              d {.*} AS decision, collect(DISTINCT t {.*}) AS tool_calls,
              collect(DISTINCT o {.*}) AS observations,
              collect(DISTINCT op {.*}) AS options,
              collect(DISTINCT e {.*}) AS policy_evaluations,
              collect(DISTINCT p {.*}) AS policy_versions,
              collect(DISTINCT s {.*}) AS statements,
              collect(DISTINCT ch {.*}) AS chunks,
              collect(DISTINCT doc {.*}) AS documents
            """,
            tenant=tenant, decision_id=decision_id,
        )
        return dict(rows[0]) if rows else {}

    async def append_governance_event(self, event: CGApproval | CGExceptionGrant | CGCorrection) -> str:
        labels = {
            CGApproval: ("CGApproval", "GOVERNED_BY", "decision_id"),
            CGExceptionGrant: ("CGExceptionGrant", "USED_EXCEPTION", "decision_id"),
            CGCorrection: ("CGCorrection", "CORRECTED_BY", "decision_id"),
        }
        label, relation, target_field = labels[type(event)]
        props = _props(event)
        query = f"""
            MATCH (d:CGDecision {{tenant: $tenant, id: $target_id}})
            MERGE (e:{label} {{tenant: $tenant, id: $id}})
            ON CREATE SET e += $props
            MERGE (d)-[:{relation}]->(e)
            RETURN e.id AS id
        """
        if isinstance(event, CGCorrection):
            query = f"""
                MATCH (old:CGDecision {{tenant: $tenant, id: $target_id}}),
                      (new:CGDecision {{tenant: $tenant, id: $replacement_id}})
                MERGE (e:{label} {{tenant: $tenant, id: $id}})
                ON CREATE SET e += $props
                MERGE (old)-[:SUPERSEDED_BY]->(new)
                MERGE (old)-[:{relation}]->(e)
                RETURN e.id AS id
            """
        rows = await self._neo4j.run(
            query, tenant=event.tenant, target_id=getattr(event, target_field),
            replacement_id=getattr(event, "replacement_decision_id", None),
            id=event.id, props=props,
        )
        if not rows:
            raise ContextGraphValidationError("governance event references a missing or cross-tenant decision")
        return event.id

    async def record_action(self, action: CGAction) -> str:
        rows = await self._neo4j.run(
            """
            MATCH (d:CGDecision {tenant: $tenant, id: $decision_id})
            MERGE (a:CGAction {tenant: $tenant, id: $id}) ON CREATE SET a += $props
            MERGE (d)-[:RESULTED_IN]->(a)
            RETURN a.id AS id
            """, tenant=action.tenant, decision_id=action.decision_id,
            id=action.id, props=_props(action),
        )
        if not rows:
            raise ContextGraphValidationError("action references a missing or cross-tenant decision")
        return action.id

    async def record_outcome(self, outcome: CGOutcome) -> str:
        rows = await self._neo4j.run(
            """
            MATCH (a:CGAction {tenant: $tenant, id: $action_id})
            MERGE (o:CGOutcome {tenant: $tenant, id: $id}) ON CREATE SET o += $props
            MERGE (a)-[:PRODUCED]->(o)
            RETURN o.id AS id
            """, tenant=outcome.tenant, action_id=outcome.action_id,
            id=outcome.id, props=_props(outcome),
        )
        if not rows:
            raise ContextGraphValidationError("outcome references a missing or cross-tenant action")
        return outcome.id

    async def record_feedback(self, feedback: CGFeedback) -> str:
        rows = await self._neo4j.run(
            """
            MATCH (d:CGDecision {tenant: $tenant, id: $decision_id})
            MERGE (f:CGFeedback {tenant: $tenant, id: $id}) ON CREATE SET f += $props
            MERGE (f)-[:EVALUATES]->(d)
            RETURN f.id AS id
            """, tenant=feedback.tenant, decision_id=feedback.decision_id,
            id=feedback.id, props=_props(feedback),
        )
        if not rows:
            raise ContextGraphValidationError("feedback references a missing or cross-tenant decision")
        return feedback.id

    async def replay_trace(self, decision_id: str, tenant: str, as_of: str) -> dict:
        rows = await self._neo4j.run(
            """
            MATCH (d:CGDecision {tenant: $tenant, id: $decision_id})
            WHERE d.transaction_from <= datetime($as_of)
              AND (d.transaction_to IS NULL OR d.transaction_to >= datetime($as_of))
            OPTIONAL MATCH (d)-[:PRODUCED_DECISION|PRODUCED_BY*0..1]-(r:CGAgentRun {tenant: $tenant})
            RETURN d {.*} AS decision, collect(DISTINCT r {.*}) AS runs
            """, tenant=tenant, decision_id=decision_id, as_of=as_of,
        )
        return dict(rows[0]) if rows else {}

    async def find_precedents(self, tenant: str, policy_version_id: str, limit: int = 10) -> list[dict]:
        rows = await self._neo4j.run(
            """
            MATCH (d:CGDecision {tenant: $tenant})-[:APPLIED_POLICY]->
                  (p:CGPolicyVersion {tenant: $tenant, id: $policy_version_id})
            WHERE d.status = 'final'
            OPTIONAL MATCH (d)-[:RESULTED_IN]->(a:CGAction {tenant: $tenant})
            OPTIONAL MATCH (a)-[:PRODUCED]->(o:CGOutcome {tenant: $tenant})
            RETURN d {.*} AS decision, p {.*} AS policy,
                   count(o) > 0 AS has_outcome
            ORDER BY has_outcome DESC, d.created_at DESC
            LIMIT $limit
            """, tenant=tenant, policy_version_id=policy_version_id, limit=limit,
        )
        return [dict(row) for row in rows]

    async def redact_trace(self, decision_id: str, tenant: str, reason_code: str, actor_id: str) -> str:
        rows = await self._neo4j.run(
            """
            MATCH (d:CGDecision {tenant: $tenant, id: $decision_id})
            MERGE (r:CGRedaction {tenant: $tenant, id: $redaction_id})
            ON CREATE SET r.reason_code = $reason_code, r.actor_id = $actor_id,
                          r.created_at = datetime(), r.schema_version = 'context-graph/v1'
            MERGE (d)-[:REDACTED_BY]->(r)
            RETURN r.id AS id
            """, tenant=tenant, decision_id=decision_id,
            redaction_id=f"redaction-{decision_id}-{reason_code}",
            reason_code=reason_code, actor_id=actor_id,
        )
        if not rows:
            raise ContextGraphValidationError("cannot redact a missing or cross-tenant trace")
        return rows[0]["id"]
