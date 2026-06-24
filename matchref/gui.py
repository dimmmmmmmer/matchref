"""PySide6 GUI for MatchRef."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from matchref.config import USER_CONFIG_PATH, AppConfig
from matchref.conform_paths import assign_conform_path, conform_path_from_config
from matchref.debug_frames import resolve_debug_dir
from matchref.logging_report import setup_logging
from matchref.pipeline import MatchRefPipeline
from matchref.selection_ui import (
    RESOLVE_COLORS,
    SELECTION_MODES,
    apply_selection_to_config,
    mode_uses_color,
    mode_uses_track,
    selection_from_config,
)

try:
    from PySide6.QtCore import QObject, QThread, Signal
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFileDialog,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QSpinBox,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise ImportError("PySide6 is required for the MatchRef GUI.") from exc

CONFORM_FILTER = "Conform (*.edl *.xml *.fcpxml);;EDL (*.edl);;XML (*.xml *.fcpxml);;All Files (*)"


def _open_in_file_manager(path: Path) -> None:
    """Reveal a folder in the OS file manager (macOS / Windows / Linux)."""
    import subprocess

    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        import os

        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


class PipelineWorker(QObject):
    """Runs MatchRef in a background thread so the UI stays responsive."""

    log_line = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, pipeline: MatchRefPipeline) -> None:
        super().__init__()
        self.pipeline = pipeline
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            if self.pipeline.resolve is None:
                self.pipeline.connect()
            self.pipeline.run_selected(
                on_message=self.log_line.emit,
                should_cancel=lambda: self._cancelled,
            )
            self.finished_ok.emit()
        except Exception as exc:  # noqa: BLE001
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            self.failed.emit(detail)


class MatchRefWindow(QMainWindow):
    def __init__(self, config: AppConfig | None = None) -> None:
        super().__init__()
        self.config = config or AppConfig()
        self.logger = setup_logging(str(self.config.get("log_file", "")))
        self.pipeline = MatchRefPipeline(self.config, self.logger)
        self._worker_thread: QThread | None = None
        self._worker: PipelineWorker | None = None
        self.setWindowTitle("MatchRef — Offline Transform Match")
        self.resize(760, 620)
        self._build_ui()
        self._load_config_to_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        ref_group = QGroupBox("Offline Reference")
        ref_layout = QHBoxLayout(ref_group)
        self.ref_path_edit = QLineEdit()
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_reference)
        ref_layout.addWidget(self.ref_path_edit)
        ref_layout.addWidget(browse_btn)
        layout.addWidget(ref_group)

        conform_group = QGroupBox("Conform EDL / XML (optional)")
        conform_layout = QHBoxLayout(conform_group)
        self.conform_path_edit = QLineEdit()
        conform_layout.addWidget(self.conform_path_edit)
        conform_browse = QPushButton("Browse")
        conform_browse.clicked.connect(self._browse_conform)
        conform_layout.addWidget(conform_browse)
        layout.addWidget(conform_group)

        select_group = QGroupBox("Clips to process")
        select_layout = QGridLayout(select_group)
        select_layout.addWidget(QLabel("Pick by"), 0, 0)
        self.selection_mode_combo = QComboBox()
        for value, label in SELECTION_MODES:
            self.selection_mode_combo.addItem(label, value)
        self.selection_mode_combo.currentIndexChanged.connect(self._on_selection_mode_changed)
        select_layout.addWidget(self.selection_mode_combo, 0, 1)
        self.selection_color_label = QLabel("Color")
        select_layout.addWidget(self.selection_color_label, 1, 0)
        self.selection_color_combo = QComboBox()
        self.selection_color_combo.addItems(RESOLVE_COLORS)
        select_layout.addWidget(self.selection_color_combo, 1, 1)
        self.selection_track_label = QLabel("Video track")
        select_layout.addWidget(self.selection_track_label, 2, 0)
        self.selection_track_spin = QSpinBox()
        self.selection_track_spin.setRange(1, 99)
        select_layout.addWidget(self.selection_track_spin, 2, 1)
        layout.addWidget(select_group)

        toggles = QGroupBox("Match Options")
        toggle_layout = QGridLayout(toggles)
        self.match_scale_cb = QCheckBox("Match Scale")
        self.match_position_cb = QCheckBox("Match Position")
        self.match_rotation_cb = QCheckBox("Match Rotation")
        self.use_mid_cb = QCheckBox("Use Midpoint Keyframe")
        self.dry_run_cb = QCheckBox("Dry Run (analyze only, no transforms)")
        self.debug_cb = QCheckBox("Debug: save comparison frames")
        for cb in (
            self.match_scale_cb,
            self.match_position_cb,
            self.match_rotation_cb,
            self.use_mid_cb,
        ):
            cb.setChecked(True)
        self.dry_run_cb.setChecked(False)
        self.dry_run_cb.toggled.connect(self._update_run_button_label)
        toggle_layout.addWidget(self.match_scale_cb, 0, 0)
        toggle_layout.addWidget(self.match_position_cb, 0, 1)
        toggle_layout.addWidget(self.match_rotation_cb, 1, 0)
        toggle_layout.addWidget(self.use_mid_cb, 1, 1)
        toggle_layout.addWidget(self.dry_run_cb, 2, 0, 1, 2)
        toggle_layout.addWidget(self.debug_cb, 3, 0, 1, 2)
        layout.addWidget(toggles)

        debug_row = QHBoxLayout()
        debug_row.addWidget(QLabel("Debug folder"))
        self.debug_dir_edit = QLineEdit()
        self.debug_dir_edit.setPlaceholderText("~/Documents/matchref/debug")
        debug_row.addWidget(self.debug_dir_edit)
        self.debug_open_btn = QPushButton("Open")
        self.debug_open_btn.clicked.connect(self._open_debug_folder)
        debug_row.addWidget(self.debug_open_btn)
        layout.addLayout(debug_row)

        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel("ECC Threshold"))
        self.ecc_threshold_spin = QDoubleSpinBox()
        self.ecc_threshold_spin.setRange(0.0, 1.0)
        self.ecc_threshold_spin.setSingleStep(0.01)
        self.ecc_threshold_spin.setDecimals(3)
        self.ecc_threshold_spin.setValue(0.85)
        threshold_row.addWidget(self.ecc_threshold_spin)
        threshold_row.addStretch()
        layout.addLayout(threshold_row)

        advanced = QGroupBox("Advanced (experimental — validate in Resolve)")
        adv_layout = QGridLayout(advanced)
        self.fusion_cb = QCheckBox("Keyframe animated clips via Fusion")
        self.perspective_cb = QCheckBox("Perspective match (Fusion CornerPin)")
        self.auto_tc_cb = QCheckBox("Auto-align start timecode (timeline ↔ reference)")
        adv_layout.addWidget(self.fusion_cb, 0, 0, 1, 2)
        adv_layout.addWidget(self.perspective_cb, 1, 0, 1, 2)
        adv_layout.addWidget(self.auto_tc_cb, 2, 0, 1, 2)
        adv_layout.addWidget(QLabel("Reference start TC"), 3, 0)
        self.ref_tc_edit = QLineEdit()
        self.ref_tc_edit.setPlaceholderText("e.g. 01:00:00:00 (optional, for auto-align)")
        adv_layout.addWidget(self.ref_tc_edit, 3, 1)
        layout.addWidget(advanced)

        action_row = QHBoxLayout()
        self.run_btn = QPushButton("Run MatchRef")
        self.run_btn.clicked.connect(self._on_run)
        self.cancel_btn = QPushButton("Stop")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        action_row.addWidget(self.run_btn)
        action_row.addWidget(self.cancel_btn)
        layout.addLayout(action_row)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #444;")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

        hint = QLabel(
            "Per clip: analyze → log → apply → next. Debug frames: online (as matched) on "
            "the left, offline lock-cut on the right. See comparison.txt in the debug folder."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        layout.addWidget(hint)

    def _browse_conform(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Conform EDL or XML",
            str(Path.home()),
            CONFORM_FILTER,
        )
        if path:
            self.conform_path_edit.setText(path)

    def _load_config_to_ui(self) -> None:
        self.ref_path_edit.setText(str(self.config.get("offline_reference_path", "")))
        self.conform_path_edit.setText(conform_path_from_config(self.config))
        self.match_scale_cb.setChecked(bool(self.config.get("match_scale", True)))
        self.match_position_cb.setChecked(bool(self.config.get("match_position", True)))
        self.match_rotation_cb.setChecked(bool(self.config.get("match_rotation", True)))
        self.use_mid_cb.setChecked(bool(self.config.get("use_midpoint_keyframe", True)))
        self.dry_run_cb.setChecked(bool(self.config.get("dry_run", False)))
        self.debug_cb.setChecked(bool(self.config.get("debug_save_frames", False)))
        self.debug_dir_edit.setText(str(self.config.get("debug_output_dir", "")))
        self.ecc_threshold_spin.setValue(float(self.config.get("ecc_threshold", 0.85)))

        mode, color, track = selection_from_config(self.config)
        mode_idx = self.selection_mode_combo.findData(mode)
        self.selection_mode_combo.setCurrentIndex(mode_idx if mode_idx >= 0 else 0)
        color_idx = self.selection_color_combo.findText(color)
        self.selection_color_combo.setCurrentIndex(max(0, color_idx))
        self.selection_track_spin.setValue(track)
        self._on_selection_mode_changed()

        self.fusion_cb.setChecked(bool(self.config.get("apply_via_fusion", False)))
        self.perspective_cb.setChecked(bool(self.config.get("perspective_match_enabled", False)))
        self.auto_tc_cb.setChecked(bool(self.config.get("auto_align_start_timecode", False)))
        self.ref_tc_edit.setText(str(self.config.get("reference_start_timecode", "")))

        self._update_run_button_label()

    def _on_selection_mode_changed(self) -> None:
        """Show the color picker / track spinner only when the mode needs them."""
        mode = str(self.selection_mode_combo.currentData() or "auto")
        uses_color = mode_uses_color(mode)
        uses_track = mode_uses_track(mode)
        self.selection_color_label.setVisible(uses_color)
        self.selection_color_combo.setVisible(uses_color)
        self.selection_track_label.setVisible(uses_track)
        self.selection_track_spin.setVisible(uses_track)

    def _sync_ui_to_config(self) -> None:
        self.config.set("offline_reference_path", self.ref_path_edit.text().strip())
        assign_conform_path(self.config, self.conform_path_edit.text())
        self.config.set("match_scale", self.match_scale_cb.isChecked())
        self.config.set("match_position", self.match_position_cb.isChecked())
        self.config.set("match_rotation", self.match_rotation_cb.isChecked())
        self.config.set("use_midpoint_keyframe", self.use_mid_cb.isChecked())
        self.config.set("dry_run", self.dry_run_cb.isChecked())
        self.config.set("debug_save_frames", self.debug_cb.isChecked())
        self.config.set("debug_output_dir", self.debug_dir_edit.text().strip())
        self.config.set("ecc_threshold", self.ecc_threshold_spin.value())
        apply_selection_to_config(
            self.config,
            mode=str(self.selection_mode_combo.currentData() or "auto"),
            color=self.selection_color_combo.currentText(),
            track_index=self.selection_track_spin.value(),
        )
        self.config.set("apply_via_fusion", self.fusion_cb.isChecked())
        self.config.set("perspective_match_enabled", self.perspective_cb.isChecked())
        self.config.set("auto_align_start_timecode", self.auto_tc_cb.isChecked())
        self.config.set("reference_start_timecode", self.ref_tc_edit.text().strip())
        self.config.save(USER_CONFIG_PATH)
        self._update_run_button_label()

    def _update_run_button_label(self) -> None:
        if self.dry_run_cb.isChecked():
            self.run_btn.setText("Analyze (Dry Run)")
        else:
            self.run_btn.setText("Analyze and Apply Transforms")

    def _browse_reference(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Offline Reference",
            str(Path.home()),
            "Video (*.mov *.mp4 *.mxf *.mkv *.avi);;All Files (*)",
        )
        if path:
            self.ref_path_edit.setText(path)

    def _append_log(self, text: str) -> None:
        self.log_view.append(text)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _set_busy(self, busy: bool) -> None:
        self.run_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.progress_bar.setVisible(busy)
        for w in (
            self.ref_path_edit,
            self.conform_path_edit,
            self.match_scale_cb,
            self.match_position_cb,
            self.match_rotation_cb,
            self.use_mid_cb,
            self.dry_run_cb,
            self.debug_cb,
            self.ecc_threshold_spin,
            self.selection_mode_combo,
            self.selection_color_combo,
            self.selection_track_spin,
            self.fusion_cb,
            self.perspective_cb,
            self.auto_tc_cb,
            self.ref_tc_edit,
        ):
            w.setEnabled(not busy)

    def _on_run(self) -> None:
        self._sync_ui_to_config()
        self.log_view.clear()
        self._append_log("Starting…")
        self._set_busy(True)
        self.status_label.setText("Running…")

        self._worker_thread = QThread()
        self._worker = PipelineWorker(self.pipeline)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.log_line.connect(self._on_worker_log)
        self._worker.finished_ok.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker_thread.start()

    def _on_cancel(self) -> None:
        if self._worker:
            self._worker.cancel()
        self.status_label.setText("Stopping after current clip…")
        self._append_log("Stop requested…")

    def _on_worker_log(self, text: str) -> None:
        self._append_log(text)
        if text.startswith("["):
            self.status_label.setText(text[:80])

    def _on_worker_finished(self) -> None:
        self._teardown_worker()
        self._set_busy(False)
        self.status_label.setText("Done")
        debug = resolve_debug_dir(self.config)
        if debug:
            self._append_log(f"Debug images: {debug}")

    def _on_worker_failed(self, detail: str) -> None:
        self._teardown_worker()
        self._set_busy(False)
        self.status_label.setText("Error")
        self._append_log(f"ERROR: {detail}")
        QMessageBox.critical(self, "MatchRef failed", detail)

    def _teardown_worker(self) -> None:
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait(5000)
            self._worker_thread = None
        self._worker = None

    def _open_debug_folder(self) -> None:
        self._sync_ui_to_config()
        path = resolve_debug_dir(self.config) if self.debug_cb.isChecked() else None
        if path is None:
            raw = self.debug_dir_edit.text().strip()
            path = Path(raw).expanduser() if raw else Path.home() / "Documents" / "matchref" / "debug"
        path.mkdir(parents=True, exist_ok=True)
        _open_in_file_manager(path)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker_thread is not None and self._worker_thread.isRunning():
            if self._worker:
                self._worker.cancel()
            self._teardown_worker()
        super().closeEvent(event)


def run_gui(config_path: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    cfg = AppConfig(Path(config_path)) if config_path else AppConfig()
    window = MatchRefWindow(cfg)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_gui())
