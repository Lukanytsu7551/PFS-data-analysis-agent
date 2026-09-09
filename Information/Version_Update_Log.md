# PFS 版本更新日志

## 0.1.0 · 2026-09-09

### 产品能力

- 建立 PFS 数据分析 Agent 的本地运行入口、应用身份、图标和桌面工作台。
- 支持 CSV/XLSX 数据上传、数据预览、字段识别、质量提示和受控分析。
- 支持自然语言对话、只读查询、图表、Dashboard、JSON、CSV、Excel、Word 和 PPT 交付物。
- 支持会话、任务、工作区、Skills、知识库、Workflow 和轻量来源留痕。
- 内置 DeepSeek、Kimi、GLM、MiniMax 及其 Coding Plan，并支持自定义 OpenAI-compatible provider。
- 提供 MCP、Teams、Hooks、飞书和云端登录扩展入口，默认可用；GPU/远程执行默认关闭。

### 验证状态

- 已完成当前验收集的 Excel、Word、PPT 交付物视觉检查。
- 本地启动、CSV/XLSX 分析、图表、交付物、任务和工作区基础链路按项目质量门检查。
- Windows x64 和 macOS Apple Silicon 为当前目标平台；macOS Intel 不在安装包范围内。

### 当前边界

- 商业画布和 Google Sheets 已退出产品范围。
- MCP、Teams、Hooks、飞书和云端登录保留为可选扩展，GPU/远程执行默认关闭。
- 当前版本不提供图片上传或视觉分析；本地样例和离线测试不代表生产数据质量或外部服务可用性。
