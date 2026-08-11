"""任务7 实测：固化引擎 + TTL 清理。

归纳器/时间都可注入，不依赖真云 LLM、不用等真实时间流逝：
- 固化：假归纳器把细记忆归纳成长期记忆 + 关系，被固化的细记忆标 consolidated
- TTL：now 往后拨模拟到期；到期且已固化的删、到期未固化的保留告警、未到期不动
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from memorypool import relation_store
from memorypool.consolidator import Consolidator, Summary
from memorypool.pool import MemoryPool
from memorypool.schema import Tier
from memorypool.ttl_cleaner import TTLCleaner


def _uid() -> str:
    return f"u_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def pool() -> MemoryPool:
    return MemoryPool()


def _fake_summarizer(texts: list[str]) -> Summary:
    """假归纳器：把细记忆拼成一条长期记忆，抽一条固定关系。"""
    return Summary(
        longterm_texts=[f"归纳:{'/'.join(texts)}"],
        relations=[("用户", "简洁回答", "prefers")],
    )


@pytest.mark.asyncio
async def test_consolidate_produces_longterm_and_relations(pool):
    """固化：细记忆→长期记忆+关系，细记忆标 consolidated。"""
    user = _uid()
    pool.add("用户说想要简洁", user_id=user, agent_id="claude", tier=Tier.REALTIME)
    pool.add("用户不喜欢废话", user_id=user, agent_id="claude", tier=Tier.REALTIME)

    consolidator = Consolidator(pool, _fake_summarizer)
    summary = await consolidator.consolidate(user)

    # 产出长期记忆 + 关系
    assert summary.longterm_texts, "应产出长期记忆文本"
    assert summary.relations, "应产出关系三元组"

    # 关系真写进了 SQLite
    rels = relation_store.get_relations(user, src="用户")
    assert any(r["rel"] == "简洁回答" and r["dst"] == "prefers" for r in rels)

    # 长期记忆真写进了 mem0（tier=longterm）
    longterm = pool.list_memories(user, tier=Tier.LONGTERM)
    assert longterm, "应有长期记忆"

    # 细记忆被标记 consolidated
    realtime = pool.list_memories(user, tier=Tier.REALTIME)
    assert all((m.get("metadata") or {}).get("consolidated") for m in realtime), \
        "所有细记忆应被标记 consolidated"


@pytest.mark.asyncio
async def test_consolidate_skips_when_nothing_pending(pool):
    """没有未固化细记忆时，固化跳过返回空。"""
    user = _uid()
    consolidator = Consolidator(pool, _fake_summarizer)
    summary = await consolidator.consolidate(user)
    assert not summary.longterm_texts and not summary.relations


def test_ttl_deletes_expired_consolidated(pool):
    """TTL：到期且已固化的细记忆被物理删。"""
    user = _uid()
    pool.add("到期已固化", user_id=user, agent_id="a", tier=Tier.REALTIME)
    # 标记为已固化
    m = pool.list_memories(user, tier=Tier.REALTIME)[0]
    pool.mark_consolidated(m["id"])

    # now 往后拨 25 小时（超过默认 24h TTL）
    future = datetime.now(timezone.utc) + timedelta(hours=25)
    cleaner = TTLCleaner(pool)
    report = cleaner.clean(user, now=future)

    assert m["id"] in report.deleted, "到期且已固化应被删"
    assert not pool.list_memories(user, tier=Tier.REALTIME), "删后无细记忆"


def test_ttl_keeps_expired_unconsolidated(pool):
    """TTL：到期但未固化的细记忆保留 + 告警，绝不误删（问题9）。"""
    user = _uid()
    pool.add("到期没固化", user_id=user, agent_id="a", tier=Tier.REALTIME)
    m = pool.list_memories(user, tier=Tier.REALTIME)[0]

    future = datetime.now(timezone.utc) + timedelta(hours=25)
    cleaner = TTLCleaner(pool)
    report = cleaner.clean(user, now=future)

    assert m["id"] in report.kept_unconsolidated, "到期未固化应保留"
    assert not report.deleted, "不应删任何未固化的"
    assert pool.list_memories(user, tier=Tier.REALTIME), "未固化细记忆应还在"


def test_ttl_keeps_alive_not_expired(pool):
    """TTL：未到期的细记忆不动。"""
    user = _uid()
    pool.add("刚写的", user_id=user, agent_id="a", tier=Tier.REALTIME)
    m = pool.list_memories(user, tier=Tier.REALTIME)[0]

    # now 就是现在，肯定没过 24h
    cleaner = TTLCleaner(pool)
    report = cleaner.clean(user)

    assert m["id"] in report.alive, "未到期应保留在 alive"
    assert not report.deleted and not report.kept_unconsolidated
