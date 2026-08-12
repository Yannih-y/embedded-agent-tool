# Changelog

## [Unreleased]

### 变更（重要：默认 embedding 模型 en → zh，需迁移）

- **默认 embedding 换 `BAAI/bge-small-zh-v1.5`（512 维）**：原默认
  `bge-small-en-v1.5` 是英文模型，对中文记忆的语义分辨率不足——已造成真实
  检索事故（状态盘点查询漏掉已存在的任务完成记忆，向用户报告了错误事实）。
  `config.py` 增加模型→维度映射表（zh=512 / en=384，表外自定义模型必须显式设
  `MEMPOOL_EMBED_DIMS`）。三组对照基线（同查询、同数据、仅换模型）：
  口语化查询「语音转文字服务装好了吗」从 top5 全灭 → 目标记忆 #3；
  盘点查询从 1/3 命中 → 3/3 全进 top5；决议查询从 #4 → #1
- **新增 `scripts/reembed.py` 重嵌入迁移工具**（换模型必须全量重建索引，
  新旧向量空间不同直接查会失真）：停服自检（单写者）→ 备份 zip → docstore
  导出（另落独立快照 json，失败重跑不依赖解包）→ **维度自检**（真嵌入一条
  核对与配置一致，防 faiss 断言炸在半途——首跑实际踩过 384/512 错配，自检
  即由此教训固化）→ 清库重灌（原 created_at 存 `metadata.orig_created_at`）
  → 计数校验。本机 33 条记忆迁移实测通过，全量测试 70 passed + 10 skipped

### 安全

- **REST 端点 Host 头防护（DNS rebinding）**：`/add` `/search` 此前无 Host
  校验——恶意网页把域名 rebind 到 127.0.0.1 后可读走全部记忆、写入毒化内容
  （检索结果进每个 agent 上下文，是现成注入面；`/mcp` 有 SDK 自带防护，REST
  裸奔）。新增 `host_guard` 中间件：默认只认回环（127.0.0.1/localhost/::1，
  含 IPv6 括号形态解析），局域网部署用 `MEMPOOL_ALLOWED_HOSTS` 显式扩白，
  拒绝返回 403。新增 `tests/test_host_guard.py` 6 用例（伪造 Host 拒 / 回环放 /
  扩白生效 / IPv6 / 解析边界）。既有 5 处 `TestClient(app)` 默认 `testserver`
  Host 会被新防护拦截，统一对齐为回环 base_url（与 test_mcp_http_endpoint
  同款写法）——属测试对齐生产行为（服务只监听回环），非放宽断言，防护本身
  有独立正反用例锁定。全量 70 passed + 10 skipped

### 修正（文档与实现不符，2026-08-12 自查）

- **固化/TTL「自动触发」误述更正**：SKILL.md 曾写「MCP 零散写入由 IdleMonitor
  兜底」——实际 `IdleMonitor`/`TTLCleaner` 只是库能力，server.py 从未加载，
  零散写入的记忆不会被自动固化或清理。SKILL / workflows / README 三处已改为
  实话并标注现状；BACKLOG 立项「服务进程接入固化/TTL 调度」（显式配置默认关，
  需先定义工作流记忆豁免规则，防止把圆桌记录/任务书归纳掉破坏「会后任查」）
- BACKLOG 新增 P1：写入内容注入审查（参照 AgentClaw scanMemoryContent）；
  备份优先级提升（0.3.0 导出本质是备份，提到写锁优化之前）
- usage.md 任务交接协议补**认领语义**：接活前先查重并写「已认领 <run_id>」，
  防两个会话重复接同一任务书

### 新增

- **MCP over streamable-http 端点**（`/mcp`，挂在服务进程内）：
  - `build_mcp(http_mode=True)`：stateless（免 MCP-Session-Id）+ json_response
    （纯 JSON 非 SSE），适配只发独立 JSON-RPC POST 的简版 HTTP MCP 客户端
    （首个消费者：AgentClaw 网关——其安全模型禁止带文件系统作用域的 agent
    使用 stdio MCP，HTTP transport 是官方放行路径）
  - `server.py` 用进程内直连后端（`_InProcessBackend`）挂载，不走 HTTP 自回环；
    FastMCP 实例每次 lifespan 重建（SDK 的 session manager 只能 run() 一次，
    TestClient 反复进出 lifespan 时必须新建）
  - stdio 入口（`memorypool.mcp_server`）不受影响，五个 IDE 工具照旧
  - 新测试 `tests/test_mcp_http_endpoint.py`：无会话头 initialize / tools/list /
    写读闭环（注意 MCP SDK 自带 Host 头防护，测试须用回环 base_url）
