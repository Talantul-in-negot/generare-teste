"""Exercise an authenticated remote MCP gateway using the Streamable HTTP protocol.

The gateway must already be running, normally through Docker Compose or the
Kubernetes MCP service.  This script performs ``initialize`` then ``tools/list``
with a bearer token and writes only the observed HTTP/session metadata.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _post(url: str, payload: dict, token: str, session_id: str = "") -> tuple[int, dict[str, str], str]:
    headers = {
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 -- user-selected local gateway
            return response.status, dict(response.headers.items()), response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read().decode("utf-8", "replace")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8002/mcp")
    parser.add_argument("--token", default=os.environ.get("GRAPHRAG_MCP_TOKEN", ""))
    parser.add_argument("--dev-token", action="store_true",
                        help="Mint a short-lived local development token for the MCP resource")
    parser.add_argument("--tenant", default="local-evidence")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.dev_token:
        from api.auth.jwt import create_access_token
        from graphrag.core.resource_identifiers import mcp_resource
        # The gateway validates the token audience (RFC 8707), so a dev token
        # must name the MCP resource, not the REST API default.
        args.token = create_access_token({
            "sub": "local-smoke-agent", "tenant": args.tenant,
            "scope": f"read tenant:{args.tenant}", "type": "m2m",
        }, audience=mcp_resource())
    if not args.token:
        raise SystemExit("GRAPHRAG_MCP_TOKEN or --token is required")
    status, headers, _body = _post(args.url, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "graphrag-evidence-smoke", "version": "1.0"}},
    }, args.token)
    session_id = headers.get("mcp-session-id", headers.get("Mcp-Session-Id", ""))
    list_status, _list_headers, body = _post(args.url, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    }, args.token, session_id)
    report = {
        "report_schema_version": "remote-mcp-smoke/v1", "url": args.url,
        "initialize_status": status, "tools_list_status": list_status,
        "session_established": bool(session_id),
        "tools_list_response_bytes": len(body.encode("utf-8")),
        "passed": status < 300 and list_status < 300 and bool(session_id),
        "claim_policy": "This verifies one authenticated local gateway run, not production availability.",
    }
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
