from graphrag.evidence.mcp_http import MCPHTTPClient, tool_result


def test_sse_payload_with_event_line_is_decoded():
    payload = MCPHTTPClient._json(
        'event: message\ndata: {"jsonrpc":"2.0","result":{"ok":true}}\n\n',
    )

    assert payload == {"jsonrpc": "2.0", "result": {"ok": True}}


def test_tool_result_prefers_json_text_content():
    payload = {
        "result": {
            "content": [
                {"type": "text", "text": "not JSON"},
                {"type": "text", "text": '{"outcome":"executed"}'},
            ],
        },
    }

    assert tool_result(payload) == {"outcome": "executed"}
