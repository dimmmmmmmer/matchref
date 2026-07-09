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
        assert not window.progress_bar.isVisible()
    finally:
        window.close()


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
