"""Versioned MCP capability registry.

A `CapabilitySpec` is the MCP-facing analogue of `graphrag.agents.tool_policy.ToolSpec`
-- reuses the exact same argument-schema shape and `validate_args()` function
(no second validation implementation), but adds what an agent-internal tool
never needed: a dotted stable id, a semver version, deprecation/replacement
fields, and legacy-name aliases so an existing wire registration keeps
working across a breaking internal rename.

`CapabilityRegistry.discover()` is entitlement-filtered -- a caller without
`biz:write` never sees that a write capability exists at all, not just that
it's denied. `contract_snapshot()` is entitlement-*independent* (the full
registry contents) -- it exists purely so a golden-file test can catch a
breaking change to the registry shape before it ships.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from graphrag.agents.tool_policy import validate_args
from graphrag.observability.agent_telemetry import record_capability_call


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str  # dotted stable id, e.g. "kg.graph.stats"
    version: str  # semver, e.g. "1.0.0"
    title: str
    kind: str  # "read" | "write"
    risk: str  # "safe" | "moderate" | "destructive"
    fn: Callable
    required_scopes: tuple[str, ...] = ()
    arg_schema: dict[str, dict] = field(default_factory=dict)
    dry_run_ok: bool = True
    requires_approval: bool = False
    deprecated: bool = False
    replacement: str | None = None
    legacy_aliases: tuple[str, ...] = ()
    # Read capabilities only ever need `tenant` (always injected below); a
    # write capability that builds a CommandEnvelope also needs the actor's
    # identity (subject, token type) to bind `actor_id`/`actor_type` from
    # the token, never from caller-supplied args. Opt-in and narrow rather
    # than passing identity to every capability, so existing read `fn`
    # signatures (e.g. `_graph_stats(tenant)`) need no change.
    pass_identity: bool = False

    @property
    def qualified_name(self) -> str:
        return f"{self.capability_id}@{self.version}"


@dataclass(frozen=True)
class DeniedCapabilityCall:
    """Structured refusal -- capability calls never raise for policy reasons."""

    capability: str
    reason: str  # "not_found" | "missing_scope" | "unauthenticated" |
    #               "tenant_mismatch" | "invalid_arg" | "dry_run"
    detail: str = ""


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


class CapabilityRegistry:
    """Entitlement-aware, versioned lookup and invocation for capabilities."""

    def __init__(self) -> None:
        self._by_qualified: dict[str, CapabilitySpec] = {}
        self._by_capability_id: dict[str, list[CapabilitySpec]] = {}
        self._by_alias: dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        if spec.qualified_name in self._by_qualified:
            raise ValueError(f"capability {spec.qualified_name!r} already registered")
        for alias in spec.legacy_aliases:
            if alias in self._by_alias:
                raise ValueError(f"legacy alias {alias!r} already registered")
        self._by_qualified[spec.qualified_name] = spec
        self._by_capability_id.setdefault(spec.capability_id, []).append(spec)
        for alias in spec.legacy_aliases:
            self._by_alias[alias] = spec

    def _find(self, name: str) -> CapabilitySpec | None:
        if name in self._by_qualified:
            return self._by_qualified[name]
        if name in self._by_alias:
            return self._by_alias[name]
        specs = self._by_capability_id.get(name)
        if not specs:
            return None
        return sorted(specs, key=lambda s: _version_key(s.version))[-1]

    def resolve(self, name: str, identity) -> CapabilitySpec | DeniedCapabilityCall:
        """Look up a capability by qualified name, bare id, or legacy alias.

        Existence is checked before entitlement (a caller who names a real
        capability they lack scope for gets `missing_scope`, not
        `not_found`) -- that distinction matters for `call()`'s error
        surface, even though `discover()` hides ungranted capabilities from
        a listing entirely.
        """
        spec = self._find(name)
        if spec is None:
            return DeniedCapabilityCall(
                capability=name, reason="not_found",
                detail=f"no capability registered as {name!r}",
            )
        missing = [s for s in spec.required_scopes if not identity.has_scope(s)]
        if missing:
            return DeniedCapabilityCall(
                capability=spec.qualified_name, reason="missing_scope",
                detail=f"missing scopes: {missing}",
            )
        return spec

    async def call(
        self, name: str, args: dict, identity, *, dry_run: bool = False,
    ) -> Any | DeniedCapabilityCall:
        """Resolve, authenticate, authorize, validate, and invoke a capability.

        Never raises for a policy refusal -- every denial path returns a
        `DeniedCapabilityCall` the same way `ToolPolicy.call()` returns a
        `DeniedAction`, so callers (the MCP tool wrappers) can serialize it
        directly instead of branching on exception types.
        """
        started_at = time.monotonic()
        capability = name
        outcome = "error"
        try:
            resolved = self.resolve(name, identity)
            if isinstance(resolved, DeniedCapabilityCall):
                outcome = resolved.reason
                return resolved
            spec = resolved
            capability = spec.qualified_name

            if not identity.authenticated:
                result = DeniedCapabilityCall(
                    capability=spec.qualified_name, reason="unauthenticated",
                    detail="no valid caller identity — set GRAPHRAG_MCP_TOKEN to a scoped token",
                )
                outcome = result.reason
                return result

            # `tenant` in caller-supplied args is an *assertion*, never an
            # authority: it must match the identity-bound tenant or the call is
            # denied outright, before argument validation even runs.
            caller_tenant = args.get("tenant")
            if caller_tenant and caller_tenant != identity.tenant:
                result = DeniedCapabilityCall(
                    capability=spec.qualified_name, reason="tenant_mismatch",
                    detail=(
                        f"caller is bound to tenant {identity.tenant!r}, "
                        f"cannot act on tenant {caller_tenant!r}"
                    ),
                )
                outcome = result.reason
                return result

            err = validate_args(spec.arg_schema, args, list(identity.scopes))
            if err:
                result = DeniedCapabilityCall(capability=spec.qualified_name, reason="invalid_arg", detail=err)
                outcome = result.reason
                return result

            if dry_run:
                if not spec.dry_run_ok:
                    result = DeniedCapabilityCall(
                        capability=spec.qualified_name, reason="dry_run_not_allowed",
                        detail="this capability cannot be safely previewed",
                    )
                    outcome = result.reason
                    return result
                result = DeniedCapabilityCall(
                    capability=spec.qualified_name, reason="dry_run",
                    detail="dry-run — capability not executed",
                )
                outcome = result.reason
                return result

            call_args = dict(args)
            call_args["tenant"] = identity.tenant  # always identity-bound, never caller-supplied
            if spec.pass_identity:
                call_args["identity"] = identity
            if asyncio.iscoroutinefunction(spec.fn):
                result = await spec.fn(**call_args)
            else:
                result = await asyncio.get_event_loop().run_in_executor(None, lambda: spec.fn(**call_args))
            outcome = "executed"
            return result
        finally:
            record_capability_call(
                capability=capability,
                outcome=outcome,
                tenant=getattr(identity, "tenant", "") or "anonymous",
                started_at=started_at,
            )

    def discover(self, identity) -> list[dict]:
        """Entitlement-filtered listing: a capability the caller lacks scope
        for is omitted entirely, not shown-then-denied."""
        return [
            self._describe(spec)
            for spec in self._by_qualified.values()
            if all(identity.has_scope(s) for s in spec.required_scopes)
        ]

    def contract_snapshot(self) -> list[dict]:
        """Full, entitlement-independent registry contents for the golden
        compatibility-test fixture (`test_mcp_contract_compat.py`)."""
        return [
            self._describe(spec)
            for spec in sorted(self._by_qualified.values(), key=lambda s: s.qualified_name)
        ]

    @staticmethod
    def _describe(spec: CapabilitySpec) -> dict:
        return {
            "capability_id": spec.capability_id,
            "version": spec.version,
            "qualified_name": spec.qualified_name,
            "title": spec.title,
            "kind": spec.kind,
            "risk": spec.risk,
            "required_scopes": list(spec.required_scopes),
            "arg_schema_keys": sorted(spec.arg_schema.keys()),
            "dry_run_ok": spec.dry_run_ok,
            "requires_approval": spec.requires_approval,
            "deprecated": spec.deprecated,
            "replacement": spec.replacement,
            "legacy_aliases": list(spec.legacy_aliases),
        }
