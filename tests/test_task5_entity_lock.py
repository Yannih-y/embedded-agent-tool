"""任务5验证：实体级锁串行化同实体的读-改-写，不丢更新。

核心场景（决策3）：多个固化动作并发更新同一实体的关系，是非原子的
"读当前 → 算新值 → 写回"。不加锁会互相覆盖丢更新；加锁串行化不丢。

用一个内存里的计数器模拟"读-改-写"，故意在读和写之间 await（让出事件循环，
制造交错），验证：
- 不加锁：并发 N 次自增，结果 < N（丢更新）
- 加锁：并发 N 次自增，结果 == N（不丢）
"""

import asyncio

import pytest

from memorypool.entity_lock import EntityLockRegistry


class _Counter:
    """模拟一个实体的非原子读-改-写。"""

    def __init__(self) -> None:
        self.value = 0

    async def unsafe_incr(self) -> None:
        cur = self.value          # 读
        await asyncio.sleep(0)    # 让出事件循环，制造交错
        self.value = cur + 1      # 写回


@pytest.mark.asyncio
async def test_without_lock_loses_updates():
    """不加锁，并发读-改-写会丢更新（证明问题真实存在）。"""
    c = _Counter()
    n = 50
    await asyncio.gather(*[c.unsafe_incr() for _ in range(n)])
    assert c.value < n, "不加锁应该丢更新（若没丢说明没制造出交错，测试无意义）"


@pytest.mark.asyncio
async def test_same_key_serialized_no_lost_update():
    """同一实体 key 加锁串行化，并发读-改-写不丢更新。"""
    lock = EntityLockRegistry()
    c = _Counter()
    n = 50

    async def guarded_incr():
        async with lock("用户偏好"):
            await c.unsafe_incr()

    await asyncio.gather(*[guarded_incr() for _ in range(n)])
    assert c.value == n, "加锁后不应丢更新"


@pytest.mark.asyncio
async def test_different_keys_run_parallel():
    """不同实体 key 拿不同锁，互不阻塞（各自 key 内仍正确）。"""
    lock = EntityLockRegistry()
    ca, cb = _Counter(), _Counter()
    n = 30

    async def incr_a():
        async with lock("实体A"):
            await ca.unsafe_incr()

    async def incr_b():
        async with lock("实体B"):
            await cb.unsafe_incr()

    await asyncio.gather(
        *[incr_a() for _ in range(n)],
        *[incr_b() for _ in range(n)],
    )
    assert ca.value == n and cb.value == n
    assert set(lock.active_keys()) == {"实体A", "实体B"}


@pytest.mark.asyncio
async def test_same_key_same_lock_instance():
    """同一 key 每次拿到的是同一把锁（复用，不是每次新建）。"""
    lock = EntityLockRegistry()
    l1 = await lock._get_lock("x")
    l2 = await lock._get_lock("x")
    l3 = await lock._get_lock("y")
    assert l1 is l2
    assert l1 is not l3
