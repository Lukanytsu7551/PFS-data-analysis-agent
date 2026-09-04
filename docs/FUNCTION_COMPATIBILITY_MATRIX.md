# PFS 数据分析 Agent：功能兼容矩阵

> 建立日期：2026-08-28。用途：记录原 Data-Analysis-Agent 能力在 PFS 中的真实迁移和验证状态。

## 状态定义

| 状态 | 含义 |
|---|---|
| 源码存在 | 找到实现或注册点，但没有足够运行证据 |
| 静态通过 | 语法、导入、单元测试或前端构建通过，但未完成真实流程 |
| 模拟通过 | 用固定 fixture、mock 或替身服务完成测试 |
| 本地真实通过 | 本机真实进程、真实接口和真实输入完成验证 |
| 集成通过 | 依赖的数据库、队列、模型或外部服务一起完成验证 |
| 线上通过 | 部署环境完成真实验收 |
| 阻塞 | 已明确缺少环境、凭据、依赖或实现；不计为完成 |

“当前状态”是本次审计结论，不是对原项目历史测试结果的继承。

> 当前范围决定（2026-09-04）：只保留轻量结果留痕和 PFS 的 UI、品牌、数据分析与交付能力。新增 Evidence Ledger 治理、质量门、语义复算、审批/修订、SQLite Ledger 后端/迁移及其前端/HTTP 入口均已撤回；历史记录中的这些能力不计入当前完成度。

## A. 用户入口和 API

| 编号 | 功能组 | PFS 入口 | 当前证据 | 状态 | 下一步验证 |
|---|---|---|---|---|---|
| UI-01 | 主工作台/聊天 | templates/agent_chat.html、GET / | PFS 页面存在；桌面端已真实上传四工作表 XLSX并完成四类交付；另用 54.7 MB、250 万行 CSV 真实点击取消；缺字段、异常日期、101 MiB 超限上传、模型未配置和交付失败均显示可执行修复建议 | 本地真实通过（复杂 XLSX + 本阶段错误/取消桌面切片） | 通用聊天多轮主流程和部署验收 |
| UI-02 | 登录与会话 | api/auth.py | 路由已注册 | 源码存在 | 云模式真实登录、权限和会话隔离 |
| API-01 | 健康检查 | GET /api/health | 127.0.0.1:5012 回读 ok=true、product=PFS | 本地真实通过 | 部署环境回读 |
| API-02 | 聊天、SSE、停止 | api/chat.py | DeepSeek 真实任务通过 SSE 返回工具事件、Token usage、最终文本和 done；长请求中调用 `/stop` 后真实返回 `stopped`，任务为 `canceled`，历史为空；空白隔离模型配置已在桌面页面回读 `model_not_configured` 的中文配置引导；Job 存储重开、备用模型替身和运行级预算门禁已有回归；客户端在 Agent 构建前或收到部分文本后断开，会取消 tracked turn、释放快照且不继续发送 done | 本地真实通过（单轮 DeepSeek + 停止 + 未配置模型桌面 + Job 存储恢复 + 运行级工具上限）+ 静态故障切换/Token/费用门禁回归 + SSE 断开收尾回归 | 多轮流式、超时、真实网络错误、流中断重连/续传和工作流恢复；真实供应商账单对账仍待实现 |
| API-03 | 数据源 | api/datasource.py | 固定/上传 CSV/XLSX 通用上传入口存在；桌面浏览器已真实上传含说明、空表、业务明细和异常指标的四工作表 XLSX；101 MiB CSV 返回 100 MB 上限提示且服务端未保留超限副本 | 本地真实通过（复杂 XLSX + 超限上传桌面切片） | 更多复杂业务 Excel 及外部连接器 |
| API-04 | 输出 | api/output.py、Function/Output、tests/test_pfs_exports.py、tests/test_pfs_delivery_artifacts.py | 固定 PFS fixture 的 Excel、Word、PPT、Dashboard 已结构解析；三种 Office 和 Dashboard HTML 下载 HTTP 200；多 Sheet Excel/Dashboard 已确认只读取所选工作表，中文文件名响应头已回归 | 本地真实通过（固定 fixture + 多 Sheet HTTP/文件结构） | 复杂报表、原生应用视觉、跨平台打开与品牌元数据验收 |
| API-05 | PFS 可信分析 | api/pfs.py、pfs_agent/reporting.py、tests/test_pfs_http_vertical_slice.py | `AnalysisResult` 保留 Metric Contract、DataSnapshot、分组结果、轻量 `claims/evidence` 和来源快照哈希；固定 fixture 与上传 CSV/XLSX 的确定性分析已回归 | 本地真实通过（fixture + 上传 HTTP） | 真实业务语义、更多指标和线上验收 |
| API-06 | 上传到 PFS 报表闭环 | api/datasource.py、api/pfs.py、pfs_agent/runs.py、tests/test_pfs_http_vertical_slice.py、tests/test_pfs_run_control.py | 四工作表 XLSX 桌面闭环通过；同步报表分析新增会话级运行登记和取消接口，并发 HTTP 回归确认取消返回稳定 `pfs_analysis_canceled` 且不会提交结果；250 万行 CSV 的桌面取消 Run 回读 0 Claim / 0 Evidence；显式 SQLite Run Registry 已验证跨进程传播取消、checkpoint 收口和崩溃 owner 回收；异常日期不再被静默隐藏；受限问句缺少指标列时保留 `source_columns_missing` 并显示中文建议 | 本地真实通过（复杂 XLSX + 取消/字段/日期/超限桌面 + SQLite 跨进程运行控制回归） | 跨服务恢复、Workflow pause/resume、通用聊天 Agent 和真实部署 |
| API-07 | 受限自然语言报表问题 | pfs_agent/query.py、api/pfs.py、frontend/legacy/pfs-report-preview.js、tests/test_pfs_http_vertical_slice.py | 明确问题可映射到指标/日期/分组列，时间范围解析后交给确定性 grouped SUM，并返回解释、Claim/Evidence；报表预览已提供问题输入、识别口径展示和错误反馈；含糊问题明确拒绝 | 本地 HTTP 与前端 production build 通过 | LLM 意图理解、更多指标/计算方式、复杂表达、通用聊天入口、浏览器视觉验收 |
| API-08 | PFS 报表下载与交付 | api/pfs.py、frontend/legacy/pfs-report-preview.js、templates/agent_chat.html、tests/test_pfs_delivery_artifacts.py | 上传 XLSX 的 JSON/CSV 已下载回读；复杂多 Sheet 文件可生成 Excel/Word/PPT/Dashboard；交付 Artifact 保留安全元数据、来源快照、成本、下载历史和工作区身份，不再提供 Evidence 治理 lineage | 本地真实通过（HTTP + 复杂 XLSX + 文件结构） | 复杂 Office 内容、原生视觉/跨平台打开和真实部署 |
| API-09 | 任务与成本审计 | api/audit.py、pfs_agent/audit.py、frontend/legacy/job_history.js、frontend/features/ui/job-history-ui.js、tests/test_pfs_audit.py | 保留会话所有权、Job/运行事件、模型、工具、重试、错误、Artifact、Token、成本和耗时的安全投影；active 路由不加载 Claim/Evidence 治理对象，任务历史只展示 Job 与 Artifact | 本地契约通过 | 真实失败/重试注入、长会话性能、部署和线上验收 |

