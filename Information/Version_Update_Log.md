# PFS 版本更新日志

## 0.1.0-dev · 2026-09-06

### 产品能力

- 建立 PFS 数据分析 Agent 的本地运行入口、应用身份、图标和桌面工作台。
- 支持 CSV/XLSX 数据上传、数据预览、字段识别、质量提示和受控分析。
- 支持自然语言对话、只读查询、图表、Dashboard、JSON、CSV、Excel、Word 和 PPT 交付物。
- 支持会话、任务、工作区、Skills、知识库、Workflow 和轻量来源留痕。
- 内置 DeepSeek、Kimi、GLM、MiniMax 及其 Coding Plan，并支持自定义 OpenAI-compatible provider。
- 提供 MCP、Teams、Hooks、飞书和云端登录扩展入口，默认可用；GPU/远程执行默认关闭。

### 验证状态

- macOS 本地启动、核心对话、CSV/XLSX 分析、图表、交付物、任务和工作区基础链路已有本地证据。
- 代码、前端格式、静态检查和离线回归测试按开发门禁执行。
- 历史 CI run `34029259331` 验证过提交 `b2a6393` 的 Windows x64、macOS Apple Silicon、Windows 干净源码安装和两端打包；当前工作区已进入新的运行基线收口，新的提交和同 SHA CI 尚待生成。

### 当前边界

- 物理 Windows/macOS 安装、正式 Release、真实外部服务、部署和线上访问需要在对应环境单独确认。
- 本地样例和离线测试不代表生产数据质量、外部服务可用性或线上服务承诺。
