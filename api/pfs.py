"""PFS report, data-source, and evidence-ledger endpoints.

The first PFS vertical slice keeps calculation deterministic and bounded. It
    can preview the reviewed fixture or analyze a CSV/XLSX already mounted in the
    current session. The ledger API uses the same contracts as the offline tests
and stores only user-scoped metadata under the runtime data directory.
"""

from __future__ import annotations

from collections.abc import Mapping
import csv
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlparse
import uuid

from flask import Blueprint, Response, jsonify, request

from .state import chart_store, require_session_ownership, session_manager
from infrastructure.paths import data_path, resource_path
from pfs_agent.ledger import (
    ClaimRecord,
    EvidenceCandidate,
    LedgerError,
    PersistentEvidenceLedger,
)
from pfs_agent.reporting import (
    AnalysisRequest,
    MetricContract,
    ReportingContractError,
    analyze_csv,
    analyze_file,
    list_xlsx_worksheets,
    load_tabular_snapshot,
)
from pfs_agent.query import QueryInterpretationError, parse_report_question


bp = Blueprint("pfs", __name__)

_FIXTURE_METRIC = MetricContract(
    metric_id="sales_amount",
    label="销售额",
    formula="SUM(sales_amount)",
    value_column="sales_amount",
    date_column="month",
    dimension="region",
    grain="month",
    version="v1",
)


def _bounded(value: object, field: str, *, limit: int = 160, required: bool = False) -> str:
    if value is None:
        text = ""
    elif isinstance(value, (str, int, float)):
        text = str(value).strip()
    else:
        raise ReportingContractError(f"{field} must be a string")
    if required and not text:
        raise ReportingContractError(f"{field} must not be empty")
    if len(text) > limit:
        raise ReportingContractError(f"{field} is too long")
    return text


def _body() -> dict:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ReportingContractError("JSON body must be an object")
    return payload


def _metric_from_payload(payload: Mapping[str, object], columns: tuple[str, ...]) -> MetricContract:
    value_column = _bounded(
        payload.get("value_column") or "sales_amount",
        "value_column",
        limit=120,
        required=True,
    )
    date_column = _bounded(
        payload.get("date_column") or "month",
        "date_column",
        limit=120,
        required=True,
    )
    dimension = _bounded(
        payload.get("dimension") or "region",
        "dimension",
        limit=120,
        required=True,
    )
    missing = {value_column, date_column, dimension} - set(columns)
    if missing:
        raise ReportingContractError("metric columns missing from CSV source: " + ", ".join(sorted(missing)))
    metric_id = _bounded(payload.get("metric_id") or value_column, "metric_id", limit=120, required=True)
    label = _bounded(payload.get("label") or value_column, "label", limit=120, required=True)
    grain = _bounded(payload.get("grain") or "month", "grain", limit=40, required=True)
    version = _bounded(payload.get("version") or "v1", "version", limit=40, required=True)
    # Formula is metadata only in this deterministic slice; calculation is
    # always Decimal SUM over the selected value column.
    return MetricContract(
        metric_id=metric_id,
        label=label,
        formula=f"SUM({value_column})",
        value_column=value_column,
        date_column=date_column,
        dimension=dimension,
        grain=grain,
        version=version,
    )


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _session_tabular_source(sid: str, source_id: str) -> tuple[Path, object]:
    sess = session_manager.get(sid)
    if not sess:
        raise ReportingContractError("session does not exist")
    source_id = _bounded(source_id, "source_id", limit=160, required=True)
    entry = next(
        (item for item in getattr(sess, "_sources", ()) if str(item.get("id") or "") == source_id),
        None,
    )
    if not entry:
        raise ReportingContractError("tabular source does not exist in this session")
    source = entry.get("source")
    raw_path = str(getattr(source, "file_path", "") or "")
    path = Path(raw_path).resolve(strict=False)
    upload_root = data_path("uploads").resolve(strict=False)
    if not _path_within(path, upload_root):
        raise ReportingContractError("PFS session analysis only accepts uploaded CSV/XLSX files")
    if path.suffix.lower() not in {".csv", ".xlsx"}:
        raise ReportingContractError("PFS deterministic analysis accepts CSV or XLSX files only")
    if not path.is_file():
        raise ReportingContractError("CSV source file no longer exists")
    return path, source


