"""High-level analyze/apply orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from matchref.config import AppConfig
from matchref.debug_frames import resolve_debug_dir
from matchref.edit_apply import EditTransformApplier
from matchref.logging_report import log_final_report
from matchref.models import AnalysisReport, ClipAnalysisResult
from matchref.resolve_api import connect_resolve, get_project_context
from matchref.timeline_context import TimelineContext
from matchref.transform_analysis import TransformAnalyzer, _clip_name


class MatchRefPipeline:
    def __init__(self, config: AppConfig, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("matchref")
        self.resolve: Any | None = None
        self.timeline: Any | None = None
        self.last_report: AnalysisReport | None = None
        self.last_apply_messages: list[str] = []
        self.timeline_ctx: TimelineContext | None = None

    def connect(self) -> None:
        self.resolve = connect_resolve()
        _, _, timeline = get_project_context(self.resolve)
        self.timeline = timeline
        self.timeline_ctx = TimelineContext.from_resolve(timeline)

    def _emit(self, on_message: Callable[[str], None] | None, text: str) -> None:
        self.logger.info(text)
        if on_message:
            on_message(text)

    def run_selected(
        self,
        on_message: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> AnalysisReport:
        """
        Analyze and optionally apply clip-by-clip with live status callbacks.
        """
        if self.timeline is None:
            self.connect()
        assert self.timeline is not None
        assert self.resolve is not None
        if self.timeline_ctx is None:
            self.timeline_ctx = TimelineContext.from_resolve(self.timeline)

        from matchref.selection import get_target_clips_with_source

        clips, source = get_target_clips_with_source(self.timeline, self.config, self.logger)
        if not clips:
            raise RuntimeError(
                "No clips to process. Set clip_selection_mode to 'all_filtered' in "
                "config/user_config.json, or flag all shots with Purple."
            )

        dry_run = bool(self.config.get("dry_run", False))
        debug_dir = resolve_debug_dir(self.config)
        self._emit(on_message, f"Clips: {len(clips)} ({source})")
        self._emit(
            on_message,
            f"Timeline hub: {self.timeline_ctx.width}x{self.timeline_ctx.height} @ "
            f"{self.timeline_ctx.fps:.3f} fps",
        )
        if debug_dir:
            self._emit(on_message, f"Debug folder: {debug_dir}")
        xml = str(self.config.get("conform_xml_path", "")).strip()
        edl = str(self.config.get("conform_edl_path", "")).strip()
        if not xml and not edl:
            self._emit(
                on_message,
                "WARNING: no Conform XML/EDL — frame mapping may be wrong. Set conform file in GUI.",
            )

        analyzer = TransformAnalyzer(
            self.config,
            self.timeline_ctx,
            self.logger,
            resolve=self.resolve,
        )
        applier: EditTransformApplier | None = None
        if not dry_run:
            applier = EditTransformApplier(
                self.resolve,
                self.config,
                self.timeline_ctx,
                self.logger,
            )

        report = AnalysisReport()
        self.last_apply_messages = []

        try:
            filtered, skipped = analyzer.prepare_batch(clips)
            report.skipped.extend(skipped)
            total = len(filtered)
            if total == 0 and report.skipped:
                self._emit(
                    on_message,
                    f"All {len(report.skipped)} selected clip(s) were skipped — nothing to "
                    "process. Reasons:",
                )
                for reason in report.skipped[:10]:
                    self._emit(on_message, f"  • {reason}")
            self._emit(on_message, f"Processing {total} clip(s)…")

            for index, item in enumerate(filtered, start=1):
                if should_cancel and should_cancel():
                    self._emit(on_message, "Stopped by user.")
                    break

                name = _clip_name(item)
                self._emit(on_message, f"[{index}/{total}] Analyzing: {name}")

                result = analyzer.analyze_one(item)
                if result is None:
                    self._emit(on_message, "  Skipped (no media).")
                    continue

                report.results.append(result)
                self._summarize_clip(on_message, result)

                if dry_run:
                    continue

                if result.is_valid and applier is not None:
                    self._emit(on_message, "  Applying transform…")
                    try:
                        applier.apply_clip(result)
                        msg = f"  Applied: {name}"
                        self.last_apply_messages.append(f"Applied (Edit): {name}")
                        self._emit(on_message, msg)
                    except Exception as exc:  # noqa: BLE001
                        err = f"  Apply failed: {exc}"
                        self.last_apply_messages.append(f"Failed {name}: {exc}")
                        self._emit(on_message, err)
                elif result.problem:
                    self._emit(on_message, "  Not applied (problem clip).")

        finally:
            analyzer.close()

        self.last_report = report
        ready = len(report.ready_clips)
        problems = len(report.problem_clips)
        self._emit(
            on_message,
            f"Finished: {ready} ready, {problems} problem, {len(report.skipped)} skipped.",
        )
        log_final_report(self.logger, report)
        return report

    def _summarize_clip(
        self,
        on_message: Callable[[str], None] | None,
        result: ClipAnalysisResult,
    ) -> None:
        ok = sum(1 for s in result.samples if s.ok)
        status = "OK" if result.is_valid else "PROBLEM"
        self._emit(on_message, f"  {status}: {ok}/{len(result.samples)} samples matched.")
        for sample in result.samples:
            if not sample.ok:
                self._emit(
                    on_message,
                    f"    {sample.sample_point.value}: {sample.message or 'failed'}",
                )
            elif bool(self.config.get("debug_save_frames", False)):
                self._emit(
                    on_message,
                    f"    {sample.sample_point.value}: offline #{sample.offline_frame} "
                    f"src #{sample.source_frame} scale={sample.scale:.3f}",
                )

    def analyze_selected(self) -> AnalysisReport:
        """Headless / legacy: run without UI callbacks."""
        return self.run_selected()

    def apply_transforms(self, report: AnalysisReport | None = None) -> list[str]:
        if self.resolve is None:
            self.connect()
        assert self.resolve is not None

        target_report = report or self.last_report
        if target_report is None:
            raise RuntimeError("Nothing to apply. Run analysis first.")

        if self.timeline_ctx is None:
            self.connect()
        assert self.timeline_ctx is not None
        applier = EditTransformApplier(
            self.resolve,
            self.config,
            self.timeline_ctx,
            self.logger,
        )
        return applier.apply_report(target_report)
