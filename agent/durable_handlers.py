"""Reconstructable handlers for the optional durable queue.

The queue worker deliberately calls application entry points only after it has
rebuilt a session, workspace and JobRunner from stable identifiers.  No Flask
request, live data-source connection or callback closure crosses the queue
boundary.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Mapping

from agent.jobs import JobContext, JobRunner

log = logging.getLogger(__name__)


@contextmanager
def _worker_session(session_id: str, queue_context):
    """Temporarily bind the queue-owned JobsStore to one session."""
    from api.state import session_manager

    session = session_manager.get_or_create(session_id)
    previous_runner = getattr(session, "_job_runner", None)
    if queue_context.job_store is None:
        raise RuntimeError("durable queue JobStore is unavailable")
    runner = JobRunner(session_id, queue_context.job_store, max_workers=2)
    session._job_runner = runner
    try:
        yield session, runner
    finally:
        # A handler may replace the temporary runner with a fresh process-local
        # runner before returning (Workflow completion callbacks need one).
        if getattr(session, "_job_runner", None) is runner:
            session._job_runner = previous_runner
        runner.shutdown(wait=True)


def _restore_workspace(session_id: str, workspace_id: str):
    """Resolve a stable workspace and refresh its persistent source index."""
    if not workspace_id:
        return None
    from data.workspace import workspace_manager

    runtime = workspace_manager.ensure_for_job(session_id, workspace_id)
    if runtime is None:
        raise RuntimeError("durable job workspace is unavailable")
    from api.workspace import _register_workdir_files

    registration = _register_workdir_files(session_id, runtime)
    if registration.get("errors") and not registration.get("reused"):
        raise RuntimeError(
            "durable job workspace data registration failed: "
            + "; ".join(str(item) for item in registration["errors"][:5])
        )
    return runtime


def workflow_node_handler(payload: Mapping[str, Any], queue_context) -> Any:
    """Execute one Workflow node using the durable node/run facts."""
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("workflow queue task is missing session_id")
    workspace_id = str(payload.get("workspace_id") or "").strip()
    with _worker_session(session_id, queue_context) as (session, worker_runner):
        workspace = _restore_workspace(session_id, workspace_id)
        from agent.workflows.runtime import workflow_runtime_manager

        runtime = workflow_runtime_manager.get(session_id)
        previous_runner = runtime.scheduler.job_runner
        runtime.scheduler.job_runner = session.job_runner
        try:
            node = dict(payload.get("node") or {})
            materials = dict(payload.get("inputs") or {})
            if not node or not str(node.get("node_id") or ""):
                raise ValueError("workflow queue task is missing node definition")
            context = JobContext(
                queue_context.job_id,
                queue_context.job_store,
                lambda _job_id: queue_context.job_is_canceled(),
                workspace_id=(
                    workspace.workspace_id if workspace is not None else runtime.workspace.workspace_id
                ),
                runtime=workspace or runtime.workspace,
            )
            return runtime._execute_node(node, materials, context)
        finally:
            # A runtime may survive this handler for a later HTTP request. Do
            # not leave it pointing at the queue worker's soon-to-close store.
            if previous_runner is worker_runner:
                session._job_runner = None
                runtime.scheduler.job_runner = session.job_runner
            else:
                runtime.scheduler.job_runner = previous_runner


def chat_turn_handler(payload: Mapping[str, Any], queue_context, app) -> Any:
    """Run a chat turn from its persisted request/replay envelope."""
    from api.chat import _unwrap_chat_resume_payload, chat_stream

    request_payload = _unwrap_chat_resume_payload(
        dict(payload).get("resume_request") if isinstance(payload, Mapping) else None
    )
    if request_payload is None:
        request_payload = _unwrap_chat_resume_payload(
            queue_context.job_store.get_request_payload(queue_context.job_id)
            if queue_context.job_store is not None
            else None
        )
    if request_payload is None:
        raise ValueError("chat queue task is missing its request snapshot")
    session_id = str(request_payload.get("session_id") or payload.get("session_id") or "").strip()
    if not session_id or session_id != str(queue_context.payload.get("session_id") or session_id):
        raise ValueError("chat queue task session identity is invalid")
    body = dict(request_payload.get("body") or {})
    body["message"] = str(request_payload.get("message") or body.get("message") or "")
    body["_resume_job_id"] = queue_context.job_id
    body["_durable_worker_resume"] = True
    if request_payload.get("user_id") and not body.get("user_id"):
        body["user_id"] = request_payload["user_id"]

    with _worker_session(session_id, queue_context):
        with app.test_request_context(
            f"/api/session/{session_id}/chat",
            method="POST",
            json=body,
            headers={"X-PFS-Durable-Worker": "1"},
        ):
            # Cloud mode still authenticates against the shared user database;
            # only the authenticated user id crosses the queue boundary.
            from flask import session as flask_session
            from infrastructure.compat import cloud_login_enabled

            if cloud_login_enabled() and request_payload.get("user_id"):
                flask_session["uid"] = str(request_payload["user_id"])
            response = chat_stream(session_id)
            if not hasattr(response, "response"):
                raise RuntimeError("chat queue handler received an invalid response")
            iteration_failed = False
            try:
                try:
                    for _chunk in response.response:
                        pass
                except BaseException:
                    # Preserve an iteration failure even if closing the
                    # response also fails.  The response still owns the
                    # generator/socket resources and must be closed on every
                    # path.
                    iteration_failed = True
                    raise
            finally:
                try:
                    response.close()
                except BaseException:
                    if not iteration_failed:
                        raise
                    log.exception("[durable-queue] chat response cleanup failed")
    return {"job_id": queue_context.job_id, "status": "completed"}


def build_handlers(app) -> dict[str, Any]:
    return {
        "workflow_node": workflow_node_handler,
        "chat_turn": lambda payload, context: chat_turn_handler(payload, context, app),
    }


def completion_hook(app):
    """Advance a Workflow after a queue-backed node reaches terminal state."""

    def on_complete(task: Mapping[str, Any], _result: Any) -> None:
        if str(task.get("kind") or "") != "workflow_node":
            return
        payload = task.get("payload") or {}
        session_id = str(payload.get("session_id") or "").strip()
        run_id = str(payload.get("run_id") or "").strip()
        if not session_id or not run_id:
            log.warning("[durable-queue] workflow completion lacks run identity")
            return
        with app.app_context():
            from agent.workflows.runtime import workflow_runtime_manager

            runtime = workflow_runtime_manager.get(session_id)
            runtime.scheduler.advance(run_id)

    return on_complete
