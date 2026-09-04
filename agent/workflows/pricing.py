"""Optional, operator-supplied Workflow model pricing."""

from __future__ import annotations

import json
import os
from infrastructure.compat import env
from typing import Any, Mapping


def workflow_model_prices() -> dict[str, dict[str, float]]:
    """Read configured USD-per-million-token rates.

    Model Settings is the primary source. ``PFS_WORKFLOW_MODEL_PRICES`` is the
    operator-level override; an explicit model setting takes precedence over
    it.
    """
    try:
        raw = json.loads(env("PFS_WORKFLOW_MODEL_PRICES", "{}"))
    except ValueError:
        return {}
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, dict[str, float]] = {}
    for key, value in raw.items():
        if not isinstance(value, Mapping):
            continue
        try:
            input_rate, output_rate = float(value["input"]), float(value["output"])
        except (KeyError, TypeError, ValueError):
            continue
        if input_rate >= 0 and output_rate >= 0:
            result[str(key)] = {"input": input_rate, "output": output_rate}
    try:
        from LLM.llm_config_manager import get_config_manager

        configs = get_config_manager().configs
        for provider, config in configs.items():
            input_rate = getattr(config, "input_price_per_million", None)
            output_rate = getattr(config, "output_price_per_million", None)
            model = str(getattr(config, "model", "") or "")
            if input_rate is None or output_rate is None or not model:
                continue
            try:
                rates = {"input": float(input_rate), "output": float(output_rate)}
            except (TypeError, ValueError):
                continue
            if rates["input"] < 0 or rates["output"] < 0:
                continue
            result[f"{provider}/{model}"] = rates
            result[model] = rates
    except Exception:
        # Cost observability must not make a workflow unavailable when the
        # optional model-settings file is absent or malformed.
        pass
    return result


def lookup_model_price(provider: str, model: str) -> dict[str, float] | None:
    """Return a complete configured price pair for one effective model.

    Workflow cost budgets must resolve both rates before dispatch.  This
    helper deliberately returns ``None`` for a missing or partial pair rather
    than treating an unknown provider price as zero.
    """
    provider_key = str(provider or "").strip()
    model_key = str(model or "").strip()
    if not model_key:
        return None
    rates = workflow_model_prices().get(f"{provider_key}/{model_key}") or workflow_model_prices().get(
        model_key
    )
    if not isinstance(rates, Mapping):
        return None
    try:
        input_rate = float(rates["input"])
        output_rate = float(rates["output"])
    except (KeyError, TypeError, ValueError):
        return None
    if input_rate < 0 or output_rate < 0:
        return None
    return {"input": input_rate, "output": output_rate}


def estimate_model_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float | None:
    rates = lookup_model_price(provider, model)
    if rates is None:
        return None
    return round(
        (max(0, input_tokens) * rates["input"] + max(0, output_tokens) * rates["output"]) / 1_000_000, 8
    )
