# 报表数据分析 Agent：研究与验证台账

> 文档日期：2026-09-01
> 当前状态（历史台账）：PFS 源码边界、产品身份、只读策略门、固定/上传 CSV/XLSX 报表分析、多工作表显式选择、受限自然语言报表问题路由、JSON/CSV 下载、Excel/Word/PPT/Dashboard 统一交付区、轻量 Claim/Evidence 来源留痕、会话统一审计、本地真实 HTTP/桌面浏览器回读和报表核验状态摘要已记录；本文件不用于判断当前发布是否完成
> 现役交接、发布状态和剩余改造顺序以 [`docs/HANDOFF.md`](docs/HANDOFF.md) 为准。本文保留研究取证、分阶段验证和历史边界，不作为第二份现役待办。
> 2026-09-05 口径提醒：本文是历史验证台账；当前四天交付范围、状态和验收门槛只看 `docs/HANDOFF.md`。本文中出现的旧 Evidence 治理、商业画布或 Google Sheets 条目保留作历史，不代表当前产品入口或运行能力。
> 2026-08-30 范围决策：PFS 当前只交付桌面端工作台；手机端适配、移动端完整分析流程和移动端下载不再作为完成条件。此前已经完成的 390×844 验收仅作为历史质量证据保留。

完整独立改造路线见：[PFS_FULL_TRANSFORMATION_PLAN.md](/Users/yangxuan/Desktop/实习/报表数据分析agent/docs/PFS_FULL_TRANSFORMATION_PLAN.md)。9 天改造与框架原理路线见：[FRAMEWORK_9_DAY_PLAN.md](/Users/yangxuan/Desktop/实习/报表数据分析agent/FRAMEWORK_9_DAY_PLAN.md)。第 2 天工具调用规则见：[DAY2_TOOL_POLICY.md](/Users/yangxuan/Desktop/实习/报表数据分析agent/DAY2_TOOL_POLICY.md)，本轮实现见 `/Users/yangxuan/Desktop/实习/报表数据分析agent/pfs_agent/`。

## 0. 2026-08-30 当前复核结论

- 本轮继续改造已完成一项可靠性切片：主 Agent 和工作流委托节点都支持基于 provider 实际 usage 的累计 Token 硬阻断；达到上限后不再执行后续工具调用，并返回明确的安全停止结果。随后补齐了按输入/输出单价计算 USD 费用、主 Agent/委托节点费用硬阻断、会话费用累计、工作流节点费用限制和工作流图级总费用硬阻断；费用与工作流专项测试为 19 项通过。
- 费用预算的边界是“模型调用完成、拿到真实 usage 后，在下一次工具或模型调用前阻断”；没有配置完整单价时保持 unknown，不伪造费用。图级检查只汇总已产生实际 Token usage 的节点；费用刚好达到上限也会阻断。真实供应商账单对账、跨 provider 线上成本和跨进程恢复仍未完成。
- 本轮 Artifact 改造已通过完整质量门并提交、推送到 GitHub 私有仓库 `main`；尚未 deploy 或 live 验收。
- 本轮新增报表核验状态摘要：报告结果会明确显示支持、冲突/反驳和待核验结论数量；Claim 具备关系、核验理由或人工决策字段时才展示对应详情，不虚构语义核验结果。新增专项 UI 契约测试 2 项；前端构建和相关回归通过。
- 本轮浏览器回读确认 PFS 报表预览入口仍可打开，已有上传 `sales-report.xlsx` 数据源和 JSON/CSV 导出按钮；核验状态区已进入 Chat bundle。随后在 Ego Browser 的 390×844 视口重新运行固定报表，真实回读“核验状态 / 当前结论均有支持证据 / 支持 2 · 冲突 0 · 待核验 0”，2 条 Claim 显示正常中文状态与“置信度 100% · 1 条证据”，页面无横向溢出，JSON/CSV 按钮可用，未发现 `pfs_report.*` 键名泄漏。该证据覆盖固定报表移动端结果展示，不等于浏览器上传后文件落盘回读、复杂导出或线上验收。
- 本轮随后完成桌面端 XLSX 浏览器闭环：真实上传一份新的 `sales-report.xlsx` 数据源，报表选择器回读 3 行和 `month / region / sales_amount` 字段；运行后得到总额 100,000、华东 42,000、华南 33,000、华北 25,000、2 条支持 Claim 和 1 条 Evidence。切换数据源时发现旧报表内容残留，已改为清空结果、禁用导出并提示“数据源已切换，请运行分析”，新增专项测试防止回归。
- 上传 XLSX 的 JSON 和 CSV 已通过真实浏览器下载落盘并回读内容：两种文件均包含 3 行覆盖、总额 100,000、三个地区分组、Claim/Evidence 和来源 SHA-256。该证据覆盖桌面本地浏览器即时下载，不代表导出历史 artifact、Word/Excel/PPT/复杂图表或线上下载完成。
- 本轮已把 Excel、Word、PPT 和 Dashboard 接入报表结果下方的统一“生成交付物”区。四种格式使用已有生成器，固定报表在真实桌面浏览器中逐项生成 5 个可打开/下载链接；看板以 1200px 视口打开，回读 1 个 KPI、1 个地区柱状图以及 42k/33k/25k 分组值。
- 实际生成的 XLSX、DOCX、PPTX 已用 `openpyxl`、`python-docx`、`python-pptx` 结构回读；三种 Office 文件和 Dashboard HTML 下载地址均返回 HTTP 200。回读时发现中文看板文件名会导致 Waitress 响应头编码失败，已改为 ASCII fallback + UTF-8 `filename*` 并补回归测试。
- 本轮移除了真实工作台中原项目遗留的社群运营入口：侧栏社群按钮、QQ、Telegram、Discord 链接及其弹窗样式和国际化键；没有替换为虚构的 PFS 社群地址。身份回归测试 8/8 通过，重建后的 Chat bundle 也未发现这些旧入口标识。
- 本轮验证：`tests.test_release_identity` 与 `tests.test_startup_scripts` 共 16 项通过；`pnpm run build:chat`、`pnpm run build:check` 和 `git diff --check` 通过。该结果只证明当前发布源和构建产物的社群入口清理，不代表所有旧作者归属、第三方版权或全部内部标识已经完成审计。

- 当前完整质量门已通过：前端 Dashboard/Chat production build、Python 全量回归和 Ruff 均通过；最新全量 Python 回归为 167 项通过、无跳过。随后完成真实桌面复杂 XLSX 验收：工作表选择、字段刷新、空表禁用、非数字指标错误和 Excel/Word/PPT/Dashboard 四类交付均通过；Dashboard 无横向溢出。报表同步分析的单进程协作式取消已通过并发 HTTP 回归，真实桌面点击仍待验收。
- 复杂 XLSX 使用 `说明`、`空表`、`销售明细`、`异常指标` 四张工作表。选择 `销售明细` 后按 3 行计算得到总额 3,600、华东 2,700、华南 900，Evidence 回读 `worksheet:销售明细` 与 `included_rows:3`；选择 `异常指标` 后明确提示 `metric value is not numeric: '待确认'`，并禁用全部下载和交付按钮。
- 首次验收曾因浏览器连接旧服务进程且 Chat bundle 未重建而只读到第一张表；重启当前工作树服务并重新执行前端 production build 后复验通过。该故障说明源码存在不能替代当前运行进程和构建产物回读。
- DeepSeek 已用本地 Waitress 真实 HTTP 复验：`deepseek-chat` 实际读取 CSV schema、执行只读 SQL 和汇总，得到华东 42,000、华南 33,000、华北 25,000、总额 100,000；最新一次记录 3 次调用、输入 12,676、输出 410 Tokens，约 5.09 秒。
- 本轮新增报表下载闭环：固定示例和上传数据均由服务端重新计算后返回 JSON/CSV，HTTP 回归覆盖文件名、总额、分组、证据和非法格式拒绝；真实工作台固定示例预览点击“下载 JSON”后显示“已下载”。浏览器 Blob 下载不提供可回读的文件事件，因此内容以接口测试为准。
- 当前运行期身份已切换为 PFS-only：前端命名空间、浏览器存储键、运行标记、环境变量、工作区指令文件名和远程 runner 均使用 PFS；重建后的 Dashboard/Chat bundle 未发现旧运行命名空间。旧参考快照、历史文档和本地忽略日志/缓存不属于发布源树。
- 发布 staging 实际生成 3,870 个文件、183,207,740 bytes；artifact audit 为 0 findings，密钥、数据库、上传/输出状态和本地参考快照没有进入 staging。压缩 draw.io 模板中的随机字符串不作为品牌命中。
- GitHub 私有仓库 `main` 已建立并接收当前 PFS 提交；这只证明源码已远端保存，不等于已有 release、deploy 或 live 验收。

### 2026-08-30 GitHub 初始上传尝试（历史记录，随后已成功）

- 本地 `main` 已初始化，并已提交当前 PFS 源码、前端、后端、测试、文档、Docker/部署配置和公开示例数据。
- 上传前已排除本地参考快照 `Data-Analysis-Agent-main/`、`.venv/`、上传/输出状态、数据库、`.env`/密钥、旧品牌截图和旧图标等不应发布的内容；工作文件仍保留在本地。
- `git push -u origin main` 已连续尝试两次，均在连接 `github.com` 的 443 端口时超时；没有产生部分远端提交。
- 当时 GitHub 回读确认仓库尚无远程分支；随后网络恢复并已成功建立远端 `main`。部署和线上验收仍未开始。
- 交付方式已确定为双入口：普通用户通过 `install.sh`、`start.command` 或 `start.bat` 使用本地 Python 环境；开发/部署人员通过 `Dockerfile` 构建和运行 PFS。普通用户不被要求安装 Docker。

当前最主要的未完成项是：原项目全部能力的逐项真实复验、多轮/长任务/跨进程工作流恢复、真实多 provider 成本对账、外部数据源和 MCP/飞书、真实业务语义评测/独立事实核查、复杂报表的 Office 视觉与跨平台打开验收、桌面安装包、Docker 多服务和真实部署。完整审批状态机与确定性 Claim–Evidence 关系核验已进入 P1 本地切片；P2 已新增一个 10 城经营组合业务验收场景，但仍不等于生产数据、14 类模型、事实核查或线上验收。详见功能矩阵。

### 2026-08-30 报表下载闭环（当前迭代）

- 新增 `/api/pfs/export` 和 `/api/session/<sid>/pfs/export`。两个入口会在服务端重新读取固定示例或当前会话上传的 CSV/XLSX，再按同一份 Metric Contract 计算并返回 JSON/CSV，避免把浏览器里的结果直接当成下载内容。
- JSON 下载保留报告结构、Claim、Evidence、快照哈希和请求口径；CSV 下载使用统一的 `section/field/value/detail` 表头，并保留汇总、分组、Claim、Evidence 与 warning。响应同时返回安全文件名、运行 ID 和来源 SHA-256。
- 受限自然语言问题在上传数据导出时会重新解析，因此导出与预览使用同一套日期、指标和分组规则；非法格式会返回明确错误，不会静默生成未知文件。
- 验证范围：固定示例、上传 CSV 和上传 XLSX 的 HTTP 导出回归通过；桌面真实工作台上传 XLSX 后，JSON 和 CSV 均已浏览器下载落盘并回读总额、分组、Claim/Evidence 和来源哈希。
- 新增统一交付区：基于当前服务端重算结果生成 Excel 数据、Word 报告、4 页 PPT 和含 KPI/柱状图的 Dashboard；交付链接在报表弹窗内累计展示。多 Sheet 文件会把分析快照中的工作表选择继续传到 Excel 和 Dashboard，不再默认导出全部表或查询第一张表。
- 当前进展：Excel、Word、PPT、Dashboard 已统一登记为本地生命周期 artifact，记录稳定 Artifact ID、Run ID、源数据 SHA-256、工作表、纳入行数、Metric Contract、Claim/Evidence、生成时间和下载历史；交付响应也返回 Artifact ID、Run ID 与源快照哈希。任务历史已改用会话作用域 Artifact 列表，并提供会话作用域单个详情读取；跨会话和归档 Artifact 不会被该入口返回。复杂内容、原生应用视觉、跨进程工作区历史和线上验收仍未完成。手机端下载已移出当前范围。

## 1. 结论先行

本项目当前不是“从一个 `index.html` 开始做一个空白 Agent”。工作区包含两层材料：

1. `/Users/yangxuan/Desktop/实习/报表数据分析agent/index.html`：已迁移为 PFS 的静态分析工作台原型。
2. `/Users/yangxuan/Desktop/实习/报表数据分析agent/` 根目录：当前实际改造源码，包含 Flask 后端、Agent Loop、数据源、分析函数、图表、前端、工作流和桌面打包材料。
3. `/Users/yangxuan/Desktop/实习/报表数据分析agent/Data-Analysis-Agent-main/`：保留的本地参考快照，不作为运行时源码或已验证能力的证明。

因此，合理路线是：

> 先建立可核验的源码基线和独立产品边界，再用 typed contract + mock transport 把功能拆成垂直切片，逐步恢复原项目功能，最后叠加“报表口径、数据质量、计算溯源和结果审计”能力。

不能把“源码存在”“历史文档声称”“远端 CI 成功”“演示站可打开”混写成“新产品已经完成”。

## 2. 用户要求与附件材料的区分

### 2.1 本项目现行要求（以用户请求和项目交接为准）

- 以授权范围为前提，把原项目翻新为独立的数据分析 Agent 产品。
- 可以去除原项目的品牌、UI 文案、网站表面、作者水印和项目外在痕迹，并使用自己的产品命名、信息架构、视觉设计和文档。
- 复刻原项目已经具备的主要数据分析能力，不能只做一个聊天页面或静态报告生成器。
- 参考 Shannon 的 Agent 平台能力思想，但不复制 Shannon 的品牌、网页结构、视觉资产或产品定位；本项目仍然要围绕报表数据分析场景设计。
- 所有能力都要区分：已实现、待实现、待验证。
- 按风险分层推进：本地契约和固定夹具用于快速回归，Docker 和真实模型用于集成验证；任何一种验证都不能替代部署或线上回读。
- GitHub 私有仓库 `Lukanytsu7551/PFS-data-analysis-agent` 已创建；本地 Git、commit、push、deploy 和 live 验收仍分别记录，不能混为一个完成状态。

