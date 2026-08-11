# Changelog

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
