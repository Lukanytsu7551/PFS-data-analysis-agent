# PFS 数据分析 Agent：第 1 天架构学习地图

> 目的：帮助开发者从一次用户提问追踪到模型、工具、数据和前端事件。  
> 范围：本文件记录当前 `Data-Analysis-Agent-main/` 源码的静态阅读结果，不代表真实模型、数据库或生产环境已经验收。

> 学习说明：用户当前重点是掌握框架和原理。本文件只作为案例索引，不要求逐行阅读或记忆源码；主学习路线见 [FRAMEWORK_9_DAY_PLAN.md](/Users/yangxuan/Desktop/实习/报表数据分析agent/FRAMEWORK_9_DAY_PLAN.md)。

## 1. 先记住一条主链路

```text
浏览器提交问题
  │
  ▼
POST /api/session/<sid>/chat
  │  api/chat.py：校验请求、会话、用户、数据上下文、source snapshot、job
  ▼
generate() / SSE
  │
  ▼
BusinessAgent.run(...)
  │  agent/agent.py：准备 history、system prompt、可用 tools、预算和循环上限
  ▼
OpenAI-compatible model provider
  │  返回普通文本，或一个/多个 function tool call
  ▼
工具参数解码 + allowlist/参数校验 + 权限/工作区约束
  │
  ├── get_schema / query_data / profile_data
  ├── run_analysis / select_chart / generate_chart
  ├── export / knowledge / memory / workspace / workflow ...
  │
  ▼
ToolResultEnvelope + artifact/source/event
  │
  ▼
追加回模型 history，继续下一轮，或结束
  │
  ▼
tool_start / tool_audit / tool_end / text_delta / chart_html / error / done
  │
  ▼
前端 API client + app store + stream/UI handlers
  │
  ▼
时间线、图表、结果、错误和完成状态
```

## 2. 每一层到底负责什么

### A. 进程启动层

位置：`Data-Analysis-Agent-main/app.py:18-141,146-180`

- 判断当前是否是本地、Vercel 或 Railway 环境。
- 当前源码在本地入口会检查并自动安装缺失依赖，然后初始化日志、清理服务和 Flask app。
- 通过 Waitress 启动本地服务，也预留 Gunicorn 路径。

学习结论：`app.py` 是进程入口，不是 Agent 大脑。启动时自动安装依赖属于后续 PFS 基座需要重新设计的行为。

### B. Flask 应用工厂与路由层

位置：`Data-Analysis-Agent-main/api/__init__.py:38-190`

- `create_app()` 创建 Flask 实例。
- 注册 models、datasource、chat、sessions、jobs、workflows、knowledge、workspace、teams 等 blueprint。
- 对跨源写入、云端认证和安全响应头设置全局边界。
- `/` 在正常本地模式渲染 `templates/agent_chat.html`，不是父级静态 `index.html`。
- `/api/health` 只表示最小服务探针可返回，不表示模型或数据源可用。

学习结论：blueprint 是 API 领域边界；真正的业务链路通常需要继续追到 `agent/`、`data/` 和 `Function/`。

### C. Chat API 与会话上下文

位置：`Data-Analysis-Agent-main/api/chat.py:770-898`、`api/chat.py:1060-1185`

收到 `POST /api/session/<sid>/chat` 后，代码依次处理：

1. 确认请求正文是 JSON 对象。
2. 确认 `message` 是非空字符串。
3. 获取或创建 session。
4. 在云端检查登录、会话所有权和 Token quota。
5. 解析 command/skill activation。
6. 恢复数据上下文并检查 SQL 分析表范围。
7. 创建 file-history snapshot 和 tracked conversation job。
8. 固定当前数据源 snapshot。
9. 在 generator 中把 Agent 事件编码成 SSE：`data: {...}\n\n`。

学习结论：一次聊天运行不只是 `message -> LLM`；它还需要身份、数据版本、任务记录和恢复上下文。

### D. Agent 核心与运行循环

位置：`Data-Analysis-Agent-main/agent/agent.py:449-480,1220-1264,1835-1915`

`BusinessAgent` 当前包含：

- `MAX_ITERATIONS = 120`：单次 Agent Loop 的迭代上限。
- `MAX_RUN_SECONDS = 1800`：运行时间边界。
- 委派/团队调用上限。
- prompt history、上下文估算和 compaction。
- 模型 provider 调用和 retry。
- tool call 解析、工具执行、结果封装和事件输出。

`run()` 是一个 generator，向上层逐个 yield event，而不是一次性返回最终字符串。事件契约在 `agent.py:1250-1263` 附近列出，包括：

- `tool_start`
- `text_delta`
- `chart_html`
- `text`
- 各类 outline
- `usage`
- `reasoning`
- `done`
- `error`

学习结论：Agent 的最小结构可以抽象成：

```text
history + tools + policy
  → model decision
  → validate tool call
  → execute tool
  → append tool result to history
  → continue or finish
```

### E. 工具契约层

位置：`Data-Analysis-Agent-main/agent/tools/schemas.py:16-35,173-315`

`AGENT_TOOLS` 是发送给模型的 JSON schema 列表。工具 schema 只描述模型“可以请求什么”，不等于工具已经被安全执行。

核心分析工具包括：

- `get_schema`：获取可用表和列。
- `query_data`：执行受约束的 SQL SELECT。
- `run_analysis`：调用统计/机器学习分析模板。
- `select_chart`：根据意图和可用列选择图表类型。
- `generate_chart`：根据确定的 chart type、SQL 和 field mapping 生成图表。
- `profile_data`：查看行列、类型、缺失、日期解析和数值概况。

