"""Bounded natural-language routing for the deterministic PFS report path.

This deliberately small parser is not an LLM and not a general SQL translator.
It creates an explicit AnalysisRequest only when the question is unambiguous.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .reporting import AnalysisRequest, MetricContract, ReportingContractError


class QueryInterpretationError(ReportingContractError):
    """Raised when a report question cannot be mapped safely."""


@dataclass(frozen=True)
class ParsedReportQuestion:
    request: AnalysisRequest
    metric: MetricContract
    interpretation: str

    def to_dict(self) -> dict[str, object]:
        return {
            "request": {
                "metric_id": self.request.metric_id,
                "dimension": self.request.dimension,
                "date_from": self.request.date_from,
                "date_to": self.request.date_to,
            },
            "metric": self.metric.to_dict(),
            "interpretation": self.interpretation,
        }


_DATE_RE = re.compile(r"(20\d{2})[-年](\d{1,2})(?:[-月](\d{1,2})日?)?")


def _find_column(columns: Iterable[str], aliases: tuple[str, ...], label: str) -> str:
    available = tuple(str(column) for column in columns)
    matches = [column for column in available if any(alias.lower() in column.lower() for alias in aliases)]
    if len(matches) != 1:
        if not matches:
            raise QueryInterpretationError(f"问题需要{label}字段，但数据源中没有明确匹配的列")
        raise QueryInterpretationError(f"问题匹配到多个{label}字段，请明确列名：" + ", ".join(matches))
    return matches[0]


def _date_bounds(question: str) -> tuple[str, str]:
    dates = []
    for year, month, day in _DATE_RE.findall(question):
        dates.append(f"{year}-{int(month):02d}-{int(day):02d}" if day else f"{year}-{int(month):02d}")
    if len(dates) > 2:
        raise QueryInterpretationError("问题包含多个时间范围，请只保留起止时间")
    if "本月" in question or "今年" in question:
        raise QueryInterpretationError("本月/今年需要明确的起止时间，系统不会按当前日期猜测")
    if len(dates) == 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        return dates[0], dates[0]
    return "", ""


def parse_report_question(
    question: str, columns: Iterable[str], *, run_id: str = "pfs-query"
) -> ParsedReportQuestion:
    """Parse a narrow, explicit question into a deterministic metric request."""
    text = str(question or "").strip()
    if not text:
        raise QueryInterpretationError("问题不能为空")
    if len(text) > 500:
        raise QueryInterpretationError("问题过长，请先缩小到一个报表问题")
    available = tuple(str(column) for column in columns)
    if not available:
        raise QueryInterpretationError("数据源没有可用字段")
    lowered = text.lower()
    if "利润率" in text or "margin" in lowered:
        raise QueryInterpretationError("目前暂不支持利润率，请先提问利润或销售额合计")
    if "利润" in text or "profit" in lowered or "毛利" in text or "净利" in text:
        value_aliases = ("profit_amount", "profit", "利润", "毛利", "净利")
        metric_label = "利润"
    elif "收入" in text or "revenue" in lowered:
        value_aliases = ("revenue", "收入", "营业额")
        metric_label = "收入"
    else:
        value_aliases = ("sales_amount", "销售额", "销售金额", "销售", "金额")
        metric_label = "销售额"
    value_column = _find_column(available, value_aliases, "指标")
    date_column = _find_column(available, ("month", "月份", "日期", "date", "时间"), "日期")
    dimension = _find_column(available, ("region", "地区", "区域", "省份", "城市", "channel", "渠道"), "分组")
    mentions_metric = any(
        token in lowered for token in ("销售", "收入", "金额", "利润", "毛利", "净利", "revenue", "sales", "profit")
    )
    mentions_dimension = any(
        token in lowered for token in ("地区", "区域", "省份", "城市", "渠道", "region", "channel")
    )
    if not mentions_metric or not mentions_dimension:
        raise QueryInterpretationError("目前只支持“按地区/渠道统计销售额、收入或利润”的明确问题")
    date_from, date_to = _date_bounds(text)
    metric_id = value_column
    metric = MetricContract(
        metric_id=metric_id,
        label=metric_label,
        formula=f"SUM({value_column})",
        value_column=value_column,
        date_column=date_column,
        dimension=dimension,
    )
    request = AnalysisRequest(
        run_id=run_id, metric_id=metric_id, dimension=dimension, date_from=date_from, date_to=date_to
    )
    return ParsedReportQuestion(
        request=request,
        metric=metric,
        interpretation=f"按 {dimension} 分组，对 {value_column} 做 SUM；时间范围 {date_from or '全部'} 至 {date_to or '全部'}。",
    )
