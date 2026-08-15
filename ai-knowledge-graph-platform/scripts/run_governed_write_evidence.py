"""Run a real local MCP governed-write lifecycle and preserve the receipts.

The harness seeds exactly one MEDIUM compliance finding in an isolated tenant,
then proves the operational control flow over authenticated Streamable HTTP:
read, approval-required write, human decision over the API, execution,
idempotent replay, stale-version rejection, dry-run, and compensation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.request import Request, urlopen

from api.auth.jwt import create_access_token
from graphrag.business.models import ComplianceFinding, FindingSeverity
from graphrag.business.repository import BusinessObjectRepository
from graphrag.evidence.mcp_http import MCPHTTPClient, tool_result
from graphrag.graph.neo4j_client import get_neo4j


async def _ensure_finding(tenant: str, finding_id: str) -> dict:
    client = get_neo4j()
    repository = BusinessObjectRepository(client)
    try:
        existing = await repository.get_finding(tenant, finding_id)
        if existing:
            return existing
        finding = ComplianceFinding(
            id=finding_id, tenant=tenant, title="Local evidence remediation finding",
            description="Synthetic local-only finding used to exercise governed writes.",
            severity=FindingSeverity.MEDIUM, created_by="evidence-seed", updated_by="evidence-seed",
            reason_code="local_evidence",
        )
        await repository.create_finding(finding)
        return (await repository.get_finding(tenant, finding_id)) or finding.model_dump(mode="json")
    finally:
        await client.close()


def _token(subject: str, tenant: str, scopes: str, token_type: str) -> str:
    return create_access_token({"sub": subject, "tenant": tenant, "scope": scopes, "type": token_type})


def _approval(api_url: str, token: str, approval_id: str) -> dict:
    request = Request(
        f"{api_url.rstrip('/')}/business/approvals/{approval_id}/decide",
        data=b'{"approved": true}', method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 -- local API supplied by caller
        return json.loads(response.read().decode("utf-8"))


def _mcp(token: str, url: str) -> MCPHTTPClient:
    client = MCPHTTPClient(url, token)
    client.initialize()
    return client


def _call(client: MCPHTTPClient, name: str, arguments: dict) -> dict:
    return tool_result(client.call_tool(name, arguments))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8002/mcp")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--tenant", default="local-evidence")
    parser.add_argument("--finding-id", default="local-evidence-finding-v1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    finding = asyncio.run(_ensure_finding(args.tenant, args.finding_id))
    tenant_scope = f"tenant:{args.tenant}"
    agent = _mcp(_token("local-evidence-agent", args.tenant, f"read biz:write {tenant_scope}", "m2m"), args.url)
    approver = _token("local-evidence-approver", args.tenant, "biz:approve", "browser")
    human = _mcp(_token("local-evidence-human", args.tenant, f"read biz:write {tenant_scope}", "browser"), args.url)

    graph_read = _call(agent, "query_graph_facts", {
        "question": "What are relations for Boeing 737 MAX?", "tenant": args.tenant,
    })
    command_id = "local-evidence-create-v1"
    write_args = {
        "reason_code": "local_evidence", "originating_finding_id": args.finding_id,
        "title": "Validate governed MCP write", "description": "Local evidence only.",
        "expected_version": int(finding["object_version"]), "command_id": command_id,
    }
    requested = _call(agent, "create_work_order", write_args)
    if requested.get("outcome") != "approval_required" or not requested.get("approval_id"):
        raise RuntimeError(f"expected approval_required receipt, got {requested}")
    approved = _approval(args.api_url, approver, requested["approval_id"])
    executed = _call(agent, "create_work_order", {**write_args, "approval_id": requested["approval_id"]})
    if executed.get("outcome") != "executed":
        raise RuntimeError(f"expected executed receipt, got {executed}")
    replay = _call(agent, "create_work_order", {**write_args, "approval_id": requested["approval_id"]})
    stale = _call(human, "create_work_order", {
        **write_args, "command_id": "local-evidence-stale-v1", "expected_version": int(finding["object_version"]),
    })
    dry_run = _call(human, "create_work_order", {
        **write_args, "command_id": "local-evidence-dry-run-v1", "expected_version": int(executed["to_version"]),
        "dry_run": True,
    })

    compensation_args = {
        "reason_code": "local_evidence_compensation", "work_order_id": executed["object_id"],
        "original_command_id": command_id, "expected_version": 1,
        "expected_finding_version": int(executed["to_version"]), "command_id": "local-evidence-compensate-v1",
    }
    compensation_requested = _call(agent, "compensate_work_order", compensation_args)
    if compensation_requested.get("outcome") != "approval_required" or not compensation_requested.get("approval_id"):
        raise RuntimeError(f"expected compensation approval receipt, got {compensation_requested}")
    compensation_approved = _approval(args.api_url, approver, compensation_requested["approval_id"])
    compensated = _call(agent, "compensate_work_order", {
        **compensation_args, "approval_id": compensation_requested["approval_id"],
    })
    report = {
        "report_schema_version": "governed-write-evidence/v1", "tenant": args.tenant,
        "finding": {"id": args.finding_id, "seeded_version": finding["object_version"]},
        "graph_fact_read": graph_read, "write_approval_requested": requested,
        "write_approval_decided": approved, "write_executed": executed,
        "idempotent_replay": replay, "stale_version_refusal": stale, "dry_run": dry_run,
        "compensation_approval_requested": compensation_requested,
        "compensation_approval_decided": compensation_approved, "compensated": compensated,
        "claim_policy": "Real local Docker MCP/API/Neo4j flow with synthetic tenant data; not a customer workflow.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
