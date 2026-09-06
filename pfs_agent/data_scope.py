"""Real-data intake gate and provenance boundary for PFS.

This module is a small, dependency-free gate used when a user declares a real,
desensitized business file.  It never proves a file is safe.  It only checks a
bounded set of obvious PII signals on column names and a small value sample,
records the declared scope and provenance, and refuses to let a real-scope run
claim production verification unless the PII gate passes and a non-empty
provenance record exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence


class DataScopeError(ValueError):
    """Raised when a data scope declaration is malformed."""


class DataScope(str, Enum):
    """Declared origin of a dataset used by a PFS scenario."""

    FIXTURE = "fixture"
    DEMO_ANONYMIZED = "demo_anonymized"
    REAL_DESENSITIZED = "real_desensitized"


_SCOPE_VALUES = frozenset(scope.value for scope in DataScope)

# Column-name signals: official Chinese identifiers plus common English slugs.
_PII_COLUMN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("id_card", re.compile(r"(身份证|证件号|id[_-]?card|national[_-]?id|certificate[_-]?no)")),
    ("phone", re.compile(r"(手机号|手机|电话|联系电话|mobile|phone|tel(?:ephone)?[_-]?(?:no|number)?)")),
    ("bank_card", re.compile(r"(银行卡|卡号|bank[_-]?card|account[_-]?no)")),
    ("email", re.compile(r"(邮箱|电子邮件|e[_-]?mail|mail[_-]?address|电子邮箱)")),
    ("name", re.compile(r"(姓名|名字|真实姓名|full[_-]?name|customer[_-]?name|user[_-]?name|联系人)")),
    ("address", re.compile(r"(住址|联系地址|家庭住址|address|收货地址)")),
    ("wechat", re.compile(r"(微信|wechat|wx[_-]?id|微信号)")),
)

# Value signals applied to a bounded text sample of each column.
_PII_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("id_card", re.compile(r"^\d{17}[\dXx]$")),
    ("phone", re.compile(r"^1[3-9]\d{9}$")),
    ("bank_card", re.compile(r"^\d{16,19}$")),
    ("email", re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")),
)


def _normalize_scope(scope: str | DataScope) -> str:
    value = scope.value if isinstance(scope, DataScope) else str(scope or "").strip()
    if value not in _SCOPE_VALUES:
        raise DataScopeError(
            "data scope must be one of: " + ", ".join(sorted(_SCOPE_VALUES))
        )
    return value


_VALUE_SAMPLE_ROWS = 50


def _column_name_hits(column: str) -> list[str]:
    lowered = str(column or "").strip().lower()
    if not lowered:
        return []
    hits: list[str] = []
    for kind, pattern in _PII_COLUMN_PATTERNS:
        if pattern.search(lowered):
            hits.append(kind)
    return hits


def _value_hits(values: Sequence[Any]) -> dict[str, int]:
    """Count value-pattern hits on a bounded text sample."""
    counts: dict[str, int] = {}
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        if not isinstance(value, (str, int, float)):
            continue
        text = str(value).strip()
        for kind, pattern in _PII_VALUE_PATTERNS:
            if pattern.match(text):
                counts[kind] = counts.get(kind, 0) + 1
                break
    return counts


def scan_data_scope(
    declared_scope: str | DataScope,
    columns: Sequence[Any],
    *,
    sample_rows: Mapping[str, Sequence[Any]] | None = None,
) -> dict[str, Any]:
    """Assess a declared data scope against bounded PII signals.

    ``sample_rows`` maps a column name to up to ``_VALUE_SAMPLE_ROWS`` values.
    Fixture and demo scopes are recorded but never blocked; a real-scope run
    delivers ``pass`` only when no signal is found.  A passing gate is
    ``machine_checked_local`` at most, never a production verification or a
    statement that the file contains no personal information.
    """

    scope = _normalize_scope(declared_scope)
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes, bytearray)):
        raise DataScopeError("columns must be a sequence of column names")
    column_names = [str(column or "").strip() for column in columns]
    if not column_names or any(not name for name in column_names):
        raise DataScopeError("columns must not be empty or contain empty names")
    if len(set(column_names)) != len(column_names):
        raise DataScopeError("columns must not contain duplicate names")
    if sample_rows is not None and not isinstance(sample_rows, Mapping):
        raise DataScopeError("sample_rows must be a mapping of column to values")

    signals: dict[str, list[dict[str, Any]]] = {}
    for name in column_names:
        column_signals: list[dict[str, Any]] = []
        for kind in _column_name_hits(name):
            column_signals.append({"type": "column_name", "pattern": kind})
        if sample_rows is not None and name in sample_rows:
            values = list(sample_rows[name])[:_VALUE_SAMPLE_ROWS]
            for kind, count in _value_hits(values).items():
                column_signals.append(
                    {
                        "type": "value",
                        "pattern": kind,
                        "examples_checked": len(values),
                        "hits": count,
                    }
                )
        if column_signals:
            signals[name] = column_signals

    pii_summary = [{"column": name, "signals": entries} for name, entries in signals.items()]
    blocked_reasons: list[str] = []
    if scope == DataScope.REAL_DESENSITIZED.value and signals:
        blocked_reasons.append(
            "真实脱敏范围检测到明显的身份信息信号：" + ", ".join(sorted(signals))
        )

    value_rows_sampled = (
        max((len(list(values)) for values in sample_rows.values()), default=0)
        if sample_rows is not None
        else 0
    )
    verification_level = (
        "machine_checked_local"
        if scope == DataScope.REAL_DESENSITIZED.value and not blocked_reasons
        else "not_production"
    )
    checks = [
        {
            "check": "pii_scan",
            "suspicious_columns": sorted(signals),
            "passed": not blocked_reasons,
        }
    ]
    return {
        "method": "data_scope_gate",
        "declared_scope": scope,
        "columns_checked": len(column_names),
        "value_rows_sampled": min(value_rows_sampled, _VALUE_SAMPLE_ROWS),
        "pii": pii_summary,
        "blocked_reasons": blocked_reasons,
        "verification_level": verification_level,
        "quality": {
            "status": "block" if blocked_reasons else "pass",
            "passed": not blocked_reasons,
            "checks": checks,
        },
        "limitations": [
            "门禁只检查列名和少量取值的明显身份信息信号（证件/手机/银行卡/邮箱/姓名/地址/微信），不能证明文件完全不含个人信息。",
            "非真实脱敏范围的数据一律不得写成生产口径验证；通过门禁也只代表本地机器检查通过，不代表业务口径审核、外部事实核查或线上部署。",
        ],
    }


@dataclass(frozen=True)
class DataSourceProvenance:
    """Declared provenance of one scenario run's dataset.

    Fields are human declarations recorded for traceability; they are not
    proof of origin.  ``snapshot_sha256`` must be a 64-char hex digest when
    provided.
    """

    scope: str
    source_label: str
    declared_period: str = ""
    snapshot_sha256: str = ""
    declared_by: str = ""
    declared_at: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        scope = _normalize_scope(self.scope)
        source_label = str(self.source_label or "").strip()
        if not source_label:
            raise DataScopeError("source_label must not be empty")
        snapshot = str(self.snapshot_sha256 or "").strip().lower()
        if snapshot and (len(snapshot) != 64 or not re.fullmatch(r"[0-9a-f]{64}", snapshot)):
            raise DataScopeError("snapshot_sha256 must be a 64-char hex digest")
        declared_period = str(self.declared_period or "").strip()
        declared_by = str(self.declared_by or "").strip()
        declared_at = str(self.declared_at or "").strip()
        if declared_at:
            try:
                datetime.fromisoformat(declared_at)
            except ValueError as exc:
                raise DataScopeError("declared_at must be an ISO-8601 timestamp") from exc
        else:
            declared_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "source_label", source_label)
        object.__setattr__(self, "declared_period", declared_period)
        object.__setattr__(self, "snapshot_sha256", snapshot)
        object.__setattr__(self, "declared_by", declared_by)
        object.__setattr__(self, "declared_at", declared_at)
        object.__setattr__(self, "note", str(self.note or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "source_label": self.source_label,
            "declared_period": self.declared_period,
            "snapshot_sha256": self.snapshot_sha256,
            "declared_by": self.declared_by,
            "declared_at": self.declared_at,
            "note": self.note,
        }


def data_verification_level(scope: str | DataScope) -> str:
    """Shortcut used by scenario results before writing any claim."""

    normalized = _normalize_scope(scope)
    if normalized == DataScope.REAL_DESENSITIZED.value:
        return "machine_checked_local"
    return "not_production"
