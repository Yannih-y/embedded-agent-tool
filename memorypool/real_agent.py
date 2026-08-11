"""真 Agent 执行器：把不同厂家的 LLM 接进调度器（你最初「打通厂家壁垒」的目标）。

已实测的关键事实（验证阶段跑出来的，不是假设）：
- 环境只有一个 key，但那个中转是聚合网关，一 key 通吃 claude/gpt/deepseek/glm...
- 网关按模型分两套协议：claude 系走 anthropic 端点 /v1/messages；
  非 claude 走 OpenAI 端点 /v1/chat/completions（base_url 要带 /v1）
- mem0 的 anthropic provider 调 claude 通；openai provider 配 openai_base_url 调 gpt/deepseek/glm 全通
- 执行器只拿到 Task，拿不到上游 result；下游靠 user_id+run_id 从内存池读上游产出

所以「多厂家」= 按 model 名分派 provider，全走 mem0 LlmFactory 抽象，不自己直连。
"""

from __future__ import annotations

import logging
from typing import Optional

from mem0.utils.factory import LlmFactory

from memorypool.config import (
    anthropic_gateway_base,
    gateway_key,
    openai_gateway_base,
)
from memorypool.orchestrator import Task
from memorypool.pool import MemoryPool
from memorypool.schema import Tier

logger = logging.getLogger("memorypool.real_agent")


def make_provider(model: str):
    """按 model 名分派厂家 provider（多厂家的开关）。

    claude 系 → anthropic provider（走网关 /v1/messages）；
    其它（gpt/deepseek/glm/qwen/minimax...）→ openai provider（走网关 /v1/chat/completions）。
    """
    key = gateway_key()
    if model.lower().startswith("claude"):
        return LlmFactory.create(
            "anthropic",
            {
                "model": model,
                "api_key": key,
                "anthropic_base_url": anthropic_gateway_base() or None,
            },
        )
    return LlmFactory.create(
        "openai",
        {
            "model": model,
            "api_key": key,
            "openai_base_url": openai_gateway_base(),
        },
    )


class RealAgent:
    """一个厂家 LLM 的执行器：读上游产出 → 干活 → 产出写回内存池。

    绑定到某个 agent_type（如 'claude'/'gpt'/'deepseek'），调度器按 agent_type 找到它。
    """

    def __init__(
        self,
        model: str,
        pool: MemoryPool,
        user_id: str,
        run_id: str,
        system_prompt: str = "你是协作 Agent，根据任务和上游产出给出你的结果，简洁明确。",
    ) -> None:
        self._model = model
        self._llm = make_provider(model)
        self._pool = pool
        self._user_id = user_id
        self._run_id = run_id
        self._system = system_prompt

    def _upstream_context(self, task: Task) -> str:
        """从内存池读本次协作（run_id）里已有的产出，拼成上游上下文。

        执行器拿不到上游 Task 的 result，只能走内存池：上游写、下游读。
        """
        items = self._pool.list_by_run(self._user_id, self._run_id)
        if not items:
            return ""
        lines = []
        for m in items:
            who = (m.get("metadata") or {}).get("by_agent") or m.get("agent_id") or "?"
            lines.append(f"[{who}] {m.get('memory', '')}")
        return "\n".join(lines)

    async def __call__(self, task: Task) -> str:
        """执行器入口：调度器 await 这个。任务描述在 task.result（decompose 塞的 desc）。"""
        desc = task.result if isinstance(task.result, str) and task.result else task.task_id
        context = self._upstream_context(task)
        user_msg = f"任务：{desc}"
        if context:
            user_msg += f"\n\n已有的上游产出：\n{context}"

        resp = self._llm.generate_response(
            messages=[
                {"role": "system", "content": self._system},
                {"role": "user", "content": user_msg},
            ],
        )
        text = resp if isinstance(resp, str) else str(resp)

        # 产出写回内存池（细记忆），标明是哪个 agent 干的，供下游读
        self._pool.add(
            f"任务[{task.task_id}]产出：{text}",
            user_id=self._user_id,
            agent_id=task.agent_type,
            run_id=self._run_id,
            tier=Tier.REALTIME,
        )
        return text