def _ledger() -> PersistentEvidenceLedger:
    scope = "local"
    try:
        from .auth import current_user, is_cloud_managed

        if is_cloud_managed():
            user = current_user()
            user_id = str((user or {}).get("id") or "anonymous")
            scope = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]
    except (ImportError, RuntimeError):
        # The helper is called only inside a request. Keeping the fallback
        # makes the dependency-free contract usable in local adapter tests.
        pass
    return PersistentEvidenceLedger(data_path("outputs", "pfs", "ledger", f"{scope}.json"))


def _error(exc: Exception, status: int = 400):
    code = getattr(exc, "code", type(exc).__name__)
    return jsonify({"ok": False, "error": str(exc), "code": code}), status


def _worksheet_from_payload(payload: Mapping[str, object]) -> str:
    return _bounded(payload.get("worksheet"), "worksheet", limit=120)


def _export_filename(run_id: str, output_format: str) -> str:
    """Build a stable, header-safe download name from an opaque run id."""
    safe_run_id = re.sub(r"[^A-Za-z0-9_-]+", "-", str(run_id or "pfs-report"))[:48].strip("-")
    return f"pfs-report-{safe_run_id or 'result'}.{output_format}"


def _csv_export(result: object) -> str:
    """Flatten the report contract without dropping its audit references."""
    payload = result.to_dict()
    metric = payload["metric"]
    snapshot = payload["snapshot"]
    request_data = payload["request"]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["section", "field", "value", "detail"])
    writer.writerow(["summary", "metric", metric["label"], metric["formula"]])
    writer.writerow(["summary", "total", payload["total"], ""])
    writer.writerow(
        [
            "summary",
            "coverage",
            snapshot["row_count"],
            f"{request_data.get('date_from') or snapshot.get('min_date') or '全部'}"
            f"..{request_data.get('date_to') or snapshot.get('max_date') or '全部'}",
        ]
    )
    writer.writerow(["summary", "source", snapshot["file_name"], snapshot["content_sha256"]])
    for group in payload["groups"]:
        writer.writerow(["group", metric["dimension"], group["dimension"], group["value"]])
    for claim in payload["claims"]:
        writer.writerow(
            [
                "claim",
                claim["claim_id"],
                claim["text"],
                f"status={claim.get('status', '')}; evidence={','.join(claim.get('evidence_ids', []))}",
            ]
        )
    for evidence in payload["evidence"]:
        writer.writerow(["evidence", evidence["evidence_id"], evidence["locator"], evidence["excerpt"]])
    for warning in payload.get("warnings", []):
        writer.writerow(["warning", "warning", warning, ""])
    return output.getvalue()


def _export_response(result: object, output_format: str) -> Response:
    """Return a downloadable, server-recomputed report artifact."""
    payload = result.to_dict()
    filename = _export_filename(payload["run_id"], output_format)
    if output_format == "json":
        body = json.dumps({"ok": True, "result": payload}, ensure_ascii=False, indent=2).encode("utf-8")
        content_type = "application/json; charset=utf-8"
    else:
        body = _csv_export(result).encode("utf-8-sig")
        content_type = "text/csv; charset=utf-8"
    return Response(
        body,
        content_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-PFS-Report-Run": payload["run_id"],
            "X-PFS-Source-SHA256": payload["snapshot"]["content_sha256"],
        },
    )


def _export_format(payload: Mapping[str, object]) -> str:
    output_format = _bounded(payload.get("format") or "json", "format", limit=12).lower()
    if output_format not in {"json", "csv"}:
        raise ReportingContractError("format must be json or csv")
    return output_format


def _delivery_format(payload: Mapping[str, object]) -> str:
    output_format = _bounded(payload.get("format"), "format", limit=16, required=True).lower()
    if output_format not in {"xlsx", "docx", "pptx", "dashboard"}:
        raise ReportingContractError("format must be xlsx, docx, pptx, or dashboard")
    return output_format


