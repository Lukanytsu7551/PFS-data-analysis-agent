# PFS 现役交接基线

> 更新时间：2026-09-04 +08:00
> 作用：下一次会话或新接手者的唯一现役状态入口。历史证据见根目录 `PROJECT_STATUS.md`，逐项证据见 `docs/FUNCTION_COMPATIBILITY_MATRIX.md`。

## 1. 产品目标

将授权参考项目改造为完全独立的 PFS 数据分析 Agent：保留并重新验证原有功能，重做品牌、UI、内部运行标识、文档和交付体系，同时增加指标口径、轻量来源/结论留痕、成本、追踪和可靠性。

当前范围只要求桌面端。普通用户使用本地 Python 安装/启动脚本，开发与部署人员可使用 Docker。

2026-09-02 范围决策：商业画布与 Google Sheets 已从产品范围完整退役，入口、前端交互、API、Agent 工具、存储、技能和依赖均不再作为兼容目标。Teams、Hooks、GPU/远程执行、飞书机器人和云端登录代码保留，但当前默认休眠；Hooks/飞书后台启动和云端登录需要显式环境开关，GPU 总开关默认关闭，Teams 默认关闭。

2026-09-04 范围回滚：只撤销本轮新增的 Evidence 治理层，包括 active Ledger hydration、Evidence 质量门、语义复算、人工审批/修订、SQLite Ledger 后端/迁移和对应治理 UI/API。保留参考 Agent 原有的轻量工具结果留痕，以及 PFS 报表结果中的 `claims`、`evidence`、来源快照/定位信息、UI、品牌、数据分析、交付物和普通任务历史。下方带有 P1 Evidence 治理内容的条目是历史记录，不能当作当前运行能力。

## 2. 六个事实面

| 事实面 | 状态 | 当前结论 |
|---|---|---|
| 代码 | `changed-and-verified` | P0 本地切片已提交；当前未提交工作树保留 PFS UI/品牌、CSV/XLSX 确定性分析、四个业务验收场景、模型预测质量评估、Agent/Workflow 预算与恢复、Artifact 成本/幂等、报表 Run 取消和 SSE 断开收尾；Evidence 治理层已按 2026-09-04 决策从 active runtime 撤回。生产数据、生产语义和预测收益验收仍未完成。push 尚未完成。 |
| 运行态 | `verified-current` / `pending` | 2026-09-03 隔离本地服务回读 `/api/health` 与工作台为 200；Ego 浏览器已分别真实上传 10 城 XLSX、城市月度损益 CSV、用户—供给效率 XLSX 和月度需求 CSV，完成业务分析、轻量 Claim/Evidence 结果与下载页面回读。无部署和线上 PFS 可用性证据。 |
| 文档 | `changed-and-verified` | 本文作为现役入口；README、PRODUCT、完整改造计划和能力矩阵按当前边界对齐。 |
| 规则 | `changed-and-verified` | 项目根目录新增精简 `AGENTS.md`，保存命令、权威入口、状态分层和安全边界。 |
| 记忆 | `changed-and-verified` | 项目 `memory/` 含本地应用运行记忆，已被 Git 忽略且不作为项目事实来源；Codex 生成记忆通过宿主允许的 correction note 更新，不直改生成索引。 |
| 工作区 | `changed-and-verified` | 挂载工作区的 `artifacts/` 已纳入生命周期登记边界；切换、卸载和独立应用进程重开后仍按稳定 Workspace ID 读取同一 Artifact，且幂等登记不重复产生记录。该结论仅是本地已知工作区，不是分布式存储。 |

## 3. 已经形成的主链路

