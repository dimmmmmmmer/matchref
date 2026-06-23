#!/usr/bin/env bash
#
# MatchRef — one-click installer for DaVinci Resolve (macOS).
#
# Double-click this file in Finder. It will:
#   1. create a local Python environment and install the dependencies, then
#   2. copy MatchRef into the DaVinci Resolve scripts folder.
#
# After it finishes, open Resolve and choose:
#   Workspace → Scripts → Utility → MatchRef
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Keep the Terminal window open so the user can read the outcome.
finish() {
  echo ""
  echo "Press Return to close this window."
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

# 1. Python 3 must be available.
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3 is required but was not found."
  echo "Install it from https://www.python.org/downloads/  (or run: brew install python)"
  echo "then double-click this installer again."
  false
fi
echo "→ Using $("$PYTHON" --version)"
echo ""

# 2. Virtual environment + dependencies.
echo "→ Setting up the Python environment (this can take a minute)…"
bash "$ROOT/setup.sh"

# 3. Install into the Resolve Scripts/Utility folder.
echo ""
echo "→ Installing into DaVinci Resolve…"
bash "$ROOT/install_resolve.sh"

echo ""
echo "✅ MatchRef is installed."
echo ""
echo "In DaVinci Resolve open:  Workspace → Scripts → Utility → MatchRef"
echo "(Do NOT use Scripts → matchref → main — that path fails silently.)"
finish