def _sql_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _report_sections(result: object) -> list[dict[str, str]]:
    payload = result.to_dict()
    metric = payload["metric"]
    snapshot = payload["snapshot"]
    request_data = payload["request"]
    coverage = (
        f"{request_data.get('date_from') or snapshot.get('min_date') or '全部'}"
        f" 至 {request_data.get('date_to') or snapshot.get('max_date') or '全部'}"
    )
    groups = "\n".join(
        f"{item['rank']}. {item['dimension']}：{item['value']}"
        for item in payload.get("groups", [])
    ) or "没有可展示的分组结果。"
    claims = "\n".join(
        f"- {item['text']}（{item.get('status') or 'unverified'}，置信度 {item.get('confidence', 0):.0%}）"
        for item in payload.get("claims", [])
    ) or "没有可展示的关键结论。"
    evidence = "\n".join(
        f"- {item['evidence_id']}：{item['excerpt']}"
        for item in payload.get("evidence", [])
    ) or "没有登记证据。"
    return [
        {
            "heading": "口径与数据快照",
            "content": (
                f"指标：{metric['label']}\n公式：{metric['formula']}\n"
                f"覆盖：{snapshot['row_count']} 行，{coverage}\n"
                f"来源：{snapshot['file_name']}\nSHA-256：{snapshot['content_sha256']}"
            ),
        },
        {"heading": "分组结果", "content": groups},
        {"heading": "关键结论", "content": claims},
        {"heading": "证据登记", "content": evidence},
    ]


def _ppt_slides(result: object) -> list[dict]:
    payload = result.to_dict()
    metric = payload["metric"]
    snapshot = payload["snapshot"]
    groups = payload.get("groups", [])
    top_group = groups[0] if groups else {"dimension": "—", "value": 0}
    return [
        {
            "layout": "cover",
            "params": {
                "title": f"PFS {metric['label']}分析",
                "subtitle": f"{snapshot['file_name']} · 可追踪报表交付物",
            },
        },
        {
            "layout": "metric_cards",
            "params": {
                "title": "核心指标",
                "cards": [
                    ["Σ", "指标合计", f"{payload['total']:,}\n{metric['formula']}"],
                    ["#", "纳入数据", f"{snapshot['row_count']} 行\n{snapshot.get('min_date') or '全部'} 至 {snapshot.get('max_date') or '全部'}"],
                    ["1", "最高分组", f"{top_group['dimension']}\n{top_group['value']:,}"],
                ],
                "source": f"PFS · SHA-256 {snapshot['content_sha256'][:16]}",
            },
        },
        {
            "layout": "data_table",
            "params": {
                "title": f"按 {metric['dimension']} 分组",
                "headers": ["排名", metric["dimension"], metric["label"]],
                "rows": [[str(item["rank"]), str(item["dimension"]), f"{item['value']:,}"] for item in groups],
                "source": snapshot["file_name"],
            },
        },
        {
            "layout": "closing",
            "params": {
                "title": "结论与核验",
                "message": "\n".join(item["text"] for item in payload.get("claims", [])) or "分析完成",
                "source_text": f"PFS 数据分析 Agent · {payload['run_id']}",
            },
        },
    ]


def _delivery_table(result: object, data_source: object) -> str:
    """Resolve the exact source table represented by the analyzed snapshot."""
    payload = result.to_dict()
    snapshot = payload["snapshot"]
    tables = list(data_source.list_tables() or [])
    if not tables:
        raise ReportingContractError(
            "data source has no exportable table", code="delivery_table_missing"
        )

    worksheet = str(snapshot.get("worksheet") or "").strip()
    if not worksheet:
        if len(tables) == 1:
            return tables[0]
        raise ReportingContractError(
            "report snapshot does not identify an exportable table",
            code="delivery_table_ambiguous",
        )

    normalized = re.sub(r"[^\w]+", "_", worksheet, flags=re.UNICODE).strip("_")
    if normalized and normalized[0].isdigit():
        normalized = "_" + normalized
    matches = [table for table in tables if table == worksheet or table == normalized]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ReportingContractError(
            f"worksheet maps to multiple export tables: {worksheet}",
            code="delivery_table_ambiguous",
        )
    raise ReportingContractError(
        f"selected worksheet is unavailable for delivery: {worksheet}",
        code="delivery_table_missing",
    )


