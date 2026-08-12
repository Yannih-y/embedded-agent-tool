# 如何使用

装完就能用。这份文档讲**日常怎么用**：约定、各工具里怎么说、跨软件互通、
AgentClaw 与 IDE 的分工。装机 / 换机见 [deployment.md](deployment.md)；
接口字段见 [api-reference.md](api-reference.md)。

## 30 秒心智模型

| 概念 | 一句话 |
|------|--------|
| 共享池 | 本机一个服务（默认 `127.0.0.1:8800`），所有 agent 读写同一份记忆 |
| `user_id` | **共享边界**——同一个人在所有工具里必须用同一个值，记忆才互通 |
| `agent_id` | **留痕**——谁写的（`cursor` / `claude-code` / `agentclaw`…），不参与检索过滤 |
| 两个工具 | `add_memory` 写、`search_memory` 查 |
| 密钥 | 纯读写**不需要**；只有任务拆解 / 记忆固化 / 多厂家协作才要网关 key |

```
Cursor ──stdio MCP──┐
Claude Code ────────┤
Codex / Windsurf / Kiro ─┤──→ 本机内存池服务 ──→ ~/.agent_memory_pool/
AgentClaw ──HTTP /mcp ──┘
Python / curl ──REST /add /search ─┘
```

## 日常约定（先定好再写）

1. **选定一个 `user_id` 并写死**（例如你的昵称缩写）。所有 IDE、AgentClaw、脚本都用它。
2. **`agent_id` 用工具自己的名字**：`cursor`、`claude-code`、`codex`、`windsurf`、`kiro`、`agentclaw`；
   同一工具开多个会话同时干活时加短后缀区分（如 `cursor-nas1`），否则留痕混淆。
3. **写什么**：需要跨软件接续的工作上下文、协作结论、暗号/里程碑、给下游 agent 的产出。
4. **不写什么**：密码、token、银行卡；单软件内部的私人事实（AgentClaw 用内部 `remember`/`personal_*`）。
5. **检索时带上同一个 `user_id`**，否则查不到别人写的（也查不到自己写错边界的）。
6. **矛盾以新为准**：记忆是 append-only 的，事实会过时（如"任务无人认领"晚些就不成立）；
   同主题冲突时看 `age`/`created_at` 取最新，更正过时事实时写一条新记忆声明作废。
7. **状态盘点别只查一次**：top-k 语义检索对"XX 做完没"这类盘点必有遗漏，
   用专有名词（run_id、服务名、项目名）多角度查几次再下结论。
8. **写入带【项目/主题 · 类型】前缀**（如【NAS 部署 · 第①项完成】【项目X · 决议】）：
   同一 `user_id` 下**不分项目**，所有项目共用一个命名空间——强前缀是检索聚焦、
   防跨项目串味的唯一手段；查询时也带上项目词。存量记忆不回填，增量生效。
   （真按项目硬隔离的 metadata 过滤维度已列入路线图，等记忆量大到检索可感知
   变差再动 API。）

## 在 IDE 里怎么用（Cursor / Claude Code / Codex / Windsurf / Kiro）

MCP 配好并重启工具后，对 agent 直接说自然语言即可——它会调 `add_memory` /
`search_memory`。

### 写入（种记忆）

> 用 add_memory 记住：登录方案定了用 JWT + refresh token，user_id 用 `<你的id>`，agent_id 写 cursor。

或更短：

> 把这句话存进共享记忆池（user_id=`<你的id>`）：项目 X 的下一刀是补 e2e。

### 检索（读记忆）

> 用 search_memory 查 user_id=`<你的id>`：登录方案用的什么。

跨工具验证（推荐首次接入时做一次）：

1. 在工具 A：「记住测试暗号是紫罗兰行动，user_id=`<你的id>`」
2. 在工具 B：「查 user_id=`<你的id>` 的测试暗号」→ 应答出暗号

工具面板里名称一般是 `add_memory` / `search_memory`（或带 server 前缀，
视客户端而定）。

### 让每个新会话自动带上「共享池意识」（推荐，Cursor 项目规则）

不想每次开新 chat 都解释一遍？在工作区建
`.cursor/rules/shared-memory-pool.mdc`（frontmatter 写 `alwaysApply: true`），
内容写清三件事，之后每个新会话天生就会用：

1. **约定**：统一的 `user_id`、本工具的 `agent_id` 标识
2. **何时用**：用户说「查共享池 / 接任务 / 其他工具做了什么」→ `search_memory`；
   产出需跨会话接续 → `add_memory` 回写；闲聊不灌水
3. **任务交接协议**：搜「任务书」接活 → **先写认领**（查无人认领后立即
   `add_memory` 一条「已认领 <run_id> + 会话标识」，防两个会话重复接活）→
   执行中回写进展 → 完成写总结（阻塞也回写）

这样跨会话协作只需要对新会话说一句「查共享池接任务」。其他工具同理：
Claude Code / Codex 用 nestwork 启动注入或各自的全局规则文件写同样内容。

## 在 AgentClaw 里怎么用

