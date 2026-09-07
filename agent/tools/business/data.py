# -*- coding: utf-8 -*-
"""Mixin: data-oriented tools (schema, query, analysis, chart, clean, profile)."""
import logging
import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Mapping

from agent.errors import AgentRunTimeout
from agent.jobs import JobCanceled

log = logging.getLogger(__name__)

_TIME_SERIES_PREFIX = "Time_Series_"
_JOB_ANALYSIS_IDS = {"Torch_MLP"}
_ANALYSIS_JOB_ROW_THRESHOLD = 1000
_CHART_JOB_ROW_THRESHOLD = 50_000
_PROFILE_JOB_ROW_THRESHOLD = 50_000
_PROFILE_JOB_COLUMN_THRESHOLD = 50
_CLEAN_JOB_ROW_THRESHOLD = 50_000
_QUERY_JOB_ROW_THRESHOLD = 100_000


def _run_interruptible(
    operation,
    interrupt,
    *,
    timeout: float | None = None,
    abort_check=None,
):
    """Run a blocking data-source operation with cooperative interruption.

    ``abort_check`` cannot be polled while a DuckDB/driver call is blocked, so
    the operation runs on a short-lived daemon thread and the owner thread
    watches the Agent budget.  Built-in sources expose an interrupt method;
    it is invoked before the cancellation/timeout exception is re-raised.
    The worker is joined briefly to avoid returning while the connection is
    still unwinding.
    """
    if timeout is None and abort_check is None:
        return operation()

    result: dict[str, Any] = {}

    def _worker() -> None:
        try:
            result["value"] = operation()
        except BaseException as exc:  # preserve cooperative exceptions
            result["error"] = exc

    worker = threading.Thread(
        target=_worker,
        name="pfs-data-query",
        daemon=True,
    )
    worker.start()
    deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))

    def _stop_query() -> None:
        try:
            interrupt()
        except Exception:
            log.warning("[tools] data-source query interrupt failed", exc_info=True)
        worker.join(timeout=1.0)
        if worker.is_alive():
            log.warning(
                "[tools] data-source query worker did not stop after interrupt"
            )

    while worker.is_alive():
        try:
            if abort_check is not None:
                abort_check()
        except BaseException:
            _stop_query()
            raise
        if deadline is not None and time.monotonic() >= deadline:
            _stop_query()
            from agent.errors import AgentRunTimeout

            raise AgentRunTimeout
        wait = 0.05
        if deadline is not None:
            wait = min(wait, max(0.001, deadline - time.monotonic()))
        worker.join(timeout=wait)

    error = result.get("error")
    if error is not None:
        raise error
    if abort_check is not None:
        abort_check()
    return result.get("value")


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _table_name_set(value) -> set[str]:
    """Normalize a connector's optional raw/derived table registry."""
    if isinstance(value, (set, list, tuple, frozenset)):
        return {str(item) for item in value if str(item).strip()}
    return set()


