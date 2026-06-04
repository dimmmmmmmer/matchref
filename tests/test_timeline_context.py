"""Shared test timeline (not in product config)."""

from matchref.timeline_context import TimelineContext


def make_timeline() -> TimelineContext:
    """Synthetic Resolve timeline for unit tests only."""
    return TimelineContext(fps=24.0, width=1920, height=1080)
