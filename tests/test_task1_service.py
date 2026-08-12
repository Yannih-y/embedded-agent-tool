"""任务1 验证：轻量服务能起来，add/search 走本地 embedder（infer=False，不需云 key）。"""

from fastapi.testclient import TestClient

from memorypool.server import app


def test_health_add_search():
    with TestClient(app, base_url="http://127.0.0.1:8800") as client:
        # 服务起来
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        # 写入（实时层 infer=False，纯本地 embedder，不调云 LLM）
        r = client.post(
            "/add",
            json={"messages": "用户喜欢简洁的回答", "user_id": "u1", "agent_id": "claude"},
        )
        assert r.status_code == 200

        # 检索能召回
        r = client.post(
            "/search",
            json={"query": "用户的回答偏好", "user_id": "u1", "limit": 5},
        )
        assert r.status_code == 200
        results = r.json()
        assert "results" in results or isinstance(results, list)
        print("SEARCH RESULT:", results)
