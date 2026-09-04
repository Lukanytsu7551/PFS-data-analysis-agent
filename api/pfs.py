"""PFS report, data-source, and deterministic analysis endpoints.

The first PFS vertical slice keeps calculation deterministic and bounded. It
    can preview the reviewed fixture or analyze a CSV/XLSX already mounted in the
    current session. Results retain lightweight source and claim references.
"""

from __future__ import annotations

from collections.abc import Mapping
import csv
import io
import json
import logging
from pathlib import Path
import re
from urllib.parse import unquote, urlparse
import uuid

from flask import Blueprint, Response, jsonify, request

from .state import chart_store, require_session_ownership, session_manager
from infrastructure.paths import data_path, resource_path
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
from pfs_agent.business_acceptance import (
    BusinessAcceptanceError,
    SCENARIO_ID as CITY_PORTFOLIO_SCENARIO_ID,
    evaluate_city_portfolio,
    MONTHLY_PNL_SCENARIO_ID,
    MONTHLY_PNL_REQUIRED_COLUMNS,
    REQUIRED_COLUMNS as CITY_PORTFOLIO_REQUIRED_COLUMNS,
    USER_SUPPLY_REQUIRED_COLUMNS as CITY_USER_SUPPLY_REQUIRED_COLUMNS,
    USER_SUPPLY_SCENARIO_ID as CITY_USER_SUPPLY_SCENARIO_ID,
    evaluate_city_monthly_pnl,
    evaluate_city_user_supply,
)
from pfs_agent.business_forecast import (
    DEMAND_FORECAST_REQUIRED_COLUMNS,
    DEMAND_FORECAST_SCENARIO_ID,
    evaluate_demand_forecast,
)
from pfs_agent.runs import (
    AnalysisRunAlreadyActive,
    AnalysisRunCanceled,
    analysis_run_registry,
)


bp = Blueprint("pfs", __name__)
log = logging.getLogger(__name__)

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
        raise ReportingContractError(
            "metric columns missing from CSV source: " + ", ".join(sorted(missing)),
            code="source_columns_missing",
        )
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
    code = getattr(exc, "code", type(exc).__name__)
    return jsonify({"ok": False, "error": str(exc), "code": code}), status


def _worksheet_from_payload(payload: Mapping[str, object]) -> str:
    return _bounded(payload.get("worksheet"), "worksheet", limit=120)


def _export_filename(run_id: str, output_format: str) -> str:
    """Build a stable, header-safe download name from an opaque run id."""
    safe_run_id = re.sub(r"[^A-Za-z0-9_-]+", "-", str(run_id or "pfs-report"))[:48].strip("-")
    return f"pfs-report-{safe_run_id or 'result'}.{output_format}"


def _result_payload(result: object) -> dict:
    """Read either the standard report object or a business-scenario mapping."""
    if isinstance(result, Mapping):
        return dict(result)
    return result.to_dict()


