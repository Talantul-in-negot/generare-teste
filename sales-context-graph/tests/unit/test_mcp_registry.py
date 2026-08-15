from src.mcp.registry import discover


def test_mcp_discovery_is_deny_by_default_and_tenant_bound():
    assert discover(scopes={"sales:read"}, workspace_id=None) == []
    tools = discover(scopes={"sales:read"}, workspace_id="ws-a")
    assert tools
    assert all(item["scope"] == "sales:read" for item in tools)


def test_mcp_write_capabilities_are_not_visible_to_read_only_callers():
    tools = discover(scopes={"sales:read"}, workspace_id="ws-a")
    assert not any(item["scope"] == "sales:write" for item in tools)
