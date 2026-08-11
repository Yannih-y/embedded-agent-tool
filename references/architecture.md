# 架构与设计决策

## 总体拓扑

```
┌─────────────┐  ┌─────────────┐  ┌──────────────────┐
│ Agent A 进程 │  │ Agent B 进程 │  │ Claude Code/Cursor│
│ (claude)    │  │ (gpt)       │  │  (MCP 客户端)     │
└──────┬──────┘  └──────┬──────┘  └────────┬─────────┘
       │ HTTP           │ HTTP             │ stdio
       │                │           ┌──────▼─────────┐
       │                │           │ mcp_server.py  │
       │                │           │ (薄代理,无状态) │
       │                │           └──────┬─────────┘
       │                │                  │ HTTP
┌──────▼────────────────▼──────────────────▼──────┐
│        内存池服务进程（memorypool.server）        │
│  FastAPI + 唯一的 MemoryPool 实例（单写者）       │
│  ┌──────────┐ ┌──────────┐ ┌─────────────────┐  │
│  │ faiss    │ │ fastembed│ │ SQLite           │  │
│  │ 向量索引  │ │ 本地嵌入  │ │ 关系三元组        │  │
│  └──────────┘ └──────────┘ └─────────────────┘  │
└──────────────────────┬───────────────────────────┘
                       │ (仅固化/拆解/真Agent时)
               ┌───────▼────────┐
               │ 聚合网关 云 LLM │ claude→/v1/messages
               │ (一 key 多厂家) │ 其余→/v1/chat/completions
               └────────────────┘
```

## 模块地图

| 层 | 模块 | 职责 |
|----|------|------|
| 存储核心 | `pool.py` | mem0 门面：写（infer=False + 写锁）、读（user_id 过滤 + 关系合并 + 相对时间） |
| | `schema.py` | 只定义 mem0 没有的字段：`tier`、`consolidated`；防覆盖 mem0 托管字段 |
| | `time_util.py` | `created_at`（UTC）→ 相对时间（"3天前"），检索时注入 `age` 字段 |
| | `relation_store.py` | SQLite 三元组表 `(user_id, src, rel, dst)` 唯一，upsert 去重 |
| 生命周期 | `consolidator.py` | 细记忆 → 长期记忆 + 关系三元组；summarizer 可注入（测试假/生产真 LLM） |
| | `ttl_cleaner.py` | 到期且已固化才删；到期未固化保留 + 告警 |
| | `idle_monitor.py` | 固化双触发的兜底半边：闲置超阈值且有新写入才触发 |
| | `entity_lock.py` | 按 key 分发 asyncio.Lock，串行化同实体关系的读-改-写 |
| 协作调度 | `orchestrator.py` | 流式 DAG 调度：依赖满足即启动、任一完成立即解锁下游、重试上限 + fallback 链 |
| | `agent_pool.py` | agent_type → 执行器注册表（不做池化，Agent 是独立进程） |
| | `llm_agents.py` | 真 LLM 拆解器（任务→DAG，断环+剔畸形）+ 真归纳器（细记忆→Summary） |
| | `real_agent.py` | 多厂家执行器：读上游（run_id）→ 调 LLM → 产出写回池 |
| 接入 | `server.py` | FastAPI 服务进程：/add /search /health /health/models，run_in_threadpool 防阻塞 |
| | `mcp_server.py` | MCP 薄代理：add_memory / search_memory 两工具，转发 HTTP |
| | `client_sdk.py` | Agent 侧 HTTP 客户端封装 |
| | `health_check.py` | 密钥校验（401/missing/ok）+ 模型通路并发探针 |
| | `config.py` | 数据目录、embedding/LLM 配置、网关地址分派、关遥测 |

## 关键设计决策（定稿）

1. **mem0 为主骨架**（PyPI `mem0ai==2.0.13`）+ faiss 向量库 + fastembed 本地
   embedding + 云 LLM 走聚合网关。embedding 本地化 = 写入/检索零云依赖。
2. **关系层用 SQLite 三元组表，弃用 cognee/Kuzu 图谱**。关系需求本质是
   (源, 关系, 目标) 三元组，SQLite 三列就够：零新增依赖、零新增进程、单文件、
   事务干净。真需要图遍历时导出升级即可。
3. **内存池独立服务进程，独占数据库文件（单写者）**。faiss 是内存索引、写时整体
   落盘，多进程同开必然互相覆盖。Agent 全部经 HTTP/MCP 接入。
4. **多租户隔离复用 mem0 的 user_id / agent_id / run_id**：user_id 是共享边界
   （检索过滤），agent_id 只留痕，run_id 标协作批次。
5. **时间只存 `created_at`（服务盖章）**，检索时现算相对时间注入。时间只做
   AI 参考信号，不做系统自动裁决（不 last-write-wins）。
6. **记忆分层**：realtime（infer=False 老实存、短 TTL）→ 协作结束固化成
   longterm（LLM 归纳、关系入表、跨会话保留）。
7. **多厂家分派**：按 model 名前缀分流——claude 系走 anthropic 端点
   `/v1/messages`，其余走 OpenAI 端点 `/v1/chat/completions`（base_url 带 /v1），
   同一网关同一 key，全走 mem0 `LlmFactory` 抽象，不自己直连。
8. **模型名不写死**：运行时 `list_gateway_models()` 拿清单 +
   `probe_models()` 探针验通路才用。

## 并发模型

- **faiss 写锁**：`MemoryPool._write_lock`（threading.Lock）串行化 add/update/delete。
  读（search/get_all）不加锁——faiss 并发查询安全，拿锁反而让读排在写后。
- **async 端点**：所有同步的 pool 调用经 `run_in_threadpool` 下放线程池，
  防止堵死事件循环把并发退化成串行。
- **实体锁**：固化写关系时按 `f"{user_id}:{src}"` 拿 asyncio.Lock，
  防并发固化对同一实体读-改-写丢更新。进程内锁有效的前提是单写者架构。
- **已知瓶颈（P0）**：写路径并发加速比仅 1.49x，单次 add ≈ embed 10.2ms（40%）+
  faiss 写 + SQLite ≈ 15ms。候选方向：embedding 批处理、faiss 写合并、SQLite WAL。

## 上下游数据流（协作场景）

1. 用户任务 → `decompose()` 真 LLM 拆成子任务 DAG（断环、剔畸形依赖）
2. `Orchestrator.run()` 流式调度，按 agent_type 从 `AgentPool` 取执行器
3. `RealAgent.__call__`：`list_by_run(user_id, run_id)` 读上游产出 → 拼 prompt →
   调厂家 LLM → 产出 `add(..., run_id, tier=realtime)` 写回池
4. DAG 全完成 → `Consolidator.consolidate(user)`：细记忆 → 长期记忆 + 关系
5. `TTLCleaner.clean(user)`：到期且已固化的细记忆物理删除
6. 后续任意 Agent `search()`：向量命中 + 相对时间 + 长期关系合并返回
