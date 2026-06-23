#!/usr/bin/env bash
#
# MatchRef — one-click installer for DaVinci Resolve (Linux).
#
# Run this file (double-click and "Run in Terminal", or: bash "Install MatchRef (Linux).sh").
# It creates a local Python environment, installs dependencies, and copies
# MatchRef into the DaVinci Resolve scripts folder.
#
# After it finishes, open Resolve: Workspace -> Scripts -> Utility -> MatchRef
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

finish() {
  echo ""
  echo "Press Return to close."
  read -r _ || true
}
on_error() {
  echo ""
  echo "❌ Installation failed — see the error above."
  finish
  exit 1
}
trap on_error ERR

echo "============================================"
echo "  MatchRef installer for DaVinci Resolve"
echo "============================================"
echo ""

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 is required but was not found. Install it (e.g. sudo apt install python3-venv)"
  echo "then run this installer again."
  false
fi
echo "→ Using $("$PYTHON" --version)"
echo ""

echo "→ Setting up the Python environment (this can take a minute)…"
bash "$ROOT/setup.sh"

echo ""
echo "→ Installing into DaVinci Resolve…"
bash "$ROOT/install_resolve.sh"

echo ""
echo "✅ MatchRef is installed."
echo ""
echo "In DaVinci Resolve open:  Workspace → Scripts → Utility → MatchRef"
finish
