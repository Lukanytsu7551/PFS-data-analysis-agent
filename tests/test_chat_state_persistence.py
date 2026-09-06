import json
import sqlite3
import threading
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from data.chat_state_store import ChatStateStore
from data.session import ChatSession, SessionManager, _SESSION_TTL


class _NullChatStateStore:
    """In-memory state-store seam for session lifecycle tests."""

    def get(self, _session_id):
        return None


class _BlockingJobRunner:
    def __init__(self):
        self.cleanup_entered = threading.Event()
        self.allow_cleanup = threading.Event()
        self.shutdown = Mock()

    def list_jobs(self, *, active_only=False, limit=100, top_level_only=False):
        del active_only, limit, top_level_only
        self.cleanup_entered.set()
        if not self.allow_cleanup.wait(timeout=2.0):
            raise AssertionError("cleanup probe was not released")
        return []


class _LifecycleLockProbe:
    """Track lifecycle-lock acquisition without changing production code."""

    def __init__(self):
        self._lock = threading.RLock()
        self._roles = {}
        self._roles_lock = threading.Lock()
        self.attempted = {}
        self.acquired = {}

    def register(self, role):
        with self._roles_lock:
            self._roles[threading.get_ident()] = role
            self.attempted[role] = threading.Event()
            self.acquired[role] = threading.Event()

    def _role(self):
        with self._roles_lock:
            return self._roles.get(threading.get_ident())

    def acquire(self, *args, **kwargs):
        role = self._role()
        if role is not None:
            self.attempted[role].set()
        acquired = self._lock.acquire(*args, **kwargs)
        if acquired and role is not None:
            self.acquired[role].set()
        return acquired

    def release(self):
        return self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        del exc_type, exc_value, traceback
        self.release()


