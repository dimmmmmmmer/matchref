"""Video I/O against tiny generated clips: frame_read, media_probe, FrameProvider."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from matchref.config import AppConfig
from matchref.frame_provider import FrameProvider
from matchref.frame_read import index_after_read, read_frame_at_index, seek_to_frame
from matchref.media_probe import probe_video

_FPS = 25.0
_W, _H = 64, 36
_FRAMES = 12


def _write_clip(path: Path, frames: int = _FRAMES) -> str:
    # Each frame is a flat gray level equal to its index * 16, so the decoded
    # frame identifies itself even through lossy encoding.
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), _FPS, (_W, _H))
    if not writer.isOpened():
        pytest.skip("mp4v encoder unavailable in this OpenCV build")
    for i in range(frames):
        frame = np.full((_H, _W, 3), i * 16, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return str(path)


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> str:
    return _write_clip(tmp_path_factory.mktemp("media") / "clip.mp4")


def _frame_level(frame: np.ndarray) -> int:
    return round(float(frame.mean()) / 16.0)


# ---- media_probe ------------------------------------------------------------


def test_probe_video_reads_real_clip(clip) -> None:
    probe = probe_video(clip)
    assert probe.fps == _FPS
    assert probe.resolution == (_W, _H)
    assert probe.frame_count == _FRAMES


def test_probe_video_missing_file_is_empty(tmp_path) -> None:
    probe = probe_video(tmp_path / "missing.mp4")
    assert probe.fps == 0.0
    assert probe.frame_count == 0


# ---- frame_read -------------------------------------------------------------


def test_read_frame_at_index_identifies_frames(clip) -> None:
    cap = cv2.VideoCapture(clip)
    try:
        for target in (0, 5, 11):
            ok, frame, decoded = read_frame_at_index(cap, target, _FPS)
            assert ok
            assert decoded == target
            assert _frame_level(frame) == target
    finally:
        cap.release()


def test_read_frame_backward_seek(clip) -> None:
    cap = cv2.VideoCapture(clip)
    try:
        read_frame_at_index(cap, 8, _FPS)
        ok, frame, decoded = read_frame_at_index(cap, 2, _FPS)
        assert ok
        assert decoded == 2
        assert _frame_level(frame) == 2
    finally:
        cap.release()


def test_read_frame_past_end_fails(clip) -> None:
    cap = cv2.VideoCapture(clip)
    try:
        ok, frame, decoded = read_frame_at_index(cap, _FRAMES + 10, _FPS)
        assert not ok
        assert frame is None
        assert decoded == -1
    finally:
        cap.release()


def test_seek_modes_land_on_target(clip) -> None:
    cap = cv2.VideoCapture(clip)
    try:
        for mode in ("refine", "msec", "frames"):
            seek_to_frame(cap, 4, _FPS, mode=mode)
            ok, frame = cap.read()
            assert ok
            assert _frame_level(frame) == 4
    finally:
        cap.release()


def test_seek_frames_mode_without_fps(clip) -> None:
    cap = cv2.VideoCapture(clip)
    try:
        seek_to_frame(cap, 3, 0.0)  # fps unknown → falls back to frame seek
        ok, frame = cap.read()
        assert ok
        assert _frame_level(frame) == 3
        assert index_after_read(cap) == 3
    finally:
        cap.release()


# ---- FrameProvider ----------------------------------------------------------


def _provider() -> FrameProvider:
    return FrameProvider(AppConfig())


def test_provider_online_frame_and_count(clip) -> None:
    provider = _provider()
    try:
        frame = provider.get_online_frame(clip, 6)
        assert frame is not None
        assert _frame_level(frame) == 6
        assert provider.get_media_frame_count(clip) == _FRAMES
    finally:
        provider.close()


def test_provider_clamps_source_frame(clip) -> None:
    provider = _provider()
    try:
        assert provider.clamp_source_frame(clip, -5) == 0
        assert provider.clamp_source_frame(clip, 500) == _FRAMES - 1
        assert provider.clamp_source_frame(clip, 7) == 7
    finally:
        provider.close()


def test_provider_missing_media_returns_none(tmp_path) -> None:
    provider = _provider()
    try:
        assert provider.get_online_frame(str(tmp_path / "gone.mp4"), 0) is None
        assert provider.get_media_frame_count(str(tmp_path / "gone.mp4")) == 0
    finally:
        provider.close()


def test_provider_offline_reference_roundtrip(clip) -> None:
    provider = _provider()
    try:
        provider.set_offline_reference(clip, timeline_fps=_FPS)
        assert provider.get_offline_frame_count() == _FRAMES
        frame, reported = provider.get_offline_frame_at_index_with_meta(9)
        assert frame is not None
        assert reported == 9
        assert _frame_level(frame) == 9
        assert provider.clamp_offline_frame(100) == _FRAMES - 1
        assert provider.get_offline_frame_at_index(100) is not None  # clamped read
    finally:
        provider.close()


def test_provider_missing_offline_reference_raises(tmp_path) -> None:
    provider = _provider()
    try:
        with pytest.raises((FileNotFoundError, RuntimeError)):
            provider.set_offline_reference(str(tmp_path / "gone.mp4"), timeline_fps=_FPS)
    finally:
        provider.close()


def test_provider_release_offline_allows_reload(clip) -> None:
    provider = _provider()
    try:
        provider.set_offline_reference(clip, timeline_fps=_FPS)
        provider.release_offline()
        assert provider.get_offline_frame_at_index(0) is None
        provider.set_offline_reference(clip, timeline_fps=_FPS)
        assert provider.get_offline_frame_at_index(0) is not None
    finally:
        provider.close()
