"""内存池服务进程入口（轻量版）。

唯一独占打开数据库文件的进程（决策10）。所有 Agent 经 HTTP 接入。
砍掉 mem0 自带 server 的 postgres/auth/alembic/telemetry，只保留核心 Memory.from_config。
任务8 会在此基础上加 MCP wrapper。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from memorypool.health_check import KeyCheck, check_key
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
    yield
    _pool = None
    _key_check = None


app = FastAPI(title="Agent Memory Pool", lifespan=lifespan)


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


def main():
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8800)


if __name__ == "__main__":
    main()
