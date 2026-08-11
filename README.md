# Agent Memory Pool

> 多 Agent 共享内存池：打通不同厂家 AI 的交互壁垒，让各自独立进程的 Agent
> 共享记忆、承接彼此产出、协作完成任务。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 它解决什么问题

不同厂家的 AI Agent（Claude、GPT、DeepSeek、GLM...）各跑各的进程，记忆互不相通，
没法真正协作。本项目提供一个**独立的内存池服务**：

- **共享记忆**：写入记 `agent_id` 留痕，检索按 `user_id` 过滤——同 user 下的
  Agent 互相可读，下游靠 `run_id` 承接上游产出
- **分层记忆**：细粒度实时记忆（老实存、短 TTL）→ 协作结束由 LLM 归纳固化成
  简要长期记忆 + 实体关系三元组，跨会话保留
- **多厂家协作**：真 LLM 把任务拆成 DAG，流式调度分派给不同厂家的 Agent，
  重试 + fallback，模型运行时从网关挑选并探针验通路
- **双接入口**：HTTP（任意语言）+ MCP（Claude Code / Cursor 一行配置接入），
  落同一份数据

## 架构一图流

```
Agent A(claude)   Agent B(gpt)   Claude Code/Cursor
     │HTTP            │HTTP            │MCP(stdio→薄代理→HTTP)
     └───────┬────────┴────────────────┘
   ┌─────────▼──────────────────────────┐
   │  内存池服务进程（唯一写者，独占数据）  │
   │  mem0 + faiss + fastembed + SQLite  │
   └─────────┬──────────────────────────┘
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

脚本幂等，重复跑安全；结尾自带冒烟测试（客户端自动拉起守护进程 + health）。
密钥不随仓库走：从旧设备拷 `~/.agent_memory_pool/.env` 一个文件即可。
参数详解、手动逐工具配置、故障排查见 [references/deployment.md](references/deployment.md)。

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

## 文档

| 文档 | 内容 |
|------|------|
| [SKILL.md](SKILL.md) | Agent Skill 定义：触发条件、接入方式、工作流、常见坑 |
| [references/deployment.md](references/deployment.md) | 部署说明：一键部署、五工具 MCP 配置、慢记忆层、排查 |
| [references/architecture.md](references/architecture.md) | 架构、模块地图、设计决策、并发模型 |
| [references/api-reference.md](references/api-reference.md) | HTTP / MCP / Python API 全参考 |
| [references/workflows.md](references/workflows.md) | 协作链、固化、TTL、健康检查、部署形态 |
| [BACKLOG.md](BACKLOG.md) | 项目状态：已验证的、还差的、踩过的坑 |

## 测试

```bash
uv run pytest -q
# 无云 key：64 passed + 10 skipped（真 LLM 用例自动跳过）
# 有云 key：全量 74 个，含真多厂家协作/真起进程/自动拉起/并发压测，约 80s
# 测试套件用独立临时数据目录，不会碰 ~/.agent_memory_pool 里的真实数据
```

## 当前状态与路线图

**技术验证通过（能跑通、设计成立），尚未到生产可用。** 详见 [BACKLOG.md](BACKLOG.md)：

- P0：faiss/SQLite 写锁并发瓶颈（加速比 1.49x）、真 Agent 走 HTTP 端点完整协作
- P1：全链路边缘压测、万级记忆检索质量、网关模型降级策略
- P2：集中配置、崩溃恢复与备份、固化冲突策略细化

## License

[MIT](LICENSE)
