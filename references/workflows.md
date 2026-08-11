# 工作流详解

## 1. 完整协作链（decompose → DAG → 真 Agent → 固化 → 恢复）

对应 `tests/test_e2e_full_chain.py`（真云 LLM 实测通过的链路）：

```python
import uuid
from memorypool.pool import MemoryPool
from memorypool.agent_pool import AgentPool
from memorypool.orchestrator import Orchestrator
from memorypool.llm_agents import decompose, make_summarizer
from memorypool.consolidator import Consolidator
from memorypool.real_agent import register_agents, verified_vendor_mapping

pool = MemoryPool()                      # 服务进程内
user, run = "alice", f"run_{uuid.uuid4().hex[:8]}"

# ① 真 LLM 拆解：畸形子任务被剔、循环依赖被断，保证 DAG 可调度
tasks = decompose("给一个 Python 项目补测试：先读结构，再挑模块，最后写测试")

# ② 注册多厂家真 Agent（探针验过通路的模型映射）
agent_pool = AgentPool()
mapping = await verified_vendor_mapping()          # 如 {"claude": "claude-sonnet-4-5", "gpt": "gpt-..."}
register_agents(agent_pool, pool, user_id=user, run_id=run, mapping=mapping)

# ③ 流式 DAG 调度：每个 Agent 产出自动写回池（tier=realtime, run_id 标批次）
result = await Orchestrator(agent_pool.as_dict()).run(tasks)
assert all(t.status.value == "done" for t in result.values())

# ④ 协作结束 → 固化：细记忆 → 简要长期记忆 + 实体关系
summary = await Consolidator(pool, make_summarizer()).consolidate(user)

# ⑤ 恢复：新一轮检索能从长期记忆 + 关系里捞回上下文
recall = pool.search("测试", user_id=user)
```

**内容承接的验证方式**（test_real_multi_agent 的做法）：上游 Agent 产出里埋"暗号"，
断言下游 Agent 的产出引用了暗号——证明下游真读了上游，不是各聊各的。

## 2. 记忆分层与固化

### 双触发机制

| 触发方式 | 场景 | 实现 |
|----------|------|------|
| 精确触发 | 走调度器的协作 | DAG 全完成后调用方主动 `consolidate(user)` |
| 兜底触发 | MCP 零散写入（无"协作结束"信号） | `IdleMonitor`：闲置超阈值且有新写入 |

### 固化流程（Consolidator.consolidate）

1. `list_memories(user, tier=REALTIME)` 取实时层，过滤掉 `consolidated=True` 的
2. 归纳器（真 LLM）：细记忆文本列表 → `Summary(longterm_texts, relations)`
3. 长期记忆逐条 `add(tier=LONGTERM)`
4. 关系三元组逐条写 `relation_store`，按 `f"{user}:{src}"` 实体加锁（防并发丢更新）
5. 被固化的细记忆逐条 `mark_consolidated`

幂等性：已标 `consolidated` 的细记忆不会二次归纳；关系表 upsert 去重。

### TTL 清理（TTLCleaner.clean）

```
到期（created_at + ttl < now）？
├─ 否 → 不动（alive）
└─ 是 → 已固化？
        ├─ 是 → 物理删除（deleted）
        └─ 否 → 保留 + 告警（kept_unconsolidated）——绝不误删未固化数据
```

服务不自动跑 TTL——按需调用或外部定时触发（当前阶段的设计取舍）。

## 3. 健康检查与模型选择

### 启动自检（服务 lifespan）

`check_key()` 打网关 `/v1/models`：401=密钥废、能列=有效、连不上=unreachable。
**不拦服务启动**——本地 infer=False 读写不依赖云 LLM，只有真 Agent 链会失败。

### 深度探针（GET /health/models 或代码调用）

```python
from memorypool.health_check import health_report
report = await health_report()      # 不传 candidates 则探网关全清单
report["usable"]                    # 真调通的模型列表
```

探针是真发最小请求，不是看清单——"清单里有"≠"能用"（实测有模型在清单里但持续 502）。

