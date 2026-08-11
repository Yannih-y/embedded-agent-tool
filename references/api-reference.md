# API 参考

## HTTP 端点（memorypool.server，默认 127.0.0.1:8800）

### `GET /health` — 轻量健康探针

服务活着 + 启动时的密钥自检结果（不重探，快）。

```json
{ "status": "ok", "pool_ready": true, "key_status": "ok|missing|invalid|unreachable", "key_detail": "..." }
```

### `GET /health/models` — 深度健康报告

现探网关密钥 + 逐个模型发最小探针请求（慢、花少量 token，按需调）。

```json
{
  "key_status": "ok", "key_detail": "...",
  "usable_models": ["claude-sonnet-4-5", "gpt-..."],
  "probes": [{ "model": "...", "status": "ok|not_found|upstream_down|error", "detail": "..." }]
}
```

### `POST /add` — 写一条记忆

```json
{
  "messages": "文本内容",          // 必填
  "user_id": "alice",             // 必填，共享边界
  "agent_id": "claude",           // 可选，留痕
  "run_id": "run_001",            // 可选，协作批次
  "tier": "realtime"              // 可选，realtime（默认）| longterm
}
```

返回 mem0 的 add 结果（`{"results": [{"id": ..., "memory": ..., "event": "ADD"}]}`）。
实时层 `infer=False` 老实存，不走 LLM 抽取。

### `POST /search` — 向量检索

```json
{ "query": "登录方案", "user_id": "alice", "limit": 10 }
```

返回：向量命中（含 `age` 相对时间字段）+ 该 user 的长期记忆关系：

```json
{
  "results": [{ "id": "...", "memory": "...", "score": 0.83, "created_at": "...", "age": "3天前", ... }],
  "relations": [{ "src": "登录模块", "rel": "使用", "dst": "JWT", "memory_id": "...", "created_at": "..." }]
}
```

## MCP 工具（memorypool.mcp_server，stdio 传输）

薄代理：自己不碰数据库，全部转发 `MEMPOOL_BASE_URL` 指向的服务进程。

| 工具 | 参数 | 说明 |
|------|------|------|
| `add_memory` | `content, user_id, agent_id?, tier?` | 写记忆，同 user 下 Agent 都能检索到 |
| `search_memory` | `query, user_id, limit?` | 检索：向量命中 + 相对时间 + 长期关系 |

启动：`python -m memorypool.mcp_server`（由 MCP 客户端拉起，环境变量
`MEMPOOL_BASE_URL` 指定服务地址）。

## Python API（服务进程内 / 客户端）

### `client_sdk.MemoryPoolClient`（Agent 侧，跨进程）

```python
MemoryPoolClient(base_url="http://127.0.0.1:8800", timeout=30.0, auto_start=True)
.add(content, user_id, agent_id=None, run_id=None, tier=Tier.REALTIME) -> dict
.search(query, user_id, limit=10) -> dict
.health() -> dict   # 纯探活，不触发自动拉起
```

- `auto_start=True`（默认）：连接被拒时自动拉起服务（见 daemon）后重试一次；
  只对「连接不上」触发，不吞其它 HTTP 错误
- `auto_start=False`：连接失败直接抛 `httpx.ConnectError`（想自己管服务生命周期时用）

### `daemon`（零设置自动拉起）

```python
daemon.probe(base_url) -> "ready" | "foreign" | "down"   # 探服务状态
daemon.ensure_service(base_url, timeout=120) -> int | None
# 没起 → 拉起后台守护进程并等 /health 就绪，返回新进程 PID；已就绪 → None
# 端口被非内存池服务占用 → ForeignServiceError；启动失败/超时 → 附日志尾部报错
```

- 只代管本机回环地址（127.0.0.1/localhost/::1），远程 base_url 不拉起
- 竞态自愈：两个客户端同时拉起，抢输端口的 uvicorn 自己退出，健康探测照样变绿
- 日志落 `MEMPOOL_DATA_ROOT/logs/server.log`，PID 落 `MEMPOOL_DATA_ROOT/server.pid`
- 等就绪超时由 `MEMPOOL_AUTOSTART_TIMEOUT` 控制（默认 120s，首次要加载 embedding 模型）

### `pool.MemoryPool`（仅服务进程内实例化）

```python
MemoryPool(memory: mem0.Memory | None = None)   # 不传则 Memory.from_config(build_mem0_config())
.add(content, user_id, agent_id=None, run_id=None, tier=Tier.REALTIME) -> dict   # 持写锁
.search(query, user_id, limit=10, with_relations=True) -> dict   # 相对时间 + 关系合并
.list_memories(user_id, tier=None, top_k=100) -> list[dict]
.list_by_run(user_id, run_id, top_k=100) -> list[dict]           # 下游读上游产出
.mark_consolidated(memory_id) -> None    # 注意：update 覆盖 metadata，内部已带 tier 写回
.delete_memory(memory_id) -> None
```

