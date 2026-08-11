---
name: agent-memory-pool
description: >
  Shared memory pool service for multi-vendor AI agents (多 Agent 共享内存池).
  Lets independent agent processes (Claude, GPT, DeepSeek, GLM...) share memories,
  read each other's outputs, and collaborate on DAG-orchestrated tasks through one
  memory service. TRIGGER when: user mentions "agent memory pool", "共享内存池",
  "共享记忆", "多 Agent 协作记忆", "跨厂家 Agent", "memory sharing between agents",
  "记忆固化", "memory consolidation", "记忆分层", "DAG 任务拆解调度", or wants
  multiple AI agents from different vendors to remember and build on each other's
  work. DO NOT TRIGGER when: user asks about the mem0 SDK itself (use the mem0
  skill), cognee knowledge graphs, or single-agent conversation memory inside one
  app process.
license: MIT
metadata:
  author: Yannih-y
  version: "0.2.0"
  category: ai-memory
  tags: "memory, multi-agent, orchestration, mem0, faiss, mcp, fastapi"
compatibility: >
  Python 3.10-3.14, uv 管理依赖。本地写入/检索（fastembed + faiss + SQLite）离线可用；
  任务拆解、记忆固化、真 Agent 协作需要云 LLM（聚合网关 key）。Windows/macOS/Linux。
---

# Agent Memory Pool（多 Agent 共享内存池）

打通不同厂家 AI 的交互壁垒：各自独立进程的 Agent 通过同一个内存池服务共享记忆、
承接彼此产出、协作完成任务。mem0 为存储骨架（faiss 向量 + fastembed 本地
embedding + SQLite 关系三元组），FastAPI 服务进程独占数据库，三接入口：
HTTP REST / stdio MCP（IDE 工具）/ streamable-http MCP（`/mcp`，AgentClaw 等）。

## 架构铁律（先读这条）

**数据库只有一个写者：内存池服务进程。**
faiss 是内存索引、写时整体落盘——两个进程同时打开同一份数据，后写的会整体覆盖先写的，
记忆静默丢失。所以：

- Agent / MCP 客户端**永远不要**自己 `MemoryPool()` 直连数据文件
- 一切读写走 HTTP（`client_sdk.MemoryPoolClient`）或 MCP（薄代理，转发 HTTP）
- 只有 `memorypool.server` 这个服务进程持有 `MemoryPool` 实例
- **服务不需要手动起**：客户端首次调用发现服务没起，自动把它拉起为后台
  守护进程（`memorypool.daemon`），单写者铁律不变，只是「手动起」变「用时自动起」

## 第一步：安装（装完即用，不用起服务）

```bash
git clone https://github.com/Yannih-y/embedded-agent-tool.git
cd embedded-agent-tool
uv sync --extra dev          # 装依赖（含 pytest）
```

Windows 换机 / 想把本机所有 agent 工具一次接完：跑
`scripts/bootstrap.ps1`（自动检测 Cursor / Claude Code / Codex / Windsurf / Kiro
并逐个注册 MCP，幂等可重跑），详见
[references/deployment.md](references/deployment.md)。

装完直接用——SDK / MCP 首次调用会自动拉起服务（零设置）。想手动管理也可以：

```bash
uv run python -m memorypool.server         # 手动起服务（可选）
curl http://127.0.0.1:8800/health          # 轻量：服务 + 密钥状态
curl http://127.0.0.1:8800/health/models   # 深度：逐个模型真实通路探针（慢）
# 停自动拉起的服务：kill $(cat ~/.agent_memory_pool/server.pid)
# Windows: taskkill /PID (type %USERPROFILE%\.agent_memory_pool\server.pid) /F
```

**密钥只有真 LLM 功能才需要**（任务拆解/记忆固化/多厂家协作）；存/读/共享**完全不用配**。
要配也只配一次：写进 `~/.agent_memory_pool/.env`（自动加载，不覆盖已有环境变量）：

```bash
# ~/.agent_memory_pool/.env
ANTHROPIC_AUTH_TOKEN=sk-xxx
ANTHROPIC_BASE_URL=https://your-gateway.example.com
```

环境变量（都有默认值，本地读写零配置可跑）：