### 模型选择策略（real_agent）

- `default_vendor_mapping()`：从网关清单挑，claude 优先 sonnet/haiku、gpt 优先
  sol/terra/luna 关键词，跳过 thinking 款；不发探针，快但可能挑中坏模型
- `verified_vendor_mapping()`：先 `probe_models` 验全清单通路，只从可用款里挑（推荐，
  启动时跑一次值得）

错误分诊（网关实测返回）：401 authentication_error=密钥问题、
400 invalid_request「模型不支持」=下架、502 api_error「Upstream」=上游坏。

## 4. 部署形态

### 零设置形态（默认，推荐）

什么都不用起。第一个调用方（SDK / MCP 工具调用）发现服务没起，自动把
`memorypool.server` 拉起为后台守护进程，之后所有调用方复用同一个服务：

```python
from memorypool.client_sdk import MemoryPoolClient
client = MemoryPoolClient()      # 服务没起就自动拉起，起了就直接用
```

- 日志：`~/.agent_memory_pool/logs/server.log`；PID：`~/.agent_memory_pool/server.pid`
- 停服务：`kill $(cat ~/.agent_memory_pool/server.pid)`（Windows `taskkill /PID x /F`）
- 并发竞态自愈：多个客户端同时拉起也只会活一个（端口独占，输家自己退出）
- 密钥（仅真 LLM 功能需要）写 `~/.agent_memory_pool/.env` 一次即可，
  自动拉起的服务进程同样会加载

### 单机手动形态（想自己管生命周期）

```
uv run python -m memorypool.server        # 唯一写者，独占 ~/.agent_memory_pool
```

- 任意数量 Agent 进程经 HTTP 接入（`MemoryPoolClient` 或裸 httpx）
- 任意数量 MCP 客户端经 `memorypool.mcp_server` 薄代理接入
- 换数据目录：`MEMPOOL_DATA_ROOT=/path/to/data`；换监听地址：
  `MEMPOOL_HOST` / `MEMPOOL_PORT`（自动拉起与手动启动都认）

### 禁止形态

- ❌ 两个服务进程指向同一 `MEMPOOL_DATA_ROOT`（faiss 后写覆盖先写）
- ❌ MCP 进程 / Agent 进程内自建 `MemoryPool()` 直连数据文件（同上 + 实体锁失效）

### 测试隔离形态

跨进程测试用 `MEMPOOL_DATA_ROOT` 指向临时目录 + OS 分配空闲端口
（见 `tests/test_real_process_http.py` 的 fixture 写法）。

## 5. 测试矩阵

`uv sync --extra dev && uv run pytest -q`

| 类别 | 文件 | 是否需要云 key |
|------|------|----------------|
| 机制单测（schema/时间/关系/池/锁/调度/固化/TTL/闲置/MCP） | `test_task2~8_*.py` | 否 |
| LLM 边缘健壮性（假 LLM monkeypatch） | `test_edge_llm_agents.py` | 否 |
| 真进程跨 HTTP | `test_real_process_http.py` `test_task1_service.py` `test_server_health.py`(部分) | 否 |
| 并发压测 | `test_load_concurrent.py` | 否 |
| 密钥/模型探针（打真网关） | `test_health_check.py`(部分) | 是（skipif 守卫） |
| 真多厂家协作 + 内容承接 | `test_real_multi_agent.py` | 是（skipif 守卫） |
| 真 LLM 全链路 e2e | `test_e2e_full_chain.py` | 是（skipif 守卫） |

无 key 环境：60 passed + 10 skipped。有 key 全量约 68~80s。

## 6. 诊断脚本（scripts/）

- `scripts/diag_e2e.py`：跑真 LLM 全链路并打印每步产物（拆解的 DAG、实时记忆、
  归纳出的长期记忆、各 query 召回分数），定位"搜空"这类问题出在哪一环
- `scripts/verify_runid.py`：验证 add 带 run_id 后落在哪个字段、能否按 run_id 过滤读回
  （下游承接上游的命门）
