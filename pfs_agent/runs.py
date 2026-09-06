"""Control planes for synchronous PFS report runs.

The deterministic report endpoints are synchronous, so cancellation is
cooperative: request handlers check this registry between bounded analysis
phases and before they persist governance artifacts.  The in-memory registry
keeps the test/default contract small; an explicit SQLite registry shares
cancel requests across local Python processes without pretending to provide a
distributed coordinator.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import socket
import sqlite3
import threading
import time
import uuid

try:  # POSIX desktop/server runtimes
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None

try:  # Windows desktop fallback
    import msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX
    msvcrt = None


class AnalysisRunError(RuntimeError):
    """Base error carrying a stable HTTP-facing code."""

    code = "pfs_analysis_run_error"


class AnalysisRunAlreadyActive(AnalysisRunError):
    code = "pfs_analysis_run_active"


class AnalysisRunCanceled(AnalysisRunError):
    code = "pfs_analysis_canceled"


@dataclass
class _RunState:
    cancel_requested: bool = False
    accepting_cancel: bool = True


class AnalysisRunRegistry:
    """Thread-safe, session-scoped registry for active report runs."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[tuple[str, str], _RunState] = {}

    @staticmethod
    def _key(session_id: str, run_id: str) -> tuple[str, str]:
        return str(session_id), str(run_id)

    def begin(self, session_id: str, run_id: str) -> None:
        key = self._key(session_id, run_id)
        with self._lock:
            if key in self._runs:
                raise AnalysisRunAlreadyActive(f"analysis run is already active: {run_id}")
            self._runs[key] = _RunState()

    def request_cancel(self, session_id: str, run_id: str) -> bool:
        key = self._key(session_id, run_id)
        with self._lock:
            state = self._runs.get(key)
            if state is None or not state.accepting_cancel:
                return False
            state.cancel_requested = True
            return True

    def checkpoint(self, session_id: str, run_id: str) -> None:
        key = self._key(session_id, run_id)
        with self._lock:
            state = self._runs.get(key)
            if state is not None and state.cancel_requested:
                raise AnalysisRunCanceled(f"analysis run canceled: {run_id}")

    def begin_commit(self, session_id: str, run_id: str) -> None:
        """Atomically close cancellation before governance side effects start."""
        key = self._key(session_id, run_id)
        with self._lock:
            state = self._runs.get(key)
            if state is None:
                return
            if state.cancel_requested:
                raise AnalysisRunCanceled(f"analysis run canceled: {run_id}")
            state.accepting_cancel = False

    def finish(self, session_id: str, run_id: str) -> None:
        with self._lock:
            self._runs.pop(self._key(session_id, run_id), None)

    def is_active(self, session_id: str, run_id: str) -> bool:
        with self._lock:
            return self._key(session_id, run_id) in self._runs


