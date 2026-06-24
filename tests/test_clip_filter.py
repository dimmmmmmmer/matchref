"""Reference-clip detection and skip rules."""

from __future__ import annotations

from typing import Any

from matchref.clip_filter import is_reference_media, should_skip_clip
from matchref.config import AppConfig


def test_is_reference_media_same_path() -> None:
    assert is_reference_media("/a/b/ref.mov", "/a/b/ref.mov")


def test_is_reference_media_same_basename() -> None:
    assert is_reference_media("/proj/online/ref.mov", "/elsewhere/ref.mov")


def test_is_reference_media_different() -> None:
    assert not is_reference_media("/a/shot01.mov", "/a/ref.mov")
    assert not is_reference_media("", "/a/ref.mov")


class _FakeMediaItem:
    def __init__(self, path: str) -> None:
        self._path = path

    def GetClipProperty(self, prop: str | None = None) -> Any:
        if prop in ("File Path", "FilePath", "filepath"):
            return self._path
        return ""


class _FakeClip:
    def __init__(
        self, path: str = "", track: int = 1, duration: int = 100, name: str = "shot"
    ) -> None:
        self._item = _FakeMediaItem(path)
        self._track = track
        self._duration = duration
        self._name = name

    def GetMediaPoolItem(self) -> _FakeMediaItem:
        return self._item

    def GetTrackTypeAndIndex(self) -> tuple[str, int]:
        return "video", self._track

    def GetDuration(self) -> int:
        return self._duration

    def GetName(self) -> str:
        return self._name


def test_skip_disabled_returns_false() -> None:
    cfg = AppConfig()
    cfg.set("skip_reference_on_timeline", False)
    cfg.set("skip_offline_media", False)  # isolate the reference-skip flag
    skip, _ = should_skip_clip(_FakeClip(path="/a/ref.mov"), cfg, offline_reference="/a/ref.mov")
    assert skip is False


def test_skips_reference_file_on_timeline() -> None:
    cfg = AppConfig()
    skip, reason = should_skip_clip(
        _FakeClip(path="/online/ref.mov"), cfg, offline_reference="/offline/ref.mov"
    )
    assert skip is True
    assert "reference" in reason


def test_skips_excluded_track() -> None:
    cfg = AppConfig()
    cfg.set("exclude_track_indices", [9])
    skip, reason = should_skip_clip(
        _FakeClip(path="/a/shot.mov", track=9), cfg, offline_reference="/a/ref.mov"
    )
    assert skip is True
    assert "excluded" in reason


def test_skips_full_length_named_reference() -> None:
    cfg = AppConfig()
    # duration >= 90% of the offline length and the name matches a skip pattern
    clip = _FakeClip(path="/a/lockcut.mov", duration=1000, name="LOCK CUT v3")
    skip, reason = should_skip_clip(
        clip, cfg, offline_reference="/b/other.mov", offline_frame_count=1000
    )
    assert skip is True
    assert "lock cut" in reason.lower()


def test_keeps_normal_shot(tmp_path) -> None:
    cfg = AppConfig()
    media = tmp_path / "shot01.mov"
    media.write_bytes(b"x")  # a real (online) file so it isn't skipped as offline
    clip = _FakeClip(path=str(media), track=1, duration=80, name="shot01")
    skip, _ = should_skip_clip(
        clip, cfg, offline_reference="/a/ref.mov", offline_frame_count=1000
    )
    assert skip is False


def test_skips_offline_media_missing_file() -> None:
    cfg = AppConfig()
    clip = _FakeClip(path="/nope/missing.mov", track=1, duration=80, name="shot")
    skip, reason = should_skip_clip(clip, cfg, offline_reference="/a/ref.mov")
    assert skip is True
    assert "offline" in reason


def test_skips_offline_media_no_linked_file() -> None:
    cfg = AppConfig()
    clip = _FakeClip(path="", track=1, duration=80, name="shot")
    skip, reason = should_skip_clip(clip, cfg)
    assert skip is True
    assert "offline" in reason


def test_offline_skip_can_be_disabled(tmp_path) -> None:
    cfg = AppConfig()
    cfg.set("skip_offline_media", False)
    clip = _FakeClip(path="/nope/missing.mov", track=1, duration=80, name="shot")
    skip, _ = should_skip_clip(clip, cfg, offline_reference="/a/ref.mov")
    assert skip is False  # offline media no longer triggers a skip
