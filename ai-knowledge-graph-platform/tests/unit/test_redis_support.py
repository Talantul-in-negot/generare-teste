"""The shared sync Redis factory, and the drift it was created to remove.

Three modules each had their own copy of this. Two were byte-identical; the
third resolved its URL from a different source, so on a deployment configured
one way but not the other, alert history and authentication state disagreed
about whether Redis existed — silently, with nothing failing.

The resolution-order test below is the one that matters: it is what stops that
divergence from being reintroduced by a fourth copy.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from graphrag.core import redis_support as rs


class TestUrlResolution:
    def test_explicit_argument_wins(self):
        with patch.dict(os.environ, {"REDIS_URL": "redis://from-env:6379/0"}):
            assert rs.resolve_redis_url("redis://explicit:6379/1") == "redis://explicit:6379/1"

    def test_environment_beats_settings_yaml(self):
        # Every container and Kubernetes manifest in this repo exports
        # REDIS_URL, while the committed YAML value is a localhost dev default.
        with patch.dict(os.environ, {"REDIS_URL": "redis://from-env:6379/0"}):
            assert rs.resolve_redis_url() == "redis://from-env:6379/0"

    def test_settings_yaml_is_the_fallback_not_ignored(self):
        # The old alerts.py read only the environment, so a settings.yml-only
        # deployment had alerting silently unshared across replicas.
        with patch.dict(os.environ, {"REDIS_URL": ""}):
            resolved = rs.resolve_redis_url()
        assert resolved  # settings.yml supplies a value in this repo
        assert resolved.startswith("redis://")

    def test_unconfigured_resolves_to_empty_not_none(self, monkeypatch):
        import graphrag.core.config as config_module

        class _Cfg:
            retrieval: dict = {}

        monkeypatch.setattr(config_module, "get_settings", lambda: _Cfg())
        with patch.dict(os.environ, {"REDIS_URL": ""}):
            assert rs.resolve_redis_url() == ""

    def test_broken_settings_do_not_raise(self, monkeypatch):
        import graphrag.core.config as config_module

        def _explode():
            raise RuntimeError("config unreadable")

        monkeypatch.setattr(config_module, "get_settings", _explode)
        with patch.dict(os.environ, {"REDIS_URL": ""}):
            # A config problem must not turn into an exception on a path whose
            # entire contract is "return None and let the caller degrade".
            assert rs.resolve_redis_url() == ""


class TestClientConstruction:
    def test_no_url_returns_none_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(rs, "resolve_redis_url", lambda url=None: "")
        assert rs.sync_redis_client() is None

    def test_malformed_url_returns_none(self):
        assert rs.sync_redis_client("not-a-redis-url") is None

    def test_client_carries_bounded_timeouts(self):
        # Long timeouts convert "Redis is unhealthy" into "the request hangs",
        # which is worse than the degradation every caller already handles.
        client = rs.sync_redis_client("redis://localhost:6379/0")
        assert client is not None
        kwargs = client.connection_pool.connection_kwargs
        assert kwargs["socket_connect_timeout"] == rs.CONNECT_TIMEOUT_SECONDS
        assert kwargs["socket_timeout"] == rs.READ_TIMEOUT_SECONDS

    def test_construction_does_not_prove_reachability(self):
        # redis-py connects lazily, so a non-None return says nothing about
        # whether Redis is up. Callers must still guard operations.
        assert rs.sync_redis_client("redis://127.0.0.1:6399/0") is not None


class TestErrorTypes:
    def test_covers_redis_and_stdlib_failures(self):
        types = rs.redis_error_types()
        assert ConnectionError in types
        assert OSError in types
        redis_lib = pytest.importorskip("redis")
        assert redis_lib.exceptions.RedisError in types

    def test_usable_directly_in_an_except_clause(self):
        with pytest.raises(RuntimeError):
            try:
                raise ConnectionError("down")
            except rs.redis_error_types():
                raise RuntimeError("caught as expected")


class TestNoDuplicateImplementations:
    def test_only_one_module_builds_a_sync_redis_client(self):
        """No second copy of the sync factory may reappear.

        Scoped to the *synchronous* client only. The async clients
        (`redis.asyncio`, imported as `aioredis`) legitimately construct their
        own: they run on the event loop and deliberately do not route through
        this module, per its docstring. Matching them here would be a false
        positive -- note that "aioredis.from_url(" contains the substring
        "redis.from_url(", which is exactly the trap this comment exists to
        stop someone re-entering.
        """
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[2]
        # A sync import: `import redis` / `import redis as x`, but NOT
        # `import redis.asyncio as x`.
        sync_import = re.compile(r"^\s*import redis(?:\s+as\s+\w+)?\s*(?:#.*)?$", re.M)
        offenders = []
        for path in root.rglob("*.py"):
            parts = set(path.parts)
            if parts & {"tests", "graphify-out", ".venv", "node_modules", "__pycache__"}:
                continue
            if path.name == "redis_support.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if sync_import.search(text) and "from_url(" in text:
                offenders.append(str(path.relative_to(root)))
        assert not offenders, (
            f"these build a sync Redis client directly instead of using "
            f"graphrag.core.redis_support: {offenders}"
        )
