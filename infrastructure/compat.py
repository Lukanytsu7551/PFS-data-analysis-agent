"""Canonical readers for PFS configuration and workspace paths."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def env(primary: str, default: str = "") -> str:
    """Read a PFS setting, returning *default* when it is unset."""
    value = str(os.environ.get(primary) or "").strip()
    return value or default


def env_from(environ: Mapping[str, str], primary: str, default: str = "") -> str:
    """Testable variant of :func:`env` for startup and configuration code."""
    value = str(environ.get(primary) or "").strip()
    return value or default


def optional_feature_enabled(
    name: str,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether a retained optional feature is explicitly enabled."""
    values = environ if environ is not None else os.environ
    key = f"PFS_ENABLE_{str(name or '').strip().upper()}"
    return env_from(values, key, "").lower() in {"1", "true", "yes", "on"}


def cloud_login_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether the retained cloud-login mode is explicitly enabled."""
    values = environ if environ is not None else os.environ
    enabled = optional_feature_enabled("CLOUD_LOGIN", values)
    return enabled and (
        bool(values.get("RAILWAY_PROJECT_ID")) or values.get("VERCEL") == "1"
    )


def request_user_id(
    headers: Mapping[str, object],
    body: Mapping[str, object] | None = None,
    default: str = "",
) -> str:
    """Read the PFS request identity from the header or request body."""
    for key in ("X-PFS-User-ID",):
        value = str(headers.get(key) or "").strip()
        if value:
            return value[:200]
    for value in ((body or {}).get("user_id"), default):
        text = str(value or "").strip()
        if text:
            return text[:200]
    return ""


def workspace_hidden_dir(workdir: Path, name: str) -> Path:
    """Return a private PFS directory below a mounted workspace."""
    return Path(workdir) / name


def workspace_metadata_dir(workdir: Path) -> Path:
    """Return the private PFS metadata directory for a mounted workspace."""
    return workspace_hidden_dir(Path(workdir), ".pfs")
