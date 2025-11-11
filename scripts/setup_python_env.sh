#!/usr/bin/env bash
# Helper script to create a .venv for this project and install requirements.
# It prefers pyenv if available; otherwise it falls back to system python3.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Default venv name
VENV_DIR=".venv"
PYENV_BIN="$(command -v pyenv || true)"

echo "Repository root: $REPO_ROOT"

if [ -n "$PYENV_BIN" ]; then
  echo "pyenv found at: $PYENV_BIN"
  # If .python-version exists, pyenv will already select the version
  if [ -f ".python-version" ]; then
    echo ".python-version found; pyenv will use that Python for creating the venv."
  else
    echo "No .python-version found. You can run 'pyenv install 3.12.x' and 'pyenv local 3.12.x' before running this script."
  fi
fi

# Determine python executable to use
PYTHON_CMD=""
if [ -n "$PYENV_BIN" ]; then
  # Let pyenv choose the python in the current shell
  PYTHON_CMD="python"
else
  # Prefer explicit python3.12 if available, then python3
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_CMD=python3.12
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD=python3
  else
    echo "No suitable Python found. Install python3.12 via Homebrew or install pyenv."
    exit 1
  fi
fi

echo "Using python command: $PYTHON_CMD"

# Create venv
if [ -d "$VENV_DIR" ]; then
  echo "Found existing $VENV_DIR — removing it to recreate a fresh venv."
  rm -rf "$VENV_DIR"
fi

$PYTHON_CMD -m venv "$VENV_DIR"

# Activate and install
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt || {
  echo "\nERROR: 'pip install -r requirements.txt' failed." >&2
  echo "This can happen if a package needs a compiled extension (e.g. watchdog)" >&2
  echo "and the system build tools are not available. On macOS, try: xcode-select --install" >&2
  echo "You can re-run this script after installing the tools." >&2
  exit 1
}

# Ensure watchdog is installed (fast file-change notifications). If installation
# of watchdog failed above this will not be reached because of -e, but keep a
# safe check here if requirements were modified.
if ! python -c "import watchdog" >/dev/null 2>&1; then
  echo "watchdog not importable after install; attempting to install directly..."
  if pip install watchdog; then
    echo "Installed watchdog successfully."
  else
    echo "Failed to install watchdog via pip. If you see build errors, run:" >&2
    echo "  xcode-select --install" >&2
    echo "and then re-run: bash scripts/setup_python_env.sh" >&2
    echo "Alternatively install prebuilt Python (Homebrew/python3.12) so wheels are available." >&2
  fi
fi

echo "Created and populated virtualenv at: $VENV_DIR"

echo "To activate: source $VENV_DIR/bin/activate"
echo "To run the app: streamlit run app.py"
