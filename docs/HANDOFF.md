# PFS 现役交接基线

> 更新时间：2026-09-03 +08:00
> 作用：下一次会话或新接手者的唯一现役状态入口。历史证据见根目录 `PROJECT_STATUS.md`，逐项证据见 `docs/FUNCTION_COMPATIBILITY_MATRIX.md`。

## 1. 产品目标

将授权参考项目改造为完全独立的 PFS 数据分析 Agent：保留并重新验证原有功能，重做品牌、UI、内部运行标识、文档和交付体系，同时增加指标口径、证据链、成本、追踪和可靠性。

当前范围只要求桌面端。普通用户使用本地 Python 安装/启动脚本，开发与部署人员可使用 Docker。

2026-09-02 范围决策：商业画布与 Google Sheets 已从产品范围完整退役，入口、前端交互、API、Agent 工具、存储、技能和依赖均不再作为兼容目标。Teams、Hooks、GPU/远程执行、飞书机器人和云端登录代码保留，但当前默认休眠；Hooks/飞书后台启动和云端登录需要显式环境开关，GPU 总开关默认关闭，Teams 默认关闭。

## 2. 六个事实面

| 事实面 | 状态 | 当前结论 |
|---|---|---|
| 代码 | `changed-and-verified` | 当前工作区完成报表取消、桌面错误态、Artifact 工作区关联、Run/Workflow → Artifact 用量关联和会话统一审计切片；审计接口按会话隔离并隐藏绝对路径，费用未知保持 unknown，审计弹层支持从内部输入控件按 Escape 关闭。该切片尚未 commit 或 push。 |
| 运行态 | `verified-current` / `pending` | 2026-09-03 隔离 Waitress 回读 `/api/health` 与工作台为 200，draw.io、商业画布和 Google Sheets 路由为 404；桌面报表与固定交付物已有历史回读。无部署和线上 PFS 可用性证据。 |
| 文档 | `changed-and-verified` | 本文作为现役入口；README、PRODUCT、完整改造计划和能力矩阵按当前边界对齐。 |
| 规则 | `changed-and-verified` | 项目根目录新增精简 `AGENTS.md`，保存命令、权威入口、状态分层和安全边界。 |
| 记忆 | `changed-and-verified` | 项目 `memory/` 含本地应用运行记忆，已被 Git 忽略且不作为项目事实来源；Codex 生成记忆通过宿主允许的 correction note 更新，不直改生成索引。 |
| 工作区 | `changed-and-verified` | 挂载工作区的 `artifacts/` 已纳入生命周期登记边界；切换、卸载和独立应用进程重开后仍按稳定 Workspace ID 读取同一 Artifact，且幂等登记不重复产生记录。该结论仅是本地已知工作区，不是分布式存储。 |

## 3. 已经形成的主链路

