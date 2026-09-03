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
import logging
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


def _ledger_task_id(result: object, sid: str) -> str:
    run_id = str(result.to_dict().get("run_id") or "")
    return f"{sid}:{run_id}" if sid else run_id


class _GovernedReportResult:
    """Small explicit adapter that preserves the report result contract."""

    def __init__(self, payload: dict):
        self._payload = payload

    def to_dict(self) -> dict:
        return self._payload


def _governance_result(result: object, sid: str = "") -> dict:
    """Persist deterministic report claims and return their hydrated detail.

    The report UI and the Ledger must refer to the same records.  Session
    prefixes keep local single-user ledgers from allowing one session to
    accidentally decide another session's report.
    """
    payload = result.to_dict()
    task_id = _ledger_task_id(result, sid)
    ledger = _ledger()
    evidence = payload.get("evidence") or []
    ledger_entries = []
    for item in evidence:
        # The ledger identity is deliberately project-scoped, while Claim
        # links are task-scoped.  A repeated run may therefore need its own
        # ledger entry when the same snapshot was already registered by a
        # different task; keep the canonical URL/snippet and add a stable
        # task discriminator only for that collision.
        source_url = (
            f"https://pfs.local/source/{payload['snapshot'].get('source_id', 'unknown')}"
            f"?sha256={payload['snapshot'].get('content_sha256', '')}"
        )
        snippet = str(item.get("excerpt") or "")
        existing = next(
            (candidate for candidate in ledger.list_evidence(task_id)
             if candidate.snippet == snippet and candidate.source_url == source_url),
            None,
        )
        if existing is None and any(candidate.snippet == snippet for candidate in ledger.list_evidence()):
            source_url += "&task=" + hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]
        entry, _ = ledger.register(EvidenceCandidate(
            source_url=source_url,
            snippet=snippet,
            task_id=task_id,
            title=str(payload["snapshot"].get("file_name") or "PFS 数据快照"),
            source_type="tabular_snapshot",
            trust_level="computed",
            content_sha256=str(item.get("content_sha256") or payload["snapshot"].get("content_sha256") or ""),
            publisher="PFS 本地上传数据",
            source_id=str(item.get("source_id") or payload["snapshot"].get("source_id") or ""),
            file_name=str(item.get("file_name") or payload["snapshot"].get("file_name") or ""),
            worksheet=str(item.get("worksheet") or payload["snapshot"].get("worksheet") or ""),
            included_rows=int(item.get("included_rows") or 0),
            locator=str(item.get("locator") or ""),
            date_from=str(item.get("date_from") or ""),
            date_to=str(item.get("date_to") or ""),
            columns=tuple(item.get("columns") or payload["snapshot"].get("columns") or ()),
        ))
        ledger_entries.append(entry)
    entry_by_report_id = {item.get("evidence_id"): entry for item, entry in zip(evidence, ledger_entries)}
    hydrated_claims = []
    for raw in payload.get("claims") or []:
        original_claim_id = str(raw.get("claim_id") or "")
        claim_id = original_claim_id
        candidate_claim = ClaimRecord(
            claim_id=claim_id, task_id=task_id, text=str(raw.get("text") or ""),
            status=str(raw.get("status") or "unverified"),
            confidence=float(raw.get("confidence") or 0),
        )
        claim = ledger.get_claim(claim_id)
        if claim is not None and (claim.task_id != candidate_claim.task_id or claim.text != candidate_claim.text):
            # Legacy deterministic reports used only run_id in claim IDs.
            # Keep that public ID when it is safe, but derive a stable
            # task-scoped ID on collision so one session cannot overwrite
            # another session's governance record.
            claim_id = (
                f"{original_claim_id}_"
                f"{hashlib.sha256(task_id.encode('utf-8')).hexdigest()[:12]}"
            )
            candidate_claim = ClaimRecord(
                claim_id=claim_id, task_id=task_id, text=str(raw.get("text") or ""),
                status=str(raw.get("status") or "unverified"),
                confidence=float(raw.get("confidence") or 0),
            )
            claim = ledger.get_claim(claim_id)
        if claim is None:
            claim = ledger.create_claim(candidate_claim)
        for report_evidence_id in raw.get("evidence_ids") or []:
            entry = entry_by_report_id.get(report_evidence_id)
            if entry is not None:
                claim = ledger.link_claim(
                    claim_id, evidence_id=entry.evidence_id, relation="supports",
                    confidence=float(raw.get("confidence") or 0),
                    verification_reason="由 PFS 确定性报表计算得到",
                )
        hydrated_claims.append(claim.to_dict())
    payload["claims"] = hydrated_claims
    payload["evidence"] = [
        {**item, "evidence_id": entry.evidence_id, "source_url": entry.source_url,
         "title": entry.title, "captured_at": entry.captured_at,
         "publisher": entry.publisher, "published_at": entry.published_at,
         "source_id": entry.source_id or item.get("source_id", ""),
         "file_name": entry.file_name or item.get("file_name", ""),
         "worksheet": entry.worksheet or item.get("worksheet", ""),
         "included_rows": entry.included_rows or item.get("included_rows", 0),
         "locator": entry.locator or item.get("locator", ""),
         "date_from": entry.date_from or item.get("date_from", ""),
         "date_to": entry.date_to or item.get("date_to", ""),
         "columns": list(entry.columns or item.get("columns", ())) }
        for item, entry in zip(evidence, ledger_entries)
    ]
    return payload


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
        "claim_ids": [item.get("claim_id") for item in payload.get("claims", [])],
        "evidence_ids": [item.get("evidence_id") for item in payload.get("evidence", [])],
        "claim_details": list(payload.get("claims", [])),
        "evidence_details": list(payload.get("evidence", [])),
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
                "claim_ids": agent._artifact_metadata["claim_ids"],
                "evidence_ids": agent._artifact_metadata["evidence_ids"],
                "claim_details": agent._artifact_metadata["claim_details"],
                "evidence_details": agent._artifact_metadata["evidence_details"],
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
            file_name=str(getattr(_source, "name", "") or ""),
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
        governed = _governance_result(result)
        return jsonify(
            _delivery_artifacts(
                _GovernedReportResult(governed),
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
        governed = _governance_result(result, sid)
        return jsonify(
            _delivery_artifacts(
                _GovernedReportResult(governed),
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
    return jsonify({"ok": True, "result": _governance_result(result)})


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
                    snapshot = load_tabular_snapshot(
                        path, source_id=source_id, date_column="month"
                    )
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
            "row_count": snapshot.row_count
            if snapshot
            else sum(item["row_count"] for item in worksheets),
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
        governed_result = _governance_result(result, sid)
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
            "result": governed_result,
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
        snapshot = load_tabular_snapshot(
            path, source_id=source_id, date_column="month", worksheet=worksheet
        )
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
        governed_result = _governance_result(result, sid)
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
            "result": governed_result,
        }
    )


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


@bp.get("/api/session/<sid>/pfs/ledger/claims/<claim_id>")
@require_session_ownership
def get_session_ledger_claim(sid: str, claim_id: str):
    """Read one Claim and its Evidence only inside its session namespace."""
    try:
        task_id = _bounded(request.args.get("task_id"), "task_id", limit=300, required=True)
        if not task_id.startswith(f"{sid}:"):
            raise LedgerError("claim task is outside this session")
        detail = _ledger().claim_detail(_bounded(claim_id, "claim_id", limit=160, required=True))
        if detail["claim"].get("task_id") != task_id:
            raise LedgerError("claim does not belong to this session")
    except (TypeError, ValueError, OSError) as exc:
        return _error(
            exc,
            404
            if any(token in str(exc) for token in ("does not belong", "outside this session", "does not exist"))
            else 400,
        )
    return jsonify({"ok": True, **detail})


@bp.post("/api/session/<sid>/pfs/ledger/claims/<claim_id>/decision")
@require_session_ownership
def decide_session_ledger_claim(sid: str, claim_id: str):
    """Decide only a Claim created by this session's report run."""
    try:
        payload = _body()
        task_id = _bounded(payload.get("task_id"), "task_id", limit=300, required=True)
        if not task_id.startswith(f"{sid}:"):
            raise LedgerError("claim task is outside this session")
        ledger = _ledger()
        before = ledger.get_claim(_bounded(claim_id, "claim_id", limit=160, required=True))
        if before is None or before.task_id != task_id:
            raise LedgerError("claim does not belong to this session")
        decision = _bounded(payload.get("decision"), "decision", limit=120, required=True)
        reason = _bounded(payload.get("reason"), "reason", limit=1000)
        result = ledger.decide_claim(claim_id, decision, reason=reason)
        from infrastructure.artifact_lifecycle import record_governance_decision
        record_governance_decision(
            claim_id=claim_id, task_id=task_id,
            previous_decision=before.human_decision, decision=decision,
            reason=reason, session_id=sid,
        )
    except (TypeError, ValueError, OSError) as exc:
        return _error(
            exc,
            404
            if any(token in str(exc) for token in ("does not belong", "outside this session", "does not exist"))
            else 400,
        )
    return jsonify({"ok": True, "claim": result.to_dict()})


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
