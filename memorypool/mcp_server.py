"""MCP 接入口（任务8）：让 Claude Code / Cursor 等 MCP 客户端接入内存池。

决策10：内存池是「唯一独占数据库文件」的服务进程。

MCP 走 stdio 传输，必然由 Claude Code / Cursor 拉起成**独立子进程**——它不可能
跟 uvicorn 那个进程共用同一个 MemoryPool 实例。若在本进程里自建 MemoryPool，
就会出现两个进程各自打开同一份 faiss + SQLite：faiss 是内存索引、写时整体落盘，
后写的会整体覆盖先写的，记忆静默丢失；entity_lock 的进程内 asyncio.Lock 也一并失效。

所以 MCP 层是**薄代理**：自己不碰数据库，把工具调用转成 HTTP 打给唯一的服务进程。
两个口子仍然落同一份数据，但真正的写者只有服务进程一个。

- HTTP（server.py）：唯一持有 MemoryPool、独占数据库文件的进程
- MCP（本文件）：无状态代理，经 MemoryPoolClient 转发

零设置：MemoryPoolClient 默认 auto_start——服务没起时第一次工具调用会自动
把服务拉起为后台守护进程（memorypool.daemon），用户不需要手动起服务。

真实 API（已实测 mcp 1.22.0）：
- FastMCP(name) 构造；@mcp.tool() 装饰器注册；mcp.run(transport='stdio') 启动
- await mcp.list_tools() 列已注册工具
"""

from __future__ import annotations

import os
from typing import Any, Optional, Protocol

from mcp.server.fastmcp import FastMCP

from memorypool.client_sdk import MemoryPoolClient
from memorypool.schema import Tier

# 服务进程地址。MCP 客户端（Claude Code / Cursor）启动本进程时可用环境变量覆盖。
DEFAULT_BASE_URL = os.environ.get("MEMPOOL_BASE_URL", "http://127.0.0.1:8800")


class MemoryBackend(Protocol):
    """MCP 工具依赖的最小后端接口。

    MemoryPoolClient（HTTP 代理，生产路径）与 MemoryPool（进程内直连，仅测试用）
    的 add/search 签名一致，两者都满足此协议。
    """

    def add(
        self,
        content: str,
        user_id: str,
        agent_id: Optional[str] = ...,
        run_id: Optional[str] = ...,
        tier: Tier = ...,
    ) -> dict[str, Any]: ...

    def search(self, query: str, user_id: str, limit: int = ...) -> dict[str, Any]: ...


def build_mcp(
    backend: Optional[MemoryBackend] = None,
    *,
    http_mode: bool = False,
) -> FastMCP:
    """构造 MCP server。

    backend 不传则用 HTTP 代理（生产路径：转发给唯一的服务进程）。
    测试可注入 MemoryPool 直连，省去起服务。

    http_mode=True 用于挂到服务进程内的 streamable-http 端点（见 server.py）：
    - stateless_http：不要求客户端维护 MCP-Session-Id，单次 JSON-RPC POST 即可用
      （AgentClaw 等简版 HTTP MCP 客户端只会发独立 POST，不做会话握手）
    - json_response：响应用纯 JSON 而非 SSE 流，简版客户端才解析得动
    """
    mem: MemoryBackend = backend if backend is not None else MemoryPoolClient(DEFAULT_BASE_URL)
    if http_mode:
        mcp = FastMCP("agent-memory-pool", stateless_http=True, json_response=True)
    else:
        mcp = FastMCP("agent-memory-pool")

    @mcp.tool(description="写一条记忆到共享内存池。同一 user 下的 Agent 都能检索到。")
    def add_memory(
        content: str,
        user_id: str,
        agent_id: str | None = None,
        tier: str = Tier.REALTIME.value,
    ) -> dict:
        result = mem.add(content, user_id=user_id, agent_id=agent_id, tier=Tier(tier))
        return {"added": result}

    @mcp.tool(description="从共享内存池检索记忆，按 user_id 过滤，返回向量命中+相对时间+长期记忆关系。")
    def search_memory(query: str, user_id: str, limit: int = 10) -> dict:
        return mem.search(query, user_id=user_id, limit=limit)

    return mcp


def main() -> None:
    build_mcp().run(transport="stdio")


if __name__ == "__main__":
    main()
