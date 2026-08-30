"""Auditable model cost calculation for PFS Agent runs."""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def _price(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("模型价格必须是数字") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("模型价格必须是非负的有限数字")
    return parsed


def calculate_model_cost_usd(
    input_tokens: int,
    output_tokens: int,
    *,
    input_price_per_million: object = None,
    output_price_per_million: object = None,
) -> float | None:
    """Calculate USD cost from actual usage and a complete price pair.

    Missing prices return ``None`` deliberately: PFS must never invent a cost.
    A partial pair is a configuration error because it would under-report cost.
    """
    input_rate = _price(input_price_per_million)
    output_rate = _price(output_price_per_million)
    if input_rate is None and output_rate is None:
        return None
    if input_rate is None or output_rate is None:
        raise ValueError("模型价格必须同时填写输入与输出单价")
    total = (
        Decimal(max(0, int(input_tokens or 0))) * input_rate
        + Decimal(max(0, int(output_tokens or 0))) * output_rate
    ) / Decimal(1_000_000)
    # Eight decimal places are enough for display and deterministic tests while
    # retaining sub-cent values for small local runs.
    return float(total.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP))


def validate_cost_limit(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError("费用预算必须是数字") from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("费用预算必须是大于 0 的有限数字")
    return parsed
