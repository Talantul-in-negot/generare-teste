"""Unit tests for mcp_server.identity.CallerIdentity."""

from __future__ import annotations

from unittest.mock import patch

from mcp_server.identity import TOKEN_ENV_VAR, CallerIdentity


class TestFromToken:
    def test_blank_token_is_anonymous(self):
        identity = CallerIdentity.from_token("")
        assert identity == CallerIdentity.anonymous()
        assert identity.authenticated is False

    def test_none_token_is_anonymous(self):
        assert CallerIdentity.from_token(None).authenticated is False

    def test_invalid_token_is_anonymous(self):
        with patch("mcp_server.identity.decode_access_token", side_effect=ValueError("bad token")):
            identity = CallerIdentity.from_token("garbage")
        assert identity.authenticated is False

    def test_token_missing_tenant_claim_is_anonymous(self):
        with patch("mcp_server.identity.decode_access_token",
                    return_value={"sub": "agent-1", "scope": "read write"}):
            identity = CallerIdentity.from_token("t")
        assert identity.authenticated is False

    def test_token_missing_sub_claim_is_anonymous(self):
        with patch("mcp_server.identity.decode_access_token",
                    return_value={"tenant": "aerospace", "scope": "read"}):
            identity = CallerIdentity.from_token("t")
        assert identity.authenticated is False

    def test_valid_token_resolves_authenticated_identity(self):
        with patch("mcp_server.identity.decode_access_token", return_value={
            "sub": "client-1", "tenant": "aerospace",
            "scope": "read write tenant:aerospace", "type": "m2m",
        }):
            identity = CallerIdentity.from_token("t")
        assert identity.authenticated is True
        assert identity.subject == "client-1"
        assert identity.tenant == "aerospace"
        assert identity.scopes == frozenset({"read", "write", "tenant:aerospace"})
        assert identity.token_type == "m2m"

    def test_has_scope(self):
        identity = CallerIdentity(
            subject="s", tenant="t", scopes=frozenset({"read"}), authenticated=True,
        )
        assert identity.has_scope("read") is True
        assert identity.has_scope("write") is False


class TestResolve:
    def test_resolve_reads_env_var(self, monkeypatch):
        monkeypatch.setenv(TOKEN_ENV_VAR, "token-value")
        with patch("mcp_server.identity.decode_access_token", return_value={
            "sub": "s", "tenant": "aerospace", "scope": "read",
        }) as mock_decode:
            identity = CallerIdentity.resolve()
        # stdio takes its credential from the launcher's environment, so it
        # deliberately does NOT demand a resource audience -- see
        # CallerIdentity.from_token and the MCP authorization specification's
        # carve-out for the stdio transport.
        mock_decode.assert_called_once_with(
            "token-value", audience=None, strict=False,
        )
        assert identity.authenticated is True

    def test_resolve_anonymous_when_env_var_absent(self, monkeypatch):
        monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
        assert CallerIdentity.resolve().authenticated is False


class TestRequestScopedIdentity:
    def test_current_prefers_bound_remote_identity_and_resets(self, monkeypatch):
        monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
        identity = CallerIdentity(
            subject="remote-agent", tenant="automotive", scopes=frozenset({"read"}),
            authenticated=True,
        )
        token = CallerIdentity.bind_request(identity)
        try:
            assert CallerIdentity.current() is identity
        finally:
            CallerIdentity.reset_request(token)
        assert CallerIdentity.current() == CallerIdentity.anonymous()

    def test_from_claims_accepts_verified_scope_list(self):
        identity = CallerIdentity.from_claims(
            {"sub": "user-1", "scope": ["read", "biz:write"], "type": "browser"},
            tenant="aerospace",
        )
        assert identity.authenticated is True
        assert identity.scopes == frozenset({"read", "biz:write"})
