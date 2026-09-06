# PFS 现役交接基线

> 更新时间：2026-09-06 +08:00
> 作用：下一次会话或新接手者的唯一现役状态入口。历史证据见根目录 `PROJECT_STATUS.md`，逐项证据见 `docs/FUNCTION_COMPATIBILITY_MATRIX.md`。

> 2026-09-05 容器双服务 smoke：当前工作树镜像中，API 容器以 `PFS_DURABLE_QUEUE_ROLE=api` 启动且健康回读确认没有内嵌 worker；独立 worker 容器使用同一 SQLite 数据卷，真实接收 API 提交的聊天任务并将无模型配置的预期失败状态回写到 API 可读的 Job。该证据收口了本地 API/worker 角色分离和共享卷交接，不等于成功模型调用、多主机队列、复制存储、生产副作用幂等或部署/live 验收。

## 0. 当前四天交付口径（2026-09-06，用户确认）

本轮最终目标是将获得授权的参考项目产品化重构为独立的 PFS 数据分析 Agent。对外只呈现 PFS 的品牌、界面、图标、文案、安装包、运行标识和项目文档。对外定位固定为：

> PFS 数据分析 Agent 是一款面向报表和经营数据的本地智能分析工作台，支持自然语言分析、受控数据查询、图表生成、多格式报告交付和结果历史回看。

本轮先交付一个可下载、可本地运行、可用于演示的 PFS 源码仓库；保留全部目标数据分析与 Agent 能力，但商业画布和 Google Sheets 是明确退役例外。Teams、Hooks、GPU/远程执行、飞书和云端登录保留扩展接口、默认关闭，不阻塞首版发布。

