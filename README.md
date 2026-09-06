<p align="right"><a href="./README_EN.md">English</a></p>

<p align="center">
  <img src="./docs/assets/pfs-repository-banner.svg" alt="PFS 数据分析 Agent" width="100%" />
</p>

<h1 align="center">PFS 数据分析 Agent</h1>

<p align="center">可追踪、可核验、可交付的报表数据分析工作台</p>

<p align="center">
  用自然语言连接数据、执行受控分析、生成图表与报告，并保留可回看的运行记录。
</p>

> PFS 数据分析 Agent 是一款面向报表和经营数据的本地智能分析工作台，支持自然语言分析、受控数据查询、图表生成、多格式报告交付和结果历史回看。

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB.svg" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/Backend-Flask-111827.svg" alt="Flask" />
  <img src="https://img.shields.io/badge/Frontend-Vanilla%20JS%20%2B%20Vite-646CFF.svg" alt="Vanilla JS and Vite" />
  <img src="https://img.shields.io/badge/Desktop-macOS%20%2F%20Windows-0f766e.svg" alt="Desktop" />
  <img src="https://img.shields.io/badge/Chart%20IDs-41-c89b4a.svg" alt="41 chart IDs" />
</p>

<p align="center">
  <a href="#changelog">📝 当前版本</a> ·
  <a href="#features">✨ 项目亮点</a> ·
  <a href="#install">⚙️ 安装说明</a> ·
  <a href="#examples">📈 使用示例</a> ·
  <a href="#status">✅ 当前状态</a> ·
  <a href="#faq">❓ FAQ</a>
</p>

> PFS 数据分析 Agent 是独立维护的本地数据分析工作台；产品能力、运行标识和发布状态以本仓库为准。来源、授权与再分发边界单独记录在 [`NOTICE.md`](NOTICE.md) 和 [`LICENSE`](LICENSE) 中。

> 当前版本：`0.1.0-dev` · 开发中
>
> 当前交付口径：P0 聚焦可下载源码、macOS/Windows 本地启动和核心数据分析演示；真实 MCP、Hooks、Teams、飞书、云端登录和线上部署仅作时间盒尝试。配置、固定夹具或本地替身不等于真实外部验收。
>
> 当前状态：PFS 独立产品表面、CSV/XLSX 确定性报表、轻量 Claim/Evidence、Run/Workflow → Artifact 成本、工作区 Artifact 重开关联、临时 PostgreSQL、HTTP API 固定替身、Docker 单容器、DeepSeek 单任务和本机 Apple Silicon 未签名 macOS `.app`/`.dmg` 已有分层本地证据；共享卷 durable queue、独立 worker 和聊天状态跨进程续跑已完成本地回归。多主机恢复、生产数据源、真实外部服务、Windows/干净系统安装、签名公证、部署和线上验收仍未完成。现役交接见 [`docs/HANDOFF.md`](docs/HANDOFF.md)。

<details>
<summary><strong>📚 完整目录</strong></summary>

