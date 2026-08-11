"""真 LLM 层：任务拆解器 + 记忆归纳器（全链路用真云 LLM，替换任务6/7的假实现）。

两个产出物对齐已有结构，不新造类型：
- decompose(user_task) → list[orchestrator.Task]（DAG，喂给调度器）
- make_summarizer() → consolidator.Summarizer（细记忆→Summary，喂给固化引擎）

LLM 走 mem0 的 LlmFactory（复用 provider 抽象 + 本机中转 key），不自己直连 anthropic。
JSON 用稳妥的剥离逻辑（剥 ```json 代码块 + 提取首个 {...}），不依赖未实测的 tool_call。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from mem0.utils.factory import LlmFactory

from memorypool.config import LLM_MODEL, LLM_PROVIDER, _anthropic_api_key
from memorypool.consolidator import Summary
from memorypool.orchestrator import Task

logger = logging.getLogger("memorypool.llm_agents")


def build_llm():
    """造一个 LLM 实例（复用 mem0 provider 抽象 + 本机中转 key）。"""
    return LlmFactory.create(
        LLM_PROVIDER,
        {"model": LLM_MODEL, "api_key": _anthropic_api_key()},
    )


def _extract_json(raw: str) -> Any:
    """从 LLM 回复里稳妥提取 JSON：先剥 ```json 代码块，再退回抓首个 {...}。

    真 LLM 常把 JSON 包在代码块里或加解释文字，直接 json.loads 会炸。
    """
    if not raw:
        raise ValueError("LLM 返回空")
    text = raw.strip()
    # 剥 ```json ... ``` 或 ``` ... ```
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 退回：抓第一个 { 到最后一个 }
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _break_cycles(tasks: list[Task]) -> list[Task]:
    """剔除会导致成环的依赖边，保证喂给 orchestrator 的 DAG 无环。

    真 LLM 偶尔会拆出循环依赖（A 依赖 B、B 依赖 A），orchestrator 的
    _validate_dag 遇环直接抛异常，整条链路崩。这里用 DFS 找回边并删掉，
    宁可少一条依赖（退化成更早启动），也不让整个 DAG 报废。
    """
    by_id = {t.task_id: t for t in tasks}
    # 0=未访问 1=访问中(在当前DFS栈) 2=已完成
    color: dict[str, int] = {t.task_id: 0 for t in tasks}

    def dfs(tid: str) -> None:
        color[tid] = 1
        task = by_id[tid]
        kept: list[str] = []
        for dep in task.depends_on:
            if dep not in by_id:
                continue
            if color[dep] == 1:
                # 回边 → 成环，删掉这条依赖
                logger.warning("拆解出现循环依赖，剔除边 %s -> %s", tid, dep)
                continue
            kept.append(dep)
            if color[dep] == 0:
                dfs(dep)
        task.depends_on = kept
        color[tid] = 2

    for t in tasks:
        if color[t.task_id] == 0:
            dfs(t.task_id)
    return tasks


_DECOMPOSE_SYSTEM = (
    "你是任务拆解器。把用户任务拆成可并行/串行执行的子任务 DAG。"
    "只返回 JSON，格式：{\"subtasks\":[{\"id\":\"t1\",\"desc\":\"...\","
    "\"agent_type\":\"claude\",\"depends_on\":[]}]}。"
    "id 用 t1/t2...；depends_on 填前置子任务 id；无依赖填 []；不要多余文字。"
)


def decompose(user_task: str, default_agent: str = "claude") -> list[Task]:
    """把用户任务拆成 DAG，产出 orchestrator.Task 列表。

    每个 Task 的 result 初始塞入子任务描述（desc），供执行器/调试用。
    depends_on 直接用 LLM 给的子任务 id（与 task_id 一致）。
    """
    llm = build_llm()
    raw = llm.generate_response(
        messages=[
            {"role": "system", "content": _DECOMPOSE_SYSTEM},
            {"role": "user", "content": user_task},
        ],
    )
    data = _extract_json(raw)
    subtasks = data.get("subtasks", []) if isinstance(data, dict) else []
    if not subtasks:
        raise ValueError(f"拆解未产出子任务，原始返回：{raw[:200]}")

    # 第一遍：收合法子任务（有 id 的），记下所有有效 id
    raw_tasks: list[dict] = []
    valid_ids: set[str] = set()
    for st in subtasks:
        if not isinstance(st, dict):
            continue
        tid = st.get("id")
        if not tid or not isinstance(tid, str):
            logger.warning("拆解子任务缺 id，跳过：%s", st)
            continue
        if tid in valid_ids:
            logger.warning("拆解子任务 id 重复，跳过：%s", tid)
            continue
        valid_ids.add(tid)
        raw_tasks.append(st)

    if not raw_tasks:
        raise ValueError(f"拆解子任务全部畸形（无合法 id），原始返回：{raw[:200]}")

    # 第二遍：建 Task，剔除指向不存在 id 的依赖（防 orchestrator 校验崩）
    tasks: list[Task] = []
    for st in raw_tasks:
        deps = [
            d for d in st.get("depends_on", [])
            if isinstance(d, str) and d in valid_ids and d != st["id"]
        ]
        task = Task(
            task_id=st["id"],
            agent_type=st.get("agent_type") or default_agent,
            depends_on=deps,
        )
        task.result = st.get("desc", "")
        tasks.append(task)
    return _break_cycles(tasks)


_CONSOLIDATE_SYSTEM = (
    "你是记忆归纳器。把一批零散的实时记忆归纳成简要的长期记忆，并抽取实体关系。"
    "只返回 JSON，格式：{\"longterm\":[\"简要长期记忆1\",\"...\"],"
    "\"relations\":[[\"源实体\",\"关系\",\"目标实体\"]]}。"
    "longterm 是精炼后的要点；relations 是 (源,关系,目标) 三元组；不要多余文字。"
)


def make_summarizer():
    """产出 consolidator.Summarizer（Callable[[list[str]], Summary]），用真 LLM 归纳。"""

    def summarize(texts: list[str]) -> Summary:
        if not texts:
            return Summary()
        llm = build_llm()
        joined = "\n".join(f"- {t}" for t in texts)
        raw = llm.generate_response(
            messages=[
                {"role": "system", "content": _CONSOLIDATE_SYSTEM},
                {"role": "user", "content": f"实时记忆：\n{joined}"},
            ],
        )
        data = _extract_json(raw)
        longterm = data.get("longterm", []) if isinstance(data, dict) else []
        rels_raw = data.get("relations", []) if isinstance(data, dict) else []
        # 只收合法的三元组
        relations = [
            (r[0], r[1], r[2])
            for r in rels_raw
            if isinstance(r, (list, tuple)) and len(r) == 3
        ]
        return Summary(longterm_texts=list(longterm), relations=relations)

    return summarize
