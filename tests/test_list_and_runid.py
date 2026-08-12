"""GET /memories 全量列出 + search 的 run_id 过滤（REST 面，真池验证 mem0 组合过滤）。"""
from fastapi.testclient import TestClient

from memorypool.server import app

LOOP = "http://127.0.0.1:8800"


def test_list_memories_and_runid_filters():
    with TestClient(app, base_url=LOOP) as client:
        for text, run in (
            ("run-a 第一条产出", "run-a"),
            ("run-a 第二条产出", "run-a"),
            ("run-b 独立产出", "run-b"),
            ("无 run 的散记", None),
        ):
            body = {"messages": text, "user_id": "lst-t"}
            if run:
                body["run_id"] = run
            assert client.post("/add", json=body).status_code == 200

        # 全量列出
        r = client.get("/memories", params={"user_id": "lst-t"})
        assert r.status_code == 200
        all_items = r.json()["results"]
        assert len(all_items) == 4

        # run_id 精确切片（状态盘点的可靠答案）
        r = client.get("/memories", params={"user_id": "lst-t", "run_id": "run-a"})
        items = r.json()["results"]
        assert len(items) == 2
        assert all(m.get("run_id") == "run-a" for m in items)

        # search 带 run_id：结果只落在该 run 内
        r = client.post(
            "/search",
            json={"query": "产出", "user_id": "lst-t", "run_id": "run-b"},
        )
        hits = r.json()["results"]
        assert hits, "run-b 内应有命中"
        assert all(m.get("run_id") == "run-b" for m in hits)

        # 隔离：别的 user 看不到
        r = client.get("/memories", params={"user_id": "someone-else"})
        assert r.json()["results"] == []