- PFS 独立产品名、图标、运行命名空间、安装/容器入口和真实 Flask 工作台已建立。
- CSV/XLSX 上传、字段选择、受限问句、确定性分组计算、Metric Contract、Claim/Evidence 和快照哈希已形成本地桌面闭环。
- JSON/CSV 服务端重算下载已验证。Excel、Word、4 页 PPT 和 Dashboard 已接入统一交付区；Office 结构、HTTP 下载和 Dashboard 桌面打开已回读。包含说明页、空表、销售明细和异常指标的复杂 XLSX 已在真实桌面页面通过工作表选择、字段刷新、确定性分析和错误状态验收；四类交付物均从所选 `销售明细` 生成。
- 本轮验收地址为 `http://127.0.0.1:5012`；它是临时本地服务，不是部署地址。验收结束后已关闭浏览器隔离空间，并停止本轮启动的服务进程。
- DeepSeek 单任务已真实调用 schema 和只读 SQL；停止路径、Job 重启收口、Workflow 节点重试和 Token/费用/工具次数硬上限已有本地证据。
- 14 类分析和 41 个注册图表已有固定夹具输出结构证据，但不等于复杂业务结果和视觉验收。
- Docker 单容器、临时 PostgreSQL、HTTP 固定替身和本地文件 checkpoint 已有分层验证。
- 本轮已将会话 Artifact 查询接入任务历史弹窗；交付物在“本会话交付物”区域独立展示稳定 ID、运行 ID、工作表、覆盖行数、快照摘要、Claim/Evidence 数量和下载次数，并可展开查看已登记的 Claim 文本、状态、人工决定和 Evidence 片段。查询改为 `/api/session/<sid>/lifecycle/artifacts`，并新增单个 Artifact 详情入口；列表和详情都执行会话所有权与 active 状态过滤，跨会话或归档对象按不存在处理。
- 本轮又扩展交付 Artifact lineage：交付响应和生命周期登记会保留分析参数、生成 SQL、图表规格、最终结论、warnings 和结构化成本；固定报表明确记录为确定性计算、模型调用 0 次、0 Token、0 USD 且非估算，不伪造 provider 账单。报表预览增加 Evidence 来源详情、Claim 的 supports/refutes 分组，以及冲突待裁决提示。冲突项现在提供二次确认后的支持/反驳/保留待确认操作，并调用 Ledger 决策接口；任务历史点击详情时按会话读取完整 lineage、成本来源和匹配的人工裁决审计记录。真实 Agent 的 usage 现在按稳定 Run ID 聚合并回填到同一 Run 生成的 Artifact，保留 provider/model、调用次数、输入/输出/缓存 Token 和费用来源；配置单价仅是估算，`billing_verified=false`，没有供应商账单对账。真实上传 CSV/XLSX 的直接分析、受限问句和确定性聊天入口现在统一经过治理函数，动态登记 Evidence/Claim，并可按 session_id + run_id 从本地 Ledger 回读。
- 2026-09-01 隔离本地真实桌面验收：服务地址 `http://127.0.0.1:5217`，数据目录 `/private/tmp/pfs-audit-browser.soChCr`，Session `1718cfc6-047a-4b2f-9fae-d353afc71fac`，Job `d251964a-98e`，Artifact `artifact-audit-browser-d251964a-98e`。夹具包含 2 个 Claim、2 条 Evidence（1 supports/1 refutes 冲突）、1 条无证据 Claim 和 1 条 `needs_review` 人工裁决（业务口径不一致）。最终 API/页面统计为 1 个成功任务、1 个交付物、2 个 Claim、1 个无证据结论、1 个冲突、1 次模型调用、150 Token（120 input + 30 output）、费用未知；页面视口 1280×720。满数据、工具类型筛选、关键词无匹配空态、重置筛选、Failed to fetch 错误态、弹窗内部滚动和显式关闭按钮均通过；审计输出未暴露绝对路径、完整 SQL、完整工具参数。
- P0 收口复测在隔离 `PFS_DATA_DIR` 和 `http://127.0.0.1:5012` 完成：任务历史审计弹层打开后，关键词输入框保持焦点并有输入值时按 Escape，弹层由可见变为关闭。Workflow 节点新增真实 `model_calls` 持久化；导出 Artifact 记录 Workflow Run/节点身份，并在导出时和 Run 终态按全部已记录节点用量刷新成本。任一已测量节点单价未知时总费用保持 `null`，不伪造 0；这仍不是供应商账单对账。
- 本轮补充结构化错误边界：缺字段、日期异常、大文件上传、模型未配置和交付生成失败均有明确 code 或处理建议；聊天 SSE 的模型构建错误会透传 `model_not_configured`。真实桌面已验证异常日期 CSV 会保留在数据源选择器并标记“需修复”，运行时显示中文日期修复建议；101 MiB CSV 会在上传弹窗显示 100 MB 上限且服务端不保留超限副本。当前工作区又为 PFS 同步报表分析增加唯一 Run ID、会话所有权校验、取消按钮、`AbortController` 和服务端取消检查；并发回归确认取消后不登记 Claim/Evidence。真实桌面使用 54.7 MB、250 万行 CSV 完成取消，Ledger 按该 Run ID 回读为 0 Claim / 0 Evidence；验收中修复了运行登记前点击取消的短竞态，以及 `hidden` 属性被按钮 CSS 覆盖的问题。该能力只覆盖单 Python 进程内的协作式取消，不能立即中断 pandas 正在执行的单个读表步骤，也不等于跨进程恢复。
- 缺字段、模型未配置和交付失败又在空白 `PFS_DATA_DIR` 的隔离本地运行中完成桌面回读：缺少销售指标列时保留 `source_columns_missing` 并显示字段建议；空白模型配置经 SSE 显示 DeepSeek 配置引导；只读输出目录返回稳定 `delivery_generation_failed`，不暴露本机路径，交付状态整行换行且无横向溢出。至此本阶段约定的桌面错误态已全部回读。
- Artifact 工作区关联已形成本地闭环：挂载目录生成的文件只能在该工作区 `artifacts/` 边界内登记，生命周期 registry 记录稳定 Workspace ID；会话切换/卸载后仍能按已知工作区身份读取和下载，独立应用进程重开同一数据目录后关联保持不变。回收恢复会返回原工作区目录，同一 Artifact ID 只允许幂等复用同一文件，不能覆盖其他产物。API 与任务历史仅返回工作区名称、短 ID 和可用状态，不返回绝对路径。