def _dashboard_widgets(result: object, table_name: str) -> list[dict]:
    payload = result.to_dict()
    metric = payload["metric"]
    request_data = payload["request"]
    table = _sql_identifier(table_name)
    value = _sql_identifier(metric["value_column"])
    dimension = _sql_identifier(metric["dimension"])
    date_column = _sql_identifier(metric["date_column"])
    filters = []
    if request_data.get("date_from"):
        filters.append(f"{date_column} >= '{str(request_data['date_from']).replace(chr(39), chr(39) * 2)}'")
    if request_data.get("date_to"):
        filters.append(f"{date_column} <= '{str(request_data['date_to']).replace(chr(39), chr(39) * 2)}'")
    where = f" WHERE {' AND '.join(filters)}" if filters else ""
    return [
        {
            "id": "pfs-total",
            "title": f"{metric['label']}合计",
            "chart_type": "KPI_Card",
            "sql": f"SELECT SUM({value}) AS total_value FROM {table}{where}",
            "field_mapping": {},
            "grid": {"x": 0, "y": 0, "w": 4, "h": 2},
        },
        {
            "id": "pfs-groups",
            "title": f"按 {metric['dimension']} 分组的{metric['label']}",
            "chart_type": "Bar_Chart",
            "sql": (
                f"SELECT {dimension} AS group_name, SUM({value}) AS total_value "
                f"FROM {table}{where} GROUP BY 1 ORDER BY total_value DESC"
            ),
            "field_mapping": {"x": "group_name", "y": "total_value"},
            "grid": {"x": 0, "y": 2, "w": 8, "h": 4},
        },
    ]


def _tool_links(tool_result: str) -> list[tuple[str, str]]:
    return [
        (label.strip(), url.strip())
        for label, url in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", str(tool_result or ""))
    ]


def _delivery_artifacts(result: object, data_source: object, sid: str, output_format: str) -> dict:
    from agent.agent import BusinessAgent

    payload = result.to_dict()
    metric = payload["metric"]
    selected_table = _delivery_table(result, data_source)
    run_id = re.sub(r"[^A-Za-z0-9_-]+", "-", payload["run_id"])[:40].strip("-") or "report"
    agent = BusinessAgent(
        client=None,
        model="pfs-deterministic-export",
        data_source=data_source,
        chart_store=chart_store,
        session_id=sid,
        color_scheme="pfs",
    )
    if output_format == "xlsx":
        tool_result = agent._tool_export_excel([selected_table], f"pfs-report-{run_id}")
    elif output_format == "docx":
        tool_result = agent._tool_export_report(f"PFS {metric['label']}分析报告", _report_sections(result))
    elif output_format == "pptx":
        tool_result = agent._tool_generate_ppt(
            f"PFS {metric['label']}分析",
            _ppt_slides(result),
            f"pfs-report-{run_id}",
        )
    else:
        tool_result = agent._tool_generate_dashboard(
            f"PFS {metric['label']}分析看板",
            _dashboard_widgets(result, selected_table),
            "pfs",
        )
    if str(tool_result).startswith("❌"):
        raise ReportingContractError(str(tool_result).removeprefix("❌").strip())
    artifacts = []
    for label, url in _tool_links(str(tool_result)):
        path_name = Path(unquote(urlparse(url).path)).name
        kind = "dashboard" if "/dashboard/" in url and "/api/" not in url else output_format
        artifacts.append(
            {
                "type": kind,
                "name": path_name or re.sub(r"^[^\w]+", "", label) or f"PFS {output_format}",
                "label": re.sub(r"^[^\w\u4e00-\u9fff]+", "", label),
                "url": url,
                "action": "open" if kind == "dashboard" else "download",
            }
        )
    if not artifacts:
        raise ReportingContractError("export completed without a downloadable artifact")
    return {"ok": True, "format": output_format, "artifacts": artifacts, "message": str(tool_result)}


