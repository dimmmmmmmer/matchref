#!/usr/bin/env bash
# Install the MatchRef launcher into the DaVinci Resolve Scripts/Utility folder.
# Works on macOS and Linux. (Windows: use "Install MatchRef (Windows).bat".)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$(uname -s)" in
  Darwin)
    RESOLVE_UTILITY="${HOME}/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
    ;;
  Linux)
    RESOLVE_UTILITY="${HOME}/.local/share/DaVinciResolve/Fusion/Scripts/Utility"
    ;;
  *)
    echo "Unsupported OS for this installer. On Windows run 'Install MatchRef (Windows).bat'." >&2
    exit 1
    ;;
esac

mkdir -p "$RESOLVE_UTILITY"

DEST="$RESOLVE_UTILITY/matchref"

echo "Installing project to: $DEST"
# Sync the project into place. --delete drops files removed upstream; the
# excludes protect what must survive a reinstall: the user's saved settings
# (config/user_config.json) and the installed .venv (handled separately below,
# so a reinstall can never leave the launcher without a working environment).
mkdir -p "$DEST"
rsync -a --delete \
  --exclude '.venv' --exclude 'config/user_config.json' \
  --exclude '__pycache__' --exclude '.git' \
  --exclude 'debug' --exclude '.pytest_cache' --exclude '.ruff_cache' \
  --exclude '.DS_Store' \
  "$ROOT/" "$DEST/"

# Replace the installed .venv only with a usable one (Python >=3.10). A stale
# local .venv (built before the setup.sh version guard) must not clobber a
# working installed environment.
if [[ -d "$ROOT/.venv" ]]; then
  if "$ROOT/.venv/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    echo "Copying .venv (may take a minute)..."
    rsync -a --delete "$ROOT/.venv/" "$DEST/.venv/"
  else
    echo "⚠️  Skipping .venv copy: the local .venv needs Python 3.10+ (run ./setup.sh to rebuild)."
    if [[ -d "$DEST/.venv" ]]; then
      echo "   Keeping the previously installed .venv."
    fi
  fi
fi

cp "$ROOT/scripts/MatchRef.py" "$RESOLVE_UTILITY/MatchRef.py"
chmod +x "$RESOLVE_UTILITY/MatchRef.py"

# Resolve hides .py entries from Workspace → Scripts when its own scripting
# host cannot load Python (it only detects specific installs — on macOS the
# python.org framework build; uv/pyenv/Homebrew pythons are invisible to it).
# Ask Resolve's bundled interpreter directly so the user learns this now, not
# after hunting for a missing menu item.
check_resolve_python() {
  local fuscript=""
  case "$(uname -s)" in
    Darwin)
      fuscript="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fuscript"
      ;;
    Linux)
      fuscript="/opt/resolve/libs/Fusion/fuscript"
      ;;
  esac
  if [[ -z "$fuscript" || ! -x "$fuscript" ]]; then
    return 0  # Resolve not found where expected — nothing to verify
  fi
  if "$fuscript" -l py3 -x "print('ok')" >/dev/null 2>&1; then
    return 0
  fi
  echo ""
  echo "⚠️  DaVinci Resolve cannot load Python 3, so MatchRef will NOT appear"
  echo "   in the Workspace → Scripts menu (.py scripts are hidden without it)."
  if [[ "$(uname -s)" == "Darwin" ]]; then
    echo "   Install Python from https://www.python.org/downloads/macos/ (the"
    echo "   official installer — Homebrew/uv/pyenv builds are not detected),"
    echo "   then restart Resolve."
  else
    echo "   Install Python 3 with your distribution's package manager, then"
    echo "   restart Resolve."
  fi
}
check_resolve_python

echo ""
echo "Done. In Resolve use:"
echo "  Workspace → Scripts → Utility → MatchRef"
echo ""
echo "Do NOT use Scripts → matchref → main (that path fails silently)."
