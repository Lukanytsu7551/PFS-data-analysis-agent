#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small SQLite-backed queue for work that must outlive one app process.

The normal PFS JobRunner remains process-local for backwards compatibility.
This module is the explicit hand-off boundary for work whose handler and
payload are safe to reconstruct in another service instance.  It provides
at-least-once delivery: a worker owns a task only while its lease is valid,
and an expired task becomes claimable by another worker.

Only JSON payloads and stable handler names belong in this queue.  Closures,
credentials, live sockets, DataSource objects, and Flask request objects must
never be placed in it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from infrastructure.paths import data_path

log = logging.getLogger(__name__)

QUEUE_READY = "ready"
QUEUE_LEASED = "leased"
QUEUE_SUCCEEDED = "succeeded"
QUEUE_FAILED = "failed"
_TERMINAL = {QUEUE_SUCCEEDED, QUEUE_FAILED}

DEFAULT_LEASE_SECONDS = 30.0
DEFAULT_MAX_ATTEMPTS = 3
_MAX_ATTEMPTS_ERROR = "durable queue task exceeded max attempts"

COMPLETION_PENDING = "pending"
COMPLETION_LEASED = "leased"
COMPLETION_DELIVERED = "delivered"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS durable_queue_tasks (
    id TEXT PRIMARY KEY,
    operation_key TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    owner_id TEXT NOT NULL DEFAULT '',
    lease_until REAL,
    available_at REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    result_json TEXT,
    error TEXT NOT NULL DEFAULT '',
    completion_status TEXT NOT NULL DEFAULT 'delivered',
    completion_attempts INTEGER NOT NULL DEFAULT 0,
    completion_owner_id TEXT NOT NULL DEFAULT '',
    completion_lease_until REAL,
    completion_available_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_durable_queue_operation
    ON durable_queue_tasks(operation_key) WHERE operation_key <> '';
CREATE INDEX IF NOT EXISTS idx_durable_queue_claim
    ON durable_queue_tasks(status, available_at, created_at);
CREATE INDEX IF NOT EXISTS idx_durable_queue_owner
    ON durable_queue_tasks(owner_id, status);
"""

_COMPATIBILITY_COLUMNS = {
    "completion_status": "TEXT NOT NULL DEFAULT 'delivered'",
    "completion_attempts": "INTEGER NOT NULL DEFAULT 0",
    "completion_owner_id": "TEXT NOT NULL DEFAULT ''",
    "completion_lease_until": "REAL",
    "completion_available_at": "REAL",
}


def _now() -> float:
    return time.time()


def _payload_json(payload: Any) -> tuple[str, str]:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    try:
        item["payload"] = json.loads(item.pop("payload_json"))
    except (KeyError, TypeError, json.JSONDecodeError):
        item["payload"] = {}
    raw_result = item.pop("result_json", None)
    if raw_result:
        try:
            item["result"] = json.loads(raw_result)
        except (TypeError, json.JSONDecodeError):
            item["result"] = raw_result
    else:
        item["result"] = None
    return item


class DurableQueueError(RuntimeError):
    """Queue contract or persistence error."""


class DurableQueueStore:
    """Thread-safe SQLite queue with lease-based cross-process claiming."""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        default_lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.path = Path(
            db_path
            or os.environ.get("PFS_DURABLE_QUEUE_DB_PATH")
            or data_path("outputs", "jobs", "durable-queue.db")
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = None
        try:
            self._conn = sqlite3.connect(
                str(self.path),
                check_same_thread=False,
                timeout=30.0,
            )
            self._conn.row_factory = sqlite3.Row
            try:
                lease = float(default_lease_seconds)
            except (TypeError, ValueError):
                lease = DEFAULT_LEASE_SECONDS
            self.default_lease_seconds = max(0.1, lease)
            with self._lock:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=5000")
                self._conn.executescript(_SCHEMA)
                self._ensure_compatibility_columns_locked()
                self._conn.commit()
        except BaseException:
            # The object never reaches the caller when initialization fails,
            # so close the local handle here.  In particular, PRAGMA/schema
            # failures must not leave a WAL connection alive in the process.
            conn = self._conn
            self._conn = None
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
            raise

    def _ensure_compatibility_columns_locked(self) -> None:
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(durable_queue_tasks)")}
        for name, ddl in _COMPATIBILITY_COLUMNS.items():
            if name not in columns:
                self._conn.execute(f"ALTER TABLE durable_queue_tasks ADD COLUMN {name} {ddl}")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_durable_queue_completion "
            "ON durable_queue_tasks(status, completion_status, completion_available_at, finished_at)"
        )

    def _expired_linked_job_decision(
        self,
        task: Mapping[str, Any],
        *,
        now: float,
    ) -> tuple[str, Any, str] | None:
        """Return a safe terminal decision for an exhausted linked task.

        ``None`` means that the Job is active or its state cannot be trusted.
        The caller must then leave the queue task recoverable.  A missing Job
        is different: there is no live Job lease to protect, so the dangling
        queue task can fail closed with a durable error.
        """
        payload = task.get("payload") or {}
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            return QUEUE_FAILED, None, _MAX_ATTEMPTS_ERROR

        try:
            from data.jobs_store import JobsStore

            raw_path = str(payload.get("job_store_path") or "").strip()
            job_store = JobsStore(
                Path(raw_path) if raw_path else None,
                owner_id=f"queue-reconcile-{uuid.uuid4().hex[:12]}",
                lease_seconds=self.default_lease_seconds,
            )
        except BaseException:
            log.exception(
                "[durable-queue] linked Job store unavailable; preserving exhausted claim id=%s",
                task.get("id"),
            )
            return None

        try:
            try:
                current = job_store.get(job_id)
            except BaseException:
                log.exception(
                    "[durable-queue] linked Job lookup failed; preserving exhausted claim id=%s",
                    task.get("id"),
                )
                return None
        finally:
            try:
                job_store.close()
            except BaseException:
                log.exception(
                    "[durable-queue] linked Job store cleanup failed id=%s",
                    task.get("id"),
                )

        if not current:
            return QUEUE_FAILED, None, "linked Job is missing; queue task was not replayed"

        status = str(current.get("status") or "")
        owner_id = str(current.get("owner_id") or "")
        lease_until = str(current.get("lease_until") or "")
        if owner_id or lease_until:
            # The Job may still be running, or its state may have changed
            # between the queue and Job reads.  Never terminalize the queue in
            # that window; the next lease cycle can reconcile it.
            return None
        if status == "succeeded":
            return QUEUE_SUCCEEDED, current.get("result"), ""
        if status in {"failed", "canceled"}:
            return QUEUE_FAILED, None, f"linked Job is already terminal: {status}"
        # A queued/non-terminal Job is still recoverable work.  Keep both
        # records non-terminal until a worker can safely claim and settle it.
        return None

    def _reconcile_ready_exhausted(self, *, now: float) -> int:
        """Finalize only exhausted READY tasks whose linked Job is safe."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM durable_queue_tasks WHERE status = ? "
                "AND attempts >= max_attempts AND available_at <= ?",
                (QUEUE_READY, now),
            ).fetchall()

        changed = 0
        for row in rows:
            item = _row(row) or {}
            decision = self._expired_linked_job_decision(item, now=now)
            if decision is None:
                if (item.get("payload") or {}).get("job_id"):
                    continue
                decision = QUEUE_FAILED, None, _MAX_ATTEMPTS_ERROR
            next_status, result, error = decision
            result_json = (
                json.dumps(result, ensure_ascii=False, default=str)
                if next_status == QUEUE_SUCCEEDED and result is not None
                else None
            )
            with self._transaction():
                cursor = self._conn.execute(
                    "UPDATE durable_queue_tasks SET status = ?, owner_id = '', "
                    "lease_until = NULL, result_json = ?, error = ?, "
                    "completion_status = ?, completion_owner_id = '', "
                    "completion_lease_until = NULL, completion_available_at = ?, "
                    "finished_at = COALESCE(finished_at, ?), updated_at = ? "
                    "WHERE id = ? AND status = ? AND attempts >= max_attempts",
                    (
                        next_status,
                        result_json,
                        "" if next_status == QUEUE_SUCCEEDED else (error or _MAX_ATTEMPTS_ERROR),
                        COMPLETION_PENDING,
                        now,
                        now,
                        now,
                        item["id"],
                        QUEUE_READY,
                    ),
                )
            changed += int(cursor.rowcount)
        return changed

    class _Transaction:
        def __init__(self, store: "DurableQueueStore") -> None:
            self.store = store

        def __enter__(self):
            self.store._lock.acquire()
            try:
                self.store._conn.execute("BEGIN IMMEDIATE")
            except BaseException:
                # BEGIN can fail after the Python lock has been acquired.  Do
                # not leave that lock owned forever; the original exception
                # must remain the one visible to the caller.
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

    def _transaction(self) -> "DurableQueueStore._Transaction":
        return self._Transaction(self)

    def enqueue(
        self,
        kind: str,
        payload: Mapping[str, Any],
        *,
        operation_key: str = "",
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        available_at: float | None = None,
    ) -> dict[str, Any]:
        kind = str(kind or "").strip()[:120]
        operation_key = str(operation_key or "").strip()[:240]
        if not kind:
            raise DurableQueueError("queue task kind is required")
        if not isinstance(payload, Mapping):
            raise DurableQueueError("queue task payload must be an object")
        try:
            max_attempts = max(1, min(int(max_attempts), 100))
        except (TypeError, ValueError):
            max_attempts = DEFAULT_MAX_ATTEMPTS
        payload_text, payload_hash = _payload_json(dict(payload))
        now = _now()
        task_id = "q_" + uuid.uuid4().hex[:20]
        due = now if available_at is None else float(available_at)
        with self._transaction():
            existing = None
            if operation_key:
                existing = self._conn.execute(
                    "SELECT * FROM durable_queue_tasks WHERE operation_key = ?",
                    (operation_key,),
                ).fetchone()
            if existing is not None:
                if (
                    str(existing["kind"] or "") != kind
                    or str(existing["payload_sha256"] or "") != payload_hash
                ):
                    raise DurableQueueError("queue operation key conflicts with a different task")
                result = _row(existing)
                result["_created"] = False
                return result
            self._conn.execute(
                "INSERT INTO durable_queue_tasks "
                "(id, operation_key, kind, payload_json, payload_sha256, status, "
                "attempts, max_attempts, available_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                (
                    task_id,
                    operation_key,
                    kind,
                    payload_text,
                    payload_hash,
                    QUEUE_READY,
                    max_attempts,
                    due,
                    now,
                    now,
                ),
            )
        result = self.get(task_id)
        result["_created"] = True
        return result

    def requeue_expired(self, *, now: float | None = None) -> int:
        now = _now() if now is None else float(now)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM durable_queue_tasks WHERE status = ? "
                "AND lease_until IS NOT NULL AND lease_until <= ?",
                (QUEUE_LEASED, now),
            ).fetchall()

        changed = 0
        for row in rows:
            item = _row(row) or {}
            if int(item.get("attempts") or 0) >= int(item.get("max_attempts") or 1):
                decision = self._expired_linked_job_decision(item, now=now)
                if decision is None:
                    # The linked Job is still active or cannot be read.  Keep
                    # the expired claim recoverable; terminalizing it here
                    # could leave that Job running under an owner lease.
                    continue
                next_status, result, error = decision
                with self._transaction():
                    if next_status == QUEUE_SUCCEEDED:
                        cursor = self._conn.execute(
                            "UPDATE durable_queue_tasks SET status = ?, owner_id = '', "
                            "lease_until = NULL, result_json = ?, error = '', "
                            "completion_status = ?, completion_owner_id = '', "
                            "completion_lease_until = NULL, completion_available_at = ?, "
                            "finished_at = COALESCE(finished_at, ?), updated_at = ? "
                            "WHERE id = ? AND status = ? AND lease_until IS NOT NULL "
                            "AND lease_until <= ?",
                            (
                                QUEUE_SUCCEEDED,
                                json.dumps(result, ensure_ascii=False, default=str)
                                if result is not None
                                else None,
                                COMPLETION_PENDING,
                                now,
                                now,
                                now,
                                item["id"],
                                QUEUE_LEASED,
                                now,
                            ),
                        )
                    else:
                        cursor = self._conn.execute(
                            "UPDATE durable_queue_tasks SET status = ?, owner_id = '', "
                            "lease_until = NULL, error = CASE WHEN error = '' THEN ? ELSE error END, "
                            "completion_status = ?, completion_owner_id = '', "
                            "completion_lease_until = NULL, completion_available_at = ?, "
                            "finished_at = COALESCE(finished_at, ?), updated_at = ? "
                            "WHERE id = ? AND status = ? AND lease_until IS NOT NULL "
                            "AND lease_until <= ?",
                            (
                                QUEUE_FAILED,
                                error or _MAX_ATTEMPTS_ERROR,
                                COMPLETION_PENDING,
                                now,
                                now,
                                now,
                                item["id"],
                                QUEUE_LEASED,
                                now,
                            ),
                        )
                changed += int(cursor.rowcount)
                continue

            with self._transaction():
                cursor = self._conn.execute(
                    "UPDATE durable_queue_tasks SET status = ?, owner_id = '', "
                    "lease_until = NULL, completion_status = ?, "
                    "completion_owner_id = '', completion_lease_until = NULL, "
                    "completion_available_at = NULL, updated_at = ? "
                    "WHERE id = ? AND status = ? AND lease_until IS NOT NULL "
                    "AND lease_until <= ?",
                    (
                        QUEUE_READY,
                        COMPLETION_DELIVERED,
                        now,
                        item["id"],
                        QUEUE_LEASED,
                        now,
                    ),
                )
            changed += int(cursor.rowcount)
        return changed

    def claim(
        self,
        owner_id: str,
        *,
        lease_seconds: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        owner_id = str(owner_id or "").strip()[:120]
        if not owner_id:
            raise DurableQueueError("queue worker owner_id is required")
        now = _now() if now is None else float(now)
        try:
            lease = self.default_lease_seconds if lease_seconds is None else float(lease_seconds)
        except (TypeError, ValueError):
            lease = self.default_lease_seconds
        lease = max(0.1, lease)

        # Reconcile expired leases before selecting a new owner.  Exhausted
        # tasks linked to an active/unknown Job remain leased and are picked up
        # below as a reconciliation claim; they must not be terminalized by a
        # generic queue-only update.
        self.requeue_expired(now=now)
        self._reconcile_ready_exhausted(now=now)

        with self._transaction():
            candidates = self._conn.execute(
                "SELECT * FROM durable_queue_tasks WHERE "
                "(status = ? AND available_at <= ?) OR "
                "(status = ? AND attempts >= max_attempts "
                "AND lease_until IS NOT NULL AND lease_until <= ?) "
                "ORDER BY created_at ASC",
                (QUEUE_READY, now, QUEUE_LEASED, now),
            ).fetchall()
            row = None
            for candidate in candidates:
                item = _row(candidate) or {}
                attempts = int(item.get("attempts") or 0)
                if attempts < int(item.get("max_attempts") or 1):
                    row = candidate
                    break
                # A maxed linked task is claimed only for safe Job
                # reconciliation.  It never invokes its application handler.
                if (item.get("payload") or {}).get("job_id"):
                    row = candidate
                    break
            if row is None:
                return None
            until = now + lease
            expected_status = str(row["status"] or "")
            exhausted = int(row["attempts"] or 0) >= int(row["max_attempts"] or 1)
            claimed_cursor = self._conn.execute(
                "UPDATE durable_queue_tasks SET status = ?, owner_id = ?, "
                "lease_until = ?, attempts = attempts + ?, started_at = COALESCE(started_at, ?), "
                "updated_at = ? WHERE id = ? AND status = ? "
                "AND ((status = ? AND attempts < max_attempts) OR "
                "(status = ? AND attempts >= max_attempts AND lease_until IS NOT NULL "
                "AND lease_until <= ?))",
                (
                    QUEUE_LEASED,
                    owner_id,
                    until,
                    0 if exhausted else 1,
                    now,
                    now,
                    row["id"],
                    expected_status,
                    QUEUE_READY,
                    QUEUE_LEASED,
                    now,
                ),
            )
            if claimed_cursor.rowcount != 1:
                return None
            claimed = self._conn.execute(
                "SELECT * FROM durable_queue_tasks WHERE id = ?",
                (row["id"],),
            ).fetchone()
        return _row(claimed)

    def heartbeat(self, task_id: str, owner_id: str, *, lease_seconds: float | None = None) -> bool:
        try:
            lease = self.default_lease_seconds if lease_seconds is None else max(0.1, float(lease_seconds))
        except (TypeError, ValueError):
            lease = self.default_lease_seconds
        now = _now()
        with self._transaction():
            cursor = self._conn.execute(
                "UPDATE durable_queue_tasks SET lease_until = ?, updated_at = ? "
                "WHERE id = ? AND owner_id = ? AND status = ? "
                "AND lease_until IS NOT NULL AND lease_until > ?",
                (
                    now + lease,
                    now,
                    str(task_id),
                    str(owner_id),
                    QUEUE_LEASED,
                    now,
                ),
            )
        return cursor.rowcount == 1

    def complete(self, task_id: str, owner_id: str, result: Any = None) -> bool:
        now = _now()
        result_json = json.dumps(result, ensure_ascii=False, default=str) if result is not None else None
        with self._transaction():
            cursor = self._conn.execute(
                "UPDATE durable_queue_tasks SET status = ?, owner_id = '', lease_until = NULL, "
                "result_json = ?, error = '', finished_at = ?, updated_at = ?, "
                "completion_status = ?, completion_owner_id = '', "
                "completion_lease_until = NULL, completion_available_at = ? "
                "WHERE id = ? AND owner_id = ? AND status = ? "
                "AND lease_until IS NOT NULL AND lease_until > ?",
                (
                    QUEUE_SUCCEEDED,
                    result_json,
                    now,
                    now,
                    COMPLETION_PENDING,
                    now,
                    str(task_id),
                    str(owner_id),
                    QUEUE_LEASED,
                    now,
                ),
            )
        return cursor.rowcount == 1

    def fail(
        self,
        task_id: str,
        owner_id: str,
        error: str,
        *,
        retry_delay: float = 1.0,
        force_terminal: bool = False,
    ) -> bool:
        now = _now()
        delay = max(0.0, min(float(retry_delay), 86_400.0))
        with self._transaction():
            row = self._conn.execute(
                "SELECT attempts, max_attempts FROM durable_queue_tasks "
                "WHERE id = ? AND owner_id = ? AND status = ? "
                "AND lease_until IS NOT NULL AND lease_until > ?",
                (str(task_id), str(owner_id), QUEUE_LEASED, now),
            ).fetchone()
            if row is None:
                return False
            terminal = force_terminal or int(row["attempts"] or 0) >= int(row["max_attempts"] or 1)
            status = QUEUE_FAILED if terminal else QUEUE_READY
            completion_status = COMPLETION_PENDING if terminal else COMPLETION_DELIVERED
            completion_available_at = now if terminal else None
            cursor = self._conn.execute(
                "UPDATE durable_queue_tasks SET status = ?, owner_id = '', lease_until = NULL, "
                "available_at = ?, error = ?, finished_at = ?, updated_at = ?, "
                "completion_status = ?, completion_owner_id = '', "
                "completion_lease_until = NULL, completion_available_at = ? "
                "WHERE id = ? AND owner_id = ? AND status = ? "
                "AND lease_until IS NOT NULL AND lease_until > ?",
                (
                    status,
                    now if terminal else now + delay,
                    str(error or "")[:4000],
                    now if terminal else None,
                    now,
                    completion_status,
                    completion_available_at,
                    str(task_id),
                    str(owner_id),
                    QUEUE_LEASED,
                    now,
                ),
            )
        return cursor.rowcount == 1

    def claim_completion(
        self,
        owner_id: str,
        *,
        task_id: str = "",
        lease_seconds: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Claim one terminal task's callback notification.

        Queue state and callback delivery are separate durable facts.  The
        callback is at-least-once: a crashed worker or a raised callback leaves
        this notification pending for a later owner.
        """
        owner_id = str(owner_id or "").strip()[:120]
        if not owner_id:
            raise DurableQueueError("queue completion owner_id is required")
        now = _now() if now is None else float(now)
        try:
            lease = self.default_lease_seconds if lease_seconds is None else float(lease_seconds)
        except (TypeError, ValueError):
            lease = self.default_lease_seconds
        task_id = str(task_id or "").strip()
        lease = max(0.1, lease)
        with self._transaction():
            query = (
                "SELECT * FROM durable_queue_tasks WHERE status IN (?, ?) "
                "AND ((completion_status = ? AND "
                "COALESCE(completion_available_at, 0) <= ?) OR "
                "(completion_status = ? AND completion_lease_until IS NOT NULL "
                "AND completion_lease_until <= ?))"
            )
            params: list[Any] = [
                QUEUE_SUCCEEDED,
                QUEUE_FAILED,
                COMPLETION_PENDING,
                now,
                COMPLETION_LEASED,
                now,
            ]
            if task_id:
                query += " AND id = ?"
                params.append(task_id)
            else:
                query += " ORDER BY COALESCE(completion_available_at, finished_at, created_at) ASC LIMIT 1"
            row = self._conn.execute(query, params).fetchone()
            if row is None:
                return None
            until = now + lease
            cursor = self._conn.execute(
                "UPDATE durable_queue_tasks SET completion_status = ?, "
                "completion_attempts = completion_attempts + 1, "
                "completion_owner_id = ?, completion_lease_until = ?, "
                "updated_at = ? WHERE id = ? AND status IN (?, ?) AND "
                "((completion_status = ? AND COALESCE(completion_available_at, 0) <= ?) OR "
                "(completion_status = ? AND completion_lease_until IS NOT NULL "
                "AND completion_lease_until <= ?))",
                (
                    COMPLETION_LEASED,
                    owner_id,
                    until,
                    now,
                    row["id"],
                    QUEUE_SUCCEEDED,
                    QUEUE_FAILED,
                    COMPLETION_PENDING,
                    now,
                    COMPLETION_LEASED,
                    now,
                ),
            )
            if cursor.rowcount != 1:
                return None
            claimed = self._conn.execute(
                "SELECT * FROM durable_queue_tasks WHERE id = ?",
                (row["id"],),
            ).fetchone()
        return _row(claimed)

    def ack_completion(self, task_id: str, owner_id: str) -> bool:
        """Mark a callback notification delivered with an owner fence."""
        now = _now()
        with self._transaction():
            cursor = self._conn.execute(
                "UPDATE durable_queue_tasks SET completion_status = ?, "
                "completion_owner_id = '', completion_lease_until = NULL, "
                "completion_available_at = NULL, updated_at = ? "
                "WHERE id = ? AND status IN (?, ?) AND completion_status = ? "
                "AND completion_owner_id = ? AND completion_lease_until IS NOT NULL "
                "AND completion_lease_until > ?",
                (
                    COMPLETION_DELIVERED,
                    now,
                    str(task_id),
                    QUEUE_SUCCEEDED,
                    QUEUE_FAILED,
                    COMPLETION_LEASED,
                    str(owner_id),
                    now,
                ),
            )
        return cursor.rowcount == 1

    def release_completion(
        self,
        task_id: str,
        owner_id: str,
        *,
        retry_delay: float = 1.0,
    ) -> bool:
        """Return a failed callback notification to the pending pool."""
        now = _now()
        try:
            delay = max(0.0, min(float(retry_delay), 86_400.0))
        except (TypeError, ValueError):
            delay = 1.0
        with self._transaction():
            cursor = self._conn.execute(
                "UPDATE durable_queue_tasks SET completion_status = ?, "
                "completion_owner_id = '', completion_lease_until = NULL, "
                "completion_available_at = ?, updated_at = ? "
                "WHERE id = ? AND status IN (?, ?) AND completion_status = ? "
                "AND completion_owner_id = ? AND completion_lease_until IS NOT NULL "
                "AND completion_lease_until > ?",
                (
                    COMPLETION_PENDING,
                    now + delay,
                    now,
                    str(task_id),
                    QUEUE_SUCCEEDED,
                    QUEUE_FAILED,
                    COMPLETION_LEASED,
                    str(owner_id),
                    now,
                ),
            )
        return cursor.rowcount == 1

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM durable_queue_tasks WHERE id = ?", (str(task_id),)
            ).fetchone()
        return _row(row)

    def get_by_operation_key(self, operation_key: str) -> dict[str, Any] | None:
        key = str(operation_key or "").strip()
        if not key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM durable_queue_tasks WHERE operation_key = ?", (key,)
            ).fetchone()
        return _row(row)

    def list(self, *, status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        query = "SELECT * FROM durable_queue_tasks"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(str(status))
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_row(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            conn = self._conn
            self._conn = None
            if conn is None:
                return
            try:
                conn.close()
            except Exception:
                pass


class DurableQueueContext:
    """Context passed to a reconstructed handler."""

    def __init__(self, task: Mapping[str, Any], owner_id: str, store: DurableQueueStore):
        self.task_id = str(task.get("id") or "")
        self.owner_id = str(owner_id or "")
        self.payload = dict(task.get("payload") or {})
        self.queue = store
        self.job_id = str(self.payload.get("job_id") or "")
        self.job_store = None
        self._job_owner_id = f"{self.owner_id}:job"[:120]
        self._lease_lost = threading.Event()
        self._lease_guard = threading.RLock()

    def heartbeat(self) -> bool:
        try:
            ok = self.queue.heartbeat(self.task_id, self.owner_id)
        except Exception:
            # A persistence/SQLite failure is a fencing failure too.  Do not
            # let a handler that catches the exception acknowledge work from
            # a lease whose ownership is no longer knowable.
            self.mark_lease_lost()
            raise
        if not ok:
            self.mark_lease_lost()
        return ok

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost.is_set()

    def mark_lease_lost(self) -> None:
        with self._lease_guard:
            self._lease_lost.set()

    def job_is_canceled(self) -> bool:
        if self.lease_lost:
            return True
        if self.job_store is None or not self.job_id:
            return False
        row = self.job_store.get(self.job_id) or {}
        return str(row.get("status") or "") in {"canceling", "canceled"}


QueueHandler = Callable[[Mapping[str, Any], DurableQueueContext], Any]
QueueCompletionHook = Callable[[Mapping[str, Any], Any], None]


class DurableQueueWorker:
    """Polling worker suitable for a dedicated service or app sidecar."""

    def __init__(
        self,
        store: DurableQueueStore,
        handlers: Mapping[str, QueueHandler],
        *,
        worker_id: str = "",
        poll_seconds: float = 0.2,
        lease_seconds: float | None = None,
        on_complete: QueueCompletionHook | None = None,
    ) -> None:
        self.store = store
        self.handlers = dict(handlers or {})
        self.worker_id = str(worker_id or "queue-worker-" + uuid.uuid4().hex[:12])[:120]
        self.poll_seconds = max(0.02, min(float(poll_seconds), 30.0))
        self.lease_seconds = lease_seconds
        self.on_complete = on_complete
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._store_close_lock = threading.Lock()
        self._store_closed = False

    def _deliver_pending_completion(
        self,
        completion_task: Mapping[str, Any] | None = None,
    ) -> bool:
        """Deliver one callback notification without replaying the handler."""
        # A worker without a callback is not a completion consumer.  In
        # particular, do not claim and acknowledge the durable notification
        # from success/failure cleanup paths: a later worker with
        # ``on_complete`` must still be able to claim and retry it.
        if self.on_complete is None:
            return False
        task_id = str((completion_task or {}).get("id") or "")
        try:
            pending = self.store.claim_completion(
                self.worker_id,
                task_id=task_id,
                lease_seconds=self.lease_seconds,
            )
        except Exception:
            log.exception("[durable-queue] completion claim failed id=%s", task_id or "unknown")
            return False
        if pending is None:
            return False

        pending_result = pending.get("result") if pending.get("status") == QUEUE_SUCCEEDED else None
        callback_task = completion_task or pending
        try:
            if self.on_complete is not None:
                self.on_complete(callback_task, pending_result)
        except Exception:
            try:
                self.store.release_completion(
                    pending["id"],
                    self.worker_id,
                    retry_delay=self.poll_seconds,
                )
            except Exception:
                log.exception(
                    "[durable-queue] completion retry release failed id=%s",
                    pending["id"],
                )
            log.exception("[durable-queue] completion hook failed id=%s", pending["id"])
            return False

        try:
            acknowledged = self.store.ack_completion(pending["id"], self.worker_id)
        except Exception:
            log.exception(
                "[durable-queue] completion acknowledgement failed id=%s",
                pending["id"],
            )
            return False
        if not acknowledged:
            log.warning(
                "[durable-queue] completion acknowledgement fence rejected id=%s",
                pending["id"],
            )
        return acknowledged

    def run_once(self) -> bool:
        if self.on_complete is not None and self._deliver_pending_completion():
            return True
        task = self.store.claim(
            self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if task is None:
            if self.on_complete is not None and self._deliver_pending_completion():
                return True
            return False
        handler = self.handlers.get(str(task.get("kind") or ""))
        context = None
        job_store = None
        heartbeat_stop = threading.Event()
        heartbeat_thread: threading.Thread | None = None
        result = None
        queue_settled = False
        preserve_lease = False
        linked_job_missing = False
        settlement_error = "durable queue task failed during worker cleanup"
        settlement_force_terminal = False

        def settle_failure(error: str, *, force_terminal: bool = False) -> bool:
            nonlocal queue_settled
            if queue_settled:
                return False
            try:
                queue_settled = bool(
                    self.store.fail(
                        task["id"],
                        self.worker_id,
                        error,
                        force_terminal=force_terminal,
                    )
                )
            except Exception:
                log.exception("[durable-queue] task settlement failed id=%s", task["id"])
            return queue_settled

        def deliver_completion(
            completion_task: Mapping[str, Any] | None = None,
            completed_result: Any = None,
        ) -> bool:
            del completed_result
            return self._deliver_pending_completion(completion_task)

        def call_completion_hook() -> None:
            deliver_completion(task, None)

        def call_success_hook(
            completed_result: Any,
            completion_task: Mapping[str, Any] | None = None,
        ) -> None:
            # ``completed_result`` is retained for API readability; the
            # persisted terminal row is authoritative for recovery delivery.
            deliver_completion(completion_task or task, completed_result)

        def unclaimed_task_snapshot() -> dict[str, Any]:
            """Build the stable task view returned by enqueue()."""
            snapshot = dict(task)
            snapshot.update(
                {
                    "status": QUEUE_READY,
                    "attempts": max(0, int(task.get("attempts") or 0) - 1),
                    "owner_id": "",
                    "lease_until": None,
                    "updated_at": task.get("created_at"),
                    "started_at": None,
                    "finished_at": None,
                    "error": "",
                    "result": None,
                    "_created": task.get("_created", True),
                }
            )
            return snapshot

        def linked_job_state() -> tuple[bool, bool]:
            """Return whether the linked Job is safe to settle with this task.

            Queue settlement is only safe after the linked Job has released its
            owner lease.  If the read itself fails, the state is unknown and
            the queue claim must remain recoverable instead of being finalized.
            The second value reports whether the Job is terminal.
            """
            nonlocal linked_job_missing
            if context is None or not context.job_id:
                return True, False
            if job_store is None:
                log.error(
                    "[durable-queue] linked Job store is unavailable; preserving claim id=%s",
                    task["id"],
                )
                return False, False
            try:
                current = job_store.get(context.job_id)
            except Exception:
                log.exception(
                    "[durable-queue] linked Job state lookup failed; preserving claim id=%s",
                    task["id"],
                )
                return False, False
            if not current:
                linked_job_missing = True
                log.error(
                    "[durable-queue] linked Job disappeared; preserving claim id=%s",
                    task["id"],
                )
                return False, False
            status = str(current.get("status") or "")
            owner_id = str(current.get("owner_id") or "")
            lease_until = str(current.get("lease_until") or "")
            lease_released = not owner_id and not lease_until
            terminal = status in {"succeeded", "failed", "canceled"}
            safe = lease_released and (terminal or status == "queued")
            if not safe:
                log.error(
                    "[durable-queue] linked Job is not safely settled status=%s owner=%s "
                    "lease=%s; preserving claim id=%s",
                    status,
                    bool(owner_id),
                    bool(lease_until),
                    task["id"],
                )
            return safe, terminal

        try:
            context = DurableQueueContext(task, self.worker_id, self.store)
            if context.job_id:
                # Import lazily so the standalone queue remains usable by small
                # services that do not use PFS JobsStore.
                from data.jobs_store import JobsStore

                raw_path = str(context.payload.get("job_store_path") or "").strip()
                job_store = JobsStore(
                    Path(raw_path) if raw_path else None,
                    owner_id=context._job_owner_id,
                    lease_seconds=self.lease_seconds or self.store.default_lease_seconds,
                )
                context.job_store = job_store
                if int(task.get("attempts") or 0) >= int(task.get("max_attempts") or 1):
                    # An exhausted linked task is a reconciliation claim, not
                    # another handler attempt.  First settle the Job (or
                    # preserve the claim while its state is uncertain), then
                    # settle the Queue under its owner fence.
                    try:
                        current = job_store.get(context.job_id)
                    except BaseException:
                        log.exception(
                            "[durable-queue] exhausted linked Job lookup failed; preserving claim id=%s",
                            task["id"],
                        )
                        preserve_lease = True
                        return True
                    if not current:
                        settlement_error = "linked Job is missing; queue task was not replayed"
                        settlement_force_terminal = True
                        settle_failure(
                            settlement_error,
                            force_terminal=True,
                        )
                        call_completion_hook()
                        return True

                    current_status = str(current.get("status") or "")
                    current_owner = str(current.get("owner_id") or "")
                    current_lease = str(current.get("lease_until") or "")
                    if current_status == "succeeded" and not current_owner and not current_lease:
                        result = current.get("result")
                        queue_settled = bool(self.store.complete(task["id"], self.worker_id, result))
                        if not queue_settled:
                            preserve_lease = True
                        else:
                            call_success_hook(result, unclaimed_task_snapshot())
                        return True
                    if current_status in {"failed", "canceled"} and not current_owner and not current_lease:
                        settlement_error = "linked Job is already terminal: " + current_status
                        settlement_force_terminal = True
                        settle_failure(
                            settlement_error,
                            force_terminal=True,
                        )
                        call_completion_hook()
                        return True
                    if current_owner or current_lease:
                        log.error(
                            "[durable-queue] exhausted linked Job is still leased; preserving claim id=%s",
                            task["id"],
                        )
                        preserve_lease = True
                        return True
                    try:
                        if not job_store.claim_durable_job(context.job_id):
                            preserve_lease = True
                            return True
                        if not job_store.mark_failed(
                            context.job_id,
                            _MAX_ATTEMPTS_ERROR,
                        ):
                            preserve_lease = True
                            return True
                        job_safe, terminal_job = linked_job_state()
                        if not job_safe or not terminal_job:
                            preserve_lease = True
                            return True
                        settlement_error = _MAX_ATTEMPTS_ERROR
                        settlement_force_terminal = True
                        settle_failure(
                            settlement_error,
                            force_terminal=True,
                        )
                        call_completion_hook()
                    except BaseException:
                        log.exception(
                            "[durable-queue] exhausted linked Job settlement failed; preserving claim id=%s",
                            task["id"],
                        )
                        preserve_lease = True
                    return True
                if not job_store.claim_durable_job(context.job_id):
                    try:
                        current = job_store.get(context.job_id) or {}
                    except Exception:
                        log.exception(
                            "[durable-queue] linked Job claim lookup failed; preserving claim id=%s",
                            task["id"],
                        )
                        preserve_lease = True
                        return True
                    if not current:
                        settlement_error = "linked Job is missing; queue task was not replayed"
                        settlement_force_terminal = True
                        settle_failure(
                            settlement_error,
                            force_terminal=True,
                        )
                        call_completion_hook()
                        return True
                    job_safe, terminal_job = linked_job_state()
                    if not job_safe:
                        preserve_lease = True
                        log.error(
                            "[durable-queue] linked Job claim is not safely recoverable; "
                            "preserving claim id=%s",
                            task["id"],
                        )
                    elif terminal_job and current.get("status") == "succeeded":
                        queue_settled = bool(
                            self.store.complete(task["id"], self.worker_id, current.get("result"))
                        )
                        if not queue_settled:
                            preserve_lease = True
                        else:
                            call_success_hook(
                                current.get("result"),
                                unclaimed_task_snapshot(),
                            )
                    elif terminal_job:
                        settlement_error = "linked Job is already terminal: " + str(
                            current.get("status") or "unknown"
                        )
                        settlement_force_terminal = True
                        settle_failure(
                            settlement_error,
                            force_terminal=settlement_force_terminal,
                        )
                    else:
                        settlement_error = "durable Job could not be claimed"
                        settle_failure(settlement_error)
                    return True

                interval = max(
                    0.02,
                    min(
                        float(self.lease_seconds or self.store.default_lease_seconds) / 4.0,
                        5.0,
                    ),
                )

                def renew() -> None:
                    while not heartbeat_stop.is_set():
                        with context._lease_guard:
                            if heartbeat_stop.is_set():
                                return
                            try:
                                queue_ok = context.heartbeat()
                                if not queue_ok or context.lease_lost:
                                    context.mark_lease_lost()
                                    heartbeat_stop.set()
                                    log.error(
                                        "[durable-queue] queue lease lost during heartbeat id=%s",
                                        task["id"],
                                    )
                                    return
                                job_ok = job_store.touch_lease(
                                    context.job_id,
                                    owner_id=job_store.owner_id,
                                )
                                if not job_ok:
                                    context.mark_lease_lost()
                                    heartbeat_stop.set()
                                    log.error(
                                        "[durable-queue] Job lease lost during heartbeat id=%s",
                                        task["id"],
                                    )
                                    return
                            except Exception:
                                log.exception("[durable-queue] heartbeat failed id=%s", task["id"])
                                context.mark_lease_lost()
                                heartbeat_stop.set()
                                log.error(
                                    "[durable-queue] preserving task claim after heartbeat error id=%s",
                                    task["id"],
                                )
                                return
                        if heartbeat_stop.wait(interval):
                            return

                heartbeat_thread = threading.Thread(
                    target=renew,
                    daemon=True,
                    name=f"queue-lease-{str(task['id'])[-8:]}",
                )
                heartbeat_thread.start()

            if handler is None:
                settlement_error = "no handler registered for queue task kind"
                if context.job_id and job_store is not None:
                    with context._lease_guard:
                        if context.lease_lost:
                            preserve_lease = True
                        else:
                            try:
                                current = job_store.get(context.job_id) or {}
                                current_status = str(current.get("status") or "")
                                if not current:
                                    # A dangling job_id has no running Job or
                                    # lease to protect.  Fail this unknown
                                    # handler task explicitly instead of
                                    # preserving an unrecoverable claim.
                                    settlement_error = (
                                        "linked Job is missing; unknown handler was not replayed"
                                    )
                                    settlement_force_terminal = True
                                    settle_failure(
                                        settlement_error,
                                        force_terminal=True,
                                    )
                                elif current_status == "succeeded":
                                    result = current.get("result")
                                    queue_settled = bool(
                                        self.store.complete(task["id"], self.worker_id, result)
                                    )
                                    if not queue_settled:
                                        preserve_lease = True
                                    else:
                                        call_success_hook(result)
                                elif current_status in {"failed", "canceled"}:
                                    settlement_force_terminal = True
                                    settle_failure(
                                        "linked Job is already terminal: " + current_status,
                                        force_terminal=True,
                                    )
                                elif not job_store.mark_failed(context.job_id, settlement_error):
                                    raise RuntimeError("linked Job failure was not applied")
                                else:
                                    job_safe, terminal_job = linked_job_state()
                                    if not job_safe or not terminal_job:
                                        preserve_lease = True
                                    else:
                                        settlement_force_terminal = True
                                        settle_failure(
                                            settlement_error,
                                            force_terminal=True,
                                        )
                            except Exception:
                                log.exception(
                                    "[durable-queue] unknown handler Job cleanup failed id=%s",
                                    task["id"],
                                )
                                preserve_lease = True
                else:
                    settle_failure(settlement_error)
                call_completion_hook()
                return True

            try:
                result = handler(task["payload"], context)
            except Exception as exc:
                log.exception(
                    "[durable-queue] task failed id=%s kind=%s",
                    task["id"],
                    task["kind"],
                )
                settlement_error = f"{type(exc).__name__}: {exc}"
                heartbeat_stop.set()
                with context._lease_guard:
                    if context.lease_lost:
                        preserve_lease = True
                    elif job_store is not None:
                        try:
                            current = job_store.get(context.job_id) or {}
                            current_status = str(current.get("status") or "")
                            if current_status == "canceling":
                                # Cancellation is a user decision, not a retryable
                                # handler error. Close it before settling the queue.
                                if not job_store.mark_canceled(context.job_id):
                                    raise RuntimeError("linked Job cancellation was not applied")
                            elif current_status in {"canceled", "failed", "succeeded"}:
                                pass
                            elif int(task.get("attempts") or 0) >= int(task.get("max_attempts") or 1):
                                if not job_store.mark_failed(context.job_id, settlement_error):
                                    raise RuntimeError("linked Job failure was not applied")
                            elif not job_store.requeue_durable_job(
                                context.job_id,
                                settlement_error,
                            ):
                                raise RuntimeError("linked Job requeue was not applied")
                        except Exception:
                            log.exception(
                                "[durable-queue] linked Job error handling failed id=%s",
                                task["id"],
                            )
                        job_safe, terminal_job = linked_job_state()
                        if not job_safe:
                            if linked_job_missing:
                                # There is no Job lease left to protect.  A
                                # dangling link is terminalized explicitly;
                                # a failed Job read remains recoverable below.
                                settlement_force_terminal = True
                            else:
                                # The original handler error remains the
                                # durable error, but the queue claim must
                                # expire for a later worker.
                                preserve_lease = True
                                log.error(
                                    "[durable-queue] linked Job recovery is uncertain; "
                                    "leaving claim recoverable id=%s error=%s",
                                    task["id"],
                                    settlement_error,
                                )
                        else:
                            settlement_force_terminal = terminal_job
                    if not preserve_lease:
                        settle_failure(
                            settlement_error,
                            force_terminal=settlement_force_terminal,
                        )
                if not preserve_lease:
                    call_completion_hook()
                return True

            with context._lease_guard:
                if context.lease_lost:
                    # A replacement worker may already own the queue/Job. The
                    # stale worker must not acknowledge or overwrite its state.
                    preserve_lease = True
                    return True

                if job_store is not None:
                    try:
                        current = job_store.get(context.job_id) or {}
                        current_status = str(current.get("status") or "")
                        if current_status == "succeeded":
                            result = current.get("result")
                        elif current_status in {"failed", "canceled", "canceling"}:
                            settlement_error = "linked Job reached terminal state before queue completion"
                            settlement_force_terminal = True
                            settle_failure(
                                settlement_error,
                                force_terminal=settlement_force_terminal,
                            )
                            return True
                        elif not job_store.mark_succeeded(context.job_id, result):
                            # The Job owner fence failed even though the queue lease had
                            # not reported loss yet. Leave the queue claim for expiry so
                            # a replacement can reconstruct the task.
                            preserve_lease = True
                            return True
                    except Exception:
                        log.exception(
                            "[durable-queue] linked Job success handling failed; preserving claim id=%s",
                            task["id"],
                        )
                        preserve_lease = True
                        return True

                try:
                    queue_settled = bool(self.store.complete(task["id"], self.worker_id, result))
                except Exception:
                    log.exception(
                        "[durable-queue] queue completion failed; preserving claim id=%s",
                        task["id"],
                    )
                    preserve_lease = True
                    return True
                if not queue_settled:
                    # A false owner-fenced completion means another worker may
                    # own the task.  Do not turn that uncertain claim into a
                    # terminal failure after the linked Job was completed.
                    preserve_lease = True
                    log.warning(
                        "[durable-queue] queue completion fence rejected; preserving claim id=%s",
                        task["id"],
                    )
            if queue_settled:
                deliver_completion(task, result)
            return True
        finally:
            heartbeat_stop.set()
            if heartbeat_thread is not None:
                try:
                    heartbeat_thread.join(timeout=1.0)
                except Exception:
                    log.exception("[durable-queue] heartbeat cleanup failed id=%s", task["id"])
            if not queue_settled and not preserve_lease and context is not None and context.job_id:
                job_safe, terminal_job = linked_job_state()
                if not job_safe:
                    if linked_job_missing:
                        settlement_force_terminal = True
                    else:
                        # Never use the generic queue fallback while the
                        # linked Job may still be running under this or
                        # another owner.
                        preserve_lease = True
                else:
                    settlement_force_terminal = settlement_force_terminal or terminal_job
            if job_store is not None:
                try:
                    job_store.close()
                except Exception:
                    log.exception("[durable-queue] JobsStore cleanup failed id=%s", task["id"])
            if not queue_settled and not preserve_lease:
                if settle_failure(
                    settlement_error,
                    force_terminal=settlement_force_terminal,
                ):
                    deliver_completion(task)

    def run_forever(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    if not self.run_once():
                        self.stop_event.wait(self.poll_seconds)
                except Exception:
                    log.exception("[durable-queue] worker loop failed")
                    self.stop_event.wait(self.poll_seconds)
        finally:
            if self.stop_event.is_set():
                self._close_store()

    def start(self) -> None:
        if self._store_closed:
            raise RuntimeError("cannot start durable queue worker after its store was closed")
        if self._thread is not None and self._thread.is_alive():
            return
        self.stop_event.clear()
        self._thread = threading.Thread(
            target=self.run_forever,
            daemon=True,
            name=f"durable-queue-{self.worker_id[-8:]}",
        )
        self._thread.start()

    def stop(self, *, wait: bool = False) -> None:
        self.stop_event.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=5.0)
        if self._thread is None or not self._thread.is_alive():
            self._close_store()

    def _close_store(self) -> None:
        with self._store_close_lock:
            if self._store_closed:
                return
            try:
                self.store.close()
            finally:
                self._store_closed = True
