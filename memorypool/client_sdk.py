"""Agent 侧轻量客户端：封装对内存池服务的 HTTP 调用。

Agent 各自独立进程（决策10），通过 localhost HTTP 打到唯一的服务进程。
这层跟调度机制解耦——只是把 /add /search 包成好用的 Python 方法。
MCP 接入口（任务8）是另一条路，与此并行，落同一份数据。
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from memorypool.schema import Tier


class MemoryPoolClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8800", timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def add(
        self,
        content: str,
        user_id: str,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        tier: Tier = Tier.REALTIME,
    ) -> dict[str, Any]:
        resp = httpx.post(
            f"{self._base}/add",
            json={
                "messages": content,
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "tier": tier.value if isinstance(tier, Tier) else tier,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def search(self, query: str, user_id: str, limit: int = 10) -> dict[str, Any]:
        resp = httpx.post(
            f"{self._base}/search",
            json={"query": query, "user_id": user_id, "limit": limit},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def health(self) -> dict[str, Any]:
        resp = httpx.get(f"{self._base}/health", timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()
