"""Integration tests require docker-compose's neo4j service running locally
(host port 7688 — see docker-compose.yml's neo4j service comment for why not the
Neo4j default 7687). `docker compose up -d neo4j` before running this suite.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from src.embedding.sentence_transformer_provider import SentenceTransformerEmbeddingProvider

import src.core.neo4j_client as neo4j_client_module
from src.core.config import get_settings
from src.core.neo4j_client import Neo4jClient
from src.graph.execution import GraphExecutor
from src.graph.migrations.migration_001_init_schema import run as run_migration


def auth_headers(monkeypatch, workspace_id: str, api_key: str = "test-key") -> dict:
    """Registers `api_key` for `workspace_id` via WORKSPACE_API_KEYS and returns
    the X-Workspace-Id/X-Api-Key header pair every authenticated route now
    requires (api/dependencies.py::verify_api_key). Needed because integration
    tests mint their own workspace ids per-test via uuid4(), so a single static
    env value can't pre-enumerate them. Merges into any already-registered
    workspaces (rather than overwriting) so a single test can call this more
    than once — e.g. to register both the real workspace and a second
    workspace used in a cross-tenant-isolation check."""
    import os

    existing = json.loads(os.environ.get("WORKSPACE_API_KEYS") or "{}")
    existing[workspace_id] = api_key
    monkeypatch.setenv("WORKSPACE_API_KEYS", json.dumps(existing))
    get_settings.cache_clear()
    return {"X-Workspace-Id": workspace_id, "X-Api-Key": api_key}


@pytest.fixture(scope="session")
def embedding_provider() -> "SentenceTransformerEmbeddingProvider":
    """Model loading (~1-2s) happens once per test session, not once per test."""
    # Keep heavyweight ML imports out of pytest collection. Unit/API tests
    # that do not need embeddings must remain fully offline and fast.
    from src.embedding.sentence_transformer_provider import SentenceTransformerEmbeddingProvider

    return SentenceTransformerEmbeddingProvider()


@pytest.fixture(autouse=True)
def _neo4j_test_env(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7688")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "scg_dev_local")
    get_settings.cache_clear()

    # pytest-asyncio gives each test function its own event loop by default,
    # but src.core.neo4j_client.get_neo4j() is a process-wide singleton — a
    # driver created in one test's loop breaks when reused from a later test's
    # (different, and by then closed) loop. API routes call get_neo4j()
    # directly (not the per-test `executor` fixture below), so drop the
    # singleton here and let each test build its own, bound to its own loop.
    # This is a test-isolation artifact only; a real running process has
    # exactly one event loop, so the singleton is correct there.
    neo4j_client_module._client = None
    yield
    neo4j_client_module._client = None
    get_settings.cache_clear()


@pytest.fixture
async def executor(_neo4j_test_env):
    client = Neo4jClient()
    ex = GraphExecutor(client)
    await run_migration(ex)
    yield ex
    await client.close()