- **登录自启入口**（`memorypool/daemon.py` 新增 `main()`，
  `pythonw -m memorypool.daemon`）：确保服务在跑后自身退出（常驻的是 spawn
  出的服务进程），已就绪幂等直退、无窗口、失败静默进日志。给 Windows
  HKCU Run 用——HTTP MCP 客户端（AgentClaw）只在自身启动时连一次 `/mcp`，
  开机即在线才不会错过；本机已实测冷拉起（杀进程 → pythonw 入口 → health ok）
- **bootstrap.ps1 第 5 步：自动注册登录自启**（`HKCU Run\AgentMemoryPool`，
  `Set-ItemProperty` 幂等覆盖，路径带引号防空格；新增 `-SkipAutostart` 开关；
  venv 无 pythonw 时跳过并提示）
- **Windows 换机一键部署**（`scripts/bootstrap.ps1`，UTF-8 BOM 保证 PS 5.1 中文解析）：
  - venv 装依赖（优先 uv，退回 pip -e .）+ 生成 `~/.agent_memory_pool/.env` 模板
  - 自动检测 Cursor / Claude Code / Codex / Windsurf / Kiro 并逐个注册 MCP
    （JSON 合并写入带幂等守卫，不重写已有条目、不碰其他 MCP server）
  - 可选 `-NestworkRemote`：克隆 nestwork 慢记忆仓库并为检测到的工具装启动注入；
    自动修补 Claude Code hooks 的裸 `bash` 为 Git Bash 绝对路径（避开 WSL）
  - 结尾冒烟测试：SDK 自动拉起守护进程 + health 检查；全程幂等可重跑
- **部署说明**（`references/deployment.md`）：三种部署路径（零设置单机 /
  Windows 一键 / 手动逐工具）、五工具 MCP 配置位置表、nestwork 慢记忆层
  分工与部署要点、换机数据迁移清单、部署后验证、故障排查表、卸载

### 文档

- **0.3.0「markdown 导出 + git 同步」设计定稿**（BACKLOG P1 新节）：由跨厂家
  圆桌会议产出——主持 cursor + 成员 AgentClaw default / hermes 两个独立 LLM
  agent，两轮制（独立发言 + 交叉质询），全程留痕共享池
  `run_id=roundtable-20260812-mempool030`。决议：按 user 聚合单文件、单向导出、
  固化事件驱动 pull→写→commit→push、冲突落 `.conflicts/` 不写 merge driver、
  目录 `memory/longterm/<user_id>.md`、防手动编辑双护栏（git 脏检查 + 导出
  哈希比对）、文件头只读声明 + UTC 时间戳
- **新增 [references/usage.md](references/usage.md)「如何使用」**：心智模型、
  `user_id`/`agent_id` 约定、IDE 五工具与 AgentClaw 话术、典型场景（IDE↔IDE /
  IDE↔网关 / DAG / 快慢记忆分工）、服务生命周期、使用向 FAQ；与 deployment
  （装机）分工明确
- README 架构图改为三入口（REST + stdio MCP + `/mcp`）；文档表 / 测试计数
  对齐 65+10=75；SKILL / deployment 首页 / api-reference daemon / workflows /
  BACKLOG 交叉链接到 usage.md
- deployment.md 新增 **D 节「接入 AgentClaw 网关（HTTP MCP）」**：三步接入
  （mcp-servers.json / agent 白名单 / personal 会话 per-tool 审查登记）+
  启动顺序说明；B 节脚本步骤与参数表同步登录自启；README / SKILL /
  api-reference 补 `/mcp` 端点章节；workflows.md 测试计数 65+10=75 并记
  Windows pytest 临时根 PermissionError 的 `--basetemp` 绕法
