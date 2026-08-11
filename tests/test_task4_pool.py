"""任务4实测：MemoryPool 核心 + 共享可见性 + 关系合并读。

重点验证「共享」的地基：agent A 写的记忆，同 user 下的 agent B 能搜到。
所有测试在 .venv 里真跑向量层（faiss），不糊弄。
"""

import uuid

import pytest

from memorypool.pool import MemoryPool
from memorypool.schema import Tier


@pytest.fixture(scope="module")
def pool():
    return MemoryPool()


def _uid() -> str:
    return f"user_{uuid.uuid4().hex[:8]}"


def test_add_returns_id(pool):
    """写入返回带 id 的记录。"""
    user = _uid()
    res = pool.add("用户喜欢简洁回答", user_id=user, agent_id="claude")
    # 这个 fork 的 add 返回 {'results': [...]}，不是裸 list
    records = res["results"] if isinstance(res, dict) else res
    assert records, "add 应返回非空记录"
    assert records[0].get("id"), "记录应带 id"


def test_shared_visibility_across_agents(pool):
    """共享可见性地基：agent A 写，同 user 下 agent B 搜得到（只按 user_id 过滤）。"""
    user = _uid()
    pool.add("项目用的是 mem0 做底座", user_id=user, agent_id="agent_A")
    # agent B 用同一 user_id 搜，不带 agent_id
    res = pool.search("项目底座是什么", user_id=user)
    hits = res["results"] if isinstance(res, dict) else res
    assert hits, "同 user 下另一个 agent 应能搜到 A 写的记忆"
    texts = " ".join(str(h.get("memory", "")) for h in hits)
    assert "mem0" in texts, "应命中 A 写入的内容"


def test_user_isolation(pool):
    """不同 user 之间隔离：user1 写的，user2 搜不到。"""
    u1, u2 = _uid(), _uid()
    pool.add("user1 的私密项目代号 ALPHA", user_id=u1, agent_id="a")
    res = pool.search("项目代号", user_id=u2)
    hits = res["results"] if isinstance(res, dict) else res
    texts = " ".join(str(h.get("memory", "")) for h in hits)
    assert "ALPHA" not in texts, "user2 不应搜到 user1 的记忆"


def test_search_injects_relative_time(pool):
    """检索结果带相对时间 age 字段（决策7/9）。"""
    user = _uid()
    pool.add("测试时间注入", user_id=user, agent_id="a")
    res = pool.search("测试", user_id=user)
    hits = res["results"] if isinstance(res, dict) else res
    assert hits, "应有命中"
    assert "age" in hits[0], "检索结果应注入相对时间 age 字段"


def test_search_merges_relations(pool):
    """检索结果合并该 user 的长期记忆关系。"""
    from memorypool import relation_store

    user = _uid()
    relation_store.add_relation(user, "用户", "偏好", "简洁回答", created_at="2026-01-01T00:00:00Z")
    pool.add("随便一条记忆", user_id=user, agent_id="a")
    res = pool.search("偏好", user_id=user)
    assert isinstance(res, dict) and "relations" in res, "结果应带 relations"
    assert any(r["dst"] == "简洁回答" for r in res["relations"]), "应合并该 user 的关系"
