"""Agent 执行器注册表：把 agent_type 映射到「怎么执行一个任务」。

Agent 各自独立进程（决策10），但调度器在服务侧只需要知道「某类任务交给谁跑」。
这里做一个简单注册表：注册 agent_type -> 执行函数。执行函数可以是：
- 假执行器（任务6 验调度机制用）
- 真执行器（经 client_sdk 打到某个 Agent 进程 / 直接调云 LLM）

不做「池化复用」——Agent 是独立进程，不是本进程可复用的对象。这里只做「按类型找执行器」。
"""

from __future__ import annotations

from typing import Awaitable, Callable

Executor = Callable[["object"], Awaitable[object]]


class AgentPool:
    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}

    def register(self, agent_type: str, executor: Executor) -> None:
        """注册一个 agent_type 的执行器。重复注册覆盖。"""
        self._executors[agent_type] = executor

    def unregister(self, agent_type: str) -> None:
        self._executors.pop(agent_type, None)

    def get(self, agent_type: str) -> Executor | None:
        return self._executors.get(agent_type)

    def as_dict(self) -> dict[str, Executor]:
        """给 Orchestrator 用的 executors 映射（快照）。"""
        return dict(self._executors)

    def types(self) -> list[str]:
        return list(self._executors)
