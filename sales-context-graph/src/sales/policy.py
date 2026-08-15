"""Versioned tenant policy catalogue for local CRM command enforcement."""

from __future__ import annotations

from datetime import datetime, timezone

from src.domain.sales import SalesPolicy


class PolicyError(ValueError):
    """A policy cannot authorize the requested CRM command."""


class PolicyCatalog:
    """Small deterministic catalogue; a persistent connector can replace it."""

    def __init__(self, policies: list[SalesPolicy] | None = None) -> None:
        self._policies: dict[tuple[str, str, str], SalesPolicy] = {}
        for policy in policies or [self.default_policy()]:
            self.register(policy)

    @staticmethod
    def default_policy(workspace_id: str = "*") -> SalesPolicy:
        return SalesPolicy(
            policy_id="local-default", workspace_id=workspace_id, version="1.0.0",
            name="Local CRM safety policy",
            approval_required_for={"stage", "forecast_category", "close_date", "discount"},
            allowed_write_fields={"stage", "forecast_category", "close_date", "discount", "amount_cents", "summary", "due_date", "owner_id"},
        )

    def register(self, policy: SalesPolicy) -> None:
        self._policies[(policy.workspace_id, policy.policy_id, policy.version)] = policy

    def resolve(self, *, workspace_id: str, policy_id: str, version: str,
                now: datetime | None = None) -> SalesPolicy:
        policy = self._policies.get((workspace_id, policy_id, version))
        policy = policy or self._policies.get(("*", policy_id, version))
        if policy is None:
            raise PolicyError("policy is not registered for this workspace")
        if not policy.applies_at(now or datetime.now(timezone.utc)):
            raise PolicyError("policy is inactive or expired")
        return policy

    def enforce(self, *, policy: SalesPolicy, patch: dict, approved: bool,
                dry_run: bool) -> None:
        fields = set(patch)
        if not fields:
            raise PolicyError("CRM patch cannot be empty")
        if not fields.issubset(policy.allowed_write_fields):
            raise PolicyError("CRM patch contains a field denied by policy")
        if fields.intersection(policy.approval_required_for) and not approved and not dry_run:
            raise PolicyError("policy requires explicit approval for this CRM patch")