- PFS 独立产品名、图标、运行命名空间、安装/容器入口和真实 Flask 工作台已建立。
- CSV/XLSX 上传、字段选择、受限问句、确定性分组计算、Metric Contract、Claim/Evidence 和快照哈希已形成本地桌面闭环。
- JSON/CSV 服务端重算下载已验证。Excel、Word、4 页 PPT 和 Dashboard 已接入统一交付区；Office 结构、HTTP 下载和 Dashboard 桌面打开已回读。包含说明页、空表、销售明细和异常指标的复杂 XLSX 已在真实桌面页面通过工作表选择、字段刷新、确定性分析和错误状态验收；四类交付物均从所选 `销售明细` 生成。
- 既有 5012 验收地址只是临时本地服务，不是部署地址；本轮 5013 服务也已停止。Ego 浏览器任务空间当前由用户控制，后续不自动接管。
- DeepSeek 单任务已真实调用 schema 和只读 SQL；停止路径、Job 重启收口、Workflow 节点重试和 Token/费用/工具次数硬上限已有本地证据。
- 14 类分析和 41 个注册图表已有固定夹具输出结构证据，但不等于复杂业务结果和视觉验收。P2 第一切片额外完成 10 城经营组合的真实业务形态验收：城市主键、必填字段、数值范围、整行重复和四类订单结构勾稽通过后，才计算订单量加权指标、年化 GMV、规模/增长/单位贡献排名；贡献余量被明确限制为非利润指标。
- P2 第二切片新增匿名化城市月度损益样本：自动识别城市月粒度，要求连续且逐城一致的同比月份，逐行复算“佣金收入 + 配送收入 - 商家结算 - 履约 - 补贴营销 - 支付成本 - 总部摊销”，不勾稽或缺月时停止输出。有效样本对比 2026 年 1–3 月与 2025 年同期，收入同比 +15.42%，经营利润增加 935.725 万元，经营利润率 8.71%；桂林当前亏损只进入核查清单，不做原因归因。结果保留轻量来源/结论留痕，并支持服务器重算 JSON/CSV 下载。
- P2 第三切片新增匿名化用户—供给效率场景：校验城市主键、关键字段、数值与比例范围、重复行、正向分母和字段完整性；有效样本合计 718.0 万月活用户、150.2 万高价值用户、加权高价值占比 20.92%、整体日均下单频次 0.106、商家日均订单量 60.95，并输出高价值规模前三、效率前三和 5 个“双低”观察城市。结论明确为横截面筛查，不推断留存、转化或因果关系；结果支持轻量 Evidence/Claim 展示和服务端重算。
- P2 第四切片新增匿名化月度需求预测场景：校验月份唯一、YYYY-MM、自然月连续、源表升序、订单/GMV 数值范围和训练窗口；对末尾真实月份做严格 temporal holdout，并补充可配置滚动时间起点，在训练前缀重拟合 ARIMA、SARIMA、VAR、Prophet 或 GRU，再计算 MAE、RMSE、bias、WAPE、sMAPE、窗口误差区间、预测总量差异、训练→holdout 订单均值漂移、订单/GMV 列边际两样本 KS 分布距离和 last-value 朴素基线 WAPE 改善。月度模型的近似步长只在显式月度粒度声明下按自然月对齐，跨月、乱序、错预测月份或缺预测会阻断；没有模型误差阈值、总量偏差、均值漂移、KS 分布或基线改善护栏时只标记“待确认”，显式 `max_total_delta_pct` / `max_holdout_orders_mean_shift_pct` / `max_holdout_orders_ks_distance` / `max_holdout_gmv_ks_distance` / `min_model_wape_lift_pct` 超限会阻断计划准入。页面与 JSON/CSV 导出会保留当前模型、Holdout、滚动窗口、质量阈值和业务护栏参数。KS 结果明确是小样本风险信号，列边际结果不代表联合分布、统计显著性或生产监控结论；基线改善不代表生产收益。该场景已完成本地真实分析器、HTTP/API 回归和本轮 Ego 桌面复验，但不是生产预测或收益承诺。
- Docker 单容器、临时 PostgreSQL、HTTP 固定替身和本地文件 checkpoint 已有分层验证。
- 本轮已将会话 Artifact 查询接入任务历史弹窗；交付物在“本会话交付物”区域独立展示稳定 ID、运行 ID、工作表、覆盖行数、快照摘要、最终结论和下载次数。查询改为 `/api/session/<sid>/lifecycle/artifacts`，并新增单个 Artifact 详情入口；列表和详情都执行会话所有权与 active 状态过滤，跨会话或归档对象按不存在处理。
- 交付 Artifact 保留分析参数、生成 SQL、图表规格、最终结论、warnings 和结构化成本；固定报表明确记录为确定性计算、模型调用 0 次、0 Token、0 USD 且非估算，不伪造 provider 账单。报表预览保留 Evidence 来源详情和 Claim 的轻量来源引用，不提供 Ledger 决策、审批、复算或治理审计 UI；真实 Agent 的 usage 仍按稳定 Run ID 聚合并回填到同一 Run 生成的 Artifact。

