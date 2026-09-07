"""PFS report and data-source endpoints.

The first PFS vertical slice keeps calculation deterministic and bounded. It
    can preview the reviewed fixture or analyze a CSV/XLSX already mounted in the
    current session. Results include lightweight source and evidence references
    without exposing a separate governance or approval service.
"""

from __future__ import annotations

from collections.abc import Mapping
import csv
import io
import json
from pathlib import Path
import re
import uuid

from flask import Blueprint, Response, jsonify, request

from .state import require_session_ownership, session_manager
from infrastructure.paths import data_path, resource_path
from pfs_agent.reporting import (
    AnalysisRequest,
    MetricContract,
    ReportingContractError,
    analyze_csv,
    analyze_file,
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


def _error(exc: Exception, status: int = 400):
    return jsonify({"ok": False, "error": str(exc), "code": type(exc).__name__}), status


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
    question = _bounded(payload.get("question"), "question", limit=500)
    if question:
        snapshot = load_tabular_snapshot(path, source_id=source_id, date_column="month")
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
        return analyze_file(path, metric=parsed.metric, request=parsed.request, source_id=source_id)

    date_column = _bounded(payload.get("date_column") or "month", "date_column", limit=120, required=True)
    snapshot = load_tabular_snapshot(path, source_id=source_id, date_column=date_column)
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
            },
            "models": {
                "deepseek_chat": "verified_local_http",
                "other_provider_live_runs": "pending",
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
        try:
            snapshot = load_tabular_snapshot(
                path,
                source_id=str(entry.get("id") or ""),
                date_column="month",
            )
        except ReportingContractError:
            continue
        sources.append(
            {
                "source_id": str(entry.get("id") or ""),
                "name": str(getattr(source, "name", "") or path.name),
                "file_name": path.name,
                "columns": list(snapshot.columns),
                "row_count": snapshot.row_count,
                "min_date": snapshot.min_date,
                "max_date": snapshot.max_date,
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
        date_column = _bounded(
            payload.get("date_column") or "month",
            "date_column",
            limit=120,
            required=True,
        )
        snapshot = load_tabular_snapshot(path, source_id=source_id, date_column=date_column)
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
        snapshot = load_tabular_snapshot(path, source_id=source_id, date_column="month")
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
        result = analyze_file(path, metric=parsed.metric, request=parsed.request, source_id=source_id)
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