## B. Agent 工具和分析能力

| 编号 | 功能组 | PFS 入口 | 当前证据 | 状态 | 下一步验证 |
|---|---|---|---|---|---|
| AG-01 | 数据/工作区观察 | agent/tools/schemas.py：workspace_status、get_schema、get_table_detail、profile_data | 固定 CSV 工具回归通过；DeepSeek 真实任务实际调用 workspace_status/get_schema 并读取 9 行 schema | 本地真实通过（固定 CSV） | 挂载真实工作区、权限隔离和大数据量验收 |
| AG-02 | 查询与派生表 | query_data、create_analysis_table、delete_analysis_tables | 固定 CSV 真实执行 schema、只读聚合、派生表创建/替换/删除；后台 query/create 路径重复执行 SQL 安全校验；非法 SQL、原始表覆盖、原始表删除和未知表均 fail closed 且不改变源表；CSV、Excel、HTTP、飞书内存源已登记 raw/derived 表边界，SQL 缓存表与工作区 registry 继续保护原始表 | 本地真实通过（固定 CSV + 工具实现层） | SQL/工作区/HTTP/飞书/Excel 在真实生产数据上的权限、并发与长任务验收；删除操作的审计回链与幂等 API 仍待补齐 |
| AG-03 | 清洗 | clean_data | 固定 CSV 上实际执行 fill_na、winsorize、trimming；结果写入 cleaned_data，源表未覆盖；非法操作拒绝 | 本地真实通过（固定 fixture） | 缺失/异常/重复数据组合、后台大数据量任务和真实业务数据验收 |
| AG-04 | 图表 | select_chart、generate_chart | LLM/chart_selector.py 实际注册 41 个图表 ID；固定 60 行夹具逐项生成 HTML 通过；缺失字段拒绝测试通过 | 模拟通过 | 真实业务报表、空数据/大数据量、浏览器展示和导出验收 |
| AG-05 | 导出 | export_excel、export_report、generate_ppt、generate_dashboard | 固定 fixture 的 Excel、Word、PPT、Dashboard 已由真实桌面 UI 触发；Office 结构、HTTP 下载和 Dashboard 浏览器展示通过 | 本地真实通过（固定 fixture 桌面闭环） | 复杂内容、原生应用视觉、跨平台打开和品牌元数据验收 |
| AG-06 | Agent Loop | agent/、pfs_agent/runtime.py | DeepSeek `deepseek-chat` 真实执行 3 轮模型调用，先读 schema，再执行两条只读 SQL，返回核对后的地区排名与总额；记录实际 Token 与缓存命中；`agent/retry.py` 的 503 重试、401 不重试和上下文超限不重试已有单元回归；主模型 503 后备用模型成功的主循环路径已有替身回归；停止路径已有真实回归；运行级工具调用硬上限、主 Agent/委托节点费用上限、完整价格配置门禁和普通 Agent 的校验环境预算传递均有回归；Workflow 图级 Token/已知费用通过 SQLite 事务原子预留已有回归 | 本地真实通过（单任务 + 停止 + 运行级工具上限）+ 静态重试/切换/Token/费用/预算配置门禁回归 | 多轮流式、未知价格策略、真实网络失败、真实多 provider 切换、流中断重连/续传 |
| AG-07 | 外部网页和 MCP | browse_webpage、search_mcp_tools | schema 和管理入口存在 | 源码存在 | 真实连接、工具发现、错误与提示注入隔离 |
| AG-08 | Workflow 预算与重试边界 | agent/workflows/models.py、agent/workflows/scheduler.py、agent/workflows/runtime.py、agent/workflows/pricing.py、data/workflow_run_store.py | 节点 `max_total_tokens`、图级剩余 Token/费用收窄、SQLite 事务内 Token/已知费用原子预留、达到 Token 上限阻断、初始节点数约束，以及失败重试/人工重试/Verifier rework 的 `max_total_node_runs` 门禁均有回归；有图级模型预算时避免并行模型节点同时消耗同一剩余预算，节点离开执行态后释放预留；图级或节点级费用上限启用时，WorkflowRuntime 会在 Run 创建前解析有效模型的完整输入/输出单价，未知价格返回 `workflow_unknown_price`，不启动模型调用；新进程启动时会恢复未终态 Run，暂停/待审批保持不动，写入/导出/网络节点的重启失败不自动重放并返回 `workflow_restart_replay_blocked`；导出节点另有本地 SQLite 副作用登记，同一 Run/节点/迭代复用已完成 Artifact，内容变化触发幂等冲突，未完成动作仍要求人工复核 | 本地合同通过（含跨存储重开恢复、副作用重放保护与导出幂等） | 跨服务恢复、真实多服务队列、外部副作用幂等协议和生产调度验收 |

