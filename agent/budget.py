"""Validated process-level budgets for PFS Agent runs.

The model may suggest work, but it cannot widen these limits.  The HTTP chat
entry point loads this contract for every Agent turn and passes the resulting
values into :class:`BusinessAgent`, where the run-scoped guards enforce them.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


class BudgetConfigurationError(ValueError):
    """Raised when a process-level Agent budget is not safe to use."""


@dataclass(frozen=True)
class RuntimeBudget:
    """Validated hard limits applied to a normal Agent turn."""

    max_iterations: int = 120
    max_tool_calls: int = 480
    max_total_tokens: int | None = None
    max_run_seconds: int = 1800
    max_job_seconds: int = 1800

    def to_dict(self) -> dict[str, int | None]:
        return {
            "max_iterations": self.max_iterations,
            "max_tool_calls": self.max_tool_calls,
            "max_total_tokens": self.max_total_tokens,
            "max_run_seconds": self.max_run_seconds,
            "max_job_seconds": self.max_job_seconds,
        }


def _read_int(
    environ: Mapping[str, str],
    name: str,
    default: int | None,
    minimum: int,
    maximum: int,
    *,
    optional: bool = False,
) -> int | None:
    raw = environ.get(name)
    if raw is None or not str(raw).strip():
        return None if optional else default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise BudgetConfigurationError(f"{name} 必须是整数") from exc
    if value < minimum or value > maximum:
        raise BudgetConfigurationError(
            f"{name} 必须在 {minimum} 到 {maximum} 之间"
        )
    return value


def load_runtime_budget(
    environ: Mapping[str, str] | None = None,
) -> RuntimeBudget:
    """Load and validate the normal Agent hard limits from the environment.

    Blank optional token configuration preserves the existing behavior of
    relying on provider usage without imposing a guessed token ceiling.
    """

    values = os.environ if environ is None else environ
    max_iterations = _read_int(
        values, "PFS_MAX_ITERATIONS", 120, 1, 1_000,
    )
    configured_tool_calls = _read_int(
        values, "PFS_MAX_TOOL_CALLS", None, 1, 4_000, optional=True,
    )
    max_tool_calls = configured_tool_calls or int(max_iterations or 120) * 4
    max_total_tokens = _read_int(
        values, "PFS_MAX_TOTAL_TOKENS", None, 1, 2_000_000, optional=True,
    )
    max_run_seconds = _read_int(
        values, "PFS_MAX_RUN_SECONDS", 1_800, 10, 7_200,
    )
    max_job_seconds = _read_int(
        values, "PFS_MAX_JOB_SECONDS", 1_800, 10, 7_200,
    )
    return RuntimeBudget(
        max_iterations=int(max_iterations or 120),
        max_tool_calls=int(max_tool_calls),
        max_total_tokens=max_total_tokens,
        max_run_seconds=int(max_run_seconds or 1_800),
        max_job_seconds=int(max_job_seconds or 1_800),
    )
