"""Keyframe-ramp apply path: fail loudly instead of writing a collapsed ramp."""

from __future__ import annotations

import pytest

from matchref.config import AppConfig
from matchref.edit_apply import EditTransformApplier
from matchref.models import SamplePoint, TransformSample
from matchref.timecode import TimecodeFormat, format_timecode
from matchref.timeline_context import TimelineContext

_FMT = TimecodeFormat(fps=24.0)


class _FakeItem:
    def __init__(self) -> None:
        self.props: dict[str, object] = {}

    def SetProperty(self, key: str, value: object) -> bool:
        self.props[key] = value
        return True

    def GetProperty(self, key: str) -> object:
        return self.props.get(key)


class _FakeTimeline:
    """Records seeks; GetCurrentTimecode confirms the move unless ``confirm`` off."""

    def __init__(self, confirm: bool = True) -> None:
        self.seeks: list[str] = []
        self._tc = "00:00:00:00"
        self._confirm = confirm

    def SetCurrentTimecode(self, tc: str) -> bool:
        self.seeks.append(tc)
        self._tc = tc if self._confirm else "00:00:00:00"
        return True

    def GetCurrentTimecode(self) -> str:
        return self._tc


class _TimelineNoSetter:
    pass


def _applier(timeline: object | None = None, resolve: object | None = None) -> EditTransformApplier:
    ctx = TimelineContext(fps=24.0, width=1920, height=1080)
    app = EditTransformApplier(resolve=resolve, config=AppConfig(), timeline_ctx=ctx)
    app._timeline = timeline  # bypass the live Resolve lookup
    return app


def _samples() -> list[TransformSample]:
    return [
        TransformSample(sample_point=SamplePoint.START, clip_local_frame=0, timeline_frame=0),
        TransformSample(sample_point=SamplePoint.END, clip_local_frame=24, timeline_frame=24),
    ]


# --- _enable_keyframe_mode --------------------------------------------------


def test_enable_keyframe_mode_true_when_supported() -> None:
    class _Res:
        def SetKeyframeMode(self, mode: int) -> bool:
            return True

    assert _applier(resolve=_Res())._enable_keyframe_mode() is True


def test_enable_keyframe_mode_false_when_absent() -> None:
    assert _applier(resolve=object())._enable_keyframe_mode() is False


# --- _seek confirmation -----------------------------------------------------


def test_seek_confirmed_when_playhead_reaches_target() -> None:
    tl = _FakeTimeline(confirm=True)
    assert _applier(timeline=tl)._seek(100) is True
    assert tl.seeks == [format_timecode(100, _FMT)]


def test_seek_fails_when_playhead_does_not_move() -> None:
    assert _applier(timeline=_FakeTimeline(confirm=False))._seek(100) is False


def test_seek_fails_without_setter() -> None:
    assert _applier(timeline=_TimelineNoSetter())._seek(100) is False


# --- _apply_keyframes refuses to write a broken ramp ------------------------


def test_keyframes_require_keyframe_mode() -> None:
    app = _applier(timeline=_FakeTimeline())
    with pytest.raises(RuntimeError, match="keyframe mode"):
        app._apply_keyframes(_FakeItem(), _samples(), 0, None, "clip", kf_enabled=False)


def test_keyframes_abort_on_failed_seek() -> None:
    app = _applier(timeline=_FakeTimeline(confirm=False))
    with pytest.raises(RuntimeError, match="seek"):
        app._apply_keyframes(_FakeItem(), _samples(), 0, None, "clip", kf_enabled=True)


def test_keyframes_written_at_each_frame_on_happy_path() -> None:
    tl = _FakeTimeline(confirm=True)
    app = _applier(timeline=tl)
    item = _FakeItem()
    app._apply_keyframes(item, _samples(), 100, None, "clip", kf_enabled=True)
    assert tl.seeks == [format_timecode(100, _FMT), format_timecode(124, _FMT)]
    assert "ZoomX" in item.props
