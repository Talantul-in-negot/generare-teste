"""Small dependency-free Streamable HTTP MCP client for local evidence runs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass
class MCPHTTPClient:
    """Stateful JSON-RPC client used only by reproducible local harnesses."""

    url: str
    token: str
    session_id: str = ""
    _next_id: int = field(default=1, init=False)

    def _post(self, method: str, params: dict[str, Any]) -> tuple[int, dict[str, str], str]:
        request_id = self._next_id
        self._next_id += 1
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = Request(
            self.url,
            data=json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310 -- caller chooses local gateway
                return response.status, dict(response.headers.items()), response.read().decode("utf-8", "replace")
        except HTTPError as exc:
            return exc.code, dict(exc.headers.items()), exc.read().decode("utf-8", "replace")

    @staticmethod
    def _json(body: str) -> dict[str, Any]:
        """Decode JSON or the final JSON object in an SSE response."""
        text = body.strip()
        if any(line.startswith("data:") for line in text.splitlines()):
            payloads = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            text = payloads[-1] if payloads else "{}"
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {"result": value}

    def initialize(self) -> dict[str, Any]:
        status, headers, body = self._post("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "graphrag-local-evidence", "version": "1.0"},
        })
        self.session_id = next(
            (value for key, value in headers.items() if key.casefold() == "mcp-session-id"), "",
        )
        payload = self._json(body)
        if status >= 300 or not self.session_id:
            raise RuntimeError(f"MCP initialize failed: status={status}, payload={payload}")
        return payload

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        status, _headers, body = self._post("tools/call", {"name": name, "arguments": arguments})
        payload = self._json(body)
        if status >= 300:
            raise RuntimeError(f"MCP tools/call failed: status={status}, payload={payload}")
        return payload


def tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the JSON object returned in a MCP tool's text content."""
    result = payload.get("result", payload)
    if isinstance(result, dict):
        content = result.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        decoded = json.loads(str(item.get("text", "")))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(decoded, dict):
                        return decoded
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
    return result if isinstance(result, dict) else {"value": result}