def _fixture_export_result(payload: Mapping[str, object]) -> object:
    question = _bounded(payload.get("question"), "question", limit=500)
    if question:
        raise ReportingContractError("fixture export does not accept a natural-language question")
    run_id = _bounded(
        payload.get("run_id") or f"pfs-export-{uuid.uuid4().hex[:16]}", "run_id", limit=120, required=True
    )
    return analyze_csv(
        resource_path("data", "fixtures", "pfs_sales.csv"),
        metric=_FIXTURE_METRIC,
        request=AnalysisRequest(
            run_id=run_id,
            metric_id=_FIXTURE_METRIC.metric_id,
            dimension=_FIXTURE_METRIC.dimension,
            date_from=_bounded(payload.get("date_from"), "date_from", limit=32),
            date_to=_bounded(payload.get("date_to"), "date_to", limit=32),
        ),
        source_id="pfs-fixture-sales",
    )


def _session_export_result(sid: str, payload: Mapping[str, object]) -> object:
    source_id = _bounded(payload.get("source_id"), "source_id", limit=160, required=True)
    path, _source = _session_tabular_source(sid, source_id)
    worksheet = _worksheet_from_payload(payload)
    question = _bounded(payload.get("question"), "question", limit=500)
    if question:
        snapshot = load_tabular_snapshot(
            path, source_id=source_id, date_column="month", worksheet=worksheet
        )
        parsed = parse_report_question(
            question,
            snapshot.columns,
            run_id=_bounded(
                payload.get("run_id") or f"pfs-export-{uuid.uuid4().hex[:16]}",
                "run_id",
                limit=120,
                required=True,
            ),
        )
        return analyze_file(
            path,
            metric=parsed.metric,
            request=parsed.request,
            source_id=source_id,
            worksheet=worksheet,
        )

    date_column = _bounded(payload.get("date_column") or "month", "date_column", limit=120, required=True)
    snapshot = load_tabular_snapshot(
        path, source_id=source_id, date_column=date_column, worksheet=worksheet
    )
    metric = _metric_from_payload(payload, snapshot.columns)
    run_id = _bounded(
        payload.get("run_id") or f"pfs-export-{uuid.uuid4().hex[:16]}", "run_id", limit=120, required=True
    )
    return analyze_file(
        path,
        metric=metric,
        request=AnalysisRequest(
            run_id=run_id,
            metric_id=metric.metric_id,
            dimension=metric.dimension,
            date_from=_bounded(payload.get("date_from"), "date_from", limit=32),
            date_to=_bounded(payload.get("date_to"), "date_to", limit=32),
        ),
        source_id=source_id,
        worksheet=worksheet,
    )


@bp.get("/api/pfs/capabilities")
def capabilities():
    """Expose the implemented PFS slice without claiming full integration."""
    return jsonify(
        {
            "ok": True,
            "product": "PFS",
            "reporting": {
                "fixture": "implemented",
                "uploaded_csv": "implemented",
                "uploaded_xlsx": "implemented",
                "report_export_json": "implemented_server_recomputed",
                "report_export_csv": "implemented_server_recomputed",
                "delivery_xlsx_docx_pptx_dashboard": "implemented_deterministic_desktop_slice",
            },
            "models": {
                "deepseek_chat": "verified_local_http",
                "other_provider_live_runs": "pending",
            },
            "evidence_ledger": {
                "identity": "implemented",
                "idempotent_batch_registration": "implemented",
                "claim_links_and_conflicts": "implemented",
                "persistent_local_adapter": "implemented",
                "semantic_verification": "pending",
            },
            "runtime": {
                "tool_policy_gate": "implemented_first_slice",
                "sse_and_long_tasks": "sse_and_job_recovery_verified_local",
                "external_sources": "postgresql_verified_local; other_connectors_pending",
            },
        }
    )


@bp.post("/api/pfs/export")
def export_fixture_report():
    """Download the fixture report after recomputing it on the server."""
    try:
        payload = _body()
        output_format = _export_format(payload)
        return _export_response(_fixture_export_result(payload), output_format)
    except QueryInterpretationError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": "pfs_query_failed"}), 400
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)


