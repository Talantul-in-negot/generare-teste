"""§11 required API surface: /health, /ready, POST /api/v1/ingestions/crm,
POST /api/v1/ingestions/content-assets, GET /api/v1/ingestions/{id}. Uses
httpx.AsyncClient + ASGITransport (not fastapi.testclient.TestClient, whose
blocking portal conflicts with already being inside pytest-asyncio's event
loop) against the real Neo4j container.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from api.main import app
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_is_always_ok():
    async with _client() as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_reports_ready_after_schema_bootstrap(executor):
    async with _client() as client:
        resp = await client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


async def test_crm_ingestion_requires_workspace_header():
    async with _client() as client:
        resp = await client.post("/api/v1/ingestions/crm", json={"accounts": []})
    assert resp.status_code == 422  # missing required X-Workspace-Id (and X-Api-Key) header


async def test_crm_ingestion_rejects_missing_or_wrong_api_key(executor, monkeypatch):
    workspace_id = f"ws-api-authcheck-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)

    async with _client() as client:
        no_key_resp = await client.post(
            "/api/v1/ingestions/crm", headers={"X-Workspace-Id": workspace_id}, json={"accounts": []}
        )
        assert no_key_resp.status_code == 422  # missing required X-Api-Key header

        wrong_key_resp = await client.post(
            "/api/v1/ingestions/crm",
            headers={"X-Workspace-Id": workspace_id, "X-Api-Key": "not-the-right-key"},
            json={"accounts": []},
        )
        assert wrong_key_resp.status_code == 401

        right_key_resp = await client.post("/api/v1/ingestions/crm", headers=headers, json={"accounts": []})
        assert right_key_resp.status_code == 202


async def test_crm_ingestion_end_to_end_and_status_lookup(executor, monkeypatch):
    workspace_id = f"ws-api-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    other_workspace_headers = auth_headers(monkeypatch, "some-other-workspace")
    account_raw = {
        "Id": "001xxAPI1", "Name": "API Test Corp", "Website": "api-test.com",
        "IsDeleted": False, "MasterRecordId": None,
    }

    async with _client() as client:
        post_resp = await client.post(
            "/api/v1/ingestions/crm",
            headers=headers,
            json={"accounts": [account_raw]},
        )
        assert post_resp.status_code == 202
        body = post_resp.json()
        assert body["state"] == "COMPLETED"
        ingestion_id = body["ingestion_id"]

        get_resp = await client.get(f"/api/v1/ingestions/{ingestion_id}", headers=headers)
        assert get_resp.status_code == 200
        get_body = get_resp.json()
        assert get_body["state"] == "COMPLETED"
        assert get_body["item_results"][0]["outcome"] == "CREATED"

        # a different (authenticated) workspace must never see this job — 404,
        # not 403 (a 403 would confirm the id's existence to a caller who
        # doesn't own it).
        cross_resp = await client.get(f"/api/v1/ingestions/{ingestion_id}", headers=other_workspace_headers)
        assert cross_resp.status_code == 404


async def test_content_asset_ingestion_end_to_end(executor, monkeypatch):
    workspace_id = f"ws-api-content-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    asset_raw = {
        "id": "asset-1", "title": "Pricing Objection Handling",
        "url": "https://showpad.example/asset-1", "type": "pdf",
        "tags": ["pricing", "objection"],
    }

    async with _client() as client:
        post_resp = await client.post(
            "/api/v1/ingestions/content-assets",
            headers=headers,
            json={"division_id": "division-a", "content_assets": [asset_raw]},
        )
        assert post_resp.status_code == 202
        body = post_resp.json()
        assert body["state"] == "COMPLETED"

        get_resp = await client.get(f"/api/v1/ingestions/{body['ingestion_id']}", headers=headers)
        assert get_resp.json()["item_results"][0]["outcome"] == "CREATED"


async def test_transcript_ingestion_end_to_end(monkeypatch):
    workspace_id = f"ws-api-transcript-{uuid4().hex[:8]}"
    headers = auth_headers(monkeypatch, workspace_id)
    raw_call = {
        "id": "call-api-1", "started": "2026-06-15T14:00:00Z", "deleted": False,
        "parties": [{"speakerId": "spk_1", "name": "Buyer", "emailAddress": "buyer@example.com"}],
        "transcript": [
            {"speakerId": "spk_1", "sentences": [{"text": "We are concerned about pricing.", "start": 0, "end": 2000}]},
        ],
    }

    async with _client() as client:
        post_resp = await client.post(
            "/api/v1/ingestions/transcripts",
            headers=headers,
            json={"calls": [raw_call]},
        )
        assert post_resp.status_code == 202
        body = post_resp.json()
        assert body["state"] == "COMPLETED"

        get_resp = await client.get(f"/api/v1/ingestions/{body['ingestion_id']}", headers=headers)
        item = get_resp.json()["item_results"][0]
        assert item["outcome"] == "CREATED"
        assert item["claims_created"] > 0


async def test_get_unknown_ingestion_is_404(monkeypatch):
    headers = auth_headers(monkeypatch, "ws-x")
    async with _client() as client:
        resp = await client.get("/api/v1/ingestions/does-not-exist", headers=headers)
    assert resp.status_code == 404