- **P0 必须收口**：GitHub 仓库中的可发布源码、macOS/Windows 启动入口、安装说明、核心数据分析主链路和质量门；每一项都要区分源码、提交、推送、部署和 live 证据。
- **P1 尽力尝试**：真实 MCP、Hooks、Microsoft Teams、飞书机器人、云端登录和线上部署。只有目标服务返回可复核响应、页面或日志，才可写成真实验收；没有凭证、目标项目、Windows 实机或线上环境时保持 `pending`。当前 `api/teams.py` 只有本地 Workspace Team 实现，不把它写成 Microsoft Teams 验收。
- **本轮不设为完成门槛**：多主机队列、复制存储、生产级聊天无损续跑、真实 Teams SaaS/Graph、签名公证、复杂 Office 跨平台视觉和外部副作用幂等。
- [Zafer-Liu/Data-Analysis-Agent](https://github.com/Zafer-Liu/Data-Analysis-Agent) 只作为仓库组织、启动体验和发布结构参考；PFS 不复制其代码、素材、品牌或许可证文本。

当前进度：P0 的文档/规则收口、安装入口一致性和当前工作树 staging 审计已完成第一段；本次已将最终目标和四天安排固化到项目规则与现役文档。2026-09-06 又完成图片代理公网地址/禁止跳转门禁、代理安全回归、产品首屏口径调整、许可证状态文件和发布工作流版本默认值修正；当前工作区仍有未提交改动，commit、push、Windows 实机/CI runner、发布资产下载和 clean-install 回读仍未完成。P1 外部能力继续保持默认关闭和时间盒尝试，不因本地合同回归而升级状态。

> 2026-09-06 P0 范围冻结：本轮只收口 `docs/P0_RELEASE_SCOPE.md` 中的源码、启动/安装、核心演示、质量门和发布边界；商业画布与 Google Sheets 继续完整退役，Teams/Hooks/GPU/远程/飞书/云端登录/真实外部 MCP 只保留默认关闭接口。范围变化必须先更新本文件、功能矩阵和长期蓝图。

> 2026-09-06 P0 本地质量门收口：全量 Python 490 项、核心上传/分析/导出/交付/Artifact 组合 smoke 50 项、Ruff、前端格式/Lint/双 bundle 构建、启动脚本和 staging audit 均通过。核心闭环的本地契约已通过；桌面端完整演示截图、最终许可证、commit/push、Windows 实机/CI runner 和 Release 下载回读仍单独待执行。

> 2026-09-06 Day 2 桌面交互回读：在隔离临时数据目录、`PFS_PORT=5012` 的本地 Flask 服务和 Ego Browser `1200×717` 视口中，真实打开并关闭模型选择/模型设置、会话文件、MCP、工作目录、Skills、业务知识库、任务历史、检查更新、帮助文档和应用设置；模型设置回读 DeepSeek、Kimi（含 Coding Plan）、GLM（含 Coding Plan）和 MiniMax（含 Coding Plan），旧 OpenAI/AtlasCloud/Ollama 未出现在公共列表。口径预览真实回读固定销售样例的 `100000` 合计、地区分组、Claim/Evidence 和来源快照，并触发 Excel 交付入口；完整截图采集在 Ego CDP 层超时，因此截图演示材料仍保持待补，不把本次回读升级为发布截图证据。

> 2026-09-06 Day 3 本地发布候选复核：重新执行全量 Python 质量门（490 项，`OK`）、Ruff、前端格式检查/Lint/双 production build、`git diff --check`；重新生成当前工作树 staging 后得到 497 个文件、38,153,573 bytes，artifact audit 为 0 findings。Ego 临时服务已停止，精确临时数据目录已清理，`5012` 端口无监听。该复核仍不替代 Windows 实机、GitHub Actions runner、最终许可证、commit/push、Release 下载、部署或 live 验收；截图采集仍因 Ego CDP `Page.captureScreenshot` 超时保持 pending。

### 四天执行表（默认后续任务按此顺序推进）

| 时间盒 | 重点 | 交付/验收门槛 | 当前状态 |
|---|---|---|---|
| Day 1 | 冻结范围与发布切片 | 授权/许可证边界、规则和文档分层；审阅工作区发布内容；完成 staging、artifact audit 和必要质量门 | 本地范围与质量门已完成；最终许可证和提交仍待处理 |
| Day 2 | 核心闭环与继承功能 smoke | 核心演示闭环完整一次；每个继承目标功能一次成功 smoke；关键入口一次交互检查；生成真实截图/演示材料 | 本地契约与继承 smoke 已通过；Ego 已完成核心预览和关键入口回读；截图演示素材待补 |
| Day 3 | Windows、CI 与发布候选 | Windows 安装/启动/分析/导出；GitHub Actions 实际结果；发布包不含密钥、用户数据、参考快照或旧品牌 | 待执行；受 Windows 实机、runner 和许可证选择影响 |
| Day 4 | GitHub 对外呈现与发布 | 对齐 README/README_EN/PRODUCT/矩阵；提交、push、tag/Release；重新下载并回读源码和发布资产 | 待执行；不能用 push 代替 release、deploy 或 live |

### 本轮最小验收规则

- 核心演示闭环完整验证一次。
- 每个继承目标功能做一次成功 smoke，不能仅因源码存在就标记完成。
- 关键入口做一次打开和交互检查。
- 默认休眠能力只确认默认关闭且不会误启动，不做真实外部接入。
- 不做生产规模压测、多主机恢复、完整安全认证和签名公证。
- 没有真实验证的能力只能写“保留”“实验性”或“待配置”，不能写成“已完成”。
- 后续任务当前不使用 Sol-Luna task lane；如用户重新指定工作流，以最新明确指令为准并同步本文。

文档分层固定为：本文是唯一现役状态入口；`docs/FUNCTION_COMPATIBILITY_MATRIX.md` 是逐项证据；`PROJECT_STATUS.md` 是历史台账；`docs/PFS_FULL_TRANSFORMATION_PLAN.md` 是长期蓝图，不是本轮待办。

## 1. 产品目标

将授权参考项目改造为完全独立的 PFS 数据分析 Agent：保留并重新验证原有功能，重做品牌、UI、内部运行标识、文档和交付体系，同时增加指标口径、轻量来源/结论留痕、成本、追踪和可靠性。

本轮范围聚焦桌面端。普通用户使用本地 Python 安装/启动脚本，开发与部署人员可使用 Docker；Windows 原生安装和 GitHub 下载体验仍需在本轮单独验收。

2026-09-02 范围决策：商业画布与 Google Sheets 已从产品范围完整退役，入口、前端交互、API、Agent 工具、存储、技能和依赖均不再作为兼容目标。Teams、Hooks、GPU/远程执行、飞书机器人和云端登录代码保留，但当前默认休眠；Hooks/飞书后台启动和云端登录需要显式环境开关，GPU 总开关默认关闭，Teams 默认关闭。

2026-09-04 范围回滚：只撤销本轮新增的 Evidence 治理层，包括 active Ledger hydration、Evidence 质量门、语义复算、人工审批/修订、SQLite Ledger 后端/迁移和对应治理 UI/API。保留参考 Agent 原有的轻量工具结果留痕，以及 PFS 报表结果中的 `claims`、`evidence`、来源快照/定位信息、UI、品牌、数据分析、交付物和普通任务历史。下方带有 P1 Evidence 治理内容的条目是历史记录，不能当作当前运行能力。

2026-09-04 可靠性边界：新增可选 SQLite durable queue、独立 worker 入口、Job/queue lease 与 heartbeat、幂等 dispatch、过期任务重挂，以及聊天请求/来源/工作区快照和独立 SQLite 会话状态。它已在同一主机的共享可写目录上完成跨进程回归，包括独立 worker 进程完成聊天并读回历史，以及 worker 领取聊天任务后消失、租约过期再由新进程重挂完成；不等于正在执行中的模型/工具调用可无副作用重放，也不等于多主机共识、复制存储或外部服务幂等协议。队列默认关闭，启用时需让 API 与 worker 使用同一组 `PFS_*_DB_PATH` 和共享工作区。

2026-09-05 聊天恢复检查点边界：新增 server-only `chat_recovery_checkpoint`。同一主机本地合同验证了模型首轮已展示文本前缀可从已持久化的 `text_delta` 重建后继续，显式标记的内置只读工具可进入安全恢复范围；写入、导出、外部调用、Teams/MCP/Hooks 和 finalizing 阶段一律 fail-closed，返回人工复核/新请求动作，不自动重放可能产生副作用的步骤。恢复检查点不会进入浏览器事件回放。该切片不等于任意 in-flight 工具无损续跑、多主机/复制存储或真实外部服务恢复。

2026-09-05 休眠扩展合同：补充 Teams 本地 workspace mailbox 的创建、质量复核成员、消息收发、成员执行状态、结果回写、清理和删除生命周期，并验证其会话所有权门禁；补充 Hooks 配置校验、提示型 Hook 测试和 HTTP/命令副作用测试拒绝；补充飞书机器人 Webhook token、URL challenge 和事件分发合同；补充云端登录必须同时满足显式开关与 Railway/Vercel 运行标记的门禁，并使关闭态 `/api/auth/*` 保持休眠；补充 GPU 开关类型、CPU 降级、远程连接定义不落明文密码和 remote runner 拒绝任意命令合同。以上是本地合同，不等于真实外部 Teams/Hooks/飞书账号、云端身份系统、GPU/SSH 主机或生产权限验收。

2026-09-05 模型目录收口：公共内置模型只保留 DeepSeek、Kimi（含 Coding Plan）、GLM（含 Coding Plan）和 MiniMax（含 Coding Plan）；OpenAI、AtlasCloud、Ollama 仅保留旧本地配置清理兼容，不再出现在公共 defaults、配置列表、环境变量加载、会话选型、默认回退或模型测试中。用户自定义 OpenAI-compatible 模型不受影响；新增接口级回归覆盖旧配置隐藏、旧环境变量不复活和退役 provider 拒绝。

## 2. 六个事实面

| 事实面 | 状态 | 当前结论 |
|---|---|---|
| 代码 | `changed-and-verified` | P0 本地切片已提交；当前未提交工作树新增 Agent Loop 流中断恢复、兼容性回归、聊天任务级事件回放、断线后后台继续执行、进程重启后的安全失败收口、聊天恢复检查点，以及从 HTTP Stop 传播到 Agent、重试退避、流迭代、自动压缩、知识预检、MCP、网页抓取、Hooks 和子 Job 等待的协作式取消；新增 provider 请求 timeout、父 Agent→子 Job 剩余 deadline、Job lease/heartbeat、Workflow dispatch key 和共享卷 durable queue 交接；聊天已增加可版本化请求/来源/工作区快照与跨进程会话状态回写；同时保留 PFS UI/品牌、CSV/XLSX 确定性分析、四个业务验收场景、模型预测质量评估、Agent/Workflow 预算与恢复、Artifact 成本/幂等、报表 Run 取消；Evidence 治理层已按 2026-09-04 决策从 active runtime 撤回。安全恢复目前只覆盖本地模型首轮前缀/显式只读工具，副作用边界 fail-closed；多主机恢复、真实外部服务、生产数据/语义、部署和 live 验收仍未完成。push 尚未完成。 |
| 运行态 | `verified-current` / `pending` | 2026-09-05 本地服务回读 `/api/health` 为 healthy；`./start.command` 临时数据目录启动后首页 HTTP 200，已停止并清理临时目录；Ego 浏览器在隔离数据目录真实回读模型选择、数据链接、MCP、工作目录、Skills、业务知识库、帮助文档、设置分区、任务历史、口径/数据预览、语言/主题和专注模式，且仅有一个活动侧栏面板；实际挂载工作目录、上传城市经营 CSV 后完成 8 项经营验收。固定销售样例不匹配经营验收时按预期给出可执行错误提示；空模型配置下选择器会明确提示并可直接打开模型设置；商业画布和 Google Sheets 未出现在运行时入口。聊天恢复专项、独立 worker 聊天回读和 worker 丢失后的任务重挂通过。无部署和线上 PFS 可用性证据。 |
| 文档 | `changed-and-verified` | 本文作为现役入口；README、PRODUCT、完整改造计划和能力矩阵按当前边界对齐。 |
| 规则 | `changed-and-verified` | 项目根目录新增精简 `AGENTS.md`，保存命令、权威入口、状态分层和安全边界。 |
| 记忆 | `changed-and-verified` | 项目 `memory/` 含本地应用运行记忆，已被 Git 忽略且不作为项目事实来源；Codex 生成记忆通过宿主允许的 correction note 更新，不直改生成索引。 |
| 工作区 | `changed-and-verified` | 挂载工作区的 `artifacts/` 已纳入生命周期登记边界；切换、卸载和独立应用进程重开后仍按稳定 Workspace ID 读取同一 Artifact，且幂等登记不重复产生记录。该结论仅是本地已知工作区，不是分布式存储。 |

## 3. 已经形成的主链路

- 2026-09-04 Agent provider 故障切换切片：当流式连接在已建立后中断且同一 provider 的有界恢复耗尽时，最多切换一跳已配置的备用 provider；文本续写仍按已展示前缀去重，未完成工具调用从原始消息重开，不把不完整 JSON 送入工具。失败流已返回的 usage 按当时 provider 的价格分别累计，避免跨 provider 切换后费用错算；仍需真实网络故障、真实多 provider 和供应商账单对账验收。

- PFS 独立产品名、图标、运行命名空间、安装/容器入口和真实 Flask 工作台已建立。
- CSV/XLSX 上传、字段选择、受限问句、确定性分组计算、Metric Contract、版本化指标目录、受限安全聚合、Claim/Evidence 和快照哈希已形成本地桌面闭环；`GET /api/pfs/metrics` 可列出固定 v1 定义，上传分析也可显式选择目录版本或安全的 SUM/AVG/COUNT/COUNT_DISTINCT 聚合，自定义指标仍保持兼容。
- JSON/CSV 服务端重算下载已验证。Excel、Word、4 页 PPT 和 Dashboard 已接入统一交付区；Office 结构、HTTP 下载和 Dashboard 桌面打开已回读。2026-09-05 又用当前工作树生成固定 PFS fixture 的 XLSX/Word/PPT，并通过带系统字体配置的 LibreOffice headless PDF/PNG 渲染，中文标题、表头、结论和来源留痕均可读且无方框；这只覆盖固定 fixture 的渲染链路，不等于复杂多 Sheet 内容、原生 Office 应用视觉或跨平台验收。包含说明页、空表、销售明细和异常指标的复杂 XLSX 已在真实桌面页面通过工作表选择、字段刷新、确定性分析和错误状态验收；四类交付物均从所选 `销售明细` 生成。
- 既有 5012/5013 验收地址只是临时本地服务，不是部署地址；本轮 5012 服务已通过 Ego 浏览器回读后停止，任务空间已释放，不作为线上地址。
- DeepSeek 单任务已真实调用 schema 和只读 SQL；停止路径、Job 重启收口、进程重启后聊天任务的 fail-closed 保护、Workflow 节点重试和 Token/费用/工具次数硬上限已有本地证据。
- 14 类分析和 41 个注册图表已有固定夹具输出结构证据，但不等于复杂业务结果和视觉验收。P2 第一切片额外完成 10 城经营组合的真实业务形态验收：城市主键、必填字段、数值范围、整行重复和四类订单结构勾稽通过后，才计算订单量加权指标、年化 GMV、规模/增长/单位贡献排名；贡献余量被明确限制为非利润指标。
- P2 第二切片新增匿名化城市月度损益样本：自动识别城市月粒度，要求连续且逐城一致的同比月份，逐行复算“佣金收入 + 配送收入 - 商家结算 - 履约 - 补贴营销 - 支付成本 - 总部摊销”，不勾稽或缺月时停止输出。有效样本对比 2026 年 1–3 月与 2025 年同期，收入同比 +15.42%，经营利润增加 935.725 万元，经营利润率 8.71%；桂林当前亏损只进入核查清单，不做原因归因。结果保留轻量来源/结论留痕，并支持服务器重算 JSON/CSV 下载。
- P2 第三切片新增匿名化用户—供给效率场景：校验城市主键、关键字段、数值与比例范围、重复行、正向分母和字段完整性；有效样本合计 718.0 万月活用户、150.2 万高价值用户、加权高价值占比 20.92%、整体日均下单频次 0.106、商家日均订单量 60.95，并输出高价值规模前三、效率前三和 5 个“双低”观察城市。结论明确为横截面筛查，不推断留存、转化或因果关系；结果支持轻量 Evidence/Claim 展示和服务端重算。
- P2 第四切片新增匿名化月度需求预测场景：校验月份唯一、YYYY-MM、自然月连续、源表升序、订单/GMV 数值范围和训练窗口；对末尾真实月份做严格 temporal holdout，并补充可配置滚动时间起点，在训练前缀重拟合 ARIMA、SARIMA、VAR、Prophet 或 GRU，再计算 MAE、RMSE、bias、WAPE、sMAPE、窗口误差区间、预测总量差异、训练→holdout 订单均值漂移、订单/GMV 列边际两样本 KS 分布距离和 last-value 朴素基线 WAPE 改善。月度模型的近似步长只在显式月度粒度声明下按自然月对齐，跨月、乱序、错预测月份或缺预测会阻断；没有模型误差阈值、总量偏差、均值漂移、KS 分布或基线改善护栏时只标记“待确认”，显式 `max_total_delta_pct` / `max_holdout_orders_mean_shift_pct` / `max_holdout_orders_ks_distance` / `max_holdout_gmv_ks_distance` / `min_model_wape_lift_pct` 超限会阻断计划准入。页面与 JSON/CSV 导出会保留当前模型、Holdout、滚动窗口、质量阈值和业务护栏参数。KS 结果明确是小样本风险信号，列边际结果不代表联合分布、统计显著性或生产监控结论；基线改善不代表生产收益。该场景已完成本地真实分析器、HTTP/API 回归和本轮 Ego 桌面复验，但不是生产预测或收益承诺。
- Docker 单容器、临时 PostgreSQL（含 Ego 浏览器连接/预览/选表主流程）、HTTP 固定替身（含 Ego 浏览器连接/预览和无 charset 中文 CSV 解码回归）和本地文件 checkpoint 已有分层验证。
- 本轮已将会话 Artifact 查询接入任务历史弹窗；交付物在“本会话交付物”区域独立展示稳定 ID、运行 ID、工作表、覆盖行数、快照摘要、最终结论和下载次数。查询改为 `/api/session/<sid>/lifecycle/artifacts`，并新增单个 Artifact 详情入口；列表和详情都执行会话所有权与 active 状态过滤，跨会话或归档对象按不存在处理。
- 交付 Artifact 保留分析参数、生成 SQL、图表规格、最终结论、warnings 和结构化成本；固定报表明确记录为确定性计算、模型调用 0 次、0 Token、0 USD 且非估算，不伪造 provider 账单。报表预览保留 Evidence 来源详情和 Claim 的轻量来源引用，不提供 Ledger 决策、审批、复算或治理审计 UI；真实 Agent 的 usage 仍按稳定 Run ID 聚合并回填到同一 Run 生成的 Artifact。

### 已撤销的 Evidence 治理试验（历史记录）

以下 P1 Evidence Ledger、质量门、语义复算、审批/修订和治理审计条目保留作历史验证记录，但已不属于当前运行时；当前范围以本节前的回滚决定和 active 代码为准。
- 2026-09-01 隔离本地真实桌面验收：服务地址 `http://127.0.0.1:5217`，数据目录 `/private/tmp/pfs-audit-browser.soChCr`，Session `1718cfc6-047a-4b2f-9fae-d353afc71fac`，Job `d251964a-98e`，Artifact `artifact-audit-browser-d251964a-98e`。夹具包含 2 个 Claim、2 条 Evidence（1 supports/1 refutes 冲突）、1 条无证据 Claim 和 1 条 `needs_review` 人工裁决（业务口径不一致）。最终 API/页面统计为 1 个成功任务、1 个交付物、2 个 Claim、1 个无证据结论、1 个冲突、1 次模型调用、150 Token（120 input + 30 output）、费用未知；页面视口 1280×720。满数据、工具类型筛选、关键词无匹配空态、重置筛选、Failed to fetch 错误态、弹窗内部滚动和显式关闭按钮均通过；审计输出未暴露绝对路径、完整 SQL、完整工具参数。
- P0 收口复测在隔离 `PFS_DATA_DIR` 和 `http://127.0.0.1:5012` 完成：任务历史审计弹层打开后，关键词输入框保持焦点并有输入值时按 Escape，弹层由可见变为关闭。Workflow 节点新增真实 `model_calls` 持久化；导出 Artifact 记录 Workflow Run/节点身份，并在导出时和 Run 终态按全部已记录节点用量刷新成本。任一已测量节点单价未知时总费用保持 `null`，不伪造 0；这仍不是供应商账单对账。
- 本轮补充结构化错误边界：缺字段、日期异常、大文件上传、模型未配置和交付生成失败均有明确 code 或处理建议；聊天 SSE 的模型构建错误会透传 `model_not_configured`。真实桌面已验证异常日期 CSV 会保留在数据源选择器并标记“需修复”，运行时显示中文日期修复建议；101 MiB CSV 会在上传弹窗显示 100 MB 上限且服务端不保留超限副本。当前工作区又为 PFS 同步报表分析增加唯一 Run ID、会话所有权校验、取消按钮、`AbortController` 和服务端取消检查；并发回归确认取消后不提交结果。该能力只覆盖单 Python 进程内的协作式取消；内置 DuckDB/SQL 活动连接、网页响应和当前导出数据读取可在取消/到期时主动收口；不支持 interrupt 的第三方或纯 pandas 阻塞步骤仍只能协作式收口，也不等于跨进程恢复。
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
- SSE 与聊天恢复切片：标准聊天 Job 在 HTTP 请求前已取得稳定 ID 并进入独立聊天 worker，SSE 只读取持久化公开事件；客户端断开不再取消任务，用户显式调用 `/stop` 或 Job cancel 才会取消，任务结束后可按游标回放而不重复提交消息。启用 durable queue 且 API/worker 共享可写卷时，聊天请求、来源、工作区快照和会话状态可由独立进程重建，任务完成后能从另一进程读回历史；未配置或不支持安全重建的 live SQL/API 连接仍 fail-closed，并明确要求重新连接。默认关闭队列，多主机故障恢复、复制存储和外部副作用协议仍未验收。
- 2026-09-04 Agent 核心恢复切片：`BusinessAgent` 现在能捕获 provider 在 `create()` 成功后、流式迭代中抛出的可重试传输错误，最多重新打开当前请求两次；已有可见前缀会进入文本续写提示，并在浏览器输出侧去重，避免恢复后重复整段答案；带部分工具 JSON 的中断会从原始消息安全重启，不拼接不完整工具调用。兼容 provider 未返回 `tool_calls` 结束原因、缺少 delta/index/id 字段的情况；仅有函数名且未因 `length`/`content_filter` 截断的调用才会进入 ReAct 工具分发。恢复请求会合并各次已返回的 usage、Token、费用和模型调用次数，避免预算低估。新增聊天任务级事件回放与后台 worker：服务端为公开 SSE 事件保存会话任务游标，提供会话所有权校验后的增量读取接口，浏览器断流后尝试回放缺失事件而不重复 POST 用户消息；回放投影会移除 recovery/messages 并限制文本、工具和 Hook 载荷。新增进程重启安全收口：JobsStore 持久化 `error_code` / `recovery_action`，聊天回放返回 `job_interrupted_after_restart` 且 `automatic_replay=false`，前端与任务历史提示重新提交。新增 HTTP 回归覆盖任务 ID、文本回放、终态回放、断开后后台完成、显式停止和重启错误契约，完整质量门以本轮实际运行结果为准；这证明本地 Agent/HTTP 合同，不等于真实网络代理、进程重启后无损续跑、跨服务恢复或 provider 账单对账。
- 2026-09-05 聊天恢复检查点增量：server-only `chat_recovery_checkpoint` 在模型首轮前标记可恢复边界，恢复时从已持久化的公开 `text_delta` 重建已展示前缀并提示模型只生成剩余内容；显式标记的内置只读工具可进入本地安全恢复范围。写入、导出、外部调用、Teams/MCP/Hooks 和 finalizing 阶段均标记为不可自动重放，进程/worker 在这些边界中断时 fail-closed 并要求人工发起新请求；恢复检查点不会进入浏览器回放。该验证仍不覆盖任意工具中途无损续跑、多主机/复制存储、真实外部服务或供应商账单对账。
- 2026-09-04 Agent 任务边界增量：普通 Agent 运行时限改为整轮总墙钟上限，不会因每个子 Job 完成而重置；provider 请求 timeout 和父 Agent→子 Job 剩余 deadline 现在共享同一轮预算；上下文自动压缩请求也继承剩余 timeout，并在请求前后响应 Stop/deadline；子 Job 到期会请求协作式取消并持久化 `job_timeout` / `retry_with_smaller_scope`，超时 worker 迟到退出不能改写为成功或普通取消；HTTP Stop 会传播到 Agent、provider retry/backoff、流式迭代、自动压缩和子 Job 等待，提交前取消的 source/file snapshot 清理回调在竞态下只执行一次；完成任务的取消标记会在 Future 收尾时清理，避免长会话内存累积。新增 Agent 直连、自动压缩、JobRunner 和 HTTP 回放回归；当时完整质量门为 288 项 Python 测试通过。
- 2026-09-04 外部扩展边界增量：知识预检、MCP 工具发现/调用、网页抓取、并行工具和同步 Hooks 均接入当前 Agent 的取消检查；MCP 同步桥接、stdio/SSE 传输、工具调用和断线重连共享剩余 timeout，Hooks 动作 timeout 会收窄到父 Agent 剩余预算，取消不会被转成普通错误；新增 7 项 MCP 回归（含真实本地 stdio initialize/tools/list/tools/call/disconnect 链路）与 4 项 Hooks/网页抓取回归（含本地 HTTP fixture 和主动内容剥离）。当前又修复知识库 API、Agent 查询和 Workflow 候选发布丢失 workspace_id 的隔离缺口，并补 4 项跨层作用域回归；随后补齐知识库请求级连接复用/关闭、知识检索 embedding 的剩余 timeout/取消传播、外部工具结果与知识预检的 DATA ONLY 边界、Skills 自动匹配 embedding 的 timeout/取消传播，以及委托 Agent 工具的取消/期限传播和 Hooks 命令子进程的超时终止；本轮继续补齐看板 SQL 预取、默认表发现、Excel/Word/PPT 交付的剩余 deadline/取消传播和临时文件原子发布；当前完整质量门为 352 项 Python 测试通过；真实外部 MCP/网页连接、启用后的 Hooks/Teams 和跨服务恢复仍未完成。
- 2026-09-04 Job/Workflow/Queue 交接增量：JobsStore 为活动任务增加可续租的 owner lease 和 JobRunner 心跳；第二个进程不会在有效 lease 内误收口，lease 过期后可在后续状态/调度巡检中 fail-closed，迟到的旧 worker 不能覆盖已恢复终态，也不能追加迟到事件。Workflow 节点分发增加确定性 `operation_key`，同一 dispatch 不重复提交，已创建但尚未绑定的 Job 可按 key 重新挂回节点；新增共享卷 SQLite durable queue、独立 worker、queue/Job 双 heartbeat、过期任务重挂和旧 worker 租约丢失保护。专项回归覆盖独立进程领取/完成、过期回收、旧 worker 迟到状态/事件写入、幂等建 Job、迁移兼容、未绑定 Job 重挂和独立进程聊天状态回读；边界仍是同一主机/共享可写卷，不等于多主机队列、复制存储或外部副作用协议。
- P1 隔离桌面验收使用 ego-browser、`http://127.0.0.1:5012` 和独立临时数据目录：2 条低风险 supported Claim 均显示机器核验、Evidence 和四类审批按钮，弹层无横向溢出；批准第一条后待审批数从 2 降至 1并产生审批时间线；第二条退回后出现修订输入，提交后由 v1 升至 v2、旧 Evidence 清空、核验变为 insufficient 且仍处于 pending。验收空间和服务已关闭，临时目录移入系统废纸篓。
- P1 持久化第二切片为 JSON Ledger 增加 OS 级互斥锁、每次写入前重载最新快照、唯一临时文件和原子替换；两个独立进程并发注册 Evidence/Claim 后，最终回读同时保留双方记录，避免最后写入者覆盖。该实现仍是本地 JSON 运行时，不等于数据库事务或分布式 Ledger。
- P1 快照绑定第二切片补充 Evidence/Claim 关联完整性：治理函数校验源快照和每条 Evidence 的 `content_sha256` 一致，缺失或不一致分别返回 `evidence_snapshot_missing` / `evidence_snapshot_mismatch`；Ledger 证据关系变化会清空审核人、决定和理由并回到 pending。Ego 浏览器真实上传用户—供给 XLSX 后回读“快照已绑定 · 1 条证据”，点击确认支持后重新运行仍保持 approved；页面与报表弹层横向溢出均为 0。
- 缺字段、模型未配置和交付失败又在空白 `PFS_DATA_DIR` 的隔离本地运行中完成桌面回读：缺少销售指标列时保留 `source_columns_missing` 并显示字段建议；空白模型配置经 SSE 显示 DeepSeek 配置引导；只读输出目录返回稳定 `delivery_generation_failed`，不暴露本机路径，交付状态整行换行且无横向溢出。至此本阶段约定的桌面错误态已全部回读。
- Artifact 工作区关联已形成本地闭环：挂载目录生成的文件只能在该工作区 `artifacts/` 边界内登记，生命周期 registry 记录稳定 Workspace ID；会话切换/卸载后仍能按已知工作区身份读取和下载，独立应用进程重开同一数据目录后关联保持不变。回收恢复会返回原工作区目录，同一 Artifact ID 只允许幂等复用同一文件，不能覆盖其他产物。API 与任务历史仅返回工作区名称、短 ID 和可用状态，不返回绝对路径。
- 2026-09-05 Skills/Commands 桌面复验：在显式临时 `PFS_SKILLS_DIR` 上，Ego 浏览器完成 Skills 面板搜索、内置 Skill 选用、自定义 Skill 创建/回显/编辑/更新/删除和选用；斜杠命令弹层回读可用/不可用状态并成功选用 `/data`。复验中发现并修复自定义 Skill 编辑抽屉被关闭遮罩遮挡的问题；删除只作用于临时测试目录。逐命令参数、生产权限、真实外部命令和跨进程恢复仍未验收。
- 2026-09-05 Knowledge/Memory 桌面复验：在显式临时扩展数据目录的 Ego 浏览器中，知识库完成打开、指标定义/业务规则/背景知识标签切换、指标/规则/背景知识创建、编辑、启停、删除与回显；设置→记忆完成用户级记忆创建与回显。真实选择结构化三工作表 Excel，在无模型配置下完成解析、预览和“全部入库”，回读指标 1 条、规则 1 条、背景知识 1 条和 RAG 分块 1 条；两个独立进程可重新读取 Knowledge/Memory 状态。又在本地 OpenAI-compatible fixture provider 上真实选择 DOCX，完成 2 条自由文本提取、预览和“全部入库”，回读指标 1 条、背景知识 1 条和 RAG 分块 1 条。混合检索相关性、禁用记录过滤和 Knowledge DATA ONLY 边界另有本地回归；真实供应商/生产文档、生产检索质量、复杂文档和跨服务恢复仍未验收；临时数据已清理。

## 4. 发布状态

> 2026-09-05 追加质量门：本轮模型目录退役门禁与 7 个内置模型/Coding Plan 的本地 OpenAI-compatible fixture 请求回归后，全量 Python 测试为 484 项；定向 Ruff check 与差异格式检查通过。全仓 `ruff format --check .` 仍有 292 个历史文件未格式化，未做无关的全仓重排。

> 2026-09-05 CI 门禁改造：`build-release.yml` 现会在 Windows/macOS 测试矩阵执行完整 Python 回归与 Ruff，并由独立前端 job 执行 Prettier、ESLint 和 Dashboard/Chat production build；打包 job 等待两类质量门。工作流结构已在本地解析并用等价命令通过，但 GitHub runner 的实际执行仍待触发验收。

> 2026-09-05 P1 可靠性收口：独立复核确认 durable queue 在无 completion callback 时保留 pending，后续带 callback worker 可安全重试；旧版 ChatStateStore 表可迁移；Queue/Job/JobsStore/ChatStateStore 初始化异常会释放 SQLite 连接；Session close 后不会返回正在释放的会话，remove 会等待 runner 收尾后再关闭数据源，关闭中的 session 不会再暴露旧 JobRunner，heartbeat 线程会在 runner shutdown 返回前退出。严格专项 77 项、全量 Python 484 项通过；真实聊天 worker 重启 8/8、独立进程竞争领取 8/8；相关 Python 文件格式、Ruff、前端格式/ESLint、Dashboard/Chat 构建、编译、staging 审计均通过。全仓历史 `ruff format --check .` 仍有未格式化文件，不在本轮重排范围。

| 阶段 | 状态 | 证据 |
|---|---|---|
| implemented | 已完成本轮切片 | Artifact、安全元数据、错误状态、测试和文档已整合；Evidence 治理层不在 active runtime。 |
| locally verified | 本轮通过（治理回滚后的运行时） | 2026-09-05 完整项目质量门通过：484 项 Python 测试、前端格式/ESLint、Dashboard/Chat production build、Ruff 和差异格式检查通过；本轮相关改动文件的 Ruff 格式检查通过，但全仓 `ruff format --check .` 仍有 292 个既有文件未格式化，未做无关的全仓重排。前端治理 UI、Ledger 路由和 Evidence 治理模块已撤下。恢复专项另通过同一主机独立 worker 与会话状态回读，并验证 worker 丢失后聊天任务租约过期重挂；新增安全恢复检查点、模型前缀重建、显式只读工具调用续跑、已完成只读工具结果续跑、只读/副作用 fail-closed 和 server-only 回放边界回归，并用真实子进程中断后替代 worker 接管验证安全前缀只补写剩余文本；新增混合安全/副作用工具不自动重放和两个独立进程并发领取只产生一个租约的回归；新增关闭期 JobRunner 拒绝、remove 等待 runner 后关数据源、heartbeat 线程 join 与关闭后不触碰 JobsStore 的回归；41 个图表注册项仍全量 smoke 通过，并新增 PFS 销售样例、空数据和 50000 行边界回归；新增派生表删除的本地 HTTP/审计/幂等、会话恢复和并发竞争回归；新增知识库结构化 Excel 无模型导入、DOCX/混合工作簿 fixture provider 提取、检索相关性/禁用过滤、Knowledge DATA ONLY 边界、RAG 分块和 Knowledge/Memory 扩展状态跨进程回读；飞书多维表格的链接解析、分页读取、富单元格转换、受限快照和初始记录分批写入、版本化 Metric Catalog API/上传选择及受限安全聚合也已加入本地合同回归；Teams 本地 mailbox 生命周期/会话隔离、Hooks 安全测试边界、飞书 Webhook 校验/挑战、云端登录门禁、GPU/远程安全边界也已回归。durable worker 异步 Memory 提取改用独立 JobsStore/Runner，已通过原 Runner 先关闭后的专项回归及 Compose 成功对话复核。当前工作树生成的 496 文件 staging、4307 文件/450 链接 macOS arm64 `.app`、未签名 `.dmg` 和 frozen smoke 均通过。固定 XLSX/Word/PPT 的 macOS headless 中文渲染也已回读；复杂 Office 原生视觉、签名/公证、跨平台安装仍未验收。 |
| committed | P0 已完成 / 本轮切片待提交 | P0 主切片为 `9ffeba6 feat: close PFS transformation slice`，本轮 Evidence 治理回滚与 P1/P2 改造已通过质量门；本轮 Agent 核心恢复和聊天事件回放改造仍在当前工作树，尚未提交。 |
| pushed / PR | `pending` / 无 PR | 提交后须回读本地 `HEAD` 与 `origin/main`，确认本轮切片已推送；无 PR。 |
| deployed | 未执行 | 无 PFS 部署 marker。 |
| live verified | 未验收 | 无 PFS 线上用户路径证据。 |
| knowledge closed | 本轮完成 | 文档、规则和获准记忆修正入口已对齐。 |
| cleaned | 已完成授权范围 | 用户在完整汇报后确认清理；两份临时 staging、一份隔离运行目录和未跟踪的 `direction-approved.md` 已清理，2026-09-05 又将根目录 `_pfs-export-test/` 移入 macOS 废纸篓（可恢复）。本轮 staging 使用系统临时目录，审计后已清理；被忽略的本地凭据、数据库、上传、输出和参考快照未获清理授权，继续保留且不进入发布包。 |

## 5. 剩余改造顺序

- 2026-09-04 P0 收口复核：当前工作树的 Evidence 治理回滚与 P1/P2 本地切片已通过完整质量门；本轮又完成 Agent 流恢复、聊天断线回放、后台继续执行、进程重启安全失败收口、整轮总时限、provider 请求 timeout、上下文自动压缩 timeout/取消传播、父子 deadline、子 Job 超时失败、Stop 取消传播、知识库连接生命周期与检索 timeout/取消传播、外部内容 DATA ONLY 标记、Skills 自动匹配 deadline、委托工具取消传播和 Hooks 命令子进程终止收口，并继续补齐看板预取和 Excel/Word/PPT 交付的取消边界；随后补齐 Job lease/heartbeat、排队态保活、过期任务巡检、Workflow dispatch key 幂等和未绑定 Job 重挂、共享卷 durable queue、独立 worker、worker 丢失后的聊天任务重挂以及聊天状态跨进程回读，385 项 Python 测试及前端 bundle/静态检查均通过。发布 staging、commit、push、deploy 和 live 状态分别以实际回读为准。

- 2026-09-05 Agent 恢复边界复核：本地恢复专项验证模型首轮安全前缀和公开 text delta 重建，显式只读工具的 replay-safe 标记及保存调用参数后的直接续跑，以及写入/导出/外部/Teams/MCP/Hooks/finalizing 步骤的 fail-closed；新增真实子进程中断后由替代 worker 接管、消费已持久化前缀并只补写剩余文本的本地回归；server-only 检查点不进入 SSE 回放。该切片不把“本地安全恢复”写成全部 in-flight 模型/工具无损续跑，仍待多主机/复制存储、真实外部服务和部署/live。

- 2026-09-05 质量门计数修订：新增显式只读工具调用参数续跑、已完成只读工具结果续跑、混合安全/副作用工具拒绝自动重放、并发 worker 单租约、PFS 图表边界、派生表删除 HTTP/审计/幂等、会话恢复和并发竞争回归、Session close/get 生命周期竞争回归、关闭期 JobRunner 拒绝、remove 等待 runner 后关数据源、heartbeat 线程 join 与关闭后不触碰 JobsStore 回归、Skills 编辑层级契约、知识库结构化 Excel 无模型导入、DOCX/混合工作簿 fixture provider 提取、扩展状态跨进程回读、混合检索相关性/禁用过滤和 Knowledge DATA ONLY 边界、飞书多维表本地数据源/服务合同、版本化 Metric Catalog API/上传选择、受限安全聚合、通用报表重复行提示以及自然语言 AVG 解析回归后，当前全量 Python 测试为 484 项；此前记录中的 385/386/391/395/397/398/401/404/406/411/415/417/419/420/431/432/476/478 项仅作为前一版快照保留。
- 2026-09-05 休眠扩展合同复核：Teams 本地 mailbox 生命周期和会话所有权、Hooks 配置/提示动作/副作用测试拒绝、飞书 Webhook token/challenge/事件分发、云端登录显式开关与托管环境标记门禁及关闭态 API 休眠、GPU 开关/CPU 降级/远程 runner 安全边界均已通过本地 API/存储合同。真实外部 Teams/Hooks/飞书账号、真实身份系统、GPU/SSH 主机与生产权限仍未验收。
- 2026-09-05 派生表删除回链增量：删除工具和本地 HTTP 入口现在要求显式确认；会话内保存有界、无路径/SQL 的删除操作记录。同一 `operation_key` 和同一表请求会复用已完成结果，换表名会在执行前冲突拒绝；直接 API 与 `/api/session/<sid>/audit` 均已用固定 CSV 回归。该能力只覆盖本地单进程会话状态，不能扩展为跨服务/多主机或真实外部副作用幂等。
- 2026-09-03 最新增量：需求预测已接入训练/holdout 订单与 GMV 的列边际两样本 KS 距离、10/50/90 分位数解释、可选 `max_holdout_orders_ks_distance` / `max_holdout_gmv_ks_distance` 护栏、页面滚动窗口控件、last-value 朴素基线 WAPE 对比、可选 `min_model_wape_lift_pct` 改善下限和参数一致的 JSON/CSV 导出重算；Workflow 又增加启动时中断运行恢复、对写入/导出/网络副作用的重放保护，以及导出动作的本地 SQLite 幂等登记、内容冲突阻断和已完成 Artifact 重用。专项需求预测 10 项、恢复专项 8 项、导出幂等 2 项与此前质量门通过；列边际 KS 仍是小样本风险信号，基线改善也不是联合分布、统计显著性、生产监控或业务收益结论。下一轮继续处理真实脱敏数据、跨场景联合漂移、校准/公平性、预测收益/库存护栏和跨服务恢复等未完成项。
- 2026-09-04 后续增量：未终态聊天任务仍会在无法安全重建时 fail-closed，持久化 `job_interrupted_after_restart` 与 `retry_from_original_request`；在 durable queue 开启且共享卷契约成立时，则由独立 worker 从请求/来源/工作区快照继续执行，并在独立会话状态库回读完成历史，不重复提交原始用户消息。普通 Agent 运行时限现在覆盖整轮总墙钟，provider 请求、自动压缩和子 Job 等待均受剩余 deadline 约束，子 Job 到期持久化 `job_timeout` 并保留重试动作；显式 Stop 传播到 Agent 重试、流式迭代、自动压缩和子 Job 等待，提交前资源清理也有竞态回归；随后补充 Job lease/heartbeat、排队态保活、过期恢复巡检、Workflow dispatch key 交接和旧 worker 租约丢失保护，完整质量门为 361 项 Python 测试、前端格式/ESLint、Dashboard/Chat production build、Ruff 和 Python 格式检查通过。
- 2026-09-04 追加可靠性回归：Job 从创建/排队态开始即由 heartbeat 保持 owner lease，长队列不会因尚未进入执行态而被第二进程误回收；终态、取消、提交失败和 runner 关闭均停止续租，heartbeat 的瞬时存储错误会在下一周期重试；独立子进程回读确认有效 lease 不会被误回收。该增量将完整质量门更新为 352 项 Python 测试；仍只覆盖本地单主机/双存储交接。
- 2026-09-05 Docker 复核：修正 `.dockerignore`，排除 `build/`、安装器、测试、打包目录和已退役 Business Canvas；当前工作树已成功完成全新的 Dockerfile arm64 镜像构建（本地镜像 manifest `sha256:673f9554f4ab05d0e55b77f5a1e56d0b558d0926216acb430dc1aba627a5ccdb`），并用临时容器通过 `/api/health` healthy、首页 HTTP 200、CSV 上传和 `/pfs/analyze`（61,000 合计、3 个地区、2 条 Claim、1 条 Evidence）回读。镜像仅保存在本机，未推送；多服务全栈、部署和线上回读仍未完成。
- 2026-09-05 Docker UI 复核：同一全新 arm64 镜像内的工作台经 Ego 浏览器打开，实际加载 `static/dist/chat-app.js`；空模型选择器显示明确配置入口并可打开模型设置，会话侧栏可展开，实测宽度 `380px`、横坐标 `280px`、背景 `rgb(251,252,255)`。临时容器和浏览器任务空间已清理；该证据仍只覆盖本机容器，不代表部署或线上验收。
- 2026-09-05 普通启动入口复核：端口 5001 空闲时通过 `./start.command` 使用隔离临时 `PFS_DATA_DIR` 启动，`/api/health` 返回 healthy、首页 HTTP 200；随后以 Ctrl-C 停止并将明确的临时数据目录移入废纸篓，未留下运行进程。该证据是本地启动回读，不是部署或线上验收。
- 2026-09-05 指标目录/安全聚合运行态复核：在隔离临时 `PFS_DATA_DIR`、端口 5014 的真实 Waitress 进程上，`/api/health` 返回 healthy，`/api/pfs/metrics` 返回 3 个版本化 v1 指标；通过真实 HTTP 上传 `pfs_sales.csv` 后执行 `AVG(sales_amount)`，回读总平均值 `11111.1111…`、华东 `14000`、华南 `11000`、华北 `8333.3333…`，未传指标版本时返回稳定 `metric_id_missing`。测试数据目录已移入废纸篓；该证据仍不覆盖真实生产口径、复杂公式、真实模型或线上部署。
- 2026-09-05 通用报表数据质量提示复核：在隔离临时 `PFS_DATA_DIR`、端口 5015 的真实 Waitress 进程上，通过 HTTP 上传包含 1 条完全重复记录的 CSV，分析返回总额 `3200`、`snapshot.duplicate_rows=1`，并明确提示“系统未自动去重”；聚焦回归覆盖快照和 HTTP 序列化，证明重复数据不会被静默丢弃或自动改写。测试数据目录已移入废纸篓；该证据仍不替代业务主键去重规则或生产数据质量治理。
- 2026-09-05 Compose 双服务复核：新增 `docker-compose.yml` 固化 API `role=api` 与独立 Worker `role=worker` 的同主机共享卷配置；使用隔离 Compose 项目名和临时命名卷实际启动，回读 API `/api/health`、Worker 独立运行、`chat_turn` 队列任务 `attempts=1`、Jobs DB 的 `queue_task_id`/终态以及 `/chat/<job>/events` 回放。无模型密钥场景按预期安全失败；容器、网络和临时卷已清理。该证据不覆盖多主机/复制存储、真实 provider、外部副作用、生产部署或 live。
- 2026-09-05 Compose 成功路径与后台任务复核：使用隔离 Compose 项目和临时共享卷，API 配置本地 OpenAI-compatible fixture 为 Kimi，独立 Worker 完成一次流式 `chat_turn`，队列和 Jobs DB 均为 `succeeded`、attempts=1，聊天事件可回放；随后异步 Memory 提取在队列 handler Runner 关闭后仍独立落到终态，日志无 `Cannot operate on a closed database`。fixture/容器/网络/临时卷已清理；这仍不覆盖真实供应商、真实多主机或线上服务。
- 2026-09-05 Compose Workflow 成功路径复核：在同一隔离 Compose API/Worker 和本地 OpenAI-compatible Kimi fixture 上，通过 HTTP 创建并发布一个 Agent Workflow，API 提交 `workflow_node` 到持久化队列，独立 Worker 完成节点后回写 Workflow Run；API 回读 Run/Node 均为 `succeeded`，模型调用 1 次、输出 Artifact 1 个、事件序列完整，队列任务 `attempts=1` 且为 `succeeded`。该证据把跨服务验证从聊天扩展到 Workflow，但仍不覆盖多主机/复制存储、真实供应商、外部副作用或线上服务。
- 2026-09-05 洁癖与 P0 安装切片复核：按完整盘点路径统一了 `AGENTS.md`、`HANDOFF.md`、README、PRODUCT、能力矩阵、长期蓝图和历史台账的当前/历史/长期分层；没有删除用户数据、授权参考快照或历史记录。`install.sh` 已设为可执行并与 `install.ps1` 同步加入 Python 3.10+ 门禁、干净 Git 更新和 `requirements.lock.txt` 优先/`requirements.txt` 回退；安装脚本专项 3 项和启动/发布包回归通过，当前工作树 staging 496 个文件、约 38 MB，artifact audit 0 findings。该证据仍是本地源码/staging，commit、push、Windows 实机、CI runner、deploy 和 live 继续分开等待。
- 2026-09-05 自然语言聚合复核：`/api/session/<sid>/pfs/query` 的原有明确销售额/利润问题继续生成 `SUM`；新增 HTTP 回归验证“按地区统计平均利润”生成 `AVG(profit_amount)`，返回总平均值 `272.5`，未改变默认汇总路径。该证据覆盖本地确定性问句解析，不覆盖自由文本、多指标、比率或生产语义理解。
- 2026-09-05 Office/包复核：当前工作树生成的固定 XLSX、Word、PPT 经带系统字体配置的 LibreOffice headless PDF/PNG 渲染，中文标题、表头、结论和来源留痕均可读且无方框；重新构建的 macOS arm64 未签名 `.app`/`.dmg` 通过 staging、包审计和 frozen smoke。Office 证据仍限定为固定 fixture/本机 headless，安装包仍未完成签名、公证、安装升级卸载和跨平台验收。
- 2026-09-06 发布前阻塞复核：刷新本地远端引用后，`HEAD=9ffeba6`、本地 `origin/main=ffa0ec1` 仍未包含当前工作树；本轮未执行 commit、push、tag 或 Release。新增根目录 `LICENSE` 只是权利状态声明，最终公开许可证仍待权利人确认；README/README_EN 首屏已改为产品内容优先，来源与授权保留在独立章节和 `NOTICE.md`。`api/system.py` 的图片代理现在拒绝私网/本机/DNS 私网解析、凭据、片段、非默认端口，并禁止重定向；5 项安全回归通过。当前工作树 staging 497 个文件、38,153,573 bytes，artifact audit 0 findings；Windows 实机、GitHub Actions runner、Release 下载、干净系统安装、部署和 live 仍不能标记完成。参考仓库只用于页面组织参考，版本/图表数量/安装包名以 PFS 源码和构建脚本回读为准。

### 1）桌面主链的错误态和复杂 Excel 验收（本地完成）

多 Sheet 显式选择、字段切换、空表禁用、非数字指标修复建议和四类交付已经通过真实桌面页面验收。报表同步分析的单进程协作式取消也已用 250 万行 CSV 完成真实桌面点击、状态、按钮和下载/交付禁用。缺字段、日期异常、101 MiB 超限上传、模型未配置和交付失败均已完成真实桌面回读。当前又补了显式 SQLite 运行注册表，可在本地跨进程传播取消并回收已崩溃 owner；Workflow pause/resume 已有本地持久化入口和待审批恢复回归；durable queue 已完成同一主机共享卷的独立 worker 领取、重挂、Job/queue 双状态收口和聊天历史跨进程回读，并用 Compose 实际跑通 `workflow_node` 从 API 提交到独立 Worker 执行、Workflow Run/Node/Artifact 终态回写。多主机/复制存储、生产数据库、外部副作用和复杂跨服务 Workflow 恢复仍归入第 5 项可靠性工作；固定模型预测质量评估合同、分类/回归/时间序列实际输出接线、显式 temporal holdout 合同和月度需求预测业务验收已完成本地回归；需求预测另已补 last-value 朴素基线比较与可选 WAPE 改善下限，当前转入真实脱敏数据接入、生产语义/预测质量和更广模型业务验收。

### 2）扩展 Artifact / 来源元数据的可查询范围

Excel、Word、PPT 和 Dashboard 已登记为本地生命周期 artifact，并记录稳定 Artifact ID、Run ID、数据快照哈希、工作表、纳入行数、Metric Contract、分析参数、SQL、图表规格、最终结论、warnings、轻量 Claim/Evidence、结构化成本和下载历史；报表交付区和任务历史弹窗已展示并可展开查看安全结果元数据。当前已补会话安全的历史列表、单个详情、工作区名称/短 ID 和工作区可用状态；回归证明工作区 Artifact 在切换、卸载、回收恢复和独立应用进程重开后仍保持同一 Workspace ID 与 Artifact ID，幂等重登记不会生成重复记录，绝对路径不会进入 API。真实 Agent usage 按 `session_id + run_id` 聚合回填；Workflow 导出也记录 Run/节点身份，并在 Run 终态按持久化节点用量刷新调用次数、Token、provider/model 和费用来源。配置价格只形成估算，`billing_verified=false`，未知价格不伪造 0。报表结果保留轻量来源/结论字段，不再写入或查询 Evidence Ledger。下一步进入分布式恢复、供应商账单对账和更广能力复验。该结论是本地 JSON registry 加已知本地工作区的跨进程重开，不是分布式故障恢复或供应商账单对账。

### 3）证据治理与人工裁决试验（已撤回，历史记录）

> 本节记录曾经新增的 Evidence 治理层，不属于当前运行能力、验收完成项或后续 P0/P1 待办。当前只保留轻量 Claim/Evidence/来源快照和参考 Agent 工具结果留痕。

本轮第三切片已为标准 CSV/XLSX Claim 增加可持久化 `recompute` 契约（源快照 SHA-256、字段、时间范围、维度和期望值）。`/verify` 会重新读取同一上传快照，使用 Decimal SUM 复算并返回 `deterministic_recompute/v2` 的支持、反驳、冲突或无法复算结果及结构化复算明细；旧 Claim 没有该契约时继续使用 `deterministic_claim_evidence/v1` 兼容读取。随后补齐 Agent 派生表 raw/derived 登记与后台 SQL 二次校验，固定 CSV 已真实验证分析表创建、替换、删除和原始表保护。新增的固定 Claim 评估契约会逐 Case 统计 Claim 覆盖率、引用准确度、冲突识别率和计算一致性。v2 与固定评估器及该工具切片只证明确定性数值/本地表边界，不是自由文本事实核查、生产级语义评测或分布式事务。

Evidence 详情、Claim 支持/反驳关系、确定性关系核验、风险等级和人工审批队列已接入 Ledger、会话 API 与任务历史审计中心。审批支持批准、拒绝、暂缓、退回修改，保存服务端审核身份与修订号；退回后可修订 Claim，旧 Evidence 关联会被清空并要求重新核验。Evidence 关系发生实质变化时旧审批会自动失效并回到 pending；治理入口会校验 Evidence 与当前分析快照 SHA-256 一致，重复分析在关系未变时保持原审批。专项回归覆盖跨会话拒绝、陈旧审批修订冲突、证据变更失效、快照哈希不一致、陈旧 Claim 版本冲突和持久化迁移。Evidence 质量门已补齐来源元数据、时效、表格覆盖、日期范围、来源状态和 Claim 关联覆盖检查，并在严格快照策略下阻断质量失败。边界：`deterministic_claim_evidence/v1` 不读取自由文本判断现实真伪，只根据登记的 supports/refutes、置信度和覆盖情况形成机器提示；真实业务语义评测和独立事实核查仍待后续。

### 4）按功能组完成原能力兼容复验

顺序为：生产 SQL/复杂 Excel → HTTP/飞书 → 14 类分析的业务边界 → 41 个图表的桌面视觉 → MCP/Teams/Hooks/远程能力。Skills/Knowledge/Memory 已完成本地扩展垂直切片，Skills/Commands 的桌面主流程也已完成第一段；仍需补逐命令完整契约、真实生产数据/命令和跨进程恢复。商业画布和 Google Sheets 已按范围决策退役，不再进入兼容验收。其余每项必须在兼容矩阵中记录正确、边界、失败和权限样例。

P2 已完成四个匿名化、可重复业务验收场景。第一切片的 10 城经营组合独立真值为日均 760 千单、年化 GMV 156.293 亿元、订单量加权客单价 56.34 元；第二切片的城市月度损益覆盖连续同期、逐行利润桥接、收入/利润同比、城市异常清单、轻量来源/结论展示和服务器重算下载；第三切片的用户—供给效率覆盖 718.0 万月活、20.92% 加权高价值用户占比、0.106 日均下单频次、60.95 商家日均订单量和双低观察城市；第四切片的月度需求预测覆盖五类本地时间序列模型、末尾真实 holdout、两个滚动窗口、自然月对齐、WAPE/MAE、预测总量偏差、训练→holdout 均值漂移、订单/GMV 列边际 KS 分布差异、last-value 朴素基线比较、可选业务护栏和参数一致的 JSON/CSV 重算。另已补固定模型预测质量评估合同，可对分类/回归 holdout 输出逐 Case、逐类别和阈值结果，并把五类时间序列分析器实际输出接入历史配对评估和 `analysis_evaluation`，显式 temporal holdout 还会在训练前缀上重拟合并对齐真实末尾行。四者和本地模型回归都不是公司生产数据；当前仍未覆盖真实脱敏生产报表、生产预测质量/收益、完整分布漂移、复杂图表视觉和因果归因。

### 5）完成长任务、工作流和成本可靠性

本地报表分析已经支持显式 SQLite Run Registry 的跨进程协作式 cancel、checkpoint 和崩溃 owner 回收；Workflow Run 另已补持久化 pause/resume API：暂停只阻止新节点调度，已运行 Job 可完成并被回读，恢复时保留 pending approval。Workflow 图级 Token/费用/NodeRun guard、跨进程原子预算预留、未知价格 fail-closed、SSE 客户端断开收尾、聊天任务级事件回放、标准聊天断线后后台执行和聊天进程重启安全失败收口已补齐本地合同；durable queue 与独立 worker 已完成同一主机共享卷的任务恢复、聊天历史跨进程回读和 worker 丢失后任务重挂回归；聊天安全恢复检查点只覆盖模型首轮/显式只读工具，副作用步骤 fail-closed。多主机/复制存储、跨服务 Workflow 生产形态、外部副作用幂等、任意 in-flight turn 的无损续跑、非协作任务强制中断、真实多 provider 切换和账单对账仍未完成。

### 6）完成 Office/桌面包与发布验收

本机 Apple Silicon 已完成未签名 macOS `.app`/`.dmg` 的 staging、冻结构建、包内容审计和离线 frozen smoke；固定 fixture 的 XLSX/Word/PPT 也已用本机 LibreOffice headless 回读中文渲染。当前只证明本机 arm64 构建和固定交付链路可用，不代表复杂报告的图表/元数据、原生 Office 视觉、签名、公证、安装、升级、卸载或跨平台兼容。仍需在干净 Windows/macOS 环境完成安装包全流程。

### 7）部署与线上闭环

完成密钥管理、生产数据库/必要外部服务、健康检查、固定任务集、真实 DeepSeek 任务、成本/延迟/错误观测和 canonical URL 回读。最终分开标记 commit、push、CI、release、deploy 和 live verified。

## 6. 当前不能宣称的能力

- 原项目全部功能已复刻并重新验证。
- 工作流可在多进程/多服务故障后无损恢复。
- 所有外部数据源、MCP、飞书和多模型供应商都可生产使用。
- 历史记录中曾出现“Office 产物、Windows/macOS 安装包和更新链路已完成跨平台验收”的表述；当前以第 6 节为准：仅本机 Apple Silicon 未签名包和固定 fixture 已验证，Windows/跨平台安装、签名、公证、升级和卸载仍未完成。
- 不能因为 GitHub push 成功，就说已经部署或线上用户已看到新版本。

## 7. 清场候选（未授权删除）

- `.DS_Store`、`__pycache__/`、`.pytest_cache/`、`.ruff_cache/`：可重建缓存。
- `outputs/`、`uploads/`、`auth.db`、`secret_key`、`memory/`：运行态/用户数据，只有在确认不需要恢复本地会话、下载或登录状态后才能清理。
- `Data-Analysis-Agent-main/`：本地授权参考快照，只有在功能兼容复验不再需要源码比较后才能删除。

本轮按用户明确范围删除了商业画布和 Google Sheets 的产品代码、技能、测试、依赖、3,332 个 draw.io 静态文件和 30 个图形库文件；这些已跟踪内容可由 Git 恢复。运行态旧 Google 配置不会自动删除，但会被忽略、禁止再次保存且不会通过配置 API 回传。开发机仍有 8 个被 Git 忽略的 draw.io secret/配置文件和 `business_canvas/business_canvas.sqlite`；为避免误删凭据或用户数据，本轮保留现场，发布策略会整体排除 `static/drawio/`，本轮 staging 回读无相关路径或凭据。根目录临时 `_pfs-export-test/` 已于 2026-09-05 移入废纸篓；其余清场候选未动。

> 2026-09-06 当前工作区复核：本轮修复前盘点为 139 个状态项，发布/安全/文档文件和桌面回读记录补充后当前为 144 个状态项；因此“发布切片已通过 staging 审计”不等于 Git 工作区已经干净。当前仍需授权后再 commit/push。
