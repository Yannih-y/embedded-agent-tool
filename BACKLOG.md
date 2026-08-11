# Agent Memory Pool — Backlog / 项目状态

> 多 Agent 共享内存池：打通不同厂家 AI 的交互壁垒，让各自独立进程的 Agent 共享记忆、协作完成任务。
> 本文档如实记录当前状态——做完并实测的、还没做的、踩过的坑、已知瓶颈。

最后更新：2026-08-11

---

## 一、当前状态总览

- **75 个测试**（`pytest`，单一 `.venv`；无云 key 时 65 passed + 10 skipped——真
  LLM / 真网关用例带 skipif 守卫，有 key 时全量跑；conftest 会话级临时数据目录，
  测试不碰真实记忆库）
- 核心机制、真多厂家协作、跨进程 HTTP、健康检查、并发——都已实测验证
- **零设置可用（0.2.0）**：不用手动起服务（客户端首次调用自动拉起后台守护进程），
  密钥只有真 LLM 功能需要且可写 `~/.agent_memory_pool/.env` 一次配置
- **仓库已自包含**：`mem0ai` 从本地路径依赖切换为 PyPI 固定版 `mem0ai==2.0.13`
  （与本地验证的 v2.0.13 克隆逐字节一致，已重跑测试确认），可独立克隆构建；
  已作为开源仓库发布（含 SKILL.md + references/ 打包）
- **定位：技术验证通过（能跑通、设计成立），尚未到生产可用**（见"四、还差的"）

---

## 二、已完成并实测（DONE）

| 模块 | 文件 | 验证要点 |
|------|------|----------|
| 环境与服务骨架 | `server.py` | mem0 + faiss + fastembed 同装无冲突，服务能起 |
| Schema + 时间维度 | `schema.py` `time_util.py` | tier/consolidated 自定义字段；复用 mem0 `created_at`，检索注入相对时间 |
| 关系层 | `relation_store.py` | SQLite 三元组表（替代 cognee 图谱）；过滤生效、user 隔离、upsert 去重 |
| 内存池核心 | `pool.py` | infer=False 写入 + 向量召回 + 关系合并 + 共享可见性（写记 agent_id、读按 user_id） |
| 实体锁 | `entity_lock.py` | 服务进程内 asyncio.Lock，并发读-改-写不丢更新 |
| 调度器 | `orchestrator.py` `agent_pool.py` `client_sdk.py` | DAG 流式调度、重试上限不死循环、fallback、环检测 |
| 固化 + TTL | `consolidator.py` `ttl_cleaner.py` | 细记忆→长期记忆，去重；到期且已固化才删，未固化保留告警 |
| 闲置触发 | `idle_monitor.py` | 固化双触发的兜底半边（闲置超时） |
| MCP 接入 | `mcp_server.py` | 与 HTTP 落同一份数据 |
| 真 LLM 层 | `llm_agents.py` | 真拆解器（任务→DAG）+ 真归纳器；畸形 DAG 断环、垃圾 JSON 剥离 |
| 真多厂家 Agent | `real_agent.py` | claude + gpt 真协作；**内容承接实测**（暗号验下游真读上游） |
| 健康检查 | `health_check.py` | 密钥校验（401/missing/ok）+ 模型通路并发探针；接进服务 lifespan + `/health` `/health/models` |
| 并发 | `server.py` | `run_in_threadpool` 修掉 async 端点同步阻塞；并发不再退化成串行 |
| 零设置自动拉起 | `daemon.py` `client_sdk.py` | 连接被拒自动拉起后台服务再重试；probe 三态识别端口占用；竞态自愈；pidfile/日志落数据目录（真拉起实测） |
| 密钥一次配置 | `config.py` | `~/.agent_memory_pool/.env` 自动加载（setdefault，不覆盖已有环境变量） |
| 测试数据隔离 | `tests/conftest.py` | 会话级临时数据目录；修掉「测试写真实数据目录 → 向量库滚雪球 → top-k 被挤占后检索归零」的随机挂 |
| 换机一键部署 | `scripts/bootstrap.ps1` | 自动检测 Cursor/Claude Code/Codex/Windsurf/Kiro 注册 MCP + 可选 nestwork 慢记忆 + 冒烟测试；幂等重跑实测通过 |
| HTTP MCP 端点 | `server.py` `mcp_server.py` | `/mcp` streamable-http（stateless+纯 JSON，进程内直连后端）；AgentClaw 网关双向实测（读回暗号 + 回写留痕 agent_id=agentclaw） |

---

## 三、关键设计决策（定稿，动手前已敲定）

