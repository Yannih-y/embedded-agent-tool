"""模型通路 + 密钥健康检查。

血泪教训催生的模块：模型名写死会 400、挑清单第一个会撞坏模型 502、密钥失效会 401。
启动/注册前先探清楚，别跑到一半才撞墙。

分两层：
1. check_key()  —— 密钥有效性：打网关 /v1/models，401=密钥废了，能列=密钥有效
2. probe_models() —— 模型通路：对候选模型各发一个最小探针请求，真调通才算可用
   （不是看它在不在清单里——claude-fable-5 在清单里但持续 502）

错误分诊（已实测网关的真实返回）：
- HTTP 401 authentication_error → 密钥问题
- HTTP 400 invalid_request_error「模型不支持」→ 模型不存在/下架
- HTTP 502 api_error「Upstream ...」→ 模型上游坏了
- 空 key → 本地直接拦，不发请求
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx

from memorypool.config import GATEWAY_BASE, gateway_key, list_gateway_models

logger = logging.getLogger("memorypool.health_check")


class KeyStatus(str, Enum):
    OK = "ok"                 # 密钥有效
    MISSING = "missing"       # 没配 key
    INVALID = "invalid"       # 401，密钥废了
    UNREACHABLE = "unreachable"  # 网关连不上/超时


@dataclass
class KeyCheck:
    status: KeyStatus
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == KeyStatus.OK


def check_key(timeout: float = 10.0) -> KeyCheck:
    """校验网关密钥是否有效。打 /v1/models：401=废，200=有效，连不上=unreachable。"""
    base, key = GATEWAY_BASE, gateway_key()
    if not key:
        return KeyCheck(KeyStatus.MISSING, "未配置网关 key（ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY）")
    if not base:
        return KeyCheck(KeyStatus.MISSING, "未配置网关地址（ANTHROPIC_BASE_URL）")
    try:
        r = httpx.get(
            f"{base}/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
    except Exception as e:  # noqa: BLE001 —— 网络层任何错都归为 unreachable
        return KeyCheck(KeyStatus.UNREACHABLE, f"{type(e).__name__}: {e}")
    if r.status_code == 401:
        return KeyCheck(KeyStatus.INVALID, "网关返回 401 authentication_error，密钥无效")
    if r.status_code >= 400:
        return KeyCheck(KeyStatus.UNREACHABLE, f"网关返回 HTTP {r.status_code}: {r.text[:100]}")
    return KeyCheck(KeyStatus.OK, "密钥有效，网关可列模型")


class ModelStatus(str, Enum):
    OK = "ok"               # 探针调通
    NOT_FOUND = "not_found"  # 400，模型不存在/下架
    UPSTREAM_DOWN = "upstream_down"  # 502，模型上游坏
    ERROR = "error"          # 其它错


@dataclass
class ModelProbe:
    model: str
    status: ModelStatus
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == ModelStatus.OK


def _classify_error(msg: str) -> ModelStatus:
    """按已实测的网关错误信号分诊。"""
    low = msg.lower()
    if "401" in low or "authentication" in low:
        return ModelStatus.ERROR  # 密钥问题该走 check_key，这里不当模型错
    if "400" in low or "not support" in low or "不支持" in low or "invalid_request" in low:
        return ModelStatus.NOT_FOUND
    if "502" in low or "upstream" in low or "api_error" in low:
        return ModelStatus.UPSTREAM_DOWN
    return ModelStatus.ERROR


def _probe_one(model: str) -> ModelProbe:
    """对单个模型发最小探针请求，真调通才算 OK。"""
    # 延迟 import，避免 health_check 反向依赖 real_agent 造成循环
    from memorypool.real_agent import make_provider

    try:
        make_provider(model).generate_response(
            messages=[{"role": "user", "content": "hi"}],
        )
        return ModelProbe(model, ModelStatus.OK, "探针调通")
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        return ModelProbe(model, _classify_error(msg), msg[:120])


async def probe_models(models: list[str]) -> list[ModelProbe]:
    """并发探测一批模型的真实通路（不是看在不在清单里，是真发请求）。"""
    tasks = [asyncio.to_thread(_probe_one, m) for m in models]
    return await asyncio.gather(*tasks)


async def health_report(candidates: Optional[list[str]] = None) -> dict:
    """一次性健康报告：密钥 + 各模型通路。candidates 不传则探网关全清单。

    返回：{"key": KeyCheck, "models": [ModelProbe...], "usable": [可用模型名...]}
    """
    key = check_key()
    if not key.ok:
        # 密钥都不行，模型不用探了
        return {"key": key, "models": [], "usable": []}

    models = candidates if candidates is not None else list_gateway_models()
    probes = await probe_models(models)
    usable = [p.model for p in probes if p.ok]
    return {"key": key, "models": probes, "usable": usable}
