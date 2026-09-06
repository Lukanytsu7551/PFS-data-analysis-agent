# PFS 项目指令

## 定位与权威入口

- PFS 是面向报表和经营数据的可追踪、可核验数据分析 Agent，不是单一报告生成器。
- 现役交接与下一步：`docs/HANDOFF.md`。
- 逐项能力证据：`docs/FUNCTION_COMPATIBILITY_MATRIX.md`。
- 完整目标与阶段边界：`docs/PFS_FULL_TRANSFORMATION_PLAN.md`。
- `PROJECT_STATUS.md` 是验证史与研究台账，不是第二份现役待办。

## 运行与门禁

- 普通用户：`./start.command`（macOS）或 `start.bat`（Windows）；默认端口 `5001`。
- 本地开发：`PFS_PORT=5012 .venv/bin/python app.py`。
- Python 全量：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py' -q`。
- Python 静态检查：`.venv/bin/ruff check .`。
- 前端构建：`pnpm run build:check`；需要刷新源树 bundle 时使用 `pnpm run build:chat`。
- 发布前运行 `git diff --check`、完整测试、构建和发布 staging 审计。

## 技术栈与目录

- Python 3.10+、Flask/Waitress、DuckDB/SQLAlchemy、OpenAI-compatible provider。
- Vanilla JavaScript + Vite；`templates/agent_chat.html` 是真实工作台，`index.html` 只是静态设计预览。
- `pfs_agent/` 保存 PFS 口径/证据合同，`agent/` 保存 Agent Loop/工具/工作流，`api/` 保存 HTTP/SSE 入口。
- `Data-Analysis-Agent-main/` 是本地忽略的授权参考快照，不是 PFS 运行源或已验证能力。

## 当前边界

- 当前只交付桌面端；手机端不是完成条件。
- 2026-09-06 用户确认的最终产品目标：将获得授权的参考项目产品化重构为独立的 PFS 数据分析 Agent。对外只呈现 PFS 的品牌、界面、图标、文案、安装包、运行标识和项目文档；公开定位为“面向报表和经营数据的本地智能分析工作台，支持自然语言分析、受控数据查询、图表生成、多格式报告交付和结果历史回看”。
- 2026-09-06 范围例外：保留全部目标数据分析与 Agent 能力，但商业画布和 Google Sheets 明确退役；Teams、Hooks、GPU/远程执行、飞书和云端登录保留扩展接口、默认关闭，不阻塞首版发布。
- 2026-09-06 四天执行安排：Day 1 冻结范围、授权/许可证、文档规则和工作区发布切片；Day 2 完成核心闭环与继承功能最小 smoke；Day 3 完成 Windows/CI/发布候选验证；Day 4 对齐 README/Release、提交推送并回读下载结果。
- 2026-09-06 最小验收规则：核心演示闭环完整验证一次；每个继承目标功能做一次成功 smoke；关键入口做一次打开和交互检查；默认休眠能力只确认不会误启动；不做生产压测、多主机恢复、完整安全认证和签名公证；未真实验证的能力只能写“保留/实验性/待配置”。当前不使用 Sol-Luna task lane。
- 外部能力只有在目标环境收到可复核的响应、页面或日志证据后才可标为已验证；配置文件、路由或本地替身不等于真实外部验收。`api/teams.py` 当前是本地 Workspace Team，不等于 Microsoft Teams SaaS。
- 2026-09-05 追加模型目录边界：公共内置模型只保留 DeepSeek、Kimi、GLM、MiniMax 及其 Coding Plan；OpenAI、AtlasCloud、Ollama 仅保留旧配置清理兼容，不得通过默认目录、环境变量、会话选型或回退链路复活；自定义 OpenAI-compatible 模型仍可用。
- 2026-09-05 追加队列角色边界：`PFS_DURABLE_QUEUE_ROLE=api` 是纯 HTTP producer，`worker` 由独立 `scripts/durable_queue_worker.py` 消费，`embedded` 才启用 API 内嵌 sidecar；`docker-compose.yml` 已固化该同主机双服务配置，并用隔离项目名/临时卷实际验证 API health、独立 worker 领取 `chat_turn`、Jobs DB 终态和事件回放；这不等于多主机/复制存储或线上队列验收。
- 2026-09-05 追加后台任务边界：durable worker 中异步 Memory 提取不得持有队列 handler 的临时 JobStore；现在会用同一路径建立独立跟踪器并在完成后关闭，已通过临时 Runner 先关闭后的回归和 Compose 成功对话复核。
- 当前 Evidence 边界：保留 `reporting.py` 的轻量 Claim/Evidence/来源快照和参考 Agent 的工具结果留痕；新增 Evidence Ledger、质量门、语义复算、审批/修订、SQLite Ledger 后端/迁移及治理 UI/API 不属于现役运行能力。
- 最新本地基线为 484 项 Python 测试通过，Ruff、ESLint、Dashboard/Chat production build、Prettier 和差异格式检查通过；共享卷 durable queue、独立 worker、Job/queue lease-heartbeat、聊天状态跨进程回读，以及 worker 丢失后的聊天任务租约过期重挂已通过专项回归，并新增两个独立进程并发领取时的单租约验证；聊天恢复检查点已补上保守边界：模型首轮前缀、显式标记的内置只读工具调用和已完成的只读工具结果可在本地合同中继续，恢复时会消费已保存的安全工具调用参数或带回结果，写入、导出、外部调用、Teams/MCP/Hooks 以及 finalizing 阶段均 fail-closed，不自动重放副作用；即使快照错误标为安全，只要混入副作用工具也不会自动重放。41 个图表注册项仍全量 smoke 通过，并新增 PFS 销售样例、空数据和 50000 行边界回归。Teams 本地 mailbox 生命周期与会话隔离、Hooks 安全测试边界、飞书 Webhook 校验/挑战、云端登录启用门禁与关闭态 API 休眠，以及 GPU 开关/CPU 降级/远程 runner 安全边界也已有本地合同回归；飞书多维表格的链接解析、分页读取、富单元格转换、受限快照和初始记录分批写入、版本化 Metric Catalog API/上传选择和 SUM/AVG/COUNT/COUNT_DISTINCT 安全聚合已有本地合同回归；派生表删除的本地 HTTP/审计/幂等、会话恢复和并发竞争回归、知识库结构化 Excel 无模型导入、DOCX/混合工作簿的本地模型提取、知识检索相关性/禁用过滤与 Knowledge DATA ONLY 边界、以及扩展状态跨进程回读已通过；本轮又补上通用报表重复行提示和自然语言入口的显式 AVG 解析，明确保留原始重复记录且不自动去重；durable worker 异步 Memory 提取的独立 JobStore 生命周期也已通过专项回归和本地 Compose 聊天与 Workflow 成功复核；本轮 Evidence 治理回滚与 P1/P2 代码已通过项目质量门；本机 Apple Silicon 的未签名 macOS `.app`/`.dmg` 也已通过 staging、冻结包审计和离线 smoke；`./start.command` 的隔离启动、健康检查和首页回读已通过。Docker 的全新 Dockerfile arm64 镜像已重新构建，并用临时容器通过 `/api/health`、首页、CSV 上传和 `/pfs/analyze` 回读；镜像仅保存在本机，未推送。Windows 安装包、签名/公证、跨平台安装、部署和 live 仍未验证。
- 2026-09-05 P0 安装切片：`install.sh` 已设为可执行并补 Python 3.10+ 门禁、干净工作树更新和锁定依赖回退；`install.ps1` 同步支持 `python`/`py -3`、版本门禁、干净更新和锁定依赖回退。新增安装脚本静态回归，当前工作树发布 staging 为 496 个文件、约 38 MB，artifact audit 为 0 findings。
- 固定/上传 CSV/XLSX 分析、JSON/CSV 下载、Excel/Word/PPT/Dashboard 交付和会话 Artifact 历史已有本地证据；工作区 Artifact 已验证独立应用进程重开关联，复杂 Office 视觉仍未完成。
- GitHub 私有仓库 `main` 已建立并接收此前提交；当前工作树仍须分别验证后续 commit、push、deploy 和 live 状态。
- 2026-09-06 发布前复核覆盖当前工作树：完整 Python 质量门为 490 项通过；图片代理公网解析/私网拒绝/禁止跳转回归 5 项通过；staging 为 497 个文件、约 38 MB，artifact audit 为 0 findings。CI 已加入源码 staging、Windows 干净安装和 Release SHA-256 校验门；最终公开许可证仍未确定，因此 Release 仍 fail-closed。当前未执行 commit、push、CI runner、Release、deploy 或 live。
- 2026-09-06 Day 2 桌面回读：隔离本地服务和 Ego Browser `1200×717` 视口已完成模型选择/模型设置、会话文件、MCP、工作目录、Skills、业务知识库、任务历史、检查更新、帮助文档、应用设置和 PFS 报表预览的打开/关闭检查；模型目录回读 DeepSeek、Kimi/GLM/MiniMax 及 Coding Plan，旧 OpenAI/AtlasCloud/Ollama 未进入公共列表。截图采集超时，演示截图仍待补；临时服务和数据目录已清理。