### 已撤销的 Evidence 治理试验（历史记录）

以下 P1 Evidence Ledger、质量门、语义复算、审批/修订和治理审计条目保留作历史验证记录，但已不属于当前运行时；当前范围以本节前的回滚决定和 active 代码为准。
- 2026-09-01 隔离本地真实桌面验收：服务地址 `http://127.0.0.1:5217`，数据目录 `/private/tmp/pfs-audit-browser.soChCr`，Session `1718cfc6-047a-4b2f-9fae-d353afc71fac`，Job `d251964a-98e`，Artifact `artifact-audit-browser-d251964a-98e`。夹具包含 2 个 Claim、2 条 Evidence（1 supports/1 refutes 冲突）、1 条无证据 Claim 和 1 条 `needs_review` 人工裁决（业务口径不一致）。最终 API/页面统计为 1 个成功任务、1 个交付物、2 个 Claim、1 个无证据结论、1 个冲突、1 次模型调用、150 Token（120 input + 30 output）、费用未知；页面视口 1280×720。满数据、工具类型筛选、关键词无匹配空态、重置筛选、Failed to fetch 错误态、弹窗内部滚动和显式关闭按钮均通过；审计输出未暴露绝对路径、完整 SQL、完整工具参数。
- P0 收口复测在隔离 `PFS_DATA_DIR` 和 `http://127.0.0.1:5012` 完成：任务历史审计弹层打开后，关键词输入框保持焦点并有输入值时按 Escape，弹层由可见变为关闭。Workflow 节点新增真实 `model_calls` 持久化；导出 Artifact 记录 Workflow Run/节点身份，并在导出时和 Run 终态按全部已记录节点用量刷新成本。任一已测量节点单价未知时总费用保持 `null`，不伪造 0；这仍不是供应商账单对账。
- 本轮补充结构化错误边界：缺字段、日期异常、大文件上传、模型未配置和交付生成失败均有明确 code 或处理建议；聊天 SSE 的模型构建错误会透传 `model_not_configured`。真实桌面已验证异常日期 CSV 会保留在数据源选择器并标记“需修复”，运行时显示中文日期修复建议；101 MiB CSV 会在上传弹窗显示 100 MB 上限且服务端不保留超限副本。当前工作区又为 PFS 同步报表分析增加唯一 Run ID、会话所有权校验、取消按钮、`AbortController` 和服务端取消检查；并发回归确认取消后不提交结果。该能力只覆盖单 Python 进程内的协作式取消，不能立即中断 pandas 正在执行的单个读表步骤，也不等于跨进程恢复。
- P1 第一、第二切片新增可持久化治理合同：每条 Claim 保存 `semantic_verification`（verdict、risk、confidence、rationale、engine/version/time）、`approval_status`、审核人、审批修订号和 Claim 版本。会话安全 API 支持审批队列、重新核验、批准/拒绝/暂缓/退回及退回后修订；审批与修订均带乐观锁，跨会话访问继续按不存在处理。任务历史审计中心可直接完成这些操作，并把核验、审批、修订写入安全审计时间线。修订 Claim 会清空旧 Evidence 关系并回到 pending，证据关系发生实质变化也会使旧审批失效并递增审批修订号，防止旧证据或旧裁决错误继承。治理入口要求 Evidence 与当前分析快照使用同一 SHA-256，否则拒绝登记；重复分析在证据关系未变时保留原审批。旧 Claim 没有数值复算契约时继续使用 `deterministic_claim_evidence/v1`，它只核验登记关系与覆盖完整性，不是 LLM 事实核查或生产级语义评测。
- P1 第三切片已为标准 CSV/XLSX Claim 增加可持久化 `RecomputeContract`：保存源快照 SHA-256、源 ID/工作表、SUM 字段、日期范围、维度过滤和期望值。报告生成和 `/verify` 都会重新读取同一上传快照，使用 Decimal SUM 复算并返回 `deterministic_recompute/v2` 的支持、反驳、冲突或无法复算结果及结构化复算明细；源文件变化不会用新数据替代旧证据。v2 证明的是登记的确定性数值能否在同一快照重现，不是自由文本事实核查。
- P1 Evidence 质量门切片新增 `pfs_agent/evidence_quality.py`：以 advisory 或严格 snapshot policy 检查来源 URL、身份/内容哈希、任务范围、采集时间、可选新鲜度、来源元数据、表格行/字段/定位范围、日期范围和来源可用状态；Claim 额外检查 Evidence 覆盖、缺失关联和任务边界。质量结果写入 `semantic_verification.evidence_quality`，报表治理使用严格快照策略，`/api/pfs/ledger/quality` 与会话范围 API 支持复核；质量检查不联网，也不把通过当作外部事实为真。
- P1 语义评估切片新增 `pfs_agent/evaluation.py`：对人工维护的固定 Claim 集合逐 Case 对照已持久化 `semantic_verification` 和 Evidence 关系，真实产出 Claim 覆盖率、引用准确度（含 precision/recall/F1）、冲突识别率和确定性计算一致性，并同时输出缺失 Claim、错引、漏冲突和复算退化明细。另新增 `pfs_agent/model_evaluation.py`，固定分类/回归契约已用本地分析器残差验证，时间序列适配器已接入 Agent 的 ARIMA/SARIMA/VAR/Prophet/GRU 结果：从 `analysis_result` 提取历史配对，排除未来 forecast 行，计算 MAE、RMSE、bias、MAPE、WAPE、sMAPE 并将覆盖率落到 `analysis_evaluation`；用户明确传入 `analysis_options.evaluation_mode=temporal_holdout` 时，会在训练前缀上重拟合并用末尾真实行做严格时间切分，五类本地分析器均有实际回归。两个评估器都不抓取网页、不把 LLM 置信度当真值，也不判断自由文本的现实真伪；独立事实核查、生产业务语义评测和真实模型预测质量仍待后续。
- P1 Evidence Ledger 存储切片新增 `SQLiteEvidenceLedger`：固定 schema、Evidence/Claim/Link 索引、WAL、`BEGIN IMMEDIATE` 事务、读回刷新、回滚和两个独立进程并发回归；API 通过 `PFS_LEDGER_BACKEND=sqlite` 显式启用，默认仍使用 JSON，未自动迁移现有 JSON 数据，避免无授权切换后隐藏历史记录。它是本地/单库事务边界，不是分布式数据库、复制或供应商账单系统。
- P1 Ledger 迁移切片新增 `migrate_json_ledger_to_sqlite`：只接受已校验的 JSON Ledger，目标 SQLite 必须为空，Evidence、Claim、Link、审批、复算契约在单事务中导入并回读；源文件不修改，目标非空时拒绝。该工具不自动替换运行中的后端。
- P1 长任务控制切片新增 `PersistentAnalysisRunRegistry`：通过 `PFS_RUN_REGISTRY_BACKEND=sqlite` 显式启用，报表分析 worker 在 checkpoint 读取跨进程取消标记，治理提交前以事务关闭取消窗口；同主机崩溃 owner 可在下一次 begin 时回收，活跃 owner 的重复 run id 仍拒绝。它只覆盖本地单库的协作式取消，不等于跨服务队列、强制中断或完整 Workflow 恢复。
- Agent 工具安全切片随后补齐了后台 `query_data` / `create_analysis_table` 的实现层二次 SQL 校验，并为 CSV、Excel、HTTP、飞书内存源登记 raw/derived 表边界：分析表可替换或删除，原始表不能被覆盖或删除，未知表 fail closed。SQL 缓存表和工作区 registry 的保护规则继续生效；当前已有固定 CSV 的真实创建、替换、删除、源表保护回归，生产数据、并发删除审计和跨进程派生表生命周期仍待验收。
- Agent 运行预算切片新增 `agent/budget.py`：`PFS_MAX_ITERATIONS`、`PFS_MAX_TOOL_CALLS`、`PFS_MAX_TOTAL_TOKENS`、`PFS_MAX_RUN_SECONDS` 和 `PFS_MAX_JOB_SECONDS` 经过整数与范围校验后，由聊天主入口传入 Agent；运行级轮数、工具调用、实际 usage Token 和普通后台 Job 的协作式超时仍由 Agent/JobRunner 内部执行，模型输出不能扩大上限。该切片是本地环境配置合同，不等于工作流图级预算动态化、供应商账单对账或强制中断非协作任务。
- Workflow 预算切片补齐节点 `max_total_tokens` 校验、图级剩余 Token/费用对节点 limits 的收窄、SQLite 事务内的 Token/已知费用原子预留、存在图级模型预算时的模型节点串行调度，以及 `max_total_node_runs` 对失败重试、人工重试和 Verifier rework 的硬阻断；Token 达到图级上限时不再放行后续调度。随后补上 Workflow 费用预算的未知单价 fail-closed：只要启用图级或节点级费用上限，当前有效模型缺少完整输入/输出单价就会在 Run 创建前返回 `workflow_unknown_price`，不会先启动模型调用；无费用上限的运行仍保留费用未知观测。最新又补上运行时启动恢复：新进程会重放无副作用节点的持久化失败状态，暂停/待审批运行保持不动；写入、导出和网络节点在 Job 因重启收口时返回 `workflow_restart_replay_blocked`，不盲目重复外部副作用。随后为导出节点增加本地 SQLite 副作用登记：同一 Run、节点和显式迭代的重复导出复用已完成 Artifact；内容变化触发幂等冲突；已声明但未完成的动作仍要求人工复核。该切片仍是本地单库合同，不等于跨服务队列、强制中断非协作任务或生产恢复。
- SSE 流中断切片补齐客户端断开后的协作式收尾：Agent 尚未构建或已收到部分文本时关闭生成器，都会取消 tracked turn、释放 source snapshot，并且不会在 `GeneratorExit` 路径继续发送 `done`；该切片不等于断线重连、事件续传或跨服务恢复。
- P1 隔离桌面验收使用 ego-browser、`http://127.0.0.1:5012` 和独立临时数据目录：2 条低风险 supported Claim 均显示机器核验、Evidence 和四类审批按钮，弹层无横向溢出；批准第一条后待审批数从 2 降至 1并产生审批时间线；第二条退回后出现修订输入，提交后由 v1 升至 v2、旧 Evidence 清空、核验变为 insufficient 且仍处于 pending。验收空间和服务已关闭，临时目录移入系统废纸篓。
- P1 持久化第二切片为 JSON Ledger 增加 OS 级互斥锁、每次写入前重载最新快照、唯一临时文件和原子替换；两个独立进程并发注册 Evidence/Claim 后，最终回读同时保留双方记录，避免最后写入者覆盖。该实现仍是本地 JSON 运行时，不等于数据库事务或分布式 Ledger。
- P1 快照绑定第二切片补充 Evidence/Claim 关联完整性：治理函数校验源快照和每条 Evidence 的 `content_sha256` 一致，缺失或不一致分别返回 `evidence_snapshot_missing` / `evidence_snapshot_mismatch`；Ledger 证据关系变化会清空审核人、决定和理由并回到 pending。Ego 浏览器真实上传用户—供给 XLSX 后回读“快照已绑定 · 1 条证据”，点击确认支持后重新运行仍保持 approved；页面与报表弹层横向溢出均为 0。
- 缺字段、模型未配置和交付失败又在空白 `PFS_DATA_DIR` 的隔离本地运行中完成桌面回读：缺少销售指标列时保留 `source_columns_missing` 并显示字段建议；空白模型配置经 SSE 显示 DeepSeek 配置引导；只读输出目录返回稳定 `delivery_generation_failed`，不暴露本机路径，交付状态整行换行且无横向溢出。至此本阶段约定的桌面错误态已全部回读。
- Artifact 工作区关联已形成本地闭环：挂载目录生成的文件只能在该工作区 `artifacts/` 边界内登记，生命周期 registry 记录稳定 Workspace ID；会话切换/卸载后仍能按已知工作区身份读取和下载，独立应用进程重开同一数据目录后关联保持不变。回收恢复会返回原工作区目录，同一 Artifact ID 只允许幂等复用同一文件，不能覆盖其他产物。API 与任务历史仅返回工作区名称、短 ID 和可用状态，不返回绝对路径。