@bp.post("/api/session/<sid>/pfs/export")
@require_session_ownership
def export_session_report(sid: str):
    """Download an uploaded-source report after server-side recomputation."""
    try:
        payload = _body()
        output_format = _export_format(payload)
        return _export_response(_session_export_result(sid, payload), output_format)
    except QueryInterpretationError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": "pfs_query_failed"}), 400
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)


@bp.post("/api/pfs/deliver")
def deliver_fixture_report():
    """Generate a richer deterministic artifact from the reviewed fixture."""
    try:
        from data.sources.csv import CSVDataSource

        payload = _body()
        output_format = _delivery_format(payload)
        data_source = CSVDataSource(
            str(resource_path("data", "fixtures", "pfs_sales.csv")),
            "pfs_sales.csv",
        )
        return jsonify(
            _delivery_artifacts(
                _fixture_export_result(payload),
                data_source,
                "",
                output_format,
            )
        )
    except QueryInterpretationError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": "pfs_query_failed"}), 400
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)


@bp.post("/api/session/<sid>/pfs/deliver")
@require_session_ownership
def deliver_session_report(sid: str):
    """Generate Excel, Word, PPT, or Dashboard artifacts for an uploaded report."""
    try:
        payload = _body()
        output_format = _delivery_format(payload)
        source_id = _bounded(payload.get("source_id"), "source_id", limit=160, required=True)
        _path, data_source = _session_tabular_source(sid, source_id)
        return jsonify(
            _delivery_artifacts(
                _session_export_result(sid, payload),
                data_source,
                sid,
                output_format,
            )
        )
    except QueryInterpretationError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": "pfs_query_failed"}), 400
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)


@bp.get("/api/pfs/fixture")
def fixture_analysis():
    """Return the deterministic fixture result used by offline acceptance."""
    run_id = (request.args.get("run_id") or "fixture-preview").strip()[:120]
    date_from = (request.args.get("date_from") or "").strip()[:32]
    date_to = (request.args.get("date_to") or "").strip()[:32]
    try:
        result = analyze_csv(
            resource_path("data", "fixtures", "pfs_sales.csv"),
            metric=_FIXTURE_METRIC,
            request=AnalysisRequest(
                run_id=run_id,
                metric_id=_FIXTURE_METRIC.metric_id,
                dimension=_FIXTURE_METRIC.dimension,
                date_from=date_from,
                date_to=date_to,
            ),
            source_id="pfs-fixture-sales",
        )
    except (TypeError, ValueError) as exc:
        return _error(exc)
    return jsonify({"ok": True, "result": result.to_dict()})


@bp.get("/api/session/<sid>/pfs/sources")
@require_session_ownership
def session_pfs_sources(sid: str):
    """List uploaded CSV/XLSX sources that the deterministic PFS path can analyze."""
    sess = session_manager.get(sid)
    if not sess:
        return jsonify({"ok": True, "sources": []})
    sources = []
    upload_root = data_path("uploads").resolve(strict=False)
    for entry in getattr(sess, "_sources", ()):
        source = entry.get("source")
        path = Path(str(getattr(source, "file_path", "") or "")).resolve(strict=False)
        if (
            path.suffix.lower() not in {".csv", ".xlsx"}
            or not path.is_file()
            or not _path_within(path, upload_root)
        ):
            continue
        source_id = str(entry.get("id") or "")
        worksheets = []
        try:
            if path.suffix.lower() == ".xlsx":
                for worksheet in list_xlsx_worksheets(path):
                    try:
                        sheet_snapshot = load_tabular_snapshot(
                            path,
                            source_id=source_id,
                            date_column="month",
                            worksheet=worksheet,
                        )
                    except ReportingContractError as exc:
                        worksheets.append(
                            {"name": worksheet, "columns": [], "row_count": 0, "error": str(exc)}
                        )
                    else:
                        worksheets.append(
                            {
                                "name": worksheet,
                                "columns": list(sheet_snapshot.columns),
                                "row_count": sheet_snapshot.row_count,
                                "min_date": sheet_snapshot.min_date,
                                "max_date": sheet_snapshot.max_date,
                            }
                        )
                usable = [item for item in worksheets if item["columns"]]
                snapshot = (
                    load_tabular_snapshot(
                        path,
                        source_id=source_id,
                        date_column="month",
                        worksheet=usable[0]["name"],
                    )
                    if len(usable) == 1
                    else None
                )
            else:
                snapshot = load_tabular_snapshot(path, source_id=source_id, date_column="month")
        except ReportingContractError:
            continue
        sources.append(
            {
                "source_id": source_id,
                "name": str(getattr(source, "name", "") or path.name),
                "file_name": path.name,
                "columns": list(snapshot.columns) if snapshot else [],
                "row_count": snapshot.row_count if snapshot else sum(item["row_count"] for item in worksheets),
                "min_date": snapshot.min_date if snapshot else "",
                "max_date": snapshot.max_date if snapshot else "",
                "worksheets": worksheets,
            }
        )
    return jsonify({"ok": True, "sources": sources})


