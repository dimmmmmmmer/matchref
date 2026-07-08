"""Final report formatting and logging setup."""

from __future__ import annotations

from matchref.logging_report import format_report, setup_logging
from matchref.models import AnalysisReport, ClipAnalysisResult, SamplePoint, TransformSample


def _clip(name: str, *, ok: bool, problem: bool, warning: str = "") -> ClipAnalysisResult:
    r = ClipAnalysisResult(
        clip_name=name,
        timeline_item_id=name,
        track_type="video",
        track_index=1,
        duration_frames=100,
    )
    s = TransformSample(sample_point=SamplePoint.MID, clip_local_frame=50, timeline_frame=50)
    s.ok = ok
    s.scale = 1.25
    r.samples.append(s)
    r.problem = problem
    if warning:
        r.warnings.append(warning)
    return r


def test_report_marks_ready_and_problem_and_summary() -> None:
    report = AnalysisReport()
    report.results.append(_clip("good", ok=True, problem=False))
    report.results.append(_clip("bad", ok=False, problem=True, warning="samples disagree"))
    report.skipped.append("ref.mov: same file as offline reference")

    text = format_report(report)

    assert "[READY] good" in text
    assert "[PROBLEM] bad" in text
    assert "! samples disagree" in text
    assert "ref.mov: same file as offline reference" in text
    assert "Summary: 1 ready, 1 problem, 1 skipped" in text


def test_empty_report_summary() -> None:
    text = format_report(AnalysisReport())
    assert "Summary: 0 ready, 0 problem, 0 skipped" in text


def test_setup_logging_writes_to_file(tmp_path) -> None:
    log_file = tmp_path / "matchref.log"
    logger = setup_logging(str(log_file))
    try:
        logger.info("hello-marker")
        for handler in logger.handlers:
            handler.flush()
        assert log_file.exists()
        assert "hello-marker" in log_file.read_text(encoding="utf-8")
    finally:
        setup_logging("")  # restore console-only logger for other tests
