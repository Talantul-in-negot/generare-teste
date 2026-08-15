"""Deterministic, entitlement-aware skills routing surface.

This route is intentionally a *planner boundary*, not another tool executor.
It turns a bounded user request into an allowlisted capability sequence and
returns a refusal or clarification when that sequence is not fully entitled.
Actual execution remains in the MCP CapabilityRegistry / business command
paths, so a route can never manufacture authority.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth.dependencies import get_current_user, get_tenant, require_scope
from graphrag.agents.skill_router import default_skill_router
from mcp_server.capabilities import build_registry
from mcp_server.identity import CallerIdentity

router = APIRouter(prefix="/skills", tags=["Agent Skills"])


class SkillRouteRequest(BaseModel):
    request: str = Field(min_length=1, max_length=4_000)


@router.post("/route", dependencies=[Depends(require_scope("read"))])
async def route_skill(
    body: SkillRouteRequest,
    user: dict = Depends(get_current_user),
    tenant: str = Depends(get_tenant),
):
    """Return one authorized capability sequence or a safe non-execution outcome."""
    identity = CallerIdentity.from_claims(user, tenant=tenant)
    capabilities = build_registry().discover(identity)
    return default_skill_router().route(body.request, capabilities).model_dump()
