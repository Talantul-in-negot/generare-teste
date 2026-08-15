"""Unit tests for Settings.retrieval_for() — per-tenant retrieval config merge.

The critical property is the *no-op guarantee*: with no override for a tenant,
retrieval_for returns the global retrieval config unchanged, so an empty
tenant_overrides block cannot alter any tenant's behavior. Overrides, when
present, win over the global default for that tenant only.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from graphrag.core.config import DEV_ENVS, Settings, is_dev_env


def _settings_with_yaml(retrieval: dict) -> Settings:
    """A Settings instance whose YAML block is a controlled test fixture.

    Settings.__init__ loads the real settings.yml, so we overwrite _yaml
    afterward (it's set via object.__setattr__ in __init__ for the same reason
    — pydantic models are otherwise frozen to declared fields)."""
    s = Settings()
    object.__setattr__(s, "_yaml", {"retrieval": retrieval})
    return s


class TestRetrievalForNoOp:
    """With no matching override, the global config passes through untouched."""

    def test_no_tenant_overrides_key_returns_base_identity(self):
        base = {"local_top_k": 10, "rerank_top_k": 5}
        s = _settings_with_yaml(base)
        # Same object identity — no copy, guaranteed no mutation.
        assert s.retrieval_for("aerospace") is s.retrieval

    def test_empty_tenant_overrides_is_noop(self):
        s = _settings_with_yaml({"local_top_k": 10, "tenant_overrides": {}})
        result = s.retrieval_for("aerospace")
        assert result == {"local_top_k": 10}          # tenant_overrides stripped
        assert "tenant_overrides" not in result

    def test_tenant_absent_from_overrides_gets_base(self):
        s = _settings_with_yaml({
            "local_top_k": 10,
            "tenant_overrides": {"marketing": {"rerank_top_k": 8}},
        })
        # aerospace has no override → global defaults, tenant_overrides stripped
        assert s.retrieval_for("aerospace") == {"local_top_k": 10}

    def test_default_tenant_gets_base(self):
        s = _settings_with_yaml({"local_top_k": 10, "tenant_overrides": {}})
        assert s.retrieval_for() == {"local_top_k": 10}


class TestRetrievalForMerge:
    """A present override wins over the global default for that tenant only."""

    def test_override_wins_over_global(self):
        s = _settings_with_yaml({
            "local_top_k": 10,
            "rerank_top_k": 5,
            "tenant_overrides": {"aerospace": {"rerank_top_k": 8}},
        })
        result = s.retrieval_for("aerospace")
        assert result["rerank_top_k"] == 8      # tenant value wins
        assert result["local_top_k"] == 10      # non-overridden key preserved
        assert "tenant_overrides" not in result  # never leaks as a knob

    def test_override_adds_new_key(self):
        # A per-tenant-only knob (e.g. a Phase-2 gate defaulting off globally)
        s = _settings_with_yaml({
            "local_top_k": 10,
            "tenant_overrides": {"aerospace": {"authority_rank_weight": 0.3}},
        })
        assert s.retrieval_for("aerospace")["authority_rank_weight"] == 0.3

    def test_isolation_one_tenant_override_does_not_affect_another(self):
        s = _settings_with_yaml({
            "local_top_k": 10,
            "rerank_top_k": 5,
            "tenant_overrides": {"aerospace": {"rerank_top_k": 8}},
        })
        assert s.retrieval_for("aerospace")["rerank_top_k"] == 8
        assert s.retrieval_for("automotive")["rerank_top_k"] == 5   # untouched
        # And the global block itself is not mutated by the merge.
        assert s.retrieval["rerank_top_k"] == 5

    def test_merge_returns_a_copy_not_the_base(self):
        base_ret = {
            "local_top_k": 10,
            "tenant_overrides": {"aerospace": {"local_top_k": 20}},
        }
        s = _settings_with_yaml(base_ret)
        result = s.retrieval_for("aerospace")
        result["local_top_k"] = 99            # mutate the returned dict
        assert s.retrieval["local_top_k"] == 10   # global unaffected


class TestProductionSettings:
    """Production must fail closed for unsafe deployment defaults."""

    @staticmethod
    def _production(**overrides) -> Settings:
        values = {
            "env": "production",
            "jwt_secret_key": "j" * 64,
            "session_secret_key": "s" * 64,
            "neo4j_password": "strong-neo4j-password",
            "rabbitmq_url": "amqps://worker:strong-password@rabbitmq.example.com/vhost",
            "cors_origins": ["https://graph.example.com"],
        }
        values.update(overrides)
        return Settings(**values)

    def test_accepts_hardened_production_settings(self):
        assert self._production().env == "production"

    @pytest.mark.parametrize(
        ("override", "message"),
        [
            ({"session_secret_key": ""}, "session_secret_key must be explicitly set"),
            ({"session_secret_key": "j" * 64}, "must differ"),
            ({"rabbitmq_url": "amqp://graphrag:graphrag_dev@localhost:5672/"}, "rabbitmq_url"),
            ({"cors_origins": ["http://localhost:8000"]}, "cors_origins"),
        ],
    )
    def test_rejects_insecure_production_settings(self, override, message):
        with pytest.raises(ValidationError, match=message):
            self._production(**override)


class TestEnvFailClosedOnUnset:
    """A missing ENV must be treated as strictly as a real production
    deployment, not silently fall into the dev allow-list.

    Previously `env: str = "development"` meant an unset ENV var landed
    inside DEV_ENVS and every insecure default below was permitted with no
    named environment at all -- fail-open on missing config, not just
    misspelled config. `_env_file=None` is used throughout so these tests
    exercise the field default itself rather than whatever this machine's
    local .env happens to contain.
    """

    def test_unset_env_raises_with_clear_message(self):
        with pytest.raises(ValidationError, match="ENV is not set"):
            Settings(_env_file=None, env="")

    def test_unset_env_field_default_is_empty_not_development(self):
        """Guards against silently reintroducing the old fail-open default."""
        assert Settings.model_fields["env"].default == ""

    def test_empty_string_not_in_dev_allowlist(self):
        assert "" not in DEV_ENVS

    @pytest.mark.parametrize("env_value", list(DEV_ENVS))
    def test_every_dev_allowlist_value_permits_insecure_defaults(self, env_value):
        s = Settings(_env_file=None, env=env_value)
        assert s.jwt_secret_key == "change-me-in-production"  # unchanged default, no raise

    def test_unnamed_non_dev_env_still_rejects_default_secret(self):
        """A typo'd env ("prod" vs "production") must fail exactly like the
        real thing -- this pins the existing allow-list check, not the new
        empty-string case above."""
        with pytest.raises(ValidationError, match="jwt_secret_key must be set"):
            Settings(_env_file=None, env="prod")


class TestIsDevEnv:
    """Single source of truth for the dev/non-dev check, shared by
    config.py's own validator and every route that used to do its own
    exact-match `env == "development"` comparison independently."""

    @pytest.mark.parametrize("env_value", list(DEV_ENVS))
    def test_allowlisted_values_are_dev(self, env_value):
        assert is_dev_env(env_value) is True

    def test_case_and_whitespace_normalized(self):
        assert is_dev_env("  Development  ") is True
        assert is_dev_env("DEV") is True
        assert is_dev_env("Test") is True

    @pytest.mark.parametrize("env_value", ["production", "prod", "Production ", "", "staging"])
    def test_non_dev_values_are_not_dev(self, env_value):
        assert is_dev_env(env_value) is False