## C. 14 类统计分析

| 编号 | 分析 ID | 代码入口 | 当前证据 | 状态 | 下一步验证 |
|---|---|---|---|---|---|
| AN-01 | AB_Test_Analysis | Function/Analyze/AB_Test_Analysis/analyze.py | 本地冒烟通过；结果级专项核对两组均值、绝对/相对提升、p 值结论和质量检查 | 本地真实通过（固定夹具） | 二元指标、SRM 异常、真实实验数据和业务护栏验收 |
| AN-02 | Data_Decile_Analysis | Function/Analyze/Data_Decile_Analysis/analyze.py | 本地冒烟通过；结果级专项核对 4 桶、总和、样本量和累计占比 | 本地真实通过（固定夹具） | 重复值合桶、负值/零和、真实报表口径验收 |
| AN-03 | Decision_Tree | Function/Analyze/Decision_Tree/analyze.py | 本地冒烟通过；结果级专项核对特征重要性非负且占比守恒、混淆矩阵计数非负、ROC 坐标范围有效 | 本地真实通过（固定夹具） | 可分数据以外的类别不平衡、缺失值和真实业务数据验收 |
| AN-04 | K_Means | Function/Analyze/K-Means/analyze.py | 本地冒烟通过；结果级专项核对 2 簇、样本数守恒、占比守恒、惯性非负和轮廓系数范围 | 本地真实通过（固定夹具） | 多特征、重复点、异常值和真实业务数据验收 |
| AN-05 | Logistic_Regression | Function/Analyze/Logistic_Regression/analyze.py | 本地冒烟通过；结果级专项核对系数有限、混淆矩阵测试样本数守恒、ROC AUC 范围有效 | 本地真实通过（固定夹具） | 多分类、类别不平衡、收敛和真实业务数据验收 |
| AN-06 | Regression | Function/Analyze/Regression/analyze.py | 本地冒烟通过；结果级专项核对完全线性数据的 R²=1、RMSE=0、系数和残差行数 | 本地真实通过（固定夹具） | 多特征、类别变量、缺失值、共线性和真实业务数据验收 |
| AN-07 | Sklearn_Model | Function/Analyze/Sklearn_Model/analyze.py | 固定夹具结果级验证：连续目标的随机森林自动判定为回归并返回有限指标、特征重要性占比守恒和逐样本残差；补充空数据、缺失目标、单类别分类、非数值回归目标、聚类样本不足、多分类不均衡、缺失特征和完整混淆矩阵边界 | 本地真实通过（固定夹具） | 真实业务特征、极小样本、模型收敛、不同拆分策略和生产预测质量验收 |
| AN-08 | Torch_MLP | Function/Analyze/Torch_MLP/analyze.py | 项目虚拟环境安装 PyTorch 2.13.0 后，分类 MLP 固定夹具返回约定的结果、损失和明细表；MPS 可用 | 本地真实通过（固定夹具） | 回归任务数值质量、训练稳定性、长序列和真实业务数据验收 |
| AN-09 | Univariate_Screening | Function/Analyze/Univariate_Screening/analyze.py | 本地冒烟通过；结果级专项核对线性信号的显著性、R²、方向及常量变量处理 | 本地真实通过（固定夹具） | 缺失值、非线性关系、批量变量和真实业务数据验收 |
| AN-10 | Time_Series_ARIMA | Function/Analyze/Time_Series_ARIMA/analyze.py | 固定趋势序列结果级核对预测步数、未来时间、预测值有限；自动选阶拟合失败时有限候选阶数回退 | 本地真实通过（固定夹具，手动与自动模式） | 更多病态序列、依赖安装、真实业务时序和预测质量验收 |
| AN-11 | Time_Series_SARIMA | Function/Analyze/Time_Series_SARIMA/analyze.py | 结果级专项核对预测步数、未来时间、预测值有限和指标表存在 | 本地真实通过（固定夹具） | 季节周期选择、长序列和真实业务时序验收 |
| AN-12 | Time_Series_VAR | Function/Analyze/Time_Series_VAR/analyze.py | 结果级专项核对多变量预测步数、未来时间、预测值有限和指标表存在 | 本地真实通过（固定夹具） | 因果检验有效性、滞后阶数稳定性和真实多变量时序验收 |
| AN-13 | Time_Series_Prophet | Function/Analyze/Time_Series_Prophet/analyze.py | 结果级专项核对预测步数、未来时间、预测值有限和指标表存在 | 本地真实通过（固定夹具） | 季节性、变点、置信区间和真实业务时序验收 |
| AN-14 | Time_Series_GRU | Function/Analyze/Time_Series_GRU/analyze.py | 结果级专项核对预测步数、未来时间、预测值有限和指标表存在；当前为纯 NumPy 实现 | 本地真实通过（固定夹具） | 训练稳定性、长序列、预测质量和真实业务时序验收 |

