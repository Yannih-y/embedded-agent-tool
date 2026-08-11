"""边缘健壮性实测：llm_agents 面对畸形 LLM 输出不崩。

畸形输入全是构造的，用 monkeypatch 塞假 LLM，不调真云 LLM（不花 token）。
验证：
- _extract_json：剥代码块 / 抓 {...} / 空输入抛
- _break_cycles：循环依赖被断，DAG 变无环
- decompose：缺 id 的子任务跳过、指向不存在 id 的依赖被剔、带环被断、全畸形抛
"""

import pytest

from memorypool import llm_agents
from memorypool.llm_agents import _break_cycles, _extract_json, decompose
from memorypool.orchestrator import Orchestrator, Task


# ---- _extract_json ----

def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    raw = '```json\n{"a": 1}\n```'
    assert _extract_json(raw) == {"a": 1}


def test_extract_json_with_surrounding_text():
    raw = '好的，结果如下：{"a": 1} 以上。'
    assert _extract_json(raw) == {"a": 1}


def test_extract_json_empty_raises():
    with pytest.raises(ValueError):
        _extract_json("")


# ---- _break_cycles ----

def test_break_cycles_removes_back_edge():
    # A->B, B->A 成环
    tasks = [
        Task(task_id="A", agent_type="x", depends_on=["B"]),
        Task(task_id="B", agent_type="x", depends_on=["A"]),
    ]
    out = _break_cycles(tasks)
    # 断环后至少一条边被删，整体无环
    total_deps = sum(len(t.depends_on) for t in out)
    assert total_deps < 2, "循环依赖应被断开"


def test_break_cycles_keeps_valid_dag():
    tasks = [
        Task(task_id="A", agent_type="x", depends_on=[]),
        Task(task_id="B", agent_type="x", depends_on=["A"]),
    ]
    out = _break_cycles(tasks)
    b = next(t for t in out if t.task_id == "B")
    assert b.depends_on == ["A"], "合法依赖不该被误删"


# ---- decompose（monkeypatch 假 LLM）----

class _FakeLLM:
    def __init__(self, raw: str):
        self._raw = raw

    def generate_response(self, messages, **kwargs):
        return self._raw


def _patch_llm(monkeypatch, raw: str):
    monkeypatch.setattr(llm_agents, "build_llm", lambda: _FakeLLM(raw))


def test_decompose_skips_subtask_without_id(monkeypatch):
    _patch_llm(monkeypatch, '{"subtasks":[{"desc":"没id"},{"id":"t1","desc":"有id"}]}')
    tasks = decompose("随便")
    ids = [t.task_id for t in tasks]
    assert ids == ["t1"], "缺 id 的子任务应被跳过"


def test_decompose_drops_dangling_dependency(monkeypatch):
    _patch_llm(
        monkeypatch,
        '{"subtasks":[{"id":"t1","depends_on":["t99"]}]}',
    )
    tasks = decompose("随便")
    assert tasks[0].depends_on == [], "指向不存在 id 的依赖应被剔除"


def test_decompose_breaks_cycle_and_orchestrator_accepts(monkeypatch):
    # 拆出 t1<->t2 循环，清洗后 orchestrator 应能正常校验不抛
    _patch_llm(
        monkeypatch,
        '{"subtasks":[{"id":"t1","depends_on":["t2"]},{"id":"t2","depends_on":["t1"]}]}',
    )
    tasks = decompose("随便")
    # 喂给 orchestrator，_validate_dag 不该因环抛异常
    orch = Orchestrator({})
    orch._validate_dag({t.task_id: t for t in tasks})  # 不抛即通过


def test_decompose_all_malformed_raises(monkeypatch):
    _patch_llm(monkeypatch, '{"subtasks":[{"desc":"无id"},{"foo":"bar"}]}')
    with pytest.raises(ValueError):
        decompose("随便")


def test_decompose_no_subtasks_raises(monkeypatch):
    _patch_llm(monkeypatch, '{"subtasks":[]}')
    with pytest.raises(ValueError):
        decompose("随便")
