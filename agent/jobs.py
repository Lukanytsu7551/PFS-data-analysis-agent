#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local JobRunner with durable events and cooperative cancellation."""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterator, List, Optional

from agent.events import ArtifactCreatedEvent, JobEvent, serialize_event
from data.jobs_store import (
    JobsStore,
    STATUS_CANCELED,
    STATUS_CANCELING,
    STATUS_CREATED,
    STATUS_FAILED,
    STATUS_QUEUED,
    _TERMINAL,
)

log = logging.getLogger(__name__)

JobFn = Callable[["JobContext"], Any]


class JobCanceled(Exception):
    """Raised by a job after observing its cooperative cancellation flag."""


class JobContext:
    """Worker-facing progress, artifact and cancellation facade."""

    def __init__(
        self,
        job_id: str,
        store: JobsStore,
        is_canceled_fn: Callable[[str], bool],
        notify_fn: Optional[Callable[[], None]] = None,
        workspace_id: str = "",
        runtime=None,
    ):
        self.job_id = job_id
        self._store = store
        self._is_canceled = is_canceled_fn
        self._notify = notify_fn or (lambda: None)
        self.workspace_id = workspace_id
        self.runtime = runtime
        self.owner_id = str(getattr(store, "owner_id", "") or "")

    def set_progress(self, pct: int, message: str = "") -> None:
        if self._store.set_progress(self.job_id, pct, message):
            self._notify()

    def artifact_created(self, artifact: Dict[str, Any]) -> None:
        event = ArtifactCreatedEvent(job_id=self.job_id, artifact=artifact)
        if (
            self._store.append_event(
                self.job_id,
                serialize_event(event),
                owner_id=self.owner_id,
            )
            is not None
        ):
            self._notify()

    def record_event(self, event_type: str, **payload: Any) -> None:
        """Persist a non-sensitive worker lifecycle event for SSE replay."""
        event = {"type": event_type, **payload}
        if (
            self._store.append_event(
                self.job_id,
                event,
                owner_id=self.owner_id,
            )
            is not None
        ):
            self._notify()

    def is_canceled(self) -> bool:
        return self._is_canceled(self.job_id)

    def check_canceled(self) -> None:
        if self._is_canceled(self.job_id):
            raise JobCanceled(self.job_id)