@bp.post("/api/session/<sid>/pfs/analyze")
@require_session_ownership
def session_pfs_analysis(sid: str):
    """Analyze one uploaded CSV/XLSX with an explicit metric contract."""
    try:
        payload = _body()
        source_id = _bounded(payload.get("source_id"), "source_id", limit=160, required=True)
        path, source = _session_tabular_source(sid, source_id)
        worksheet = _worksheet_from_payload(payload)
        date_column = _bounded(
            payload.get("date_column") or "month",
            "date_column",
            limit=120,
            required=True,
        )
        snapshot = load_tabular_snapshot(
            path, source_id=source_id, date_column=date_column, worksheet=worksheet
        )
        metric = _metric_from_payload(payload, snapshot.columns)
        run_id = _bounded(
            payload.get("run_id") or f"pfs-{uuid.uuid4().hex[:16]}",
            "run_id",
            limit=120,
            required=True,
        )
        result = analyze_file(
            path,
            metric=metric,
            request=AnalysisRequest(
                run_id=run_id,
                metric_id=metric.metric_id,
                dimension=metric.dimension,
                date_from=_bounded(payload.get("date_from"), "date_from", limit=32),
                date_to=_bounded(payload.get("date_to"), "date_to", limit=32),
            ),
            source_id=source_id,
            worksheet=worksheet,
        )
    except QueryInterpretationError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": "pfs_query_failed"}), 400
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)
    return jsonify(
        {
            "ok": True,
            "source": {
                "source_id": source_id,
                "name": str(getattr(source, "name", "") or path.name),
            },
            "result": result.to_dict(),
        }
    )


@bp.post("/api/session/<sid>/pfs/query")
@require_session_ownership
def session_pfs_query(sid: str):
    """Route one explicit natural-language question to deterministic analysis."""
    try:
        payload = _body()
        question = _bounded(payload.get("question"), "question", limit=500, required=True)
        source_id = _bounded(payload.get("source_id"), "source_id", limit=160, required=True)
        path, source = _session_tabular_source(sid, source_id)
        worksheet = _worksheet_from_payload(payload)
        snapshot = load_tabular_snapshot(
            path, source_id=source_id, date_column="month", worksheet=worksheet
        )
        parsed = parse_report_question(
            question,
            snapshot.columns,
            run_id=_bounded(
                payload.get("run_id") or f"pfs-query-{uuid.uuid4().hex[:16]}",
                "run_id",
                limit=120,
                required=True,
            ),
        )
        result = analyze_file(
            path,
            metric=parsed.metric,
            request=parsed.request,
            source_id=source_id,
            worksheet=worksheet,
        )
    except QueryInterpretationError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": "pfs_query_failed"}), 400
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)
    return jsonify(
        {
            "ok": True,
            "source": {"source_id": source_id, "name": str(getattr(source, "name", "") or path.name)},
            "interpretation": parsed.to_dict(),
            "result": result.to_dict(),
        }
    )


