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

echo "Installing project to: $RESOLVE_UTILITY/matchref"
rm -rf "$RESOLVE_UTILITY/matchref"
rsync -a \
  --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
  --exclude 'debug' --exclude '.pytest_cache' --exclude '.ruff_cache' \
  --exclude '.DS_Store' \
  "$ROOT/" "$RESOLVE_UTILITY/matchref/"

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
