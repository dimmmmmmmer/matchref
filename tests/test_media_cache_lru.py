"""The source-decoder cache must stay bounded (memory-leak regression)."""

import matchref.frame_provider as fp
from matchref.config import AppConfig
from matchref.frame_provider import FrameProvider


class _FakeCap:
    def __init__(self, path):
        self.path = path
        self.released = False

    def isOpened(self):  # noqa: N802
        return True

    def release(self):
        self.released = True


def test_media_cache_bounded_and_releases(monkeypatch):
    opened = []

    def fake_capture(path):
        cap = _FakeCap(path)
        opened.append(cap)
        return cap

    monkeypatch.setattr(fp.cv2, "VideoCapture", fake_capture)
    monkeypatch.setattr(fp.Path, "is_file", lambda self: True)
    monkeypatch.setattr(fp, "probe_video", lambda path: type("P", (), {"fps": 24.0})())

    cfg = AppConfig()
    cfg.set("media_cache_size", 2)
    prov = FrameProvider(cfg)

    for i in range(5):
        prov._open_media(f"/clip_{i}.mov")

    # Never holds more than the cap; the 3 oldest were evicted and released.
    assert len(prov._media_cache) == 2
    assert sum(1 for c in opened if c.released) == 3
    assert sum(1 for c in opened if not c.released) == 2


def test_media_cache_reuses_open_decoder(monkeypatch):
    monkeypatch.setattr(fp.cv2, "VideoCapture", lambda path: _FakeCap(path))
    monkeypatch.setattr(fp.Path, "is_file", lambda self: True)
    monkeypatch.setattr(fp, "probe_video", lambda path: type("P", (), {"fps": 24.0})())
    prov = FrameProvider(AppConfig())
    a = prov._open_media("/same.mov")
    b = prov._open_media("/same.mov")
    assert a is b  # cached, not re-opened