## 4. 发布状态

| 阶段 | 状态 | 证据 |
|---|---|---|
| implemented | 已完成本轮切片 | Artifact、治理、错误状态、测试和文档已整合。 |
| locally verified | 本轮通过 | 2026-09-03 实跑 196 项 Python 测试，全部通过、无失败、无跳过；Ruff、ESLint、Chat production build、Dashboard/Chat build check 和 `git diff --check` 通过。退役范围回归锁定商业画布/Google Sheets 路由、工具、实现和可执行静态资产缺失，并防止旧 Google 凭据回传。临时发布 staging 重建为 489 个文件、37,703,382 bytes，artifact audit 为 0 findings、0 symlink；无退役路径、数据库或本地凭据命中。构建仅有既有 500 kB chunk 提示，不是失败。 |
| committed | 当前切片未执行 | 本地工作区仍有未提交改动；本轮没有创建 commit。 |
| pushed / PR | 当前切片未执行 / 无 PR | GitHub 私有仓库 `main` 已建立，但本轮没有 push。 |
| deployed | 未执行 | 无 PFS 部署 marker。 |
| live verified | 未验收 | 无 PFS 线上用户路径证据。 |
| knowledge closed | 本轮完成 | 文档、规则和获准记忆修正入口已对齐。 |
| cleaned | 已完成授权范围 | 用户在完整汇报后确认清理；两份临时 staging、一份隔离运行目录和未跟踪的 `direction-approved.md` 已删除。被忽略的本地凭据、数据库、上传、输出和参考快照未获清理授权，继续保留且不进入发布包。 |

## 5. 剩余改造顺序

### 1）桌面主链的错误态和复杂 Excel 验收（本地完成）

多 Sheet 显式选择、字段切换、空表禁用、非数字指标修复建议和四类交付已经通过真实桌面页面验收。报表同步分析的单进程协作式取消也已用 250 万行 CSV 完成真实桌面点击、状态、按钮、下载/交付禁用和 Ledger 空回读。缺字段、日期异常、101 MiB 超限上传、模型未配置和交付失败均已完成真实桌面回读。该结论仅是本地桌面切片；跨进程取消归入第 5 项可靠性工作，当前下一步进入第 2 项。

### 2）扩展 Artifact / Lineage 的可查询范围

Excel、Word、PPT 和 Dashboard 已登记为本地生命周期 artifact，并记录稳定 Artifact ID、Run ID、数据快照哈希、工作表、纳入行数、Metric Contract、分析参数、SQL、图表规格、最终结论、warnings、Claim/Evidence、结构化成本和下载历史；报表交付区和任务历史弹窗已展示并可展开查看追溯信息。当前已补会话安全的历史列表、单个详情、工作区名称/短 ID 和工作区可用状态；回归证明工作区 Artifact 在切换、卸载、回收恢复和独立应用进程重开后仍保持同一 Workspace ID 与 Artifact ID，幂等重登记不会生成重复记录，绝对路径不会进入 API。真实 Agent usage 按 `session_id + run_id` 聚合回填；Workflow 导出也记录 Run/节点身份，并在 Run 终态按持久化节点用量刷新调用次数、Token、provider/model 和费用来源。配置价格只形成估算，`billing_verified=false`，未知价格不伪造 0。真实上传 CSV/XLSX 的直接分析、受限问句和确定性聊天结果也已在同一 Run 动态登记 Evidence/Claim。下一步进入分布式恢复、供应商账单对账和更广能力复验。该结论是本地 JSON registry 加已知本地工作区的跨进程重开，不是分布式故障恢复或供应商账单对账。