class ChatStatePersistenceTests(unittest.TestCase):
    def test_independent_session_managers_restore_completed_state(self):
        with TemporaryDirectory(prefix="pfs-chat-state-") as temp_dir:
            path = Path(temp_dir) / "chat-state.sqlite3"
            writer_store = ChatStateStore(path)
            reader_store = None
            try:
                writer = SessionManager(writer_store)
                session_id = f"cross-service-state-{uuid.uuid4().hex[:10]}"
                session = writer.get_or_create(session_id)
                session.model_provider = "kimi"
                session.workspace_id = "workspace-for-recovery"
                session.add_user("请分析本月销售额")
                session.add_assistant("本月销售额为 42 万元。")
                session.record_usage(120, 38, cost_usd=0.01)
                self.assertTrue(writer.persist(session_id))

                reader_store = ChatStateStore(path)
                reader = SessionManager(reader_store)
                restored = reader.get_or_create(session_id)
                self.assertEqual("kimi", restored.model_provider)
                self.assertEqual("workspace-for-recovery", restored.workspace_id)
                self.assertEqual(
                    [
                        {"role": "user", "content": "请分析本月销售额"},
                        {"role": "assistant", "content": "本月销售额为 42 万元。"},
                    ],
                    restored.history,
                )
                self.assertEqual(120, restored.total_input_tokens)
                self.assertEqual(38, restored.total_output_tokens)
            finally:
                if reader_store is not None:
                    reader_store.close()
                writer_store.close()

    def test_snapshot_removes_credentials_and_rejects_stale_revision(self):
        with TemporaryDirectory(prefix="pfs-chat-state-fence-") as temp_dir:
            path = Path(temp_dir) / "chat-state.sqlite3"
            first_store = ChatStateStore(path)
            second_store = ChatStateStore(path)
            try:
                session_id = f"chat-state-fence-{uuid.uuid4().hex[:10]}"
                self.assertEqual(
                    1,
                    first_store.save(
                        session_id,
                        {
                            "history": [],
                            "api_key": "must-not-persist",
                            "total_input_tokens": 9,
                        },
                    ),
                )
                saved = first_store.get(session_id)
                self.assertNotIn("api_key", saved["state"])
                self.assertEqual(9, saved["state"]["total_input_tokens"])
                self.assertIsNone(
                    second_store.save(
                        session_id,
                        {"history": [{"role": "user", "content": "stale"}]},
                        expected_revision=0,
                    )
                )
            finally:
                second_store.close()
                first_store.close()

    def test_legacy_schema_is_migrated_without_losing_state_and_supports_save(self):
        """A pre-metadata table remains readable and writable after startup."""
        with TemporaryDirectory(prefix="pfs-chat-state-legacy-") as temp_dir:
            path = Path(temp_dir) / "chat-state.sqlite3"
            session_id = f"legacy-chat-state-{uuid.uuid4().hex[:10]}"
            legacy_state = {
                "history": [{"role": "user", "content": "旧版本数据"}],
                "legacy_marker": "keep-me",
            }
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "CREATE TABLE chat_session_states ("
                    "session_id TEXT PRIMARY KEY, "
                    "revision INTEGER NOT NULL, "
                    "state_json TEXT NOT NULL, "
                    "updated_at REAL NOT NULL"
                    ")"
                )
                connection.execute(
                    "INSERT INTO chat_session_states "
                    "(session_id, revision, state_json, updated_at) VALUES (?, ?, ?, ?)",
                    (session_id, 7, json.dumps(legacy_state, ensure_ascii=False), 123.0),
                )
                connection.commit()
            finally:
                connection.close()

            store = ChatStateStore(path)
            try:
                restored = store.get(session_id)
                self.assertIsNotNone(restored)
                self.assertEqual(session_id, restored["session_id"])
                self.assertEqual(7, restored["revision"])
                self.assertEqual(legacy_state, restored["state"])
                self.assertEqual("", restored["owner_user_id"])
                self.assertEqual("", restored["workspace_id"])
                self.assertEqual("", restored["model_provider"])

                updated_state = {**legacy_state, "new_marker": "after-migration"}
                self.assertEqual(
                    8,
                    store.save(
                        session_id,
                        updated_state,
                        expected_revision=7,
                    ),
                )
                updated = store.get(session_id)
                self.assertEqual(updated_state["history"], updated["state"]["history"])
                self.assertEqual(
                    updated_state["legacy_marker"],
                    updated["state"]["legacy_marker"],
                )
                self.assertEqual(updated_state["new_marker"], updated["state"]["new_marker"])
                self.assertEqual(8, updated["revision"])
            finally:
                store.close()

    def test_expired_cleanup_rechecks_a_session_accessed_during_scan(self):
        """A concurrent access must win over a stale cleanup snapshot."""
        state_store = _NullChatStateStore()
        manager = SessionManager(state_store)
        cleanup_thread = None
        probe = None
        try:
            session_id = f"session-cleanup-race-{uuid.uuid4().hex[:10]}"
            session = manager.get_or_create(session_id)
            probe = _BlockingJobRunner()
            session._job_runner = probe
            session.last_accessed = datetime.now() - timedelta(seconds=_SESSION_TTL + 1)

            with patch.object(SessionManager, "_release"):
                cleanup_thread = threading.Thread(target=manager._cleanup_expired)
                cleanup_thread.start()
                self.assertTrue(probe.cleanup_entered.wait(timeout=2.0))

                # Refresh the same object while cleanup is still deciding
                # whether the old snapshot is expired.
                self.assertIs(session, manager.get_or_create(session_id))
                probe.allow_cleanup.set()
                cleanup_thread.join(timeout=3.0)
                self.assertFalse(cleanup_thread.is_alive())
                self.assertIs(session, manager.get(session_id))
        finally:
            if probe is not None:
                probe.allow_cleanup.set()
            if cleanup_thread is not None:
                cleanup_thread.join(timeout=3.0)
            manager.close()

    def test_concurrent_get_or_create_returns_one_session_instance(self):
        """Concurrent callers must share one in-memory ChatSession."""
        manager = SessionManager(_NullChatStateStore())
        session_id = f"session-get-create-race-{uuid.uuid4().hex[:10]}"
        barrier = threading.Barrier(12)
        sessions = []
        errors = []
        result_lock = threading.Lock()

        def get_session():
            try:
                barrier.wait(timeout=3.0)
                session = manager.get_or_create(session_id)
                with result_lock:
                    sessions.append(session)
            except BaseException as exc:  # pragma: no cover - assertion below
                with result_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=get_session) for _ in range(12)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=4.0)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual([], errors)
            self.assertEqual(12, len(sessions))
            self.assertEqual(1, len({id(session) for session in sessions}))
            self.assertEqual(1, len(manager._store))
        finally:
            for thread in threads:
                thread.join(timeout=1.0)
            manager.close()

    def test_chat_session_job_runner_initialization_is_singleton_under_concurrency(self):
        """Concurrent lazy access must not leak duplicate JobRunner pools."""
        session = ChatSession(session_id=f"runner-race-{uuid.uuid4().hex[:10]}")
        factory_calls = []
        factory_lock = threading.Lock()
        second_factory_called = threading.Event()
        barrier = threading.Barrier(2)
        runners = []
        errors = []

        def make_runner(*_args, **_kwargs):
            runner = Mock()
            with factory_lock:
                factory_calls.append(runner)
                index = len(factory_calls)
            if index == 1:
                # Without a per-session initialization lock, the second
                # property access enters before the first assignment.
                second_factory_called.wait(timeout=1.0)
            else:
                second_factory_called.set()
            return runner

        def access_runner():
            try:
                barrier.wait(timeout=2.0)
                runner = session.job_runner
                with factory_lock:
                    runners.append(runner)
            except BaseException as exc:  # pragma: no cover - assertion below
                with factory_lock:
                    errors.append(exc)

        with (
            patch("data.session.get_global_jobs_store", return_value=object()),
            patch("agent.jobs.JobRunner", side_effect=make_runner),
        ):
            threads = [threading.Thread(target=access_runner) for _ in range(2)]
            try:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=3.0)
                self.assertTrue(all(not thread.is_alive() for thread in threads))
                self.assertEqual([], errors)
                self.assertEqual(2, len(runners))
                self.assertEqual(1, len(factory_calls))
                self.assertIs(runners[0], runners[1])
            finally:
                for thread in threads:
                    thread.join(timeout=1.0)

        session._job_runner = None

    def test_close_stops_cleanup_thread_and_releases_session_runner_once(self):
        """Manager close is idempotent and releases session-owned workers."""
        manager = SessionManager(_NullChatStateStore())
        session = manager.create()
        runner = Mock()
        session._job_runner = runner
        cleanup_thread = manager._cleanup_thread

        def release_session(_sid, current_session, *, wait=False):
            current_session.shutdown_job_runner(wait=wait)

        with patch.object(SessionManager, "_release", side_effect=release_session) as release:
            manager.close()
            manager.close()

        self.assertFalse(cleanup_thread.is_alive())
        self.assertIsNone(manager._cleanup_thread)
        self.assertEqual({}, manager._store)
        release.assert_called_once_with(session.session_id, session, wait=True)
        runner.shutdown.assert_called_once_with(wait=True)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            manager.create()

    def test_get_during_close_never_returns_session_being_released(self):
        """Once close starts, concurrent readers cannot receive its session."""
        manager = SessionManager(_NullChatStateStore())
        session = manager.create()
        release_entered = threading.Event()
        allow_release = threading.Event()
        get_finished = threading.Event()
        observed = {}

        def blocking_release(_sid, _session, *, wait=False):
            del wait
            release_entered.set()
            if not allow_release.wait(timeout=2.0):
                raise AssertionError("session release was not released")

        def read_session():
            observed["session"] = manager.get(session.session_id)
            get_finished.set()

        close_thread = None
        get_thread = None
        try:
            with patch.object(SessionManager, "_release", side_effect=blocking_release):
                close_thread = threading.Thread(target=manager.close)
                close_thread.start()
                self.assertTrue(release_entered.wait(timeout=2.0))

                get_thread = threading.Thread(target=read_session)
                get_thread.start()
                allow_release.set()

                self.assertTrue(get_finished.wait(timeout=2.0))
                get_thread.join(timeout=2.0)
                close_thread.join(timeout=2.0)
                self.assertFalse(get_thread.is_alive())
                self.assertFalse(close_thread.is_alive())
                self.assertIsNone(observed["session"])
        finally:
            allow_release.set()
            if get_thread is not None:
                get_thread.join(timeout=2.0)
            if close_thread is not None:
                close_thread.join(timeout=2.0)
            manager.close()

    def test_get_or_create_close_race_does_not_return_released_session(self):
        """Hydration must finish before close can release the same session."""
        manager = SessionManager(_NullChatStateStore())
        session = manager.create()
        lifecycle_lock = _LifecycleLockProbe()
        manager._lifecycle_lock = lifecycle_lock

        hydrate_entered = threading.Event()
        allow_hydrate = threading.Event()
        release_entered = threading.Event()
        allow_release = threading.Event()
        get_finished = threading.Event()
        close_started = threading.Event()
        close_finished = threading.Event()
        observed = {}
        errors = []
        events = []
        event_lock = threading.Lock()

        def record(event):
            with event_lock:
                events.append(event)

        def blocking_hydrate(_session):
            hydrate_entered.set()
            if not allow_hydrate.wait(timeout=3.0):
                raise AssertionError("hydration probe was not released")

        def blocking_release(_sid, current_session, *, wait=False):
            del wait
            record("release_entered")
            current_session._test_released = True
            release_entered.set()
            if not allow_release.wait(timeout=3.0):
                raise AssertionError("release probe was not released")
            record("release_finished")

        def read_session():
            lifecycle_lock.register("getter")
            try:
                result = manager.get_or_create(session.session_id)
                observed["session"] = result
                observed["released_at_return"] = getattr(result, "_test_released", False)
                record("get_returned")
            except BaseException as exc:  # pragma: no cover - assertion below
                errors.append(exc)
            finally:
                get_finished.set()

        def close_manager():
            lifecycle_lock.register("closer")
            close_started.set()
            try:
                manager.close()
            except BaseException as exc:  # pragma: no cover - assertion below
                errors.append(exc)
            finally:
                close_finished.set()
                record("close_finished")

        getter = threading.Thread(target=read_session, name="get-or-create-race")
        closer = threading.Thread(target=close_manager, name="close-race")
        try:
            with (
                patch.object(
                    SessionManager,
                    "_hydrate_persisted_state",
                    side_effect=blocking_hydrate,
                ),
                patch.object(SessionManager, "_release", side_effect=blocking_release),
            ):
                getter.start()
                self.assertTrue(hydrate_entered.wait(timeout=2.0))

                closer.start()
                self.assertTrue(close_started.wait(timeout=2.0))
                self.assertTrue(lifecycle_lock.attempted["closer"].wait(timeout=2.0))

                # On the broken implementation the closer can acquire the
                # lifecycle lock while hydration is blocked. Hold its release
                # there so the invalid ordering is deterministic. A correct
                # implementation keeps the getter's lifecycle lock until its
                # hydration and return are complete.
                if not lifecycle_lock.acquired["getter"].is_set():
                    self.assertTrue(lifecycle_lock.acquired["closer"].wait(timeout=2.0))
                    self.assertTrue(release_entered.wait(timeout=2.0))

                allow_hydrate.set()
                self.assertTrue(get_finished.wait(timeout=2.0))
                self.assertEqual([], errors)
                self.assertIs(session, observed.get("session"))
                self.assertFalse(observed.get("released_at_return", True))

                self.assertTrue(release_entered.wait(timeout=2.0))
                with event_lock:
                    self.assertLess(
                        events.index("get_returned"),
                        events.index("release_entered"),
                    )

                allow_release.set()
                closer.join(timeout=3.0)
                getter.join(timeout=3.0)
                self.assertFalse(getter.is_alive())
                self.assertFalse(closer.is_alive())
                self.assertTrue(close_finished.is_set())
        finally:
            allow_hydrate.set()
            allow_release.set()
            getter.join(timeout=3.0)
            closer.join(timeout=3.0)
            manager.close()
        self.assertFalse(getter.is_alive())
        self.assertFalse(closer.is_alive())

    def test_remove_close_waits_for_inflight_release(self):
        """Manager close must wait while remove releases a session."""
        manager = SessionManager(_NullChatStateStore())
        session = manager.create()
        release_entered = threading.Event()
        allow_release = threading.Event()
        release_finished = threading.Event()
        close_started = threading.Event()
        close_finished = threading.Event()
        errors = []
        events = []
        event_lock = threading.Lock()

        def record(event):
            with event_lock:
                events.append(event)

        def blocking_release(_sid, current_session, *, wait=False):
            del wait
            record("release_entered")
            release_entered.set()
            if not allow_release.wait(timeout=3.0):
                raise AssertionError("release probe was not released")
            current_session._test_released = True
            release_finished.set()
            record("release_finished")

        def remove_session():
            try:
                manager.remove(session.session_id)
            except BaseException as exc:  # pragma: no cover - assertion below
                errors.append(exc)

        def close_manager():
            close_started.set()
            try:
                manager.close()
            except BaseException as exc:  # pragma: no cover - assertion below
                errors.append(exc)
            finally:
                close_finished.set()
                record("close_finished")

        remover = threading.Thread(target=remove_session, name="remove-race")
        closer = threading.Thread(target=close_manager, name="close-remove-race")
        try:
            with patch.object(SessionManager, "_release", side_effect=blocking_release):
                remover.start()
                self.assertTrue(release_entered.wait(timeout=2.0))

                closer.start()
                self.assertTrue(close_started.wait(timeout=2.0))
                self.assertFalse(
                    close_finished.wait(timeout=1.0),
                    "close returned while remove was still releasing the session",
                )

                allow_release.set()
                remover.join(timeout=3.0)
                closer.join(timeout=3.0)
                self.assertFalse(remover.is_alive())
                self.assertFalse(closer.is_alive())
                self.assertEqual([], errors)
                self.assertTrue(release_finished.is_set())
                self.assertTrue(close_finished.is_set())
                with event_lock:
                    self.assertLess(
                        events.index("release_finished"),
                        events.index("close_finished"),
                    )
        finally:
            allow_release.set()
            remover.join(timeout=3.0)
            closer.join(timeout=3.0)
            manager.close()
        self.assertFalse(remover.is_alive())
        self.assertFalse(closer.is_alive())

    def test_job_runner_is_unavailable_during_shutdown(self):
        """A closing session must not hand a stale runner to new callers."""
        session = ChatSession(session_id=f"runner-shutdown-{uuid.uuid4().hex[:10]}")
        shutdown_entered = threading.Event()
        allow_shutdown = threading.Event()
        shutdown_errors = []

        class BlockingRunner:
            _pool = Mock(_threads=set())
            _detached_pool = Mock(_threads=set())
            _lock = threading.RLock()
            _lease_stops = {}

            def shutdown(self, *, wait=True):
                self.wait = wait
                shutdown_entered.set()
                if not allow_shutdown.wait(timeout=2.0):
                    raise AssertionError("runner shutdown was not released")

        runner = BlockingRunner()
        session._job_runner = runner

        def shutdown_runner():
            try:
                session.shutdown_job_runner(wait=True)
            except BaseException as exc:  # pragma: no cover - assertion below
                shutdown_errors.append(exc)

        thread = threading.Thread(target=shutdown_runner, name="runner-shutdown")
        try:
            thread.start()
            self.assertTrue(shutdown_entered.wait(timeout=2.0))
            with self.assertRaisesRegex(RuntimeError, "JobRunner is shut down"):
                _ = session.job_runner
            allow_shutdown.set()
            thread.join(timeout=3.0)
            self.assertFalse(thread.is_alive())
            self.assertEqual([], shutdown_errors)
            self.assertIsNone(session._job_runner)
            self.assertTrue(runner.wait)
        finally:
            allow_shutdown.set()
            thread.join(timeout=3.0)

    def test_remove_waits_for_runner_before_closing_data_source(self):
        """Session removal closes data sources only after runner quiescence."""
        manager = SessionManager(_NullChatStateStore())
        session = manager.create()
        source_closed = threading.Event()
        source = Mock()
        source.name = "blocking-source"
        source.close = source_closed.set
        session.add_source(source)

        shutdown_entered = threading.Event()
        allow_shutdown = threading.Event()

        class BlockingRunner:
            _pool = Mock(_threads=set())
            _detached_pool = Mock(_threads=set())
            _lock = threading.RLock()
            _lease_stops = {}

            def shutdown(self, *, wait=True):
                self.wait = wait
                shutdown_entered.set()
                if not allow_shutdown.wait(timeout=2.0):
                    raise AssertionError("runner shutdown was not released")

        runner = BlockingRunner()
        session._job_runner = runner
        remover = threading.Thread(
            target=manager.remove,
            args=(session.session_id,),
            name="remove-waits-for-runner",
        )
        try:
            remover.start()
            self.assertTrue(shutdown_entered.wait(timeout=2.0))
            self.assertFalse(
                source_closed.wait(timeout=0.15),
                "data source closed before the session runner quiesced",
            )
            allow_shutdown.set()
            remover.join(timeout=3.0)
            self.assertFalse(remover.is_alive())
            self.assertTrue(source_closed.is_set())
            self.assertTrue(runner.wait)
        finally:
            allow_shutdown.set()
            remover.join(timeout=3.0)
            manager.close()


if __name__ == "__main__":
    unittest.main()