def _csv_export(result: object) -> str:
    """Flatten the report contract without dropping its lightweight trace."""
    payload = _result_payload(result)
    if payload.get("scenario"):
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["section", "field", "value", "detail"])
        writer.writerow(
            ["scenario", "name", payload["scenario"].get("name", ""), payload["scenario"].get("question", "")]
        )
        writer.writerow(["scenario", "assessment", payload.get("assessment", ""), ""])
        for field, value in (payload.get("metrics") or {}).items():
            writer.writerow(["metric", field, value, ""])
        for claim in payload.get("claims") or []:
            writer.writerow(
                [
                    "claim",
                    claim.get("claim_id", ""),
                    claim.get("text") or claim.get("claim", ""),
                    f"status={claim.get('status', '')}; evidence={','.join(claim.get('evidence_ids', []))}",
                ]
            )
        for evidence in payload.get("evidence") or []:
            writer.writerow(
                [
                    "evidence",
                    evidence.get("evidence_id", ""),
                    evidence.get("locator", ""),
                    evidence.get("content_sha256", ""),
                ]
            )
        for caveat in payload.get("caveats") or []:
            writer.writerow(["caveat", "caveat", caveat, ""])
        return output.getvalue()
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
    payload = _result_payload(result)
    filename = _export_filename(payload["run_id"], output_format)
    if output_format == "json":
        body = json.dumps({"ok": True, "result": payload}, ensure_ascii=False, indent=2).encode("utf-8")
        content_type = "application/json; charset=utf-8"
    else:
        body = _csv_export(result).encode("utf-8-sig")
        content_type = "text/csv; charset=utf-8"
    source_meta = payload.get("snapshot") or payload.get("source") or {}
    return Response(
        body,
        content_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-PFS-Report-Run": payload["run_id"],
            "X-PFS-Source-SHA256": source_meta.get("content_sha256", ""),
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
    groups = (
        "\n".join(
            f"{item['rank']}. {item['dimension']}：{item['value']}" for item in payload.get("groups", [])
        )
        or "没有可展示的分组结果。"
    )
    claims = (
        "\n".join(
            f"- {item['text']}（{item.get('status') or 'unverified'}，置信度 {item.get('confidence', 0):.0%}）"
            for item in payload.get("claims", [])
        )
        or "没有可展示的关键结论。"
    )
    evidence = (
        "\n".join(f"- {item['evidence_id']}：{item['excerpt']}" for item in payload.get("evidence", []))
        or "没有登记证据。"
    )
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
        {"heading": "来源留痕", "content": evidence},
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
                    [
                        "#",
                        "纳入数据",
                        f"{snapshot['row_count']} 行\n{snapshot.get('min_date') or '全部'} 至 {snapshot.get('max_date') or '全部'}",
                    ],
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
                "rows": [
                    [str(item["rank"]), str(item["dimension"]), f"{item['value']:,}"] for item in groups
                ],
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
        raise ReportingContractError("data source has no exportable table", code="delivery_table_missing")

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
    artifact_id = f"pfs:{run_id}:{output_format}"
    chart_specs = _dashboard_widgets(result, selected_table)
    agent._artifact_metadata = {
        "artifact_id": artifact_id,
        "run_id": payload["run_id"],
        "source_id": payload["snapshot"].get("source_id", ""),
        "source_sha256": payload["snapshot"].get("content_sha256", ""),
        "worksheet": payload["snapshot"].get("worksheet", ""),
        "included_rows": payload["snapshot"].get("row_count", 0),
        "metric_contract": metric,
        "analysis_parameters": payload.get("request", {}),
        "sql": [widget.get("sql", "") for widget in chart_specs],
        "chart_specs": chart_specs,
        "final_claims": [item.get("text", "") for item in payload.get("claims", [])],
        "warnings": list(payload.get("warnings", [])),
        "cost": {
            "amount": 0.0,
            "currency": "USD",
            "estimated": False,
            "source": "deterministic_no_model",
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        },
        "download_count": 0,
        "download_history": [],
    }
    try:
        if output_format == "xlsx":
            tool_result = agent._tool_export_excel([selected_table], f"pfs-report-{run_id}")
        elif output_format == "docx":
            tool_result = agent._tool_export_report(
                f"PFS {metric['label']}分析报告", _report_sections(result)
            )
        elif output_format == "pptx":
            tool_result = agent._tool_generate_ppt(
                f"PFS {metric['label']}分析",
                _ppt_slides(result),
                f"pfs-report-{run_id}",
            )
        else:
            tool_result = agent._tool_generate_dashboard(
                f"PFS {metric['label']}分析看板",
                chart_specs,
                "pfs",
            )
    except ReportingContractError:
        raise
    except Exception as exc:
        log.warning("[pfs-delivery] artifact generation failed: %s", exc)
        raise ReportingContractError(
            "交付物生成失败",
            code="delivery_generation_failed",
        ) from exc
    if str(tool_result).startswith("❌"):
        raise ReportingContractError(
            str(tool_result).removeprefix("❌").strip(),
            code="delivery_generation_failed",
        )
    artifacts = []
    for label, url in _tool_links(str(tool_result)):
        path_name = Path(unquote(urlparse(url).path)).name
        kind = "dashboard" if "/dashboard/" in url and "/api/" not in url else output_format
        if kind != "dashboard":
            url = f"{url}{'&' if '?' in url else '?'}artifact_id={artifact_id}"
        if kind == "dashboard":
            from infrastructure.artifact_lifecycle import register_artifact
            from api.dashboard import _dashboard_path

            dashboard_path = Path(_dashboard_path(path_name))
            if dashboard_path.is_file():
                try:
                    register_artifact(
                        dashboard_path,
                        artifact_type="dashboard",
                        session_id=sid,
                        artifact_id=artifact_id,
                        metadata=agent._artifact_metadata,
                    )
                except ValueError:
                    # Test/custom dashboard roots may intentionally live outside
                    # the managed data root; lifecycle registration stays conservative.
                    pass
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "type": kind,
                "name": path_name or re.sub(r"^[^\w]+", "", label) or f"PFS {output_format}",
                "label": re.sub(r"^[^\w\u4e00-\u9fff]+", "", label),
                "url": url,
                "action": "open" if kind == "dashboard" else "download",
                "run_id": agent._artifact_metadata["run_id"],
                "source_sha256": agent._artifact_metadata["source_sha256"],
                "worksheet": agent._artifact_metadata["worksheet"],
                "included_rows": agent._artifact_metadata["included_rows"],
                "metric_contract": agent._artifact_metadata["metric_contract"],
                "analysis_parameters": agent._artifact_metadata["analysis_parameters"],
                "sql": agent._artifact_metadata["sql"],
                "chart_specs": agent._artifact_metadata["chart_specs"],
                "final_claims": agent._artifact_metadata["final_claims"],
                "warnings": agent._artifact_metadata["warnings"],
                "cost": agent._artifact_metadata["cost"],
            }
        )
    if not artifacts:
        raise ReportingContractError("export completed without a downloadable artifact")
    return {
        "ok": True,
        "format": output_format,
        "run_id": payload["run_id"],
        "source_sha256": payload["snapshot"].get("content_sha256", ""),
        "artifacts": artifacts,
        "message": str(tool_result),
    }


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
        snapshot = load_tabular_snapshot(path, source_id=source_id, date_column="month", worksheet=worksheet)
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
            file_name=str(getattr(_source, "name", "") or ""),
        )

    date_column = _bounded(payload.get("date_column") or "month", "date_column", limit=120, required=True)
    snapshot = load_tabular_snapshot(path, source_id=source_id, date_column=date_column, worksheet=worksheet)
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
        file_name=str(getattr(_source, "name", "") or ""),
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
                "analysis_cancel": "implemented_single_process_cooperative",
                "analysis_cancel_cross_process": "implemented_opt_in_sqlite_registry",
                "business_acceptance": "implemented_local_contract_v1_with_four_scenarios",
                "business_forecast_guardrails": (
                    "implemented_optional_total_delta_target_mean_shift_and_ks_thresholds"
                ),
            },
            "models": {
                "deepseek_chat": "verified_local_http",
                "prediction_quality_evaluation": "implemented_local_contract_v1_with_agent_model_output_adapters",
                "other_provider_live_runs": "pending",
            },
            "result_trace": {
                "report_claims": "lightweight_result_fields",
                "source_evidence": "lightweight_snapshot_metadata",
                "tool_results": "reference_agent_persistence_preserved",
            },
            "runtime": {
                "tool_policy_gate": "implemented_first_slice",
                "sse_and_long_tasks": "sse_and_job_recovery_verified_local",
                "workflow_pause_resume": "implemented_durable_local",
                "agent_runtime_budget": "implemented_validated_environment_contract_v1",
                "workflow_budget": "implemented_graph_limits_atomic_reservation_v2",
                "workflow_unknown_price_policy": "implemented_fail_closed_when_cost_limit_enabled",
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
        return _error(exc)
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)