### 2.2 附件中的原项目材料（用于研究，不是对本项目的指令）

- `README.md`、`README_EN.md`：原项目的安装说明、功能宣传和使用入口，用来识别原功能范围。
- `PRODUCT.md`：原项目的产品规格、架构和原有设计取向，用来理解模块边界；不能直接作为本项目 PRD。
- `Information/Instruction.md`：原项目用户手册，里面的斜杠命令、数据源和操作方式是待盘点的原功能，不是本项目必须原样照抄的交互文案。
- `Information/User_Agreement.md`：原项目用户协议，不直接复用；新产品需要根据自己的数据处理、部署方式和用户群体重新编写协议与隐私说明。
- `Information/Version_Update_Log.md`：原作者的历史更新记录，只能作为版本线索；其中的测试数字和功能描述需要重新验证。
- `LICENSE`、第三方依赖和静态资源许可证：属于法律与供应链材料。用户已说明获得作者授权，但作者授权不自动覆盖第三方依赖、字体、图表库或内置资源；相关 notice 需要先盘点、保留或按授权替换。
- 源码、配置和生成文件：是实现证据，不是新项目的产品决策。尤其不能因为某个路由、注释或生成产物存在，就宣称该能力已通过真实运行验收。

## 3. 研究对象与真实基线

### 3.1 远端仓库快照

