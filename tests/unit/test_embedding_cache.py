"""Unit tests for graphrag.graph.embedding_cache.EmbeddingCache."""

from __future__ import annotations

import numpy as np
import pytest

from graphrag.graph.embedding_cache import EmbeddingCache, get_embedding_cache


class TestGetSet:
    def test_miss_returns_none(self) -> None:
        cache = EmbeddingCache()
        assert cache.get("aerospace", "FAA", "ORG") is None

    def test_set_then_get_returns_float32_array(self) -> None:
        cache = EmbeddingCache()
        cache.set("aerospace", "FAA", "ORG", [0.1, 0.2, 0.3])
        arr = cache.get("aerospace", "FAA", "ORG")
        assert isinstance(arr, np.ndarray)
        assert arr.dtype == np.float32
        assert list(arr) == pytest.approx([0.1, 0.2, 0.3])


class TestTenantIsolation:
    def test_same_name_type_different_tenant_does_not_collide(self) -> None:
        """Two tenants can have an entity with the same (name, type) and a
        different embedding — the cache must never return one tenant's
        data for another's query."""
        cache = EmbeddingCache()
        cache.set("aerospace", "Apple", "ORG", [1.0, 0.0])
        cache.set("automotive", "Apple", "ORG", [0.0, 1.0])

        aero = cache.get("aerospace", "Apple", "ORG")
        auto = cache.get("automotive", "Apple", "ORG")

        assert list(aero) == pytest.approx([1.0, 0.0])
        assert list(auto) == pytest.approx([0.0, 1.0])

    def test_unset_tenant_returns_none_even_if_other_tenant_has_it(self) -> None:
        cache = EmbeddingCache()
        cache.set("aerospace", "FAA", "ORG", [0.1, 0.2])
        assert cache.get("marketing", "FAA", "ORG") is None


class TestInvalidate:
    def test_invalidate_removes_cached_entry(self) -> None:
        cache = EmbeddingCache()
        cache.set("aerospace", "FAA", "ORG", [0.1, 0.2])
        cache.invalidate("aerospace", [("FAA", "ORG")])
        assert cache.get("aerospace", "FAA", "ORG") is None

    def test_invalidate_absent_key_does_not_raise(self) -> None:
        cache = EmbeddingCache()
        cache.invalidate("aerospace", [("Nonexistent", "ORG")])  # should not raise

    def test_invalidate_only_affects_named_tenant(self) -> None:
        cache = EmbeddingCache()
        cache.set("aerospace", "Apple", "ORG", [1.0, 0.0])
        cache.set("automotive", "Apple", "ORG", [0.0, 1.0])
        cache.invalidate("aerospace", [("Apple", "ORG")])
        assert cache.get("aerospace", "Apple", "ORG") is None
        assert cache.get("automotive", "Apple", "ORG") is not None

    def test_invalidate_only_affects_listed_entities(self) -> None:
        cache = EmbeddingCache()
        cache.set("aerospace", "FAA", "ORG", [0.1, 0.2])
        cache.set("aerospace", "Boeing", "ORG", [0.3, 0.4])
        cache.invalidate("aerospace", [("FAA", "ORG")])
        assert cache.get("aerospace", "FAA", "ORG") is None
        assert cache.get("aerospace", "Boeing", "ORG") is not None


class TestSingletonFactory:
    def test_get_embedding_cache_returns_same_instance(self) -> None:
        assert get_embedding_cache() is get_embedding_cache()