@bp.post("/api/session/<sid>/pfs/export")
@require_session_ownership
def export_session_report(sid: str):
    """Download an uploaded-source report after server-side recomputation."""
    try:
        payload = _body()
        output_format = _export_format(payload)
        if payload.get("scenario"):
            return _export_response(_session_business_acceptance_result(sid, payload), output_format)
        return _export_response(_session_export_result(sid, payload), output_format)
    except QueryInterpretationError as exc:
        return _error(exc)
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
        result = _fixture_export_result(payload)
        return jsonify(
            _delivery_artifacts(
                result,
                data_source,
                "",
                output_format,
            )
        )
    except QueryInterpretationError as exc:
        return _error(exc)
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
        result = _session_export_result(sid, payload)
        return jsonify(
            _delivery_artifacts(
                result,
                data_source,
                sid,
                output_format,
            )
        )
    except QueryInterpretationError as exc:
        return _error(exc)
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
        validation_error = None
        try:
            if path.suffix.lower() == ".xlsx":
                worksheet_snapshots = {}
                for worksheet in list_xlsx_worksheets(path):
                    try:
                        sheet_snapshot = load_tabular_snapshot(
                            path,
                            source_id=source_id,
                            date_column="month",
                            worksheet=worksheet,
                        )
                    except ReportingContractError as exc:
                        if exc.code == "source_date_invalid":
                            try:
                                sheet_snapshot = load_tabular_snapshot(
                                    path,
                                    source_id=source_id,
                                    date_column="",
                                    worksheet=worksheet,
                                )
                            except ReportingContractError as fallback_exc:
                                worksheets.append(
                                    {
                                        "name": worksheet,
                                        "columns": [],
                                        "row_count": 0,
                                        "error": str(fallback_exc),
                                        "error_code": fallback_exc.code,
                                    }
                                )
                            else:
                                worksheet_snapshots[worksheet] = sheet_snapshot
                                worksheets.append(
                                    {
                                        "name": worksheet,
                                        "columns": list(sheet_snapshot.columns),
                                        "row_count": sheet_snapshot.row_count,
                                        "min_date": "",
                                        "max_date": "",
                                        "validation_error": {
                                            "code": exc.code,
                                            "message": str(exc),
                                        },
                                    }
                                )
                        else:
                            worksheets.append(
                                {
                                    "name": worksheet,
                                    "columns": [],
                                    "row_count": 0,
                                    "error": str(exc),
                                    "error_code": exc.code,
                                }
                            )
                    else:
                        worksheet_snapshots[worksheet] = sheet_snapshot
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
                snapshot = worksheet_snapshots[usable[0]["name"]] if len(usable) == 1 else None
                if len(usable) == 1:
                    validation_error = usable[0].get("validation_error")
            else:
                try:
                    snapshot = load_tabular_snapshot(path, source_id=source_id, date_column="month")
                except ReportingContractError as exc:
                    if exc.code != "source_date_invalid":
                        raise
                    snapshot = load_tabular_snapshot(path, source_id=source_id, date_column="")
                    validation_error = {"code": exc.code, "message": str(exc)}
        except ReportingContractError:
            continue
        source_payload = {
            "source_id": source_id,
            "name": str(getattr(source, "name", "") or path.name),
            # Keep the physical path private.  Consumers of the source
            # listing should receive the filename the user uploaded, just as
            # the report snapshot and Ledger do.
            "file_name": str(getattr(source, "name", "") or path.name),
            "columns": list(snapshot.columns) if snapshot else [],
            "row_count": snapshot.row_count if snapshot else sum(item["row_count"] for item in worksheets),
            "min_date": snapshot.min_date if snapshot else "",
            "max_date": snapshot.max_date if snapshot else "",
            "worksheets": worksheets,
        }
        if validation_error:
            source_payload["validation_error"] = validation_error
        sources.append(source_payload)
    return jsonify({"ok": True, "sources": sources})


