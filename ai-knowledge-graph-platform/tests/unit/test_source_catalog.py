from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from graphrag.graph.source_catalog import (
    ConnectorRegistry, SourceCatalogRepository, SourceKind, SourceMapping, SourceSystem,
)


def test_source_mapping_digest_is_canonical():
    first = SourceMapping(
        tenant="marketing", source_id="source-1", version="1",
        mapping={"b": 2, "a": {"y": 1, "x": 0}},
    )
    second = SourceMapping(
        tenant="marketing", source_id="source-1", version="1",
        mapping={"a": {"x": 0, "y": 1}, "b": 2},
    )
    assert first.config_digest == second.config_digest


def test_source_mapping_rejects_persisted_secrets():
    with pytest.raises(ValidationError, match="must reference secrets"):
        SourceMapping(
            tenant="marketing", source_id="source-1", version="1",
            mapping={"api_token": "do-not-store-this"},
        )


async def test_source_catalog_is_tenant_scoped():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[])
    source = SourceSystem(
        id="source-1", tenant="marketing", name="Policy repository",
        kind=SourceKind.REPOSITORY,
    )
    repo = SourceCatalogRepository(neo4j)

    assert await repo.upsert_source(source) == "source-1"
    assert neo4j.run.await_args.kwargs["tenant"] == "marketing"
    assert "KGSource" in neo4j.run.await_args.args[0]


def test_connector_registry_rejects_ambiguous_ownership():
    class Connector:
        kind = SourceKind.API

    registry = ConnectorRegistry()
    registry.register(Connector())
    assert registry.get(SourceKind.API).kind == SourceKind.API
    with pytest.raises(ValueError, match="already registered"):
        registry.register(Connector())
