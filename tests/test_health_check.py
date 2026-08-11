"""健康检查实测：密钥校验 + 模型通路探测 + 错误分诊。

- 错误分诊：纯字符串逻辑，不联网
- 密钥校验：坏 key 应判 INVALID/真 key 应判 OK（联网，不花 token）
- 模型探针：真探当前网关的模型，验能区分 OK / 坏模型（联网、少量 token）
"""

import os

import pytest

from memorypool import health_check as hc
from memorypool.health_check import (
    KeyStatus,
    ModelStatus,
    _classify_error,
    check_key,
    health_report,
)

pytestmark_net = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_AUTH_TOKEN") and not os.environ.get("ANTHROPIC_API_KEY"),
    reason="需要网关 key",
)


# ---- 错误分诊：纯逻辑，不联网 ----

def test_classify_400_not_found():
    assert _classify_error("Error code: 400 - 模型不支持: gpt-4o") == ModelStatus.NOT_FOUND
    assert _classify_error("invalid_request_error") == ModelStatus.NOT_FOUND


def test_classify_502_upstream_down():
    assert _classify_error("Error code: 502 - Upstream API request failed") == ModelStatus.UPSTREAM_DOWN
    assert _classify_error("api_error") == ModelStatus.UPSTREAM_DOWN


def test_classify_other_error():
    assert _classify_error("some random boom") == ModelStatus.ERROR


# ---- 密钥校验：坏 key 判 INVALID ----

@pytestmark_net
def test_check_key_invalid(monkeypatch):
    """把 key 换成坏的，check_key 应判 INVALID（401）。"""
    monkeypatch.setattr(hc, "gateway_key", lambda: "sk-invalid-xxxxx")
    result = check_key()
    assert result.status == KeyStatus.INVALID, result.detail


@pytestmark_net
def test_check_key_missing(monkeypatch):
    """没配 key，判 MISSING，不发请求。"""
    monkeypatch.setattr(hc, "gateway_key", lambda: None)
    result = check_key()
    assert result.status == KeyStatus.MISSING


@pytestmark_net
def test_check_key_ok():
    """真 key 应判 OK。"""
    result = check_key()
    assert result.status == KeyStatus.OK, result.detail


# ---- 模型通路探针：真探 ----

@pytestmark_net
@pytest.mark.asyncio
async def test_probe_distinguishes_good_and_bad():
    """探针能区分：好模型 OK，坏模型（claude-fable-5 持续502）UPSTREAM_DOWN。"""
    report = await health_report(["claude-sonnet-5", "claude-fable-5", "gpt-5.6-sol"])
    assert report["key"].ok
    by_model = {p.model: p for p in report["models"]}
    # 好模型应可用
    assert by_model["claude-sonnet-5"].status == ModelStatus.OK
    assert by_model["gpt-5.6-sol"].status == ModelStatus.OK
    # 坏模型应被识别为上游坏，不混进 usable
    assert "claude-fable-5" not in report["usable"]
    assert by_model["claude-fable-5"].status in (ModelStatus.UPSTREAM_DOWN, ModelStatus.NOT_FOUND)


@pytestmark_net
@pytest.mark.asyncio
async def test_verified_mapping_avoids_bad_models():
    """verified_vendor_mapping 挑出的模型都是探针验过能用的，不含坏模型。"""
    from memorypool.real_agent import verified_vendor_mapping
    from memorypool.health_check import _probe_one

    mapping = await verified_vendor_mapping()
    assert mapping, "应挑出至少一家"
    # 挑出的每个模型再探一次，都应真能调通
    for agent_type, model in mapping.items():
        assert model != "claude-fable-5", "不该挑中已知坏模型"
        probe = _probe_one(model)
        assert probe.ok, f"{agent_type}={model} 应真能调通，实际 {probe.status}: {probe.detail}"
