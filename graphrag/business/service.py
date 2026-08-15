"""WorkOrderService -- the P0 flagship safe write path.

`create_from_finding` implements the write flow end to end: idempotency
short-circuit, argument schema validation, referenced-object existence,
approval-reference validation, deterministic policy evaluation (escalating
CRITICAL/HIGH-severity findings or any agent-initiated command to human
approval), atomic optimistic-concurrency execution inside a
`CorpusMutation` bracket, and an immutable, idempotently-persisted receipt.

What this service deliberately does NOT re-implement, because it is already
enforced upstream by the FastAPI route layer (`api/routes/business.py`):
bearer-token authentication, the `biz:write`/`biz:approve` scope gate, and
binding `actor_id`/`tenant` to the caller's own token rather than trusting
anything client-supplied. Those checks happen before this service is ever
called; documented here so the split is explicit rather than assumed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from graphrag.business.commands import CommandEnvelope, CommandOutcome, CommandReceipt
from graphrag.business.models import ApprovalStatus, BizApproval, WorkOrder
from graphrag.business.policy import workorder_approval_policy
from graphrag.business.repository import BusinessObjectRepository, NotFoundError, StaleVersionError
from graphrag.context_graph.models import PolicyResult
from graphrag.context_graph.policy_engine import evaluate_policy
from graphrag.core.tenancy import require_tenant
from graphrag.graph.corpus_revision import CorpusMutation

WORKORDER_CREATE_CAPABILITY = "biz.workorder.create@1.0.0"

# Receipts are only persisted for outcomes that represent a permanent
# decision -- retrying the identical command_id must return the identical
# answer, not re-run the side effect. EXECUTED (already happened, must not
# repeat) and DENIED (a policy/entitlement decision that won't change on
# retry) are permanent. APPROVAL_REQUIRED, DRY_RUN, and STALE_VERSION are
# all deliberately excluded: each describes a state the caller is expected
# to resolve and retry past -- get the approval granted, drop --dry-run, or
# re-read the object and submit the current expected_version -- and a
# stale-version rejection in particular is the entire point of optimistic
# concurrency existing, so permanently caching it under the same command_id
# would turn a transient race into a permanent block.
_IDEMPOTENT_OUTCOMES = frozenset({CommandOutcome.EXECUTED, CommandOutcome.DENIED})


class WorkOrderCreateArgs(BaseModel):
    """Schema-validated payload carried inside a work-order-create envelope."""

    originating_finding_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    assignee: str = ""


class WorkOrderService:
    def __init__(self, neo4j_client):
        self._neo4j = neo4j_client
        self._repo = BusinessObjectRepository(neo4j_client)

    async def create_from_finding(self, envelope: CommandEnvelope) -> CommandReceipt:
        tenant = require_tenant(envelope.tenant)

        if envelope.capability != WORKORDER_CREATE_CAPABILITY:
            return await self._finalize(
                envelope, CommandOutcome.DENIED, denial_reason="capability_mismatch",
                detail=f"expected {WORKORDER_CREATE_CAPABILITY!r}, got {envelope.capability!r}",
            )

        existing = await self._repo.get_command_receipt(tenant, envelope.command_id)
        if existing:
            return CommandReceipt(**existing)

        try:
            args = WorkOrderCreateArgs(**envelope.args)
        except ValidationError as exc:
            return await self._finalize(
                envelope, CommandOutcome.DENIED, denial_reason="invalid_args", detail=str(exc),
            )

        finding = await self._repo.get_finding(tenant, args.originating_finding_id)
        if finding is None:
            return await self._finalize(
                envelope, CommandOutcome.DENIED, denial_reason="finding_not_found",
                detail=f"no finding {args.originating_finding_id!r} for this tenant",
            )

        approval: dict[str, Any] | None = None
        if envelope.approval_id:
            approval = await self._repo.get_approval(tenant, envelope.approval_id)
            if approval is None or approval["capability"] != envelope.capability:
                return await self._finalize(
                    envelope, CommandOutcome.DENIED, denial_reason="approval_not_found",
                    detail="referenced approval does not exist for this capability and tenant",
                )
            if approval["status"] != ApprovalStatus.APPROVED.value:
                return await self._finalize(
                    envelope, CommandOutcome.DENIED, denial_reason="approval_not_granted",
                    detail=f"approval status is {approval['status']!r}, not approved",
                )

        policy = workorder_approval_policy(tenant)
        evaluation = evaluate_policy(
            policy,
            {"finding": {"severity": finding.get("severity")}, "actor": {"type": envelope.actor_type}},
            decision_id=envelope.command_id,
            evaluation_id=f"{envelope.command_id}-policy",
        )

        if evaluation.result == PolicyResult.DENY:
            return await self._finalize(
                envelope, CommandOutcome.DENIED, denial_reason=evaluation.reason_code,
                detail=evaluation.rationale, policy_result=evaluation.result.value,
            )

        if evaluation.result == PolicyResult.ESCALATE and approval is None:
            pending = BizApproval(
                tenant=tenant, command_id=envelope.command_id, capability=envelope.capability,
                requested_by=envelope.actor_id, reason_code=evaluation.reason_code,
                rationale=evaluation.rationale,
            )
            await self._repo.create_approval(pending)
            # Not persisted as an idempotent command receipt: this command_id
            # must remain retriable once the approval is granted.
            return CommandReceipt(
                tenant=tenant, command_id=envelope.command_id, capability=envelope.capability,
                outcome=CommandOutcome.APPROVAL_REQUIRED, policy_result=evaluation.result.value,
                approval_id=pending.id, detail=evaluation.rationale,
            ).with_receipt_hash()

        if envelope.dry_run:
            return CommandReceipt(
                tenant=tenant, command_id=envelope.command_id, capability=envelope.capability,
                outcome=CommandOutcome.DRY_RUN, policy_result=evaluation.result.value,
                detail="dry-run — no write performed",
            ).with_receipt_hash()

        if envelope.expected_version is None:
            return await self._finalize(
                envelope, CommandOutcome.DENIED, denial_reason="missing_expected_version",
                detail="expected_version is required for a write command",
            )

        work_order = WorkOrder(
            tenant=tenant, originating_finding_id=args.originating_finding_id,
            title=args.title, description=args.description, assignee=args.assignee,
            created_by=envelope.actor_id, updated_by=envelope.actor_id,
            reason_code=envelope.reason_code,
        )

        mutation = CorpusMutation(self._neo4j, tenant, reason="business.workorder.create")
        try:
            async with mutation:
                result = await self._repo.create_work_order_from_finding(
                    tenant, work_order, expected_finding_version=envelope.expected_version,
                    actor_id=envelope.actor_id, actor_type=envelope.actor_type,
                    reason_code=envelope.reason_code,
                )
        except StaleVersionError as exc:
            return await self._finalize(
                envelope, CommandOutcome.STALE_VERSION, denial_reason="stale_version",
                detail=f"expected version {exc.expected}, actual {exc.actual}",
                from_version=exc.expected, to_version=exc.actual,
            )
        except NotFoundError as exc:
            return await self._finalize(
                envelope, CommandOutcome.DENIED, denial_reason="not_found", detail=str(exc),
            )
        except ValueError as exc:
            return await self._finalize(
                envelope, CommandOutcome.DENIED, denial_reason="invalid_transition", detail=str(exc),
            )

        return await self._finalize(
            envelope, CommandOutcome.EXECUTED,
            object_id=result["work_order_id"], object_type="WorkOrder",
            from_state=result["finding_from_state"], to_state=result["finding_to_state"],
            from_version=envelope.expected_version, to_version=result["finding_version"],
            policy_result=evaluation.result.value, approval_id=envelope.approval_id,
            corpus_revision=mutation.revision,
        )

    async def decide_approval(
        self, tenant: str, approval_id: str, *, approved: bool, actor_id: str,
    ) -> dict[str, Any]:
        """Decide a pending approval. The decider may never be the requester."""
        tenant = require_tenant(tenant)
        approval = await self._repo.get_approval(tenant, approval_id)
        if approval is None:
            raise NotFoundError(f"BizApproval {approval_id!r} not found")
        if approval["requested_by"] == actor_id:
            raise PermissionError("an approver cannot decide their own request")
        return await self._repo.decide_approval(
            tenant, approval_id, approved=approved, approved_by=actor_id,
        )

    async def _finalize(self, envelope: CommandEnvelope, outcome: CommandOutcome, **fields) -> CommandReceipt:
        tenant = require_tenant(envelope.tenant)
        receipt = CommandReceipt(
            tenant=tenant, command_id=envelope.command_id, capability=envelope.capability,
            outcome=outcome, **fields,
        ).with_receipt_hash()
        if outcome in _IDEMPOTENT_OUTCOMES:
            await self._repo.save_command_receipt(tenant, receipt.model_dump(mode="json"))
        return receipt
