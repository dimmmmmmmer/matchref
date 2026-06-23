"""DaVinci Resolve timeline as the single source for FPS and resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from matchref.config import AppConfig
from matchref.resolve_api import get_timeline_media_info


class TimelineContextError(RuntimeError):
    """Timeline FPS or resolution unavailable from Resolve."""


@dataclass(frozen=True)
class TimelineContext:
    """Hub: current Resolve timeline playback rate and raster size."""

    fps: float
    width: int
    height: int
    drop_frame: bool = False
    start_timecode: str = ""

    @classmethod
    def from_resolve(cls, timeline: Any) -> TimelineContext:
        info = get_timeline_media_info(timeline)
        if info.fps <= 0:
            raise TimelineContextError(
                "Cannot read timeline frame rate from DaVinci Resolve. "
                "Open the correct timeline and retry."
            )
        if info.width <= 0 or info.height <= 0:
            raise TimelineContextError(
                "Cannot read timeline resolution from DaVinci Resolve."
            )
        return cls(
            fps=info.fps,
            width=info.width,
            height=info.height,
            drop_frame=info.drop_frame,
            start_timecode=info.start_timecode,
        )

    @property
    def resolution(self) -> tuple[int, int]:
        return self.width, self.height

    def analysis_max_width(self, config: AppConfig) -> int:
        """ECC downscale cap: 0 in config = use full timeline width."""
        cap = int(config.get("analysis_max_width", 0))
        return cap if cap > 0 else self.width
