"""Structured logging and final report generation."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from matchref.models import AnalysisReport, TransformSample


def setup_logging(log_file: str = "") -> logging.Logger:
    logger = logging.getLogger("matchref")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def log_sample(
    logger: logging.Logger,
    clip_name: str,
    sample: TransformSample,
) -> None:
    logger.info(
        "%s | local=%s | offline=%s (%s) | reel=%s | src=%s | scale=%.4f | "
        "pos_x=%.4f | pos_y=%.4f | rot=%.2f | ecc=%.4f | %s",
        clip_name,
        sample.clip_local_frame,
        sample.offline_frame,
        sample.offline_mapping,
        sample.reel_name,
        sample.source_frame,
        sample.scale,
        sample.position_x,
        sample.position_y,
        sample.rotation_deg,
        sample.ecc_score,
        "OK" if sample.ok else f"FAIL ({sample.message})",
    )


def format_report(report: AnalysisReport) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"MatchRef Analysis Report — {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("=" * 72)

    for result in report.results:
        status = "READY" if result.is_valid else "PROBLEM"
        lines.append("")
        lines.append(
            f"[{status}] {result.clip_name} (track {result.track_index}, {result.duration_frames}f)"
        )
        if result.warnings:
            for warning in result.warnings:
                lines.append(f"  ! {warning}")
        for sample in result.samples:
            lines.append(
                f"  {sample.sample_point.value:5s} local={sample.clip_local_frame:5d} "
                f"offline={sample.offline_frame:6d} ({sample.offline_mapping}) "
                f"reel={sample.reel_name!r} src={sample.source_frame:6d} "
                f"scale={sample.scale:7.4f} pos=({sample.position_x:7.4f},{sample.position_y:7.4f}) "
                f"rot={sample.rotation_deg:7.2f} ecc={sample.ecc_score:.4f}"
            )

    if report.skipped:
        lines.append("")
        lines.append("Skipped:")
        for item in report.skipped:
            lines.append(f"  - {item}")

    ready = len(report.ready_clips)
    problems = len(report.problem_clips)
    lines.append("")
    lines.append(f"Summary: {ready} ready, {problems} problem, {len(report.skipped)} skipped")
    lines.append("=" * 72)
    return "\n".join(lines)


def log_final_report(logger: logging.Logger, report: AnalysisReport) -> str:
    text = format_report(report)
    for line in text.splitlines():
        logger.info(line)
    return text
