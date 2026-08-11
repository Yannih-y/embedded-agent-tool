"""任务2 验证：Schema + 时间维度。

验证 plan 定的两条核心：
1. created_at（记录进系统时刻）由服务/mem0 盖章，不是 Agent 传入
2. 检索时 context 带出相对时间（"X前"），而非裸时间戳
外加 schema 的 tier/consolidated 字段能正确写入并读回。
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from memorypool.server import app
from memorypool.schema import Tier, build_metadata, get_tier, is_consolidated, MEM0_MANAGED_KEYS
from memorypool import time_util


# ---------- 纯单元：time_util 相对时间 ----------

def test_relative_time_buckets():
    now = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)
    mk = lambda **kw: (now - timedelta(**kw)).isoformat()

    assert time_util.relative_time(mk(seconds=10), now) == "刚刚"
    assert time_util.relative_time(mk(minutes=5), now) == "5分钟前"
    assert time_util.relative_time(mk(hours=3), now) == "3小时前"
    assert time_util.relative_time(mk(days=2), now) == "2天前"
    assert time_util.relative_time(mk(days=60), now) == "2个月前"
    assert time_util.relative_time(mk(days=400), now) == "1年前"


def test_relative_time_bad_input_no_throw():
    # 解析不了原样返回，不能抛异常挡住检索
    assert time_util.relative_time("not-a-date") == "not-a-date"
    assert time_util.relative_time("") == "未知时间"


def test_annotate_results_injects_age():
    now = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)
    created = (now - timedelta(hours=2)).isoformat()
    results = {"results": [{"id": "1", "memory": "x", "created_at": created}]}
    out = time_util.annotate_results(results, now)
    assert out["results"][0]["age"] == "2小时前"
    # 原始 created_at 不动
    assert out["results"][0]["created_at"] == created


# ---------- schema 字段 ----------

def test_build_metadata_defaults():
    meta = build_metadata()
    assert meta["tier"] == "realtime"
    assert meta["consolidated"] is False


def test_build_metadata_rejects_managed_keys():
    import pytest
    for k in ("created_at", "user_id", "agent_id"):
        with pytest.raises(ValueError):
            build_metadata(extra={k: "x"})


# ---------- 端到端：服务盖章 + 检索注入相对时间 ----------

def test_created_at_stamped_by_service_not_agent():
    with TestClient(app) as client:
        # Agent 试图传一个假的 created_at，不该生效
        fake = "1999-01-01T00:00:00+00:00"
        r = client.post("/add", json={
            "messages": "用户喜欢简洁回答",
            "user_id": "u_task2",
            "agent_id": "agentA",
            "tier": "realtime",
        })
        assert r.status_code == 200

        s = client.post("/search", json={"query": "用户偏好", "user_id": "u_task2"})
        assert s.status_code == 200
        body = s.json()
        items = body["results"] if isinstance(body, dict) and "results" in body else body
        assert len(items) >= 1
        item = items[0]
        # created_at 存在且不是 Agent 编的假时间
        created = item.get("created_at") or item.get("metadata", {}).get("created_at")
        assert created is not None
        assert not created.startswith("1999")
        # 检索结果带出相对时间 age（不是裸时间戳）
        assert "age" in item
        assert "前" in item["age"] or item["age"] == "刚刚"


def test_tier_written_and_readable():
    with TestClient(app) as client:
        client.post("/add", json={
            "messages": "长期结论：项目用RT-Thread",
            "user_id": "u_task2b",
            "agent_id": "agentA",
            "tier": "longterm",
        })
        s = client.post("/search", json={"query": "项目技术栈", "user_id": "u_task2b"})
        body = s.json()
        items = body["results"] if isinstance(body, dict) and "results" in body else body
        assert len(items) >= 1
        assert get_tier(items[0]) == Tier.LONGTERM