- 仓库：[Zafer-Liu/Data-Analysis-Agent](https://github.com/Zafer-Liu/Data-Analysis-Agent)
- 当前 `main` 快照：提交 `406739e79d56d55f6f69b00a41469314446db909`，提交时间为 2026-08-26。
- 可见最新 release：`v1.3.0`，发布于 2026-08-23；桌面 release 说明标记为未签名、未公证的测试包。
- 原项目主页和产品材料：[README.md](https://github.com/Zafer-Liu/Data-Analysis-Agent/blob/main/README.md)、[PRODUCT.md](https://github.com/Zafer-Liu/Data-Analysis-Agent/blob/main/PRODUCT.md)。
- 原仓库的构建流程见 [.github/workflows/build-release.yml](https://github.com/Zafer-Liu/Data-Analysis-Agent/blob/main/.github/workflows/build-release.yml)。它包含编译检查、前端构建和桌面打包，不等于真实模型任务和全栈集成验收。

### 3.2 本地附件

- 本地路径：`/Users/yangxuan/Desktop/实习/报表数据分析agent/Data-Analysis-Agent-main/`
- 本地附件没有 `.git`，不能直接从本地 Git 历史判断来源、分支或未提交变更。
- 本地文件统计：约 3986 个文件、293 个 Python 文件、513 个 JavaScript 文件；包含大量构建产物和静态资源。
- 已将关键文件的 Git blob 与远端 `main` 对照：`app.py`、`README.md`、`PRODUCT.md`、`pyproject.toml`、`package.json`、`agent/agent.py`、`api/chat.py`、`templates/agent_chat.html` 与当前远端对应文件一致。这个结论只覆盖抽查文件，不代表每个文件都已逐一 hash 对照。
- 发现原项目运行状态文件：`secret_key`、`business_canvas/business_canvas.sqlite` 及其 `-wal`、`-shm` 文件。没有读取 secret 内容，也不应把这些运行时状态带入新仓库。
- 初始附件没有 `node_modules`、本地 `ruff`、本地 `pytest`，也没有 `requirements.lock.txt`；当前验证使用项目级 `node_modules` 和被忽略的 `.venv`，没有把它们作为源码交付物。原质量脚本仍引用不存在的 `Test/` 测试目录，属于后续工程基线问题。

### 3.3 已做的本地验证

#### 2026-08-30 聊天结果可视化闭环

- 前端已新增 pfs_result SSE 事件处理：聊天在文字回答之外，会显示 PFS 分析结果卡片，包括确定性口径、合计、分组结果，以及可展开的 Claim/Evidence 核验链。
- 结果卡片使用 DOM textContent 写入接口返回值，不把上传数据、来源片段或外部内容直接拼进 HTML；普通聊天事件处理保持不变。
- node --check frontend/features/chat-stream.js、pnpm run build:check、pnpm run build:chat 均通过；Chat bundle 由构建脚本重新生成并通过校验。
- PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests -q：107 项通过，1 项可选测试跳过。统计模型固定夹具仍有非致命警告，启动时仍提示可选 MCP.flowchart_server 缺失。
- 这证明本地代码和契约闭环可运行，不等于真实浏览器截图验收、真实模型任务、复杂数据源、长任务恢复或线上功能已完成。

#### 2026-08-30 通用聊天到 PFS 的受控桥接

- 新增 api/chat.py 的显式 pfs_mode=deterministic 路由：聊天请求只有同时提供 source_id 时，才把明确报表问题交给 PFS 受限解析器和确定性分组汇总；返回 SSE 文本、pfs_result、Claim/Evidence 结果和 done 事件。
- 保留普通聊天 Agent Loop 不变；桥接位于原有云端登录、会话归属和额度检查之后，不允许通过该路径绕过身份或配额控制，也不允许模型选择任意 SQL 或计算。
- 新增 tests/test_pfs_http_vertical_slice.py 的 3 项测试，覆盖成功流、缺少数据源和含糊问题拒绝；本轮该专项共 8 项通过。命令：PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest tests.test_pfs_http_vertical_slice -q。
- 边界：这是本地 Flask SSE 接口契约，不是前端已自动接入、真实模型理解、长任务/停止/恢复、外部数据源或线上验收；pytest 因当前 .venv 未安装该模块未执行，不能写成 pytest 通过。

#### 2026-08-28 本轮基线复核

- 新建并回读了功能兼容矩阵：当前源码实际确认 14 类分析、41 个图表 ID、23 个 API 领域入口；这些数字只代表注册/入口盘点，不代表全部功能已验收。
- python3 -m pytest -q tests/test_pfs_ledger.py tests/test_pfs_reporting.py tests/test_pfs_runtime.py tests/test_pfs_tool_policy.py tests/test_release_identity.py tests/test_startup_requirements.py tests/test_startup_scripts.py：36 passed。
- python3 -m compileall -q api pfs_agent agent/tools Function/Analyze：通过。
- 本轮补齐 PFS 的 XLSX 第一工作表适配：`pfs_agent.reporting.load_xlsx_snapshot`、统一 `analyze_file` 和 `/api/session/<sid>/pfs/analyze` 已支持 CSV/XLSX；能力接口中的 `uploaded_xlsx` 已更新为 implemented。
- 早期系统 Python 回归曾为 53 个测试运行、52 个通过、1 个跳过（XLSX 依赖缺失）；该记录已被后续项目环境结果 supersede。当前应以项目环境的最新完整回归和 XLSX 专项回归为准。
- 使用项目虚拟环境执行完整 Python 回归：58 个测试运行，58 个通过；系统 Python 缺少 Flask 仍不作为业务测试环境。完整 Flask 回归证据以项目虚拟环境为准。
- 本轮没有初始化 Git、提交、推送或部署，也没有修改参考快照 Data-Analysis-Agent-main/。
- 本轮新增 PFS 远程 runner 规范入口 `remote_runner/pfs_remote_runner.py`；旧 `baa_remote_runner.py` 保留为兼容转发，SSH 预检优先调用 PFS 入口并回退旧入口。固定预检协议测试通过；没有真实远程 GPU 服务器，因此远程链路仍未完成真实环境验收。
- 本轮补齐本地 XLSX 真实执行验收：项目虚拟环境通过 `uv` 确认 `openpyxl 3.1.5` 可用，XLSX 专项测试 3/3 通过，完整 Python 测试 63/63 通过。该结果只覆盖第一工作表 fixture、统一 Metric/Evidence 契约，不代表复杂 Excel、浏览器上传或外部数据源已完成验收。
- 内部身份迁移已继续推进：除核心运行模块外，事件委托、团队/工作流、业务画布、聊天重试、自动保存、PFS 报表预览和远程 runner 入口均已改为 PFS 优先；旧 BAA 仍保留为兼容回退。前端生产构建 54 个模块转换成功，身份迁移测试覆盖相关模块并通过。后端仍有 BAA_* 配置兼容项、部分 legacy 模块和存储键待审计，因此不能写成内部命名已清零。
- 2026-08-30 继续清理用户可见身份：工作区移除确认文案不再显示历史 `.zhixi` 目录名，改为“工作区元数据”；旧目录仍仅用于后端兼容读取。修复身份迁移测试断言后，身份迁移、PFS HTTP 垂直切片和远程 runner 身份测试共 32 项通过；`pnpm run build:chat` 的 54 模块构建和 bundle 校验通过。
- 2026-08-30 又迁移一处新建运行时状态：`api/state.py` 在首选图表目录不可写时改用 `/tmp/pfs`，不再新建 `/tmp/baa`；Dashboard 的 `pfs_lang`/`pfs_session_id` 已确认以旧键只读回退。相关身份、HTTP、远程 runner 测试共 34 项通过，`pnpm run build:check` 的 Dashboard/Chat production build 均通过。

- 2026-08-30 在真实 PFS 报表预览中加入受限自然语言入口：上传并选择 CSV/XLSX 后，可输入明确的“按地区/渠道统计某段时间销售额或收入”问题；前端调用 `/api/session/<sid>/pfs/query`，展示系统实际识别的指标、分组和时间口径，再复用确定性结果、Claim 和 Evidence 渲染。保留原有显式字段分析路径，并补充 Enter 提交、加载锁和错误提示。JavaScript 语法检查、45 项 PFS/身份/runner 回归、Dashboard/Chat production build 均通过。该入口不是通用聊天 Agent，也不代表真实模型理解、更多计算方式、导出、长任务或线上部署已完成。
- 后端运行目录迁移已完成一段：启动、认证、Skills、Excel/CSV 导入阈值、DuckDB 资源、工作区租约等配置均以 PFS_* 优先并兼容 BAA_*；工作区 Skills、Commands、工具结果和缓存目录新建时使用 .pfs/.pfs_cache，检测到已有 .baa/.baa_cache 时继续读取旧目录，不自动搬移或删除。后端仍有桌面、清理、工作流、知识库和部分数据源配置使用 BAA_*，因此内部命名迁移仍未完成。
- 2026-08-30 补充 XLSX 浏览器验收：Ego Browser 实际完成 Sample-data.xlsx 上传，页面显示上传成功并自动缓存到数据仓库；通过同一会话 API 回读到 1 个 XLSX 数据源、10 行、19 个字段。该样例为城市经营宽表，缺少 month/sales_amount 默认分析字段，因此浏览器层本次只确认上传和数据源回读；受控多 Sheet XLSX 的分析与 Evidence 闭环已由 HTTP 专项测试通过。浏览器视口修复后才计入本条结果。
- 本轮继续迁移了业务画布、清理策略和知识库嵌入配置：业务画布使用 `PFS_BUSINESS_CANVAS_DB`，清理策略使用 `PFS_CLEANUP_*` / `PFS_AUTOSAVE_IDLE_DAYS`，嵌入配置使用 `PFS_MODEL_CACHE_DIR`、`PFS_CLOUD_EMBED_*` 和 `PFS_EMBED_MODE`；旧 `BAA_*` 变量仍作为只读回退。
- 本轮建立 `tests/test_analysis_modules.py` 作为 14 类分析统一复验卡：固定确定性夹具下 13 类返回可用 DataFrame 结果，Torch MLP 因 PyTorch 未安装明确跳过；另有无效目标列错误测试。该测试只证明本地接口/输出形状级冒烟，不证明统计准确性、生产规模、真实 Agent 调度或所有依赖环境。
- 本轮新增 `tests/test_chart_generation_smoke.py` 的注册表专项回归：41 个图表逐项使用固定 60 行夹具生成 HTML，缺失字段拒绝测试通过；同时修复 4 个已确认的输入契约/宽格式兼容问题。专项 2/2 通过，完整 Python 回归更新为 67/67 通过、1 个可选 Torch 测试跳过。该结果不等于视觉、真实数据或线上验收。
- 本轮又迁移了桌面运行与发布链路：`packaging/desktop_launcher.py`、macOS/Windows 构建脚本和 PyInstaller spec 现在以 `PFS_*` 为主配置，旧桌面变量仅作为兼容回退；桌面生命周期状态也使用 PFS 标识。发布身份测试已覆盖 staging、自检和运行参数。当前完整 Python 测试为 63 个运行、63 个通过；XLSX 专项测试 3/3 通过；桌面安装包仍尚未在 Windows/macOS 上实际构建和安装验收。

以下是本轮实际执行过的有限检查：

- `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py' -v`：21 个测试通过（图表 5、报表契约 2、Evidence Ledger 5、运行时适配 3、策略门 6）。
- 本轮修改的 Flask、Agent、策略适配器、报表契约和邮件/渠道模块通过 `py_compile`。
- `frontend/legacy/i18n.js`、`frontend/legacy/update.js`、PFS 报表预览模块和 `frontend/legacy/app.js` 通过 Node 语法检查。
- 项目源码 Python AST 解析：305 个文件、0 个语法解析错误（排除 `.venv`、`node_modules`、构建目录和本地参考快照）。
- `frontend/` 下 JavaScript 语法检查通过。
- `pnpm install --frozen-lockfile`、`pnpm format:check`、`pnpm lint`、`pnpm build` 和聊天 bundle 校验通过。
- 使用被忽略的项目 `.venv`（Python 3.13.14，镜像安装 requirements）通过 Flask test client 回读：`/api/health`、`/api/pfs/capabilities`、`/api/pfs/fixture`（总额 100000）、Ledger 批量幂等/Claim 关联、上传 CSV、上传 CSV 分组分析均返回预期结果。
- 使用 Waitress 在 `127.0.0.1:5177` 启动真实 HTTP 进程并回读 `/api/health`、`/api/pfs/fixture`、`/api/pfs/capabilities`、`/` 和 PFS SVG 图标；均返回 200，响应包含安全 headers，页面含 PFS 文案且不含已审计的旧品牌词。
- `ruff check .` 通过；全仓 `ruff format --check .` 仍会报告原始上游大量未格式化文件，本轮只对 PFS 新增/改造 Python 文件执行格式化检查，不做全仓格式化重写。
- 发布 staging 生成与 artifact audit 已通过：3868 个文件、183143559 bytes、0 findings；固定报表 fixture、PFS Windows 图标、PFS 打包 spec 和聊天 bundle 均纳入审核 staging。
- 本轮前端构建通过：`pnpm run build:chat`（54 个模块转换成功，`verify-chat-bundle` 通过）和 `pnpm run build:check`（Dashboard、Chat 两套 production build 通过）。本轮 MCP、知识库、工作目录模块的 PFS 命名迁移通过 ESLint；构建产物由命令生成，未直接编辑 `static/dist/chat-app.js`。
- 在 `PFS_PORT=5012 .venv/bin/python app.py` 启动的本地服务上，已完成 390×844 视口的真实工作台侧栏验收：Skills、业务知识库、MCP 之间互斥切换；Escape 关闭；点击 Agent 导航关闭；会话文件/数据链接抽屉打开与关闭；backdrop 外侧点击关闭；关闭后恢复 Agent 导航。
- 在浏览器模拟约 1.4 秒网络延迟的条件下，已验证 Knowledge、Skills 的异步加载完成后不会重新打开已被 Agent 导航或 Escape 取消的过期面板请求；Knowledge 切换到 MCP 时最终只保留 MCP 面板。

以下内容本轮没有验证，必须继续保持“待验证”：

- Flask 服务启动后的完整 API 链路（目前只回读 PFS 垂直切片）。
- 真实业务 Excel/数据库/API 数据源连接；PFS fixture、上传 CSV/XLSX 和临时 PostgreSQL 已验证。Google Sheets 已于 2026-09-02 按范围决策退役。
- 多轮真实模型调用、跨 provider 费用对账、SSE 长任务和工具循环恢复；DeepSeek 单轮、Token 记录和停止路径已验证。
- Redis、Temporal、外部 MCP、Feishu、权限和多用户状态。
- Docker 全栈、PyInstaller/Inno/macOS 桌面安装包最终产物、完整重启恢复、真实部署和线上登录后的分析流程。
- 当前环境仍缺少具体厂商 ODBC 驱动，`pyodbc.drivers()` 为空，因此 SQL Server/ODBC 仍待真实连接验证。流程图/商业画布链路的历史回归仅保留为验证记录；该能力已于 2026-09-02 退役，真实 MCP 连接仍待验证。

### 3.4 外部演示站验证边界

原项目配置的公开演示站可以返回 HTTP 200，但本轮只看到登录页（`v1.3.0 LTS`），没有进行登录，也没有验证登录后的分析、数据上传、模型调用或导出。因此“线上地址可访问”不等于“线上分析功能已验收”。

## 4. 现有页面审计：不要覆盖 `index.html`

文件：`/Users/yangxuan/Desktop/实习/报表数据分析agent/index.html`

### 已实现（静态原型层）

- PFS 品牌的深色侧边栏和分析工作台布局。
- 会话、模型、数据源、工具、工作区等控制区。
- 分析、Skills、知识库、工具、历史、画布、帮助等导航入口。
- 对话空状态、示例问题、输入框、运行按钮和额度摘要。
- 响应式布局、键盘 focus 样式、跳过链接和 reduced-motion 处理。

### 尚未实现或尚未接通

- 文件中没有 JavaScript `<script>`，没有实际请求、状态管理或表单提交。
- 示例问题不会自动填入输入框。
- 导航入口主要是锚点或隐藏占位区，按钮没有接入后端。
- “PFS Report Engine”“示例状态”等信息仍是静态原型文案，不是实时模型和额度数据。
- 该页面目前不是 Flask 应用 `/` 路由实际渲染的模板；当前 `/` 默认渲染 `templates/agent_chat.html`，两套入口尚未合并。

结论：保留该文件作为可复用的视觉原型和交互参考，但在决定“它是新产品入口、落地页还是控制台壳”之前，不把它覆盖，也不把它描述成已接入 Agent。

## 5. 原项目能力地图

附件源码的主要结构如下：

| 能力域 | 现有源码证据 | 当前判断 |
|---|---|---|
| 服务入口 | `app.py`、`api/__init__.py`、Flask blueprints | 本地 Waitress、健康检查、首页和 PFS 垂直切片已真实回读 |
| Agent Loop | `agent/agent.py`，含迭代上限、运行时限、重试、压缩和工具事件 | DeepSeek 单任务真实通过；重试策略已有替身回归，失败切换和恢复待验证 |
| 数据接入 | Excel/CSV、DuckDB、SQLAlchemy、HTTP API、Feishu Bitable、跨源合并 | CSV/XLSX、HTTP fixture 和临时 PostgreSQL 已验证；其余连接和权限待验证；Google Sheets 已退役 |
| 查询与分析 | schema、SQL、派生分析表、数据 profile、清洗、14 类统计/机器学习分析 | 固定夹具和 DeepSeek 只读主链路已验证；真实业务结果待验证 |
| 图表 | 选择器、生成器和 41 个图表目录 | 41 个注册图表已用固定夹具生成冒烟；视觉和真实业务数据待验证 |
| 输出 | Excel、报告、PPT、Dashboard | 固定 fixture 已在统一桌面交付区生成；Office 结构解析、HTTP 下载和 Dashboard 浏览器打开通过，复杂内容、原生应用视觉和跨平台待验证 |
| 多模型 | OpenAI 兼容调用、DeepSeek/OpenAI/其他 provider 配置 | DeepSeek 已真实连通并完成单任务；provider 失败切换、跨 provider 成本统计待验证 |
| 实时交互 | Chat SSE、停止、提示建议、工具事件、图表 HTML | DeepSeek 单轮 SSE、停止、工具事件和 Token 记录已验证；长连接恢复待验证 |
| Sessions / Memory | 会话保存、历史、记忆读取与整理 | 会话和记忆代码存在；多轮、隔离和跨进程恢复待验证 |
| Skills / Commands | 28 个 skills 目录、17 个命令文档、斜杠命令 | 源码和文档存在，命令覆盖待验证 |
| MCP / Knowledge | MCP server 管理、工具发现、知识库分类/检索/临时提示 | 源码存在，外部接入和检索质量待验证 |
| Workspace | 挂载、读写、checkpoint、回滚、受限 bash facade | checkpoint 跨 FileHistory 实例读取、文件+会话恢复、只读恢复拒绝已有本地回归；挂载与其他工作区工具仍需完整验收 |
| Jobs / Workflows | 后台任务、DAG/工作流、审批、取消、恢复、重试、fork、调度器 | Job 存储重启收口和本地 Workflow 节点重试已验证；跨进程/跨服务恢复待验证 |
| Teams | 受限委派、成员工具上限、质量 reviewer | 源码存在，真实并发和成本待验证 |
| Hooks / Policy | HTTP/command hooks、确认标记、写操作门禁 | 源码存在，默认策略和 fail-closed 行为待验证 |
| Auth / Cloud / Feishu | 登录、云端 guard、GPU/桌面、Feishu bot | 源码存在，部署环境和数据合规待验证 |
| PFS 报表口径预览 | 固定 CSV fixture、上传 CSV/XLSX、Metric Contract、数据快照、Claim、Evidence、只读预览 API 和主界面入口 | 已实现第一段；CSV 本地 HTTP 已验证，XLSX 第一工作表 fixture 已本地真实执行，外部数据源仍待验证 |
| Evidence Ledger / Claim–Evidence | URL+片段稳定身份、批量幂等登记、Claim 关联、支持/反驳关系、冲突待裁决、原子 JSON 持久化和 HTTP API | 已实现第一段；多进程数据库适配和语义核验待实现 |
| 计算溯源与治理扩展 | 自动消费事件、成本/延迟对象、完整 lineage、审批队列和正式评估 | 本项目待实现 |

## 6. 新产品定位（暂定）

### 6.1 一句话定位

> 面向业务团队的可追踪报表分析工作台：从报表接入、指标理解和数据质量检查，到可复现计算、图表报告和结论审计，Agent 每一步都留下可核验的过程与依据。

这不是原项目的换皮，也不是只会生成文字报告的聊天机器人。它应该是“数据分析执行平台 + 受治理的 Agent 编排层”，报表分析是旗舰场景。

### 6.2 目标用户与用户旅程

优先用户：经营分析、财务/业务运营、市场增长、产品分析和需要定期产出报表的分析人员。

核心旅程：

1. 创建工作区，声明分析目标、周期、权限和预算。
2. 接入 Excel/CSV/数据库/API 等数据源，先看字段、样例、时间范围、缺失和重复。
3. 选择或确认指标口径，Agent 给出分析计划、所需数据和风险提示。
4. Agent 通过受限工具执行查询、清洗、统计分析和图表生成；前端实时显示阶段、耗时、成本、重试和错误。
5. 用户查看每个关键数字对应的 SQL/计算步骤、输入数据版本、指标定义和结果证据。
6. 对异常、冲突或高风险写操作进行人工确认；默认不让模型越过权限和预算。
7. 导出报表/图表/数据产物，或创建周期任务；后续运行支持对比、重跑、暂停、恢复和审计。

### 6.3 Shannon 参考能力与本项目业务增量

参考 Shannon 的是平台能力，不是其外在表面：任务编排、Agent Loop、工具调用、长任务、事件流、预算治理、重试/恢复、权限和人工控制、可观察性、API/SDK 等。参考实现时以能力契约和边界为准，不复制 Shannon 的品牌、页面、文案或架构图。

本项目的业务特色应集中在：

- 报表优先的数据源体验：上传、字段识别、周期对齐、表间关系和数据质量概览。
- Metric Contract：指标名称、公式、粒度、时间口径、过滤条件、负责人和版本。
- Calculation Lineage：每个结论可回到数据版本、SQL/代码、参数、图表和运行事件。
- Evidence Ledger：保存来源、快照/哈希、片段、采集时间和可信等级；不把“引用链接”当作完整证据。
- Claim–Evidence：对关键业务结论记录支持/反驳依据、置信度、核验状态和人工决策。
- 冲突与异常队列：口径不一致、数据突变、来源冲突和无法核验的结论必须显式进入待处理状态。
- 周期性报表治理：同口径重跑、版本对比、延迟/成本/失败率、审批和交付记录。

## 7. 非目标与边界

- 不把产品做成 Shannon 介绍页、教程页或原项目的品牌镜像。
- 不把“生成一篇报告”作为唯一核心；报告只是分析产物之一。
- 不在第一阶段同时支持所有行业、所有 BI 可视化和所有数据仓库。
- 不允许模型自行决定权限、预算、工具白名单、是否跳过审批或是否修改生产数据。
- 不把外部网页、文件、工具结果或用户上传内容当作可信系统指令；必须经过参数校验、工具隔离和策略门禁。
- 不因为代码目录、历史 changelog、静态 dist 或线上登录页存在，就宣称能力已在真实环境验收。
- 不删除第三方依赖的法定许可和归属信息；作者授权、第三方许可证和商业使用范围要分别记录。
- 不把初始化 Git、提交、推送、部署和线上验收合并为一个“完成”状态。

## 8. 分阶段改造路线

### Phase 0：授权、取证和仓库卫生

1. 保存用户所述授权范围的记录，明确是否覆盖衍生、再分发、商业使用、品牌/水印移除和原作者代码改造。
2. 盘点原作者许可证、第三方许可证、字体、图表库、静态 vendor、draw.io 等资源，形成 `NOTICE`/依赖清单。
3. 对原项目做品牌和耦合审计，不做全局盲目替换：产品文案、代码标识、服务名、安装器、更新检查、作者页脚、赞助链接、资源文件、生成 dist 分开处理。
4. 清理边界：排除 `secret_key`、SQLite WAL/SHM、缓存、构建临时文件、个人配置和密钥；改为环境变量或本机安全存储，并生成 `.env.example`。
5. 明确新的源码根目录和 Git 策略。初始审计时私有 GitHub 仓库为空且本地未初始化；当前状态已迁移到远端 `main`，现役结论以 `docs/HANDOFF.md` 为准。

### Phase 1：稳定运行基座

1. 取消生产启动时自动安装依赖的隐式行为，补齐可复现的依赖锁定、配置校验和启动错误提示。
2. 修复过期测试入口（例如质量命令引用不存在的 `Test/`），建立最小健康检查、API contract test 和前端 build gate。
3. 以固定 CSV/XLSX fixture 建立无真实模型的离线回归；把原项目历史测试数字改为“待重新测量”。
4. 建立能力清单，每一个原 API、工具、命令、skill、输出格式和页面入口都有唯一状态与验证记录。

### Phase 2：typed contract 与新产品壳

1. 定义 `Workspace`、`DataSource`、`MetricDefinition`、`AnalysisRequest`、`Plan`、`ToolCall`、`RunEvent`、`Artifact`、`Evidence`、`Cost`、`Approval`、`Error` 等 typed contract。
2. 将 Agent、数据源、模型、图表、导出和事件流抽成 adapter；先使用 mock transport，不等待 Docker 或真实 provider。
3. 决定父级 `index.html` 与附件 `templates/agent_chat.html` 的关系：只保留一个产品主入口，避免两套 UI 同时漂移。
4. 建立独立视觉系统和文案：品牌、导航、空状态、加载态、运行态、审批态、错误态、完成态、来源/口径显示都重新设计。

### Phase 3：按垂直切片恢复原功能

建议顺序：

1. CSV/XLSX → schema/profile → 自然语言问题 → SQL/计算 → 图表 → 结果解释。
2. 数据预览、质量检查、字段筛选、清洗和派生表。
3. DuckDB/SQL 数据源与跨源查询；再接入 HTTP API 和 Feishu 等外部源。Google Sheets 不再属于产品范围。
4. 高级分析：回归、聚类、分类、时间序列、漏斗、分层/筛选等，每类都配固定输入、期望输出和失败样例。
5. Excel/Word/PPT/Dashboard 导出，并验证产物内容、图表可读性和数据口径。
6. SSE、停止、后台任务、会话历史、工作区和恢复。
7. Skills、命令、知识库、MCP、团队/工作流/审批/调度等扩展能力。

每个切片都必须同时完成：typed contract、mock 测试、真实 fixture 测试、错误态、权限边界、前端回读和文档状态更新。

### Phase 4：报表信任层

1. Metric Contract 和指标字典：统一名称、公式、粒度、时间口径、过滤和版本。
2. Evidence Ledger 和 Calculation Lineage：记录来源、数据版本、SQL/代码、参数、运行 ID、产物哈希和关键片段。
3. Claim–Evidence 关联：支持/反驳、置信度、核验理由、人工决定和待处理状态。
4. 冲突、异常和不可核验结论进入队列，不静默选择一个答案。
5. 将成本、延迟、错误、重试、恢复、审批、人工改动和交付结果做成可查询对象。

### Phase 5：可靠性与生产化

1. Agent Loop 设置最大步数、最大时长、Token/费用预算、工具 allowlist、参数 schema、统一错误边界和连续失败熔断。
2. 对副作用操作提供幂等键、read-before-write、确认和审计；默认只读。
3. 对长任务提供 checkpoint、pause/resume、cancel、retry、重启恢复和不重复执行保证。
4. 接入结构化日志、Prometheus/OpenTelemetry、事件回放和故障排查页面。
5. 在离线/本地门禁通过后，再验证 Docker、PostgreSQL、Redis、Temporal、Gateway、真实模型、部署、域名、线上页面和真实用户流程。

### Phase 6：发布与验收

- 产品文档、API/SDK 文档、迁移说明、许可证/NOTICE、隐私与用户协议全部独立重写。
- 做品牌残留扫描、依赖许可证扫描、secret 扫描、无障碍和视觉回归。
- 用固定任务集真实产生指标：成功率、Claim 覆盖率、citation precision/recall、entailment、冲突识别、恢复成功率、成本、P50/P95 延迟。
- 分别记录 `local`、`commit`、`push`、`deploy`、`live`，每一层都必须有独立证据；任何一层没有做，就保持未完成。

## 9. 第一版最小可运行目标

第一版不追求一次搬完所有模块，而是完成一个能被核验的闭环：

1. 本地打开独立产品 UI，选择固定 CSV/XLSX fixture。
2. 展示数据 schema、行数、时间范围、缺失/重复和指标口径提示。
3. 用户提出一个报表问题，Agent 使用 mock 或可配置 provider 生成受限分析计划。
4. 经策略校验后执行确定性 SQL/分析代码，生成至少一张图和一项关键结论。
5. 结果页面能回看输入数据版本、计算步骤、运行事件、成本/耗时占位和证据/口径状态。
6. 失败、取消、预算超限和人工确认都有真实 UI 状态。
7. 固定 fixture 的 contract、单元、集成和视觉检查可在不开 Docker 时运行。

完成这个闭环后，再把原项目能力按矩阵逐项迁入，而不是先把全部源码改名后再寻找验证方法。

## 10. 当前状态总表

### 2026-08-29 本地 Agent 垂直切片补充

新增 `tests/test_pfs_agent_vertical_slice.py`，固定 PFS CSV 直连 Agent，验证 schema、只读查询、派生表、图表选择/HTML、数据概况，以及写入 SQL、错误字段和未知表的失败闭环。为防止内部调用绕过模型入口，在 `agent/tools/business/data.py` 的数据工具实现边界补加统一只读 SQL 校验。垂直切片 2/2 通过，最新完整 Python 回归为 69 个通过、1 个可选 Torch 测试跳过；不等于真实模型、外部数据源、导出、视觉或线上验收。

| 范围 | 状态 | 证据/边界 |
|---|---|---|
| 父级 `index.html` 静态页面 | 已实现（静态原型） | 页面和 CSS 存在；未接后端、无脚本状态 |
| 附件原项目源码 | 已存在（源码快照） | 关键文件与远端 `main` 抽查一致；没有本地 Git 历史 |
| 原项目后端与 Agent 能力 | 局部已验证 | PFS 垂直切片和临时 PostgreSQL SQL 数据源已通过真实本地验证；其余原路由、工具和模块仍未完成全链路验收 |
| 本地 Python 语法 | 已验证（离线） | 293 个 Python 文件 AST 解析通过 |
| 图表选择器与注册图表生成 | 已验证（局部） | 选择器既有测试加 41 图表逐项固定夹具冒烟通过；不代表视觉、真实报表或导出验收 |
| 前端 JS 语法与 bundle | 已验证（离线） | `frontend/` 检查、`pnpm lint`、`pnpm build` 和聊天 bundle 校验通过 |
| 独立品牌与新 UI | 已实现第一段 | PFS 图标、产品身份、服务名、模板、静态原型和报表预览入口已迁移；MCP、知识库和工作目录模块已切换为 PFS 主命名空间并保留旧入口兼容；真实工作台侧栏交互已完成一组移动端验收；本轮又完成浏览器 CSV 上传到会话数据源的本地回读；父级静态原型仍未接后端 |
| typed data/agent contract | 已实现第一段 | `pfs_agent/` 已有工具契约和策略门，已接入只读/计算工具的 Agent 预分发检查 |
| 报表口径、证据链、结论核验 | 已实现第一段 | `reporting.py`、`ledger.py` 和 `/api/pfs/ledger` 已覆盖 fixture/上传 CSV/XLSX 结果、快照哈希、Claim/Evidence、幂等、冲突和本地持久化契约；XLSX 真实依赖执行、语义核验待实现 |
| 真实工作台移动侧栏交互 | 已有历史验收；不再扩展 | 390×844 浏览器验收覆盖面板互斥、异步过期请求保护、Escape、Agent 导航、两个抽屉和 backdrop；手机端已移出当前交付范围，不再要求完整移动端分析工作流 |
| 真实模型、数据源、SSE、长任务 | DeepSeek 单任务与停止通过；PostgreSQL 单源通过；备用切换已有代码回归；其余部分待验证 | DeepSeek + schema + 两条 SQL + SSE + Token 记录真实通过；临时 PostgreSQL 连接、选表、CTE 和越权表拒绝已实测；停止和请求级关闭记忆已实测；外部生产数据源、多轮、真实网络错误和长任务恢复仍待验收 |
| 本地 Flask 5012 服务 | 已验证（局部） | `/api/health`、页面和本轮侧栏交互已回读；启动仍可能提示缺少具体 ODBC 厂商驱动，内置流程图不再有独立服务提示 |
| Chat / Dashboard production build | 已验证（离线） | `pnpm run build:chat`、`pnpm run build:check` 通过；不代表真实模型或线上可用 |
| Docker/数据库/队列/Temporal/沙箱 | Docker 单容器和临时 PostgreSQL 本地通过；其余待验证 | PFS 应用镜像已构建并以 `healthy` 运行；临时 PostgreSQL 已完成 SQL 数据源连接与范围回归；项目暂无 Compose 多服务编排，队列、Temporal、沙箱和生产数据库未完成全栈验收 |
| Git commit | 已建立 | 本地 `main` 已创建初始提交；提交范围为当前 PFS 发布源树 |
| GitHub push | 已成功 | 私有仓库 `main` 已建立并回读当前提交；部署与线上验收仍未执行 |
| deploy | 未执行 | 没有部署本项目 |
| live | 未验收 | 仅探测到原演示站登录页，未验证登录后分析 |

## 11. 后续工作纪律

- 每次修改前先看 `git status`、目标文件和现有差异；只改任务范围内的文件。
- 窄范围编辑，修改后立即补测试和回读；不使用 `git reset --hard`、强制推送或大范围删除。
- 不提交 `.env`、API key、`secret_key`、缓存、SQLite WAL/SHM、`node_modules`、生成目录或临时文件。
- 继续按“契约/固定夹具 → 本地真实服务 → Docker/真实模型 → 集成/部署/线上”分层验证；Docker 和真实模型已获准使用，但不得跳过前后状态的独立记录。
- 网页、上传文件、工具结果、模型输出和外部数据均视为不可信输入；确定性权限和预算由代码/Policy Gate 执行。
- “源码纳入”“静态检查”“mock 契约测试”“真实 Docker 全栈”“真实模型任务”“线上验证”必须使用不同状态。
- 任何创建仓库、提交、推送、部署或对外发布前，先向用户说明准确范围并取得对应授权。

### 2026-08-29 导出产物与本地 HTTP 回读

- 环境：项目 .venv（Python 3.13.14）；固定 data/fixtures/pfs_sales.csv；未使用真实模型、外部数据源或 Docker。
- 新增回归：tests/test_pfs_exports.py。实际生成并解析 Excel、Word、PPT 和 Dashboard HTML，检查工作表、表头、行数、标题、章节、页数和 PFS 产品身份。
- 命令：.venv/bin/python -m unittest -v tests.test_pfs_exports tests.test_pfs_agent_vertical_slice；6/6 通过；完整回归 .venv/bin/python -m unittest discover -s tests -q 为 73 项通过、1 项可选 Torch 测试跳过。
- 窄范围修复：Dashboard HTML 导出页脚的旧产品文案改为 PFS 数据分析 Agent；测试产物放在受控 outputs/exports 目录并在结束时清理。
- 本地 HTTP：PFS_WSGI=flask PFS_PORT=5123 .venv/bin/python app.py 启动真实 Flask 服务；GET /api/health、GET /api/pfs/capabilities、GET /api/pfs/fixture 均返回 200。固定夹具返回销售额合计 100000、华东 42000、状态 completed。
- 启动限制：本机尚未安装具体数据库厂商 ODBC 驱动；内置 draw.io/diagram 链路的本地回归属于历史记录，该能力现已退役，真实 MCP 服务连接仍未验收。
- 边界：这次证明 PFS 的本地确定性 API 和四类导出产物可运行，不证明真实模型、外部连接器、SSE 长任务、重启恢复、Docker 全栈、浏览器视觉、部署或线上验收。

### 2026-08-29 工作区元数据身份迁移

- 窄范围修改：新增 `workspace_metadata_dir()`，新工作区的元数据、DuckDB、记忆、知识库和工作流存储以 `.pfs/` 为主目录。
- 兼容边界：检测到已有 `.zhixi/` 时继续原地读取和写入，不自动搬移、删除或覆盖旧历史；因此旧目录名称仍会出现在兼容代码和数据扫描黑名单中。
- 历史运行时迁移：draw.io 缓存刷新参数曾从旧键迁移为 `pfs_reload`；该前端链路现已退役。浏览器高频键、模型缓存、桌面配置和工作区路径的上一轮迁移测试仍通过。
- 验证：`tests.test_pfs_identity_migration` 19 项通过；相关 Python 模块 `py_compile` 通过；`node --check` 和 `pnpm run build:check` 通过。
- 未完成：仍有少量 BAA 兼容环境变量/存储键、legacy 文案、原作者版权头和 `.zhixi` 兼容说明待分类；不能写成内部命名已清零，也未进行 Git、push、deploy 或 live 验收。
- 后续回归：完整 `unittest discover` 为 78 项通过、1 项可选 Torch 测试跳过；旧路径定向复查未发现新的运行时 `.zhixi` 直接存储路径或 `_baa_query_count` 计数别名；`pnpm run build:check` 再次通过。测试中的统计模型告警来自固定夹具的秩亏/非平稳输入，不影响测试退出状态，但不应被当作统计质量验收。

### 2026-08-29 数据源适配器专项回归

- 环境：项目 `.venv`（Python 3.13）；固定 `data/fixtures/pfs_sales.csv`；临时生成双工作表 XLSX；临时启动 `127.0.0.1` HTTP fixture server；不含真实模型、外部账号或 Docker。
- 新增回归：`tests/test_pfs_data_sources.py`。
- 覆盖：CSV 加载、字段和行数、只读聚合查询、预览；Excel 双工作表加载、工作表集合、工作簿顺序预览和查询；HTTP JSON/CSV 加载、查询、预览、Bearer 请求头；空响应和 HTTP 500 失败。
- 命令：`.venv/bin/python -m unittest tests.test_pfs_data_sources -v`。
- 结果：4/4 通过。
- 结论：CSV、Excel 和 HTTP 适配器在固定本地输入/本地替身服务下达到“本地真实通过”；这不代表外部 HTTP、SQL 数据库、飞书或浏览器上传全链路完成。Google Sheets 后续已按范围决策退役。Excel 的 `list_tables()` 按 DuckDB 表名排序，工作簿原始顺序由 `get_preview()` 保留，测试已按实际公共契约断言。

### 2026-08-29 分析结果数值契约回归

- 环境：项目 `.venv`（Python 3.13）；使用固定、人工可计算的 pandas 夹具；不含真实模型、外部数据源或 Docker。
- 新增回归：`tests/test_pfs_analysis_accuracy.py`。
- 覆盖：Regression 在完全线性数据上的 R²、RMSE、系数和残差行数；AB Test 两组均值、绝对/相对提升、p 值结论和质量检查；Data Decile 分桶数、总和、样本量和累计占比；Univariate Screening 对显著线性变量、常量变量和摘要计数的处理。
- 命令：`.venv/bin/python -m unittest tests.test_pfs_analysis_accuracy -v`；结果 4/4 通过。
- 联合回归：`.venv/bin/python -m unittest discover -s tests -q`；结果 87 项通过、1 项可选 Torch 测试跳过。
- 结论：4 类分析从“只验证返回表结构”推进到固定夹具下的核心数值契约验证；其余 10 类仍仅有结构级冒烟或依赖缺失，不能据此宣称 14 类统计分析全部完成。回归过程中出现的 statsmodels 秩亏/非平稳警告已保留为测试信号，不等于统计质量验收通过。

### 2026-08-29 分类、聚类与时间序列结果级回归

- 环境：项目 `.venv`（Python 3.13）；固定分类、聚类和 60 行日频时序 fixture；不含真实模型、真实外部数据源或 Docker。
- 命令：`.venv/bin/python -m unittest tests.test_pfs_analysis_accuracy -v`，专项 6 项通过；随后 `.venv/bin/python -m unittest discover -s tests -q`，完整回归 89 项通过、1 项可选 Torch 测试跳过。
- 新增覆盖：Decision Tree 的重要性/混淆矩阵/ROC 范围；Logistic Regression 的系数有限性、测试样本守恒和 AUC 范围；K-Means 的簇数、样本数与占比守恒、惯性及轮廓系数范围；ARIMA、SARIMA、VAR、Prophet、GRU 的预测步数、未来时间方向、有限预测值和指标表。
- ARIMA 已补充自动模式回归：当 auto_arima 选出的阶数在最终拟合阶段触发数值错误时，会在有限候选阶数中重新拟合并按 AIC 选择可用模型；若候选均失败则抛出清晰错误。该回退已在趋势序列上本地真实通过，但不等于所有病态数据和真实业务预测质量均已验收。
- 边界：结果级固定夹具验证不等于统计方法全面正确、真实业务报表验收、长序列性能验收或生产预测质量验收；statsmodels 仍可能产生秩亏/非平稳输入告警。

### 2026-08-29 Sklearn 通用建模结果与输入边界回归

- 修改：Function/Analyze/Sklearn_Model/analyze.py、tests/test_pfs_analysis_accuracy.py。
- 修复：随机森林默认入口根据目标列实际形态区分分类与回归；连续数值目标不再被误当成大量类别。
- 新增覆盖：连续目标随机森林的回归指标、特征重要性占比、逐样本残差；空数据、目标缺失、单类别分类、非数值回归目标和聚类样本不足的清晰拒绝。
- 命令：.venv/bin/python -m unittest tests.test_pfs_analysis_accuracy -v；9 项通过；随后 .venv/bin/python -m unittest discover -s tests -q；94 项通过、1 项可选 Torch 测试跳过；.venv/bin/ruff check ... 通过。
- 边界：仍未完成多分类、类别不平衡、真实业务特征组合、模型稳定性和生产级预测质量验收。

### 2026-08-29 Sklearn 多分类与不均衡回归

- 修改：`Function/Analyze/Sklearn_Model/analyze.py`、`tests/test_pfs_analysis_accuracy.py`。
- 改进：分类评估对未预测类别使用明确的零除处理；混淆矩阵固定输出全部已知类别；样本量允许时采用分层拆分，减少类别不均衡导致的评估偏差。
- 新增覆盖：20 行三分类不均衡 fixture、缺失特征自动填补、accuracy/precision_macro/recall_macro/f1_macro 有限性、3×3 混淆矩阵类别完整性和特征重要性占比守恒。
- 命令：`.venv/bin/python -m unittest tests.test_pfs_analysis_accuracy -v`；`.venv/bin/ruff check Function/Analyze/Sklearn_Model/analyze.py tests/test_pfs_analysis_accuracy.py`。
- 结果：专项 10 项通过，Ruff 通过；未使用真实模型、真实业务报表、Docker 或外部数据源。
- 边界：多分类与不均衡已获得固定 fixture 的本地真实通过；仍需真实业务特征、缺失模式、极小样本、收敛与预测质量验收。

### 2026-08-30 上传到 PFS 报表预览的 HTTP 垂直切片

- 新增：`tests/test_pfs_http_vertical_slice.py`。
- 覆盖：通过 Flask 测试客户端上传 UTF-8 CSV → 列出当前会话可分析数据源 → 提交明确的指标/日期/分组口径 → 返回分组汇总、数据快照、Claim 和 Evidence；同时验证未知指标列返回 400，不能绕过列校验。
- 命令：`.venv/bin/python -m unittest tests.test_pfs_http_vertical_slice -v`；`.venv/bin/ruff check tests/test_pfs_http_vertical_slice.py`。
- 结果：HTTP 垂直切片 2 项通过，Ruff 通过；固定本地上传文件、无真实模型、无外部数据源、无 Docker。
- 边界：已证明 Flask 上传接口与 PFS 确定性分析接口可以串联；受限自然语言问题路由另有 HTTP 用例通过。浏览器真实点击流程、XLSX 浏览器完整分析、通用聊天 Agent 自然语言触发、导出、SSE/长任务和线上环境仍未验收。

### 2026-08-30 利润指标问句与错误边界

- 窄范围修改：受限自然语言报表解析现在可从 `profit_amount`、`profit`、利润、毛利或净利列中选择利润指标；收入和销售额继续按各自别名匹配。
- 明确边界：利润率/margin 尚未实现，系统会明确拒绝并提示先提问利润或销售额合计，不把利润率冒充为利润。
- 接口修复：`/api/session/<sid>/pfs/query` 对问句无法安全解释时统一返回 `pfs_query_failed` 和 HTTP 400；不再泄漏异常类名。
- 新增真实回归：上传包含销售额与利润的 CSV，验证利润合计 1090、华东分组利润 750，以及利润率拒绝路径；`tests.test_pfs_http_vertical_slice` 共 10 项通过。
- 边界：这只覆盖确定性报表路径中的利润合计，不代表利润率、同比/环比、毛利率、复杂公式、真实模型或线上环境已实现。

### 2026-08-30 数据清洗工具本地复验

- 环境：项目 `.venv`（Python 3.13）；临时 UTF-8 CSV；不含真实模型、外部数据源或 Docker。
- 覆盖：`clean_data` 的 `fill_na`、`winsorize`、`trimming` 三种操作；补缺失值结果实际写入 `cleaned_data`，缩尾和截尾返回预期数值/行数；非法操作返回清晰错误。
- 数据保护：回读证明原始 `quality` 表仍保留缺失值，清洗结果作为派生表保存，没有覆盖源表；非法操作前后表集合不变。
- 命令：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest tests.test_pfs_agent_vertical_slice -v`；3 项通过。
- 边界：这是固定小数据的本地真实工具复验，不代表重复值、异常数据组合、后台大数据量清洗、审批工作流或真实业务数据已经验收。

### 2026-08-30 Docker 与真实模型验证条件升级

- 用户已取消“不开 Docker、不开真实模型”的限制；后续可以启动容器、数据库、外部依赖和真实模型，但仍需分别记录服务启动、全栈集成、真实模型任务、部署和线上验收。
- Docker 环境：Docker Desktop 29.7.2 与 Compose v5.3.1 已可用。构建前发现 `.dockerignore` 未排除 `secret_key`、模型配置、SQLite/DB 文件和原项目参考快照；现已补齐规则并增加发布安全测试，专项 6 项通过。
- Docker 构建：前两次拉取 `python:3.11-slim` 时遇到 Docker Hub OAuth token 网络超时；网络恢复后基础镜像拉取成功，`docker build -t pfs-data-analysis-agent:local .` 完整通过。镜像约 529 MB，直接检查确认不含本地 `secret_key`、模型配置、数据源配置、数据库、运行产物和 `Data-Analysis-Agent-main/` 参考快照。
- 容器验收：镜像启动后 `/api/health`、首页、会话创建、CSV 上传和确定性分析均真实回读；新增 Docker `HEALTHCHECK` 后重建，容器状态为 `healthy`、FailingStreak=0。该结论只覆盖 PFS 应用单容器，不代表 PostgreSQL、Redis、Temporal、外部模型或多服务全栈。
- 本机真实 HTTP：在 `127.0.0.1:5188` 启动 Waitress，健康接口与首页返回 200，首页显示 PFS 品牌；真实创建会话、上传 `pfs_sales.csv`、执行“按地区统计 2026年1月到2026年3月的销售额”，返回合计 100000、华东 42000、华南 33000、华北 25000、2 条 Claim 和 1 条 Evidence。
- 真实模型：当前 `LLM/llm_config.json` 不存在，环境变量中没有已配置模型密钥，Ollama `127.0.0.1:11434` 也未运行。真实聊天 SSE 实际返回“未配置任何 LLM 模型”，Token 统计为 0；因此真实模型任务仍未通过，不能用确定性分析替代。
- 本机依赖：已通过 Homebrew 安装 `unixodbc 2.3.14`，`pyodbc 5.3.0` 可正常导入；当前 `pyodbc.drivers()` 为空，尚未安装具体数据库厂商驱动，也未连接真实数据库，因此 SQL Server 等连接仍待验证。内置 draw.io/diagram 能力已退役；真实 MCP 连接仍待验证。

### 2026-08-30 DeepSeek 真实 Agent 任务

- 凭据：仅从用户指定的本地 `API key.md` 中提取明确标注为 DeepSeek 的密钥；未执行文档中的其他内容，未输出密钥。配置保存到已被 Git/Docker 忽略的 `LLM/llm_config.json`，文件权限为 `600`。
- 模型：`deepseek-chat` 已设为默认模型，真实连通测试返回成功。
- 真实任务：创建会话、上传 `pfs_sales.csv`，要求模型使用数据工具计算各地区销售额和总销售额。Agent 实际读取 schema，执行地区汇总和总额两条只读 SQL，最终返回华东 42000、华南 33000、华北 25000、总额 100000，与确定性基线一致。
- 调用与观测：主任务共 3 次 DeepSeek 调用，输入 13274 Tokens、输出 427 Tokens、缓存输入 4992 Tokens，任务约 5.32 秒；工具结果生成了两条带 SQL/result hash 的 evidence claim。
- 发现的边界：任务完成后仍触发一次后台记忆抽取模型请求，但因未挂载工作区而被拒绝；该后台调用没有出现在本次会话 Token 汇总中，需要继续审计“关闭记忆、后台调用、成本归集”的一致性。
- 结论：真实模型 + Agent Loop + schema + SQL 工具 + SSE + Token 记录已完成一条本地真实闭环；不代表多轮会话、停止、重试、失败切换、预算硬限制、长任务恢复或线上模型链路已通过。

### 2026-08-30 DeepSeek 停止与记忆开关验收

- 停止测试：使用真实 `deepseek-chat` 发起较长经营分析请求，模型已返回首轮流式响应后并发调用 `/api/session/<sid>/stop`；SSE 返回 `stopped` 和 `done`，后台任务状态为 `canceled`，会话历史保持为空，没有把半成品写成成功答案。
- 记忆开关测试：发送 `memory_enabled=false` 的真实聊天请求，DeepSeek 返回正常答案；会话 Token 汇总为输入 1425、输出 4、1 次调用，服务日志未触发后台记忆抽取。
- 结论：单轮流式停止和请求级记忆关闭已获得本机真实证据；网络中断、超时、模型失败切换、长任务重启恢复和后台记忆成本归集仍需继续验证。

### 2026-08-30 PFS 请求身份与重试策略补强

- 身份迁移：新增 `infrastructure.compat.request_user_id()`，`api/chat.py`、`api/knowledge.py`、`api/memory.py` 和 `api/workflow_runs.py` 统一读取 `X-PFS-User-ID`；`X-BAA-User-ID` 仅保留为旧客户端的只读回退，不能覆盖 PFS 请求头。
- 重试回归：新增 `tests/test_pfs_retry_policy.py`，验证 503 按 3 秒、6 秒指数退避重试，401 立即失败，上下文超限不被误当成网络错误重试。
- 验证：`.venv/bin/python -m unittest tests.test_pfs_retry_policy tests.test_pfs_identity_migration -v`，34/34 通过；`.venv/bin/ruff check agent/retry.py api/workflow_runs.py infrastructure/compat.py tests/test_pfs_retry_policy.py tests/test_pfs_identity_migration.py` 通过。
- 边界：这次证明了身份优先级和重试策略的代码契约；没有伪造真实 503 网络故障，真实多 provider 切换、流式中断重连、长任务重启恢复和多用户集成仍未完成。

### 2026-08-30 Agent 备用模型切换路径

- 实现：`agent/agent.py` 在当前 provider 的初始流式请求耗尽重试后，针对网络/服务/认证类失败调用 `get_llm_client_with_fallback()`，排除已失败 provider，重建模型限制和 provider 专属缓存参数，再尝试备用模型；上下文超限和格式类请求错误不会盲目切换。
- 配置：`LLM/llm_config_manager.py` 增加 `excluded_providers`，避免备用选择再次返回刚失败的 provider。
- 回归：`tests/test_pfs_retry_policy.py` 使用替身客户端验证主模型 503 → 备用模型成功、DeepSeek 缓存参数不会泄漏到 OpenAI 请求、401 可切换、上下文超限不切换。
- 验证：`.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -q`，119 项通过、1 项可选测试跳过；`.venv/bin/ruff check . --exclude Data-Analysis-Agent-main --exclude .venv --exclude node_modules --exclude static/dist` 通过；目标文件 `py_compile` 通过。
- 边界：当前本机只配置了 DeepSeek，因此没有真实第二供应商可供故障切换；流式响应已经产生部分内容后的网络中断、跨 provider 真实费用和长任务恢复仍待验收。

### 2026-08-30 DeepSeek 回归复验（备用切换改动后）

- 环境：`PFS_WSGI=waitress PFS_PORT=5193 .venv/bin/python app.py`；真实 DeepSeek `deepseek-chat`；固定 `data/fixtures/pfs_sales.csv`；请求关闭记忆，不使用外部数据库。
- 请求：新建会话 → 上传 CSV → 选择 DeepSeek → 通过 `POST /api/session/<sid>/chat` 要求读取 schema、按地区汇总销售额并核对总额。
- 回读：SSE 正常返回 `get_schema`、两次 `query_data`、3 次模型调用和 `done`；地区结果为华东 42,000、华南 33,000、华北 25,000，总销售额 100,000；任务耗时约 4.59 秒，输入 12,671 Tokens、输出 299 Tokens。
- 结论：备用模型切换代码接入后，既有 DeepSeek 主链路仍可运行；本次仍没有真实故障注入，也没有第二个真实 provider。

### 2026-08-30 Job 存储重启恢复回归

- 实现边界：没有把“工作流源码存在”当作恢复完成；针对现有 `data/jobs_store.py` 的启动恢复逻辑新增 `tests/test_pfs_reliability.py`。
- 场景：在临时 SQLite 中创建并推进一个 running 任务、一个 canceling 任务，关闭存储连接后重新打开，模拟应用重启。
- 回读：running 任务收口为 `failed` 并写入 `Application restarted before the job completed.`；canceling 任务收口为 `canceled`；两者均有 `finished_at`，并在会话事件流中分别留下 `job_error` / `job_canceled` 恢复事件。
- 验证：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest tests.test_pfs_reliability tests.test_pfs_retry_policy -v`，7/7 通过；完整回归 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py' -q`，122 项通过、1 项可选测试跳过；Ruff 和 `pnpm run build:check` 通过。
- 边界：这证明了本地 Job 状态存储在重启时的收口和事件留痕，不证明 WorkflowScheduler 跨进程恢复、长任务自动续跑、Temporal/Redis/PostgreSQL、多服务故障恢复或线上恢复已完成。

### 2026-08-30 Workflow 节点失败重试回归

- 场景：使用真实 SQLite `WorkflowStore` / `WorkflowRunStore`、真实 `WorkflowScheduler` 和确定性本地任务替身；第 1 次节点执行故意失败，第 2 次返回合法结果。
- 回读：第一次失败后持久化 `failed` 节点并创建 attempt=2；第二次成功后 Run 进入 `succeeded`，最终输出为 `answer=retry succeeded`，事件流包含 `workflow_node_retry_created`。
- 验证：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest tests.test_pfs_reliability -v`，2/2 通过；本轮完整回归为 122 项通过、无跳过。
- 边界：这是本地 WorkflowScheduler 的节点级自动重试闭环，不是跨进程/跨服务重启恢复；真实长任务、暂停恢复、审批、Temporal/Redis/PostgreSQL 和线上故障恢复仍未完成。

### 2026-08-30 Torch MLP 依赖与分析回归

- 环境：项目 `.venv`（Python 3.13.14，arm64 macOS），通过 `uv pip install --python .venv/bin/python -r requirements-dl.txt` 安装 PyTorch 2.13.0；检测到 MPS 可用。
- 验证：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest tests.test_analysis_modules -v`，2/2 通过；14 类分析统一冒烟不再跳过 Torch MLP；完整回归为 122 项通过、无跳过。
- 边界：这是固定夹具下的接口和输出形状验证，不代表 MLP 的业务预测质量、训练稳定性、GPU/CPU 跨平台一致性或生产规模性能已经验收。

### 2026-08-30 PostgreSQL 数据源与 SQL 范围回归

- 环境：临时 Docker `postgres:16-alpine`，项目 `.venv`（SQLAlchemy、psycopg2、DuckDB）；创建 `sales_report` 和未授权的 `private_notes` 表后，通过 Flask `/api/session/<sid>/connect-db` 连接。
- 验证：调用分析表范围设置和 `/preview`，显式选择 `sales_report` 后读取 schema、执行地区销售额汇总和 CTE；真实连接返回华东 42,000、华南 33,000、华北 25,000。
- 安全修复：SQL 表引用识别现在保留未知/系统表引用；`pg_catalog.pg_tables`、未选择表和无法解析表引用会在远端执行前被拒绝，避免越过选表范围。
- 回归：`tests.test_sql_source_scope` 2/2 通过；Flask API 连接/选表/预览回读通过；完整 `unittest discover` 为 124 项通过、无跳过；相关 Ruff 检查通过。临时容器已删除。
- 边界：这证明了临时 PostgreSQL 的连接和选表范围，不代表 SQL Server 厂商驱动、生产数据库权限/网络、连接池压力、浏览器端到端或线上部署已完成。

### 2026-08-31 PFS 同步报表分析取消回归

- 实现：新增会话作用域的内存运行登记表；上传 CSV/XLSX 的显式口径分析和受限问句分析都会使用唯一 Run ID，并在读取、解释、计算和治理登记之间检查取消状态。前端运行中显示“取消分析”，同时中止浏览器等待并请求服务端取消。
- 并发验证：测试线程在 `analyze_file` 内等待，另一客户端请求 `/api/session/<sid>/pfs/runs/<run_id>/cancel`；接口返回 202，原分析最终返回 409 / `pfs_analysis_canceled`，`_governance_result` 未被调用。不存在的运行返回 `pfs_analysis_run_not_active`。
- 质量门：取消专项与相关 HTTP/UI 共 31 项通过；完整 Python 回归 167 项通过、无跳过；Ruff、`git diff --check`、`pnpm run build:chat` 和 `pnpm run build:check` 均通过。
- 边界：这是单 Python 进程内的协作式取消。它会在分析阶段间阻断后续处理和 Claim/Evidence 登记，但不能强制打断 pandas 正在运行的单个函数，也未覆盖多进程共享状态、重启恢复或分布式队列。

#### 真实桌面补充验收

- 环境：本地 Waitress `127.0.0.1:5012`；Ego Browser 隔离空间；桌面视口 1200×900；临时生成并通过页面上传 54,732,526 字节、2,500,000 行 CSV。
- 首次发现：取消按钮可早于服务端 Run 登记出现，第一次请求可能返回 404 后继续运行；并且按钮的 `hidden=true` 被通用按钮 CSS 覆盖，取消完成后视觉上仍残留禁用按钮。
- 修复：取消请求对“Run 尚未激活”的 404 做 0/50/100/200/400/800 ms 有界重试；其他错误不重试。按钮同时使用 DOM `hidden` 属性和项目 `.hidden` 类，避免 CSS 覆盖。
- 最终回读：运行约 6.2 秒后取消按钮出现，点击后约 1.2 秒进入“已取消”；正文显示“分析已取消，未生成结论或证据记录”，JSON/CSV 和四类交付均禁用，取消按钮 `display:none`。从浏览器资源记录取回 Run ID 后查询 Ledger，Claims、Evidence 和 pending conflicts 均为空。
- 边界：本次是本机单进程真实桌面验收；测试上传副本和临时 CSV 未在本轮删除，未验证多进程、重启恢复、分布式状态或部署环境取消。

### 2026-08-31 日期异常与超限上传桌面验收

- 环境：本地 Waitress `localhost:5012`；Ego Browser 隔离空间；桌面视口 1200×717。
- 日期异常：真实上传含 `2026-02-30` 的 CSV。首次验收发现 `/pfs/sources` 在预读日期失败后会静默跳过整份文件，且错误建议显示内部翻译键。修复后，文件保留在选择器并标记“需修复”；运行返回 `source_date_invalid`，页面显示中文 YYYY-MM / YYYY-MM-DD 修复建议，JSON、CSV 和四类交付均禁用。
- 超限上传：真实选择 104,857,601 字节 CSV 并点击上传；弹窗显示单文件 100 MB 上限和压缩/拆分建议，上传按钮恢复可重试状态。服务端上传目录回读未发现该超限文件。
- 质量证据：新增 HTTP 回归确认异常日期数据源仍返回字段和行数、携带 `validation_error`，实际分析仍按稳定错误码拒绝；中英文结构化错误词条补齐并由 UI 契约测试覆盖。
- 截图：`/tmp/pfs-error-qa.vCxRwy/pfs-invalid-date-fixed.png`、`/tmp/pfs-error-qa.vCxRwy/pfs-too-large-final.png`。临时输入和合法上传副本未在本轮清理。
- 边界：缺字段、模型未配置和交付失败尚未完成真实桌面回读；本次不代表部署或线上验收。

### 2026-08-31 剩余桌面错误态验收

- 隔离环境：本地 Waitress `localhost:5013`，空白 `PFS_DATA_DIR`，关闭自动清理，不读取或修改现役 DeepSeek 配置；Ego Browser 隔离空间，桌面视口 1200×717。
- 缺字段：上传仅含 `month / region / orders` 的 CSV，以“按地区统计销售额”运行受限问句。修复前所有解析错误会被覆盖成 `pfs_query_failed`；修复后无匹配指标列保留 `source_columns_missing`，页面同时解释缺少指标字段并提示核对指标/日期/分组列，下载和四类交付禁用。
- 模型未配置：空白模型配置下从真实聊天输入发送请求，SSE 返回 `model_not_configured`；页面显示“未配置任何 LLM 模型”以及打开模型设置、配置 DeepSeek 或其他模型的建议，流正常结束，没有内部错误码泄漏。
- 交付失败：先完成固定报表，再临时将隔离输出目录切为只读并点击 Excel。意外导出异常现在统一包装为 `delivery_generation_failed`；页面显示输出目录权限建议，四类按钮恢复可重试，无 Artifact 产生。验收后恢复隔离目录权限。
- 视觉与安全修复：长交付错误曾将标题挤成竖排并产生横向截断；交付头改为单列网格，错误状态允许任意位置换行。意外异常详细内容只写服务端日志，页面不再暴露 `/private/tmp/...` 等绝对路径。最终 DOM 回读标题 462×17、错误状态 462×31，弹窗 `scrollWidth <= clientWidth`。
- 截图：`/tmp/pfs-isolated-errors.xQXBci/missing-field.png`、`/tmp/pfs-isolated-errors.xQXBci/model-not-configured.png`、`/tmp/pfs-isolated-errors.xQXBci/delivery-failure-final.png`。
- 边界：以上是本地隔离错误注入，不是现役模型配置失效，也不代表线上故障、跨进程恢复或生产权限策略已验收。

### 2026-08-31 Artifact 成本与跨进程恢复契约

- 固定报表的 Excel、Word、PPT、Dashboard 交付响应和生命周期登记新增结构化成本：确定性计算、模型调用 0 次、输入/输出 0 Token、0 USD、`estimated=false`。这表示该路径没有调用模型，不是 provider 账单估算。
- 任务历史完整详情新增成本来源展示；生命周期安全字段允许按会话查询成本，但仍不返回本地文件路径。
- 恢复回归先下载一次 Artifact，再回收并恢复；回读确认原 Artifact ID、Run ID、源快照、Claim/Evidence、成本和下载历史保持不变，第二次恢复返回 404，不重复恢复文件。
- 测试启动独立 Python 应用进程并重开同一临时 `PFS_DATA_DIR`，通过会话 Artifact 详情与下载 HTTP 入口读取同一对象和文件，证明本地 registry 可跨应用进程重开关联。
- 专项验证：`tests.test_pfs_delivery_artifacts` 7 项通过，目标 Ruff、ESLint 和 `git diff --check` 通过。
- 边界：这是本地 JSON registry 和受控文件目录的跨进程重开，不是多服务/分布式恢复；真实 Agent 生成 Artifact 时的模型 usage 归集、工作区关联和 provider 账单对账仍待完成。

### 2026-08-31 Artifact 工作区关联契约

- 生命周期登记现支持 PFS 数据目录与已知工作区 `artifacts/` 两种受控边界；工作区外文件、缺少 Workspace ID 的外部文件和越界路径仍被拒绝。
- 工作区 Artifact 记录稳定 Workspace ID；切换/卸载后和独立应用进程重开同一 `PFS_DATA_DIR` 后，仍可通过会话 Artifact 详情和下载入口读取同一文件。
- 任务历史展示工作区名称、短 ID 和可用状态，不返回或悬浮显示绝对路径；同一 Artifact ID 只能幂等复用同一文件，不能静默覆盖另一产物。
- 工作区 Artifact 回收后恢复到原工作区目录，并保持原 Artifact ID；会话归档不会把用户项目内的 Artifact 搬出工作区。
- 数据目录 TTL 清扫器只裁剪 data-scope 登记，即使工作区 Artifact 与数据目录文件具有相同相对路径，也不会误删工作区登记。
- 本轮完整门禁为 171 项 Python 测试通过，Ruff、ESLint、Chat production build、Dashboard/Chat build check 和 `git diff --check` 通过；发布 staging 为 3,871 个文件、183,313,961 bytes，artifact audit 为 0 findings。
- 边界：这是单机已知工作区索引和本地文件系统契约，不证明分布式存储、跨主机迁移、生产故障恢复或真实 Agent 成本归集完成。

### 2026-08-31 Run → Artifact 用量关联与文档收口

- 实现：主 Agent 聊天链路以会话级 `conversation_job_id` 作为稳定 Run ID；模型 usage 事件记录 `run_id`、provider、model、调用次数和输入/输出/缓存 Token。该 Run 生成的所有 Artifact 会按 `session_id + run_id` 回填聚合用量，取消、失败或中断时保留已经发生的用量。
- 费用边界：配置完整模型单价时，Artifact 记录基于配置单价的估算并标记 `estimated=true`、`billing_verified=false`；价格未知时保留 Token/调用次数但费用为 unknown；确定性报表明确为 0 次模型调用、0 Token、0 USD 且非估算。没有供应商账单对账，也没有将 Workflow 图级费用自动关联到 Artifact。
- 安全边界：用量回填只匹配 `session_id + run_id` 和 active Artifact，只更新结构化 cost 字段，不改变 Artifact 身份、路径、快照哈希、大小或 Workspace ID；未知价格不以 0 假装完成计费。
- 验证：完整 Python 回归 `175` 项通过、无失败、无跳过；真实上传 CSV/XLSX 的动态 Evidence/Claim 登记、按 `session_id + run_id` Ledger 回读和 Artifact lineage 专项回归通过；Ruff、ESLint、Chat/Dashboard production build、`git diff --check` 均通过。
- 发布边界：本轮文档和代码仍未 commit、push、deploy 或 live 验收；GitHub 私有仓库的既有远端状态不代表本轮改动已发布。
- 本轮门禁：Ruff、ESLint、Chat production build、Dashboard/Chat build check 和 `git diff --check` 全部通过；重新生成的临时发布 staging 为 3,871 个文件、183,325,640 bytes，manifest 与文件系统回读一致，artifact audit 为 0 findings、0 symlink。
- 动态回链边界：真实上传 CSV/XLSX 的直接分析、受限问句和确定性聊天均写入本地 JSON Ledger；Claim 保存 `evidence_ids`，Claim 与 Evidence 建立 `supports` 关系，Evidence 保留原始文件名、来源 ID、工作表、纳入行数、日期范围、内容哈希、locator 和来源 URL。报表预览、SSE `pfs_result` 和会话 Artifact 详情可以按同一 `session_id + run_id` 回读这些 lineage。该结果是本地 HTTP/前端/Artifact 回归，不代表自动语义核验、核心事件自动消费、分布式 Ledger 或线上验收。

### 2026-09-01 会话统一审计中心：隔离本地真实桌面验收

- 新增会话所有权保护的 `GET /api/session/<sid>/audit`。接口聚合当前会话 Job、可回放事件、模型调用、工具步骤、模型重试、错误、Artifact、Claim、Evidence、人工裁决、Token、成本、耗时和命令指标，并支持对象、状态、关键词和起止时间筛选。
- 新增与 Flask 解耦的 `pfs_agent/audit.py` 聚合契约。响应只投影固定安全字段，不返回绝对路径、模型请求正文或完整工具参数；其他会话的 Job、Artifact、Claim、Evidence 和裁决不会进入当前响应。
- Claim 会回链 supports/refutes Evidence 与片段；没有证据的 Claim 进入 `uncovered_claims`，同时存在支持和反驳关系的 Claim 进入 `conflicts`。人工决定单独进入审批时间线；未知模型单价保持 `amount=null`，不转成 0。
- `agent/retry.py` 增加只含审计元数据的重试观察回调；主 Agent 会把重试次数、等待秒数、归类原因、provider 和 model 写入当前 conversation Job 事件，不保存请求正文或 provider 错误原文。任务历史内新增“本会话审计中心”，保留原 Job 与交付物区域，并提供统一时间线和核验待处理区。
- 验证：隔离服务 `http://127.0.0.1:5217` 使用 `/private/tmp/pfs-audit-browser.soChCr` 数据目录，Session `1718cfc6-047a-4b2f-9fae-d353afc71fac`、Job `d251964a-98e`、Artifact `artifact-audit-browser-d251964a-98e`；1280×720 真实桌面回读满数据、工具类型筛选、关键词无匹配空态、重置筛选、`Failed to fetch` 错误态、弹窗内部滚动和显式关闭按钮。夹具包含 2 个 Claim、2 条 Evidence、1 个 supports/refutes 冲突、1 个无证据 Claim 和 1 条 `needs_review` 人工裁决，原因“业务口径不一致”。最终统计为 1 个成功任务、1 个交付物、2 个 Claim、1 个无证据结论、1 个冲突、1 次模型调用、120 input + 30 output Token，费用未知；安全投影未暴露绝对路径、完整 SQL 或完整工具参数。
- 门禁：本轮重新运行的 Python 全量测试、Ruff、ESLint、Chat production build、Dashboard/Chat build check 和 `git diff --check` 结果以本轮实测为准；构建既有 500 kB chunk 提示不影响退出状态。
- 边界：真实桌面验收发现关键词输入框按 Escape 不关闭审计弹窗，列为待修复缺陷；真实模型网络失败/重试注入、长会话性能、Docker/全栈、发布 staging、commit、push、deploy 或 live 验收仍未完成。Workflow 图级 Artifact 成本、供应商账单对账、语义核验和完整审批仍未完成。默认项目数据根另有一次误启动留下的 Job/Artifact/ledger 残留，本轮未清理。

### 2026-09-01 P0 本地切片收口

- 修复：动态任务历史/统一审计弹层在关键词等内部控件聚焦时响应 Escape，并由弹层自身消费事件后关闭。隔离本地工作台实测 `beforeEscape=true`、`afterEscape=false`。
- Workflow 成本：委托模型调用新增 `model_calls` 计数并持久化到节点；调度器只向执行副本注入 Run/节点身份；导出 Artifact 记录 `run_id`、Workflow Run 与节点 Run 身份，导出时写入当前聚合用量，Run 成功/失败/取消后刷新终态总量。
- 费用语义：总量汇总全部已记录节点 attempt/iteration；配置价格完整时记录估算 USD，任一已测量节点价格未知时 `amount=null`，无模型用量时标记 unavailable，均保持 `billing_verified=false`。
- 边界：这是本地持久化和隔离桌面证据，不是供应商账单、多服务队列或分布式恢复；当前切片未 commit、push、deploy 或 live 验收。默认项目数据根的既有误启动残留仍未清理。
- 门禁：196 项 Python 测试通过；Ruff、ESLint、Chat production build、Dashboard/Chat build check 和 `git diff --check` 通过。当前源码发布 staging 为 3,872 个文件，artifact audit 的 0 findings、0 symlink 是上一轮记录，本切片未重跑 staging；既有 500 kB chunk 提示不影响构建退出状态。

### 2026-09-03 P0 退役范围与发布 staging 收口

- 范围修复：删除此前仍被跟踪的 3,332 个 `static/drawio/` 静态文件和 30 个 `data/shape_libs/` 图形库文件，移除 draw.io 专用安全响应头、过期 Google Sheets 用户说明和残留注释；退役回归增加可执行静态资产缺失断言。
- 本地数据边界：`static/drawio/` 下仍有 8 个被 Git 忽略的 secret/配置文件，`business_canvas/business_canvas.sqlite` 仍是本地运行数据。它们未获凭据/数据清场授权，因此保留现场；发布策略新增整个 `drawio` 路径拒绝规则，防止本地残留进入制品。
- 门禁：2026-09-03 实跑 196 项 Python 测试，全部通过、无失败、无跳过；Ruff、ESLint、Chat production build、Dashboard/Chat build check 和 `git diff --check` 全部通过。Chat 主 bundle 为 511.80 kB，保留既有 500 kB 体积提示。
- 发布预演：独立临时 staging 为 489 个文件、37,703,382 bytes；artifact audit 为 0 findings、0 symlink，路径回读未发现 draw.io、shape_libs、business_canvas 或 gsheets，文件回读未发现数据库和本地凭据。
- 隔离运行：当前工作树在 `127.0.0.1:5023` 启动 Waitress，`/api/health` 和主工作台返回 200；`/static/drawio/index.html`、`/api/business-canvas` 和 `/api/connect-gsheets` 均返回 404。回读后测试服务已停止。
- 清场与发布边界：用户在完整汇报后确认删除两份临时 staging、一份隔离运行目录和未跟踪的 `direction-approved.md`，清理后逐项回读为不存在。被忽略的本地凭据、数据库、上传、输出和参考快照继续保留且不进入发布包。主切片已提交为 `756f358 feat: close PFS local transformation slice`，清场与状态已另行提交到本地 `main`；两次 HTTPS push 均因 GitHub 443 连接超时失败，SSH 通道因本机无可用 public key 拒绝认证，远端尚未回读到本轮提交。桌面安装包、部署和线上验收仍未执行。

### 2026-09-03 P1 Claim 核验与审批状态机第一切片

- Ledger 新增 `deterministic_claim_evidence/v1` 关系核验：根据已登记 supports/refutes、置信度和 Evidence 覆盖形成 verdict、风险和理由。它不读取自由文本判断现实真伪，不替代业务审核。
- Claim 新增 pending/approved/rejected/changes_requested/deferred 状态、服务端审核身份、审批修订号、决定时间/理由和 Claim 版本。会话 API 新增审批队列、重新核验和退回后修订；审批修订号与 Claim 版本使用乐观锁，跨会话请求继续拒绝。
- 任务历史审计中心新增待审批数量、高风险数量、机器核验卡、Evidence 关系及批准/拒绝/暂缓/退回交互。退回后可以修订文本；修订会清空旧 Evidence 关联并回到 pending，避免旧证据继承给新结论。核验、审批和修订进入安全审计时间线，不记录业务文本到生命周期日志。
- 门禁：201 项 Python 测试通过、无失败、无跳过；Ruff、ESLint、Chat production build、Dashboard/Chat build check 和 `git diff --check` 通过。Chat 主 bundle 513.13 kB，只有既有 500 kB 体积提示。ego-browser 在隔离数据目录完成 2 条 Claim 展示、批准、退回、修订 v1→v2、Evidence 清空、insufficient 回退和无横向溢出回读；空间和服务已关闭，临时目录移入系统废纸篓。P1 尚未 commit、push 或重建发布 staging。

### 2026-09-03 P2 真实业务验收第一切片

- 场景：使用项目随附、匿名化的 `deploy/samples/Sample-data.xlsx` 中 `10城数据包`，回答经营负责人关于城市规模、增长、单位贡献与关注优先级的问题。该样本不是公司生产数据。
- 数据门禁：执行城市编号唯一、关键字段完整、数值可解析、比例/金额范围、整行重复和四类订单结构逐城加总 100% 六项检查。重复城市编号或订单结构不勾稽的反例会返回 `needs_revision`，并停止输出指标、排名和结论。
- 独立真值：10 城、日均 760 千单、按 365 天年化 277,400,000 单、年化 GMV 15,629,300,000 元（156.293 亿元）、订单量加权客单价 56.34 元；规模前三为泉州、惠州、徐州。源表 9/10 城状态含“亏损”。
- 业务边界：单位贡献余量只计算客单价减履约成本及补贴营销，不称为利润；源表缺少佣金收入、商家结算、总部费用和其他损益项，不能据此解释亏损原因。横截面的订单年增速也不能独立复算。
- 产品入口：新增会话安全的 `POST /api/session/<sid>/pfs/business-acceptance` 和口径预览中的“运行经营验收”。结果页展示是否可汇报、质量勾稽、四张指标卡、规模前三、可陈述结论和汇报限制；业务验收结果暂不启用旧的通用报表下载/交付按钮，避免错误套用 grouped SUM 导出合同。
- 真实桌面：Ego 浏览器在 `http://127.0.0.1:5038` 上传同一 XLSX、选择 `10城数据包` 并点击运行；页面回读“可汇报，但必须带限制条件”、6 项通过/0 项阻断、156.293 亿元、760 千单、56.34 元、9 个亏损城市及三条管理结论。弹窗宽度为 920px，页面无横向溢出；首次截图调用超时，因此不把截图列为完成证据。
- 门禁：207 项 Python 测试全部通过、无失败、无跳过；Ruff、ESLint、Chat production build、Dashboard/Chat build check 与 `git diff --check` 通过。Chat 主 bundle 517.40 kB，只有既有 500 kB 体积提示。P1/P2 当前仍未 commit、push、重建 staging、deploy 或 live 验收。

### 2026-09-03 P2 城市月度损益验收第二切片

- 新增 `data/fixtures/pfs_city_monthly_pnl.csv` 匿名化演示数据，覆盖徐州、泉州、桂林三城 2025/2026 年 1–3 月。它不是生产数据，不用于宣称真实经营结果。
- 自动识别城市月度损益结构；校验城市月唯一性、必填、数值、月份格式、逐城同比覆盖、连续月份、非负范围、GMV/收入关系、贡献利润与经营利润桥接及重复行。任何阻断项都会停止输出指标和结论。
- 独立真值：2026 年 1–3 月收入 18,780.3 万元，同比 +15.42%；经营利润 1,636.225 万元，同比增加 935.725 万元；经营利润率 8.71%。桂林经营利润 -1,362.325 万元，只标记为核查对象，不推断原因。
- 结果已进入会话 Evidence Ledger：三条 Claim 关联同一快照证据，限制性结论保留 `business_status=supported_with_caveat` 并进入人工复核。JSON/CSV 下载会由服务端重新读取上传快照并重跑业务场景，避免套用普通销售额导出。
- 门禁：211 项 Python 测试全部通过、无失败、无跳过；Ruff、ESLint、Chat production build、Dashboard/Chat build check 与 `git diff --check` 通过。Chat 主 bundle 518.58 kB，仅有既有 500 kB 体积提示。当前仍未 commit、push、重建 staging、deploy 或 live 验收。
- Ego 桌面验收在隔离数据目录与 `http://127.0.0.1:5040` 完成：真实上传 18 行 CSV 后自动识别场景，页面回读 8 项通过、0 阻断、4 张指标卡、3 城利润表、3 条 Claim、1 条 Evidence 和 1 条待复核限制性结论。验收发现 Evidence 挂接会把默认置信度覆盖为 0%，已修复并新增断言；复验显示两条确定性结论 95%、限制性结论 75%。人工选择“保留待确认”后审批状态变为 `deferred` 并显示决策，JSON 下载回读“已下载”；页面与弹窗横向溢出均为 0，Office 交付保持禁用。

### 2026-09-03 P1 Evidence Ledger 跨进程持久化第二切片

- JSON Ledger 写入现在使用 OS 级互斥锁；每次变更前重载最新磁盘快照，写入唯一临时文件并 fsync 后原子替换，异常时清理临时文件。读操作继续依赖原子替换，不读取半写入内容。
- 并发回归启动两个独立 Python 进程，分别注册 Evidence 与 Claim；最终 Ledger 同时保留两个进程的记录，证明本地跨进程写入不会发生最后写入者覆盖。该证据不等同于数据库事务、压力测试或分布式故障恢复。
- 门禁：本轮完整 Python 回归为 216 项通过、无失败、无跳过；Ruff、ESLint、Chat production build、Dashboard/Chat build check 和 `git diff --check` 通过。P1/P2 当前仍未 commit、push、重建 staging、deploy 或 live 验收。

### 2026-09-03 P2 用户—供给效率验收第三切片

- 新增 `city_user_supply_v1` 场景和口径预览选择器，针对匿名化 `Sample-data.xlsx` 的 `10城数据包` 校验城市主键唯一、字段完整、数值与比例范围、非负值、正向分母和重复行；阻断时不输出指标、排名或结论。
- 独立真值：10 城、718.0 万月活用户、150.2 万高价值用户、加权高价值用户占比 20.92%、日均订单量 760 千单、整体日均下单频次 0.106、商家日均订单量 60.95；高价值规模前三为泉州/惠州/徐州，双低观察城市为赣州/宜昌/桂林/威海/洛阳。结论仅用于横截面筛查，不推断留存、转化或因果关系。
- 结果登记到会话 Evidence Ledger，包含 3 条 Claim、1 条 Evidence；确定性结论置信度 95%，限制性筛查结论置信度 75%。服务端重读上传快照并重算，避免下载时套用普通销售额导出；结果页继续禁用 Office 交付。
- Ego 浏览器在隔离服务 `http://127.0.0.1:5051` 真实上传 XLSX、显式选择场景并运行；页面回读 `已完成`、7 项通过/0 阻断、4 张指标卡、排名表、5 个双低城市、3 条 Claim、1 条 Evidence，且页面和弹窗横向溢出均为 0。
- 门禁：216 项 Python 测试、Ruff、ESLint、Chat production build、Dashboard/Chat build check 和 `git diff --check` 全部通过；当前仍未 commit、push、重建 staging、deploy 或 live 验收。

### 2026-09-03 P1 Evidence 治理第二版：快照绑定与审批失效

- 治理函数现在要求报告带有效的源快照 SHA-256，并逐条校验 Evidence 的 `content_sha256` 与当前分析快照一致；缺失或不一致分别返回 `evidence_snapshot_missing` / `evidence_snapshot_mismatch`，不再把来源相近但内容不同的证据登记到同一份报告。
- Ledger 的 Claim 证据关系发生实质变化时，会清空审核人、人工决定、决定理由和决定时间，使审批状态回到 `pending` 并递增 `approval_revision`；相同证据关系的重复分析保持既有审批，避免重算导致无意义的审批丢失。
- 报表预览治理摘要新增“快照已绑定 · N 条证据”标识，保留现有 Claim/Evidence 详情和人工裁决交互。UI 契约新增哈希不一致回归；Ledger 回归覆盖证据变化后的旧审批失效，HTTP 回归覆盖正常快照绑定、重复分析后已批准状态保持和不一致哈希拒绝。
- 门禁：本轮完整 Python 回归为 218 项通过、无失败、无跳过；Ruff、ESLint、Chat production build、Dashboard/Chat build check、`pnpm run format:check` 和 `git diff --check` 全部通过。Chat 主 bundle 为 521.57 kB，仅有既有 500 kB 体积提示。
- Ego 浏览器在隔离服务 `http://127.0.0.1:5051` 真实上传 `Sample-data.xlsx` 的 `10城数据包`，显式选择“用户—供给效率”并运行；页面回读 7 项通过/0 项阻断、4 张指标卡、3 条 Claim、1 条 Evidence 和“快照已绑定 · 1 条证据”。页面点击“确认支持”后重新运行，已批准状态仍保持；页面与报表弹层横向溢出均为 0。验收空间、服务和临时目录已关闭/移入废纸篓。
- 边界：这是本地 JSON Ledger 与确定性分析的快照完整性门禁，不是数据库事务、分布式 Ledger、独立事实核查或生产级语义评测；P1/P2 当前仍未 commit、push、重建发布 staging、deploy 或 live 验收。

### 2026-09-03 P2 需求预测业务护栏切片

- 输入：`data/fixtures/pfs_monthly_demand.csv` 匿名化月度订单/GMV 样本；本轮没有使用生产数据、外部数据源或供应商模型服务。
- 改造：`pfs_agent/business_forecast.py` 新增可选 `business_thresholds.max_total_delta_pct`，独立检查末尾 temporal holdout 的预测订单总量绝对偏差；缺少护栏时返回 `needs_review`，超出护栏时返回 `needs_revision`，不把模型误差通过误写成经营计划准入。口径预览增加模型、Holdout 月数和总量偏差上限控件，并将参数发送到会话 API。
- 验证：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest tests.test_pfs_business_forecast tests.test_pfs_analysis_accuracy tests.test_pfs_agent_vertical_slice -q`，27 项通过；Ruff 和前端 ESLint 通过。专项覆盖 Prophet/ARIMA/SARIMA/VAR/GRU、未配置护栏、显式通过、超限阻断、非法阈值、API 转发和 UI 参数契约。
- 关键回读：样本末尾 3 个月 holdout 的订单总量绝对偏差为 6.06%；`max_total_delta_pct=100` 通过该护栏，`max_total_delta_pct=0` 返回 `needs_revision`。前端和 API 仍支持只报告、不作准入结论的默认路径。
- 边界：这是匿名本地业务形态和 HTTP/API 回归，不是生产数据、预测收益/库存护栏、数据漂移、校准、公平性、浏览器本轮复验或线上部署证明；当前切片未 commit、push、deploy 或 live 验收。

### 2026-09-03 P2 需求预测多指标分布漂移增量

- 改造：新增 `pfs_agent/distribution_drift.py`，对训练段与真实 holdout 订单样本计算确定性的两样本 KS 距离，并输出 10/50/90 分位数作为解释信息；不计算 p-value，不推断漂移原因，也不把小样本信号包装成生产稳定性结论。
- 护栏：新增可选 `business_thresholds.max_holdout_orders_ks_distance` / `max_holdout_gmv_ks_distance`，均限定在 0–1；留空返回 `needs_review`，超限返回 `needs_revision`，与预测总量偏差和训练→holdout 均值漂移独立计入业务护栏。工作台新增订单/GMV KS 上限和滚动窗口控件，API 与 JSON/CSV 导出重算沿用同一参数。
- 口径：订单和 GMV 分别计算列边际两样本 KS 距离与 10/50/90 分位数，聚合状态在任一列失败时阻断；该切片不声称联合分布、统计显著性、生产监控、收益或库存结论。
- 验证：分布漂移与需求预测专项共 14 项通过；随后全项目 `pnpm run quality` 为 290 项 Python 测试通过、无失败/无跳过，Ruff、ESLint、Dashboard/Chat build check、Chat production build 和 `git diff --check` 通过；Chat 主 bundle 为 525.55 kB，只有既有 500 kB 体积提示。
- 边界：KS 只是当前需求预测场景的数值分布漂移第一版，仍未完成真实脱敏生产数据、跨场景/多变量漂移、校准、公平性、预测收益/库存护栏、复杂图表/Office 视觉、部署和线上验收；本轮仍未 commit、push、deploy 或 live 验收。

### 2026-09-03 P2 需求预测桌面交互复验

- 修复：需求预测的 8 个参数控件原先置于横向 flex，验收区被撑出报告弹窗；“运行经营验收”按钮视觉可见但实际落在遮罩层下，无法命中。现改为明确的场景/运行按钮行与参数换行网格，600px 视口切换为单列布局。
- Ego 回读：隔离服务 `http://127.0.0.1:5012` 使用真实上传的 `pfs_monthly_demand.csv`，设置滚动窗口=2、GMV KS 上限=1 后点击运行；页面回读 `已完成`、9 项通过/5 项待确认/0 项阻断、GMV KS=0.9630、滚动 2 个窗口。JSON/CSV 下载状态均为“已下载”。1280px 桌面按钮可直接命中；600px 视口无横向溢出，滚动到按钮后仍可命中。
- 边界：本轮证明的是本地匿名样本的前端布局、点击、结果展示和下载交互；不代表生产数据、生产预测质量、部署或线上状态。当前工作树仍未 commit/push/deploy。

### 2026-09-03 P2 需求预测可解释基线增量

- 改造：需求预测现在在同一末尾 holdout 和滚动窗口内生成 `last_value_naive` 最后一期延续基线，逐项比较模型与基线 WAPE，并把可选 `business_thresholds.min_model_wape_lift_pct` 作为模型改善下限；未配置时为 `needs_review`，配置后低于下限会阻断计划准入。
- 独立回读：匿名月度样本使用 Prophet 时，末尾 holdout 模型 WAPE 为 6.0569%、last-value 基线为 14.4737%、相对改善 58.1524%；滚动窗口模型 WAPE 为 4.1517%、基线为 10.7728%、相对改善 61.4613%。设置改善下限 0% 时基线护栏通过，设置 100% 时返回 `needs_revision`。
- 产品接线：工作台新增“相对朴素基线 WAPE 改善下限（%）”控件；结果明细展示模型/基线/改善值；请求、JSON/CSV 服务端重算和 Claim 均保留该参数与结果。此前 Ego 任务空间被用户接管前，当前源码在 `http://127.0.0.1:5013` 已回读请求中的 `min_model_wape_lift_pct=0`、页面 `10 项通过/5 项待确认/0 项阻断`、GMV KS `0.9630` 和 JSON “已下载”；Ego 后续操作按控制权规则暂停。
- 门禁：需求预测专项 10 项回归通过；本轮全量 `pnpm run quality` 为 291 项 Python 测试通过、无失败/无跳过，Ruff、ESLint、Dashboard/Chat build check、Chat production build、格式检查和 `git diff --check` 全部通过。Chat 主 bundle 为 526.85 kB，保留既有 500 kB 体积提示；当前工作树仍未 commit、push、deploy 或 live 验收。
- 边界：last-value 只表示最近一期延续法，不代表季节性、预算、供给约束、生产收益或因果效果；该切片仍未完成真实脱敏生产数据、跨场景联合漂移、校准/公平性、预测收益/库存护栏、复杂图表/Office 视觉、部署和线上验收。

### 2026-09-03 Workflow 启动恢复与副作用重放保护

- 改造：`WorkflowRuntime` 新建时会按会话扫描未终态 Run 并调用 `WorkflowScheduler.recover_interrupted_runs`。`JobsStore` 重开后留下的中断 Job 会重新进入既有节点重试/调度路径；暂停和待审批 Run 不会被启动恢复流程强行唤醒，恢复过程和结果写入 Workflow 事件流。
- 安全边界：节点声明 `write_data`、`export_file` 或 `network` 且绑定 Job 因进程重启收口时，不自动创建下一次执行；节点失败并返回稳定的 `workflow_restart_replay_blocked`，Run 记录 `workflow_side_effect_replay_blocked`，要求人工检查外部状态后再走显式重试。读数据节点仍服从原有 `max_attempts` 和 `max_total_node_runs` 门禁。
- 验证：`tests/test_pfs_reliability.py` 新增跨存储重开后只读节点恢复和副作用重放阻断两项回归；本地专项 8 项全部通过。该证据覆盖同一台机器上的 SQLite Workflow/Job 存储重开，不代表跨服务队列、强制中断、分布式一致性或生产账单对账。
- 门禁：本轮全量质量门更新为 293 项 Python 测试通过、无失败/无跳过；Ruff、ESLint、Dashboard/Chat build check、Chat production build、格式检查和 `git diff --check` 通过。Chat 主 bundle 保留既有 500 kB 体积提示；当前工作树仍未 commit、push、deploy 或 live 验收。

### 2026-09-03 Workflow 导出副作用幂等切片

- 改造：`workflow_run_store.py` 新增本地 SQLite `workflow_side_effects` 登记表和 claim/complete/fail 事务；导出动作以 Run、节点、显式迭代和目标形成稳定操作身份，以内容哈希检测同一动作的载荷变化。文件写出使用同目录临时文件、`fsync` 和原子替换，避免产生半写入 Artifact。
- 恢复：同一导出动作若已完成，会先核验 Artifact 仍存在且内容哈希一致，再复用已完成结果；载荷变化返回幂等冲突；已 claim 但未 complete 的动作保持人工复核，不自动再次写出。重启时若已完成登记存在，Workflow 可回收节点结果而不重放导出；该切片只覆盖本地 `export_file`，不宣称网络、飞书或数据库写入已具备通用幂等协议。
- 验证：新增导出重复调用复用 Artifact、内容变化冲突和重启后已完成导出回收回归；导出幂等专项 2 项及恢复相关回归通过。随后全量 `pnpm run quality` 为 295 项 Python 测试通过、无失败/无跳过，Ruff、ESLint、Dashboard/Chat build check、Chat production build、格式检查和 `git diff --check` 通过；Chat 主 bundle 保留既有 500 kB 体积提示。
- 边界：这是同一台机器上的 SQLite + 本地 Artifact 幂等合同，不是跨服务幂等键、外部 API 去重、供应商账单对账或分布式恢复；本轮仍未 commit、push、deploy 或 live 验收。

### 2026-09-04 P0 当前本地切片收口复核

- 质量门：在当前工作树重新执行 `pnpm quality`，格式检查、ESLint、Dashboard/Chat build check、Chat production build、295 项 Python 测试、Ruff 和格式差异检查全部通过。Chat bundle 仍有既有 500 kB 体积提示，不影响构建退出状态。
- 发布预演：按当前源码执行 allowlist staging，生成 492 个文件、37,836,179 bytes；`packaging/audit_artifact.py` 回读为 0 findings、0 symlink。新增城市月度损益和月度需求匿名 fixture 已加入明确的 reviewed offline fixture 白名单，其余 CSV/数据库/用户文档扩展名仍保持拒绝。
- 版本边界：当前本地 `HEAD` 为 `67d6c34`，远端 `origin/main` 为 `ffa0ec1`，远端包含本地历史基线；P1/P2 和本轮 staging 策略改动仍在工作树，尚未 commit、push、deploy 或 live 验收。此前 489 文件 staging 为历史 P0 记录，本条为当前切片的最新 staging 证据。
