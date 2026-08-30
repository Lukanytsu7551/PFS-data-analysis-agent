"""PFS product identity shared by the server-rendered application surface."""

import os


PRODUCT_SHORT_NAME = "PFS"
PRODUCT_NAME = os.environ.get("PFS_PRODUCT_NAME", "PFS 数据分析 Agent").strip()
PRODUCT_TAGLINE = os.environ.get(
    "PFS_PRODUCT_TAGLINE",
    "可追踪、可核验的报表数据分析工作台",
).strip()
PRODUCT_VERSION = os.environ.get("PFS_PRODUCT_VERSION", "0.1.0-dev").strip()
SERVICE_ID = "pfs-data-analysis-agent"
PRODUCT_ICON = "Images/pfs-mark.svg"
PRODUCT_REPOSITORY_URL = os.environ.get(
    "PFS_REPOSITORY_URL",
    "https://github.com/Lukanytsu7551/PFS-data-analysis-agent",
).strip()


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean feature flag without making deployment defaults unsafe."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
