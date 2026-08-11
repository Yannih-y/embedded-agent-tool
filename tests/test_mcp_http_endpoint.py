"""MCP over streamable-http 端点实测（/mcp，挂在服务进程内）。

场景：AgentClaw 这类 HTTP transport 的 MCP 客户端——不维护 MCP-Session-Id、
只发独立 JSON-RPC POST、期待纯 JSON 响应。服务端以 stateless_http +
json_response 模式挂载（见 server.py / build_mcp(http_mode=True)），
后端进程内直连 MemoryPool，不走 HTTP 回环。
"""

from fastapi.testclient import TestClient

from memorypool.server import app

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _rpc(client: TestClient, method: str, params: dict, rpc_id: int) -> dict:
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params},
        headers=HEADERS,
    )
    assert resp.status_code == 200, f"{method} -> {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "error" not in body, f"{method} 返回错误：{body}"
    return body["result"]


def test_mcp_http_stateless_roundtrip():
    """无会话头的独立 POST 完成 initialize / tools/list / 写读闭环。

    base_url 必须用回环地址：MCP SDK 的 transport 层带 DNS rebinding 防护，
    默认只放行 localhost/127.0.0.1 的 Host 头，TestClient 默认的 testserver 会被拒。
    """
    with TestClient(app, base_url="http://127.0.0.1:8800") as client:
        init = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test-http-client", "version": "0"},
            },
            1,
        )
        assert init["serverInfo"]["name"] == "agent-memory-pool"

        tools = _rpc(client, "tools/list", {}, 2)
        names = {t["name"] for t in tools["tools"]}
        assert {"add_memory", "search_memory"} <= names

        _rpc(
            client,
            "tools/call",
            {
                "name": "add_memory",
                "arguments": {
                    "content": "HTTP MCP 端点回归测试记忆",
                    "user_id": "mcp-http-test",
                },
            },
            3,
        )
        found = _rpc(
            client,
            "tools/call",
            {
                "name": "search_memory",
                "arguments": {"query": "回归测试", "user_id": "mcp-http-test"},
            },
            4,
        )
        assert found.get("isError") is not True, f"search_memory 报错：{found}"