class PersistentAnalysisRunRegistry(AnalysisRunRegistry):
    """SQLite-backed run registry for opt-in local cross-process cancel.

    A worker updates its heartbeat at every checkpoint.  If a later process
    tries to reuse a run id, an owner that is still alive keeps the duplicate
    rejection; a dead owner on the same host is reclaimed so a crashed worker
    cannot strand a run forever.  Remote hosts are fail-closed because this
    adapter cannot prove their liveness.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS analysis_runs (
        session_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        accepting_cancel INTEGER NOT NULL DEFAULT 1,
        owner_pid INTEGER NOT NULL,
        owner_host TEXT NOT NULL,
        owner_token TEXT NOT NULL,
        started_at REAL NOT NULL,
        heartbeat_at REAL NOT NULL,
        PRIMARY KEY (session_id, run_id)
    );
    CREATE INDEX IF NOT EXISTS idx_analysis_runs_heartbeat
        ON analysis_runs (heartbeat_at);
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        super().__init__()
        self.path = Path(path).resolve()
        if self.path.exists() and self.path.is_dir():
            raise AnalysisRunError(f"analysis run registry path is a directory: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._owner_pid = os.getpid()
        self._owner_host = socket.gethostname()
        self._owner_token = uuid.uuid4().hex
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                str(self.path),
                timeout=30,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            return connection
        except sqlite3.Error as exc:
            raise AnalysisRunError(f"cannot open analysis run registry: {self.path}") from exc

    @contextmanager
    def _process_lock(self):
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        with lock_path.open("a+b") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - Windows path
                handle.seek(0)
                handle.write(b"\0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - platform without an OS lock API
                yield
                return
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:  # pragma: no cover - Windows path
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    def _initialize(self) -> None:
        with self._process_lock():
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(self._SCHEMA)
            except sqlite3.Error as exc:
                raise AnalysisRunError(f"cannot initialize analysis run registry: {self.path}") from exc
            finally:
                connection.close()

    @contextmanager
    def _transaction(self):
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                connection.close()

    @staticmethod
    def _owner_alive(row: sqlite3.Row | tuple[object, ...], current_host: str) -> bool:
        owner_host = str(row["owner_host"] if isinstance(row, sqlite3.Row) else row[5])
        if owner_host != current_host:
            return True
        pid_value = row["owner_pid"] if isinstance(row, sqlite3.Row) else row[4]
        try:
            pid = int(pid_value)
        except (TypeError, ValueError):
            return True

        if os.name == "nt":
            # ``os.kill(pid, 0)`` is not a liveness probe on Windows: it can
            # report success for an exited process.  Ask Kernel32 for the
            # actual exit code instead.  Access-denied remains conservative so
            # a permission boundary cannot cause two owners to run together.
            try:
                import ctypes

                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                STILL_ACTIVE = 259
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
                kernel32.OpenProcess.restype = ctypes.c_void_p
                kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
                kernel32.GetExitCodeProcess.restype = ctypes.c_int
                kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel32.CloseHandle.restype = ctypes.c_int
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
                if not handle:
                    error = ctypes.get_last_error()
                    return error not in {6, 87, 1168}
                try:
                    exit_code = ctypes.c_uint32()
                    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                        return True
                    return exit_code.value == STILL_ACTIVE
                finally:
                    kernel32.CloseHandle(handle)
            except Exception:
                # Keep the existing fail-closed behavior if the native probe
                # itself is unavailable on a particular Windows runtime.
                return True

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except (OSError, ValueError):
            return True
        return True

    def begin(self, session_id: str, run_id: str) -> None:
        session, run = self._key(session_id, run_id)
        now = time.time()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_runs WHERE session_id = ? AND run_id = ?",
                (session, run),
            ).fetchone()
            if row is not None:
                if self._owner_alive(row, self._owner_host):
                    raise AnalysisRunAlreadyActive(f"analysis run is already active: {run_id}")
                connection.execute(
                    "DELETE FROM analysis_runs WHERE session_id = ? AND run_id = ?",
                    (session, run),
                )
            connection.execute(
                """
                INSERT INTO analysis_runs (
                    session_id, run_id, owner_pid, owner_host, owner_token,
                    started_at, heartbeat_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session, run, self._owner_pid, self._owner_host, self._owner_token, now, now),
            )

    def request_cancel(self, session_id: str, run_id: str) -> bool:
        session, run = self._key(session_id, run_id)
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT accepting_cancel
                FROM analysis_runs
                WHERE session_id = ? AND run_id = ?
                """,
                (session, run),
            ).fetchone()
            if row is None or not bool(row["accepting_cancel"]):
                return False
            connection.execute(
                """
                UPDATE analysis_runs
                SET cancel_requested = 1, heartbeat_at = ?
                WHERE session_id = ? AND run_id = ? AND accepting_cancel = 1
                """,
                (time.time(), session, run),
            )
            return connection.execute("SELECT changes()").fetchone()[0] == 1

    def checkpoint(self, session_id: str, run_id: str) -> None:
        session, run = self._key(session_id, run_id)
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT cancel_requested
                FROM analysis_runs
                WHERE session_id = ? AND run_id = ?
                """,
                (session, run),
            ).fetchone()
            if row is None:
                return
            if bool(row["cancel_requested"]):
                raise AnalysisRunCanceled(f"analysis run canceled: {run_id}")
            connection.execute(
                """
                UPDATE analysis_runs SET heartbeat_at = ?
                WHERE session_id = ? AND run_id = ?
                """,
                (time.time(), session, run),
            )

    def begin_commit(self, session_id: str, run_id: str) -> None:
        session, run = self._key(session_id, run_id)
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT cancel_requested
                FROM analysis_runs
                WHERE session_id = ? AND run_id = ?
                """,
                (session, run),
            ).fetchone()
            if row is None:
                return
            if bool(row["cancel_requested"]):
                raise AnalysisRunCanceled(f"analysis run canceled: {run_id}")
            connection.execute(
                """
                UPDATE analysis_runs
                SET accepting_cancel = 0, heartbeat_at = ?
                WHERE session_id = ? AND run_id = ?
                """,
                (time.time(), session, run),
            )

    def finish(self, session_id: str, run_id: str) -> None:
        session, run = self._key(session_id, run_id)
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM analysis_runs WHERE session_id = ? AND run_id = ?",
                (session, run),
            )

    def is_active(self, session_id: str, run_id: str) -> bool:
        session, run = self._key(session_id, run_id)
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT 1 FROM analysis_runs WHERE session_id = ? AND run_id = ?",
                    (session, run),
                ).fetchone()
                return row is not None
            finally:
                connection.close()


def _build_analysis_run_registry() -> AnalysisRunRegistry:
    backend = str(os.getenv("PFS_RUN_REGISTRY_BACKEND") or "memory").strip().lower()
    if backend == "memory":
        return AnalysisRunRegistry()
    if backend == "sqlite":
        from infrastructure.paths import data_path

        return PersistentAnalysisRunRegistry(data_path("outputs", "pfs", "analysis-runs.sqlite3"))
    raise AnalysisRunError("PFS_RUN_REGISTRY_BACKEND must be memory or sqlite")


analysis_run_registry = _build_analysis_run_registry()
