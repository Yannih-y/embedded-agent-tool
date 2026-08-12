#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# agent_memory_pool 一键部署（macOS / Linux）
#
# 用法：
#   git clone https://github.com/Yannih-y/embedded-agent-tool.git
#   cd embedded-agent-tool
#   bash scripts/bootstrap.sh [--skip-mcp] [--skip-autostart] [--skip-smoke]
#
# 做的事（幂等，重复跑安全）：
#   1. 装依赖（优先 uv，退回 python3 venv + pip -e .）
#   2. 生成 ~/.agent_memory_pool/.env 模板（已存在不动）
#   3. 检测本机 agent 工具并注册 MCP：Cursor / Claude Code / Codex / Windsurf / Kiro
#   4. 注册登录自启（Linux: systemd --user；macOS: LaunchAgent）
#   5. 冒烟测试（SDK 自动拉起守护进程 + health）
#
# 注：Windows 用 scripts/bootstrap.ps1。nestwork 慢记忆层在 POSIX 上用其自带
# 安装器即可（无需本脚本代跑）：git clone <私有仓库> ~/nestwork && 按其 README。
# -----------------------------------------------------------------------------
set -euo pipefail

SKIP_MCP=0; SKIP_AUTOSTART=0; SKIP_SMOKE=0
for arg in "$@"; do
  case "$arg" in
    --skip-mcp) SKIP_MCP=1 ;;
    --skip-autostart) SKIP_AUTOSTART=1 ;;
    --skip-smoke) SKIP_SMOKE=1 ;;
    *) echo "未知参数: $arg（可用 --skip-mcp --skip-autostart --skip-smoke）"; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
step() { printf '\n==> %s\n' "$1"; }
ok()   { printf '  v %s\n' "$1"; }
skip() { printf '  - %s\n' "$1"; }

# --- 1. 依赖 -------------------------------------------------------------------
step "环境检查与依赖安装 ($REPO_ROOT)"
command -v git >/dev/null || { echo "缺 git"; exit 1; }
if command -v uv >/dev/null; then
  (cd "$REPO_ROOT" && uv sync >/dev/null)
  ok "uv sync 完成"
else
  command -v python3 >/dev/null || { echo "缺 python3（>=3.10），或先装 uv"; exit 1; }
  [ -d "$REPO_ROOT/.venv" ] || python3 -m venv "$REPO_ROOT/.venv"
  "$REPO_ROOT/.venv/bin/pip" install -q -e "$REPO_ROOT"
  ok "pip install -e . 完成"
fi
VENV_PY="$REPO_ROOT/.venv/bin/python"
[ -x "$VENV_PY" ] || { echo "venv python 不存在: $VENV_PY"; exit 1; }

# --- 2. .env 模板 ---------------------------------------------------------------
step "全局配置 ~/.agent_memory_pool/.env"
ENV_DIR="$HOME/.agent_memory_pool"; ENV_FILE="$ENV_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  mkdir -p "$ENV_DIR"
  cat > "$ENV_FILE" <<'EOF'
# agent_memory_pool 全局配置（每次启动自动加载，改完重启守护进程生效）
# 纯读写记忆不需要任何 key；只有 LLM 固化/整合链路需要。
# ANTHROPIC_API_KEY=
# ANTHROPIC_AUTH_TOKEN=
# ANTHROPIC_BASE_URL=
# MEMPOOL_PORT=8800
EOF
  ok "已生成模板（从旧设备拷贝同名文件可直接覆盖）"
else
  skip ".env 已存在，不动"
fi