- 全量校订：SKILL.md MCP 支持清单扩为五工具；bootstrap.ps1 收尾提示的
  user_id 示例通用化（不再硬编码个人 id）

## [0.2.0] - 2026-08-11

零设置体验：装完即用，不用手动起服务，密钥只配一次（或完全不配）。

### 新增

- **服务自动拉起**（`memorypool/daemon.py`）：客户端连接被拒时自动把
  `memorypool.server` 拉起为后台守护进程（Windows 无窗口独立进程组 /
  POSIX new session），等 `/health` 就绪后重试原请求
  - 端口被非内存池服务占用能识别（`ForeignServiceError`），不往陌生服务写数据
  - 并发竞态自愈：多客户端同时拉起只活一个，输家自动退出，调用方无感
  - 只代管本机回环地址；启动失败/超时报错附日志尾部
  - 日志落 `MEMPOOL_DATA_ROOT/logs/server.log`，PID 落 `MEMPOOL_DATA_ROOT/server.pid`
- **`MemoryPoolClient(auto_start=True)`**（默认开）：SDK / MCP 薄代理零设置接入；
  `health()` 保持纯探活不触发拉起
- **密钥一次配置**：`~/.agent_memory_pool/.env` 自动加载（setdefault 语义，
  不覆盖已有环境变量，无第三方依赖）；本地存/读/共享本就无需密钥
- **`MEMPOOL_HOST` / `MEMPOOL_PORT`**：服务监听地址环境变量（手动/自动启动都认）；
  `MEMPOOL_AUTOSTART_TIMEOUT` 控制等就绪超时（默认 120s）
- 新增 4 个测试：零设置端到端（真拉起真检索）、probe 三态、远程地址拒绝、.env 加载

### 修复

- **测试套件数据隔离**（`tests/conftest.py`）：全套件改用独立临时数据目录。
  此前测试直接写用户真实的 `~/.agent_memory_pool`，向量库随历史测试运行滚雪球，
  faiss 先取 top-k 再按 user 过滤导致检索被历史数据挤占、用例开始随机挂（已实测复现）；
  同时避免测试垃圾污染真实记忆库

## [0.1.0] - 2026-08-11

首个开源版本（技术验证阶段）。

### 核心能力

- **共享内存池服务**：FastAPI 独立服务进程独占数据库（单写者架构），
  `/add` `/search` `/health` `/health/models` 四端点
- **存储栈**：mem0 2.0.13 + faiss 本地向量库 + fastembed 本地 embedding
  （写入/检索离线可用）+ SQLite 关系三元组表
- **分层记忆**：realtime（infer=False 老实存）→ LLM 归纳固化 → longterm + 实体关系；
  TTL 清理（到期且已固化才删，未固化保留告警）；闲置超时兜底触发固化
- **多 Agent 协作**：真 LLM 任务拆解（断环/剔畸形）→ 流式 DAG 调度（重试 + fallback）
  → 多厂家真 Agent（claude 走 anthropic 端点、其余走 OpenAI 端点，同一网关）；
  下游按 `user_id + run_id` 承接上游产出
- **健康检查**：密钥校验 + 模型通路并发探针；模型运行时从网关清单选取、探针验通路
- **双接入口**：HTTP（client_sdk）+ MCP 薄代理（add_memory / search_memory），
  落同一份数据
- **测试**：70 个测试（无云 key 60 passed + 10 skipped；含真起进程、并发压测、
  真多厂家协作用例）

### 本次整合改动

- 依赖解耦：`mem0ai` 从本地路径编辑安装（`../mem0`）切换为 PyPI 固定版本
  `mem0ai==2.0.13`（与本地验证版本逐字节一致），仓库自包含可独立克隆构建
- `test_e2e_full_chain.py` 补 skipif 守卫：无网关 key 时跳过而非失败
- 诊断脚本移入 `scripts/`（`diag_e2e.py`、`verify_runid.py`）
- 新增 skill 打包：`SKILL.md` + `references/`（architecture / api-reference / workflows）
- 新增 `README.md`、`LICENSE`（MIT）、`.gitignore`、本文件