### 3）完成证据治理与人工裁决界面

Evidence 详情、Claim 支持/反驳关系和冲突待裁决提示已加入报表预览；人工裁决按钮和二次确认已接入 Ledger 决策接口。真实上传 CSV/XLSX 的直接分析、受限问句和确定性聊天结果已完成动态 Evidence/Claim 登记、按 `session_id + run_id` Ledger 回读和 Artifact 关联。统一审计中心已在任务历史中聚合 Claim 来源、Evidence 片段、无证据结论、冲突和人工决定；真实桌面视觉/交互切片与内部输入焦点 Escape 关闭均已完成。下一步做语义核验评估和更完整的审批流程；语义核验结果不能覆盖确定性计算或跳过审批。

### 4）按功能组完成原能力兼容复验

顺序为：生产 SQL/复杂 Excel → HTTP/飞书 → 14 类分析的业务边界 → 41 个图表的桌面视觉 → Skills/Commands/Knowledge/Memory → MCP/Teams/Hooks/远程能力。商业画布和 Google Sheets 已按范围决策退役，不再进入兼容验收。其余每项必须在兼容矩阵中记录正确、边界、失败和权限样例。

### 5）完成长任务、工作流和成本可靠性

复验多轮 SSE、流中断、cancel、checkpoint、跨进程 pause/resume、节点重试、图级费用上限、幂等副作用、真实多 provider 切换和账单对账。

### 6）完成 Office/桌面包与发布验收

用复杂报告打开 Excel/Word/PPT，检查图表、字体、元数据和跨平台显示；再在干净 Windows/macOS 环境生成、安装、启动、升级和卸载包。

### 7）部署与线上闭环

完成密钥管理、生产数据库/必要外部服务、健康检查、固定任务集、真实 DeepSeek 任务、成本/延迟/错误观测和 canonical URL 回读。最终分开标记 commit、push、CI、release、deploy 和 live verified。

## 6. 当前不能宣称的能力

- 原项目全部功能已复刻并重新验证。
- 工作流可在多进程/多服务故障后无损恢复。
- 所有外部数据源、MCP、飞书和多模型供应商都可生产使用。
- Office 产物、Windows/macOS 安装包和更新链路已完成跨平台验收。
- 不能因为 GitHub push 成功，就说已经部署或线上用户已看到新版本。

## 7. 清场候选（未授权删除）

- `.DS_Store`、`__pycache__/`、`.pytest_cache/`、`.ruff_cache/`：可重建缓存。
- `_pfs-export-test/`：本地导出验收产物。
- `outputs/`、`uploads/`、`auth.db`、`secret_key`、`memory/`：运行态/用户数据，只有在确认不需要恢复本地会话、下载或登录状态后才能清理。
- `Data-Analysis-Agent-main/`：本地授权参考快照，只有在功能兼容复验不再需要源码比较后才能删除。

本轮按用户明确范围删除了商业画布和 Google Sheets 的产品代码、技能、测试、依赖、3,332 个 draw.io 静态文件和 30 个图形库文件；这些已跟踪内容可由 Git 恢复。运行态旧 Google 配置不会自动删除，但会被忽略、禁止再次保存且不会通过配置 API 回传。开发机仍有 8 个被 Git 忽略的 draw.io secret/配置文件和 `business_canvas/business_canvas.sqlite`；为避免误删凭据或用户数据，本轮保留现场，发布策略会整体排除 `static/drawio/`，本轮 staging 回读无相关路径或凭据。其余清场候选未动。