| 变量 | 作用 | 默认 |
|------|------|------|
| `MEMPOOL_DATA_ROOT` | 数据根目录（faiss + SQLite + 日志 + pid） | `~/.agent_memory_pool` |
| `MEMPOOL_HOST` / `MEMPOOL_PORT` | 服务监听地址（自动拉起也走这里） | `127.0.0.1` / `8800` |
| `MEMPOOL_AUTOSTART_TIMEOUT` | 自动拉起等就绪的秒数（首次要加载模型） | `120` |
| `MEMPOOL_EMBED_MODEL` / `MEMPOOL_EMBED_DIMS` | 本地 embedding 模型/维度 | `BAAI/bge-small-en-v1.5` / 384 |
| `ANTHROPIC_AUTH_TOKEN` 或 `ANTHROPIC_API_KEY` | 聚合网关 key（一 key 通吃多厂家） | 无——缺了本地读写仍可用，云 LLM 链路失败 |
| `ANTHROPIC_BASE_URL` | 聚合网关地址 | 无 |
| `MEMPOOL_LLM_PROVIDER` / `MEMPOOL_LLM_MODEL` | 固化/拆解用的云 LLM | `anthropic` / `claude-sonnet-4-5` |
| `MEMPOOL_BASE_URL` | MCP 薄代理指向的服务地址 | `http://127.0.0.1:8800` |

## 第二步：接入（三选一）

日常怎么说、跨软件互通、AgentClaw 与内部记忆的分工 → 先读
[references/usage.md](references/usage.md)。下面是接入配置速查。

### Python SDK（最省事：连服务都不用管）

```python
from memorypool.client_sdk import MemoryPoolClient

client = MemoryPoolClient()   # 服务没起会自动拉起（auto_start 默认开）
client.add("任务[t1]产出：登录模块用 JWT", user_id="alice", agent_id="claude", run_id="run_001")
hits = client.search("登录方案", user_id="alice")
```

### HTTP（任意语言的 Agent 进程）

```bash
# 写记忆：infer=False 老实存，agent_id 留痕，run_id 标协作批次
curl -X POST http://127.0.0.1:8800/add -H "Content-Type: application/json" -d '{
  "messages": "任务[t1]产出：登录模块用 JWT",
  "user_id": "alice", "agent_id": "claude", "run_id": "run_001", "tier": "realtime"
}'

# 检索：按 user_id 过滤（同 user 下所有 Agent 互相可读），返回命中+相对时间+关系
curl -X POST http://127.0.0.1:8800/search -H "Content-Type: application/json" -d '{
  "query": "登录方案", "user_id": "alice", "limit": 10
}'
```

### MCP（Cursor / Claude Code / Codex / Windsurf / Kiro 等）

MCP 层是无状态薄代理，把工具调用转成 HTTP 打给服务进程。配完即用，
不需要先起服务（首次工具调用自动拉起）：

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

工具两个：`add_memory(content, user_id, agent_id?, tier?)`、
`search_memory(query, user_id, limit?)`。

对 agent 的示例话术：

- 「用 add_memory 记住：…，user_id=`alice`，agent_id 写 `cursor`」
- 「用 search_memory 查 user_id=`alice` 的登录方案」

### HTTP MCP（AgentClaw 网关等不便拉 stdio 子进程的客户端）

服务进程自带 streamable-http 端点 `POST /mcp`（stateless + 纯 JSON 响应），
直接把 MCP 客户端指过来即可，工具同上：

```jsonc
// AgentClaw data/mcp-servers.json
[{ "name": "agent-memory-pool", "transport": "http", "url": "http://127.0.0.1:8800/mcp" }]
```

