"""任务3重做验证：SQLite 三元组关系表（替代 cognee 图谱）。

重点验证之前 cognee 踩坑的那条：按 src 过滤真的生效，查不存在的实体返回空
（cognee 的 {id:$id} 绑定不生效、查不存在返回别的节点，就是栽在这）。
用临时库文件，跟生产库隔离。
"""

import tempfile
from pathlib import Path

import pytest

from memorypool import relation_store


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "test_rel.db"
    relation_store.init_db(p)
    return p


def test_add_and_get_by_src(db):
    """写一条关系，按 src 查回来。"""
    relation_store.add_relation(
        "user1", "用户偏好", "是", "简洁回答",
        memory_id="mem_001", created_at="2026-07-24T00:00:00Z", db_path=db,
    )
    rows = relation_store.get_relations("user1", src="用户偏好", db_path=db)
    assert len(rows) == 1
    assert rows[0]["dst"] == "简洁回答"
    assert rows[0]["memory_id"] == "mem_001"


def test_filter_actually_works(db):
    """核心：按 src 过滤真的生效——查不存在的实体返回空，不返回别的。"""
    relation_store.add_relation("user1", "项目", "用", "mem0", db_path=db)
    rows = relation_store.get_relations("user1", src="根本不存在的实体", db_path=db)
    assert rows == [], "查不存在的实体必须返回空，不能带出别的关系"


def test_user_isolation(db):
    """多租户隔离：user2 查不到 user1 的关系。"""
    relation_store.add_relation("user1", "A", "r", "B", db_path=db)
    assert relation_store.get_relations("user2", db_path=db) == []
    assert len(relation_store.get_relations("user1", db_path=db)) == 1


def test_upsert_dedup(db):
    """同 (user,src,rel,dst) 重复写 = 覆盖，不产生重复行（固化受控去重）。"""
    relation_store.add_relation("user1", "A", "r", "B", memory_id="m1", created_at="t1", db_path=db)
    relation_store.add_relation("user1", "A", "r", "B", memory_id="m2", created_at="t2", db_path=db)
    rows = relation_store.get_relations("user1", src="A", db_path=db)
    assert len(rows) == 1, "重复三元组应覆盖而非新增"
    assert rows[0]["memory_id"] == "m2"