## D. 数据源、扩展和运行管理

| 编号 | 能力组 | PFS 入口 | 当前证据 | 状态 | 下一步验证 |
|---|---|---|---|---|---|
| DS-01 | CSV / Excel | api/datasource.py、data/sources/、pfs_agent/reporting.py | CSV 已验证；四工作表 XLSX 已在真实桌面完成显式选择、所选表字段刷新、分组 SUM、哈希证据回链、空表禁用和非数字错误提示；10 城经营 XLSX 已完成真实上传与业务验收页面回读 | 本地真实通过（复杂 XLSX + 业务形态样本桌面切片） | 生产业务文件、更多复杂 Excel 与权限边界 |
| DS-02 | SQL 数据库 | connect-db、data/sources/sql.py | Flask `/connect-db`、分析表范围设置和 `/preview` 均用临时 Docker PostgreSQL 16 实际通过；选表后读取 schema、分组汇总和 CTE 查询通过；未授权系统表 `pg_catalog.pg_tables` 被拒绝；`pyodbc` 仍无厂商驱动 | 本地真实通过（临时 PostgreSQL） | SQL Server/ODBC、真实业务数据库、连接池/超时和浏览器端到端 |
| DS-03 | HTTP API | connect-api、data/sources/http.py | HTTP JSON/CSV 已用本地 fixture server 真实加载、查询、预览，并验证 Bearer 请求头和空响应/HTTP 500 失败 | 本地真实通过（HTTP 固定替身） | 外部 HTTP 权限/超时/分页和浏览器端到端 |
| DS-04 | 飞书多维表格 | api/feishu_bot.py、Agent Feishu tools | 代码和路由存在，未真实账号验证 | 源码存在 | 真实账号、权限、读取范围、错误与审计验收 |
| EX-01 | Skills / Commands | skills/、commands/、api/skills.py、api/commands.py | 文件和 API 存在；侧栏入口有历史 UI 证据 | 静态通过 | 逐命令契约、权限、错误态和桌面主流程验收 |
| EX-02 | Memory / Knowledge | api/memory.py、api/knowledge.py、Function/Knowledge/ | 入口存在；真实检索链路未验收 | 源码存在 | 多轮隔离、检索质量、注入防护和跨进程恢复 |
| EX-03 | MCP | api/mcp.py、MCP/ | MCP 管理 API 存在；商业画布及其 draw.io Agent 工具、静态编辑器和图形库已按范围决策退役 | 源码存在 | 真实 MCP 连接、工具发现、跨服务事件和提示注入隔离 |
| EX-04 | Workspace / checkpoints | api/workspace.py、filehistory/、infrastructure/artifact_lifecycle.py | 工作目录 checkpoint 可持久化快照并恢复文件与会话；只读目录拒绝恢复。Artifact 与稳定 Workspace ID 关联，切换/卸载、回收恢复和独立应用进程重开后仍可读取下载，历史只显示名称/短 ID | 本地真实通过（临时工作目录文件系统与跨进程 Artifact 回归） | 浏览器端完整工作区操作、长任务跨进程恢复、分布式存储和线上故障恢复 |
| EX-05 | Jobs / Workflows / Approvals | api/jobs.py、api/workflows.py、api/workflow_runs.py、pfs_agent/runs.py | JobsStore 重开可收口中断任务并写入事件；真实 SQLite WorkflowScheduler 已验证失败 attempt 重试成功；节点持久化 model/provider、模型调用次数、Token、工具次数和费用；图级费用门禁不把未知费用伪造为 0；报表 Run Registry 可显式使用 SQLite 跨进程传播 cancel 并回收崩溃 owner；Workflow Run 新增持久化 pause/resume API，暂停不丢弃已排队/运行节点，恢复时保留待审批状态；新进程启动会自动检查同会话未终态 Run，读节点按既有重试策略恢复，副作用节点在重启后 fail-closed；导出 Artifact 关联 Run/节点身份，并在 Run 终态刷新图级已记录用量；导出节点又有本地 SQLite 副作用登记和已完成结果复用，内容冲突与不确定重放均 fail-closed | 部分完成（Job 存储恢复 + Workflow 本地重试/暂停恢复/启动恢复 + 导出幂等 + 报表跨进程取消 + 图级费用门禁与 Artifact 成本回填本地回归） | 跨服务恢复、真实多服务队列、外部副作用幂等协议和生产账单对账 |
| EX-06 | Teams / Hooks | api/teams.py、api/hooks.py | 代码保留，当前默认关闭或无配置不执行 | 保留休眠 | 后续启用时再做并发、权限、审批、失败和审计验收 |
| EX-07 | GPU / 远程 / 桌面 | api/gpu.py、api/desktop.py、packaging/ | 入口和打包材料存在；无真实硬件/安装验收 | 源码存在 | 真实远程环境、桌面包安装、升级和卸载 |

> 范围删除（2026-09-02）：商业画布和 Google Sheets 已删除，不属于“待完成”或“兼容通过”；如未来恢复，必须作为新范围重新立项和验收。

## E. PFS 独有能力和产品独立性

