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
        clips, source = self._target_clips()
        dry_run = bool(self.config.get("dry_run", False))
        self._emit_run_header(on_message, len(clips), source)
        analyzer, applier = self._build_workers(dry_run)

        report = AnalysisReport()

        try:
            filtered, skipped = analyzer.prepare_batch(clips)
            report.skipped.extend(skipped)
            total = len(filtered)
            if total == 0 and report.skipped:
                self._emit_all_skipped(on_message, report.skipped)
            self._emit(on_message, f"Processing {total} clip(s)…")

            for index, item in enumerate(filtered, start=1):
                if should_cancel and should_cancel():
                    self._emit(on_message, "Stopped by user.")
                    break
                if not self._process_clip(
                    on_message,
                    should_cancel,
                    analyzer,
                    applier,
                    report,
                    item,
                    index,
                    total,
                    dry_run,
                ):
                    self._emit(on_message, "Stopped by user.")
                    break

        finally:
            analyzer.close()

        ready = len(report.ready_clips)
        problems = len(report.problem_clips)
        self._emit(
            on_message,
            f"Finished: {ready} ready, {problems} problem, {len(report.skipped)} skipped.",
        )
        log_final_report(self.logger, report)
        return report

    def _target_clips(self) -> tuple[list[Any], str]:
        """Connect (if needed) and resolve the clip selection; raises when empty."""
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
        return clips, source

    def _build_workers(
        self, dry_run: bool
    ) -> tuple[TransformAnalyzer, EditTransformApplier | None]:
        """Analyzer + (unless dry run) applier bound to the connected timeline."""
        assert self.timeline_ctx is not None
        assert self.resolve is not None
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
        return analyzer, applier

    def _emit_run_header(
        self, on_message: Callable[[str], None] | None, clip_count: int, source: str
    ) -> None:
        """Describe the run (clip count, hub format, debug dir, conform warning)."""
        assert self.timeline_ctx is not None
        self._emit(on_message, f"Clips: {clip_count} ({source})")
        self._emit(
            on_message,
            f"Timeline hub: {self.timeline_ctx.width}x{self.timeline_ctx.height} @ "
            f"{self.timeline_ctx.fps:.3f} fps",
        )
        debug_dir = resolve_debug_dir(self.config)
        if debug_dir:
            self._emit(on_message, f"Debug folder: {debug_dir}")
        xml = str(self.config.get("conform_xml_path", "")).strip()
        edl = str(self.config.get("conform_edl_path", "")).strip()
        if not xml and not edl:
            self._emit(
                on_message,
                "WARNING: no Conform XML/EDL — frame mapping may be wrong. Set conform file in GUI.",
            )

    def _emit_all_skipped(
        self, on_message: Callable[[str], None] | None, skipped: list[str]
    ) -> None:
        self._emit(
            on_message,
            f"All {len(skipped)} selected clip(s) were skipped — nothing to process. Reasons:",
        )
        for reason in skipped[:10]:
            self._emit(on_message, f"  • {reason}")

    def _process_clip(
        self,
        on_message: Callable[[str], None] | None,
        should_cancel: Callable[[], bool] | None,
        analyzer: TransformAnalyzer,
        applier: EditTransformApplier | None,
        report: AnalysisReport,
        item: Any,
        index: int,
        total: int,
        dry_run: bool,
    ) -> bool:
        """Analyze one clip, record it, and apply when valid. False = cancelled mid-clip."""
        name = _clip_name(item)
        self._emit(on_message, f"[{index}/{total}] Analyzing: {name}")

        result = analyzer.analyze_one(item, should_cancel=should_cancel)
        if should_cancel and should_cancel():
            return False
        if result is None:
            self._emit(on_message, "  Skipped (no media).")
            return True

        report.results.append(result)
        self._summarize_clip(on_message, result)

        if dry_run:
            return True

        if result.is_valid and applier is not None:
            self._emit(on_message, "  Applying transform…")
            try:
                applier.apply_clip(result)
                self._emit(on_message, f"  Applied: {name}")
            except Exception as exc:  # noqa: BLE001
                self._emit(on_message, f"  Apply failed: {exc}")
        elif result.problem:
            self._emit(on_message, "  Not applied (problem clip).")
        return True

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
