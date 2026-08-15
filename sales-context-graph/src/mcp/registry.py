"""Discoverable capability metadata shared by local and future remote MCP."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    capability_id: str
    version: str
    scope: str
    description: str
    deprecated: bool = False
    replacement: str | None = None


CAPABILITIES = (
    Capability("sales.account.brief", "1.0.0", "sales:read", "Grounded account briefing"),
    Capability("sales.opportunity.health", "1.0.0", "sales:read", "Opportunity health and risk"),
    Capability("sales.stakeholder.map", "1.0.0", "sales:read", "Buying committee map"),
    Capability("sales.commitments.overdue", "1.0.0", "sales:read", "Overdue commitments"),
    Capability("sales.next_action.recommend", "1.0.0", "sales:recommend", "Grounded next action"),
    Capability("sales.interaction.log", "1.0.0", "sales:write", "Log an interaction"),
    Capability("sales.opportunity.update", "1.0.0", "sales:write", "Preview or update an opportunity"),
    Capability("sales.discount.request", "1.0.0", "sales:write", "Request discount approval"),
    Capability("sales.crm.compensate", "1.0.0", "sales:write", "Compensate a prior CRM write"),
)


def discover(*, scopes: set[str], workspace_id: str | None) -> list[dict[str, object]]:
    """Return only capabilities explicitly entitled to the caller.

    ``workspace_id`` is required even for discovery so an anonymous caller
    cannot use the registry as an unscoped capability oracle.
    """
    if not workspace_id:
        return []
    return [
        {"id": item.capability_id, "version": item.version, "scope": item.scope,
         "description": item.description, "deprecated": item.deprecated,
         "replacement": item.replacement}
        for item in CAPABILITIES if item.scope in scopes
    ]