# --- 3. MCP 注册（JSON 合并交给 python，幂等：已有同名条目不覆盖） ----------------
merge_mcp_json() {  # $1=json文件路径
  "$VENV_PY" - "$1" "$VENV_PY" <<'PYEOF'
import json, sys
from pathlib import Path
path, venv_py = Path(sys.argv[1]), sys.argv[2]
path.parent.mkdir(parents=True, exist_ok=True)
data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
servers = data.setdefault("mcpServers", {})
if "agent-memory-pool" in servers:
    print("skip")
else:
    servers["agent-memory-pool"] = {"command": venv_py, "args": ["-m", "memorypool.mcp_server"]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("ok")
PYEOF
}

if [ "$SKIP_MCP" -eq 0 ]; then
  step "MCP 注册（检测到哪个工具配哪个）"
  if [ -d "$HOME/.cursor" ]; then
    [ "$(merge_mcp_json "$HOME/.cursor/mcp.json")" = ok ] && ok "Cursor" || skip "Cursor 已有条目"
  fi
  if command -v claude >/dev/null; then
    claude mcp add --scope user agent-memory-pool -- "$VENV_PY" -m memorypool.mcp_server >/dev/null 2>&1 \
      && ok "Claude Code (CLI)" || skip "Claude Code 已有条目或 CLI 拒绝"
  fi
  if [ -d "$HOME/.codex" ]; then
    if grep -q "mcp_servers.agent_memory_pool" "$HOME/.codex/config.toml" 2>/dev/null; then
      skip "Codex 已有条目"
    else
      { printf '\n[mcp_servers.agent_memory_pool]\ntype = "stdio"\ncommand = "%s"\nargs = ["-m", "memorypool.mcp_server"]\nstartup_timeout_sec = 120\n' "$VENV_PY" >> "$HOME/.codex/config.toml"; } && ok "Codex"
    fi
  fi
  if [ -d "$HOME/.codeium/windsurf" ]; then
    [ "$(merge_mcp_json "$HOME/.codeium/windsurf/mcp_config.json")" = ok ] && ok "Windsurf" || skip "Windsurf 已有条目"
  fi
  if [ -d "$HOME/.kiro" ]; then
    [ "$(merge_mcp_json "$HOME/.kiro/settings/mcp.json")" = ok ] && ok "Kiro" || skip "Kiro 已有条目"
  fi
fi

# --- 4. 登录自启 -----------------------------------------------------------------
if [ "$SKIP_AUTOSTART" -eq 0 ]; then
  step "注册登录自启"
  case "$(uname -s)" in
    Linux)
      UNIT_DIR="$HOME/.config/systemd/user"; mkdir -p "$UNIT_DIR"
      cat > "$UNIT_DIR/agent-memory-pool.service" <<EOF
[Unit]
Description=Agent Memory Pool autostart (ensure daemon running)

[Service]
Type=oneshot
ExecStart=$VENV_PY -m memorypool.daemon

[Install]
WantedBy=default.target
EOF
      systemctl --user daemon-reload && systemctl --user enable agent-memory-pool.service >/dev/null 2>&1 \
        && ok "systemd --user 单元已启用" || skip "systemctl 不可用（无 systemd 用户会话？手动起：$VENV_PY -m memorypool.daemon）"
      ;;
    Darwin)
      PLIST="$HOME/Library/LaunchAgents/com.agent-memory-pool.daemon.plist"
      mkdir -p "$(dirname "$PLIST")"
      cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.agent-memory-pool.daemon</string>
  <key>ProgramArguments</key><array>
    <string>$VENV_PY</string><string>-m</string><string>memorypool.daemon</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOF
      launchctl unload "$PLIST" >/dev/null 2>&1 || true
      launchctl load -w "$PLIST" && ok "LaunchAgent 已加载" || skip "launchctl 加载失败（手动起：$VENV_PY -m memorypool.daemon）"
      ;;
    *) skip "未知平台 $(uname -s)，跳过自启" ;;
  esac
fi

# --- 5. 冒烟 --------------------------------------------------------------------
if [ "$SKIP_SMOKE" -eq 0 ]; then
  step "冒烟测试（首次运行会下载本地 embedding 模型，1~2 分钟属正常）"
  # health() 是纯探活不拉起，必须先 ensure_service（新机器上服务必然没起）
  "$VENV_PY" -c "from memorypool.daemon import ensure_service; from memorypool.client_sdk import MemoryPoolClient; import json; ensure_service('http://127.0.0.1:8800'); print(json.dumps(MemoryPoolClient().health(), ensure_ascii=False))"
  ok "守护进程拉起 + health 通过"
fi

step "部署完成"
cat <<'EOF'
手动收尾（只需一次）：
  1. 如需 LLM 固化功能：填 ~/.agent_memory_pool/.env 里的 key（或从旧设备拷贝整个文件）
  2. 重启各 agent 工具让 MCP 生效
  3. 记忆约定：同一个人在所有工具统一用同一个 user_id，跨工具记忆才互通
EOF
