"""Process-local owner for durable Workflow schedulers and node executors."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping

from data.workflow_run_store import WorkflowRunStore, WorkflowRunStoreError
from data.workflow_store import WorkflowStore, WorkflowStoreError
from data.workspace import workspace_manager

from .models import WorkflowContractError, WorkflowErrorCode
from .pricing import lookup_model_price
from .scheduler import WorkflowScheduler


def _bounded_materials(materials: Mapping[str, Any], limit: int = 100000) -> str:
    text = json.dumps(materials, ensure_ascii=False, default=str, indent=2)
    if len(text) <= limit:
        return text
    # Passing a partial evidence bundle to an LLM node is worse than failing:
    # downstream conclusions could look complete while being based on silently
    # missing inputs.  GE-3 will replace this boundary with Artifact readers;
    # until then fail explicitly so the Run remains recoverable and auditable.
    raise WorkflowContractError(
        WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
        "workflow node materials exceed 100000 characters; "
        "pass a bounded Artifact reference instead of inline content",
    )


class WorkflowRuntime:
    def __init__(self, session_id: str):
        from api.state import session_manager

        runtime = workspace_manager.workflow_runtime_for_session(session_id)
        self.session_id = session_id
        self.workspace = runtime
        self.session = session_manager.get_or_create(session_id)
        db_path = runtime.meta_dir / "workflows.sqlite3"
        self.workflow_store = WorkflowStore(db_path, runtime.workspace_id)
        self.run_store = WorkflowRunStore(db_path, runtime.workspace_id)
        self.scheduler = WorkflowScheduler(
            workflow_store=self.workflow_store,
            run_store=self.run_store,
            job_runner=self.session.job_runner,
            executor=self._execute_node,
            preflight=self._preflight_node,
            graph_preflight=self._preflight_graph,
            on_run_terminal=self._finalize_workflow_artifact_cost,
        )
        # A fresh process has a new executor pool. Reconcile durable runs
        # after the JobsStore has closed callbacks left by the old process;
        # paused/approval-waiting runs remain untouched by this call.
        self.recovered_workflow_runs = self.scheduler.recover_interrupted_runs(
            session_id=self.session_id,
        )

    def _finalize_workflow_artifact_cost(self, run_id: str, status: str) -> None:
        """Refresh every export from this Run after its final node settles."""
        from infrastructure.artifact_lifecycle import update_artifact_run_usage

        try:
            update_artifact_run_usage(
                session_id=self.session_id,
                run_id=run_id,
                cost=self._workflow_run_cost(run_id, status=status),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.run_store.record_event(
                run_id,
                "workflow_artifact_cost_update_failed",
                {"run_id": run_id, "status": status, "error": str(exc)},
            )

    def _preflight_node(self, node: Mapping[str, Any]) -> None:
        """Reject disallowed side effects before a workflow Job is created."""
        side_effects = set(node.get("side_effects") or ())
        denied = side_effects & {"write_data", "export_file", "network"}
        if self.workspace.permission != "read_write" and denied:
            raise WorkflowContractError(
                WorkflowErrorCode.PERMISSION_DENIED,
                "read-only workspace cannot execute node side effects: " + ", ".join(sorted(denied)),
            )
        if self._requires_cost_pricing(node):
            self._require_known_model_price(node)

    @staticmethod
    def _node_limits(node: Mapping[str, Any]) -> Mapping[str, Any]:
        limits = node.get("limits") or {}
        return limits if isinstance(limits, Mapping) else {}

    def _effective_model(self, node: Mapping[str, Any]) -> tuple[str, str]:
        from LLM.llm_config_manager import get_config_manager

        manager = get_config_manager()
        provider = str(getattr(self.session, "model_provider", "") or "").strip()
        if not provider:
            provider = str(manager.get_default_provider() or "").strip()
        selectable = getattr(manager, "is_selectable_provider", None)
        config = (
            manager.get_config(provider)
            if provider and (selectable(provider) if callable(selectable) else True)
            else None
        )
        model = str(getattr(config, "model", "") or "").strip()
        profile_id = str(node.get("agent_profile_id") or "").strip()
        profile = self.workflow_store.get_agent_profile(profile_id) if profile_id else None
        model_policy = str((profile or {}).get("model_policy") or "inherit").strip()
        if model_policy and model_policy != "inherit":
            model = model_policy
        return provider, model

    def _requires_cost_pricing(self, node: Mapping[str, Any]) -> bool:
        if str(node.get("type") or "").strip() not in {"agent", "verifier"}:
            return False
        node_limits = self._node_limits(node)
        graph_limits = node.get("__pfs_workflow_graph_limits__") or {}
        if not isinstance(graph_limits, Mapping):
            graph_limits = {}
        return (
            node_limits.get("max_cost_usd") is not None or graph_limits.get("max_total_cost_usd") is not None
        )

    def _require_known_model_price(self, node: Mapping[str, Any]) -> None:
        provider, model = self._effective_model(node)
        if not provider or not model:
            # The normal model configuration guard owns this error path. Do
            # not hide a missing model behind a pricing error.
            return
        if lookup_model_price(provider, model) is not None:
            return
        raise WorkflowContractError(
            WorkflowErrorCode.UNKNOWN_PRICE,
            "Workflow 已启用费用上限，但当前模型未配置完整输入/输出单价；"
            "请在模型设置中补齐价格后再创建 Run。",
        )

    def _preflight_graph(self, graph: Mapping[str, Any]) -> None:
        """Fail before Run creation when a hard cost budget is unverifiable."""
        limits = graph.get("limits") or {}
        if not isinstance(limits, Mapping):
            return
        nodes = graph.get("nodes") or []
        for raw_node in nodes:
            if not isinstance(raw_node, Mapping):
                continue
            node = dict(raw_node)
            node["__pfs_workflow_graph_limits__"] = dict(limits)
            if self._requires_cost_pricing(node):
                self._require_known_model_price(node)

    def _workspace_persistent_source(self):
        """Return this run's durable workspace source, never an in-memory upload.

        A Workflow may read from several interactive session sources. Controlled
        cleaning is different: its approved output must survive the session, so
        it may write only to the mounted workspace's persistent DuckDB source.
        """
        expected_path = Path(self.workspace.db_path).resolve(strict=False)
        for entry in getattr(self.session, "_sources", []):
            source = entry.get("source") if isinstance(entry, Mapping) else None
            raw_path = getattr(source, "_db_path", None)
            if raw_path and Path(raw_path).resolve(strict=False) == expected_path:
                return source
        raise WorkflowContractError(
            WorkflowErrorCode.PERMISSION_DENIED,
            "controlled cleaning requires the mounted workspace persistent data source",
        )

    @staticmethod
    def _tool_event_for_cleaning(result: Mapping[str, Any]) -> Mapping[str, Any]:
        events = [
            event
            for event in (result.get("tool_events") or [])
            if isinstance(event, Mapping) and event.get("tool") == "clean_data"
        ]
        if len(events) != 1:
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "controlled cleaning must call clean_data exactly once",
            )
        event = events[0]
        tool_text = str(event.get("result") or "")
        if (
            event.get("status") != "ok"
            or "✅ 清洗结果已保存为表 `cleaned_data`" not in tool_text
            or "❌" in tool_text
            or "⚠️" in tool_text
        ):
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "clean_data did not confirm a successful cleaned_data write: "
                + (tool_text[:500] or "no tool result"),
            )
        return event

    def _verified_cleaning_execution(
        self,
        agent,
        result: Mapping[str, Any],
    ) -> str:
        """Build execution evidence from the tool result and durable database."""
        event = self._tool_event_for_cleaning(result)
        source = agent.data_source
        list_tables = getattr(source, "list_tables", None)
        if not callable(list_tables) or "cleaned_data" not in set(list_tables() or []):
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "clean_data returned success but cleaned_data is absent from the workspace database",
            )
        frame, error = source.execute_query('SELECT COUNT(*) AS row_count FROM "cleaned_data"')
        if error or frame is None or frame.empty:
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "cleaned_data exists but could not be read back: " + str(error or "empty result"),
            )
        row_count = int(frame.iloc[0]["row_count"])
        return (
            "## cleaning_execution（已核验）\n\n"
            "- 结果表：`cleaned_data`\n"
            f"- 数据库回读行数：{row_count}\n"
            "- 工具回执：\n\n" + str(event["result"]).strip()
        )

    def _execute_node(self, node: dict, materials: dict, _ctx) -> dict[str, Any]:
        # Keep the guard in the executor as defense in depth; normal dispatch
        # is rejected by _preflight_node before it creates a Job.
        self._preflight_node(node)
        if node.get("type") == "validation":
            return self._execute_validation_node(node, materials)
        if node.get("type") == "router":
            return self._execute_router_node(node, materials)
        if node.get("type") == "sql":
            return self._execute_sql_node(node)
        if node.get("type") == "export":
            return self._execute_export_node(node, materials)

        from api.chat import _build_agent

        profile = self.workflow_store.get_agent_profile(node["agent_profile_id"])
        if profile is None:
            raise RuntimeError(f"agent profile not found: {node['agent_profile_id']}")
        is_controlled_cleaning = "write_data" in set(node.get("side_effects") or ())
        agent = _build_agent(
            self.session,
            workspace_id=self.workspace.workspace_id,
        )
        if is_controlled_cleaning:
            persistent_source = self._workspace_persistent_source()
            agent.set_data_source(persistent_source)
            agent._all_sources = [persistent_source]
            agent._merged_source = None
            agent._combined_schema = persistent_source.get_schema()
        model_policy = str(profile.get("model_policy") or "inherit").strip()
        if model_policy and model_policy != "inherit":
            # A Workflow profile is an immutable capability revision; honor its
            # model selection without changing the interactive session default.
            agent.model = model_policy
        output_names = list(node.get("output_contract") or [])
        node_limits = dict(node.get("limits") or {})
        verifier_config = dict(node.get("verifier") or {})
        verifier_instruction = ""
        if node.get("type") == "verifier":
            standards = "\n".join(f"- {item}" for item in verifier_config.get("standards", []))
            verifier_instruction = (
                "You are an independent verifier. Do not rewrite the deliverable. "
                "Assess only the supplied materials against these acceptance standards:\n"
                + standards
                + "\nReturn JSON only with keys: decision (pass|rework|escalate), "
                "issues (string array), evidence (string array).\n\n"
            )
        operating_report_instruction = ""
        if "operating_report" in output_names:
            operating_report_instruction = (
                "The final operating_report is a complete management operating-analysis report, not a technical audit and not a short memo. "
                "Write in Chinese. Target 1,800–3,000 Chinese characters when evidence permits; even when data is blocked, provide a "
                "substantive restricted report of at least 1,200 characters using only defensible business implications. Use these sections: "
                "# 经营分析报告; ## 一、管理摘要; ## 二、经营表现与关键指标; ## 三、结构表现与业务驱动; "
                "## 四、问题、风险与数据可信度; ## 五、管理建议与行动计划. "
                "Use compact tables for validated KPI comparisons or action owners when useful. Translate findings into management language: "
                "what happened, why it matters, affected scope, and what decision or action follows. If a metric's unit or source is "
                "unreliable, do not invent a value; label that metric as temporarily unpublished, still analyze unaffected structural signals, "
                "and explain the decision impact in one concise data-trust subsection. Never include artifact IDs, preview/truncation notes, "
                "source-table inventories, raw SQL, raw calculations, or a field-by-field technical audit. Detailed evidence remains in the "
                "verification_report node audit trail.\n\n"
            )
        if "cleaning_execution" in materials:
            operating_report_instruction = (
                "This is a controlled data-cleaning completion report. Write in Chinese and begin exactly with "
                "# 数据清洗执行报告. Clearly distinguish: ## 一、审批授权的方案; ## 二、实际执行结果; "
                "## 三、结果表与影响范围; ## 四、未执行或失败项; ## 五、后续建议. "
                "State only operations evidenced by cleaning_execution; do not claim the source table was overwritten. "
                "Name the derived cleaned table, the actual operation summary, and any failed or skipped step.\n\n"
            )
        requires_json_output = node.get("type") == "verifier" or len(output_names) > 1
        structured_output_instruction = ""
        if len(output_names) > 1 and node.get("type") != "verifier":
            structured_output_instruction = (
                "Return raw valid JSON only: no prose before or after it, no Markdown, "
                "and no code fence. Its top-level object must contain exactly these "
                "output names, each with its own complete value: "
                + ", ".join(output_names)
                + ". Do not duplicate one combined response under multiple keys.\n\n"
            )
        if requires_json_output:
            response_format_instruction = (
                "The response must be raw valid JSON only. Do not add Markdown, explanation, "
                "or a code fence.\n\n"
            )
        else:
            response_format_instruction = (
                "Use normal Markdown for prose, headings and lists; never wrap the entire "
                "response in a fenced code block.\n\n"
            )
        language_instruction = (
            "All user-facing output must be written in Simplified Chinese, including reports, "
            "analysis, findings, issues, evidence, and recommendations. Preserve only required "
            "JSON keys, artifact keys, SQL, field names, and identifiers exactly as supplied.\n\n"
        )
        prompt = (
            f"Workflow node: {node['node_id']}\n"
            f"Required outputs: {', '.join(output_names) or 'result'}\n\n"
            + verifier_instruction
            + structured_output_instruction
            + operating_report_instruction
            + language_instruction
            + "Complete only this workflow node using the supplied materials. "
            "Return a concise, evidence-based result. Do not change the workflow. "
            + response_format_instruction
            + "Materials:\n"
            + _bounded_materials(materials)
        )
        result = agent._run_delegated_llm(
            member={
                "role": profile["role"],
                "instructions": profile["instructions"],
            },
            prompt=prompt,
            timeout_seconds=int(node_limits.get("max_run_seconds") or 300),
            max_tokens=int(node_limits.get("max_tokens") or 2000),
            max_tool_calls=node_limits.get("max_tool_calls"),
            max_total_tokens=node_limits.get("max_total_tokens"),
            max_cost_usd=node_limits.get("max_cost_usd"),
            allowed_tools=frozenset(profile.get("allowed_tools") or ()),
            allow_write_tools="write_data" in set(node.get("side_effects") or ()),
        )
        content = str(result.get("content") or "").strip()
        try:
            if is_controlled_cleaning:
                if output_names != ["cleaning_execution"]:
                    raise WorkflowContractError(
                        WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                        "controlled cleaning nodes must declare only cleaning_execution",
                    )
                outputs = {
                    "cleaning_execution": self._verified_cleaning_execution(agent, result),
                }
            elif node.get("type") == "verifier":
                parsed_verifier = self._parse_verifier_result(content)
                # A verifier's decision is a structured Artifact. Support both
                # the explicit decision/issues/evidence contract and a single
                # named verification Artifact without losing the decision fields.
                outputs = (
                    {output_names[0]: dict(parsed_verifier)}
                    if len(output_names) == 1
                    else {output_name: parsed_verifier.get(output_name) for output_name in output_names}
                    or dict(parsed_verifier)
                )
            else:
                outputs = self._parse_agent_outputs(content, output_names)
            self._validate_node_outputs(node, outputs)
        except WorkflowContractError as exc:
            # Published templates before 2026-08-04 exposed the inspection
            # result as two independent outputs. Some providers reliably
            # return a normal Markdown inspection despite the JSON request.
            # Keep those immutable versions executable by normalizing that
            # legacy shape; newly created templates use one inspection report.
            if (
                str(node.get("node_id")) == "inspect_data"
                and output_names == ["data_quality_report", "metric_scope"]
                and content
            ):
                outputs = {
                    "data_quality_report": content,
                    "metric_scope": {
                        "legacy_source": "data_quality_report",
                        "inspection_report": content,
                    },
                }
            else:
                # Contract failures are normal, auditable node failures. Returning
                # a stable result lets the scheduler persist usage, mark the node
                # failed, skip blocked descendants, and settle the Run immediately
                # instead of depending on an exception callback to do that work.
                outputs = {"__workflow_output_error__": str(exc)}
        usage = result.get("usage")
        if isinstance(usage, Mapping):
            outputs["__workflow_usage__"] = dict(usage)
        return outputs

    @staticmethod
    def _parse_agent_outputs(content: str, output_names: list[str]) -> dict[str, Any]:
        """Keep each declared Agent output independently addressable.

        A multi-output node is an Artifact interface, not a prompt convention:
        accepting prose here would recreate the old behaviour that copied one
        long response to every output name.
        """
        if not output_names:
            return {"result": content}
        if len(output_names) == 1:
            return {output_names[0]: content}
        raw = str(content or "").strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
        if fenced:
            raw = fenced.group(1).strip()
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "multi-output agent node must return a JSON object keyed by its output contract",
            ) from exc
        if not isinstance(parsed, Mapping):
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "multi-output agent node must return a JSON object",
            )
        expected = set(output_names)
        actual = set(parsed)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if extra:
                details.append("undeclared " + ", ".join(extra))
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "multi-output agent result keys do not match output contract: " + "; ".join(details),
            )
        return {name: parsed[name] for name in output_names}

    @staticmethod
    def _validate_node_outputs(node: Mapping[str, Any], outputs: Mapping[str, Any]) -> None:
        """Reject placeholder deliverables that merely describe missing inputs."""
        blocking_markers = (
            "工具调用已达到上限",
            "未能生成完整最终总结",
            "tool calls have reached the limit",
            "tool call limit reached",
            "failed / empty",
        )
        forbidden = [
            str(item).lower()
            for item in dict(node.get("output_validation") or {}).get("forbidden_substrings", [])
        ]
        for name in node.get("output_contract") or []:
            value = outputs.get(name)
            if value is None or not str(value).strip():
                raise WorkflowContractError(
                    WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                    f"workflow node {node['node_id']} produced an empty required output: {name}",
                )
            text = str(value).lower()
            match = next((item for item in (*blocking_markers, *forbidden) if item in text), "")
            if match:
                raise WorkflowContractError(
                    WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                    f"workflow node {node['node_id']} produced a placeholder output containing: {match}",
                )

    @staticmethod
    def _parse_verifier_result(content: str) -> dict[str, Any]:
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError) as exc:
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "verifier must return a JSON decision",
            ) from exc
        if not isinstance(parsed, Mapping) or parsed.get("decision") not in {
            "pass",
            "rework",
            "escalate",
        }:
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "verifier decision must be pass, rework, or escalate",
            )
        for key in ("issues", "evidence"):
            if not isinstance(parsed.get(key), list) or any(
                not isinstance(item, str) for item in parsed[key]
            ):
                raise WorkflowContractError(
                    WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                    f"verifier {key} must be a string array",
                )
        return dict(parsed)

    @staticmethod
    def _execute_validation_node(node: Mapping[str, Any], materials: Mapping[str, Any]) -> dict[str, Any]:
        """Run deterministic contract checks without an LLM or tool access."""
        config = dict(node.get("validation") or {})
        missing = [key for key in config.get("required", []) if key not in materials]
        empty = [
            key
            for key in config.get("non_empty", [])
            if key in materials and materials[key] in (None, "", [], {})
        ]
        wrong_types = [
            key
            for key, expected in dict(config.get("field_types") or {}).items()
            if key in materials and type(materials[key]).__name__ != str(expected)
        ]
        too_small = [
            key
            for key, minimum in dict(config.get("min_items") or {}).items()
            if key in materials and hasattr(materials[key], "__len__") and len(materials[key]) < int(minimum)
        ]
        too_large = [
            key
            for key, maximum in dict(config.get("max_items") or {}).items()
            if key in materials and hasattr(materials[key], "__len__") and len(materials[key]) > int(maximum)
        ]
        unequal = [
            key
            for key, expected in dict(config.get("equals") or {}).items()
            if key in materials and materials[key] != expected
        ]
        if missing or empty or wrong_types or too_small or too_large or unequal:
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if empty:
                details.append("empty " + ", ".join(empty))
            if wrong_types:
                details.append("wrong type " + ", ".join(wrong_types))
            if too_small:
                details.append("below min_items " + ", ".join(too_small))
            if too_large:
                details.append("above max_items " + ", ".join(too_large))
            if unequal:
                details.append("does not equal " + ", ".join(unequal))
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                "validation failed: " + "; ".join(details),
            )
        result = {
            "passed": True,
            "checked": {
                "required": list(config.get("required", [])),
                "non_empty": list(config.get("non_empty", [])),
                "field_types": dict(config.get("field_types") or {}),
                "min_items": dict(config.get("min_items") or {}),
                "max_items": dict(config.get("max_items") or {}),
                "equals": dict(config.get("equals") or {}),
            },
            "quality": {"status": "passed", "checks": ["workflow_validation"]},
        }
        output_names = list(node.get("output_contract") or [])
        return {name: result for name in output_names} or {"validation": result}

    @staticmethod
    def _execute_router_node(node: Mapping[str, Any], materials: Mapping[str, Any]) -> dict[str, Any]:
        config = dict(node.get("router") or {})
        input_name = str(config.get("input") or "")
        value = materials.get(input_name, config.get("default"))
        if isinstance(value, Mapping) and "route" in value:
            value = value["route"]
        if value is None:
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                f"router input is missing: {input_name}",
            )
        output_names = list(node.get("output_contract") or [])
        return {name: value for name in output_names} or {"route": value}

    def _execute_sql_node(self, node: Mapping[str, Any]) -> dict[str, Any]:
        """Execute a declared read-only query after the shared AST validation."""
        from agent.validate import validate_tool_args
        from api.chat import _build_agent

        sql = str(node.get("sql") or "").strip()
        error = validate_tool_args(
            "query_data",
            {"sql": sql},
            workspace_authorization=workspace_manager.path_authorization(self.workspace.workspace_id),
        )
        if error:
            raise WorkflowContractError(WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION, error)
        agent = _build_agent(self.session, workspace_id=self.workspace.workspace_id)
        text, refs = agent._tool_query_data_with_refs(sql)
        if text.startswith("SQL Error:") or text.startswith("No data source"):
            raise RuntimeError(text)
        result = {"sql": sql, "result": text, "sources": refs}
        result["evidence"] = list(refs or [])
        result["quality"] = {"status": "passed", "checks": ["shared_sql_validation"]}
        output_names = list(node.get("output_contract") or [])
        return {name: result for name in output_names} or {"query_result": result}

    def _execute_export_node(
        self,
        node: Mapping[str, Any],
        materials: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Export an already-produced material without giving an LLM write access."""
        config = dict(node.get("export") or {})
        source = str(config.get("source") or "")
        if source not in materials:
            raise WorkflowContractError(
                WorkflowErrorCode.OUTPUT_CONTRACT_VIOLATION,
                f"export source is missing: {source}",
            )
        export_format = str(config.get("format") or "markdown")
        extensions = {"markdown": ".md", "json": ".json", "text": ".txt"}
        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", str(config.get("filename") or source)).strip("._")
        filename = (filename or "workflow_export") + extensions[export_format]
        value = materials[source]
        if export_format == "json":
            content = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        elif isinstance(value, str):
            content = value
        else:
            content = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

        execution_context = node.get("__pfs_workflow_context__") or {}
        run_id = str(execution_context.get("run_id") or "")
        node_run_id = str(execution_context.get("node_run_id") or "")
        node_id = str(execution_context.get("node_id") or node.get("node_id") or "")
        iteration = str(execution_context.get("iteration") or "1")
        operation_key = ""
        side_effect_acquired = False
        side_effect_store = getattr(self, "run_store", None)
        claim_side_effect = getattr(side_effect_store, "claim_side_effect", None)
        complete_side_effect = getattr(side_effect_store, "complete_side_effect", None)
        fail_side_effect = getattr(side_effect_store, "fail_side_effect", None)
        if run_id and node_run_id and callable(claim_side_effect):
            identity = {
                "effect_type": "export_file",
                "run_id": run_id,
                "node_id": node_id,
                "iteration": iteration,
                "filename": filename,
                "format": export_format,
            }
            identity_hash = hashlib.sha256(
                json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            request = {
                **identity,
                "content_sha256": content_sha256,
            }
            request_hash = hashlib.sha256(
                json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            operation_key = f"workflow-export:{run_id}:{node_id}:{iteration}:{identity_hash}"
            claim = claim_side_effect(
                operation_key=operation_key,
                run_id=run_id,
                node_id=node_id,
                node_run_id=node_run_id,
                effect_type="export_file",
                request_hash=request_hash,
            )
            cached = claim.get("result") if isinstance(claim, Mapping) else None
            if str(claim.get("status") or "") == "succeeded" and isinstance(cached, Mapping):
                cached_output = dict(cached)
                if self._cached_export_output_is_valid(cached_output, content_sha256):
                    return cached_output
                raise WorkflowContractError(
                    WorkflowErrorCode.RESTART_REPLAY_BLOCKED,
                    "workflow export was marked complete but its artifact is missing or changed; manual review required",
                )
            if not bool(claim.get("acquired")):
                raise WorkflowContractError(
                    WorkflowErrorCode.RESTART_REPLAY_BLOCKED,
                    "workflow export side effect is already claimed; manual review required before replay",
                )
            side_effect_acquired = True

        artifact_dir = Path(self.workspace.artifacts_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        target = artifact_dir / filename
        target = target.with_name(target.name[:160])
        if target.exists():
            try:
                existing_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
            except OSError:
                existing_sha256 = ""
            if existing_sha256 != content_sha256:
                token = re.sub(
                    r"[^A-Za-z0-9_-]+",
                    "_",
                    f"{run_id[-12:]}_{node_id}_{iteration}",
                ).strip("_")
                token = token or content_sha256[:12]
                target = target.with_name(f"{target.stem[:100]}__{token[:40]}{target.suffix}")

        temp_path: Path | None = None
        try:
            # A same-directory temporary file plus replace prevents a reader
            # from observing a half-written report and keeps a retry from
            # appending to an already visible partial artifact.
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(target.parent),
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, target)
            temp_path = None
        except Exception as exc:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if side_effect_acquired and callable(fail_side_effect):
                fail_side_effect(operation_key, str(exc))
            raise

        from infrastructure.artifact_lifecycle import (
            register_artifact,
            update_artifact_run_usage,
        )

        metadata = {
            "workflow_node_run_id": node_run_id,
            "content_sha256": content_sha256,
        }
        if run_id:
            metadata["run_id"] = run_id
            metadata["workflow_run_id"] = run_id
        if operation_key:
            metadata["workflow_operation_key"] = operation_key
        artifact_id = register_artifact(
            target,
            artifact_type="workflow_export",
            session_id=self.session_id,
            workspace_id=self.workspace.workspace_id,
            metadata=metadata,
        )
        result = {
            "artifact_id": artifact_id,
            "path": str(target),
            "filename": target.name,
            "uri": f"workspace://artifacts/{target.name}",
            "media_type": {"markdown": "text/markdown", "json": "application/json", "text": "text/plain"}[
                export_format
            ],
            "content_sha256": content_sha256,
            "evidence": [f"workflow material:{source}"],
            "quality": {"status": "passed", "checks": ["deterministic_export"]},
        }
        output_names = list(node.get("output_contract") or [])
        output = {name: result for name in output_names} or {"export": result}
        if side_effect_acquired and callable(complete_side_effect):
            if not complete_side_effect(operation_key, output):
                raise WorkflowContractError(
                    WorkflowErrorCode.RESTART_REPLAY_BLOCKED,
                    "workflow export result could not be durably committed; manual review required",
                )
        if run_id:
            update_artifact_run_usage(
                session_id=self.session_id,
                run_id=run_id,
                cost=self._workflow_run_cost(run_id, status="running"),
            )
        return output

    def _cached_export_output_is_valid(
        self,
        output: Mapping[str, Any],
        content_sha256: str,
    ) -> bool:
        """Verify a completed export before reusing it after a restart."""
        from infrastructure.artifact_lifecycle import resolve_registered_artifact_path

        values = list(output.values())
        if not values:
            return False
        for value in values:
            if not isinstance(value, Mapping):
                return False
            if str(value.get("content_sha256") or "") != content_sha256:
                return False
            artifact_id = str(value.get("artifact_id") or "")
            resolved = resolve_registered_artifact_path(artifact_id, session_id=self.session_id)
            if resolved is None:
                return False
            _metadata, path = resolved
            try:
                if hashlib.sha256(path.read_bytes()).hexdigest() != content_sha256:
                    return False
            except OSError:
                return False
        return True

    def _workflow_run_cost(
        self,
        run_id: str,
        *,
        status: str,
    ) -> dict[str, Any]:
        """Aggregate persisted Workflow model usage without inventing a price."""
        nodes = self.run_store.list_node_runs(run_id)
        measured = [
            node
            for node in nodes
            if int(node.get("model_calls") or 0)
            or int(node.get("input_tokens") or 0)
            or int(node.get("output_tokens") or 0)
        ]
        unknown_price = bool(measured) and any(node.get("cost_usd") is None for node in measured)
        if not measured:
            amount = None
            estimated = None
            source = "provider_usage_unavailable"
        elif unknown_price:
            amount = None
            estimated = None
            source = "provider_usage_price_unknown"
        else:
            amount = round(sum(float(node["cost_usd"]) for node in measured), 8)
            estimated = True
            source = "provider_usage_configured_pricing"
        return {
            "amount": amount,
            "currency": "USD",
            "estimated": estimated,
            "source": source,
            "model_calls": sum(int(node.get("model_calls") or 0) for node in measured),
            "input_tokens": sum(int(node.get("input_tokens") or 0) for node in measured),
            "output_tokens": sum(int(node.get("output_tokens") or 0) for node in measured),
            "cached_input_tokens": sum(int(node.get("cached_input_tokens") or 0) for node in measured),
            "providers": sorted(
                {
                    str(node.get("provider_name") or "").strip()
                    for node in measured
                    if str(node.get("provider_name") or "").strip()
                }
            ),
            "models": sorted(
                {
                    str(node.get("model_name") or "").strip()
                    for node in measured
                    if str(node.get("model_name") or "").strip()
                }
            ),
            "status": status,
        }

    def delete_run(self, run_id: str) -> dict[str, Any]:
        if self.workspace.permission != "read_write":
            raise WorkflowContractError(
                WorkflowErrorCode.PERMISSION_DENIED,
                "workspace is mounted read-only",
            )
        run = self.run_store.get_run(run_id)
        if run is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow run not found: {run_id}",
            )
        if run["status"] not in {"canceled", "succeeded", "failed"}:
            raise WorkflowContractError(
                WorkflowErrorCode.VERSION_CONFLICT,
                "请先取消仍在运行的 Workflow Run",
            )
        job_ids = [
            str(node.get("job_id") or "")
            for node in self.run_store.list_node_runs(run_id)
            if str(node.get("job_id") or "")
        ]
        active_jobs = [
            job_id
            for job_id in job_ids
            if (self.session.job_runner.get_status_for_session(str(run["session_id"]), job_id) or {}).get(
                "status"
            )
            not in {None, "succeeded", "failed", "canceled"}
        ]
        if active_jobs:
            raise WorkflowContractError(
                WorkflowErrorCode.VERSION_CONFLICT,
                "请等待节点 Job 结束后再删除：" + ", ".join(active_jobs),
            )
        for job_id in job_ids:
            if str(run["session_id"]) == self.session_id:
                self.session.job_runner.remove_terminal_listeners(job_id)
        try:
            result = self.run_store.delete_run_cascade(run_id)
        except WorkflowRunStoreError as exc:
            raise WorkflowContractError(
                WorkflowErrorCode.VERSION_CONFLICT,
                str(exc),
            ) from exc
        if result is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow run not found: {run_id}",
            )
        deleted_jobs = self.session.job_runner.purge_terminal_for_session(
            result.pop("session_id"),
            result.pop("job_ids"),
        )
        result["deleted"]["jobs"] = deleted_jobs
        return result

    def delete_workflow(self, workflow_id: str) -> dict[str, Any]:
        if self.workspace.permission != "read_write":
            raise WorkflowContractError(
                WorkflowErrorCode.PERMISSION_DENIED,
                "workspace is mounted read-only",
            )
        plan = self.workflow_store.workflow_delete_plan(workflow_id)
        if plan is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow not found: {workflow_id}",
            )
        if plan["active_run_ids"]:
            raise WorkflowContractError(
                WorkflowErrorCode.VERSION_CONFLICT,
                "请先取消仍在运行的 Workflow Run：" + ", ".join(plan["active_run_ids"]),
            )
        active_jobs = []
        for session_id, job_ids in plan["jobs_by_session"].items():
            for job_id in job_ids:
                job = self.session.job_runner.get_status_for_session(session_id, job_id)
                if job and job.get("status") not in {"succeeded", "failed", "canceled"}:
                    active_jobs.append(job_id)
        if active_jobs:
            raise WorkflowContractError(
                WorkflowErrorCode.VERSION_CONFLICT,
                "请等待节点 Job 结束后再删除：" + ", ".join(active_jobs),
            )
        for job_id in plan["jobs_by_session"].get(self.session_id, []):
            self.session.job_runner.remove_terminal_listeners(job_id)
        try:
            result = self.workflow_store.delete_workflow_cascade(workflow_id)
        except WorkflowStoreError as exc:
            raise WorkflowContractError(
                WorkflowErrorCode.VERSION_CONFLICT,
                str(exc),
            ) from exc
        if result is None:
            raise WorkflowContractError(
                WorkflowErrorCode.RESOURCE_NOT_FOUND,
                f"workflow not found: {workflow_id}",
            )
        deleted_jobs = 0
        for session_id, job_ids in result.pop("jobs_by_session", {}).items():
            deleted_jobs += self.session.job_runner.purge_terminal_for_session(
                session_id,
                job_ids,
            )
        result["deleted"]["jobs"] = deleted_jobs
        return result

    def close(self) -> None:
        self.run_store.close()
        self.workflow_store.close()


class WorkflowRuntimeManager:
    """Keep callback-owning schedulers alive while their process is running."""

    def __init__(self):
        self._by_session: dict[str, WorkflowRuntime] = {}
        self._lock = threading.RLock()

    def get(self, session_id: str) -> WorkflowRuntime:
        with self._lock:
            current = self._by_session.get(session_id)
            workspace_id = workspace_manager.workflow_workspace_id_for_session(session_id)
            if current is not None and current.workspace.workspace_id == workspace_id:
                return current
            if current is not None:
                current.close()
            created = WorkflowRuntime(session_id)
            self._by_session[session_id] = created
            return created

    def close_session(self, session_id: str) -> None:
        with self._lock:
            runtime = self._by_session.pop(session_id, None)
        if runtime is not None:
            runtime.close()


workflow_runtime_manager = WorkflowRuntimeManager()
