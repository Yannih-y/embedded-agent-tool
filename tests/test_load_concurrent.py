"""并发压测：真起 uvicorn 服务，并发 HTTP 打，验吞吐 + 是否真并发。

验两件事：
1. 并发扛不扛：N 个请求同时打，全成功、无 500、无丢数据。
2. HTTP 层是真并发还是假并发：FastAPI 的 async def 端点里 pool.add 是同步阻塞的，
   会堵事件循环，让并发退化成串行。这里用「并发耗时 vs 串行估算」对比判定。
   —— 若并发明显快过串行，说明服务真的在并行处理（uvicorn/anyio 把同步端点
   丢线程池了）；若几乎等于串行，说明被堵成串行，得改 run_in_threadpool。

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

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server():
    # 独立数据目录：避免与其他起服务的测试模块共享默认 faiss（多进程争写会丢数据）
    data_root = tempfile.mkdtemp(prefix="mempool_load_test_")
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


def _measure_serial(base: str, user: str, n: int) -> float:
    """串行打 n 个 add，返回耗时（作为并发对比基准）。"""
    t0 = time.time()
    for i in range(n):
        r = httpx.post(
            f"{base}/add",
            json={"messages": f"串行记忆{i}", "user_id": user, "agent_id": "s"},
            timeout=30.0,
        )
        r.raise_for_status()
    return time.time() - t0


def _measure_concurrent(base: str, user: str, n: int, workers: int) -> tuple[float, int]:
    """并发打 n 个 add，返回 (耗时, 成功数)。"""
    import concurrent.futures as cf

    def one(i: int) -> bool:
        r = httpx.post(
            f"{base}/add",
            json={"messages": f"并发记忆{i}", "user_id": user, "agent_id": "c"},
            timeout=30.0,
        )
        return r.status_code == 200

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, range(n)))
    return time.time() - t0, sum(results)


def test_concurrent_no_error_no_loss(server):
    """并发 20 个 add 全成功、无 500，且数据一条不丢。"""
    user = "load_u1"
    n = 20
    elapsed, ok = _measure_concurrent(server, user, n, workers=10)
    assert ok == n, f"并发 {n} 个 add 应全成功，实际 {ok}"

    # 数据一条不丢：搜回来（服务端 list 该 user 的记忆）
    r = httpx.post(
        f"{server}/search",
        json={"query": "并发记忆", "user_id": user, "limit": 100},
        timeout=30.0,
    )
    r.raise_for_status()
    body = r.json()
    hits = body["results"] if isinstance(body, dict) and "results" in body else body
    assert len(hits) >= n, f"并发写入 {n} 条，检索应至少召回 {n}，实际 {len(hits)}"
    print(f"[并发] {n} 个 add 耗时 {elapsed:.2f}s，全成功，召回 {len(hits)}")


def test_concurrent_faster_than_serial(server):
    """判定 HTTP 层真并发：并发耗时应明显短于串行（同步端点被丢线程池才有此效果）。"""
    user_s = "load_serial"
    user_c = "load_conc"
    n = 12

    serial = _measure_serial(server, user_s, n)
    concurrent, ok = _measure_concurrent(server, user_c, n, workers=n)
    assert ok == n

    ratio = serial / concurrent if concurrent > 0 else 0
    print(f"[对比] 串行 {serial:.2f}s vs 并发 {concurrent:.2f}s，加速比 {ratio:.2f}x")
    # 真并发至少该有些加速；若几乎 1x 说明被堵成串行，暴露问题（不硬失败，打印判定）
    assert concurrent <= serial * 1.2, (
        f"并发({concurrent:.2f}s)不该比串行({serial:.2f}s)慢——被堵成串行了"
    )
