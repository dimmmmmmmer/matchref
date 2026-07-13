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

# 4. Resolve itself must be able to load Python or it hides .py scripts from
#    the Workspace → Scripts menu. It only detects the python.org framework
#    build — offer to install it right here (needs the admin password).
#    Version pinned to a python.org release that ships a macOS installer.
PYORG_VERSION="3.13.14"
FUSCRIPT="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fuscript"
if [[ -x "$FUSCRIPT" ]] && ! "$FUSCRIPT" -l py3 -x "print(1)" >/dev/null 2>&1; then
  echo ""
  echo "DaVinci Resolve cannot load Python 3 yet, so MatchRef would not appear"
  echo "in the Workspace → Scripts menu. This needs Python from python.org"
  echo "(Homebrew/uv/pyenv builds are not detected by Resolve)."
  read -r -p "Download and install Python $PYORG_VERSION from python.org now? [Y/n] " answer
  if [[ ! "$answer" =~ ^[Nn] ]]; then
    PKG="$(mktemp -d)/python-$PYORG_VERSION-macos11.pkg"
    echo "→ Downloading Python $PYORG_VERSION…"
    curl -fL --progress-bar -o "$PKG" \
      "https://www.python.org/ftp/python/$PYORG_VERSION/python-$PYORG_VERSION-macos11.pkg"
    # Refuse to install anything that is not Apple-notarized PSF software.
    if ! pkgutil --check-signature "$PKG" | grep -q "Python Software Foundation"; then
      echo "❌ Signature check failed — not installing. Get Python manually from python.org."
      false
    fi
    echo "→ Installing (administrator password required)…"
    sudo installer -pkg "$PKG" -target /
    rm -f "$PKG"
    if "$FUSCRIPT" -l py3 -x "print(1)" >/dev/null 2>&1; then
      echo "✅ Resolve can now load Python."
    else
      echo "⚠️  Resolve still cannot load Python — restart Resolve and check again."
    fi
  else
    echo "Skipped. Install Python from https://www.python.org/downloads/macos/ later,"
    echo "then restart Resolve — otherwise MatchRef will not show up in the menu."
  fi
fi

echo ""
echo "✅ MatchRef is installed."
echo ""
echo "In DaVinci Resolve open:  Workspace → Scripts → Utility → MatchRef"
echo "(Do NOT use Scripts → matchref → main — that path fails silently.)"
finish
