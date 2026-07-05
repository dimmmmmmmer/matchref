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

# Preserve the user's saved settings (reference/conform paths, options) across a
# reinstall — they live in config/user_config.json inside the install dir, which
# the rm -rf below would otherwise wipe on every update.
SAVED_CONFIG=""
if [[ -f "$DEST/config/user_config.json" ]]; then
  SAVED_CONFIG="$(mktemp)"
  cp "$DEST/config/user_config.json" "$SAVED_CONFIG"
  echo "Preserving existing user settings (config/user_config.json)."
fi

echo "Installing project to: $DEST"
rm -rf "$DEST"
rsync -a \
  --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
  --exclude 'debug' --exclude '.pytest_cache' --exclude '.ruff_cache' \
  --exclude '.DS_Store' \
  "$ROOT/" "$DEST/"

if [[ -n "$SAVED_CONFIG" ]]; then
  mkdir -p "$DEST/config"
  cp "$SAVED_CONFIG" "$DEST/config/user_config.json"
  rm -f "$SAVED_CONFIG"
fi

if [[ -d "$ROOT/.venv" ]]; then
  echo "Copying .venv (may take a minute)..."
  rsync -a "$ROOT/.venv/" "$RESOLVE_UTILITY/matchref/.venv/"
fi

cp "$ROOT/scripts/MatchRef.py" "$RESOLVE_UTILITY/MatchRef.py"
chmod +x "$RESOLVE_UTILITY/MatchRef.py"

echo ""
echo "Done. In Resolve use:"
echo "  Workspace → Scripts → Utility → MatchRef"
echo ""
echo "Do NOT use Scripts → matchref → main (that path fails silently)."
