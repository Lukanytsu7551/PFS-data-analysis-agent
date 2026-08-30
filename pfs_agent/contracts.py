"""Small, dependency-free contracts used by the PFS policy boundary."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
import re


class ToolRisk(str, Enum):
    """Operational risk level of a tool."""

    OBSERVE = "observe"
    COMPUTE = "compute"
    ARTIFACT = "artifact"
    EXTERNAL = "external"
    SIDE_EFFECT = "side_effect"


class ApprovalMode(str, Enum):
    """How a tool may cross the action boundary."""

    NONE = "none"
    USER = "user"
    POLICY = "policy"


_TOOL_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class ContractError(ValueError):
    """Raised when a PFS contract is internally inconsistent."""


@dataclass(frozen=True)
class ToolSpec:
    """The part of a tool contract that is safe to expose to a model."""

    tool_id: str
    purpose: str
    risk: ToolRisk
    input_schema: Mapping[str, Any]
    output_type: str
    required_scopes: tuple[str, ...] = ()
    approval: ApprovalMode = ApprovalMode.NONE
    idempotency_required: bool = False
    max_calls: int = 1
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if not _TOOL_ID.fullmatch(self.tool_id):
            raise ContractError("tool_id must use 2-64 lowercase letters, numbers, or underscores")
        if not self.purpose.strip():
            raise ContractError("purpose must not be empty")
        if not isinstance(self.risk, ToolRisk):
            raise ContractError("risk must be a ToolRisk")
        if not isinstance(self.input_schema, Mapping):
            raise ContractError("input_schema must be a mapping")
        if self.input_schema.get("type") not in {None, "object"}:
            raise ContractError("tool input_schema must describe an object")
        if not self.output_type.strip():
            raise ContractError("output_type must not be empty")
        if any(not scope.strip() for scope in self.required_scopes):
            raise ContractError("required_scopes must not contain empty values")
        if self.max_calls < 1:
            raise ContractError("max_calls must be at least 1")
        if self.timeout_seconds < 1:
            raise ContractError("timeout_seconds must be at least 1")
        if self.risk is ToolRisk.SIDE_EFFECT:
            if self.approval is ApprovalMode.NONE:
                raise ContractError("side-effect tools require approval")
            if not self.idempotency_required:
                raise ContractError("side-effect tools require idempotency")


@dataclass(frozen=True)
class ToolCall:
    """A model-proposed action before the policy gate evaluates it."""

    run_id: str
    tool_id: str
    arguments: Mapping[str, Any]
    workspace_id: str = ""
    idempotency_key: str = ""
    estimated_cost_cents: int = 0

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ContractError("run_id must not be empty")
        if not _TOOL_ID.fullmatch(self.tool_id):
            raise ContractError("tool_id has an invalid format")
        if not isinstance(self.arguments, Mapping):
            raise ContractError("arguments must be a mapping")
        if self.estimated_cost_cents < 0:
            raise ContractError("estimated_cost_cents cannot be negative")