| 编号 | 能力 | PFS 入口 | 当前证据 | 状态 | 下一步验证 |
|---|---|---|---|---|---|
| PFS-01 | Metric Contract | pfs_agent/contracts.py、api/pfs.py | 固定/上传 CSV/XLSX 使用统一指标、维度和筛选契约 | 模拟通过 | 版本化指标字典、复杂公式和生产口径验收 |
| PFS-02 | 轻量结果留痕 | pfs_agent/reporting.py、agent/tools/results.py、frontend/legacy/pfs-report-preview.js | 报表结果保留 Claim 文本、状态、Evidence 来源 ID、文件/工作表、纳入行数、定位信息和快照哈希；参考 Agent 的工具结果持久化继续保留；没有 active Ledger、质量门或审批 API | 本地真实通过第一段 | 更复杂来源与生产数据回读 |
| PFS-03 | Evidence 治理扩展 | — | 本轮新增的 Ledger hydration、Evidence 质量门、语义复算、人工审批/修订、SQLite Ledger 后端/迁移及对应 UI/API 已撤回，不计入当前运行能力 | 已撤回（不计当前完成项） | 如重新需要，作为独立范围重新设计和验收 |
| PFS-04 | 成本、延迟、错误、重试、恢复记录 | pfs_agent/runtime.py、pfs_agent/runs.py、聊天、Artifact 与工作流模块 | DeepSeek 主任务、固定报表、Agent/Workflow Run → Artifact 用量关联、503 重试、JobsStore 重开、Workflow retry/pause/resume、导出幂等和报表取消均保留本地回归；未知价格不会伪造 0 成本 | 部分完成 | 真实失败、多 provider 账单对账、跨服务恢复和生产观测 |
| PFS-05 | 真实业务验收 | pfs_agent/business_acceptance.py、pfs_agent/business_forecast.py、pfs_agent/distribution_drift.py、POST /api/session/&lt;sid&gt;/pfs/business-acceptance、口径预览工作台 | 已有四个匿名化场景：10 城经营组合、城市月度损益、用户—供给效率和月度需求预测；结果保留业务检查、指标、管理结论、轻量来源留痕并支持服务端重算/导出 | 本地真实通过（业务形态 XLSX/CSV + API 回归；四场景另有 Ego） | 接入真实脱敏生产数据；补联合漂移、校准/公平性、预测收益/库存护栏、复杂图表/Office 视觉和因果归因验收 |
| PFS-06 | 固定模型预测质量评估 | pfs_agent/model_evaluation.py、agent/tools/business/data.py、agent/prompts.py、tests/test_pfs_prediction_evaluation.py、tests/test_pfs_analysis_accuracy.py、tests/test_pfs_agent_vertical_slice.py、api/pfs.py | 新增分类/回归固定 holdout 评估合同；分类输出 accuracy、macro-F1、balanced accuracy、逐类别诊断和混淆矩阵，回归输出 MAE、RMSE、bias、R²；时间序列适配器从实际 `analysis_result` 提取历史配对，排除 future forecast，输出 MAE、RMSE、bias、MAPE、WAPE、sMAPE、时间范围和配对覆盖率，并写入 `analysis_evaluation`；Agent 已将 Regression、Decision_Tree、Logistic_Regression、Sklearn_Model、Torch_MLP 以及五类时间序列结果接入统一派生表（VAR 使用目标列专名，其他形状不支持时 fail closed）；显式 `analysis_options.evaluation_mode=temporal_holdout` 会在训练前缀上重拟合、校验训练截止点/时间顺序/预测长度/时间戳并用真实末尾行评分，五类时间序列均有实际回归；支持显式阈值、缺失指标阻断和逐 Case 结果，不回显原始标签/数值 | 本地契约通过（固定输入 + 多个本地分析器实际输出接线 + 五类真实时间切分回归） | 接入真实脱敏业务数据；补数据漂移、校准、公平性、生产预测质量和业务收益护栏 |
| ID-01 | 用户可见 PFS 品牌 | config/product_identity.py、模板、安装/发布材料 | PFS 名称、图标、工作台和发布源标识已有静态回归；GitHub 源码与发布 staging 已审计 | 静态通过 | 安装包和线上品牌残留扫描 |
| ID-02 | 内部标识迁移 | frontend/core/product-identity.js、frontend/core/runtime.js、frontend/core/overlay.js、infrastructure/compat.py、前端模块、agent/instructions.py、remote_runner/ | 当前运行期已统一使用 PFS 浏览器命名空间、pfs_* 存储键、__pfs* 运行标记、PFS_* 配置、PFS 工作区指令文件名和 PFS runner；Dashboard/Chat bundle 已重建，旧运行命名空间未进入发布 staging。参考快照、历史审计文字和本地忽略日志/缓存仍需在最终交付前单独清理或确认不随包发布 | GitHub 源码与本地发布 staging 静态审计通过；安装包待验证 | 安装包和线上静态资源扫描 |
| ID-03 | 授权、版权、供应链边界 | NOTICE.md、SECURITY.md、参考快照 | NOTICE、安全说明和授权参考快照存在 | 源码存在 | 最终依赖许可、源码归属和发布包审计 |
| ID-04 | 发布与线上交付 | Dockerfile、.dockerignore、install.sh、packaging/、railway.json | PFS 镜像本地构建通过；镜像内容审计不含密钥、数据库和原项目快照；容器健康检查为 healthy，首页与 CSV 确定性分析真实回读；GitHub 私有仓库 `main` 已建立 | 本地 Docker 单容器与 GitHub push 通过；部署待验证 | CI、桌面安装包、部署和线上回读 |