## 4. 发布状态

| 阶段 | 状态 | 证据 |
|---|---|---|
| implemented | 已完成本轮切片 | Artifact、安全元数据、错误状态、测试和文档已整合；Evidence 治理层不在 active runtime。 |
| locally verified | 本轮通过（治理回滚后的运行时） | 2026-09-04 完整项目质量门通过：267 项 Python 测试、前端格式/ESLint、Dashboard/Chat production build、Ruff 和 Python 格式检查均通过；前端治理 UI、Ledger 路由和 Evidence 治理模块已撤下。 |
| committed | P0 已完成 / 本轮切片待提交 | P0 主切片为 `756f358 feat: close PFS local transformation slice`，本轮 Evidence 治理回滚与 P1/P2 改造已通过质量门，正在形成新的提交。 |
| pushed / PR | `pending` / 无 PR | 提交后须回读本地 `HEAD` 与 `origin/main`，确认本轮切片已推送；无 PR。 |
| deployed | 未执行 | 无 PFS 部署 marker。 |
| live verified | 未验收 | 无 PFS 线上用户路径证据。 |
| knowledge closed | 本轮完成 | 文档、规则和获准记忆修正入口已对齐。 |
| cleaned | 已完成授权范围 | 用户在完整汇报后确认清理；两份临时 staging、一份隔离运行目录和未跟踪的 `direction-approved.md` 已删除。被忽略的本地凭据、数据库、上传、输出和参考快照未获清理授权，继续保留且不进入发布包。 |

