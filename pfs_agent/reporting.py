"""Deterministic reporting contracts and a small CSV analysis path for PFS.

This module is intentionally dependency-free.  It gives the product layer a
stable offline path for snapshotting a report, applying a metric contract, and
returning claims with evidence references before any LLM or database adapter is
involved.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Optional


class ReportingContractError(ValueError):
    """Raised when a reporting request or source violates its contract."""


@dataclass(frozen=True)
class MetricContract:
    metric_id: str
    label: str
    formula: str
    value_column: str
    date_column: str
    dimension: str
    grain: str = "month"
    version: str = "v1"
    exclusions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("metric_id", self.metric_id),
            ("label", self.label),
            ("formula", self.formula),
            ("value_column", self.value_column),
            ("date_column", self.date_column),
            ("dimension", self.dimension),
            ("grain", self.grain),
            ("version", self.version),
        ):
            if not str(value or "").strip():
                raise ReportingContractError(f"{name} must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "label": self.label,
            "formula": self.formula,
            "value_column": self.value_column,
            "date_column": self.date_column,
            "dimension": self.dimension,
            "grain": self.grain,
            "version": self.version,
            "exclusions": list(self.exclusions),
        }


@dataclass(frozen=True)
class AnalysisRequest:
    run_id: str
    metric_id: str
    dimension: str
    date_from: str = ""
    date_to: str = ""

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ReportingContractError("run_id must not be empty")
        if not self.metric_id.strip():
            raise ReportingContractError("metric_id must not be empty")
        if not self.dimension.strip():
            raise ReportingContractError("dimension must not be empty")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ReportingContractError("date_from must not be after date_to")


@dataclass(frozen=True)
class DataSnapshot:
    source_id: str
    file_name: str
    content_sha256: str
    row_count: int
    columns: tuple[str, ...]
    null_counts: Mapping[str, int]
    duplicate_rows: int
    min_date: str = ""
    max_date: str = ""
    rows: tuple[Mapping[str, str], ...] = field(default_factory=tuple, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "file_name": self.file_name,
            "content_sha256": self.content_sha256,
            "row_count": self.row_count,
            "columns": list(self.columns),
            "null_counts": dict(self.null_counts),
            "duplicate_rows": self.duplicate_rows,
            "min_date": self.min_date,
            "max_date": self.max_date,
        }


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    source_id: str
    kind: str
    locator: str
    excerpt: str
    content_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id,
            "kind": self.kind,
            "locator": self.locator,
            "excerpt": self.excerpt,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class AnalysisResult:
    run_id: str
    status: str
    metric: MetricContract
    request: AnalysisRequest
    snapshot: DataSnapshot
    total: int | float
    groups: tuple[dict[str, Any], ...]
    claims: tuple[dict[str, Any], ...]
    evidence: tuple[EvidenceRecord, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "metric": self.metric.to_dict(),
            "request": {
                "run_id": self.request.run_id,
                "metric_id": self.request.metric_id,
                "dimension": self.request.dimension,
                "date_from": self.request.date_from,
                "date_to": self.request.date_to,
            },
            "snapshot": self.snapshot.to_dict(),
            "total": self.total,
            "groups": [dict(item) for item in self.groups],
            "claims": [dict(item) for item in self.claims],
            "evidence": [item.to_dict() for item in self.evidence],
            "warnings": list(self.warnings),
        }


def load_csv_snapshot(
    path: str | Path,
    *,
    source_id: str,
    date_column: str,
) -> DataSnapshot:
    """Read a bounded UTF-8 CSV and record its stable content identity."""
    csv_path = Path(path).resolve()
    if not csv_path.is_file():
        raise ReportingContractError(f"CSV source does not exist: {csv_path}")
    raw = csv_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ReportingContractError("CSV source must be UTF-8 encoded") from exc

    reader = csv.DictReader(text.splitlines())
    columns = tuple(str(column or "").strip() for column in (reader.fieldnames or ()))
    if not columns or any(not column for column in columns):
        raise ReportingContractError("CSV source must have a non-empty header")
    rows: list[dict[str, str]] = []
    null_counts = {column: 0 for column in columns}
    row_fingerprints: set[str] = set()
    duplicate_rows = 0
    dates: list[str] = []
    for raw_row in reader:
        row = {column: str(raw_row.get(column) or "").strip() for column in columns}
        rows.append(row)
        for column, value in row.items():
            if not value:
                null_counts[column] += 1
        fingerprint = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if fingerprint in row_fingerprints:
            duplicate_rows += 1
        row_fingerprints.add(fingerprint)
        if row.get(date_column):
            dates.append(row[date_column])

    return DataSnapshot(
        source_id=source_id.strip() or csv_path.stem,
        file_name=csv_path.name,
        content_sha256=digest,
        row_count=len(rows),
        columns=columns,
        null_counts=null_counts,
        duplicate_rows=duplicate_rows,
        min_date=min(dates) if dates else "",
        max_date=max(dates) if dates else "",
        rows=tuple(rows),
    )


def load_xlsx_snapshot(
    path: str | Path,
    *,
    source_id: str,
    date_column: str,
) -> DataSnapshot:
    """Read the first worksheet of an XLSX without changing cell values.

    XLSX support is deliberately limited to the first worksheet in this first
    vertical slice. The workbook bytes still form the source identity, so the
    resulting evidence can be reproduced later.
    """
    xlsx_path = Path(path).resolve()
    if not xlsx_path.is_file():
        raise ReportingContractError(f"XLSX source does not exist: {xlsx_path}")
    raw = xlsx_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ReportingContractError("XLSX analysis requires the openpyxl dependency") from exc
    try:
        workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
        sheet = workbook.active
        values = list(sheet.iter_rows(values_only=True))
    except Exception as exc:
        raise ReportingContractError("XLSX source could not be read") from exc
    finally:
        try:
            workbook.close()
        except UnboundLocalError:
            pass
    if not values:
        raise ReportingContractError("XLSX source must have a non-empty header")
    columns = tuple(str(value or "").strip() for value in values[0])
    if not columns or any(not column for column in columns):
        raise ReportingContractError("XLSX source must have a non-empty header")
    rows: list[dict[str, str]] = []
    null_counts = {column: 0 for column in columns}
    row_fingerprints: set[str] = set()
    duplicate_rows = 0
    dates: list[str] = []
    for values_row in values[1:]:
        row = {
            column: str(values_row[index] if index < len(values_row) and values_row[index] is not None else "").strip()
            for index, column in enumerate(columns)
        }
        if not any(row.values()):
            continue
        rows.append(row)
        for column, value in row.items():
            if not value:
                null_counts[column] += 1
        fingerprint = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if fingerprint in row_fingerprints:
            duplicate_rows += 1
        row_fingerprints.add(fingerprint)
        if row.get(date_column):
            dates.append(row[date_column])
    return DataSnapshot(
        source_id=source_id.strip() or xlsx_path.stem,
        file_name=xlsx_path.name,
        content_sha256=digest,
        row_count=len(rows),
        columns=columns,
        null_counts=null_counts,
        duplicate_rows=duplicate_rows,
        min_date=min(dates) if dates else "",
        max_date=max(dates) if dates else "",
        rows=tuple(rows),
    )


def load_tabular_snapshot(path: str | Path, *, source_id: str, date_column: str) -> DataSnapshot:
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return load_csv_snapshot(path, source_id=source_id, date_column=date_column)
    if suffix == ".xlsx":
        return load_xlsx_snapshot(path, source_id=source_id, date_column=date_column)
    raise ReportingContractError("PFS deterministic analysis accepts CSV or XLSX files only")


def analyze_snapshot(
    snapshot: DataSnapshot,
    *,
    metric: MetricContract,
    request: AnalysisRequest,
) -> AnalysisResult:
    """Apply the deterministic grouped SUM contract to a loaded snapshot."""
    return _analyze_snapshot(snapshot, metric=metric, request=request)


def analyze_csv(
    path: str | Path,
    *,
    metric: MetricContract,
    request: AnalysisRequest,
    source_id: str = "fixture",
) -> AnalysisResult:
    """Run the first deterministic PFS report analysis: grouped SUM."""
    if request.metric_id != metric.metric_id:
        raise ReportingContractError("request metric_id does not match metric contract")
    if request.dimension != metric.dimension:
        raise ReportingContractError("request dimension does not match metric contract")
    snapshot = load_csv_snapshot(
        path,
        source_id=source_id,
        date_column=metric.date_column,
    )
    return _analyze_snapshot(snapshot, metric=metric, request=request)


def analyze_file(
    path: str | Path,
    *,
    metric: MetricContract,
    request: AnalysisRequest,
    source_id: str = "fixture",
) -> AnalysisResult:
    """Analyze a supported CSV or XLSX source with one shared contract."""
    snapshot = load_tabular_snapshot(path, source_id=source_id, date_column=metric.date_column)
    return _analyze_snapshot(snapshot, metric=metric, request=request)


def _analyze_snapshot(
    snapshot: DataSnapshot,
    *,
    metric: MetricContract,
    request: AnalysisRequest,
) -> AnalysisResult:
    if request.metric_id != metric.metric_id:
        raise ReportingContractError("request metric_id does not match metric contract")
    if request.dimension != metric.dimension:
        raise ReportingContractError("request dimension does not match metric contract")
    missing_columns = {
        metric.value_column,
        metric.date_column,
        request.dimension,
    } - set(snapshot.columns)
    if missing_columns:
        raise ReportingContractError(
            "metric columns missing from snapshot: " + ", ".join(sorted(missing_columns))
        )

    buckets: dict[str, Decimal] = {}
    total = Decimal("0")
    warnings: list[str] = []
    included_rows = 0
    for row in snapshot.rows:
        date_value = row.get(metric.date_column, "")
        if request.date_from and date_value < request.date_from:
            continue
        if request.date_to and date_value > request.date_to:
            continue
        dimension_value = row.get(request.dimension, "")
        raw_value = row.get(metric.value_column, "")
        if not dimension_value or not raw_value:
            warnings.append("存在缺少分组字段或指标值的行，已从本次汇总排除。")
            continue
        try:
            amount = Decimal(raw_value)
        except InvalidOperation as exc:
            raise ReportingContractError(f"metric value is not numeric: {raw_value!r}") from exc
        buckets[dimension_value] = buckets.get(dimension_value, Decimal("0")) + amount
        total += amount
        included_rows += 1

    def _number(value: Decimal) -> int | float:
        return int(value) if value == value.to_integral_value() else float(value)

    groups = tuple(
        {"dimension": name, "value": _number(value), "rank": rank}
        for rank, (name, value) in enumerate(
            sorted(buckets.items(), key=lambda item: (-item[1], item[0])), start=1
        )
    )
    evidence_text = (
        f"{snapshot.file_name} · sha256:{snapshot.content_sha256[:16]} · "
        f"included_rows:{included_rows} · date:{request.date_from or snapshot.min_date}"
        f"..{request.date_to or snapshot.max_date}"
    )
    evidence_id = "ev_" + hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()[:16]
    evidence = EvidenceRecord(
        evidence_id=evidence_id,
        source_id=snapshot.source_id,
        kind="csv_snapshot",
        locator=f"{snapshot.file_name}#sha256={snapshot.content_sha256}",
        excerpt=evidence_text,
        content_sha256=snapshot.content_sha256,
    )
    top_group = groups[0] if groups else None
    claims = [
        {
            "claim_id": "cl_" + hashlib.sha256(f"{request.run_id}:total".encode("utf-8")).hexdigest()[:16],
            "text": f"{metric.label}合计为 {total}",
            "status": "supported" if included_rows else "unverified",
            "confidence": 1.0 if included_rows else 0.0,
            "evidence_ids": [evidence_id],
        }
    ]
    if top_group:
        claims.append(
            {
                "claim_id": "cl_" + hashlib.sha256(f"{request.run_id}:top".encode("utf-8")).hexdigest()[:16],
                "text": f"{metric.label}最高的{request.dimension}为 {top_group['dimension']}",
                "status": "supported",
                "confidence": 1.0,
                "evidence_ids": [evidence_id],
            }
        )
    return AnalysisResult(
        run_id=request.run_id,
        status="completed" if included_rows else "unverified",
        metric=metric,
        request=request,
        snapshot=snapshot,
        total=_number(total),
        groups=groups,
        claims=tuple(claims),
        evidence=(evidence,),
        warnings=tuple(sorted(set(warnings))),
    )
