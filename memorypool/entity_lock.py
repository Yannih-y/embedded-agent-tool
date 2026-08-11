"""实体级锁：按 key（实体名/entity_id）分发 asyncio.Lock，串行化同实体的读-改-写。

决策3：所有写入汇聚到内存池「服务进程内」，跨进程问题消失，进程内 asyncio.Lock 即可。
Agent 各自独立进程（决策10），但它们都经 HTTP/MCP 打到同一个服务进程，
真正碰数据库的只有服务进程一个事件循环——所以这里的 asyncio.Lock 是有效的串行化手段。

保护对象：长期记忆关系的读-改-写（多个固化动作可能同时更新同一实体的关系）。
实时层 infer=False 各写各的、不改同一条，不需要走这里。

用法：
    async with entity_lock("用户偏好"):
        rels = get_relations(user, "用户偏好")   # 读
        ...                                        # 改
        add_relation(user, "用户偏好", ...)        # 写
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class EntityLockRegistry:
    """按 key 惰性创建并复用 asyncio.Lock。

    同一 key 拿到同一把锁 → 串行；不同 key 各拿各的 → 并行。
    锁本身很轻，用完不主动回收（避免"回收时正好有人在等"的竞态）；
    单用户本地场景 key 基数有限，不会涨爆。
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        # 保护 _locks 字典本身的创建动作，防止两个协程同时给同一 key 建两把锁
        self._guard = asyncio.Lock()

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    @asynccontextmanager
    async def __call__(self, key: str):
        lock = await self._get_lock(key)
        async with lock:
            yield

    def active_keys(self) -> list[str]:
        """当前已注册的 key，调试/测试用。"""
        return list(self._locks)


# 服务进程内唯一注册表实例
entity_lock = EntityLockRegistry()
