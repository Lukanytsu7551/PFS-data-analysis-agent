# PFS Release Notes

## 0.1.0-dev · 2026-09-09

### Product capabilities

- Added the PFS local runtime entry points, application identity, icons, and desktop workbench.
- Added CSV/XLSX upload, data preview, field inspection, quality signals, and controlled analysis.
- Added natural-language chat, read-only queries, charts, Dashboard, JSON, CSV, Excel, Word, and PPT deliverables.
- Added conversations, jobs, workspaces, Skills, knowledge, workflows, and lightweight source trace.
- Added DeepSeek, Kimi, GLM, MiniMax, their Coding Plan variants, and custom OpenAI-compatible providers.
- Added MCP, Teams, Hooks, Feishu, and cloud-login surfaces; they are available by default. GPU/remote execution remains off by default.

### Validation status

- The current acceptance set for Excel, Word, and PPT deliverables has completed visual review.
- Local startup, CSV/XLSX analysis, charts, deliverables, jobs, and workspace basics follow the project quality gates.
- Windows x64 and macOS Apple Silicon are the target platforms; no macOS Intel installer is provided.

### Current boundary

- Business Canvas and Google Sheets are outside the current product scope.
- MCP, Teams, Hooks, Feishu, and cloud login remain optional extensions; GPU/remote execution is disabled by default.
- Image upload and visual analysis are not part of the current version; local samples and offline tests do not promise production data quality or external-service availability.
