"""真多厂家 Agent 协作实测（要联网、花 token）。

验证你最初的核心目标：不同 agent_type 真的调了不同厂家 LLM，产出经内存池串起来。
按 real_agent 真实签名：register_agents(agent_pool, pool, user_id, run_id, mapping)。
"""

import os
import uuid

import pytest

from memorypool.agent_pool import AgentPool
from memorypool.orchestrator import Orchestrator, Task, TaskStatus
from memorypool.pool import MemoryPool
from memorypool.real_agent import make_provider, register_agents


pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_AUTH_TOKEN") and not os.environ.get("ANTHROPIC_API_KEY"),
    reason="需要网关 key 才能跑真多厂家协作",
)


def _uid() -> str:
    return f"u_{uuid.uuid4().hex[:8]}"


def test_multi_vendor_providers_differ():
    """按名分派到不同 provider 类：claude 系→AnthropicLLM，其它→OpenAILLM。

    只验分派逻辑，用固定名字（不联网），跟网关当前有没有这个型号无关。
    """
    claude = make_provider("claude-anything")
    gpt = make_provider("gpt-anything")

    assert type(claude).__name__ == "AnthropicLLM"
    assert type(gpt).__name__ == "OpenAILLM"


@pytest.mark.asyncio
async def test_vendors_collaborate_via_pool():
    """多厂家 Agent 线性协作，模型名运行时从网关挑（不写死）。

    只验：各家都被调、产出都进内存池、任务全成功。
    """
    pool = MemoryPool()
    user = _uid()
    run = f"run_{uuid.uuid4().hex[:8]}"

    ap = AgentPool()
    # 不传 mapping → 运行时从网关真实清单自动挑多厂家
    mapping = register_agents(ap, pool, user, run)
    assert len(mapping) >= 2, f"应挑出至少两家，实际 {mapping}"
    agents = list(mapping)  # 如 ['claude', 'gpt']

    # 线性 DAG，强制走内存池把上游产出传给下游
    # max_retries：网关会偶发 502，靠 orchestrator 重试兜（这本就是它的能力）
    t1 = Task(task_id="t1", agent_type=agents[0], max_retries=3)
    t1.result = "列出3个Python Web框架的名字"
    t2 = Task(task_id="t2", agent_type=agents[1], depends_on=["t1"], max_retries=3)
    t2.result = "从上文列出的框架里挑一个最适合初学者的，只回框架名"

    orch = Orchestrator(ap.as_dict())
    result = await orch.run([t1, t2])

    assert all(t.status == TaskStatus.DONE for t in result.values()), \
        {tid: (t.status, t.error) for tid, t in result.items()}
    stored = pool.list_by_run(user, run)
    assert len(stored) >= 2, f"应有至少2条协作产出，实际 {len(stored)}"


@pytest.mark.asyncio
async def test_downstream_actually_reads_upstream():
    """内容承接实测：下游产出真的带着只可能来自上游的「信物」。

    这才证明「协作」不是各说各话——t1 产出一个运行时才定的暗号，
    t2 的任务是复述上文出现的暗号；t2 产出里带上那个暗号，
    就证明 t2 真读到了 t1 经内存池传来的产出。
    """
    import uuid as _uuid

    pool = MemoryPool()
    user = _uid()
    run = f"run_{_uuid.uuid4().hex[:8]}"
    # 运行时才生成的暗号，测试里写不死，只能来自 t1→内存池→t2
    token = f"MEMPOOL{_uuid.uuid4().hex[:6].upper()}"

    ap = AgentPool()
    mapping = register_agents(ap, pool, user, run)
    agents = list(mapping)

    t1 = Task(task_id="t1", agent_type=agents[0], max_retries=3)
    t1.result = f"请原样输出这个暗号，不要加别的：{token}"
    t2 = Task(task_id="t2", agent_type=agents[-1], depends_on=["t1"], max_retries=3)
    t2.result = "上文里出现了一个暗号，把它原样复述出来"

    orch = Orchestrator(ap.as_dict())
    result = await orch.run([t1, t2])
    assert all(t.status == TaskStatus.DONE for t in result.values()), \
        {tid: (t.status, t.error) for tid, t in result.items()}

    # t2 的产出里必须出现暗号 —— 只可能是它读到了 t1 经内存池传来的产出
    assert token in (result["t2"].result or ""), \
        f"下游未承接上游暗号，说明协作没真串上。t2产出：{result['t2'].result!r}"