AgentClaw 额外步骤（白名单 / personal 审查登记 / 启动顺序）见
[deployment.md D 节](references/deployment.md#d-接入-agentclaw-网关http-mcp) 与
[usage.md](references/usage.md#在-agentclaw-里怎么用)。

## 核心概念速查

| 概念 | 含义 |
|------|------|
| `user_id` | 共享边界：检索只按它过滤，同 user 下的 Agent 互相可读 |
| `agent_id` | 留痕：谁写的，不参与检索过滤 |
| `run_id` | 协作批次：下游 Agent 靠 `user_id + run_id` 读上游产出 |
| `tier=realtime` | 细粒度实时记忆：`infer=False` 老实存，短 TTL，协作过程产物 |
| `tier=longterm` | 简要长期记忆：固化产出，跨会话保留，关系入 SQLite 三元组 |
| `consolidated` | 细记忆是否已固化——TTL 清理的前置条件，未固化绝不删 |
| `created_at` | 服务进程盖章的 UTC 时刻，检索时自动注入相对时间（`age` 字段） |

## 记忆生命周期（写 → 固化 → 清理）

```python
from memorypool.pool import MemoryPool                 # 仅服务进程内
from memorypool.consolidator import Consolidator
from memorypool.llm_agents import make_summarizer      # 真 LLM 归纳器
from memorypool.ttl_cleaner import TTLCleaner

pool = MemoryPool()
# 固化：细记忆 → 简要长期记忆 + 实体关系三元组，细记忆标 consolidated
await Consolidator(pool, make_summarizer()).consolidate("alice")
# TTL：到期且已固化才物理删；到期未固化 → 保留 + 告警
TTLCleaner(pool, ttl_seconds=24 * 3600).clean("alice")
```

固化双触发：走调度器的场景在 DAG 全完成时精确触发；MCP 零散写入场景由
`idle_monitor.IdleMonitor`（闲置超时，默认 5 分钟）兜底。

## 多 Agent 协作（拆解 → DAG 调度 → 真 Agent）

```python
from memorypool.llm_agents import decompose            # 真 LLM 拆任务 → DAG
from memorypool.agent_pool import AgentPool
from memorypool.orchestrator import Orchestrator
from memorypool.real_agent import register_agents, verified_vendor_mapping

tasks = decompose("给一个 Python 项目补测试：先读结构，再挑模块，最后写测试")

agent_pool = AgentPool()
mapping = await verified_vendor_mapping()              # 探针验过通路才选模型
register_agents(agent_pool, pool, user_id="alice", run_id="run_001", mapping=mapping)

result = await Orchestrator(agent_pool.as_dict()).run(tasks)
```

- 流式调度：任一任务完成立即解锁下游，不做批 barrier；重试有上限、可 fallback 切备用厂家
- 多厂家分派：claude 系走 anthropic 端点，其余（gpt/deepseek/glm...）走 OpenAI 端点，同一网关同一 key
- 下游承接：执行器拿不到上游 result，靠 `list_by_run(user_id, run_id)` 从池里读上游产出

## 常见坑（血泪教训，必读）

- **模型名不能写死**：网关随时上下架/改名。用 `list_gateway_models()` 运行时拿清单，
  `verified_vendor_mapping()` 探针验通路后再选（"清单里有" ≠ "能用"）
- **mem0 检索签名**：`search` 必须 `filters={"user_id": ...}`，顶层 `user_id=` 会报错；
  `add` 则相反，用顶层 kwargs
- **update 会整体覆盖 metadata**：改 `consolidated` 时必须带上 `tier` 一起写回，否则 tier 丢失
- **async 端点里别调同步阻塞代码**：会把并发退化成串行，用 `run_in_threadpool`
- **两个进程各开一份数据 = 静默丢数据**：见「架构铁律」
- **mem0 posthog 遥测**：`config.py` 已在 import 前关掉，别改动这个顺序

## References

| 主题 | 文件 |
|------|------|
| **如何使用**（约定 / IDE·AgentClaw 话术 / 场景 / FAQ） | [references/usage.md](references/usage.md) |
| 部署说明（一键部署 / 五工具 MCP / AgentClaw / 慢记忆层 / 排查） | [references/deployment.md](references/deployment.md) |
| 架构与设计决策（分层/单写者/时间维度/关系表选型） | [references/architecture.md](references/architecture.md) |
| API 参考（HTTP 端点 / MCP 工具 / Python 类） | [references/api-reference.md](references/api-reference.md) |
| 工作流详解（协作链 / 固化 / TTL / 健康检查） | [references/workflows.md](references/workflows.md) |
| 项目状态与路线图 | [BACKLOG.md](BACKLOG.md) |
