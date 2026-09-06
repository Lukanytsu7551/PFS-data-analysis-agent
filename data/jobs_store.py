#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite persistence for jobs and their replayable event stream."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from infrastructure.paths import data_path
from typing import Any, Dict, List, Mapping, Optional

log = logging.getLogger(__name__)

STATUS_CREATED = "created"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELING = "canceling"
STATUS_CANCELED = "canceled"

# A JobRunner owns the executable callback in process memory. When the
# process disappears, a reopened store cannot safely replay that callback;
# keep the recovery reason stable so higher-level workflow code can distinguish
# a restart from an ordinary provider failure. The action is deliberately
# manual: the original provider/tool call must never be replayed implicitly.
RESTART_RECOVERY_ERROR = "Application restarted before the job completed."
RESTART_RECOVERY_CODE = "job_interrupted_after_restart"
RESTART_RECOVERY_ACTION = "retry_from_original_request"

# Chat turns use a server-only checkpoint to distinguish a safe model/read
# replay from an interrupted side-effect boundary.  Keep these values stable:
# the API and frontend use the codes to explain why an automatic replay was
# intentionally refused.
CHAT_RECOVERY_CHECKPOINT_EVENT = "chat_recovery_checkpoint"
CHAT_UNSAFE_RECOVERY_ERROR = "Chat worker interrupted during a non-idempotent step; it was not replayed."
CHAT_UNSAFE_RECOVERY_CODE = "job_interrupted_requires_review"
CHAT_UNSAFE_RECOVERY_ACTION = "review_and_retry_as_new_request"

_TERMINAL = {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELED}

EVENT_RETENTION_DAYS = 30
MAX_EVENTS_PER_SESSION = 5000

# A Job callback lives in one process, while its durable row may be observed by
# another process.  A short renewable lease prevents a second process from
# treating a healthy worker as interrupted; an expired lease is the only
# condition under which startup recovery is allowed to close the row.
JOB_LEASE_SECONDS = 30.0