## 当前统计和下一切片

- 2026-09-03 当前增量：需求预测新增 `pfs_agent/distribution_drift.py`，对训练段与末尾真实 holdout 的订单和 GMV 计算列边际两样本 KS 距离及 10/50/90 分位数；可选 `max_holdout_orders_ks_distance` / `max_holdout_gmv_ks_distance` 仅接受 0–1，缺失时待确认，超限时阻断；又新增 last-value 朴素基线比较和可选 `min_model_wape_lift_pct` WAPE 改善下限；Workflow 增加启动时未终态 Run 恢复，重启导致的写入/导出/网络副作用不自动重放；导出节点再增加本地 SQLite 副作用登记，同一 Run/节点/迭代复用已完成 Artifact，内容变化冲突，未完成动作 fail-closed。口径预览、API 和 JSON/CSV 导出重算保持模型、Holdout、滚动窗口、质量阈值、漂移护栏和基线改善参数一致。该信号是小样本风险提示，基线改善也不替代联合分布、统计显著性、生产监控或业务收益验收。恢复和导出幂等切片只覆盖本地单库，不等于跨服务恢复或外部服务幂等协议。专项需求预测回归 10 项、恢复专项 8 项、导出幂等 2 项与全项目质量门 267 项均通过。
- 2026-09-03 Ego 回读补充：在隔离服务 `http://127.0.0.1:5013` 真实上传匿名化月度需求 CSV，设置滚动窗口=2、GMV KS 上限=1、模型基线改善下限=0 后点击运行；页面回读 `已完成`、10 项通过/5 项待确认/0 项阻断、GMV KS=0.9630、末尾基线改善 58.1524%、滚动基线改善 61.4613%，并回读 JSON“已下载”。此后任务空间被用户接管，未自动夺回控制权；该证据只覆盖本地匿名数据和本地服务。
- 2026-09-03 Ego 回读补充：`http://127.0.0.1:5012` 隔离服务中真实上传匿名化月度需求 CSV，设置滚动窗口=2、GMV KS 上限=1 后点击“运行经营验收”，页面回读 `已完成`、9 项通过/5 项待确认/0 项阻断、滚动 2 个窗口、GMV KS=0.9630；JSON/CSV 下载均回读“已下载”。修复需求预测验收区横向 flex 溢出后，1280px 桌面按钮可直接命中；600px 视口无横向溢出，滚动到按钮后仍可命中。该证据仍只覆盖匿名本地数据和本地服务。

- 2026-09-03 增量：标准 CSV/XLSX Claim 已增加同源快照 `RecomputeContract` 和 `deterministic_recompute/v2` 数值复算；`pfs_agent/evaluation.py` 与固定回归集已能真实产出 Claim 覆盖率、引用准确度、冲突识别率和计算一致性，并逐 Case 暴露缺失/错引/漏冲突/复算退化。新增 `pfs_agent/model_evaluation.py`，以固定 holdout 对分类/回归预测输出指标和显式阈值判定，并将五类时间序列实际 `analysis_result` 接到 Agent 的 `analysis_evaluation` 派生表；VAR 的目标列专名会按请求目标安全适配，未来 forecast 行不进入误差分母，历史未配对行进入覆盖率告警；用户明确要求时间切分时，`analysis_options.evaluation_mode=temporal_holdout` 会在训练前缀重拟合并对齐真实 holdout 行，五类分析器均有实际回归。旧 Ledger Claim 无该契约时保留 `deterministic_claim_evidence/v1` 兼容路径。Agent 查询/派生表实现层同时补齐了后台路径二次 SQL 校验、raw/derived 表登记、原始表覆盖保护和本地派生表删除回读。v2、Claim 固定评估器和模型固定评估器仍不等于自由文本事实核查、生产语义评测、数据库事务或分布式审计。
- 2026-09-03 增量：新增 `pfs_agent/business_forecast.py` 月度需求预测业务验收场景，服务端从上传快照读取月份、订单量和 GMV，校验唯一/连续月度、源表升序、数值范围和训练窗口；对末尾真实行在训练前缀重拟合 ARIMA、SARIMA、VAR、Prophet、GRU，复用 temporal holdout 评估并在显式月度粒度下做自然月对齐。结果包含 WAPE/MAE、训练截止点、实际/预测订单总量差异、训练→holdout 订单均值漂移、Evidence/Claim 和模型误差阈值；未配置阈值时为 needs_review，重复/月缺口/乱序/预测错月会阻断。随后增加可选 `business_thresholds.max_total_delta_pct` 与 `max_holdout_orders_mean_shift_pct`，分别对 holdout 预测总量绝对偏差和目标均值漂移做独立计划准入护栏，未配置时仍待确认、超限时返回 `needs_revision`；口径预览补充模型、Holdout 月数、总量偏差上限和均值漂移上限控件。该场景以匿名本地 fixture 和上传 HTTP/API 回归验证，不代表生产预测质量、完整分布漂移、收益或库存护栏。

