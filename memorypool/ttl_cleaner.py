"""细记忆 TTL 清理：到期且已固化才物理删，未固化的保留并告警。

机制（决策11 + 问题9，可被测试断言）：
1. 扫某 user 的实时层细记忆
2. 到期（created_at + ttl < now）且 consolidated=True → 物理删
3. 到期但 consolidated=False → 保留 + 告警（绝不误删没固化的数据）
4. 未到期 → 不动

TTL 时长可配（默认 24h）。清理动作走 pool 门面，不直接碰 mem0。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from memorypool.pool import MemoryPool
from memorypool.schema import Tier
from memorypool.time_util import parse_created_at

logger = logging.getLogger("memorypool.ttl_cleaner")

# 细记忆默认存活时长（秒），24 小时
DEFAULT_TTL_SECONDS = 24 * 3600


@dataclass
class CleanReport:
    """一次清理的结果，供断言/审计。"""

    deleted: list[str] = field(default_factory=list)       # 已删（到期且已固化）
    kept_unconsolidated: list[str] = field(default_factory=list)  # 到期但没固化，保留告警
    alive: list[str] = field(default_factory=list)         # 未到期，不动


class TTLCleaner:
    def __init__(self, pool: MemoryPool, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._pool = pool
        self._ttl = timedelta(seconds=ttl_seconds)

    def clean(self, user_id: str, now: Optional[datetime] = None) -> CleanReport:
        """清理某 user 的过期已固化细记忆，返回清理报告。"""
        now = now or datetime.now(timezone.utc)
        report = CleanReport()

        for m in self._pool.list_memories(user_id, tier=Tier.REALTIME):
            mid = m.get("id")
            if not mid:
                continue

            created = m.get("created_at") or (m.get("metadata") or {}).get("created_at")
            dt = parse_created_at(created) if created else None
            expired = dt is not None and (now - dt) > self._ttl

            if not expired:
                report.alive.append(mid)
                continue

            consolidated = bool((m.get("metadata") or {}).get("consolidated"))
            if consolidated:
                self._pool.delete_memory(mid)
                report.deleted.append(mid)
            else:
                # 到期但没固化：绝不删，保留并告警（问题9：防误删未固化数据）
                logger.warning(
                    "user=%s 细记忆 %s 已到期但未固化，保留（固化可能漏跑或未触发）",
                    user_id, mid,
                )
                report.kept_unconsolidated.append(mid)

        logger.info(
            "user=%s TTL 清理：删 %d，保留未固化 %d，未到期 %d",
            user_id, len(report.deleted), len(report.kept_unconsolidated), len(report.alive),
        )
        return report
