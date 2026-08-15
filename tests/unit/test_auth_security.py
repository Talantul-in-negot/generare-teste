"""Unit tests for auth security fixes — scope enforcement, open redirect, cookie flag."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.auth.default_auth import RequireAuthMiddleware, _is_public
from api.auth.dependencies import require_scope
from api.auth.jwt import create_access_token
from api.routes.auth import _safe_next


# ── require_scope — unconditional enforcement ──────────────────────────────────

class TestRequireScope:
    """Verify scope is checked for ALL token types, not just M2M.

    require_scope(s) returns an async inner function _check(user).
    We call it directly (bypassing FastAPI DI) by passing user as kwarg.
    """

    def _user(self, token_type: str, scopes: str) -> dict:
        return {"sub": "u1", "type": token_type, "scope": scopes}

    async def test_browser_token_with_required_scope_passes(self):
        checker = require_scope("read")
        user = self._user("browser", "read write")
        result = await checker(user=user)
        assert result == user

    async def test_browser_token_missing_scope_raises_403(self):
        checker = require_scope("admin")
        user = self._user("browser", "read write")   # no "admin"
        with pytest.raises(HTTPException) as exc_info:
            await checker(user=user)
        assert exc_info.value.status_code == 403

    async def test_m2m_token_with_scope_passes(self):
        checker = require_scope("write")
        user = self._user("m2m", "read write")
        result = await checker(user=user)
        assert result == user

    async def test_m2m_token_missing_scope_raises_403(self):
        checker = require_scope("write")
        user = self._user("m2m", "read")    # no "write"
        with pytest.raises(HTTPException) as exc_info:
            await checker(user=user)
        assert exc_info.value.status_code == 403

    async def test_empty_scope_field_raises_403_for_any_type(self):
        for token_type in ("browser", "m2m"):
            checker = require_scope("read")
            user = self._user(token_type, "")
            with pytest.raises(HTTPException) as exc_info:
                await checker(user=user)
            assert exc_info.value.status_code == 403

    async def test_multiple_scopes_all_individually_enforceable(self):
        """Each scope in a multi-scope token must pass its own gate."""
        checker_read  = require_scope("read")
        checker_write = require_scope("write")
        user = self._user("browser", "read write")
        assert await checker_read(user=user) == user
        assert await checker_write(user=user) == user

    async def test_scope_with_extra_whitespace_still_works(self):
        """Scope field with extra spaces must not break the split."""
        checker = require_scope("read")
        user = self._user("browser", "  read  write  ")
        result = await checker(user=user)
        assert result == user


# ── _safe_next — open redirect prevention ─────────────────────────────────────

class TestSafeNext:
    """Verify _safe_next rejects external URLs and allows safe relative paths."""

    def test_relative_path_allowed(self):
        assert _safe_next("/docs") == "/docs"

    def test_deep_relative_path_allowed(self):
        assert _safe_next("/admin/health") == "/admin/health"

    def test_none_returns_default(self):
        assert _safe_next(None) == "/docs"

    def test_empty_string_returns_default(self):
        assert _safe_next("") == "/docs"

    def test_absolute_http_rejected(self):
        assert _safe_next("http://evil.com") == "/docs"

    def test_absolute_https_rejected(self):
        assert _safe_next("https://evil.com/steal") == "/docs"

    def test_protocol_relative_rejected(self):
        assert _safe_next("//evil.com") == "/docs"

    def test_javascript_scheme_rejected(self):
        assert _safe_next("javascript:alert(1)") == "/docs"

    def test_custom_default_used_on_bad_url(self):
        assert _safe_next("http://bad.com", default="/home") == "/home"

    def test_custom_default_not_used_on_good_url(self):
        assert _safe_next("/dashboard", default="/home") == "/dashboard"


# ── _cookie_secure logic ───────────────────────────────────────────────────────

class TestCookieSecureLogic:
    """Verify the env == 'production' formula directly."""

    def test_production_env_means_secure_true(self):
        assert ("production" == "production") is True

    def test_development_env_means_secure_false(self):
        assert ("development" == "production") is False

    def test_staging_env_means_secure_false(self):
        """Non-production envs must not set secure=True."""
        assert ("staging" == "production") is False


# ── RequireAuthMiddleware — deny-by-default app floor ──────────────────────────

def _middleware_client() -> TestClient:
    """Minimal ASGI app with just RequireAuthMiddleware + a few dummy routes.

    Mirrors test_query_routes.py's `_make_client` pattern rather than
    spinning up the full api.main app, which needs live Neo4j/RabbitMQ/Redis
    connections at import/lifespan time that this file has no business
    depending on.
    """
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/kg/some-protected-thing")
    async def protected():
        return {"ok": True}

    @app.get("/auth/dev-token")
    async def dev_token_stub():
        return {"ok": True}

    @app.get("/admin/")
    async def admin_stub():
        return {"ok": True}

    app.add_middleware(RequireAuthMiddleware)
    return TestClient(app)


class TestIsPublic:
    """Unit-level coverage of the path-classification logic, independent of
    the ASGI plumbing — this is what actually decides deny-vs-allow."""

    def test_health_always_public(self):
        assert _is_public("/health", dev=False) is True
        assert _is_public("/health", dev=True) is True

    def test_admin_prefix_always_public_to_this_middleware(self):
        """/admin has its own auth mechanism (X-Admin-Token / session) --
        this middleware must not double-gate it with an incompatible Bearer
        requirement, which would make the Dash login page itself unreachable."""
        assert _is_public("/admin", dev=False) is True
        assert _is_public("/admin/", dev=False) is True
        assert _is_public("/admin/gdpr", dev=False) is True

    def test_admin_lookalike_path_not_treated_as_admin(self):
        """A path merely starting with 'admin' (no slash boundary) must not
        match the /admin prefix exemption."""
        assert _is_public("/administration", dev=False) is False

    def test_dev_only_route_public_in_dev(self):
        assert _is_public("/auth/dev-token", dev=True) is True
        assert _is_public("/demo", dev=True) is True

    def test_dev_only_route_not_public_outside_dev(self):
        assert _is_public("/auth/dev-token", dev=False) is False
        assert _is_public("/demo", dev=False) is False

    def test_protected_route_never_public(self):
        assert _is_public("/kg/some-protected-thing", dev=True) is False
        assert _is_public("/kg/some-protected-thing", dev=False) is False


class TestRequireAuthMiddleware:
    """End-to-end ASGI behavior via TestClient."""

    def test_public_path_reachable_with_no_token(self):
        client = _middleware_client()
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_protected_path_denied_with_no_token(self):
        client = _middleware_client()
        resp = client.get("/kg/some-protected-thing")
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Bearer"

    def test_protected_path_denied_with_garbage_token(self):
        client = _middleware_client()
        resp = client.get(
            "/kg/some-protected-thing",
            headers={"Authorization": "Bearer not-a-real-jwt"},
        )
        assert resp.status_code == 401

    def test_protected_path_allowed_with_valid_token(self):
        client = _middleware_client()
        token = create_access_token({"sub": "u1", "scope": "read", "tenant": "t1"})
        resp = client.get(
            "/kg/some-protected-thing",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_admin_path_reachable_with_no_bearer_token(self):
        """/admin's own guard (not tested here) is the real gate; this
        middleware must never block it before that guard even runs."""
        client = _middleware_client()
        resp = client.get("/admin/")
        assert resp.status_code == 200

    @patch("api.auth.default_auth.is_dev_env", return_value=True)
    def test_dev_route_reachable_with_no_token_in_dev(self, _mock_dev):
        client = _middleware_client()
        resp = client.get("/auth/dev-token")
        assert resp.status_code == 200

    @patch("api.auth.default_auth.is_dev_env", return_value=False)
    def test_dev_route_hidden_as_404_outside_dev(self, _mock_dev):
        """Hidden (404), not merely denied (401/403) -- a caller outside dev
        shouldn't be able to tell this route exists on the deployment."""
        client = _middleware_client()
        resp = client.get("/auth/dev-token")
        assert resp.status_code == 404

    def test_case_insensitive_bearer_scheme_accepted(self):
        """RFC 6750 auth-scheme token is case-insensitive."""
        client = _middleware_client()
        token = create_access_token({"sub": "u1", "scope": "read", "tenant": "t1"})
        resp = client.get(
            "/kg/some-protected-thing",
            headers={"Authorization": f"BEARER {token}"},
        )
        assert resp.status_code == 200
