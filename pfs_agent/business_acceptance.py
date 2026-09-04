"""Deterministic business acceptance for the bundled city portfolio scenario.

This module intentionally answers a narrow, reviewable business question.  It
does not turn city-level averages into an accounting profit claim: the source
does not contain commission revenue, merchant settlement, headquarters cost,
or every other profit-and-loss item needed for that conclusion.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any

from .reporting import DataSnapshot


class BusinessAcceptanceError(ValueError):
    """Stable, user-actionable failure for a business acceptance request."""

    def __init__(self, message: str, *, code: str = "business_acceptance_failed") -> None:
        super().__init__(message)
        self.code = code


SCENARIO_ID = "city_portfolio_v1"
MONTHLY_PNL_SCENARIO_ID = "city_monthly_pnl_v1"
USER_SUPPLY_SCENARIO_ID = "city_user_supply_v1"

CITY_ID = "城市编号"
CITY = "城市"
PROVINCE = "省份"
OPEN_YEAR = "Instant Retail Co.开城年份"
DAILY_ORDERS_K = "2025年日均订单量（千单）"
ORDER_GROWTH = "订单年增速（%）"
MARKET_SHARE = "Instant Retail Co.市占率（%）"
AOV = "平均客单价（元）"
FULFILLMENT_COST = "平均履约成本/单（元）"
SUBSIDY_COST = "平均补贴及营销/单（元）"
PROFITABILITY_STATUS = "城市当前盈利状况"
MONTHLY_ACTIVE_USERS = "月度活跃用户数（万）"
HIGH_VALUE_USER_SHARE = "高价值用户占比（%）"
ACTIVE_MERCHANTS = "活跃合作商家数"
CHAIN_MERCHANT_SHARE = "大型商超及连锁便利店占比（%）"
ORDER_MIX_COLUMNS = (
    "订单结构_到家快消占比（%）",
    "订单结构_生鲜占比（%）",
    "订单结构_餐饮占比（%）",
    "订单结构_其他占比（%）",
)
REQUIRED_COLUMNS = (
    CITY_ID,
    CITY,
    PROVINCE,
    OPEN_YEAR,
    DAILY_ORDERS_K,
    ORDER_GROWTH,
    MARKET_SHARE,
    AOV,
    FULFILLMENT_COST,
    SUBSIDY_COST,
    PROFITABILITY_STATUS,
    MONTHLY_ACTIVE_USERS,
    HIGH_VALUE_USER_SHARE,
    ACTIVE_MERCHANTS,
    CHAIN_MERCHANT_SHARE,
    *ORDER_MIX_COLUMNS,
)
NUMERIC_COLUMNS = (
    OPEN_YEAR,
    DAILY_ORDERS_K,
    ORDER_GROWTH,
    MARKET_SHARE,
    AOV,
    FULFILLMENT_COST,
    SUBSIDY_COST,
    MONTHLY_ACTIVE_USERS,
    HIGH_VALUE_USER_SHARE,
    ACTIVE_MERCHANTS,
    CHAIN_MERCHANT_SHARE,
    *ORDER_MIX_COLUMNS,
)
PERCENT_COLUMNS = (
    ORDER_GROWTH,
    MARKET_SHARE,
    HIGH_VALUE_USER_SHARE,
    CHAIN_MERCHANT_SHARE,
    *ORDER_MIX_COLUMNS,
)
NONNEGATIVE_COLUMNS = (
    DAILY_ORDERS_K,
    AOV,
    FULFILLMENT_COST,
    SUBSIDY_COST,
    MONTHLY_ACTIVE_USERS,
    ACTIVE_MERCHANTS,
)

MONTH = "月份"
MONTHLY_ORDERS = "订单量（万单）"
GMV = "GMV（万元）"
COMMISSION_REVENUE = "佣金收入（万元）"
DELIVERY_REVENUE = "配送收入（万元）"
MERCHANT_SETTLEMENT_COST = "商家结算成本（万元）"
MONTHLY_FULFILLMENT_COST = "履约成本（万元）"
MONTHLY_SUBSIDY_COST = "补贴营销成本（万元）"
PAYMENT_COST = "支付成本（万元）"
HEADQUARTERS_COST = "总部摊销（万元）"
REPORTED_CONTRIBUTION = "报表贡献利润（万元）"
REPORTED_OPERATING_PROFIT = "报表经营利润（万元）"
MONTHLY_PNL_REQUIRED_COLUMNS = (
    MONTH,
    CITY_ID,
    CITY,
    MONTHLY_ORDERS,
    GMV,
    COMMISSION_REVENUE,
    DELIVERY_REVENUE,
    MERCHANT_SETTLEMENT_COST,
    MONTHLY_FULFILLMENT_COST,
    MONTHLY_SUBSIDY_COST,
    PAYMENT_COST,
    HEADQUARTERS_COST,
    REPORTED_CONTRIBUTION,
    REPORTED_OPERATING_PROFIT,
)
MONTHLY_PNL_NUMERIC_COLUMNS = MONTHLY_PNL_REQUIRED_COLUMNS[3:]
MONTHLY_PNL_NONNEGATIVE_COLUMNS = MONTHLY_PNL_NUMERIC_COLUMNS[:-2]

USER_SUPPLY_REQUIRED_COLUMNS = (
    CITY_ID,
    CITY,
    PROVINCE,
    DAILY_ORDERS_K,
    MARKET_SHARE,
    MONTHLY_ACTIVE_USERS,
    HIGH_VALUE_USER_SHARE,
    ACTIVE_MERCHANTS,
    CHAIN_MERCHANT_SHARE,
)
USER_SUPPLY_NUMERIC_COLUMNS = USER_SUPPLY_REQUIRED_COLUMNS[3:]
USER_SUPPLY_PERCENT_COLUMNS = (
    MARKET_SHARE,
    HIGH_VALUE_USER_SHARE,
    CHAIN_MERCHANT_SHARE,
)
USER_SUPPLY_NONNEGATIVE_COLUMNS = (
    DAILY_ORDERS_K,
    MONTHLY_ACTIVE_USERS,
    ACTIVE_MERCHANTS,
)


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise BusinessAcceptanceError("source contains invalid numeric values") from exc


def _rounded(value: Decimal, places: int = 2) -> float:
    quantum = Decimal(1).scaleb(-places)
    return float(value.quantize(quantum, rounding=ROUND_HALF_UP))


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    if not ordered:
        raise BusinessAcceptanceError("cannot calculate a median from an empty set")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _check(
    check_id: str,
    label: str,
    status: str,
    *,
    severity: str,
    evidence: str,
    impact: str = "",
) -> dict[str, str]:
    return {
        "check_id": check_id,
        "label": label,
        "status": status,
        "severity": severity,
        "evidence": evidence,
        "impact": impact,
    }


def _base_payload(snapshot: DataSnapshot, checks: list[dict[str, str]]) -> dict[str, Any]:
    blockers = [item for item in checks if item["status"] == "fail"]
    evidence_id = f"business-snapshot-{snapshot.content_sha256[:16]}"
    return {
        "scenario": {
            "id": SCENARIO_ID,
            "name": "城市经营组合诊断",
            "question": "10 个城市的经营规模、增长和单位贡献表现如何，管理层应优先关注哪里？",
            "audience": "经营负责人",
            "grain": "每行一个城市",
        },
        "source": {
            "source_id": snapshot.source_id,
            "file_name": snapshot.file_name,
            "worksheet": snapshot.worksheet,
            "content_sha256": snapshot.content_sha256,
            "row_count": snapshot.row_count,
        },
        "assessment": "needs_revision" if blockers else "share_with_caveats",
        "quality": {
            "checks": checks,
            "blocker_count": len(blockers),
            "passed_count": sum(item["status"] == "pass" for item in checks),
        },
        "metrics": None,
        "rankings": {},
        "claims": [],
        "evidence": [
            {
                "evidence_id": evidence_id,
                "kind": "tabular_snapshot",
                "source_id": snapshot.source_id,
                "file_name": snapshot.file_name,
                "worksheet": snapshot.worksheet,
                "included_rows": snapshot.row_count,
                "content_sha256": snapshot.content_sha256,
                "locator": (
                    f"{snapshot.file_name}#{snapshot.worksheet}#sha256="
                    f"{snapshot.content_sha256}"
                ),
                "columns": list(snapshot.columns),
            }
        ],
        "caveats": [
            "年化值按当前日均订单量乘以 365 天测算，未建模季节性和未来增长。",
            "订单年增速是源表提供的横截面字段，缺少逐期明细，无法在本次验收中独立复算。",
            "单位贡献余量仅为客单价减履约成本及补贴营销，不等于收入、毛利或净利润。",
            "源表缺少佣金收入、商家结算、总部费用及其他损益项，不能据此解释城市亏损原因。",
        ],
    }


def evaluate_city_portfolio(snapshot: DataSnapshot) -> dict[str, Any]:
    """Validate and summarize a city portfolio without overstating profitability."""
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in snapshot.columns]
    if missing_columns:
        raise BusinessAcceptanceError(
            "city portfolio columns missing: " + ", ".join(missing_columns),
            code="business_scenario_columns_missing",
        )

    rows = list(snapshot.rows)
    checks: list[dict[str, str]] = []
    city_ids = [row[CITY_ID] for row in rows]
    duplicate_ids = sorted({item for item in city_ids if city_ids.count(item) > 1})
    checks.append(
        _check(
            "city_grain_unique",
            "城市粒度唯一",
            "fail" if duplicate_ids else "pass",
            severity="critical",
            evidence=("重复城市编号：" + "、".join(duplicate_ids)) if duplicate_ids else f"{len(city_ids)} 个城市编号均唯一",
            impact="重复城市会放大汇总值" if duplicate_ids else "",
        )
    )

    missing_cells = [
        f"{row.get(CITY_ID) or index + 1}:{column}"
        for index, row in enumerate(rows)
        for column in REQUIRED_COLUMNS
        if not str(row.get(column, "")).strip()
    ]
    checks.append(
        _check(
            "required_values_complete",
            "关键字段完整",
            "fail" if missing_cells else "pass",
            severity="critical",
            evidence=("缺失单元格：" + "、".join(missing_cells[:8])) if missing_cells else "关键字段无缺失",
            impact="无法稳定计算经营指标" if missing_cells else "",
        )
    )

    parsed_rows: list[dict[str, Decimal]] = []
    invalid_numeric: list[str] = []
    for index, row in enumerate(rows):
        parsed: dict[str, Decimal] = {}
        for column in NUMERIC_COLUMNS:
            try:
                parsed[column] = _decimal(row.get(column, ""))
            except BusinessAcceptanceError:
                invalid_numeric.append(f"{row.get(CITY_ID) or index + 1}:{column}")
        parsed_rows.append(parsed)
    checks.append(
        _check(
            "numeric_values_valid",
            "数值字段可计算",
            "fail" if invalid_numeric else "pass",
            severity="critical",
            evidence=("非法数值：" + "、".join(invalid_numeric[:8])) if invalid_numeric else "所有数值字段均可解析",
            impact="非法数值会导致指标失真" if invalid_numeric else "",
        )
    )

    out_of_range: list[str] = []
    negative_values: list[str] = []
    mix_mismatches: list[str] = []
    if not invalid_numeric:
        for row, parsed in zip(rows, parsed_rows):
            city_id = row[CITY_ID]
            out_of_range.extend(
                f"{city_id}:{column}={parsed[column]}"
                for column in PERCENT_COLUMNS
                if not Decimal("0") <= parsed[column] <= Decimal("100")
            )
            negative_values.extend(
                f"{city_id}:{column}={parsed[column]}"
                for column in NONNEGATIVE_COLUMNS
                if parsed[column] < 0
            )
            mix_total = sum((parsed[column] for column in ORDER_MIX_COLUMNS), Decimal("0"))
            if abs(mix_total - Decimal("100")) > Decimal("0.01"):
                mix_mismatches.append(f"{city_id}={mix_total}")
    checks.extend(
        [
            _check(
                "business_ranges_valid",
                "比例与金额范围有效",
                "fail" if out_of_range or negative_values else "pass",
                severity="high",
                evidence=("；".join((out_of_range + negative_values)[:8])) if out_of_range or negative_values else "比例位于 0–100，金额与规模非负",
                impact="超界值会破坏排序和加权指标" if out_of_range or negative_values else "",
            ),
            _check(
                "order_mix_reconciles",
                "订单结构逐城加总为 100%",
                "fail" if mix_mismatches else "pass",
                severity="high",
                evidence=("未勾稽：" + "、".join(mix_mismatches[:8])) if mix_mismatches else f"{len(rows)} 行全部勾稽",
                impact="订单结构存在遗漏或重复分类" if mix_mismatches else "",
            ),
            _check(
                "duplicate_rows_absent",
                "整行记录无重复",
                "fail" if snapshot.duplicate_rows else "pass",
                severity="high",
                evidence=f"发现 {snapshot.duplicate_rows} 条重复记录" if snapshot.duplicate_rows else "未发现整行重复",
                impact="重复记录会放大汇总值" if snapshot.duplicate_rows else "",
            ),
        ]
    )

    payload = _base_payload(snapshot, checks)
    if payload["quality"]["blocker_count"]:
        return payload

    annual_days = Decimal("365")
    thousand = Decimal("1000")
    hundred_million = Decimal("100000000")
    enriched: list[dict[str, Any]] = []
    total_annual_orders = Decimal("0")
    total_annual_gmv = Decimal("0")
    total_fulfillment = Decimal("0")
    total_subsidy = Decimal("0")
    total_modeled_contribution = Decimal("0")
    for row, parsed in zip(rows, parsed_rows):
        annual_orders = parsed[DAILY_ORDERS_K] * thousand * annual_days
        annual_gmv = annual_orders * parsed[AOV]
        unit_contribution = parsed[AOV] - parsed[FULFILLMENT_COST] - parsed[SUBSIDY_COST]
        modeled_contribution = annual_orders * unit_contribution
        total_annual_orders += annual_orders
        total_annual_gmv += annual_gmv
        total_fulfillment += annual_orders * parsed[FULFILLMENT_COST]
        total_subsidy += annual_orders * parsed[SUBSIDY_COST]
        total_modeled_contribution += modeled_contribution
        enriched.append(
            {
                "city_id": row[CITY_ID],
                "city": row[CITY],
                "province": row[PROVINCE],
                "daily_orders_thousand": _rounded(parsed[DAILY_ORDERS_K], 2),
                "order_growth_pct": _rounded(parsed[ORDER_GROWTH], 2),
                "market_share_pct": _rounded(parsed[MARKET_SHARE], 2),
                "annual_gmv_100m": _rounded(annual_gmv / hundred_million, 4),
                "modeled_unit_contribution_yuan": _rounded(unit_contribution, 2),
                "declared_profitability": row[PROFITABILITY_STATUS],
            }
        )

    weighted_aov = total_annual_gmv / total_annual_orders
    weighted_fulfillment = total_fulfillment / total_annual_orders
    weighted_subsidy = total_subsidy / total_annual_orders
    loss_count = sum("亏损" in row[PROFITABILITY_STATUS] for row in rows)
    scale_leaders = sorted(enriched, key=lambda item: (-item["annual_gmv_100m"], item["city_id"]))[:3]
    growth_leaders = sorted(enriched, key=lambda item: (-item["order_growth_pct"], item["city_id"]))[:3]
    contribution_leaders = sorted(
        enriched,
        key=lambda item: (-item["modeled_unit_contribution_yuan"], item["city_id"]),
    )[:3]

    payload["metrics"] = {
        "city_count": len(rows),
        "daily_orders_thousand": _rounded(sum((item[DAILY_ORDERS_K] for item in parsed_rows), Decimal("0")), 2),
        "annual_orders": int(total_annual_orders),
        "annual_gmv_yuan": _rounded(total_annual_gmv, 2),
        "annual_gmv_100m": _rounded(total_annual_gmv / hundred_million, 4),
        "weighted_aov_yuan": _rounded(weighted_aov, 2),
        "weighted_fulfillment_cost_yuan": _rounded(weighted_fulfillment, 2),
        "weighted_subsidy_cost_yuan": _rounded(weighted_subsidy, 2),
        "modeled_unit_contribution_yuan": _rounded(
            weighted_aov - weighted_fulfillment - weighted_subsidy, 2
        ),
        "modeled_annual_contribution_yuan": _rounded(total_modeled_contribution, 2),
        "modeled_annual_contribution_100m": _rounded(
            total_modeled_contribution / hundred_million, 4
        ),
        "declared_loss_city_count": loss_count,
    }
    payload["rankings"] = {
        "scale_leaders": scale_leaders,
        "growth_leaders": growth_leaders,
        "modeled_unit_contribution_leaders": contribution_leaders,
    }
    payload["claims"] = [
        {
            "claim": f"{len(rows)} 城日均订单合计 {payload['metrics']['daily_orders_thousand']:.0f} 千单，按 365 天年化 GMV 为 {payload['metrics']['annual_gmv_100m']:.4f} 亿元。",
            "status": "supported",
            "basis": "逐城日均订单量乘 1,000，乘 365，再乘平均客单价后加总",
            "evidence_ids": [payload["evidence"][0]["evidence_id"]],
        },
        {
            "claim": f"规模前三城市为{'、'.join(item['city'] for item in scale_leaders)}。",
            "status": "supported",
            "basis": "按逐城年化 GMV 降序排列",
            "evidence_ids": [payload["evidence"][0]["evidence_id"]],
        },
        {
            "claim": f"源表有 {loss_count}/{len(rows)} 个城市状态包含“亏损”，但现有字段不足以归因亏损。",
            "status": "supported_with_caveat",
            "basis": "盈利状态文本计数；未将单位贡献余量当作利润",
            "evidence_ids": [payload["evidence"][0]["evidence_id"]],
        },
    ]
    payload["lineage"] = {
        "annual_orders": f"{DAILY_ORDERS_K} * 1,000 * 365",
        "annual_gmv": f"annual_orders * {AOV}",
        "modeled_unit_contribution": f"{AOV} - {FULFILLMENT_COST} - {SUBSIDY_COST}",
        "weighted_average": "SUM(city_value * city_annual_orders) / SUM(city_annual_orders)",
    }
    return payload


def _user_supply_base_payload(
    snapshot: DataSnapshot, checks: list[dict[str, str]]
) -> dict[str, Any]:
    blockers = [item for item in checks if item["status"] == "fail"]
    evidence_id = f"business-snapshot-{snapshot.content_sha256[:16]}"
    return {
        "scenario": {
            "id": USER_SUPPLY_SCENARIO_ID,
            "name": "城市用户—供给效率诊断",
            "question": "各城市的高价值用户规模、用户活跃度和商家承载效率如何，应该优先核查哪里？",
            "audience": "经营与城市运营负责人",
            "grain": "每行一个城市",
        },
        "source": {
            "source_id": snapshot.source_id,
            "file_name": snapshot.file_name,
            "worksheet": snapshot.worksheet,
            "content_sha256": snapshot.content_sha256,
            "row_count": snapshot.row_count,
        },
        "assessment": "needs_revision" if blockers else "share_with_caveats",
        "quality": {
            "checks": checks,
            "blocker_count": len(blockers),
            "passed_count": sum(item["status"] == "pass" for item in checks),
        },
        "metrics": None,
        "rankings": {},
        "claims": [],
        "evidence": [
            {
                "evidence_id": evidence_id,
                "kind": "tabular_snapshot",
                "source_id": snapshot.source_id,
                "file_name": snapshot.file_name,
                "worksheet": snapshot.worksheet,
                "included_rows": snapshot.row_count,
                "content_sha256": snapshot.content_sha256,
                "locator": (
                    f"{snapshot.file_name}#{snapshot.worksheet}#sha256="
                    f"{snapshot.content_sha256}"
                ),
                "columns": list(snapshot.columns),
            }
        ],
        "caveats": [
            "这是项目随附的匿名化城市快照，不是公司生产经营数据。",
            "用户日频用日均订单量除以月活用户估算，两个字段的观测时点和统计口径可能不同，只适合方向性比较。",
            "高价值用户占比和活跃商家数来自源表，未接入用户事件、留存、转化或商家履约明细，无法解释效率差异原因。",
            "双低观察按本批城市中位数筛选，不代表经营红线，也不构成因果判断或预算建议。",
        ],
    }


def evaluate_city_user_supply(snapshot: DataSnapshot) -> dict[str, Any]:
    """Assess user scale and merchant supply efficiency for each city.

    The source is a city-level snapshot, so this function reports comparable
    directional ratios.  It deliberately avoids calling them retention,
    conversion, profitability, or causal drivers.
    """
    missing_columns = [
        column for column in USER_SUPPLY_REQUIRED_COLUMNS if column not in snapshot.columns
    ]
    if missing_columns:
        raise BusinessAcceptanceError(
            "city user-supply columns missing: " + ", ".join(missing_columns),
            code="business_scenario_columns_missing",
        )

    rows = list(snapshot.rows)
    checks: list[dict[str, str]] = []
    checks.append(
        _check(
            "city_user_supply_rows_present",
            "城市快照存在记录",
            "pass" if rows else "fail",
            severity="critical",
            evidence=f"读取 {len(rows)} 行城市记录" if rows else "未读取到城市记录",
            impact="没有记录就无法计算用户或供给效率" if not rows else "",
        )
    )
    city_ids = [row.get(CITY_ID, "") for row in rows]
    duplicate_ids = sorted({item for item in city_ids if city_ids.count(item) > 1})
    checks.append(
        _check(
            "city_user_supply_grain_unique",
            "城市粒度唯一",
            "fail" if duplicate_ids else "pass",
            severity="critical",
            evidence=("重复城市编号：" + "、".join(duplicate_ids[:8]))
            if duplicate_ids
            else f"{len(city_ids)} 个城市编号均唯一",
            impact="重复城市会放大用户和商家汇总" if duplicate_ids else "",
        )
    )

    missing_cells = [
        f"{row.get(CITY_ID) or index + 1}:{column}"
        for index, row in enumerate(rows)
        for column in USER_SUPPLY_REQUIRED_COLUMNS
        if not str(row.get(column, "")).strip()
    ]
    checks.append(
        _check(
            "user_supply_values_complete",
            "用户与供给字段完整",
            "fail" if missing_cells else "pass",
            severity="critical",
            evidence=("缺失单元格：" + "、".join(missing_cells[:8]))
            if missing_cells
            else "用户、订单和商家字段无缺失",
            impact="缺失值会破坏效率比率" if missing_cells else "",
        )
    )

    parsed_rows: list[dict[str, Decimal]] = []
    invalid_numeric: list[str] = []
    for index, row in enumerate(rows):
        parsed: dict[str, Decimal] = {}
        for column in USER_SUPPLY_NUMERIC_COLUMNS:
            try:
                parsed[column] = _decimal(row.get(column, ""))
            except BusinessAcceptanceError:
                invalid_numeric.append(f"{row.get(CITY_ID) or index + 1}:{column}")
        parsed_rows.append(parsed)
    checks.append(
        _check(
            "user_supply_numeric_values_valid",
            "用户与供给数值可计算",
            "fail" if invalid_numeric else "pass",
            severity="critical",
            evidence=("非法数值：" + "、".join(invalid_numeric[:8]))
            if invalid_numeric
            else "所有用户、订单和商家数值均可解析",
            impact="非法数值会导致效率排序失真" if invalid_numeric else "",
        )
    )

    invalid_ranges: list[str] = []
    invalid_denominators: list[str] = []
    if not invalid_numeric:
        for row, parsed in zip(rows, parsed_rows):
            city_id = row[CITY_ID]
            invalid_ranges.extend(
                f"{city_id}:{column}={parsed[column]}"
                for column in USER_SUPPLY_PERCENT_COLUMNS
                if not Decimal("0") <= parsed[column] <= Decimal("100")
            )
            invalid_ranges.extend(
                f"{city_id}:{column}={parsed[column]}"
                for column in USER_SUPPLY_NONNEGATIVE_COLUMNS
                if parsed[column] < 0
            )
            invalid_denominators.extend(
                f"{city_id}:{column}={parsed[column]}"
                for column in (MONTHLY_ACTIVE_USERS, ACTIVE_MERCHANTS)
                if parsed[column] <= 0
            )
    checks.extend(
        [
            _check(
                "user_supply_business_ranges_valid",
                "比例与规模范围有效",
                "fail" if invalid_ranges else "pass",
                severity="high",
                evidence=("异常值：" + "、".join(invalid_ranges[:8]))
                if invalid_ranges
                else "比例位于 0–100，订单、月活和商家数非负",
                impact="超界值会破坏用户与供给比较" if invalid_ranges else "",
            ),
            _check(
                "user_supply_denominators_positive",
                "效率分母为正",
                "fail" if invalid_denominators else "pass",
                severity="critical",
                evidence=("非正分母：" + "、".join(invalid_denominators[:8]))
                if invalid_denominators
                else "月活用户数和活跃商家数均大于 0",
                impact="无法稳定计算用户日频或商家承载" if invalid_denominators else "",
            ),
            _check(
                "user_supply_duplicate_rows_absent",
                "整行记录无重复",
                "fail" if snapshot.duplicate_rows else "pass",
                severity="high",
                evidence=(
                    f"发现 {snapshot.duplicate_rows} 条重复记录"
                    if snapshot.duplicate_rows
                    else "未发现整行重复"
                ),
                impact="重复记录会放大用户和商家汇总" if snapshot.duplicate_rows else "",
            ),
        ]
    )

    payload = _user_supply_base_payload(snapshot, checks)
    if payload["quality"]["blocker_count"]:
        return payload

    total_mau = sum((parsed[MONTHLY_ACTIVE_USERS] for parsed in parsed_rows), Decimal("0"))
    total_orders = sum((parsed[DAILY_ORDERS_K] for parsed in parsed_rows), Decimal("0"))
    total_merchants = sum((parsed[ACTIVE_MERCHANTS] for parsed in parsed_rows), Decimal("0"))
    total_high_value_users = sum(
        (
            parsed[MONTHLY_ACTIVE_USERS]
            * parsed[HIGH_VALUE_USER_SHARE]
            / Decimal("100")
            for parsed in parsed_rows
        ),
        Decimal("0"),
    )
    city_metrics: list[dict[str, Any]] = []
    efficiency_by_city: dict[str, tuple[Decimal, Decimal]] = {}
    for row, parsed in zip(rows, parsed_rows):
        high_value_users = (
            parsed[MONTHLY_ACTIVE_USERS]
            * parsed[HIGH_VALUE_USER_SHARE]
            / Decimal("100")
        )
        user_frequency = parsed[DAILY_ORDERS_K] / (
            Decimal("10") * parsed[MONTHLY_ACTIVE_USERS]
        )
        merchant_productivity = (
            parsed[DAILY_ORDERS_K] * Decimal("1000") / parsed[ACTIVE_MERCHANTS]
        )
        efficiency_by_city[row[CITY_ID]] = (user_frequency, merchant_productivity)
        city_metrics.append(
            {
                "city_id": row[CITY_ID],
                "city": row[CITY],
                "province": row[PROVINCE],
                "high_value_users_10k": _rounded(high_value_users, 2),
                "high_value_user_share_pct": _rounded(parsed[HIGH_VALUE_USER_SHARE], 2),
                "daily_orders_thousand": _rounded(parsed[DAILY_ORDERS_K], 2),
                "monthly_active_users_10k": _rounded(parsed[MONTHLY_ACTIVE_USERS], 2),
                "user_order_frequency_per_day": _rounded(user_frequency, 3),
                "merchant_productivity_orders_per_day": _rounded(merchant_productivity, 2),
                "active_merchants": int(parsed[ACTIVE_MERCHANTS]),
                "market_share_pct": _rounded(parsed[MARKET_SHARE], 2),
                "chain_merchant_share_pct": _rounded(parsed[CHAIN_MERCHANT_SHARE], 2),
            }
        )

    user_frequency_median = _median(
        [
            parsed[DAILY_ORDERS_K] / (Decimal("10") * parsed[MONTHLY_ACTIVE_USERS])
            for parsed in parsed_rows
        ]
    )
    merchant_productivity_median = _median(
        [
            parsed[DAILY_ORDERS_K] * Decimal("1000") / parsed[ACTIVE_MERCHANTS]
            for parsed in parsed_rows
        ]
    )
    for item in city_metrics:
        user_frequency, merchant_productivity = efficiency_by_city[item["city_id"]]
        item["dual_efficiency_attention"] = (
            user_frequency < user_frequency_median
            and merchant_productivity < merchant_productivity_median
        )

    high_value_leaders = sorted(
        city_metrics,
        key=lambda item: (-item["high_value_users_10k"], item["city_id"]),
    )[:3]
    user_engagement_leaders = sorted(
        city_metrics,
        key=lambda item: (-item["user_order_frequency_per_day"], item["city_id"]),
    )[:3]
    merchant_productivity_leaders = sorted(
        city_metrics,
        key=lambda item: (
            -item["merchant_productivity_orders_per_day"],
            item["city_id"],
        ),
    )[:3]
    attention_candidates = sorted(
        [item for item in city_metrics if item["dual_efficiency_attention"]],
        key=lambda item: (
            item["user_order_frequency_per_day"],
            item["merchant_productivity_orders_per_day"],
            item["city_id"],
        ),
    )
    weighted_high_value_share = total_high_value_users / total_mau * Decimal("100")
    overall_user_frequency = total_orders / (Decimal("10") * total_mau)
    overall_merchant_productivity = total_orders * Decimal("1000") / total_merchants
    payload["metrics"] = {
        "city_count": len(rows),
        "monthly_active_users_10k": _rounded(total_mau, 2),
        "high_value_users_10k": _rounded(total_high_value_users, 2),
        "weighted_high_value_user_share_pct": _rounded(weighted_high_value_share, 2),
        "daily_orders_thousand": _rounded(total_orders, 2),
        "active_merchants": int(total_merchants),
        "user_order_frequency_per_day": _rounded(overall_user_frequency, 3),
        "merchant_productivity_orders_per_day": _rounded(overall_merchant_productivity, 2),
        "user_order_frequency_median": _rounded(user_frequency_median, 3),
        "merchant_productivity_median": _rounded(merchant_productivity_median, 2),
        "dual_efficiency_attention_count": len(attention_candidates),
    }
    payload["rankings"] = {
        "high_value_user_leaders": high_value_leaders,
        "user_engagement_leaders": user_engagement_leaders,
        "merchant_productivity_leaders": merchant_productivity_leaders,
        "dual_efficiency_attention": attention_candidates,
    }
    evidence_ids = [payload["evidence"][0]["evidence_id"]]
    attention_text = "、".join(item["city"] for item in attention_candidates) or "无"
    payload["claims"] = [
        {
            "claim": (
                f"{len(rows)} 城月活用户合计 {payload['metrics']['monthly_active_users_10k']:.2f} 万，"
                f"其中高价值用户约 {payload['metrics']['high_value_users_10k']:.2f} 万，"
                f"按月活加权占比 {payload['metrics']['weighted_high_value_user_share_pct']:.2f}%。"
            ),
            "status": "supported",
            "basis": "逐城月活用户数乘高价值用户占比后加总，再除以月活总量",
            "evidence_ids": evidence_ids,
        },
        {
            "claim": (
                f"整体用户日均下单频次约 {payload['metrics']['user_order_frequency_per_day']:.3f} 单/人，"
                f"活跃商家日均承载约 {payload['metrics']['merchant_productivity_orders_per_day']:.2f} 单。"
            ),
            "status": "supported",
            "basis": "日均订单量分别除以月活用户数和活跃商家数，按单位换算得到比率",
            "evidence_ids": evidence_ids,
        },
        {
            "claim": (
                f"按本批城市中位数筛选，{len(attention_candidates)} 个城市同时低于用户日频和商家承载中位数："
                f"{attention_text}；建议先核查，不据此判断原因。"
            ),
            "status": "supported_with_caveat",
            "basis": "以城市横截面中位数作为相对基准，两个效率指标均低于中位数",
            "evidence_ids": evidence_ids,
        },
    ]
    payload["lineage"] = {
        "high_value_users": f"{MONTHLY_ACTIVE_USERS} * {HIGH_VALUE_USER_SHARE} / 100",
        "weighted_high_value_share": "SUM(high_value_users) / SUM(monthly_active_users) * 100",
        "user_order_frequency": f"{DAILY_ORDERS_K} * 1,000 / ({MONTHLY_ACTIVE_USERS} * 10,000)",
        "merchant_productivity": f"{DAILY_ORDERS_K} * 1,000 / {ACTIVE_MERCHANTS}",
        "dual_efficiency_attention": "user_order_frequency < city_median AND merchant_productivity < city_median",
    }
    return payload


def _monthly_base_payload(
    snapshot: DataSnapshot, checks: list[dict[str, str]]
) -> dict[str, Any]:
    blockers = [item for item in checks if item["status"] == "fail"]
    evidence_id = f"business-snapshot-{snapshot.content_sha256[:16]}"
    return {
        "scenario": {
            "id": MONTHLY_PNL_SCENARIO_ID,
            "name": "城市月度损益诊断",
            "question": "同口径月份下，收入、经营利润和城市贡献发生了什么变化？",
            "audience": "经营负责人",
            "grain": "每行一个城市月",
        },
        "source": {
            "source_id": snapshot.source_id,
            "file_name": snapshot.file_name,
            "worksheet": snapshot.worksheet,
            "content_sha256": snapshot.content_sha256,
            "row_count": snapshot.row_count,
        },
        "assessment": "needs_revision" if blockers else "share_with_caveats",
        "quality": {
            "checks": checks,
            "blocker_count": len(blockers),
            "passed_count": sum(item["status"] == "pass" for item in checks),
        },
        "metrics": None,
        "rankings": {},
        "claims": [],
        "evidence": [
            {
                "evidence_id": evidence_id,
                "kind": "tabular_snapshot",
                "source_id": snapshot.source_id,
                "file_name": snapshot.file_name,
                "worksheet": snapshot.worksheet,
                "included_rows": snapshot.row_count,
                "content_sha256": snapshot.content_sha256,
                "locator": (
                    f"{snapshot.file_name}#{snapshot.worksheet}#sha256="
                    f"{snapshot.content_sha256}"
                ),
                "columns": list(snapshot.columns),
            }
        ],
        "caveats": [
            "这是项目随附的匿名化演示数据，不是公司生产经营报表。",
            "同比仅比较数据中最新年度与上一年度共同覆盖的月份，不外推全年。",
            "总部摊销按源表直接使用；若财务分摊规则变化，城市经营利润不可直接同比。",
            "异常城市只表示结果恶化或亏损，需要业务事件和更细成本明细才能归因。",
        ],
    }


def evaluate_city_monthly_pnl(snapshot: DataSnapshot) -> dict[str, Any]:
    """Reconcile a city-month P&L and compare like-for-like periods."""
    missing_columns = [
        column for column in MONTHLY_PNL_REQUIRED_COLUMNS if column not in snapshot.columns
    ]
    if missing_columns:
        raise BusinessAcceptanceError(
            "city monthly P&L columns missing: " + ", ".join(missing_columns),
            code="business_scenario_columns_missing",
        )

    rows = list(snapshot.rows)
    checks: list[dict[str, str]] = []
    keys = [(row[MONTH], row[CITY_ID]) for row in rows]
    duplicate_keys = sorted({f"{month}/{city_id}" for month, city_id in keys if keys.count((month, city_id)) > 1})
    checks.append(
        _check(
            "city_month_grain_unique",
            "城市月粒度唯一",
            "fail" if duplicate_keys else "pass",
            severity="critical",
            evidence=("重复城市月：" + "、".join(duplicate_keys[:8])) if duplicate_keys else f"{len(keys)} 个城市月组合均唯一",
            impact="重复城市月会放大收入和利润" if duplicate_keys else "",
        )
    )

    missing_cells = [
        f"{row.get(MONTH) or index + 1}/{row.get(CITY_ID) or '?'}:{column}"
        for index, row in enumerate(rows)
        for column in MONTHLY_PNL_REQUIRED_COLUMNS
        if not str(row.get(column, "")).strip()
    ]
    checks.append(
        _check(
            "monthly_required_values_complete",
            "月度损益字段完整",
            "fail" if missing_cells else "pass",
            severity="critical",
            evidence=("缺失单元格：" + "、".join(missing_cells[:8])) if missing_cells else "关键字段无缺失",
            impact="缺失值会破坏同期比较或利润桥接" if missing_cells else "",
        )
    )

    parsed_rows: list[dict[str, Decimal]] = []
    invalid_numeric: list[str] = []
    for row in rows:
        parsed: dict[str, Decimal] = {}
        for column in MONTHLY_PNL_NUMERIC_COLUMNS:
            try:
                parsed[column] = _decimal(row.get(column, ""))
            except BusinessAcceptanceError:
                invalid_numeric.append(f"{row.get(MONTH)}/{row.get(CITY_ID)}:{column}")
        parsed_rows.append(parsed)
    checks.append(
        _check(
            "monthly_numeric_values_valid",
            "月度金额可计算",
            "fail" if invalid_numeric else "pass",
            severity="critical",
            evidence=("非法数值：" + "、".join(invalid_numeric[:8])) if invalid_numeric else "所有金额与数量均可解析",
            impact="非法数值会导致损益无法复算" if invalid_numeric else "",
        )
    )

    invalid_months: list[str] = []
    month_parts: list[tuple[int, int] | None] = []
    for row in rows:
        match = re.fullmatch(r"(\d{4})-(\d{2})", row[MONTH])
        if not match or not 1 <= int(match.group(2)) <= 12:
            invalid_months.append(f"{row[CITY_ID]}:{row[MONTH]}")
            month_parts.append(None)
        else:
            month_parts.append((int(match.group(1)), int(match.group(2))))
    checks.append(
        _check(
            "month_format_valid",
            "月份格式有效",
            "fail" if invalid_months else "pass",
            severity="critical",
            evidence=("非法月份：" + "、".join(invalid_months[:8])) if invalid_months else "月份均为 YYYY-MM",
            impact="无法建立同口径同比期间" if invalid_months else "",
        )
    )

    coverage_by_city: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        coverage_by_city[row[CITY_ID]].add(row[MONTH])
    coverage_sets = {tuple(sorted(months)) for months in coverage_by_city.values()}
    balanced_coverage = len(coverage_sets) == 1 and bool(coverage_sets)

    comparable = False
    latest_year = 0
    prior_year = 0
    comparable_months: list[int] = []
    if not invalid_months:
        years = sorted({part[0] for part in month_parts if part})
        if len(years) >= 2:
            latest_year = years[-1]
            prior_year = latest_year - 1
            latest_months = {part[1] for part in month_parts if part and part[0] == latest_year}
            prior_months = {part[1] for part in month_parts if part and part[0] == prior_year}
            comparable_months = sorted(latest_months & prior_months)
            months_are_contiguous = comparable_months == list(
                range(min(comparable_months), max(comparable_months) + 1)
            ) if comparable_months else False
            comparable = (
                prior_year in years
                and latest_months == prior_months
                and bool(comparable_months)
                and months_are_contiguous
                and balanced_coverage
            )
    checks.append(
        _check(
            "like_for_like_period_coverage",
            "同比月份覆盖一致",
            "pass" if comparable else "fail",
            severity="critical",
            evidence=(
                f"{prior_year} 与 {latest_year} 共同覆盖 "
                + "、".join(f"{month:02d}月" for month in comparable_months)
            ) if comparable else "城市或同比年度的月份覆盖不一致",
            impact="不完整期间会造成虚假同比" if not comparable else "",
        )
    )

    invalid_ranges: list[str] = []
    bridge_mismatches: list[str] = []
    if not invalid_numeric:
        for row, parsed in zip(rows, parsed_rows):
            key = f"{row[MONTH]}/{row[CITY_ID]}"
            invalid_ranges.extend(
                f"{key}:{column}={parsed[column]}"
                for column in MONTHLY_PNL_NONNEGATIVE_COLUMNS
                if parsed[column] < 0
            )
            revenue = parsed[COMMISSION_REVENUE] + parsed[DELIVERY_REVENUE]
            if revenue <= 0:
                invalid_ranges.append(f"{key}:收入必须大于0")
            if parsed[GMV] < revenue:
                invalid_ranges.append(f"{key}:GMV小于收入")
            contribution = revenue - sum(
                (
                    parsed[MERCHANT_SETTLEMENT_COST],
                    parsed[MONTHLY_FULFILLMENT_COST],
                    parsed[MONTHLY_SUBSIDY_COST],
                    parsed[PAYMENT_COST],
                ),
                Decimal("0"),
            )
            operating_profit = contribution - parsed[HEADQUARTERS_COST]
            if abs(contribution - parsed[REPORTED_CONTRIBUTION]) > Decimal("0.01"):
                bridge_mismatches.append(f"{key}:贡献利润")
            if abs(operating_profit - parsed[REPORTED_OPERATING_PROFIT]) > Decimal("0.01"):
                bridge_mismatches.append(f"{key}:经营利润")
    checks.extend(
        [
            _check(
                "monthly_business_ranges_valid",
                "经营数量与金额范围有效",
                "fail" if invalid_ranges else "pass",
                severity="high",
                evidence=("异常值：" + "、".join(invalid_ranges[:8])) if invalid_ranges else "订单、GMV、收入和成本均非负，且 GMV 不低于收入",
                impact="异常范围会使收入与利润失真" if invalid_ranges else "",
            ),
            _check(
                "pnl_bridge_reconciles",
                "贡献利润与经营利润可勾稽",
                "fail" if bridge_mismatches else "pass",
                severity="critical",
                evidence=("未勾稽：" + "、".join(bridge_mismatches[:8])) if bridge_mismatches else f"{len(rows)} 行损益桥接全部一致",
                impact="利润无法从收入和成本逐项复算" if bridge_mismatches else "",
            ),
            _check(
                "monthly_duplicate_rows_absent",
                "月度记录无整行重复",
                "fail" if snapshot.duplicate_rows else "pass",
                severity="high",
                evidence=f"发现 {snapshot.duplicate_rows} 条重复记录" if snapshot.duplicate_rows else "未发现整行重复",
                impact="重复记录会放大损益" if snapshot.duplicate_rows else "",
            ),
        ]
    )

    payload = _monthly_base_payload(snapshot, checks)
    if payload["quality"]["blocker_count"]:
        return payload

    totals: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    city_totals: dict[str, dict[int, dict[str, Decimal]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(Decimal))
    )
    city_names: dict[str, str] = {}
    for row, parsed, part in zip(rows, parsed_rows, month_parts):
        if part is None or part[0] not in {prior_year, latest_year} or part[1] not in comparable_months:
            continue
        year = part[0]
        city_id = row[CITY_ID]
        city_names[city_id] = row[CITY]
        revenue = parsed[COMMISSION_REVENUE] + parsed[DELIVERY_REVENUE]
        values = {
            "orders": parsed[MONTHLY_ORDERS],
            "gmv": parsed[GMV],
            "revenue": revenue,
            "contribution": parsed[REPORTED_CONTRIBUTION],
            "operating_profit": parsed[REPORTED_OPERATING_PROFIT],
        }
        for key, value in values.items():
            totals[year][key] += value
            city_totals[city_id][year][key] += value

    def pct_change(current: Decimal, prior: Decimal) -> float | None:
        if prior == 0:
            return None
        return _rounded((current / prior - Decimal("1")) * Decimal("100"), 2)

    current = totals[latest_year]
    prior = totals[prior_year]
    current_margin = current["operating_profit"] / current["revenue"] * Decimal("100")
    prior_margin = prior["operating_profit"] / prior["revenue"] * Decimal("100")
    city_results: list[dict[str, Any]] = []
    for city_id, yearly in city_totals.items():
        current_city = yearly[latest_year]
        prior_city = yearly[prior_year]
        city_results.append(
            {
                "city_id": city_id,
                "city": city_names[city_id],
                "current_revenue_10k": _rounded(current_city["revenue"], 3),
                "current_operating_profit_10k": _rounded(
                    current_city["operating_profit"], 3
                ),
                "operating_profit_delta_10k": _rounded(
                    current_city["operating_profit"] - prior_city["operating_profit"], 3
                ),
                "revenue_yoy_pct": pct_change(
                    current_city["revenue"], prior_city["revenue"]
                ),
                "current_operating_margin_pct": _rounded(
                    current_city["operating_profit"] / current_city["revenue"] * Decimal("100"),
                    2,
                ),
            }
        )

    profit_leaders = sorted(
        city_results,
        key=lambda item: (-item["current_operating_profit_10k"], item["city_id"]),
    )
    improvement_leaders = sorted(
        city_results,
        key=lambda item: (-item["operating_profit_delta_10k"], item["city_id"]),
    )
    attention_candidates = sorted(
        [item for item in city_results if item["current_operating_profit_10k"] < 0],
        key=lambda item: (item["current_operating_profit_10k"], item["city_id"]),
    )
    first_month = min(comparable_months)
    last_month = max(comparable_months)
    period_label = f"{latest_year}-{first_month:02d} 至 {latest_year}-{last_month:02d}"
    comparison_label = f"{prior_year}-{first_month:02d} 至 {prior_year}-{last_month:02d}"
    payload["metrics"] = {
        "current_period": period_label,
        "comparison_period": comparison_label,
        "city_count": len(city_results),
        "current_orders_10k": _rounded(current["orders"], 2),
        "current_gmv_10k": _rounded(current["gmv"], 2),
        "current_revenue_10k": _rounded(current["revenue"], 3),
        "current_contribution_profit_10k": _rounded(current["contribution"], 3),
        "current_operating_profit_10k": _rounded(current["operating_profit"], 3),
        "current_operating_margin_pct": _rounded(current_margin, 2),
        "prior_revenue_10k": _rounded(prior["revenue"], 3),
        "prior_operating_profit_10k": _rounded(prior["operating_profit"], 3),
        "prior_operating_margin_pct": _rounded(prior_margin, 2),
        "orders_yoy_pct": pct_change(current["orders"], prior["orders"]),
        "gmv_yoy_pct": pct_change(current["gmv"], prior["gmv"]),
        "revenue_yoy_pct": pct_change(current["revenue"], prior["revenue"]),
        "operating_profit_delta_10k": _rounded(
            current["operating_profit"] - prior["operating_profit"], 3
        ),
        "operating_margin_delta_pct": _rounded(current_margin - prior_margin, 2),
    }
    payload["rankings"] = {
        "profit_leaders": profit_leaders,
        "improvement_leaders": improvement_leaders,
        "attention_candidates": attention_candidates,
    }
    evidence_ids = [payload["evidence"][0]["evidence_id"]]
    attention_text = "、".join(item["city"] for item in attention_candidates) or "无"
    payload["claims"] = [
        {
            "claim": f"{period_label} 收入同比增长 {payload['metrics']['revenue_yoy_pct']:.2f}%。",
            "status": "supported",
            "basis": "最新年度与上一年度相同月份的佣金收入和配送收入加总后比较",
            "evidence_ids": evidence_ids,
        },
        {
            "claim": f"经营利润同比增加 {payload['metrics']['operating_profit_delta_10k']:.3f} 万元，经营利润率为 {payload['metrics']['current_operating_margin_pct']:.2f}%。",
            "status": "supported",
            "basis": "逐行复算贡献利润并扣除总部摊销，再按同期汇总",
            "evidence_ids": evidence_ids,
        },
        {
            "claim": f"当前亏损且需优先核查的城市为：{attention_text}。",
            "status": "supported_with_caveat",
            "basis": "仅按当前同期经营利润识别，不推断亏损原因",
            "evidence_ids": evidence_ids,
        },
    ]
    payload["lineage"] = {
        "revenue": f"{COMMISSION_REVENUE} + {DELIVERY_REVENUE}",
        "contribution_profit": (
            f"revenue - {MERCHANT_SETTLEMENT_COST} - {MONTHLY_FULFILLMENT_COST} - "
            f"{MONTHLY_SUBSIDY_COST} - {PAYMENT_COST}"
        ),
        "operating_profit": f"contribution_profit - {HEADQUARTERS_COST}",
        "yoy": "SUM(latest comparable months) / SUM(prior-year same months) - 1",
    }
    return payload
