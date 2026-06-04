"""Frame extraction from online media and offline reference."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from matchref.config import AppConfig
from matchref.conform_index import OfflineFrameMapping, OfflineFrameResolver
from matchref.frame_read import SeekMode, read_frame_at_index
from matchref.media_probe import probe_video
from matchref.models import SamplePoint


class FrameProvider:
    """Read frames via OpenCV from clip media and offline reference."""

    def __init__(
        self,
        config: AppConfig,
        offline_resolver: OfflineFrameResolver | None = None,
    ) -> None:
        self.config = config
        self.offline_resolver = offline_resolver or OfflineFrameResolver(config)
        self._offline_cap: cv2.VideoCapture | None = None
        self._offline_path: str = ""
        self._offline_fps: float = 0.0
        # LRU of open source decoders. Unbounded growth here leaks hundreds of MB
        # per file across a large batch (each 6K VideoCapture holds native decoder
        # buffers); clips are processed sequentially using one source at a time, so
        # a tiny cache is enough.
        self._media_cache: OrderedDict[str, cv2.VideoCapture] = OrderedDict()
        self._media_cache_size = max(1, int(config.get("media_cache_size", 2)))
        self._media_fps: dict[str, float] = {}

    def set_offline_reference(self, path: str, *, timeline_fps: float = 0.0) -> None:
        path = str(path).strip()
        if path == self._offline_path and self._offline_cap is not None:
            return
        self.release_offline()
        if not path:
            return
        if not Path(path).is_file():
            raise FileNotFoundError(f"Offline reference not found: {path}")
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open offline reference: {path}")
        probe = probe_video(path)
        self._offline_fps = probe.fps or float(timeline_fps) or 0.0
        self._offline_cap = cap
        self._offline_path = path

    def release_offline(self) -> None:
        if self._offline_cap is not None:
            self._offline_cap.release()
            self._offline_cap = None
        self._offline_path = ""
        self._offline_fps = 0.0

    def _seek_mode(self) -> SeekMode:
        raw = str(self.config.get("video_seek_mode", "refine")).lower()
        if raw in ("msec", "frames", "refine"):
            return raw  # type: ignore[return-value]
        return "refine"

    def _fps_for_offline(self) -> float:
        return self._offline_fps or float(self.config.get("hub_fps_fallback", 24.0))

    def _fps_for_media(self, media_path: str) -> float:
        if media_path in self._media_fps and self._media_fps[media_path] > 0:
            return self._media_fps[media_path]
        probe = probe_video(media_path)
        if probe.fps > 0:
            self._media_fps[media_path] = probe.fps
            return probe.fps
        return float(self.config.get("hub_fps_fallback", 24.0))

    def close(self) -> None:
        self.release_offline()
        for cap in self._media_cache.values():
            cap.release()
        self._media_cache.clear()
        self._media_fps.clear()

    def get_offline_frame_at_index(self, frame_index: int) -> np.ndarray | None:
        frame, _ = self.get_offline_frame_at_index_with_meta(frame_index)
        return frame

    def get_offline_frame_at_index_with_meta(
        self, frame_index: int
    ) -> tuple[np.ndarray | None, int]:
        """Return (frame, reported_index_before_read)."""
        if self._offline_cap is None:
            return None, -1
        frame_index = self.clamp_offline_frame(frame_index)
        offset = int(self.config.get("video_decode_index_offset", 0))
        ok, frame, reported = read_frame_at_index(
            self._offline_cap,
            frame_index,
            self._fps_for_offline(),
            mode=self._seek_mode(),
            index_offset=offset,
        )
        if not ok or frame is None:
            return None, reported
        return frame, reported

    def get_offline_frame(self, timeline_frame: int) -> np.ndarray | None:
        """Legacy: timeline-frame lookup with config offset only."""
        offset = int(self.config.get("offline_timeline_offset_frames", 0))
        return self.get_offline_frame_at_index(int(timeline_frame) + offset)

    def get_offline_frame_mapped(
        self,
        mapping: OfflineFrameMapping,
    ) -> np.ndarray | None:
        return self.get_offline_frame_at_index(mapping.offline_frame)

    def get_media_frame_count(self, media_path: str) -> int:
        cap = self._open_media(media_path)
        if cap is None:
            return 0
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        return max(0, count)

    def clamp_source_frame(self, media_path: str, source_frame: int) -> int:
        count = self.get_media_frame_count(media_path)
        if count <= 0:
            return max(0, int(source_frame))
        return min(max(0, int(source_frame)), count - 1)

    def get_offline_frame_count(self) -> int:
        if self._offline_cap is None:
            return 0
        return max(0, int(self._offline_cap.get(cv2.CAP_PROP_FRAME_COUNT)))

    def clamp_offline_frame(self, frame_index: int) -> int:
        count = self.get_offline_frame_count()
        if count <= 0:
            return max(0, int(frame_index))
        return min(max(0, int(frame_index)), count - 1)

    def get_online_frame(self, media_path: str, source_frame: int) -> np.ndarray | None:
        cap = self._open_media(media_path)
        if cap is None:
            return None
        frame_index = self.clamp_source_frame(media_path, source_frame)
        offset = int(self.config.get("video_decode_index_offset", 0))
        ok, frame, _ = read_frame_at_index(
            cap,
            frame_index,
            self._fps_for_media(media_path),
            mode=self._seek_mode(),
            index_offset=offset,
        )
        if not ok or frame is None:
            return None
        return frame

    def _open_media(self, media_path: str) -> cv2.VideoCapture | None:
        if not media_path or not Path(media_path).is_file():
            return None
        cached = self._media_cache.get(media_path)
        if cached is not None:
            self._media_cache.move_to_end(media_path)
            return cached
        cap = cv2.VideoCapture(media_path)
        if not cap.isOpened():
            return None
        self._media_cache[media_path] = cap
        probe = probe_video(media_path)
        if probe.fps > 0:
            self._media_fps[media_path] = probe.fps
        # Evict and release the least-recently-used decoders beyond the cap.
        while len(self._media_cache) > self._media_cache_size:
            _, old = self._media_cache.popitem(last=False)
            try:
                old.release()
            except Exception:
                pass
        return cap


def sample_points_for_clip(
    duration_frames: int,
    sample_every_n: int = 0,
    *,
    config: AppConfig | None = None,
) -> list[tuple[SamplePoint, int]]:
    """
    Control frames inside clip (0-based local).

    match_sample_mode:
      - single: one frame (match_sample_point: start | mid | end)
      - three: start, mid, end
      - every_n: use sample_every_n_frames
    """
    duration = max(1, int(duration_frames))
    # round(duration/2), half-up, clamped to a valid local index [0, duration-1].
    # Keeps the sampled mid frame at the true clip centre regardless of even/odd length.
    mid = min(max(0, (duration + 1) // 2), duration - 1)
    mode = "three"
    if config is not None:
        mode = str(config.get("match_sample_mode", "single")).lower()
    if mode == "single":
        which = str(config.get("match_sample_point", "mid") if config else "mid").lower()
        if which == "start":
            local = 0
            point = SamplePoint.START
        elif which == "end":
            local = max(0, duration - 1)
            point = SamplePoint.END
        else:
            local = mid
            point = SamplePoint.MID
        return [(point, local)]

    if sample_every_n and sample_every_n > 0:
        indices = list(range(0, duration, sample_every_n))
        if indices[-1] != duration - 1:
            indices.append(duration - 1)
        points: list[tuple[SamplePoint, int]] = []
        for idx in indices:
            if idx == 0:
                points.append((SamplePoint.START, idx))
            elif idx == duration - 1:
                points.append((SamplePoint.END, idx))
            else:
                points.append((SamplePoint.MID, idx))
        return points

    end = max(0, duration - 1)
    return [
        (SamplePoint.START, 0),
        (SamplePoint.MID, mid),
        (SamplePoint.END, end),
    ]


def resolve_media_path(media_pool_item: Any) -> str:
    """Best-effort file path from MediaPoolItem."""
    if media_pool_item is None:
        return ""
    for prop in ("File Path", "FilePath", "filepath"):
        try:
            value = media_pool_item.GetClipProperty(prop)
            if value:
                return str(value)
        except Exception:
            continue
    try:
        props = media_pool_item.GetClipProperty()
        if isinstance(props, dict):
            for key in ("File Path", "FilePath", "filepath"):
                if props.get(key):
                    return str(props[key])
    except Exception:
        pass
    return ""


def source_frame_for_local(
    timeline_item: Any,
    local_frame: int,
    *,
    config: AppConfig | None = None,
) -> int:
    """Map clip-local frame to source media frame.

    Without retime the timeline advances 1:1 with the source, so
    ``source_start + local_frame`` is exact. Retimed clips (slow/fast motion,
    reverse) break that assumption: the source in/out span no longer equals the
    timeline duration. When ``retime_aware_source_mapping`` is enabled we
    linearly interpolate the source frame from the clip's source span versus its
    timeline duration, which is exact for constant-speed retimes (including
    reverse) and a reasonable approximation for speed ramps.
    """
    try:
        source_start = int(timeline_item.GetSourceStartFrame())
    except Exception:
        source_start = 0

    local = int(local_frame)
    enabled = True if config is None else bool(config.get("retime_aware_source_mapping", True))
    if not enabled:
        return source_start + local

    try:
        source_end = int(timeline_item.GetSourceEndFrame())
    except Exception:
        return source_start + local
    try:
        duration = int(timeline_item.GetDuration())
    except Exception:
        duration = 0

    if duration <= 1:
        return source_start
    src_span = source_end - source_start
    # No retime (or span unavailable): identical to the 1:1 mapping. Tolerate a
    # 1-frame difference so we don't misread Resolve's inclusive-vs-exclusive
    # GetSourceEndFrame convention as a tiny retime.
    if src_span == 0 or abs(src_span - (duration - 1)) <= 1:
        return source_start + local
    ratio = src_span / float(duration - 1)
    return source_start + int(round(local * ratio))


def timeline_frame_for_local(timeline_item: Any, local_frame: int) -> int:
    try:
        clip_start = int(timeline_item.GetStart())
    except Exception:
        clip_start = 0
    return clip_start + int(local_frame)