真实签名坑（已实测）：mem0 的 `add` 用顶层 `user_id=` kwargs；`search`/`get_all`
必须 `filters={"user_id": ...}`，顶层传会报错。

### `schema` — 自定义字段

```python
Tier.REALTIME / Tier.LONGTERM
build_metadata(tier, consolidated=False, extra=None) -> dict   # extra 撞 mem0 托管字段会抛 ValueError
get_tier(payload) -> Tier
is_consolidated(payload) -> bool
```

### `consolidator.Consolidator` — 固化引擎

```python
Summary(longterm_texts: list[str], relations: list[tuple[str, str, str]])
Summarizer = Callable[[list[str]], Summary]     # 可注入：测试假实现 / 生产 make_summarizer()

Consolidator(pool, summarizer)
await .consolidate(user_id) -> Summary
# 取未固化的 realtime 记忆 → summarizer 归纳 → 写 longterm + 关系（按实体加锁）→ 标 consolidated
```

### `ttl_cleaner.TTLCleaner`

```python
TTLCleaner(pool, ttl_seconds=86400)
.clean(user_id, now=None) -> CleanReport   # deleted / kept_unconsolidated / alive 三份清单
# 规则：到期且 consolidated=True → 删；到期未固化 → 保留+告警；未到期 → 不动
```

### `idle_monitor.IdleMonitor` — 固化兜底触发

```python
IdleMonitor(consolidator, idle_seconds=300, poll_seconds=30, clock=time.monotonic)
.touch(user_id)                  # 每次写入后调，记录活动时刻
await .check_once(now=None) -> list[str]   # 手动扫一遍，返回本次触发的 user
.start() / await .stop()         # 后台循环（服务 lifespan 里用）
# 触发条件：闲置 > idle_seconds 且自上次固化后有新写入（防空跑）
```

### `orchestrator` — DAG 调度

```python
Task(task_id, agent_type, depends_on=[], fallback_types=[], max_retries=2)
# status: pending/running/done/failed；attempts 计总尝试次数

Orchestrator(executors: dict[str, Executor])    # Executor = async (Task) -> Any
await .run(tasks) -> dict[str, Task]
# 流式调度：任一完成立即解锁下游；上游 failed 级联标记下游 failed（error="upstream_failed"）
# _validate_dag：依赖缺失/成环直接抛 ValueError
.start_order -> list[str]                        # 上次 run 的启动顺序（断言用）
```

### `agent_pool.AgentPool`

```python
.register(agent_type, executor) / .unregister(agent_type)
.get(agent_type) / .as_dict() / .types()
```

### `llm_agents` — 真 LLM 拆解/归纳

```python
decompose(user_task, default_agent="claude") -> list[Task]
# 剥 ```json 代码块 → 提取 JSON → 跳过缺 id/重复 id 子任务 → 剔悬空依赖 → DFS 断环
make_summarizer() -> Summarizer   # 细记忆列表 → Summary(longterm + relations)，只收合法三元组
build_llm()                       # mem0 LlmFactory（MEMPOOL_LLM_PROVIDER/MODEL + 网关 key）
```

### `real_agent` — 多厂家真执行器

```python
make_provider(model)   # claude* → anthropic provider；其余 → openai provider（网关分流）
RealAgent(model, pool, user_id, run_id, system_prompt=...)   # async 执行器
# __call__: list_by_run 读上游 → 调 LLM → add(产出, run_id, tier=realtime) 写回

default_vendor_mapping() -> dict[str, str]        # 网关清单里挑稳定款（sonnet/haiku 优先）
await verified_vendor_mapping() -> dict[str, str] # 先探针验通路再挑（推荐）
register_agents(agent_pool, pool, user_id, run_id, mapping=None) -> dict
```

### `health_check`

```python
check_key(timeout=10.0) -> KeyCheck            # KeyStatus: ok/missing/invalid/unreachable
await probe_models(models) -> list[ModelProbe]  # ModelStatus: ok/not_found/upstream_down/error
await health_report(candidates=None) -> dict    # {"key", "models", "usable"}
```

### `relation_store` — SQLite 三元组

```python
init_db(db_path=DB_PATH)                                    # 幂等建表
add_relation(user_id, src, rel, dst, memory_id=None, created_at="", db_path=...)
# (user_id, src, rel, dst) 唯一，重复写 upsert 覆盖 memory_id/created_at
get_relations(user_id, src=None, db_path=...) -> list[dict]
```

### `entity_lock`

```python
async with entity_lock(f"{user_id}:{实体名}"):
    ...  # 同 key 串行，不同 key 并行；锁不回收（单机 key 基数有限）
```
