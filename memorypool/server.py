"""内存池服务进程入口（轻量版）。

唯一独占打开数据库文件的进程（决策10）。所有 Agent 经 HTTP 接入。
砍掉 mem0 自带 server 的 postgres/auth/alembic/telemetry，只保留核心 Memory.from_config。

对外两个口子，同一进程同一份数据：
- REST：/add /search /health（curl / Python SDK 用）
- MCP over streamable-http：/mcp（AgentClaw 等 HTTP transport 的 MCP 客户端用；
  stdio MCP 仍走 memorypool.mcp_server 独立进程 → HTTP 代理回本服务）
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from memorypool.health_check import KeyCheck, check_key
from memorypool.mcp_server import build_mcp
from memorypool.pool import MemoryPool
from memorypool.schema import Tier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memorypool")

# 服务进程内唯一 MemoryPool 实例，独占底层数据库文件
_pool: MemoryPool | None = None
# 启动自检结果（密钥状态），供 /health 报告
_key_check: KeyCheck | None = None


def get_pool() -> MemoryPool:
    if _pool is None:
        raise RuntimeError("MemoryPool 尚未初始化")
    return _pool


class _InProcessBackend:
    """MCP /mcp 端点的进程内后端：直调本进程唯一的 MemoryPool。

    不走 MemoryPoolClient 的 HTTP 回环（自己 POST 自己既多一跳，线程池吃紧时
    还可能自锁）。工具函数是同步 def，FastMCP 会丢到工作线程执行，与 REST
    路由的 run_in_threadpool 同一模式。惰性取 get_pool()：MCP app 在模块导入
    期构建，而 _pool 要到 lifespan 才就绪。
    """

    def add(
        self,
        content: str,
        user_id: str,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        tier: Tier = Tier.REALTIME,
    ) -> dict[str, Any]:
        return get_pool().add(
            content, user_id=user_id, agent_id=agent_id, run_id=run_id, tier=tier
        )

    def search(self, query: str, user_id: str, limit: int = 10) -> dict[str, Any]:
        return get_pool().search(query, user_id=user_id, limit=limit)


# /mcp 的当前 ASGI 处理器。放 dict 而不是模块级变量重绑定，闭包好取。
# SDK 约束：StreamableHTTPSessionManager 每个实例只能 run() 一次，所以 FastMCP
# 必须在每次 lifespan 里重建（TestClient 反复进出 lifespan 时尤其）。
_mcp_state: dict[str, Any] = {"app": None}


async def _mcp_asgi(scope, receive, send):  # type: ignore[no-untyped-def]
    """惰性转发到当前 lifespan 构建的 streamable-http app。"""
    inner = _mcp_state["app"]
    if inner is None:
        await send(
            {"type": "http.response.start", "status": 503, "headers": []}
        )
        await send({"type": "http.response.body", "body": b"MCP not ready"})
        return
    await inner(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool, _key_check
    # 启动自检：先查密钥，废了/没配就明确告警（但不拦服务起——
    # 本地 infer=False 写入/检索不依赖云 LLM，密钥只影响调度器那条真 Agent 链）
    _key_check = check_key()
    if _key_check.ok:
        logger.info("密钥自检通过：%s", _key_check.detail)
    else:
        logger.warning(
            "密钥自检未通过（status=%s）：%s —— 本地记忆读写仍可用，"
            "但真 Agent 协作（调云 LLM）会失败",
            _key_check.status.value, _key_check.detail,
        )
    logger.info("初始化 MemoryPool（服务独占数据库）...")
    _pool = MemoryPool()
    logger.info("MemoryPool 就绪")
    # streamable-http 的会话管理器必须在服务期内保持运行，否则 /mcp 一律 500
    mcp = build_mcp(backend=_InProcessBackend(), http_mode=True)
    inner = mcp.streamable_http_app()
    async with mcp.session_manager.run():
        _mcp_state["app"] = inner
        yield
    _mcp_state["app"] = None
    _pool = None
    _key_check = None


app = FastAPI(title="Agent Memory Pool", lifespan=lifespan)

# ---- Host 头防护（DNS rebinding） -------------------------------------------
# /mcp 由 MCP SDK 自带 Host 校验，但 REST（/add /search）此前裸奔：恶意网页把
# 域名 rebind 到 127.0.0.1 后，浏览器视作"同源"，能读走全部记忆、写入毒化内容
# （检索结果会进每个 agent 的上下文，是现成的注入面）。默认只认回环 Host；
# 局域网部署用 MEMPOOL_ALLOWED_HOSTS=nas.local,192.168.1.10 显式扩白。
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _host_allowed(raw_host: str) -> bool:
    host = (raw_host or "").strip().lower()
    if host.startswith("["):  # IPv6 形如 [::1]:8800
        host = host[1 : host.index("]")] if "]" in host else host.lstrip("[")
    else:
        host = host.split(":", 1)[0]
    if host in _LOOPBACK_HOSTS:
        return True
    extra = os.environ.get("MEMPOOL_ALLOWED_HOSTS", "")
    return host in {h.strip().lower() for h in extra.split(",") if h.strip()}


@app.middleware("http")
async def host_guard(request, call_next):
    if not _host_allowed(request.headers.get("host", "")):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=403,
            content={"detail": "Host 头不在白名单（DNS rebinding 防护）——"
                     "本地访问用 127.0.0.1/localhost；局域网部署设 MEMPOOL_ALLOWED_HOSTS"},
        )
    return await call_next(request)


class AddRequest(BaseModel):
    messages: str
    user_id: str
    agent_id: str | None = None
    run_id: str | None = None
    tier: str = Tier.REALTIME.value


class SearchRequest(BaseModel):
    query: str
    user_id: str
    limit: int = 10


@app.get("/health")
async def health():
    """轻量健康探针：服务活着 + 密钥自检状态（启动时查的，不重探，快）。"""
    pool_ready = _pool is not None
    key = _key_check
    return {
        "status": "ok" if pool_ready else "starting",
        "pool_ready": pool_ready,
        "key_status": key.status.value if key else "unknown",
        "key_detail": key.detail if key else "",
    }


@app.get("/health/models")
async def health_models():
    """深度健康报告：现探网关密钥 + 各模型通路（慢，按需调，不放启动路径）。"""
    from memorypool.health_check import health_report

    report = await health_report()
    return {
        "key_status": report["key"].status.value,
        "key_detail": report["key"].detail,
        "usable_models": report["usable"],
        "probes": [
            {"model": p.model, "status": p.status.value, "detail": p.detail}
            for p in report["models"]
        ],
    }


@app.post("/add")
async def add(req: AddRequest):
    pool = get_pool()
    return await run_in_threadpool(
        pool.add,
        req.messages,
        user_id=req.user_id,
        agent_id=req.agent_id,
        run_id=req.run_id,
        tier=Tier(req.tier),
    )


@app.post("/search")
async def search(req: SearchRequest):
    pool = get_pool()
    return await run_in_threadpool(
        pool.search, req.query, user_id=req.user_id, limit=req.limit
    )


# 挂在所有显式路由之后：FastAPI 按注册顺序匹配，/health /add /search 优先命中，
# 其余路径落进 MCP 转发器（内部 streamable app 只服务 /mcp）。最终对外 URL：/mcp
app.mount("/", _mcp_asgi)


def main():
    import uvicorn

    # daemon 自动拉起时经这两个环境变量传监听地址；手动起服务也可用它们改端口
    host = os.environ.get("MEMPOOL_HOST", "127.0.0.1")
    port = int(os.environ.get("MEMPOOL_PORT", "8800"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
