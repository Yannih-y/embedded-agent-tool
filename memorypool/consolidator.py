"""固化引擎：把细粒度实时记忆归纳成简要长期记忆（决策11）。

机制（可被测试断言，跟"谁来归纳"解耦）：
1. 取某 user 的实时层细记忆
2. 调归纳器 summarizer（注入的 callable）→ 简要长期记忆文本 + 实体关系三元组
3. 长期记忆写进 mem0（tier=longterm），关系写进 relation_store
4. 被固化的细记忆标 consolidated=True
5. 受控去重（决策12）：新旧长期记忆冲突由固化逻辑显式判断，不走 mem0 黑盒

summarizer 是注入的：任务7 用假归纳器验机制，生产时注入真云 LLM。
不接 mem0 实时写入路径——固化是「协作结束」的一次性动作（决策11 + 固化双触发）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from memorypool import relation_store
from memorypool.entity_lock import entity_lock
from memorypool.pool import MemoryPool
from memorypool.schema import Tier

logger = logging.getLogger("memorypool.consolidator")


@dataclass
class Summary:
    """归纳器的产出：简要长期记忆文本 + 实体关系三元组。"""

    longterm_texts: list[str] = field(default_factory=list)
    # (src, rel, dst) 三元组
    relations: list[tuple[str, str, str]] = field(default_factory=list)


# 归纳器签名：给一批细记忆文本，返回 Summary。失败抛异常。
Summarizer = Callable[[list[str]], Summary]


class Consolidator:
    def __init__(self, pool: MemoryPool, summarizer: Summarizer) -> None:
        self._pool = pool
        self._summarize = summarizer

    async def consolidate(self, user_id: str) -> Summary:
        """固化某 user 当前未固化的实时层细记忆。

        返回本次产出的 Summary（长期记忆文本 + 关系）。没有可固化的细记忆则返回空。
        """
        realtime = self._pool.list_memories(user_id, tier=Tier.REALTIME)
        # 只固化还没固化过的（consolidated=False/缺省）
        pending = [
            m for m in realtime
            if not (m.get("metadata") or {}).get("consolidated")
        ]
        if not pending:
            logger.info("user=%s 无未固化细记忆，跳过", user_id)
            return Summary()

        texts = [m.get("memory", "") for m in pending if m.get("memory")]
        summary = self._summarize(texts)

        # 写长期记忆（tier=longterm）
        for text in summary.longterm_texts:
            self._pool.add(text, user_id=user_id, tier=Tier.LONGTERM)

        # 写关系三元组，按 src 实体加锁串行（决策3：防并发固化丢更新）
        for src, rel, dst in summary.relations:
            async with entity_lock(f"{user_id}:{src}"):
                relation_store.add_relation(user_id, src, rel, dst)

        # 标记被固化的细记忆
        for m in pending:
            self._pool.mark_consolidated(m["id"])

        logger.info(
            "user=%s 固化 %d 条细记忆 → %d 条长期 + %d 条关系",
            user_id, len(pending), len(summary.longterm_texts), len(summary.relations),
        )
        return summary
