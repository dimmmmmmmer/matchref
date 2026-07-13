"""GUI construction smoke test (offscreen Qt — no display needed).

Builds the real MainWindow with the offscreen platform plugin so every panel
builder and the config→UI load path actually run. Skips cleanly where Qt
cannot initialize at all (e.g. a CI image without GL/EGL system libraries).
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        try:
            app = QApplication([])
        except Exception as exc:  # pragma: no cover — CI image without GL/EGL
            pytest.skip(f"Qt offscreen platform unavailable: {exc}")
    return app


def test_main_window_builds_offscreen(qt_app: QApplication) -> None:
    from matchref.gui import MatchRefWindow

    window = MatchRefWindow()
    try:
        # Every panel exists and the defaults made it from config to the widgets.
        assert window.selection_mode_combo.count() > 0
        assert window.selection_color_combo.count() == 16
        assert window.run_btn.text()
        assert window.match_scale_cb.isChecked()
        # Shipped defaults are zoom-first: position/rotation matching is off.
        # The checkboxes must mirror config, not a hardcoded pre-check.
        assert not window.match_position_cb.isChecked()
        assert not window.match_rotation_cb.isChecked()
        assert not window.progress_bar.isVisible()
        # The engine (OpenCV etc.) is built lazily by the first run.
        assert window.pipeline is None
    finally:
        window.close()


def test_gui_import_defers_heavy_modules() -> None:
    # The window must paint fast: importing the GUI module must not pull
    # OpenCV/numpy (they load with the pipeline, in the worker thread).
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    code = "import sys, matchref.gui; sys.exit(1 if 'cv2' in sys.modules else 0)"
    env = dict(os.environ, PYTHONPATH=str(repo_root))
    result = subprocess.run([sys.executable, "-c", code], env=env, check=False)
    assert result.returncode == 0, "importing matchref.gui must not import cv2"


def test_selection_mode_toggles_color_row(qt_app: QApplication) -> None:
    from matchref.gui import MatchRefWindow
    from matchref.selection_ui import SELECTION_MODES, mode_uses_color, mode_uses_track

    window = MatchRefWindow()
    try:
        for index, (value, _label) in enumerate(SELECTION_MODES):
            window.selection_mode_combo.setCurrentIndex(index)
            # The window itself is never shown offscreen, so check the explicit
            # hidden flag rather than effective visibility.
            assert window.selection_color_combo.isHidden() != mode_uses_color(value)
            assert window.selection_track_spin.isHidden() != mode_uses_track(value)
    finally:
        window.close()
