"""长期记忆关系存储：SQLite 三元组表，替代 cognee 图谱。

砍掉 cognee/Kuzu 的理由（plan v3 决策2）：关系需求本质是 (源, 关系, 目标) 三元组，
SQLite 三列就够，用不上图数据库的图遍历。零新增依赖、零新增进程、单文件、
事务干净，跟「服务进程独占数据库」架构天然一致。将来真需要图遍历，导出即可升级。

只给长期记忆用（决策11：实时层走向量，长期层才有关系）。
关系写入由固化引擎（consolidator）调用，不接 mem0 实时写入路径。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from memorypool.config import DB_PATH


def _connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str = DB_PATH) -> None:
    """建关系表。幂等，可重复调用。

    relations 表：
      - src / rel / dst：三元组本体
      - memory_id：溯源，这条关系是从哪条长期记忆固化出来的
      - user_id：多租户隔离（决策4），查询按 user 过滤
      - created_at：服务盖章时刻
    (user_id, src, rel, dst) 唯一，重复写入 = 覆盖（固化受控去重，决策12）。
    """
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS relations (
                user_id    TEXT NOT NULL,
                src        TEXT NOT NULL,
                rel        TEXT NOT NULL,
                dst        TEXT NOT NULL,
                memory_id  TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, src, rel, dst)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_relations_user_src ON relations(user_id, src)"
        )
        conn.commit()
    finally:
        conn.close()


def add_relation(
    user_id: str,
    src: str,
    rel: str,
    dst: str,
    memory_id: Optional[str] = None,
    created_at: str = "",
    db_path: Path | str = DB_PATH,
) -> None:
    """写一条关系三元组。同 (user, src, rel, dst) 重复写则覆盖 memory_id/created_at。"""
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO relations (user_id, src, rel, dst, memory_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, src, rel, dst)
            DO UPDATE SET memory_id=excluded.memory_id, created_at=excluded.created_at
            """,
            (user_id, src, rel, dst, memory_id, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_relations(
    user_id: str,
    src: Optional[str] = None,
    db_path: Path | str = DB_PATH,
) -> list[dict]:
    """查关系。给 src 就查该实体出发的关系，不给就查该 user 的全部。按 user 隔离。"""
    conn = _connect(db_path)
    try:
        if src is not None:
            rows = conn.execute(
                "SELECT src, rel, dst, memory_id, created_at "
                "FROM relations WHERE user_id=? AND src=?",
                (user_id, src),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT src, rel, dst, memory_id, created_at "
                "FROM relations WHERE user_id=?",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