@bp.get("/api/pfs/ledger")
def get_ledger():
    task_id = (request.args.get("task_id") or "").strip()[:160]
    ledger = _ledger()
    return jsonify(
        {
            "ok": True,
            "evidence": [item.to_dict() for item in ledger.list_evidence(task_id)],
            "claims": [item.to_dict() for item in ledger.list_claims(task_id)],
            "pending_conflicts": [item.to_dict() for item in ledger.pending_conflicts(task_id)],
        }
    )


@bp.post("/api/pfs/ledger/evidence")
def register_evidence():
    try:
        payload = _body()
        candidates = payload.get("candidates")
        if candidates is None:
            candidates = [payload]
        if not isinstance(candidates, list) or not candidates or len(candidates) > 100:
            raise LedgerError("candidates must contain between 1 and 100 items")
        prepared = []
        for item in candidates:
            if not isinstance(item, Mapping):
                raise LedgerError("each evidence candidate must be an object")
            prepared.append(
                EvidenceCandidate(
                    source_url=_bounded(item.get("source_url"), "source_url", limit=2048, required=True),
                    snippet=_bounded(item.get("snippet"), "snippet", limit=10000, required=True),
                    task_id=_bounded(item.get("task_id"), "task_id", limit=160, required=True),
                    title=_bounded(item.get("title"), "title", limit=300),
                    publisher=_bounded(item.get("publisher"), "publisher", limit=300),
                    published_at=_bounded(item.get("published_at"), "published_at", limit=80),
                    captured_at=_bounded(item.get("captured_at"), "captured_at", limit=80),
                    source_type=_bounded(item.get("source_type"), "source_type", limit=80) or "unknown",
                    trust_level=_bounded(item.get("trust_level"), "trust_level", limit=80) or "unknown",
                    content_sha256=_bounded(item.get("content_sha256"), "content_sha256", limit=64),
                )
            )
        result = _ledger().register_batch(prepared)
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)
    return jsonify({"ok": True, **result.to_dict()})


@bp.post("/api/pfs/ledger/claims")
def create_ledger_claim():
    try:
        payload = _body()
        claim = ClaimRecord(
            claim_id=_bounded(payload.get("claim_id"), "claim_id", limit=160, required=True),
            task_id=_bounded(payload.get("task_id"), "task_id", limit=160, required=True),
            text=_bounded(payload.get("text"), "text", limit=10000, required=True),
            confidence=float(payload.get("confidence") or 0),
            verification_reason=_bounded(
                payload.get("verification_reason"), "verification_reason", limit=1000
            ),
        )
        result = _ledger().create_claim(claim)
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)
    return jsonify({"ok": True, "claim": result.to_dict()})


@bp.get("/api/pfs/ledger/claims/<claim_id>")
def get_ledger_claim(claim_id: str):
    try:
        detail = _ledger().claim_detail(_bounded(claim_id, "claim_id", limit=160, required=True))
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc, 404 if "does not exist" in str(exc) else 400)
    return jsonify({"ok": True, **detail})


@bp.post("/api/pfs/ledger/claims/<claim_id>/links")
def link_ledger_claim(claim_id: str):
    try:
        payload = _body()
        result = _ledger().link_claim(
            _bounded(claim_id, "claim_id", limit=160, required=True),
            evidence_id=_bounded(payload.get("evidence_id"), "evidence_id", limit=160, required=True),
            relation=_bounded(payload.get("relation"), "relation", limit=20, required=True),
            confidence=float(payload.get("confidence") or 0),
            verification_reason=_bounded(
                payload.get("verification_reason"), "verification_reason", limit=1000
            ),
        )
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)
    return jsonify({"ok": True, "claim": result.to_dict()})


@bp.post("/api/pfs/ledger/claims/<claim_id>/decision")
def decide_ledger_claim(claim_id: str):
    try:
        payload = _body()
        result = _ledger().decide_claim(
            _bounded(claim_id, "claim_id", limit=160, required=True),
            _bounded(payload.get("decision"), "decision", limit=120, required=True),
            reason=_bounded(payload.get("reason"), "reason", limit=1000),
        )
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)
    return jsonify({"ok": True, "claim": result.to_dict()})