AgentClaw 走 **HTTP MCP**（`http://127.0.0.1:8800/mcp`），不是 stdio。
配置步骤见 [deployment.md D 节](deployment.md#d-接入-agentclaw-网关http-mcp)。

### 和内部记忆的分工

| | 共享池（`agent-memory-pool__*`） | 内部记忆（`remember` / `personal_*`） |
|--|------|------|
| 用途 | 与 IDE 侧 agent **互通**的工作上下文 | 个人事实、画像、仅网关内使用的记忆 |
| 边界 | `user_id` 与 IDE 约定一致 | 不进共享池 |
| 会话 | public / personal（已审查登记）可用；sensitive+ 仍拒 | 按既有 classification 规则 |

对 AgentClaw 可以说：

> 查一下共享记忆池里 user_id=`<你的id>` 今天关于登录方案的结论。

> 把「飞书渠道联调通过」写进共享记忆池，方便 Cursor 那边接续。

系统提示词里应有「跨软件共享记忆池」小节（约定 `user_id` / `agent_id=agentclaw`）。
协调型角色（如 hermes）若要主动用共享池，需在该 agent 的 `tools` 白名单里放行两工具。

### 启动顺序

AgentClaw 只在自己启动时连一次 `/mcp`。请保证内存池**先在线**：

- Windows：登录自启（`pythonw -m memorypool.daemon`，bootstrap 会注册）
- 或手动：`MemoryPoolClient().health()` / 起一次任意 MCP 客户端（stdio 会自动拉起）

池子中途重启不影响已连接后的调用（stateless HTTP，每次 POST 独立）。

## Python / HTTP 怎么用

```python
from memorypool.client_sdk import MemoryPoolClient

client = MemoryPoolClient()  # 服务没起会自动拉起
client.add("登录模块用 JWT", user_id="alice", agent_id="my-script")
hits = client.search("登录方案", user_id="alice")
print(hits["results"])
```

```bash
curl -X POST http://127.0.0.1:8800/add -H "Content-Type: application/json" \
  -d '{"messages":"登录模块用 JWT","user_id":"alice","agent_id":"curl"}'
curl -X POST http://127.0.0.1:8800/search -H "Content-Type: application/json" \
  -d '{"query":"登录方案","user_id":"alice"}'
curl http://127.0.0.1:8800/health
```

`auto_start=False` 可关掉自动拉起（测试或强制要求服务已运行时）。

## 典型场景

### 1. IDE ↔ IDE 接续

Cursor 写完方案 → `add_memory` → Claude Code / Codex 开新会话 → `search_memory`
继续实现。同一 `user_id` 即可。

### 2. IDE ↔ AgentClaw（网关 / 飞书等渠道）

IDE 侧记下「用户偏好回复简短」或「今晚要联调飞书」→ AgentClaw 会话里检索共享池 →
渠道 bot 按同一上下文行动。反过来：AgentClaw 在渠道里得出结论 → 写回共享池 →
回 IDE 继续改代码。

### 3. 多厂家 DAG 协作（进阶，需网关 key）

服务进程内：`decompose` → `Orchestrator` + `register_agents` → 产出进池
（`run_id` 标批次）→ `Consolidator` 固化。详见 [workflows.md](workflows.md)。

### 4. 快记忆 + 慢记忆（可选 nestwork）

| 放共享池 | 放 nestwork |
|----------|-------------|
| 临时协作产物、暗号、当日结论 | 长期规则、偏好、项目状态卡 |
| 语义检索 | 会话启动注入，人可读可 git 审计 |

### 5. 圆桌会议（多 agent 议题决策）

对主持 agent（如 Cursor / Claude Code 里的 AI）说一句「开圆桌：议题 XXX，
成员 AgentClaw + hermes」，主持自动跑完整流程，池子当会议室：

1. **第一轮并行独立发言**：成员互相看不到对方观点（并行发出防污染）；
   提示词写明「直接发言、不调工具、字数上限、最后标注最大风险」
2. **第二轮交叉质询**：把对方发言互递，要求指出最站不住的一点并修正自己
3. **主持收敛决议**：矛盾点主持裁决（成员自己前后矛盾的话术是好抓手）
4. **全程留痕**：每条发言 `add(run_id="roundtable-<日期>-<议题>",
   agent_id=<真实发言人>)`；决议同步进项目文档
5. **会后任查**：任何工具里「查 user_id=<你的id> 圆桌决议」即可读到

实操经验（2026-08-12 首场会议，0.3.0 设计定稿）：成员空回复属 LLM 偶发，
重试一次即可；两位成员独立指出同一风险 = 强信号，决议必须处理。

## 服务生命周期（日常）

| 操作 | 怎么做 |
|------|--------|
| 看是否在线 | `curl http://127.0.0.1:8800/health` |
| 日志 | `~/.agent_memory_pool/logs/server.log` |
| PID | `~/.agent_memory_pool/server.pid` |
| 停服务 | Windows：`taskkill /PID <pid> /F`；POSIX：`kill $(cat ~/.agent_memory_pool/server.pid)` |
| 再起 | 任意 SDK/MCP 调用即可自动拉起；或 `pythonw -m memorypool.daemon` |
| 密钥 | 编辑 `~/.agent_memory_pool/.env` 后重启服务进程 |

## 常见问题（使用向）

| 现象 | 处理 |
|------|------|
| 工具 B 查不到工具 A 写的 | 核对两边 `user_id` 是否完全一致；是否写进了共享池而不是内部 remember |
| AgentClaw 说没有共享池工具 | 看该 agent 白名单是否含 `agent-memory-pool__*`；网关是否已连上 `/mcp`；personal 会话是否已做审查登记 |
| AgentClaw 启动后连不上 | 池子未先启动——开登录自启，或先手动拉起再重启网关 |
| MCP 列表里没有工具 | 重启 IDE；确认 mcp 配置路径与 command 指向本机 venv |
| 不想用自动拉起 | `MemoryPoolClient(auto_start=False)`，或自己常驻 `python -m memorypool.server` |

更多装机 / 端口占用 / PowerShell 乱码等见 [deployment.md 故障排查](deployment.md#故障排查)。
