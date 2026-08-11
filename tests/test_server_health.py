"""服务健康端点实测：/health 轻量自检 + /health/models 深度探针。

/health：启动时查密钥，返回服务/密钥状态（快，不重探）。
/health/models：现探网关密钥 + 各模型通路（慢、花 token，联网）。
"""

import os

import pytest
from fastapi.testclient import TestClient

from memorypool.server import app


def test_health_reports_key_status():
    """/health 带出启动自检的密钥状态 + pool 就绪。"""
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        # 新结构字段都在
        assert body["status"] == "ok"
        assert body["pool_ready"] is True
        assert "key_status" in body
        assert body["key_status"] in {"ok", "missing", "invalid", "unreachable"}


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_AUTH_TOKEN") and not os.environ.get("ANTHROPIC_API_KEY"),
    reason="深度探针需要网关 key",
)
def test_health_models_deep_probe():
    """/health/models 现探密钥 + 模型通路，返回结构完整。"""
    with TestClient(app) as client:
        r = client.get("/health/models")
        assert r.status_code == 200
        body = r.json()
        assert body["key_status"] in {"ok", "missing", "invalid", "unreachable"}
        assert isinstance(body["usable_models"], list)
        assert isinstance(body["probes"], list)
        # 密钥有效时，至少探到一个可用模型（网关此刻不至于全挂）
        if body["key_status"] == "ok":
            assert len(body["usable_models"]) >= 1, f"无可用模型，探针：{body['probes']}"
