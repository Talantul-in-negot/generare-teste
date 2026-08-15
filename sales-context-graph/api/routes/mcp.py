"""Authenticated, request-local Streamable-HTTP MCP subset for sales tools.

The transport deliberately accepts a small JSON-RPC-shaped surface rather than
generic code/Cypher execution. It is enabled only with ``MCP_ENABLED=true``.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from api.dependencies import get_mcp_access_context, verify_mcp_bearer
from src.auth.policy import AccessContext
from src.core.config import get_settings
from src.core.telemetry import (
    CRM_COMMANDS_TOTAL,
    GROUNDED_RECOMMENDATIONS_TOTAL,
    MCP_REQUEST_DURATION_SECONDS,
    MCP_REQUESTS_TOTAL,
)
from src.domain.sales import SalesCompensationAction, SalesCRMWrite, SalesEvidence, SalesPolicy
from src.mcp.registry import discover
from src.sales.adapter import CRMCommandError, LocalCRMEmulator
from src.sales.policy import PolicyCatalog, PolicyError
from src.usecases.sales_intelligence import SalesAbstention, recommend_next_action

router = APIRouter(tags=["mcp"])
log = structlog.get_logger(__name__)


class McpRequest(BaseModel):
    method: str = Field(pattern=r"^(tools/list|tools/call)$")
    params: dict = Field(default_factory=dict)


def _scopes(access: AccessContext) -> set[str]:
    scopes = {"sales:read", "sales:recommend"}
    if access.has_role("admin", "workspace_admin", "manager", "sales_approver"):
        scopes.add("sales:write")
    return scopes


def _crm() -> LocalCRMEmulator:
    settings = get_settings()
    return LocalCRMEmulator(storage_path=Path(settings.local_crm_emulator_path))


def _command(arguments: dict, *, workspace_id: str, actor_id: str, dry_run: bool) -> SalesCRMWrite:
    requested_workspace = arguments.get("workspace_id")
    if requested_workspace is not None and requested_workspace != workspace_id:
        raise HTTPException(status_code=403, detail="MCP command workspace does not match authenticated workspace")
    values = {**arguments, "workspace_id": workspace_id, "actor_id": actor_id, "dry_run": dry_run}
    try:
        return SalesCRMWrite.model_validate(values)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _require_write(access: AccessContext, command: SalesCRMWrite) -> None:
    if "sales:write" not in _scopes(access):
        raise HTTPException(status_code=403, detail="MCP write capability is not entitled")
    high_risk = {"stage", "forecast_category", "close_date", "discount"}
    if high_risk.intersection(command.patch) and not access.has_role(
        "admin", "workspace_admin", "sales_approver",
    ):
        raise HTTPException(status_code=403, detail="high-risk CRM command requires an approver role")


def _receipt_payload(receipt: object) -> dict:
    value = receipt.__dict__.copy()  # CRMReceipt's frozen dataclass payload
    compensation = value.get("compensation")
    if compensation is not None:
        value["compensation"] = compensation.model_dump(mode="json")
    return value


@router.post("/mcp")
async def streamable_mcp(
    request: Request,
    workspace_id: str = Depends(verify_mcp_bearer),
    access: AccessContext = Depends(get_mcp_access_context),
    x_correlation_id: str | None = Header(None, alias="X-Correlation-Id"),
) -> dict:
    settings = get_settings()
    if not settings.mcp_enabled:
        raise HTTPException(status_code=503, detail="MCP transport is disabled")
    raw = await request.body()
    if len(raw) > settings.mcp_request_max_bytes:
        raise HTTPException(status_code=413, detail="MCP request exceeds configured size limit")
    try:
        payload = McpRequest.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail="invalid MCP request") from exc

    correlation_id = x_correlation_id or uuid4().hex
    capability = str(payload.params.get("name", "discovery")) if payload.method == "tools/call" else "discovery"
    started = time.perf_counter()
    try:
        if payload.method == "tools/list":
            tools = discover(scopes=_scopes(access), workspace_id=workspace_id)
            result = {"tools": tools, "correlation_id": correlation_id}
        else:
            result = _call_tool(
                name=capability, arguments=dict(payload.params.get("arguments", {})),
                workspace_id=workspace_id, access=access, correlation_id=correlation_id,
            )
        MCP_REQUESTS_TOTAL.labels(method=payload.method, capability=capability, outcome="success").inc()
        return result
    except HTTPException:
        MCP_REQUESTS_TOTAL.labels(method=payload.method, capability=capability, outcome="denied").inc()
        raise
    except CRMCommandError as exc:
        MCP_REQUESTS_TOTAL.labels(method=payload.method, capability=capability, outcome="rejected").inc()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - deployment guard
        log.exception("mcp.request_failed", correlation_id=correlation_id, capability=capability)
        MCP_REQUESTS_TOTAL.labels(method=payload.method, capability=capability, outcome="error").inc()
        raise HTTPException(status_code=503, detail="MCP tool is temporarily unavailable") from exc
    finally:
        MCP_REQUEST_DURATION_SECONDS.labels(capability=capability).observe(time.perf_counter() - started)


def _call_tool(*, name: str, arguments: dict, workspace_id: str, access: AccessContext,
               correlation_id: str) -> dict:
    if name == "sales.next_action.recommend":
        evidence = [SalesEvidence.model_validate(item) for item in arguments.get("evidence", [])]
        if any(item.workspace_id != workspace_id for item in evidence):
            raise HTTPException(status_code=403, detail="recommendation evidence crosses workspace boundary")
        requested_policy = SalesPolicy.model_validate(arguments["policy"])
        if requested_policy.workspace_id != workspace_id:
            raise HTTPException(status_code=403, detail="recommendation policy crosses workspace boundary")
        try:
            policy = PolicyCatalog().resolve(
                workspace_id=workspace_id, policy_id=requested_policy.policy_id,
                version=requested_policy.version,
            )
        except PolicyError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        result = recommend_next_action(
            workspace_id=workspace_id, opportunity_id=arguments["opportunity_id"], evidence=evidence,
            policy=policy, now=datetime.now(timezone.utc),
        )
        GROUNDED_RECOMMENDATIONS_TOTAL.labels(
            outcome="abstained" if isinstance(result, SalesAbstention) else "recommended",
        ).inc()
        return {"result": result.model_dump(mode="json"), "abstained": isinstance(result, SalesAbstention),
                "correlation_id": correlation_id}
    if name in {"sales.opportunity.update", "sales.interaction.log", "sales.discount.request"}:
        command = _command(arguments, workspace_id=workspace_id, actor_id=access.subject_id, dry_run=bool(arguments.get("dry_run", False)))
        _require_write(access, command)
        crm = _crm()
        if command.dry_run:
            CRM_COMMANDS_TOTAL.labels(operation="preview", outcome="success").inc()
            return {"receipt": _receipt_payload(crm.execute(command)), "correlation_id": correlation_id}
        receipt = crm.execute(command)
        CRM_COMMANDS_TOTAL.labels(operation="execute", outcome="success").inc()
        return {"receipt": _receipt_payload(receipt), "correlation_id": correlation_id}
    if name == "sales.crm.compensate":
        if "sales:write" not in _scopes(access) or not access.has_role("admin", "workspace_admin", "sales_approver"):
            raise HTTPException(status_code=403, detail="CRM compensation requires an approver role")
        action = SalesCompensationAction.model_validate({**arguments, "workspace_id": workspace_id})
        receipt = _crm().compensate(action)
        CRM_COMMANDS_TOTAL.labels(operation="compensate", outcome="success").inc()
        return {"receipt": _receipt_payload(receipt), "correlation_id": correlation_id}
    raise HTTPException(status_code=404, detail="MCP capability is not executable in this deployment")
