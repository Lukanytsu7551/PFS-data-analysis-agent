import tempfile
import threading
import time
import unittest
import warnings
from pathlib import Path

from agent.jobs import JobRunner
from data.jobs_store import JobsStore


class JobHeartbeatShutdownTests(unittest.TestCase):
    def test_shutdown_waits_for_heartbeat_before_store_close(self):
        """A non-waiting executor shutdown still drains the lease thread."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            with tempfile.TemporaryDirectory(prefix="pfs-heartbeat-shutdown-") as temp_dir:
                store = JobsStore(Path(temp_dir) / "jobs.db", lease_seconds=0.1)
                runner = JobRunner("heartbeat-shutdown-session", store, max_workers=1)
                shutdown_finished = threading.Event()
                touch_entered = threading.Event()
                allow_touch = threading.Event()
                calls = []
                touch_errors = []

                job = store.create(runner.session_id, "heartbeat_probe")
                job_id = job["id"]
                store.mark_queued(job_id)
                store.mark_started(job_id)
                original_touch = store.touch_lease

                def gated_touch(jid, *, owner_id=""):
                    calls.append(jid)
                    touch_entered.set()
                    if not allow_touch.wait(timeout=2.0):
                        raise AssertionError("heartbeat touch was not released")
                    try:
                        return original_touch(jid, owner_id=owner_id)
                    except BaseException as exc:
                        touch_errors.append(exc)
                        raise

                store.touch_lease = gated_touch
                try:
                    runner._start_lease_heartbeat(job_id)
                    heartbeat = runner._lease_threads[job_id]
                    self.assertTrue(touch_entered.wait(timeout=2.0))

                    def shutdown_runner():
                        try:
                            runner.shutdown(wait=False)
                        finally:
                            shutdown_finished.set()

                    shutdown_thread = threading.Thread(target=shutdown_runner)
                    shutdown_thread.start()
                    self.assertFalse(
                        shutdown_finished.wait(timeout=0.15),
                        "runner shutdown returned before the heartbeat exited",
                    )
                    self.assertTrue(heartbeat.is_alive())

                    allow_touch.set()
                    self.assertTrue(shutdown_finished.wait(timeout=2.0))
                    shutdown_thread.join(timeout=2.0)
                    self.assertFalse(heartbeat.is_alive())
                    self.assertNotIn(job_id, runner._lease_stops)
                    self.assertNotIn(job_id, runner._lease_threads)

                    calls_before_close = len(calls)
                    store.close()
                    time.sleep(0.1)
                    self.assertEqual(calls_before_close, len(calls))
                    self.assertEqual([], touch_errors)
                finally:
                    allow_touch.set()
                    runner.shutdown(wait=True)
                    store.close()

    def test_natural_heartbeat_exit_cleans_both_handles_and_stop_is_repeatable(self):
        class NaturalExitStore:
            lease_seconds = 0.1

            def __init__(self):
                self.touched = threading.Event()

            def touch_lease(self, _jid):
                self.touched.set()
                return False

        store = NaturalExitStore()
        runner = JobRunner("heartbeat-natural-exit-session", store, max_workers=1)
        try:
            runner._start_lease_heartbeat("natural-exit")
            heartbeat = runner._lease_threads["natural-exit"]
            self.assertTrue(store.touched.wait(timeout=2.0))
            heartbeat.join(timeout=2.0)
            self.assertFalse(heartbeat.is_alive())
            self.assertNotIn("natural-exit", runner._lease_stops)
            self.assertNotIn("natural-exit", runner._lease_threads)

            runner._stop_lease_heartbeat("natural-exit")
            runner._stop_lease_heartbeat("natural-exit")
        finally:
            runner.shutdown(wait=True)

    def test_stop_from_heartbeat_thread_does_not_self_join(self):
        class SelfStoppingStore:
            lease_seconds = 0.1

            def __init__(self):
                self.touched = threading.Event()
                self.calls = 0

            def touch_lease(self, _jid):
                self.calls += 1
                if self.calls == 1:
                    runner._stop_lease_heartbeat("self-stop")
                    self.touched.set()
                return True

        store = SelfStoppingStore()
        runner = JobRunner("heartbeat-self-stop-session", store, max_workers=1)
        try:
            runner._start_lease_heartbeat("self-stop")
            heartbeat = runner._lease_threads["self-stop"]
            self.assertTrue(store.touched.wait(timeout=2.0))
            heartbeat.join(timeout=2.0)
            self.assertFalse(heartbeat.is_alive())
            self.assertNotIn("self-stop", runner._lease_stops)
            self.assertNotIn("self-stop", runner._lease_threads)
        finally:
            runner.shutdown(wait=True)

    def test_stop_handles_a_registered_thread_that_never_started(self):
        store = object()
        runner = JobRunner("heartbeat-unstarted-session", store, max_workers=1)
        stop = threading.Event()
        heartbeat = threading.Thread(target=lambda: None)
        try:
            with runner._lock:
                runner._lease_stops["unstarted"] = stop
                runner._lease_threads["unstarted"] = heartbeat

            runner._stop_lease_heartbeat("unstarted")
            self.assertTrue(stop.is_set())
            self.assertIsNone(heartbeat.ident)
            self.assertNotIn("unstarted", runner._lease_stops)
            self.assertNotIn("unstarted", runner._lease_threads)
        finally:
            runner.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
