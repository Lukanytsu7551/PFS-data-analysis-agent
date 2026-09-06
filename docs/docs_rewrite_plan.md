# PFS 报表数据分析 Agent 技术文档重构方案

> 文档状态：已执行
> 目标文件：`docs/PFS数据分析Agent项目技术文档.md`
> 事实基线：当前项目源码、`docs/HANDOFF.md`、`docs/FUNCTION_COMPATIBILITY_MATRIX.md`
> 参考材料：`ai-agents-from-zero-main/实战项目-深度研搜`、`ai-agents-from-zero-main/实战项目-电商问数`

> 当前范围说明（2026-09-04）：技术文档中的 Evidence Ledger、Lineage 治理、质量门、语义复算和审批/修订只作为历史设计或后续候选保留；当前文档应以轻量 Claim/Evidence/来源快照、参考 Agent 工具结果留痕，以及 PFS 的 UI、品牌、数据分析和交付能力为准。

## 1. 当前项目真实技术架构总结

PFS 是面向报表和经营数据的桌面端数据分析 Agent。系统采用 OpenAI-compatible Tool Calling 架构，普通对话主链由 `BusinessAgent.run()` 自研循环控制，不依赖 LangChain Agent Executor 或 LangGraph 作为默认聊天运行时。

系统由以下能力组合构成：

1. Flask/Waitress 提供页面、HTTP API 和 SSE 流式通道。
2. `ChatSession` 管理会话、历史、数据源、Workspace、取消和工具审计。
3. `BusinessAgent` 负责 Prompt 构造、LLM 调用、Tool Call 解析、工具执行和终止判断。
4. Tool Schema、Registry、Policy Gate、Executor 和 `ToolResultEnvelope` 共同构成工具运行合同。
5. CSV、XLSX、SQL 和合并数据源统一实现 `DataSource` 接口。
6. DuckDB/SQL 和 Python 分析函数负责确定性计算，模型不直接承担业务数值计算。
7. Job、Run、Artifact、Claim、Evidence 和 Audit 分别承载任务状态、交付物与轻量结果留痕；Evidence Ledger/Lineage 治理不属于当前运行链路。
8. Skill、Command、MCP、Teams 和 Workflow 是可选扩展机制，不能与默认聊天主循环混为一谈。

准确的技术定义如下：

> PFS 是以 OpenAI-compatible LLM 为决策层、以受控 Tool Calling 为执行协议、以确定性数据计算为事实来源、以 Session/Run/Job 为运行状态、以 Artifact/Claim/Evidence 为轻量交付留痕载体，并通过 Flask/SSE 工作台向用户提供实时分析过程与结果的数据分析 Agent。

## 2. 当前文档存在的问题

| 问题 | 表现 | 重构动作 |
|---|---|---|
| 章节结构不规范 | 一级章节和内部小节均使用二级标题，编号层级重复 | 统一为 `# 文档标题`、`## 章节`、`### 小节`、`#### 子项` |
| 存在拼接痕迹 | 最小 Agent 示例中存在空标题，部分章节转场重复 | 删除空标题，合并重复说明，补充章节导语和上下游关系 |
| 教材式表达偏多 | “第一遍阅读”“第几步”“用 Mock 学循环”等 | 改为工程职责、建设阶段、实施基线和运行判定 |
| 项目背景偏弱 | 开篇很快进入 Agent 概念，业务角色和输入输出不完整 | 补充业务背景、用户角色、典型场景、系统价值、非目标和边界 |
| 模块描述颗粒度不一致 | 部分模块只有文件列表，部分模块只有概念解释 | 核心模块统一补充职责、位置、输入、输出、依赖、异常和扩展点 |
| 接口协议不完整 | 已有聊天链路，但缺少接口地图和 SSE 事件合同 | 增加 API 分类、关键路由和事件字段说明 |
| 配置说明分散 | Provider、端口、数据目录、功能开关分别出现 | 增加配置层级、敏感信息规则和主要环境变量章节 |
| 从零实现过于教程化 | 以 calculator 和“第一步、第二步”展开 | 重写为最小核心架构、工程基线和能力演进，不使用课程口吻 |
| 验证内容与用户要求冲突 | 出现测试目录、Mock 测试、测试命令和自测式表达 | 不设置测试章节；只保留运行判定、质量门禁和故障定位所需内容 |
| 当前限制不集中 | 边界散落在多个章节 | 增加系统限制、风险和 Architecture Decision 章节 |

## 3. 参考文档值得借鉴的结构

参考文档的可复用部分不是教学口吻，而是其信息组织方式：

- 先说明项目解决的问题，再展示整体架构和模块关系。
- 用总体架构图、时序图和流程图建立全局模型。
- 每个关键模块独立成章，并从概念映射到真实文件、类和函数。
- 以一条代表性任务贯穿 API、Agent、工具、数据、结果和前端。
- 将元数据、上下文、SQL 生成与执行、流式协议分别展开。
- 对关键设计解释采用方案、原因、收益、代价和替代方案。
- 对接口和事件先定义合同，再解释实现。
- 明确当前能力与故意保留的边界。

