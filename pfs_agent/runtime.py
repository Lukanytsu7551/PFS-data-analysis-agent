"""Runtime adapter between the existing Agent tools and the PFS policy gate.

The upstream-compatible tool registry remains the source of execution metadata;
this module translates that metadata into the smaller PFS contract without
duplicating every prompt-facing JSON schema.  Only observe/compute tools are
enforced in the first migration slice.  Side effects stay on the existing
approval paths until their individual contracts are migrated.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .contracts import ToolCall, ToolRisk
from .policy import PolicyContext, PolicyDecision, PolicyGate, ToolRegistry


_ENFORCED_RISKS = frozenset({ToolRisk.OBSERVE, ToolRisk.COMPUTE})


def _schema_by_name(tool_schemas: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for schema in tool_schemas:
        function = schema.get("function") or {}
        name = str(function.get("name") or "").strip()
        if name:
            result[name] = schema
    return result


def _risk_for_category(category: str) -> ToolRisk:
    if category == "read":
        return ToolRisk.OBSERVE
    if category == "analysis":
        return ToolRisk.COMPUTE
    if category == "output":
        return ToolRisk.ARTIFACT
    return ToolRisk.SIDE_EFFECT


def build_builtin_registry(
    runtime_specs: Iterable[Any],
    tool_schemas: Iterable[Mapping[str, Any]],
) -> ToolRegistry:
    """Translate existing runtime specs and prompt schemas into PFS contracts."""
    schemas = _schema_by_name(tool_schemas)
    contracts = []
    for runtime_spec in runtime_specs:
        name = str(getattr(runtime_spec, "name", "") or "").strip()
        schema = schemas.get(name)
        if not name or schema is None:
            continue
        function = schema.get("function") or {}
        parameters = function.get("parameters") or {"type": "object", "properties": {}}
        risk = _risk_for_category(str(getattr(runtime_spec, "category", "read")))
        required_scopes = []
        if getattr(runtime_spec, "requires_data_source", False):
            required_scopes.append("data:read")
        if getattr(runtime_spec, "requires_workspace", False):
            required_scopes.append("workspace:read")
        contracts.append(
            _make_spec(
                name,
                str(getattr(runtime_spec, "discovery_summary", "") or function.get("description") or name),
                risk,
                parameters,
                tuple(required_scopes),
                max_calls=50,
                timeout_seconds=30,
            )
        )
    return ToolRegistry(tuple(contracts))


def _make_spec(
    name: str,
    purpose: str,
    risk: ToolRisk,
    input_schema: Mapping[str, Any],
    required_scopes: tuple[str, ...],
    *,
    max_calls: int,
    timeout_seconds: int,
):
    # Import locally to keep the adapter's public imports small and explicit.
    from .contracts import ApprovalMode, ToolSpec

    is_side_effect = risk is ToolRisk.SIDE_EFFECT
    return ToolSpec(
        tool_id=name,
        purpose=purpose,
        risk=risk,
        input_schema=input_schema,
        output_type="tool_result",
        required_scopes=required_scopes,
        approval=ApprovalMode.USER if is_side_effect else ApprovalMode.NONE,
        idempotency_required=is_side_effect,
        max_calls=max_calls,
        timeout_seconds=timeout_seconds,
    )


def evaluate_builtin_call(
    registry: ToolRegistry,
    *,
    run_id: str,
    tool_id: str,
    arguments: Mapping[str, Any],
    workspace_id: str,
    visible_tools: Iterable[str],
    has_data_source: bool,
    has_workspace: bool,
    remaining_calls: int,
    remaining_seconds: int,
    tool_call_counts: Mapping[str, int],
) -> PolicyDecision | None:
    """Evaluate one migrated built-in call; return None for deferred risks."""
    spec = registry.get(tool_id)
    if spec is None or spec.risk not in _ENFORCED_RISKS:
        return None
    granted_scopes = set()
    if has_data_source:
        granted_scopes.add("data:read")
    if has_workspace:
        granted_scopes.add("workspace:read")
    call = ToolCall(
        run_id=run_id or "pfs-local-run",
        tool_id=tool_id,
        arguments=arguments,
        workspace_id=workspace_id or "",
    )
    context = PolicyContext(
        allowed_tools=frozenset(str(name) for name in visible_tools),
        granted_scopes=frozenset(granted_scopes),
        workspace_id=workspace_id or "",
        remaining_calls=max(0, int(remaining_calls)),
        remaining_seconds=max(0, int(remaining_seconds)),
        remaining_cost_cents=0,
        tool_call_counts=tool_call_counts,
    )
    return PolicyGate(registry).evaluate(call, context)
