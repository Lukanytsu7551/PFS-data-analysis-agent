"""In-process control plane for synchronous PFS report runs.

The deterministic report endpoints are synchronous, so cancellation is
cooperative: request handlers check this registry between bounded analysis
phases and before they persist governance artifacts.  The registry is scoped to
one Python process; distributed cancellation remains a separate capability.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading


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


analysis_run_registry = AnalysisRunRegistry()
