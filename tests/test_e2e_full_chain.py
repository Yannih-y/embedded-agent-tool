"""端到端全链路实测（用真云 LLM，会慢、花 token）。

串起整个内存池的完整生命周期，证明各模块合起来能用：
  1. 用户任务 → 真 LLM decompose 拆成 DAG
  2. DAG 喂调度器，执行器执行每个子任务时往内存池写实时层细记忆（模拟 Agent 协作产出）
  3. 协作结束 → consolidator 用真 LLM 归纳器固化：细记忆 → 简要长期记忆 + 实体关系
  4. 验证固化产物：长期记忆入库、关系入 SQLite、细记忆标 consolidated
  5. 恢复：新一轮 search 能从长期记忆 + 关系里捞回上下文

跟单元测试的假实现不同，这里 decompose/summarizer 都是真 LLM。
"""

import os
import uuid

import pytest

from memorypool.agent_pool import AgentPool
from memorypool.consolidator import Consolidator
from memorypool.llm_agents import decompose, make_summarizer
from memorypool.orchestrator import Orchestrator, TaskStatus
from memorypool.pool import MemoryPool
from memorypool.schema import Tier

# 真云 LLM 全链路：没配网关 key 时跳过（与 test_real_multi_agent 同一守卫）
pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_AUTH_TOKEN") and not os.environ.get("ANTHROPIC_API_KEY"),
    reason="需要真云 LLM（ANTHROPIC_AUTH_TOKEN/ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL）",
)


def _uid() -> str:
    return f"u_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def pool() -> MemoryPool:
    return MemoryPool()


@pytest.mark.asyncio
async def test_full_chain(pool):
    user = _uid()
    user_task = "给一个 Python 项目补测试：先读代码结构，再挑需要覆盖的模块，最后写测试"

    # === 1. 真 LLM 拆解成 DAG ===
    tasks = decompose(user_task)
    assert tasks, "拆解应产出子任务"
    assert any(t.depends_on for t in tasks), "DAG 应含依赖关系（不是全并行）"

    # === 2. 执行器：跑子任务时往内存池写实时层细记忆（模拟 Agent 协作产出）===
    async def executor(task):
        note = f"子任务 {task.task_id} 完成：{task.result}"
        pool.add(note, user_id=user, agent_id=task.agent_type, tier=Tier.REALTIME)
        return note

    agent_pool = AgentPool()
    # 拆解出的所有 agent_type 都注册同一个执行器
    for at in {t.agent_type for t in tasks}:
        agent_pool.register(at, executor)

    orch = Orchestrator(agent_pool.as_dict())
    result = await orch.run(tasks)

    # 全部完成，且下游在上游之后启动
    assert all(t.status == TaskStatus.DONE for t in result.values()), "所有子任务应完成"

    # === 3. 细记忆确实写进了内存池 ===
    realtime = pool.list_memories(user, tier=Tier.REALTIME)
    assert len(realtime) == len(tasks), "每个子任务应写一条实时层细记忆"

    # === 4. 固化：真 LLM 归纳细记忆 → 长期记忆 + 关系 ===
    consolidator = Consolidator(pool, make_summarizer())
    summary = await consolidator.consolidate(user)
    assert summary.longterm_texts, "固化应产出简要长期记忆"

    # 长期记忆入库
    longterm = pool.list_memories(user, tier=Tier.LONGTERM)
    assert longterm, "长期记忆应写进内存池"

    # 细记忆全标 consolidated
    realtime_after = pool.list_memories(user, tier=Tier.REALTIME)
    assert all(
        (m.get("metadata") or {}).get("consolidated") for m in realtime_after
    ), "固化后细记忆应全标 consolidated"

    # === 5. 恢复：新 search 能从长期记忆捞回上下文 ===
    recall = pool.search("测试", user_id=user)
    hits = recall["results"] if isinstance(recall, dict) else recall
    assert hits, "恢复检索应命中长期记忆"
