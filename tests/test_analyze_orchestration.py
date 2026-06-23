"""Driver-level orchestration of TransformAnalyzer._analyze_single.

These lock in the post-refactor sequencing (begin → per-sample loop → finalize)
without needing a live DaVinci Resolve: the four stage methods are stubbed.
"""

from __future__ import annotations

from types import SimpleNamespace

from matchref.transform_analysis import TransformAnalyzer


def _bare_analyzer() -> TransformAnalyzer:
    # Skip __init__ (it needs Resolve/conform/frame providers); the driver only
    # calls the four stage methods, which we stub per-instance below.
    return TransformAnalyzer.__new__(TransformAnalyzer)


def test_begin_returns_none_skips_clip() -> None:
    analyzer = _bare_analyzer()
    processed: list[object] = []
    analyzer._begin_clip = lambda item: None  # type: ignore[method-assign]
    analyzer._process_sample = lambda *a: processed.append(a)  # type: ignore[method-assign]
    analyzer._should_stop_after = lambda *a: False  # type: ignore[method-assign]
    analyzer._finalize_clip = lambda ctx: processed.append("finalize")  # type: ignore[method-assign]

    assert analyzer._analyze_single(object()) is None
    assert processed == []  # neither sampling nor finalize ran


def test_processes_every_control_point_then_finalizes() -> None:
    analyzer = _bare_analyzer()
    ctx = SimpleNamespace(
        control_points=[("a", 0), ("b", 12), ("c", 24)],
        result="RESULT",
    )
    seen: list[object] = []
    analyzer._begin_clip = lambda item: ctx  # type: ignore[method-assign]
    analyzer._process_sample = lambda c, p, f: seen.append((p, f)) or p  # type: ignore[method-assign]
    analyzer._should_stop_after = lambda c, s: False  # type: ignore[method-assign]
    finalized: list[object] = []
    analyzer._finalize_clip = lambda c: finalized.append(c)  # type: ignore[method-assign]

    result = analyzer._analyze_single(object())
    assert seen == [("a", 0), ("b", 12), ("c", 24)]
    assert finalized == [ctx]
    assert result == "RESULT"


def test_stop_after_short_circuits_remaining_samples() -> None:
    analyzer = _bare_analyzer()
    ctx = SimpleNamespace(control_points=[("a", 0), ("b", 12), ("c", 24)], result="R")
    seen: list[object] = []
    finalized: list[object] = []
    analyzer._begin_clip = lambda item: ctx  # type: ignore[method-assign]
    analyzer._process_sample = lambda c, p, f: seen.append(p) or p  # type: ignore[method-assign]
    analyzer._should_stop_after = lambda c, s: s == "a"  # stop right after first  # type: ignore[method-assign]
    analyzer._finalize_clip = lambda c: finalized.append(c)  # type: ignore[method-assign]

    analyzer._analyze_single(object())
    assert seen == ["a"]  # b and c never processed
    assert finalized == [ctx]  # finalize still runs