## 5. 剩余改造顺序

- 2026-09-04 P0 收口复核：当前工作树的 Evidence 治理回滚与 P1/P2 本地切片已通过完整质量门；提交前已完成差异格式、前端 bundle、运行时冒烟和治理入口残留审计。发布 staging、commit、push、deploy 和 live 状态分别以实际回读为准。
- 2026-09-03 最新增量：需求预测已接入训练/holdout 订单与 GMV 的列边际两样本 KS 距离、10/50/90 分位数解释、可选 `max_holdout_orders_ks_distance` / `max_holdout_gmv_ks_distance` 护栏、页面滚动窗口控件、last-value 朴素基线 WAPE 对比、可选 `min_model_wape_lift_pct` 改善下限和参数一致的 JSON/CSV 导出重算；Workflow 又增加启动时中断运行恢复、对写入/导出/网络副作用的重放保护，以及导出动作的本地 SQLite 幂等登记、内容冲突阻断和已完成 Artifact 重用。专项需求预测 10 项、恢复专项 8 项、导出幂等 2 项与全量 295 项质量门通过；列边际 KS 仍是小样本风险信号，基线改善也不是联合分布、统计显著性、生产监控或业务收益结论。下一轮继续处理真实脱敏数据、跨场景联合漂移、校准/公平性、预测收益/库存护栏和跨服务恢复等未完成项。