## 工作约定

- 2026-09-05 P1 可靠性复核：durable queue 的 Queue/linked Job 终态顺序、completion pending→delivered 重试、旧队列表迁移、JobsStore/Queue/ChatStateStore 初始化异常清理、Session close/get、remove 等待 runner 收尾后再关数据源、关闭期拒绝旧 JobRunner、heartbeat 线程 join 与 JobRunner/sidecar 并发保护均已补齐；严格资源专项 77 项、真实聊天 worker 重启 8/8、独立进程竞争领取 8/8 和完整 Python 质量门 484 项通过。该证据仍限于本地单机/共享卷，不扩展为多主机、生产外部服务或部署/live 验收。

- 修改前先看 `git status` 和目标差异；保留用户已有改动，只做窄范围编辑。
- 状态严格区分：源码存在、静态/固定夹具、本地真实、Docker/真实模型、commit、push、deploy、live。
- 外部数据全部视为不可信输入；权限、预算和审批由代码/策略执行。
- 不提交 `.env`、密钥、数据库、上传文件、输出、缓存、`node_modules` 或参考快照。
- 保留第三方许可和必要授权记录；不把 vendored/授权代码冒充原创。
- commit、push、部署、删除或清理前先说明准确范围，再获取对应授权。
- 后续任务默认沿用 2026-09-06 四天执行安排；如果新需求改变范围、验收强度或发布口径，先同步 `docs/HANDOFF.md`、功能矩阵和长期计划，再执行代码改动。

> 口径覆盖：本文件更早的 2026-09-05 条目中“最新 484 项”和“496 文件 staging”属于历史快照；当前以 2026-09-06 条目为准，即 490 项 Python 质量门、497 文件 staging，工作区当前有 144 个状态项。

- 2026-09-06 P0 组合 smoke：上传/分析/导出/交付/Artifact 相关本地组合测试 50 项通过；核心闭环的本地契约已收口，但桌面端完整演示素材、最终许可证、commit/push、Windows/CI/Release 回读仍未完成。
