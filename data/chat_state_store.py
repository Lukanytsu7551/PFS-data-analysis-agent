#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded cross-process persistence for reconstructable chat state.

ChatSession intentionally keeps live data-source objects and worker pools in
memory.  A separate API process must still see the completed conversation
after a queue worker finishes, so this store keeps only the JSON portion of a
session state.  Credentials, private keys, live connections and executable
objects are never part of this contract.
"""

from __future__ import annotations

import atexit
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from infrastructure.paths import data_path

log = logging.getLogger(__name__)

STATE_SCHEMA_VERSION = 1
MAX_STATE_BYTES = 2_000_000
_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|auth(?:orization)?|credential|password|"
    r"private[_-]?key|secret|(?:^|[_-])token(?:$|[_-]))",
    flags=re.IGNORECASE,
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS chat_session_states (
    session_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 1,
    owner_user_id TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL DEFAULT '',
    model_provider TEXT NOT NULL DEFAULT '',
    state_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""
_INDEXES = ("CREATE INDEX IF NOT EXISTS idx_chat_session_states_updated ON chat_session_states(updated_at)",)
_MIGRATION_COLUMNS = (
    ("owner_user_id", "TEXT NOT NULL DEFAULT ''"),
    ("workspace_id", "TEXT NOT NULL DEFAULT ''"),
    ("model_provider", "TEXT NOT NULL DEFAULT ''"),
)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON value with credential-looking keys removed."""
    if depth > 10:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:200_000]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:250]:
            key = str(raw_key)[:120]
            if key.startswith("_") or _SECRET_KEY.search(key):
                continue
            item = _safe_value(raw_value, depth=depth + 1)
            if item is not None:
                result[key] = item
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in list(value)[:250]:
            safe = _safe_value(item, depth=depth + 1)
            if safe is not None:
                result.append(safe)
        return result
    return str(value)[:200_000]


class ChatStateStoreError(RuntimeError):
    """Raised when a session snapshot cannot be persisted safely."""


class ChatStateStore:
    """SQLite state store with optimistic revision fencing."""

    def __init__(self, db_path: Path | None = None):
        self.path = Path(
            db_path
            or os.environ.get("PFS_CHAT_STATE_DB_PATH")
            or data_path("outputs", "sessions", "chat-state.db")
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(
                str(self.path),
                check_same_thread=False,
                timeout=30.0,
            )
            self._conn = conn
            conn.row_factory = sqlite3.Row
            with self._lock:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute(_CREATE_TABLE)
                    existing_columns = {
                        str(row[1])
                        for row in conn.execute("PRAGMA table_info(chat_session_states)").fetchall()
                    }
                    for column_name, column_definition in _MIGRATION_COLUMNS:
                        if column_name not in existing_columns:
                            # These identifiers and definitions are module
                            # constants, never user input.  ADD COLUMN keeps
                            # every existing row and fills the new NOT NULL
                            # fields with the same defaults as the new schema.
                            conn.execute(
                                "ALTER TABLE chat_session_states ADD COLUMN "
                                f"{column_name} {column_definition}"
                            )
                    for index_sql in _INDEXES:
                        conn.execute(index_sql)
                    conn.commit()
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        log.exception(
                            "[chat-state] initialization rollback failed path=%s",
                            self.path,
                        )
                    raise
        except Exception:
            conn = self._conn
            self._conn = None
            self._closed = True
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    log.exception(
                        "[chat-state] initialization cleanup failed path=%s",
                        self.path,
                    )
            raise

    def _connection_locked(self) -> sqlite3.Connection:
        if self._closed or self._conn is None:
            raise ChatStateStoreError("chat state store is closed")
        return self._conn

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connection_locked()
            row = conn.execute(
                "SELECT * FROM chat_session_states WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
        if row is None:
            return None
        try:
            state = json.loads(row["state_json"])
        except (TypeError, json.JSONDecodeError):
            log.warning("[chat-state] invalid snapshot sid=%s", session_id)
            return None
        if not isinstance(state, dict):
            return None
        return {
            "session_id": str(row["session_id"]),
            "revision": int(row["revision"] or 0),
            "owner_user_id": str(row["owner_user_id"] or ""),
            "workspace_id": str(row["workspace_id"] or ""),
            "model_provider": str(row["model_provider"] or ""),
            "state": state,
            "updated_at": float(row["updated_at"] or 0),
        }

    def save(
        self,
        session_id: str,
        state: Mapping[str, Any],
        *,
        owner_user_id: str = "",
        workspace_id: str = "",
        model_provider: str = "",
        expected_revision: int = 0,
    ) -> int | None:
        if not str(session_id or "").strip():
            raise ChatStateStoreError("session_id is required")
        safe_state = _safe_value(dict(state))
        if not isinstance(safe_state, dict):
            raise ChatStateStoreError("session state must be an object")
        safe_state["schema_version"] = STATE_SCHEMA_VERSION
        encoded = json.dumps(
            safe_state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > MAX_STATE_BYTES:
            raise ChatStateStoreError("session state exceeds 2 MB")
        sid = str(session_id).strip()[:200]
        owner = str(owner_user_id or "")[:200]
        workspace = str(workspace_id or "")[:200]
        provider = str(model_provider or "")[:200]
        now = time.time()
        expected = max(0, int(expected_revision or 0))
        with self._lock:
            conn = self._connection_locked()
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    "SELECT revision FROM chat_session_states WHERE session_id = ?",
                    (sid,),
                ).fetchone()
                current_revision = int(current["revision"] or 0) if current else 0
                if current_revision != expected:
                    conn.rollback()
                    return None
                revision = current_revision + 1
                conn.execute(
                    "INSERT INTO chat_session_states "
                    "(session_id, revision, owner_user_id, workspace_id, model_provider, state_json, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET revision=excluded.revision, "
                    "owner_user_id=excluded.owner_user_id, workspace_id=excluded.workspace_id, "
                    "model_provider=excluded.model_provider, state_json=excluded.state_json, "
                    "updated_at=excluded.updated_at",
                    (sid, revision, owner, workspace, provider, encoded, now),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return revision

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            conn = self._conn
            self._conn = None
            if conn is None:
                return
            try:
                conn.close()
            except Exception:
                log.exception("[chat-state] close failed path=%s", self.path)


_STORE: ChatStateStore | None = None
_STORE_LOCK = threading.Lock()


def get_chat_state_store() -> ChatStateStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = ChatStateStore()
        return _STORE


def close_chat_state_store() -> None:
    """Close and forget the lazy singleton without masking shutdown errors."""
    global _STORE
    with _STORE_LOCK:
        store = _STORE
        _STORE = None
        if store is None:
            return
        try:
            store.close()
        except Exception:
            log.exception("[chat-state] global store close failed")


atexit.register(close_chat_state_store)
