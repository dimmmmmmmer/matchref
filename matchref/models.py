"""Shared data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class SamplePoint(str, Enum):
    START = "start"
    MID = "mid"
    END = "end"


class OfflineMappingSource(str, Enum):
    EDL = "edl"
    XML = "xml"
    TIMELINE = "timeline"
    NONE = "none"


@dataclass
class TransformSample:
    """Transform measured at one control frame."""

    sample_point: SamplePoint
    clip_local_frame: int
    timeline_frame: int
    offline_frame: int = 0
    offline_mapping: str = "timeline"
    reel_name: str = ""
    source_frame: int = 0
    scale: float = 1.0
    position_x: float = 0.0
    position_y: float = 0.0
    rotation_deg: float = 0.0
    ecc_score: float = 0.0
    ok: bool = False
    message: str = ""
    warp_matrix: Any = None
    refined_edit: Any = None  # ClipEditTransform when Resolve Edit search used


@dataclass
class ClipAnalysisResult:
    """Analysis output for a single timeline clip."""

    clip_name: str
    timeline_item_id: str
    track_type: str
    track_index: int
    duration_frames: int
    samples: list[TransformSample] = field(default_factory=list)
    problem: bool = False
    warnings: list[str] = field(default_factory=list)
    use_mid_key: bool = True
    timeline_item: Any = None
    baseline_zoom_x: float = 1.0
    baseline_zoom_y: float = 1.0
    baseline_pan: float = 0.0
    baseline_tilt: float = 0.0
    baseline_rotation: float = 0.0

    @property
    def is_valid(self) -> bool:
        return bool(self.samples) and any(s.ok for s in self.samples) and not self.problem

    def min_ecc_score(self) -> float:
        if not self.samples:
            return 0.0
        return min(s.ecc_score for s in self.samples)


@dataclass
class AnalysisReport:
    """Batch analysis summary."""

    results: list[ClipAnalysisResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def problem_clips(self) -> list[ClipAnalysisResult]:
        return [r for r in self.results if r.problem or not r.is_valid]

    @property
    def ready_clips(self) -> list[ClipAnalysisResult]:
        return [r for r in self.results if r.is_valid]