不沿用以下教学属性：

- 本章学习目标、学习建议和知识复习；
- “跟着做”“学完应该掌握”“是不是很简单”；
- 自测题、课程总结和面向初学者的练习任务；
- 为演示概念而虚构当前项目不存在的框架或服务。

## 4. 当前项目核心模块

| 层级 | 核心模块 | 主要职责 |
|---|---|---|
| 交互层 | `templates/agent_chat.html`、`frontend/` | 工作台、输入、SSE 消费、时间线、图表和交付物展示 |
| API 层 | `api/` | 会话、聊天、数据源、任务、Workflow、Artifact、Audit 等接口 |
| Agent Runtime | `agent/agent.py` | 模型循环、工具调用、边界控制和事件输出 |
| 模型层 | `LLM/` | Provider 配置、客户端创建、模型能力和成本元数据 |
| Prompt/Context | `agent/prompts.py`、`agent/instructions.py`、`agent/compaction.py` | 系统指令、动态上下文和上下文压缩 |
| Tool 层 | `agent/tools/` | Schema、注册、暴露、执行、结果封装和业务工具 |
| 能力编排 | `agent/skills/`、`agent/commands/`、`agent/workflows/` | Skill、Command 和持久化 Workflow |
| 数据层 | `data/sources/`、`data/merged_source.py` | 数据连接、Schema、查询、预览和多源合并 |
| 分析输出 | `Function/` | 清洗、统计分析、图表和 Office 输出 |
| 状态持久化 | `data/session.py`、`data/jobs_store.py`、`data/workflow*.py` | Session、Job、Workflow 状态和 Workspace |
| PFS 治理 | `pfs_agent/` | Policy、Contract、Run、Artifact、Claim、Evidence、Ledger、Audit |
| 基础设施 | `infrastructure/`、`app.py`、`start.command` | 路径、日志、清理、启动和运行目录 |

## 5. 当前 Agent 执行主链路

```text
浏览器提交问题
→ POST /api/session/<sid>/chat
→ 会话所有权、配额、输入、Command/Skill/Activation 处理
→ 固定 DataSourceSnapshot，创建 conversation Job
→ _build_agent() 组装 Provider、Prompt、工具、Workspace 和预算
→ BusinessAgent.run() 构造本轮 messages 和可见 Tool Schema
→ OpenAI-compatible LLM 返回文本增量或 tool_calls
→ 解析 JSON、注册表匹配、Schema 校验和 Policy Gate
→ Python Tool 执行数据查询、分析、图表或导出
→ ToolResultEnvelope 写入 role=tool 消息和运行审计
→ 下一轮 LLM 调用，直至最终文本或受限终态
→ API 编码 SSE，前端更新过程、结果、图表、用量和交付物
→ Run/Artifact/Claim/Evidence/Lineage/Audit 持久化
```

## 6. 当前项目核心代码文件

| 文件 | 核心对象 | 作用 |
|---|---|---|
| `app.py` | `main()`、`ensure_requirements()` | 进程启动、应用创建和服务监听 |
| `api/__init__.py` | `create_app()` | Flask 应用工厂和 Blueprint 注册 |
| `api/chat.py` | `chat_stream()`、`_build_agent()` | 聊天请求生命周期和 Agent 构建 |
| `agent/agent.py` | `BusinessAgent`、`run()` | 默认 Agent Runtime |
| `agent/tools/schemas.py` | `AGENT_TOOLS` | 发送给模型的工具 JSON Schema |
| `agent/tools/registry.py` | `ToolSpec`、`ToolRegistry` | 工具暴露和运行元数据 |
| `agent/tools/results.py` | `ToolResultEnvelope` | 工具结果的统一结构 |
| `pfs_agent/policy.py` | `PolicyGate` | 参数、权限、风险和预算决策 |
| `pfs_agent/runtime.py` | `build_builtin_registry()` | 内置工具到 PFS 合同的映射 |
| `data/session.py` | `ChatSession`、`DataSourceSnapshot` | 会话与冻结数据源视图 |
| `data/sources/base.py` | `DataSource` | 数据源统一接口 |
| `agent/jobs.py`、`data/jobs_store.py` | `JobRunner`、`JobsStore` | 长任务、进度和取消 |
| `pfs_agent/runs.py` | `AnalysisRunRegistry` | 分析 Run 生命周期 |
| `pfs_agent/ledger.py` | `EvidenceLedger` | Claim、Evidence、关系、复算和人工决定 |
| `pfs_agent/artifact_lifecycle.py` | Artifact 生命周期对象 | 交付物登记、归档和追踪 |
| `frontend/features/chat-stream.js` | SSE 消费逻辑 | 将后端事件映射到前端状态 |

## 7. 新文档目录方案

最终仍交付一个 Markdown 文件，内部目录调整为：

