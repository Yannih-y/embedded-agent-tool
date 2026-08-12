"""REST 端点 Host 头防护（DNS rebinding）测试。

/mcp 有 MCP SDK 自带防护，REST 靠 server.py 的 host_guard 中间件。
"""
from fastapi.testclient import TestClient

from memorypool.server import app

LOOP = "http://127.0.0.1:8800"


def test_rebound_host_rejected():
    """伪造非回环 Host（rebinding 后浏览器发的就是攻击域名）→ 403，读写全拒。"""
    with TestClient(app, base_url="http://evil.example.com") as client:
        r = client.post("/search", json={"query": "任意", "user_id": "u"})
        assert r.status_code == 403
        r = client.post(
            "/add", json={"messages": "毒化内容", "user_id": "u"}
        )
        assert r.status_code == 403
        assert "Host" in r.json()["detail"]


def test_loopback_hosts_allowed():
    """127.0.0.1 / localhost 正常放行（health 不依赖 pool 就绪，最轻）。"""
    for base in (LOOP, "http://localhost:8800"):
        with TestClient(app, base_url=base) as client:
            assert client.get("/health").status_code == 200


def test_allowed_hosts_env_extends_whitelist(monkeypatch):
    """MEMPOOL_ALLOWED_HOSTS 显式扩白后，局域网主机名放行；未列的仍拒。"""
    monkeypatch.setenv("MEMPOOL_ALLOWED_HOSTS", "nas.local, 192.168.1.10")
    with TestClient(app, base_url="http://nas.local") as client:
        assert client.get("/health").status_code == 200
    with TestClient(app, base_url="http://other.local") as client:
        assert client.get("/health").status_code == 403


def test_ipv6_bracket_host_allowed():
    """[::1]:8800 形态的 Host 解析正确。

    starlette TestClient 不认 IPv6 字面量 base_url（解析端口就炸），
    所以用回环 base_url + 显式覆盖 Host 头的方式送进中间件。
    """
    with TestClient(app, base_url=LOOP) as client:
        r = client.get("/health", headers={"host": "[::1]:8800"})
        assert r.status_code == 200


def test_host_allowed_parser_units():
    """解析函数的边界：带端口/大小写/IPv6/空值/白名单外。"""
    from memorypool.server import _host_allowed

    assert _host_allowed("127.0.0.1:8800")
    assert _host_allowed("LocalHost")
    assert _host_allowed("[::1]:8800")
    assert _host_allowed("[::1]")
    assert not _host_allowed("")
    assert not _host_allowed("evil.example.com:8800")
