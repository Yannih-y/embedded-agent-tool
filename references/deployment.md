# 部署说明

从零到「五个 agent 工具 + AgentClaw 共享同一份记忆」的完整路径。
装完后的**日常用法**（约定、话术、场景）见 [usage.md](usage.md)。

| 方式 | 适用场景 | 耗时 |
|------|----------|------|
| [A. 零设置单机](#a-零设置单机最小路径) | 只想让代码 / 某一个工具用上记忆池 | 1 分钟 |
| [B. Windows 一键部署](#b-windows-一键部署推荐) | 新设备 / 换机，想把本机所有 agent 工具全接上 | 3 分钟 |
| [B2. macOS / Linux 一键部署](#b2-macos--linux-一键部署) | 同上，POSIX 平台（`bootstrap.sh`） | 3 分钟 |
| [C. 手动逐工具配置](#c-手动逐工具配置) | 不想跑脚本，或只想接某几个工具 | 每工具 1 分钟 |
| [D. 接入 AgentClaw](#d-接入-agentclaw-网关http-mcp) | 个人 AI 网关走 HTTP MCP | 2 分钟 |

## 前置要求

- **git** + **Python >= 3.10**（推荐装 [uv](https://docs.astral.sh/uv/)，没有则退回 pip）
- 磁盘约 500 MB（依赖 + 首次运行下载的本地 embedding 模型）
- **不需要任何 API key**——本地写入/检索/共享完全离线；只有真 LLM 链路
  （任务拆解 / 记忆固化 / 多厂家协作）才需要网关 key

## A. 零设置单机（最小路径）

```bash
git clone https://github.com/Yannih-y/embedded-agent-tool.git
cd embedded-agent-tool
uv sync                      # 或：python -m venv .venv && .venv/Scripts/pip install -e .
```

装完即用。服务不用手动起——SDK / MCP 首次调用发现服务没起，自动拉起后台守护进程：

```python
from memorypool.client_sdk import MemoryPoolClient
client = MemoryPoolClient()
client.add("登录模块用 JWT", user_id="alice", agent_id="claude")
```

服务生命周期速查：

```bash
# 日志 / PID
~/.agent_memory_pool/logs/server.log
~/.agent_memory_pool/server.pid
# 停服务
kill $(cat ~/.agent_memory_pool/server.pid)            # POSIX
taskkill /PID <server.pid 内容> /F                      # Windows
```

## B. Windows 一键部署（推荐）

新设备克隆仓库后跑一条命令：

```powershell
git clone https://github.com/Yannih-y/embedded-agent-tool.git
cd embedded-agent-tool
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 `
    -NestworkRemote https://github.com/<you>/nestwork-private.git   # 慢记忆层，可省略
```

### 脚本做什么（全部幂等，重复跑安全）

1. **装依赖**：优先 `uv sync`，无 uv 退回 `python -m venv` + `pip install -e .`
2. **生成配置模板**：`~/.agent_memory_pool/.env`（已存在则不动）
3. **自动检测并注册 MCP**：扫描本机装了哪些工具，装了哪个配哪个——

   | 工具 | 检测依据 | 写入位置 |
   |------|----------|----------|
   | Cursor | `~/.cursor` 存在 | `~/.cursor/mcp.json` |
   | Claude Code | `claude` CLI 可用 | `claude mcp add --scope user`（CLI 缺失时直写 `~/.claude.json`） |
   | Codex | `~/.codex` 存在 | `~/.codex/config.toml` 追加 `[mcp_servers.agent_memory_pool]` |
   | Windsurf | `~/.codeium/windsurf` 存在 | `~/.codeium/windsurf/mcp_config.json` |
   | Kiro | `~/.kiro` 或 `%APPDATA%\kiro` 存在 | `~/.kiro/settings/mcp.json`（带 autoApprove） |

   JSON 写入带幂等守卫：已有同名同 command 的条目不重写文件，不碰其他 MCP server。
4. **（可选）部署 nestwork 慢记忆**：见下节
5. **注册登录自启**：`HKCU Run\AgentMemoryPool → pythonw -m memorypool.daemon`
   （无窗口，已就绪则幂等退出）。stdio MCP 客户端首次调用本就会自动拉起服务，
   这一步是给 **HTTP MCP 客户端**（如 AgentClaw，见 D 节）兜底——它们只在
   自己启动时连一次 `/mcp`，开机即在线才不会错过
6. **冒烟测试**：SDK 自动拉起守护进程 + `/health` 检查（首次运行要下载
   embedding 模型，1~2 分钟属正常）

### 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `-NestworkRemote <url>` | 空 | nestwork 仓库地址；不传且本机无 `~/nestwork` 则跳过慢记忆层 |
| `-NestworkPath <path>` | `~/nestwork` | nestwork 本地克隆位置 |
| `-SkipMcp` | 关 | 跳过 MCP 注册（只装依赖） |
| `-SkipAutostart` | 关 | 跳过登录自启注册 |
| `-SkipSmoke` | 关 | 跳过结尾冒烟测试 |

### 换机时随身带什么

| 数据 | 怎么迁移 |
|------|----------|
| 密钥 `~/.agent_memory_pool/.env` | 手动拷贝一个文件（密钥永不进 git） |
| 慢记忆（nestwork 仓库） | git 自动同步，换机即有——前提是远程建在**私有**仓库 |
| 快记忆（0.4.0 起） | `mempool-export --user <id> --repo <私有仓库>` 导出 markdown 随 git 同步；新机克隆即有全部记忆文本。急用向量级迁移：停服务后整目录拷 `~/.agent_memory_pool` |

> 小技巧：把带你真实仓库地址的完整换机命令存进**私有** nestwork 仓库的
> `DEPLOY.md`——新设备登录 GitHub 打开该页即可复制；公开仓库的文档里只留占位符。

## B2. macOS / Linux 一键部署

```bash
git clone https://github.com/Yannih-y/embedded-agent-tool.git
cd embedded-agent-tool
bash scripts/bootstrap.sh          # --skip-mcp / --skip-autostart / --skip-smoke 可选
```

与 Windows 版对应：装依赖（uv 优先）→ `.env` 模板 → 检测到的工具逐个注册 MCP
（Cursor / Claude Code / Codex / Windsurf / Kiro，JSON 合并幂等）→ 登录自启
（Linux `systemd --user` 单元 / macOS LaunchAgent）→ 冒烟测试。

> 首版脚本（本项目主力机是 Windows），CI 只做语法检查——真机跑出问题请开 issue。
> nestwork 慢记忆层在 POSIX 上直接用其自带安装器，无需本脚本代跑。

## C. 手动逐工具配置

任何支持 MCP 的工具都认这个通用配置（把路径换成你的克隆位置）：

```json
{
  "mcpServers": {
    "agent-memory-pool": {
      "command": "/path/to/embedded-agent-tool/.venv/bin/python",
      "args": ["-m", "memorypool.mcp_server"]
    }
  }
}
```

Windows 下 `command` 是 `X:\\path\\to\\embedded-agent-tool\\.venv\\Scripts\\python.exe`。
也可以用 `uv run` 免关心 venv 路径：

```json
{
  "command": "uv",
  "args": ["run", "--directory", "/path/to/embedded-agent-tool",
           "python", "-m", "memorypool.mcp_server"]
}
```

各工具配置文件位置见上面 B 节的表格。配完重启工具即可（Kiro 热加载可不重启）。
工具只有两个：`add_memory(content, user_id, agent_id?, tier?)`、
`search_memory(query, user_id, limit?)`。

> **user_id 约定**：`user_id` 是共享边界——同一个人在所有工具里统一用同一个
> `user_id`，跨工具记忆才互通；`agent_id` 写各工具自己的名字留痕。

## D. 接入 AgentClaw 网关（HTTP MCP）

AgentClaw 这类自带安全边界的网关**不用 stdio**（其安全模型对带文件系统作用域
的 agent 整类禁用 stdio MCP），改连服务进程自带的 streamable-http 端点
`POST /mcp`（stateless + 纯 JSON 响应）。三步接入：

1. **MCP 配置**（`<agentclaw>/data/mcp-servers.json`，运行时文件不入库）：

   ```json
   [
     {
       "name": "agent-memory-pool",
       "transport": "http",
       "url": "http://127.0.0.1:8800/mcp"
     }
   ]
   ```

2. **agent 工具白名单**（`data/agents/<agent>/config.json` 的 `tools` 数组）——
   AgentClaw 每个 agent 只见白名单内工具：

   ```json
   "agent-memory-pool__search_memory",
   "agent-memory-pool__add_memory"
   ```

3. **（要在 personal 会话用才需要）per-tool 审查登记**
   （`data/reviewed-tool-capabilities.json` 的 `mcpTools` 数组，`server__tool`
   全名 + `reviewedBy/reviewedAt` 留痕）：MCP 工具的类别兜底画像是
   `maxInputClassification=public`，personal 及以上会话默认拒。登记
   `maxInputClassification: "personal"` 后放行（sensitive 及以上仍拒）。

配完 `powershell.exe -File restart.ps1 -NoBuild` 重启网关，日志见
`[bootstrap] MCP server "agent-memory-pool" connected: 2 tools` 即通。

**启动顺序**：AgentClaw 只在自己启动时连一次 `/mcp`，所以内存池要先在线——
bootstrap.ps1 第 5 步的登录自启已兜底；手动装的跑一次
`reg add HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v AgentMemoryPool /t REG_SZ /d "\"<venv>\Scripts\pythonw.exe\" -m memorypool.daemon" /f`。
系统提示词建议补一段使用约定（内部记忆与共享池的分工 + `user_id`/`agent_id`
约定），AgentClaw 的 `system-prompt.md` 有现成示例（「跨软件共享记忆池」小节）。

## 慢记忆层：nestwork（可选搭档）

内存池是**快记忆**（语义检索、TTL、LLM 固化），[nestwork](https://github.com/songth1ef/nestwork)
是**慢记忆**（规则 / 偏好 / 项目状态，markdown + git，人可读可审计）。两层互补：

| | 快记忆（本项目） | 慢记忆（nestwork） |
|--|------|------|
| 内容 | 协作产物、事实、上下文碎片 | 规则、偏好、战略、项目状态卡 |
| 检索 | 向量语义检索 | 会话启动时全量注入 |
| 同步 | 单机服务（0.4.0 计划 git 导出） | git push/pull，天然跨设备 |
| 写入方 | 任何 agent 随时写 | 人写 queen/，agent 只写自己的 agents/ 目录 |

部署要点：

1. **远程必须是私有仓库**（里面是个人记忆）。GitHub 建私有仓库后把 url 传给
   `bootstrap.ps1 -NestworkRemote`，或手动 `git clone <url> ~/nestwork`
2. 各工具的启动注入由 nestwork 自带安装器完成（bootstrap.ps1 会代跑）：
   Claude Code 走 hooks，Codex 走 `~/.codex/AGENTS.md`，Windsurf 注入
   `global_rules.md`，Kiro 注入 `~/.kiro/steering/AGENTS.md`
3. **Windows 特有补丁**（bootstrap.ps1 已自动处理）：Claude Code hooks 写的是裸
   `bash`，Windows 上会命中 WSL 的 bash 导致 hook 失败；需替换成 Git Bash 绝对路径
   （如 `C:/Program Files/Git/bin/bash.exe`）

## 部署后验证

```bash
# 1. 服务与守护进程
curl http://127.0.0.1:8800/health        # {"status":"ok","pool_ready":true,...}

# 2. 跨工具共享：在工具 A 里种一条暗号
#    「用 add_memory 记住：测试暗号是紫罗兰行动，user_id=<你的id>」
# 3. 在工具 B 里检索
#    「用 search_memory 查 user_id=<你的id> 的测试暗号」→ 应答出暗号
```

慢记忆验证：新开会话直接问「我的主项目是什么」，工具应不经介绍就答对
（说明启动注入生效、它真的读了 nestwork）。

## 故障排查

| 现象 | 原因与解法 |
|------|-----------|
| MCP 工具在软件里不出现 | 没重启软件；Kiro 需确认设置里 MCP 开关已开；Windsurf 在 Cascade 的 Plugins/MCP 面板确认服务器已启用 |
| 首次调用卡 1~2 分钟 | 正常：守护进程首次启动要下载本地 embedding 模型（之后秒起） |
| `ForeignServiceError` | 8800 端口被别的服务占用。换端口：`.env` 里写 `MEMPOOL_PORT=8801`，MCP 配置加 `"env": {"MEMPOOL_BASE_URL": "http://127.0.0.1:8801"}` |
| PowerShell 报一堆语法错/乱码 | 脚本被存成了无 BOM 的 UTF-8。仓库内的 `bootstrap.ps1` 自带 BOM；自己改动后保存时保持「UTF-8 with BOM」 |
| nestwork 安装器报 `python3 not found` | Windows 的 `python3` 常是 WindowsApps 假名。bootstrap.ps1 已做会话内 `python3 → python` 别名兜底；手动跑时先 `Set-Alias python3 python` |
| Claude Code hooks 无输出/报 WSL 错误 | 裸 `bash` 命中了 WSL。把 `~/.claude/settings.json` 里 hook 命令的 `bash` 换成 Git Bash 绝对路径（bootstrap.ps1 自动补） |
| git 报 `dubious ownership` | 仓库目录属主与当前用户不一致。按 git 提示 `git config --global --add safe.directory <path>`，或单次 `git -c safe.directory=<path> ...` |
| 记忆写进去查不到 | 检查两边 `user_id` 是否一致——检索只按 `user_id` 过滤 |

## 卸载

```bash
# 1. 停服务
kill $(cat ~/.agent_memory_pool/server.pid)      # Windows: taskkill /PID x /F
# 2. 删数据（先备份！）
rm -rf ~/.agent_memory_pool
# 3. 各工具 MCP 配置里删掉 agent-memory-pool 条目
# 4. nestwork：用其自带 scripts/uninstall/ 对应脚本
```
