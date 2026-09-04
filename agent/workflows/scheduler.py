"""Deterministic WF2 scheduler over published auto-edge DAGs."""

from __future__ import annotations

import threading
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from data.jobs_store import (
    RESTART_RECOVERY_ERROR,
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
)
from data.workflow_run_store import WorkflowRunStore, WorkflowRunStoreError
from data.workflow_store import WorkflowStore

from .pricing import estimate_model_cost

from .models import (
    NODE_RUN_TERMINAL_STATUSES,
    RUN_TERMINAL_STATUSES,
    EdgeType,
    NodeRunStatus,
    RunStatus,
    WorkflowContractError,
    WorkflowErrorCode,
    WorkflowRunMode,
)


NodeExecutor = Callable[[dict[str, Any], dict[str, Any], Any], Any]
NodePreflight = Callable[[Mapping[str, Any]], None]
GraphPreflight = Callable[[Mapping[str, Any]], None]
RunTerminalHook = Callable[[str, str], None]
MODEL_NODE_TYPES = frozenset({"agent", "verifier"})
NODE_RUN_BUDGET_ERROR = "workflow node-run budget exceeded"
RESTART_REPLAY_BLOCKED_ERROR = (
    "workflow side-effect replay blocked after restart; manual review required"
)


class WorkflowConcurrencyLimiter:
    """Process-local quotas; persistent state remains in NodeRun/Job."""

    def __init__(
        self,
        *,
        global_limit: int = 6,
        workspace_limit: int = 3,
        run_limit: int = 2,
        profile_limit: int = 1,
    ):
        self.limits = {
            "global": global_limit,
            "workspace": workspace_limit,
            "run": run_limit,
            "profile": profile_limit,
        }
        self._active: set[tuple[str, str, str]] = set()
        self._lock = threading.RLock()

    def acquire(self, workspace_id: str, run_id: str, profile_id: str) -> bool:
        key = (workspace_id, run_id, profile_id)
        with self._lock:
            if key in self._active:
                return False
            workspaces = Counter(item[0] for item in self._active)
            runs = Counter(item[1] for item in self._active)
            profiles = Counter(item[2] for item in self._active)
            if len(self._active) >= self.limits["global"]:
                return False
            if workspaces[workspace_id] >= self.limits["workspace"]:
                return False
            if runs[run_id] >= self.limits["run"]:
                return False
            if profiles[profile_id] >= self.limits["profile"]:
                return False
            self._active.add(key)
            return True

    def release(self, workspace_id: str, run_id: str, profile_id: str) -> None:
        with self._lock:
            self._active.discard((workspace_id, run_id, profile_id))


GLOBAL_WORKFLOW_LIMITER = WorkflowConcurrencyLimiter()


