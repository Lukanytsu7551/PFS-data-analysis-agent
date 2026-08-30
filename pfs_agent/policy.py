"""Deterministic allow/deny decisions for PFS tool calls.

This module deliberately does not execute tools.  It only validates a proposed
call against a registered contract and a run-scoped policy context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import ToolCall, ToolRisk, ToolSpec


@dataclass(frozen=True)
class PolicyContext:
    """The non-model-controlled limits for one Agent run."""

    allowed_tools: frozenset[str]
    granted_scopes: frozenset[str] = frozenset()
    workspace_id: str = ""
    remaining_calls: int = 0
    remaining_seconds: int = 0
    remaining_cost_cents: int = 0
    approval_granted: bool = False
    tool_call_counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecision:
    """A stable result that can be shown to the UI and written to an audit log."""

    allowed: bool
    code: str
    reason: str
    requires_approval: bool = False


class ToolRegistry:
    """A deterministic map from public tool ids to their contracts."""

    def __init__(self, specs: tuple[ToolSpec, ...] = ()) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.tool_id in self._specs:
            raise ValueError(f"duplicate tool id: {spec.tool_id}")
        self._specs[spec.tool_id] = spec

    def get(self, tool_id: str) -> ToolSpec | None:
        return self._specs.get(tool_id)

    def visible(self, allowed_tools: frozenset[str]) -> tuple[ToolSpec, ...]:
        """Return only tools that both exist and are allowed for the run."""

        return tuple(self._specs[tool_id] for tool_id in self._specs if tool_id in allowed_tools)


class PolicyGate:
    """Evaluate a proposed tool call without performing any side effect."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def evaluate(self, call: ToolCall, context: PolicyContext) -> PolicyDecision:
        spec = self.registry.get(call.tool_id)
        if spec is None:
            return PolicyDecision(False, "unknown_tool", "tool is not registered")
        if call.tool_id not in context.allowed_tools:
            return PolicyDecision(False, "tool_not_allowed", "tool is not allowed in this run")
        if context.workspace_id and call.workspace_id != context.workspace_id:
            return PolicyDecision(False, "workspace_forbidden", "call workspace does not match run workspace")
        missing_scopes = set(spec.required_scopes) - set(context.granted_scopes)
        if missing_scopes:
            missing = ", ".join(sorted(missing_scopes))
            return PolicyDecision(False, "scope_forbidden", f"missing required scope: {missing}")
        argument_error = validate_arguments(spec.input_schema, call.arguments)
        if argument_error:
            return PolicyDecision(False, "invalid_arguments", argument_error)
        if context.remaining_calls < 1:
            return PolicyDecision(False, "call_budget_exceeded", "tool call budget is exhausted")
        if context.tool_call_counts.get(call.tool_id, 0) >= spec.max_calls:
            return PolicyDecision(False, "tool_call_limit_exceeded", "per-tool call limit is exhausted")
        if context.remaining_seconds < spec.timeout_seconds:
            return PolicyDecision(False, "time_budget_exceeded", "remaining run time is below tool timeout")
        if call.estimated_cost_cents > context.remaining_cost_cents:
            return PolicyDecision(
                False, "cost_budget_exceeded", "estimated tool cost exceeds remaining budget"
            )
        if spec.approval.value != "none" and not context.approval_granted:
            return PolicyDecision(
                False,
                "approval_required",
                "human or policy approval is required before execution",
                requires_approval=True,
            )
        if spec.idempotency_required and not call.idempotency_key.strip():
            return PolicyDecision(False, "idempotency_required", "side-effect call needs an idempotency key")
        return PolicyDecision(True, "allowed", "tool call passed the policy gate")


def validate_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> str:
    """Validate the small JSON-schema subset needed by the first PFS tools."""

    if schema.get("type") not in {None, "object"}:
        return "tool schema must describe an object"
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return "tool schema properties must be an object"
    required = schema.get("required", ())
    if not isinstance(required, (list, tuple)):
        return "tool schema required must be an array"
    missing = [name for name in required if name not in arguments]
    if missing:
        return "missing required argument(s): " + ", ".join(sorted(map(str, missing)))
    if schema.get("additionalProperties") is False:
        unknown = set(arguments) - set(properties)
        if unknown:
            return "unknown argument(s): " + ", ".join(sorted(map(str, unknown)))
    for name, value in arguments.items():
        if name not in properties:
            continue
        expected = properties[name].get("type") if isinstance(properties[name], Mapping) else None
        if expected and not _matches_json_type(value, expected):
            return f"argument '{name}' must be {expected}"
    return ""


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, Mapping)
    return False
