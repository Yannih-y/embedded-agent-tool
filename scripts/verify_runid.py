"""验证 run_id 存取：add 带 run_id 后，run_id 落在返回/get_all 的哪个字段，能否按它过滤读回。

这是真 Agent 流转的命门——下游靠 run_id + task_id 从内存池取上游产出。
"""

import uuid

from memorypool.pool import MemoryPool
from memorypool.schema import Tier

pool = MemoryPool()
user = f"u_{uuid.uuid4().hex[:8]}"
run = f"run_{uuid.uuid4().hex[:8]}"

# 写两条：同 user 同 run，不同 task 前缀
pool.add("[t1] 上游产出：登录模块用 JWT", user_id=user, agent_id="claude", run_id=run, tier=Tier.REALTIME)
pool.add("[t2] 另一条别的 run", user_id=user, agent_id="gpt", tier=Tier.REALTIME)

# 1) list_memories 捞出来，看 run_id 落在哪
items = pool.list_memories(user, tier=Tier.REALTIME)
print("=== list_memories 条数:", len(items))
for m in items:
    print("  memory:", m.get("memory"))
    print("    顶层 run_id:", m.get("run_id"))
    print("    metadata:", m.get("metadata"))

# 2) 直接问底层 get_all 能不能按 run_id 过滤
raw = pool._mem.get_all(filters={"user_id": user, "run_id": run})
res = raw["results"] if isinstance(raw, dict) else raw
print("=== get_all filters run_id 过滤条数:", len(res))
for m in res:
    print("  ", m.get("memory"))