# Compatibility aliases for callers outside this module during the B migration.
STATUS_STARTED = STATUS_RUNNING
STATUS_PROGRESS = STATUS_RUNNING
STATUS_DONE = STATUS_SUCCEEDED
STATUS_ERROR = STATUS_FAILED

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    workspace_id TEXT DEFAULT '',
    type TEXT NOT NULL,
    label TEXT DEFAULT '',
    parent_id TEXT DEFAULT '',
    operation_key TEXT DEFAULT '',
    status TEXT NOT NULL,
    progress INTEGER DEFAULT 0,
    message TEXT DEFAULT '',
    result TEXT,
    error TEXT,
    error_code TEXT DEFAULT '',
    recovery_action TEXT DEFAULT '',
    owner_id TEXT DEFAULT '',
    lease_until TEXT,
    request_json TEXT,
    durable_handler TEXT DEFAULT '',
    queue_task_id TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_session ON jobs(session_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS job_event_sequences (
    session_id TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_job_events_replay
    ON job_events(session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_job_events_job
    ON job_events(job_id, sequence);
"""

_ALLOWED_TRANSITIONS = {
    STATUS_CREATED: {STATUS_QUEUED, STATUS_CANCELING, STATUS_CANCELED},
    STATUS_QUEUED: {STATUS_RUNNING, STATUS_CANCELING, STATUS_CANCELED},
    STATUS_RUNNING: {
        STATUS_RUNNING,
        STATUS_SUCCEEDED,
        STATUS_FAILED,
        STATUS_CANCELING,
        STATUS_CANCELED,
    },
    STATUS_CANCELING: {STATUS_CANCELED, STATUS_FAILED},
    STATUS_SUCCEEDED: set(),
    STATUS_FAILED: set(),
    STATUS_CANCELED: set(),
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    if data.get("result"):
        try:
            data["result"] = json.loads(data["result"])
        except (json.JSONDecodeError, TypeError):
            pass
    return data


def _event_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    payload = json.loads(row["payload_json"])
    return {
        **payload,
        "type": row["event_type"],
        "job_id": row["job_id"],
        "session_id": row["session_id"],
        "sequence": row["sequence"],
        "created_at": row["created_at"],
    }


class JobsStore:
    """Thread-safe jobs CRUD plus a durable, per-session ordered event log."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        owner_id: str = "",
        lease_seconds: float = JOB_LEASE_SECONDS,
    ):
        if db_path is None:
            db_path = Path(os.environ.get("PFS_JOBS_DB_PATH") or data_path("outputs", "jobs", "jobs.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._owner_id = str(owner_id or f"job-owner-{uuid.uuid4().hex}")[:120]
        try:
            parsed_lease = float(lease_seconds)
        except (TypeError, ValueError):
            parsed_lease = JOB_LEASE_SECONDS
        self._lease_seconds = max(0.1, parsed_lease)
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        connection: Optional[sqlite3.Connection] = None
        try:
            connection = sqlite3.connect(
                str(db_path),
                check_same_thread=False,
                timeout=30.0,
            )
            self._conn = connection
            self._conn.row_factory = sqlite3.Row
            with self._lock:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=5000")
                self._conn.executescript(_SCHEMA)
                self._ensure_columns_locked()
                self._migrate_legacy_statuses_locked()
                self._recover_interrupted_jobs_locked()
                self._conn.commit()
            self.cleanup_events()
        except BaseException:
            # Preserve the initialization failure even if connection cleanup
            # itself fails. Clearing the reference also prevents a partially
            # initialized store from retaining the SQLite handle.
            if connection is not None:
                try:
                    connection.close()
                except BaseException:
                    pass
            self._conn = None
            raise
        log.info("[jobs] store opened at %s", db_path)

    def _ensure_columns_locked(self) -> None:
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(jobs)")}
        for name, ddl in {
            "label": "TEXT DEFAULT ''",
            "parent_id": "TEXT DEFAULT ''",
            "operation_key": "TEXT DEFAULT ''",
            "message": "TEXT DEFAULT ''",
            "error_code": "TEXT DEFAULT ''",
            "recovery_action": "TEXT DEFAULT ''",
            "owner_id": "TEXT DEFAULT ''",
            "lease_until": "TEXT",
            "request_json": "TEXT",
            "durable_handler": "TEXT DEFAULT ''",
            "queue_task_id": "TEXT DEFAULT ''",
            "updated_at": "TEXT",
            "workspace_id": "TEXT DEFAULT ''",
        }.items():
            if name not in columns:
                self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_workspace ON jobs(workspace_id)")
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_operation_key "
            "ON jobs(operation_key) WHERE operation_key <> ''"
        )

    def _migrate_legacy_statuses_locked(self) -> None:
        mapping = {
            "started": STATUS_RUNNING,
            "progress": STATUS_RUNNING,
            "done": STATUS_SUCCEEDED,
            "error": STATUS_FAILED,
        }
        for old, new in mapping.items():
            self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = COALESCE(updated_at, ?) WHERE status = ?",
                (new, _now_iso(), old),
            )

    def _lease_until(self, now: str | None = None) -> str:
        """Return an ISO timestamp comparable to the stored lease column."""
        if now:
            try:
                base = datetime.fromisoformat(str(now))
            except (TypeError, ValueError):
                base = datetime.now()
        else:
            base = datetime.now()
        return (base + timedelta(seconds=self._lease_seconds)).isoformat(timespec="milliseconds")

    def _recover_interrupted_jobs_locked(self) -> int:
        """Close jobs whose owning worker disappeared and its lease expired."""
        placeholders = ",".join("?" for _ in _TERMINAL)
        now = _now_iso()
        rows = self._conn.execute(
            f"SELECT * FROM jobs WHERE status NOT IN ({placeholders}) "
            "AND (lease_until IS NULL OR lease_until = '' OR lease_until <= ?)",
            (*_TERMINAL, now),
        ).fetchall()
        for row in rows:
            if row["queue_task_id"] and row["status"] == STATUS_QUEUED and not row["owner_id"]:
                # A ready durable queue task is not interrupted work. Its
                # queue lease, rather than JobsStore startup, owns recovery.
                continue
            if row["queue_task_id"]:
                checkpoint = self._latest_recovery_checkpoint_locked(row["id"])
                is_chat_job = (
                    str(row["type"] or "") == "conversation_analysis"
                    or str(row["durable_handler"] or "") == "chat_turn"
                )
                if is_chat_job and checkpoint and not bool(checkpoint.get("replay_safe")):
                    # Replaying the original request could repeat a write,
                    # export, external call, or team/MCP action.  The durable
                    # queue will see the terminal Job and settle its own task;
                    # the user must explicitly review and retry as a new turn.
                    status = STATUS_FAILED
                    error = CHAT_UNSAFE_RECOVERY_ERROR
                    error_code = CHAT_UNSAFE_RECOVERY_CODE
                    recovery_action = CHAT_UNSAFE_RECOVERY_ACTION
                    event = {
                        "type": "job_error",
                        "job_id": row["id"],
                        "status": STATUS_FAILED,
                        "error": error,
                        "error_code": error_code,
                        "recovery_action": recovery_action,
                        "automatic_replay": False,
                        "resume_available": False,
                        "recovery_phase": str(checkpoint.get("phase") or "")[:80],
                    }
                    finished_at = now
                    self._conn.execute(
                        "UPDATE jobs SET status = ?, error = ?, error_code = ?, "
                        "recovery_action = ?, owner_id = '', lease_until = NULL, "
                        "updated_at = ?, finished_at = ? WHERE id = ?",
                        (status, error, error_code, recovery_action, now, finished_at, row["id"]),
                    )
                    self._append_event_locked(row["session_id"], row["id"], event, now)
                    continue
                # A durable queue task has a reconstructable handler.  Its
                # process-local callback may be gone, but the queue worker can
                # claim the task again after the lease expires.  Keep the Job
                # queued so a restart does not destroy the recovery path.
                status = STATUS_QUEUED
                error = None
                error_code = ""
                recovery_action = "durable_queue_requeued"
                event = {
                    "type": "job_requeued",
                    "job_id": row["id"],
                    "status": STATUS_QUEUED,
                    "recovery_action": recovery_action,
                }
            elif row["status"] == STATUS_CANCELING:
                status = STATUS_CANCELED
                error = None
                error_code = ""
                recovery_action = ""
                event = {
                    "type": "job_canceled",
                    "job_id": row["id"],
                    "status": STATUS_CANCELED,
                }
            else:
                status = STATUS_FAILED
                error = RESTART_RECOVERY_ERROR
                error_code = RESTART_RECOVERY_CODE
                recovery_action = RESTART_RECOVERY_ACTION
                event = {
                    "type": "job_error",
                    "job_id": row["id"],
                    "status": STATUS_FAILED,
                    "error": error,
                    "error_code": error_code,
                    "recovery_action": recovery_action,
                    "resume_available": bool(row["request_json"]),
                }
            finished_at = None if status == STATUS_QUEUED else now
            self._conn.execute(
                "UPDATE jobs SET status = ?, error = ?, error_code = ?, "
                "recovery_action = ?, owner_id = '', lease_until = NULL, "
                "updated_at = ?, finished_at = ? "
                "WHERE id = ?",
                (status, error, error_code, recovery_action, now, finished_at, row["id"]),
            )
            self._append_event_locked(row["session_id"], row["id"], event, now)
        if rows:
            log.warning("[jobs] recovered %d interrupted jobs after restart", len(rows))
        return len(rows)

    def _latest_recovery_checkpoint_locked(self, jid: str) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT payload_json FROM job_events "
            "WHERE job_id = ? AND event_type = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (jid, CHAT_RECOVERY_CHECKPOINT_EVENT),
        ).fetchone()
        if row is None:
            return {}
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    def recover_expired_jobs(self) -> int:
        """Recover expired jobs after a store has already been opened.

        A process can start while another worker's lease is still valid.  The
        caller can invoke this method from a later status or scheduler tick so
        an expired lease is eventually closed without requiring a third process
        to open the database.
        """
        with self._transaction():
            return self._recover_interrupted_jobs_locked()

    # ── Creation and transitions ──────────────────────────────────────────

    def create(
        self,
        session_id: str,
        job_type: str,
        label: str = "",
        parent_id: str = "",
        workspace_id: str = "",
        operation_key: str = "",
    ) -> Dict[str, Any]:
        operation_key = str(operation_key or "").strip()[:240]
        jid = str(uuid.uuid4())[:12]
        now = _now_iso()
        lease_until = self._lease_until(now)
        payload = {
            "type": "job_created",
            "job_id": jid,
            "job_type": job_type,
            "label": label,
            "status": STATUS_CREATED,
            "workspace_id": workspace_id,
        }
        created = True
        result: Optional[Dict[str, Any]] = None
        with self._transaction():
            if operation_key:
                existing = self._conn.execute(
                    "SELECT * FROM jobs WHERE operation_key = ?",
                    (operation_key,),
                ).fetchone()
                if existing is not None:
                    same_identity = all(
                        str(existing[key] or "") == str(value or "")
                        for key, value in (
                            ("session_id", session_id),
                            ("workspace_id", workspace_id),
                            ("type", job_type),
                            ("parent_id", parent_id),
                        )
                    )
                    if not same_identity:
                        raise ValueError("job operation key conflicts with another request")
                    result = _row_to_dict(existing)
                    created = False
            if created:
                self._conn.execute(
                    "INSERT INTO jobs "
                    "(id, session_id, workspace_id, type, label, parent_id, operation_key, "
                    "status, progress, owner_id, lease_until, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                    (
                        jid,
                        session_id,
                        workspace_id,
                        job_type,
                        label,
                        parent_id,
                        operation_key,
                        STATUS_CREATED,
                        self._owner_id,
                        lease_until,
                        now,
                        now,
                    ),
                )
                self._append_event_locked(session_id, jid, payload, now)
        if not created:
            result["_created"] = False  # type: ignore[index]
            return result  # type: ignore[return-value]
        result = self.get(jid)
        result["_created"] = True  # type: ignore[index]
        return result  # type: ignore[return-value]

    def set_request_payload(
        self,
        jid: str,
        payload: Mapping[str, Any],
        *,
        durable_handler: str = "",
        queue_task_id: str = "",
    ) -> bool:
        """Persist a bounded, secret-free reconstruction envelope.

        The caller is responsible for filtering secrets before this method is
        called.  This store never persists live objects or executable code.
        """
        if not isinstance(payload, Mapping):
            raise ValueError("job request payload must be an object")
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > 2_000_000:
            raise ValueError("job request payload exceeds 2 MB")
        with self._transaction():
            cursor = self._conn.execute(
                "UPDATE jobs SET request_json = ?, durable_handler = ?, "
                "queue_task_id = ?, updated_at = ? WHERE id = ?",
                (
                    encoded,
                    str(durable_handler or "")[:120],
                    str(queue_task_id or "")[:120],
                    _now_iso(),
                    jid,
                ),
            )
        return cursor.rowcount == 1

    def get_request_payload(self, jid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT request_json FROM jobs WHERE id = ?", (jid,)).fetchone()
        if row is None or not row["request_json"]:
            return None
        try:
            value = json.loads(row["request_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        return dict(value) if isinstance(value, dict) else None

    def handoff_to_queue(self, jid: str, queue_task_id: str) -> bool:
        """Release a Job lease so a durable queue worker can claim it."""
        queue_task_id = str(queue_task_id or "").strip()[:120]
        if not queue_task_id:
            return False
        with self._transaction():
            row = self._get_row_locked(jid)
            if row is None or row["status"] in _TERMINAL:
                return False
            now = _now_iso()
            self._conn.execute(
                "UPDATE jobs SET status = ?, owner_id = '', lease_until = NULL, "
                "queue_task_id = ?, durable_handler = COALESCE(durable_handler, ''), "
                "updated_at = ? WHERE id = ?",
                (STATUS_QUEUED, queue_task_id, now, jid),
            )
            self._append_event_locked(
                row["session_id"],
                jid,
                {
                    "type": "job_queued_durable",
                    "job_id": jid,
                    "status": STATUS_QUEUED,
                    "queue_task_id": queue_task_id,
                },
                now,
            )
        return True

    def claim_durable_job(self, jid: str) -> bool:
        """Claim a queue-backed Job for this store owner after queue claim."""
        with self._transaction():
            row = self._get_row_locked(jid)
            if row is None or row["status"] in _TERMINAL or not row["queue_task_id"]:
                return False
            now = _now_iso()
            current_owner = str(row["owner_id"] or "")
            lease_until = str(row["lease_until"] or "")
            if current_owner and current_owner != self._owner_id and lease_until and lease_until > now:
                return False
            started_at = row["started_at"] or now
            self._conn.execute(
                "UPDATE jobs SET status = ?, owner_id = ?, lease_until = ?, "
                "error = NULL, error_code = '', recovery_action = '', "
                "started_at = ?, updated_at = ? WHERE id = ?",
                (
                    STATUS_RUNNING,
                    self._owner_id,
                    self._lease_until(now),
                    started_at,
                    now,
                    jid,
                ),
            )
            self._append_event_locked(
                row["session_id"],
                jid,
                {"type": "job_started", "job_id": jid, "status": STATUS_RUNNING},
                now,
            )
        return True

    def reopen_for_resume(self, jid: str) -> bool:
        """Reopen only a restart-recovered conversation for explicit resume."""
        with self._transaction():
            row = self._get_row_locked(jid)
            if row is None or row["status"] != STATUS_FAILED:
                return False
            if row["error_code"] != RESTART_RECOVERY_CODE:
                return False
            if not row["request_json"]:
                return False
            now = _now_iso()
            self._conn.execute(
                "UPDATE jobs SET status = ?, error = NULL, error_code = '', "
                "recovery_action = '', owner_id = ?, lease_until = ?, "
                "finished_at = NULL, updated_at = ? WHERE id = ?",
                (
                    STATUS_QUEUED,
                    self._owner_id,
                    self._lease_until(now),
                    now,
                    jid,
                ),
            )
            self._append_event_locked(
                row["session_id"],
                jid,
                {
                    "type": "job_resume_requested",
                    "job_id": jid,
                    "status": STATUS_QUEUED,
                    "recovery_action": "resume_chat_job",
                },
                now,
            )
        return True

    def requeue_durable_job(self, jid: str, error: str = "") -> bool:
        """Return a queue-backed Job to queued after a retryable handler error."""
        with self._transaction():
            row = self._get_row_locked(jid)
            if row is None or row["status"] in _TERMINAL or not row["queue_task_id"]:
                return False
            now = _now_iso()
            current_owner = str(row["owner_id"] or "")
            lease_until = str(row["lease_until"] or "")
            if current_owner != self._owner_id or not lease_until or lease_until <= now:
                # A replacement queue worker may already own this Job.  The
                # old handler must not move it back to queued after losing its
                # lease; the queue task's owner fence is authoritative.
                return False
            self._conn.execute(
                "UPDATE jobs SET status = ?, owner_id = '', lease_until = NULL, "
                "error = ?, updated_at = ? WHERE id = ?",
                (STATUS_QUEUED, str(error or "")[:4000], now, jid),
            )
            self._append_event_locked(
                row["session_id"],
                jid,
                {
                    "type": "job_requeued",
                    "job_id": jid,
                    "status": STATUS_QUEUED,
                    "error": str(error or "")[:1000],
                },
                now,
            )
        return True

    def mark_queued(self, jid: str) -> bool:
        return self._transition(
            jid,
            STATUS_QUEUED,
            owner_id=self._owner_id,
            lease_until=self._lease_until(),
        )

    def mark_started(self, jid: str) -> bool:
        return self._transition(
            jid,
            STATUS_RUNNING,
            event={"type": "job_started", "job_id": jid, "status": STATUS_RUNNING},
            started_at=_now_iso(),
            owner_id=self._owner_id,
            lease_until=self._lease_until(),
        )

    def touch_lease(self, jid: str, *, owner_id: str = "") -> bool:
        """Renew a live worker lease without creating a noisy lifecycle event."""
        owner = str(owner_id or self._owner_id)
        if not owner:
            return False
        with self._transaction():
            now = _now_iso()
            cursor = self._conn.execute(
                "UPDATE jobs SET lease_until = ?, updated_at = ? "
                "WHERE id = ? AND owner_id = ? AND status NOT IN (?, ?, ?)",
                (
                    self._lease_until(now),
                    now,
                    jid,
                    owner,
                    STATUS_SUCCEEDED,
                    STATUS_FAILED,
                    STATUS_CANCELED,
                ),
            )
        return cursor.rowcount == 1

    def set_progress(
        self,
        jid: str,
        progress: int,
        message: str = "",
        *,
        owner_id: str | None = None,
    ) -> bool:
        progress = max(0, min(100, int(progress)))
        with self._transaction():
            row = self._get_row_locked(jid)
            if row is None or row["status"] != STATUS_RUNNING:
                return False
            owner = self._owner_id if owner_id is None else str(owner_id or "")
            lease_until = str(row["lease_until"] or "")
            if (
                not owner
                or str(row["owner_id"] or "") != owner
                or not lease_until
                or lease_until <= _now_iso()
            ):
                return False
            if row["progress"] == progress and (row["message"] or "") == message:
                return False
            now = _now_iso()
            self._conn.execute(
                "UPDATE jobs SET progress = ?, message = ?, updated_at = ? WHERE id = ?",
                (progress, message, now, jid),
            )
            self._append_event_locked(
                row["session_id"],
                jid,
                {
                    "type": "job_progress",
                    "job_id": jid,
                    "job_type": row["type"],
                    "status": STATUS_RUNNING,
                    "progress": progress,
                    "message": message,
                },
                now,
            )
        return True

    def mark_succeeded(self, jid: str, result: Any) -> bool:
        result_json = json.dumps(result, ensure_ascii=False, default=str)
        return self._transition(
            jid,
            STATUS_SUCCEEDED,
            event={
                "type": "job_done",
                "job_id": jid,
                "status": STATUS_SUCCEEDED,
                "result": result,
            },
            progress=100,
            result=result_json,
            finished_at=_now_iso(),
            _require_owner=True,
        )

    def mark_failed(
        self,
        jid: str,
        error: str,
        *,
        error_code: str = "",
        recovery_action: str = "",
    ) -> bool:
        event = {
            "type": "job_error",
            "job_id": jid,
            "status": STATUS_FAILED,
            "error": error,
        }
        if error_code:
            event["error_code"] = error_code
        if recovery_action:
            event["recovery_action"] = recovery_action
        return self._transition(
            jid,
            STATUS_FAILED,
            event=event,
            error=error,
            error_code=error_code,
            recovery_action=recovery_action,
            finished_at=_now_iso(),
            _require_owner=True,
        )

    def mark_canceling(self, jid: str) -> bool:
        return self._transition(jid, STATUS_CANCELING)

    def mark_canceled(self, jid: str) -> bool:
        return self._transition(
            jid,
            STATUS_CANCELED,
            event={
                "type": "job_canceled",
                "job_id": jid,
                "status": STATUS_CANCELED,
            },
            finished_at=_now_iso(),
        )

    # Compatibility method names while B2-B4 migrate callers.
    mark_done = mark_succeeded
    mark_error = mark_failed

    def _transition(
        self,
        jid: str,
        new_status: str,
        *,
        event: Optional[Mapping[str, Any]] = None,
        **extra: Any,
    ) -> bool:
        with self._transaction():
            row = self._get_row_locked(jid)
            if row is None:
                log.warning("[jobs] transition on missing job %s", jid)
                return False
            current = row["status"]
            if new_status not in _ALLOWED_TRANSITIONS.get(current, set()):
                log.warning("[jobs] reject transition %s: %s -> %s", jid, current, new_status)
                return False
            require_owner = bool(extra.pop("_require_owner", False))
            if require_owner:
                owner = self._owner_id
                lease_until = str(row["lease_until"] or "")
                if (
                    not owner
                    or str(row["owner_id"] or "") != owner
                    or not lease_until
                    or lease_until <= _now_iso()
                ):
                    log.warning("[jobs] reject stale owner transition %s", jid)
                    return False
            now = _now_iso()
            sets = ["status = ?", "updated_at = ?"]
            values: List[Any] = [new_status, now]
            if new_status in _TERMINAL:
                sets.extend(["owner_id = ?", "lease_until = ?"])
                values.extend(["", None])
            for key, value in extra.items():
                sets.append(f"{key} = ?")
                values.append(value)
            values.append(jid)
            self._conn.execute(
                f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?",
                values,
            )
            if event is not None:
                self._append_event_locked(row["session_id"], jid, event, now)
        return True

    # ── Durable events ────────────────────────────────────────────────────

    def append_event(
        self,
        jid: str,
        event: Mapping[str, Any],
        *,
        owner_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Persist a non-state event while the owning worker lease is valid.

        State transitions already fail closed once a job reaches a terminal
        state.  Applying the same boundary to replayable events prevents an
        old worker from appending a late artifact or lifecycle event after a
        replacement process has recovered the job.
        """
        with self._transaction():
            row = self._get_row_locked(jid)
            if row is None or row["status"] in _TERMINAL:
                return None
            owner = str(owner_id or "")
            current_owner = str(row["owner_id"] or "")
            lease_until = str(row["lease_until"] or "")
            if not owner or owner != current_owner or not lease_until or lease_until <= _now_iso():
                log.warning("[jobs] reject event from stale owner for %s", jid)
                return None
            return self._append_event_locked(row["session_id"], jid, event, _now_iso())

    def _append_event_locked(
        self,
        session_id: str,
        jid: str,
        event: Mapping[str, Any],
        created_at: str,
    ) -> Dict[str, Any]:
        event_type = str(event.get("type") or "").strip()
        if not event_type:
            raise ValueError("job event type cannot be empty")
        self._conn.execute(
            "INSERT INTO job_event_sequences(session_id, last_sequence) VALUES (?, 0) "
            "ON CONFLICT(session_id) DO NOTHING",
            (session_id,),
        )
        self._conn.execute(
            "UPDATE job_event_sequences SET last_sequence = last_sequence + 1 WHERE session_id = ?",
            (session_id,),
        )
        sequence = self._conn.execute(
            "SELECT last_sequence FROM job_event_sequences WHERE session_id = ?",
            (session_id,),
        ).fetchone()["last_sequence"]
        payload = dict(event)
        payload.pop("sequence", None)
        payload.pop("session_id", None)
        payload.pop("created_at", None)
        self._conn.execute(
            "INSERT INTO job_events "
            "(session_id, job_id, sequence, event_type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                jid,
                sequence,
                event_type,
                json.dumps(payload, ensure_ascii=False, default=str),
                created_at,
            ),
        )
        return {
            **payload,
            "type": event_type,
            "job_id": jid,
            "session_id": session_id,
            "sequence": sequence,
            "created_at": created_at,
        }

    def list_events(
        self,
        session_id: str,
        after_sequence: int = 0,
        limit: int = 200,
        job_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        sql = "SELECT * FROM job_events WHERE session_id = ? AND sequence > ?"
        params: List[Any] = [session_id, max(0, int(after_sequence))]
        if job_id:
            sql += " AND job_id = ?"
            params.append(job_id)
        sql += " ORDER BY sequence ASC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_event_row_to_dict(row) for row in rows]

    def last_sequence(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_sequence FROM job_event_sequences WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row["last_sequence"]) if row else 0

    def oldest_sequence(self, session_id: str) -> int:
        """Return the oldest retained sequence, or latest+1 when no events remain."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(sequence) AS sequence FROM job_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row and row["sequence"] is not None:
            return int(row["sequence"])
        return self.last_sequence(session_id) + 1

    def cleanup_events(
        self,
        retention_days: int = EVENT_RETENTION_DAYS,
        max_events_per_session: int = MAX_EVENTS_PER_SESSION,
    ) -> int:
        """Bound event storage by age and per-session count without reusing sequences."""
        retention_days = max(1, int(retention_days))
        max_events_per_session = max(100, int(max_events_per_session))
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="milliseconds")
        with self._transaction():
            before = self._conn.total_changes
            self._conn.execute("DELETE FROM job_events WHERE created_at < ?", (cutoff,))
            sessions = self._conn.execute(
                "SELECT session_id FROM job_events GROUP BY session_id HAVING COUNT(*) > ?",
                (max_events_per_session,),
            ).fetchall()
            for row in sessions:
                self._conn.execute(
                    "DELETE FROM job_events WHERE session_id = ? AND id NOT IN ("
                    "SELECT id FROM job_events WHERE session_id = ? "
                    "ORDER BY sequence DESC LIMIT ?)",
                    (row["session_id"], row["session_id"], max_events_per_session),
                )
            deleted = self._conn.total_changes - before
        if deleted:
            log.info("[jobs] event cleanup removed=%d", deleted)
        return deleted

    # ── Queries ───────────────────────────────────────────────────────────

    def _get_row_locked(self, jid: str) -> Optional[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()

    def get(self, jid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._get_row_locked(jid)
        return _row_to_dict(row) if row else None

    def get_for_session(self, session_id: str, jid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE session_id = ? AND id = ?",
                (session_id, jid),
            ).fetchone()
        return _row_to_dict(row) if row else None

    def get_last_recovery_checkpoint(
        self,
        session_id: str,
        jid: str,
    ) -> Dict[str, Any]:
        """Read the latest server-only chat recovery checkpoint for a Job."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM jobs WHERE session_id = ? AND id = ?",
                (session_id, jid),
            ).fetchone()
            if row is None:
                return {}
            checkpoint_row = self._conn.execute(
                "SELECT sequence, payload_json FROM job_events "
                "WHERE session_id = ? AND job_id = ? AND event_type = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (session_id, jid, CHAT_RECOVERY_CHECKPOINT_EVENT),
            ).fetchone()
            if checkpoint_row is None:
                return {}
            try:
                checkpoint = json.loads(checkpoint_row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                return {}
            if not isinstance(checkpoint, dict):
                return {}

            # Text deltas are already persisted as browser-safe stream events.
            # Reconstruct them on read instead of writing a large checkpoint
            # for every token. This keeps recovery exact while keeping the
            # internal event log bounded enough for long answers.
            if str(checkpoint.get("phase") or "") == "model_call" and bool(checkpoint.get("replay_safe")):
                partial = str(checkpoint.get("partial_content") or "")[:120_000]
                delta_rows = self._conn.execute(
                    "SELECT payload_json FROM job_events "
                    "WHERE session_id = ? AND job_id = ? AND sequence > ? "
                    "AND event_type = 'conversation_stream_event' "
                    "ORDER BY sequence ASC",
                    (session_id, jid, checkpoint_row["sequence"]),
                ).fetchall()
                for delta_row in delta_rows:
                    try:
                        event_payload = json.loads(delta_row["payload_json"])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    event = event_payload.get("event") if isinstance(event_payload, dict) else None
                    if not isinstance(event, dict) or event.get("type") != "text_delta":
                        continue
                    partial = (partial + str(event.get("content") or ""))[:120_000]
                checkpoint["partial_content"] = partial
            return dict(checkpoint)

    def get_by_operation_key(
        self,
        session_id: str,
        operation_key: str,
    ) -> Optional[Dict[str, Any]]:
        """Find a Job created for a deterministic workflow dispatch key."""
        key = str(operation_key or "").strip()
        if not key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE session_id = ? AND operation_key = ?",
                (session_id, key),
            ).fetchone()
        return _row_to_dict(row) if row else None

    def list_by_session(
        self,
        session_id: str,
        limit: int = 50,
        top_level_only: bool = False,
    ) -> List[Dict[str, Any]]:
        parent_filter = "AND COALESCE(parent_id, '') = '' " if top_level_only else ""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE session_id = ? "
                + parent_filter
                + "ORDER BY created_at DESC LIMIT ?",
                (session_id, max(1, min(int(limit), 500))),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def list_children(self, session_id: str, parent_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE session_id = ? AND parent_id = ? ORDER BY created_at ASC",
                (session_id, parent_id),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def list_detail_events(
        self,
        session_id: str,
        job_ids: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return retained conversation step events grouped by parent job."""
        clean_ids = [str(value) for value in job_ids if value]
        if not clean_ids:
            return {}
        placeholders = ",".join("?" for _ in clean_ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM job_events WHERE session_id = ? "
                f"AND job_id IN ({placeholders}) "
                "AND event_type IN ('conversation_step_started', 'conversation_step_finished') "
                "ORDER BY sequence ASC",
                (session_id, *clean_ids),
            ).fetchall()
        return_value: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            return_value.setdefault(row["job_id"], []).append(_event_row_to_dict(row))
        return return_value

    def list_active(
        self,
        session_id: str,
        top_level_only: bool = False,
    ) -> List[Dict[str, Any]]:
        placeholders = ",".join("?" for _ in _TERMINAL)
        parent_filter = "AND COALESCE(parent_id, '') = '' " if top_level_only else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM jobs WHERE session_id = ? "
                f"AND status NOT IN ({placeholders}) " + parent_filter + "ORDER BY created_at ASC",
                (session_id, *_TERMINAL),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def list_artifacts(
        self,
        session_id: str,
        job_ids: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return retained artifact events grouped by job for history hydration."""
        clean_ids = [str(value) for value in job_ids if value]
        if not clean_ids:
            return {}
        placeholders = ",".join("?" for _ in clean_ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT job_id, payload_json FROM job_events WHERE session_id = ? "
                f"AND event_type = 'artifact_created' AND job_id IN ({placeholders}) "
                "ORDER BY sequence ASC",
                (session_id, *clean_ids),
            ).fetchall()
        result: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            artifact = payload.get("artifact")
            if isinstance(artifact, dict):
                result.setdefault(row["job_id"], []).append(artifact)
        return result

    def purge_terminal_ids(self, session_id: str, job_ids: List[str]) -> int:
        """Delete selected terminal jobs and events without touching other history."""
        selected = list(dict.fromkeys(str(job_id) for job_id in job_ids if job_id))
        if not selected:
            return 0
        with self._transaction():
            pending = list(selected)
            all_ids = set(selected)
            while pending:
                placeholders = ",".join("?" for _ in pending)
                children = self._conn.execute(
                    f"SELECT id FROM jobs WHERE session_id = ? AND parent_id IN ({placeholders})",
                    (session_id, *pending),
                ).fetchall()
                pending = [str(row["id"]) for row in children if str(row["id"]) not in all_ids]
                all_ids.update(pending)
            placeholders = ",".join("?" for _ in all_ids)
            rows = self._conn.execute(
                f"SELECT id, status FROM jobs WHERE session_id = ? AND id IN ({placeholders})",
                (session_id, *all_ids),
            ).fetchall()
            active = [str(row["id"]) for row in rows if str(row["status"]) not in _TERMINAL]
            if active:
                raise RuntimeError("jobs are still active: " + ", ".join(active))
            existing_ids = [str(row["id"]) for row in rows]
            if not existing_ids:
                return 0
            placeholders = ",".join("?" for _ in existing_ids)
            self._conn.execute(
                f"DELETE FROM job_events WHERE session_id = ? AND job_id IN ({placeholders})",
                (session_id, *existing_ids),
            )
            self._conn.execute(
                f"DELETE FROM jobs WHERE session_id = ? AND id IN ({placeholders})",
                (session_id, *existing_ids),
            )
        return len(existing_ids)

    def clear_terminal(self, session_id: str) -> int:
        """Delete completed/failed/canceled jobs and their retained events.

        Active jobs are intentionally preserved. Sequence counters are not
        rewound, so browser replay cursors remain monotonic after cleanup.
        """
        placeholders = ",".join("?" for _ in _TERMINAL)
        with self._transaction():
            rows = self._conn.execute(
                f"SELECT id FROM jobs WHERE session_id = ? AND status IN ({placeholders})",
                (session_id, *_TERMINAL),
            ).fetchall()
            job_ids = [row["id"] for row in rows]
            if not job_ids:
                return 0
            id_placeholders = ",".join("?" for _ in job_ids)
            self._conn.execute(
                f"DELETE FROM job_events WHERE session_id = ? AND job_id IN ({id_placeholders})",
                (session_id, *job_ids),
            )
            self._conn.execute(
                f"DELETE FROM jobs WHERE session_id = ? AND id IN ({id_placeholders})",
                (session_id, *job_ids),
            )
        return len(job_ids)

    # ── Transaction/lifecycle ─────────────────────────────────────────────

    class _Transaction:
        def __init__(self, store: "JobsStore"):
            self.store = store

        def __enter__(self):
            self.store._lock.acquire()
            try:
                self.store._conn.execute("BEGIN IMMEDIATE")
            except BaseException:
                # BEGIN can fail after the Python lock has been acquired. Do
                # not leave that lock owned forever; preserve the original
                # exception for the caller.
                try:
                    self.store._conn.rollback()
                except Exception:
                    pass
                finally:
                    self.store._lock.release()
                raise
            return self

        def __exit__(self, exc_type, exc, tb):
            try:
                if exc_type is None:
                    self.store._conn.commit()
                else:
                    self.store._conn.rollback()
            finally:
                self.store._lock.release()
            return False

    def _transaction(self) -> "JobsStore._Transaction":
        return self._Transaction(self)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    @property
    def path(self) -> Path:
        return self._path

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def lease_seconds(self) -> float:
        return self._lease_seconds
