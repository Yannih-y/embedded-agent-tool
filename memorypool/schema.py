"""统一 Memory Schema —— 只定义 mem0 没有的自定义字段。

时间维度（决策7/8/9）：直接复用 mem0 payload 自带的 `created_at`（写入时服务进程盖 UTC
isoformat 时间戳），不另造 recorded_at，避免两个时间字段并存冲突。time_util 负责把
created_at 转成相对时间注入 context。

自定义 metadata 只加 mem0 没有的两个字段：
- tier：记忆分层（realtime 实时协作 / longterm 简要长期），决策11
- consolidated：细粒度实时记忆是否已固化成长期记忆，决策11 + TTL 清理前置条件（问题9）
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional


class Tier(str, Enum):
    """记忆分层。"""

    REALTIME = "realtime"   # 细粒度实时协作记忆：infer=False 老实存，短 TTL
    LONGTERM = "longterm"   # 简要长期记忆：固化产出，跨会话保留，关系入图谱


# mem0 已在 payload 里维护的字段，schema 不重复定义，仅登记以免误当自定义字段
MEM0_MANAGED_KEYS = frozenset(
    {
        "user_id",
        "agent_id",
        "run_id",
        "created_at",      # 记录进系统的 UTC 时刻（决策7/8：服务进程统一盖章）
        "updated_at",
        "hash",
        "data",
        "text_lemmatized",
    }
)

# 内存池自定义 metadata 字段
TIER_KEY = "tier"
CONSOLIDATED_KEY = "consolidated"


def build_metadata(
    tier: Tier = Tier.REALTIME,
    consolidated: bool = False,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """构造写入 mem0 的自定义 metadata。

    只带内存池自己的字段；user_id/agent_id/run_id/created_at 由 mem0 处理。
    """
    meta: dict[str, Any] = {
        TIER_KEY: tier.value,
        CONSOLIDATED_KEY: consolidated,
    }
    if extra:
        for k, v in extra.items():
            if k in MEM0_MANAGED_KEYS:
                raise ValueError(f"metadata 字段 {k!r} 由 mem0 管理，不可自定义覆盖")
            meta[k] = v
    return meta


def get_tier(payload: dict[str, Any]) -> Tier:
    """从 mem0 返回的 payload/metadata 里读 tier，缺省当实时层。"""
    raw = payload.get(TIER_KEY)
    if raw is None and "metadata" in payload:
        raw = payload["metadata"].get(TIER_KEY)
    return Tier(raw) if raw else Tier.REALTIME


def is_consolidated(payload: dict[str, Any]) -> bool:
    """该记忆是否已固化（TTL 清理前置检查用）。"""
    val = payload.get(CONSOLIDATED_KEY)
    if val is None and "metadata" in payload:
        val = payload["metadata"].get(CONSOLIDATED_KEY)
    return bool(val)
