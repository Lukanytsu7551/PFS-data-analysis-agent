# PFS Release Notes

## 0.1.0-dev · 2026-09-06

### Product capabilities

- Added the PFS local runtime entry points, application identity, icons, and desktop workbench.
- Added CSV/XLSX upload, data preview, field inspection, quality signals, and controlled analysis.
- Added natural-language chat, read-only queries, charts, Dashboard, JSON, CSV, Excel, Word, and PPT deliverables.
- Added conversations, jobs, workspaces, Skills, knowledge, workflows, and lightweight source trace.
- Added DeepSeek, Kimi, GLM, MiniMax, their Coding Plan variants, and custom OpenAI-compatible providers.
- Added optional MCP, Teams, Hooks, Feishu, GPU/remote execution, and cloud-login surfaces; all remain off by default.

### Local checks

- macOS startup, core chat, CSV/XLSX analysis, charts, deliverables, jobs, and workspace basics have local evidence.
- Code, frontend formatting, static checks, and offline regression tests follow the development gates.

### Current boundary

- Clean Windows installation, CI/Release downloads, real external services, deployment, and live access require separate checks in their target environments.
- Local samples and offline tests do not promise production data quality, external-service availability, or live service operation.