- [📝 当前版本与路线](#changelog)
- [✨ 项目亮点](#features)
- [🎬 产品演示](#demo)
- [🧠 核心能力](#capabilities)
- [⚡ 下载与快速启动](#quick-start)
- [⚙️ 安装说明](#install)
- [🛠 斜杠命令](#commands)
- [📈 使用示例](#examples)
- [🤖 模型配置](#models)
- [✅ 当前状态](#status)
- [🧱 目录与架构](#architecture)
- [❓ FAQ](#faq)
- [🤝 贡献与反馈](#contributing)
- [📄 来源与授权](#attribution)

</details>

---

<a id="changelog"></a>
## 📝 当前版本与路线

`0.1.0-dev` · 2026-09-05

- **P0 本地交付**：收口可下载源码、macOS/Windows 启动入口、CSV/XLSX 核心分析、图表与交付物链路，并以质量门和 staging 审计作为发布前检查。
- **P1 外部能力**：MCP、Hooks、Teams、飞书、云端登录和线上部署按时间盒尝试；没有真实目标环境的响应、页面或日志时保持待验收。
- **P2 业务分析**：继续扩展真实脱敏数据、业务口径、预测质量和复杂交付物验收；固定夹具通过不替代生产业务验收。

完整的逐项状态和证据见 [`docs/HANDOFF.md`](docs/HANDOFF.md)；历史验证记录见 [`PROJECT_STATUS.md`](PROJECT_STATUS.md)。

<a id="features"></a>
## ✨ 项目亮点

PFS 面向需要反复查看报表、经营数据和分析过程的业务人员、数据分析师与团队负责人。它的重点不是把一次问答包装成报告，而是把“数据进入 → 分析执行 → 结果交付 → 后续回看”放在同一个工作台里。

- **自然语言驱动**：用业务问题开始分析，不要求用户先写 SQL。
- **受控数据链路**：先识别字段和范围，再执行只读查询、统计分析和图表生成。
- **结果可回看**：保留运行状态、来源快照、轻量 Claim/Evidence、Artifact 和下载记录。
- **桌面优先交付**：提供 macOS/Windows 源码启动入口，普通用户不需要先部署复杂服务。
- **渐进式扩展**：模型、Skills、知识库、MCP、工作区和 Workflow 可以按需启用；未完成的外部能力明确标记为待验收。

<a id="demo"></a>
## 🎬 产品演示

最快的本地演示路径是：

1. 启动 PFS 并打开 `http://127.0.0.1:5001`。
2. 新建会话，上传仓库内的 `data/fixtures/pfs_sales.csv`。
3. 选择数据表 `pfs_sales`，查看字段、行数、时间范围和分组维度。
4. 输入：`按地区汇总销售额，并说明哪个地区最高。`
5. 查看确定性分析结果、图表/结论、来源信息，并从交付区下载 JSON 或 CSV。

固定销售样例包含 9 行数据、3 个月和 3 个地区，销售额合计为 `100,000`。这是一条不依赖真实模型的本地演示路径；接入模型后，再使用自然语言 Agent 主链路完成更开放的问题分析。

---

<a id="capabilities"></a>
## 🧠 核心能力

- **数据输入**：CSV/XLSX 上传、SQLite/MySQL/PostgreSQL/SQL Server 连接入口，以及受控 HTTP 数据源。
- **数据理解**：字段与数据预览、缺失/重复提示、数据范围和来源快照。
- **分析执行**：只读 SQL、分组统计、指标目录、安全聚合、异常值处理、十分位、K-Means、决策树和预测评估入口。
- **图表与看板**：当前注册 41 个图表 ID，并提供 Dashboard 交付入口；固定夹具已做结构级冒烟，复杂业务视觉仍按场景验收。
- **交付物**：JSON、CSV，以及 Excel、Word、PPT 和 Dashboard 交付；会话历史保留 Artifact 元数据和下载记录。
- **Agent 扩展**：Skills、业务知识库、工作区、MCP、Teams、Hooks、飞书和 Workflow 保留扩展入口；真实外部服务状态以能力矩阵为准。

<a id="quick-start"></a>
## ⚡ 下载与快速启动

当前按源码仓库交付，私有仓库下载者需要先获得 GitHub 权限。进入项目目录后：

```bash
# macOS
./install.sh
./start.command
```

```powershell
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File .\install.ps1
.\start.bat
```

默认服务地址为 `http://127.0.0.1:5001`。Windows 干净系统安装、发布资产下载、签名、公证、部署和线上地址仍以 `docs/HANDOFF.md` 的最新验收状态为准。

## 产品边界

PFS 的核心链路是：

```text
提出分析目标 → 连接或挂载数据 → 检查数据结构 → 执行受控工具
→ 形成可核验结果 → 生成图表/报表/Artifact → 保留运行记录
```

平台优先保证：

- 工具调用有明确的输入契约、数据范围和运行边界；
- 查询、统计和图表结果能够回到数据源、SQL 和任务上下文；
- 长任务可以显示状态，失败、重试和未验证项不会被伪装成成功；
- 写入、导出和外部连接逐步纳入人工审批、幂等和审计；
- 本地、提交、远端、部署和线上状态分别验证。

<a id="status"></a>
## ✅ 当前能力状态

| 能力 | 当前状态 | 证据边界 |
|---|---|---|
| Flask 应用、SSE 对话和数据分析工具 | 本地真实通过第一段 | DeepSeek 实际读取 schema、执行只读 SQL、返回结果并记录 Token；停止、Job 存储重启收口、共享卷 queue worker 和聊天历史跨进程回读已实测；报表分析取消支持显式 `PFS_RUN_REGISTRY_BACKEND=sqlite` 的本地跨进程协作式取消 |
| Excel/CSV、DuckDB、图表和导出 | 固定样例本地通过；临时 PostgreSQL 连接、浏览器数据预览/选表与分组查询通过 | 固定报表的 Office 结构、Dashboard 和 macOS headless 中文渲染已回读；复杂业务文件、生产数据库、原生 Office 视觉和跨平台质量待验证 |
| PFS 产品身份、图标和服务标识 | 已实现 | 已做静态编译与模板入口检查 |
| PFS 工具契约与策略门 | 已实现第一段 | `get_schema`、`query_data`、`run_analysis` 等只读/计算调用已接入；写入类仍沿用原流程 |
| PFS 报表口径预览 | 已实现第一段 | 主聊天页可读取固定 fixture，也可选择当前会话上传的 CSV，展示指标、分组、Claim、Evidence 和数据快照哈希 |
| PFS 报表下载与交付 | 已实现桌面第一段 | 服务端重新计算 JSON/CSV；Excel/Word/PPT/Dashboard 已登记为会话 Artifact，保留安全元数据、来源快照、成本与下载历史；已知本地工作区在独立应用进程重开后仍可关联，分布式恢复仍待实现 |
| 轻量结果留痕 | 已实现第一段 | 报表结果保留 `claims/evidence`、来源 ID、文件/工作表、纳入行数、定位信息和快照哈希；参考 Agent 的工具结果持久化继续保留。Evidence Ledger、质量门、审批/修订、同源复算治理和 `/api/pfs/ledger` 不属于当前运行链路 |
| Docker 单容器 | 本地真实通过（arm64） | 当前工作树全新 Dockerfile 镜像已构建；临时容器健康检查、首页、CSV 上传和确定性分析通过。镜像仅保存在本机；多服务全栈、部署和线上回读待验证 |
| MCP、Feishu、生产数据源、恢复和部署 | 部分验证 | 临时 PostgreSQL 与本地 Job 恢复已通过；MCP/Feishu、生产权限、多服务恢复和部署仍需真实场景逐项验收 |

<a id="install"></a>
## ⚙️ 安装说明与本地运行

PFS 采用双入口交付：普通用户使用安装脚本或启动脚本，开发/部署人员使用 Docker。两条入口运行的是同一个 PFS Agent，普通用户不需要安装 Docker。

### 普通用户：安装脚本或启动脚本

需要 Python 3.10+。私有仓库安装需要当前环境具备 GitHub 访问权限。启动脚本只检查环境，不会在每次启动时偷偷安装依赖。

首次使用可以执行：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
./start.command
```

macOS/Linux 也可以使用仓库中的 `install.sh` 完成安装并生成 `pfs-data-analysis-agent` 启动命令；Windows 使用 `start.bat`。

如果已经完成依赖安装，也可以直接运行：

```bash
python app.py
```

然后打开 `http://localhost:5001`。模型、数据源和外部服务配置不会写入仓库；请通过本地配置或环境变量提供。

### 开发/部署人员：Docker

Docker 用于统一开发环境、验证发布镜像和部署服务，不是普通用户的必需条件。构建并启动单容器：

```bash
docker build -t pfs-data-analysis-agent:local .
docker run --rm -p 5001:5001 \
  -e DEEPSEEK_API_KEY=your-key \
  pfs-data-analysis-agent:local
```

然后打开 `http://localhost:5001`。不要把 API Key 写入 Dockerfile、镜像或 Git；生产环境应通过部署平台的密钥管理注入。当前 Docker 配置覆盖 PFS 应用单容器，PostgreSQL、Redis、工作流服务和其他外部服务仍需按功能矩阵单独配置与验收。

### 可选：跨进程 durable queue worker

需要 API 进程和任务 worker 分离时，先把下面的配置注入 API 与 worker，并确保它们使用同一组 SQLite 文件和同一可写工作区：

```bash
export PFS_ENABLE_DURABLE_QUEUE=1
export PFS_DURABLE_QUEUE_DB_PATH=/shared/pfs/durable-queue.db
export PFS_JOBS_DB_PATH=/shared/pfs/jobs.db
export PFS_CHAT_STATE_DB_PATH=/shared/pfs/chat-state.db
export PFS_DURABLE_QUEUE_ROLE=worker
.venv/bin/python scripts/durable_queue_worker.py
```

API 进程使用相同的三个路径并保持 `PFS_DURABLE_QUEUE_ROLE=api`；API 只负责提交任务，不会再额外启动内嵌 worker。该 worker 是同一主机/共享卷的 at-least-once 交接实现，依靠 lease、heartbeat 和 operation key 防止重复认领；它不是多主机共识队列，也不替代外部副作用的幂等协议。若只在单进程本地试用，可将角色设为 `embedded`，由 API 自带 sidecar。

2026-09-05 已用当前 Docker 镜像做双容器 smoke：`api` 角色只提供 HTTP，独立 `worker` 容器通过同一共享卷领取并回写聊天 Job。该 smoke 使用无模型配置验证安全失败回写，不代表真实 provider、生产队列或线上部署已经验收。

仓库也提供了可复用的本地双服务配置 `docker-compose.yml`：

```bash
docker compose up --build
```

默认只绑定 `127.0.0.1:5001`；可用 `PFS_COMPOSE_PORT=5012` 或 `PFS_COMPOSE_BIND=0.0.0.0` 覆盖。API 和 worker 共用命名卷 `/var/lib/pfs`，API 只负责 HTTP producer，worker 负责消费队列。2026-09-05 已用隔离项目名和临时卷实际回读 API health、独立 worker 领取 `chat_turn`、Jobs DB 终态、聊天事件回放，以及一个 `workflow_node` 的 Workflow Run/Artifact 终态；无模型配置安全失败路径与本地 OpenAI-compatible fixture 的成功流式聊天和 Workflow 均已验证，且后台 Memory 提取不会再引用已关闭的队列 JobStore。该配置用于同一主机/共享卷验证，不是多主机生产部署模板；停止服务使用 `docker compose down`，不要在没有确认数据范围时删除命名卷。

<a id="commands"></a>
## 🛠 斜杠命令

命令面板和输入框支持以下本地命令。命令能否调用真实外部服务，仍以当前配置和能力矩阵为准。

| 命令 | 状态 | 作用 |
|---|---|---|
| `/new` | ✅ | 新建干净的分析会话 |
| `/sessions` | ✅ | 查看或刷新已保存会话 |
| `/data` | ✅ | 打开数据预览与表选择 |
| `/status` | ✅ | 查看模型、数据源和上下文状态 |
| `/jobs` | ✅ | 查看任务历史和运行状态 |
| `/skills` | ✅ | 查看、选择或刷新 Skills |
| `/knowledge` | ✅ | 打开业务知识库 |
| `/mcp` | ✅ | 打开 MCP 连接与工具管理 |
| `/workspace` | ✅ | 管理工作目录和权限 |
| `/teams` | 💤 | 打开本地分析团队入口，外部 Teams 未验收 |
| `/robot` | 💤 | 打开飞书机器人入口，真实飞书未验收 |
| `/stop` | ✅ | 停止当前正在生成的回复 |
| `/compact` | ✅ | 压缩当前对话上下文 |
| `/help` | ✅ | 查看命令帮助 |

<a id="examples"></a>
## 📈 使用示例

### 示例 1：区域销售分析

```text
按地区汇总销售额，找出最高地区，并给出一张适合汇报的图表。
```

系统会先读取字段和范围，再执行受控查询，输出分组结果、图表建议和业务说明；结果可以回到来源数据和本次运行记录。

### 示例 2：数据质量检查

```text
先检查这份销售表的缺失值、重复记录、日期范围和异常金额，再告诉我哪些问题会影响结论。
```

系统会把数据质量提示和分析结果分开呈现，不自动静默删除重复记录，也不会把待确认项伪装成结论。

### 示例 3：交付分析结果

```text
把本次分析整理成 Excel 和 PPT，并保留数据来源、口径和最终结论。
```

固定报表的 JSON/CSV 与 Excel/Word/PPT/Dashboard 交付入口已经接入本地工作台；复杂 Office 内容和跨平台视觉仍需逐项验收。

<a id="models"></a>
## 🤖 模型配置

PFS 当前公共内置模型目录为：

| 类型 | 可选模型 |
|---|---|
| 通用模型 | DeepSeek、Kimi、GLM、MiniMax |
| Coding Plan | Kimi Coding Plan、GLM Coding Plan、MiniMax Coding Plan |
| 自定义 | 任意 OpenAI-compatible API（自定义名称、Base URL、Model 和 API Key） |

OpenAI / ChatGPT、AtlasCloud、Ollama 只保留旧本地配置清理兼容，不会重新出现在默认目录、会话选型或回退链路中。模型设置中的密钥只保存在本地运行环境，不要写入仓库。

<a id="verification"></a>
## 🧪 本地验证

本地静态检查不依赖 Docker；需要时也可以按状态记录启动 Docker 和真实模型进行集成验证：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -B -m py_compile app.py api/__init__.py agent/agent.py
node --check frontend/legacy/i18n.js
node --check frontend/legacy/update.js
pnpm install --frozen-lockfile
pnpm format:check
pnpm lint
pnpm build
```

这些检查只证明静态代码、前端 bundle 和离线契约，不代表 Flask 完整启动、真实模型任务、数据库、SSE 长连接或部署成功。启动时默认不会偷偷执行 pip 安装；缺依赖时应先按 requirements 安装，旧式自动安装必须显式设置 `PFS_AUTO_INSTALL_DEPENDENCIES=1`。

<a id="architecture"></a>
## 🧱 目录与架构

```text
app.py                 应用启动入口
api/                   HTTP、SSE、工作区、任务和系统接口
agent/                 Agent Loop、工具、Skills、Workflow、MCP
data/                  会话、工作区、数据源和运行状态存储
Function/              统计分析、清洗、图表和导出实现
pfs_agent/             PFS 业务契约和确定性策略门
frontend/              前端模块和构建入口
templates/             服务端页面
static/                样式、图标和构建资源
tests/                 PFS 契约、适配层和分析选择器测试
```

## 项目文档

- [现役交接与剩余顺序](docs/HANDOFF.md)
- [产品规格与能力矩阵](PRODUCT.md)
- [研究与验证台账](PROJECT_STATUS.md)
- [功能兼容矩阵](docs/FUNCTION_COMPATIBILITY_MATRIX.md)
- [架构地图](ARCHITECTURE_MAP.md)
- [PFS 工具策略契约](DAY2_TOOL_POLICY.md)
- [框架改造路线](FRAMEWORK_9_DAY_PLAN.md)
- [完整独立改造计划](docs/PFS_FULL_TRANSFORMATION_PLAN.md)
- [安全策略](SECURITY.md)

## 🔐 数据与安全边界

不要提交 API Key、`.env`、`secret_key`、SQLite 运行时文件、上传数据、生成产物或 `node_modules`。对外部网页、工具返回和模型输出一律按不可信输入处理；权限、预算和人工审批由代码和策略执行，不由模型自行决定。

第三方依赖、原始授权和版权材料按仓库中的许可文件保留。PFS 的产品文案、界面、配置命名和新增业务层独立维护。

<a id="faq"></a>
## ❓ FAQ

<details>
<summary><strong>这是云端 SaaS 还是本地 Agent？</strong></summary>

当前交付以桌面本地工作台为主。Docker、云端登录和线上部署属于扩展/验收边界，不能因为仓库存在相关配置就当作线上产品已经可用。
</details>

<details>
<summary><strong>普通用户需要安装 Docker 吗？</strong></summary>

不需要。普通用户使用 `install.sh` / `start.command` 或 `install.ps1` / `start.bat`；Docker 主要用于开发、镜像验证和部署尝试。
</details>

<details>
<summary><strong>哪里能看到哪些能力真正完成？</strong></summary>

请先查看 [`docs/HANDOFF.md`](docs/HANDOFF.md) 和 [`docs/FUNCTION_COMPATIBILITY_MATRIX.md`](docs/FUNCTION_COMPATIBILITY_MATRIX.md)。它们会区分源码存在、固定夹具、本地真实、集成、部署和线上验收，不用“代码还在”替代功能完成。
</details>

<details>
<summary><strong>遇到安装或分析问题，应该提供什么信息？</strong></summary>

请提供操作系统、Python 版本、启动入口、脱敏后的错误信息、最小输入样例和对应的运行状态。不要提交 API Key、连接串、上传数据、SQLite 文件或完整日志。
</details>

<a id="contributing"></a>
## 🤝 贡献与反馈

欢迎围绕可复现的问题提交 Issue 或改动建议。建议先说明：

- 影响的页面、命令、数据源或分析能力；
- 最小复现步骤和期望结果；
- 实际环境与验证边界；
- 是否涉及密钥、用户数据或外部副作用。

代码改动应至少通过相关回归、格式检查和 `git diff --check`。外部连接、部署和线上能力必须附上目标环境的可复核响应、页面或日志，不能只提交配置文件。

<a id="attribution"></a>
## 📄 来源与授权

PFS 是基于已获授权的 [Zafer-Liu/Data-Analysis-Agent](https://github.com/Zafer-Liu/Data-Analysis-Agent) 进行的独立改造。参考仓库的代码、版权、第三方资源和原始许可证不因 PFS 的品牌改造而自动改变；PFS 新增的产品层、业务契约、界面和验证文档按本仓库当前文件维护。

- 来源、授权和参考快照边界：[NOTICE.md](NOTICE.md)
- 安全问题报告：[SECURITY.md](SECURITY.md)
- 当前最终公开许可证尚未在本仓库选定；在许可证补齐前，不应从本 README 推断商业再分发权。
