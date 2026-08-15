"""Deterministic, auditable Context Graph policy evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from graphrag.context_graph.models import (
    ConditionOperator,
    PolicyEvaluation,
    PolicyRule,
    PolicyVersion,
    RuleMatch,
)


def _field_value(context: Mapping[str, Any], path: str) -> Any:
    value: Any = context
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _matches(actual: Any, operator: ConditionOperator, expected: Any) -> bool:
    if operator == ConditionOperator.IS_EMPTY:
        return actual is None or actual == "" or actual == [] or actual == {}
    if operator == ConditionOperator.NOT_EMPTY:
        return not _matches(actual, ConditionOperator.IS_EMPTY, expected)
    if operator == ConditionOperator.EQ:
        return actual == expected
    if operator == ConditionOperator.NE:
        return actual != expected
    if operator == ConditionOperator.IN:
        try:
            return actual in expected if expected is not None else False
        except TypeError:
            return False
    if operator == ConditionOperator.CONTAINS:
        try:
            return expected in actual if actual is not None else False
        except TypeError:
            return False
    if actual is None or expected is None:
        return False
    if operator == ConditionOperator.GT:
        return actual > expected
    if operator == ConditionOperator.GTE:
        return actual >= expected
    if operator == ConditionOperator.LT:
        return actual < expected
    if operator == ConditionOperator.LTE:
        return actual <= expected
    return False


def matching_rule(policy: PolicyVersion, context: Mapping[str, Any]) -> PolicyRule | None:
    """Return the first matching rule in stable priority/id order."""
    for rule in sorted(policy.rules, key=lambda item: (item.priority, item.id)):
        results = [
            _matches(_field_value(context, condition.field), condition.operator, condition.value)
            for condition in rule.conditions
        ]
        matched = all(results) if rule.match == RuleMatch.ALL else any(results)
        if matched:
            return rule
    return None


def evaluate_policy(
    policy: PolicyVersion,
    context: Mapping[str, Any],
    *,
    decision_id: str,
    evaluation_id: str,
) -> PolicyEvaluation:
    """Evaluate policy data without ``eval`` or hidden model reasoning."""
    rule = matching_rule(policy, context)
    result = rule.result if rule else policy.default_result
    return PolicyEvaluation(
        id=evaluation_id,
        tenant=policy.tenant,
        decision_id=decision_id,
        policy_version_id=policy.id,
        result=result,
        matched_rule=rule.id if rule else "default",
        reason_code=rule.reason_code if rule else "policy_default",
        rationale=rule.rationale if rule else "No policy rule matched; the declared default was applied.",
        valid_from=policy.valid_from,
        valid_to=policy.valid_to,
        transaction_from=policy.transaction_from,
        transaction_to=policy.transaction_to,
    )
