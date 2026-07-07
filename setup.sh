#!/usr/bin/env bash
# Create a project virtualenv and install dependencies (PEP 668 safe on macOS/Homebrew).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Error: $PYTHON not found. Install Python 3 (e.g. brew install python@3.12)." >&2
  exit 1
fi

# MatchRef needs Python 3.10+ (zip(strict=), PEP 604 unions). Stock macOS ships
# 3.9 — creating the venv from it yields a build that fails at runtime, so stop
# here with a clear message instead.
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  ver="$("$PYTHON" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  echo "Error: MatchRef requires Python 3.10+, but '$PYTHON' is $ver." >&2
  echo "Install a newer Python and re-run, e.g.:" >&2
  echo "  brew install python@3.12 && PYTHON=python3.12 bash setup.sh" >&2
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating virtual environment in $VENV_DIR ..."
  "$PYTHON" -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo ""
echo "Done. Activate the environment with:"
echo "  source $VENV_DIR/bin/activate"
echo ""
echo "Run the GUI:"
echo "  python main.py"