### 1）桌面主链的错误态和复杂 Excel 验收（本地完成）

多 Sheet 显式选择、字段切换、空表禁用、非数字指标修复建议和四类交付已经通过真实桌面页面验收。报表同步分析的单进程协作式取消也已用 250 万行 CSV 完成真实桌面点击、状态、按钮和下载/交付禁用。缺字段、日期异常、101 MiB 超限上传、模型未配置和交付失败均已完成真实桌面回读。当前又补了显式 SQLite 运行注册表，可在本地跨进程传播取消并回收已崩溃 owner；Workflow pause/resume 已有本地持久化入口和待审批恢复回归。跨服务取消、长任务恢复和跨进程 Workflow 调度恢复仍归入第 5 项可靠性工作；固定模型预测质量评估合同、分类/回归/时间序列实际输出接线、显式 temporal holdout 合同和月度需求预测业务验收已完成本地回归；需求预测另已补 last-value 朴素基线比较与可选 WAPE 改善下限，当前转入真实脱敏数据接入、生产语义/预测质量和更广模型业务验收。

### 2）扩展 Artifact / 来源元数据的可查询范围

Excel、Word、PPT 和 Dashboard 已登记为本地生命周期 artifact，并记录稳定 Artifact ID、Run ID、数据快照哈希、工作表、纳入行数、Metric Contract、分析参数、SQL、图表规格、最终结论、warnings、轻量 Claim/Evidence、结构化成本和下载历史；报表交付区和任务历史弹窗已展示并可展开查看安全结果元数据。当前已补会话安全的历史列表、单个详情、工作区名称/短 ID 和工作区可用状态；回归证明工作区 Artifact 在切换、卸载、回收恢复和独立应用进程重开后仍保持同一 Workspace ID 与 Artifact ID，幂等重登记不会生成重复记录，绝对路径不会进入 API。真实 Agent usage 按 `session_id + run_id` 聚合回填；Workflow 导出也记录 Run/节点身份，并在 Run 终态按持久化节点用量刷新调用次数、Token、provider/model 和费用来源。配置价格只形成估算，`billing_verified=false`，未知价格不伪造 0。报表结果保留轻量来源/结论字段，不再写入或查询 Evidence Ledger。下一步进入分布式恢复、供应商账单对账和更广能力复验。该结论是本地 JSON registry 加已知本地工作区的跨进程重开，不是分布式故障恢复或供应商账单对账。

