# PFS Development Log

## 0.1.0-dev · 2026-08-28

### Implemented

- Established the PFS runtime source boundary without carrying secrets, SQLite runtime state, or uploaded data from the local reference snapshot.
- Added PFS product identity, an independent SVG mark, service identifier, login branding, and chat-page branding.
- Rewrote the README, product specification, usage guide, security policy, and user/data-processing note for PFS while retaining license materials.
- Switched update checks to an explicitly configured PFS release channel, disabled by default during development.
- Added PFS tool contracts and a deterministic policy gate for the first read/compute tool slice.
- Added a fixed sales-report fixture, metric contract, data snapshot, claims, evidence records, and a read-only preview API.
- Added the first Evidence Ledger contract: URL-plus-snippet identity, idempotent batch registration, claim links, conflict detection, and an atomic JSON persistence adapter.
- Added an explicit metric-column analysis endpoint and source selector for CSV files uploaded in the current session; calculations remain deterministic Decimal sums.
- Replaced the desktop installer artwork with a multi-size PFS icon and staged the Windows PyInstaller icon for audit.
- Fixed existing frontend format and ESLint gate issues, then rebuilt the chat bundle.

### Verified

- 21 offline unit tests covering PFS contracts, the runtime adapter, reporting contracts, the Evidence Ledger, and chart selection passed.
- Modified Python modules passed `py_compile`.
- Prettier check, ESLint, and chat-bundle verification passed.
- Flask test-client and Waitress local HTTP readbacks passed in the isolated `.venv`; PFS health, fixture, capability, ledger, and uploaded-CSV analysis responses matched expectations.
- Release staging passed artifact audit with 3,868 files, 183,143,559 bytes, and no findings; the PFS fixture, Windows icon, PFS packaging spec, and chat bundle are included.

### Not yet verified

- Full Flask startup, model connections, real XLSX/database connections, and SSE long-running jobs.
- Docker, queues, external MCP, Feishu, desktop packaging, restart recovery, deployment, and live login flow.
- Automatic Evidence consumption, semantic verification, complete reporting UI, and formal evaluation datasets.