class JobRunner:
    """Per-session worker pool backed by the process-wide ``JobsStore``."""

    def __init__(self, session_id: str, store: JobsStore, max_workers: int = 2):
        self._sid = session_id
        self._store = store
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=f"job-{session_id[:8]}",
        )
        # A top-level conversation may synchronously wait for child tool jobs
        # (for example a long query or an export). Keep detached chat turns in
        # their own slot so moving the Agent off the HTTP request thread does
        # not consume a worker that the Agent needs for its own tools.
        self._detached_pool = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"chat-{session_id[:8]}",
        )
        self._futures: Dict[str, Future] = {}
        self._canceled: set[str] = set()
        self._timed_out: Dict[str, Dict[str, str]] = {}
        self._lock = threading.RLock()
        self._event_condition = threading.Condition()
        self._scope = threading.local()
        self._job_leases: Dict[str, str] = {}
        self._prestart_cleanups: Dict[str, Callable[[], None]] = {}
        self._terminal_listeners: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._lease_stops: Dict[str, threading.Event] = {}
        self._lease_threads: Dict[str, threading.Thread] = {}
        self._shutting_down = False
        self._last_recovery_check = 0.0

    @property
    def durable_queue_enabled(self) -> bool:
        """Whether this runner may hand structured work to the durable queue."""
        return str(os.environ.get("PFS_ENABLE_DURABLE_QUEUE") or "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    # ── Submission/execution ──────────────────────────────────────────────

    def create(
        self,
        fn: JobFn,
        job_type: str,
        label: str = "",
        *,
        operation_key: str = "",
    ) -> str:
        parent_id = getattr(self._scope, "parent_id", "")
        workspace_id, lease_acquired = self._acquire_workspace_lease()
        try:
            job = self._store.create(
                self._sid,
                job_type,
                label=label,
                parent_id=parent_id,
                workspace_id=workspace_id,
                operation_key=operation_key,
            )
        except Exception:
            if lease_acquired:
                self._release_workspace_id(workspace_id)
            raise
        jid = job["id"]
        created = bool(job.pop("_created", True))
        if not created:
            if lease_acquired:
                self._release_workspace_id(workspace_id)
            log.info(
                "[job %s] reused existing operation key (type=%s, session=%s)",
                jid,
                job_type,
                self._sid,
            )
            return jid
        if lease_acquired:
            with self._lock:
                self._job_leases[jid] = workspace_id
        self._store.mark_queued(jid)
        self._start_lease_heartbeat(jid)
        self._notify_event()
        log.info("[job %s] queued (type=%s, session=%s)", jid, job_type, self._sid)

        try:
            future = self._pool.submit(self._run, jid, fn)
        except Exception:
            self._stop_lease_heartbeat(jid)
            self._store.mark_failed(jid, "Job worker submission failed.")
            self._release_job_lease(jid)
            raise
        with self._lock:
            self._futures[jid] = future
        future.add_done_callback(lambda completed, job_id=jid: self._future_done(job_id, completed))
        return jid

    def create_with_operation_key(
        self,
        fn: JobFn,
        job_type: str,
        *,
        label: str = "",
        operation_key: str,
    ) -> str:
        """Create or reuse one durable Job for a workflow dispatch key."""
        return self.create(
            fn,
            job_type,
            label=label,
            operation_key=operation_key,
        )

    def create_durable(
        self,
        handler: str,
        payload: Dict[str, Any],
        job_type: str,
        *,
        label: str = "",
        operation_key: str = "",
    ) -> str:
        """Create a Job whose executable work is a named queue handler.

        The handler name and JSON payload are the only cross-process contract.
        The normal local callback path remains unchanged when the durable queue
        feature flag is off; callers should check ``durable_queue_enabled``
        before choosing this method.
        """
        from data.durable_queue import DurableQueueStore

        if not str(handler or "").strip():
            raise ValueError("durable handler is required")
        if not isinstance(payload, dict):
            raise ValueError("durable payload must be an object")
        parent_id = getattr(self._scope, "parent_id", "")
        workspace_id, lease_acquired = self._acquire_workspace_lease()
        queue = None
        submission_failed = False
        job_created = False
        lease_registered = False
        jid = ""
        try:
            job = self._store.create(
                self._sid,
                job_type,
                label=label,
                parent_id=parent_id,
                workspace_id=workspace_id,
                operation_key=operation_key,
            )
            jid = job["id"]
            job_created = True
            created = bool(job.pop("_created", True))
            if not created:
                job_created = False
                if lease_acquired:
                    self._release_workspace_id(workspace_id)
                return jid

            if lease_acquired:
                with self._lock:
                    self._job_leases[jid] = workspace_id
                lease_registered = True
            if not self._store.mark_queued(jid):
                raise RuntimeError("failed to queue Job for durable submission")
            self._start_lease_heartbeat(jid)
            self._notify_event()

            # Keep construction inside the protected submission region.  A
            # constructor failure has no usable handle to close, while every
            # successfully returned queue must be closed below.
            queue = DurableQueueStore()
            queue_key = str(operation_key or f"job:{jid}").strip()[:240]
            queue_payload = {
                **payload,
                "job_id": jid,
                "session_id": self._sid,
                "workspace_id": workspace_id,
                "job_store_path": str(self._store.path),
            }
            task = queue.enqueue(
                str(handler).strip(),
                queue_payload,
                operation_key=queue_key,
            )
            if not self._store.set_request_payload(
                jid,
                queue_payload,
                durable_handler=str(handler).strip(),
                queue_task_id=str(task["id"]),
            ):
                raise RuntimeError("failed to persist durable Job payload")
            if not self._store.handoff_to_queue(jid, str(task["id"])):
                raise RuntimeError("failed to hand off Job to durable queue")
        except BaseException:
            submission_failed = True
            if job_created:
                self._cleanup_durable_submission(
                    jid,
                    workspace_id=workspace_id if lease_acquired else "",
                    lease_registered=lease_registered,
                )
            elif lease_acquired:
                self._best_effort_release_workspace_id(workspace_id)
            raise
        finally:
            if queue is not None:
                try:
                    queue.close()
                except BaseException:
                    if submission_failed:
                        # Never replace an enqueue/payload/handoff exception
                        # with a failure raised while closing the connection.
                        log.exception("[job %s] durable queue close failed", jid)
                    else:
                        # A close failure is still a failed submission when
                        # no earlier error exists; settle the Job before
                        # propagating the close error.
                        self._cleanup_durable_submission(
                            jid,
                            workspace_id=workspace_id if lease_acquired else "",
                            lease_registered=lease_registered,
                        )
                        raise
        self._stop_lease_heartbeat(jid)
        # The process-local WorkspaceRuntime lease cannot be held by a queue
        # worker in another service.  The handler reacquires the workspace by
        # stable identity and releases it when the task finishes.
        self._release_job_lease(jid)
        self._notify_event()
        log.info(
            "[job %s] handed to durable queue (handler=%s, type=%s)",
            jid,
            handler,
            job_type,
        )
        return jid

    def enqueue_durable(
        self,
        jid: str,
        handler: str,
        payload: Dict[str, Any],
        *,
        operation_key: str = "",
    ) -> str:
        """Attach a reconstructable queue task to an existing tracked Job."""
        from data.durable_queue import DurableQueueStore

        job = self._store.get_for_session(self._sid, jid)
        if job is None:
            raise ValueError(f"job not found: {jid}")
        queue = None
        submission_failed = False
        try:
            if not str(handler or "").strip():
                raise ValueError("durable handler is required")
            if not isinstance(payload, dict):
                raise ValueError("durable payload must be an object")
            queue = DurableQueueStore()
            queue_key = str(operation_key or job.get("operation_key") or f"job:{jid}").strip()[:240]
            queue_payload = {
                **payload,
                "job_id": jid,
                "session_id": self._sid,
                "workspace_id": str(job.get("workspace_id") or ""),
                "job_store_path": str(self._store.path),
            }
            task = queue.enqueue(
                str(handler).strip(),
                queue_payload,
                operation_key=queue_key,
            )
            if not self._store.set_request_payload(
                jid,
                queue_payload,
                durable_handler=str(handler).strip(),
                queue_task_id=str(task["id"]),
            ):
                raise RuntimeError("failed to persist durable Job payload")
            if not self._store.handoff_to_queue(jid, str(task["id"])):
                raise RuntimeError("failed to hand off Job to durable queue")
        except BaseException:
            submission_failed = True
            # The queue task may already exist, but the Job must not be left
            # running when its durable hand-off fails.  Keep cleanup best
            # effort and idempotent so the original submission error wins.
            self._cleanup_durable_submission(jid)
            raise
        finally:
            if queue is not None:
                try:
                    queue.close()
                except BaseException:
                    if submission_failed:
                        # Queue shutdown is cleanup.  In particular, do not
                        # mask the original enqueue/handoff error with a close
                        # failure.
                        log.exception("[job %s] durable queue close failed", jid)
                    else:
                        self._cleanup_durable_submission(jid)
                        raise
        self._stop_lease_heartbeat(jid)
        self._release_job_lease(jid)
        self._notify_event()
        return str(task["id"])

    def get_request_payload(self, jid: str) -> Optional[Dict[str, Any]]:
        return self._store.get_request_payload(jid)

    def get_last_recovery_checkpoint(self, jid: str) -> Dict[str, Any]:
        getter = getattr(self._store, "get_last_recovery_checkpoint", None)
        if not callable(getter):
            return {}
        value = getter(self._sid, jid)
        return dict(value) if isinstance(value, dict) else {}

    def persist_request_payload(
        self,
        jid: str,
        payload: Dict[str, Any],
        *,
        durable_handler: str = "",
        queue_task_id: str = "",
    ) -> bool:
        """Persist a bounded reconstruction envelope for restart recovery."""
        return self._store.set_request_payload(
            jid,
            payload,
            durable_handler=durable_handler,
            queue_task_id=queue_task_id,
        )

    def reopen_for_resume(self, jid: str) -> bool:
        return self._store.reopen_for_resume(jid)

    def _acquire_workspace_lease(self) -> tuple[str, bool]:
        from data.workspace import workspace_manager

        scoped_id = str(getattr(self._scope, "workspace_id", "") or "")
        if scoped_id:
            return scoped_id, workspace_manager.acquire_job(scoped_id) is not None
        workspace_id, runtime = workspace_manager.acquire_job_for_session(self._sid)
        return str(workspace_id or ""), runtime is not None

    @staticmethod
    def _release_workspace_id(workspace_id: str) -> None:
        if workspace_id:
            from data.workspace import workspace_manager

            workspace_manager.release_job(workspace_id)

    def _release_job_lease(self, jid: str) -> None:
        with self._lock:
            workspace_id = self._job_leases.pop(jid, "")
        self._release_workspace_id(workspace_id)

    @staticmethod
    def _best_effort_release_workspace_id(workspace_id: str) -> None:
        try:
            JobRunner._release_workspace_id(workspace_id)
        except BaseException:
            log.exception(
                "[job] workspace lease cleanup failed (workspace=%s)",
                workspace_id,
            )

    def _cleanup_durable_submission(
        self,
        jid: str,
        *,
        workspace_id: str = "",
        lease_registered: bool = True,
    ) -> None:
        """Best-effort terminal cleanup that cannot replace a submission error."""
        try:
            self._stop_lease_heartbeat(jid)
        except BaseException:
            log.exception("[job %s] durable lease heartbeat cleanup failed", jid)

        failed = False
        try:
            failed = bool(self._store.mark_failed(jid, "Durable queue submission failed."))
        except BaseException:
            log.exception("[job %s] durable submission failure recording failed", jid)
        if not failed:
            canceling = False
            try:
                canceling = bool(self._store.mark_canceling(jid))
            except BaseException:
                log.exception("[job %s] durable cleanup canceling transition failed", jid)
            if canceling:
                try:
                    failed = bool(
                        self._store.mark_failed(
                            jid,
                            "Durable queue submission failed.",
                        )
                    )
                except BaseException:
                    log.exception("[job %s] durable submission failure retry failed", jid)
            if not failed:
                try:
                    self._store.mark_canceled(jid)
                except BaseException:
                    log.exception("[job %s] durable cleanup terminal transition failed", jid)

        if lease_registered:
            try:
                self._release_job_lease(jid)
            except BaseException:
                log.exception("[job %s] durable Job lease cleanup failed", jid)
        elif workspace_id:
            self._best_effort_release_workspace_id(workspace_id)

    @contextmanager
    def conversation_scope(self, parent_id: str):
        """Attach jobs created by this request to a visible conversation parent."""
        previous = getattr(self._scope, "parent_id", "")
        previous_workspace = getattr(self._scope, "workspace_id", "")
        parent = self._store.get_for_session(self._sid, parent_id) or {}
        self._scope.parent_id = parent_id
        self._scope.workspace_id = str(parent.get("workspace_id") or "")
        try:
            yield
        finally:
            self._scope.parent_id = previous
            self._scope.workspace_id = previous_workspace

    def begin_tracked(self, job_type: str, label: str = "") -> str:
        workspace_id, lease_acquired = self._acquire_workspace_lease()
        try:
            job = self._store.create(
                self._sid,
                job_type,
                label=label,
                parent_id=getattr(self._scope, "parent_id", ""),
                workspace_id=workspace_id,
            )
        except Exception:
            if lease_acquired:
                self._release_workspace_id(workspace_id)
            raise
        jid = job["id"]
        if lease_acquired:
            with self._lock:
                self._job_leases[jid] = workspace_id
        self._store.mark_queued(jid)
        self._store.mark_started(jid)
        self._start_lease_heartbeat(jid)
        self._notify_event()
        return jid

    def submit_tracked(
        self,
        jid: str,
        fn: JobFn,
        on_cancel_before_start: Optional[Callable[[], None]] = None,
    ) -> Future:
        """Run an already-created tracked job in the detached chat slot.

        ``begin_tracked`` is intentionally separate from submission because
        callers need the durable job ID before the HTTP response is opened.
        The returned Future is only an internal lifecycle handle; progress and
        recovery continue to use the durable JobsStore record.
        """
        job = self._store.get_for_session(self._sid, jid)
        if job is None:
            raise ValueError(f"job not found: {jid}")
        with self._lock:
            if jid in self._futures:
                raise ValueError(f"job already submitted: {jid}")
            # Register the cleanup before submitting to the executor.  The
            # worker may start immediately, and an explicit stop can arrive
            # between submit() and the future bookkeeping below.  Keeping the
            # callback visible for that whole window closes the pre-start
            # cancellation leak without changing the normal worker path.
            if on_cancel_before_start is not None:
                self._prestart_cleanups[jid] = on_cancel_before_start
        try:
            future = self._detached_pool.submit(self._run, jid, fn)
        except Exception:
            with self._lock:
                self._prestart_cleanups.pop(jid, None)
            self._stop_lease_heartbeat(jid)
            self._store.mark_failed(jid, "Job worker submission failed.")
            self._release_job_lease(jid)
            raise
        with self._lock:
            self._futures[jid] = future
        future.add_done_callback(lambda completed, job_id=jid: self._future_done(job_id, completed))
        return future

    def append_tracked_event(self, jid: str, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        persisted = self._store.append_event(
            jid,
            event,
            owner_id=self._store.owner_id,
        )
        if persisted is not None:
            self._notify_event()
        return persisted

    def update_tracked(self, jid: str, progress: int, message: str = "") -> None:
        if self._store.set_progress(jid, progress, message):
            self._notify_event()

    def succeed_tracked(self, jid: str, result: Any) -> None:
        if self._store.mark_succeeded(jid, result):
            self._stop_lease_heartbeat(jid)
            self._release_job_lease(jid)
            self._notify_event()

    def fail_tracked(
        self,
        jid: str,
        error: str,
        *,
        error_code: str = "",
        recovery_action: str = "",
    ) -> None:
        if self._store.mark_failed(
            jid,
            error,
            error_code=error_code,
            recovery_action=recovery_action,
        ):
            self._release_job_lease(jid)
            self._stop_lease_heartbeat(jid)
            self._notify_event()

    def timeout_tracked(
        self,
        jid: str,
        error: str,
        *,
        error_code: str = "job_timeout",
        recovery_action: str = "retry_with_smaller_scope",
    ) -> bool:
        """Stop a child job and persist a deadline failure.

        The timeout reason is recorded before the cooperative cancel signal is
        visible to the worker. If the worker wins the small race and handles
        the signal first, it still writes the same failed terminal state rather
        than downgrading a deadline into an ordinary user cancellation.
        """
        job = self._store.get_for_session(self._sid, jid)
        if job is None or job["status"] in _TERMINAL:
            return False
        with self._lock:
            # An explicit cancel already owns this job. Preserve that user
            # decision instead of relabeling it as a timeout.
            if jid in self._canceled:
                return False
            self._canceled.add(jid)
            self._timed_out[jid] = {
                "error": str(error),
                "error_code": str(error_code or "job_timeout"),
                "recovery_action": str(recovery_action or "retry_with_smaller_scope"),
            }

        for child in self._store.list_children(self._sid, jid):
            if child["status"] not in _TERMINAL:
                self.cancel(child["id"])

        # QUEUED jobs cannot transition directly to failed; the intermediate
        # canceling state also makes their intent visible to API clients.
        self._store.mark_canceling(jid)
        changed = self._store.mark_failed(
            jid,
            str(error),
            error_code=str(error_code or "job_timeout"),
            recovery_action=str(recovery_action or "retry_with_smaller_scope"),
        )
        current = self._store.get_for_session(self._sid, jid)
        if not changed and (current is None or current["status"] != STATUS_FAILED):
            with self._lock:
                self._timed_out.pop(jid, None)
                self._canceled.discard(jid)
            return False
        if changed:
            self._stop_lease_heartbeat(jid)
            self._release_job_lease(jid)
            self._notify_event()
            self._notify_terminal_listeners(jid)
        return True

    def cancel_tracked(self, jid: str) -> None:
        job = self._store.get_for_session(self._sid, jid)
        if job and job["status"] not in _TERMINAL:
            for child in self._store.list_children(self._sid, jid):
                if child["status"] not in _TERMINAL:
                    self.cancel(child["id"])
            if job["status"] != STATUS_CANCELING:
                self._store.mark_canceling(jid)
            self._store.mark_canceled(jid)
            self._stop_lease_heartbeat(jid)
            self._release_job_lease(jid)
            self._notify_event()

    def _run(self, jid: str, fn: JobFn) -> None:
        try:
            if self._is_canceled(jid):
                self._run_prestart_cleanup(jid)
                self._close_canceled_job(jid)
                return
            if not self._store.mark_started(jid):
                # Cancellation may win after the first check but before the
                # durable running transition.  Treat that as a pre-start
                # cancellation as well; otherwise the owner snapshot can be
                # stranded even though the Job is already canceled.
                if self._is_canceled(jid):
                    self._run_prestart_cleanup(jid)
                return
            self._start_lease_heartbeat(jid)
            self._notify_event()
            log.info("[job %s] running", jid)
            job = self._store.get(jid) or {}
            workspace_id = str(job.get("workspace_id") or "")
            runtime = None
            if workspace_id:
                from data.workspace import workspace_manager

                runtime = workspace_manager.get_by_workspace(workspace_id)
            ctx = JobContext(
                jid,
                self._store,
                self._is_canceled,
                self._notify_event,
                workspace_id=workspace_id,
                runtime=runtime,
            )
            try:
                result = fn(ctx)
                if self._is_canceled(jid):
                    current = self._close_canceled_job(jid)
                    log.info(
                        "[job %s] canceled after current step (status=%s)",
                        jid,
                        current.get("status"),
                    )
                else:
                    current = self._store.get(jid) or {}
                    if current.get("status") not in _TERMINAL:
                        self._store.mark_succeeded(jid, result)
                        log.info("[job %s] succeeded", jid)
                    else:
                        # Some tracked workflows close their parent job before
                        # returning from the worker (for example a detached
                        # conversation that has already persisted its result).
                        # Do not attempt an invalid terminal -> terminal
                        # transition or report a misleading warning.
                        log.info(
                            "[job %s] worker completed with terminal status=%s",
                            jid,
                            current.get("status"),
                        )
            except JobCanceled:
                current = self._close_canceled_job(jid)
                log.info("[job %s] canceled cooperatively", jid)
            except Exception as exc:
                current = self._store.get(jid) or {}
                if current.get("status") in _TERMINAL:
                    # A caller may have closed the Job while the worker was
                    # unwinding. The durable terminal state wins; never turn
                    # an explicit cancellation into a late failure.
                    log.info(
                        "[job %s] worker raised after terminal status=%s",
                        jid,
                        current.get("status"),
                    )
                elif self._is_canceled(jid):
                    self._close_canceled_job(jid)
                    log.info("[job %s] cancellation won over worker error", jid)
                else:
                    self._store.mark_failed(jid, f"{type(exc).__name__}: {exc}")
                    log.exception("[job %s] failed", jid)
        finally:
            self._stop_lease_heartbeat(jid)
            self._release_job_lease(jid)
            self._notify_event()
            self._notify_terminal_listeners(jid)

    def _forget_future(self, jid: str) -> None:
        with self._lock:
            self._futures.pop(jid, None)

    def _start_lease_heartbeat(self, jid: str) -> None:
        """Renew the durable Job lease while its callback is still running."""
        renew_lease = getattr(self._store, "touch_lease", None)
        if not callable(renew_lease):
            return
        stop = threading.Event()
        interval = max(
            0.02,
            min(float(getattr(self._store, "lease_seconds", 30.0)) / 4.0, 5.0),
        )

        def renew() -> None:
            try:
                while not stop.is_set():
                    try:
                        if not renew_lease(jid):
                            return
                    except Exception:
                        # A transient SQLite/filesystem error must not silently
                        # kill the heartbeat while the callback is still live.
                        # Keep retrying on the next interval; terminal state or
                        # runner shutdown will make the update return False.
                        log.exception("[job %s] lease heartbeat failed", jid)
                    if stop.wait(interval):
                        return
            finally:
                # Natural expiry, a terminal job, and an explicit stop all
                # converge here.  Match the Event as well as the thread so an
                # old heartbeat cannot remove a newer registration for the
                # same Job ID.
                with self._lock:
                    if self._lease_stops.get(jid) is stop:
                        self._lease_stops.pop(jid, None)
                        self._lease_threads.pop(jid, None)

        thread = threading.Thread(
            target=renew,
            name=f"lease-{jid[:8]}",
            daemon=True,
        )
        with self._lock:
            if self._shutting_down or jid in self._lease_stops:
                return
            # Register both objects before starting.  Starting while holding
            # the runner lock closes the shutdown/start race: shutdown cannot
            # observe an unstarted thread and then miss it after start().
            self._lease_stops[jid] = stop
            self._lease_threads[jid] = thread
            try:
                thread.start()
            except BaseException:
                self._lease_stops.pop(jid, None)
                self._lease_threads.pop(jid, None)
                stop.set()
                raise

    def _stop_lease_heartbeat(self, jid: str) -> None:
        with self._lock:
            stop = self._lease_stops.get(jid)
            thread = self._lease_threads.get(jid)
        if stop is None:
            return

        stop.set()
        if thread is None or thread is threading.current_thread():
            # A heartbeat may stop itself after a terminal touch result.  A
            # thread cannot join itself; its finally block removes the
            # registration after the last store call has returned.
            return
        if thread.ident is None:
            # Defensive boundary for a registered-but-never-started Thread.
            # Production registration starts under the same lock, but a
            # failed/custom Thread implementation must not make stop() call
            # Thread.join() before start().
            with self._lock:
                if self._lease_stops.get(jid) is stop:
                    self._lease_stops.pop(jid, None)
                    self._lease_threads.pop(jid, None)
            return

        # No timeout: shutdown must not return while this thread can still
        # call touch_lease on a store that its owner is about to close.
        thread.join()
        with self._lock:
            if self._lease_stops.get(jid) is stop:
                self._lease_stops.pop(jid, None)
                self._lease_threads.pop(jid, None)

    def _run_prestart_cleanup(self, jid: str) -> None:
        """Run and forget a cleanup owned by a canceled, not-yet-started job."""
        with self._lock:
            cleanup = self._prestart_cleanups.pop(jid, None)
        if cleanup is None:
            return
        try:
            cleanup()
        except Exception:
            log.exception("[job %s] pre-start cleanup failed", jid)

    def _future_done(self, jid: str, future: Future) -> None:
        self._forget_future(jid)
        # Cancellation is a per-future signal, not durable job state. Drop it
        # after the worker (or a pre-start cancellation) has finished so a
        # long-lived session cannot retain every completed job id forever.
        with self._lock:
            self._canceled.discard(jid)
            self._timed_out.pop(jid, None)
        if future.cancelled():
            self._stop_lease_heartbeat(jid)
            self._release_job_lease(jid)
            self._run_prestart_cleanup(jid)
        else:
            with self._lock:
                self._prestart_cleanups.pop(jid, None)

    def add_terminal_listener(
        self,
        jid: str,
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Call *callback* once after the Job terminal state is durable."""
        immediate = None
        with self._lock:
            job = self._store.get_for_session(self._sid, jid)
            if job is None:
                raise ValueError(f"job not found: {jid}")
            if job["status"] in _TERMINAL:
                immediate = job
            else:
                self._terminal_listeners.setdefault(jid, []).append(callback)
        if immediate is not None:
            callback(immediate)

    def _notify_terminal_listeners(self, jid: str) -> None:
        job = self._store.get_for_session(self._sid, jid)
        if job is None or job["status"] not in _TERMINAL:
            return
        with self._lock:
            callbacks = self._terminal_listeners.pop(jid, [])
        for callback in callbacks:
            try:
                callback(job)
            except Exception:
                log.exception("[job %s] terminal listener failed", jid)

    def remove_terminal_listeners(self, jid: str) -> int:
        """Remove pending callbacks when an owning workflow is disposed."""
        with self._lock:
            callbacks = self._terminal_listeners.pop(jid, [])
        return len(callbacks)

    def _is_canceled(self, jid: str) -> bool:
        with self._lock:
            return jid in self._canceled

    def _close_canceled_job(self, jid: str) -> Dict[str, Any]:
        """Close a cooperatively stopped job without losing timeout metadata."""
        with self._lock:
            timeout = dict(self._timed_out.get(jid) or {})
        current = self._store.get(jid) or {}
        if current.get("status") in _TERMINAL:
            return current
        if timeout:
            self._store.mark_failed(
                jid,
                timeout["error"],
                error_code=timeout["error_code"],
                recovery_action=timeout["recovery_action"],
            )
        else:
            self._store.mark_canceled(jid)
        return self._store.get(jid) or current

    # ── Cancellation ──────────────────────────────────────────────────────

    def cancel(self, jid: str) -> bool:
        job = self._store.get_for_session(self._sid, jid)
        if job is None or job["status"] in _TERMINAL:
            return False

        with self._lock:
            if jid in self._timed_out:
                return False
            self._canceled.add(jid)
            future = self._futures.get(jid)

        for child in self._store.list_children(self._sid, jid):
            if child["status"] not in _TERMINAL:
                self.cancel(child["id"])

        if not self._store.mark_canceling(jid):
            with self._lock:
                self._canceled.discard(jid)
            return False
        canceled_before_start = future is not None and future.cancel()
        # Durable queue Jobs have no process-local Future.  Once the user has
        # requested cancellation there is no callback left to observe the
        # cancel flag, so close the Job immediately; a queue worker will see
        # the terminal state and settle its task without invoking the handler.
        canceled_durable_task = future is None and bool(job.get("queue_task_id"))
        if canceled_before_start or canceled_durable_task:
            self._store.mark_canceled(jid)
            self._stop_lease_heartbeat(jid)
            self._release_job_lease(jid)
            self._notify_terminal_listeners(jid)
        self._notify_event()
        return True

    # ── Events/query bridge ───────────────────────────────────────────────

    def _maybe_recover_expired_jobs(self) -> None:
        recover = getattr(self._store, "recover_expired_jobs", None)
        if not callable(recover):
            return
        now = time.monotonic()
        with self._lock:
            if now - self._last_recovery_check < 0.5:
                return
            self._last_recovery_check = now
        try:
            if recover():
                self._notify_event()
        except Exception:
            # Status reads must remain available if a transient SQLite lock or
            # filesystem error prevents the opportunistic recovery check.
            log.exception("[job] expired-job recovery check failed")

    def _notify_event(self) -> None:
        with self._event_condition:
            self._event_condition.notify_all()

    def wait_for_events(self, after_sequence: int, timeout: float = 0.2) -> bool:
        if self._store.last_sequence(self._sid) > after_sequence:
            return True
        with self._event_condition:
            self._event_condition.wait(timeout=max(0.0, timeout))
        return self._store.last_sequence(self._sid) > after_sequence

    def list_events(
        self,
        after_sequence: int = 0,
        limit: int = 200,
        job_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self._maybe_recover_expired_jobs()
        if job_id and self._store.get_for_session(self._sid, job_id) is None:
            return []
        return self._store.list_events(
            self._sid,
            after_sequence=after_sequence,
            limit=limit,
            job_id=job_id,
        )

    def iter_events(
        self,
        jid: str,
        after_sequence: int = 0,
        timeout: Optional[float] = None,
        cancel_check: Optional[Callable[[], None]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield persisted events until ``jid`` reaches a terminal state.

        ``cancel_check`` lets a parent worker interrupt its wait without
        polling a provider or child job forever.  The callback may raise the
        caller's cooperative cancellation exception.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        sequence = max(0, int(after_sequence))
        while True:
            if cancel_check is not None:
                cancel_check()
            events = self.list_events(sequence, job_id=jid)
            for event in events:
                sequence = event["sequence"]
                yield event
            job = self.get_status(jid)
            if job is None or (job["status"] in _TERMINAL and not events):
                return
            if deadline is not None and time.monotonic() >= deadline:
                return
            wait = 0.2 if deadline is None else min(0.2, max(0.0, deadline - time.monotonic()))
            self.wait_for_events(sequence, wait)

    def publish_event(self, event: JobEvent) -> Optional[Dict[str, Any]]:
        persisted = self._store.append_event(
            event.job_id,
            serialize_event(event),
            owner_id=self._store.owner_id,
        )
        if persisted is not None:
            self._notify_event()
        return persisted

    def get_status(self, jid: str) -> Optional[Dict[str, Any]]:
        self._maybe_recover_expired_jobs()
        return self._store.get_for_session(self._sid, jid)

    def get_by_operation_key(self, operation_key: str) -> Optional[Dict[str, Any]]:
        getter = getattr(self._store, "get_by_operation_key", None)
        if not callable(getter):
            return None
        return getter(self._sid, operation_key)

    def list_jobs(
        self,
        active_only: bool = False,
        limit: int = 100,
        top_level_only: bool = False,
    ) -> List[Dict[str, Any]]:
        self._maybe_recover_expired_jobs()
        if active_only:
            return self._store.list_active(
                self._sid,
                top_level_only=top_level_only,
            )[: max(1, int(limit))]
        return self._store.list_by_session(
            self._sid,
            limit=limit,
            top_level_only=top_level_only,
        )

    def list_artifacts(self, job_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        return self._store.list_artifacts(self._sid, job_ids)

    def list_detail_events(self, job_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        return self._store.list_detail_events(self._sid, job_ids)

    def get_status_for_session(self, session_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get_for_session(session_id, job_id)

    def purge_terminal_for_session(self, session_id: str, job_ids: List[str]) -> int:
        return self._store.purge_terminal_ids(session_id, job_ids)

    def clear_terminal(self) -> int:
        return self._store.clear_terminal(self._sid)

    @property
    def session_id(self) -> str:
        return self._sid

    @property
    def last_sequence(self) -> int:
        return self._store.last_sequence(self._sid)

    @property
    def oldest_sequence(self) -> int:
        return self._store.oldest_sequence(self._sid)

    def shutdown(self, wait: bool = True) -> None:
        try:
            with self._lock:
                self._shutting_down = True
                lease_ids = list(self._lease_stops)
            # Stop and join heartbeats before returning from shutdown, even
            # when executor workers are deliberately not awaited.  This is
            # the resource-ordering boundary for the JobsStore owner.
            for jid in lease_ids:
                self._stop_lease_heartbeat(jid)
            self._pool.shutdown(wait=wait, cancel_futures=True)
            self._detached_pool.shutdown(wait=wait, cancel_futures=True)
            log.info("[job] runner shutdown (session=%s)", self._sid)
        except Exception:
            log.exception("[job] shutdown error")


def empty_job(ctx: JobContext, duration: float = 0.3) -> Dict[str, Any]:
    """Small progress-reporting task used by the B1 end-to-end tests."""
    ticks = 0
    steps = max(1, int(duration / 0.05))
    for index in range(steps + 1):
        ctx.check_canceled()
        progress = int(index * 100 / steps)
        ctx.set_progress(progress, f"step {index}/{steps}")
        ticks += 1
        time.sleep(0.05)
    return {"duration": duration, "ticks": ticks}
