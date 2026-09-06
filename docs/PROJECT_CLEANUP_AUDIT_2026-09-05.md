# PFS 项目洁癖收口记录

> 审计日期：2026-09-05；2026-09-06 发布前复核
> 文档性质：一次性的知识、规则和工作区盘点记录，不是第二份现役待办。
> 当前状态入口：[`docs/HANDOFF.md`](HANDOFF.md)。逐项能力证据：[`docs/FUNCTION_COMPATIBILITY_MATRIX.md`](FUNCTION_COMPATIBILITY_MATRIX.md)。

## 盘点范围

本次按完整洁癖路径检查了项目规则、现役交接、能力矩阵、产品说明、长期改造计划、历史验证台账、前端状态、学习路线、Git 远端、CI/打包/容器入口，以及宿主记忆中记录的 PFS 历史成果和边界。审计脚本盘点起点为：根目录只有一份项目 `AGENTS.md`；全局 `AGENTS.md` 为空；当时分支为 `main`，HEAD 为 `9ffeba6`，工作树有 117 项变更，较 `origin/main` 本地多 4 个提交。随后新增的本次洁癖记录、安装脚本回归和安装入口改动属于当前未提交工作树。2026-09-06 复核起点工作树共有 139 个状态项，发布修复和 Day 2 桌面回读记录后当前为 144 个状态项；本文件保留历史审计上下文，现役状态以 `docs/HANDOFF.md` 为准。

## 统一后的事实面

| 事实面 | 状态 | 收口结论 |
|---|---|---|
| 代码 | `changed-and-verified` | 当前工作树已有本地质量门、核心分析、聊天/Workflow 本地可靠性和 PFS UI 切片；变更尚未全部提交，不能把源码状态写成已发布。 |
| 运行态 | `verified-current` / `pending` | 本地 Flask、Docker arm64、Ego 桌面回读和固定数据分析已有证据；真实外部服务、Windows 实机、部署和 live 仍待验证。 |
| 文档 | `changed-and-verified` | `HANDOFF` 作为唯一现役入口；能力矩阵记录证据；`PROJECT_STATUS` 明确为历史台账；完整改造计划明确为长期蓝图。 |
| 规则 | `changed-and-verified` | 统一了 P0/P1 四天口径、状态分层、外部证据门槛、参考仓库使用边界和危险操作前置说明。 |
| 记忆 | `verified-current` | 已读取 PFS 相关宿主记忆和历史回顾；未改写生成式记忆，也未把宿主记忆当成当前运行证据。 |
| 工作区 | `changed-and-verified` | 已识别 139 项工作树状态项、忽略的运行数据、授权参考快照和发布候选；未在本次洁癖中删除任何用户数据或参考材料。 |

## 当前产品口径

- P0：可下载的 GitHub 源码仓库、macOS/Windows 本地启动、核心数据分析演示、安装说明和质量门。
- P1：在四天时间盒内尝试真实 MCP、Hooks、Microsoft Teams、飞书机器人、云端登录和线上部署；必须有目标环境的响应、页面或日志才能升级为真实验收。当前本地 `api/teams.py` 不等于 Microsoft Teams SaaS。
- 保留但暂不启用：本地 Workspace Teams、Hooks、GPU/远程执行、飞书机器人和云端登录代码。
- 已退役：商业画布、Google Sheets；不再列为待完成兼容项。
- 保留能力：PFS UI/品牌、CSV/XLSX 数据分析、轻量 `claims/evidence` 来源留痕、工具结果留痕、Artifact/任务历史和本地可靠性切片。
- 不纳入本轮门槛：多主机恢复、复制存储、生产级任意 in-flight 无损续跑、真实 Microsoft Teams SaaS/Graph、签名公证、复杂 Office 跨平台视觉和外部副作用幂等。

## 未做删除的候选

以下内容只登记，不在本次操作中删除：`.DS_Store`、`__pycache__/`、`.pytest_cache/`、`.ruff_cache/`、`outputs/`、`uploads/`、`auth.db`、项目 `memory/`、本地授权参考快照 `Data-Analysis-Agent-main/`、开发机上的历史 draw.io/Business Canvas 运行数据库，以及其他被忽略的运行态文件。它们可能包含用户数据、恢复状态、凭据或后续兼容复验所需材料；如需清理，必须先确认精确路径和可恢复性。

## 交付阻塞

1. 当前工作树仍有未提交变更；2026-09-06 执行 fetch 后，本地记录的 `origin/main` 仍为 `ffa0ec1`，不含本轮全部切片。
2. GitHub Actions 尚未取得本轮实际 runner 运行证据；Windows 安装包和干净系统安装尚未实机验收。
3. 根目录现有 `LICENSE` 仅明确“尚未授予公开许可证”的权利状态，不是最终开源许可证；仍不能擅自选定或复制参考仓库许可证。
4. 真实外部 MCP、Hooks、飞书、云端登录、线上部署需要目标凭证、项目或 canonical URL；本地 contract/fixture 不替代真实验收。

## 下一步

洁癖阶段已收口，P0 的安装链路、源码发布 staging 和本地质量门已开始并通过；2026-09-06 又为图片代理补上公网解析与禁止跳转门禁，staging 497 个文件、约 38 MB 且 artifact audit 为 0 findings。下一步按 `docs/HANDOFF.md` 继续处理 commit/push、Windows 实机/CI runner 和发布资产回读，再按时间盒尝试 P1 外部能力。每次完成后分别记录本地、commit、push、CI、release、deploy、live，不合并成一个“已完成”。