### 3）证据治理与人工裁决试验（已撤回，历史记录）

> 本节记录曾经新增的 Evidence 治理层，不属于当前运行能力、验收完成项或后续 P0/P1 待办。当前只保留轻量 Claim/Evidence/来源快照和参考 Agent 工具结果留痕。

本轮第三切片已为标准 CSV/XLSX Claim 增加可持久化 `recompute` 契约（源快照 SHA-256、字段、时间范围、维度和期望值）。`/verify` 会重新读取同一上传快照，使用 Decimal SUM 复算并返回 `deterministic_recompute/v2` 的支持、反驳、冲突或无法复算结果及结构化复算明细；旧 Claim 没有该契约时继续使用 `deterministic_claim_evidence/v1` 兼容读取。随后补齐 Agent 派生表 raw/derived 登记与后台 SQL 二次校验，固定 CSV 已真实验证分析表创建、替换、删除和原始表保护。新增的固定 Claim 评估契约会逐 Case 统计 Claim 覆盖率、引用准确度、冲突识别率和计算一致性。v2 与固定评估器及该工具切片只证明确定性数值/本地表边界，不是自由文本事实核查、生产级语义评测或分布式事务。

Evidence 详情、Claim 支持/反驳关系、确定性关系核验、风险等级和人工审批队列已接入 Ledger、会话 API 与任务历史审计中心。审批支持批准、拒绝、暂缓、退回修改，保存服务端审核身份与修订号；退回后可修订 Claim，旧 Evidence 关联会被清空并要求重新核验。Evidence 关系发生实质变化时旧审批会自动失效并回到 pending；治理入口会校验 Evidence 与当前分析快照 SHA-256 一致，重复分析在关系未变时保持原审批。专项回归覆盖跨会话拒绝、陈旧审批修订冲突、证据变更失效、快照哈希不一致、陈旧 Claim 版本冲突和持久化迁移。Evidence 质量门已补齐来源元数据、时效、表格覆盖、日期范围、来源状态和 Claim 关联覆盖检查，并在严格快照策略下阻断质量失败。边界：`deterministic_claim_evidence/v1` 不读取自由文本判断现实真伪，只根据登记的 supports/refutes、置信度和覆盖情况形成机器提示；真实业务语义评测和独立事实核查仍待后续。