1. 文档控制与阅读入口
2. 项目概述与业务边界
3. 技术栈与总体架构
4. 工程结构与代码地图
5. 启动、进程与运行目录
6. HTTP/SSE 接口和前端交互
7. Agent 技术架构
8. LLM Provider 与模型适配
9. Prompt、Context 与上下文压缩
10. Tool 系统与受控执行
11. Skill、Command、MCP 与能力发现
12. Session、State、Memory、Run 与 Job
13. 数据源、Schema 与 SQL 执行
14. 数据分析、图表与交付物
15. Artifact、Lineage、Claim、Evidence 与 Audit
16. Workflow、Teams 与 Delegated LLM
17. 完整真实任务调用链
18. 从零建设同类系统的工程方案
19. 配置、安全、日志与可观测性
20. 部署、启动、停止与复现
21. 二次开发与扩展规范
22. 故障定位
23. 系统限制、风险与架构决策
24. 附录：接口、事件、术语和代码索引

## 8. 旧文档到新文档迁移关系

| 当前内容 | 新位置 | 操作 |
|---|---|---|
| 项目定位与建设目标 | 项目概述与业务边界 | 重写，增加用户、场景、输入输出、非目标 |
| Agent 核心原理 | Agent 技术架构 | 去教程化，保留协议和职责解释 |
| 系统总体架构 | 技术栈与总体架构 | 补充分层、数据流和控制流 |
| 工程目录与模块职责 | 工程结构与代码地图 | 统一模块描述模板 |
| 系统启动与请求执行链路 | 启动章节、接口章节、完整调用链 | 拆分并消除重复 |
| LLM Provider | LLM Provider 与模型适配 | 补充配置优先级、能力差异和替换边界 |
| Prompt 与动态上下文 | Prompt、Context 与上下文压缩 | 增加单轮输入组成和注入边界 |
| Tool 能力系统 | Tool 系统与受控执行 | 补充工具分类、合同、清单和异常 |
| Agent 核心执行循环 | Agent 技术架构 | 作为核心 Runtime 展开 |
| Session、State、Context、Memory | State、Memory、Run 与 Job | 明确各对象生命周期和关系 |
| 数据分析业务执行体系 | 数据源章节、分析章节、治理章节 | 按数据流拆分 |
| 最小可运行 Agent | 从零建设工程方案 | 去除课程式步骤和 Mock 学习段落 |
| 从最小闭环到完整 PFS | 从零建设工程方案、架构决策 | 合并为阶段化建设基线 |
| 运行诊断 | 故障定位 | 保留工程排障，不写测试教程 |
| 二次开发 | 二次开发与扩展规范 | 增加接口合同和注册路径 |
| 环境部署与运行管理 | 部署、启动、停止与复现 | 补充运行目录和配置边界 |

## 9. 当前文档与源码不一致或不够准确的地方

1. 当前文档将 `tests/` 放入主要工程地图，与用户要求的文档范围不一致；新文档不以测试体系为内容模块。
2. 当前文档对 Tool 层主要使用“四层结构”概括，但源码实际还包含 exposure/discovery、并行安全和 Job 执行模式，需要补充。
3. 当前文档把 Skill 和 Command 放在 Prompt 章节中简述，未完整说明 Loader、Registry、Executor、Activation 和可用性条件。
4. 当前文档对 API 主要描述聊天入口，未集中列出数据源、模型、Job、Workflow、Artifact、Audit 和 Workspace 接口。
5. 当前文档以通用“最小 Agent”示例占据较大篇幅，对 PFS 的工程建设顺序、模块边界和替换策略说明不足。
6. 当前文档中的模型默认上下文值可能随配置变化；新文档应区分代码默认值、Provider 声明值和上游真实限制。
7. 当前文档对 Memory 和 Knowledge 的边界已有提醒，但需要进一步区分会话历史、文件型长期记录、知识库检索和数据快照。
8. 商业画布和 Google Sheets 已退役，Teams/Hooks/GPU/飞书/云登录默认休眠的产品边界现已集中写入 `docs/HANDOFF.md`、`README.md` 和能力矩阵；本方案保留为已执行的文档重构记录。

## 10. 当前缺失但必须补充的技术内容

- 文档元数据、事实来源和状态术语；
- 业务角色、典型场景、输入输出合同和非目标；
- 技术栈总表及替换边界；
- 系统数据流、控制流和部署图；
- 核心模块统一责任卡；
- API 地图和 SSE 事件合同；
- 单轮模型输入的真实组成；
- Tool 暴露、发现、并行、Job 和结果有界化；
- Skill、Command、MCP、Workflow 和 Teams 的清晰边界；
- 数据快照、Schema、质量门和 SQL 安全；
- Artifact、Claim、Evidence、Lineage、RecomputeContract 和人工复核关系；
- 完整代表性任务的逐阶段代码映射；
- 从空目录建设到 PFS 完整形态的工程阶段和模块交付；
- 配置优先级、敏感信息、功能开关、日志与审计定位；
- 已知限制、性能边界、安全风险和关键 Architecture Decision。
