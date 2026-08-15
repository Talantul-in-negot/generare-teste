"""HTTP surface for the P0 business-object safe write path.

`POST /business/work-orders` is the flagship write endpoint: it builds a
`CommandEnvelope` from the caller's own token (never from the request
body) and hands it to `WorkOrderService`. Everything the service assumes
is already true by the time it runs -- authentication, the `biz:write`
scope, and `actor_id`/`tenant` being token-derived, not client-supplied --
is enforced here, in the same pattern `context_graph.py` uses for its
body-tenant routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.auth.dependencies import get_current_user, get_tenant, require_scope
from graphrag.business.commands import CommandEnvelope, CommandOutcome, CommandReceipt
from graphrag.business.repository import BusinessObjectRepository, NotFoundError
from graphrag.business.service import (
    WORKORDER_COMPENSATE_CAPABILITY, WORKORDER_CREATE_CAPABILITY, WorkOrderService,
)
from graphrag.graph.neo4j_client import get_neo4j

router = APIRouter(prefix="/business", tags=["Business Objects P0"])


def _actor_type(user: dict) -> str:
    return "agent" if user.get("type") == "m2m" else "human"


_STATUS_BY_OUTCOME = {
    CommandOutcome.EXECUTED: 201,
    CommandOutcome.DRY_RUN: 200,
    CommandOutcome.APPROVAL_REQUIRED: 202,
    CommandOutcome.STALE_VERSION: 409,
}

_DENIED_NOT_FOUND_REASONS = frozenset({"finding_not_found", "approval_not_found", "not_found"})


def _status_for_receipt(receipt: CommandReceipt) -> int:
    if receipt.outcome != CommandOutcome.DENIED:
        return _STATUS_BY_OUTCOME[receipt.outcome]
    if receipt.denial_reason == "invalid_args":
        return 422
    if receipt.denial_reason in _DENIED_NOT_FOUND_REASONS:
        return 404
    return 403


class WorkOrderCreateRequest(BaseModel):
    command_id: str | None = None
    reason_code: str = Field(min_length=1)
    originating_finding_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    assignee: str = ""
    expected_version: int | None = None
    dry_run: bool = False
    approval_id: str | None = None
    correlation_id: str = ""


@router.post("/work-orders", dependencies=[Depends(require_scope("biz:write"))])
async def create_work_order(
    request: WorkOrderCreateRequest,
    tenant: str = Depends(get_tenant),
    user: dict = Depends(get_current_user),
):
    envelope = CommandEnvelope(
        **({"command_id": request.command_id} if request.command_id else {}),
        capability=WORKORDER_CREATE_CAPABILITY,
        tenant=tenant,
        actor_id=user["sub"],
        actor_type=_actor_type(user),
        reason_code=request.reason_code,
        args={
            "originating_finding_id": request.originating_finding_id,
            "title": request.title,
            "description": request.description,
            "assignee": request.assignee,
        },
        expected_version=request.expected_version,
        dry_run=request.dry_run,
        approval_id=request.approval_id,
        correlation_id=request.correlation_id,
    )
    service = WorkOrderService(get_neo4j())
    receipt = await service.create_from_finding(envelope)
    status_code = _status_for_receipt(receipt)
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=receipt.model_dump(mode="json"))
    return JSONResponse(status_code=status_code, content=receipt.model_dump(mode="json"))


class ApprovalDecisionRequest(BaseModel):
    approved: bool


class WorkOrderCompensationRequest(BaseModel):
    command_id: str | None = None
    reason_code: str = Field(min_length=1)
    original_command_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)
    expected_finding_version: int = Field(ge=1)
    dry_run: bool = False
    approval_id: str | None = None
    correlation_id: str = ""


@router.post("/work-orders/{work_order_id}/compensate", dependencies=[Depends(require_scope("biz:write"))])
async def compensate_work_order(
    work_order_id: str,
    request: WorkOrderCompensationRequest,
    tenant: str = Depends(get_tenant),
    user: dict = Depends(get_current_user),
):
    envelope = CommandEnvelope(
        **({"command_id": request.command_id} if request.command_id else {}),
        capability=WORKORDER_COMPENSATE_CAPABILITY, tenant=tenant,
        actor_id=user["sub"], actor_type=_actor_type(user), reason_code=request.reason_code,
        args={
            "work_order_id": work_order_id,
            "original_command_id": request.original_command_id,
            "expected_finding_version": request.expected_finding_version,
        },
        expected_version=request.expected_version, dry_run=request.dry_run,
        approval_id=request.approval_id, correlation_id=request.correlation_id,
    )
    receipt = await WorkOrderService(get_neo4j()).compensate_work_order(envelope)
    status_code = _status_for_receipt(receipt)
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=receipt.model_dump(mode="json"))
    return JSONResponse(status_code=status_code, content=receipt.model_dump(mode="json"))


@router.post("/approvals/{approval_id}/decide", dependencies=[Depends(require_scope("biz:approve"))])
async def decide_approval(
    approval_id: str,
    request: ApprovalDecisionRequest,
    tenant: str = Depends(get_tenant),
    user: dict = Depends(get_current_user),
):
    service = WorkOrderService(get_neo4j())
    try:
        result = await service.decide_approval(
            tenant, approval_id, approved=request.approved, actor_id=user["sub"],
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return result


@router.get("/work-orders/{work_order_id}", dependencies=[Depends(require_scope("biz:read"))])
async def get_work_order(work_order_id: str, tenant: str = Depends(get_tenant)):
    result = await BusinessObjectRepository(get_neo4j()).get_work_order(tenant, work_order_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"work order {work_order_id!r} not found")
    return result


@router.get("/findings/{finding_id}", dependencies=[Depends(require_scope("biz:read"))])
async def get_finding(finding_id: str, tenant: str = Depends(get_tenant)):
    result = await BusinessObjectRepository(get_neo4j()).get_finding(tenant, finding_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"finding {finding_id!r} not found")
    return result