在 `agent/agent.py:2365-2448` 一带可以看到输出工具策略、JSON 参数解码和预分发校验；在 `agent/agent.py:2640-2755` 一带可以看到工具执行分派、结果封装和 `tool_audit`。

学习结论：模型只能提出 tool call；真正的 allowlist、参数校验、权限和副作用控制必须在代码中执行。

### F. 数据与分析层

位置：

- `Data-Analysis-Agent-main/data/sources/base.py`
- `data/sources/csv.py`
- `data/sources/excel.py`
- `data/sources/sql.py`
- `data/sources/workspace_persistent.py`
- `agent/tools/business/data.py`
- `Function/Analyze/registry.py`

职责分为：

1. 把文件、SQL 数据库或外部源注册为 data source。
2. 暴露 schema 和表详情。
3. 在受限上下文中执行 SELECT 和派生表操作。
4. 调用确定性的统计/机器学习函数。
5. 把结果变成可查询表、图表或导出 artifact。

学习结论：LLM 适合理解“用户要看什么”，不适合替代 SQL 引擎、统计函数和结果校验。

### G. 事件与前端层

位置：

- `Data-Analysis-Agent-main/frontend/core/api-client.js`
- `frontend/core/app-store.js`
- `frontend/core/event-bus.js`
- `frontend/entries/chat-app.js`
- `templates/agent_chat.html` 底部的 vendor 与 module script

前端使用 API client 发请求，用 app store 保存状态，再通过 legacy/module handlers 消费事件。模板底部加载：

- `marked.min.js`
- `purify.min.js`
- `vue.global.prod.js`
- `static/dist/chat-app.js`

学习结论：`templates/agent_chat.html` 是当前 Flask 入口实际渲染的产品页面；父级 `index.html` 是另一个尚未接入该 Flask 链路的静态原型。

## 3. 一个具体问题是怎么走的

假设用户问：“按月份统计销售额，并画趋势图。”

### 可能的运行过程

1. 前端把问题和 session id POST 到 `/api/session/<sid>/chat`。
2. Chat API 确认问题合法，并固定当前数据源 snapshot。
3. `BusinessAgent.run()` 准备历史、系统提示、工具列表和预算。
4. 模型先调用 `get_schema`，确认真实表名和列名。
5. 模型调用 `query_data`，执行按月份聚合的 SELECT。
6. 模型调用 `select_chart`，让图表 registry 返回合适的 chart id 和字段角色。
7. 模型调用 `generate_chart`，使用真实 SQL 结果列生成趋势图。
8. 每次工具执行都会形成 audit/event，并把工具结果追加回 history。
9. Agent 输出文字结论、图表事件和 `done`。
10. 前端将事件变成工具时间线、图表和最终答案。

### 这个过程中尚未自动保证的事情

- “销售额”的业务口径是否正确。
- 月份字段是否完整、时区是否一致。
- 数据是否存在重复、漏数或异常值。
- 图表是否与指标定义一致。
- 模型是否真的完成了所需工具调用。
- 真实 provider、数据库、SSE 长连接和浏览器 UI 是否在当前环境成功。

这些正是 PFS 要增加 Metric Contract、Data Quality、Calculation Lineage 和 Evidence/Claim 层的原因。

## 4. 当前事实状态

| 对象 | 当前状态 | 证据边界 |
|---|---|---|
| 原项目源码入口 | 已存在 | 本地附件和远端 main 关键文件抽查一致 |
| Agent Loop 设计 | 已存在（源码） | 可读到循环、工具和事件代码；真实模型待验证 |
| 数据分析模块 | 已存在（源码） | 目录和注册表存在；结果正确性待验证 |
| 父级 PFS 页面 | 已实现（静态原型） | 无脚本、无 API 接入 |
| PFS 独立 Agent | 待实现 | 还没有自己的 contract 和主入口 |
| PFS 证据与口径治理 | 待实现 | 需要新增业务层模型和 UI |
| Docker/数据库/线上分析 | 待验证 | 第 1 天不启动全栈 |

## 5. 第 1 天需要你掌握的三个区分

### 区分一：API 与 Agent

API 负责接收请求、建立上下文和传输事件；Agent 负责在约束内多轮决定是否调用工具。

### 区分二：Tool schema 与 Tool executor

Schema 告诉模型参数格式和使用说明；executor 才真正访问数据、运行分析或生成文件。两者之间必须有校验和策略门禁。

### 区分三：源码存在与能力验收

源码、静态语法、mock、真实服务、真实模型、部署和线上流程是不同证据等级，不能合并为一个“已完成”。

## 6. 第 1 天验收题

请先不用看答案，尝试用自己的话回答：

1. `/api/session/<sid>/chat` 为什么要先固定数据源 snapshot，再进入 Agent？
2. `BusinessAgent.run()` 为什么返回一串 event，而不是只返回一个字符串？
3. 模型返回 `query_data` 的 SQL 后，至少有哪几层代码可以阻止危险或错误调用？
4. 父级 `index.html` 为什么目前不能证明 PFS Agent 已经接通？
5. 如果模型说“我已经画好趋势图”，但没有调用 `generate_chart`，系统应该相信它吗？

下一步在第 1 天内只做一个小练习：你先回答这 5 题，我根据你的回答补充解释，然后我们一起定位一个最小的 `MockModelProvider`/工具测试切入点。暂不进入大规模代码改造。
