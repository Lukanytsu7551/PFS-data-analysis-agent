#!/usr/bin/env sh
set -eu

REPO_URL="${PFS_REPO_URL:-https://github.com/Lukanytsu7551/PFS-data-analysis-agent.git}"
PROJECT_NAME="${PFS_PROJECT_NAME:-PFS-data-analysis-agent}"
INSTALL_ROOT="${PFS_INSTALL_ROOT:-$HOME/.pfs-data-analysis-agent}"
PROJECT_DIR="$INSTALL_ROOT/$PROJECT_NAME"
LAUNCHER="${PFS_LAUNCHER_PATH:-$HOME/.local/bin/pfs-data-analysis-agent}"

info() {
  printf '[PFS] %s\n' "$1"
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python3 not found. Please install Python 3.10+ first." >&2
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10+ is required." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "Git not found. Please install Git first." >&2
  exit 1
fi

mkdir -p "$INSTALL_ROOT"
mkdir -p "$(dirname "$LAUNCHER")"

if [ -d "$PROJECT_DIR" ]; then
  info "Project already exists. Updating..."
  cd "$PROJECT_DIR"
  if [ ! -d .git ]; then
    echo "Existing install is not a Git checkout: $PROJECT_DIR" >&2
    exit 1
  fi
  working_tree="$(git status --porcelain --untracked-files=all)"
  if [ -n "$working_tree" ]; then
    echo "Existing install has local changes; refusing to overwrite it: $PROJECT_DIR" >&2
    exit 1
  fi
  git pull --ff-only
else
  info "Cloning project..."
  git clone "$REPO_URL" "$PROJECT_DIR"
  cd "$PROJECT_DIR"
fi

info "Creating virtual environment..."
python3 -m venv .venv

info "Installing dependencies..."
.venv/bin/python -m pip install --upgrade pip
DEPENDENCY_FILE="requirements.txt"
if [ -f requirements.lock.txt ]; then
  DEPENDENCY_FILE="requirements.lock.txt"
fi
info "Using dependency manifest: $DEPENDENCY_FILE"
.venv/bin/python -m pip install -r "$DEPENDENCY_FILE"

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env sh
if ! cd "$PROJECT_DIR"; then
  echo "[PFS][ERROR] Unable to enter the installed project directory: $PROJECT_DIR" >&2
  exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "[PFS][ERROR] The project virtual environment is not ready: .venv/bin/python" >&2
  exit 1
fi
if ! ".venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "[PFS][ERROR] Python 3.10+ is required in the project virtual environment." >&2
  exit 1
fi
exec ".venv/bin/python" app.py
EOF

chmod +x "$LAUNCHER"

info "Installed successfully."
info "Start with: pfs-data-analysis-agent"
info "If command not found, add this to your shell config:"
  info 'export PATH="$HOME/.local/bin:$PATH"'