- 全项目剩余顺序以 [`HANDOFF.md`](HANDOFF.md) 第 5 节为准；本矩阵只负责逐项记录能力证据，不维护第二份并行排期。
- 已确认：14 类分析、41 个图表 ID、PFS UI/品牌、确定性 CSV/XLSX 分析、四个业务验收场景、模型预测质量评估、Agent/Workflow 预算与恢复、Artifact 成本/幂等、报表取消、SSE 断开收尾和轻量结果留痕；Evidence Ledger 治理层已按 2026-09-04 决策撤回。2026-09-04 全量质量门 267 项通过。
- 当前没有任何一项可以据此宣布“原项目全部功能已重新验证”。
- 本轮错误契约包含 `source_columns_missing`、`source_date_invalid`、`date_filter_invalid`、`date_range_invalid`、`upload_file_too_large`、`model_not_configured`、`pfs_analysis_canceled`、`evidence_snapshot_missing`、`evidence_snapshot_mismatch` 和交付失败建议；日期异常、超限上传、取消、缺字段、模型未配置、快照不一致和交付失败均已有对应本地桌面或隔离回归。
- 当前最小可执行垂直切片：新建会话 → 上传 CSV/XLSX → 预览 → 业务问题 → 确定性分析 → 图表/结论 → 依据 → 导出。
- 本轮已把 XLSX 纳入同一条确定性分析闭环，并完成 41 个注册图表的固定夹具逐项生成冒烟；该结果仍不代表真实业务报表、统计准确性、浏览器展示或导出链路已验收。

### 2026-08-30 PFS 报表下载闭环

- 固定示例入口 `/api/pfs/export` 和会话上传入口 `/api/session/<sid>/pfs/export` 只接受 JSON/CSV 下载格式；服务器会重新读取数据并按同一份 Metric Contract 计算，避免直接信任浏览器结果。
- JSON 保留完整的报告、Claim、Evidence、快照哈希和请求口径；CSV 使用统一的 `section/field/value/detail` 结构，保留汇总、分组、Claim、Evidence 和 warning 信息。
- HTTP 回归覆盖固定数据总额 100000、上传数据总额 4400、地区分组、证据字段、安全文件名、来源哈希、受限自然语言口径和非法格式拒绝；真实工作台固定报表预览点击下载后显示成功状态。
- 补充：Excel/Word/PPT/Dashboard 已接入报表弹窗的统一交付区，并完成固定 fixture 的桌面浏览器触发、Office 结构回读、Dashboard 打开和四类 HTTP 下载检查。
- 边界：artifact 元数据已持久化到本地生命周期 registry，并接入会话历史详情；跨进程工作区关联、复杂内容、Office 原生视觉与跨平台打开、品牌元数据和部署仍待验证；移动端不在当前范围。

### 2026-08-30 上传到 PFS 报表预览 HTTP 回归

- 环境：项目 `.venv`（Python 3.13）；Flask 测试客户端；临时 UTF-8 CSV；不含真实模型、外部数据源或 Docker。
- 命令：`.venv/bin/python -m unittest tests.test_pfs_http_vertical_slice -v`。
- 结果：2/2 通过；上传、会话数据源列表、确定性分组 SUM、快照哈希、Claim/Evidence 和错误列拒绝均通过。
- 边界：这是本地 HTTP 接口级验收，不是浏览器视觉验收，也不证明聊天 Agent、SSE、导出、外部连接器或线上部署已完成。

### 2026-08-30 浏览器 CSV 上传闭环验收

- 环境：本地 PFS_PORT=5012 Flask 服务；Ego Browser 独立任务空间；桌面视口 1280×900；不含真实模型、外部数据源或 Docker。
- 输入：data/fixtures/pfs_sales.csv。
- 操作：打开数据源 → 添加数据源 → 上传 Excel / CSV → 选择文件 → 点击上传。
- 结果：文件输入回读 files=1、文件名为 pfs_sales.csv、上传按钮由禁用变为可用；提交后上传弹层关闭，数据链接列表出现上传数据_pfs_sales.csv_20260830_044644。
- 状态：本地真实通过（浏览器 CSV 上传到会话数据源）。
- 结果：随后在“口径预览”中选择 pfs_sales.csv · 9 rows，列选择器加载 month / region / product / sales_amount；运行后状态为“已完成”，来源为 pfs_sales.csv · v1，返回销售额合计 100,000、9 行、2026-01 → 2026-03、华东/华南/华北分组、2 条 supported Claim 和 1 条 csv_snapshot Evidence。
- 边界：本次证明 CSV 上传 → 报表数据源选择 → 确定性分析 → Claim/Evidence 展示这一条浏览器闭环；尚未证明浏览器 XLSX、自然语言触发、导出、真实模型或部署链路。

### 2026-08-29 PFS Agent 本地垂直切片回归

- 环境：项目 .venv（Python 3.13）；固定 data/fixtures/pfs_sales.csv；不含真实模型、外部数据源或 Docker。
- 命令：.venv/bin/python -m unittest tests.test_pfs_agent_vertical_slice -v，并纳入完整测试回归。
- 覆盖：Agent 直连 CSV → schema → 只读聚合查询 → 派生分析表 → 图表选择 → Plotly HTML → 数据概况；同时验证缺失字段、未知表和写入 SQL 会失败且不改变源表。
- 结果：垂直切片 2/2 通过；完整 Python 回归 69 项通过、1 项可选 Torch 测试跳过。
- 窄范围修复：在数据工具实现边界补加统一 SQL 只读校验，避免绕过模型分发入口的内部调用执行 DELETE 等写操作。
- 边界：证明固定 CSV 和本地工具契约可串联，不证明真实模型调度、外部数据源、SSE、长任务恢复、导出质量、浏览器视觉或线上功能。

## 证据记录格式

后续每项更新必须写明：日期和环境、输入样例或外部依赖、执行命令或请求、关键输出、产物路径或截图、是否包含真实模型和真实数据源、失败时的阻塞原因。

### 2026-08-29 图表注册表专项回归

