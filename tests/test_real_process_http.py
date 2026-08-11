"""真起进程实测：uvicorn 独立服务进程 + Agent 跨进程经 HTTP 接入（决策10）。

这是唯一用「真进程」验证架构的测试——之前全是单进程内 TestClient/直接调 pool。
核心验证：
- uvicorn 真把 server.py 拉起成独立进程，服务进程独占数据库
- 测试进程（扮演 Agent，绝不建 MemoryPool、不碰数据库文件）全程只走 HTTP
- Agent A 进程写 → Agent B（同 user 不同 agent）进程读，搜到 A 写的 → 跨进程同源数据

不依赖云 key：写入走 infer=False，纯本地 fastembed。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

# 工程根：subprocess 起 uvicorn 时的 cwd，保证 memorypool 包可导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    """让 OS 分配一个空闲端口，避开写死的 8800 被占的坑。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server():
    """真起一个 uvicorn 服务进程，轮询 /health 就绪后交给测试；结束时清理干净。"""
    # 独立数据目录：避免与其他起服务的测试模块共享默认 faiss（多进程争写会丢数据）
    data_root = tempfile.mkdtemp(prefix="mempool_realproc_test_")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "MEMPOOL_DATA_ROOT": data_root}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "memorypool.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    try:
        # 轮询直到就绪：起进程 + 初始化 MemoryPool + 加载 fastembed 模型要好几秒
        ready = False
        for _ in range(120):  # 最多 60 秒
            if proc.poll() is not None:
                raise RuntimeError(f"服务进程提前退出，exit={proc.returncode}")
            try:
                r = httpx.get(f"{base}/health", timeout=1.0)
                if r.status_code == 200:
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


def test_cross_process_shared_data(server):
    """Agent A 进程写、Agent B（同 user 不同 agent）进程读，搜到同一份数据。"""
    base = server
    user = f"u_realproc_{int(time.time())}"

    # Agent A 经 HTTP 写一条（infer=False，纯本地，不依赖云 key）
    r_add = httpx.post(
        f"{base}/add",
        json={
            "messages": "跨进程验证记忆_喜欢向量检索",
            "user_id": user,
            "agent_id": "claude",
        },
        timeout=30.0,
    )
    assert r_add.status_code == 200, f"/add 应成功，实际 {r_add.status_code}: {r_add.text}"

    # Agent B（同 user 不同 agent）经 HTTP 读，应搜到 A 写的
    r_search = httpx.post(
        f"{base}/search",
        json={"query": "向量检索", "user_id": user},
        timeout=30.0,
    )
    assert r_search.status_code == 200, f"/search 应成功，实际 {r_search.status_code}"
    data = r_search.json()
    hits = data.get("results", data) if isinstance(data, dict) else data
    assert any(
        "跨进程验证记忆" in m.get("memory", "") for m in hits
    ), f"Agent B 应跨进程搜到 A 写的记忆，实际返回：{hits}"


def test_health_endpoint(server):
    """服务进程的 /health 正常响应（就绪探针本身可用）。"""
    r = httpx.get(f"{server}/health", timeout=5.0)
    assert r.status_code == 200
    assert r.json().get("status") == "ok"
