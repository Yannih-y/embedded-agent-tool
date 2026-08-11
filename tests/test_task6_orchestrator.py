"""任务6验证：调度器 DAG 流式调度 + 重试/fallback。

只验机制（显式 DAG + 假执行器），不接真 LLM。核心断言（plan v3）：
- 3节点 DAG（A→B→C）依赖完成才启动下游
- 必失败任务重试到上限标 failed，不死循环
- 流式调度：下游在上游 done 之后才启动
- fallback：主 agent_type 失败切备用
- 依赖失败级联：上游 failed，下游标 failed 不启动
"""

import asyncio

import pytest

from memorypool.agent_pool import AgentPool
from memorypool.orchestrator import Orchestrator, Task, TaskStatus


def _ok_executor(record: list[str]):
    async def _run(task: Task):
        record.append(task.task_id)
        return f"result_of_{task.task_id}"
    return _run


@pytest.mark.asyncio
async def test_linear_dag_dependency_order():
    """A→B→C：启动顺序必须 A 在 B 前、B 在 C 前。"""
    record: list[str] = []
    pool = AgentPool()
    pool.register("worker", _ok_executor(record))

    tasks = [
        Task(task_id="A", agent_type="worker"),
        Task(task_id="B", agent_type="worker", depends_on=["A"]),
        Task(task_id="C", agent_type="worker", depends_on=["B"]),
    ]
    orch = Orchestrator(pool.as_dict())
    result = await orch.run(tasks)

    assert all(result[t].status == TaskStatus.DONE for t in ("A", "B", "C"))
    assert orch.start_order == ["A", "B", "C"], "下游必须在上游完成后才启动"


@pytest.mark.asyncio
async def test_failed_task_marks_failed_no_infinite_loop():
    """必失败任务：重试到上限标 failed，run 能正常返回（不死循环）。"""
    async def _always_fail(task: Task):
        raise RuntimeError("boom")

    pool = AgentPool()
    pool.register("bad", _always_fail)

    tasks = [Task(task_id="X", agent_type="bad", max_retries=2)]
    orch = Orchestrator(pool.as_dict())
    # 加超时兜底：真死循环就让测试超时失败，而不是挂住
    result = await asyncio.wait_for(orch.run(tasks), timeout=5)

    assert result["X"].status == TaskStatus.FAILED
    assert result["X"].attempts == 3, "max_retries=2 → 首次+2重试=3 次尝试"


@pytest.mark.asyncio
async def test_fallback_switches_agent_type():
    """主 agent_type 全失败，切 fallback 成功。"""
    async def _fail(task: Task):
        raise RuntimeError("primary down")

    record: list[str] = []
    pool = AgentPool()
    pool.register("primary", _fail)
    pool.register("backup", _ok_executor(record))

    tasks = [Task(task_id="T", agent_type="primary",
                  fallback_types=["backup"], max_retries=1)]
    orch = Orchestrator(pool.as_dict())
    result = await orch.run(tasks)

    assert result["T"].status == TaskStatus.DONE
    assert record == ["T"], "fallback 执行器应真正跑过一次"


@pytest.mark.asyncio
async def test_upstream_failure_cascades():
    """上游失败，下游级联标 failed 且不启动。"""
    async def _fail(task: Task):
        raise RuntimeError("boom")

    record: list[str] = []
    pool = AgentPool()
    pool.register("bad", _fail)
    pool.register("worker", _ok_executor(record))

    tasks = [
        Task(task_id="A", agent_type="bad", max_retries=0),
        Task(task_id="B", agent_type="worker", depends_on=["A"]),
    ]
    orch = Orchestrator(pool.as_dict())
    result = await orch.run(tasks)

    assert result["A"].status == TaskStatus.FAILED
    assert result["B"].status == TaskStatus.FAILED
    assert result["B"].error == "upstream_failed"
    assert record == [], "下游不该启动"


@pytest.mark.asyncio
async def test_diamond_dag_parallel_branches():
    """菱形 DAG：A→(B,C)→D。B/C 都在 A 后、D 前启动。"""
    record: list[str] = []
    pool = AgentPool()
    pool.register("worker", _ok_executor(record))

    tasks = [
        Task(task_id="A", agent_type="worker"),
        Task(task_id="B", agent_type="worker", depends_on=["A"]),
        Task(task_id="C", agent_type="worker", depends_on=["A"]),
        Task(task_id="D", agent_type="worker", depends_on=["B", "C"]),
    ]
    orch = Orchestrator(pool.as_dict())
    result = await orch.run(tasks)

    assert all(result[t].status == TaskStatus.DONE for t in ("A", "B", "C", "D"))
    order = orch.start_order
    assert order.index("A") < order.index("B")
    assert order.index("A") < order.index("C")
    assert order.index("B") < order.index("D")
    assert order.index("C") < order.index("D")


@pytest.mark.asyncio
async def test_cycle_detection():
    """有环 DAG 直接报错，不死等。"""
    pool = AgentPool()
    pool.register("worker", _ok_executor([]))
    tasks = [
        Task(task_id="A", agent_type="worker", depends_on=["B"]),
        Task(task_id="B", agent_type="worker", depends_on=["A"]),
    ]
    orch = Orchestrator(pool.as_dict())
    with pytest.raises(ValueError, match="环"):
        await orch.run(tasks)
