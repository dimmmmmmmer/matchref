"""Pipeline orchestration (analyze → apply) on stubs — no DaVinci Resolve."""

from __future__ import annotations

import logging

import matchref.pipeline as pipeline_mod
import matchref.selection as selection_mod
from matchref.config import AppConfig
from matchref.models import ClipAnalysisResult, SamplePoint, TransformSample
from matchref.pipeline import MatchRefPipeline
from matchref.timeline_context import TimelineContext


class _Clip:
    def __init__(self, name: str) -> None:
        self._name = name

    def GetName(self) -> str:  # noqa: N802 — Resolve API name
        return self._name


def _result(name: str, *, ok: bool, problem: bool) -> ClipAnalysisResult:
    r = ClipAnalysisResult(
        clip_name=name,
        timeline_item_id=name,
        track_type="video",
        track_index=1,
        duration_frames=10,
    )
    sample = TransformSample(sample_point=SamplePoint.MID, clip_local_frame=5, timeline_frame=5)
    sample.ok = ok
    r.samples.append(sample)
    r.problem = problem
    return r


class _StubAnalyzer:
    last: _StubAnalyzer | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.closed = False
        _StubAnalyzer.last = self

    def prepare_batch(self, clips: list[_Clip]) -> tuple[list[_Clip], list[str]]:
        return clips, ["skipped-because-reference"]

    def analyze_one(self, item: _Clip, should_cancel=None) -> ClipAnalysisResult | None:
        name = item.GetName()
        if name == "no-media":
            return None
        return _result(name, ok=name != "problem", problem=name == "problem")

    def close(self) -> None:
        self.closed = True


class _StubApplier:
    applied: list[str] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def apply_clip(self, result: ClipAnalysisResult) -> None:
        _StubApplier.applied.append(result.clip_name)


def _pipeline(config: AppConfig, monkeypatch, clips: list[_Clip]) -> MatchRefPipeline:
    _StubApplier.applied = []
    monkeypatch.setattr(
        selection_mod, "get_target_clips_with_source", lambda *a, **k: (clips, "stub")
    )
    monkeypatch.setattr(pipeline_mod, "TransformAnalyzer", _StubAnalyzer)
    monkeypatch.setattr(pipeline_mod, "EditTransformApplier", _StubApplier)
    pipe = MatchRefPipeline(config, logging.getLogger("test"))
    pipe.timeline = object()
    pipe.resolve = object()
    pipe.timeline_ctx = TimelineContext(fps=24.0, width=1920, height=1080)
    return pipe


def test_analyze_apply_flow(monkeypatch) -> None:
    clips = [_Clip("good"), _Clip("problem"), _Clip("no-media")]
    pipe = _pipeline(AppConfig(), monkeypatch, clips)

    report = pipe.run_selected()

    # no-media (None) is dropped; the other two are recorded.
    assert {r.clip_name for r in report.results} == {"good", "problem"}
    assert "skipped-because-reference" in report.skipped
    assert [r.clip_name for r in report.ready_clips] == ["good"]
    assert [r.clip_name for r in report.problem_clips] == ["problem"]
    # only the valid clip is applied, and the analyzer is closed.
    assert _StubApplier.applied == ["good"]
    assert _StubAnalyzer.last is not None and _StubAnalyzer.last.closed is True


def test_dry_run_analyzes_but_does_not_apply(monkeypatch) -> None:
    cfg = AppConfig()
    cfg.set("dry_run", True)
    pipe = _pipeline(cfg, monkeypatch, [_Clip("good")])

    report = pipe.run_selected()

    assert [r.clip_name for r in report.ready_clips] == ["good"]
    assert _StubApplier.applied == []  # dry run never applies


def test_cancellation_stops_before_processing(monkeypatch) -> None:
    pipe = _pipeline(AppConfig(), monkeypatch, [_Clip("good"), _Clip("good2")])

    report = pipe.run_selected(should_cancel=lambda: True)

    assert report.results == []
    assert _StubApplier.applied == []


def test_no_clips_raises(monkeypatch) -> None:
    pipe = _pipeline(AppConfig(), monkeypatch, [])
    try:
        pipe.run_selected()
    except RuntimeError as exc:
        assert "No clips" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError for an empty selection")


def test_all_skipped_surfaces_reasons(monkeypatch) -> None:
    class _AllSkipped(_StubAnalyzer):
        def prepare_batch(self, clips):
            return [], ["shotA: media offline (file not found on disk)"]

    pipe = _pipeline(AppConfig(), monkeypatch, [_Clip("shotA")])
    monkeypatch.setattr(pipeline_mod, "TransformAnalyzer", _AllSkipped)

    messages: list[str] = []
    report = pipe.run_selected(on_message=messages.append)

    assert report.results == []
    assert any("were skipped" in m for m in messages)
    assert any("media offline" in m for m in messages)
