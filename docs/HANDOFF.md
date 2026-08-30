# PFS 现役交接基线

> 更新时间：2026-08-30 20:43 +08:00
> 作用：下一次会话或新接手者的唯一现役状态入口。历史证据见根目录 `PROJECT_STATUS.md`，逐项证据见 `docs/FUNCTION_COMPATIBILITY_MATRIX.md`。

## 1. 产品目标

将授权参考项目改造为完全独立的 PFS 数据分析 Agent：保留并重新验证原有功能，重做品牌、UI、内部运行标识、文档和交付体系，同时增加指标口径、证据链、成本、追踪和可靠性。

当前范围只要求桌面端。普通用户使用本地 Python 安装/启动脚本，开发与部署人员可使用 Docker。

## 2. 六个事实面

| 事实面 | 状态 | 当前结论 |
|---|---|---|
| 代码 | `verified-current` | 本地 `main` 为 `3c6e91a`，当前工作树包含未提交改造；152 项 Python 测试通过，Ruff 和 Dashboard/Chat build 通过。 |
| 运行态 | `verified-current` / `pending` | 本地 Waitress 健康检查、桌面报表与固定交付物已回读；无部署和线上 PFS 可用性证据。 |
| 文档 | `changed-and-verified` | 本文作为现役入口；README、PRODUCT、完整改造计划和能力矩阵按当前边界对齐。 |
| 规则 | `changed-and-verified` | 项目根目录新增精简 `AGENTS.md`，保存命令、权威入口、状态分层和安全边界。 |
| 记忆 | `changed-and-verified` | 项目 `memory/` 是应用运行数据，当前为空且不应写入项目事实；Codex 生成记忆通过宿主允许的 correction note 更新，不直改生成索引。 |
| 工作区 | `pending` | 当前工作树有 39 个 dirty/untracked 条目（含本轮文档治理文件）；参考快照、数据库、密钥文件、输出、上传和缓存均被 ignore，但复核现场未清理。 |

## 3. 已经形成的主链路

- PFS 独立产品名、图标、运行命名空间、安装/容器入口和真实 Flask 工作台已建立。
- CSV/XLSX 上传、字段选择、受限问句、确定性分组计算、Metric Contract、Claim/Evidence 和快照哈希已形成本地桌面闭环。
- JSON/CSV 服务端重算下载已验证。Excel、Word、4 页 PPT 和 Dashboard 已接入统一交付区；Office 结构、HTTP 下载和 Dashboard 桌面打开已回读。包含说明页、空表、销售明细和异常指标的复杂 XLSX 已在真实桌面页面通过工作表选择、字段刷新、确定性分析和错误状态验收；四类交付物均从所选 `销售明细` 生成。
- 本轮验收地址为 `http://127.0.0.1:5012`；它是临时本地服务，不是部署地址。验收结束后已关闭浏览器隔离空间，并停止本轮启动的服务进程。
- DeepSeek 单任务已真实调用 schema 和只读 SQL；停止路径、Job 重启收口、Workflow 节点重试和 Token/费用/工具次数硬上限已有本地证据。
- 14 类分析和 41 个注册图表已有固定夹具输出结构证据，但不等于复杂业务结果和视觉验收。
- Docker 单容器、临时 PostgreSQL、HTTP 固定替身和本地文件 checkpoint 已有分层验证。

## 4. 发布状态

| 阶段 | 状态 | 证据 |
|---|---|---|
| implemented | 已进行 | 当前工作树包含新一轮功能与文档改造。 |
| locally verified | 已通过本轮基线 | 152 项 Python，Ruff，Dashboard/Chat build；真实桌面复杂 XLSX 已通过多工作表选择、字段刷新、空表禁用、非数字错误和四类交付验收，Dashboard 无横向溢出。 |
| committed | 部分 | HEAD 只到 `3c6e91a`，当前 39 个条目尚未整合提交。 |
| pushed / PR | 未完成 | GitHub 私有仓库已创建，API 记录默认分支名为 `main`，但 `git ls-remote --heads origin` 无任何远程分支，当前改造仍未 push。 |
| deployed | 未执行 | 无 PFS 部署 marker。 |
| live verified | 未验收 | 无 PFS 线上用户路径证据。 |
| knowledge closed | 本轮完成 | 文档、规则和获准记忆修正入口已对齐。 |
| cleaned | 未执行 | 未获得本次完整汇报后的清场确认。 |

## 5. 剩余改造顺序

### 1）先保住当前成果：差异分组、提交和私有仓库首次 push

先以文件组分开审查当前 dirty 改动，排除运行数据和参考快照；用户明确确认范围后再创建本地提交并 push。验收是远程 `main` 可回读目标 commit，不是只看 push 命令退出码。

### 2）继续完成桌面主链的错误态和复杂 Excel 验收

多 Sheet 显式选择、字段切换、空表禁用、非数字指标修复建议和四类交付已经通过真实桌面页面验收。还需继续覆盖缺字段、日期异常、大文件、取消、模型未配置和交付失败，并同时检查页面状态、计算值和实际产物。

### 3）把交付物纳入 Artifact / Lineage

将 Excel、Word、PPT、Dashboard、图表和最终结论登记为工作区 artifact，关联 Run ID、数据快照哈希、Metric Contract、SQL/参数、Claim/Evidence、成本和下载历史；重试不能重复产生副作用。

### 4）完成证据治理与人工裁决界面

补证据详情、Claim 支持/反驳关系、冲突队列、待核验、人工意见和审计事件；语义核验结果不能覆盖确定性计算或跳过审批。

### 5）按功能组完成原能力兼容复验

顺序为：生产 SQL/复杂 Excel → Google Sheets/HTTP/飞书 → 14 类分析的业务边界 → 41 个图表的桌面视觉 → Skills/Commands/Knowledge/Memory → MCP/Teams/Hooks/Business Canvas/远程能力。每项必须在兼容矩阵中记录正确、边界、失败和权限样例。

### 6）完成长任务、工作流和成本可靠性

复验多轮 SSE、流中断、cancel、checkpoint、跨进程 pause/resume、节点重试、图级费用上限、幂等副作用、真实多 provider 切换和账单对账。

### 7）完成 Office/桌面包与发布验收

用复杂报告打开 Excel/Word/PPT，检查图表、字体、元数据和跨平台显示；再在干净 Windows/macOS 环境生成、安装、启动、升级和卸载包。

### 8）部署与线上闭环

完成密钥管理、生产数据库/必要外部服务、健康检查、固定任务集、真实 DeepSeek 任务、成本/延迟/错误观测和 canonical URL 回读。最终分开标记 commit、push、CI、release、deploy 和 live verified。

## 6. 当前不能宣称的能力

- 原项目全部功能已复刻并重新验证。
- 工作流可在多进程/多服务故障后无损恢复。
- 所有外部数据源、MCP、飞书和多模型供应商都可生产使用。
- Office 产物、Windows/macOS 安装包和更新链路已完成跨平台验收。
- GitHub、部署或线上页面已包含当前未提交修改。

## 7. 清场候选（未授权删除）

- `.DS_Store`、`__pycache__/`、`.pytest_cache/`、`.ruff_cache/`：可重建缓存。
- `_pfs-export-test/`：本地导出验收产物。
- `outputs/`、`uploads/`、`auth.db`、`secret_key`、`memory/`：运行态/用户数据，只有在确认不需要恢复本地会话、下载或登录状态后才能清理。
- `Data-Analysis-Agent-main/`：本地授权参考快照，只有在功能兼容复验不再需要源码比较后才能删除。

本轮未删除任何文件。复核现场仍保留，等待用户看完完整汇报后确认是否清场。
