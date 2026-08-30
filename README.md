# PFS 数据分析 Agent

PFS（可追踪、可核验的报表数据分析工作台）是一个面向报表、经营数据和团队分析任务的 Agent 平台。

它把数据源、自然语言分析、SQL、统计工具、图表、长任务、人工确认和结果追踪放在同一条工作链路中。报表生成是一个产出场景，不是产品边界。

> 当前版本：`0.1.0-dev` · 开发中
>
> 当前状态：PFS 独立产品表面、确定性报表切片、服务端重算的 JSON/CSV 报表下载、临时 PostgreSQL SQL 数据源、Docker 单容器和 DeepSeek 真实 Agent 任务已通过；停止路径、Job 存储重启收口已实测，备用模型切换已有代码回归；多轮会话、工作流跨进程恢复、其他外部数据源、多服务全栈、部署和线上验收仍未完成。

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

## 当前能力状态

| 能力 | 当前状态 | 证据边界 |
|---|---|---|
| Flask 应用、SSE 对话和数据分析工具 | 本地真实通过第一段 | DeepSeek 实际读取 schema、执行只读 SQL、返回结果并记录 Token；停止和 Job 存储重启收口已实测，多轮和工作流恢复待验证 |
| Excel/CSV、DuckDB、图表和导出 | 固定样例本地通过；临时 PostgreSQL 连接与分组查询通过 | 真实业务文件、SQL Server/生产数据库、视觉和跨平台质量待验证 |
| PFS 产品身份、图标和服务标识 | 已实现 | 已做静态编译与模板入口检查 |
| PFS 工具契约与策略门 | 已实现第一段 | `get_schema`、`query_data`、`run_analysis` 等只读/计算调用已接入；写入类仍沿用原流程 |
| PFS 报表口径预览 | 已实现第一段 | 主聊天页可读取固定 fixture，也可选择当前会话上传的 CSV，展示指标、分组、Claim、Evidence 和数据快照哈希 |
| PFS 报表下载 | 已实现第一段 | 预览可下载服务端重新计算的 JSON/CSV；上传数据的自然语言口径也会在服务端重新解析，Word/Excel/PPT 原有导出路径仍需逐项复验 |
| Evidence Ledger、Claim–Evidence、冲突队列 | 已实现第一段 | `pfs_agent/ledger.py` 与 `/api/pfs/ledger` 已支持稳定身份、批量幂等、冲突检测和原子 JSON 持久化；语义核验待实现 |
| Docker 单容器 | 本地真实通过 | 镜像内容审计、健康检查、首页和 CSV 分析通过；多服务全栈及部署待验证 |
| MCP、Feishu、生产数据源、恢复和部署 | 部分验证 | 临时 PostgreSQL 与本地 Job 恢复已通过；MCP/Feishu、生产权限、多服务恢复和部署仍需真实场景逐项验收 |

## 本地运行

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

## 开发检查

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

## 目录结构

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

- [产品规格与能力矩阵](PRODUCT.md)
- [当前真实状态](PROJECT_STATUS.md)
- [架构地图](ARCHITECTURE_MAP.md)
- [PFS 工具策略契约](DAY2_TOOL_POLICY.md)
- [九天改造计划](NINE_DAY_PLAN.md)
- [框架改造路线](FRAMEWORK_9_DAY_PLAN.md)
- [安全策略](SECURITY.md)

## 数据与安全边界

不要提交 API Key、`.env`、`secret_key`、SQLite 运行时文件、上传数据、生成产物或 `node_modules`。对外部网页、工具返回和模型输出一律按不可信输入处理；权限、预算和人工审批由代码和策略执行，不由模型自行决定。

第三方依赖、原始授权和版权材料按仓库中的许可文件保留。PFS 的产品文案、界面、配置命名和新增业务层独立维护。
