"""零设置自动拉起实测（真起进程）。

验证「不手动起服务直接用」这条链路：
- 服务没起时 MemoryPoolClient.add 第一次调用自动拉起后台服务进程并重试成功
- 同 user 立刻能 search 到（数据真落了盘）
- 服务已就绪时 ensure_service 不重复拉起（返回 None）
- probe 三态：down（没起）/ ready（就绪）
- pidfile 落在数据目录，可用于停服务

用独立 MEMPOOL_DATA_ROOT + OS 分配空闲端口，不污染默认数据，不与其它测试抢写。
不依赖云 key（infer=False 本地链路）。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from memorypool import daemon
from memorypool.client_sdk import MemoryPoolClient


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _kill(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _wait_port_closed(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not daemon._port_open("127.0.0.1", port):
            return
        time.sleep(0.3)


def test_probe_down_on_free_port():
    port = _free_port()
    assert daemon.probe(f"http://127.0.0.1:{port}") == "down"


def test_zero_setup_client_autostarts(tmp_path, monkeypatch):
    """服务没起 → client.add 自动拉起并成功 → search 命中 → 不重复拉起。"""
    monkeypatch.setenv("MEMPOOL_DATA_ROOT", str(tmp_path))
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    user = f"u_auto_{uuid.uuid4().hex[:8]}"

    pid: int | None = None
    try:
        client = MemoryPoolClient(base)  # auto_start 默认开
        # 此刻服务并没有起——add 应触发自动拉起后重试成功
        result = client.add("零设置自动拉起验证记忆", user_id=user, agent_id="claude")
        assert result, "/add 应返回 mem0 结果"

        # pidfile 落在本次的数据目录
        pid_file = Path(tmp_path) / "server.pid"
        assert pid_file.is_file(), "自动拉起应写 server.pid"
        pid = int(pid_file.read_text(encoding="utf-8"))

        # 数据真写进去了：同 user 立刻可检索
        hits = client.search("自动拉起", user_id=user)
        memories = hits["results"] if isinstance(hits, dict) else hits
        assert any("零设置自动拉起验证记忆" in m.get("memory", "") for m in memories)

        # 服务已就绪：probe=ready，ensure_service 不重复拉起
        assert daemon.probe(base) == "ready"
        assert daemon.ensure_service(base) is None

        # health() 纯探活可用
        assert client.health().get("status") == "ok"
    finally:
        if pid:
            _kill(pid)
            _wait_port_closed(port)


def test_ensure_service_rejects_remote_host():
    """远程地址不代为拉起，报错明确。"""
    with pytest.raises(RuntimeError, match="回环"):
        daemon.ensure_service("http://192.0.2.1:8800", timeout=1.0)


def test_env_file_loader(tmp_path, monkeypatch):
    """~/.agent_memory_pool/.env 自动注入环境：setdefault 语义，不覆盖已有值。"""
    from memorypool.config import _load_env_file

    monkeypatch.setenv("MEMPOOL_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MEMPOOL_TEST_EXISTING", "keep-me")
    monkeypatch.delenv("MEMPOOL_TEST_NEWKEY", raising=False)
    (tmp_path / ".env").write_text(
        "# 注释行\n"
        "MEMPOOL_TEST_NEWKEY = 'quoted-value'\n"
        "MEMPOOL_TEST_EXISTING=should-not-override\n"
        "这行没有等号会被跳过\n",
        encoding="utf-8",
    )

    _load_env_file()

    assert os.environ["MEMPOOL_TEST_NEWKEY"] == "quoted-value", "新 key 应注入且去引号"
    assert os.environ["MEMPOOL_TEST_EXISTING"] == "keep-me", "已有环境变量不可被覆盖"
