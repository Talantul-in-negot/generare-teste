"""Pure authorization policy primitives.

The IdP integration supplies identity and claims; this module owns the
application-side deny-by-default rules so they can be tested without a live
Okta/Azure/Showpad tenant.  Callers should construct ``AccessContext`` from
verified token claims, never directly from an arbitrary request body.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class AccessDenied(PermissionError):
    """Raised when a principal is outside the requested resource scope."""


@dataclass(frozen=True)
class AccessContext:
    workspace_id: str
    subject_id: str
    roles: frozenset[str] = field(default_factory=frozenset)
    division_ids: frozenset[str] = field(default_factory=frozenset)
    opportunity_ids: frozenset[str] = field(default_factory=frozenset)

    def has_role(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))


def require_role(context: AccessContext, *roles: str) -> None:
    if not context.has_role(*roles):
        raise AccessDenied(f"one of roles {sorted(roles)} is required")


def require_division(context: AccessContext, division_id: str | None) -> None:
    """Enforce division scope; unassigned assets are visible to admins only."""
    if context.has_role("admin", "workspace_admin"):
        return
    if division_id is None or division_id not in context.division_ids:
        raise AccessDenied("principal is not authorized for this division")


def require_opportunity(context: AccessContext, opportunity_id: str) -> None:
    if context.has_role("admin", "workspace_admin"):
        return
    if opportunity_id not in context.opportunity_ids:
        raise AccessDenied("principal is not authorized for this opportunity")


def can_read_asset(context: AccessContext, *, division_id: str | None, is_sensitive: bool) -> bool:
    if context.has_role("admin", "workspace_admin"):
        return True
    if is_sensitive and not context.has_role("compliance", "content_admin"):
        return False
    try:
        require_division(context, division_id)
    except AccessDenied:
        return False
    return True