class WorkflowScheduler:
    """Idempotently derive and dispatch work from durable Run facts."""

    def __init__(
        self,
        *,
        workflow_store: WorkflowStore,
        run_store: WorkflowRunStore,
        job_runner,
        executor: NodeExecutor,
        preflight: NodePreflight | None = None,
        graph_preflight: GraphPreflight | None = None,
        on_run_terminal: RunTerminalHook | None = None,
        limiter: WorkflowConcurrencyLimiter | None = None,
    ):
        self.workflow_store = workflow_store
        self.run_store = run_store
        self.job_runner = job_runner
        self.executor = executor
        self.preflight = preflight
        self.graph_preflight = graph_preflight
        self.on_run_terminal = on_run_terminal
        self.limiter = limiter or GLOBAL_WORKFLOW_LIMITER
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _notify_run_terminal(self, run_id: str) -> None:
        hook = getattr(self, "on_run_terminal", None)
        if hook is None:
            return
        run = self.run_store.get_run(run_id)
        if run and RunStatus(run["status"]) in RUN_TERMINAL_STATUSES:
            hook(run_id, str(run["status"]))

    def _run_lock(self, run_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(run_id, threading.RLock())

    @staticmethod
    def _concurrency_key(node_run: Mapping[str, Any]) -> str:
        return str(node_run.get("agent_profile_id") or node_run["node_id"])

    def start(
        self,
        *,
        workflow_version_id: str,
        session_id: str,
        inputs: Mapping[str, Any],
        started_by: str = "",
    ) -> dict[str, Any]:
        version = self.workflow_store.get_version(workflow_version_id)
        if version is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow version not found: {workflow_version_id}",
            )
        if self.graph_preflight is not None:
            self.graph_preflight(version["graph"])
        run = self.run_store.create_run(
            workflow_version_id=workflow_version_id,
            session_id=session_id,
            graph=version["graph"],
            inputs=inputs,
            started_by=started_by,
        )
        self.run_store.transition_run(run["id"], RunStatus.RUNNING)
        return self.advance(run["id"])

    def recover_interrupted_runs(self, *, session_id: str = "") -> list[dict[str, Any]]:
        """Reconcile non-terminal Runs after a scheduler/process restart.

        Jobs persist their state, but their executable callbacks do not. A
        reopened ``JobsStore`` therefore marks an in-flight Job as failed. A
        new scheduler can safely advance read-only work according to the
        graph's existing retry policy. Nodes that declare an irreversible
        side effect are handled fail-closed by ``_reconcile_jobs`` so a
        process restart cannot silently duplicate a write, export, or network
        action. Deliberately paused and approval-waiting Runs are never
        resumed by startup recovery.
        """
        recovered: list[dict[str, Any]] = []
        for run in self.run_store.list_runs(session_id=session_id):
            status = RunStatus(str(run["status"]))
            if status in RUN_TERMINAL_STATUSES or status in {
                RunStatus.PAUSED,
                RunStatus.WAITING_APPROVAL,
            }:
                continue
            run_id = str(run["id"])
            active_nodes = [
                item
                for item in self.run_store.list_node_runs(run_id)
                if item["status"]
                in {
                    NodeRunStatus.QUEUED.value,
                    NodeRunStatus.RUNNING.value,
                }
            ]
            self.run_store.record_event(
                run_id,
                "workflow_run_recovery_started",
                {
                    "run_id": run_id,
                    "previous_status": status.value,
                    "active_node_run_ids": [str(item["id"]) for item in active_nodes],
                    "active_job_ids": [
                        str(item["job_id"]) for item in active_nodes if item.get("job_id")
                    ],
                    "session_id": str(run.get("session_id") or ""),
                },
            )
            try:
                detail = self.advance(run_id)
                final_status = str((detail.get("run") or {}).get("status") or "")
                self.run_store.record_event(
                    run_id,
                    "workflow_run_recovery_completed",
                    {
                        "run_id": run_id,
                        "previous_status": status.value,
                        "status": final_status,
                    },
                )
                recovered.append(
                    {
                        "run_id": run_id,
                        "previous_status": status.value,
                        "status": final_status,
                        "ok": True,
                    }
                )
            except Exception as exc:  # recovery must remain visible, not take down startup
                error = f"{type(exc).__name__}: {exc}"
                self.run_store.record_event(
                    run_id,
                    "workflow_run_recovery_failed",
                    {"run_id": run_id, "previous_status": status.value, "error": error},
                )
                recovered.append(
                    {
                        "run_id": run_id,
                        "previous_status": status.value,
                        "status": str(
                            (self.run_store.get_run(run_id) or {}).get("status") or ""
                        ),
                        "ok": False,
                        "error": error,
                    }
                )
        return recovered

    def detail(self, run_id: str) -> dict[str, Any]:
        run = self.run_store.get_run(run_id)
        if run is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow run not found: {run_id}",
            )
        version = self.workflow_store.get_version(run["workflow_version_id"])
        nodes = self.run_store.list_node_runs(run_id)
        for node in nodes:
            model = str(node.get("model_name") or "")
            node["estimated_cost"] = (
                estimate_model_cost(
                    str(node.get("provider_name") or ""),
                    model,
                    int(node.get("input_tokens") or 0),
                    int(node.get("output_tokens") or 0),
                )
                if model
                else None
            )
        latest_nodes = self._latest_by_node(nodes)
        declared_outputs = set(((version or {}).get("output_schema") or {}).get("properties", {}))
        outputs: dict[str, Any] = {}
        lineage: list[dict[str, Any]] = []
        for node in latest_nodes.values():
            node_output = node.get("output")
            if not isinstance(node_output, Mapping):
                continue
            for key, value in node_output.items():
                if not declared_outputs or key in declared_outputs:
                    outputs[str(key)] = value
                    manifest = self.run_store.get_manifest(str(node.get("output_manifest_id") or ""))
                    artifact = next(
                        (
                            item
                            for item in (manifest or {}).get("items", [])
                            if item.get("logical_name") == key
                        ),
                        {},
                    )
                    lineage.append(
                        {
                            "output": str(key),
                            "producer_node_id": node.get("node_id", ""),
                            "producer_node_run_id": node.get("id", ""),
                            "artifact_id": artifact.get("artifact_id", ""),
                            "uri": artifact.get("uri", ""),
                            "evidence": artifact.get("evidence", []),
                            "quality": artifact.get("quality", {}),
                        }
                    )
        return {
            "run": run,
            "graph": version["graph"] if version else {},
            "output_schema": version["output_schema"] if version else {},
            "outputs": outputs,
            "lineage": lineage,
            "nodes": nodes,
            "manifests": self.run_store.list_manifests(run_id),
            "consumptions": self.run_store.list_consumptions(run_id),
            "approvals": self.run_store.list_approvals(run_id),
            "templates": [item for item in self.run_store.list_run_templates() if item["run_id"] == run_id],
            "knowledge_candidates": self.run_store.list_knowledge_candidates(run_id=run_id),
            "events": self.run_store.list_events(run_id),
        }

    def advance(self, run_id: str) -> dict[str, Any]:
        with self._run_lock(run_id):
            run = self.run_store.get_run(run_id)
            if run is None:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND,
                    f"workflow run not found: {run_id}",
                )
            status = RunStatus(run["status"])
            if status in RUN_TERMINAL_STATUSES:
                return self.detail(run_id)
            version = self.workflow_store.get_version(run["workflow_version_id"])
            if version is None:
                self.run_store.transition_run(
                    run_id,
                    RunStatus.FAILED,
                    failure_code=WorkflowErrorCode.RESOURCE_NOT_FOUND.value,
                    failure_message="published workflow version is missing",
                )
                self._notify_run_terminal(run_id)
                return self.detail(run_id)

            if self._expire_timed_out_run(run, version["graph"]):
                return self.detail(run_id)
            self._reconcile_jobs(run_id, version["graph"])
            if self._expire_token_budget(run_id, version["graph"]):
                return self.detail(run_id)
            if self._expire_cost_budget(run_id, version["graph"]):
                return self.detail(run_id)
            if RunStatus((self.run_store.get_run(run_id) or run)["status"]) is RunStatus.CANCELING:
                self._advance_canceling(run_id)
                return self.detail(run_id)

            for _node in version["graph"].get("nodes", []):
                self._mark_ready_nodes(run_id, version["graph"])
            self._mark_ready_nodes(run_id, version["graph"])
            self._dispatch_ready_nodes(run_id, version["graph"])
            # A policy preflight can fail without creating a Job, so there is
            # no terminal Job callback to derive downstream skips. Re-evaluate
            # readiness before settling the Run in the same scheduler tick.
            for _node in version["graph"].get("nodes", []):
                self._mark_ready_nodes(run_id, version["graph"])
            self._settle_run(run_id)
            return self.detail(run_id)

    @staticmethod
    def _latest_by_node(node_runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for item in node_runs:
            current = latest.get(item["node_id"])
            item_key = (int(item["iteration"]), int(item["attempt"]), item["created_at"])
            current_key = (
                (
                    int(current["iteration"]),
                    int(current["attempt"]),
                    current["created_at"],
                )
                if current
                else None
            )
            if current is None or item_key > current_key:
                latest[item["node_id"]] = item
        return latest

    @staticmethod
    def _run_mode(graph: Mapping[str, Any]) -> WorkflowRunMode:
        policy = graph.get("run_policy", {})
        raw_mode = policy.get("mode") if isinstance(policy, Mapping) else None
        if raw_mode:
            try:
                return WorkflowRunMode(str(raw_mode))
            except ValueError:
                return WorkflowRunMode.KEY_APPROVAL
        has_approval = any(edge.get("type") == EdgeType.APPROVAL.value for edge in graph.get("edges", []))
        return WorkflowRunMode.KEY_APPROVAL if has_approval else WorkflowRunMode.FULL_AUTO

    @staticmethod
    def _node_max_attempts(node: Mapping[str, Any]) -> int:
        value = node.get("max_attempts", 1)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return 1
        return value

    @staticmethod
    def _is_model_node(node: Mapping[str, Any]) -> bool:
        return str(node.get("type") or "agent") in MODEL_NODE_TYPES

    @staticmethod
    def _has_irreversible_side_effects(node: Mapping[str, Any]) -> bool:
        return bool(
            set(node.get("side_effects") or ())
            & {"write_data", "export_file", "network"}
        )

    @staticmethod
    def _is_restart_recovery_failure(job: Mapping[str, Any]) -> bool:
        return (
            str(job.get("status") or "") == STATUS_FAILED
            and str(job.get("error") or "").strip() == RESTART_RECOVERY_ERROR
        )

    def _completed_side_effect_output(
        self,
        node_run: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        """Return a durable result that is safe to reconcile without replay."""
        getter = getattr(self.run_store, "get_side_effect_for_node_run", None)
        if not callable(getter):
            return None
        effect = getter(str(node_run["id"]), effect_type="export_file")
        if not isinstance(effect, Mapping) or str(effect.get("status") or "") != "succeeded":
            return None
        result = effect.get("result")
        return dict(result) if isinstance(result, Mapping) and result else None

    @staticmethod
    def _graph_limit(graph: Mapping[str, Any], name: str) -> Any:
        limits = graph.get("limits", {})
        return limits.get(name) if isinstance(limits, Mapping) else None

    def _node_run_budget_allows(
        self,
        run_id: str,
        graph: Mapping[str, Any],
        *,
        additional: int = 1,
    ) -> bool:
        """Return whether another persisted NodeRun may be created.

        The initial graph rows are reserved when a Run is created.  They count
        toward the contract, so only retries/iterations need to ask this
        helper before inserting another row.
        """
        limit = self._graph_limit(graph, "max_total_node_runs")
        if limit is None:
            return True
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return False
        return len(self.run_store.list_node_runs(run_id)) + max(0, int(additional)) <= limit

    def _remaining_graph_tokens(
        self,
        run_id: str,
        graph: Mapping[str, Any],
    ) -> int | None:
        limit = self._graph_limit(graph, "max_total_tokens")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return None
        used = sum(
            int(item.get("input_tokens") or 0)
            + int(item.get("output_tokens") or 0)
            + int(item.get("reserved_tokens") or 0)
            for item in self.run_store.list_node_runs(run_id)
        )
        return max(0, limit - used)

    def _remaining_graph_cost(
        self,
        run_id: str,
        graph: Mapping[str, Any],
    ) -> float | None:
        limit = self._graph_limit(graph, "max_total_cost_usd")
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0:
            return None
        node_runs = self.run_store.list_node_runs(run_id)
        measured = [
            item
            for item in node_runs
            if int(item.get("input_tokens") or 0)
            or int(item.get("output_tokens") or 0)
            or int(item.get("model_calls") or 0)
        ]
        if any(item.get("cost_usd") is None for item in measured):
            return None
        try:
            used = sum(float(item.get("cost_usd") or 0) for item in measured)
            used += sum(float(item.get("reserved_cost_usd") or 0) for item in node_runs)
        except (TypeError, ValueError):
            return None
        return max(0.0, float(limit) - used)

    def _apply_graph_model_budget(
        self,
        run_id: str,
        graph: Mapping[str, Any],
        node: Mapping[str, Any],
        *,
        reserved_tokens: int | None = None,
        reserved_cost_usd: float | None = None,
    ) -> dict[str, Any]:
        """Clamp a model node to its atomically reserved graph budget."""
        bounded = dict(node)
        node_limits = dict(node.get("limits") or {})
        remaining_tokens = (
            reserved_tokens if reserved_tokens is not None else self._remaining_graph_tokens(run_id, graph)
        )
        if remaining_tokens is not None:
            configured = node_limits.get("max_total_tokens")
            if isinstance(configured, int) and not isinstance(configured, bool):
                node_limits["max_total_tokens"] = min(configured, remaining_tokens)
            else:
                node_limits["max_total_tokens"] = remaining_tokens
        remaining_cost = (
            reserved_cost_usd if reserved_cost_usd is not None else self._remaining_graph_cost(run_id, graph)
        )
        if remaining_cost is not None:
            configured_cost = node_limits.get("max_cost_usd")
            try:
                node_cost = float(configured_cost) if configured_cost is not None else None
            except (TypeError, ValueError):
                node_cost = None
            node_limits["max_cost_usd"] = (
                min(node_cost, remaining_cost) if node_cost is not None else remaining_cost
            )
        bounded["limits"] = node_limits
        return bounded

    def _model_budget_reservation(
        self,
        run_id: str,
        graph: Mapping[str, Any],
        node: Mapping[str, Any],
    ) -> tuple[int | None, float | None] | None:
        """Calculate a conservative reservation before an atomic node claim."""
        node_limits = node.get("limits") or {}
        if not isinstance(node_limits, Mapping):
            node_limits = {}
        reserved_tokens: int | None = None
        graph_token_limit = self._graph_limit(graph, "max_total_tokens")
        if graph_token_limit is not None:
            remaining_tokens = self._remaining_graph_tokens(run_id, graph)
            if remaining_tokens is None or remaining_tokens <= 0:
                return None
            configured = node_limits.get("max_total_tokens")
            if isinstance(configured, int) and not isinstance(configured, bool):
                reserved_tokens = min(configured, remaining_tokens)
            else:
                reserved_tokens = remaining_tokens
            if reserved_tokens <= 0:
                return None

        reserved_cost: float | None = None
        graph_cost_limit = self._graph_limit(graph, "max_total_cost_usd")
        if graph_cost_limit is not None:
            remaining_cost = self._remaining_graph_cost(run_id, graph)
            if remaining_cost is not None:
                if remaining_cost <= 0:
                    return None
                configured_cost = node_limits.get("max_cost_usd")
                try:
                    node_cost = float(configured_cost) if configured_cost is not None else None
                except (TypeError, ValueError):
                    node_cost = None
                reserved_cost = min(node_cost, remaining_cost) if node_cost is not None else remaining_cost
                if reserved_cost <= 0:
                    return None
        return reserved_tokens, reserved_cost

    @staticmethod
    def _retry_iteration_limit(graph: Mapping[str, Any], node_id: str) -> int:
        """Return the maximum total iterations allowed for a human retry."""
        limits: list[int] = []
        for edge in graph.get("edges", []):
            if edge.get("type") == EdgeType.RETRY_LOOP.value and str(edge.get("from_node")) == node_id:
                raw = edge.get("max_iterations")
                if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
                    limits.append(raw)
        if limits:
            return min(limits)

        for node in graph.get("nodes", []):
            if str(node.get("node_id")) == node_id:
                raw = node.get("max_iterations")
                if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
                    return raw
                break
        return 2

    @staticmethod
    def _retry_iteration_nodes(graph: Mapping[str, Any], node_id: str) -> list[str]:
        targets: list[str] = []
        for edge in graph.get("edges", []):
            if edge.get("type") == EdgeType.RETRY_LOOP.value and str(edge.get("from_node")) == node_id:
                target = str(edge.get("to_node") or "").strip()
                if target and target not in targets:
                    targets.append(target)
        if not targets:
            return [node_id]
        if node_id not in targets:
            targets.append(node_id)
        return targets

    @staticmethod
    def _run_deadline(run: Mapping[str, Any], graph: Mapping[str, Any]) -> datetime | None:
        raw_minutes = graph.get("limits", {}).get("max_run_minutes")
        if isinstance(raw_minutes, bool) or not isinstance(raw_minutes, int) or raw_minutes < 1:
            return None
        try:
            started_at = datetime.fromisoformat(str(run["started_at"]))
        except (KeyError, TypeError, ValueError):
            return None
        return started_at + timedelta(minutes=raw_minutes)

    def _expire_timed_out_run(
        self,
        run: Mapping[str, Any],
        graph: Mapping[str, Any],
    ) -> bool:
        deadline = self._run_deadline(run, graph)
        if deadline is None or datetime.now() <= deadline:
            return False
        run_id = str(run["id"])
        for node_run in self.run_store.list_node_runs(run_id):
            status = NodeRunStatus(node_run["status"])
            if status in NODE_RUN_TERMINAL_STATUSES:
                continue
            if node_run["job_id"]:
                job = self.job_runner.get_status(node_run["job_id"])
                if job and job.get("status") not in {
                    STATUS_CANCELED,
                    STATUS_FAILED,
                    STATUS_SUCCEEDED,
                }:
                    self.job_runner.cancel(node_run["job_id"])
            self.run_store.transition_node(
                node_run["id"],
                NodeRunStatus.CANCELED,
                error="workflow run timed out",
            )
        self.run_store.transition_run(
            run_id,
            RunStatus.FAILED,
            failure_code="workflow_run_timeout",
            failure_message="workflow run exceeded max_run_minutes",
        )
        self._notify_run_terminal(run_id)
        return True

    def _expire_token_budget(self, run_id: str, graph: Mapping[str, Any]) -> bool:
        limit = graph.get("limits", {}).get("max_total_tokens")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return False
        used = sum(
            int(item.get("input_tokens") or 0) + int(item.get("output_tokens") or 0)
            for item in self.run_store.list_node_runs(run_id)
        )
        if used < limit:
            return False
        for node_run in self.run_store.list_node_runs(run_id):
            if NodeRunStatus(node_run["status"]) is NodeRunStatus.READY:
                self.run_store.transition_node(
                    node_run["id"], NodeRunStatus.CANCELED, error="workflow token budget exceeded"
                )
        self.run_store.transition_run(
            run_id,
            RunStatus.FAILED,
            failure_code="workflow_token_budget_exceeded",
            failure_message=f"workflow consumed {used} tokens, above max_total_tokens={limit}",
        )
        self._notify_run_terminal(run_id)
        return True

    def _expire_cost_budget(self, run_id: str, graph: Mapping[str, Any]) -> bool:
        limit = graph.get("limits", {}).get("max_total_cost_usd")
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0:
            return False
        node_runs = self.run_store.list_node_runs(run_id)
        # Only completed model usage is measurable. Pending/ready deterministic
        # nodes have no cost field and must not make the graph check inert.
        measured = [
            item
            for item in node_runs
            if int(item.get("input_tokens") or 0) or int(item.get("output_tokens") or 0)
        ]
        if not measured:
            return False
        if any(item.get("cost_usd") is None for item in measured):
            # Unknown pricing is deliberately not converted to zero. The run
            # remains auditable, but a cost ceiling cannot be asserted yet.
            return False
        used = sum(float(item["cost_usd"]) for item in measured)
        if used < float(limit):
            return False
        for node_run in self.run_store.list_node_runs(run_id):
            if NodeRunStatus(node_run["status"]) is NodeRunStatus.READY:
                self.run_store.transition_node(
                    node_run["id"], NodeRunStatus.CANCELED, error="workflow cost budget exceeded"
                )
        self.run_store.transition_run(
            run_id,
            RunStatus.FAILED,
            failure_code="workflow_cost_budget_exceeded",
            failure_message=f"workflow consumed {used:.8f} USD, at or above max_total_cost_usd={float(limit):.8f}",
        )
        self._notify_run_terminal(run_id)
        return True

    def _reconcile_jobs(self, run_id: str, graph: Mapping[str, Any]) -> None:
        nodes_by_id = {str(node["node_id"]): dict(node) for node in graph.get("nodes", [])}
        mode = self._run_mode(graph)
        for node_run in self.run_store.list_node_runs(run_id):
            if node_run["status"] not in {
                NodeRunStatus.QUEUED.value,
                NodeRunStatus.RUNNING.value,
            }:
                continue
            if not node_run["job_id"]:
                self.run_store.transition_node(
                    node_run["id"],
                    NodeRunStatus.FAILED,
                    error="claimed node has no bound Job",
                )
                continue
            job = self.job_runner.get_status(node_run["job_id"])
            if job is None:
                self.run_store.transition_node(
                    node_run["id"],
                    NodeRunStatus.FAILED,
                    error="bound Job is missing",
                )
                continue
            current = NodeRunStatus(node_run["status"])
            if job["status"] == STATUS_RUNNING and current is NodeRunStatus.QUEUED:
                self.run_store.transition_node(node_run["id"], NodeRunStatus.RUNNING)
            elif job["status"] == STATUS_SUCCEEDED:
                self.limiter.release(
                    self.run_store.workspace_id,
                    run_id,
                    self._concurrency_key(node_run),
                )
                if current is NodeRunStatus.QUEUED:
                    self.run_store.transition_node(node_run["id"], NodeRunStatus.RUNNING)
                job_result = job.get("result")
                usage: Mapping[str, Any] | None = None
                if isinstance(job_result, Mapping):
                    job_result = dict(job_result)
                    raw_usage = job_result.pop("__workflow_usage__", None)
                    usage = raw_usage if isinstance(raw_usage, Mapping) else None
                    output_error = str(job_result.pop("__workflow_output_error__", "") or "")
                else:
                    output_error = ""
                if usage:
                    self.run_store.record_node_usage(node_run["id"], usage)
                self.run_store.transition_node(
                    node_run["id"],
                    NodeRunStatus.OUTPUT_READY,
                    output=job_result,
                    artifact_types=nodes_by_id.get(node_run["node_id"], {}).get("output_artifacts", {}),
                )
                if output_error:
                    self.run_store.transition_node(
                        node_run["id"],
                        NodeRunStatus.FAILED,
                        error=output_error,
                    )
                    continue
                node_definition = nodes_by_id.get(node_run["node_id"], {})
                is_verifier_rework = (
                    node_definition.get("type") == "verifier"
                    and isinstance(job_result, Mapping)
                    and job_result.get("decision") == "rework"
                )
                if is_verifier_rework:
                    # A failed independent check never opens a side-effect
                    # approval.  It returns to the graph's bounded rework
                    # loop first, so a reviewer cannot approve a known-bad
                    # plan by mistake.
                    self.run_store.transition_node(
                        node_run["id"],
                        NodeRunStatus.SUCCEEDED,
                    )
                    retry_node_ids = self._retry_iteration_nodes(graph, str(node_run["node_id"]))
                    if not self._node_run_budget_allows(run_id, graph, additional=len(retry_node_ids)):
                        for pending_node in self.run_store.list_node_runs(run_id):
                            if pending_node["status"] in {
                                NodeRunStatus.PENDING.value,
                                NodeRunStatus.READY.value,
                            }:
                                self.run_store.transition_node(
                                    pending_node["id"],
                                    NodeRunStatus.CANCELED,
                                    error=NODE_RUN_BUDGET_ERROR,
                                )
                        self.run_store.record_event(
                            run_id,
                            "workflow_node_run_budget_exceeded",
                            {
                                "run_id": run_id,
                                "node_run_id": node_run["id"],
                                "node_id": node_run["node_id"],
                                "max_total_node_runs": self._graph_limit(graph, "max_total_node_runs"),
                                "node_run_count": len(self.run_store.list_node_runs(run_id)),
                                "requested_retry_count": len(retry_node_ids),
                            },
                        )
                        self.run_store.transition_run(
                            run_id,
                            RunStatus.FAILED,
                            failure_code=WorkflowErrorCode.NODE_RUN_LIMIT_REACHED.value,
                            failure_message=NODE_RUN_BUDGET_ERROR,
                        )
                        continue
                    for retry_node_id in retry_node_ids:
                        latest = self._latest_by_node(self.run_store.list_node_runs(run_id)).get(
                            retry_node_id
                        )
                        if latest:
                            self.run_store.create_retry_iteration(
                                latest["id"],
                                max_iteration=self._retry_limit(graph, str(node_run["node_id"])),
                            )
                    continue
                if self._requires_key_approval(node_run, graph):
                    self._open_node_approval(
                        run_id,
                        node_run,
                        mode=mode.value,
                        reason="key_approval",
                    )
                    continue
                self.run_store.transition_node(
                    node_run["id"],
                    NodeRunStatus.SUCCEEDED,
                )
            elif job["status"] == STATUS_FAILED:
                self.limiter.release(
                    self.run_store.workspace_id,
                    run_id,
                    self._concurrency_key(node_run),
                )
                node = nodes_by_id.get(node_run["node_id"], {})
                if self._is_restart_recovery_failure(job) and self._has_irreversible_side_effects(node):
                    completed_output = self._completed_side_effect_output(node_run)
                    if completed_output is not None:
                        if current is NodeRunStatus.QUEUED:
                            self.run_store.transition_node(
                                node_run["id"],
                                NodeRunStatus.RUNNING,
                            )
                        self.run_store.transition_node(
                            node_run["id"],
                            NodeRunStatus.OUTPUT_READY,
                            output=completed_output,
                            artifact_types=node.get("output_artifacts", {}),
                        )
                        self.run_store.record_event(
                            run_id,
                            "workflow_side_effect_recovered",
                            {
                                "run_id": run_id,
                                "node_run_id": node_run["id"],
                                "node_id": node_run["node_id"],
                                "effect_type": "export_file",
                                "reason": "durable side-effect completion found after Job restart",
                            },
                        )
                        if self._requires_key_approval(node_run, graph):
                            self._open_node_approval(
                                run_id,
                                node_run,
                                mode=mode.value,
                                reason="key_approval",
                            )
                        else:
                            self.run_store.transition_node(
                                node_run["id"],
                                NodeRunStatus.SUCCEEDED,
                            )
                        continue
                    self.run_store.transition_node(
                        node_run["id"],
                        NodeRunStatus.FAILED,
                        error=RESTART_REPLAY_BLOCKED_ERROR,
                    )
                    self.run_store.record_event(
                        run_id,
                        "workflow_side_effect_replay_blocked",
                        {
                            "run_id": run_id,
                            "node_run_id": node_run["id"],
                            "node_id": node_run["node_id"],
                            "job_id": node_run["job_id"],
                            "side_effects": sorted(node.get("side_effects") or ()),
                            "reason": RESTART_REPLAY_BLOCKED_ERROR,
                        },
                    )
                    continue
                if int(node_run["attempt"]) < self._node_max_attempts(node):
                    if not self._node_run_budget_allows(run_id, graph):
                        self.run_store.transition_node(
                            node_run["id"],
                            NodeRunStatus.FAILED,
                            error=NODE_RUN_BUDGET_ERROR,
                        )
                        self.run_store.record_event(
                            run_id,
                            "workflow_node_run_budget_exceeded",
                            {
                                "run_id": run_id,
                                "node_run_id": node_run["id"],
                                "node_id": node_run["node_id"],
                                "max_total_node_runs": self._graph_limit(graph, "max_total_node_runs"),
                                "node_run_count": len(self.run_store.list_node_runs(run_id)),
                            },
                        )
                    else:
                        self.run_store.transition_node(
                            node_run["id"],
                            NodeRunStatus.FAILED,
                            error=str(job.get("error") or "workflow node Job failed"),
                        )
                        self.run_store.create_retry_attempt(node_run["id"])
                elif mode is WorkflowRunMode.EXCEPTION_REVIEW:
                    if current is NodeRunStatus.QUEUED:
                        self.run_store.transition_node(node_run["id"], NodeRunStatus.RUNNING)
                    self.run_store.transition_node(
                        node_run["id"],
                        NodeRunStatus.OUTPUT_READY,
                        output={
                            "exception_review": True,
                            "error": str(job.get("error") or "workflow node Job failed"),
                        },
                    )
                    self._open_node_approval(
                        run_id,
                        node_run,
                        mode=mode.value,
                        reason="exception_review",
                    )
                else:
                    self.run_store.transition_node(
                        node_run["id"],
                        NodeRunStatus.FAILED,
                        error=str(job.get("error") or "workflow node Job failed"),
                    )
            elif job["status"] == STATUS_CANCELED:
                self.limiter.release(
                    self.run_store.workspace_id,
                    run_id,
                    self._concurrency_key(node_run),
                )
                self.run_store.transition_node(
                    node_run["id"],
                    NodeRunStatus.CANCELED,
                    error="workflow node Job was canceled",
                )

    def _requires_key_approval(
        self,
        node_run: Mapping[str, Any],
        graph: Mapping[str, Any],
    ) -> bool:
        if self._run_mode(graph) is not WorkflowRunMode.KEY_APPROVAL:
            return False
        node_id = str(node_run["node_id"])
        return any(
            edge.get("type") == EdgeType.APPROVAL.value and str(edge.get("from_node")) == node_id
            for edge in graph.get("edges", [])
        )

    def _open_node_approval(
        self,
        run_id: str,
        node_run: Mapping[str, Any],
        *,
        mode: str,
        reason: str,
    ) -> None:
        latest_node_run = self.run_store.get_node_run(str(node_run["id"])) or node_run
        self.run_store.create_approval(
            run_id=run_id,
            node_run_id=str(latest_node_run["id"]),
            node_id=str(latest_node_run["node_id"]),
            mode=mode,
            reason=reason,
            artifact_manifest_id=str(latest_node_run.get("output_manifest_id") or ""),
        )
        self.run_store.transition_node(str(latest_node_run["id"]), NodeRunStatus.WAITING_APPROVAL)
        run = self.run_store.get_run(run_id)
        if run and run["status"] == RunStatus.RUNNING.value:
            self.run_store.transition_run(run_id, RunStatus.WAITING_APPROVAL)

    @staticmethod
    def _predecessors(graph: Mapping[str, Any]) -> dict[str, list[str]]:
        result = {str(node["node_id"]): [] for node in graph.get("nodes", [])}
        for edge in graph.get("edges", []):
            if edge.get("type", EdgeType.AUTO.value) != EdgeType.RETRY_LOOP.value:
                result[str(edge["to_node"])].append(str(edge["from_node"]))
        return result

    def _mark_ready_nodes(self, run_id: str, graph: Mapping[str, Any]) -> None:
        node_runs = self._latest_by_node(self.run_store.list_node_runs(run_id))
        predecessors = self._predecessors(graph)
        entries = set(graph.get("entry_node_ids", []))
        for node_id, node_run in node_runs.items():
            if node_run["status"] != NodeRunStatus.PENDING.value:
                continue
            node = next(
                (item for item in graph.get("nodes", []) if str(item.get("node_id")) == node_id),
                {},
            )
            required = predecessors[node_id]
            conditional_edges = [
                edge
                for edge in graph.get("edges", [])
                if str(edge.get("to_node")) == node_id and edge.get("type") == EdgeType.CONDITIONAL.value
            ]
            if conditional_edges:
                selected = []
                unresolved = False
                for edge in conditional_edges:
                    source = str(edge["from_node"])
                    source_run = node_runs[source]
                    source_status = NodeRunStatus(source_run["status"])
                    if source_status not in NODE_RUN_TERMINAL_STATUSES:
                        unresolved = True
                        continue
                    if source_status is not NodeRunStatus.SUCCEEDED:
                        continue
                    output = source_run.get("output") or {}
                    condition = edge.get("condition") or {}
                    if isinstance(output, Mapping) and output.get(condition.get("field")) == condition.get(
                        "equals"
                    ):
                        selected.append(source)
                required = [
                    source
                    for source in required
                    if source not in {str(edge["from_node"]) for edge in conditional_edges}
                ] + selected
                if not selected and not unresolved and node_id not in entries:
                    self.run_store.transition_node(node_run["id"], NodeRunStatus.SKIPPED)
                    continue
            if node_id in entries and not required:
                self.run_store.transition_node(node_run["id"], NodeRunStatus.READY)
                continue
            states = [NodeRunStatus(node_runs[source]["status"]) for source in required]
            if states and all(state is NodeRunStatus.SUCCEEDED for state in states):
                self.run_store.transition_node(node_run["id"], NodeRunStatus.READY)
                continue
            if states and all(state in NODE_RUN_TERMINAL_STATUSES for state in states):
                if str(node.get("join_policy") or "all_success") == "all_terminal" and any(
                    state is NodeRunStatus.SUCCEEDED for state in states
                ):
                    self.run_store.transition_node(node_run["id"], NodeRunStatus.READY)
                else:
                    self.run_store.transition_node(node_run["id"], NodeRunStatus.SKIPPED)

    def _material_value(self, item: Mapping[str, Any]) -> Any:
        if "data" in item:
            return item["data"]
        artifact_id = str(item.get("artifact_id") or "")
        if artifact_id:
            content = self.run_store.get_artifact_content(artifact_id)
            if content is not None:
                return content
        if item.get("content_available"):
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "complete Artifact content is unavailable: " + artifact_id,
            )
        # Legacy manifests created before Artifact blobs had no recoverable
        # full body. Never pass their preview to a new downstream node as if it
        # were the source material.
        if item.get("data_preview"):
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "legacy Artifact contains only a preview and cannot be consumed safely: " + artifact_id,
            )
        return {
            "artifact_id": artifact_id,
            "logical_name": item.get("logical_name", item.get("name", "")),
            "uri": item.get("uri", ""),
            "sha256": item.get("sha256", ""),
            "media_type": item.get("media_type", ""),
            "size": item.get("size", 0),
            "preview": item.get("data_preview", ""),
        }

    def _node_inputs(
        self,
        run: Mapping[str, Any],
        node_run: Mapping[str, Any],
        graph: Mapping[str, Any],
    ) -> dict[str, Any]:
        node_id = str(node_run["node_id"])
        nodes = {str(node["node_id"]): dict(node) for node in graph.get("nodes", [])}
        required_contract = set(nodes[node_id].get("input_contract") or [])
        values: dict[str, Any] = {}
        predecessors = self._predecessors(graph)[node_id]
        # Backward compatibility for published templates created before
        # ``business_context`` became a declared input.  The optional context
        # is only injected into an entry node and remains visible in its saved
        # node input, so it cannot leak across arbitrary downstream edges.
        entry_context = not predecessors
        run_manifest = self.run_store.get_manifest(str(run.get("input_manifest_id") or ""))
        if run_manifest:
            for item in run_manifest.get("items", []):
                name = str(item.get("logical_name") or item.get("name") or "")
                if name in required_contract or (entry_context and name == "business_context"):
                    values[name] = self._material_value(item)
        saved_inputs = node_run.get("input") or {}
        if isinstance(saved_inputs, Mapping):
            for name in required_contract:
                if name in saved_inputs:
                    values[name] = saved_inputs[name]
        by_id = self._latest_by_node(self.run_store.list_node_runs(run["id"]))
        for source in predecessors:
            producer = by_id[source]
            if producer.get("status") != NodeRunStatus.SUCCEEDED.value:
                continue
            manifest = self.run_store.get_manifest(producer.get("output_manifest_id") or "")
            if not manifest:
                continue
            for item in manifest.get("items", []):
                name = str(item.get("logical_name") or item.get("name") or "")
                if name not in required_contract:
                    continue
                values[name] = self._material_value(item)
                self.run_store.record_artifact_consumption(
                    run_id=str(run["id"]),
                    consumer_node_run_id=str(node_run["id"]),
                    producer_node_run_id=str(producer["id"]),
                    manifest_id=str(manifest["id"]),
                    artifact_id=str(item.get("artifact_id") or ""),
                    purpose=name,
                )
        # Older published analysis templates connected the final report only
        # to verification_report. Preserve their immutable graph while giving
        # the management report the two substantive analyses it needs. Each
        # borrowed artifact is explicitly recorded for auditability.
        if "operating_report" in set(nodes[node_id].get("output_contract") or []):
            report_context_names = {"metric_analysis", "anomaly_analysis"}
            for producer in by_id.values():
                if producer.get("status") != NodeRunStatus.SUCCEEDED.value:
                    continue
                manifest = self.run_store.get_manifest(producer.get("output_manifest_id") or "")
                if not manifest:
                    continue
                for item in manifest.get("items", []):
                    name = str(item.get("logical_name") or item.get("name") or "")
                    if name not in report_context_names or name in values:
                        continue
                    values[name] = self._material_value(item)
                    self.run_store.record_artifact_consumption(
                        run_id=str(run["id"]),
                        consumer_node_run_id=str(node_run["id"]),
                        producer_node_run_id=str(producer["id"]),
                        manifest_id=str(manifest["id"]),
                        artifact_id=str(item.get("artifact_id") or ""),
                        purpose=f"report_context:{name}",
                    )
        return values

    def _dispatch_ready_nodes(self, run_id: str, graph: Mapping[str, Any]) -> None:
        run = self.run_store.get_run(run_id)
        if run is None or run["status"] != RunStatus.RUNNING.value:
            return
        nodes = {str(node["node_id"]): dict(node) for node in graph.get("nodes", [])}
        graph_has_model_budget = (
            self._graph_limit(graph, "max_total_tokens") is not None
            or self._graph_limit(graph, "max_total_cost_usd") is not None
        )
        active_model_nodes = sum(
            1
            for item in self.run_store.list_node_runs(run_id)
            if item["status"]
            in {
                NodeRunStatus.QUEUED.value,
                NodeRunStatus.RUNNING.value,
            }
            and self._is_model_node(nodes.get(str(item["node_id"]), {}))
        )
        max_concurrent = graph.get("limits", {}).get("max_concurrent_node_runs")
        active_count = sum(
            1
            for item in self.run_store.list_node_runs(run_id)
            if item["status"] in {NodeRunStatus.QUEUED.value, NodeRunStatus.RUNNING.value}
        )
        for node_run in self.run_store.list_node_runs(run_id):
            if node_run["status"] != NodeRunStatus.READY.value:
                continue
            if (
                isinstance(max_concurrent, int)
                and not isinstance(max_concurrent, bool)
                and active_count >= max_concurrent
            ):
                break
            node = nodes.get(str(node_run["node_id"]))
            if node is None:
                continue
            profile_id = self._concurrency_key(node_run)
            if not self.limiter.acquire(
                self.run_store.workspace_id,
                run_id,
                profile_id,
            ):
                continue
            is_model_node = self._is_model_node(node)
            if graph_has_model_budget and is_model_node and active_model_nodes:
                self.limiter.release(
                    self.run_store.workspace_id,
                    run_id,
                    profile_id,
                )
                continue
            reserved_tokens: int | None = None
            reserved_cost_usd: float | None = None
            if graph_has_model_budget and is_model_node:
                reservation = self._model_budget_reservation(run_id, graph, node)
                if reservation is None:
                    self.limiter.release(
                        self.run_store.workspace_id,
                        run_id,
                        profile_id,
                    )
                    continue
                reserved_tokens, reserved_cost_usd = reservation
            operation_key = (
                f"dispatch:{run_id}:{node_run['node_id']}:{node_run['iteration']}:{node_run['attempt']}"
            )
            claim_with_budget = getattr(self.run_store, "claim_node_with_budget", None)
            if callable(claim_with_budget):
                claimed = claim_with_budget(
                    node_run["id"],
                    operation_key,
                    max_total_tokens=(
                        self._graph_limit(graph, "max_total_tokens")
                        if graph_has_model_budget and is_model_node
                        else None
                    ),
                    max_total_cost_usd=(
                        self._graph_limit(graph, "max_total_cost_usd")
                        if graph_has_model_budget and is_model_node
                        else None
                    ),
                    reserved_tokens=reserved_tokens or 0,
                    reserved_cost_usd=reserved_cost_usd,
                )
            else:
                claimed = self.run_store.claim_node(node_run["id"], operation_key)
            if not claimed:
                self.limiter.release(self.run_store.workspace_id, run_id, profile_id)
                continue
            try:
                if self.preflight is not None:
                    preflight_node = dict(node)
                    preflight_node["__pfs_workflow_graph_limits__"] = dict(graph.get("limits") or {})
                    self.preflight(preflight_node)
                inputs = self._node_inputs(run, node_run, graph)
                self.run_store.set_node_input(node_run["id"], inputs)
                execution_node = (
                    self._apply_graph_model_budget(
                        run_id,
                        graph,
                        node,
                        reserved_tokens=reserved_tokens,
                        reserved_cost_usd=reserved_cost_usd,
                    )
                    if graph_has_model_budget and is_model_node
                    else dict(node)
                )
                execution_node["__pfs_workflow_context__"] = {
                    "run_id": run_id,
                    "node_run_id": str(node_run["id"]),
                    "node_id": str(node_run["node_id"]),
                    "iteration": int(node_run["iteration"]),
                }
                job_id = self.job_runner.create(
                    lambda ctx, item=execution_node, material=inputs: self.executor(
                        item,
                        material,
                        ctx,
                    ),
                    job_type="workflow_node",
                    label=node_run["node_id"],
                )
                if not self.run_store.bind_job(node_run["id"], job_id):
                    self.job_runner.cancel(job_id)
                    raise RuntimeError("failed to bind workflow Job")
                active_count += 1
                listener = getattr(self.job_runner, "add_terminal_listener", None)
                if callable(listener):
                    listener(
                        job_id,
                        lambda _job, rid=run_id, pid=profile_id: self._job_terminal(
                            rid,
                            pid,
                        ),
                    )
                if is_model_node:
                    active_model_nodes += 1
            except Exception as exc:
                self.limiter.release(self.run_store.workspace_id, run_id, profile_id)
                if isinstance(exc, WorkflowContractError) and exc.code is WorkflowErrorCode.PERMISSION_DENIED:
                    self.run_store.record_event(
                        run_id,
                        "workflow_policy_denied",
                        {
                            "run_id": run_id,
                            "node_run_id": node_run["id"],
                            "node_id": node_run["node_id"],
                            "requested_side_effects": sorted(node.get("side_effects") or ()),
                            "policy_source": "workspace.permission",
                            "reason": str(exc),
                            "error_code": exc.code.value,
                        },
                    )
                self.run_store.transition_node(
                    node_run["id"],
                    NodeRunStatus.FAILED,
                    error=f"Job submission failed: {exc}",
                )

    def _job_terminal(self, run_id: str, profile_id: str) -> None:
        self.limiter.release(self.run_store.workspace_id, run_id, profile_id)
        self.advance(run_id)

    def pause(self, run_id: str, *, reason: str = "") -> dict[str, Any]:
        """Pause scheduling without discarding durable node or Job state.

        In-flight Jobs are allowed to finish cooperatively. Their terminal
        callbacks may reconcile outputs while the Run is paused, but no new
        node is dispatched until an explicit resume. This makes pause safe
        for a process restart and keeps the decision visible in the Run event
        stream instead of encoding it only in process memory.
        """
        with self._run_lock(run_id):
            run = self.run_store.get_run(run_id)
            if run is None:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND,
                    f"workflow run not found: {run_id}",
                )
            status = RunStatus(run["status"])
            if status in RUN_TERMINAL_STATUSES or status is RunStatus.PAUSED:
                return self.detail(run_id)
            if status not in {RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}:
                raise WorkflowContractError(
                    WorkflowErrorCode.RUN_NOT_RECOVERABLE,
                    f"workflow run cannot be paused from {status.value}",
                )
            active_nodes = [
                str(item["id"])
                for item in self.run_store.list_node_runs(run_id)
                if item["status"]
                in {
                    NodeRunStatus.QUEUED.value,
                    NodeRunStatus.RUNNING.value,
                }
            ]
            pending_approvals = [
                str(item["id"])
                for item in self.run_store.list_approvals(run_id)
                if item["status"] == "pending"
            ]
            self.run_store.transition_run(run_id, RunStatus.PAUSED)
            self.run_store.record_event(
                run_id,
                "workflow_run_paused",
                {
                    "run_id": run_id,
                    "previous_status": status.value,
                    "reason": str(reason or "").strip()[:500],
                    "active_node_run_ids": active_nodes,
                    "pending_approval_ids": pending_approvals,
                },
            )
            return self.detail(run_id)

    def resume(self, run_id: str) -> dict[str, Any]:
        """Resume a deliberately paused Run without bypassing approvals."""
        with self._run_lock(run_id):
            run = self.run_store.get_run(run_id)
            if run is None:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND,
                    f"workflow run not found: {run_id}",
                )
            status = RunStatus(run["status"])
            if status is RunStatus.RUNNING:
                return self.advance(run_id)
            if status is not RunStatus.PAUSED:
                raise WorkflowContractError(
                    WorkflowErrorCode.RUN_NOT_RECOVERABLE,
                    f"workflow run cannot be resumed from {status.value}",
                )
            pending_approvals = [
                item for item in self.run_store.list_approvals(run_id) if item["status"] == "pending"
            ]
            target = RunStatus.WAITING_APPROVAL if pending_approvals else RunStatus.RUNNING
            self.run_store.transition_run(run_id, target)
            self.run_store.record_event(
                run_id,
                "workflow_run_resumed",
                {
                    "run_id": run_id,
                    "status": target.value,
                    "pending_approval_ids": [str(item["id"]) for item in pending_approvals],
                },
            )
            return self.advance(run_id)

    @staticmethod
    def _forward_descendants(graph: Mapping[str, Any], node_id: str) -> set[str]:
        adjacency: dict[str, set[str]] = {}
        for edge in graph.get("edges", []):
            if str(edge.get("type") or "auto") == EdgeType.RETRY_LOOP.value:
                continue
            source = str(edge.get("from_node") or "")
            target = str(edge.get("to_node") or "")
            if source and target:
                adjacency.setdefault(source, set()).add(target)
        descendants: set[str] = set()
        pending = list(adjacency.get(node_id, ()))
        while pending:
            current = pending.pop()
            if current in descendants:
                continue
            descendants.add(current)
            pending.extend(adjacency.get(current, ()))
        return descendants

    def retry_node(self, run_id: str, node_run_id: str) -> dict[str, Any]:
        """Explicitly retry the latest failed node and reopen its descendants."""
        with self._run_lock(run_id):
            run = self.run_store.get_run(run_id)
            if run is None:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND,
                    f"workflow run not found: {run_id}",
                )
            if RunStatus(run["status"]) is not RunStatus.FAILED:
                raise WorkflowContractError(
                    WorkflowErrorCode.RUN_NOT_RECOVERABLE,
                    "only a failed workflow run can retry a node manually",
                )
            node_run = self.run_store.get_node_run(node_run_id)
            if node_run is None or str(node_run.get("run_id")) != run_id:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND,
                    f"workflow node run not found: {node_run_id}",
                )
            latest = self._latest_by_node(self.run_store.list_node_runs(run_id))
            latest_node = latest.get(str(node_run["node_id"]))
            if (
                latest_node is None
                or latest_node["id"] != node_run_id
                or NodeRunStatus(node_run["status"]) is not NodeRunStatus.FAILED
            ):
                raise WorkflowContractError(
                    WorkflowErrorCode.RUN_NOT_RECOVERABLE,
                    "only the latest failed node run can be retried",
                )
            version = self.workflow_store.get_version(run["workflow_version_id"])
            if version is None:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND,
                    "published workflow version is missing",
                )
            if not self._node_run_budget_allows(run_id, version["graph"]):
                raise WorkflowContractError(
                    WorkflowErrorCode.NODE_RUN_LIMIT_REACHED,
                    NODE_RUN_BUDGET_ERROR,
                )
            retried = self.run_store.create_retry_attempt(node_run_id)
            if retried is None:
                raise WorkflowContractError(
                    WorkflowErrorCode.IDEMPOTENCY_CONFLICT,
                    "a retry for this node run already exists",
                )
            for descendant_id in self._forward_descendants(version["graph"], str(node_run["node_id"])):
                descendant = latest.get(descendant_id)
                if descendant and NodeRunStatus(descendant["status"]) in NODE_RUN_TERMINAL_STATUSES:
                    self.run_store.create_retry_iteration(descendant["id"])
            self.run_store.transition_run(run_id, RunStatus.RUNNING)
            return self.advance(run_id)

    @staticmethod
    def _checkpoint_ancestors(graph: Mapping[str, Any], node_id: str) -> set[str]:
        """Return the successful prefix needed to make a checkpoint reusable."""
        incoming: dict[str, set[str]] = {}
        for edge in graph.get("edges", []):
            if str(edge.get("type") or "auto") == EdgeType.RETRY_LOOP.value:
                continue
            source = str(edge.get("from_node") or "")
            target = str(edge.get("to_node") or "")
            if source and target:
                incoming.setdefault(target, set()).add(source)
        required = {node_id}
        pending = [node_id]
        while pending:
            current = pending.pop()
            for source in incoming.get(current, ()):
                if source not in required:
                    required.add(source)
                    pending.append(source)
        return required

    def fork_from_checkpoint(
        self,
        run_id: str,
        checkpoint_node_run_id: str,
        *,
        session_id: str,
        started_by: str = "",
    ) -> dict[str, Any]:
        """Create an auditable branch that reuses a successful graph prefix."""
        with self._run_lock(run_id):
            source_run = self.run_store.get_run(run_id)
            if source_run is None:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND, f"workflow run not found: {run_id}"
                )
            if str(source_run.get("session_id") or "") != str(session_id):
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND, f"workflow run not found: {run_id}"
                )
            if RunStatus(source_run["status"]) not in RUN_TERMINAL_STATUSES:
                raise WorkflowContractError(
                    WorkflowErrorCode.RUN_NOT_RECOVERABLE, "only a terminal workflow run can be branched"
                )
            version = self.workflow_store.get_version(source_run["workflow_version_id"])
            if version is None:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND, "published workflow version is missing"
                )
            node_runs = self.run_store.list_node_runs(run_id)
            nodes_by_run_id = {str(node["id"]): node for node in node_runs}
            checkpoint = self.run_store.get_node_run(checkpoint_node_run_id)
            if checkpoint is None or checkpoint.get("run_id") != run_id:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND,
                    f"workflow node run not found: {checkpoint_node_run_id}",
                )
            if NodeRunStatus(checkpoint["status"]) is not NodeRunStatus.SUCCEEDED:
                raise WorkflowContractError(
                    WorkflowErrorCode.RUN_NOT_RECOVERABLE, "checkpoint node must have succeeded"
                )
            reusable_ids = self._checkpoint_ancestors(version["graph"], str(checkpoint["node_id"]))
            # Prefer the exact producer NodeRuns consumed by this checkpoint
            # (and by those producers).  This keeps a branch from an earlier
            # successful iteration coherent even if a later retry succeeded.
            consumptions_by_consumer: dict[str, list[str]] = {}
            for item in self.run_store.list_consumptions(run_id):
                consumer = str(item.get("consumer_node_run_id") or "")
                producer = str(item.get("producer_node_run_id") or "")
                if consumer and producer:
                    consumptions_by_consumer.setdefault(consumer, []).append(producer)
            exact_runs: dict[str, Mapping[str, Any]] = {}
            pending = [str(checkpoint["id"])]
            seen: set[str] = set()
            while pending:
                current_id = pending.pop()
                if current_id in seen:
                    continue
                seen.add(current_id)
                current = nodes_by_run_id.get(current_id)
                if current is None or NodeRunStatus(current["status"]) is not NodeRunStatus.SUCCEEDED:
                    continue
                exact_runs[str(current["node_id"])] = current
                pending.extend(consumptions_by_consumer.get(current_id, ()))

            def fallback_success(node_id: str) -> Mapping[str, Any] | None:
                candidates = [
                    node
                    for node in node_runs
                    if str(node.get("node_id")) == node_id
                    and NodeRunStatus(node["status"]) is NodeRunStatus.SUCCEEDED
                    and int(node.get("iteration") or 1) <= int(checkpoint.get("iteration") or 1)
                ]
                if not candidates:
                    return None
                return sorted(
                    candidates,
                    key=lambda node: (
                        int(node.get("iteration") or 1),
                        int(node.get("attempt") or 1),
                        str(node.get("created_at") or ""),
                    ),
                )[-1]

            reused = {
                node_id: exact_runs.get(node_id) or fallback_success(node_id) for node_id in reusable_ids
            }
            missing = sorted(
                node_id
                for node_id, node in reused.items()
                if node is None or NodeRunStatus(node["status"]) is not NodeRunStatus.SUCCEEDED
            )
            if missing:
                raise WorkflowContractError(
                    WorkflowErrorCode.RUN_NOT_RECOVERABLE,
                    "checkpoint has incomplete successful dependencies: " + ", ".join(missing),
                )
            branch = self.run_store.create_run(
                workflow_version_id=source_run["workflow_version_id"],
                session_id=session_id,
                graph=version["graph"],
                inputs=source_run.get("inputs") or {},
                started_by=started_by or "checkpoint_fork",
            )
            try:
                seeded = self.run_store.seed_checkpoint_nodes(
                    branch["id"], source_run_id=run_id, source_nodes=reused
                )
            except WorkflowRunStoreError as exc:
                raise WorkflowContractError(
                    WorkflowErrorCode.IDEMPOTENCY_CONFLICT,
                    "unable to seed workflow checkpoint branch",
                ) from exc
            if not seeded:
                raise WorkflowContractError(
                    WorkflowErrorCode.IDEMPOTENCY_CONFLICT,
                    "unable to seed workflow checkpoint branch",
                )
            self.run_store.transition_run(branch["id"], RunStatus.RUNNING)
        return self.advance(branch["id"])

    def cancel(self, run_id: str) -> dict[str, Any]:
        run = self.run_store.get_run(run_id)
        if run is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow run not found: {run_id}",
            )
        if RunStatus(run["status"]) in RUN_TERMINAL_STATUSES:
            return self.detail(run_id)
        if run["status"] != RunStatus.CANCELING.value:
            self.run_store.transition_run(run_id, RunStatus.CANCELING)
        return self.advance(run_id)

    def _advance_canceling(self, run_id: str) -> None:
        for node_run in self.run_store.list_node_runs(run_id):
            status = NodeRunStatus(node_run["status"])
            if status in NODE_RUN_TERMINAL_STATUSES:
                continue
            if node_run["job_id"]:
                self.job_runner.cancel(node_run["job_id"])
                job = self.job_runner.get_status(node_run["job_id"])
                if job and job["status"] == STATUS_CANCELED:
                    self.run_store.transition_node(
                        node_run["id"],
                        NodeRunStatus.CANCELED,
                    )
            elif status in {
                NodeRunStatus.PENDING,
                NodeRunStatus.READY,
                NodeRunStatus.QUEUED,
            }:
                self.run_store.transition_node(
                    node_run["id"],
                    NodeRunStatus.CANCELED,
                )
        nodes = self.run_store.list_node_runs(run_id)
        if all(NodeRunStatus(item["status"]) in NODE_RUN_TERMINAL_STATUSES for item in nodes):
            self.run_store.transition_run(run_id, RunStatus.CANCELED)
            self._notify_run_terminal(run_id)

    def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        decision: str,
        decided_by: str = "",
        comment: str = "",
        comments: Mapping[str, Any] | None = None,
        revised_outputs: Mapping[str, Any] | None = None,
        revised_summary: str = "",
    ) -> dict[str, Any]:
        with self._run_lock(run_id):
            normalized = str(decision or "").strip().lower()
            canonical = {
                "approved": "approve",
                "continue": "approve",
                "approved_with_changes": "approve_with_changes",
                "reject": "reject_and_stop",
                "rejected": "reject_and_stop",
                "fail": "reject_and_stop",
                "stop": "reject_and_stop",
                "rework": "reject_and_retry",
                "retry": "reject_and_retry",
                "redo": "reject_and_retry",
            }.get(normalized, normalized)
            if canonical not in {
                "approve",
                "approve_with_changes",
                "reject_and_retry",
                "reject_and_stop",
            }:
                raise WorkflowContractError(
                    WorkflowErrorCode.GRAPH_INVALID,
                    "approval decision must be approve, approve_with_changes, reject_and_retry, or reject_and_stop",
                )
            approval = self.run_store.get_approval(approval_id)
            if approval is None or approval["run_id"] != run_id:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND,
                    f"workflow approval not found: {approval_id}",
                )
            if approval["status"] != "pending":
                raise WorkflowContractError(
                    WorkflowErrorCode.APPROVAL_ALREADY_DECIDED,
                    "workflow approval already decided",
                )
            node_run = self.run_store.get_node_run(approval["node_run_id"])
            if node_run is None:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND,
                    f"workflow node run not found: {approval['node_run_id']}",
                )
            revised_manifest_id = ""
            run = self.run_store.get_run(run_id)
            if run is None:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND,
                    f"workflow run not found: {run_id}",
                )
            version = self.workflow_store.get_version(run["workflow_version_id"])
            if version is None:
                raise WorkflowContractError(
                    WorkflowErrorCode.RESOURCE_NOT_FOUND,
                    "published workflow version is missing",
                )
            node_definition = next(
                (
                    item
                    for item in version["graph"].get("nodes", [])
                    if str(item.get("node_id")) == str(node_run["node_id"])
                ),
                {},
            )
            if (
                canonical in {"approve", "approve_with_changes"}
                and node_definition.get("type") == "verifier"
                and str((node_run.get("output") or {}).get("decision") or "") != "pass"
            ):
                raise WorkflowContractError(
                    WorkflowErrorCode.GRAPH_INVALID,
                    "a verifier must return decision=pass before its approval can authorize a side effect",
                )
            if canonical == "reject_and_retry":
                iteration_limit = self._retry_iteration_limit(version["graph"], str(node_run["node_id"]))
                if int(node_run["iteration"]) >= iteration_limit:
                    raise WorkflowContractError(
                        WorkflowErrorCode.ITERATION_LIMIT_REACHED,
                        f"workflow node {node_run['node_id']} reached max_iterations={iteration_limit}",
                    )
                retry_node_ids = self._retry_iteration_nodes(version["graph"], str(node_run["node_id"]))
                if not self._node_run_budget_allows(run_id, version["graph"], additional=len(retry_node_ids)):
                    raise WorkflowContractError(
                        WorkflowErrorCode.NODE_RUN_LIMIT_REACHED,
                        NODE_RUN_BUDGET_ERROR,
                    )
            if canonical == "approve_with_changes":
                if not isinstance(revised_outputs, Mapping) or not revised_outputs:
                    raise WorkflowContractError(
                        WorkflowErrorCode.GRAPH_INVALID,
                        "approve_with_changes requires revised_outputs object",
                    )
                try:
                    revised_manifest_id = (
                        self.run_store.create_revised_node_manifest(
                            node_run["id"],
                            values=revised_outputs,
                            summary=revised_summary,
                        )
                        or ""
                    )
                except WorkflowRunStoreError as exc:
                    raise WorkflowContractError(
                        WorkflowErrorCode.GRAPH_INVALID,
                        str(exc),
                    ) from exc
            try:
                approval = self.run_store.decide_approval(
                    approval_id,
                    decision=canonical,
                    decided_by=decided_by,
                    comment=comment,
                    comments=comments,
                    revised_artifact_manifest_id=revised_manifest_id,
                )
            except WorkflowRunStoreError as exc:
                raise WorkflowContractError(
                    WorkflowErrorCode.APPROVAL_ALREADY_DECIDED,
                    str(exc),
                ) from exc
            if canonical in {"approve", "approve_with_changes"}:
                self.run_store.transition_node(node_run["id"], NodeRunStatus.SUCCEEDED)
            elif canonical == "reject_and_stop":
                node = next(
                    (
                        item
                        for item in version["graph"].get("nodes", [])
                        if str(item.get("node_id")) == str(node_run["node_id"])
                    ),
                    {},
                )
                target_status = (
                    NodeRunStatus.SKIPPED
                    if str(node.get("on_reject") or "fail_run") == "close_branch"
                    else NodeRunStatus.FAILED
                )
                self.run_store.transition_node(
                    node_run["id"],
                    target_status,
                    error=comment or "approval rejected",
                )
            elif canonical == "reject_and_retry":
                self.run_store.transition_node(
                    node_run["id"],
                    NodeRunStatus.REJECTED,
                    error=comment or "approval requested rework",
                )
                latest_by_node = self._latest_by_node(self.run_store.list_node_runs(run_id))
                definitions = {str(item.get("node_id")): item for item in version["graph"].get("nodes", [])}
                for retry_node_id in self._retry_iteration_nodes(version["graph"], str(node_run["node_id"])):
                    retry_node = latest_by_node.get(retry_node_id)
                    if retry_node:
                        self.run_store.create_retry_iteration(
                            retry_node["id"],
                            max_iteration=iteration_limit,
                            input_overrides=(
                                {"revision_request": comment.strip()}
                                if comment.strip()
                                and (
                                    retry_node_id == str(node_run["node_id"])
                                    or "revision_request"
                                    in set(definitions.get(retry_node_id, {}).get("input_contract") or [])
                                )
                                else None
                            ),
                        )

            run = self.run_store.get_run(run_id)
            if run and run["status"] == RunStatus.WAITING_APPROVAL.value:
                pending = [
                    item for item in self.run_store.list_approvals(run_id) if item["status"] == "pending"
                ]
                if not pending:
                    self.run_store.transition_run(run_id, RunStatus.RUNNING)
            return self.advance(run_id)

    def _settle_run(self, run_id: str) -> None:
        run = self.run_store.get_run(run_id)
        if run is None or run["status"] != RunStatus.RUNNING.value:
            return
        nodes = list(self._latest_by_node(self.run_store.list_node_runs(run_id)).values())
        states = [NodeRunStatus(item["status"]) for item in nodes]
        if not states or not all(state in NODE_RUN_TERMINAL_STATUSES for state in states):
            return
        if any(state in {NodeRunStatus.FAILED, NodeRunStatus.REJECTED} for state in states):
            budget_exceeded = any(
                state is NodeRunStatus.FAILED and NODE_RUN_BUDGET_ERROR in str(item.get("error") or "")
                for item, state in zip(nodes, states)
            )
            restart_replay_blocked = any(
                state is NodeRunStatus.FAILED
                and RESTART_REPLAY_BLOCKED_ERROR in str(item.get("error") or "")
                for item, state in zip(nodes, states)
            )
            if budget_exceeded:
                failure_code = WorkflowErrorCode.NODE_RUN_LIMIT_REACHED.value
                failure_message = NODE_RUN_BUDGET_ERROR
            elif restart_replay_blocked:
                failure_code = WorkflowErrorCode.RESTART_REPLAY_BLOCKED.value
                failure_message = RESTART_REPLAY_BLOCKED_ERROR
            elif any(state is NodeRunStatus.REJECTED for state in states):
                failure_code = "workflow_node_rejected"
                failure_message = "one or more workflow nodes were rejected"
            else:
                failure_code = "workflow_node_failed"
                failure_message = "one or more workflow nodes failed"
            self.run_store.transition_run(
                run_id,
                RunStatus.FAILED,
                failure_code=failure_code,
                failure_message=failure_message,
            )
        elif any(state is NodeRunStatus.CANCELED for state in states):
            self.run_store.transition_run(run_id, RunStatus.CANCELED)
        else:
            detail = self.detail(run_id)
            required_outputs = set((detail.get("output_schema") or {}).get("required") or [])
            missing_outputs = sorted(required_outputs - set(detail.get("outputs") or {}))
            if missing_outputs:
                self.run_store.transition_run(
                    run_id,
                    RunStatus.FAILED,
                    failure_code="workflow_final_output_missing",
                    failure_message="workflow final outputs missing: " + ", ".join(missing_outputs),
                )
            else:
                self.run_store.transition_run(run_id, RunStatus.SUCCEEDED)
        self._notify_run_terminal(run_id)
