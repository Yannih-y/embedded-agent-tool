# Agent Memory Pool

> 多 Agent 共享内存池：打通不同厂家 AI 的交互壁垒，让各自独立进程的 Agent
> 共享记忆、承接彼此产出、协作完成任务。
> *A local-first shared memory service that lets AI agents from different
> vendors (Cursor / Claude Code / Codex / Windsurf / Kiro / your own gateway)
> read and write the same semantic memory.*

[![CI](https://github.com/Yannih-y/embedded-agent-tool/actions/workflows/ci.yml/badge.svg)](https://github.com/Yannih-y/embedded-agent-tool/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

| 平台 | 一键部署 | 登录自启 |
|------|----------|----------|
| Windows | `scripts/bootstrap.ps1`（实机验证） | HKCU Run |
| macOS | `scripts/bootstrap.sh`（首版） | LaunchAgent |
| Linux | `scripts/bootstrap.sh`（首版） | systemd --user |

## 它解决什么问题

不同厂家的 AI Agent（Claude、GPT、DeepSeek、GLM...）各跑各的进程，记忆互不相通，
没法真正协作。本项目提供一个**独立的内存池服务**：

- **共享记忆**：写入记 `agent_id` 留痕，检索按 `user_id` 过滤——同 user 下的
  Agent 互相可读，下游靠 `run_id` 承接上游产出
- **分层记忆**：细粒度实时记忆（老实存）→ 由调用方在协作结束时触发 LLM 归纳，
  固化成简要长期记忆 + 实体关系三元组，跨会话保留（固化/TTL 为显式调用，
  服务进程暂不自动调度）
- **多厂家协作**：真 LLM 把任务拆成 DAG，流式调度分派给不同厂家的 Agent，
  重试 + fallback，模型运行时从网关挑选并探针验通路
- **三接入口**：HTTP（任意语言）+ stdio MCP（Claude Code / Cursor 等 IDE 工具
  一行配置）+ streamable-http MCP（`/mcp`，AgentClaw 等 HTTP 客户端），落同一份数据

## 架构一图流

```
Agent A/B (HTTP)   IDE 五工具 (stdio MCP)   AgentClaw (/mcp HTTP)
     │                    │                       │
     └─────────┬──────────┴───────────────────────┘
   ┌───────────▼────────────────────────────────┐
   │  内存池服务进程（唯一写者，独占数据）           │
   │  REST /add /search  +  MCP /mcp              │
   │  mem0 + faiss + fastembed + SQLite           │
   └───────────┬────────────────────────────────┘
               │ 仅拆解/固化/真Agent时
          聚合网关云 LLM（一 key 多厂家）
```

技术选型：[mem0](https://github.com/mem0ai/mem0)（2.0.13）为存储骨架，faiss 本地向量库，
fastembed 本地 embedding（写入/检索**离线可用**），SQLite 三元组表做长期记忆关系
（评估后弃用图数据库——复杂度配不上价值），FastAPI 服务 + MCP 薄代理。

## 快速开始（零设置：不用起服务，不用配密钥）

```bash
git clone https://github.com/Yannih-y/embedded-agent-tool.git
cd embedded-agent-tool
uv sync --extra dev
```

### Windows 换机一键部署

新设备上克隆后跑一条命令，自动装依赖 + 检测本机装了哪些 agent 工具
（Cursor / Claude Code / Codex / Windsurf / Kiro）并逐个注册 MCP，
可选一并部署 [nestwork](https://github.com/songth1ef/nestwork) 慢记忆层：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 `
    -NestworkRemote https://github.com/<you>/nestwork-private.git   # 可省略
```

脚本幂等，重复跑安全；会注册登录自启（给 AgentClaw 等 HTTP 客户端兜底）+ 冒烟测试。
密钥不随仓库走：从旧设备拷 `~/.agent_memory_pool/.env` 一个文件即可。
**日常怎么用**（约定、跨工具话术、AgentClaw 分工）见 [references/usage.md](references/usage.md)；
装机 / 换机 / 故障排查见 [references/deployment.md](references/deployment.md)。

装完直接写代码——服务没起时首次调用**自动拉起**后台服务进程：

```python
from memorypool.client_sdk import MemoryPoolClient

client = MemoryPoolClient()                                # 不用手动起服务
client.add("登录模块用 JWT", user_id="alice", agent_id="claude")
hits = client.search("登录方案", user_id="alice")           # 另一个 Agent 同 user 可读
```

HTTP 口子同样可用（服务被自动拉起后就是普通 FastAPI）：

```bash
curl -X POST http://127.0.0.1:8800/add -H "Content-Type: application/json" \
  -d '{"messages":"登录模块用 JWT","user_id":"alice","agent_id":"claude"}'
curl -X POST http://127.0.0.1:8800/search -H "Content-Type: application/json" \
  -d '{"query":"登录方案","user_id":"alice"}'
```

本地写入/检索/共享**不需要任何密钥**。只有真 LLM 链路（任务拆解 / 记忆固化 /
多厂家协作）才要网关 key，且只需配一次——写进 `~/.agent_memory_pool/.env` 自动加载：

```bash
# ~/.agent_memory_pool/.env
ANTHROPIC_AUTH_TOKEN=sk-xxx
ANTHROPIC_BASE_URL=https://your-gateway.example.com
```

停掉自动拉起的服务：`kill $(cat ~/.agent_memory_pool/server.pid)`
（Windows：`taskkill /PID <server.pid 内容> /F`）；日志在 `~/.agent_memory_pool/logs/server.log`。

### 接入 Claude Code / Cursor（MCP）

配完即用，不需要先起服务（首次工具调用自动拉起）：

```json
{
  "mcpServers": {
    "agent-memory-pool": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/embedded-agent-tool",
               "python", "-m", "memorypool.mcp_server"],
      "env": { "MEMPOOL_BASE_URL": "http://127.0.0.1:8800" }
    }
  }
}
```

> 铁律：数据库只有一个写者（服务进程）。MCP 层是无状态薄代理，一切经 HTTP 转发——
> 两个进程同开一份 faiss 会静默丢数据。自动拉起不破坏这条：数据的主人永远只有
> 一个服务进程，变的只是它由谁、何时启动。

### 接入 HTTP MCP 客户端（如 AgentClaw 网关）

服务进程自带 MCP streamable-http 端点 `POST /mcp`（stateless + 纯 JSON 响应），
不方便拉 stdio 子进程的客户端直接指过来即可，同样落同一份数据：

```jsonc
// AgentClaw data/mcp-servers.json
[{ "name": "agent-memory-pool", "transport": "http", "url": "http://127.0.0.1:8800/mcp" }]
```

AgentClaw 还要：agent 工具白名单 +（personal 会话）审查登记 + 池子先于网关在线
（登录自启）。完整三步与话术见 [usage.md](references/usage.md) /
[deployment.md D 节](references/deployment.md#d-接入-agentclaw-网关http-mcp)。

## 文档

| 文档 | 内容 |
|------|------|
| [SKILL.md](SKILL.md) | Agent Skill 定义：触发条件、接入方式、工作流、常见坑 |
| [references/usage.md](references/usage.md) | **如何使用**：约定、IDE/AgentClaw 话术、跨软件场景、FAQ |
| [references/deployment.md](references/deployment.md) | 部署说明：一键部署、五工具 MCP、AgentClaw、慢记忆、排查 |
| [references/architecture.md](references/architecture.md) | 架构、模块地图、设计决策、并发模型 |
| [references/api-reference.md](references/api-reference.md) | HTTP / MCP / Python API 全参考 |
| [references/workflows.md](references/workflows.md) | 协作链、固化、TTL、健康检查、部署形态 |
| [BACKLOG.md](BACKLOG.md) | 项目状态：已验证的、还差的、踩过的坑 |

## 测试

```bash
uv run pytest -q
# 无云 key：65 passed + 10 skipped（真 LLM 用例自动跳过）
# 有云 key：全量 75 个，含真多厂家协作/真起进程/自动拉起/HTTP MCP/并发压测，约 80s
# Windows 若遇临时目录 PermissionError：加 --basetemp <新目录>
# 测试套件用独立临时数据目录，不会碰 ~/.agent_memory_pool 里的真实数据
```

## 安全

单机、单用户、本机回环的信任模型：Host 头防护（DNS rebinding）、写入内容审查
（提示注入 / 隐形字符 / 凭证格式命中即拒）、数据不出网。完整威胁模型与部署
红线见 [SECURITY.md](SECURITY.md)。

## 备份与换机（v0.4.0）

```bash
mempool-export --user <你的id> --repo /path/to/私有git仓库
# 或：python -m memorypool.exporter --user <你的id> --repo ...
```

全量记忆导出为人可读 markdown（按 tier 分节、只读快照）并自动
`pull → commit → push`。防手动编辑双护栏 + 双机冲突自动隔离 `.conflicts/`。
换机时：新机克隆仓库即有全部记忆文本（向量索引按需重建）。

## 当前状态与路线图（v0.4.0）

**单机产品可用**（安全审查 / CI / 跨平台部署 / 中文检索 / markdown 备份同步）。
详见 [BACKLOG.md](BACKLOG.md)：

- P1：固化/TTL 调度接入服务进程（含工作流记忆豁免规则）、记忆去重卫生、
  导出挂固化事件自动触发
- P2：faiss → qdrant（混合检索 + 项目维度过滤）、写锁并发、集中配置、
  导入命令（md → 新机池重建）

## License

[MIT](LICENSE)