def default_vendor_mapping() -> dict[str, str]:
    """运行时从网关真实清单挑出多厂家映射（不写死模型名）。

    血泪教训：写死 gpt-4o/claude-sonnet-4.5 某天就全 400。这里问网关拿当前
    可用清单，各厂家挑第一个非 thinking 的稳定型号，配成 agent_type -> model。
    网关拿不到清单时退回一个保守猜测（仅兜底，仍可能失效）。
    """
    from memorypool.config import list_gateway_models

    models = list_gateway_models()

    def pick(prefix: str, prefer: tuple[str, ...]) -> Optional[str]:
        """挑某厂家的模型：优先挑名字含 prefer 关键词的稳定款，
        再退回该厂家任意非 thinking 款。

        血泪教训：不能只挑「清单第一个」——清单第一个可能是坏模型
        （实测 claude-fable-5 持续 502），挑中就整条链崩。sonnet 系最稳，优先。
        """
        cands = [
            m for m in models
            if m.lower().startswith(prefix) and "thinking" not in m.lower()
        ]
        for kw in prefer:
            for m in cands:
                if kw in m.lower():
                    return m
        return cands[0] if cands else None

    mapping: dict[str, str] = {}
    # claude 优先 sonnet（主力最稳），其次 haiku，最后才别的
    claude = pick("claude", prefer=("sonnet", "haiku"))
    gpt = pick("gpt", prefer=("sol", "terra", "luna"))
    if claude:
        mapping["claude"] = claude
    if gpt:
        mapping["gpt"] = gpt

    # 网关啥也没返回时的保守兜底（可能已失效，仅防空）
    if not mapping:
        mapping = {"claude": "claude-sonnet-5", "gpt": "gpt-5.6-sol"}
    return mapping


async def verified_vendor_mapping() -> dict[str, str]:
    """挑模型时用健康探针验过通路，只选真能调通的（从根上杜绝挑中坏模型）。

    比 default_vendor_mapping 更稳：先探清全清单哪些真可用，再各厂家挑一个可用的。
    要联网、发探针请求（少量 token），启动时跑一次值得。探不出可用时退回 default。
    """
    from memorypool.health_check import check_key, probe_models
    from memorypool.config import list_gateway_models

    if not check_key().ok:
        # 密钥都不行，探也白探，退回默认（调用方应先看 check_key）
        return default_vendor_mapping()

    models = list_gateway_models()
    probes = await probe_models(models)
    usable = [p.model for p in probes if p.ok]

    def pick(prefix: str, prefer: tuple[str, ...]) -> Optional[str]:
        cands = [
            m for m in usable
            if m.lower().startswith(prefix) and "thinking" not in m.lower()
        ]
        for kw in prefer:
            for m in cands:
                if kw in m.lower():
                    return m
        return cands[0] if cands else None

    mapping: dict[str, str] = {}
    claude = pick("claude", prefer=("sonnet", "haiku"))
    gpt = pick("gpt", prefer=("sol", "terra", "luna"))
    if claude:
        mapping["claude"] = claude
    if gpt:
        mapping["gpt"] = gpt
    return mapping or default_vendor_mapping()


def register_agents(
    agent_pool,
    pool: MemoryPool,
    user_id: str,
    run_id: str,
    mapping: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """把多个厂家注册成不同 agent_type 的执行器，塞进 agent_pool。

    mapping: agent_type -> model 名。不传则运行时从网关真实清单自动挑（不写死）。
    返回实际注册的 mapping（调试用）。
    """
    mapping = mapping or default_vendor_mapping()
    for agent_type, model in mapping.items():
        agent_pool.register(agent_type, RealAgent(model, pool, user_id, run_id))
    return mapping
