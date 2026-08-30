# PFS 版本开发日志

## 0.1.0-dev · 2026-08-28

### 已完成

- 建立 PFS 根目录运行源码边界；运行时不依赖嵌套参考快照中的密钥、SQLite 状态或上传数据。
- 加入 PFS 产品身份配置、独立 SVG 图标、服务标识、登录页和主聊天页品牌入口。
- 将 README、产品规格、使用说明、安全策略和用户协议改写为 PFS 版本，并保留授权/版权材料。
- 将更新检查改为 PFS 自有发布通道配置，默认关闭外部更新访问，避免开发阶段误连未配置的发布服务。
- 建立 `pfs_agent` 工具契约与确定性策略门，并接入 Agent 的只读/计算工具预分发检查。
- 增加固定销售报表 fixture、指标契约、数据快照、Claim、Evidence 和只读预览 API。
- 增加 Evidence Ledger 第一段契约：URL+片段身份、批量幂等登记、Claim 关联、冲突待裁决检测和原子 JSON 持久化适配器。
- 增加当前会话上传 CSV 的显式指标列分析入口、数据源选择器和受限报表 API；计算仍由确定性 Decimal SUM 完成。
- 桌面安装图标替换为 PFS 多尺寸图标，并将 Windows PyInstaller 图标纳入审核 staging。
- 修复前端现存的格式和 ESLint 质量门问题，重建聊天 bundle。

### 已验证

- PFS 离线单元测试、原图表选择器测试：21 项通过。
- 本轮修改的 Python 模块通过 `py_compile`。
- 前端 `prettier --check`、`eslint`、聊天 bundle 校验通过。
- 在隔离 `.venv` 中完成 Flask test client 与 Waitress 本地 HTTP 回读；PFS 健康检查、固定报表、能力清单、Ledger、上传 CSV 分析均返回预期结果。
- 发布 staging 通过 artifact audit：3868 个文件、183143559 bytes、0 findings；PFS fixture、Windows 图标、PFS 打包 spec 和聊天 bundle 已纳入 staging。

### 尚未验证

- Flask 完整启动、模型连接、真实 XLSX/数据库连接和 SSE 长任务。
- Docker、队列、外部 MCP、Feishu、桌面打包、重启恢复、部署和线上登录流程。
- Evidence 自动消费核心事件、语义核验、完整报表 UI 和正式评估数据集。
