<p align="right"><a href="./README.md">中文</a></p>

<p align="center">
  <img src="./docs/assets/pfs-repository-banner.svg" alt="PFS Data Analysis Agent" width="100%" />
</p>

<h1 align="center">PFS Data Analysis Agent</h1>

<p align="center">A traceable, reviewable, and deliverable workbench for report and operating-data analysis.</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB.svg" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/Backend-Flask-111827.svg" alt="Flask" />
  <img src="https://img.shields.io/badge/Frontend-Vanilla%20JS%20%2B%20Vite-646CFF.svg" alt="Vanilla JS and Vite" />
  <img src="https://img.shields.io/badge/Desktop-macOS%20%2F%20Windows-0f766e.svg" alt="Desktop" />
</p>

> PFS Data Analysis Agent is a local intelligent workbench for report and operating-data analysis, with natural-language analysis, controlled data queries, chart generation, multi-format report delivery, and result history.

> PFS Data Analysis Agent is an independently maintained local workbench for report and operating-data analysis. Product capabilities, runtime identity, and release status are defined by this repository; source, authorization, and redistribution boundaries are recorded separately in [`NOTICE.md`](NOTICE.md) and [`LICENSE`](LICENSE).

## Highlights

- Ask business questions in natural language instead of starting with SQL.
- Connect or upload data, inspect schema and quality signals, and run bounded analysis.
- Produce charts, JSON/CSV results, Excel, Word, PPT, and Dashboard artifacts.
- Keep lightweight claim/evidence source references, run state, artifact metadata, and download history.
- Use a desktop-first local workflow with macOS and Windows source launchers.
- Extend the agent with models, Skills, knowledge, workspaces, MCP, teams, hooks, Feishu, and workflows as those integrations are separately verified.

## Quick start

Python 3.10+ is required. From the repository directory:

```bash
# macOS
./install.sh
./start.command
```

```powershell
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File .\install.ps1
.\start.bat
```

Open `http://127.0.0.1:5001`. The normal user path does not require Docker.

## Local demo

1. Start PFS and create a new conversation.
2. Upload `data/fixtures/pfs_sales.csv`.
3. Select `pfs_sales` and inspect its fields and date range.
4. Ask: `Summarize sales by region and identify the top region.`
5. Review the result, source information, and downloadable JSON or CSV artifact.

The fixture contains 9 rows, 3 months, 3 regions, and a total sales amount of `100,000`. This deterministic path does not require an external model. Configure a supported model when you want to exercise the open-ended Agent loop.

## Core capabilities

| Area | What PFS provides |
|---|---|
| Data | CSV/XLSX upload, SQLite/MySQL/PostgreSQL/SQL Server entry points, and controlled HTTP sources |
| Analysis | Read-only SQL, grouped metrics, versioned metric catalog, safe aggregations, statistical analysis, and forecast evaluation entry points |
| Visualization | 41 registered chart IDs and a Dashboard delivery entry point; fixture-level generation smoke tests are available |
| Delivery | JSON, CSV, Excel, Word, PPT, and Dashboard artifacts with local history metadata |
| Agent surface | SSE chat, Skills, knowledge base, workspace permissions, jobs, workflows, MCP, teams, hooks, and optional Feishu/cloud surfaces |

## Supported models

The public built-in catalog currently contains:

- DeepSeek
- Kimi and Kimi Coding Plan
- GLM and GLM Coding Plan
- MiniMax and MiniMax Coding Plan
- Custom OpenAI-compatible providers

OpenAI / ChatGPT, AtlasCloud, and Ollama remain cleanup-only compatibility identifiers for old local configuration. They are not restored to the default catalog, session selector, or fallback chain.

## Slash commands

Useful local commands include `/new`, `/sessions`, `/data`, `/status`, `/jobs`, `/skills`, `/knowledge`, `/mcp`, `/workspace`, `/stop`, `/compact`, and `/help`. `/teams` and `/robot` are retained optional surfaces; local contracts do not prove Microsoft Teams or Feishu SaaS acceptance.

## Verification boundary

The current P0 delivery target is a downloadable source repository, local macOS/Windows startup, and a demonstrable core data-analysis loop. Local tests and fixed fixtures are not equivalent to production data, external-service acceptance, deployment, or live verification.

See:

- [`docs/HANDOFF.md`](docs/HANDOFF.md) — current handoff and remaining order
- [`docs/FUNCTION_COMPATIBILITY_MATRIX.md`](docs/FUNCTION_COMPATIBILITY_MATRIX.md) — feature-by-feature evidence
- [`PRODUCT.md`](PRODUCT.md) — product specification
- [`SECURITY.md`](SECURITY.md) — security reporting and runtime boundaries

## Development checks

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py' -q
.venv/bin/ruff check .
pnpm run format:check
pnpm run lint
pnpm run build:check
```

## Attribution and license

PFS is based on the authorized [Data-Analysis-Agent](https://github.com/Zafer-Liu/Data-Analysis-Agent) transformation effort. Original source code, third-party resources, copyright notices, and applicable licenses retain their own boundaries. PFS-specific product code and documentation are maintained in this repository.

The final public license for PFS has not yet been selected. Do not infer commercial redistribution rights from this README. See [`NOTICE.md`](NOTICE.md) for the current source and authorization record.
