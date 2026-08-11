"""闲置监视器实测：固化双触发的「闲置超时」兜底半边。

用可注入的假 clock 控制时序，不空等；用假 consolidator 记录触发，不调真 LLM。
核心验证：
- 闲置超阈值 → 触发固化
- 没闲够 → 不触发
- 已触发后无新写入 → 不重复触发
- 触发后有新写入 → 再次到期能再触发
"""

import pytest

from memorypool.idle_monitor import IdleMonitor


class FakeClock:
    """手动可控的单调时钟。"""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, secs: float) -> None:
        self.t += secs


class FakeConsolidator:
    """记录 consolidate 被谁调过，不干真活。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def consolidate(self, user_id: str):
        self.calls.append(user_id)
        return None


def _monitor(idle_seconds: float = 10.0):
    clock = FakeClock()
    cons = FakeConsolidator()
    mon = IdleMonitor(cons, idle_seconds=idle_seconds, clock=clock)
    return mon, cons, clock


@pytest.mark.asyncio
async def test_idle_over_threshold_fires():
    """写入后闲置超阈值，触发固化。"""
    mon, cons, clock = _monitor(idle_seconds=10.0)
    mon.touch("u1")
    clock.advance(11.0)  # 闲置 11s > 10s
    fired = await mon.check_once()
    assert fired == ["u1"], "闲置超阈值应触发固化"
    assert cons.calls == ["u1"]


@pytest.mark.asyncio
async def test_not_idle_enough_no_fire():
    """没闲够，不触发。"""
    mon, cons, clock = _monitor(idle_seconds=10.0)
    mon.touch("u1")
    clock.advance(5.0)  # 才闲 5s
    fired = await mon.check_once()
    assert fired == [], "没闲够不应触发"
    assert cons.calls == []


@pytest.mark.asyncio
async def test_no_repeat_without_new_write():
    """触发一次后，没有新写入不重复触发。"""
    mon, cons, clock = _monitor(idle_seconds=10.0)
    mon.touch("u1")
    clock.advance(11.0)
    await mon.check_once()  # 第一次触发
    clock.advance(20.0)     # 又过很久，但期间没有新写入
    fired = await mon.check_once()
    assert fired == [], "无新写入不应重复触发"
    assert cons.calls == ["u1"], "只应触发过一次"


@pytest.mark.asyncio
async def test_new_write_after_fire_can_refire():
    """触发后又有新写入，再次到期能再触发。"""
    mon, cons, clock = _monitor(idle_seconds=10.0)
    mon.touch("u1")
    clock.advance(11.0)
    await mon.check_once()  # 第一次触发
    # 新写入
    mon.touch("u1")
    clock.advance(11.0)     # 新写入后再次闲够
    fired = await mon.check_once()
    assert fired == ["u1"], "新写入后再次到期应能再触发"
    assert cons.calls == ["u1", "u1"], "应触发两次"


@pytest.mark.asyncio
async def test_multiple_users_independent():
    """多个 user 各自独立计时。"""
    mon, cons, clock = _monitor(idle_seconds=10.0)
    mon.touch("u1")
    clock.advance(6.0)
    mon.touch("u2")     # u2 比 u1 晚 6s
    clock.advance(5.0)  # 此刻 u1 闲 11s（到期），u2 闲 5s（没到）
    fired = await mon.check_once()
    assert fired == ["u1"], "只有 u1 到期"
    assert cons.calls == ["u1"]
