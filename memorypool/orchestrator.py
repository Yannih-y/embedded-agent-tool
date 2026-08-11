"""调度器：任务图 DAG + 流式调度 + 重试/fallback。

任务6 只做机制（显式 DAG，不做 LLM 自动拆解——那是入口层，后面加）。
核心行为（可被测试断言）：
- 依赖未满足的任务不能启动；依赖全 done 才能启动
- 流式调度：某任务一完成，立刻检查并解锁下游，不等整批（不用 gather 批 barrier）
- 失败重试有次数上限；上限到了标 failed，不死循环
- fallback：失败可切备用 agent_type，fallback 链穷尽才真正 failed

执行器（谁来跑一个任务）从 agent_pool 按 agent_type 取；任务6用假执行器验机制，不接真 LLM。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("memorypool.orchestrator")

# 执行器签名：给一个 Task，返回结果（任意）。失败就抛异常。
Executor = Callable[["Task"], Awaitable[Any]]


class TaskStatus(str, Enum):
    PENDING = "pending"     # 等依赖 / 等调度
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"       # 重试+fallback 都穷尽


@dataclass
class Task:
    task_id: str
    agent_type: str                              # 该任务默认交给哪种 agent 执行
    depends_on: list[str] = field(default_factory=list)
    fallback_types: list[str] = field(default_factory=list)  # 失败后依次尝试的备用 agent_type
    max_retries: int = 2                         # 每个 agent_type 的重试次数上限
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    attempts: int = 0                            # 总尝试次数（含重试+fallback），调试/断言用


class Orchestrator:
    """流式 DAG 调度器。

    executors: agent_type -> Executor 的映射（由 agent_pool 提供）。
    """

    def __init__(self, executors: dict[str, Executor]) -> None:
        self._executors = executors

    async def run(self, tasks: list[Task]) -> dict[str, Task]:
        """执行整个 DAG，返回 task_id -> Task（含最终状态/结果）。

        流式调度：用 asyncio 事件驱动，每个任务完成后立即尝试解锁下游，
        不做批 barrier。DAG 有环或依赖缺失会直接报错（防止死等）。
        """
        by_id: dict[str, Task] = {t.task_id: t for t in tasks}
        self._validate_dag(by_id)

        # 记录每个任务的启动顺序，供测试断言「下游在上游之后启动」
        start_order: list[str] = []
        order_lock = asyncio.Lock()

        running: dict[str, asyncio.Task] = {}
        # 已经调度过的任务（防重复启动）
        scheduled: set[str] = set()

        def ready(t: Task) -> bool:
            return t.status == TaskStatus.PENDING and all(
                by_id[d].status == TaskStatus.DONE for d in t.depends_on
            )

        def any_dep_failed(t: Task) -> bool:
            return any(by_id[d].status == TaskStatus.FAILED for d in t.depends_on)

        async def launch(t: Task) -> None:
            async with order_lock:
                start_order.append(t.task_id)
            t.status = TaskStatus.RUNNING
            await self._execute_with_retry(t)

        # 事件循环：反复扫描，把 ready 的任务丢进 running，等任意一个完成再扫
        while True:
            # 依赖失败的任务：级联标记 failed（上游挂了，下游没法跑）
            for t in by_id.values():
                if t.status == TaskStatus.PENDING and any_dep_failed(t):
                    t.status = TaskStatus.FAILED
                    t.error = "upstream_failed"

            # 启动所有当前 ready 的任务
            for t in by_id.values():
                if t.task_id not in scheduled and ready(t):
                    scheduled.add(t.task_id)
                    running[t.task_id] = asyncio.create_task(launch(t))

            if not running:
                break  # 没有在跑的，也没有能启动的 → 结束

            # 流式关键：等「任意一个」完成就回到扫描，立即解锁下游
            done, _ = await asyncio.wait(
                running.values(), return_when=asyncio.FIRST_COMPLETED
            )
            # 清掉已完成的
            finished_ids = [
                tid for tid, at in running.items() if at in done
            ]
            for tid in finished_ids:
                running.pop(tid)

        self._start_order = start_order
        return by_id

    async def _execute_with_retry(self, t: Task) -> None:
        """对一个任务：当前 agent_type 重试到上限，再依次切 fallback_types。"""
        candidates = [t.agent_type, *t.fallback_types]
        for agent_type in candidates:
            executor = self._executors.get(agent_type)
            if executor is None:
                logger.warning("无执行器 agent_type=%s，跳过", agent_type)
                continue
            for _ in range(t.max_retries + 1):
                t.attempts += 1
                try:
                    t.result = await executor(t)
                    t.status = TaskStatus.DONE
                    t.error = None
                    return
                except Exception as e:  # noqa: BLE001 —— 调度器要兜住任何执行异常
                    t.error = str(e)
                    logger.info("任务 %s 用 %s 失败(第%d次): %s",
                                t.task_id, agent_type, t.attempts, e)
        # 所有 agent_type 的重试都穷尽
        t.status = TaskStatus.FAILED

    @staticmethod
    def _validate_dag(by_id: dict[str, Task]) -> None:
        """依赖必须存在 + 无环（拓扑排序检测），否则直接抛，防止运行时死等。"""
        for t in by_id.values():
            for d in t.depends_on:
                if d not in by_id:
                    raise ValueError(f"任务 {t.task_id} 依赖不存在的任务 {d}")
        # Kahn 拓扑排序检测环
        indeg = {tid: 0 for tid in by_id}
        for t in by_id.values():
            for _ in t.depends_on:
                indeg[t.task_id] += 1
        queue = [tid for tid, d in indeg.items() if d == 0]
        seen = 0
        while queue:
            cur = queue.pop()
            seen += 1
            for t in by_id.values():
                if cur in t.depends_on:
                    indeg[t.task_id] -= 1
                    if indeg[t.task_id] == 0:
                        queue.append(t.task_id)
        if seen != len(by_id):
            raise ValueError("DAG 存在环，无法调度")

    @property
    def start_order(self) -> list[str]:
        """上一次 run 的任务启动顺序，测试断言用。"""
        return getattr(self, "_start_order", [])
