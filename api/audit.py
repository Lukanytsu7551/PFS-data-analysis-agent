"""Session-isolated unified audit API."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from infrastructure.artifact_lifecycle import list_registered_artifacts
from pfs_agent.audit import build_session_audit

from .state import require_session_ownership, session_manager


bp = Blueprint("audit", __name__)


def _all_job_events(runner) -> list[dict]:
    events: list[dict] = []
    cursor = 0
    for _ in range(20):
        page = runner.list_events(after_sequence=cursor, limit=1000)
        if not page:
            break
        events.extend(page)
        next_cursor = int(page[-1].get("sequence") or cursor)
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if cursor >= runner.last_sequence:
            break
    return events


@bp.get("/api/session/<sid>/audit")
@require_session_ownership
def get_session_audit(sid: str):
    """Return one session's safe task, artifact and cost trail."""
    try:
        limit = int(request.args.get("limit", "300"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "limit 必须是整数"}), 400
    if not 1 <= limit <= 1000:
        return jsonify({"ok": False, "error": "limit 必须在 1 到 1000 之间"}), 400

    audit_type = str(request.args.get("type") or "all").strip().lower()
    allowed_types = {
        "all", "job", "run", "model", "tool", "retry", "error", "artifact", "cost",
    }
    if audit_type not in allowed_types:
        return jsonify({"ok": False, "error": "type 筛选值无效"}), 400

    sess = session_manager.get_or_create(sid)
    jobs = sess.job_runner.list_jobs(limit=500, top_level_only=False)
    events = _all_job_events(sess.job_runner)
    artifacts = list_registered_artifacts(session_id=str(sid)[:160], limit=200)

    payload = build_session_audit(
        session_id=sid,
        jobs=jobs,
        events=events,
        artifacts=artifacts,
        claims=(),
        evidence=(),
        lifecycle_events=(),
        usage_breakdowns=list(getattr(sess, "usage_breakdowns", []) or [])[-100:],
        command_metrics=list(getattr(sess, "command_metrics", []) or [])[-200:],
        analysis_delete_operations=list(
            getattr(sess, "analysis_delete_operations", []) or []
        )[-100:],
        filters={
            "type": audit_type,
            "status": request.args.get("status") or "all",
            "query": request.args.get("query") or "",
            "from": request.args.get("from") or "",
            "to": request.args.get("to") or "",
            "limit": limit,
        },
    )
    return jsonify(payload)
