#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""In-memory session management for the PFS data-analysis agent."""

import atexit
import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass, field, fields, MISSING
from datetime import datetime
from typing import List, Dict, Any, Optional, TYPE_CHECKING

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agent.jobs import JobRunner
    from data.jobs_store import JobsStore


def _unique_objects(objects) -> tuple:
    seen: set[int] = set()
    result = []
    for obj in objects:
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        result.append(obj)
    return tuple(result)


class DataSourceSnapshot:
    """Immutable active-source view leased for one conversation parent task."""

    def __init__(
        self,
        session: "ChatSession",
        entries: List[Dict[str, Any]],
        combined_schema: str,
        merged_source=None,
    ) -> None:
        self._session = session
        self.entries = tuple({"id": item["id"], "source": item["source"]} for item in entries)
        self.sources = tuple(item["source"] for item in self.entries)
        self.primary = self.sources[0] if self.sources else None
        self.combined_schema = combined_schema
        self.merged_source = merged_source
        self._leased_objects = _unique_objects(
            [
                *self.sources,
                *([merged_source] if merged_source is not None else []),
            ]
        )
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._session._release_data_source_objects(self._leased_objects)


@dataclass
class ChatSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    owner_user_id: str = ""  # Cloud-mode user isolation
    workspace_id: str = ""
    history: List[Dict[str, str]] = field(default_factory=list)
    # ── Multi-source support ───────────────────────────────────────────────────
    # Each entry: {"id": str, "source": DataSource}
    # Multiple sources can be active simultaneously; `_active_ids` is a set.
    _sources: List[Dict[str, Any]] = field(default_factory=list)
    _active_ids: List[str] = field(default_factory=list)  # ordered, all active
    model_provider: str = ""  # Selected LLM provider key
    # Token usage tracking
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    last_prompt_tokens: int = 0  # most recent call's prompt size (for context bar)
    total_cached_input_tokens: int = 0
    total_cache_write_tokens: int = 0
    usage_breakdowns: List[Dict[str, Any]] = field(default_factory=list)
    # Bounded, content-free Slash Command execution telemetry.
    command_metrics: List[Dict[str, Any]] = field(default_factory=list)
    # Mutable, session-owned auto-compaction circuit state. BusinessAgent keeps
    # a reference to this dict so failures survive across user turns.
    compaction_state: Dict[str, Any] = field(
        default_factory=lambda: {
            "consecutive_failures": 0,
            "last_failure_type": "",
            "circuit_open": False,
            "last_attempt_at": 0.0,
            "last_success_at": 0.0,
        }
    )
    # Cancellation flag — set by POST /api/session/<sid>/stop
    cancel_requested: bool = False
    # IDs of every chart generated in this session (appended by api/chat.py)
    chart_ids: List[str] = field(default_factory=list)
    # PPT color scheme — persisted so it survives multiple PPT requests
    ppt_color_scheme: str = "mckinsey"
    # ── Temporary per-session prompt ───────────────────────────────────────────
    # A free-form instruction the user sets for THIS conversation only. When
    # enabled, it is appended to the system prompt on every turn (see agent.run).
    # `temp_prompt` holds the processed text that will be injected;
    # `temp_prompt_enabled` is the on/off switch.
    temp_prompt: str = ""
    temp_prompt_enabled: bool = False
    # TTL tracking — updated on every access
    last_accessed: datetime = field(default_factory=datetime.now)
    # Set when a saved session older than 24h is restored (stale-data hint).
    restored_saved_at: str = ""
    # Revision of the last cross-process JSON state snapshot applied to this
    # in-memory session. Live data-source objects are intentionally excluded.
    _persisted_state_revision: int = field(default=0, repr=False, compare=False)
    # Last turn's reasoning chain summary — injected into the next turn's messages
    last_reasoning: str = ""
    # ── JobRunner (A6) ──────────────────────────────────────────────────────────
    # Lazily initialized via the `job_runner` property. Each session gets its own
    # ThreadPoolExecutor; the underlying JobsStore is a process-wide singleton.
    _job_runner_lock: threading.RLock = field(
        default_factory=threading.RLock,
        repr=False,
        compare=False,
    )
    _job_runner_shutdown: bool = field(default=False, repr=False, compare=False)
    _job_runner: Optional["JobRunner"] = None
    # Cached merged schema string — cleared whenever data sources change so we
    # don't serve a stale schema after upload/connect/disconnect.
    _combined_schema_cache: Optional[str] = None
    # Cached MergedDataSource — rebuilt whenever data sources change.
    # Only populated when ≥2 sources are active.
    _merged_source_cache: Optional[object] = None
    # B6 durable active-context index. Full result bytes live in workspace/global
    # artifact stores; the session only keeps bounded metadata and recent SQL.
    recent_sql: List[str] = field(default_factory=list)
    recent_artifacts: List[Dict[str, Any]] = field(default_factory=list)
    # Destructive derived-table operations keep a bounded, path-free audit
    # record and an idempotency key.  The actual table contents remain owned
    # by the active DataSource; this list only coordinates safe retries.
    analysis_delete_operations: List[Dict[str, Any]] = field(default_factory=list)
    # Typed activation audit, kept outside LLM message history so provider
    # payloads never receive application-only fields.
    turn_activations: List[Dict[str, Any]] = field(default_factory=list)
    # D6: low-frequency tools discovered from user language stay available in
    # this session, while data/workspace guards are still evaluated per turn.
    discovered_tools: List[str] = field(default_factory=list)
    # Phase 2: bounded MCP full-schema discovery cache. Names are ordered from
    # least to most recently discovered/used.
    discovered_mcp_tools: List[str] = field(default_factory=list)
    mcp_tool_last_used: Dict[str, float] = field(default_factory=dict)
    mcp_catalog_version: str = ""
    # C4.0 source lifecycle. Removed/switched sources remain usable by the
    # conversation snapshots that leased them, then close at the last release.
    _source_lock: threading.RLock = field(
        default_factory=threading.RLock,
        repr=False,
        compare=False,
    )
    _source_lease_counts: Dict[int, int] = field(default_factory=dict, repr=False)
    _retired_sources: Dict[int, Any] = field(default_factory=dict, repr=False)
    _usage_lock: threading.RLock = field(
        default_factory=threading.RLock,
        repr=False,
        compare=False,
    )
    # Skill name auto-loaded by the LLM via load_analysis_skill tool.
    # Persisted across turns so guard/nudge logic works in subsequent turns
    # (e.g. after ask_user user-reply starts a new turn).
    auto_loaded_skill: str = ""
    # Optional conversation-scoped Feishu bridge.  These fields deliberately
    # contain only a group identifier and display label; application credentials
    # remain in the OS credential store managed by feishu_bot_store.
    feishu_bot_enabled: bool = False
    feishu_chat_id: str = ""
    feishu_chat_name: str = ""
    # Browser clients keep their own SSE stream for Web-originated turns.  A
    # small, bounded event feed bridges completed turns that originated from a
    # Feishu mobile/desktop client back into the currently open Web chat.
    feishu_inbound_revision: int = 0
    feishu_inbound_events: List[Dict[str, Any]] = field(default_factory=list)
    _feishu_turn_lock: threading.RLock = field(
        default_factory=threading.RLock,
        repr=False,
        compare=False,
    )
    _feishu_event_lock: threading.RLock = field(
        default_factory=threading.RLock,
        repr=False,
        compare=False,
    )

    # ── Multi-source API ───────────────────────────────────────────────────────

    @property
    def data_source(self):
        """Backward-compat: return first active DataSource (or None)."""
        active = self._active_entries()
        return active[0]["source"] if active else None

    @data_source.setter
    def data_source(self, source):
        """Backward-compat setter: replaces entire list with one source (old code)."""
        with self._source_lock:
            previous = [entry.get("source") for entry in self._sources]
            if source is None:
                self._sources = []
                self._active_ids = []
            else:
                sid = str(uuid.uuid4())[:8]
                self._sources = [{"id": sid, "source": source}]
                self._active_ids = [sid]
            self._combined_schema_cache = None
            self._invalidate_merged_source()
            for old_source in previous:
                if old_source is not None and old_source is not source:
                    self._retire_data_source(old_source)

    # ── JobRunner (A6) ──────────────────────────────────────────────────────────

    @property
    def job_runner(self):
        """Lazily create a per-session JobRunner backed by the global JobsStore."""
        with self._job_runner_lock:
            # Shutdown is one-way.  Keep the old runner published while it is
            # unwinding so an already-running worker can finish through its
            # captured reference, but never hand that runner to a new caller.
            if self._job_runner_shutdown:
                raise RuntimeError("ChatSession JobRunner is shut down")
            if self._job_runner is None:
                from agent.jobs import JobRunner

                self._job_runner = JobRunner(self.session_id, get_global_jobs_store())
            return self._job_runner

    @staticmethod
    def _is_runner_worker_thread(runner) -> bool:
        """Return whether the caller is one of *runner*'s executor workers."""
        current = threading.current_thread()
        for pool_name in ("_pool", "_detached_pool"):
            pool = getattr(runner, pool_name, None)
            threads = getattr(pool, "_threads", ())
            try:
                if current in threads:
                    return True
            except TypeError:
                # Test doubles and alternate executor implementations may not
                # expose an iterable thread set.  They are not self-joinable
                # through this compatibility path.
                continue
        return False

    @staticmethod
    def _runner_heartbeat_names(runner) -> set[str]:
        """Snapshot names of JobRunner heartbeat threads before shutdown."""
        lock = getattr(runner, "_lock", None)
        lease_stops = getattr(runner, "_lease_stops", {})
        try:
            if lock is not None:
                with lock:
                    job_ids = tuple(lease_stops)
            else:
                job_ids = tuple(lease_stops)
        except (TypeError, AttributeError):
            return set()
        return {f"lease-{str(job_id)[:8]}" for job_id in job_ids}

    @staticmethod
    def _join_runner_heartbeats(names: set[str]) -> None:
        """Join heartbeat daemons so they cannot touch a closed JobsStore."""
        if not names:
            return
        current = threading.current_thread()
        for thread in threading.enumerate():
            if thread is current or thread.name not in names:
                continue
            thread.join()

    def shutdown_job_runner(self, wait: bool = False) -> bool:
        """Release the runner pool and report whether it is fully quiescent.

        A runner worker cannot wait for its own executor.  In that narrow
        case, signal shutdown and let the owning session release path finish
        from a separate thread after the worker unwinds.
        """
        with self._job_runner_lock:
            runner = self._job_runner
            self._job_runner_shutdown = True
            if runner is None:
                return True
            heartbeat_names = self._runner_heartbeat_names(runner)

        if self._is_runner_worker_thread(runner):
            try:
                # ``wait=True`` would make ThreadPoolExecutor try to join the
                # current worker.  The deferred release watcher will perform
                # the real wait after this worker returns.
                runner.shutdown(wait=False)
            except Exception:
                log.exception("[session] job_runner shutdown error")
            return False

        if runner is not None:
            try:
                runner.shutdown(wait=wait)
                if wait:
                    self._join_runner_heartbeats(heartbeat_names)
            except Exception:
                log.exception("[session] job_runner shutdown error")
            finally:
                with self._job_runner_lock:
                    if self._job_runner is runner:
                        self._job_runner = None
        self._invalidate_merged_source()
        return True

    def close_sources(self) -> None:
        """Close session-owned connections before releasing its workspace lease."""
        with self._source_lock:
            sources = [entry.get("source") for entry in self._sources]
            self._sources = []
            self._active_ids = []
            self._combined_schema_cache = None
            self._invalidate_merged_source()
            for source in sources:
                if source is not None:
                    self._retire_data_source(source)

    def _active_entries(self) -> List[Dict[str, Any]]:
        """Ordered list of active source entries."""
        with self._source_lock:
            id_set = set(self._active_ids)
            return [e for e in self._sources if e["id"] in id_set]

    def add_source(self, source) -> str:
        """Append a new data source and activate it. Returns its internal ID."""
        sid = str(uuid.uuid4())[:8]
        with self._source_lock:
            self._sources.append({"id": sid, "source": source})
            if sid not in self._active_ids:
                self._active_ids.append(sid)
            self._combined_schema_cache = None
            self._invalidate_merged_source()
        log.info(
            "[session] source added  session=%s  source=%s  id=%s  total=%d",
            self.session_id,
            getattr(source, "name", "?"),
            sid,
            len(self._sources),
        )
        return sid

    def remove_source(self, source_id: str) -> bool:
        with self._source_lock:
            before = len(self._sources)
            removed = next((e for e in self._sources if e["id"] == source_id), None)
            self._sources = [e for e in self._sources if e["id"] != source_id]
            self._active_ids = [i for i in self._active_ids if i != source_id]
            removed_ok = len(self._sources) < before
            if removed_ok:
                name = getattr(removed["source"], "name", "?") if removed else "?"
                self._combined_schema_cache = None
                self._invalidate_merged_source()
                self._retire_data_source(removed["source"])
                log.info(
                    "[session] source removed  session=%s  source=%s  id=%s  remaining=%d",
                    self.session_id,
                    name,
                    source_id,
                    len(self._sources),
                )
        return removed_ok

    def toggle_source(self, source_id: str) -> bool:
        """Toggle a source's active state. Returns new active state."""
        entry = next((e for e in self._sources if e["id"] == source_id), None)
        if not entry:
            return False
        if source_id in self._active_ids:
            self._active_ids = [i for i in self._active_ids if i != source_id]
            new_state = False
        else:
            self._active_ids.append(source_id)
            new_state = True
        self._combined_schema_cache = None
        self._invalidate_merged_source()
        log.info(
            "[session] source toggled  session=%s  source=%s  id=%s  active=%s",
            self.session_id,
            getattr(entry["source"], "name", "?"),
            source_id,
            new_state,
        )
        return new_state

    def list_sources(self) -> List[Dict[str, Any]]:
        """Return [{id, name, type, active}] for the frontend."""
        active_set = set(self._active_ids)
        return [
            {
                "id": e["id"],
                "name": getattr(e["source"], "name", "未命名"),
                "type": type(e["source"]).__name__.replace("DataSource", "").lower(),
                "active": e["id"] in active_set,
            }
            for e in self._sources
        ]

    def get_combined_schema(self) -> str:
        """Merged schema from all ACTIVE sources.

        When multiple sources are active and any two share the same table name,
        each table is prefixed with ``src{N}__`` (1-based) so that the LLM can
        unambiguously reference it.  Single-source behaviour is unchanged.

        The prefix is understood by ``BusinessAgent._route_query`` and
        ``_tool_query_data`` / ``_tool_create_analysis_table``, which strip it
        before passing the SQL to the individual DataSource.
        """
        active = self._active_entries()
        if not active:
            # Fallback: use all sources if none activated
            active = self._sources
        if not active:
            return ""
        if len(active) == 1:
            return active[0]["source"].get_schema()

        # Collect all table names across sources to detect collisions.
        all_table_names: list[str] = []
        src_tables: list[list[str]] = []
        for entry in active:
            try:
                tables = entry["source"].list_tables()
            except Exception:
                tables = []
            src_tables.append(tables)
            all_table_names.extend(tables)

        collision = len(all_table_names) != len(set(all_table_names))

        parts = []
        for idx, entry in enumerate(active, start=1):
            src = entry["source"]
            raw_schema = src.get_schema()
            if collision:
                # Prefix every "Table: <name>" line with src{N}__ so the LLM
                # (and the router) can tell tables apart across sources.
                import re as _re

                def _add_prefix(m):
                    return f"Table: src{idx}__{m.group(1)}"

                raw_schema = _re.sub(r"Table:\s+(\S+)", _add_prefix, raw_schema)
                note = (
                    f"  [NOTE: prefix all table names with src{idx}__ when writing SQL, "
                    f'e.g. SELECT * FROM "src{idx}__<table_name>"]'
                )
            else:
                note = ""
            header = f"=== 数据源 {idx}: {getattr(src, 'name', '未命名')} ==="
            parts.append(f"{header}{note}\n{raw_schema}")
        return "\n\n".join(parts)

    # ── Merged source (cross-source JOIN support) ──────────────────────────────

    def _invalidate_merged_source(self) -> None:
        """Close and drop the cached MergedDataSource.

        Must be called whenever the active source list changes so the merged
        connection is rebuilt with up-to-date data on next access.
        """
        with self._source_lock:
            ms = getattr(self, "_merged_source_cache", None)
            if ms is not None:
                self._merged_source_cache = None
                self._retire_data_source(ms)

    @staticmethod
    def _close_data_source_object(source) -> None:
        close = getattr(source, "close", None)
        invalidate = getattr(source, "invalidate", None)
        try:
            if callable(close):
                close()
            elif callable(invalidate):
                invalidate()
        except Exception:
            log.exception("[session] data source close error")

    def _retire_data_source(self, source) -> None:
        """Remove ownership now; defer physical close while snapshots use it."""
        key = id(source)
        if self._source_lease_counts.get(key, 0) > 0:
            self._retired_sources[key] = source
        else:
            self._close_data_source_object(source)

    def _release_data_source_objects(self, sources) -> None:
        to_close = []
        with self._source_lock:
            for source in sources:
                key = id(source)
                count = self._source_lease_counts.get(key, 0)
                if count <= 1:
                    self._source_lease_counts.pop(key, None)
                    retired = self._retired_sources.pop(key, None)
                    if retired is not None:
                        to_close.append(retired)
                else:
                    self._source_lease_counts[key] = count - 1
        for source in to_close:
            self._close_data_source_object(source)

    def acquire_data_source_snapshot(self) -> DataSourceSnapshot:
        """Freeze and lease the active source graph for one parent task."""
        with self._source_lock:
            entries = self._active_entries()
            combined_schema = self.get_combined_schema() if entries else ""
            merged_source = self.get_merged_source() if len(entries) >= 2 else None
            leased = _unique_objects(
                [
                    *(entry["source"] for entry in entries),
                    *([merged_source] if merged_source is not None else []),
                ]
            )
            for source in leased:
                key = id(source)
                self._source_lease_counts[key] = self._source_lease_counts.get(key, 0) + 1
            return DataSourceSnapshot(self, entries, combined_schema, merged_source)

    def get_merged_source(self):
        """Return a MergedDataSource covering all active sources.

        The object is created on first call and cached until the source list
        changes.  Returns None when fewer than 2 sources are active (no merge
        needed) or when construction fails.
        """
        active = self._active_entries()
        if len(active) < 2:
            return None

        if self._merged_source_cache is not None:
            return self._merged_source_cache

        try:
            from data.merged_source import MergedDataSource

            src_list = [e["source"] for e in active]
            ms = MergedDataSource(src_list)
            self._merged_source_cache = ms
            log.info(
                "[session] MergedDataSource built  session=%s  sources=%s",
                self.session_id,
                [getattr(s, "name", "?") for s in src_list],
            )
            return ms
        except Exception as exc:
            log.warning("[session] MergedDataSource build failed: %s", exc)
            return None

    def add_user(self, text: str):
        self.history.append({"role": "user", "content": text})

    # Maximum characters kept per tool result in history.
    # Large query results are truncated here so they don't bloat the prompt on
    # subsequent turns.  800 chars ≈ 230 tokens — enough for the Agent to know
    # what was queried and what the key values were.
    _TOOL_RESULT_HISTORY_CAP = 800

    def add_tool_messages(self, messages: list) -> None:
        """Store tool call / tool result messages from one agent turn.

        Tool results are truncated to _TOOL_RESULT_HISTORY_CAP chars so that
        large query outputs don't cause prompt bloat on subsequent turns.
        The Agent can always re-run the same query if it needs the full data.
        """
        _KEEP_ROLES = {"assistant", "tool"}
        for m in messages:
            if m.get("role") not in _KEEP_ROLES:
                continue
            if m.get("role") == "assistant":
                if not m.get("tool_calls"):
                    continue  # intermediate assistant text — skip
                entry = {"role": "assistant", "tool_calls": m["tool_calls"], "content": ""}
            else:
                # role == "tool" — truncate large results
                raw = m.get("content", "")
                cap = self._TOOL_RESULT_HISTORY_CAP
                from agent.tools.results import truncate_tool_result_preserving_refs

                content = truncate_tool_result_preserving_refs(raw, cap)
                entry = {
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": content,
                }
            self.history.append(entry)

    def record_tool_audit(self, event: Dict[str, Any]) -> None:
        """Update the bounded recovery index from a completed tool call."""
        recovery = event.get("recovery") or {}
        sql = str(recovery.get("sql") or "").strip()
        if sql:
            self.recent_sql = [item for item in self.recent_sql if item != sql]
            self.recent_sql.append(sql[:4000])
            self.recent_sql = self.recent_sql[-5:]
        for artifact in event.get("artifacts") or []:
            if not isinstance(artifact, dict):
                continue
            artifact_id = str(artifact.get("artifact_id") or "")
            uri = str(artifact.get("uri") or "")
            key = artifact_id or uri or str(artifact.get("url") or "")
            if not key:
                continue
            self.recent_artifacts = [
                item
                for item in self.recent_artifacts
                if (item.get("artifact_id") or item.get("uri") or item.get("url")) != key
            ]
            self.recent_artifacts.append(
                {
                    k: artifact.get(k)
                    for k in (
                        "type",
                        "artifact_id",
                        "name",
                        "uri",
                        "url",
                        "size_bytes",
                        "sha256",
                        "workspace_id",
                        "session_id",
                    )
                    if artifact.get(k) not in (None, "")
                }
            )
            self.recent_artifacts = self.recent_artifacts[-20:]

    def begin_analysis_delete_operation(
        self,
        *,
        operation_key: str,
        request_sha256: str,
        table_names: List[str],
        source_name: str = "",
    ) -> tuple[str, Dict[str, Any]]:
        """Atomically reserve or reuse one derived-table delete request.

        The result is deliberately small and free of SQL/absolute paths.  A
        repeated key with the same request is replay-safe; a repeated key with
        different tables is a conflict and must never touch the datasource.
        """
        import copy

        key = str(operation_key or "").strip()[:240]
        digest = str(request_sha256 or "").strip()[:64]
        names = [str(item or "").strip()[:160] for item in (table_names or [])]
        names = [item for item in names if item][:32]
        source = str(source_name or "").strip()[:160]
        with self._usage_lock:
            for item in reversed(self.analysis_delete_operations):
                if str(item.get("operation_key") or "") != key:
                    continue
                if str(item.get("request_sha256") or "") != digest:
                    return "conflict", copy.deepcopy(item)
                if str(item.get("status") or "") == "running":
                    return "in_progress", copy.deepcopy(item)
                return "replay", copy.deepcopy(item)
            record = {
                "operation_key": key,
                "request_sha256": digest,
                "table_names": names,
                "source_name": source,
                "status": "running",
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            self.analysis_delete_operations.append(record)
            self.analysis_delete_operations = self.analysis_delete_operations[-100:]
            return "new", copy.deepcopy(record)

    def finish_analysis_delete_operation(
        self,
        operation_key: str,
        *,
        status: str,
        result: str = "",
        deleted: List[str] | None = None,
        skipped: List[Dict[str, str]] | None = None,
        error: str = "",
    ) -> Dict[str, Any]:
        """Finalize a previously reserved delete operation."""
        import copy

        key = str(operation_key or "").strip()[:240]
        safe_status = str(status or "failed").strip()[:40]
        safe_deleted = [str(item or "").strip()[:160] for item in (deleted or [])]
        safe_deleted = [item for item in safe_deleted if item][:32]
        safe_skipped = []
        for item in (skipped or [])[:32]:
            if not isinstance(item, dict):
                continue
            safe_skipped.append(
                {
                    "table_name": str(item.get("table_name") or "").strip()[:160],
                    "reason_code": str(item.get("reason_code") or "unknown").strip()[:80],
                }
            )
        with self._usage_lock:
            for item in reversed(self.analysis_delete_operations):
                if str(item.get("operation_key") or "") != key:
                    continue
                item.update(
                    {
                        "status": safe_status,
                        "deleted": safe_deleted,
                        "skipped": safe_skipped,
                        "result": str(result or "")[:8_000],
                        "error": str(error or "")[:500],
                        "finished_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                return copy.deepcopy(item)
        return {}

    def record_activation(self, activation, message: str, job_id: str = "") -> None:
        from datetime import datetime

        record = activation.to_record()
        record.update(
            {
                "message": (message or "")[:500],
                "job_id": job_id,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self.turn_activations.append(record)
        self.turn_activations = self.turn_activations[-100:]

    def record_discovered_tools(self, message: str) -> List[str]:
        from agent.tools.exposure import discover_tool_names_for_query

        discovered = sorted(discover_tool_names_for_query(message))
        if not discovered:
            return []
        existing = list(self.discovered_tools or [])
        seen = set(existing)
        added = []
        for tool in discovered:
            if tool not in seen:
                existing.append(tool)
                seen.add(tool)
                added.append(tool)
        self.discovered_tools = existing[-100:]
        return added

    def record_discovered_mcp_tools(
        self,
        names,
        catalog_version: str,
        *,
        used: bool = False,
    ) -> List[str]:
        """Synchronize MCP catalog identity and update the 10-name LRU."""
        version = str(catalog_version or "")
        with self._usage_lock:
            if version != self.mcp_catalog_version:
                self.discovered_mcp_tools = []
                self.mcp_tool_last_used = {}
                self.mcp_catalog_version = version
            existing = list(self.discovered_mcp_tools or [])
            added: List[str] = []
            now = time.time()
            for raw_name in names or ():
                name = str(raw_name or "")
                if not name.startswith("mcp__"):
                    continue
                if name in existing:
                    existing.remove(name)
                else:
                    added.append(name)
                existing.append(name)
                if used:
                    self.mcp_tool_last_used[name] = now
            self.discovered_mcp_tools = existing[-10:]
            retained = set(self.discovered_mcp_tools)
            self.mcp_tool_last_used = {
                name: value for name, value in self.mcp_tool_last_used.items() if name in retained
            }
            return added

    def build_recovery_context(self, workspace_status: Optional[Dict[str, Any]] = None) -> str:
        """Build a small system context that survives compaction and reload."""
        lines: List[str] = []
        if workspace_status and workspace_status.get("mounted"):
            lines.append(
                "Workspace: mounted; permission="
                f"{workspace_status.get('permission', 'read_only')}; "
                f"workdir={workspace_status.get('workdir', '')}"
            )
        active = [item for item in self.list_sources() if item.get("active")]
        if active:
            lines.append(
                "Active data sources: "
                + ", ".join(f"{item.get('name')} ({item.get('type')})" for item in active)
            )
        if self.recent_sql:
            lines.append(
                "Recent SQL (newest last):\n" + "\n".join(f"- {sql}" for sql in self.recent_sql[-3:])
            )
        if self.recent_artifacts:
            lines.append(
                "Recent recoverable artifacts:\n"
                + "\n".join(
                    f"- {item.get('name', item.get('artifact_id', 'artifact'))}: "
                    f"{item.get('uri') or item.get('url', '')}"
                    for item in self.recent_artifacts[-8:]
                )
            )
        if self.restored_saved_at:
            lines.append(
                f"Restored from a session saved at {self.restored_saved_at} (more than "
                "24h ago). Data sources, files, and previews may have changed since; "
                "re-verify source status before relying on earlier previews or results."
            )
        return "\n".join(lines)[:6000]

    def add_assistant(self, text: str, reasoning: str = "", chart_ids: list = None):
        from agent.reasoning import split_reasoning_tags

        text, embedded_reasoning = split_reasoning_tags(text or "")
        reasoning = "\n\n".join(part for part in ((reasoning or "").strip(), embedded_reasoning) if part)
        msg = {"role": "assistant", "content": text}
        if reasoning:
            msg["reasoning"] = reasoning
        # Record the charts produced in this turn so they can be restored when
        # the conversation is reloaded from disk.
        if chart_ids:
            msg["chart_ids"] = list(chart_ids)
        self.history.append(msg)
        self.last_reasoning = reasoning

    def record_feishu_inbound_event(self, role: str, content: str) -> None:
        """Expose a completed Feishu-originated message to the linked Web UI.

        This is intentionally separate from ``history``: regular Web turns are
        already painted by their request SSE stream and must not be duplicated
        by the browser poller.  Event content is bounded and remains only for
        the lifetime of the active session, matching the existing bridge.
        """
        if role not in {"user", "assistant"}:
            return
        text = str(content or "").strip()
        if not text:
            return
        with self._feishu_event_lock:
            self.feishu_inbound_revision += 1
            self.feishu_inbound_events.append(
                {
                    "revision": self.feishu_inbound_revision,
                    "role": role,
                    "content": text[:24_000],
                }
            )
            self.feishu_inbound_events = self.feishu_inbound_events[-120:]

    def feishu_inbound_events_after(self, revision: int) -> tuple[int, List[Dict[str, Any]]]:
        """Return a safe incremental snapshot for the linked browser client."""
        cursor = max(0, int(revision or 0))
        with self._feishu_event_lock:
            return self.feishu_inbound_revision, [
                dict(event)
                for event in self.feishu_inbound_events
                if int(event.get("revision") or 0) > cursor
            ]

    def clear_history(self):
        self.history.clear()
        with self._feishu_event_lock:
            self.feishu_inbound_revision = 0
            self.feishu_inbound_events.clear()
        self.last_reasoning = ""
        self.restored_saved_at = ""
        self.chart_ids.clear()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = 0.0
        self.last_prompt_tokens = 0
        self.total_cached_input_tokens = 0
        self.total_cache_write_tokens = 0
        self.usage_breakdowns.clear()
        self.command_metrics.clear()
        self.compaction_state = {
            "consecutive_failures": 0,
            "last_failure_type": "",
            "circuit_open": False,
            "last_attempt_at": 0.0,
            "last_success_at": 0.0,
        }
        self.recent_sql.clear()
        self.recent_artifacts.clear()
        self.turn_activations.clear()
        self.discovered_tools.clear()
        self.discovered_mcp_tools.clear()
        self.mcp_tool_last_used.clear()
        self.mcp_catalog_version = ""

    def capture_rewind_state(self) -> Dict[str, Any]:
        """Return the conversation-owned state restored by file history."""
        import copy

        return copy.deepcopy(
            {
                "history": self.history,
                "last_reasoning": self.last_reasoning,
                "chart_ids": self.chart_ids,
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_cost_usd": self.total_cost_usd,
                "last_prompt_tokens": self.last_prompt_tokens,
                "total_cached_input_tokens": self.total_cached_input_tokens,
                "total_cache_write_tokens": self.total_cache_write_tokens,
                "usage_breakdowns": self.usage_breakdowns,
                "command_metrics": self.command_metrics,
                "compaction_state": self.compaction_state,
                "recent_sql": self.recent_sql,
                "recent_artifacts": self.recent_artifacts,
                "analysis_delete_operations": self.analysis_delete_operations,
                "turn_activations": self.turn_activations,
                "discovered_tools": self.discovered_tools,
                "discovered_mcp_tools": self.discovered_mcp_tools,
                "mcp_tool_last_used": self.mcp_tool_last_used,
                "mcp_catalog_version": self.mcp_catalog_version,
            }
        )

    def restore_rewind_state(self, state: Dict[str, Any]) -> None:
        """Restore conversation state without changing models or data sources."""
        import copy

        if "history" in state:
            self.history = copy.deepcopy(list(state.get("history") or []))
        if "last_reasoning" in state:
            self.last_reasoning = str(state.get("last_reasoning") or "")
        if "chart_ids" in state:
            self.chart_ids = list(state.get("chart_ids") or [])
        if "total_input_tokens" in state:
            self.total_input_tokens = int(state.get("total_input_tokens") or 0)
        if "total_output_tokens" in state:
            self.total_output_tokens = int(state.get("total_output_tokens") or 0)
        if "total_cost_usd" in state:
            self.total_cost_usd = float(state.get("total_cost_usd") or 0.0)
        if "last_prompt_tokens" in state:
            self.last_prompt_tokens = int(state.get("last_prompt_tokens") or 0)
        if "total_cached_input_tokens" in state:
            self.total_cached_input_tokens = int(state.get("total_cached_input_tokens") or 0)
        if "total_cache_write_tokens" in state:
            self.total_cache_write_tokens = int(state.get("total_cache_write_tokens") or 0)
        if "usage_breakdowns" in state:
            self.usage_breakdowns = copy.deepcopy(list(state.get("usage_breakdowns") or []))[-100:]
        if "command_metrics" in state:
            self.command_metrics = copy.deepcopy(list(state.get("command_metrics") or []))[-200:]
        if "compaction_state" in state:
            self.compaction_state = copy.deepcopy(
                dict(
                    state.get("compaction_state")
                    or {
                        "consecutive_failures": 0,
                        "last_failure_type": "",
                        "circuit_open": False,
                        "last_attempt_at": 0.0,
                        "last_success_at": 0.0,
                    }
                )
            )
        if "recent_sql" in state:
            self.recent_sql = copy.deepcopy(list(state.get("recent_sql") or []))[-5:]
        if "recent_artifacts" in state:
            self.recent_artifacts = copy.deepcopy(list(state.get("recent_artifacts") or []))[-20:]
        if "analysis_delete_operations" in state:
            self.analysis_delete_operations = copy.deepcopy(
                list(state.get("analysis_delete_operations") or [])
            )[-100:]
        if "turn_activations" in state:
            self.turn_activations = copy.deepcopy(list(state.get("turn_activations") or []))[-100:]
        if "discovered_tools" in state:
            self.discovered_tools = copy.deepcopy(list(state.get("discovered_tools") or []))[-100:]
        if "discovered_mcp_tools" in state:
            self.discovered_mcp_tools = copy.deepcopy(list(state.get("discovered_mcp_tools") or []))[-10:]
        if "mcp_tool_last_used" in state:
            self.mcp_tool_last_used = {
                str(name): float(value or 0)
                for name, value in dict(state.get("mcp_tool_last_used") or {}).items()
                if str(name) in self.discovered_mcp_tools
            }
        if "mcp_catalog_version" in state:
            self.mcp_catalog_version = str(state.get("mcp_catalog_version") or "")

    def record_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        breakdown: Optional[Dict[str, Any]] = None,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float | None = None,
        update_last_prompt: bool = True,
    ):
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        cached_input_tokens = int(cached_input_tokens or 0)
        cache_write_tokens = int(cache_write_tokens or 0)
        if cost_usd is not None:
            cost_usd = float(cost_usd)
            if cost_usd < 0 or not math.isfinite(cost_usd):
                raise ValueError("费用必须是非负的有限数字")
        with self._usage_lock:
            self.total_input_tokens += prompt_tokens
            self.total_output_tokens += completion_tokens
            if cost_usd is not None:
                self.total_cost_usd += cost_usd
            if update_last_prompt:
                self.last_prompt_tokens = prompt_tokens
            self.total_cached_input_tokens += cached_input_tokens
            self.total_cache_write_tokens += cache_write_tokens
            if isinstance(breakdown, dict):
                self.usage_breakdowns.append(dict(breakdown))
                self.usage_breakdowns = self.usage_breakdowns[-100:]

    def record_command_metric(
        self,
        *,
        command: str,
        command_type: str,
        outcome: str,
        duration_ms: int = 0,
        error_code: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_input_tokens: int = 0,
        compression_ratio: float | None = None,
    ) -> None:
        """Record bounded command telemetry without arguments or Prompt text."""
        item = {
            "command": str(command or "")[:80],
            "command_type": str(command_type or "")[:20],
            "outcome": str(outcome or "")[:20],
            "duration_ms": max(0, min(int(duration_ms or 0), 86_400_000)),
            "error_code": str(error_code or "")[:80],
            "input_tokens": max(0, int(input_tokens or 0)),
            "output_tokens": max(0, int(output_tokens or 0)),
            "cached_input_tokens": max(0, int(cached_input_tokens or 0)),
            "recorded_at": time.time(),
        }
        if compression_ratio is not None:
            item["compression_ratio"] = round(
                max(0.0, min(float(compression_ratio), 1.0)),
                4,
            )
        with self._usage_lock:
            self.command_metrics.append(item)
            self.command_metrics = self.command_metrics[-200:]

    def _ensure_fields(self):
        """Backfill any dataclass field missing on objects created by an
        older code version (e.g. surviving a hot-reload). Keeps old in-memory
        sessions usable after a field is added to ChatSession."""
        for f in fields(self):
            if not hasattr(self, f.name):
                if f.default is not MISSING:
                    setattr(self, f.name, f.default)
                elif f.default_factory is not MISSING:  # type: ignore[misc]
                    setattr(self, f.name, f.default_factory())
                else:
                    setattr(self, f.name, None)
        # Migrate sessions that had old single-source _active_source_id field
        if hasattr(self, "_active_source_id") and not self._active_ids:
            old_id = getattr(self, "_active_source_id", "")
            if old_id and any(e["id"] == old_id for e in self._sources):
                self._active_ids = [old_id]


_SESSION_TTL = 7200  # seconds before an idle session is evicted
_CLEANUP_INTERVAL = 1800  # how often the daemon thread wakes to prune

# ── Global JobsStore singleton (A6) ──────────────────────────────────────────
# One SQLite DB shared by all sessions. JobRunner instances are per-session,
# but they all write to this single store.
_global_jobs_store: Optional["JobsStore"] = None
_jobs_store_lock = threading.Lock()


def get_global_jobs_store() -> "JobsStore":
    """Return the process-wide JobsStore singleton (lazily created)."""
    global _global_jobs_store
    with _jobs_store_lock:
        if _global_jobs_store is None:
            from data.jobs_store import JobsStore

            _global_jobs_store = JobsStore()
            log.info(
                "[session] global JobsStore initialized at %s",
                _global_jobs_store.path,
            )
        return _global_jobs_store


def close_global_jobs_store() -> None:
    """Close and forget the process-wide JobsStore without masking shutdown errors."""
    global _global_jobs_store
    with _jobs_store_lock:
        store = _global_jobs_store
        _global_jobs_store = None
        if store is None:
            return
        try:
            store.close()
        except Exception:
            log.exception("[session] global JobsStore close failed")


atexit.register(close_global_jobs_store)


class SessionManager:
    def __init__(self, state_store=None):
        self._store: Dict[str, ChatSession] = {}
        self._store_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._chat_state_store = state_store
        self._cleanup_stop = threading.Event()
        self._cleanup_thread: Optional[threading.Thread] = None
        self._closed = False
        self._start_cleanup_daemon()

    def _state_store(self):
        if self._chat_state_store is None:
            from data.chat_state_store import get_chat_state_store

            self._chat_state_store = get_chat_state_store()
        return self._chat_state_store

    def _hydrate_persisted_state(self, session: ChatSession) -> None:
        """Refresh JSON conversation state written by another service."""
        try:
            record = self._state_store().get(session.session_id)
        except Exception:
            log.exception(
                "[session] persisted state read failed sid=%s",
                session.session_id,
            )
            return
        if not record or int(record.get("revision") or 0) <= int(
            getattr(session, "_persisted_state_revision", 0) or 0
        ):
            return
        state = record.get("state")
        if not isinstance(state, dict):
            return
        try:
            session.restore_rewind_state(state)
        except (TypeError, ValueError, KeyError):
            log.warning(
                "[session] persisted state rejected sid=%s",
                session.session_id,
            )
            return
        # Current rows always include these columns, so an explicit empty
        # value means "clear".  Older/custom stores may omit a column; in
        # that case retain the in-memory value instead of treating absence as
        # an instruction to erase it.
        for field_name in ("owner_user_id", "workspace_id", "model_provider"):
            if field_name in record:
                setattr(session, field_name, str(record.get(field_name) or ""))
        session._persisted_state_revision = int(record["revision"])

    def persist(self, sid: str) -> bool:
        """Persist bounded session JSON after a completed turn."""
        # Keep the lifecycle boundary across the snapshot, durable write and
        # revision update.  Otherwise close() can pop/release the session
        # after the lookup but while this method still reads or mutates it.
        with self._lifecycle_lock:
            with self._store_lock:
                if self._closed:
                    return False
                session = self._store.get(sid)
            if session is None:
                return False
            try:
                revision = self._state_store().save(
                    sid,
                    session.capture_rewind_state(),
                    owner_user_id=session.owner_user_id,
                    workspace_id=session.workspace_id,
                    model_provider=session.model_provider,
                    expected_revision=int(getattr(session, "_persisted_state_revision", 0) or 0),
                )
            except Exception:
                log.exception("[session] persisted state write failed sid=%s", sid)
                return False
            if revision is None:
                log.warning(
                    "[session] persisted state write lost revision race sid=%s",
                    sid,
                )
                self._hydrate_persisted_state(session)
                return False
            session._persisted_state_revision = int(revision)
            return True

    def create(self, owner_user_id: str = "") -> ChatSession:
        s = ChatSession(owner_user_id=owner_user_id)
        # Hold the lifecycle lock through the return so close() cannot release
        # this newly published session before the caller receives it.
        with self._lifecycle_lock:
            with self._store_lock:
                if self._closed:
                    raise RuntimeError("SessionManager is closed")
                self._store[s.session_id] = s
            return s

    def get(self, sid: str) -> Optional[ChatSession]:
        # Serialize reads with close(): once close() marks the manager closed,
        # callers must not receive a session whose resources are being
        # released. Keep the lifecycle lock through hydration as well; doing
        # only a closed check around the dictionary lookup still leaves a
        # window where close() can clear and release the session before get()
        # returns it.
        with self._lifecycle_lock:
            with self._store_lock:
                if self._closed:
                    return None
                s = self._store.get(sid)
                if s:
                    s._ensure_fields()
                    s.last_accessed = datetime.now()
            if s:
                self._hydrate_persisted_state(s)
            return s

    def get_or_create(self, sid: str) -> ChatSession:
        # This lock must cover hydration too.  A lookup-only lock still lets
        # close() begin releasing the session before this method returns.
        with self._lifecycle_lock:
            with self._store_lock:
                if self._closed:
                    raise RuntimeError("SessionManager is closed")
                s = self._store.get(sid) if sid else None
                if s is None:
                    s = ChatSession(session_id=sid) if sid else ChatSession()
                    self._store[s.session_id] = s
                s._ensure_fields()
                s.last_accessed = datetime.now()
            self._hydrate_persisted_state(s)
            return s

    def find_feishu_chat(self, chat_id: str) -> Optional[ChatSession]:
        """Return the active conversation explicitly linked to a Feishu group."""
        target = str(chat_id or "").strip()
        if not target:
            return None
        # Serialize the returned-session lookup with close(); otherwise the
        # list snapshot can outlive the manager entry and its resources.
        with self._lifecycle_lock:
            with self._store_lock:
                if self._closed:
                    return None
                sessions = list(self._store.values())
                for session in sessions:
                    session._ensure_fields()
                    if session.feishu_bot_enabled and session.feishu_chat_id == target:
                        session.last_accessed = datetime.now()
                        return session
            return None

    def remove(self, sid: str):
        # Keep pop() and the complete resource release in one lifecycle
        # critical section.  A concurrent close() must wait for this release,
        # rather than observe an already-popped but still-live session.
        with self._lifecycle_lock:
            with self._store_lock:
                if self._closed:
                    return
                s = self._store.pop(sid, None)
            if s is not None:
                self._release(sid, s, wait=True)

    @staticmethod
    def _finish_release(sid: str, session: ChatSession) -> None:
        session.close_sources()
        # Local import avoids coupling ChatSession construction to workspace
        # initialization while still releasing the C1 session reference.
        try:
            from data.workspace import workspace_manager
            from agent.workflows.runtime import workflow_runtime_manager

            workflow_runtime_manager.close_session(sid)
            workspace_manager.release_workflow_session(sid)
            workspace_manager.unmount(sid)
        except Exception:
            log.exception("[session] workspace release error sid=%s", sid)

    @staticmethod
    def _release(sid: str, session: ChatSession, *, wait: bool = False):
        """Release a session only after its process-local worker has quiesced.

        ``wait`` remains part of the private compatibility seam, but resource
        cleanup always requests a real wait.  If removal is initiated by the
        runner's own worker, the session cannot self-join; that one case is
        handed to a daemon finalizer which waits and then closes sources and
        workspace state.
        """
        del wait
        if session.shutdown_job_runner(wait=True):
            SessionManager._finish_release(sid, session)
            return None

        release_done = threading.Event()

        def _deferred_release() -> None:
            try:
                # This watcher is not a JobRunner worker, so it can safely
                # perform the blocking executor shutdown and heartbeat join.
                session.shutdown_job_runner(wait=True)
                SessionManager._finish_release(sid, session)
            finally:
                release_done.set()

        threading.Thread(
            target=_deferred_release,
            daemon=True,
            name=f"session-release-{sid[:8]}",
        ).start()
        return release_done

    @staticmethod
    def _has_active_jobs(session: ChatSession) -> bool:
        """Conservatively detect work that makes TTL eviction unsafe."""
        session._ensure_fields()
        with session._job_runner_lock:
            runner = session._job_runner
        if runner is None:
            return False
        try:
            return bool(runner.list_jobs(active_only=True, limit=1))
        except Exception:
            # A failed status read must not turn a live session into a resource
            # cleanup race. The next maintenance pass can retry safely.
            log.exception("[session] active job check failed sid=%s", session.session_id)
            return True

    def _cleanup_expired(self):
        cutoff = datetime.now()
        with self._store_lock:
            sessions = list(self._store.items())
        expired = []
        for sid, session in sessions:
            session._ensure_fields()
            if (cutoff - session.last_accessed).total_seconds() > _SESSION_TTL and not self._has_active_jobs(
                session
            ):
                expired.append((sid, session, session.last_accessed))
        removed = 0
        for sid, snapshot, observed_last_accessed in expired:
            with self._store_lock:
                s = self._store.get(sid)
                if (
                    s is not snapshot
                    or s.last_accessed != observed_last_accessed
                    or (datetime.now() - s.last_accessed).total_seconds() <= _SESSION_TTL
                    or self._has_active_jobs(s)
                ):
                    continue
                self._store.pop(sid, None)
            if s is not None:
                self._release(sid, s, wait=True)
                removed += 1
        with self._store_lock:
            remaining = len(self._store)
        if removed:
            log.info("[session] TTL cleanup  removed=%d  remaining=%d", removed, remaining)
        # Keep the durable Job event log bounded even when the application runs
        # continuously for weeks without restarting.
        with _jobs_store_lock:
            if _global_jobs_store is not None:
                try:
                    _global_jobs_store.cleanup_events()
                except Exception:
                    log.exception("[session] JobsStore event cleanup failed")

    def close(self) -> None:
        """Stop maintenance and release all session-owned resources once."""
        with self._lifecycle_lock:
            if self._closed:
                thread = self._cleanup_thread
                if thread is not None and thread is not threading.current_thread():
                    thread.join()
                return
            self._closed = True
            self._cleanup_stop.set()
            thread = self._cleanup_thread
            if thread is not None and thread is not threading.current_thread():
                # The API closes the process-wide SQLite stores only after
                # this method returns.  Do not continue after a timeout while
                # the maintenance thread could still be using them.
                thread.join()
            with self._store_lock:
                sessions = list(self._store.items())
                self._store.clear()
            for sid, session in sessions:
                try:
                    # Shutdown waits here so the following global SQLite close
                    # cannot race a still-running session JobRunner.
                    self._release(sid, session, wait=True)
                except Exception:
                    log.exception("[session] session resource cleanup failed sid=%s", sid)
            self._cleanup_thread = None
            log.info("[session] manager closed sessions=%d", len(sessions))

    def _start_cleanup_daemon(self):
        def _loop():
            while not self._cleanup_stop.wait(_CLEANUP_INTERVAL):
                try:
                    self._cleanup_expired()
                except Exception:
                    log.exception("[session] cleanup loop failed")

        t = threading.Thread(target=_loop, daemon=True, name="session-cleanup")
        self._cleanup_thread = t
        t.start()
