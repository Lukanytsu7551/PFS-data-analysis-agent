#!/usr/bin/env bash

# PFS Data Analysis Agent launcher.
# Environment setup is a separate, explicit user action.

cd "$(dirname "$0")" || {
    echo "[PFS][ERROR] Failed to switch to the project directory."
    exit 1
}

APP_FILE="app.py"
VENV_PYTHON=".venv/bin/python"
PORT=5001

show_install_steps() {
    echo
    echo "[PFS][ACTION] Prepare the local environment explicitly, then run ./start.command again:"
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/python -m pip install -r requirements.txt"
}

echo "============================================"
echo "  PFS Data Analysis Agent"
echo "============================================"

if [ ! -f "$APP_FILE" ]; then
    echo "[PFS][ERROR] Entry file not found: $APP_FILE"
    exit 1
fi

if [ ! -x "$VENV_PYTHON" ]; then
    echo "[PFS][ERROR] The project virtual environment is not ready: $VENV_PYTHON"
    show_install_steps
    exit 1
fi

if ! "$VENV_PYTHON" --version >/dev/null 2>&1; then
    echo "[PFS][ERROR] The project Python executable cannot run: $VENV_PYTHON"
    show_install_steps
    exit 1
fi

# Inspect only the explicit core startup manifest. Optional feature packages are
# intentionally excluded from this read-only launcher gate.
if ! "$VENV_PYTHON" - <<'PY'
from infrastructure.startup_requirements import (
    MissingCoreDependencies,
    inspect_startup_dependencies,
    require_core_dependencies,
)

report = inspect_startup_dependencies(optional={})
try:
    require_core_dependencies(report)
except MissingCoreDependencies as exc:
    packages = ", ".join(item.package_name for item in exc.dependencies)
    print(f"[PFS][ERROR] Missing required Python dependencies: {packages}")
    raise SystemExit(1) from None
PY
then
    show_install_steps
    exit 1
fi

if command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    port_pid=$(lsof -ti TCP:"$PORT" -sTCP:LISTEN | head -n 1)
    echo "[PFS][ERROR] Port $PORT is already in use. PID=$port_pid"
    echo "[PFS][TIP] Inspect it with: ps -p $port_pid"
    exit 1
fi

echo "[PFS][INFO] Starting PFS Data Analysis Agent"
echo "[PFS][INFO] Open: http://127.0.0.1:$PORT"
exec "$VENV_PYTHON" "$APP_FILE"
