# PFS Release Notes

## 0.1.0-dev · 2026-09-06

### Product capabilities

- Added the PFS local runtime entry points, application identity, icons, and desktop workbench.
- Added CSV/XLSX upload, data preview, field inspection, quality signals, and controlled analysis.
- Added natural-language chat, read-only queries, charts, Dashboard, JSON, CSV, Excel, Word, and PPT deliverables.
- Added conversations, jobs, workspaces, Skills, knowledge, workflows, and lightweight source trace.
- Added DeepSeek, Kimi, GLM, MiniMax, their Coding Plan variants, and custom OpenAI-compatible providers.
- Added MCP, Teams, Hooks, Feishu, and cloud-login surfaces; they are available by default. GPU/remote execution remains off by default.

### Validation status

- macOS startup, core chat, CSV/XLSX analysis, charts, deliverables, jobs, and workspace basics have local evidence.
- Code, frontend formatting, static checks, and offline regression tests follow the development gates.
- The latest successful CI run, `34029259331`, validated commit `b2a6393` for Windows x64, macOS Apple Silicon, a clean Windows source install, and both desktop packages. The current `main` commit is `1cc49c9` and has not yet run CI at the same SHA.

### Current boundary

- Physical Windows/macOS installation, a formal Release, real external services, deployment, and live access require separate checks in their target environments.
- Local samples and offline tests do not promise production data quality, external-service availability, or live service operation.
