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
- CI run `34191614452` validated the remote release-candidate tree `ae8142c0a6754ed7635eea9ffece550979833d01` for Windows x64, macOS Apple Silicon, a clean Windows source install, frontend quality, and both desktop packages. The latest SQL compatibility fix is now included in the current release slice; a new same-SHA CI result is still required before the formal Release.

### Current boundary

- Physical Windows/macOS installation, a formal Release, real external services, deployment, and live access require separate checks in their target environments.
- Local samples and offline tests do not promise production data quality, external-service availability, or live service operation.
