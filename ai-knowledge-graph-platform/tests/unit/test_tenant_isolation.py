"""Regression guards for the tenant-isolation and metric-correctness fixes.

Every test here corresponds to a defect that shipped and that the existing
suite could not catch, because no test ever inspected a tenant predicate and
no test ever executed Cypher.

The defects:
  1. `$tenant = 'default' OR x.tenant = $tenant` appeared 80 times, making
     tenant "default" — also the default value of every route's tenant
     parameter — a read-every-tenant wildcard.
  2. Routes took `tenant` from the request body, so any token holder could
     name any tenant.
  3. audit_trail matched entities on (name, type) with no tenant, attaching
     one tenant's ChangeLog to another tenant's node.
  4. apply_calibration's final bin was half-open, so confidence 1.0 — the
     extractor's clamp ceiling — could never be calibrated.
  5. graph_evaluator's `prev_orphans or orphans` reported zero orphan growth
     when the previous snapshot was healthy.
  6. community_manager counted every RELATES_TO edge in the database because
     a WHERE attached to an OPTIONAL MATCH does not filter rows.
"""

from __future__ import annotations

import pathlib
import re
from unittest.mock import AsyncMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ── 1. The wildcard must not come back ────────────────────────────────────────

class TestNoTenantWildcard:
    def test_no_cypher_treats_default_as_a_wildcard(self):
        """No source file may reintroduce `$tenant = 'default' OR ...`.

        This is a text-level guard on purpose: the pattern is a Cypher string,
        so no type checker or import graph would catch its return, and it
        reads as innocuous at the call site.
        """
        offenders = []
        for path in (REPO_ROOT / "graphrag").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if re.search(r"\$tenant\s*=\s*'default'", line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
        assert not offenders, (
            "tenant 'default' is being used as a read-everything wildcard at:\n  "
            + "\n  ".join(offenders)
        )

    def test_no_tenant_filter_is_conditional_on_truthiness(self):
        """`"...tenant..." if tenant else ""` silently widens on a falsy tenant."""
        offenders = []
        for path in (REPO_ROOT / "graphrag").rglob("*.py"):
            # tenancy.py documents the anti-pattern in its module docstring.
            if "__pycache__" in path.parts or path.name == "tenancy.py":
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "tenant" in line and re.search(r'if tenant else ""', line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
        assert not offenders, (
            "tenant filter is dropped rather than enforced when tenant is falsy at:\n  "
            + "\n  ".join(offenders)
        )


# ── 2. Tenant comes from the token, not the request ───────────────────────────

class TestTenantFromToken:
    async def test_get_tenant_rejects_a_token_with_no_tenant_claim(self):
        from fastapi import HTTPException

        from api.auth.dependencies import get_tenant

        with pytest.raises(HTTPException) as exc:
            await get_tenant(user={"sub": "u", "scope": "read"})
        assert exc.value.status_code == 403

    async def test_get_tenant_returns_the_claim(self):
        from api.auth.dependencies import get_tenant

        assert await get_tenant(user={"sub": "u", "tenant": "acme"}) == "acme"

    def test_issued_tokens_carry_a_tenant_claim(self):
        """Every create_access_token call site must stamp a tenant."""
        auth_src = (REPO_ROOT / "api" / "routes" / "auth.py").read_text(encoding="utf-8")
        payloads = re.findall(r"create_access_token\(\{(.*?)\}\)", auth_src, re.S)
        assert payloads, "no create_access_token call sites found — test is stale"
        for payload in payloads:
            assert '"tenant"' in payload, f"token payload has no tenant claim:\n{payload}"

    def test_no_route_accepts_tenant_from_the_client(self):
        """Handlers must take tenant via Depends(get_tenant), never as a default."""
        offenders = []
        for path in (REPO_ROOT / "api" / "routes").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r'tenant:\s*str\s*(\|\s*None\s*)?=\s*("|\')', line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
        assert not offenders, (
            "tenant is client-supplied at:\n  " + "\n  ".join(offenders)
        )


class TestRequireTenant:
    @pytest.mark.parametrize("missing", [None, "", "   "])
    def test_falsy_tenant_raises(self, missing):
        from graphrag.core.tenancy import require_tenant

        with pytest.raises(ValueError, match="tenant is required"):
            require_tenant(missing)

    def test_valid_tenant_passes_through(self):
        from graphrag.core.tenancy import require_tenant

        assert require_tenant("acme") == "acme"


# ── 3. Audit trail is tenant-scoped ───────────────────────────────────────────

class TestAuditTrailTenantScoping:
    async def test_entity_batch_matches_within_the_tenant(self):
        from graphrag.graph.audit_trail import AuditTrail

        neo4j = AsyncMock()
        neo4j.run = AsyncMock(return_value=[])
        trail = AuditTrail(neo4j)

        await trail.log_entities_batch(
            [{"name": "Boeing", "type": "ORG", "log_id": "1", "operation": "upsert",
              "old_values": "{}", "new_values": "{}", "changed_by": "t",
              "source_doc_id": "d"}],
            tenant="acme",
        )

        cypher = neo4j.run.call_args[0][0]
        assert "tenant: $tenant" in cypher, (
            "MATCH has no tenant in its key — `LIMIT 1` can bind another "
            "tenant's entity with the same (name, type)"
        )
        assert neo4j.run.call_args.kwargs["tenant"] == "acme"

    async def test_get_history_is_tenant_scoped(self):
        from graphrag.graph.audit_trail import AuditTrail

        neo4j = AsyncMock()
        neo4j.run = AsyncMock(return_value=[])
        await AuditTrail(neo4j).get_history("Boeing", "ORG", tenant="acme")

        cypher = neo4j.run.call_args[0][0]
        assert "tenant: $tenant" in cypher
        assert neo4j.run.call_args.kwargs["tenant"] == "acme"


# ── 4. Calibration covers the closed upper bound ──────────────────────────────

class TestCalibrationUpperBound:
    async def test_confidence_of_exactly_one_is_calibrated(self):
        """1.0 is the extractor's clamp ceiling and the most over-confident value.

        With a half-open final bin it matched nothing and was returned raw —
        the one value most in need of correction was the one that bypassed it.
        """
        from graphrag.graph.confidence_calibration import CalibrationService

        svc = CalibrationService(AsyncMock())
        curve = [
            {"bin_start": 0.0, "bin_end": 0.9, "n": 5,  "mean_actual": 0.5},
            {"bin_start": 0.9, "bin_end": 1.0, "n": 20, "mean_actual": 0.62},
        ]
        svc.calibration_curve = AsyncMock(return_value=curve)

        assert await svc.apply_calibration(1.0, tenant="acme") == 0.62
        assert await svc.apply_calibration(0.95, tenant="acme") == 0.62
        assert await svc.apply_calibration(0.5, tenant="acme") == 0.5


# ── 5. Orphan growth from a healthy baseline is reported ──────────────────────

class TestOrphanDelta:
    async def test_growth_from_a_zero_baseline_is_not_silenced(self):
        from graphrag.graph.graph_evaluator import GraphEvaluator

        neo4j = AsyncMock()
        # 1st query returns total + current orphans; 2nd returns the previous
        # snapshot, which is healthy (0 orphans) — the case that was silenced.
        neo4j.run = AsyncMock(side_effect=[
            [{"total": 1000, "orphans": 500}],
            [{"prev_orphans": 0}],
        ])

        result = await GraphEvaluator(neo4j).orphan_growth_rate(tenant="acme")

        assert result["orphan_count"] == 500
        assert result["orphan_delta"] == 500, (
            "a clean previous snapshot (0 orphans) must not be treated as "
            "'no previous data' — that silences the alarm case"
        )


# ── 6. Community staleness counts only this tenant's edges ────────────────────

class TestCommunityStalenessEdgeCount:
    async def test_edge_count_is_scoped_to_the_tenant(self):
        """A WHERE on an OPTIONAL MATCH nulls the optional node, it does not
        drop the row — so `count(r)` counted every edge in the database."""
        from graphrag.graph.community_manager import CommunityManager

        neo4j = AsyncMock()
        neo4j.run = AsyncMock(return_value=[
            {"entity_count": 1, "edge_count": 1, "community_count": 1}
        ])

        await CommunityManager(neo4j).snapshot(tenant="acme")

        cypher = neo4j.run.call_args_list[0][0][0]
        assert "RELATES_TO {tenant: $tenant}" in cypher, (
            "RELATES_TO is not tenant-scoped in the staleness edge count"
        )
        assert "OPTIONAL MATCH (d:Document" not in cypher
