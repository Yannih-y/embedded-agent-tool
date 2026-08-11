"""闲置监视器：固化双触发的「闲置超时」兜底半边（决策11 + 固化双触发）。

走调度器的场景由 orchestrator 在 DAG 全完成时精确触发固化；
不走调度器的 MCP 零散场景没有明确「协作结束」信号，靠这里兜底——
每个 user 最后一次写入后，闲置超过阈值就自动固化一次。

机制（可被测试断言，跟"多久算闲置"解耦）：
- touch(user)：每次写入记一下该 user 的最后活动时刻
- 后台循环定期扫描：某 user 闲置 > idle_seconds 且自上次固化后有新写入 → 触发 consolidate
- 已固化过、之后无新写入的 user 不重复触发（防空跑）

阈值用秒，测试塞很短的值验触发时序，不空等、不调真 LLM。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from memorypool.consolidator import Consolidator

logger = logging.getLogger("memorypool.idle_monitor")

DEFAULT_IDLE_SECONDS = 300.0  # 默认闲置 5 分钟触发固化
DEFAULT_POLL_SECONDS = 30.0   # 默认每 30 秒扫一次


class IdleMonitor:
    def __init__(
        self,
        consolidator: Consolidator,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        clock=time.monotonic,
    ) -> None:
        self._consolidator = consolidator
        self._idle = idle_seconds
        self._poll = poll_seconds
        self._clock = clock
        # user -> 最后活动时刻
        self._last_activity: dict[str, float] = {}
        # user -> 触发固化时所依据的活动时刻（防止无新写入重复触发）
        self._last_consolidated_at: dict[str, float] = {}
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def touch(self, user_id: str) -> None:
        """记一次该 user 的写入活动。写入路径每次 add 后调用。"""
        self._last_activity[user_id] = self._clock()

    def _due_users(self, now: float) -> list[str]:
        """当前该触发固化的 user：闲置超阈值 且 自上次固化后有新写入。"""
        due = []
        for user, last in self._last_activity.items():
            if now - last < self._idle:
                continue  # 还没闲够
            # 上次固化依据的活动时刻 == 当前最后活动 → 之后没新写入，不重复触发
            if self._last_consolidated_at.get(user) == last:
                continue
            due.append(user)
        return due

    async def check_once(self, now: Optional[float] = None) -> list[str]:
        """扫一遍，对到期的 user 触发固化。返回本次触发的 user 列表。"""
        now = now if now is not None else self._clock()
        fired = []
        for user in self._due_users(now):
            try:
                await self._consolidator.consolidate(user)
                # 记下这次固化依据的活动时刻，之后无新写入不再触发
                self._last_consolidated_at[user] = self._last_activity[user]
                fired.append(user)
            except Exception as e:  # noqa: BLE001 —— 兜底触发不能因单个 user 失败中断
                logger.error("闲置固化 user=%s 失败：%s", user, e)
        return fired

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.check_once()
            except Exception as e:  # noqa: BLE001
                logger.error("闲置监视循环异常：%s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll)
            except asyncio.TimeoutError:
                pass  # 正常轮询间隔到，继续下一轮

    def start(self) -> None:
        """启动后台监视循环（服务进程 lifespan 里调）。"""
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """停止后台循环（服务关闭时调）。"""
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