@bp.post("/api/session/<sid>/pfs/analyze")
@require_session_ownership
def session_pfs_analysis(sid: str):
    """Analyze one uploaded CSV/XLSX with an explicit metric contract."""
    run_id = ""
    registered = False
    try:
        payload = _body()
        run_id = _bounded(
            payload.get("run_id") or f"pfs-{uuid.uuid4().hex[:16]}",
            "run_id",
            limit=120,
            required=True,
        )
        analysis_run_registry.begin(sid, run_id)
        registered = True
        analysis_run_registry.checkpoint(sid, run_id)
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
        analysis_run_registry.checkpoint(sid, run_id)
        metric = _metric_from_payload(payload, snapshot.columns)
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
            file_name=str(getattr(source, "name", "") or ""),
        )
        analysis_run_registry.begin_commit(sid, run_id)
        result_dict = result.to_dict()
    except AnalysisRunCanceled as exc:
        return _error(exc, 409)
    except AnalysisRunAlreadyActive as exc:
        return _error(exc, 409)
    except QueryInterpretationError as exc:
        return _error(exc)
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)
    finally:
        if registered:
            analysis_run_registry.finish(sid, run_id)
    return jsonify(
        {
            "ok": True,
            "source": {
                "source_id": source_id,
                "name": str(getattr(source, "name", "") or path.name),
            },
            "result": result_dict,
        }
    )


