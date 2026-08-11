"""任务8 实测：MCP 接入口，与 HTTP 落同一份数据。

MCP 走 stdio，必然被客户端拉起成独立子进程。若它自建 MemoryPool，就会有两个进程
各自打开同一份 faiss + SQLite —— faiss 写时整体落盘，后写的整体覆盖先写的，静默丢数据。
所以 MCP 必须是薄代理：经 HTTP 转发给唯一持有 MemoryPool 的服务进程。

核心验证：
- MCP tool 注册成功（list_tools 含 add_memory / search_memory）
- 默认后端是 HTTP 代理、绝不自建 MemoryPool（回归防线，防「双进程写同一 faiss」复发）
- 经 MCP call_tool 写入 → 真服务进程的 HTTP /search 能搜到，证明同源且只有一个写者
- 真实 API（已实测 mcp 1.22.0）：call_tool(name, arguments) 返回 (blocks, structured)
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import pytest

from memorypool.client_sdk import MemoryPoolClient
from memorypool.mcp_server import build_mcp

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _uid() -> str:
    return f"u_{uuid.uuid4().hex[:8]}"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server():
    """真起唯一的服务进程（它才是持有 MemoryPool、独占数据库的那个）。

    关键：给子进程独立的 MEMPOOL_DATA_ROOT。否则本模块的服务进程与
    test_load_concurrent / test_real_process_http 的服务进程会各自打开
    默认的同一份 faiss —— 多进程各持内存副本、terminate 落盘互相覆盖，
    正是「多进程写同一 faiss 丢数据」（本次 P0-2 要根除的问题）在测试里的复现。
    """
    data_root = tempfile.mkdtemp(prefix="mempool_mcp_test_")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "MEMPOOL_DATA_ROOT": data_root}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "memorypool.server:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    try:
        ready = False
        for _ in range(120):
            if proc.poll() is not None:
                raise RuntimeError(f"服务进程提前退出，exit={proc.returncode}")
            try:
                if httpx.get(f"{base}/health", timeout=1.0).status_code == 200:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not ready:
            raise RuntimeError("服务 60 秒内未就绪")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        shutil.rmtree(data_root, ignore_errors=True)


@pytest.fixture(scope="module")
def mcp(server):
    """MCP 以 HTTP 客户端为后端——与生产路径一致，本进程不碰数据库文件。"""
    return build_mcp(MemoryPoolClient(server))


@pytest.mark.asyncio
async def test_tools_registered(mcp):
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "add_memory" in names, "add_memory 应注册"
    assert "search_memory" in names, "search_memory 应注册"


def test_default_backend_is_http_proxy():
    """回归防线：build_mcp() 默认必须走 HTTP 代理，绝不在本进程自建 MemoryPool。

    自建会导致 MCP 子进程与服务进程各写一份 faiss，后写覆盖先写、记忆静默丢失。
    这里断言 mcp_server 模块既不导入也不构造 MemoryPool。
    """
    import inspect

    from memorypool import mcp_server

    src = inspect.getsource(mcp_server)
    assert "MemoryPool()" not in src, "MCP 层不得自建 MemoryPool（会与服务进程争写 faiss）"
    assert not hasattr(mcp_server, "MemoryPool"), "MCP 层不应导入 MemoryPool"

    default_backend = build_mcp.__defaults__
    assert default_backend == (None,), "backend 默认应为 None（内部回落 HTTP 客户端）"


@pytest.mark.asyncio
async def test_mcp_write_visible_via_http(mcp, server):
    """MCP 写的记忆，经服务进程的 HTTP /search 能搜到 —— 同源，且写者只有服务进程一个。"""
    user = _uid()
    await mcp.call_tool(
        "add_memory",
        {"content": "MCP 写入的记忆_喜欢向量检索", "user_id": user, "agent_id": "cursor"},
    )
    # 独立走 HTTP 检索（不复用 MCP 的后端对象），命中即证明落在服务进程那份数据里
    r = httpx.post(
        f"{server}/search",
        json={"query": "向量检索", "user_id": user},
        timeout=30.0,
    )
    r.raise_for_status()
    body = r.json()
    hits = body.get("results", body) if isinstance(body, dict) else body
    assert any(
        "MCP 写入" in m.get("memory", "") for m in hits
    ), f"MCP 写的记忆应经 HTTP 搜到，实际：{hits}"


@pytest.mark.asyncio
async def test_mcp_search_tool_returns_hits(mcp):
    """经 MCP search_memory 工具检索，返回结构正常。

    真实返回：单元素 list [TextContent]，text 里是 JSON 字符串（已实测）。
    """
    import json

    user = _uid()
    await mcp.call_tool(
        "add_memory",
        {"content": "任务8测试记忆_MCP检索路径", "user_id": user},
    )
    blocks = await mcp.call_tool(
        "search_memory", {"query": "检索路径", "user_id": user}
    )
    payload = json.loads(blocks[0].text)
    hits = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
    assert hits, "MCP search_memory 应返回命中"
