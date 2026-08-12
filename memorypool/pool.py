"""MemoryPool 核心：内存池服务进程内唯一的记忆读写门面。

包住 mem0.Memory，统一处理：
- 写入：infer=False 老实存（决策12 实时层）+ tier/consolidated 标记 + 服务盖 created_at（决策8）
- 读取：向量召回（按 user_id 过滤）+ 长期记忆关系合并 + 相对时间注入（决策7/9）
- 共享可见性：写入记 agent_id 留痕，检索只按 user_id 过滤——同 user 下 Agent 互相可读

真实签名（已实测）：
- mem0.add 用顶层 user_id/agent_id/run_id + infer=False
- mem0.search 必须用 filters={'user_id': ...}，顶层 user_id 会报错
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from mem0 import Memory

from memorypool import relation_store
from memorypool.config import build_mem0_config
from memorypool.schema import Tier, build_metadata
from memorypool.time_util import annotate_results


class MemoryPool:
    """服务进程内唯一实例，独占底层数据库文件（决策10）。"""

    def __init__(self, memory: Optional[Memory] = None) -> None:
        self._mem = memory or Memory.from_config(build_mem0_config())
        # faiss 索引写入不是线程安全的（并发写会损坏索引/丢数据）。
        # 之前 async 端点里同步调用被事件循环串行化，误打误撞躲过了这个问题；
        # 现在写路径丢进线程池（真并行），必须显式串行化所有写动作。
        # 读（search/get_all）不加锁：faiss 并发查询安全，且拿锁会让读排在写后面。
        self._write_lock = threading.Lock()
        relation_store.init_db()

    def add(
        self,
        content: str,
        user_id: str,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        tier: Tier = Tier.REALTIME,
    ) -> dict:
        """写一条记忆。实时层 infer=False 老实存，打 tier 标记，agent_id 留痕。

        写前过内容审查（唯一写入闸口，REST / MCP / SDK / 内部固化全走这里）：
        检索结果会进每个 agent 上下文，注入与凭证在写入面拦截，见 content_guard。
        持写锁：faiss 单写者约束，见 __init__ 说明。
        """
        from memorypool.content_guard import ensure_clean

        ensure_clean(content)
        with self._write_lock:
            return self._mem.add(
                content,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                infer=False,
                metadata=build_metadata(tier=tier),
            )

    def search(
        self,
        query: str,
        user_id: str,
        limit: int = 10,
        with_relations: bool = True,
        run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """检索。向量召回按 user_id 过滤（共享可见性：不带 agent_id，同 user 互读），
        注入相对时间；可选合并该 user 的长期记忆关系。

        run_id：可选窄化到某次协作/任务/会议线程——状态盘点靠纯语义 top-k 会漏
        （2026-08-12 实际漏报过），带 run_id 才是可靠的精确切片。
        """
        filters: dict[str, Any] = {"user_id": user_id}
        if run_id:
            filters["run_id"] = run_id
        results = self._mem.search(query, filters=filters, limit=limit)
        results = annotate_results(results)
        if with_relations:
            relations = relation_store.get_relations(user_id)
            if isinstance(results, dict):
                results = {**results, "relations": relations}
            else:
                results = {"results": results, "relations": relations}
        return results

    def list_memories(
        self,
        user_id: str,
        tier: Optional[Tier] = None,
        top_k: int = 100,
    ) -> list[dict]:
        """列某 user 的记忆。给 tier 就只返回该层（自定义字段在 metadata 子字典里）。"""
        raw = self._mem.get_all(filters={"user_id": user_id}, top_k=top_k)
        items = raw["results"] if isinstance(raw, dict) else raw
        if tier is None:
            return items
        return [
            m for m in items
            if (m.get("metadata") or {}).get("tier") == tier.value
        ]

    def list_by_run(
        self,
        user_id: str,
        run_id: str,
        top_k: int = 100,
    ) -> list[dict]:
        """列某次协作（run_id）里的记忆。下游 Agent 按此读上游同 run 的产出。"""
        raw = self._mem.get_all(
            filters={"user_id": user_id, "run_id": run_id}, top_k=top_k
        )
        return raw["results"] if isinstance(raw, dict) else raw

    def mark_consolidated(self, memory_id: str) -> None:
        """把一条细记忆标记为已固化（TTL 清理前置条件，决策11 + 问题9）。

        update 会覆盖 metadata，须带上 tier 一起写回，否则 tier 丢失。
        """
        with self._write_lock:
            self._mem.update(
                memory_id,
                metadata=build_metadata(tier=Tier.REALTIME, consolidated=True),
            )

    def delete_memory(self, memory_id: str) -> None:
        """物理删一条记忆（TTL 清理用）。"""
        with self._write_lock:
            self._mem.delete(memory_id)
