"""PFS product identity shared by the server-rendered application surface."""

from __future__ import annotations

import json
import os
import plistlib
from pathlib import Path
import sys


PRODUCT_SHORT_NAME = "PFS"
PRODUCT_NAME = os.environ.get("PFS_PRODUCT_NAME", "PFS 数据分析 Agent").strip()
PRODUCT_TAGLINE = os.environ.get(
    "PFS_PRODUCT_TAGLINE",
    "可追踪、可核验的数据分析工作台",
).strip()
_DEFAULT_PRODUCT_VERSION = "0.1.0"
_FROZEN_PRODUCT_METADATA = "pfs-product-metadata.json"
_FROZEN_VERSION_KEYS = (
    "PFSProductVersion",
    "product_version",
    "PFS_PRODUCT_VERSION",
    "CFBundleShortVersionString",
    "CFBundleVersion",
)
SERVICE_ID = "pfs-data-analysis-agent"
PRODUCT_ICON = "Images/pfs-mark.svg"
PRODUCT_REPOSITORY_URL = os.environ.get(
    "PFS_REPOSITORY_URL",
    "https://github.com/Lukanytsu7551/PFS-data-analysis-agent",
).strip()


def _clean_product_version(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _frozen_metadata_paths() -> tuple[Path, ...]:
    """Return package-owned version metadata paths without inspecting user data."""
    if not getattr(sys, "frozen", False):
        return ()

    candidates: list[Path] = []
    executable = _clean_product_version(getattr(sys, "executable", ""))
    if executable and sys.platform == "darwin":
        try:
            executable_path = Path(executable).resolve(strict=False)
        except (OSError, RuntimeError):
            executable_path = Path(executable)
        for parent in executable_path.parents:
            if parent.name == "Contents":
                candidates.append(parent / "Info.plist")
                break

    meipass = _clean_product_version(getattr(sys, "_MEIPASS", ""))
    if meipass:
        resource_root = Path(meipass)
        candidates.append(resource_root / _FROZEN_PRODUCT_METADATA)
        if sys.platform == "darwin":
            candidates.extend(
                (
                    resource_root / "Info.plist",
                    resource_root.parent / "Info.plist",
                    resource_root.parent.parent / "Info.plist",
                )
            )

    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def _frozen_product_version() -> str:
    """Read the product version embedded in a frozen package, if present."""
    for path in _frozen_metadata_paths():
        try:
            if path.name == "Info.plist":
                with path.open("rb") as stream:
                    metadata = plistlib.load(stream)
            else:
                metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
        if not isinstance(metadata, dict):
            continue
        for key in _FROZEN_VERSION_KEYS:
            version = _clean_product_version(metadata.get(key))
            if version:
                return version
    return ""


def _resolve_product_version() -> str:
    """Prefer frozen package identity, while preserving the source default."""
    if getattr(sys, "frozen", False):
        return _frozen_product_version() or _DEFAULT_PRODUCT_VERSION
    return _clean_product_version(os.environ.get("PFS_PRODUCT_VERSION")) or _DEFAULT_PRODUCT_VERSION


PRODUCT_VERSION = _resolve_product_version()


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean feature flag without making deployment defaults unsafe."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
