<p align="right"><a href="./README.md">中文</a></p>

<p align="center">
  <img src="./docs/assets/pfs-repository-banner.svg" alt="PFS Data Analysis Agent" width="100%" />
</p>

<h1 align="center">PFS Data Analysis Agent</h1>

<p align="center">A local intelligent data analysis workbench.</p>

<p align="center">
  Connect files, databases, or controlled data sources; ask questions in natural language; run bounded analysis; and keep the result trail for review.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB.svg" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/Backend-Flask-111827.svg" alt="Flask" />
  <img src="https://img.shields.io/badge/Frontend-Vanilla%20JS%20%2B%20Vite-646CFF.svg" alt="Vanilla JS and Vite" />
  <img src="https://img.shields.io/badge/Desktop-macOS%20%2F%20Windows-0b5bd3.svg" alt="Desktop" />
</p>

> Current version: `0.1.0-dev`. This is a desktop-first development build focused on local startup and reviewable analysis flows.

PFS supports natural-language analysis, controlled data queries, chart generation, multi-format report delivery, and result-history review.

## Highlights

- Ask questions in natural language instead of starting with SQL.
- Inspect schema, coverage, and data-quality signals before running bounded queries.
- Keep run state, source references, lightweight result trace, artifact metadata, and download history.
- Produce charts, dashboards, JSON, CSV, Excel, Word, and PPT deliverables.
- Run locally on macOS or Windows with a simple launcher.
- Extend the Agent with models, Skills, knowledge, workspaces, workflows, and optional integrations.

## Core capabilities

| Area          | What PFS provides                                                                                                                                               |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Data          | CSV/XLSX upload, SQLite/MySQL/PostgreSQL/SQL Server entry points, and controlled HTTP sources                                                                   |
| Understanding | Table and field preview, coverage, missing/duplicate signals, and source snapshots                                                                              |
| Analysis      | Read-only SQL, grouped metrics, safe aggregations, anomaly detection, clustering, decision trees, and time-series analysis entry points                         |
| Visualization | Chart recommendations, interactive charts, and a Dashboard delivery entry point                                                                                 |
| Agent         | SSE chat, tool calls, Skills, knowledge base, workflows, jobs, and context management                                                                           |
| Extensions    | MCP, Teams, Hooks, Feishu, and cloud-login interfaces are retained as optional surfaces; remote GPU is off by default and requires explicit local configuration |

## Quick demo

1. Start PFS and open `http://127.0.0.1:5001`.
2. Create a conversation and upload `data/fixtures/pfs_sales.csv`.
3. Select the table and inspect its fields, row count, date range, and dimensions.
4. Ask: `Summarize sales by region and identify the top region.`
5. Review the result and download JSON or CSV.

The bundled fixture contains 9 rows, 3 months, 3 regions, and a total sales amount of `100,000`. This deterministic path does not require an external model; configure a supported model to exercise the open-ended Agent loop.

## Interface preview

The image below shows the current Flask workbench, captured from an isolated local service at a `1280×720` desktop viewport. The root `index.html` remains a static design preview and is not the runtime application entry point.

![PFS Data Analysis Agent workbench](./docs/assets/pfs-workbench-overview.png)

Showcase path: start the app → upload `data/fixtures/pfs_sales.csv` → open metric preview → run the analysis → review the result summary and source snapshot → create a deliverable → revisit it in task history.

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

Open `http://127.0.0.1:5001`. Docker is not required for the normal local path.

If dependencies are already installed:

```bash
python app.py
```

### Docker

Docker is provided for development, image verification, and deployment attempts:

```bash
docker build -t pfs-data-analysis-agent:local .
docker run --rm -p 5001:5001 pfs-data-analysis-agent:local
```

Keep API keys out of Dockerfiles, images, and Git. See `docker-compose.yml` for the optional API/worker setup.

## Documentation

- [Product overview](PRODUCT.md): positioning, capabilities, interface, and runtime
- [User guide](Information/Instruction.md): from data connection to result review
- [Knowledge base](Information/repository_tutorial.md): business context and analysis rules
- [MCP guide](Information/MCP_tutorial.md): optional external tool connections
- [Release notes](Information/Version_Update_Log_EN.md)
- [License](LICENSE) · [Security policy](SECURITY.md) · [Rights and third-party notice](NOTICE.md)

## Slash commands

| Command      | Purpose                                  |
| ------------ | ---------------------------------------- |
| `/new`       | Start a clean analysis conversation      |
| `/sessions`  | View or refresh saved conversations      |
| `/data`      | Open data preview and table selection    |
| `/status`    | Inspect model, source, and context state |
| `/jobs`      | View task history and status             |
| `/skills`    | View or select Skills                    |
| `/knowledge` | Open the knowledge base                  |
| `/mcp`       | Manage MCP connections and tools         |
| `/workspace` | Manage workspace and permissions         |
| `/stop`      | Stop the current response                |
| `/compact`   | Compact the current context              |
| `/help`      | Show command help                        |

`/teams` and `/robot` are optional extension surfaces. External connections depend on the target environment, credentials, and explicit user configuration; no external call starts without a target and credentials.

## Examples

### Analyze grouped data

```text
Summarize sales by region, identify the top region, and recommend a clear chart.
```

PFS inspects fields and scope first, then runs a controlled query and returns grouped results with source information.

### Check data quality

```text
Check missing values, duplicate records, date coverage, and unusual amounts. Explain which issues could affect the result.
```

Quality signals stay separate from conclusions; original records are not silently removed and unresolved items remain visible.

### Create deliverables

```text
Turn this analysis into an Excel file and a PPT deck while keeping the source, analysis definition, and conclusion.
```

Deliverables are registered in the current conversation with source and run metadata. Complex file content and cross-platform rendering should be checked in the target environment.

## Models

The public built-in catalog currently contains:

- DeepSeek
- Kimi and Kimi Coding Plan
- GLM and GLM Coding Plan
- MiniMax and MiniMax Coding Plan
- Custom OpenAI-compatible providers

Keep model keys in local configuration or environment variables. Other legacy provider identifiers are not part of the default catalog.

## Version and platform boundary

The current version targets local use on Windows x64 and macOS Apple Silicon. No macOS Intel installer is provided. Final installers and version numbers are published on [GitHub Releases](https://github.com/Lukanytsu7551/PFS-data-analysis-agent/releases).

Business Canvas and Google Sheets are outside the current product scope. MCP, Teams, Hooks, Feishu, and cloud login remain optional extension surfaces. GPU/remote execution is retained but disabled by default and requires explicit local configuration.

The current version is designed for structured data files and database connections; image upload and visual analysis are not part of the current capability set. Availability of models, databases, external services, and local dependencies depends on the target environment, and a fixed fixture does not replace validation with real data.

## Development checks

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests -p 'test_*.py' -q
.venv/bin/ruff check .
pnpm run format:check
pnpm run lint
pnpm run build:check
git diff --check
```

## Rights and security

This repository uses the custom non-commercial license in [`LICENSE`](LICENSE): learning, research, non-commercial use, modification, and redistribution are allowed with attribution; commercial use requires prior written authorization from the copyright holder. Software, dependencies, fonts, icons, and other materials remain governed by their applicable copyright, license, or written authorization. Read [`NOTICE.md`](NOTICE.md) before using or redistributing the repository. See [`SECURITY.md`](SECURITY.md) for security reports.
