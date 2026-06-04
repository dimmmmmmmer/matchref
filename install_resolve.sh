#!/usr/bin/env bash
# Install MatchRef launcher into DaVinci Resolve Scripts/Utility (macOS).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESOLVE_UTILITY="${HOME}/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"

mkdir -p "$RESOLVE_UTILITY"

echo "Installing project to: $RESOLVE_UTILITY/matchref"
rm -rf "$RESOLVE_UTILITY/matchref"
rsync -a --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
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
