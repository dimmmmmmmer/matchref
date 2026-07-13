"""Shared state for the clip analysis stages (contexts + analyzer service surface)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from matchref.config import AppConfig
from matchref.conform_index import ConformIndex
from matchref.frame_provider import FrameProvider
from matchref.models import ClipAnalysisResult, TransformSample
from matchref.timeline_context import TimelineContext


@dataclass
class _ClipContext:
    """Per-clip state shared across the analyze stages (setup → samples → finalize)."""

    timeline_item: Any
    result: ClipAnalysisResult
    baseline: Any
    media_path: str
    canvas_size: tuple[int, int]
    control_points: list[Any]
    stop_on_confident: bool
    confident_score: float
    min_scale_apply: float
    max_scale_apply: float
    match_base_scale: float
    match_base_center: tuple[float, float]
    match_base_angle: float
    transform_vectors: list[tuple[float, float, float, float]] = field(default_factory=list)
    # Samples whose refine was rejected but whose direct ECC alignment is plausible.
    # Used to rescue low-structure clips (smoke/sky) where the ECC zoom is reliable
    # and corroborated across frames but the refine search and structure gate are not.
    ecc_candidates: list[TransformSample] = field(default_factory=list)


@dataclass
class _SampleFrames:
    """Frames + bookkeeping for one sample, produced by `_load_sample`.

    ``terminal`` means the sample already failed (frame missing / no conform match)
    and was appended to the result — the caller should not attempt matching.
    """

    sample: TransformSample
    terminal: bool
    online: Any = None
    offline: Any = None
    online_raw: Any = None
    compensate: bool = False
    mapping: Any = None
    compare_line: str = ""
    clamped_src: int = 0
    absolute: bool = False
    # Alignment-derived thresholds, filled in by _process_sample once the sample
    # is aligned; carried here so the reject/debug helpers don't re-derive them.
    admit: float = 0.0
    apply_floor: float = 0.0
    # The rejected first refine attempt (outcome + reasons), kept so the
    # escalation retry can decide whether widened limits could rescue it.
    first_refine: Any = None
    first_reasons: list[str] = field(default_factory=list)


class AnalyzerCore:
    """Attribute/service surface the analyzer mixins rely on.

    ``TransformAnalyzer.__init__`` populates every attribute below; the
    annotations let the sampling/debug mixins type-check standalone.
    """

    config: AppConfig
    logger: logging.Logger
    resolve: Any
    timeline_ctx: TimelineContext
    conform: ConformIndex
    frames: FrameProvider
    _variance_threshold: float
    _sample_n: int
    _debug_dir: Path | None
    _batch_ready: bool

    def _timeline_canvas_size(self) -> tuple[int, int]:
        return self.timeline_ctx.resolution