- 环境：项目 `.venv`（Python 3.13）；固定 60 行 pandas fixture；不含真实模型、真实数据源或浏览器上传。
- 命令：`.venv/bin/python -m unittest tests.test_chart_generation_smoke -v`。
- 结果：41 个注册图表逐项生成通过；缺失字段拒绝测试通过；专项共 2 个测试通过。
- 本轮窄范围修复：Horizon/Box-and-Whisker 宽格式转换避免与输入 `value` 列冲突；Pyramid/Beeswarm 接受注册表的 `label/left_value/right_value` 与 `x/y` 映射；测试夹具补充数值 x/y、正负值、真实中国地区名和混合节点字段。
- 边界：属于固定夹具的输出结构级冒烟；不证明图表视觉正确性、统计准确性、复杂数据规模、外部地理库覆盖或生产导出质量。

### 2026-08-30 PFS 请求身份与重试策略回归

- 修改范围：`infrastructure/compat.py`、`api/chat.py`、`api/knowledge.py`、`api/memory.py`、`api/workflow_runs.py`、`tests/test_pfs_identity_migration.py`、`tests/test_pfs_retry_policy.py`。
- 结果：`X-PFS-User-ID` 优先于旧 `X-BAA-User-ID` 和请求体身份；旧头仅作兼容回退。503 重试按 3 秒、6 秒退避，401 不重试，上下文超限不重试。
- 命令：`.venv/bin/python -m unittest tests.test_pfs_retry_policy tests.test_pfs_identity_migration -v`；`.venv/bin/ruff check agent/retry.py api/workflow_runs.py infrastructure/compat.py tests/test_pfs_retry_policy.py tests/test_pfs_identity_migration.py`。
- 结果：身份与重试专项 34/34 通过，Ruff 通过。
- 边界：属于本地代码契约回归；没有模拟成真实网络故障已恢复，provider 失败切换、流式中断重连、长任务重启恢复和线上多用户隔离仍待验证。

### 2026-08-30 DeepSeek 主链路回归复验

- 环境：本地 Waitress `127.0.0.1:5193`、真实 `deepseek-chat`、固定 `data/fixtures/pfs_sales.csv`，请求关闭记忆。
- 结果：真实 SSE 任务 3 次模型调用，执行 `get_schema` 和两次 `query_data`；返回华东 42,000、华南 33,000、华北 25,000，总额 100,000；输入 12,671 Tokens、输出 299 Tokens，约 4.59 秒。
- 结论：备用切换改动没有破坏 DeepSeek 主链路；仍不等于真实第二 provider、网络故障恢复或长任务恢复已验收。

### 2026-08-30 Job 存储重启恢复回归

- 实现边界：没有把“工作流源码存在”当作恢复完成；针对现有 `data/jobs_store.py` 的启动恢复逻辑新增 `tests/test_pfs_reliability.py`。
- 场景：在临时 SQLite 中创建并推进一个 running 任务、一个 canceling 任务，关闭存储连接后重新打开，模拟应用重启。
- 回读：running 任务收口为 `failed` 并写入 `Application restarted before the job completed.`；canceling 任务收口为 `canceled`；两者均有 `finished_at`，并在会话事件流中分别留下 `job_error` / `job_canceled` 恢复事件。
- 验证：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest tests.test_pfs_reliability tests.test_pfs_retry_policy -v`，7/7 通过；完整回归 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py' -q`，122 项通过、1 项可选测试跳过；Ruff 和 `pnpm run build:check` 通过。
- 边界：这证明了本地 Job 状态存储在重启时的收口和事件留痕，不证明 WorkflowScheduler 跨进程恢复、长任务自动续跑、Temporal/Redis/PostgreSQL、多服务故障恢复或线上恢复已完成。

### 2026-08-30 Torch MLP 依赖与分析回归

- 环境：项目 `.venv`（Python 3.13.14，arm64 macOS），通过 `uv pip install --python .venv/bin/python -r requirements-dl.txt` 安装 PyTorch 2.13.0；检测到 MPS 可用。
- 验证：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest tests.test_analysis_modules -v`，2/2 通过；14 类分析统一冒烟不再跳过 Torch MLP。
- 边界：这是固定夹具下的接口和输出形状验证，不代表 MLP 的业务预测质量、训练稳定性、GPU/CPU 跨平台一致性或生产规模性能已经验收。

### 2026-08-30 PostgreSQL 数据源与 SQL 范围回归

- 环境：临时 Docker `postgres:16-alpine`，项目 `.venv`（SQLAlchemy、psycopg2、DuckDB）；创建 `sales_report` 和未授权的 `private_notes` 表后，通过 `SQLDataSource` 连接。
- 验证：从 Flask `/api/session/<sid>/connect-db` 连接，调用分析表范围设置和 `/preview`，再执行底层查询；显式选择 `sales_report` 后读取 schema、执行地区销售额汇总和 CTE，真实连接返回华东 42,000、华南 33,000、华北 25,000。
- 安全修复：SQL 表引用识别现在保留未知/系统表引用；`pg_catalog.pg_tables`、未选择表和无法解析表引用会在远端执行前被拒绝，避免越过选表范围。
- 回归：`tests.test_sql_source_scope` 2/2 通过；Flask API 连接/选表/预览回读通过；完整 `unittest discover` 为 124 项通过、无跳过；相关 Ruff 检查通过。临时容器已删除。
- 边界：这证明了临时 PostgreSQL 的连接和选表范围，不代表 SQL Server 厂商驱动、生产数据库权限/网络、连接池压力、浏览器端到端或线上部署已完成。
