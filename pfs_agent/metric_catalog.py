"""Versioned, dependency-free metric definitions for the PFS report path.

The catalog is deliberately small.  It gives the deterministic report API a
stable set of versioned definitions without preventing callers from supplying
an explicit custom metric contract for an uploaded source.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

from .reporting import MetricContract, ReportingContractError


_VERSION_PATTERN = re.compile(r"^v[1-9]\d*(?:\.[0-9]+){0,2}$")


class MetricCatalogError(ReportingContractError):
    """Raised when a versioned metric definition cannot be resolved safely."""

    def __init__(self, message: str, *, code: str = "metric_catalog_invalid") -> None:
        super().__init__(message, code=code)


@dataclass(frozen=True)
class MetricCatalogEntry:
    """A user-facing metric definition backed by a deterministic contract."""

    metric: MetricContract
    owner: str
    description: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.metric, MetricContract):
            raise MetricCatalogError("metric must be a MetricContract")
        if not self.owner.strip():
            raise MetricCatalogError("metric owner must not be empty")
        if not self.description.strip():
            raise MetricCatalogError("metric description must not be empty")
        if not _VERSION_PATTERN.fullmatch(self.metric.version):
            raise MetricCatalogError(
                "metric version must use vN, vN.N, or vN.N.N",
                code="metric_version_invalid",
            )
        if any(not str(alias).strip() for alias in self.aliases):
            raise MetricCatalogError("metric aliases must not contain empty values")

    @property
    def key(self) -> tuple[str, str]:
        return self.metric.metric_id, self.metric.version

    def to_dict(self) -> dict[str, object]:
        return {
            **self.metric.to_dict(),
            "owner": self.owner,
            "description": self.description,
            "aliases": list(self.aliases),
        }


class MetricCatalog:
    """An immutable-by-default lookup surface for versioned metrics.

    Registration is intentionally explicit and rejects duplicate
    ``(metric_id, version)`` pairs.  This prevents a later import or retry from
    silently changing the meaning of an already selected metric version.
    """

    def __init__(self, entries: Iterable[MetricCatalogEntry] = ()) -> None:
        self._entries: dict[tuple[str, str], MetricCatalogEntry] = {}
        for entry in entries:
            self.register(entry)

    def register(self, entry: MetricCatalogEntry) -> MetricCatalogEntry:
        if not isinstance(entry, MetricCatalogEntry):
            raise MetricCatalogError("catalog entry must be a MetricCatalogEntry")
        if entry.key in self._entries:
            metric_id, version = entry.key
            raise MetricCatalogError(
                f"metric version already registered: {metric_id} {version}",
                code="metric_version_duplicate",
            )
        self._entries[entry.key] = entry
        return entry

    def entries(self, metric_id: str = "") -> tuple[MetricCatalogEntry, ...]:
        """Return stable catalog entries, optionally filtered by metric id."""
        requested = str(metric_id or "").strip()
        selected = [
            entry for entry in self._entries.values() if not requested or entry.metric.metric_id == requested
        ]
        return tuple(sorted(selected, key=lambda entry: (entry.metric.metric_id, entry.metric.version)))

    def resolve_entry(self, metric_id: str, version: str = "v1") -> MetricCatalogEntry:
        requested_id = str(metric_id or "").strip()
        requested_version = str(version or "").strip()
        if not requested_id:
            raise MetricCatalogError("metric_id must not be empty", code="metric_id_missing")
        if not requested_version:
            raise MetricCatalogError("metric version must not be empty", code="metric_version_missing")
        entry = self._entries.get((requested_id, requested_version))
        if entry:
            return entry
        if self.entries(requested_id):
            available = ", ".join(item.metric.version for item in self.entries(requested_id))
            raise MetricCatalogError(
                f"metric version does not exist: {requested_id} {requested_version}; available: {available}",
                code="metric_version_not_found",
            )
        raise MetricCatalogError(
            f"metric does not exist in catalog: {requested_id}",
            code="metric_not_found",
        )

    def resolve(self, metric_id: str, version: str = "v1") -> MetricContract:
        return self.resolve_entry(metric_id, version).metric

    def to_list(self) -> list[dict[str, object]]:
        return [entry.to_dict() for entry in self.entries()]


DEFAULT_METRIC_CATALOG = MetricCatalog(
    (
        MetricCatalogEntry(
            metric=MetricContract(
                metric_id="profit_amount",
                label="利润",
                formula="SUM(profit_amount)",
                value_column="profit_amount",
                date_column="month",
                dimension="region",
                version="v1",
            ),
            owner="PFS 数据分析",
            description="按月份和地区汇总利润字段。",
            aliases=("利润", "毛利", "净利"),
        ),
        MetricCatalogEntry(
            metric=MetricContract(
                metric_id="revenue",
                label="收入",
                formula="SUM(revenue)",
                value_column="revenue",
                date_column="month",
                dimension="region",
                version="v1",
            ),
            owner="PFS 数据分析",
            description="按月份和地区汇总收入字段。",
            aliases=("收入", "营业额"),
        ),
        MetricCatalogEntry(
            metric=MetricContract(
                metric_id="sales_amount",
                label="销售额",
                formula="SUM(sales_amount)",
                value_column="sales_amount",
                date_column="month",
                dimension="region",
                version="v1",
            ),
            owner="PFS 数据分析",
            description="按月份和地区汇总销售额字段。",
            aliases=("销售额", "销售金额", "销售"),
        ),
    )
)
