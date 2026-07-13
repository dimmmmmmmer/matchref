#!/usr/bin/env python3
"""MatchRef entry point for DaVinci Resolve and standalone launch."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Fail loudly on an unsupported interpreter (Resolve's bundled Python or a stock
# macOS python3 can be 3.9). Without this the tool dies mid-analysis with an
# opaque `zip() takes no keyword arguments`, which reads like a MatchRef bug.
if sys.version_info < (3, 10):  # noqa: UP036 — intentional runtime guard; may run on 3.9
    _v = ".".join(str(p) for p in sys.version_info[:3])
    raise SystemExit(
        f"MatchRef requires Python 3.10 or newer, but is running under {_v}.\n"
        "Re-run the installer with a newer Python "
        "(e.g. PYTHON=python3.12 ./setup.sh), or install Python 3.10+."
    )

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Match offline reference transforms to conformed timeline clips.",
    )
    parser.add_argument("--config", help="Path to JSON config file")
    parser.add_argument("--analyze", action="store_true", help="Run analysis headless")
    parser.add_argument("--apply", action="store_true", help="Apply last analysis headless")
    parser.add_argument(
        "--no-gui", action="store_true", help="Disable GUI (requires --analyze or --apply)"
    )
    args = parser.parse_args()

    if args.analyze or args.apply:
        # Heavy imports (OpenCV/numpy via the pipeline) belong to the headless
        # path only — the GUI defers them so its window paints immediately.
        from matchref.config import AppConfig
        from matchref.logging_report import setup_logging
        from matchref.pipeline import MatchRefPipeline

        config = AppConfig(Path(args.config)) if args.config else AppConfig()
        setup_logging(str(config.get("log_file", "")))
        pipeline = MatchRefPipeline(config)
        pipeline.connect()
        if args.apply:
            # An analysis report can't persist across processes (it holds live
            # Resolve API object references and numpy warps), so a standalone
            # apply must re-analyze the current timeline. Force dry_run off so
            # the inline apply actually writes to the Inspector.
            config.set("dry_run", False)
            pipeline.run_selected()
        else:
            pipeline.analyze_selected()
        return 0

    if args.no_gui:
        parser.error("Headless mode requires --analyze and/or --apply.")
        return 2

    from matchref.gui import run_gui

    return run_gui(args.config)


def _show_startup_error(exc: BaseException) -> None:
    import traceback

    msg = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    traceback.print_exc()
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is None:
            QApplication([])
        QMessageBox.critical(None, "MatchRef", msg)
    except Exception:
        print(f"MatchRef error: {msg}", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        _show_startup_error(exc)
        raise SystemExit(1) from exc