1. **mem0 为主骨架** + faiss 向量库 + fastembed 本地 embedding + 云 LLM（走聚合网关）
2. **关系层用 SQLite 三元组表**，弃用 cognee/Kuzu（复杂度配不上价值，踩坑后砍掉）
3. **内存池独立服务进程，独占数据库文件**；Agent 各自独立进程，经 HTTP/MCP 接入（决策10）
4. **多租户隔离**复用 mem0 的 user_id/agent_id/run_id
5. **时间**：只存 `created_at`（服务盖章），事件真实时刻让 AI 从文本推断；只做 AI 参考信号，不做系统自动裁决
6. **记忆分层**：细粒度实时记忆（infer=False，短 TTL）→ 协作结束固化成简要长期记忆
7. **多厂家分派**：claude 系走 anthropic 端点，其它走 OpenAI 端点，同一网关同一 key
8. **模型名不写死**：运行时从网关 `/v1/models` 拿清单 + 探针验通路才用

---

## 四、还差的（TODO，按优先级）

### P0 — 性能瓶颈（进行中，被打断）
- [ ] **底层写锁瓶颈**：`run_in_threadpool` 修完后并发加速比仅 1.49x，瓶颈下移到 faiss/SQLite 单文件写锁
  - 已拆解单次 add 耗时：**embed 10.2ms（占 40%）+ 其余 15ms（faiss 写 + SQLite）**
  - 下一步：确认写锁是不是真串行瓶颈；考虑 embedding 批处理 / faiss 写合并 / SQLite WAL 模式
- [ ] 真 Agent 接进 **HTTP 服务端点**跑一轮完整协作（目前 real_agent 直接调 pool，没走 HTTP 那层）

### P1 — 健壮性上量
- [ ] 全链路只跑过 happy path，真 LLM 的边缘（畸形归纳、超长输出）没在全链路里压
- [ ] 大数据量（万级记忆）下的检索质量与 faiss 性能未测
- [ ] 网关模型可用性依赖外部实时状态（会突然下架/502），需要更强的运行时降级策略

### P2 — 生产化
- [ ] 配置化：key/base_url/模型清单目前读环境变量，缺集中配置
- [ ] 服务崩溃恢复、数据备份
- [ ] 固化去重的冲突判断策略细化（新旧长期记忆冲突时更新 vs 并存）

---

## 五、踩过的坑（教训，避免重犯）

1. **别信 "exit code 0"**：进程不崩不代表测试真过。faiss 没装时测试却"通过"，是因为没跑到向量层——必须看真实输出。
2. **别拿二手结论写生产代码**：cognee 的 query 参数绑定 `{id:$id}` 不生效（查啥都返回），是照探子签名硬写踩的——凡调外部库先写最小脚本实测调用方式。
3. **模型名不能写死**：网关会随时上下架/改名，写死的 gpt-4o/deepseek 某天全 400。
4. **"清单里有" ≠ "能用"**：claude-fable-5 在清单里但持续 502，"挑第一个"正好撞坏模型——要探针验过通路才选。
5. **路径乱码**：工具回显里 `Embedded/yyy` 曾显示乱码，导致文件写歪到错误路径——用 Glob 列真实路径核对。
6. **async 端点里调同步阻塞代码**会堵死事件循环，让并发退化成串行——用 `run_in_threadpool`。
7. **mem0 内置 posthog 遥测**会往外发数据 + 超时噪音污染日志，已在 config 关掉。

---

## 六、怎么跑

```bash
# 环境（uv 管理，工程根有 .venv）
uv sync --extra dev

# 全量测试（有云 key 含真 LLM/真起进程慢用例约 80s；无 key 64 passed + 10 skipped）
.venv/Scripts/python -m pytest -q

# 起服务（可选——客户端首次调用会自动拉起，见 daemon.py）
.venv/Scripts/python -m memorypool.server   # 默认 127.0.0.1:8800，MEMPOOL_HOST/PORT 可改

# 健康检查
curl http://127.0.0.1:8800/health          # 轻量：服务 + 密钥状态
curl http://127.0.0.1:8800/health/models   # 深度：各模型真实通路（慢）

# 诊断脚本
.venv/Scripts/python scripts/verify_runid.py   # run_id 存取验证
.venv/Scripts/python scripts/diag_e2e.py       # 真 LLM 全链路逐步打印（需云 key）
```

**依赖的环境变量**：`ANTHROPIC_AUTH_TOKEN`（或 `ANTHROPIC_API_KEY`）+ `ANTHROPIC_BASE_URL`（聚合网关）。
缺 key 时服务仍可起——本地 infer=False 读写不依赖云 LLM，只有真 Agent 协作那条链会失败。
