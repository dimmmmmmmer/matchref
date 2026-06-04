#!/usr/bin/env python3
"""
DaVinci Resolve menu script — launches MatchRef GUI.

Copy or symlink this file into Resolve Scripts folder, e.g.:
  macOS: ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
  Windows: %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Fusion\\Scripts\\Utility\\
  Linux: ~/.local/share/DaVinciResolve/Fusion/Scripts/Utility/

Ensure the matchref package root is on PYTHONPATH or placed alongside this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from matchref.gui import run_gui  # noqa: E402

if __name__ == "__main__":
    run_gui()