@bp.post("/api/session/<sid>/pfs/query")
@require_session_ownership
def session_pfs_query(sid: str):
    """Route one explicit natural-language question to deterministic analysis."""
    run_id = ""
    registered = False
    try:
        payload = _body()
        run_id = _bounded(
            payload.get("run_id") or f"pfs-query-{uuid.uuid4().hex[:16]}",
            "run_id",
            limit=120,
            required=True,
        )
        analysis_run_registry.begin(sid, run_id)
        registered = True
        analysis_run_registry.checkpoint(sid, run_id)
        question = _bounded(payload.get("question"), "question", limit=500, required=True)
        source_id = _bounded(payload.get("source_id"), "source_id", limit=160, required=True)
        path, source = _session_tabular_source(sid, source_id)
        worksheet = _worksheet_from_payload(payload)
        snapshot = load_tabular_snapshot(path, source_id=source_id, date_column="month", worksheet=worksheet)
        analysis_run_registry.checkpoint(sid, run_id)
        parsed = parse_report_question(
            question,
            snapshot.columns,
            run_id=run_id,
        )
        analysis_run_registry.checkpoint(sid, run_id)
        result = analyze_file(
            path,
            metric=parsed.metric,
            request=parsed.request,
            source_id=source_id,
            worksheet=worksheet,
            file_name=str(getattr(source, "name", "") or ""),
        )
        analysis_run_registry.begin_commit(sid, run_id)
        result_dict = result.to_dict()
    except AnalysisRunCanceled as exc:
        return _error(exc, 409)
    except AnalysisRunAlreadyActive as exc:
        return _error(exc, 409)
    except QueryInterpretationError as exc:
        return _error(exc)
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)
    finally:
        if registered:
            analysis_run_registry.finish(sid, run_id)
    return jsonify(
        {
            "ok": True,
            "source": {"source_id": source_id, "name": str(getattr(source, "name", "") or path.name)},
            "interpretation": parsed.to_dict(),
            "result": result_dict,
        }
    )


