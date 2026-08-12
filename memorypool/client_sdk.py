"""Agent 侧轻量客户端：封装对内存池服务的 HTTP 调用。

Agent 各自独立进程（决策10），通过 localhost HTTP 打到唯一的服务进程。
这层跟调度机制解耦——只是把 /add /search 包成好用的 Python 方法。
MCP 接入口（任务8）是另一条路，与此并行，落同一份数据。

零设置（auto_start，默认开）：连接被拒时自动把服务作为后台守护进程拉起
（memorypool.daemon），就绪后重试原请求——用户不需要手动起服务。
只对「连接不上」触发，不吞其它错误；远程地址不代为拉起。
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from memorypool.schema import Tier


class MemoryPoolClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8800",
        timeout: float = 30.0,
        auto_start: bool = True,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._auto_start = auto_start

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST 一发；连接被拒且 auto_start 开 → 自动拉起服务后重试一次。"""
        url = f"{self._base}{path}"
        try:
            resp = httpx.post(url, json=payload, timeout=self._timeout)
        except httpx.ConnectError:
            if not self._auto_start:
                raise
            from memorypool.daemon import ensure_service

            ensure_service(self._base)
            resp = httpx.post(url, json=payload, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def add(
        self,
        content: str,
        user_id: str,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        tier: Tier = Tier.REALTIME,
    ) -> dict[str, Any]:
        return self._post(
            "/add",
            {
                "messages": content,
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "tier": tier.value if isinstance(tier, Tier) else tier,
            },
        )

    def search(
        self,
        query: str,
        user_id: str,
        limit: int = 10,
        run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query, "user_id": user_id, "limit": limit}
        if run_id:
            payload["run_id"] = run_id
        return self._post("/search", payload)

    def list(
        self,
        user_id: str,
        run_id: Optional[str] = None,
        tier: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """全量列出（非向量检索）：备份/导出/状态盘点用，按 run_id/tier 可选过滤。"""
        params: dict[str, Any] = {"user_id": user_id, "limit": limit}
        if run_id:
            params["run_id"] = run_id
        if tier:
            params["tier"] = tier
        url = f"{self._base}/memories"
        try:
            resp = httpx.get(url, params=params, timeout=self._timeout)
        except httpx.ConnectError:
            if not self._auto_start:
                raise
            from memorypool.daemon import ensure_service

            ensure_service(self._base)
            resp = httpx.get(url, params=params, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()["results"]

    def health(self) -> dict[str, Any]:
        """纯探活：不触发自动拉起（想知道「现在起没起」就该拿到真实答案）。"""
        resp = httpx.get(f"{self._base}/health", timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()
