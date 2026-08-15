"""Agent tool dispatch — the HTTP surface of :class:`ToolPolicy`.

``ToolPolicy`` implements the allowlist, per-tool risk levels, scope
enforcement, argument validation, cross-tenant guard, dry-run mode, timeout
and audit log that the README describes as the agent safety story. It was
fully tested but had no production caller, so none of those guarantees applied
to anything a request could reach. This router is that caller.

Scopes and tenant both come from the caller's signed token, never from the
request body: the policy's scope checks are only meaningful if the caller
cannot choose its own scopes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth.dependencies import get_current_user, get_tenant, require_scope
from graphrag.agents.tool_policy import DeniedAction, ToolPolicy

router = APIRouter()


class ToolCallRequest(BaseModel):
    tool: str = Field(min_length=1)
    args: dict = {}
    dry_run: bool = False


@router.post(
    "/tool",
    summary="Invoke a registered agent tool through the ToolPolicy gate",
    dependencies=[Depends(require_scope("read"))],
)
async def call_tool(
    request: ToolCallRequest,
    user: dict = Depends(get_current_user),
    tenant: str = Depends(get_tenant),
):
    """Execute one tool call under policy.

    Returns ``{"outcome": "executed", "result": ...}`` on success, or
    ``{"outcome": "denied", ...}`` with the policy's reason. A denial is a
    200 with an explicit outcome rather than an error status: the refusal is
    the product here, and the caller needs the structured reason.
    """
    policy = ToolPolicy.from_defaults(
        caller_scopes=user.get("scope", "").split(),
        dry_run=request.dry_run,
    )
    result = await policy.call(request.tool, request.args, tenant=tenant)

    if isinstance(result, DeniedAction):
        return {
            "outcome": "denied",
            "tool":    result.tool,
            "reason":  result.reason,
            "detail":  result.detail,
            "tenant":  result.tenant,
        }
    return {"outcome": "executed", "tool": request.tool, "result": result}


@router.get(
    "/tools",
    summary="List the tools this caller is permitted to invoke",
    dependencies=[Depends(require_scope("read"))],
)
async def list_tools(user: dict = Depends(get_current_user)):
    scopes = set(user.get("scope", "").split())
    policy = ToolPolicy.from_defaults(caller_scopes=sorted(scopes))
    return {
        "tools": [
            {
                "name":      spec.name,
                "risk":      spec.risk,
                "scopes":    spec.scopes,
                "permitted": all(s in scopes for s in spec.scopes),
            }
            for spec in policy._tools.values()
        ]
    }


@router.get(
    "/audit",
    summary="Structured audit log of tool calls made by this policy instance",
    dependencies=[Depends(require_scope("read"))],
)
async def tool_audit():
    """Audit entries are per-policy-instance and therefore per-request.

    A durable cross-request audit trail would need the entries persisted to
    Neo4j alongside the ChangeLog nodes; this endpoint documents the shape
    without pretending the history survives the request.
    """
    return {"entries": [], "note": "ToolPolicy audit is per-instance; see ChangeLog for durable history"}
