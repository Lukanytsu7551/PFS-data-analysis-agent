"""Session-scoped audit aggregation for PFS analysis runs.

The module is intentionally independent from Flask and persistence adapters.
Callers provide data that has already been isolated to one session; this layer
projects it into a bounded, path-free contract for the desktop audit console.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping


_TERMINAL = {"succeeded", "failed", "canceled"}
_KNOWN_TYPES = {
    "all", "job", "run", "model", "tool", "retry", "error", "approval",
    "artifact", "claim", "evidence", "cost",
}


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _number(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> str:
    """Normalize ISO strings and unix timestamps without inventing a time."""
    if value in {None, ""}:
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return ""
    raw = _text(value, 80)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _timestamp(value: Any) -> float:
    normalized = _iso(value)
    if not normalized:
        return 0.0
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _duration_ms(start: Any, end: Any) -> int | None:
    left = _timestamp(start)
    right = _timestamp(end)
    if not left or not right or right < left:
        return None
    return int((right - left) * 1000)


def _job_projection(raw: Mapping[str, Any]) -> dict[str, Any]:
    result = raw.get("result") if isinstance(raw.get("result"), Mapping) else {}
    return {
        "id": _text(raw.get("id") or raw.get("job_id"), 160),
        "type": _text(raw.get("type") or raw.get("job_type"), 100),
        "label": _text(raw.get("label"), 200),
        "parent_id": _text(raw.get("parent_id"), 160),
        "workspace_id": _text(raw.get("workspace_id"), 160),
        "status": _text(raw.get("status") or "created", 40),
        "progress": min(100, _number(raw.get("progress"))),
        "message": _text(raw.get("message"), 300),
        "error": _text(raw.get("error"), 500),
        "created_at": _iso(raw.get("created_at")),
        "updated_at": _iso(raw.get("updated_at")),
        "started_at": _iso(raw.get("started_at")),
        "finished_at": _iso(raw.get("finished_at")),
        "duration_ms": _duration_ms(raw.get("started_at"), raw.get("finished_at")),
        "answer_available": bool(result.get("answer")),
        "step_count": _number(result.get("step_count")),
    }


def _cost_projection(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    estimated = raw.get("estimated")
    if estimated is not None and not isinstance(estimated, bool):
        estimated = None
    providers = raw.get("providers") if isinstance(raw.get("providers"), (list, tuple, set)) else []
    models = raw.get("models") if isinstance(raw.get("models"), (list, tuple, set)) else []
    return {
        "amount": _float(raw.get("amount")),
        "currency": _text(raw.get("currency") or "USD", 12),
        "estimated": estimated,
        "source": _text(raw.get("source"), 80),
        "billing_verified": bool(raw.get("billing_verified", False)),
        "model_calls": _number(raw.get("model_calls")),
        "input_tokens": _number(raw.get("input_tokens")),
        "output_tokens": _number(raw.get("output_tokens")),
        "cached_input_tokens": _number(raw.get("cached_input_tokens")),
        "providers": [_text(item, 120) for item in providers if item],
        "models": [_text(item, 160) for item in models if item],
        "status": _text(raw.get("status"), 40),
    }


def _artifact_projection(raw: Mapping[str, Any]) -> dict[str, Any]:
    cost = _cost_projection(raw.get("cost"))
    return {
        "id": _text(raw.get("id") or raw.get("artifact_id"), 180),
        "type": _text(raw.get("type"), 60),
        "created_at": _iso(raw.get("created_at")),
        "run_id": _text(raw.get("run_id"), 160),
        "source_id": _text(raw.get("source_id"), 160),
        "worksheet": _text(raw.get("worksheet"), 160),
        "size_bytes": _number(raw.get("size_bytes")),
        "included_rows": _number(raw.get("included_rows")),
        "sha256": _text(raw.get("sha256"), 64),
        "source_sha256": _text(raw.get("source_sha256"), 64),
        "claim_ids": [_text(item, 180) for item in raw.get("claim_ids") or [] if item],
        "evidence_ids": [_text(item, 180) for item in raw.get("evidence_ids") or [] if item],
        "warnings": [_text(item, 300) for item in raw.get("warnings") or [] if item],
        "download_count": _number(raw.get("download_count")),
        "cost": cost,
    }


def _claim_projection(raw: Mapping[str, Any]) -> dict[str, Any]:
    links = []
    for item in raw.get("evidence_links") or []:
        if not isinstance(item, Mapping):
            continue
        links.append({
            "evidence_id": _text(item.get("evidence_id"), 180),
            "relation": _text(item.get("relation"), 20),
            "confidence": _float(item.get("confidence")) or 0.0,
            "verification_reason": _text(item.get("verification_reason"), 500),
        })
    return {
        "claim_id": _text(raw.get("claim_id"), 180),
        "task_id": _text(raw.get("task_id"), 240),
        "text": _text(raw.get("text"), 1000),
        "status": _text(raw.get("status") or "unverified", 40),
        "confidence": _float(raw.get("confidence")) or 0.0,
        "verification_reason": _text(raw.get("verification_reason"), 800),
        "human_decision": _text(raw.get("human_decision"), 120),
        "evidence_links": links,
        "evidence_ids": [item["evidence_id"] for item in links if item["evidence_id"]],
    }


def _evidence_projection(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": _text(raw.get("evidence_id"), 180),
        "task_id": _text(raw.get("task_id"), 240),
        "title": _text(raw.get("title"), 300),
        "publisher": _text(raw.get("publisher"), 200),
        "source_url": _text(raw.get("source_url"), 1000),
        "source_type": _text(raw.get("source_type"), 80),
        "trust_level": _text(raw.get("trust_level"), 80),
        "captured_at": _iso(raw.get("captured_at")),
        "published_at": _iso(raw.get("published_at")),
        "snippet": _text(raw.get("snippet"), 1200),
        "content_sha256": _text(raw.get("content_sha256"), 64),
        "source_id": _text(raw.get("source_id"), 160),
        "file_name": _text(raw.get("file_name"), 240),
        "worksheet": _text(raw.get("worksheet"), 160),
        "included_rows": _number(raw.get("included_rows")),
        "locator": _text(raw.get("locator"), 300),
        "date_from": _text(raw.get("date_from"), 80),
        "date_to": _text(raw.get("date_to"), 80),
        "columns": [_text(item, 160) for item in raw.get("columns") or [] if item],
    }


def _event_kind(event_type: str) -> str:
    lower = event_type.lower()
    if "retry" in lower:
        return "retry"
    if "error" in lower or "failed" in lower:
        return "error"
    if lower.startswith("conversation_step") or lower == "tool_audit":
        return "tool"
    if "approval" in lower or "decision" in lower:
        return "approval"
    if "artifact" in lower:
        return "artifact"
    if lower.startswith("conversation_"):
        return "run"
    return "job"


def _safe_identifier(value: Any, limit: int = 160) -> str:
    """Keep an audit identifier bounded and free of path-shaped values."""
    raw = _text(value, limit)
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[/\\]", raw):
        return "<path-redacted>"
    return raw


def _analysis_delete_projection(raw: Mapping[str, Any]) -> dict[str, Any]:
    skipped = []
    for item in raw.get("skipped") or []:
        if not isinstance(item, Mapping):
            continue
        skipped.append({
            "table_name": _safe_identifier(item.get("table_name")),
            "reason_code": _text(item.get("reason_code") or "unknown", 80),
        })
    return {
        "operation_key": _text(raw.get("operation_key"), 240),
        "request_sha256": _text(raw.get("request_sha256"), 64),
        "source_name": _safe_identifier(raw.get("source_name")),
        "table_names": [
            _safe_identifier(item) for item in (raw.get("table_names") or [])[:32]
        ],
        "status": _text(raw.get("status") or "running", 40),
        "deleted": [
            _safe_identifier(item) for item in (raw.get("deleted") or [])[:32]
        ],
        "skipped": skipped[:32],
        "created_at": _iso(raw.get("created_at")),
        "finished_at": _iso(raw.get("finished_at")),
        "error": _text(raw.get("error"), 120),
        "idempotent_replay": bool(raw.get("idempotent_replay", False)),
    }


def _event_title(raw: Mapping[str, Any], kind: str) -> str:
    if kind == "tool":
        return _text(raw.get("display") or raw.get("tool") or "工具调用", 200)
    if kind == "error":
        return "运行失败"
    if kind == "retry":
        return "正在重试"
    if kind == "artifact":
        return "生成交付物"
    labels = {
        "job_created": "任务已创建", "job_started": "任务开始执行",
        "job_progress": "任务进度更新", "job_done": "任务执行完成",
        "job_canceled": "任务已取消", "conversation_activation": "分析策略已激活",
    }
    return labels.get(_text(raw.get("type"), 100), _text(raw.get("type") or "任务事件", 160))


def _timeline_event(raw: Mapping[str, Any]) -> dict[str, Any]:
    event_type = _text(raw.get("type"), 100)
    kind = _event_kind(event_type)
    status = _text(raw.get("status"), 40)
    if not status:
        if event_type.endswith("_finished"):
            status = "succeeded" if not raw.get("error") else "failed"
        elif event_type in {"job_error"}:
            status = "failed"
        elif event_type in {"job_done"}:
            status = "succeeded"
        else:
            status = "running"
    elapsed = _float(raw.get("elapsed_seconds"))
    return {
        "id": f"{_text(raw.get('job_id'), 160)}:{_number(raw.get('sequence'))}",
        "kind": kind,
        "type": event_type,
        "title": _event_title(raw, kind),
        "status": status,
        "created_at": _iso(raw.get("created_at")),
        "sequence": _number(raw.get("sequence")),
        "job_id": _text(raw.get("job_id"), 160),
        "run_id": _text(raw.get("run_id"), 160),
        "artifact_id": _text(raw.get("artifact_id"), 180),
        "claim_id": _text(raw.get("claim_id"), 180),
        "evidence_id": _text(raw.get("evidence_id"), 180),
        "duration_ms": int(elapsed * 1000) if elapsed is not None else None,
        "error": _text(raw.get("error"), 500),
        "metadata": {
            key: value for key, value in {
                "tool": _text(raw.get("tool"), 120),
                "progress": _number(raw.get("progress")) if raw.get("progress") is not None else None,
                "message": _text(raw.get("message"), 240),
                "step_number": _number(raw.get("step_number")) if raw.get("step_number") is not None else None,
                "provider": _text(raw.get("provider"), 120),
                "model": _text(raw.get("model"), 160),
                "attempt": _number(raw.get("attempt")) if raw.get("attempt") is not None else None,
                "max_retries": _number(raw.get("max_retries")) if raw.get("max_retries") is not None else None,
                "wait_seconds": _float(raw.get("wait_seconds")),
                "reason": _text(raw.get("reason"), 80),
                "error_type": _text(raw.get("error_type"), 120),
            }.items() if value not in {None, ""}
        },
    }


def _cost_summary(artifacts: list[dict[str, Any]], model_calls: int) -> dict[str, Any]:
    run_costs: dict[str, Mapping[str, Any]] = {}
    for artifact in artifacts:
        cost = artifact.get("cost")
        if not isinstance(cost, Mapping):
            continue
        key = artifact.get("run_id") or artifact.get("id")
        run_costs.setdefault(str(key), cost)
    if model_calls == 0:
        return {
            "amount": 0.0, "currency": "USD", "known": True,
            "estimated": False, "billing_verified": False,
            "source": "no_model_calls",
        }
    if not run_costs:
        return {
            "amount": None, "currency": "USD", "known": False,
            "estimated": None, "billing_verified": False,
            "source": "usage_without_pricing",
        }
    values = list(run_costs.values())
    amounts = [_float(item.get("amount")) for item in values]
    known = all(amount is not None for amount in amounts) and all(
        item.get("source") == "provider_usage_configured_pricing" for item in values
    )
    return {
        "amount": round(sum(amount for amount in amounts if amount is not None), 8) if known else None,
        "currency": _text(values[0].get("currency") or "USD", 12),
        "known": known,
        "estimated": True if known else None,
        "billing_verified": False,
        "source": "configured_pricing" if known else "price_unknown_or_incomplete",
    }


def _matches_time(item: Mapping[str, Any], start: float, end: float) -> bool:
    if not start and not end:
        return True
    value = next((_timestamp(item.get(key)) for key in (
        "created_at", "captured_at", "recorded_at", "at", "started_at",
    ) if item.get(key)), 0.0)
    if not value:
        return False
    return (not start or value >= start) and (not end or value <= end)


def _search_blob(item: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key, value in item.items():
        if key in {"cost", "metadata"} and isinstance(value, Mapping):
            values.extend(_text(part, 300) for part in value.values())
        elif isinstance(value, (str, int, float)):
            values.append(str(value))
        elif isinstance(value, list):
            values.extend(str(part) for part in value if isinstance(part, (str, int, float)))
    return " ".join(values).lower()


def _filter_items(
    items: list[dict[str, Any]], *, query: str, status: str, start: float, end: float,
) -> list[dict[str, Any]]:
    needle = query.lower().strip()
    return [
        item for item in items
        if (not needle or needle in _search_blob(item))
        and (status == "all" or str(item.get("status") or "") == status)
        and _matches_time(item, start, end)
    ]


def build_session_audit(
    *,
    session_id: str,
    jobs: Iterable[Mapping[str, Any]] = (),
    events: Iterable[Mapping[str, Any]] = (),
    artifacts: Iterable[Mapping[str, Any]] = (),
    claims: Iterable[Mapping[str, Any]] = (),
    evidence: Iterable[Mapping[str, Any]] = (),
    lifecycle_events: Iterable[Mapping[str, Any]] = (),
    usage_breakdowns: Iterable[Mapping[str, Any]] = (),
    command_metrics: Iterable[Mapping[str, Any]] = (),
    analysis_delete_operations: Iterable[Mapping[str, Any]] = (),
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable unified audit response for one already-isolated session."""
    requested = dict(filters or {})
    audit_type = _text(requested.get("type") or "all", 30).lower()
    if audit_type not in _KNOWN_TYPES:
        audit_type = "all"
    status = _text(requested.get("status") or "all", 40).lower()
    query = _text(requested.get("query"), 200)
    start = _timestamp(requested.get("from"))
    end = _timestamp(requested.get("to"))
    limit = min(1000, max(1, _number(requested.get("limit"), 300)))

    job_items = [_job_projection(item) for item in jobs]
    artifact_items = [_artifact_projection(item) for item in artifacts]
    claim_items = [_claim_projection(item) for item in claims]
    evidence_items = [_evidence_projection(item) for item in evidence]
    analysis_delete_items = [
        _analysis_delete_projection(item)
        for item in analysis_delete_operations
        if isinstance(item, Mapping)
    ]

    evidence_by_id = {item["evidence_id"]: item for item in evidence_items}
    for claim in claim_items:
        claim["evidence"] = [
            evidence_by_id[evidence_id]
            for evidence_id in claim["evidence_ids"] if evidence_id in evidence_by_id
        ]
        relations = {item.get("relation") for item in claim["evidence_links"]}
        claim["conflict"] = {"supports", "refutes"}.issubset(relations)
        claim["unsupported"] = not claim["evidence_links"]
        if claim["conflict"]:
            claim["status"] = "conflict"
        elif claim["unsupported"]:
            claim["status"] = "unsupported"

    timeline = [_timeline_event(item) for item in events]
    for artifact in artifact_items:
        timeline.append({
            "id": f"artifact:{artifact['id']}", "kind": "artifact", "type": "artifact_registered",
            "title": f"登记 {artifact['type'] or '交付物'}", "status": "succeeded",
            "created_at": artifact["created_at"], "sequence": 0,
            "job_id": artifact["run_id"], "run_id": artifact["run_id"],
            "artifact_id": artifact["id"], "claim_id": "", "evidence_id": "",
            "duration_ms": None, "error": "", "metadata": {"size_bytes": artifact["size_bytes"]},
        })
    for claim in claim_items:
        timeline.append({
            "id": f"claim:{claim['claim_id']}", "kind": "claim", "type": "claim_recorded",
            "title": claim["text"] or "登记分析结论", "status": claim["status"],
            "created_at": "", "sequence": 0, "job_id": "",
            "run_id": claim["task_id"].removeprefix(f"{session_id}:"),
            "artifact_id": "", "claim_id": claim["claim_id"], "evidence_id": "",
            "duration_ms": None, "error": "", "metadata": {"confidence": claim["confidence"]},
        })
    for item in evidence_items:
        timeline.append({
            "id": f"evidence:{item['evidence_id']}", "kind": "evidence", "type": "evidence_registered",
            "title": item["title"] or item["file_name"] or "登记分析证据", "status": "succeeded",
            "created_at": item["captured_at"], "sequence": 0, "job_id": "",
            "run_id": item["task_id"].removeprefix(f"{session_id}:"),
            "artifact_id": "", "claim_id": "", "evidence_id": item["evidence_id"],
            "duration_ms": None, "error": "", "metadata": {"source_type": item["source_type"]},
        })
    for item in analysis_delete_items:
        created_at = item["finished_at"] or item["created_at"]
        timeline.append({
            "id": f"analysis-delete:{item['operation_key']}:{item['created_at']}",
            "kind": "tool", "type": "analysis_table_delete",
            "title": "删除分析表",
            "status": item["status"], "created_at": created_at, "sequence": 0,
            "job_id": "", "run_id": "", "artifact_id": "", "claim_id": "",
            "evidence_id": "",
            "duration_ms": _duration_ms(item["created_at"], item["finished_at"]),
            "error": item["error"],
            "metadata": {
                "operation_key": item["operation_key"],
                "request_sha256": item["request_sha256"],
                "source_name": item["source_name"],
                "table_names": item["table_names"],
                "deleted": item["deleted"],
                "skipped": item["skipped"],
                "idempotent_replay": item["idempotent_replay"],
            },
        })

    approvals = []
    for index, raw in enumerate(lifecycle_events):
        if _text(raw.get("event")) != "claim_decision":
            continue
        approval = {
            "id": f"approval:{_text(raw.get('claim_id'), 180)}:{index}",
            "claim_id": _text(raw.get("claim_id"), 180),
            "task_id": _text(raw.get("task_id"), 240),
            "decision": _text(raw.get("decision"), 120),
            "previous_decision": _text(raw.get("previous_decision"), 120),
            "reason": _text(raw.get("reason"), 800),
            "status": "succeeded" if raw.get("decision") else "pending",
            "at": _iso(raw.get("at")),
        }
        approvals.append(approval)
        timeline.append({
            "id": approval["id"], "kind": "approval", "type": "claim_decision",
            "title": f"人工裁决：{approval['decision'] or '待处理'}", "status": approval["status"],
            "created_at": approval["at"], "sequence": 0, "job_id": "",
            "run_id": approval["task_id"].removeprefix(f"{session_id}:"),
            "artifact_id": "", "claim_id": approval["claim_id"], "evidence_id": "",
            "duration_ms": None, "error": "", "metadata": {"reason": approval["reason"]},
        })

    usage_items = []
    for index, raw in enumerate(usage_breakdowns):
        usage = {
            "id": f"model:{index}", "provider": _text(raw.get("provider"), 120),
            "model": _text(raw.get("model"), 160), "status": "succeeded",
            "recorded_at": _iso(raw.get("recorded_at")),
            "input_tokens": _number(raw.get("actual_prompt_tokens")),
            "output_tokens": _number(raw.get("actual_completion_tokens")),
            "cached_input_tokens": _number(raw.get("cached_input_tokens")),
            "iteration": _number(raw.get("iteration")),
            "workflow_stage": _text(raw.get("workflow_stage"), 100),
        }
        usage_items.append(usage)
        timeline.append({
            "id": usage["id"], "kind": "model", "type": "model_call",
            "title": "模型调用" + (f" · {usage['model']}" if usage["model"] else ""),
            "status": "succeeded", "created_at": usage["recorded_at"], "sequence": index + 1,
            "job_id": "", "run_id": "", "artifact_id": "", "claim_id": "", "evidence_id": "",
            "duration_ms": None, "error": "", "metadata": {
                "provider": usage["provider"], "model": usage["model"],
                "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
                "cached_input_tokens": usage["cached_input_tokens"],
            },
        })

    commands = []
    for index, raw in enumerate(command_metrics):
        command = {
            "id": f"command:{index}", "command": _text(raw.get("command"), 80),
            "command_type": _text(raw.get("command_type"), 40),
            "status": "succeeded" if raw.get("outcome") == "success" else "failed",
            "outcome": _text(raw.get("outcome"), 40),
            "duration_ms": _number(raw.get("duration_ms")),
            "error_code": _text(raw.get("error_code"), 100),
            "recorded_at": _iso(raw.get("recorded_at")),
        }
        commands.append(command)
        timeline.append({
            "id": command["id"], "kind": "tool", "type": "command_metric",
            "title": f"命令 /{command['command']}", "status": command["status"],
            "created_at": command["recorded_at"], "sequence": index + 1,
            "job_id": "", "run_id": "", "artifact_id": "", "claim_id": "", "evidence_id": "",
            "duration_ms": command["duration_ms"], "error": command["error_code"],
            "metadata": {"command_type": command["command_type"], "outcome": command["outcome"]},
        })

    conflicts = [item for item in claim_items if item["conflict"]]
    uncovered = [item for item in claim_items if item["unsupported"]]
    failed_jobs = sum(item["status"] == "failed" for item in job_items)
    model_calls = len(usage_items)
    cost = _cost_summary(artifact_items, model_calls)
    if model_calls:
        latest_usage_at = max(
            (item.get("recorded_at") or "" for item in usage_items), default="",
        )
        timeline.append({
            "id": "cost:session", "kind": "cost", "type": "cost_summary",
            "title": "模型费用已汇总" if cost["known"] else "模型费用待补充单价",
            "status": "succeeded" if cost["known"] else "pending",
            "created_at": latest_usage_at, "sequence": 0, "job_id": "", "run_id": "",
            "artifact_id": "", "claim_id": "", "evidence_id": "",
            "duration_ms": None, "error": "", "metadata": {
                "amount": cost["amount"], "currency": cost["currency"],
                "estimated": cost["estimated"], "billing_verified": False,
            },
        })
    timeline.sort(
        key=lambda item: (_timestamp(item.get("created_at")), _number(item.get("sequence"))),
        reverse=True,
    )
    total_duration = sum(item.get("duration_ms") or 0 for item in job_items)
    warnings = []
    if model_calls and not cost["known"]:
        warnings.append("模型用量已记录，但单价未完整配置，费用保持未知。")
    if uncovered:
        warnings.append(f"有 {len(uncovered)} 条结论尚未关联证据。")
    if conflicts:
        warnings.append(f"有 {len(conflicts)} 条结论同时存在支持与反驳证据，等待裁决。")

    full_summary = {
        "jobs": len(job_items),
        "succeeded_jobs": sum(item["status"] == "succeeded" for item in job_items),
        "failed_jobs": failed_jobs,
        "canceled_jobs": sum(item["status"] == "canceled" for item in job_items),
        "active_jobs": sum(item["status"] not in _TERMINAL for item in job_items),
        "artifacts": len(artifact_items), "claims": len(claim_items),
        "evidence": len(evidence_items), "uncovered_claims": len(uncovered),
        "conflicts": len(conflicts), "approvals": len(approvals),
        "analysis_delete_operations": len(analysis_delete_items),
        "model_calls": model_calls,
        "input_tokens": sum(item["input_tokens"] for item in usage_items),
        "output_tokens": sum(item["output_tokens"] for item in usage_items),
        "cached_input_tokens": sum(item["cached_input_tokens"] for item in usage_items),
        "total_duration_ms": total_duration,
        "errors": failed_jobs + sum(item["status"] == "failed" for item in commands),
        "cost": cost,
    }

    filtered_timeline = _filter_items(timeline, query=query, status=status, start=start, end=end)
    if audit_type != "all":
        filtered_timeline = [item for item in filtered_timeline if item.get("kind") == audit_type]
    filtered_timeline = filtered_timeline[:limit]

    return {
        "ok": True,
        "session_id": _text(session_id, 160),
        "summary": full_summary,
        "filters": {
            "type": audit_type, "status": status, "query": query,
            "from": _iso(requested.get("from")), "to": _iso(requested.get("to")), "limit": limit,
        },
        "timeline": filtered_timeline,
        "jobs": _filter_items(job_items, query=query, status=status, start=start, end=end),
        "artifacts": _filter_items(artifact_items, query=query, status="all", start=start, end=end),
        "claims": _filter_items(claim_items, query=query, status=status, start=start, end=end),
        "evidence": _filter_items(evidence_items, query=query, status="all", start=start, end=end),
        "approvals": _filter_items(approvals, query=query, status=status, start=start, end=end),
        "model_calls": _filter_items(usage_items, query=query, status=status, start=start, end=end),
        "commands": _filter_items(commands, query=query, status=status, start=start, end=end),
        "analysis_delete_operations": _filter_items(
            analysis_delete_items, query=query, status=status, start=start, end=end,
        ),
        "uncovered_claims": _filter_items(uncovered, query=query, status=status, start=start, end=end),
        "conflicts": _filter_items(conflicts, query=query, status=status, start=start, end=end),
        "cost": cost,
        "warnings": warnings,
    }
