"""按需自动拉起内存池服务，实现「零设置直接用」。

客户端（client_sdk / MCP 薄代理）发现服务没起时，自动把 `memorypool.server`
作为后台独立进程拉起来，等 /health 就绪后继续原请求。用户不再需要手动起服务。

设计要点：
- 单写者铁律不变：数据的主人仍是唯一的服务进程，这里只是把「手动起」变成「用时自动起」
- 并发竞态自愈：两个客户端同时拉起 → 抢输端口的 uvicorn 自己退出，健康探测
  照样变绿，谁赢都一样，调用方无感
- 端口被「别的东西」占用能识别（/health 返回形状不对 → ForeignServiceError），
  不会往陌生服务里写数据
- 只代管本机回环地址；远程 base_url 不代为拉起
- 数据根目录在调用时从环境变量现读（不用 config 的 import 时快照），
  测试/多实例改 MEMPOOL_DATA_ROOT 才能生效
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

# 首次拉起要初始化 MemoryPool + 加载 fastembed 模型（冷缓存还要下载），给足冗余
DEFAULT_READY_TIMEOUT = float(os.environ.get("MEMPOOL_AUTOSTART_TIMEOUT", "120"))

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ForeignServiceError(RuntimeError):
    """base_url 上有服务在跑，但不是内存池（端口被别的东西占了）。"""


def _data_root() -> Path:
    """调用时现读数据根目录（与 config.DATA_ROOT 同源，但不吃 import 时快照）。"""
    return Path(os.environ.get("MEMPOOL_DATA_ROOT", Path.home() / ".agent_memory_pool"))


def _log_path() -> Path:
    return _data_root() / "logs" / "server.log"


def _pid_path() -> Path:
    return _data_root() / "server.pid"


def _log_tail(lines: int = 15) -> str:
    """取服务日志尾部，拼进错误信息帮用户定位启动失败原因。"""
    try:
        content = _log_path().read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-lines:])
    except OSError:
        return "（无日志）"


def probe(base_url: str, timeout: float = 2.0) -> str:
    """探服务状态：'ready' 就绪 / 'foreign' 端口被别的服务占 / 'down' 没起。"""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/health", timeout=timeout)
    except Exception:
        return "down"
    if r.status_code == 200:
        try:
            if "pool_ready" in r.json():
                return "ready"
        except Exception:
            pass
    return "foreign"


def _port_open(host: str, port: int) -> bool:
    """TCP 层探端口是否有人持有（uvicorn 起了但 lifespan 还在加载时，/health 不通但端口已 bind）。"""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def spawn_server(host: str, port: int) -> subprocess.Popen:
    """把服务进程作为后台守护进程拉起（脱离当前进程生命周期），日志/pid 落数据目录。"""
    log_path = _log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab")
    env = {**os.environ, "MEMPOOL_HOST": host, "MEMPOOL_PORT": str(port)}
    kwargs: dict = {
        "stdout": log,
        "stderr": log,
        "stdin": subprocess.DEVNULL,
        "env": env,
    }
    if sys.platform == "win32":
        # 无窗口 + 独立进程组：父进程退出后服务继续活着
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen([sys.executable, "-m", "memorypool.server"], **kwargs)
    finally:
        log.close()  # 子进程已继承句柄，父进程这份关掉
    try:
        _pid_path().write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass  # pidfile 只是停服务的便利项，写不进不影响功能
    return proc


def ensure_service(base_url: str, timeout: float = DEFAULT_READY_TIMEOUT) -> int | None:
    """确保 base_url 上有内存池服务：没起就自动拉起并等到就绪。

    返回本次拉起的进程 PID；服务本来就在跑则返回 None。
    端口被别的服务占用抛 ForeignServiceError；启动失败/超时抛错并附日志尾部。
    """
    base_url = base_url.rstrip("/")
    state = probe(base_url)
    if state == "ready":
        return None
    if state == "foreign":
        raise ForeignServiceError(
            f"{base_url} 已被非内存池服务占用——换 MEMPOOL_BASE_URL 端口，或停掉占用者"
        )

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8800
    if host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            f"服务未运行且 {host} 不是本机回环地址，不代为拉起——请在目标机器上起服务"
        )

    proc = spawn_server(host, port)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = probe(base_url)
        if state == "ready":
            return proc.pid
        if state == "foreign":
            raise ForeignServiceError(f"{base_url} 已被非内存池服务占用")
        # 自己拉的进程死了且没有任何人持有端口 → 不是竞态输了，是真启动失败，快速报错
        if proc.poll() is not None and not _port_open(host, port):
            raise RuntimeError(
                f"服务进程启动失败（exit={proc.returncode}），日志尾部：\n{_log_tail()}"
            )
        time.sleep(0.5)
    raise TimeoutError(
        f"自动拉起后 {timeout}s 内未就绪（首次运行可能在下载 embedding 模型），"
        f"日志：{_log_path()}\n{_log_tail()}"
    )
