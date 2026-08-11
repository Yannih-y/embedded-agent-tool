# Changelog

## [Unreleased]

### 新增

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

- 全量校订：workflows.md 测试计数改为 64+10=74；SKILL.md / README /
  workflows.md 补部署文档链接；SKILL.md MCP 支持清单扩为五工具；
  bootstrap.ps1 收尾提示的 user_id 示例通用化（不再硬编码个人 id）

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
