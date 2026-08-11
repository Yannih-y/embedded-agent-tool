"""诊断 e2e：跑真 LLM 全链路，打印归纳出的长期记忆内容 + 各 query 召回，
定位「search('测试') 搜空」到底是 LLM 归纳内容不含相关语义，还是别的问题。"""
import uuid, asyncio
from memorypool.agent_pool import AgentPool
from memorypool.consolidator import Consolidator
from memorypool.llm_agents import decompose, make_summarizer
from memorypool.orchestrator import Orchestrator, TaskStatus
from memorypool.pool import MemoryPool
from memorypool.schema import Tier


async def main():
    pool = MemoryPool()
    user = f"diag_{uuid.uuid4().hex[:6]}"
    user_task = "给一个 Python 项目补测试：先读代码结构，再挑需要覆盖的模块，最后写测试"

    tasks = decompose(user_task)
    print("== decompose ==", len(tasks), "tasks")
    for t in tasks:
        print("  ", t.task_id, "agent=", t.agent_type, "deps=", t.depends_on)

    async def executor(task):
        note = f"子任务 {task.task_id} 完成：{task.result}"
        pool.add(note, user_id=user, agent_id=task.agent_type, tier=Tier.REALTIME)
        return note

    agent_pool = AgentPool()
    for at in {t.agent_type for t in tasks}:
        agent_pool.register(at, executor)
    orch = Orchestrator(agent_pool.as_dict())
    result = await orch.run(tasks)
    print("== orchestrate ==", {tid: t.status.value for tid, t in result.items()})

    realtime = pool.list_memories(user, tier=Tier.REALTIME)
    print("== realtime memories ==", len(realtime))
    for m in realtime:
        print("  ", repr(m.get("memory"))[:100])

    consolidator = Consolidator(pool, make_summarizer())
    summary = await consolidator.consolidate(user)
    print("== consolidate longterm_texts ==", len(summary.longterm_texts))
    for t in summary.longterm_texts:
        print("  LT:", repr(t)[:150])
    print("== consolidate relations ==", summary.relations)

    longterm = pool.list_memories(user, tier=Tier.LONGTERM)
    print("== list_memories(LONGTERM) ==", len(longterm))
    for m in longterm:
        print("  ", repr(m.get("memory"))[:150])

    for q in ["测试", "单元测试", "代码结构", "模块", "覆盖"]:
        res = pool.search(q, user_id=user)
        hits = res["results"] if isinstance(res, dict) else res
        top = hits[0].get("score") if hits else None
        print(f"== search({q!r}) == {len(hits)} hits, top_score={top}")


asyncio.run(main())
