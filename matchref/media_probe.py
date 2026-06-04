"""Probe video files (fps, resolution) via OpenCV."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2

from matchref.fps import normalize_fps


@dataclass(frozen=True)
class VideoProbe:
    path: str
    fps: float = 0.0
    width: int = 0
    height: int = 0
    frame_count: int = 0

    @property
    def resolution(self) -> tuple[int, int]:
        return self.width, self.height


def probe_video(path: str | Path) -> VideoProbe:
    path = str(path)
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return VideoProbe(path=path)
    try:
        raw_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        fps = normalize_fps(raw_fps) if raw_fps > 0 else 0.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        return VideoProbe(path=path, fps=fps, width=width, height=height, frame_count=count)
    finally:
        cap.release()