def _session_business_acceptance_result(sid: str, payload: Mapping[str, object]) -> dict:
    requested_scenario = _bounded(payload.get("scenario") or "auto", "scenario", limit=80, required=True)
    supported_scenarios = {
        CITY_PORTFOLIO_SCENARIO_ID,
        MONTHLY_PNL_SCENARIO_ID,
        CITY_USER_SUPPLY_SCENARIO_ID,
        DEMAND_FORECAST_SCENARIO_ID,
        "auto",
    }
    if requested_scenario not in supported_scenarios:
        raise BusinessAcceptanceError(
            f"unsupported business acceptance scenario: {requested_scenario}",
            code="business_scenario_not_supported",
        )
    source_id = _bounded(payload.get("source_id"), "source_id", limit=160, required=True)
    path, source = _session_tabular_source(sid, source_id)
    worksheet = _worksheet_from_payload(payload)
    snapshot = load_tabular_snapshot(
        path,
        source_id=source_id,
        date_column="",
        worksheet=worksheet,
        file_name=str(getattr(source, "name", "") or path.name),
    )
    scenario = requested_scenario
    if scenario == "auto":
        columns = set(snapshot.columns)
        if set(MONTHLY_PNL_REQUIRED_COLUMNS) <= columns:
            scenario = MONTHLY_PNL_SCENARIO_ID
        elif set(DEMAND_FORECAST_REQUIRED_COLUMNS) <= columns:
            scenario = DEMAND_FORECAST_SCENARIO_ID
        elif set(CITY_PORTFOLIO_REQUIRED_COLUMNS) <= columns:
            scenario = CITY_PORTFOLIO_SCENARIO_ID
        elif set(CITY_USER_SUPPLY_REQUIRED_COLUMNS) <= columns:
            scenario = CITY_USER_SUPPLY_SCENARIO_ID
        else:
            raise BusinessAcceptanceError(
                "uploaded table does not match a registered business scenario",
                code="business_scenario_columns_missing",
            )
    if scenario == DEMAND_FORECAST_SCENARIO_ID:
        model_name = _bounded(
            payload.get("model") or "Time_Series_Prophet",
            "model",
            limit=80,
            required=True,
        )
        result = evaluate_demand_forecast(
            snapshot,
            model_name=model_name,
            holdout_size=payload.get("holdout_size", 3),
            rolling_origins=payload.get("rolling_origins", 2),
            quality_thresholds=payload.get("quality_thresholds"),
            business_thresholds=payload.get("business_thresholds"),
        )
    elif scenario == MONTHLY_PNL_SCENARIO_ID:
        result = evaluate_city_monthly_pnl(snapshot)
    elif scenario == CITY_USER_SUPPLY_SCENARIO_ID:
        result = evaluate_city_user_supply(snapshot)
    else:
        result = evaluate_city_portfolio(snapshot)
    run_id = _bounded(
        payload.get("run_id") or f"business-{scenario}-{snapshot.content_sha256[:12]}",
        "run_id",
        limit=120,
        required=True,
    )
    result["run_id"] = run_id
    return result


@bp.post("/api/session/<sid>/pfs/business-acceptance")
@require_session_ownership
def session_pfs_business_acceptance(sid: str):
    """Run one explicit, deterministic business-shaped acceptance scenario."""
    try:
        result = _session_business_acceptance_result(sid, _body())
    except (BusinessAcceptanceError, ReportingContractError, TypeError, ValueError, OSError) as exc:
        return _error(exc)
    return jsonify({"ok": True, "result": result})


@bp.post("/api/session/<sid>/pfs/runs/<run_id>/cancel")
@require_session_ownership
def cancel_session_pfs_run(sid: str, run_id: str):
    """Request cooperative cancellation for one active run in this session."""
    try:
        bounded_run_id = _bounded(run_id, "run_id", limit=120, required=True)
    except (TypeError, ValueError, OSError) as exc:
        return _error(exc)
    accepted = analysis_run_registry.request_cancel(sid, bounded_run_id)
    if not accepted:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "analysis run is not active in this session",
                    "code": "pfs_analysis_run_not_active",
                }
            ),
            404,
        )
    return jsonify({"ok": True, "run_id": bounded_run_id, "status": "cancel_requested"}), 202
