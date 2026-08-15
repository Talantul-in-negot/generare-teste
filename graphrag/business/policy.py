"""Deterministic escalation policy for business-object write commands.

Reuses `graphrag.context_graph.policy_engine.evaluate_policy` unchanged --
no second policy evaluator. The policy itself is a small, in-code
`PolicyVersion` rather than one loaded from Neo4j: P0 has exactly one
mutating capability (`biz.workorder.create`), so a stored/versioned policy
registry for it would be scaffolding with nothing yet to justify it. The
rule set is still real and inspectable, and `evaluate_policy` is the same
deterministic, `eval`-free function that would run a Neo4j-stored policy --
swapping the source of the `PolicyVersion` in a later milestone is a
non-breaking change confined to `_workorder_approval_policy`.
"""

from __future__ import annotations

from graphrag.context_graph.models import (
    ConditionOperator,
    PolicyCondition,
    PolicyResult,
    PolicyRule,
    PolicyVersion,
    RuleMatch,
)

WORKORDER_CREATE_POLICY_ID = "biz-workorder-create-policy"
WORKORDER_CREATE_POLICY_VERSION = "2026.1"


def workorder_approval_policy(tenant: str) -> PolicyVersion:
    """Escalate CRITICAL/HIGH-severity findings, or any agent-initiated
    command, to human approval; allow everything else.
    """
    return PolicyVersion(
        tenant=tenant,
        policy_id=WORKORDER_CREATE_POLICY_ID,
        version=WORKORDER_CREATE_POLICY_VERSION,
        title="Work order creation approval policy",
        default_result=PolicyResult.ALLOW,
        rules=[
            PolicyRule(
                id="critical-or-high-severity-escalates",
                priority=10,
                match=RuleMatch.ANY,
                conditions=[
                    PolicyCondition(field="finding.severity", operator=ConditionOperator.EQ, value="critical"),
                    PolicyCondition(field="finding.severity", operator=ConditionOperator.EQ, value="high"),
                ],
                result=PolicyResult.ESCALATE,
                reason_code="high_severity_requires_approval",
                rationale="Critical or high-severity findings require human approval before a work order is created.",
            ),
            PolicyRule(
                id="agent-actor-escalates",
                priority=20,
                match=RuleMatch.ALL,
                conditions=[
                    PolicyCondition(field="actor.type", operator=ConditionOperator.EQ, value="agent"),
                ],
                result=PolicyResult.ESCALATE,
                reason_code="agent_initiated_requires_approval",
                rationale="Agent-initiated work order creation requires human approval regardless of severity.",
            ),
        ],
    )
