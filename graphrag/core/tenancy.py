"""Tenant guard for query-building code.

Several modules built their Cypher tenant filter as::

    tenant_filter = "AND c.tenant = $tenant" if tenant else ""

which is a silent-widening bug: a falsy tenant (``None``, ``""``) does not
raise, it removes the filter and returns every tenant's rows. The failure is
invisible — the query succeeds and returns *more* data than asked for.

``require_tenant`` turns that into a loud failure at the boundary, so the
filter itself can be unconditional.
"""

from __future__ import annotations


def require_tenant(tenant: str | None) -> str:
    """Return ``tenant``, or raise if it is missing or blank.

    Call this at the public entry point of anything that builds a
    tenant-scoped Cypher query.
    """
    if not tenant or not tenant.strip():
        raise ValueError(
            "tenant is required and must be non-empty — refusing to run an "
            "unscoped query that would read across every tenant"
        )
    return tenant