### 4）按功能组完成原能力兼容复验

顺序为：生产 SQL/复杂 Excel → HTTP/飞书 → 14 类分析的业务边界 → 41 个图表的桌面视觉 → Skills/Commands/Knowledge/Memory → MCP/Teams/Hooks/远程能力。商业画布和 Google Sheets 已按范围决策退役，不再进入兼容验收。其余每项必须在兼容矩阵中记录正确、边界、失败和权限样例。

P2 已完成四个匿名化、可重复业务验收场景。第一切片的 10 城经营组合独立真值为日均 760 千单、年化 GMV 156.293 亿元、订单量加权客单价 56.34 元；第二切片的城市月度损益覆盖连续同期、逐行利润桥接、收入/利润同比、城市异常清单、轻量来源/结论展示和服务器重算下载；第三切片的用户—供给效率覆盖 718.0 万月活、20.92% 加权高价值用户占比、0.106 日均下单频次、60.95 商家日均订单量和双低观察城市；第四切片的月度需求预测覆盖五类本地时间序列模型、末尾真实 holdout、两个滚动窗口、自然月对齐、WAPE/MAE、预测总量偏差、训练→holdout 均值漂移、订单/GMV 列边际 KS 分布差异、last-value 朴素基线比较、可选业务护栏和参数一致的 JSON/CSV 重算。另已补固定模型预测质量评估合同，可对分类/回归 holdout 输出逐 Case、逐类别和阈值结果，并把五类时间序列分析器实际输出接入历史配对评估和 `analysis_evaluation`，显式 temporal holdout 还会在训练前缀上重拟合并对齐真实末尾行。四者和本地模型回归都不是公司生产数据；当前仍未覆盖真实脱敏生产报表、生产预测质量/收益、完整分布漂移、复杂图表视觉和因果归因。

### 5）完成长任务、工作流和成本可靠性

本地报表分析已经支持显式 SQLite Run Registry 的跨进程协作式 cancel、checkpoint 和崩溃 owner 回收；Workflow Run 另已补持久化 pause/resume API：暂停只阻止新节点调度，已运行 Job 可完成并被回读，恢复时保留 pending approval。Workflow 图级 Token/费用/NodeRun guard、跨进程原子预算预留、未知价格 fail-closed 和 SSE 客户端断开收尾已补齐本地合同；跨服务 Workflow 调度恢复和非协作任务强制中断仍未完成。仍需复验多轮 SSE、流中断重连/续传、跨服务任务恢复、节点重试的生产形态、幂等副作用、真实多 provider 切换和账单对账。

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