def _safe_delete_source_name(value: Any) -> str:
    """Keep delete audit metadata free of local absolute paths."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    # Data-source display names normally are file names.  If a connector
    # accidentally exposes a path, retain only its final component.
    raw = re.split(r"[/\\]", raw)[-1]
    return raw[:160]


def _safe_delete_table_name(value: Any) -> str:
    """Bound a table identifier before it enters user-visible audit data."""
    raw = str(value or "").strip()[:160]
    # Table names are identifiers, not filesystem locations.  Do not allow an
    # absolute-path-shaped value to become a path disclosure in /audit.
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[/\\]", raw):
        return "<path-redacted>"
    return raw


def _delete_operation_digest(
    source_name: str, confirm: bool, table_names: list[str],
) -> str:
    payload = {
        "source_name": _safe_delete_source_name(source_name),
        "confirm": bool(confirm),
        "table_names": [_safe_delete_table_name(item) for item in table_names],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _delete_reason_code(reason: str) -> str:
    """Map connector-specific delete failures to a stable safe code."""
    text = str(reason or "").strip().lower()
    if "empty table" in text:
        return "empty_table_name"
    if "not found" in text:
        return "table_not_found"
    if "registered source" in text or "raw" in text or "protected" in text:
        return "source_table_protected"
    if "registry" in text:
        return "registry_unavailable"
    if "connection unavailable" in text:
        return "connection_unavailable"
    if "not a known analysis" in text or "derived table" in text:
        return "unknown_derived_table"
    return "drop_failed"


def _execute_analysis(
    analysis_name: str,
    df,
    target_column: str,
    groupby_column: str,
    n_deciles: int,
    analysis_options: dict | None = None,
    progress_callback=None,
):
    """Run one analysis without holding an Agent or data-source reference."""
    from Function.Analyze.registry import get as get_analysis

    entry = get_analysis(analysis_name)
    run_fn = entry.get("run")
    if run_fn is None:
        raise RuntimeError(f"Analysis module '{analysis_name}' failed to load.")
    kwargs = {
        "df": df,
        "target_column": target_column,
        "groupby_column": groupby_column or None,
        "n_deciles": n_deciles,
    }
    if progress_callback is not None and (
        analysis_name.startswith(_TIME_SERIES_PREFIX) or analysis_name in _JOB_ANALYSIS_IDS
    ):
        kwargs["progress_callback"] = progress_callback
    if analysis_name == "AB_Test_Analysis":
        kwargs["analysis_options"] = analysis_options or {}
    result = run_fn(**kwargs)
    # Most analyzers use the historical tuple contract, while the univariate
    # screening analyzer returns a named table mapping.  Normalize both at the
    # boundary so the persistence layer never tries to unpack a dict as three
    # or four positional values.
    if isinstance(result, dict):
        import pandas as pd

        output_tables = list(entry.get("output_tables") or [])
        result_df = result.get("analysis_result")
        breakdown_df = result.get("analysis_breakdown")
        extra_name = output_tables[2] if len(output_tables) > 2 else ""
        extra_df = result.get(extra_name) if extra_name else None
        if result_df is None:
            raise RuntimeError(
                f"Analysis module '{analysis_name}' did not return analysis_result."
            )
        if breakdown_df is None:
            breakdown_df = pd.DataFrame()
        markdown = str(result.get("markdown") or result.get("text") or "")
        result = (result_df, breakdown_df, extra_df, markdown)
    if analysis_options and analysis_options.get("evaluation_mode"):
        holdout_evaluation = _build_temporal_holdout_evaluation(
            analysis_name,
            df,
            target_column,
            groupby_column,
            n_deciles,
            analysis_options,
        )
        entry = {**entry, "__pfs_model_evaluation__": holdout_evaluation}
    return entry, result


def _holdout_time_column(df, groupby_column: str) -> str:
    """Resolve a time column without accepting a numeric model hint as one."""
    import pandas as pd

    hint = str(groupby_column or "").strip()
    if "," not in hint and hint in df.columns:
        parsed = pd.to_datetime(df[hint], errors="coerce")
        if parsed.notna().all():
            return hint
    keywords = ("date", "time", "month", "year", "week", "day", "period", "ds", "日期", "时间")
    for column in df.columns:
        if not any(keyword in str(column).lower() for keyword in keywords):
            continue
        parsed = pd.to_datetime(df[column], errors="coerce")
        if parsed.notna().all():
            return str(column)
    for column in df.columns:
        if pd.api.types.is_numeric_dtype(df[column]):
            continue
        parsed = pd.to_datetime(df[column], errors="coerce")
        if parsed.notna().all():
            return str(column)
    raise ValueError("temporal holdout requires a parseable time column")


def _build_temporal_holdout_evaluation(
    analysis_name: str,
    df,
    target_column: str,
    groupby_column: str,
    n_deciles: int,
    analysis_options: Mapping[str, Any],
    *,
    include_prediction_rows: bool = False,
):
    """Fit a time-series analyzer on the train prefix and score its next rows."""
    import numpy as np
    import pandas as pd

    if not analysis_name.startswith(_TIME_SERIES_PREFIX):
        raise ValueError("temporal_holdout evaluation only supports time-series analyses")
    if not isinstance(analysis_options, Mapping):
        raise ValueError("analysis_options must be an object")
    mode = str(analysis_options.get("evaluation_mode") or "").strip().lower()
    if mode != "temporal_holdout":
        raise ValueError("analysis_options.evaluation_mode must be temporal_holdout")
    time_column = _holdout_time_column(df, groupby_column)
    if target_column not in df.columns:
        raise ValueError(f"temporal holdout target column missing: {target_column}")

    work = df[[time_column, target_column, *(
        [column for column in df.columns if column not in {time_column, target_column}]
    )]].copy()
    work[time_column] = pd.to_datetime(work[time_column], errors="coerce")
    work[target_column] = pd.to_numeric(work[target_column], errors="coerce")
    if work[time_column].isna().any() or work[target_column].isna().any():
        raise ValueError("temporal holdout requires complete time and target columns")
    if not np.isfinite(work[target_column].to_numpy(dtype=float)).all():
        raise ValueError("temporal holdout target must contain finite numeric values")
    work = work.sort_values(time_column).reset_index(drop=True)
    if work[time_column].duplicated().any():
        raise ValueError("temporal holdout requires unique timestamps")

    raw_horizon = analysis_options.get("holdout_size")
    if raw_horizon is None or raw_horizon == "":
        raw_horizon = n_deciles if int(n_deciles or 0) > 0 else 4
    if isinstance(raw_horizon, bool):
        raise ValueError("temporal holdout holdout_size must be a positive integer")
    try:
        horizon = int(raw_horizon)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("temporal holdout holdout_size must be a positive integer") from exc
    if horizon < 1 or horizon > 60:
        raise ValueError("temporal holdout holdout_size must be between 1 and 60")
    if len(work) - horizon < 8:
        raise ValueError("temporal holdout leaves too few training rows for the analyzer")

    train = work.iloc[:-horizon].copy()
    holdout = work.iloc[-horizon:].copy()
    _inner_entry, inner_result = _execute_analysis(
        analysis_name,
        train,
        target_column,
        groupby_column,
        horizon,
        analysis_options=None,
    )
    if not inner_result or not hasattr(inner_result[0], "columns"):
        raise ValueError("temporal holdout analyzer did not return a result table")
    forecast_table = inner_result[0]
    prediction_key = "y_pred"
    if not _frame_has_columns(forecast_table, {"ds", prediction_key, "segment"}):
        prediction_key = f"{target_column}_pred"
    if not _frame_has_columns(forecast_table, {"ds", prediction_key, "segment"}):
        raise ValueError(
            "temporal holdout analyzer output lacks ds/prediction/segment columns"
        )
    forecast = forecast_table[
        forecast_table["segment"].astype(str).str.strip().str.lower() == "forecast"
    ].head(horizon)
    if len(forecast) != horizon:
        raise ValueError(
            f"temporal holdout expected {horizon} forecast rows, got {len(forecast)}"
    )
    forecast_times = pd.to_datetime(forecast["ds"], errors="coerce")
    holdout_times = holdout[time_column]
    predicted = pd.to_numeric(forecast[prediction_key], errors="coerce")
    if predicted.isna().any() or not np.isfinite(predicted.to_numpy(dtype=float)).all():
        raise ValueError("temporal holdout predictions must be finite numeric values")

    alignment = str(analysis_options.get("temporal_alignment") or "exact").strip().lower()
    if alignment not in {"exact", "month"}:
        raise ValueError("temporal holdout temporal_alignment must be exact or month")
    if alignment == "month":
        # Some local analyzers infer a monthly step as a fixed number of days
        # (for example 2025-10-02) instead of the source's month-start date.
        # For a declared monthly business grain, compare calendar periods and
        # keep the source timestamp as the canonical Evidence locator.
        forecast_periods = forecast_times.dt.to_period("M")
        holdout_periods = holdout_times.dt.to_period("M")
        if not forecast_periods.reset_index(drop=True).equals(
            holdout_periods.reset_index(drop=True)
        ):
            raise ValueError("temporal holdout forecast months do not match source holdout rows")
    elif forecast_times.isna().any() or not forecast_times.reset_index(drop=True).equals(
        holdout_times.reset_index(drop=True)
    ):
        raise ValueError("temporal holdout forecast timestamps do not match source holdout rows")

    rows = [
        {
            "ds": timestamp.isoformat(),
            "segment": "holdout",
            "y_actual": float(actual),
            "y_pred": float(prediction),
        }
        for timestamp, actual, prediction in zip(
            holdout_times,
            holdout[target_column],
            predicted,
        )
    ]
    from pfs_agent.model_evaluation import evaluate_time_series_holdout_rows

    evaluation = evaluate_time_series_holdout_rows(
        rows,
        case_id=f"{analysis_name}:temporal-holdout",
        training_end=train[time_column].iloc[-1].isoformat(),
        thresholds=analysis_options.get("quality_thresholds"),
    )
    # Keep only business-safe aggregates in the evaluation contract.  The
    # raw source rows remain in the uploaded snapshot/Evidence; these totals
    # make a business acceptance card auditable without duplicating the data.
    evaluation["time_series"].update(
        {
            "holdout_actual_total": round(sum(item["y_actual"] for item in rows), 4),
            "holdout_predicted_total": round(sum(item["y_pred"] for item in rows), 4),
        }
    )
    if include_prediction_rows:
        # Internal callers such as rolling backtests need the paired rows to
        # aggregate folds.  The public business result never includes this
        # private field, so raw target values are not added to API responses.
        evaluation["_prediction_rows"] = rows
    return evaluation


def _frame_has_columns(frame, required: set[str]) -> bool:
    if frame is None:
        return False
    return required.issubset(set(getattr(frame, "columns", ())))


def _metric_names(frame) -> set[str]:
    if not _frame_has_columns(frame, {"metric"}):
        return set()
    return {str(value) for value in frame["metric"].tolist()}


def _model_evaluation_for_result(
    analysis_name: str,
    result_df,
    breakdown_df=None,
    extra_df=None,
    target_column: str = "",
):
    """Evaluate model output when an analyzer exposes a stable result shape.

    The adapters consume the same tables that are written for the Agent to
    query: time-series rows, regression residuals, or aggregated confusion
    rows.  Unsupported analyzer shapes return ``None`` rather than inventing a
    quality score.
    """
    from pfs_agent.model_evaluation import (
        evaluate_classification_confusion_rows,
        evaluate_prediction_rows,
        evaluate_time_series_rows,
    )

    if analysis_name.startswith(_TIME_SERIES_PREFIX):
        actual_key = "y_actual"
        predicted_key = "y_pred"
        if not _frame_has_columns(result_df, {actual_key, predicted_key}):
            target = str(target_column or "").strip()
            if target:
                actual_key = f"{target}_actual"
                predicted_key = f"{target}_pred"
        if not _frame_has_columns(result_df, {actual_key, predicted_key}):
            return None
        return evaluate_time_series_rows(
            result_df,
            case_id=f"{analysis_name}:paired-history",
            actual_key=actual_key,
            predicted_key=predicted_key,
        )

    if analysis_name == "Regression" and _frame_has_columns(
        breakdown_df, {"y_actual", "y_pred"}
    ):
        return evaluate_prediction_rows(
            breakdown_df,
            case_id=f"{analysis_name}:test-residuals",
            task_type="regression",
            expected_key="y_actual",
            predicted_key="y_pred",
        )

    if analysis_name in {"Decision_Tree", "Logistic_Regression"} and _frame_has_columns(
        breakdown_df, {"actual", "predicted", "count"}
    ):
        return evaluate_classification_confusion_rows(
            breakdown_df,
            case_id=f"{analysis_name}:test-confusion",
        )

    if analysis_name in {"Sklearn_Model", "Torch_MLP"} and _frame_has_columns(
        extra_df, {"actual", "predicted"}
    ):
        task_type = "classification" if "accuracy" in _metric_names(result_df) else "regression"
        if task_type == "classification" and _frame_has_columns(
            extra_df, {"count"}
        ):
            return evaluate_classification_confusion_rows(
                extra_df,
                case_id=f"{analysis_name}:test-confusion",
            )
        return evaluate_prediction_rows(
            extra_df,
            case_id=f"{analysis_name}:test-predictions",
            task_type=task_type,
        )
    return None


def _model_evaluation_table(evaluation: dict[str, Any]):
    """Build a compact derived table for querying and artifact lineage."""
    import pandas as pd

    scope = evaluation.get("scope") or {}
    cases = evaluation.get("cases") or ()
    case_id = str(cases[0].get("case_id") or "") if cases else ""
    rows = []
    for metric, detail in (evaluation.get("metrics") or {}).items():
        rows.append(
            {
                "case_id": case_id,
                "task_type": evaluation.get("task_type", ""),
                "metric": metric,
                "value": detail.get("value"),
                "sample_count": detail.get("sample_count", 0),
                "paired_rows": scope.get("paired_rows", scope.get("sample_count", 0)),
                "total_rows": scope.get("total_rows", scope.get("sample_count", 0)),
                "quality_passed": (evaluation.get("quality") or {}).get("passed"),
                "evaluation_scope": (evaluation.get("time_series") or {}).get(
                    "evaluation_scope", "fixed_rows"
                ),
            }
        )
    return pd.DataFrame(rows)


def _model_evaluation_markdown(analysis_name: str, evaluation: dict[str, Any]) -> str:
    """Format model quality as a bounded, explicit caveat-bearing report."""
    scope = evaluation.get("scope") or {}
    metrics = evaluation.get("metrics") or {}
    metric_text = "；".join(
        f"{name.upper()}={detail.get('value') if detail.get('value') is not None else '不可用'}"
        for name, detail in metrics.items()
    )
    is_time_series = evaluation.get("task_type") == "time_series"
    paired = scope.get("paired_rows", scope.get("sample_count", 0))
    total = scope.get("total_rows", scope.get("sample_count", paired))
    excluded_forecast = scope.get("excluded_forecast_rows", 0)
    excluded_unpaired = scope.get("excluded_unpaired_rows", 0)
    coverage = scope.get("paired_ratio", 1.0)
    time_series_scope = (evaluation.get("time_series") or {}).get(
        "evaluation_scope", "paired_history"
    )
    warning = ""
    if is_time_series and excluded_unpaired:
        warning = (
            f"；另有 {excluded_unpaired} 行历史记录未形成 actual/predicted 配对，"
            "覆盖率检查未通过"
        )
    if is_time_series:
        scope_label = "时间切分 holdout 行" if time_series_scope == "temporal_holdout" else "历史配对行"
        scope_text = (
            f"{scope_label} {paired}/{total}（覆盖率 {coverage:.2%}），"
            f"未来 forecast 行排除 {excluded_forecast} 行{warning}"
        )
        limitation = (
            "该结果来自显式训练截止点之后的时间切分 holdout，仍不等于生产预测质量或业务收益。"
            if time_series_scope == "temporal_holdout"
            else "该结果来自分析器实际输出的历史拟合配对，不等于时间外推 holdout、生产预测质量或业务收益。"
        )
    else:
        scope_text = f"分析器实际输出中的评估样本 {scope.get('sample_count', 0)} 行"
        limitation = "该结果来自本地分析器实际输出，不等于生产预测质量或业务收益。"
    return (
        "\n\n---\n"
        f"### 模型质量评估（{analysis_name}）\n"
        f"> 评估范围：{scope_text}。\n"
        f"> 观测指标：{metric_text}\n"
        f"> {limitation}"
    )


class DataToolsMixin:
    """All methods here rely on self.data_source, self._schema_cache,
    self.ppt_color_scheme — defined in BusinessAgent.__init__."""

    def _execute_source_operation(
        self,
        source,
        operation,
        *,
        timeout: float | None = None,
        abort_check=None,
    ):
        """Run a source operation without letting a blocking driver bypass Agent control."""
        if abort_check is not None:
            abort_check()
        if timeout is None and abort_check is None:
            return operation()

        interrupt = getattr(source, "interrupt_query", None)
        if not callable(interrupt):
            # Keep older/custom connectors bounded when they expose the
            # underlying connection but predate DataSource.interrupt_query.
            for attr in ("_conn", "_duck", "_cache_conn"):
                candidate = getattr(getattr(source, attr, None), "interrupt", None)
                if callable(candidate):
                    interrupt = candidate
                    break
        if not callable(interrupt):
            result = operation()
            if abort_check is not None:
                abort_check()
            return result
        result = _run_interruptible(
            operation,
            interrupt,
            timeout=timeout,
            abort_check=abort_check,
        )
        if abort_check is not None:
            abort_check()
        return result

    def _execute_source_query(
        self,
        source,
        sql: str,
        *,
        timeout: float | None = None,
        abort_check=None,
    ):
        """Execute a source query without letting a blocking driver bypass Agent control."""
        return self._execute_source_operation(
            source,
            lambda: source.execute_query(sql),
            timeout=timeout,
            abort_check=abort_check,
        )

    # ── Knowledge base lookup ─────────────────────────────────────────────────

    def _validate_data_sql(self, tool_name: str, sql: str) -> str | None:
        """Apply the same read-only SQL policy at the tool implementation boundary.

        The normal model dispatch validates arguments before reaching these
        methods. Keeping a second guard here prevents direct/internal callers
        from accidentally turning a read tool into a write-capable path.
        """
        from agent.validate import validate_tool_args

        authorization = getattr(self, "_workspace_path_authorization", lambda: None)()
        roots = None
        workspace_id = str(getattr(self, "_workspace_id", "") or "")
        if workspace_id and authorization is None:
            roots = []
        # This helper is deliberately SQL-only.  The outer Agent dispatch
        # validates the complete call (including run_analysis's
        # ``analysis_name`` and ``target_column``), while this second guard is
        # also used by direct/internal callers.  Passing ``run_analysis`` back
        # into the complete argument validator here used to make every valid
        # analysis fail with "requires analysis_name" because only ``sql`` was
        # present in this helper's input.
        return validate_tool_args(
            "query_data",
            {"sql": sql},
            allowed_roots=roots,
            workspace_authorization=authorization,
        )

    def _tool_query_knowledge_results(
        self,
        question: str,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> dict:
        if not bool(getattr(self, "_knowledge_allowed_this_turn", False)):
            return {"error": "Knowledge lookup is not allowed for this request."}
        try:
            from agent.errors import AgentRunTimeout
            from agent.jobs import JobCanceled
            from Function.Knowledge.knowledge_base import KnowledgeBase

            if abort_check is None:
                abort_check = getattr(self, "_check_active_run_budget", None)
                if not callable(abort_check):
                    abort_check = None
            if abort_check is not None:
                abort_check()
            if timeout is None:
                timeout_provider = getattr(
                    self, "_remaining_active_run_timeout", None,
                )
                timeout = timeout_provider() if callable(timeout_provider) else None
            kb = KnowledgeBase(
                workspace_id=str(getattr(self, "_workspace_id", "") or ""),
                user_id=getattr(self, "_user_id", ""),
            )
            try:
                results = kb.search(
                    question,
                    limit=5,
                    timeout=timeout,
                    abort_check=abort_check,
                )
                if abort_check is not None:
                    abort_check()
                return results
            finally:
                kb.close()
        except (JobCanceled, AgentRunTimeout):
            raise
        except Exception as e:
            return {"error": f"Knowledge base unavailable: {e}"}

    def _format_knowledge_results(self, results: dict) -> str:
        if results.get("error"):
            return results["error"]

        if not any(results.values()):
            return "No relevant knowledge found."

        lines: list[str] = []

        for m in results.get("metrics", []):
            lines.append(f"[Metric] {m['name']}")
            if m.get("alias"):
                lines.append(f"  Alias: {m['alias']}")
            if m.get("definition"):
                lines.append(f"  Definition: {m['definition']}")
            if m.get("sql_template"):
                lines.append(f"  SQL template: {m['sql_template']}")
            if m.get("notes"):
                lines.append(f"  Notes: {m['notes']}")

        for r in results.get("rules", []):
            lines.append(f"[Rule/{r['severity'].upper()}] {r['rule_id']}: {r['description']}")
            if r.get("condition"):
                lines.append(f"  Condition: {r['condition']}")

        for n in results.get("notes", []):
            lines.append(f"[Context] {n['topic']}: {n['content']}")

        for d in results.get("documents", []):
            source = d.get("source_name", "unknown")
            idx = d.get("chunk_index", 0)
            score = d.get("score", d.get("vector_score", ""))
            score_part = f" | score={score}" if score != "" else ""
            content = (d.get("content") or "").strip()
            lines.append(f"[Document] {source}#chunk-{idx}{score_part}")
            lines.append(content[:1200])

        return "\n".join(lines)

    def _knowledge_refs_from_results(self, results: dict, limit: int = 8) -> list[dict]:
        """Compact, UI-safe citation metadata for the knowledge tool step."""
        if results.get("error"):
            return []

        refs: list[dict] = []
        for m in results.get("metrics", []):
            refs.append({
                "type": "指标",
                "title": m.get("name", ""),
                "source": m.get("alias", "") or "指标定义",
                "snippet": m.get("definition", "") or m.get("notes", ""),
                "score": m.get("vector_score", ""),
            })

        for r in results.get("rules", []):
            refs.append({
                "type": "规则",
                "title": r.get("rule_id", ""),
                "source": (r.get("severity") or "warning").upper(),
                "snippet": r.get("description", "") or r.get("condition", ""),
                "score": "",
            })

        for n in results.get("notes", []):
            refs.append({
                "type": "背景",
                "title": n.get("topic", ""),
                "source": n.get("tags", "") or "背景知识",
                "snippet": n.get("content", ""),
                "score": n.get("vector_score", ""),
            })

        for d in results.get("documents", []):
            refs.append({
                "type": "文档",
                "title": f"{d.get('source_name', 'unknown')} #chunk-{d.get('chunk_index', 0)}",
                "source": d.get("source_name", ""),
                "snippet": d.get("content", ""),
                "score": d.get("score", d.get("vector_score", "")),
            })

        clean_refs: list[dict] = []
        for ref in refs[:limit]:
            clean_refs.append({
                "type": str(ref.get("type") or ""),
                "title": str(ref.get("title") or "")[:120],
                "source": str(ref.get("source") or "")[:160],
                "snippet": re.sub(r"\s+", " ", str(ref.get("snippet") or "")).strip()[:260],
                "score": ref.get("score", ""),
            })
        return clean_refs

    def _tool_query_knowledge_with_refs(
        self,
        question: str,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> tuple[str, list[dict]]:
        results = self._tool_query_knowledge_results(
            question,
            timeout=timeout,
            abort_check=abort_check,
        )
        return self._format_knowledge_results(results), self._knowledge_refs_from_results(results)

    def _tool_query_knowledge(
        self,
        question: str,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> str:
        results = self._tool_query_knowledge_results(
            question,
            timeout=timeout,
            abort_check=abort_check,
        )
        return self._format_knowledge_results(results)

    # ── Basic data access ─────────────────────────────────────────────────────

    def _tool_get_schema(self, *, timeout: float | None = None, abort_check=None) -> str:
        if abort_check is not None:
            abort_check()
        if not self.data_source and not getattr(self, "_combined_schema", None):
            return "No data source connected."
        if not self._schema_cache:
            combined = getattr(self, "_combined_schema", None)
            self._schema_cache = combined if combined else self._execute_source_operation(
                self.data_source,
                lambda: self.data_source.get_schema(),
                timeout=timeout,
                abort_check=abort_check,
            )
        if abort_check is not None:
            abort_check()
        return self._schema_cache

    def _tool_get_table_detail(
        self,
        table_name: str,
        *,
        timeout: float | None = None,
        abort_check=None,
        timeout_provider=None,
    ) -> str:
        """Return full column list + row count for a single table (SQL databases)."""
        def _remaining_timeout():
            return timeout_provider() if callable(timeout_provider) else timeout

        # Try each active source until one knows the table
        for src in getattr(self, "_all_sources", [self.data_source]):
            if abort_check is not None:
                abort_check()
            if src is None:
                continue
            fn = getattr(src, "get_table_detail", None)
            try:
                tables = self._execute_source_operation(
                    src,
                    lambda: src.list_tables(),
                    timeout=_remaining_timeout(),
                    abort_check=abort_check,
                )
            except (JobCanceled, AgentRunTimeout):
                raise
            except Exception:
                tables = []
            if abort_check is not None:
                abort_check()
            if table_name in tables:
                if callable(fn):
                    result = self._execute_source_operation(
                        src,
                        lambda: fn(table_name),
                        timeout=_remaining_timeout(),
                        abort_check=abort_check,
                    )
                    if abort_check is not None:
                        abort_check()
                    return result

                # File connectors expose bounded previews instead of the SQL
                # connector's get_table_detail method.  Use that common
                # contract here so Excel/CSV/Feishu tables are not falsely
                # reported as missing when they are queryable.
                preview_fn = getattr(src, "get_preview_table", None)
                if callable(preview_fn):
                    preview = self._execute_source_operation(
                        src,
                        lambda: preview_fn(table_name, max_rows=1),
                        timeout=_remaining_timeout(),
                        abort_check=abort_check,
                    )
                    if isinstance(preview, dict) and not preview.get("error"):
                        columns = [
                            str(column).strip()
                            for column in (preview.get("columns") or [])
                            if str(column).strip()
                        ]
                        total_rows = preview.get("total_rows")
                        row_label = (
                            f"  ({int(total_rows):,} rows)"
                            if isinstance(total_rows, (int, float))
                            and not isinstance(total_rows, bool)
                            else ""
                        )
                        return (
                            f"Table: {table_name}{row_label}\n"
                            + "\n".join(f"  {column}" for column in columns)
                        )
        # Fallback: try primary source regardless
        if abort_check is not None:
            abort_check()
        if self.data_source:
            fn = getattr(self.data_source, "get_table_detail", None)
            if callable(fn):
                result = self._execute_source_operation(
                    self.data_source,
                    lambda: fn(table_name),
                    timeout=_remaining_timeout(),
                    abort_check=abort_check,
                )
                if abort_check is not None:
                    abort_check()
                return result
            preview_fn = getattr(self.data_source, "get_preview_table", None)
            if callable(preview_fn):
                preview = self._execute_source_operation(
                    self.data_source,
                    lambda: preview_fn(table_name, max_rows=1),
                    timeout=_remaining_timeout(),
                    abort_check=abort_check,
                )
                if isinstance(preview, dict) and not preview.get("error"):
                    columns = [
                        str(column).strip()
                        for column in (preview.get("columns") or [])
                        if str(column).strip()
                    ]
                    total_rows = preview.get("total_rows")
                    row_label = (
                        f"  ({int(total_rows):,} rows)"
                        if isinstance(total_rows, (int, float))
                        and not isinstance(total_rows, bool)
                        else ""
                    )
                    return (
                        f"Table: {table_name}{row_label}\n"
                        + "\n".join(f"  {column}" for column in columns)
                    )
        return f"Table '{table_name}' not found in any connected data source."

    @staticmethod
    def _strip_src_prefix(sql: str, src_index: int) -> str:
        """Remove ``src{N}__`` prefixes injected by get_combined_schema.

        Called just before executing SQL against a specific DataSource so the
        engine sees only bare table names it actually owns.
        """
        import re as _re
        prefix = f"src{src_index}__"
        # Replace quoted  "src1__tablename"  and bare  src1__tablename
        sql = _re.sub(
            rf'"?{_re.escape(prefix)}([^"\s,)]+)"?',
            lambda m: f'"{m.group(1)}"',
            sql,
        )
        return sql

    def _route_query(self, sql: str):
        """Return (DataSource, rewritten_sql) for the source that owns the tables
        referenced in *sql*.

        Routing priority
        ----------------
        1. **Cross-source SQL** (SQL contains ``src{N}__`` prefixes from two or
           more distinct source indices) → execute on ``_merged_source`` as-is.
           The merged connection already has all tables registered with prefixes.
        2. **Single-source prefixed SQL** (all ``src{N}__`` prefixes point to the
           same index N) → strip prefix, execute on ``sources[N-1]`` directly.
        3. **Bare table names** (no prefix) → heuristic match against each
           source's table list; first match wins.  Falls back to primary source.

        Returns a (DataSource, sql) tuple.  Callers must use the returned *sql*.
        """
        import re as _re
        sources = getattr(self, "_all_sources", None)
        if not sources or len(sources) == 1:
            return self.data_source, sql

        # ── Detect src{N}__ prefixes in SQL ─────────────────────────────────
        prefix_pat = _re.compile(r'src(\d+)__', _re.IGNORECASE)
        prefix_hits = prefix_pat.findall(sql)

        if prefix_hits:
            unique_indices = set(int(h) for h in prefix_hits)

            # ── Mode 1: cross-source — two or more different src indices ────
            if len(unique_indices) > 1:
                merged = getattr(self, "_merged_source", None)
                if merged is not None:
                    log.debug("[route] cross-source SQL → MergedDataSource")
                    return merged, sql
                # Merged source unavailable — fall through to heuristic as best-effort
                log.warning("[route] cross-source SQL but _merged_source is None, "
                            "falling back to primary source")
                return self.data_source, sql

            # ── Mode 2: single-source prefix — strip and route directly ─────
            idx = next(iter(unique_indices))   # 1-based
            if 1 <= idx <= len(sources):
                src = sources[idx - 1]
                rewritten = self._strip_src_prefix(sql, idx)
                log.debug("[route] src%d prefix → source=%s", idx, getattr(src, "name", "?"))
                return src, rewritten
            # Index out of range — fall through

        # ── Mode 3: bare table name heuristic ───────────────────────────────
        sql_upper = sql.upper()
        for src in sources:
            try:
                tables = [t.upper() for t in src.list_tables()]
            except Exception:
                tables = []
            if any(t in sql_upper for t in tables):
                log.debug("[route] bare-name heuristic → source=%s", getattr(src, "name", "?"))
                return src, sql

        # Fallback: primary source, unchanged SQL
        return self.data_source, sql

    def _tool_query_data(self, sql: str, *, abort_check=None, timeout=None) -> str:
        result, _refs = self._tool_query_data_with_refs(
            sql, abort_check=abort_check, timeout=timeout,
        )
        return result

    def _data_refs_for_sql(self, sql: str, src, row_count: int | None = None) -> list[dict]:
        source_name = getattr(src, "name", "未知数据源") if src else "未知数据源"
        tables = []
        for pattern in (
            r'(?i)\bfrom\s+"([^"]+)"',
            r'(?i)\bjoin\s+"([^"]+)"',
            r'(?i)\bfrom\s+([A-Za-z_][\w$]*)',
            r'(?i)\bjoin\s+([A-Za-z_][\w$]*)',
        ):
            tables.extend(re.findall(pattern, sql or ""))
        # Keep order, remove duplicates and internal aliases.
        seen = set()
        clean_tables = []
        for t in tables:
            if t.lower() in {"select", "where", "group", "order"}:
                continue
            if t not in seen:
                seen.add(t)
                clean_tables.append(t)
        return [{
            "type": "数据查询",
            "title": ", ".join(clean_tables[:6]) or "SQL 查询",
            "source": source_name,
            "snippet": " ".join((sql or "").split())[:320],
            "rows": row_count,
        }]

    def _tool_query_data_with_refs(
        self,
        sql: str,
        *,
        abort_check=None,
        timeout: float | None = None,
    ) -> tuple[str, list[dict]]:
        if abort_check is not None:
            abort_check()
        sql_preview = sql.replace("\n", " ")[:120]
        validation_error = self._validate_data_sql("query_data", sql)
        if validation_error:
            return f"SQL Error: {validation_error}", self._data_refs_for_sql(sql, self.data_source, None)
        src, rewritten_sql = self._route_query(sql)
        if not src:
            log.warning("[tools] query_data  no data source  sql=%.80r", sql_preview)
            return "No data source. Please connect a database or upload an Excel file first.", []
        if abort_check is not None:
            abort_check()
        df, error = self._execute_source_query(
            src,
            rewritten_sql,
            timeout=timeout,
            abort_check=abort_check,
        )
        if abort_check is not None:
            abort_check()
        if error:
            log.warning("[tools] query_data  ERROR  source=%s  sql=%.80r  error=%s",
                        getattr(src, "name", "?"), sql_preview, error[:200])
            # If primary source failed and we have alternatives, try them
            # (only for bare-name queries — prefixed queries target a specific source)
            import re as _re
            if not _re.search(r'src\d+__', sql, _re.IGNORECASE):
                sources = getattr(self, "_all_sources", None) or []
                for alt in sources:
                    if abort_check is not None:
                        abort_check()
                    if alt is src:
                        continue
                    df2, err2 = self._execute_source_query(
                        alt,
                        rewritten_sql,
                        timeout=timeout,
                        abort_check=abort_check,
                    )
                    if abort_check is not None:
                        abort_check()
                    if not err2:
                        log.info("[tools] query_data  fallback OK  source=%s  rows=%d",
                                 getattr(alt, "name", "?"), len(df2))
                        return alt.format_result(df2), self._data_refs_for_sql(sql, alt, len(df2))
            return f"SQL Error: {error}", self._data_refs_for_sql(sql, src, None)
        log.info("[tools] query_data  OK  source=%s  rows=%d  sql=%.80r",
                 getattr(src, "name", "?"), len(df), sql_preview)
        return src.format_result(df), self._data_refs_for_sql(sql, src, len(df))

    def _query_data_job_connection_info(self, src):
        """Return (db_path, lock) for sources that are safe to query from a worker."""
        db_path = getattr(src, "_db_path", None)
        if not db_path or not hasattr(src, "_db_lock"):
            return None, None
        try:
            db_path = Path(db_path)
        except TypeError:
            return None, None
        if not db_path.exists():
            return None, None
        return db_path, getattr(src, "_db_lock", None)

    def _estimate_query_rows_for_job(
        self,
        db_path: Path,
        sql: str,
        lock=None,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> int | None:
        """Estimate result size using a fresh DuckDB connection."""
        import duckdb

        query = (sql or "").strip().rstrip(";")
        if not query:
            return None
        guard = lock or nullcontext()
        try:
            with guard:
                conn = duckdb.connect(str(db_path))
                try:
                    row = _run_interruptible(
                        lambda: conn.execute(
                            f"SELECT COUNT(*) FROM ({query}) AS _pfs_query_count"
                        ).fetchone(),
                        conn.interrupt,
                        timeout=timeout,
                        abort_check=abort_check,
                    )
                    return int(row[0]) if row else None
                finally:
                    conn.close()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            from agent.errors import AgentRunTimeout
            from agent.jobs import JobCanceled

            if isinstance(exc, (JobCanceled, AgentRunTimeout)):
                raise
            log.info("[tools] query_data row estimate skipped: %s", exc)
            return None

    def _execute_query_in_fresh_db(
        self,
        db_path: Path,
        sql: str,
        lock=None,
        *,
        timeout: float | None = None,
        abort_check=None,
    ):
        import duckdb
        import pandas as pd

        conn = None
        try:
            guard = lock or nullcontext()
            with guard:
                conn = duckdb.connect(str(db_path))
                try:
                    frame = _run_interruptible(
                        lambda: conn.execute(sql).df(),
                        conn.interrupt,
                        timeout=timeout,
                        abort_check=abort_check,
                    )
                    return frame, ""
                finally:
                    conn.close()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            from agent.errors import AgentRunTimeout
            from agent.jobs import JobCanceled

            if isinstance(exc, (JobCanceled, AgentRunTimeout)):
                raise
            return pd.DataFrame(), str(exc)

    def _tool_query_data_with_jobs(
        self,
        sql: str,
        *,
        timeout: float | None = None,
        abort_check=None,
        timeout_provider=None,
    ):
        if abort_check is not None:
            abort_check()

        def _query_timeout() -> float | None:
            if callable(timeout_provider):
                return timeout_provider()
            return timeout

        sql_preview = sql.replace("\n", " ")[:120]
        validation_error = self._validate_data_sql("query_data", sql)
        if validation_error:
            return (
                f"SQL Error: {validation_error}",
                self._data_refs_for_sql(sql, self.data_source, None),
            )
        src, rewritten_sql = self._route_query(sql)
        if not src:
            log.warning("[tools] query_data  no data source  sql=%.80r", sql_preview)
            return "No data source. Please connect a database or upload an Excel file first.", []

        db_path, db_lock = self._query_data_job_connection_info(src)
        can_job = self._job_runner is not None and db_path is not None
        estimated_rows = (
            self._estimate_query_rows_for_job(
                db_path,
                rewritten_sql,
                db_lock,
                timeout=_query_timeout(),
                abort_check=abort_check,
            )
            if can_job else None
        )
        if not can_job or estimated_rows is None or estimated_rows < _QUERY_JOB_ROW_THRESHOLD:
            return self._tool_query_data_with_refs(
                sql,
                timeout=_query_timeout(),
                abort_check=abort_check,
            )

        result_holder = {}
        sql_snapshot = str(rewritten_sql or "")
        db_path_snapshot = Path(db_path)

        def _worker(ctx):
            ctx.set_progress(10, "正在准备查询")
            ctx.check_canceled()
            ctx.set_progress(45, "正在执行 SQL 查询")
            df, error = self._execute_query_in_fresh_db(
                db_path_snapshot,
                sql_snapshot,
                db_lock,
                timeout=_query_timeout(),
                abort_check=ctx.check_canceled,
            )
            ctx.check_canceled()
            if error:
                result_holder["error"] = error
            else:
                result_holder["df"] = df
            ctx.set_progress(100, "查询完成")
            return {
                "rows": 0 if error else len(df),
                "source": getattr(src, "name", ""),
                "estimated_rows": estimated_rows,
            }

        job = yield from self._run_as_job(
            _worker,
            job_type="query_data",
            label=f"{estimated_rows} rows",
        )
        refs = self._data_refs_for_sql(sql, src, None)
        if job.get("status") == "canceled":
            return "查询已取消。", refs
        if job.get("status") != "succeeded":
            return f"SQL Error: {job.get('error') or 'background job failed'}", refs
        if result_holder.get("error"):
            return f"SQL Error: {result_holder['error']}", refs
        df = result_holder.get("df")
        if df is None:
            return "SQL Error: background result unavailable", refs
        log.info("[tools] query_data job  source=%s  rows=%d  sql=%.80r",
                 getattr(src, "name", "?"), len(df), sql_preview)
        return src.format_result(df), self._data_refs_for_sql(sql, src, len(df))

    def _tool_create_analysis_table(
        self,
        sql: str,
        table_name: str = "analysis_data",
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> str:
        result, _refs = self._tool_create_analysis_table_with_refs(
            sql,
            table_name,
            timeout=timeout,
            abort_check=abort_check,
        )
        return result

    def _known_analysis_tables(
        self,
        src,
        *,
        existing_tables: set[str] | None = None,
        timeout: float | None = None,
        abort_check=None,
    ) -> set[str]:
        """Return connector-owned derived tables that may be replaced/deleted.

        Connectors intentionally keep raw source tables and transient analysis
        tables in the same DuckDB catalog. The registry is therefore part of
        the safety boundary: a table is replaceable only when the connector
        can prove that it was created as an analysis result.
        """
        known = _table_name_set(getattr(src, "_cache_tables", None))

        # MergedDataSource uses a list for derived tables. The in-memory file
        # connectors use a set, but expose _source_tables to distinguish it
        # from SQLDataSource's _analysis_tables (which means source scope).
        scoped = getattr(src, "_analysis_tables", None)
        if isinstance(scoped, list):
            known.update(_table_name_set(scoped))
        elif hasattr(src, "_source_tables"):
            known.update(_table_name_set(scoped))

        # WorkspacePersistentSource stores raw source ownership in the durable
        # registry. Any existing table outside that registry is derived, but a
        # missing/corrupt registry must not silently widen access.
        db_path = getattr(src, "_db_path", None)
        if db_path is not None and getattr(src, "_conn", None) is not None:
            registry_path = Path(db_path).parent / "registry.json"
            try:
                import json

                raw = json.loads(registry_path.read_text(encoding="utf-8"))
                registered = set(raw.keys()) if isinstance(raw, dict) else set()
                existing = (
                    set(existing_tables)
                    if existing_tables is not None
                    else set(self._execute_source_operation(
                        src,
                        lambda: src.list_tables(),
                        timeout=timeout,
                        abort_check=abort_check,
                    ) or [])
                )
                known.update(existing - {str(item) for item in registered})
            except Exception:
                # _analysis_table_connection also fails closed when this
                # registry cannot be read; keep the creation path consistent.
                pass
        return known

    def _validate_analysis_table_name(
        self,
        src,
        table_name: str,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> str | None:
        """Reject unsafe names and prevent replacing a raw source table."""
        name = str(table_name or "").strip()
        if not name:
            return "分析表名不能为空。"
        if not re.fullmatch(r"[\w$][\w$]*", name, flags=re.UNICODE):
            return "分析表名只能包含字母、数字、下划线或中文。"

        list_tables = getattr(src, "list_tables", None)
        if not callable(list_tables):
            return "无法确认数据源表目录，已拒绝创建分析表。"
        try:
            existing = set(self._execute_source_operation(
                src,
                lambda: list_tables(),
                timeout=timeout,
                abort_check=abort_check,
            ) or [])
        except (JobCanceled, AgentRunTimeout):
            raise
        except Exception as exc:
            return f"无法确认数据源表目录，已拒绝创建分析表：{exc}"

        if name in existing and name not in self._known_analysis_tables(
            src,
            existing_tables=existing,
            timeout=timeout,
            abort_check=abort_check,
        ):
            return f"不能覆盖原始数据表 `{name}`；请使用新的分析表名。"
        return None

    def _tool_create_analysis_table_with_refs(
        self,
        sql: str,
        table_name: str = "analysis_data",
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> tuple[str, list[dict]]:
        if abort_check is not None:
            abort_check()
        validation_error = self._validate_data_sql("create_analysis_table", sql)
        if validation_error:
            return f"Error building analysis table: {validation_error}", self._data_refs_for_sql(sql, self.data_source, None)
        src, rewritten_sql = self._route_query(sql)
        if not src:
            return "No data source connected.", []
        table_error = self._validate_analysis_table_name(
            src,
            table_name,
            timeout=timeout,
            abort_check=abort_check,
        )
        if table_error:
            refs = self._data_refs_for_sql(sql, src, None)
            refs[0]["type"] = "分析表"
            refs[0]["title"] = str(table_name or "analysis_data")
            return f"Error building analysis table: {table_error}", refs
        result = self._execute_source_operation(
            src,
            lambda: src.create_analysis_table(rewritten_sql, table_name),
            timeout=timeout,
            abort_check=abort_check,
        )
        if abort_check is not None:
            abort_check()
        self._schema_cache = None
        log.info("[tools] create_analysis_table  table=%s  source=%s",
                 table_name, getattr(src, "name", "?"))
        refs = self._data_refs_for_sql(sql, src, None)
        refs[0]["type"] = "分析表"
        refs[0]["title"] = table_name
        return result, refs

    def _tool_create_analysis_table_with_jobs(
        self,
        sql: str,
        table_name: str = "analysis_data",
        *,
        timeout: float | None = None,
        abort_check=None,
        timeout_provider=None,
    ):
        if abort_check is not None:
            abort_check()

        def _remaining_timeout():
            return timeout_provider() if callable(timeout_provider) else timeout

        validation_error = self._validate_data_sql("create_analysis_table", sql)
        if validation_error:
            return (
                f"Error building analysis table: {validation_error}",
                self._data_refs_for_sql(sql, self.data_source, None),
            )
        src, rewritten_sql = self._route_query(sql)
        if not src:
            return "No data source connected.", []

        table_error = self._validate_analysis_table_name(
            src,
            table_name,
            timeout=_remaining_timeout(),
            abort_check=abort_check,
        )
        if table_error:
            refs = self._data_refs_for_sql(sql, src, None)
            refs[0]["type"] = "分析表"
            refs[0]["title"] = str(table_name or "analysis_data")
            return f"Error building analysis table: {table_error}", refs

        refs = self._data_refs_for_sql(sql, src, None)
        refs[0]["type"] = "分析表"
        refs[0]["title"] = table_name

        can_job = self._job_runner is not None and hasattr(src, "_db_lock")
        if not can_job:
            result = self._execute_source_operation(
                src,
                lambda: src.create_analysis_table(rewritten_sql, table_name),
                timeout=_remaining_timeout(),
                abort_check=abort_check,
            )
            if abort_check is not None:
                abort_check()
            self._schema_cache = None
            log.info("[tools] create_analysis_table  table=%s  source=%s",
                     table_name, getattr(src, "name", "?"))
            return result, refs

        result_holder = {}
        table_snapshot = str(table_name or "analysis_data")
        sql_snapshot = str(rewritten_sql or "")

        def _worker(ctx):
            ctx.set_progress(10, "正在准备分析表")
            ctx.check_canceled()
            ctx.set_progress(40, "正在执行建表 SQL")
            result = self._execute_source_operation(
                src,
                lambda: src.create_analysis_table(sql_snapshot, table_snapshot),
                timeout=_remaining_timeout(),
                abort_check=ctx.check_canceled,
            )
            ctx.check_canceled()
            result_holder["text"] = result
            ctx.set_progress(100, "分析表创建完成")
            return {"table": table_snapshot, "source": getattr(src, "name", "")}

        job = yield from self._run_as_job(
            _worker,
            job_type="create_analysis_table",
            label=table_snapshot,
        )
        if job.get("status") == "canceled":
            return "分析表创建已取消。", refs
        if job.get("status") != "succeeded":
            return f"Error building analysis table: {job.get('error') or 'background job failed'}", refs
        self._schema_cache = None
        log.info("[tools] create_analysis_table job  table=%s  source=%s",
                 table_snapshot, getattr(src, "name", "?"))
        return result_holder.get(
            "text", "Error building analysis table: background result unavailable"
        ), refs

    def _analysis_table_connection(
        self,
        src,
        table_name: str,
        *,
        timeout: float | None = None,
        abort_check=None,
    ):
        """Return (conn, lock, cleanup_callback) when table is safe to DROP."""
        table_name = str(table_name or "").strip()
        if not table_name:
            return None, None, None, "empty table name"

        list_tables = getattr(src, "list_tables", None)
        try:
            existing = (
                set(self._execute_source_operation(
                    src,
                    lambda: list_tables(),
                    timeout=timeout,
                    abort_check=abort_check,
                ) or [])
                if callable(list_tables) else set()
            )
        except (JobCanceled, AgentRunTimeout):
            raise
        except Exception:
            existing = set()
        if existing and table_name not in existing:
            return None, None, None, "table not found"

        # SQLDataSource and fallback in-memory analysis tables.
        cache_tables = getattr(src, "_cache_tables", None)
        if isinstance(cache_tables, set) and table_name in cache_tables:
            conn = getattr(src, "_duck", None) or getattr(src, "_conn", None) or getattr(src, "_cache_conn", None)
            if conn is None:
                return None, None, None, "analysis table connection unavailable"

            def cleanup():
                cache_tables.discard(table_name)

            return conn, getattr(src, "_lock", None), cleanup, ""

        # MergedDataSource tracks derived tables separately.
        analysis_tables = getattr(src, "_analysis_tables", None)
        if isinstance(analysis_tables, (set, list, tuple)) and table_name in analysis_tables:
            conn = getattr(src, "_conn", None)
            if conn is None:
                return None, None, None, "analysis table connection unavailable"

            def cleanup():
                if isinstance(analysis_tables, set):
                    analysis_tables.discard(table_name)
                elif isinstance(analysis_tables, list):
                    while table_name in analysis_tables:
                        analysis_tables.remove(table_name)

            return conn, getattr(src, "_lock", None), cleanup, ""

        # WorkspacePersistentDataSource: registered source tables are listed in
        # registry.json; unregistered tables are derived/analysis objects.
        db_path = getattr(src, "_db_path", None)
        conn = getattr(src, "_conn", None)
        if db_path is not None and conn is not None:
            registry_path = Path(db_path).parent / "registry.json"
            registered = set()
            try:
                import json
                if registry_path.is_file():
                    raw = json.loads(registry_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        registered = {str(key) for key in raw.keys()}
            except Exception as exc:
                return None, None, None, f"registry unavailable; refusing to delete: {exc}"
            if table_name in registered:
                return None, None, None, "registered source table is protected"
            if not existing or table_name in existing:
                return conn, getattr(src, "_db_lock", None), None, ""

        return None, None, None, "not a known analysis/derived table"

    def _drop_analysis_table(
        self,
        src,
        table_name: str,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> tuple[bool, str]:
        conn, lock, cleanup, reason = self._analysis_table_connection(
            src,
            table_name,
            timeout=timeout,
            abort_check=abort_check,
        )
        if conn is None:
            return False, reason

        def run_drop():
            guard = lock or nullcontext()
            with guard:
                conn.execute(f"DROP TABLE IF EXISTS {_quote_ident(table_name)}")
                if cleanup:
                    cleanup()

        try:
            self._execute_source_operation(
                src,
                run_drop,
                timeout=timeout,
                abort_check=abort_check,
            )
            if abort_check is not None:
                abort_check()
            return True, "deleted"
        except (JobCanceled, AgentRunTimeout):
            raise
        except Exception as exc:
            return False, str(exc)

    def _tool_delete_analysis_tables(
        self,
        table_names: list,
        confirm: bool = False,
        *,
        operation_key: str = "",
        timeout: float | None = None,
        abort_check=None,
        timeout_provider=None,
    ) -> str:
        self._last_analysis_delete_audit = None
        if abort_check is not None:
            abort_check()
        clean_names = []
        seen = set()
        for name in table_names or []:
            value = str(name or "").strip()
            if value and value not in seen:
                clean_names.append(value)
                seen.add(value)

        source_name = _safe_delete_source_name(
            getattr(self.data_source, "name", "") if self.data_source else ""
        )
        operation_key = str(operation_key or "").strip()[:240]
        operation_store = getattr(self, "_analysis_delete_operation_store", None)
        request_sha256 = _delete_operation_digest(
            source_name, confirm, clean_names,
        )

        def _local_audit(
            status: str,
            *,
            result: str = "",
            deleted: list[str] | None = None,
            skipped: list[tuple[str, str]] | None = None,
            error: str = "",
            replay: bool = False,
        ) -> dict:
            safe_deleted = [
                _safe_delete_table_name(item) for item in (deleted or [])[:32]
            ]
            safe_skipped = [
                {
                    "table_name": _safe_delete_table_name(name),
                    "reason_code": _delete_reason_code(reason),
                }
                for name, reason in (skipped or [])[:32]
            ]
            summary_parts = []
            if safe_deleted:
                summary_parts.append(
                    "已删除分析表：" + "、".join(safe_deleted)
                )
            if safe_skipped:
                summary_parts.append(
                    "未删除：" + "、".join(
                        f"{item['table_name']}（{item['reason_code']}）"
                        for item in safe_skipped
                    )
                )
            if not summary_parts:
                summary_parts.append(
                    "操作未执行" if status in {"rejected", "conflict"}
                    else "操作未产生删除结果"
                )
            return {
                "operation_key": operation_key,
                "request_sha256": request_sha256,
                "source_name": source_name,
                "table_names": [
                    _safe_delete_table_name(item) for item in clean_names[:32]
                ],
                "status": status,
                "deleted": safe_deleted,
                "skipped": safe_skipped,
                # The raw connector message is returned to the current caller
                # for compatibility, but never enters persisted audit data.
                "result": "；".join(summary_parts)[:8_000],
                "error": str(error or "")[:500],
                "idempotent_replay": bool(replay),
            }

        def _finish(
            status: str,
            *,
            result: str = "",
            deleted: list[str] | None = None,
            skipped: list[tuple[str, str]] | None = None,
            error: str = "",
        ) -> dict:
            audit = _local_audit(
                status,
                result=result,
                deleted=deleted,
                skipped=skipped,
                error=error,
            )
            if operation_store is not None and operation_key:
                stored = operation_store.finish_analysis_delete_operation(
                    operation_key,
                    status=status,
                    result=audit["result"],
                    deleted=audit["deleted"],
                    skipped=audit["skipped"],
                    error=audit["error"],
                )
                if stored:
                    audit = stored
                    audit["idempotent_replay"] = False
            self._last_analysis_delete_audit = audit
            return audit

        if operation_store is not None and operation_key:
            decision, existing = operation_store.begin_analysis_delete_operation(
                operation_key=operation_key,
                request_sha256=request_sha256,
                table_names=[_safe_delete_table_name(item) for item in clean_names],
                source_name=source_name,
            )
            if decision == "replay":
                replay = dict(existing or {})
                replay["idempotent_replay"] = True
                self._last_analysis_delete_audit = replay
                return str(replay.get("result") or "删除分析表操作已完成（重复请求已复用结果）。")
            if decision == "conflict":
                conflict = _local_audit(
                    "conflict",
                    error="operation_key_reused_with_different_request",
                )
                conflict["conflict_with"] = str(
                    (existing or {}).get("request_sha256") or ""
                )[:64]
                self._last_analysis_delete_audit = conflict
                return "❌ 相同 operation_key 已对应另一组删除请求，已拒绝执行。"
            if decision == "in_progress":
                in_progress = dict(existing or {})
                in_progress["status"] = "in_progress"
                in_progress["idempotent_replay"] = False
                self._last_analysis_delete_audit = in_progress
                return "⏳ 相同 operation_key 的删除操作仍在处理中，已拒绝重复执行。"

        if not confirm:
            result = "❌ 删除分析表需要 confirm=true。"
            _finish(
                "rejected", result=result,
                skipped=[(name, "confirmation required") for name in clean_names],
                error="confirmation_required",
            )
            return result
        if not self.data_source:
            result = "❌ 请先连接数据源。"
            _finish("rejected", result=result, error="data_source_required")
            return result
        if not clean_names:
            result = "❌ 请提供至少一个要删除的分析表名。"
            _finish("rejected", result=result, error="table_names_required")
            return result

        deleted = []
        skipped = []
        try:
            for name in clean_names:
                if abort_check is not None:
                    abort_check()
                operation_timeout = (
                    timeout_provider() if callable(timeout_provider) else timeout
                )
                ok, message = self._drop_analysis_table(
                    self.data_source,
                    name,
                    timeout=operation_timeout,
                    abort_check=abort_check,
                )
                if ok:
                    deleted.append(name)
                else:
                    skipped.append((name, message))
        except JobCanceled:
            _finish(
                "interrupted",
                result="删除分析表操作已中断，未自动重试。",
                deleted=deleted,
                skipped=skipped,
                error="job_canceled",
            )
            raise
        except AgentRunTimeout:
            _finish(
                "interrupted",
                result="删除分析表操作已超时，未自动重试。",
                deleted=deleted,
                skipped=skipped,
                error="agent_run_timeout",
            )
            raise
        if deleted:
            self._schema_cache = None

        lines = ["🗑️ 表清理结果"]
        if deleted:
            lines.extend(["", "已删除的分析表："])
            lines.extend(f"- `{name}`" for name in deleted)
        if skipped:
            lines.extend(["", "未删除的表："])
            lines.extend(f"- `{name}`：{reason}" for name, reason in skipped)
        if not skipped:
            lines.append("")
            lines.append("✅ 指定分析表已清理完成。")
        else:
            lines.append("")
            lines.append("说明：只允许删除可证明为分析/派生表的对象；原始源表和无法判定的表会被保护。")
        result = "\n".join(lines)
        status = "succeeded" if not skipped else ("partial" if deleted else "rejected")
        _finish(status, result=result, deleted=deleted, skipped=skipped)
        return result

    # ── DataFrame → DataSource writer (backward-compatible) ──────────────────

    def _write_analysis_df(
        self,
        df,
        table_name: str,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> None:
        """Write df into the connected data source as a queryable table.

        Tries the new connector API first; falls back to direct SQLite write
        for older connector.py versions that lack the _df parameter.
        """
        ds = self.data_source
        if abort_check is not None:
            abort_check()

        try:
            created = self._execute_source_operation(
                ds,
                lambda: ds.create_analysis_table(
                    sql=None,
                    table_name=table_name,
                    _df=df,
                ),
                timeout=timeout,
                abort_check=abort_check,
            )
            # Some connectors report failures as text instead of raising.  Do
            # not let a caller subsequently claim a derived table was written.
            if isinstance(created, str) and (
                "失败" in created or "error" in created.lower()
            ):
                raise RuntimeError(created)
            self._schema_cache = None
            return
        except TypeError:
            pass  # old connector — fall through to direct SQLite write

        conn = getattr(ds, "_conn", None)
        if conn is None:
            if getattr(ds, "_cache_conn", None) is None:
                ds._cache_conn = sqlite3.connect(":memory:", check_same_thread=False)
                ds._cache_tables = set()
            conn = ds._cache_conn
            ds._cache_tables.add(table_name)

        self._execute_source_operation(
            ds,
            lambda: df.to_sql(table_name, conn, if_exists="replace", index=False),
            timeout=timeout,
            abort_check=abort_check,
        )
        if abort_check is not None:
            abort_check()
        self._schema_cache = None

    def _write_analysis_df_with_budget(
        self,
        df,
        table_name: str,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> None:
        """Call the writer while retaining compatibility with old test/connectors."""
        if timeout is None and abort_check is None:
            # A few integrations override this legacy two-argument hook.
            return self._write_analysis_df(df, table_name)
        return self._write_analysis_df(
            df,
            table_name,
            timeout=timeout,
            abort_check=abort_check,
        )

    # ── Analysis tool ─────────────────────────────────────────────────────────

    def _tool_run_analysis(
        self,
        analysis_name: str,
        sql: str,
        target_column: str,
        groupby_column: str = "",
        n_deciles: int = 10,
        analysis_options: dict | None = None,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> str:
        if abort_check is not None:
            abort_check()
        if not self.data_source:
            return "No data source connected."

        validation_error = self._validate_data_sql("run_analysis", sql)
        if validation_error:
            return f"SQL Error while fetching data: {validation_error}"

        df, error = self._execute_source_query(
            self.data_source,
            sql,
            timeout=timeout,
            abort_check=abort_check,
        )
        if error:
            return f"SQL Error while fetching data: {error}"
        if df.empty:
            return "Query returned no rows — cannot run analysis."

        try:
            entry, ret = _execute_analysis(
                analysis_name,
                df,
                target_column,
                groupby_column,
                n_deciles,
                analysis_options,
            )
        except KeyError as exc:
            return f"Analysis error: {exc}"
        except Exception as exc:
            from agent.errors import AgentRunTimeout
            from agent.jobs import JobCanceled

            if isinstance(exc, (JobCanceled, AgentRunTimeout)):
                raise
            return f"Analysis error: {exc}"

        return self._finalize_analysis_result(
            entry,
            ret,
            analysis_name,
            sql,
            target_column,
            n_deciles,
            timeout=timeout,
            abort_check=abort_check,
        )

    def _tool_run_analysis_with_jobs(
        self,
        analysis_name: str,
        sql: str,
        target_column: str,
        groupby_column: str = "",
        n_deciles: int = 10,
        analysis_options: dict | None = None,
        *,
        timeout: float | None = None,
        abort_check=None,
        timeout_provider=None,
    ):
        """Run large time-series analyses as cancellable JobRunner work."""
        if abort_check is not None:
            abort_check()
        if not self.data_source:
            return "No data source connected."

        validation_error = self._validate_data_sql("run_analysis", sql)
        if validation_error:
            return f"SQL Error while fetching data: {validation_error}"

        query_timeout = (
            timeout_provider() if callable(timeout_provider) else timeout
        )
        df, error = self._execute_source_query(
            self.data_source,
            sql,
            timeout=query_timeout,
            abort_check=abort_check,
        )
        if error:
            return f"SQL Error while fetching data: {error}"
        if df.empty:
            return "Query returned no rows — cannot run analysis."

        should_job = self._job_runner is not None and (
            analysis_name in _JOB_ANALYSIS_IDS
            or (analysis_name.startswith(_TIME_SERIES_PREFIX) and len(df) >= _ANALYSIS_JOB_ROW_THRESHOLD)
        )
        if not should_job:
            if abort_check is not None:
                abort_check()
            try:
                entry, ret = _execute_analysis(
                    analysis_name, df, target_column, groupby_column, n_deciles, analysis_options
                )
            except KeyError as exc:
                return f"Analysis error: {exc}"
            except Exception as exc:
                from agent.errors import AgentRunTimeout
                from agent.jobs import JobCanceled

                if isinstance(exc, (JobCanceled, AgentRunTimeout)):
                    raise
                return f"Analysis error: {exc}"
            return self._finalize_analysis_result(
                entry,
                ret,
                analysis_name,
                sql,
                target_column,
                n_deciles,
                timeout=(timeout_provider() if callable(timeout_provider) else timeout),
                abort_check=abort_check,
            )

        result_holder = {}

        def _worker(ctx):
            def _progress(pct: int, message: str = ""):
                ctx.check_canceled()
                ctx.set_progress(pct, message)

            _progress(2, "正在准备深度学习训练" if analysis_name in _JOB_ANALYSIS_IDS else "正在准备时序分析")
            entry, ret = _execute_analysis(
                analysis_name,
                df,
                target_column,
                groupby_column,
                n_deciles,
                analysis_options=analysis_options,
                progress_callback=_progress,
            )
            ctx.check_canceled()
            result_holder["entry"] = entry
            result_holder["ret"] = ret
            return {
                "analysis_name": analysis_name,
                "input_rows": len(df),
                "output_tables": list(entry.get("output_tables", [])),
            }

        job = yield from self._run_as_job(
            _worker,
            job_type="time_series_analysis",
            label=f"{analysis_name} · {len(df)} rows",
        )
        if job.get("status") == "canceled":
            return "Analysis canceled."
        if job.get("status") != "succeeded":
            return f"Analysis error: {job.get('error') or 'background job failed'}"
        if "ret" not in result_holder:
            return "Analysis error: background result was not available."

        return self._finalize_analysis_result(
            result_holder["entry"],
            result_holder["ret"],
            analysis_name,
            sql,
            target_column,
            n_deciles,
            timeout=(timeout_provider() if callable(timeout_provider) else timeout),
            abort_check=abort_check,
        )

    def _finalize_analysis_result(
        self,
        entry,
        ret,
        analysis_name: str,
        sql: str,
        target_column: str,
        n_deciles: int,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> str:
        """Persist computed tables on the request thread and format the result."""

        if len(ret) == 4:
            result_df, breakdown_df, extra_df, markdown = ret
        else:
            result_df, breakdown_df, markdown = ret
            extra_df = None

        try:
            _out_tbls = entry.get("output_tables", [])
            self._write_analysis_df_with_budget(
                result_df,
                "analysis_result",
                timeout=timeout,
                abort_check=abort_check,
            )
            # Materialize declared result tables even when a valid analysis
            # has no rows for one view (for example, no significant variables
            # or no ROC points).  The model can then inspect an empty table
            # instead of receiving a misleading "table does not exist" error.
            if breakdown_df is not None:
                self._write_analysis_df_with_budget(
                    breakdown_df,
                    "analysis_breakdown",
                    timeout=timeout,
                    abort_check=abort_check,
                )
            # Always write the third table so LLM SQL queries don't fail on missing table.
            # Write an empty-but-structured DataFrame when the result is empty.
            if extra_df is not None:
                extra_table_name = _out_tbls[2] if len(_out_tbls) > 2 else "analysis_extra"
                self._write_analysis_df_with_budget(
                    extra_df,
                    extra_table_name,
                    timeout=timeout,
                    abort_check=abort_check,
                )
        except (JobCanceled, AgentRunTimeout):
            raise
        except Exception as exc:
            return (
                markdown
                + f"\n\n⚠️ **结果表写入失败**：{exc}\n"
                "分析计算已完成，但结果无法存为可查询表格，请联系开发者。"
            )

        try:
            evaluations = []
            evaluation = _model_evaluation_for_result(
                analysis_name,
                result_df,
                breakdown_df,
                extra_df,
                target_column,
            )
            if evaluation is not None:
                evaluations.append(evaluation)
            requested_evaluation = entry.get("__pfs_model_evaluation__")
            if requested_evaluation is not None:
                evaluations.append(requested_evaluation)
        except Exception as exc:
            evaluations = []
            if analysis_name in {
                "Regression",
                "Decision_Tree",
                "Logistic_Regression",
                "Sklearn_Model",
                "Torch_MLP",
            } or analysis_name.startswith(_TIME_SERIES_PREFIX):
                markdown += f"\n\n⚠️ **模型质量评估未生成**：{exc}"
        if evaluations:
            try:
                tables = [_model_evaluation_table(item) for item in evaluations]
                if len(tables) == 1:
                    evaluation_table = tables[0]
                else:
                    import pandas as pd

                    evaluation_table = pd.concat(tables, ignore_index=True)
                self._write_analysis_df_with_budget(
                    evaluation_table,
                    "analysis_evaluation",
                    timeout=timeout,
                    abort_check=abort_check,
                )
            except (JobCanceled, AgentRunTimeout):
                raise
            except Exception as exc:
                markdown += f"\n\n⚠️ **模型质量评估表写入失败**：{exc}"
            for evaluation in evaluations:
                markdown += _model_evaluation_markdown(analysis_name, evaluation)

        if analysis_name == "K_Means" and "cluster" in breakdown_df.columns:
            markdown += self._kmeans_build_labeled(
                sql,
                breakdown_df,
                timeout=timeout,
                abort_check=abort_check,
            )

        if analysis_name == "Data_Decile_Analysis" and "decile" in result_df.columns:
            markdown += self._decile_build_labeled(
                sql,
                target_column,
                n_deciles,
                timeout=timeout,
                abort_check=abort_check,
            )

        output_tables = [
            str(table).strip()
            for table in (entry.get("output_tables") or [])
            if str(table).strip()
        ]
        if output_tables:
            contract = (
                "\n\n---\n"
                "**本次分析已生成的可查询结果表**："
                + "、".join(f"`{table}`" for table in output_tables)
                + "。仅可查询上述表名；如需其他结果，请先确认工具返回的结果表清单。"
            )
            markdown = contract + (f"\n\n{markdown}" if markdown else "")
        return markdown

    def _kmeans_build_labeled(self, sql: str, breakdown_df, *, timeout=None, abort_check=None) -> str:
        try:
            labeled_sql = re.sub(
                r"(?is)\bSELECT\b.+?\bFROM\b",
                "SELECT *\nFROM",
                sql,
                count=1,
            )
            full_df, err = self._execute_source_query(
                self.data_source,
                labeled_sql,
                timeout=timeout,
                abort_check=abort_check,
            )
            if err or full_df.empty:
                return ""
            if len(full_df) != len(breakdown_df):
                return ""

            labeled_df = full_df.copy().reset_index(drop=True)
            labeled_df["cluster"] = breakdown_df["cluster"].values
            self._write_analysis_df_with_budget(
                labeled_df,
                "cluster_labels",
                timeout=timeout,
                abort_check=abort_check,
            )
            self._schema_cache = None

            cols_preview = ", ".join(str(c) for c in labeled_df.columns[:8])
            if len(labeled_df.columns) > 8:
                cols_preview += ", ..."
            return (
                "\n\n---\n"
                "### 📌 数据标签表 `cluster_labels`\n"
                f"已将聚类结果（cluster 列）回写到原始数据，"
                f"生成包含所有原始字段的标签表：\n\n"
                f"**列：** `{cols_preview}`\n\n"
                "可直接用于后续分析，例如：\n"
                "```sql\n"
                "-- 查看各簇的详细记录\n"
                "SELECT * FROM cluster_labels WHERE cluster = 0 LIMIT 20\n\n"
                "-- 统计各簇某字段的均值\n"
                "SELECT cluster, AVG(target_col) AS avg_val FROM cluster_labels GROUP BY cluster\n"
                "```"
            )
        except (JobCanceled, AgentRunTimeout):
            raise
        except Exception:
            return ""

    def _decile_build_labeled(
        self,
        sql: str,
        target_column: str,
        n_deciles: int,
        *,
        timeout=None,
        abort_check=None,
    ) -> str:
        """回写十分位标签到原始数据，生成 decile_labels 表。"""
        try:
            labeled_sql = re.sub(
                r"(?is)\bSELECT\b.+?\bFROM\b",
                "SELECT *\nFROM",
                sql,
                count=1,
            )
            full_df, err = self._execute_source_query(
                self.data_source,
                labeled_sql,
                timeout=timeout,
                abort_check=abort_check,
            )
            if err or full_df.empty:
                return ""

            import pandas as pd
            col = full_df[target_column]
            # 用与 analyze.py 完全一致的逻辑重新打标签
            raw_cut = pd.qcut(
                pd.to_numeric(col, errors="coerce"),
                q=n_deciles,
                duplicates="drop",
            )
            ordered_cats = raw_cut.cat.categories
            cat_to_int = {cat: i + 1 for i, cat in enumerate(ordered_cats)}
            decile_int = raw_cut.map(cat_to_int)
            actual_n = int(decile_int.nunique())

            labeled_df = full_df.copy().reset_index(drop=True)
            labeled_df["decile"] = decile_int.values
            # 生成可读标签，如 "D01 (低)" / "D10 (高)"
            width = len(str(actual_n))
            def _label(d):
                if pd.isna(d):
                    return None
                d = int(d)
                if d == 1:
                    suffix = "（最低）"
                elif d == actual_n:
                    suffix = "（最高）"
                else:
                    suffix = ""
                return f"D{str(d).zfill(width)}{suffix}"
            labeled_df["decile_label"] = labeled_df["decile"].map(_label)

            self._write_analysis_df_with_budget(
                labeled_df,
                "decile_labels",
                timeout=timeout,
                abort_check=abort_check,
            )
            self._schema_cache = None

            cols_preview = ", ".join(str(c) for c in labeled_df.columns[:8])
            if len(labeled_df.columns) > 8:
                cols_preview += ", ..."
            return (
                "\n\n---\n"
                "### 📌 数据标签表 `decile_labels`\n"
                f"已将十分位标签（`decile` + `decile_label`）回写到原始数据，"
                f"共 {len(labeled_df)} 行：\n\n"
                f"**列：** `{cols_preview}`\n\n"
                "可直接导出或用于进一步分析，例如：\n"
                "```sql\n"
                "-- 查看某分位的原始记录\n"
                f"SELECT * FROM decile_labels WHERE decile = 10 LIMIT 20\n\n"
                "-- 各分位均值汇总\n"
                f"SELECT decile, decile_label, AVG({target_column}) AS avg_val\n"
                "FROM decile_labels GROUP BY decile, decile_label ORDER BY decile\n"
                "```"
            )
        except (JobCanceled, AgentRunTimeout):
            raise
        except Exception:
            return ""

    # ── Chart selector ────────────────────────────────────────────────────────

    def _tool_select_chart(
        self,
        user_intent: str,
        available_columns: list = None,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> str:
        """Query the embedded chart registry and return ranked candidates with exact field_mapping specs."""
        try:
            if abort_check is not None:
                abort_check()
            from LLM.chart_selector import select_charts, format_selection_result
            cols = list(available_columns or [])
            # Auto-enrich with schema column names when the caller didn't supply them
            if not cols and self.data_source:
                schema = self._tool_get_schema(
                    timeout=timeout,
                    abort_check=abort_check,
                )
                cols = re.findall(r"^\s{2,4}(\w+)\b", schema, re.MULTILINE)
            candidates = select_charts(user_intent, cols, top_n=3)
            if abort_check is not None:
                abort_check()
            return format_selection_result(candidates)
        except (JobCanceled, AgentRunTimeout):
            raise
        except Exception as exc:
            return f"Chart selection error: {exc}"

    # ── Chart tool ────────────────────────────────────────────────────────────

    def _render_chart_from_df(
        self, df, chart_type: str, field_mapping: dict, title: str = "",
    ) -> dict:
        from chart_generate import generate_chart as _gen

        options = {"title": title} if title else {}
        result = _gen(
            df=df,
            chart_type=chart_type,
            mapping=field_mapping,
            options=options,
            color_scheme=self.ppt_color_scheme,
        )
        if "error" in result:
            return {"error": result["error"]}
        return {"html": result.get("html", ""), "chart_type": chart_type}

    def _tool_generate_chart(
        self,
        chart_type: str,
        sql: str,
        field_mapping: dict,
        title: str = "",
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> dict:
        if abort_check is not None:
            abort_check()
        if not self.data_source:
            return {"error": "No data source connected."}
        validation_error = self._validate_data_sql("generate_chart", sql)
        if validation_error:
            return {"error": f"Data query failed: {validation_error}"}
        df, error = self._execute_source_query(
            self.data_source,
            sql,
            timeout=timeout,
            abort_check=abort_check,
        )
        if error:
            return {"error": f"Data query failed: {error}"}
        if df.empty:
            return {"error": "Query returned no rows — cannot generate chart."}
        return self._render_chart_from_df(df, chart_type, field_mapping, title)

    def _tool_generate_chart_with_jobs(
        self,
        chart_type: str,
        sql: str,
        field_mapping: dict,
        title: str = "",
        *,
        timeout: float | None = None,
        abort_check=None,
        timeout_provider=None,
    ):
        if abort_check is not None:
            abort_check()
        if not self.data_source:
            return {"error": "No data source connected."}
        validation_error = self._validate_data_sql("generate_chart", sql)
        if validation_error:
            return {"error": f"Data query failed: {validation_error}"}
        query_timeout = timeout_provider() if callable(timeout_provider) else timeout
        df, error = self._execute_source_query(
            self.data_source,
            sql,
            timeout=query_timeout,
            abort_check=abort_check,
        )
        if error:
            return {"error": f"Data query failed: {error}"}
        if df.empty:
            return {"error": "Query returned no rows — cannot generate chart."}

        should_job = self._job_runner is not None and len(df) >= _CHART_JOB_ROW_THRESHOLD
        if not should_job:
            if abort_check is not None:
                abort_check()
            return self._render_chart_from_df(df, chart_type, field_mapping, title)

        df_snapshot = df.copy(deep=True)
        mapping_snapshot = dict(field_mapping or {})
        title_snapshot = str(title or "")
        chart_type_snapshot = str(chart_type or "Bar_Chart")
        result_holder = {}

        def _worker(ctx):
            ctx.set_progress(10, "正在准备图表数据")
            ctx.check_canceled()
            ctx.set_progress(35, "正在渲染图表")
            result = self._render_chart_from_df(
                df_snapshot, chart_type_snapshot, mapping_snapshot, title_snapshot
            )
            ctx.check_canceled()
            result_holder["chart"] = result
            ctx.set_progress(100, "图表生成完成")
            return {
                "chart_type": chart_type_snapshot,
                "input_rows": len(df_snapshot),
                "ok": "html" in result,
            }

        job = yield from self._run_as_job(
            _worker,
            job_type="chart_generation",
            label=f"{chart_type_snapshot} · {len(df_snapshot)} rows",
        )
        if job.get("status") == "canceled":
            return {"error": "Chart generation canceled."}
        if job.get("status") != "succeeded":
            return {"error": job.get("error") or "background job failed"}
        return result_holder.get("chart", {"error": "background result was not available"})

    # ── Table discovery helpers ───────────────────────────────────────────────

    def _discover_all_tables(
        self,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> list:
        if abort_check is not None:
            abort_check()
        if not self.data_source:
            return []
        # Preferred: connector.list_tables() — returns ALL tables incl. runtime
        # analysis/derived tables (DuckDB information_schema based).
        list_fn = getattr(self.data_source, "list_tables", None)
        if callable(list_fn):
            try:
                tables = self._execute_source_operation(
                    self.data_source,
                    list_fn,
                    timeout=timeout,
                    abort_check=abort_check,
                )
                if tables:
                    return list(tables)
            except (JobCanceled, AgentRunTimeout):
                raise
            except Exception:
                pass
        # Fallback: parse the schema text (works for any connector).
        schema = self._tool_get_schema(timeout=timeout, abort_check=abort_check)
        return re.findall(r"^Table:\s+(\S+)", schema, re.MULTILINE)

    def _get_first_raw_table(
        self,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> str:
        tables = self._discover_all_tables(timeout=timeout, abort_check=abort_check)
        raw = [t for t in tables if not t.startswith("analysis_") and t != "cleaned_data"]
        return raw[0] if raw else (tables[0] if tables else "")

    # ── Profile & clean ───────────────────────────────────────────────────────

    def _profile_dataframe(self, df, table_name: str, columns: list = None) -> dict:
        from Function.Clean.data_profile import profile
        text, charts = profile(df, columns or None)
        return {"text": f"### 数据概况 · `{table_name}`\n\n" + text, "charts": charts}

    def _tool_profile_data(
        self,
        table_name: str = "",
        columns: list = None,
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> dict:
        if abort_check is not None:
            abort_check()
        if not self.data_source:
            return {"text": "❌ 请先连接数据源。", "charts": []}

        tname = table_name or self._get_first_raw_table(
            timeout=timeout,
            abort_check=abort_check,
        )
        if abort_check is not None:
            abort_check()
        if not tname:
            return {"text": "❌ 数据源中没有可用的表格。", "charts": []}

        df, err = self._execute_source_query(
            self.data_source,
            f'SELECT * FROM "{tname}"',
            timeout=timeout,
            abort_check=abort_check,
        )
        if abort_check is not None:
            abort_check()
        if err or df is None or df.empty:
            return {"text": f"❌ 读取表 '{tname}' 失败：{err}", "charts": []}

        try:
            result = self._profile_dataframe(df, tname, columns)
            if abort_check is not None:
                abort_check()
            return result
        except (JobCanceled, AgentRunTimeout):
            raise
        except Exception as exc:
            return {"text": f"❌ 数据概况生成失败：{exc}", "charts": []}

    def _tool_profile_data_with_jobs(
        self,
        table_name: str = "",
        columns: list = None,
        *,
        timeout: float | None = None,
        abort_check=None,
        timeout_provider=None,
    ):
        if abort_check is not None:
            abort_check()
        if not self.data_source:
            return {"text": "❌ 请先连接数据源。", "charts": []}

        query_timeout = timeout_provider() if callable(timeout_provider) else timeout
        tname = table_name or self._get_first_raw_table(
            timeout=query_timeout,
            abort_check=abort_check,
        )
        if not tname:
            return {"text": "❌ 数据源中没有可用的表格。", "charts": []}

        df, err = self._execute_source_query(
            self.data_source,
            f'SELECT * FROM "{tname}"',
            timeout=query_timeout,
            abort_check=abort_check,
        )
        if err or df is None or df.empty:
            return {"text": f"❌ 读取表 '{tname}' 失败：{err}", "charts": []}

        should_job = (
            self._job_runner is not None
            and (
                len(df) >= _PROFILE_JOB_ROW_THRESHOLD
                or len(df.columns) >= _PROFILE_JOB_COLUMN_THRESHOLD
            )
        )
        if not should_job:
            if abort_check is not None:
                abort_check()
            try:
                return self._profile_dataframe(df, tname, columns)
            except Exception as exc:
                return {"text": f"❌ 数据概况生成失败：{exc}", "charts": []}

        df_snapshot = df.copy(deep=True)
        columns_snapshot = list(columns or [])
        table_snapshot = str(tname)
        result_holder = {}

        def _worker(ctx):
            ctx.set_progress(10, "正在准备数据概况")
            ctx.check_canceled()
            ctx.set_progress(40, "正在计算字段统计")
            result = self._profile_dataframe(df_snapshot, table_snapshot, columns_snapshot)
            ctx.check_canceled()
            result_holder["profile"] = result
            ctx.set_progress(100, "数据概况生成完成")
            return {
                "table": table_snapshot,
                "input_rows": len(df_snapshot),
                "input_columns": len(df_snapshot.columns),
                "charts": len(result.get("charts", [])),
            }

        job = yield from self._run_as_job(
            _worker,
            job_type="data_profile",
            label=f"{table_snapshot} · {len(df_snapshot)} rows",
        )
        if job.get("status") == "canceled":
            return {"text": "数据概况生成已取消。", "charts": []}
        if job.get("status") != "succeeded":
            return {"text": f"❌ 数据概况生成失败：{job.get('error') or 'background job failed'}", "charts": []}
        return result_holder.get("profile", {"text": "❌ 数据概况生成失败：后台结果不可用。", "charts": []})

    def _clean_dataframe(
        self,
        df,
        operation: str,
        columns=None,
        fill_method: str = "mean",
        lower_pct: float = 1.0,
        upper_pct: float = 99.0,
        trim_column: str = "",
        min_val=None,
        max_val=None,
    ):
        if operation == "fill_na":
            from Function.Clean.missing_handler import fill_missing
            return fill_missing(df, fill_method, columns)
        if operation == "winsorize":
            from Function.Clean.winsorize import winsorize
            return winsorize(df, lower_pct, upper_pct, columns)
        if operation == "trimming":
            if not trim_column:
                raise ValueError("trimming 操作需要指定 trim_column。")
            if min_val is None or max_val is None:
                raise ValueError("trimming 操作需要同时指定 min_val 和 max_val。")
            from Function.Clean.trimming import trim
            return trim(df, trim_column, float(min_val), float(max_val))
        raise ValueError(f"未知操作 '{operation}'，支持：fill_na / winsorize / trimming")

    def _tool_clean_data(
        self,
        operation: str,
        table_name: str = "",
        columns=None,
        fill_method: str = "mean",
        lower_pct: float = 1.0,
        upper_pct: float = 99.0,
        trim_column: str = "",
        min_val=None,
        max_val=None,
        output_table: str = "cleaned_data",
        *,
        timeout: float | None = None,
        abort_check=None,
    ) -> str:
        if abort_check is not None:
            abort_check()
        if not self.data_source:
            return "❌ 请先连接数据源。"

        tname = table_name or self._get_first_raw_table(
            timeout=timeout,
            abort_check=abort_check,
        )
        if not tname:
            return "❌ 数据源中没有可用的表格。"

        df, err = self._execute_source_query(
            self.data_source,
            f'SELECT * FROM "{tname}"',
            timeout=timeout,
            abort_check=abort_check,
        )
        if err or df is None or df.empty:
            return f"❌ 读取表 '{tname}' 失败：{err}"

        try:
            cleaned_df, summary = self._clean_dataframe(
                df, operation, columns, fill_method, lower_pct, upper_pct,
                trim_column, min_val, max_val,
            )
        except Exception as exc:
            return f"❌ 清洗失败：{exc}"

        try:
            self._write_analysis_df_with_budget(
                cleaned_df,
                output_table,
                timeout=timeout,
                abort_check=abort_check,
            )
            self._schema_cache = None
        except (JobCanceled, AgentRunTimeout):
            raise
        except Exception as exc:
            return summary + f"\n\n⚠️ 结果表写入失败：{exc}"

        return (
            summary
            + f"\n\n✅ 清洗结果已保存为表 `{output_table}`，可直接用于后续分析和图表生成。"
        )

    def _tool_clean_data_with_jobs(
        self,
        operation: str,
        table_name: str = "",
        columns=None,
        fill_method: str = "mean",
        lower_pct: float = 1.0,
        upper_pct: float = 99.0,
        trim_column: str = "",
        min_val=None,
        max_val=None,
        output_table: str = "cleaned_data",
        *,
        timeout: float | None = None,
        abort_check=None,
        timeout_provider=None,
    ):
        if abort_check is not None:
            abort_check()
        if not self.data_source:
            return "❌ 请先连接数据源。"

        query_timeout = timeout_provider() if callable(timeout_provider) else timeout
        tname = table_name or self._get_first_raw_table(
            timeout=query_timeout,
            abort_check=abort_check,
        )
        if not tname:
            return "❌ 数据源中没有可用的表格。"

        df, err = self._execute_source_query(
            self.data_source,
            f'SELECT * FROM "{tname}"',
            timeout=query_timeout,
            abort_check=abort_check,
        )
        if err or df is None or df.empty:
            return f"❌ 读取表 '{tname}' 失败：{err}"

        should_job = self._job_runner is not None and len(df) >= _CLEAN_JOB_ROW_THRESHOLD
        if not should_job:
            if abort_check is not None:
                abort_check()
            try:
                cleaned_df, summary = self._clean_dataframe(
                    df, operation, columns, fill_method, lower_pct, upper_pct,
                    trim_column, min_val, max_val,
                )
            except Exception as exc:
                return f"❌ 清洗失败：{exc}"
        else:
            df_snapshot = df.copy(deep=True)
            columns_snapshot = list(columns or [])
            params = {
                "operation": operation,
                "fill_method": fill_method,
                "lower_pct": lower_pct,
                "upper_pct": upper_pct,
                "trim_column": trim_column,
                "min_val": min_val,
                "max_val": max_val,
            }
            result_holder = {}

            def _worker(ctx):
                ctx.set_progress(10, "正在准备清洗数据")
                ctx.check_canceled()
                ctx.set_progress(45, "正在执行清洗计算")
                cleaned, summary_text = self._clean_dataframe(
                    df_snapshot,
                    params["operation"],
                    columns_snapshot,
                    params["fill_method"],
                    params["lower_pct"],
                    params["upper_pct"],
                    params["trim_column"],
                    params["min_val"],
                    params["max_val"],
                )
                ctx.check_canceled()
                result_holder["cleaned_df"] = cleaned
                result_holder["summary"] = summary_text
                ctx.set_progress(100, "清洗计算完成")
                return {
                    "operation": params["operation"],
                    "input_rows": len(df_snapshot),
                    "output_rows": len(cleaned),
                }

            job = yield from self._run_as_job(
                _worker,
                job_type="data_cleaning",
                label=f"{operation} · {len(df_snapshot)} rows",
            )
            if job.get("status") == "canceled":
                return "数据清洗已取消。"
            if job.get("status") != "succeeded":
                return f"❌ 清洗失败：{job.get('error') or 'background job failed'}"
            cleaned_df = result_holder.get("cleaned_df")
            summary = result_holder.get("summary", "")
            if cleaned_df is None:
                return "❌ 清洗失败：后台结果不可用。"

        write_timeout = timeout_provider() if callable(timeout_provider) else timeout
        try:
            self._write_analysis_df_with_budget(
                cleaned_df,
                output_table,
                timeout=write_timeout,
                abort_check=abort_check,
            )
            self._schema_cache = None
        except (JobCanceled, AgentRunTimeout):
            raise
        except Exception as exc:
            return summary + f"\n\n⚠️ 结果表写入失败：{exc}"

        return (
            summary
            + f"\n\n✅ 清洗结果已保存为表 `{output_table}`，可直接用于后续分析和图表生成。"
        )
